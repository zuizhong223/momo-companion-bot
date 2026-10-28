# -*- coding: utf-8 -*-
"""把已算好的 276 条向量追加进索引（embedding 已经花过钱了，不再重算）。

faiss 的 C++ 层在 Windows 上读不了/写不了中文路径：
  读 → 先 copy 到 ASCII 临时目录；
  写 → 先写到 ASCII 临时目录，再 shutil.copy2 回中文路径。

用法：
  python ops\\kb\\apply_vectors.py [--apply]
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
KB_ID = "3e25c50e-ade0-4612-9818-6a856dd4ca2f"
DOC_DB = KB / KB_ID / "doc.db"
INDEX = KB / KB_ID / "index.faiss"
OUT = ROOT / "ops/kb"
VEC = OUT / "fix_vectors.npy"
IDS = OUT / "fix_ids.npy"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    import faiss
    import numpy as np

    if not (VEC.exists() and IDS.exists()):
        print(f"缺少向量缓存：{VEC} / {IDS}")
        print("先跑 python ops\\kb\\fix_index.py --apply（它会把向量存下来）")
        return 1

    vecs = np.load(VEC).astype("float32")
    ids = np.load(IDS).astype("int64")
    print(f"缓存向量: {vecs.shape} ids={len(ids)}")

    # 读索引（ASCII 临时路径）
    ascii_dir = Path(tempfile.mkdtemp())
    shutil.copy2(INDEX, ascii_dir / "index.faiss")
    index = faiss.read_index(str(ascii_dir / "index.faiss"))
    print(f"当前索引: ntotal={index.ntotal} d={index.d}")
    if vecs.shape[1] != index.d:
        print(f"FAIL 维度不一致 {vecs.shape[1]} vs {index.d}")
        return 1

    # 只追加真正还缺的
    have = set(faiss.vector_to_array(index.id_map).astype("int64").tolist())
    todo = [(i, v) for i, v in zip(ids, vecs, strict=False) if int(i) not in have]
    print(f"需要追加: {len(todo)} 条")
    if todo:
        index.add_with_ids(
            np.array([v for _, v in todo], dtype="float32"),
            np.array([i for i, _ in todo], dtype="int64"))
    print(f"追加后 ntotal={index.ntotal}")

    # 写到 ASCII 路径再拷回去
    out_ascii = ascii_dir / "index.new.faiss"
    faiss.write_index(index, str(out_ascii))
    print(f"新索引大小 {out_ascii.stat().st_size / 1e6:.1f} MB")

    # 校验
    check = faiss.read_index(str(out_ascii))
    got = set(faiss.vector_to_array(check.id_map).astype("int64").tolist())
    con = sqlite3.connect(f"file:{DOC_DB}?mode=ro", uri=True)
    db_ids = set(r[0] for r in con.execute("select id from documents"))
    con.close()
    missing = db_ids - got
    ok = (check.ntotal == len(db_ids)) and not missing
    print(f"校验: ntotal={check.ntotal} doc.db={len(db_ids)} 仍缺={len(missing)} "
          f"→ {'OK' if ok else 'FAIL'}")
    if not ok:
        return 1
    if not args.apply:
        print("\n未加 --apply，仅校验（索引未替换）")
        return 0

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = INDEX.with_name(f"index.faiss.bak.{ts}")
    shutil.copy2(INDEX, bak)
    shutil.copy2(out_ascii, INDEX)
    print(f"\n已替换 index.faiss（旧索引 -> {bak.name}）")
    print("提醒：重启 AstrBot 才会加载新索引。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
