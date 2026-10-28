# -*- coding: utf-8 -*-
"""本地模型 best-of-N 风格选择（离线，不接线上）。

用 Ollama 的 demo 生成 N 条候选，再用机器人的 3-gram/启发式打分选一条。
用法：
  python ops\\eval\\best_of_n.py --prompt "在干嘛" --n 4
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(r"D:/demo")
PERSONA = ROOT / "persona.txt"
sys.path.insert(0, str(ROOT / "ops/eval"))
from style_score import StyleModel  # noqa: E402

OLLAMA = "http://127.0.0.1:11434/api/chat"
MODEL = "demo"


def style_core(limit: int = 1800) -> str:
    text = PERSONA.read_text(encoding="utf-8")
    # 取「唯一决策表 + 语气层 + 说话风格」这几段，别把 2.5 万字全塞给本地模型
    return text[:limit]


def gen(prompt: str, system: str, temperature: float = 0.95) -> str:
    payload = {
        "model": MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"用户说：{prompt}\n只回一条，不要解释。"},
        ],
        "options": {"temperature": temperature, "num_predict": 40},
    }
    req = urllib.request.Request(
        OLLAMA, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.loads(r.read().decode())
    return ((d.get("message") or {}).get("content") or "").strip().strip('"')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--n", type=int, default=4)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    system = style_core()
    model = StyleModel()
    rows = []
    for i in range(args.n):
        try:
            text = gen(args.prompt, system)
        except Exception as e:  # noqa: BLE001
            print(f"  #{i+1} 生成失败: {e}")
            continue
        sc = model.score(text, "banter")
        rows.append((sc["final"], text, sc))
        print(f"  #{i+1} {sc['final']:.3f} ppl={sc['ppl']:.1f} {text!r}")
    if not rows:
        return 1
    rows.sort(key=lambda x: -x[0])
    print("\nBEST:", rows[0][1])
    print("score:", rows[0][2])
    return 0


if __name__ == "__main__":
    sys.exit(main())
