# -*- coding: utf-8 -*-
"""线上按模式分布 + 每轮气泡数（按 part 切气泡，只读）。"""
from __future__ import annotations

import json
import re
import sqlite3
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(r"D:/demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
DB = CORE / "data/data_v4.db"
sys.path.insert(0, str(CORE))
from astrbot.core.utils.style_profile import TARGETS, detect_mode  # noqa: E402

OUT = Path(r"D:/demo\Documents\deepseek-harness\default-workspace\live_mode_audit.txt")


def bubbles_of(msg) -> list[str]:
    out = []
    for p in msg.get("content") or []:
        if not isinstance(p, dict):
            continue
        for s in str(p.get("text") or "").split("\n"):
            s = re.sub(r"<system_reminder>.*?</system_reminder>", "", s, flags=re.S).strip()
            if s:
                out.append(s)
    return out


def main() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    row = con.execute("select content from conversations where user_id=?",
                      ("aiocqhttp:FriendMessage:100000000",)).fetchone()
    msgs = json.loads(row[0]) if row else []
    pairs = []
    last_user = None
    for m in msgs[-160:]:
        if m.get("role") == "user":
            b = bubbles_of(m)
            last_user = " ".join(b) if b else None
        elif m.get("role") == "assistant" and last_user:
            bs = bubbles_of(m)
            if bs:
                pairs.append((last_user, bs))
            last_user = None

    pairs = pairs[-40:]
    groups: dict[str, list[str]] = {}
    for u, bs in pairs:
        groups.setdefault(detect_mode(u), []).extend(bs)

    def stat(items):
        lens = [len(re.sub(r"\s+", "", x)) for x in items]
        n = len(items)
        return (n,
                round(sum(1 for x in lens if x <= 4) / n, 3),
                round(sum(1 for x in lens if x >= 11) / n, 3),
                statistics.median(lens),
                round(sum(1 for x in items if re.search(r"[（(）)]$", x)) / n, 3),
                round(sum(1 for x in items if "好好好" in x) / n, 3),
                round(sum(1 for x in items if "哈哈" in x) / n, 3))

    lines = [f"turns={len(pairs)}"]
    for mode, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        n, sh, lo, med, br, hh, ha = stat(items)
        t = TARGETS.get(mode, {})
        lines.append(
            f"{mode}: n={n} short={sh} ({t.get('short')}) long={lo} "
            f"({t.get('long')}) med={med} bracket={br} ({t.get('bracket')}) "
            f"haohao={hh} ({t.get('haohao')}) haha={ha} ({t.get('haha')})")

    dist = Counter(min(len(bs), 5) for _, bs in pairs)
    lines.append("bubble_dist=" + " ".join(
        f"{k}:{dist.get(k, 0)}" for k in sorted(dist)))
    if pairs:
        lines.append(f"bubble avg={statistics.mean([len(bs) for _, bs in pairs]):.2f}")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("written")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

