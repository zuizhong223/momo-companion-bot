# -*- coding: utf-8 -*-
"""P0/A5：payload 冲突要落独立事件表，observe 能看到，不污染 yy_send_task。"""
import asyncio
import importlib.util
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

TMP = Path(tempfile.mkdtemp(prefix="yy_conflict_"))
DB = TMP / "conflict.db"
os.environ["DEMO_TEST"] = "1"
os.environ["DEMO_LEDGER_DB"] = str(DB)

PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


async def accept():
    return {"message_id": "mid"}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
        AiocqhttpMessageEvent,
    )
    from astrbot.core.utils.send_ledger import Ledger

    ledger = Ledger(DB)
    print("\n[1] 首次发送占用 task，之后同 task 换音频 = 冲突")
    r1 = asyncio.run(AiocqhttpMessageEvent._tracked_send(
        accept, session_key="p:1", text="语音", kind="record",
        reply_id="conflict", seq=0, client_token="conflict",
        managed_history=False, umo="u", audio_id="voice-1"))
    check(r1.get("state") == "sent", "首次 sent", str(r1))
    r2 = asyncio.run(AiocqhttpMessageEvent._tracked_send(
        accept, session_key="p:1", text="语音", kind="record",
        reply_id="conflict", seq=0, client_token="conflict",
        managed_history=False, umo="u", audio_id="voice-2"))
    check(r2.get("state") == "skipped" and r2.get("error") == "payload_mismatch",
          "换音频被拒绝", str(r2))

    print("\n[2] 冲突落独立表，原 task 状态不被改成 failed")
    con = sqlite3.connect(DB)
    n = con.execute("select count(*) from yy_send_conflict").fetchone()[0]
    row = con.execute("select task_id, field, text_sha, created_at"
                      " from yy_send_conflict order by created_at desc limit 1").fetchone()
    state = con.execute("select state from yy_send_task where task_id='conflict'").fetchone()[0]
    con.close()
    check(n == 1, "冲突表 1 条", str(n))
    check(row and row[0] == "conflict" and row[1] == "payload", "任务/字段记录", str(row))
    check(row and row[2] and len(row[2]) == 64, "只存 sha256，不存正文")
    check(state == "sent", "原 task 仍是 sent", str(state))

    print("\n[3] observe 从同一来源计数")
    os.environ["DEMO_DB"] = str(DB)
    spec = importlib.util.spec_from_file_location("observe_p0", ROOT / "ops/verify/observe.py")
    observe = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = observe
    spec.loader.exec_module(observe)
    c = observe.counts()
    check(c.get("payload_mismatch") == 1, "payload_mismatch=1", str(c))
    check(c.get("payload_mismatch_last_at"), "有最近冲突时间", str(c))

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
