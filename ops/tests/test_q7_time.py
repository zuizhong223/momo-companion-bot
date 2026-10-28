# -*- coding: utf-8 -*-
"""Q7：固定时钟的日期/星期/时区回归。"""
import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

from astrbot.core.astr_main_agent import _datetime_line  # noqa: E402

TZ = ZoneInfo("Asia/Shanghai")
PASS = True


def check(cond, label, extra=""):
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    print("\n[1] 23:59 不跳日")
    line = _datetime_line(dt.datetime(2026, 10, 26, 23, 59, tzinfo=TZ))
    check("2026-10-26 23:59" in line and "Monday" in line, line)

    print("\n[2] 00:01 跳日、星期正确")
    line = _datetime_line(dt.datetime(2026, 10, 27, 0, 1, tzinfo=TZ))
    check("2026-10-27 00:01" in line and "Tuesday" in line, line)

    print("\n[3] UTC 折算到 Asia/Shanghai")
    utc = dt.datetime(2026, 10, 26, 15, 59, tzinfo=dt.timezone.utc)
    line = _datetime_line(utc.astimezone(TZ))
    check("2026-10-26 23:59" in line and "Monday" in line, line)

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
