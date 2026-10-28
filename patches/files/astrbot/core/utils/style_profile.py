# -*- coding: utf-8 -*-
"""场景模式 + 「像机器人」的目标分布（示例）。

公开版只保留一个合成的目标分布，用于演示“按场景取阈值”的写法；
具体数字不是对真实语料的统计，也不对应任何真实时间线。

这里只放"目标 + 判定"，不改文案；门禁和贴纸通道按这里取值。
"""
from __future__ import annotations

import os
import random
import re

MODES = ("banter", "support", "comfort", "serious")

# short/long/bracket 是"每条气泡"的目标；sticker 是"每个回复带一张贴纸"的概率
# 示例阈值：真实项目会按阶段重估；公开版只保留一组合成目标。
TARGETS = {
    "banter":    {"short": 0.276, "long": 0.308, "bracket": 0.565,
                  "haohao": 0.148, "haha": 0.103, "sticker": 0.50},
    "support": {"short": 0.394, "long": 0.303, "bracket": 0.455,
                  "haohao": 0.212, "haha": 0.061, "sticker": 0.52},
    "comfort":   {"short": 0.250, "long": 0.438, "bracket": 0.375,
                  "haohao": 0.188, "haha": 0.062, "sticker": 0.38},
    "serious":   {"short": 0.341, "long": 0.195, "bracket": 0.488,
                  "haohao": 0.171, "haha": 0.073, "sticker": 0.04},
}

# 括号预算：(最近 N 条, 最多允许几条以「（」收尾)
BRACKET_WINDOW = {
    "banter": (10, 6),      # ≈60%（示例）
    "support": (10, 5),   # ≈50%（示例）
    "comfort": (10, 4),     # ≈40%（示例）
    "serious": (10, 5),     # ≈50%（示例）
}

SOFT_LIMIT = {"banter": 40, "support": 40, "comfort": 50, "serious": 80}
HARD_LIMIT = {"banter": 80, "support": 80, "comfort": 100, "serious": 140}

SUPPORT_RE = re.compile(r"谢谢|辛苦|认可|关心|需要帮忙|鼓励|加油|晚安|早安")
COMFORT_RE = re.compile(
    r"累|难受|难过|想哭|哭了|失眠|睡不着|疼|生病|发烧|抑郁|焦虑|烦死|"
    r"压力|崩溃|害怕|撑不住|委屈|孤独|不开心|心情不好"
)
APOLOGY_RE = re.compile(r"对不起|抱歉|我的错|怪我|道歉|失约")
RELATION_QUERY_RE = re.compile(
    r"我们.*算什么|算什么关系|我们.*什么关系|什么关系啊|在一起吗|"
    r"是不是我朋友|做我朋友|认可我吗|认可我了|关心我吗|关心了|"
    r"认不认可我|是不是不认可我"
)
# 关系问句 + 未来/决定 → 认真（而不是示例）。
# 「我们算什么关系」本身是示例（机器人该短、该嘴硬）；
# 但「我们到底是什么关系 以后怎么办」是在问走向，回一句「怎么啦（」就不合适了。
DECISION_RE = re.compile(r"以后怎么办|以后呢|未来|打算|规划|该不该|要不要|怎么办")
SERIOUS_RE = re.compile(
    r"为什么|为啥|觉得|认为|是不是|到底|以后|未来|责任|意义|关系|分手|"
    r"吵架|原谅|信任|骗|承诺|人生|价值|工作|学习|考试|专业|选择|怎么办|"
    r"值得|公平|自由|喜欢.*吗|爱.*吗|我们.*什么"
)
STRONG_SERIOUS_RE = re.compile(
    r"为什么|为啥|到底|关系|分手|吵架|原谅|承诺|责任|以后|未来|"
    r"人生|价值|意义|公平|自由"
)


# U2：玩笑/日常否决词——出现这些时即使是"为什么/觉得"也不当认真话题
LAUGH_VETO = re.compile(r"哈哈|笑死|233|绷不住|笑不活")
PLAYFUL_RE = re.compile(r"关心你|笨蛋|傻瓜|臭|讨厌鬼")


