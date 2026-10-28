# -*- coding: utf-8 -*-
"""P3-19：结构化记忆（机器人记得的"事实"，不是聊天原文）。

聊天记录检索（doc.db）解决"我以前说过什么"；这个表解决"关于用户/我，有哪些确定的事"：
  yy_fact(subject, predicate, obj, confidence, source, created_at, updated_at)
  subject  = umo（或 "her" / "world"）
  predicate= 喜欢 / 生日 / 在练 / 讨厌 / 叫 …
  obj      = 值

提供：
  remember(...)     写入或覆盖（同 subject+predicate 视为更新）
  recall(...)       读（按 subject，或模糊匹配 predicate）
  format_for_prompt 生成一小段给模型看的事实清单（有上限，避免占预算）

写入方（谁来判断"这是一条事实"）留给人/上层，不在这里做 LLM 抽取——
避免模型把自己的猜测写成事实。

用法：
  python ops\\memory\\structured_memory.py --demo
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_DB = Path(
    r"D:/demo\instances\11111111-1111-4111-8111-111111111111\core\data\data_v4.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS yy_fact (
  subject TEXT NOT NULL,
  predicate TEXT NOT NULL,
  obj TEXT NOT NULL,
  confidence REAL DEFAULT 1.0,
  source TEXT,
  created_at TEXT,
  updated_at TEXT,
  PRIMARY KEY (subject, predicate)
)
"""


def _db(db_path=None) -> Path:
    return Path(db_path or os.environ.get("DEMO_LEDGER_DB") or DEFAULT_DB)


def remember(subject: str, predicate: str, obj: str, *,
             confidence: float = 1.0, source: str = "",
             db_path=None) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    con = sqlite3.connect(str(_db(db_path)), timeout=15)
    try:
        con.executescript(SCHEMA)
        con.execute(
            "insert into yy_fact(subject, predicate, obj, confidence, source,"
            " created_at, updated_at) values (?,?,?,?,?,?,?)"
            " on conflict(subject, predicate) do update set"
            " obj=excluded.obj, confidence=excluded.confidence,"
            " source=excluded.source, updated_at=excluded.updated_at",
            (subject, predicate, obj, confidence, source, now, now))
        con.commit()
    finally:
        con.close()


def recall(subject: str | None = None, like: str | None = None,
           limit: int = 20, db_path=None) -> list[tuple[str, str, str]]:
    con = sqlite3.connect(f"file:{_db(db_path)}?mode=ro", uri=True)
    try:
        sql = "select subject, predicate, obj from yy_fact"
        args: list = []
        where = []
        if subject:
            where.append("subject=?")
            args.append(subject)
        if like:
            where.append("(predicate like ? or obj like ?)")
            args += [f"%{like}%", f"%{like}%"]
        if where:
            sql += " where " + " and ".join(where)
        sql += " order by confidence desc, updated_at desc limit ?"
        args.append(limit)
        return con.execute(sql, args).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def forget(subject: str, predicate: str, db_path=None) -> int:
    con = sqlite3.connect(str(_db(db_path)), timeout=15)
    try:
        n = con.execute("delete from yy_fact where subject=? and predicate=?",
                        (subject, predicate)).rowcount
        con.commit()
        return n
    finally:
        con.close()


def format_for_prompt(subject: str, limit: int = 8, db_path=None) -> str:
    rows = recall(subject, limit=limit, db_path=db_path)
    if not rows:
        return ""
    lines = [f"{p}：{o}" for _s, p, o in rows]
    return "（关于用户，你记得这些：" + "；".join(lines) + "）"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if "--demo" not in sys.argv:
        print(__doc__)
        return 0
    import tempfile

    db = Path(tempfile.mkdtemp()) / "demo.db"
    umo = "aiocqhttp:FriendMessage:100000000"
    remember(umo, "叫", "用户", source="demo", db_path=db)
    remember(umo, "生日", "没问出来", source="demo", db_path=db)
    remember(umo, "在练", "乐器", source="demo", db_path=db)
    remember(umo, "在练", "乐器", source="demo", db_path=db)   # 覆盖
    print("recall:", recall(umo, db_path=db))
    print("按词查:", recall(umo, like="练", db_path=db))
    print(format_for_prompt(umo, db_path=db))
    return 0


if __name__ == "__main__":
    sys.exit(main())

