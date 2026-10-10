# -*- coding: utf-8 -*-
"""分阶段验证 ffmpeg_fetch：先只读远端 zip 目录（几乎不耗流量）。"""
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import ffmpeg_fetch as F


def log(m):
    print(m, flush=True)


def main():
    stage = sys.argv[1] if len(sys.argv) > 1 else "source"

    if stage in ("source", "all"):
        print("=== 阶段1：解析下载源 ===")
        t0 = time.time()
        src = F.resolve_source(log)
        print("  用时 %.2f s" % (time.time() - t0))
        print("  url = %s" % src["url"])
        print("  name= %s" % src["name"])
        print("  size= %d (%.1f MB)" % (src["size"], src["size"] / 1048576))
        print("  tag = %s" % src["tag"])
        globals()["_SRC"] = src

    if stage in ("peek", "all"):
        print()
        print("=== 阶段2：只读 zip 目录，定位成员 ===")
        src = globals().get("_SRC") or F.resolve_source(None)
        size = src["size"]
        if not size:
            req = F._request(src["url"], {"Range": "bytes=0-0"}, "HEAD")
            with F._opener().open(req, timeout=30) as r:
                cr = r.headers.get("Content-Range", "")
                size = int(cr.split("/")[-1])
            print("  探测到大小 %d" % size)
        t0 = time.time()
        m = F.peek_member(src["url"], F.MEMBER_SUFFIX, size, log)
        print("  用时 %.2f s" % (time.time() - t0))
        print("  local_off   = %d" % m["local_off"])
        print("  method      = %d (8=deflate)" % m["method"])
        print("  comp_size   = %d (%.1f MB)" % (m["comp_size"], m["comp_size"] / 1048576))
        print("  uncomp_size = %d (%.1f MB)" % (m["uncomp_size"], m["uncomp_size"] / 1048576))
        print("  压缩比      = %.2fx" % (m["uncomp_size"] / max(1, m["comp_size"])))
        print("  占整包比例  = %.1f%%" % (m["comp_size"] * 100.0 / size))


if __name__ == "__main__":
    main()
