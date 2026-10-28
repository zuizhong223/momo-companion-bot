# -*- coding: utf-8 -*-
"""P1/Q8：B 站分享正文只能是“一句 + 链接”，不能把视频标题整段拼进去。"""
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


def load_butler():
    spec = importlib.util.spec_from_file_location("demo_butler_bili", ROOT / "butler.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    mod = load_butler()
    sent = []
    title = "【完整标题】这个视频标题很长很长不要发出来"

    mod.http_json = lambda *a, **k: {
        "data": {"result": [{"bvid": "BV1xx411c7mD", "title": title}]}
    }
    lines = iter(("这个建得挺费劲的 苦力怕来得也太快",
                  "有点想看看后面还修不修"))
    mod.gen_proactive = lambda *a, **k: next(lines)
    mod.send_parts = lambda parts, **kw: (sent.append((parts, kw)) or (True, "sent"))
    mod.send_image = lambda *a, **k: (False, "failed")
    mod.subprocess.run = lambda *a, **k: None
    mod.shutil.which = lambda *a, **k: "msedge"
    mod.os.path.exists = lambda *a, **k: False
    mod._log_external_source = lambda *a, **k: None
    mod.log = lambda *a, **k: None

    ok = mod.bili_share()
    check(ok is True, "bili_share 返回 True")
    check(len(sent) == 2, "分段发两条文字", str(len(sent)))
    text1 = sent[0][0][0]["text"] if sent else ""
    text2 = sent[1][0][0]["text"] if len(sent) > 1 else ""
    check(text1.endswith("（"), "第一段以「（」收尾", text1)
    check(text2.split("\n")[0].endswith("）"), "第二段以「）」收尾", text2)
    check("https://www.bilibili.com/video/BV1xx411c7mD" in text2,
          "链接在第二段", text2)
    check(title not in text1 + text2, "完整标题没有进正文", text1 + text2)
    check("苦力怕" in text1, "第一段说哪里好玩", text1)
    check("想看看" in text2, "第二段说感受", text2)
    check(text2.count("\n") == 1, "第二段一行感受 + 一行链接", repr(text2))

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
