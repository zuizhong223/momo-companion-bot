# 改完核心文件后跑这一条：刷新补丁清单 -> 门禁 -> 提交
# 用法：powershell -NoProfile -File ops\dev\after_edit.ps1 -Message "fix(R02): 多段整轮清单"
param(
    [Parameter(Mandatory = $true)][string]$Message,
    [switch]$SkipGate
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ROOT   = 'D:/demo'
$CORE   = Join-Path $ROOT 'instances\11111111-1111-4111-8111-111111111111\core'
$MAN    = Join-Path $ROOT 'patches\MANIFEST.txt'
$STORE  = Join-Path $ROOT 'patches\files'
$PY     = Join-Path $ROOT 'instances\11111111-1111-4111-8111-111111111111\venv\Scripts\python.exe'
$GIT    = 'C:\Program Files\Git\cmd\git.exe'

Set-Location -LiteralPath $ROOT

# 0) 提醒：有没有别人的未提交改动
$dirty = (& $GIT status --porcelain | Measure-Object).Count
Write-Host "工作区未提交条目: $dirty（其中可能有别人的改动，提交前确认）"

# 1) 刷新清单
$raw = [System.IO.File]::ReadAllText($MAN, [System.Text.Encoding]::UTF8).TrimStart([char]0xFEFF)
$comments = @(); $map = [ordered]@{}
foreach ($line in ($raw -split "`r?`n")) {
    $t = $line.Trim()
    if (-not $t) { continue }
    if ($t.StartsWith('#')) { $comments += $t; continue }
    $p = $t -split '\s+'
    if ($p.Count -eq 2) { $map[$p[0]] = $p[1] }
}
$changed = 0
foreach ($rel in @($map.Keys)) {
    $src = Join-Path $CORE $rel
    if (-not (Test-Path -LiteralPath $src)) { continue }
    $h = (Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash.ToLower()
    if ($h -ne $map[$rel]) {
        $map[$rel] = $h; $changed++
        $dst = Join-Path $STORE $rel
        New-Item -ItemType Directory -Path (Split-Path $dst) -Force | Out-Null
        Copy-Item -LiteralPath $src -Destination $dst -Force
    }
}
$out = New-Object System.Collections.Generic.List[string]
$comments | ForEach-Object { $out.Add($_) }
foreach ($k in $map.Keys) { $out.Add("$k $($map[$k])") }
[System.IO.File]::WriteAllText($MAN, ($out -join "`r`n") + "`r`n", (New-Object System.Text.UTF8Encoding($false)))
Write-Host "补丁清单刷新: $changed 个文件"

# 2) 门禁
if (-not $SkipGate) {
    & $PY 'ops\verify\release_gate.py' | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Host "门禁未通过，已停止提交" -ForegroundColor Red
        exit 1
    }
}

# 3) 提交（只提交补丁包与测试，不碰运行时数据）
& $GIT add patches/MANIFEST.txt patches/files ops/tests ops/verify ops/CONVENTIONS.md ops/dev | Out-Null
& $GIT -c user.name='demo-dev' -c user.email='demo-dev@localhost' commit -m $Message | Out-Null
Write-Host "已提交: $Message"
& $GIT log --oneline -1
