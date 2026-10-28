# -*- coding: utf-8 -*-
"""P3-20：情绪连续性。

机器人不是每句话都从"中性"开始。上一轮被夸、被顶、被冷落，下一轮的底色应该不一样。

一张表存两个数：
  yy_mood(umo, valence, energy, note, updated_at)
  valence: -1（不高兴）… +1（高兴）    energy: 0（困）… 1（精神）

  apply()     按关键词保守地给一点增量（不做 LLM 情绪识别，容易胡猜）
  bump()      直接给增量（上层自己判断时用）
  current()   读当前值，并按时间衰减回中性（45 分钟半衰）
  format_for_prompt()  只有明显偏离中性时才给一句状态

用法：
  python ops\\mood\\mood_state.py --demo
"""
from __future__ import annotations

import math
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_DB = Path(
    r"D:/demo\instances\11111111-1111-4111-8111-111111111111\core\data\data_v4.db")

FMT = "%Y-%m-%d %H:%M:%S"
HALF_LIFE_MIN = 45.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS yy_mood (
  umo TEXT PRIMARY KEY,
  valence REAL DEFAULT 0,
  energy REAL DEFAULT 0.5,
  note TEXT,
  updated_at TEXT
)
"""

POS = ("喜欢", "关心你", "谢谢", "可爱", "棒", "厉害", "开心", "好耶", "夸", "好的")
NEG = ("滚", "烦", "闭嘴", "讨厌", "生气", "没用", "别说了")
TIRED = ("困", "累", "熬夜", "没睡", "睡了", "好晚", "凌晨")


def _db(db_path=None) -> Path:
    return Path(db_path or os.environ.get("DEMO_LEDGER_DB") or DEFAULT_DB)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _decayed(v: float, e: float, then: datetime, now: datetime) -> tuple[float, float]:
    minutes = max(0.0, (now - then).total_seconds() / 60.0)
    k = math.pow(0.5, minutes / HALF_LIFE_MIN)
    return v * k, 0.5 + (e - 0.5) * k


def _read(umo: str, db_path=None) -> tuple[float, float, datetime]:
    p = _db(db_path)
    if not p.exists():                 # 还没建库：就是中性
        return 0.0, 0.5, datetime.now()
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        row = con.execute(
            "select valence, energy, updated_at from yy_mood where umo=?",
            (umo,)).fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        con.close()
    if not row:
        return 0.0, 0.5, datetime.now()
    try:
        then = datetime.strptime(row[2] or "", FMT)
    except ValueError:
        then = datetime.now()
    return float(row[0]), float(row[1]), then


def current(umo: str, db_path=None) -> tuple[float, float]:
    v, e, then = _read(umo, db_path)
    return _decayed(v, e, then, datetime.now())


def bump(umo: str, dv: float = 0.0, de: float = 0.0, note: str = "",
         db_path=None) -> tuple[float, float]:
    now = datetime.now()
    v, e, then = _read(umo, db_path)
    v, e = _decayed(v, e, then, now)        # 先把旧值衰减到"现在"
    v = _clamp(v + dv, -1.0, 1.0)
    e = _clamp(e + de, 0.0, 1.0)
    con = sqlite3.connect(str(_db(db_path)), timeout=15)
    try:
        con.executescript(SCHEMA)
        con.execute(
            "insert into yy_mood(umo, valence, energy, note, updated_at)"
            " values (?,?,?,?,?) on conflict(umo) do update set"
            " valence=excluded.valence, energy=excluded.energy,"
            " note=excluded.note, updated_at=excluded.updated_at",
            (umo, v, e, note, now.strftime(FMT)))
        con.commit()
    finally:
        con.close()
    return v, e


def apply(umo: str, text: str, db_path=None) -> tuple[float, float]:
    dv = de = 0.0
    if any(w in text for w in POS):
        dv += 0.08
    if any(w in text for w in NEG):
        dv -= 0.15
    if any(w in text for w in TIRED):
        de -= 0.05
    if dv == 0.0 and de == 0.0:
        return current(umo, db_path)
    return bump(umo, dv, de, note=text[:20], db_path=db_path)


def format_for_prompt(umo: str, db_path=None) -> str:
    v, e = current(umo, db_path)
    bits = []
    if v > 0.25:
        bits.append("心情不错")
    elif v < -0.25:
        bits.append("有点不高兴")
    if e < 0.30:
        bits.append("很困、没精神")
    elif e > 0.75:
        bits.append("挺精神")
    if not bits:
        return ""
    return "（你现在：" + "，".join(bits) + "）"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if "--demo" not in sys.argv:
        print(__doc__)
        return 0
    import tempfile

    db = Path(tempfile.mkdtemp()) / "mood.db"
    umo = "aiocqhttp:FriendMessage:100000000"
    print("初始:", current(umo, db_path=db), "|", format_for_prompt(umo, db_path=db) or "(无)")
    print("被夸1次:", apply(umo, "你今天好可爱", db_path=db))
    for _ in range(3):
        v = apply(umo, "认可你 你可真棒", db_path=db)
    print("再夸3次:", v, "|", format_for_prompt(umo, db_path=db))
    print("被顶一句:", apply(umo, "别说了 烦", db_path=db), "|",
          format_for_prompt(umo, db_path=db))
    print("熬夜:", apply(umo, "我又熬夜了 好困", db_path=db), "|",
          format_for_prompt(umo, db_path=db))
    return 0


if __name__ == "__main__":
    sys.exit(main())