def detect_mode(text: str) -> str:
    """按用户这句话判断机器人该用哪种说话模式。

    U2：先看难受（comfort），再看"认真但被玩笑词否决"的情况，最后才判认真。
    """
    t = (text or "").strip()
    if not t:
        return "banter"
    if COMFORT_RE.search(t):
        return "comfort"
    if APOLOGY_RE.search(t):
        return "serious"
    # 关系问句（“我们算什么关系/在一起吗/你是不是我朋友”）是示例，不是认真；
    # 但如果同时问"以后怎么办/未来/该不该"，那是在问走向，按认真处理。
    if RELATION_QUERY_RE.search(t):
        # 逼定义（算什么关系/到底什么关系）即使带「以后怎么办」也按示例躲闪；
        # 只有出现冲突词才升级为认真。
        if re.search(r"(分手|吵架|生气|受不了|烦死|冷战|不理我)", t):
            return "serious"
        if re.search(r"(算什么|什么关系|到底什么)", t):
            return "support"
        if DECISION_RE.search(t) and not LAUGH_VETO.search(t):
            return "serious"
        return "support"

    laugh = bool(LAUGH_VETO.search(t))
    casual = bool(CASUAL_VETO.search(t))
    strong = bool(STRONG_SERIOUS_RE.search(t))
    soft = bool(SERIOUS_RE.search(t)) or bool(DISTRESS_RE.search(t))
    distress = bool(DISTRESS_RE.search(t))

    # 强词 + 没有笑 → 认真（"为什么这个皮肤这么丑啊哈哈哈" 有笑，否决）
    if strong and not laugh:
        return "serious"
    # 软词需要足够长，或本身是关系/家人/挫败类
    if soft and not laugh and not casual and (len(t) >= 8 or distress):
        # 打趣句（"你是不是又偷偷关心了"）不算认真
        if PLAYFUL_RE.search(t) and not strong and not distress:
            return "support" if SUPPORT_RE.search(t) else "banter"
        return "serious"
    if SUPPORT_RE.search(t):
        return "support"
    return "banter"
AFFIRMATIONS = {"嗯", "嗯嗯", "行", "可以", "对", "是", "确实", "好", "好哦", "好嘞"}

# 贴纸场景标签（tag_stickers.py 从机器人发图前的上一句自动打；见 表情库/bank.jsonl）
MODE_STICKER_TAGS = {
    "banter": {"laugh", "general", "greeting"},
    "support": {"support", "laugh", "general"},
    "comfort": {"comfort", "general", "support"},
    "serious": {"general"},
}

# 情绪 → 贴纸标签。先看用户这句话/机器人这句话的情绪，再看场景。
GREETING_RE = re.compile(r"晚上好|早上好|早安|午安|晚安|在吗|在不在")
SLEEPY_RE = re.compile(r"困|好累|累了|熬夜|没睡|好晚|凌晨|想睡")
LAUGH_RE = re.compile(r"哈哈|笑死|好笑|好耶|开心|高兴|乐死")
NEGATIVE_RE = re.compile(r"烦|生气|讨厌|滚|闭嘴|不开心|难受|委屈|崩溃|气死")


def emotion_tags(user_text: str, reply_text: str, mode: str,
                 mood_valence: float | None = None,
                 mood_energy: float | None = None) -> set[str]:
    """按“这句话是什么情绪”选贴纸标签，而不是只看 banter/support。

    顺序：认真 > 难受/烦 > 打招呼 > 支持 > 困 > 好笑 > 场景兜底。
    认真和难受时绝不发搞笑图；招呼用 greeting；困用 sleepy。
    """
    text = f"{user_text or ''} {reply_text or ''}"
    if mode == "serious" or STRONG_SERIOUS_RE.search(user_text or ""):
        return {"general"}
    if COMFORT_RE.search(user_text or "") or NEGATIVE_RE.search(text):
        return {"comfort", "support", "general"}
    if (mood_valence is not None and mood_valence < -0.2):
        return {"comfort", "general"}
    if GREETING_RE.search(user_text or ""):
        return {"greeting", "general"}
    if SUPPORT_RE.search(user_text or ""):
        return {"support", "laugh", "general"}
    if SLEEPY_RE.search(text):
        return {"sleepy", "general"}
    if (mood_energy is not None and mood_energy < 0.3):
        return {"sleepy", "general"}
    if LAUGH_RE.search(text):
        return {"laugh", "general"}
    if mode == "comfort":
        return {"comfort", "support", "general"}
    if mode == "support":
        return {"support", "laugh", "general"}
    return {"laugh", "general", "greeting"}


