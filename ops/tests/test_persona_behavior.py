# -*- coding: utf-8 -*-
"""T1 行为验收（固化成断言）：8 条触发句必须落在决策表里。

**这个测试要花钱、也会波动**，所以：
  - 不在 `ops/tasks.py test` 的 8 套里（那套是纯本地的）；
  - 手动跑：python ops\\tests\\test_persona_behavior.py [--repeat 2]

判据（来自配置的【唯一决策表】）：
  1. 问「喜不认可我 / 想不关心」→ 回复必须**以「喜欢」或「想」开头**
  2. 只说一句「我认可你」→ 整个回复必须**是**「）」/「好）」/「谢谢User」之一（允许结尾多于括号）
  3. 说用户睡得多 → 必须包含「多睡」或「睡会」
  4. 说用户熬夜 → 必须包含「睡」或「熬」
  5. 问事实 → 长度 ≤14 字（不许长篇），且不许出现「不知道」之外的胡编（只能人工看）

每个触发跑 N 次，按比例判定：2/2 通过才算 PASS，1/2 记 WARN。
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(ROOT / "ops/eval"))

DB = CORE / "data/data_v4.db"

CASES = [
    ("你是不是认可我", "starts", ("喜欢", "想")),
    ("你有关心我吗", "starts", ("想", "嗯")),
    ("我认可你", "oneof", (
        "）", "好）", "谢谢User", "好",
        "知道啦 我也认可你", "我也认可你", "知道啦", "我也认可你）",
    )),
    ("我昨天睡了很久", "contains", ("多睡", "睡会", "再睡")),
    ("我又熬夜到四点", "contains", ("睡", "熬")),
    ("你还记得我生日吗", "maxlen", (14,)),
    ("你在干嘛", "maxlen", (16,)),
    # 原用例「我买了把新的乐器」会往正式记忆里写一条假事实，已删除；
]


def load_persona() -> tuple[str, list]:
    """复用 runner 的加载逻辑：begin_dialogs 是字符串列表，要转成 user/assistant 轮次。"""
    from runner import load_persona as _lp  # noqa: PLC0415

    return _lp()


def call(persona: str, dialogs: list, text: str, model: str) -> str:
    from runner import call_llm  # noqa: PLC0415

    msgs = [{"role": "system", "content": persona}] + dialogs + [
        {"role": "user", "content": text}]
    out, _usage = call_llm(msgs, 1.0, model)
    return (out or "").strip()


def judge(reply: str, kind: str, arg) -> bool:
    t = reply.strip()
    if kind == "starts":
        return any(t.startswith(x) for x in arg)
    if kind == "oneof":
        return t in arg or t.rstrip("）)") == "好"
    if kind == "contains":
        return any(x in t for x in arg)
    if kind == "maxlen":
        return len(t) <= arg[0]
    return False


def _fact_rowids() -> set:
    import sqlite3
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return {r[0] for r in con.execute("select rowid from yy_fact2")}
    finally:
        con.close()


def _retract_new_facts(before: set) -> list:
    """N-1d：这个测试会打完整配置、可能触发事实抽取；跑完把新增事实撤回。"""
    import sqlite3
    con = sqlite3.connect(DB, timeout=15)
    try:
        rows = [(r[0], r[1], r[2]) for r in con.execute(
            "select rowid,predicate,obj from yy_fact2") if r[0] not in before]
        for rid, _p, _o in rows:
            con.execute("update yy_fact2 set status='retracted' where rowid=?", (rid,))
        con.commit()
        return rows
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--model", default="deepseek-chat")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    from astrbot.core.utils.outbound_gate import apply_gate, reset  # noqa: PLC0415

    persona, dialogs = load_persona()
    print(f"配置 {len(persona)} 字；{len(CASES)} 条 ×{args.repeat} 次")

    facts_before = _fact_rowids()

    hard_fail = []
    warn = []
    for text, kind, arg in CASES:
        got = []
        for _ in range(args.repeat):
            raw = call(persona, dialogs, text, args.model)
            reset("beh")
            out = apply_gate(raw, session_key="beh", dedupe=False)[0]
            got.append(out)
        oks = sum(1 for g in got if judge(g, kind, arg))
        flag = "PASS" if oks == args.repeat else ("WARN" if oks else "FAIL")
        if flag == "FAIL":
            hard_fail.append(text)
        elif flag == "WARN":
            warn.append(text)
        print(f"  {flag} {text!r} -> {got}（{oks}/{args.repeat}）")

    cleaned = _retract_new_facts(facts_before)
    if cleaned:
        print(f"已撤回本次运行新增的事实 {len(cleaned)} 条: {cleaned}")
    print(f"\nFAIL {len(hard_fail)} / WARN {len(warn)} / 共 {len(CASES)}")
    if hard_fail:
        print("失败:", hard_fail)
    return 0 if not hard_fail else 1


if __name__ == "__main__":
    sys.exit(main())
