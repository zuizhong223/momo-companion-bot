# -*- coding: utf-8 -*-
"""A2 影子探针：开关、闸门、失败退避、日志写入（全部离线）。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

PASS = True


def check(cond, name, extra=""):
    global PASS
    print(("  OK   " if cond else "  FAIL ") + name + (f" | {extra}" if extra and not cond else ""))
    if not cond:
        PASS = False


def main() -> int:
    from astrbot.core.utils import serious_probe as sp

    print("\n[1] 开关解析")
    for v, want in (("off", "off"), ("shadow", "shadow"), ("on", "on"),
                    ("", "shadow"), ("乱写", "shadow")):
        os.environ["DEMO_SERIOUS_LLM"] = v
        check(sp.mode() == want, f"{v!r} -> {want}", sp.mode())

    print("\n[2] 闸门：什么句子值得问")
    check(not sp.worth_probe("在吗"), "太短不问")
    check(sp.worth_probe("我在想要不要搬出去住"), "够长就问")
    check(not sp.worth_probe("关心你"), "短轻松不打扰")

    print("\n[3] 调用失败必须退避（不抛异常、返回 None）")
    os.environ["DEMO_SERIOUS_LLM_URL"] = "http://127.0.0.1:9/v1/chat/completions"
    t0 = time.time()
    got = sp.classify("我在想要不要搬出去住", timeout=0.5)
    cost = time.time() - t0
    check(got is None, "失败返回 None", str(got))
    check(cost < 3, "没有卡住", f"{cost:.2f}s")

    print("\n[4] shadow 只写日志、不抛异常")
    log = Path(tempfile.mkdtemp()) / "probe.jsonl"
    os.environ["DEMO_SERIOUS_LOG"] = str(log)
    sp.shadow("用户妈妈好像不太认可我", "banter")
    for _ in range(40):
        if log.exists() and log.read_text(encoding="utf-8").strip():
            break
        time.sleep(0.05)
    ok = log.exists() and log.read_text(encoding="utf-8").strip()
    check(bool(ok), "日志已写入", "无内容")
    if ok:
        rec = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
        check(rec.get("regex") == "banter", "记录了正则判定", str(rec))
        check("llm" in rec, "记录了模型判定", str(rec))

    for k in ("DEMO_SERIOUS_LLM", "DEMO_SERIOUS_LLM_URL", "DEMO_SERIOUS_LOG"):
        os.environ.pop(k, None)

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
