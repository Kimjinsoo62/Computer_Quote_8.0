@echo off
setlocal EnableDelayedExpansion

echo ==========================================
echo   AI PC Quote Server - Updating...
echo ==========================================
echo.
echo Waiting for existing server process to shut down (3s)...
timeout /t 3 /nobreak >nul

REM Kill any remaining process on port 8090
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8090 ^| findstr LISTENING') do (
    echo Killing existing process PID: %%a
    taskkill /F /PID %%a >nul 2>&1
)

REM "restart" arg: app already pulled - reinstall deps only
if /i "%~1"=="restart" goto deps

echo.
echo [1] Downloading latest version (Git Pull)...
call :fixremote & git pull origin main
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [WARNING] Git pull failed.
    echo ^(Git might not be installed, or no remote repository is configured.^)
    echo Please update manually if necessary.
    echo.
) else (
    echo [SUCCESS] Download complete.
)

:deps
echo.
echo [2] Checking dependencies...
if exist requirements.txt (
    if exist ".venv\Scripts\python.exe" (
        ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    ) else (
        python -m pip install -r requirements.txt
    )
)

echo.
echo ==========================================
echo   Update complete. Restarting server...
echo ==========================================
timeout /t 3 /nobreak >nul

REM Existing app window reloads itself, so don't open another one
set "QUOTE_NO_BROWSER=1"
start "" "START.bat"
exit

REM Repository was renamed on GitHub: point origin at the new name
:fixremote
set "url="
for /f "delims=" %%u in ('git remote get-url origin 2^>nul') do set "url=%%u"
if not defined url goto :eof
set "newurl=!url:/Computer_Quote_9.0=/Computer_Quote_X.15!"
if not "!newurl!"=="!url!" (
    echo Repository renamed - switching remote to !newurl!
    git remote set-url origin "!newurl!"
)
goto :eof
