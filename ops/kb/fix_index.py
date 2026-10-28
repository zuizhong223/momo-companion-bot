# -*- coding: utf-8 -*-
"""修知识库索引：把 doc.db 里有、faiss 里没有的切片补进去。

诊断结论（2026-10-25 07:50）：
  faiss 是 `IndexIDMap`（1536 维，ntotal=4654），id 就是 `doc.db.documents.id`；
  缺的 276 条集中在 7 段连续区间（1-29 / 114-150 / 178-207 / 274-316 / 373-402 /
  433-470 / 507-575），看着像 7 个文档当时没嵌进去。

做法（最小改动，不重建全量）：
  1. 读 doc.db 里缺的文本；
  2. 用**和线上同一个模型/同一个接口**（MiniMax embo-01，type=query，因为适配器对文档也用的 query）
     分批嵌入；
  3. `index.add_with_ids(vecs, ids)` 追加到现有索引；
  4. 写到 `index.faiss.new`，校验 ntotal == doc.db 行数、id 全覆盖、抽样检索；
  5. `--apply` 时先备份 `index.faiss.bak.<ts>`，再原子替换。

用法：
  python ops\\kb\\fix_index.py --dry-run
  python ops\\kb\\fix_index.py --apply
"""
import argparse
import json
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

BATCH = 16
PROBE_QUERIES = ["匹诺康尼贴纸拿满要多少个", "用户生日", "机器人练习练到几点"]


def load_faiss():
    import faiss  # noqa: PLC0415

    tmp = Path(tempfile.mkdtemp()) / "i.faiss"
    shutil.copy2(INDEX, tmp)          # faiss 读不了中文路径
    return faiss.read_index(str(tmp))


def missing_ids(index) -> list[int]:
    import faiss  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    ids = faiss.vector_to_array(index.id_map).astype("int64")
    con = sqlite3.connect(f"file:{DOC_DB}?mode=ro", uri=True)
    db_ids = np.array([r[0] for r in con.execute("select id from documents order by id")],
                      dtype="int64")
    con.close()
    return sorted(np.setdiff1d(db_ids, ids).tolist())


def texts_for(ids: list[int]) -> list[str]:
    con = sqlite3.connect(f"file:{DOC_DB}?mode=ro", uri=True)
    out = []
    for i in ids:
        row = con.execute("select text from documents where id=?", (i,)).fetchone()
        out.append((row[0] if row else "") or "")
    con.close()
    return out


