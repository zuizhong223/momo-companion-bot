# -*- coding: utf-8 -*-
"""S10：日志脱敏。"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(r"D:\demo")
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("demo_redact", ROOT / "butler.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

PASS = True


def check(cond, label, extra=""):
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    cases = [
        ("key sk-" + "a" * 30, "sk-***"),
        ("Authorization: Bearer " + "b" * 30, "Bearer ***"),
        ("token=" + "c" * 20, "token=***"),
    ]
    for raw, expect in cases:
        out = mod._redact(raw)
        check("sk-" + "a" * 30 not in out and expect in out, expect, out)
    check("sk-" + "a" * 30 not in mod._redact("normal xxx"), "无关文本不误伤",
          mod._redact("normal xxx"))
    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
