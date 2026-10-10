#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
screen-cast.py  —  DLNA 屏幕投屏器（Windows）

原理
----
不依赖 Miracast。利用智能电视标准的 DLNA / UPnP 渲染能力：
  1. SSDP 发现局域网内的 MediaRenderer 设备
  2. ffmpeg 采集本机屏幕 -> H.264 编码 -> MPEG-TS 流
  3. 本程序内置一个 HTTP 服务器，把该流以 video/mp2t 输出
  4. 通过 UPnP AVTransport 的 SetAVTransportURI + Play，让电视主动拉流播放

电视端无需安装任何软件，只要支持 DLNA 即可（本机电视已实测支持
video/MP2T、video/m3u8、video/x-motion-jpeg）。

声音
----
默认经 WASAPI 环回捕获「电脑正在播放的声音」（含系统提示音、浏览器、
播放器），以 32-bit float PCM 通过管道与画面一起编码，随流送到电视。
不依赖声卡的「立体声混音」（本机该项被系统禁用），也不改动任何系统设置。
配置为 dshow 设备时可用 --audio dshow:<设备名>。

声音转移（默认开启）
-------------------
投屏开始时会**静音本机播放端点**，使声音只从电视出，避免电脑与电视同时响；
退出（含 Ctrl+C 与异常退出）时自动恢复原来的静音状态。
实现方式是 IAudioEndpointVolume::SetMute，作用在硬件输出层，位于 WASAPI
环回采样点之后，因此本机静音后环回仍能取到完全相同的音频数据（已实测比对）。
不切换默认设备、不改注册表，退出即还原。
如需保留电脑本地声音，加 --keep-local-audio。
若程序被 taskkill /F 或任务管理器强制结束（这是唯一无法自动恢复的退出方式），
运行 `screen-cast.py --unmute` 即可解除本机静音。

延迟
----
端到端延迟由三部分构成：
    1) 本机采集 + 编码     —— 可用硬件编码（h264_mf）压到 10 ms 量级
    2) 本机流缓冲积压      —— 由 --latency-ms 直接控制（默认 300 ms）
    3) 电视端播放器缓冲    —— ★ 本程序无法控制，通常在 1~3 秒

1、2 两项已经压到很低。第 3 项取决于电视自带的 DLNA 播放器，任何
"电脑推流 + 电视拉流"的方案都绕不过它。因此本方案的实际端到端延迟
通常落在 1~2.5 秒；若电视播放器缓冲激进，可能仍有 3 秒。

要最流畅，用一键预设：
    python screen-cast.py --low-latency
它等价于：x264 软编 + 250 ms 缓冲 + 400 ms 关键帧 + 30 fps。

帧率
----
默认 30 fps。实测本机 gdigrab 在 1080p 下能稳定跑满 30 fps，而 25 fps
白丢 17% 的帧；更要紧的是 25 与电视 60 Hz 刷新率不整除（60÷25 = 2.4），
电视适配时每帧占据的刷新周期数忽多忽少，滚动、字幕类画面会明显发涩 ——
即主观上的"迟缓"。而 30 与 60 是 2:1 整除，节奏完全均匀。

gdigrab 在 1080p 下的上限就是 30 fps（BitBlt 全屏耗时决定，请求 60 也
只出 30）。要突破需改用 GPU 采集：
    python screen-cast.py --capture ddagrab
它走 Desktop Duplication，CPU 占用更低、帧率上限更高，但依赖可用的
D3D11 桌面会话；不可用时自动退回 gdigrab，不会导致投屏失败。

画质
----
档位：540 / 720 / 1080 / 1080hq / 1440(2K) / 2160(4K)。
注意：目标分辨率是「上限」，程序会探测源屏幕尺寸，**默认不做上采样**——
1080p 屏幕选 4K 档不会变清晰，只白白增加码率与 CPU 占用，因此会自动
降级到源分辨率并在日志中说明。源本身是 2K/4K 时才会真正输出 2K/4K。
想要更高画质，正确做法是选 1080hq（提高码率）而非放大分辨率。
如需强制上采样（例如喂给 4K 电视让电视做锐化），加 --allow-upscale。

花屏
----
花屏（马赛克/绿块/画面撕裂后长时间不恢复）的唯一成因是：客户端收到的
第一个视频包不是 IDR 关键帧，导致后续 P 帧引用了不存在的参考帧。
本程序在放弃积压数据时，一律把丢弃位置对齐到 **下一个 IDR 帧的起始
TS 包**，保证客户端拿到的数据永远自含可解码。

用法
----
    python screen-cast.py                 # 扫描并交互选择设备
    python screen-cast.py -l              # 仅列出设备
    python screen-cast.py -n 1            # 直接选第 1 个设备
    python screen-cast.py --mode hls      # 用 HLS 模式（默认 mpegts）
    python screen-cast.py --quality 1080  # 画质档位 540/720/1080/1080hq/1440/2160
    python screen-cast.py --low-latency   # 一键流畅优先（x264 + 250ms 缓冲 + 30fps）
    python screen-cast.py --capture ddagrab  # 尝试 GPU 采集（帧率上限更高）
    python screen-cast.py --fps 60        # 提高帧率（需 --capture ddagrab 才可能吃满）
    python screen-cast.py --encoder mf    # 硬件编码（Intel/AMD 通用，CPU 几乎不占）
    python screen-cast.py --audio none    # 关闭声音
    python screen-cast.py --keep-local-audio  # 投屏时电脑仍出声（默认会静音电脑）
    python screen-cast.py --unmute        # 解除本机静音（被强杀后没声时用）
    python screen-cast.py --latency-ms 250   # 本机缓冲水位（毫秒），越小延迟越低
    python screen-cast.py --buffer 192    # 等价的字节写法（KB），与 --latency-ms 二选一
    python screen-cast.py --clock         # 打开毫秒时钟页，用于实测端到端延迟
    python screen-cast.py --no-push       # 只启动流服务，不推给电视（本机自测用）
    python screen-cast.py -v              # 显示 ffmpeg 详细日志

电视端低延迟播放（★ 强烈推荐，延迟可进 1 秒）
-------------------------------------------
端到端延迟的主体是**电视自带 DLNA 播放器的缓冲深度**，这一项在本机侧调节不了。
要真正把延迟降下来，只能绕开它——改用电视浏览器打开本程序自带的播放页：

    http://<本机IP>:<端口>/tv          （启动时会打印完整地址）

该页面用 mpegts.js 把 MPEG-TS 流直接喂给 <video>，缓冲深度由页面控制，不是
由电视厂商的播放器决定。页面提供三档可现场切换：
    · 极低延迟（0.5 s 触发追赶）—— 延迟最低，弱网下更易卡
    · 低延迟  （1.0 s，默认）    —— 推荐
    · 稳定优先（2.0 s）          —— 网络差时用
底部实时显示「画面延迟 / 缓冲 / 分辨率 / 帧率 / 丢帧 / 码率」，
按遥控器 OK 或空格键可显示/隐藏。

前提：电视有可用浏览器且支持 MSE 实时播放（Android TV 基本都支持）。
若浏览器不支持，页面会给出改用 VLC 的提示（把 VLC「网络缓冲」调到 300 ms）。

注意：该页面只在 mpegts 模式（默认）下提供；HLS 模式没有它。

电视端启动广告
--------------
部分电视在开始投屏时会先播 15 秒左右的开机/贴片广告，期间播放器
可能反复重建管道、只缓冲不消费。本程序默认会：
    · 推送后等待电视真正接入并正常消费；
    · 若超时未接入、或接入后长时间只缓冲不消化，自动 Stop + 重新推送，
      强制电视端丢弃旧缓冲重新拉流。
用 --push-retry 0 可关闭该行为。

停止：投屏中按 Ctrl+C
"""

import argparse
import atexit
import collections
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.sax.saxutils import escape

# 界面文案国际化。套 try 是为了极端情况（有人把这个文件单独拷出去当脚本跑）
# 也能退化运行：没有 i18n 模块就当纯中文，功能一点不少。
try:
    import i18n
except Exception:                                   # pragma: no cover
    class _I18nStub(object):
        LANG = "zh"

        @staticmethod
        def tr(s):
            return s

        @staticmethod
        def localize_html(s):
            return s

        @staticmethod
        def set_lang(code):
            return code

        @staticmethod
        def resolve(argv=None):
            return "zh"

        @staticmethod
        def is_en():
            return False

    i18n = _I18nStub()

# 短名。build_parser() 的 help 文案里出现几十次，写全名太吵。
T = i18n.tr

# ---------------------------------------------------------------- 常量

SSDP_MCAST_ADDR = "239.255.255.250"
SSDP_MCAST_PORT = 1900
ST_MEDIA_RENDERER = "urn:schemas-upnp-org:device:MediaRenderer:1"
ST_ALL = "ssdp:all"

DEFAULT_HTTP_PORT = 8899

# 首选端口不可用时的回退候选（按顺序试，都不行就让系统分配）。
# Windows 会用「保留区间」成段吞掉端口 —— Hyper-V / WSL2 / Docker 动态保留，
# 每次重启都可能变。实测本机 8880-9079 整段被占，8899 必然绑定失败。
# 详见 start_http_server()。
FALLBACK_PORTS = (9080, 9081, 9091, 18080, 28080, 38080, 48080, 58880)
DEFAULT_BITRATE = 2500

# ---------------------------------------------------------------- 冻结/资源路径
#
# 打包成 exe（PyInstaller onefile）后的三个目录要分清：
#   _MEIPASS         本次运行解包出的临时目录 —— static/ 在这里（只读、每次变）
#   sys.executable   用户双击的那个 exe 自己 —— 稳定；ffmpeg 自动下载也落这里
#   LOCALAPPDATA    可写的持久缓存 —— OSD 临时文件、崩溃日志
# 未打包时三者都退回脚本目录，开发体验完全不变。


def _bundle_dir():
    """只读资源目录（static/ 所在处）。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return meipass
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return os.path.dirname(os.path.abspath(sys.executable))


def _app_dir():
    """exe 自身所在目录；开发模式下等于脚本目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return _bundle_dir()


def _cache_dir():
    """可写的持久缓存目录。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "DlnaCast")


BUNDLE_DIR = _bundle_dir()
APP_DIR = _app_dir()
CACHE_DIR = _cache_dir()
STATIC_DIR = os.path.join(BUNDLE_DIR, "static")

# OSD 那两个临时文件必须落在「可写且稳定」的目录：
# 打包后 BUNDLE_DIR 是每次运行都会变的临时解包目录，写进去虽然能用，
# 但进程被杀时残留无法回收；CACHE_DIR 更合适。
RUNTIME_DIR = os.path.join(CACHE_DIR, "run")
try:
    os.makedirs(RUNTIME_DIR, exist_ok=True)
except Exception:
    RUNTIME_DIR = tempfile.gettempdir()

# 画质档位: (目标高度, 目标码率 kbps, 峰值码率 kbps)
#   target_h 是「上限高度」：源屏幕比它矮时不做上采样（放大不等于变清晰）
#
# ★ 码率上限由「电视端实际能持续消费的速率」倒推，不是拍脑袋定的。
#   真机实测（放剧集，1080p30）：编码产出 7956 kbps，但电视只能稳定吃下
#   约 7040 kbps —— 缺口 11.5% 全部转成持续丢弃 → 卡顿。
#   因此各档必须留出 ≥15% 余量：1080 档峰值不越过 6500 kbps。
#   同时把 x264 preset 从 ultrafast 提到 veryfast —— 同画质省约 10% 码率，
#   部分抵消降码率带来的画质损失（换句话说：码率降三成，画质降不到两成）。
QUALITY_PRESETS = {
    "540":    (540,   1200,  1400),
    "720":    (720,   2500,  2900),
    "1080":   (1080,  4500,  5200),
    "1080hq": (1080,  5500,  6500),
    "1440":   (1440, 14000, 17000),
    "2160":   (2160, 28000, 34000),
}

QUALITY_LABELS = {
    "540":    "540p   流畅（弱网/弱 CPU）",
    "720":    "720p   标清（默认）",
    "1080":   "1080p  高清",
    "1080hq": "1080p  高画质（推荐，已按电视端带宽上限调优）",
    "1440":   "2K     1440p（需屏幕本身 ≥1440p 才有效）",
    "2160":   "4K     2160p（需 4K 屏幕；软编需高性能 CPU）",
}

# 交互菜单里的固定顺序
QUALITY_ORDER = ["720", "1080", "1080hq", "1440", "2160", "540"]

# 编码时交织音视频包的最大时间跨度（微秒）。
# ffmpeg 默认 10 秒，会让音频包和视频包在 TS 里相距很远；一旦按字节丢弃
# 积压，两者被丢掉的"时间跨度"就会不一致，累积起来就是音画不同步。
INTERLEAVE_DELTA_US = 100000

FIREWALL_RULE_NAME = "DLNA Screen Cast"

# ---------------------------------------------------------------- 工具


# 日志出口。命令行模式下 _LOG_SINKS 为空；GUI 会把回调挂上来。
_LOG_SINKS = []


def add_log_sink(fn):
    """挂一个 (text, is_err) 回调，用于把日志同时送进图形界面。"""
    if fn not in _LOG_SINKS:
        _LOG_SINKS.append(fn)


def remove_log_sink(fn):
    try:
        _LOG_SINKS.remove(fn)
    except ValueError:
        pass


def _emit_log(text, is_err):
    for fn in list(_LOG_SINKS):
        try:
            fn(text, is_err)
        except Exception:
            pass


def log(msg, *args):
    # 翻译必须发生在 % 格式化**之前** —— 翻译表的 key 是带 %s/%d 的模板，
    # 格式化之后数字已经填进去，就再也匹配不上了。
    msg = i18n.tr(msg)
    if args:
        # 参数里同样可能带文案（画质档位标签、退出码含义、设备状态…），
        # 一并翻掉；非字符串（数字、异常对象）原样传下去。
        msg = msg % tuple(i18n.tr(a) if isinstance(a, str) else a
                          for a in args)
    # ★ --windowed 打包后 sys.stdout 为 None，直接 print 会抛 AttributeError
    try:
        if sys.stdout is not None:
            print(msg, flush=True)
    except Exception:
        pass
    _emit_log(msg, False)


def err(msg, *args):
    msg = i18n.tr(msg)
    if args:
        msg = msg % tuple(i18n.tr(a) if isinstance(a, str) else a
                          for a in args)
    try:
        if sys.stderr is not None:
            print(msg, file=sys.stderr, flush=True)
    except Exception:
        pass
    _emit_log(msg, True)


class CastCancelled(Exception):
    """用户中途取消（GUI 的「停止」按钮）。"""


# 运行期的取消信号。命令行模式下为 None（靠 Ctrl+C）；
# GUI 模式下由 run_cast() 注入 threading.Event。内部那些长等待
# （电视接入确认、重推之间的间隔）只能用它来提前退出，否则点了停止
# 还要干等十几秒才响应。
_STOP_EVENT = None


def stop_requested():
    ev = _STOP_EVENT
    return ev is not None and ev.is_set()


def _check_stop():
    if stop_requested():
        raise CastCancelled()


# ---------------------------------------------------------------- 网络适配器

# 枚举结果缓存。TUN 开关会改变适配器列表，所以 TTL 给得很短。
_ADAPTER_CACHE = {"t": 0.0, "list": None}

# 名称/描述里出现这些词 → 判定为虚拟网卡（含 Windows 自带的那些）。
# 刻意不含 "tunnel"：Teredo/ISATAP 的描述里带 "Tunneling"，
# 但它们另有 "teredo"/"isatap" 专门命中，不会漏，也不会误伤。
_VIRTUAL_HINTS = (
    "wintun", "wireguard", "tailscale", "zerotier", "clash", "mihomo",
    "sing-box", "singbox", "nekoray", "nekobox", "v2ray", "xray", "openvpn",
    "tap-windows", "utun", "nordlynx", "surge",
    "hyper-v", "vethernet", "wi-fi direct", "wifi direct", "bluetooth",
    "teredo", "isatap", "loopback", "personal area network",
    "vmware", "virtualbox", "vbox", "docker", "npcap",
)

# 更窄的一组，只用于「疑似代理 TUN」提示 —— 不含 hyper-v / 蓝牙，
# 免得把 WSL 和蓝牙网络也报成代理。
_TUN_HINTS = (
    "wintun", "wireguard", "tailscale", "zerotier", "clash", "mihomo",
    "sing-box", "singbox", "nekoray", "nekobox", "v2ray", "xray", "openvpn",
    "tap-windows", "utun", "nordlynx", "surge", "vpn", "proxy",
)

# Windows 专属：关掉「ICMP 端口不可达 → 连接重置」的映射。
# 网段扫描会打中大量不存在的 IP，不关的话每次 recvfrom 都立刻抛
# ConnectionResetError，形成忙循环把 CPU 打满。
_SIO_UDP_CONNRESET = 0x9800000C


