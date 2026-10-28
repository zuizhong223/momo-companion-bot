# -*- coding: utf-8 -*-
"""删掉知识库里 2 片测试残留（kb_id="x"、kb_doc_id="d1"、文本「测试内容一/二」）。

为什么要删：元数据（4928）和 doc.db（4930）差的正是这 2 片；它们属于一个不存在的
知识库（kb_id="x"），却还在索引里能被检索到——等于把测试垃圾喂给模型。

步骤：备份 doc.db → 删 documents + FTS → 从 faiss 里 remove_ids → 三边对账。

用法：python ops\\kb\\drop_junk_chunks.py [--apply]
"""
import argparse
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:/demo")
KB = ROOT / "instances/11111111-1111-4111-8111-111111111111/core/data/knowledge_base"
ID = "3e25c50e-ade0-4612-9818-6a856dd4ca2f"
DOC_DB = KB / ID / "doc.db"
INDEX = KB / ID / "index.faiss"
JUNK = (574, 575)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    import faiss
    import numpy as np

    con = sqlite3.connect(f"file:{DOC_DB}?mode=ro", uri=True)
    rows = con.execute(
        "select id, text, metadata from documents where id in (?,?)", JUNK).fetchall()
    total_before = con.execute("select count(*) from documents").fetchone()[0]
    trig = con.execute(
        "select name from sqlite_master where type='trigger' and tbl_name='documents'"
    ).fetchall()
    con.close()
    print(f"doc.db 现有 {total_before} 片；触发器: {[t[0] for t in trig] or '无'}")
    for r in rows:
        print(f"  待删 id={r[0]} text={r[1]!r} meta={r[2]}")

    if not args.apply:
        print("\n未加 --apply，只检查")
        return 0
    if not rows:
        print("要删的已经不在了，退出")
        return 0

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(DOC_DB, DOC_DB.with_name(f"doc.db.bak.{ts}"))
    shutil.copy2(INDEX, INDEX.with_name(f"index.faiss.bak.{ts}"))
    print(f"已备份 doc.db / index.faiss -> *.bak.{ts}")

    # 1) 删切片（FTS 若是 external content，需要一起删）
    con = sqlite3.connect(DOC_DB)
    con.execute("delete from documents where id in (?,?)", JUNK)
    try:
        con.execute("delete from documents_fts where rowid in (?,?)", JUNK)
    except sqlite3.OperationalError as e:
        print(f"  FTS 删除跳过: {e}")
    con.commit()
    after = con.execute("select count(*) from documents").fetchone()[0]
    con.close()
    print(f"doc.db: {total_before} -> {after}")

    # 2) 从 faiss 移除
    ascii_dir = Path(tempfile.mkdtemp())
    shutil.copy2(INDEX, ascii_dir / "i.faiss")
    index = faiss.read_index(str(ascii_dir / "i.faiss"))
    n0 = index.ntotal
    index.remove_ids(np.array(JUNK, dtype="int64"))
    out = ascii_dir / "i.new.faiss"
    faiss.write_index(index, str(out))
    shutil.copy2(out, INDEX)
    print(f"faiss: {n0} -> {index.ntotal}")

    # 3) 对账
    shutil.copy2(INDEX, ascii_dir / "check.faiss")
    chk = faiss.read_index(str(ascii_dir / "check.faiss"))
    ids = set(faiss.vector_to_array(chk.id_map).astype("int64").tolist())
    con = sqlite3.connect(f"file:{DOC_DB}?mode=ro", uri=True)
    db_ids = {r[0] for r in con.execute("select id from documents")}
    kb_sum = sqlite3.connect(f"file:{KB / 'kb.db'}?mode=ro", uri=True).execute(
        "select coalesce(sum(chunk_count),0) from kb_documents").fetchone()[0]
    con.close()
    ok = (chk.ntotal == len(db_ids) == kb_sum) and (ids == db_ids)
    print(f"对账: faiss={chk.ntotal} doc.db={len(db_ids)} kb_documents合计={kb_sum} "
          f"id集一致={ids == db_ids}")
    print("RESULT:", "PASS" if ok else "FAIL")
    print("提醒：重启 AstrBot 才会加载新索引")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
