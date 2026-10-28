# -*- coding: utf-8 -*-
"""P2-17：一台机器上的常用动作收在一处（make 目标的实现体）。

没有 make 也能用：
  python ops\\tasks.py status
  python ops\\tasks.py test
  python ops\\tasks.py check
  python ops\\tasks.py backup
  python ops\\tasks.py drill
  python ops\\tasks.py drill-full
  python ops\\tasks.py restart-astrbot
  python ops\\tasks.py restart-butler
  python ops\\tasks.py patches

有 make 的话：`make status|test|check|backup|drill|restart`
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"D:/demo")

FLAGS_FILE = ROOT / "ops" / "config" / "env_defaults.json"


def _load_flags() -> dict[str, str]:
    """唯一开关来源：ops/config/env_defaults.json（只补缺省，不覆盖已有环境变量）。"""
    try:
        raw = json.loads(FLAGS_FILE.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return {str(k): str(v) for k, v in raw.items() if not str(k).startswith("_")}


def _env_with_defaults() -> dict[str, str]:
    env = dict(os.environ)
    for k, v in _load_flags().items():
        env.setdefault(k, v)
    return env


def _log_flags(env: dict[str, str]) -> None:
    try:
        path = ROOT / "ops" / "deploy" / "flags.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        import re as _re
        secret = _re.compile(r"PASS|KEY|TOKEN|SECRET|PASSWORD", _re.I)
        keys = sorted(k for k in env if k.startswith("DEMO_"))
        line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + " ".join(
            f"{k}={'***' if secret.search(k) else env[k]}" for k in keys)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

INST = ROOT / "instances/11111111-1111-4111-8111-111111111111"
CORE = INST / "core"
PY = INST / "venv/Scripts/python.exe"
DB = CORE / "data/data_v4.db"
LOG = ROOT / "astrbot_run2.log"
TESTS = ["test_gate", "test_merge", "test_tz", "test_ledger",
         "test_history", "test_history_race", "test_voice_history", "test_proactive",
         "test_life_relations_fix"]


def _run(args: list[str], **kw) -> int:
    print(f"$ {' '.join(str(a) for a in args)}")
    return subprocess.call([str(a) for a in args], **kw)


def _port_open(port: int) -> bool:
    import socket

    with socket.socket() as s:
        s.settimeout(1.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _established(port: int) -> int:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"(Get-NetTCPConnection -LocalPort {port} -State Established "
         "-ErrorAction SilentlyContinue | Measure-Object).Count"],
        capture_output=True, text=True, timeout=30)
    try:
        return int((r.stdout or "0").strip() or 0)
    except ValueError:
        return -1


def _counts() -> dict:
    if not DB.exists():
        return {}
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return {
            "tasks": dict(con.execute(
                "select state, count(*) from yy_send_task group by state").fetchall()),
            "history": dict(con.execute(
                "select history_state, count(*) from yy_send_task group by history_state"
            ).fetchall()),
            "excluded": con.execute("select count(*) from yy_history_exclude").fetchone()[0],
        }
    except sqlite3.OperationalError:
        return {}
    finally:
        con.close()


def cmd_status() -> int:
    print("=== 端口 ===")
    for p in (22877, 6199, 11434):
        print(f"  {p}: {'open' if _port_open(p) else 'closed'}")
    print(f"=== QQ 连接: {_established(6199)} ===")
    c = _counts()
    if c:
        print(f"=== 发送台账: {c['tasks']} ===")
        print(f"=== 历史提交: {c['history']}　排除: {c['excluded']} 条 ===")
    if LOG.exists():
        errs = sum(1 for ln in LOG.read_text(encoding="utf-8", errors="ignore").splitlines()
                   if "[ERRO]" in ln or "ERROR" in ln)
        print(f"=== 日志 ERRO 行数: {errs} ===")
    return 0


def cmd_test(repeat: int = 1) -> int:
    """repeat>1 时连跑多遍，用来验证稳定性（偶发失败会暴露出来）。"""
    all_bad: dict[str, int] = {}
    for rnd in range(1, repeat + 1):
        bad = []
        if repeat > 1:
            print(f"=== 第 {rnd}/{repeat} 遍 ===")
        for name in TESTS:
            r = subprocess.run([str(PY), str(ROOT / f"ops/tests/{name}.py")],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=1800)
            line = next((ln for ln in (r.stdout or "").splitlines()
                         if ln.startswith("RESULT")), "(no result)")
            print(f"  {name:<22} {line}")
            if "PASS" not in line:
                bad.append(name)
                all_bad[name] = all_bad.get(name, 0) + 1
        print(f"  → {len(TESTS) - len(bad)}/{len(TESTS)} 通过"
              + (f"；失败: {bad}" if bad else ""))
    if repeat > 1:
        runs = repeat
        print(f"\n稳定性：{runs} 遍 × {len(TESTS)} 套")
        if all_bad:
            print(f"  不稳定: {all_bad}（共 {runs} 遍）")
            return 1
        print(f"  全部 {runs} 遍都通过")
        return 0
    bad = [n for n, c in all_bad.items()]
    print(f"\n{len(TESTS) - len(bad)}/{len(TESTS)} 通过" + (f"；失败: {bad}" if bad else ""))
    return 0 if not bad else 1


def cmd_backup(extra: list[str]) -> int:
    return _run([PY, ROOT / "ops/backup/backup.py", "backup", *extra])


def cmd_drill() -> int:
    return _run([PY, ROOT / "ops/backup/backup.py", "drill"])


def cmd_drill_full() -> int:
    return _run([PY, ROOT / "ops/backup/drill_full.py"])


def cmd_check() -> int:
    return _run([PY, ROOT / "ops/backup/backup.py", "check"])


def cmd_patches() -> int:
    return _run([PY, ROOT / "ops/patches/patches.py", "verify"])


def _kill(match: str) -> None:
    # 启动管家可能用 python.exe，也可能用 pythonw.exe；两种都要杀。
    ps = ("Get-CimInstance Win32_Process -Filter "
          "\"Name='python.exe' OR Name='pythonw.exe'\" | "
          f"Where-Object {{ $_.CommandLine -match '{match}' }} | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    subprocess.call(["powershell", "-NoProfile", "-Command", ps], timeout=60)


def _pids_on_port(port: int) -> list[int]:
    """按监听端口找 PID：比命令行正则精确，不会误杀别家的 main.py。"""
    ps = ("Get-NetTCPConnection -LocalPort %d -State Listen -ErrorAction SilentlyContinue | "
          "Select-Object -ExpandProperty OwningProcess -Unique" % port)
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=30,
                             creationflags=0x08000000)
    except Exception:
        return []
    return [int(x) for x in (out.stdout or "").split() if x.strip().isdigit()]


def _kill_port(port: int) -> bool:
    pids = _pids_on_port(port)
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True,
                           timeout=30, creationflags=0x08000000)
        except Exception:
            pass
    return bool(pids)


def _kill_pidfile(pid_file: Path, expect: str) -> bool:
    """按 PID 文件杀，并核对命令行包含 expect，避免 PID 复用误杀。"""
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except Exception:
        return False
    ps = ("Get-CimInstance Win32_Process -Filter \"ProcessId=%d\" | "
          "Select-Object -ExpandProperty CommandLine" % pid)
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=30,
                             creationflags=0x08000000)
    except Exception:
        return False
    if expect not in (out.stdout or ""):
        return False
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True,
                       timeout=30, creationflags=0x08000000)
    except Exception:
        pass
    return True


def _acquire_restart_lock(timeout: float = 180.0) -> Path | None:
    """重启互斥锁：两个会话同时 restart 会撞端口（06:09/06:53 各出过 ERRO）。"""
    lock = ROOT / "ops" / "restart.lock"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(f"{os.getpid()} {time.time():.0f}\n")
            return lock
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except OSError:
                age = 0
            if age > 120:            # 超过 2 分钟的锁当成残留
                lock.unlink(missing_ok=True)
                continue
            print(f"  另一个重启正在进行（锁 {age:.0f}s），等 5 秒…")
            time.sleep(5)
    return None


def cmd_restart_astrbot() -> int:
    lock = _acquire_restart_lock()
    if lock is None:
        print("等锁超时：另一个会话一直在重启，放弃本次（不重复启动，避免抢端口）")
        return 1
    try:
        return _restart_astrbot_locked()
    finally:
        lock.unlink(missing_ok=True)


def _restart_astrbot_locked() -> int:
    if not _kill_port(22877):
        _kill(r"-u main\.py")
    time.sleep(4)
    env = _env_with_defaults()
    env.update(DASHBOARD_PORT="22877", PYTHONIOENCODING="utf-8",
               PATH=str(ROOT / "ffmpeg") + os.pathsep + env.get("PATH", ""))
    _log_flags(env)
    with (LOG).open("a", encoding="utf-8") as out, \
            (ROOT / "astrbot_err2.log").open("a", encoding="utf-8") as err:
        subprocess.Popen([str(PY), "-u", "main.py"], cwd=str(CORE), env=env,
                         stdout=out, stderr=err, creationflags=0x08000000)
    print("等 45 秒…")
    time.sleep(45)
    return cmd_status()


def cmd_restart_butler() -> int:
    if not _kill_pidfile(ROOT / "Bot管家.pid", "Bot管家"):
        _kill("Bot管家")
    time.sleep(3)
    # 只删 pid 展示文件；.pid.lock 是 msvcrt 的锁文件，绝不能删。
    # 旧进程若没被杀掉，保留锁文件能让新进程安全地退出，而不是双实例。
    (ROOT / "Bot管家.pid").unlink(missing_ok=True)
    with (ROOT / "Bot管家_stdout.log").open("w", encoding="utf-8") as out, \
            (ROOT / "Bot管家_err.log").open("w", encoding="utf-8") as err:
        subprocess.Popen([str(PY), "-u", "Bot管家.py"], cwd=str(ROOT),
                         stdout=out, stderr=err, creationflags=0x08000000)
    time.sleep(12)
    log = ROOT / "Bot管家.log"
    if log.exists():
        for ln in log.read_text(encoding="utf-8", errors="ignore").splitlines()[-3:]:
            print("  " + ln)
    return 0


def cmd_daily() -> int:
    return _run([PY, ROOT / "ops/alerts/daily.py"])


def cmd_lock() -> int:
    return _run([PY, ROOT / "ops/deploy/check_lock.py"])


def cmd_docker_check() -> int:
    """容器里跑 check（数据用副本，不碰线上）"""
    d = Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")
    if not d.exists():
        print("没有 docker")
        return 1
    copy = Path(r"D:/demo\data_copy")
    src = ROOT / "instances/11111111-1111-4111-8111-111111111111/core/data"
    if not copy.exists():
        print(f"先复制一份数据到 {copy}（不碰线上）")
        shutil.copytree(src, copy)
    return _run([d, "run", "--rm",
                 "-v", f"{copy}:/app/core/data",
                 "-e", "DEMO_ROOT=/app", "-e", "DEMO_CORE=/app/core",
                 "-e", "DEMO_CONTAINER=1",
                 "demo:4.29.1", "python", "/app/ops/backup/backup.py", "check"])


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("status", "drill", "drill-full", "check", "patches",
                 "restart-astrbot", "restart-butler", "lock", "docker-check", "daily"):
        sub.add_parser(name)
    p_test = sub.add_parser("test")
    p_backup = sub.add_parser("backup")
    p_backup.add_argument("extra", nargs="*")
    p_test.add_argument("--repeat", type=int, default=1,
                        help="连跑 N 遍，验证稳定性（默认 1）")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    table = {
        "status": cmd_status, "test": cmd_test, "drill": cmd_drill,
        "drill-full": cmd_drill_full, "check": cmd_check, "patches": cmd_patches,
        "restart-astrbot": cmd_restart_astrbot,
        "restart-butler": cmd_restart_butler,
        "daily": cmd_daily,
        "lock": cmd_lock,
        "docker-check": cmd_docker_check,
    }
    if args.cmd == "backup":
        return cmd_backup(args.extra)
    if args.cmd == "test":
        return cmd_test(getattr(args, "repeat", 1) or 1)
    return table[args.cmd]()


if __name__ == "__main__":
    sys.exit(main())
