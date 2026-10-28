# -*- coding: utf-8 -*-
"""生活层最小运行时（09 号设计文件的实现）。

原则：
  - 只推进状态，不调用大模型；生成层只拿“可以说什么”。
  - 事件有稳定 ID；重启/重复推进不会重复生成同一天。
  - 离线只推进到“现在”，不补发几十条过去的消息。
  - 默认关闭：`DEMO_LIFE=1` 才启用；先影子读，再接生成。

示例作息（合成数据，不是真实日程）：睡眠、上课、吃饭、游戏、练习；
具体时间由配置/正典决定。
"""
from __future__ import annotations

import datetime as dt
from astrbot.core.utils.paths import db_path
import json
import os
import random
import re
import sqlite3
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_DB = Path(
    os.environ.get("DEMO_DB")
    or db_path())

SCHEMA = """
CREATE TABLE IF NOT EXISTS yy_life_state (
  umo TEXT PRIMARY KEY,
  version INTEGER NOT NULL DEFAULT 0,
  tz TEXT NOT NULL,
  local_time TEXT,
  activity_id TEXT,
  place_id TEXT,
  energy REAL DEFAULT 0.5,
  energy_reason TEXT,
  energy_delta REAL,
  energy_baseline REAL,
  mood_valence REAL DEFAULT 0,
  mood_reason TEXT,
  last_advance TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS yy_life_event (
  event_id TEXT PRIMARY KEY,
  umo TEXT NOT NULL,
  kind TEXT NOT NULL,
  title TEXT,
  start_at TEXT NOT NULL,
  end_at TEXT,
  status TEXT NOT NULL,
  place_id TEXT,
  source_type TEXT NOT NULL,
  source_ref TEXT,
  occurred_at TEXT,
  told_at TEXT,
  told_kind TEXT,
  forgotten_at TEXT,
  message_id TEXT,
  task_id TEXT,
  payload_json TEXT,
  created_at TEXT,
  version INTEGER DEFAULT 0
);
CREATE VIEW IF NOT EXISTS yy_event AS SELECT * FROM yy_life_event;
CREATE TABLE IF NOT EXISTS yy_life_place (
  umo TEXT NOT NULL,
  subject TEXT NOT NULL DEFAULT 'her',
  place_id TEXT NOT NULL,
  source_type TEXT NOT NULL,
  confidence REAL NOT NULL,
  updated_at TEXT,
  expires_at TEXT,
  PRIMARY KEY (umo, subject)
);
CREATE TABLE IF NOT EXISTS yy_life_commitment (
  commit_id TEXT PRIMARY KEY,
  umo TEXT NOT NULL,
  text TEXT NOT NULL,
  created_at TEXT,
  due_at TEXT,
  status TEXT NOT NULL,
  delivered_task_id TEXT,
  evidence_ref TEXT,
  updated_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_life_commit_task
  ON yy_life_commitment(delivered_task_id)
  WHERE delivered_task_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS yy_life_source (
  source_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  url TEXT,
  bvid TEXT,
  title TEXT,
  fetched_at TEXT,
  read_scope TEXT,
  raw_sha TEXT,
  msg_ids TEXT,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS yy_life_conflict (
  conflict_id TEXT PRIMARY KEY,
  umo TEXT NOT NULL,
  text TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  source_msg_id TEXT,
  created_at TEXT,
  resolved_at TEXT,
  note TEXT,
  repair_state TEXT DEFAULT 'open',
  acknowledged TEXT,
  updated_at TEXT,
  apologizer TEXT,
  injured TEXT,
  resolution TEXT
);
CREATE TABLE IF NOT EXISTS yy_life_topic (
  umo TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS yy_life_meta (
  key TEXT PRIMARY KEY,
  value TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS yy_life_day_override (
  umo TEXT NOT NULL,
  date TEXT NOT NULL,
  kind TEXT NOT NULL,
  payload_json TEXT,
  updated_at TEXT,
  PRIMARY KEY (umo, date)
);
CREATE TABLE IF NOT EXISTS yy_life_delay (
  id TEXT PRIMARY KEY,
  umo TEXT NOT NULL,
  text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  due_at TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  task_id TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS yy_life_detail (
  event_id TEXT PRIMARY KEY,
  umo TEXT NOT NULL,
  date TEXT,
  kind TEXT,
  detail_json TEXT,
  outcome TEXT DEFAULT 'planned',
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS yy_life_summary (
  summary_id TEXT PRIMARY KEY,
  umo TEXT NOT NULL,
  date TEXT,
  kind TEXT,
  importance INTEGER DEFAULT 1,
  text TEXT,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_life_delay_due ON yy_life_delay(status, due_at);
CREATE INDEX IF NOT EXISTS idx_life_event_time ON yy_life_event(umo, start_at);
CREATE INDEX IF NOT EXISTS idx_life_event_status ON yy_life_event(status);
"""

SCHEMA_VERSION = "life-2026-10-28.1"

DEFAULT_TZ = "Asia/Shanghai"
SLEEP_START_H = float(os.environ.get("DEMO_LIFE_SLEEP_START", "0.5"))   # 00:30
SLEEP_END_H = float(os.environ.get("DEMO_LIFE_SLEEP_END", "7.0"))        # 07:00
PRACTICE_START_H = float(os.environ.get("DEMO_LIFE_PRACTICE_START", "22.0"))
PRACTICE_HOURS = float(os.environ.get("DEMO_LIFE_PRACTICE_HOURS", "1.5"))
SCHOOL_ENABLED = os.environ.get("DEMO_LIFE_SCHOOL", "1").strip().lower() not in ("0", "false", "off", "no", "")


def enabled() -> bool:
    return os.environ.get("DEMO_LIFE", "0").strip().lower() not in (
        "0", "", "false", "off", "no")


def shadow() -> bool:
    """影子读：只记日志，不改 prompt。"""
    return os.environ.get("DEMO_LIFE_SHADOW", "0").strip().lower() not in (
        "0", "", "false", "off", "no")


def relation_enabled() -> bool:
    """关系记忆独立开关；默认开，关生活层不影响它。"""
    return os.environ.get("DEMO_RELATION", "1").strip().lower() not in (
        "0", "", "false", "off", "no")


def _is_test() -> bool:
    return os.environ.get("DEMO_TEST", "").strip() not in ("", "0")


def shadow_db() -> Path:
    """影子生活层自己的库；默认放在正式库旁边的 life_shadow.db。"""
    raw = os.environ.get("DEMO_LIFE_SHADOW_DB")
    if raw:
        return Path(raw)
    return DEFAULT_DB.parent / "life_shadow.db"


def relation_db() -> Path:
    """关系记忆的正式库；影子生活层不会写这里。"""
    raw = os.environ.get("DEMO_RELATION_DB")
    if raw:
        return Path(raw)
    return Path(os.environ.get("DEMO_LEDGER_DB") or DEFAULT_DB)


def _relation_path(db_path=None) -> Path:
    """承诺/关系类写入：显式路径优先，否则一律正式库，绝不跟随 shadow。"""
    return Path(db_path) if db_path else relation_db()


_LIFE_Q = re.compile(
    r"在干嘛|在干什么|干嘛呢|干嘛啊|在忙什么|忙什么呢|在做什么|在做什么呢|"
    r"在哪儿|在哪呢|现在在哪|你在哪|练习|练了没|练完没|练了多久|上课|看番|打游戏|"
    r"今天做什么|今天干嘛|今天干什么|睡了吗|起床了吗|吃了吗|"
    r"午饭|晚饭|吃什么|吃啥|"
    r"你[^。！？]{0,8}吃(?:什么|啥)|"
    r"后来|然后呢|忙完|做完|吃完|怎么样|好吃吗|好玩吗|那个呢|"
    r"刚才|昨天|早上打算|打算做|去哪|生日|生日快乐|哪里人|哪座城市|忙啥|在忙吗|现在忙吗|干嘛呀|在忙啥"
)

_REL_Q = re.compile(
    r"什么关系|算什么|在一起吗|朋友|朋友|喜不认可我|认可我吗|"
    r"我们算什么|确定关系|什么关系啊"
)

# 追问/指代：要落到“刚才那件事”，不能只给当前快照。
_FOLLOW_RE = re.compile(
    r"后来|然后呢|忙完|练完|做完|吃完|结束|怎么样|好吃吗|好玩吗|"
    r"那个|刚才|昨天|早上打算|打算做|去哪|然后"
)


def is_followup(text: str) -> bool:
    return _about_her(text) and bool(_FOLLOW_RE.search(text or ""))


def _about_her(text: str) -> bool:
    """P1-B07：这句话是不是在问/说机器人，而不是用户讲自己的事或转述第三人。"""
    t = (text or "").strip()
    if not t:
        return False
    if any(w in t for w in (
            "用户说", "机器人说", "用户们说", "它说", "听说", "同事说", "朋友说",
            "别人说", "上级说", "你朋友", "你同学", "你同事", "你上级",
            "你妈", "你爸", "你家", "你们")):
        return False
    # “我昨天在外面吃饭”是用户自己的经历，不能更新机器人的话题指针。
    if "我" in t and "你" not in t:
        return False
    if ("用户" in t or "机器人" in t) and "你" not in t:
        return False
    return True


def should_inject(text: str) -> bool:
    """被动回复要不要带生活层状态：问机器人在干嘛/在哪/练习，或追问刚才那件事。"""
    t = text or ""
    # "今天吃什么" 是用户在盘算自己的饭，不是问机器人；带"你/机器人"才算问到机器人
    if re.fullmatch(r"(今天|中午|晚上|今儿)?\s*吃(什么|啥)[？?]?", t.strip()):
        return False
    if _REL_Q.search(t):
        return True
    return _about_her(t) and bool(_LIFE_Q.search(t))


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
    if db_path:
        p = Path(db_path)
    elif shadow() and not _is_test():
        # 影子期绝不把虚拟生活写进正式库；正式关系记忆由 relation_db() 单独指定。
        p = shadow_db()
    else:
        p = Path(os.environ.get("DEMO_LEDGER_DB") or DEFAULT_DB)
    _assert_not_prod(p)
    return p


_ENSURED: set[str] = set()


def _migrate_place_subject(con: sqlite3.Connection) -> None:
    """P1-B06：老库 yy_life_place 没有 subject，旧行主体无法证实。

    不把历史记录猜成 her：整表改名到 legacy 表留审计，新表从空开始。
    current_place() 只读新表，因此旧数据不会冒充当前地点。
    """
    cols = {r[1] for r in con.execute("PRAGMA table_info(yy_life_place)")}
    if not cols or "subject" in cols:
        return
    legacy = "yy_life_place_legacy"
    n = 1
    while con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (legacy,)).fetchone():
        n += 1
        legacy = f"yy_life_place_legacy_{n}"
    con.execute(f"ALTER TABLE yy_life_place RENAME TO {legacy}")
    con.execute(
        "CREATE TABLE yy_life_place ("
        " umo TEXT NOT NULL, subject TEXT NOT NULL DEFAULT 'her',"
        " place_id TEXT NOT NULL, source_type TEXT NOT NULL,"
        " confidence REAL NOT NULL, updated_at TEXT, expires_at TEXT,"
        " PRIMARY KEY (umo, subject))")
    con.execute(
        "INSERT INTO yy_life_meta(key,value,updated_at)"
        " VALUES('place_legacy_migrated',?,?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
        " updated_at=excluded.updated_at",
        (legacy, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")))
    con.commit()


def _conn(db_path=None) -> sqlite3.Connection:
    path = str(_db(db_path))
    con = sqlite3.connect(path, timeout=15)
    con.execute("PRAGMA busy_timeout=15000")
    if path in _ENSURED:
        return con
    con.executescript(SCHEMA)
    con.execute(
        "INSERT INTO yy_life_meta(key,value,updated_at) VALUES('schema_version',?,?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (SCHEMA_VERSION, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")),
    )
    con.commit()
    try:
        _scols = [r[1] for r in con.execute("PRAGMA table_info(yy_life_state)")]
        for _c in ("energy_reason", "energy_delta", "energy_baseline"):
            if _c not in _scols:
                con.execute(f"ALTER TABLE yy_life_state ADD COLUMN {_c} REAL" if _c != "energy_reason" else "ALTER TABLE yy_life_state ADD COLUMN energy_reason TEXT")
        cols = [r[1] for r in con.execute("PRAGMA table_info(yy_life_source)")]
        if "title" not in cols:
            con.execute("ALTER TABLE yy_life_source ADD COLUMN title TEXT")
            con.commit()
    except sqlite3.OperationalError:
        pass
    # F2：地点表补 subject（老库只有 umo 主键）
    try:
        _migrate_place_subject(con)
    except sqlite3.OperationalError:
        pass
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(yy_life_event)")]
        for name in ("occurred_at", "told_at", "told_kind", "forgotten_at",
                     "message_id", "task_id"):
            if name not in cols:
                con.execute(f"ALTER TABLE yy_life_event ADD COLUMN {name} TEXT")
        con.execute("UPDATE yy_life_event SET occurred_at=start_at"
                    " WHERE occurred_at IS NULL")
        con.commit()
    except sqlite3.OperationalError:
        pass
    # P0-3 / P1-B05：冲突补修复阶段/承认内容/责任方；老库默认 open。
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(yy_life_conflict)")]
        for name in ("repair_state", "acknowledged", "updated_at",
                     "apologizer", "injured", "resolution"):
            if name not in cols:
                con.execute(f"ALTER TABLE yy_life_conflict ADD COLUMN {name} TEXT")
        con.execute("UPDATE yy_life_conflict SET repair_state=status"
                    " WHERE repair_state IS NULL")
        con.execute("UPDATE yy_life_conflict SET updated_at=COALESCE(updated_at,created_at)"
                    " WHERE updated_at IS NULL")
        con.commit()
    except sqlite3.OperationalError:
        pass
    _ENSURED.add(path)
    return con


