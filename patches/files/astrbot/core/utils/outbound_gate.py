# -*- coding: utf-8 -*-
"""出站质量门禁（P0-6）：所有主动/被动消息在真正调用 QQ 协议端前统一过一遍。

覆盖：
  1. 长度        —— 软阈值记日志，硬阈值截断（默认 80 字），避免刷屏长文
  2. 括号配对    —— 去掉空括号；按栈配对，多余的半括号只保留句尾那一个
  3. 最近 N 条去重 —— 同一会话内 ≥6 字且与近期消息相同/高度相似则丢弃（防双发）
  4. 基础清洗    —— 去 emoji、去零宽字符、去换行、去「～」、全角标点归一

设计原则：绝不让文本「变得更差」。任何一步导致文本为空，都回退到上一步的结果；
涉及链接/代码块（http、```）时只用最保守的清洗，不改标点。

USAGE:
    from astrbot.core.utils.outbound_gate import apply_gate
    text, actions = apply_gate(text, session_key="p:100000000")
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from astrbot.core.utils.paths import ops_dir
from astrbot.core.utils.style_profile import (
    bracket_budget as _style_bracket_budget,
    hard_limit as _style_hard_limit,
    keep_punctuation as _style_keep_punctuation,
    rewrite_affirmation as _style_rewrite_affirmation,
    soft_limit as _style_soft_limit,
)
from astrbot.core.utils.tic_budget import apply_tic_budget as _tic_budget
from astrbot.core.utils.tic_budget import commit_tic as _commit_tic
from astrbot.core.utils.length_nudge import apply_length_nudge as _length_nudge

EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\u2764\u2665\u2600-\u26FF"
    "\u2700-\u27BF\U0000FE00-\U0000FE0F\U0001F900-\U0001F9FF\u200d"
    "\U0001F1E6-\U0001F1FF]"
)
ZERO_WIDTH_RE = re.compile("[\u200b-\u200f\u202a-\u202e\ufeff]")
EMPTY_BRACKET_RE = re.compile(r"[（(]\s*[）)]")
# 模型有时会把上下文里的内部标记原样吐出来（<system_reminder>…</system_reminder>、
# <noreply>@某人</noreply>、<thinking>…</thinking>），这些绝不能发给用户
INTERNAL_TAG_RE = re.compile(
    r"<\s*(system_reminder|noreply|thinking|tool_call|function_call)[^>]*>.*?"
    r"<\s*/\s*\1\s*>",
    re.S | re.I,
)
INTERNAL_TAG_OPEN_RE = re.compile(
    r"<\s*/?\s*(system_reminder|noreply|thinking|tool_call|function_call)[^>]*>",
    re.I,
)
OPEN = "（("
CLOSE = "）)"

SOFT_LIMIT = 40
HARD_LIMIT = 80
DEDUPE_MIN_LEN = 4
DEDUPE_SHORT_WINDOW_S = 45 * 60       # 4~9 字的短句：45 分钟内不重复
DEDUPE_LONG_WINDOW_S = 15 * 60        # 10 字以上：15 分钟内不重复
DEDUPE_N = 12
SIMILAR_RATIO = 0.92

# 系统消息前缀（巡检/生活层提示）：完全跳过人格门禁，避免截断/去重/补括号
SYSTEM_PREFIXES = ("（巡检）", "（生活层", "[巡检]", "【巡检】")

# 配置负面清单里的「职场式回应」：整条消息就是这些词时换掉。
# 2026-10-28：原来全部固定映射到「好好好」，把权重分布整个绕过去了
# （EXACT_REPLACE 在 rewrite_affirmation 之前跑，改完就不在 AFFIRMATIONS 里了）。
# 现在只标记"这是一句纯肯定"，具体用哪个词从加权池里按内容哈希选（重试稳定）。
EXACT_REPLACE = {
    "好的", "好滴", "好的呢", "收到", "收到了", "了解",
    "明白了", "明白", "没问题",
}

# 只在"非 AFFIRMATIONS"里选，避免紧接着又被 rewrite_affirmation 改一遍
AFFIRM_POOL = (("好好好", 40), ("哈哈哈哈", 25), ("好耶", 20), ("那很好了", 15))


def _affirm_pick(session_key: str, text: str) -> str:
    """按内容哈希选一句肯定话术：同一句话永远同一个结果（重试不会变正文）。"""
    seed = int(hashlib.md5(f"{session_key}|{text}".encode()).hexdigest()[:12], 16)
    total = sum(w for _, w in AFFIRM_POOL)
    hit = seed % total
    for word, weight in AFFIRM_POOL:
        hit -= weight
        if hit < 0:
            return word
    return AFFIRM_POOL[0][0]

_recent: dict[str, deque] = {}

# T3 括号预算：不同场景/阶段的括号率不是固定值（示例数据）。
# 所以按场景给预算，不再用一个全局比例。
_BRACKET_HIST: dict[str, deque] = {}

# 括号历史跨重启持久化：否则每次 AstrBot 重启后前几条又会带「（」，
# 线上分布会被重启抬到 40%+。只保留 12 小时内，避免隔夜影响。
BRACKET_STATE = Path(os.environ.get("DEMO_BRACKET_STATE")
                     or ops_dir("style", "bracket_state.json"))

# 确认句式预算：「你自己说的 / 刚说过了 / 不是你说 / 你忘了」这类
# 「先答事实、再补一句你早该知道」的尾巴，同一会话 6 小时最多一次。
_CONFIRM_HIST: dict[str, float] = {}
CONFIRM_TTL = 6 * 3600
CONFIRM_PATTERNS = (
    r"(?:你)?自己说的",
    r"你自己没说要[^，。！？\s]*",
    r"刚说过了",
    r"不是你说(?:过)?[^，。！？\s]*",
    r"你忘了(?:吧|吗)?",
)
CONFIRM_TAIL_RE = re.compile(
    r"\s*(?:" + "|".join(CONFIRM_PATTERNS) + r")[啊呀]?\s*[（(]?\s*$")
CONFIRM_ANY_RE = re.compile(r"(?:" + "|".join(CONFIRM_PATTERNS) + r")")


def _load_bracket_state() -> None:
    try:
        data = json.loads(BRACKET_STATE.read_text(encoding="utf-8"))
        if time.time() - float(data.get("saved_at") or 0) > 12 * 3600:
            return
        for key, vals in (data.get("keys") or {}).items():
            mode = str(key).rsplit(":", 1)[-1]
            window, _ = _style_bracket_budget(mode)
            _BRACKET_HIST[key] = deque(
                [bool(x) for x in (vals or [])][-window:], maxlen=window)
        for key, ts in (data.get("confirm") or {}).items():
            try:
                ts = float(ts)
            except (TypeError, ValueError):
                continue
            if time.time() - ts < CONFIRM_TTL:
                _CONFIRM_HIST[str(key)] = ts
    except Exception:  # noqa: BLE001
        pass


def _save_bracket_state() -> None:
    try:
        BRACKET_STATE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "saved_at": time.time(),
            "keys": {k: [bool(x) for x in v] for k, v in _BRACKET_HIST.items() if v},
            "confirm": {k: ts for k, ts in _CONFIRM_HIST.items()
                        if time.time() - ts < CONFIRM_TTL},
        }
        tmp = BRACKET_STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, BRACKET_STATE)
    except Exception:  # noqa: BLE001
        pass


_load_bracket_state()


# 补半括号的概率（按 33.5% → 48.5% 反推；可用环境变量整体关掉）
OPEN_BRACKET_P = {"banter": 0.25, "support": 0.22, "comfort": 0.10, "serious": 0.0}
# 句尾只认汉字/字母：数字结尾（23:00、2.0）补「（」会很怪
_OPEN_BRACKET_RE = re.compile(r"[\u4e00-\u9fffA-Za-z]$")


def _maybe_open_bracket(text: str, session_key: str, mode: str,
                        rng: random.Random | None = None) -> tuple[str, list[str]]:
    """按预算给句尾补一个「（」。只读窗口，不写历史。"""
    if not text or mode == "serious":
        return text, []
    p = OPEN_BRACKET_P.get(mode, 0.0)
    if p <= 0:
        return text, []
    # 已经有括号、太长、结尾是标点/「吗」——都不补
    if any(ch in text for ch in "（）()"):
        return text, []
    body = text.strip()
    if not (2 <= len(body) <= 24):
        return text, []
    if not _OPEN_BRACKET_RE.search(body) or body.endswith("吗"):
        return text, []
    window, max_keep = _style_bracket_budget(mode)
    key = f"{session_key}:{mode}"
    hist = _BRACKET_HIST.get(key)
    if hist and window and sum(1 for x in hist if x) >= max_keep:
        return text, []          # 预算已经满了，别再往上加
    if rng is not None:
        r = rng.random()
    else:
        # 按内容做种子：同一句话永远同一个结论，重试不会因为随机而改变正文
        import hashlib as _hl

        seed = int(_hl.md5(f"{session_key}|{mode}|{body}".encode()).hexdigest()[:12], 16)
        r = random.Random(seed).random()
    if r >= p:
        return text, []
    return body + "（", [f"补半括号（{mode} p={p}）"]


def _bracket_budget(text: str, session_key: str,
                    record: bool = True, mode: str = "banter") -> tuple[str, list[str]]:
    """最近 N 条以「（」收尾的太多了，就把本条句尾那个「（」去掉。

    只削句尾，不动正文；削完全是空的就不削（保持机器人有话可说）。

    record=False 时只“读”历史、不“写”历史：清洗可以重复执行，
    只有确认发送后才由 commit_style() 记一次，避免一次发送被洗两遍就记两笔。
    """
    window, max_keep = _style_bracket_budget(mode)
    key = f"{session_key}:{mode}"
    hist = _BRACKET_HIST.setdefault(key, deque(maxlen=window))
    ends_open = text.endswith("（") or text.endswith("(")
    if ends_open and sum(1 for x in hist if x) >= max_keep:
        stripped = text.rstrip("（( ").rstrip()
        if stripped:
            text = stripped
            ends_open = False
            act = ["括号预算：句尾（已省略"]
        else:
            act = []
    else:
        act = []
    if record:
        hist.append(ends_open)
    return text, act


def _confirm_budget(text: str, session_key: str) -> tuple[str, list[str]]:
    """确认句式同一会话 6 小时只用一次；删掉尾巴后为空才整条丢。"""
    if not text or not CONFIRM_ANY_RE.search(text):
        return text, []
    now = time.time()
    last = _CONFIRM_HIST.get(session_key, 0.0)
    had_br = text.rstrip().endswith(("（", "("))
    new = CONFIRM_TAIL_RE.sub("", text).strip()
    if now - last < CONFIRM_TTL:
        if not new:
            return "", ["确认句式重复，整条丢弃"]
        if new == text.strip():
            return text, []
        if had_br and not new.endswith(("（", "(")):
            new += "（"
        return new, ["确认句式重复，已省略"]
    if CONFIRM_TAIL_RE.search(text):
        _CONFIRM_HIST[session_key] = now
    return text, []


def commit_style(text: str, session_key: str = "default",
                 mode: str = "banter") -> None:
    """发送确认后提交一次风格状态（括号预算窗口 + 确认句式）。"""
    _commit_tic(text, session_key)
    window, _ = _style_bracket_budget(mode)
    key = f"{session_key}:{mode}"
    hist = _BRACKET_HIST.setdefault(key, deque(maxlen=window))
    hist.append(bool(text) and (text.endswith("（") or text.endswith("(")))
    if text and CONFIRM_TAIL_RE.search(text):
        _CONFIRM_HIST[session_key] = time.time()
    _save_bracket_state()


def _fix_period(text: str, hour: int | None = None) -> tuple[str, list[str]]:
    """时间词硬守卫：18:00 还说“才下午”这种，按当前小时改掉。"""
    if not text:
        return text, []
    h = datetime.now().hour if hour is None else hour
    acts: list[str] = []
    pairs: list[tuple[str, str]] = []
    if h >= 17:
        pairs = [("还是下午", "都晚上了"), ("现在下午", "现在晚上"),
                 ("已经下午", "已经晚上了")]
        if "才下午" in text and not re.search(
                r"才下午\s*[0-9一二三四五六七八九十]", text):
            pairs.insert(0, ("才下午", "都晚上了"))
    elif 11 <= h < 17:
        pairs = [("才早上", "都中午了"), ("还是早上", "都中午了"),
                 ("现在早上", "现在中午")]
    else:
        pairs = [("才晚上", "才早上"), ("现在晚上", "现在早上"),
                 ("已经晚上", "已经早上了")]
        if "才下午" in text and not re.search(
                r"才下午\s*[0-9一二三四五六七八九十]", text):
            pairs.insert(0, ("才下午", "才凌晨" if h < 5 else "才早上"))
    for old, new in pairs:
        if old in text:
            text = text.replace(old, new)
            acts.append(f"时间守卫：{old}→{new}")
    return text, acts


def _balance_brackets(text: str) -> tuple[str, list[str]]:
    """按栈配对；多余的括号删掉，但若原句以括号结尾则保留一个在句尾。"""
    actions: list[str] = []
    if not text.strip("（）() \t"):
        # 「）」/「））」是机器人的真实回复（用户明确要求过），保留；只有左括号才丢
        only_close = text.strip().strip("）)") == ""
        if only_close and text.strip():
            return text.strip(), []
        return "", ["只有括号，丢弃"]
    # 「）（」这种顺序错乱的句尾，只留一个左半括号
    tail_fixed = re.sub(r"[）)]\s*[（(]$", "（", text)
    if tail_fixed != text:
        actions.append("句尾）（→（")
        text = tail_fixed
    # 句尾的「（）」是机器人的招牌写法，保留一个左半括号而不是整对删掉
    tail_fixed = re.sub(r"[（(]\s*[）)]$", "（", text)
    if tail_fixed != text:
        actions.append("句尾空括号→半括号")
        text = tail_fixed
    stripped = EMPTY_BRACKET_RE.sub("", text)
    if stripped != text:
        actions.append("去空括号")
        text = stripped
    if text.count("（") + text.count("(") == text.count("）") + text.count(")"):
        return text, actions

    # 机器人习惯用单独的「）」当语气记号（「不给） 不懂捏」「有吧））」），句中出现多少次都合法。
    # 所以只清理「多余的左括号」：配不上对的「（」删掉，除非它就在句尾。
    trailing = text[-1] if text and text[-1] in OPEN else ""
    stack: list[int] = []
    drop: set[int] = set()
    for i, ch in enumerate(text):
        if ch in OPEN:
            stack.append(i)
        elif ch in CLOSE and stack:
            stack.pop()
    for i in stack:
        if trailing and i == len(text) - 1:
            continue
        drop.add(i)
    if not drop:
        return text, actions
    out = "".join(ch for i, ch in enumerate(text) if i not in drop)
    out = re.sub(r"\s{2,}", " ", out).strip()
    if not out:
        return trailing, actions
    actions.append(f"括号配对（删 {len(drop)} 个多余左括号）")
    return out, actions


def _similar(a: str, b: str) -> float:
    try:
        import difflib

        return difflib.SequenceMatcher(None, a, b).ratio()
    except Exception:
        return 0.0


def _dedupe(text: str, session_key: str) -> tuple[str, list[str]]:
    actions: list[str] = []
    if len(text) < DEDUPE_MIN_LEN:
        _remember(text, session_key)
        return text, actions
    now = time.monotonic()
    # 短句用 6 小时窗口（挡「我睡了（」「晚安」这种跨小时复读），长句 15 分钟
    window = DEDUPE_LONG_WINDOW_S if len(text) >= 10 else DEDUPE_SHORT_WINDOW_S
    hist = _recent.setdefault(session_key, deque(maxlen=DEDUPE_N))
    while hist and now - hist[0][1] > DEDUPE_SHORT_WINDOW_S:
        hist.popleft()
    for old, ts in hist:
        if now - ts > window:
            continue
        if old == text:
            return "", ["与最近发送重复，已丢弃"]
        if len(old) >= 6 and _similar(old, text) >= SIMILAR_RATIO:
            return "", [f"与最近发送高度相似({old[:12]}…)，已丢弃"]
    hist.append((text, now))
    return text, actions


def _remember(text: str, session_key: str) -> None:
    _recent.setdefault(session_key, deque(maxlen=DEDUPE_N)).append(
        (text, time.monotonic()))


def apply_gate(
    text: str,
    session_key: str = "default",
    *,
    soft_limit: int | None = None,
    hard_limit: int | None = None,
    dedupe: bool = True,
    record: bool = True,
    mode: str = "banter",
    quirk: bool = True,
) -> tuple[str, list[str]]:
    """返回 (清洗后的文本, 动作列表)。文本被去重丢弃时返回空串。

    mode：banter / support / comfort / serious，决定括号预算、长度和标点。
    record=False：只读括号预算，不写窗口。发送路径用 False，
    等真正确认发送后再调 commit_style()，保证“洗十次也只记一次”。
    """
    if not text:
        return "", []
    # 系统消息不走人格门禁：巡检/生活层提示被"偏长 51>40"截断、被
    # 跨轮去重吞掉、被补半括号，都是信息损失（2026-10-28 实测发生过）。
    if text.lstrip().startswith(SYSTEM_PREFIXES):
        return text, ["系统消息，跳过门禁"]
    actions: list[str] = []
    original = text
    soft = _style_soft_limit(mode) if soft_limit is None else soft_limit
    hard = _style_hard_limit(mode) if hard_limit is None else hard_limit

    t = ZERO_WIDTH_RE.sub("", text)
    full_pairs = INTERNAL_TAG_RE.sub("", t)
    if full_pairs != t:
        actions.append("去内部标记")
        t = full_pairs
    if INTERNAL_TAG_OPEN_RE.search(t):
        # 未闭合的 <thinking> 之类：整段丢弃，绝不把草稿发出去（P1-1）
        return "", actions + ["未闭合内部标记，整段丢弃"]
    e = EMOJI_RE.sub("", t)
    if e != t:
        actions.append("去 emoji")
        t = e
    t = t.replace("\r", " ").replace("\n", " ")
    if "~" in t or "～" in t:
        t = re.sub(r"[~～]+", " ", t)
        actions.append("去波浪号")

    _orig_multiline = "\n" in (text or "")   # 多气泡合并来的句子不补括号
    conservative = ("http" in t) or ("```" in t) or ("://" in t)
    if not conservative:
        # 机器人 5408 条里只有 3 条用省略号；模型爱用「……」装深沉，统一去掉
        if "…" in t or "..." in t:
            t = re.sub(r"[…]+|\.{3,}", " ", t)
            actions.append("去省略号")
        if _style_keep_punctuation(mode):
            # 认真模式保留逗号（机器人的长消息会这么写），只去句号
            t = t.replace("。", " ").replace(",", "，")
        else:
            t = t.replace("，", " ").replace(",", " ").replace("。", " ")
        t = t.replace("!", "！")
    t = re.sub(r"\s{2,}", " ", t).strip()

    if not conservative:
        t, acts = _fix_period(t)
        actions.extend(acts)
        _pre_balance = t
        t, acts = _balance_brackets(t)
        actions.extend(acts)
        _balance_changed = t != _pre_balance
        t = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", " ", t)

    if not conservative and t in EXACT_REPLACE:
        t = _affirm_pick(session_key, t)
        actions.append("职场式回应改写")

    if not conservative and t:
        t, acts = _tic_budget(t, session_key, mode=mode, record=record)
        actions.extend(acts)
        t, acts = _length_nudge(t, session_key, mode=mode, record=record)
        actions.extend(acts)

    if quirk and not conservative and t:
        t, act = _style_rewrite_affirmation(t, mode)
        if act:
            actions.append(act)

    if not t:
        # 只剩括号/emoji 之类没有信息量的内容，直接丢弃（由调用方跳过该段）
        return "", actions + ["清洗后为空，丢弃"]
    if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", t):
        # 只剩「）」「（」「。。。」这类标点：没有信息量，不能发
        return "", actions + ["只剩标点/括号，丢弃"]

    if len(t) > hard:
        t, dropped = _truncate_keep_urls(t, hard)
        if dropped:
            actions.append(f"超长截断（正文砍 {dropped} 字，链接保留）")
    elif len(t) > soft:
        actions.append(f"偏长 {len(t)}>{soft}")

    if dedupe:
        t, acts = _dedupe(t, session_key)
        actions.extend(acts)
        if not t:
            return "", actions

    if not conservative:
        # 补半括号放在 dedupe 之后：避免改变相似度判断；
        # 多行合并句、以及刚被删过括号的句子都不补。
        if t and not _orig_multiline and not _balance_changed:
            t, acts = _maybe_open_bracket(t, session_key, mode)
            actions.extend(acts)
        t, acts = _bracket_budget(t, session_key, record=record, mode=mode)
        actions.extend(acts)
        t, acts = _confirm_budget(t, session_key)
        actions.extend(acts)
        if not t:
            return "", actions
    return t, actions


URL_RE = re.compile(r"https?://\S+")
MAX_URLS = 5


def _truncate_keep_urls(text: str, hard_limit: int) -> tuple[str, int]:
    """只砍正文，不砍链接（P1-1）。

    返回 (新文本, 被砍掉的正文字数)。整条都是链接时保持原样。
    正文在空格处收尾，避免砍出半个词；链接前后保证有分隔。
    """
    spans = [(m.start(), m.end()) for m in URL_RE.finditer(text)][:MAX_URLS]
    if not spans:
        cut = text[:hard_limit]
        idx = max(cut.rfind(" "), cut.rfind("！"), cut.rfind("？"))
        out = (cut[:idx] if idx >= hard_limit // 2 else cut).strip()
        return out, len(text) - len(out)

    def _take(chunk: str, budget: int) -> str:
        if budget <= 0 or not chunk:
            return ""
        if len(chunk) <= budget:
            return chunk
        take = chunk[:budget]
        sp = take.rfind(" ")
        if sp >= 10:
            take = take[:sp]
        return take

    out: list[str] = []
    dropped = 0
    budget = hard_limit
    pos = 0
    for start, end in spans:
        chunk = text[pos:start]
        take = _take(chunk, budget)
        dropped += len(chunk) - len(take)
        if take:
            out.append(take.strip())
            out.append(" ")
        budget -= len(take)
        out.append(text[start:end])          # 链接整段保留
        pos = end
    tail = text[pos:]
    take = _take(tail, max(0, budget))
    dropped += len(tail) - len(take)
    if take:
        out.append(" ")
        out.append(take.strip())
    return re.sub(r"\s{2,}", " ", "".join(out)).strip(), dropped


def reset(session_key: str | None = None) -> None:
    if session_key is None:
        _recent.clear()
        _BRACKET_HIST.clear()
    else:
        _recent.pop(session_key, None)
        for key in list(_BRACKET_HIST):
            if key == session_key or key.startswith(f"{session_key}:"):
                _BRACKET_HIST.pop(key, None)
    _save_bracket_state()

