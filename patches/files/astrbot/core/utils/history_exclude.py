# -*- coding: utf-8 -*-
"""历史排除表（P1-5）。v2：键从"位置"换成"角色 + 内容哈希 + 第几次出现"。

为什么换：
  原来用 `(conversation_id, idx, text_sha)` —— 一旦 AstrBot 把历史重排/插入，
  idx 全错位，排除就失效（虽然 sha 校验能防止误伤，但会"该排除的没排除"）。
  现在用 `(umo, role, text_sha, occur)`：不依赖位置，只依赖"这句话在历史里出现第几次"。

  例：用户连说两句一样的「在吗」，只排除第一句 —— occur=1。

标记**不能**写进消息体：AstrBot 被动回复时整段回写，会把自定义字段冲掉（实测过）。

用法：
  from astrbot.core.utils.history_exclude import seed, filter_history, load
  seed(umo, [(idx, 原文), ...], "原因")          # 按位置标（内部换算成 role+sha+occur）
  filter_history(messages, umo)                  # 载入历史时过滤
"""
from __future__ import annotations

import hashlib
from astrbot.core.utils.paths import db_path
import os
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

DEFAULT_DB = Path(
    db_path())

SCHEMA = """
CREATE TABLE IF NOT EXISTS yy_history_exclude (
  umo TEXT NOT NULL,
  role TEXT NOT NULL,
  text_sha TEXT NOT NULL,
  occur INTEGER NOT NULL,
  reason TEXT,
  created_at TEXT,
  PRIMARY KEY (umo, role, text_sha, occur)
)
"""


def _db() -> Path:
    return Path(os.environ.get("DEMO_LEDGER_DB") or DEFAULT_DB)


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").strip().encode()).hexdigest()


def text_of(m: dict) -> str:
    return " ".join(p.get("text", "") for p in (m.get("content") or [])
                    if isinstance(p, dict))


def _key(m: dict) -> tuple[str, str]:
    return m.get("role", "user"), _sha(text_of(m))


def _ensure(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)


def _migrate(con: sqlite3.Connection) -> None:
    """老表（conversation_id/idx）→ 新表。老表存在才做，做完改名存档。"""
    cols = [r[1] for r in con.execute("PRAGMA table_info(yy_history_exclude)")]
    if "idx" not in cols or "occur" in cols:
        return
    rows = con.execute(
        "select conversation_id, idx, text_sha, reason, created_at"
        " from yy_history_exclude").fetchall()
    con.execute("ALTER TABLE yy_history_exclude RENAME TO yy_history_exclude_v1")
    _ensure(con)
    # 老表的键是 conversation_id（其实存的是 umo），换算成 role+sha+occur
    hist_cache: dict[str, list] = {}
    import json

    for umo, idx, _sha_old, reason, created in rows:
        if umo not in hist_cache:
            r = con.execute("select content from conversations where user_id=?",
                            (umo,)).fetchone()
            hist_cache[umo] = json.loads(r[0] or "[]") if r else []
        hist = hist_cache[umo]
        if idx >= len(hist):
            continue
        seen: dict[tuple[str, str], int] = defaultdict(int)
        for i, m in enumerate(hist[:idx + 1]):
            k = _key(m)
            seen[k] += 1
            if i == idx:
                con.execute(
                    "insert or replace into yy_history_exclude"
                    "(umo, role, text_sha, occur, reason, created_at) values (?,?,?,?,?,?)",
                    (umo, k[0], k[1], seen[k], reason, created))
    con.commit()


def remember(umo: str, role: str, text: str, occur: int, reason: str = "",
             db_path=None) -> None:
    con = sqlite3.connect(str(db_path or _db()), timeout=15)
    try:
        _ensure(con)
        con.execute(
            "insert or replace into yy_history_exclude"
            "(umo, role, text_sha, occur, reason, created_at) values (?,?,?,?,?,?)",
            (umo, role, _sha(text), occur, reason,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        con.commit()
    finally:
        con.close()


def load(umo: str, db_path=None) -> dict[tuple[str, str], int]:
    """返回 {(role, text_sha): 要排除的出现次数}。"""
    p = Path(db_path or _db())
    if not p.exists():
        return {}
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "select role, text_sha, occur from yy_history_exclude where umo=?",
            (umo,)).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        con.close()
    out: dict[tuple[str, str], int] = {}
    for role, sha, occur in rows:
        k = (role, sha)
        out[k] = max(out.get(k, 0), int(occur))
    return out


def filter_history(messages: list, umo: str) -> list:
    """按 (role, 内容, 第几次出现) 过滤。只影响喂模型的上下文，原文不动。"""
    if not isinstance(messages, list) or not messages:
        return messages
    excl = load(umo)
    if not excl:
        return messages
    seen: dict[tuple[str, str], int] = defaultdict(int)
    out = []
    for m in messages:
        if not isinstance(m, dict):
            out.append(m)
            continue
        k = _key(m)
        seen[k] += 1
        if seen[k] <= excl.get(k, 0):
            continue
        out.append(m)
    return out


def seed(umo: str, picks: list[tuple[int, str]], reason: str,
         db_path=None) -> int:
    """按 (位置, 文本) 标排除——内部换算成 role + sha + 第几次出现。"""
    import json

    p = Path(db_path or _db())
    con = sqlite3.connect(str(p), timeout=15)
    try:
        _ensure(con)
        _migrate(con)
        _ensure(con)
        row = con.execute("select content from conversations where user_id=?",
                          (umo,)).fetchone()
        hist = json.loads(row[0] or "[]") if row else []
        n = 0
        for idx, text in picks:
            if idx >= len(hist):
                continue
            if text_of(hist[idx]).strip() != text.strip():
                continue
            seen: dict[tuple[str, str], int] = defaultdict(int)
            hit = None
            for i, m in enumerate(hist[:idx + 1]):
                k = _key(m)
                seen[k] += 1
                if i == idx:
                    hit = (k[0], k[1], seen[k])
            if hit:
                con.execute(
                    "insert or replace into yy_history_exclude"
                    "(umo, role, text_sha, occur, reason, created_at)"
                    " values (?,?,?,?,?,?)",
                    (umo, hit[0], hit[1], hit[2], reason,
                     datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                n += 1
        con.commit()
        return n
    finally:
        con.close()
