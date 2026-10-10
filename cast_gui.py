# -*- coding: utf-8 -*-
"""DLNA 投屏器 —— 图形界面，同时是 PyInstaller 的打包入口。

两种运行形态共用一个 exe：

* **不带参数**（双击）      → 打开图形界面
* **带参数**（命令行）      → 走 ``screen-cast.py`` 的原始命令行，并临时
                             分配一个控制台窗口来显示输出

界面里把「投屏目标 / 画质性能 / 高级设置」分成三块，默认只露前两块 ——
高级那块收起来，不把一堆术语砸到不看参数的人脸上。

投屏逻辑一行都没重写：``cast.py`` 那套 run_cast() 同时服务命令行和界面，
所以两边行为不可能跑偏。
"""

import os
import queue
import sys
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import ffmpeg_fetch  # noqa: E402
import i18n  # noqa: E402

# 界面文案一律走 i18n.tr()（短名 T）。中文表命中就换英文，查不到原样返回，
# 所以中文版的行为跟改造前完全一致。
T = i18n.T


# ------------------------------------------------------------------ 环境准备


def _ensure_streams():
    """保证 sys.stdout / sys.stderr 不是 None。

    窗口形态（``--windowed``）下这两个流就是 None。任何库代码只要往它们
    写一个字（比如 argparse 打印 ``--help``）就会抛 AttributeError，而且
    这个异常本身也没地方输出 —— 整个进程静默死掉，查起来极其费劲。
    这里先垫一个黑洞，至少让写入"成功"。
    """
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            try:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            except Exception:
                pass


def _alloc_console():
    """把 GUI 形态的 exe 临时变成控制台形态。

    打包时用 --windowed 是为了双击时不弹黑框；但同一个 exe 要支持
    ``--list`` 这类命令，那些输出没地方去。AllocConsole() 现开一个
    控制台并把标准流接上去，两全。

    设环境变量 DLNACAST_NO_CONSOLE=1 可禁用（脚本调用 exe 时不想看到窗口）。
    """
    if os.environ.get("DLNACAST_NO_CONSOLE"):
        return False
    try:
        import ctypes
        k = ctypes.windll.kernel32
        if not k.AllocConsole():
            return False
        k.SetConsoleOutputCP(65001)   # UTF-8，否则中文参数会乱码
        k.SetConsoleCP(65001)
        sys.stdout = open("CONOUT$", "w", encoding="utf-8",
                          errors="replace", buffering=1)
        sys.stderr = open("CONOUT$", "w", encoding="utf-8",
                          errors="replace", buffering=1)
        sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")
        return True
    except Exception:
        return False


