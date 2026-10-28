# -*- coding: utf-8 -*-
"""分布指标：把两份基线（过门禁 / raw）按施工单 §5 的口径算出来。

指标：1~4 字占比、11+ 占比、中位数、半括号收尾率、raw 逗号/句号率、口头禅占比与种类。
用法：python ops\\eval\\dist_metrics.py 08_v11_baseline 08_v11_raw
"""
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(r"D:/demo")
REPORTS = ROOT / "ops/eval/reports"
QUIRKS = ["好好好", "哈哈", "嗯嗯", "好耶", "那很好了", "啊？", "诶", "捏", "来着", "摸摸"]


def load(tag: str) -> list[str]:
    d = json.loads((REPORTS / f"{tag}.json").read_text(encoding="utf-8"))
    out = []
    for r in d.get("results", []):
        t = (r.get("reply") or r.get("text") or r.get("output") or "").strip()
        if t:
            out.append(t)
    return out


def metrics(replies: list[str]) -> dict:
    lens = [len(t) for t in replies]
    n = len(lens)
    bracket = sum(1 for t in replies if t.endswith("（") or t.endswith("("))
    punct = sum(1 for t in replies if re.search(r"[，。！？!?,]", t))
    qmark = sum(1 for t in replies if "？" in t or "?" in t)
    cnt = Counter()
    for t in replies:
        for q in QUIRKS:
            if q in t:
                cnt[q] += 1
    return {
        "n": n,
        "pct_1_4": round(sum(1 for x in lens if x <= 4) / n, 3),
        "pct_11_plus": round(sum(1 for x in lens if x >= 11) / n, 3),
        "median": statistics.median(lens),
        "avg": round(sum(lens) / n, 2),
        "bracket_end": round(bracket / n, 3),
        "punct_rate": round(punct / n, 3),
        "qmark_rate": round(qmark / n, 3),
        "quirk_kinds": len(cnt),
        "quirk_top": cnt.most_common(5),
        "haohao": round(cnt.get("好好好", 0) / n, 3),
    }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    tags = sys.argv[1:] or ["08_v11_baseline", "08_v11_raw"]
    for tag in tags:
        try:
            r = load(tag)
        except FileNotFoundError:
            print(f"{tag}: 没有报告")
            continue
        m = metrics(r)
        print(f"\n=== {tag}（{m['n']} 条）===")
        print(f"  1~4 字 {m['pct_1_4']:.1%} | 11+ 字 {m['pct_11_plus']:.1%} | "
              f"中位 {m['median']} | 平均 {m['avg']}")
        print(f"  半括号收尾 {m['bracket_end']:.1%} | 含标点 {m['punct_rate']:.1%} | "
              f"问号 {m['qmark_rate']:.1%}")
        print(f"  口头禅 {m['quirk_kinds']} 种，好好好 {m['haohao']:.1%}；"
              f"top: {m['quirk_top']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
