# -*- coding: utf-8 -*-
"""P2-14：把补丁做成"可搬家的一包"——导出、校验、升级后一键回贴。

AstrBot 升级会覆盖掉所有改过的文件。这里的思路很土但可靠：
  `patches/MANIFEST.txt` 记录「路径 + sha256」，
  `patches/files/` 存这些文件的原样副本；
  升级后 `apply` 按清单把文件贴回去，并逐个核对哈希。

子命令：
  export  —— 按 MANIFEST 把当前文件复制到 patches/files/
  verify  —— 只核对（不写），列出缺失/哈希不符
  apply   —— 贴回实例（默认只贴哈希对得上的；--force 允许覆盖不符的）

用法：
  python ops\\patches\\patches.py export
  python ops\\patches\\patches.py verify
  python ops\\patches\\patches.py apply [--force]
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(r"D:/demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
MANIFEST = ROOT / "patches/MANIFEST.txt"
STORE = ROOT / "patches/files"

# 不该进补丁包的：密钥（备份包里另存）与构建产物
SKIP_SUFFIX = (".npy",)
SKIP_NAMES = {".env"}


def _skippable(rel: str) -> bool:
    name = Path(rel).name
    return name in SKIP_NAMES or name.endswith(SKIP_SUFFIX)


def resolve(rel: str, base: Path = CORE) -> Path:
    """清单里的路径有的相对 core，有的相对项目根——先找 core，再找根。"""
    p = base / rel
    if p.exists():
        return p
    q = ROOT / rel
    return q if q.exists() else p


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def read_manifest() -> list[tuple[str, str]]:
    out = []
    for raw in MANIFEST.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) == 2:
            out.append((parts[0], parts[1]))
    return out


def cmd_export() -> int:
    items = read_manifest()
    ok = 0
    for rel, want in items:
        src = resolve(rel)
        if not src.exists():
            print(f"  缺失 {rel}")
            continue
        got = sha256(src)
        if got != want:
            print(f"  哈希不符（清单旧了）{rel}")
        if _skippable(rel):
            print(f"  跳过（不入补丁包） {rel}")
            continue
        dst = STORE / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        ok += 1
    skipped = len(items) - ok
    print(f"导出 {ok} 个文件（跳过 {skipped} 个：密钥/构建产物）-> {STORE}")
    return 0 if skipped >= 0 and ok + skipped == len(items) else 1


def cmd_verify(store: Path = STORE, core: Path = CORE, quiet: bool = False) -> int:
    items = read_manifest()
    bad = []
    for rel, want in items:
        if _skippable(rel):
            continue
        src = store / rel
        if not src.exists():
            bad.append((rel, "包里没有"))
            continue
        got = sha256(src)
        if got != want:
            bad.append((rel, f"包内哈希不符 {got[:8]}≠{want[:8]}"))
    live_bad = []
    for rel, want in items:
        live = resolve(rel, core)
        if not live.exists():
            live_bad.append((rel, "实例里没有"))
        elif sha256(live) != want:
            live_bad.append((rel, "实例里已被改过"))
    if not quiet:
        for rel, why in bad:
            print(f"  包 {why}: {rel}")
        for rel, why in live_bad:
            print(f"  实例 {why}: {rel}")
        checked = len(items) - sum(1 for rel, _ in items if _skippable(rel))
        print(f"包内 {checked - len(bad)}/{checked} 完好；"
              f"实例里 {checked - len(live_bad)}/{checked} 与清单一致"
              f"（另有 {len(items) - checked} 个是密钥/产物，不入包）")
    return 0 if not bad and not live_bad else 1


def cmd_apply(force: bool = False) -> int:
    items = read_manifest()
    rc = cmd_verify(quiet=True)
    applied = skipped = 0
    for rel, want in items:
        if _skippable(rel):
            continue
        src = STORE / rel
        if not src.exists():
            print(f"  跳过（包里没有）: {rel}")
            skipped += 1
            continue
        if sha256(src) != want and not force:
            print(f"  跳过（包内哈希不符）: {rel}")
            skipped += 1
            continue
        live = resolve(rel)
        if live.exists() and sha256(live) == sha256(src):
            skipped += 1
            continue
        live.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, live)
        applied += 1
        print(f"  已贴回 {rel}")
    print(f"贴回 {applied} 个，跳过 {skipped} 个")
    if rc != 0:
        print("警告：verify 有失败项（见上）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("export")
    sub.add_parser("verify")
    p_apply = sub.add_parser("apply")
    p_apply.add_argument("--force", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.cmd == "export":
        return cmd_export()
    if args.cmd == "verify":
        return cmd_verify()
    return cmd_apply(force=args.force)


if __name__ == "__main__":
    sys.exit(main())
