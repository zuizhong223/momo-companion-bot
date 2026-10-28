# -*- coding: utf-8 -*-
"""P1-B08：生活层预览/体检用的一致性快照与角色时区日界。

不要用 shutil.copy2 复制正在运行的 SQLite 主文件：WAL 里未 checkpoint 的页
会漏，快照可能不一致。这里统一走 SQLite backup API。

也可以从测试里复用：

    from ops.life.snapshot import snapshot, local_day_bounds
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo


def snapshot(src: str | Path, dst: str | Path) -> Path:
    """用 SQLite backup API 复制一份一致快照（会等写事务结束）。"""
    src_p, dst_p = Path(src), Path(dst)
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    if dst_p.exists():
        dst_p.unlink()
    with sqlite3.connect(f"file:{src_p}?mode=ro", uri=True, timeout=30) as s:
        with sqlite3.connect(str(dst_p)) as d:
            s.backup(d)
    return dst_p


def local_day_bounds(day: dt.date, tz: ZoneInfo) -> tuple[str, str]:
    """角色时区某一天对应的 UTC 半开区间 [start, end)。"""
    start = dt.datetime.combine(day, dt.time(0, 0), tzinfo=tz)
    end = start + dt.timedelta(days=1)
    return (
        start.astimezone(dt.timezone.utc).isoformat(timespec="seconds"),
        end.astimezone(dt.timezone.utc).isoformat(timespec="seconds"),
    )
