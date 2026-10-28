# -*- coding: utf-8 -*-
"""A04 回归：管家只把 sent 当成功；处理中只查询，不重发。"""
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "instances/11111111-1111-4111-8111-111111111111/core"))

spec = importlib.util.spec_from_file_location("demo_steward", ROOT / "butler.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.log = lambda *a, **k: None  # 测试不写生产butler.log

PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def run(responses, status="sent"):
    calls = {"n": 0}

    def fake_http(*_a, **_kw):
        i = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return responses[i]

    mod.http_json = fake_http
    mod.get_task_status = lambda _t: status
    mod.time.sleep = lambda _s: None
    mod.log = lambda *_a, **_k: None
    mod._log_unknown = lambda *_a, **_k: None
    return mod.send_parts([{"type": "plain", "text": "x"}], retries=3,
                          client_token="state-test"), calls["n"]


def main() -> int:
    print("\n[1] sent 才算成功")
    (ok, st), n = run([{"status": "ok", "data": {"state": "sent"}}])
    check(ok is True and st == "sent", "sent→成功", f"{ok},{st},n={n}")

    print("\n[2] sending 只查询：查到 sent 就算成功")
    (ok, st), n = run([{"status": "ok", "data": {"state": "sending"}}], status="sent")
    check(ok is True and st == "sent", "sending→查询到 sent", f"{ok},{st},n={n}")

    print("\n[3] sending 一直处理中：不重发、不 fallback")
    (ok, st), n = run([{"status": "ok", "data": {"state": "sending"}}], status="sending")
    check(ok is False and st == "in_progress", "sending→in_progress", f"{ok},{st},n={n}")
    check(n == 1, "只调用一次发送", f"n={n}")

    print("\n[4] dropped 不再发")
    (ok, st), n = run([{"status": "ok", "data": {"state": "dropped"}}])
    check(ok is False and st == "dropped", "dropped→失败", f"{ok},{st},n={n}")
    check(n == 1, "只调用一次", f"n={n}")

    print("\n[5] 缺失 state 不再默认 sent")
    (ok, st), n = run([{"status": "ok", "data": {}}])
    check(ok is False and st == "unknown", "无 state→unknown", f"{ok},{st},n={n}")

    print("\n[6] failed 可重试，耗尽后 failed")
    (ok, st), n = run([{"status": "ok", "data": {"state": "failed"}}])
    check(ok is False and st == "failed", "failed→耗尽", f"{ok},{st},n={n}")
    check(n == 3, "重试 3 次", f"n={n}")

    print("\n[7] state 文件带 BOM 也能读（否则重启会立即发消息）")
    import json
    import tempfile
    from pathlib import Path

    p = Path(tempfile.mkdtemp()) / "state.json"
    p.write_text('\ufeff{"last_sent": 123, "last_kind": "voice"}', encoding="utf-8")
    mod.STATE_FILE = str(p)
    st = mod.load_state()
    check(st.get("last_sent") == 123, "BOM 不影响 load_state", str(st))

    print("\n[8] TTS 实际念稿：去括号、补句号；历史应记这一版")
    check(mod._speech_text("晚安（") == "晚安。", "去尾括号补句号",
          mod._speech_text("晚安（"))
    s = mod._speech_text("好耶（ 今天练习到三点")
    check("（" not in s and s.endswith("。"), "无括号、有句号", s)
    check(mod._speech_text("哈哈哈哈哈") == "哈哈哈。", "长笑压三声（保持中文）",
          mod._speech_text("哈哈哈哈哈"))

    print("\n[9] B站 412：没有旧素材时不分享")
    import json
    import tempfile
    import urllib.error

    ext = Path(tempfile.mkdtemp()) / "external_seen.jsonl"
    mod.EXTERNAL_LOG = str(ext)
    mod.http_json = lambda *_a, **_k: (_ for _ in ()).throw(
        urllib.error.HTTPError("https://api.bilibili.com", 412, "Precondition Failed", None, None))
    check(mod.bili_share() is False, "412→返回 False，不抛异常")

    print("\n[10] B站 412：有来源旧素材可降级，不造假")
    ext.write_text(json.dumps({
        "kind": "bilibili", "bvid": "BVold", "url": "https://b23.tv/old",
        "title": "旧标题", "fetched_at": mod.datetime.now().isoformat(timespec="seconds"),
        "read_scope": "title", "shared": True,
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    calls = []
    mod.send_parts = lambda parts, **kw: (calls.append((parts, kw)) or (True, "sent"))
    check(mod._share_stale_bili("412") is True, "旧素材降级成功")
    check(calls and "b23.tv/old" in calls[0][0][0]["text"], "文本含来源 URL",
          str(calls[0][0][0]["text"])[:60])

    ext.write_text(json.dumps({
        "url": "https://b23.tv/ancient", "title": "很旧的",
        "fetched_at": "2020-01-01T00:00:00", "shared": True,
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    check(mod._last_external_source() is None, "超过 7 天不降级")

    print("\n[11] P3-4：同源失败不回退本地日常，直接跳过")
    check(mod.gen_topic_fallback() == "", "fallback 返回空")
    orig = mod.gen_proactive
    mod.gen_proactive = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        check(mod.gen_topic() == "", "文字主动跳过")
        check(mod.gen_voice_text() == "", "语音主动跳过")
    finally:
        mod.gen_proactive = orig

    print("\n[12] L5：生活层静默只拦真实主动，影子不拦")
    from astrbot.core.utils import life_state as lsx
    oe, os_, oa = lsx.enabled, lsx.shadow, lsx.proactive_allowed
    try:
        lsx.proactive_allowed = lambda *a, **k: False
        lsx.enabled = lambda: False
        check(mod._life_gate_blocks() is False, "默认关：不拦")
        lsx.enabled = lambda: True
        lsx.shadow = lambda: False
        check(mod._life_gate_blocks() is True, "启用+静默：拦")
        lsx.shadow = lambda: True
        check(mod._life_gate_blocks() is False, "影子：不拦")
    finally:
        lsx.enabled, lsx.shadow, lsx.proactive_allowed = oe, os_, oa

    print("\n[13] L6：练习提醒只在开关打开且事件未 told 时发一次")
    o_adv, o_ev, o_ra, o_mt = lsx.advance, lsx.event_by_id, lsx.reminder_allowed, lsx.mark_event_told
    o_send, o_rec, o_ura = mod.send_text, mod._remember_send, mod.user_recently_active
    sent, told = [], []
    try:
        os.environ["DEMO_LIFE_REMINDER"] = "1"
        lsx.enabled = lambda: True
        lsx.shadow = lambda: False
        lsx.proactive_allowed = lambda *a, **k: True
        lsx.advance = lambda *a, **k: {"title": "练习", "event_id": "e1", "now": None}
        lsx.event_by_id = lambda *a, **k: {"told_at": None}
        lsx.reminder_allowed = lambda *a, **k: True
        lsx.mark_event_told = lambda eid, **k: told.append(eid) or True
        mod.user_recently_active = lambda minutes=10: False
        mod.send_text = lambda text, **kw: (sent.append((text, kw)) or (True, "sent"))
        mod._remember_send = lambda *a, **k: None
        mod.act({})
        check(len(sent) == 1 and "练习" in sent[0][0], "发出一次练习提醒", str(sent))
        check(told == ["e1"], "发送后标记 told", str(told))
    finally:
        os.environ.pop("DEMO_LIFE_REMINDER", None)
        lsx.advance, lsx.event_by_id, lsx.reminder_allowed, lsx.mark_event_told = o_adv, o_ev, o_ra, o_mt
        mod.send_text, mod._remember_send, mod.user_recently_active = o_send, o_rec, o_ura

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
