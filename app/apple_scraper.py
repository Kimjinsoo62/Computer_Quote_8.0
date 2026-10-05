# -*- coding: utf-8 -*-
"""
Apple Store Korea live catalog sync.

카드 목록의 단일 출처는 공식 구매 페이지의 PRODUCT_SELECTION_BOOTSTRAP 이다.
하드코딩 모델 리스트는 사용하지 않는다.
라이브 수집 실패 시에는 마지막 성공 스냅샷만 쓰고, 구세대 하드코딩으로 되돌리지 않는다.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any

from app.config import APPLE_CATALOG_SOURCES

logger = logging.getLogger(__name__)

APPLE_ORIGIN = "https://www.apple.com"
HTML_TTL_SEC = 15 * 60
CATALOG_TTL_SEC = 30 * 60
FETCH_TIMEOUT_SEC = 20
SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "apple_catalog_cache.json"
CATALOG_SCHEMA = 2

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

FAMILY_HREF_RE = re.compile(
    r"(?:https://www\.apple\.com)?(?:/kr)?(?:/shop)?/buy-(mac|ipad|iphone)/([a-z0-9-]+)",
    re.I,
)
SKIP_SLUG_NEEDLES = ("offer", "overlay", "compare", "trade", "carrier")
COLOR_DIMS = {
    "chassis-dimensionColor",
    "dimensionColor",
}
IGNORE_DIMS = COLOR_DIMS | {
    "display-dimensionFinish",
    "dimensionFinish",
    "dimensionStandType",
}
WIFI_CONNECTIONS = {"", "wifi", "wi-fi", "none", "null"}
HTML_TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")

FAMILY_LABELS = {
    "macbook-air": "MacBook Air",
    "macbook-pro": "MacBook Pro",
    "macbook-neo": "MacBook Neo",
    "imac": "iMac",
    "mac-mini": "Mac mini",
    "mac-studio": "Mac Studio",
    "studio-display": "Studio Display",
    "studio-display-xdr": "Studio Display XDR",
    "ipad": "iPad",
    "ipad-air": "iPad Air",
    "ipad-pro": "iPad Pro",
    "ipad-mini": "iPad mini",
    "ipad-10-2": "iPad",
}

_html_cache: dict[str, tuple[float, str]] = {}
_catalog_cache: dict[str, dict[str, Any]] = {}
_cache_lock = threading.Lock()


def scrape_live_apple_html(url: str) -> str:
    now = time.time()
    with _cache_lock:
        hit = _html_cache.get(url)
        if hit and now - hit[0] < HTML_TTL_SEC:
            return hit[1]
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
        "Accept": "text/html,application/xhtml+xml",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SEC) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        logger.warning("Apple HTML fetch failed for %s: %s", url, e)
        return ""
    with _cache_lock:
        _html_cache[url] = (time.time(), html)
    return html


def fetch_live_apple_price(item: dict) -> tuple[int, str]:
    """호환용. 카탈로그 동기화 이후에는 카드 가격을 이 함수로 맞추지 않는다."""
    price = int(item.get("price") or item.get("fallback_total") or item.get("fallback_price") or 0)
    return price, "apple_official"


def clear_apple_catalog_cache(cat_key: str | None = None) -> None:
    """공홈 개정 전 이전 데이터 및 캐시를 완전히 비운다."""
    with _cache_lock:
        if cat_key:
            _catalog_cache.pop(cat_key, None)
        else:
            _catalog_cache.clear()
        _html_cache.clear()


def fetch_apple_catalog(
    cat_key: str, force_refresh: bool = False
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """현재 판매 중인 대표 구성 목록과 동기화 메타를 반환한다.
    
    신규 크롤링 시 이전 리스트를 병합/누적하지 않고, 공홈의 최신 판매 리스트만으로 100% 완전 교체한다.
    """
    now = time.time()
    if force_refresh:
        clear_apple_catalog_cache(cat_key)
    else:
        with _cache_lock:
            cached = _catalog_cache.get(cat_key)
            if (
                cached
                and int(cached.get("schema") or 0) == CATALOG_SCHEMA
                and now - float(cached.get("fetched_ts") or 0) < CATALOG_TTL_SEC
            ):
                items = list(cached.get("items") or [])
                if items:
                    return items, dict(cached.get("meta") or {})

    # 실시간 공홈 크롤링 수행: 이전 리스트를 완전히 비우고 신규 수집된 리스트로 교체
    live_items, meta = _sync_catalog(cat_key)
    if live_items:
        payload = {
            "schema": CATALOG_SCHEMA,
            "fetched_ts": time.time(),
            "items": live_items,
            "meta": meta,
        }
        with _cache_lock:
            _catalog_cache[cat_key] = payload
            if cat_key == "all":
                cat_buckets: dict[str, list[dict[str, Any]]] = {}
                for it in live_items:
                    ck = it.get("cat_key")
                    if ck:
                        cat_buckets.setdefault(ck, []).append(it)
                for ck, sub_items in cat_buckets.items():
                    _catalog_cache[ck] = {
                        "schema": CATALOG_SCHEMA,
                        "fetched_ts": time.time(),
                        "items": sub_items,
                        "meta": meta,
                    }
        _save_snapshot(cat_key, payload)
        return live_items, meta

    snap = _load_snapshot(cat_key)
    if snap and snap.get("items"):
        logger.warning("Apple live catalog empty for %s; using last snapshot", cat_key)
        with _cache_lock:
            _catalog_cache[cat_key] = snap
        meta = dict(snap.get("meta") or {})
        meta["from_snapshot"] = True
        return list(snap["items"]), meta

    return [], {"error": "catalog_unavailable", "from_snapshot": False}



def _sync_catalog(cat_key: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    families = _discover_family_urls(cat_key)
    if not families:
        return [], {"family_count": 0, "synced_at": datetime.now().isoformat(timespec="seconds")}

    items: list[dict[str, Any]] = []
    errors = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {
            pool.submit(_parse_family_page, fam["url"], fam): fam
            for fam in families
        }
        for fut in as_completed(futs):
            fam = futs[fut]
            try:
                parsed = fut.result()
            except Exception as e:
                logger.warning("Apple family parse failed for %s: %s", fam["url"], e)
                errors += 1
                continue
            items.extend(parsed)

    items.sort(
        key=lambda it: (
            int(it.get("family_order") or 0),
            _size_sort(it.get("screensize") or ""),
            int(it.get("price") or 0),
            it.get("tier_key") or "",
        )
    )
    meta = {
        "synced_at": datetime.now().isoformat(timespec="seconds"),
        "family_count": len(families),
        "item_count": len(items),
        "errors": errors,
        "from_snapshot": False,
        "source": "apple_official_catalog",
    }
    return items, meta


def _discover_family_urls(cat_key: str) -> list[dict[str, Any]]:
    specs = _source_specs(cat_key)
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    order = 0
    for spec in specs:
        for hub in spec.get("hubs") or []:
            html = scrape_live_apple_html(hub)
            if not html:
                continue
            kinds = spec.get("kinds") or ("mac", "ipad", "iphone")
            prefixes = spec.get("include_prefixes") or ()
            exclude = set(spec.get("exclude_slugs") or ())
            for kind, slug in FAMILY_HREF_RE.findall(html):
                kind = kind.lower()
                slug = slug.lower()
                if kind not in kinds:
                    continue
                if slug in exclude or any(n in slug for n in SKIP_SLUG_NEEDLES):
                    continue
                if prefixes and not any(
                    slug == p or slug.startswith(p) or slug.startswith(p.rstrip("-") + "-")
                    for p in prefixes
                ):
                    continue
                key = (kind, slug)
                if key in seen:
                    continue
                seen.add(key)
                found.append(
                    {
                        "kind": kind,
                        "slug": slug,
                        "url": f"{APPLE_ORIGIN}/kr/shop/buy-{kind}/{slug}",
                        "cat_key": spec["cat_key"],
                        "family_order": order,
                    }
                )
                order += 1
    return found


def _source_specs(cat_key: str) -> list[dict[str, Any]]:
    if cat_key == "all":
        specs = []
        for key, spec in APPLE_CATALOG_SOURCES.items():
            row = dict(spec)
            row["cat_key"] = key
            specs.append(row)
        return specs
    spec = APPLE_CATALOG_SOURCES.get(cat_key)
    if not spec:
        return []
    row = dict(spec)
    row["cat_key"] = cat_key
    return [row]


def _parse_family_page(url: str, fam: dict[str, Any]) -> list[dict[str, Any]]:
    html = scrape_live_apple_html(url)
    if not html:
        return []
    data = _extract_product_selection(html)
    if not data:
        return []
    if _is_mac_configurator(data):
        candidates = _parse_mac_configurator(data, fam)
    else:
        candidates = _parse_metrics_products(data, fam)
    return _pick_representatives(candidates, fam)


def _extract_product_selection(html: str) -> dict[str, Any] | None:
    m = re.search(r"productSelectionData:\s*(\{)", html)
    if not m:
        return None
    decoder = json.JSONDecoder()
    try:
        data, _end = decoder.raw_decode(html[m.start(1) :])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _is_mac_configurator(data: dict[str, Any]) -> bool:
    products = data.get("products") or []
    if not products or not isinstance(products[0], dict):
        return False
    if products[0].get("priceKey"):
        return True
    return bool(data.get("mainDisplayValues", {}).get("prices"))


def _parse_mac_configurator(data: dict[str, Any], fam: dict[str, Any]) -> list[dict[str, Any]]:
    prices = (data.get("mainDisplayValues") or {}).get("prices") or {}
    display = data.get("mainDisplayValues") or {}
    config_display = data.get("configDisplayValues") or {}
    out: list[dict[str, Any]] = []
    for product in data.get("products") or []:
        if not isinstance(product, dict):
            continue
        if product.get("isComingSoon") or product.get("comingSoon"):
            continue
        dims = dict(product.get("dimensions") or {})
        price_key = product.get("priceKey") or ""
        price = _amount(prices.get(price_key))
        if price <= 0:
            continue
        chip = str(dims.get("processor-dimensionChip") or "").lower()
        if not chip:
            chip = _chip_from_part(
                str(product.get("aosContainerPartNumber") or product.get("btrOrFdPartNumber") or "")
            )
        cpu_gpu = str(
            dims.get("processor-cpuCoreCount-gpuCoreCount")
            or dims.get("processor-dimensionChip-cpuCoreCount-gpuCoreCount")
            or ""
        )
        screensize = str(
            dims.get("chassis-dimensionScreensize") or dims.get("dimensionScreensize") or ""
        )
        finish = str(dims.get("display-dimensionFinish") or "")
        storage = str(dims.get("storage-dimensionCapacity") or "")
        memory = str(dims.get("memory-dimensionMemory") or "")
        stand = str(dims.get("dimensionStandType") or "")
        chip_label = _format_chip_token(chip) if chip else _chip_label(chip, cpu_gpu, display, dims)
        memory_label = _header_for(
            config_display, "memory-dimensionMemory", memory
        ) or _default_config_label(config_display, "memory-dimensionMemory")
        storage_label = _header_for(
            config_display, "storage-dimensionCapacity", storage
        ) or _default_config_label(config_display, "storage-dimensionCapacity")
        part = product.get("btrOrFdPartNumber") or product.get("aosContainerPartNumber") or price_key
        if screensize:
            group_key = (screensize,)
        elif chip:
            group_key = (chip,)
        else:
            group_key = (cpu_gpu or part,)
        out.append(
            {
                "cat_key": fam["cat_key"],
                "family": fam["slug"],
                "family_url": fam["url"],
                "family_order": fam["family_order"],
                "part_number": str(part),
                "price": price,
                "type": product.get("type") or "",
                "chip": chip,
                "cpu_gpu": cpu_gpu,
                "screensize": screensize,
                "finish": finish,
                "stand": stand,
                "storage": storage,
                "memory": memory,
                "connection": "",
                "group_key": group_key,
                "specs": {
                    "chip_label": chip_label,
                    "memory": memory_label,
                    "storage": storage_label,
                    "display": _size_label(screensize),
                    "cpu_gpu": cpu_gpu,
                },
            }
        )
    return out


def _parse_metrics_products(data: dict[str, Any], fam: dict[str, Any]) -> list[dict[str, Any]]:
    prices = (data.get("displayValues") or {}).get("prices") or {}
    display = data.get("displayValues") or {}
    out: list[dict[str, Any]] = []
    for product in data.get("products") or []:
        if not isinstance(product, dict):
            continue
        if product.get("comingSoon") or product.get("isComingSoon") or product.get("getReady"):
            continue
        conn = str(product.get("dimensionConnection") or "").lower()
        part = str(product.get("partNumber") or "")
        price = _metrics_price(product, prices)
        if price <= 0:
            continue
        family_type = str(product.get("familyType") or product.get("productLocatorFamily") or fam["slug"])
        screensize = str(product.get("dimensionScreensize") or "")
        capacity = str(product.get("dimensionCapacity") or "")
        chip_label = _chip_from_family_type(family_type)
        finish = str(product.get("dimensionFinish") or "")
        stand = str(product.get("dimensionStandType") or "")
        out.append(
            {
                "cat_key": fam["cat_key"],
                "family": fam["slug"],
                "family_url": fam["url"],
                "family_order": fam["family_order"],
                "part_number": part,
                "price": price,
                "type": "PRECONFIGURED_BTR",
                "chip": chip_label.lower().replace(" ", ""),
                "cpu_gpu": "",
                "screensize": screensize,
                "finish": finish,
                "stand": stand,
                "storage": capacity,
                "memory": "",
                "connection": conn,
                "group_key": (family_type, screensize),
                "family_type": family_type,
                "specs": {
                    "chip_label": chip_label,
                    "memory": "",
                    "storage": _header_for(display, "dimensionCapacity", capacity) or _human_dim("storage", capacity),
                    "display": _header_for(display, "dimensionScreensize", screensize) or _size_label(screensize),
                    "connection": "Wi-Fi" if conn in WIFI_CONNECTIONS else "Wi-Fi + Cellular",
                    "cpu_gpu": "",
                },
            }
        )
    return out


def _pick_representatives(candidates: list[dict[str, Any]], fam: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[tuple, list[dict[str, Any]]] = {}
    for item in candidates:
        grouped.setdefault(item["group_key"], []).append(item)

    picked: list[dict[str, Any]] = []
    for _key, rows in grouped.items():
        best = _pick_best(rows)
        if not best:
            continue
        picked.append(_finalize_item(best, fam, cto_groups=_build_live_cto(best, rows)))
    return picked


def _pick_best(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    priced = [r for r in rows if int(r.get("price") or 0) > 0]
    if not priced:
        return None
    wifi = [r for r in priced if str(r.get("connection") or "") in WIFI_CONNECTIONS]
    pool = wifi or priced
    btr = [r for r in pool if r.get("type") == "PRECONFIGURED_BTR"]
    pool = btr or pool
    standard = [
        r
        for r in pool
        if str(r.get("finish") or "standard").lower() in ("", "standard", "glossy", "tiltadjuststand")
    ]
    pool = standard or pool
    return min(pool, key=lambda r: (int(r["price"]), r.get("part_number") or ""))


def _finalize_item(
    item: dict[str, Any],
    fam: dict[str, Any],
    cto_groups: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    family_label = _family_label(item, fam)
    specs = dict(item.get("specs") or {})
    size_txt = _size_label(item.get("screensize") or "")
    chip_txt = specs.get("chip_label") or ""
    storage_txt = specs.get("storage") or ""
    cpu_gpu = str(item.get("cpu_gpu") or specs.get("cpu_gpu") or "")
    tab_bits = [family_label]
    if size_txt:
        tab_bits.append(size_txt)
    if chip_txt and chip_txt.lower() not in family_label.lower() and "코어" not in chip_txt:
        tab_bits.append(chip_txt)
    if re.match(r"^\d+-\d+$", cpu_gpu) and not size_txt and "코어" not in chip_txt:
        cpu, gpu = cpu_gpu.split("-")
        tab_bits.append(f"{cpu}/{gpu}코어")
    tab_label = " ".join(tab_bits)

    model_bits = [f"Apple {family_label}"]
    if size_txt:
        model_bits.append(size_txt)
    if chip_txt and len(chip_txt) <= 24:
        model_bits.append(chip_txt)
    extra = []
    if re.match(r"^\d+-\d+$", cpu_gpu) and "코어" not in chip_txt:
        cpu, gpu = cpu_gpu.split("-")
        extra.append(f"{cpu}코어 CPU / {gpu}코어 GPU")
    if storage_txt:
        extra.append(storage_txt)
    if specs.get("connection") == "Wi-Fi" and fam["kind"] == "ipad":
        extra.append("Wi-Fi")
    model = " ".join(model_bits)
    if extra:
        model = f"{model} ({', '.join(extra)})"

    desc_parts = [p for p in (chip_txt, specs.get("memory"), storage_txt, specs.get("display")) if p]
    desc = " / ".join(desc_parts) if desc_parts else tab_label

    prefix = _tier_prefix(fam["cat_key"], fam["slug"])
    key_bits = [prefix]
    if size_txt:
        key_bits.append(re.sub(r"[^0-9.]", "", size_txt) or size_txt.lower())
    chip_key = (item.get("chip") or item.get("cpu_gpu") or "").lower().replace(" ", "")
    if not chip_key:
        chip_key = re.sub(r"[^a-z0-9]+", "", (chip_txt or "").lower())
    if chip_key and chip_key not in "".join(key_bits):
        key_bits.append(chip_key)
    tier_key = "-".join(b for b in key_bits if b)

    return {
        "cat_key": fam["cat_key"],
        "family": fam["slug"],
        "url": fam["url"],
        "family_order": fam["family_order"],
        "tier_key": tier_key,
        "tier": family_label,
        "tab_label": tab_label,
        "model": model,
        "desc": desc,
        "price": int(item["price"]),
        "part_number": item.get("part_number") or "",
        "pno": _stable_pno(item.get("part_number") or tier_key),
        "screensize": item.get("screensize") or "",
        "chip": item.get("chip") or "",
        "specs": specs,
        "price_source": "apple_official",
        "bom_key": "",
        "cto_groups": cto_groups or [],
    }


CTO_DIMS = (
    ("storage", "저장장치"),
    ("memory", "통합 메모리"),
    ("cpu_gpu", "프로세서"),
    ("connection", "연결"),
    ("finish", "디스플레이 마감"),
    ("stand", "스탠드"),
)


def _build_live_cto(best: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 패밀리 그룹에서 공식 가격이 다른 옵션만 CTO로 만든다."""
    base_price = int(best.get("price") or 0)
    if base_price <= 0 or len(rows) < 2:
        return []
    groups: list[dict[str, Any]] = []
    varying = [
        dim
        for dim, _name in CTO_DIMS
        if len({str(r.get(dim) or "") for r in rows if str(r.get(dim) or "")}) >= 2
    ]
    for dim, name in CTO_DIMS:
        if dim not in varying:
            continue
        hold = [d for d in varying if d != dim]
        options = _cto_options_for_dim(best, rows, dim, hold, base_price)
        if len(options) >= 2:
            groups.append({"id": dim, "name": name, "options": options})
    return groups


