# -*- coding: utf-8 -*-
"""L6 地点观测 CLI（只写/查地点表，不接机器人）。

用法：
  python ops/life_place.py set 练习室
  python ops/life_place.py list
  python ops/life_place.py clear
  python ops/life_place.py prune
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:/demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

UMO = os.environ.get("DEMO_UMO") or "aiocqhttp:FriendMessage:100000000"


def main() -> int:
    from astrbot.core.utils import life_state as ls

    ap = argparse.ArgumentParser(description="L6 place CLI")
    ap.add_argument("action", choices=("set", "list", "clear", "prune"))
    ap.add_argument("place", nargs="?")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    if args.action == "set":
        if not args.place:
            print("place required", file=sys.stderr)
            return 2
        ok = ls.set_place(UMO, args.place, "user_statement", 0.9,
                          ttl_hours=2, db_path=args.db)
        print(f"{UMO} -> {args.place}: {'OK' if ok else 'FAIL'}")
        return 0 if ok else 1
    if args.action == "list":
        print(ls.current_place(UMO, db_path=args.db))
        return 0
    if args.action == "clear":
        print("cleared" if ls.clear_place(UMO, db_path=args.db) else "nothing")
        return 0
    if args.action == "prune":
        print("pruned", ls.prune_place(UMO, db_path=args.db))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())

