# -*- coding: utf-8 -*-
"""线上同构复现：把线上真实请求（配置 + begin_dialogs + 真实历史 + 用户这句话）跑一遍。

之前的 runner.py 没带历史，所以复现不出线上的"我也认可你（"。
这个脚本把历史一起塞进去，输出和线上走同一套出站门禁。

用法：
  python ops/eval/live_repro.py                     # 默认跑用户点名的那几条
  python ops/eval/live_repro.py --inputs "我回来了" "你好热啊"
  python ops/eval/live_repro.py --history 125 --tag before
"""
import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(CORE))

from astrbot.core.utils.outbound_gate import apply_gate  # noqa: E402

DB = CORE / "data/data_v4.db"
API = "https://api.deepseek.com/v1/chat/completions"
UMO = "aiocqhttp:FriendMessage:100000000"

DEFAULT_INPUTS = [
    "我回来了",
    "我可以鼓励你一下吗",
    "你有关心我吗",
    "我认可你",
    "你是不是也认可我",
    "我又睡晚了",
    "我们来联机玩mc吧",
    "我买了件新东西",
    "你还记得我们约定的事吗",
    "我们第一次聊天是什么时候",
    "我是不是很没用",
    "用一句话夸我",
    "朋友刚才说关心你了",
]


def load_persona():
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    sp, bd = db.execute(
        "select system_prompt, begin_dialogs from personas where persona_id='ajing'"
    ).fetchone()
    db.close()
    dialogs = json.loads(bd) if bd else []
    ctx = [{"role": "user" if i % 2 == 0 else "assistant", "content": d}
           for i, d in enumerate(dialogs)]
    return sp, ctx


def load_history(limit: int):
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    content = db.execute("select content from conversations where user_id=?",
                         (UMO,)).fetchone()[0]
    db.close()
    hist = json.loads(content)[-limit:]
    for m in hist:
        m.pop("_no_save", None)
        if isinstance(m.get("content"), list):
            m["content"] = [p for p in m["content"]
                            if isinstance(p, dict) and p.get("type") == "text"]
    return [m for m in hist if m.get("content")]


def call(messages, retries=3):
    import local_keys as lk

    payload = {"model": "deepseek-chat", "messages": messages,
               "temperature": 1.0, "max_tokens": 200, "stream": False}
    for i in range(retries):
        try:
            req = urllib.request.Request(
                API, data=json.dumps(payload).encode(),
                headers={"Authorization": f"Bearer {lk.DEEPSEEK_KEY}",
                         "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                d = json.loads(r.read().decode())
            return (d["choices"][0]["message"]["content"] or "").strip()
        except urllib.error.HTTPError as e:
            print(f"  HTTP {e.code} retry {i+1}")
        except Exception as e:  # noqa: BLE001
            print(f"  {type(e).__name__} retry {i+1}")
        time.sleep(2 * (i + 1))
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", nargs="*", default=None)
    ap.add_argument("--from-jsonl", default=None,
                    help="从 jsonl 读输入（每行 {input: ...}）")
    ap.add_argument("--history", type=int, default=125)
    ap.add_argument("--tag", default="")
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    persona, dialogs = load_persona()
    hist = load_history(args.history)
    inputs = args.inputs or DEFAULT_INPUTS
    if args.from_jsonl:
        import json as _json
        rows = [ _json.loads(ln) for ln in
                 open(args.from_jsonl, encoding="utf-8") if ln.strip() ]
        inputs = [r["input"] for r in rows]
    print(f"配置 {len(persona)} 字 + 预设对话 {len(dialogs)} 条 + 真实历史 {len(hist)} 条\n")

    rows = []
    for text in inputs:
        msgs = ([{"role": "system", "content": persona}] + dialogs + hist
                + [{"role": "user", "content": text}])
        outs = []
        for _ in range(args.repeat):
            raw = call(msgs)
            gated, acts = apply_gate(raw, session_key="repro", dedupe=False)
            outs.append(gated)
        rows.append({"input": text, "outs": outs, "gated": outs[0]})
        print(f"  用户: {text}")
        for o in outs:
            print(f"    -> {o!r}")

    if args.tag:
        out = HERE / "reports" / f"repro_{args.tag}.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n写入 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

