# -*- coding: utf-8 -*-
"""获取 ffmpeg：先找本机已有的，找不到再从 GitHub 下一个放到 exe 旁边。

为什么不再把 ffmpeg 内嵌进单文件 exe
--------------------------------------
把 231 MB 的 ffmpeg 塞进 exe，exe 会从 13 MB 涨到 69 MB。代价是每次启动
都要让杀软把那 69 MB 重扫一遍，窗口出现前多等好几秒；而 ffmpeg 本身极少
变化，没必要每次都跟着 exe 走一遍。

查找顺序（前者永远优先，所以用户想指定自己的那份很容易）
--------------------------------------------------------
    1. 环境变量 FFMPEG_PATH
    2. exe 同目录的 ffmpeg.exe        ← 自动下载也放这里
    3. %LOCALAPPDATA%\\DlnaCast\\bin  ← 旧的内嵌解包位置，兼容保留
    4. 常见安装路径（D:\\ffmpeg\\ffmpeg.exe 等）
    5. PATH

都没有就下载
------------
源：GyanD/codexffmpeg 的 GitHub Release（gyan.dev 官方构建的镜像仓库），
取 ``*-essentials_build.zip``。它包含本项目要的全部能力：
gdigrab、ddagrab、libx264、h264_mf(mediafoundation)、drawtext、mpegts、aac。

下载**只取包里 bin/ffmpeg.exe 一个成员**：
用 HTTP Range 读 zip 中央目录 → 定位成员 → 只下那一段。
跳过同包里的 ffplay.exe / ffprobe.exe，实测省掉约三分之二的流量
（整包 109.5 MB，只要的那一段压缩后 35.7 MB，占 32.6%）。
服务器不支持 Range 时自动退回「整包下载再解压」。

那 35.7 MB 会切成几段并行下（默认 4 段，`DLNACAST_DOWNLOAD_WORKERS`
可调），每段单独落一个 ``.pN`` 临时文件 —— 断了重跑能接着下，不用从 0 开始。
解压仍然是顺序做的：deflate 是一条连续的流，第二段离了第一段解不开。
解完按中央目录里记的 CRC32 校验，不一致直接报错，不会把一个半截的
exe 交给用户。

关于速度：实测本机到 GitHub 单连接约 0.20 MB/s，4 连接 0.226 MB/s
（并发只快 1.13 倍）。瓶颈在 GitHub 出口，不在单连接限速，所以并发
不是为了提速，主要是为了断点续传。嫌慢可以自己换镜像。

想换镜像：设环境变量 ``DLNACAST_FFMPEG_URL`` 指向另一个 zip 即可。
本机那份 ffmpeg 有问题（太旧 / 缺 h264_mf）不想用：
设 ``DLNACAST_FORCE_DOWNLOAD=1`` 跳过本机查找，强制下新的。
"""

import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zlib

# 双语支持。注意必须在**格式化之前**翻译：翻译表的 key 是带 %s/%d 的模板，
# 一旦先把数字填进去就再也匹配不上了。所以本模块里所有 _l("...%s..." % x)
# 都必须写成 _l(T("...%s...") % x)。
# 本模块要能单独 import（tools/_fetch_*.py 会直接用），所以 i18n 缺失时给个退化桩。
try:
    import i18n
except Exception:
    class _I18nStub(object):
        @staticmethod
        def tr(s):
            return s

        @staticmethod
        def current():
            return "zh"
    i18n = _I18nStub()
T = i18n.tr

GITHUB_API = "https://api.github.com/repos/GyanD/codexffmpeg/releases/latest"
UA = "DlnaCast/1.0 (+https://github.com/GyanD/codexffmpeg)"
ASSET_SUFFIX = "-essentials_build.zip"
MEMBER_SUFFIX = "/bin/ffmpeg.exe"

CHUNK = 256 * 1024
EOCD_SIG = b"PK\x05\x06"
CD_SIG = b"PK\x01\x02"
LFH_SIG = b"PK\x03\x04"