def _cto_options_for_dim(
    best: dict[str, Any],
    rows: list[dict[str, Any]],
    dim: str,
    hold: list[str],
    base_price: int,
) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if any(str(row.get(h) or "") != str(best.get(h) or "") for h in hold):
            continue
        val = str(row.get(dim) or "")
        if not val and dim == "connection":
            val = "wifi"
        if not val:
            continue
        buckets.setdefault(val, []).append(row)
    if len(buckets) < 2:
        return []
    options: list[dict[str, Any]] = []
    best_val = str(best.get(dim) or "") or ("wifi" if dim == "connection" else "")
    for val, group in buckets.items():
        price = min(int(r.get("price") or 0) for r in group)
        if price <= 0:
            continue
        delta = price - base_price
        label_name = _human_dim(dim, val)
        is_base = bool(best_val) and val == best_val
        options.append(
            {
                "value": val,
                "label": _option_label(label_name, delta, is_base=is_base),
                "delta": delta,
                "name_override": f"[Apple] {label_name}" if delta else "",
            }
        )
    options.sort(key=lambda o: (int(o["delta"]), str(o.get("value") or "")))
    if best_val:
        options.sort(key=lambda o: (0 if o.get("value") == best_val else 1, int(o["delta"])))
    if options and best_val and not any(o.get("value") == best_val for o in options):
        cheapest = options[0]
        cheapest["delta"] = 0
        cheapest["name_override"] = ""
        cheapest["label"] = _option_label(
            _human_dim(dim, str(cheapest.get("value") or "")), 0, is_base=True
        )
    return options if len(options) >= 2 else []