def _enumerate_adapters():
    """用 GetAdaptersAddresses 枚举本机网络适配器（ctypes，零外部依赖）。

    返回 [{name, desc, ip, prefix, up, iftype}]；任何一步失败都返回 []，
    调用方必须能容忍空列表 —— 绝不允许因为「读不到网卡信息」而崩掉投屏。
    """
    try:
        import ctypes
    except Exception:
        return []

    class _SOCKADDR(ctypes.Structure):
        _fields_ = [("lpSockaddr", ctypes.c_void_p),
                    ("iSockaddrLength", ctypes.c_int)]

    class _UNICAST(ctypes.Structure):
        pass
    _UNICAST._fields_ = [
        ("Length", ctypes.c_ulong),
        ("Flags", ctypes.c_ulong),
        ("Next", ctypes.POINTER(_UNICAST)),
        ("Address", _SOCKADDR),
        ("PrefixOrigin", ctypes.c_int),
        ("SuffixOrigin", ctypes.c_int),
        ("DadState", ctypes.c_int),
        ("ValidLifetime", ctypes.c_ulong),
        ("PreferredLifetime", ctypes.c_ulong),
        ("LeaseLifetime", ctypes.c_ulong),
        ("OnLinkPrefixLength", ctypes.c_ubyte),
    ]

    class _ADAPTER(ctypes.Structure):
        pass
    _ADAPTER._fields_ = [
        ("Length", ctypes.c_ulong),
        ("IfIndex", ctypes.c_ulong),
        ("Next", ctypes.POINTER(_ADAPTER)),
        ("AdapterName", ctypes.c_char_p),
        ("FirstUnicastAddress", ctypes.POINTER(_UNICAST)),
        ("FirstAnycastAddress", ctypes.c_void_p),
        ("FirstMulticastAddress", ctypes.c_void_p),
        ("FirstDnsServerAddress", ctypes.c_void_p),
        ("DnsSuffix", ctypes.c_wchar_p),
        ("Description", ctypes.c_wchar_p),
        ("FriendlyName", ctypes.c_wchar_p),
        ("PhysicalAddress", ctypes.c_ubyte * 8),
        ("PhysicalAddressLength", ctypes.c_ulong),
        ("Flags", ctypes.c_ulong),
        ("Mtu", ctypes.c_ulong),
        ("IfType", ctypes.c_ulong),
        ("OperStatus", ctypes.c_int),
        ("Ipv6IfIndex", ctypes.c_ulong),
        ("ZoneIndices", ctypes.c_ulong * 16),
        ("FirstPrefix", ctypes.c_void_p),
    ]

    try:
        iphlp = ctypes.WinDLL("iphlpapi.dll")
        iphlp.GetAdaptersAddresses.argtypes = [
            ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        iphlp.GetAdaptersAddresses.restype = ctypes.c_ulong
    except Exception:
        return []

    AF_UNSPEC = 0
    SKIP = 0x2 | 0x4 | 0x8           # 跳过 anycast / multicast / DNS 服务器
    size = ctypes.c_ulong(16384)
    buf = ctypes.create_string_buffer(size.value)
    rc = iphlp.GetAdaptersAddresses(AF_UNSPEC, SKIP, None, buf,
                                    ctypes.byref(size))
    if rc == 111:                    # ERROR_BUFFER_OVERFLOW：按返回的大小重来
        buf = ctypes.create_string_buffer(size.value)
        rc = iphlp.GetAdaptersAddresses(AF_UNSPEC, SKIP, None, buf,
                                        ctypes.byref(size))
    if rc != 0:
        return []

    out = []
    p = ctypes.cast(buf, ctypes.POINTER(_ADAPTER))
    guard = 0
    while p and guard < 512:
        a = p.contents
        guard += 1
        ip, prefix = "", 0
        ua, n = a.FirstUnicastAddress, 0
        while ua and n < 64:
            u = ua.contents
            n += 1
            if u.Address.lpSockaddr:
                raw = ctypes.string_at(u.Address.lpSockaddr, 16)
                if int.from_bytes(raw[0:2], "little") == 2:        # AF_INET
                    ip = socket.inet_ntoa(raw[4:8])
                    prefix = int(u.OnLinkPrefixLength)
                    break
            ua = u.Next
        out.append({
            "name": a.FriendlyName or "",
            "desc": a.Description or "",
            "ip": ip,
            "prefix": prefix,
            "up": int(a.OperStatus) == 1,
            "iftype": int(a.IfType),
        })
        p = a.Next
    return out


# Windows 自带的隧道适配器 —— 它们的 iftype 同样是 TUNNEL(131)，
# 但跟代理毫无关系，必须排除，否则 Teredo 一启动就会被报成「代理 TUN」。
_NOT_PROXY_TUNNEL = ("teredo", "isatap", "6to4", "iphttpsinterface",
                     "microsoft tunnel", "directaccess")


def _is_tun_like(name, desc, iftype=0):
    """判断某个适配器是不是「代理 / VPN 的 TUN 口」。

    判据刻意保守。最硬的一条是 "wintun" —— WinTun 驱动的适配器描述固定为
    "Wintun Userspace Tunnel"，而 Clash.Meta / Mihomo / sing-box / NekoRay
    全都基于它，所以命中 wintun 必然是真正的 TUN。
    """
    text = ("%s %s" % (name, desc)).lower()
    if any(x in text for x in _NOT_PROXY_TUNNEL):
        return False
    if "wintun" in text:
        return True
    if any(h in text for h in _TUN_HINTS):
        return True
    if iftype == 131:                # IF_TYPE_TUNNEL
        return True
    # 适配器名恰好叫 tun / tun0 / tap0 …（用词边界，避免 Teredo 的
    # "Tunneling" 被误判）
    return re.search(r"(^|[^a-z])(tun|tap)([^a-z]|$)", text) is not None


def list_adapters(force=False):
    """带 2 秒缓存的适配器列表，并标好 virtual / tun 两个标志。"""
    now = time.time()
    if (not force and _ADAPTER_CACHE["list"] is not None
            and now - _ADAPTER_CACHE["t"] < 2.0):
        return _ADAPTER_CACHE["list"]
    res = _enumerate_adapters()
    for a in res:
        text = ("%s %s" % (a["name"], a["desc"])).lower()
        a["virtual"] = (a["iftype"] in (24, 131)
                        or not text.strip()
                        or any(h in text for h in _VIRTUAL_HINTS))
        a["tun"] = _is_tun_like(a["name"], a["desc"], a["iftype"])
    _ADAPTER_CACHE["t"] = now
    _ADAPTER_CACHE["list"] = res
    return res


def _is_private_ipv4(ip):
    try:
        o = [int(x) for x in ip.split(".")]
    except Exception:
        return False
    if len(o) != 4 or any(x < 0 or x > 255 for x in o):
        return False
    return (o[0] == 10
            or (o[0] == 172 and 16 <= o[1] <= 31)
            or (o[0] == 192 and o[1] == 168))


def _belongs_to_virtual(ip, adapters):
    """ip 是否落在某块虚拟网卡上。

    枚举不到该 ip 时按 False 处理 —— 保持「读不到网卡信息 = 维持旧行为」，
    但补一条硬编码兜底：Clash / Mihomo 的默认 TUN 地址是 198.18.0.0/15
    （RFC 2544 基准测试段），正常局域网不会用，命中就一定是 TUN。
    """
    for a in adapters:
        if a["ip"] == ip:
            return a["virtual"]
    return ip.startswith("198.18.") or ip.startswith("198.19.")


def pick_physical_ipv4(adapters=None):
    """挑一块「真实」网卡的 IPv4 地址，优先局域网段；找不到返回 None。"""
    ads = adapters if adapters is not None else list_adapters()
    cands = []
    for a in ads:
        ip = a["ip"]
        if not ip or not a["up"] or a["virtual"]:
            continue
        if ip.startswith("127.") or ip.startswith("169.254."):
            continue
        cands.append(ip)
    for ip in cands:
        if _is_private_ipv4(ip):
            return ip
    return cands[0] if cands else None


def detect_tun_adapters():
    """正在工作的 TUN / VPN 适配器名字列表（用于诊断提示）。"""
    return sorted({(a["name"] or a["desc"])
                   for a in list_adapters() if a["tun"] and a["up"] and a["name"]})


def _probe_route_ip(target):
    """让内核替我们选路：返回访问 target 时会用到的源 IP。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((target, 80))
        return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"
    finally:
        s.close()


def get_local_ip(target=None):
    """获取本机的局域网地址。

    首选「到公网探路」的结果 —— 正常网络下它最准，多网卡会自动选中真正
    能出网的那块。但代理软件的 TUN 模式会把默认路由指向虚拟网卡，此时探路
    返回的是虚拟网卡 IP（Clash / Mihomo 默认 198.18.0.1），拿它去发 SSDP
    组播必然发不出去 —— 用户看到的现象就是「一开代理就扫不到电视」。

    所以这里多一道校验：探路结果若落在虚拟网卡上，改用物理网卡的地址。
    校验失败（读不到网卡信息）时完全退回旧行为，不会因为诊断逻辑而变差。
    """
    probe = _probe_route_ip(target or "223.5.5.5")
    ads = list_adapters()
    if probe and not _belongs_to_virtual(probe, ads):
        return probe
    phys = pick_physical_ipv4(ads)
    if phys:
        log("[网络] 默认路由被虚拟网卡接管（探路得到 %s），改用物理网卡 %s",
            probe or "无", phys)
        return phys
    return probe or "127.0.0.1"


def find_ffmpeg():
    """定位 ffmpeg 可执行文件。

    优先级刻意这样排：
      1) FFMPEG_PATH        —— 显式指定，永远最高
      2) exe 同目录         —— 自动下载的落点；也方便用户放自己那份
      3) 解包缓存（旧版）    —— 兼容曾经内嵌过 ffmpeg 的版本
      4) 常见安装路径       —— 开发/老环境兼容
      5) PATH

    exe 同目录排在常见路径之前，是因为自动下载的一定是为了这个程序准备的；
    D:\\ffmpeg 那份可能被换过、缺 mediafoundation 或 ddagrab。
    """
    candidates = [
        os.environ.get("FFMPEG_PATH", ""),
        os.path.join(APP_DIR, "ffmpeg.exe"),
        os.path.join(CACHE_DIR, "bin", "ffmpeg.exe"),
        r"D:\ffmpeg\ffmpeg.exe",
        r"D:\ffmpeg\bin\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    w = shutil.which("ffmpeg")
    if w:
        return w
    return None


def no_proxy_opener():
    """禁用系统代理（否则请求会被代理劫持，导致局域网访问失败）。"""
    handler = urllib.request.ProxyHandler({})
    return urllib.request.build_opener(handler)


OPENER = None


def http_get(url, timeout=5, headers=None):
    global OPENER
    if OPENER is None:
        OPENER = no_proxy_opener()
    req = urllib.request.Request(url, headers=headers or {})
    with OPENER.open(req, timeout=timeout) as r:
        return r.read()


def soap_call(control_url, service, action, inner_xml="", timeout=8):
    """发送 UPnP SOAP 请求，返回响应文本。"""
    global OPENER
    if OPENER is None:
        OPENER = no_proxy_opener()
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        "<s:Body>"
        '<u:%s xmlns:u="%s">%s</u:%s>'
        "</s:Body></s:Envelope>" % (action, service, inner_xml, action)
    )
    req = urllib.request.Request(
        control_url,
        data=body.encode("utf-8"),
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPAction": '"%s#%s"' % (service, action),
            "Connection": "close",
        },
        method="POST",
    )
    try:
        with OPENER.open(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.read().decode("utf-8", "replace")
    except Exception as e:
        raise RuntimeError("%s 调用失败: %s" % (action, e))


# ---------------------------------------------------------------- SSDP 发现


class Device(object):
    def __init__(self, name, location, udn, ip):
        self.name = name
        self.location = location
        self.udn = udn
        self.ip = ip
        self.services = {}   # serviceType -> controlURL(绝对)
        self.url_base = ""

    def __repr__(self):
        return "<Device %s @ %s>" % (self.name, self.ip)


def ssdp_search(local_ip, timeout=4.0, st=ST_MEDIA_RENDERER):
    """发送 SSDP M-SEARCH，返回 {udn: location} 去重后的原始响应。"""
    msg = (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: %s:%d\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 2\r\n"
        "ST: %s\r\n"
        "\r\n" % (SSDP_MCAST_ADDR, SSDP_MCAST_PORT, st)
    ).encode("ascii")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                        socket.inet_aton(local_ip))
    except Exception:
        pass
    try:
        sock.bind((local_ip, 0))
    except Exception:
        try:
            sock.bind(("", 0))
        except Exception:
            pass
    sock.settimeout(0.5)

    found = {}          # udn -> location
    locations = set()
    try:
        sock.sendto(msg, (SSDP_MCAST_ADDR, SSDP_MCAST_PORT))
    except Exception as e:
        err("SSDP 发送失败: %s", e)

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data, addr = sock.recvfrom(65535)
        except socket.timeout:
            continue
        except Exception:
            break
        text = data.decode("utf-8", "replace")
        loc = None
        usn = None
        for line in text.split("\r\n"):
            if line.lower().startswith("location:"):
                loc = line.split(":", 1)[1].strip()
            elif line.lower().startswith("usn:"):
                usn = line.split(":", 1)[1].strip()
        if loc:
            locations.add((loc, usn or loc))
    sock.close()

    # 抓取 description，按 UDN 去重
    devs = {}
    for loc, usn in locations:
        try:
            dev = describe_device(loc)
        except Exception:
            continue
        if dev is None:
            continue
        key = dev.udn or loc
        if key not in devs:
            devs[key] = dev
    return list(devs.values())


def ssdp_search_unicast(ip, local_ip, timeout=1.5):
    """对指定 IP 单播 M-SEARCH（组播被挡时的兜底）。"""
    msg = (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: %s:%d\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 1\r\n"
        "ST: %s\r\n"
        "\r\n" % (SSDP_MCAST_ADDR, SSDP_MCAST_PORT, ST_MEDIA_RENDERER)
    ).encode("ascii")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(msg, (ip, SSDP_MCAST_PORT))
        data, addr = sock.recvfrom(65535)
    except Exception:
        sock.close()
        return None
    sock.close()
    text = data.decode("utf-8", "replace")
    for line in text.split("\r\n"):
        if line.lower().startswith("location:"):
            try:
                return describe_device(line.split(":", 1)[1].strip())
            except Exception:
                return None
    return None


def _tag_text(block, tag):
    m = re.search(r"<%s>(.*?)</%s>" % (tag, tag), block, re.S)
    return m.group(1).strip() if m else ""


def describe_device(location):
    """拉取并解析 UPnP device description。"""
    raw = http_get(location, timeout=5).decode("utf-8", "replace")

    # friendlyName / UDN
    name = _tag_text(raw, "friendlyName") or "未知设备"
    udn = _tag_text(raw, "UDN") or ""

    # URLBase
    m = re.search(r"<URLBase>(.*?)</URLBase>", raw, re.S)
    if m:
        url_base = m.group(1).strip().rstrip("/")
    else:
        url_base = re.sub(r"/[^/]*$", "", location)

    ip = ""
    m = re.search(r"https?://([0-9.]+)", location)
    if m:
        ip = m.group(1)

    dev = Device(name, location, udn, ip)
    dev.url_base = url_base

    # 逐个 <service> 块解析（避免正则跨块误匹配）
    for blk in re.findall(r"<service>.*?</service>", raw, re.S):
        stype = _tag_text(blk, "serviceType")
        ctrl = _tag_text(blk, "controlURL")
        if not stype or not ctrl:
            continue
        if ctrl.lower().startswith("http"):
            full = ctrl
        else:
            full = url_base + "/" + ctrl.lstrip("/")
        # serviceType 形如 urn:schemas-upnp-org:service:AVTransport:1
        parts = stype.split(":")
        short = parts[-2] if len(parts) >= 2 else stype
        dev.services[short] = (stype, full)
    return dev


def probe_device(dev, timeout=2.5):
    """可用性预检：AVTransport 端点能否正常响应。

    有些设备（如电视内置的第三方音乐投屏服务）会通过 SSDP 广播，
    但其 controlURL 指向的端口并不监听，直接推送会报 WinError 10061。
    """
    avt = dev.services.get("AVTransport")
    if not avt:
        return False
    stype, ctrl = avt
    try:
        r = soap_call(ctrl, stype, "GetTransportInfo",
                      "<InstanceID>0</InstanceID>", timeout=timeout)
        return "GetTransportInfoResponse" in r
    except Exception:
        return False


def scan_subnet(local_ip, timeout=2.0, prefix=24):
    """对本网段逐个发单播 M-SEARCH，返回 Device 列表。

    组播在两种场景下不可靠：代理 TUN 会吞掉组播，部分企业 AP 也默认屏蔽
    IGMP。单播 M-SEARCH 是 DLNA 标准允许的（很多控制点就靠它），绕开组播
    即可。一个 /24 网段总共 254 个 UDP 包，一次发完再统一收，不构成风暴。
    """
    if not _is_private_ipv4(local_ip or ""):
        return []
    base = local_ip.rsplit(".", 1)[0]
    msg = (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: %s:%d\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 1\r\n"
        "ST: %s\r\n"
        "\r\n" % (SSDP_MCAST_ADDR, SSDP_MCAST_PORT, ST_MEDIA_RENDERER)
    ).encode("ascii")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.ioctl(_SIO_UDP_CONNRESET, 0)
    except Exception:
        pass
    try:
        sock.bind((local_ip, 0))
    except Exception:
        try:
            sock.bind(("", 0))
        except Exception:
            pass
    sock.settimeout(0.35)

    p = max(16, min(30, prefix))
    count = min((1 << (32 - p)) - 2, 1024)
    for i in range(1, count + 1):
        try:
            sock.sendto(msg, ("%s.%d" % (base, i), SSDP_MCAST_PORT))
        except Exception:
            pass

    locs, seen = [], set()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data, _addr = sock.recvfrom(65535)
        except socket.timeout:
            continue
        except Exception:
            time.sleep(0.01)          # 收到 ICMP 错误：歇一下，别忙循环
            continue
        for line in data.decode("utf-8", "replace").split("\r\n"):
            if line.lower().startswith("location:"):
                loc = line.split(":", 1)[1].strip()
                if loc not in seen:
                    seen.add(loc)
                    locs.append(loc)
    sock.close()

    devs = []
    for loc in locs:
        try:
            d = describe_device(loc)
        except Exception:
            d = None
        if d:
            devs.append(d)
    return devs


def network_diagnosis():
    """扫描不到设备时的诊断建议（字符串列表，供界面/日志逐行显示）。"""
    tuns = detect_tun_adapters()
    if not tuns:
        return [
            "排查建议：",
            "  1) 确认电视与电脑在同一个局域网（同一路由器、同一网段）",
            "  2) 电视要打开「多屏互动 / DLNA / 媒体共享」这类开关",
            "  3) 也可以直接在「电视 IP」框里填电视的 IP，填了就跳过扫描",
        ]
    phys = pick_physical_ipv4()
    seg = (phys.rsplit(".", 1)[0] + ".0/24") if phys else "你的局域网段"
    return [
        "★ 检测到代理/VPN 的 TUN 虚拟网卡「%s」正在运行。" % "、".join(tuns),
        "  TUN 模式会抢走默认路由，DLNA 的组播发现会被它吃掉，所以扫不到电视。",
        "  三种解法任选其一：",
        "   ① 把局域网加进代理的绕过列表（推荐，设一次永久生效）：",
        "      Clash / Mihomo → tun.route-exclude-address 里加 %s" % seg,
        "      其它客户端 → 打开「绕过局域网 / Bypass LAN」开关",
        "   ② 临时关掉 TUN 模式（切到「系统代理 / PAC」模式就行，",
        "      不影响上网，也不影响局域网扫描）",
        "   ③ 直接把电视 IP 填进上面的输入框（最省事）",
    ]


def discover_devices(local_ip, timeout=4.0, extra_ips=None, verify=True):
    """发现设备：组播 → 网段单播 → 指定 IP 单播，逐级兜底，按 UDN 去重。

    组播排在第一位是因为它最快最标准；但它同时也是最容易被拦的一种
    （代理 TUN、企业 AP 隔离、跨 VLAN 都会吃掉它），所以后两级兜底
    不是可有可无的。
    """
    devs = {}
    for d in ssdp_search(local_ip, timeout=timeout):
        devs[d.udn or d.ip] = d

    if not devs:
        for d in scan_subnet(local_ip):
            devs[d.udn or d.ip] = d
        if devs:
            log("（组播无响应，已通过网段单播找到 %d 个设备）", len(devs))

    if not devs and extra_ips:
        for ip in extra_ips:
            d = ssdp_search_unicast(ip, local_ip)
            if d:
                devs[d.udn or d.ip] = d

    result = list(devs.values())
    if verify and result:
        alive = [d for d in result if probe_device(d)]
        if alive and len(alive) < len(result):
            log("（已自动跳过 %d 个不可达的渲染端点）", len(result) - len(alive))
        if alive:
            result = alive
    return result


# ---------------------------------------------------------------- WASAPI 环回音频
#
# 为什么不用 ffmpeg 的 dshow：
#   本机 Realtek 的「立体声混音」被系统禁用，而 HKLM\...\MMDevices 注册表键
#   归 NT AUTHORITY\SYSTEM 所有，管理员态也写不进去；且 ffmpeg 官方 Windows
#   构建未编译 WASAPI 输入设备（-devices 只有 dshow / openal）。
#
# 因此这里用 Windows 官方 WASAPI loopback 直接捕获「默认播放设备」的输出，
# 以 32-bit float PCM 经管道喂给 ffmpeg。
# 优点：不改注册表、不动声卡设置、不影响你正常听声；失败时自动降级为无声。

_WIN = sys.platform == "win32"

if _WIN:
    import ctypes
    from ctypes import wintypes, POINTER, byref, c_void_p, c_uint32, c_uint64

    _ole32 = ctypes.OleDLL("ole32")
    _HRESULT = ctypes.c_long
    _P32 = POINTER(c_uint32)
    _P64 = POINTER(c_uint64)
    _PB = POINTER(ctypes.c_ubyte)

    class _GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_uint32),
                    ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16),
                    ("Data4", ctypes.c_ubyte * 8)]

    class _WAVEFORMATEX(ctypes.Structure):
        _fields_ = [("wFormatTag", ctypes.c_uint16),
                    ("nChannels", ctypes.c_uint16),
                    ("nSamplesPerSec", ctypes.c_uint32),
                    ("nAvgBytesPerSec", ctypes.c_uint32),
                    ("nBlockAlign", ctypes.c_uint16),
                    ("wBitsPerSample", ctypes.c_uint16),
                    ("cbSize", ctypes.c_uint16)]

    _ole32.CLSIDFromString.argtypes = [wintypes.LPCWSTR, POINTER(_GUID)]
    _ole32.CLSIDFromString.restype = _HRESULT
    _ole32.CoInitializeEx.argtypes = [c_void_p, ctypes.c_uint32]
    _ole32.CoInitializeEx.restype = _HRESULT
    _ole32.CoCreateInstance.argtypes = [POINTER(_GUID), c_void_p, ctypes.c_uint32,
                                        POINTER(_GUID), POINTER(c_void_p)]
    _ole32.CoCreateInstance.restype = _HRESULT
    _ole32.CoUninitialize.argtypes = []

    def _mkguid(s):
        g = _GUID()
        _ole32.CLSIDFromString(s, byref(g))
        return g

    _CLSID_MMDE = _mkguid("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
    _IID_MMDE = _mkguid("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _IID_AC = _mkguid("{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}")
    _IID_ACC = _mkguid("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")
    _IID_AEV = _mkguid("{5CDF2C82-841E-4546-9722-0CF74078229A}")

    def _vcall(p, index, restype, argtypes, *args):
        """按 vtable 索引调用 COM 方法（p 可为 c_void_p 实例或裸地址）。"""
        addr = p.value if isinstance(p, c_void_p) else p
        vtbl = ctypes.cast(c_void_p(addr), POINTER(POINTER(c_void_p)))[0]
        proto = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
        return proto(vtbl[index])(addr, *args)


class WasapiLoopback(object):
    """捕获默认播放设备输出的 PCM，即「电脑正在播放的声音」。"""

    _CLSCTX_ALL = 0x17
    _SHARED = 0
    _LOOPBACK = 0x00020000
    _SILENT = 0x0002

    def __init__(self, buffer_ms=120):
        if not _WIN:
            raise RuntimeError("WASAPI 环回仅支持 Windows")
        self.channels = 2
        self.rate = 48000
        self.bits = 32
        self.block_align = 8
        self._buffer_ms = buffer_ms
        self._com_inited = False
        self._pFmt = None
        self.pEnum = self.pDev = self.pClient = self.pCap = None
        self.pVol = None                  # IAudioEndpointVolume（用于静音本地）
        self._mute_original = None        # 静音前用户原本的静音状态
        self._mute_applied = False
        self._running = False

    def open(self, loopback=True):
        hr = _ole32.CoInitializeEx(None, 0)
        self._com_inited = hr in (0, 1)

        self.pEnum = c_void_p()
        hr = _ole32.CoCreateInstance(byref(_CLSID_MMDE), None, self._CLSCTX_ALL,
                                     byref(_IID_MMDE), byref(self.pEnum))
        if hr != 0:
            raise RuntimeError("创建 MMDeviceEnumerator 失败 hr=0x%08X" % (hr & 0xFFFFFFFF))

        # IMMDeviceEnumerator::GetDefaultAudioEndpoint(eRender=0, eConsole=0)
        self.pDev = c_void_p()
        hr = _vcall(self.pEnum, 4, _HRESULT,
                    [ctypes.c_int, ctypes.c_int, POINTER(c_void_p)],
                    0, 0, byref(self.pDev))
        if hr != 0:
            raise RuntimeError("获取默认播放设备失败 hr=0x%08X" % (hr & 0xFFFFFFFF))

        # IMMDevice::Activate(IAudioEndpointVolume) —— 静音/取消静音本地扬声器。
        # 失败不影响投屏，只是无法实现"声音只从电视出"。
        pVol = c_void_p()
        hr = _vcall(self.pDev, 3, _HRESULT,
                    [POINTER(_GUID), ctypes.c_uint32, c_void_p, POINTER(c_void_p)],
                    byref(_IID_AEV), self._CLSCTX_ALL, None, byref(pVol))
        self.pVol = pVol if hr == 0 else None

        if not loopback:
            return self        # 只需音量接口时（如 --unmute）不必打开环回

        # IMMDevice::Activate(IAudioClient)
        self.pClient = c_void_p()
        hr = _vcall(self.pDev, 3, _HRESULT,
                    [POINTER(_GUID), ctypes.c_uint32, c_void_p, POINTER(c_void_p)],
                    byref(_IID_AC), self._CLSCTX_ALL, None, byref(self.pClient))
        if hr != 0:
            raise RuntimeError("激活 IAudioClient 失败 hr=0x%08X" % (hr & 0xFFFFFFFF))

        pFmt = POINTER(_WAVEFORMATEX)()
        hr = _vcall(self.pClient, 8, _HRESULT,
                    [POINTER(POINTER(_WAVEFORMATEX))], byref(pFmt))
        if hr != 0:
            raise RuntimeError("GetMixFormat 失败 hr=0x%08X" % (hr & 0xFFFFFFFF))
        f = pFmt.contents
        self.channels = f.nChannels
        self.rate = f.nSamplesPerSec
        self.bits = f.wBitsPerSample
        self.block_align = f.nBlockAlign
        self._pFmt = pFmt

        # IAudioClient::Initialize(SHARED, LOOPBACK, ...)
        dur = int(self._buffer_ms * 10000)      # ms -> 100ns 单位
        hr = _vcall(self.pClient, 3, _HRESULT,
                    [ctypes.c_int, ctypes.c_uint32, ctypes.c_longlong,
                     ctypes.c_longlong, POINTER(_WAVEFORMATEX), c_void_p],
                    self._SHARED, self._LOOPBACK, dur, 0, pFmt, None)
        if hr != 0:
            raise RuntimeError("Initialize(LOOPBACK) 失败 hr=0x%08X" % (hr & 0xFFFFFFFF))

        self.pCap = c_void_p()
        hr = _vcall(self.pClient, 14, _HRESULT,
                    [POINTER(_GUID), POINTER(c_void_p)],
                    byref(_IID_ACC), byref(self.pCap))
        if hr != 0:
            raise RuntimeError("GetService(IAudioCaptureClient) 失败 hr=0x%08X" % (hr & 0xFFFFFFFF))

        hr = _vcall(self.pClient, 10, _HRESULT, [])
        if hr != 0:
            raise RuntimeError("IAudioClient::Start 失败 hr=0x%08X" % (hr & 0xFFFFFFFF))

        self._running = True
        return self

    def read(self, timeout=0.05):
        """读取一段已捕获的 PCM 数据；无数据时返回空 bytes。"""
        out = bytearray()
        deadline = time.time() + timeout
        while self._running:
            pkt = c_uint32()
            hr = _vcall(self.pCap, 5, _HRESULT, [_P32], byref(pkt))
            if hr != 0:
                break
            if pkt.value == 0:
                if out or time.time() >= deadline:
                    break
                time.sleep(0.002)
                continue
            buf = _PB()
            frames = c_uint32()
            flags = c_uint32()
            hr = _vcall(self.pCap, 3, _HRESULT,
                        [POINTER(_PB), _P32, _P32, _P64, _P64],
                        byref(buf), byref(frames), byref(flags), None, None)
            if hr != 0:
                break
            n = frames.value
            if n:
                size = n * self.block_align
                if flags.value & self._SILENT:
                    out.extend(b"\x00" * size)
                else:
                    out.extend(ctypes.string_at(buf, size))
            _vcall(self.pCap, 4, _HRESULT, [c_uint32], c_uint32(n))
        return bytes(out)

    # ---- 本地静音（把声音"转移"到电视）----
    #
    # 原理与实测依据：
    #   静音由 IAudioEndpointVolume 作用在端点的**硬件输出层**，位于 WASAPI
    #   环回采样点之后，因此静音后环回仍能取到完全相同的真实数据。
    #   实测对照（同一段 440 Hz 测试音，各采 3 秒）：
    #       未静音  峰值 0.08838 / 有声帧 148967
    #       已静音  峰值 0.08838 / 有声帧 149032
    #   两列数据一致，证明该方案可行。
    #
    #   好处：不切默认设备、不改注册表、不动声卡设置，退出时原样恢复。
    _AEV_SETMUTE = 14
    _AEV_GETMUTE = 15

    def mute_local(self):
        """静音本地播放端点，返回原静音状态(0/1)；不可用时返回 None。"""
        if not self.pVol:
            return None
        cur = ctypes.c_int(-1)
        hr = _vcall(self.pVol, self._AEV_GETMUTE, _HRESULT,
                    [POINTER(ctypes.c_int)], byref(cur))
        if hr != 0:
            return None
        hr = _vcall(self.pVol, self._AEV_SETMUTE, _HRESULT,
                    [ctypes.c_int, c_void_p], 1, None)
        if hr != 0:
            return None
        self._mute_original = cur.value
        self._mute_applied = True
        return cur.value

    def restore_local(self):
        """恢复用户原本的静音状态。幂等，可重复调用。"""
        if not (self._mute_applied and self.pVol):
            return False
        want = self._mute_original if self._mute_original in (0, 1) else 0
        ok = False
        try:
            hr = _vcall(self.pVol, self._AEV_SETMUTE, _HRESULT,
                        [ctypes.c_int, c_void_p], want, None)
            ok = (hr == 0)
        except Exception:
            ok = False
        self._mute_applied = False
        return ok

    def set_local_mute(self, muted):
        """直接把本机静音状态设为指定值（用于 --unmute 解除残留静音）。"""
        if not self.pVol:
            return None
        hr = _vcall(self.pVol, self._AEV_SETMUTE, _HRESULT,
                    [ctypes.c_int, c_void_p], 1 if muted else 0, None)
        return hr == 0

    def close(self):
        self._running = False
        # 释放前务必恢复本地声音，否则退出后电脑会一直没声
        try:
            self.restore_local()
        except Exception:
            pass
        if self.pClient:
            try:
                _vcall(self.pClient, 11, _HRESULT, [])      # Stop
            except Exception:
                pass
        for p in (self.pCap, self.pClient, self.pVol, self.pDev, self.pEnum):
            if p:
                try:
                    _vcall(p, 2, ctypes.c_ulong, [])        # Release
                except Exception:
                    pass
        self.pCap = self.pClient = self.pVol = self.pDev = self.pEnum = None
        if self._com_inited:
            try:
                _ole32.CoUninitialize()
            except Exception:
                pass

    @property
    def sample_fmt(self):
        """对应的 ffmpeg 采样格式名。"""
        if self.bits == 32:
            return "f32le"
        if self.bits == 16:
            return "s16le"
        return "s32le"


def install_console_close_guard(loopback):
    """关闭控制台窗口 / 注销 / 关机时也恢复本机声音。

    Ctrl+C 会走 KeyboardInterrupt -> finally，能正常恢复；但用户直接点窗口
    的关闭按钮时 Python 收不到 KeyboardInterrupt，等同于被强杀，atexit 也不
    保证执行，结果就是电脑一直没声。这里注册 ConsoleCtrlHandler 兜住这类退出。

    返回回调对象（必须持有引用，否则被回收后回调会崩）；不支持时返回 None。
    """
    if not _WIN:
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    HANDLER = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
    CTRL_CLOSE_EVENT = 2
    CTRL_LOGOFF_EVENT = 5
    CTRL_SHUTDOWN_EVENT = 6
    interesting = (CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT)

    def _handler(ctrl_type):
        if ctrl_type in interesting:
            try:
                loopback.restore_local()
            except Exception:
                pass
        return False        # 返回 False：不拦截，让系统继续默认退出流程

    cb = HANDLER(_handler)
    try:
        if not kernel32.SetConsoleCtrlHandler(cb, True):
            return None
    except Exception:
        return None
    return cb


class AudioFeeder(threading.Thread):
    """把 WASAPI 环回 PCM 持续写入 ffmpeg 的 stdin。

    需要补静音的原因
    ----------------
    实测确认：当系统完全静音、没有任何程序在播放声音时，WASAPI loopback
    不产出任何数据包（实测 3 秒内 0 帧）。若不补数据，ffmpeg 会一直阻塞
    等待音频输入，连视频也一起卡死——表现为"编码已输出 0.0 KB"、电视端黑屏。

    ★ 时间轴守恒（本项目最关键的一处修复）
    ------------------------------------
    补静音必须**按"欠账"补，而不是按"间隔"补**。

    真实 WASAPI 环回不是"每次 read 都吐一点"，而是**周期性批量投递**
    （典型 100~200 ms 一整批）。旧实现记录"距上次供数的间隔"，一旦超过
    100 ms 就按该间隔补静音 —— 但那段间隔里的数据并没有丢，只是**迟到**
    了。补完静音后迟到的数据照样到达，两者重复计数。

    实测数据（MockLB 精确复现批量投递）：
        投递周期  50 ms -> 音频时间轴系数 0.62（欠发）
        投递周期 100 ms -> 系数 0.82（欠发）
        投递周期 150 ms -> 系数 1.67（超发 67%）
        投递周期 200 ms -> 系数 1.58（超发 58%）

    音频时间轴一旦以 >1 倍速推进，流的内容时长增长就快于真实时间，
    播放器端缓冲必然随播放时长**持续累积** —— 这就是"播放一会后延迟
    越来越大"的根因之一。

    现改为「墙钟驱动」：维护输入缓冲，每轮只写出**自启动以来真实经过时间
    所对应的字节数** ——
      · 数据早到 → 多出的留在缓冲里慢慢放，绝不提前写出去
      · 数据迟到 → 差额先补静音，数据到了再从缓冲里放
      · 数据丢失 → 差额补静音
    三者收敛到同一个恒等式：已写入字节 / 字节率 == 真实经过时间。
    音频 PTS 因此永远与真实时间对齐，内容时长不可能超发。

    （注意：单纯改成"按欠账补"仍然不够 —— 它无法区分"数据丢了"和
      "数据只是迟到"，实测在 160 ms 投递周期下依然跑到 1.5 倍速。
      必须由墙钟统一驱动写入量才能真正守恒。）
    """

    READ_TIMEOUT = 0.02         # 环回读取轮询步长（秒）
    MAX_BUFFER_S = 2.0          # 输入缓冲上限（秒），防设备时钟反常时无限积压

    def __init__(self, loopback, sink):
        threading.Thread.__init__(self, daemon=True)
        self.lb = loopback
        self.sink = sink
        self.stop_flag = False
        self.written = 0
        self.silence_bytes = 0
        self.error = None
        self._frame = max(1, loopback.channels * (loopback.bits // 8))
        self._bps = max(1, loopback.rate * self._frame)
        self.buf = bytearray()      # 环回数据缓冲
        self.fed_bytes = 0          # 已写入 ffmpeg 的字节数（严格按帧对齐）
        self.started_at = None

    def _write(self, data):
        """raw 管道下 write 可能部分写入，必须循环写完。"""
        view = memoryview(data)
        while len(view) and not self.stop_flag:
            n = self.sink.write(view)
            if not n:
                break
            view = view[n:]
        self.written += len(data)

    def run(self):
        """墙钟驱动：已写入字节数严格跟随真实经过时间。

        每轮只写出 target = 真实经过时间 × 字节率 的差额，与"数据到没到"
        无关。早到的数据留在 buf 里排队，迟到的差额用静音补齐，
        因此写入总量恒定 = 真实时间，音频时间轴不可能超发。
        """
        self.started_at = time.time()
        max_buf = int(self._bps * self.MAX_BUFFER_S)
        try:
            while not self.stop_flag:
                data = self.lb.read(timeout=self.READ_TIMEOUT)
                if data:
                    self.buf.extend(data)
                    if len(self.buf) > max_buf:
                        # 正常不会发生（环回产出速率 == 实时）。真发生了说明
                        # 设备时钟反常：丢最旧的，宁可轻微跳音也不无限积压。
                        del self.buf[:len(self.buf) - max_buf]

                target = int((time.time() - self.started_at) * self._bps)
                need = target - self.fed_bytes
                if need < self._frame:
                    continue
                # 按采样帧对齐，保证左右声道不会在写入边界上错位
                need -= need % self._frame

                have = len(self.buf)
                if have >= need:
                    self._write(bytes(self.buf[:need]))
                    del self.buf[:need]
                else:
                    if have:
                        self._write(bytes(self.buf[:have]))
                        del self.buf[:have]
                    pad = need - have
                    if pad > 0:
                        self._write(b"\x00" * pad)
                        self.silence_bytes += pad
                self.fed_bytes += need
        except Exception as e:
            self.error = e
        finally:
            try:
                self.sink.flush()
            except Exception:
                pass

    def timeline_drift(self):
        """音频时间轴与真实时间的偏差（秒）。正常应恒在 ±0.02 内。"""
        if self.started_at is None:
            return None
        return ((time.time() - self.started_at)
                - self.fed_bytes / float(self._bps))

    def stop(self):
        self.stop_flag = True


def remove_osd_temp_files():
    """清理 OSD 运行时产生的临时文件。"""
    for p in (OSD_TEXT_FILE, OSD_PROGRESS_FILE, OSD_TEXT_FILE + ".tmp"):
        try:
            if os.path.isfile(p):
                os.remove(p)
        except Exception:
            pass


# ---------------------------------------------------------------- 流服务


class MediaBroadcaster(object):
    """从 ffmpeg stdout 读取 MPEG-TS，用环形缓冲供 HTTP 客户端拉取。

    低延迟 + 不花屏的设计要点：
      1. 无客户端时不往缓冲里攒数据（直接丢弃）—— 实时流的历史数据
         对直播毫无价值，留着只会变成"新客户端先补播几秒旧画面"。
      2. 缓冲水位按「目标毫秒」用实测码率动态换算，而不是写死字节数。
         写死字节数时，一旦码率上升，同样的字节就代表更长的延迟。
      3. ★ 积压超水位时，丢弃位置一律对齐到**下一个 IDR 关键帧的起始
         TS 包**。这是"花屏"的唯一成因所在：若只对齐到 188 字节包边界
         而落在 GOP 中间，客户端收到的第一个包是 P 帧，它引用的参考帧
         从未送达，解码器会一直花屏/绿屏到下一个关键帧才恢复；若反复
         丢在中部，就表现为持续花屏、永不恢复（典型日志：
         "non-existing PPS 0 referenced"）。
      4. 缓冲起点始终保持 TS 包边界 —— 只按 188 的整数倍删除。以前
         read() 按 65536 字节切，65536 % 188 = 112，起点会永久错位，
         使所有边界对齐计算失效。
    """

    TS_PACKET = 188
    _IDR_SCAN_PKTS = 4000       # 找 IDR 时最多向后扫多少个包（≈750 KB）
    RATE_WINDOW_S = 3.0         # 实时码率估算窗口（秒）

    def __init__(self, proc, max_buffer=384 * 1024, target_ms=300):
        self.proc = proc
        self.max_buffer = max_buffer      # 绝对上限（字节），兜底
        self.target_ms = target_ms        # 目标本机缓冲时长（毫秒）
        self.buf = bytearray()
        self.cv = threading.Condition()
        self.running = True
        self.total_bytes = 0
        self.dropped_bytes = 0
        self.started_at = time.time()
        self.clients = 0
        self.last_trim = 0.0
        self.realigned = 0
        self._video_pid = None
        self._rate_marks = []           # (时刻, 累计字节)，用于实时码率估算
        self._t = threading.Thread(target=self._pump, daemon=True)
        self._t.start()

    # ------------------------------------------------ 水位与裁剪

    def _keep_bytes(self):
        """当前应保留的缓冲字节数 = 实测码率 × 目标时延。

        按时间而不是按固定字节算，才能在码率变化时保持延迟恒定
        （2.5 Mbps 下的 384 KB 是 1.2 秒，28 Mbps 下的 384 KB 只有 0.11 秒）。
        """
        kbps = self.kbps
        if kbps <= 1:
            return self.max_buffer
        b = int(kbps * 1000.0 / 8.0 * (self.target_ms / 1000.0))
        return max(self.TS_PACKET * 8, b)

    def _pkt_pid(self, idx):
        buf = self.buf
        return ((buf[idx + 1] & 0x1F) << 8) | buf[idx + 2]

    def _is_pes_start(self, idx):
        return bool(self.buf[idx + 1] & 0x40)

    def _find_next_idr(self, pos):
        """从 pos（须是 TS 包边界）找最近的 IDR 起始包，找不到返回 -1。

        IDR slice 的 NAL 头固定为 0x65（nal_ref_idc=3, nal_unit_type=5），
        前面是 Annex-B 起始码。用 bytearray.find 做字节级预筛（C 实现，
        很快），再换算回 TS 包边界，并校验：
          · 该包是 PES 起始包（payload_unit_start_indicator = 1）
          · PID 与首次识别到的视频 PID 一致
        这两条把"把音频数据误判为 IDR"的概率压到可忽略。
        """
        buf = self.buf
        n = len(buf)
        limit = min(pos + self._IDR_SCAN_PKTS * self.TS_PACKET, n)
        if limit - 4 <= pos:
            return -1
        p = pos
        while p < limit - 4:
            k = buf.find(b"\x00\x00\x01\x65", p, limit)
            if k < 0:
                return -1
            base = k - (k % self.TS_PACKET)
            if base >= pos and base + 3 < n and buf[base] == 0x47:
                if self._is_pes_start(base):
                    pid = self._pkt_pid(base)
                    if self._video_pid is None:
                        self._video_pid = pid
                        return base
                    if pid == self._video_pid:
                        return base
            p = k + 4
        return -1

    def _resync_locked(self):
        """确保缓冲起点落在 TS 包边界上（buf[0] 与 buf[188] 都是 0x47）。

        ★ 为什么必须有这一步：缓冲起点会因两种常见操作而错位 ——
          · 无客户端期间数据被整块丢弃，恢复时下一次 append 从流的
            任意字节位置开始（ffmpeg 一次吐 65536 字节，65536 % 188 = 112）；
          · clear() 清空时的物理偏移。
        而 IDR 检测、追帧裁剪全部依赖"缓冲起点 = 包边界"这一前提。
        以前没有这一步，起点一旦错位，_find_next_idr 就永远找不到关键帧，
        追帧退化为简单的包边界截断 —— 客户端拿到孤立 P 帧，于是花屏。

        用"相隔 188 字节的两个 0x47"做严格校验，避免把 payload 中
        偶然出现的 0x47 当成同步字节。正常情况下此函数是 O(1) 直接返回。
        """
        buf = self.buf
        n = len(buf)
        if n < self.TS_PACKET * 2:
            return
        if buf[0] == 0x47 and buf[self.TS_PACKET] == 0x47:
            return
        limit = max(1, min(n - self.TS_PACKET, self.TS_PACKET * 4))
        i = buf.find(0x47, 0, limit)
        while i >= 0:
            if buf[i + self.TS_PACKET] == 0x47:
                break
            i = buf.find(0x47, i + 1, limit)
        if i <= 0:
            return
        del buf[:i]
        self.dropped_bytes += i
        self.realigned += 1

    def _find_prev_idr(self, pos):
        """从 pos（含）向前找最近的关键帧起始包边界，找不到返回 -1。

        ★ 为什么必须同时有"向前找"：裁剪目标点 cut 到缓冲末尾的距离就是
        保留水位（低延迟模式下只有约 95 KB），而一个 GOP 可能有 130 KB ——
        也就是说目标点之后完全可能一个关键帧都没有。此时若退化成
        "丢弃到 cut 这个 188 包边界"，客户端拿到的第一个包就是 PES 续包
        （PUSI=0），解码器缺参考帧 → 花屏。这正是之前实测到的现象：
        发送首块头部 47 01 00 11（PUSI=0）而不是 47 41 00 3d（PUSI=1）。
        """
        buf = self.buf
        n = len(buf)
        low = max(0, pos - self._IDR_SCAN_PKTS * self.TS_PACKET)
        p = min(pos + self.TS_PACKET, n)
        while p > low + 4:
            k = buf.rfind(b"\x00\x00\x01\x65", low, p)
            if k < 0:
                return -1
            base = k - (k % self.TS_PACKET)
            if (base <= pos and base + 3 < n and buf[base] == 0x47
                    and self._is_pes_start(base)):
                pid = self._pkt_pid(base)
                if self._video_pid is None or pid == self._video_pid:
                    self._video_pid = pid
                    return base
            p = k
        return -1

    def _trim_locked(self, keep_bytes):
        """把缓冲裁到最多 keep_bytes，起点对齐到 IDR。须已持有 self.cv。"""
        n = len(self.buf)
        if n <= keep_bytes or n < self.TS_PACKET * 4:
            return 0
        cut = (n - keep_bytes) // self.TS_PACKET * self.TS_PACKET
        if cut <= 0:
            return 0
        idr = self._find_next_idr(cut)
        if idr < 0:
            # 目标点之后再无关键帧（保留水位短于一个 GOP，很常见）：
            # 改为向前找。代价是这次保留的数据略多于水位（最多多一个
            # GOP，约 250 ms），换来客户端起点一定是可解码的关键帧。
            idr = self._find_prev_idr(cut)
        if idr <= 0 or idr >= n:
            # 前后都没有可用关键帧：这次不裁。宁可延迟暂时高一点，
            # 也绝不把缓冲腰斩在 GOP 中间。
            return 0
        del self.buf[:idr]
        self.dropped_bytes += idr
        self.last_trim = time.time()
        return idr

    def skip_to_live(self):
        """把积压一次性丢到目标水位（对齐 IDR）。对端消费过慢时调用。"""
        with self.cv:
            return self._trim_locked(self._keep_bytes())

    # ------------------------------------------------ 采集

    def _pump(self):
        out = self.proc.stdout
        while self.running:
            try:
                chunk = out.read(65536)
            except Exception:
                break
            if not chunk:
                break
            with self.cv:
                self.total_bytes += len(chunk)
                # 记录编码产出的速率轨迹，供发送节流取基准（见 rate_bps）
                now = time.time()
                self._rate_marks.append((now, self.total_bytes))
                cutoff = now - self.RATE_WINDOW_S
                while len(self._rate_marks) > 2 and self._rate_marks[0][0] < cutoff:
                    self._rate_marks.pop(0)
                # 无观众时不缓冲：实时流的历史数据对直播毫无价值
                if self.clients > 0:
                    self.buf.extend(chunk)
                    # 起点可能在"无客户端丢弃期"之后错位，先纠正再谈裁剪
                    self._resync_locked()
                    keep = self._keep_bytes()
                    # max_buffer 是"至少攒这么多才做一次裁剪"的触发阈值，
                    # 避免每个 64 KB 都去扫一次 IDR（白费 CPU）
                    if len(self.buf) > max(self.max_buffer, keep * 3):
                        self._trim_locked(keep)
                self.cv.notify_all()
        with self.cv:
            self.running = False
            self.cv.notify_all()

    def rate_bps(self):
        """编码器的实时产出速率（字节/秒），滑动窗口 ≈ RATE_WINDOW_S。

        发送节流必须用**编码产出速率**当基准，绝不能用"本连接的发送速率"：
        后者一旦被节流压低，基准也跟着降低，闸门会把自己越限越死（正反馈）。
        产出速率只取决于 ffmpeg，不受节流影响，因此是唯一稳定的基准。
        """
        with self.cv:
            marks = self._rate_marks
            if len(marks) < 2:
                return 0.0
            dt = marks[-1][0] - marks[0][0]
            if dt <= 0.25:
                return 0.0
            return (marks[-1][1] - marks[0][1]) / dt

    def align_to_idr(self):
        """丢弃缓冲头部直到起点是 IDR 起始包。

        返回 >0：实际丢弃的字节数（已对齐）
        返回 -1：起点本来就是 IDR，无需处理
        返回  0：数据还太少，等下一轮
        用于"客户端首次接入"——此时必须从关键帧开始送，否则解码器
        拿到孤立 P 帧就会花屏到下一个关键帧。
        """
        with self.cv:
            self._resync_locked()
            n = len(self.buf)
            if n < self.TS_PACKET * 4:
                return 0
            first = self._find_next_idr(0)
            if first == 0:
                return -1
            if first < 0:
                return 0
            del self.buf[:first]
            self.dropped_bytes += first
            self.last_trim = time.time()
            return first

    def read(self, n=65536, timeout=2.0):
        with self.cv:
            if len(self.buf) < self.TS_PACKET and self.running:
                self.cv.wait(timeout)
            if not self.buf:
                return b"" if self.running else None
            self._resync_locked()       # 起点必须在包边界，下面才敢谈对齐
            # 追帧：把积压压到目标水位，丢弃点对齐 IDR（不花屏的关键）
            keep = self._keep_bytes()
            if len(self.buf) > keep:
                self._trim_locked(keep)
            avail = len(self.buf)
            # ★ 只取 188 的整数倍，保证缓冲起点永远是 TS 包边界。
            #   以前按 65536 取，65536 % 188 = 112，起点会永久错位。
            take = n - (n % self.TS_PACKET)
            if take <= 0:
                take = self.TS_PACKET
            if take > avail:
                take = avail - (avail % self.TS_PACKET)
            if take <= 0:
                return b""
            data = bytes(self.buf[:take])
            del self.buf[:take]
            return data

    def clear(self):
        """丢弃积压（客户端首次接入/全部断开时调用）。

        ★ 只能按 188 的整数倍删除。若直接 del self.buf[:] 全清，
        剩余零点几个包会让缓冲起点永久错位，之后所有 IDR 检测与
        IDR 对齐都会失效 —— 这正是"花屏"曾经存在的深层原因。
        留下的不足 188 字节的零头会在后续 _resync_locked /
        align_to_idr 中被一并丢掉。
        """
        with self.cv:
            n = len(self.buf)
            if n < self.TS_PACKET:
                return 0
            drop = n - (n % self.TS_PACKET)
            if drop <= 0:
                return 0
            del self.buf[:drop]
            self.dropped_bytes += drop
            return drop

    @property
    def backlog_ms(self):
        """当前积压对应的时长（按实测码率估算）。"""
        kbps = self.kbps
        if kbps <= 1:
            return 0.0
        return len(self.buf) * 8 / kbps

    @property
    def kbps(self):
        dt = max(0.001, time.time() - self.started_at)
        return self.total_bytes * 8 / dt / 1000.0

    def stop(self):
        self.running = False
        with self.cv:
            self.cv.notify_all()


BROADCASTER = None          # 全局：MP2T 模式的流
_STATIC_CACHE = {}          # 静态资源内存缓存：名称 -> 字节串
HLS_DIR = None              # 全局：HLS 模式的分片目录
HTTP_PORT = DEFAULT_HTTP_PORT

# 全局投送状态：给"电视是否真的在消费"判断使用
BC_STATE = {
    "clients": 0,           # 当前拉流客户端数
    "delivered": 0,         # 累计成功写出的字节数
    "last_deliver": 0.0,    # 最近一次成功写出的时间戳
    "slow_events": 0,       # 对端写阻塞（只缓冲不消化）次数
    "paced_wait_ms": 0.0,   # 累计被发送节流闸门拦下的毫秒数
}

# 发送节流：允许服务端超前实时多少毫秒（0 = 关闭节流）。
#
# 为什么必须节流
# --------------
# 电视自带 DLNA 播放器**不会主动丢弃缓冲**：服务端灌得多快，它就一路攒多深。
# 而服务端原本是全速把编码产出写进 socket，靠 TCP 管道当"无限缓冲"，
# 因此客户端缓冲深度随播放时长线性增长 —— 直到整条管道被填满（几 MB，
# 折合数秒）时才触发追帧。而那时延迟已经涨到几秒且降不下来：丢弃只能
# 影响"还没发出去的"数据，清不掉电视已经收下的部分。
#
# 加了这个闸门后，发送节奏被锚定在编码器的实时产出速率上，客户端缓冲
# 深度恒定在 lead 附近，延迟不再随时间累积。
#
# 取值权衡
#   · 太小（<300ms）：抗抖动能力差，网络波动易 underrun 卡顿
#   · 太大（>2000ms）：白白抬高固定延迟
#   800 ms 是"够电视起播 + 抗抖动 + 不累积"的平衡点，可用 --lead-ms 调整。
PACE_LEAD_MS = 800

# ---------------------------------------------------------------- 画面 OSD
# 把 FPS / 码率 / 端到端延迟(ms) 实时烧进画面右上角，供电视端直接观察。
#
# 为什么必须烧进画面（而不是做网页叠加）：
#   电视端播放的是电视**自带的 DLNA 播放器**，它不会渲染我们的网页；
#   只有把文字烧进视频流，才能出现在电视屏幕上。
#
# 字体路径必须写成「双反斜杠 + 冒号」：ffmpeg filter 里 ':' 是参数分隔符，
# 而 filtergraph 层与 filter option 层会各消费一次转义。实测单反斜杠
# （C\:/...）会被当成参数分隔符，报 "No option name near ..."；
# 必须 C\\:/... 才能正确解析。见 esc_filter_path()。
OSD_TEXT_FILE = os.path.join(RUNTIME_DIR, ".osd.txt")
OSD_PROGRESS_FILE = os.path.join(RUNTIME_DIR, ".osd_progress.txt")

# OSD 运行时配置（main 按参数与输出分辨率填充）
OSD_ENABLED = True          # --no-osd 可关闭
OSD_FONT = None             # 运行时探测；为空即不启用
OSD_SIZE = 18               # 字号（像素）
OSD_MARGIN = 16             # 距画面右上角边距（像素）
OSD_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",        # 微软雅黑（含中文，字形清晰）
    "C:/Windows/Fonts/simhei.ttf",      # 黑体
    "C:/Windows/Fonts/consola.ttf",     # Consolas（纯 ASCII，等宽）
    "C:/Windows/Fonts/arial.ttf",
]


def esc_filter_path(path):
    """把本地路径转义成可放进 ffmpeg filter 参数里的形式。

    ★ 冒号必须写成**两个**反斜杠加冒号（C\\\\:/...）：
      实测单反斜杠（C\\:/...）会被当成参数分隔符，ffmpeg 直接报
      "No option name near ..."，因为 filtergraph 层与 filter option 层
      会各消费一次转义。
    反斜杠统一成 '/'；逗号 / 方括号 / 单引号这些 filtergraph 元字符
    一并转义，避免程序被放在含这些字符的路径下时滤镜解析失败。
    """
    p = path.replace("\\", "/")
    out = []
    for ch in p:
        out.append("\\\\" + ch if ch in ":,[]'" else ch)
    return "".join(out)


def find_osd_font():
    """挑一个可用字体；一个都没有则返回 None，调用方据此关闭 OSD。"""
    for p in OSD_FONT_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


def build_osd_filter(text_path, font_path, size, margin):
    """构造 OSD 的 drawtext 滤镜串：右上角、小字、白字带黑描边。

    设计取舍（"不能影响画面"）：
      · 位置贴右上角，避开绝大多数画面的主体与字幕区
      · 字号取画面高度的 ~1.8%，在电视上看得清但不抢眼
      · 纯白字 + 1px 黑描边(borderw) + 1px 黑色投影。
        早前用灰白字 (0xE4E4E4) + 仅投影，实测在浅色画面上（白底网页、
        文档、Light 主题 IDE）对比度严重不足，字几乎糊进背景 —— 而桌面
        投屏的内容绝大多数是浅色的，等于该功能在真实场景下失效。
        描边只占文字边缘 1px、不增加任何占用面积，却能让白字在任意
        明暗背景上保持清晰，因此取代"灰白 + 阴影"方案。
      · borderw 随字号缩放：13px 用 1px、28px 以上用 2px，
        否则大字号下描边细得失去作用。
      · reload=1 每帧重读文本，实现实时数值。
    """
    border = max(1, int(round(size / 13.0)))
    return (
        "drawtext=fontfile=%s:textfile=%s:reload=1"
        ":fontsize=%d:fontcolor=0xFFFFFF@1.0"
        ":borderw=%d:bordercolor=0x000000@0.85"
        ":shadowcolor=0x000000@0.80:shadowx=1:shadowy=1"
        ":x=w-tw-%d:y=%d"
        % (esc_filter_path(font_path), esc_filter_path(text_path),
           size, border, margin, margin)
    )


def parse_clock_time(text):
    """把 'HH:MM:SS.mmm' 或 'HH:MM:SS' 解析成秒；失败返回 None。"""
    if not text:
        return None
    m = re.match(r"^\s*(\d+):(\d{1,2}):(\d{1,2}(?:\.\d+)?)\s*$", text)
    if not m:
        return None
    return (int(m.group(1)) * 3600 + int(m.group(2)) * 60
            + float(m.group(3)))


def get_tv_position(dev, timeout=3.0):
    """查询 DLNA 渲染器当前播放位置（秒）；不可用返回 None。

    ★ 这是服务端唯一能拿到「电视到底播到第几秒」的途径。
    电视自带播放器缓冲了多久，只有它自己知道；而 GetPositionInfo 的
    RelTime 正是它的播放进度。用它和「已推送的内容时长」作差，就是
    端到端延迟（画面已产生但还没播出来的那一部分）。
    注意：绝大多数电视的 RelTime 精度只到 1 秒，调用方负责插值平滑。
    """
    if dev is None:
        return None
    avt = getattr(dev, "services", {}).get("AVTransport")
    if not avt:
        return None
    stype, ctrl = avt
    try:
        xml = soap_call(ctrl, stype, "GetPositionInfo",
                        "<InstanceID>0</InstanceID>", timeout=timeout)
    except Exception:
        return None
    m = re.search(r"<RelTime>([^<]*)</RelTime>", xml)
    if not m:
        return None
    return parse_clock_time(m.group(1))


# ------------------------------------------------------------ OSD 文本写入
# ★ 铁律：drawtext(reload=1) 每一帧都会对 textfile 重新建立文件映射
#   （av_file_map -> CreateFile -> CreateFileMapping）。由此推出两条
#   一旦违反就必崩的约束：
#
#   ① 文本文件**永远不能是 0 字节**。
#      映射 0 字节文件必然失败，drawtext 于是抛出
#      "Error occurred in CreateFileMapping()" /
#      "The text file ... could not be read or is empty"，
#      滤镜报错 -> 整条 ffmpeg 流水线立即终止（不是重试，是退出）。
#
#   ② 因此 **绝不能** 用 open(path, "w") 覆盖（它先把文件截断到 0），
#      也 **绝不能** 靠 os.replace 做更新 —— 改名本身是原子的，但它一旦
#      因目标文件被 ffmpeg 占用而失败（实测 112 次里失败 30 次，
#      报 WinError 5），退路几乎必然落到截断写上，于是踩中 ①。
#      4 Hz 的更新频率下，几分钟内必然命中，表现为"投屏一会儿
#      ffmpeg 就自己退出"。
#
#   正确做法 —— 定长记录 + 只就地覆盖：
#     · 长度恒定：不足补空格、超出截断，文件尺寸永不变化、永不为空
#     · 只覆盖不截断、不改名：os.open(..., O_CREAT) 不带 O_TRUNC
#       （O_CREAT 对已存在的文件不会清空内容）
#     · 打开失败（被占用）就放弃这一拍：宁可数字晚刷新 0.25 秒，
#       也绝不退化成截断写
#
#   实测（_t_fix.py）：定长原地写在 100 Hz 极端压力下连续 4007 次
#   零失败、ffmpeg 稳定跑满；而 os.replace + 截断退路的实现 2.2 秒即崩。
OSD_RECORD_BYTES = 56


def osd_record_bytes(text, width=OSD_RECORD_BYTES):
    """把一行 OSD 文本编码成"定长记录"（帧长恒定，永不出现 0 字节）。

    ★ 补齐的空格放在**左侧**，不是右侧 —— 这一点直接决定观感：

      drawtext 用 x = w - tw - margin 定位，而 tw 会把补齐空格一起算进去。
        · 补在右侧：可见文字被整体向左推离右边距；而且文字长短一变，
          补的空格数跟着变，位置就会左右乱跳。
        · 补在左侧：字符串右端恒在 (w - margin)，**可见文字永远贴着
          右边距右对齐**，长度变化只影响左端，视觉上完全稳定。
    """
    raw = text.encode("utf-8")
    if len(raw) > width:
        raw = raw[:width]
        # 不要把多字节字符截成半截：残缺的 UTF-8 会让 drawtext 解码失败
        while raw and (raw[-1] & 0xC0) == 0x80:
            raw = raw[:-1]
        if raw and raw[-1] >= 0xC0:
            raw = raw[:-1]
    return b" " * (width - len(raw)) + raw


def osd_write_record(text):
    """以定长、就地覆盖的方式写入 OSD 文本，返回是否成功。

    只会在"打开文件被占用"这类瞬时错误上失败，且失败是无害的：
    文件保留上一拍的内容，ffmpeg 仍能正常映射，不会中断投屏。
    """
    data = osd_record_bytes(text)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(OSD_TEXT_FILE, flags)
    except OSError:
        return False
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, data)
        return True
    except OSError:
        return False
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def osd_reset_file(text="fps --   Mbps --   延迟 --"):
    """在 ffmpeg 启动**之前**把 OSD 文本文件规整为标准的定长文件。

    必须覆盖三种脏状态，否则会带到画面上：
      · 文件不存在
      · 文件比 OSD_RECORD_BYTES 长（旧版本留下的、或曾被外部改过）
        —— 我们只就地覆盖前 56 字节、不截断，多出来的尾巴会继续被
        drawtext 渲染成乱码；若尾巴里含换行符，整段文字会全部消失
      · 文件长度不对导致后续定位异常

    这一步发生在 ffmpeg 起来之前，此时"截断重建"是安全的 —— 运行时
    绝不能这么做（见 osd_write_record 的说明）。
    """
    try:
        with open(OSD_TEXT_FILE, "wb") as f:
            f.write(osd_record_bytes(text))
        return True
    except OSError:
        return False


class OsdUpdater(threading.Thread):
    """把实时指标写成一行文本，供 ffmpeg drawtext 逐帧重读（reload=1）。

    指标来源
    --------
    · FPS / 码率 / 已产出内容时长 —— 解析 ffmpeg 的 -progress 输出。
      用 ffmpeg 自报的数据，比在 Python 侧另起一套统计更准（它知道真实
      编码帧数与输出字节数）。
    · 端到端延迟(ms) —— (已推送内容时长 - 电视播放位置)。

    延迟为什么这么算
    ----------------
    服务端知道「我产出并推了多少秒内容」（out_time 减去客户端接入瞬间的
    基准），电视知道自己「播到第几秒」（DLNA RelTime）。两者之差，就是
    已经产生但还没在电视上播出来的那一段 —— 这正是用户感知到的延迟，
    也正好等于「本机缓冲 + 网络在途 + 电视端缓冲」。

    查询失败（--no-push 模式、电视不响应 GetPositionInfo）时显示 `--`，
    不伪造数字。
    """

    POS_INTERVAL = 1.0          # 查询电视播放位置的间隔（秒）
    PROGRESS_TAIL = 4096        # 只读 -progress 文件尾部这么多字节（见 _read_progress）
    REL_EXTRAP_MAX = 1.5        # RelTime 外推上限（秒），防电视暂停后无限外推
    LAT_EMA = 0.65              # 延迟平滑权重（越大越平滑、越迟钝）

    def __init__(self, bc, dev=None, interval=0.25):
        threading.Thread.__init__(self, daemon=True)
        self._fd = None             # OSD 文本文件的持久写句柄（见 _write_text）
        self.bc = bc
        self.dev = dev
        self.interval = interval
        self.stop_flag = False
        self.base_out_s = None      # 客户端接入瞬间 ffmpeg 已产出的内容时长
        self._rel_raw = None        # 最近一次查到的原始 RelTime（秒）
        self._rel_prev = None
        self._rel_prev_at = 0.0
        self._rel_next_at = 0.0
        self._lat_ema = None
        self.last_text = ""
        self.last_fps = None
        self.last_kbps = None
        self.last_lat_ms = None

    # ---------------- ffmpeg -progress ----------------
    def _read_progress(self):
        """返回 (fps, kbps, out_seconds)，任一项未就绪则为 None。

        ★ 只读文件**尾部**，绝不做整文件读取。

        ffmpeg 的 -progress 是**追加**写的（不截断），长时投屏下这个文件会
        一直膨胀：实测约 550 B/s ≈ 33 KB/min ≈ 2 MB/小时。而本函数每 0.25 s
        被调用一次，若整文件读，开销会随播放时长**线性增长** ——
        8 小时后单次就是十几 MB，相当于每秒几十 MB 的无意义 IO 加上
        大字符串切分，白白烧 CPU 与磁盘缓存。

        尾部 4 KB 已覆盖最近十几次更新，fps / bitrate / out_time 都能拿到
        最新值。截断点若落在某行中间，那一行不会匹配任何前缀，直接忽略即可。
        """
        try:
            with open(OSD_PROGRESS_FILE, "rb") as f:
                f.seek(0, os.SEEK_END)
                start = max(0, f.tell() - self.PROGRESS_TAIL)
                f.seek(start)
                txt = f.read().decode("utf-8", "replace")
        except Exception:
            return None, None, None
        fps = kbps = out_s = None
        for line in txt.splitlines():
            if line.startswith("fps="):
                try:
                    fps = float(line[4:].strip())
                except Exception:
                    pass
            elif line.startswith("bitrate="):
                v = line[8:].strip()
                if v.endswith("kbits/s"):
                    try:
                        kbps = float(v[:-7])
                    except Exception:
                        pass
            elif line.startswith("out_time_us="):
                try:
                    out_s = float(line[12:].strip()) / 1e6
                except Exception:
                    pass
        return fps, kbps, out_s

    # ---------------- RelTime 插值 ----------------
    def _reltime_continuous(self, rel, now):
        """把秒级 RelTime 插值成连续值，恢复毫秒级分辨率。

        RelTime 通常每秒跳一次，直接用会得到 ±1 秒的锯齿。这里记住每次
        跳变的墙钟时刻，其后按 1 倍速外推。外推封顶 REL_EXTRAP_MAX 秒，
        这样电视一旦暂停/卡死，不会无限外推造成"延迟看起来很小"的假象。
        """
        if rel is None:
            self._rel_prev = None
            return None
        if self._rel_prev is None or abs(rel - self._rel_prev) > 1e-6:
            self._rel_prev = rel
            self._rel_prev_at = now
        return rel + min(max(now - self._rel_prev_at, 0.0), self.REL_EXTRAP_MAX)

    # ---------------- 每轮更新 ----------------
    def _tick(self):
        now = time.time()
        fps, kbps, out_s = self._read_progress()

        if self.bc is not None and self.bc.clients <= 0:
            # 没有观众：清空基准与平滑值，下次接入重新起算
            self.base_out_s = None
            self._rel_prev = None
            self._rel_raw = None
            self._rel_next_at = 0.0
            self._lat_ema = None
        elif out_s is not None and self.base_out_s is None:
            self.base_out_s = out_s

        lat_ms = None
        if (out_s is not None and self.base_out_s is not None
                and self.bc is not None and self.bc.clients > 0):
            content_s = out_s - self.base_out_s
            if now >= self._rel_next_at:
                self._rel_raw = get_tv_position(self.dev)
                self._rel_next_at = now + self.POS_INTERVAL
            rel_c = self._reltime_continuous(self._rel_raw, now)
            if rel_c is not None:
                raw = (content_s - rel_c) * 1000.0
                if raw < 0.0:
                    raw = 0.0
                self._lat_ema = (raw if self._lat_ema is None else
                                 self._lat_ema * self.LAT_EMA
                                 + raw * (1.0 - self.LAT_EMA))
                lat_ms = self._lat_ema

        self.last_fps = fps
        self.last_kbps = kbps
        self.last_lat_ms = lat_ms

        parts = []
        parts.append("%.1ffps" % fps if fps else "fps --")
        parts.append("%.1fMbps" % (kbps / 1000.0) if kbps else "Mbps --")
        parts.append(("%dms" % int(round(lat_ms))) if lat_ms is not None
                     else "延迟 --")
        text = "   ".join(parts)
        if text != self.last_text:
            self._write_text(text)
            self.last_text = text

    def _write_text(self, text):
        """更新 OSD 文本文件：定长、就地覆盖、永不截断。

        两个必须遵守的约束：

        1) ★ 文件**永远不能变成 0 字节**，也不能改名替换。
           open(path, "w") 会先把文件截断到 0；os.replace 本身是原子的，
           但它一旦因目标被 ffmpeg 占用而失败（实测 112 次中失败 30 次，
           报 WinError 5），退路往往就落到截断写上。两者都会让 ffmpeg 的
           CreateFileMapping() 失败 -> drawtext 报错 -> 整条流水线终止，
           表现为"投屏一会儿 ffmpeg 就莫名退出"。
           因此改为定长记录 + 就地覆盖，细节见 osd_write_record()。

        2) ★ 文本末尾**绝对不能带换行符**。
           实测（_t_d10.py 四组对照）：textfile 里只要含 \\n 或 \\r\\n，
           drawtext 就整段渲染不出来 —— 画面上一个像素都没有，而且
           ffmpeg 不报任何错误、不退出、码率也看不出异常。
           更隐蔽的是 Windows 文本模式会把 '\\n' 自动转成 '\\r\\n'，
           所以"只加一个 \\n"在 Windows 上同样会触发。
           这正是"OSD 数值一直在更新、画面却始终看不到字"的根因。
           因此这里用 newline="" 关掉换行转换，并且不追加任何换行。
        """
        data = osd_record_bytes(text)
        # 持久句柄：只在第一次写入时打开，之后一直持有。
        #
        # 为什么不能每次开关：drawtext 每帧都要对该文件做一次
        # CreateFile。只要它的打开动作撞进我们的 open/close 窗口，就会
        # 因共享冲突报错 -> 滤镜失败 -> ffmpeg 退出。句柄长期持有后，
        # 文件对 ffmpeg 而言状态恒定，这个竞争窗口被彻底消除（实测
        # 400 次更新 / 约 1200 次 ffmpeg 重映射，零异常）。ffmpeg
        # 自己以共享读写方式打开该文件，因此我们持有写句柄不会挡住它。
        if self._fd is None:
            self._fd = self._open_fd()
        if self._fd is not None:
            try:
                os.lseek(self._fd, 0, os.SEEK_SET)
                os.write(self._fd, data)
                return
            except OSError:
                # 句柄失效（极少见）：关掉，本轮改走无句柄退路，下轮重开
                self._close_fd()
        osd_write_record(text)

    def _open_fd(self):
        """打开 OSD 文本文件的持久写句柄，失败返回 None（不抛异常）。"""
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
        try:
            return os.open(OSD_TEXT_FILE, flags)
        except OSError:
            return None

    def _close_fd(self):
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    def run(self):
        try:
            while not self.stop_flag:
                try:
                    self._tick()
                except Exception:
                    pass
                time.sleep(self.interval)
        finally:
            # 必须先关句柄再让主流程删文件：Windows 上打开着的文件删不掉
            self._close_fd()

    def stop(self):
        self.stop_flag = True
        if threading.current_thread() is not self:
            # 等它真正退出，保证句柄已释放，随后的临时文件清理才有效
            try:
                self.join(timeout=1.5)
            except Exception:
                pass

# ---------------------------------------------------------------- 诊断时钟页
# 目的：把「延迟」和「卡顿」两种完全不同的毛病区分开。
#   · 延迟 = 画面连贯，但整体比真实时间慢 → 大时钟连续均匀地跳，只是落后
#   · 卡顿 = 画面断续，走一下停一下   → 大时钟会卡住不动，然后猛地跳一段
# 页面元素全部用纯色块（不用渐变/细线），因为投屏链路会做有损压缩，
# 渐变会被压出色带、细线会糊掉，反而干扰判断。
CLOCK_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>延迟 / 卡顿自测</title>
<style>
  html,body{margin:0;height:100%;background:#000;overflow:hidden;
    font-family:ui-monospace,Consolas,monospace;-webkit-user-select:none;
    user-select:none;cursor:none}
  #wrap{position:absolute;inset:0;display:flex;flex-direction:column;
    align-items:center;justify-content:center;gap:2.4vh}
  #t{color:#00ff66;font-weight:700;font-size:min(15vw,300px);line-height:.9;
    letter-spacing:.01em;white-space:nowrap}
  #bar{position:relative;width:88vw;height:min(4.6vh,58px);background:#101010;
    border:3px solid #2c2c2c;border-radius:10px;overflow:hidden}
  .tick{position:absolute;top:0;bottom:0;width:3px;background:#2c2c2c}
  #dot{position:absolute;top:0;bottom:0;left:0;width:min(5.4vh,68px);
    background:#ff2d55;border-radius:6px}
  #row{display:flex;gap:5vw;color:#8c939c;font-size:min(2.3vw,30px);
    align-items:baseline}
  #row i{font-style:normal;font-size:.78em}
  #row b{color:#e8e8e8;font-weight:700;font-size:1.25em}
  #row b.warn{color:#ff2d55}
  #tip{color:#6f7a86;font-size:min(1.9vw,24px);text-align:center;
    line-height:1.5;max-width:92vw}
  #tip em{color:#00ff66;font-style:normal}
  #tip u{color:#ff2d55;text-decoration:none}
</style></head><body>
<div id="wrap">
  <div id="t">--:--:--.---</div>
  <div id="bar"><div id="dot"></div></div>
  <div id="row">
    <span><i>本机渲染帧</i> <b id="f">0</b></span>
    <span><i>卡顿次数</i> <b id="s">0</b></span>
    <span><i>最长一帧</i> <b id="g">0</b> ms</span>
  </div>
  <div id="tip">
    盯着<em>数字</em>看：连续均匀地跳 = <em>延迟</em>（只是慢，不乱）
    ｜ 卡住不动再猛跳 = <u>卡顿</u><br>
    盯着<u>红条</u>看：匀速滑动 = 好 ｜ 一顿一顿 = 卡顿
  </div>
</div>
<script>
(function(){
  var bar=document.getElementById('bar'), dot=document.getElementById('dot'),
      elT=document.getElementById('t'), elF=document.getElementById('f'),
      elS=document.getElementById('s'), elG=document.getElementById('g');
  // 进度条刻度：10 格，每格 100 ms，丢帧时会看到红条跳过格线
  for (var i=1;i<10;i++){
    var k=document.createElement('div'); k.className='tick';
    k.style.left=(i*10)+'%'; bar.appendChild(k);
  }
  function pad(n,w){n=String(n);while(n.length<w)n='0'+n;return n}
  var frames=0, stalls=0, lastTs=0, worst=0, lastPaint=0;
  function tick(ts){
    var d=new Date();
    elT.textContent = pad(d.getHours(),2)+':'+pad(d.getMinutes(),2)+':'+
                      pad(d.getSeconds(),2)+'.'+pad(d.getMilliseconds(),3);

    // 红条位置由「真实时间」决定而非逐帧累加 —— 所以它一旦停顿或跳跃，
    // 就是链路上真的丢了帧，而不是脚本算错。
    // 三角波：1 秒从左到右、1 秒从右到左，转折处不会突变。
    var ph=(d.getTime()%2000)/2000;
    var p = ph<0.5 ? ph*2 : (1-ph)*2;
    dot.style.left = (p*(bar.clientWidth-dot.clientWidth))+'px';

    frames++;
    if (lastTs){
      var gap = ts-lastTs;
      if (gap>worst){ worst=gap; elG.textContent=Math.round(worst); }
      // 超过 120 ms 才算一次停顿（正常 60Hz 渲染约 16.7 ms，30Hz 约 33 ms）
      if (gap>120){
        stalls++;
        elS.textContent=stalls;
        elS.className='warn';
        elG.className='warn';
      }
    }
    lastTs=ts;
    elF.textContent=frames;
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
})();
</script></body></html>
"""


class CastHTTPHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "ScreenCast/1.0"
    _seg_count = 0

    def log_message(self, fmt, *args):
        pass    # 静默

    def setup(self):
        BaseHTTPRequestHandler.setup(self)
        # 关掉 Nagle 算法，让每个 TS 包立即发出，不参与合并等待
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass

    # ---- MPEG-TS 直播流 ----
    def _serve_live_ts(self):
        global BROADCASTER
        bc = BROADCASTER
        if bc is None:
            self.send_error(503, "stream not ready")
            return
        self.send_response(200)
        self.send_header("Content-Type", "video/mp2t")
        self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Accept-Ranges", "none")
        self.end_headers()
        first_client = (bc.clients == 0)
        bc.clients += 1
        BC_STATE["clients"] = bc.clients
        if first_client:
            # 首个观看者接入，做两件事：
            #   1) 丢弃历史积压 —— 避免"先补播几秒旧画面"
            #   2) 把起点对齐到下一个 IDR —— 否则客户端从 GOP 中间开始
            #      收流，开头必然花屏到下一个关键帧
            bc.clear()
            t0 = time.time()
            tossed = 0
            tries = 0
            while bc.running and time.time() - t0 < 1.5:
                r = bc.align_to_idr()
                tries += 1
                if r != 0:
                    tossed = max(0, r)
                    break
                # 事件驱动地等下一批数据（_pump 会 notify），
                # 比固定 20 ms 轮询少一份抖动延迟
                with bc.cv:
                    bc.cv.wait(0.05)
            log("  [stream] 起播对齐关键帧：耗时 %.0f ms、前移 %.1f KB"
                " —— 客户端从关键帧开始解码，不会花屏",
                (time.time() - t0) * 1000, tossed / 1024.0)
        log("  [stream] 电视已接入直播流")
        last_report = time.time()
        last_bytes = bc.total_bytes      # 以接入点为起点算瞬时码率，
                                         # 否则第一条日志会把开播前的累计量
                                         # 一并算进去，虚报成上万 kbps

        # ---- 发送节流：消除"播放越久延迟越大" ----
        # 闸门 = 「已发送字节」不得超过「新产出字节 + lead_s 对应的字节」。
        #
        # ★ 基准取「接入后 ffmpeg 新产出的字节数」，而不是
        #   「已发送字节 / 平均码率 − 墙钟经过时间」：
        #   后一个式子把码率估计误差直接放大成超前量，实测瞬时码率在
        #   8803 kbps ↔ 2500 kbps 之间波动时，超前量冲到 943 ms（设定 800 ms）。
        #   改为纯字节比较后，闸门是否放行与码率估计无关；码率只负责把
        #   "超出多少字节"折算成"应当等待多少毫秒"，误差不再累积。
        #
        #   基准用编码产出速率而非发送速率：后者一旦被闸门压低，基准也
        #   跟着降低，闸门会把自己越限越死（正反馈）。
        lead_s = max(0.0, PACE_LEAD_MS) / 1000.0
        paced_bytes = 0
        paced_t0 = time.time()
        paced_wait = 0.0
        produced0 = bc.total_bytes       # 闸门零点：接入瞬间的累计产出
        last_dropped = bc.dropped_bytes  # 丢弃量零点，用于算"丢了多少"的速率
        warn_t = 0.0                     # 「链路吞吐不足」告警的限频计时
        try:
            while True:
                # 读块收到 16 KB：闸门精度 ≈ 16 KB 折合的内容时长
                # （@4.5 Mbps 约 28 ms），既够细又不至于让循环过密
                d = bc.read(n=16384, timeout=1.0)
                if d is None:
                    break
                if d == b"":
                    continue
                # 闸门必须放在 write 之前，sleep 才能真正推迟"占满管道"
                paced_bytes += len(d)
                rate = bc.rate_bps()
                if lead_s > 0.0 and rate > 2048.0:
                    produced = bc.total_bytes - produced0
                    over = paced_bytes - produced - rate * lead_s
                    if over > 0:
                        wait = min(over / rate, 0.4)
                        time.sleep(wait)
                        paced_wait += wait
                        BC_STATE["paced_wait_ms"] = paced_wait * 1000.0
                t_w = time.time()
                self.wfile.write(d)
                self.wfile.flush()
                cost = time.time() - t_w
                BC_STATE["delivered"] += len(d)
                BC_STATE["last_deliver"] = time.time()
                if cost > 0.30:
                    # 对端消费不过来（电视正在缓冲，或它自己卡住了）。
                    # 立刻把积压丢到目标水位，否则延迟会像滚雪球一样涨。
                    dropped = bc.skip_to_live()
                    BC_STATE["slow_events"] += 1
                    if dropped:
                        log("  [stream] 电视消费缓慢（单次写 %.0f ms），"
                            "已追帧丢弃 %.0f KB", cost * 1000, dropped / 1024.0)
                now = time.time()
                if now - last_report >= 5.0:
                    dt = now - last_report
                    inst = (bc.total_bytes - last_bytes) * 8 / dt / 1000.0
                    drop_kbps = ((bc.dropped_bytes - last_dropped)
                                 * 8 / dt / 1000.0)
                    last_bytes = bc.total_bytes
                    last_dropped = bc.dropped_bytes
                    last_report = now
                    # 报告「客户端相对本进程新产出的内容多领取了多久」。
                    # 不用 paced_bytes/rate − elapsed：那个式子会把码率估计
                    # 误差直接放大成超前量，数值虚高（实测虚报 1097 ms，
                    # 而闸门按字节比较根本没放行那么多），容易误判闸门失效。
                    over_ms = 0.0
                    if rate > 2048.0:
                        over_ms = ((paced_bytes
                                    - (bc.total_bytes - produced0))
                                   / rate * 1000.0)
                    log("  [stream] %.0f kbps | 本机缓冲 %.0f ms | "
                        "领取余量 %.0f ms | 闸门等待 %.0f ms | 已丢弃 %.0f KB",
                        inst, bc.backlog_ms, over_ms,
                        paced_wait * 1000.0, bc.dropped_bytes / 1024.0)

                    # ---- 链路吞吐不足的自动判定 ----
                    # 丢弃是压住延迟必须做的取舍：电视端一旦持续消费不过来，
                    # 积压只能丢。但若丢弃长期占产出的 5% 以上，说明当前档位的
                    # 码率本身就超过了这条链路的能力 —— 靠调缓冲、调节流都救
                    # 不了，只会周期性卡顿，唯一的出路是把产出降到链路之下。
                    # 这条日志把「产出 / 实收 / 缺多少 / 建议值」一次讲清，
                    # 不需要用户去读代码或换算。
                    if inst > 3000.0 and drop_kbps > inst * 0.05:
                        if now - warn_t >= 30.0:
                            warn_t = now
                            deliver = max(500.0, inst - drop_kbps)
                            log("  [stream] ★ 链路吞吐不足：本机产出 %.1f Mbps，"
                                "电视端只能持续收下约 %.1f Mbps，"
                                "差额 %.0f%% 正在被丢弃（画面会周期性卡顿）",
                                inst / 1000.0, deliver / 1000.0,
                                drop_kbps / inst * 100.0)
                            log("             当前档位码率超出这条链路，"
                                "请改选更低画质档位；"
                                "或加 --bitrate %d 手动指定",
                                max(800, int(deliver * 0.85)))
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            bc.clients -= 1
            if bc.clients < 0:
                bc.clients = 0
            BC_STATE["clients"] = bc.clients
            log("  [stream] 电视断开直播流")

    # ---- HLS ----
    def _serve_hls_file(self, rel):
        global HLS_DIR
        if not HLS_DIR:
            self.send_error(503, "hls not ready")
            return
        safe = os.path.basename(rel)
        path = os.path.join(HLS_DIR, safe)
        if not os.path.isfile(path):
            self.send_error(404, "not found")
            return
        with open(path, "rb") as f:
            data = f.read()
        if safe.endswith(".m3u8"):
            ctype = "application/vnd.apple.mpegurl"
            log("  [hls] 电视拉取播放列表 %s (%d B)", safe, len(data))
        else:
            ctype = "video/mp2t"
            CastHTTPHandler._seg_count += 1
            if CastHTTPHandler._seg_count <= 3:
                log("  [hls] 电视拉取分片 %s", safe)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def do_HEAD(self):
        self.do_GET(head_only=True)

    def _serve_static(self, name, ctype, head_only=False):
        """提供 static/ 下的文件（电视端播放页与播放库）。

        走本地文件而不是 CDN —— 电视不一定能连外网，但一定能连局域网。
        文件读进内存后缓存，避免每次请求都读盘（mpegts.js 有 200+ KB）。
        """
        key = os.path.basename(name)
        data = _STATIC_CACHE.get(key)
        if data is None:
            p = os.path.join(STATIC_DIR, key)
            if not os.path.isfile(p):
                self.send_error(404, "static missing: %s" % key)
                return
            try:
                with open(p, "rb") as f:
                    data = f.read()
            except OSError:
                self.send_error(500, "static read failed")
                return
            _STATIC_CACHE[key] = data
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        if not head_only:
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    def do_GET(self, head_only=False):
        path = self.path.split("?", 1)[0]
        if path in ("/live.ts", "/live"):
            if head_only:
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.end_headers()
                return
            self._serve_live_ts()
        elif path in ("/clock", "/clock.html"):
            body = i18n.localize_html(CLOCK_PAGE).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            if not head_only:
                try:
                    self.wfile.write(body)
                except Exception:
                    pass
        elif path in ("/tv", "/tv.html"):
            self._serve_static("tv.html", "text/html; charset=utf-8", head_only)
        elif path == "/mpegts.js":
            self._serve_static("mpegts.js", "application/javascript; charset=utf-8",
                               head_only)
        elif path.endswith(".m3u8") or path.endswith(".ts"):
            self._serve_hls_file(path)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Connection", "close")
            body = b"screen-cast is running\n"
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head_only:
                try:
                    self.wfile.write(body)
                except Exception:
                    pass


