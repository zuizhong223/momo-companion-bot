# -*- coding: utf-8 -*-
"""Q1/Q2：按场景拆机器人的括号/短句/长句参照，避免拿总体 21%/15% 硬压。"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(r"D:/demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))
from astrbot.core.utils.style_profile import detect_mode  # noqa: E402


def main() -> int:
    path = ROOT / "ops/style/style_bank.jsonl"
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        u, h = (d.get("user") or "").strip(), (d.get("her") or "").strip()
        if u and h:
            rows.append((u, h, detect_mode(u)))
    by = defaultdict(list)
    for _, h, mode in rows:
        by[mode].append(h)
    out = {}
    for mode, texts in sorted(by.items()):
        n = len(texts)
        out[mode] = {
            "n": n,
            "bracket_pct": round(sum(t.endswith(("（", "(")) for t in texts) / n, 3),
            "short_1_4_pct": round(sum(len(t) <= 4 for t in texts) / n, 3),
            "long_11_pct": round(sum(len(t) >= 11 for t in texts) / n, 3),
            "median": statistics.median(len(t) for t in texts),
        }
    out["_all"] = {
        "n": len(rows),
        "bracket_pct": round(sum(h.endswith(("（", "(")) for _, h, _ in rows) / len(rows), 3),
        "short_1_4_pct": round(sum(len(h) <= 4 for _, h, _ in rows) / len(rows), 3),
        "long_11_pct": round(sum(len(h) >= 11 for _, h, _ in rows) / len(rows), 3),
        "median": statistics.median(len(h) for _, h, _ in rows),
    }
    dest = ROOT / "ops/eval/reports/style_reference_by_mode.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print("->", dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
