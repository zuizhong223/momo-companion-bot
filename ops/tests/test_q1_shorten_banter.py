# -*- coding: utf-8 -*-
"""Q1：banter 长句安全收短。"""
import sys
from pathlib import Path

ROOT = Path(r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

from astrbot.core.utils.style_profile import shorten_banter  # noqa: E402

PASS = True


class R:
    def __init__(self, v):
        self.v = v

    def random(self):
        return self.v


def check(cond, label, extra=""):
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    out, why = shorten_banter("今天真的累了 想早点睡", "banter", R(0.0))
    check(out == "今天真的累了" and why, "取安全第一小句", out)
    check(shorten_banter("今天真的累了 想早点睡", "banter", R(0.99))[0]
          == "今天真的累了 想早点睡", "概率不命中不改")
    check(shorten_banter("今天真的累了 想早点睡", "serious", R(0.0))[0]
          == "今天真的累了 想早点睡", "serious 不改")
    check(shorten_banter("我 没想过 也不打算恋爱（", "banter", R(0.0))[0]
          == "我 没想过 也不打算恋爱（", "单字开头不改")
    check(shorten_banter("今天真的累了", "banter", R(0.0))[0] == "今天真的累了",
          "无空格不改")
    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
