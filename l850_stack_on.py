#!/usr/bin/python3
"""Power on the XMM7360 cellular stack using runtime RPCs.

Sequence based on https://github.com/sbtasm-cmd/xmm7360-lte .
No firmware or NVM writes. ModemManager is always restarted.
"""

import json
import os
import select
import struct
import subprocess
import termios
import time

RPC_PORT = os.environ.get("L850_RPC_PORT", "/dev/wwan0xmmrpc0")
AT_PORT = os.environ.get("L850_AT_PORT", "/dev/wwan0at1")
MODEM = os.environ.get("L850_MODEM", "0")


def modem_state():
    result = subprocess.run(["mmcli", "-m", MODEM, "-J"], capture_output=True, text=True, timeout=15)
    if result.returncode:
        return None
    try:
        return json.loads(result.stdout)["modem"]["generic"]["state"]
    except (KeyError, ValueError):
        return None


def i32(value):
    return b"\x02\x04" + struct.pack(">I", value)


def rpc_send(fd, code, *args):
    body = b"".join(i32(x) for x in args)
    length = len(body) + 16
    frame = struct.pack("<I", length) + i32(length) + i32(code) + struct.pack(">I", 0x11000100) + body
    os.write(fd, frame)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if select.select([fd], [], [], 0.5)[0]:
            response = os.read(fd, 131072)
            if len(response) >= 20 and struct.unpack(">I", response[16:20])[0] == 0x11000100:
                return
    raise TimeoutError(f"RPC 0x{code:x} timed out")


def at_command(command, timeout=10):
    fd = os.open(AT_PORT, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        attrs = termios.tcgetattr(fd)
        attrs[0] = attrs[1] = attrs[3] = 0
        attrs[2] |= termios.CLOCAL | termios.CREAD
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        os.write(fd, (command + "\r").encode())
        answer = bytearray()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if select.select([fd], [], [], 0.5)[0]:
                answer.extend(os.read(fd, 4096))
                if b"\r\nOK\r\n" in answer:
                    return
                if b"\r\nERROR\r\n" in answer or b"+CME ERROR" in answer:
                    raise RuntimeError(f"{command} failed: {answer.decode(errors='replace').strip()}")
        raise TimeoutError(f"{command} timed out")
    finally:
        os.close(fd)


def main():
    deadline = time.monotonic() + 150
    last_enable_attempt = 0
    while time.monotonic() < deadline:
        state = modem_state()
        if state == "failed":
            raise RuntimeError("模块状态 failed：请用 mmcli 检查失败原因；若 sim-missing，请关机检查 SIM 后重启")
        print(f"等待模块启用：state={state}, 剩余 {max(0, int(deadline-time.monotonic()))} 秒", flush=True)
        if state in ("registered", "connected"):
            print(f"L850 already {state}; no action needed", flush=True)
            return
        if state in ("enabled", "searching"):
            break
        if state == "disabled" and time.monotonic() - last_enable_attempt > 12:
            last_enable_attempt = time.monotonic()
            result = subprocess.run(["mmcli", "-m", MODEM, "--enable"],
                                    capture_output=True, text=True, timeout=20)
            if result.returncode:
                print(f"L850 enable retry: {result.stderr.strip()}", flush=True)
        time.sleep(1)
    else:
        raise RuntimeError("ModemManager did not expose an enabled L850 within 150 seconds")

    subprocess.run(["systemctl", "stop", "ModemManager"], check=True)
    try:
        time.sleep(2)
        fd = os.open(RPC_PORT, os.O_RDWR | os.O_SYNC)
        try:
            for code in (0x30, 0x25, 0x53, 0x24, 0x3A, 0x26, 0x01):
                rpc_send(fd, code, 0)
            rpc_send(fd, 0x12F, 0, 15, 1)
            time.sleep(2)
            rpc_send(fd, 0x20, 1, 0)
            rpc_send(fd, 0x1F, 0x10, 0, 0, 1, 0)
            print("L850 protocol stack powered on", flush=True)
        finally:
            os.close(fd)
        time.sleep(2)
        at_command("AT+COPS=0", timeout=90)
        print("L850 automatic network selection enabled", flush=True)
    finally:
        subprocess.run(["systemctl", "start", "ModemManager"], check=True)
        subprocess.run(["udevadm", "trigger", "--action=add", "--subsystem-match=wwan"], check=True)

    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        print(f"等待重新探测及注册：剩余 {max(0, int(deadline-time.monotonic()))} 秒", flush=True)
        if modem_state() in ("registered", "connected"):
            print("L850 registered after ModemManager restart", flush=True)
            return
        time.sleep(2)
    raise RuntimeError("L850 did not register within 90 seconds after ModemManager restart")


if __name__ == "__main__":
    main()
