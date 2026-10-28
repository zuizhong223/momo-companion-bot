# -*- coding: utf-8 -*-
"""R03/P1-3：同一 task_id 的媒体载荷必须一致。"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))
os.environ["DEMO_TEST"] = "1"

from astrbot.core.utils.send_ledger import Ledger, PayloadMismatch  # noqa: E402

DB = Path(tempfile.mkdtemp(prefix="yy_media_")) / "l.db"
PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    led = Ledger(DB)
    tid = led.new_task(session="s", kind="record", text="[语音]", audio_id="a1")
    check(led.get(tid)["audio_id"] == "a1", "audio_id 落库")

    print("\n[1] 同 task 同 audio_id：幂等")
    led.new_task(session="s", kind="record", text="[语音]", audio_id="a1", task_id=tid)
    check(True, "不抛异常")

    print("\n[2] 同 task 换 audio_id：冲突")
    try:
        led.new_task(session="s", kind="record", text="[语音]", audio_id="b2", task_id=tid)
        check(False, "应抛 PayloadMismatch")
    except PayloadMismatch:
        check(True, "PayloadMismatch")

    print("\n[3] 同 task 漏传 audio_id：冲突")
    try:
        led.new_task(session="s", kind="record", text="[语音]", task_id=tid)
        check(False, "应抛 PayloadMismatch")
    except PayloadMismatch:
        check(True, "PayloadMismatch")

    print("\n[4] 新 task 无 audio_id：正常")
    tid2 = led.new_task(session="s", kind="text", text="hi")
    check(led.get(tid2)["audio_id"] is None, "text task audio_id=None")

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
