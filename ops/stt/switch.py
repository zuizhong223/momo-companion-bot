# -*- coding: utf-8 -*-
"""P2-13：STT 本地/云端开关（先能切、再谈删）。

现状：`provider_stt_settings` 关闭时用**本地 Whisper**（实例 venv 里装好）；
打开并指定 `provider_id` 时走云端 STT。

这个脚本只改配置，不动代码，切完要重启 AstrBot。
**不会**替你删本地模型——按计划"先测再删"。

用法：
  python ops\\stt\\switch.py                 # 看现状
  python ops\\stt\\switch.py --to cloud --provider-id minimax_stt
  python ops\\stt\\switch.py --to local
"""
import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:/demo")
CFG = ROOT / "instances/11111111-1111-4111-8111-111111111111/core/data/cmd_config.json"
BAK = ROOT / "backups/secrets"


def load() -> dict:
    return json.loads(CFG.read_text(encoding="utf-8-sig"))


def stt_providers(cfg: dict) -> list[str]:
    return [p.get("id") for p in cfg.get("provider", [])
            if "tts" not in str(p.get("id", "")).lower()
            and "whisper" in str(p.get("type", "")).lower()]


def show(cfg: dict) -> None:
    stt = cfg.get("provider_stt_settings", {})
    print(f"当前: enable={stt.get('enable')} provider_id={stt.get('provider_id')!r}")
    print(f"可用的 STT provider: {stt_providers(cfg)}")
    print("       enable=false = 完全不转写（语音会被当附件）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", choices=["local", "cloud"])
    ap.add_argument("--provider-id", default=None)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    cfg = load()
    show(cfg)
    if not args.to:
        return 0

    stt = cfg.setdefault("provider_stt_settings", {})
    if args.to == "cloud":
        if not args.provider_id:
            print("切云端要 --provider-id（在面板的 STT 提供商里看 id）")
            return 1
        stt["enable"] = True
        stt["provider_id"] = args.provider_id
    else:
        # 本地 = whisper_local 这个 provider（关掉 enable 是完全不转写，不是本地）
        stt["enable"] = True
        stt["provider_id"] = args.provider_id or "whisper_local"

    BAK.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(CFG, BAK / f"cmd_config.json.stt.{ts}")
    CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=4), encoding="utf-8")
    print(f"已切换 -> {args.to}；备份 backups/secrets/cmd_config.json.stt.{ts}")
    print("重启 AstrBot 生效；切完记得发一条语音验证转写。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
