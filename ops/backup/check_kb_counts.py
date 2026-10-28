# -*- coding: utf-8 -*-
"""四数对账（遍历所有 KB）：
    faiss.ntotal == doc.db.documents == sum(kb_documents.chunk_count)
    == knowledge_bases.chunk_count

只读检查，不修改；用于备份/迁移前置。退出码：
  0 = 所有 KB 四数一致
  1 = 至少一个 KB 不一致（打印差异）
  2 = 无法读取（缺文件/faiss 读不了）
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:/demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
KB = CORE / "data/knowledge_base"
KB_DB = KB / "kb.db"


def read_ntotal(faiss_path: Path) -> int:
    import faiss  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d) / "index.faiss"
        shutil.copy2(faiss_path, tmp)
        return int(faiss.read_index(str(tmp)).ntotal)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if not KB_DB.exists():
        print("INCOMPLETE: 缺 kb.db")
        return 2

    con = sqlite3.connect(f"file:{KB_DB}?mode=ro", uri=True)
    try:
        kbs = con.execute(
            "select kb_id, kb_name, chunk_count, doc_count from knowledge_bases "
            "order by id").fetchall()
    finally:
        con.close()

    if not kbs:
        print("INCOMPLETE: knowledge_bases 为空")
        return 2

    incomplete = False
    failures = 0
    for kb_id, kb_name, row_chunks, row_docs in kbs:
        doc_db = KB / kb_id / "doc.db"
        faiss_path = KB / kb_id / "index.faiss"
        if not (doc_db.exists() and faiss_path.exists()):
            print(f"INCOMPLETE: {kb_name} ({kb_id[:8]}) 缺 doc.db / index.faiss")
            incomplete = True
            continue

        try:
            con = sqlite3.connect(f"file:{doc_db}?mode=ro", uri=True)
            chunks = con.execute("select count(*) from documents").fetchone()[0]
            con.close()

            con = sqlite3.connect(f"file:{KB_DB}?mode=ro", uri=True)
            sum_chunks = con.execute(
                "select coalesce(sum(chunk_count),0) from kb_documents where kb_id=?",
                (kb_id,)).fetchone()[0]
            n_docs = con.execute(
                "select count(*) from kb_documents where kb_id=?",
                (kb_id,)).fetchone()[0]
            con.close()

            ntotal = read_ntotal(faiss_path)
        except Exception as e:  # noqa: BLE001
            print(f"INCOMPLETE: {kb_name} ({kb_id[:8]}) 读取失败: {e}")
            incomplete = True
            continue

        ok = ntotal == chunks == sum_chunks == int(row_chunks or 0)
        print(f"--- {kb_name} ({kb_id[:8]}) ---")
        print(f"faiss_ntotal              = {ntotal}")
        print(f"doc.db.documents          = {chunks}")
        print(f"sum(kb_documents.chunk)   = {sum_chunks}")
        print(f"knowledge_bases.chunk     = {row_chunks}")
        print(f"knowledge_bases.doc_count = {row_docs} (kb_documents={n_docs})")
        if ok:
            print("RESULT: PASS")
        else:
            print("RESULT: FAIL")
            failures += 1

    if incomplete:
        return 2
    if failures:
        print(f"RESULT: FAIL（{failures} 个 KB 不一致）")
        return 1
    print(f"RESULT: PASS（{len(kbs)} 个 KB 四数一致）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
