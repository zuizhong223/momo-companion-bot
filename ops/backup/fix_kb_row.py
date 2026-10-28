# -*- coding: utf-8 -*-
"""把 knowledge_bases.chunk_count 对齐到 doc.db 的实际切片数。

背景：doc.db / FAISS / sum(kb_documents) 都是 4928，只有 knowledge_bases
的冗余计数还是 4930（旧索引遗留）。本脚本先 VACUUM INTO 备份 kb.db，
再只改这一行；不改文档、不改索引。

用法：
  python ops\\backup\\fix_kb_row.py            # 预览
  python ops\\backup\\fix_kb_row.py --apply    # 备份并更新
"""
from __future__ import annotations

import datetime
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:/demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
KB = CORE / "data/knowledge_base"
KB_ID = "3e25c50e-ade0-4612-9818-6a856dd4ca2f"
DOC_DB = KB / KB_ID / "doc.db"
KB_DB = KB / "kb.db"
OUT = ROOT / "backups"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    apply = "--apply" in sys.argv
    con = sqlite3.connect(f"file:{DOC_DB}?mode=ro", uri=True)
    try:
        chunks = con.execute("select count(*) from documents").fetchone()[0]
    finally:
        con.close()
    con = sqlite3.connect(f"file:{KB_DB}?mode=ro", uri=True)
    try:
        row = con.execute(
            "select chunk_count, doc_count from knowledge_bases where kb_id=?",
            (KB_ID,)).fetchone()
    finally:
        con.close()
    if not row:
        print("FAIL: knowledge_bases 没有该 kb_id")
        return 1
    old_chunks, doc_count = row
    print(f"doc.db documents = {chunks}")
    print(f"knowledge_bases.chunk_count = {old_chunks} -> {chunks}")
    if old_chunks == chunks:
        print("已经一致，无需修改")
        return 0
    if not apply:
        print("预览模式：加 --apply 才会备份并修改")
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = OUT / f"kb_before_chunkrow_{ts}.db"
    con = sqlite3.connect(f"file:{KB_DB}?mode=ro", uri=True)
    try:
        con.execute("VACUUM INTO ?", (str(bak),))
    finally:
        con.close()
    print(f"备份: {bak}")

    con = sqlite3.connect(str(KB_DB), timeout=15)
    try:
        con.execute("PRAGMA busy_timeout=15000")
        cur = con.execute(
            "UPDATE knowledge_bases SET chunk_count=? WHERE kb_id=? AND chunk_count=?",
            (chunks, KB_ID, old_chunks))
        con.commit()
        print(f"updated rows = {cur.rowcount}")
    finally:
        con.close()
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
