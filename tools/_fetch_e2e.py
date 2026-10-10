# -*- coding: utf-8 -*-
"""端到端实测：单成员分段下载 → 解压 → CRC → -version → 能力检查。
顺带验证断点续传：第一次限时中止，第二次应当接着下。

    python tools/_fetch_e2e.py abort 60     # 下 60 秒后中止
    python tools/_fetch_e2e.py finish       # 接着下完
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import ffmpeg_fetch as F

DEST = os.path.join(HERE, "_ffmpeg_test")


def show_parts():
    if not os.path.isdir(DEST):
        return []
    out = []
    for f in sorted(os.listdir(DEST)):
        p = os.path.join(DEST, f)
        out.append((f, os.path.getsize(p)))
    return out


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "finish"
    limit = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0

    print("=== 目录 %s ===" % DEST)
    before = show_parts()
    if before:
        print("  开始前已有文件：")
        for n, s in before:
            print("    %-40s %8.2f MB" % (n, s / 1048576))
        resume_bytes = sum(s for n, s in before if ".p" in n)
        print("  其中可续传的分段数据：%.2f MB" % (resume_bytes / 1048576))
    else:
        print("  （空目录，从头下）")

    t_deadline = time.time() + limit if limit > 0 else None

    def cancel():
        return t_deadline is not None and time.time() > t_deadline

    def log(m):
        print(m, flush=True)

    def prog(done, total, speed):
        pct = done * 100 // max(1, total)
        eta = (total - done) / max(0.001, speed)
        sys.stdout.write("\r    %3d%%  %6.2f / %.1f MB  %.2f MB/s  剩余约 %3.0f s"
                         % (pct, done / 1048576, total / 1048576,
                            speed / 1048576, eta))
        sys.stdout.flush()

    t0 = time.time()
    try:
        p, src = F.ensure_ffmpeg(DEST, locate=lambda: None, log=log,
                                 progress=prog, cancel=cancel)
    except Exception as e:
        p, src = None, "异常：%s" % e
    print()
    print("  用时 %.1f 秒" % (time.time() - t0))
    print("  -> 路径：%s" % p)
    print("  -> 来源：%s" % src)
    after = show_parts()
    print("  结束后目录内容：")
    for n, s in after:
        print("    %-40s %8.2f MB" % (n, s / 1048576))


if __name__ == "__main__":
    main()
