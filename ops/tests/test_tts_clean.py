# -*- coding: utf-8 -*-
"""Q6：TTS 前/合成后统一清省略号、句号、换行。"""
import sys
from pathlib import Path

CORE = Path(r"D:\demo\instances\11111111-1111-4111-8111-111111111111\core")
sys.path.insert(0, str(CORE))

from astrbot.core.pipeline.result_decorate.stage import clean_tts_text  # noqa: E402

cases = {
    "吃了吗……": "吃了吗",
    "这还困吗 才下午诶。": "这还困吗 才下午诶",
    "  a\n\nb  ": "a b",
    "   ": "",
    "正常一句": "正常一句",
}
ok = True
for src, want in cases.items():
    got = clean_tts_text(src)
    print(("OK   " if got == want else "FAIL ") + repr(src) + " -> " + repr(got))
    ok = ok and got == want
print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
