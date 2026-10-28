# -*- coding: utf-8 -*-
"""生成 Docker 精简构建目录（避开仓库里 15GB 无关内容）。

产物：D:/demo\\build
  core/     ← instances/<ID>/core（去掉 data、__pycache__、*.pyc）
  ops/      ← ops（去掉 __pycache__、*.npy）
  patches/  ← 补丁包
  Dockerfile.slim、requirements.lock

用法：python ops\\deploy\\make_build_ctx.py [目标目录]
"""
import shutil
import sys
from pathlib import Path

ROOT = Path(r"D:/demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
TARGET = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"D:/demo\build")

IGNORE = shutil.ignore_patterns("data", "__pycache__", "*.pyc", "*.npy", "*.log")
LOCK_FILES = ("Dockerfile.slim", "requirements.lock", "requirements.lock.linux")


def _validated_target(target: Path) -> Path:
    """拒绝项目根/源码/祖先；只允许项目树之外的明确构建目录。"""
    resolved = target.resolve()
    project = ROOT.resolve()
    forbidden = {
        project,
        CORE.resolve(),
        (ROOT / "ops").resolve(),
        (ROOT / "patches").resolve(),
    }
    if resolved in forbidden or resolved in project.parents or project in resolved.parents:
        raise SystemExit(f"拒绝清理危险目标: {resolved}")
    return resolved


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    target = _validated_target(TARGET)
    missing = [n for n in LOCK_FILES if not (ROOT / n).is_file()]
    if missing:
        raise SystemExit("缺少构建输入: " + ", ".join(missing))
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    shutil.copytree(CORE, target / "core", ignore=IGNORE)
    shutil.copytree(ROOT / "ops", target / "ops", ignore=IGNORE)
    shutil.copytree(ROOT / "patches", target / "patches", ignore=IGNORE)
    for name in LOCK_FILES:
        shutil.copy2(ROOT / name, target / name)
    shutil.copy2(ROOT / "Dockerfile.slim", target / "Dockerfile")

    files = [p for p in target.rglob("*") if p.is_file()]
    size = sum(p.stat().st_size for p in files)
    print(f"构建目录: {target}")
    print(f"  {len(files)} 个文件，{size / 1e6:.1f} MB")
    print("  next: cd 到这个目录，docker build -t demo:4.29.1 .")
    return 0


if __name__ == "__main__":
    sys.exit(main())