def _enable_dpi_awareness():
    """让界面在高分屏上不发虚。

    顺带对齐了抓屏逻辑：``get_desktop_size()`` 依赖进程是 DPI 感知的，
    否则拿到的分辨率是缩放后的逻辑值，会算错档位。
    """
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _mmss(seconds):
    """把秒数说成人话（下载剩下多久）。"""
    try:
        s = int(max(0, seconds))
    except Exception:
        return "?"
    if s < 60:
        return T("%d 秒") % s
    return T("%d 分 %02d 秒") % (s // 60, s % 60)


def load_core():
    """加载 ``screen-cast.py``。

    文件名带连字符，没法 ``import``，所以按路径加载。打包后它被当作数据
    文件放进 ``_MEIPASS``，于是开发和冻结两种模式走的是同一段代码 ——
    不会出现「本地能用、打包后不一样」。
    """
    import importlib.util
    name = "screen_cast_core"
    if name in sys.modules:
        return sys.modules[name]
    base = getattr(sys, "_MEIPASS", None) or _HERE
    path = os.path.join(base, "screen-cast.py")
    if not os.path.isfile(path):
        raise IOError(T("找不到投屏核心文件：%s") % path)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _install_crash_handler(cache_dir):
    """窗口形态下未捕获异常会静默退出；落盘 + 弹窗，别让用户只看到「闪退」。"""
    import traceback

    def _hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            os.makedirs(cache_dir, exist_ok=True)
            with open(os.path.join(cache_dir, "crash.log"), "a",
                      encoding="utf-8") as f:
                f.write("\n===== %s =====\n%s" % (__import__("time").ctime(), text))
        except Exception:
            pass
        try:
            import tkinter.messagebox as mb
            mb.showerror(T("DLNA 投屏器 出错"), text[-1500:])
        except Exception:
            pass

    sys.excepthook = _hook


# ------------------------------------------------------------------ 界面

def build_gui(core):
    """构造并运行主窗口。core 是已加载的 screen-cast 模块。"""
    import tkinter as tk
    from tkinter import ttk, messagebox
    from tkinter.scrolledtext import ScrolledText

    # 英文界面用 Segoe UI（Windows 自带的西文字体，比微软雅黑好看）；
    # 中文界面仍用微软雅黑。两个字体在两种语言的 Windows 上都有。
    UI_FONT = "Segoe UI" if i18n.is_en() else "Microsoft YaHei UI"
    # 下拉框宽度（字符数）。英文描述比中文长，窄了会被截断成 "Auto (recomm…"
    _EN = i18n.is_en()
    W_LBL = 15 if _EN else 10      # 左侧标签列
    W_Q = 46 if _EN else 44        # 画质档位
    W_ENC = 42 if _EN else 36      # 视频编码
    W_CAP = 42 if _EN else 34      # 采集后端

    # ---- 选项文案 ↔ 内部取值 的映射（界面说人话，代码收短标识）----
    # 文案在**构造时**就翻译好，后面的 label_of()/value_of() 拿到的是一致
    # 的字符串，映射关系不受语言影响。
    ENCODERS = [
        (T("自动（推荐：优先硬件，失败自动退软编）"), "auto"),
        (T("硬件编码 h264_mf（几乎不占 CPU）"), "mf"),
        (T("软件编码 x264（帧率最稳、画质最实）"), "x264"),
    ]
    CAPTURES = [
        (T("自动（推荐）"), "auto"),
        (T("GDI 抓屏 gdigrab（兼容性最好）"), "gdigrab"),
        (T("桌面复制 ddagrab（CPU 占用低、帧率上限高）"), "ddagrab"),
    ]
    AUDIOS = [
        (T("跟随系统声音（推荐，声音从电视出）"), "loopback"),
        (T("不要声音"), "none"),
    ]
    MODES = [(T("MPEG-TS 直播（推荐，延迟低）"), "mpegts"),
             (T("HLS 分片（兼容优先，延迟高）"), "hls")]
    FPS_ITEMS = ["24", "25", "30", "50", "60"]

    def label_of(pairs, value):
        for text, val in pairs:
            if val == value:
                return text
        return pairs[0][0]

    def value_of(pairs, text):
        for t, v in pairs:
            if t == text:
                return v
        return pairs[0][1]

    quality_texts = [T(core.QUALITY_LABELS[k]) for k in core.QUALITY_ORDER]

    # ---- 窗口骨架 ----
    root = tk.Tk()
    root.title(T("DLNA 屏幕投屏器"))
    root.minsize(800, 620)

    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass

    # 高分屏字体缩放：tk scaling 的单位是「每点像素数」
    try:
        dpi = root.winfo_fpixels("1i")
        root.tk.call("tk", "scaling", max(1.0, min(2.0, dpi / 72.0)))
    except Exception:
        pass

    outer = ttk.Frame(root, padding=10)
    outer.pack(fill="both", expand=True)

    ui = {}                      # 控件登记表，回调里按名字取

    # ================= 顶部：标题 + 本机信息 =================
    head = ttk.Frame(outer)
    head.pack(fill="x")
    ttk.Label(head, text=T("DLNA 屏幕投屏器"),
              font=(UI_FONT, 15, "bold")).pack(anchor="w")
    # 环境信息单独占一行：放在标题右侧会被窗口宽度截断，
    # 而 IP / 分辨率恰恰是排查问题时要看的
    ui["env_label"] = ttk.Label(head, text=T("正在检查运行环境 ..."),
                                foreground="#666666")
    ui["env_label"].pack(anchor="w")

    # ================= 投屏目标 =================
    lf_dev = ttk.LabelFrame(outer, text=T(" 投屏目标 "), padding=10)
    lf_dev.pack(fill="x", pady=(10, 0))

    row = ttk.Frame(lf_dev)
    row.pack(fill="x")
    ttk.Label(row, text=T("电视设备"), width=W_LBL).pack(side="left")
    ui["dev_combo"] = ttk.Combobox(row, state="readonly", width=48,
                                   values=[T("（尚未扫描）")])
    ui["dev_combo"].current(0)
    ui["dev_combo"].pack(side="left", fill="x", expand=True, padx=(0, 8))
    ui["btn_scan"] = ttk.Button(row, text=T("扫描设备"), width=12,
                                command=lambda: on_scan())
    ui["btn_scan"].pack(side="left")

    row2 = ttk.Frame(lf_dev)
    row2.pack(fill="x", pady=(8, 0))
    ttk.Label(row2, text=T("手动指定"), width=W_LBL).pack(side="left")
    ui["ip_entry"] = ttk.Entry(row2, width=20)
    ui["ip_entry"].pack(side="left")
    ttk.Label(row2, text=T("  电视 IP（填了就跳过扫描，如 192.168.1.100）"),
              foreground="#666666").pack(side="left")

    # ================= 画质与性能 =================
    lf_q = ttk.LabelFrame(outer, text=T(" 画质与性能 "), padding=10)
    lf_q.pack(fill="x", pady=(10, 0))

    r1 = ttk.Frame(lf_q)
    r1.pack(fill="x")
    ttk.Label(r1, text=T("画质档位"), width=W_LBL).pack(side="left")
    ui["quality"] = ttk.Combobox(r1, state="readonly", width=W_Q,
                                 values=quality_texts)
    ui["quality"].set(T(core.QUALITY_LABELS["1080hq"]))
    ui["quality"].pack(side="left")
    ttk.Label(r1, text=T("   帧率")).pack(side="left")
    ui["fps"] = ttk.Combobox(r1, state="readonly", width=6, values=FPS_ITEMS)
    ui["fps"].set("30")
    ui["fps"].pack(side="left", padx=(6, 0))

    r2 = ttk.Frame(lf_q)
    r2.pack(fill="x", pady=(8, 0))
    ttk.Label(r2, text=T("视频编码"), width=W_LBL).pack(side="left")
    ui["encoder"] = ttk.Combobox(r2, state="readonly", width=W_ENC,
                                 values=[t for t, _ in ENCODERS])
    ui["encoder"].set(ENCODERS[0][0])
    ui["encoder"].pack(side="left")
    ttk.Label(r2, text=T("   采集")).pack(side="left")
    ui["capture"] = ttk.Combobox(r2, state="readonly", width=W_CAP,
                                 values=[t for t, _ in CAPTURES])
    ui["capture"].set(CAPTURES[0][0])
    ui["capture"].pack(side="left", padx=(6, 0))

    # ================= 高级设置（默认收起） =================
    ui["adv_shown"] = tk.BooleanVar(value=False)
    ui["btn_adv"] = ttk.Button(outer, text=T("▸  高级设置（一般不用改）"),
                               command=lambda: toggle_adv())
    ui["btn_adv"].pack(fill="x", pady=(8, 0))

    lf_adv = ttk.LabelFrame(outer, text=T(" 高级设置 "), padding=10)

    g = ttk.Frame(lf_adv)
    g.pack(fill="x")
    specs = [
        ("port", T("HTTP 端口"), "8899", 8),
        ("latency_ms", T("本机缓冲 ms"), "300", 8),
        ("gop_ms", T("关键帧间隔 ms"), "500", 8),
        ("lead_ms", T("发送节流 ms"), "800", 8),
        ("bitrate", T("手动码率 kbps"), "0", 10),
    ]
    for i, (key, text, default, width) in enumerate(specs):
        col = ttk.Frame(g)
        col.grid(row=0, column=i, sticky="w", padx=(0, 14))
        ttk.Label(col, text=text).pack(anchor="w")
        e = ttk.Entry(col, width=width)
        e.insert(0, default)
        e.pack(anchor="w")
        ui[key] = e
    ttk.Label(g, text=T("(手动码率 0 = 用档位默认值)"),
              foreground="#666666").grid(row=0, column=len(specs), sticky="sw")

    r3 = ttk.Frame(lf_adv)
    r3.pack(fill="x", pady=(10, 0))
    ttk.Label(r3, text=T("推流模式"), width=W_LBL).pack(side="left")
    ui["mode"] = ttk.Combobox(r3, state="readonly", width=W_CAP,
                              values=[t for t, _ in MODES])
    ui["mode"].set(MODES[0][0])
    ui["mode"].pack(side="left")
    ttk.Label(r3, text=T("   声音来源")).pack(side="left")
    ui["audio"] = ttk.Combobox(r3, state="readonly", width=W_CAP,
                               values=[t for t, _ in AUDIOS])
    ui["audio"].set(AUDIOS[0][0])
    ui["audio"].pack(side="left", padx=(6, 0))

    r4 = ttk.Frame(lf_adv)
    r4.pack(fill="x", pady=(10, 0))
    checks = [
        ("no_osd_inv", T("在电视画面右上角显示帧率 / 码率 / 延迟"), True),
        ("low_latency", T("流畅优先预设（软编 + 更小缓冲，帧率最稳）"), False),
        ("allow_upscale", T("允许把画面放大到档位分辨率（通常没必要）"), False),
        ("clock", T("同时打开延迟诊断时钟页（用于量端到端延迟）"), False),
        ("no_push", T("仅在本机自测，不推送到电视"), False),
    ]
    ui["checks"] = {}
    for key, text, default in checks:
        v = tk.BooleanVar(value=default)
        cb = ttk.Checkbutton(r4, text=text, variable=v)
        cb.pack(anchor="w")
        ui["checks"][key] = v

    ttk.Label(lf_adv,
              text=T("提示：电视端卡顿时，把「手动码率」调到比实测交付速率低 15% 左右即可。"),
              foreground="#666666").pack(anchor="w", pady=(8, 0))

    # ================= 操作区 =================
    act = ttk.Frame(outer)
    act.pack(fill="x", pady=(10, 0))
    ui["btn_start"] = ttk.Button(act, text=T("▶  开始投屏"), width=18,
                                 command=lambda: on_start())
    ui["btn_start"].pack(side="left")
    ui["btn_stop"] = ttk.Button(act, text=T("■  停止"), width=13,
                                state="disabled", command=lambda: on_stop())
    ui["btn_stop"].pack(side="left", padx=(8, 0))
    ui["progress"] = ttk.Progressbar(act, mode="indeterminate", length=150)
    ui["progress"].pack(side="left", padx=(16, 0))
    ui["status"] = ttk.Label(act, text=T("就绪"), foreground="#1a5fb4")
    ui["status"].pack(side="left", padx=(12, 0))

    # 电视端低延迟播放页地址（推流成功后才出现）
    tvrow = ttk.Frame(outer)
    tvrow.pack(fill="x", pady=(8, 0))
    ui["tv_label"] = ttk.Label(
        tvrow,
        text=T("电视端低延迟播放页：开始投屏后这里会显示地址"
               "（在电视浏览器里打开，可把延迟从几秒压到 1 秒内）"),
        foreground="#777777")
    ui["tv_label"].pack(side="left")
    ui["btn_copy"] = ttk.Button(tvrow, text=T("复制地址"),
                                width=14 if _EN else 10,
                                state="disabled", command=lambda: on_copy())
    ui["btn_copy"].pack(side="right")

    # ================= 日志 =================
    lf_log = ttk.LabelFrame(outer, text=T(" 运行日志 "), padding=6)
    lf_log.pack(fill="both", expand=True, pady=(10, 0))
    ui["log"] = ScrolledText(lf_log, height=8, wrap="word",
                             font=("Consolas", 9), state="disabled",
                             background="#1e1e1e", foreground="#d4d4d4",
                             insertbackground="#d4d4d4")
    ui["log"].pack(fill="both", expand=True)
    ui["log"].tag_config("err", foreground="#ff8080")
    ui["log"].tag_config("ok", foreground="#7ddc7d")

    # ---- 默认窗口尺寸 ----
    # 高度按「高级设置已展开」时所需的高度来定。否则用户一点开高级设置，
    # 多出来的内容会把日志区挤成一条缝（实测过，确实会）。
    # 上限取屏幕高度减 120，免得在 1366×768 这类小屏上超出桌面。
    lf_adv.pack(fill="x", pady=(8, 0), before=act)
    root.update_idletasks()
    need_h = root.winfo_reqheight()
    screen_h = root.winfo_screenheight()
    lf_adv.pack_forget()
    root.update_idletasks()
    # 减 150 是给标题栏和任务栏留位置，否则窗口底边会压到任务栏下面
    root.geometry("880x%d" % max(640, min(need_h + 8, screen_h - 150)))

    # ================= 运行期状态 =================
    st = {
        "q": queue.Queue(),
        "devices": [],
        "stop_event": None,
        "worker": None,
        "running": False,
        "ffmpeg_ok": False,
        "ffmpeg_path": None,
        "tv_url": "",
        # 下载 ffmpeg 用的是独立的取消标志：那会儿还没开始投屏，
        # stop_event 还是 None，不能共用
        "downloading": False,
        "dl_cancel": threading.Event(),
    }

    # ---------------------------------------------------------- 日志与状态

    def log_line(text, is_err=False):
        """只从主线程调用。"""
        # 最后一道翻译：核心模块的 log 已翻过一遍，这里覆盖 GUI 自己与
        # ffmpeg_fetch 回调送来的文本。tr() 幂等，重复翻不会出错。
        text = i18n.tr(text)
        w = ui["log"]
        w.configure(state="normal")
        w.insert("end", text + "\n", ("err",) if is_err else ())
        w.see("end")
        # 长时间投屏日志会很长，超过 2000 行就砍掉最早的
        if int(w.index("end-1c").split(".")[0]) > 2000:
            w.delete("1.0", "200.0")
        w.configure(state="disabled")

    def sink(text, is_err):
        """核心模块的回调，来自工作线程 —— 只投队列，不碰控件。"""
        st["q"].put(("log", (text, is_err)))

    def set_status(text, color="#1a5fb4"):
        ui["status"].configure(text=i18n.tr(text), foreground=color)

    def drain():
        try:
            while True:
                kind, payload = st["q"].get_nowait()
                try:
                    if kind == "log":
                        log_line(payload[0], payload[1])
                    elif kind == "status":
                        set_status(*payload)
                    elif kind == "done":
                        on_done(payload)
                    elif kind == "ready":
                        on_ready(payload)
                    elif kind == "progress":
                        # 投屏进度是 (done, total)；下载进度多一个网速
                        done, total = payload[0], payload[1]
                        ui["progress"].configure(mode="determinate",
                                                 maximum=max(1, total), value=done)
                        if len(payload) > 2 and total:
                            spd = payload[2]
                            left = (total - done) / max(0.001, spd)
                            set_status(
                                T("正在下载 ffmpeg  %.1f / %.1f MB（%.0f%%，"
                                  "%.2f MB/s，剩余约 %s）")
                                % (done / 1048576, total / 1048576,
                                   done * 100.0 / total, spd / 1048576,
                                   _mmss(left)), "#a35a00")
                    elif kind == "progress_reset":
                        ui["progress"].stop()
                        ui["progress"].configure(mode="indeterminate", value=0)
                    elif kind == "dl_state":
                        # 下载开始/结束：把「停止」按钮借去当「取消下载」
                        if payload:
                            ui["btn_stop"].configure(text=T("■  取消下载"),
                                                     state="normal")
                        else:
                            ui["btn_stop"].configure(text=T("■  停止"),
                                                     state="disabled")
                    elif kind == "env":
                        ui["env_label"].configure(text=payload)
                    elif kind == "devices":
                        fill_devices(payload)
                except Exception as e:
                    # 单个消息出错不能把整个消息泵带走
                    log_line(T("界面刷新出错：%s") % e, True)
        except queue.Empty:
            pass
        root.after(120, drain)

    # ---------------------------------------------------------- 设备

    def fill_devices(devices):
        st["devices"] = devices
        ui["btn_scan"].configure(state="normal")
        set_status("就绪")
        ui["progress"].stop()
        ui["progress"].configure(mode="indeterminate")
        if not devices:
            ui["dev_combo"].configure(values=[T("（未发现设备）")])
            ui["dev_combo"].current(0)
            try:
                diag = core.network_diagnosis()
            except Exception:
                diag = ["未发现 DLNA 设备。请确认电视与电脑在同一局域网，"
                        "且电视已开启「多屏互动 / DLNA / 媒体共享」。"]
            for line in diag:
                log_line(line, line.startswith("★"))
            return
        names = ["%s  (%s)" % (d.name, d.ip) for d in devices]
        ui["dev_combo"].configure(values=names)
        ui["dev_combo"].current(0)

    def on_scan():
        if st["running"]:
            return
        ui["btn_scan"].configure(state="disabled")
        set_status("正在扫描设备 ...")
        ui["progress"].configure(mode="indeterminate")
        ui["progress"].start(12)

        def work():
            try:
                devs = core.scan_devices(
                    4.0, log_fn=lambda m: st["q"].put(("log", (m, False))))
            except Exception as e:
                st["q"].put(("log", (T("扫描失败：%s") % e, True)))
                devs = []
            st["q"].put(("devices", devs))

        threading.Thread(target=work, daemon=True).start()

    # ---------------------------------------------------------- 配置收集

    def _int_of(widget, name, default):
        raw = widget.get().strip()
        if raw == "":
            return default
        try:
            return int(float(raw))
        except ValueError:
            raise ValueError(T("「%s」要填数字，当前是「%s」") % (name, raw))

    def collect_config():
        cfg = core.CastConfig()
        cfg.quality = core.QUALITY_ORDER[
            quality_texts.index(ui["quality"].get())]
        cfg.fps = int(ui["fps"].get())
        cfg.encoder = value_of(ENCODERS, ui["encoder"].get())
        cfg.capture = value_of(CAPTURES, ui["capture"].get())
        cfg.mode = value_of(MODES, ui["mode"].get())
        cfg.audio = value_of(AUDIOS, ui["audio"].get())
        cfg.port = _int_of(ui["port"], T("HTTP 端口"), 8899)
        cfg.latency_ms = _int_of(ui["latency_ms"], T("本机缓冲"), 300)
        cfg.gop_ms = _int_of(ui["gop_ms"], T("关键帧间隔"), 500)
        cfg.lead_ms = _int_of(ui["lead_ms"], T("发送节流"), 800)
        cfg.bitrate = _int_of(ui["bitrate"], T("手动码率"), 0)
        cfg.no_osd = not ui["checks"]["no_osd_inv"].get()
        cfg.low_latency = ui["checks"]["low_latency"].get()
        cfg.allow_upscale = ui["checks"]["allow_upscale"].get()
        cfg.clock = ui["checks"]["clock"].get()
        cfg.no_push = ui["checks"]["no_push"].get()

        ip = ui["ip_entry"].get().strip()
        if ip:
            cfg.ip = ip
        else:
            idx = ui["dev_combo"].current()
            if not st["devices"] or idx < 0 or idx >= len(st["devices"]):
                if not cfg.no_push:
                    raise ValueError(
                        T("还没选电视。点「扫描设备」，或在上面的「手动指定」里填电视 IP。"))
            else:
                cfg.ip = st["devices"][idx].ip
        return cfg

    # ---------------------------------------------------------- 启停

    def on_start():
        if st["running"]:
            return
        try:
            cfg = collect_config()
        except ValueError as e:
            messagebox.showwarning(T("参数需要调整"), str(e))
            return

        st["tv_url"] = ""
        ui["tv_label"].configure(text="")
        ui["btn_copy"].configure(state="disabled")
        st["stop_event"] = threading.Event()
        st["running"] = True
        ui["btn_start"].configure(state="disabled")
        ui["btn_scan"].configure(state="disabled")
        ui["btn_stop"].configure(state="normal")
        ui["progress"].configure(mode="indeterminate")
        ui["progress"].start(12)
        set_status("正在启动 ...")
        log_line("")
        log_line("—" * 60)

        core.add_log_sink(sink)

        def work():
            code = None
            try:
                code = core.run_cast(
                    cfg, stop_event=st["stop_event"],
                    ready_cb=lambda info: st["q"].put(("ready", info)))
            except Exception as e:
                import traceback
                st["q"].put(("log", (T("发生异常：%s") % e, True)))
                st["q"].put(("log", (traceback.format_exc(), True)))
                code = -1
            finally:
                core.remove_log_sink(sink)
                st["q"].put(("done", code))

        st["worker"] = threading.Thread(target=work, daemon=True)
        st["worker"].start()

    def on_stop():
        # 下载阶段也允许中止 —— 几十 MB 的下载不给个取消太难受了
        if st["downloading"]:
            st["dl_cancel"].set()
            ui["btn_stop"].configure(state="disabled")
            set_status("正在取消下载 ...", "#a35a00")
            log_line("已请求取消下载。已下好的分段会留着，下次接着下。")
            return
        if not st["running"] or st["stop_event"] is None:
            return
        ui["btn_stop"].configure(state="disabled")
        set_status("正在停止 ...", "#a35a00")
        log_line("")
        log_line("收到停止指令，正在收尾（会恢复电脑声音）...")
        st["stop_event"].set()

    def on_ready(info):
        url = info.get("tv_url")
        st["tv_url"] = url or ""
        if url:
            ui["tv_label"].configure(
                text=T("电视端低延迟播放页：%s   （在电视浏览器里打开，点「开始播放」）") % url,
                foreground="#0f6b3a", font=("Consolas", 10))
            ui["btn_copy"].configure(state="normal")
        set_status(T("投屏中 · %s") % info.get("device", ""), "#0f6b3a")

    def on_done(code):
        st["running"] = False
        st["stop_event"] = None
        ui["worker"] = None
        ui["btn_start"].configure(state="normal")
        ui["btn_scan"].configure(state="normal")
        ui["btn_stop"].configure(state="disabled")
        ui["progress"].stop()
        ui["progress"].configure(mode="indeterminate", value=0)
        if code == 0:
            set_status("已停止", "#444444")
        else:
            set_status(T("已结束（退出码 %s）") % code, "#a30000")

    def on_copy():
        if not st["tv_url"]:
            return
        root.clipboard_clear()
        root.clipboard_append(st["tv_url"])
        set_status("地址已复制到剪贴板", "#0f6b3a")

    def toggle_adv():
        if ui["adv_shown"].get():
            lf_adv.pack_forget()
            ui["btn_adv"].configure(text=T("▸  高级设置（一般不用改）"))
            ui["adv_shown"].set(False)
        else:
            # 插到「开始投屏」按钮之前，保持视觉顺序
            lf_adv.pack(fill="x", pady=(8, 0), before=act)
            ui["btn_adv"].configure(text=T("▾  高级设置（一般不用改）"))
            ui["adv_shown"].set(True)

    # ---------------------------------------------------------- 关闭

    closing = {"asked": False}

    def on_close():
        if st["running"] and not closing["asked"]:
            if not messagebox.askokcancel(
                    T("正在投屏"),
                    T("投屏还在进行。确定要停止并退出吗？\n\n"
                      "（会先停止投屏、恢复电脑本地声音，再关闭窗口）")):
                return
            closing["asked"] = True
            st["stop_event"].set()
            set_status("正在停止，请稍候 ...", "#a35a00")
            # 给收尾留时间（恢复静音、杀 ffmpeg），但最多等 10 秒
            root.after(200, _wait_then_quit)
            return
        root.destroy()

    waited = {"n": 0}

    def _wait_then_quit():
        w = st["worker"]
        if (w is None or not w.is_alive()) or waited["n"] > 50:
            root.destroy()
            return
        waited["n"] += 1
        root.after(200, _wait_then_quit)

    root.protocol("WM_DELETE_WINDOW", on_close)

    # ---------------------------------------------------------- 启动自检

    def prepare():
        """自检 ffmpeg：本机有就直接用，没有就下载（可能要几分钟）。

        这段放后台线程 + 进度条。下载只走一次，之后 exe 同目录就有一份
        ffmpeg.exe，再启动是秒过的。
        """
        core.add_log_sink(sink)
        cancel_ev = threading.Event()
        st["dl_cancel"] = cancel_ev
        try:
            st["q"].put(("status", ("正在检查 ffmpeg 运行环境 ...", "#1a5fb4")))
            st["q"].put(("progress_reset", None))

            # 只有真的开始下载了才把「停止」变成「取消下载」——
            # 本机已有 ffmpeg 时这条路径根本不走，不该闪一下按钮。
            # progress 只在下载过程中被调用，正好当这个信号用。
            started = {"v": False}

            def _prog(done, total, speed):
                if not started["v"]:
                    started["v"] = True
                    st["downloading"] = True
                    st["q"].put(("dl_state", True))
                st["q"].put(("progress", (done, total, speed)))

            # 下载落点 = exe 同目录（开发时是项目目录）
            path, desc = ffmpeg_fetch.ensure_ffmpeg(
                dest_dir=core.APP_DIR,
                locate=core.find_ffmpeg,
                log=lambda m: st["q"].put(("log", (m, False))),
                progress=_prog,
                cancel=cancel_ev.is_set,
            )
        except Exception as e:
            path, desc = None, T("环境自检失败：%s") % e
        finally:
            st["downloading"] = False
            st["q"].put(("dl_state", False))

        try:
            st["ffmpeg_path"] = path
            st["ffmpeg_ok"] = bool(path)

            if path:
                st["q"].put(("log", (T("ffmpeg 就绪：%s") % path, False)))
            else:
                st["q"].put(("log", (T("没有可用的 ffmpeg —— 投屏会失败。"), True)))
                st["q"].put(("log", (desc, True)))
                st["q"].put(("log", (
                    T("可以这样解决：① 装一份 ffmpeg 并把它的 bin 目录加进 PATH；"
                      "② 设环境变量 FFMPEG_PATH 指向 ffmpeg.exe；"
                      "③ 直接把 ffmpeg.exe 放到本程序旁边（%s）")
                    % core.APP_DIR, True)))

            ip = core.get_local_ip()
            sz = core.get_desktop_size()
            if not path:
                ff_desc = T("未找到 ffmpeg")
            elif desc.startswith("本机已有"):
                ff_desc = T("使用本机 ffmpeg")
            else:
                ff_desc = T("已下载 ffmpeg")
            # 端口可能被 Windows 的保留区间吞掉（见 screen-cast.start_http_server），
            # 所以自检就按「实际会绑上的端口」显示，免得给出一个连不上的地址。
            try:
                _port = core.pick_http_port(core.DEFAULT_HTTP_PORT)
            except Exception:
                _port = core.DEFAULT_HTTP_PORT
            st["q"].put(("env", T("本机 %s   ·   桌面 %s   ·   端口 %d   ·   %s")
                         % (ip, ("%d×%d" % sz) if sz else T("未知"),
                            _port, ff_desc)))
            if _port != core.DEFAULT_HTTP_PORT:
                st["q"].put(("log", (T("端口 %d 不可用，将自动改用 %d")
                                     % (core.DEFAULT_HTTP_PORT, _port), False)))
            st["q"].put(("log", (T("本机 IP %s / 桌面 %s / 端口 %d")
                                 % (ip, sz, _port), False)))
            # 代理的 TUN 模式会吃掉 DLNA 组播 —— 开机自检就提醒，别等扫不到才说
            try:
                _tuns = core.detect_tun_adapters()
            except Exception:
                _tuns = []
            if _tuns:
                st["q"].put(("log", (
                    T("★ 检测到代理 TUN 虚拟网卡「%s」在运行。"
                      "若扫描不到电视，请把局域网加入代理绕过，或临时关闭 TUN 模式。")
                    % "、".join(_tuns), False)))
            if path:
                st["q"].put(("status", ("就绪 —— 先扫描设备，再点「开始投屏」", "#1a5fb4")))
            else:
                st["q"].put(("status", ("缺少 ffmpeg，无法投屏", "#a30000")))
        except Exception as e:
            import traceback
            st["q"].put(("log", (T("环境自检出错：%s") % e, True)))
            st["q"].put(("log", (traceback.format_exc(), True)))
            st["q"].put(("status", ("自检失败", "#a30000")))
        finally:
            st["q"].put(("progress_reset", None))
            core.remove_log_sink(sink)

    ui["progress"].configure(mode="determinate", maximum=100, value=0)
    log_line("DLNA 屏幕投屏器已启动。")
    log_line("正在查找 ffmpeg：先看环境变量 FFMPEG_PATH，再看本程序目录，"
             "都没有才联网下载（约 36 MB，只下这一次）。")
    root.after(120, drain)
    threading.Thread(target=prepare, daemon=True).start()
    root.mainloop()
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    _ensure_streams()
    # 语言必须在建界面之前定下来：控件文案是构造时求值的，晚一步就晚了。
    i18n.set_lang(i18n.resolve(argv))

    # 带参数 → 命令行形态：临时开控制台，走原始命令行逻辑
    if argv:
        _alloc_console()
        _enable_dpi_awareness()
        core = load_core()
        return core.main(argv)

    # 不带参数 → 图形界面
    _enable_dpi_awareness()
    core = load_core()
    _install_crash_handler(core.CACHE_DIR)
    return build_gui(core)


if __name__ == "__main__":
    sys.exit(main())
