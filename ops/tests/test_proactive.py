# -*- coding: utf-8 -*-
"""P0-1/P0-3 验收：任务 ID 贯穿 + managed 历史提交 + unknown 不重发。

全部在**数据库副本**上跑，用 FakeBot，不连正式实例、不发 QQ。
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

TMP = Path(tempfile.mkdtemp(prefix="yy_proactive_"))
DB = TMP / "data_v4.db"
snapshot(CORE / "data/data_v4.db", DB)
os.environ["DEMO_LEDGER_DB"] = str(DB)
os.environ["DEMO_TEST"] = "1"          # 隔离自检：不许碰正式路径

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


async def send(bot, text, token, managed=False, history_text=None):
    return await AiocqhttpMessageEvent.send_message(
        bot=bot, message_chain=MessageChain([Plain(text)]), event=None,
        is_group=False, session_id=SID, client_token=token,
        managed_history=managed, umo=UMO if managed else None,
        history_text=history_text,
    )


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ledger = Ledger(DB)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    print("\n[1] 新任务：sent + managed 历史提交")
    a0 = assistant_count()
    bot = FakeBot("ok")
    tok = "token-managed-1"
    r = loop.run_until_complete(send(bot, "主动消息第一版", tok, managed=True,
                                     history_text="主动消息第一版"))
    check(r and r.get("state") == "sent", "state=sent", str(r))
    check(len(bot.calls) == 1, "FakeBot 发了 1 次", str(len(bot.calls)))
    a1 = assistant_count()
    check(a1 == a0 + 1, f"历史 +1（{a0}->{a1}）")
    check(ledger.get(tok)["history_state"] == "committed", "history_state=committed")

    print("\n[2] 同一 token 再来一次：不发、不重复写历史")
    r2 = loop.run_until_complete(send(bot, "主动消息第一版", tok, managed=True,
                                      history_text="主动消息第一版"))
    check(r2 and r2.get("state") == "sent", "返回已有状态 sent", str(r2))
    check(len(bot.calls) == 1, "FakeBot 没有再发", str(len(bot.calls)))
    check(assistant_count() == a1, "历史没有重复")

    print("\n[3] 平台已接受后断连 → unknown，重试也不再发")
    bot2 = FakeBot("accepted_lost")
    tok2 = "token-unknown-1"
    try:
        loop.run_until_complete(send(bot2, "可能已经发出去的", tok2, managed=True,
                                     history_text="可能已经发出去的"))
    except Exception as exc:  # noqa: BLE001
        print(f"      (异常抛给上层: {type(exc).__name__})")
    st = ledger.get(tok2)["state"]
    check(st == "unknown", "台账 unknown", st)
    r3 = loop.run_until_complete(send(bot2, "可能已经发出去的", tok2, managed=True,
                                      history_text="可能已经发出去的"))
    check(r3.get("state") == "unknown" and not r3.get("claimed", False),
          "同 token 重试被拒（不再发）", str(r3))
    check(len(bot2.calls) == 1, "FakeBot 只尝试了 1 次", str(len(bot2.calls)))
    check(assistant_count() == a1, "unknown 不写历史")

    print("\n[4] 明确失败 → failed；同 token 重试复用同一任务")
    bot3 = FakeBot("definite")
    tok3 = "token-retry-1"
    try:
        loop.run_until_complete(send(bot3, "第一次被拒", tok3))
    except Exception:  # noqa: BLE001
        pass
    check(ledger.get(tok3)["state"] == "failed", "state=failed")
    bot3.mode = "ok"                     # 平台恢复
    r4 = loop.run_until_complete(send(bot3, "第一次被拒", tok3))
    check(r4 and r4.get("state") == "sent", "重试后 sent", str(r4))
    check(len(bot3.calls) == 2, "FakeBot 发了 2 次（含重试）", str(len(bot3.calls)))
    attempts = _attempts(ledger, tok3)
    check(len(attempts) == 2 and attempts[0]["state"] == "failed"
          and attempts[1]["state"] == "sent", "两次 attempt 都记了", str(attempts))

    print("\n[5] 重试超过 3 次后不再认领")
    bot4 = FakeBot("definite")
    tok4 = "token-retry-limit"
    for _ in range(4):
        try:
            loop.run_until_complete(send(bot4, "一直被拒", tok4))
        except Exception:  # noqa: BLE001
            pass
    row = ledger.get(tok4)
    check(row["retry_count"] >= 3, "retry_count 已到上限", str(row["retry_count"]))
    check(len(bot4.calls) <= 4, "没有无限重试", str(len(bot4.calls)))

    print(f"\n副本库: {DB}")
    print("RESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


def _attempts(ledger, task_id):
    con = sqlite3.connect(f"file:{ledger.db_path}?mode=ro", uri=True)
    rows = con.execute(
        "select attempt_no, state from yy_send_attempt where task_id=?"
        " order by attempt_no", (task_id,)).fetchall()
    con.close()
    return [{"no": r[0], "state": r[1]} for r in rows]


if __name__ == "__main__":
    sys.exit(main())
