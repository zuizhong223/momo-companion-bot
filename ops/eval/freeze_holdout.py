#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""U6 · 冻结 holdout：把用例文件锁成"只用于发布门禁、不再调参"的版本。

用法：
  python ops\\eval\\freeze_holdout.py --cases holdout_v2_20261026.jsonl   # 生成 .freeze.json
  python ops\\eval\\freeze_holdout.py --check holdout_v2_20261026.jsonl   # 校验是否被改过

冻结文件内容：版本、用例数、每条的 id 与输入哈希、整文件 sha256、时间、使用规则。
一旦冻结，**改题目会让 --check 失败**；需要新题就新建一个版本文件。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:/demo")
EVAL = ROOT / "ops/eval"

POLICY = (
    "本集只用于发布门禁，不参与调参；题目一经冻结不得修改；"
    "发布结论必须引用本集完整一次运行记录。"
)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load(cases: Path) -> list[dict]:
    return [json.loads(l) for l in cases.read_text(encoding="utf-8").splitlines() if l.strip()]


def freeze(cases: Path) -> int:
    rows = load(cases)
    ids = [r["id"] for r in rows]
    if len(ids) != len(set(ids)):
        print("id 重复，拒绝冻结")
        return 1
    raw = cases.read_text(encoding="utf-8")
    meta = {
        "version": cases.stem,
        "frozen_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(rows),
        "file": cases.name,
        "sha256": sha(raw),
        "policy": POLICY,
        "cases": [{"id": r["id"], "input_sha": sha(r.get("input", ""))} for r in rows],
    }
    out = cases.with_suffix(".freeze.json")
    out.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已冻结 {cases.name}: {len(rows)} 条 -> {out.name}")
    print(f"  sha256={meta['sha256'][:16]}…")
    return 0


def check(cases: Path) -> int:
    meta_path = cases.with_suffix(".freeze.json")
    if not meta_path.exists():
        print("没有冻结文件，先运行 --cases")
        return 1
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    raw = cases.read_text(encoding="utf-8")
    ok = True
    if sha(raw) != meta["sha256"]:
        print("FAIL 用例文件已被修改（sha256 不符）")
        ok = False
    rows = {r["id"]: r for r in load(cases)}
    if len(rows) != meta["count"]:
        print(f"FAIL 条数变化：{meta['count']} -> {len(rows)}")
        ok = False
    for item in meta["cases"]:
        r = rows.get(item["id"])
        if not r:
            print(f"FAIL 缺少用例 {item['id']}")
            ok = False
        elif sha(r.get("input", "")) != item["input_sha"]:
            print(f"FAIL 题目被改：{item['id']}")
            ok = False
    print("RESULT:", "PASS（未改动）" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", required=True)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    cases = args.cases if Path(args.cases).is_absolute() else EVAL / args.cases
    if not cases.exists():
        print("找不到:", cases)
        return 1
    return check(cases) if args.check else freeze(cases)


if __name__ == "__main__":
    sys.exit(main())
