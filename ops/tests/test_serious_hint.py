# -*- coding: utf-8 -*-
"""认真话题必须有“至少 20 字 + 自己的判断”的硬提醒。"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

TMP = Path(tempfile.mkdtemp(prefix="yy_serious_"))
os.environ["DEMO_TEST"] = "1"
os.environ["DEMO_DB"] = str(TMP / "s.db")
os.environ["DEMO_LEDGER_DB"] = str(TMP / "s.db")
os.environ["DEMO_LIFE"] = "0"

from astrbot.core.astr_main_agent import _append_system_reminders  # noqa: E402

UMO = "aiocqhttp:FriendMessage:10001"


class E:
    unified_msg_origin = UMO
    message_obj = SimpleNamespace(message_id="serious-1")

    def __init__(self, text):
        self.message_str = text

    def get_extra(self, *_a, **_k):
        return None


class R:
    def __init__(self):
        self.extra_user_content_parts = []


def run(text):
    req = R()
    _append_system_reminders(E(text), req, {}, None)
    return "\n".join(getattr(p, "text", "") for p in req.extra_user_content_parts)


ok = True


def check(cond, msg):
    global ok
    print(("OK   " if cond else "FAIL ") + msg)
    ok = ok and bool(cond)


serious = run("我今天被同事抢功了 因为报表做错了 你说我该怎么办")
check("这是认真话题" in serious, "认真问题有硬提醒")
control = run("今天吃什么")
check("这是认真话题" not in control, "普通聊天不误触发")
print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
