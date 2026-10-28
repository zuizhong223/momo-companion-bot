# -*- coding: utf-8 -*-
"""P1/Q10：主动消息已有日上限、未回复退避、开关和 9-23 活跃窗口。"""
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(ROOT))
PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def load_butler():
    spec = importlib.util.spec_from_file_location("demo_butler_gate", ROOT / "butler.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    mod.log = lambda *a, **k: None
    return mod


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    mod = load_butler()
    old_disabled = mod.PROACTIVE_DISABLED
    try:
        mod.PROACTIVE_DISABLED = str(ROOT / "_no_such_disabled_file")
        mod._last_user_activity_ts = lambda: 0

        print("\n[1] 活跃窗口 9-23")
        check(mod.ACTIVE_HOURS == (9, 23), "ACTIVE_HOURS=(9,23)", str(mod.ACTIVE_HOURS))

        print("\n[2] 日上限")
        old_max = mod.MAX_PROACTIVE_PER_DAY
        try:
            mod.MAX_PROACTIVE_PER_DAY = 1
            mod._daily_count = lambda state: 1
            allowed, reason = mod._proactive_allowed({})
            check(not allowed and "上限" in reason, "达到上限拒绝", reason)
        finally:
            mod.MAX_PROACTIVE_PER_DAY = old_max

        print("\n[3] 连续 2 轮未回复退避")
        check(mod._proactive_allowed({"unanswered": 2})[0] is False,
              "unanswered=2 拒绝", str(mod._proactive_allowed({"unanswered": 2})))

        print("\n[4] 开关可关闭")
        old_env = os.environ.get("DEMO_PROACTIVE_ENABLED")
        try:
            os.environ["DEMO_PROACTIVE_ENABLED"] = "0"
            check(mod._proactive_allowed({})[0] is False, "env=0 拒绝")
        finally:
            if old_env is None:
                os.environ.pop("DEMO_PROACTIVE_ENABLED", None)
            else:
                os.environ["DEMO_PROACTIVE_ENABLED"] = old_env
    finally:
        mod.PROACTIVE_DISABLED = old_disabled

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
