# -*- coding: utf-8 -*-
"""按窗口标题抓图：不依赖窗口是否在最前面，也不抢焦点。

整屏截图会拍到别的窗口（IDE、浏览器），验证界面时不可靠；
FindWindowW + PrintWindow 能精确抓某一个窗口，哪怕是背景窗口。

    python tools/_winshot.py "DLNA 屏幕投屏器" out.png
"""
import ctypes
import sys
from ctypes import wintypes

from PIL import Image

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

PW_RENDERFULLCONTENT = 0x00000002
SRCCOPY = 0x00CC0020


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def find_window(title_part):
    """按标题子串找顶层可见窗口。"""
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if title_part in buf.value:
            found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(_cb, 0)
    return found


def grab(hwnd, path):
    rect = wintypes.RECT()
    # DwmGetWindowAttribute(9) 拿的是不含投影的真实边框
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), 9, ctypes.byref(rect),
            ctypes.sizeof(rect)) != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    if w <= 0 or h <= 0:
        raise RuntimeError("窗口尺寸异常 %dx%d" % (w, h))

    hdc = user32.GetWindowDC(hwnd)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(memdc, bmp)
    try:
        # 先试 PW_RENDERFULLCONTENT（能抓到 DirectComposition 内容），
        # 失败就退回 BitBlt
        ok = user32.PrintWindow(hwnd, memdc, PW_RENDERFULLCONTENT)
        if not ok:
            gdi32.BitBlt(memdc, 0, 0, w, h, hdc, 0, 0, SRCCOPY)

        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = w
        bi.bmiHeader.biHeight = -h          # 负数 = 自上而下
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = 0
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(memdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
        img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1)
        img.convert("RGB").save(path)
        return w, h
    finally:
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(memdc)
        user32.ReleaseDC(hwnd, hdc)


if __name__ == "__main__":
    title = sys.argv[1]
    out = sys.argv[2]
    wins = find_window(title)
    if not wins:
        print("没找到标题含 %r 的窗口" % title)
        sys.exit(1)
    for hwnd, name in wins:
        print("窗口: %r hwnd=%s" % (name, hwnd))
    w, h = grab(wins[0][0], out)
    print("已保存 %s  %dx%d" % (out, w, h))
