# -*- coding: utf-8 -*-
"""R09/P3-3：送审/历史侧表保留来源元数据，不再是无来源 assistant。"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _dbcopy import snapshot  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="yy_source_"))
DB = TMP / "data_v4.db"
snapshot(CORE / "data/data_v4.db", DB)
os.environ["DEMO_LEDGER_DB"] = str(DB)
os.environ["DEMO_TEST"] = "1"

from astrbot.core.utils.send_ledger import Ledger  # noqa: E402

UMO = "aiocqhttp:FriendMessage:100000000"
PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cid = con.execute("select conversation_id from conversations limit 1").fetchone()[0]
    con.close()
    led = Ledger(DB)

    print("\n[1] 虚拟来源任务：历史带 source_type/source_ref/event_id")
    tid = led.new_task(session=UMO, kind="text", text="晚上练习练到手指都僵了（",
                       conversation_id=cid, managed_history=True,
                       source_type="virtual", source_ref="evt-1", event_id="evt-1")
    check(led.claim(tid) is True, "claim")
    check(led.finish_ok(tid) is True, "finish_ok")
    check(led.commit_history(tid) == "committed", "commit_history")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    hist = json.loads(con.execute("select content from conversations where conversation_id=?",
                                  (cid,)).fetchone()[0])
    last = hist[-1]
    check(last.get("source_type") == "virtual", "source_type=virtual", str(last))
    check(last.get("source_ref") == "evt-1", "source_ref=evt-1")
    check(last.get("event_id") == "evt-1", "event_id=evt-1")
    row = con.execute("select source_type,source_ref,event_id from yy_history_commit"
                      " where task_id=?", (tid,)).fetchone()
    check(row == ("virtual", "evt-1", "evt-1"), "history_commit 侧表", str(row))
    con.close()

    print("\n[2] 未显式传来源：至少 model_output + task_id")
    tid2 = led.new_task(session=UMO, kind="text", text="在的（",
                        conversation_id=cid, managed_history=True)
    led.claim(tid2); led.finish_ok(tid2); led.commit_history(tid2)
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    hist = json.loads(con.execute("select content from conversations where conversation_id=?",
                                  (cid,)).fetchone()[0])
    last = hist[-1]
    check(last.get("source_type") == "model_output", "默认 model_output")
    check(last.get("source_ref") == tid2, "默认 source_ref=task_id")
    con.close()

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
