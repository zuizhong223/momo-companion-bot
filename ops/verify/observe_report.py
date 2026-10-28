# -*- coding: utf-8 -*-
"""P2-4 观察报告：把 observe.jsonl 汇总成 24h 增量。

用法：
  python ops\\verify\\observe_report.py --hours 24
只读，不发消息、不改库。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:/demo")
JSONL = ROOT / "ops" / "alerts" / "observe.jsonl"
KEYS = ("sent", "failed", "unknown", "dropped", "h_committed",
        "h_pending", "h_needs_target", "h_partial", "payload_mismatch", "total")


def load(hours: int) -> list[dict]:
    if not JSONL.exists():
        return []
    cut = dt.datetime.now().astimezone() - dt.timedelta(hours=hours)
    out = []
    for line in JSONL.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            t = dt.datetime.fromisoformat(row["time"])
        except Exception:  # noqa: BLE001
            continue
        if t >= cut:
            out.append(row)
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    args = ap.parse_args()
    rows = load(args.hours)
    if not rows:
        print(f"没有最近 {args.hours}h 的观察快照：{JSONL}")
        return 1
    first, last = rows[0], rows[-1]
    span = (dt.datetime.fromisoformat(last["time"])
            - dt.datetime.fromisoformat(first["time"]))
    print(f"# 观察报告（最近 {args.hours}h）\n")
    print(f"- 快照数：{len(rows)}")
    print(f"- 时间：{first['time']} → {last['time']}")
    print(f"- 覆盖：{span}")
    print("\n| 指标 | 起始 | 结束 | 增量 |")
    print("|---|---:|---:|---:|")
    for k in KEYS:
        a, b = int(first.get(k, 0)), int(last.get(k, 0))
        print(f"| {k} | {a} | {b} | {b - a:+d} |")
    bad = int(last.get("failed", 0)) + int(last.get("unknown", 0))
    print(f"\n- 终态失败+未知：{bad}")
    print(f"- 未提交历史：pending={last.get('h_pending', 0)} "
          f"needs_target={last.get('h_needs_target', 0)} "
          f"partial={last.get('h_partial', 0)}")
    print(f"- 载荷冲突：{last.get('payload_mismatch', 0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
