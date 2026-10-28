# -*- coding: utf-8 -*-
"""P0-1：整轮清单冲突时 send_message 必须返回 skipped，不能抛 NameError。"""
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
        return None

    def start_turn(self, **kwargs):
        return False


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    from astrbot.core.message.components import File
    from astrbot.core.message.message_event_result import MessageChain
    from astrbot.core.platform.sources.aiocqhttp import aiocqhttp_message_event as ev
    from astrbot.core.utils import send_ledger

    old = send_ledger.get_ledger
    send_ledger.get_ledger = lambda: FakeLedger()
    try:
        chain = MessageChain([File(name="a.txt", file="a.txt"),
                              File(name="b.txt", file="b.txt")])
        ct = "turn-conflict"
        res = asyncio.run(ev.AiocqhttpMessageEvent.send_message(
            bot=None, message_chain=chain, event=None, is_group=False,
            session_id="1", client_token=ct, managed_history=True,
            umo="u", history_text="ab"))
        check(isinstance(res, dict), "返回 dict", str(res))
        check(res.get("state") == "skipped"
              and res.get("error") == "turn_manifest_conflict",
              "返回 skipped/turn_manifest_conflict", str(res))
    finally:
        send_ledger.get_ledger = old

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
