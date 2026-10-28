#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""密钥轮换：一次改完所有存放位置，避免"只改了 .env"。

用法：
    python ops\\dev\\rotate_key.py --check              # 只看位置和长度（不显示 key）
    python ops\\dev\\rotate_key.py --name deepseek      # 交互输入新 key（不回显）
    python ops\\dev\\rotate_key.py --name minimax
    python ops\\dev\\rotate_key.py --name deepseek --rollback 20261026_1800

设计：
  - 新 key 只从 stdin 读，不落历史、不进命令行参数；
  - 改之前把相关文件备份到 ops/backup/keys_<时间戳>/；
  - 只改已知位置，改完打印"改了哪几处"，不打印 key 本身。
"""
from __future__ import annotations

import argparse
import getpass
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:/demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
CFG = CORE / "data/cmd_config.json"
ENV = ROOT / ".env"
DS_TXT = ROOT / "deepseek_key.txt"
MM_TXT = ROOT / "minimax_key.txt"
BACKUP = ROOT / "ops/backup"


def mask(s: str) -> str:
    s = s or ""
    return f"{s[:7]}…({len(s)})" if len(s) > 10 else f"<{len(s)} 字符>"


def load_cfg() -> dict:
    return json.loads(CFG.read_text(encoding="utf-8-sig"))


def save_cfg(cfg: dict) -> None:
    CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=4), encoding="utf-8")


def read_env() -> dict:
    out = {}
    if not ENV.exists():
        return out
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def write_env(updates: dict) -> None:
    lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    seen = set()
    new = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in updates:
            new.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            new.append(line)
    for k, v in updates.items():
        if k not in seen:
            new.append(f"{k}={v}")
    ENV.write_text("\n".join(new) + "\n", encoding="utf-8")


def backup(tag: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = BACKUP / f"keys_{ts}"
    dst.mkdir(parents=True, exist_ok=True)
    for f in (ENV, CFG, DS_TXT, MM_TXT):
        if f.exists():
            shutil.copy2(f, dst / f.name)
    (dst / "README.txt").write_text(
        f"轮换前备份（{tag}），含明文密钥，用完请删除或加密归档。\n", encoding="utf-8")
    return dst


def check() -> int:
    cfg = load_cfg()
    env = read_env()
    print("=== DeepSeek ===")
    src = next((s for s in cfg.get("provider_sources", [])
                if s.get("provider") == "deepseek"), None)
    if src is not None:
        k = src.get("key") or []
        first = k[0] if isinstance(k, list) and k else (k if isinstance(k, str) else "")
        print(f"  cmd_config.json  provider_sources[{src.get('id')}].key = {mask(first)}"
              f"（列表 {len(k) if isinstance(k, list) else 1} 项）")
    print(f"  .env             DEEPSEEK_KEY = {mask(env.get('DEEPSEEK_KEY', ''))}")
    if DS_TXT.exists():
        print(f"  deepseek_key.txt = {mask(DS_TXT.read_text(encoding='utf-8-sig').strip())}")
    print("=== MiniMax ===")
    for i, p in enumerate(cfg.get("provider", [])):
        if p.get("type") == "minimax_tts_api" and p.get("api_key"):
            print(f"  cmd_config.json  provider[{i}] {p.get('id')}.api_key = {mask(p['api_key'])}")
        if p.get("type") == "minimax_embedding" and p.get("embedding_api_key"):
            print(f"  cmd_config.json  provider[{i}] {p.get('id')}.embedding_api_key = "
                  f"{mask(p['embedding_api_key'])}")
    if MM_TXT.exists():
        print(f"  minimax_key.txt  = {mask(MM_TXT.read_text(encoding='utf-8-sig').strip())}")
    return 0


def rotate(name: str, from_file: str | None = None) -> int:
    if from_file:
        key = Path(from_file).read_text(encoding="utf-8-sig").strip()
        print(f"已从文件读取新 key: {mask(key)}")
    else:
        key = getpass.getpass("粘贴新 key（不回显，回车确认）: ").strip()
        if not key or len(key) < 16:
            print("key 太短，已中止")
            return 1
        again = getpass.getpass("再粘贴一次确认: ").strip()
        if key != again:
            print("两次不一致，已中止")
            return 1
    if not key or len(key) < 16:
        print("key 太短，已中止")
        return 1

    dst = backup(name)
    print(f"已备份到 {dst}")
    changed = []

    cfg = load_cfg()
    if name == "deepseek":
        for s in cfg.get("provider_sources", []):
            if s.get("provider") == "deepseek":
                s["key"] = [key] if isinstance(s.get("key"), list) else key
                changed.append(f"cmd_config.json provider_sources[{s.get('id')}].key")
        save_cfg(cfg)
        write_env({"DEEPSEEK_KEY": key})
        changed.append(".env DEEPSEEK_KEY")
        DS_TXT.write_text(key, encoding="utf-8")
        changed.append("deepseek_key.txt")
    elif name == "minimax":
        for p in cfg.get("provider", []):
            if p.get("type") == "minimax_tts_api":
                p["api_key"] = key
                changed.append(f"cmd_config.json provider[{p.get('id')}].api_key")
            if p.get("type") == "minimax_embedding":
                p["embedding_api_key"] = key
                changed.append(f"cmd_config.json provider[{p.get('id')}].embedding_api_key")
        save_cfg(cfg)
        MM_TXT.write_text(key, encoding="utf-8")
        changed.append("minimax_key.txt")
    else:
        print("未知名称，可选 deepseek / minimax")
        return 1

    print("\n已更新：")
    for c in changed:
        print("  -", c)
    print(f"\n新 key 指纹: {mask(key)}")
    print("\n下一步：")
    print("  1) 重启 AstrBot（务必带 DASHBOARD_PORT=22877）")
    print("  2) 验证能正常回复 / 能出语音")
    print("  3) 再去平台撤销旧 key")
    print("  4) 用旧 key 调一次应失败")
    return 0


def rollback(tag: str) -> int:
    src = BACKUP / f"keys_{tag}"
    if not src.exists():
        print("找不到备份:", src)
        return 1
    for f in src.iterdir():
        if f.name == "README.txt":
            continue
        target = {"cmd_config.json": CFG, ".env": ENV,
                  "deepseek_key.txt": DS_TXT, "minimax_key.txt": MM_TXT}.get(f.name)
        if target:
            shutil.copy2(f, target)
            print("已恢复", target)
    return 0



def verify() -> int:
    """用当前 .env 里的 key 做一次最小真实调用，确认可用（不打印 key）。"""
    import urllib.error
    import urllib.request

    env = read_env()
    key = env.get("DEEPSEEK_KEY", "").strip()
    if not key:
        print("DEEPSEEK_KEY 为空")
        return 1
    body = json.dumps({
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
    }).encode()
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
        print("DeepSeek 调用成功:",
              data.get("choices", [{}])[0].get("message", {}).get("content", "")[:10])
        return 0
    except urllib.error.HTTPError as e:
        print("DeepSeek 调用失败: HTTP", e.code, e.read().decode("utf-8", "replace")[:120])
        return 1
    except Exception as e:  # noqa: BLE001
        print("DeepSeek 调用异常:", type(e).__name__, e)
        return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--name", choices=["deepseek", "minimax"])
    ap.add_argument("--rollback")
    ap.add_argument("--from-file", help="从文件读取新 key（避免粘贴进聊天）")
    ap.add_argument("--verify", action="store_true", help="用当前配置的 key 做一次真实调用")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.check:
        return check()
    if args.rollback:
        return rollback(args.rollback)
    if args.verify:
        return verify()
    if not args.name:
        print(__doc__)
        return 0
    return rotate(args.name, args.from_file)


if __name__ == "__main__":
    sys.exit(main())
