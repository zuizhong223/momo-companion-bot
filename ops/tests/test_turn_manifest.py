#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R02：多段消息的整轮清单（yy_turn）离线回归。

覆盖：
  [1] 三段只送达两段 → partial，不写历史
  [2] 三段全部 sent → ready → 提交一次历史
  [3] 全部 sent 但进程在提交前挂掉 → recover() 补写一次，且只一次
  [4] 重放同一整轮 → already，不再加历史

用法：python ops\\tests\\test_turn_manifest.py
"""
from __future__ import annotations

import json
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


def make_db(tmp: Path) -> Path:
    db = tmp / "t.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE conversations (conversation_id TEXT PRIMARY KEY,"
                " content TEXT, updated_at TEXT)")
    con.execute("INSERT INTO conversations VALUES('cid1','[]','2026-10-26 10:00:00')")
    con.commit()
    con.close()
    return db


def hist_count(db: Path, cid: str = "cid1") -> int:
    con = sqlite3.connect(db)
    try:
        row = con.execute("SELECT content FROM conversations WHERE conversation_id=?",
                          (cid,)).fetchone()
        return len(json.loads(row[0] or "[]")) if row else -1
    finally:
        con.close()


def main() -> int:
    os.environ["DEMO_TEST"] = "1"
    tmp = Path(tempfile.mkdtemp(prefix="yy_turn_"))
    db = make_db(tmp)
    os.environ["DEMO_LEDGER_DB"] = str(db)

    sys.path.insert(0, str(CORE))
    from astrbot.core.utils.send_ledger import (  # noqa: PLC0415
        SENT, get_ledger, Ledger)

    led = Ledger(db)
    UMO = "aiocqhttp:FriendMessage:10001"
    RID = "turn-a"

    print("\n[0] A6：同一 reply_id 首次确定后不可变")
    check(led.start_turn(reply_id="turn-imm", session="p:1", total=3,
                         umo=UMO, history_text="甲乙丙",
                         conversation_id="cid1") is True, "首次建立")
    check(led.start_turn(reply_id="turn-imm", session="p:1", total=2,
                         umo=UMO, history_text="甲乙丙",
                         conversation_id="cid1") is False, "缩减 total 拒绝")
    check(led.start_turn(reply_id="turn-imm", session="p:1", total=4,
                         umo=UMO, history_text="甲乙丙",
                         conversation_id="cid1") is False, "增加 total 拒绝")
    check(led.start_turn(reply_id="turn-imm", session="p:1", total=3,
                         umo=UMO, history_text="甲乙丁",
                         conversation_id="cid1") is False, "换正文拒绝")
    check(led.start_turn(reply_id="turn-imm", session="p:1", total=3,
                         umo=UMO, history_text="甲乙丙",
                         conversation_id="cid2") is False, "换目标拒绝")
    check(led.start_turn(reply_id="turn-imm", session="p:1", total=3,
                         umo=UMO, history_text="甲乙丙",
                         conversation_id="cid1") is True, "原清单重放允许")

    print("\n[1] 三段只送达两段 → partial，不写历史")
    led.start_turn(reply_id=RID, session="p:10001", total=3, umo=UMO,
                   history_text="三段合一", conversation_id="cid1")
    for i in range(3):
        tid = f"{RID}:{i}"
        led.new_task(session="p:10001", kind="segment", text=f"seg{i}",
                     conversation_id="cid1", umo=UMO, reply_id=RID, seq=i,
                     managed_history=False, task_id=tid, total=3)
        if i < 2:
            led.claim(tid)
            led.finish_ok(tid)
    prep = led.prepare_turn_commit(RID)
    check(prep["status"] == "partial", "判为 partial", str(prep))
    check(hist_count(db) == 0, "没有写历史", str(hist_count(db)))

    print("\n[2] 补齐第三段 → ready → 提交一次历史")
    tid2 = f"{RID}:2"
    led.claim(tid2)
    led.finish_ok(tid2)
    prep = led.prepare_turn_commit(RID)
    check(prep["status"] == "ready", "判为 ready", str(prep))
    res = led.commit_history(prep["task_id"])
    check(res in ("committed", "already"), "提交成功", res)
    led.mark_turn_committed(RID)
    check(hist_count(db) == 1, "历史 +1", str(hist_count(db)))

    print("\n[3] 重放：already，不再加历史")
    prep = led.prepare_turn_commit(RID)
    check(prep["status"] == "already", "判为 already", str(prep))
    check(hist_count(db) == 1, "历史仍是 1", str(hist_count(db)))

    print("\n[4] 新整轮：全部 sent 但没提交 → recover() 补一次")
    RID2 = "turn-b"
    led.start_turn(reply_id=RID2, session="p:10001", total=2, umo=UMO,
                   history_text="两段合一", conversation_id="cid1")
    for i in range(2):
        tid = f"{RID2}:{i}"
        led.new_task(session="p:10001", kind="segment", text=f"b{i}",
                     conversation_id="cid1", umo=UMO, reply_id=RID2, seq=i,
                     managed_history=False, task_id=tid, total=2)
        led.claim(tid)
        led.finish_ok(tid)
    check(hist_count(db) == 1, "提交前历史仍是 1", str(hist_count(db)))
    # 模拟进程重启：新 Ledger 实例
    led2 = Ledger(db)
    report = led2.recover()
    check(hist_count(db) == 2, "恢复后历史 +1", str(hist_count(db)))
    check(report.get("history_committed", 0) >= 1, "记录为已提交", str(report))
    report2 = led2.recover()
    check(hist_count(db) == 2, "再次恢复不重复", str(hist_count(db)))
    check(report2.get("history_committed", 0) == 0, "第二次没有新增提交", str(report2))

    print("\n[5] R05：needs_target 补上目标后能继续提交")
    tid = "turn-c:0"
    led.new_task(session="p:10001", kind="segment", text="c0", conversation_id=None,
                 umo=None, reply_id="turn-c", seq=0, managed_history=True,
                 task_id=tid, total=1)
    led.claim(tid)
    led.finish_ok(tid)
    res = led.commit_history(tid)
    check(res == "needs_target", "先记 needs_target", res)
    con = sqlite3.connect(db)
    con.execute("UPDATE yy_send_task SET conversation_id='cid1' WHERE task_id=?", (tid,))
    con.commit()
    con.close()
    led.recover()
    check(hist_count(db) == 3, "补齐目标后恢复提交（2->3）", str(hist_count(db)))

    print("\n[6] R03：同一 task 换 audio_id 必须冲突")
    led.new_task(session="p:10001", kind="record", text="[语音]", audio_id="a1",
                 reply_id="media1", seq=0, managed_history=False, task_id="media-1")
    from astrbot.core.utils.send_ledger import PayloadMismatch  # noqa: PLC0415
    try:
        led.new_task(session="p:10001", kind="record", text="[语音]", audio_id="a2",
                     reply_id="media1", seq=0, managed_history=False, task_id="media-1")
        check(False, "换 audio_id 应报冲突", "未抛异常")
    except PayloadMismatch:
        check(True, "换 audio_id 报 PayloadMismatch")

    print("\n[7] 重复序号 0/1/1：不能 ready")
    RID3 = "turn-dup"
    led.start_turn(reply_id=RID3, session="p:10001", total=2, umo=UMO,
                   history_text="重复序号")
    for i, seq in enumerate((0, 1, 1)):
        tid = f"{RID3}:{i}"
        led.new_task(session="p:10001", kind="segment", text=f"d{i}",
                     conversation_id="cid1", umo=UMO, reply_id=RID3, seq=seq,
                     managed_history=False, task_id=tid, total=2)
        led.claim(tid)
        led.finish_ok(tid)
    prep = led.prepare_turn_commit(RID3)
    check(prep["status"] == "partial", "重复序号判 partial", str(prep))

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
