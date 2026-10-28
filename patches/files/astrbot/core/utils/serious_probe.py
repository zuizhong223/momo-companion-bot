# -*- coding: utf-8 -*-
"""认真话题的模型兜底探针（A2：默认影子，只记日志不生效）。

开关 DEMO_SERIOUS_LLM：
  off    -> 完全不调用
  shadow -> 后台算一次，写日志，不影响回复（默认）
  on     -> 阻塞最多 1.5s，命中就加"认真话题"提示

日志默认写到 <instance core>/data/serious_shadow.jsonl，可用
DEMO_SERIOUS_LOG 覆盖（测试用）。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import threading
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_PROMPT = (
    "判断这句话：机器人要回的是\"认真对待\"（需要多想一下、不能一句玩笑带过），还是\"随口接\"（玩笑、闲聊、分享）。\n\n"
    "判 1（认真）的例子：\n- 我在想要不要搬出去住\n- 用户妈妈好像不太认可我\n"
    "- 说好一起的结果用户放我鸽子\n- 我跟用户已经三天没说话了\n\n"
    "判 0（随口接）的例子：\n- 新番第一集就给我看哭了 好看\n- 这把我carry了 快夸我\n"
    "- 刚点了炸鸡 香\n- 嘿嘿 猜猜我在干嘛\n\n只回一个数字：1 或 0，不要解释。"
)
_cache: dict[str, tuple[float, bool | None]] = {}
_CACHE_TTL = 600.0
_lock = threading.Lock()


def mode() -> str:
    v = (os.environ.get("DEMO_SERIOUS_LLM") or "shadow").strip().lower()
    return v if v in ("off", "shadow", "on") else "shadow"


def log_path() -> Path:
    p = os.environ.get("DEMO_SERIOUS_LOG")
    return Path(p) if p else _ROOT / "data/serious_shadow.jsonl"


def worth_probe(text: str) -> bool:
    """正则没判 serious 时，这句值不值得问模型。"""
    t = (text or "").strip()
    if len(t) < 8:
        return False
    try:
        from astrbot.core.utils.style_profile import SUPPORT_RE
        if SUPPORT_RE.search(t) and len(t) < 12:
            return False
    except Exception:  # noqa: BLE001
        pass
    return True


def _key() -> str:
    try:
        cfg = json.loads((_ROOT / "data/cmd_config.json").read_text(encoding="utf-8-sig"))
        for s in cfg.get("provider_sources", []) or []:
            if s.get("provider") == "deepseek":
                k = s.get("key")
                if isinstance(k, list) and k:
                    return str(k[0])
                if isinstance(k, str):
                    return k
    except Exception:  # noqa: BLE001
        pass
    return ""


def classify(text: str, timeout: float = 1.5) -> bool | None:
    """True=认真 False=不认真 None=没问成（超时/key 缺失/异常）。"""
    t = (text or "").strip()
    if not t:
        return None
    h = hashlib.sha256(t.encode("utf-8")).hexdigest()
    now = datetime.datetime.now().timestamp()
    with _lock:
        hit = _cache.get(h)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
    key = _key()
    if not key:
        return None
    url = os.environ.get("DEMO_SERIOUS_LLM_URL") or "https://api.deepseek.com/v1/chat/completions"
    body = json.dumps({"model": "deepseek-chat",
                       "messages": [{"role": "system", "content": _PROMPT},
                                    {"role": "user", "content": t}],
                       "temperature": 0.0, "max_tokens": 4, "stream": False}).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode())
        txt = (d["choices"][0]["message"]["content"] or "").strip()
        pred = txt.startswith("1")
    except Exception:  # noqa: BLE001
        return None
    with _lock:
        _cache[h] = (now, pred)
    return pred


def _write(record: dict) -> None:
    try:
        p = log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def shadow(text: str, regex_mode: str) -> None:
    """后台算一次并记日志，不影响回复。"""
    def _run() -> None:
        t0 = datetime.datetime.now().timestamp()
        pred = classify(text)
        _write({"at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "text": text[:120], "regex": regex_mode, "llm": pred,
                "ms": int((datetime.datetime.now().timestamp() - t0) * 1000)})
    threading.Thread(target=_run, daemon=True).start()


def record_live(text: str, regex_mode: str, pred: bool | None, ms: int = 0) -> None:
    _write({"at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "text": text[:120], "regex": regex_mode, "llm": pred, "ms": ms, "applied": True})
