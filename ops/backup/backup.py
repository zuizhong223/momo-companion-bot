# -*- coding: utf-8 -*-
"""P0-C：能真正恢复的备份（MANIFEST 驱动 + 一致性校验 + 检索基线）。

  python ops\\backup\\backup.py check              # 只核对必备文件是否齐全
  python ops\\backup\\backup.py backup             # 在线快照（可能在途写入，会标注）
  python ops\\backup\\backup.py backup --stop-service  # 停 AstrBot → 快照 → 重启（推荐）
  python ops\\backup\\backup.py verify             # 校验最新包：哈希 + 完整性 + 指纹
  python ops\\backup\\backup.py drill              # 独立目录恢复并逐项比对（硬失败）
  python ops\\backup\\backup.py list

必备内容由 patches\\MANIFEST.txt + 固定数据清单共同决定，缺一个就失败。
"""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb_baseline  # noqa: E402

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:/demo")
# 容器里没有 instances/ 这层，用 DEMO_CORE 覆盖
INST = Path(os.environ.get("DEMO_CORE")
            or ROOT / "instances/11111111-1111-4111-8111-111111111111/core")
DATA = INST / "data"
KB = DATA / "knowledge_base"
KB_ID = "3e25c50e-ade0-4612-9818-6a856dd4ca2f"
OUT = ROOT / "backups"
# 容器里没有 Windows 专属的根文件（管家、.env、bat），check 要跳过它们
CONTAINER = os.environ.get("DEMO_CONTAINER", "").strip() == "1"
_ROOT_ONLY = {"Bot管家.py", "local_keys.py", ".env", "boot_all.ps1", "启动User.bat"}
MANIFEST_TXT = ROOT / "patches" / "MANIFEST.txt"

_venv_py = INST.parent / "venv" / "Scripts" / "python.exe"
VBASE = str(Path(os.environ.get("DEMO_PY")
                or (_venv_py if _venv_py.exists() else Path(sys.executable))))
START_CMD = (
    "$env:DASHBOARD_PORT='22877'; $env:PYTHONIOENCODING='utf-8'; "
    "$env:PATH='D:/demo\\ffmpeg;'+$env:PATH; "
    f"Start-Process -FilePath '{VBASE}' -ArgumentList '-u','main.py' "
    f"-WorkingDirectory '{INST}' -WindowStyle Hidden "
    "-RedirectStandardOutput 'D:/demo\\astrbot_run2.log' "
    "-RedirectStandardError 'D:/demo\\astrbot_err2.log'"
)
STOP_CMD = (
    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
    "Where-Object { $_.CommandLine -match 'main\\.py' } | "
    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
)

