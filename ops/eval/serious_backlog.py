#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""离线回放：拿示例对话里的用户消息过一遍认真话题分类，不必等线上样本。"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:/demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
DB = CORE / "data/data_v4.db"
OUT = ROOT / "ops/eval/reports"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(CORE))


def user_messages(min_len: int) -> list[str]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = con.execute("select content from conversations").fetchall()
    finally:
        con.close()
    seen, out = set(), []
    for (content,) in rows:
        for m in json.loads(content or "[]"):
            if m.get("role") != "user":
                continue
            txt = m.get("content")
            if isinstance(txt, list):
                txt = "".join(p.get("text", "") for p in txt if isinstance(p, dict))
            txt = (txt or "").strip()
            if len(txt) < min_len or txt in seen:
                continue
            seen.add(txt)
            out.append(txt)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-len", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    from astrbot.core.utils import serious_probe as sp
    from astrbot.core.utils.style_profile import detect_mode

    msgs = user_messages(args.min_len)
    if args.limit:
        msgs = msgs[: args.limit]
    print(f"真实用户消息 {len(msgs)} 条（≥{args.min_len} 字）")
    if not msgs:
        return 0

    rows = []
    for t in msgs:
        t0 = time.time()
        pred = sp.classify(t, timeout=6)
        rows.append({"text": t, "regex": detect_mode(t), "llm": pred,
                     "ms": int((time.time() - t0) * 1000)})

    ok = [r for r in rows if r["llm"] is not None]
    failed = len(rows) - len(ok)
    regex_hit = [r for r in ok if r["regex"] == "serious"]
    llm_hit = [r for r in ok if r["llm"]]
    only_llm = [r for r in ok if r["llm"] and r["regex"] != "serious"]
    only_regex = [r for r in ok if not r["llm"] and r["regex"] == "serious"]
    ms = sorted(r["ms"] for r in ok)

    print(f"  可用 {len(ok)}，失败 {failed}")
    print(f"  正则判 serious {len(regex_hit)}（{len(regex_hit)*100//max(len(ok),1)}%）")
    print(f"  模型判 serious {len(llm_hit)}（{len(llm_hit)*100//max(len(ok),1)}%）")
    print(f"  仅模型抓到 {len(only_llm)}；正则说是模型说不是 {len(only_regex)}")
    if ms:
        print(f"  延迟 p50={ms[len(ms)//2]}ms max={ms[-1]}ms")

    if only_llm:
        print("\n=== 正则漏掉、模型认为要认真（最该看的一批）===")
        for r in only_llm[:20]:
            print(f"  + {r['text'][:44]}")
    if only_regex:
        print("\n=== 正则说认真、模型说不用（潜在误判）===")
        for r in only_regex[:20]:
            print(f"  - {r['text'][:44]}")

    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / "serious_backlog.json"
    out.write_text(json.dumps({"n": len(rows), "failed": failed,
                               "regex_serious": len(regex_hit), "llm_serious": len(llm_hit),
                               "only_llm": only_llm, "only_regex": only_regex,
                               "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
