# -*- coding: utf-8 -*-
"""本机验证 /ws-live：握手 → 读二进制帧 → 检查首字节是不是 TS 同步字节。

只读 3 秒，够判断「帧在来、内容对」就行。
"""
import base64
import os
import socket
import struct
import sys
import time

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8900
SECS = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0

s = socket.create_connection(("127.0.0.1", PORT), timeout=10)
key = base64.b64encode(os.urandom(16)).decode()
s.sendall(("GET /ws-live HTTP/1.1\r\n"
           "Host: 127.0.0.1:%d\r\n"
           "Upgrade: websocket\r\n"
           "Connection: Upgrade\r\n"
           "Sec-WebSocket-Key: %s\r\n"
           "Sec-WebSocket-Version: 13\r\n\r\n" % (PORT, key)).encode())

buf = b""
while b"\r\n\r\n" not in buf:
    d = s.recv(4096)
    if not d:
        break
    buf += d
head, _, pending = buf.partition(b"\r\n\r\n")
status = head.decode("utf-8", "replace").splitlines()[0]
print("握手响应: %s" % status)
if "101" not in status:
    print(head.decode("utf-8", "replace"))
    sys.exit(1)

t0 = time.time()
frames = 0
total = 0
first_byte = None
sizes = []


def need(n):
    global pending
    while len(pending) < n:
        d = s.recv(65536)
        if not d:
            raise EOFError("对端关闭")
        pending += d


try:
    while time.time() - t0 < SECS:
        need(2)
        b1, b2 = pending[0], pending[1]
        op = b1 & 0x0F
        n = b2 & 0x7F
        idx = 2
        if n == 126:
            need(4)
            n = struct.unpack(">H", pending[2:4])[0]
            idx = 4
        elif n == 127:
            need(10)
            n = struct.unpack(">Q", pending[2:10])[0]
            idx = 10
        need(idx + n)
        payload = pending[idx:idx + n]
        pending = pending[idx + n:]
        frames += 1
        total += len(payload)
        if len(sizes) < 6:
            sizes.append(len(payload))
        if first_byte is None and payload:
            first_byte = payload[0]
        if op == 0x8:
            print("收到 close 帧")
            break
except EOFError as e:
    print("读帧提前结束：%s" % e)

dt = time.time() - t0
print("帧数      : %d" % frames)
print("累计字节  : %d 字节 (%.2f MB)" % (total, total / 1048576.0))
print("首字节    : %s%s" % (hex(first_byte) if first_byte is not None else "无",
                           "  ← 0x47 = TS 同步字节 ✓"
                           if first_byte == 0x47 else ""))
print("前几帧大小: %s" % sizes)
print("耗时      : %.2f s   平均 %.2f Mbps"
      % (dt, total * 8 / max(0.001, dt) / 1e6))
ok = frames > 0 and first_byte == 0x47
print()
print("结论: %s" % ("✓ WebSocket 推流正常，电视可以试了"
                    if ok else "✗ 数据不对，需要排查"))
sys.exit(0 if ok else 2)
