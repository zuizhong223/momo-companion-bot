# -*- coding: utf-8 -*-
"""发送台账（P0-A / P0-B）。

一个库、四张表，全部放在 `data_v4.db`（与 conversations 同库，才能做原子提交）：

  yy_send_task     一条要发出去的消息（多段回复 = 多个 task，共用 reply_id）
  yy_send_attempt  每次网络尝试
  yy_history_commit 历史已提交的标记（只随历史内容同事务写）
  yy_recent_sent    近期已发送（只用于主动话题的 L2 相似度抑制）

核心保证：
  1. **原子认领**：`UPDATE ... WHERE task_id=? AND state='pending'` 影响 1 行才允许发送；
     认领与 attempt 写入同一事务，网络调用在事务之外。
  2. **异常分类**：明确未发出/明确拒绝 → failed（可重试）；
     无法确认是否被接受（超时/断连/解析失败/崩溃）→ unknown（不自动重发）。
  3. **状态与历史解耦**：`state` 只表示"发没发出去"，历史写入看 `history_state`。
  4. **历史幂等**：`yy_history_commit` 与历史内容在同一个 `BEGIN IMMEDIATE` 事务里写。

环境变量：
  DEMO_LEDGER_DB  覆盖数据库路径（测试用）
  DEMO_CRASH_AT   注入崩溃点：after_send_confirm / before_history_commit / after_history_commit
"""
from __future__ import annotations

import asyncio
from astrbot.core.utils.paths import data_dir, db_path
import datetime
import hashlib
import json
import os
import sqlite3
import uuid
from pathlib import Path

DEFAULT_DB = Path(
    os.environ.get("DEMO_DB")
    or db_path()
)
# 隔离自检要比较“正式库”，不能比较 DEFAULT_DB：
# 测试里 DEMO_LEDGER_DB 会让 DEFAULT_DB/db_path() 都指向临时库，
# 拿它当正式库会把所有隔离测试全部误判成“指向正式库”。
# 用源码位置固定正式库，避免 data_dir() 被测试环境改写。
_PROD_DB = Path(__file__).resolve().parents[3] / "data" / "data_v4.db"

# 状态
PENDING, SENDING, SENT = "pending", "sending", "sent"
FAILED, UNKNOWN, SKIPPED, DROPPED = "failed", "unknown", "skipped", "dropped"

# history_state
H_PENDING, H_COMMITTED, H_SKIPPED, H_EXTERNAL = (
    "pending", "committed", "skipped_no_conv", "external")
# 目标会话缺失：保留可修复的 pending，不静默结案（P0-B）
H_NEEDS_TARGET = "needs_target"
# 同一业务回复的其它物理段未全部送达：暂不写整段历史
H_PARTIAL = "partial"
# 确定没发出去（failed/dropped）：不会再有历史，不要永远挂在 pending
H_NOT_SENT = "not_sent"

RETRY_LIMIT = 3
# TTS/媒体上传可能超过 2 分钟；给在途发送 5 分钟宽限，别把真实成功误判 unknown。
UNKNOWN_GRACE_S = 300

# 明确"平台拒绝/没发出去"的返回码（其余按 unknown 处理，宁可不重发）
# 1200 = rich media transfer failed：语音/图片在协议端传输失败，消息没落地，可重试
DEFINITE_RETCODES = {100, 102, 103, 104, 106, 110, 120, 1200, 1400, 1401, 1404}

SCHEMA = """
CREATE TABLE IF NOT EXISTS yy_send_task (
  task_id TEXT PRIMARY KEY,
  reply_id TEXT NOT NULL,
  seq INTEGER NOT NULL DEFAULT 0,
  session TEXT NOT NULL,
  conversation_id TEXT,
  umo TEXT,
  kind TEXT NOT NULL,
  text TEXT NOT NULL,
  history_text TEXT,
  delivered_text TEXT,
  audio_id TEXT,
  source_type TEXT NOT NULL DEFAULT 'model_output',
  source_ref TEXT,
  event_id TEXT,
  text_sha TEXT NOT NULL,
  state TEXT NOT NULL,
  history_state TEXT NOT NULL DEFAULT 'external',
  managed_history INTEGER NOT NULL DEFAULT 0,
  retry_count INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  created_at TEXT NOT NULL,
  finalized_at TEXT
);
CREATE TABLE IF NOT EXISTS yy_send_attempt (
  attempt_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  attempt_no INTEGER NOT NULL,
  state TEXT NOT NULL,
  error TEXT,
  classified_as TEXT,
  qq_message_id TEXT,
  started_at TEXT,
  ended_at TEXT
);
CREATE TABLE IF NOT EXISTS yy_history_commit (
  task_id TEXT PRIMARY KEY,
  committed_at TEXT NOT NULL,
  source_type TEXT NOT NULL DEFAULT 'model_output',
  source_ref TEXT,
  event_id TEXT
);
CREATE TABLE IF NOT EXISTS yy_recent_sent (
  session TEXT NOT NULL,
  text_sha TEXT NOT NULL,
  text TEXT,
  scope TEXT NOT NULL DEFAULT 'proactive',
  sent_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS yy_turn (
  reply_id TEXT PRIMARY KEY,
  session TEXT NOT NULL,
  conversation_id TEXT,
  umo TEXT,
  total INTEGER NOT NULL,
  history_text TEXT,
  created_at TEXT NOT NULL,
  committed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_yy_task_state ON yy_send_task(state, history_state);
CREATE INDEX IF NOT EXISTS idx_yy_recent ON yy_recent_sent(session, scope, sent_at);
CREATE INDEX IF NOT EXISTS idx_yy_task_umo_created ON yy_send_task(umo, state, kind, created_at);
"""


