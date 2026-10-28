# -*- coding: utf-8 -*-
"""P3-19（生产版）：结构化记忆（机器人记得的"事实"，不是聊天原文）。

聊天记录检索（doc.db）解决"我以前说过什么"；这个表解决"关于用户/我，有哪些确定的事"：
  yy_fact2(subject, predicate, obj, kind, status, confidence, source,
           source_msg_id, valid_from, valid_to, created_at, updated_at)

关键规则（A06）：
  - 多值：喜欢音乐 + 喜欢游戏可以并存（remember(..., multi=True)）；
  - 单值：生日/称呼等更正时，旧值标 superseded，不直接删；
  - 来源：每条带 source 与 source_msg_id，召回时一起给模型；
  - 状态：active / superseded / retracted，召回默认只读 active；
  - 事件：kind='event' 带 valid_from/valid_to，不覆盖永久身份。

写入方（谁来判断"这是一条事实"）仍留给上层；这里不做 LLM 抽取，
避免模型把自己的猜测写成事实。
"""
from __future__ import annotations

import os
from astrbot.core.utils.paths import db_path
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_DB = Path(
    os.environ.get("DEMO_DB")
    or db_path())

SCHEMA = """
CREATE TABLE IF NOT EXISTS yy_fact2 (
  subject TEXT NOT NULL,
  predicate TEXT NOT NULL,
  obj TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'fact',
  status TEXT NOT NULL DEFAULT 'active',
  confidence REAL DEFAULT 1.0,
  source TEXT,
  source_msg_id TEXT,
  valid_from TEXT,
  valid_to TEXT,
  created_at TEXT,
  updated_at TEXT,
  PRIMARY KEY (subject, predicate, obj)
)
"""
INDEX = "CREATE INDEX IF NOT EXISTS idx_yy_fact2_sub ON yy_fact2(subject, status)"


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
    # 每个库只做一次 DDL；capture 每轮都会调 remember，不能重复 executescript。
    try:
        path = con.execute("PRAGMA database_list").fetchone()[2]
    except Exception:  # noqa: BLE001
        path = ""
    if path and path in _ENSURED:
        return
    con.executescript(SCHEMA)
    con.execute(INDEX)
    # 旧表迁移：旧 PK 是 (subject,predicate)，先原样搬进 v2，旧表保留可追溯
    try:
        old = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='yy_fact'"
        ).fetchone()
        if old:
            n = con.execute("SELECT COUNT(*) FROM yy_fact").fetchone()[0]
            have = con.execute("SELECT COUNT(*) FROM yy_fact2").fetchone()[0]
            if n and not have:
                con.execute(
                    "INSERT OR IGNORE INTO yy_fact2(subject,predicate,obj,kind,status,"
                    "confidence,source,created_at,updated_at)"
                    " SELECT subject,predicate,obj,'fact','active',confidence,source,"
                    "created_at,updated_at FROM yy_fact")
                con.commit()
    except sqlite3.OperationalError:
        pass
    if path:
        _ENSURED.add(path)

