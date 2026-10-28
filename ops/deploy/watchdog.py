# -*- coding: utf-8 -*-
"""崩溃 watchdog：连续两次巡检发现 AstrBot/QQ 端口都不在，才跑一次 boot_all。

设计成“很保守”：
  - 每 5 分钟一次；连续 2 次（约 10 分钟）都 down 才动手；
  - 15 分钟冷却，避免重启风暴；
  - 只调用已有 boot_all.ps1，不自己拼启动命令；
  - 状态写 ops/alerts/watchdog_state.json，日志写 ops/alerts/watchdog.log。

用法：
  python ops\\deploy\\watchdog.py --once
  python ops\\deploy\\watchdog.py --once --dry-run
  python ops\\deploy\\watchdog.py --status
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import os

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

ROOT = Path(r"D:/demo")
STATE = ROOT / "ops/alerts/watchdog_state.json"
LOG = ROOT / "ops/alerts/watchdog.log"
BOOT = ROOT / "boot_all.ps1"
PORTS = (22877, 6199)
LOG_PATH = Path(r"D:/demo\astrbot_run2.log")


def scan_errors(st: dict) -> tuple[int, str]:
    """返回 (本次新增 ERRO 数, 最后一条 ERRO 摘要)；同时更新 st 里的基线。"""
    try:
        size = LOG_PATH.stat().st_size if LOG_PATH.exists() else 0
    except OSError:
        return 0, ""
    if size < int(st.get("log_size") or 0):
        # 日志被截断（重启）→ 重新计数
        st["erro_total"] = 0
    try:
        text = LOG_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0, ""
    total = text.count("[ERRO]") + text.count("[ERROR]")
    prev = int(st.get("erro_total") or 0)
    delta = max(0, total - prev)
    st["erro_total"] = total
    st["log_size"] = size
    last = ""
    if delta:
        for ln in reversed(text.splitlines()):
            if "[ERRO]" in ln or "[ERROR]" in ln:
                last = ln.replace("\x1b", "")[:160]
                break
    return delta, last
THRESHOLD = 2
COOLDOWN = 15 * 60


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def disk_free_gb() -> float | None:
    """系统盘（Windows 所在的 C:）剩余 GB；查不到返回 None。"""
    import shutil

    try:
        import os

        system_drive = os.environ.get("SystemDrive", "C:") + os.sep
        return round(shutil.disk_usage(system_drive).free / 1e9, 1)
    except Exception:  # noqa: BLE001
        return None


def notify(title: str, message: str) -> None:
    """弹桌面通知；失败只记日志，不能让 watchdog 崩。"""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from notify import toast  # noqa: PLC0415

        if not toast(title, message):
            log(f"通知发送失败：{title}")
    except Exception as e:  # noqa: BLE001
        log(f"通知异常：{type(e).__name__}: {e}")


def butler_alive() -> bool:
    """管家进程在不在（它的命令行里有「管家」）。"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -match '管家' }).Count"],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
        return int((r.stdout or "0").strip() or 0) > 0
    except Exception:  # noqa: BLE001
        return False         # 查不到按“没活着”处理；外层有连续 2 次 + 冷却兜底


def qq_connections() -> int | None:
    """6199 上 established 连接数；查不到返回 None。

    端口 open 只说明 AstrBot 在听；QQ 掉登录时 NapCat 根本不连进来，
    所以这个数才是"机器人还能不能说话"的直接指标。
    """
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-NetTCPConnection -LocalPort 6199 -State Established "
             "-ErrorAction SilentlyContinue | Measure-Object).Count"],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
        return int((r.stdout or "0").strip() or 0)
    except Exception:  # noqa: BLE001
        return None


def decide(ok: bool, failures: int, last_restart: float, now: float,
           threshold: int = THRESHOLD, cooldown: float = COOLDOWN) -> tuple[str, int]:
    """返回 (action, failures)。action: ok / wait / restart / cooldown。"""
    if ok:
        return "ok", 0
    failures += 1
    if failures < threshold:
        return "wait", failures
    if now - last_restart < cooldown:
        return "cooldown", failures
    return "restart", failures


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"failures": 0, "last_restart": 0.0, "last_check": 0.0}


def save_state(st: dict) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STATE)
    except Exception:  # noqa: BLE001
        pass


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:  # noqa: BLE001
        pass
    print(line.strip())


