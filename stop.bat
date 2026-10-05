@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo ==========================================
echo   AI PC Quote Server - STOP (8090)
echo ==========================================
echo.

echo [1/4] Sending shutdown request...
powershell -NoProfile -Command ^
  "try { Invoke-RestMethod -Uri 'http://127.0.0.1:8090/api/shutdown' -Method Post -TimeoutSec 5 | Out-Null; Write-Host '   - Shutdown request sent' } catch { Write-Host '   - Server not running or already stopped' }"

echo.
echo [2/4] Waiting for server shutdown...
powershell -NoProfile -Command ^
  "for ($i=0; $i -lt 20; $i++) { if (-not (Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue)) { Write-Host '   - Port 8090 released'; exit }; Start-Sleep -Milliseconds 500 }; Write-Host '   - Timeout: moving to force cleanup'"

echo.
echo [3/4] Checking and closing processes...
powershell -NoProfile -Command ^
  "$n=0; Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^(msedge|chrome|brave)\.exe$' -and $_.CommandLine -match '--app=http://(127\.0\.0\.1|localhost):8090' } | ForEach-Object { Write-Host ('   - Closing browser PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $n++ }; if ($n -eq 0) { Write-Host '   - No app browser windows found' }"

powershell -NoProfile -Command ^
  "$n=0; foreach ($p in 8090,8091) { Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { if ($_ -gt 0) { Write-Host ('   - Port ' + $p + ' : PID ' + $_ + ' terminated'); Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue; $n++ } } }; Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'uvicorn' -and $_.CommandLine -match 'app\.main:app' } | ForEach-Object { Write-Host ('   - uvicorn PID ' + $_.ProcessId + ' terminated'); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $n++ }; if ($n -eq 0) { Write-Host '   - All processes stopped cleanly' }"

echo.
echo [4/4] Cleaning cache (__pycache__, *.pyc)...
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY where python >nul 2>&1 && set "PY=python"
if defined PY (
    "!PY!" -c "from app.shutdown_service import clear_python_cache; n=len(clear_python_cache()); print(f'   - {n} items removed' if n else '   - No cache to remove')"
) else (
    echo   - Python not found, skipping cache cleanup.
)

echo.
echo ==========================================
echo   Done
echo   - Server stopped
echo   - App browser windows closed
echo   - Port 8090 / 8091 released
echo   - Python cache cleaned
echo.
echo   Start again: run.bat
echo ==========================================
echo.
if /I not "%~1"=="/silent" pause
