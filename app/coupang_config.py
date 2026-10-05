# -*- coding: utf-8 -*-
"""쿠팡 파트너스 API 키 로컬 저장 (coupang_config.json)"""

from __future__ import annotations

import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_FILE = BASE_DIR / "coupang_config.json"

DEFAULT_CONFIG = {
    "access_key": "",
    "secret_key": "",
    "sub_id": "pc-quote",
}


def _load_env_file() -> None:
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path)
    except ImportError:
        pass


def _from_env() -> dict:
    _load_env_file()
    return {
        "access_key": os.getenv("COUPANG_ACCESS_KEY", "").strip(),
        "secret_key": os.getenv("COUPANG_SECRET_KEY", "").strip(),
        "sub_id": (os.getenv("COUPANG_SUB_ID", "pc-quote").strip() or "pc-quote"),
    }


def load_coupang_config() -> dict:
    if DATA_FILE.exists():
        try:
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            cfg = {
                "access_key": str(data.get("access_key", "")).strip(),
                "secret_key": str(data.get("secret_key", "")).strip(),
                "sub_id": (str(data.get("sub_id", "pc-quote")).strip() or "pc-quote"),
            }
            if cfg["access_key"] and cfg["secret_key"]:
                return cfg
        except (json.JSONDecodeError, OSError):
            pass
    return _from_env()


def save_coupang_config(data: dict) -> dict:
    cfg = {
        "access_key": str(data.get("access_key", "")).strip(),
        "secret_key": str(data.get("secret_key", "")).strip(),
        "sub_id": (str(data.get("sub_id", "pc-quote")).strip() or "pc-quote"),
    }
    DATA_FILE.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return cfg


def reset_coupang_config() -> dict:
    if DATA_FILE.exists():
        DATA_FILE.unlink()
    return dict(DEFAULT_CONFIG)
