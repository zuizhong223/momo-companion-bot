# -*- coding: utf-8 -*-
"""生活层遗忘/恢复/已告诉 的临时库测试（不碰生产库）。"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CORE = Path(r"D:/demo\instances\11111111-1111-4111-8111-111111111111\core")
sys.path.insert(0, str(CORE))
sys.stdout.reconfigure(encoding="utf-8")

from astrbot.core.utils import life_state as ls  # noqa: E402

TMP = Path(r"D:/demo\Documents\deepseek-harness\default-workspace\life_test.db")
if TMP.exists():
    TMP.unlink()

now = datetime.now(timezone.utc)
old = now - timedelta(days=10)
eid = "test:2026-10-17:meal:1200"
con = ls._conn(TMP)
con.execute(
    "INSERT INTO yy_life_event(event_id,umo,kind,title,start_at,end_at,status,"
    "source_type,occurred_at,created_at,version) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
    (eid, "test", "meal", "午饭",
     old.isoformat(timespec="seconds"), (old + timedelta(minutes=40)).isoformat(timespec="seconds"),
     "planned", "virtual", old.isoformat(timespec="seconds"),
     now.isoformat(timespec="seconds"), 0))
con.commit()
con.close()

n = ls.forget_virtual_events(now, ttl_days=0, db_path=TMP)
con = ls._conn(TMP)
row = con.execute("select status,forgotten_at from yy_life_event where event_id=?", (eid,)).fetchone()
con.close()
forgot_status = row[0]
print(f"forget n={n} status={row[0]} forgotten_at={row[1]}")

ok_restore = ls.restore_event(eid, db_path=TMP)
con = ls._conn(TMP)
row = con.execute("select status,forgotten_at from yy_life_event where event_id=?", (eid,)).fetchone()
con.close()
restored_status = row[0]
print(f"restore={ok_restore} status={row[0]} forgotten_at={row[1]}")

ok_told = ls.mark_event_told(eid, task_id="t1", db_path=TMP)
con = ls._conn(TMP)
row = con.execute("select told_at,task_id from yy_life_event where event_id=?", (eid,)).fetchone()
con.close()
told_at = row[0]
print(f"told={ok_told} told_at={row[0]} task_id={row[1]}")

# advance 路径：7 天前的虚拟事件应被软遗忘
eid2 = "test:2026-10-17:free:1500"
old2 = now - timedelta(days=9)
con = ls._conn(TMP)
con.execute(
    "INSERT INTO yy_life_event(event_id,umo,kind,title,start_at,end_at,status,"
    "source_type,occurred_at,created_at,version) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
    (eid2, "test", "free", "空闲",
     old2.isoformat(timespec="seconds"),
     (old2 + timedelta(hours=1)).isoformat(timespec="seconds"),
     "planned", "virtual", old2.isoformat(timespec="seconds"),
     now.isoformat(timespec="seconds"), 0))
con.commit()
con.close()
ls.advance("test", now=now, db_path=TMP)
con = ls._conn(TMP)
row = con.execute("select status from yy_life_event where event_id=?", (eid2,)).fetchone()
con.close()
adv_status = row[0] if row else "missing"
print(f"advance-forget status={adv_status}")

ok = (n == 1 and forgot_status == "forgotten" and ok_restore
      and restored_status == "planned" and ok_told and bool(told_at)
      and adv_status == "forgotten")
TMP.unlink(missing_ok=True)
print("RESULT:", "PASS" if ok else "FAIL")
