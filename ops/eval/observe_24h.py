# -*- coding: utf-8 -*-
"""24h 观察快照：每次运行把台账/历史/分布追加一行 JSONL。

用法：
  python ops\\eval\\observe_24h.py
建议每小时跑一次（或手动在关键节点跑）。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:/demo")
DB = ROOT / "instances/11111111-1111-4111-8111-111111111111/core/data/data_v4.db"
OUT = ROOT / "ops/eval/reports/observe_24h.jsonl"
sys.path.insert(0, str(ROOT / "ops/eval"))
from live_dist import bubbles, load_messages, metrics  # noqa: E402


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        tasks = dict(con.execute(
            "select kind || ':' || state, count(*) from yy_send_task group by 1").fetchall())
        hist = dict(con.execute(
            "select history_state, count(*) from yy_send_task group by 1").fetchall())
        last_sticker = con.execute(
            "select created_at,state from yy_send_task where kind='sticker'"
            " order by created_at desc limit 1").fetchone()
    finally:
        con.close()
    replies, st, tt = load_messages(None)
    m = metrics(bubbles(replies))
    row = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tasks": tasks, "history": hist,
        "last_sticker": last_sticker,
        "assistant_replies": len(replies),
        "metrics": m,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    print(json.dumps(row, ensure_ascii=False, indent=2, default=str))
    print("->", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
