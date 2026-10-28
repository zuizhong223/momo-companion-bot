# -*- coding: utf-8 -*-
"""验证风格检索真的进了 passive 的 system reminder（不调模型）。"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

CORE = Path(r"D:\demo\instances\11111111-1111-4111-8111-111111111111\core")
sys.path.insert(0, str(CORE))
# N-1a：这个测试会走生产代码路径，必须把库指到临时文件，绝不碰正式库
os.environ.setdefault("DEMO_LEDGER_DB",
                      str(Path(tempfile.mkdtemp(prefix="yy_stylewire_")) / "wire.db"))

from astrbot.core.astr_main_agent import _append_system_reminders  # noqa: E402


class E:
    message_str = "在干嘛"
    unified_msg_origin = "aiocqhttp:FriendMessage:100000000"
    message_obj = SimpleNamespace(message_id=None, group_id=None,
                                  sender=SimpleNamespace(user_id=1, nickname="x"))


class R:
    def __init__(self):
        self.extra_user_content_parts = []


req = R()
_append_system_reminders(E(), req, {}, None)
text = "\n".join(getattr(p, "text", "") for p in req.extra_user_content_parts)
print(text[:800])
ok = "机器人以前遇到类似的话这样回" in text and "在干嘛" in text
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
