#!/usr/bin/env bash
# 一键部署：Linux 版（不需要 Docker）
#
# 用法：
#   bash ops/deploy/deploy.sh [目标venv路径]
#
# 做什么：建 venv → 按 requirements.lock 装锁定版本 → import 自检 → 打印下一步
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOCK="$ROOT/requirements.lock"
TARGET="${1:-$ROOT/venv}"

[ -f "$LOCK" ] || { echo "找不到 $LOCK"; exit 1; }

# 1) Python 3.12
PY="${PYTHON:-python3.12}"
command -v "$PY" >/dev/null 2>&1 || PY=python3
VER="$($PY -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
echo "Python: $($PY -V) ($VER)"
[ "$VER" = "3.12" ] || echo "警告：锁是 3.12 生成的，当前 $VER"

# 2) venv
if [ -d "$TARGET" ]; then
  echo "复用 venv: $TARGET"
else
  echo "创建 venv: $TARGET"
  "$PY" -m venv "$TARGET"
fi
VP="$TARGET/bin/python"
[ -x "$VP" ] || { echo "venv 创建失败"; exit 1; }

# 3) 装依赖
echo "安装依赖（首次约 5~10 分钟）…"
"$VP" -m pip install --upgrade pip -q
"$VP" -m pip install -r "$LOCK" --no-input --disable-pip-version-check \
  --extra-index-url https://download.pytorch.org/whl/cpu

# 4) 自检
"$VP" - <<'EOF'
import sys
mods = ["aiocqhttp", "aiohttp", "faiss", "sqlalchemy", "fastapi", "pydantic"]
bad = []
for m in mods:
    try:
        __import__(m)
    except Exception as e:
        bad.append(f"{m}: {e}")
print("BAD=" + repr(bad))
sys.exit(1 if bad else 0)
EOF

cat <<'EOS'

完成。下一步：
  1) 数据放回 instances/<ID>/core/data（或解压备份包）
  2) export DASHBOARD_PORT=22877
  3) 启动：<venv>/bin/python -u main.py（工作目录 instances/<ID>/core）
  4) 健康检查：python ops/tasks.py status

注意：Linux 上 NapCat/QQ 要另外解决（通常用 napcat-docker），这部分不在本脚本里。
EOS
