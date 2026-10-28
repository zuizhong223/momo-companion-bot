# -*- coding: utf-8 -*-
"""P3 S9 最小健康报告：有分母、有窗口，不把“异常为 0”当通过。"""
from __future__ import annotations

import json
import sqlite3
import socket
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:/demo")
DB = ROOT / "instances/11111111-1111-4111-8111-111111111111/core/data/data_v4.db"
BACKUPS = ROOT / "backups"


def port(host: str, p: int) -> bool:
    try:
        with socket.create_connection((host, p), timeout=2):
            return True
    except OSError:
        return False


def main() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    states = {r["state"]: r["n"] for r in con.execute(
        "select state,count(*) n from yy_send_task group by state")}
    hist = {r["history_state"]: r["n"] for r in con.execute(
        "select history_state,count(*) n from yy_send_task group by history_state")}
    attempts = con.execute("select count(*) from yy_send_attempt").fetchone()[0]
    last = con.execute("select created_at,kind,text,state from yy_send_task order by created_at desc limit 1").fetchone()
    conv = con.execute("select updated_at from conversations order by updated_at desc limit 1").fetchone()
    integrity = con.execute("pragma integrity_check").fetchone()[0]
    con.close()

    sent = states.get("sent", 0)
    failed = states.get("failed", 0)
    unknown = states.get("unknown", 0)
    dropped = states.get("dropped", 0)
    total = sent + failed + unknown + dropped
    rate = (sent / total) if total else None

    zips = sorted(BACKUPS.glob("demo_backup_*.zip"), key=lambda p: p.stat().st_mtime)
    b = zips[-1] if zips else None
    b_age = (datetime.now() - datetime.fromtimestamp(b.stat().st_mtime)).total_seconds() / 3600 if b else None

    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "ports": {str(p): port("127.0.0.1", p) for p in (22877, 6199, 11434)},
        "db": {"path": str(DB), "integrity": integrity, "size_mb": round(DB.stat().st_size / 1048576, 1)},
        "tasks": {"total": total, "sent": sent, "failed": failed, "unknown": unknown,
                  "dropped": dropped, "sent_rate": None if rate is None else round(rate, 4)},
        "history": hist,
        "attempts": attempts,
        "last_send": dict(last) if last else None,
        "conversation_updated_at": conv["updated_at"] if conv else None,
        "backup": {"latest": b.name if b else None,
                   "size_mb": round(b.stat().st_size / 1048576, 1) if b else None,
                   "age_hours": None if b_age is None else round(b_age, 2)},
        "notes": [
            "sent_rate 分母是已结案任务；0 次尝试时不输出通过率",
            "unknown 是未确认送达，不等于失败",
            "QQ 连接数需用 ops/tasks.py status 另查（本脚本只测端口）",
        ],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
