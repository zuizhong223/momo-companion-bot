# -*- coding: utf-8 -*-
"""连续说关心你/认可你时，提醒不要每轮都用“快十二点/睡吧”回避。"""
import os
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

CORE = Path(r"D:\demo\instances\11111111-1111-4111-8111-111111111111\core")
sys.path.insert(0, str(CORE))
os.environ["DEMO_LEDGER_DB"] = str(Path(tempfile.mkdtemp(prefix="yy_aff_")) / "ledger.db")

from astrbot.core.astr_main_agent import _append_system_reminders  # noqa: E402

umo = "aiocqhttp:FriendMessage:test-" + uuid.uuid4().hex
event = SimpleNamespace(
    message_str="我真的好关心你",
    unified_msg_origin=umo,
    message_obj=SimpleNamespace(
        message_id="m1",
        sender=SimpleNamespace(user_id="1", nickname="User"),
        group_id=None,
        group=None,
    ),
)


def run():
    req = SimpleNamespace(extra_user_content_parts=[], contexts=[])
    _append_system_reminders(event, req, {}, None)
    return "\n".join(getattr(p, "text", "") for p in req.extra_user_content_parts)


first = run()
second = run()
third = run()
ok = True


def check(cond, msg):
    global ok
    print(("OK   " if cond else "FAIL ") + msg)
    ok = ok and bool(cond)


check("连续几轮在说关心你" not in first, "第一次不提醒")
check("连续几轮在说关心你" in second, "第二次提醒")
check("睡觉" in second and "我也关心你" in second, "提醒包含具体禁令")
check("连续几轮在说关心你" in third, "第三次继续提醒")
print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
