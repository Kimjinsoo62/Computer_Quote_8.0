# -*- coding: utf-8 -*-
"""계산기를 견적 창과 별도 OS 창으로 연다. window.open 은 --app 모드에서 크기를 무시한다."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

CALC_W = 280
CALC_H = 540
_proc: subprocess.Popen | None = None


def _browser() -> Path | None:
    pf = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    pf86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    for p in (
        pf86 / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        pf / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        pf / "Google" / "Chrome" / "Application" / "chrome.exe",
    ):
        if p.is_file():
            return p
    return None


def _profile_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    d = base / "AI-PC-Quote-Calc"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _no_window() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def open_calc_window(
    url: str,
    *,
    left: int = 80,
    top: int = 80,
    width: int = CALC_W,
    height: int = CALC_H,
) -> dict:
    """이미 있으면 앞으로 가져오고, 없으면 작은 --app 창을 연다."""
    global _proc
    if _proc is not None and _proc.poll() is None:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(New-Object -ComObject WScript.Shell).AppActivate('계산기') | Out-Null",
            ],
            capture_output=True,
            timeout=8,
            creationflags=_no_window(),
        )
        return {"ok": True, "already": True}

    browser = _browser()
    if browser is None:
        return {"ok": False, "reason": "browser"}

    width = max(268, min(int(width or CALC_W), 420))
    height = max(520, min(int(height or CALC_H), 800))
    left = int(left)
    top = int(top)

    _proc = subprocess.Popen(
        [
            str(browser),
            f"--app={url}",
            f"--user-data-dir={_profile_dir()}",
            "--no-first-run",
            "--no-default-browser-check",
            f"--window-size={width},{height}",
            f"--window-position={left},{top}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_no_window(),
    )
    return {"ok": True, "already": False}


def close_calc_window() -> None:
    global _proc
    if _proc is None:
        return
    if _proc.poll() is None:
        try:
            _proc.terminate()
        except OSError:
            pass
    _proc = None
