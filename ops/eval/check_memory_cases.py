# -*- coding: utf-8 -*-
"""B03 自检：20 条记忆题的结构/证据覆盖。

只读 JSONL，不改文件。退出码 0 = 结构完整；1 = 缺字段/重复 id。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(r"D:/demo")
CASES = ROOT / "ops" / "eval" / "cases_memory_20.jsonl"
CHAT = ROOT / "聊天全记录_Bot.txt"
REQUIRED = ("id", "cat", "kind", "input", "reference", "source")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    rows = [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    bad = []
    ids = Counter()
    kinds = Counter()
    for i, r in enumerate(rows, 1):
        ids[r.get("id")] += 1
        kinds[r.get("source", "").split(":")[0] or "none"] += 1
        for k in REQUIRED:
            if not str(r.get(k) or "").strip():
                bad.append(f"行{i} 缺 {k}")
    dup = [k for k, v in ids.items() if v > 1]
    if dup:
        bad.append(f"重复 id: {dup}")

    chat_lines = CHAT.read_text(encoding="utf-8", errors="replace").splitlines()
    refs = 0
    for r in rows:
        src = str(r.get("source") or "")
        if not src.startswith("chat:"):
            continue
        try:
            n = int(src.split(":", 1)[1])
        except ValueError:
            bad.append(f"{r.get('id')} source 行号非法: {src}")
            continue
        if not (1 <= n <= len(chat_lines)):
            bad.append(f"{r.get('id')} 行号越界: {n}")
            continue
        refs += 1

    print(f"cases = {len(rows)}")
    print(f"chat evidence = {refs}")
    print("source kinds =", dict(kinds))
    print("missing/duplicate =", bad or "none")
    if len(rows) != 20:
        print("FAIL: 不是 20 条")
        return 1
    if bad:
        print("FAIL")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