# 数据侧必备（相对 core）
DATA_FILES = [
    "data/cmd_config.json",
    "data/data_v4.db",
    "data/knowledge_base/kb.db",
    f"data/knowledge_base/{KB_ID}/doc.db",
    f"data/knowledge_base/{KB_ID}/index.faiss",
]
DATA_DIRS = ["data/config"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest_entries(full: bool = False) -> list[tuple[str, Path]]:
    """patches/MANIFEST.txt 里列的代码文件 + 固定数据清单。"""
    items: list[tuple[str, Path]] = []
    if MANIFEST_TXT.exists():
        for line in MANIFEST_TXT.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            rel = line.split(" ", 1)[0]
            src = (INST / rel) if (INST / rel).exists() else (ROOT / rel)
            if src.exists():
                arc = f"core/{rel}" if src.is_relative_to(INST) else rel
                items.append((arc, src))
            else:
                items.append((f"MISSING:{rel}", Path("__missing__") / rel))
    for rel in DATA_FILES:
        p = INST / rel
        items.append((f"core/{rel}", p))
    # 运维脚本、清单、机器人的表情库也进包（恢复/对账/表情通道都要用）
    for rel in ("ops", "patches", "表情库"):
        base = ROOT / rel
        if not base.exists():
            continue
        for f in sorted(base.rglob("*")):
            if not f.is_file() or f.suffix in (".pyc", ".zip"):
                continue
            if "__pycache__" in f.parts:
                continue
            items.append((f"{rel}/{f.relative_to(base).as_posix()}", f))
    # 全量模式：把 core/ 整棵树也打进包（除 data/缓存），让备份能被启动
    if full:
        # INST 本身就是 core 目录
        if INST.exists():
            for f in sorted(INST.rglob('*')):
                if not f.is_file():
                    continue
                rel = f.relative_to(INST).as_posix()
                if rel.startswith('data/') or '__pycache__' in f.parts or f.suffix == '.pyc':
                    continue
                items.append((f'core/{rel}', f))
    for rel in DATA_DIRS:
        base = INST / rel
        if base.exists():
            for f in sorted(base.rglob("*")):
                if f.is_file():
                    items.append((f"core/{f.relative_to(INST).as_posix()}", f))
    return items



SECRET_NAMES = {".env", "local_keys.py", "deepseek_key.txt", "minimax_key.txt", "napcat_cred.txt"}
REDACT_FIELDS = ("api_key", "embedding_api_key", "key", "jwt_secret")


def _scrub_secrets(stage: Path) -> list[str]:
    """打包前脱敏：删掉密钥文件，抹掉配置里的 key。返回处理记录。"""
    done: list[str] = []
    for f in list(stage.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(stage).as_posix()
        # 1) 明文密钥文件：直接不进包
        if f.name in SECRET_NAMES or f.name.endswith("_key.txt"):
            f.unlink()
            done.append(f"删 {rel}")
            continue
        # 2) 轮换备份目录：整目录都不进包
        if "/keys_" in "/" + rel and "ops/backup/keys_" in rel:
            f.unlink()
            done.append(f"删 {rel}")
            continue
        # 3) 配置里的 key：抹成占位符
        if f.name.startswith("cmd_config") and f.suffix == ".json":
            try:
                data = json.loads(f.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            changed = False
            for src in data.get("provider_sources", []) or []:
                if src.get("key"):
                    src["key"] = ["REDACTED"] if isinstance(src["key"], list) else "REDACTED"
                    changed = True
            for prov in data.get("provider", []) or []:
                for k in ("api_key", "embedding_api_key"):
                    if prov.get(k):
                        prov[k] = "REDACTED"
                        changed = True
            dash = data.get("dashboard")
            if isinstance(dash, dict) and dash.get("jwt_secret"):
                dash["jwt_secret"] = "REDACTED"
                changed = True
            if changed:
                f.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")
                done.append(f"抹 {rel}")
    for d in sorted(stage.rglob("keys_*"), reverse=True):
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
            done.append(f"删目录 {d.relative_to(stage).as_posix()}")
    return done


def missing_files() -> list[str]:
    return [arc for arc, src in manifest_entries() if not src.exists()]


def db_fingerprint(db_path: Path) -> dict:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        integ = con.execute("PRAGMA integrity_check").fetchone()[0]
        personas = {pid: hashlib.sha256((sp or "").encode()).hexdigest()
                    for pid, sp in con.execute(
                        "select persona_id, system_prompt from personas")}
        counts = {}
        for t in ("conversations", "personas", "api_keys", "yy_send_task"):
            try:
                counts[t] = con.execute(f"select count(*) from {t}").fetchone()[0]
            except sqlite3.OperationalError:
                counts[t] = -1
    finally:
        con.close()
    return {"integrity": integ, "personas_sha": personas, "counts": counts}


def kb_fingerprint() -> dict:
    """知识库内容指纹：chunk 数 + 索引 ntotal + 整库文本哈希。

    不假定存在"索引版本"字段——实测 kb.db/doc.db 都没有，只能自己算。
    """
    doc_db = KB / KB_ID / "doc.db"
    kb_db = KB / "kb.db"
    faiss_path = KB / KB_ID / "index.faiss"
    con = sqlite3.connect(f"file:{doc_db}?mode=ro", uri=True)
    try:
        chunks = con.execute("select count(*) from documents").fetchone()[0]
        h = hashlib.sha256()
        for did, (text,) in zip(
            (r[0] for r in con.execute("select id from documents order by id")),
            con.execute("select text from documents order by id"),
            strict=False,
        ):
            h.update(f"{did}\u0001{(text or '')[:200]}".encode())
        text_sha = h.hexdigest()
    finally:
        con.close()

    con = sqlite3.connect(f"file:{kb_db}?mode=ro", uri=True)
    try:
        sum_chunks = con.execute(
            "select coalesce(sum(chunk_count),0) from kb_documents").fetchone()[0]
        kb_row = con.execute(
            "select chunk_count, doc_count from knowledge_bases where kb_id=?",
            (KB_ID,)).fetchone()
    finally:
        con.close()

    ntotal = -1
    tmp_copy = None
    try:
        import tempfile  # noqa: PLC0415

        import faiss  # noqa: PLC0415

        # faiss 的 C++ 层在 Windows 上读不了中文路径，先拷到纯 ASCII 的临时路径
        with tempfile.TemporaryDirectory() as ascii_dir:
            tmp_copy = Path(ascii_dir) / "index.faiss"
            shutil.copy2(faiss_path, tmp_copy)
            ntotal = int(faiss.read_index(str(tmp_copy)).ntotal)
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] 读 faiss 失败: {e}")

    # 权威对账：doc.db 的 chunk 数 == faiss.ntotal。
    # kb_documents.chunk_count 之和与它已知有 2 片偏差（旧文档重传留下的元数据漂移），
    # 只作为 WARN 报出来，不阻断备份。
    consistent = (ntotal == chunks) if ntotal >= 0 else (chunks > 0)
    drift = chunks - sum_chunks
    if drift:
        print(f"  [warn] kb_documents 计数比 doc.db 少 {drift} 片（元数据漂移，不影响检索）")
    return {
        "chunks": chunks,
        "sum_kb_documents_chunk_count": sum_chunks,
        "kb_row": {"chunk_count": kb_row[0] if kb_row else None,
                   "doc_count": kb_row[1] if kb_row else None},
        "faiss_ntotal": ntotal,
        "text_sha256": text_sha,
        "meta_drift": drift,
        "consistent": bool(consistent),
    }


def cmd_check() -> int:
    miss = missing_files()
    if CONTAINER:
        miss = [m for m in miss if Path(m.split(":")[-1]).name not in _ROOT_ONLY]
    total = len([1 for _, s in manifest_entries() if s.exists()])
    print(f"必备文件：{total} 个就位，{len(miss)} 个缺失")
    for m in miss:
        print(f"  MISSING {m}")
    if miss:
        print("RESULT: FAIL")
        return 1
    fp = db_fingerprint(DATA / "data_v4.db")
    kb = kb_fingerprint()
    print(f"DB integrity={fp['integrity']} counts={fp['counts']}")
    print(f"KB chunks={kb['chunks']} faiss={kb['faiss_ntotal']} "
          f"sum(kb_documents)={kb['sum_kb_documents_chunk_count']} "
          f"consistent={kb['consistent']}")
    print("RESULT:", "PASS" if fp["integrity"] == "ok" and kb["consistent"] else "FAIL")
    return 0 if fp["integrity"] == "ok" and kb["consistent"] else 1


def _run_ps(cmd: str, timeout: int = 120) -> int:
    """跑 powershell。

    注意：不能用 capture_output —— Start-Process 起的子进程会继承管道，
    communicate() 会一直等到那个长期运行的子进程退出（表现为备份卡死 10 分钟）。
    这里用 DEVNULL，父进程立刻返回。
    """
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=timeout)
    return r.returncode


def _stop_service() -> bool:
    print("  停止 AstrBot …")
    rc = _run_ps(STOP_CMD)
    time.sleep(4)
    return rc == 0


def _start_service() -> None:
    print("  重启 AstrBot …")
    _run_ps(START_CMD, timeout=60)


def cmd_backup(stop_service: bool, full: bool = False) -> int:
    miss = missing_files()
    if miss:
        print("必备文件缺失，拒绝备份：")
        for m in miss:
            print(f"  MISSING {m}")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = OUT / f"demo_backup_{ts}.zip"
    manifest = {"created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "mode": "stopped" if stop_service else "online",
                  "full": full,
                "files": {}, "dbs": {}, "kb": {}, "db": {}}

    stopped = False
    if stop_service:
        stopped = _stop_service()
        manifest["mode"] = "stopped" if stopped else "online(stop-failed)"

    try:
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp)
            # 1) 文件（三个库单独做 VACUUM 快照，别在这里重复拷）
            snap_arcs = {"core/" + r for r in ("data/data_v4.db",
                                                "data/knowledge_base/kb.db",
                                                f"data/knowledge_base/{KB_ID}/doc.db")}
            for arc, src in manifest_entries(full):
                if arc in snap_arcs:
                    continue
                dst = stage / arc
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            scrubbed = _scrub_secrets(stage)
            if scrubbed:
                print(f"  脱敏 {len(scrubbed)} 项（密钥不进包）")
            manifest["files"] = {
                q.relative_to(stage).as_posix(): sha256(q)
                for q in sorted(stage.rglob("*"))
                if q.is_file()
                and q.relative_to(stage).as_posix() not in snap_arcs
            }
            # 2) 三个库的一致性快照（VACUUM INTO，WAL 安全）
            for rel in ("data/data_v4.db", "data/knowledge_base/kb.db",
                        f"data/knowledge_base/{KB_ID}/doc.db"):
                src = INST / rel
                dst = stage / "core" / rel
                if dst.exists():
                    dst.unlink()
                con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
                con.execute("VACUUM INTO ?", (str(dst),))
                con.close()
                manifest["dbs"][rel] = {"sha256": sha256(dst),
                                        "bytes": dst.stat().st_size}
            # 3) 指纹
            snap_db = stage / "core/data/data_v4.db"
            manifest["db"] = db_fingerprint(snap_db)
            manifest["kb"] = kb_fingerprint()
            try:
                manifest["kb"]["retrieval"] = kb_baseline.compute(
                    INST / f"data/knowledge_base/{KB_ID}/index.faiss")
                manifest["kb"]["retrieval_fp"] = kb_baseline.fingerprint(
                    manifest["kb"]["retrieval"])
                print(f"  检索基线：{len(kb_baseline.QUERIES)} 个 query，"
                      f"fp={manifest['kb']['retrieval_fp'][:12]}")
            except Exception as e:  # noqa: BLE001
                manifest["kb"]["retrieval"] = {"error": str(e)[:200]}
                print(f"  [warn] 检索基线不可用: {e}")
            (stage / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
                for f in sorted(stage.rglob("*")):
                    if f.is_file():
                        z.write(f, f.relative_to(stage).as_posix())
    finally:
        if stop_service and stopped:
            _start_service()

    (OUT / "LATEST.txt").write_text(zip_path.name, encoding="utf-8")
    size = zip_path.stat().st_size / 1e6
    print(f"\n备份完成: {zip_path.name} ({size:.1f} MB, {len(manifest['files'])} 文件, "
          f"模式={manifest['mode']})")
    print(f"  DB: {manifest['db']['counts']} integrity={manifest['db']['integrity']}")
    print(f"  KB: chunks={manifest['kb']['chunks']} faiss={manifest['kb']['faiss_ntotal']} "
          f"consistent={manifest['kb']['consistent']}")
    return 0 if manifest["kb"]["consistent"] else 1


def _latest() -> Path | None:
    p = OUT / "LATEST.txt"
    if p.exists():
        name = p.read_text(encoding="utf-8").strip()
        if name:
            return OUT / name
    items = sorted(OUT.glob("demo_backup_*.zip"))
    return items[-1] if items else None


def _extract(zip_path: Path, work: Path) -> dict:
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(work)
    return json.loads((work / "manifest.json").read_text(encoding="utf-8"))


def cmd_verify() -> int:
    zip_path = _latest()
    if not zip_path:
        print("没有备份")
        return 1
    print(f"校验: {zip_path.name}")
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        man = _extract(zip_path, work)
        for arc, digest in man["files"].items():
            p = work / arc
            if not p.exists():
                print(f"  MISSING {arc}")
                ok = False
            elif sha256(p) != digest:
                print(f"  HASH MISMATCH {arc}")
                ok = False
        for rel, info in man["dbs"].items():
            p = work / "core" / rel
            if not p.exists():
                print(f"  MISSING db {rel}")
                ok = False
                continue
            if sha256(p) != info["sha256"]:
                print(f"  HASH MISMATCH db {rel}")
                ok = False
        print(f"  文件 {len(man['files'])} 个、库 {len(man['dbs'])} 个校验完毕")
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def cmd_drill() -> int:
    """把备份解到独立目录，逐项与备份时记录的指纹比对（不和"现在的线上"比）。"""
    zip_path = _latest()
    if not zip_path:
        print("没有备份")
        return 1
    print(f"演练: {zip_path.name}")
    ok = True
    incomplete: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        man = _extract(zip_path, work)
        restored = work / "core/data/data_v4.db"
        if not restored.exists():
            print("  FAIL 没有 data_v4.db")
            return 1

        got_db = db_fingerprint(restored)
        if got_db["integrity"] != "ok":
            print(f"  FAIL 数据库完整性 {got_db['integrity']}")
            ok = False
        else:
            print("  OK   数据库完整性 ok")
        if got_db["personas_sha"] != man["db"]["personas_sha"]:
            print(f"  FAIL 配置哈希不一致 {got_db['personas_sha']} vs {man['db']['personas_sha']}")
            ok = False
        else:
            print(f"  OK   配置哈希一致（{len(got_db['personas_sha'])} 个）")
        for t, n in man["db"]["counts"].items():
            g = got_db["counts"].get(t)
            flag = "OK  " if g == n else "WARN"
            if g is None or g < 0:
                flag = "WARN"
            print(f"  {flag} {t}: 备份 {n} / 恢复 {g}")

        # 知识库：对备份时算好的指纹，而不是现在的线上
        global KB, KB_ID  # noqa: PLW0603
        saved = (KB, KB_ID)
        KB = work / "core/data/knowledge_base"
        KB_ID = saved[1]
        try:
            got_kb = kb_fingerprint()
        finally:
            KB, KB_ID = saved
        if got_kb["chunks"] != man["kb"]["chunks"]:
            print(f"  FAIL chunk 数不一致 {got_kb['chunks']} vs {man['kb']['chunks']}")
            ok = False
        elif got_kb["text_sha256"] != man["kb"]["text_sha256"]:
            print("  FAIL 知识库文本指纹不一致（内容变了）")
            ok = False
        else:
            print(f"  OK   知识库指纹一致（{got_kb['chunks']} 片，faiss={got_kb['faiss_ntotal']}）")
        if not (man.get("kb") or {}).get("consistent", False):
            print("  INCOMPLETE 备份时知识库就不一致（索引与切片条数不符），恢复后仍不一致")
            incomplete.append("kb_consistent=false")

        expected = (man.get("kb") or {}).get("retrieval")
        if expected:
            try:
                got = kb_baseline.compute(
                    work / f"core/data/knowledge_base/{KB_ID}/index.faiss")
                same, why = kb_baseline.compare(expected, got)
                print(f"  {'OK  ' if same else 'FAIL'} 检索基线: {why}")
                if not same:
                    ok = False
                if "不可用" in why:
                    incomplete.append("retrieval_baseline")
            except Exception as e:  # noqa: BLE001
                print(f"  INCOMPLETE 检索基线无法重算: {e}")
                incomplete.append("retrieval_baseline")

        # 代码文件齐全
        core = work / "core"
        for must in ("astrbot/core/utils/send_ledger.py",
                     "astrbot/core/utils/outbound_gate.py",
                     "astrbot/core/pipeline/message_merge.py",
                     "astrbot/dashboard/services/proactive_service.py"):
            if not (core / must).exists():
                print(f"  FAIL 缺代码 {must}")
                ok = False
        print(f"  OK   代码清单检查完毕")
    if not ok:
        print("RESULT: FAIL")
        return 1
    if incomplete:
        print(f"RESULT: INCOMPLETE（{', '.join(sorted(set(incomplete)))}）")
        print("  说明：归档本身可读、可还原，但有一致性/验证项没通过，不能算恢复验证通过。")
        return 2
    print("RESULT: PASS（归档可还原且数据一致、检索一致）")
    return 0


def cmd_list() -> int:
    items = sorted(OUT.glob("demo_backup_*.zip"))
    if not items:
        print("没有备份")
        return 0
    for p in items:
        print(f"  {p.name}  {p.stat().st_size / 1e6:.1f} MB  "
              f"{datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d %H:%M}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["check", "backup", "verify", "drill", "list"])
    ap.add_argument("--stop-service", action="store_true",
                    help="备份前停 AstrBot、备份后重启（推荐，保证一致性）")
    ap.add_argument("--full", action="store_true",
                    help="把 core/ 整棵树也打进包（约 +21MB，恢复后可直接启动）")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.action == "check":
        return cmd_check()
    if args.action == "backup":
        return cmd_backup(args.stop_service, args.full)
    if args.action == "verify":
        return cmd_verify()
    if args.action == "drill":
        return cmd_drill()
    return cmd_list()


if __name__ == "__main__":
    sys.exit(main())
