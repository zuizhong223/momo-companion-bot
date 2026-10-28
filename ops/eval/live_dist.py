# -*- coding: utf-8 -*-
"""线上真实分布：按**台账**统计（yy_send_task），带时间窗、一个 task 当一个气泡。

为什么改（2026-10-28）：
  旧实现从 conversations 最新一条里读 assistant 消息，时间戳靠解析用户消息里的
  "Current datetime:"。大多数消息不带这个字段 → `--since` **静默失效**，
  每次都统计"全部"，和"近 24h"的结论能差一倍（中位 7 vs 5）；
  而且贴纸/文字计数是全量，和窗口混在一起。

现在：
  · 数据源 = yy_send_task（有 created_at / kind，能精确开窗）
  · 一个 task = 一个气泡（多气泡发送本来就是每段一条 task）
  · 排除系统消息（巡检/生活层提示）
  · 贴纸占比与文字计数用同一个窗口

用法：
  python ops\\eval\\live_dist.py                     # 最近 24h
  python ops\\eval\\live_dist.py --hours 168         # 最近 7 天
  python ops\\eval\\live_dist.py --since 2026-10-27  # 指定起始日期
  python ops\\eval\\live_dist.py --json
不依赖 AstrBot 运行时；只读 data_v4.db。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(r"D:/demo")
DB = ROOT / "instances/11111111-1111-4111-8111-111111111111/core/data/data_v4.db"
OUT = ROOT / "ops/eval/reports"
QUIRKS = ["好好好", "哈哈哈哈", "哈哈", "嗯嗯", "好耶", "那很好了",
          "啊？", "诶", "捏", "来着", "摸摸"]
# 机器人的目标：唯一权威是 ops/eval/style_targets.py（读 phase_stats.json 的 early 段）
try:
    from ops.eval.style_targets import TARGET as _TGT  # type: ignore
except Exception:  # noqa: BLE001
    try:
        sys.path.insert(0, str(ROOT / "ops"))
        from eval.style_targets import TARGET as _TGT  # type: ignore
    except Exception:  # noqa: BLE001
        _TGT = {"short": 0.412, "long": 0.154, "median": 5,
                "bracket": 0.485, "haohao": 0.110, "haha": 0.067}
TARGET = _TGT
SYSTEM_MARKERS = ("（巡检）", "（生活层", "[巡检]")
NON_TEXT = {"[语音]", "[动画表情]"}


def load_tasks(since: str | None, hours: int | None) -> tuple[list[str], int, int, str]:
    """返回 (气泡文本, 贴纸数, 文字气泡数, 窗口描述)。"""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        where = ["state='sent'", "text IS NOT NULL", "text != ''"]
        args: list = []
        window = "全部"
        if since:
            where.append("created_at >= ?")
            args.append(f"{since} 00:00:00" if len(since) == 10 else since)
            window = f"since {since}"
        elif hours:
            cut = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
            where.append("created_at >= ?")
            args.append(cut)
            window = f"近 {hours}h"
        rows = con.execute(
            "SELECT kind, text FROM yy_send_task WHERE " + " AND ".join(where)
            + " ORDER BY created_at", args).fetchall()
    finally:
        con.close()

    bubbles: list[str] = []
    sticker = texts = 0
    for kind, text in rows:
        t = (text or "").strip()
        if any(m in t for m in SYSTEM_MARKERS):
            continue
        if kind == "sticker":
            sticker += 1
            continue
        if kind not in ("text", "record") or t in NON_TEXT:
            continue
        texts += 1
        bubbles.append(t)
    return bubbles, sticker, texts, window


def metrics(bubbles_: list[str]) -> dict:
    n = len(bubbles_)
    if not n:
        return {"n": 0}
    lens = [len(re.sub(r"\s+", "", b)) for b in bubbles_]
    return {
        "n": n,
        "short": sum(1 for x in lens if x <= 4) / n,
        "long": sum(1 for x in lens if x >= 11) / n,
        "median": statistics.median(lens),
        "avg": round(statistics.mean(lens), 1),
        # 机器人的 bracket 定义（build_phase_banks.py L82）：**以任何括号结尾**（（(））
        # 旧实现只认「（」，把「好好好好）」「想吧想吧）」这类全漏了 → 一直低估。
        "bracket": sum(1 for b in bubbles_ if re.search(r"[（(）)]$", b)) / n,
        "punct": sum(1 for b in bubbles_ if re.search(r"[，。！？]", b)) / n,
        "quirks": {q: sum(1 for b in bubbles_ if q in b) / n for q in QUIRKS},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None, help="YYYY-MM-DD（含）或完整时间")
    ap.add_argument("--hours", type=int, default=None, help="最近 N 小时；默认 24")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    hours = None if args.since else (args.hours or 24)
    bub, st, tt, window = load_tasks(args.since, hours)
    m = metrics(bub)
    l1 = None
    if m.get("n"):
        l1 = (abs(m["short"] - TARGET["short"]) + abs(m["long"] - TARGET["long"])
              + abs(m["bracket"] - TARGET["bracket"])
              + abs(m["quirks"]["好好好"] - TARGET["haohao"])
              + abs(m["quirks"]["哈哈"] - TARGET["haha"]))

    if args.json:
        print(json.dumps({"source": "ledger", "window": window, "metrics": m,
                          "sticker_tasks": st, "text_tasks": tt, "l1": l1},
                         ensure_ascii=False, indent=2, default=str))
        return 0

    print(f"=== 线上分布（源=台账，{window}）===")
    if not m.get("n"):
        print("没有样本")
        return 0
    print(f"气泡 {m['n']} 个；台账 文字={tt} 贴纸={st}")
    print(f"1~4 字 {m['short']:.1%}（机器人 {TARGET['short']:.0%}） | "
          f"11+ 字 {m['long']:.1%}（机器人 {TARGET['long']:.0%}）")
    print(f"中位 {m['median']}（机器人 {TARGET['median']}） | 均值 {m['avg']}")
    print(f"半括号 {m['bracket']:.1%}（机器人 {TARGET['bracket']:.0%}） | 标点 {m['punct']:.1%}")
    print("口癖: " + " | ".join(f"{k} {v:.1%}" for k, v in m["quirks"].items() if v))
    print(f"贴纸任务占比 {st / max(st + tt, 1):.1%}（机器人 39% 消息 / 52% 回合）")
    print(f"L1 距离（5 项，越小越像）: {l1:.2f}")

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"live_dist_{datetime.now():%Y%m%d_%H%M%S}.json"
    p.write_text(json.dumps({"source": "ledger", "window": window, "metrics": m,
                             "sticker_tasks": st, "text_tasks": tt, "l1": l1},
                            ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"报告: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
