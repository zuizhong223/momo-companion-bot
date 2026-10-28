#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""人工校准：你判"像不像机器人"，和 LLM 判据对一下，看看裁判靠不靠谱。

用法：
  python ops\\eval\\calibrate.py                 # 逐条判（默认取最新一轮报告）
  python ops\\eval\\calibrate.py --report        # 只算吻合度
  python ops\\eval\\calibrate.py --limit 10      # 先判 10 条试试

判定键：y=像机器人 / n=不像 / s=跳过 / q=退出
标签存 ops/eval/human_labels.jsonl（可重复跑，已有标签会跳过）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(r"D:/demo")
REPORTS = ROOT / "ops/eval/reports"
LABELS = ROOT / "ops/eval/human_labels.jsonl"


def latest_report() -> Path | None:
    fs = sorted(REPORTS.glob("judge_*.json"), key=lambda p: p.stat().st_mtime)
    return fs[-1] if fs else None


def load_labels() -> dict[str, dict]:
    out = {}
    if LABELS.exists():
        for ln in LABELS.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            try:
                d = json.loads(ln)
                out[d["id"]] = d
            except Exception:  # noqa: BLE001
                continue
    return out


def rows_of(path: Path) -> list[dict]:
    d = json.loads(path.read_text(encoding="utf-8"))
    return d.get("rows") or d.get("results") or []


def cmd_label(limit: int) -> int:
    rep = latest_report()
    if not rep:
        print("没有可用的判分报告，先跑 runner + judge")
        return 1
    rows = rows_of(rep)
    known = load_labels()
    todo = [r for r in rows if r.get("id") not in known]
    if limit:
        todo = todo[:limit]
    print(f"报告: {rep.name} · 共 {len(rows)} 条 · 待判 {len(todo)} 条（已判 {len(known)}）")
    if not todo:
        print("都判过了，用 --report 看吻合度")
        return 0

    n = 0
    for r in todo:
        j = r.get("judge") or {}
        print("\n" + "-" * 60)
        print(f"[{r.get('id')}] 用户: {r.get('input','')}")
        print(f"       机器人: {(r.get('text') or '').strip()!r}")
        print(f"  判据给分: 相关 {j.get('relevance')} · 像机器人 {j.get('in_character')} "
              f"· 自然 {j.get('naturalness')} · 接住 {j.get('attunement')}")
        if r.get("hard_fails"):
            print(f"  硬伤: {r['hard_fails']}")
        ans = input("像机器人吗？(y/n/s=跳过 q=退出) ").strip().lower()
        if ans == "q":
            break
        if ans not in ("y", "n"):
            continue
        rec = {"id": r.get("id"), "input": r.get("input"), "text": r.get("text"),
               "human": 1 if ans == "y" else 0,
               "judge_in_character": j.get("in_character"),
               "judge_overall": j.get("overall")}
        with LABELS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        n += 1
    print(f"\n本次记录 {n} 条 -> {LABELS.name}")
    return 0


def cmd_report() -> int:
    labels = load_labels()
    if not labels:
        print("还没有人工标签，先跑 python ops/eval/calibrate.py")
        return 1
    agree = 0
    bad = []
    for d in labels.values():
        human = d["human"] == 1
        jic = d.get("judge_in_character")
        if jic is None:
            continue
        judge_ok = jic >= 4
        if human == judge_ok:
            agree += 1
        else:
            bad.append(d)
    total = len(labels)
    print(f"人工标签 {total} 条，判据在'像/不像'上和你一致 {agree} 条"
          f"（{agree * 100 // max(total,1)}%）")
    if bad:
        print("\n判据和你分歧的：")
        for d in bad[:10]:
            who = "你说像、判据说不到 4" if d["human"] else "你说不像、判据给了 4+"
            print(f"  [{d['id']}] {who}（像机器人分 {d['judge_in_character']}）")
            print(f"     用户: {d['input']}")
            print(f"     机器人: {(d['text'] or '').strip()!r}")
    print("\n结论：" + ("判据基本可信，可以用它做 A/B。" if agree >= total * 0.8
                       else "判据和你的口味偏差较大，A/B 结论要谨慎看。"))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    return cmd_report() if args.report else cmd_label(args.limit)


if __name__ == "__main__":
    sys.exit(main())
