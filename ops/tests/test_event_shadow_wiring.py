# -*- coding: utf-8 -*-
"""事件 shadow 接线：只写 yy_event + 日志，不把事件塞进 prompt。"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

TMP = Path(tempfile.mkdtemp(prefix="yy_evshadow_"))
DB = TMP / "ev.db"
os.environ["DEMO_TEST"] = "1"
os.environ["DEMO_DB"] = str(DB)
os.environ["DEMO_LEDGER_DB"] = str(DB)
os.environ["DEMO_EVENTS"] = "1"
os.environ["DEMO_EVENTS_SHADOW"] = "1"
os.environ["DEMO_LIFE"] = "0"

from astrbot.core.astr_main_agent import _append_system_reminders  # noqa: E402
from astrbot.core.utils import event_memory as ev  # noqa: E402

UMO = "aiocqhttp:FriendMessage:10001"


class E:
    message_str = "今天考试考完了"
    unified_msg_origin = UMO
    message_obj = SimpleNamespace(message_id="ev-msg-1")

    def get_extra(self, *_a, **_k):
        return None


class R:
    def __init__(self):
        self.extra_user_content_parts = []


req = R()
_append_system_reminders(E(), req, {}, None)
text = "\n".join(getattr(p, "text", "") for p in req.extra_user_content_parts)
rows = ev.recent(UMO, 5)
ok = True


def check(cond, msg):
    global ok
    print(("OK   " if cond else "FAIL ") + msg)
    ok = ok and bool(cond)


check(bool(rows) and "考试" in rows[0]["text"], "事件已写入")
check("最近发生的事" not in text, "shadow 不注入 prompt")
check("机器人以前遇到类似的话" in text, "风格检索仍在（不受 shadow 影响）")
print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
