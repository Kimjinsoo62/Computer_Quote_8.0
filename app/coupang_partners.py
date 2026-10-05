# -*- coding: utf-8 -*-
"""쿠팡 파트너스 Open API — 검색 URL 딥링크 변환"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache

from app.coupang_config import load_coupang_config

API_DOMAIN = "https://api-gateway.coupang.com"
DEEPLINK_PATH = (
    "/v2/providers/affiliate_open_api/apis/openapi/v1/deeplink"
)
# q= 만 붙인 축약형은 쿠팡이 비정상 접근으로 판단해 "사용권한이 없습니다" 페이지를 띄운다.
# 검색 페이지의 표준 파라미터(component, channel)를 함께 넘긴다.
COUPANG_SEARCH_URL = (
    "https://www.coupang.com/np/search?component=&q={query}&channel=user"
)


def reload_partner_cache() -> None:
    _cached_partner_link.cache_clear()


def is_configured() -> bool:
    cfg = load_coupang_config()
    return bool(cfg["access_key"] and cfg["secret_key"])


# ── 검색 키워드 정제 ──────────────────────────────────────────────
# 카드의 model 문자열은 견적서용이라 쿠팡 검색에는 그대로 쓸 수 없다.
#   애플   : 스펙 나열(코어 수·색상)이 검색을 방해 → 모델명 + 저장용량만 남긴다
#   조립PC : "아이웍스X-0225" 같은 컴퓨존 전용 모델명은 쿠팡에 없음 → 부품으로 검색한다

_APPLE_HINTS = (
    "맥북", "아이맥", "맥 미니", "맥미니", "맥 스튜디오", "맥스튜디오",
    "맥 프로", "맥프로", "아이패드", "아이폰", "에어팟", "애플",
)
# 쿠팡에서는 붙여 쓰는 표기가 검색 결과가 많다
_APPLE_SPACING = (
    ("맥북 에어", "맥북에어"),
    ("맥북 프로", "맥북프로"),
    ("맥 미니", "맥미니"),
    ("맥 스튜디오", "맥스튜디오"),
    ("맥 프로", "맥프로"),
)
_PAREN_RE = re.compile(r"\(([^)]*)\)")
_CAPACITY_RE = re.compile(r"(\d+)\s*(TB|GB)\b", re.IGNORECASE)
_MARKETING_RE = re.compile(r"[▶◀►◄][^▶◀►◄]*[▶◀►◄]?")
_HANGUL_RE = re.compile(r"[가-힣]")


def _is_apple(text: str) -> bool:
    return text.lstrip().lower().startswith("apple") or any(h in text for h in _APPLE_HINTS)


def _normalize_apple(text: str) -> str:
    spec_match = _PAREN_RE.search(text)
    spec = spec_match.group(1) if spec_match else ""

    base = _PAREN_RE.sub(" ", text)
    base = re.sub(r"^\s*apple\s+", "", base, flags=re.IGNORECASE)
    for spaced, joined in _APPLE_SPACING:
        base = base.replace(spaced, joined)

    # RAM 이 앞, 저장용량이 뒤에 오므로 마지막 용량 토큰을 저장용량으로 본다.
    caps = _CAPACITY_RE.findall(spec)
    storage = f"{caps[-1][0]}{caps[-1][1].upper()}" if caps else ""
    return f"{base} {storage}"


def _normalize_assembled(text: str) -> str:
    cleaned = _MARKETING_RE.sub(" ", text)
    spec_match = _PAREN_RE.search(cleaned)
    if not spec_match:
        return cleaned
    # 괄호 안은 "CPU/GPU" 조합. 내장그래픽처럼 부품명이 아닌 토큰은 버린다.
    parts = [p.strip() for p in spec_match.group(1).split("/")]
    keep = [p for p in parts if p and not _HANGUL_RE.search(p)]
    return " ".join(["조립PC", *keep]) if keep else "조립PC"


def normalize_search_keyword(model: str) -> str:
    """견적 카드의 model 문자열을 쿠팡 검색에 적합한 키워드로 정제."""
    text = (model or "").strip()
    if not text:
        return ""
    result = _normalize_apple(text) if _is_apple(text) else _normalize_assembled(text)
    return re.sub(r"\s+", " ", result).strip() or text


def build_search_url(keyword: str) -> str:
    # quote() 기본값은 safe="/" 라 "16GB/512GB" 같은 모델명의 슬래시가 인코딩되지 않는다.
    # 쿼리 값이므로 슬래시까지 모두 인코딩해야 한다(JS 쪽 encodeURIComponent 와 동작을 맞춘다).
    query = urllib.parse.quote(keyword.strip(), safe="")
    return COUPANG_SEARCH_URL.format(query=query)


def _generate_hmac(method: str, path: str, access_key: str, secret_key: str) -> str:
    from time import gmtime, strftime

    datetime_gmt = (
        strftime("%y%m%d", gmtime())
        + "T"
        + strftime("%H%M%S", gmtime())
        + "Z"
    )
    path_only, _, query = path.partition("?")
    message = datetime_gmt + method + path_only + query
    signature = hmac.new(
        secret_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return (
        "CEA algorithm=HmacSHA256, "
        f"access-key={access_key}, signed-date={datetime_gmt}, signature={signature}"
    )


def _request_deeplink(
    search_url: str,
    sub_id: str,
    access_key: str,
    secret_key: str,
) -> dict:
    body: dict[str, object] = {"coupangUrls": [search_url]}
    if sub_id:
        body["subId"] = sub_id

    payload = json.dumps(body).encode("utf-8")
    authorization = _generate_hmac("POST", DEEPLINK_PATH, access_key, secret_key)
    req = urllib.request.Request(
        f"{API_DOMAIN}{DEEPLINK_PATH}",
        data=payload,
        method="POST",
        headers={
            "Authorization": authorization,
            "Content-Type": "application/json;charset=UTF-8",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


@lru_cache(maxsize=256)
def _cached_partner_link(keyword: str, sub_id: str, access_key: str) -> str | None:
    cfg = load_coupang_config()
    secret_key = cfg["secret_key"]
    search_url = build_search_url(keyword)
    try:
        result = _request_deeplink(search_url, sub_id, access_key, secret_key)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        return None

    if result.get("rCode") != "0":
        return None

    data = result.get("data") or []
    if not data:
        return None

    link = data[0].get("shortenUrl") or data[0].get("landingUrl")
    return str(link) if link else None


def test_partner_connection(
    access_key: str | None = None,
    secret_key: str | None = None,
    sub_id: str | None = None,
) -> dict:
    cfg = load_coupang_config()
    ak = (access_key or cfg["access_key"]).strip()
    sk = (secret_key or cfg["secret_key"]).strip()
    sid = ((sub_id or cfg["sub_id"]).strip() or "pc-quote")

    if not ak or not sk:
        return {"ok": False, "message": "Access Key와 Secret Key를 입력해 주세요."}

    search_url = build_search_url("노트북")
    try:
        result = _request_deeplink(search_url, sid, ak, sk)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:200]
        return {"ok": False, "message": f"API 오류 (HTTP {e.code}): {detail}"}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {"ok": False, "message": f"연결 실패: {e}"}

    if result.get("rCode") != "0":
        return {
            "ok": False,
            "message": result.get("rMessage") or "API 응답 오류",
        }

    data = result.get("data") or []
    sample = (data[0].get("shortenUrl") if data else None) or ""
    return {
        "ok": True,
        "message": "쿠팡 파트너스 API 연결 성공!",
        "sample_url": sample,
    }


def create_search_partner_link(keyword: str, sub_id: str | None = None) -> dict:
    """검색어를 파트너스 딥링크로 변환. 실패 시 일반 검색 URL 반환."""
    raw = keyword.strip()
    if not raw:
        raise ValueError("검색어가 비어 있습니다.")

    # 견적서용 모델명을 그대로 넘기면 검색 결과가 없다. 정제한 키워드로 검색한다.
    search_keyword = normalize_search_keyword(raw)
    fallback_url = build_search_url(search_keyword)
    cfg = load_coupang_config()
    if not cfg["access_key"] or not cfg["secret_key"]:
        return {
            "url": fallback_url,
            "keyword": search_keyword,
            "partner": False,
            "message": "쿠팡 파트너스 API가 설정되지 않았습니다. 상단 ⚡ 왼쪽 영역을 클릭해 설정하세요.",
        }

    sid = (sub_id or cfg["sub_id"]).strip()
    partner_url = _cached_partner_link(search_keyword, sid, cfg["access_key"])
    if partner_url:
        return {"url": partner_url, "keyword": search_keyword, "partner": True}

    return {
        "url": fallback_url,
        "keyword": search_keyword,
        "partner": False,
        "message": "파트너스 API 변환에 실패해 일반 검색 URL을 사용합니다.",
    }
