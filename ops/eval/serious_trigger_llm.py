# -*- coding: utf-8 -*-
"""U2 方案 A 离线验证：正则高精度 + 模型只兜底"模糊句"（不改生产代码）。"""
from __future__ import annotations

import argparse, json, os, sys, urllib.request
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:/demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(CORE))

PROMPT = """判断这句话：机器人要回的是"认真对待"（需要多想一下、不能一句玩笑带过），还是"随口接"（玩笑、闲聊、分享）。

判 1（认真）的例子：
- 我在想要不要搬出去住
- 用户妈妈好像不太认可我
- 说好一起的结果用户放我鸽子
- 我跟用户已经三天没说话了

判 0（随口接）的例子：
- 新番第一集就给我看哭了 好看
- 这把我carry了 快夸我
- 刚点了炸鸡 香
- 嘿嘿 猜猜我在干嘛

只回一个数字：1 或 0，不要解释。"""


def ask_llm(text):
    import local_keys as lk
    body = json.dumps({"model": "deepseek-chat",
                       "messages": [{"role": "system", "content": PROMPT},
                                    {"role": "user", "content": text}],
                       "temperature": 0.0, "max_tokens": 4, "stream": False}).encode()
    req = urllib.request.Request("https://api.deepseek.com/v1/chat/completions", data=body,
                                 headers={"Authorization": "Bearer " + lk.DEEPSEEK_KEY,
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read().decode())
    txt = (d["choices"][0]["message"]["content"] or "").strip()
    return txt.startswith("1")


def ambiguous(text, mode):
    from astrbot.core.utils.style_profile import SUPPORT_RE, COMFORT_RE, SERIOUS_RE
    if mode == "serious":
        return False
    if SERIOUS_RE.search(text) or COMFORT_RE.search(text):
        return True
    if len(text) >= 8:
        return True
    if SUPPORT_RE.search(text):
        return False
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(ROOT / "ops/eval/serious_trigger_fresh_11.jsonl"))
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    from astrbot.core.utils.style_profile import detect_mode

    cases = [json.loads(l) for l in Path(args.file).read_text(encoding="utf-8").splitlines() if l.strip()]
    tp = fp = fn = tn = calls = 0
    rows = []
    for c in cases:
        want = c["label"] == "serious"
        mode = detect_mode(c["text"])
        used = False
        if mode == "serious":
            pred = True
        elif ambiguous(c["text"], mode):
            pred = ask_llm(c["text"]); used = True; calls += 1
        else:
            pred = False
        if want and pred: tp += 1
        elif want and not pred: fn += 1
        elif not want and pred: fp += 1
        else: tn += 1
        rows.append((c["id"], c["text"], want, pred, used, mode))
    prec = tp / (tp + fp) if (tp + fp) else 0
    rec = tp / (tp + fn) if (tp + fn) else 0
    print("样本 %d：TP=%d FP=%d FN=%d TN=%d" % (len(cases), tp, fp, fn, tn))
    print("  precision=%.2f recall=%.2f  模型调用 %d 次" % (prec, rec, calls))
    for cid, text, want, pred, used, mode in rows:
        mark = "OK " if want == pred else "!! "
        print("  %s%s regex=%-8s llm=%-5s pred=%-5s %s" % (mark, cid, mode, used, pred, text))
    return 0 if (prec >= 0.85 and rec >= 0.80) else 1


if __name__ == "__main__":
    sys.exit(main())
