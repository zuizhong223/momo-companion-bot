# -*- coding: utf-8 -*-
"""生活层事件审计：时区、覆盖、空档、told/forgotten。

用法：python ops\\life\\audit_events.py
只读，不改库。
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(r"D:/demo")
DB = ROOT / "instances/11111111-1111-4111-8111-111111111111/core/data/data_v4.db"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    row = con.execute("select tz from yy_life_state limit 1").fetchone()
    tzname = row[0] if row and row[0] else "Asia/Shanghai"
    tz = ZoneInfo(tzname)
    rows = con.execute(
        "select event_id,kind,title,start_at,end_at,status,source_type,"
        "occurred_at,told_at,forgotten_at from yy_life_event order by start_at"
    ).fetchall()
    con.close()

    print(f"tz={tzname} events={len(rows)}")
    st: dict[str, int] = {}
    for r in rows:
        st[r[5]] = st.get(r[5], 0) + 1
    print("status:", st)
    print("told:", sum(1 for r in rows if r[8]),
          "forgotten:", sum(1 for r in rows if r[9]))
    print("sources:", {s: sum(1 for r in rows if r[6] == s) for s in {r[6] for r in rows}})

    spans = []
    for r in rows:
        if not r[3]:
            continue
        start = datetime.fromisoformat(r[3]).astimezone(tz)
        end = datetime.fromisoformat(r[4]).astimezone(tz) if r[4] else start + timedelta(hours=1)
        spans.append((start, end, r[1], r[2]))
    spans.sort()
    print("\n--- 本地时间线 ---")
    for start, end, kind, title in spans:
        print(f"{start:%m-%d %H:%M}-{end:%H:%M} {kind:<8} {title}")

    print("\n--- 空档 >30min ---")
    for (s1, e1, k1, _), (s2, e2, k2, _) in zip(spans, spans[1:]):
        gap = (s2 - e1).total_seconds() / 60
        if gap > 30:
            print(f"{e1:%m-%d %H:%M} -> {s2:%m-%d %H:%M}  gap={gap:.0f}min  "
                  f"({k1} -> {k2})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
