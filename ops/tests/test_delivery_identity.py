# -*- coding: utf-8 -*-
"""P0-A/P0-B 回归：送达身份、恢复目标、终态守卫、部分段、重试计时。

不连接 QQ、不调用模型；全部在临时合成库上跑。
"""
import asyncio
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

TMP = Path(tempfile.mkdtemp(prefix="yy_delivery_"))
DB = TMP / "delivery.db"
os.environ["DEMO_TEST"] = "1"
os.environ["DEMO_LEDGER_DB"] = str(DB)

UMO = "aiocqhttp:FriendMessage:10001"
CID = "cid-delivery"

PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def make_db() -> None:
    con = sqlite3.connect(DB)
    con.execute(
        "create table conversations(conversation_id text primary key,user_id text,"
        "content text,updated_at text)")
    con.execute("insert into conversations values(?,?,?,?)",
                (CID, UMO, "[]", "2026-10-25 00:00:00"))
    con.commit()
    con.close()


def hist() -> list:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    row = con.execute("select content from conversations where conversation_id=?",
                      (CID,)).fetchone()
    con.close()
    return json.loads(row[0] or "[]") if row else []


async def accept():
    return {"message_id": "synthetic-mid"}


def main() -> int:
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
        AiocqhttpMessageEvent,
    )
    from astrbot.core.utils.send_ledger import (
        H_NEEDS_TARGET,
        H_PARTIAL,
        Ledger,
    )

    make_db()
    ledger = Ledger(DB)

    print("\n[1] _tracked_send 落库目标；sent 后恢复补历史")
    before = len(hist())
    res = asyncio.run(AiocqhttpMessageEvent._tracked_send(
        accept, session_key="p:10001", text="synthetic body", kind="text",
        reply_id="reply-1", seq=0, client_token="crash", managed_history=True,
        umo=UMO, history_text="synthetic body"))
    check(res.get("state") == "sent", "返回 sent", str(res))
    task = ledger.get("crash")
    check(task["conversation_id"] == CID, "发送前保存 conversation_id", str(task["conversation_id"]))
    check(task["umo"] == UMO, "发送前保存 umo")
    check(task["history_text"] == "synthetic body", "发送前保存 history_text")
    check(task["history_state"] == "pending", "历史仍 pending（崩溃窗口）")
    rec = ledger.recover()
    check(rec["history_committed"] == 1, "恢复补写 1 条", str(rec))
    check(len(hist()) == before + 1, f"历史 +1（{before}->{len(hist())}）")
    check(ledger.get("crash")["history_state"] == "committed", "标记 committed")
    rec2 = ledger.recover()
    check(rec2["history_committed"] == 0, "再次恢复不重复")
    check(len(hist()) == before + 1, "历史没有重复追加")

    print("\n[2] 缺目标会话：保留 pending，标 needs_target，不静默结案")
    tid = ledger.new_task(session="p:10001", kind="text", text="no target",
                          managed_history=True)
    ledger.claim(tid)
    ledger.finish_ok(tid)
    n0 = len(hist())
    check(ledger.commit_history(tid) == "needs_target", "返回 needs_target")
    check(ledger.get(tid)["history_state"] == H_NEEDS_TARGET, "history_state=needs_target")
    check(len(hist()) == n0, "历史不变")

    print("\n[3] 兄弟段未全 sent：只标 partial，不写整段历史")
    r = "reply-partial"
    t0 = ledger.new_task(session="p:10001", kind="file", text="seg0",
                         reply_id=r, seq=0, task_id="reply-partial:0",
                         conversation_id=CID, umo=UMO)
    ledger.claim(t0)
    ledger.finish_err(t0, error="definite reject")
    t1 = ledger.new_task(session="p:10001", kind="file", text="seg1",
                         reply_id=r, seq=1, task_id="reply-partial:1",
                         conversation_id=CID, umo=UMO)
    ledger.claim(t1)
    ledger.finish_ok(t1)
    ledger.attach_history(t1, umo=UMO, history_text="full reply")
    n1 = len(hist())
    check(ledger.commit_history(t1) == "partial", "返回 partial")
    check(ledger.get(t1)["history_state"] == H_PARTIAL, "history_state=partial")
    check(len(hist()) == n1, "历史不变")

    print("\n[4] 旧任务新尝试不被立刻判 stale")
    old = ledger.new_task(session="p:10001", kind="text", text="old retry")
    con = sqlite3.connect(DB)
    con.execute("update yy_send_task set created_at='2020-01-01 00:00:00.000000'"
                " where task_id=?", (old,))
    con.commit()
    con.close()
    check(ledger.claim(old), "认领旧任务")
    check(old not in [x["task_id"] for x in ledger.stale_sending(120)],
          "按本次 attempt 计时，不 stale")
    con = sqlite3.connect(DB)
    con.execute("update yy_send_attempt set started_at='2020-01-01 00:00:00.000000'"
                " where task_id=?", (old,))
    con.commit()
    con.close()
    check(old in [x["task_id"] for x in ledger.stale_sending(120)],
          "attempt 也旧了才 stale")

    print("\n[5] unknown 不被迟到的成功覆盖")
    late = ledger.new_task(session="p:10001", kind="text", text="late")
    ledger.claim(late)
    ledger.mark_unknown(late, "recovery")
    check(ledger.finish_ok(late) is False, "finish_ok 拒绝 unknown")
    check(ledger.get(late)["state"] == "unknown", "仍是 unknown")

    print("\n[6] 多段共用业务 token：各段有自己的 task_id，不互相吞掉")
    a = asyncio.run(AiocqhttpMessageEvent._tracked_send(
        accept, session_key="p:10001", text="first", kind="segment",
        reply_id="multi", seq=0, client_token="multi",
        managed_history=False, umo=UMO))
    b = asyncio.run(AiocqhttpMessageEvent._tracked_send(
        accept, session_key="p:10001", text="second", kind="segment",
        reply_id="multi", seq=1, client_token="multi",
        managed_history=False, umo=UMO))
    check(a.get("task_id") == "multi", "seq0 task=multi", str(a))
    check(b.get("task_id") == "multi:1", "seq1 task=multi:1", str(b))
    check(a.get("state") == "sent" and b.get("state") == "sent", "两段都 sent")
    # R03：同 task_id 换正文必须拒绝，不能当成“重试成功”
    again = asyncio.run(AiocqhttpMessageEvent._tracked_send(
        accept, session_key="p:10001", text="first again", kind="segment",
        reply_id="multi", seq=0, client_token="multi",
        managed_history=False, umo=UMO))
    check(again.get("state") == "skipped"
          and again.get("error") == "payload_mismatch",
          "换正文被拒绝", str(again))
    # 同一正文重入：幂等返回旧任务，不重发
    same = asyncio.run(AiocqhttpMessageEvent._tracked_send(
        accept, session_key="p:10001", text="first", kind="segment",
        reply_id="multi", seq=0, client_token="multi",
        managed_history=False, umo=UMO))
    check(same.get("claimed") is False and same.get("state") == "sent",
          "同正文重入不重发", str(same))

    print("\n[7] send_message 单链：发送后提交历史，同 token 重试不重发")
    from astrbot.core.message.components import Plain
    from astrbot.core.message.message_event_result import MessageChain

    class FakeBot:
        def __init__(self):
            self.calls = []

        async def send_private_msg(self, user_id, message, **kw):
            self.calls.append((user_id, message))
            return {"message_id": "fake-mid"}

    fake = FakeBot()
    n2 = len(hist())
    res = asyncio.run(AiocqhttpMessageEvent.send_message(
        bot=fake, message_chain=MessageChain([Plain("hello")]), event=None,
        is_group=False, session_id="10001", client_token="sm1",
        managed_history=True, umo=UMO, history_text="hello"))
    check(fake.calls and len(fake.calls) == 1, "只调用一次发送", str(len(fake.calls)))
    check(res.get("state") == "sent", "返回 sent", str(res))
    check(len(hist()) == n2 + 1, f"历史 +1（{n2}->{len(hist())}）")
    check(ledger.get("sm1")["history_state"] == "committed", "历史已提交")
    res2 = asyncio.run(AiocqhttpMessageEvent.send_message(
        bot=fake, message_chain=MessageChain([Plain("hello")]), event=None,
        is_group=False, session_id="10001", client_token="sm1",
        managed_history=True, umo=UMO, history_text="hello"))
    check(len(fake.calls) == 1, "同 token 重试不重发", str(len(fake.calls)))
    check(res2.get("state") == "sent" and res2.get("claimed") is False, "返回 sent/claimed=False")
    check(len(hist()) == n2 + 1, "历史不重复")

    print("\n[8] 括号预算：清洗十次只记一次，发送后才提交")
    from astrbot.core.utils import outbound_gate as gate

    gate.reset("a08")
    t1, _ = gate.apply_gate("好耶（", "a08", dedupe=False, record=False)
    for _ in range(9):
        t1, _ = gate.apply_gate(t1, "a08", dedupe=False, record=False)
    check(len(gate._BRACKET_HIST.get("a08:banter") or []) == 0, "纯清洗不写预算")
    gate.commit_style(t1, "a08")
    check(len(gate._BRACKET_HIST.get("a08:banter") or []) == 1, "确认发送后只记 1 条")

    print("\n[9] 适配器能力判定：不靠 TypeError 试错")
    from astrbot.dashboard.services.open_api_service import _accepts_delivery_params

    def with_token(session, chain, client_token=None):
        return None

    def with_kwargs(session, chain, **kw):
        return None

    def without(session, chain):
        return None

    check(_accepts_delivery_params(with_token) is True, "显式参数→支持")
    check(_accepts_delivery_params(with_kwargs) is True, "**kwargs→支持")
    check(_accepts_delivery_params(without) is False, "无参数→不支持")

    print("\n[10] 明确失败码：1200/1400 可重试，未知码仍 unknown")
    from aiocqhttp.exceptions import ActionFailed
    from astrbot.core.utils.send_ledger import classify_exception

    for rc, want in ((1200, "definite"), (1400, "definite"), (99, "unknown")):
        ex = ActionFailed({"retcode": rc, "status": "failed", "message": "x"})
        check(classify_exception(ex) == want, f"retcode {rc} -> {want}",
              classify_exception(ex))

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
