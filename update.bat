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

echo.
echo [1] Downloading latest version (Git Pull)...
git pull origin main
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [WARNING] Git pull failed.
    echo ^(Git might not be installed, or no remote repository is configured.^)
    echo Please update manually if necessary.
    echo.
) else (
    echo [SUCCESS] Download complete.
)

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
start "" "run.bat"
exit
