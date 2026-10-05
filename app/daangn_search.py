# -*- coding: utf-8 -*-
"""당근(daangn.com) 중고 매물 공개 검색."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

def normalize_search_keyword(text: str) -> str:
    import re
    text = re.sub(r"\[.*?\]|\(.*?\)|\{.*?\}", "", text)
    return text.strip()

logger = logging.getLogger(__name__)

ORIGIN = "https://www.daangn.com"
REGION_API = ORIGIN + "/kr/api/v1/regions/keyword"
SEARCH_PATH = "/kr/buy-sell/"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
NOMINATIM_UA = "ComputerQuote/8.5 (local used-pc compare; https://www.apple.com/kr)"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
FETCH_TIMEOUT_SEC = 15
CACHE_TTL_SEC = 10 * 60
GEO_CACHE_TTL_SEC = 30 * 60
BLOCK_TTL_SEC = 15 * 60
MAX_ITEMS = 20
_NOMINATIM_MIN_INTERVAL = 1.1
_REMIX_CTX_RE = re.compile(r"window\.__remixContext\s*=\s*")

_APPLE_EN_TO_KO = (
    ("MacBook Air", "맥북 에어"),
    ("MacBook Pro", "맥북 프로"),
    ("MacBook Neo", "맥북 네오"),
    ("Mac mini", "맥미니"),
    ("Mac Mini", "맥미니"),
    ("Mac Studio", "맥스튜디오"),
    ("Studio Display", "스튜디오 디스플레이"),
    ("iPad Pro", "아이패드 프로"),
    ("iPad Air", "아이패드 에어"),
    ("iPad mini", "아이패드 미니"),
    ("iPad Mini", "아이패드 미니"),
    ("iPhone Air", "아이폰 에어"),
    ("iPhone Pro Max", "아이폰 프로맥스"),
    ("iPhone Pro", "아이폰 프로"),
    ("iMac", "아이맥"),
    ("iPad", "아이패드"),
    ("iPhone", "아이폰"),
)
_COUPANG_JOIN_TO_SPACE = (
    ("맥북에어", "맥북 에어"),
    ("맥북프로", "맥북 프로"),
    ("맥미니", "맥미니"),
    ("맥스튜디오", "맥스튜디오"),
    ("맥프로", "맥 프로"),
)

_cache_lock = threading.Lock()
_search_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_region_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_geo_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_nominatim_last_ts = 0.0
_block_until = 0.0


def normalize_daangn_keyword(model: str) -> str:
    """견적 모델명을 당근 중고 검색어로 바꾼다."""
    text = (model or "").strip()
    if not text:
        return ""
    base = normalize_search_keyword(text)
    for joined, spaced in _COUPANG_JOIN_TO_SPACE:
        base = re.sub(re.escape(joined), spaced, base, flags=re.I)
    for eng, kor in _APPLE_EN_TO_KO:
        base = re.sub(re.escape(eng), kor, base, flags=re.I)
    if base.startswith("조립PC"):
        base = "컴퓨터" + base[len("조립PC") :]
    return re.sub(r"\s+", " ", base).strip() or text


def build_classic_search_url(keyword: str) -> str:
    return f"{ORIGIN}/search/{urllib.parse.quote(keyword.strip())}"


def build_daangn_search_url(keyword: str, region_slug: str) -> str:
    slug = str(region_slug or "").strip()
    if not slug:
        return build_classic_search_url(keyword)
    query = urllib.parse.urlencode(
        {
            "in": slug,
            "search": keyword,
            "only_on_sale": "true",
        },
        quote_via=urllib.parse.quote,
    )
    return f"{ORIGIN}{SEARCH_PATH}?{query}"


def _public_listing_url(href: str, region_slug: str = "") -> str:
    del region_slug  # 당근 원본 경로만 쓴다. 임의 쿼리는 CloudFront가 막는 경우가 있다.
    raw = str(href or "").strip()
    if raw.startswith("/"):
        raw = ORIGIN + raw
    if not raw.startswith(ORIGIN + "/kr/buy-sell/"):
        return ""
    parts = urllib.parse.urlsplit(raw)
    if "_data=" in (parts.query or ""):
        return ""
    return urllib.parse.urlunsplit(("https", "www.daangn.com", parts.path or "/", "", ""))


def search_daangn_used(model: str, region_query: str | None = None) -> dict[str, Any]:
    keyword = normalize_daangn_keyword(model)
    if not keyword:
        raise ValueError("검색어가 비어 있습니다.")
    if not (region_query or "").strip():
        raise ValueError("검색 동네가 필요합니다.")
    try:
        region = resolve_daangn_region(region_query)
    except ValueError as e:
        if not _daangn_blocked():
            raise
        region = {
            "id": 0,
            "name": str(region_query).strip(),
            "label": str(region_query).strip(),
            "slug": "",
            "warning": str(e),
        }
    cache_key = f"{region['slug']}|{keyword}"
    now = time.time()
    with _cache_lock:
        cached = _search_cache.get(cache_key)
        if cached and now - cached[0] < CACHE_TTL_SEC:
            return dict(cached[1])

    search_url = build_daangn_search_url(keyword, region["slug"])
    items: list[dict[str, Any]] = []
    warning = str(region.get("warning") or "")
    blocked = _daangn_blocked()
    if blocked:
        warning = warning or (
            "당근이 자동 접속을 잠시 차단했습니다. "
            "「당근에서 더 보기」로 브라우저에서 직접 열어 주세요."
        )
        search_url = build_classic_search_url(keyword)
    else:
        try:
            html = _fetch_daangn(search_url, accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.8")
            payload = _payload_from_html(html)
            items = _prefer_similar(_parse_articles(payload, region["slug"]), keyword)
        except urllib.error.HTTPError as e:
            logger.warning("Daangn search HTTP %s: %s", e.code, e)
            if e.code in (403, 429):
                _mark_daangn_blocked()
                blocked = True
                search_url = build_classic_search_url(keyword)
                warning = (
                    "당근이 접속을 차단했습니다(CloudFront 403). "
                    "잠시 후 「당근에서 더 보기」로 직접 검색해 주세요."
                )
            else:
                warning = warning or "당근 매물을 불러오지 못했습니다. 사이트에서 직접 검색해 주세요."
        except Exception as e:
            logger.warning("Daangn search failed: %s", e)
            warning = warning or "당근 매물을 불러오지 못했습니다. 사이트에서 직접 검색해 주세요."

    result = {
        "keyword": keyword,
        "region": region,
        "search_url": search_url,
        "items": items[:MAX_ITEMS],
        "count": min(len(items), MAX_ITEMS),
        "source": "daangn_public",
        "warning": warning,
        "blocked": blocked,
    }
    with _cache_lock:
        _search_cache[cache_key] = (time.time(), result)
    return result


def resolve_daangn_region(
    query: str,
    hint_sido: str = "",
    hint_sigungu: str = "",
) -> dict[str, Any]:
    q = re.sub(r"\s+", " ", (query or "").strip())
    if not q:
        raise ValueError("검색 동네가 비어 있습니다.")
    cache_key = f"{q}|{hint_sido}|{hint_sigungu}"
    now = time.time()
    with _cache_lock:
        cached = _region_cache.get(cache_key)
        if cached and now - cached[0] < CACHE_TTL_SEC * 10:
            return dict(cached[1])
    try:
        data = _fetch_daangn_json(f"{REGION_API}?keyword={urllib.parse.quote(q)}")
        picked = _pick_region(
            (data or {}).get("locations") or [],
            q,
            hint_sido=hint_sido,
            hint_sigungu=hint_sigungu,
        )
    except Exception as e:
        logger.warning("Daangn region lookup failed for %s: %s", q, e)
        picked = None
    if not picked:
        raise ValueError(f"당근에서 '{q}' 동네를 찾지 못했습니다.")
    with _cache_lock:
        _region_cache[cache_key] = (time.time(), picked)
    return dict(picked)


def region_from_coords(lat: float, lng: float) -> dict[str, Any]:
    """브라우저 GPS 좌표를 당근 행정동으로 바꾼다."""
    try:
        lat_f = float(lat)
        lng_f = float(lng)
    except (TypeError, ValueError) as e:
        raise ValueError("위치 좌표가 올바르지 않습니다.") from e
    if not (33.0 <= lat_f <= 43.0 and 124.0 <= lng_f <= 132.0):
        raise ValueError("한국 밖 좌표로는 당근 동네를 정할 수 없습니다.")

    cache_key = f"{lat_f:.4f},{lng_f:.4f}"
    now = time.time()
    with _cache_lock:
        cached = _geo_cache.get(cache_key)
        if cached and now - cached[0] < GEO_CACHE_TTL_SEC:
            return dict(cached[1])

    try:
        addr = _nominatim_reverse(lat_f, lng_f)
    except ValueError:
        raise
    except Exception as e:
        logger.warning("Nominatim reverse failed: %s", e)
        raise ValueError("현재 위치의 주소를 확인하지 못했습니다.") from e
    sido, sigungu = _sido_sigungu(addr)
    candidates = _dong_candidates(addr)
    last_error = "좌표에서 동 이름을 읽지 못했습니다."
    picked = None
    used_query = ""
    for cand in candidates:
        try:
            picked = resolve_daangn_region(cand, hint_sido=sido, hint_sigungu=sigungu)
            used_query = cand
            break
        except ValueError as e:
            last_error = str(e)
    if not picked:
        raise ValueError(last_error)

    result = {
        "ok": True,
        "source": "gps",
        "query": used_query,
        "candidates": candidates,
        "sido": sido,
        "sigungu": sigungu,
        "region": picked,
    }
    with _cache_lock:
        _geo_cache[cache_key] = (time.time(), result)
    return dict(result)


def _pick_region(
    locations: list[dict[str, Any]],
    query: str,
    hint_sido: str = "",
    hint_sigungu: str = "",
) -> dict[str, Any] | None:
    if not locations:
        return None
    pool = list(locations)
    if hint_sido:
        matched = [
            row
            for row in pool
            if _norm_area(hint_sido) in _norm_area(row.get("name1"))
            or _norm_area(row.get("name1")) in _norm_area(hint_sido)
        ]
        if matched:
            pool = matched
    if hint_sigungu:
        matched = [
            row
            for row in pool
            if _norm_area(hint_sigungu) in _norm_area(row.get("name2"))
            or _norm_area(row.get("name2")) in _norm_area(hint_sigungu)
        ]
        if matched:
            pool = matched
    exact = [row for row in pool if str(row.get("name") or "") == query]
    if exact:
        pool = exact
    elif not hint_sido:
        seoul = [row for row in pool if str(row.get("name1") or "") == "서울특별시"]
        if seoul:
            pool = seoul
    row = pool[0]
    name = str(row.get("name") or query)
    rid = int(row.get("id") or 0)
    if rid <= 0:
        return None
    label = " ".join(
        p for p in (row.get("name1"), row.get("name2"), row.get("name3") or name) if p
    )
    return {
        "id": rid,
        "name": name,
        "label": label,
        "slug": f"{name}-{rid}",
    }


def _norm_area(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _sido_sigungu(addr: dict[str, Any]) -> tuple[str, str]:
    city = str(addr.get("city") or "").strip()
    state = str(addr.get("state") or "").strip()
    borough = str(addr.get("borough") or addr.get("county") or "").strip()
    if any(tag in city for tag in ("특별시", "광역시", "특별자치시")):
        return city, borough
    sido = state or city
    sigungu = borough
    if not sigungu and city.endswith(("시", "군", "구")):
        sigungu = city
    return sido, sigungu


def _looks_like_dong(name: str) -> bool:
    return bool(name) and name.endswith(("동", "가", "읍", "면", "리"))


def _dong_variants(name: str) -> list[str]:
    out = [name]
    numbered = re.match(r"^(.+?)(\d+)동$", name)
    if numbered:
        out.append(f"{numbered.group(1)}동")
    je = re.match(r"^(.+?)제(\d+)동$", name)
    if je:
        out.append(f"{je.group(1)}동")
    # 고유한 순서 유지
    seen: set[str] = set()
    uniq: list[str] = []
    for item in out:
        if item not in seen:
            seen.add(item)
            uniq.append(item)
    return uniq


def _dong_candidates(addr: dict[str, Any]) -> list[str]:
    keys = ("suburb", "neighbourhood", "quarter", "village", "hamlet")
    found: list[str] = []
    for key in keys:
        value = str(addr.get(key) or "").strip()
        if _looks_like_dong(value):
            found.extend(_dong_variants(value))
    seen: set[str] = set()
    out: list[str] = []
    for item in found:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _nominatim_reverse(lat: float, lng: float) -> dict[str, Any]:
    global _nominatim_last_ts
    query = urllib.parse.urlencode(
        {
            "format": "jsonv2",
            "lat": f"{lat:.6f}",
            "lon": f"{lng:.6f}",
            "zoom": "18",
            "addressdetails": "1",
            "accept-language": "ko",
        }
    )
    url = f"{NOMINATIM_URL}?{query}"
    with _cache_lock:
        wait = _NOMINATIM_MIN_INTERVAL - (time.time() - _nominatim_last_ts)
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": NOMINATIM_UA,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SEC) as resp:
            raw = resp.read().decode("utf-8", "replace")
    finally:
        with _cache_lock:
            _nominatim_last_ts = time.time()
    data = json.loads(raw)
    addr = data.get("address") if isinstance(data, dict) else None
    if not isinstance(addr, dict) or not addr:
        raise ValueError("현재 위치의 주소를 읽지 못했습니다.")
    return addr


def _daangn_blocked() -> bool:
    with _cache_lock:
        return time.time() < _block_until


def _mark_daangn_blocked() -> None:
    global _block_until
    with _cache_lock:
        _block_until = time.time() + BLOCK_TTL_SEC


def _daangn_headers(accept: str) -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": accept,
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": ORIGIN + "/",
        "Cache-Control": "no-cache",
    }


def _fetch_daangn(url: str, accept: str) -> str:
    if _daangn_blocked():
        raise urllib.error.HTTPError(url, 403, "daangn temporarily blocked", hdrs=None, fp=None)
    req = urllib.request.Request(url, headers=_daangn_headers(accept))
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SEC) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            _mark_daangn_blocked()
        raise


def _fetch_daangn_json(url: str) -> dict[str, Any]:
    raw = _fetch_daangn(url, accept="application/json,text/plain;q=0.9,*/*;q=0.8")
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _extract_remix_context(html: str) -> dict[str, Any]:
    m = _REMIX_CTX_RE.search(html or "")
    if not m:
        return {}
    try:
        data, _end = json.JSONDecoder().raw_decode(html[m.end() :].lstrip())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _payload_from_html(html: str) -> dict[str, Any]:
    ctx = _extract_remix_context(html)
    state = ctx.get("state") if isinstance(ctx.get("state"), dict) else {}
    loader = state.get("loaderData") if isinstance(state.get("loaderData"), dict) else {}
    if isinstance(ctx.get("allPage"), dict):
        return ctx
    for value in loader.values():
        if isinstance(value, dict) and (
            isinstance(value.get("allPage"), dict) or value.get("fleamarketArticles")
        ):
            return value
    return {}


def _parse_articles(payload: dict[str, Any], region_slug: str = "") -> list[dict[str, Any]]:
    page = payload.get("allPage") or {}
    rows = page.get("fleamarketArticles") or []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        href = str(row.get("href") or row.get("id") or "").strip()
        title = str(row.get("title") or "").strip()
        if not href or not title:
            continue
        url = _public_listing_url(href, region_slug)
        if not url:
            continue
        if url in seen:
            continue
        seen.add(url)
        price = _int_price(row.get("price"))
        status = str(row.get("status") or "")
        if status and status.lower() not in ("ongoing", "onsale", ""):
            continue
        region = row.get("region") if isinstance(row.get("region"), dict) else {}
        thumb = str(row.get("thumbnail") or "").strip()
        if thumb and not thumb.startswith("https://"):
            thumb = ""
        out.append(
            {
                "title": title,
                "price": price,
                "price_text": f"{price:,}원" if price > 0 else "가격 없음",
                "region": str(region.get("name") or ""),
                "url": url,
                "thumbnail": thumb,
                "created_at": str(row.get("createdAt") or ""),
                "status": status or "Ongoing",
            }
        )
    return out


def _prefer_similar(items: list[dict[str, Any]], keyword: str) -> list[dict[str, Any]]:
    """검색어 제품군이 제목에 있는 매물을 앞에 둔다. 없으면 원래 목록을 유지한다."""
    needles: list[str] = []
    for token, aliases in (
        ("맥북", ("맥북", "macbook")),
        ("아이맥", ("아이맥", "imac")),
        ("맥미니", ("맥미니", "맥 미니", "mac mini", "macmini")),
        ("맥스튜디오", ("맥스튜디오", "맥 스튜디오", "mac studio")),
        ("아이패드", ("아이패드", "ipad")),
        ("아이폰", ("아이폰", "iphone")),
        ("스튜디오 디스플레이", ("스튜디오 디스플레이", "studio display")),
        ("컴퓨터", ("컴퓨터", "조립", "본체", "게이밍pc", "게이밍 pc")),
    ):
        if token in keyword:
            needles.extend(aliases)
            break
    if not needles:
        return items
    matched = [
        item
        for item in items
        if any(n in str(item.get("title") or "").lower() for n in needles)
    ]
    return matched or items


def _int_price(value: Any) -> int:
    text = str(value or "").replace(",", "").strip()
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return 0
    try:
        return int(digits)
    except ValueError:
        return 0
