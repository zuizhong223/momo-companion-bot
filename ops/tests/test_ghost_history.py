# -*- coding: utf-8 -*-
"""A05 幽灵历史：被动回复发送失败时，assistant 不能写进长期历史。"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

from astrbot.core.agent.message import Message, TextPart  # noqa: E402
from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import (  # noqa: E402
    InternalAgentSubStage,
)
from astrbot.core.provider.entities import LLMResponse  # noqa: E402

PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


class FakeCM:
    def __init__(self):
        self.saved = None

    async def update_conversation(self, umo, cid, history=None, token_usage=None):
        self.saved = history


class FakeEvent:
    unified_msg_origin = "aiocqhttp:FriendMessage:10001"

    def __init__(self, state):
        self.state = state

    def get_extra(self, key, default=None):
        if key == "delivery_state":
            return self.state
        if key == "delivery_token":
            return "tok"
        return default


def run(state):
    cm = FakeCM()
    self = SimpleNamespace(conv_manager=cm)
    req = SimpleNamespace(conversation=SimpleNamespace(cid="cid", token_usage=0))
    msgs = [
        Message(role="user", content=[TextPart(text="在干嘛")]),
        Message(role="assistant", content=[TextPart(text="在练习（")]),
    ]
    asyncio.run(InternalAgentSubStage._save_to_history(
        self, FakeEvent(state), req,
        LLMResponse(role="assistant", completion_text="在练习（"), msgs, None))
    return [m.get("role") for m in (cm.saved or [])]


def main() -> int:
    print("\n[1] delivery_state=failed：丢弃 assistant")
    roles = run("failed")
    check(roles == ["user"], "只剩 user", str(roles))

    print("\n[2] delivery_state=unknown：丢弃 assistant")
    roles = run("unknown")
    check(roles == ["user"], "只剩 user", str(roles))

    print("\n[3] delivery_state=sent：保留 assistant")
    roles = run("sent")
    check(roles == ["user", "assistant"], "user+assistant", str(roles))

    print("\n[4] delivery_state=None + delivery_token：按未送达处理（R04）")
    roles = run(None)
    check(roles == ["user"], "缺送达证据不写 assistant", str(roles))

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
