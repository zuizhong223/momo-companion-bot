# -*- coding: utf-8 -*-
"""L4 日程变体 CLI（不启动生活层、不接机器人）。

用法：
  python ops/life_day.py exam   2026-10-28
  python ops/life_day.py exam   2026-10-28 --end 2026-10-30
  python ops/life_day.py outing 2026-10-29
  python ops/life_day.py normal 2026-10-29
  python ops/life_day.py list

只写 yy_life_day_override；不会发消息、不会改 prompt、不会开启生活层。
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:/demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

UMO = os.environ.get("DEMO_UMO") or "aiocqhttp:FriendMessage:100000000"


def main() -> int:
    from astrbot.core.utils import life_state as ls

    ap = argparse.ArgumentParser(description="L4 exam/outing schedule override")
    ap.add_argument("kind", choices=("exam", "outing", "normal", "list"))
    ap.add_argument("date", nargs="?")
    ap.add_argument("--end", default=None, help="inclusive end date for a range")
    ap.add_argument("--start-time", default=None, help="HH:MM, exam/outing start")
    ap.add_argument("--end-time", default=None, help="HH:MM, exam/outing end")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    if args.kind == "list":
        for r in ls.list_day_overrides(UMO, db_path=args.db):
            print(f"{r['date']}  {r['kind']}  {r['payload']}")
        return 0

    if not args.date:
        print("date required: YYYY-MM-DD", file=sys.stderr)
        return 2
    try:
        start = dt.date.fromisoformat(args.date)
        end = dt.date.fromisoformat(args.end) if args.end else start
    except ValueError:
        print("date must be YYYY-MM-DD", file=sys.stderr)
        return 2
    if end < start:
        print("--end must be >= date", file=sys.stderr)
        return 2

    ok = True
    payload = {}
    if args.kind == "exam":
        if args.start_time:
            payload["exam_start"] = args.start_time
        if args.end_time:
            payload["exam_end"] = args.end_time
    elif args.kind == "outing":
        if args.start_time:
            payload["start"] = args.start_time
        if args.end_time:
            payload["end"] = args.end_time
    try:
        payload = ls.validate_day_payload(args.kind, payload)
    except ValueError as e:
        print(f"invalid time: {e}", file=sys.stderr)
        return 2
    d = start
    while d <= end:
        r = ls.set_day_override(UMO, d, args.kind, payload=payload,
                                db_path=args.db)
        print(f"{UMO} {d} -> {args.kind}: {'OK' if r else 'FAIL'}")
        ok = ok and r
        d += dt.timedelta(days=1)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

