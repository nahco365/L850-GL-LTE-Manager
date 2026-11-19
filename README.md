# L850-GL LTE 管理工具（实验版）

为 Ubuntu Server 上的 Fibocom L850-GL / Intel XMM7360 提供 Bash 中文菜单，自动检测模块和端口，管理 APN、LTE 数据连接及开机初始化，并提供可选 IPv6 补丁工具。

目前仅在一台 ThinkPad L490 上实测。基本功能面向 PCIe 模式、Linux `iosm` 驱动；USB/MBIM 模式、其他芯片和多模块环境不在已验证范围。自动 IPv6 补丁限定 Ubuntu resolute amd64、ModemManager `1.25.95-1ubuntu1`。

## 使用

将**整个仓库目录**复制到 Ubuntu，进入目录运行：

```bash
sudo bash lte-manager.sh
```

Bash 入口依赖配套 Python 文件；不要只复制 `.sh`。菜单启动会检查依赖，缺少时通过 apt 安装 Python 3、ModemManager、NetworkManager、xxd 和 curl 等软件，需要已有可用网络。

一键部署并联网：

```bash
sudo bash lte-manager.sh quickstart
```

会启动计费数据连接，请先确认套餐。等待期间显示阶段和用时；插拔 SIM 后可能需要重启，推荐关机换卡。

## 菜单功能

| 编号 | 功能 |
| --- | --- |
| 0 | 退出 |
| 1 | 自动检测模块编号、PCI 地址、驱动、端口、网卡及 IP |
| 2 | 链接发行版自带的 FCC 解锁脚本 |
| 3 | 初始化协议栈并等待运营商注册 |
| 4 | 根据 SIM/运营商代码选择常用 APN |
| 5 | 手动修改 APN 名称 |
| 6 | 进入 nmcli 高级编辑器，编辑 APN、认证及 IP 配置 |
| 7 | 安装开机初始化服务 |
| 8 | 卸载开机初始化服务 |
| 9 | 开启 LTE 数据连接 |
| 10 | 关闭本工具的数据连接 |
| 11 | 自动配置 PCI 运行时电源管理规则 |
| 12 | 查看本次开机的初始化服务日志 |
| 13 | 一键检查、部署、初始化、配置 APN、连接并测试联网 |
| 14 | 检查网卡及 SIM、PIN 要求、信号、注册状态、路由 |
| 15 | 自动下载、构建、备份并安装可选 IPv6 插件补丁 |
| 16 | 恢复 IPv6 补丁安装前的插件 |

一键联网会检查 FCC 链接、电源管理，启动管理服务并等待模块注册。首次创建连接时自动选择 APN；已有 `L850-Managed` 配置时保留手动设置并更新端口。随后安装开机初始化服务、激活数据连接并验证互联网。IPv4 HTTP 测试失败时会尝试 IPv6 HTTPS HEAD，任一成功即保留连接；均失败则关闭本工具的数据连接。公网测试地址被阻断也可能导致测试失败。

## APN 与数据连接

内置中国移动 `cmnet`、中国联通 `3gnet`、中国电信 `ctnet` 的常见运营商代码。专网、虚拟运营商、海外卡及特殊套餐需要向运营商确认 APN。自动识别优先使用 SIM 归属运营商代码，无法读取时才使用注册网络代码；未知代码会报错。

工具管理名为 **`L850-Managed`** 的 NetworkManager 配置，默认 `connection.autoconnect=no`、计量连接，路由 metric 为 1000。以太网通常优先，因此蜂窝激活后普通应用不一定走 LTE。该设置不能阻止所有后台程序使用数据；运营商仍可能计费。

可用 `/etc/l850-manager/apns.json` 替换内置映射，例如：

```json
{"46000": "cmnet", "46001": "3gnet", "46011": "ctnet"}
```

此文件完全替代内置映射，应包含要使用的所有代码。修改 APN 后，先关闭再开启数据连接即可应用，通常无需重启电脑。修改数据 APN不会自动修改初始 EPS APN。

高级编辑器中可以用 `help`、`print`、`set gsm.apn cmnet`、`save persistent`、`quit`。先通过菜单创建配置，再进入高级编辑器。其他已有蜂窝配置由用户自行管理，停止本工具连接不会关闭它们。

## 初始化、FCC 与开机服务

初始化用于解决模块已识别、SIM 可读但协议栈未启动而不搜网的情况。它通过运行时 RPC 启动协议栈并自动选网；已注册时跳过。不刷写固件，不能修复 SIM 接触故障。

FCC helper 来自 Ubuntu 的 `modemmanager` 软件包，本仓库仅链接 `/usr/share/ModemManager/fcc-unlock.available.d/8086:7360` 至 `/etc/ModemManager/fcc-unlock.d/8086:7360`。可用 `dpkg -S` 核查归属。重启 ModemManager 不保证调用 FCC helper，解锁也不保证恢复 SIM 识别。

