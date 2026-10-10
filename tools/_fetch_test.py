# -*- coding: utf-8 -*-
"""实测 ffmpeg_fetch 的两条路径：①本机已有 ②自动下载。"""
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import ffmpeg_fetch as F

DEST = os.path.join(HERE, "_ffmpeg_test")


def log(m):
    print(m, flush=True)


def prog(done, total, speed):
    if not total:
        sys.stdout.write("\r    %6.1f MB" % (done / 1048576))
    else:
        pct = done * 100 // max(1, total)
        sys.stdout.write("\r    %3d%%  %6.1f / %.1f MB  %.2f MB/s"
                         % (pct, done / 1048576, total / 1048576,
                            speed / 1048576))
    sys.stdout.flush()


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else "both"

    if what in ("locate", "both"):
        print("=== 路径A：本机已有 ffmpeg（locate 能返回路径）===")
        t0 = time.time()
        p, src = F.ensure_ffmpeg(DEST, locate=lambda: r"D:\ffmpeg\ffmpeg.exe",
                                 log=log, progress=prog)
        print("  -> %s" % p)
        print("  -> 来源：%s" % src)
        print("  -> 用时 %.3f s" % (time.time() - t0))

    if what in ("download", "both"):
        print()
        print("=== 路径B：本机没有（locate 返回 None）→ 自动下载 ===")
        if os.path.isdir(DEST):
            shutil.rmtree(DEST, ignore_errors=True)
        t0 = time.time()
        p, src = F.ensure_ffmpeg(DEST, locate=lambda: None,
                                 log=log, progress=prog)
        print()
        print("  -> %s" % p)
        print("  -> 来源：%s" % src)
        print("  -> 总用时 %.1f s" % (time.time() - t0))
        if p and os.path.isfile(p):
            print("  -> 文件大小 %.1f MB" % (os.path.getsize(p) / 1048576))
            print("  -> 同目录残留：%s"
                  % [f for f in os.listdir(DEST) if not f.endswith(".exe")])


if __name__ == "__main__":
    main()
