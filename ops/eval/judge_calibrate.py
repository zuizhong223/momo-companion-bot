# -*- coding: utf-8 -*-
"""B04：judge 人工校准脚手架（不调用任何模型）。

步骤：
  1) python ops\\eval\\judge_calibrate.py --build
     -> reports/judge_calibration_20.csv（20 条，模型回复已填，人工三列空白）
  2) 人把 human_relevance / human_in_character / human_naturalness 填 1~5
  3) python ops\\eval\\judge_calibrate.py --compare
     -> 与 judge 分数的一致率（精确/±1）

注意：本脚本只读写 CSV，不调用 judge.py 的付费接口。
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPORTS = HERE / "reports"
DEFAULT_REPORT = REPORTS / "09_c.json"
DEFAULT_OUT = REPORTS / "judge_calibration_20.csv"
COLS = ["id", "cat", "input", "model_reply",
        "human_relevance", "human_in_character", "human_naturalness", "human_note",
        "judge_relevance", "judge_in_character", "judge_naturalness"]


def build(report: Path, out: Path, n: int, seed: int) -> int:
    d = json.loads(report.read_text(encoding="utf-8"))
    rows = d.get("results") or d.get("rows") or []
    if not rows:
        print("报告没有 results/rows")
        return 1
    by_cat: dict[str, list] = {}
    for r in rows:
        by_cat.setdefault(r.get("cat") or r.get("kind") or "", []).append(r)
    picked = []
    cats = sorted(by_cat)
    while len(picked) < n and any(by_cat.values()):
        for c in cats:
            if by_cat[c] and len(picked) < n:
                picked.append(by_cat[c].pop(0))
    random.Random(seed).shuffle(picked)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in picked[:n]:
            w.writerow({
                "id": r.get("id", ""),
                "cat": r.get("cat", ""),
                "input": r.get("input", ""),
                "model_reply": r.get("text") or r.get("final") or r.get("raw") or "",
                "human_relevance": "",
                "human_in_character": "",
                "human_naturalness": "",
                "human_note": "",
                "judge_relevance": r.get("relevance", ""),
                "judge_in_character": r.get("in_character", ""),
                "judge_naturalness": r.get("naturalness", ""),
            })
    print(f"写出 {len(picked[:n])} 条 -> {out}")
    return 0


def render_md(csv_path: Path, out_path: Path) -> int:
    if not csv_path.exists():
        print(f"没有 {csv_path}，先 --build")
        return 1
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8-sig")))
    lines = [
        "# judge 人工校准（20 条）",
        "",
        "> 先只看 `model_reply`，按“像不像机器人 / 相不相关 / 自不自然”给 1–5 分；",
        "> **不要先看 judge 列**，填完再跑 `--compare`。",
        "",
        "| id | cat | 输入 | 模型回复 | 相关 | 像机器人 | 自然 | 备注 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        def clean(s: str) -> str:
            return (s or "").replace("|", "\\|").replace("\n", " ")[:120]
        lines.append(
            f"| {clean(r['id'])} | {clean(r['cat'])} | {clean(r['input'])} | "
            f"{clean(r['model_reply'])} |  |  |  |  |")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"写出 {len(rows)} 条 -> {out_path}")
    return 0


def compare(csv_path: Path) -> int:
    if not csv_path.exists():
        print(f"没有 {csv_path}，先 --build")
        return 1
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8-sig")))
    axes = ("relevance", "in_character", "naturalness")
    filled = 0
    exact = {a: 0 for a in axes}
    near = {a: 0 for a in axes}
    total = {a: 0 for a in axes}
    for r in rows:
        for a in axes:
            h = (r.get(f"human_{a}") or "").strip()
            j = (r.get(f"judge_{a}") or "").strip()
            if not h or not j:
                continue
            try:
                hv, jv = int(h), int(j)
            except ValueError:
                continue
            total[a] += 1
            exact[a] += hv == jv
            near[a] += abs(hv - jv) <= 1
        if any((r.get(f"human_{a}") or "").strip() for a in axes):
            filled += 1
    print(f"已填 {filled}/{len(rows)} 条")
    for a in axes:
        t = total[a]
        if not t:
            print(f"  {a}: 没有可比较的数据")
            continue
        print(f"  {a}: 精确 {exact[a]}/{t} ({exact[a]/t:.0%})；±1 {near[a]}/{t} ({near[a]/t:.0%})")
    return 0 if filled else 1


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--md", action="store_true")
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--report", default=str(DEFAULT_REPORT))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--csv", default=str(DEFAULT_OUT))
    args = ap.parse_args()
    if args.build:
        rc = build(Path(args.report), Path(args.out), args.n, args.seed)
        if rc:
            return rc
    if args.md:
        return render_md(Path(args.csv), Path(args.out).with_suffix(".md"))
    if args.compare:
        return compare(Path(args.csv))
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