开机服务复制 `lte-manager.py` 和 `l850_stack_on.py` 至 `/usr/local/lib/l850-manager/`，安装 `/etc/systemd/system/l850-stack-on.service`。服务**只初始化和注册网络，不自动开启流量**。服务名与本项目旧部署一致，安装会替换同名服务；更新脚本后需重新安装服务以更新副本。

菜单 8 删除服务，保留 APN、工具副本和 FCC 链接。菜单 11 写入 `/etc/udev/rules.d/99-l850-no-runtime-pm.rules`，使匹配设备电源控制保持 `on`。硬件地址、AT/RPC 端口由菜单工具自动发现，无需填写个人设备标识。

## 可选 IPv6 补丁

```bash
sudo bash lte-manager.sh patch-ipv6
# 回退：
sudo bash lte-manager.sh restore-ipv6
```

自动安装检查系统、架构和软件包版本，并要求非 LTE 的 IPv4 下载出口。随后临时启用 Ubuntu 源码仓库，安装构建依赖，下载固定版本源码并核对 SHA-256，应用补丁、编译 Intel 插件、备份和替换，然后重启 ModemManager。每几秒显示等待进度，退出时清理临时源码仓库和构建目录。安装中会中断蜂窝连接，不主动重新开启流量。

已存在原备份且检测到本补丁时，重复执行直接跳过。备份存在但当前插件不含补丁时停止，要求检查，防止覆盖原备份。

补丁读取 CID 0 的 `AT+CGCONTRDP` IPv6 参数，复用 ModemManager 解析器，向 NetworkManager 提交协商的链路本地地址和 DNS，由主机完成 SLAAC。它解决的是该版本 XMM7360 原生 RPC 实现只交付 IPv4 配置的问题。未读取到有效 IPv6 参数时保留 IPv4；补丁不会为不提供 IPv6 的套餐创造 IPv6。

只实测 IPv4v6 双栈请求，尚未验证 IPv6-only、其他固件和其他运营商。补丁是本地实验实现，尚未合入上游。软件包升级可能覆盖插件，不得跨版本恢复旧 `.so`。原插件备份路径：

```text
/usr/lib/x86_64-linux-gnu/ModemManager/libmm-plugin-intel.so.before-ipv6-20261119
```

## 文件说明

| 文件 | 用途 |
| --- | --- |
| `lte-manager.sh` | Bash 菜单与命令行入口 |
| `lte-manager.py` | 动态探测、连接管理、状态检查、服务管理 |
| `l850_stack_on.py` | 二进制 RPC 协议栈初始化与注册等待 |
| `99-l850-no-runtime-pm.rules` | 电源管理规则 |
| `scripts/auto-ipv6-plugin.sh` | 自动准备源码、依赖并调用安装器 |
| `scripts/install-ipv6-plugin.sh` | 从指定源码目录构建、备份并安装补丁 |
| `scripts/restore-ipv6-plugin.sh` | 回退原插件 |
| `scripts/patch_ipv6.py` | 对匹配版本源码进行严格替换 |
| `scripts/xmm7360-ipv6-host-slaac.patch` | 对应源码差异，供审阅 |

## 验证范围与限制

已在一台 L490 上验证硬件/SIM 状态检查、APN 创建和修改、关闭连接，以及 IPv6 补丁后的公网 IPv6、DNS、ping、HTTPS 和重连。重复安装补丁不会覆盖备份或重启服务。自动补丁依赖安装全流程尚未在另一台干净设备重跑，长期稳定性和整机重启恢复尚未完整验证。

热插拔后曾出现 `sim-missing`，重启 ModemManager 和补执行 FCC 解锁未恢复；模块复位还曾导致 AT 端口失联，不建议依赖软复位恢复。推荐关机换卡。`wwan` 的 `UNKNOWN` 状态本身不代表故障。

测试中曾有 IPv4 无回包现象，原插件对照结果也相同；不承诺所有 APN 和网络均可用。本工具不提供短信、VoLTE 或免流量功能。

## 参考与来源

- [ModemManager FCC 解锁说明](https://modemmanager.org/docs/modemmanager/fcc-unlock/)
- [ModemManager LTE IP 配置及主机 SLAAC](https://modemmanager.org/docs/modemmanager/ip-connectivity-setup-in-lte-modems/)
- [ModemManager XMM7360 上游源码](https://github.com/linux-mobile-broadband/ModemManager/blob/main/src/plugins/intel/mm-bearer-xmm7360.c)
- [XMM7360 协议栈上电研究](https://github.com/sbtasm-cmd/xmm7360-lte)

IPv6 差异文件修改的是上游 ModemManager 的 GPL-2.0-or-later 源文件；上游许可证及作者声明仍适用。FCC helper 使用发行版提供的文件，本仓库不附带其副本。
