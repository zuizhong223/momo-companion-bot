# -*- coding: utf-8 -*-
"""S11 备份轮转：保留最近 7 天 + 4 周，默认只打印计划，不删。

用法：
  python ops\\backup\\rotate.py                 # 只看计划
  python ops\\backup\\rotate.py --apply --yes   # 真删（先确认）
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:/demo")
BACKUPS = ROOT / "backups"


def plan(files: list[Path], now: float | None = None,
         daily: int = 7, weekly: int = 4) -> tuple[list[Path], list[Path]]:
    """返回 (keep, delete)。保留最近 daily 个 + 更早的 weekly 个周代表。"""
    now = time.time() if now is None else now
    files = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return [], []
    keep: set[Path] = set(files[:daily])  # 最近 7 个，按天不够也保留
    weeks: set[str] = set()
    for p in files[daily:]:
        w = datetime.fromtimestamp(p.stat().st_mtime).strftime("%G-W%V")
        if len(weeks) < weekly and w not in weeks:
            weeks.add(w)
            keep.add(p)
    delete = [p for p in files if p not in keep]
    return sorted(keep), delete


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()
    files = sorted(BACKUPS.glob("demo_backup_*.zip"))
    keep, delete = plan(files)
    print(f"备份 {len(files)} 个；保留 {len(keep)}；删除 {len(delete)}")
    for p in keep:
        print("  KEEP  ", p.name, f"{p.stat().st_size/1048576:.1f}MB")
    for p in delete:
        print("  DEL   ", p.name, f"{p.stat().st_size/1048576:.1f}MB")
    if args.apply:
        if not args.yes:
            print("\n拒绝：--apply 还要加 --yes（先看计划再确认）")
            return 2
        for p in delete:
            p.unlink()
        print(f"\n已删除 {len(delete)} 个")
    else:
        print("\n(dry-run；加 --apply --yes 才删除)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
