# -*- coding: utf-8 -*-
"""P1-B15：固定源码版本 + 分阶段真实验收（影子 → 被动注入 → 主动推送）。

它不假装“跑过一次启动”就是长期证据：
  - 每次记录关键文件 sha256、git HEAD、进程 flags、DB 计数；
  - 同一阶段必须同指纹，且持续 >= 要求小时数，才 PASS；
  - 指纹变了自动判“版本已变，重新计时”。

用法：
  python ops\\verify\\phase_acceptance.py --init shadow
  python ops\\verify\\phase_acceptance.py --check shadow --min-hours 24
  python ops\\verify\\phase_acceptance.py --init passive --min-hours 24
  python ops\\verify\\phase_acceptance.py --check proactive --min-hours 72
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:/demo")
DB = ROOT / "instances/11111111-1111-4111-8111-111111111111/core/data/data_v4.db"
STATE = ROOT / "ops/verify/phase_state.json"
LOG = ROOT / "astrbot_run2.log"
FILES = [
    ROOT / "instances/11111111-1111-4111-8111-111111111111/core/astrbot/core/utils/life_state.py",
    ROOT / "instances/11111111-1111-4111-8111-111111111111/core/astrbot/core/astr_main_agent.py",
    ROOT / "instances/11111111-1111-4111-8111-111111111111/core/astrbot/dashboard/services/proactive_service.py",
    ROOT / "Bot管家.py",
]


def _sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return "missing"


def _git_head() -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            text=True, encoding="utf-8", timeout=10).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _flags() -> dict:
    return {
        "DEMO_LIFE": os.environ.get("DEMO_LIFE"),
        "DEMO_LIFE_SHADOW": os.environ.get("DEMO_LIFE_SHADOW"),
        "DEMO_LIFE_REMINDER": os.environ.get("DEMO_LIFE_REMINDER"),
        "DEMO_RELATION": os.environ.get("DEMO_RELATION"),
    }


def _log_flags() -> str:
    if not LOG.exists():
        return ""
    try:
        lines = LOG.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        if "启动状态" in line or "enabled=" in line:
            return line.strip()[-180:]
    return ""


def _db_counts() -> dict:
    out = {}
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)
    except sqlite3.Error as e:
        return {"error": str(e)}
    try:
        for t in ("yy_send_task", "yy_life_event", "yy_life_conflict",
                  "yy_life_commitment", "yy_life_topic"):
            try:
                out[t] = con.execute(f"select count(*) from {t}").fetchone()[0]
            except sqlite3.OperationalError:
                out[t] = -1
        try:
            out["unknown"] = con.execute(
                "select count(*) from yy_send_task where state='unknown'").fetchone()[0]
        except sqlite3.OperationalError:
            pass
    finally:
        con.close()
    return out


def _fingerprint() -> dict:
    return {"git": _git_head(),
            "files": {p.name: _sha(p) for p in FILES},
            "log_flags": _log_flags()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", choices=("shadow", "passive", "proactive"))
    ap.add_argument("--check", choices=("shadow", "passive", "proactive"))
    ap.add_argument("--min-hours", type=float, default=24)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    state = {}
    if STATE.exists():
        try:
            state = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            state = {}

    fp = _fingerprint()
    now = datetime.datetime.now(datetime.timezone.utc)
    phase = args.init or args.check
    if args.init:
        state = {"phase": phase, "started_at": now.isoformat(timespec="seconds"),
                 "fingerprint": fp, "db": _db_counts(), "flags": _flags()}
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1),
                         encoding="utf-8")
        print(f"INIT {phase} at {state['started_at']}")
        print("fingerprint:", json.dumps(fp, ensure_ascii=False))
        print("RESULT: STARTED（现在开始计时，不要改这些文件）")
        return 0

    if not state:
        print("没有 phase_state；先 --init shadow")
        return 2
    if state.get("phase") != phase:
        print(f"当前记录阶段是 {state.get('phase')}，不是 {phase}")
        return 2
    if state.get("fingerprint") != fp:
        print("FAIL: 源码/flags 指纹已变，本阶段计时作废；重新 --init")
        print("  old:", json.dumps(state.get("fingerprint"), ensure_ascii=False))
        print("  new:", json.dumps(fp, ensure_ascii=False))
        return 1
    started = datetime.datetime.fromisoformat(state["started_at"])
    elapsed = (now - started).total_seconds() / 3600
    db = _db_counts()
    print(f"phase={phase} started={state['started_at']} elapsed={elapsed:.1f}h "
          f"required={args.min_hours:.1f}h")
    print("git:", fp["git"], "files:", fp["files"])
    print("flags:", _flags())
    print("log:", fp["log_flags"])
    print("db:", db)
    ok = elapsed >= args.min_hours
    print("RESULT:", "PASS" if ok else f"PENDING（还差 {args.min_hours - elapsed:.1f}h）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
