#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""磁盘清理：只删"过期临时文件"和"过期日志"，不碰模型、备份、数据库。

默认干跑；加 --apply 才删。

用法：
  python ops\\verify\\cleanup.py                    # 看计划
  python ops\\verify\\cleanup.py --days 3 --apply
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(r"D:/demo")
INST = ROOT / "instances/11111111-1111-4111-8111-111111111111"
TEMP = INST / "core/data/temp"

# (目录, 文件名 glob, 说明)
TARGETS = [
    (TEMP, "*", "TTS/图片临时文件"),
    (ROOT, "napcat_boot_qr*.log", "NapCat 启动日志"),
    (ROOT, "astrbot_run*.log", "AstrBot 启动日志（旧）"),
    (ROOT, "astrbot_err*.log", "AstrBot 错误日志（旧）"),
]

# 无论多旧都不删
KEEP = {"astrbot_run2.log", "astrbot_err2.log"}


def plan(days: int) -> list[tuple[Path, int]]:
    cutoff = time.time() - days * 86400
    out = []
    for base, glob, _ in TARGETS:
        if not base.exists():
            continue
        for f in base.glob(glob):
            if not f.is_file() or f.name in KEEP:
                continue
            try:
                if f.stat().st_mtime < cutoff:
                    out.append((f, f.stat().st_size))
            except OSError:
                continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3, help="超过多少天算过期（默认 3）")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    items = plan(args.days)
    total = sum(s for _, s in items)
    print(f"过期文件（>{args.days} 天）: {len(items)} 个，{total/1024/1024:.1f} MB")
    for f, s in sorted(items, key=lambda x: -x[1])[:12]:
        print(f"  {s/1024/1024:7.1f} MB  {f.relative_to(ROOT)}")
    if len(items) > 12:
        print(f"  … 另外 {len(items) - 12} 个")

    if not args.apply:
        print("\n这是干跑。确认后加 --apply。")
        return 0
    n = 0
    for f, _ in items:
        try:
            f.unlink()
            n += 1
        except Exception as e:  # noqa: BLE001
            print(f"  删除失败 {f.name}: {e}")
    print(f"\n已删 {n} 个，释放 {total/1024/1024:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
