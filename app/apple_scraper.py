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
import urllib.parse
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
CATALOG_SCHEMA = 2  # 항목의 cfg(구성하기 API 정보)는 선택 항목이라 예전 스냅샷도 그대로 쓴다

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
    # 구성하기(CTO) 가격 API 의 컬렉션 이름. Mac 구매 페이지에만 있다.
    m = re.search(r"updateConfigUrl:\s*'[^']*collection=([A-Za-z0-9_]+)", html)
    collection = m.group(1) if m else ""
    return _pick_representatives(candidates, fam, collection)


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
                # 구성하기 API 에 그대로 넘길 원래 차원 값 (색상 포함)
                "cfg_params": {k: str(v) for k, v in dims.items() if v},
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


def _pick_representatives(
    candidates: list[dict[str, Any]], fam: dict[str, Any], collection: str = ""
) -> list[dict[str, Any]]:
    grouped: dict[tuple, list[dict[str, Any]]] = {}
    for item in candidates:
        grouped.setdefault(item["group_key"], []).append(item)

    picked: list[dict[str, Any]] = []
    for _key, rows in grouped.items():
        best = _pick_best(rows)
        if not best:
            continue
        cfg = None
        if collection and best.get("cfg_params"):
            cfg = {"collection": collection, "params": dict(best["cfg_params"])}
            best = _apply_base_config_specs(best, cfg)
        picked.append(_finalize_item(best, fam, cto_groups=_build_live_cto(best, rows), cfg=cfg))
    return picked


