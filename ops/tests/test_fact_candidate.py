#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R01：候选记忆不得污染已确认事实（离线回归）。

覆盖：
  [1] 已确认 + 候选同键 → 仍 active，来源/置信度不被改写
  [2] 已确认生日 1/1 + 候选 2/2 → 旧值不 supersede
  [3] 候选被撤回后，旧消息重放不能复活
  [4] 显式确认更正 → 旧值 superseded、新值 active
  [5] 候选不参与正常召回
  [6] DEMO_CANDIDATE=0 时自动候选整体关闭

用法：python ops\\tests\\test_fact_candidate.py
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


def rows(db, subject="me"):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(
            "SELECT subject,predicate,obj,status,source,confidence FROM yy_fact2"
            " WHERE subject=? ORDER BY predicate,obj", (subject,))]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="yy_fact_"))
    db = tmp / "f.db"
    from astrbot.core.utils.memory_facts import (  # noqa: PLC0415
        capture_candidates, drop_candidate, promote_candidate, recall, remember)

    print("\n[1] 已确认 + 候选同键：不得降级")
    remember("me", "喜欢", "蓝莓", status="active", source="user_confirmed",
             confidence=1.0, db_path=db)
    remember("me", "喜欢", "蓝莓", status="candidate", source="auto",
             confidence=0.4, db_path=db)
    r = {x["obj"]: x for x in rows(db)}
    check(r["蓝莓"]["status"] == "active", "仍 active", r["蓝莓"]["status"])
    check(r["蓝莓"]["source"] == "user_confirmed", "来源未被改写", r["蓝莓"]["source"])
    check(float(r["蓝莓"]["confidence"]) >= 1.0, "置信度未下降", r["蓝莓"]["confidence"])

    print("\n[2] 已确认 1/1 + 候选 2/2：旧值不被 supersede")
    remember("me", "生日", "1月1日", status="active", source="user_confirmed",
             confidence=1.0, db_path=db)
    remember("me", "生日", "2月2日", status="candidate", source="auto",
             confidence=0.4, db_path=db)
    r = {x["obj"]: x for x in rows(db)}
    check(r["1月1日"]["status"] == "active", "旧生日仍 active", r["1月1日"]["status"])
    check(r["2月2日"]["status"] == "candidate", "新值只是候选", r["2月2日"]["status"])

    print("\n[3] 撤回后重放：不能复活")
    drop_candidate("me", "生日", "2月2日", db_path=db)
    remember("me", "生日", "2月2日", status="candidate", source="auto",
             confidence=0.4, db_path=db)
    r = {x["obj"]: x for x in rows(db)}
    check(r["2月2日"]["status"] == "retracted", "仍是 retracted", r["2月2日"]["status"])

    print("\n[4] 显式确认更正：旧的退出、新的生效")
    remember("me", "生日", "2月2日", status="active", source="user_confirmed",
             confidence=1.0, db_path=db)
    r = {x["obj"]: x for x in rows(db)}
    check(r["1月1日"]["status"] == "superseded", "旧值 superseded", r["1月1日"]["status"])
    check(r["2月2日"]["status"] == "active", "新值 active", r["2月2日"]["status"])

    print("\n[5] 候选不参与正常召回")
    remember("me", "住在", "柏林", status="candidate", source="auto",
             confidence=0.4, db_path=db)
    hit = [x for x in recall("me", db_path=db) if x.get("obj") == "柏林"]
    check(not hit, "候选不出现在召回里", str(hit))

    print("\n[6] DEMO_CANDIDATE=0：自动候选整体关闭")
    before = len(rows(db))
    os.environ["DEMO_CANDIDATE"] = "0"
    n = capture_candidates("me", "我喜欢草莓", "msg-1", db_path=db)
    os.environ.pop("DEMO_CANDIDATE", None)
    after = len(rows(db))
    check(n == 0, "返回 0 条", str(n))
    check(after == before, "库没有新增", f"{before}->{after}")

    print("\n[7] 确认更正走单事务；无可确认候选时不误改")
    remember("me", "生日", "3月3日", status="candidate", source="auto",
             confidence=0.4, db_path=db)
    check(promote_candidate("me", "生日", "3月3日", db_path=db) is True, "promote 成功")
    r = {x["obj"]: x for x in rows(db)}
    check(r["2月2日"]["status"] == "superseded", "旧值 superseded", r["2月2日"]["status"])
    check(r["3月3日"]["status"] == "active", "新值 active", r["3月3日"]["status"])
    check(promote_candidate("me", "生日", "3月3日", db_path=db) is False,
          "重复 promote 返回 False")
    check(promote_candidate("me", "生日", "不存在", db_path=db) is False,
          "不存在候选返回 False")

    print("\n[8] 多值属性默认不覆盖：喜欢 蓝莓 + 候选 草莓")
    remember("me", "喜欢", "蓝莓", status="active", source="user_confirmed",
             confidence=1.0, db_path=db)
    remember("me", "喜欢", "草莓", status="candidate", source="auto",
             confidence=0.4, db_path=db)
    check(promote_candidate("me", "喜欢", "草莓", db_path=db) is True, "promote 草莓")
    likes = {x["obj"]: x["status"] for x in rows(db) if x["predicate"] == "喜欢"}
    check(likes.get("蓝莓") == "active" and likes.get("草莓") == "active",
          "蓝莓和草莓都 active", str(likes))

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
