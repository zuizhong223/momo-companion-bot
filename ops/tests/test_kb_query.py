# -*- coding: utf-8 -*-
"""A09 回归：主动检索的 query 不能取“用户上一句”，要选可承接的话题。"""
import os
import sys
import datetime
import json
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

from astrbot.dashboard.services.proactive_service import (  # noqa: E402
    _pick_kb_query,
    _sources_context,
    _time_phrase,
    _with_time_context,
)

PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def main() -> int:
    print("\n[1] 有 assistant：承接机器人自己的话，不取用户上一句")
    q = _pick_kb_query([
        {"role": "user", "content": [{"type": "text", "text": "我天天都很关心你"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "你又熬夜了吧 这个点才起（"}]},
    ])
    check(q.startswith("你又熬夜"), "query=机器人上一句", q)
    check("关心你" not in q, "不含用户上一句", q)

    print("\n[2] 没有 assistant：只接“像问题”的用户消息")
    q = _pick_kb_query([{"role": "user", "content": "你还记得我生日吗"}])
    check("生日" in q, "问句→检索", q)

    print("\n[3] 没有 assistant、用户只是表达：不检索")
    q = _pick_kb_query([{"role": "user", "content": "我天天都很关心你"}])
    check(q == "", "陈述句→不检索", repr(q))

    print("\n[4] assistant 太短：回退到用户问句")
    q = _pick_kb_query([
        {"role": "assistant", "content": [{"type": "text", "text": "嗯嗯"}]},
        {"role": "user", "content": [{"type": "text", "text": "你在干嘛"}]},
    ])
    check(q == "你在干嘛", "短 assistant 跳过", q)

    print("\n[5] 去掉 URL 和 system_reminder")
    q = _pick_kb_query([{
        "role": "assistant",
        "content": [{"type": "text",
                     "text": "<system_reminder>x</system_reminder>看这个 https://x.com/a 好离谱"}],
    }])
    check("http" not in q and "system_reminder" not in q, "清洗干净", q)

    print("\n[6] 主动生成带时段：15:30 不能说成早上")
    check(_time_phrase(6) == "早上", "6 点早上")
    check(_time_phrase(15) == "下午", "15 点下午")
    check(_time_phrase(1) == "深夜", "1 点深夜")
    ins = _with_time_context("随便说一句", datetime.datetime(2026, 10, 25, 15, 30))
    check("15:30" in ins and "下午" in ins and "早上" not in ins.split("\n")[0],
          "instruction 带 15:30/下午", ins)

    print("\n[7] 外部来源上下文：只取最近、非 stale 的来源")
    tmp = Path(tempfile.mkdtemp()) / "ops"
    tmp.mkdir(parents=True)
    (tmp / "external_seen.jsonl").write_text(
        "\n".join([
            json.dumps({"title": "旧素材", "url": "https://b23.tv/old", "stale": True},
                       ensure_ascii=False),
            json.dumps({"title": "示例视频", "url": "https://b23.tv/x"},
                       ensure_ascii=False),
        ]) + "\n", encoding="utf-8")
    ctx = _sources_context(root=tmp.parent)
    check("示例视频" in ctx and "https://b23.tv/x" in ctx, "来源进上下文", ctx)
    check("旧素材" not in ctx, "stale 不注入")

    print("\n[8] 同一 query 不连续复用")
    hist = [{"role": "assistant", "content": [
        {"type": "text", "text": "然后我就想起来那个示例视频 然后我就"}]}]
    q1 = _pick_kb_query(hist)
    check(bool(q1), "第一次有 query", q1)
    check(_pick_kb_query(hist, q1) == "", "同 query 第二次跳过")
    check(_pick_kb_query(hist, "别的") == q1, "不同 last_query 仍可用", q1)

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
