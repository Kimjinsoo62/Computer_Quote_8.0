# -*- coding: utf-8 -*-
"""견적처/공급자 정보 저장·관리 (quote_info.json)"""
from __future__ import annotations

import json
from pathlib import Path

from app.config import SUPPLIER

DATA_FILE = Path(__file__).resolve().parent.parent / "quote_info.json"

DEFAULT_INFO = {
    "supplier_name": SUPPLIER.get("name", "대한민국주식회사"),
    "supplier_manager": SUPPLIER.get("manager", "홍길동대표"),
    "supplier_address": SUPPLIER.get("address", "서울시"),
    "supplier_phone": SUPPLIER.get("phone", "010-1234-5678"),
    "supplier_email": SUPPLIER.get("email", "email1234@gmail.com"),
    "customer_name": "대한민국주식회사",
    "customer_manager": "홍길동대표",
    "customer_address": "서울시",
    "customer_phone": "010-1234-5678",
    "customer_email": "email1234@gmail.com",
    "quote_date": "",  # 빈 값이면 당일 날짜 사용
    "monitor_price": "180000",
    "keyboard_price": "30000",
    "setup_price": "0",
}


def parse_won(value, default: int = 0) -> int:
    raw = (
        str(value if value is not None else "")
        .replace(",", "")
        .replace("원", "")
        .replace(" ", "")
        .strip()
    )
    if raw == "":
        return int(default)
    try:
        return max(0, int(float(raw)))
    except (TypeError, ValueError):
        return int(default)


def extra_item_prices() -> dict[str, int]:
    info = load_quote_info()
    return {
        "모니터": parse_won(info.get("monitor_price"), 180000),
        "키보드마우스": parse_won(info.get("keyboard_price"), 30000),
        "초기 세팅비": parse_won(info.get("setup_price"), 0),
    }


def load_quote_info() -> dict:
    if DATA_FILE.exists():
        try:
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            return {**DEFAULT_INFO, **{k: v for k, v in data.items() if k in DEFAULT_INFO}}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULT_INFO)


def save_quote_info(data: dict) -> dict:
    info = {k: str(data.get(k, DEFAULT_INFO[k]) or "").strip() for k in DEFAULT_INFO}
    DATA_FILE.write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return info


def reset_quote_info() -> dict:
    if DATA_FILE.exists():
        DATA_FILE.unlink()
    return dict(DEFAULT_INFO)