def port_candidates(preferred):
    """尝试顺序：首选端口 → 回退候选 → 0（交给系统分配）。

    绑定和界面探测共用这一份顺序，免得两边算出不同结果。
    """
    return [preferred] + [x for x in FALLBACK_PORTS if x != preferred] + [0]


def pick_http_port(preferred):
    """探测「实际会绑上的端口」，只探测不保留 —— 界面自检显示用。

    Windows 会用保留区间成段吞掉端口（见 start_http_server），
    自检里若照抄配置值，用户会拿到一个根本连不上的地址。
    """
    for p in port_candidates(preferred):
        s = socket.socket()
        try:
            s.bind(("0.0.0.0", p))
            return s.getsockname()[1] if p == 0 else p
        except OSError:
            continue
        finally:
            s.close()
    return preferred


def start_http_server(port):
    """绑定 HTTP 流服务；首选端口不可用时自动回退，绝不因此启动失败。

    Windows 上 bind 失败有两种成因，都不是「多试几次」能绕开的：
      WinError 10013 (WSAEACCES) —— 端口落在系统「保留区间」。Hyper-V / WSL2 /
          Docker 会动态保留成段端口，且**每次重启都可能变**。实测本机
          8880-9079 整段被占，所以 8899 必定绑不上 —— 这既不是防火墙问题，
          也不是端口冲突，加防火墙规则、用管理员运行都没用。
      WinError 10048 (WSAEADDRINUSE) —— 已被别的进程占用。
    候选全部失败时退化为 port=0，由系统分配一个空闲端口。
    """
    global HTTP_PORT
    last_err = None
    for p in port_candidates(port):
        try:
            srv = ThreadingHTTPServer(("0.0.0.0", p), CastHTTPHandler)
        except OSError as e:
            last_err = e
            continue
        actual = srv.server_address[1]
        if actual != port:
            why = ("被系统保留" if getattr(last_err, "winerror", None) == 10013
                   else "已被占用")
            log("[端口] 首选 %d %s，已自动改用 %d", port, why, actual)
        HTTP_PORT = actual
        srv.daemon_threads = True
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        return srv
    raise RuntimeError("没有可用端口（最后错误：%s）" % last_err)


