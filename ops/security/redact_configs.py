# -*- coding: utf-8 -*-
"""把 config 副本里的密钥字段抹成 REDACTED（默认 dry-run，--apply 才写）。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(r"D:/demo")
FILES = [
    ROOT / "ops/config/cmd_config.backup_1025_052134.json",
    ROOT / "ops/config/cmd_config.backup_1025_052855.json",
    ROOT / "patches/files/data/cmd_config.json",
    Path(r"D:/demo\data_copy\cmd_config.json"),
]
KEYS = {"api_key", "embedding_api_key", "key", "jwt_secret", "token",
        "ws_reverse_token", "secret", "password", "websearch_tavily_key",
        "websearch_bocha_key", "websearch_brave_key", "websearch_baidu_app_builder_key",
        "websearch_firecrawl_key", "websearch_exa_key", "websearch_anysearch_key",
        "websearch_serpapi_key"}


def scrub(o):
    changed = 0
    if isinstance(o, dict):
        for k, v in list(o.items()):
            if k in KEYS and isinstance(v, str) and v and v != "REDACTED":
                o[k] = "REDACTED"
                changed += 1
            elif k in KEYS and isinstance(v, list) and v:
                o[k] = ["REDACTED"]
                changed += 1
            else:
                changed += scrub(v)
    elif isinstance(o, list):
        for v in o:
            changed += scrub(v)
    return changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    for p in FILES:
        if not p.exists():
            print("MISSING", p)
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception as e:  # noqa: BLE001
            print("SKIP", p, type(e).__name__)
            continue
        n = scrub(d)
        print(f"{'APPLY' if args.apply else 'DRY  '} {p} fields={n}")
        if args.apply and n:
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False, indent=4), encoding="utf-8")
            tmp.replace(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