def _apply_base_config_specs(item: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """구매 페이지에는 기본 메모리·SSD 가 없어 최소 용량으로 적히는 모델이 있다(예: Mac mini M5 Pro 는 24GB·512GB).
    구성하기 API 의 기본 구성으로 사양 표기를 바로잡는다. 실패하면 그대로 둔다."""
    base = _update_config(cfg, {})
    if not base:
        return item
    sel = _selected_values(base)
    specs = dict(item.get("specs") or {})
    mem = sel.get("memory-dimensionMemory")
    if mem:
        specs["memory"] = _human_dim("memory", mem).replace(" 통합 메모리", "")
    sto = sel.get("storage-dimensionCapacity")
    if sto:
        specs["storage"] = _human_dim("storage", sto)
    return {**item, "specs": specs}


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
    cfg: dict[str, Any] | None = None,
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
        "cfg": cfg,
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


# ---------------------------------------------------------------------------
# 구성하기(CTO) 실시간 가격: 공식몰 /shop/api/cto/update-config
# 옵션 가격은 서로 독립적이지 않다(칩을 바꾸면 메모리 기본값·가격이 바뀐다).
# 그래서 선택이 바뀔 때마다 전체 선택값으로 다시 물어 총액을 받는다.
# ---------------------------------------------------------------------------
CTO_LIVE_DIMS = {
    "processor-dimensionChip-cpuCoreCount-gpuCoreCount": "프로세서",
    "processor-cpuCoreCount-gpuCoreCount": "프로세서",
    "memory-dimensionMemory": "통합 메모리",
    "storage-dimensionCapacity": "저장장치",
    "display-dimensionFinish": "디스플레이 마감",
    "ethernet_adapter-ethernetPortCount": "이더넷",
    "ethernet_adapter-ethernetBandwidth": "이더넷",
    "power_adapter-wattage": "전원 어댑터",
}
_cfg_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _update_config(cfg: dict[str, Any], selections: dict[str, str]) -> dict[str, Any] | None:
    params = dict(cfg.get("params") or {})
    for dim, val in (selections or {}).items():
        if dim in CTO_LIVE_DIMS and val:
            params[dim] = str(val)
    # 칩+코어 조합을 바꾸면 칩 차원도 같이 맞춰야 API 가 그 구성으로 계산한다.
    combo = params.get("processor-dimensionChip-cpuCoreCount-gpuCoreCount")
    if combo and "processor-dimensionChip" in params:
        params["processor-dimensionChip"] = combo.split("-")[0]
    query = "&".join(
        f"sv.{urllib.parse.quote(k)}={urllib.parse.quote(v)}" for k, v in sorted(params.items())
    )
    url = (
        f"{APPLE_ORIGIN}/kr/shop/api/cto/update-config?collection="
        f"{urllib.parse.quote(cfg['collection'])}&fae=true&{query}"
    )
    now = time.time()
    with _cache_lock:
        hit = _cfg_cache.get(url)
        if hit and now - hit[0] < HTML_TTL_SEC:
            return hit[1]
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json", "Accept-Language": "ko-KR,ko;q=0.9"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SEC) as resp:
            body = (json.loads(resp.read().decode("utf-8")) or {}).get("body") or {}
    except Exception as e:
        logger.warning("Apple update-config failed for %s: %s", url, e)
        return None
    if not body.get("options"):
        return None
    with _cache_lock:
        _cfg_cache[url] = (time.time(), body)
    return body


def _live_option_label(dim: str, value: str) -> str:
    v = str(value or "")
    if dim.startswith("processor-"):
        m = re.match(r"^([a-z0-9]+?)-(\d+)-(\d+)$", v)
        if m:
            return f"{_format_chip_token(m.group(1))} {m.group(2)}코어 CPU / {m.group(3)}코어 GPU"
        return _human_dim("cpu_gpu", v)
    if dim == "memory-dimensionMemory":
        return _human_dim("memory", v)
    if dim == "storage-dimensionCapacity":
        return _human_dim("storage", v)
    if dim == "display-dimensionFinish":
        return _human_dim("finish", v)
    if dim == "ethernet_adapter-ethernetPortCount":
        return "이더넷 없음 (Wi-Fi)" if v == "0" else "기가비트 이더넷"
    if dim == "ethernet_adapter-ethernetBandwidth":
        m = re.match(r"^(\d+)(?:_(\d+))?gb", v)
        if m:
            speed = m.group(1) + (f".{m.group(2)}" if m.group(2) else "")
            return f"{speed}Gb 이더넷"
        return v
    if dim == "power_adapter-wattage":
        return f"{v.upper()} 전원 어댑터"
    return v


def _dim_sort_key(value: str) -> float:
    """'24gb', '2tb', '70w' 처럼 크기가 붙은 값을 크기순으로 정렬하기 위한 키."""
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(gb|tb|w)?", str(value or "").lower())
    if not m:
        return 0.0
    n = float(m.group(1))
    return n * 1024 if m.group(2) == "tb" else n


def _price_of(body: dict[str, Any], key: Any) -> int | None:
    if not key:
        return None
    hit = (body.get("prices") or {}).get(key)
    if not hit:
        return None
    try:
        return int(round(float(hit.get("amount") or 0)))
    except (TypeError, ValueError):
        return None


def _selected_values(body: dict[str, Any]) -> dict[str, str]:
    """실제로 계산된 구성. selectedKits 가 기준이다(options 의 isSelected 는 요청값을 그대로 돌려줄 때가 있다)."""
    out: dict[str, str] = {}
    kit_dims = (body.get("selectedKits") or {}).get("dimensions") or {}
    for dim, info in kit_dims.items():
        val = (info or {}).get("dimensionValue")
        if val is not None:
            out[dim] = str(val)
    for dim, o in (body.get("options") or {}).items():
        if dim in out:
            continue
        comp = o.get("compatibleOptions") or {}
        sel = next((k for k, v in comp.items() if v.get("isSelected")), None)
        sel = sel or next((k for k, v in comp.items() if v.get("isDefault")), None)
        if sel is not None:
            out[dim] = str(sel)
    return out


def _total_of(body: dict[str, Any]) -> int:
    price = ((body.get("selectedKits") or {}).get("priceData") or {}).get("amount")
    try:
        return int(round(float(price or 0)))
    except (TypeError, ValueError):
        return 0


def fetch_apple_cto_live(
    tier_key: str, selections: dict[str, str] | None = None, changed: str = ""
) -> dict[str, Any] | None:
    """구성하기 API 로 현재 선택의 옵션·총액·추가 품목 행을 만든다. 쓸 수 없으면 None."""
    item = _lookup_item(str(tier_key or "").strip())
    cfg = (item or {}).get("cfg")
    if not cfg:
        return None
    base = _update_config(cfg, {})
    if not base:
        return None
    sel = {k: str(v) for k, v in (selections or {}).items() if k in CTO_LIVE_DIMS and v}
    if sel:
        # 일부 차원만 보내면 공식몰이 임의의 구성(예: 1TB·35W)을 고른다. 빠진 차원은 기본 구성 값으로 채운다.
        base_fill = {d: v for d, v in _selected_values(base).items() if d in CTO_LIVE_DIMS}
        sel = {**base_fill, **sel}
    def _took(body: dict[str, Any] | None, req: dict[str, str]) -> bool:
        # 공식몰은 불가능한 조합도 오류 없이 받고 다른 구성으로 계산할 때가 있다. 요청값이 실제 구성에 들어갔는지 본다.
        if not body:
            return False
        got = _selected_values(body)
        return all(got.get(d) == v for d, v in req.items())

    cur = _update_config(cfg, sel) if sel else base
    if sel and not _took(cur, sel):
        cur = None
    if not cur and sel:
        # 칩을 바꾸면 이전 메모리·저장장치 값이 그 칩에서 불가능할 수 있다(예: M5 Max 는 48GB 부터).
        # 프로세서 → 방금 바꾼 항목 → 나머지 순으로 하나씩 더해 보며 API 가 받아 주는 조합만 남긴다.
        # (방금 바꾼 항목이 예전 선택보다 먼저 들어가야, 충돌할 때 예전 선택이 빠진다)
        ordered = sorted(
            sel.items(),
            key=lambda kv: 0 if kv[0].startswith("processor-") else (1 if kv[0] == changed else 2),
        )
        accepted: dict[str, str] = {}
        pending = list(ordered)
        # 앞 항목이 받아들여져야 가능한 값도 있어서(예: 10코어 GPU 를 먼저 골라야 24GB 가능) 진전이 없을 때까지 반복한다.
        for _round in range(3):
            rejected = []
            for dim, val in pending:
                trial = {**accepted, dim: val}
                body = _update_config(cfg, trial)
                if _took(body, trial):
                    accepted, cur = trial, body
                else:
                    rejected.append((dim, val))
            if not rejected or len(rejected) == len(pending):
                break
            pending = rejected
    if not cur:
        return None

    base_total = _total_of(base) or int(item.get("price") or 0)
    total = _total_of(cur) or base_total
    base_sel = _selected_values(base)
    cur_sel = _selected_values(cur)

    groups: list[dict[str, Any]] = []
    for dim, name in CTO_LIVE_DIMS.items():
        o = (cur.get("options") or {}).get(dim)
        if not o:
            continue
        opts = []
        for val, info in (o.get("compatibleOptions") or {}).items():
            delta = _price_of(cur, info.get("priceDelta"))
            if delta is None or info.get("isBlocked"):
                continue
            label = _live_option_label(dim, val)
            is_sel = cur_sel.get(dim) == str(val)
            if is_sel:
                text = f"{label} (선택됨)"
            elif delta == 0:
                text = f"{label} (동일 가격)"
            else:
                text = f"{label} ({'+' if delta > 0 else ''}{delta:,}원)"
            opts.append({"value": str(val), "label": text, "delta": delta, "selected": is_sel})
        # upgradeOptions: 고르면 다른 구성도 함께 바뀌어야 하는 옵션(예: 30W 어댑터 모델의 메모리 업그레이드).
        # 가격은 고른 뒤 API 가 계산한다.
        for val, info in (o.get("upgradeOptions") or {}).items():
            if info.get("isBlocked") or any(x["value"] == str(val) for x in opts):
                continue
            label = _live_option_label(dim, val)
            opts.append({"value": str(val), "label": f"{label} (선택 시 계산)", "delta": None, "selected": False})
        if len(opts) >= 2:
            opts.sort(key=lambda x: (x["delta"] is None, x["delta"] or 0, _dim_sort_key(x["value"])))
            groups.append({"id": dim, "name": name, "options": opts})

    # 기본 모델에서는 API 가 칩 변경을 주지 않는 경우가 있다(예: MacBook Pro 14 M5).
    # 구매 페이지의 다른 완제품 가격으로 만든 프로세서 옵션을 붙이고, 선택하면 API 로 정확한 총액을 받는다.
    proc_dim = next((d for d in base_sel if d.startswith("processor-") and d in CTO_LIVE_DIMS), "")
    static_proc = next((g for g in item.get("cto_groups") or [] if g.get("id") == "cpu_gpu"), None)
    if proc_dim and static_proc and not any(g["id"] == proc_dim for g in groups):
        static_delta = {str(o.get("value")): int(o.get("delta") or 0) for o in static_proc.get("options") or []}
        cur_val = cur_sel.get(proc_dim, "")
        if cur_val and cur_val not in static_delta:
            static_delta[cur_val] = total - base_total
        opts = []
        for val, d in sorted(static_delta.items(), key=lambda kv: kv[1]):
            label = _live_option_label(proc_dim, val)
            rel = d - static_delta.get(cur_val, 0)
            if val == cur_val:
                text = f"{label} (선택됨)"
            elif rel == 0:
                text = f"{label} (동일 가격)"
            else:
                text = f"{label} ({'+' if rel > 0 else ''}{rel:,}원)"
            opts.append({"value": val, "label": text, "delta": rel, "selected": val == cur_val})
        if len(opts) >= 2:
            groups.insert(0, {"id": proc_dim, "name": CTO_LIVE_DIMS[proc_dim], "options": opts})
    if not groups:
        return None  # 바꿀 옵션이 없으면 구매 페이지 비교 옵션(예: MacBook Neo 저장장치)을 쓴다

    # 추가 품목 행: 기본값에서 바뀐 차원마다 "기본값으로 되돌릴 때 빠지는 금액"을 그 행의 금액으로 둔다.
    parts: list[dict[str, Any]] = []
    for dim, name in CTO_LIVE_DIMS.items():
        now_val = cur_sel.get(dim)
        if not now_val or now_val == base_sel.get(dim):
            continue
        back = ((cur.get("options") or {}).get(dim) or {}).get("compatibleOptions", {}).get(base_sel.get(dim) or "")
        back_delta = _price_of(cur, (back or {}).get("priceDelta"))
        parts.append(
            {
                "category": name,
                "name": f"[Apple] {_live_option_label(dim, now_val)}",
                "amount": -back_delta if back_delta is not None else None,
            }
        )
    # 행 금액 합이 총액 차이와 다르면(칩을 바꿔 메모리·저장장치 기본값이 함께 바뀐 경우 등)
    # 차액을 금액을 알 수 없는 첫 행(대개 프로세서)에, 없으면 마지막 행에 넣는다.
    # 금액이 0 인 행도 남긴다: "48GB 통합 메모리(포함)"처럼 바뀐 구성을 견적서에 보여 준다.
    diff = total - base_total
    known = sum(p["amount"] for p in parts if p["amount"] is not None)
    unknown = [p for p in parts if p["amount"] is None]
    for p in unknown:
        p["amount"] = 0
    if parts and known != diff:
        (unknown[0] if unknown else parts[-1])["amount"] += diff - known
    if not parts and diff:
        parts = [{"category": "구성 변경", "name": "[Apple] 맞춤 구성", "amount": diff}]

    return {
        "live": True,
        "groups": groups,
        "total": total,
        "base_total": base_total,
        "selected": {k: v for k, v in cur_sel.items() if k in CTO_LIVE_DIMS},
        "parts": parts,
    }


def _lookup_item(tier_key: str) -> dict[str, Any] | None:
    with _cache_lock:
        for payload in _catalog_cache.values():
            for it in payload.get("items") or []:
                if it.get("tier_key") == tier_key:
                    return it
    for payload in _load_all_snapshots().values():
        for it in (payload or {}).get("items") or []:
            if it.get("tier_key") == tier_key:
                return it
    return None


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
