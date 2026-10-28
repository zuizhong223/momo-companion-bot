# -*- coding: utf-8 -*-
"""L2 backup/verify CLI 回归（临时库，不接机器人）。"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))
os.environ["DEMO_TEST"] = "1"
os.environ["DEMO_LIFE"] = "1"

from astrbot.core.utils import life_state as ls  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="yy_backup_"))
DB = TMP / "life.db"
OUT = TMP / "backups"
os.environ["DEMO_LEDGER_DB"] = str(DB)
PASS = True


def check(cond, label, extra=""):
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + str(extra)) if extra else ''}")


def main():
    global PASS
    sys.stdout.reconfigure(encoding="utf-8")
    con = ls._conn(DB)
    con.execute("insert into yy_life_state(umo,version,tz,energy) values('u',1,'Asia/Shanghai',0.5)")
    con.commit()
    con.close()

    env = dict(os.environ)
    r = subprocess.run(
        [sys.executable, "-X", "utf8", str(ROOT / "ops/life_backup.py"),
         "backup", "--out", str(OUT)],
        capture_output=True, text=True, encoding="utf-8", env=env)
    check(r.returncode == 0, "backup exit 0", r.stderr[-300:])
    files = sorted(OUT.glob("life_*.json"))
    check(len(files) == 1, "backup file", files)
    if files:
        r2 = subprocess.run(
            [sys.executable, "-X", "utf8", str(ROOT / "ops/life_backup.py"),
             "verify", "--file", str(files[0])],
            capture_output=True, text=True, encoding="utf-8", env=env)
        check(r2.returncode == 0 and "PASS" in r2.stdout, "verify PASS", r2.stdout[-300:])

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
