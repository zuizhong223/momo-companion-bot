# -*- coding: utf-8 -*-
"""按 yy_send_task 时间统计“修复后/某时间之后”的发出分布。

区别 live_dist：live_dist 读 conversation（被动回复，可能缺历史）；
本脚本读发送台账，含主动/被动/贴纸，用来在 50 条还没攒够时做早期信号。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

DB = Path(r"D:/demo\instances\11111111-1111-4111-8111-111111111111/core/data/data_v4.db")
TARGET = {"short": 39, "long": 20, "bracket": 33, "sticker": 39}

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-10-26 17:01:00")
    ap.add_argument("--until", default="")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    sql = ("select kind,text,created_at from yy_send_task"
           " where state='sent' and created_at>=?")
    params = [args.since]
    if args.until:
        sql += " and created_at<?"
        params.append(args.until)
    sql += " order by created_at"
    con = sqlite3.connect(DB)
    rows = con.execute(sql, params).fetchall()
    con.close()
    texts = [r[1] for r in rows if r[0] == "text" and r[1]]
    tests = [t for t in texts if t.startswith("[") or "测" in t]
    texts = [t for t in texts if not (t.startswith("[") or "测" in t)]
    stickers = [r for r in rows if r[0] == "sticker"]
    bubbles = []
    for t in texts:
        for b in re.split(r"[\n]+", t):
            b = b.strip()
            if b:
                bubbles.append(b)
    n = len(bubbles)
    if not n:
        print("没有文本")
        return 1
    short = sum(1 for b in bubbles if 1 <= len(b) <= 4)
    long = sum(1 for b in bubbles if len(b) >= 11)
    bracket = sum(1 for b in bubbles if b.endswith("（") or b.endswith("("))
    print(f"区间 {args.since} ~ {args.until or 'now'}")
    print(f"文本任务 {len(texts)} / 气泡 {n} / 贴纸 {len(stickers)}（排除测试 {len(tests)} 条）")
    print(f"1~4 字 {short}/{n} = {short/n:.1%}（机器人 {TARGET['short']}%）")
    print(f"11+ 字 {long}/{n} = {long/n:.1%}（机器人 {TARGET['long']}%）")
    print(f"半括号 {bracket}/{n} = {bracket/n:.1%}（机器人 {TARGET['bracket']}%）")
    print(f"贴纸任务占比 {len(stickers)}/{len(texts)+len(stickers)} = {len(stickers)/max(1,len(texts)+len(stickers)):.1%}（机器人 {TARGET['sticker']}%）")
    print("文本：")
    for t in texts:
        print("  ", repr(t))
    out = Path(r"D:/demo\ops\eval\reports") / "task_dist_latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"since": args.since, "until": args.until,
                               "texts": texts, "stickers": len(stickers),
                               "bubbles": n, "short": short, "long": long,
                               "bracket": bracket}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
