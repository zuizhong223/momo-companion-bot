# -*- coding: utf-8 -*-
"""L4 变体回归：考试日 / 出门日，不接机器人、不写正式库。"""
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
U = "u:l4"
PASS = True
TMP = Path(tempfile.mkdtemp(prefix="yy_l4_variants_"))


def check(cond, label, extra=""):
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + str(extra)) if extra else ''}")


def at(day, h, m=0):
    return dt.datetime(2026, 10, day, h, m, tzinfo=TZ)


def main():
    global PASS
    sys.stdout.reconfigure(encoding="utf-8")
    db = TMP / "exam.db"
    print("\n[1] 考试日")
    check(ls.set_day_override(U, dt.date(2026, 10, 28), "exam", db_path=db), "写入 exam")
    st = ls.advance(U, now=at(28, 10), db_path=db)
    check(st["title"] == "考试" and st["place"] == "学校", "10:00 考试/学校", st)
    st = ls.advance(U, now=at(28, 14), db_path=db)
    check(st["title"] == "复习" and st["place"] == "学校", "14:00 复习/学校", st)
    con = ls._conn(db)
    titles = [r[0] for r in con.execute(
        "select title from yy_life_event where umo=? and start_at like '2026-10-28%'", (U,))]
    con.close()
    check("考试" in titles and "复习" in titles, "有考试/复习", titles)
    check("上课" not in titles, "考试日不上课", titles)

    print("\n[2] 出门日")
    db2 = TMP / "outing.db"
    check(ls.set_day_override(U, dt.date(2026, 10, 29), "outing", db_path=db2), "写入 outing")
    st = ls.advance(U, now=at(29, 12), db_path=db2)
    check(st["title"] == "午饭" and st["place"] == "外面", "12:00 在外面吃", st)
    st = ls.advance(U, now=at(29, 14), db_path=db2)
    check(st["title"] == "出门" and st["place"] == "外面", "14:00 在外面", st)
    st = ls.advance(U, now=at(29, 19), db_path=db2)
    check(st["title"] == "晚饭" and st["place"] == "home", "19:00 回家晚饭", st)
    con = ls._conn(db2)
    titles = [r[0] for r in con.execute(
        "select title from yy_life_event where umo=? and start_at like '2026-10-29%'", (U,))]
    con.close()
    check("出门" in titles and "上课" not in titles, "出门日不上课", titles)

    print("\n[3] 自定义时间")
    db3 = TMP / "custom.db"
    check(ls.set_day_override(U, dt.date(2026, 10, 30), "exam",
                              payload={"exam_start": "08:30", "exam_end": "10:30"},
                              db_path=db3), "写入自定义考试")
    st = ls.advance(U, now=at(30, 9), db_path=db3)
    check(st["title"] == "考试", "09:00 考试", st["title"])
    st = ls.advance(U, now=at(30, 10, 45), db_path=db3)
    check(st["title"] == "空闲", "10:45 空闲", st["title"])
    db4 = TMP / "custom_out.db"
    check(ls.set_day_override(U, dt.date(2026, 10, 31), "outing",
                              payload={"start": "11:00", "end": "17:00"},
                              db_path=db4), "写入自定义出门")
    st = ls.advance(U, now=at(31, 11, 30), db_path=db4)
    check(st["title"] == "出门" and st["place"] == "外面", "11:30 出门", st)
    st = ls.advance(U, now=at(31, 17, 30), db_path=db4)
    check(st["title"] == "空闲", "17:30 回家", st["title"])

    print("\n[4] 非法时间拒收")
    try:
        ls.set_day_override(U, dt.date(2026, 10, 30), "exam",
                            payload={"exam_start": "13:00", "exam_end": "14:00"},
                            db_path=db2)
        ok = False
    except ValueError:
        ok = True
    check(ok, "考试 13:00 拒收")

    print("\n[4] normal 删除变体")
    check(ls.set_day_override(U, dt.date(2026, 10, 29), "normal", db_path=db2), "删除 outing")
    check(ls.list_day_overrides(U, db_path=db2) == [], "override 为空")

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
