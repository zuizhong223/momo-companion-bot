# -*- coding: utf-8 -*-
"""用 LLM 当裁判，评「实质质量」：相关性 / 像不像机器人 / 自然度（各 1~5 分）。

规则评分器只能查形式（长度、标点、括号），查不出「答非所问」「纯语气词糊弄」
「视角错乱」「编造细节」。这个裁判专门查这几类。

用法：
  python ops/eval/judge.py --report reports/04_final.json --tag 04_final
  python ops/eval/judge.py --report reports/06_persona_v2.json --tag 06 --compare 04_final
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

API = "https://api.deepseek.com/v1/chat/completions"

RUBRIC = """你在给一个 QQ 聊天机器人做质量评审：相关性、自然度、风格一致性。

## 目标风格（合成示例，不是真实用户）
- 短句为主，2~6 字常见；允许半括号结尾。
- 高频中性词：「好好好」「哈哈哈哈」「那很好了」「好耶」「嗯嗯」「摸摸」。
- 事实问题必须给具体答案；情绪陈述可以短接，不要求长篇安慰。
- 避免剧本腔、说教、AI 腔、正式承诺。
- 不评价“像某个示例对象”，只评价是否符合配置和上下文。

## 打分（1~5 整数，严格）
1. in_character（风格一致性）——最重要
   - 5=长度、用词、句尾都符合配置；3=略长/略顺；2=剧本腔/说教/AI 腔；1=完全不像。
2. naturalness（自然度）
   - 5=像随手打出来的；3=有点怪；2=视角颠倒/编造细节/语法不通；1=看不懂。
3. relevance（有没有答到）
   - 用户问事实（在干嘛/做了多久/吃了吗）→ 必须答；只回语气词给 2 分。
   - 用户说自己的事 → 短反应也算答到，给 4~5；完全答非所问才给 2 分或更低。
4. attunement（有没有读对意思）
   - 5=读对对方在说什么/什么情绪；3=看不出；2=读错；1=把情绪顶回去或开始说教。
   - 不要因为“没安慰、没追问、太短”给低分——短是配置的一部分。

