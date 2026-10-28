# -*- coding: utf-8 -*-
"""历史 dump 兼容 dict text part：R04 绑定把 part 换成 dict 时不能崩。"""
import sys
from pathlib import Path

CORE = Path(r"D:\demo\instances\11111111-1111-4111-8111-111111111111\core")
sys.path.insert(0, str(CORE))

from astrbot.core.agent.message import (  # noqa: E402
    Message,
    TextPart,
    dump_messages_with_checkpoints,
)

m = Message(role="assistant", content=[TextPart(text="原稿")])
m.content = [{"type": "text", "text": "实际送达"}]  # 模拟 bind_delivered_text
out = dump_messages_with_checkpoints([m])
ok = (out[0]["content"][0]["text"] == "实际送达")
print(out)
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
