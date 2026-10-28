# -*- coding: utf-8 -*-
"""AstrBot 看门狗：22877 掉线 60 秒自动重启；单实例；带退避。

用法：
  python ops\\watchdog.py            # 常驻
  python ops\\watchdog.py --check    # 只检查一次
  python ops\\watchdog.py --once     # 掉线就重启一次，然后退出

停止：在 ops\\ 下建 watchdog.disabled，或结束进程。
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:/demo")
LOG = ROOT / "ops" / "watchdog.log"
DISABLED = ROOT / "ops" / "watchdog.disabled"
TASKS = ROOT / "ops" / "tasks.py"
PORT = 22877
LOCK_PORT = 22998
PY = sys.executable


def log(msg: str) -> None:
    line = f"[{datetime.now():%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def port_open(port: int = PORT) -> bool:
    with socket.socket() as s:
        s.settimeout(2.0)
        return s.connect_ex(("127.0.0.1", port)) == 0


def astrbot_running() -> bool:
    """端口没开但进程还在（启动中/事件循环卡住）时，先别急着重启。"""
    try:
        cmd = [
            "powershell", "-NoProfile", "-Command",
            "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
            "Where-Object {$_.CommandLine -like '*main.py*'} | "
            "Select-Object -First 1 -ExpandProperty ProcessId",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=12,
                           encoding="utf-8", errors="replace")
        return bool((r.stdout or "").strip())
    except Exception:
        return True  # 查不到就当在跑，避免 watchdog 自己制造重启风暴


def restart_running() -> bool:
    """已有 restart-astrbot 在跑时，不要叠加第二次重启。"""
    try:
        cmd = [
            "powershell", "-NoProfile", "-Command",
            "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
            "Where-Object {$_.CommandLine -like '*restart-astrbot*'} | "
            "Select-Object -First 1 -ExpandProperty ProcessId",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=12,
                           encoding="utf-8", errors="replace")
        return bool((r.stdout or "").strip())
    except Exception:
        return False


def restart() -> bool:
    env = dict(os.environ)
    env.setdefault("DASHBOARD_PORT", "22877")
    env.setdefault("DEMO_EVENTS", "1")
    env.setdefault("DEMO_EVENTS_SHADOW", "1")
    env.setdefault("DEMO_LIFE", "1")
    env.setdefault("DEMO_LIFE_SHADOW", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("NO_PROXY", "127.0.0.1,localhost,api.deepseek.com,api.minimaxi.com,api.minimax.chat")
    env.setdefault("no_proxy", env["NO_PROXY"])
    log("AstrBot 掉线，执行 restart-astrbot")
    try:
        r = subprocess.run(
            [PY, str(TASKS), "restart-astrbot"],
            cwd=str(ROOT), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180)
        tail = (r.stdout or "").strip().splitlines()[-3:]
        log("restart rc=%s %s" % (r.returncode, " | ".join(tail)))
        return r.returncode == 0
    except Exception as e:  # noqa: BLE001
        log(f"restart 失败: {type(e).__name__}: {e}")
        return False


def acquire_lock() -> socket.socket | None:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    if args.check:
        print("22877:", "open" if port_open() else "closed")
        return 0 if port_open() else 1

    if DISABLED.exists():
        log(f"watchdog.disabled 存在，退出")
        return 0
    if args.once:
        if port_open():
            log("22877 open，无需重启")
            return 0
        return 0 if restart() else 1

    lock = acquire_lock()
    if lock is None:
        log("已有 watchdog 在跑，退出")
        return 0
    log(f"watchdog 启动 pid={os.getpid()} port={PORT}")
    fails = 0
    attempts: list[float] = []
    closed_since: float | None = None
    while True:
        if DISABLED.exists():
            log("watchdog.disabled 出现，退出")
            return 0
        if port_open():
            if fails:
                log("22877 已恢复")
            fails = 0
            closed_since = None
            time.sleep(30)
            continue
        if closed_since is None:
            closed_since = time.time()
        if restart_running():
            log("restart-astrbot 正在跑，等它启动")
            fails = 0
            closed_since = None
            time.sleep(30)
            continue
        if astrbot_running():
            waited = int(time.time() - closed_since)
            if waited < 180:
                log(f"22877 closed but main.py running; wait {waited}s")
                fails = 0
                time.sleep(30)
                continue
            log("main.py running but 22877 closed >180s; force restart")
        fails += 1
        log(f"22877 closed（{fails}/2）")
        if fails >= 2:
            now = time.time()
            attempts = [t for t in attempts if now - t < 1800]
            if len(attempts) >= 3:
                log("30 分钟内已重启 3 次，退避 30 分钟")
                time.sleep(1800)
                attempts.clear()
                fails = 0
                closed_since = None
                continue
            restart()
            attempts.append(time.time())
            fails = 0
            closed_since = time.time()
            time.sleep(90)
        else:
            time.sleep(20)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
