# -*- coding: utf-8 -*-
"""修 kb_documents.chunk_count 与 knowledge_bases 的计数（用 metadata.kb_doc_id 关联）。

`documents.doc_id` 是**每个切片自己的 uuid**，不是文档 id；
真正能关联的是 `documents.metadata` 里的 `kb_doc_id`（对应 `kb_documents.doc_id`）。

用法：python ops\\kb\\fix_counts.py [--apply]
"""
import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:/demo")
KB = ROOT / "instances/11111111-1111-4111-8111-111111111111/core/data/knowledge_base"
KB_ID = "3e25c50e-ade0-4612-9818-6a856dd4ca2f"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    doc = sqlite3.connect(f"file:{KB / KB_ID / 'doc.db'}?mode=ro", uri=True)
    rows = doc.execute(
        "select json_extract(metadata, '$.kb_doc_id') as k, count(*) "
        "from documents group by k").fetchall()
    total = doc.execute("select count(*) from documents").fetchone()[0]
    doc.close()
    per_doc = [(k, n) for k, n in rows if k]
    orphan = total - sum(n for _, n in per_doc)
    print(f"doc.db 切片 {total}；按 kb_doc_id 归到 {len(per_doc)} 个文档；"
          f"无 kb_doc_id 的切片 {orphan}")

    kb = sqlite3.connect(KB / "kb.db")
    before = kb.execute("select coalesce(sum(chunk_count),0), count(*) from kb_documents").fetchone()
    krow = kb.execute("select chunk_count, doc_count from knowledge_bases where kb_id=?",
                      (KB_ID,)).fetchone()
    print(f"现状: kb_documents 合计={before[0]} 文档数={before[1]}；"
          f"knowledge_bases={krow}")

    if not args.apply:
        print("\n未加 --apply，只报告")
        return 0

    bak = KB / f"kb.db.before_countfix.{datetime.now():%Y%m%d_%H%M%S}"
    shutil.copy2(KB / "kb.db", bak)
    # 注意参数顺序：set chunk_count=?  where doc_id=?  ->  (数量, doc_id)
    kb.executemany("update kb_documents set chunk_count=? where doc_id=?",
                   [(n, k) for k, n in per_doc])
    n_docs = kb.execute("select count(*) from kb_documents").fetchone()[0]
    kb.execute("update knowledge_bases set chunk_count=?, doc_count=? where kb_id=?",
               (total, n_docs, KB_ID))
    kb.commit()
    after = kb.execute("select coalesce(sum(chunk_count),0), count(*) from kb_documents").fetchone()
    krow2 = kb.execute("select chunk_count, doc_count from knowledge_bases where kb_id=?",
                       (KB_ID,)).fetchone()
    kb.close()
    print(f"修正后: kb_documents 合计={after[0]} 文档数={after[1]}；"
          f"knowledge_bases={krow2}")
    print(f"备份: {bak.name}")
    ok = after[0] == total and krow2[0] == total
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