def _human_dim(dim: str, value: str) -> str:
    v = str(value or "").strip()
    if dim == "cpu_gpu":
        m = re.search(r"(\d+)-(\d+)$", v)
        if m:
            return f"{m.group(1)}코어 CPU / {m.group(2)}코어 GPU"
        return v
    if dim == "storage":
        m = re.match(r"(\d+)\s*(gb|tb)", v, re.I)
        if m:
            return f"{m.group(1)}{m.group(2).upper()}"
        return v.upper() if v else v
    if dim == "memory":
        m = re.match(r"(\d+)\s*(gb|tb)", v, re.I)
        if m:
            return f"{m.group(1)}{m.group(2).upper()} 통합 메모리"
        return v
    if dim == "connection":
        return "Wi-Fi" if v.lower() in WIFI_CONNECTIONS else "Wi-Fi + Cellular"
    if dim == "finish":
        key = re.sub(r"[^a-z]", "", v.lower())
        return {
            "standard": "스탠다드 글래스",
            "glossy": "스탠다드 글래스",
            "nanotexture": "나노 텍스처 글래스",
            "nano": "나노 텍스처 글래스",
            "matte": "나노 텍스처 글래스",
        }.get(key, v)
    if dim == "stand":
        key = re.sub(r"[^a-z]", "", v.lower())
        return {
            "tiltadjuststand": "기울기 조절 스탠드",
            "tiltheightadjuststand": "기울기 및 높이 조절 스탠드",
            "vesamountadapter": "VESA 마운트 어댑터",
            "vesa": "VESA 마운트 어댑터",
        }.get(key, v)
    return v


