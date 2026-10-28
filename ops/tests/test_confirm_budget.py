# -*- coding: utf-8 -*-
"""检查确认句式预算：同会话 6 小时只留一次「刚说过了/你自己说的」。"""
import os
import sys
import tempfile
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="yy_confirm_")) / "state.json"
os.environ["DEMO_BRACKET_STATE"] = str(tmp)
CORE = Path(r"D:\demo\instances\11111111-1111-4111-8111-111111111111\core")
sys.path.insert(0, str(CORE))

from astrbot.core.utils.outbound_gate import CONFIRM_ANY_RE, apply_gate, commit_style  # noqa: E402
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import _duplicate_fallback  # noqa: E402

ok = True


def check(cond, msg, extra=""):
    global ok
    print(("OK   " if cond else "FAIL ") + msg + (f" | {extra}" if extra else ""))
    ok = ok and bool(cond)


def gate(text, sess):
    return apply_gate(text, sess, dedupe=False, record=False,
                      mode="banter", quirk=False)


first, _ = gate("数学作业啊 刚说过了", "p:test")
commit_style(first, "p:test", "banter")
check("刚说过了" in first, "第一次保留确认句")

second, acts = gate("数学作业啊 刚说过了", "p:test")
check("刚说过了" not in second, "第二次删掉确认句", repr(second))
check("数学作业啊" in second, "事实部分保留", repr(second))
check(any("确认句式" in a for a in acts), "有动作记录", str(acts))

only, _ = gate("刚说过了", "p:test")
check(only == "", "只剩确认句时整条丢弃", repr(only))

other, _ = gate("面条啊 自己说的", "p:other")
check("自己说的" in other, "其他会话不受影响（含自己说的）", repr(other))

for mode in ("banter", "comfort", "serious"):
    fb = _duplicate_fallback(mode)
    check(bool(fb) and not CONFIRM_ANY_RE.search(fb), f"{mode} 兜底不是确认句", repr(fb))
    a, _ = gate(fb, "p:fb_" + mode)
    commit_style(a, "p:fb_" + mode, mode)
    b, _ = gate(fb, "p:fb_" + mode)
    check(bool(b), f"{mode} 第二次兜底不静默", repr(b))

print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
