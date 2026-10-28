# -*- coding: utf-8 -*-
"""P0-2 验证：旧逻辑（本地时间直接相减）vs 新逻辑（按 UTC 解释）。"""
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB = r"D:\demo\instances\11111111-1111-4111-8111-111111111111\core\data\data_v4.db"
UMO = "aiocqhttp:FriendMessage:100000000"


def old_logic(minutes):
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    row = db.execute("SELECT updated_at FROM conversations WHERE user_id=?",
                     (UMO,)).fetchone()
    db.close()
    if not row:
        return False
    ts = datetime.fromisoformat(str(row[0]))
    return datetime.now() - ts < timedelta(minutes=minutes)


def new_logic(minutes):
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    row = db.execute("SELECT updated_at FROM conversations WHERE user_id=?",
                     (UMO,)).fetchone()
    db.close()
    if not row or not row[0]:
        return False
    ts = datetime.fromisoformat(str(row[0]))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() < minutes * 60


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    row = db.execute("SELECT updated_at FROM conversations WHERE user_id=?",
                     (UMO,)).fetchone()
    db.close()
    ts = datetime.fromisoformat(str(row[0]))
    utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    age_min = (utc_now - ts).total_seconds() / 60
    print(f"updated_at(UTC) = {ts}")
    print(f"UTC now         = {utc_now:%Y-%m-%d %H:%M:%S}")
    print(f"本地 now        = {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"真实距今        = {age_min:.0f} 分钟\n")
    ok = True
    for m in (10, 120):
        o, n = old_logic(m), new_logic(m)
        # 真实答案：age_min < m
        truth = age_min < m
        mark = "OK  " if n == truth else "FAIL"
        ok = ok and n == truth
        print(f"{mark} window={m:>3}min  旧={o}  新={n}  应为={truth}")
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
