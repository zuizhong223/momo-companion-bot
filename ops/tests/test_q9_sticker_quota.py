# -*- coding: utf-8 -*-
"""Q9：贴纸每日配额。"""
import os
import sys
from pathlib import Path

ROOT = Path(r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (  # noqa: E402
    _STICKER_QUOTA, _sticker_quota_allowed,
)

PASS = True


def check(cond, label, extra=""):
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    old = os.environ.get("DEMO_MAX_STICKERS_PER_DAY")
    try:
        os.environ["DEMO_MAX_STICKERS_PER_DAY"] = "2"
        _STICKER_QUOTA["day"] = None
        _STICKER_QUOTA["n"] = 0
        check(_sticker_quota_allowed() is True, "第 1 张")
        check(_sticker_quota_allowed() is True, "第 2 张")
        check(_sticker_quota_allowed() is False, "第 3 张拒绝")

        os.environ["DEMO_MAX_STICKERS_PER_DAY"] = "0"
        check(_sticker_quota_allowed() is True, "0=不限")
    finally:
        _STICKER_QUOTA["day"] = None
        _STICKER_QUOTA["n"] = 0
        if old is None:
            os.environ.pop("DEMO_MAX_STICKERS_PER_DAY", None)
        else:
            os.environ["DEMO_MAX_STICKERS_PER_DAY"] = old

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
