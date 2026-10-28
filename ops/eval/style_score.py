# -*- coding: utf-8 -*-
"""机器人的风格打分器：字符 3-gram 困惑度 + 长度/括号/口癖启发式。

用途：
  - 离线给候选回复排序（best-of-N / A/B），不改线上；
  - 训练语料 = Bot 3310 条真实文字消息。
"""
from __future__ import annotations

import math
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(r"D:/demo")
SRC = ROOT / "聊天提取_Bot.txt"
QUIRKS = ("好好好", "哈哈哈哈", "嗯嗯", "好耶", "那很好了", "啊？", "诶", "捏", "来着", "摸摸")


def _clean(t: str) -> str:
    t = re.sub(r"\[[^\]]*\]", "", t or "")
    return re.sub(r"\s+", "", t)


def load_corpus() -> list[str]:
    out = []
    for line in SRC.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        t = _clean(line)
        if len(t) >= 1:
            out.append(t)
    return out


class StyleModel:
    def __init__(self, corpus: list[str] | None = None):
        self.corpus = corpus or load_corpus()
        self.c1: Counter = Counter()
        self.c2: Counter = Counter()
        self.c3: Counter = Counter()
        for t in self.corpus:
            s = f"^^^{t}$$$"
            for i in range(len(s)):
                self.c1[s[i]] += 1
            for i in range(len(s) - 1):
                self.c2[s[i:i + 2]] += 1
            for i in range(len(s) - 2):
                self.c3[s[i:i + 3]] += 1
        self.vocab = len(self.c1) + 1
        self.median_len = statistics.median(len(t) for t in self.corpus)

    def ppl(self, text: str) -> float:
        t = _clean(text)
        if not t:
            return 999.0
        s = f"^^^{t}$$$"
        nll = 0.0
        n = 0
        for i in range(len(s) - 2):
            a, b, c = s[i], s[i:i + 2], s[i:i + 3]
            p1 = (self.c1.get(c, 0) + 0.1) / (sum(self.c1.values()) + 0.1 * self.vocab)
            p2 = (self.c2.get(b, 0) + 0.1) / (self.c1.get(a, 0) + 0.1 * self.vocab)
            p3 = (self.c3.get(c, 0) + 0.1) / (self.c2.get(b, 0) + 0.1 * self.vocab)
            p = 0.6 * p3 + 0.3 * p2 + 0.1 * p1
            nll -= math.log(max(p, 1e-9))
            n += 1
        return math.exp(nll / max(n, 1))

    def score(self, text: str, mode: str = "banter") -> dict:
        t = _clean(text)
        n = len(t)
        ppl = self.ppl(t)
        len_score = math.exp(-abs(n - 6) / 7.0) if mode != "serious" else math.exp(-abs(n - 28) / 18.0)
        bracket = t.endswith("（") or t.endswith("(") or t.endswith("）")
        if mode == "support":
            bracket_score = 1.0 if bracket else 0.75
        elif mode == "serious":
            bracket_score = 0.3 if bracket else 1.0
        else:
            bracket_score = 0.65 if bracket else 1.0
        quirk = 1.0 if any(q in t for q in QUIRKS) else 0.85
        punct = 1.0 if not re.search(r"[，。；]", t) else 0.6
        ppl_score = 1.0 / (1.0 + ppl / 80.0)
        final = (0.45 * ppl_score + 0.25 * len_score + 0.12 * bracket_score
                 + 0.09 * quirk + 0.09 * punct)
        return {"ppl": round(ppl, 2), "len": n, "bracket": bracket,
                "ppl_score": round(ppl_score, 3), "len_score": round(len_score, 3),
                "bracket_score": round(bracket_score, 3), "quirk": quirk,
                "punct": punct, "final": round(final, 4)}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    model = StyleModel()
    print(f"语料 {len(model.corpus)} 条，3-gram {len(model.c3)} 个，中位长度 {model.median_len}")
    for t in sys.argv[1:] or ["好耶（", "好的，我明白了。", "今天好累 早点睡（"]:
        print(t, "->", model.score(t))
    return 0


if __name__ == "__main__":
    sys.exit(main())
