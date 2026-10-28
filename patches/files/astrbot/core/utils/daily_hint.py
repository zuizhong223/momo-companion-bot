# -*- coding: utf-8 -*-
"""日常小修：被问“什么时候睡/还不睡”时，只给一个状态，不编“挂着/在吃饭”。

只命中睡眠时间问句；“晚安/去睡吧”这类支持场景不碰。
"""
from __future__ import annotations

import itertools
import re

_SLEEP_RE = re.compile(
    r"什么时候睡|几点睡|还不睡|不睡吗|打算睡|准备睡|该睡|睡不睡|去睡吗"
)
_SUMMARY_RE = re.compile(r"水到渠成|顺其自然")
# 日常第 10 条定稿：接住总结，不追问；四条轮换。
_SUMMARY_VARIANTS = ("那很好了（", "这样挺好（", "那不挺好吗（", "嗯 慢慢来（")
_SUMMARY_CYCLE = itertools.count()

_HINT = (
    "<system_reminder>用户在问你什么时候睡。只输出两条气泡，中间用一个空格隔开：\n"
    "还不困（ 一会就睡（\n"
    "不要换行、不要合并成一句、不要反问；不要用“挂着/在吃饭/不知道耶”。\n"
    "</system_reminder>"
)


def daily_hint(text: str) -> str:
    return _HINT if _SLEEP_RE.search(text or "") else ""


def daily_reply_fallback(text: str, reply_text: str = "") -> str:
    """“水到渠成/顺其自然”这类总结不追问，四条短回复轮换。"""
    if not _SUMMARY_RE.search(text or ""):
        return ""
    return _SUMMARY_VARIANTS[next(_SUMMARY_CYCLE) % len(_SUMMARY_VARIANTS)]
