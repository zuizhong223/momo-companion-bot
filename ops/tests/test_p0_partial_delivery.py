# -*- coding: utf-8 -*-
"""P0/A1：多段回复部分成功时，只保留确认送达的部分。

H01：第一段 sent、末段 failed；delivery_text=前段。历史必须保留前段，
不能把整轮 assistant 全删掉，也不能把失败段写进去。
"""
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

    async def get_conversation(self, umo, cid):
        return None

    async def update_conversation(self, umo, cid, history=None, token_usage=None):
        self.saved = history


class FakeEvent:
    unified_msg_origin = "aiocqhttp:FriendMessage:10001"
    message_obj = SimpleNamespace(message_id="mid-1")

    def __init__(self, state, delivery_text=None):
        self.state = state
        self.delivery_text = delivery_text

    def get_extra(self, key, default=None):
        if key == "delivery_state":
            return self.state
        if key == "delivery_token":
            return "tok"
        if key == "delivery_text":
            return self.delivery_text
        return default


def save(state, delivery_text=None):
    cm = FakeCM()
    self = SimpleNamespace(conv_manager=cm)
    req = SimpleNamespace(conversation=SimpleNamespace(cid="cid", token_usage=0))
    msgs = [
        Message(role="user", content=[TextPart(text="在干嘛")]),
        Message(role="assistant", content=[TextPart(text="甲")]),
        Message(role="assistant", content=[TextPart(text="乙")]),
    ]
    asyncio.run(InternalAgentSubStage._save_to_history(
        self, FakeEvent(state, delivery_text), req,
        LLMResponse(role="assistant", completion_text="甲 乙"), msgs, None))
    return cm.saved or []


def body(m):
    content = m.get("content")
    if isinstance(content, str):
        return content
    return " ".join(p.get("text", "") for p in content if isinstance(p, dict))


def main() -> int:
    print("\n[1] failed + delivery_text=甲：保留前段，丢掉乙")
    hist = save("failed", "甲")
    check([m.get("role") for m in hist] == ["user", "assistant"],
          "只剩 user+assistant", str([m.get("role") for m in hist]))
    check(body(hist[-1]) == "甲", "保留已送达前段", body(hist[-1]))

    print("\n[2] failed + 无 delivery_text：仍按未送达丢弃")
    hist = save("failed", None)
    check([m.get("role") for m in hist] == ["user"],
          "只剩 user", str([m.get("role") for m in hist]))

    print("\n[3] sent + delivery_text=甲 乙：绑定完整送达文本")
    hist = save("sent", "甲 乙")
    check([m.get("role") for m in hist] == ["user", "assistant"],
          "user+assistant", str([m.get("role") for m in hist]))
    check(body(hist[-1]) == "甲 乙", "绑定完整送达文本", body(hist[-1]))

    print("\n[4] skipped + delivery_text=甲：前段已送达，仍保留")
    hist = save("skipped", "甲")
    check([m.get("role") for m in hist] == ["user", "assistant"],
          "skipped 仍保留前段", str([m.get("role") for m in hist]))
    check(body(hist[-1]) == "甲", "保留甲", body(hist[-1]))

    print("\n[5] skipped + 无 delivery_text：全部丢弃")
    hist = save("skipped", None)
    check([m.get("role") for m in hist] == ["user"],
          "skipped 无送达证据只留 user", str([m.get("role") for m in hist]))

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
