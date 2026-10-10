# -*- coding: utf-8 -*-
"""采集环节隔离测试：定位 gdigrab 帧率损失的构成，并验证 ddagrab 是否可用。"""
import re
import subprocess

FF = "D:/ffmpeg/ffmpeg.exe"
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(cmd, timeout=180):
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       timeout=timeout, creationflags=FLAGS)
    return (p.stdout or "") + (p.stderr or "")


def frames(out):
    m = None
    for line in out.splitlines():
        if "frame=" in line and "fps=" in line:
            m = line
    if not m:
        return None, None
    f = re.search(r"frame=\s*(\d+)", m)
    r = re.search(r"fps=\s*([\d.]+)", m)
    t = re.search(r"time=(\S+)", m)
    return (int(f.group(1)) if f else None,
            (float(r.group(1)) if r else None, t.group(1) if t else "?"),
            m.strip())


DUR = 10

CASES = [
    ("A 基线：gdigrab 无任何滤镜",
     ["-f", "gdigrab", "-framerate", "30", "-i", "desktop", "-t", str(DUR),
      "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "20000k"]),
    ("B 仅 format=nv12（无 scale）",
     ["-f", "gdigrab", "-framerate", "30", "-i", "desktop", "-t", str(DUR),
      "-vf", "format=nv12",
      "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "20000k"]),
    ("C 当前实现：scale=-2:1080 + format=nv12",
     ["-f", "gdigrab", "-framerate", "30", "-i", "desktop", "-t", str(DUR),
      "-vf", "scale=-2:1080:flags=fast_bilinear,format=nv12",
      "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "20000k"]),
    ("D 当前实现 + nobuffer + thread_queue_size 512",
     ["-fflags", "nobuffer", "-f", "gdigrab", "-framerate", "30",
      "-thread_queue_size", "512", "-i", "desktop", "-t", str(DUR),
      "-vf", "scale=-2:1080:flags=fast_bilinear,format=nv12",
      "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "20000k"]),
    ("E 请求 60fps 探真实上限",
     ["-f", "gdigrab", "-framerate", "60", "-i", "desktop", "-t", str(DUR),
      "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "20000k"]),
    ("F 当前实现但用 mf 硬编（最贴近真实）",
     ["-fflags", "nobuffer", "-f", "gdigrab", "-framerate", "30",
      "-thread_queue_size", "512", "-i", "desktop", "-t", str(DUR),
      "-vf", "scale=-2:1080:flags=fast_bilinear,format=nv12",
      "-c:v", "h264_mf", "-hw_encoding", "1", "-scenario", "display_remoting",
      "-rate_control", "ld_vbr", "-pix_fmt", "nv12", "-b:v", "4500k",
      "-maxrate", "5200k", "-bufsize", "4500k", "-bf", "0", "-g", "8"]),
]

print("=" * 74)
print("采集环节隔离测试（本机 1920x1080，每组 %d 秒，请求 30fps → 期望 %d 帧）"
      % (DUR, DUR * 30))
print("=" * 74)

for name, args in CASES:
    req = 30 if "60fps" not in name else 60
    expect = DUR * req
    out = run([FF, "-hide_banner", "-loglevel", "info", "-stats", "-y"] +
              args + ["-an", "-f", "null", "-"])
    n, (fps, t), raw = frames(out)
    if n is None:
        print("\n--- %s\n    失败：%s" % (name, out.strip().splitlines()[-1:]))
        continue
    pct = n / expect * 100.0
    verdict = ("✔ 达标" if pct >= 97 else
               "△ 轻微不足" if pct >= 85 else
               "✘ 明显掉帧")
    print("\n--- %s" % name)
    print("    %d 帧 / %.1f 秒 = %.1f fps（请求 %d）→ %.0f%% %s"
          % (n, float(t.split(":")[-1]) if t != "?" else DUR, fps, req,
             pct, verdict))
    print("    %s" % raw)

# ---------------- ddagrab 可用性 ----------------
print()
print("=" * 74)
print("ddagrab（Desktop Duplication GPU 采集）可用性")
print("=" * 74)
out = run([FF, "-hide_banner", "-filters"])
hits = [l.strip() for l in out.splitlines() if "ddagrab" in l]
print("滤镜列表:", hits if hits else "未找到 ddagrab")

if hits:
    out = run([FF, "-hide_banner", "-loglevel", "warning", "-stats", "-y",
               "-f", "lavfi", "-i",
               "ddagrab=output_idx=0:framerate=30:draw_mouse=1",
               "-t", str(DUR),
               "-vf", "hwdownload,format=bgra,scale=-2:1080:flags=fast_bilinear,format=nv12",
               "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "20000k",
               "-an", "-f", "null", "-"])
    n, info, raw = frames(out)
    if n is None:
        print("ddagrab 运行失败：")
        print("\n".join(out.strip().splitlines()[-6:]))
    else:
        fps, t = info
        print("ddagrab 结果: %d 帧 / %.1f 秒 = %.1f fps (请求 30) → %.0f%%"
              % (n, float(t.split(":")[-1]), fps, n / (DUR * 30) * 100))
        print(raw)
