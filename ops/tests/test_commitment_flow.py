#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R08 后半：承诺状态机（planned → active → fulfilled/expired）。

覆盖：
  [1] planned 不进提示词（只是草稿）
  [2] 发送成功后按文本匹配转 active，并绑定 task_id
  [3] active 才进提示词
  [4] 文本不匹配 → 不变
  [5] 到期未兑现 → expired，不再进提示词
  [6] 绑定不存在的任务 → 激活被拒

用法：python ops\\tests\\test_commitment_flow.py
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
UMO = "aiocqhttp:FriendMessage:10001"


def check(cond, name, extra=""):
    global PASS
    print(("  OK   " if cond else "  FAIL ") + name + (f" | {extra}" if extra and not cond else ""))
    if not cond:
        PASS = False


def main() -> int:
    from astrbot.core.utils import life_state as ls  # noqa: PLC0415

    tmp = Path(tempfile.mkdtemp(prefix="yy_commit_flow_"))
    db = tmp / "c.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE yy_send_task (task_id TEXT PRIMARY KEY, state TEXT)")
    con.execute("INSERT INTO yy_send_task VALUES('t-sent','sent')")
    con.commit()
    con.close()

    print("\n[1] planned 不进提示词")
    cid = ls.add_commitment(UMO, "明天陪你练习", db_path=db)
    check(ls.commitments_context(UMO, db_path=db) == "", "草稿不进提示",
          ls.commitments_context(UMO, db_path=db))

    print("\n[2] 发送成功后转 active")
    got = ls.activate_from_send(UMO, "明天陪你练习呀（", "t-sent", db_path=db)
    check(got == cid, "命中同一条承诺", str(got))
    rows = ls.list_commitments(UMO, db_path=db)
    row = [r for r in rows if r["commit_id"] == cid][0]
    check(row["status"] == "active", "状态 active", row["status"])
    check(row["delivered_task_id"] == "t-sent", "绑定任务", str(row["delivered_task_id"]))

    print("\n[3] active 才进提示词")
    ctx = ls.commitments_context(UMO, db_path=db)
    check("明天陪你练习" in ctx, "进入提示", ctx)

    print("\n[4] 文本不匹配 → 不变")
    cid2 = ls.add_commitment(UMO, "回头给你带糖", db_path=db)
    check(ls.activate_from_send(UMO, "完全无关的一句", "t-sent", db_path=db) is None,
          "不误激活")
    check([r for r in ls.list_commitments(UMO, db_path=db)
           if r["commit_id"] == cid2][0]["status"] == "planned", "仍是 planned")

    print("\n[5] 到期 → expired，不再进提示")
    con = sqlite3.connect(db)
    con.execute("UPDATE yy_life_commitment SET due_at='2000-01-01T00:00:00+00:00'"
                " WHERE commit_id=?", (cid,))
    con.commit()
    con.close()
    n = ls.expire_overdue(UMO, db_path=db)
    check(n >= 1, "至少过期一条", str(n))
    check("明天陪你练习" not in ls.commitments_context(UMO, db_path=db),
          "过期后不进提示", ls.commitments_context(UMO, db_path=db))

    print("\n[6] 绑定不存在的任务 → 激活被拒")
    cid3 = ls.add_commitment(UMO, "下次带你打本", db_path=db)
    got3 = ls.activate_from_send(UMO, "下次带你打本", "no-such-task", db_path=db)
    check(got3 is None, "拒绝激活", str(got3))

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
