# -*- coding: utf-8 -*-
"""L4：7 天压缩时钟，30 分钟步进；只读临时库。"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:/demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))
os.environ["DEMO_LIFE_TZ"] = "Asia/Shanghai"

from astrbot.core.utils import life_state as ls  # noqa: E402

UMO = "aiocqhttp:FriendMessage:100000000"
DB = Path(tempfile.mkdtemp(prefix="yy_life7d_")) / "life.db"
TZ = ls._tz()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    now = dt.datetime.combine(dt.date(2026, 10, 26), dt.time(0, 0), tzinfo=TZ)
    end = now + dt.timedelta(days=7)
    steps = 0
    no_state = []
    bad_energy = 0
    backwards = 0
    prev_local = None
    while now <= end:
        st = ls.advance(UMO, now=now, db_path=DB)
        steps += 1
        if not st.get("title"):
            no_state.append(now.isoformat())
        if not (0 <= float(st.get("energy", 0)) <= 1):
            bad_energy += 1
        if prev_local and st["local"] < prev_local:
            backwards += 1
        prev_local = st["local"]
        now += dt.timedelta(minutes=30)
    print(f"steps={steps} no_state={len(no_state)} bad_energy={bad_energy} backwards={backwards}")
    ok = steps > 300 and not no_state and bad_energy == 0 and backwards == 0
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

