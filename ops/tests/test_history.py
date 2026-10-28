# -*- coding: utf-8 -*-
"""P0-B 历史提交的隔离测试：同事务幂等 + 三个崩溃断点 + 并发。

每个场景用独立的数据副本，绝不碰正式库。
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
TSRC = ROOT / "ops/tests/crash_proc.py"
sys.path.insert(0, str(CORE))

PROD_DB = CORE / "data/data_v4.db"
UMO = "aiocqhttp:FriendMessage:100000000"
PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def fresh_db(tag: str) -> Path:
    d = Path(tempfile.mkdtemp(prefix=f"yy_hist_{tag}_"))
    p = d / "data_v4.db"
    shutil.copy2(PROD_DB, p)
    return p


def conv_state(db: Path):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cid = con.execute(
        "select conversation_id from conversations where user_id=?", (UMO,)).fetchone()[0]
    hist = json.loads(con.execute(
        "select content from conversations where conversation_id=?", (cid,)).fetchone()[0])
    con.close()
    n_assistant = sum(1 for m in hist if m.get("role") == "assistant")
    return cid, n_assistant


def new_sent_task(ledger, text: str, cid: str | None):
    tid = ledger.new_task(session="p:test", kind="text", text=text,
                          conversation_id=cid, managed_history=True)
    assert ledger.claim(tid)
    ledger.finish_ok(tid, "fake-mid")
    return tid


def main() -> int:
    from astrbot.core.utils.send_ledger import Ledger

    print("\n[1] 正常提交")
    os.environ["DEMO_LEDGER_DB"] = str(fresh_db("ok"))
    ledger = Ledger()
    cid, before = conv_state(ledger.db_path)
    tid = new_sent_task(ledger, "提交测试一", cid)
    check(ledger.commit_history(tid) == "committed", "返回 committed")
    _, after = conv_state(ledger.db_path)
    check(after == before + 1, f"assistant 条数 {before} -> {after}")
    check(ledger.commit_history(tid) == "already", "第二次返回 already")
    _, after2 = conv_state(ledger.db_path)
    check(after2 == after, "没有重复追加")

    print("\n[2] 原会话被删 → skipped_no_conv，state 仍是 sent")
    os.environ["DEMO_LEDGER_DB"] = str(fresh_db("gone"))
    ledger = Ledger()
    tid = new_sent_task(ledger, "会话没了的消息", "no-such-cid")
    check(ledger.commit_history(tid) == "skipped_no_conv", "返回 skipped_no_conv")
    row = ledger.get(tid)
    check(row["state"] == "sent" and row["history_state"] == "skipped_no_conv",
          "state=sent / history_state=skipped_no_conv")

    print("\n[3] 未 sent 的任务不能提交")
    os.environ["DEMO_LEDGER_DB"] = str(fresh_db("notyet"))
    ledger = Ledger()
    cid, before = conv_state(ledger.db_path)
    tid = ledger.new_task(session="p:test", kind="text", text="还没发出去",
                          conversation_id=cid, managed_history=True)
    check(ledger.commit_history(tid) == "missing_task", "拒绝提交")
    _, after = conv_state(ledger.db_path)
    check(after == before, "历史没变")

    print("\n[4] 断点 before_history_commit → 重启后补写一条")
    db = fresh_db("crash_before")
    os.environ["DEMO_LEDGER_DB"] = str(db)
    ledger = Ledger()
    cid, before = conv_state(db)
    tid = new_sent_task(ledger, "断点前的消息", cid)
    env = {**os.environ, "DEMO_CRASH_AT": "before_history_commit"}
    r = subprocess.run([sys.executable, str(TSRC), tid, "before_history_commit"],
                       env=env, capture_output=True, text=True, timeout=120)
    check(r.returncode != 0, "子进程确实崩了", f"rc={r.returncode}")
    check(ledger.get(tid)["history_state"] == "pending", "崩后仍是 pending")
    _, mid = conv_state(db)
    check(mid == before, "崩溃时历史没写入（事务回滚）")
    pending = [t["task_id"] for t in ledger.pending_history()]
    check(tid in pending, "恢复扫描能看到它")
    check(ledger.commit_history(tid) == "committed", "重启后补写成功")
    _, after = conv_state(db)
    check(after == before + 1, f"正好多一条 {before} -> {after}")

    print("\n[5] 断点 after_history_commit → 重启后不重复")
    db = fresh_db("crash_after")
    os.environ["DEMO_LEDGER_DB"] = str(db)
    ledger = Ledger()
    cid, before = conv_state(db)
    tid = new_sent_task(ledger, "断点后的消息", cid)
    env = {**os.environ, "DEMO_CRASH_AT": "after_history_commit"}
    r = subprocess.run([sys.executable, str(TSRC), tid, "after_history_commit"],
                       env=env, capture_output=True, text=True, timeout=120)
    check(r.returncode != 0, "子进程确实崩了", f"rc={r.returncode}")
    check(ledger.get(tid)["history_state"] == "committed", "崩前已提交")
    check(tid not in [t["task_id"] for t in ledger.pending_history()],
          "恢复扫描不会再补")
    check(ledger.commit_history(tid) == "already", "手工再提交也幂等")
    _, after = conv_state(db)
    check(after == before + 1, f"只有一条 {before} -> {after}")

    print("\n[6] 并发提交：2 线程 × 25 轮只加一条")
    db = fresh_db("race")
    os.environ["DEMO_LEDGER_DB"] = str(db)
    ledger = Ledger()
    cid, before = conv_state(db)
    tid = new_sent_task(ledger, "并发提交的消息", cid)
    results = []
    lock = threading.Lock()

    def worker():
        for _ in range(25):
            try:
                res = ledger.commit_history(tid)
            except sqlite3.OperationalError as exc:  # busy 允许重试
                res = f"busy:{exc}"
            with lock:
                results.append(res)

    ts = [threading.Thread(target=worker) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    _, after = conv_state(db)
    check(after == before + 1, f"只加了一条 {before} -> {after}",
          f"committed={results.count('committed')} already={results.count('already')}")
    check(results.count("committed") == 1, "只有一个 committed")

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
