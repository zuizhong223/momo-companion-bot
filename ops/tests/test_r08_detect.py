#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R08：冲突与和好的否定误判（离线回归）。

覆盖：
  [1] “我不讨厌你”不生成冲突
  [2] “讨厌你”仍然生成冲突
  [3] “你真的很烦”仍然生成冲突
  [4] “我不想和好”不结清冲突
  [5] “我们和好吧”/“对不起”仍能结清
  [6] “你朋友真讨厌”不算（第三方）

用法：python ops\\tests\\test_r08_detect.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

PASS = True


def check(cond, name, extra=""):
    global PASS
    print(("  OK   " if cond else "  FAIL ") + name + (f" | {extra}" if extra and not cond else ""))
    if not cond:
        PASS = False


def main() -> int:
    from astrbot.core.utils.life_state import detect_conflict, detect_resolve  # noqa: PLC0415

    print("\n[1] 我不讨厌你：不生成冲突")
    got = detect_conflict("我不讨厌你")
    check(got == "", "conflict 为空", got)

    print("\n[2] 讨厌你：仍生成冲突")
    got = detect_conflict("讨厌你")
    check(got != "", "命中冲突词", got)

    print("\n[3] 你真的很烦：仍生成冲突")
    got = detect_conflict("你真的很烦")
    check(got != "", "命中冲突词", got)

    print("\n[4] 我不想和好：不结清")
    got = detect_resolve("我不想和好")
    check(got == "", "resolve 为空", got)

    print("\n[5] 我们和好吧 / 对不起：仍能结清")
    check(detect_resolve("我们和好吧") != "", "和好命中", detect_resolve("我们和好吧"))
    check(detect_resolve("对不起") != "", "对不起命中", detect_resolve("对不起"))

    print("\n[6] 你朋友真讨厌：第三方，不算")
    got = detect_conflict("你朋友真讨厌")
    check(got == "", "conflict 为空", got)

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
