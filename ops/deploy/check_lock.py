# -*- coding: utf-8 -*-
"""检查「锁文件」和「实际装的东西」是否还一致。

为什么要这个：requirements.lock 是某一天的快照；装新插件、升级包之后它就不准了。
这个脚本只读，报告三类差异：
  1. 锁里有、环境里版本不同（drift）
  2. 环境里有、锁里没有（多装的）
  3. 锁里有、环境里没装（少装的）

用法：
  python ops\\deploy\\check_lock.py [--lock requirements.lock]
  退出码：0 一致 / 1 有差异
"""
import argparse
import re
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(r"D:/demo")


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_lock(path: Path) -> dict[str, str]:
    out = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;]+)", line)
        if m:
            out[norm(m.group(1))] = m.group(2)
    return out


def installed() -> dict[str, str]:
    out = {}
    for d in metadata.distributions():
        name = d.metadata["Name"]
        if name:
            out[norm(name)] = d.version
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lock", default=str(ROOT / "requirements.lock"))
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    lock_path = Path(args.lock)
    if not lock_path.exists():
        print(f"没有锁文件: {lock_path}")
        return 1
    lock = read_lock(lock_path)
    have = installed()
    print(f"锁: {lock_path.name}（{len(lock)} 个包）｜环境: {len(have)} 个包")

    drift = [(n, lock[n], have[n]) for n in lock if n in have and have[n] != lock[n]]
    extra = sorted(n for n in have if n not in lock)
    missing = sorted(n for n in lock if n not in have)

    for n, want, got in drift:
        print(f"  DRIFT   {n}: 锁 {want} → 实际 {got}")
    for n in extra:
        print(f"  EXTRA   {n}=={have[n]}（锁里没有）")
    for n in missing:
        print(f"  MISSING {n}=={lock[n]}（没装）")

    if not (drift or extra or missing):
        print("  OK 完全一致")
        return 0
    print(f"\n差异 {len(drift) + len(extra) + len(missing)} 处"
          f"（drift {len(drift)} / 多装 {len(extra)} / 少装 {len(missing)}）")
    print("要更新锁：python ops\\deploy\\regen_lock.py（会同时出 Windows 和 Linux 两份）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
