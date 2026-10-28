# -*- coding: utf-8 -*-
"""生活层连续性检查：48 小时每 15 分钟推进，断言没有无状态空档。

只读/临时库，不连 QQ、不写正式库。
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:/demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))
os.environ["DEMO_LIFE_TZ"] = "Asia/Shanghai"

from astrbot.core.utils import life_state as ls  # noqa: E402

UMO = "aiocqhttp:FriendMessage:100000000"
DB = Path(tempfile.mkdtemp(prefix="yy_life72_")) / "life.db"
TZ = ls._tz()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    now = dt.datetime.combine(dt.date(2026, 10, 26), dt.time(12, 0), tzinfo=TZ)
    end = now + dt.timedelta(hours=48)
    titles = Counter()
    none = []
    bad_ctx = 0
    steps = 0
    while now <= end:
        st = ls.advance(UMO, now=now, db_path=DB)
        steps += 1
        t = st.get("title")
        titles[t or "(none)"] += 1
        if not t:
            none.append(now.strftime("%m-%d %H:%M"))
        ctx = ls.life_context(UMO, now=now, db_path=DB)["text"]
        if len(ctx) > 300 or not ctx.startswith("（"):
            bad_ctx += 1
        e = st.get("energy", 0)
        if not (0 <= e <= 1):
            print("bad energy", now, e)
            return 1
        now += dt.timedelta(minutes=15)

    print(f"steps = {steps}")
    print("titles =", dict(titles))
    print("no-state =", none[:10], "count=", len(none))
    print("bad_ctx =", bad_ctx)
    ok = not none and bad_ctx == 0 and titles.get("(none)", 0) == 0
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

