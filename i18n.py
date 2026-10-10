# -*- coding: utf-8 -*-
"""界面文案国际化：中文 / 英文两套。

设计要点
--------
1. **key 就是中文原文**，不另起一套键名。好处是源码里 ``T("扫描设备")``
   一眼能看懂，漏译只会回落到中文，绝不会冒出 ``ui.scan`` 这种鬼东西。

2. **只在「输出口」翻译**，不在业务逻辑里翻译。也就是说：
     * ``screen-cast.log() / err()``     —— 所有日志的必经之路
     * GUI 的 ``log_line() / set_status()`` 与控件构造处
   而像 ``desc.startswith("本机已有")`` 这类**依赖文案做判断**的地方，
   文案保持中文不变（它是内部契约，不是给人看的），显示时再翻。

3. ``tr()`` 幂等：英文结果查不到表，原样返回。所以同一段文本被翻两次
   也不会出事 —— 这让 "GUI 与核心模块各翻一道" 成为安全做法。

语言从哪来（优先级从高到低）
----------------------------
    ``--lang en``  /  ``--lang=en``
    ``DLNACAST_LANG`` 环境变量
    构建期常量 ``_lang_build.DEFAULT``（英文版 exe 就是靠它定死英文）
    ``zh``
"""

import os
import re
import sys

ZH = "zh"
EN = "en"

# 构建期常量：build_exe.py --lang en 会生成 _lang_build.py，随包打进去。
# 开发环境下这个文件不存在，于是回落到中文 —— 开发体验不变。
try:
    from _lang_build import DEFAULT as _BUILD_DEFAULT   # type: ignore
except Exception:
    _BUILD_DEFAULT = None

LANG = ZH


# ------------------------------------------------------------------ 语言解析

def resolve(argv=None):
    """按优先级推断语言，但不改变全局状态（纯函数）。"""
    argv = list(sys.argv[1:] if argv is None else argv)
    # --lang en / --lang=en
    for i, a in enumerate(argv):
        if a == "--lang" and i + 1 < len(argv):
            v = argv[i + 1].strip().lower()
            if v:
                return _norm(v)
        if a.startswith("--lang="):
            v = a.split("=", 1)[1].strip().lower()
            if v:
                return _norm(v)
    env = (os.environ.get("DLNACAST_LANG") or "").strip().lower()
    if env:
        return _norm(env)
    if _BUILD_DEFAULT:
        return _norm(_BUILD_DEFAULT)
    return ZH


def _norm(code):
    """把各种写法归一到 zh / en。"""
    code = code.strip().lower()
    if code.startswith(("zh", "cn", "chinese", "chi")):
        return ZH
    if code.startswith(("en", "english", "eng")):
        return EN
    return ZH


def set_lang(code):
    """显式设置当前语言，返回设置后的值。"""
    global LANG
    LANG = _norm(code)
    return LANG


def current():
    return LANG


def is_en():
    return LANG == EN


def tr(s):
    """翻译一段文本。查不到就原样返回（中文版因此完全不受影响）。"""
    # 非字符串（数字、异常对象等）直接放行 —— 调用方常在一条格式化语句里
    # 顺手把参数也丢进来翻，不该在这里炸掉。
    if not isinstance(s, str):
        return s
    if LANG != EN or not s:
        return s
    hit = _T.get(s)
    if hit is not None:
        return hit
    # 带动态尾巴的来源说明，例如 "本机已有：ffmpeg version 9.0"。
    # 尾巴若正好是一整条已知文案（如 "下载失败：已取消"），一并翻掉。
    for zh, en in _PREFIX:
        if s.startswith(zh):
            tail = s[len(zh):]
            hit = _T.get(tail)
            return en + (hit if hit is not None else tail)
    return s


# 短名，界面代码里到处用，越短越好读
T = tr


def tr_desc(s):
    """来源说明专用（含动态内容的走前缀替换）。"""
    return tr(s)


# ------------------------------------------------------------------ 时钟页
# 这个页面整段是内嵌在源码里的 HTML，逐条套 T() 会把模板搅得没法读，
# 所以保留中文模板，输出前做一次短语替换。表里只放页面里出现的中文。

_CLOCK_REPLACEMENTS = (
    ("<title>延迟 / 卡顿自测</title>",
     "<title>Latency / Stutter self-test</title>"),
    ("本机渲染帧", "Rendered frames"),
    ("卡顿次数", "Stutters"),
    ("最长一帧", "Longest frame"),
    ("盯着<em>数字</em>看：连续均匀地跳 = <em>延迟</em>（只是慢，不乱）",
     "Watch the <em>digits</em>: ticking evenly = <em>latency</em> "
     "(just behind, not broken)"),
    ("｜ 卡住不动再猛跳 = <u>卡顿</u><br>",
     " | frozen then jumping = <u>stutter</u><br>"),
    ("盯着<u>红条</u>看：匀速滑动 = 好 ｜ 一顿一顿 = 卡顿",
     "Watch the <u>red bar</u>: steady = good | jerky = stuttering"),
)


def localize_html(html):
    """把时钟页里的中文换成英文（仅英文模式生效）。"""
    if LANG != EN:
        return html
    for zh, en in _CLOCK_REPLACEMENTS:
        html = html.replace(zh, en)
    return html


# ------------------------------------------------------------------ 翻译表
#
# 组织方式与提取清单一致：先 GUI，再核心模块（画质标签 → 诊断建议 → 日志
# → 命令行帮助），最后 ffmpeg 获取。前导空格一律保留 —— 它们是排版的一部分。

