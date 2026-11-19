#!/usr/bin/env bash
# Bash entry point; mmcli JSON parsing and binary RPC use Python standard library.
set -Eeuo pipefail
TASK_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if (( EUID != 0 )); then
    echo "请使用 sudo bash $0" >&2
    exit 1
fi
ensure_dependencies() {
    local missing=0 required
    for required in python3 mmcli nmcli udevadm systemctl xxd curl; do
        command -v "$required" >/dev/null || missing=1
    done
    if (( missing )); then
        echo '检查到缺少依赖，使用 apt 安装（请确保有线网络可用）…'
        apt-get update
        DEBIAN_FRONTEND=noninteractive apt-get install -y python3 modemmanager network-manager xxd curl
    fi
}
if [[ ${1:-} == quickstart || $# == 0 ]]; then
    ensure_dependencies
fi
for required in python3 mmcli nmcli udevadm systemctl; do
    command -v "$required" >/dev/null || {
        echo "缺少 $required。请先安装：sudo apt-get install python3 modemmanager network-manager xxd" >&2
        exit 1
    }
done
[[ -f "$TASK_DIR/lte-manager.py" && -f "$TASK_DIR/l850_stack_on.py" ]] || {
    echo '请保留完整目录，缺少配套 Python 文件。' >&2; exit 1;
}

detect_hardware() {
    python3 - "$TASK_DIR" <<'PY'
import importlib.util
from pathlib import Path
import sys
spec = importlib.util.spec_from_file_location('lte_manager', Path(sys.argv[1]) / 'lte-manager.py')
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)
try:
    modem, data = manager.detect()
    generic = data['generic']
    device = Path(generic['device'])
    print('模块编号：', modem)
    print('PCI 地址：', device.name)
    print('驱动：', ', '.join(generic.get('drivers', [])))
    print('AT 端口：', generic.get('primary-port', '--'))
    print('模块端口：', ', '.join(generic.get('ports', [])))
    nets = [p for p in Path('/sys/class/net').glob('*') if p.resolve().is_relative_to(device.resolve())]
    if nets:
        for net in nets:
            print('数据网卡：', net.name)
            print(manager.run(['ip', '-brief', 'address', 'show', 'dev', net.name]))
    else:
        print('数据网卡：尚未创建；启用模块或连接后重新查看。')
    print('运行状态：', generic.get('state'), generic.get('state-failed-reason', ''))
except (RuntimeError, ValueError, OSError) as exc:
    print('检测失败：', exc, file=sys.stderr)
    sys.exit(1)
PY
}

apply_power_rule() {
    # Match by hardware ID rather than a machine-specific PCI bus address.
    local found=0 pci
    for pci in /sys/bus/pci/devices/*; do
        [[ -r "$pci/vendor" && -r "$pci/device" ]] || continue
        if [[ $(<"$pci/vendor") == 0x8086 && $(<"$pci/device") == 0x7360 ]]; then
            echo on > "$pci/power/control"
            echo "已配置电源管理：${pci##*/}"
            found=1
        fi
    done
    (( found )) || { echo '未检测到 PCI 8086:7360，未写入规则。' >&2; return 1; }
    install -m 0644 "$TASK_DIR/99-l850-no-runtime-pm.rules" /etc/udev/rules.d/99-l850-no-runtime-pm.rules
    udevadm control --reload-rules
}

invoke() { python3 "$TASK_DIR/lte-manager.py" "$@"; }

if (( $# )); then
    case "$1" in
        detect) detect_hardware ;;
        power-rule) apply_power_rule ;;
        patch-ipv6) bash "$TASK_DIR/scripts/auto-ipv6-plugin.sh" ;;
        restore-ipv6) bash "$TASK_DIR/scripts/restore-ipv6-plugin.sh" ;;
        *) invoke "$@" ;;
    esac
    exit
fi

echo 'L850 LTE 管理：自动检测设备地址及端口，无需填写 PCI 地址或模块编号。'
echo '插拔 SIM 可能需要重启，推荐关机换卡。开启数据连接可能计费。'
while true; do
    cat <<'MENU'

 0. 退出
 1. 自动检测硬件、网卡及 IP 地址
 2. 链接 FCC 解锁脚本
 3. 初始化模块并等待网络注册
 4. 自动识别运营商并保存 APN
 5. 手动变更 APN
 6. 高级编辑 APN（先创建配置；save persistent 保存）
 7. 安装开机初始化服务
 8. 卸载开机初始化服务
9. 开启流量连接
10. 关闭流量连接
11. 自动配置 PCI 电源管理规则
12. 查看开机服务日志
13. 一键检查、部署、初始化并联网（会产生数据费用）
14. 检查当前网卡与 SIM 状态（只读）
15. 一键下载、编译并安装 IPv6 补丁（会中断蜂窝连接）
16. 回退 IPv6 插件补丁（会中断蜂窝连接）
MENU
    read -r -p '请选择：' choice || exit 0
    case "$choice" in
        0) exit 0 ;;
        1) detect_hardware || echo '检测未完成，请检查设备和服务。' ;;
        2) invoke fcc || echo 'FCC 配置失败。' ;;
        3) invoke init || echo '初始化失败，请查看状态和日志。' ;;
        4) invoke auto-apn || echo '无法自动选择，请手动输入运营商提供的 APN。' ;;
        5) read -r -p '输入 APN：' apn; invoke set-apn "$apn" || echo '修改失败。' ;;
        6) nmcli connection edit L850-Managed || echo '编辑失败，请先创建 APN 配置。' ;;
        7) invoke install-service || echo '安装失败。' ;;
        8) invoke uninstall-service || echo '卸载失败。' ;;
        9) invoke connect || echo '连接失败，请检查 APN、SIM 及日志。' ;;
        10) invoke disconnect || echo '关闭失败，请检查活动连接。' ;;
        11) apply_power_rule || echo '电源管理配置失败。' ;;
        12) journalctl -u l850-stack-on.service -b -n 60 --no-pager || true ;;
        13) ensure_dependencies && invoke quickstart || echo '一键启动失败，请按上述阶段提示检查；插拔 SIM 后可能需要重启。' ;;
        14) invoke health || echo '状态检查失败，请检查服务和日志。' ;;
        15) bash "$TASK_DIR/scripts/auto-ipv6-plugin.sh" || echo 'IPv6 补丁未完成，请查看上方错误；原插件备份会保留。' ;;
        16) bash "$TASK_DIR/scripts/restore-ipv6-plugin.sh" || echo '回退失败，请检查备份和版本。' ;;
        *) echo '无效选项。' ;;
    esac
done
