#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Bot生活层 · 影子实现（依据《生活层_设计与接入契约_20261025》）

边界（严格遵守契约）：
* 本模块只在**影子库/影子日志**里工作，不发送任何消息、不修改线上 AstrBot 状态。
* 计时推进 `advance()` 不调用模型；只做确定性转移。
* 未确认的正典（时区/乐器/作息/关系）**不写死**：`canon.jsonl` 里全是 pending，context 不会据此断言。
* 事件、状态、承诺都在短事务内更新，用版本比较做并发控制。

CLI:
  python life.py init                    建库 + 建表
  python life.py status                  打印世界状态
  python life.py advance [--now ISO]     推进一次（默认当前 UTC）
  python life.py shadow                  生成一次上下文快照，写影子日志
  python life.py selftest                内置检查（不碰网络）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

HERE = Path(__file__).resolve().parent
SCHEMA = HERE / "schema.sql"
DEFAULT_DB = HERE / "life_shadow.db"
CANON = HERE / "canon.jsonl"
SHADOW_LOG = HERE / "shadow.jsonl"

UTC = timezone.utc
# 契约 §11：三档回退开关，默认全关（影子模式）
SWITCHES = {
    "life_state_injection": False,
    "life_proactive": False,
    "life_delay": False,
}
# 契约 §3：未确认前不启用地点/课表，只推进时间与精力
DEFAULT_ENERGY_DECAY_PER_H = 0.02
DEFAULT_MOOD_REGRESS_PER_H = 0.01
OFFLINE_GAP_HOURS = 30.0


# ---------------------------------------------------------------- 时间

def now_utc() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    s = s.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def resolve_local(tz_name: str, naive_local: datetime):
    """把"角色本地时间"解析成 UTC。

    返回 (utc_dt|None, note)：
      note = 'ok' | 'nonexistent'（夏令时跳过的时刻）| 'ambiguous'（回拨重复的时刻）
    契约 §3：不存在的当地时间必须明确跳过并记录理由；重复时间要保存偏移。
    """
    if ZoneInfo is None:
        raise RuntimeError("需要 Python 3.9+ 的 zoneinfo")
    tz = ZoneInfo(tz_name)
    dt0 = naive_local.replace(tzinfo=tz, fold=0)
    utc0 = dt0.astimezone(UTC)
    back = utc0.astimezone(tz).replace(tzinfo=None)
    if back != naive_local:
        return None, "nonexistent"
    dt1 = naive_local.replace(tzinfo=tz, fold=1)
    if dt1.utcoffset() != dt0.utcoffset():
        return utc0, "ambiguous"
    return utc0, "ok"


def local_date(utc_dt: datetime, tz_name: str) -> str:
    return utc_dt.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- 存储

def connect(db_path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))


def ensure_world(conn, world_id: str, character_id: str, tz_name: str,
                 at: datetime | None = None) -> sqlite3.Row:
    at = at or now_utc()
    conn.execute(
        """INSERT OR IGNORE INTO world_state
           (world_id, character_id, version, tz, energy, global_mood,
            last_advance_utc, updated_at)
           VALUES (?,?,1,?,0.6,0.0,?,?)""",
        (world_id, character_id, tz_name, iso(at), iso(at)),
    )
    return get_world(conn, world_id)


