# -*- coding: utf-8 -*-
"""P1-6：跨轮去重只在用户上下文也相似时生效，不能按 bot 句子单边误杀。"""
import sys
from pathlib import Path

CORE = Path(r"D:\demo\instances\11111111-1111-4111-8111-111111111111\core")
sys.path.insert(0, str(CORE))
PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    from astrbot.core.platform.sources.aiocqhttp import aiocqhttp_message_event as ev

    ev._SEM_CTX.clear()
    key = "p:sem-test"
    text = "好好好好"
    first, acts = ev._semantic_guard(text, "今天好累", key, "normal")
    check(first == text, "首次不替换", str(acts))

    different, acts2 = ev._semantic_guard(text, "代码报错了怎么办", key, "normal")
    check(different == text, "用户换话题时不误杀", str(acts2))

    same, acts3 = ev._semantic_guard(text, "今天好累", key, "normal")
    check(same != text, "同一话题重复才替换", str(acts3))

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
