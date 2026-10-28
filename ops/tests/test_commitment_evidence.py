#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R08/L08：承诺激活必须绑定真实送达的任务（离线回归）。

覆盖：
  [1] 不存在的 delivered_task_id → 拒绝激活
  [2] 存在但 state=failed → 拒绝
  [3] state=sent → 允许激活
  [4] 没有 delivered_task_id → 拒绝
  [5] 纯生活层库（无 yy_send_task 表）→ 放行（兼容影子测试）

用法：python ops\\tests\\test_commitment_evidence.py
"""
from __future__ import annotations

import os
import sqlite3
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
    from astrbot.core.utils import life_state as ls  # noqa: PLC0415

    UMO = "aiocqhttp:FriendMessage:10001"

    # 带台账的库
    tmp = Path(tempfile.mkdtemp(prefix="yy_commit_"))
    db = tmp / "c.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE yy_send_task (task_id TEXT PRIMARY KEY, state TEXT)")
    con.execute("INSERT INTO yy_send_task VALUES('ok-1','sent')")
    con.execute("INSERT INTO yy_send_task VALUES('bad-1','failed')")
    con.commit()
    con.close()

    cid = ls.add_commitment(UMO, "明天陪你练习", db_path=db)

    print("\n[1] 不存在的任务 → 拒绝")
    check(ls.set_commitment_status(cid, "active", delivered_task_id="nope", db_path=db) is False,
          "拒绝激活")

    print("\n[2] failed 任务 → 拒绝")
    check(ls.set_commitment_status(cid, "active", delivered_task_id="bad-1", db_path=db) is False,
          "拒绝激活")

    print("\n[3] sent 任务 → 允许")
    check(ls.set_commitment_status(cid, "active", delivered_task_id="ok-1", db_path=db) is True,
          "允许激活")
    rows = ls.list_commitments(UMO, db_path=db)
    check(any(r["status"] == "active" and r["delivered_task_id"] == "ok-1" for r in rows),
          "状态已生效", str(rows))

    print("\n[4] 没有任务 ID → 拒绝")
    cid2 = ls.add_commitment(UMO, "再说一条", db_path=db)
    check(ls.set_commitment_status(cid2, "active", db_path=db) is False, "拒绝激活")

    print("\n[5] 纯生活层库（无台账表）→ 放行")
    db2 = tmp / "life.db"
    cid3 = ls.add_commitment(UMO, "影子测试", db_path=db2)
    check(ls.set_commitment_status(cid3, "active", delivered_task_id="x", db_path=db2) is True,
          "无台账时放行")

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
