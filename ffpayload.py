# -*- coding: utf-8 -*-
"""内嵌 ffmpeg 载荷：打包侧的追加写入，运行侧的首次解包。

为什么不用 PyInstaller 自带的 ``--add-binary``
------------------------------------------------
PyInstaller 的 onefile 模式**每次启动**都会把归档里的全部内容解包到临时
目录。把 231 MB 的 ffmpeg 塞进去，等于每双击一次就要往磁盘写 231 MB，
冷启动凭空多出好几秒，而且每次都是白写 —— 内容根本没变。

这里改用「追加式载荷」：

* **构建期**：把 ffmpeg.exe 用 LZMA 压一遍，直接**追加**到 PyInstaller
  产物 exe 的末尾，再写一个定长尾部索引。
  PyInstaller 只按自己归档头里记录的长度去读，尾部多出来的字节它不理会，
  所以追加不会破坏 exe 的可执行性。
* **运行期**：只在**首次**启动时把载荷解包到
  ``%LOCALAPPDATA%\\DlnaCast\\bin\\ffmpeg.exe``，之后每次启动直接复用缓存。
  启动开销只剩 Python 运行时那一部分。

实测数据（本机 4 核 8 线程笔记本，源文件 231.3 MB）
--------------------------------------------
    zlib-9     压缩比 2.22x  产物 104.1 MB  构建 34 s   首次解包 1.5 s
    lzma-1     压缩比 2.54x  产物  91.1 MB  构建 43 s   首次解包 7.0 s
    lzma-6 ★   压缩比 2.83x  产物  81.8 MB  构建 162 s  首次解包 6.5 s

选 lzma：体积小 21 MB，代价是首次解包多 5 秒（一次性，且界面有进度条）。
构建慢一点无所谓 —— 那是自动化的，没人盯着。

文件布局
--------
    ┌────────────────────┬──────────────┬────────────┬──────────────┐
    │ PyInstaller 归档    │ 压缩载荷      │ 索引 32 B  │ MAGIC 16 B   │
    └────────────────────┴──────────────┴────────────┴──────────────┘
    ↑ 文件开头                                          ↑ 文件结尾

索引 32 字节，全部小端：

    偏移 0    uint64  raw_size   解压后字节数
    偏移 8    uint64  blob_size  压缩后字节数
    偏移 16   uint64  stamp      载荷指纹（源文件 mtime），用于让旧缓存失效
    偏移 24   uint64  保留

命令行
------
    python ffpayload.py pack   <exe> <ffmpeg.exe>   # 压缩并追加
    python ffpayload.py info   <exe>                # 查看载荷信息
    python ffpayload.py strip  <exe>                # 去掉载荷（便于重新打包）
    python ffpayload.py unpack <exe> <目标路径>      # 手工解包
"""

import lzma
import os
import shutil
import struct
import subprocess
import sys
import time

MAGIC = b"DLNACAST-PAYLOAD"          # 恰好 16 字节
INDEX_SIZE = 32
FOOTER_SIZE = INDEX_SIZE + len(MAGIC)   # 48

# 7-Zip 的位置（存在就用它，多线程 LZMA2，构建快约 1.7 倍；不存在不影响功能）
_SEVEN_ZIP_CANDIDATES = [
    r"C:\Program Files\7-Zip\7z.exe",
    r"C:\Program Files (x86)\7-Zip\7z.exe",
]


def _now():
    return int(time.time())


def _fingerprint(path):
    """载荷指纹：源文件大小与修改时间。换一份 ffmpeg 重新打包后指纹必变，
    旧缓存自动失效，不需要用户手动清理。"""
    st = os.stat(path)
    return ((int(st.st_size) & 0xFFFFFFFF) << 20) ^ (int(abs(st.st_mtime)) & 0xFFFFF)


# ------------------------------------------------------------------ 读取侧


def read_footer(exe_path):
    """读取尾部索引。不是「带载荷的 exe」就返回 None。"""
    try:
        size = os.path.getsize(exe_path)
        if size < FOOTER_SIZE:
            return None
        with open(exe_path, "rb") as f:
            f.seek(size - FOOTER_SIZE)
            footer = f.read(FOOTER_SIZE)
        if len(footer) != FOOTER_SIZE or footer[INDEX_SIZE:] != MAGIC:
            return None
        raw_size, blob_size, stamp, _res = struct.unpack("<QQQQ", footer[:INDEX_SIZE])
        if blob_size <= 0 or raw_size <= 0 or blob_size > size - FOOTER_SIZE:
            return None
        return {
            "exe_size": size,
            "raw_size": raw_size,
            "blob_size": blob_size,
            "stamp": stamp,
            "payload_offset": size - FOOTER_SIZE - blob_size,
        }
    except Exception:
        return None


