# -*- coding: utf-8 -*-
"""Q1：日常贫嘴短句提醒只对 banter 概率触发。"""
import sys
from pathlib import Path

ROOT = Path(r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

import astrbot.core.astr_main_agent as m  # noqa: E402

PASS = True


def check(cond, label, extra=""):
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    old = m.random.random
    try:
        m.random.random = lambda: 0.0
        h = m._banter_brevity_hint("今天吃什么")
        check("1~6" in h, "banter 命中短句提醒", h)
        check(m._banter_brevity_hint("为什么你会这样") == "", "serious 不触发")
        m.random.random = lambda: 0.99
        check(m._banter_brevity_hint("今天吃什么") == "", "概率未命中")
    finally:
        m.random.random = old
    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
