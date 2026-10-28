# -*- coding: utf-8 -*-
"""P2-4 观察快照：只读台账，记录 sent/failed/unknown/partial 增量。

用法：
  python ops\\verify\\observe.py
输出一行 JSON 到 ops\\alerts\\observe.jsonl，并打印增量。
不发消息、不改台账。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:/demo")
DB = Path(os.environ.get("DEMO_DB") or
          ROOT / "instances/11111111-1111-4111-8111-111111111111/core/data/data_v4.db")
OUT = ROOT / "ops" / "alerts" / "observe.jsonl"
LOG = ROOT / "instances/11111111-1111-4111-8111-111111111111/core/data/logs/astrbot.log"


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    idx = int(round((len(xs) - 1) * p / 100.0))
    idx = max(0, min(len(xs) - 1, idx))
    return xs[idx]


def log_metrics() -> dict:
    """从日志尾部取 TTS 成功/失败和被动去重次数；不读全文。"""
    d = {"log_window": "last2MB", "tts_requests": 0, "tts_results": 0,
         "tts_fail_rate": None, "dedup_skip": 0}
    try:
        with LOG.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 2_000_000))
            text = f.read().decode("utf-8", errors="ignore")
    except OSError:
        return d
    for line in text.splitlines():
        if "TTS request:" in line:
            d["tts_requests"] += 1
        elif "TTS result:" in line:
            d["tts_results"] += 1
        elif "[被动去重]" in line:
            d["dedup_skip"] += 1
    if d["tts_requests"]:
        d["tts_fail_rate"] = round(
            max(0, d["tts_requests"] - d["tts_results"]) / d["tts_requests"], 3)
    return d


def counts() -> dict:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        d = {}
        for st in ("sent", "failed", "unknown", "dropped", "sending"):
            d[st] = con.execute(
                "select count(*) from yy_send_task where state=?", (st,)).fetchone()[0]
        for hs in ("committed", "pending", "needs_target", "partial"):
            d[f"h_{hs}"] = con.execute(
                "select count(*) from yy_send_task where history_state=? and state='sent'",
                (hs,)).fetchone()[0]
        d["payload_mismatch"] = con.execute(
            "select count(*) from yy_send_task where error like '%payload_mismatch%'"
        ).fetchone()[0]
        # P0/A5：新冲突落独立事件表；旧库没有这张表时回退到 error 字段。
        has_conflict = con.execute(
            "select 1 from sqlite_master where type='table' and name='yy_send_conflict'"
        ).fetchone()
        d["payload_mismatch_last_at"] = None
        if has_conflict:
            d["payload_mismatch"] = con.execute(
                "select count(*) from yy_send_conflict"
            ).fetchone()[0]
            row = con.execute(
                "select max(created_at) from yy_send_conflict"
            ).fetchone()
            d["payload_mismatch_last_at"] = row[0] if row else None
        d["total"] = con.execute("select count(*) from yy_send_task").fetchone()[0]
        try:
            d["attempts"] = con.execute(
                "select count(*) from yy_send_attempt").fetchone()[0]
        except sqlite3.OperationalError:
            d["attempts"] = 0
        d["sent_rate"] = (round(d["sent"] / d["attempts"], 3)
                          if d["attempts"] else None)
        # S9：最近 500 次 sent 尝试的端到端耗时（开始认领→结束），不依赖日志。
        d["send_latency_n"] = 0
        d["send_p50_ms"] = None
        d["send_p95_ms"] = None
        try:
            rows = con.execute(
                "select started_at, ended_at from yy_send_attempt"
                " where state='sent' and started_at is not null and ended_at is not null"
                " order by rowid desc limit 500").fetchall()
            vals: list[float] = []
            for started, ended in rows:
                try:
                    a = dt.datetime.fromisoformat(started)
                    b = dt.datetime.fromisoformat(ended)
                    if a.tzinfo is None:
                        a = a.replace(tzinfo=dt.timezone.utc)
                    if b.tzinfo is None:
                        b = b.replace(tzinfo=dt.timezone.utc)
                    ms = (b - a).total_seconds() * 1000.0
                    if 0 <= ms < 600_000:
                        vals.append(ms)
                except (TypeError, ValueError):
                    continue
            d["send_latency_n"] = len(vals)
            d["send_p50_ms"] = (round(_pct(vals, 50), 1) if vals else None)
            d["send_p95_ms"] = (round(_pct(vals, 95), 1) if vals else None)
        except sqlite3.OperationalError:
            pass
        row = con.execute(
            "select min(created_at) from yy_send_task"
            " where history_state in ('pending','needs_target','partial')"
        ).fetchone()
        d["oldest_pending_at"] = row[0] if row else None
        d["oldest_pending_age_s"] = None
        if row and row[0]:
            try:
                t = dt.datetime.fromisoformat(row[0])
                if t.tzinfo is None:
                    t = t.replace(tzinfo=dt.timezone.utc)
                d["oldest_pending_age_s"] = int(
                    (dt.datetime.now(dt.timezone.utc) - t).total_seconds())
            except Exception:  # noqa: BLE001
                pass
        # S9：unknown / sending 的积压年龄，别只看总数。
        for state, prefix in (("unknown", "unknown"), ("sending", "sending")):
            r = con.execute(
                "select min(created_at) from yy_send_task where state=?",
                (state,)).fetchone()
            d[f"{prefix}_oldest_at"] = r[0] if r else None
            d[f"{prefix}_oldest_age_s"] = None
            if r and r[0]:
                try:
                    t = dt.datetime.fromisoformat(r[0])
                    if t.tzinfo is None:
                        t = t.replace(tzinfo=dt.timezone.utc)
                    d[f"{prefix}_oldest_age_s"] = int(
                        (dt.datetime.now(dt.timezone.utc) - t).total_seconds())
                except Exception:  # noqa: BLE001
                    pass
        d["alerts"] = []
        if d.get("unknown"):
            d["alerts"].append(f"unknown={d['unknown']}")
        if d.get("sending"):
            d["alerts"].append(f"sending={d['sending']}")
        if d.get("h_pending") or d.get("h_needs_target") or d.get("h_partial"):
            d["alerts"].append(
                f"history_pending={d['h_pending'] + d['h_needs_target'] + d['h_partial']}")
        if d.get("payload_mismatch"):
            d["alerts"].append(f"payload_mismatch={d['payload_mismatch']}")
        d.update(log_metrics())
        return d
    finally:
        con.close()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    cur = counts()
    prev = {}
    if OUT.exists():
        lines = OUT.read_text(encoding="utf-8").splitlines()
        if lines:
            try:
                prev = json.loads(lines[-1])
            except Exception:  # noqa: BLE001
                prev = {}
    delta = {}
    for k, v in cur.items():
        if k not in prev:
            delta[k] = None          # 新字段只建基线，不把全量当增量
        elif isinstance(v, (int, float)) and not isinstance(v, bool) \
                and isinstance(prev.get(k), (int, float)):
            delta[k] = v - prev[k]
        else:
            delta[k] = None
    row = {"time": now, **cur, "delta": delta}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
