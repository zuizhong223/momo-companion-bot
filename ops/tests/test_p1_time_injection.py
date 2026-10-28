# -*- coding: utf-8 -*-
"""P1/Q7：每条消息都要注入当前时间；固定时钟覆盖 18:00 / 23:59。"""
import datetime as _dt
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

TMP = Path(tempfile.mkdtemp(prefix="yy_time_"))
os.environ["DEMO_TEST"] = "1"
os.environ["DEMO_DB"] = str(TMP / "t.db")
os.environ["DEMO_LEDGER_DB"] = str(TMP / "t.db")
os.environ["DEMO_LIFE"] = "0"

import astrbot.core.astr_main_agent as agent  # noqa: E402

PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


class FakeDateTime(_dt.datetime):
    fixed: _dt.datetime = _dt.datetime(2026, 10, 26, 18, 0)

    @classmethod
    def now(cls, tz=None):
        base = cls.fixed
        if tz is not None:
            return base.replace(tzinfo=tz)
        return base.astimezone()


class E:
    unified_msg_origin = "aiocqhttp:FriendMessage:10001"
    message_obj = SimpleNamespace(message_id="time-1")
    message_str = "今天好累"

    def get_extra(self, *_a, **_k):
        return None


class R:
    def __init__(self):
        self.extra_user_content_parts = []
        self.contexts = []


def prompt_at(when: _dt.datetime) -> str:
    old = agent.datetime.datetime
    try:
        FakeDateTime.fixed = when
        agent.datetime.datetime = FakeDateTime
        req = R()
        agent._append_system_reminders(E(), req, {"datetime_system_prompt": True},
                                       "Asia/Shanghai")
        return "\n".join(getattr(p, "text", "") for p in req.extra_user_content_parts)
    finally:
        agent.datetime.datetime = old


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p18 = prompt_at(_dt.datetime(2026, 10, 26, 18, 0))
    p2359 = prompt_at(_dt.datetime(2026, 10, 26, 23, 59))
    p0001 = prompt_at(_dt.datetime(2026, 10, 27, 0, 1))

    print("\n[1] 18:00 注入")
    check("Current datetime:" in p18, "有 Current datetime", p18)
    check("18:00" in p18, "时间=18:00", p18)
    check("2026-10-26" in p18, "日期正确", p18)

    print("\n[2] 23:59 / 00:01 跨午夜")
    check("23:59" in p2359, "23:59", p2359)
    check("00:01" in p0001 and "2026-10-27" in p0001, "00:01 次日", p0001)
    check(p18 != p2359, "两次请求时间不同", "same")

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