def _option_label(name: str, delta: int, *, is_base: bool = False) -> str:
    if is_base:
        return f"{name} (기본)"
    if int(delta) == 0:
        return f"{name} (동일 가격)"
    sign = "+" if delta > 0 else ""
    return f"{name} ({sign}{int(delta):,}원)"


def fetch_apple_cto_options(tier_key: str) -> list[dict[str, Any]]:
    """카탈로그 스냅샷에 저장된 라이브 CTO 옵션을 반환한다."""
    key = str(tier_key or "").strip()
    if not key:
        return []
    found = _lookup_cto(key)
    if found is not None:
        return found
    try:
        fetch_apple_catalog("all")
    except Exception as e:
        logger.warning("Apple CTO live sync failed for %s: %s", key, e)
    found = _lookup_cto(key)
    return found or []


def _lookup_cto(tier_key: str) -> list[dict[str, Any]] | None:
    with _cache_lock:
        for payload in _catalog_cache.values():
            found = _cto_from_items(payload.get("items") or [], tier_key)
            if found is not None:
                return found
    snap = _load_all_snapshots()
    for payload in snap.values():
        found = _cto_from_items((payload or {}).get("items") or [], tier_key)
        if found is not None:
            return found
    return None


def _cto_from_items(items: list[dict[str, Any]], tier_key: str) -> list[dict[str, Any]] | None:
    for item in items:
        if item.get("tier_key") == tier_key:
            if "cto_groups" not in item:
                return None
            return list(item.get("cto_groups") or [])
    return None