# ---------------------------------------------------------------- ffmpeg


def get_desktop_size(primary=False):
    """返回抓屏尺寸 (w, h)。

    primary=False —— gdigrab 的 `-i desktop` 抓整个虚拟桌面（多显示器为并集），
                     故读 SM_*VIRTUALSCREEN。
    primary=True  —— ddagrab 的 output_idx=0 只抓主显示器，故读 SM_*SCREEN。
    """
    if not _WIN:
        return None
    try:
        import ctypes
        u = ctypes.windll.user32
        try:
            u.SetProcessDPIAware()
        except Exception:
            pass
        if primary:
            w = int(u.GetSystemMetrics(0))      # SM_CXSCREEN
            h = int(u.GetSystemMetrics(1))      # SM_CYSCREEN
        else:
            w = int(u.GetSystemMetrics(78))     # SM_CXVIRTUALSCREEN
            h = int(u.GetSystemMetrics(79))     # SM_CYVIRTUALSCREEN
        if w <= 0 or h <= 0:
            w = int(u.GetSystemMetrics(0))
            h = int(u.GetSystemMetrics(1))
        if w <= 0 or h <= 0:
            return None
        return (w, h)
    except Exception:
        return None


# 各高度对应的"最高档"码率，用于档位被降级后重新定档
# 1080 一档与 QUALITY_PRESETS["1080hq"] 保持一致，且受链路能力约束（见上）
_BEST_RATE_BY_HEIGHT = [
    (540,  (1200, 1400)),
    (720,  (2500, 2900)),
    (1080, (5500, 6500)),
    (1440, (14000, 17000)),
    (2160, (28000, 34000)),
]


