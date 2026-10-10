# -*- coding: utf-8 -*-
"""用原始 socket 重测接收节奏，排除 urllib 读满缓冲造成的测量假象。"""
import os
import socket
import statistics
import subprocess
import sys
import threading
import time

# 默认用当前解释器；需要指定别的版本时设 DLNACAST_PYTHON
PY = os.environ.get("DLNACAST_PYTHON", sys.executable)
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8931
DUR = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
EXTRA = sys.argv[3:]

proc = subprocess.Popen([PY, "screen-cast.py", "--no-push",
                         "--port", str(PORT)] + EXTRA,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace",
                        bufsize=1)
logs = []
threading.Thread(target=lambda: [logs.append(l.rstrip())
                                 for l in proc.stdout], daemon=True).start()
time.sleep(6)

s = socket.create_connection(("127.0.0.1", PORT), timeout=10)
s.sendall(b"GET /live.ts HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
s.settimeout(5)

# 先吃掉 HTTP 头
buf = b""
t0 = time.time()
while b"\r\n\r\n" not in buf and time.time() - t0 < 5:
    buf += s.recv(4096)
head, rest = buf.split(b"\r\n\r\n", 1)
print("HTTP 头: %s" % head.decode("latin1").strip().replace("\r\n", " | "))

total = len(rest)
stamps = [time.time() - t0]
t0 = time.time()
while time.time() - t0 < DUR:
    try:
        b = s.recv(32768)
    except socket.timeout:
        stamps.append(time.time() - t0)
        continue
    if not b:
        break
    total += len(b)
    stamps.append(time.time() - t0)
el = time.time() - t0
s.close()
try:
    proc.terminate()
    proc.wait(timeout=10)
except Exception:
    proc.kill()

gaps = [stamps[i + 1] - stamps[i] for i in range(len(stamps) - 1)]
gaps = [g for g in gaps if g >= 0]
print("\n[原始 socket 接收]")
print("  总量 %.1f KB / %.1f 秒 = %.0f kbps"
      % (total / 1024.0, el, total * 8 / el / 1000.0))
print("  数据块 %d 个，平均每块 %.1f ms" % (len(stamps), el / max(1, len(stamps)) * 1000))
if gaps:
    print("  到达间隔: 中位 %.0f ms | 平均 %.0f ms | 最大 %.0f ms | 标准差 %.0f ms"
          % (statistics.median(gaps) * 1000, statistics.mean(gaps) * 1000,
             max(gaps) * 1000, statistics.pstdev(gaps) * 1000))
    for th in (0.1, 0.2, 0.3, 0.5):
        n = sum(1 for g in gaps if g > th)
        print("    停顿 >%3.0f ms: %2d 次" % (th * 1000, n))
print("\n[程序日志]")
for l in logs:
    if "[stream]" in l or "延迟参数" in l or "编码器" in l:
        print("  " + l)
