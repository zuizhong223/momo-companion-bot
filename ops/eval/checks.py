# -*- coding: utf-8 -*-
"""Bot回复风格规则评分器（确定性，无 LLM 评审）。

规则全部来自配置文本（personas.system_prompt）里可量化的条目：
  【精确长度与词汇】约四成 1~4 字、六成 ≤6 字、八成 ≤10 字；越短越好
  【标点与断句】句中断句用空格，不用「，」；绝不用「～」和「。」
  【说话风格】句尾偶尔「（」或「）」，不配对的半括号只出现在句尾
  【负面清单】不说 收到/了解/好滴/没问题/好的/明白，不用「哈哈~」
  【底线】不说自己是 AI / 模型 / 机器人
"""
import re

# 与 aiocqhttp_message_event.send_message 里的清洗正则保持一致
EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\u2764\u2665\u2600-\u26FF"
    "\u2700-\u27BF\U0000FE00-\U0000FE0F\U0001F900-\U0001F9FF\u200d"
    "\U0001F1E6-\U0001F1FF]"
)

AI_TALK_RE = re.compile(
    r"作为(一个)?(AI|人工智能|语言模型|助手)|我是(AI|人工智能|语言模型|机器人|助手)"
    r"|语言模型|大模型|我理解你的感受|希望(这些|以上)|建议你|首先|其次|总之|综上"
    r"|抱歉|很遗憾|值得注意的是|需要注意的是|总结一下"
)

# 职场式 / 腻乎式回应：配置明确「从不说」
BANNED_TONE = (
    "收到", "了解", "好滴", "没问题", "好的", "明白", "遵命", "已阅",
    "加油", "加油", "亲爱的", "朋友", "Bot",
)

# 名字开头：配置「消息开头不要带称呼」
NAME_PREFIX = ("用户", "主人", "用户", "亲爱的")

CH_PUNCT_FORBIDDEN = "。～~!，,"


def normalize(text: str) -> str:
    """与出站清洗一致：去 emoji、换行变空格、压缩空白。"""
    t = EMOJI_RE.sub("", text or "")
    t = t.replace("\r", " ").replace("\n", " ")
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip()


def _bracket_check(t: str) -> tuple[bool, str]:
    """机器人的写法：句尾一个「（」或任意数量的「）」（「不给）」「有吧））」）。

    所以：单独的「）」一律合法；只有「多余的左括号不在句尾」和空括号才算错。
    """
    if re.search(r"（\s*）|\(\s*\)", t):
        return False, "空括号"
    if "（" not in t:
        return True, ""
    stack: list[int] = []
    for i, ch in enumerate(t):
        if ch in "（(":
            stack.append(i)
        elif ch in "）)" and stack:
            stack.pop()
    for i in stack:
        if i != len(t) - 1:
            return False, "多余的（不在句尾"
    return True, ""


def _lcs_len(a: str, b: str) -> int:
    """最长公共子串长度（a、b 都很短，暴力即可）。"""
    if not a or not b:
        return 0
    best = 0
    prev = [0] * (len(b) + 1)
    for ch in a:
        cur = [0] * (len(b) + 1)
        for j, bch in enumerate(b, 1):
            if ch == bch:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