def bitrate_for_height(height, fallback):
    """按实际输出高度取该高度的最高档码率。

    档位被降级（如 1080p 屏幕选了 4K 档）时，码率必须跟着降：
    28 Mbps 灌进 1080p 不会让画面更清晰，只会挤爆局域网并推高延迟。
    """
    best = None
    for h, rate in _BEST_RATE_BY_HEIGHT:
        if h <= height:
            best = rate
    return best if best else fallback


def resolve_video_geometry(quality, src_size=None, allow_upscale=False):
    """把画质档位解析为 (目标高度, 是否被降级, 源高度)。

    目标高度 = min(档位高度, 源高度)，除非显式允许上采样。
    上采样不会带来任何画质提升，只会成倍增加码率与 CPU 开销。
    """
    target_h, _br, _mr = QUALITY_PRESETS.get(quality,
                                             QUALITY_PRESETS["720"])
    src_h = src_size[1] if src_size else None
    downgraded = False
    if src_h and not allow_upscale and target_h > src_h:
        target_h = src_h
        downgraded = True
    return target_h, downgraded, src_h


def build_ffmpeg_cmd(ffmpeg, mode, quality, fps, hls_dir, audio_mode="none",
                     audio_dev=None, audio_fmt=None, audio_offset=0.0,
                     verbose=False, gop_ms=500, encoder="x264",
                     src_size=None, allow_upscale=False, capture="gdigrab",
                     osd_text=None, osd_font=None, osd_size=0, osd_margin=0,
                     progress_path=None, bitrate_override=0):
    """构造 ffmpeg 命令行。

    bitrate_override : >0 时用该值（kbps）覆盖画质档位的目标码率，
                 峰值自动取 1.18 倍。用于链路吞吐与档位默认值不匹配时的微调。

    audio_mode : "none" | "pipe"（WASAPI 环回经 stdin 送入）| "dshow"
    audio_fmt  : (sample_fmt, rate, channels)，audio_mode == "pipe" 时必填
    gop_ms     : 关键帧间隔（毫秒）。越小，追赶丢帧后恢复越快（花屏窗口越短），
                 代价是码率上升。默认 500；--low-latency 下用 400。
    encoder    : "x264"（软编，帧率最稳、画质最实）| "mf"（硬件编码，CPU 最省）
    capture    : "gdigrab"（GDI，兼容性最好，1080p 上限 30 fps）
                 | "ddagrab"（Desktop Duplication，GPU 抓帧，CPU 低、帧率更高）
    osd_text   : 画面 OSD 的文本文件路径（drawtext 每帧重读，实现实时数值）
    osd_font   : 字体文件；缺一即不启用 OSD
    osd_size   : OSD 字号（像素）
    osd_margin : OSD 距画面右上角的边距（像素）
    progress_path : ffmpeg -progress 输出文件，供 OSD 取 fps/码率/内容时长
    """
    target_h, downgraded, _src_h = resolve_video_geometry(
        quality, src_size, allow_upscale)
    _th, bitrate, maxrate = QUALITY_PRESETS.get(quality,
                                                QUALITY_PRESETS["720"])
    if downgraded:
        # 分辨率降了，码率必须一起降，否则只是白白占用带宽
        bitrate, maxrate = bitrate_for_height(target_h, (bitrate, maxrate))
    if bitrate_override and bitrate_override > 0:
        # 手动档优先：链路实测吞吐与档位默认值不匹配时用来微调
        bitrate = int(bitrate_override)
        maxrate = int(round(bitrate * 1.18))

    # 像素格式按编码器选：h264_mf 原生吃 nv12，选它可省一次格式转换
    pix = "nv12" if encoder == "mf" else "yuv420p"
    vf = []
    if capture == "ddagrab":
        # ddagrab 产出的是 GPU 上的 d3d11 纹理，必须先下载到内存才能缩放/转格式
        vf.append("hwdownload")
        vf.append("format=bgra")
    if target_h:
        vf.append("scale=-2:%d:flags=fast_bilinear" % target_h)
    if osd_text and osd_font:
        # drawtext 在常规内存像素格式上渲染。先统一到 yuv420p；
        # 若编码器要 nv12，则在叠加文字之后再转，避免文字被二次采样糊掉。
        vf.append("format=yuv420p")
        vf.append(build_osd_filter(osd_text, osd_font, osd_size, osd_margin))
        if pix != "yuv420p":
            vf.append("format=%s" % pix)
    else:
        vf.append("format=%s" % pix)
    vf = ",".join(vf)

    cmd = [ffmpeg, "-hide_banner"]
    # 音频经 stdin 送入时不能加 -nostdin，否则 pipe:0 无法打开
    if audio_mode != "pipe":
        cmd += ["-nostdin"]
    cmd += ["-loglevel", "info" if verbose else "warning"]

    # 视频输入
    #   gdigrab : GDI BitBlt 抓屏，兼容性最好；1080p 实测帧率上限 30 fps
    #             （BitBlt 全屏耗时决定，请求 60 也只出 30）
    #   ddagrab : Desktop Duplication（GPU 抓帧），CPU 占用低、帧率上限远高于 30，
    #             但需要可用的 D3D11 桌面会话，失败时由 start_pipeline 回退 gdigrab
    #
    # thread_queue_size 收到 64：实时流的输入队列越深，一旦积压就越不可追，
    # 把瞬时抖动转成持久延迟。64 个包足以吸收抖动，又不会攒出可见延迟。
    if capture == "ddagrab":
        cmd += ["-fflags", "nobuffer",
                "-thread_queue_size", "64",
                "-f", "lavfi",
                "-i", "ddagrab=output_idx=0:framerate=%d:draw_mouse=1" % fps]
    else:
        cmd += ["-fflags", "nobuffer",
                "-f", "gdigrab", "-framerate", str(fps),
                "-thread_queue_size", "64",
                "-i", "desktop"]

    # 音频输入
    if audio_mode == "pipe" and audio_fmt:
        sfmt, rate, ch = audio_fmt
        if audio_offset:
            cmd += ["-itsoffset", "%.3f" % audio_offset]
        cmd += ["-fflags", "nobuffer",
                "-thread_queue_size", "64",
                "-f", sfmt, "-ar", str(rate), "-ac", str(ch), "-i", "pipe:0"]
    elif audio_mode == "dshow" and audio_dev:
        cmd += ["-f", "dshow", "-thread_queue_size", "64",
                "-i", "audio=%s" % audio_dev]

    gop = max(1, int(round(fps * gop_ms / 1000.0)))

    if encoder == "mf":
        # MediaFoundation 硬件编码（Intel/AMD/NVIDIA 通用）。
        # display_remoting 是专为「屏幕远程投放」优化的场景档，延迟最低。
        cmd += [
            "-vf", vf,
            "-c:v", "h264_mf",
            "-hw_encoding", "1",
            "-scenario", "display_remoting",
            "-rate_control", "ld_vbr",
            # 注意：h264_mf 未实现 -profile:v（会报 Eval 解析错误），不要加
            "-pix_fmt", pix,
            "-b:v", "%dk" % bitrate,
            "-maxrate", "%dk" % maxrate,
            "-bufsize", "%dk" % bitrate,
            "-bf", "0",
            "-g", str(gop),
        ]
    else:
        cmd += [
            "-vf", vf,
            "-c:v", "libx264",
            # ★ preset 用 veryfast 而不是 ultrafast：
            #   ultrafast 是最差的压缩档，同画质要多花约 10% 码率，而码率在本
            #   场景是稀缺资源（电视端消费能力 ~7 Mbps 是硬上限）。veryfast
            #   在 4 核 8 线程笔记本上占约 1 个核，1080p30 完全跟得上，不拖累帧率。
            #   zerolatency 已关掉 B 帧与 lookahead，编码延迟仍在几十毫秒级。
            "-preset", "veryfast",
            "-tune", "zerolatency",
            "-profile:v", "main",
            "-pix_fmt", "yuv420p",
            "-b:v", "%dk" % bitrate,
            "-maxrate", "%dk" % maxrate,
            "-bufsize", "%dk" % bitrate,     # 收紧编码缓冲（原为 maxrate * 2）
            "-bf", "0",                      # 无 B 帧，消除重排序延迟
            "-g", str(gop),
            "-keyint_min", str(gop),
            "-sc_threshold", "0",
            "-flags", "low_delay",
            "-x264-params", "nal-hrd=none:force-cfr=1",
        ]

    if audio_mode == "none":
        cmd += ["-an"]
    else:
        cmd += ["-c:a", "aac", "-b:a", "160k", "-ac", "2", "-ar", "48000"]

    # 音视频包紧密交织：默认 10 秒的交织跨度会让音、视频在 TS 里相距很远，
    # 按字节丢弃积压时两者丢掉的时间跨度不一致 —— 这是长期运行后
    # 音画逐渐错位的根因。压到 100 ms 后可保证同段丢弃。
    cmd += ["-max_interleave_delta", str(INTERLEAVE_DELTA_US)]

    # -progress：让 ffmpeg 周期性把 frame/fps/bitrate/out_time 写到文件。
    # OSD 直接用 ffmpeg 自报的数据，比在 Python 侧另起一套统计更准
    # （它知道真实编码帧数与输出字节数，而且含编码器内部状态）。
    if progress_path:
        cmd += ["-progress", progress_path]

    if mode == "hls":
        os.makedirs(hls_dir, exist_ok=True)
        m3u8 = os.path.join(hls_dir, "live.m3u8").replace("\\", "/")
        seg = os.path.join(hls_dir, "seg%05d.ts").replace("\\", "/")
        cmd += [
            "-f", "hls",
            "-hls_time", "1",
            "-hls_list_size", "3",
            "-hls_flags", "delete_segments+omit_endlist+independent_segments",
            "-hls_segment_type", "mpegts",
            "-hls_segment_filename", seg,
            m3u8,
        ]
    else:
        cmd += ["-f", "mpegts",
                "-muxdelay", "0", "-muxpreload", "0",
                "-flush_packets", "1",
                "pipe:1"]

    return cmd


# ffmpeg 的 stderr 环形缓冲（诊断用）。每次 start_ffmpeg 会清空一次，
# 因此里面永远只保留"当前这次编码尝试"的输出。
FFMPEG_LOG_MAX = 200
FFMPEG_LOG = collections.deque(maxlen=FFMPEG_LOG_MAX)
FFMPEG_LOG_LOCK = threading.Lock()


def start_ffmpeg(cmd, verbose, stdin_pipe=False):
    """启动 ffmpeg 进程，并**无条件**收集它的 stderr。

    以前非 verbose 模式把 stderr 直接丢进 DEVNULL，结果是：一旦 ffmpeg
    自己退出，控制台只留一句"返回码 -13"，完全无从判断原因（这正是
    OSD 那个 bug 排查了半天的原因）。现在无论是否 verbose 都接管 stderr：
      · 保留最后 FFMPEG_LOG_MAX 行，崩溃时打印出来
      · 顺带避免 stderr 管道写满把 ffmpeg 卡死（必须持续读走）
    """
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with FFMPEG_LOG_LOCK:
        FFMPEG_LOG.clear()
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE if stdin_pipe else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,          # raw 模式：管道不额外缓冲，避免延迟堆积
        creationflags=creationflags,
    )

    def _reader(stream=proc.stderr):
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip()
                if not line:
                    continue
                with FFMPEG_LOG_LOCK:
                    FFMPEG_LOG.append(line)
                if verbose:
                    log("  [ffmpeg] %s", line)
        except Exception:
            pass

    threading.Thread(target=_reader, daemon=True).start()
    return proc


def explain_ffmpeg_exit(code):
    """把 ffmpeg 退出码翻译成人话，省去每次现查 errno。"""
    if code is None:
        return
    c = code - 0x100000000 if code > 0x7FFFFFFF else code
    hints = {
        0: "正常结束（不该在投屏中出现 —— 检查是否有 -t 之类的时长限制）",
        -1: "EPERM 操作不被允许 —— 常见于输出端（管道/文件）被占用或关闭",
        -5: "EIO 读写出错",
        -13: "EACCES 权限/共享被拒 —— 通常是某个文件被另一个进程并发读写",
        -22: "EINVAL 参数非法 —— 某个选项或滤镜参数不被支持",
        -32: "EPIPE 输出端已断开（对端不再读取）",
        1: "一般性错误 —— 看下面的 ffmpeg 输出",
        139: "段错误 —— 已知组合：ddagrab 配 h264_mf（代码里已规避）",
    }
    if c in hints:
        log("  退出码 %s 的含义：%s", c, hints[c])


def dump_ffmpeg_log(tail=20):
    """打印 ffmpeg 最近的 stderr 尾巴，用于定位它为什么退出。"""
    with FFMPEG_LOG_LOCK:
        lines = list(FFMPEG_LOG)[-tail:]
    if not lines:
        log("  （ffmpeg 没有输出任何信息，无更多线索）")
        return
    log("  —— ffmpeg 最后 %d 行输出 ——", len(lines))
    for s in lines:
        log("  | %s", s)


# ---------------------------------------------------------------- DLNA 推送


def push_to_tv(dev, url, protocol_info, title="电脑屏幕"):
    avt = dev.services.get("AVTransport")
    if not avt:
        raise RuntimeError("该设备没有 AVTransport 服务，不能作为播放目标")
    stype, ctrl = avt

    didl = (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        '<item id="0" parentID="-1" restricted="1">'
        "<dc:title>%s</dc:title>"
        "<upnp:class>object.item.videoItem</upnp:class>"
        '<res protocolInfo="%s">%s</res>'
        "</item></DIDL-Lite>" % (escape(title), escape(protocol_info), escape(url))
    )

    inner = (
        "<InstanceID>0</InstanceID>"
        "<CurrentURI>%s</CurrentURI>"
        "<CurrentURIMetaData>%s</CurrentURIMetaData>"
        % (escape(url), escape(didl))
    )
    log("  -> SetAVTransportURI ...")
    resp = soap_call(ctrl, stype, "SetAVTransportURI", inner)
    if "errorCode" in resp or "faultcode" in resp.lower():
        raise RuntimeError("SetAVTransportURI 被拒绝:\n%s" % resp)

    time.sleep(0.6)
    log("  -> Play ...")
    resp2 = soap_call(ctrl, stype, "Play",
                      "<InstanceID>0</InstanceID><Speed>1</Speed>")
    if "errorCode" in resp2 or "faultcode" in resp2.lower():
        raise RuntimeError("Play 被拒绝:\n%s" % resp2)
    return True


def stop_tv(dev):
    avt = dev.services.get("AVTransport")
    if not avt:
        return
    stype, ctrl = avt
    try:
        soap_call(ctrl, stype, "Stop", "<InstanceID>0</InstanceID>", timeout=4)
        log("  -> 已通知电视停止播放")
    except Exception as e:
        log("  -> 停止指令未送达（可忽略）: %s", e)


