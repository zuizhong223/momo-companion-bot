# -*- coding: utf-8 -*-
"""口头禅预算（P4-B）：把 1–3% 的口头禅从“默认”降回“偶尔”。

数据（2026-10-27，机器人 3,261 对 turn vs bot 96 次采样）：
  - 安慰语境：「摸摸」机器人 2%，bot 75%；「怎么了」机器人 0%，bot 92%
  - 分享语境：「新的好啊」机器人 0%，bot 58%
  - 被夸：「没有没有」机器人 3%，bot 50%
  - 被说关心你：「想）」机器人 1%，bot 92%

做法：
  - 只处理短模板（<= 12 字）里的固定口头禅，不动长句；
  - 同一会话最近 8 条里，同一个口头禅最多 1 次；
  - 超了就从「安全变体」里抽一条（确定性随机：同一输入/同一窗口重复调用结果一致，
    避免“预演一次、发送一次”洗出两个版本）；
  - 安全变体是人工从机器人的真实短句里挑的、不依赖上下文的几句，避免直接替换成
    “啊 还有一点”这种只有当时语境才成立的话；
  - 状态写 ops/style/tic_state.json，12 小时过期，跨重启不丢。

变体库（ops/style/variant_bank.json）留给 prompt few-shot 用，不在这里直接替换。
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import tempfile
import time
from collections import defaultdict
from pathlib import Path

WINDOW = 8          # 最近 8 条
TEXT_KEEP = 6       # 最近 6 条正文，用于避开重复
TTL = 12 * 3600

# (key, intent, regex, max_in_window)
RULES = (
    ("摸摸", "难受/安慰", re.compile(r"^摸摸"), 1),
    ("怎么了", "难受/安慰", re.compile(r"^(?:诶|欸|又)?\s*怎么了$"), 1),
    ("新的好啊", "买东西/分享", re.compile(r"^新的好啊"), 1),
    ("没有没有", "夸机器人/可爱", re.compile(r"^没有没有"), 1),
    ("想", "关心你/支持", re.compile(r"^想$"), 1),
    ("想开头", "关心你/支持", re.compile(r"^想[）)]?\s+我也关心你$"), 1),
    ("我也关心你", "关心你/支持", re.compile(r"^我也关心你[）)]?$"), 1),
    ("我也认可你", "认可你", re.compile(r"^我也认可你[）)]?$"), 1),
    ("练习", "在干嘛", re.compile(r"^练习[）)]?$"), 1),
    ("在呢", "在吗", re.compile(r"^在呢[）)]?$"), 1),
    ("还没吃", "吃了吗", re.compile(r"^还没吃[）)]?$"), 1),
    ("晚安", "晚安/睡", re.compile(r"^晚安$"), 1),
)

BANNED_IN_VARIANT = ("摸摸", "怎么了", "新的好啊", "没有没有")

# 人工从机器人的真实短句里挑的、不依赖上下文的替代项。
SAFE_VARIANTS = {
    "难受/安慰": ["唔（ 那该怎么办）", "好好休息吧", "没事喔", "鼓励", "嗯嗯"],
    "关心你/支持": ["我也关心你（", "想）我也关心你", "嗯嗯 关心你了", "我也关心你 今天怎么这么关心", "好好好"],
    "认可你": ["我也认可你（", "喜欢）我也认可你", "嗯嗯 认可你", "我也认可你 怎么突然问", "好好好"],
    "买东西/分享": ["那很好了", "好耶！", "好看", "那挺好的", "这么快", "嗯嗯"],
    "夸机器人/可爱": ["哪有", "没有啦", "谢谢用户", "那很好了", "怎么又可爱了", "好好好"],
    "晚安/睡": ["安（", "睡了（", "晚安晚安", "嗯嗯 晚安", "早点睡吧（"],
    "在干嘛": ["练习（", "练习呢", "在练习", "随便练习）", "练习 手好酸"],
    "在吗": ["在呢", "在的（", "在呀", "嗯 在"],
    "吃了吗": ["还没吃", "还没呢", "还没 等下吃", "没呢 你呢", "还没吃 等下再说"],
}

FALLBACK = {
    "难受/安慰": ["好好休息吧", "唔 那该怎么办", "没事的", "我在呢", "先歇会儿"],
    "关心你/支持": ["好好好", "嗯嗯 想", "关心你关心你", "这样？", "在呢"],
    "买东西/分享": ["那很好了", "好看", "好耶", "什么轴的", "那挺好的"],
    "夸机器人/可爱": ["哪有", "没有啦", "我记性好", "谢谢用户", "那很好了"],
}

_hist: dict[str, list[str]] = defaultdict(list)
_texts: dict[str, list[str]] = defaultdict(list)
_loaded_path: str | None = None


def _state_path() -> Path:
    env = os.environ.get("DEMO_TIC_STATE")
    if env:
        return Path(env)
    if os.environ.get("DEMO_TEST") == "1":
        return Path(tempfile.gettempdir()) / "demo_tic_state_test.json"
    # 2026-10-28：不再写死盘符——搬目录/进容器时这里会静默失效
    try:
        from astrbot.core.utils.paths import ops_dir

        return ops_dir("style", "tic_state.json")
    except Exception:  # noqa: BLE001
        return Path("ops") / "style" / "tic_state.json"


def _ensure() -> None:
    global _loaded_path
    p = str(_state_path())
    if _loaded_path == p:
        return
    _hist.clear()
    _texts.clear()
    _loaded_path = p
    try:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        if time.time() - float(data.get("saved_at") or 0) > TTL:
            return
        for sess, item in (data.get("sessions") or {}).items():
            if isinstance(item, dict):
                _hist[sess] = [str(x) for x in (item.get("keys") or [])][-16:]
                _texts[sess] = [str(x) for x in (item.get("texts") or [])][-TEXT_KEEP:]
    except Exception:  # noqa: BLE001
        pass


def _save() -> None:
    try:
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "saved_at": time.time(),
            "sessions": {s: {"keys": _hist[s][-16:], "texts": _texts[s][-TEXT_KEEP:]}
                         for s in set(_hist) | set(_texts)},
        }
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except Exception:  # noqa: BLE001
        pass


def _norm(text: str) -> str:
    return re.sub(r"[\s（(）)]+$", "", (text or "").strip())


def _match(text: str) -> list[tuple[str, str, int]]:
    n = _norm(text)
    if not n or len(n) > 12:
        return []
    out = []
    for key, intent, rx, maxn in RULES:
        if rx.search(n):
            out.append((key, intent, maxn))
    return out


def _count(key: str, session: str) -> int:
    return sum(1 for k in _hist.get(session, []) if k == key)


def _record(key: str, session: str) -> None:
    h = _hist[session]
    h.append(key)
    del h[:-16]


def _remember(text: str, session: str) -> None:
    t = _texts[session]
    t.append(_norm(text))
    del t[:-TEXT_KEEP]


def _pool(intent: str, session: str, exclude: str = "") -> list[str]:
    recent = set(_texts.get(session, []))
    source = SAFE_VARIANTS.get(intent) or FALLBACK.get(intent, [])
    out, seen = [], set()
    for part in source:
        if not part or part in recent or part in seen:
            continue
        if exclude and _norm(part) == exclude:
            continue
        if any(b in part for b in BANNED_IN_VARIANT):
            continue
        seen.add(part)
        out.append(part)
    return out


def _pick(intent: str, session: str, seed: str, exclude: str = "") -> str:
    pool = _pool(intent, session, exclude)
    if not pool:
        return ""
    rnd = random.Random(int(hashlib.md5(seed.encode("utf-8")).hexdigest()[:12], 16))
    return rnd.choice(pool)


def apply_tic_budget(text: str, session_key: str = "default",
                     mode: str = "banter", record: bool = True) -> tuple[str, list[str]]:
    """返回 (可能被改写的文本, 动作)。record=False 只读窗口，不写状态。"""
    _ensure()
    if not text:
        return text, []
    hits = _match(text)
    if not hits:
        if record:
            _remember(text, session_key)
        return text, []

    key, intent, maxn = hits[0]
    if _count(key, session_key) < maxn:
        if record:
            for k, _, _ in hits:
                _record(k, session_key)
            _remember(text, session_key)
        return text, []

    seed = f"{session_key}|{text}|{_count(key, session_key)}|{len(_texts.get(session_key, []))}"
    new = _pick(intent, session_key, seed, exclude=_norm(text))
    if not new or new == text:
        if record:
            for k, _, _ in hits:
                _record(k, session_key)
            _remember(text, session_key)
        return text, []

    if record:
        _remember(new, session_key)
    return new, [f"口头禅预算：{key}→「{new}」"]


def commit_tic(text: str, session_key: str = "default") -> None:
    """发送确认后记一次：把这条里出现的口头禅都记进窗口。"""
    _ensure()
    if not text:
        return
    for key, _, _ in _match(text):
        _record(key, session_key)
    _remember(text, session_key)
    _save()


def reset(session_key: str | None = None) -> None:
    _ensure()
    if session_key is None:
        _hist.clear()
        _texts.clear()
    else:
        _hist.pop(session_key, None)
        _texts.pop(session_key, None)
    _save()
