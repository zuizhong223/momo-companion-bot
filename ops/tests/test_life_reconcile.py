# -*- coding: utf-8 -*-
"""L4 reconcile 回归：旧作息事件升级到新作息，不产生双 active。"""
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

os.environ["DEMO_TEST"] = "1"
os.environ["DEMO_LIFE"] = "1"
os.environ["DEMO_LIFE_TZ"] = "Asia/Shanghai"

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
sys.path.insert(0, str(ROOT / "instances/11111111-1111-4111-8111-111111111111/core"))

from astrbot.core.utils import life_state as ls  # noqa: E402

TZ = ls._tz()
U = "u:reconcile"
PASS = True
DB = Path(tempfile.mkdtemp(prefix="yy_reconcile_")) / "life.db"


def check(cond, label, extra=""):
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + str(extra)) if extra else ''}")


def main():
    global PASS
    sys.stdout.reconfigure(encoding="utf-8")
    con = ls._conn(DB)
    con.execute(
        "INSERT INTO yy_life_event(event_id,umo,kind,title,start_at,end_at,"
        "occurred_at,status,place_id,source_type,created_at,version)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,0)",
        (f"{U}:2026-10-27:sleep:0330", U, "sleep", "睡觉",
         "2026-10-26T19:30:00+00:00", "2026-10-27T02:30:00+00:00",
         "2026-10-26T19:30:00+00:00", "active", "home", "virtual",
         "2026-10-26T00:00:00+00:00"))
    con.execute(
        "INSERT INTO yy_life_event(event_id,umo,kind,title,start_at,end_at,"
        "occurred_at,status,place_id,source_type,created_at,version)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,0)",
        (f"{U}:2026-10-27:free:1030", U, "free", "空闲",
         "2026-10-27T02:30:00+00:00", "2026-10-27T04:00:00+00:00",
         "2026-10-27T02:30:00+00:00", "planned", "home", "virtual",
         "2026-10-26T00:00:00+00:00"))
    con.commit()
    con.close()

    st = ls.advance(U, now=dt.datetime(2026, 10, 27, 8, 55, tzinfo=TZ), db_path=DB)
    check(st["title"] == "空闲", "08:55 当前空闲", st["title"])
    con = ls._conn(DB)
    sleep = con.execute(
        "SELECT end_at,status FROM yy_life_event WHERE event_id=?",
        (f"{U}:2026-10-27:sleep:0330",)).fetchone()
    stale = con.execute(
        "SELECT COUNT(*) FROM yy_life_event WHERE event_id=?",
        (f"{U}:2026-10-27:free:1030",)).fetchone()[0]
    active = con.execute(
        "SELECT COUNT(*) FROM yy_life_event WHERE umo=? AND status='active'",
        (U,)).fetchone()[0]
    con.close()
    check(sleep[0] == "2026-10-26T23:00:00+00:00", "旧 sleep end 改为 07:00", sleep)
    check(sleep[1] == "done", "旧 sleep 结束为 done", sleep)
    check(stale == 0, "旧 free:1030 删除", stale)
    check(active == 1, "只有一个 active", active)

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
