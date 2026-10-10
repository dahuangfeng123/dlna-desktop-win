# -*- coding: utf-8 -*-
"""把改好的页面立刻端给电视看，不必先重新打包。

为什么需要它：
  tv.html 是打进 exe 的静态资源，改完必须重打包才会在电视上生效；
  但重打包要先关掉正在运行的投屏程序（exe 文件被占用，改不了名）。
  排查阶段「改一次打一次包」太慢，于是这里起一个预览服务：

      /tv、/mpegts.js、/probe  → 直接读磁盘上的 static/（改动立刻可见）
      /live.ts                 → 反向代理到正在运行的那个投屏服务
      /live-chunked            → 同上，但用 HTTP/1.1 + chunked 重发一遍
      /ws                      → 最小 WebSocket 端点（只做握手 + 一条消息）

  电视打开 http://<本机IP>:8900/tv 就能看到最新页面，
  正式的 8899 服务完全不受影响，正在跑的投屏也不用关。

用法：
    python tools/_tv_preview.py                 # 8900 → 8899
    python tools/_tv_preview.py 8901            # 只换端口
    python tools/_tv_preview.py 8901 8899       # 换端口 + 换上游

为什么要有 /live-chunked：
    正式服务发的是 HTTP/1.0 且没有 Content-Length。这种响应浏览器只能
    靠「连接关闭」判断 body 结束，对永不结束的直播流是灾难：fetch 可能
    拿不到 response.body，XHR 可能读不到增量。加一条 HTTP/1.1 + chunked
    的对照端点，一次就能判出「协议版本是不是决定因素」。

注意：代理会额外占一路 /live.ts 客户端，和原来那路分摊数据。
      测试时先把电视上原来那个 /tv 页面关掉，避免两路互抢。
"""
import base64
import hashlib
import io
import os
import socket
import struct
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(HERE, "static")

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8900
UP_HOST = "127.0.0.1"
UP_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8899

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def local_ip():
    """取本机在局域网里的地址（用 UDP connect 探一下，不发真包）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.1.1", 80))
        return s.getsockname()[0]
    except Exception:
        return "<本机IP>"
    finally:
        s.close()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"       # 默认与正式服务一致，测的才是真行为

    def log_message(self, fmt, *args):
        pass

    # ------------------------------------------------------------ 静态资源
    def _static(self, name, ctype):
        p = os.path.join(STATIC, name)
        if not os.path.isfile(p):
            self.send_error(404, "static missing: " + name)
            return
        with io.open(p, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # 预览必须不吃缓存，否则电视还拿着旧页面
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    # ------------------------------------------------------------ 直播流代理
    def _proxy_live(self, chunked=False):
        """把 /live.ts 的字节流原样转给客户端。

        自己拼头、自己转发，不用 http.client —— 后者会把「对端关闭」
        和「正常读完」混成一件事，正好丢掉我们最想看的那个信号。

        chunked=True 时用 HTTP/1.1 + Transfer-Encoding: chunked 重发：
        这是「长度未知的流」唯一被所有浏览器完整支持的 HTTP 形式。
        """
        peer = self.client_address[0]
        tag = "chunked" if chunked else "plain"
        print("  [proxy] %s 接入 /live.ts (%s)" % (peer, tag))
        try:
            up = socket.create_connection((UP_HOST, UP_PORT), timeout=10)
        except OSError as e:
            print("  [proxy] 连不上上游 %s:%d —— %s（投屏程序还在跑吗？）"
                  % (UP_HOST, UP_PORT, e))
            self.send_error(502, "upstream unreachable")
            return

        sent = 0
        try:
            up.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            up.sendall(b"GET /live.ts HTTP/1.0\r\n"
                       b"Host: %s:%d\r\n"
                       b"Connection: close\r\n\r\n" % (UP_HOST.encode(), UP_PORT))

            buf = b""
            while b"\r\n\r\n" not in buf:
                d = up.recv(4096)
                if not d:
                    break
                buf += d
            i = buf.find(b"\r\n\r\n")
            if i < 0:
                print("  [proxy] 上游没返回响应头，断了")
                self.send_error(502, "upstream no header")
                return
            body = buf[i + 4:]

            if chunked:
                # 只有 HTTP/1.1 支持 chunked，所以这一条响应单独切版本号
                self.protocol_version = "HTTP/1.1"
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Transfer-Encoding", "chunked")
                self.send_header("Cache-Control", "no-cache, no-store, "
                                                     "must-revalidate")
                self.send_header("Accept-Ranges", "none")
                self.end_headers()
                self.close_connection = True
            else:
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Connection", "close")
                self.send_header("Cache-Control", "no-cache, no-store, "
                                                     "must-revalidate")
                self.send_header("Accept-Ranges", "none")
                self.end_headers()

            def emit(data):
                nonlocal sent
                if not data:
                    return
                if chunked:
                    # 每个 chunk = 十六进制长度 \r\n 数据 \r\n
                    self.wfile.write(b"%x\r\n" % len(data))
                    self.wfile.write(data)
                    self.wfile.write(b"\r\n")
                else:
                    self.wfile.write(data)
                self.wfile.flush()
                sent += len(data)

            emit(body)
            while True:
                d = up.recv(16384)
                if not d:
                    print("  [proxy] %s 上游结束，已转发 %.1f MB"
                          % (peer, sent / 1048576.0))
                    break
                emit(d)
            if chunked:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            print("  [proxy] %s 断开（已转发 %.1f MB）：%s"
                  % (peer, sent / 1048576.0, e))
        finally:
            try:
                up.close()
            except Exception:
                pass

    # ------------------------------------------------------------ WebSocket
    def _ws_handshake(self):
        """做一次 WebSocket 握手。成功返回 True。"""
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self.send_error(400, "not a websocket handshake")
            return False
        ver = self.headers.get("Sec-WebSocket-Version", "?")
        acc = base64.b64encode(
            hashlib.sha1((key + WS_GUID).encode("ascii")).digest()
        ).decode("ascii")

        self.protocol_version = "HTTP/1.1"      # 101 必须由 1.1 发出
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", acc)
        self.end_headers()
        self.close_connection = True
        print("  [ws] %s 握手成功（Sec-WebSocket-Version=%s）"
              % (self.client_address[0], ver))
        return True

    def _ws_send(self, payload, opcode=2):
        """发一个服务端 → 客户端的帧。

        注意服务端发的帧**不加掩码**（RFC 6455 规定只有客户端发的才掩码）。
        """
        n = len(payload)
        if n < 126:
            hdr = bytes([0x80 | opcode, n])
        elif n < 65536:
            hdr = bytes([0x80 | opcode, 126]) + struct.pack(">H", n)
        else:
            hdr = bytes([0x80 | opcode, 127]) + struct.pack(">Q", n)
        self.wfile.write(hdr + payload)

    def _ws(self):
        """能力探测用的最小端点：握手 → 发一条文本 → 读两条帧。

        只回答一个问题：这台电视的 WebSocket 通不通。
        """
        if not self._ws_handshake():
            return
        try:
            self._ws_send(b"dlna-cast-ws-ok", 1)
            self.connection.settimeout(5.0)
            for _ in range(2):
                h = self.rfile.read(2)
                if len(h) < 2:
                    break
                op = h[0] & 0x0F
                masked = bool(h[1] & 0x80)
                n = h[1] & 0x7F
                if n == 126:
                    n = struct.unpack(">H", self.rfile.read(2))[0]
                elif n == 127:
                    n = struct.unpack(">Q", self.rfile.read(8))[0]
                mask = self.rfile.read(4) if masked else None
                data = self.rfile.read(n) if n else b""
                if mask:
                    data = bytes(data[i] ^ mask[i % 4] for i in range(len(data)))
                print("  [ws] 收到帧 opcode=%d len=%d %r"
                      % (op, len(data), data[:40]))
                if op == 0x8:                   # close
                    break
                if op == 0x9:                   # ping → pong
                    self._ws_send(data, 0xA)
        except Exception as e:
            print("  [ws] 读帧结束：%s" % e)

    def _ws_live(self):
        """把 /live.ts 的 TS 流改从 WebSocket 推出去。

        为什么值得做：这台电视没有 fetch，mpegts.js 只剩下 xhr-range-loader
        可选，而它是「拉」模式、要等响应结束才处理数据 —— 对无限长的直播流
        从设计上就不兼容。WebSocket 是长连接、双向、浏览器支持面最广，
        不受 HTTP 版本 / chunked / Range / response.body 这些差异影响。

        mpegts.js 有现成的 websocket-loader：URL 以 ws:// 开头就会自动选它，
        websocket-loader 只要求服务端把数据原样按二进制帧发过来。
        """
        peer = self.client_address[0]
        if not self._ws_handshake():
            return
        try:
            up = socket.create_connection((UP_HOST, UP_PORT), timeout=10)
        except OSError as e:
            print("  [ws-live] 连不上上游 %s:%d —— %s" % (UP_HOST, UP_PORT, e))
            try:
                self._ws_send(("上游连不上：" + str(e)).encode("utf-8"), 1)
            except Exception:
                pass
            return

        sent = 0
        print("  [ws-live] %s 接入，开始按帧推流" % peer)
        try:
            up.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            up.sendall(b"GET /live.ts HTTP/1.0\r\n"
                       b"Host: %s:%d\r\n"
                       b"Connection: close\r\n\r\n"
                       % (UP_HOST.encode(), UP_PORT))
            buf = b""
            while b"\r\n\r\n" not in buf:
                d = up.recv(4096)
                if not d:
                    break
                buf += d
            i = buf.find(b"\r\n\r\n")
            if i < 0:
                self._ws_send(b"upstream no header", 1)
                return
            body = buf[i + 4:]
            if body:
                self._ws_send(body, 2)
                sent += len(body)
            while True:
                d = up.recv(16384)
                if not d:
                    print("  [ws-live] %s 上游结束，共推 %.1f MB"
                          % (peer, sent / 1048576.0))
                    break
                self._ws_send(d, 2)
                sent += len(d)
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            print("  [ws-live] %s 断开（已推 %.1f MB）：%s"
                  % (peer, sent / 1048576.0, e))
        finally:
            try:
                up.close()
            except Exception:
                pass

    # ------------------------------------------------------------ 路由
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/probe":
            self._static("probe.html", "text/html; charset=utf-8")
        elif path in ("/live.ts", "/live"):
            self._proxy_live(chunked=False)
        elif path == "/live-chunked":
            self._proxy_live(chunked=True)
        elif path == "/ws":
            self._ws()
        elif path == "/ws-live":
            self._ws_live()
        elif path in ("/tv", "/tv.html"):
            self._static("tv.html", "text/html; charset=utf-8")
        elif path == "/mpegts.js":
            self._static("mpegts.js", "application/javascript; charset=utf-8")
        else:
            body = b"tv preview (proxy) is running\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    ip = local_ip()
    print("=" * 62)
    print("  电视端页面预览（改完立刻生效，不用重新打包）")
    print("=" * 62)
    print("  静态资源    : %s" % STATIC)
    print("  /live.ts    : 反向代理到 %s:%d（HTTP/1.0 直通）"
          % (UP_HOST, UP_PORT))
    print("  /live-chunked: 同上，但用 HTTP/1.1 + chunked 重发")
    print("  /ws         : 最小 WebSocket 端点（只探测连通性）")
    print("  /ws-live    : WebSocket 推流端点（把 /live.ts 改从 ws 推）")
    print()
    print("  能力探测页  ：http://%s:%d/probe   ← 先开这个" % (ip, PORT))
    print("  播放页      ：http://%s:%d/tv" % (ip, PORT))
    print("  ★ WebSocket ：http://%s:%d/tv?ws=1   ← 走 /ws-live"
          % (ip, PORT))
    print("  强制 XHR    ：http://%s:%d/tv?xhr=1" % (ip, PORT))
    print("  强制 fetch  ：http://%s:%d/tv?fetch=1" % (ip, PORT))
    print()
    print("  测试前先把电视上原来那个 /tv 关掉，免得几路互抢数据。")
    print("  Ctrl+C 结束预览。")
    print("=" * 62)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n预览已结束")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