def run_once(dry: bool = False) -> int:
    st = load_state()
    now = time.time()
    ok = all(port_open(p) for p in PORTS)
    erro_delta, erro_last = scan_errors(st)
    if erro_delta >= 3:
        log(f"WARN 日志新增 {erro_delta} 条 ERRO：{erro_last[:120]}")
        if not dry:
            notify("Bot · 日志出现异常",
                   f"5 分钟内新增 {erro_delta} 条 ERRO：{erro_last[:120]}")

    free_gb = disk_free_gb()
    st["disk_free_gb"] = free_gb
    if free_gb is not None and free_gb < 5.0:
        st["disk_low_streak"] = int(st.get("disk_low_streak") or 0) + 1
        if st["disk_low_streak"] == 2:
            log(f"WARN 系统盘剩余 {free_gb}GB（<5GB），连续 {st['disk_low_streak']} 次")
            if not dry:
                notify("Bot · 磁盘快满了", f"C 盘只剩 {free_gb}GB，清一下临时文件/镜像")
    else:
        st["disk_low_streak"] = 0

    qq = qq_connections()
    butler = butler_alive()
    st["butler_alive"] = butler
    st["butler_down_streak"] = (0 if butler
                                else int(st.get("butler_down_streak") or 0) + 1)
    if st["butler_down_streak"] == 2:
        log("WARN 管家进程不在（连续 2 次），重新拉起")
        if not dry:
            try:
                subprocess.Popen(
                    [str(BOOT.parent / "instances/11111111-1111-4111-8111-111111111111"
                         / "venv" / "Scripts" / "python.exe"), "-u", "\u6708\u6708\u7ba1\u5bb6.py"],
                    cwd=str(BOOT.parent), creationflags=0x08000000)
                notify("Bot · 管家已重启", "管家进程不在，已自动拉起")
            except Exception as e:  # noqa: BLE001
                log(f"管家拉起失败：{e}")
                notify("Bot · 管家挂了", f"自动拉起失败：{e}")
    elif st["butler_down_streak"] > 2 and st["butler_down_streak"] % 12 == 0:
        notify("Bot · 管家仍未恢复",
               f"已 {st['butler_down_streak'] * 5} 分钟没管家进程，需要人看")
    qq_down = qq == 0
    st["qq_conn"] = qq
    st["qq_down_streak"] = (int(st.get("qq_down_streak") or 0) + 1) if qq_down else 0
    if st["qq_down_streak"] == 2:
        log(f"WARN QQ 未连接（6199 无 established，连续 {st['qq_down_streak']} 次）"
            "——可能需要扫码登录，重启无效")
        if not dry:
            notify("Bot · QQ 掉线了", "需要扫码登录（重启没用）。"
                                      "二维码在 NapCat 的 cache\\qrcode.png")
    elif st["qq_down_streak"] > 2 and st["qq_down_streak"] % 12 == 0:
        log(f"WARN QQ 仍未连接（已 {st['qq_down_streak']} 次检查 ≈"
            f"{st['qq_down_streak'] * 5} 分钟）")

    action, failures = decide(ok, int(st.get("failures") or 0),
                              float(st.get("last_restart") or 0.0), now)
    st["failures"] = failures
    st["last_check"] = now
    st["last_action"] = action
    if action == "restart":
        log(f"连续 {failures} 次 down，触发 boot_all.ps1（dry={dry}）")
        if not dry:
            try:
                subprocess.Popen(
                    ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                     "-File", str(BOOT)], creationflags=_NO_WINDOW,
                    cwd=str(ROOT),
                )
                st["last_restart"] = now
            except Exception as e:  # noqa: BLE001
                log(f"启动失败：{e}")
                st["last_error"] = str(e)
            if not dry:
                notify("Bot · 服务掉了", "连续 2 次检测为 down，已自动跑 boot_all.ps1")
    elif action == "ok":
        if qq_down:
            log(f"端口 ok / QQ 未连接（第 {st['qq_down_streak']} 次）")
        else:
            log("ok")
    elif action == "wait":
        log(f"端口 down（第 {failures} 次），先观察")
    else:
        log(f"端口 down，但还在冷却期（{int(now - float(st.get('last_restart') or 0))}s）")
    save_state(st)
    return 0 if action in ("ok", "wait", "cooldown") else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    if args.status:
        st = load_state()
        st["ports"] = {str(p): port_open(p) for p in PORTS}
        print(json.dumps(st, ensure_ascii=False, indent=1))
        return 0
    if not args.once:
        ap.print_help()
        return 2
    return run_once(dry=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
