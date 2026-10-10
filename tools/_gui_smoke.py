# -*- coding: utf-8 -*-
"""界面冒烟测试：真的把窗口开起来、展开高级设置、截图、再关掉。

不点「开始投屏」——那会真往电视推流。这里只验证界面构造、
自检线程、日志泵、折叠展开这些不出异常，并留一张截图供人眼复核。
"""
import os
import sys
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


def find_button(text_prefix):
    for w in walk(ROOT["r"]):
        try:
            if w.winfo_class() == "TButton" and str(w.cget("text")).startswith(text_prefix):
                return w
        except Exception:
            pass
    return None


def shot(path):
    """按窗口句柄抓图。

    不能用 ImageGrab 抓屏幕坐标 —— 窗口不在最前时会拍到别的程序
    （实测抓到过 IDE 和浏览器）。PrintWindow 不受遮挡影响。
    """
    r = ROOT["r"]
    r.update_idletasks()
    r.update()
    import _winshot
    wins = _winshot.find_window("DLNA 屏幕投屏器")
    if not wins:
        print("[截图跳过] 没找到窗口")
        return
    w, h = _winshot.grab(wins[0][0], path)
    print("[截图] %s  %dx%d" % (path, w, h))


out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_gui_shot.png")

_orig_mainloop = tk.Tk.mainloop


def _mainloop(self):
    def step_expand():
        b = find_button("▸")
        if b:
            print("[测试] 展开高级设置")
            b.invoke()
        else:
            print("[测试] 没找到高级设置按钮")

    def step_shot():
        try:
            shot(out)
        except Exception as e:
            print("[截图失败] %s" % e)

    self.after(1500, step_expand)
    self.after(2600, step_shot)
    self.after(3400, self.destroy)
    _orig_mainloop(self)


tk.Tk.mainloop = _mainloop

try:
    code = cast_gui.main([])
    print("[测试] 主界面正常退出，返回码 %s" % code)
except Exception:
    traceback.print_exc()
    print("[测试] 界面抛出异常")
    sys.exit(1)
