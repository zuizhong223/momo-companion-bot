# -*- coding: utf-8 -*-
"""Q3：banter 的「好好好」小概率改「哈哈哈哈」，不硬塞。"""
import sys
from pathlib import Path

ROOT = Path(r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

from astrbot.core.utils.style_profile import vary_haohao  # noqa: E402

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
    out, why = vary_haohao("好好好", "banter", R(0.0))
    check(out == "哈哈哈哈" and why, "命中换成哈哈哈哈", out)
    out2, _ = vary_haohao("好好好）", "banter", R(0.0))
    check(out2 == "哈哈哈哈）", "保留半括号", out2)
    check(vary_haohao("好好好", "banter", R(0.99))[0] == "好好好", "未命中不改")
    check(vary_haohao("好好好", "support", R(0.0))[0] == "好好好", "support 不改")
    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
