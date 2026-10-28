# -*- coding: utf-8 -*-
"""按 release_gate 的测试清单逐个跑，每个 30s 超时；输出 PASS/FAIL/TIMEOUT。"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:/demo")
TESTS = ROOT / "ops/tests"
sys.path.insert(0, str(ROOT / "ops/verify"))
sys.path.insert(0, str(ROOT))
from release_gate import TESTS as NAMES  # noqa: E402

OUT = ROOT / "ops/verify" / f"test_run_{datetime.now():%Y%m%d_%H%M%S}.log"
env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
lines = []
fails = 0
for name in NAMES:
    p = TESTS / name
    if not p.exists():
        lines.append(f"MISSING {name}")
        fails += 1
        continue
    t0 = time.time()
    try:
        r = subprocess.run([sys.executable, str(p)], cwd=str(ROOT), env=env,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=90)
        out = (r.stdout or "").strip().splitlines()[-2:]
        tail = " | ".join(x.strip() for x in out if x.strip())
        status = "PASS" if r.returncode == 0 else "FAIL"
        if r.returncode != 0:
            fails += 1
            err_lines = (r.stderr or "").strip().splitlines()
            tail = tail[-220:] + (" || " + err_lines[-1][-120:] if err_lines else "")
    except subprocess.TimeoutExpired:
        status = "TIMEOUT"
        fails += 1
        tail = "90s timeout"
    dt = time.time() - t0
    line = f"{status:7} {dt:5.1f}s {name}  {tail}"
    print(line, flush=True)
    lines.append(line)

header = f"tests={len(NAMES)} fails={fails} at {datetime.now():%H:%M:%S}"
OUT.write_text(header + "\n" + "\n".join(lines), encoding="utf-8")
print(header)
print(f"log {OUT}")
