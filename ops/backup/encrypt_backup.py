# -*- coding: utf-8 -*-
"""把最新备份 zip 加密成 7z（AES-256，文件名也加密），可选复制到异地目录。

密码只从环境变量读，不落盘：
  $env:DEMO_BACKUP_PASS = "..."   # 自己保存好
  python ops\\backup\\encrypt_backup.py --offsite D:\\demo_offsite

7z 路径默认取 NapCat 自带的 7z.exe。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or Path(__file__).resolve().parents[2])
BACKUPS = ROOT / "backups"
SEVEN = Path(r"D:/demo/napcat\NapCat\7z.exe")


def latest_zip() -> Path | None:
    items = sorted(BACKUPS.glob("demo_backup_*.zip"), key=lambda p: p.stat().st_mtime)
    return items[-1] if items else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--offsite", default=None, help="加密后复制到这个目录")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    pw = os.environ.get("DEMO_BACKUP_PASS", "").strip()
    if not pw:
        print("缺少 DEMO_BACKUP_PASS；不生成加密包。")
        return 2
    if not SEVEN.exists():
        print(f"找不到 7z: {SEVEN}")
        return 2
    src = Path(args.zip) if args.zip else latest_zip()
    if not src or not src.exists():
        print("没有可加密的 zip")
        return 2
    out = Path(args.out) if args.out else src.with_suffix(".7z")
    cmd = [str(SEVEN), "a", "-t7z", "-mx=3", "-mhe=on", f"-p{pw}", str(out), str(src)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="ignore", timeout=600)
    if r.returncode != 0 or not out.exists():
        print("加密失败:", (r.stderr or r.stdout)[-400:])
        return 1
    chk = subprocess.run([str(SEVEN), "l", f"-p{pw}", str(out)],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="ignore", timeout=300)
    print(f"加密完成: {out} ({out.stat().st_size / 1e6:.1f} MB) list_rc={chk.returncode}")
    if args.offsite:
        dst = Path(args.offsite)
        dst.mkdir(parents=True, exist_ok=True)
        target = dst / out.name
        shutil.copy2(out, target)
        print(f"已复制异地副本: {target}")
    return 0 if chk.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
