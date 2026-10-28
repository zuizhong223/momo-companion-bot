# -*- coding: utf-8 -*-
"""S10 日志扫描：只读，找 sk-/Bearer/手机号；命中返回 1。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(r"D:/demo")
FILES = [ROOT / "astrbot_run2.log", ROOT / "Bot管家.log",
         ROOT / "ops/alerts/alerts.log", ROOT / "ops/alerts/watchdog.log"]
PATTERNS = {
    "key": re.compile(r"sk-[A-Za-z0-9_-]{10,}"),
    "bearer": re.compile(r"Bearer\s+[A-Za-z0-9._-]{10,}"),
    "phone": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
}


def mask(s: str) -> str:
    return s[:6] + "…" + s[-4:] if len(s) > 12 else s[:4] + "…"


def main() -> int:
    hits = 0
    for p in FILES:
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for name, rx in PATTERNS.items():
            for m in rx.finditer(text):
                hits += 1
                line = text.count("\n", 0, m.start()) + 1
                print(f"{p.name}:{line} [{name}] {mask(m.group(0))}")
    print(f"\nRESULT: {'FAIL' if hits else 'PASS'}（命中 {hits}）")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
