# -*- coding: utf-8 -*-
"""P0-3：yy_turn 清单缺行时，recover/commit_history 不能把部分段写成整轮。"""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

TMP = Path(tempfile.mkdtemp(prefix="yy_turn_gap_"))
DB = TMP / "turn.db"
os.environ["DEMO_TEST"] = "1"
os.environ["DEMO_LEDGER_DB"] = str(DB)

PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def history_count(cid: str) -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    row = con.execute("select content from conversations where conversation_id=?",
                      (cid,)).fetchone()
    con.close()
    return len(json.loads(row[0] or "[]")) if row else 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    from astrbot.core.utils.send_ledger import Ledger

    con = sqlite3.connect(DB)
    con.execute("create table conversations(conversation_id text primary key,"
                "user_id text, content text, updated_at text)")
    con.execute("insert into conversations values(?,?,?,?)",
                ("cid-gap", "u", "[]", "2026-10-26 00:00:00"))
    con.commit()
    con.close()

    led = Ledger(DB)
    rid = "turn-gap"
    check(led.start_turn(reply_id=rid, session="p:u", total=3, umo="u",
                         history_text="甲乙丙", conversation_id="cid-gap") is True,
          "清单 3 段")
    for i in (0, 1):
        tid = f"{rid}:{i}"
        led.new_task(session="p:u", kind="segment", text=f"seg{i}",
                     conversation_id="cid-gap", umo="u", reply_id=rid, seq=i,
                     managed_history=True, task_id=tid, total=3)
        led.claim(tid)
        led.finish_ok(tid)
    check(led.prepare_turn_commit(rid)["status"] == "partial", "prepare=partial")
    check(history_count("cid-gap") == 0, "提交前历史 0")

    report = led.recover()
    check(history_count("cid-gap") == 0,
          "recover 不能把缺第三段写成整轮", str(report))
    check(report.get("history_committed", 0) == 0,
          "recover 未记 committed", str(report))

    tid2 = f"{rid}:2"
    led.new_task(session="p:u", kind="segment", text="seg2",
                 conversation_id="cid-gap", umo="u", reply_id=rid, seq=2,
                 managed_history=True, task_id=tid2, total=3)
    led.claim(tid2)
    led.finish_ok(tid2)
    report2 = led.recover()
    check(history_count("cid-gap") == 1, "补齐后 recover 写一次历史", str(report2))

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
