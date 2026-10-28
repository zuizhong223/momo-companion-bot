# -*- coding: utf-8 -*-
"""8k 核心配置结构测试（不替换线上，只验文件）。"""
import sys
from pathlib import Path

P = Path(r"D:\demo\ops\persona\persona_core_8k.md")
text = P.read_text(encoding="utf-8")
ok = True


def check(cond, msg):
    global ok
    print(("OK   " if cond else "FAIL ") + msg)
    ok = ok and bool(cond)


check(3500 <= len(text) <= 8500, f"核心 {len(text)} 字")
for key in ("唯一决策表", "语气层", "底线", "回忆与事实", "机器人不会这样说话"):
    check(key in text, f"含 {key}")
check("我记得User" not in text, "不塞长篇回忆")
check(text.count("（") < 200, "括号示例不过量")
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