只输出一行 JSON，不要解释：
{"relevance": n, "in_character": n, "naturalness": n, "attunement": n, "note": "不超过 20 字的理由"}"""


FACT_CATS = {'日常', '练习日常', '游戏崩铁', '记忆提问', '深夜时间'}
FACT_HINT = ('用户问的是关于机器人的事实（在干嘛、练了多久、吃了吗、某段记忆）。机器人应该给出具体答案。'
             '只回语气词、或者把问题反问回去，说明没答，relevance 直接给 2 分或更低。'
             '但如果资料没有给出答案，简短承认「不知道 / 不记得」是合格，不要奖励编造的具体时间、数字或人名。')
SOFT_HINT = ('用户在表达情绪、轻松、表达，或者说用户自己的事。机器人本人对这类话经常只回「好好好」'
             '「哈哈哈哈」「晚安（」或者干脆转话题——**这不算扣分，反而更像机器人**。'
             '不要因为机器人没正面回应而扣 relevance（但 attunement 仍看情绪方向对不对）。只扣：像剧本台词、太长、说教、AI 腔、视角颠倒、编造细节。')


def judge_one(case, reply, retries=3):
    import local_keys as lk

    kind = case.get('kind') or ('fact' if case.get('cat') in FACT_CATS else 'soft')
    hint = FACT_HINT if kind == 'fact' else SOFT_HINT
    extra = []
    if case.get('reference'):
        extra.append(f"可核对的事实依据：{case['reference']}")
    if case.get('history'):
        extra.append("前几轮上下文：\n" + "\n".join(
            f"- {h.get('role')}: {h.get('content')}" for h in case['history']))
    user = (f"场景类型：{kind}\n评分提示：{hint}\n"
            + ("\n".join(extra) + "\n" if extra else "")
            + f"用户说的话：{case['input']}\n机器人的回复：{reply or '（空）'}")
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "system", "content": RUBRIC},
                     {"role": "user", "content": user}],
        "temperature": 0.0,
        "max_tokens": 120,
        "stream": False,
    }
    last = ""
    for i in range(retries):
        try:
            req = urllib.request.Request(
                API, data=json.dumps(payload).encode(),
                headers={"Authorization": f"Bearer {lk.DEEPSEEK_KEY}",
                         "Content-Type": "application/json"})
            with _OPENER.open(req, timeout=90) as r:
                d = json.loads(r.read().decode())
            txt = (d["choices"][0]["message"]["content"] or "").strip()
            m = re.search(r"\{.*\}", txt, re.S)
            obj = json.loads(m.group(0)) if m else {}
            return {
                "relevance": int(obj.get("relevance", 0)),
                "in_character": int(obj.get("in_character", 0)),
                "naturalness": int(obj.get("naturalness", 0)),
                "attunement": int(obj.get("attunement", 0)),
                "note": str(obj.get("note", ""))[:40],
            }
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001
            last = str(e)[:80]
        time.sleep(2 * (i + 1))
    return {"relevance": 0, "in_character": 0, "naturalness": 0, "attunement": 0,
            "note": f"judge失败 {last}"}


def summarize(rows):
    n = len(rows) or 1
    axes = ("relevance", "in_character", "naturalness", "attunement")
    means = {a: round(sum(r.get(a, 0) for r in rows) / n, 2) for a in axes}
    means["overall"] = round(sum(means[a] for a in axes) / len(axes), 2)
    weak = [r for r in rows if min(r.get("relevance", 0), r.get("naturalness", 0), r.get("attunement", 0)) <= 2]
    means["weak_count"] = len(weak)
    means["weak_rate"] = round(len(weak) / n, 3)
    means["fail_judge"] = sum(1 for r in rows if r["relevance"] == 0)
    return means


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True, help="reports/xxx.json")
    ap.add_argument("--tag", default="")
    ap.add_argument("--compare", default="")
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    rep = json.loads((HERE / args.report).read_text(encoding="utf-8")
                     if not Path(args.report).is_absolute()
                     else Path(args.report).read_text(encoding="utf-8"))
    results = rep.get("results") or rep.get("rows") or []
    src_tag = rep.get("tag") or rep["summary"]["tag"]
    tag = f"judge_{args.tag or src_tag}"

    def one(r):
        j = judge_one(r, r.get("text") or r.get("raw") or "")
        return {**r, **j}

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        rows = list(ex.map(one, results))
    stats = summarize(rows)

    for r in rows:
        flag = "!!" if min(r.get("relevance", 0), r.get("naturalness", 0), r.get("attunement", 0)) <= 2 else "  "
        print(f"  {flag} {r['id']:<4} rel={r['relevance']} ic={r['in_character']} "
              f"nat={r['naturalness']} att={r['attunement']} | {r['input'][:18]!r} -> "
              f"{(r.get('text') or '')[:24]!r} | {r['note']}")

    print(f"\n[judge] 相关性 {stats['relevance']} / 像机器人 {stats['in_character']} / "
          f"自然度 {stats['naturalness']} / 接住 {stats['attunement']} → 综合 {stats['overall']} "
          f"| 明显不合格 {stats['weak_count']}/{len(rows)} ({stats['weak_rate']:.0%}) "
          f"| {round(time.time() - t0, 1)}s")

    out = HERE / "reports"
    out.mkdir(exist_ok=True)
    payload = {"tag": tag, "judged_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "source": args.report, "stats": stats, "rows": rows}
    (out / f"{tag}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                     encoding="utf-8")

    lines = [f"# 评审报告（LLM 裁判）· {tag}", "",
             f"- 来源：`{args.report}`　裁判模型：deepseek-chat（temperature 0）",
             f"- 综合 **{stats['overall']}**（相关性 {stats['relevance']} / "
             f"像机器人 {stats['in_character']} / 自然度 {stats['naturalness']} / 接住 {stats['attunement']}，满分 5）",
             f"- 明显不合格（相关性/自然度/接住 任一 ≤2）：**{stats['weak_count']}/{len(rows)}**"
             f"（{stats['weak_rate']:.0%}）", "",
             "| id | 类别 | 输入 | 回复 | 相关 | 像机器人 | 自然 | 接住 | 评语 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(
            f"| {r['id']} | {r['cat']} | {r['input'][:22]} | {r.get('text','')[:26]} | "
            f"{r['relevance']} | {r['in_character']} | {r['naturalness']} | {r['attunement']} | {r['note']} |")
    if args.compare:
        prev_path = out / f"{args.compare}.json"
        if prev_path.exists():
            prev = json.loads(prev_path.read_text(encoding="utf-8"))["stats"]
            lines += ["", f"## 对比 `{args.compare}`", "",
                      "| 指标 | 旧 | 新 | 变化 |", "|---|---|---|---|"]
            for k, label in (("overall", "综合"), ("relevance", "相关性"),
                             ("in_character", "像机器人"), ("naturalness", "自然度"), ("attunement", "接住"),
                             ("weak_count", "不合格数")):
                a, b = prev.get(k), stats.get(k)
                if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                    lines.append(f"| {label} | {a} | {b} | {b - a:+} |")
    (out / f"{tag}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[judge] 报告 -> {out / (tag + '.md')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
