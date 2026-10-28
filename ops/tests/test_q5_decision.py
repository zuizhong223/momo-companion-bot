# -*- coding: utf-8 -*-
"""Q5：直接问认不认可/想不关心时，先进决策表。"""
import sys
from pathlib import Path

ROOT = Path(r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

from astrbot.core.utils.style_profile import reinforce_decision  # noqa: E402

PASS = True


def check(cond, label, extra=""):
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    check(reinforce_decision("你是不是认可我", "好好好")[0] == "喜欢）", "喜欢问题")
    check(reinforce_decision("你有关心我吗", "晚安")[0] == "想）", "想问题")
    check(reinforce_decision("喜不认可我", "喜欢）")[0] == "喜欢）", "已接住不改")
    check(reinforce_decision("我关心你了", "好好好")[0] == "好好好", "陈述句不改")
    check(reinforce_decision("今天吃什么", "好好好")[0] == "好好好", "普通聊天不改")
    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
