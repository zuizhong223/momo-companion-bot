# -*- coding: utf-8 -*-
"""P-1 评测基线运行器：固定 40 条输入 → 生成回复 → 规则打分 → 出报告。

用法：
  python ops/eval/runner.py --tag baseline           # 跑全量，写 reports/baseline.json/.md
  python ops/eval/runner.py --tag after_persona      # 改完配置再跑一次
  python ops/eval/runner.py --tag x --limit 10       # 冒烟
  python ops/eval/runner.py --compare baseline --tag y   # 与旧报告对比

生成端与线上一致：system = 配置原文；contexts = begin_dialogs 24 组；user = 输入。
"""
import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import checks  # noqa: E402

CORE = ROOT / 'instances/11111111-1111-4111-8111-111111111111/core'
sys.path.insert(0, str(CORE))
from astrbot.core.utils.outbound_gate import apply_gate, commit_style  # noqa: E402

DB = ROOT / "instances/11111111-1111-4111-8111-111111111111/core/data/data_v4.db"
API = "https://api.deepseek.com/v1/chat/completions"
PRICE_IN = 2.0 / 1_000_000   # deepseek-chat 输入（缓存未命中）估算价，元/token
PRICE_OUT = 8.0 / 1_000_000


def _gate_key(gate_mode: str, case_id: str) -> str:
    """B02：shared 共用会话流；per_case 每题独立，避免前题括号状态污染后题。"""
    return "eval:main" if gate_mode == "shared" else f"eval:single:{case_id}"


def run_fingerprint(persona: str, *, model: str, temp: float, rewrite: bool,
                    gate: bool, gate_mode: str, cases: str,
                    limit: int | None, seed: int = 0) -> tuple[str, str, dict]:
    """B05：同一份配置/配置必须得到同一指纹；参数变了指纹必须变。"""
    persona_sha = hashlib.sha256(persona.encode("utf-8")).hexdigest()
    config = {
        "model": model,
        "temperature": temp,
        "rewrite": bool(rewrite),
        "gate": bool(gate),
        "gate_mode": gate_mode,
        "persona_sha256": persona_sha,
        "cases": str(cases),
        "limit": limit,
        "seed": seed,
    }
    config_sha = hashlib.sha256(
        json.dumps(config, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return persona_sha, config_sha, config


def load_persona(persona_file=None):
    import sqlite3

    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    sp, bd = db.execute(
        "select system_prompt, begin_dialogs from personas where persona_id='ajing'"
    ).fetchone()
    db.close()
    if persona_file:
        sp = Path(persona_file).read_text(encoding='utf-8-sig')
    dialogs = json.loads(bd) if bd else []
    ctx = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": d}
        for i, d in enumerate(dialogs)
    ]
    return sp, ctx


def call_llm(messages, temp, model, retries=3):
    import local_keys as lk

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temp,
        "max_tokens": 200,
        "stream": False,
    }
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(
                API,
                data=json.dumps(payload).encode(),
                headers={
                    "Authorization": f"Bearer {lk.DEEPSEEK_KEY}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=120) as r:
                d = json.loads(r.read().decode())
            return (
                (d["choices"][0]["message"]["content"] or "").strip(),
                d.get("usage") or {},
            )
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code} {e.read().decode('utf-8', 'replace')[:200]}"
        except Exception as e:  # noqa: BLE001
            last = str(e)[:200]
        time.sleep(2 * (i + 1))
    return "", {"error": last}


def rewrite_long(text):
    """复刻线上 11~16 字的本地模型改写（可选）。"""
    if not (11 <= len(text) <= 16):
        return text
    try:
        payload = {
            "model": "demo",
            "stream": False,
            "messages": [
                {"role": "system", "content": (
                    "你是改写器。把用户的话改写成Bot说的第一人称话：极短（1-15字），"
                    "句尾可带半括号（或），用空格断句，不用句号不用emoji不换行，"
                    "主语改成我，不回应不反问，只改写原意，保留所有数字和名字。只输出改写结果。")},
                {"role": "user", "content": text},
            ],
        }
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode())
        out = (d.get("message") or {}).get("content", "").strip().strip('"')
        if 1 <= len(out) <= 30:
            return out
    except Exception:
        pass
    return text


