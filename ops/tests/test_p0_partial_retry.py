# -*- coding: utf-8 -*-
"""P0-2：多段重试时 seq0 已 sent，不能直接返回整轮 sent；必须补 seq1。"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))
PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


class FakeLedger:
    def get(self, task_id):
        if task_id == "tok":
            return {"state": "sent", "task_id": "tok"}
        return None

    def prepare_turn_commit(self, reply_id):
        return {"status": "partial", "task_id": None}

    def start_turn(self, **kwargs):
        return True


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    from astrbot.core.message.components import File
    from astrbot.core.message.message_event_result import MessageChain
    from astrbot.core.platform.sources.aiocqhttp import aiocqhttp_message_event as ev
    from astrbot.core.utils import send_ledger

    calls = []

    async def fake_tracked(*args, **kwargs):
        calls.append(kwargs.get("seq"))
        return {"state": "sent", "task_id": f"tok:{kwargs.get('seq')}",
                "claimed": True, "resent": False}

    async def fake_from_segment(seg):
        return {"type": "file"}

    old_get = send_ledger.get_ledger
    old_tracked = ev.AiocqhttpMessageEvent._tracked_send
    old_from = ev.AiocqhttpMessageEvent._from_segment_to_dict
    send_ledger.get_ledger = lambda: FakeLedger()
    ev.AiocqhttpMessageEvent._tracked_send = staticmethod(fake_tracked)
    ev.AiocqhttpMessageEvent._from_segment_to_dict = staticmethod(fake_from_segment)
    try:
        chain = MessageChain([File(name="a.txt", file="a.txt"),
                              File(name="b.txt", file="b.txt")])
        res = asyncio.run(ev.AiocqhttpMessageEvent.send_message(
            bot=None, message_chain=chain, event=None, is_group=False,
            session_id="1", client_token="tok", managed_history=True,
            umo="u", history_text="ab"))
        check(isinstance(res, dict) and res.get("state") == "sent",
              "整轮返回 sent", str(res))
        check(calls == [1], "只补发缺失的 seq1", str(calls))
    finally:
        send_ledger.get_ledger = old_get
        ev.AiocqhttpMessageEvent._tracked_send = old_tracked
        ev.AiocqhttpMessageEvent._from_segment_to_dict = old_from

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
