# -*- coding: utf-8 -*-
"""P1-B04 ~ B09 回归：主体/修复出口/位置/追问/快照/关系同源。

全部在临时库上跑，不碰正式库、不发 QQ、不调模型。
"""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "ops" / "life"))

os.environ["DEMO_TEST"] = "1"
os.environ["DEMO_LIFE"] = "1"
os.environ["DEMO_LIFE_SHADOW"] = "0"
os.environ["DEMO_LIFE_TZ"] = "Asia/Shanghai"

from astrbot.core.utils import life_state as L  # noqa: E402
from snapshot import local_day_bounds, snapshot  # noqa: E402

OK = True
UMO = "aiocqhttp:FriendMessage:p1_b04_b09"
TZ = ZoneInfo("Asia/Shanghai")


def check(cond: bool, label: str, detail: str = "") -> None:
    global OK
    OK = OK and bool(cond)
    print(f"  {'OK  ' if cond else 'FAIL'} {label}" + (f" | {detail}" if detail else ""))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    tmp = Path(tempfile.mkdtemp(prefix="yy_p1_"))
    db = tmp / "life.db"

    print("[B04] 冲突规则统一主体/引用/否定")
    for text, want in [
        ("我今天遇到了骗子，你能陪我聊聊吗", ""),
        ("用户说你很敷衍", ""),
        ("你没有反悔，说好的也没忘", ""),
        ("我答应你的事没做到", ""),
        ("用户说你答应用户的事又忘了", ""),
        ("你答应我的事又忘了", "失约"),
        ("你是不是又忘了答应我的事", "失约"),
        ("你真的很敷衍", "敷衍"),
        ("你是个骗子", "骗"),
        ("你烦不烦", "烦"),
        ("我不讨厌你", ""),
        ("你朋友真讨厌", ""),
    ]:
        got = L.detect_conflict(text)
        check(got == want, f"conflict {text!r}", f"{got!r}")

    print("[B05] 修复出口、责任方、同一争执合并")
    check(L.apology_kind("你该对我说对不起") == "demand", "要求道歉不算 own")
    check(L.detect_resolve("你该对我说对不起") == "", "要求道歉不解除")
    check(L.apology_kind("同事跟我说对不起") == "third_party", "第三方道歉")
    check(L.detect_resolution("我们已经说开了，这件事解决了") == "说开了", "说开了")
    check(L.detect_resolution("还没说开") == "", "否定不解除")
    check(L.detect_resolution("用户说解决了") == "", "转述不解除")

    c1 = L.open_conflict(UMO, "你昨天说那句话让我挺难受的", "m1", db_path=db)
    c2 = L.open_conflict(UMO, "你昨天那句话真的让我难受", "m2", db_path=db)
    check(c1 == c2, "同一争执只留一条", str(c1))
    check(len(L.list_conflicts(UMO, db_path=db)) == 1, "冲突计数 1")

    L.note_conflict_repair(UMO, "own", note="对不起", db_path=db)
    row = L.list_conflicts(UMO, db_path=db)[0]
    check(row["status"] == "repairing", "own 道歉 -> repairing", row["status"])
    check(row["apologizer"] == "user" and row["injured"] == "her",
          "记录谁道歉/谁受伤", f"{row['apologizer']}/{row['injured']}")
    check("用户道过歉了" in L.format_conflicts(UMO, db_path=db),
          "摘要按责任方生成")

    check(L.resolve_latest_conflict(UMO, note="我们已经说开了", db_path=db),
          "repairing 能被说开结束")
    check(not L.list_conflicts(UMO, db_path=db), "resolved 不再注入")

    c3 = L.open_conflict(UMO, "你昨天那句话让我难受", "m3", db_path=db)
    L.note_conflict_repair(UMO, "own", note="我道歉", db_path=db,
                           apologizer="her", injured="user")
    check("你道过歉了" in L.format_conflicts(UMO, db_path=db),
          "机器人道歉时摘要不写反")
    check(L.resolve_latest_conflict(UMO, note="翻篇了", db_path=db),
          "still_hurt 也能结束")

    print("[B06] 位置否定/时态/主体清理")
    place = tmp / "place.db"
    check(L.observe_place(UMO, "我不在学校", db_path=place) is None, "否定不写")
    check(L.current_place(UMO, db_path=place, subject="user") is None, "否定后无位置")
    check(L.observe_place(UMO, "我在学校", db_path=place) == "学校", "肯定写入")
    check(L.observe_place(UMO, "我不在学校", db_path=place) is None, "再否定")
    check(L.current_place(UMO, db_path=place, subject="user") is None,
          "否定会清掉旧位置")
    check(L.observe_place(UMO, "我明天去学校", db_path=place) is None, "将来不写")
    check(L.observe_place(UMO, "我昨天在学校", db_path=place) is None, "过去不写")
    check(L.observe_place(UMO, "我现在在练习室", db_path=place) == "练习室", "现在写入")
    L.set_place(UMO, "学校", "user_statement", 0.9, db_path=place, subject="her")
    check(L.clear_place(UMO, db_path=place, subject="her"), "清机器人的位置")
    check(L.current_place(UMO, db_path=place, subject="user") == "练习室",
          "清机器人不动用户位置")

    # 旧表迁移：不猜 her，旧表改名留审计
    legacy_db = tmp / "legacy.db"
    con = sqlite3.connect(str(legacy_db))
    con.execute("CREATE TABLE yy_life_place (umo TEXT PRIMARY KEY, place_id TEXT,"
                " source_type TEXT, confidence REAL, updated_at TEXT, expires_at TEXT)")
    con.execute("INSERT INTO yy_life_place VALUES ('u:old','学校','user_statement',0.9,NULL,NULL)")
    con.commit()
    con.close()
    L._conn(legacy_db).close()
    con = sqlite3.connect(str(legacy_db))
    try:
        legacy_count = con.execute(
            "SELECT count(*) FROM yy_life_place_legacy").fetchone()[0]
        new_count = con.execute("SELECT count(*) FROM yy_life_place").fetchone()[0]
    finally:
        con.close()
    check(legacy_count == 1 and new_count == 0, "旧主体隔离到 legacy 表",
          f"legacy={legacy_count} new={new_count}")

    print("[B07] 追问的人物/日期/事件选择")
    topic_db = tmp / "topic.db"
    L.advance(UMO, now=dt.datetime(2026, 10, 26, 22, 30, tzinfo=TZ), db_path=topic_db)
    ctx = L.life_context(UMO, now=dt.datetime(2026, 10, 27, 12, 0, tzinfo=TZ),
                         db_path=topic_db, text="昨天练习怎么样")
    check("你昨天在练习" in ctx["text"], "按日期找昨天练习", ctx["text"][:80])
    check("2026-10-26" in (ctx.get("source_event_id") or "")
          and "practice" in (ctx.get("source_event_id") or ""),
          "事件 ID 对应实际讲述内容", ctx.get("source_event_id"))
    check(L.should_inject("我昨天在外面吃饭") is False, "用户自己的事不注入")
    check(L.should_inject("你昨天练习怎么样") is True, "问机器人的事注入")

    topic_id = ctx["source_event_id"]
    check(L.last_topic(UMO, db_path=topic_db,
                       now=dt.datetime(2026, 10, 27, 13, 0, tzinfo=TZ)) == topic_id,
          "话题时间用模拟时间")
    check(L.last_topic(UMO, db_path=topic_db,
                       now=dt.datetime(2026, 10, 28, 2, 0, tzinfo=TZ)) is None,
          "跨 12h 过期")
    ctx_user = L.life_context(UMO, now=dt.datetime(2026, 10, 27, 13, 0, tzinfo=TZ),
                              db_path=topic_db, text="我昨天在外面吃饭")
    check(L.last_topic(UMO, db_path=topic_db,
                       now=dt.datetime(2026, 10, 27, 13, 5, tzinfo=TZ)) == topic_id,
          "用户陈述不覆盖机器人的话题", ctx_user["text"][:60])
    ctx_meal = L.life_context(UMO, now=dt.datetime(2026, 10, 27, 12, 30, tzinfo=TZ),
                              db_path=topic_db, text="今天午饭吃什么")
    check("午饭" in ctx_meal["text"], "按日期找今天午饭", ctx_meal["text"][:80])

    print("[B08] 快照与日界")
    src = tmp / "wal_src.db"
    con = sqlite3.connect(str(src))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    con.execute("INSERT INTO t(v) VALUES ('uncheckpointed')")
    con.commit()
    # 不 checkpoint，模拟 WAL 里还有未落主文件的数据
    dst = tmp / "wal_copy.db"
    snapshot(src, dst)
    con2 = sqlite3.connect(str(dst))
    try:
        got = con2.execute("SELECT v FROM t").fetchone()[0]
    finally:
        con2.close()
    con.close()
    check(got == "uncheckpointed", "WAL 未 checkpoint 也能快照完整", got)
    start, end = local_day_bounds(dt.date(2026, 10, 27), TZ)
    check(start == "2026-10-26T16:00:00+00:00" and end == "2026-10-27T16:00:00+00:00",
          "角色时区日界", f"{start} ~ {end}")

    print("[B09] 关系与生活同源")
    old_ledger = os.environ.get("DEMO_LEDGER_DB")
    old_shadow = os.environ.get("DEMO_LIFE_SHADOW")
    os.environ["DEMO_LEDGER_DB"] = str(tmp / "relation.db")
    os.environ["DEMO_LIFE_SHADOW"] = "1"
    rel = L.relation_db()
    os.environ["DEMO_LIFE_SHADOW"] = old_shadow or "0"
    if old_ledger is None:
        os.environ.pop("DEMO_LEDGER_DB", None)
    else:
        os.environ["DEMO_LEDGER_DB"] = old_ledger
    check(rel == tmp / "relation.db", "relation_db 不随 shadow 切库", str(rel))

    commit_db = tmp / "commit.db"
    con = sqlite3.connect(str(commit_db))
    con.execute("CREATE TABLE yy_send_task (task_id TEXT PRIMARY KEY, state TEXT)")
    con.execute("INSERT INTO yy_send_task VALUES ('task-1','sent')")
    con.commit()
    con.close()
    cid = L.add_commitment(UMO, "明天陪你练习", db_path=commit_db)
    check(L.set_commitment_status(cid, "active", delivered_task_id="task-1",
                                  db_path=commit_db), "承诺 active 绑定真实 task")
    old_life = os.environ.get("DEMO_LIFE")
    os.environ["DEMO_LIFE"] = "0"
    ctx_c = L.commitments_context(UMO, db_path=commit_db)
    os.environ["DEMO_LIFE"] = old_life or "1"
    check("明天陪你练习" in ctx_c, "生活层关闭后承诺仍可用", ctx_c[:60])

    agent_src = (ROOT / "patches/files/astrbot/core/astr_main_agent.py").read_text(
        encoding="utf-8", errors="ignore")
    pro_src = (ROOT / "patches/files/astrbot/dashboard/services/proactive_service.py"
               ).read_text(encoding="utf-8", errors="ignore")
    check("_commit_ctx(event.unified_msg_origin, db_path=_rel_db)" in agent_src,
          "被动承诺读关系库")
    check("_commit_ctx(umo, db_path=_rel_db)" in pro_src, "主动承诺读关系库")
    check("_format_conflicts(umo, db_path=_rel_db)" in pro_src, "主动注入冲突")

    print("\nRESULT:", "PASS" if OK else "FAIL")
    return 0 if OK else 1


if __name__ == "__main__":
    sys.exit(main())
