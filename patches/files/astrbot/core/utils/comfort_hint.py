# -*- coding: utf-8 -*-
"""安慰小修：累/疼先接住；16/18 按指定分行，18 轮换“摸摸”。

- 16 偏头疼：`诶 怎么偏头疼了` / `有吃药吗（`（两条气泡）
- 17 很累是真的：`摸摸 那今天早点睡`
- 18 肚子开始疼：`诶 又疼了` / `吃药了没`，与 `摸摸` 轮换
- 19 肚子疼也会传染：旧回复 `哈哈哈哈哈哈`
- 20 真有点累有点破防：旧回复 `诶 怎么啦（`
"""
from __future__ import annotations

import itertools
import re

_FATIGUE_RE = re.compile(r"很累|好累|累是真的|有点累|真有点累|破防|累死")
_PAIN_RE = re.compile(r"(我|肚子|胃|头).{0,6}(疼|痛)")
_JOKE_RE = re.compile(r"传染|第一次看到")
_NEG_RE = re.compile(r"不累|不太累|没累|不疼|不太疼|没疼|不痛")
_CYCLE = itertools.count()


def comfort_hint(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    if _JOKE_RE.search(t):
        return ""
    if _NEG_RE.search(t):
        return ""
    if not (_FATIGUE_RE.search(t) or _PAIN_RE.search(t)):
        return ""
    return (
        "<system_reminder>用户在说累/疼。先接住，1 句，短；"
        "不要追问“怎么了/吃药了没/忙坏了吧”，不要医务盘问。\n"
        "累：摸摸 那今天早点睡／累了就歇会\n"
        "疼：又疼了 摸摸／揉揉\n"
        "</system_reminder>"
    )


def comfort_reply_fallback(user_text: str, reply_text: str) -> str:
    """按这一轮定稿覆盖；不匹配返回空，交给模型/其他兜底。"""
    u = (user_text or "").strip()
    if not u:
        return ""
    if _NEG_RE.search(u):
        return ""
    # 玩笑句：用旧回复
    if "传染" in u:
        return "哈哈哈哈哈哈"
    # 破防：用旧回复
    if "破防" in u:
        return "诶 怎么啦（"
    # 16 偏头疼：旧回复在“有吃药”前分行
    if "偏头疼" in u or "头疼" in u:
        return "诶 怎么偏头疼了\n有吃药吗（"
    # 17 很累是真的
    if "很累是真的" in u or "很累" in u:
        return "摸摸 那今天早点睡"
    # 18 肚子开始疼：两条气泡 / 摸摸 轮换
    if "肚子开始疼" in u or "肚子疼" in u:
        if next(_CYCLE) % 2 == 0:
            return "诶 又疼了\n吃药了没"
        return "摸摸"
    return ""
