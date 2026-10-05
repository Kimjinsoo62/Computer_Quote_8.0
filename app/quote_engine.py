# -*- coding: utf-8 -*-
"""
컴퓨존 견적 추출 엔진

로직 흐름 (AI PC):
  1. DISCOVER → 기획전/조립PC 메인관에서 ProductNo 수집
  2. FETCH    → 상품 상세 HTML 요청 (EUC-KR)
  3. PARSE    → 실시간 BOM 슬롯 + CardPrice
  4. ADJUST   → (옵션) 모니터/키보드/메모리32GB + 번들할인 행
  5. FORMAT   → 견적서 JSON/Excel
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime
from typing import Any

from app.quote_info import extra_item_prices
from app.config import (
    AI_PC_SEED_PNOS,
    ALL_ASSEMBLED_URL,
    APPLE_CATEGORIES,
    APPLE_FALLBACK_BOM,
    INITIAL_SETUP_ITEM,
    SETUP_EXTRA_CAT,
    ASSEMBLED_CATEGORIES,
    ASSEMBLED_PRICE_SOURCES,
    BASE_PRODUCT_URL,
    PRICE_SEARCH_LIMIT,
    CATEGORY_ORDER,
    CONSUMER_MARGIN_PERCENT,
    ESTIMATE_TOKEN_URL,
    EVENT_DESKTOP_URL,
    EVENT_NO,
    EVENT_TITLE,
    EVENT_URL,
    EXTRA_ITEMS,
    GAMING_PC_API_URL,
    GAMING_PC_URL,
    GAMING_RESOLUTIONS,
    MAC_APPLE_CATS,
    MAC_TIER_PREFIXES,
    QUOTE_DISCLAIMER,
    SERIES_NAME,
    SHARE_PRODUCT_URL,
)

# 모니터·키보드마우스 등 공통 추가 항목 합계 (수량 0 항목은 별도청구로 제외)
EXTRA_ITEMS_TOTAL = sum(price * qty for _, _, price, qty in EXTRA_ITEMS)
KEYBOARD_EXTRA_CAT = "키보드마우스"
DISCOUNT_CAT = "할인"


def _priced_extra(item: tuple, prices: dict[str, int] | None = None) -> tuple:
    """견적 정보에 저장된 모니터·키보드·초기 세팅비 단가를 반영한다. 0원이면 별도청구."""
    cat, name, price, qty = item[0], item[1], item[2], item[3]
    if prices is None:
        prices = extra_item_prices()
    if cat in prices:
        price = prices[cat]
        qty = 1 if price > 0 else 0
    return (cat, name, price, qty)


def _active_extras(
    include_monitor: bool = False,
    include_keyboard: bool = False,
    include_setup: bool = False,
) -> list[tuple]:
    """모니터·무선키보드·초기 세팅비 포함 옵션이 적용된 공통 추가 항목"""
    prices = extra_item_prices()
    rows: list[tuple] = []
    for item in EXTRA_ITEMS:
        cat = item[0]
        if cat == "모니터" and not include_monitor:
            continue
        if cat == KEYBOARD_EXTRA_CAT and not include_keyboard:
            continue
        if cat == SETUP_EXTRA_CAT and not include_setup:
            continue
        rows.append(_priced_extra(item, prices))
    return rows


def _extras_total(
    include_monitor: bool = False,
    include_keyboard: bool = False,
    include_setup: bool = False,
) -> int:
    return sum(
        price * qty
        for _, _, price, qty in _active_extras(
            include_monitor, include_keyboard, include_setup
        )
    )


def is_mac_tier(tier_key: str) -> bool:
    key = str(tier_key or "").lower()
    return key.startswith(MAC_TIER_PREFIXES)


def is_mac_apple_cat(cat_key: str) -> bool:
    return str(cat_key or "").replace("apple_", "") in MAC_APPLE_CATS


MANWON_UNIT = 10_000


def ceil_to_manwon(amount: int) -> int:
    """천원 단위는 의미 없으므로 만원 단위로 올림한다. 음수는 절댓값을 올림한다."""
    n = int(amount or 0)
    if n == 0:
        return 0
    unit = MANWON_UNIT
    if n > 0:
        return ((n + unit - 1) // unit) * unit
    return -(((-n) + unit - 1) // unit) * unit


def marked_up_amount(amount: int, margin_percent: int) -> int:
    """총액에 마진율(%)을 더한 소비자가. 0%면 원가 그대로."""
    if margin_percent <= 0:
        return int(amount or 0)
    return int(round(int(amount or 0) * (100 + int(margin_percent)) / 100.0))


def margin_kind_label(margin_percent: int) -> str:
    pct = int(margin_percent or 0)
    if pct == CONSUMER_MARGIN_PERCENT:
        return "소비자견적"
    if pct == 0:
        return "원가견적"
    return f"마진 {pct}% 견적"


def apply_manwon_rounding(result: TodayQuotesResult) -> TodayQuotesResult:
    """모든 금액을 만원 단위로 올림한 사본을 반환한다."""
    if not result:
        return result
    quotes = []
    for q in result.quotes:
        parts = [
            replace(
                p,
                unit_price=ceil_to_manwon(p.unit_price),
                amount=ceil_to_manwon(p.amount),
            )
            for p in q.parts
        ]
        quotes.append(
            replace(
                q,
                parts=parts,
                total=ceil_to_manwon(q.total),
                parts_subtotal=ceil_to_manwon(q.parts_subtotal),
            )
        )
    return replace(result, quotes=quotes)


def marked_up_part(part: QuotePart, margin_percent: int) -> QuotePart:
    """품목 단가에 마진을 얹고 만원 단위로 올린다. 금액은 단가 x 수량으로 다시 맞춘다."""
    unit = ceil_to_manwon(marked_up_amount(part.unit_price, margin_percent))
    return replace(part, unit_price=unit, amount=unit * part.qty if part.qty > 0 else 0)


def reconcile_parts_to_total(parts: list[QuotePart], total: int) -> list[QuotePart]:
    """단가를 만원 단위로 올리며 생긴 잔액을 번들할인 행에 흡수시켜 품목합=합계로 맞춘다."""
    diff = total - sum(p.amount for p in parts)
    if diff == 0:
        return parts
    for i in range(len(parts) - 1, -1, -1):
        if parts[i].category != DISCOUNT_CAT:
            continue
        amount = parts[i].amount + diff
        parts[i] = replace(parts[i], unit_price=amount, amount=amount)
        break
    return parts


def apply_quote_margin(result: TodayQuotesResult, margin_percent: int) -> TodayQuotesResult:
    """마진을 합계와 품목 단가에 함께 적용한 사본을 반환한다. 0%는 컴퓨존 단가 그대로."""
    if not result:
        return result
    pct = int(margin_percent or 0)
    if pct <= 0:
        return result
    quotes = []
    for q in result.quotes:
        total = ceil_to_manwon(marked_up_amount(q.total, pct))
        parts = reconcile_parts_to_total(
            [marked_up_part(p, pct) for p in q.parts], total
        )
        quotes.append(
            replace(
                q,
                parts=parts,
                total=total,
                parts_subtotal=sum(p.amount for p in parts),
            )
        )
    return replace(result, quotes=quotes)


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


@dataclass
class QuotePart:
    no: int
    category: str
    name: str
    qty: int
    unit_price: int
    amount: int


@dataclass
class Quote:
    tier: str
    tier_key: str
    model: str
    tab_label: str
    description: str
    product_no: int
    url: str
    quote_date: str
    parts: list[QuotePart] = field(default_factory=list)
    parts_subtotal: int = 0
    total: int = 0
    price_source: str = "live"
    fetched_at: str = ""
    cto_groups: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TodayQuotesResult:
    quote_date: str
    event_url: str
    event_title: str
    series: str
    quotes: list[Quote]
    logic_steps: list[dict[str, str]]
    catalog_synced_at: str = ""
    engine: str = ""


def _sort_key(category: str) -> int:
    try:
        return CATEGORY_ORDER.index(category)
    except ValueError:
        return 99


def _fetch_html(pno: int) -> str:
    url = BASE_PRODUCT_URL.format(pno=pno)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read().decode("euc-kr", errors="replace")


def _parse_card_price(html: str) -> int | None:
    patterns = [
        r"CardPrice\s*=\s*[\"']?(\d+)[\"']?",
        r"(?:ProdPrice|ProductPrice|salePrice|price)\s*[:=]\s*[\"']?(\d+)[\"']?",
        r'property="og:price:amount"\s+content="(\d+)"',
        r'itemprop="price"\s+content="(\d+)"',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            val = int(m.group(1))
            if val > 10000:
                return val
    return None


def _parse_canonical_url(html: str, pno: int) -> str:
    m = re.search(r'property="og:url"\s+content="([^"]+)"', html)
    if m:
        url = m.group(1).strip()
        if not url.startswith("http"):
            url = "https://" + url.lstrip("/")
        return url
    return BASE_PRODUCT_URL.format(pno=pno)


def _parse_bundle_slots(html: str) -> list[tuple[str, str, str]]:
    slots: list[tuple[str, str, str]] = []
    for slot_id, qty in re.findall(r"DefListProductEa\[(\d+)\]' value=\"(\d+)\"", html):
        price_m = re.search(
            rf"id=['\"]ListPrice{slot_id}['\"][^>]*prm_ori=\"(\d+)\"",
            html,
        )
        if price_m:
            slots.append((slot_id, qty, price_m.group(1)))
    return slots


def _fetch_share_token(
    pno: int,
    html: str,
    quote_overrides: dict[str, dict[str, str]] | None = None,
) -> str | None:
    slots = _parse_bundle_slots(html)
    if not slots:
        return None

    post: list[tuple[str, str]] = [("actype", "setSpecEstimateShare")]
    for slot_id, qty, price in slots:
        product_no = slot_id
        part_price = price
        if quote_overrides and slot_id in quote_overrides:
            override = quote_overrides[slot_id]
            if "product_no" in override:
                product_no = override["product_no"]
                post.append((f"change_product[{slot_id}]", product_no))
            if "price" in override:
                part_price = override["price"]
            if "ea" in override:
                qty = override["ea"]
        post.append((f"ListProductNo[{slot_id}]", product_no))
        post.append((f"ListProductEa[{slot_id}]", qty))
        post.append((f"ListProductPrice[{slot_id}]", part_price))

    body = urllib.parse.urlencode(post).encode()
    req = urllib.request.Request(
        ESTIMATE_TOKEN_URL,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": BASE_PRODUCT_URL.format(pno=pno),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            token = resp.read().decode("utf-8", errors="replace").strip().strip('"')
            if token and len(token) > 8 and "error" not in token.lower():
                return token
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    return None


def _resolve_product_url(
    pno: int,
    html: str,
    quote_overrides: dict[str, dict[str, str]] | None = None,
) -> str:
    token = _fetch_share_token(pno, html, quote_overrides)
    if token:
        return SHARE_PRODUCT_URL.format(pno=pno, token=token)
    return _parse_canonical_url(html, pno)


def _build_parts(
    bom_key: int | str,
    include_setup: bool = False,
) -> tuple[list[QuotePart], int]:
    """Apple 견적 전용. AI PC/조립PC는 실시간 BOM만 사용하며 하드코딩 FALLBACK 없음."""
    if bom_key not in APPLE_FALLBACK_BOM:
        return [], 0
    raw = APPLE_FALLBACK_BOM[bom_key]

    sorted_raw = sorted(raw, key=lambda x: _sort_key(x[0]))
    parts: list[QuotePart] = []
    subtotal = 0
    for i, row in enumerate(sorted_raw, 1):
        cat, name, price = row[0], row[1], row[2]
        qty = row[3] if len(row) > 3 else 1
        amount = price * qty
        parts.append(
            QuotePart(
                no=i,
                category=cat,
                name=name,
                qty=qty,
                unit_price=price,
                amount=amount,
            )
        )
        subtotal += amount
    if include_setup:
        cat, name, price, qty = _priced_extra(INITIAL_SETUP_ITEM)
        amount = price * qty
        parts.append(
            QuotePart(
                no=len(parts) + 1,
                category=cat,
                name=name,
                qty=qty,
                unit_price=price,
                amount=amount,
            )
        )
        subtotal += amount
    return parts, subtotal


# ---------------------------------------------------------------------------
# 조립PC 카테고리 (프리미엄PC · 아이웍스PC · 추천조립PC) — 실시간 BOM 파싱
# ---------------------------------------------------------------------------

# 컴퓨존 change_type → 견적서 구분 표준화
_CATEGORY_ALIAS = {
    "쿨러": "쿨러/특수냉각",
    "베어본": "베어본",
}


def _fetch_url(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read().decode("euc-kr", errors="replace")


def _clean_product_name(name: str) -> str:
    name = re.sub(r"\s*\(\+[\d,]+원\)\s*$", "", name)
    name = re.sub(r"[★☆].*?[★☆]", "", name)
    name = re.sub(r"^\[컴퓨존\]\s*", "", name)
    return re.sub(r"\s+", " ", name).strip(" -·")


def _clean_part_name(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip()
    name = re.sub(r"\s*\(\+?[\d,]*원\)\s*$", "", name)
    name = re.sub(r"\s*\(\s*[\d,]+\s*원\s*\)\s*$", "", name)
    name = re.sub(r"\|\s*\[베어본\]\s*$", "", name)
    name = re.sub(r"\s*\.\.\.\s*$", "", name)
    return name.strip(" -·|")


def _parse_slot_name(html: str, slot_id: str) -> str | None:
    """기본 선택된 부품명: pdtl_sel_txt 스팬 (def_prm_val == prm_val == slot_id)"""
    m = re.search(
        rf"def_prm_val=\"{slot_id}\"[^>]*prm_val='{slot_id}'[^>]*>\s*([^<]+?)\s*<",
        html,
    )
    if m:
        return _clean_part_name(m.group(1))
    m = re.search(rf"ProductNo={slot_id}[^\"]*\"[^>]*>\s*([^<>]{{5,120}}?)\s*</a>", html)
    if m:
        return _clean_part_name(m.group(1))
    return None


def _is_unavailable_product(html: str) -> bool:
    if not html or len(html) < 500:
        return True
    return "판매 중인 상품이 아닙니다" in html


def _parse_live_bom(
    html: str,
    include_monitor: bool = False,
    include_keyboard: bool = False,
    include_setup: bool = False,
) -> tuple[list[QuotePart], int]:
    """상세페이지 번들 슬롯에서 (구분·품명·단가·수량) BOM 구성"""
    rows: list[tuple[str, str, int, int]] = []
    for slot_id, qty in re.findall(r"DefListProductEa\[(\d+)\]' value=\"(\d+)\"", html):
        pm = re.search(
            rf"id=['\"]ListPrice{slot_id}['\"][^>]*prm_ori=\"(\d+)\"[^>]*change_type=\"([^\"]*)\"",
            html,
        )
        if not pm:
            continue
        price, cat = int(pm.group(1)), pm.group(2).strip()
        cat = _CATEGORY_ALIAS.get(cat, cat)
        name = _parse_slot_name(html, slot_id) or f"부품 (ProductNo {slot_id})"
        rows.append((cat, name, price, int(qty)))

    rows.sort(key=lambda x: _sort_key(x[0]))
    parts: list[QuotePart] = []
    subtotal = 0
    extras = _active_extras(include_monitor, include_keyboard, include_setup)
    for i, (cat, name, price, qty) in enumerate(rows + extras, 1):
        amount = price * qty
        parts.append(
            QuotePart(no=i, category=cat, name=name, qty=qty, unit_price=price, amount=amount)
        )
        subtotal += amount
    return parts, subtotal


def _renumber_parts(parts: list[QuotePart]) -> list[QuotePart]:
    return [
        QuotePart(
            no=i,
            category=p.category,
            name=p.name,
            qty=p.qty,
            unit_price=p.unit_price,
            amount=p.amount,
        )
        for i, p in enumerate(parts, 1)
    ]


def _apply_bundle_discount(
    parts: list[QuotePart],
    total: int,
) -> tuple[list[QuotePart], int]:
    """
    부품 정가(prm_ori) 합과 컴퓨존 판매가(CardPrice 기준 합계) 차이를
    '번들할인' 행으로 넣어 품목 합 = 합계가 되도록 맞춤.
    """
    core = [p for p in parts if p.category != DISCOUNT_CAT]
    parts_sum = sum(p.amount for p in core)
    discount = parts_sum - total
    if discount <= 0 or total <= 0:
        return _renumber_parts(core), parts_sum

    new_parts = _renumber_parts(core)
    new_parts.append(
        QuotePart(
            no=len(new_parts) + 1,
            category=DISCOUNT_CAT,
            name="컴퓨존 조립PC 번들할인",
            qty=1,
            unit_price=-discount,
            amount=-discount,
        )
    )
    return new_parts, total


def _short_code(model: str) -> str:
    """탭 라벨용 짧은 모델 코드 추출"""
    base = model.split("(")[0].strip()
    token = base.split()[-1] if base.split() else base
    code = token.split("_")[-1]
    return code[:14] if code else base[:14]


def fetch_assembled_single(
    cat_label: str,
    tier_key: str,
    pno: int,
    list_name: str,
    include_monitor: bool = False,
    include_keyboard: bool = False,
    include_setup: bool = False,
    description: str | None = None,
) -> Quote:
    now = datetime.now().isoformat(timespec="seconds")
    html = _fetch_html(pno)
    og = re.search(r'property="og:title"\s+content="([^"]+)"', html)
    raw_name = og.group(1).split(" : ")[0] if og else list_name
    model = _clean_product_name(raw_name or list_name)
    parts, subtotal = _parse_live_bom(
        html, include_monitor, include_keyboard, include_setup
    )
    live = _parse_card_price(html)
    extras_total = _extras_total(include_monitor, include_keyboard, include_setup)
    total = (live + extras_total) if live is not None else subtotal
    if live is not None:
        parts, subtotal = _apply_bundle_discount(parts, total)
    url = _resolve_product_url(pno, html, {})

    return Quote(
        tier=cat_label,
        tier_key=tier_key,
        model=model,
        tab_label=_short_code(model),
        description=description or model,
        product_no=pno,
        url=url,
        quote_date=date.today().isoformat(),
        parts=parts,
        parts_subtotal=subtotal,
        total=total,
        price_source="live" if live is not None else "fallback",
        fetched_at=now,
    )


def fetch_assembled_products(cat_key: str) -> list[tuple[int, str]]:
    """카테고리 목록 페이지의 대표(베스트셀러) 상품 목록"""
    cat = ASSEMBLED_CATEGORIES[cat_key]
    html = _fetch_url(cat["list_url"])
    products: list[tuple[int, str]] = []
    seen: set[int] = set()
    for m in re.finditer(
        r'product_detail\.htm\?ProductNo=(\d+)[^"]*bannerid=\w*TopProduct"[^>]*>(.*?)</a>',
        html,
        re.S,
    ):
        pno = int(m.group(1))
        if pno in seen:
            continue
        seen.add(pno)
        nm = re.search(r'<p class="ntRB16[^"]*">\s*([^<]+?)\s*</p>', m.group(2))
        name = _clean_product_name(nm.group(1)) if nm else f"ProductNo {pno}"
        products.append((pno, name))
    return products[:5]


def fetch_assembled_quotes(
    cat_key: str,
    include_monitor: bool = False,
    include_keyboard: bool = False,
    include_setup: bool = False,
) -> TodayQuotesResult:
    if cat_key not in ASSEMBLED_CATEGORIES:
        raise ValueError(f"Unknown category: {cat_key}")
    cat = ASSEMBLED_CATEGORIES[cat_key]
    products = fetch_assembled_products(cat_key)
    if not products:
        raise RuntimeError(f"{cat['label']} 상품 목록을 가져오지 못했습니다.")

    quotes: list[Quote] = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [
            pool.submit(
                fetch_assembled_single,
                cat["label"],
                f"{cat_key}-{i}",
                pno,
                name,
                include_monitor,
                include_keyboard,
                include_setup,
            )
            for i, (pno, name) in enumerate(products, 1)
        ]
        for f in futures:
            try:
                quotes.append(f.result())
            except (urllib.error.URLError, TimeoutError, OSError):
                continue

    return TodayQuotesResult(
        quote_date=date.today().isoformat(),
        event_url=cat["list_url"],
        event_title=cat["label"],
        series=cat["series"],
        quotes=quotes,
        logic_steps=get_logic_steps(),
    )


def _parse_won_text(text: str) -> int:
    digits = re.sub(r"[^\d]", "", str(text or ""))
    return int(digits) if digits else 0


def _fetch_list_html(
    url: str, referer: str, data: dict[str, str] | None = None
) -> str:
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": referer,
        "X-Requested-With": "XMLHttpRequest",
    }
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(
            url.split("?")[0],
            data=urllib.parse.urlencode(data).encode(),
            headers=headers,
            method="POST",
        )
    else:
        req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as resp:
        raw = resp.read()
    for enc in ("euc-kr", "utf-8", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("euc-kr", errors="replace")


def _spec_from_chunk(chunk: str, label: str) -> str:
    matches = list(
        re.finditer(
            rf"<th>\s*{re.escape(label)}\s*</th>\s*<td>(?:<a[^>]*>)?\s*([^<]+)",
            chunk,
            re.I,
        )
    )
    return _clean_part_name(matches[-1].group(1)) if matches else ""


def _parse_assembled_list_html(html: str, source_label: str) -> list[dict[str, Any]]:
    """목록 AJAX HTML에서 ProductNo·판매가·사양을 수집한다."""
    items: list[dict[str, Any]] = []
    seen: set[int] = set()
    for m in re.finditer(
        r'data-pricetable="(\d+)"[^>]*data-price="([^"]*)"[^>]*data-discountprice="([^"]*)"',
        html,
        re.I,
    ):
        try:
            pno = int(m.group(1))
        except (TypeError, ValueError):
            continue
        if pno < 100000 or pno in seen:
            continue
        list_price = _parse_won_text(m.group(2))
        sale_price = _parse_won_text(m.group(3))
        price = sale_price if sale_price > 0 else list_price
        if price <= 0:
            continue
        seen.add(pno)
        chunk = html[max(0, m.start() - 4500) : m.start()]
        names = list(re.finditer(r'<p class="name">\s*([^<]+)', chunk))
        name = _clean_product_name(names[-1].group(1)) if names else f"ProductNo {pno}"
        cpu = _spec_from_chunk(chunk, "CPU")
        vga = _spec_from_chunk(chunk, "VGA")
        spec = " / ".join(x for x in (cpu, vga) if x)
        items.append(
            {
                "pno": pno,
                "name": name,
                "price": price,
                "cpu": cpu,
                "vga": vga,
                "spec": spec,
                "source": source_label,
            }
        )
    return items


def _tot_count_from_list_html(html: str) -> int:
    m = re.search(r'id=["\']TotCount["\'][^>]*value=["\'](\d+)', html, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r'value=["\'](\d+)["\'][^>]*id=["\']TotCount["\']', html, re.I)
    return int(m.group(1)) if m else 0


def _fetch_price_source(src: dict[str, str]) -> list[dict[str, Any]]:
    """한 조립PC 라인업 목록을 페이지 단위로 수집한다."""
    items: list[dict[str, Any]] = []
    seen: set[int] = set()
    page_size = 60
    actype = src.get("actype") or "recom_search_list_2025"
    referer = src.get("referer") or src["list_url"].replace(".php", ".htm")
    for page in range(1, 8):
        start = (page - 1) * page_size
        params = {
            "actype": actype,
            "ViewType": "L",
            "PageCount": str(page_size),
            "StartNum": str(start),
            "PageNum": str(page),
            "recom_orderby": "low_price",
            "InputPage": src.get("input_page") or "",
            "SoldOutExcept": "Y",
        }
        url = f"{src['list_url']}?{urllib.parse.urlencode(params)}"
        html = ""
        try:
            html = _fetch_list_html(url, referer)
        except (urllib.error.URLError, TimeoutError, OSError):
            html = ""
        batch = _parse_assembled_list_html(html, src["label"]) if html else []
        if not batch:
            try:
                html = _fetch_list_html(src["list_url"], referer, params)
            except (urllib.error.URLError, TimeoutError, OSError):
                break
            batch = _parse_assembled_list_html(html, src["label"])
        if not batch:
            break
        added = 0
        for row in batch:
            if row["pno"] in seen:
                continue
            seen.add(row["pno"])
            items.append(row)
            added += 1
        tot = _tot_count_from_list_html(html)
        if added == 0:
            break
        if tot and start + page_size >= tot:
            break
        if len(batch) < 8:
            break
    return items


def fetch_assembled_price_catalog() -> list[dict[str, Any]]:
    """추천·아이웍스·프리미엄 조립PC를 모아 판매가 목록을 만든다."""
    catalog: list[dict[str, Any]] = []
    seen: set[int] = set()
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(_fetch_price_source, src) for src in ASSEMBLED_PRICE_SOURCES]
        for f in futures:
            try:
                rows = f.result()
            except (urllib.error.URLError, TimeoutError, OSError):
                continue
            for row in rows:
                if row["pno"] in seen:
                    continue
                seen.add(row["pno"])
                catalog.append(row)
    catalog.sort(key=lambda r: (int(r["price"]), int(r["pno"])))
    return catalog


def fetch_assembled_quotes_by_price(
    min_price: int,
    max_price: int,
    include_monitor: bool = False,
    include_keyboard: bool = False,
    include_setup: bool = False,
    limit: int = PRICE_SEARCH_LIMIT,
) -> tuple[TodayQuotesResult, dict[str, Any]]:
    """최저가~최고가 사이 조립PC를 금액 낮은 순으로 상위 N건 견적한다."""
    try:
        lo = int(min_price)
        hi = int(max_price)
    except (TypeError, ValueError) as e:
        raise ValueError("최저가와 최고가는 숫자로 입력해주세요.") from e
    if lo < 0 or hi < 0:
        raise ValueError("금액은 0원 이상이어야 합니다.")
    if hi < lo:
        raise ValueError("최고가는 최저가보다 크거나 같아야 합니다.")
    if hi == 0:
        raise ValueError("최고가를 입력해주세요.")

    cap = max(1, min(int(limit or PRICE_SEARCH_LIMIT), PRICE_SEARCH_LIMIT))
    catalog = fetch_assembled_price_catalog()
    if not catalog:
        raise RuntimeError("컴퓨존 조립PC 목록을 가져오지 못했습니다.")

    matched = [row for row in catalog if lo <= int(row["price"]) <= hi]
    matched.sort(key=lambda r: (int(r["price"]), int(r["pno"])))
    top = matched[:cap]
    if not top:
        raise RuntimeError(
            f"{lo:,}원 ~ {hi:,}원 구간의 조립PC가 없습니다. 금액대를 바꿔 보세요."
        )

    quotes: list[Quote] = []
    with ThreadPoolExecutor(max_workers=min(10, len(top))) as pool:
        futures = [
            pool.submit(
                fetch_assembled_single,
                "금액대검색",
                f"price-{i}",
                int(row["pno"]),
                row["name"],
                include_monitor,
                include_keyboard,
                include_setup,
                (
                    f"{row['source']} · {lo:,}~{hi:,}원"
                    + (f" · {row['spec']}" if row.get("spec") else "")
                ),
            )
            for i, row in enumerate(top, 1)
        ]
        for i, f in enumerate(futures, 1):
            try:
                q = f.result()
            except (urllib.error.URLError, TimeoutError, OSError):
                continue
            quotes.append(
                replace(
                    q,
                    tab_label=f"{i}위",
                    tier="금액대검색",
                    tier_key=f"price-{i}",
                )
            )

    if not quotes:
        raise RuntimeError("조건에 맞는 조립PC 견적을 수집하지 못했습니다.")

    result = TodayQuotesResult(
        quote_date=date.today().isoformat(),
        event_url=ALL_ASSEMBLED_URL,
        event_title="금액대로 찾기",
        series=f"금액대로 찾기 · {lo:,}원 ~ {hi:,}원",
        quotes=quotes,
        logic_steps=get_logic_steps(),
    )
    meta = {
        "min_price": lo,
        "max_price": hi,
        "matched_count": len(matched),
        "limit": cap,
        "catalog_count": len(catalog),
    }
    return result, meta


def _is_ai_pc_title(title: str) -> bool:
    if not title:
        return False
    upper = title.upper()
    if "AI PC" in title or "AMD AI" in upper:
        return True
    if "아이웍스X-020" in title or "아이웍스X-02" in title:
        return True
    if "아이웍스X" in title and any(k in upper for k in ("4545P", "4585PX", "EPYC")):
        return True
    return False


def _classify_ai_tier(model: str, index: int) -> tuple[str, str, str]:
    """모델명 기반 티어 분류 → (tier, tier_key, tab_label)"""
    compact = re.sub(r"\s+", "", model.upper())
    if "5090" in compact:
        return "전문가형", "expert-5090", "전문가형 · 5090"
    if "5080" in compact:
        return "전문가형", "expert-5080", "전문가형 · 5080"
    if re.search(r"5060\s*TI\s*X\s*2|5060TIX2", compact) or re.search(
        r"5060\s*Ti\s*x\s*2", model, re.I
    ):
        return "실무형", "pro", "실무형"
    if "내장" in model or "미니PC" in model:
        return "입문형", "entry", "입문형"
    if "5060" in compact:
        return "표준형", "standard", "표준형"
    code = _short_code(model)
    return "AI PC", f"ai-{index}", code or f"AI-{index}"


def discover_ai_pc_product_nos() -> list[int]:
    """기획전·조립PC 메인관에서 AI PC ProductNo 를 실시간 수집"""
    found: list[int] = []
    seen: set[int] = set()

    # 1) AI 기획전 페이지 링크
    try:
        html = _fetch_url(EVENT_DESKTOP_URL)
        for m in re.finditer(r"ProductNo=(\d+)", html):
            pno = int(m.group(1))
            if pno >= 100000 and pno not in seen:
                seen.add(pno)
                found.append(pno)
    except (urllib.error.URLError, TimeoutError, OSError):
        pass

    # 2) 조립PC 메인관 — AI/아이웍스X-02 문맥의 상품만
    try:
        html = _fetch_url("https://www.compuzone.co.kr/product/Allassembled.htm")
        for m in re.finditer(r"ProductNo=(\d+)", html):
            pno = int(m.group(1))
            if pno < 100000 or pno in seen:
                continue
            ctx = html[max(0, m.start() - 280) : m.start() + 1200]
            if any(
                k in ctx
                for k in ("AMD AI", "AI PC", "아이웍스X-02", "4545P", "4585PX")
            ):
                seen.add(pno)
                found.append(pno)
    except (urllib.error.URLError, TimeoutError, OSError):
        pass

    # 3) 시드(기획전 파싱 실패 대비) — 판매종료면 이후 단계에서 스킵
    for pno in AI_PC_SEED_PNOS:
        if pno not in seen:
            found.append(pno)
            seen.add(pno)
    return found


def _memory_slot_ids(html: str) -> list[str]:
    """상세페이지에서 메모리 슬롯 ProductNo 목록"""
    ids: list[str] = []
    for slot_id, _qty in re.findall(r"DefListProductEa\[(\d+)\]' value=\"(\d+)\"", html):
        pm = re.search(
            rf"id=['\"]ListPrice{slot_id}['\"][^>]*change_type=\"([^\"]*)\"",
            html,
        )
        if pm and pm.group(1).strip() == "메모리":
            ids.append(slot_id)
    return ids


def _apply_ram32_upgrade(
    parts: list[QuotePart], html: str
) -> tuple[list[QuotePart], int, dict[str, dict[str, str]]]:
    """
    기본 1개(보통 16GB) 메모리를 2개(32GB)로 확장.
    이미 qty>=2 이면 변경 없음.
    반환: (parts, 추가금액, share token overrides)
    """
    overrides: dict[str, dict[str, str]] = {}
    ram_delta = 0
    new_parts: list[QuotePart] = []
    upgraded = False

    for p in parts:
        if not upgraded and p.category == "메모리" and p.qty == 1 and p.unit_price > 0:
            new_qty = 2
            new_amount = p.unit_price * new_qty
            ram_delta = p.unit_price  # 1개 추가분
            name = p.name
            if "32GB" not in name and "×2" not in name and "x2" not in name.lower():
                name = f"{name} — 32GB 구성(×2)"
            new_parts.append(
                QuotePart(
                    no=p.no,
                    category=p.category,
                    name=name,
                    qty=new_qty,
                    unit_price=p.unit_price,
                    amount=new_amount,
                )
            )
            upgraded = True
        else:
            new_parts.append(p)

    if upgraded:
        for slot_id in _memory_slot_ids(html):
            overrides[slot_id] = {"ea": "2"}

    # 번호 재정렬
    new_parts = [
        QuotePart(
            no=i,
            category=p.category,
            name=p.name,
            qty=p.qty,
            unit_price=p.unit_price,
            amount=p.amount,
        )
        for i, p in enumerate(new_parts, 1)
    ]
    return new_parts, ram_delta, overrides


def fetch_ai_pc_live_quote(
    pno: int,
    include_monitor: bool = False,
    include_keyboard: bool = False,
    include_ram32: bool = False,
    include_setup: bool = False,
    index: int = 1,
) -> Quote:
    """하드코딩 BOM 없이 상품 상세 실시간 데이터만으로 AI PC 견적 생성"""
    html = _fetch_html(pno)
    if _is_unavailable_product(html):
        raise RuntimeError(f"판매종료 상품: ProductNo={pno}")

    og = re.search(r'property="og:title"\s+content="([^"]+)"', html)
    if not og:
        raise RuntimeError(f"상품명을 찾을 수 없습니다: ProductNo={pno}")
    model = _clean_product_name(og.group(1).split(" : ")[0])
    if not _is_ai_pc_title(model):
        raise RuntimeError(f"AI PC 상품이 아닙니다: {model}")

    parts, subtotal = _parse_live_bom(
        html, include_monitor, include_keyboard, include_setup
    )
    # 슬롯 BOM이 비면 이전 fallback 을 쓰지 않고 실패 처리
    core_parts = [
        p
        for p in parts
        if p.category not in ("모니터", "키보드마우스", SETUP_EXTRA_CAT, "기술지원")
    ]
    if not core_parts:
        raise RuntimeError(f"실시간 BOM 없음: ProductNo={pno}")

    live = _parse_card_price(html)
    if live is None:
        raise RuntimeError(f"실시간 판매가 없음: ProductNo={pno}")

    # 기본(OFF): 컴퓨존 기본 슬롯 수량 그대로(보통 16GB ×1). 과거 하드코딩 32GB(×2) 강제 금지.
    ram_delta = 0
    quote_overrides: dict[str, dict[str, str]] = {}
    description = model
    if include_ram32:
        parts, ram_delta, quote_overrides = _apply_ram32_upgrade(parts, html)
        subtotal = sum(p.amount for p in parts)
        if ram_delta > 0:
            description = f"{model} · 메모리 32GB 확장"
    else:
        # 견적서에서 기본 구성임을 명확히 표시
        clarified: list[QuotePart] = []
        for p in parts:
            if (
                p.category == "메모리"
                and p.qty == 1
                and "×1" not in p.name
                and "x1" not in p.name.lower()
            ):
                clarified.append(
                    QuotePart(
                        no=p.no,
                        category=p.category,
                        name=f"{p.name} · 기본(×1)",
                        qty=p.qty,
                        unit_price=p.unit_price,
                        amount=p.amount,
                    )
                )
            else:
                clarified.append(p)
        parts = clarified

    extras_total = _extras_total(include_monitor, include_keyboard, include_setup)
    total = live + ram_delta + extras_total
    parts, subtotal = _apply_bundle_discount(parts, total)
    tier, tier_key, tab_label = _classify_ai_tier(model, index)
    url = _resolve_product_url(pno, html, quote_overrides)

    return Quote(
        tier=tier,
        tier_key=tier_key,
        model=model,
        tab_label=tab_label,
        description=description,
        product_no=pno,
        url=url,
        quote_date=date.today().isoformat(),
        parts=parts,
        parts_subtotal=subtotal,
        total=total,
        price_source="live",
        fetched_at=datetime.now().isoformat(timespec="seconds"),
    )


def fetch_single_quote(
    product: dict[str, Any],
    include_monitor: bool = False,
    include_keyboard: bool = False,
    include_ram32: bool = False,
    include_setup: bool = False,
) -> Quote:
    """하위 호환: 설정 딕셔너리의 pno 로 실시간 AI PC 견적 생성"""
    return fetch_ai_pc_live_quote(
        int(product["pno"]),
        include_monitor=include_monitor,
        include_keyboard=include_keyboard,
        include_ram32=include_ram32,
        include_setup=include_setup,
    )


def fetch_game_pc_meta() -> dict[str, Any]:
    """컴퓨존 '내 게임PC 찾기' 페이지에서 게임 목록을 수집"""
    html = _fetch_url(GAMING_PC_URL)
    games: list[dict[str, str]] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'<li class="game_item_li game_item_li_(\d+)">\s*'
        r"<a href=\"javascript:setGameItem\('(\d+)','([^']+)'\)\">\s*"
        r'<span class="gm_thum">\s*<img[^>]+src="([^"]+)"',
        html,
    ):
        gid = m.group(1)
        if gid in seen:
            continue
        seen.add(gid)
        img = m.group(4)
        if img.startswith("//"):
            img = "https:" + img
        games.append({"id": gid, "title": m.group(3), "image": img})
    if not games:
        raise RuntimeError("게임 목록을 가져오지 못했습니다.")
    return {
        "page_url": GAMING_PC_URL,
        "max_games": 4,
        "resolutions": GAMING_RESOLUTIONS,
        "games": games,
    }


def _post_game_list(game_ids: list[str], resolution: str) -> dict[str, Any]:
    body = urllib.parse.urlencode(
        {
            "actype": "getGameList",
            "resolution": resolution,
            "game": "|".join(game_ids),
        }
    ).encode()
    req = urllib.request.Request(
        GAMING_PC_API_URL,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": GAMING_PC_URL,
            "X-Requested-With": "XMLHttpRequest",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    for enc in ("utf-8", "euc-kr"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    data = json.loads(text) if text.strip() else {}
    if isinstance(data, list):
        return {}
    if not isinstance(data, dict):
        raise RuntimeError("게임PC 추천 응답 형식이 올바르지 않습니다.")
    return data


def fetch_game_pc_quotes(
    game_ids: list[str],
    resolution: str,
    include_monitor: bool = False,
    include_keyboard: bool = False,
    include_setup: bool = False,
    game_titles: list[str] | None = None,
) -> TodayQuotesResult:
    """게임·해상도 선택 후 컴퓨존 추천 PC를 견적서로 변환"""
    ids = [str(g).strip() for g in game_ids if str(g).strip()]
    if not ids:
        raise ValueError("게임을 1개 이상 선택해주세요.")
    if len(ids) > 4:
        raise ValueError("게임은 최대 4개까지 선택 가능합니다.")
    resolution = (resolution or "").strip().upper()
    if resolution not in {r["id"] for r in GAMING_RESOLUTIONS}:
        raise ValueError("해상도를 선택해주세요.")

    data = _post_game_list(ids, resolution)
    if not data or (not data.get("S") and not data.get("P")):
        raise RuntimeError("선택한 조건에 맞는 추천 PC가 없습니다.")

    targets: list[tuple[str, str, int, str, str]] = []
    for group_key, label in (("S", "표준 추천"), ("P", "프리미엄 추천")):
        group = data.get(group_key) or {}
        if not isinstance(group, dict):
            continue
        for i, (pno_key, row) in enumerate(group.items(), 1):
            try:
                pno = int(row.get("ProductNo") or pno_key)
            except (TypeError, ValueError):
                continue
            name = row.get("AssembleSummary") or f"ProductNo {pno}"
            part = row.get("Part") or {}
            cpu = part.get("CPU") or ""
            vga = part.get("VGA") or ""
            spec = " / ".join(x for x in (cpu, vga) if x)
            desc = f"{label} · {resolution}" + (f" · {spec}" if spec else "")
            targets.append((label, f"game-{group_key.lower()}-{i}", pno, name, desc))

    if not targets:
        raise RuntimeError("선택한 조건에 맞는 추천 PC가 없습니다.")

    quotes: list[Quote] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [
            pool.submit(
                fetch_assembled_single,
                label,
                tier_key,
                pno,
                name,
                include_monitor,
                include_keyboard,
                include_setup,
                desc,
            )
            for label, tier_key, pno, name, desc in targets[:10]
        ]
        for f in futures:
            try:
                quotes.append(f.result())
            except (urllib.error.URLError, TimeoutError, OSError):
                continue

    if not quotes:
        raise RuntimeError("추천 PC 견적을 수집하지 못했습니다.")

    titles = [t for t in (game_titles or []) if t]
    title_part = ", ".join(titles[:4]) if titles else "|".join(ids)
    return TodayQuotesResult(
        quote_date=date.today().isoformat(),
        event_url=GAMING_PC_URL,
        event_title=f"내 게임PC 찾기 ({resolution})",
        series=f"게임으로PC찾기 · {title_part} · {resolution}",
        quotes=quotes,
        logic_steps=get_logic_steps(),
    )


def get_logic_steps() -> list[dict[str, str]]:
    return [
        {
            "step": "01",
            "title": "기획전 매핑",
            "desc": f"EventNo={EVENT_NO} 기획전·조립PC 메인관에서 AI PC ProductNo 실시간 수집",
        },
        {
            "step": "02",
            "title": "페이지 수집",
            "desc": "각 상품 상세 HTML 요청 (EUC-KR) — 판매종료 상품 자동 제외",
        },
        {
            "step": "03",
            "title": "실시간 파싱",
            "desc": "og:title 상품명 + 번들 BOM 슬롯 + CardPrice 판매가 + 번들할인 행으로 품목합=합계 맞춤",
        },
        {
            "step": "04",
            "title": "티어 분류",
            "desc": "모델명(입문/실무/5080 등) 기준으로 탭 라벨 자동 분류",
        },
        {
            "step": "05",
            "title": "견적서 출력",
            "desc": "컴퓨존 표준 구분 순서 정렬 → 웹/Excel 견적서 생성",
        },
    ]


def fetch_today_quotes(
    tier_key: str | None = None,
    include_monitor: bool = False,
    include_keyboard: bool = False,
    include_ram32: bool = False,
    include_setup: bool = False,
) -> TodayQuotesResult:
    """AI PC: 기획전에서 상품번호를 실시간 수집 후, 각 상세페이지 BOM/가격만 사용"""
    pnos = discover_ai_pc_product_nos()
    quotes: list[Quote] = []

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            pool.submit(
                fetch_ai_pc_live_quote,
                pno,
                include_monitor,
                include_keyboard,
                include_ram32,
                include_setup,
                idx,
            ): pno
            for idx, pno in enumerate(pnos, 1)
        }
        for fut in futures:
            try:
                quotes.append(fut.result())
            except (RuntimeError, urllib.error.URLError, TimeoutError, OSError):
                continue

    # 가격순 정렬 후 티어 키 재부여(표시 일관성)
    quotes.sort(key=lambda q: (q.total, q.product_no))
    for i, q in enumerate(quotes, 1):
        tier, tkey, tab = _classify_ai_tier(q.model, i)
        q.tier = tier
        q.tier_key = tkey
        q.tab_label = tab

    if tier_key:
        quotes = [q for q in quotes if q.tier_key == tier_key]
        if not quotes:
            raise ValueError(f"Unknown tier: {tier_key}")

    if not quotes:
        raise RuntimeError("판매 중인 AI PC 상품 견적을 수집하지 못했습니다.")

    return TodayQuotesResult(
        quote_date=date.today().isoformat(),
        event_url=EVENT_DESKTOP_URL or EVENT_URL,
        event_title=EVENT_TITLE,
        series=SERIES_NAME,
        quotes=quotes,
        logic_steps=get_logic_steps(),
    )


def quote_to_dict(quote: Quote) -> dict[str, Any]:
    d = asdict(quote)
    return d


def today_quotes_to_dict(result: TodayQuotesResult) -> dict[str, Any]:
    data = {
        "quote_date": result.quote_date,
        "event_url": result.event_url,
        "event_title": result.event_title,
        "series": result.series,
        "disclaimer": QUOTE_DISCLAIMER,
        "logic_steps": result.logic_steps,
        "engine": result.engine or "live-bom-v2",
        "quotes": [quote_to_dict(q) for q in result.quotes],
    }
    if result.catalog_synced_at:
        data["catalog_synced_at"] = result.catalog_synced_at
    return data


def _build_apple_catalog_parts(
    item: dict[str, Any],
    include_setup: bool = False,
) -> tuple[list[QuotePart], int]:
    """공식몰에서 가져온 대표 구성으로 사양 행을 만든다. 합계는 라이브 가격과 같게 맞춘다."""
    specs = item.get("specs") or {}
    total = int(item.get("price") or 0)
    rows: list[tuple[str, str, int]] = []
    chip = specs.get("chip_label") or item.get("chip") or ""
    if chip:
        rows.append(("CPU/SoC", f"[Apple] {chip}", total))
    memory = specs.get("memory") or ""
    if memory:
        rows.append(("메모리", f"[Apple] {memory} 통합 메모리", 0))
    storage = specs.get("storage") or ""
    if storage:
        rows.append(("SSD", f"[Apple] {storage}", 0))
    display = specs.get("display") or ""
    if display:
        rows.append(("디스플레이", f"[Apple] {display}", 0))
    if not rows:
        rows.append(("본체", item.get("model") or "Apple 정품", total))

    sorted_raw = sorted(rows, key=lambda x: _sort_key(x[0]))
    parts: list[QuotePart] = []
    subtotal = 0
    for i, (cat, name, price) in enumerate(sorted_raw, 1):
        parts.append(
            QuotePart(
                no=i,
                category=cat,
                name=name,
                qty=1,
                unit_price=price,
                amount=price,
            )
        )
        subtotal += price
    if include_setup:
        cat, name, price, qty = _priced_extra(INITIAL_SETUP_ITEM)
        amount = price * qty
        parts.append(
            QuotePart(
                no=len(parts) + 1,
                category=cat,
                name=name,
                qty=qty,
                unit_price=price,
                amount=amount,
            )
        )
        subtotal += amount
    return parts, subtotal


def fetch_apple_quotes(
    cat_key: str = "macbook",
    include_monitor: bool = False,
    include_setup: bool = False,
    force_refresh: bool = False,
) -> TodayQuotesResult:
    if cat_key not in APPLE_CATEGORIES and cat_key != "all":
        raise ValueError(f"Unknown Apple category: {cat_key}")

    if cat_key == "all":
        series_label = "Apple 전제품 정품 라인업"
        title_label = "Apple Store 전제품 견적"
        event_url = "https://www.apple.com/kr/shop/buy-mac"
    else:
        cat_info = APPLE_CATEGORIES[cat_key]
        series_label = cat_info["series"]
        title_label = f"Apple Store {cat_info['label']}"
        event_url = "https://www.apple.com/kr/shop/buy-mac"

    from app.apple_scraper import fetch_apple_catalog

    del include_monitor  # 컴퓨존 모니터 옵션은 Apple 견적에 쓰지 않는다
    items, meta = fetch_apple_catalog(cat_key, force_refresh=force_refresh)
    if not items:
        raise RuntimeError("Apple 공식몰에서 현재 판매 중인 제품을 가져오지 못했습니다.")

    synced_at = str(meta.get("synced_at") or datetime.now().isoformat(timespec="seconds"))
    now = datetime.now().isoformat(timespec="seconds")
    quotes: list[Quote] = []
    for item in items:
        add_setup = bool(include_setup) and is_mac_tier(item.get("tier_key", ""))
        parts, subtotal = _build_apple_catalog_parts(item, include_setup=add_setup)
        total = int(item.get("price") or 0)
        if add_setup:
            _cat, _name, setup_price, setup_qty = _priced_extra(INITIAL_SETUP_ITEM)
            total += setup_price * setup_qty
        source = "apple_official"
        if meta.get("from_snapshot"):
            source = "apple_official_snapshot"
        quotes.append(
            Quote(
                tier=item.get("tier") or item.get("tab_label") or "Apple",
                tier_key=item["tier_key"],
                model=item["model"],
                tab_label=item.get("tab_label") or item.get("tier") or item["model"],
                description=item.get("desc") or item["model"],
                product_no=int(item.get("pno") or 900000),
                url=item.get("url") or event_url,
                quote_date=date.today().isoformat(),
                parts=parts,
                parts_subtotal=subtotal,
                total=total,
                price_source=source,
                fetched_at=now,
                cto_groups=list(item.get("cto_groups") or []),
            )
        )

    apple_steps = [
        {
            "step": "01",
            "title": "공식몰 패밀리 동기화",
            "desc": "Apple Store Korea 구매 허브에서 현재 판매 중인 패밀리 페이지를 수집한다.",
        },
        {
            "step": "02",
            "title": "대표 구성 선택",
            "desc": "색상·나노텍스처 조합을 접고, 패밀리·화면 크기·칩별 기본 구성(최저 공식가)만 카드로 남긴다.",
        },
        {
            "step": "03",
            "title": "단종/신제품 반영",
            "desc": "구매 페이지에 없는 모델은 목록에서 제외하고, 새로 생긴 패밀리는 자동으로 추가한다.",
        },
        {
            "step": "04",
            "title": "공식 시작가 적용",
            "desc": f"카탈로그 동기화 시각: {synced_at}"
            + (" (직전 성공 스냅샷)" if meta.get("from_snapshot") else ""),
        },
        {
            "step": "05",
            "title": "라이브 CTO 옵션",
            "desc": "공식몰 가격표에서 저장장치·프로세서·셀룰러 등 업그레이드 차액을 카드별로 동기화한다.",
        },
    ]

    return TodayQuotesResult(
        quote_date=date.today().isoformat(),
        event_url=event_url,
        event_title=title_label,
        series=series_label,
        quotes=quotes,
        logic_steps=apple_steps,
        catalog_synced_at=synced_at,
        engine="apple-live-catalog",
    )

