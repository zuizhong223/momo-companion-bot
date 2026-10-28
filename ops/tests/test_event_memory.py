# -*- coding: utf-8 -*-
"""事件记忆单元测试（临时库，不碰正式库）。"""
import os
import sys
import tempfile
from pathlib import Path

tmp = Path(tempfile.mkdtemp()) / "ev.db"
os.environ["DEMO_DB"] = str(tmp)
os.environ["DEMO_EVENTS"] = "1"
os.environ["DEMO_EVENTS_SHADOW"] = "1"
CORE = Path(r"D:\demo\instances\11111111-1111-4111-8111-111111111111\core")
sys.path.insert(0, str(CORE))

from astrbot.core.utils import event_memory as ev  # noqa: E402

ok = True


def check(cond, msg):
    global ok
    print(("OK   " if cond else "FAIL ") + msg)
    ok = ok and bool(cond)


check(ev.enabled(), "enable 开关")
check(ev.shadow(), "shadow 开关")
check(not ev.capture_cue("你好"), "无事件词不记")
check(ev.capture_cue("今天考试考完了"), "事件词可记")
check(ev.remember("u1", "今天考试考完了", "m1"), "第一次写入")
check(not ev.remember("u1", "今天考试考完了", "m1"), "同 msg 去重")
check(ev.remember("u1", "昨天和用户吵架了", "m2"), "第二次写入")
rows = ev.recent("u1", 3)
check(len(rows) == 2, f"最近 2 条，实际 {len(rows)}")
block = ev.format_for_prompt("u1")
check("考试" in block and "吵架" in block, "格式化包含两条")
check(ev.recent("nobody", 3) == [], "无记录返回空")
print(block)
print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
