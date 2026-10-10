# -*- coding: utf-8 -*-
"""①修正区段越界后重测并发 ②对比几个下载源的吞吐。"""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import ffmpeg_fetch as F

WINDOW = 8.0
CHUNK = 256 * 1024


def worker(url, start, span, stop_evt, box, idx, plain=False):
    n = 0
    try:
        hdr = {} if plain else {"Range": "bytes=%d-%d" % (start, start + span - 1)}
        with F._urlopen(F._request(url, hdr), timeout=60) as r:
            while not stop_evt.is_set():
                b = r.read(CHUNK)
                if not b:
                    break
                n += len(b)
    except Exception as e:
        box["err%d" % idx] = "%s" % e
    box["n%d" % idx] = n


def measure(url, conns, span, url_size, plain=False):
    stop_evt = threading.Event()
    box = {}
    stride = max(1, (url_size - span - 2) // max(1, conns))
    ts = []
    for i in range(conns):
        start = 1 + i * stride
        if start + span > url_size:
            start = max(1, url_size - span - 1)
        t = threading.Thread(target=worker,
                             args=(url, start, span, stop_evt, box, i, plain),
                             daemon=True)
        t.start()
        ts.append(t)
    t0 = time.time()
    time.sleep(WINDOW)
    stop_evt.set()
    for t in ts:
        t.join(timeout=6)
    el = max(0.001, time.time() - t0)
    total = sum(box.get("n%d" % i, 0) for i in range(conns))
    errs = {k: v for k, v in box.items() if k.startswith("err")}
    ok = conns - len(errs)
    return total, el, total / el, errs, ok


def head_size(url):
    try:
        req = F._request(url, {"Range": "bytes=0-0"})
        with F._opener().open(req, timeout=30) as r:
            cr = r.headers.get("Content-Range", "")
            if "/" in cr:
                return int(cr.split("/")[-1])
            cl = r.headers.get("Content-Length")
            return int(cl) if cl else 0
    except Exception:
        return 0


def bench_source(label, url):
    size = head_size(url)
    if not size:
        print("  %-14s  HEAD 失败或不可按范围读取" % label)
        return None
    rows = []
    for n in (1, 4):
        span = min(8 * 1048576, max(1048576, size // 20))
        total, el, rate, errs, ok = measure(url, n, span, size)
        print("  %-14s 并发 %d（有效 %d）：%6.3f MB/s%s"
              % (label, n, ok, rate / 1048576,
                 ("  错误 %d 个" % len(errs)) if errs else ""))
        rows.append(rate)
        time.sleep(1.5)
    if len(rows) == 2 and rows[0] > 0:
        print("  %-14s 并发增益：%.2fx" % ("", rows[1] / rows[0]))
    return rows


def main():
    src = F.resolve_source(None)
    print("=== 修正区段后，GitHub 上的并发对比 ===")
    bench_source("GitHub", src["url"])

    print()
    print("=== 其他源的吞吐（每源只测单连接）===")
    others = [
        ("gyan.dev", "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"),
        ("清华镜像", "https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ls-lR.gz"),
        ("pypi", "https://files.pythonhosted.org/packages/source/s/six/six-1.16.0.tar.gz"),
    ]
    for label, url in others:
        try:
            size = head_size(url)
            if not size:
                print("  %-10s 不可用（无 Range 或无长度）" % label)
                continue
            total, el, rate, errs, ok = measure(url, 1,
                                                min(8 * 1048576, size), size)
            print("  %-10s 单连接：%6.3f MB/s  （文件 %.1f MB）"
                  % (label, rate / 1048576, size / 1048576))
        except Exception as e:
            print("  %-10s 失败：%s" % (label, e))
        time.sleep(1)


if __name__ == "__main__":
    main()
