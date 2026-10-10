# -*- coding: utf-8 -*-
"""帧率诊断：分别测「采集能力」与「编码器行为」，定位掉帧发生在哪一环。

实验 1  gdigrab 真实采集帧率（决定帧率上限）
实验 2  同一合成源下，各参数组的实际输出帧数（判断编码器是否丢帧/GOP 是否生效）
"""
import re
import subprocess
import sys

FF = "D:/ffmpeg/ffmpeg.exe"
FP = "D:/ffmpeg/ffprobe.exe"
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(cmd, timeout=120):
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       timeout=timeout, creationflags=FLAGS)
    return (p.stdout or "") + (p.stderr or "")


def last_stats(out):
    """取 ffmpeg 最后一行 stats：frame= / fps= / speed="""
    m = None
    for line in out.splitlines():
        if "frame=" in line and ("fps=" in line or "speed=" in line):
            m = line.strip()
    return m


def probe_frames(path):
    out = run([FP, "-v", "error", "-select_streams", "v:0",
               "-count_frames", "-show_entries",
               "stream=nb_read_frames,r_frame_rate,avg_frame_rate", "-of",
               "csv=p=0", path])
    return out.strip()


def probe_gop(path):
    """统计关键帧位置，判断 GOP 参数是否真的生效。"""
    out = run([FP, "-v", "error", "-select_streams", "v:0",
               "-show_entries", "packet=flags", "-of", "csv=p=0", path])
    flags = [x.strip() for x in out.splitlines() if x.strip()]
    idx = [i for i, f in enumerate(flags) if f.startswith("K")]
    if not idx:
        return (0, [])
    gaps = [idx[i + 1] - idx[i] for i in range(len(idx) - 1)]
    return (len(idx), gaps)


print("=" * 72)
print("实验 1 —— gdigrab 真实采集帧率（本机屏幕，12 秒，软编兜底）")
print("=" * 72)
out = run([FF, "-hide_banner", "-loglevel", "info", "-stats", "-y",
           "-f", "gdigrab", "-framerate", "30", "-thread_queue_size", "512",
           "-i", "desktop", "-t", "12",
           "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
           "-b:v", "20000k", "-an", "-f", "null", "-"])
print(last_stats(out) or "(无 stats 行)")
for line in out.splitlines():
    if "Too large" in line or "duplicated" in line or "drop" in line.lower():
        print("  [!]", line.strip())
        break

print()
print("=" * 72)
print("实验 2 —— 合成源 1080p@30（12 秒 = 360 帧），只比编码器参数")
print("=" * 72)

VG = "scale=-2:1080:flags=fast_bilinear,format="

CASES = [
    ("A 当前实现 mf gop=8(250ms) bufsize=bitrate",
     ["-vf", VG + "nv12", "-c:v", "h264_mf", "-hw_encoding", "1",
      "-scenario", "display_remoting", "-rate_control", "ld_vbr",
      "-pix_fmt", "nv12", "-b:v", "4500k", "-maxrate", "5200k",
      "-bufsize", "4500k", "-bf", "0", "-g", "8"]),
    ("B mf bufsize=maxrate*2 (放宽 VBV)",
     ["-vf", VG + "nv12", "-c:v", "h264_mf", "-hw_encoding", "1",
      "-scenario", "display_remoting", "-rate_control", "ld_vbr",
      "-pix_fmt", "nv12", "-b:v", "4500k", "-maxrate", "5200k",
      "-bufsize", "10400k", "-bf", "0", "-g", "8"]),
    ("C mf gop=15(500ms) bufsize=maxrate*2",
     ["-vf", VG + "nv12", "-c:v", "h264_mf", "-hw_encoding", "1",
      "-scenario", "display_remoting", "-rate_control", "ld_vbr",
      "-pix_fmt", "nv12", "-b:v", "4500k", "-maxrate", "5200k",
      "-bufsize", "10400k", "-bf", "0", "-g", "15"]),
    ("D mf rc=cbr (固定码率，不丢帧)",
     ["-vf", VG + "nv12", "-c:v", "h264_mf", "-hw_encoding", "1",
      "-scenario", "display_remoting", "-rate_control", "cbr",
      "-pix_fmt", "nv12", "-b:v", "4500k", "-maxrate", "5200k",
      "-bufsize", "10400k", "-bf", "0", "-g", "15"]),
    ("E 当前实现 x264 gop=8 bufsize=bitrate",
     ["-vf", VG + "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
      "-tune", "zerolatency", "-profile:v", "main", "-pix_fmt", "yuv420p",
      "-b:v", "4500k", "-maxrate", "5200k", "-bufsize", "4500k",
      "-bf", "0", "-g", "8", "-keyint_min", "8", "-sc_threshold", "0",
      "-flags", "low_delay", "-x264-params", "nal-hrd=none:force-cfr=1"]),
]

for name, vargs in CASES:
    ts = "_dg_%s.ts" % name.split()[0]
    cmd = ([FF, "-hide_banner", "-loglevel", "info", "-stats", "-y",
            "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=12"] + vargs +
           ["-an", "-f", "mpegts", ts])
    out = run(cmd)
    stats = last_stats(out)
    fr = re.search(r"frame=\s*(\d+)", stats or "")
    nframes = int(fr.group(1)) if fr else -1
    fs = probe_frames(ts)
    nk, gaps = probe_gop(ts)
    warn = [l.strip() for l in out.splitlines()
            if "Too large" in l or "Non-monotonous" in l or "drop" in l.lower()]
    print("\n--- %s" % name)
    print("    stats        : %s" % (stats or "无"))
    print("    输出帧数     : %d / 360  %s"
          % (nframes, "✔ 满帧" if nframes >= 355 else "✘ 掉帧 %.1f%%"
             % ((360 - nframes) / 3.6)))
    print("    ffprobe      : %s" % fs)
    print("    关键帧数     : %d  GOP 间隔: %s" % (nk, gaps[:8]))
    if warn:
        print("    警告         : %s" % warn[:2])

print()
sys.stdout.flush()
