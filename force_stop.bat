@echo off
net session >nul 2>&1
if %errorlevel% NEQ 0 (
  echo Requesting administrator privileges...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

cd /d "%~dp0"
echo [force_stop] Killing listeners on 8090/8091 and uvicorn...
for %%P in (8090 8091) do (
  for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":%%P" ^| findstr "LISTENING"') do (
    if not "%%a"=="0" (
      echo   kill PID %%a ^(port %%P^)
      taskkill /PID %%a /F >nul 2>&1
    )
  )
)
powershell -NoProfile -Command ^
  "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'python' -and $_.CommandLine -match 'uvicorn' -and $_.CommandLine -match 'app\.main:app' } | ForEach-Object { Write-Host ('  kill uvicorn PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo Done. Run START.bat to start fresh.
if /I not "%~1"=="/silent" pause
