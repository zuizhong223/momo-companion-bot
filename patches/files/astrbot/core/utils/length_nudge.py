# -*- coding: utf-8 -*-
"""长度补一句：把“什么耳机）”这种短问句补成两句；把道歉后的“没事”补一句具体的话。

为什么不用 prompt：C/D 验证里，分享场景 11+ 一直是 0%，模型只回「什么耳机）」。
这里只处理两个**上下文明确**的短模板，不动其他文本：
  - 买了东西：`什么<物品>）` -> 补「多少钱买的 / 好用吗 / 什么型号的」
  - 道歉：serious 模式下的 `没事 / 没有 / 没生气` -> 补「下次注意就行 别一直道歉」等
"""
from __future__ import annotations

import hashlib
import re

SHARE_ITEMS = re.compile(r"耳机|手机|鞋|键盘|杯子|相机|电脑|平板|衣服|包|手表|多少钱")
APOLOGY_ACK = re.compile(r"^(没事|没有|没生气|不用道歉|没怪你|没事喔|没有啊|没怪)[^，。！？\n]{0,8}$")

SHARE_TAILS = ("多少钱买的 我也看看", "好用吗 什么型号的", "什么型号的 多少钱")
APOLOGY_TAILS = (" 下次注意就行 别一直道歉", " 我知道你不是故意的 别多想", " 不用道歉啊 我又没怪你")


def _pick(seed: str, options: tuple[str, ...]) -> str:
    h = int(hashlib.md5(seed.encode("utf-8")).hexdigest()[:8], 16)
    return options[h % len(options)]


def apply_length_nudge(text: str, session_key: str = "default",
                       mode: str = "banter", record: bool = True) -> tuple[str, list[str]]:
    """返回 (可能变长的文本, 动作)。无状态、确定性；同一输入重复调用结果一致。"""
    if not text:
        return text, []
    t = text.strip()
    if SHARE_ITEMS.search(t) and len(re.sub(r"\s+", "", t)) <= 12:
        if any(k in t for k in ("多少钱", "好用吗", "什么型号")):
            return text, []
        # 约一半补，避免每句分享都变成追问
        if int(hashlib.md5(f"{session_key}|{t}|share2".encode("utf-8")).hexdigest()[:8], 16) % 2:
            return text, []
        tail = _pick(f"{session_key}|{t}|share", SHARE_TAILS)
        base = t.rstrip("）)")
        new = f"{base}）{tail}"
        return new, [f"补一句：{t}→{new}"]
    if mode == "serious" and APOLOGY_ACK.match(t):
        norm_len = len(re.sub(r"\s+", "", t))
        if norm_len > 14:
            return text, []
        # 8 字以内必补；9~14 字约一半补
        if norm_len > 8 and int(hashlib.md5(
                f"{session_key}|{t}|apology2".encode("utf-8")).hexdigest()[:8], 16) % 2:
            return text, []
        tail = _pick(f"{session_key}|{t}|apology", APOLOGY_TAILS)
        base = t.rstrip("（(）)")
        new = f"{base}{tail}"
        return new, [f"补一句：{t}→{new}"]
    return text, []
