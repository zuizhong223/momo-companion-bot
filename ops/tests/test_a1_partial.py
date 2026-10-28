# -*- coding: utf-8 -*-
"""P0/A1：部分送达时，历史保留已送达片段，不因末段失败全删。"""
import sys
from pathlib import Path

CORE = Path(r"D:\demo\instances\11111111-1111-4111-8111-111111111111\core")
sys.path.insert(0, str(CORE))

from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import (  # noqa: E402
    rebuild_delivered_history,
)


class M:
    def __init__(self, role, content="", tool_calls=None):
        self.role = role
        self.content = content
        self.tool_calls = tool_calls


msgs = [
    M("user", "在吗"),
    M("assistant", "模型草稿一"),
    M("assistant", "", tool_calls=[{"id": "t1"}]),
    M("assistant", "模型草稿二"),
]
kept, removed = rebuild_delivered_history(msgs, 1, "第一段 已送达")
ok = True


def check(cond, msg):
    global ok
    print(("OK   " if cond else "FAIL ") + msg)
    ok = ok and bool(cond)


check(removed == 2, "删掉 2 条正文草稿")
check([m.role for m in kept] == ["user", "assistant", "assistant"], "结构保留")
check(kept[0].content == "在吗", "user 保留")
check(kept[1].tool_calls == [{"id": "t1"}], "tool_calls 保留")
check(kept[-1].content[0].text == "第一段 已送达", "已送达文本入历史")
check(all("模型草稿" not in str(m.content) for m in kept), "未送达草稿清掉")
print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
