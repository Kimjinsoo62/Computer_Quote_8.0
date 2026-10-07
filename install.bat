@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ==========================================
echo   AI PC Quote - 최초 설치 (install.bat)
echo ==========================================
echo.

REM ── 1) Python 확인 ──
echo [1/4] Python 설치 확인...
where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo [오류] Python이 설치되어 있지 않거나 PATH에 없습니다.
    echo.
    echo   1. https://www.python.org/downloads/ 에서 Python 3.10 이상 설치
    echo   2. 설치 시 "Add python.exe to PATH" 체크
    echo   3. 설치 후 명령 프롬프트를 다시 열고 install.bat 재실행
    echo.
    pause
    exit /b 1
)

for /f "delims=" %%v in ('python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2^>nul') do set "PYVER=%%v"
echo   - Python %PYVER% 확인됨

python -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [오류] Python 3.10 이상이 필요합니다. 현재: %PYVER%
    echo.
    pause
    exit /b 1
)

REM ── 2) 가상환경 생성 ──
echo.
echo [2/4] 가상환경 생성 (.venv)...
set "NEED_VENV=1"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys; exit(0)" >nul 2>&1
    if not errorlevel 1 (
        set "NEED_VENV=0"
        echo   - 기존 .venv 사용
    ) else (
        echo   - 기존 .venv가 다른 PC/경로를 가리켜 재생성합니다...
        rmdir /s /q ".venv" 2>nul
    )
)
if "!NEED_VENV!"=="1" (
    python -m venv .venv
    if errorlevel 1 (
        echo.
        echo [오류] 가상환경 생성 실패. python -m venv 모듈을 확인하세요.
        echo.
        pause
        exit /b 1
    )
    echo   - .venv 생성 완료
)

set "PY=%~dp0.venv\Scripts\python.exe"
set "PIP=%~dp0.venv\Scripts\python.exe -m pip"

REM ── 3) pip / 의존성 설치 ──
echo.
echo [3/4] pip 업그레이드 및 라이브러리 설치...
"%PY%" -m pip install --upgrade pip
if errorlevel 1 (
    echo [오류] pip 업그레이드 실패
    pause
    exit /b 1
)

"%PY%" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo.
    echo [오류] requirements.txt 설치 실패
    echo.
    pause
    exit /b 1
)

echo   - requirements.txt 설치 완료

REM ── 4) 설치 검증 ──
echo.
echo [4/4] 설치 검증...
"%PY%" -c "import fastapi, uvicorn, jinja2, openpyxl; print('   - fastapi, uvicorn, jinja2, openpyxl OK')"
if errorlevel 1 (
    echo.
    echo [오류] 패키지 import 검증 실패
    echo.
    pause
    exit /b 1
)

"%PY%" -c "from app.main import app; print('   - app.main 로드 OK')"
if errorlevel 1 (
    echo.
    echo [오류] 앱 모듈 로드 실패 (app 폴더 위치 확인)
    echo.
    pause
    exit /b 1
)

echo.
echo ==========================================
echo   설치 완료
echo.
echo   설치된 패키지 (requirements.txt):
echo     - fastapi, uvicorn, jinja2
echo     - openpyxl, python-multipart
echo.
echo   가상환경: .venv
echo.
echo   다음 단계:
echo     START.bat    서버 시작 (http://127.0.0.1:8090)
echo     stop.bat     서버 중지
echo ==========================================
echo.
REM START.bat calls with /auto: continue without waiting
if /I not "%~1"=="/auto" pause
exit /b 0
