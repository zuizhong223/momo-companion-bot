# 每月一次全量备份（可直接启动的那种）
# 由计划任务 DemoBotFullBackup 调用；也可以手动跑。
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ROOT = 'D:/demo'
$PY   = Join-Path $ROOT 'instances\11111111-1111-4111-8111-111111111111\venv\Scripts\python.exe'
$LOG  = Join-Path $ROOT 'ops\backup\monthly.log'

function Write-Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -LiteralPath $LOG -Value $line -Encoding UTF8
    Write-Host $line
}

Set-Location -LiteralPath $ROOT
Write-Log '==== 每月全量备份开始 ===='
try {
    $out = & $PY 'ops\backup\backup.py' backup --full 2>&1
    $out | ForEach-Object { Write-Log $_ }
    Write-Log '---- 校验 ----'
    $v = & $PY 'ops\backup\backup.py' verify 2>&1
    $v | ForEach-Object { Write-Log $_ }
    if ($LASTEXITCODE -eq 0) { Write-Log '全量备份完成且校验通过' }
    else { Write-Log "校验失败（exit=$LASTEXITCODE）" }
} catch {
    Write-Log ("异常: " + $_.Exception.Message)
}
Write-Log '==== 结束 ===='
