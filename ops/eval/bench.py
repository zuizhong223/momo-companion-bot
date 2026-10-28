#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""多轮基准：跑 N 轮 + 判分，给出均值/标准差/最小可检测差异。

为什么要有它：20 条 × 1 轮的时候，"像机器人"单轮能在 4.05~4.55 之间跳，
    任何小于 0.3 的差异都测不出来。这个脚本把"跑几轮、差多少才算真"讲清楚。

用法：
  python ops\\eval\\bench.py --cases holdout_v2_20261026.jsonl --runs 3
  python ops\\eval\\bench.py --cases holdout_v2_20261026.jsonl --runs 3 --persona-file 备份.txt
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:/demo")
EVAL = ROOT / "ops/eval"
REPORTS = EVAL / "reports"
DB = ROOT / "instances/11111111-1111-4111-8111-111111111111/core/data/data_v4.db"
PERSONA_TXT = ROOT / "persona.txt"
KEYS = ("overall", "relevance", "in_character", "naturalness", "attunement", "weak_count")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def persona_fingerprint() -> dict:
    """配置指纹：文件 + 数据库里生效的那份。任何 A/B 都必须带上它。"""
    out = {"file_sha": None, "db_sha": None, "chars": None}
    try:
        text = PERSONA_TXT.read_text(encoding="utf-8")
        out["file_sha"] = sha(text)
        out["chars"] = len(text)
    except Exception:  # noqa: BLE001
        pass
    try:
        import sqlite3

        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        try:
            row = con.execute(
                "select system_prompt from personas where persona_id='ajing'").fetchone()
        finally:
            con.close()
        if row and row[0]:
            out["db_sha"] = sha(row[0])
    except Exception:  # noqa: BLE001
        pass
    return out


def run_once(cases: str, tag: str, extra: list[str], concurrency: int) -> dict:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    subprocess.run([sys.executable, str(EVAL / "runner.py"), "--cases", cases,
                    "--tag", tag, "--concurrency", str(concurrency)] + extra,
                   cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
    subprocess.run([sys.executable, str(EVAL / "judge.py"), "--report", f"reports/{tag}.json",
                    "--tag", tag, "--concurrency", str(concurrency)],
                   cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
    f = REPORTS / f"judge_{tag}.json"
    if not f.exists():
        raise RuntimeError(f"判分没有产出：{f}")
    data = json.loads(f.read_text(encoding="utf-8"))
    stats = data["stats"]
    # 判分失败保护：全 0 说明是调用失败，不是"机器人突然变差"。重试一次判分。
    if stats.get("in_character") == 0 and stats.get("relevance") == 0:
        fails = sum(1 for r in data.get("rows", []) if str(r.get("note", "")).startswith("judge失败"))
        print(f"    [warn] 判分疑似失败（{fails} 条），重试一次…")
        subprocess.run([sys.executable, str(EVAL / "judge.py"), "--report", f"reports/{tag}.json",
                        "--tag", f"{tag}_retry", "--concurrency", "1"],
                       cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
        f2 = REPORTS / f"judge_{tag}_retry.json"
        if f2.exists():
            data = json.loads(f2.read_text(encoding="utf-8"))
            stats = data["stats"]
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="holdout_v3_20261027.jsonl")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--persona-file", default="")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    tag_base = args.tag or f"bench_{datetime.now().strftime('%m%d_%H%M')}"
    extra = ["--persona-file", args.persona_file] if args.persona_file else []
    fp = persona_fingerprint()
    print(f"用例 {args.cases} · {args.runs} 轮 · 并发 {args.concurrency}")
    print(f"配置指纹：file={fp['file_sha']} db={fp['db_sha']} chars={fp['chars']}")
    if args.persona_file:
        print(f"（本轮用替换配置：{args.persona_file}）")

    runs = []
    for i in range(1, args.runs + 1):
        tag = f"{tag_base}_r{i}"
        s = run_once(args.cases, tag, extra, args.concurrency)
        runs.append(s)
        print(f"  第{i}轮: 综合 {s['overall']} | 像机器人 {s['in_character']} | "
              f"自然 {s['naturalness']} | 接住 {s['attunement']} | 不合格 {s['weak_count']}")

    print("\n=== 汇总 ===")
    summary = {}
    for k in KEYS:
        v = [r[k] for r in runs]
        m = round(statistics.mean(v), 2)
        sd = round(statistics.pstdev(v), 2) if len(v) > 1 else 0.0
        # 最小可检测差异：约 2 倍标准差（粗估，1 轮时不可估）
        mdd = round(2 * sd, 2) if len(v) > 1 else None
        summary[k] = {"mean": m, "sd": sd, "min": min(v), "max": max(v), "mdd": mdd}
        rng = f"{min(v)}~{max(v)}"
        print(f"  {k:<12}: {m} ± {sd}  (范围 {rng})" +
              (f"  可分辨 ≥{mdd}" if mdd else "  （单轮不可估）"))

    out = REPORTS / f"{tag_base}.json"
    out.write_text(json.dumps({
        "cases": args.cases, "runs": runs, "summary": summary,
        "persona": fp, "persona_file_override": args.persona_file,
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": "mdd ≈ 2σ；差异小于它就不能算变好或变差",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存: {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
