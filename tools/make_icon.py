# -*- coding: utf-8 -*-
"""生成应用图标 assets/app.ico。

用代码画而不是塞一个二进制图片进仓库：形状改起来就是改几个数字，
也不会出现"这张图哪来的"的问题。跑一次生成多尺寸 ico。

    python tools/make_icon.py
"""
import os
import sys

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "assets", "app.ico")

S = 256                      # 画布边长，Pillow 会自动缩出其他尺寸
BG_TOP = (26, 54, 92)        # 深蓝
BG_BOTTOM = (16, 33, 58)
FG = (255, 255, 255)
ACCENT = (94, 203, 133)      # 投屏成功感的绿


def rounded_rect_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def vertical_gradient(size, top, bottom):
    img = Image.new("RGB", (1, size))
    px = img.load()
    for y in range(size):
        t = y / max(1, size - 1)
        px[0, y] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return img.resize((size, size), Image.BILINEAR)


def build():
    bg = vertical_gradient(S, BG_TOP, BG_BOTTOM).convert("RGBA")
    bg.putalpha(rounded_rect_mask(S, int(S * 0.22)))

    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # 显示器：外框 + 支架
    mx0, my0, mx1, my1 = int(S * 0.22), int(S * 0.24), int(S * 0.78), int(S * 0.60)
    d.rounded_rectangle([mx0, my0, mx1, my1], radius=int(S * 0.045),
                        outline=FG, width=max(2, int(S * 0.035)))
    stand_w = int(S * 0.09)
    d.rectangle([S // 2 - stand_w // 2, my1, S // 2 + stand_w // 2,
                 my1 + int(S * 0.05)], fill=FG)
    d.rounded_rectangle([S // 2 - int(S * 0.16), my1 + int(S * 0.05),
                         S // 2 + int(S * 0.16), my1 + int(S * 0.085)],
                        radius=int(S * 0.015), fill=FG)

    # 投屏信号：左下角一个圆点 + 两道弧，越大越淡
    cx, cy = int(S * 0.335), int(S * 0.715)
    d.ellipse([cx - int(S * 0.035), cy - int(S * 0.035),
               cx + int(S * 0.035), cy + int(S * 0.035)], fill=ACCENT)
    for i, r in enumerate((0.10, 0.165, 0.23)):
        rr = int(S * r)
        alpha = 235 - i * 55
        d.arc([cx - rr, cy - rr, cx + rr, cy + rr], start=270, end=360,
              fill=ACCENT + (alpha,), width=max(2, int(S * 0.028)))

    out = Image.alpha_composite(bg, layer)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    out.save(OUT, format="ICO",
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                    (128, 128), (256, 256)])
    return OUT


if __name__ == "__main__":
    p = build()
    print("已生成 %s  (%d 字节)" % (p, os.path.getsize(p)))
    sys.exit(0)