def strip_payload(exe_path):
    """把尾部载荷截掉，还原成纯 PyInstaller 产物。返回是否真的截了。

    重新打包前调用 —— 否则载荷会一层叠一层，exe 越滚越大。
    """
    info = read_footer(exe_path)
    if not info:
        return False
    os.truncate(exe_path, info["payload_offset"])
    return True


# ------------------------------------------------------------------ 写入侧


def _find_7z():
    for p in _SEVEN_ZIP_CANDIDATES:
        if os.path.isfile(p):
            return p
    return shutil.which("7z")


def compress_to_xz(src, dst, preset=6, use_7z=True, log=None):
    """把 src 压成 xz 写到 dst。返回 (方法名, 压缩后字节数)。

    7-Zip 的 ``-mmt=on`` 能跑满多核，实测比 Python 单线程 lzma 快约 1.7 倍，
    压缩比完全一致（同为 lzma2/mx=6）。产物是标准 .xz，Python 的 lzma
    模块可直接解，所以运行侧不依赖 7-Zip。
    """
    def _l(msg):
        if log:
            log(msg)

    z = _find_7z() if use_7z else None
    if z:
        _l("  压缩：%s（多线程 LZMA2, -mx=%d）" % (os.path.basename(z), preset))
        # -bso0 -bsp0 静音进度噪声；-y 覆盖旧文件
        cp = subprocess.run(
            [z, "a", "-txz", "-m0=lzma2", "-mx=%d" % preset, "-mmt=on",
             "-y", "-bso0", "-bsp0", dst, src],
            capture_output=True,
        )
        if cp.returncode == 0 and os.path.isfile(dst) and os.path.getsize(dst) > 0:
            return "7z-lzma2", os.path.getsize(dst)
        _l("  7-Zip 失败（返回码 %s），回退 Python lzma" % cp.returncode)

    _l("  压缩：Python 标准库 lzma（单线程, preset=%d）" % preset)
    done = 0
    total = os.path.getsize(src)
    CH = 4 * 1024 * 1024
    t0 = time.time()
    with open(src, "rb") as fin, lzma.open(dst, "wb", preset=preset) as fout:
        while True:
            chunk = fin.read(CH)
            if not chunk:
                break
            fout.write(chunk)
            done += len(chunk)
            if log and total:
                pct = done * 100 // total
                _l("    压入 %3d%%  (%.0f/%.0f MB, %.0fs)"
                   % (pct, done / 1048576, total / 1048576, time.time() - t0))
    return "python-lzma", os.path.getsize(dst)


def append_payload(exe_path, src_ffmpeg, preset=6, use_7z=True, log=None):
    """把 src_ffmpeg 压缩后追加到 exe_path 末尾，并写尾部索引。

    重复调用是安全的：会先把上一次的载荷截掉再追加，不会越堆越大。
    """
    def _l(msg):
        if log:
            log(msg)

    if not os.path.isfile(exe_path):
        raise IOError("找不到目标 exe：%s" % exe_path)
    if not os.path.isfile(src_ffmpeg):
        raise IOError("找不到 ffmpeg：%s" % src_ffmpeg)

    old = read_footer(exe_path)
    if old:
        _l("  检测到旧载荷（%.1f MB），先剥离" % (old["blob_size"] / 1048576))
        os.truncate(exe_path, old["payload_offset"])

    raw_size = os.path.getsize(src_ffmpeg)
    stamp = _fingerprint(src_ffmpeg)
    tmp_xz = exe_path + ".payload.xz"
    try:
        method, blob_size = compress_to_xz(src_ffmpeg, tmp_xz, preset, use_7z, log)
        _l("  压缩完成：%s  比值 %.2fx  %.1f MB → %.1f MB"
           % (method, raw_size / max(1, blob_size),
              raw_size / 1048576, blob_size / 1048576))
        _l("  追加到 exe ...")
        with open(exe_path, "ab") as out, open(tmp_xz, "rb") as f:
            shutil.copyfileobj(f, out, 4 * 1024 * 1024)
            out.write(struct.pack("<QQQQ", raw_size, blob_size, stamp, 0))
            out.write(MAGIC)
    finally:
        try:
            os.remove(tmp_xz)
        except Exception:
            pass

    info = read_footer(exe_path)
    if not info:
        raise IOError("追加后校验失败：读不到尾部索引")
    _l("  完成：exe 现在 %.1f MB（其中载荷 %.1f MB）"
       % (info["exe_size"] / 1048576, info["blob_size"] / 1048576))
    return info


# ------------------------------------------------------------------ 解包侧


