#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R04 后半：历史绑定"实际送达/念出的那句"（离线回归）。

直接从 internal.py 抽取 bind_delivered_text 源码执行，避免拉起整条 pipeline 依赖。

覆盖：
  [1] str 内容 → 替换成送达文本
  [2] 多个 text 段 → 合并成一条
  [3] 带 tool_calls 的 assistant → 不动
  [4] 没有 assistant → 返回 0
  [5] 已经一样 → 返回 0
  [6] text + 图片混排 → 只换 text，保留图片

用法：python ops\\tests\\test_history_binding.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
INTERNAL = (ROOT / "instances/11111111-1111-4111-8111-111111111111/core/astrbot"
            / "core/pipeline/process_stage/method/agent_sub_stages/internal.py")
PASS = True


def check(cond, name, extra=""):
    global PASS
    print(("  OK   " if cond else "  FAIL ") + name + (f" | {extra}" if extra and not cond else ""))
    if not cond:
        PASS = False


def load_helper():
    src = INTERNAL.read_text(encoding="utf-8")
    m = re.search(r"^def bind_delivered_text\(.*?(?=^(?:def |async def |class ))", src,
                  re.S | re.M)
    if not m:
        raise SystemExit("找不到 bind_delivered_text")
    ns: dict = {}
    exec(compile(m.group(0), str(INTERNAL), "exec"), ns)  # noqa: S102
    return ns["bind_delivered_text"]


class M:
    def __init__(self, role, content, tool_calls=None):
        self.role = role
        self.content = content
        self.tool_calls = tool_calls


def main() -> int:
    fn = load_helper()

    print("\n[1] str 内容 → 替换")
    msgs = [M("user", "在吗"), M("assistant", "模型原稿")]
    n = fn(msgs, "念出来的那句")
    check(n == 1 and msgs[-1].content == "念出来的那句", "已替换", str(msgs[-1].content))

    print("\n[2] 多个 text 段 → 合并")
    msgs = [M("user", "在吗"),
            M("assistant", [{"type": "text", "text": "第一段"},
                            {"type": "text", "text": "第二段"}])]
    n = fn(msgs, "合成一句")
    c = msgs[-1].content
    check(n == 1 and len(c) == 1 and c[0]["text"] == "合成一句", "合并为一条", str(c))

    print("\n[3] 带 tool_calls → 不动")
    msgs = [M("assistant", "原稿", tool_calls=[{"id": "1"}])]
    n = fn(msgs, "不该改")
    check(n == 0 and msgs[-1].content == "原稿", "未被修改", str(msgs[-1].content))

    print("\n[4] 没有 assistant → 0")
    msgs = [M("user", "在吗"), M("system", "x")]
    check(fn(msgs, "x") == 0, "返回 0")

    print("\n[5] 已经一样 → 0")
    msgs = [M("assistant", "同一句")]
    check(fn(msgs, "同一句") == 0, "不重复替换")

    print("\n[6] text + 图片 → 只换 text")
    msgs = [M("assistant", [{"type": "text", "text": "原稿"},
                            {"type": "image", "file": "a.png"}])]
    n = fn(msgs, "新文本")
    c = msgs[-1].content
    check(n == 1 and c[0]["text"] == "新文本" and c[1]["type"] == "image",
          "文本换、图片留", str(c))

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
