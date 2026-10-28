# -*- coding: utf-8 -*-
"""生活层注入门禁回归：只在用户问“机器人在干嘛/在哪/练习/上课”时才注入状态。"""
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

os.environ["DEMO_TEST"] = "1"

from astrbot.core.utils import life_state as ls  # noqa: E402

CASES = {
    "你在干嘛": True,
    "在干嘛呢": True,
    "你在哪儿": True,
    "现在在哪": True,
    "今天练习了吗": True,
    "练了没": True,
    "上课了吗": True,
    "在打游戏吗": True,
    "我认可你": False,
    "我关心你了": False,
    "这个代码怎么改": False,
    "我买了个新杯子": False,
    "今天吃什么": False,
}

fails = []
for text, want in CASES.items():
    got = ls.should_inject(text)
    if got != want:
        fails.append((text, want, got))

if fails:
    print("FAIL", fails)
    sys.exit(1)
print(f"PASS life_inject {len(CASES)}/{len(CASES)}")
