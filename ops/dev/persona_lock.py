#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""配置单改权：同一时间只允许一个人改配置。

为什么要有它：2026-10-27 两个人同时改配置，改动混在一起，
"像机器人掉了 0.61"最后证明是噪声——因为文件在实验中途被改了。

用法：
  python ops\\dev\\persona_lock.py status
  python ops\\dev\\persona_lock.py acquire --who 我 --why "改决策表"
  python ops\\dev\\persona_lock.py release
  python ops\\dev\\persona_lock.py acquire --who 我 --force      # 覆盖过期锁
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import platform
import sys
from pathlib import Path

ROOT = Path(r"D:/demo")
# 允许环境变量覆盖，方便测试时用临时文件
PERSONA = Path(os.environ.get("DEMO_PERSONA_FILE") or ROOT / "persona.txt")
LOCK = Path(os.environ.get("DEMO_PERSONA_LOCK") or ROOT / "ops/persona.lock")
HISTORY = Path(os.environ.get("DEMO_PERSONA_HISTORY") or ROOT / "ops/persona_history.jsonl")
BASELINE = Path(os.environ.get("DEMO_PERSONA_BASELINE") or ROOT / "ops/eval/reports/bench_base_1027.json")
STALE_HOURS = 4


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def persona_sha() -> str:
    try:
        return sha(PERSONA.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return "<读不到>"


def read_lock() -> dict | None:
    if not LOCK.exists():
        return None
    try:
        return json.loads(LOCK.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"who": "?", "why": "（锁文件损坏）", "since": ""}


def age_hours(since: str) -> float | None:
    try:
        t = datetime.datetime.fromisoformat(since)
        return (datetime.datetime.now() - t).total_seconds() / 3600
    except Exception:  # noqa: BLE001
        return None


def baseline_sha() -> str | None:
    try:
        return json.loads(BASELINE.read_text(encoding="utf-8"))["persona"]["file_sha"]
    except Exception:  # noqa: BLE001
        return None


def cmd_status() -> int:
    lock = read_lock()
    cur = persona_sha()
    base = baseline_sha()
    print(f"配置当前 sha: {cur}")
    print(f"基准分数对应 sha: {base or '（没有基准记录）'}")
    if base and cur != base:
        print("  ⚠ 配置和基准不一致：改过之后没有跑过 bench，任何和历史分数的比较都不可信")
    if lock:
        h = age_hours(lock.get("since", ""))
        stale = "（已过期，可 --force 覆盖）" if h and h > STALE_HOURS else ""
        print(f"锁: 被 {lock.get('who')} 持有，{lock.get('since')}"
              f"（{h:.1f} 小时前）{stale}")
        print(f"  理由：{lock.get('why')}")
        print(f"  加锁时配置 sha: {lock.get('sha')}")
    else:
        print("锁: 空闲")
    return 0


def cmd_acquire(who: str, why: str, force: bool) -> int:
    lock = read_lock()
    if lock and not force:
        h = age_hours(lock.get("since", ""))
        if h is not None and h > STALE_HOURS:
            print(f"发现过期锁（{lock.get('who')}，{h:.1f} 小时前）。确认没人改的话加 --force")
            return 1
        print(f"已被 {lock.get('who')} 持有（{lock.get('since')}）：{lock.get('why')}")
        print("别动。等用户 release，或确认用户不在后加 --force")
        return 1
    data = {"who": who, "why": why,
            "since": datetime.datetime.now().isoformat(timespec="seconds"),
            "sha": persona_sha(), "host": platform.node(), "pid": os.getpid()}
    LOCK.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已加锁：{who} — {why}")
    print("改完记得跑 bench，再 release：")
    print("  python ops\\eval\\bench.py --cases holdout_v2_20261026.jsonl --runs 3")
    print("  python ops\\dev\\persona_lock.py release")
    return 0


def cmd_release() -> int:
    lock = read_lock()
    if not lock:
        print("本来就没锁")
        return 0
    before = lock.get("sha")
    after = persona_sha()
    changed = before != after
    rec = {"at": datetime.datetime.now().isoformat(timespec="seconds"),
           "who": lock.get("who"), "why": lock.get("why"),
           "sha_before": before, "sha_after": after, "changed": changed}
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    LOCK.unlink(missing_ok=True)
    print(f"已释放（{lock.get('who')}）。配置{'有' if changed else '无'}变化：{before} -> {after}")
    if changed:
        print("提醒：跑过 bench 了吗？没跑的话，这次改动没有对应的分数。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    pa = sub.add_parser("acquire")
    pa.add_argument("--who", required=True)
    pa.add_argument("--why", default="")
    pa.add_argument("--force", action="store_true")
    sub.add_parser("release")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.cmd == "status":
        return cmd_status()
    if args.cmd == "acquire":
        return cmd_acquire(args.who, args.why, args.force)
    return cmd_release()


if __name__ == "__main__":
    sys.exit(main())