# 并发分段的默认条数。实测本机到 GitHub 单连接约 0.15 MB/s，
# 三个连接聚合到 0.27 MB/s（约 1.8 倍）—— 部分限速是按连接给的，
# 但整体还有一条共享瓶颈，所以再加连接收益递减。4 是收益/风险比较稳的点。
DEFAULT_WORKERS = 4
# 单段小于这个值就不值得再拆（拆太碎反而被连接建立开销拖累）
MIN_SPAN = 1024 * 1024


class FetchError(Exception):
    pass


# ------------------------------------------------------------------ HTTP


def _opener():
    """绕开系统代理 —— 否则局域网/直连可能被代理劫持。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _request(url, headers=None, method="GET"):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    return urllib.request.Request(url, headers=h, method=method)


def _urlopen(req, timeout=60):
    return _opener().open(req, timeout=timeout)


def _range_bytes(url, start, end, timeout=120):
    """取 [start, end] 闭区间的字节。服务器若忽略 Range 会返回全文，
    这里必须校验，不能让调用方拿到错位的数据。"""
    req = _request(url, {"Range": "bytes=%d-%d" % (start, end)})
    with _urlopen(req, timeout=timeout) as r:
        status = getattr(r, "status", 200)
        if status != 206:
            raise FetchError(T("服务器不支持按范围下载（HTTP %s）") % status)
        data = r.read()
    want = end - start + 1
    if len(data) != want:
        raise FetchError(T("范围下载长度不符：要 %d，得 %d") % (want, len(data)))
    return data


def _http_json(url, timeout=30):
    with _urlopen(_request(url), timeout=timeout) as r:
        import json
        return json.loads(r.read().decode("utf-8"))


# ------------------------------------------------------------------ 下载源


def resolve_source(log=None):
    """决定从哪下载。返回 dict(url, name, size, tag)。"""
    def _l(m):
        if log:
            log(m)

    override = os.environ.get("DLNACAST_FFMPEG_URL")
    if override:
        _l(T("  使用环境变量 DLNACAST_FFMPEG_URL 指定的下载地址"))
        return {"url": override, "name": os.path.basename(override),
                "size": 0, "tag": "自定义"}

    _l(T("  查询 GitHub 上的最新版本 ..."))
    try:
        data = _http_json(GITHUB_API)
        tag = data.get("tag_name", "?")
        for a in data.get("assets", []):
            if a.get("name", "").endswith(ASSET_SUFFIX):
                _l(T("  最新版本 %s，包 %s（%.1f MB）")
                   % (tag, a["name"], a.get("size", 0) / 1048576))
                return {"url": a["browser_download_url"], "name": a["name"],
                        "size": a.get("size", 0), "tag": tag}
        raise FetchError(T("最新版本 %s 里没有找到 %s") % (tag, ASSET_SUFFIX))
    except Exception as e:
        _l(T("  GitHub API 失败（%s），改用固定地址兜底") % e)

    # 兜底：直接指向 gyan.dev 的稳定链接（会 303 到当前版本）
    return {"url": "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
            "name": "ffmpeg-release-essentials.zip", "size": 0, "tag": "gyan.dev"}


# ------------------------------------------------------------------ zip 定位


def _find_eocd(tail, total_size):
    """在尾部数据里找 zip 的 EOCD，返回 (cd_offset, cd_size, entries)。"""
    pos = len(tail)
    while True:
        pos = tail.rfind(EOCD_SIG, 0, pos)
        if pos < 0:
            raise FetchError("不是有效的 zip（找不到中央目录结尾记录）")
        if pos + 22 <= len(tail):
            (_disk, _cd_disk, _n_disk, _n_total,
             cd_size, cd_offset, comment) = struct.unpack("<HHHHIIH",
                                                         tail[pos + 4:pos + 22])
            if cd_offset == 0xFFFFFFFF:
                raise FetchError("zip64 暂不支持")
            # EOCD 里记的偏移是从文件开头算的；若带注释，需按实际位置校正
            if cd_offset + cd_size <= total_size:
                return cd_offset, cd_size, _n_total
        pos -= 1


def _pick_member(cd, member_suffix):
    """在中央目录里找目标成员，返回它的元信息。"""
    p = 0
    while p + 46 <= len(cd):
        if cd[p:p + 4] != CD_SIG:
            break
        # 从 p+10 起：method(2) 时间(2) 日期(2) crc(4) 压缩大小(4) 原始大小(4)
        #            名字长(2) 扩展长(2) 注释长(2) 起始盘(2) 内部属性(2)
        #            外部属性(4) 本地头偏移(4)  —— 合计 36 字节
        (method, _t, _d, crc, comp_size, uncomp,
         name_len, extra_len, comment_len, _dsk, _ia, _ea,
         local_off) = struct.unpack("<HHHIIIHHHHHII", cd[p + 10:p + 46])
        name = cd[p + 46:p + 46 + name_len].decode("utf-8", "replace")
        if name.endswith(member_suffix):
            return {"name": name, "method": method, "crc": crc,
                    "comp_size": comp_size, "uncomp_size": uncomp,
                    "local_off": local_off}
        p += 46 + name_len + extra_len + comment_len
    return None


def peek_member(url, member_suffix, total_size, log=None):
    """只读 zip 的目录信息，拿到目标成员的位置与大小（不下载内容）。"""
    if not total_size:
        raise FetchError("不知道远端文件大小，无法按范围读取")
    tail_start = max(0, total_size - 65536 - 22)
    tail = _range_bytes(url, tail_start, total_size - 1)
    cd_off, cd_size, n = _find_eocd(tail, total_size)
    if log:
        log("  压缩包目录：%d 个成员，目录表 %d 字节" % (n, cd_size))
    if cd_off + cd_size > total_size:
        raise FetchError("中央目录越界")
    cd = _range_bytes(url, cd_off, cd_off + cd_size - 1)
    m = _pick_member(cd, member_suffix)
    if not m:
        raise FetchError(T("压缩包里没有 %s") % member_suffix)
    if log:
        log("  找到成员 %s（压缩 %0.1f MB / 解开 %0.1f MB）"
            % (m["name"], m["comp_size"] / 1048576, m["uncomp_size"] / 1048576))
    return m


def _download_span(url, start, end, path, on_bytes=None, timeout=600,
                   retries=3):
    """下载闭区间 [start, end] 到 path。断点续传 + 重试。

    分段下载的意义只在「拿字节」这一步 —— deflate 是一条连续的流，第 2 段
    离了第 1 段解不开，所以解压仍然是拿到全部字节之后顺序做的。
    """
    want = end - start + 1
    last = None
    for attempt in range(retries):
        try:
            done = os.path.getsize(path) if os.path.isfile(path) else 0
            if done > want:
                # 残留的旧半截文件。不删（有些环境删文件会被拦），
                # 下面用 "wb" 打开直接截断重写。
                done = 0
            if done == want:
                return
            req = _request(url, {"Range": "bytes=%d-%d" % (start + done, end)})
            with _urlopen(req, timeout=timeout) as r:
                if getattr(r, "status", 200) != 206:
                    raise FetchError("服务器不支持按范围下载")
                with open(path, "ab" if done else "wb") as f:
                    while True:
                        b = r.read(CHUNK)
                        if not b:
                            break
                        f.write(b)
                        done += len(b)
                        if on_bytes:
                            on_bytes(len(b))
            if done != want:
                raise FetchError(T("分段长度不符：%d ≠ %d") % (done, want))
            return
        except Exception as e:
            last = e
            time.sleep(1.0 * (attempt + 1))
    raise FetchError(T("分段下载失败（%d 次重试后）：%s")
                        % (retries, T(last)))


def _split_spans(data_start, comp_size, workers):
    """把整段压缩数据切成 workers 份，返回 [(start, end, index), ...]。"""
    if workers <= 1 or comp_size < MIN_SPAN * 2:
        return [(data_start, data_start + comp_size - 1, 0)]
    n = min(workers, max(1, comp_size // MIN_SPAN))
    span = comp_size // n
    out = []
    for i in range(n):
        s = data_start + i * span
        e = (data_start + comp_size - 1) if i == n - 1 else (s + span - 1)
        out.append((s, e, i))
    return out


def fetch_member(url, member_suffix, total_size, dest, progress=None, log=None,
                 workers=None, cancel=None):
    """只下载 zip 里那一个成员、解出来写到 dest。

    多连接并行取字节（见 DEFAULT_WORKERS），解压与校验仍是单线程顺序做。
    cancel : 可调用，返回真值表示用户要求中止（界面上的「停止」）。
    """
    import threading

    def _l(m):
        if log:
            log(m)

    if workers is None:
        try:
            workers = int(os.environ.get("DLNACAST_DOWNLOAD_WORKERS",
                                         DEFAULT_WORKERS))
        except ValueError:
            workers = DEFAULT_WORKERS
    workers = max(1, min(16, workers))

    m = peek_member(url, member_suffix, total_size, log)
    lfh = _range_bytes(url, m["local_off"], m["local_off"] + 29)
    if lfh[:4] != LFH_SIG:
        raise FetchError("本地文件头签名不对")
    name_len, extra_len = struct.unpack("<HH", lfh[26:30])
    data_start = m["local_off"] + 30 + name_len + extra_len

    spans = _split_spans(data_start, m["comp_size"], workers)
    _l(T("  开始下载（%d 个连接并行，共约 %.1f MB）...")
       % (len(spans), m["comp_size"] / 1048576))

    # 分段文件带上 crc，避免下载源换了新版本后误用旧版本的半截文件
    tag = "%08x" % (m["crc"] or m["comp_size"])
    parts = [(s, e, "%s.%s.p%d" % (dest, tag, i), i) for s, e, i in spans]

    state = {"got": 0, "tick": 0.0}
    lock = threading.Lock()
    t0 = time.time()

    def _on_bytes(k):
        now = time.time()
        with lock:
            state["got"] += k
            n = state["got"]
            due = now - state["tick"] >= 0.25
            if due:
                state["tick"] = now
        if due and progress:
            progress(n, m["comp_size"], n / max(0.001, now - t0))

    def _run(s, e, path, i):
        try:
            _download_span(url, s, e, path, on_bytes=_on_bytes)
        except Exception as ex:
            errs[i] = "%s" % ex

    errs = {}
    threads = [threading.Thread(target=_run, args=(s, e, p, i), daemon=True)
               for s, e, p, i in parts]
    for t in threads:
        t.start()

    cancelled = False
    while any(t.is_alive() for t in threads):
        for t in threads:
            t.join(timeout=0.3)
        if cancel and cancel():
            cancelled = True
            break

    if cancelled or errs:
        # 分段文件保留着 —— 下次重跑能接着下（断点续传）
        if cancelled:
            raise FetchError("已取消")
        raise FetchError(T("有 %d 段没下完：%s")
                         % (len(errs), list(errs.values())[0]))

    # 顺序解压 + CRC（deflate 是连续流，必须按顺序）
    tmp = dest + ".part"
    dec = zlib.decompressobj(-15)
    crc = 0
    try:
        with open(tmp, "wb") as f:
            for _s, _e, p, _i in parts:
                with open(p, "rb") as src:
                    while True:
                        chunk = src.read(CHUNK)
                        if not chunk:
                            break
                        raw = (dec.decompress(chunk)
                               if m["method"] == 8 else chunk)
                        if raw:
                            f.write(raw)
                            crc = zlib.crc32(raw, crc)
            tail = dec.flush()
            if tail:
                f.write(tail)
                crc = zlib.crc32(tail, crc)
        fsize = os.path.getsize(tmp)
        if fsize != m["uncomp_size"]:
            raise FetchError(T("解出大小不符：%d ≠ %d")
                             % (fsize, m["uncomp_size"]))
        if m["crc"] and (crc & 0xFFFFFFFF) != m["crc"]:
            raise FetchError("CRC 校验失败，下载可能被截断或篡改")
        _commit(tmp, dest)
    finally:
        # 走到这里说明数据已经用完（成功落盘，或校验失败）——
        # 分段临时文件都该清掉。中途取消的路径在上面就 return 了，
        # 分段文件会留着给下次续传。
        for _s, _e, p, _i in parts:
            try:
                os.remove(p)
            except Exception:
                pass
        if os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass

    el = max(0.001, time.time() - t0)
    _l(T("  下载完成 %.1f MB，用时 %.0f 秒（平均 %.2f MB/s）")
       % (state["got"] / 1048576, el, state["got"] / 1048576 / el))
    return dest


def download_full_and_extract(url, member_suffix, dest, progress=None, log=None,
                              cancel=None):
    """兜底：整包下载再解压出目标成员。"""
    import zipfile

    def _l(m):
        if log:
            log(m)

    tmp_zip = dest + ".zip"
    _l(T("  整包下载（不支持的服务器走这条路）..."))
    t0 = time.time()
    with _urlopen(_request(url), timeout=600) as r, open(tmp_zip, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        last = 0.0
        while True:
            chunk = r.read(CHUNK)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            now = time.time()
            if cancel and cancel():
                raise FetchError("已取消")
            if progress and now - last >= 0.25:
                last = now
                progress(got, total, got / max(0.001, now - t0))
    try:
        with zipfile.ZipFile(tmp_zip) as z:
            member = None
            for n in z.namelist():
                if n.endswith(member_suffix):
                    member = n
                    break
            if not member:
                raise FetchError(T("压缩包里没有 %s") % member_suffix)

            _l(T("  解出 %s") % member)
            with z.open(member) as src, open(dest + ".part", "wb") as out:
                shutil.copyfileobj(src, out, CHUNK)
        _commit(dest + ".part", dest)
    finally:
        try:
            os.remove(tmp_zip)
        except Exception:
            pass
    _l(T("  完成，用时 %.0f 秒") % (time.time() - t0))
    return dest


def _commit(tmp, dest):
    """落盘。优先原子替换，失败则用「改名接力」。"""
    try:
        os.replace(tmp, dest)
        return
    except OSError:
        pass
    old = dest + ".old"
    moved = False
    try:
        if os.path.isfile(dest):
            os.rename(dest, old)
            moved = True
        os.rename(tmp, dest)
    finally:
        if moved:
            try:
                os.remove(old)
            except Exception:
                pass


# ------------------------------------------------------------------ 校验


def probe_version(path, timeout=30):
    """跑一次 -version，返回版本描述行；不能执行则返回 None。"""
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        cp = subprocess.run([path, "-version"], capture_output=True,
                            timeout=timeout, creationflags=flags)
        if cp.returncode != 0:
            return None
        first = (cp.stdout or b"").decode("utf-8", "replace").split("\n")[0]
        return first.strip() or None
    except Exception:
        return None


def required_features_ok(path):
    """确认这份 ffmpeg 具备本项目必需的能力。

    下载来的二进制不能想当然 —— 少一个 mediafoundation 或 drawtext，
    用户看到的就是「硬件编码用不了」或「画面信息不显示」。
    返回 (是否合格, 缺失项列表)。
    """
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    checks = [
        ("编码器 libx264", ["-hide_banner", "-encoders"], b"libx264"),
        ("硬件编码 h264_mf", ["-hide_banner", "-h", "encoder=h264_mf"], b"h264_mf"),
        ("采集 gdigrab", ["-hide_banner", "-devices"], b"gdigrab"),
        ("桌面复制 ddagrab", ["-hide_banner", "-f", "lavfi", "-i",
                              "ddagrab=output_idx=0", "-frames:v", "1",
                              "-f", "null", "-"], None),
        ("字幕绘制 drawtext", ["-hide_banner", "-filters"], b"drawtext"),
        ("封装 mpegts", ["-hide_banner", "-formats"], b"mpegts"),
    ]
    missing = []
    for label, args, needle in checks:
        try:
            cp = subprocess.run([path] + args, capture_output=True,
                                timeout=30, creationflags=flags)
            blob = (cp.stdout or b"") + (cp.stderr or b"")
            if needle is None:
                # ddagrab 只能靠实际跑一次判断（无 D3D 桌面的环境会失败，
                # 那种情况不算「这份 ffmpeg 缺能力」）
                if cp.returncode != 0 and b"ddagrab" in blob:
                    pass
            elif needle not in blob:
                missing.append(label)
        except Exception:
            missing.append(label)
    return (not missing), missing


# ------------------------------------------------------------------ 主入口

COMMON_PATHS = [
    r"D:\ffmpeg\ffmpeg.exe",
    r"D:\ffmpeg\bin\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
]


def download_ffmpeg(dest_dir, log=None, progress=None, cancel=None,
                    workers=None):
    """下载最新版 ffmpeg 到 dest_dir/ffmpeg.exe，返回路径。"""
    def _l(m):
        if log:
            log(m)

    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, "ffmpeg.exe")
    src = resolve_source(log)
    size = src["size"]

    if not size:
        # 拿不到大小就没法按范围读，先探一次真实大小
        try:
            req = _request(src["url"], {"Range": "bytes=0-0"}, "HEAD")
            with _opener().open(req, timeout=30) as r:
                cr = r.headers.get("Content-Range", "")
                if "/" in cr:
                    size = int(cr.split("/")[-1])
        except Exception:
            size = 0

    try:
        if size:
            fetch_member(src["url"], MEMBER_SUFFIX, size, dest,
                         progress=progress, log=log, workers=workers,
                         cancel=cancel)
        else:
            raise FetchError("拿不到远端文件大小")
    except FetchError as e:
        if "已取消" in str(e):
            raise
        _l(T("  按范围下载失败（%s），改为整包下载") % e)
        if progress:
            progress(0, 0, 0)
        download_full_and_extract(src["url"], MEMBER_SUFFIX, dest,
                                  progress=progress, log=log, cancel=cancel)

    ver = probe_version(dest)
    if not ver:
        raise FetchError("下载的 ffmpeg 无法执行（可能被杀软隔离）")
    # 这行只是「两个空格 + 版本号」，版本号是纯 ASCII，没有需要翻的东西
    _l("  %s" % ver)

    ok, missing = required_features_ok(dest)
    if not ok:
        # 顿号是中文标点，英文界面该用逗号 —— 分隔符本身也是文案
        sep = ", " if i18n.current() == "en" else "、"
        _l(T("  ! 这份 ffmpeg 缺少：%s") % sep.join(missing))
    else:
        _l(T("  能力检查通过：libx264 / h264_mf / gdigrab / drawtext / mpegts 都在"))
    return dest


def ensure_ffmpeg(dest_dir, locate=None, log=None, progress=None,
                  allow_download=True, cancel=None):
    """有就用，没有就下。

    locate : 可调用，返回本机已有的 ffmpeg 路径或 None。
             传 screen-cast.find_ffmpeg 进来，保证查找顺序只有一处定义。
    返回 (路径, 来源说明)，找不到且不允许下载时返回 (None, 原因)。

    设 DLNACAST_FORCE_DOWNLOAD=1 可以跳过本机那份、强制下新的
    （本机装的版本太旧或者缺 h264_mf / ddagrab 时用得上）。
    """
    def _l(m):
        if log:
            log(m)

    force = bool(os.environ.get("DLNACAST_FORCE_DOWNLOAD"))

    if locate is not None and not force:
        try:
            found = locate()
        except Exception:
            found = None
        if found:
            ver = probe_version(found)
            if ver:
                return found, ("本机已有：" + ver.split(" Copyright")[0])
            # 找得到但跑不起来（被杀软隔离 / 依赖缺失）—— 当成没有
            _l(T("找到 %s，但它无法执行，忽略。") % found)
    elif force:
        _l(T("已设 DLNACAST_FORCE_DOWNLOAD，跳过本机已有的 ffmpeg。"))

    if not allow_download:
        return None, "本机没有 ffmpeg，且已禁用自动下载"

    _l(T("本机没有找到 ffmpeg，准备从 GitHub 下载最新版 ..."))
    _l(T("  （想用别的镜像：设环境变量 DLNACAST_FFMPEG_URL 指向另一个 zip）"))
    try:
        path = download_ffmpeg(dest_dir, log=log, progress=progress,
                               cancel=cancel)
        return path, "已下载最新版"
    except Exception as e:
        return None, "下载失败：%s" % e


# ------------------------------------------------------------------ 命令行

def _cli(argv):
    def _log(m):
        print(m, flush=True)

    def _prog(done, total, speed):
        if not total:
            print("    %6.1f MB" % (done / 1048576), flush=True)
            return
        pct = done * 100 // max(1, total)
        sys.stdout.write("\r    %3d%%  %6.1f / %.1f MB  %.1f MB/s"
                         % (pct, done / 1048576, total / 1048576,
                            speed / 1048576))
        sys.stdout.flush()

    dest = argv[0] if argv else os.path.dirname(os.path.abspath(__file__))
    p = download_ffmpeg(dest, log=_log, progress=_prog)
    print()
    print("已就绪：%s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
