# -*- coding: utf-8 -*-
"""09 生活层运行时回归：推进、跨午夜、离线、幂等、上下文长度。"""
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

import os  # noqa: E402

os.environ["DEMO_TEST"] = "1"
os.environ["DEMO_LIFE_TZ"] = "Asia/Shanghai"

from astrbot.core.utils import life_state as ls  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="yy_life_"))
DB = TMP / "life.db"
UMO = "aiocqhttp:FriendMessage:10001"
TZ = ls.ZoneInfo("Asia/Shanghai") if hasattr(ls, "ZoneInfo") else dt.timezone(dt.timedelta(hours=8))

PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def at(day: int, hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(2026, 10, day, hour, minute, tzinfo=TZ)


def main() -> int:
    print("\n[1] 周一 14:00 在上课")
    st = ls.advance(UMO, now=at(26, 14), db_path=DB)
    check(st["title"] == "上课", "14:00 上课", str(st["title"]))
    check(st["place"] == "学校", "地点学校", str(st["place"]))
    check(0.5 < st["energy"] < 0.8, "精力 0.65 左右", str(st["energy"]))

    print("\n[2] 深夜在练习")
    st = ls.advance(UMO, now=at(26, 23), db_path=DB)
    check(st["title"] == "练习", "23:00 练习", str(st["title"]))
    check(st["place"] == "练习室", "地点练习室", str(st["place"]))
    ctx = ls.life_context(UMO, now=at(26, 23), db_path=DB)
    check("练习" in ctx["text"] and len(ctx["text"]) <= 300, "上下文含练习且≤300", ctx["text"])

    print("\n[3] 跨午夜：01:00 仍是前一天 21:30–22:30 开始的练习")
    st = ls.advance(UMO, now=at(27, 1), db_path=DB)
    check(st["title"] == "练习", "01:00 练习", str(st["title"]))
    start = dt.datetime.fromisoformat(st["start_at"])
    check(start.astimezone(TZ).day == 26
          and start.astimezone(TZ).hour in (21, 22)
          and start.astimezone(TZ).minute in (0, 30),
          "开始时间=26日21:30~22:30", str(start))

    print("\n[4] 凌晨在睡觉")
    st = ls.advance(UMO, now=at(27, 4), db_path=DB)
    check(st["title"] == "睡觉", "04:00 睡觉", str(st["title"]))
    check(st["energy"] < 0.4, "精力低", str(st["energy"]))

    print("\n[5] 重复推进不重复生成事件")
    con = ls._conn(DB)
    n1 = con.execute("select count(*) from yy_life_event").fetchone()[0]
    con.close()
    ls.advance(UMO, now=at(27, 4), db_path=DB)
    ls.advance(UMO, now=at(27, 4), db_path=DB)
    con = ls._conn(DB)
    n2 = con.execute("select count(*) from yy_life_event").fetchone()[0]
    con.close()
    check(n1 == n2, "事件数不变", f"{n1}->{n2}")

    print("\n[6] 离线 30 小时：只推进，不补发")
    st = ls.advance(UMO, now=at(28, 10), db_path=DB)
    check(st["title"] == "空闲", "10:00 新作息为空闲", str(st["title"]))
    con = ls._conn(DB)
    n3 = con.execute("select count(*) from yy_life_event").fetchone()[0]
    con.close()
    check(n3 >= n2, "事件只增不减", f"{n2}->{n3}")

    print("\n[6b] 回拨：活动/精力必须用同一个有效时间")
    st = ls.advance(UMO, now=at(28, 23), db_path=DB)
    check(st["title"] == "练习", "先到 23:00 练习", str(st["title"]))
    st = ls.advance(UMO, now=at(27, 11), db_path=DB)
    check(st["local"].hour == 23, "回拨后 local 仍是 23:00", str(st["local"]))
    check(st["energy"] < 0.4, "回拨后精力按 23:00", str(st["energy"]))
    check(st["title"] == "练习", "活动仍练习", str(st["title"]))

    print("\n[6c] L2：event_id / task_id / message_id 可回查，told 与发生分开")
    con = ls._conn(DB)
    eid = con.execute("select event_id from yy_life_event where kind='class' limit 1").fetchone()[0]
    view_n = con.execute("select count(*) from yy_event").fetchone()[0]
    con.close()
    check(view_n > 0, "yy_event 视图可查", str(view_n))
    ev = ls.event_by_id(eid, db_path=DB)
    check(ev is not None and ev["occurred_at"] and ev["told_at"] is None,
          "事件有 occurred_at、未 told", str(ev))
    check(ls.mark_event_told(eid, task_id="life-task-1", message_id="life-msg-1",
                             db_path=DB) is True, "mark told")
    ev = ls.event_by_id(eid, db_path=DB)
    check(ev["told_at"] and ev["task_id"] == "life-task-1"
          and ev["message_id"] == "life-msg-1", "told/来源已写", str(ev))
    got = ls.events_for_source(task_id="life-task-1", db_path=DB)
    check(len(got) == 1 and got[0]["event_id"] == eid, "按 task_id 找回")
    check(ls.mark_event_told(eid, task_id="other-task", message_id="other-msg",
                             db_path=DB) is True, "重复 mark 不报错")
    ev2 = ls.event_by_id(eid, db_path=DB)
    check(ev2["task_id"] == "life-task-1" and ev2["message_id"] == "life-msg-1",
          "首次来源不被覆盖", str(ev2))

    print("\n[7] 默认关闭 / 影子开关")
    os.environ.pop("DEMO_LIFE", None)
    check(ls.enabled() is False, "DEMO_LIFE 未设置=关闭")
    os.environ["DEMO_LIFE"] = "1"
    check(ls.enabled() is True, "DEMO_LIFE=1 开启")
    os.environ["DEMO_LIFE_SHADOW"] = "1"
    check(ls.shadow() is True, "DEMO_LIFE_SHADOW=1 影子")
    os.environ.pop("DEMO_LIFE_SHADOW", None)
    check(ls.shadow() is False, "影子默认关")

    print("\n[8] 未解决冲突：可开、可结、可查；不自动消失")
    c1 = ls.open_conflict(UMO, "用户昨天说我烦", source_msg_id="m1", db_path=DB)
    c2 = ls.open_conflict(UMO, "答应的事没做", source_msg_id="m2", db_path=DB)
    check(len(ls.list_conflicts(UMO, db_path=DB)) == 2, "两条 open", str(len(
        ls.list_conflicts(UMO, db_path=DB))))
    check(ls.resolve_conflict(c1, "道歉了", db_path=DB), "resolve 成功")
    check(len(ls.list_conflicts(UMO, db_path=DB)) == 1, "open 剩 1 条")
    check(len(ls.list_conflicts(UMO, only_open=False, db_path=DB)) == 2,
          "历史 2 条")
    check(ls.resolve_conflict(c1, "again", db_path=DB) is False, "重复 resolve 幂等")

    print("\n[9] 自动冲突识别：只认冲机器人来的；同 msg 去重")
    check(ls.detect_conflict("你烦不烦") == "烦", "你烦不烦")
    check(ls.detect_conflict("别说了 烦") == "烦", "别说了 烦")
    check(ls.detect_conflict("那个人很讨厌") == "", "第三方不认")
    check(ls.detect_conflict("你妈很烦") == "", "你妈不认")
    check(ls.detect_conflict("我生气了") == "", "用户自己的情绪不认")
    id1 = ls.open_conflict(UMO, "你烦不烦", source_msg_id="same-msg", db_path=DB)
    id2 = ls.open_conflict(UMO, "你烦不烦", source_msg_id="same-msg", db_path=DB)
    check(id1 == id2, "同 msg 幂等", f"{id1}/{id2}")

    print("\n[10] 道歉/和好：resolve 最近一条 open")
    check(ls.detect_resolve("对不起") == "对不起", "detect 对不起")
    check(ls.detect_resolve("我错了") == "我错了", "detect 我错了")
    check(ls.detect_resolve("今天天气不错") == "", "普通话不 resolve")
    id3 = ls.open_conflict(UMO, "又吵了", source_msg_id="m3", db_path=DB)
    check(ls.resolve_latest_conflict(UMO, "道歉", db_path=DB), "resolve_latest 成功")
    open_now = ls.list_conflicts(UMO, db_path=DB)
    check(all(c["conflict_id"] != id3 for c in open_now), "刚开的已不在 open", str(open_now))

    print("\n[11] 承诺：planned→active→fulfilled，能查交付任务")
    cid = ls.add_commitment(UMO, "周末整理一份示例", due_at="2026-10-26T22:00:00+08:00",
                            source_msg_id="m4", db_path=DB)
    rows = ls.list_commitments(UMO, db_path=DB)
    check(len(rows) == 1 and rows[0]["status"] == "planned", "新承诺 planned", str(rows))
    check(ls.set_commitment_status(cid, "active", delivered_task_id="task-1", db_path=DB),
          "active 成功")
    rows = ls.list_commitments(UMO, db_path=DB)
    check(rows[0]["status"] == "active" and rows[0]["delivered_task_id"] == "task-1",
          "带交付任务", str(rows))
    check(ls.set_commitment_status(cid, "fulfilled", db_path=DB), "fulfilled 成功")
    check(ls.list_commitments(UMO, db_path=DB) == [], "open 不再列出")
    try:
        ls.set_commitment_status(cid, "bogus", db_path=DB)
        check(False, "非法状态应报错")
    except ValueError:
        check(True, "非法状态报错")

    print("\n[12] 外部来源：只存元数据，可回查")
    sid = ls.add_source("bilibili", url="https://b23.tv/x", bvid="BVx",
                        title="示例视频", read_scope="title", db_path=DB)
    src = ls.get_source(sid, db_path=DB)
    check(src and src["title"] == "示例视频" and src["read_scope"] == "title",
          "来源可取", str(src))
    check(ls.get_source("nope", db_path=DB) is None, "不存在的来源 None")

    print("\n[13] 承诺上下文：只有真实送达的 active 承诺进提示")
    cid2 = ls.add_commitment(UMO, "周末整理一份示例", db_path=DB)
    import sqlite3  # noqa: PLC0415

    _con = sqlite3.connect(DB)
    _con.execute("CREATE TABLE IF NOT EXISTS yy_send_task(task_id TEXT PRIMARY KEY,state TEXT)")
    _con.execute("INSERT OR REPLACE INTO yy_send_task(task_id,state) VALUES('life-task','sent')")
    _con.commit()
    _con.close()
    ls.set_commitment_status(cid2, "active", delivered_task_id="life-task", db_path=DB)
    ctx = ls.commitments_context(UMO, db_path=DB)
    check("周末整理一份示例" in ctx and "不要编新的承诺" in ctx, "承诺进上下文", ctx)
    ls.set_commitment_status(cid2, "fulfilled", db_path=DB)
    check(ls.commitments_context(UMO, db_path=DB) == "", "fulfilled 后不再提示")

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
