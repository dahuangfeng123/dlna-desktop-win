# -*- coding: utf-8 -*-
"""界面全流程测试：构造窗口 → 展开高级设置 → 开始投屏 → 停止。

用「替身 run_cast」替换真正的推流，不碰电视、不启 ffmpeg，但按钮状态、
日志泵、就绪回调、停止流程全都真实走一遍，并在三个关键状态各截一张图。
"""
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

ROOT = {}


class _RecTk(tk.Tk):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        ROOT["r"] = self


tk.Tk = _RecTk

import cast_gui  # noqa: E402

_here = os.path.dirname(os.path.abspath(__file__))
REAL = cast_gui.load_core()


def fake_run_cast(args, stop_event=None, ready_cb=None):
    """替身：模拟一次成功的投屏，直到收到停止信号。"""
    REAL.log("=" * 58)
    REAL.log(" DLNA 屏幕投屏器（测试替身，未真正推流）")
    REAL.log("=" * 58)
    REAL.log("本机 IP   : 192.168.1.100")
    REAL.log("画质      : 1080p  高画质")
    REAL.log("编码已输出 512.0 KB（编码器 x264 / 采集 ddagrab）")
    REAL.log("正在把画面推送到电视 ...")
    REAL.log("")
    REAL.log("=" * 58)
    REAL.log(" 投屏已开始：Android TV")
    REAL.log("=" * 58)
    if ready_cb:
        ready_cb({
            "local_ip": "192.168.1.100", "port": args.port,
            "stream_url": "http://192.168.1.100:%d/live.ts" % args.port,
            "tv_url": "http://192.168.1.100:%d/tv" % args.port,
            "device": "Android TV", "device_ip": "192.168.1.101",
            "encoder": "x264", "capture": "ddagrab",
        })
    for _ in range(200):
        if stop_event is not None and stop_event.is_set():
            break
        time.sleep(0.05)
    REAL.log("已停止。")
    return 0


REAL.run_cast = fake_run_cast
cast_gui.load_core = lambda: REAL


def walk(w):
    yield w
    for c in w.winfo_children():
        for x in walk(c):
            yield x


def find_button(prefix):
    for w in walk(ROOT["r"]):
        try:
            if w.winfo_class() == "TButton" and str(w.cget("text")).startswith(prefix):
                return w
        except Exception:
            pass
    return None


def click(prefix, what):
    b = find_button(prefix)
    if b is None:
        print("[测试] ✗ 找不到按钮 %s" % what)
        return False
    if str(b.cget("state")) == "disabled":
        print("[测试] 按钮 %s 处于禁用状态，跳过" % what)
        return False
    b.invoke()
    print("[测试] 点击 %s" % what)
    return True


def shot(path):
    from PIL import ImageGrab
    r = ROOT["r"]
    r.update_idletasks()
    r.update()
    x, y = r.winfo_rootx(), r.winfo_rooty()
    w, h = r.winfo_width(), r.winfo_height()
    ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(path)
    print("[截图] %s" % os.path.basename(path))


def entries():
    return [w for w in walk(ROOT["r"]) if w.winfo_class() == "TEntry"]


_orig_mainloop = tk.Tk.mainloop


def _mainloop(self):
    def s_fill_ip():
        # 第 0 个 TEntry 就是「手动指定 IP」那个（高级设置里的输入框在它之后创建）
        e = entries()[0]
        e.insert(0, "192.168.1.101")
        print("[测试] 已填入电视 IP 192.168.1.101")

    def s_idle_adv():
        click("▸", "高级设置")
    def s_idle():
        shot(os.path.join(_here, "_shot1_idle.png"))
    def s_start():
        click("▶", "开始投屏")
    def s_running():
        shot(os.path.join(_here, "_shot2_running.png"))
    def s_stop():
        click("■", "停止")
    def s_stopped():
        shot(os.path.join(_here, "_shot3_stopped.png"))
    def s_quit():
        self.destroy()

    for ms, fn in [(600, s_fill_ip), (1200, s_idle_adv), (2100, s_idle),
                   (2400, s_start), (4200, s_running), (4500, s_stop),
                   (5300, s_stopped), (5700, s_quit)]:
        self.after(ms, fn)
    # 保底：万一有模态框卡住，20 秒后强退，别让测试挂死
    self.after(20000, self.destroy)
    _orig_mainloop(self)


tk.Tk.mainloop = _mainloop

try:
    code = cast_gui.main([])
    print("[测试] 正常退出，返回码 %s" % code)
except Exception:
    traceback.print_exc()
    print("[测试] 抛出异常")
    sys.exit(1)
