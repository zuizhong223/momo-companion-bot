# DemoBot full-stack auto-boot (run at logon)
# ASCII-only on purpose: PowerShell 5.1 reads BOM-less files as ANSI, so any
# non-ASCII literal here breaks when a tool re-saves the file without BOM
# (exactly what happened on 2026-10-25: autostart died silently for a day).
# All paths are derived from $PSScriptRoot, so no Chinese literal is needed.
$ErrorActionPreference = 'Continue'

$Root = $PSScriptRoot
$Core = Join-Path $Root 'instances\11111111-1111-4111-8111-111111111111\core'
$Py = Join-Path $Root 'instances\11111111-1111-4111-8111-111111111111\venv\Scripts\python.exe'
$LogDir = Join-Path $Root 'ops'
$BootLog = Join-Path $LogDir 'boot.log'

function Wait-Port([int]$Port, [int]$TimeoutSec) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        $ok = Test-NetConnection -ComputerName '127.0.0.1' -Port $Port -InformationLevel Quiet -WarningAction SilentlyContinue
        if ($ok) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

Start-Transcript -Path $BootLog -Append -Force | Out-Null
Write-Host "boot: root=$Root core=$Core"

# 1) Ollama
if (-not (Get-Process -Name 'ollama' -ErrorAction SilentlyContinue)) {
    $ollama = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama app.exe'
    if (Test-Path $ollama) {
        Start-Process $ollama -WindowStyle Hidden
        Start-Sleep -Seconds 5
    }
}

# 2) NapCat (ASCII path, safe; override with NAPCAT_DIR)
if (-not (Get-Process -Name 'NapCatWinBootMain' -ErrorAction SilentlyContinue)) {
    $napcatDir = if ($env:NAPCAT_DIR) { $env:NAPCAT_DIR } else { 'D:\napcat\NapCat\NapCat.52230.Shell' }
    if (Test-Path (Join-Path $napcatDir 'napcat.bat')) {
        # Normal QQ can grab the session before NapCat; close it first.
        Get-Process -Name 'QQ' -ErrorAction SilentlyContinue | ForEach-Object {
            try { Stop-Process -Id $_.Id -Force -ErrorAction Stop } catch { }
        }
        Start-Sleep -Seconds 3
        Write-Host 'boot: normal QQ cleared, starting NapCat'
        Start-Process -FilePath 'cmd.exe' -ArgumentList '/c','napcat.bat' -WorkingDirectory $napcatDir
        if (-not (Wait-Port 6199 90)) { Write-Host 'boot: NapCat/OneBot port 6199 not ready' }
        $qr = Get-ChildItem -Path $napcatDir -Recurse -Filter 'qrcode.png' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($qr) { Write-Host ('boot: QR code, if needed: ' + $qr.FullName) }
    } else {
        Write-Host "boot: napcat not found at $napcatDir, skip"
    }
} else {
    Write-Host 'boot: NapCat already running, skip'
}

# 3) AstrBot
$astrbot = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'main\.py' -and $_.CommandLine -match [regex]::Escape($Root) }
if (-not $astrbot) {
    $env:DASHBOARD_PORT = '22877'
    $env:PYTHONIOENCODING = 'utf-8'
$env:DEMO_EVENTS = '1'
$env:DEMO_EVENTS_SHADOW = '1'
# Shadow rollout: life state is computed/logged, but never injected into prompts.
if (-not $env:DEMO_LIFE) { $env:DEMO_LIFE = '1' }
if (-not $env:DEMO_LIFE_SHADOW) { $env:DEMO_LIFE_SHADOW = '1' }
if (-not $env:DEMO_LIFE_REMINDER) { $env:DEMO_LIFE_REMINDER = '0' }
    $env:PATH = (Join-Path $Root 'ffmpeg') + ';' + $env:PATH
    Start-Process -FilePath $Py -ArgumentList '-u','main.py' -WorkingDirectory $Core `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Root 'astrbot_run2.log') `
        -RedirectStandardError (Join-Path $Root 'astrbot_err2.log')
}
if (-not (Wait-Port 22877 90)) { Write-Host 'boot: AstrBot port 22877 not ready' }
if (-not (Wait-Port 6199 60)) { Write-Host 'boot: OneBot port 6199 not ready; QQ may not be connected' }

# 4) Butler
$Butler = Join-Path $Root 'butler.py'
if (Test-Path $Butler) {
    $running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like '*butler.py*' }
    if (-not $running) {
        $quoted = [char]34 + $Butler + [char]34
        $bp = Start-Process -FilePath $Py -ArgumentList '-u', $quoted -WorkingDirectory $Root `
            -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $Root 'butler_stdout.log') `
            -RedirectStandardError (Join-Path $Root 'butler_err.log') -PassThru
        if ($bp) {
            Start-Sleep -Seconds 3
            if (-not (Get-Process -Id $bp.Id -ErrorAction SilentlyContinue)) {
                Write-Host 'boot: butler process exited early'
            }
        }
    }
} else {
    Write-Host 'boot: butler.py not found, skip'
}

$butlerOk = [bool](Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*butler.py*' })
$health = [ordered]@{
    timestamp      = (Get-Date).ToString('o')
    astrbot_port   = (Test-NetConnection -ComputerName '127.0.0.1' -Port 22877 -InformationLevel Quiet -WarningAction SilentlyContinue)
    onebot_port    = (Test-NetConnection -ComputerName '127.0.0.1' -Port 6199 -InformationLevel Quiet -WarningAction SilentlyContinue)
    napcat_process = [bool](Get-Process -Name 'NapCatWinBootMain' -ErrorAction SilentlyContinue)
    qq_process     = [bool](Get-Process -Name 'QQ' -ErrorAction SilentlyContinue)
    butler_process = $butlerOk
}
$health | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $LogDir 'boot_health.json') -Encoding utf8
Write-Host 'boot: health written'

Stop-Transcript | Out-Null
