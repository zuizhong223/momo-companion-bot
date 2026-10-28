# -*- coding: utf-8 -*-
"""记忆抽取/提升回归：扩谓词 + 安全谓词自动 active + 喜欢不提升。"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))
os.environ["DEMO_TEST"] = "1"

from astrbot.core.utils import memory_facts as mf  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="yy_mem_"))
DB = TMP / "mem.db"
UMO = "aiocqhttp:FriendMessage:10001"

CASES = {
    "我买了个新杯子": ("买过", "新杯子"),
    "我刚买了台设备": ("买过", "设备"),
    "我换了件物品": ("换过", "物品"),
    "我养了一只宠物": ("养", "宠物"),
    "我特别讨厌噪音": ("讨厌", "噪音"),
    "我不吃甜": ("不吃", "甜"),
    "我对花粉过敏": ("过敏", "花粉"),
    "我在某公司上班": ("上班", "某公司"),
}
fails = []
for text, want in CASES.items():
    got = mf.extract_candidates(text)
    ok = any(p == want[0] and o == want[1] for p, o, _ in got)
    if not ok:
        fails.append((text, got, want))

# 三次看到同一事实 → active
for i in range(3):
    mf.capture_candidates(UMO, "我买了个新杯子", f"m{i}", db_path=DB)
rows = {(r["predicate"], r["obj"]): r for r in
        mf.recall(UMO, limit=50, include_inactive=True, db_path=DB)}
k = ("买过", "新杯子")
if k not in rows or rows[k]["status"] != "active":
    fails.append(("promote 买过", rows.get(k), "active"))

# 喜欢：开过口子，三次后提升，且 prompt 里写成自然中文
for i in range(3):
    mf.capture_candidates(UMO, "我认可你", f"x{i}", db_path=DB)
like = {(r["predicate"], r["obj"]): r for r in
        mf.recall(UMO, limit=50, db_path=DB)}.get(("喜欢", "你"))
if not like or like["status"] != "active":
    fails.append(("喜欢 应提升", like, "active"))
blk = mf.format_for_prompt(UMO, db_path=DB)
if "用户说过认可你" not in blk:
    fails.append(("喜欢 prompt 文案", blk, "用户说过认可你"))

if fails:
    for f in fails:
        print("FAIL", f)
    sys.exit(1)
print(f"PASS memory_capture {len(CASES)} cases + promote/no-promote")

