#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""U2 · 认真模式触发判定（20 条边界句）。

判据（P1.5/U2）：边界句的 precision/recall 可接受，且真实认真对话能进入认真模式。
本脚本只测 detect_mode()，不调模型、不发消息。

用法：
  python ops\\eval\\check_serious_trigger.py
  python ops\\eval\\check_serious_trigger.py --file ops/eval/serious_trigger_20.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:/demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(ROOT / "ops/eval/serious_trigger_20.jsonl"))
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    from astrbot.core.utils.style_profile import detect_mode  # noqa: PLC0415

    cases = [json.loads(line) for line in Path(args.file).read_text(encoding="utf-8").splitlines()
             if line.strip()]
    tp = fp = fn = tn = 0
    bad = []
    for c in cases:
        want_serious = c["label"] == "serious"
        pred = detect_mode(c["text"])
        got_serious = pred == "serious"
        if want_serious and got_serious:
            tp += 1
        elif want_serious and not got_serious:
            fn += 1
            bad.append(("漏判", c["id"], c["text"], pred))
        elif not want_serious and got_serious:
            fp += 1
            bad.append(("误判", c["id"], c["text"], pred))
        else:
            tn += 1

    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    print(f"样本 {len(cases)} 条：serious {tp + fn} / 非 serious {tn + fp}")
    print(f"  TP={tp} FP={fp} FN={fn} TN={tn}")
    print(f"  precision={prec:.2f}  recall={rec:.2f}")
    if bad:
        print("  错例：")
        for kind, cid, text, pred in bad:
            print(f"    [{kind}] {cid} {text!r} -> {pred}")
    else:
        print("  无错例")
    return 0 if (prec >= 0.85 and rec >= 0.80) else 1


if __name__ == "__main__":
    sys.exit(main())
