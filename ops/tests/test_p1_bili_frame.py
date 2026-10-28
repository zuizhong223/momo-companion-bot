# -*- coding: utf-8 -*-
"""Q8：机器人说的话 -> 弹幕相似时刻；没把握必须回退，不硬塞一帧。"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(CORE))
PASS = True


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' | ' + extra) if extra else ''}")


def load_butler():
    spec = importlib.util.spec_from_file_location("butler_bili_frame_test", ROOT / "butler.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    mod = load_butler()
    rows = [
        (5, "这个树屋建得好好"),
        (20, "苦力怕炸了哈哈"),
        (30, "后面还修不修"),
    ]
    hit = mod._bili_match_danmaku("树屋刚建好就被苦力怕炸了 好笑", rows)
    check(hit is not None and abs(hit[1] - 20) < 1, "匹配到苦力怕那一帧", str(hit))
    check(mod._bili_match_danmaku("今天天气不错", rows) is None,
          "没有相似弹幕时返回 None")

    mod._bili_fetch_cid = lambda v: 1
    mod._bili_danmaku = lambda cid: rows
    mod._bili_videoshot_frame = lambda cid, bvid, at: ("/tmp/frame.jpg", 20)
    got = mod._bili_frame({"bvid": "BVtest"}, "树屋刚建好就被苦力怕炸了 好笑")
    check(got is not None and got[0] == "/tmp/frame.jpg", "命中时返回帧", str(got))
    check(mod._bili_frame({"bvid": "BVtest"}, "今天天气不错") is None,
          "不命中时回退", str(mod._bili_frame({"bvid": "BVtest"}, "今天天气不错")))

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
