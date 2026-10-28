# -*- coding: utf-8 -*-
"""R3：语音/多段消息的历史文本要用"实际说的内容"，不是 [语音] 占位。

覆盖两点：
  1. `_task_text` 对 Record 段返回占位符（台账里看得懂"这是语音"）；
  2. `commit_history(text=...)` 优先用调用方给的文本（语音稿），而不是占位符。
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _dbcopy import snapshot  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="yy_voice_"))
DB = TMP / "data_v4.db"
snapshot(CORE / "data/data_v4.db", DB)
os.environ["DEMO_LEDGER_DB"] = str(DB)
os.environ["DEMO_TEST"] = "1"

from astrbot.core.message.components import File, Plain, Record  # noqa: E402
from astrbot.core.message.message_event_result import MessageChain  # noqa: E402
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (  # noqa: E402
    _task_kind, _task_text,
)
from astrbot.core.utils.send_ledger import Ledger  # noqa: E402

UMO = "aiocqhttp:FriendMessage:100000000"
PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def hist() -> list:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    row = con.execute("select content from conversations where user_id=?", (UMO,)).fetchone()
    con.close()
    return json.loads(row[0] or "[]")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    print("\n[1] _task_text：语音段是占位符，纯文本是原文")
    check(_task_text(MessageChain([Plain("晚安")])) == "晚安", "纯文本原样")
    check(_task_text(MessageChain([Record(file="x.amr")])) == "[语音]", "无稿子的语音→[语音]")
    # TTS 转出来的 Record 自带 text：台账要记机器人实际说的话，不是占位符
    said = "喜欢） 刚睡醒就听你问这个（"
    check(_task_text(MessageChain([Record(file="x.amr", text=said)])) == said,
          "TTS 语音→实际稿子")

    print("\n[2] commit_history 用调用方给的语音稿，不用占位符")
    ledger = Ledger(DB)
    before = len(hist())
    task = ledger.new_task(session=f"p:{UMO}", kind="record", text="[语音]",
                           managed_history=True, audio_id="voice-1")
    check(ledger.get(task)["audio_id"] == "voice-1", "audio_id 落库")
    check(ledger.claim(task), "认领")
    ledger.finish_ok(task, "mid-voice")
    res = ledger.commit_history(task, "然后我今天练习练到三点 手都僵了", umo=UMO)
    check(res == "committed", "提交成功", res)
    after = hist()
    bodies = [" ".join(p.get("text", "") for p in (m.get("content") or [])
                       if isinstance(p, dict))
              for m in after if m.get("role") == "assistant"]
    check(len(after) == before + 1, f"历史 +1（{before}->{len(after)}）")
    check("然后我今天练习练到三点 手都僵了" in bodies, "记的是语音稿原文")
    check("[语音]" not in bodies, "没有把占位符写进历史")

    print("\n[3] 双发：history_text 可以是两段拼起来")
    task2 = ledger.new_task(session=f"p:{UMO}", kind="text", text="文字段 + [语音]",
                            managed_history=True)
    ledger.claim(task2)
    ledger.finish_ok(task2, "mid-both")
    ledger.commit_history(task2, "文字段 然后语音里说了一长串", umo=UMO)
    bodies2 = [" ".join(p.get("text", "") for p in (m.get("content") or [])
                        if isinstance(p, dict)) for m in hist() if m.get("role") == "assistant"]
    check("文字段 然后语音里说了一长串" in bodies2, "双发记两段拼起来的文本")

    print("\n[4] kind 按实际媒体类型：语音不能记成 text")
    check(_task_kind(MessageChain([Record(file="x.amr")])) == "record", "Record→record")
    check(_task_kind(MessageChain([Plain("晚安")])) == "text", "Plain→text")
    check(_task_kind(MessageChain([Plain("晚安"), File(name="x.txt")])) == "file",
          "含 File→file")

    print("RESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
