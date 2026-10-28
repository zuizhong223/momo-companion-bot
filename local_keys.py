# -*- coding: utf-8 -*-
"""本地密钥加载器：所有密钥从同目录 .env 读取（.env 不进 git）。"""
from pathlib import Path

_ENV_PATH = Path(__file__).with_name(".env")


def _load_env(path: Path) -> dict:
    data = {}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return data


ENV = _load_env(_ENV_PATH)

DEEPSEEK_KEY = ENV.get("DEEPSEEK_KEY", "")
DASHBOARD_PASSWORD = ENV.get("DASHBOARD_PASSWORD", "")
OPENAPI_KEY = ENV.get("OPENAPI_KEY", "")
MINIMAX_KEY_FILE = ENV.get("MINIMAX_KEY_FILE", str(Path(__file__).with_name("minimax_key.txt")))