def _load_all_snapshots() -> dict[str, Any]:
    try:
        if not SNAPSHOT_PATH.exists():
            return {}
        data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("Apple catalog snapshot load failed: %s", e)
        return {}


def _family_label(item: dict[str, Any], fam: dict[str, Any]) -> str:
    family_type = str(item.get("family_type") or "")
    compact = family_type.replace("_", "").replace("-", "").lower()
    if "promax" in compact:
        base = _label_from_slug(fam["slug"])
        if "Pro Max" in base:
            return base
        if "Pro" in base:
            return base.replace("Pro", "Pro Max", 1)
        return f"{base} Pro Max"
    return _label_from_slug(fam["slug"])


def _label_from_slug(slug: str) -> str:
    if slug in FAMILY_LABELS:
        return FAMILY_LABELS[slug]
    special = {
        "ipad": "iPad",
        "iphone": "iPhone",
        "macbook": "MacBook",
        "imac": "iMac",
        "mac": "Mac",
        "mini": "mini",
        "pro": "Pro",
        "max": "Max",
        "air": "Air",
        "neo": "Neo",
        "ultra": "Ultra",
        "xdr": "XDR",
        "studio": "Studio",
        "display": "Display",
    }
    parts = []
    for token in (slug or "").split("-"):
        if token in special:
            parts.append(special[token])
        elif re.fullmatch(r"m\d+[a-z]*", token):
            parts.append(_format_chip_token(token))
        elif re.fullmatch(r"\d+[a-z]+", token):
            parts.append(token)
        elif token.isdigit():
            parts.append(token)
        else:
            parts.append(token.upper() if len(token) <= 3 else token.title())
    return " ".join(parts) or "Apple"


