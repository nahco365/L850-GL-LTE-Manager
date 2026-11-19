#!/usr/bin/env bash
# Download, build and install the tested optional XMM7360 IPv6 patch.
set -euo pipefail
TASK_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
(( EUID == 0 )) || { echo '请使用 sudo' >&2; exit 1; }
VERSION=$(dpkg-query -W -f='${Version}' modemmanager 2>/dev/null || true)
[[ $VERSION == 1.25.95-1ubuntu1 ]] || {
    echo "不支持当前 ModemManager 版本：$VERSION；仅支持 1.25.95-1ubuntu1。" >&2; exit 1;
}
PLUGIN=/usr/lib/x86_64-linux-gnu/ModemManager/libmm-plugin-intel.so
BACKUP=${PLUGIN}.before-ipv6-20261119
if [[ -e "$BACKUP" ]]; then
    echo "检测到原插件备份：$BACKUP"
    if [[ -f "$PLUGIN" ]] && grep -aq 'Negotiated IPv6 IID handed to host SLAAC' "$PLUGIN"; then
        echo '当前插件已包含本工具的 IPv6 补丁，无需重复安装；本次未重启服务。'
        exit 0
    fi
    echo '当前插件状态不确定，或已回退。保留备份并停止；请先检查插件与备份。' >&2
    exit 1
fi
[[ -f "$PLUGIN" ]] || { echo '没有找到 Intel 插件' >&2; exit 1; }
[[ -f "$TASK_DIR/patch_ipv6.py" && -f "$TASK_DIR/install-ipv6-plugin.sh" ]] || {
    echo '缺少配套补丁脚本，请复制完整工具目录。' >&2; exit 1;
}
. /etc/os-release
[[ ${ID:-} == ubuntu && ${VERSION_CODENAME:-} == resolute && $(dpkg --print-architecture) == amd64 ]] || {
    echo '自动部署仅支持已测试的 Ubuntu resolute amd64；其他系统请先核对构建方案。' >&2; exit 1;
}
echo '[1/5] 检查下载出口（避免使用计费 LTE 下载构建依赖）…'
command -v python3 >/dev/null
DOWNLOAD_IF=$(ip -j -4 route get 1.1.1.1 | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["dev"])')
[[ -n "$DOWNLOAD_IF" && $DOWNLOAD_IF != wwan* ]] || {
    echo '当前 IPv4 默认出口为蜂窝网卡，请先连接以太网或 Wi-Fi。' >&2; exit 1;
}
if [[ -e /sys/class/net/$DOWNLOAD_IF/device/vendor && -e /sys/class/net/$DOWNLOAD_IF/device/device ]]; then
    if [[ $(cat "/sys/class/net/$DOWNLOAD_IF/device/vendor") == 0x8086 && $(cat "/sys/class/net/$DOWNLOAD_IF/device/device") == 0x7360 ]]; then
        echo '检测到下载出口是 XMM7360 数据接口，请先连接其他网络。' >&2; exit 1
    fi
fi
echo "下载使用 IPv4 出口：$DOWNLOAD_IF。安装会重启 ModemManager 并中断蜂窝连接。"
WORK=$(mktemp -d /tmp/l850-auto-ipv6.XXXXXXXX)
SOURCE_LIST="/etc/apt/sources.list.d/l850-ipv6-build-$$.sources"
cleanup() {
    rm -f -- "$SOURCE_LIST"
    rm -rf -- "$WORK"
}
trap cleanup EXIT
step() {
    local label=$1 elapsed=0 pid rc
    shift
    echo "$label"
    "$@" > "$WORK/step.log" 2>&1 &
    pid=$!
    while kill -0 "$pid" 2>/dev/null; do
        echo "  处理中… 已等待 ${elapsed} 秒（首次安装依赖可能需要数分钟）"
        sleep 3
        elapsed=$((elapsed + 3))
    done
    if wait "$pid"; then rc=0; else rc=$?; fi
    tail -n 12 "$WORK/step.log"
    if (( rc )); then
        cat "$WORK/step.log" >&2
        return "$rc"
    fi
}
echo '[2/5] 临时启用 Ubuntu 源码仓库，安装编译依赖…'
cat > "$SOURCE_LIST" <<'SOURCES'
Types: deb-src
URIs: http://mirrors.ustc.edu.cn/ubuntu/
Suites: resolute resolute-updates
Components: main universe
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
SOURCES
export DEBIAN_FRONTEND=noninteractive
step '更新 apt 索引' apt-get -o Acquire::ForceIPv4=true update
step '安装构建工具' apt-get -o Acquire::ForceIPv4=true install -y build-essential meson ninja-build python3 curl
step '安装 ModemManager 构建依赖' apt-get -o Acquire::ForceIPv4=true build-dep -y "modemmanager=$VERSION"
echo '[3/5] 下载固定版本源码并校验 SHA-256…'
step '下载 Ubuntu ModemManager 源码' curl -4 --interface "$DOWNLOAD_IF" --fail --location --connect-timeout 10 --max-time 180 --output "$WORK/source.tar.xz" \
    http://mirrors.ustc.edu.cn/ubuntu/pool/main/m/modemmanager/modemmanager_1.25.95.orig.tar.xz
echo "4d93ffd677b4c9f7f773197a77bfe2cc40f3a6fea94409f91ffd51dd7c20c64d  $WORK/source.tar.xz" | sha256sum -c -
tar -xf "$WORK/source.tar.xz" -C "$WORK"
echo '[4/5] 应用补丁、编译、备份并安装（持续显示进度）…'
step '构建安装 IPv6 插件' bash "$TASK_DIR/install-ipv6-plugin.sh" "$WORK/modemmanager-1.25.95"
echo '[5/5] 完成。等待约 30 秒后，用菜单 9 重连、菜单 14 检查 IPv6。'
echo '本操作不主动开启流量。需要联网时可再选菜单 13；需要回退可选菜单 16。'
