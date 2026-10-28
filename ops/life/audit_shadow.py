# -*- coding: utf-8 -*-
"""生活层 / 事件影子审计：日志条数 vs 事件表状态。只读。"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(r"D:/demo")
DB = ROOT / "instances/11111111-1111-4111-8111-111111111111/core/data/data_v4.db"
LOGS = [
    ROOT / "astrbot_run2.log",
    ROOT / "astrbot.log",
    ROOT / "instances/11111111-1111-4111-8111-111111111111/core/data/logs/astrbot.log",
]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    life, event = [], []
    for p in LOGS:
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "[生活层影子]" in line:
                life.append(line)
            if "[事件影子]" in line:
                event.append(line)
    print(f"生活层影子日志: {len(life)} 条")
    for x in life[-3:]:
        print("  " + x[-160:])
    print(f"事件影子日志: {len(event)} 条")
    for x in event[-2:]:
        print("  " + x[-160:])

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    print("life status:", con.execute(
        "select status,count(*) from yy_life_event group by status").fetchall())
    print("life told:", con.execute(
        "select count(*) from yy_life_event where told_at is not null").fetchone()[0],
        "forgotten:", con.execute(
        "select count(*) from yy_life_event where forgotten_at is not null").fetchone()[0])
    print("event_memory rows:", con.execute(
        "select count(*) from yy_event_memory").fetchone()[0])
    if con.execute("select count(*) from yy_event_memory").fetchone()[0]:
        print("last memory:", con.execute(
            "select ts,kind,text from yy_event_memory order by rowid desc limit 1").fetchone())
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
