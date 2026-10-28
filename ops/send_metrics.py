# -*- coding: utf-8 -*-
"""S9 发送健康指标（只读，不接机器人）。

用法：
  python ops/send_metrics.py
  python ops/send_metrics.py --hours 24
  python ops/send_metrics.py --json

指标：state 分布、history_state 分布、失败率、p95 发送耗时、retry 数。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import statistics
import sys
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:/demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))


def _db_path() -> Path:
    from astrbot.core.utils.paths import db_path
    return Path(os.environ.get("DEMO_DB") or db_path())


def _parse(s):
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(str(s))
        return d if d.tzinfo else d.astimezone()
    except ValueError:
        return None


def _p95(values):
    if not values:
        return None
    values = sorted(values)
    i = min(len(values) - 1, int(round(0.95 * (len(values) - 1))))
    return values[i]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    cutoff = dt.datetime.now().astimezone() - dt.timedelta(hours=args.hours)
    db = _db_path()
    con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT state,history_state,kind,retry_count,created_at,finalized_at"
            " FROM yy_send_task ORDER BY created_at DESC LIMIT 5000")]
    finally:
        con.close()
    rows = [r for r in rows if (_parse(r.get("created_at")) or cutoff) >= cutoff]
    states: dict[str, int] = {}
    hist: dict[str, int] = {}
    kinds: dict[str, int] = {}
    durations = []
    retries = 0
    for r in rows:
        states[r.get("state") or "?"] = states.get(r.get("state") or "?", 0) + 1
        hist[r.get("history_state") or "?"] = hist.get(r.get("history_state") or "?", 0) + 1
        kinds[r.get("kind") or "?"] = kinds.get(r.get("kind") or "?", 0) + 1
        retries += int(r.get("retry_count") or 0)
        a, b = _parse(r.get("created_at")), _parse(r.get("finalized_at"))
        if a and b and b >= a:
            durations.append((b - a).total_seconds())
    total = len(rows)
    failed = states.get("failed", 0)
    unknown = states.get("unknown", 0)
    out = {
        "db": str(db),
        "hours": args.hours,
        "total": total,
        "states": states,
        "history_states": hist,
        "kinds": kinds,
        "failed_rate": round(failed / total, 4) if total else None,
        "unknown_rate": round(unknown / total, 4) if total else None,
        "retry_total": retries,
        "p50_s": round(statistics.median(durations), 3) if durations else None,
        "p95_s": round(_p95(durations), 3) if durations else None,
    }
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        for k, v in out.items():
            print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
