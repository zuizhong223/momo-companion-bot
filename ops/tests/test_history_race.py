# -*- coding: utf-8 -*-
"""P0-2 验收：主动提交 vs 被动整段写回，交错时不许互相覆盖。

模拟的是这个场景（GPT 点名的那种）：
  被动流程先读到旧历史 → 主动在中间提交 → 被动再把旧列表整体写回 → 主动那条被抹掉。

两边都拿 `session_lock_manager.acquire_lock(umo)` 之后，写回必须串行，
结果应该是两条都在、顺序不乱。

跑在数据库副本上，用 FakeBot，不连正式实例。
"""
import asyncio
import json
import os
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

TMP = Path(tempfile.mkdtemp(prefix="yy_race_"))
DB = TMP / "data_v4.db"
snapshot(CORE / "data/data_v4.db", DB)
os.environ["DEMO_LEDGER_DB"] = str(DB)
os.environ["DEMO_TEST"] = "1"

from fakebot import FakeBot  # noqa: E402

from astrbot.core.message.components import Plain  # noqa: E402
from astrbot.core.message.message_event_result import MessageChain  # noqa: E402
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (  # noqa: E402
    AiocqhttpMessageEvent,
)
from astrbot.core.utils.send_ledger import Ledger  # noqa: E402
from astrbot.core.utils.session_lock import session_lock_manager  # noqa: E402

UMO = "aiocqhttp:FriendMessage:100000000"
SID = "100000000"
PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def read_hist() -> list:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    row = con.execute("select content from conversations where user_id=?", (UMO,)).fetchone()
    con.close()
    return json.loads(row[0] or "[]")


def text_of(m: dict) -> str:
    return " ".join(p.get("text", "") for p in (m.get("content") or [])
                    if isinstance(p, dict))


async def passive_write(marker: str, delay: float = 0.08) -> None:
    """模拟 AstrBot 被动回复：持锁 → 读整段 → 干点活 → 整体写回。"""
    async with session_lock_manager.acquire_lock(UMO):
        hist = read_hist()
        await asyncio.sleep(delay)          # 模拟生成耗时（锁还握着）
        hist.append({"role": "assistant",
                     "content": [{"type": "text", "text": marker}]})
        con = sqlite3.connect(DB, timeout=15, isolation_level=None)
        con.execute("BEGIN IMMEDIATE")
        con.execute("update conversations set content=? where user_id=?",
                    (json.dumps(hist, ensure_ascii=False), UMO))
        con.execute("COMMIT")
        con.close()


async def proactive_send(text: str, token: str) -> dict:
    return await AiocqhttpMessageEvent.send_message(
        bot=FakeBot("ok"), message_chain=MessageChain([Plain(text)]),
        event=None, is_group=False, session_id=SID,
        client_token=token, managed_history=True, umo=UMO, history_text=text,
    )


async def case1() -> None:
    print("\n[1] 被动先读 → 主动提交 → 被动写回")
    before = len(read_hist())
    t1 = asyncio.create_task(passive_write("被动写的这一条"))
    await asyncio.sleep(0.02)               # 让被动先拿到锁
    r = await proactive_send("主动写的这一条", "race-token-1")
    await t1
    hist = read_hist()
    texts = [text_of(m) for m in hist]
    check(r.get("state") == "sent", "主动发送 sent", str(r))
    check("被动写的这一条" in texts, "被动那条还在")
    check("主动写的这一条" in texts, "主动那条还在（没被覆盖）", f"{len(hist)-before} 条新增")
    check(len(hist) == before + 2, f"正好多两条（{before}->{len(hist)}）")


async def case2() -> None:
    print("\n[2] 50 轮交错：两条都不丢")
    before = len(read_hist())
    tasks = [asyncio.create_task(passive_write(f"被动{i}")) for i in range(25)]
    sends = [asyncio.create_task(proactive_send(f"主动{i}", f"race-tok-{i}"))
             for i in range(25)]
    await asyncio.gather(*tasks, *sends)
    hist = read_hist()
    texts = [text_of(m) for m in hist]
    passive_ok = sum(1 for i in range(25) if f"被动{i}" in texts)
    active_ok = sum(1 for i in range(25) if f"主动{i}" in texts)
    check(passive_ok == 25, "25 条被动都在", str(passive_ok))
    check(active_ok == 25, "25 条主动都在", str(active_ok))
    check(len(hist) == before + 50, f"正好多 50 条（{before}->{len(hist)}）")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(case1())
    loop.run_until_complete(case2())
    print(f"\n副本库: {DB}")
    print("RESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
