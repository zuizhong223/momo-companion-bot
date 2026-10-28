# -*- coding: utf-8 -*-
"""B04 的 AI 预标：不替代人工，只把 20 条先按 1~5 打一遍，省人一半时间。

输出 reports/judge_calibration_20_ai.csv；人工只改有异议的格子，再填 human_*。
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

REPORTS = Path(r"D:/demo\ops\eval\reports")
SRC = REPORTS / "judge_calibration_20.csv"
OUT = REPORTS / "judge_calibration_20_ai.csv"

# id: (相关, 像机器人, 自然, 备注)
SCORES = {
    "k01": (5, 5, 5, "生日答对，短句自然"),
    "n02": (5, 5, 5, "晚安（ 标准"),
    "m02": (3, 4, 4, "只回“想）”，没接“原谅”"),
    "c02": (5, 5, 5, "熬夜关心，短"),
    "t01": (4, 5, 5, "太极式拒绝，符合配置"),
    "d02": (5, 5, 5, "日常真实"),
    "b01": (5, 5, 5, "边界测试，不像 AI"),
    "e01": (5, 5, 5, "先接情绪，不问教"),
    "m01": (4, 5, 4, "想）有点短，但符合决策表"),
    "v01": (4, 5, 4, "视角守卫，答“我不是用户”"),
    "n01": (4, 5, 5, "三点在练习，真实"),
    "p01": (5, 5, 4, "十点到三点，符合机器人练习"),
    "d01": (5, 5, 5, "在干嘛→练习"),
    "g02": (5, 5, 5, "反问差哪层，像机器人"),
    "g01": (4, 5, 5, "云游戏卡，真实"),
    "e02": (5, 5, 4, "不是啊+夸，括号略多"),
    "q01": (4, 5, 5, "群里被问，回得自然"),
    "c01": (5, 5, 5, "头好疼→摸摸早点睡"),
    "t02": (3, 5, 4, "和 t01 同一句，重复"),
    "x01": (3, 5, 4, "被上级骂只回吃点好的，没接问题"),
}

rows = list(csv.DictReader(SRC.open(encoding="utf-8-sig")))
out = []
for r in rows:
    rs = SCORES.get(r["id"])
    if not rs:
        continue
    rel, ic, nat, note = rs
    out.append({
        "id": r["id"], "cat": r["cat"], "input": r["input"],
        "model_reply": r["model_reply"],
        "ai_relevance": rel, "ai_in_character": ic, "ai_naturalness": nat,
        "ai_note": note,
    })

with OUT.open("w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["id", "cat", "input", "model_reply",
                                      "ai_relevance", "ai_in_character",
                                      "ai_naturalness", "ai_note"])
    w.writeheader()
    w.writerows(out)

avg = {k: sum(r[k] for r in out) / len(out) for k in
       ("ai_relevance", "ai_in_character", "ai_naturalness")}
print(f"写出 {len(out)} 条 -> {OUT}")
print("均值:", {k: round(v, 2) for k, v in avg.items()})
print("注意：这是 AI 预标，不是人工校准；人工改完再填 human_* 跑 --compare。")
