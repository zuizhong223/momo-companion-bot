# -*- coding: utf-8 -*-
"""重新生成 requirements.lock 和 requirements.lock.linux。

什么时候跑：装完新插件、升级过包之后。跑完记得看一眼 diff（版本跳到哪了）。

用法：
  python ops\\deploy\\regen_lock.py            # 直接重写
  python ops\\deploy\\regen_lock.py --dry      # 只看会变成什么
"""
import argparse
import platform
import sys
from datetime import datetime
from importlib import metadata
from pathlib import Path

ROOT = Path(r"D:/demo")
LOCK = ROOT / "requirements.lock"
LOCK_LINUX = ROOT / "requirements.lock.linux"

# Linux 上装不了的 Windows 专用包
WINDOWS_ONLY = ("pywin32", "pywinpty", "comtypes", "pypiwin32", "winsdk", "pyreadline")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    pkgs = sorted(
        ((d.metadata["Name"], d.version) for d in metadata.distributions()
         if d.metadata["Name"]),
        key=lambda x: x[0].lower())
    header = (
        "# Python 依赖锁（自动生成，不要手改）\n"
        f"# 生成时间: {datetime.now():%Y-%m-%d %H:%M}\n"
        f"# Python: {sys.version.split()[0]}  平台: {platform.platform()}\n"
        f"# 来源: 实例 venv 实际安装的 {len(pkgs)} 个包\n"
        "# 用法: pip install -r requirements.lock\n"
        "# 检查漂移: python ops\\deploy\\check_lock.py\n")
    body = "\n".join(f"{n}=={v}" for n, v in pkgs)
    text = header + "\n" + body + "\n"

    linux = [f"{n}=={v}" for n, v in pkgs
             if n.lower().replace("_", "-") not in WINDOWS_ONLY]
    lheader = (
        "# Linux 版依赖锁（自动生成）\n"
        f"# 生成时间: {datetime.now():%Y-%m-%d %H:%M}\n"
        f"# 从 requirements.lock 去掉 Windows 专用包，共 {len(linux)} 个\n"
        "# 用法: pip install -r requirements.lock.linux "
        "-i https://pypi.tuna.tsinghua.edu.cn/simple "
        "--extra-index-url https://download.pytorch.org/whl/cpu\n")
    ltext = lheader + "\n" + "\n".join(linux) + "\n"

    print(f"Windows 锁: {len(pkgs)} 个包")
    print(f"Linux  锁: {len(linux)} 个包（去掉 {len(pkgs) - len(linux)} 个 Windows 专用）")
    if args.dry:
        print("（--dry：没有写入）")
        return 0
    LOCK.write_text(text, encoding="utf-8")
    LOCK_LINUX.write_text(ltext, encoding="utf-8")
    print(f"已写入 {LOCK.name} / {LOCK_LINUX.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
