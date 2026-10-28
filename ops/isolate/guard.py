# -*- coding: utf-8 -*-
"""隔离自检（第 0 步 0.2）：测试进程不许碰正式数据。

`DEMO_TEST=1` 时，任何落在正式目录（含 junction 解析后）的路径都直接拒绝运行。
不靠"端口通不通"判断——同机正式 NapCat 在跑时，测试进程照样连得上 6199。
"""
from __future__ import annotations

import os
from pathlib import Path

PROD_ROOT = Path(r"D:/demo")
# 正式实例里允许被只读的东西（复制源），但绝不允许被写
PROD_DB = PROD_ROOT / "instances/11111111-1111-4111-8111-111111111111/core/data/data_v4.db"


def is_test() -> bool:
    return os.environ.get("DEMO_TEST", "").strip() not in ("", "0")


def _real(p: str | Path) -> Path:
    try:
        return Path(p).resolve()
    except Exception:  # noqa: BLE001
        return Path(str(p))


def under_prod(p: str | Path) -> bool:
    """realpath 之后再比——D:/demo/napcat 是 D:/demo 的联接，字符串比较会漏。"""
    rp, root = _real(p), _real(PROD_ROOT)
    try:
        rp.relative_to(root)
        return True
    except ValueError:
        return False


def assert_isolated(paths: list[str | Path], *, allow_readonly: bool = False) -> None:
    """测试模式下发现路径指向正式数据 → 直接退出。"""
    if not is_test():
        return
    bad = [str(p) for p in paths if under_prod(p)]
    if not bad:
        return
    if allow_readonly:
        print(f"[guard] 允许只读引用正式数据: {bad}")
        return
    raise SystemExit(
        "隔离自检失败：测试模式(DEMO_TEST=1)下这些路径指向正式数据：\n  "
        + "\n  ".join(bad)
        + "\n请把数据复制到临时目录再跑。"
    )


def summary() -> str:
    return (f"DEMO_TEST={'1' if is_test() else '0'} "
            f"PROD_ROOT={_real(PROD_ROOT)} "
            f"junction解析={'一致' if str(_real(PROD_ROOT)) == str(PROD_ROOT) else '不同'}")
