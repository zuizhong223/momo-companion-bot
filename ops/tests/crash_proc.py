# -*- coding: utf-8 -*-
"""崩溃注入子进程：在 commit_history 的指定断点上 os._exit(1)。

由 test_history.py 用不同 DEMO_CRASH_AT 调用：
  python ops/tests/crash_proc.py <task_id> <crash_point>
"""
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("DEMO_ROOT") or r"D:\demo")
CORE = ROOT / "instances/11111111-1111-4111-8111-111111111111/core"
sys.path.insert(0, str(CORE))

from astrbot.core.utils.send_ledger import Ledger  # noqa: E402


def main() -> int:
    task_id = sys.argv[1]
    point = sys.argv[2] if len(sys.argv) > 2 else ""
    if point:
        os.environ["DEMO_CRASH_AT"] = point
    ledger = Ledger()
    result = ledger.commit_history(task_id)
    print(f"no crash, commit returned: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
