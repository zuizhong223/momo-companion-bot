# -*- coding: utf-8 -*-
"""A02 回归：适配器能力判定不靠 TypeError 试错。"""
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

from astrbot.dashboard.services.open_api_service import (  # noqa: E402
    _accepts_delivery_params,
)

PASS = True


def check(cond: bool, label: str) -> None:
    global PASS
    if not cond:
        PASS = False
    print(f"  {'OK  ' if cond else 'FAIL'} {label}")


def main() -> int:
    print("\n[1] 显式 client_token：支持")
    def with_token(umo, message, client_token=None):
        return None
    check(_accepts_delivery_params(with_token) is True, "client_token 参数")

    print("\n[2] **kwargs：按支持处理")
    def with_kwargs(umo, message, **kwargs):
        return None
    check(_accepts_delivery_params(with_kwargs) is True, "**kwargs")

    print("\n[3] 没有台账参数：不支持，应显式报错")
    def old_adapter(umo, message):
        return None
    check(_accepts_delivery_params(old_adapter) is False, "旧适配器 False")

    print("\n[4] 内置函数没有可检查签名：不支持")
    check(_accepts_delivery_params(len) is False, "builtin False")

    print("\nRESULT:", "PASS" if PASS else "FAIL")
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