def _chip_from_part(part: str) -> str:
    m = re.search(
        r"(?:^|_|-)(m\d+(?:pro|max|ultra)?|a\d{2}(?:pro)?)(?:_|-|$)",
        part or "",
        re.I,
    )
    return m.group(1).lower() if m else ""


def _chip_from_family_type(family_type: str) -> str:
    m = re.search(r"(?:^|[_-])(m\d(?:pro|max|ultra)?|a\d{2}(?:pro)?)(?:[_-]|$)", family_type or "", re.I)
    if m:
        return _format_chip_token(m.group(1))
    return ""


def _chip_label(chip: str, cpu_gpu: str, display: dict[str, Any], dims: dict[str, Any]) -> str:
    if chip:
        return _format_chip_token(chip)
    header = _header_for(display, "processor-cpuCoreCount-gpuCoreCount", cpu_gpu)
    found = re.search(r"\b(M\d+(?:\s+(?:Pro|Max|Ultra))?|A\d{2}(?:\s+Pro)?)\b", header or "")
    if found:
        return found.group(1)
    if header:
        return header.split(",")[0].strip()
    if cpu_gpu and re.match(r"^\d+-\d+$", cpu_gpu):
        cpu, gpu = cpu_gpu.split("-")
        return f"{cpu}코어 CPU / {gpu}코어 GPU"
    return ""


