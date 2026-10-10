# -*- coding: utf-8 -*-
"""直连 /live.ts，看服务端到底吐不吐健康的 MPEG-TS。

为什么必须自己写 socket 探测：
  MEMORY 里记过一条测量陷阱 —— 用 http.client / urllib 的 read(65536)
  会**阻塞读满**才返回，把平滑流伪装成周期性停顿；而且它会把
  "连接被对端关闭" 和 "正常读完" 混成同一件事。所以这里用原始 socket
  配小 recv()，逐块记录到达时刻，并单独检测 EOF。

用法：
    python tools/_tv_probe.py            # 读 8 秒
    python tools/_tv_probe.py 15         # 读 15 秒
"""
import socket
import sys
import time
from collections import Counter

HOST = "127.0.0.1"
PORT = 8899
PKT = 188
SYNC = 0x47
VIDEO_PID_HINT = (0x100, 0x1000, 0x1011)   # 常见编码器输出 PID


def recv_until_close(dur):
    """返回 (raw_bytes, marks, hdr_end, eof_at)。marks = [(相对秒, 累计字节)]"""
    s = socket.create_connection((HOST, PORT), timeout=6)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    req = ("GET /live.ts HTTP/1.0\r\n"
           "Host: %s:%d\r\n"
           "User-Agent: tv-probe/1\r\n"
           "Accept: */*\r\n\r\n" % (HOST, PORT))
    s.sendall(req.encode())

    raw = bytearray()
    marks = []
    t0 = time.time()
    hdr_end = None
    eof_at = None
    try:
        while time.time() - t0 < dur:
            try:
                d = s.recv(4096)
            except socket.timeout:
                continue
            except OSError as e:
                print("    recv 出错：%s" % e)
                break
            now = time.time() - t0
            if not d:
                eof_at = now
                break
            raw += d
            marks.append((now, len(raw)))
            if hdr_end is None:
                i = raw.find(b"\r\n\r\n")
                if i >= 0:
                    hdr_end = i + 4
                    print("=== 响应头（%.3f s 到达）===" % now)
                    print(bytes(raw[:hdr_end]).decode("latin-1").rstrip())
                    print()
    finally:
        try:
            s.close()
        except Exception:
            pass
    return bytes(raw), marks, hdr_end, eof_at


def analyse(body, marks, hdr_end, eof_at, dur):
    if hdr_end is None:
        print("!! %.0f 秒内连响应头都没收到（收到 %d 字节）" % (dur, len(body)))
        print("   说明服务端建连后什么都没发 —— 不是 None/空包问题，是根本没走这条路")
        return

    body = body[hdr_end:]
    npkt = len(body) // PKT
    print("=== 体量 ===")
    print("总 body      : %d 字节" % len(body))
    print("完整 TS 包   : %d（%d 字节）" % (npkt, npkt * PKT))
    print("尾部零头     : %d 字节 %s"
          % (len(body) - npkt * PKT,
             "（正常，收流中途截断）" if len(body) % PKT else "（正好整包）"))
    if eof_at:
        print("★ 服务端在 %.2f s 主动关闭了连接（EOF）" % eof_at)
        print("   → /live.ts 循环退出的唯一路径是 bc.read() 返回 None，")
        print("     即 ffmpeg 已结束（running=False）且缓冲已排空")
    else:
        print("连接在 %.0f s 内一直保持打开（未 EOF）" % dur)

    if not npkt:
        print()
        print("!! 收到 %d 字节 body 但连一个完整 TS 包都不够" % len(body))
        if body[:64]:
            print("   前 64 字节：%r" % body[:64])
        return

    # ---- 同步字节 ----
    print()
    print("=== TS 结构 ===")
    lim = min(npkt, 4000)
    bad = [i for i in range(lim) if body[i * PKT] != SYNC]
    print("前 %d 包同步字节 0x47 ：%s"
          % (lim, "全部正确 ✓" if not bad else
             "★ %d 个错位（首批错位在第 %d 包）" % (len(bad), bad[0])))

    pids = Counter()
    for i in range(npkt):
        p = body[i * PKT:(i + 1) * PKT]
        pids[((p[1] & 0x1F) << 8) | p[2]] += 1
    print("PID 分布（前 6）     : %s"
          % ", ".join("0x%03X×%d" % (k, v) for k, v in pids.most_common(6)))
    print("含 PAT(0x000)        : %s" % ("是" if pids.get(0) else "★ 否"))
    print("含 PMT(0x1000/0x100): %s"
          % ("是" if (pids.get(0x1000) or pids.get(0x100)) else "?"))
    print("唯一 PID 数          : %d" % len(pids))

    # ---- 关键帧 ----
    vid = None
    for cand in VIDEO_PID_HINT:
        if pids.get(cand, 0) > npkt * 0.5:
            vid = cand
            break
    if vid is None and pids:
        vid = pids.most_common(1)[0][0]
    if vid is not None:
        has_idr = False
        for i in range(min(npkt, 400)):
            p = body[i * PKT:(i + 1) * PKT]
            if ((p[1] & 0x1F) << 8 | p[2]) != vid:
                continue
            # 只看含 PES 起始标记的包，避免误判负载里的 0x65
            if p[1] & 0x40 and p[4:7] == b"\x00\x00\x01":
                if b"\x00\x00\x01\x65" in p:
                    has_idr = True
                    print("首个 IDR 关键帧       : 第 %d 包（%.0f KB 处）"
                          % (i, i * PKT / 1024.0))
                    break
        if not has_idr:
            print("首个 IDR 关键帧       : ★ 前 %d 包内没找到（可能落在更后面）"
                  % min(npkt, 400))

    # ---- 到达节奏 ----
    print()
    print("=== 到达节奏（按秒汇总）===")
    per_sec = {}
    prev = 0
    for t, n in marks:
        k = int(t)
        per_sec[k] = n - prev if k not in per_sec else per_sec[k] + (n - prev)
        prev = n
    for k in sorted(per_sec):
        b = per_sec[k]
        print("  第 %2d 秒 : %8d 字节  （%5.2f Mbps）" % (k, b, b * 8 / 1000.0))

    if marks:
        print()
        print("首字节到达   : %.3f s（头部之后）"
              % max(0.0, marks[0][0] - 0.0))
        print("平均速率     : %.2f Mbps"
              % (len(body) * 8 / max(0.001, marks[-1][0]) / 1000.0))


def main():
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0
    print("直连 http://%s:%d/live.ts ，采 %.0f 秒" % (HOST, PORT, dur))
    print("-" * 62)
    body, marks, hdr_end, eof_at = recv_until_close(dur)
    analyse(body, marks, hdr_end, eof_at, dur)
    print("-" * 62)


if __name__ == "__main__":
    main()