# ---------------------------------------------------------------- 交互


def choose_device(devices):
    if not devices:
        return None
    if len(devices) == 1:
        log("发现 1 台设备：%s", devices[0].name)
        return devices[0]
    log("")
    log("发现 %d 台可投屏设备：", len(devices))
    for i, d in enumerate(devices, 1):
        log("  [%d] %s  (%s)", i, d.name, d.ip)
    log("  [R] 重新扫描")
    log("  [0] 退出")
    while True:
        try:
            k = input(T("请选择: ")).strip().upper()
        except (EOFError, KeyboardInterrupt):
            return None
        if k == "0":
            return None
        if k == "R":
            return "RESCAN"
        if k.isdigit():
            idx = int(k)
            if 1 <= idx <= len(devices):
                return devices[idx - 1]
        log("输入无效，请重试。")


def choose_quality(src_size=None):
    src_h = src_size[1] if src_size else None
    log("")
    log("选择画质：")
    keys = QUALITY_ORDER
    for i, k in enumerate(keys, 1):
        note = ""
        tgt_h = QUALITY_PRESETS[k][0]
        if src_h and tgt_h > src_h:
            note = T("  ← 本机屏幕为 %dp，选它会自动降级为 %dp") % (src_h, src_h)
        log("  [%d] %s%s", i, QUALITY_LABELS[k], note)
    if src_h:
        log("      （本机屏幕 %dp：1080hq 才是真正提高画质的那一档）", src_h)
    while True:
        try:
            k = input(T("请选择 [1]: ")).strip() or "1"
        except (EOFError, KeyboardInterrupt):
            return "720"
        if k.isdigit() and 1 <= int(k) <= len(keys):
            return keys[int(k) - 1]
        if k in QUALITY_PRESETS:
            return k
        return "720"


# ---------------------------------------------------------------- 编码器探测


_ENCODER_CACHE = {}


def ffmpeg_has_encoder(ffmpeg, name):
    """查 ffmpeg 是否内置某个编码器（只查一次并缓存）。"""
    if name in _ENCODER_CACHE:
        return _ENCODER_CACHE[name]
    ok = False
    try:
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        p = subprocess.run([ffmpeg, "-hide_banner", "-encoders"],
                           capture_output=True, text=True, timeout=20,
                           creationflags=creationflags)
        ok = name in (p.stdout or "")
    except Exception:
        ok = False
    _ENCODER_CACHE[name] = ok
    return ok


def resolve_encoder(ffmpeg, want):
    """把 --encoder auto 解析成实际可用的编码器名。"""
    if want == "x264":
        return "x264"
    if want == "mf":
        if ffmpeg_has_encoder(ffmpeg, "h264_mf"):
            return "mf"
        err("  警告：当前 ffmpeg 不含 h264_mf，已回退到 libx264 软编")
        return "x264"
    # auto：硬件优先
    for cand in ("h264_mf", "h264_qsv", "h264_nvenc"):
        if ffmpeg_has_encoder(ffmpeg, cand):
            name = cand.replace("h264_", "")
            if name in ("mf", "qsv", "nvenc"):
                log("编码器    : 自动选择 %s（硬件编码）", cand)
                return "mf" if name == "mf" else "x264"
    log("编码器    : 未发现可用硬件编码器，使用 libx264（软编）")
    return "x264"


# ---------------------------------------------------------------- 采集后端探测

_CAPTURE_CACHE = {}


def capture_available(ffmpeg, name):
    """探测采集后端是否真的能打开（只探一次并缓存）。

    注意：「滤镜列表里存在」不等于「能用」—— ddagrab 需要可用的 D3D11
    桌面会话，在服务会话 / 受限会话中会报 external library error。
    因此这里实抓 1 帧验证，而不是只查 -filters。
    """
    if name in _CAPTURE_CACHE:
        return _CAPTURE_CACHE[name]
    ok = False
    try:
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error"]
        if name == "ddagrab":
            cmd += ["-f", "lavfi", "-i", "ddagrab=output_idx=0:framerate=30",
                    "-vf", "hwdownload,format=bgra"]
        else:
            cmd += ["-f", "gdigrab", "-framerate", "30", "-i", "desktop"]
        cmd += ["-frames:v", "1", "-f", "null", "-"]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                           creationflags=creationflags)
        ok = (p.returncode == 0)
    except Exception:
        ok = False
    _CAPTURE_CACHE[name] = ok
    return ok


def resolve_capture(ffmpeg, want):
    """把 --capture 解析成实际可用的采集后端名。

    gdigrab 是保底方案：1080p 实测上限 30 fps（BitBlt 全屏耗时决定）。
    ddagrab 走 Desktop Duplication（GPU 抓帧），CPU 占用低且帧率上限更高，
    但依赖 D3D11 桌面会话，不可用时必须能安静地退回 gdigrab。
    """
    if want == "gdigrab":
        log("采集方式  : gdigrab（GDI 抓屏）")
        return "gdigrab"
    if capture_available(ffmpeg, "ddagrab"):
        log("采集方式  : ddagrab（Desktop Duplication，GPU 抓帧，CPU 占用低）")
        return "ddagrab"
    if want == "ddagrab":
        err("  警告：ddagrab 在本机不可用（需要可用的 D3D11 桌面会话），"
            "已回退 gdigrab")
    log("采集方式  : gdigrab（GDI 抓屏）")
    return "gdigrab"


# ---------------------------------------------------------------- 播放确认


def wait_healthy(window=3.0):
    """在 window 秒内观察投送字节数是否持续增长，判断电视是否真在消费。"""
    d0 = BC_STATE["delivered"]
    t0 = time.time()
    while time.time() - t0 < window:
        time.sleep(0.4)
        if BC_STATE["clients"] <= 0:
            return False
    return (BC_STATE["delivered"] - d0) > 32 * 1024


def confirm_playback(dev, url, protocol_info, wait_s=18.0, retries=2):
    """推送后确认电视真的在拉流并播放；否则 Stop + 重推。

    为什么需要这一步：实测电视端在开始投屏时会先播约 15 秒的启动广告，
    期间播放器要么根本不连流，要么连上之后长期只缓冲不消费 —— 数据全
    堆在电视内存里，广告结束才开始播放，于是用户看到的是 15 秒前的画面，
    表现为"延迟异常大"。重新推送（Stop + SetAVTransportURI + Play）会
    让电视端丢弃旧缓冲、重新拉取当前时刻的流，是唯一能从我们这侧生效的
    补救手段。
    """
    if retries <= 0:
        return True
    for attempt in range(retries + 1):
        if stop_requested():
            return False
        log("  [check] 等待电视接入并正常消费（最长 %.0f 秒）...", wait_s)
        deadline = time.time() + wait_s
        ok = False
        while time.time() < deadline:
            if stop_requested():
                # 用户点了停止：别再等下去，也不需要重推了
                return False
            if BC_STATE["clients"] > 0 and wait_healthy(2.5):
                ok = True
                break
        if ok:
            log("  [check] 电视正在正常播放，流稳定")
            return True
        if attempt >= retries:
            break
        reason = ("电视未接入流" if BC_STATE["clients"] <= 0
                  else "电视只缓冲不消费")
        log("  [check] %s（电视端可能仍在播启动广告），"
            "重新推送以清空电视端缓冲 ...（%d/%d）", reason, attempt + 1, retries)
        try:
            stop_tv(dev)
        except Exception:
            pass
        time.sleep(0.8)
        try:
            push_to_tv(dev, url, protocol_info)
        except Exception as e:
            log("  [check] 重推失败：%s", e)
    log("  [check] 未确认电视端播放状态。流服务仍在运行，"
        "可在电视上手动切到投屏/媒体播放界面重试")
    return False


# ---------------------------------------------------------------- 管道启动


def start_pipeline(ffmpeg, args, quality, hls_dir, audio_mode, audio_dev,
                   audio_fmt, encoder, src_size, loopback, capture="gdigrab"):
    """启动 ffmpeg + 流缓冲 + 音频喂入线程。

    两类失败都要能兜住：
      1) 编码器「在 -encoders 里存在但实际初始化失败」（驱动、远程会话、权限）；
      2) 采集后端打不开（ddagrab 依赖可用的 D3D11 桌面会话）。
    因此回退顺序是「先降编码器，再降采集方式」，最终必落到
    gdigrab + libx264 这个已实测可用的组合，避免用户只看到黑屏。

    返回 (proc, broadcaster, encoder, capture, feeder)；全部失败返回五个 None。
    """
    combos = [(encoder, capture)]
    if encoder != "x264":
        combos.append(("x264", capture))
    if capture != "gdigrab":
        combos.append((encoder, "gdigrab"))
        combos.append(("x264", "gdigrab"))
    # h264_mf + ddagrab 会让 ffmpeg 直接段错误退出（实测 exit=139，Intel QSV MFT
    # 与 ddagrab 帧不兼容）。明知会崩的组合直接剔除，不浪费一次启动去踩它。
    combos = [(e, c) for (e, c) in combos if not (e == "mf" and c == "ddagrab")]

    tried = []
    for enc, cap in combos:
        if (enc, cap) in tried:
            continue
        tried.append((enc, cap))

        osd_on = bool(OSD_ENABLED and OSD_FONT)
        if osd_on:
            # ffmpeg 跑起来才会往 -progress 文件写第一行。若上一次运行
            # （尤其是被强杀、没走 atexit 的那种）留下的残留还在，
            # OsdUpdater 会先读到旧数据，画面上闪一下错误的帧率/码率。
            # 每次启动前清掉，保证读到的一定是本进程的真实值。
            try:
                if os.path.isfile(OSD_PROGRESS_FILE):
                    os.remove(OSD_PROGRESS_FILE)
            except Exception:
                pass
        cmd = build_ffmpeg_cmd(
            ffmpeg, args.mode, quality, args.fps, hls_dir or "",
            audio_mode, audio_dev, audio_fmt, args.audio_offset, args.verbose,
            gop_ms=args.gop_ms, encoder=enc, capture=cap,
            src_size=src_size, allow_upscale=args.allow_upscale,
            osd_text=(OSD_TEXT_FILE if osd_on else None),
            osd_font=OSD_FONT, osd_size=OSD_SIZE, osd_margin=OSD_MARGIN,
            progress_path=(OSD_PROGRESS_FILE if osd_on else None),
            bitrate_override=getattr(args, "bitrate", 0))
        if args.verbose:
            log("ffmpeg 命令: %s", " ".join(cmd))

        proc = start_ffmpeg(cmd, args.verbose, stdin_pipe=(audio_mode == "pipe"))

        feeder = None
        if audio_mode == "pipe" and loopback is not None:
            feeder = AudioFeeder(loopback, proc.stdin)
            feeder.start()

        bc = None
        if args.mode == "mpegts":
            bc = MediaBroadcaster(proc,
                                  max_buffer=max(256 * 1024, args.buffer * 1024),
                                  target_ms=args.latency_ms)
            globals()["BROADCASTER"] = bc
            m3u8 = None
        else:
            m3u8 = os.path.join(hls_dir or "", "live.m3u8")

        ok = False
        t0 = time.time()
        limit = 10.0
        while time.time() - t0 < limit:
            if bc is not None:
                if bc.total_bytes > 0:
                    ok = True
                    break
            elif m3u8 and os.path.isfile(m3u8):
                ok = True
                break
            if proc.poll() is not None:
                break
            time.sleep(0.2)

        if ok:
            if cap != capture:
                log("采集方式  : 已回退到 %s", cap)
            return proc, bc, enc, cap, feeder

        code = proc.poll()
        err("编码器 %s + 采集 %s 启动失败%s，尝试回退 ...", enc, cap,
            "" if code is None else "（退出码 %s）" % code)
        if feeder is not None:
            feeder.stop()
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        if bc is not None:
            bc.stop()
            globals()["BROADCASTER"] = None
        try:
            proc.terminate()
            proc.wait(timeout=4)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    return None, None, None, None, None


# ---------------------------------------------------------------- 主流程


def ensure_firewall(port):
    """尝试放行入站端口（需要管理员权限，失败不阻断）。"""
    if sys.platform != "win32":
        return
    # 图形界面形态下没带 CREATE_NO_WINDOW 的话，这里会闪两个黑框
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.run(
            ["netsh", "advfirewall", "firewall", "add", "rule",
             "name=%s" % FIREWALL_RULE_NAME, "dir=in", "action=allow",
             "protocol=TCP", "localport=%d" % port],
            capture_output=True, timeout=8, creationflags=creationflags,
        )
        subprocess.run(
            ["netsh", "advfirewall", "firewall", "add", "rule",
             "name=%s-udp" % FIREWALL_RULE_NAME, "dir=in", "action=allow",
             "protocol=UDP", "localport=%d" % port],
            capture_output=True, timeout=8, creationflags=creationflags,
        )
    except Exception:
        pass


class CastConfig(object):
    """投屏参数集合。

    字段名与命令行参数一一对应（含 no_osd 这种否定式命名）——这样
    run_cast() 内部完全不必区分「参数来自命令行还是来自图形界面」，
    同一份逻辑两边共用，不会出现只有一边生效的错位。
    """

    def __init__(self, **kw):
        self.list = False           # 仅列出设备后退出
        self.number = None          # 直接选第 N 个设备
        self.ip = None              # 直接指定电视 IP（跳过扫描）
        self.mode = "mpegts"
        self.quality = "720"
        self.fps = 30
        self.port = DEFAULT_HTTP_PORT
        self.scan_timeout = 4.0
        self.encoder = "auto"
        self.capture = "auto"
        self.latency_ms = 300
        self.bitrate = 0
        self.gop_ms = 500
        self.low_latency = False
        self.allow_upscale = False
        self.push_retry = 2
        self.push_wait = 18.0
        self.clock = False
        self.no_osd = False
        self.osd_size = 0
        self.lead_ms = PACE_LEAD_MS
        self.audio = "loopback"
        self.audio_offset = 0.0
        self.keep_local_audio = False
        self.unmute = False
        self.buffer = 384
        self.no_push = False
        self.verbose = False
        self.__dict__.update(kw)


def build_parser(argv=None):
    ap = argparse.ArgumentParser(
        description=T("DLNA 屏幕投屏器（把本机屏幕投到智能电视）"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            T("想进一步降低延迟 ——\n"
            "  端到端延迟的主体是电视自带播放器的缓冲深度，本机侧调不动它。\n"
            "  绕开的办法：用【电视自己的浏览器】打开下面这个地址（属于同一个\n"
            "  局域网，启动时程序会打印出完整地址）：\n"
            "\n"
            "      http://<本机IP>:<端口>/tv\n"
            "\n"
            "  这个页面用 mpegts.js 直接播放裸 MPEG-TS 流，缓冲深度由页面控制，\n"
            "  可现场切换三档延迟（0.5s / 1.0s / 2.0s），并实时显示画面延迟、\n"
            "  缓冲、帧率、丢帧、码率。通常能把延迟从几秒压到 1 秒以内。\n"
            "  该页面仅在 mpegts 模式（默认）下提供。\n"
            "\n"
            "  若电视浏览器不支持 MSE，页面会提示改用 VLC（把「网络缓冲」\n"
            "  调到 300 毫秒）。\n"
            "\n"
            "实测延迟 ——\n"
            "  加 --clock 会打开毫秒时钟页，手机拍一张电脑与电视的同框照，\n"
            "  两个时间戳相减即端到端延迟。隔 30 秒再拍一张，数值变大说明\n"
            "  电视端缓冲在持续堆积。\n")
        ))
    ap.add_argument("-l", "--list", action="store_true", help=T("仅列出设备"))
    ap.add_argument("-n", "--number", type=int, help=T("直接选择第 N 个设备"))
    ap.add_argument("--ip", help=T("直接指定电视 IP（跳过扫描）"))
    ap.add_argument("--mode", choices=["mpegts", "hls"], default="mpegts",
                    help=T("推流模式，默认 mpegts"))
    ap.add_argument("--quality", choices=list(QUALITY_PRESETS.keys()),
                    default="720",
                    help=T("画质档位：540/720/1080/1080hq/1440(2K)/2160(4K)"))
    ap.add_argument("--fps", type=int, default=30,
                    help=T("帧率，默认 30。实测 gdigrab 1080p 可稳定跑满 30 —— "
                         "25 fps 白丢 17%% 的帧，且 25 与电视 60Hz 不整除"
                         "（60÷25=2.4）会产生节奏不均的 judder；30 整除最顺畅。"
                         "想更高需配合 --capture ddagrab"))
    ap.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT)
    ap.add_argument("--scan-timeout", type=float, default=4.0)
    ap.add_argument("--encoder", choices=["auto", "x264", "mf"], default="auto",
                    help=T("视频编码器：auto（默认，优先硬件编码，失败自动回退）"
                         " / mf（MediaFoundation 硬件编码，CPU 几乎不占，"
                         "适合 2K/4K 与低延迟） / x264（软编，帧率最稳、"
                         "输出 Main profile 画质更实）"))
    ap.add_argument("--capture", choices=["auto", "gdigrab", "ddagrab"],
                    default="auto",
                    help=T("屏幕采集后端。auto（默认，ddagrab 可用则优先，"
                         "不可用自动退回 gdigrab） / gdigrab（GDI 抓屏，"
                         "兼容性最好，1080p 实测上限 30 fps） / ddagrab"
                         "（Desktop Duplication，GPU 抓帧，CPU 占用低、"
                         "帧率上限更高，需可用的 D3D11 桌面会话）"))
    ap.add_argument("--latency-ms", type=int, default=300,
                    help=T("★ 本机流缓冲水位（毫秒）。这是本机侧延迟的唯一来源，"
                         "越小延迟越低，弱网下更易卡顿。默认 300"))
    ap.add_argument("--bitrate", type=int, default=0, metavar="KBPS",
                    help=T("手动指定视频目标码率（kbps），覆盖画质档位的默认值，"
                         "峰值自动取 1.18 倍。0 = 用档位默认。"
                         "当电视端持续卡顿、日志出现\"链路吞吐不足\"时，"
                         "用一个比实测交付速率低 15%% 左右的值即可"))
    ap.add_argument("--gop-ms", type=int, default=500,
                    help=T("关键帧间隔（毫秒）。越小，追帧丢弃后画面恢复越快，"
                         "代价是码率上升。默认 500"))
    ap.add_argument("--low-latency", action="store_true",
                    help=T("一键流畅优先预设：x264 软编 + 250ms 缓冲 + "
                         "400ms 关键帧 + 30fps（帧率最稳、judder 最小）"))
    ap.add_argument("--allow-upscale", action="store_true",
                    help=T("允许把画面放大到档位分辨率。默认禁止：1080p 屏幕选 4K 档"
                         "会自动降级到 1080p，因为放大不会变清晰，只增开销"))
    ap.add_argument("--push-retry", type=int, default=2,
                    help=T("电视未接入、或接入后只缓冲不消化时的自动重推次数"
                         "（重推会强制电视丢弃旧缓冲重新拉流），默认 2，0 关闭"))
    ap.add_argument("--push-wait", type=float, default=18.0,
                    help=T("每次推送后等待电视接入并正常消费的秒数，默认 18（覆盖常见 15 秒广告）"))
    ap.add_argument("--clock", action="store_true",
                    help=T("额外打开毫秒时钟页，用于实测端到端延迟"
                         "（电脑与电视同框拍照，读时间差）"))
    ap.add_argument("--no-osd", action="store_true",
                    help=T("关闭画面右上角的信息显示。默认开启："
                         "帧率 / 码率 / 端到端延迟(ms)，灰白小字带阴影"))
    ap.add_argument("--osd-size", type=int, default=0,
                    help=T("OSD 字号（像素）。默认按输出分辨率自动取约 1.8%% 高度"))
    ap.add_argument("--lead-ms", type=int, default=PACE_LEAD_MS,
                    help=T("★ 发送节流：允许服务端超前实时多少毫秒。它直接决定"
                         "电视端缓冲深度，也就是延迟。默认 800 —— 既够电视起播，"
                         "又不会像原来那样随播放时长无限累积。填 0 关闭节流"))
    ap.add_argument("--audio", default="loopback",
                    help=T("音频来源：loopback（默认，WASAPI 环回捕获系统声音）"
                         " / none / dshow:<设备名>"))
    ap.add_argument("--audio-offset", type=float, default=0.0,
                    help=T("音频相对视频的偏移（秒），音画不同步时微调，默认 0"))
    ap.add_argument("--keep-local-audio", action="store_true",
                    help=T("投屏时保留电脑本地声音；默认会把电脑静音，"
                         "让声音只从电视出（避免两边同时响）"))
    ap.add_argument("--unmute", action="store_true",
                    help=T("只解除本机静音后退出（程序被强制结束后电脑没声时用）"))
    ap.add_argument("--buffer", type=int, default=384,
                    help=T("流缓冲上限（KB），越小延迟越低但越易卡顿，默认 384"))
    ap.add_argument("--no-push", action="store_true",
                    help=T("只启动流服务，不推给电视（本机自测）"))
    ap.add_argument("--lang", choices=["zh", "en"], default=None,
                    help=T("界面语言：zh = 中文 / en = English"))
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap.parse_args(argv)


