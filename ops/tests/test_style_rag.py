# -*- coding: utf-8 -*-
"""风格检索单元测试：不调模型，只验库/过滤/输出格式。"""
import sys
from pathlib import Path

CORE = Path(r"D:\demo\instances\11111111-1111-4111-8111-111111111111\core")
sys.path.insert(0, str(CORE))

from astrbot.core.utils.style_rag import examples  # noqa: E402

ok = True


def check(cond, msg):
    global ok
    print(("OK   " if cond else "FAIL ") + msg)
    ok = ok and bool(cond)


block = examples("在干嘛")
check("用户：" in block and "你：" in block, "在干嘛 有示例")
check(len(block) < 800, f"长度 {len(block)}")
check("[动画表情]" not in block, "不含表情占位")
check("他人A" not in block and "OtherUser" not in block, "不含别人真名")

serious = examples("你觉得我们到底是什么关系，以后怎么办")
check(len(serious) < 900, "认真示例长度")
check("雷神" not in serious and "遗器" not in serious, "认真示例不串游戏配装")

check(examples("") == "", "空输入返回空")
check(examples("a") == "", "太短返回空")
check(examples("你学什么乐器") == "", "事实问句不注入")
check(examples("你会不会离开我") == "", "关系问句不注入")

print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
