# -*- coding: utf-8 -*-
"""P1-7：端口收敛到本机 + 轮换 Open API key。

做两件事（都可单独执行、都有备份）：
  1. `--bind-local`：把 dashboard.host 与 aiocqhttp.ws_reverse_host 从 0.0.0.0 改成 127.0.0.1；
  2. `--rotate-key`：生成新的 Open API key，更新 api_keys 表与 .env，旧 key 立刻失效。

不会动：NapCat 自己的 token（要改 NapCat 配置并重启，可能触发重新登录，留给人工）。

用法：
  python ops\\security\\rotate_secrets.py --bind-local
  python ops\\security\\rotate_secrets.py --rotate-key
"""
import argparse
import hashlib
import json
import re
import secrets
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:/demo")
INST = ROOT / "instances/11111111-1111-4111-8111-111111111111"
CFG = INST / "core/data/cmd_config.json"
DB = INST / "core/data/data_v4.db"
ENV = ROOT / ".env"
BAK = ROOT / "backups/secrets"


def _sha(key: str) -> str:
    """和 AstrBot 一致：PBKDF2-HMAC-SHA256(raw, salt=b"astrbot_api_key", 100000)。

    一开始我以为是 sha256(raw)，结果轮换后新 key 401 —— 库里的 hash 根本对不上。
    """
    return hashlib.pbkdf2_hmac("sha256", key.encode("utf-8"),
                               b"astrbot_api_key", 100_000).hex()


def _read_env() -> dict:
    out = {}
    for raw in ENV.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _write_env(d: dict) -> None:
    lines = ["# 由 ops/security/rotate_secrets.py 维护；不要提交到 git"]
    for k, v in d.items():
        lines.append(f"{k}={v}")
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def bind_local() -> int:
    raw = CFG.read_text(encoding="utf-8-sig")
    cfg = json.loads(raw)
    changed = []
    if cfg["dashboard"].get("host") != "127.0.0.1":
        cfg["dashboard"]["host"] = "127.0.0.1"
        changed.append("dashboard.host")
    for p in cfg.get("platform", []):
        if p.get("type") == "aiocqhttp" and p.get("ws_reverse_host") != "127.0.0.1":
            p["ws_reverse_host"] = "127.0.0.1"
            changed.append("aiocqhttp.ws_reverse_host")
    if not changed:
        print("已经是 127.0.0.1，不用改")
        return 0
    BAK.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(CFG, BAK / f"cmd_config.json.bak.{ts}")
    CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=4), encoding="utf-8")
    print(f"已改: {', '.join(changed)}；备份 backups/secrets/cmd_config.json.bak.{ts}")
    print("需要重启 AstrBot 生效。")
    return 0


def rotate_key() -> int:
    env = _read_env()
    if "OPENAPI_KEY" not in env:
        print("没有 OPENAPI_KEY")
        return 1
    old = env["OPENAPI_KEY"]
    con = sqlite3.connect(DB)
    row = con.execute(
        "select key_id, key_hash, key_prefix from api_keys where revoked_at is null"
    ).fetchone()
    if not row:
        print("没有可用的 api key")
        return 1
    key_id, key_hash, prefix = row
    if _sha(old) != key_hash:
        print(f"警告：.env 里的 key 与库里哈希不一致（{_sha(old)[:12]} vs {key_hash[:12]}）")
        print("继续轮换（以库为准），但如果管家用的是别的 key，先查清楚。")
    else:
        print("校验通过：.env 里的 key 与库里哈希一致")
    new = "demo-" + secrets.token_hex(24)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    BAK.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ENV, BAK / f".env.bak.{ts}")
    con.execute(
        "update api_keys set key_hash=?, key_prefix=?, updated_at=? where key_id=?",
        (_sha(new), new[:8], datetime.now().strftime("%Y-%m-%d %H:%M:%S"), key_id))
    con.commit()
    con.close()
    env["OPENAPI_KEY"] = new
    _write_env(env)
    print(f"已轮换 key：{new[:12]}…（前缀 {new[:8]}），备份 backups/secrets/.env.bak.{ts}")
    print("需要重启管家（它启动时读 .env）；旧 key 已失效。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind-local", action="store_true")
    ap.add_argument("--rotate-key", action="store_true")
    ap.add_argument("--repair", action="store_true",
                    help="只按当前 .env 的 key 重算哈希写回库里（轮换出错时用）")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if not (args.bind_local or args.rotate_key or args.repair):
        print(__doc__)
        return 0
    if args.bind_local:
        bind_local()
    if args.rotate_key:
        rotate_key()
    if args.repair:
        env = _read_env()
        key = env.get("OPENAPI_KEY", "")
        if not key:
            print("没有 OPENAPI_KEY")
            return 1
        con = sqlite3.connect(DB)
        con.execute("update api_keys set key_hash=?, key_prefix=?, updated_at=? "
                    "where revoked_at is null",
                    (_sha(key), key[:8], datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        con.commit()
        con.close()
        print(f"已按当前 .env 修正哈希（前缀 {key[:8]}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
