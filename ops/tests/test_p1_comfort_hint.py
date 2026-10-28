# -*- coding: utf-8 -*-
"""安慰小修：累/疼接住；玩笑句、晚安不触发。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

from astrbot.core.utils.comfort_hint import (  # noqa: E402
    comfort_hint,
    comfort_reply_fallback,
)

OK = True


def check(cond: bool, label: str) -> None:
    global OK
    OK = OK and bool(cond)
    print(f"  {'OK  ' if cond else 'FAIL'} {label}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    for text in ("很累是真的。", "我肚子开始疼了", "真有点累有点破防。",
                 "我这几天偏头疼的厉害。"):
        block = comfort_hint(text)
        check(bool(block) and "不要追问" in block, f"hit {text!r}")
    for text in ("第一次看到肚子疼也会传染。", "晚安", "今天吃什么"):
        check(comfort_hint(text) == "", f"no hint {text!r}")

    check(comfort_reply_fallback("我以为你有点不高兴来着，我这几天偏头疼的厉害。", "x")
          == "诶 怎么偏头疼了\n有吃药吗（", "16 分行")
    check(comfort_reply_fallback("很累是真的。", "x") == "摸摸 那今天早点睡", "17 定稿")
    check(comfort_reply_fallback("我肚子开始疼了", "x") == "诶 又疼了\n吃药了没", "18 第一条")
    check(comfort_reply_fallback("我肚子开始疼了", "x") == "摸摸", "18 轮换摸摸")
    check(comfort_reply_fallback("第一次看到肚子疼也会传染。", "x") == "哈哈哈哈哈哈", "19 旧回复")
    check(comfort_reply_fallback("我今天也会很累", "x") == "摸摸 那今天早点睡",
          "也会很累不误判玩笑")
    check(comfort_hint("我今天不累") == "" and comfort_reply_fallback("我今天不累", "x") == "",
          "否定不触发")
    check(comfort_hint("我肚子不疼了") == "" and comfort_reply_fallback("我肚子不疼了", "x") == "",
          "不疼不触发")
    check(comfort_reply_fallback("真有点累有点破防。", "x") == "诶 怎么啦（", "20 旧回复")
    print("\nRESULT:", "PASS" if OK else "FAIL")
    return 0 if OK else 1


if __name__ == "__main__":
    sys.exit(main())
