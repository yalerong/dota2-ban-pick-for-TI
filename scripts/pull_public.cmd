@echo off
rem Desktop wrapper: PowerShell ExecutionPolicy blocks .ps1 there, .cmd runs anywhere.
rem Usage:  scripts\pull_public.cmd [extra args for pull_public.py]
rem         scripts\pull_public.cmd --status
rem Runs detached with no console window; log in data\pull_public.log, PID in data\pull_public.pid.
setlocal
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
if "%~1"=="--status" (
  python scripts\pull_public.py --status
  exit /b %errorlevel%
)
if not exist data mkdir data
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$p = Start-Process -FilePath 'pythonw' -ArgumentList 'scripts\pull_public.py --patch 7.41 --target 1500000 %*' -RedirectStandardOutput 'data\pull_public.log' -RedirectStandardError 'data\pull_public.err' -PassThru -WindowStyle Hidden; $p.Id | Out-File -Encoding ascii 'data\pull_public.pid'; Write-Host ('started pid ' + $p.Id)"
endlocal
