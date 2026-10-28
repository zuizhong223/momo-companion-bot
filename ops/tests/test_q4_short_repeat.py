# -*- coding: utf-8 -*-
"""Q4：同语境短口头禅连续复读要换一句。"""
import sys
from pathlib import Path

ROOT = Path(r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

from astrbot.core.platform.sources.aiocqhttp import aiocqhttp_message_event as ev  # noqa: E402

PASS = True


def check(cond, label, extra=""):
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    key = "q4-test"
    ev._SEM_CTX.pop(key, None)
    ev._sem_record_user(key, "我关心你了")
    ev._sem_record_bot(key, "好好好", "我关心你了")
    out, why = ev._semantic_guard("好好好", "我关心你了", key, "support")
    check(out != "好好好" and why, "同语境复读被换", f"{out} {why}")

    ev._SEM_CTX.pop(key, None)
    ev._sem_record_user(key, "今天吃什么")
    ev._sem_record_bot(key, "好好好", "今天吃什么")
    out2, _ = ev._semantic_guard("好好好", "今天天气不错", key, "support")
    check(out2 == "好好好", "不同语境不误杀", out2)
    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
