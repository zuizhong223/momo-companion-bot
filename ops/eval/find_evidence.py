# -*- coding: utf-8 -*-
"""在示例对话里找证据行（B03 用）。

用法：
  python ops\\eval\\find_evidence.py 示例 日期 练习
输出：行号、时间、说话人、片段；不改文件。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(r"D:/demo")
CHAT = ROOT / "examples/chat_sample.txt"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    queries = sys.argv[1:] or ["示例", "日期", "练习"]
    if not CHAT.exists():
        print(f"缺 {CHAT}")
        return 1
    lines = CHAT.read_text(encoding="utf-8", errors="replace").splitlines()
    for q in queries:
        print(f"\n## {q}")
        n = 0
        for i, line in enumerate(lines, 1):
            if q not in line:
                continue
            print(f"{i:6d} | {line[:180]}")
            n += 1
            if n >= 8:
                break
        if not n:
            print("（没有命中）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
