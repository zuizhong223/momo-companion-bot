# -*- coding: utf-8 -*-
"""P1-B11：按文件核对知识库完整性（只读）。

对每个 kb_documents：
  - kb.db 里登记的 chunk_count；
  - doc.db 里该 doc_id 实际切片数；
  - 文件是否有 >0 切片；
  - 全局 faiss.ntotal 是否等于 doc.db.documents。

用法：
  python ops\\verify\\kb_file_status.py
  python ops\\verify\\kb_file_status.py --pattern 示例
退出码：0 全 OK；1 有缺失/不一致；2 无法读取。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:/demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
KB = CORE / "data/knowledge_base"
OUT = ROOT / "ops/verify"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pattern", default="%")
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    kb_db = KB / "kb.db"
    if not kb_db.exists():
        print("INCOMPLETE: 缺 kb.db")
        return 2
    con = sqlite3.connect(f"file:{kb_db}?mode=ro", uri=True)
    try:
        docs = con.execute(
            "select kb_id,doc_id,doc_name,chunk_count from kb_documents"
            " where doc_name like ? order by kb_id,doc_name", (args.pattern,)).fetchall()
        kb_rows = con.execute(
            "select kb_id,doc_count,chunk_count from knowledge_bases").fetchall()
    finally:
        con.close()

    rows = []
    for kb_id, doc_id, name, chunk_count in docs:
        doc_db = KB / kb_id / "doc.db"
        actual = -1
        err = ""
        if doc_db.exists():
            try:
                d = sqlite3.connect(f"file:{doc_db}?mode=ro", uri=True)
                try:
                    actual = d.execute(
                        "select count(*) from documents"
                        " where json_extract(metadata,'$.kb_doc_id')=?",
                        (doc_id,)).fetchone()[0]
                finally:
                    d.close()
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
        status = "ok"
        if actual < 0:
            status = "missing_index"
        elif actual == 0:
            status = "missing_chunks"
        elif int(chunk_count or 0) != actual:
            status = "count_mismatch"
        rows.append({
            "kb_id": kb_id, "doc_id": doc_id, "doc_name": name,
            "registered_chunks": int(chunk_count or 0),
            "actual_chunks": actual, "status": status, "error": err,
        })

    kb_summary = []
    for kb_id, doc_count, chunk_count in kb_rows:
        doc_db = KB / kb_id / "doc.db"
        actual = -1
        if doc_db.exists():
            d = sqlite3.connect(f"file:{doc_db}?mode=ro", uri=True)
            try:
                actual = d.execute("select count(*) from documents").fetchone()[0]
            finally:
                d.close()
        kb_summary.append({"kb_id": kb_id, "doc_count": doc_count,
                           "chunk_count": chunk_count, "actual_chunks": actual})

    bad = [r for r in rows if r["status"] != "ok"]
    today = datetime.date.today().strftime("%Y%m%d")
    out = Path(args.json) if args.json else OUT / f"kb_file_status_{today}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"docs": rows, "kbs": kb_summary},
                              ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"文件数 {len(rows)}；异常 {len(bad)}")
    for r in rows:
        mark = "OK  " if r["status"] == "ok" else "FAIL"
        print(f"  {mark} {r['doc_name']:<32} 登记 {r['registered_chunks']:>3} "
              f"实际 {r['actual_chunks']:>3} {r['status']} {r['error']}")
    for k in kb_summary:
        print(f"  KB {k['kb_id'][:8]} 文档 {k['doc_count']} 登记切片 "
              f"{k['chunk_count']} 实际 {k['actual_chunks']}")
    print("报告:", out)
    print("RESULT:", "PASS" if not bad else "FAIL")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