def _format_chip_token(token: str) -> str:
    t = (token or "").strip().lower()
    for src, dst in (
        ("m5ultra", "M5 Ultra"),
        ("m5max", "M5 Max"),
        ("m5pro", "M5 Pro"),
        ("m4ultra", "M4 Ultra"),
        ("m4max", "M4 Max"),
        ("m4pro", "M4 Pro"),
        ("m3ultra", "M3 Ultra"),
        ("a18pro", "A18 Pro"),
        ("a19pro", "A19 Pro"),
    ):
        if t == src:
            return dst
    m = re.match(r"^(m|a)(\d+)(pro|max|ultra)?$", t)
    if m:
        suffix = {"pro": " Pro", "max": " Max", "ultra": " Ultra"}.get(m.group(3) or "", "")
        return f"{m.group(1).upper()}{m.group(2)}{suffix}"
    return token.upper() if token else ""


def _default_config_label(display: dict[str, Any], key: str) -> str:
    block = display.get(key) or {}
    order = block.get("variantOrder") or []
    if not order:
        return ""
    return _header_for(display, key, order[0])


def _header_for(display: dict[str, Any], key: str, variant: str) -> str:
    if not variant:
        return ""
    block = display.get(key) or {}
    entry = block.get(variant) or {}
    if isinstance(entry, dict):
        return _strip_html(str(entry.get("header") or ""))
    return _strip_html(str(entry))


