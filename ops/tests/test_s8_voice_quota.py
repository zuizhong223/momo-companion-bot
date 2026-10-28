# -*- coding: utf-8 -*-
"""S8：每日语音次数上限。"""
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(r"D:\demo")
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("demo_s8", ROOT / "butler.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
PASS = True


def check(cond, label, extra=""):
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    old = os.environ.get("DEMO_DAILY_TTS_LIMIT")
    try:
        os.environ["DEMO_DAILY_TTS_LIMIT"] = "2"
        st = {}
        check(mod._voice_quota_ok(st) is True, "第 1 次")
        check(mod._voice_quota_ok(st) is True, "第 2 次")
        check(mod._voice_quota_ok(st) is False, "第 3 次拒绝")
        os.environ["DEMO_DAILY_TTS_LIMIT"] = "0"
        check(mod._voice_quota_ok({}) is True, "0=不限")
    finally:
        if old is None:
            os.environ.pop("DEMO_DAILY_TTS_LIMIT", None)
        else:
            os.environ["DEMO_DAILY_TTS_LIMIT"] = old
    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
