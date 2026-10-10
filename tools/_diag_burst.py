# -*- coding: utf-8 -*-
"""输出突发性诊断：ffmpeg stdout 直连本机读取，排除网络因素，
统计「数据到达节奏」，定位 500ms 级断流的成因（VBV / 编码器 / 帧率）。
"""
import statistics
import subprocess
import time

FF = "D:/ffmpeg/ffmpeg.exe"
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DUR = 20

V_IN = ["-fflags", "nobuffer", "-f", "gdigrab", "-framerate", "{FPS}",
        "-thread_queue_size", "512", "-i", "desktop"]
VF = "scale=-2:1080:flags=fast_bilinear,format=nv12"
MUX = ["-f", "mpegts", "-muxdelay", "0", "-muxpreload", "0",
       "-flush_packets", "1", "pipe:1"]


def mf(bv, mx, bs, g):
    return ["-vf", VF, "-c:v", "h264_mf", "-hw_encoding", "1",
            "-scenario", "display_remoting", "-rate_control", "ld_vbr",
            "-pix_fmt", "nv12", "-b:v", bv, "-maxrate", mx, "-bufsize", bs,
            "-bf", "0", "-g", str(g)]


def x264(bv, mx, bs, g):
    return ["-vf", VF, "-c:v", "libx264", "-preset", "ultrafast",
            "-tune", "zerolatency", "-profile:v", "main", "-pix_fmt", "nv12",
            "-b:v", bv, "-maxrate", mx, "-bufsize", bs, "-bf", "0",
            "-g", str(g), "-keyint_min", str(g), "-sc_threshold", "0",
            "-flags", "low_delay", "-x264-params", "nal-hrd=none:force-cfr=1"]


CASES = [
    ("A  mf  fps25  bufsize=bitrate(4500k)   ← 当前实现",
     25, mf("4500k", "5200k", "4500k", 12)),
    ("B  mf  fps25  bufsize=maxrate*2(10400k) 放宽 VBV",
     25, mf("4500k", "5200k", "10400k", 12)),
    ("C  mf  fps30  bufsize=maxrate*2(10400k)",
     30, mf("4500k", "5200k", "10400k", 15)),
    ("D  x264 fps25 bufsize=bitrate(4500k)   ← 当前实现",
     25, x264("4500k", "5200k", "4500k", 12)),
    ("E  x264 fps25 bufsize=maxrate*2(10400k)",
     25, x264("4500k", "5200k", "10400k", 12)),
]

print("=" * 78)
print("输出突发性诊断（本机 stdout 直读 %d 秒，无网络因素）" % DUR)
print("=" * 78)

for name, fps, vargs in CASES:
    cmd = ([FF, "-hide_banner", "-loglevel", "error", "-y"] +
           [x.replace("{FPS}", str(fps)) for x in V_IN] + vargs + ["-an"] + MUX)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, bufsize=0,
                         creationflags=FLAGS)
    recv = 0
    stamps = []
    t0 = time.time()
    while time.time() - t0 < DUR:
        b = p.stdout.read(65536)
        if not b:
            break
        recv += len(b)
        stamps.append(time.time() - t0)
    el = time.time() - t0
    try:
        p.terminate()
        p.wait(timeout=8)
    except Exception:
        p.kill()

    gaps = [stamps[i + 1] - stamps[i] for i in range(len(stamps) - 1)]
    if not gaps:
        print("\n--- %s\n    无数据" % name)
        continue
    big = sum(1 for g in gaps if g > 0.3)
    huge = sum(1 for g in gaps if g > 0.5)
    print("\n--- %s" % name)
    print("    吞吐 %.0f kbps | 数据块 %d 个 | 平均每块 %.0f ms"
          % (recv * 8 / el / 1000.0, len(stamps), el / len(stamps) * 1000))
    print("    到达间隔: 中位 %.0f ms | 最大 %.0f ms | 标准差 %.0f ms"
          % (statistics.median(gaps) * 1000, max(gaps) * 1000,
             statistics.pstdev(gaps) * 1000))
    print("    停顿 >300ms: %d 次   >500ms: %d 次  %s"
          % (big, huge,
             "✘ 明显突发" if huge >= 3 else
             "△ 轻微" if big >= 3 else "✔ 平滑"))

print("\n说明：本测试读的是 ffmpeg 直接输出到本机管道的字节，")
print("      不经过 HTTP 服务器与局域网，因此停顿只可能来自编码/封装层。")
