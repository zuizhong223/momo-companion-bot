# -*- coding: utf-8 -*-
"""P0-A 发送台账 + 真实发送路径的隔离测试。

用 FakeBot 顶替底层网络，把 AiocqhttpMessageEvent.send_message 整条路径跑一遍：
  - 正常发送 / 超时 / 明确拒绝 / 平台已接受后断连
  - 并发认领（2 线程 × 50 轮只允许一个赢）
  - 多段回复（reply_id 相同、seq 递增）
  - 门禁过滤不产生发送
"""
import asyncio
import json
import shutil
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(ROOT / "ops/isolate"))

PROD_DB = CORE / "data/data_v4.db"

TMP = Path(tempfile.mkdtemp(prefix="yy_ledger_"))
DB = TMP / "data_v4.db"
# 不复制正式库：本测试只验证台账状态机，应该从空库开始，
# 否则线上多出来的 unknown/failed 任务会把计数断言带崩（测试隔离）。
import os  # noqa: E402

os.environ["DEMO_LEDGER_DB"] = str(DB)

from fakebot import FakeBot  # noqa: E402

from astrbot.core.message.components import Plain  # noqa: E402
from astrbot.core.message.message_event_result import MessageChain  # noqa: E402
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (  # noqa: E402
    AiocqhttpMessageEvent,
)
from astrbot.core.utils.send_ledger import (  # noqa: E402
    FAILED,
    SENT,
    SKIPPED,
    UNKNOWN,
    Ledger,
)

PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


async def send(bot, text="测试一条消息", is_group=False, sid="100000000"):
    return await AiocqhttpMessageEvent.send_message(
        bot=bot, message_chain=MessageChain([Plain(text)]),
        event=None, is_group=is_group, session_id=sid)


def main() -> int:
    sys.stdout.write("")
    ledger = Ledger(DB)

    print("\n[1] 正常发送")
    bot = FakeBot("ok")
    run(send(bot))
    st = ledger.stats()["state"]
    check(st.get(SENT, 0) >= 1, "task 落成 sent", str(st))
    check(len(bot.calls) == 1, "FakeBot 收到 1 次", str(bot.calls))
    row = ledger.get(ledger.pending_history()[-1]["task_id"]) if ledger.pending_history() else None
    check(row is None, "被动发送不进入待补历史（history_state=external）")

    print("\n[2] 超时 → unknown，不重发")
    bot = FakeBot("timeout")
    try:
        run(send(bot, "超时用的消息"))
    except Exception as exc:  # noqa: BLE001
        print(f"      (抛给上层的异常: {type(exc).__name__})")
    unk = [t for t in _tasks(ledger) if t["state"] == UNKNOWN]
    check(len(unk) == 1, "1 条 unknown", str([t["text"] for t in unk]))
    check(not ledger.claim_retry(unk[0]["task_id"]), "unknown 不允许重试")

    print("\n[3] 明确拒绝 → failed，可重试一次")
    bot = FakeBot("definite")
    try:
        run(send(bot, "被拒的消息"))
    except Exception:  # noqa: BLE001
        pass
    bad = [t for t in _tasks(ledger) if t["state"] == FAILED]
    check(len(bad) == 1, "1 条 failed")
    tid = bad[0]["task_id"]
    check(ledger.claim_retry(tid), "failed 允许重试认领")
    ledger.finish_ok(tid, "retry-1")
    check(ledger.get(tid)["state"] == SENT, "重试后变 sent")

    print("\n[4] 平台已接受后断连 → unknown")
    bot = FakeBot("accepted_lost")
    try:
        run(send(bot, "已接受但断连"))
    except Exception:  # noqa: BLE001
        pass
    check(any(t["state"] == UNKNOWN for t in _tasks(ledger)
              if t["text"] == "已接受但断连"), "记为 unknown，不重发")

    print("\n[5] 并发认领：2 线程 × 50 轮只有一个赢")
    winners = []
    t0 = ledger.new_task(session="race", kind="text", text="并发抢一条")
    lock = threading.Lock()

    def worker():
        for _ in range(50):
            if ledger.claim(t0):
                with lock:
                    winners.append(threading.current_thread().name)
                ledger.finish_ok(t0, "race")

    threads = [threading.Thread(target=worker, name=f"t{i}") for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check(len(winners) == 1, "只有一个执行者认领成功", str(winners))
    check(ledger.get(t0)["state"] == SENT, "最终 sent")

    print("\n[6] 多段回复：reply_id 相同、seq 递增")
    bot = FakeBot("ok")
    rid = "manual-reply-1"
    for i, txt in enumerate(["第一段", "第二段", "第三段"]):
        tid = ledger.new_task(session="p:1", kind="text", text=txt,
                              reply_id=rid, seq=i)
        ledger.claim(tid)
        ledger.finish_ok(tid, f"m{i}")
    rows = [t for t in _tasks(ledger) if t["reply_id"] == rid]
    check(len(rows) == 3 and [r["seq"] for r in rows] == [0, 1, 2], "三段 seq 0/1/2")

    print("\n[7] 去重只服务主动话题")
    ledger.recent_push("p:2", "今天练习练到手都僵了")
    check(ledger.recent_similar("p:2", "今天练习练到手都僵了") is not None, "主动话题能命中")
    check(ledger.recent_similar("p:2", "好好好") is None, "短句不参与")
    ledger.recent_push("p:3", "哈哈哈", scope="proactive")
    check(ledger.recent_similar("p:3", "哈哈哈") is None, "短文本不抑制")

    print("\n[8] 门禁过滤 → 空内容走兜底")
    bot = FakeBot("ok")
    run(send(bot, "（ ）"))
    check(len(bot.calls) == 1, "空内容仍发兜底", str(bot.calls))
    call = str(bot.calls[0])
    check("在" in call, "兜底带「在」", call)

    print("\n[9] resolve：unknown → dropped")
    tid = ledger.new_task(session="p:4", kind="text", text="人工结案测试")
    ledger.claim(tid)
    ledger.mark_unknown(tid, "test")
    check(ledger.resolve(tid, "dropped"), "结案成功")
    check(ledger.get(tid)["state"] == "dropped", "状态 dropped")

    print(f"\nDB: {DB}")
    print("RESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


def _tasks(ledger: Ledger) -> list[dict]:
    import sqlite3

    con = sqlite3.connect(f"file:{ledger.db_path}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT task_id, reply_id, seq, session, conversation_id, kind, text,"
        " state, history_state, retry_count, error FROM yy_send_task").fetchall()
    con.close()
    keys = ("task_id", "reply_id", "seq", "session", "conversation_id", "kind",
            "text", "state", "history_state", "retry_count", "error")
    return [dict(zip(keys, r, strict=False)) for r in rows]


if __name__ == "__main__":
    sys.exit(main())
