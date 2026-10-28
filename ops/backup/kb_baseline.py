# -*- coding: utf-8 -*-
"""知识库检索基线（P0-C）：同一组 query → 同一组 (向量序号, 距离)。

为什么要它：faiss 条数和 doc.db 条数一致，不代表索引内容一致。
只有"同样的查询检索到同样的位置"才能证明恢复出来的索引是同一个东西。

用法（被 backup.py / drill 调用）：
    from kb_baseline import compute, compare
    base = compute(faiss_path)          # 5 个固定 query
    compare(base, compute(restored))    # True/False
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import urllib.request
from pathlib import Path

QUERIES = [
    "匹诺康尼贴纸拿满要多少个",
    "示例用户的生日是几月几号",
    "示例日程里练习到几点",
    "崩铁里大黑塔用什么光锥",
    "示例对话里约好考完试做什么",
]
TOP_K = 3
EMB_API = "https://api.minimaxi.com/v1/embeddings"
EMB_MODEL = "embo-01"


def _core() -> Path:
    """实例 core 目录：DEMO_CORE 或从本文件反推（ops/backup → 项目根）。"""
    env = os.environ.get("DEMO_CORE", "").strip()
    if env:
        return Path(env)
    root = Path(os.environ.get("DEMO_ROOT", "").strip()
                or Path(__file__).resolve().parents[2])
    return root / "instances/11111111-1111-4111-8111-111111111111/core"


# 线上配置的位置：容器里没有 F: 盘，用 DEMO_LIVE_CFG 或 <DEMO_CORE>/data 推导
LIVE_CFG = Path(
    os.environ.get("DEMO_LIVE_CFG")
    or (_core() / "data" / "cmd_config.json"))
REDACTED = {"", "REDACTED", "***", "<redacted>"}




def _key_of(cfg_path: Path) -> tuple[str, str]:
    data = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    for p in data.get("provider", []):
        if "embedding" in str(p.get("type", "")):
            api_key = p.get("embedding_api_key") or p.get("api_key") or ""
            base = p.get("embedding_api_base") or p.get("api_base") or ""
            return api_key, base
    return "", ""


def _load_embed_key(config_path: str | Path | None = None) -> tuple[str, str]:
    """读 embedding 的 base/key。默认读正式配置，恢复演练时读归档里的配置。"""
    cfg = (Path(config_path) if config_path
           else _core() / "data" / "cmd_config.json")
    data = json.loads(cfg.read_bytes().decode("utf-8-sig"))
    for p in data.get("provider", []):
        if p.get("type") == "minimax_embedding":
            base = (p.get("embedding_api_base") or EMB_API.rsplit("/v1/", 1)[0]).rstrip("/")
            if not base.endswith("/v1"):
                base += "/v1"
            key = (p.get("embedding_api_key") or "").strip()
            if key in REDACTED and cfg != LIVE_CFG and LIVE_CFG.exists():
                # 备份里的 key 被脱敏了（安全改动）：回退到线上 key + 线上 base。
                # 只回退 key 会留下归档里的本地 base（127.0.0.1），恢复机必然 10061。
                print("  WARN 归档里的 embedding key 是脱敏值，改用线上 key/base 做检索验证")
                _k, _b = _key_of(LIVE_CFG)
                if _k:
                    live = (_b or EMB_API.rsplit("/v1/", 1)[0]).rstrip("/")
                    if not live.endswith("/v1"):
                        live += "/v1"
                    return f"{live}/embeddings", _k
            return f"{base}/embeddings", key
    return EMB_API, ""


def embed(texts: list[str], timeout: int = 30,
          config_path: str | Path | None = None) -> list[list[float]]:
    url, key = _load_embed_key(config_path)
    body = json.dumps({"model": EMB_MODEL, "texts": texts, "type": "query"}).encode()
    import time
    last = None
    d = None
    for attempt in range(4):
        req = urllib.request.Request(
            url, data=body,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode())
            base0 = d.get("base_resp") or {}
            if base0.get("status_code") not in (0, None):
                raise RuntimeError(f"MiniMax {base0.get('status_code')}: "
                                   f"{base0.get('status_msg')}")
            break
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (attempt + 1))
    else:
        raise RuntimeError(f"embedding 重试 4 次仍失败: {last}")
    base = d.get("base_resp") or {}
    if base.get("status_code") not in (0, None):
        raise RuntimeError(
            f"MiniMax embedding {base.get('status_code')}: {base.get('status_msg')}")
    vectors = d.get("vectors") or []
    out = []
    for v in vectors:
        out.append([float(x) for x in (v.get("embedding") if isinstance(v, dict) else v)])
    if len(out) != len(texts):
        raise RuntimeError(f"embedding 返回 {len(out)} 条，期望 {len(texts)}")
    return out


def compute(faiss_path: str | Path, top_k: int = TOP_K,
            config_path: str | Path | None = None) -> dict:
    """返回 {model, top_k, dim, ntotal, results:{query:[[idx, dist],...]}}。"""
    import faiss  # noqa: PLC0415  只有实例 venv 里有
    import numpy as np  # noqa: PLC0415

    src = Path(faiss_path)
    # faiss 的 C++ 层读不了中文路径，先拷到 ASCII 临时目录
    with tempfile.TemporaryDirectory() as tmp:
        ascii_path = Path(tmp) / "index.faiss"
        shutil.copy2(src, ascii_path)
        index = faiss.read_index(str(ascii_path))

    vecs = embed(QUERIES, config_path=config_path)
    arr = np.array(vecs, dtype="float32")
    dist, idx = index.search(arr, top_k)
    texts = _texts_for(Path(faiss_path).parent / "doc.db",
                       [int(i) for row in idx for i in row])
    results = {}
    for q, ids, ds in zip(QUERIES, idx, dist, strict=False):
        results[q] = [[int(i), round(float(d), 4), texts.get(int(i), "")]
                      for i, d in zip(ids, ds, strict=False)]
    return {"model": EMB_MODEL, "top_k": top_k, "dim": int(index.d),
            "ntotal": int(index.ntotal), "results": results}


def _texts_for(doc_db: Path, ids: list[int]) -> dict[int, str]:
    """把命中 id 映射成文本的短哈希——用来验证"向量→文本"映射没变。"""
    if not Path(doc_db).exists() or not ids:
        return {}
    con = sqlite3.connect(f"file:{doc_db}?mode=ro", uri=True)
    out = {}
    try:
        for i in set(ids):
            row = con.execute("select text from documents where id=?", (i,)).fetchone()
            txt = ((row[0] if row else "") or "")[:200]
            out[i] = hashlib.sha1(txt.encode()).hexdigest()[:12]
    finally:
        con.close()
    return out


def fingerprint(base: dict) -> str:
    return hashlib.sha256(
        json.dumps(base.get("results", {}), sort_keys=True, ensure_ascii=False)
        .encode()).hexdigest()


def compare(expected: dict, actual: dict) -> tuple[bool, str]:
    """返回 (是否一致, 说明)。

    注意：**不可比也算不一致**（返回 False）——以前这里返回 True，等于把
    “没验证”当成“通过了”，被复核点过名。
    """
    if expected.get("error") or actual.get("error"):
        return False, ("检索基线不可用，无法验证："
                       + str(expected.get("error") or actual.get("error"))[:80])
    if expected.get("dim") != actual.get("dim"):
        return False, f"维度不同 {expected.get('dim')} vs {actual.get('dim')}"
    if expected.get("ntotal") != actual.get("ntotal"):
        return False, f"向量数不同 {expected.get('ntotal')} vs {actual.get('ntotal')}"
    exp, act = expected.get("results", {}), actual.get("results", {})
    for q in exp:
        if exp[q] == act.get(q):
            continue
        return False, f"检索结果不同：「{q}」{exp[q]} vs {act.get(q)}"
    return True, "检索结果与基线一致（id / 距离 / 文本映射）"


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    p = (Path(sys.argv[1]) if len(sys.argv) > 1
         else _core() / "data/knowledge_base/3e25c50e-ade0-4612-9818-6a856dd4ca2f/index.faiss")
    base = compute(p)
    print(json.dumps(base, ensure_ascii=False, indent=1))
    print("fingerprint:", fingerprint(base)[:16])
