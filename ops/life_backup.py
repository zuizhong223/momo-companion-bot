# -*- coding: utf-8 -*-
"""L2 生活层备份/校验 CLI（只读 + 写备份文件，不接机器人）。

用法：
  python ops/life_backup.py backup
  python ops/life_backup.py verify
  python ops/life_backup.py verify --file ops/backups/life_xxx.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:/demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

TABLES = (
    "yy_life_event",
    "yy_life_state",
    "yy_life_place",
    "yy_life_day_override",
    "yy_life_delay",
    "yy_life_commitment",
    "yy_life_source",
    "yy_life_conflict",
    "yy_life_meta",
)


def _db_path() -> Path:
    from astrbot.core.utils.life_state import _db
    return _db()


def _dump(db: Path) -> dict:
    con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    tables: dict[str, list[dict]] = {}
    try:
        for name in TABLES:
            try:
                tables[name] = [dict(r) for r in con.execute(f"SELECT * FROM {name}")]
            except sqlite3.OperationalError:
                tables[name] = []
    finally:
        con.close()
    body = {
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "db": str(db),
        "tables": tables,
        "counts": {k: len(v) for k, v in tables.items()},
    }
    body["sha256"] = hashlib.sha256(
        json.dumps(tables, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return body


def main() -> int:
    ap = argparse.ArgumentParser(description="life layer backup/verify")
    ap.add_argument("action", choices=("backup", "verify"))
    ap.add_argument("--file", default=None)
    ap.add_argument("--out", default=None, help="backup directory")
    args = ap.parse_args()

    backup_dir = Path(args.out) if args.out else ROOT / "ops" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    db = _db_path()

    if args.action == "backup":
        body = _dump(db)
        out = backup_dir / (
            "life_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S") + ".json")
        out.write_text(json.dumps(body, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        print(f"backup: {out}")
        print(f"counts: {body['counts']}")
        print(f"sha256: {body['sha256']}")
        return 0

    if args.file:
        path = Path(args.file)
    else:
        files = sorted(backup_dir.glob("life_*.json"))
        if not files:
            print("no backup file", file=sys.stderr)
            return 2
        path = files[-1]
    body = json.loads(path.read_text(encoding="utf-8"))
    expected = body.get("counts", {})
    cur_body = _dump(db)
    cur = cur_body["counts"]
    ok = (all(cur.get(k, 0) == v for k, v in expected.items())
          and cur_body.get("sha256") == body.get("sha256"))
    print(f"file: {path}")
    print(f"expected: {expected}")
    print(f"current:  {cur}")
    print(f"sha256:   {'MATCH' if cur_body.get('sha256') == body.get('sha256') else 'DIFF'}")
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
