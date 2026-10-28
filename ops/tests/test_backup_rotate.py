# -*- coding: utf-8 -*-
"""备份轮转计划测试：只测 plan，不删文件。"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
sys.path.insert(0, str(ROOT / "ops/backup"))
import rotate as r  # noqa: E402

ok = True


def check(cond, label, extra=""):
    global ok
    ok = ok and bool(cond)
    print(("OK   " if cond else "FAIL ") + label + (f" | {extra}" if extra else ""))


TMP = Path(tempfile.mkdtemp(prefix="yy_rot_"))
now = 1_800_000_000
files = []
for i in range(20):
    p = TMP / f"demo_backup_{i:02d}.zip"
    p.write_bytes(b"x")
    os.utime(p, (now - i * 86400, now - i * 86400))
    files.append(p)

keep, delete = r.plan(files, now=now)
check(len(keep) >= 1 and files[0] in keep, "最新必留", str(len(keep)))
check(len(keep) + len(delete) == len(files), "不重不漏", f"{len(keep)}+{len(delete)}")
check(len(delete) > 0, "有可删", str(len(delete)))

keep2, delete2 = r.plan([], now=now)
check(keep2 == [] and delete2 == [], "空目录", "")

print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
