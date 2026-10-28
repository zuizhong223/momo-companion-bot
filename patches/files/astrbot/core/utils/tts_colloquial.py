# -*- coding: utf-8 -*-
"""P3-18（生产版）：语音的"二次口语化"。

机器人打字是「喜欢） 刚睡醒就听你问这个（」——那个「（」「）」是打字习惯，
念出来会很怪（TTS 可能念"左括号"或直接卡住）。所以在**送 TTS 之前**做一遍改写：

  1. 去掉括号类符号（（ ） ( ) ～ 等），句尾补一个停顿；
  2. 去掉 markdown 记号（* _ ` #）；
  3. 过长的句子按「，」断开（TTS 长句容易糊）；
  4. 可选：交给本地 3B 润色一遍（默认关，3B 容易改坏）。

不改语义、不加内容，只做"念出来顺"。

用法：
  python ops\\persona\\tts_colloquial.py "喜欢） 刚睡醒就听你问这个（"
"""
from __future__ import annotations

import re
import sys

BRACKETS = "（）()【】[]［］"
NOISE = "*_`#~～"
SENT_SPLIT = 18          # 单句超过这么多字就按逗号拆


def to_speech(text: str) -> str:
    if not text:
        return ""
    s = text
    # 句尾的「（」是"话没说完、收着说"：念出来要变成渐弱的省略号，
    # 直接删掉会变成硬收尾，听起来很机械。
    s = re.sub(r"[（(]\s*$", "……", s)
    for ch in BRACKETS:
        s = s.replace(ch, "")
    for ch in NOISE:
        s = s.replace(ch, "")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"，\s*，+", "，", s)
    # 尾巴上一个孤零零的逗号/顿号，改句号
    s = re.sub(r"[，、,]\s*$", "。", s.strip())
    s = s.strip()
    if not s:
        return ""
    # 长句拆开
    parts = re.split(r"(?<=[，,。！？!?])", s)
    out, buf = [], ""
    for p in parts:
        if len(buf) + len(p) > SENT_SPLIT and buf:
            out.append(buf)
            buf = p
        else:
            buf += p
    if buf:
        out.append(buf)
    s = " ".join(x.strip() for x in out if x.strip())
    if s and s[-1] not in "。！？!?…":
        s += "。"
    return s


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    samples = sys.argv[1:] or [
        "喜欢） 刚睡醒就听你问这个（",
        "那还是再多睡会吧）",
        "）",
        "好好好",
        "憨）",
        "**不是啊** 用户特别棒）真的很棒）",
    ]
    for raw in samples:
        print(f"{raw!r:<34} -> {to_speech(raw)!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
