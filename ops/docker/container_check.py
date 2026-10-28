# -*- coding: utf-8 -*-
"""容器内自检：补丁模块能否 import + 能否读到挂载进来的真实数据。

为什么不用 drill_full.py：它里面写死了 Windows 路径（D:/demo\\...），
容器里没有那个路径。这里改成"数据从 /app/core/data 读"，不依赖宿主路径。

用法（容器里）：
  python /app/ops/docker/container_check.py
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

DATA = Path(os.environ.get("DEMO_DATA", "/app/core/data"))
KB_ID = "3e25c50e-ade0-4612-9818-6a856dd4ca2f"

MODULES = [
    "astrbot.core.utils.send_ledger",
    "astrbot.core.utils.history_exclude",
    "astrbot.core.utils.memory_facts",
    "astrbot.core.utils.mood_state",
    "astrbot.core.utils.tts_colloquial",
    "astrbot.core.utils.outbound_gate",
    "astrbot.core.pipeline.message_merge",
    "astrbot.core.event_bus",
    "astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event",
    "astrbot.dashboard.services.proactive_service",
]

PASS = True


def check(ok: bool, label: str, extra: str = "") -> None:
    global PASS
    PASS = PASS and ok
    print(f"  {'OK  ' if ok else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"data: {DATA}  exists={DATA.exists()}")
    print(f"python: {sys.version.split()[0]}")

    print("\n[1] 补丁模块 import")
    bad = []
    for m in MODULES:
        try:
            __import__(m)
        except Exception as e:  # noqa: BLE001
            bad.append(f"{m}: {type(e).__name__}: {e}")
    check(not bad, f"{len(MODULES)} 个模块", "; ".join(bad)[:300])

    print("\n[2] 读真实数据（挂载进来的）")
    db = DATA / "data_v4.db"
    check(db.exists(), "data_v4.db 存在")
    if db.exists():
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        tables = {r[0] for r in con.execute(
            "select name from sqlite_master where type='table'")}
        need = {"yy_send_task", "yy_send_attempt", "yy_history_commit",
                "yy_history_exclude", "yy_fact", "yy_mood", "conversations", "personas"}
        check(need <= tables, "yy_/业务表齐全", f"缺 {sorted(need - tables)}")
        n = con.execute("select count(*) from yy_send_task").fetchone()[0]
        check(n >= 0, f"yy_send_task 行数 {n}")
        sp = con.execute(
            "select length(system_prompt) from personas where persona_id='ajing'"
        ).fetchone()
        check(bool(sp and sp[0]), f"配置 {sp[0] if sp else 0} 字")
        con.close()

    print("\n[3] 知识库索引")
    idx = DATA / "knowledge_base" / KB_ID / "index.faiss"
    doc = DATA / "knowledge_base" / KB_ID / "doc.db"
    check(idx.exists() and doc.exists(), "index.faiss + doc.db 存在")
    if idx.exists():
        import faiss  # noqa: PLC0415

        i = faiss.read_index(str(idx))
        con = sqlite3.connect(f"file:{doc}?mode=ro", uri=True)
        chunks = con.execute("select count(*) from documents").fetchone()[0]
        con.close()
        check(i.ntotal == chunks, f"faiss {i.ntotal} == doc.db {chunks}")

    print("\n[4] 配置能解析")
    cfg = DATA / "cmd_config.json"
    if cfg.exists():
        raw = cfg.read_text(encoding="utf-8-sig")
        d = json.loads(raw)
        check("dashboard" in d, "cmd_config.json 可解析",
              f"host={d.get('dashboard', {}).get('host')}")

    print("\n[5] 内存")
    try:
        import resource  # noqa: PLC0415

        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        print(f"  峰值 RSS: {rss:.0f} MB")
    except Exception:  # noqa: BLE001
        rss = 0
        print("  （读不到 RSS）")

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
