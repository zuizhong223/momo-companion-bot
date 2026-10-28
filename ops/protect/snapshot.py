# -*- coding: utf-8 -*-
"""第 0 步 0.1：修改前的回退点。

不是"完整备份系统"（那是 P0-C），只是"改坏了能立刻退回去"：
  1. 按实际改动清单复制文件到 backups\\pre_fix_<ts>\\
  2. VACUUM INTO 一份 data_v4.db 快照
  3. 记录 personas.system_prompt 的 sha256
  4. 生成 patches\\MANIFEST.txt（P0-C 的备份脚本按它核对完整性）

用法：
  python ops\\protect\\snapshot.py           # 预览
  python ops\\protect\\snapshot.py --apply   # 真正复制
"""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:/demo")
INST = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
OUT_ROOT = ROOT / "backups"
MANIFEST_TXT = ROOT / "patches" / "MANIFEST.txt"

# 实例内被改过的文件（相对 core）
INST_FILES = [
    "astrbot/core/utils/outbound_gate.py",
    "astrbot/core/utils/history_sanitize.py",
    "astrbot/core/utils/send_ledger.py",
    "astrbot/core/pipeline/message_merge.py",
    "astrbot/core/event_bus.py",
    "astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py",
    "astrbot/core/platform/sources/aiocqhttp/aiocqhttp_message_event.py",
    "astrbot/core/platform/sources/aiocqhttp/aiocqhttp_platform_adapter.py",
    "astrbot/core/provider/sources/minimax_tts_api_source.py",
    "astrbot/core/provider/sources/minimax_embedding_source.py",
    "astrbot/core/provider/manager.py",
    "astrbot/dashboard/services/proactive_service.py",
    "astrbot/dashboard/api/open_api.py",
    "astrbot/core/astr_main_agent.py",
    "astrbot/dashboard/services/open_api_service.py",
    "astrbot/dashboard/schemas.py",
    # 本轮新增的模块（之前是手工补进清单的，重生成会丢——这次写进来源列表）
    "astrbot/core/utils/history_exclude.py",
    "astrbot/core/utils/memory_facts.py",
    "astrbot/core/utils/mood_state.py",
    "astrbot/core/utils/life_state.py",
    "astrbot/core/utils/tts_colloquial.py",
    "astrbot/core/pipeline/result_decorate/stage.py",
    "data/cmd_config.json",
]
INST_DIRS = ["data/config"]

ROOT_FILES = [
    "Bot管家.py",
    "local_keys.py",
    ".env",
    "boot_all.ps1",
    "启动User.bat",
    "handoff_内部.md",
    "变更记录_20261025.md",
    "未完成清单_定稿_20261025.md",
]
ROOT_DIRS = ["ops"]
SKIP_DIRS = {"__pycache__", ".git"}
SKIP_SUFFIX = {".pyc"}
DB = INST / "data/data_v4.db"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect() -> list[tuple[str, Path]]:
    """返回 [(zip 内相对路径, 源路径)]"""
    items: list[tuple[str, Path]] = []
    for rel in INST_FILES:
        p = INST / rel
        if p.exists():
            items.append((f"core/{rel}", p))
        else:
            print(f"  [warn] 实例文件不存在: {rel}")
    for rel in INST_DIRS:
        base = INST / rel
        if base.exists():
            for f in sorted(base.rglob("*")):
                if f.is_file() and f.suffix not in SKIP_SUFFIX:
                    items.append((f"core/{f.relative_to(INST).as_posix()}", f))
    for rel in ROOT_FILES:
        p = ROOT / rel
        if p.exists():
            items.append((rel, p))
        else:
            print(f"  [warn] 项目文件不存在: {rel}")
    for rel in ROOT_DIRS:
        base = ROOT / rel
        if not base.exists():
            continue
        for f in sorted(base.rglob("*")):
            if not f.is_file() or f.suffix in SKIP_SUFFIX:
                continue
            if any(part in SKIP_DIRS for part in f.parts):
                continue
            items.append((f"{rel}/{f.relative_to(base).as_posix()}", f))
    return items


