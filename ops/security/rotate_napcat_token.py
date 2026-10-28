# -*- coding: utf-8 -*-
"""P1-7 收尾：给 NapCat ↔ AstrBot 的 WebSocket 加 token（**默认只预演**）。

为什么单独拎出来：改它要动 NapCat 的配置并让它重连，**可能触发重新扫码**，
所以这个脚本默认 `--dry-run`，真正执行要显式加 `--apply`，而且建议你在场。

现状（2026-10-25 08:45）：`onebot11_100000000.json` 里
    websocketClients = {"url":"ws://127.0.0.1:6199/ws", "token":"", ...}
也就是**没有 token**，本机任何进程连上 6199 都能冒充 QQ 通道。

做法：
  1. 生成随机 token；
  2. 改 NapCat 的 `websocketClients[0].token`（备份原文件）；
  3. 改 AstrBot 平台配置 `ws_reverse_token`（如果这个版本支持；不支持就只改 NapCat）；
  4. 打印"接下来要做什么"（重启 AstrBot → 让 NapCat 重连 → 确认 6199 连接恢复）。

回滚：
  cp <备份> onebot11_100000000.json && cp <备份> cmd_config.json
  然后重启 AstrBot、让 NapCat 重连。

用法：
  python ops\\security\\rotate_napcat_token.py            # 预演，不改任何东西
  python ops\\security\\rotate_napcat_token.py --apply
"""
from __future__ import annotations

import argparse
import json
import secrets
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:/demo")
NAPCAT_CFG = (ROOT.parent / "napcat6/NapCat/NapCat.52230.Shell/versions/9.9.33-52230/"
              "resources/app/napcat/config/onebot11_100000000.json")
ASTRBOT_CFG = ROOT / "instances/11111111-1111-4111-8111-111111111111/core/data/cmd_config.json"
BAK = ROOT / "backups/secrets"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--token", default=None, help="指定 token（默认随机生成）")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    if not NAPCAT_CFG.exists():
        print(f"找不到 NapCat 配置: {NAPCAT_CFG}")
        return 1
    napcat = json.loads(NAPCAT_CFG.read_text(encoding="utf-8-sig"))
    clients = (napcat.get("network") or {}).get("websocketClients") or []
    if not clients:
        print("没有 websocketClients，先确认 NapCat 是不是用反向 WS 连 AstrBot")
        return 1
    cur = clients[0].get("token", "")
    print(f"NapCat 当前 token: {cur!r}（{'空 = 没有鉴权' if not cur else '已设置'}）")
    print(f"连接到: {clients[0].get('url')}")

    ast = json.loads(ASTRBOT_CFG.read_text(encoding="utf-8-sig"))
    plat = next((p for p in ast.get("platform", []) if p.get("type") == "aiocqhttp"), None)
    # 适配器读的是 platform_config["ws_reverse_token"]（aiocqhttp_platform_adapter.py:60），
    # 当前实例没写这个键而已，不代表不支持。
    supported = plat is not None
    print(f"AstrBot 侧: 适配器支持 ws_reverse_token = {'是' if supported else '否'}"
          f"（当前{'已设置' if (plat or {}).get('ws_reverse_token') else '未设置'}）")

    token = args.token or secrets.token_hex(20)
    print(f"将写入的 token: {token[:8]}…（{len(token)} 位）")

    if not args.apply:
        print("\n这是预演，什么都没改。真正执行要 --apply，并且建议你人在场。")
        print("执行前请确认：NapCat 的二维码在手上，或者已经确认它不会掉登录。")
        return 0

    BAK.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(NAPCAT_CFG, BAK / f"onebot11.bak.{ts}.json")
    clients[0]["token"] = token
    NAPCAT_CFG.write_text(json.dumps(napcat, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已改 NapCat 配置（备份 backups/secrets/onebot11.bak.{ts}.json）")

    if supported:
        shutil.copy2(ASTRBOT_CFG, BAK / f"cmd_config.json.bak.{ts}")
        plat["ws_reverse_token"] = token
        ASTRBOT_CFG.write_text(json.dumps(ast, ensure_ascii=False, indent=4),
                               encoding="utf-8")
        print("已改 AstrBot 平台配置")

    print("""
接下来（顺序别反）：
  1. 重启 AstrBot：python ops\\tasks.py restart-astrbot
  2. 让 NapCat 重连（它每 3 秒重试一次；如果 60 秒还没连上，去 WebUI 里点重连/重启）
  3. 确认：python ops\\tasks.py status    → 6199 连接 ≥1
  4. 发一条消息验证收发

如果掉了登录（收到扫码提示）：
  用 backups/secrets/onebot11.bak.<ts>.json 覆盖回去，再重启，扫码恢复。
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())

