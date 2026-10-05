# -*- coding: utf-8 -*-
"""컴퓨존 AI PC 견적 서비스 설정"""

EVENT_URL = "https://m.compuzone.co.kr/s_event/event_detail.htm?EventNo=60921"
EVENT_DESKTOP_URL = "https://www.compuzone.co.kr/event_zone/eventzone_view.htm?EventNo=60921"
EVENT_NO = 60921
EVENT_TITLE = "AI 데스크탑 이해와 선택"
SERIES_NAME = "컴퓨존 아이웍스X (AMD AI PC)"
# 기획전 파싱 실패 시에만 사용하는 시드(판매종료면 자동 스킵)
AI_PC_SEED_PNOS = [1339664, 1339673, 1339674]
BASE_PRODUCT_URL = "https://www.compuzone.co.kr/product/product_detail.htm?ProductNo={pno}"
SHARE_PRODUCT_URL = (
    "https://compuzone.co.kr/product/product_detail.htm?ProductNo={pno}&token={token}"
)
ESTIMATE_TOKEN_URL = "https://www.compuzone.co.kr/pop_page/estimate_function.php"
QUOTE_DISCLAIMER = "※ 윈도우, 오피스 별도 · 기술지원비 별도청구"

# 견적폼 공급자 정보
SUPPLIER = {
    "name": "대한민국주식회사",
    "manager": "홍길동대표",
    "address": "서울시",
    "phone": "010-1234-5678",
    "email": "email1234@gmail.com",
}

# 컴퓨존 메인 > 조립PC 카테고리 (목록 페이지에서 대표 상품을 실시간 수집)
ASSEMBLED_CATEGORIES = {
    "premium": {
        "label": "프리미엄PC",
        "list_url": "https://www.compuzone.co.kr/product/compuzone_premium_pc.htm",
        "series": "컴퓨존 프리미엄PC",
    },
    "iworks": {
        "label": "아이웍스PC",
        "list_url": "https://www.compuzone.co.kr/product/iworks_list.htm",
        "series": "컴퓨존 아이웍스PC",
    },
    "recommend": {
        "label": "추천조립PC",
        "list_url": "https://www.compuzone.co.kr/product/recommend_list.htm",
        "series": "컴퓨존 추천조립PC",
    },
}

# 금액대로 찾기: 컴퓨존 조립PC 전체 목록(추천·아이웍스·프리미엄)
ALL_ASSEMBLED_URL = "https://www.compuzone.co.kr/product/Allassembled.htm"
PRICE_SEARCH_LIMIT = 10
ASSEMBLED_PRICE_SOURCES = [
    {
        "key": "recommend",
        "label": "추천조립PC",
        "list_url": "https://www.compuzone.co.kr/product/recommend_list.php",
        "referer": "https://www.compuzone.co.kr/product/recommend_list.htm",
        "input_page": "recompc",
        "actype": "recom_search_list_2025",
    },
    {
        "key": "iworks",
        "label": "아이웍스PC",
        "list_url": "https://www.compuzone.co.kr/product/iworks_list.php",
        "referer": "https://www.compuzone.co.kr/product/iworks_list.htm",
        "input_page": "recompc",
        "actype": "recom_search_list_2021",
    },
    {
        "key": "premium",
        "label": "프리미엄PC",
        "list_url": "https://www.compuzone.co.kr/product/compuzone_premium_pc.php",
        "referer": "https://www.compuzone.co.kr/product/compuzone_premium_pc.htm",
        "input_page": "compuzonepremiumpc",
        "actype": "compuzone_premium_pc_list_2025",
    },
]

# 컴퓨존 조립PC > 내 게임PC 찾기
GAMING_PC_URL = "https://www.compuzone.co.kr/exc_event/gaming_pc.htm"
GAMING_PC_API_URL = "https://www.compuzone.co.kr/exc_event/gamezone_function.php"
GAMING_RESOLUTIONS = [
    {"id": "FHD", "label": "FHD (1920×1080)"},
    {"id": "QHD", "label": "QHD (2560×1440)"},
    {"id": "UHD", "label": "UHD / 4K (3840×2160)"},
]

