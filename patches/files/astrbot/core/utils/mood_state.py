# -*- coding: utf-8 -*-
"""P3-20（生产版）：情绪连续性。

机器人不是每句话都从"中性"开始。上一轮被夸、被顶、被冷落，下一轮的底色应该不一样。

A07 的边界：
  - 分开“机器人的情绪/精力”和“用户自己的情绪/精力”（who='her' / 'user'）；
  - 用户说“我累了”只更新 user，不推断机器人也困；
  - “我不困 / 我不喜欢”这类否定不触发；第三方“那个人很讨厌”不触发；
  - 同一条入站消息用 event_id 去重，重试不会反复加；
  - 45 分钟半衰只作用于短期情绪，不删除未解决冲突（冲突另存 life/commitment）。

表：
  yy_mood(umo, who, valence, energy, note, reason, updated_at)
  yy_mood_event(event_id, umo, who, applied_at)

用法：
  python ops\\mood\\mood_state.py --demo
"""
from __future__ import annotations

import math
from astrbot.core.utils.paths import db_path
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_DB = Path(
    os.environ.get("DEMO_DB")
    or db_path())

FMT = "%Y-%m-%d %H:%M:%S"
HALF_LIFE_MIN = 45.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS yy_mood (
  umo TEXT NOT NULL,
  who TEXT NOT NULL DEFAULT 'her',
  valence REAL DEFAULT 0,
  energy REAL DEFAULT 0.5,
  note TEXT,
  reason TEXT,
  updated_at TEXT,
  PRIMARY KEY (umo, who)
);
CREATE TABLE IF NOT EXISTS yy_mood_event (
  event_id TEXT PRIMARY KEY,
  umo TEXT,
  who TEXT,
  applied_at TEXT
);
"""

POS = ("喜欢", "关心你", "谢谢", "可爱", "棒", "厉害", "开心", "好耶", "夸")
NEG = ("滚", "烦", "闭嘴", "讨厌", "生气", "没用", "别说了", "难受", "委屈")
TIRED = ("困", "累", "熬夜", "没睡", "好晚", "凌晨")
NEGATORS = "不没别无未"


_PROD_DB = Path(r"D:/demo\instances\11111111-1111-4111-8111-111111111111\core\data\data_v4.db")


def _assert_not_prod(path) -> None:
    """N-1a 隔离自检：测试模式（DEMO_TEST=1）下严禁落到正式库。"""
    if os.environ.get("DEMO_TEST", "").strip() in ("", "0"):
        return
    try:
        if Path(path).resolve() == _PROD_DB.resolve():
            raise SystemExit(
                "隔离自检失败：测试模式下访问了正式库 " + str(path)
                + "；请把 db_path 指到临时库，或设置 DEMO_LEDGER_DB")
    except OSError:
        pass


def _db(db_path=None) -> Path:
    p = Path(db_path or os.environ.get("DEMO_LEDGER_DB") or DEFAULT_DB)
    _assert_not_prod(p)
    return p


_ENSURED: set[str] = set()


def _ensure(con: sqlite3.Connection) -> None:
    # 每个库只做一次 DDL；否则每条消息 executescript(SCHEMA) 会白占锁。
    try:
        path = con.execute("PRAGMA database_list").fetchone()[2]
    except Exception:  # noqa: BLE001
        path = ""
    if path and path in _ENSURED:
        return
    cols = {row[1] for row in con.execute("PRAGMA table_info(yy_mood)")}
    if cols and "who" not in cols:
        # 旧版 PK 只有 umo：重建，旧表留作审计
        try:
            con.execute("ALTER TABLE yy_mood RENAME TO yy_mood_old")
        except sqlite3.OperationalError:
            pass
        con.executescript(SCHEMA)
        try:
            con.execute(
                "INSERT OR IGNORE INTO yy_mood(umo,who,valence,energy,note,updated_at)"
                " SELECT umo,'her',valence,energy,note,updated_at FROM yy_mood_old")
            con.commit()
        except sqlite3.OperationalError:
            pass
    else:
        con.executescript(SCHEMA)
    if path:
        _ENSURED.add(path)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _decayed(v: float, e: float, then: datetime, now: datetime) -> tuple[float, float]:
    minutes = max(0.0, (now - then).total_seconds() / 60.0)
    k = math.pow(0.5, minutes / HALF_LIFE_MIN)
    return v * k, 0.5 + (e - 0.5) * k


def _read(umo: str, who: str = "her", db_path=None) -> tuple[float, float, datetime]:
    p = _db(db_path)
    if not p.exists():                 # 还没建库：就是中性
        return 0.0, 0.5, datetime.now()
    con = sqlite3.connect(str(p), timeout=15)
    try:
        _ensure(con)
        row = con.execute(
            "select valence, energy, updated_at from yy_mood where umo=? and who=?",
            (umo, who)).fetchone()
    finally:
        con.close()
    if not row:
        return 0.0, 0.5, datetime.now()
    try:
        then = datetime.strptime(row[2] or "", FMT)
    except ValueError:
        then = datetime.now()
    return float(row[0]), float(row[1]), then


def current(umo: str, who: str = "her", db_path=None) -> tuple[float, float]:
    v, e, then = _read(umo, who, db_path)
    return _decayed(v, e, then, datetime.now())


def _seen(event_id: str, db_path=None) -> bool:
    if not event_id:
        return False
    con = sqlite3.connect(str(_db(db_path)), timeout=15)
    try:
        _ensure(con)
        return con.execute("select 1 from yy_mood_event where event_id=?",
                           (event_id,)).fetchone() is not None
    finally:
        con.close()


def _mark(event_id: str, umo: str, who: str, db_path=None) -> None:
    if not event_id:
        return
    con = sqlite3.connect(str(_db(db_path)), timeout=15)
    try:
        _ensure(con)
        con.execute(
            "insert or ignore into yy_mood_event(event_id,umo,who,applied_at)"
            " values(?,?,?,?)", (event_id, umo, who, datetime.now().strftime(FMT)))
        con.commit()
    finally:
        con.close()


def bump(umo: str, dv: float = 0.0, de: float = 0.0, note: str = "",
         who: str = "her", reason: str = "", event_id: str | None = None,
         db_path=None) -> tuple[float, float]:
    if event_id and _seen(event_id, db_path):
        return current(umo, who, db_path)
    now = datetime.now()
    v, e, then = _read(umo, who, db_path)
    v, e = _decayed(v, e, then, now)        # 先把旧值衰减到"现在"
    v = _clamp(v + dv, -1.0, 1.0)
    e = _clamp(e + de, 0.0, 1.0)
    # R06：去重标记与数值写入同一个事务。
    # 先插入标记，rowcount=0 说明这条情绪已被处理过 → 回滚，绝不重复累加。
    con = sqlite3.connect(str(_db(db_path)), timeout=15)
    try:
        _ensure(con)
        con.execute("BEGIN IMMEDIATE")
        if event_id:
            cur = con.execute(
                "insert or ignore into yy_mood_event(event_id,umo,who,applied_at)"
                " values(?,?,?,?)",
                (event_id, umo, who, now.strftime(FMT)))
            if cur.rowcount == 0:
                con.execute("ROLLBACK")
                return current(umo, who, db_path)
        con.execute(
            "insert into yy_mood(umo,who,valence,energy,note,reason,updated_at)"
            " values (?,?,?,?,?,?,?) on conflict(umo,who) do update set"
            " valence=excluded.valence, energy=excluded.energy,"
            " note=excluded.note, reason=excluded.reason,"
            " updated_at=excluded.updated_at",
            (umo, who, v, e, note[:40], reason[:80], now.strftime(FMT)))
        con.execute("COMMIT")
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        con.close()
    return v, e


# 第三方词：紧跟"你"时不算在说机器人（R06）
_THIRD_PARTY = ("朋友", "同学", "室友", "妈", "爸", "父母", "家人", "老师",
                "同事", "上级", "哥哥", "姐姐", "弟弟", "妹妹", "对象", "朋友",
                "朋友", "你们", "用户", "机器人")

# 引用/转述：这些词出现在情绪词前，说明不是机器人经历的情绪。
_QUOTE_VERBS = ("用户说", "机器人说", "用户们说", "它说", "同事说", "朋友说",
                "同学说", "上级说", "别人说", "听说", "转述", "跟我说",
                "我妈说", "我爸说", "我哥说", "我姐说", "室友说")
_THIRD_HEAD = ("你妈", "你爸", "你家", "你们", "你朋友", "你同学",
               "你同事", "你上级", "你室友", "你对象", "你朋友", "你朋友")
_HER_CUES = ("关心你", "讨厌你", "你走")


def _cue_subject(text: str, i: int, cue: str) -> str:
    """判断一个情绪/冲突词的主语：her / user / third / 空（不确定）。

    只做保守判断；不确定就不改任何人的状态。
    """
    if _negated(text, i):
        return ""
    head = text[max(0, i - 18):i]
    tail = text[i + len(cue):i + len(cue) + 4]
    if any(v in head for v in _QUOTE_VERBS):
        return "third"
    if any(w in head for w in _THIRD_HEAD):
        return "third"
    if tail.startswith("吗") and cue in ("烦", "难受", "委屈", "介意", "生气", "失望"):
        return ""       # “你难受吗/你介意吗”是关心，不是冲突
    if cue in _HER_CUES:
        return "her"
    # 命令式：别烦我 / 滚 / 闭嘴；但“别说了，我很烦”是用户自己的状态。
    if text.startswith(("别", "滚", "闭嘴")):
        return "user" if "我" in head[-12:] else "her"
    # “认可你 / 烦你 / 关心你”里的“你”在词后面，直接指向机器人。
    if tail.startswith("你"):
        return "her"
    # “你好烦 / 你让我很烦 / 你能不能别烦”里的“你”在词前面。
    if re.search(r"你[^。！？]{0,6}$", head):
        return "her"
    # “你昨天那句话让我挺难受”：经历者是“我”，但情绪是指向“你”的。
    if re.search(r"你[^。！？]{0,12}(?:让|害|把)我[^。！？]{0,8}$", head):
        return "her"
    # “我今天很烦，你能陪我吗”：情绪词前最近的主语是“我”。
    if "我" in head[-12:]:
        return "user"
    return ""


def emotion_subject(text: str, cue: str) -> str:
    """返回某个情绪词的主语；同一个词出现多次时取第一次。"""
    t = (text or "").strip()
    i = t.find(cue) if cue else -1
    return _cue_subject(t, i, cue) if i >= 0 else ""


def _addressed_to_her(text: str) -> bool:
    """兼容旧调用：只有明确指向机器人时才返回 True。"""
    return any(_cue_subject(text, i, w) == "her"
               for w in ("你", "烦", "喜欢", "想") for i in [text.find(w)]
               if i >= 0)


def _negated(text: str, idx: int) -> bool:
    head = text[max(0, idx - 3):idx]
    return any(ch in head for ch in NEGATORS)


def _hit(text: str, words: tuple[str, ...]) -> tuple[str, int] | None:
    for w in words:
        start = 0
        while True:
            i = text.find(w, start)
            if i < 0:
                break
            # “麻烦/烦恼”不是情绪词；避免把“麻烦你”当成“烦”。
            if w == "烦" and i > 0 and text[i - 1] in ("麻", "厌"):
                start = i + 1
                continue
            if w == "烦" and text[i + 1:i + 2] == "恼":
                start = i + 1
                continue
            return w, i
    return None


def apply(umo: str, text: str, event_id: str | None = None,
          db_path=None) -> tuple[float, float]:
    """只看“谁对谁说了什么”，保守地改机器人的状态；用户自己的状态单独记。"""
    text = (text or "").strip()
    if not text:
        return current(umo, "her", db_path)
    if event_id and _seen(event_id, db_path):
        return current(umo, "her", db_path)

    dv = de = 0.0
    reason = ""

    pos = _hit(text, POS)
    pos_subject = _cue_subject(text, pos[1], pos[0]) if pos else ""
    if pos and pos_subject == "her":
        dv += 0.08
        reason = f"被夸:{pos[0]}"
    neg = _hit(text, NEG)
    neg_subject = _cue_subject(text, neg[1], neg[0]) if neg else ""
    # 只有明确冲机器人来的负向情绪才改机器人的心情；
    # “我今天很烦”“用户说你很烦”“我妈很烦”都不动机器人。
    if neg and neg_subject == "her":
        dv -= 0.15
        reason = f"被顶:{neg[0]}"

    # 用户自己的状态：只在第一人称、且未被否定时记到 who='user'
    # R06：两类变化用不同事件 ID，否则第二次会被第一次的去重标记吃掉
    if "我" in text:
        tired = _hit(text, TIRED)
        if tired and not _negated(text, tired[1]):
            bump(umo, 0.0, -0.05, note=text[:20], who="user",
                 reason=f"用户累了:{tired[0]}",
                 event_id=f"{event_id}:user:tired" if event_id else None,
                 db_path=db_path)
        if neg and neg_subject in ("user", "her"):
            bump(umo, -0.08, 0.0, note=text[:20], who="user",
                 reason=f"用户情绪:{neg[0]}",
                 event_id=f"{event_id}:user:neg" if event_id else None,
                 db_path=db_path)

    if dv == 0.0 and de == 0.0:
        if event_id:
            _mark(event_id, umo, "her", db_path)
        return current(umo, "her", db_path)
    return bump(umo, dv, de, note=text[:20], who="her", reason=reason,
                event_id=event_id, db_path=db_path)


def format_for_prompt(umo: str, who: str = "her", db_path=None) -> str:
    v, e = current(umo, who, db_path)
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
    prefix = "你现在" if who == "her" else "用户现在"
    return f"（{prefix}：" + "，".join(bits) + "）"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if "--demo" not in sys.argv:
        print(__doc__)
        return 0
    import tempfile

    db = Path(tempfile.mkdtemp()) / "mood.db"
    umo = "aiocqhttp:FriendMessage:100000000"
    print("初始:", current(umo, db_path=db), "|", format_for_prompt(umo, db_path=db) or "(无)")
    for t in ("我不喜欢这个", "我不困", "那个人很讨厌"):
        print(t, "->", apply(umo, t, event_id=t, db_path=db))
    print("被夸:", apply(umo, "你今天好可爱", event_id="praise", db_path=db))
    print("被顶:", apply(umo, "别说了 烦", event_id="rude", db_path=db))
    print("用户累了:", apply(umo, "我累了", event_id="tired", db_path=db))
    print("机器人的状态:", current(umo, "her", db), "|", format_for_prompt(umo, db_path=db))
    print("用户的状态:", current(umo, "user", db))
    return 0


if __name__ == "__main__":
    sys.exit(main())

