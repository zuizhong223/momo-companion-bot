# -*- coding: utf-8 -*-
"""P2-3：跟踪文件里不允许出现明文 key；只报文件名/模式，不打印值。"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# 明显的测试占位符，不当密钥处理
String_QUOTES = '\"\''
PLACEHOLDER = re.compile(r"^(tok|test|fake|dummy|xxx|placeholder|sample)[-_]", re.I)

PATTERNS = {
    "sk": re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    "bearer": re.compile(r"Bearer\s+[A-Za-z0-9._-]{12,}"),
    "token": re.compile(r"token\s*=\s*['\"][^'\"]{12,}"),
    "password": re.compile(r"password\s*=\s*['\"][^'\"]{6,}"),
}
SKIP_FILES = {"version_list.json"}  # 更新日志正文含 "Bearer" 示例，不是凭据


def git() -> str:
    exe = shutil.which("git") or r"C:\Program Files\Git\cmd\git.exe"
    if not Path(exe).exists():
        raise SystemExit("git not found")
    return exe


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    out = subprocess.run([git(), "-C", str(ROOT), "ls-files"],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    if out.returncode != 0:
        print("git ls-files failed")
        return 1
    hits = []
    for rel in out.stdout.splitlines():
        if Path(rel).name in SKIP_FILES:
            continue
        p = ROOT / rel
        if not p.is_file() or p.stat().st_size > 2_000_000:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            continue
        for name, rx in PATTERNS.items():
            for m in rx.finditer(text):
                val = m.group(0)
                if '=' in val:
                    val = val.split('=', 1)[1].strip().strip(String_QUOTES)
                if PLACEHOLDER.match(val):
                    continue
                hits.append((rel, name))
                break
    if hits:
        print(f"FAIL：{len({h[0] for h in hits})} 个 tracked 文件含密钥形态")
        for rel, name in hits[:20]:
            print(f"  {rel} [{name}]")
        return 1
    print("OK：tracked 文件密钥扫描 0 命中")
    return 0


if __name__ == "__main__":
    sys.exit(main())
