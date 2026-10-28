# -*- coding: utf-8 -*-
"""P1-8：删除会话时，发送台账/历史/turn/recent 不能残留。"""
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(r"D:\demo")
TMP = Path(tempfile.mkdtemp(prefix="yy_delete_"))
DB = TMP / "delete.db"
PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def make_db():
    con = sqlite3.connect(DB)
    con.executescript("""
        create table conversations(conversation_id text primary key,user_id text,
                                   content text,updated_at text);
        create table yy_send_task(task_id text primary key,conversation_id text);
        create table yy_send_attempt(attempt_id text primary key,task_id text);
        create table yy_history_commit(task_id text primary key);
        create table yy_turn(reply_id text primary key,conversation_id text);
        create table yy_recent_sent(session text);
        create table yy_life_event(event_id text primary key,umo text);
        create table yy_mood(umo text);
    """)
    con.execute("insert into conversations values('c1','u1','hello','2026-10-27')")
    con.execute("insert into yy_send_task values('t1','c1')")
    con.execute("insert into yy_send_attempt values('a1','t1')")
    con.execute("insert into yy_history_commit values('t1')")
    con.execute("insert into yy_turn values('r1','c1')")
    con.execute("insert into yy_recent_sent values('p:u1')")
    con.execute("insert into yy_life_event values('e1','u1')")
    con.execute("insert into yy_mood values('u1')")
    con.commit()
    con.close()


def count(table):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    n = con.execute(f"select count(*) from {table}").fetchone()[0]
    con.close()
    return n


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    make_db()
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "delete_p1", ROOT / "ops/data/delete.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    mod.BACKUP = TMP / "backups"
    old_argv = sys.argv
    try:
        sys.argv = ["delete.py", "--conversation-id", "c1", "--apply",
                    "--db", str(DB), "--include-life"]
        rc = mod.main()
    finally:
        sys.argv = old_argv
    check(rc == 0, "delete 返回 0", str(rc))
    for table in ("conversations", "yy_send_task", "yy_send_attempt",
                  "yy_history_commit", "yy_turn", "yy_recent_sent",
                  "yy_life_event", "yy_mood"):
        check(count(table) == 0, f"{table} 已清空", str(count(table)))
    backups = list((TMP / "backups").glob("delete_backup_*/conversation_c1.json"))
    check(len(backups) == 1, "备份 JSON 存在", str(backups))

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