def run(args):
    persona, dialogs = load_persona(args.persona_file)
    cases = [json.loads(line) for line in
             (HERE / args.cases if not os.path.isabs(args.cases) else args.cases).read_text(encoding="utf-8").splitlines() if line.strip()]
    for _c in cases:
        _c.setdefault("cat", _c.get("kind") or "heldout")
    if args.only:
        want = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in want or c.get("cat") in want or c.get("kind") in want]
    if args.limit:
        cases = cases[: args.limit]
    print(f"[eval] persona={len(persona)} chars, dialogs={len(dialogs)}, "
          f"cases={len(cases)}, temp={args.temp}, model={args.model}")

    # B05：口癖提示是随机的，必须在并发前固定种子、串行抽好，
    # 否则“三种子均值”本身不可复现。
    quirk_hints: list[str] = [""] * len(cases)
    try:
        import random as _random

        _random.seed(args.seed)
        from astrbot.core.astr_main_agent import _quirk_hint

        quirk_hints = [_quirk_hint() for _ in cases]
    except Exception:  # noqa: BLE001
        pass

    def one(item):
        idx, case = item
        q = quirk_hints[idx]
        sys_text = persona + ("\n\n" + q if q else "")

        def gen(user_text, session_key, context):
            msgs = [{"role": "system", "content": sys_text}] + context + [
                {"role": "user", "content": user_text}
            ]
            raw_text, usage = call_llm(msgs, args.temp, args.model)
            text = rewrite_long(raw_text) if args.rewrite else raw_text
            if args.gate:
                # 单轮题共用一条会话流（括号预算是"最近 3 条"）；
                # 多轮题各自一条流，避免互相污染。
                text = apply_gate(text, session_key=session_key,
                                  dedupe=False, record=False)[0]
                commit_style(text, session_key)
            return raw_text, text, usage

        # B02：多轮题必须把上一轮真实输出放进下一轮上下文，串行执行；
        # 单轮题仍然独立构造，不假装能证明跨题矛盾。
        turns = case.get("turns")
        if turns:
            session_key = f"eval:mt:{case['id']}"
            context = list(dialogs)
            detail = []
            last = None
            for t in turns:
                raw_text, text, usage = gen(t, session_key, context)
                r = checks.score(text, {**case, "input": t})
                r.update({"turn_input": t, "raw": raw_text, "final": text,
                          "quirk": q, "usage": usage})
                detail.append(r)
                context += [{"role": "user", "content": t},
                            {"role": "assistant", "content": text}]
                last = r
            last.update({
                "id": case["id"], "cat": case.get("cat") or case.get("kind") or "",
                "input": " / ".join(turns), "max_len": case.get("max_len", 30),
                "turns": turns, "turn_results": detail,
            })
            tag = "PASS" if last["ok"] else "FAIL"
            print(f"  {tag} {case['id']:<4} [multi] {turns[0][:20]!r}… -> "
                  f"{last['text'][:40]!r} {('| ' + '; '.join(last['hard_fails'])) if not last['ok'] else ''}")
            return last

        raw_text, text, usage = gen(
            case["input"],
            _gate_key(args.gate_mode, case["id"]),
            list(dialogs))
        r = checks.score(text, case)
        r.update({
            "id": case["id"], "cat": case.get("cat") or case.get("kind") or "",
            "input": case["input"], "max_len": case.get("max_len", 30),
            "raw": raw_text, "final": text, "quirk": q, "usage": usage,
        })
        tag = "PASS" if r["ok"] else "FAIL"
        print(f"  {tag} {case['id']:<4} [{case['cat']}] {case['input'][:24]!r} -> "
              f"{r['text'][:40]!r} {('| ' + '; '.join(r['hard_fails'])) if not r['ok'] else ''}")
        return r

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        results = list(ex.map(one, list(enumerate(cases))))
    elapsed = round(time.time() - t0, 1)

    summary = checks.summarize(results)
    tin = sum((r["usage"] or {}).get("prompt_tokens", 0) for r in results)
    tout = sum((r["usage"] or {}).get("completion_tokens", 0) for r in results)
    persona_sha, config_sha, config = run_fingerprint(
        persona, model=args.model, temp=args.temp, rewrite=bool(args.rewrite),
        gate=bool(args.gate), gate_mode=getattr(args, "gate_mode", "shared"),
        cases=str(args.cases), limit=args.limit,
        seed=getattr(args, "seed", 0))
    summary.update({
        "tag": args.tag,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "model": args.model,
        "temperature": args.temp,
        "rewrite": args.rewrite,
        "gate": args.gate,
        "persona_chars": len(persona),
        "persona_sha256": persona_sha,
        "config_sha256": config_sha,
        "config": config,
        "seed": getattr(args, "seed", 0),
        "elapsed_s": elapsed,
        "tokens_in": tin,
        "tokens_out": tout,
        "cost_yuan": round(tin * PRICE_IN + tout * PRICE_OUT, 3),
    })

    out = HERE / "reports"
    out.mkdir(exist_ok=True)
    report = {"summary": summary, "results": results}
    (out / f"{args.tag}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / f"{args.tag}.md").write_text(render_md(report, args.compare), encoding="utf-8")
    print(f"\n[eval] pass {summary['passed']}/{summary['total']} "
          f"({summary['pass_rate']:.0%}), avg_len={summary['avg_len']}, "
          f"tokens={tin}+{tout}, cost≈¥{summary['cost_yuan']}, {elapsed}s")
    print(f"[eval] report -> {out / (args.tag + '.md')}")


def render_md(report, compare_tag):
    s = report["summary"]
    lines = [
        f"# 评测报告 · {s['tag']}",
        "",
        f"- 时间：{s['time']}　模型：`{s['model']}`　temperature：{s['temperature']}"
        f"　本地改写：{s['rewrite']}",
        f"- 配置长度：{s['persona_chars']} 字　用例：{s['total']} 条　耗时：{s['elapsed_s']}s",
        f"- persona_sha256：{s.get('persona_sha256', '-')[:16]}　"
        f"config_sha256：{s.get('config_sha256', '-')[:16]}",
        f"- token：{s['tokens_in']} in / {s['tokens_out']} out　"
        f"估算费用：¥{s['cost_yuan']}",
        "",
        f"## 总览：通过 **{s['passed']}/{s['total']}**（{s['pass_rate']:.1%}）",
        "",
        f"平均长度 {s['avg_len']} 字，中位数 {s['median_len']} 字。",
        "",
        "| 长度区间 | 条数 | 占比 | 配置目标 |",
        "|---|---|---|---|",
    ]
    target = {"1-4": "约 40%", "5-6": "累计 60%", "7-10": "累计 80%", "11-16": "—", "17+": "—"}
    for b in ("1-4", "5-6", "7-10", "11-16", "17+"):
        n = s["buckets"].get(b, 0)
        pct = n / s["total"] if s["total"] else 0
        lines.append(f"| {b} | {n} | {pct:.0%} | {target[b]} |")
    lines += ["", "### 分类通过率", "", "| 类别 | 通过 | 总数 | 通过率 |", "|---|---|---|---|"]
    for cat, d in s["by_cat"].items():
        lines.append(f"| {cat} | {d['pass']} | {d['n']} | {d['rate']:.0%} |")
    lines += ["", "### 失败原因分布", ""]
    if s["fail_kinds"]:
        for k, v in s["fail_kinds"].items():
            lines.append(f"- {k}：{v}")
    else:
        lines.append("- 无")
    lines += ["", "## 逐条结果", "", "| id | 类别 | 输入 | 回复 | 字数 | 结果 |", "|---|---|---|---|---|---|"]
    for r in report["results"]:
        text = r["text"].replace("|", "\\|")
        inp = r["input"].replace("|", "\\|")
        flag = "✅" if r["ok"] else "❌ " + "；".join(r["hard_fails"])
        lines.append(f"| {r['id']} | {r['cat']} | {inp} | {text} | {r['length']} | {flag} |")

    if compare_tag:
        prev_path = HERE / "reports" / f"{compare_tag}.json"
        if prev_path.exists():
            prev = json.loads(prev_path.read_text(encoding="utf-8"))["summary"]
            lines += ["", f"## 对比 `{compare_tag}`", "",
                      "| 指标 | 旧 | 新 | 变化 |", "|---|---|---|---|"]
            for key, label in (("pass_rate", "通过率"), ("avg_len", "平均长度"),
                               ("median_len", "中位长度"), ("cost_yuan", "费用(元)")):
                a, b = prev.get(key), s.get(key)
                lines.append(f"| {label} | {a} | {b} | {b - a:+} |")
            old_fail = set(prev.get("fail_kinds", {}))
            new_fail = set(s.get("fail_kinds", {}))
            lines.append(f"| 已消除的失败类型 | - | {', '.join(sorted(old_fail - new_fail)) or '无'} | |")
            lines.append(f"| 新增的失败类型 | - | {', '.join(sorted(new_fail - old_fail)) or '无'} | |")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="cases.jsonl", help="用例文件（相对 ops/eval/）")
    ap.add_argument("--tag", default=datetime.now().strftime("run_%m%d_%H%M"))
    ap.add_argument("--temp", type=float, default=1.0)
    ap.add_argument("--model", default="deepseek-chat")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--seed", type=int, default=0, help="口癖抽样种子（B05）")
    ap.add_argument("--gate-mode", choices=("shared", "per_case"), default="shared",
                    help="单轮门禁状态：shared=连续会话流；per_case=每题独立（B02）")
    ap.add_argument("--rewrite", action="store_true")
    ap.add_argument("--gate", action="store_true", help="过一遍出站质量门禁再评分")
    ap.add_argument("--persona-file", default="", help="用指定配置文件替换数据库配置（做基线对比用）")
    ap.add_argument("--compare", default="")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    run(args)


if __name__ == "__main__":
    main()
