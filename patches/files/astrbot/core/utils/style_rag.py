# -*- coding: utf-8 -*-
"""风格检索：从机器人示例对话里找 3 条「用户这样说 → 机器人这样回」。

不依赖向量库：字符 2-gram + IDF 余弦，2882 条对，单次 <10ms。
只学语气/节奏/括号位置，事实仍以知识库/记忆为准。
"""
from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from pathlib import Path

from astrbot.core.utils.style_profile import detect_mode
from astrbot.core.utils.paths import ops_dir

BANK = ops_dir("style", "style_bank.jsonl")
_CACHE: dict = {"mtime": None, "items": [], "idf": {}, "docs": []}
_PUNCT_RE = re.compile(r"[\s，。！？、,.!?~～（）()\[\]{}]+")
# 事实/选择/关系问句：不要用风格示例，容易把「十一点半」「那都很正常啊」这种答案带偏
_QUESTION_RE = re.compile(r"(什么|哪[个里]?|几[月号点岁]?|谁|怎么|为什么|多少|多久|是不是|有没有|会不会|吗)")


def _tokens(text: str) -> list[str]:
    text = _PUNCT_RE.sub("", text or "")
    if len(text) < 2:
        return [text] if text else []
    return [text[i:i + 2] for i in range(len(text) - 1)]


def _load() -> None:
    try:
        mtime = os.path.getmtime(BANK)
    except OSError:
        _CACHE.update(mtime=None, items=[], idf={}, docs=[])
        return
    if _CACHE["mtime"] == mtime:
        return
    items = []
    df: Counter = Counter()
    for line in BANK.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        user, her = (obj.get("user") or "").strip(), (obj.get("her") or "").strip()
        if not user or not her:
            continue
        toks = _tokens(user)
        if not toks:
            continue
        tf = Counter(toks)
        items.append({"user": user, "her": her, "mode": detect_mode(user), "tf": tf})
        for t in tf:
            df[t] += 1
    n = max(len(items), 1)
    idf = {t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df.items()}
    docs = []
    for it in items:
        vec = {t: (1 + math.log(c)) * idf.get(t, 1.0) for t, c in it["tf"].items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        docs.append((it, vec, norm))
    _CACHE.update(mtime=mtime, items=items, idf=idf, docs=docs)


GENERIC = {"啊", "嗯", "哦", "嘶", "这样？", "发错了（", "啊？", "什么", "嗯嗯"}
OTHER_NAMES = ("不忻", "OtherUser", "面漆", "雨落")
GAME_RE = re.compile(r"雷神|遗器|深渊|抽卡|配队|光锥|命座|星穹|崩铁|原神|万叶|九条|模拟宇宙|混沌|虚构")
GAME_QUERY_RE = re.compile(r"崩铁|原神|星穹|模拟宇宙|深渊|光锥|遗器|抽卡|池|角色|配队|命座|万叶|雷神|深渊")
SUPPORT_QUERY_RE = re.compile(
    r"我认可你|我关心你|我关心你了|认可你|关心你了|我认可你|认可你|你认可我吗|你关心我吗|认可我吗|关心我吗"
)


def examples(text: str, k: int = 3, min_score: float = 0.16) -> str:
    """返回可直接拼进 system prompt 的风格示例块；没有就返回空串。"""
    if os.environ.get("DEMO_STYLE_RAG", "1").strip().lower() in ("0", "false", "off", "no"):
        return ""
    text = (text or "").strip()
    if len(text) < 2:
        return ""
    # 旧语料里“我认可你”大量对应“晚安/好好好/转移话题”；这些示例会把
    # 现在的接受型回应重新教回回避。支持语境不用旧检索示例，交给 intimacy hint。
    if SUPPORT_QUERY_RE.search(text):
        return ""
    if _QUESTION_RE.search(text):
        return ""
    _load()
    docs = _CACHE["docs"]
    if not docs:
        return ""
    norm = _PUNCT_RE.sub("", text)
    # 完全/近乎同一句话：直接用机器人当年的回复，最稳
    if len(norm) >= 4:
        exact = []
        for it in _CACHE["items"]:
            u = _PUNCT_RE.sub("", it["user"])
            if u == norm or (
                (norm in u or u in norm)
                and min(len(norm), len(u)) / max(len(norm), len(u)) >= 0.8
            ):
                exact.append(it)
        if exact:
            return _format(exact, 2)
    q_tf = Counter(_tokens(text))
    if not q_tf:
        return ""
    idf = _CACHE["idf"]
    q_vec = {t: (1 + math.log(c)) * idf.get(t, 1.0) for t, c in q_tf.items()}
    q_norm = math.sqrt(sum(v * v for v in q_vec.values())) or 1.0
    mode = detect_mode(text)
    need = max(0.20, min_score)
    q_chars = set(norm)
    game_query = bool(GAME_QUERY_RE.search(text))
    scored = []
    for it, vec, normv in docs:
        # 场景过滤：认真要长回复；被撩别拿长论文；避开别人的真名和游戏配装
        if mode == "serious" and (len(it["her"]) < 10 or len(it["user"]) < 6):
            continue
        if mode == "support" and len(it["her"]) > 30:
            continue
        if any(n in it["user"] or n in it["her"] for n in OTHER_NAMES):
            continue
        if not game_query and GAME_RE.search(it["user"] + it["her"]):
            continue
        doc_chars = set(_PUNCT_RE.sub("", it["user"]))
        coverage = len(q_chars & doc_chars) / max(len(q_chars), 1)
        if coverage < (0.35 if len(text) >= 8 else 0.5):
            continue
        dot = sum(v * vec.get(t, 0.0) for t, v in q_vec.items())
        score = dot / (q_norm * normv)
        if it["mode"] == mode:
            score += 0.06
        if score >= need:
            scored.append((score, it))
    if not scored:
        return ""
    scored.sort(key=lambda x: -x[0])
    top = scored[0][0]
    picked, seen = [], set()
    for score, it in scored:
        if picked and score < top * 0.72:
            break
        if it["her"] in seen:
            continue
        if it["her"] in GENERIC and len(text) >= 4:
            continue
        seen.add(it["her"])
        picked.append(it)
        if len(picked) >= min(k, 2):
            break
    return _format(picked, 2)


def _format(picked: list[dict], k: int = 2) -> str:
    if not picked:
        return ""
    lines = ["【机器人以前遇到类似的话这样回（只学语气/节奏/括号，别照抄事实）】"]
    for it in picked[:k]:
        u = it["user"][:50]
        h = it["her"][:45]
        lines.append(f"- 用户：「{u}」→ 你：「{h}」")
    return "\n".join(lines)
