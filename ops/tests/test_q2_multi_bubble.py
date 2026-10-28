# -*- coding: utf-8 -*-
"""Q2：多气泡 follow-up 只在 sent/无贴纸/被动时触发。"""
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

from astrbot.core.platform.sources.aiocqhttp import aiocqhttp_message_event as ev  # noqa: E402

PASS = True


def check(cond, label, extra=""):
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


class Owner:
    unified_msg_origin = "aiocqhttp:FriendMessage:1"

    def __init__(self, sticker=False):
        self.extra = {"_sticker_sent": sticker}

    def get_extra(self, k, default=None):
        return self.extra.get(k, default)

    def set_extra(self, k, v):
        self.extra[k] = v


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    os.environ.pop("DEMO_TEST", None)
    os.environ["DEMO_MULTI_BUBBLE_RATE"] = "1.0"
    calls = []

    async def fake_send(**kw):
        calls.append(kw)
        return {"state": "sent"}

    old_send, old_sleep = ev.AiocqhttpMessageEvent.send_message, ev.asyncio.sleep
    ev.AiocqhttpMessageEvent.send_message = staticmethod(fake_send)
    ev.asyncio.sleep = lambda *_a, **_k: old_sleep(0)
    try:
        asyncio.run(ev.AiocqhttpMessageEvent._send_multi_bubble(
            Owner(), object(), object(), False, "1", "tok", "banter",
            "今天真的有点累 想早点睡", True))
        check(len(calls) == 1, "补一条气泡", str(len(calls)))
        check(calls and calls[0].get("client_token") == "tok:b2", "新 token",
              str(calls[0].get("client_token") if calls else ""))

        calls.clear()
        asyncio.run(ev.AiocqhttpMessageEvent._send_multi_bubble(
            Owner(sticker=True), object(), object(), False, "1", "tok2", "banter",
            "今天真的有点累 想早点睡", True))
        check(not calls, "已有贴纸不补气泡")
    finally:
        ev.AiocqhttpMessageEvent.send_message = old_send
        ev.asyncio.sleep = old_sleep
        os.environ.pop("DEMO_MULTI_BUBBLE_RATE", None)
        os.environ["DEMO_TEST"] = "1"

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