# 모든 견적서 공통 추가 항목: (구분, 품명, 단가, 수량)
# 수량 0 = 금액 미표기(별도청구) 항목
# 모니터·키보드·초기 세팅비는 포함/제외 토글. 기술지원은 항상 표시.
EXTRA_ITEMS = [
    ("모니터", "LG PC 모니터 27U411A 68cm(27인치)", 180000, 1),
    ("키보드마우스", "무선키보드마우스", 30000, 1),
    ("초기 세팅비", "초기 세팅비", 0, 0),
    ("기술지원", "AI PC 구축용 앱 설치와 설정 테스트비용 ____별도청구", 0, 0),
]
SETUP_EXTRA_CAT = "초기 세팅비"
INITIAL_SETUP_ITEM = ("초기 세팅비", "초기 세팅비", 0, 0)
MAC_TIER_PREFIXES = ("macbook", "imac", "macmini", "macstudio")
MAC_APPLE_CATS = frozenset({"macbook", "imac", "macmini", "macstudio", "all"})

# 컴퓨존 소비자견적: 총액의 30% 마진이 기본. 마진은 부품 개별단가에도 배분해 표시한다.
CONSUMER_MARGIN_PERCENT = 30
MARGIN_BUTTONS = (25, 20, 15, 10, 0)

# AI PC 견적은 PRODUCTS/FALLBACK BOM 하드코딩을 사용하지 않는다.
# discover_ai_pc_product_nos() + 상품 상세 실시간 BOM/CardPrice 만 사용.

CATEGORY_ORDER = [
    "CPU",
    "베어본",
    "메인보드",
    "메모리",
    "그래픽카드",
    "SSD",
    "HDD",
    "케이스",
    "파워",
    "쿨러/특수냉각",
    "소프트웨어",
    "조립비",
    "모니터",
    "키보드마우스",
    "초기 세팅비",
    "할인",
    "기타",
]

# ---------------------------------------------------------------------------
# 애플스토어 (Apple Store) 카테고리 및 제품군 설정
# ---------------------------------------------------------------------------
APPLE_CATEGORIES = {
    "macbook": {
        "label": "MacBook (맥북)",
        "series": "Apple MacBook (Air / Pro) 정품 견적",
    },
    "imac": {
        "label": "iMac (아이맥)",
        "series": "Apple iMac 24인치 정품 견적",
    },
    "macmini": {
        "label": "Mac mini (맥미니)",
        "series": "Apple Mac mini 콤팩트 데스크탑 정품 견적",
    },
    "macstudio": {
        "label": "Mac Studio (맥스튜디오)",
        "series": "Apple Mac Studio 고성능 워크스테이션 정품 견적",
    },
    "ipad_iphone": {
        "label": "iPad & iPhone",
        "series": "Apple iPad / iPhone 정품 견적",
    },
    "accessories": {
        "label": "Apple 주변기기 & Care+",
        "series": "Apple 정품 디스플레이, 키보드/마우스 & AppleCare+",
    },
}

# 카드 목록의 출처는 하드코딩 모델이 아니라 공식 구매 허브/패밀리 페이지.
# include_prefixes 만 고정하고, 그 안의 현재 판매 SKU 는 매번 동기화한다.
APPLE_CATALOG_SOURCES = {
    "macbook": {
        "hubs": ["https://www.apple.com/kr/shop/buy-mac"],
        "kinds": ("mac",),
        "include_prefixes": ("macbook-",),
    },
    "imac": {
        "hubs": ["https://www.apple.com/kr/shop/buy-mac"],
        "kinds": ("mac",),
        "include_prefixes": ("imac",),
    },
    "macmini": {
        "hubs": ["https://www.apple.com/kr/shop/buy-mac"],
        "kinds": ("mac",),
        "include_prefixes": ("mac-mini",),
    },
    "macstudio": {
        "hubs": ["https://www.apple.com/kr/shop/buy-mac"],
        "kinds": ("mac",),
        "include_prefixes": ("mac-studio",),
    },
    "ipad_iphone": {
        "hubs": [
            "https://www.apple.com/kr/shop/buy-ipad",
            "https://www.apple.com/kr/shop/buy-iphone",
        ],
        "kinds": ("ipad", "iphone"),
        "include_prefixes": ("ipad", "iphone-"),
        "exclude_slugs": ("carrier-offers",),
    },
    "accessories": {
        "hubs": ["https://www.apple.com/kr/shop/buy-mac"],
        "kinds": ("mac",),
        "include_prefixes": ("studio-display",),
    },
}

# 레거시 고정 모델. 리스트 카드는 APPLE_CATALOG_SOURCES 의 공식몰 동기화 결과를 쓴다.
# 레거시 호환용 빈 딕셔너리 (공식몰 실시간 크롤링으로 완전 대체됨)
APPLE_PRODUCTS: dict[str, list[dict]] = {}
APPLE_FALLBACK_BOM: dict[str, list[tuple]] = {}
APPLE_CTO_OPTIONS: dict[str, list[dict]] = {}
