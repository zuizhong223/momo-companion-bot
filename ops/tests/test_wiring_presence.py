#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""N-5 接线存在性：防止"测试全绿但功能被覆盖"。

背景：2026-10-26 另一个 agent 覆盖了 aiocqhttp_message_event.py，
R02 的 start_turn / prepare_turn_commit 静默消失，43 个测试无一报警。
本测试直接检查"调用点是否还在"。

覆盖：
  [1] 事件层：R02 整轮清单三个调用点
  [2] 事件层：R04 delivery_text 落点、R03 载荷冲突捕获
  [3] internal：bind_delivered_text 在类外（模块级）且被 _save_to_history 调用
  [4] 类结构：_save_to_history 仍是方法（防"类被提前结束"）
  [5] 生活层：承诺状态机函数与提示词过滤
  [6] 自检：把源码裁掉一段，检查器必须报错

用法：python ops\\tests\\test_wiring_presence.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core/astrbot"
EVENT = CORE / "core/platform/sources/aiocqhttp/aiocqhttp_message_event.py"
INTERNAL = CORE / "core/pipeline/process_stage/method/agent_sub_stages/internal.py"
LIFE = CORE / "core/utils/life_state.py"

PASS = True


def check(cond, name, extra=""):
    global PASS
    print(("  OK   " if cond else "  FAIL ") + name + (f" | {extra}" if extra and not cond else ""))
    if not cond:
        PASS = False


def region(src: str, start_pat: str, end_pat: str) -> str:
    i = src.find(start_pat)
    if i < 0:
        return ""
    j = src.find(end_pat, i + len(start_pat))
    return src[i:j if j > 0 else len(src)]


def event_body(src: str) -> str:
    return region(src, "async def send_message(", "\n    @classmethod")


def check_event(src: str) -> list[str]:
    """返回缺失项；空列表表示接线完整。"""
    body = event_body(src)
    missing = []
    for need in ("start_turn", "prepare_turn_commit", "mark_turn_committed"):
        if not re.search(rf"\.{need}\b", body):
            missing.append(need)
    if "delivery_text" not in src:
        missing.append("delivery_text")
    if "PayloadMismatch" not in src:
        missing.append("PayloadMismatch")
    if "before_segment_send" not in src:
        missing.append("before_segment_send")
    # 顺序：start_turn 必须出现在 prepare_turn_commit 之前
    if "start_turn(" in body and "prepare_turn_commit(" in body:
        # 单段分支可能先 prepare 再 start_turn；多段分支的最终 prepare 必须在 start_turn 之后。
        if body.find("start_turn(") > body.rfind("prepare_turn_commit("):
            missing.append("start_turn 顺序")
    return missing


def main() -> int:
    ev = EVENT.read_text(encoding="utf-8")
    inner = INTERNAL.read_text(encoding="utf-8")
    life = LIFE.read_text(encoding="utf-8")

    print("\n[1][2] 事件层接线")
    miss = check_event(ev)
    check(not miss, "R02/R04/R03 调用点齐全", ",".join(miss))

    print("\n[3] internal：helper 模块级且被调用")
    check("def bind_delivered_text(" in inner, "helper 存在")
    idx_def = inner.find("def bind_delivered_text(")
    idx_cls = inner.find("class InternalAgentSubStage")
    check(0 <= idx_def < idx_cls, "helper 在类定义之前", f"def={idx_def} cls={idx_cls}")
    body = region(inner, "async def _save_to_history(", "\n    async def ")
    check("bind_delivered_text(messages_to_save" in body, "已在 _save_to_history 内调用")

    print("\n[4] 类结构：_save_to_history 仍是方法")
    for ln in inner.splitlines():
        if "_save_to_history(" in ln and ln.strip().startswith("async def"):
            check(ln.startswith("    "), "有 4 空格缩进（是方法）", repr(ln))
            break
    else:
        check(False, "找到 _save_to_history 定义")

    print("\n[4b] A2 认真探针接线")
    agent = (CORE / "core/astr_main_agent.py")
    probe = (CORE / "core/utils/serious_probe.py")
    check(probe.exists(), "serious_probe.py 存在")
    if agent.exists():
        a = agent.read_text(encoding="utf-8")
        check("serious_probe" in a, "主链路引用了探针")
        check("shadow(" in a, "影子路径在（默认不生效）")

    print("\n[5] 生活层：承诺状态机")
    for fn in ("def activate_from_send(", "def expire_overdue(", "def commitments_context("):
        check(fn in life, f"{fn} 存在")
    ctx = region(life, "def commitments_context(", "\ndef ")
    check("expire_overdue(" in ctx, "读取前先过期")
    check('status"] == "active"' in ctx or "status'] == 'active'" in ctx.replace('"', "'"),
          "只取 active", ctx[:120])

    print("\n[6] 自检：裁掉 start_turn 后必须报错")
    broken = ev.replace("ledger.start_turn,", "ledger._removed,")
    if broken == ev:
        broken = ev.replace("ledger.start_turn(", "ledger._removed(")
    check(bool(check_event(broken)), "检查器能发现缺失", str(check_event(broken)))

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
