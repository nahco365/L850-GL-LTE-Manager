#!/usr/bin/env bash
# Optional, experimental plugin replacement. Run over Ethernet.
set -euo pipefail
TASK_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
(( EUID == 0 )) || { echo '请使用 sudo' >&2; exit 1; }
[[ $# == 1 ]] || { echo '用法：sudo bash scripts/install-ipv6-plugin.sh 未修改的modemmanager源码目录' >&2; exit 1; }
[[ $(dpkg-query -W -f='${Version}' modemmanager) == 1.25.95-1ubuntu1 ]] || {
    echo '仅适用于已测试的 Ubuntu 1.25.95-1ubuntu1' >&2; exit 1;
}
SOURCE=$(realpath -- "$1")
[[ -f "$SOURCE/src/plugins/intel/mm-bearer-xmm7360.c" ]] || { echo '无效源码目录' >&2; exit 1; }
PLUGIN=/usr/lib/x86_64-linux-gnu/ModemManager/libmm-plugin-intel.so
BACKUP=${PLUGIN}.before-ipv6-20261119
[[ ! -e "$BACKUP" ]] || { echo "备份已存在，请先确认当前插件及备份：$BACKUP" >&2; exit 1; }
command -v meson >/dev/null
command -v ninja >/dev/null
WORK=$(mktemp -d /tmp/l850-ipv6.XXXXXXXX)
trap 'rm -rf -- "$WORK"' EXIT
echo '复制源码并应用 IPv6 主机 SLAAC 补丁…'
cp -a "$SOURCE" "$WORK/source"
python3 "$TASK_DIR/patch_ipv6.py" "$WORK/source/src/plugins/intel/mm-bearer-xmm7360.c"
meson setup "$WORK/build" "$WORK/source"
ninja -C "$WORK/build" src/plugins/libmm-plugin-intel.so
echo '备份并替换 Intel 插件；将中断蜂窝连接…'
cp -a "$PLUGIN" "$BACKUP"
install -m 0755 "$WORK/build/src/plugins/libmm-plugin-intel.so" "$PLUGIN"
if ! systemctl restart ModemManager; then
    install -m 0755 "$BACKUP" "$PLUGIN"
    systemctl restart ModemManager
    echo '服务重启失败，已恢复原插件' >&2
    exit 1
fi
echo '插件已安装，服务启动不等于联网验证成功。请等待约 30 秒，重连后检查 IPv6。'
echo "原插件：$BACKUP"
