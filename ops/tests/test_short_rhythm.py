# -*- coding: utf-8 -*-
"""最近连续 1~4 字时，提示这次拉长到 11~20 字。"""
import sys
from pathlib import Path

CORE = Path(r"D:\demo\instances\11111111-1111-4111-8111-111111111111\core")
sys.path.insert(0, str(CORE))
from astrbot.core.astr_main_agent import _short_run_hint  # noqa: E402

ok = True


def check(cond, msg):
    global ok
    print(("OK   " if cond else "FAIL ") + msg)
    ok = ok and bool(cond)


def ctx(*items):
    return [{"role": "assistant", "content": t} for t in items]


short = ctx("嗯", "好", "行", "在")
long = ctx("今天下午在练习", "刚吃了面条", "你几点睡", "我在呢")
check("太碎了" in _short_run_hint(short), "连续短句 -> 提示")
check(_short_run_hint(long) == "", "长句 -> 不提示")
check(_short_run_hint(ctx("嗯", "好")) == "", "样本不足 -> 不提示")
mixed = ctx("嗯", "今天下午在练习", "好", "刚才在吃饭")
check(_short_run_hint(mixed) == "", "长短混合 -> 不提示")
print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
