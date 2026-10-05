# -*- coding: utf-8 -*-
"""앱 종료: 캐시 정리 · 포트 해제 · uvicorn/브라우저 프로세스 종료"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
PORTS = (8090, 8091)


def _run_ps(script: str) -> str:
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return (completed.stdout or "") + (completed.stderr or "")
    except (OSError, subprocess.TimeoutExpired):
        return ""


def protected_pids() -> set[int]:
    """자신과 부모(uvicorn reloader / run.bat cmd)는 강제 종료 대상에서 제외.

    콘솔에 attach 된 프로세스를 TerminateProcess 로 죽이면 conhost.exe 가
    콘솔 정리 중 크래시(스택 버퍼 오버런 대화상자)한다. 자신은 os._exit 로만 끝낸다.
    """
    pids = {os.getpid()}
    out = _run_ps(
        f'(Get-CimInstance Win32_Process -Filter "ProcessId={os.getpid()}"'
        ").ParentProcessId"
    )
    for token in out.split():
        if token.isdigit():
            pids.add(int(token))
            break
    return pids


def clear_python_cache(base: Path = BASE_DIR) -> list[str]:
    removed: list[str] = []
    skip = {".venv", "venv", "node_modules", ".git", ".pytest_cache", ".mypy_cache"}
    # os.walk 로 가지치기한다. rglob 은 .venv 내부 수만 개까지 전부 훑은 뒤 걸러내서 느리다.
    for root, dirnames, filenames in os.walk(base, topdown=True):
        dirnames[:] = [d for d in dirnames if d not in skip]
        if "__pycache__" in dirnames:
            dirnames.remove("__pycache__")
            target = Path(root) / "__pycache__"
            shutil.rmtree(target, ignore_errors=True)
            removed.append(str(target))
        for name in filenames:
            if not name.endswith(".pyc"):
                continue
            try:
                (Path(root) / name).unlink(missing_ok=True)
                removed.append(str(Path(root) / name))
            except OSError:
                continue
    for name in (".pytest_cache", ".mypy_cache"):
        cache_dir = base / name
        if cache_dir.exists():
            shutil.rmtree(cache_dir, ignore_errors=True)
            removed.append(str(cache_dir))
    return removed


def free_ports(
    ports: tuple[int, ...] = PORTS,
    protected: set[int] | None = None,
) -> list[int]:
    killed: list[int] = []
    keep = set(protected) if protected else {os.getpid()}
    for port in ports:
        try:
            completed = subprocess.run(
                f'netstat -ano | findstr ":{port}" | findstr LISTENING',
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        for line in completed.stdout.splitlines():
            parts = line.split()
            if not parts:
                continue
            pid_s = parts[-1]
            if not pid_s.isdigit():
                continue
            pid = int(pid_s)
            if pid <= 0 or pid in keep or pid in killed:
                continue
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            killed.append(pid)

    # PowerShell 보조
    ports_csv = ",".join(str(p) for p in ports)
    keep_csv = ",".join(str(p) for p in sorted(keep))
    _run_ps(
        f"$keep = @({keep_csv}); "
        f"$ports = @({ports_csv}); "
        "foreach ($p in $ports) { "
        "  Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue "
        "  | Select-Object -ExpandProperty OwningProcess -Unique "
        "  | ForEach-Object { if ($_ -gt 0 -and $keep -notcontains $_) "
        "      { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue } } "
        "}"
    )
    return killed


def kill_uvicorn_processes(protected: set[int] | None = None) -> int:
    keep = set(protected) if protected else {os.getpid()}
    keep_csv = ",".join(str(p) for p in sorted(keep))
    out = _run_ps(
        f"$keep = @({keep_csv}); $n=0; "
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" -ErrorAction SilentlyContinue "
        "| Where-Object { $_.CommandLine -match 'uvicorn' -and $_.CommandLine -match 'app\\.main:app' "
        "  -and $keep -notcontains $_.ProcessId } "
        "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $n++ }; "
        "Write-Output $n"
    )
    try:
        return int((out.strip().splitlines() or ["0"])[-1])
    except ValueError:
        return 0


def close_app_browsers() -> int:
    """run.bat 이 연 --app= 전용 창만 종료 (일반 브라우저 전체는 건드리지 않음)"""
    out = _run_ps(
        "$n=0; "
        "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue "
        "| Where-Object { "
        "  $_.Name -match '^(msedge|chrome|brave)\\.exe$' -and "
        "  ($_.CommandLine -match '--app=http://(127\\.0\\.0\\.1|localhost):(8090|8091)' "
        "   -or $_.CommandLine -match 'AI-PC-Quote-Calc') "
        "} "
        "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $n++ }; "
        "Write-Output $n"
    )
    try:
        return int((out.strip().splitlines() or ["0"])[-1])
    except ValueError:
        return 0


def perform_shutdown_cleanup() -> dict[str, Any]:
    keep = protected_pids()
    cache_removed = clear_python_cache()
    browsers = close_app_browsers()
    uvicorn_killed = kill_uvicorn_processes(keep)
    ports_killed = free_ports(PORTS, keep)
    return {
        "cache_removed": len(cache_removed),
        "browsers_closed": browsers,
        "uvicorn_killed": uvicorn_killed,
        "ports_killed": ports_killed,
        "ok": True,
    }


def schedule_full_shutdown(delay_sec: float = 0.6) -> None:
    """응답 전송 후 정리·프로세스 종료를 백그라운드에서 수행"""

    def _worker() -> None:
        time.sleep(delay_sec)
        try:
            perform_shutdown_cleanup()
        finally:
            # 자신은 taskkill 이 아니라 os._exit 로 끝낸다.
            # 콘솔에 attach 된 프로세스를 강제 종료하면 conhost.exe 가 크래시한다.
            os._exit(0)

    threading.Thread(target=_worker, daemon=True).start()