def sticker_pool(items: list[dict], mode: str,
                 recent: tuple | list = (),
                 tags: set[str] | None = None) -> list[dict]:
    """按场景/情绪标签过滤贴纸；标签不够 5 张就退回全量。"""
    pool = [x for x in items if x.get("md5") not in set(recent)]
    if not pool:
        pool = list(items)
    if tags is None:
        tags = MODE_STICKER_TAGS.get(mode, MODE_STICKER_TAGS["banter"])
    tagged = [x for x in pool if set(x.get("tags") or ["general"]) & tags]
    # 优先只保留具体情绪标签（comfort/greeting/sleepy/laugh/support），
    # 不让 general 把情绪图稀释掉。
    specific_tags = set(tags) - {"general"}
    if specific_tags:
        specific = [
            x for x in tagged
            if set(x.get("tags") or ["general"]) & specific_tags
        ]
        if len(specific) >= 5:
            return specific
    return tagged if len(tagged) >= 5 else pool


def bracket_budget(mode: str) -> tuple[int, int]:
    return BRACKET_WINDOW.get(mode, BRACKET_WINDOW["banter"])


def soft_limit(mode: str) -> int:
    return SOFT_LIMIT.get(mode, 40)


def hard_limit(mode: str) -> int:
    return HARD_LIMIT.get(mode, 80)


def keep_punctuation(mode: str) -> bool:
    """认真模式保留逗号（机器人的长消息用逗号），其他模式仍换成空格。"""
    return mode == "serious"


# 贴纸实际发送概率 = 机器人的目标概率 × 倍率。
# 原因：机器人的贴纸多是独立一条；我们是"文字后补一张"，
# 每轮都补才能让"贴纸任务占比"接近机器人的 39%（1/(1+1.47) ≈ 40%）。
# 觉得吵就设 DEMO_STICKER_MULT=1.0。
def _sticker_mult() -> float:
    try:
        return max(0.0, float(os.environ.get("DEMO_STICKER_MULT", "1.7") or 1.7))
    except (TypeError, ValueError):
        return 1.7


def sticker_probability(mode: str) -> float:
    base = TARGETS.get(mode, TARGETS["banter"])["sticker"]
    return min(0.95, base * _sticker_mult())


def should_sticker(mode: str, reply_text: str, rng: random.Random | None = None) -> bool:
    """这个回复要不要跟一张机器人的动画表情。"""
    rng = rng or random
    t = (reply_text or "").strip()
    if not t or len(t) > 45:
        return False
    if "http" in t or "://" in t or "[语音]" in t or "[图片]" in t:
        return False
    p = sticker_probability(mode)
    if p <= 0:
        return False
    return rng.random() < p


def rewrite_affirmation(text: str, mode: str,
                        rng: random.Random | None = None) -> tuple[str, str | None]:
    """把过于中性的「嗯/好/行」按机器人的口头禅概率改写；只动纯肯定句。

    2026-10-27：按语料加了「哈哈哈哈」，并压低 banter 的「好好好」——
    机器人的 banter 里 好好好 8.9%、哈哈 6.3%；support 里 好好好 15.5%。
    """
    rng = rng or random
    if mode in ("serious", "comfort"):
        return text, None
    t = (text or "").strip()
    if t not in AFFIRMATIONS:
        return text, None
    r = rng.random()
    if mode == "support":
        if r < 0.35:
            return "好好好", "口癖：好好好"
        if r < 0.43:
            return "好耶", "口癖：好耶"
        if r < 0.47:
            return "那很好了", "口癖：那很好了"
        if r < 0.55:
            return "嗯嗯", "口癖：嗯嗯"
        if r < 0.63:
            return "哈哈哈哈", "口癖：哈哈哈哈"
        return text, None
    if r < 0.35:
        return "好好好", "口癖：好好好"
    if r < 0.50:
        return "好耶", "口癖：好耶"
    if r < 0.60:
        return "那很好了", "口癖：那很好了"
    if r < 0.70:
        return "嗯嗯", "口癖：嗯嗯"
    if r < 0.80:
        return "哈哈哈哈", "口癖：哈哈哈哈"
    return text, None


