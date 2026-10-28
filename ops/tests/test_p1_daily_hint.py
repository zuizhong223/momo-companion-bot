# -*- coding: utf-8 -*-
"""日常小修：问睡觉时间给两条气泡；晚安等非问句不触发。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

from astrbot.core.utils.daily_hint import (  # noqa: E402
    daily_hint,
    daily_reply_fallback,
)

OK = True


def check(cond: bool, label: str) -> None:
    global OK
    OK = OK and bool(cond)
    print(f"  {'OK  ' if cond else 'FAIL'} {label}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    for text in ("你打算什么时候睡觉", "还不睡吗", "准备睡了吗"):
        block = daily_hint(text)
        check(bool(block) and "两条气泡" in block, f"hit {text!r}")
    for text in ("晚安", "赶紧去睡觉，晚安", "今天吃什么", "在干嘛"):
        check(daily_hint(text) == "", f"no hint {text!r}")

    text = "还不困（ 一会就睡（"
    segs = re.findall(r".*?[ 。？！，~…]+|.+$", text, re.DOTALL | re.MULTILINE)
    check(len([s for s in segs if s.strip()]) == 2, "两条气泡分段")

    got = [daily_reply_fallback("这个事，好像确实是水到渠成的", "x") for _ in range(4)]
    check(got == ["那很好了（", "这样挺好（", "那不挺好吗（", "嗯 慢慢来（"],
          "水到渠成四条轮换")
    check(daily_reply_fallback("今天吃什么", "x") == "", "非总结不触发")
    print("\nRESULT:", "PASS" if OK else "FAIL")
    return 0 if OK else 1


if __name__ == "__main__":
    sys.exit(main())
