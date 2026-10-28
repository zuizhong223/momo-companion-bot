# -*- coding: utf-8 -*-
"""口头禅预算测试：不碰正式库，状态/变体库都用临时文件。"""
import json
import os
import sys
import tempfile
from pathlib import Path
ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

TMP = Path(os.environ.get("DEMO_TIC_TMP")
           or tempfile.mkdtemp(prefix="yy_tic_"))
TMP.mkdir(parents=True, exist_ok=True)
TAG = str(os.getpid())
os.environ["DEMO_TIC_STATE"] = str(TMP / f"tic_state_{TAG}.json")
bank = {
    "难受/安慰": [
        {"her": "好好休息吧", "bubbles": 1},
        {"her": "唔（ 那该怎么办）", "bubbles": 2},
        {"her": "没有没有 / 和你聊天很开心（", "bubbles": 2},
        {"her": "先歇会儿", "bubbles": 1},
    ],
    "买东西/分享": [{"her": "那很好了", "bubbles": 1}, {"her": "什么轴的", "bubbles": 1}],
    "夸机器人/可爱": [{"her": "哪有", "bubbles": 1}, {"her": "我记性好", "bubbles": 1}],
    "关心你/支持": [{"her": "好好好", "bubbles": 1}, {"her": "嗯嗯 想", "bubbles": 1}],
}
BANK = TMP / f"bank_{TAG}.json"
BANK.write_text(json.dumps(bank, ensure_ascii=False), encoding="utf-8")
os.environ["DEMO_TIC_BANK"] = str(BANK)

try:
    from astrbot.core.utils import tic_budget as tb  # noqa: E402
except Exception:  # 独立跑测试时直接用同目录模块
    sys.path.insert(0, str(Path(__file__).parent))
    import tic_budget as tb  # noqa: E402

ok = True


def check(cond, label, extra=""):
    global ok
    ok = ok and bool(cond)
    print(("OK   " if cond else "FAIL ") + label + (f" | {extra}" if extra else ""))


tb.reset("s1")
t1, a1 = tb.apply_tic_budget("摸摸 怎么了（", "s1", "comfort", record=True)
check(t1 == "摸摸 怎么了（", "第一条保留", t1)

t2, a2 = tb.apply_tic_budget("摸摸 怎么了（", "s1", "comfort", record=True)
check(t2 != t1, "第二条改写", t2)
check("摸摸" not in t2 and "怎么了" not in t2, "改写后无超频词", t2)
check(t2 in tb.SAFE_VARIANTS["难受/安慰"], "来自安全变体", t2)
check(bool(a2) and "口头禅预算" in a2[0], "有动作记录", str(a2))

t3, a3 = tb.apply_tic_budget("摸摸 怎么了（", "s1", "comfort", record=True)
check(t3 not in {t1, t2}, "第三条也不重复", t3)

# 非模板不碰
t4, a4 = tb.apply_tic_budget("今天练习到三点", "s1", "banter", record=True)
check(t4 == "今天练习到三点" and not a4, "长句/非模板不改", t4)

# 不同会话不串
tb.reset("s2")
t5, _ = tb.apply_tic_budget("摸摸 怎么了（", "s2", "comfort", record=True)
check(t5 == "摸摸 怎么了（", "新会话第一条保留", t5)

# 分享/夸/关心你
tb.reset("s3")
for _ in range(2):
    t6, _ = tb.apply_tic_budget("新的好啊（", "s3", "banter", record=True)
check("新的好啊" not in t6, "新的好啊 第二条改写", t6)

tb.reset("s4")
for _ in range(2):
    t7, _ = tb.apply_tic_budget("没有没有（", "s4", "banter", record=True)
check("没有没有" not in t7, "没有没有 第二条改写", t7)

tb.reset("s5")
for _ in range(2):
    t8, _ = tb.apply_tic_budget("想）", "s5", "support", record=True)
check(t8 != "想）", "想）第二条改写", t8)

# 确定性：同样窗口重复调用，结果一致
tb.reset("s6")
tb.apply_tic_budget("摸摸 怎么了（", "s6", "comfort", record=True)
a, _ = tb.apply_tic_budget("摸摸 怎么了（", "s6", "comfort", record=False)
b, _ = tb.apply_tic_budget("摸摸 怎么了（", "s6", "comfort", record=False)
check(a == b, "重复调用结果一致", f"{a} / {b}")

# 晚安：第二条不再只是「晚安（」
tb.reset("s8")
w1, _ = tb.apply_tic_budget("晚安（", "s8", "banter", record=True)
check(w1 == "晚安（", "晚安第一条保留", w1)
w2, _ = tb.apply_tic_budget("晚安（", "s8", "banter", record=True)
check(w2 not in ("晚安（", "晚安"), "晚安第二条改写", w2)

# 在干嘛 / 吃了吗 / 我也关心你
tb.reset("s9")
for _ in range(2):
    q1, _ = tb.apply_tic_budget("练习（", "s9", "banter", record=True)
check(q1 != "练习（", "练习第二条改写", q1)

tb.reset("s10")
for _ in range(2):
    q2, _ = tb.apply_tic_budget("还没吃（", "s10", "banter", record=True)
check("还没吃" not in q2 or q2 != "还没吃（", "还没吃第二条改写", q2)

tb.reset("s11")
for _ in range(2):
    q3, _ = tb.apply_tic_budget("我也关心你（", "s11", "support", record=True)
check(q3 != "我也关心你（", "我也关心你第二条改写", q3)

tb.reset("s14")
for _ in range(2):
    q6, _ = tb.apply_tic_budget("想） 我也关心你", "s14", "support", record=True)
check(q6 != "想） 我也关心你", "想）+我也关心你 第二条改写", q6)

tb.reset("s15")
for _ in range(2):
    q7, _ = tb.apply_tic_budget("在呢（", "s15", "banter", record=True)
check(q7 != "在呢（", "在呢第二条改写", q7)

tb.reset("s12")
for _ in range(2):
    q4, _ = tb.apply_tic_budget("我也认可你（", "s12", "support", record=True)
check(q4 != "我也认可你（", "我也认可你第二条改写", q4)

# 状态持久化
tb.reset("s7")
tb.apply_tic_budget("摸摸 怎么了（", "s7", "comfort", record=True)
tb._save()
tb._loaded_path = None
c, _ = tb.apply_tic_budget("摸摸 怎么了（", "s7", "comfort", record=True)
check(c != "摸摸 怎么了（", "重启后仍记得窗口", c)

print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