def persona_sha() -> str:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    row = con.execute(
        "select system_prompt from personas where persona_id='ajing'").fetchone()
    con.close()
    return hashlib.sha256((row[0] or "").encode()).hexdigest() if row else ""


def _old_manifest_entries() -> list[tuple[str, Path]]:
    """读旧清单里已有的条目（只保留文件仍存在的）。

    为什么需要：来源列表 INST_FILES/ROOT_FILES 是手工维护的，别处新增的补丁文件
    不在里面；重生成时会把它们**静默丢掉**（10-26 就丢过 5 个，10-27 从 35 掉到 28）。
    这里改成"和旧清单取并集"，只增不减。
    """
    out: list[tuple[str, Path]] = []
    if not MANIFEST_TXT.exists():
        return out
    for raw in MANIFEST_TXT.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            continue
        rel = parts[0]
        for base in (INST, ROOT):
            cand = base / rel
            if cand.exists() and cand.is_file():
                out.append((f"core/{rel}" if base is INST else rel, cand))
                break
    return out


def write_manifest_txt(items: list[tuple[str, Path]]) -> None:
    MANIFEST_TXT.parent.mkdir(parents=True, exist_ok=True)
    # 并集：新算出来的 + 旧清单里还在的
    seen = {rel for rel, _ in items}
    for rel, src in _old_manifest_entries():
        if rel not in seen:
            items.append((rel, src))
            seen.add(rel)
    lines = ["# 实例/项目里被改动过的文件（备份与升级前按此清单核对）",
             f"# 生成于 {datetime.now():%Y-%m-%d %H:%M:%S}",
             "# 格式：<相对 core 或项目根的路径> <sha256>"]
    for rel, src in items:
        if rel.startswith("ops/") or rel in ("handoff_内部.md", "变更记录_20261025.md",
                                             "未完成清单_定稿_20261025.md"):
            continue
        clean = rel[5:] if rel.startswith("core/") else rel
        lines.append(f"{clean} {sha256(src)}")
    MANIFEST_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--manifest-only", action="store_true", help="只重生成 patches/MANIFEST.txt")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    items = collect()
    total = sum(p.stat().st_size for _, p in items)
    print(f"待保护：{len(items)} 个文件，{total / 1e6:.2f} MB")
    for rel, _ in items[:8]:
        print(f"   {rel}")
    print(f"   ...（共 {len(items)}）")

    if args.manifest_only:
        write_manifest_txt(items)
        print(f"已重写 {MANIFEST_TXT}")
        return 0
    if not args.apply:
        print("\n未加 --apply，仅预览。")
        return 0

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = OUT_ROOT / f"pre_fix_{ts}"
    out.mkdir(parents=True, exist_ok=True)

    manifest = {"created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "root": str(ROOT), "persona_sha256": persona_sha(), "files": {}}
    for rel, src in items:
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        manifest["files"][rel] = {"sha256": sha256(dst), "bytes": dst.stat().st_size}

    snap = out / "data_v4.db"
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.execute("VACUUM INTO ?", (str(snap),))
    integ = con.execute("PRAGMA integrity_check").fetchone()[0]
    con.close()
    manifest["db_snapshot"] = {"file": "data_v4.db", "bytes": snap.stat().st_size,
                               "integrity": integ, "sha256": sha256(snap)}

    write_manifest_txt(items)
    (out / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n回退点：{out}")
    print(f"  文件 {len(manifest['files'])} 个，DB 快照 {snap.stat().st_size / 1e6:.2f} MB "
          f"(integrity={integ})")
    print(f"  persona sha256: {manifest['persona_sha256'][:16]}…")
    print(f"  清单：{MANIFEST_TXT}（{sum(1 for _ in MANIFEST_TXT.read_text(encoding='utf-8').splitlines() if not _.startswith('#'))} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
