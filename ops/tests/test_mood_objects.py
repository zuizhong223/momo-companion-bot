#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R06：情绪的对象、否定与原子去重（离线回归）。

覆盖：
  [1] “你朋友真讨厌”不动角色的情绪（第三方，不是冲机器人）
  [2] “别说了 烦” 才动机器人（明确冲机器人/命令）
  [3] “我很累也很烦” 同时记下用户的两种状态（不同维度不同事件 ID）
  [4] 同一 event_id 重放：只生效一次（单事务认领）
  [5] “你妈也很烦” 仍是第三方

用法：python ops\\tests\\test_mood_objects.py
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


def near(a, b, eps=1e-6):
    return abs(float(a) - float(b)) < eps


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="yy_mood_"))
    db = tmp / "m.db"
    from astrbot.core.utils.mood_state import apply, current  # noqa: PLC0415

    UMO = "aiocqhttp:FriendMessage:10001"

    print("\n[1] 你朋友真讨厌：不动机器人")
    v0, e0 = current(UMO, "her", db)
    apply(UMO, "你朋友真讨厌", event_id="m1", db_path=db)
    v1, e1 = current(UMO, "her", db)
    check(near(v0, v1), "角色的 valence 没变", f"{v0}->{v1}")

    print("\n[2] 别说了 烦：明确冲机器人")
    apply(UMO, "别说了 烦", event_id="m2", db_path=db)
    v2, _ = current(UMO, "her", db)
    check(v2 < v1, "角色的 valence 下降了", f"{v1}->{v2}")

    print("\n[3] 我很累也很烦：两种状态都记下")
    uv0, ue0 = current(UMO, "user", db)
    apply(UMO, "我很累也很烦", event_id="m3", db_path=db)
    uv1, ue1 = current(UMO, "user", db)
    check(ue1 < ue0, "用户的 energy 下降", f"{ue0}->{ue1}")
    check(uv1 < uv0, "用户的 valence 下降", f"{uv0}->{uv1}")

    print("\n[4] 同一 event_id 重放：只生效一次")
    uv_before, ue_before = current(UMO, "user", db)
    apply(UMO, "我很累也很烦", event_id="m3", db_path=db)
    uv_after, ue_after = current(UMO, "user", db)
    check(near(uv_before, uv_after) and near(ue_before, ue_after),
          "重放不再累加", f"{uv_before},{ue_before} -> {uv_after},{ue_after}")

    print("\n[5] 你妈也很烦：仍是第三方")
    v3, _ = current(UMO, "her", db)
    apply(UMO, "你妈也很烦", event_id="m5", db_path=db)
    v4, _ = current(UMO, "her", db)
    check(near(v3, v4), "角色的 valence 没变", f"{v3}->{v4}")

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
