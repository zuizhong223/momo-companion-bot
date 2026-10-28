# -*- coding: utf-8 -*-
"""B05：persona/config 指纹必须稳定，参数变化必须变。"""
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
sys.path.insert(0, str(ROOT))

from ops.eval.runner import run_fingerprint  # noqa: E402

PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def fp(**kw):
    base = dict(model="deepseek-chat", temp=0.7, rewrite=False, gate=False,
                gate_mode="shared", cases="cases.jsonl", limit=None, seed=42)
    base.update(kw)
    return run_fingerprint("配置原文", **base)


def main() -> int:
    p1, c1, cfg = fp()
    p2, c2, _ = fp()
    check(len(p1) == 64 and len(c1) == 64, "sha256 长度")
    check(p1 == p2 and c1 == c2, "同配置指纹稳定")
    check(cfg["gate_mode"] == "shared" and cfg["persona_sha256"] == p1, "config 字段")
    check(cfg["seed"] == 42, "config 含 seed")
    _, c3, _ = fp(temp=0.2)
    check(c3 != c1, "温度变了指纹变")
    _, c4, _ = fp(gate_mode="per_case")
    check(c4 != c1, "gate_mode 变了指纹变")
    _, c5, _ = fp(seed=7)
    check(c5 != c1, "seed 变了指纹变")
    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
