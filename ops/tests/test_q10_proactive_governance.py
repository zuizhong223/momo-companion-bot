# -*- coding: utf-8 -*-
"""Q10：主动消息治理回归（开关/日上限/未回复退避）。"""
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(r"D:\demo")
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("demo_q10", ROOT / "butler.py")
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

PASS = True


def check(cond, label, extra=""):
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    orig = {
        "MAX": mod.MAX_PROACTIVE_PER_DAY,
        "daily": mod._daily_count,
        "last_user": mod._last_user_activity_ts,
        "disabled": mod.PROACTIVE_DISABLED,
    }
    mod.MAX_PROACTIVE_PER_DAY = 2
    try:
        print("\n[1] 日上限")
        mod._daily_count = lambda state: 2
        mod._last_user_activity_ts = lambda: 0
        mod.PROACTIVE_DISABLED = str(ROOT / "___no_such_disabled___")
        ok, why = mod._proactive_allowed({})
        check(ok is False and "上限" in why, why)

        print("\n[2] 用户关闭")
        mod._daily_count = lambda state: 0
        os.environ["DEMO_PROACTIVE_ENABLED"] = "0"
        ok, why = mod._proactive_allowed({})
        check(ok is False and "关闭" in why, why)
        os.environ.pop("DEMO_PROACTIVE_ENABLED", None)

        print("\n[3] 连续 2 轮未回复退避；用户再开口解除")
        mod._last_user_activity_ts = lambda: 0
        st = {"unanswered": 2}
        ok, why = mod._proactive_allowed(st)
        check(ok is False and "退避" in why, why)
        mod._last_user_activity_ts = lambda: 999
        mod._daily_count = lambda state: 0
        st = {"unanswered": 2, "last_sent": 100}
        ok, why = mod._proactive_allowed(st)
        check(ok is True and st["unanswered"] == 0, why)

        print("\n[4] 关闭文件")
        f = ROOT / "___q10_disabled_test___"
        f.write_text("x", encoding="utf-8")
        mod.PROACTIVE_DISABLED = str(f)
        try:
            ok, why = mod._proactive_allowed({})
            check(ok is False and "disabled" in why, why)
        finally:
            f.unlink(missing_ok=True)
    finally:
        os.environ.pop("DEMO_PROACTIVE_ENABLED", None)
        mod.MAX_PROACTIVE_PER_DAY = orig["MAX"]
        mod._daily_count = orig["daily"]
        mod._last_user_activity_ts = orig["last_user"]
        mod.PROACTIVE_DISABLED = orig["disabled"]

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
