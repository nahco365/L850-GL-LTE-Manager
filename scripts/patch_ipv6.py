#!/usr/bin/env python3
"""Experimental host-SLAAC handoff for ModemManager 1.25.95 XMM7360.

Uses the existing CGCONTRDP parser, one line at a time: this modem may
return multiple IPv4/IPv6 records for CID 0. No guessed IPv6 IID is used.
"""
import argparse
from pathlib import Path

CALLBACK = r'''
/* L850 local patch: hand negotiated IPv6 settings to host SLAAC. */
static void
ipv6_context_ready (MMBaseModem *modem,
                    GAsyncResult *res,
                    GTask *task)
{
    ConnectContext *ctx;
    g_autoptr(GError) error = NULL;
    const gchar *response;
    g_auto(GStrv) lines = NULL;
    g_autoptr(MMBearerIpConfig) ipv6_config = NULL;
    guint i;

    ctx = g_task_get_task_data (task);
    response = mm_base_modem_at_command_finish (modem, res, &error);
    if (!response)
        mm_obj_warn (modem, "IPv6 CGCONTRDP query failed: %s", error->message);
    else {
        lines = g_strsplit (response, "\r\n", -1);
        for (i = 0; lines[i]; i++) {
            g_autofree gchar *address = NULL;
            g_autofree gchar *dns1 = NULL;
            g_autofree gchar *dns2 = NULL;
            g_autoptr(GInetAddress) ip = NULL;
            g_autoptr(GError) parse_error = NULL;
            const gchar *dns[3] = { NULL, NULL, NULL };
            guint cid = G_MAXUINT;
            guint n = 0;

            if (!g_str_has_prefix (g_strstrip (lines[i]), "+CGCONTRDP:"))
                continue;
            if (!mm_3gpp_parse_cgcontrdp_response (lines[i], &cid, NULL, NULL,
                                                 &address, NULL, NULL,
                                                 &dns1, &dns2, &parse_error)) {
                mm_obj_warn (modem, "IPv6 context parse failed: %s", parse_error->message);
                continue;
            }
            if (cid != 0 || !address)
                continue;
            ip = g_inet_address_new_from_string (address);
            if (!ip || g_inet_address_get_family (ip) != G_SOCKET_FAMILY_IPV6 ||
                g_inet_address_get_is_any (ip) || g_inet_address_get_is_multicast (ip) ||
                g_inet_address_get_is_loopback (ip))
                continue;
            /* Only a negotiated link-local IID is used for host SLAAC.
             * Do not guess a /64 for an unrecognized/global response. */
            if (!g_inet_address_get_is_link_local (ip)) {
                mm_obj_warn (modem, "IPv6 context is not link-local; unsupported by local SLAAC patch");
                continue;
            }
            ipv6_config = mm_bearer_ip_config_new ();
            mm_bearer_ip_config_set_method (ipv6_config, MM_BEARER_IP_METHOD_DHCP);
            mm_bearer_ip_config_set_address (ipv6_config, address);
            mm_bearer_ip_config_set_prefix (ipv6_config, 64);
            if (dns1) {
                g_autoptr(GInetAddress) d = g_inet_address_new_from_string (dns1);
                if (d && g_inet_address_get_family (d) == G_SOCKET_FAMILY_IPV6 &&
                    !g_inet_address_get_is_any (d))
                    dns[n++] = dns1;
            }
            if (dns2) {
                g_autoptr(GInetAddress) d = g_inet_address_new_from_string (dns2);
                if (d && g_inet_address_get_family (d) == G_SOCKET_FAMILY_IPV6 &&
                    !g_inet_address_get_is_any (d))
                    dns[n++] = dns2;
            }
            mm_bearer_ip_config_set_dns (ipv6_config, dns);
            mm_obj_info (modem, "Negotiated IPv6 IID handed to host SLAAC");
            break;
        }
    }
    if (!ipv6_config)
        mm_obj_warn (modem, "No usable negotiated IPv6 IID; preserving IPv4 connection");
    g_task_return_pointer (task,
                           mm_bearer_connect_result_new (ctx->data, ctx->ipv4_config, ipv6_config),
                           (GDestroyNotify)mm_bearer_connect_result_unref);
    g_object_unref (task);
}

'''


def patch(text):
    if 'L850 local patch:' in text:
        raise ValueError('already patched')
    replacements = [
        ('#include "mm-bind.h"', '#include "mm-bind.h"\n#include "mm-base-modem-at.h"\n#include "mm-modem-helpers.h"'),
        ('    GInetAddress *ip;','    GInetAddress *ip;\n    MMBearerIpConfig *ipv4_config;\n    MMBearerIpFamily requested_ip_type;'),
        ('    ctx->dns = g_ptr_array_new_with_free_func ((GDestroyNotify)g_object_unref);',
         '''    ctx->dns = g_ptr_array_new_with_free_func ((GDestroyNotify)g_object_unref);
    ctx->requested_ip_type = mm_bearer_properties_get_ip_type (mm_base_bearer_peek_config (
        MM_BASE_BEARER (g_task_get_source_object (G_TASK (user_data)))));'''),
        ('    g_clear_object (&ctx->ip);','    g_clear_object (&ctx->ip);\n    g_clear_object (&ctx->ipv4_config);'),
        ('static void\nps_connect_setup_ready', CALLBACK + 'static void\nps_connect_setup_ready'),
        ('''    g_task_return_pointer (task,
                           mm_bearer_connect_result_new (ctx->data, ip_config, NULL),
                           (GDestroyNotify)mm_bearer_connect_result_unref);
    g_object_unref (task);''', '''    if (ctx->requested_ip_type &
        (MM_BEARER_IP_FAMILY_IPV6 | MM_BEARER_IP_FAMILY_IPV4V6)) {
        ctx->ipv4_config = g_object_ref (ip_config);
        mm_base_modem_at_command (MM_BASE_MODEM (modem), "+CGCONTRDP=0", 10, FALSE,
                                 (GAsyncReadyCallback)ipv6_context_ready, task);
        return;
    }
    g_task_return_pointer (task,
                           mm_bearer_connect_result_new (ctx->data, ip_config, NULL),
                           (GDestroyNotify)mm_bearer_connect_result_unref);
    g_object_unref (task);''')]
    for old, new in replacements:
        if text.count(old) != 1:
            raise ValueError('source does not match tested version: ' + old[:65])
        text = text.replace(old, new)
    return text


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    args = parser.parse_args()
    args.source.write_text(patch(args.source.read_text()))
    print('Applied experimental dual-stack host-SLAAC patch')
