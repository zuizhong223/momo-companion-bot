#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""备份保留策略：全量包全留，增量只留最近 N 个。

默认**只打印计划，不删**；加 --apply 才真删。

判定"全量"的方式：读包内 manifest.json 的 "full" 字段（老包没有该字段 → 当增量）。

用法：
  python ops\\backup\\prune.py                 # 干跑
  python ops\\backup\\prune.py --keep 30       # 增量留 30 个
  python ops\\backup\\prune.py --keep 30 --apply
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(r"D:/demo")
BACKUPS = ROOT / "backups"


def read_meta(z: Path) -> dict:
    if z.suffix.lower() == ".7z":
        # 7z 包读不了 manifest，用体积粗判（全量包明显更大）
        return {"full": z.stat().st_size > 44 * 1024 * 1024, "created": ""}
    try:
        with zipfile.ZipFile(z) as f:
            with f.open("manifest.json") as fh:
                d = json.loads(fh.read().decode("utf-8"))
        return {"full": bool(d.get("full")), "created": d.get("created", "")}
    except Exception:  # noqa: BLE001
        return {"full": False, "created": ""}


def plan(keep: int, min_keep_total: int = 3) -> tuple[list[Path], list[Path], float]:
    zips = sorted(list(BACKUPS.glob("demo_backup_*.zip")) + list(BACKUPS.glob("demo_backup_*.7z")), key=lambda p: p.stat().st_mtime)
    metas = {z: read_meta(z) for z in zips}
    fulls = [z for z in zips if metas[z]["full"]]
    incs = [z for z in zips if not metas[z]["full"]]
    keep_set = set(fulls) | set(incs[-keep:]) | set(zips[-min_keep_total:])
    drop = [z for z in zips if z not in keep_set]
    freed = sum(z.stat().st_size for z in drop) / (1024 ** 3)
    return drop, sorted(keep_set), freed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=int, default=30, help="增量保留个数（默认 30）")
    ap.add_argument("--apply", action="store_true", help="真的删除（默认只打印）")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    if not BACKUPS.exists():
        print("没有备份目录")
        return 1
    drop, keep, freed = plan(args.keep)
    total = len(list(BACKUPS.glob("demo_backup_*.zip")))
    print(f"现有 {total} 个包；策略：全量全留 + 增量留最近 {args.keep} 个 + 至少留 3 个")
    print(f"将删除 {len(drop)} 个，释放约 {freed:.2f} GB；保留 {len(keep)} 个")
    for z in drop[:15]:
        print(f"  - {z.name}  {z.stat().st_size / 1e6:.1f} MB")
    if len(drop) > 15:
        print(f"  … 另外 {len(drop) - 15} 个")

    if not args.apply:
        print("\n这是干跑。确认无误后加 --apply 执行。")
        return 0
    n = 0
    for z in drop:
        try:
            z.unlink()
            n += 1
        except Exception as e:  # noqa: BLE001
            print(f"  删除失败 {z.name}: {e}")
    print(f"\n已删除 {n} 个包，释放 {freed:.2f} GB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
