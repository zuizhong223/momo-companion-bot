#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""N-1a 隔离自检：测试模式绝不能落到正式库。

背景：2026-10-26 一次测试把"我买了把新的乐器"写进了正式 yy_fact2，
      随后被主动消息读了出来。根因是没有"测试必须用临时库"的硬约束。

覆盖：
  [1] memory_facts：无 db_path + DEMO_TEST=1 → 拒绝
  [2] mood_state：同上
  [3] life_state：同上
  [4] 显式传临时库 → 正常返回
  [5] 非测试模式 → 允许默认库（生产路径不受影响）

用法：python ops\\tests\\test_db_isolation.py
"""
from __future__ import annotations

import os
import sys
import tempfile
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
    tmp = Path(tempfile.mkdtemp(prefix="yy_iso_")) / "iso.db"
    saved = {k: os.environ.get(k) for k in ("DEMO_TEST", "DEMO_LEDGER_DB", "DEMO_DB")}
    os.environ.pop("DEMO_LEDGER_DB", None)
    os.environ.pop("DEMO_DB", None)
    os.environ["DEMO_TEST"] = "1"

    from astrbot.core.utils import life_state, memory_facts, mood_state  # noqa: PLC0415

    for label, mod in (("memory_facts", memory_facts),
                       ("mood_state", mood_state),
                       ("life_state", life_state)):
        print(f"\n[{label}] 无 db_path → 拒绝")
        try:
            mod._db()
            check(False, "应当抛 SystemExit", "未抛")
        except SystemExit as e:
            check("隔离自检失败" in str(e), "拒绝并说明原因", str(e)[:40])
        except Exception as e:  # noqa: BLE001
            check(False, "应当抛 SystemExit", type(e).__name__)

    print("\n[4] 显式临时库 → 正常")
    for label, mod in (("memory_facts", memory_facts),
                       ("mood_state", mood_state),
                       ("life_state", life_state)):
        p = mod._db(tmp)
        check(str(p) == str(tmp), f"{label} 接受临时库", str(p))

    print("\n[5] 非测试模式 → 允许默认库")
    os.environ["DEMO_TEST"] = "0"
    for label, mod in (("memory_facts", memory_facts),
                       ("mood_state", mood_state),
                       ("life_state", life_state)):
        try:
            mod._db()
            check(True, f"{label} 生产路径放行")
        except SystemExit:
            check(False, f"{label} 不该拒绝")

    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
