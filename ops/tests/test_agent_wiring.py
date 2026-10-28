# -*- coding: utf-8 -*-
"""被动注入接线：mood / facts 候选 / life_context / 冲突识别 是否真的进请求。"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

TMP = Path(tempfile.mkdtemp(prefix="yy_wire_"))
DB = TMP / "wire.db"
os.environ["DEMO_TEST"] = "1"
os.environ["DEMO_DB"] = str(DB)
os.environ["DEMO_LEDGER_DB"] = str(DB)
os.environ["DEMO_LIFE"] = "1"
os.environ["DEMO_LIFE_SHADOW"] = "0"
os.environ["DEMO_LIFE_TZ"] = "Asia/Shanghai"

from astrbot.core.astr_main_agent import _append_system_reminders  # noqa: E402
from astrbot.core.utils import life_state as _ls0  # noqa: E402

_cid = _ls0.add_commitment("aiocqhttp:FriendMessage:10001", "周末整理一份示例", db_path=DB)
# R08：planned 不进提示；只有“真实送达过”的 active 承诺才算机器人答应过
import sqlite3 as _sql  # noqa: E402

_con = _sql.connect(DB)
_con.execute("CREATE TABLE IF NOT EXISTS yy_send_task(task_id TEXT PRIMARY KEY,state TEXT)")
_con.execute("INSERT OR REPLACE INTO yy_send_task(task_id,state) VALUES('wire-task','sent')")
_con.commit()
_con.close()
_ls0.set_commitment_status(_cid, "active", delivered_task_id="wire-task", db_path=DB)

PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


class FakeEvent:
    unified_msg_origin = "aiocqhttp:FriendMessage:10001"

    def __init__(self, text="我喜欢蓝莓 你烦不烦", mid="wire-msg-1"):
        self.message_str = text
        self.message_obj = SimpleNamespace(message_id=mid)

    def get_extra(self, *_a, **_k):
        return None


def main() -> int:
    req = SimpleNamespace(extra_user_content_parts=[])
    _append_system_reminders(FakeEvent("你现在在干嘛 我喜欢蓝莓 你烦不烦"), req, {}, None)
    texts = [getattr(p, "text", "") for p in req.extra_user_content_parts]
    joined = "\n".join(texts)
    print("  injected:", joined[:200].replace("\n", " / "))
    check(any("你那边" in t for t in texts), "life_context 已注入")
    check(any("还记着" in t and "周末整理一份示例" in t for t in texts), "承诺已注入")

    from astrbot.core.utils import memory_facts as mf  # noqa: E402

    cands = mf.list_candidates(FakeEvent.unified_msg_origin, db_path=DB)
    check(any(c["obj"] == "蓝莓" for c in cands), "候选蓝莓已写入", str(cands))

    from astrbot.core.utils import life_state as ls  # noqa: E402

    conflicts = ls.list_conflicts(FakeEvent.unified_msg_origin, db_path=DB)
    check(any("烦" in c["text"] for c in conflicts), "冲突已记录", str(conflicts))

    req2 = SimpleNamespace(extra_user_content_parts=[])
    _append_system_reminders(FakeEvent("对不起 我错了", "wire-msg-2"), req2, {}, None)
    check(ls.list_conflicts(FakeEvent.unified_msg_origin, db_path=DB) == [],
          "道歉后 open 冲突清零")

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
