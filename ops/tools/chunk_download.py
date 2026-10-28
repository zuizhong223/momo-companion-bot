# -*- coding: utf-8 -*-
"""分块下载器：大文件被中途重置时用。

背景：这台机器对 600MB 的持续下载会被反复 reset（curl -C - / winget 都失败），
但 1MB 的 Range 请求能成。所以按块拉、每块单独重试、按偏移写入同一个文件。

用法：
  python ops\\tools\\chunk_download.py <url> <输出文件> [--chunk-mb 2] [--resume]
"""
import argparse
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def head_size(url: str) -> int:
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=30) as r:
        return int(r.headers.get("Content-Length") or 0)


def fetch_range(url: str, start: int, end: int, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("out")
    ap.add_argument("--chunk-mb", type=float, default=2.0)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--retries", type=int, default=6)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    total = head_size(args.url)
    if total <= 0:
        print("拿不到文件大小（服务端没给 Content-Length）")
        return 1
    out = Path(args.out)
    chunk = int(args.chunk_mb * 1024 * 1024)

    have = out.stat().st_size if (args.resume and out.exists()) else 0
    if have > total:
        print(f"本地文件比远端还大（{have} > {total}），删掉重下")
        out.unlink()
        have = 0
    mode = "r+b" if have else "wb"
    print(f"总大小 {total / 1e6:.1f} MB，已有 {have / 1e6:.1f} MB，"
          f"块大小 {args.chunk_mb} MB")

    t0 = time.time()
    with out.open(mode) as f:
        f.seek(have)
        pos = have
        fails_in_row = 0
        while pos < total:
            end = min(pos + chunk - 1, total - 1)
            data = None
            for attempt in range(args.retries):
                try:
                    data = fetch_range(args.url, pos, end)
                    if len(data) != end - pos + 1:
                        raise IOError(f"块长度不对: {len(data)} != {end - pos + 1}")
                    break
                except Exception as e:  # noqa: BLE001
                    wait = min(10, 1 + attempt * 2)
                    print(f"  块 {pos // chunk} 失败({type(e).__name__})，{wait}s 后重试")
                    time.sleep(wait)
                    data = None
            if data is None:
                fails_in_row += 1
                if fails_in_row >= 3:
                    print(f"连续 3 块失败，停在 {pos / 1e6:.1f} MB（可 --resume 续）")
                    return 1
                time.sleep(5)
                continue
            fails_in_row = 0
            f.write(data)
            pos = end + 1
            if (pos // chunk) % 10 == 0:
                pct = pos / total * 100
                print(f"  {pct:5.1f}%  {pos / 1e6:6.1f} MB  "
                      f"{(time.time() - t0) / 60:.1f} 分钟", flush=True)

    size = out.stat().st_size
    print(f"完成：{size / 1e6:.1f} MB / 期望 {total / 1e6:.1f} MB")
    if size != total:
        print("大小不一致，需要 --resume 继续")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
