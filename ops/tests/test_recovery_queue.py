# -*- coding: utf-8 -*-
"""R05/P1-4：needs_target / partial 必须能被 recover 重扫，而不是永远卡住。"""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))
os.environ["DEMO_TEST"] = "1"

from astrbot.core.utils.send_ledger import Ledger  # noqa: E402

PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def make_db(tmp: Path) -> Path:
    db = tmp / "t.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE conversations (conversation_id TEXT PRIMARY KEY,"
                " content TEXT, updated_at TEXT)")
    con.execute("INSERT INTO conversations VALUES('cid1','[]','2026-10-26 10:00:00')")
    con.commit()
    con.close()
    return db


def hist(db: Path) -> int:
    con = sqlite3.connect(db)
    try:
        return len(json.loads(con.execute(
            "select content from conversations where conversation_id='cid1'"
        ).fetchone()[0] or "[]"))
    finally:
        con.close()


def sent_task(led: Ledger, rid: str, tid: str, text: str, *, cid="cid1", managed=True, seq=0):
    led.new_task(session="p:1", kind="segment", text=text, conversation_id=cid,
                 umo="aiocqhttp:FriendMessage:1", reply_id=rid, seq=seq,
                 managed_history=managed, task_id=tid)
    led.claim(tid)
    led.finish_ok(tid)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    tmp = Path(tempfile.mkdtemp(prefix="yy_r05_"))
    db = make_db(tmp)
    os.environ["DEMO_LEDGER_DB"] = str(db)
    led = Ledger(db)

    print("\n[1] needs_target：缺 cid 时不静默结案")
    sent_task(led, "nt", "nt:0", "缺目标", cid=None)
    rec = led.recover(grace_s=0)
    row = led.get("nt:0")
    check(row["state"] == "sent", "任务仍 sent", row["state"])
    check(row["history_state"] == "needs_target", "标 needs_target", row["history_state"])
    check(rec["needs_target"] >= 1, "recover 报告 needs_target", str(rec))
    check(hist(db) == 0, "没写历史")

    print("\n[2] 目标补齐后：recover 应能提交")
    con = sqlite3.connect(db)
    con.execute("update yy_send_task set conversation_id='cid1' where task_id='nt:0'")
    con.commit()
    con.close()
    led.recover(grace_s=0)
    check(led.get("nt:0")["history_state"] == "committed", "补目标后 committed")
    check(hist(db) == 1, "历史 +1", str(hist(db)))

    print("\n[3] partial：兄弟段未齐时不写整段")
    sent_task(led, "p", "p:0", "第一段", managed=True, seq=0)
    led.new_task(session="p:1", kind="segment", text="第二段", conversation_id="cid1",
                 umo="aiocqhttp:FriendMessage:1", reply_id="p", seq=1,
                 managed_history=False, task_id="p:1")
    r = led.commit_history("p:0")
    check(r == "partial", "commit 返回 partial", r)
    check(led.get("p:0")["history_state"] == "partial", "history=partial")
    check(hist(db) == 1, "没有写第二段")

    print("\n[4] 兄弟段补齐后：recover 重扫 partial 并提交一次")
    led.claim("p:1")
    led.finish_ok("p:1")
    rec = led.recover(grace_s=0)
    check(led.get("p:0")["history_state"] == "committed", "partial 被重扫提交")
    check(hist(db) == 2, "历史 +1", str(hist(db)))
    led.recover(grace_s=0)
    check(hist(db) == 2, "再 recover 不重复", str(hist(db)))
    check(rec["partial"] >= 0, "recover 不报错", str(rec))

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
