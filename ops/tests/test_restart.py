# -*- coding: utf-8 -*-
"""A01/A05 离线演练：发送成功后“重启”，同 token 不再发、历史不重复。

用正式库副本 + FakeBot，不连 QQ、不发真实消息。
"""
import asyncio
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _dbcopy import snapshot  # noqa: E402

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(ROOT / "ops/isolate"))

TMP = Path(tempfile.mkdtemp(prefix="yy_restart_"))
DB = TMP / "data_v4.db"
snapshot(CORE / "data/data_v4.db", DB)
os.environ["DEMO_LEDGER_DB"] = str(DB)
os.environ["DEMO_TEST"] = "1"

from _dbcopy import snapshot
from fakebot import FakeBot  # noqa: E402

from astrbot.core.message.components import Plain  # noqa: E402
from astrbot.core.message.message_event_result import MessageChain  # noqa: E402
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (  # noqa: E402
    AiocqhttpMessageEvent,
)
from astrbot.core.utils.send_ledger import Ledger  # noqa: E402

UMO = "aiocqhttp:FriendMessage:100000000"
SID = "100000000"
PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def assistant_count() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    content = con.execute("select content from conversations where user_id=?",
                          (UMO,)).fetchone()[0]
    con.close()
    return sum(1 for m in json.loads(content) if m.get("role") == "assistant")


def send(bot, text, token):
    return asyncio.run(AiocqhttpMessageEvent.send_message(
        bot=bot, message_chain=MessageChain([Plain(text)]), event=None,
        is_group=False, session_id=SID, client_token=token,
        managed_history=True, umo=UMO, history_text=text,
    ))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    a0 = assistant_count()

    print("\n[1] 重启前：发送成功、历史 +1")
    l1 = Ledger(DB)
    b1 = FakeBot("ok")
    r1 = send(b1, "重启前发的", "restart-token-1")
    check(r1 and r1.get("state") == "sent", "sent", str(r1))
    a1 = assistant_count()
    check(a1 == a0 + 1, f"历史 +1（{a0}->{a1}）")
    check(l1.get("restart-token-1")["history_state"] == "committed", "已提交历史")

    print("\n[2] 重启后：新 Ledger、同 token 不再发、历史不重复")
    Ledger(DB)  # 模拟新进程重新建连接
    b2 = FakeBot("ok")
    r2 = send(b2, "重启前发的", "restart-token-1")
    check(r2 and r2.get("state") == "sent", "返回已有 sent", str(r2))
    check(len(b2.calls) == 0, "没有再次发送", str(len(b2.calls)))
    check(assistant_count() == a1, "历史没有重复")

    print("\n[3] 重启后新消息：正常发送并 +1")
    r3 = send(b2, "重启后发的", "restart-token-2")
    check(r3 and r3.get("state") == "sent", "新消息 sent", str(r3))
    check(assistant_count() == a1 + 1, "历史再 +1")

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
