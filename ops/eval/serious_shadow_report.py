#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""影子日志回放：把 serious_shadow.jsonl 变成"能不能开 on"的判断依据。

输出：
  - 样本量、正则/模型一致率、模型调用失败率
  - 延迟 p50/p90/max
  - **重点清单**：正则没判 serious、但模型判 serious 的句子（这些才是收益点）
  - 反向清单：正则判了 serious、模型说不是（这些是潜在误判）
  - 可选：给一份人工标签 jsonl，直接算 precision/recall

用法：
  python ops\\eval\\serious_shadow_report.py
  python ops\\eval\\serious_shadow_report.py --labels ops/eval/shadow_labels.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:/demo")
DEFAULT_LOG = ROOT / "instances/11111111-1111-4111-8111-111111111111/core/data/serious_shadow.jsonl"
OUT = ROOT / "ops/eval/reports"


def load(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:  # noqa: BLE001
            continue
    return rows


def pct(n: int, d: int) -> str:
    return f"{n * 100 / d:.0f}%" if d else "-"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(DEFAULT_LOG))
    ap.add_argument("--labels", default="", help="人工标签 jsonl：{text, label: serious|not}")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    rows = load(Path(args.file))
    if not rows:
        print(f"还没有样本：{args.file}")
        print("正常聊天即可，影子探针会在后台记录（不影响回复）。")
        return 0

    n = len(rows)
    failed = sum(1 for r in rows if r.get("llm") is None)
    ok = [r for r in rows if r.get("llm") is not None]
    llm_true = [r for r in ok if r["llm"]]
    regex_serious = [r for r in ok if r.get("regex") == "serious"]
    both = [r for r in ok if r["llm"] and r.get("regex") == "serious"]
    only_llm = [r for r in ok if r["llm"] and r.get("regex") != "serious"]
    only_regex = [r for r in ok if not r["llm"] and r.get("regex") == "serious"]

    ms = [r["ms"] for r in ok if isinstance(r.get("ms"), (int, float))]
    lat = {}
    if ms:
        s = sorted(ms)
        lat = {"p50": s[len(s) // 2], "p90": s[int(len(s) * 0.9)], "max": s[-1]}

    print(f"样本 {n} 条（可用 {len(ok)}，失败 {failed}）")
    print(f"  正则判 serious: {len(regex_serious)}（{pct(len(regex_serious), len(ok))}）")
    print(f"  模型判 serious: {len(llm_true)}（{pct(len(llm_true), len(ok))}）")
    print(f"  两者一致: {len(both) + len([r for r in ok if not r['llm'] and r.get('regex') != 'serious'])}"
          f"  仅模型: {len(only_llm)}  仅正则: {len(only_regex)}")
    if lat:
        print(f"  延迟 p50={lat['p50']}ms p90={lat['p90']}ms max={lat['max']}ms")

    if only_llm:
        print("\n=== 收益点（正则漏了、模型抓到）===")
        for r in only_llm[:20]:
            print(f"  + {r.get('text','')[:40]!r}  ({r.get('ms','?')}ms)")
    if only_regex:
        print("\n=== 潜在误判（正则说是、模型说不是）===")
        for r in only_regex[:20]:
            print(f"  - {r.get('text','')[:40]!r}")

    pr = None
    if args.labels:
        labels = {r["text"]: r["label"] for r in load(Path(args.labels)) if "text" in r}
        tp = fp = fn = 0
        for r in ok:
            lab = labels.get(r.get("text"))
            if lab is None:
                continue
            want = lab == "serious"
            if want and r["llm"]:
                tp += 1
            elif want and not r["llm"]:
                fn += 1
            elif not want and r["llm"]:
                fp += 1
        prec = tp / (tp + fp) if (tp + fp) else 0
        rec = tp / (tp + fn) if (tp + fn) else 0
        pr = {"tp": tp, "fp": fp, "fn": fn, "precision": round(prec, 2), "recall": round(rec, 2)}
        print(f"\n人工标签对照：P={pr['precision']} R={pr['recall']} (TP{tp} FP{fp} FN{fn})")

    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / "serious_shadow_report.json"
    out.write_text(json.dumps({"n": n, "failed": failed, "llm_true": len(llm_true),
                               "regex_serious": len(regex_serious),
                               "only_llm": len(only_llm), "only_regex": len(only_regex),
                               "latency": lat, "judged": pr,
                               "samples": only_llm[:50]},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
