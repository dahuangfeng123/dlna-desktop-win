# -*- coding: utf-8 -*-
"""测 GitHub 单连接 vs 多连接的聚合吞吐。

如果限速是「每连接」的，多连接并行就能几倍提速 —— 这直接决定
要不要给下载器加并发。
"""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import ffmpeg_fetch as F

WINDOW = 10.0        # 每轮读多久
CHUNK = 256 * 1024


def worker(url, start, stop_evt, box, idx):
    n = 0
    try:
        req = F._request(url, {"Range": "bytes=%d-%d" % (start, start + 32 * 1048576)})
        with F._urlopen(req, timeout=60) as r:
            while not stop_evt.is_set():
                b = r.read(CHUNK)
                if not b:
                    break
                n += len(b)
    except Exception as e:
        box["err%d" % idx] = repr(e)
    box["n%d" % idx] = n


def measure(url, conns, base=0):
    stop_evt = threading.Event()
    box = {}
    ts = []
    for i in range(conns):
        # 每连接取文件不同区段，避免服务器端合并/缓存
        start = base + i * 40 * 1048576
        t = threading.Thread(target=worker, args=(url, start, stop_evt, box, i),
                             daemon=True)
        t.start()
        ts.append(t)
    t0 = time.time()
    time.sleep(WINDOW)
    stop_evt.set()
    for t in ts:
        t.join(timeout=8)
    el = time.time() - t0
    total = sum(box.get("n%d" % i, 0) for i in range(conns))
    errs = {k: v for k, v in box.items() if k.startswith("err")}
    return total, el, total / el, errs


def main():
    src = F.resolve_source(None)
    url = src["url"]
    print("url = %s" % url)
    print("窗口 = %.0f 秒/轮" % WINDOW)
    print()
    rows = []
    for n in (1, 4, 8):
        total, el, rate, errs = measure(url, n, base=1024)
        print("  并发 %d：%.1f MB / %.1f s  ->  %6.3f MB/s%s"
              % (n, total / 1048576, el, rate / 1048576,
                 ("  错误 %s" % errs) if errs else ""))
        rows.append((n, rate))
        time.sleep(2)
    print()
    base = rows[0][1]
    for n, rate in rows:
        print("  并发 %d 相对单连接：%.2fx" % (n, rate / max(1e-9, base)))


if __name__ == "__main__":
    main()
