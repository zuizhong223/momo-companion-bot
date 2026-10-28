# -*- coding: utf-8 -*-
"""P2 L5/L6 策略回归：静默、延迟、软遗忘、地点可信度、提醒开关。"""
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))
os.environ["DEMO_LIFE_TZ"] = "Asia/Shanghai"

from astrbot.core.utils import life_state as ls  # noqa: E402

TZ = ls._tz()
DB = Path(tempfile.mkdtemp(prefix="yy_policy_")) / "life.db"
UMO = "aiocqhttp:FriendMessage:10001"
PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def at(y, m, d, h, mi=0):
    return dt.datetime(y, m, d, h, mi, tzinfo=TZ)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    print("\n[1] 睡眠静默：03:30–10:30 主动不发，紧急例外")
    check(ls.is_quiet(at(2026, 10, 26, 4)) is True, "04:00 quiet")
    check(ls.is_quiet(at(2026, 10, 26, 12)) is False, "12:00 not quiet")
    check(ls.proactive_allowed(at(2026, 10, 26, 4)) is False, "04:00 主动禁止")
    check(ls.proactive_allowed(at(2026, 10, 26, 4), urgent=True) is True, "紧急放行")

    print("\n[2] 延迟：超过 2h 丢弃")
    check(ls.delay_allowed(at(2026, 10, 26, 12), at(2026, 10, 26, 13)) is True, "1h 允许")
    check(ls.delay_allowed(at(2026, 10, 26, 12), at(2026, 10, 26, 15)) is False, "3h 丢弃")

    print("\n[3] 软遗忘：只忘 virtual，保留 id，可恢复")
    ls.advance(UMO, now=at(2026, 10, 1, 12), db_path=DB)
    con = ls._conn(DB)
    eid = con.execute("select event_id from yy_life_event where kind='class'").fetchone()[0]
    con.close()
    n = ls.forget_virtual_events(now=at(2026, 10, 20, 12), ttl_days=7, umo=UMO, db_path=DB)
    check(n >= 1, "至少忘掉一条", str(n))
    ev = ls.event_by_id(eid, db_path=DB)
    check(ev["status"] == "forgotten" and ev["forgotten_at"], "状态 forgotten", str(ev))
    check(ls.restore_event(eid, db_path=DB) is True, "可恢复")
    check(ls.event_by_id(eid, db_path=DB)["status"] == "planned", "恢复为 planned")

    print("\n[4] 地点：无可信来源/低置信度不说")
    check(ls.location_claim("练习室", "user_statement", 0.9) == "练习室", "用户明说可信")
    check(ls.location_claim("练习室", "model_inference", 0.99) is None, "模型推断不说")
    check(ls.location_claim("练习室", "user_statement", 0.4) is None, "低置信度不说")

    print("\n[4b] 地点证据：可信来源/置信度/过期")
    check(ls.set_place(UMO, "练习室", "user_statement", 0.9, ttl_hours=1, db_path=DB) is True,
          "写入可信地点")
    check(ls.current_place(UMO, now=at(2026, 10, 26, 21), db_path=DB) == "练习室",
          "未过期可取")
    check(ls.set_place(UMO, "外面", "model_inference", 0.99, db_path=DB) is False,
          "模型推断拒绝")
    check(ls.current_place(UMO, now=at(2026, 10, 26, 12), db_path=DB) == "练习室",
          "拒绝不覆盖旧值")
    check(ls.set_place(UMO, "学校", "user_statement", 0.4, db_path=DB) is False,
          "低置信度拒绝")
    check(ls.current_place(UMO, now=at(2026, 10, 27, 12), db_path=DB) is None,
          "过期不给地点")
    check(ls.clear_place(UMO, db_path=DB) is True, "可清除")

    print("\n[5] 提醒：默认关；同一天不重复")
    os.environ.pop("DEMO_LIFE_REMINDER", None)
    check(ls.reminder_allowed(now=at(2026, 10, 26, 12)) is False, "默认关")
    os.environ["DEMO_LIFE_REMINDER"] = "1"
    try:
        check(ls.reminder_allowed(now=at(2026, 10, 26, 12)) is True, "开启后允许")
        check(ls.reminder_allowed(now=at(2026, 10, 26, 18),
                                  last_at=at(2026, 10, 26, 12)) is False, "同日不重复")
        check(ls.reminder_allowed(now=at(2026, 10, 27, 12),
                                  last_at=at(2026, 10, 26, 12)) is True, "次日允许")
    finally:
        os.environ.pop("DEMO_LIFE_REMINDER", None)

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
