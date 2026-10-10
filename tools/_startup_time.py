# -*- coding: utf-8 -*-
"""测「双击到窗口出现」的真实耗时（A/B 对比两个 exe）。

两个坑，都踩过：

1. **窗口不属于 Popen 的 pid。** PyInstaller 的 onefile 引导程序启动后
   会再 spawn 一个子进程跑真正的 Python，窗口是子进程的。按父进程 pid
   去匹配窗口永远匹配不上 —— 只能按窗口标题找。所以每轮开始前必须
   确认残留进程和窗口都清了，否则会匹配到上一轮的窗口。

2. **%TEMP% 里的 _MEI* 残留会污染结果。** onefile 引导程序每次启动都要
   先扫一遍这些东西，于是「窗口出现时间」跟 exe 大小无关，只跟残留多少
   有关。每轮之间把残留改名挪走，两组数据才可比。

    python tools/_startup_time.py <exeA> <exeB> [轮数]
"""
import ctypes
import os
import subprocess
import sys
import time

TITLE = "DLNA 屏幕投屏器"
# 中英文两版 exe 的窗口标题不同，两个都认；也可用环境变量指定。
TITLES = (TITLE, "DLNA Screen Caster")
if os.environ.get("DLNACAST_WINDOW_TITLE"):
    TITLES = (os.environ["DLNACAST_WINDOW_TITLE"],)
TEMP = os.environ.get("TEMP") or os.environ.get("TMP") or "."
STALE_DIR = os.path.join(TEMP, "_dlna_stale_park")


def hwnd_of_title():
    for t in TITLES:
        h = ctypes.windll.user32.FindWindowW(None, t)
        if h:
            return h
    return 0


def park_stale_mei():
    """把 _MEI* 残留改名挪走（删不掉就改名，和 build_exe.retire 一个思路）。"""
    moved = 0
    try:
        os.makedirs(STALE_DIR, exist_ok=True)
        for name in list(os.listdir(TEMP)):
            if not name.startswith("_MEI"):
                continue
            try:
                os.rename(os.path.join(TEMP, name),
                          os.path.join(STALE_DIR,
                                       "%s.%d" % (name, int(time.time() * 1000))))
                moved += 1
            except Exception:
                pass
    except Exception as e:
        print("  清残留失败：%s" % e)
    return moved


def kill_all(names):
    for n in names:
        subprocess.run(["taskkill", "/F", "/IM", n], capture_output=True)


def wait_gone(timeout=20.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not hwnd_of_title():
            return True
        time.sleep(0.05)
    return False


def one_run(exe):
    """返回 (窗口出现秒数 or None, 存活到强杀的秒数)。"""
    t0 = time.time()
    p = subprocess.Popen([exe], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    t_win = None
    while time.time() - t0 < 120.0:
        if hwnd_of_title():
            t_win = time.time() - t0
            break
        time.sleep(0.02)
    t_alive = time.time() - t0
    return t_win, t_alive, p.pid


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    a, b = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
    rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    for path in (a, b):
        if not os.path.isfile(path):
            print("找不到：%s" % path)
            return 1

    names = [os.path.basename(a), os.path.basename(b), "DLNA投屏器.exe",
             "DLNACast.exe"]

    print("A = %s  (%.2f MB)" % (os.path.basename(a),
                                 os.path.getsize(a) / 1048576))
    print("B = %s  (%.2f MB)" % (os.path.basename(b),
                                 os.path.getsize(b) / 1048576))
    print()
    kill_all(names)
    wait_gone(10)
    print("清 %%TEMP%% 里的 _MEI 残留 ...（挪走 %d 个）" % park_stale_mei())

    res = {"A": [], "B": []}
    for i in range(rounds):
        for tag, exe in (("A", a), ("B", b)):
            kill_all(names)
            wait_gone(15)
            if hwnd_of_title():
                print("  ! 有残留窗口没关掉，跳过本轮 %s" % tag)
                continue
            t_win, t_alive, pid = one_run(exe)
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True)
            # 引导进程可能还有子进程，按镜像名一起收
            kill_all(names)
            moved = park_stale_mei()
            res[tag].append(t_win)
            print("  第 %d 轮 %s：窗口出现 %s 秒（进程存活 %.1f 秒，"
                  "本轮清掉残留 %d 个）"
                  % (i + 1, tag,
                     ("%.2f" % t_win) if t_win else "**超时**",
                     t_alive, moved))
    print()
    for tag, exe in (("A", a), ("B", b)):
        wins = sorted(w for w in res[tag] if w)
        label = "%s（%.1f MB）" % (os.path.basename(exe),
                                  os.path.getsize(exe) / 1048576)
        if not wins:
            print("%s %s：全部超时" % (tag, label))
            continue
        print("%s %s：窗口出现 中位 %.2f 秒 | 最小 %.2f | 最大 %.2f （%d 次有效）"
              % (tag, label, wins[len(wins) // 2], wins[0], wins[-1], len(wins)))
    kill_all(names)
    return 0


if __name__ == "__main__":
    sys.exit(main())