def extract_payload(exe_path, dest_path, log=None, progress=None):
    """把载荷解包到 dest_path。先写 .tmp 再原子替换，避免半截文件被误用。"""
    info = read_footer(exe_path)
    if not info:
        return None

    def _l(msg):
        if log:
            log(msg)

    dest_dir = os.path.dirname(dest_path)
    if dest_dir:
        os.makedirs(dest_dir, exist_ok=True)
    tmp = dest_path + ".tmp"
    raw_size = info["raw_size"]
    remaining = info["blob_size"]
    done = 0
    CH = 4 * 1024 * 1024
    dec = lzma.LZMADecompressor(format=lzma.FORMAT_XZ)
    t0 = time.time()

    _l("  首次运行：解包内置 ffmpeg（%.0f MB，约需数秒）..." % (raw_size / 1048576))
    try:
        with open(exe_path, "rb") as fin, open(tmp, "wb") as fout:
            fin.seek(info["payload_offset"])
            while remaining > 0:
                chunk = fin.read(min(CH, remaining))
                if not chunk:
                    raise IOError("载荷提前结束（还差 %d 字节）" % remaining)
                remaining -= len(chunk)
                out = dec.decompress(chunk)
                if out:
                    fout.write(out)
                    done += len(out)
                    if progress:
                        progress(done, raw_size)
                if dec.eof:
                    break
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass
        raise

    if done != raw_size:
        try:
            os.remove(tmp)
        except Exception:
            pass
        raise IOError("解包大小不符：得到 %d，期望 %d" % (done, raw_size))

    _commit(tmp, dest_path)
    _l("  解包完成：%.0f MB，用时 %.1f 秒" % (done / 1048576, time.time() - t0))
    return dest_path


def _commit(tmp, dest):
    """把临时文件落成正式文件。

    正常路径就是 os.replace（同盘原子替换）。但某些环境会拦截「覆盖已存在
    文件」这一步（实测有返回 WinError 5 的），所以备一条「名字换名字」的
    路：先把旧的挪开，再把新的改名到位。
    """
    try:
        os.replace(tmp, dest)
        return
    except OSError:
        pass

    old = dest + ".old"
    moved_old = False
    try:
        if os.path.isfile(dest):
            os.rename(dest, old)      # 只用到改名，不触发删除拦截
            moved_old = True
        os.rename(tmp, dest)
    finally:
        if moved_old:
            try:
                os.remove(old)
            except Exception:
                pass


def ensure_ffmpeg(exe_path=None, cache_dir=None, log=None, progress=None):
    """返回可用的内嵌 ffmpeg 路径；没有内嵌载荷就返回 None。

    快路径：缓存文件存在且指纹一致 → 直接返回，**不重新解包、不做任何哈希**，
    所以第二次之后的启动开销可以忽略。
    """
    info = read_footer(exe_path) if exe_path else None
    if not info:
        return None

    cache_dir = cache_dir or os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "DlnaCast")
    bin_dir = os.path.join(cache_dir, "bin")
    dest = os.path.join(bin_dir, "ffmpeg.exe")
    stamp_file = os.path.join(bin_dir, "ffmpeg.stamp")

    if os.path.isfile(dest):
        try:
            if os.path.getsize(dest) == info["raw_size"]:
                with open(stamp_file, "r", encoding="utf-8") as f:
                    if f.read().strip() == str(info["stamp"]):
                        return dest
        except Exception:
            pass

    extract_payload(exe_path, dest, log=log, progress=progress)
    try:
        os.makedirs(bin_dir, exist_ok=True)
        with open(stamp_file, "w", encoding="utf-8") as f:
            f.write(str(info["stamp"]))
    except Exception:
        pass
    return dest


def verify_ffmpeg(path, timeout=20):
    """跑一次 ``-version`` 确认解出来的确实能执行（防杀软隔离/半截文件）。"""
    try:
        cp = subprocess.run([path, "-version"], capture_output=True, timeout=timeout)
        return cp.returncode == 0
    except Exception:
        return False


# ------------------------------------------------------------------ 命令行


def _cli(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd = argv[0]
    if cmd == "pack":
        if len(argv) < 3:
            print("用法: ffpayload.py pack <exe> <ffmpeg.exe>")
            return 1
        append_payload(argv[1], argv[2], log=lambda m: print(m, flush=True))
        return 0
    if cmd == "strip":
        print("已剥离" if strip_payload(argv[1]) else "该文件不含载荷")
        return 0
    if cmd == "info":
        info = read_footer(argv[1])
        if not info:
            print("该文件不含载荷")
            return 1
        for k in ("exe_size", "raw_size", "blob_size", "stamp", "payload_offset"):
            print("%-15s %s" % (k, info[k]))
        return 0
    if cmd == "unpack":
        if len(argv) < 3:
            print("用法: ffpayload.py unpack <exe> <目标 ffmpeg.exe>")
            return 1
        extract_payload(argv[1], argv[2], log=lambda m: print(m, flush=True))
        return 0
    print("未知命令：%s" % cmd)
    return 1


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