def remember(subject: str, predicate: str, obj: str, *,
             kind: str = "fact", multi: bool = False,
             confidence: float = 1.0, source: str = "",
             source_msg_id: str | None = None,
             valid_from: str | None = None,
             status: str = "active",
             db_path=None) -> None:
    """写入一条事实/偏好/事件。

    multi=False（默认）：同一 subject+predicate 视为“单值更正”，
    旧值标 superseded 并写 valid_to，新值按 status 写入。
    multi=True：同一 subject+predicate+obj 幂等更新，允许多个值并存。
    status='candidate' 用于自动抽取的候选，不参与召回/提示。
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    con = sqlite3.connect(str(_db(db_path)), timeout=15)
    try:
        _ensure(con)
        if status == "candidate":
            # R01：候选只能写"候选自己的行"。
            #   已有 active/confirmed → 完全不碰（不降级、不改来源、不 supersede）
            #   已有 retracted      → 不复活（旧消息重放不能撤销撤回）
            #   已有 candidate      → 只更新证据，保持 candidate
            con.execute(
                "INSERT INTO yy_fact2(subject,predicate,obj,kind,status,confidence,"
                "source,source_msg_id,valid_from,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(subject,predicate,obj) DO UPDATE SET"
                " confidence=MIN(0.9, MAX(yy_fact2.confidence, excluded.confidence) + 0.2),"
                " source=COALESCE(NULLIF(yy_fact2.source,''), excluded.source),"
                " source_msg_id=COALESCE(yy_fact2.source_msg_id, excluded.source_msg_id),"
                " updated_at=excluded.updated_at"
                " WHERE yy_fact2.status='candidate'",
                (subject, predicate, obj, kind, status, confidence, source,
                 source_msg_id, valid_from or now, now, now))
            if predicate in _PROMOTE_OK:
                con.execute(
                    "UPDATE yy_fact2 SET status='active', updated_at=?"
                    " WHERE subject=? AND predicate=? AND obj=?"
                    " AND status='candidate' AND confidence>=0.8",
                    (now, subject, predicate, obj))
            con.commit()
            return
        if not multi:
            con.execute(
                "UPDATE yy_fact2 SET status='superseded', valid_to=?, updated_at=?"
                " WHERE subject=? AND predicate=? AND status='active' AND obj<>?",
                (now, now, subject, predicate, obj))
        con.execute(
            "INSERT INTO yy_fact2(subject,predicate,obj,kind,status,confidence,"
            "source,source_msg_id,valid_from,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(subject,predicate,obj) DO UPDATE SET"
            " kind=excluded.kind, status=excluded.status,"
            " confidence=excluded.confidence,"
            " source=excluded.source, source_msg_id=excluded.source_msg_id,"
            " valid_from=COALESCE(excluded.valid_from, yy_fact2.valid_from),"
            " valid_to=NULL, updated_at=excluded.updated_at",
            (subject, predicate, obj, kind, status, confidence, source,
             source_msg_id, valid_from or now, now, now))
        con.commit()
    finally:
        con.close()


_PROMOTE_OK = {"买过", "换过", "养", "讨厌", "不吃", "过敏", "上班",
               "住在", "生日", "叫", "不喜欢", "喜欢"}

_CAND_PATTERNS = (
    (re.compile(r"我喜欢([^，。！？\s]{1,12})"), "喜欢", True),
    (re.compile(r"我(?:刚|昨天|今天|最近)?买了([^，。！？\s]{1,14})"), "买过", True),
    (re.compile(r"我(?:刚|昨天|今天|最近)?换了([^，。！？\s]{1,14})"), "换过", True),
    (re.compile(r"我养了([^，。！？\s]{1,10})"), "养", True),
    (re.compile(r"我(?:很|特别|最)?讨厌([^，。！？\s]{1,10})"), "讨厌", True),
    (re.compile(r"我不(?:喜欢|爱吃|吃)([^，。！？\s]{1,10})"), "不吃", True),
    (re.compile(r"我对([^，。！？\s]{1,10})过敏"), "过敏", True),
    (re.compile(r"我在([^，。！？\s]{1,10})(?:上班|工作)"), "上班", False),
    (re.compile(r"我生日(?:是|在)?\s*([0-9]{1,2}月[0-9]{1,2}日|[0-9]{1,2}\.[0-9]{1,2})"),
     "生日", False),
    (re.compile(r"我叫([^，。！？\s]{1,8})"), "叫", False),
    (re.compile(r"我住在([^，。！？\s]{1,12})"), "住在", False),
    (re.compile(r"我在(?:学|练)([^，。！？\s]{1,8})"), "在练", True),
)
_CAND_SKIP = ("用户说", "机器人说", "别人说", "如果说", "假如", "假设")


def extract_candidates(text: str) -> list[tuple[str, str, bool]]:
    """从用户原话抽“候选记忆”；只抽显式第一人称，不做推断。"""
    text = (text or "").strip()
    if not text or any(w in text for w in _CAND_SKIP):
        return []
    out: list[tuple[str, str, bool]] = []
    for rx, pred, multi in _CAND_PATTERNS:
        for m in rx.finditer(text):
            obj = m.group(1).strip()
            obj = re.sub(r"^(一个|个|一把|把|一条|条|一台|台|一双|双|一件|件|一只|只)", "", obj)
            if not obj:
                continue
            if text[max(0, m.start() - 2):m.start()].endswith("不是"):
                continue
            out.append((pred, obj, multi))
    return out


def capture_candidates(subject: str, text: str, source_msg_id: str | None,
                       db_path=None) -> int:
    """把候选写进 yy_fact2（status=candidate），不参与召回/提示。

    止血开关：DEMO_CANDIDATE=0 可整体关闭自动候选写入（不动已确认事实）。
    """
    if os.environ.get("DEMO_CANDIDATE", "1").strip() in ("0", "false", "False"):
        return 0
    n = 0
    for pred, obj, multi in extract_candidates(text):
        try:
            remember(subject, pred, obj, kind="candidate", multi=multi,
                     confidence=0.4, source="auto", source_msg_id=source_msg_id,
                     status="candidate", db_path=db_path)
            n += 1
        except Exception:  # noqa: BLE001
            pass
    return n


def list_candidates(subject: str, limit: int = 20, db_path=None) -> list[dict]:
    rows = recall(subject, limit=limit * 4, include_inactive=True, db_path=db_path)
    return [r for r in rows if r.get("status") == "candidate"][:limit]


def promote_candidate(subject: str, predicate: str, obj: str, *,
                      multi: bool | None = None, db_path=None) -> bool:
    """用户确认后，把候选升为 active；单值更正与状态切换在同一事务内。

    P0-3：不再“先读候选、再调 remember”分两个连接；确认动作必须原子。
    多值属性（喜欢/在练/养）默认 multi，避免确认草莓时覆盖蓝莓；
    其它单值属性默认按更正处理。调用方仍可显式覆盖。
    """
    use_multi = multi if multi is not None else predicate in ("喜欢", "在练", "养")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    con = sqlite3.connect(str(_db(db_path)), timeout=15)
    try:
        _ensure(con)
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT confidence,source,source_msg_id,valid_from FROM yy_fact2"
            " WHERE subject=? AND predicate=? AND obj=? AND status='candidate'",
            (subject, predicate, obj)).fetchone()
        if row is None:
            con.execute("ROLLBACK")
            return False
        confidence, source, source_msg_id, valid_from = row
        if not use_multi:
            con.execute(
                "UPDATE yy_fact2 SET status='superseded', valid_to=?, updated_at=?"
                " WHERE subject=? AND predicate=? AND status='active' AND obj<>?",
                (now, now, subject, predicate, obj))
        con.execute(
            "INSERT INTO yy_fact2(subject,predicate,obj,kind,status,confidence,"
            "source,source_msg_id,valid_from,created_at,updated_at)"
            " VALUES(?,?,?,'fact','active',?,?,?,?,?,?)"
            " ON CONFLICT(subject,predicate,obj) DO UPDATE SET"
            " kind='fact', status='active', confidence=excluded.confidence,"
            " source=excluded.source, source_msg_id=excluded.source_msg_id,"
            " valid_from=COALESCE(yy_fact2.valid_from, excluded.valid_from),"
            " valid_to=NULL, updated_at=excluded.updated_at",
            (subject, predicate, obj,
             max(float(confidence or 0.4), 0.8),
             source or "confirmed", source_msg_id, valid_from or now, now, now))
        con.execute("COMMIT")
        return True
    except BaseException:
        try:
            con.execute("ROLLBACK")
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        con.close()


def drop_candidate(subject: str, predicate: str, obj: str, db_path=None) -> bool:
    """用户否认/撤回候选：标 retracted，不删原始来源。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    con = sqlite3.connect(str(_db(db_path)), timeout=15)
    try:
        _ensure(con)
        cur = con.execute(
            "UPDATE yy_fact2 SET status='retracted', valid_to=?, updated_at=?"
            " WHERE subject=? AND predicate=? AND obj=? AND status='candidate'",
            (now, now, subject, predicate, obj))
        con.commit()
        return cur.rowcount == 1
    finally:
        con.close()


