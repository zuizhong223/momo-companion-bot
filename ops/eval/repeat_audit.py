# -*- coding: utf-8 -*-
"""Q4：复读审计。只读对话库，标出重复回复及其上一句用户消息。"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(r"D:/demo")
DB = ROOT / "instances/11111111-1111-4111-8111-111111111111/core/data/data_v4.db"
OUT = ROOT / "ops/eval/reports/repeat_audit.txt"


def text_of(m):
    return "".join(p.get("text", "") for p in (m.get("content") or [])
                   if isinstance(p, dict)).strip()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    row = con.execute("select content from conversations order by updated_at desc limit 1").fetchone()
    con.close()
    msgs = json.loads(row[0]) if row else []
    pairs = []
    last_user = ""
    for m in msgs:
        t = text_of(m)
        if not t:
            continue
        if m.get("role") == "user":
            t = re.sub(r"<system_reminder>.*?</system_reminder>", "", t, flags=re.S).strip()
            last_user = t
        elif m.get("role") == "assistant" and last_user:
            pairs.append((last_user, t))
            last_user = ""
    pairs = pairs[-80:]
    norm = lambda s: re.sub(r"[\s）)（(，,。.!！?？]", "", s)
    cnt = Counter(norm(a) for _, a in pairs)
    repeats = {k: v for k, v in cnt.items() if v >= 2 and len(k) >= 2}
    print(f"pairs={len(pairs)} repeated_keys={len(repeats)}")
    lines = [f"pairs={len(pairs)} repeated_keys={len(repeats)}"]
    for key, n in sorted(repeats.items(), key=lambda x: -x[1])[:12]:
        ctx = [(u, a) for u, a in pairs if norm(a) == key]
        print(f"\n[{n}x] {key}")
        lines.append(f"\n[{n}x] {key}")
        for u, a in ctx[:6]:
            print(f"  U: {u[:45]}  -> A: {a[:45]}")
            lines.append(f"  U: {u[:45]}  -> A: {a[:45]}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nreport={OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
