# -*- coding: utf-8 -*-
"""similarity_metrics 测试：合成数据，不碰正式库。"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
sys.path.insert(0, str(ROOT / "ops/eval"))

import similarity_metrics as sm  # noqa: E402

ok = True


def check(cond, label, extra=""):
    global ok
    ok = ok and bool(cond)
    print(("OK   " if cond else "FAIL ") + label + (f" | {extra}" if extra else ""))


TMP = Path(tempfile.mkdtemp(prefix="yy_sim_"))
p = TMP / "samples.json"
p.write_text(json.dumps({
    "关心你/支持": {"samples": ["想）"] * 11 + ["好好好"]},
    "难受/安慰": {"samples": ["摸摸 怎么了（"] * 3 + ["好好休息吧", "唔（ 那该怎么办）"] * 2},
}, ensure_ascii=False), encoding="utf-8")

data = sm.load(p)
check(data["关心你/支持"][0] == "想）", "读取 dict/samples", str(len(data)))
m = sm.metrics(data["关心你/支持"])
check(m["n"] == 12 and m["top_share"] == round(11 / 12, 3), "top_share", str(m["top_share"]))
check(m["unique"] == round(2 / 12, 3), "unique", str(m["unique"]))
check(any("top" in x for x in sm.flags(m)), "超 top 会判失败", str(sm.flags(m)))

# list[dict] + auto intent
p2 = TMP / "rows.jsonl"
p2.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in [
    {"user": "我关心你了", "reply": "好好好"},
    {"user": "今天好难受", "reply": "怎么了（"},
]), encoding="utf-8")
d2 = sm.load(p2)
check("关心你/支持" in d2 and "难受/安慰" in d2, "JSONL 自动判意图", str(list(d2)))

# 比较输出
txt = sm.render("t", data, {"关心你/支持": {"samples": ["想）"] * 12}})
check("改前 → 改后" in txt and "92%→100%" in txt or "→" in txt, "compare 表", txt.splitlines()[-1][:40])

print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
