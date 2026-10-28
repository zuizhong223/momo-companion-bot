# -*- coding: utf-8 -*-
"""主动跨天重复：主题词包含度测试（合成样例）。"""
import sys
from pathlib import Path

CORE = Path(r"D:\demo\instances\11111111-1111-4111-8111-111111111111\core")
sys.path.insert(0, str(CORE))

from astrbot.dashboard.services.proactive_service import topic_duplicate  # noqa: E402

olds = [
    "刚看到有人用杯子敲节奏 离谱（",
    "今天早上被闹钟吵醒 爬起来关闹钟还撞了一下 烦死了（",
    "然后我今天下午就看到一个视频 就是拿杯子敲节奏 然后敲得还真挺像那么回事的",
]
dup_new = "然后就是 我看到那个杯子的视频 敲节奏 然后我就想起来 反正太离谱了 就是不想试"
ok = True
hit = topic_duplicate(olds, dup_new)
print("repeat ->", hit)
ok = ok and bool(hit)
ctrl = topic_duplicate(olds, "今晚练习练到手指都僵了 这乐器也太难了吧")
print("control ->", ctrl)
ok = ok and not ctrl
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