def score(reply: str, case: dict) -> dict:
    """返回 {ok, hard_fails[], soft_notes[], text, length}。"""
    raw = reply or ""
    t = normalize(raw)
    hard: list[str] = []
    soft: list[str] = []

    max_len = int(case.get("max_len", 30))
    n = len(t)
    if n == 0:
        hard.append("空回复")
    elif n > max_len:
        hard.append(f"过长 {n}>{max_len}")

    for ch in CH_PUNCT_FORBIDDEN:
        if ch in t:
            hard.append(f"用了禁用标点「{ch}」")

    if EMOJI_RE.search(raw):
        hard.append("含 emoji")

    ok, why = _bracket_check(t)
    if not ok:
        hard.append(why)

    m = AI_TALK_RE.search(t)
    if m:
        hard.append(f"AI 腔「{m.group(0)}」")

    for w in BANNED_TONE:
        # 中文没有词边界，用「前后不是汉字」近似，避免把「好好的」判成「好的」
        pattern = rf"(?<![\u4e00-\u9fff]){re.escape(w)}(?![\u4e00-\u9fff])"
        if re.search(pattern, t):
            hard.append(f"负面清单用词「{w}」")
            break

    for w in NAME_PREFIX:
        if t.startswith(w):
            hard.append(f"开头带称呼「{w}」")
            break

    for w in case.get("forbid", []) or []:
        if w in t:
            hard.append(f"用例禁用「{w}」")

    must_any = case.get("must_any") or []
    if must_any and not any(w in t for w in must_any):
        hard.append("未命中期望关键词之一：" + "/".join(must_any))

    src = (case.get("input") or "").strip()
    if src:
        if t == src:
            hard.append("原样复读输入")
        elif len(src) >= 6 and src in t:
            hard.append("整句复读输入")
        elif len(src) >= 8 and _lcs_len(t, src) >= 12:
            hard.append("与输入高度重合")

    if t.count("？") + t.count("?") > 1:
        soft.append("连续反问")
    if n > 8 and " " not in t:
        soft.append("长句未用空格断句")
    if "\n" in raw:
        soft.append("输出含换行（出站会替换成空格）")

    return {
        "ok": not hard,
        "hard_fails": hard,
        "soft_notes": soft,
        "text": t,
        "length": n,
    }


def bucket(length: int) -> str:
    if length <= 4:
        return "1-4"
    if length <= 6:
        return "5-6"
    if length <= 10:
        return "7-10"
    if length <= 16:
        return "11-16"
    return "17+"


def summarize(results: list[dict]) -> dict:
    total = len(results)
    passed = sum(1 for r in results if r["ok"])
    buckets = {}
    for r in results:
        b = bucket(r["length"])
        buckets[b] = buckets.get(b, 0) + 1
    fails = {}
    for r in results:
        for f in r["hard_fails"]:
            key = re.sub(r"[\d]+", "N", f.split("「")[0].split(" ")[0])
            fails[key] = fails.get(key, 0) + 1
    by_cat = {}
    for r in results:
        cat = r["cat"]
        d = by_cat.setdefault(cat, {"n": 0, "pass": 0})
        d["n"] += 1
        d["pass"] += 1 if r["ok"] else 0
    lens = sorted(r["length"] for r in results)
    n_all = total or 1
    le4 = sum(1 for x in lens if x <= 4) / n_all
    le6 = sum(1 for x in lens if x <= 6) / n_all
    le10 = sum(1 for x in lens if x <= 10) / n_all
    # 配置目标：四成 ≤4、六成 ≤6、八成 ≤10。
    # B01：两个方向都算偏差。旧版 min(实际/目标,1) 只罚“不够短”，
    # 十个回答全是「嗯」也能拿 1.0；现在超短同样扣分。
    _dev = sum(
        min(1.0, abs(actual - target) / target)
        for actual, target in ((le4, 0.4), (le6, 0.6), (le10, 0.8))
    ) / 3
    length_score = round(1.0 - _dev, 4)
    return {
        "total": total,
        "passed": passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "avg_len": round(sum(lens) / len(lens), 2) if lens else 0.0,
        "median_len": lens[len(lens) // 2] if lens else 0,
        "pct_le4": round(le4, 4),
        "pct_le6": round(le6, 4),
        "pct_le10": round(le10, 4),
        "length_target_score": length_score,
        "buckets": buckets,
        "fail_kinds": dict(sorted(fails.items(), key=lambda kv: -kv[1])),
        "by_cat": {k: {"n": v["n"], "pass": v["pass"],
                       "rate": round(v["pass"] / v["n"], 3)}
                   for k, v in sorted(by_cat.items())},
    }
