# -*- coding: utf-8 -*-
"""drill --full：在独立目录里重建一份可运行的 core，验证"补丁能 import + 检索能出同样结果"。

做法（不启动整个 AstrBot，不连 QQ）：
  1. 复制**当前实例的代码树**（core，不含 data）到 D:/demo/restore_test\\<ts>\\
  2. 把归档里的 core/** 覆盖上去（补丁以归档为准）
  3. 用归档里的 data/ 替换（配置、库、索引全部来自快照）
  4. 在独立目录里 import 每个补丁模块（用 PYTHONPATH 指向恢复目录）
  5. 用**归档里的配置**取 embedding key，对恢复出来的索引重算检索基线，和 manifest 比

退出码：0 通过 / 1 失败 / 2 不完整（例如基线不可比）
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

# 全部可用环境变量覆盖，这样容器里也能跑（容器里没有 F: 盘、也没有 venv）
#   DEMO_ROOT      项目根（默认 D:/demo）
#   DEMO_CORE      代码目录（默认 <root>/instances/<ID>/core；容器里传 /app/core）
#   DEMO_BACKUPS   备份目录（默认 <root>/backups）
#   DEMO_RESTORE   恢复演练目录（默认 <root>/restore_test；容器里可给 /tmp/restore_test）
#   DEMO_PY        用哪个解释器跑 import 自检（默认 <core>/../venv；容器里用 sys.executable）
ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:/demo")
CORE = Path(os.environ.get("DEMO_CORE")
            or ROOT / "instances/11111111-1111-4111-8111-111111111111/core")
OUT = Path(os.environ.get("DEMO_BACKUPS") or ROOT / "backups")
RESTORE_ROOT = Path(os.environ.get("DEMO_RESTORE") or ROOT / "restore_test")
KB_ID = "3e25c50e-ade0-4612-9818-6a856dd4ca2f"

_venv_py = CORE.parent / "venv" / "Scripts" / "python.exe"
PY = Path(os.environ.get("DEMO_PY") or (_venv_py if _venv_py.exists() else sys.executable))

PATCH_MODULES = [
    "astrbot.core.utils.send_ledger",
    "astrbot.core.utils.outbound_gate",
    "astrbot.core.utils.history_sanitize",
    "astrbot.core.pipeline.message_merge",
    "astrbot.core.event_bus",
    "astrbot.core.astr_main_agent",
    "astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event",
    "astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter",
    "astrbot.dashboard.services.proactive_service",
    "astrbot.dashboard.api.open_api",
    "astrbot.core.utils.history_exclude",
    "astrbot.core.utils.memory_facts",
    "astrbot.core.utils.mood_state",
    "astrbot.core.utils.tts_colloquial",
    "astrbot.core.pipeline.result_decorate.stage",
]


def latest_zip() -> Path | None:
    p = OUT / "LATEST.txt"
    if p.exists():
        name = p.read_text(encoding="utf-8").strip()
        if name and (OUT / name).exists():
            return OUT / name
    items = sorted(OUT.glob("demo_backup_*.zip"))
    return items[-1] if items else None


def copy_code(work: Path) -> None:
    def _ignore(_dir, names):
        return [n for n in names if n in ("data", "__pycache__", ".git")]

    shutil.copytree(CORE, work / "core", ignore=_ignore)


def overlay(work: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if name.endswith("/"):
                continue
            if not (name.startswith("core/") or name == "manifest.json"):
                continue
            z.extract(name, work)


def import_check(work: Path) -> tuple[bool, list[str]]:
    """在恢复目录里 import 每个补丁模块（子进程，隔离子环境）。"""
    code = (
        "import importlib, sys\n"
        f"mods = {PATCH_MODULES!r}\n"
        "bad = []\n"
        "for m in mods:\n"
        "    try:\n"
        "        importlib.import_module(m)\n"
        "    except Exception as e:\n"
        "        bad.append(f'{m}: {type(e).__name__}: {e}')\n"
        "print('BAD=' + repr(bad))\n"
    )
    env = {
        **os.environ,                      # 必须继承（否则没有 SystemRoot，winsock 初始化会失败）
        "PYTHONPATH": str(work / "core"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "DEMO_TEST": "1",
    }
    r = subprocess.run([str(PY), "-c", code], cwd=str(work / "core"),
                       capture_output=True, text=True, timeout=300,
                       env={**env}, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    bad_line = next((ln for ln in out.splitlines() if ln.startswith("BAD=")), "")
    try:
        bad = ast.literal_eval(bad_line[4:]) if bad_line else ["<no output>"]
    except Exception:  # noqa: BLE001
        bad = ["<无法解析 import 结果>"]
    return (not bad), bad


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kb_baseline

    zip_path = latest_zip()
    if not zip_path:
        print("没有备份")
        return 1

    # 只保留最近两份，避免每跑一次多占 60MB
    if RESTORE_ROOT.exists():
        olds = sorted((d for d in RESTORE_ROOT.iterdir() if d.is_dir()),
                      key=lambda d: d.name)
        for d in olds[:-1]:
            shutil.rmtree(d, ignore_errors=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    work = RESTORE_ROOT / ts
    work.mkdir(parents=True, exist_ok=True)
    print(f"drill --full: {zip_path.name}")
    print(f"  恢复目录: {work}")

    print("  1) 复制当前代码树 …")
    copy_code(work)
    print("  2) 覆盖归档里的补丁 …")
    overlay(work, zip_path)
    man = json.loads((work / "manifest.json").read_text(encoding="utf-8"))

    data = work / "core/data"
    ok = True
    incomplete: list[str] = []

    print("  3) 校验恢复出来的数据文件")
    for must in ("data_v4.db", "cmd_config.json",
                 f"knowledge_base/{KB_ID}/doc.db",
                 f"knowledge_base/{KB_ID}/index.faiss"):
        p = data / must
        exists = p.exists()
        print(f"     {'OK  ' if exists else 'FAIL'} {must}")
        ok = ok and exists

    print("  4) 在恢复目录里 import 补丁模块")
    good, bad = import_check(work)
    if good:
        print(f"     OK   {len(PATCH_MODULES)} 个模块全部 import 成功")
    else:
        ok = False
        for b in bad:
            print(f"     FAIL {b}")

    print("  5) 用归档配置重算检索基线")
    cfg = data / "cmd_config.json"
    index = data / f"knowledge_base/{KB_ID}/index.faiss"
    expected = (man.get("kb") or {}).get("retrieval") or {}
    try:
        got = kb_baseline.compute(index, config_path=cfg)
        same, why = kb_baseline.compare(expected, got)
        print(f"     {'OK  ' if same else 'FAIL'} {why}")
        if not same:
            ok = False
    except Exception as e:  # noqa: BLE001
        print(f"     INCOMPLETE 检索基线无法重算: {e}")
        incomplete.append("retrieval")

    if not (man.get("kb") or {}).get("consistent", False):
        print("     INCOMPLETE 备份时索引与切片就不一致，恢复后仍不一致")
        incomplete.append("kb_consistent=false")

    if not ok:
        print("RESULT: FAIL")
        return 1
    if incomplete:
        print(f"RESULT: INCOMPLETE（{', '.join(sorted(set(incomplete)))}）")
        return 2
    print("RESULT: PASS（独立目录可加载补丁 + 检索结果与基线一致）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
