#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dlna-probe.py — DLNA 投屏能力诊断工具

用途：
  1) 扫描局域网内所有 UPnP 渲染设备，输出能力报告
  2) 把一个本地媒体文件通过"带 Range 支持"的 HTTP 服务投送给电视，
     并完整记录电视发出的每一个 HTTP 请求 —— 用于判断电视的 DLNA
     渲染器到底能不能播放视频。

用法：
    python dlna-probe.py                      # 只做能力报告
    python dlna-probe.py --file test.mp4      # 投送文件并观察电视请求
    python dlna-probe.py --file test.mp4 --device 客厅
    python dlna-probe.py --file test.mp4 --wait 40
"""

import argparse
import importlib.util
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SC_PATH = os.path.join(HERE, "screen-cast.py")

_spec = importlib.util.spec_from_file_location("sc", SC_PATH)
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)

MIME = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".ts": "video/mp2t",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".mov": "video/quicktime",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".mp3": "audio/mpeg",
}


def banner(t):
    print("")
    print("=" * 62)
    print(" " + t)
    print("=" * 62)


# ------------------------------------------------------------ 能力报告


def scan_report():
    lip = sc.get_local_ip()
    banner("DLNA 设备能力报告")
    print("本机 IP: %s" % lip)
    print("")
    devs = sc.discover_devices(lip, 6, verify=False)
    print("发现 %d 个 UPnP 设备实体" % len(devs))
    for d in devs:
        print("-" * 62)
        print("名称    : %s" % d.name)
        print("地址    : %s" % d.ip)
        print("URLBase : %s" % d.url_base)
        print("UDN     : %s" % d.udn)
        svcs = sorted(d.services.keys())
        print("服务    : %s" % ", ".join(svcs))
        if "MediaRenderer" in d.name or "电视" in d.name or "AVTransport" in d.services:
            avt = d.services.get("AVTransport")
            ok = "N/A"
            if avt:
                try:
                    r = sc.soap_call(avt[1], avt[0], "GetTransportInfo",
                                     "<InstanceID>0</InstanceID>", timeout=3)
                    ok = "可控制" if "GetTransportInfoResponse" in r else "不支持"
                except Exception as e:
                    ok = "不可达 (%s)" % type(e).__name__
            print("AVTransport 可达性: %s" % ok)
            cm = d.services.get("ConnectionManager")
            if cm:
                try:
                    r = sc.soap_call(cm[1], cm[0], "GetProtocolInfo", "", timeout=4)
                    m = re.search(r"<Sink>(.*?)</Sink>", r, re.S)
                    if m and m.group(1):
                        sink = m.group(1).split(",")
                        vids = sorted({x.split(":")[2] for x in sink if ":video" in x})
                        print("支持视频格式(%d): %s" % (len(vids), ", ".join(vids)))
                    else:
                        print("Sink 协议列表: 空")
                except Exception as e:
                    print("GetProtocolInfo 失败: %s" % e)


# ------------------------------------------------------------ 文件投送


class ProbeState(object):
    def __init__(self):
        self.requests = []
        self.lock = threading.Lock()

    def add(self, line):
        with self.lock:
            self.requests.append(line)
        print(line, flush=True)


def make_file_server(path, port, state):
    fsize = os.path.getsize(path)
    ext = os.path.splitext(path)[1].lower()
    ctype = MIME.get(ext, "application/octet-stream")

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "Probe/1.0"

        def log_message(self, fmt, *args):
            pass

        def _serve(self, head=False):
            rng = self.headers.get("Range")
            ua = (self.headers.get("User-Agent") or "")[:70]
            state.add("  [REQ] %-4s %-28s Range=%-16s UA=%s"
                      % (self.command, self.path[:28], str(rng), ua))
            if rng:
                m = re.match(r"bytes=(\d*)-(\d*)", rng)
                s = int(m.group(1)) if m and m.group(1) else 0
                e = int(m.group(2)) if m and m.group(2) else fsize - 1
                e = min(e, fsize - 1)
                if s > e:
                    s = e
                ln = e - s + 1
                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Range", "bytes %d-%d/%d" % (s, e, fsize))
                self.send_header("Content-Length", str(ln))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Connection", "close")
                self.end_headers()
                if not head:
                    try:
                        with open(path, "rb") as f:
                            f.seek(s)
                            remaining = ln
                            while remaining > 0:
                                b = f.read(min(65536, remaining))
                                if not b:
                                    break
                                self.wfile.write(b)
                                remaining -= len(b)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
            else:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(fsize))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Connection", "close")
                self.end_headers()
                if not head:
                    try:
                        with open(path, "rb") as f:
                            while True:
                                b = f.read(65536)
                                if not b:
                                    break
                                self.wfile.write(b)
                    except (BrokenPipeError, ConnectionResetError):
                        pass

        def do_GET(self):
            self._serve(False)

        def do_HEAD(self):
            self._serve(True)

    srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, ctype


def push_file(path, device_hint, port, wait_sec):
    if not os.path.isfile(path):
        print("文件不存在: %s" % path)
        return 2

    lip = sc.get_local_ip()
    ext = os.path.splitext(path)[1].lower()
    proto = "http-get:*:%s:*" % MIME.get(ext, "video/mpeg")

    banner("文件投送测试")
    print("文件    : %s (%.1f KB)" % (path, os.path.getsize(path) / 1024.0))
    print("协议    : %s" % proto)

    devs = sc.discover_devices(lip, 6, verify=True)
    if not devs:
        print("未发现可用渲染设备")
        return 3
    dev = None
    if device_hint:
        for d in devs:
            if device_hint in d.name:
                dev = d
                break
    if dev is None:
        dev = devs[0]
    print("目标    : %s (%s)" % (dev.name, dev.ip))

    state = ProbeState()
    srv, ctype = make_file_server(path, port, state)
    url = "http://%s:%d/%s" % (lip, port, os.path.basename(path))
    print("投送URL : %s" % url)
    print("")

    try:
        sc.push_to_tv(dev, url, proto, title=os.path.basename(path))
        print("推送指令已发送成功，开始监测电视请求 ...")
    except Exception as e:
        print("推送失败: %s" % e)
        srv.shutdown()
        return 4

    t0 = time.time()
    last = 0
    while time.time() - t0 < wait_sec:
        time.sleep(2)
        n = len(state.requests)
        if n != last:
            last = n
        else:
            print("  ... 已等待 %ds，电视暂无新请求" % int(time.time() - t0), flush=True)

    banner("结果")
    print("电视共发出 HTTP 请求: %d 次" % len(state.requests))
    if state.requests:
        print("=> 电视确实在拉取媒体，DLNA 播放在工作。")
    else:
        print("=> 电视未发起任何请求，说明渲染器拒绝了该 URI 或未真正开始播放。")

    avt = dev.services.get("AVTransport")
    if avt:
        try:
            r = sc.soap_call(avt[1], avt[0], "GetTransportInfo",
                             "<InstanceID>0</InstanceID>", timeout=4)
            m = re.search(r"<CurrentTransportState>(.*?)</CurrentTransportState>", r)
            print("当前传输状态: %s" % (m.group(1) if m else "?"))
        except Exception as e:
            print("状态查询失败: %s" % e)
    srv.shutdown()
    return 0


def main():
    ap = argparse.ArgumentParser(description="DLNA 投屏能力诊断工具")
    ap.add_argument("--file", help="要投送的本地媒体文件")
    ap.add_argument("--device", help="设备名称关键字")
    ap.add_argument("--port", type=int, default=8912)
    ap.add_argument("--wait", type=int, default=30, help="监测时长(秒)")
    args = ap.parse_args()

    if sys.platform == "win32":
        for s in (sys.stdout, sys.stderr):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    if args.file:
        return push_file(args.file, args.device, args.port, args.wait)
    scan_report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