def get_world(conn, world_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM world_state WHERE world_id=?", (world_id,)).fetchone()


class VersionConflict(RuntimeError):
    pass


def add_source(conn, source_type: str, ref: str | None = None, speaker: str | None = None,
               occurred_at: str | None = None, verification_status: str = "candidate",
               visibility_scope: str = "private", raw: bytes | None = None) -> str:
    sid = "src_" + uuid.uuid4().hex[:16]
    sha = hashlib.sha256(raw).hexdigest() if raw is not None else None
    conn.execute(
        """INSERT INTO source(source_id, source_type, ref, speaker, occurred_at, fetched_at,
                              read_scope, raw_sha, verification_status, visibility_scope)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (sid, source_type, ref, speaker, occurred_at, iso(now_utc()),
         "ops/life", sha, verification_status, visibility_scope),
    )
    return sid


def emit_event(conn, world_id: str, idempotency_key: str, kind: str,
               start_utc: str, end_utc: str | None, status: str,
               payload: dict | None = None, source_id: str | None = None,
               visibility_scope: str = "private") -> str | None:
    """幂等写入事件。已存在同键则返回 None（调用方据此判断是否重复）。"""
    eid = "ev_" + hashlib.sha256(f"{world_id}|{idempotency_key}".encode()).hexdigest()[:20]
    cur = conn.execute(
        """INSERT OR IGNORE INTO life_event
           (event_id, world_id, idempotency_key, source_id, kind, start_utc, end_utc,
            status, visibility_scope, payload, revision)
           VALUES (?,?,?,?,?,?,?,?,?,?,0)""",
        (eid, world_id, idempotency_key, source_id, kind, start_utc, end_utc,
         status, visibility_scope, json.dumps(payload or {}, ensure_ascii=False)),
    )
    return eid if cur.rowcount == 1 else None


# ---------------------------------------------------------------- 推进（无模型）

def advance(conn, world_id: str, at: datetime, expected_version: int | None = None) -> dict:
    """确定性推进到 at。不调用模型；短事务；版本比较。

    契约 §3/§10：离线 30 小时直接算到当前，只产生少量聚合事件，不补发全部消息。
    """
    w = get_world(conn, world_id)
    if w is None:
        raise KeyError(f"world 不存在: {world_id}")
    if expected_version is not None and w["version"] != expected_version:
        raise VersionConflict(f"expected v{expected_version}, got v{w['version']}")

    last = parse_iso(w["last_advance_utc"])
    if at <= last:
        return {"advanced": False, "reason": "not_forward", "version": w["version"]}

    hours = (at - last).total_seconds() / 3600.0
    energy = max(0.0, min(1.0, w["energy"] - DEFAULT_ENERGY_DECAY_PER_H * hours))
    mood = w["global_mood"] * max(0.0, 1.0 - DEFAULT_MOOD_REGRESS_PER_H * hours)

    emitted = []
    conn.execute("BEGIN IMMEDIATE")
    try:
        if hours >= OFFLINE_GAP_HOURS:
            key = f"{world_id}:offline_gap:{at.strftime('%Y%m%d%H')}"
            eid = emit_event(conn, world_id, key, "offline_gap", iso(last), iso(at), "unknown",
                             {"hours": round(hours, 1), "note": "离线聚合，不补发全部消息"})
            if eid:
                emitted.append(eid)
        # 跨当地日期：一天最多一条 tick，避免补发洪水
        d0, d1 = local_date(last, w["tz"]), local_date(at, w["tz"])
        if d0 != d1:
            key = f"{world_id}:day:{d1}"
            eid = emit_event(conn, world_id, key, "tick", iso(last), iso(at), "done",
                             {"local_date": d1})
            if eid:
                emitted.append(eid)

        cur = conn.execute(
            """UPDATE world_state
                  SET version=version+1, energy=?, global_mood=?,
                      last_advance_utc=?, updated_at=?
                WHERE world_id=? AND version=?""",
            (round(energy, 4), round(mood, 4), iso(at), iso(at), world_id, w["version"]),
        )
        if cur.rowcount != 1:
            raise VersionConflict("并发修改，重读后重试")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return {"advanced": True, "hours": round(hours, 2), "energy": round(energy, 4),
            "mood": round(mood, 4), "version": w["version"] + 1, "events": emitted}


# ---------------------------------------------------------------- 正典

def load_canon(path: Path = CANON) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(json.loads(line))
    return out


def canon_pending(path: Path = CANON) -> list[dict]:
    return [c for c in load_canon(path) if c.get("status") != "confirmed"]


# ---------------------------------------------------------------- 上下文

def build_context(conn, world_id: str, umo: str, at: datetime,
                  purpose: str = "passive", turn_id: str | None = None,
                  budget_chars: int = 1000) -> dict:
    """契约 §10 的返回结构。只读；不做任何断言扩展。"""
    w = get_world(conn, world_id)
    if w is None:
        return {
            "text": "", "state_version": None, "relation_version": None,
            "visibility_version": None, "conversation_revision": None,
            "latest_inbound_id": None, "valid_until_utc": None,
            "source_event_ids": [], "source_ids": [], "allowed_claims": [],
            "degraded": True, "reason": "no_world_state",
            "purpose": purpose, "turn_id": turn_id, "umo": umo,
        }

    events = conn.execute(
        """SELECT * FROM life_event
            WHERE world_id=? AND start_utc <= ?
            ORDER BY start_utc DESC LIMIT 5""",
        (world_id, iso(at)),
    ).fetchall()

    allowed, lines = [], []
    ld = local_date(at, w["tz"])
    lines.append(f"（世界时间 {ld}，角色时区 {w['tz']}）")
    lines.append(f"精力 {w['energy']:.2f}／心情 {w['global_mood']:+.2f}")
    if w["activity_event_id"]:
        act = conn.execute("SELECT * FROM life_event WHERE event_id=?",
                           (w["activity_event_id"],)).fetchone()
        if act:
            allowed.append({"claim": f"正在做 {act['kind']}", "event_id": act["event_id"]})
            lines.append(f"当前活动：{act['kind']}")
    for e in events:
        lines.append(f"- {e['kind']} ({e['status']})")
        if e["status"] in ("done", "active"):
            allowed.append({"claim": f"{e['kind']} {e['status']}",
                            "event_id": e["event_id"], "start_utc": e["start_utc"]})

    pend = canon_pending()
    if pend:
        lines.append(f"注意：有 {len(pend)} 条正典待审计，不得据此断言具体地点/作息/关系。")

    # 契约 §10：按完整证据块裁剪，不切断半句、不丢来源
    while len("\n".join(lines)) > budget_chars and len(lines) > 1:
        lines.pop()
    text = "\n".join(lines)

    return {
        "text": text,
        "state_version": w["version"],
        "relation_version": None,
        "visibility_version": 1,
        "conversation_revision": None,
        "latest_inbound_id": None,
        "valid_until_utc": iso(at + timedelta(minutes=15)),
        "source_event_ids": [e["event_id"] for e in events],
        "source_ids": [],
        "allowed_claims": allowed,
        "degraded": False, "reason": "",
        "purpose": purpose, "turn_id": turn_id, "umo": umo,
    }


def validate_context(snapshot: dict, at: datetime, world: sqlite3.Row | None) -> tuple[bool, str]:
    """契约 §10：发送前核对世界版本与有效期。"""
    if snapshot.get("degraded"):
        return False, "degraded"
    if world is None:
        return False, "world_gone"
    if snapshot["state_version"] != world["version"]:
        return False, "state_version_changed"
    if snapshot["valid_until_utc"] and parse_iso(snapshot["valid_until_utc"]) < at:
        return False, "expired"
    return True, "ok"


# ---------------------------------------------------------------- 影子

def shadow_once(conn, world_id: str, umo: str, at: datetime, log_path: Path = SHADOW_LOG,
                purpose: str = "passive") -> dict:
    """只生成快照并落盘，不发送、不调模型。"""
    snap = build_context(conn, world_id, umo, at, purpose=purpose,
                         turn_id="shadow_" + uuid.uuid4().hex[:8])
    rec = {"at": iso(at), "world_id": world_id, "umo": umo,
           "switches": SWITCHES, "snapshot": snap}
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return snap


# ---------------------------------------------------------------- CLI

def _db(args) -> sqlite3.Connection:
    conn = connect(args.db)
    migrate(conn)
    return conn


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Bot生活层 · 影子实现")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init")
    sub.add_parser("status")
    sub.add_parser("selftest")

    p_adv = sub.add_parser("advance")
    p_adv.add_argument("--now", default=None)
    p_adv.add_argument("--world", default="demo")
    p_adv.add_argument("--character", default="demo")
    p_adv.add_argument("--tz", default="Asia/Shanghai")
    p_adv.add_argument("--expect", type=int, default=None)

    p_sh = sub.add_parser("shadow")
    p_sh.add_argument("--now", default=None)
    p_sh.add_argument("--world", default="demo")
    p_sh.add_argument("--umo", default="aiocqhttp:FriendMessage:shadow")

    args = ap.parse_args(argv)
    conn = _db(args)

    if args.cmd == "init":
        ensure_world(conn, "demo", "demo", "Asia/Shanghai")
        print(f"OK schema=v1 db={args.db} canon_pending={len(canon_pending())}")
        return 0

    if args.cmd == "status":
        w = get_world(conn, "demo")
        if not w:
            print("no world")
            return 1
        print(json.dumps({k: w[k] for k in w.keys()}, ensure_ascii=False, indent=1))
        print("events:", conn.execute("SELECT COUNT(*) FROM life_event").fetchone()[0])
        return 0

    if args.cmd == "advance":
        ensure_world(conn, args.world, args.character, args.tz)
        at = parse_iso(args.now) if args.now else now_utc()
        print(json.dumps(advance(conn, args.world, at, args.expect), ensure_ascii=False))
        return 0

    if args.cmd == "shadow":
        ensure_world(conn, args.world, args.world, "Asia/Shanghai")
        at = parse_iso(args.now) if args.now else now_utc()
        snap = shadow_once(conn, args.world, args.umo, at)
        print(json.dumps(snap, ensure_ascii=False, indent=1))
        return 0

    if args.cmd == "selftest":
        import unittest
        suite = unittest.defaultTestLoader.discover(str(HERE / "tests"), pattern="test_*.py")
        res = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if res.wasSuccessful() else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
