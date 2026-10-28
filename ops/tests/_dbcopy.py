# -*- coding: utf-8 -*-
"""测试用：把线上数据库安全地复制一份快照。

为什么不用 shutil.copy2：
  data_v4.db 是 WAL 模式，正在写入时最后一些页还在 `-wal` 文件里。
  只 copy 主文件可能拿到**不一致**的快照（偶发 "database is locked" /
  "no such table" / 计数对不上）。这正是 8 套测试偶尔挂一个的原因。

用法：
    from _dbcopy import snapshot
    snapshot(在线库路径, 目标路径)
"""
import sqlite3
from pathlib import Path


def snapshot(src: str | Path, dst: str | Path) -> Path:
    """用 SQLite 的 backup API 拷一份一致快照（会等写事务结束）。"""
    src_p, dst_p = Path(src), Path(dst)
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    if dst_p.exists():
        dst_p.unlink()
    with sqlite3.connect(f"file:{src_p}?mode=ro", uri=True, timeout=30) as s:
        with sqlite3.connect(str(dst_p)) as d:
            s.backup(d)
    return dst_p


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    print("快照:", snapshot(sys.argv[1], sys.argv[2]))
