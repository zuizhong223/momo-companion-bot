# -*- coding: utf-8 -*-
"""只读探针：查看 conversations / personas / api_keys 现状（不做任何写入）。"""
import datetime
import os
import sqlite3
import sys

DB = r"D:/demo\instances\11111111-1111-4111-8111-111111111111\core\data\data_v4.db"


def main():
    print("db:", DB, os.path.getsize(DB), "bytes")
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    names = [r[0] for r in db.execute(
        "select name from sqlite_master where type='table' order by name")]
    print("tables:", ", ".join(names))
    print()
    cols = [r[1] for r in db.execute("PRAGMA table_info(conversations)")]
    print("conversations cols:", cols)
    print()
    for r in db.execute(
            "select user_id, updated_at, created_at, length(content), title, persona_id "
            "from conversations order by updated_at desc limit 8"):
        print(" ", r)
    print()
    print("now local:", datetime.datetime.now().isoformat(timespec="seconds"))
    print("now utc  :", datetime.datetime.now(datetime.timezone.utc)
          .replace(tzinfo=None).isoformat(timespec="seconds"))
    print()
    for r in db.execute("select persona_id, length(system_prompt), "
                        "length(begin_dialogs) from personas"):
        print(" persona:", r)
    print()
    for r in db.execute("select name from sqlite_master where type='table' "
                        "and name like '%api_key%'"):
        print(" apikey table:", r)
    for r in db.execute("select * from api_keys"):
        print(" api_keys row:", r[:2], "...", str(r)[:200])


if __name__ == "__main__":
    sys.exit(main())
