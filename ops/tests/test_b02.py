# -*- coding: utf-8 -*-
"""B02 回归：shared 共用会话流，per_case 每题独立 gate key。"""
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
sys.path.insert(0, str(ROOT))

from ops.eval.runner import _gate_key  # noqa: E402

PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def main() -> int:
    check(_gate_key("shared", "d01") == "eval:main", "shared 共用")
    check(_gate_key("shared", "d02") == "eval:main", "shared 不同题也共用")
    check(_gate_key("per_case", "d01") == "eval:single:d01", "per_case d01")
    check(_gate_key("per_case", "d02") == "eval:single:d02", "per_case d02")
    check(_gate_key("per_case", "d01") != _gate_key("per_case", "d02"),
          "per_case 两题不共享")
    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
