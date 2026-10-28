#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R02 崩溃矩阵：多段回复在四个注入点崩溃后的恢复行为（子进程 os._exit）。

覆盖：
  [A] 全部 sent、提交历史前崩 → recover 补一次，且只一次
  [B] 历史已提交后崩 → recover 不再重复写
  [C] 认领后、确认前崩（平台可能已收到）→ 标 unknown，不重发、不写历史
  [D] 第二段发送前崩 → 整轮 partial，不写整段历史；补齐后才提交

用法：python ops\\tests\\test_crash_matrix.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
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


def make_db(tmp: Path) -> Path:
    db = tmp / "t.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE conversations (conversation_id TEXT PRIMARY KEY,"
                " content TEXT, updated_at TEXT)")
    con.execute("INSERT INTO conversations VALUES('cid1','[]','2026-10-26 10:00:00')")
    con.commit()
    con.close()
    return db


def hist_count(db: Path) -> int:
    con = sqlite3.connect(db)
    try:
        row = con.execute("SELECT content FROM conversations WHERE conversation_id='cid1'").fetchone()
        return len(json.loads(row[0] or "[]")) if row else -1
    finally:
        con.close()


CHILD_PRELUDE = """
import os, sys
sys.path.insert(0, r"{core}")
from astrbot.core.utils.send_ledger import Ledger, crash_point
db = r"{db}"
led = Ledger(db)
"""


def run_child(body: str, db: Path, crash_at: str) -> int:
    code = CHILD_PRELUDE.format(core=str(CORE), db=str(db)) + body
    env = dict(os.environ, DEMO_TEST="1", DEMO_LEDGER_DB=str(db),
               DEMO_CRASH_AT=crash_at, PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=120)
    return p.returncode


def setup_turn(db: Path, reply_id: str, total: int):
    from astrbot.core.utils.send_ledger import Ledger  # noqa: PLC0415
    led = Ledger(db)
    led.start_turn(reply_id=reply_id, session="p:10001", total=total, umo=UMO,
                   history_text="整轮", conversation_id="cid1")
    for i in range(total):
        tid = f"{reply_id}:{i}"
        led.new_task(session="p:10001", kind="segment", text=f"s{i}",
                     conversation_id="cid1", umo=UMO, reply_id=reply_id, seq=i,
                     managed_history=False, task_id=tid, total=total)
    return led


def mark_sent(led, tid: str):
    led.claim(tid)
    led.finish_ok(tid)


def main() -> int:
    os.environ["DEMO_TEST"] = "1"
    tmp = Path(tempfile.mkdtemp(prefix="yy_crash_"))

    # ---------- [A] 提交历史前崩 ----------
    print("\n[A] 全部 sent、提交历史前崩 → recover 补一次")
    (tmp / "a").mkdir(exist_ok=True)
    dbA = make_db(tmp / "a")
    os.environ["DEMO_LEDGER_DB"] = str(dbA)
    led = setup_turn(dbA, "tA", 2)
    mark_sent(led, "tA:0")
    mark_sent(led, "tA:1")
    prep = led.prepare_turn_commit("tA")
    check(prep["status"] == "ready", "就绪", str(prep))
    rc = run_child(f"led.commit_history({prep['task_id']!r})\n", dbA, "before_history_commit")
    check(rc not in (0,), "子进程确实崩了", str(rc))
    check(hist_count(dbA) == 0, "崩溃后历史没写", str(hist_count(dbA)))

    from astrbot.core.utils.send_ledger import Ledger  # noqa: PLC0415
    led2 = Ledger(dbA)
    led2.recover()
    check(hist_count(dbA) == 1, "恢复后 +1", str(hist_count(dbA)))
    led2.recover()
    check(hist_count(dbA) == 1, "再恢复不重复", str(hist_count(dbA)))

    # ---------- [B] 历史已提交后崩 ----------
    print("\n[B] 历史提交后崩 → 不再重复")
    (tmp / "b").mkdir(exist_ok=True)
    dbB = make_db(tmp / "b")
    os.environ["DEMO_LEDGER_DB"] = str(dbB)
    ledB = setup_turn(dbB, "tB", 1)
    mark_sent(ledB, "tB:0")
    prepB = ledB.prepare_turn_commit("tB")
    rc = run_child(f"led.commit_history({prepB['task_id']!r})\n", dbB, "after_history_commit")
    check(hist_count(dbB) == 1, "崩溃前已提交", str(hist_count(dbB)))
    Ledger(dbB).recover()
    check(hist_count(dbB) == 1, "恢复不重复", str(hist_count(dbB)))

    # ---------- [C] 认领后、确认前崩 ----------
    print("\n[C] 认领后确认前崩 → unknown，不重发、不写历史")
    (tmp / "c").mkdir(exist_ok=True)
    dbC = make_db(tmp / "c")
    os.environ["DEMO_LEDGER_DB"] = str(dbC)
    ledC = setup_turn(dbC, "tC", 1)
    rc = run_child(
        "led.claim('tC:0')\n"
        "crash_point('after_send_confirm')\n"
        "led.finish_ok('tC:0')\n", dbC, "after_send_confirm")
    check(rc not in (0,), "子进程确实崩了", str(rc))
    ledC = Ledger(dbC)
    check(ledC.state_of("tC:0") == "sending", "停在 sending", str(ledC.state_of("tC:0")))
    ledC.recover(grace_s=0)
    check(ledC.state_of("tC:0") == "unknown", "恢复标 unknown", str(ledC.state_of("tC:0")))
    check(hist_count(dbC) == 0, "历史没写", str(hist_count(dbC)))

    # ---------- [D] 第二段发送前崩 ----------
    print("\n[D] 第二段发送前崩 → partial，不写整段历史")
    (tmp / "d").mkdir(exist_ok=True)
    dbD = make_db(tmp / "d")
    os.environ["DEMO_LEDGER_DB"] = str(dbD)
    ledD = setup_turn(dbD, "tD", 2)
    rc = run_child(
        "led.claim('tD:0')\n"
        "led.finish_ok('tD:0')\n"
        "crash_point('before_segment_send')\n", dbD, "before_segment_send")
    check(rc not in (0,), "子进程确实崩了", str(rc))
    ledD = Ledger(dbD)
    prepD = ledD.prepare_turn_commit("tD")
    check(prepD["status"] == "partial", "判为 partial", str(prepD))
    check(hist_count(dbD) == 0, "历史没写", str(hist_count(dbD)))
    mark_sent(ledD, "tD:1")
    prepD2 = ledD.prepare_turn_commit("tD")
    check(prepD2["status"] == "ready", "补齐后 ready", str(prepD2))
    ledD.commit_history(prepD2["task_id"])
    ledD.mark_turn_committed("tD")
    check(hist_count(dbD) == 1, "提交一次", str(hist_count(dbD)))

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
