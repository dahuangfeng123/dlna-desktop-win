# -*- coding: utf-8 -*-
"""列出远端 zip 成员；用一个**小成员**快速验证「解压 + CRC + 大小校验」链路。

大成员（ffmpeg.exe 35.7 MB）在慢网络上要 8 分钟，先拿小文件把逻辑跑通，
省得等半天才发现解压写错。
"""
import os
import struct
import sys
import time
import zlib

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import ffmpeg_fetch as F


def list_members(url, total_size, log=None):
    tail_start = max(0, total_size - 65536 - 22)
    tail = F._range_bytes(url, tail_start, total_size - 1)
    cd_off, cd_size, n = F._find_eocd(tail, total_size)
    cd = F._range_bytes(url, cd_off, cd_off + cd_size - 1)
    out = []
    p = 0
    while p + 46 <= len(cd) and cd[p:p + 4] == F.CD_SIG:
        (method, _t, _d, crc, comp, uncomp, nl, el, cl,
         _dsk, _ia, _ea, loff) = struct.unpack("<HHHIIIHHHHHII", cd[p + 10:p + 46])
        name = cd[p + 46:p + 46 + nl].decode("utf-8", "replace")
        out.append({"name": name, "method": method, "crc": crc,
                    "comp_size": comp, "uncomp_size": uncomp,
                    "local_off": loff})
        p += 46 + nl + el + cl
    return out


def fetch_one(url, m, dest):
    """完全照搬 fetch_member 的逻辑，但允许指定任意成员。"""
    lfh = F._range_bytes(url, m["local_off"], m["local_off"] + 29)
    assert lfh[:4] == F.LFH_SIG, "本地文件头签名不对"
    name_len, extra_len = struct.unpack("<HH", lfh[26:30])
    data_start = m["local_off"] + 30 + name_len + extra_len

    resp = F._urlopen(F._request(url, {
        "Range": "bytes=%d-%d" % (data_start,
                                  data_start + m["comp_size"] - 1)}), timeout=300)
    assert getattr(resp, "status", 200) == 206, "服务器不支持 Range"
    dec = zlib.decompressobj(-15)
    crc = 0
    got = 0
    with resp, open(dest, "wb") as f:
        while True:
            chunk = resp.read(F.CHUNK)
            if not chunk:
                break
            got += len(chunk)
            raw = dec.decompress(chunk) if m["method"] == 8 else chunk
            if raw:
                f.write(raw)
                crc = zlib.crc32(raw, crc)
        raw = dec.flush()
        if raw:
            f.write(raw)
            crc = zlib.crc32(raw, crc)
    return got, crc & 0xFFFFFFFF


def main():
    src = F.resolve_source(lambda m: print(m, flush=True))
    url, size = src["url"], src["size"]
    print()
    print("=== 列出 zip 成员 ===")
    t0 = time.time()
    ms = list_members(url, size)
    print("  共 %d 个成员，用时 %.2f s" % (len(ms), time.time() - t0))
    ms.sort(key=lambda m: m["comp_size"])
    print()
    print("  --- 最小的 8 个 ---")
    for m in ms[:8]:
        print("    %8.1f KB  压缩后 %7.1f KB  %s"
              % (m["uncomp_size"] / 1024.0, m["comp_size"] / 1024.0, m["name"]))
    print()
    print("  --- 目标成员 ---")
    tgt = [m for m in ms if m["name"].endswith("/bin/ffmpeg.exe")]
    for m in tgt:
        print("    %8.1f MB  压缩后 %7.1f MB  %s"
              % (m["uncomp_size"] / 1048576.0, m["comp_size"] / 1048576.0,
                 m["name"]))

    # 挑一个小的、非空的成员做链路验证
    probe = None
    for m in ms:
        if m["comp_size"] > 1024 and m["name"].endswith(".txt"):
            probe = m
            break
    if not probe:
        probe = ms[0]
    print()
    print("=== 用小成员验证解压链路：%s ===" % probe["name"])
    out = os.path.join(HERE, "_ffmpeg_test", "probe_" +
                       os.path.basename(probe["name"]))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    t0 = time.time()
    got, crc = fetch_one(url, probe, out)
    fsize = os.path.getsize(out)
    print("  下载压缩数据 %d 字节，用时 %.2f s" % (got, time.time() - t0))
    print("  解出 %d 字节（期望 %d）→ %s"
          % (fsize, probe["uncomp_size"],
             "一致" if fsize == probe["uncomp_size"] else "**不符**"))
    print("  CRC32 计算值 %08X / 期望 %08X → %s"
          % (crc, probe["crc"], "通过" if crc == probe["crc"] else "**失败**"))
    ok = (fsize == probe["uncomp_size"] and crc == probe["crc"])
    print()
    print("  链路验证：%s" % ("通过 ✅" if ok else "失败 ❌"))
    with open(out, "rb") as f:
        print("  内容开头：%r" % f.read(120))


if __name__ == "__main__":
    main()
