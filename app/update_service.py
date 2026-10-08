# -*- coding: utf-8 -*-
"""GitHub 업데이트: 원격 확인 → 승인 후 pull → 재시작."""
import os
import subprocess
import threading
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# GitHub 저장소 이름이 바뀌면 예전 이름을 여기에 추가한다.
REPO_NAME = "Computer_Quote_X.15"
OLD_REPO_NAMES = ("Computer_Quote_9.0",)


def _git(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=_NO_WINDOW,
    )


def _upstream() -> str:
    r = _git("rev-parse", "--abbrev-ref", "@{u}")
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else "origin/main"


def _migrate_remote(remote: str) -> None:
    """원격 주소가 예전 저장소 이름이면 새 이름으로 바꾼다(GitHub 리다이렉트에 기대지 않도록)."""
    url = _git("remote", "get-url", remote).stdout.strip()
    for old in OLD_REPO_NAMES:
        for suffix in (".git", ""):
            tail = f"/{old}{suffix}"
            if url.lower().endswith(tail.lower()):
                new_url = url[: -len(tail)] + f"/{REPO_NAME}{suffix}"
                _git("remote", "set-url", remote, new_url)
                return


def check_update() -> dict:
    """원격 저장소를 fetch 해 받을 커밋이 있는지 알려 준다."""
    try:
        upstream = _upstream()
        remote = upstream.split("/", 1)[0]
        _migrate_remote(remote)
        f = _git("fetch", remote, timeout=90)
        if f.returncode != 0:
            return {"ok": False, "message": "원격 저장소 확인 실패:\n" + (f.stderr or f.stdout).strip()}
        log = _git("log", "--pretty=format:%h %ad %s", "--date=short", f"HEAD..{upstream}")
        commits = [line for line in log.stdout.splitlines() if line.strip()]
        dirty = bool(_git("status", "--porcelain", "--untracked-files=no").stdout.strip())
        return {
            "ok": True,
            "upstream": upstream,
            "behind": len(commits),
            "commits": commits[:30],
            "dirty": dirty,
        }
    except FileNotFoundError:
        return {"ok": False, "message": "Git이 설치되어 있지 않습니다."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": "원격 저장소 확인 시간이 초과되었습니다. 네트워크를 확인하세요."}


def apply_update() -> dict:
    """fast-forward pull 로 갱신하고, 의존성 파일이 바뀌었는지 알려 준다."""
    try:
        upstream = _upstream()
        remote, branch = upstream.split("/", 1)
        _migrate_remote(remote)
        before = _git("rev-parse", "HEAD").stdout.strip()
        p = _git("pull", "--ff-only", remote, branch, timeout=180)
        output = (p.stdout + p.stderr).strip()
        if p.returncode != 0:
            return {"ok": False, "message": "업데이트(pull) 실패:\n" + output}
        changed = _git("diff", "--name-only", before, "HEAD").stdout.splitlines()
        return {
            "ok": True,
            "output": output,
            "changed_files": len(changed),
            "deps_changed": "requirements.txt" in changed,
        }
    except FileNotFoundError:
        return {"ok": False, "message": "Git이 설치되어 있지 않습니다."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": "업데이트 시간이 초과되었습니다. 네트워크를 확인하세요."}


def schedule_restart() -> dict:
    """update.bat restart 를 새 창에서 실행(의존성 확인 후 START.bat)하고 현재 서버를 끝낸다."""
    script = BASE_DIR / "update.bat"
    if not script.exists():
        return {"ok": False, "message": "update.bat 파일을 찾을 수 없습니다."}

    def run():
        time.sleep(1)
        subprocess.Popen(
            ["cmd.exe", "/c", "start", "cmd.exe", "/c", str(script), "restart"],
            cwd=BASE_DIR,
        )
        os._exit(0)

    threading.Thread(target=run, daemon=True).start()
    return {"ok": True, "message": "재시작을 시작했습니다."}
