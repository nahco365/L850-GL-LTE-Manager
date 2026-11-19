#!/usr/bin/env python3
"""Interactive L850-GL management; Python standard library only."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parent
PROFILE = "L850-Managed"
ENV = dict(os.environ, LC_ALL="C")
CONFIG = Path("/etc/l850-manager/apns.json")
DEFAULT_APNS = {"46000": "cmnet", "46002": "cmnet", "46004": "cmnet",
                "46007": "cmnet", "46008": "cmnet", "46001": "3gnet",
                "46006": "3gnet", "46009": "3gnet", "46003": "ctnet",
                "46005": "ctnet", "46011": "ctnet"}


def run(args, timeout=30, check=True, progress=False, env=None):
    if not progress:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env or ENV)
        if check and p.returncode:
            raise RuntimeError(p.stderr.strip() or p.stdout.strip())
        return p.stdout.strip()
    # A temporary file avoids pipe deadlocks with verbose child processes.
    import tempfile
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log:
        p = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, env=env or ENV)
        start = time.monotonic()
        try:
            while p.poll() is None:
                elapsed = int(time.monotonic() - start)
                print(f"处理中… 已等待 {elapsed} 秒 / 最长 {timeout} 秒", flush=True)
                if elapsed >= timeout:
                    raise TimeoutError("等待超时，请查看服务日志")
                time.sleep(2)
        finally:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
        log.seek(0)
        out = log.read()
        print(out, end="" if out.endswith("\n") else "\n")
        if check and p.returncode:
            raise RuntimeError(f"命令失败，退出码 {p.returncode}")
        return out.strip()


def detect():
    data = json.loads(run(["mmcli", "-L", "-J"]))
    matches = []
    for path in data.get("modem-list", []):
        m = json.loads(run(["mmcli", "-m", path, "-J"]))["modem"]
        g = m["generic"]
        if "7360" in str(g.get("device", "")) or "L850" in str(g.get("model", "")).upper() or "xmmrpc" in str(g.get("ports", "")):
            matches.append((path, m))
    if len(matches) != 1:
        raise RuntimeError(f"找到 {len(matches)} 个匹配模块；请检查 SIM、iosm 和 mmcli -L。插拔 SIM 后可能需要重启。")
    return matches[0]


def wait_modem():
    for remaining in range(90, 0, -3):
        try:
            return detect()
        except (RuntimeError, ValueError):
            print(f"等待 L850 加载… 剩余约 {remaining} 秒", flush=True)
            time.sleep(3)
    raise RuntimeError("90 秒内未找到模块；请检查日志，插拔 SIM 后可能需要重启。")


def fcc():
    src = Path("/usr/share/ModemManager/fcc-unlock.available.d/8086:7360")
    dst = Path("/etc/ModemManager/fcc-unlock.d/8086:7360")
    if not src.is_file():
        raise RuntimeError("发行版未提供 8086:7360 FCC 脚本，请先安装 modemmanager 和 xxd")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_symlink() and dst.resolve() == src:
        print("FCC 解锁链接已存在")
        return
    if dst.exists() or dst.is_symlink():
        raise RuntimeError(f"{dst} 已存在且不是预期链接，请先人工检查")
    dst.symlink_to(src)
    print("已启用发行版 FCC 脚本；下次启用模块时由 ModemManager 调用")


def restart():
    print("重启会中断该模块现有连接。")
    run(["udevadm", "control", "--reload-rules"])
    run(["udevadm", "trigger", "--action=add", "--subsystem-match=wwan"])
    run(["systemctl", "restart", "ModemManager"], progress=True, timeout=45)
    path, m = wait_modem()
    if m['generic'].get('state') == 'failed':
        raise RuntimeError('ModemManager 已重启，但模块仍失败：' + m['generic'].get('state-failed-reason', '--') + '；请运行 health 检查 SIM')
    print('ModemManager 已重启；模块状态：' + m['generic'].get('state', '--') + '（联网前仍需检查 SIM 和注册状态）')
    return path, m


def initialize():
    path, m = wait_modem()
    g = m["generic"]
    ports = g.get("ports", [])
    rpc = [str(p).split()[0] for p in ports if "xmmrpc" in str(p)]
    at = g.get("primary-port")
    if not rpc or not at or not re.fullmatch(r"wwan\d+at\d+", at):
        raise RuntimeError("未识别到 PCIe XMMRPC/AT 端口，停止初始化")
    env = dict(ENV, L850_MODEM=path, L850_RPC_PORT="/dev/" + rpc[0], L850_AT_PORT="/dev/" + at)
    run(["python3", str(ROOT / "l850_stack_on.py")], timeout=360, progress=True, env=env)


def exists():
    return bool(run(["nmcli", "-g", "connection.uuid", "connection", "show", PROFILE], check=False))


def set_apn(apn):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", apn):
        raise RuntimeError("APN 格式不正确")
    _, m = detect()
    at = m["generic"]["primary-port"]
    if not exists():
        run(["nmcli", "connection", "add", "type", "gsm", "ifname", at, "con-name", PROFILE,
             "gsm.apn", apn, "connection.autoconnect", "no", "connection.metered", "yes",
             "ipv4.route-metric", "1000", "ipv6.route-metric", "1000"])
    else:
        run(["nmcli", "connection", "modify", PROFILE, "gsm.apn", apn,
             "connection.interface-name", at, "connection.autoconnect", "no"])
    print(f"已保存 {PROFILE}: APN={apn}。当前会话不变，下次连接生效。")


def auto_apn():
    _, m = detect()
    sim = m["generic"].get("sim")
    code = None
    if sim and sim != "--":
        s = json.loads(run(["mmcli", "-i", sim, "-J"]))
        code = s.get("sim", {}).get("properties", {}).get("operator-identifier")
    code = code or m.get("3gpp", {}).get("operator-code")
    mapping = json.loads(CONFIG.read_text()) if CONFIG.exists() else DEFAULT_APNS
    if code not in mapping:
        raise RuntimeError(f"运营商代码 {code!r} 没有 APN 映射，请手动设置（虚拟运营商及专用卡需查运营商）")
    print(f"SIM/运营商代码 {code}，选择常用 APN {mapping[code]}，请核对套餐。")
    set_apn(mapping[code])


def service(install):
    dst = Path("/etc/systemd/system/l850-stack-on.service")
    if install:
        target = Path("/usr/local/lib/l850-manager")
        target.mkdir(parents=True, exist_ok=True)
        for name in ("lte-manager.py", "l850_stack_on.py"):
            if (ROOT / name).resolve() != (target / name).resolve():
                shutil.copy2(ROOT / name, target / name)
        dst.write_text("[Unit]\nDescription=L850 LTE initialization\nAfter=ModemManager.service NetworkManager.service\nWants=ModemManager.service\n\n[Service]\nType=oneshot\nExecStart=/usr/bin/python3 /usr/local/lib/l850-manager/lte-manager.py boot\nTimeoutStartSec=480\n\n[Install]\nWantedBy=multi-user.target\n")
        run(["systemctl", "daemon-reload"])
        run(["systemctl", "enable", "l850-stack-on.service"])
        print("已安装开机初始化服务；仅注册网络，不自动开启流量。本次可选择“初始化/等待注册”。")
    else:
        run(["systemctl", "disable", "--now", "l850-stack-on.service"], check=False)
        dst.unlink(missing_ok=True)
        run(["systemctl", "daemon-reload"])
        print("已卸载开机服务，保留 APN 和工具文件。")


def status():
    path, _ = detect()
    print(run(["mmcli", "-m", path]))
    print(run(["nmcli", "device", "status"]))
    print(run(["ip", "route", "show", "default"]))
    if exists():
        print(run(["nmcli", "-f", "connection.id,gsm.apn,connection.autoconnect,GENERAL.STATE", "connection", "show", PROFILE]))


def health():
    print('=== 网卡与 SIM 状态检查（只读，不发起数据连接） ===')
    found = False
    for dev in Path('/sys/bus/pci/devices').glob('*'):
        if not (dev / 'vendor').exists() or not (dev / 'device').exists():
            continue
        if (dev / 'vendor').read_text().strip() != '0x8086' or (dev / 'device').read_text().strip() != '0x7360':
            continue
        found = True
        driver = (dev / 'driver').resolve().name if (dev / 'driver').exists() else '未绑定'
        print(f'PCI 设备：{dev.name}；驱动：{driver}')
        nets = [n for n in Path('/sys/class/net').glob('*')
                if n.resolve().is_relative_to(dev.resolve())]
        if not nets:
            print('数据网卡：尚未创建，可能需要先初始化模块。')
        for net in nets:
            print(f'接口 {net.name}：')
            print(run(['ip', '-brief', 'address', 'show', 'dev', net.name], check=False))
            for key in ('operstate', 'carrier', 'statistics/rx_bytes', 'statistics/tx_bytes'):
                try:
                    print(f'  {key}: {(net / key).read_text().strip()}')
                except OSError:
                    pass
    if not found:
        print('未发现 PCI 8086:7360，请检查 BIOS、模块安装及硬件供电。')
    print('ModemManager 服务：', run(['systemctl', 'is-active', 'ModemManager'], check=False))
    print('NetworkManager 设备：\n' + run(['nmcli', 'device', 'status'], check=False))
    try:
        path, m = detect()
    except (RuntimeError, ValueError) as exc:
        print(f'模块检测：{exc}')
        print('SIM 状态：无法查询，不能仅据此判定 SIM 缺失。可先重启 ModemManager 并等待加载。')
        return
    g = m['generic']
    print(f"模块：{path}；状态：{g.get('state', '--')}；失败原因：{g.get('state-failed-reason', '--')}")
    print(f"电源：{g.get('power-state', '--')}；待解锁类型：{g.get('unlock-required', '--')}")
    print('信号质量：', g.get('signal-quality', {}).get('value', '--'), '%')
    print('注册状态：', m.get('3gpp', {}).get('registration-state', '--'))
    sim = g.get('sim')
    if not sim or sim == '--':
        print('SIM：ModemManager 尚未提供 SIM 对象。')
        if g.get('state-failed-reason') == 'sim-missing':
            print('模块明确报告 SIM 缺失；推荐关机重新插牢 SIM，再开机检查。')
        else:
            print('请等待初始化，检查模块失败原因及日志。')
    else:
        try:
            s = json.loads(run(['mmcli', '-i', sim, '-J']))['sim']['properties']
            print(f"SIM：已识别；active={s.get('active', '--')}；运营商={s.get('operator-name', '--')}；运营商代码={s.get('operator-identifier', '--')}")
            print('已识别 SIM 不等于已注册网络；请结合待解锁类型和注册状态判断。')
        except (RuntimeError, ValueError, KeyError) as exc:
            print(f'SIM 对象存在但读取失败：{exc}')
    print('默认路由：\n' + run(['ip', 'route', 'show', 'default'], check=False))
    print('提示：WWAN 接口显示 UNKNOWN 不一定是故障。插拔 SIM 后可能需要重启，推荐关机换卡。')


def connect():
    if not exists():
        auto_apn()
    print("将开启计费数据连接；默认路由 metric 1000，有线网络优先。")
    run(["nmcli", "--wait", "90", "connection", "up", PROFILE], timeout=100, progress=True)
    print("连接激活；此状态不保证互联网可达，请检查实际路由。")


def disconnect():
    if exists():
        run(["nmcli", "connection", "modify", PROFILE, "connection.autoconnect", "no"])
        run(["nmcli", "connection", "down", PROFILE], check=False)
    print("已关闭本工具配置的流量连接。其他蜂窝配置可在 nmcli connection show --active 中检查。")


def quickstart():
    print("一键启动将部署初始化服务并开启计费数据，使用一个极小 HTTP 请求验证蜂窝联网。", flush=True)
    print("[1/7] 检查硬件及 FCC 解锁脚本", flush=True)
    devices = []
    for p in Path('/sys/bus/pci/devices').glob('*'):
        if (p / 'vendor').exists() and (p / 'device').exists():
            if (p / 'vendor').read_text().strip() == '0x8086' and (p / 'device').read_text().strip() == '0x7360':
                devices.append(p)
    if len(devices) != 1:
        raise RuntimeError(f"需要恰好一个 PCI 8086:7360，实际 {len(devices)} 个")
    fcc()
    print("[2/7] 安装电源管理规则、启动管理服务", flush=True)
    rule = Path('/etc/udev/rules.d/99-l850-no-runtime-pm.rules')
    rule.write_text('ACTION=="add", SUBSYSTEM=="pci", ATTR{vendor}=="0x8086", ATTR{device}=="0x7360", ATTR{power/control}="on"\n')
    (devices[0] / 'power/control').write_text('on\n')
    run(['systemctl', 'start', 'NetworkManager'], progress=True, timeout=45)
    run(['nmcli', 'radio', 'wwan', 'on'])
    restart()
    print("[3/7] 初始化协议栈并等待网络注册", flush=True)
    initialize()
    print("[4/7] 配置 APN", flush=True)
    if exists():
        print('保留已有 L850-Managed 配置（包括手动 APN）；可用自动 APN 菜单重新选择。')
        print(run(['nmcli', '-g', 'gsm.apn', 'connection', 'show', PROFILE]))
        _, m = detect()
        run(['nmcli', 'connection', 'modify', PROFILE, 'connection.interface-name',
             m['generic']['primary-port'], 'connection.autoconnect', 'no'])
    else:
        auto_apn()
    print("[5/7] 安装开机初始化服务（以后开机只注册，不自动开启数据）", flush=True)
    service(True)
    print("[6/7] 启动蜂窝数据连接", flush=True)
    try:
        connect()
        print("[7/7] 通过蜂窝数据网卡验证互联网", flush=True)
        _, m = detect()
        ports = m['generic'].get('ports', [])
        nets = [p.split()[0] for p in ports if '(net)' in p]
        if not nets:
            nets = [p.name for p in (devices[0] / 'net').glob('*')]
        if len(nets) != 1:
            raise RuntimeError(f'无法唯一确定蜂窝数据网卡：{nets}')
        interface = nets[0]
        print(f'测试网卡：{interface}；HTTP 204 测试最多等待 12 秒，不走系统代理。')
        family = 'IPv4'
        try:
            code = run(['curl', '-4', '--interface', interface, '--noproxy', '*',
                        '--connect-timeout', '5', '--max-time', '12', '--silent', '--show-error',
                        '--output', '/dev/null', '--write-out', '%{http_code}',
                        'http://connectivitycheck.gstatic.com/generate_204'], timeout=15)
            if code != '204':
                raise RuntimeError(f'IPv4 HTTP {code}')
        except (RuntimeError, subprocess.SubprocessError) as exc:
            print(f'IPv4 测试未通过：{exc}；尝试 IPv6 HTTPS HEAD（最多 12 秒）')
            family = 'IPv6'
            code = run(['curl', '-6', '--interface', interface, '--noproxy', '*',
                        '--head', '--connect-timeout', '5', '--max-time', '12', '--silent', '--show-error',
                        '--output', '/dev/null', '--write-out', '%{http_code}',
                        'https://www.baidu.com'], timeout=15)
            if code not in ('200', '204', '301', '302'):
                raise RuntimeError(f'IPv6 HTTPS HEAD 返回 {code}，未通过检查')
        print(f'一键联网完成：{interface} 的 {family} 测试通过（HTTP {code}），数据连接保持开启。')
        print('默认有线优先，普通应用可能继续走有线；关闭数据请选择“关闭流量连接”。')
    except (RuntimeError, OSError, subprocess.SubprocessError):
        print('连接或联网测试失败，正在关闭本工具的数据连接。', flush=True)
        disconnect()
        raise


def main():
    parser = argparse.ArgumentParser(description="L850 LTE 管理工具：插拔 SIM 后可能需要重启，推荐关机换卡。")
    parser.add_argument("action", nargs="?", default="menu", choices=["menu", "status", "fcc", "restart", "init", "auto-apn", "set-apn", "connect", "disconnect", "install-service", "uninstall-service", "boot", "quickstart", "health"])
    parser.add_argument("apn", nargs="?")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("请使用 sudo python3 lte-manager.py")
    actions = {"health": health, "status": status, "fcc": fcc, "restart": restart, "init": initialize,
               "auto-apn": auto_apn, "set-apn": lambda: set_apn(args.apn or input("输入 APN: ").strip()),
               "connect": connect, "disconnect": disconnect, "install-service": lambda: service(True),
               "uninstall-service": lambda: service(False), "boot": initialize, "quickstart": quickstart}
    if args.action != "menu":
        actions[args.action]()
        return
    print("插拔 SIM 可能需要重启；推荐关机换卡。数据连接按套餐计费。")
    menu = [("状态/路由", status), ("链接 FCC 解锁脚本", fcc), ("初始化/等待注册", initialize),
            ("重启 ModemManager", restart), ("识别运营商并加载常用 APN", auto_apn),
            ("手动变更 APN", lambda: set_apn(input("APN: ").strip())),
            ("高级编辑 APN 配置（nmcli 交互编辑器）", lambda: subprocess.run(["nmcli", "connection", "edit", PROFILE], check=True)),
            ("安装开机初始化服务", lambda: service(True)), ("卸载开机服务", lambda: service(False)),
            ("开启流量上网", connect), ("关闭流量上网", disconnect),
            ("查看服务日志", lambda: print(run(["journalctl", "-u", "l850-stack-on.service", "-b", "-n", "60", "--no-pager"]))),
            ("检查网卡与 SIM 状态", health)]
    while True:
        print("\n" + "\n".join(f"{i}. {label}" for i, (label, _) in enumerate(menu, 1)) + "\n0. 退出")
        choice = input("选择: ").strip()
        if choice == "0":
            return
        try:
            if not choice.isdigit() or not 1 <= int(choice) <= len(menu):
                raise RuntimeError("无效选项")
            menu[int(choice) - 1][1]()
        except (RuntimeError, subprocess.SubprocessError, ValueError, OSError) as exc:
            print(f"操作失败：{exc}")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.SubprocessError, ValueError, OSError) as exc:
        print(f"失败：{exc}")
        raise SystemExit(1)
