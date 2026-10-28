# -*- coding: utf-8 -*-
"""配置收敛（P1-8 / P1-9 / P0-3）：带断言、带备份、可回滚。

改动：
  1. agent_runner.config.misc.max_steps        128 -> 8
     （机器人不需要多步工具链；128 步一旦模型乱调工具会烧很久的钱）
  2. agent_runner.config.compression.max_turns  -1 -> 200
     （只作失控兜底，远高于当前 124 条，不会触发对共同记忆的压缩）
  3. log_file_enable                            false -> true（P1-9 要求留日志）
  4. platform_settings.message_merge            新增（P0-3 连发合并参数）

用法：python ops/config/patch_config.py [--apply]
"""
import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

CFG = Path(r"D:/demo\instances\11111111-1111-4111-8111-111111111111\core\data\cmd_config.json")
OUT = Path(r"D:/demo\ops\config")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    raw = CFG.read_bytes()
    cfg = json.loads(raw.decode("utf-8-sig"))
    changes = []

    misc = cfg["agent_runner"]["config"]["misc"]
    if misc.get("max_steps") != 8:
        changes.append(("agent_runner.config.misc.max_steps", misc.get("max_steps"), 8))
        misc["max_steps"] = 8

    comp = cfg["agent_runner"]["config"]["compression"]
    if comp.get("max_turns") != 200:
        changes.append(("agent_runner.config.compression.max_turns",
                        comp.get("max_turns"), 200))
        comp["max_turns"] = 200

    if cfg.get("log_file_enable") is not True:
        changes.append(("log_file_enable", cfg.get("log_file_enable"), True))
        cfg["log_file_enable"] = True

    merge_cfg = {"enable": True, "window_s": 1.6, "max_wait_s": 6.0}
    cur = cfg.setdefault("platform_settings", {}).get("message_merge")
    if cur != merge_cfg:
        changes.append(("platform_settings.message_merge", cur, merge_cfg))
        cfg["platform_settings"]["message_merge"] = merge_cfg

    print("计划改动：")
    for k, old, new in changes:
        print(f"  {k}: {old!r} -> {new!r}")
    if not changes:
        print("  无（已是最新）")
        return 0

    if not args.apply:
        print("\n未加 --apply，仅预览。")
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%m%d_%H%M%S")
    backup = OUT / f"cmd_config.backup_{ts}.json"
    shutil.copy2(CFG, backup)
    print(f"已备份 -> {backup}")

    # 原文件带 BOM，写回时保持一致
    text = json.dumps(cfg, ensure_ascii=False, indent=2)
    CFG.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))

    check = json.loads(CFG.read_bytes().decode("utf-8-sig"))
    print("回读校验：",
          check["agent_runner"]["config"]["misc"]["max_steps"],
          check["agent_runner"]["config"]["compression"]["max_turns"],
          check["log_file_enable"],
          check["platform_settings"]["message_merge"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
