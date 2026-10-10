# -*- coding: utf-8 -*-
"""端到端链路诊断：客户端实收帧率 + 本机缓冲积压 + 数据流抖动。

直接启动 screen-cast.py 的 --no-push 模式，从 /live.ts 拉流，
解析程序自身日志中的积压报告，并对接收节奏做时间序列分析。
"""
import os
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.request

# 默认用当前解释器；需要指定别的版本时设 DLNACAST_PYTHON
PY = os.environ.get("DLNACAST_PYTHON", sys.executable)
# ffprobe 默认从 PATH 找；本机装在别处时设 FFPROBE_PATH
FP = os.environ.get("FFPROBE_PATH", "ffprobe")
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8911
EXTRA = sys.argv[2:] if len(sys.argv) > 2 else []
DUR = 42


def run_case(title, extra):
    ts_path = "_e2e.ts"
    print("=" * 74)
    print(">> %s" % title)
    print("   参数: %s" % (" ".join(extra) or "(默认)"))
    print("=" * 74)

    cmd = [PY, "screen-cast.py", "--no-push", "--port", str(PORT)] + extra
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", bufsize=1)

    logs = []

    def reader():
        for line in proc.stdout:
            logs.append(line.rstrip())
    t = threading.Thread(target=reader, daemon=True)
    t.start()

    time.sleep(6)   # 等管道起来

    recv = bytearray()
    stamps = []
    sizes = []
    t0 = time.time()
    last_t = t0
    try:
        r = urllib.request.urlopen(
            "http://127.0.0.1:%d/live.ts" % PORT, timeout=15)
        while time.time() - t0 < DUR:
            c = r.read(65536)
            if not c:
                break
            now = time.time()
            recv.extend(c)
            stamps.append(now - t0)
            sizes.append(len(c))
            last_t = now
        r.close()
    except Exception as e:
        print("   拉流异常: %s" % e)

    el = time.time() - t0
    try:
        proc.terminate()
        proc.wait(timeout=12)
    except Exception:
        proc.kill()

    if recv:
        with open(ts_path, "wb") as f:
            f.write(recv)
        q = subprocess.run(
            [FP, "-v", "error", "-select_streams", "v:0", "-count_frames",
             "-show_entries", "stream=nb_read_frames,r_frame_rate",
             "-of", "csv=p=0", ts_path],
            capture_output=True, text=True, creationflags=FLAGS)
        info = q.stdout.strip().replace("\n", " | ")
        m = re.search(r"(\d+)\s*$", q.stdout.strip())
        nf = int(m.group(1)) if m else 0
        print("\n  [接收侧] %.1f KB / %.1f 秒 = %.0f kbps"
              % (len(recv) / 1024.0, el, len(recv) * 8 / el / 1000.0))
        print("  [接收侧] ffprobe: %s" % info)
        print("  [接收侧] 实收帧率 = %.1f fps  %s"
              % (nf / el, "✔" if nf / el >= 27 else "✘ 明显偏低"))

        # 数据到达节奏：判断"卡顿"（长时间无数据）
        gaps = [stamps[i + 1] - stamps[i] for i in range(len(stamps) - 1)]
        if gaps:
            print("  [接收侧] 数据块 %d 个，平均 %.0f ms 一块；"
                  "间隔 中位 %.0f ms / 最大 %.0f ms / 标准差 %.0f ms"
                  % (len(stamps), el / len(stamps) * 1000,
                     statistics.median(gaps) * 1000, max(gaps) * 1000,
                     statistics.pstdev(gaps) * 1000))
            big = sum(1 for g in gaps if g > 0.5)
            print("  [接收侧] 超过 500 ms 的停顿: %d 次%s"
                  % (big, "  ← 卡顿来源" if big else ""))

    print("\n  [程序侧日志]")
    for line in logs:
        if any(k in line for k in ("[stream]", "缓冲", "编码已输出",
                                   "画质", "编码器", "延迟参数", "接入")):
            print("    " + line)
    print()


if __name__ == "__main__":
    extra = EXTRA if EXTRA else ["--quality", "1080", "--audio", "none"]
    run_case("出厂默认（不传帧率参数）" if not EXTRA else "指定参数",
             extra)
