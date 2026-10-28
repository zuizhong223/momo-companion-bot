# -*- coding: utf-8 -*-
"""P0/A3：去重只在 sent 后登记；主动两次重复必须放弃本轮。"""
import importlib.util
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(ROOT))
PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def load_butler():
    path = ROOT / "butler.py"
    spec = importlib.util.spec_from_file_location("demo_butler_p0", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    from astrbot.core.platform.sources.aiocqhttp import aiocqhttp_message_event as ev

    print("\n[1] 失败发送不登记，sent 后才登记")
    ev._PASSIVE_SEEN.clear()
    text = "今天天气真的不错"
    check(ev._passive_duplicate(text, "u1") is False, "第一次检查 false")
    check(ev._passive_duplicate(text, "u1") is False, "模拟失败后仍 false")
    ev._passive_mark_sent(text, "u1")
    check(ev._passive_duplicate(text, "u1") is True, "sent 后重复 true")

    print("\n[2] 主动话题两次重复：返回空，不返回重复文本")
    b = load_butler()
    b.log = lambda *a, **k: None  # 测试不写生产butler.log
    b.save_state = lambda state: None
    b._pick_angle = lambda state: "angle"
    b.gen_proactive = lambda instruction="", max_chars=40: "重复的话"
    state = {"recent_texts": [{"t": int(time.time()), "s": "重复的话"}]}
    out = b.gen_topic(state)
    check(out == "", "两次重复返回空", repr(out))

    print("\n[3] 主动话题不重复：正常返回并记住")
    b.gen_proactive = lambda instruction="", max_chars=40: "新的主动话题"
    out2 = b.gen_topic({})
    check(out2 == "新的主动话题", "不重复正常返回", repr(out2))

    print("\n[4] 入站协议重发：同 message_id 只处理一次")
    from astrbot.core.platform.sources.aiocqhttp import aiocqhttp_platform_adapter as ad
    ad._INBOUND_SEEN.clear()
    ev1 = {"post_type": "message", "message_id": "m1", "self_id": "bot1"}
    check(ad._inbound_duplicate(ev1) is False, "首次入站放行")
    check(ad._inbound_duplicate(dict(ev1)) is True, "同 id 重发丢弃")
    ev2 = {"post_type": "message", "message_id": "m1", "self_id": "bot2"}
    check(ad._inbound_duplicate(ev2) is False, "不同 bot self_id 不误杀")
    check(ad._inbound_duplicate({"post_type": "notice", "message_id": "n1"}) is False,
          "notice 不走消息去重")

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
