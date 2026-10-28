# -*- coding: utf-8 -*-
"""P2：最小事件记忆（机器人记得"最近发生了什么"）。

和 yy_fact2 的分工：
  - yy_fact2：稳定事实（叫用户、学过乐器、买过乐器）；
  - yy_event_memory：有时间感的事（今天考试、昨天吵架、约好周末玩）。

默认关闭（DEMO_EVENTS=0）；打开后只做保守抽取：
  - 有"今天/昨天/刚才/去了/买了/考试/生日/吵架/道歉/约好"等事件词；
  - 6~120 字，不是纯提问。
写入不调用模型，避免把猜测写成事实。
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

DEFAULT_DB = Path(
    os.environ.get("DEMO_DB")
    or r"D:/demo\instances\11111111-1111-4111-8111-111111111111\core\data\data_v4.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS yy_event_memory (
  event_id TEXT PRIMARY KEY,
  umo TEXT NOT NULL,
  ts TEXT NOT NULL,
  text TEXT NOT NULL,
  msg_id TEXT,
  kind TEXT NOT NULL DEFAULT 'event'
)
"""
INDEX = "CREATE INDEX IF NOT EXISTS idx_yy_event_memory_umo ON yy_event_memory(umo, ts)"

CUE_RE = re.compile(
    r"今天|昨天|前天|刚刚|刚才|这周|上周|周末|去了|吃了|买了|看完|考试|生日|"
    r"吵架|和好|道歉|答应|约好|见面|回家|上课|上班|生病|手术|出院|下雨|下雪"
)


def enabled() -> bool:
    return os.environ.get("DEMO_EVENTS", "0").strip().lower() in ("1", "true", "on", "yes")


def shadow() -> bool:
    """只记日志、不改 prompt，用于 24h 抽查误记。"""
    return os.environ.get("DEMO_EVENTS_SHADOW", "0").strip().lower() in ("1", "true", "on", "yes")


def _db() -> Path:
    return Path(os.environ.get("DEMO_DB")
                or os.environ.get("DEMO_LEDGER_DB") or DEFAULT_DB)


def _ensure(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    con.execute(INDEX)


def capture_cue(text: str) -> bool:
    t = (text or "").strip()
    if not (6 <= len(t) <= 120):
        return False
    if t.endswith(("？", "?")) and not any(k in t for k in ("今天", "昨天", "刚才")):
        return False
    return bool(CUE_RE.search(t))


def remember(umo: str, text: str, msg_id: str | None = None,
             kind: str = "event") -> bool:
    t = (text or "").strip()
    if not umo or not capture_cue(t):
        return False
    eid = hashlib.sha1(f"{umo}|{msg_id or ''}|{t}".encode()).hexdigest()[:20]
    con = sqlite3.connect(str(_db()), timeout=10)
    try:
        _ensure(con)
        cur = con.execute(
            "INSERT OR IGNORE INTO yy_event_memory(event_id,umo,ts,text,msg_id,kind)"
            " VALUES(?,?,?,?,?,?)",
            (eid, umo, datetime.now().strftime("%Y-%m-%d %H:%M"), t, msg_id, kind))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def recent(umo: str, k: int = 3) -> list[dict]:
    db = _db()
    if not db.exists():
        return []
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        rows = con.execute(
            "SELECT ts,text,kind FROM yy_event_memory WHERE umo=? ORDER BY ts DESC LIMIT ?",
            (umo, k)).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()
    return [{"ts": r[0], "text": r[1], "kind": r[2]} for r in rows]


def format_for_prompt(umo: str, k: int = 3) -> str:
    rows = recent(umo, k)
    if not rows:
        return ""
    lines = ["【最近发生的事（机器人记得的，别编别的）】"]
    for r in reversed(rows):
        lines.append(f"- {r['ts']} {r['text']}")
    return "\n".join(lines)
