# -*- coding: utf-8 -*-
"""验证「下载 ffmpeg」在界面上的接线（用替身，不真下载）。

真下 100 MB 太慢，而这里要验证的只是管线：
    进度回调 → 状态栏文字 / 进度条
    下载开始 → 「停止」按钮变成「取消下载」并可点
    点「取消下载」→ 取消标志置位
    下载结束 → 按钮复原

替身 tk.Tk，不弹真窗口。
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


def walk(w):
    yield w
    for c in w.winfo_children():
        for x in walk(c):
            yield x


def find(pred):
    for w in walk(ROOT["r"]):
        try:
            if pred(w):
                return w
        except Exception:
            pass
    return None


def texts():
    out = {}
    for w in walk(ROOT["r"]):
        try:
            cls = w.winfo_class()
            t = str(w.cget("text"))
            if cls == "TButton":
                out.setdefault("btn", []).append(t)
            elif cls == "TLabel" and t:
                out.setdefault("lbl", []).append(t)
            elif cls == "TProgressbar":
                out["prog"] = (w.cget("mode"), w.cget("value"),
                               w.cget("maximum"))
        except Exception:
            pass
    return out


FAKE = os.path.join(os.environ.get("TEMP", "."), "fake_ffmpeg.exe")

# 替身：假装在下载，中途推进度；被取消就提前返回
state = {"cancelled": False}


def fake_ensure(dest_dir=None, locate=None, log=None, progress=None,
                cancel=None, **kw):
    log("本机没有找到 ffmpeg，准备从 GitHub 下载最新版 ...")
    log("  最新版本 9.0.2，包 ffmpeg-9.0.2-essentials_build.zip（109.5 MB）")
    total = 35 * 1048576
    for i in range(1, 11):
        if cancel and cancel():
            state["cancelled"] = True
            log("  按范围下载失败（已取消），改为整包下载")
            return None, "下载失败：已取消"
        progress(i * total // 10, total, 240000.0)
        time.sleep(0.25)
    log("  下载完成 35.0 MB，用时 146 秒（平均 0.24 MB/s）")
    log("  ffmpeg version 9.0.2-essentials_build")
    log("  能力检查通过：libx264 / h264_mf / gdigrab / drawtext / mpegts 都在")
    return FAKE, "已下载最新版"


cast_gui.ffmpeg_fetch.ensure_ffmpeg = fake_ensure

steps = {"n": 0}


def report(tag):
    t = texts()
    btn_stop = [b for b in t.get("btn", []) if "停止" in b or "取消" in b]
    st = [l for l in t.get("lbl", []) if ("下载" in l or "就绪" in l
                                          or "ffmpeg" in l)]
    print("  [%s] 停止按钮=%s  进度=%s" % (tag, btn_stop, t.get("prog")))
    for l in st[-4:]:
        print("        文案：%s" % l)


def driver():
    def step1():
        report("下载中")
        b = find(lambda w: w.winfo_class() == "TButton"
                 and "取消" in str(w.cget("text")))
        if not b:
            print("  !! 没找到「取消下载」按钮 —— 按钮没切换")
            print("     当前所有按钮：%s" % texts().get("btn"))
        else:
            print("  点「取消下载」...")
            if str(b.cget("state")) != "normal":
                print("  !! 按钮不可点：state=%s" % b.cget("state"))
            b.invoke()

    def step2():
        report("已取消")
        print("  取消标志是否置位：%s" % state["cancelled"])

    def step3():
        report("下载结束后")
        print("  替身是否收到取消：%s" % state["cancelled"])
        ROOT["r"].destroy()

    ROOT["r"].after(900, step1)
    ROOT["r"].after(1800, step2)
    ROOT["r"].after(3600, step3)


_orig_mainloop = tk.Tk.mainloop


def _mainloop(self):
    driver()
    _orig_mainloop(self)


tk.Tk.mainloop = _mainloop

try:
    code = cast_gui.main([])
    print("[测试] 返回码 %s" % code)
except Exception:
    traceback.print_exc()
    sys.exit(1)