_T = {

    # ============================================================= GUI 文案
    "DLNA 屏幕投屏器": "DLNA Screen Caster",
    "正在检查运行环境 ...": "Checking environment ...",
    " 投屏目标 ": " Target device ",
    "电视设备": "TV device",
    "（尚未扫描）": "(not scanned yet)",
    "扫描设备": "Scan",
    "手动指定": "Manual",
    "  电视 IP（填了就跳过扫描，如 192.168.1.100）":
        "  TV IP (skips scanning once filled, e.g. 192.168.1.100)",
    " 画质与性能 ": " Quality & performance ",
    "画质档位": "Quality",
    "   帧率": "   FPS",
    "视频编码": "Video codec",
    "   采集": "   Capture",
    "▸  高级设置（一般不用改）": u"▸  Advanced (rarely needed)",
    "▾  高级设置（一般不用改）": u"▾  Advanced (rarely needed)",
    " 高级设置 ": " Advanced ",
    "HTTP 端口": "HTTP port",
    "本机缓冲 ms": "Local buffer ms",
    "关键帧间隔 ms": "Keyframe ms",
    "发送节流 ms": "Pacing ms",
    "手动码率 kbps": "Bitrate kbps",
    "(手动码率 0 = 用档位默认值)": "(bitrate 0 = use preset default)",
    "推流模式": "Mode",
    "   声音来源": "   Audio source",
    "在电视画面右上角显示帧率 / 码率 / 延迟":
        "Show FPS / bitrate / latency on the TV picture",
    "流畅优先预设（软编 + 更小缓冲，帧率最稳）":
        "Smoothness preset (x264 + smaller buffer, steadiest FPS)",
    "允许把画面放大到档位分辨率（通常没必要）":
        "Allow upscaling to the preset resolution (usually unnecessary)",
    "同时打开延迟诊断时钟页（用于量端到端延迟）":
        "Also open the latency clock page (for end-to-end measuring)",
    "仅在本机自测，不推送到电视": "Local test only, do not push to the TV",
    "提示：电视端卡顿时，把「手动码率」调到比实测交付速率低 15% 左右即可。":
        "Tip: if the TV stutters, set the manual bitrate about 15% below "
        "the measured delivery rate.",
    u"▶  开始投屏": u"▶  Start casting",
    u"■  停止": u"■  Stop",
    u"■  取消下载": u"■  Cancel download",
    "就绪": "Ready",
    "电视端低延迟播放页：开始投屏后这里会显示地址（在电视浏览器里打开，可把延迟从几秒压到 1 秒内）":
        "Low-latency player page: the address appears here once casting "
        "starts (open it in the TV browser to cut latency to under 1 s)",
    "复制地址": "Copy address",
    " 运行日志 ": " Log ",
    "界面刷新出错：%s": "UI refresh error: %s",
    "（未发现设备）": "(no device found)",
    "未发现 DLNA 设备。请确认电视与电脑在同一局域网，且电视已开启「多屏互动 / DLNA / 媒体共享」。":
        "No DLNA device found. Make sure the TV and this PC are on the same "
        "LAN and the TV has screen-mirroring / DLNA / media sharing enabled.",
    "正在扫描设备 ...": "Scanning ...",
    "扫描失败：%s": "Scan failed: %s",
    u"「%s」要填数字，当前是「%s」":
        u'"%s" must be a number; got "%s"',
    "还没选电视。点「扫描设备」，或在上面的「手动指定」里填电视 IP。":
        'No TV selected yet. Click "Scan", or type the TV IP under "Manual".',
    "参数需要调整": "Adjust the settings",
    "正在启动 ...": "Starting ...",
    "发生异常：%s": "Exception: %s",
    "正在取消下载 ...": "Cancelling download ...",
    "已请求取消下载。已下好的分段会留着，下次接着下。":
        "Download cancelled. Completed segments are kept and reused next time.",
    "正在停止 ...": "Stopping ...",
    "收到停止指令，正在收尾（会恢复电脑声音）...":
        "Stop requested, wrapping up (local audio will be restored) ...",
    "电视端低延迟播放页：%s   （在电视浏览器里打开，点「开始播放」）":
        "Low-latency player page: %s   (open in the TV browser, tap Play)",
    u"投屏中 · %s": u"Casting · %s",
    "已停止": "Stopped",
    "已结束（退出码 %s）": "Finished (exit code %s)",
    "地址已复制到剪贴板": "Address copied to clipboard",
    "正在投屏": "Casting in progress",
    "投屏还在进行。确定要停止并退出吗？\n\n（会先停止投屏、恢复电脑本地声音，再关闭窗口）":
        "Casting is still running. Stop it and quit?\n\n"
        "(Casting will be stopped and local audio restored before closing.)",
    "正在停止，请稍候 ...": "Stopping, please wait ...",
    "正在检查 ffmpeg 运行环境 ...": "Checking the ffmpeg runtime ...",
    "环境自检失败：%s": "Environment check failed: %s",
    "ffmpeg 就绪：%s": "ffmpeg ready: %s",
    "没有可用的 ffmpeg —— 投屏会失败。":
        "No usable ffmpeg found - casting will fail.",
    "可以这样解决：① 装一份 ffmpeg 并把它的 bin 目录加进 PATH；② 设环境变量 FFMPEG_PATH 指向 ffmpeg.exe；③ 直接把 ffmpeg.exe 放到本程序旁边（%s）":
        "How to fix: (1) install ffmpeg and add its bin folder to PATH; "
        "(2) set FFMPEG_PATH to point at ffmpeg.exe; "
        "(3) simply drop ffmpeg.exe next to this program (%s)",
    "未找到 ffmpeg": "ffmpeg not found",
    "使用本机 ffmpeg": "Using the local ffmpeg",
    "已下载 ffmpeg": "ffmpeg downloaded",
    "本机 %s   ·   桌面 %s   ·   端口 %d   ·   %s":
        "Local %s   ·   Desktop %s   ·   Port %d   ·   %s",
    "未知": "unknown",
    "端口 %d 不可用，将自动改用 %d":
        "Port %d is unavailable; switching to %d",
    "本机 IP %s / 桌面 %s / 端口 %d": "Local IP %s / desktop %s / port %d",
    u"★ 检测到代理 TUN 虚拟网卡「%s」在运行。若扫描不到电视，请把局域网加入代理绕过，或临时关闭 TUN 模式。":
        u'★ Proxy TUN adapter "%s" detected. If no TV is found, add your LAN to '
        u"the proxy bypass list, or turn TUN mode off temporarily.",
    "就绪 —— 先扫描设备，再点「开始投屏」":
        u'Ready - scan for devices, then click "Start casting"',
    "缺少 ffmpeg，无法投屏": "ffmpeg missing, cannot cast",
    "环境自检出错：%s": "Environment check error: %s",
    "自检失败": "Check failed",
    "DLNA 屏幕投屏器已启动。": "DLNA Screen Caster started.",
    "正在查找 ffmpeg：先看环境变量 FFMPEG_PATH，再看本程序目录，都没有才联网下载（约 36 MB，只下这一次）。":
        "Looking for ffmpeg: first FFMPEG_PATH, then this program's folder; "
        "only if neither works will it download (~36 MB, one time only).",
    "正在下载 ffmpeg  %.1f / %.1f MB（%.0f%%，%.2f MB/s，剩余约 %s）":
        "Downloading ffmpeg  %.1f / %.1f MB (%.0f%%, %.2f MB/s, about %s left)",
    "DLNA 投屏器 出错": "DLNA Screen Caster - error",
    "找不到投屏核心文件：%s": "Core file not found: %s",
    "%d 秒": "%d s",
    "%d 分 %02d 秒": "%d min %02d s",

    # GUI 下拉选项
    "自动（推荐：优先硬件，失败自动退软编）":
        "Auto (recommended: prefer hardware, fall back to x264)",
    "硬件编码 h264_mf（几乎不占 CPU）":
        "Hardware h264_mf (almost no CPU load)",
    "软件编码 x264（帧率最稳、画质最实）":
        "Software x264 (steadiest FPS, most faithful)",
    "自动（推荐）": "Auto (recommended)",
    "GDI 抓屏 gdigrab（兼容性最好）": "GDI capture gdigrab (most compatible)",
    "桌面复制 ddagrab（CPU 占用低、帧率上限高）":
        "Desktop Duplication ddagrab (low CPU, higher FPS ceiling)",
    "跟随系统声音（推荐，声音从电视出）":
        "Follow system audio (recommended, sound from the TV)",
    "不要声音": "No audio",
    "MPEG-TS 直播（推荐，延迟低）": "MPEG-TS live (recommended, low latency)",
    "HLS 分片（兼容优先，延迟高）": "HLS segments (compatibility first, higher latency)",

    # ========================================================= 画质档位标签
    "540p   流畅（弱网/弱 CPU）": "540p   Smooth (weak network / slow CPU)",
    "720p   标清（默认）": "720p   Standard (default)",
    "1080p  高清": "1080p  HD",
    "1080p  高画质（推荐，已按电视端带宽上限调优）":
        "1080p  High (recommended, tuned to the TV's bandwidth ceiling)",
    "2K     1440p（需屏幕本身 ≥1440p 才有效）":
        "2K     1440p (needs a >=1440p screen)",
    "4K     2160p（需 4K 屏幕；软编需高性能 CPU）":
        "4K     2160p (needs a 4K screen; x264 needs a fast CPU)",

    # ========================================================= 诊断与建议
    "排查建议：": "Things to check:",
    "  1) 确认电视与电脑在同一个局域网（同一路由器、同一网段）":
        "  1) Make sure the TV and this PC share one LAN (same router, same subnet)",
    "  2) 电视要打开「多屏互动 / DLNA / 媒体共享」这类开关":
        "  2) Enable screen-mirroring / DLNA / media sharing on the TV",
    "  3) 也可以直接在「电视 IP」框里填电视的 IP，填了就跳过扫描":
        u'  3) Or just type the TV IP into the "TV IP" box to skip scanning',
    "你的局域网段": "your LAN subnet",
    u"★ 检测到代理/VPN 的 TUN 虚拟网卡「%s」正在运行。":
        u'★ Proxy/VPN TUN adapter "%s" is active.',
    "  TUN 模式会抢走默认路由，DLNA 的组播发现会被它吃掉，所以扫不到电视。":
        "  TUN mode takes over the default route and swallows DLNA multicast "
        "discovery, so no TV can be found.",
    "  三种解法任选其一：": "  Pick any one of these:",
    "   ① 把局域网加进代理的绕过列表（推荐，设一次永久生效）：":
        "   (1) Add your LAN to the proxy bypass list (recommended, permanent):",
    "      Clash / Mihomo → tun.route-exclude-address 里加 %s":
        "      Clash / Mihomo -> add %s to tun.route-exclude-address",
    "      其它客户端 → 打开「绕过局域网 / Bypass LAN」开关":
        u'      Other clients -> enable "Bypass LAN"',
    "   ② 临时关掉 TUN 模式（切到「系统代理 / PAC」模式就行，":
        u'   (2) Turn TUN off temporarily (switch to "System proxy / PAC" mode;',
    "      不影响上网，也不影响局域网扫描）":
        "       internet still works and LAN scanning is unaffected)",
    "   ③ 直接把电视 IP 填进上面的输入框（最省事）":
        "   (3) Just type the TV IP into the box above (simplest)",

    # ===================================================== 核心模块：日志
    "[网络] 默认路由被虚拟网卡接管（探路得到 %s），改用物理网卡 %s":
        "[net] Default route is taken over by a virtual adapter (probe got %s); "
        "switching to the physical adapter %s",
    "（组播无响应，已通过网段单播找到 %d 个设备）":
        "(no multicast reply; found %d device(s) via subnet unicast)",
    "（已自动跳过 %d 个不可达的渲染端点）":
        "(%d unreachable renderer endpoint(s) skipped)",
    "  [stream] 起播对齐关键帧：耗时 %.0f ms、前移 %.1f KB —— 客户端从关键帧开始解码，不会花屏":
        "  [stream] aligned to a keyframe at start: %.0f ms, %.1f KB ahead - "
        "the client decodes from a keyframe, so no corruption",
    "  [stream] 电视已接入直播流": "  [stream] TV joined the live stream",
    "  [stream] 电视消费缓慢（单次写 %.0f ms），已追帧丢弃 %.0f KB":
        "  [stream] TV is consuming slowly (single write %.0f ms); "
        "dropped %.0f KB to catch up",
    "  [stream] %.0f kbps | 本机缓冲 %.0f ms | 领取余量 %.0f ms | 闸门等待 %.0f ms | 已丢弃 %.0f KB":
        "  [stream] %.0f kbps | buffer %.0f ms | lead %.0f ms | "
        "gate wait %.0f ms | dropped %.0f KB",
    "  [stream] ★ 链路吞吐不足：本机产出 %.1f Mbps，电视端只能持续收下约 %.1f Mbps，差额 %.0f%% 正在被丢弃（画面会周期性卡顿）":
        "  [stream] ★ Link throughput too low: this PC produces %.1f Mbps but "
        "the TV can only sustain about %.1f Mbps; the %.0f%% gap is being "
        "dropped (the picture will stutter periodically)",
    "             当前档位码率超出这条链路，请改选更低画质档位；或加 --bitrate %d 手动指定":
        "             The current preset exceeds this link. Choose a lower "
        "preset, or pass --bitrate %d",
    "  [stream] 电视断开直播流": "  [stream] TV left the live stream",
    "  [hls] 电视拉取播放列表 %s (%d B)":
        "  [hls] TV fetched the playlist %s (%d B)",
    "  [hls] 电视拉取分片 %s": "  [hls] TV fetched segment %s",
    "被系统保留": "is reserved by the system",
    "已被占用": "is already in use",
    "[端口] 首选 %d %s，已自动改用 %d":
        "[port] Preferred %d %s; switched to %d automatically",
    "没有可用端口（最后错误：%s）":
        "No usable port (last error: %s)",
    "  退出码 %s 的含义：%s": "  Exit code %s means: %s",
    "  （ffmpeg 没有输出任何信息，无更多线索）":
        "  (ffmpeg printed nothing, no further clues)",
    "  —— ffmpeg 最后 %d 行输出 ——":
        "  --- last %d lines of ffmpeg output ---",
    "  -> 已通知电视停止播放": "  -> TV told to stop playback",
    "  -> 停止指令未送达（可忽略）: %s":
        "  -> stop command not delivered (ignorable): %s",
    "发现 1 台设备：%s": "Found 1 device: %s",
    "发现 %d 台可投屏设备：": "Found %d castable device(s):",
    "  [R] 重新扫描": "  [R] Rescan",
    "  [0] 退出": "  [0] Quit",
    "请选择: ": "Select: ",
    "输入无效，请重试。": "Invalid input, please try again.",
    "选择画质：": "Choose quality:",
    "  ← 本机屏幕为 %dp，选它会自动降级为 %dp":
        "  <- local screen is %dp; this will auto-downgrade to %dp",
    "      （本机屏幕 %dp：1080hq 才是真正提高画质的那一档）":
        "      (local screen %dp: 1080hq is the one that really improves quality)",
    "请选择 [1]: ": "Select [1]: ",
    "  警告：当前 ffmpeg 不含 h264_mf，已回退到 libx264 软编":
        "  Warning: this ffmpeg lacks h264_mf; falling back to libx264",
    "编码器    : 自动选择 %s（硬件编码）":
        "Encoder   : auto-selected %s (hardware)",
    "编码器    : 未发现可用硬件编码器，使用 libx264（软编）":
        "Encoder   : no hardware encoder available; using libx264 (software)",
    "采集方式  : gdigrab（GDI 抓屏）": "Capture   : gdigrab (GDI capture)",
    "采集方式  : ddagrab（Desktop Duplication，GPU 抓帧，CPU 占用低）":
        "Capture   : ddagrab (Desktop Duplication, GPU capture, low CPU)",
    "  警告：ddagrab 在本机不可用（需要可用的 D3D11 桌面会话），已回退 gdigrab":
        "  Warning: ddagrab is unavailable here (needs a working D3D11 desktop "
        "session); fell back to gdigrab",
    "  [check] 等待电视接入并正常消费（最长 %.0f 秒）...":
        "  [check] Waiting for the TV to join and consume the stream (up to %.0f s) ...",
    "  [check] 电视正在正常播放，流稳定":
        "  [check] TV is playing normally, stream is stable",
    "电视未接入流": "TV did not join the stream",
    "电视只缓冲不消费": "TV buffers but does not consume",
    "  [check] %s（电视端可能仍在播启动广告），重新推送以清空电视端缓冲 ...（%d/%d）":
        "  [check] %s (the TV may still be showing its start-up ad); "
        "re-pushing to clear the TV buffer ... (%d/%d)",
    "  [check] 重推失败：%s": "  [check] Re-push failed: %s",
    "  [check] 未确认电视端播放状态。流服务仍在运行，可在电视上手动切到投屏/媒体播放界面重试":
        "  [check] TV playback state unconfirmed. The stream server is still "
        "running; switch the TV to its casting/media screen manually and retry",
    "ffmpeg 命令: %s": "ffmpeg command: %s",
    "采集方式  : 已回退到 %s": "Capture   : fell back to %s",
    "编码器 %s + 采集 %s 启动失败%s，尝试回退 ...":
        "Encoder %s + capture %s failed to start%s; retrying with fallbacks ...",
    "（退出码 %s）": "(exit code %s)",
    "已解除本机静音，电脑声音恢复正常。":
        "Local mute released, computer audio is back to normal.",
    "解除失败：当前播放设备不支持音量控制接口。":
        "Unmute failed: the current playback device has no volume-control interface.",
    "解除失败：%s": "Unmute failed: %s",
    "未找到 ffmpeg。请安装后设置环境变量 FFMPEG_PATH，或放到 D:\\ffmpeg\\ffmpeg.exe":
        "ffmpeg not found. Install it and set FFMPEG_PATH, or place it at "
        "D:\\ffmpeg\\ffmpeg.exe",
    " DLNA 屏幕投屏器": " DLNA Screen Caster",
    "本机 IP   : %s": "Local IP  : %s",
    "未知（按档位原样输出）": "unknown (kept at the preset height)",
    "桌面分辨率: %s": "Desktop   : %s",
    "自测模式（--no-push）：不推送设备":
        "Test mode (--no-push): no device will be pushed to",
    "正在解析指定设备 %s ...": "Resolving the specified device %s ...",
    "无法获取 %s 的设备描述": "Cannot fetch the device description for %s",
    "正在扫描局域网内的 DLNA 渲染设备（约 %.0f 秒）...":
        "Scanning the LAN for DLNA renderers (about %.0f s) ...",
    "未发现设备。": "No device found.",
    "  %s | %s | 服务: %s": "  %s | %s | services: %s",
    "未发现任何 DLNA 渲染设备。": "No DLNA renderer found at all.",
    "序号超出范围": "Index out of range",
    "目标设备  : %s (%s)": "Target    : %s (%s)",
    "可用服务  : %s": "Services  : %s",
    "该设备不支持 AVTransport，无法作为播放目标。":
        "This device has no AVTransport and cannot be used as a playback target.",
    "已取消。": "Cancelled.",
    "画质      : %s": "Quality   : %s",
    "            本机屏幕 %dp 低于档位高度，已自动降级为 %dp —— 放大不会变清晰，只会多花码率和 CPU":
        "            The local screen is %dp, below the preset height; "
        "downgraded to %dp - upscaling does not add detail, it only costs "
        "bitrate and CPU",
    "            想要更高画质请改用 --quality 1080hq（同分辨率提高码率）":
        "            For better quality use --quality 1080hq (more bitrate, same resolution)",
    "            输出高度 %dp": "            Output height %dp",
    "            注意：ddagrab 只抓主显示器 %d×%d，而 gdigrab 会覆盖整个虚拟桌面 %d×%d；多屏需要拼接请用 --capture gdigrab":
        "            Note: ddagrab captures only the primary display %dx%d, "
        "while gdigrab covers the whole virtual desktop %dx%d; use "
        "--capture gdigrab if you need multiple monitors stitched",
    "            h264_mf 配 ddagrab 会让 ffmpeg 异常退出（实测段错误），已自动改用 x264；若要保留硬件编码请用 --capture gdigrab":
        "            h264_mf with ddagrab makes ffmpeg crash (segfault, "
        "measured); switched to x264. Keep hardware encoding by using "
        "--capture gdigrab",
    "            （--low-latency 已自动选用 x264：帧率更稳、画质更实；想省 CPU 可显式加 --encoder mf）":
        "            (--low-latency picked x264 automatically: steadier FPS, "
        "more faithful picture; add --encoder mf explicitly to save CPU)",
    "延迟参数  : 本机缓冲 %d ms | 关键帧 %d ms | %d fps":
        "Latency   : buffer %d ms | keyframe %d ms | %d fps",
    "音频      : 当前平台不支持 WASAPI 环回，已关闭":
        "Audio     : this platform has no WASAPI loopback; disabled",
    "音频      : WASAPI 环回已就绪（%d 声道 / %d Hz / %s）":
        "Audio     : WASAPI loopback ready (%d ch / %d Hz / %s)",
    "音频      : 环回初始化失败，降级为无声 —— %s":
        "Audio     : loopback init failed, continuing without sound - %s",
    "音频      : 已关闭": "Audio     : disabled",
    "音频      : dshow 设备 %s": "Audio     : dshow device %s",
    "HTTP 流服务已启动：0.0.0.0:%d":
        "HTTP stream server started on 0.0.0.0:%d",
    "投流地址  : %s": "Stream URL: %s",
    u"★ 电视端低延迟播放页：http://%s:%d/tv":
        u"★ Low-latency player page for the TV: http://%s:%d/tv",
    u"  在电视的浏览器里打开这个地址，点「开始播放」。":
        u'  Open this in the TV browser and tap "Play".',
    "  它不使用电视自带的 DLNA 播放器，缓冲深度由本页控制，":
        "  It bypasses the TV's built-in DLNA player; the buffer depth is",
    "  可以把端到端延迟从几秒压到 1 秒以内。":
        "  controlled by the page, cutting end-to-end latency to under 1 s.",
    "诊断时钟页: %s": "Clock page: %s",
    "            浏览器按 F11 全屏。电视投上后：":
        "            Press F11 for full screen. Once the TV shows it:",
    "              · 数字连续均匀地跳、只是落后  → 延迟（画面不乱）":
        "              - digits tick evenly but are behind  -> latency (picture intact)",
    "              · 数字卡住不动、然后猛地跳一段 → 卡顿（链路丢帧）":
        "              - digits freeze then jump suddenly -> stutter (dropped frames)",
    "              · 红条匀速滑动 → 好；一顿一顿 → 卡顿":
        "              - red bar slides steadily -> good; jerky -> stuttering",
    "            量延迟：手机拍一张电脑与电视同框照，两个时间戳相减":
        "            Measure latency: photograph PC and TV together, subtract the timestamps",
    "            隔 30 秒再拍一张：数值变大 = 缓冲在持续堆积（有问题）":
        "            Shoot again 30 s later: a larger value means the buffer keeps "
        "growing (a problem)",
    "画面 OSD  : 未找到可用字体（msyh.ttc / simhei.ttf），已自动关闭":
        "OSD       : no usable font (msyh.ttc / simhei.ttf) found; disabled",
    "画面 OSD  : 无法写入 %s，已自动关闭":
        "OSD       : cannot write %s; disabled",
    "画面 OSD  : 右上角 %dpx 灰白小字（帧率 / 码率 / 延迟）":
        "OSD       : %dpx grey text in the top-right corner (FPS / bitrate / latency)",
    "发送节流  : 超前上限 %d ms（抑制电视端缓冲累积）":
        "Pacing    : lead capped at %d ms (stops the TV buffer from growing)",
    "发送节流  : 已关闭 —— 注意延迟会随播放时长累积":
        "Pacing    : disabled - latency will accumulate as playback goes on",
    "编码管道启动失败：所有可用编码器都没能产出数据。":
        "Encode pipeline failed to start: none of the available encoders "
        "produced any data.",
    "可先用这条命令单测采集与编码是否正常：":
        "First check capture and encoding with this command:",
    "编码已输出 %.1f KB（编码器 %s / 采集 %s）":
        "Encoder produced %.1f KB (encoder %s / capture %s)",
    "HLS 播放列表已生成（编码器 %s / 采集 %s）":
        "HLS playlist generated (encoder %s / capture %s)",
    "本地声音  : 静音失败（端点不支持），电脑与电视会同时出声":
        "Local audio: mute failed (endpoint unsupported); PC and TV will both play",
    "本地声音  : 已静音电脑（原状态 %s），声音只从电视出":
        "Local audio: PC muted (was %s); sound comes from the TV only",
    "静音": "muted",
    "未静音": "unmuted",
    "            退出投屏时会自动恢复原状态":
        "            The original state is restored when casting stops",
    "            提示：请用 Ctrl+C 退出，直接关窗口可能导致电脑没声":
        "            Tip: exit with Ctrl+C; closing the window may leave the PC muted",
    "已取消，正在收尾 ...": "Cancelled, wrapping up ...",
    "正在把画面推送到电视 ...": "Pushing the picture to the TV ...",
    " 投屏已开始：%s": " Casting started: %s",
    " 电视端如无画面，请确认电视已切到对应的投屏/媒体播放界面":
        " If the TV shows nothing, make sure it is on its casting/media screen",
    " 按 Ctrl+C 停止投屏": " Press Ctrl+C to stop casting",
    "推送失败：%s": "Push failed: %s",
    "可尝试：": "Things to try:",
    "  · 换用 HLS 模式： --mode hls": "  - Switch to HLS mode: --mode hls",
    "  · 确认电视的 DLNA/多屏互动功能已开启":
        "  - Make sure DLNA / screen mirroring is enabled on the TV",
    "  · 已恢复电脑本地声音": "  - Local audio has been restored",
    "自测模式：用下面命令在本机验证流是否正常：":
        "Test mode: verify the stream locally with:",
    "按 Ctrl+C 停止": "Press Ctrl+C to stop",
    "ffmpeg 已退出（返回码 %s）": "ffmpeg exited (return code %s)",
    "投屏已中断，上面是 ffmpeg 自己给出的原因。":
        "Casting aborted; the reason above comes from ffmpeg itself.",
    "已停止。": "Stopped.",
    "本机 IP %s，开始扫描（约 %.0f 秒）...":
        "Local IP %s, scanning (about %.0f s) ...",
    "发现 %d 个可投屏设备": "Found %d castable device(s)",
    "来自环境变量 FFMPEG_PATH": "from the FFMPEG_PATH environment variable",
    "来自程序同目录": "from this program's folder",
    "来自缓存目录": "from the cache directory",
    "来自本机已有安装": "from an existing local installation",

    # ffmpeg 退出码含义
    "正常结束（不该在投屏中出现 —— 检查是否有 -t 之类的时长限制）":
        "Clean exit (should not happen while casting - check for a -t style duration limit)",
    "EPERM 操作不被允许 —— 常见于输出端（管道/文件）被占用或关闭":
        "EPERM operation not permitted - usually the output (pipe/file) is busy or closed",
    "EIO 读写出错": "EIO read/write error",
    "EACCES 权限/共享被拒 —— 通常是某个文件被另一个进程并发读写":
        "EACCES permission/sharing denied - usually a file being read and written by another process",
    "EINVAL 参数非法 —— 某个选项或滤镜参数不被支持":
        "EINVAL invalid argument - some option or filter parameter is unsupported",
    "EPIPE 输出端已断开（对端不再读取）":
        "EPIPE output end closed (the peer stopped reading)",
    "一般性错误 —— 看下面的 ffmpeg 输出":
        "Generic error - see the ffmpeg output below",
    "段错误 —— 已知组合：ddagrab 配 h264_mf（代码里已规避）":
        "Segfault - known combination: ddagrab with h264_mf (already avoided in code)",
    "电脑屏幕": "computer screen",
    "该设备没有 AVTransport 服务，不能作为播放目标":
        "This device offers no AVTransport service and cannot be a playback target",
    "SetAVTransportURI 被拒绝:\n%s": "SetAVTransportURI rejected:\n%s",
    "Play 被拒绝:\n%s": "Play rejected:\n%s",
    "fps --   Mbps --   延迟 --": "fps --   Mbps --   latency --",
    "延迟 --": "latency --",

    # =============================================== 核心模块：命令行帮助
    "DLNA 屏幕投屏器（把本机屏幕投到智能电视）":
        "DLNA Screen Caster (mirror this PC's screen to a smart TV)",
    "仅列出设备": "list devices only",
    "直接选择第 N 个设备": "pick the N-th device directly",
    "直接指定电视 IP（跳过扫描）": "specify the TV IP directly (skip scanning)",
    "推流模式，默认 mpegts": "streaming mode, default mpegts",
    "画质档位：540/720/1080/1080hq/1440(2K)/2160(4K)":
        "quality preset: 540/720/1080/1080hq/1440(2K)/2160(4K)",
    "帧率，默认 30。实测 gdigrab 1080p 可稳定跑满 30 —— 25 fps 白丢 17%% 的帧，且 25 与电视 60Hz 不整除（60÷25=2.4）会产生节奏不均的 judder；30 整除最顺畅。想更高需配合 --capture ddagrab":
        "frame rate, default 30. gdigrab holds a steady 30 at 1080p - 25 fps "
        "wastes 17%% of frames, and 25 does not divide the TV's 60 Hz evenly "
        "(60/25=2.4), producing uneven judder; 30 divides cleanly. Higher "
        "rates need --capture ddagrab",
    "视频编码器：auto（默认，优先硬件编码，失败自动回退） / mf（MediaFoundation 硬件编码，CPU 几乎不占，适合 2K/4K 与低延迟） / x264（软编，帧率最稳、输出 Main profile 画质更实）":
        "video encoder: auto (default, prefers hardware, falls back "
        "automatically) / mf (MediaFoundation hardware, almost no CPU load, "
        "good for 2K/4K and low latency) / x264 (software, steadiest FPS, "
        "Main profile output, more faithful)",
    "屏幕采集后端。auto（默认，ddagrab 可用则优先，不可用自动退回 gdigrab） / gdigrab（GDI 抓屏，兼容性最好，1080p 实测上限 30 fps） / ddagrab（Desktop Duplication，GPU 抓帧，CPU 占用低、帧率上限更高，需可用的 D3D11 桌面会话）":
        "screen capture backend. auto (default, prefers ddagrab when "
        "available, falls back to gdigrab) / gdigrab (GDI capture, most "
        "compatible, 30 fps ceiling at 1080p) / ddagrab (Desktop Duplication, "
        "GPU capture, low CPU, higher FPS ceiling, needs a working D3D11 "
        "desktop session)",
    "★ 本机流缓冲水位（毫秒）。这是本机侧延迟的唯一来源，越小延迟越低，弱网下更易卡顿。默认 300":
        "★ local stream buffer level (ms). The only source of local latency; "
        "lower means less delay but more stutter on weak networks. Default 300",
    "手动指定视频目标码率（kbps），覆盖画质档位的默认值，峰值自动取 1.18 倍。0 = 用档位默认。当电视端持续卡顿、日志出现\"链路吞吐不足\"时，用一个比实测交付速率低 15%% 左右的值即可":
        "manual target video bitrate (kbps), overriding the preset; the peak "
        "is taken as 1.18x. 0 = use the preset default. If the TV keeps "
        "stuttering and the log says \"link throughput too low\", use a value "
        "about 15%% below the measured delivery rate",
    "关键帧间隔（毫秒）。越小，追帧丢弃后画面恢复越快，代价是码率上升。默认 500":
        "keyframe interval (ms). Smaller recovers faster after dropped frames, "
        "at the cost of a higher bitrate. Default 500",
    "一键流畅优先预设：x264 软编 + 250ms 缓冲 + 400ms 关键帧 + 30fps（帧率最稳、judder 最小）":
        "smoothness-first preset: x264 + 250 ms buffer + 400 ms keyframe + "
        "30 fps (steadiest FPS, least judder)",
    "允许把画面放大到档位分辨率。默认禁止：1080p 屏幕选 4K 档会自动降级到 1080p，因为放大不会变清晰，只增开销":
        "allow upscaling to the preset resolution. Off by default: choosing "
        "4K on a 1080p screen downgrades to 1080p, since upscaling adds no "
        "detail, only overhead",
    "电视未接入、或接入后只缓冲不消化时的自动重推次数（重推会强制电视丢弃旧缓冲重新拉流），默认 2，0 关闭":
        "how many times to re-push when the TV never joins, or joins but only "
        "buffers (a re-push forces the TV to drop its buffer and re-pull), "
        "default 2, 0 disables",
    "每次推送后等待电视接入并正常消费的秒数，默认 18（覆盖常见 15 秒广告）":
        "seconds to wait after each push for the TV to join and consume, "
        "default 18 (covers the common 15 s start-up ad)",
    "额外打开毫秒时钟页，用于实测端到端延迟（电脑与电视同框拍照，读时间差）":
        "also open the millisecond clock page for measuring end-to-end "
        "latency (photograph PC and TV together and read the difference)",
    "关闭画面右上角的信息显示。默认开启：帧率 / 码率 / 端到端延迟(ms)，灰白小字带阴影":
        "disable the on-screen info in the top-right corner. On by default: "
        "FPS / bitrate / end-to-end latency (ms), small grey text with a shadow",
    "OSD 字号（像素）。默认按输出分辨率自动取约 1.8%% 高度":
        "OSD font size (px). Defaults to about 1.8%% of the output height",
    "★ 发送节流：允许服务端超前实时多少毫秒。它直接决定电视端缓冲深度，也就是延迟。默认 800 —— 既够电视起播，又不会像原来那样随播放时长无限累积。填 0 关闭节流":
        "★ send pacing: how many ms the server may run ahead of real time. It "
        "directly sets the TV buffer depth, i.e. the latency. Default 800 - "
        "enough for the TV to start, without accumulating endlessly as "
        "playback goes on. 0 disables pacing",
    "音频来源：loopback（默认，WASAPI 环回捕获系统声音） / none / dshow:<设备名>":
        "audio source: loopback (default, WASAPI loopback captures system "
        "audio) / none / dshow:<device>",
    "音频相对视频的偏移（秒），音画不同步时微调，默认 0":
        "audio offset relative to video (seconds); tweak if A/V drift. Default 0",
    "投屏时保留电脑本地声音；默认会把电脑静音，让声音只从电视出（避免两边同时响）":
        "keep the PC's local sound while casting; by default the PC is muted "
        "so only the TV plays (avoids hearing both at once)",
    "只解除本机静音后退出（程序被强制结束后电脑没声时用）":
        "only unmute the PC and exit (for when a forced kill left the PC silent)",
    "流缓冲上限（KB），越小延迟越低但越易卡顿，默认 384":
        "stream buffer limit (KB); smaller means less latency but more "
        "stutter. Default 384",
    "只启动流服务，不推给电视（本机自测）":
        "start the stream server only, do not push to the TV (local testing)",
    "界面语言：zh = 中文 / en = English":
        "UI language: zh = Chinese / en = English",

    "想进一步降低延迟 ——\n"
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
    "  电视端缓冲在持续堆积。\n":

    "For even lower latency --\n"
    "  End-to-end latency is dominated by the TV player's buffer depth; it\n"
    "  cannot be tuned from this side. The way around it: open the address\n"
    "  below in the TV's OWN BROWSER (same LAN; the program prints the full\n"
    "  address at start-up):\n"
    "\n"
    "      http://<local-ip>:<port>/tv\n"
    "\n"
    "  That page plays the raw MPEG-TS stream via mpegts.js, with the buffer\n"
    "  depth controlled by the page. Three latency levels can be switched on\n"
    "  the spot (0.5s / 1.0s / 2.0s), and it shows live latency, buffer,\n"
    "  FPS, dropped frames and bitrate. It usually cuts latency from several\n"
    "  seconds to under one. Available in mpegts mode (the default) only.\n"
    "\n"
    "  If the TV browser lacks MSE, the page suggests using VLC instead\n"
    "  (set its network buffering to 300 ms).\n"
    "\n"
    "Measuring latency --\n"
    "  --clock opens a millisecond clock page; photograph the PC and the TV\n"
    "  together and subtract the two timestamps for end-to-end latency.\n"
    "  Shoot again 30 s later: a larger value means the TV buffer keeps\n"
    "  growing.\n",

    # ===================================================== ffmpeg 获取
    "服务器不支持按范围下载（HTTP %s）":
        "Server does not support range requests (HTTP %s)",
    "范围下载长度不符：要 %d，得 %d":
        "Range response length mismatch: asked %d, got %d",
    "  使用环境变量 DLNACAST_FFMPEG_URL 指定的下载地址":
        "  Using the URL from the DLNACAST_FFMPEG_URL environment variable",
    "自定义": "custom",
    "  查询 GitHub 上的最新版本 ...": "  Querying GitHub for the latest release ...",
    "  最新版本 %s，包 %s（%.1f MB）":
        "  Latest release %s, asset %s (%.1f MB)",
    "最新版本 %s 里没有找到 %s": "Asset %s not found in release %s",
    "  GitHub API 失败（%s），改用固定地址兜底":
        "  GitHub API failed (%s); falling back to a fixed URL",
    "不是有效的 zip（找不到中央目录结尾记录）":
        "Not a valid zip (no end-of-central-directory record)",
    "zip64 暂不支持": "zip64 is not supported yet",
    "不知道远端文件大小，无法按范围读取":
        "Remote file size unknown; cannot read by range",
    "  压缩包目录：%d 个成员，目录表 %d 字节":
        "  Archive directory: %d entries, central directory %d bytes",
    "中央目录越界": "Central directory out of bounds",
    "压缩包里没有 %s": "The archive contains no %s",
    "  找到成员 %s（压缩 %0.1f MB / 解开 %0.1f MB）":
        "  Found entry %s (compressed %0.1f MB / uncompressed %0.1f MB)",
    "服务器不支持按范围下载": "Server does not support range requests",
    "分段长度不符：%d ≠ %d": "Segment length mismatch: %d != %d",
    "分段下载失败（%d 次重试后）：%s":
        "Segment download failed after %d retries: %s",
    "本地文件头签名不对": "Bad local file header signature",
    "  开始下载（%d 个连接并行，共约 %.1f MB）...":
        "  Downloading (%d parallel connections, about %.1f MB total) ...",
    "已取消": "Cancelled",
    "有 %d 段没下完：%s": "%d segment(s) incomplete: %s",
    "解出大小不符：%d ≠ %d": "Inflated size mismatch: %d != %d",
    "CRC 校验失败，下载可能被截断或篡改":
        "CRC check failed; the download may be truncated or tampered with",
    "  下载完成 %.1f MB，用时 %.0f 秒（平均 %.2f MB/s）":
        "  Download finished: %.1f MB in %.0f s (%.2f MB/s average)",
    "  整包下载（不支持的服务器走这条路）...":
        "  Full-archive download (for servers without range support) ...",
    "  解出 %s": "  Extracted %s",
    "  完成，用时 %.0f 秒": "  Done in %.0f s",
    "编码器 libx264": "encoder libx264",
    "硬件编码 h264_mf": "hardware encoder h264_mf",
    "采集 gdigrab": "capture gdigrab",
    "桌面复制 ddagrab": "desktop duplication ddagrab",
    "字幕绘制 drawtext": "text drawing drawtext",
    "封装 mpegts": "muxer mpegts",
    "拿不到远端文件大小": "Cannot determine the remote file size",
    "  按范围下载失败（%s），改为整包下载":
        "  Range download failed (%s); falling back to the full archive",
    "下载的 ffmpeg 无法执行（可能被杀软隔离）":
        "The downloaded ffmpeg will not run (possibly quarantined by antivirus)",
    "  ! 这份 ffmpeg 缺少：%s": "  ! This ffmpeg lacks: %s",
    "  能力检查通过：libx264 / h264_mf / gdigrab / drawtext / mpegts 都在":
        "  Capability check passed: libx264 / h264_mf / gdigrab / drawtext / mpegts all present",
    "本机已有：": "Found locally: ",
    "找到 %s，但它无法执行，忽略。":
        "Found %s but it will not run; ignoring it.",
    "已设 DLNACAST_FORCE_DOWNLOAD，跳过本机已有的 ffmpeg。":
        "DLNACAST_FORCE_DOWNLOAD is set; skipping the local ffmpeg.",
    "本机没有 ffmpeg，且已禁用自动下载":
        "No local ffmpeg and automatic download is disabled",
    "本机没有找到 ffmpeg，准备从 GitHub 下载最新版 ...":
        "No local ffmpeg found; downloading the latest from GitHub ...",
    "  （想用别的镜像：设环境变量 DLNACAST_FFMPEG_URL 指向另一个 zip）":
        "  (To use a mirror: point DLNACAST_FFMPEG_URL at another zip)",
    "已下载最新版": "Downloaded the latest version",
    "下载失败：%s": "Download failed: %s",
    "已就绪：%s": "Ready: %s",

    # ==================================================== 零散片段与错误消息
    # 这些是拼进更大句子、或直接弹给用户看的短串
    "无": "none",
    "未知设备": "Unknown device",
    "%s 调用失败: %s": "%s call failed: %s",
    "SSDP 发送失败: %s": "SSDP send failed: %s",
    "本机缓冲": "local buffer",
    "关键帧间隔": "keyframe interval",
    "发送节流": "pacing",
    "手动码率": "bitrate",

    # WASAPI 环回（ctypes 直调 COM）的失败消息 —— 都会打到日志里
    "WASAPI 环回仅支持 Windows": "WASAPI loopback is available on Windows only",
    "创建 MMDeviceEnumerator 失败 hr=0x%08X":
        "Failed to create MMDeviceEnumerator (hr=0x%08X)",
    "获取默认播放设备失败 hr=0x%08X":
        "Failed to get the default playback device (hr=0x%08X)",
    "激活 IAudioClient 失败 hr=0x%08X":
        "Failed to activate IAudioClient (hr=0x%08X)",
    "GetMixFormat 失败 hr=0x%08X": "GetMixFormat failed (hr=0x%08X)",
    "Initialize(LOOPBACK) 失败 hr=0x%08X":
        "Initialize(LOOPBACK) failed (hr=0x%08X)",
    "GetService(IAudioCaptureClient) 失败 hr=0x%08X":
        "GetService(IAudioCaptureClient) failed (hr=0x%08X)",
    "IAudioClient::Start 失败 hr=0x%08X":
        "IAudioClient::Start failed (hr=0x%08X)",
}

# 含动态内容的来源说明：前缀匹配 + 保留尾部
_PREFIX = (
    ("本机已有：", "Found locally: "),
    ("下载失败：", "Download failed: "),
)
