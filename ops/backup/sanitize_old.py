# -*- coding: utf-8 -*-
"""清理旧备份里的明文密钥：解包 -> _scrub_secrets -> 重打包 -> 校验 -> 替换。

默认 dry-run；--apply 才动。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import zipfile
from pathlib import Path

ROOT = Path(r"D:/demo")
BACKUPS = ROOT / "backups"
TMP = BACKUPS / "_sanitize_tmp"
SECRET_NAMES = {".env", "local_keys.py", "deepseek_key.txt", "minimax_key.txt",
                "napcat_cred.txt"}


def load_scrub():
    spec = importlib.util.spec_from_file_location("yy_backup", ROOT / "ops/backup/backup.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._scrub_secrets


def _config_has_key(data: dict) -> bool:
    for src in data.get("provider_sources", []) or []:
        if src.get("key") and src.get("key") != ["REDACTED"] and src.get("key") != "REDACTED":
            return True
    for prov in data.get("provider", []) or []:
        for k in ("api_key", "embedding_api_key"):
            if prov.get(k) and prov.get(k) != "REDACTED":
                return True
    dash = data.get("dashboard")
    if isinstance(dash, dict) and dash.get("jwt_secret") not in (None, "REDACTED"):
        return True
    return False


def bad_entries(z: zipfile.ZipFile) -> list[str]:
    out = []
    for n in z.namelist():
        base = n.rsplit("/", 1)[-1]
        if base in SECRET_NAMES or base.endswith("_key.txt"):
            out.append(n)
        elif "ops/backup/keys_" in n:
            out.append(n)
        elif base.startswith("cmd_config") and base.endswith(".json"):
            try:
                data = json.loads(z.read(n).decode("utf-8-sig"))
            except Exception:  # noqa: BLE001
                out.append(n + " (无法解析)")
                continue
            if _config_has_key(data):
                out.append(n + " (含 key)")
    return out


def sanitize_one(path: Path, scrub) -> tuple[bool, str]:
    if TMP.exists():
        shutil.rmtree(TMP, ignore_errors=True)
    TMP.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as z:
        z.extractall(TMP)
    changes = scrub(TMP)
    out = path.with_name(path.stem + ".sanitized.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in TMP.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(TMP).as_posix())
    with zipfile.ZipFile(out) as z:
        if z.testzip() is not None:
            out.unlink(missing_ok=True)
            return False, "新包损坏"
    os.replace(out, path)
    shutil.rmtree(TMP, ignore_errors=True)
    return True, "; ".join(changes[:4]) + (f" …共 {len(changes)} 项" if len(changes) > 4 else "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only", default="", help="只处理这个文件名")
    args = ap.parse_args()
    scrub = load_scrub()
    files = sorted(BACKUPS.glob("demo_backup_*.zip"))
    if args.only:
        files = [p for p in files if p.name == args.only]
    todo = []
    for p in files:
        with zipfile.ZipFile(p) as z:
            bad = bad_entries(z)
        if bad:
            todo.append((p, bad))
    print(f"备份 {len(files)} 个，需清理 {len(todo)} 个")
    for p, bad in todo:
        print(f"- {p.name}: {len(bad)} 项，如 {bad[:2]}")
    if not args.apply:
        print("\n(dry-run；加 --apply 才清理)")
        return 0
    ok = 0
    for p, _ in todo:
        try:
            good, msg = sanitize_one(p, scrub)
        except Exception as e:  # noqa: BLE001
            good, msg = False, f"{type(e).__name__}: {e}"
        print(("OK   " if good else "FAIL ") + p.name + " | " + msg)
        ok += 1 if good else 0
    print(f"\n完成 {ok}/{len(todo)}")
    return 0 if ok == len(todo) else 1


if __name__ == "__main__":
    raise SystemExit(main())