def now_iso() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")


def sha(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()


class PayloadMismatch(ValueError):
    """同一个 task_id 被用于不同载荷（正文/会话/种类/UMO/父回复）。"""


class TurnManifestConflict(ValueError):
    """同一 reply_id 的整轮清单已确定，不能再改 total/目标/正文。"""


def crash_point(name: str) -> None:
    """测试用崩溃注入。生产环境 DEMO_CRASH_AT 不设置时什么都不做。"""
    if os.environ.get("DEMO_CRASH_AT", "").strip() == name:
        os._exit(1)


def classify_exception(exc: BaseException) -> str:
    """'definite' = 明确没发出去 / 明确被拒；'unknown' = 无法确认。"""
    try:
        from aiocqhttp.exceptions import ActionFailed
    except Exception:  # noqa: BLE001
        ActionFailed = ()  # type: ignore
    if ActionFailed and isinstance(exc, ActionFailed):
        retcode = getattr(exc, "retcode", None)
        if retcode is None and getattr(exc, "args", None):
            first = exc.args[0]
            if isinstance(first, dict):
                retcode = first.get("retcode")
        try:
            retcode = int(retcode) if retcode is not None else None
        except (TypeError, ValueError):
            retcode = None
        return "definite" if retcode in DEFINITE_RETCODES else "unknown"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "unknown"
    if isinstance(exc, (ConnectionError, OSError, json.JSONDecodeError)):
        return "unknown"
    if isinstance(exc, ValueError):
        return "definite"  # 我们自己的参数校验失败，没进网络
    return "unknown"       # 保守：认不出来就当不确定


class Ledger:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(
            os.environ.get("DEMO_LEDGER_DB")
            or (db_path if db_path else DEFAULT_DB)
        )
        # 隔离自检：测试模式(DEMO_TEST=1)下不许指向正式库
        if os.environ.get("DEMO_TEST", "").strip() not in ("", "0"):
            try:
                if str(self.db_path.resolve()).lower() == str(_PROD_DB.resolve()).lower():
                    raise SystemExit(
                        "隔离自检失败：测试模式下 Ledger 指向了正式库 " + str(self.db_path)
                        + "；请设置 DEMO_LEDGER_DB 到临时副本")
            except OSError:
                pass
        self._ensured = False

    # ---------- 基础设施 ----------
    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path), timeout=15, isolation_level=None)
        con.execute("PRAGMA busy_timeout=15000")
        if not self._ensured:
            con.executescript(SCHEMA)
            cols = {row[1] for row in con.execute("PRAGMA table_info(yy_send_task)")}
            for name, ddl in (("umo", "TEXT"),
                              ("history_text", "TEXT"),
                              ("delivered_text", "TEXT"),
                              ("audio_id", "TEXT"),
                              ("source_type", "TEXT DEFAULT 'model_output'"),
                              ("source_ref", "TEXT"),
                              ("event_id", "TEXT")):
                if name not in cols:
                    con.execute(f"ALTER TABLE yy_send_task ADD COLUMN {name} {ddl}")
            hcols = {row[1] for row in con.execute("PRAGMA table_info(yy_history_commit)")}
            for name, ddl in (("source_type", "TEXT DEFAULT 'model_output'"),
                              ("source_ref", "TEXT"), ("event_id", "TEXT")):
                if name not in hcols:
                    con.execute(f"ALTER TABLE yy_history_commit ADD COLUMN {name} {ddl}")
            self._ensured = True
        return con

    # ---------- 任务 ----------
    def new_task(self, *, session: str, kind: str, text: str,
                 conversation_id: str | None = None,
                 umo: str | None = None,
                 history_text: str | None = None,
                 delivered_text: str | None = None,
                 audio_id: str | None = None,
                 source_type: str = "model_output",
                 source_ref: str | None = None,
                 event_id: str | None = None,
                 reply_id: str | None = None, seq: int = 0,
                 managed_history: bool = False, state: str = PENDING,
                 task_id: str | None = None,
                 total: int | None = None) -> str:
        """建任务。task_id 由调用方给（管家重试要复用同一个），已存在则幂等返回。

        P0-B：发送前就把“这条消息最终要写到哪个会话、哪段正文、哪个 UMO”
        一起落库；恢复不再依赖进程内变量或“该用户最新会话”。
        """
        task_id = (task_id or uuid.uuid4().hex).strip()
        existing = self.get(task_id)
        if existing:
            # R03：同一 task_id 只能对应同一份载荷。换正文/换会话/换种类/换 UMO/换父回复
            # 都必须显式报错，绝不能把"另一个内容"当成"重试"而返回 already sent。
            if (existing["session"] != session
                    or existing["kind"] != kind
                    or sha(existing.get("text") or "") != sha(text)
                    or (umo and existing.get("umo") and umo != existing["umo"])
                    or (audio_id or existing.get("audio_id")) and
                       (audio_id or "") != (existing.get("audio_id") or "")
                    or (reply_id and existing.get("reply_id")
                        and reply_id != existing["reply_id"])):
                raise PayloadMismatch(
                    f"task_id={task_id} 载荷冲突："
                    f"session {existing['session']!r}->{session!r} "
                    f"kind {existing['kind']!r}->{kind!r} "
                    f"text {(existing.get('text') or '')[:20]!r}->{text[:20]!r} "
                    f"umo {existing.get('umo')!r}->{umo!r} "
                    f"reply {existing.get('reply_id')!r}->{reply_id!r}")
            # 旧任务（或首次调用没拿到 cid）在重试时补齐目标，不覆盖已有值
            con = self._conn()
            try:
                con.execute(
                    "UPDATE yy_send_task SET"
                    " conversation_id=COALESCE(conversation_id,?),"
                    " umo=COALESCE(umo,?),"
                    " history_text=COALESCE(history_text,?),"
                    " delivered_text=COALESCE(delivered_text,?),"
                    " audio_id=COALESCE(audio_id,?),"
                    " source_type=COALESCE(source_type,?),"
                    " source_ref=COALESCE(source_ref,?),"
                    " event_id=COALESCE(event_id,?)"
                    " WHERE task_id=?",
                    (conversation_id, umo, history_text, delivered_text, audio_id,
                     source_type, source_ref, event_id, task_id),
                )
            finally:
                con.close()
            return task_id
        hist = H_PENDING if managed_history else H_EXTERNAL
        inserted = False
        con = self._conn()
        try:
            cur = con.execute(
                "INSERT INTO yy_send_task(task_id, reply_id, seq, session,"
                " conversation_id, umo, kind, text, history_text, delivered_text,"
                " audio_id, source_type, source_ref, event_id, text_sha, state,"
                " history_state, managed_history, created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(task_id) DO NOTHING",
                (task_id, reply_id or task_id, seq, session,
                 conversation_id, umo, kind, text, history_text, delivered_text,
                 audio_id, source_type or "model_output", source_ref or task_id,
                 event_id, sha(text), state, hist, 1 if managed_history else 0,
                 now_iso()),
            )
            inserted = cur.rowcount > 0
        finally:
            con.close()
        if not inserted:
            # 并发插入：另一个线程刚建了同 task_id。回到顶部按“已存在”
            # 分支做载荷校验/补齐目标，不能直接抛 IntegrityError。
            return self.new_task(
                session=session, kind=kind, text=text,
                conversation_id=conversation_id, umo=umo,
                history_text=history_text, delivered_text=delivered_text,
                audio_id=audio_id, source_type=source_type,
                source_ref=source_ref, event_id=event_id,
                reply_id=reply_id, seq=seq, managed_history=managed_history,
                state=state, task_id=task_id, total=total,
            )
        if total and reply_id:
            ok = self.start_turn(reply_id=reply_id, session=session, total=total,
                                 umo=umo, history_text=history_text,
                                 conversation_id=conversation_id)
            if not ok:
                raise TurnManifestConflict(
                    f"turn manifest conflict reply={reply_id} total={total}")
        return task_id

    def get(self, task_id: str) -> dict | None:
        con = self._conn()
        try:
            row = con.execute(
                "SELECT task_id, reply_id, seq, session, conversation_id, umo, kind,"
                " text, history_text, delivered_text, audio_id, state, history_state,"
                " retry_count, error FROM yy_send_task WHERE task_id=?", (task_id,)).fetchone()
        finally:
            con.close()
        if not row:
            return None
        keys = ("task_id", "reply_id", "seq", "session", "conversation_id", "umo", "kind",
                "text", "history_text", "delivered_text", "audio_id", "state",
                "history_state", "retry_count", "error")
        return dict(zip(keys, row, strict=False))

    def resolve_conversation(self, umo: str | None) -> str | None:
        """发送前解析目标会话并落库；恢复只读库里的 conversation_id。"""
        if not umo:
            return None
        con = self._conn()
        try:
            row = con.execute(
                "SELECT conversation_id FROM conversations WHERE user_id=?"
                " ORDER BY updated_at DESC LIMIT 1", (umo,)).fetchone()
        finally:
            con.close()
        return row[0] if row else None

    def attach_history(self, task_id: str, *, umo: str | None = None,
                       history_text: str | None = None) -> None:
        """多段回复时，把“整段历史”挂到实际最后一段的任务上。

        前面各段 managed_history=0；只有最后一段在全部段 sent 后提交，
        避免第一段成功就把整条回复记成已说。
        """
        con = self._conn()
        try:
            con.execute(
                "UPDATE yy_send_task SET managed_history=1,"
                " history_state=CASE WHEN history_state='external' THEN ? ELSE history_state END,"
                " umo=COALESCE(umo,?), history_text=COALESCE(history_text,?)"
                " WHERE task_id=?", (H_PENDING, umo, history_text, task_id))
        finally:
            con.close()

    def _claim_with(self, con: sqlite3.Connection, task_id: str,
                    from_states: tuple[str, ...]) -> bool:
        marks = ",".join("?" * len(from_states))
        con.execute("BEGIN IMMEDIATE")
        cur = con.execute(
            f"UPDATE yy_send_task SET state='{SENDING}', finalized_at=NULL"
            f" WHERE task_id=? AND state IN ({marks})",
            (task_id, *from_states),
        )
        if cur.rowcount != 1:          # ① 认领失败：立刻回滚，什么都不写、不发
            con.execute("ROLLBACK")
            return False
        n = con.execute(
            "SELECT COUNT(*) FROM yy_send_attempt WHERE task_id=?", (task_id,)
        ).fetchone()[0]
        con.execute(                    # ② 只有认领成功才写 attempt
            "INSERT INTO yy_send_attempt(attempt_id, task_id, attempt_no, state,"
            " started_at) VALUES(?,?,?,?,?)",
            (uuid.uuid4().hex, task_id, n + 1, PENDING, now_iso()),
        )
        con.execute("COMMIT")
        return True

    def claim(self, task_id: str) -> bool:
        """首次发送认领：pending -> sending。"""
        con = self._conn()
        try:
            return self._claim_with(con, task_id, (PENDING,))
        finally:
            con.close()

    def claim_retry(self, task_id: str) -> bool:
        """重试认领：failed 且未超限 -> sending（同样是条件更新）。"""
        con = self._conn()
        try:
            con.execute("BEGIN IMMEDIATE")
            cur = con.execute(
                "UPDATE yy_send_task SET state=?, retry_count=retry_count+1,"
                " finalized_at=NULL WHERE task_id=? AND state=? AND retry_count<?",
                (SENDING, task_id, FAILED, RETRY_LIMIT),
            )
            if cur.rowcount != 1:
                con.execute("ROLLBACK")
                return False
            n = con.execute(
                "SELECT COUNT(*) FROM yy_send_attempt WHERE task_id=?", (task_id,)
            ).fetchone()[0]
            con.execute(
                "INSERT INTO yy_send_attempt(attempt_id, task_id, attempt_no, state,"
                " started_at) VALUES(?,?,?,?,?)",
                (uuid.uuid4().hex, task_id, n + 1, PENDING, now_iso()),
            )
            con.execute("COMMIT")
            return True
        finally:
            con.close()

    def _finish_attempt(self, con: sqlite3.Connection, task_id: str,
                        state: str, classified: str, error: str | None,
                        qq_message_id: str | None) -> None:
        row = con.execute(
            "SELECT attempt_id FROM yy_send_attempt WHERE task_id=?"
            " ORDER BY attempt_no DESC LIMIT 1", (task_id,)).fetchone()
        if row:
            con.execute(
                "UPDATE yy_send_attempt SET state=?, classified_as=?, error=?,"
                " qq_message_id=?, ended_at=? WHERE attempt_id=?",
                (state, classified, error, qq_message_id, now_iso(), row[0]),
            )

    def finish_ok(self, task_id: str, qq_message_id: str | None = None) -> bool:
        """只有在途(sending)任务才允许改成 sent；迟到响应不能覆盖 unknown。"""
        con = self._conn()
        try:
            con.execute("BEGIN IMMEDIATE")
            cur = con.execute(
                "UPDATE yy_send_task SET state=?, error=NULL, finalized_at=?"
                " WHERE task_id=? AND state=?", (SENT, now_iso(), task_id, SENDING))
            if cur.rowcount != 1:
                con.execute("ROLLBACK")
                return False
            self._finish_attempt(con, task_id, SENT, "definite", None, qq_message_id)
            con.execute("COMMIT")
            return True
        finally:
            con.close()

    def finish_err(self, task_id: str, exc: BaseException | None = None,
                   error: str | None = None) -> str:
        """记失败/不确定，返回 classified（definite / unknown）。

        同样只允许从 sending 转出：恢复已经把任务判成 unknown 后，
        迟到的失败/成功响应都不该改写终态。
        """
        classified = classify_exception(exc) if exc is not None else "definite"
        state = FAILED if classified == "definite" else UNKNOWN
        msg = (error or (repr(exc) if exc is not None else "unknown"))[:300]
        con = self._conn()
        try:
            con.execute("BEGIN IMMEDIATE")
            cur = con.execute(
                "UPDATE yy_send_task SET state=?, error=?, finalized_at=?"
                " WHERE task_id=? AND state=?", (state, msg, now_iso(), task_id, SENDING))
            if cur.rowcount != 1:
                con.execute("ROLLBACK")
                return classified
            if state == FAILED:
                con.execute(
                    "UPDATE yy_send_task SET history_state=?"
                    " WHERE task_id=? AND managed_history=1 AND history_state=?",
                    (H_NOT_SENT, task_id, H_PENDING))
            self._finish_attempt(con, task_id, state, classified, msg, None)
            con.execute("COMMIT")
        finally:
            con.close()
        return classified

    def state_of(self, task_id: str) -> str | None:
        row = self.get(task_id)
        return row["state"] if row else None

    def skip(self, task_id: str, reason: str) -> None:
        con = self._conn()
        try:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "UPDATE yy_send_task SET state=?, error=?, finalized_at=?"
                " WHERE task_id=?", (SKIPPED, reason[:300], now_iso(), task_id))
            con.execute("COMMIT")
        finally:
            con.close()

    def confirm_sent(self, task_id: str, qq_message_id: str | None = None) -> bool:
        """由真正的发送方（如管家）回报"已发出"，把任务置为 sent。

        只允许从 pending/sending/failed 转过来；已经是 sent/unknown 的不动
        （unknown 必须人工结案，不能凭一句回报就改成 sent）。
        """
        con = self._conn()
        try:
            con.execute("BEGIN IMMEDIATE")
            cur = con.execute(
                "UPDATE yy_send_task SET state=?, error=NULL, finalized_at=?"
                " WHERE task_id=? AND state IN (?,?,?)",
                (SENT, now_iso(), task_id, PENDING, SENDING, FAILED))
            ok = cur.rowcount == 1
            if ok:
                self._finish_attempt(con, task_id, SENT, "definite", None, qq_message_id)
            con.execute("COMMIT")
            return ok
        finally:
            con.close()

    def resolve(self, task_id: str, as_state: str) -> bool:
        if as_state not in (SENT, DROPPED):
            raise ValueError("as_state 只能是 sent / dropped")
        allowed = (PENDING, FAILED, UNKNOWN) if as_state == DROPPED else (UNKNOWN, FAILED)
        marks = ",".join("?" * len(allowed))
        con = self._conn()
        try:
            cur = con.execute(
                "UPDATE yy_send_task SET state=?, finalized_at=?, error=COALESCE(error,'')"
                f" || ' [resolved:{as_state}]' WHERE task_id=? AND state IN ({marks})",
                (as_state, now_iso(), task_id, *allowed))
            if as_state == DROPPED and cur.rowcount == 1:
                con.execute(
                    "UPDATE yy_send_task SET history_state=?"
                    " WHERE task_id=? AND managed_history=1 AND history_state=?",
                    (H_NOT_SENT, task_id, H_PENDING))
            con.commit()
            return cur.rowcount == 1
        finally:
            con.close()

    # ---------- 去重（只服务主动话题的 L2） ----------
    def recent_push(self, session: str, text: str, scope: str = "proactive") -> None:
        import time

        con = self._conn()
        try:
            con.execute(
                "INSERT INTO yy_recent_sent(session, text_sha, text, scope, sent_at)"
                " VALUES(?,?,?,?,?)", (session, sha(text), text, scope, time.time()))
            con.commit()
        finally:
            con.close()

    def recent_texts(self, session: str, window_s: float = 7 * 24 * 3600,
                     scope: str = "proactive", limit: int = 200) -> list[str]:
        """最近的主动文本（默认 7 天），给“跨天重复话题”用。"""
        import time

        con = self._conn()
        try:
            rows = con.execute(
                "SELECT text FROM yy_recent_sent WHERE session=? AND scope=?"
                " AND sent_at>? ORDER BY sent_at DESC LIMIT ?",
                (session, scope, time.time() - window_s, limit)).fetchall()
        finally:
            con.close()
        return [r[0] for r in rows if r[0]]

    def recent_similar(self, session: str, text: str, *,
                       window_s: float = 900, ratio: float = 0.92) -> str | None:
        """命中返回旧的文本，否则 None。只用于主动话题。"""
        import difflib
        import time

        if len(text) < 6:
            return None
        con = self._conn()
        try:
            rows = con.execute(
                "SELECT text FROM yy_recent_sent WHERE session=? AND scope='proactive'"
                " AND sent_at>?", (session, time.time() - window_s)).fetchall()
        finally:
            con.close()
        for (old,) in rows:
            if not old:
                continue
            if old == text:
                return old
            if difflib.SequenceMatcher(None, old, text).ratio() >= ratio:
                return old
        return None

    # ---------- 历史提交（与标记同事务） ----------
    def commit_history(self, task_id: str, text: str | None = None,
                       umo: str | None = None) -> str:
        """返回 committed / already / skipped_no_conv / missing_task / needs_target / partial.

        P0-B：
        - 目标会话只读 task.conversation_id，绝不在恢复时重新选“最新会话”；
        - 缺少目标时保留 pending 并标 needs_target，让人能看见并修；
        - 同一 reply_id 的其它物理段未全部 sent 时只标 partial，不写整段历史。
        """
        con = self._conn()
        try:
            con.execute("BEGIN IMMEDIATE")
            if con.execute("SELECT 1 FROM yy_history_commit WHERE task_id=?",
                           (task_id,)).fetchone():
                con.execute("ROLLBACK")
                return "already"
            task = con.execute(
                "SELECT conversation_id, text, state, managed_history,"
                " history_state, reply_id, history_text,"
                " source_type, source_ref, event_id"
                " FROM yy_send_task WHERE task_id=?", (task_id,)).fetchone()
            if task is None:
                con.execute("ROLLBACK")
                return "missing_task"
            (cid, task_text, state, managed, hstate, reply_id, stored,
             source_type, source_ref, event_id) = task
            if state != SENT or not managed:
                con.execute("ROLLBACK")
                return "missing_task"
            body = text if text is not None else (stored or task_text)
            if not cid and umo:
                # 只有调用方在“发送/提交现场”显式给出 umo 时才解析；
                # recover() 不传 umo，所以恢复路径永远不会回退到“最新会话”。
                hit = con.execute(
                    "SELECT conversation_id FROM conversations WHERE user_id=?"
                    " ORDER BY updated_at DESC LIMIT 1", (umo,)).fetchone()
                cid = hit[0] if hit else None
            if not cid:
                # 不猜“最新会话”：保留待处理，人工补目标或修数据
                con.execute(
                    "UPDATE yy_send_task SET history_state=?,"
                    " error=COALESCE(error,'') || ' [needs_target]'"
                    " WHERE task_id=?", (H_NEEDS_TARGET, task_id))
                con.execute("COMMIT")
                return "needs_target"
            # C6/A6：只要这一轮有 yy_turn 清单，就必须按清单核对全部序号，
            # 不能只看“已存在的兄弟行”。缺行（第三段根本没建出来）也算 partial。
            turn_row = con.execute(
                "SELECT total, committed_at FROM yy_turn WHERE reply_id=?",
                (reply_id,)).fetchone()
            if turn_row is not None and turn_row[1]:
                con.execute("ROLLBACK")
                return "already"
            if turn_row is not None:
                try:
                    total = int(turn_row[0])
                    rows = con.execute(
                        "SELECT seq,state FROM yy_send_task WHERE reply_id=?"
                        " AND kind<>'sticker'", (reply_id,)).fetchall()
                    seqs = {int(s) for s, _ in rows}
                    complete = (
                        total > 0
                        and len(rows) == total
                        and seqs == set(range(total))
                        and all(st == SENT for _, st in rows)
                    )
                except (TypeError, ValueError):
                    complete = False
                if not complete:
                    con.execute(
                        "UPDATE yy_send_task SET history_state=?,"
                        " error=COALESCE(error,'') || ' [partial]'"
                        " WHERE task_id=?", (H_PARTIAL, task_id))
                    con.execute("COMMIT")
                    return "partial"
            sibling_bad = con.execute(
                "SELECT COUNT(*) FROM yy_send_task WHERE reply_id=? AND task_id<>?"
                " AND state NOT IN (?,?,?)",
                (reply_id, task_id, SENT, SKIPPED, DROPPED)).fetchone()[0]
            if sibling_bad:
                con.execute(
                    "UPDATE yy_send_task SET history_state=?,"
                    " error=COALESCE(error,'') || ' [partial]'"
                    " WHERE task_id=?", (H_PARTIAL, task_id))
                con.execute("COMMIT")
                return "partial"
            row = con.execute("SELECT content FROM conversations WHERE conversation_id=?",
                              (cid,)).fetchone()
            if row is None:
                con.execute(
                    "UPDATE yy_send_task SET history_state=? WHERE task_id=?",
                    (H_SKIPPED, task_id))
                con.execute("COMMIT")
                return "skipped_no_conv"
            hist = json.loads(row[0] or "[]")
            # R09：历史里保留来源元数据，摘要/检索以后能区分虚拟日常、模型输出与原始记录。
            # 当前调用方未传时，至少记为 model_output + task_id，不再是无来源文本。
            hist.append({
                "role": "assistant",
                "content": [{"type": "text", "text": body}],
                "source_type": source_type or "model_output",
                "source_ref": source_ref or task_id,
                "event_id": event_id,
            })
            crash_point("before_history_commit")
            con.execute(
                "UPDATE conversations SET content=?, updated_at=datetime('now')"
                " WHERE conversation_id=?",
                (json.dumps(hist, ensure_ascii=False), cid))
            con.execute(
                "INSERT INTO yy_history_commit(task_id, committed_at, source_type,"
                " source_ref, event_id) VALUES(?,?,?,?,?)",
                (task_id, now_iso(), source_type or "model_output",
                 source_ref or task_id, event_id))
            con.execute("UPDATE yy_send_task SET history_state=? WHERE task_id=?",
                        (H_COMMITTED, task_id))
            if turn_row is not None:
                # 整轮提交和 turn 的 committed_at 同事务落盘，避免 recover
                # 再拿其它兄弟段各提交一次。
                con.execute(
                    "UPDATE yy_turn SET committed_at=?"
                    " WHERE reply_id=? AND committed_at IS NULL",
                    (now_iso(), reply_id))
            con.execute("COMMIT")
            crash_point("after_history_commit")
            return "committed"
        except BaseException:
            try:
                con.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass
            raise
        finally:
            con.close()

    # ---------- 恢复 ----------
    def pending_history(self, include_stuck: bool = False) -> list[dict]:
        """sent 且历史未提交、且由本系统管理历史的 task。

        R05：include_stuck=True 时把 needs_target / partial 也纳入重扫——
        目标会话补齐或兄弟段到齐后，这些任务应该能继续被处理，
        而不是永远卡在那里、统计还报 0。
        """
        hstates = (H_PENDING, H_NEEDS_TARGET, H_PARTIAL) if include_stuck else (H_PENDING,)
        marks = ",".join("?" * len(hstates))
        con = self._conn()
        try:
            rows = con.execute(
                "SELECT task_id, session, conversation_id, text, reply_id, umo,"
                " history_text, history_state FROM yy_send_task"
                f" WHERE state=? AND history_state IN ({marks}) AND managed_history=1"
                " ORDER BY created_at", (SENT, *hstates)).fetchall()
        finally:
            con.close()
        keys = ("task_id", "session", "conversation_id", "text",
                "reply_id", "umo", "history_text", "history_state")
        return [dict(zip(keys, r, strict=False)) for r in rows]

    def stale_sending(self, grace_s: int = UNKNOWN_GRACE_S) -> list[dict]:
        """认领后没下文的（可能崩了），按**本次尝试**的 started_at 计时。

        旧实现用 task.created_at：一个很早就失败、现在才重试的任务，
        刚认领就会被判“超时”。这里取最近一次 attempt 的 started_at。
        """
        cutoff = (datetime.datetime.now()
                  - datetime.timedelta(seconds=grace_s)).strftime("%Y-%m-%d %H:%M:%S.%f")
        con = self._conn()
        try:
            rows = con.execute(
                "SELECT task_id, session, text, created_at,"
                " COALESCE((SELECT started_at FROM yy_send_attempt a"
                "           WHERE a.task_id=t.task_id"
                "           ORDER BY attempt_no DESC LIMIT 1), created_at) AS last_start"
                " FROM yy_send_task t"
                " WHERE state=?"
                " AND COALESCE((SELECT started_at FROM yy_send_attempt a"
                "               WHERE a.task_id=t.task_id"
                "               ORDER BY attempt_no DESC LIMIT 1), created_at) < ?"
                " ORDER BY created_at", (SENDING, cutoff)).fetchall()
        finally:
            con.close()
        keys = ("task_id", "session", "text", "created_at", "last_start")
        return [dict(zip(keys, r, strict=False)) for r in rows]

    def mark_unknown(self, task_id: str, reason: str) -> None:
        con = self._conn()
        try:
            con.execute(
                "UPDATE yy_send_task SET state=?, error=?, finalized_at=?"
                " WHERE task_id=? AND state=?",
                (UNKNOWN, reason[:300], now_iso(), task_id, SENDING))
            con.commit()
        finally:
            con.close()

    def stale_pending_managed(self, hours: int = 6) -> list[dict]:
        """主动生成了但一直没被确认发送的任务（超时就人工/自动放弃）。"""
        cutoff = (datetime.datetime.now()
                  - datetime.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S.%f")
        con = self._conn()
        try:
            rows = con.execute(
                "SELECT task_id, session, text, created_at FROM yy_send_task"
                " WHERE state=? AND managed_history=1 AND created_at<?"
                " ORDER BY created_at", (PENDING, cutoff)).fetchall()
        finally:
            con.close()
        return [dict(zip(("task_id", "session", "text", "created_at"), r, strict=False))
                for r in rows]

    def stats(self) -> dict:
        con = self._conn()
        try:
            rows = con.execute(
                "SELECT state, COUNT(*) FROM yy_send_task GROUP BY state").fetchall()
            hist = con.execute(
                "SELECT history_state, COUNT(*) FROM yy_send_task WHERE managed_history=1"
                " GROUP BY history_state").fetchall()
        finally:
            con.close()
        return {"state": dict(rows), "history": dict(hist)}

    # ---------- 启动恢复 ----------
    # ---------- 整轮（多段）清单：R02 ----------
    def start_turn(self, *, reply_id: str, session: str, total: int,
                   umo: str | None = None, history_text: str | None = None,
                   conversation_id: str | None = None) -> bool:
        """第一段发送前就落库"这一轮本来有几段"，恢复才知道缺哪段。

        C6/A6：同一 reply_id 的 session/total/history_text 首次确定后不可变。
        total 缩减/增加、换目标/换正文都拒绝；缺省字段只允许从 None 补一次。
        """
        if not reply_id or total is None:
            return False
        con = self._conn()
        try:
            row = con.execute(
                "SELECT session,total,umo,history_text,conversation_id"
                " FROM yy_turn WHERE reply_id=?", (reply_id,)).fetchone()
            if row is not None:
                _session, _total, _umo, _hist, _cid = row
                if _session != session or int(_total) != int(total):
                    return False
                if _umo and umo and _umo != umo:
                    return False
                if _hist and history_text and _hist != history_text:
                    return False
                if _cid and conversation_id and _cid != conversation_id:
                    return False
                # 只补空字段，不改已确定字段
                con.execute(
                    "UPDATE yy_turn SET umo=COALESCE(umo,?),"
                    " history_text=COALESCE(history_text,?),"
                    " conversation_id=COALESCE(conversation_id,?)"
                    " WHERE reply_id=?",
                    (umo, history_text, conversation_id, reply_id))
                return True
            con.execute(
                "INSERT INTO yy_turn(reply_id,session,conversation_id,umo,total,"
                " history_text,created_at) VALUES(?,?,?,?,?,?,?)",
                (reply_id, session, conversation_id, umo, int(total),
                 history_text, now_iso()))
            return True
        finally:
            con.close()

    def open_turns(self, limit: int = 50) -> list[dict]:
        con = self._conn()
        try:
            rows = con.execute(
                "SELECT reply_id,total,created_at FROM yy_turn"
                " WHERE committed_at IS NULL ORDER BY created_at LIMIT ?",
                (limit,)).fetchall()
        finally:
            con.close()
        return [{"reply_id": r[0], "total": r[1], "created_at": r[2]} for r in rows]

    def prepare_turn_commit(self, reply_id: str) -> dict:
        """整轮是否可提交：全部有效段 sent 才 ready；否则 partial。

        返回 {"status": ready|partial|already|missing, "task_id": 最后一段}
        """
        con = self._conn()
        try:
            row = con.execute(
                "SELECT reply_id,session,conversation_id,umo,total,history_text,"
                " committed_at FROM yy_turn WHERE reply_id=?", (reply_id,)).fetchone()
            if not row:
                return {"status": "missing", "task_id": None}
            turn = dict(zip(("reply_id", "session", "conversation_id", "umo", "total",
                             "history_text", "committed_at"), row, strict=False))
            if turn["committed_at"]:
                return {"status": "already", "task_id": None}
            tasks = con.execute(
                "SELECT task_id,state,seq FROM yy_send_task WHERE reply_id=?"
                " AND kind<>'sticker' ORDER BY seq", (reply_id,)).fetchall()
        finally:
            con.close()
        if not tasks:
            return {"status": "missing", "task_id": None}
        sent = [t for t in tasks if t[1] == SENT]
        live = [t for t in tasks if t[1] not in (SKIPPED, DROPPED)]
        # C6/A6：不能只数 sent 行；缺段/重复序号/额外段都不能算整篇送达。
        try:
            total = int(turn["total"])
            seqs = [int(t[2]) for t in tasks]
            sent_seqs = [int(t[2]) for t in sent]
            expected = set(range(total))
            actual = set(sent_seqs)
        except (TypeError, ValueError):
            total, seqs, sent_seqs, expected, actual = 0, [], [], set(), set()
        if not (
            total > 0
            and len(tasks) == total
            and len(sent) == total
            and len(live) == total
            and len(set(seqs)) == total
            and len(set(sent_seqs)) == total
            and actual == expected
        ):
            if sent:
                # 部分送达：把已送达段标 partial，避免被当成 external 静默结案
                con = self._conn()
                try:
                    for t in sent:
                        con.execute(
                            "UPDATE yy_send_task SET history_state=?"
                            " WHERE task_id=? AND history_state=?",
                            (H_PARTIAL, t[0], H_EXTERNAL))
                finally:
                    con.close()
            return {"status": "partial", "task_id": None}
        if sent:
            last_id = sent[-1][0]
            con = self._conn()
            try:
                con.execute(
                    "UPDATE yy_send_task SET managed_history=1,"
                    " history_state=CASE WHEN history_state='external' THEN ?"
                    "                     ELSE history_state END,"
                    " umo=COALESCE(umo,?), history_text=COALESCE(history_text,?),"
                    " conversation_id=COALESCE(conversation_id,?)"
                    " WHERE task_id=?",
                    (H_PENDING, turn["umo"], turn["history_text"],
                     turn["conversation_id"], last_id))
            finally:
                con.close()
            return {"status": "ready", "task_id": last_id}
        return {"status": "partial", "task_id": None}

    def mark_turn_committed(self, reply_id: str) -> None:
        con = self._conn()
        try:
            con.execute("UPDATE yy_turn SET committed_at=? WHERE reply_id=?",
                        (now_iso(), reply_id))
        finally:
            con.close()

    def recover(self, ops_log: str | Path | None = None,
                grace_s: int = UNKNOWN_GRACE_S) -> dict:
        """启动/周期恢复：
        1) sent 但历史没提交的 → 用 task 里保存的 cid/正文补写（幂等）；
        2) sending 超过宽限期、没下文的 → 标 unknown 并写运维日志（**不重发**）；
        3) 缺目标会话 / 兄弟段未全送达 → 保留可修复状态并报警，不静默结案。
        """
        committed, failed, unknown = 0, 0, 0
        needs_target, partial, skipped = 0, 0, 0
        lines: list[str] = []
        # R02：先补"整轮已齐但没提交"的多段回复
        for turn in self.open_turns():
            try:
                prep = self.prepare_turn_commit(turn["reply_id"])
                if prep["status"] == "ready" and prep["task_id"]:
                    res = self.commit_history(prep["task_id"])
                    if res in ("committed", "already"):
                        self.mark_turn_committed(turn["reply_id"])
                        committed += 1
                    elif res == "skipped_no_conv":
                        # C6/A6：会话被有意删除和意外缺失现在分开计数；
                        # 这里先关闭整轮，但不冒充“历史提交成功”。
                        self.mark_turn_committed(turn["reply_id"])
                        skipped += 1
                        lines.append(
                            f"[{now_iso()}] 整轮目标会话不存在，记为 skipped_no_conv"
                            f" reply={turn['reply_id']}")
                elif prep["status"] == "partial":
                    partial += 1
                    lines.append(
                        f"[{now_iso()}] 整轮未齐（部分送达） reply={turn['reply_id']}"
                        f" total={turn['total']}")
            except Exception:  # noqa: BLE001
                failed += 1
        for t in self.pending_history(include_stuck=True):
            try:
                res = self.commit_history(t["task_id"])
                if res == "committed":
                    committed += 1
                elif res == "skipped_no_conv":
                    skipped += 1
                    lines.append(
                        f"[{now_iso()}] 历史目标会话不存在 task={t['task_id']}"
                        f" reply={t.get('reply_id')}")
                elif res == "needs_target":
                    needs_target += 1
                    lines.append(
                        f"[{now_iso()}] 历史缺目标会话 task={t['task_id']}"
                        f" reply={t.get('reply_id')} session={t['session']}"
                        f" umo={t.get('umo')} text={(t.get('history_text') or t['text'])[:40]!r}")
                elif res == "partial":
                    partial += 1
                    lines.append(
                        f"[{now_iso()}] 兄弟段未全送达，暂不写整段历史"
                        f" task={t['task_id']} reply={t.get('reply_id')}")
            except Exception:  # noqa: BLE001
                failed += 1
        for t in self.stale_sending(grace_s):
            self.mark_unknown(t["task_id"], "恢复：认领后无下文，可能没发出去")
            unknown += 1
            lines.append(
                f"[{now_iso()}] 可能丢消息 task={t['task_id']} session={t['session']}"
                f" created={t['created_at']} last_start={t.get('last_start')}"
                f" text={t['text'][:40]!r}")
        if lines and ops_log:
            p = Path(ops_log)
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        expired = 0
        for t in self.stale_pending_managed():
            if self.resolve(t["task_id"], DROPPED):
                expired += 1
        return {"history_committed": committed, "history_failed": failed,
                "marked_unknown": unknown, "expired_unsent": expired,
                "needs_target": needs_target, "partial": partial,
                "skipped_no_conv": skipped}


_LEDGER: Ledger | None = None


def get_ledger() -> Ledger:
    global _LEDGER  # noqa: PLW0603
    if _LEDGER is None:
        _LEDGER = Ledger()
    return _LEDGER
