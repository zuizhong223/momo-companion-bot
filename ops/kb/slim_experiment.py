# -*- coding: utf-8 -*-
"""P2-16：知识库瘦身实验——**先测，不先删**。

计划里写的是"不为凑数字删记忆"，所以这里只做实验：
  1. 找**内容完全相同**的切片（文本 sha1 一样），只留 id 最小的那条；
  2. 用同一批查询，比"全量索引"与"去重索引"的 top-1 是否一致、top-3 重叠多少；
  3. top-1 全一致才建议瘦身，否则不建议。

不改线上索引，只在临时目录里折腾。

用法：python ops\\kb\\slim_experiment.py
"""
import hashlib
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(r"D:/demo")
KB = ROOT / "instances/11111111-1111-4111-8111-111111111111/core/data/knowledge_base"
KB_ID = "3e25c50e-ade0-4612-9818-6a856dd4ca2f"
DOC_DB = KB / KB_ID / "doc.db"
INDEX = KB / KB_ID / "index.faiss"

QUERIES = ["匹诺康尼贴纸拿满要多少个", "用户生日", "机器人练习练到几点",
           "崩铁大黑塔光锥", "机器人喜欢吃什么"]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    import faiss
    import numpy as np

    sys.path.insert(0, str(ROOT / "ops/backup"))
    import kb_baseline

    con = sqlite3.connect(f"file:{DOC_DB}?mode=ro", uri=True)
    rows = con.execute("select id, text from documents order by id").fetchall()
    con.close()
    print(f"切片总数: {len(rows)}")

    seen: dict[str, int] = {}
    dup: list[int] = []
    for i, text in rows:
        h = hashlib.sha1(((text or "").strip().encode())).hexdigest()
        if h in seen:
            dup.append(i)
        else:
            seen[h] = i
    print(f"完全重复的切片: {len(dup)} 条（{len(dup) / len(rows):.1%}）")

    if not dup:
        print("没有完全重复的切片；按当前策略不做任何删除。")
        return 0

    tmp = Path(tempfile.mkdtemp())
    shutil.copy2(INDEX, tmp / "full.faiss")
    shutil.copy2(INDEX, tmp / "slim.faiss")
    full = faiss.read_index(str(tmp / "full.faiss"))
    slim = faiss.read_index(str(tmp / "slim.faiss"))
    slim.remove_ids(np.array(dup, dtype="int64"))
    print(f"全量 ntotal={full.ntotal}　去重后 ntotal={slim.ntotal}")

    qv = np.array(kb_baseline.embed(QUERIES), dtype="float32")
    d_full, i_full = full.search(qv, 3)
    d_slim, i_slim = slim.search(qv, 3)

    con = sqlite3.connect(f"file:{DOC_DB}?mode=ro", uri=True)

    def text_of(idx: int) -> str:
        r = con.execute("select text from documents where id=?", (int(idx),)).fetchone()
        return ((r[0] if r else "") or "")[:28].replace("\n", " ")

    same_top1 = 0
    for q, fi, si, fd, sd in zip(QUERIES, i_full, i_slim, d_full, d_slim, strict=False):
        t1_full, t1_slim = text_of(fi[0]), text_of(si[0])
        overlap = len(set(int(x) for x in fi[:3]) & set(int(x) for x in si[:3])) / 3
        same = t1_full == t1_slim
        same_top1 += same
        print(f"  {q}")
        print(f"    全量 top1: {t1_full!r} (d={fd[0]:.4f})")
        print(f"    去重 top1: {t1_slim!r} (d={sd[0]:.4f})　top3 重叠 {overlap:.0%}")
    con.close()

    print(f"\ntop-1 一致: {same_top1}/{len(QUERIES)}")
    if same_top1 == len(QUERIES):
        print(f"结论：可以瘦身 {len(dup)} 条（{len(dup) / len(rows):.1%}），召回不变。"
              "真实删除前先备份 index.faiss。")
    else:
        print("结论：不要删——top-1 变了，说明这些重复切片在参与召回。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
