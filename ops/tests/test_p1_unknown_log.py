# -*- coding: utf-8 -*-
"""P1-9：未知发送日志不能写完整正文。"""
import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="yy_unknown_log_"))
PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    spec = importlib.util.spec_from_file_location("butler_unknown_log", ROOT / "butler.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    mod.ROOT_DIR = TMP
    (TMP / "ops").mkdir(parents=True, exist_ok=True)
    mod._log_unknown("t1", [{"type": "plain", "text": "这是秘密正文"},
                            {"type": "record", "file": "x.wav"}], "timeout")
    p = TMP / "ops" / "send_unknown.log"
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    check(bool(text), "日志已写", text)
    check("秘密正文" not in text, "日志不含正文", text)
    check("plain" in text and "record" in text, "只记段类型", text)

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
