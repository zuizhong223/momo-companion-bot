# -*- coding: utf-8 -*-
"""长度补一句测试：不碰正式库。"""
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
sys.path.insert(0, str(ROOT / "instances/11111111-1111-4111-8111-111111111111/core"))

try:
    from astrbot.core.utils.length_nudge import apply_length_nudge as f
except Exception:
    sys.path.insert(0, str(Path(__file__).parent))
    from length_nudge import apply_length_nudge as f

ok = True


def check(cond, label, extra=""):
    global ok
    ok = ok and bool(cond)
    print(("OK   " if cond else "FAIL ") + label + (f" | {extra}" if extra else ""))


def find(text, mode="banter"):
    for s in [f"s{i}" for i in range(30)]:
        t, a = f(text, s, mode)
        if t != text:
            return s, t, a
    return None, text, []


s, t, a = find("什么耳机）")
check(bool(s) and len(t) >= 11, "分享问句补长", t)
t2, _ = f("什么耳机）", s or "s", "banter")
check(t2 == t, "确定性", t2)

s2, t3, _ = find("好看 多少钱买的（")
check(t3 == "好看 多少钱买的（", "已问过不重复补", t3)

t4, _ = f("什么游戏啊", "s", "banter")
check(t4 == "什么游戏啊", "非物品不改", t4)

t5, _ = f("好看（", "s", "banter")
check(t5 == "好看（", "纯夸不改", t5)

t6, _ = f("没事（", "s", "serious")
check(t6 != "没事（" and len(t6) >= 10, "短道歉必补", t6)

t7, _ = f("没事（", "s", "banter")
check(t7 == "没事（", "非 serious 不改", t7)

t8, _ = f("啊？", "s", "serious")
check(t8 == "啊？", "不误伤「啊？」", t8)

print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
