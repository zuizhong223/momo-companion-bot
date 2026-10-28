# -*- coding: utf-8 -*-
"""游戏语境小修：只在游戏/具体内容时注入，不误伤普通聊天。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

from astrbot.core.utils.game_hint import game_hint, game_reply_fallback  # noqa: E402

OK = True


def check(cond: bool, label: str) -> None:
    global OK
    OK = OK and bool(cond)
    print(f"  {'OK  ' if cond else 'FAIL'} {label}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    for text in ("还是先拿星穹奖励", "送你游戏你大概率也不玩）",
                 "你还在玩游戏吗", "歪打正着，小黑塔光锥也解决了"):
        block = game_hint(text)
        check(bool(block) and "先回答" in block, f"game hit {text!r}")
    for text in ("今天吃什么", "我今天很累", "晚安", "在干嘛"):
        check(game_hint(text) == "", f"no game hint {text!r}")
    check(game_reply_fallback("送你游戏你大概率也不玩）") == "什么游戏（",
          "送你游戏定稿")
    check(game_reply_fallback("不知道干嘛，你还在玩游戏吗")
          == "没，刚刚在拿快递）", "在玩游戏吗定稿")
    print("\nRESULT:", "PASS" if OK else "FAIL")
    return 0 if OK else 1


if __name__ == "__main__":
    sys.exit(main())
