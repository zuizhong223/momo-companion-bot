# -*- coding: utf-8 -*-
"""贴纸标签/场景过滤 + 场景判定的离线测试（不依赖 AstrBot 运行时）。"""
import json
import sys
from pathlib import Path

CORE = Path(r"D:\demo\instances\11111111-1111-4111-8111-111111111111\core")
BANK = Path(r"D:\demo\表情库\bank.jsonl")
sys.path.insert(0, str(CORE))

from astrbot.core.utils.style_profile import (  # noqa: E402
    MODE_STICKER_TAGS,
    detect_mode,
    should_sticker,
    sticker_pool,
)

items = [json.loads(l) for l in BANK.read_text(encoding="utf-8").splitlines() if l.strip()]
ok = True


def check(cond, msg):
    global ok
    print(("OK   " if cond else "FAIL ") + msg)
    ok = ok and bool(cond)


check(len(items) >= 100, f"表情库 {len(items)} 条")
check(all(it.get("tags") for it in items), "每条都有 tags")
check(all((it.get("bytes") or 0) <= 400 * 1024 for it in items), "每条 ≤400KB")

for mode in ("banter", "support", "comfort", "serious"):
    pool = sticker_pool(items, mode)
    check(len(pool) >= 5, f"{mode} 候选 {len(pool)} 张")
    good = [x for x in pool if set(x.get("tags") or ["general"]) & MODE_STICKER_TAGS[mode]]
    check(len(good) == len(pool), f"{mode} 全部命中偏好标签")

cases = [
    ("在干嘛", "banter"),
    ("我关心你了", "support"),
    ("我好累 今天烦死了", "comfort"),
    ("你觉得我们到底是什么关系 以后怎么办", "support"),
    ("你喜不认可我", "support"),
    ("晚安", "support"),
]
for text, want in cases:
    got = detect_mode(text)
    check(got == want, f"detect {text!r} -> {got} (want {want})")

check(not should_sticker("serious", "这是一段很长的认真回复" * 3), "认真长文不带贴纸")

print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
