# -*- coding: utf-8 -*-
"""真实验收前置快照：只读 DB + /im/bots，不发消息。

不传 --require-online 时只报告；传了且没有在线 bot 则退出 1。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:/demo")
DB = Path(os.environ.get("DEMO_DB") or
          ROOT / "instances/11111111-1111-4111-8111-111111111111/core/data/data_v4.db")
BASE = os.environ.get("DEMO_ASTRBOT_BASE") or "http://127.0.0.1:22877"
sys.path.insert(0, str(ROOT))


def table_count(con, sql: str) -> int:
    try:
        return con.execute(sql).fetchone()[0]
    except sqlite3.OperationalError:
        return -1


def bots() -> list[dict]:
    try:
        from local_keys import OPENAPI_KEY  # noqa: PLC0415
        req = urllib.request.Request(
            f"{BASE}/api/v1/im/bots",
            headers={"Authorization": f"Bearer {OPENAPI_KEY}"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        data = data.get("data") if isinstance(data, dict) else data
        if isinstance(data, dict) and "bot_ids" in data:
            return [{"id": x, "status": "registered"} for x in (data.get("bot_ids") or [])]
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        return []
    except Exception as e:  # noqa: BLE001
        print(f"bots: ERROR {type(e).__name__}: {str(e)[:100]}")
        return []


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        print(f"DB: {DB}")
        print("conversations =", table_count(con, "select count(*) from conversations"))
        print("personas =", table_count(con, "select count(*) from personas"))
        print("yy_send_task =", table_count(con, "select count(*) from yy_send_task"))
        for st in ("sending", "sent", "failed", "unknown", "dropped", "skipped"):
            print(f"  task[{st}] =",
                  table_count(con, f"select count(*) from yy_send_task where state='{st}'"))
        for hs in ("pending", "committed", "needs_target", "partial"):
            print(f"  history[{hs}] =",
                  table_count(con, f"select count(*) from yy_send_task where history_state='{hs}'"))
        print("yy_fact2 active =",
              table_count(con, "select count(*) from yy_fact2 where status='active'"))
        print("yy_fact2 candidate =",
              table_count(con, "select count(*) from yy_fact2 where status='candidate'"))
        print("yy_mood =", table_count(con, "select count(*) from yy_mood"))
        for t in ("yy_life_state", "yy_life_event", "yy_life_commitment", "yy_life_source"):
            print(f"{t} =", table_count(con, f"select count(*) from {t}"))
        print("recent tasks:")
        for row in con.execute(
                "select task_id,state,retry_count,created_at from yy_send_task"
                " order by created_at desc limit 5"):
            print("  ", row)
    finally:
        con.close()

    bs = bots()
    online = [b for b in bs if str(b.get("status") or "").lower() in
              ("online", "connected", "ok") or b.get("online") is True]
    print(f"bots = {len(bs)}, online = {len(online)}")
    for b in bs:
        print("  ", {k: b.get(k) for k in ("id", "platform", "status", "online")})
    if "--require-online" in sys.argv and not online:
        print("RESULT: BLOCKED（没有在线 bot；/im/bots 只返回已注册 id，需实发验证）")
        return 1
    print("RESULT: READY" if online else "RESULT: DB_OK / QQ_UNKNOWN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
