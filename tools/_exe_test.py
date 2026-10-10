# -*- coding: utf-8 -*-
"""真机验证：直接运行打好的 exe（图形界面形态），检查首启解包与界面。

不点任何按钮，只是把窗口开起来看一眼，顺便确认
%LOCALAPPDATA%\\DlnaCast\\bin\\ffmpeg.exe 被正确解出来了。
"""
import os
import subprocess
import sys
import time

EXE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "dist", "DLNA投屏器.exe")
CACHE = os.path.join(os.environ["LOCALAPPDATA"], "DlnaCast", "bin")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_exe_shot.png")

print("exe     :", EXE, "%.1f MB" % (os.path.getsize(EXE) / 1048576))
print("缓存目录:", CACHE)

had = os.path.isfile(os.path.join(CACHE, "ffmpeg.exe"))
print("运行前已有缓存:", had)

t0 = time.time()
p = subprocess.Popen([EXE], cwd=os.path.dirname(EXE))
print("已启动 pid=%d，等待首启解包 + 界面就绪 ..." % p.pid)

time.sleep(22)

from PIL import ImageGrab
img = ImageGrab.grab()
img.save(OUT)
print("整屏截图:", OUT, "%dx%d" % img.size)

alive = p.poll() is None
print("进程存活:", alive, "" if alive else "（退出码 %s）" % p.returncode)

ff = os.path.join(CACHE, "ffmpeg.exe")
stamp = os.path.join(CACHE, "ffmpeg.stamp")
print("解出 ffmpeg:", os.path.isfile(ff),
      ("%.1f MB" % (os.path.getsize(ff) / 1048576)) if os.path.isfile(ff) else "")
print("指纹文件  :", os.path.isfile(stamp),
      open(stamp).read().strip() if os.path.isfile(stamp) else "")

if alive:
    p.terminate()
    try:
        p.wait(timeout=10)
    except Exception:
        p.kill()
    print("已终止")

print("首启总耗时约 %.1f 秒（含 69 MB 自解压 + 56 MB 载荷解包）" % (time.time() - t0))
sys.exit(0 if (alive or p.returncode == 0) else 1)
