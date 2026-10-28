# -*- coding: utf-8 -*-
"""人工结案：把 unknown / failed 的发送任务标成 sent 或 dropped。

  python ops\\send\\resolve.py --list                 # 看还有哪些卡着
  python ops\\send\\resolve.py --task <id> --as dropped
  python ops\\send\\resolve.py --task <id> --as sent    # 确认其实发出去了（不会补历史）

`unknown` 是"不知道发没发"，只能人来判断；程序不猜。
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(r"D:/demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

from astrbot.core.utils.send_ledger import Ledger  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--task", default="")
    ap.add_argument("--as", dest="as_state", default="", choices=["", "sent", "dropped"])
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    ledger = Ledger()
    if args.list or not args.task:
        rows = ledger.stats()
        print("task 状态:", rows["state"])
        print("历史状态:", rows["history"])
        import sqlite3

        con = sqlite3.connect(f"file:{ledger.db_path}?mode=ro", uri=True)
        pending = con.execute(
            "select task_id, state, created_at, substr(text,1,30) from yy_send_task"
            " where state in ('unknown','failed') order by created_at desc limit 20"
        ).fetchall()
        con.close()
        if pending:
            print("\n需要人工判断：")
            for tid, st, ts, text in pending:
                print(f"  {tid[:12]} {st:<8} {ts} {text!r}")
            print("\n用 --task <id> --as sent|dropped 结案")
        else:
            print("\n没有卡住的任务")
        return 0

    if not args.as_state:
        print("缺少 --as sent|dropped")
        return 1

    # 允许用前缀（列表里只显示 12 位）
    import sqlite3

    con = sqlite3.connect(f"file:{ledger.db_path}?mode=ro", uri=True)
    hits = [r[0] for r in con.execute(
        "select task_id from yy_send_task where task_id like ?", (args.task + "%",))]
    con.close()
    if not hits:
        print(f"找不到任务: {args.task}")
        return 1
    if len(hits) > 1:
        print(f"前缀不唯一（{len(hits)} 个匹配），请给更长的 id")
        return 1
    task_id = hits[0]
    ok = ledger.resolve(task_id, args.as_state)
    print(("已结案: " if ok else "未结案（状态不允许）: ") + task_id)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
