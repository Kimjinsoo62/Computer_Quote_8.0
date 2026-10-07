@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

REM Dependencies missing (no .venv or packages) -> run install.bat once
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if defined PY "%PY%" -c "import fastapi, uvicorn, jinja2, openpyxl" >nul 2>&1 || set "PY="
if not defined PY (
    echo [*] Dependencies not installed. Running install.bat...
    call "%~dp0install.bat" /auto
    if errorlevel 1 (
        echo [ERROR] install.bat failed. Fix the error above and run START.bat again.
        pause
        exit /b 1
    )
    set "PY=.venv\Scripts\python.exe"
)

REM LAN address for access from other PCs
set "LANIP=127.0.0.1"
for /f "delims=" %%i in ('call "%PY%" -c "import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.connect(('8.8.8.8',80));print(s.getsockname()[0])" 2^>nul') do set "LANIP=%%i"

echo ==========================================
echo   AI PC Quote Server (Port 8090)
echo   Local:   http://127.0.0.1:8090/
echo   Network: http://!LANIP!:8090/
echo ==========================================

set "BROWSER="
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set "BROWSER=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not defined BROWSER if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" set "BROWSER=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if not defined BROWSER if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "BROWSER=%ProgramFiles%\Google\Chrome\Application\chrome.exe"

REM update.bat restarts with QUOTE_NO_BROWSER=1: the existing app window reloads itself
if defined QUOTE_NO_BROWSER goto :server

if defined BROWSER (
    start "" /B powershell -NoProfile -Command ^
      "$u='http://127.0.0.1:8090/'; for ($i=0; $i -lt 60; $i++) { try { Invoke-WebRequest -Uri $u -TimeoutSec 1 -UseBasicParsing | Out-Null; break } catch { Start-Sleep -Milliseconds 300 } }; $d = Join-Path $env:LOCALAPPDATA 'AI-PC-Quote-Browser'; New-Item -ItemType Directory -Force -Path $d | Out-Null; try { $d = (New-Object -ComObject Scripting.FileSystemObject).GetFolder($d).ShortPath } catch { }; $w=1920; $h=1080; $x=0; $y=0; try { Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop; $wa=[System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea; $w=[Math]::Min(1920,[int]($wa.Width*0.99)); $h=[Math]::Min(1080,[int]($wa.Height*0.99)); $x=$wa.X+[int](($wa.Width-$w)/2); $y=$wa.Y+[int](($wa.Height-$h)/2) } catch { }; Start-Process -FilePath '!BROWSER!' -ArgumentList "--app=$u", "--user-data-dir=$d", '--no-first-run', '--no-default-browser-check', "--window-size=$w,$h", "--window-position=$x,$y""
) else (
    start "" /B powershell -NoProfile -Command ^
      "$u='http://127.0.0.1:8090/'; for ($i=0; $i -lt 60; $i++) { try { Invoke-WebRequest -Uri $u -TimeoutSec 1 -UseBasicParsing | Out-Null; break } catch { Start-Sleep -Milliseconds 300 } }; Start-Process $u"
)

:server
"%PY%" -m uvicorn app.main:app --host 0.0.0.0 --port 8090