def cfg_from_args(ns):
    """argparse 结果 → CastConfig（字段名一致，直接搬即可）。"""
    cfg = CastConfig()
    for k, v in vars(ns).items():
        setattr(cfg, k, v)
    return cfg


def run_cast(args, stop_event=None, ready_cb=None):
    """执行一次投屏，阻塞到用户停止或出错。

    args       : CastConfig。字段名与命令行参数一致，故此处仍叫 args。
    stop_event : threading.Event。被 set 后尽快收尾退出（GUI 的「停止」）。
                 命令行模式下为 None，靠 KeyboardInterrupt。
    ready_cb   : 管道就绪并推送成功后回调一次，参数是 info dict
                 （本机 IP / 端口 / 流地址 / 电视端播放页地址 / 设备名 /
                  编码器 / 采集后端）。GUI 用它把地址显示出来。

    返回进程退出码：0 正常结束。
    """
    global OSD_ENABLED, OSD_FONT, OSD_SIZE, OSD_MARGIN, PACE_LEAD_MS
    global _STOP_EVENT
    _STOP_EVENT = stop_event

    # ---------- --low-latency 一键预设（流畅优先）----------
    # 实测依据：
    #   · 250 ms 关键帧会把码率从 3576 推到 4617 kbps（+29%），这些码率全花在
    #     变密的 I 帧上，对画质无益，反而增加电视端解码抖动 → 放宽到 400 ms。
    #   · 同参数下 libx264 输出 29.7 fps，h264_mf 只有 28.0 fps（ld_vbr 会丢帧），
    #     且 mf 只能输出 Constrained Baseline，压缩效率低于 x264 的 Main
    #     → 流畅优先时改用 x264。
    ll_forced_encoder = False
    if args.low_latency:
        args.latency_ms = min(args.latency_ms, 250)
        args.gop_ms = min(args.gop_ms, 400)
        if args.fps < 30:
            args.fps = 30
        if args.encoder == "auto":
            args.encoder = "x264"
            ll_forced_encoder = True

    # Windows 控制台 UTF-8
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    # ---------- --unmute：仅解除本机静音 ----------
    # 用于程序被 taskkill /F 或任务管理器强制结束、静音来不及恢复的情况。
    if getattr(args, "unmute", False):
        lb = None
        try:
            lb = WasapiLoopback().open(loopback=False)
            ok = lb.set_local_mute(False)
            if ok:
                log("已解除本机静音，电脑声音恢复正常。")
                return 0
            err("解除失败：当前播放设备不支持音量控制接口。")
            return 1
        except Exception as e:
            err("解除失败：%s", e)
            return 1
        finally:
            if lb is not None:
                try:
                    lb.close()
                except Exception:
                    pass

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        err("未找到 ffmpeg。请安装后设置环境变量 FFMPEG_PATH，"
            "或放到 D:\\ffmpeg\\ffmpeg.exe")
        return 2

    # ---------- 发现 ----------
    local_ip = get_local_ip()
    src_size = get_desktop_size()
    log("=" * 58)
    log(" DLNA 屏幕投屏器")
    log("=" * 58)
    log("本机 IP   : %s", local_ip)
    log("ffmpeg    : %s", ffmpeg)
    log("桌面分辨率: %s", ("%d × %d" % src_size) if src_size else "未知（按档位原样输出）")
    log("")

    device = None

    if args.no_push:
        log("自测模式（--no-push）：不推送设备")
    elif args.ip:
        log("正在解析指定设备 %s ...", args.ip)
        loc = "http://%s:49152/description.xml" % args.ip
        try:
            device = describe_device(loc)
        except Exception:
            device = ssdp_search_unicast(args.ip, local_ip)
        if not device:
            err("无法获取 %s 的设备描述", args.ip)
            return 3
    else:
        log("正在扫描局域网内的 DLNA 渲染设备（约 %.0f 秒）...",
            args.scan_timeout)
        while True:
            devices = discover_devices(local_ip, args.scan_timeout)
            if args.list:
                if not devices:
                    log("未发现设备。")
                    for line in network_diagnosis():
                        log("%s", line)
                    return 1
                for d in devices:
                    log("  %s | %s | 服务: %s",
                        d.name, d.ip, ", ".join(sorted(d.services.keys())))
                return 0
            if not devices:
                log("未发现任何 DLNA 渲染设备。")
                for line in network_diagnosis():
                    log("%s", line)
                return 1
            if args.number:
                if 1 <= args.number <= len(devices):
                    device = devices[args.number - 1]
                else:
                    err("序号超出范围")
                    return 1
            else:
                device = choose_device(devices)
            if device == "RESCAN":
                continue
            break

    if device:
        log("")
        log("目标设备  : %s (%s)", device.name, device.ip)
        log("可用服务  : %s", ", ".join(sorted(device.services.keys())))
        if "AVTransport" not in device.services:
            err("该设备不支持 AVTransport，无法作为播放目标。")
            return 4

    # 扫描/解析期间用户可能已经点了停止 —— 此时还没占用任何资源，直接退。
    if stop_requested():
        log("已取消。")
        return 0

    # ---------- 画质 ----------
    quality = args.quality
    if not args.list and not args.no_push and not args.number and not args.ip:
        quality = choose_quality(src_size)

    target_h, downgraded, src_h = resolve_video_geometry(
        quality, src_size, args.allow_upscale)
    log("画质      : %s", QUALITY_LABELS.get(quality, quality))
    if downgraded:
        log("            本机屏幕 %dp 低于档位高度，已自动降级为 %dp —— "
            "放大不会变清晰，只会多花码率和 CPU", src_h, target_h)
        log("            想要更高画质请改用 --quality 1080hq（同分辨率提高码率）")
    else:
        log("            输出高度 %dp", target_h)

    # ---------- 编码器 / 采集后端 ----------
    encoder = resolve_encoder(ffmpeg, args.encoder)
    capture = resolve_capture(ffmpeg, args.capture)
    if capture == "ddagrab":
        # ddagrab 的 output_idx=0 只覆盖主显示器，不像 gdigrab 会抓虚拟桌面并集
        _p = get_desktop_size(primary=True)
        if _p and src_size and _p != src_size:
            log("            注意：ddagrab 只抓主显示器 %d×%d，而 gdigrab 会覆盖"
                "整个虚拟桌面 %d×%d；多屏需要拼接请用 --capture gdigrab",
                _p[0], _p[1], src_size[0], src_size[1])
        if _p:
            src_size = _p
        if encoder == "mf":
            log("            h264_mf 配 ddagrab 会让 ffmpeg 异常退出（实测段错误），"
                "已自动改用 x264；若要保留硬件编码请用 --capture gdigrab")
            encoder = "x264"
    if ll_forced_encoder:
        log("            （--low-latency 已自动选用 x264：帧率更稳、画质更实；"
            "想省 CPU 可显式加 --encoder mf）")
    log("延迟参数  : 本机缓冲 %d ms | 关键帧 %d ms | %d fps",
        args.latency_ms, args.gop_ms, args.fps)

    # ---------- 音频来源 ----------
    audio_mode = "none"
    audio_dev = None
    audio_fmt = None
    loopback = None
    a = (args.audio or "none").strip()
    al = a.lower()
    if al == "loopback":
        if not _WIN:
            log("音频      : 当前平台不支持 WASAPI 环回，已关闭")
        else:
            try:
                loopback = WasapiLoopback().open()
                audio_fmt = (loopback.sample_fmt, loopback.rate, loopback.channels)
                audio_mode = "pipe"
                log("音频      : WASAPI 环回已就绪（%d 声道 / %d Hz / %s）",
                    loopback.channels, loopback.rate, loopback.sample_fmt)
            except Exception as e:
                log("音频      : 环回初始化失败，降级为无声 —— %s", e)
    elif al == "none":
        log("音频      : 已关闭")
    else:
        audio_dev = a.split(":", 1)[1] if al.startswith("dshow:") else a
        audio_mode = "dshow"
        log("音频      : dshow 设备 %s", audio_dev)

    # 是否把声音"转移"到电视（投屏时静音本机，避免电脑与电视同时出声）。
    # 仅在确实把系统声音送进流里时才静音；用户可用 --keep-local-audio 关闭。
    mute_requested = (audio_mode == "pipe" and loopback is not None
                      and not getattr(args, "keep_local_audio", False))

    # ---------- 准备端口 ----------
    # 首选端口可能被 Windows 的保留区间吞掉（WinError 10013）。start_http_server
    # 会自动回退，所以先绑定拿到「实际端口」，再按实际端口放行防火墙。
    http_srv = start_http_server(args.port)
    args.port = HTTP_PORT      # 回退后同步实际端口，后面所有 URL / 回调自动跟随
    ensure_firewall(args.port)
    log("")
    log("HTTP 流服务已启动：0.0.0.0:%d", args.port)

    hls_dir = None
    if args.mode == "hls":
        hls_dir = tempfile.mkdtemp(prefix="screencast_")
        globals()["HLS_DIR"] = hls_dir

    if args.mode == "mpegts":
        stream_path = "/live.ts"
        protocol_info = "http-get:*:video/mp2t:*"
    else:
        stream_path = "/live.m3u8"
        protocol_info = "http-get:*:video/m3u8:*"

    stream_url = "http://%s:%d%s" % (local_ip, args.port, stream_path)
    log("投流地址  : %s", stream_url)

    if args.mode == "mpegts":
        log("")
        log("★ 电视端低延迟播放页：http://%s:%d/tv", local_ip, args.port)
        log("  在电视的浏览器里打开这个地址，点「开始播放」。")
        log("  它不使用电视自带的 DLNA 播放器，缓冲深度由本页控制，")
        log("  可以把端到端延迟从几秒压到 1 秒以内。")

    if args.clock:
        try:
            cu = "http://127.0.0.1:%d/clock" % args.port
            webbrowser.open(cu)
            log("诊断时钟页: %s", cu)
            log("            浏览器按 F11 全屏。电视投上后：")
            log("              · 数字连续均匀地跳、只是落后  → 延迟（画面不乱）")
            log("              · 数字卡住不动、然后猛地跳一段 → 卡顿（链路丢帧）")
            log("              · 红条匀速滑动 → 好；一顿一顿 → 卡顿")
            log("            量延迟：手机拍一张电脑与电视同框照，两个时间戳相减")
            log("            隔 30 秒再拍一张：数值变大 = 缓冲在持续堆积（有问题）")
        except Exception:
            pass

    # ---------- 画面 OSD 与发送节流配置 ----------
    PACE_LEAD_MS = max(0, int(args.lead_ms))
    OSD_ENABLED = not args.no_osd
    atexit.register(remove_osd_temp_files)
    _th, _dg, _sh = resolve_video_geometry(quality, src_size,
                                           args.allow_upscale)
    _out_h = _th or _sh or 1080
    OSD_SIZE = (args.osd_size if args.osd_size > 0
                else max(12, int(round(_out_h * 0.018))))
    OSD_MARGIN = max(10, int(round(_out_h * 0.012)))
    if OSD_ENABLED:
        OSD_FONT = find_osd_font()
        if OSD_FONT is None:
            OSD_ENABLED = False
            log("画面 OSD  : 未找到可用字体（msyh.ttc / simhei.ttf），已自动关闭")
        else:
            # drawtext 在启动时要求 textfile 已存在，否则 ffmpeg 直接报错退出。
            # 这里先规整成标准定长文件（清掉旧版本残留的换行/长尾巴），
            # 且尺寸从第一帧起就恒定 —— 尺寸变化或变 0 都会让 ffmpeg 挂掉
            if not osd_reset_file():
                OSD_ENABLED = False
                log("画面 OSD  : 无法写入 %s，已自动关闭", OSD_TEXT_FILE)
    if OSD_ENABLED:
        log("画面 OSD  : 右上角 %dpx 灰白小字（帧率 / 码率 / 延迟）", OSD_SIZE)
    if PACE_LEAD_MS > 0:
        log("发送节流  : 超前上限 %d ms（抑制电视端缓冲累积）", PACE_LEAD_MS)
    else:
        log("发送节流  : 已关闭 —— 注意延迟会随播放时长累积")

    proc, bc, encoder, capture, feeder = start_pipeline(
        ffmpeg, args, quality, hls_dir, audio_mode, audio_dev, audio_fmt,
        encoder, src_size, loopback, capture)
    if proc is None:
        err("")
        err("编码管道启动失败：所有可用编码器都没能产出数据。")
        err("可先用这条命令单测采集与编码是否正常：")
        err("  %s -f gdigrab -framerate 30 -i desktop -t 3 "
            "-c:v libx264 -preset ultrafast -f null -", ffmpeg)
        return 5
    if args.mode == "mpegts":
        log("编码已输出 %.1f KB（编码器 %s / 采集 %s）",
            bc.total_bytes / 1024.0, encoder, capture)
    else:
        log("HLS 播放列表已生成（编码器 %s / 采集 %s）", encoder, capture)

    # ---------- 启动画面 OSD 指标采集 ----------
    # dev 先留空：等推送成功拿到设备对象后才有办法查它的播放位置，
    # 在那之前延迟一栏显示 "--"，不会伪造数字。
    osd = None
    if OSD_ENABLED and bc is not None:
        osd = OsdUpdater(bc, dev=None, interval=0.25)
        osd.start()

    # ---------- 把声音转移到电视（静音本机）----------
    if mute_requested:
        prev = loopback.mute_local()
        if prev is None:
            log("本地声音  : 静音失败（端点不支持），电脑与电视会同时出声")
        else:
            log("本地声音  : 已静音电脑（原状态 %s），声音只从电视出",
                "静音" if prev == 1 else "未静音")
            log("            退出投屏时会自动恢复原状态")
            # 兜底：非正常退出（异常/被信号中断）也尽量恢复本机声音
            def _restore_on_exit(_lb=loopback):
                try:
                    _lb.restore_local()
                except Exception:
                    pass
            atexit.register(_restore_on_exit)
            # 兜底 2：直接关闭控制台窗口时恢复（此时收不到 KeyboardInterrupt）
            guard = install_console_close_guard(loopback)
            if guard is None:
                log("            提示：请用 Ctrl+C 退出，直接关窗口可能导致电脑没声")

    # ---------- 推送到电视 ----------
    # 若这期间用户已点了停止，就不要再推了：跳过推送直接落到下面的收尾段，
    # 由 finally 统一关服务、杀 ffmpeg、恢复本机声音。
    if stop_requested():
        log("已取消，正在收尾 ...")
    elif not args.no_push:
        log("")
        log("正在把画面推送到电视 ...")
        try:
            push_to_tv(device, stream_url, protocol_info)
            if osd is not None:
                # 拿到设备对象后，OSD 才能查到电视的播放位置（GetPositionInfo）
                osd.dev = device
            if args.mode == "mpegts":
                confirm_playback(device, stream_url, protocol_info,
                                 wait_s=args.push_wait,
                                 retries=args.push_retry)
            log("")
            log("=" * 58)
            log(" 投屏已开始：%s", device.name)
            log(" 电视端如无画面，请确认电视已切到对应的投屏/媒体播放界面")
            log(" 按 Ctrl+C 停止投屏")
            log("=" * 58)
            if ready_cb is not None:
                try:
                    ready_cb({
                        "local_ip": local_ip,
                        "port": args.port,
                        "stream_url": stream_url,
                        "tv_url": ("http://%s:%d/tv" % (local_ip, args.port)
                                   if args.mode == "mpegts" else None),
                        "device": device.name,
                        "device_ip": device.ip,
                        "encoder": encoder,
                        "capture": capture,
                    })
                except Exception:
                    pass
        except Exception as e:
            err("")
            err("推送失败：%s", e)
            err("")
            err("可尝试：")
            if args.mode == "mpegts":
                err("  · 换用 HLS 模式： --mode hls")
            err("  · 确认电视的 DLNA/多屏互动功能已开启")
            # 此处直接 return 会绕过下方 finally，必须先把本机声音还原
            if mute_requested and loopback is not None:
                try:
                    if loopback.restore_local():
                        err("  · 已恢复电脑本地声音")
                except Exception:
                    pass
            proc.terminate()
            return 6
    else:
        log("")
        log("自测模式：用下面命令在本机验证流是否正常：")
        log("  ffplay \"%s\"", stream_url.replace(local_ip, "127.0.0.1"))
        log("按 Ctrl+C 停止")

    # ---------- 保持运行 ----------
    # 三种退出方式都要能走到下面同一段收尾逻辑：正常停止、ffmpeg 自己挂了、
    # Ctrl+C。GUI 的「停止」按钮走 stop_event，轮询间隔 0.25 秒保证点下去
    # 立刻有反应（原来 1 秒一次的 sleep 会让按钮显得迟钝）。
    try:
        while True:
            time.sleep(0.25)
            if stop_requested():
                log("")
                log("正在停止 ...")
                break
            if proc is None or proc.poll() is not None:
                err("")
                err("ffmpeg 已退出（返回码 %s）",
                    None if proc is None else proc.returncode)
                if proc is not None:
                    explain_ffmpeg_exit(proc.returncode)
                dump_ffmpeg_log()
                err("")
                err("投屏已中断，上面是 ffmpeg 自己给出的原因。")
                break
    except (KeyboardInterrupt, CastCancelled):
        log("")
        log("正在停止 ...")
    finally:
        try:
            if device and not args.no_push:
                stop_tv(device)
        except Exception:
            pass
        if feeder:
            feeder.stop()
        try:
            if loopback:
                loopback.close()
        except Exception:
            pass
        try:
            if proc is not None:
                proc.terminate()
                proc.wait(timeout=5)
        except Exception:
            try:
                if proc is not None:
                    proc.kill()
            except Exception:
                pass
        try:
            http_srv.shutdown()
        except Exception:
            pass
        if hls_dir and os.path.isdir(hls_dir):
            shutil.rmtree(hls_dir, ignore_errors=True)
        if osd is not None:
            osd.stop()
        remove_osd_temp_files()
        log("已停止。")

    return 0


# ---------------------------------------------------------------- 图形界面用接口


def scan_devices(timeout=4.0, log_fn=None):
    """扫描局域网内的 DLNA 渲染设备，返回 Device 列表。

    独立于 run_cast 之外，供 GUI 的「扫描设备」按钮在后台线程里调用，
    避免把设备列表跟一次投屏会话绑定在一起。
    """
    local_ip = get_local_ip()
    if log_fn:
        log_fn(T("本机 IP %s，开始扫描（约 %.0f 秒）...") % (local_ip, timeout))
    devices = discover_devices(local_ip, timeout)
    if log_fn:
        log_fn(T("发现 %d 个可投屏设备") % len(devices))
    return devices


def ffmpeg_status():
    """返回 (路径, 中文来源说明)，找不到时路径为 None。

    界面自检用。真正的「找不到就下载」在 ffmpeg_fetch.ensure_ffmpeg()，
    这里只管查本机已有的（顺序与 find_ffmpeg 一致，只有一处定义）。
    """
    p = find_ffmpeg()
    if not p:
        return None, "未找到 ffmpeg"
    if os.environ.get("FFMPEG_PATH") == p:
        return p, "来自环境变量 FFMPEG_PATH"
    if os.path.dirname(os.path.abspath(p)) == os.path.abspath(APP_DIR):
        return p, "来自程序同目录"
    if os.path.dirname(os.path.dirname(os.path.abspath(p))) == \
            os.path.abspath(CACHE_DIR):
        return p, "来自缓存目录"
    return p, "来自本机已有安装"


def main(argv=None):
    """命令行入口（行为与改造前完全一致）。

    注意 build_parser() 返回的已经是 Namespace（它内部就 parse 过了），
    这里不要再对它调 parse_args —— 否则 CLI 直接 AttributeError。
    """
    # 语言必须在建 parser 之前定下来：help 文案是构造时求值的。
    i18n.set_lang(i18n.resolve(argv))
    return run_cast(cfg_from_args(build_parser(argv)))


if __name__ == "__main__":
    sys.exit(main())
