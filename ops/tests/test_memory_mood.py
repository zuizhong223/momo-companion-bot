# -*- coding: utf-8 -*-
"""A06/A07 回归：结构化记忆的多值/更正/来源；情绪否定/对象/去重/分离。"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

TMP = Path(tempfile.mkdtemp(prefix="yy_mem_mood_"))
DB = TMP / "mem_mood.db"
UMO = "aiocqhttp:FriendMessage:10001"

PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def main() -> int:
    from astrbot.core.utils import memory_facts as mf
    from astrbot.core.utils import mood_state as ms

    print("\n[1] 单值更正：旧值 superseded，新值 active，来源保留")
    mf.remember(UMO, "生日", "1.22", source="orig-msg-1", source_msg_id="m1", db_path=DB)
    mf.remember(UMO, "生日", "1.23", source="correction-msg-2", source_msg_id="m2", db_path=DB)
    active = mf.recall(UMO, like="生日", db_path=DB)
    allrows = mf.recall(UMO, like="生日", include_inactive=True, db_path=DB)
    check(len(active) == 1 and active[0]["obj"] == "1.23", "active 只有新值", str(active))
    check(len(allrows) == 2, "旧值仍可追溯", str(len(allrows)))
    old = [r for r in allrows if r["obj"] == "1.22"][0]
    check(old["status"] == "superseded" and old["valid_to"], "旧值 superseded+valid_to")
    check("correction-msg-2" in (mf.format_for_prompt(UMO, db_path=DB) or ""),
          "召回带来源")

    print("\n[2] 多值喜好：音乐和游戏并存，不互相覆盖")
    mf.remember(UMO, "喜欢", "音乐", multi=True, source="m3", db_path=DB)
    mf.remember(UMO, "喜欢", "游戏", multi=True, source="m4", db_path=DB)
    likes = mf.recall(UMO, like="喜欢", db_path=DB)
    check({r["obj"] for r in likes} == {"音乐", "游戏"}, "两个喜好都在", str(likes))
    mf.forget(UMO, "喜欢", "音乐", db_path=DB)
    likes2 = mf.recall(UMO, like="喜欢", db_path=DB)
    check([r["obj"] for r in likes2] == ["游戏"], "软撤回只撤一条", str(likes2))

    print("\n[3] 情绪关键词：否定/第三方不误触发")
    base = ms.current(UMO, "her", DB)
    for text in ("我不喜欢这个", "我不困", "那个人很讨厌"):
        ms.apply(UMO, text, event_id="neg:" + text, db_path=DB)
    after = ms.current(UMO, "her", DB)
    check(abs(after[0] - base[0]) < 1e-6 and abs(after[1] - base[1]) < 1e-6,
          "三条都不改角色的状态", f"{base} -> {after}")

    print("\n[4] 被夸 / 被顶：有原因、有方向")
    for i in range(4):
        ms.apply(UMO, "你今天好可爱", event_id=f"praise-{i}", db_path=DB)
    praised = ms.current(UMO, "her", DB)
    check(praised[0] > 0.25, "被夸→愉悦上升", str(praised))
    check("心情不错" in (ms.format_for_prompt(UMO, db_path=DB) or ""), "提示里有心情")
    ms.apply(UMO, "别说了 烦", event_id="rude-1", db_path=DB)
    after_rude = ms.current(UMO, "her", DB)
    check(after_rude[0] < praised[0], "被顶→下降", f"{praised} -> {after_rude}")

    print("\n[5] 同一条消息重试不重复加情绪")
    v1 = ms.current(UMO, "her", DB)[0]
    ms.apply(UMO, "你今天好可爱", event_id="praise-1", db_path=DB)
    v2 = ms.current(UMO, "her", DB)[0]
    check(abs(v2 - v1) < 1e-6, "同 event_id 幂等", f"{v1} -> {v2}")

    print("\n[6] 用户累了：只记 user，不推断机器人也困")
    her_before = ms.current(UMO, "her", DB)
    ms.apply(UMO, "我累了", event_id="tired-1", db_path=DB)
    her_after = ms.current(UMO, "her", DB)
    user_after = ms.current(UMO, "user", DB)
    check(abs(her_after[0] - her_before[0]) < 1e-3
          and abs(her_after[1] - her_before[1]) < 1e-3,
          "角色的状态不变（允许毫秒级衰减）", f"{her_before} -> {her_after}")
    check(user_after[1] < 0.5, "user 精力下降", str(user_after))

    print("\n[7] 自动候选：只抽显式第一人称，candidate 不参与召回")
    cand = mf.extract_candidates("我喜欢音乐和游戏")
    check(("喜欢", "音乐和游戏", True) in cand, "抽到喜欢", str(cand))
    check(mf.extract_candidates("用户说我认可你") == [], "转述不抽")
    check(mf.extract_candidates("我生日是1月22日") == [("生日", "1月22日", False)],
          "生日单值", str(mf.extract_candidates("我生日是1月22日")))
    n = mf.capture_candidates(UMO, "我喜欢草莓", "msg-cand-1", db_path=DB)
    check(n >= 1, "写入候选", str(n))
    active = mf.recall(UMO, like="草莓", db_path=DB)
    check(active == [], "candidate 不出现在 active 召回", str(active))
    allrows = mf.recall(UMO, like="草莓", include_inactive=True, db_path=DB)
    check(any(r["status"] == "candidate" and r["source_msg_id"] == "msg-cand-1"
              for r in allrows), "候选带来源", str(allrows))
    check("草莓" not in (mf.format_for_prompt(UMO, db_path=DB) or ""),
          "候选不进提示")

    print("\n[8] 候选确认/撤回：promote 才进 active，drop 标 retracted")
    mf.capture_candidates(UMO, "我喜欢蓝莓", "msg-cand-2", db_path=DB)
    cands = mf.list_candidates(UMO, db_path=DB)
    check(any(c["obj"] == "蓝莓" for c in cands), "候选列表有蓝莓", str(cands))
    check(mf.promote_candidate(UMO, "喜欢", "蓝莓", multi=True, db_path=DB),
          "promote 成功")
    blue = mf.recall(UMO, like="蓝莓", db_path=DB)
    check(len(blue) == 1 and blue[0]["status"] == "active", "蓝莓进 active", str(blue))
    check(not any(c["obj"] == "蓝莓" for c in mf.list_candidates(UMO, db_path=DB)),
          "升 active 后不在候选")

    mf.capture_candidates(UMO, "我住在ExampleCity", "msg-cand-3", db_path=DB)
    check(mf.drop_candidate(UMO, "住在", "ExampleCity", db_path=DB), "drop 成功")
    check(mf.recall(UMO, like="ExampleCity", db_path=DB) == [], "ExampleCity不进 active")

    mf.capture_candidates(UMO, "我生日是9月9日", "msg-cand-4", db_path=DB)
    mf.promote_candidate(UMO, "生日", "9月9日", db_path=DB)
    bdays = mf.recall(UMO, like="生日", include_inactive=True, db_path=DB)
    check(any(b["obj"] == "9月9日" and b["status"] == "active" for b in bdays),
          "新生日 active", str(bdays))
    check(any(b["obj"] == "1.23" and b["status"] == "superseded" for b in bdays),
          "旧生日 superseded", str(bdays))

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
