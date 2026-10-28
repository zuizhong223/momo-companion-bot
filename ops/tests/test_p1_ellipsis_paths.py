# -*- coding: utf-8 -*-
"""P1/Q6：文字出口、TTS 输入、TTS 旁路都不能把省略号发出去。"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(ROOT))
PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    from astrbot.core.utils.outbound_gate import apply_gate
    from astrbot.core.provider.sources import minimax_tts_api_source as tts

    spec = importlib.util.spec_from_file_location("demo_butler_q6", ROOT / "butler.py")
    butler = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = butler
    spec.loader.exec_module(butler)

    raw = "晚上好 吃了吗……"
    raw2 = "晚上好 吃了吗..."
    cfg = {
        "api_key": "x", "api_base": "https://example.invalid", "model": "speech-2.8-hd",
        "minimax-group-id": "g", "minimax-voice-id": "v",
    }
    provider = tts.ProviderMiniMaxTTSAPI(cfg, {})

    print("\n[1] 文字出口")
    out, _ = apply_gate(raw, session_key="q6-text")
    check("…" not in out and "..." not in out, "apply_gate 去省略号", repr(out))

    print("\n[2] TTS 输入（result_decorate 调 prepare_text）")
    p1 = provider.prepare_text(raw)
    p2 = provider.prepare_text(raw2)
    check("…" not in p1 and "..." not in p1, "provider 去省略号", repr(p1))
    check("…" not in p2 and "..." not in p2, "provider 去 ASCII 省略号", repr(p2))

    print("\n[3] TTS 旁路（管家主动语音）")
    s1 = butler._speech_text(raw)
    s2 = butler._speech_text(raw2)
    check("…" not in s1 and "..." not in s1, "butler 去省略号", repr(s1))
    check("…" not in s2 and "..." not in s2, "butler 去 ASCII 省略号", repr(s2))

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