def _iso(d: dt.datetime) -> str:
    return d.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def _parse(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def _tz() -> ZoneInfo:
    name = os.environ.get("DEMO_LIFE_TZ", DEFAULT_TZ)
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        return ZoneInfo(DEFAULT_TZ)


# ---------- 日程（provisional） ----------
def _day_events(umo: str, local_date: dt.date, tz: ZoneInfo,
                override: str | None = None,
                payload: dict | None = None) -> list[dict]:
    """一天的基础事件；练习 22:00-23:30，睡觉 00:30-07:00。

    override:
      - "exam"   考试日：上午考试、下午/晚上复习，不上课
      - "outing" 出门日：默认 10:00–18:00 在外面，午/晚饭在外面，不上课
    payload 可自定义 exam_start/exam_end 或 outing start/end（HH:MM）。
    """
    def at(h: int, m: int = 0) -> dt.datetime:
        return dt.datetime.combine(local_date, dt.time(h, m), tzinfo=tz)

    def ath(value: float) -> dt.datetime:
        return at(int(value), int(round((value % 1) * 60)))

    def hm(value, default: dt.datetime) -> dt.datetime:
        if not value:
            return default
        try:
            h, m = str(value).split(":", 1)
            return at(int(h), int(m))
        except (ValueError, TypeError):
            return default

    payload = payload or {}
    rng = random.Random(f"{umo}:{local_date.isoformat()}")
    ph = int(PRACTICE_START_H)
    pm = int(round((PRACTICE_START_H % 1) * 60))
    p_start = at(ph, pm)
    p_end = p_start + dt.timedelta(hours=PRACTICE_HOURS)
    _pw = _canon_spec("practice_window")
    if _pw:
        p_start = hm(_pw.get("start"), p_start)
        p_end = hm(_pw.get("end"), p_end)
        if p_end <= p_start:
            p_end = p_end + dt.timedelta(days=1)
    ev: list[dict] = []

    def add(kind: str, title: str, place: str,
            start: dt.datetime, end: dt.datetime) -> None:
        ev.append({
            "event_id": f"{umo}:{local_date.isoformat()}:{kind}:"
                        f"{start.strftime('%H%M')}",
            "umo": umo, "kind": kind, "title": title,
            "start_at": _iso(start), "end_at": _iso(end),
            "place_id": place, "source_type": "virtual",
        })

    add("sleep", "睡觉", "home", ath(SLEEP_START_H), ath(SLEEP_END_H))

    # 周末有 30% 概率出门半天（确定性随机，重启不漂）
    if override is None and local_date.weekday() >= 5 and rng.random() < 0.30:
        override = "outing"

    if override == "exam":
        e_start = hm(payload.get("exam_start"), at(9, 0))
        e_end = hm(payload.get("exam_end"), at(11, 30))
        if not (at(8, 0) <= e_start < e_end <= at(12, 0)):
            e_start, e_end = at(9, 0), at(11, 30)
        if e_start > ath(SLEEP_END_H):
            add("free", "空闲", "home", ath(SLEEP_END_H), e_start)
        add("exam", "考试", "学校", e_start, e_end)
        if e_end < at(12, 0):
            add("free", "空闲", "home", e_end, at(12, 0))
        add("meal", "午饭", "home", at(12, 0), at(12, 40))
        add("free", "空闲", "home", at(12, 40), at(13, 0))
        add("study", "复习", "学校", at(13, 0), at(17, 0))
        add("free", "空闲", "home", at(17, 0), at(18, 0))
        add("meal", "晚饭", "home", at(18, 0), at(18, 40))
        add("free", "空闲", "home", at(18, 40), at(19, 30))
        add("study", "复习", "home", at(19, 30), at(21, 30))
    elif override == "outing":
        o_start = hm(payload.get("start"), at(10, 0))
        o_end = hm(payload.get("end"), at(18, 0))
        if not (at(8, 0) <= o_start < at(12, 0) and at(13, 0) < o_end <= at(18, 0)):
            o_start, o_end = at(10, 0), at(18, 0)
        if o_start > ath(SLEEP_END_H):
            add("free", "空闲", "home", ath(SLEEP_END_H), o_start)
        add("outing", "出门", "外面", o_start, at(12, 0))
        add("meal", "午饭", "外面", at(12, 0), at(13, 0))
        add("outing", "出门", "外面", at(13, 0), o_end)
        if o_end < at(18, 40):
            add("free", "空闲", "home", o_end, at(18, 40))
        add("meal", "晚饭", "home", at(18, 40), at(19, 20))
        add("free", "空闲", "home", at(19, 20), at(19, 30))
        if rng.random() < 0.72:
            add("game", "打崩铁", "home", at(19, 30), at(21, 30))
        else:
            add("anime", "看番", "home", at(19, 30), at(21, 30))
    else:
        _mc = (_canon_spec("school_schedule") or {}).get("morning_class")
        add("free", "空闲", "home", ath(SLEEP_END_H), at(7, 30))
        add("meal", "早饭", "home", at(7, 30), at(8, 0))
        if _mc and (local_date.weekday() + 1) in (_mc.get("days") or []):
            _ms = hm(_mc.get("start"), at(9, 0))
            _me = hm(_mc.get("end"), at(11, 0))
            if at(8, 0) < _ms < _me <= at(12, 0):
                add("free", "空闲", "home", at(8, 0), _ms)
                add("class", "上课", "学校", _ms, _me)
                add("free", "空闲", "home", _me, at(12, 0))
            else:
                add("free", "空闲", "home", at(8, 0), at(12, 0))
        else:
            add("free", "空闲", "home", at(8, 0), at(12, 0))
        add("meal", "午饭", "home", at(12, 0), at(12, 40))
        add("free", "空闲", "home", at(12, 40), at(13, 0))
        if local_date.weekday() < 5 and SCHOOL_ENABLED:
            add("class", "上课", "学校", at(13, 0), at(17, 0))
        else:
            add("free", "空闲", "home", at(13, 0), at(17, 0))
        add("free", "空闲", "home", at(17, 0), at(18, 0))
        add("meal", "晚饭", "home", at(18, 0), at(18, 40))
        add("free", "空闲", "home", at(18, 40), at(19, 30))
        if rng.random() < 0.72:
            add("game", "打崩铁", "home", at(19, 30), at(21, 30))
        else:
            add("anime", "看番", "home", at(19, 30), at(21, 30))

    if p_start > at(21, 30):
        add("free", "空闲", "home", at(21, 30), p_start)
    add("practice", "练习", "练习室", p_start, p_end)
    sleep_next = ath(SLEEP_START_H) + dt.timedelta(days=1)
    if p_end < sleep_next:
        add("free", "空闲", "home", p_end, sleep_next)

    ev.sort(key=lambda e: e["start_at"])
    return ev


_MENUS = ("番茄鸡蛋面", "青椒肉丝盖饭", "小馄饨", "牛肉粉", "蛋炒饭", "外卖寿司")
_BREAKFAST = ("豆浆油条", "包子", "煎蛋吐司", "小米粥", "三明治")
_PIECES = {
    "乐器": ("练习曲一", "练习曲二", "练习曲三"),
}


def _practice_pieces() -> tuple:
    """按正典乐器挑曲目：优先主项；没填就退回乐器。"""
    spec = _canon_spec("instrument")
    name = str(spec.get("primary") or "乐器")
    return _PIECES.get(name, ()) or _PIECES["乐器"]
_SUBJECTS = ("专业课", "选修课", "实验课", "习题课")
_GAMES = ("崩铁", "原神", "双人成行")
_ANIME = ("番剧", "纪录片", "老电影")


def _detail_text(e: dict) -> str:
    rng = random.Random(f"{e['event_id']}:detail")
    kind = e.get("kind")
    if kind == "meal":
        _menu = _BREAKFAST if "早饭" in str(e.get("title")) else _MENUS
        _day = str(e.get("start_at"))[:10]
        _off = {"早饭": 0, "午饭": 1, "晚饭": 2}.get(str(e.get("title")), 0)
        _h = sum(ord(c) * (i + 1) for i, c in enumerate(f"{_day}:{e.get('title')}"))
        return f"这顿吃的是{_menu[(_h + _off) % len(_menu)]}"
    if kind == "practice":
        return f"在练{rng.choice(_practice_pieces())}，重点是节奏"
    if kind == "class":
        return rng.choice(_SUBJECTS)
    if kind == "game":
        return ""
    if kind == "anime":
        return ""
    if kind == "exam":
        return "在考试，先不看手机"
    if kind == "outing":
        return "在外面，可能晚点回"
    return ""


def _ensure_detail(con: sqlite3.Connection, e: dict) -> None:
    text = _detail_text(e)
    if not text:
        return
    con.execute(
        "INSERT OR IGNORE INTO yy_life_detail(event_id,umo,date,kind,detail_json,"
        "outcome,updated_at) VALUES(?,?,?,?,?,?,?)",
        (e["event_id"], e["umo"], str(e["start_at"])[:10], e.get("kind"),
         json.dumps({"text": text}, ensure_ascii=False), "planned",
         dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")))


def detail_for(event_id: str, db_path=None) -> dict | None:
    con = _conn(db_path)
    try:
        row = con.execute(
            "SELECT event_id,umo,date,kind,detail_json,outcome,updated_at"
            " FROM yy_life_detail WHERE event_id=?", (event_id,)).fetchone()
        if not row:
            return None
        try:
            payload = json.loads(row[4] or "{}")
        except (TypeError, ValueError):
            payload = {}
        return {"event_id": row[0], "umo": row[1], "date": row[2],
                "kind": row[3], "text": payload.get("text", ""),
                "outcome": row[5], "updated_at": row[6]}
    finally:
        con.close()


def set_event_outcome(event_id: str, outcome: str, note: str = "",
                      db_path=None) -> bool:
    if outcome not in ("completed", "interrupted", "cancelled", "planned"):
        raise ValueError("invalid outcome")
    con = _conn(db_path)
    try:
        row = con.execute("SELECT detail_json FROM yy_life_detail WHERE event_id=?",
                          (event_id,)).fetchone()
        if not row:
            return False
        try:
            payload = json.loads(row[0] or "{}")
        except (TypeError, ValueError):
            payload = {}
        payload["outcome"] = outcome
        if note:
            payload["note"] = note
        con.execute("UPDATE yy_life_detail SET detail_json=?, outcome=?, updated_at=?"
                    " WHERE event_id=?",
                    (json.dumps(payload, ensure_ascii=False), outcome,
                     dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                     event_id))
        con.commit()
        return True
    finally:
        con.close()


def summarize_event(event_id: str, importance: int = 1, text: str | None = None,
                    db_path=None) -> bool:
    detail = detail_for(event_id, db_path=db_path)
    if not detail:
        return False
    body = (text or detail.get("text") or "").strip()
    if not body:
        return False
    sid = "sum:" + event_id
    con = _conn(db_path)
    try:
        con.execute(
            "INSERT OR REPLACE INTO yy_life_summary(summary_id,umo,date,kind,"
            "importance,text,created_at) VALUES(?,?,?,?,?,?,?)",
            (sid, detail["umo"], detail.get("date"), detail.get("kind"),
             int(importance), body,
             dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")))
        con.commit()
        return True
    finally:
        con.close()


def list_summaries(umo: str, days: int = 7, *, min_importance: int = 1,
                   db_path=None) -> list[dict]:
    cutoff = (dt.datetime.now(dt.timezone.utc)
              - dt.timedelta(days=days)).date().isoformat()
    con = _conn(db_path)
    try:
        rows = con.execute(
            "SELECT summary_id,date,kind,importance,text FROM yy_life_summary"
            " WHERE umo=? AND date>=? AND importance>=? ORDER BY date DESC",
            (umo, cutoff, int(min_importance))).fetchall()
        return [{"summary_id": r[0], "date": r[1], "kind": r[2],
                 "importance": r[3], "text": r[4]} for r in rows]
    finally:
        con.close()


def growth_stats(umo: str, db_path=None) -> dict:
    """B18：关系成长的最小计数（说开/重要经历）。"""
    con = _conn(db_path)
    try:
        rows = con.execute(
            "SELECT kind,COUNT(*) FROM yy_life_summary WHERE umo=? GROUP BY kind",
            (umo,)).fetchall()
        return {r[0]: r[1] for r in rows}
    finally:
        con.close()


def relationship_context(umo: str, limit: int = 3, db_path=None) -> str:
    """B18：只取高重要度摘要，作为“共同经历”背景；不进正式 prompt（shadow）。"""
    rows = [x for x in list_summaries(umo, days=180, min_importance=2,
                                      db_path=db_path)][:max(1, limit)]
    if not rows:
        return ""
    return "（你俩记得的事：" + "；".join(
        f"{r['date']} {r['text']}" for r in rows) + "）"


def schedule_config() -> dict:
    """B17：作息/学校配置；默认值与原模板一致。"""
    return {"sleep_start": SLEEP_START_H, "sleep_end": SLEEP_END_H,
            "practice_start": PRACTICE_START_H, "practice_hours": PRACTICE_HOURS,
            "school": bool(SCHOOL_ENABLED), "tz": DEFAULT_TZ}


def _ensure_events(con: sqlite3.Connection, umo: str, tz: ZoneInfo,
                   around: dt.date) -> None:
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    dates = [around + dt.timedelta(days=d) for d in (-1, 0, 1)]
    overrides: dict[str, tuple[str, dict]] = {}
    try:
        marks = ",".join("?" for _ in dates)
        for d, kind, raw in con.execute(
                f"SELECT date,kind,payload_json FROM yy_life_day_override"
                f" WHERE umo=? AND date IN ({marks})",
                (umo, *[d.isoformat() for d in dates])):
            if kind in ("exam", "outing"):
                try:
                    payload = json.loads(raw or "{}")
                except (TypeError, ValueError):
                    payload = {}
                overrides[d] = (kind, payload)
    except sqlite3.OperationalError:
        pass
    for delta in (-1, 0, 1):
        local_date = around + dt.timedelta(days=delta)
        ov = overrides.get(local_date.isoformat())
        desired = _day_events(umo, local_date, tz,
                              ov[0] if ov else None,
                              ov[1] if ov else None)
        ids: list[str] = []
        for e in desired:
            eid = e["event_id"]
            ids.append(eid)
            con.execute(
                "INSERT OR IGNORE INTO yy_life_event(event_id,umo,kind,title,start_at,"
                "end_at,occurred_at,status,place_id,source_type,created_at,version)"
                " VALUES(?,?,?,?,?,?,?, 'planned', ?,?,?,0)",
                (eid, e["umo"], e["kind"], e["title"], e["start_at"],
                 e["end_at"], e["start_at"], e["place_id"], e["source_type"], now))
            _ensure_detail(con, e)
            # 日程参数变更（如睡醒时间）时，旧同名事件必须更新结束时间/标题；
            # 只改 planned/active，绝不改 done/missed/forgotten 的历史。
            con.execute(
                "UPDATE yy_life_event SET kind=?,title=?,end_at=?,place_id=?"
                " WHERE event_id=? AND source_type='virtual'"
                " AND status IN ('planned','active')",
                (e["kind"], e["title"], e["end_at"], e["place_id"], eid))
        if not ids:
            continue
        prefix = f"{umo}:{local_date.isoformat()}:%"
        marks = ",".join("?" for _ in ids)
        # 按“事件所属的本地日期”清旧 planned，不能按 UTC 起止窗口：
        # 跨午夜的 02:30 空闲 start_at 会落进次日 UTC 窗口，被误删。
        con.execute(
            f"DELETE FROM yy_life_event WHERE umo=? AND source_type='virtual'"
            f" AND status='planned' AND event_id LIKE ?"
            f" AND event_id NOT IN ({marks})",
            (umo, prefix, *ids))
        con.execute(
            f"UPDATE yy_life_event SET status='missed' WHERE umo=?"
            f" AND source_type='virtual' AND status='active'"
            f" AND event_id LIKE ? AND event_id NOT IN ({marks})",
            (umo, prefix, *ids))


def _hm_minutes(value, default: str) -> int:
    raw = str(value or default).strip()
    try:
        h, m = raw.split(":", 1)
        h, m = int(h), int(m)
    except (ValueError, TypeError):
        raise ValueError(f"invalid time: {raw!r}") from None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"invalid time: {raw!r}")
    return h * 60 + m


def validate_day_payload(kind: str, payload: dict | None = None) -> dict:
    """校验并规范化 L4 考试/出门变体时间；非法时 raise ValueError。"""
    p = dict(payload or {})
    if kind == "exam":
        start = _hm_minutes(p.get("exam_start"), "09:00")
        end = _hm_minutes(p.get("exam_end"), "11:30")
        if not (8 * 60 <= start < end <= 12 * 60):
            raise ValueError("exam times must satisfy 08:00 <= start < end <= 12:00")
        return {"exam_start": f"{start // 60:02d}:{start % 60:02d}",
                "exam_end": f"{end // 60:02d}:{end % 60:02d}"}
    if kind == "outing":
        start = _hm_minutes(p.get("start"), "10:00")
        end = _hm_minutes(p.get("end"), "18:00")
        if not (8 * 60 <= start < 12 * 60 and 13 * 60 < end <= 18 * 60):
            raise ValueError(
                "outing times must satisfy 08:00 <= start < 12:00 and 13:00 < end <= 18:00")
        return {"start": f"{start // 60:02d}:{start % 60:02d}",
                "end": f"{end // 60:02d}:{end % 60:02d}"}
    return {}


def set_day_override(umo: str, local_date: dt.date | str, kind: str,
                     *, payload: dict | None = None, db_path=None) -> bool:
    """L4：考试/出门变体；kind=normal 删除 override。不触发发送。"""
    if not umo:
        return False
    d = (local_date.isoformat() if isinstance(local_date, dt.date)
         else str(local_date))
    if kind in ("exam", "outing"):
        payload = validate_day_payload(kind, payload)
    con = _conn(db_path)
    try:
        if kind == "normal":
            con.execute(
                "DELETE FROM yy_life_day_override WHERE umo=? AND date=?",
                (umo, d))
            con.commit()
            return True
        elif kind in ("exam", "outing"):
            cur = con.execute(
                "INSERT INTO yy_life_day_override(umo,date,kind,payload_json,updated_at)"
                " VALUES(?,?,?,?,?) ON CONFLICT(umo,date) DO UPDATE SET"
                " kind=excluded.kind, payload_json=excluded.payload_json,"
                " updated_at=excluded.updated_at",
                (umo, d, kind,
                 json.dumps(payload or {}, ensure_ascii=False),
                 dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")))
        else:
            return False
        con.commit()
        return cur.rowcount == 1
    finally:
        con.close()


def list_day_overrides(umo: str, *, start: dt.date | None = None,
                       end: dt.date | None = None, db_path=None) -> list[dict]:
    con = _conn(db_path)
    try:
        sql = "SELECT date,kind,payload_json,updated_at FROM yy_life_day_override WHERE umo=?"
        args: list = [umo]
        if start:
            sql += " AND date>=?"
            args.append(start.isoformat())
        if end:
            sql += " AND date<=?"
            args.append(end.isoformat())
        sql += " ORDER BY date"
        return [{"date": r[0], "kind": r[1],
                 "payload": json.loads(r[2] or "{}"), "updated_at": r[3]}
                for r in con.execute(sql, args)]
    finally:
        con.close()


# F5：必需活动一定发生；休闲活动按事件 id 的种子决定（10% 没做成）
_ESSENTIAL_TITLES = {"睡觉", "上课", "午饭", "晚饭", "练习", "复习", "考试"}
_LEISURE_MISS_RATE = 0.10


def resolve_outcome(event_id: str, title: str, told_at: str | None = None,
                    told_kind: str | None = None) -> str:
    """一个已经过去的虚拟活动，最终算 done 还是 missed。

    规则要**与读取频率无关**：只看事件自身（id/标题）和"机器人说到了哪一步"。
    P0-B02：讲计划/说正在做 != 后来完成；只有 result 类 told 才能直接 done。
    """
    if told_at and told_kind == "result":
        return "done"                      # 说过结果，不能回头说没做
    if (title or "") in _ESSENTIAL_TITLES:
        return "done"
    r = random.Random(f"{event_id}:outcome").random()
    return "missed" if r < _LEISURE_MISS_RATE else "done"


CANON_FILE = Path(os.environ.get("DEMO_CANON")
                      or r"D:/demo\ops\life\canon.jsonl")
_CANON_CACHE: dict | None = None
_CANON_MTIME: float = 0.0


def _canon() -> dict:
    """读已确认的正典；DEMO_CANON=0 表示关闭（测试用）。"""
    global _CANON_CACHE, _CANON_MTIME
    _env = os.environ.get("DEMO_CANON")
    if _env == "0":
        _CANON_CACHE = {}
        return _CANON_CACHE
    target = Path(_env) if _env else CANON_FILE
    try:
        _mt = target.stat().st_mtime
    except OSError:
        _mt = 0.0
    if _CANON_CACHE is not None and _mt == _CANON_MTIME:
        return _CANON_CACHE
    _CANON_MTIME = _mt
    out: dict = {}
    try:
        for line in target.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("status") == "confirmed" and rec.get("value"):
                out[rec["predicate"]] = rec
    except Exception:  # noqa: BLE001
        out = {}
    _CANON_CACHE = out
    return out


_WEATHER_CACHE: tuple | None = None


def _canon_spec(predicate: str) -> dict:
    rec = _canon().get(predicate) or {}
    spec = rec.get("spec")
    return spec if isinstance(spec, dict) else {}


_ACTIVITY_RATE = {
    "早饭": 0.06, "午饭": 0.06, "晚饭": 0.06,
    "上课": -0.08, "练习": -0.08, "复习": -0.06, "考试": -0.10,
    "打崩铁": -0.03, "看番": -0.03, "游戏": -0.03, "番剧": -0.03,
    "出门": -0.02,
}
_RATE_DEFAULT = 0.02


def _energy_rate_for(con, umo: str, start_utc: dt.datetime, end_utc: dt.datetime,
                     fallback_title: str) -> float:
    """按事件重叠时长加权，避免"推进频率"改变精力。"""
    try:
        rows = con.execute(
            "SELECT title,start_at,end_at FROM yy_life_event WHERE umo=?"
            " AND end_at>? AND start_at<?",
            (umo, _iso(start_utc), _iso(end_utc))).fetchall()
    except sqlite3.OperationalError:
        rows = []
    total = 0.0
    for title, s, e in rows:
        s2 = max(_parse(s), start_utc)
        e2 = min(_parse(e), end_utc)
        h = max(0.0, (e2 - s2).total_seconds() / 3600.0)
        total += _ACTIVITY_RATE.get(title, _RATE_DEFAULT) * h
    if not rows:
        h = max(0.0, (end_utc - start_utc).total_seconds() / 3600.0)
        total = _ACTIVITY_RATE.get(fallback_title, _RATE_DEFAULT) * h
    return total


def _energy_for(local: dt.datetime) -> float:
    h = local.hour + local.minute / 60
    if SLEEP_START_H <= h < SLEEP_END_H:
        return 0.30
    if SLEEP_END_H <= h < 13:
        return 0.75
    if 13 <= h < 18:
        return 0.65
    if 18 <= h < 22:
        return 0.55
    return 0.35


# ---------- 推进 ----------
def advance(umo: str, now: dt.datetime | None = None, db_path=None) -> dict:
    """把状态惰性推进到 now；返回当前 state。"""
    tz = _tz()
    now_utc = now or dt.datetime.now(dt.timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=dt.timezone.utc)
    local = now_utc.astimezone(tz)
    con = _conn(db_path)
    try:
        # P1-11：同一 umo 的推进串行化，避免 SELECT 后 UPSERT 丢版本/精力。
        # 异常时 finally close 会回滚未提交事务。
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT version,tz,last_advance,energy FROM yy_life_state WHERE umo=?",
            (umo,)).fetchone()
        version = (row[0] if row else 0) + 1
        last = _parse(row[2]) if row else None
        if last and now_utc <= last:
            now_utc = last  # 防倒拨：不往回走
            # L1/A7：回拨后必须用有效时间重算 local，否则活动是 23:00、精力却是 11:00。
            local = now_utc.astimezone(tz)
        _ensure_events(con, umo, tz, local.date())

        now_s = _iso(now_utc)
        # 已结束：active→done；planned→missed（离线没赶上，不假装做过）
        # F4：收尾用 <=（含边界），与"选择"的 end_at>now 组成半开区间，
        #     否则 now==end_at 那一刻旧事件未收尾、新事件已被选中 → 双 active。
        # F5：结算改成逐条、按种子（resolve_outcome），不再区分 active/planned——
        #     否则"密集读"和"稀疏读"的终态会不一致（实测 45% 事件受影响）。
        due = con.execute(
            "SELECT event_id,title,told_at,told_kind FROM yy_life_event"
            " WHERE umo=? AND status IN ('planned','active')"
            " AND end_at IS NOT NULL AND end_at<=?",
            (umo, now_s)).fetchall()
        for eid, title, told, told_kind in due:
            _ns = resolve_outcome(eid, title, told, told_kind)
            con.execute(
                "UPDATE yy_life_event SET status=?"
                " WHERE event_id=? AND status IN ('planned','active')",
                (_ns, eid))
            con.execute(
                "UPDATE yy_life_detail SET outcome=CASE WHEN ?='done' THEN 'completed'"
                " WHEN ?='missed' THEN 'cancelled' ELSE outcome END"
                " WHERE event_id=? AND outcome IN ('planned','completed','cancelled')",
                (_ns, _ns, eid))
        # L5：虚拟细节 7 天后软遗忘；事实/承诺不在这里，不受影响。
        cutoff = (now_utc - dt.timedelta(days=7)).isoformat(timespec="seconds")
        con.execute(
            "UPDATE yy_life_event SET status='forgotten', forgotten_at=?"
            " WHERE umo=? AND source_type='virtual' AND status<>'forgotten'"
            " AND told_at IS NULL AND COALESCE(end_at,start_at)<?",
            (now_s, umo, cutoff))
        # B18：考试/出门等重要事件自动留摘要（只留摘要，不无限留细节）
        try:
            _imp = con.execute(
                "SELECT e.event_id,e.kind,d.detail_json FROM yy_life_event e"
                " JOIN yy_life_detail d ON d.event_id=e.event_id"
                " WHERE e.umo=? AND e.status='done' AND e.kind IN ('exam','outing')"
                " ORDER BY e.end_at DESC LIMIT 8", (umo,)).fetchall()
            for _eid, _kind, _raw in _imp:
                _txt = ""
                try:
                    _txt = (json.loads(_raw or "{}") or {}).get("text", "")
                except (TypeError, ValueError):
                    pass
                con.execute(
                    "INSERT OR IGNORE INTO yy_life_summary(summary_id,umo,date,kind,"
                    "importance,text,created_at) VALUES(?,?,?,?,?,?,?)",
                    ("sum:" + _eid, umo, (_eid.split(":")[1] if ":" in _eid else ""),
                     _kind, 3, _txt or _kind,
                     dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")))
        except sqlite3.OperationalError:
            pass
        # 当前事件：开始<=now<结束，取最晚开始
        cur = con.execute(
            "SELECT event_id,title,place_id,start_at,end_at FROM yy_life_event"
            " WHERE umo=? AND status IN ('planned','active')"
            " AND start_at<=? AND (end_at IS NULL OR end_at>?)"
            " ORDER BY start_at DESC LIMIT 1",
            (umo, now_s, now_s)).fetchone()
        if cur:
            eid, title, place, start_at, end_at = cur
            con.execute(
                "UPDATE yy_life_event SET status='active', version=version"
                " WHERE event_id=?", (eid,))
        else:
            eid = title = place = start_at = end_at = None
        base = _energy_for(local)
        prev_e = float(row[3]) if row and len(row) > 3 and row[3] is not None else base
        _k = title or ""
        _hours = 0.0
        _rate_total = 0.0
        try:
            _prev_ts = row[2] if row and len(row) > 2 else None
            _pt = _parse(_prev_ts) if _prev_ts else None
            if _pt is not None:
                _hours = max(0.0, min(12.0, (now_utc - _pt).total_seconds() / 3600.0))
                _rate_total = _energy_rate_for(con, umo, _pt, now_utc, _k)
        except Exception:  # noqa: BLE001
            _hours, _rate_total = 0.0, 0.0
        if _k == "睡觉":
            base = min(base, 0.30)
        energy = prev_e + (base - prev_e) * min(1.0, _hours / 3.0) + _rate_total
        energy = max(0.20, min(0.95, energy))
        delta = round(energy - prev_e, 3)
        reason = f"{_k or '空档'} / {local.strftime('%H:%M')} ({delta:+.2f})"
        con.execute(
            "INSERT INTO yy_life_state(umo,version,tz,local_time,activity_id,place_id,"
            "energy,energy_reason,energy_delta,energy_baseline,last_advance,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(umo) DO UPDATE SET version=excluded.version, tz=excluded.tz,"
            " local_time=excluded.local_time, activity_id=excluded.activity_id,"
            " place_id=excluded.place_id, energy=excluded.energy,"
            " energy_reason=excluded.energy_reason, energy_delta=excluded.energy_delta,"
            " energy_baseline=excluded.energy_baseline,"
            " last_advance=excluded.last_advance, updated_at=excluded.updated_at",
            (umo, version, str(tz), local.isoformat(timespec="seconds"), eid, place,
             energy, reason, delta, base, now_s, now_s))
        con.commit()
        return {
            "umo": umo, "version": version, "now": now_utc,
            "local": local, "tz": str(tz),
            "event_id": eid, "title": title, "place": place,
            "start_at": start_at, "end_at": end_at, "energy": energy,
            "energy_reason": reason, "energy_delta": delta,
            "energy_baseline": base,
        }
    finally:
        con.close()


def get_state(umo: str, db_path=None) -> dict | None:
    con = _conn(db_path)
    try:
        row = con.execute(
            "SELECT umo,version,tz,local_time,activity_id,place_id,energy,last_advance"
            " FROM yy_life_state WHERE umo=?", (umo,)).fetchone()
    finally:
        con.close()
    if not row:
        return None
    keys = ("umo", "version", "tz", "local_time", "activity_id", "place_id",
            "energy", "last_advance")
    return dict(zip(keys, row, strict=False))


_EVENT_KEYS = ("event_id", "umo", "kind", "title", "start_at", "end_at",
               "occurred_at", "told_at", "forgotten_at", "status", "place_id",
               "source_type", "source_ref", "message_id", "task_id",
               "payload_json", "created_at", "version")


def event_by_id(event_id: str, db_path=None) -> dict | None:
    """L2：按稳定 event_id 找回事件；重放/摘要用。"""
    con = _conn(db_path)
    try:
        row = con.execute(
            "SELECT " + ",".join(_EVENT_KEYS) + " FROM yy_life_event"
            " WHERE event_id=?", (event_id,)).fetchone()
    finally:
        con.close()
    return dict(zip(_EVENT_KEYS, row, strict=False)) if row else None


def events_for_source(*, task_id: str | None = None,
                      message_id: str | None = None, limit: int = 20,
                      db_path=None) -> list[dict]:
    """L2：按 task_id/message_id 找事件，不用正文当 key。"""
    if not task_id and not message_id:
        return []
    where = []
    args: list = []
    if task_id:
        where.append("task_id=?")
        args.append(task_id)
    if message_id:
        where.append("message_id=?")
        args.append(message_id)
    con = _conn(db_path)
    try:
        rows = con.execute(
            "SELECT " + ",".join(_EVENT_KEYS) + " FROM yy_life_event"
            " WHERE " + " OR ".join(where) + " ORDER BY start_at DESC LIMIT ?",
            (*args, limit)).fetchall()
    finally:
        con.close()
    return [dict(zip(_EVENT_KEYS, row, strict=False)) for row in rows]


def _told_schema(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS yy_life_told_pending ("
        " umo TEXT NOT NULL, event_id TEXT NOT NULL, text TEXT NOT NULL,"
        " message_id TEXT, kind TEXT DEFAULT 'context', created_at TEXT NOT NULL,"
        " PRIMARY KEY (umo, event_id))")
    cols = {r[1] for r in con.execute("PRAGMA table_info(yy_life_told_pending)")}
    for name in ("message_id", "kind"):
        if name not in cols:
            con.execute(f"ALTER TABLE yy_life_told_pending ADD COLUMN {name} TEXT")


def note_told_pending(umo: str, event_id: str, text: str, db_path=None,
                      *, message_id: str | None = None,
                      kind: str = "context") -> bool:
    """生成阶段记一笔“可能要说这件事”；**不是** told。

    P0-B02：必须带这件事件的 event_id；passive 还要带本轮入站 message_id，
    发送成功后只在该轮内核对，避免“今天没去上课”被当成“上课”。
    """
    if not umo or not event_id:
        return False
    con = _conn(db_path)
    try:
        _told_schema(con)
        con.execute(
            "INSERT INTO yy_life_told_pending(umo,event_id,text,message_id,kind,"
            "created_at) VALUES(?,?,?,?,?,?)"
            " ON CONFLICT(umo,event_id) DO UPDATE SET text=excluded.text,"
            " message_id=excluded.message_id, kind=excluded.kind,"
            " created_at=excluded.created_at",
            (umo, event_id, (text or "").strip(), message_id, kind,
             dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")))
        con.commit()
        return True
    finally:
        con.close()


def _told_candidates(title: str, payload_json: str | None) -> list[str]:
    out = [title or ""]
    try:
        payload = json.loads(payload_json or "{}")
    except (TypeError, ValueError):
        payload = {}
    if isinstance(payload, dict):
        for key in ("menu", "piece", "detail", "place", "note"):
            v = payload.get(key)
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
    return [x for x in out if len(x) >= 2]


def _mentions_event(text: str, title: str, payload_json: str | None) -> bool:
    """严格：回复真的提到该事件的标题/细节，且不是否定。"""
    t = text or ""
    for idx, w in enumerate(_told_candidates(title, payload_json)):
        probes = [w]
        # 细节允许“芙莉莲”这种短片段；标题仍要求完整。
        if idx > 0 and len(w) >= 3:
            probes += [w[i:i + 3] for i in range(len(w) - 2)]
        for probe in probes:
            start = 0
            while True:
                i = t.find(probe, start)
                if i < 0:
                    break
                head = t[max(0, i - 4):i]
                if any(x in head for x in ("不", "没", "未", "别", "取消")):
                    start = i + 1
                    continue
                return True
    return False


def commit_told(umo: str, text: str, *, task_id: str | None = None,
                message_id: str | None = None, event_id: str | None = None,
                db_path=None, max_age_s: int = 1800) -> str | None:
    """发送成功后调用：只提交“真的提到事件、且未否定”的 pending。

    P0-B02：没有前缀/16 字/`*` 宽泛匹配；事件 ID 或本轮 message_id 绑定。
    told 只表示“说给用户听了”，不等于“事情完成”。
    """
    t = (text or "").strip()
    if not umo or not t:
        return None
    con = _conn(db_path)
    try:
        _told_schema(con)
        sql = ("SELECT event_id,text,message_id,kind,created_at"
               " FROM yy_life_told_pending WHERE umo=?")
        args: list = [umo]
        if event_id:
            sql += " AND event_id=?"
            args.append(event_id)
        elif message_id:
            sql += " AND message_id=?"
            args.append(message_id)
        try:
            rows = con.execute(sql, args).fetchall()
        except sqlite3.OperationalError:
            return None
        now = dt.datetime.now(dt.timezone.utc)
        best = None
        for eid, ptext, mid, kind, created in rows:
            try:
                age = (now - dt.datetime.fromisoformat(created)).total_seconds()
            except Exception:  # noqa: BLE001
                age = 0
            if age > max_age_s:
                continue
            ev = con.execute(
                "SELECT title,payload_json,status FROM yy_life_event WHERE event_id=?",
                (eid,)).fetchone()
            if not ev:
                continue
            if not _mentions_event(t, ev[0], ev[1]):
                continue
            # 说过计划/进展/结果分档；只有 result 才允许 resolve_outcome 直接 done。
            status = ev[2] or ""
            told_kind = "result" if status in ("done", "missed", "cancelled") else (
                kind if kind in ("plan", "progress", "result") else "progress")
            con.execute(
                "UPDATE yy_life_event SET told_at=COALESCE(told_at,?),"
                " told_kind=COALESCE(told_kind,?), task_id=COALESCE(task_id,?),"
                " message_id=COALESCE(message_id,?) WHERE event_id=?",
                (now.isoformat(timespec="seconds"), told_kind, task_id,
                 message_id or mid, eid))
            con.execute("DELETE FROM yy_life_told_pending WHERE umo=? AND event_id=?",
                        (umo, eid))
            best = eid
            break
        con.commit()
        return best
    finally:
        con.close()


def mark_event_told(event_id: str, *, task_id: str | None = None,
                    message_id: str | None = None, told_kind: str = "progress",
                    db_path=None) -> bool:
    """L2：事件“已告诉用户”与“发生”分开；told_at 只写一次。

    P0-B02：这是底层入口，必须带送达证据（task_id 或 message_id）；
    没有证据的调用只在显式隔离测试里放行，避免“注入上下文就标 told”。

    2026-10-28 备注：**发送链路已不再直接调用它**。
    现在是两段式：生成时 `note_told_pending()` 登记“可能要说”，
    真正送达后由 `commit_told()` 提交（要过"句子里确实提到这件事"的校验）。
    本函数保留给测试和回滚，生产路径不要新增调用。
    """
    if not (task_id or message_id) and os.environ.get(
            "DEMO_ALLOW_NO_LEDGER", "") not in ("1", "true", "yes"):
        return False
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    con = _conn(db_path)
    try:
        cur = con.execute(
            "UPDATE yy_life_event SET told_at=COALESCE(told_at,?),"
            " told_kind=COALESCE(told_kind,?), task_id=COALESCE(task_id,?),"
            " message_id=COALESCE(message_id,?)"
            " WHERE event_id=?", (now, told_kind, task_id, message_id, event_id))
        con.commit()
        return cur.rowcount == 1
    finally:
        con.close()


QUIET_START_H = SLEEP_START_H
QUIET_END_H = SLEEP_END_H
TRUSTED_PLACE_SOURCES = {"user_statement", "schedule"}


def _as_local(now: dt.datetime | None, tz: ZoneInfo) -> dt.datetime:
    n = now or dt.datetime.now(dt.timezone.utc)
    if n.tzinfo is None:
        n = n.replace(tzinfo=dt.timezone.utc)
    return n.astimezone(tz)


def is_quiet(now: dt.datetime | None = None, tz: ZoneInfo | None = None) -> bool:
    """L5：本地 00:30–07:00 是睡眠静默；被动消息不受此限制。"""
    local = _as_local(now, tz or _tz())
    h = local.hour + local.minute / 60
    return QUIET_START_H <= h < QUIET_END_H


def proactive_allowed(now: dt.datetime | None = None, *, urgent: bool = False,
                      tz: ZoneInfo | None = None) -> bool:
    """L5：静默期只允许用户明确要求的紧急提醒。"""
    return True if urgent else not is_quiet(now, tz)


def delay_allowed(created_at: str | dt.datetime, now: dt.datetime | None = None,
                  max_delay_h: float = 2.0) -> bool:
    """L5：主动闲聊延迟超过 max_delay_h 就丢弃，不伪装成刚发生。"""
    created = _parse(created_at) if isinstance(created_at, str) else created_at
    if created is None:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=dt.timezone.utc)
    n = now or dt.datetime.now(dt.timezone.utc)
    if n.tzinfo is None:
        n = n.replace(tzinfo=dt.timezone.utc)
    return (n - created).total_seconds() <= max_delay_h * 3600


def _utc_now(now: dt.datetime | None = None) -> dt.datetime:
    n = now or dt.datetime.now(dt.timezone.utc)
    if n.tzinfo is None:
        n = n.replace(tzinfo=dt.timezone.utc)
    return n.astimezone(dt.timezone.utc)


def queue_delayed(umo: str, text: str, *, delay_s: float = 0,
                  created_at: dt.datetime | None = None,
                  db_path=None) -> str | None:
    """L5：把主动消息放入 outbox；delay_s 秒后才 due。不发送。"""
    if not umo or not (text or "").strip():
        return None
    created = _utc_now(created_at)
    due = created + dt.timedelta(seconds=max(0.0, float(delay_s)))
    did = uuid.uuid4().hex
    con = _conn(db_path)
    try:
        con.execute(
            "INSERT INTO yy_life_delay(id,umo,text,created_at,due_at,status,updated_at)"
            " VALUES(?,?,?,?,?, 'pending', ?)",
            (did, umo, text.strip(), created.isoformat(timespec="seconds"),
             due.isoformat(timespec="seconds"), created.isoformat(timespec="seconds")))
        con.commit()
        return did
    finally:
        con.close()


def expire_delayed(now: dt.datetime | None = None, *, max_delay_h: float = 2.0,
                   db_path=None) -> int:
    """L5：pending 超过 max_delay_h 仍未发，标 dropped，绝不补发。"""
    n = _utc_now(now)
    cutoff = (n - dt.timedelta(hours=max_delay_h)).isoformat(timespec="seconds")
    con = _conn(db_path)
    try:
        cur = con.execute(
            "UPDATE yy_life_delay SET status='dropped', updated_at=?"
            " WHERE status='pending' AND created_at<?",
            (n.isoformat(timespec="seconds"), cutoff))
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def due_delayed(now: dt.datetime | None = None, *, max_delay_h: float = 2.0,
                limit: int = 20, db_path=None) -> list[dict]:
    """L5：返回已 due 的 outbox 行；不发送、不改 sent。"""
    n = _utc_now(now)
    expire_delayed(n, max_delay_h=max_delay_h, db_path=db_path)
    con = _conn(db_path)
    try:
        rows = con.execute(
            "SELECT id,umo,text,created_at,due_at FROM yy_life_delay"
            " WHERE status='pending' AND due_at<=? ORDER BY due_at LIMIT ?",
            (n.isoformat(timespec="seconds"), max(1, int(limit)))).fetchall()
        return [{"id": r[0], "umo": r[1], "text": r[2],
                 "created_at": r[3], "due_at": r[4]} for r in rows]
    finally:
        con.close()


def mark_delayed(delay_id: str, status: str = "sent",
                 *, task_id: str | None = None, db_path=None) -> bool:
    if status not in ("sent", "dropped"):
        return False
    con = _conn(db_path)
    try:
        cur = con.execute(
            "UPDATE yy_life_delay SET status=?, task_id=?, updated_at=?"
            " WHERE id=? AND status='pending'",
            (status, task_id, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
             delay_id))
        con.commit()
        return cur.rowcount == 1
    finally:
        con.close()


def forget_virtual_events(now: dt.datetime | None = None, *, ttl_days: int = 7,
                          umo: str | None = None, db_path=None) -> int:
    """L5：只软遗忘 virtual 生活细节；保留 event_id 与审计，可 restore。"""
    now_utc = now or dt.datetime.now(dt.timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=dt.timezone.utc)
    cutoff = (now_utc - dt.timedelta(days=ttl_days)).isoformat(timespec="seconds")
    now_s = now_utc.isoformat(timespec="seconds")
    sql = ("UPDATE yy_life_event SET status='forgotten', forgotten_at=?"
           " WHERE source_type='virtual' AND status<>'forgotten'"
           " AND told_at IS NULL AND COALESCE(end_at,start_at)<?")
    args: list = [now_s, cutoff]
    if umo:
        sql += " AND umo=?"
        args.append(umo)
    con = _conn(db_path)
    try:
        cur = con.execute(sql, args)
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def restore_event(event_id: str, db_path=None) -> bool:
    """L5：遗忘可恢复；恢复为 planned，不直接伪造成已发生。"""
    con = _conn(db_path)
    try:
        cur = con.execute(
            "UPDATE yy_life_event SET status='planned', forgotten_at=NULL"
            " WHERE event_id=? AND status='forgotten'", (event_id,))
        con.commit()
        return cur.rowcount == 1
    finally:
        con.close()


def set_place(umo: str, place_id: str, source_type: str, confidence: float,
              *, ttl_hours: float = 8.0, now: dt.datetime | None = None,
              db_path=None, subject: str = "her") -> bool:
    """L6：只接受可信来源的地点，写本地短期证据；低置信度不覆盖。"""
    if not umo or not place_id or source_type not in TRUSTED_PLACE_SOURCES:
        return False
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        return False
    if confidence < 0.7:
        return False
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    now = now.astimezone(dt.timezone.utc)
    expires = (now + dt.timedelta(hours=ttl_hours)).isoformat(timespec="seconds")
    con = _conn(db_path)
    try:
        con.execute(
            "INSERT INTO yy_life_place(umo,subject,place_id,source_type,confidence,"
            "updated_at,expires_at) VALUES(?,?,?,?,?,?,?)"
            " ON CONFLICT(umo,subject) DO UPDATE SET place_id=excluded.place_id,"
            " source_type=excluded.source_type, confidence=excluded.confidence,"
            " updated_at=excluded.updated_at, expires_at=excluded.expires_at",
            (umo, subject, place_id, source_type, confidence,
             now.isoformat(timespec="seconds"), expires))
        con.commit()
        return True
    finally:
        con.close()


def last_known_place(umo: str, *, within_hours: float = 24.0, db_path=None,
                     subject: str = "user") -> tuple[str, float] | None:
    """过期也返回最后一次已知位置（带小时数），供"用户之前说用户在X"这种句式。"""
    try:
        con = _conn(db_path)
        try:
            row = con.execute(
                "SELECT place_id,updated_at FROM yy_life_place WHERE umo=? AND subject=?",
                (umo, subject)).fetchone()
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        return None
    if not row:
        return None
    age = 0.0
    try:
        _t = _parse(row[1])
        if _t:
            age = (dt.datetime.now(dt.timezone.utc) - _t).total_seconds() / 3600.0
            # 记录时间在未来（时钟偏差/测试用固定时间）就不算数
            if age < -0.1:
                return None
    except Exception:  # noqa: BLE001
        pass
    if age > within_hours:
        return None
    return str(row[0]), round(age, 1)


def current_place(umo: str, now: dt.datetime | None = None, db_path=None,
                  subject: str = "her") -> str | None:
    """L6：过期/低置信度/不可信来源都返回 None，调用方不得硬说地点。"""
    n = now or dt.datetime.now(dt.timezone.utc)
    if n.tzinfo is None:
        n = n.replace(tzinfo=dt.timezone.utc)
    con = _conn(db_path)
    try:
        row = con.execute(
            "SELECT place_id,source_type,confidence,expires_at FROM yy_life_place"
            " WHERE umo=? AND subject=?", (umo, subject)).fetchone()
    finally:
        con.close()
    if not row:
        return None
    place, source, confidence, expires = row
    if source not in TRUSTED_PLACE_SOURCES or float(confidence or 0) < 0.7:
        return None
    exp = _parse(expires)
    if exp is not None and exp < n:
        return None
    return place


def clear_place(umo: str, db_path=None, subject: str = "her") -> bool:
    """P1-B06：按 (umo, subject) 删除，清机器人的位置不能带走用户的。"""
    con = _conn(db_path)
    try:
        cur = con.execute(
            "DELETE FROM yy_life_place WHERE umo=? AND subject=?", (umo, subject))
        con.commit()
        return cur.rowcount >= 1
    finally:
        con.close()


def prune_place(umo: str | None = None, now: dt.datetime | None = None,
                db_path=None) -> int:
    """L6：清掉已过期的地点证据；不改变当前输出（current_place 也会判过期）。"""
    n = _utc_now(now)
    con = _conn(db_path)
    try:
        sql = "DELETE FROM yy_life_place WHERE expires_at IS NOT NULL AND expires_at<?"
        args: list = [n.isoformat(timespec="seconds")]
        if umo:
            sql += " AND umo=?"
            args.append(umo)
        cur = con.execute(sql, args)
        con.commit()
        return cur.rowcount
    finally:
        con.close()


_PLACE_TEXT_RE = re.compile(
    r"(?<![用户机器人你])(?:我)?(?:在|到|回|去|待|呆)"
    r"(家里|家|学校|练习室|教室|车上|外面|图书馆|宿舍|公司|医院|路上|餐厅|咖啡店)")
_PLACE_PAST = ("昨天", "前天", "上次", "以前", "之前", "那时", "小时候", "去年")
_PLACE_FUTURE = ("明天", "后天", "下周", "下个月", "等会", "等会儿", "晚点", "一会儿")


def observe_place(umo: str, text: str, now: dt.datetime | None = None,
                  db_path=None) -> str | None:
    """L6 / P1-B06：用户亲口说的**当前**位置才记；否定/过去/将来都不算。"""
    t = text or ""
    m = _PLACE_TEXT_RE.search(t)
    if not m:
        return None
    if _negated_before(t, m.start()):
        # “我不在学校”不是新位置；同时把旧位置作废，避免继续拿学校当当前地点。
        clear_place(umo, db_path=db_path, subject="user")
        return None
    head = t[max(0, m.start() - 8):m.start()]
    if any(w in head for w in _PLACE_PAST):
        return None
    if any(w in head for w in _PLACE_FUTURE):
        return None
    place = m.group(1)
    if place == "家里":
        place = "家"
    if set_place(umo, place, "user_statement", 0.9, ttl_hours=2,
                 now=now, db_path=db_path, subject="user"):
        return place
    return None


def location_claim(place_id: str | None, source_type: str | None,
                   confidence: float, *, threshold: float = 0.7) -> str | None:
    """L6：没有可信来源/置信度就不说地点。"""
    if not place_id or source_type not in TRUSTED_PLACE_SOURCES:
        return None
    try:
        if float(confidence) < threshold:
            return None
    except (TypeError, ValueError):
        return None
    return place_id


def reminder_allowed(now: dt.datetime | None = None, last_at: str | dt.datetime | None = None,
                     *, enabled: bool | None = None,
                     per_day: int = 1, tz: ZoneInfo | None = None) -> bool:
    """L6：提醒默认关；同一本地日期最多 per_day 次（最小版按 1 次）。"""
    if enabled is None:
        enabled = os.environ.get("DEMO_LIFE_REMINDER", "0").strip() not in (
            "0", "", "false", "False", "off", "no")
    if not enabled or per_day < 1:
        return False
    local = _as_local(now, tz or _tz())
    if last_at:
        last = _parse(last_at) if isinstance(last_at, str) else last_at
        if last is not None:
            if last.tzinfo is None:
                last = last.replace(tzinfo=dt.timezone.utc)
            if last.astimezone(local.tzinfo).date() == local.date():
                return False
    return True


def practice_reminder(umo: str, now: dt.datetime | None = None,
                      db_path=None) -> dict | None:
    """L6：只在“正在练习、今天没提醒过、开关打开”时给一次 cue。

    不发送消息；发送端拿到 event_id 后自己发送并 mark_event_told。
    """
    if os.environ.get("DEMO_LIFE_REMINDER", "0").strip() in (
            "0", "", "false", "False", "off", "no"):
        return None
    if not enabled() or shadow():
        return None
    if is_quiet(now):
        return None
    st = advance(umo, now=now, db_path=db_path)
    if st.get("title") != "练习" or not st.get("event_id"):
        return None
    ev = event_by_id(st["event_id"], db_path=db_path)
    if not ev or ev.get("told_at"):
        return None
    con = _conn(db_path)
    try:
        last = con.execute(
            "SELECT MAX(told_at) FROM yy_life_event"
            " WHERE umo=? AND title='练习' AND told_at IS NOT NULL",
            (umo,)).fetchone()[0]
    finally:
        con.close()
    if not reminder_allowed(now=st.get("now"), last_at=last):
        return None
    return {"event_id": st["event_id"], "text": "练习中 晚点找你（",
            "now": st.get("now")}


def remember_topic(umo: str, event_id: str, db_path=None,
                   now: dt.datetime | None = None) -> bool:
    """记住“你们刚才在聊哪件事”，供“后来呢/忙完没”指代。

    P1-B07：时间由调用方传入，模拟时间不会被机器实时时间污染。
    """
    if not umo or not event_id:
        return False
    ts = _utc_now(now).isoformat(timespec="seconds")
    con = _conn(db_path)
    try:
        con.execute(
            "INSERT INTO yy_life_topic(umo,event_id,updated_at) VALUES(?,?,?)"
            " ON CONFLICT(umo) DO UPDATE SET event_id=excluded.event_id,"
            " updated_at=excluded.updated_at",
            (umo, event_id, ts))
        con.commit()
        return True
    finally:
        con.close()


def last_topic(umo: str, *, max_age_h: float = 12, db_path=None,
               now: dt.datetime | None = None) -> str | None:
    con = _conn(db_path)
    try:
        row = con.execute(
            "SELECT event_id,updated_at FROM yy_life_topic WHERE umo=?",
            (umo,)).fetchone()
    finally:
        con.close()
    if not row:
        return None
    ts = _parse(row[1])
    if ts is None:
        return None
    if (_utc_now(now) - ts).total_seconds() > max_age_h * 3600:
        return None
    return row[0]


def _hm(value: str | None, tz) -> str:
    d = _parse(value)
    return d.astimezone(tz).strftime("%H:%M") if d else ""


def _outcome_word(status: str) -> str:
    return {"done": "做完了", "missed": "没做成", "cancelled": "取消了"}.get(
        status, "结束了")


def birthday_today(local_date: dt.date) -> bool:
    """正典里确认过生日，且今天是那天。"""
    spec = _canon_spec("birthday")
    try:
        m = int(spec.get("month") or 0)
        d = int(spec.get("day") or 0)
    except (TypeError, ValueError):
        return False
    return bool(m and d) and local_date.month == m and local_date.day == d
_TOPIC_TITLES = (
    ("练习", ("练习", "演奏", "琴")),
    ("午饭", ("午饭", "中饭", "中午吃")),
    ("晚饭", ("晚饭", "晚上吃")),
    ("上课", ("上课", "课")),
    ("睡觉", ("睡觉", "睡了")),
    ("游戏", ("游戏", "打游戏")),
)


def _day_offset(text: str) -> int | None:
    if "前天" in text:
        return -2
    if "昨天" in text or "昨儿" in text:
        return -1
    if "今天" in text or "今儿" in text:
        return 0
    if "明天" in text or "明儿" in text:
        return 1
    if "上周" in text:
        return -7
    return None


def _topic_title(text: str) -> str | None:
    for title, words in _TOPIC_TITLES:
        if any(w in text for w in words):
            return title
    return None


def _day_event(umo: str, day: dt.date, title: str | None, tz: ZoneInfo,
               db_path=None) -> dict | None:
    start = dt.datetime.combine(day, dt.time(0, 0), tzinfo=tz)
    end = start + dt.timedelta(days=1)
    sql = ("SELECT " + ",".join(_EVENT_KEYS) + " FROM yy_life_event"
           " WHERE umo=? AND start_at>=? AND start_at<?")
    args: list = [umo, _iso(start), _iso(end)]
    if title:
        sql += " AND title=?"
        args.append(title)
    else:
        sql += " AND title NOT IN ('睡觉','空闲')"
    sql += " ORDER BY start_at DESC LIMIT 1"
    con = _conn(db_path)
    try:
        row = con.execute(sql, args).fetchone()
    finally:
        con.close()
    return dict(zip(_EVENT_KEYS, row, strict=False)) if row else None


def _query_focus(umo: str, text: str, st: dict, db_path=None) -> dict | None:
    """P1-B07：人物/日期/活动优先于上一话题。

    - “昨天练习怎么样” → 昨日的练习事件，而不是 last_topic；
    - “练习怎么样” → 最近一次练习；
    - “后来呢/忙完没” → 交给 last_topic。
    """
    t = (text or "").strip()
    if not t or not _about_her(t):
        return None
    tz = st["local"].tzinfo
    day_off = _day_offset(t)
    title = _topic_title(t)
    if day_off is not None:
        day = st["local"].date() + dt.timedelta(days=day_off)
        ev = _day_event(umo, day, title, tz, db_path=db_path)
        if ev:
            return ev
        if title:
            return None
    # 只有"在问"时才主动回顾旧事件；用户陈述自己的课时不该扯到机器人昨天的课
    _asks = any(w in t for w in ("吗", "呢", "怎么样", "?", "？", "如何", "是不是"))
    if title and not is_followup(t) and _asks:
        now_s = _iso(st["now"])
        con = _conn(db_path)
        try:
            row = con.execute(
                "SELECT " + ",".join(_EVENT_KEYS) + " FROM yy_life_event"
                " WHERE umo=? AND title=? AND start_at<=?"
                " ORDER BY start_at DESC LIMIT 1",
                (umo, title, now_s)).fetchone()
            if not row:
                row = con.execute(
                    "SELECT " + ",".join(_EVENT_KEYS) + " FROM yy_life_event"
                    " WHERE umo=? AND title=? AND start_at>?"
                    " ORDER BY start_at ASC LIMIT 1",
                    (umo, title, now_s)).fetchone()
        finally:
            con.close()
        if row:
            ev = dict(zip(_EVENT_KEYS, row, strict=False))
            _end = _parse(ev.get("end_at") or ev.get("start_at"))
            if _end:
                _delta = (st["now"] - _end).total_seconds()
                if not (-6 * 3600 <= _delta <= 6 * 3600):
                    return None
            return ev
    return None


def _focus_prefix(focus: dict, now_local: dt.datetime) -> str:
    start = _parse(focus.get("start_at"))
    if not start:
        return "你刚才"
    d = (now_local.date() - start.astimezone(now_local.tzinfo).date()).days
    if d == 1:
        return "你昨天"
    if d == 2:
        return "你前天"
    if d > 2:
        return "你之前"
    if d == 0:
        return "你今天"
    return "你明天"


def _life_mood_offset(umo: str, db_path=None) -> float:
    """生活事件对心情的影响：练习做成 +0.05、没做成 -0.05、出门 +0.08。"""
    off = 0.0
    try:
        con = _conn(db_path)
        try:
            day = dt.datetime.now().strftime("%Y-%m-%d")
            rows = con.execute(
                "SELECT kind,title,status FROM yy_life_event WHERE umo=?"
                " AND date(COALESCE(end_at,start_at))=?", (umo, day)).fetchall()
        finally:
            con.close()
        for kind, title, status in rows:
            if kind == "practice":
                off += 0.05 if status == "done" else (-0.05 if status == "missed" else 0.0)
            elif kind == "outing" and status == "done":
                off += 0.08
    except Exception:  # noqa: BLE001
        pass
    return max(-0.3, min(0.3, off))


def _weather_line() -> str:
    """ExampleCity天气（open-meteo，无 key），30 分钟缓存；失败静默。"""
    if os.environ.get("DEMO_WEATHER", "1").strip() in ("0", "", "false", "off"):
        return ""
    global _WEATHER_CACHE
    now = dt.datetime.now()
    if _WEATHER_CACHE and (now - _WEATHER_CACHE[0]).total_seconds() < 1800:
        return _WEATHER_CACHE[1]
    url = ("https://api.open-meteo.com/v1/forecast?latitude=31.23&longitude=121.47"
           "&current=temperature_2m,weather_code&timezone=Asia%2FShanghai")
    try:
        import json as _json
        import urllib.request as _url
        _op = _url.build_opener(_url.ProxyHandler({}))
        with _op.open(url, timeout=2.5) as r:
            d = _json.loads(r.read().decode())
        cur = d.get("current") or {}
        t = cur.get("temperature_2m")
        code = int(cur.get("weather_code") or 0)
        desc = ("晴" if code == 0 else "多云" if code in (1, 2) else "阴" if code == 3
                else "小雨" if code in (51, 53, 55, 61, 63, 65) else "雨" if code in (80, 81, 82)
                else "雪" if code in (71, 73, 75) else "")
        if t is None:
            return ""
        line = f"外面{desc or '天'} {round(float(t))}°C"
        _WEATHER_CACHE = (now, line)
        return line
    except Exception:  # noqa: BLE001
        _WEATHER_CACHE = (now, "")
        return ""


def sync_mood(umo: str, db_path=None) -> float:
    """把 mood_state 的效价同步进生活层 mood_valence。"""
    try:
        from astrbot.core.utils.mood_state import current as _cur
        v, _e = _cur(umo, db_path=db_path)
        v = max(-1.0, min(1.0, float(v)))
        # 生活事件也会影响心情（练习做成/没做成、出门）
        v = max(-1.0, min(1.0, v + _life_mood_offset(umo, db_path=db_path)))
    except Exception:  # noqa: BLE001
        return 0.0
    try:
        con = _conn(db_path)
        try:
            con.execute("UPDATE yy_life_state SET mood_valence=?, updated_at=?"
                        " WHERE umo=?",
                        (round(v, 3),
                         dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                         umo))
            con.commit()
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        pass
    return v


def life_context(umo: str, purpose: str = "passive",
                 now: dt.datetime | None = None, db_path=None,
                 text: str = "") -> dict:
    """给生成层的一小段状态；不调用模型，不发送消息。

    包含：当前活动 + 追问指向的刚才那件事 + 最近一条经历 + 下一项计划
    + 是否已经告诉过用户。目标不是写日记，是让“后来呢/忙完没”接得上。
    """
    st = advance(umo, now=now, db_path=db_path)
    local = st["local"]
    tz = local.tzinfo
    bits = [f"你那边现在是 {local.strftime('%Y-%m-%d %H:%M')}（{st['tz']}）"]
    _wx = ""
    if text and any(w in text for w in ("天气", "下雨", "出门", "带伞", "冷", "热", "外面")):
        _wx = _weather_line()
    if _wx:
        bits.append(_wx)
    _cur_end_hm = ""      # 当前活动的结束时刻（HH:MM），用于和"接下来"去重
    _his = None
    try:
        _his = current_place(umo, now=st["now"], db_path=db_path, subject="user")
    except Exception:  # noqa: BLE001
        _his = None
    _his_age = 0.0
    if not _his:
        _lk = last_known_place(umo, within_hours=24.0, db_path=db_path, subject="user")
        if _lk:
            _his, _his_age = _lk[0], float(_lk[1])
    override = current_place(umo, now=st["now"], db_path=db_path)
    used: set[str] = set()

    focus = _query_focus(umo, text, st, db_path=db_path)
    if focus is None and text and is_followup(text):
        fid = last_topic(umo, db_path=db_path, now=st["now"])
        if fid:
            candidate = event_by_id(fid, db_path=db_path)
            if candidate and candidate.get("umo") == umo:
                focus = candidate
    if focus and focus["event_id"] != st.get("event_id"):
        status = focus.get("status") or ""
        title = focus.get("title") or ""
        _start = _hm(focus.get("start_at"), tz)
        _end = _hm(focus.get("end_at"), tz)
        when = _end or _start
        prefix = _focus_prefix(focus, local)
        if status == "planned":
            bits.append(f"{prefix}要{title}" + (f"，{_start} 开始" if _start else ""))
        elif status == "active":
            bits.append(f"{prefix}在做{title}" + (f"，大约到 {when} 结束" if when else ""))
        else:
            bits.append(f"{prefix}在{title}" + (f"，{when} " if when else "")
                        + _outcome_word(status))
        used.add(focus["event_id"])

    if st["title"]:
        place = override or {"home": "家", "school": "学校"}.get(
            st["place"], st["place"] or "")
        _det = ""
        if st.get("event_id"):
            used.add(st["event_id"])
            _dd = detail_for(st["event_id"], db_path=db_path)
            _det = (_dd or {}).get("text") or ""
        if st["title"] == "空闲":
            bits.append(f"你现在在{place}闲着" if place else "你现在闲着")
        elif st["title"] == "睡觉":
            bits.append(f"你在{place}睡觉" if place else "你在睡觉")
        elif st["title"] in ("早饭", "午饭", "晚饭"):
            bits.append(f"你在{place}吃{st['title']}" if place
                        else f"你在吃{st['title']}")
        elif st["title"] == "上课" and _det:
            bits.append(f"你在{place}上{_det}" if place else f"你在上{_det}")
            _det = ""
        else:
            bits.append(f"你在{place}{st['title']}" if place
                        else f"你在{st['title']}")
        if _det:
            bits.append(_det)
        if st.get("end_at"):
            _end = _parse(st["end_at"])
            if _end:
                _cur_end_hm = _end.astimezone(tz).strftime("%H:%M")
                bits.append(f"大约到 {_cur_end_hm} 结束")
    else:
        bits.append(f"你现在在{override}，没什么特别的" if override
                    else "你现在在家，没什么特别的")

    # 最近一条做过/没做成的经历（不重复焦点/当前）
    try:
        con = _conn(db_path)
        try:
            recent = con.execute(
                "SELECT event_id,title,status,end_at FROM yy_life_event"
                " WHERE umo=? AND status IN ('done','missed')"
                " AND end_at IS NOT NULL AND end_at<=?"
                " ORDER BY end_at DESC LIMIT 4",
                (umo, _iso(st["now"]))).fetchall()
        finally:
            con.close()
        for eid, title, status, end_at in recent:
            if eid in used or title == "空闲":
                continue
            _word = ("起来了" if title == "睡觉" and status == "done"
                     else _outcome_word(status))
            bits.append(f"之前：{title}（{_hm(end_at, tz)} {_word}）")
            used.add(eid)
            break
    except Exception:  # noqa: BLE001
        pass

    # B18：重要摘要（只取 3 条，避免无限记忆）
    try:
        _sums = list_summaries(umo, days=30, min_importance=2, db_path=db_path)[:3]
        for x in _sums:
            bits.append(f"记得：{x['date']} {x['text']}")
    except Exception:  # noqa: BLE001
        pass

    # 下一项计划
    try:
        con = _conn(db_path)
        try:
            _now_s = _iso(st["now"])
            nxt = con.execute(
                "SELECT event_id,title,start_at FROM yy_life_event"
                " WHERE umo=? AND status IN ('planned','active') AND start_at>?"
                " AND title<>'空闲' ORDER BY start_at ASC LIMIT 1",
                (umo, _now_s)).fetchone()
            if not nxt:
                nxt = con.execute(
                    "SELECT event_id,title,start_at FROM yy_life_event"
                    " WHERE umo=? AND status IN ('planned','active') AND start_at>?"
                    " ORDER BY start_at ASC LIMIT 1",
                    (umo, _now_s)).fetchone()
        finally:
            con.close()
        if nxt:
            _nxt_hm = _hm(nxt[2], tz)
            if _cur_end_hm and _nxt_hm == _cur_end_hm:
                # 同一个时刻只出现一次：「大约到 07:30 结束；接下来 早饭」
                bits.append(f"接下来 {nxt[1]}")
            else:
                bits.append(f"接下来 {_nxt_hm} {nxt[1]}")
    except Exception:  # noqa: BLE001
        pass

    # 是否已经跟用户说过（当前/追问焦点）
    told_id = (focus or {}).get("event_id") or st.get("event_id")
    if told_id:
        e = event_by_id(told_id, db_path=db_path)
        if e and e.get("told_at"):
            bits.append("这件事你已经跟用户说过")

    energy = st["energy"]
    if energy < 0.35:
        bits.append("有点困")
    elif energy > 0.75:
        bits.append("挺精神")
    # 今天没做成的事（最多一条）：让机器人能自然说"今天没练成琴"
    try:
        _c2 = _conn(db_path)
        try:
            _miss = _c2.execute(
                "SELECT event_id,title FROM yy_life_event WHERE umo=?"
                " AND status='missed' AND date(COALESCE(end_at,start_at))=?"
                " AND title<>'空闲' ORDER BY COALESCE(end_at,start_at) DESC LIMIT 1",
                (umo, local.date().isoformat())).fetchone()
        finally:
            _c2.close()
        if _miss and _miss[0] not in used:
            bits.append(f"今天{_miss[1]}没做成")
            used.add(_miss[0])
    except Exception:  # noqa: BLE001
        pass

    # 关系阶段：只在用户问关系时给一句，避免机器人乱叫朋友
    try:
        _rel = (_canon() or {}).get("relationship_stage") or {}
        _rv = str(_rel.get("value") or "").strip()
        if _rv and _REL_Q.search(text or ""):
            bits.append(f"你们现在是{_rv}，还没确定关系")
    except Exception:  # noqa: BLE001
        pass
    if birthday_today(local.date()):
        bits.append("今天是你生日（9 月 10 日）")
    _res = (_canon_spec("residence") or {})
    if _res and any(w in (text or "") for w in ("住哪", "哪里人", "哪座城市", "哪个城市", "你在哪")):
        bits.append("你住在" + str(_res.get("city") or "") + str(_res.get("district") or ""))
    if _his:
        if _his_age >= 0.5:
            bits.append(f"用户{round(_his_age)}小时前说用户在{_his}")
        else:
            bits.append(f"用户刚说用户在{_his}")
    out = "（" + "；".join(bits) + "。）"
    # 只在“用户确实在问机器人的生活”时更新指代；讲用户自己的事不能冲掉。
    if purpose == "passive" and text and should_inject(text):
        if focus:
            remember_topic(umo, focus["event_id"], db_path=db_path, now=st["now"])
        elif st.get("event_id"):
            remember_topic(umo, st["event_id"], db_path=db_path, now=st["now"])
    return {
        "text": out[:500],
        "version": st["version"],
        "ttl_s": 60,
        "source_event_id": (focus or {}).get("event_id") or st["event_id"],
        "purpose": purpose,
    }


_CONFLICT_CUES = ("烦", "滚", "闭嘴", "别说了", "不想理", "生气", "失望",
                  "难受", "委屈", "心寒", "介意", "讨厌你", "你走")
# 不带脏话的常见冲突说法（答应又忘 / 骗子 / 敷衍 / 讨厌）
_CONFLICT_PATTERNS = (
    (re.compile(r"(答应|说好|约好)[^。！？]{0,12}(没|忘|又|不算|反悔|放我鸽子|做不到)"), "失约"),
    (re.compile(r"(忘了|忘记|没做到|没兑现)[^。！？]{0,10}(答应|说好|约好)"), "失约"),
    (re.compile(r"(骗子|骗我|骗人|糊弄我|耍我)"), "骗"),
    (re.compile(r"(敷衍|随便你吧?|你根本|不在乎我|不理我|冷暴力)"), "敷衍"),
    (re.compile(r"讨厌(?!你)"), "讨厌"),
)

_RESOLVE_CUES = ("对不起", "抱歉", "我错了", "别生气", "和好", "原谅我", "不气了", "没事了")
_NOT_HER = ("你妈", "你爸", "你家", "你们", "你朋友", "你同学", "你同事")


# R08：否定词。命中词前面紧邻这些字时不算数（"不讨厌你""我不想和好"）
# 「别」只在紧贴命中词时算否定（别生气/别烦），
# 不能跨词算——「别说了 烦」里的「别」是祈使，不否定「烦」。
_NEG_BEFORE = ("不", "没", "无需", "不用", "哪有")


def _negated_before(t: str, idx: int, window: int = 4) -> bool:
    """命中词前 window 个字里出现否定词 → 视为否定表达。

    “是不是/难道”是疑问，不是否定；先剥掉再判。
    """
    if idx > 0 and t[idx - 1] == "别":
        return True
    head = t[max(0, idx - window):idx]
    head = head.replace("是不是", "").replace("难道", "")
    return any(ch in head for ch in _NEG_BEFORE)


def detect_conflict(text: str) -> str:
    """保守识别“冲机器人来的”冲突；第三方/否定/用户自己的情绪都不算。"""
    t = (text or "").strip()
    if not t:
        return ""
    if any(w in t for w in _NOT_HER):
        return ""
    try:
        from astrbot.core.utils.mood_state import _cue_subject as _subject
    except Exception:  # noqa: BLE001
        _subject = None
    for w in _CONFLICT_CUES:
        start = 0
        while True:
            i = t.find(w, start)
            if i < 0:
                break
            # “麻烦/烦恼”不是冲突词。
            if w == "烦" and i > 0 and t[i - 1] in ("麻", "厌"):
                start = i + 1
                continue
            if w == "烦" and t[i + 1:i + 2] == "恼":
                start = i + 1
                continue
            if _negated_before(t, i):
                start = i + 1
                continue
            if _subject is not None:
                if _subject(t, i, w) == "her":
                    return w
            else:
                addressed = ("你" in t) or t.startswith(("别", "滚", "闭嘴"))
                if addressed:
                    return w
            start = i + 1
    for pat, label in _CONFLICT_PATTERNS:
        m = pat.search(t)
        if not m:
            continue
        frag = m.group(0)
        # “说好的也没忘/你没有反悔”是否定失约，不是失约。
        if any(x in frag for x in ("没忘", "没有忘", "没反悔", "没有反悔")):
            continue
        if _negated_before(t, m.start()):
            continue
        if _pattern_subject(t, m, _subject) == "her":
            return label
    return ""


def _pattern_subject(text: str, match: re.Match, subject_fn=None) -> str:
    """P1-B04：给每个模式判主语；不确定返回空，绝不默认成机器人。

    所有分支共用：转述/第三方 → third；机器人 → her；用户 → user；其余 ""。
    """
    t = text or ""
    cue = (match.group(1) if match.groups() else match.group(0)) or ""
    head = t[max(0, match.start() - 12):match.start()]
    if any(v in head for v in (
            "用户说", "机器人说", "用户们说", "它说", "听说", "同事说", "朋友说",
            "别人说", "同学说", "上级说", "跟我说", "转述")):
        return "third"
    if any(v in head for v in (
            "你妈", "你爸", "你家", "你们", "你朋友", "你同学",
            "你同事", "你上级", "你室友", "你对象")):
        return "third"
    # 承诺类：说这句话的人是不是“机器人”，看“你/我”谁在动词前。
    if cue in ("答应", "说好", "约好", "承诺", "答应过"):
        if t.startswith("你") or re.search(r"你[^。！？]{0,6}$", head):
            return "her"
        if t.startswith("我") or "我" in head[-6:]:
            return "user"
    if cue in ("忘了", "忘记", "没做到", "没兑现"):
        if t.startswith("你") or "你" in head[-8:]:
            return "her"
        if t.startswith("我") or "我" in head[-8:]:
            return "user"
    if subject_fn is not None:
        sub = subject_fn(t, match.start(), cue)
        if sub:
            return sub
    if re.search(r"你[^。！？]{0,6}$", head) or t[match.start():].startswith("你"):
        return "her"
    if "我" in head[-12:]:
        return "user"
    return ""


# F3：第三方道歉不算"我们和好"。这些人称/身份出现在道歉词前面就拦下。
_THIRD_PARTY_BEFORE = (
    "用户说", "机器人说", "用户们说", "它说", "同事", "朋友", "别人", "同学", "上级",
    "有人", "我妈", "我爸", "我哥", "我姐", "室友", "客服", "店家",
    "跟我说", "给我道歉", "跟我道歉", "替我",
)
# F3：道歉后面还有"但/可是/还是…"= 只是部分修复，不能整条解除。
_PARTIAL_AFTER = ("但", "可是", "不过", "然而", "还是", "仍然", "依然", "就是")
# P1-B05：要求/指责不是道歉。「你该对我说对不起」「你说对不起」都算 demand。
_DEMAND_BEFORE = (
    "你该", "你应该", "你才", "你得", "你给我", "你对我", "你向我",
    "你欠我", "你说", "你给", "你凭什么", "凭什么让",
)


def apology_kind(text: str) -> str:
    """区分道歉性质：own / third_party / partial / demand / 空。

    拆出来是给测试和后续"修复进度"用的；detect_resolve 只认 own。
    """
    t = (text or "").strip()
    if not t:
        return ""
    for w in _RESOLVE_CUES:
        start = 0
        while True:
            i = t.find(w, start)
            if i < 0:
                break
            if _negated_before(t, i):
                start = i + 1
                continue
            head = t[max(0, i - 8):i]
            if any(x in head for x in _THIRD_PARTY_BEFORE):
                return "third_party"
            if any(x in head for x in _DEMAND_BEFORE):
                return "demand"
            if t.startswith("你") and i < 8:
                return "demand"
            tail = t[i + len(w):]
            if any(x in tail for x in _PARTIAL_AFTER):
                return "partial"
            return "own"
        start = i + 1
    return ""


def detect_resolve(text: str) -> str:
    """识别**本人**道歉/和好；返回命中的词，否则空。

    第三方道歉（"同事跟我说对不起"）和部分修复（"对不起，但你还是让我难受"）
    都不算解除——冲突要留着，等真正的修复。
    """
    t = (text or "").strip()
    kind = apology_kind(t)
    if kind != "own":
        return ""
    for w in _RESOLVE_CUES:
        i = t.find(w)
        if i >= 0 and not _negated_before(t, i):
            return w
    return ""


# P1-B05：明确“这件事过去了/说开了”的结束语。它们不是道歉，是修复出口。
_RESOLVE_DONE = (
    "说开了", "说清楚", "讲开了", "解决了", "翻篇", "过去了",
    "和好了", "没事了", "不生气了", "原谅你了", "不提了",
)


def detect_resolution(text: str) -> str:
    """识别“这件事已经解决/翻篇”；第三方转述、否定、条件都不算。"""
    t = (text or "").strip()
    if not t:
        return ""
    for w in _RESOLVE_DONE:
        i = t.find(w)
        if i < 0:
            continue
        head = t[max(0, i - 12):i]
        if any(x in head for x in (
                "用户说", "机器人说", "用户们说", "听说", "同事说", "朋友说",
                "别人说", "如果", "要是", "假如", "假设", "还没", "没有")):
            continue
        if w in ("没事了", "不生气了"):
            # “没事了/不生气了”本身是正向；只拦“还没/没有/不会”这种前缀。
            if any(x in head[-4:] for x in ("还没", "没有", "不会")):
                continue
        elif _negated_before(t, i):
            continue
        return w
    return ""


def resolve_latest_conflict(umo: str, note: str = "", db_path=None,
                            conflict_id: str | None = None) -> bool:
    """显式“说开了”才调用；普通道歉不要走这里（用 note_conflict_repair）。

    P1-B05：repairing / still_hurt 也要能结束；可指定 conflict_id。
    """
    con = _conn(db_path)
    try:
        if conflict_id:
            row = con.execute(
                "SELECT conflict_id FROM yy_life_conflict"
                " WHERE conflict_id=? AND umo=?"
                " AND status IN ('open','repairing','still_hurt')",
                (conflict_id, umo)).fetchone()
        else:
            row = con.execute(
                "SELECT conflict_id FROM yy_life_conflict WHERE umo=?"
                " AND status IN ('open','repairing','still_hurt')"
                " ORDER BY created_at DESC, rowid DESC LIMIT 1", (umo,)).fetchone()
        if not row:
            return False
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        cur = con.execute(
            "UPDATE yy_life_conflict SET status='resolved', repair_state='resolved',"
            " resolved_at=?, resolution=?, note=?, updated_at=?"
            " WHERE conflict_id=? AND status IN ('open','repairing','still_hurt')",
            (now, note[:200], note[:200], now, row[0]))
        con.commit()
        return cur.rowcount == 1
    finally:
        con.close()


def note_conflict_repair(umo: str, kind: str, note: str = "",
                         db_path=None, apologizer: str = "user",
                         injured: str = "her") -> str | None:
    """本人道歉后进入 repairing；部分道歉进入 still_hurt，都不直接清零。

    kind: own / partial / third_party。third_party 不改变双方关系。
    P1-B05：apologizer/injured 记录谁道歉、谁受伤，摘要不再固定成“用户道歉”。
    """
    if kind not in ("own", "partial"):
        return None
    if apologizer not in ("user", "her") or injured not in ("user", "her"):
        return None
    if apologizer == injured:
        return None
    con = _conn(db_path)
    try:
        row = con.execute(
            "SELECT conflict_id FROM yy_life_conflict WHERE umo=?"
            " AND status IN ('open','repairing','still_hurt')"
            " ORDER BY created_at DESC, rowid DESC LIMIT 1", (umo,)).fetchone()
        if not row:
            return None
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        status = "repairing" if kind == "own" else "still_hurt"
        if apologizer == "user":
            ack = "用户道过歉了" if kind == "own" else "用户道歉了，但还有没消化的点"
        else:
            ack = "你道过歉了" if kind == "own" else "你道歉了，但用户还有没消化的点"
        con.execute(
            "UPDATE yy_life_conflict SET status=?, repair_state=?,"
            " acknowledged=?, apologizer=?, injured=?, note=?, updated_at=?"
            " WHERE conflict_id=?",
            (status, status, ack, apologizer, injured,
             note[:200] or ack, now, row[0]))
        con.commit()
        return row[0]
    finally:
        con.close()


def _conflict_bigrams(s: str) -> set[str]:
    t = re.sub(r"[\s，。！？、,.!?（）()「」“”\"']", "", s or "")
    return {t[i:i + 2] for i in range(max(0, len(t) - 1))}


def _similar_conflict(a: str, b: str) -> bool:
    na = re.sub(r"[\s，。！？、,.!?（）()「」“”\"']", "", a or "")
    nb = re.sub(r"[\s，。！？、,.!?（）()「」“”\"']", "", b or "")
    if not na or not nb:
        return False
    if na == nb or (len(na) >= 4 and na in nb) or (len(nb) >= 4 and nb in na):
        return True
    ga, gb = _conflict_bigrams(na), _conflict_bigrams(nb)
    if not ga or not gb:
        return False
    if len(ga & gb) / len(ga | gb) >= 0.6:
        return True
    # 同一争执的换说法：字符集合重叠高（允许“挺难受/真的让我难受”这种改写）。
    sa, sb = set(na), set(nb)
    return len(sa & sb) / max(1, len(sa | sb)) >= 0.6


def open_conflict(umo: str, text: str, source_msg_id: str | None = None,
                  db_path=None) -> str:
    """记一条未解决冲突；不自动衰减，等 resolve_conflict。

    P1-B05：同一争执的后续消息合并到一条，避免每句话新开一个问题。
    """
    import uuid

    con = _conn(db_path)
    try:
        if source_msg_id:
            row = con.execute(
                "SELECT conflict_id FROM yy_life_conflict WHERE umo=? AND source_msg_id=?"
                " ORDER BY created_at DESC LIMIT 1", (umo, source_msg_id)).fetchone()
            if row:
                return row[0]
        now = dt.datetime.now(dt.timezone.utc)
        now_s = now.isoformat(timespec="seconds")
        rows = con.execute(
            "SELECT conflict_id,text,created_at FROM yy_life_conflict"
            " WHERE umo=? AND status IN ('open','repairing','still_hurt')"
            " ORDER BY created_at DESC LIMIT 20", (umo,)).fetchall()
        for cid, old_text, created in rows:
            ts = _parse(created)
            if ts is not None and (now - ts).total_seconds() > 6 * 3600:
                continue
            if not _similar_conflict(old_text or "", text):
                continue
            keep = (old_text or "") if len(old_text or "") >= len(text) else text[:300]
            con.execute(
                "UPDATE yy_life_conflict SET text=?,"
                " source_msg_id=COALESCE(?,source_msg_id), updated_at=?"
                " WHERE conflict_id=?",
                (keep, source_msg_id, now_s, cid))
            con.commit()
            return cid
        cid = uuid.uuid4().hex
        con.execute(
            "INSERT INTO yy_life_conflict(conflict_id,umo,text,status,"
            "source_msg_id,created_at,repair_state,updated_at,"
            "apologizer,injured)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (cid, umo, text[:300], "open", source_msg_id, now_s, "open", now_s,
             "her", "user"))
        con.commit()
        return cid
    finally:
        con.close()


def resolve_conflict(conflict_id: str, note: str = "", db_path=None) -> bool:
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    con = _conn(db_path)
    try:
        row = con.execute("SELECT umo,text FROM yy_life_conflict WHERE conflict_id=?",
                          (conflict_id,)).fetchone()
        cur = con.execute(
            "UPDATE yy_life_conflict SET status='resolved', repair_state='resolved',"
            " resolved_at=?, resolution=?, note=?, updated_at=?"
            " WHERE conflict_id=? AND status IN ('open','repairing','still_hurt')",
            (now, note[:200], note[:200], now, conflict_id))
        if cur.rowcount == 1 and row:
            # B18：和好也是共同经历，留一条高重要摘要；不保留无限细节。
            con.execute(
                "INSERT OR IGNORE INTO yy_life_summary(summary_id,umo,date,kind,"
                "importance,text,created_at) VALUES(?,?,?,?,?,?,?)",
                ("sum:conflict:" + conflict_id, row[0], now[:10], "conflict", 3,
                 ("说开了：" + (note or row[1] or ""))[:120], now))
        con.commit()
        return cur.rowcount == 1
    finally:
        con.close()


def list_conflicts(umo: str, only_open: bool = True, db_path=None) -> list[dict]:
    con = _conn(db_path)
    try:
        sql = ("SELECT conflict_id,text,status,source_msg_id,created_at,resolved_at,"
               "note,repair_state,acknowledged,updated_at,apologizer,injured,resolution"
               " FROM yy_life_conflict WHERE umo=?")
        if only_open:
            sql += " AND status IN ('open','repairing','still_hurt')"
        sql += " ORDER BY created_at"
        rows = con.execute(sql, (umo,)).fetchall()
    finally:
        con.close()
    keys = ("conflict_id", "text", "status", "source_msg_id", "created_at",
            "resolved_at", "note", "repair_state", "acknowledged", "updated_at",
            "apologizer", "injured", "resolution")
    return [dict(zip(keys, r, strict=False)) for r in rows]


def format_conflicts(umo: str, limit: int = 2, db_path=None) -> str:
    """给生成层的最多 1–2 条未解决/修复中问题；没有就返回空。"""
    try:
        rows = list_conflicts(umo, only_open=False, db_path=db_path)
    except Exception:  # noqa: BLE001
        return ""
    rows = [r for r in rows if r.get("status") in ("open", "repairing", "still_hurt")]
    if not rows:
        return ""
    bits = []
    for r in rows[-max(1, limit):]:
        text = (r.get("text") or "").strip().replace("\n", " ")[:60]
        status = r.get("status")
        ap = r.get("apologizer") or "user"
        inj = r.get("injured") or "her"
        if status == "repairing":
            if ap == "user":
                state = "用户道过歉了，你还在慢慢缓"
            else:
                state = "你道过歉了，用户还在慢慢缓"
        elif status == "still_hurt":
            if ap == "user":
                state = "用户道歉了，你还有点在意"
            else:
                state = "你道歉了，用户还有点在意"
        else:
            state = "还没说开"
        bits.append(f"之前那件事：{text}；{state}")
    return ("（你们还有没完全说开的事：" + "；".join(bits)
            + "。别主动翻旧账，也别当作没发生。）")


def add_commitment(umo: str, text: str, due_at: str | None = None,
                   source_msg_id: str | None = None, db_path=None) -> str:
    """记一条答应/计划；默认 planned，真正说出口后再 active。

    P0-B01：承诺属于关系正式库，默认不跟随 shadow 路由。
    """
    import uuid

    cid = uuid.uuid4().hex
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    con = _conn(_relation_path(db_path))
    try:
        con.execute(
            "INSERT INTO yy_life_commitment(commit_id,umo,text,created_at,due_at,"
            "status,evidence_ref,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (cid, umo, text[:300], now, due_at, "planned", source_msg_id, now))
        con.commit()
    finally:
        con.close()
    return cid


def set_commitment_status(commit_id: str, status: str, *,
                          delivered_task_id: str | None = None,
                          db_path=None) -> bool:
    if status not in ("planned", "active", "fulfilled", "cancelled", "expired"):
        raise ValueError(f"bad status: {status}")
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    con = _conn(_relation_path(db_path))
    try:
        row = con.execute(
            "SELECT status,delivered_task_id FROM yy_life_commitment"
            " WHERE commit_id=?", (commit_id,)).fetchone()
        if not row:
            return False
        # 同一任务重复回调：已经 active 就返回成功，不重复写。
        if status == "active" and row[0] == "active" and row[1] == delivered_task_id:
            return True
        if status == "active":
            # P0-B01：没有真实送达证据不能变 active。
            if not delivered_task_id:
                return False
            try:
                ev = con.execute("SELECT state FROM yy_send_task WHERE task_id=?",
                                 (delivered_task_id,)).fetchone()
                has_ledger = True
            except sqlite3.OperationalError:
                ev, has_ledger = None, False
            if not has_ledger and os.environ.get("DEMO_ALLOW_NO_LEDGER", "") not in (
                    "1", "true", "yes"):
                # 只有显式隔离测试才允许没有账本的库；生产/影子都不能。
                return False
            if has_ledger and (not ev or ev[0] != "sent"):
                return False
        try:
            cur = con.execute(
                "UPDATE yy_life_commitment SET status=?, delivered_task_id=COALESCE(?,"
                "delivered_task_id), updated_at=? WHERE commit_id=?",
                (status, delivered_task_id, now, commit_id))
        except sqlite3.IntegrityError:
            # 同一 delivered_task_id 已有另一条承诺：不制造第二条。
            return False
        con.commit()
        return cur.rowcount == 1
    finally:
        con.close()


def list_commitments(umo: str, only_open: bool = True, db_path=None) -> list[dict]:
    con = _conn(_relation_path(db_path))
    try:
        sql = ("SELECT commit_id,text,status,due_at,delivered_task_id,evidence_ref,"
               "created_at,updated_at FROM yy_life_commitment WHERE umo=?")
        if only_open:
            sql += " AND status IN ('planned','active')"
        sql += " ORDER BY created_at"
        rows = con.execute(sql, (umo,)).fetchall()
    finally:
        con.close()
    keys = ("commit_id", "text", "status", "due_at", "delivered_task_id",
            "evidence_ref", "created_at", "updated_at")
    return [dict(zip(keys, r, strict=False)) for r in rows]


def _norm_text(s: str) -> str:
    return re.sub(r"[\s，。！？、,.!?（）()「」“”\"']", "", s or "")


_PROMISE_RE = re.compile(
    r"(我(晚点|稍后|等下|一会儿|回头|明天|后天|周末|等会|待会)[^，。！？]{0,12}"
    r"(给你|帮你|陪你|发|录|唱|找|问|说|弄))"
    r"|(我(来|去)[^，。！？]{0,8}(找你|陪你|帮你))"
)
_PROMISE_BLOCK = re.compile(
    r"(不能|没法|不保证|不一定|可能|要是|如果|用户说|机器人说|同事说|朋友说|"
    r"不(陪|给|帮|发|录|唱|找|问|说|弄|来|去)|没(陪|给|帮|发|录|唱|找|问|说|弄|来|去))"
)


def parse_due_at(text: str, now: dt.datetime | None = None) -> str | None:
    """把“明天/后天/周末/晚点”变成 UTC ISO；不确定就 None。"""
    t = text or ""
    local = now or dt.datetime.now(_tz())
    if local.tzinfo is None:
        local = local.replace(tzinfo=dt.timezone.utc)
    local = local.astimezone(_tz())
    if any(k in t for k in ("晚点", "稍后", "等会", "待会", "一会儿", "回头")):
        due = local + dt.timedelta(hours=2)
        return due.astimezone(dt.timezone.utc).isoformat(timespec="seconds")
    if "后天" in t:
        day = local.date() + dt.timedelta(days=2)
    elif "明天" in t:
        day = local.date() + dt.timedelta(days=1)
    elif "周末" in t:
        add = (5 - local.weekday()) % 7 or 7
        day = local.date() + dt.timedelta(days=add)
    else:
        return None
    due = dt.datetime.combine(day, dt.time(23, 59), tzinfo=local.tzinfo)
    return due.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def is_promise(text: str) -> bool:
    """机器人真的答应了吗？否定/条件/提问/转述都不算。"""
    t = (text or "").strip()
    if not t or not _PROMISE_RE.search(t):
        return False
    if t.rstrip().endswith(("吗", "？", "?")):
        return False
    if _PROMISE_BLOCK.search(t):
        return False
    return True


def activate_from_send(umo: str, text: str, task_id: str, db_path=None) -> str | None:
    """R08：发送成功后，把文本匹配的 planned 承诺转 active 并绑定真实任务。

    P0-B01：只写关系正式库；同一 task_id 幂等；否定/条件/提问不建承诺。
    """
    if not umo or not text or not task_id:
        return None
    dbp = _relation_path(db_path)
    con = _conn(dbp)
    try:
        row = con.execute(
            "SELECT commit_id FROM yy_life_commitment WHERE delivered_task_id=?",
            (task_id,)).fetchone()
        if row:
            return row[0]
        rows = con.execute(
            "SELECT commit_id,text FROM yy_life_commitment"
            " WHERE umo=? AND status='planned'", (umo,)).fetchall()
    finally:
        con.close()
    nt = _norm_text(text)
    for cid, t in rows:
        nt2 = _norm_text(t)
        if nt2 and (nt2 in nt or nt in nt2):
            if set_commitment_status(cid, "active", delivered_task_id=task_id,
                                     db_path=dbp):
                return cid
    if is_promise(text):
        cid = add_commitment(umo, text[:300], due_at=parse_due_at(text),
                             db_path=dbp)
        if set_commitment_status(cid, "active", delivered_task_id=task_id,
                                 db_path=dbp):
            return cid
    return None


def expire_overdue(umo: str | None = None, db_path=None) -> int:
    """到期未兑现的承诺标 expired；只处理 planned/active。"""
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    con = _conn(_relation_path(db_path))
    try:
        sql = ("UPDATE yy_life_commitment SET status='expired', updated_at=?"
               " WHERE status IN ('planned','active')"
               " AND ((due_at IS NOT NULL AND due_at<>'' AND due_at<?)"
               "      OR ((due_at IS NULL OR due_at='') AND created_at<?))")
        _old = (dt.datetime.now(dt.timezone.utc)
                - dt.timedelta(days=7)).isoformat(timespec="seconds")
        args: list = [now, now, _old]
        if umo:
            sql += " AND umo=?"
            args.append(umo)
        n = con.execute(sql, args).rowcount
        con.commit()
        return n
    finally:
        con.close()


def commitments_context(umo: str, limit: int = 3, db_path=None) -> str:
    """把**已说出口的**未完成承诺压成一句提示；没有就返回空。

    R08：planned 只是草稿，不能当成"机器人答应过"进提示词。
    """
    try:
        expire_overdue(umo, db_path=db_path)
    except Exception:  # noqa: BLE001
        pass
    rows = [r for r in list_commitments(umo, only_open=True, db_path=db_path)
            if r["status"] == "active"][:limit]
    if not rows:
        return ""
    body = "；".join(f"{r['text']}（{r['status']}）" for r in rows)
    return ("<system_reminder>你这边还记着：" + body +
            "。只在合适时提一句，不要编新的承诺，也不要把用户的承诺当成自己的。"
            "</system_reminder>")


def add_source(kind: str, *, url: str | None = None, bvid: str | None = None,
               title: str | None = None, read_scope: str | None = None,
               raw_sha: str | None = None, msg_ids: str | None = None,
               db_path=None) -> str:
    """外部来源账；只存元数据/哈希，不存全文。"""
    import uuid

    sid = uuid.uuid4().hex
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    con = _conn(db_path)
    try:
        con.execute(
            "INSERT INTO yy_life_source(source_id,kind,url,bvid,title,fetched_at,"
            "read_scope,raw_sha,msg_ids,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (sid, kind, url, bvid, title, now, read_scope, raw_sha, msg_ids, now))
        con.commit()
    finally:
        con.close()
    return sid


def get_source(source_id: str, db_path=None) -> dict | None:
    con = _conn(db_path)
    try:
        row = con.execute(
            "SELECT source_id,kind,url,bvid,title,fetched_at,read_scope,raw_sha,"
            "msg_ids,created_at FROM yy_life_source WHERE source_id=?",
            (source_id,)).fetchone()
    finally:
        con.close()
    if not row:
        return None
    keys = ("source_id", "kind", "url", "bvid", "title", "fetched_at",
            "read_scope", "raw_sha", "msg_ids", "created_at")
    return dict(zip(keys, row, strict=False))


def main() -> int:
    import sys
    if "--demo" not in sys.argv:
        print(__doc__)
        return 0
    umo = "aiocqhttp:FriendMessage:100000000"
    for h in (4, 11, 14, 20, 23, 1):
        base = dt.datetime(2026, 10, 25, h, 0, tzinfo=_tz())
        st = advance(umo, now=base)
        print(f"{h:02d}:00 ->", st["title"], st["place"], st["energy"],
              life_context(umo, now=base)["text"])
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

