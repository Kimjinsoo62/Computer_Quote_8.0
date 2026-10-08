# -*- coding: utf-8 -*-
"""카드 원본 페이지(컴퓨존 상품상세 · Apple 구매 페이지)의 대표 이미지(og:image)를 찾는다."""
from __future__ import annotations

import html as html_lib
import logging
import re
import threading
import time
import urllib.request
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# 서버가 대신 열어 보는 주소이므로 카드 출처 사이트로만 제한한다.
ALLOWED_HOSTS = ("compuzone.co.kr", "apple.com")
CACHE_TTL_SEC = 6 * 60 * 60
FETCH_TIMEOUT_SEC = 15
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
OG_IMAGE_RES = (
    re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', re.I),
)

_cache: dict[str, tuple[float, str]] = {}
_lock = threading.Lock()


def is_allowed_url(url: str) -> bool:
    try:
        p = urlparse(url)
    except ValueError:
        return False
    host = (p.hostname or "").lower()
    return p.scheme in ("http", "https") and any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def find_representative_image(url: str) -> str:
    """원본 페이지의 대표 이미지 URL. 못 찾으면 빈 문자열."""
    if not is_allowed_url(url):
        raise ValueError("컴퓨존·Apple 페이지만 조회할 수 있습니다.")
    now = time.time()
    with _lock:
        hit = _cache.get(url)
        if hit and now - hit[0] < CACHE_TTL_SEC:
            return hit[1]
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SEC) as resp:
            # og:image 는 문서 앞부분 <head> 에 있다
            page = resp.read(400_000).decode("utf-8", errors="replace")
    except Exception as e:
        logger.warning("Image lookup failed for %s: %s", url, e)
        return ""
    image = ""
    for rx in OG_IMAGE_RES:
        m = rx.search(page)
        if m:
            image = html_lib.unescape(m.group(1).strip())
            break
    if image.startswith("//"):
        image = "https:" + image
    if image and not is_allowed_image(image):
        image = ""
    with _lock:
        _cache[url] = (time.time(), image)
    return image


def is_allowed_image(url: str) -> bool:
    """이미지도 출처 사이트의 이미지 서버(image*.compuzone.co.kr, store.storeimages.cdn-apple.com 등)만 허용."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return any(host == h or host.endswith("." + h) for h in ("compuzone.co.kr", "apple.com", "cdn-apple.com"))
