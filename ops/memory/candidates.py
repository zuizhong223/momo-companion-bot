# -*- coding: utf-8 -*-
"""A06：候选记忆管理（list / promote / drop）。

默认只预览；加 --apply 才写入。用法：
  python ops\\memory\\candidates.py list
  python ops\\memory\\candidates.py promote --predicate 喜欢 --obj 蓝莓 --multi --apply
  python ops\\memory\\candidates.py drop --predicate 住在 --obj 南京 --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(r"D:/demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

from astrbot.core.utils.memory_facts import (  # noqa: E402
    drop_candidate,
    list_candidates,
    promote_candidate,
    recall,
)

DEFAULT_UMO = "aiocqhttp:FriendMessage:100000000"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("list", "promote", "drop"))
    ap.add_argument("--umo", default=DEFAULT_UMO)
    ap.add_argument("--predicate", default="")
    ap.add_argument("--obj", default="")
    ap.add_argument("--multi", action="store_true", default=None,
                    help="强制多值；不传按属性（喜欢/在练/养默认多值，其它单值）")
    ap.add_argument("--single", action="store_false", dest="multi",
                    help="强制单值更正")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--db", default="")
    args = ap.parse_args()
    db = args.db or None

    if args.action == "list":
        rows = list_candidates(args.umo, db_path=db)
        if not rows:
            print("（没有候选）")
            return 0
        for r in rows:
            print(f"{r['predicate']}：{r['obj']}  source={r.get('source')} "
                  f"msg={r.get('source_msg_id')} conf={r.get('confidence')}")
        return 0

    if not args.predicate or not args.obj:
        print("promote/drop 需要 --predicate 和 --obj")
        return 2
    if not args.apply:
        print("预览：加 --apply 才写入")
        return 0

    if args.action == "promote":
        ok = promote_candidate(args.umo, args.predicate, args.obj,
                               multi=args.multi, db_path=db)
        print("promoted" if ok else "not found")
        if ok:
            for r in recall(args.umo, like=args.obj, db_path=db):
                print(f"  active: {r['predicate']}：{r['obj']} ({r['status']})")
        return 0 if ok else 1

    ok = drop_candidate(args.umo, args.predicate, args.obj, db_path=db)
    print("dropped" if ok else "not found")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

