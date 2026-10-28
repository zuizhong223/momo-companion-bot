# -*- coding: utf-8 -*-
"""watchdog 决策测试：不真重启。"""
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
sys.path.insert(0, str(ROOT / "ops/deploy"))
import watchdog as w  # noqa: E402

ok = True


def check(cond, label, extra=""):
    global ok
    ok = ok and bool(cond)
    print(("OK   " if cond else "FAIL ") + label + (f" | {extra}" if extra else ""))


check(w.decide(True, 5, 0, 100)[0] == "ok", "端口在 -> ok")
check(w.decide(False, 0, 0, 100) == ("wait", 1), "第一次 down -> wait")
check(w.decide(False, 1, 0, 1000)[0] == "restart", "第二次 down -> restart")
check(w.decide(False, 5, 100, 200)[0] == "cooldown", "冷却期内 -> cooldown")
check(w.decide(False, 5, 0, w.COOLDOWN + 1)[0] == "restart", "冷却结束 -> restart")
check(w.PORTS == (22877, 6199), "端口", str(w.PORTS))

print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
