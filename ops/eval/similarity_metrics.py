# -*- coding: utf-8 -*-
"""像机器人指标：同一意图的重复度 / 长度 / 括号 / 笑 / 口头禅。

为什么单独做：现在的 45 条、20 条 holdout 只测“单句合规”，不测“像”。
这个脚本按意图算长尾，专门用来验收 A（决策表变体池）、C（笑）、D（场景长尾）。

用法：
  python ops\\eval\\similarity_metrics.py --samples ops\\eval\\reports\\probe_variance.json
  python ops\\eval\\similarity_metrics.py --samples before.json --compare after.json
  python ops\\eval\\similarity_metrics.py --samples x.jsonl --strict

支持输入：
  1) {intent: [reply, ...]}
  2) {intent: {"samples": [reply, ...]}}
  3) JSONL / list[dict]：{"intent": "...", "reply": "..."}
     （也认 cat/text/a 字段；--auto-intent 时按 user 文本猜意图）

输出：markdown 表 + 可选 JSON；--strict 时低于目标返回 1。
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

INTENTS = {
    "关心你/支持": r"关心你|关心|好关心你|认可你|认可你|鼓励|抱一下|加油|在一起吧|做我朋友",
    "难受/安慰": r"累死|好累|难受|想哭|哭了|烦死|压力|崩溃|不开心|emo|心情不好|头疼|发烧|没用|情绪|撑不住",
    "道歉/生气": r"生气|对不起|道歉|吵架|别生气|原谅|失望|不理你|失约",
    "晚安/睡": r"晚安|睡了|睡觉|困了|熬夜|几点睡|睡吧|去睡了",
    "在干嘛": r"在干嘛|干嘛呢|做什么呢|在忙|忙吗|干嘛啊|在干什么",
    "吃了吗": r"吃了吗|吃饭|吃了没|饿|夜宵|外卖|吃什么|午饭|晚饭",
    "买东西/分享": r"买了|新买|到了|给你看|看看这个|你看|键盘|耳机|相机|手机|鞋",
    "夸机器人/可爱": r"可爱|好看|漂亮|厉害|牛|棒|真美",
    "游戏": r"崩铁|原神|星铁|mc|我的世界|开黑|上号|深渊|遗器|模拟宇宙",
}

TICS = ("摸摸", "怎么了", "新的好啊", "没有没有", "想）")

TARGETS = {
    "top_max": 0.15,
    "unique_min": 0.70,
    "long_min": 0.30,
    "long_max": 0.40,
    "haha_min": 0.05,
    "haha_max": 0.07,
    "tic_max": 0.05,
}


def norm(s: str) -> str:
    s = re.sub(r"\[[^\]]{0,14}\]", "", s or "")
    return " ".join(s.split()).strip()


def intent_of(text: str) -> str:
    for name, pat in INTENTS.items():
        if re.search(pat, text or ""):
            return name
    return "其他"


def load(path: Path, auto: bool = False) -> dict[str, list[str]]:
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        rows = [json.loads(x) for x in raw.splitlines() if x.strip()]
        return _from_rows(rows, auto)
    data = json.loads(raw)
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        return _from_rows(data["results"], auto)
    if isinstance(data, dict):
        out: dict[str, list[str]] = {}
        for k, v in data.items():
            if isinstance(v, dict):
                vals = v.get("samples") or v.get("replies") or []
            elif isinstance(v, list):
                vals = v
            else:
                continue
            out[k] = [norm(x) for x in vals if isinstance(x, str) and norm(x)]
        return out
    if isinstance(data, list):
        return _from_rows(data, auto)
    raise SystemExit(f"不认识的输入格式：{path}")


def _from_rows(rows: list[dict], auto: bool = False) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        intent = "" if auto else (r.get("intent") or r.get("cat") or "")
        if not intent:
            intent = intent_of(str(r.get("user") or r.get("input") or ""))
        reply = (r.get("reply") or r.get("text") or r.get("final")
                 or r.get("a") or r.get("her") or "")
        reply = norm(str(reply))
        if reply:
            out.setdefault(str(intent), []).append(reply)
    return out


def metrics(rows: list[str]) -> dict:
    rows = [r for r in rows if r]
    n = len(rows)
    if not n:
        return {"n": 0}
    lens = sorted(len(re.sub(r"\s+", "", r)) for r in rows)
    cnt = Counter(rows)
    top, c = cnt.most_common(1)[0]
    return {
        "n": n,
        "uniq": len(cnt),
        "unique": round(len(cnt) / n, 3),
        "top": top,
        "top_share": round(c / n, 3),
        "median": statistics.median(lens),
        "short_1_4": round(sum(1 for x in lens if x <= 4) / n, 3),
        "long_11": round(sum(1 for x in lens if x >= 11) / n, 3),
        "bracket": round(sum(1 for r in rows if re.search(r"[（(）)]$", r)) / n, 3),
        "haha": round(sum(1 for r in rows if "哈哈" in r) / n, 3),
        "tics": {t: round(sum(1 for r in rows if t in r) / n, 3) for t in TICS},
    }


def flags(m: dict) -> list[str]:
    if not m.get("n"):
        return ["空"]
    out = []
    if m["top_share"] > TARGETS["top_max"]:
        out.append(f"top{m['top_share']:.0%}")
    if m["unique"] < TARGETS["unique_min"]:
        out.append(f"unique{m['unique']:.0%}")
    if m["long_11"] < TARGETS["long_min"]:
        out.append(f"长{m['long_11']:.0%}")
    if m["long_11"] > TARGETS["long_max"]:
        out.append(f"太长{m['long_11']:.0%}")
    if m["haha"] < TARGETS["haha_min"]:
        out.append(f"笑{m['haha']:.0%}")
    for t, v in m["tics"].items():
        if v > TARGETS["tic_max"]:
            out.append(f"{t}{v:.0%}")
    return out


def render(name: str, data: dict[str, list[str]], compare: dict[str, list[str]] | None) -> str:
    lines = [f"# 像机器人指标 · {name}", "",
             "目标：top ≤15% · unique ≥70% · 11+ 30–40% · 哈哈 5–7% · 口头禅 ≤5%", ""]
    header = "| 意图 | n | 种 | 最高频 | 中位 | 1-4 | 11+ | 括号 | 哈哈 | 摸/怎/新/没/想 | 判定 |"
    lines += [header, "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|"]
    keys = list(dict.fromkeys(list(data) + list(compare or {})))
    for k in keys:
        m = metrics(data.get(k, []))
        if not m.get("n"):
            continue
        tic = "/".join(f"{m['tics'][t]:.0%}" for t in TICS)
        fl = "、".join(flags(m)) or "OK"
        lines.append(f"| {k} | {m['n']} | {m['uniq']} | {m['top_share']:.0%}「{m['top'][:14]}」 | "
                     f"{m['median']} | {m['short_1_4']:.0%} | {m['long_11']:.0%} | "
                     f"{m['bracket']:.0%} | {m['haha']:.0%} | {tic} | {fl} |")
    allrows = [r for v in data.values() for r in v]
    m = metrics(allrows)
    if m.get("n"):
        tic = "/".join(f"{m['tics'][t]:.0%}" for t in TICS)
        lines += ["", f"**总体** n={m['n']}，unique {m['unique']:.0%}，top {m['top_share']:.0%}，"
                  f"11+ {m['long_11']:.0%}，哈哈 {m['haha']:.0%}，口头禅 {tic}"]
    if compare:
        lines += ["", "## 改前 → 改后", "",
                  "| 意图 | top | unique | 11+ | 哈哈 |", "|---|---:|---:|---:|---:|"]
        for k in keys:
            a, b = metrics(compare.get(k, [])), metrics(data.get(k, []))
            if not a.get("n") or not b.get("n"):
                continue
            lines.append(f"| {k} | {a['top_share']:.0%}→{b['top_share']:.0%} | "
                         f"{a['unique']:.0%}→{b['unique']:.0%} | "
                         f"{a['long_11']:.0%}→{b['long_11']:.0%} | "
                         f"{a['haha']:.0%}→{b['haha']:.0%} |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", required=True)
    ap.add_argument("--compare", default="")
    ap.add_argument("--json", default="")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--auto-intent", action="store_true",
                    help="忽略 cat/intent 字段，按 user/input 文本猜意图")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    data = load(Path(args.samples), args.auto_intent)
    compare = load(Path(args.compare), args.auto_intent) if args.compare else None
    text = render(Path(args.samples).name, data, compare)
    print(text)
    if args.json:
        Path(args.json).write_text(json.dumps(
            {"intents": {k: metrics(v) for k, v in data.items()},
             "targets": TARGETS}, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.strict:
        bad = [k for k, v in data.items() if flags(metrics(v))]
        if bad:
            print(f"\nSTRICT FAIL: {', '.join(bad)}")
            return 1
        print("\nSTRICT PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
