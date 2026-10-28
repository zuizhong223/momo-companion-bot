# 一键部署：Windows 版（不需要 Docker）
#
# 做什么：建 venv → 按 requirements.lock 装**锁定的版本** → 跑 import 自检 → 打印下一步
#
# 用法（在项目根目录）：
#   powershell -ExecutionPolicy Bypass -File ops\deploy\install.ps1
#   powershell -ExecutionPolicy Bypass -File ops\deploy\install.ps1 -Target D:/demo/venv
#
param(
    [string]$Target = "",
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
$Lock = Join-Path $Root "requirements.lock"

if (-not (Test-Path $Lock)) {
    Write-Error "找不到 requirements.lock：$Lock"
}
if (-not $Target) {
    $Target = Join-Path $Root "venv"
}

# 1) 找 Python 3.12
if (-not $Python) {
    $cands = @(
        "D:/demo\components\python\py312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    )
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $cands += $cmd.Source }
    $Python = $cands | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
}
if (-not $Python) {
    Write-Error "找不到 Python 3.12，请用 -Python 指定路径"
}
$ver = & $Python -c "import sys; print('.'.join(map(str, sys.version_info[:2])))"
Write-Host "Python: $Python ($ver)"
if ($ver -ne "3.12") {
    Write-Warning "锁是针对 3.12 生成的，当前是 $ver，个别包可能装不上"
}

# 2) 建 venv
if (Test-Path $Target) {
    Write-Host "venv 已存在，复用：$Target"
} else {
    Write-Host "创建 venv：$Target"
    & $Python -m venv $Target
}
$vp = Join-Path $Target "Scripts\python.exe"
if (-not (Test-Path $vp)) { Write-Error "venv 创建失败：$vp" }

# 3) 装锁定版本
Write-Host "安装依赖（181 个包，首次约 5~10 分钟）…"
& $vp -m pip install --upgrade pip --quiet
# torch 是 +cpu 本地版本，只在 pytorch 的 cpu 索引上有
$ExtraIndex = "https://download.pytorch.org/whl/cpu"
& $vp -m pip install -r $Lock --no-input --disable-pip-version-check --extra-index-url $ExtraIndex
if ($LASTEXITCODE -ne 0) {
    Write-Error "依赖安装失败，退出码 $LASTEXITCODE"
}

# 4) 自检：能不能起关键模块
Write-Host "自检 import…"
$code = @'
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
'@
$tmp = Join-Path $env:TEMP "deploy_check.py"
Set-Content -Path $tmp -Value $code -Encoding UTF8
& $vp $tmp
if ($LASTEXITCODE -ne 0) {
    Write-Error "自检失败"
}

Write-Host ""
Write-Host "完成。下一步：" -ForegroundColor Green
Write-Host "  1) 把数据放回 instances\<ID>\core\data（或解压备份包）"
Write-Host "  2) 设置环境变量 DASHBOARD_PORT=22877"
Write-Host "  3) 启动：$vp -u main.py（工作目录 instances\<ID>\core）"
Write-Host "  4) 健康检查：python ops\tasks.py status"
