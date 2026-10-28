# -*- coding: utf-8 -*-
"""找出 doc.db 里有切片、但 kb_documents 里没有记录的那个"孤儿文档"。"""
import sqlite3
from pathlib import Path

KB = Path(r"D:/demo\instances\11111111-1111-4111-8111-111111111111/core/data/knowledge_base")
ID = "3e25c50e-ade0-4612-9818-6a856dd4ca2f"

doc = sqlite3.connect(f"file:{KB / ID / 'doc.db'}?mode=ro", uri=True)
kb = sqlite3.connect(f"file:{KB / 'kb.db'}?mode=ro", uri=True)
known = {r[0] for r in kb.execute("select doc_id from kb_documents")}
rows = doc.execute(
    "select json_extract(metadata, '$.kb_doc_id') as k, count(*) "
    "from documents group by k").fetchall()
orphans = [(k, n) for k, n in rows if k not in known]
print("kb_documents 里没有的文档:", orphans)
for k, n in orphans:
    print(f"\n--- kb_doc_id={k}（{n} 片）---")
    for cid, text, meta in doc.execute(
            "select id, text, metadata from documents "
            "where json_extract(metadata, '$.kb_doc_id') = ? order by id", (k,)):
        print(f"  chunk id={cid} len={len(text or '')}")
        print(f"  text={((text or '')[:160])!r}")
        print(f"  meta={meta}")
doc.close()
kb.close()
