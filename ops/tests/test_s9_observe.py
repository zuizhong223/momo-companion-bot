# -*- coding: utf-8 -*-
"""S9：observe 的发送 p50/p95、TTS 失败率、去重跳过计数。"""
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(r"D:\demo")
OPS = ROOT / "ops"
sys.path.insert(0, str(OPS / "verify"))
PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    import observe

    tmp = Path(tempfile.mkdtemp(prefix="yy_s9_"))
    db = tmp / "s9.db"
    con = sqlite3.connect(db)
    con.executescript("""
        create table yy_send_task(
          task_id text primary key, state text, history_state text,
          error text, created_at text);
        create table yy_send_attempt(
          attempt_id text primary key, task_id text, state text,
          started_at text, ended_at text);
    """)
    con.execute("insert into yy_send_task values('t1','sent','committed',null,"
                "'2026-10-27T00:00:00+00:00')")
    con.executemany(
        "insert into yy_send_attempt values(?,?,'sent',?,?)",
        [("a1", "t1", "2026-10-27T00:00:00+00:00", "2026-10-27T00:00:00.100000+00:00"),
         ("a2", "t1", "2026-10-27T00:00:00+00:00", "2026-10-27T00:00:00.300000+00:00"),
         ("a3", "t1", "2026-10-27T00:00:00+00:00", "2026-10-27T00:00:01.000000+00:00")])
    con.commit()
    con.close()

    log = tmp / "astrbot.log"
    log.write_text(
        "[x] TTS request: a\n[x] TTS result: a.wav\n"
        "[x] TTS request: b\n[x] [被动去重] 与最近回复重复\n",
        encoding="utf-8")
    observe.DB = db
    observe.LOG = log
    d = observe.counts()
    check(d["send_latency_n"] == 3, "三次延迟样本", str(d["send_latency_n"]))
    check(d["send_p50_ms"] == 300.0, "p50=300ms", str(d["send_p50_ms"]))
    check(d["send_p95_ms"] == 1000.0, "p95=1000ms", str(d["send_p95_ms"]))
    check(d["tts_requests"] == 2 and d["tts_results"] == 1, "TTS 请求/结果", str(d))
    check(d["tts_fail_rate"] == 0.5, "TTS 失败率 0.5", str(d["tts_fail_rate"]))
    check(d["dedup_skip"] == 1, "去重跳过 1", str(d["dedup_skip"]))

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