def _size_label(screensize: str) -> str:
    if not screensize:
        return ""
    s = screensize.lower().replace("inch", "")
    s = s.replace("_", ".")
    s = s.strip(".")
    if not s:
        return ""
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        if "." in s:
            return f'{s}"'
        return s
    return screensize


def _size_sort(screensize: str) -> tuple:
    m = re.search(r"(\d+(?:[._]\d+)?)", screensize or "")
    if not m:
        return (99.0, screensize or "")
    return (float(m.group(1).replace("_", ".")), screensize)


def _tier_prefix(cat_key: str, slug: str) -> str:
    aliases = {
        "mac-mini": "macmini",
        "mac-studio": "macstudio",
    }
    if cat_key == "accessories":
        return "apple-" + slug
    return aliases.get(slug, slug)


def _stable_pno(token: str) -> int:
    return 900000 + (zlib.crc32((token or "apple").encode("utf-8")) & 0xFFFFFFFF) % 99000


def _metrics_price(product: dict[str, Any], prices: dict[str, Any]) -> int:
    part = str(product.get("partNumber") or "")
    keys = [
        product.get("fullPrice"),
        product.get("price"),
        part.lower().replace("/", "_") if part else "",
        part,
    ]
    for key in keys:
        if key and key in prices:
            amount = _amount(prices[key])
            if amount > 0:
                return amount
    if product.get("price"):
        return _amount(product.get("price"))
    return 0


def _amount(value: Any) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, dict):
        for key in ("amountBeforeTradeIn", "amount", "seoPrice"):
            if value.get(key) not in (None, ""):
                n = _amount(value.get(key))
                if n > 0:
                    return n
        current = value.get("currentPrice")
        if isinstance(current, dict):
            n = _amount(current.get("raw_amount") or current.get("amount"))
            if n > 0:
                return n
        return _amount(current)
    text = str(value).replace(",", "").replace("₩", "").strip()
    text = text.replace("_", ".")
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    if not m:
        return 0
    return int(float(m.group(1)))


def _strip_html(text: str) -> str:
    text = re.split(r"<div\b", text or "", maxsplit=1)[0]
    text = unescape(HTML_TAG_RE.sub(" ", text))
    text = WS_RE.sub(" ", text).strip(" .")
    return text


def _save_snapshot(cat_key: str, payload: dict[str, Any]) -> None:
    try:
        data = {}
        if SNAPSHOT_PATH.exists():
            data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        # 이전 리스트를 완전히 제거하고 신규 크롤링된 리스트로 100% 교체
        data[cat_key] = {
            "schema": CATALOG_SCHEMA,
            "fetched_ts": payload.get("fetched_ts"),
            "meta": payload.get("meta"),
            "items": payload.get("items"),
        }
        if cat_key == "all":
            # 전체 카테고리 동기화 시 개별 카테고리 스냅샷도 최신 신규 데이터로 분배하여 교체
            items = payload.get("items") or []
            cat_buckets: dict[str, list[dict[str, Any]]] = {}
            for it in items:
                ck = it.get("cat_key")
                if ck:
                    cat_buckets.setdefault(ck, []).append(it)
            for ck, sub_items in cat_buckets.items():
                data[ck] = {
                    "schema": CATALOG_SCHEMA,
                    "fetched_ts": payload.get("fetched_ts"),
                    "meta": payload.get("meta"),
                    "items": sub_items,
                }
        tmp = SNAPSHOT_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(SNAPSHOT_PATH)
    except Exception as e:
        logger.warning("Apple catalog snapshot save failed: %s", e)


def _load_snapshot(cat_key: str) -> dict[str, Any] | None:
    try:
        if not SNAPSHOT_PATH.exists():
            return None
        data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        row = data.get(cat_key)
        if not isinstance(row, dict):
            return None
        if int(row.get("schema") or 0) != CATALOG_SCHEMA:
            return None
        return row
    except Exception as e:
        logger.warning("Apple catalog snapshot load failed: %s", e)
        return None
