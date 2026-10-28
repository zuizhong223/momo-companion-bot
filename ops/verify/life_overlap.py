# -*- coding: utf-8 -*-
"""R07/P3-2：检查一天日程没有重叠、工作日/周末 13:00–17:00 没有空档。"""
from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:/demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

from astrbot.core.utils import life_state as ls  # noqa: E402

UMO = "aiocqhttp:FriendMessage:100000000"
TZ = ls._tz()
PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def day(date: dt.date) -> list[tuple[dt.datetime, dt.datetime, str]]:
    return [(dt.datetime.fromisoformat(e["start_at"]).astimezone(TZ),
             dt.datetime.fromisoformat(e["end_at"]).astimezone(TZ), e["title"])
            for e in ls._day_events(UMO, date, TZ)]


def overlaps(items) -> list[str]:
    bad = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a0, a1, an = items[i]
            b0, b1, bn = items[j]
            if a0 < b1 and b0 < a1:
                bad.append(f"{an}[{a0:%H:%M}-{a1:%H:%M}] × {bn}[{b0:%H:%M}-{b1:%H:%M}]")
    return bad


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    mon = day(dt.date(2026, 10, 26))
    sat = day(dt.date(2026, 10, 31))

    check(not overlaps(mon), "工作日无重叠", "; ".join(overlaps(mon)))
    check(not overlaps(sat), "周末无重叠", "; ".join(overlaps(sat)))

    for label, items, base in (("工作日", mon, dt.date(2026, 10, 26)),
                               ("周末", sat, dt.date(2026, 10, 31))):
        a = dt.datetime.combine(base, dt.time(13, 0), tzinfo=TZ)
        b = dt.datetime.combine(base, dt.time(17, 0), tzinfo=TZ)
        covered = any(x0 <= a and x1 >= b for x0, x1, _ in items)
        check(covered, f"{label} 13:00–17:00 被完整覆盖")

    check(any(13 <= s.hour < 17 and "上课" in n for s, e, n in mon), "工作日 13–17 有课")
    # 周末 13-17 可能是空闲，也可能出门（模板有 30% 概率）
    check(any(13 <= s.hour < 17 and ("空闲" in n or "出门" in n) for s, e, n in sat),
          "周末 13–17 是空闲或出门")

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())

