# -*- coding: utf-8 -*-
"""生活层离线一日模拟：只读打印，不连 QQ、不写正式库。

用法：
  python ops\\verify\\life_sim.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:/demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

DB = Path(tempfile.mkdtemp(prefix="yy_life_sim_")) / "life.db"
os.environ["DEMO_LIFE_TZ"] = "Asia/Shanghai"

import datetime as dt  # noqa: E402

from astrbot.core.utils import life_state as ls  # noqa: E402

UMO = "aiocqhttp:FriendMessage:100000000"
TZ = ls._tz()
DAY = dt.date(2026, 10, 26)  # 周一


def at(h: int, m: int, day_offset: int = 0) -> dt.datetime:
    return dt.datetime.combine(DAY + dt.timedelta(days=day_offset),
                               dt.time(h, m), tzinfo=TZ)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ls.add_commitment(UMO, "周末整理一份示例", db_path=DB)
    sid = ls.add_source("bilibili", url="https://b23.tv/x", bvid="BVx",
                        title="示例视频", read_scope="title", db_path=DB)
    print(f"# 生活层一日模拟（{DAY}，{TZ}）\n")
    for h, m, off in ((7, 30, 0), (10, 45, 0), (14, 30, 0), (19, 45, 0),
                      (23, 10, 0), (1, 20, 1), (4, 0, 1)):
        now = at(h, m, off)
        st = ls.advance(UMO, now=now, db_path=DB)
        ctx = ls.life_context(UMO, now=now, db_path=DB)
        print(f"## {now.strftime('%H:%M')}")
        print(f"- 状态：{st['title']} / {st.get('place') or '-'} / energy={st.get('energy', 0):.2f}")
        print(f"- 上下文：{ctx['text']}")
        print()
    print("## 承诺")
    print(ls.commitments_context(UMO, db_path=DB))
    print("\n## 来源")
    print(ls.get_source(sid, db_path=DB))
    return 0


if __name__ == "__main__":
    sys.exit(main())