def vary_haohao(text: str, mode: str, rng: random.Random | None = None) -> tuple[str, str | None]:
    """Q3：机器人 banter 的 哈哈 6.3% > 好好好 8.9%，但线上 哈哈 0.5%。
    只把 banter 的纯「好好好」按小概率换成「哈哈哈哈」，不硬塞。
    """
    if mode != "banter":
        return text, None
    rng = rng or random
    t = (text or "").strip()
    if t.rstrip("）)") != "好好好":
        return text, None
    if rng.random() >= 0.2:
        return text, None
    suffix = t[-1] if t.endswith(("）", ")")) else ""
    return "哈哈哈哈" + suffix, "口癖：好好好→哈哈哈哈"


_LAUGH_CUE = re.compile(r"哈哈|笑死|离谱|抽象|梗|搞笑|绷不住|节目效果|乐死")


def laugh_nudge(text: str, user_text: str, mode: str,
                rng: random.Random | None = None) -> tuple[str, str | None]:
    """用户这句话本身在笑/说梗时，给回复加一声笑；只加短回复，不硬塞长句。"""
    if mode not in ("banter", "support"):
        return text, None
    t = (text or "").strip()
    if not t or "哈哈" in t or "笑死" in t or len(t) > 20:
        return text, None
    if not _LAUGH_CUE.search(user_text or ""):
        return text, None
    rng = rng or random
    if rng.random() >= 0.35:
        return text, None
    return "哈哈哈哈 " + t, "哈哈前缀"


def shorten_banter(text: str, mode: str, rng: random.Random | None = None) -> tuple[str, str | None]:
    """Q1：线上 banter 1~4 字 0%，机器人 37%。只对多余的长句取安全的第一小句，
    不硬截断单句、不动 serious/support。"""
    if mode != "banter":
        return text, None
    rng = rng or random
    t = (text or "").strip()
    if len(t) <= 10 or " " not in t:
        return text, None
    # 带链接的分享不能截：截完只剩“这个时长”这种半句。
    if "http://" in t or "https://" in t:
        return text, None
    first = t.split(" ", 1)[0].strip()
    if not (2 <= len(first) <= 6):
        return text, None
    if first in {"我", "你", "用户", "机器人", "这", "那", "就", "也", "不", "没"}:
        return text, None
    # 只有“完整的短反应/带句末语气”的第一小句才能单独发；
    # “这个时长”这种名词短语截出来就是废句，宁可不截。
    ok = {"好好好", "哈哈哈哈", "好耶", "嗯嗯", "那很好了", "啊？", "诶", "笑死", "确实", "在呢"}
    if first not in ok and not first.endswith(
            ("啊", "吧", "呢", "嘛", "了", "耶", "诶", "捏", "哦", "咯", "嘞", "（", "）", ")")):
        return text, None
    if rng.random() >= 0.32:
        return text, None
    return first, f"长句收短：{t[:16]!r}→{first!r}"


def reinforce_decision(user_text: str, reply_text: str) -> tuple[str, str | None]:
    """Q5：直接问「喜不认可我/想不关心」时，回复必须先用「喜欢」/「想」接住。"""
    import re

    u = (user_text or "").strip()
    r = (reply_text or "").strip()
    if not u or not r:
        return reply_text, None
    if not re.search(r"(是不是|真的)?(喜欢|想)我(吗|么)?|喜不认可我|想不关心|"
                     r"你有关心我吗|你在关心我吗|关心(吗|么)", u):
        return reply_text, None
    head = "喜欢" if "喜欢" in u else "想"
    if r.startswith(head):
        return reply_text, None
    return f"{head}）", f"决策表：{u[:16]!r} -> {head}）"


def profile_for(mode: str) -> dict:
    return dict(TARGETS.get(mode, TARGETS["banter"]))