def recall(subject: str | None = None, like: str | None = None,
           limit: int = 20, include_inactive: bool = False,
           db_path=None) -> list[dict]:
    con = sqlite3.connect(str(_db(db_path)), timeout=15)
    try:
        _ensure(con)
        sql = ("SELECT subject,predicate,obj,kind,status,confidence,source,"
               "source_msg_id,valid_from,valid_to FROM yy_fact2")
        args: list = []
        where = []
        if subject:
            where.append("subject=?")
            args.append(subject)
        if like:
            where.append("(predicate like ? or obj like ?)")
            args += [f"%{like}%", f"%{like}%"]
        if not include_inactive:
            where.append("status='active'")
        if where:
            sql += " where " + " and ".join(where)
        sql += " order by kind='event' desc, confidence desc, updated_at desc limit ?"
        args.append(limit)
        keys = ("subject", "predicate", "obj", "kind", "status", "confidence",
                "source", "source_msg_id", "valid_from", "valid_to")
        return [dict(zip(keys, row, strict=False)) for row in con.execute(sql, args)]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def forget(subject: str, predicate: str, obj: str | None = None,
           db_path=None) -> int:
    """软撤回：保留证据，只把状态改成 retracted。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    con = sqlite3.connect(str(_db(db_path)), timeout=15)
    try:
        _ensure(con)
        if obj is None:
            cur = con.execute(
                "UPDATE yy_fact2 SET status='retracted', valid_to=?, updated_at=?"
                " WHERE subject=? AND predicate=? AND status='active'",
                (now, now, subject, predicate))
        else:
            cur = con.execute(
                "UPDATE yy_fact2 SET status='retracted', valid_to=?, updated_at=?"
                " WHERE subject=? AND predicate=? AND obj=? AND status='active'",
                (now, now, subject, predicate, obj))
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def format_for_prompt(subject: str, limit: int = 8, db_path=None) -> str:
    rows = recall(subject, limit=limit, db_path=db_path)
    if not rows:
        return ""
    lines = []
    for r in rows:
        if r["predicate"] == "喜欢" and r["obj"] == "你":
            line = "用户说过认可你"
        elif r["predicate"] == "喜欢":
            line = f"用户喜欢{r['obj']}"
        elif r["predicate"] == "讨厌":
            line = f"用户讨厌{r['obj']}"
        elif r["predicate"] == "不吃":
            line = f"用户不吃{r['obj']}"
        else:
            line = f"{r['predicate']}：{r['obj']}"
        if r.get("source") and r["source"] != "auto":
            line += f"（{r['source']}）"
        lines.append(line)
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
    remember(umo, "生日", "1.22", source="demo", db_path=db)
    remember(umo, "生日", "1.23", source="demo", db_path=db)   # 更正
    remember(umo, "喜欢", "音乐", multi=True, source="demo", db_path=db)
    remember(umo, "喜欢", "游戏", multi=True, source="demo", db_path=db)
    print("recall:", recall(umo, db_path=db))
    print("生日历史:", recall(umo, like="生日", include_inactive=True, db_path=db))
    print(format_for_prompt(umo, db_path=db))
    return 0


if __name__ == "__main__":
    sys.exit(main())

