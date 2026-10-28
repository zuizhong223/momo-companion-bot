# -*- coding: utf-8 -*-
"""P0/A2：同文句不能按正文回填来源；旧记录各自身份不变，新句不继承旧来源。"""
import asyncio
import json
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


OLD = [
    {"role": "assistant", "content": [{"type": "text", "text": "今天练习了"}],
     "message_id": "m-chat", "source_type": "original_chat",
     "source_ref": "chat-1", "event_id": "e1"},
    {"role": "user", "content": [{"type": "text", "text": "后来呢"}]},
    {"role": "assistant", "content": [{"type": "text", "text": "今天练习了"}],
     "message_id": "m-virtual", "source_type": "virtual_life",
     "source_ref": "virtual-2", "event_id": "e2"},
]


class FakeCM:
    def __init__(self):
        self.saved = None

    async def get_conversation(self, umo, cid):
        return SimpleNamespace(history=json.dumps(OLD, ensure_ascii=False))

    async def update_conversation(self, umo, cid, history=None, token_usage=None):
        self.saved = history


class FakeEvent:
    unified_msg_origin = "aiocqhttp:FriendMessage:10001"
    message_obj = SimpleNamespace(message_id="new-mid")

    def __init__(self, state="sent", text="今天练习了", tokens=("tok-new",)):
        self.state = state
        self.text = text
        self.tokens = list(tokens)

    def get_extra(self, key, default=None):
        return {
            "delivery_state": self.state,
            "delivery_token": "tok-new",
            "delivery_text": self.text,
            "delivery_tokens": self.tokens,
        }.get(key, default)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    cm = FakeCM()
    self = SimpleNamespace(conv_manager=cm)
    req = SimpleNamespace(conversation=SimpleNamespace(cid="cid", token_usage=0))
    msgs = [Message.model_validate(m) for m in OLD]
    msgs.append(Message(role="user", content=[TextPart(text="新问题")]))
    msgs.append(Message(role="assistant", content=[TextPart(text="今天练习了")]))
    asyncio.run(InternalAgentSubStage._save_to_history(
        self, FakeEvent(), req,
        LLMResponse(role="assistant", completion_text="今天练习了"), msgs, None))

    saved = cm.saved or []
    assistants = [m for m in saved if m.get("role") == "assistant"]
    print("\n[1] 两条旧同文句各自保留来源")
    check(len(assistants) >= 3, "至少有旧两条 + 新一条", str(len(assistants)))
    old = assistants[:2]
    check(old[0].get("source_type") == "original_chat"
          and old[0].get("source_ref") == "chat-1",
          "旧1 来源不变", str(old[0]))
    check(old[1].get("source_type") == "virtual_life"
          and old[1].get("source_ref") == "virtual-2",
          "旧2 来源不变", str(old[1]))

    print("\n[2] 新同文句不继承旧来源")
    new = assistants[-1]
    check(new.get("content") and "今天练习了" in json.dumps(new, ensure_ascii=False),
          "新句存在", str(new))
    check(new.get("source_type") != "virtual_life"
          and new.get("source_ref") != "virtual-2",
          "新句没有继承虚拟来源", str(new))
    check(new.get("source_type") == "model_output"
          and new.get("source_ref") == "tok-new"
          and new.get("event_id") == "new-mid",
          "新句绑定当前送达任务", str(new))

    print("\n[3] 全失败：不改旧消息来源")
    cm2 = FakeCM()
    self2 = SimpleNamespace(conv_manager=cm2)
    msgs2 = [Message.model_validate(m) for m in OLD]
    msgs2.append(Message(role="user", content=[TextPart(text="新问题")]))
    msgs2.append(Message(role="assistant", content=[TextPart(text="今天练习了")]))
    asyncio.run(InternalAgentSubStage._save_to_history(
        self2, FakeEvent(state="failed", text="", tokens=()),
        req, LLMResponse(role="assistant", completion_text="今天练习了"), msgs2, None))
    saved2 = cm2.saved or []
    ass2 = [m for m in saved2 if m.get("role") == "assistant"]
    check(len(ass2) == 2, "只剩旧两条 assistant", str([a.get("source_ref") for a in ass2]))
    check(ass2[0].get("source_ref") == "chat-1"
          and ass2[1].get("source_ref") == "virtual-2",
          "旧来源未被改", str([a.get("source_ref") for a in ass2]))

    print("\n[4] 部分成功：只绑成功任务，不绑最后失败任务")
    cm3 = FakeCM()
    self3 = SimpleNamespace(conv_manager=cm3)
    msgs3 = [Message.model_validate(m) for m in OLD]
    msgs3.append(Message(role="user", content=[TextPart(text="新问题")]))
    msgs3.append(Message(role="assistant", content=[TextPart(text="甲 乙")]))
    asyncio.run(InternalAgentSubStage._save_to_history(
        self3, FakeEvent(state="failed", text="甲", tokens=("tok-ok",)),
        req, LLMResponse(role="assistant", completion_text="甲 乙"), msgs3, None))
    saved3 = cm3.saved or []
    ass3 = [m for m in saved3 if m.get("role") == "assistant"]
    new3 = ass3[-1]
    check(len(ass3) == 3, "旧两条 + 新前段", str(len(ass3)))
    check(new3.get("source_ref") == "tok-ok", "新段绑成功任务", str(new3))
    check(ass3[0].get("source_ref") == "chat-1"
          and ass3[1].get("source_ref") == "virtual-2",
          "旧来源仍不变", str([a.get("source_ref") for a in ass3]))

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
