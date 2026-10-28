# -*- coding: utf-8 -*-
"""没有 Docker 也能做的 Dockerfile 预检。

检查四件事：
  1. Dockerfile 里每个 `COPY <src>` 的源在仓库里都存在（最常见的构建失败原因）；
  2. `.dockerignore` 确实排除了 `core/data`、`backups`、`.env`（否则镜像里有数据和密钥）；
  3. `CMD` 里的入口文件存在；
  4. 基础镜像是否固定了版本（禁止 `:latest`）。

用法：python ops\\docker\\preflight.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(r"D:/demo")
DOCKERFILE = ROOT / "Dockerfile"
IGNORE = ROOT / ".dockerignore"

MUST_IGNORE = ("core/data", "backups", ".env")


def parse_copies(text: str) -> list[str]:
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.upper().startswith("COPY "):
            continue
        parts = line.split()
        # COPY src... dest
        out.extend(p for p in parts[1:-1] if not p.startswith("--"))
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if not DOCKERFILE.exists():
        print("没有 Dockerfile")
        return 1
    text = DOCKERFILE.read_text(encoding="utf-8")
    ok = True

    print("1) COPY 源路径")
    for src in parse_copies(text):
        p = ROOT / src
        exists = p.exists()
        print(f"   {'OK  ' if exists else 'FAIL'} {src}")
        ok &= exists

    print("2) .dockerignore 是否排除敏感/数据目录")
    ign = IGNORE.read_text(encoding="utf-8") if IGNORE.exists() else ""
    for pat in MUST_IGNORE:
        hit = pat in ign
        print(f"   {'OK  ' if hit else 'FAIL'} {pat}")
        ok &= hit

    print("3) CMD 入口")
    m = re.search(r'CMD\s+\[(.*?)\]', text)
    if m:
        args = [a.strip().strip('"') for a in m.group(1).split(",")]
        entry = args[-1]
        # 容器里 WORKDIR 可能是 /app 后又 /app/core，取最后一个
        wds = re.findall(r'WORKDIR\s+(\S+)', text)
        wd = wds[-1] if wds else "/app"
        CORE_LOCAL = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
        if wd.rstrip("/").endswith("/core"):
            target = CORE_LOCAL / entry
        else:
            target = ROOT / entry
        exists = bool(target and target.exists())
        print(f"   {'OK  ' if exists else 'FAIL'} CMD {args} -> {target}")
        ok &= exists
    else:
        print("   FAIL 没找到 CMD")

    print("4) 基础镜像是否固定版本")
    m = re.search(r'^FROM\s+(\S+)', text, re.M)
    img = m.group(1) if m else ""
    pinned = bool(img) and ":latest" not in img and ":" in img
    print(f"   {'OK  ' if pinned else 'FAIL'} FROM {img}")
    ok &= pinned

    print("\nRESULT:", "PASS" if ok else "FAIL")
    print("  说明：这只是静态预检，真正构建仍需 docker。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
