# -*- coding: utf-8 -*-
"""L6 回归：地点时间注入、TTL、练习提醒开关/静默。不接机器人。"""
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

os.environ["DEMO_TEST"] = "1"
os.environ["DEMO_LIFE"] = "1"
os.environ["DEMO_LIFE_SHADOW"] = "0"
os.environ["DEMO_LIFE_REMINDER"] = "1"
os.environ["DEMO_LIFE_TZ"] = "Asia/Shanghai"

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
sys.path.insert(0, str(ROOT / "instances/11111111-1111-4111-8111-111111111111/core"))

from astrbot.core.utils import life_state as ls  # noqa: E402

TZ = ls._tz()
U = "u:l6"
PASS = True
DB = Path(tempfile.mkdtemp(prefix="yy_l6_")) / "life.db"


def check(cond, label, extra=""):
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + str(extra)) if extra else ''}")


def main():
    global PASS
    sys.stdout.reconfigure(encoding="utf-8")
    t0 = dt.datetime(2026, 10, 27, 22, 0, tzinfo=TZ)

    print("\n[1] 地点 now 注入/TTL")
    check(ls.observe_place(U, "我在练习室", now=t0, db_path=DB) == "练习室", "记录练习室")
    check(ls.current_place(U, now=t0 + dt.timedelta(minutes=30), db_path=DB) == "练习室",
          "30 分钟内可取")
    check(ls.current_place(U, now=t0 + dt.timedelta(hours=3), db_path=DB) is None,
          "3 小时后过期")
    check(ls.prune_place(U, now=t0 + dt.timedelta(hours=3), db_path=DB) == 1,
          "过期地点清理")
    check(ls.observe_place(U, "用户在练习室", now=t0, db_path=DB) is None, "用户在练习室不记")

    print("\n[2] 练习提醒")
    r = ls.practice_reminder(U, now=dt.datetime(2026, 10, 27, 23, 0, tzinfo=TZ), db_path=DB)
    check(bool(r and r.get("event_id")), "23:00 有 cue", r)
    if r:
        ls.mark_event_told(r["event_id"], db_path=DB)
    check(ls.practice_reminder(U, now=dt.datetime(2026, 10, 27, 23, 5, tzinfo=TZ),
                                db_path=DB) is None, "同日不重复")

    print("\n[3] 关闭/静默")
    old = os.environ.get("DEMO_LIFE_REMINDER")
    os.environ["DEMO_LIFE_REMINDER"] = "0"
    check(ls.practice_reminder(U, now=dt.datetime(2026, 10, 27, 23, 0, tzinfo=TZ),
                                db_path=DB) is None, "开关关")
    os.environ["DEMO_LIFE_REMINDER"] = old or "1"
    os.environ["DEMO_LIFE"] = "0"
    check(ls.practice_reminder(U, now=dt.datetime(2026, 10, 27, 23, 0, tzinfo=TZ),
                                db_path=DB) is None, "生活层关")
    os.environ["DEMO_LIFE"] = "1"
    check(ls.is_quiet(dt.datetime(2026, 10, 27, 4, 0, tzinfo=TZ)) is True, "04:00 quiet")

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
