#!/usr/bin/env bash
set -euo pipefail
(( EUID == 0 )) || { echo '请使用 sudo' >&2; exit 1; }
[[ $(dpkg-query -W -f='${Version}' modemmanager) == 1.25.95-1ubuntu1 ]] || {
    echo '软件包版本已变更，不得跨版本恢复插件' >&2; exit 1;
}
PLUGIN=/usr/lib/x86_64-linux-gnu/ModemManager/libmm-plugin-intel.so
BACKUP=${PLUGIN}.before-ipv6-20261119
[[ -f "$BACKUP" ]] || { echo '未找到原插件备份' >&2; exit 1; }
install -m 0755 "$BACKUP" "$PLUGIN"
systemctl restart ModemManager
echo '已恢复原 Intel 插件；蜂窝连接需要重新建立。'