def embed(texts: list[str]) -> list[list[float]]:
    sys.path.insert(0, str(ROOT / "ops/backup"))
    import kb_baseline  # noqa: PLC0415

    import time
    vecs: list[list[float]] = []
    for i in range(0, len(texts), BATCH):
        chunk = texts[i:i + BATCH]
        last = None
        for attempt in range(6):
            try:
                got = kb_baseline.embed(chunk)
                if len(got) != len(chunk):
                    raise RuntimeError(f"返回 {len(got)} 条，期望 {len(chunk)}")
                vecs.extend(got)
                break
            except Exception as e:  # noqa: BLE001
                last = e
                wait = min(30, 2 ** attempt)
                print(f"    批次 {i} 失败({type(e).__name__}: {str(e)[:60]})，{wait}s 后重试",
                      flush=True)
                time.sleep(wait)
        else:
            raise RuntimeError(f"批次 {i} 重试 6 次仍失败: {last}")
        print(f"    嵌入 {min(i + BATCH, len(texts))}/{len(texts)}", flush=True)
        time.sleep(0.6)          # 主动限速
    return vecs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    import faiss  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    index = load_faiss()
    print(f"现有索引: {type(index).__name__} ntotal={index.ntotal} d={index.d}")
    miss = missing_ids(index)
    print(f"缺失 id: {len(miss)} 条", miss[:10], "…" if len(miss) > 10 else "")
    if not miss:
        print("不需要修")
        return 0
    if args.dry_run or not args.apply:
        print(f"\n[预览] 需要嵌入 {len(miss)} 条文本，追加后 ntotal 应为 "
              f"{index.ntotal + len(miss)}")
        print("加 --apply 执行")
        return 0

    texts = texts_for(miss)
    empty = [i for i, t in zip(miss, texts, strict=False) if not t.strip()]
    print(f"其中空文本 {len(empty)} 条（仍然嵌入，用占位）")
    texts = [t if t.strip() else "（空）" for t in texts]

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "missing_ids.json").write_text(
        json.dumps({"missing": miss, "count": len(miss),
                    "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                   ensure_ascii=False, indent=1), encoding="utf-8")

    print("开始嵌入（MiniMax embo-01）…")
    vecs = embed(texts)
    arr = np.array(vecs, dtype="float32")
    if arr.shape[1] != index.d:
        print(f"FAIL 维度不一致: {arr.shape[1]} vs {index.d}")
        return 1

    print("追加到索引副本 …")
    # 直接在内存索引上追加（index 已是独立拷贝）
    index.add_with_ids(arr, np.array(miss, dtype="int64"))
    print(f"追加后 ntotal={index.ntotal}")

    # 先把向量存下来，免得又得重新嵌入（已经因为中文路径白跑过一次）
    OUT.mkdir(parents=True, exist_ok=True)
    cache = ROOT / "backups/kb"
    cache.mkdir(parents=True, exist_ok=True)
    np.save(cache / "fix_vectors.npy", arr)
    np.save(cache / "fix_ids.npy", np.array(miss, dtype="int64"))
    print(f"向量已缓存: {cache / 'fix_vectors.npy'}（不进补丁包/备份）")

    # faiss 写不了中文路径：先写 ASCII 临时目录，再 copy 回来
    tmp_dir = Path(tempfile.mkdtemp())
    tmp_out = tmp_dir / "index.faiss.new"
    faiss.write_index(index, str(tmp_out))
    # faiss 写中文路径也炸，写完再拷回目标目录
    print(f"新索引已写到临时目录（{tmp_out.stat().st_size / 1e6:.1f} MB）")

    # 校验：重新读 + id 覆盖 + 抽样检索
    check = faiss.read_index(str(tmp_out))
    ids = faiss.vector_to_array(check.id_map).astype("int64")
    con = sqlite3.connect(f"file:{DOC_DB}?mode=ro", uri=True)
    db_ids = set(r[0] for r in con.execute("select id from documents"))
    con.close()
    missing_after = sorted(db_ids - set(int(i) for i in ids))
    ok = (check.ntotal == len(db_ids) and not missing_after)
    print(f"校验 ntotal={check.ntotal} doc.db={len(db_ids)} 仍缺={len(missing_after)} "
          f"→ {'OK' if ok else 'FAIL'}")
    if not ok:
        return 1

    sys.path.insert(0, str(ROOT / "ops/backup"))
    import kb_baseline  # noqa: PLC0415

    qv = np.array(kb_baseline.embed(PROBE_QUERIES), dtype="float32")
    dist, hit = check.search(qv, 1)
    con = sqlite3.connect(f"file:{DOC_DB}?mode=ro", uri=True)
    print("抽样检索：")
    for q, idx, d in zip(PROBE_QUERIES, hit[:, 0], dist[:, 0], strict=False):
        row = con.execute("select text from documents where id=?", (int(idx),)).fetchone()
        snippet = ((row[0] if row else "") or "")[:40].replace("\n", " ")
        print(f"   {q} → id={int(idx)} dis={float(d):.4f} {snippet!r}")
    con.close()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = INDEX.with_suffix(f".faiss.bak.{ts}")
    shutil.copy2(INDEX, bak)
    shutil.copy2(tmp_out, INDEX)      # 覆盖（中文路径用 copy2 而不是 faiss.write_index）
    tmp_out.unlink()
    print(f"\n已替换：{INDEX.name}（旧索引备份 {bak.name}）")
    print("提醒：需要重启 AstrBot 才会加载新索引；重启后跑 ops\\backup\\backup.py check 复核。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
