# -*- coding: utf-8 -*-
"""L5 延迟 outbox 回归：due、sent、超时 dropped。不接机器人。"""
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

os.environ["DEMO_TEST"] = "1"
os.environ["DEMO_LIFE"] = "1"
os.environ["DEMO_LIFE_TZ"] = "Asia/Shanghai"

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
sys.path.insert(0, str(ROOT / "instances/11111111-1111-4111-8111-111111111111/core"))

from astrbot.core.utils import life_state as ls  # noqa: E402

TZ = ls._tz()
U = "u:delay"
PASS = True
DB = Path(tempfile.mkdtemp(prefix="yy_delay_")) / "life.db"
T0 = dt.datetime(2026, 10, 27, 12, 0, tzinfo=TZ)


def check(cond, label, extra=""):
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + str(extra)) if extra else ''}")


def main():
    global PASS
    sys.stdout.reconfigure(encoding="utf-8")

    print("\n[1] 立即可发")
    did = ls.queue_delayed(U, "在吗", delay_s=0, created_at=T0, db_path=DB)
    check(bool(did), "queue")
    rows = ls.due_delayed(now=T0, db_path=DB)
    check(len(rows) == 1 and rows[0]["text"] == "在吗", "due", rows)
    check(ls.mark_delayed(did, "sent", task_id="t1", db_path=DB), "mark sent")
    check(ls.due_delayed(now=T0, db_path=DB) == [], "sent 不再 due")

    print("\n[2] 延迟 10 分钟")
    did2 = ls.queue_delayed(U, "晚点", delay_s=600, created_at=T0, db_path=DB)
    check(ls.due_delayed(now=T0, db_path=DB) == [], "未到点")
    rows = ls.due_delayed(now=T0 + dt.timedelta(minutes=11), db_path=DB)
    check(len(rows) == 1 and rows[0]["id"] == did2, "到点", rows)

    print("\n[3] 超过 2h 丢弃")
    did3 = ls.queue_delayed(U, "过期", delay_s=3 * 3600, created_at=T0, db_path=DB)
    n = ls.expire_delayed(now=T0 + dt.timedelta(hours=3), db_path=DB)
    check(n >= 1, "expire", n)
    check(ls.due_delayed(now=T0 + dt.timedelta(hours=4), db_path=DB) == [], "不补发")
    check(ls.mark_delayed(did3, "sent", db_path=DB) is False, "已 dropped 不能再 sent")

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
