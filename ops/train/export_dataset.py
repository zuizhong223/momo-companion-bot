# -*- coding: utf-8 -*-
"""P3-21：训练数据导出（可控上下文长度）。

从真实会话里切窗口：每条 assistant 回复 + 它前面最多 N 条上下文，导出成 JSONL。
默认 N=16（比现在喂模型的 125 条短很多——训练要的是"这一段对话怎么接"，不是全部历史）。

用法：
  python ops\\train\\export_dataset.py                     # 看统计
  python ops\\train\\export_dataset.py --window 16 --out ops/train/out.jsonl
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(r"D:/demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
DB = CORE / "data/data_v4.db"
UMO = "aiocqhttp:FriendMessage:100000000"
OUT = ROOT / "ops/train/dataset.jsonl"


def _text(m: dict) -> str:
    import re
    t = " ".join(p.get("text", "") for p in (m.get("content") or [])
                  if isinstance(p, dict)).strip()
    return re.sub(r"<system_reminder>.*?</system_reminder>", "", t, flags=re.S).strip()


def load_history() -> list[dict]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    row = con.execute("select content from conversations where user_id=?",
                      (UMO,)).fetchone()
    con.close()
    hist = json.loads(row[0] or "[]") if row else []
    # 过滤排除项 + 空消息
    out = []
    for m in hist:
        if m.get("excluded"):
            continue
        t = _text(m)
        if not t:
            continue
        out.append({"role": m.get("role", "user"), "content": t})
    return out


def windows(hist: list[dict], size: int) -> list[dict]:
    out = []
    for i, m in enumerate(hist):
        if m["role"] != "assistant":
            continue
        ctx = hist[max(0, i - size):i]
        if not ctx:
            continue
        out.append({
            "messages": ctx + [{"role": "assistant", "content": m["content"]}],
            "meta": {"index": i, "ctx_len": len(ctx),
                     "reply_len": len(m["content"])},
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=16)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    hist = load_history()
    ws = windows(hist, args.window)
    if not ws:
        print("没有可导出的窗口")
        return 1
    lens = [sum(len(x["content"]) for x in w["messages"]) for w in ws]
    print(f"消息 {len(hist)} 条 → 窗口 {len(ws)} 个（每条回复一个）")
    print(f"窗口总字数: 平均 {sum(lens) // len(lens)}，"
          f"最短 {min(lens)}，最长 {max(lens)}")
    print(f"回复平均长度: {sum(w['meta']['reply_len'] for w in ws) // len(ws)} 字")

    if not args.apply:
        print(f"\n未加 --apply；将写入 {args.out}")
        return 0

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for w in ws:
            f.write(json.dumps(w, ensure_ascii=False) + "\n")
    print(f"\n已写 {len(ws)} 行 -> {out}（{out.stat().st_size / 1024:.1f} KB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

