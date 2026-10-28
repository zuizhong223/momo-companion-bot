# DemoBot watchdog: every 5 min, check ports 22877/6199; restart after 2 consecutive failures.
# Install: powershell -ExecutionPolicy Bypass -File install_watchdog.ps1
# Remove:  schtasks /Delete /TN "DemoBotWatchdog" /F
$ErrorActionPreference = 'Stop'
$Root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Py = Join-Path $Root 'instances\11111111-1111-4111-8111-111111111111\venv\Scripts\pythonw.exe'
$Script = Join-Path $Root 'ops\deploy\watchdog.py'

if (-not (Test-Path $Py)) { throw "pythonw not found: $Py" }
if (-not (Test-Path $Script)) { throw "watchdog not found: $Script" }

$Action = New-ScheduledTaskAction -Execute $Py -Argument ('"' + $Script + '" --once') -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).Date.AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes 5)
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "DemoBotWatchdog" -Action $Action -Trigger $Trigger -Settings $Settings -Description "DemoBot AstrBot/QQ watchdog" -Force | Out-Null
Write-Host "installed. check: schtasks /Query /TN DemoBotWatchdog /V /FO LIST"
