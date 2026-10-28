# -*- coding: utf-8 -*-
"""配置单改权：加锁/抢占/过期覆盖/释放留痕（全用临时文件）。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
TOOL = ROOT / "ops/dev/persona_lock.py"
PASS = True


def check(cond, name, extra=""):
    global PASS
    print(("  OK   " if cond else "  FAIL ") + name + (f" | {extra}" if extra and not cond else ""))
    if not cond:
        PASS = False


def run(env, *args):
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True,
                          text=True, encoding="utf-8", env=env, cwd=str(ROOT))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="yy_lock_"))
    persona = tmp / "persona.txt"
    persona.write_text("配置 v1", encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8",
           "DEMO_PERSONA_FILE": str(persona),
           "DEMO_PERSONA_LOCK": str(tmp / "lock.json"),
           "DEMO_PERSONA_HISTORY": str(tmp / "hist.jsonl"),
           "DEMO_PERSONA_BASELINE": str(tmp / "base.json")}

    print("\n[1] 初始 status：无锁")
    r = run(env, "status")
    check("锁: 空闲" in r.stdout, "空闲", r.stdout[-80:])

    print("\n[2] 加锁")
    r = run(env, "acquire", "--who", "A", "--why", "改决策表")
    check(r.returncode == 0 and (tmp / "lock.json").exists(), "锁文件已写")

    print("\n[3] 别人抢锁被拒")
    r = run(env, "acquire", "--who", "B", "--why", "硬抢")
    check(r.returncode == 1 and "已被 A 持有" in r.stdout, "拒绝 B", r.stdout[:80])

    print("\n[4] 过期锁 + --force 可覆盖")
    lock = json.loads((tmp / "lock.json").read_text(encoding="utf-8"))
    lock["since"] = "2020-01-01T00:00:00"
    (tmp / "lock.json").write_text(json.dumps(lock, ensure_ascii=False), encoding="utf-8")
    r = run(env, "acquire", "--who", "B", "--why", "接手")
    check(r.returncode == 1 and "过期锁" in r.stdout, "提示过期", r.stdout[:80])
    r = run(env, "acquire", "--who", "B", "--why", "接手", "--force")
    check(r.returncode == 0, "force 成功", r.stdout[:60])

    print("\n[5] release 留痕并记录是否改动")
    persona.write_text("配置 v2", encoding="utf-8")
    r = run(env, "release")
    check(r.returncode == 0 and "有变化" in r.stdout, "识别到变化", r.stdout[:80])
    hist = (tmp / "hist.jsonl")
    ok = hist.exists() and json.loads(hist.read_text(encoding="utf-8").splitlines()[-1])["changed"]
    check(ok, "历史记录 changed=true")

    print("\n[6] 锁已释放")
    check(not (tmp / "lock.json").exists(), "锁文件已删")

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
