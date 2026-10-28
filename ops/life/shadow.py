# -*- coding: utf-8 -*-
"""L3：生活事件 shadow。复制一份 DB，在临时库上推演，不写生产。

用法：
  python ops\\life\\shadow.py --hours 24
输出 ops\\life\\shadow.jsonl（独立 JSONL，不写生产记忆/历史）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:/demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
PROD = CORE / "data/data_v4.db"
OUT = ROOT / "ops" / "life" / "shadow.jsonl"
sys.path.insert(0, str(CORE))

from astrbot.core.utils import life_state as ls  # noqa: E402

UMO = "aiocqhttp:FriendMessage:100000000"


def clone_db() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="yy_shadow_")) / "shadow.db"
    src = sqlite3.connect(f"file:{PROD}?mode=ro", uri=True)
    dst = sqlite3.connect(str(tmp))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return tmp


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--step-min", type=int, default=60)
    args = ap.parse_args()
    db = clone_db()
    tz = ls._tz()
    now = dt.datetime.now(dt.timezone.utc).astimezone(tz).replace(minute=0, second=0, microsecond=0)
    rows = []
    for i in range(0, args.hours * 60 + 1, max(5, args.step_min)):
        t = now + dt.timedelta(minutes=i)
        st = ls.advance(UMO, now=t, db_path=db)
        con = ls._conn(db)
        try:
            ev = con.execute(
                "SELECT event_id,kind,title,start_at,end_at,occurred_at,told_at,"
                "status,place_id,source_type,source_ref,message_id,task_id"
                " FROM yy_life_event WHERE event_id=?", (st["event_id"],)).fetchone() if st["event_id"] else None
        finally:
            con.close()
        rows.append({
            "at": t.isoformat(timespec="seconds"),
            "purpose": "shadow",
            "state_version": st["version"],
            "local": st["local"].isoformat(timespec="seconds"),
            "energy": st["energy"],
            "event_id": st["event_id"],
            "title": st["title"],
            "place": st["place"],
            "event": ev,
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")
    changed = sum(1 for a, b in zip(rows, rows[1:]) if a["event_id"] != b["event_id"])
    print(f"shadow rows={len(rows)} transitions={changed} out={OUT}")
    print(f"prod_db={PROD} temp_db={db} written_prod=false")
    return 0


if __name__ == "__main__":
    sys.exit(main())

