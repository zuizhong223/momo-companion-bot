# -*- coding: utf-8 -*-
"""S11：删除会话先备份，默认 dry-run。"""
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(r"D:\demo")
SCRIPT = ROOT / "ops/data/delete.py"
PASS = True


def check(cond, label, extra=""):
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    db = Path(tempfile.mkdtemp(prefix="yy_del_")) / "t.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE conversations (conversation_id TEXT PRIMARY KEY,"
                " content TEXT, updated_at TEXT)")
    con.execute("INSERT INTO conversations VALUES('cid1','[]','2026-10-26')")
    con.commit()
    con.close()

    print("\n[1] dry-run 不删")
    p = subprocess.run([sys.executable, str(SCRIPT), "--db", str(db),
                        "--conversation-id", "cid1"], capture_output=True,
                       text=True, encoding="utf-8")
    check(p.returncode == 0 and "dry-run" in p.stdout, p.stdout[-80:])
    con = sqlite3.connect(db)
    n = con.execute("select count(*) from conversations").fetchone()[0]
    con.close()
    check(n == 1, "仍存在")

    print("\n[2] --apply 删除并备份")
    p = subprocess.run([sys.executable, str(SCRIPT), "--db", str(db),
                        "--conversation-id", "cid1", "--apply"],
                       capture_output=True, text=True, encoding="utf-8")
    check(p.returncode == 0 and "deleted" in p.stdout, p.stdout[-80:])
    con = sqlite3.connect(db)
    n = con.execute("select count(*) from conversations").fetchone()[0]
    con.close()
    check(n == 0, "已删除")

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
