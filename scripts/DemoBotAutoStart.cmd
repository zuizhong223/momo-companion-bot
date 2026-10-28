@echo off
rem DemoBot auto-boot (logon startup folder)
start "" /min powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "D:/demo\boot_all.ps1"
