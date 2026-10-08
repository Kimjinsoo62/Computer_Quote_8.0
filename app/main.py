# -*- coding: utf-8 -*-
import socket
from dataclasses import replace
from datetime import date
from pathlib import Path
from urllib.parse import quote

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import (
    APPLE_CATEGORIES,
    ASSEMBLED_CATEGORIES,
    CONSUMER_MARGIN_PERCENT,
    GAMING_PC_URL,
    PRICE_SEARCH_LIMIT,
    QUOTE_DISCLAIMER,
)
from app.excel_export import build_excel
from app.apple_scraper import fetch_apple_cto_options
from app.used_market import used_market_links
from app.quote_info import load_quote_info, reset_quote_info, save_quote_info
from app.quote_engine import (
    apply_manwon_rounding,
    apply_quote_margin,
    fetch_apple_quotes,
    fetch_assembled_quotes,
    fetch_assembled_quotes_by_price,
    fetch_notebook_quotes_by_price,
    fetch_game_pc_meta,
    fetch_game_pc_quotes,
    fetch_today_quotes,
    get_logic_steps,
    is_mac_apple_cat,
    margin_kind_label,
    today_quotes_to_dict,
)
from app.calc_window import close_calc_window, open_calc_window
from app.shutdown_service import schedule_full_shutdown
from app.update_service import apply_update, check_update, schedule_restart
from app.task_report import (
    build_task_report_excel,
    create_task_report,
    delete_task_report,
    get_task_report,
    load_task_reports,
    update_task_report,
)

BASE_DIR = Path(__file__).resolve().parent.parent

app = FastAPI(title="AI PC & Apple Store 견적 추출기", version="9.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def get_lan_ip() -> str:
    """실행 중인 컴퓨터의 로컬 네트워크(LAN) IPv4 주소 반환"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


@app.get("/api/server-info")
def api_server_info():
    """로컬 및 네트워크 접근 URL 반환"""
    lan_ip = get_lan_ip()
    port = 8090
    return {
        "local_url": f"http://127.0.0.1:{port}/",
        "network_url": f"http://{lan_ip}:{port}/",
        "lan_ip": lan_ip,
        "port": port,
    }


def _extra_label(
    include_monitor: bool,
    include_keyboard: bool,
    include_ram32: bool = False,
    include_setup: bool = False,
    is_apple: bool = False,
    apple_cat: str = "",
) -> str:
    parts: list[str] = []
    if is_apple:
        if is_mac_apple_cat(apple_cat) and not include_setup:
            parts.append("초기세팅비제외")
        return ("_" + "_".join(parts)) if parts else ""
    if not include_monitor:
        parts.append("모니터제외")
    if not include_keyboard:
        parts.append("무선키보드제외")
    if not include_setup:
        parts.append("초기세팅비제외")
    if include_ram32:
        parts.append("메모리32GB")
    return ("_" + "_".join(parts)) if parts else ""


def _margin_filename_label(margin: int, is_apple: bool) -> str:
    if is_apple:
        return ""
    if margin == CONSUMER_MARGIN_PERCENT:
        return "_소비자견적"
    if margin == 0:
        return "_원가견적"
    return f"_마진{margin}"


@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    lan_ip = get_lan_ip()
    port = 8090
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "today": date.today().isoformat(),
            "logic_steps": get_logic_steps(),
            "quote_disclaimer": QUOTE_DISCLAIMER,
            "apple_categories": APPLE_CATEGORIES,
            "lan_ip": lan_ip,
            "port": port,
            "network_url": f"http://{lan_ip}:{port}/",
            "local_url": f"http://127.0.0.1:{port}/",
        },
    )


@app.get("/manual", response_class=HTMLResponse)
def manual(request: Request):
    """사용설명서 — 상단 메뉴에서 연다."""
    return templates.TemplateResponse(request, "사용설명서.html", {})


@app.get("/calc", response_class=HTMLResponse)
def calc_page(request: Request):
    """독립 창으로 띄우는 계산기. 메인 견적 창 밖으로 옮길 수 있다."""
    return templates.TemplateResponse(request, "calc.html", {})


@app.post("/api/calc/open")
def api_calc_open(request: Request, payload: dict = Body(default={})):
    """견적 --app 창과 별도 프로세스로 계산기를 연다. 모든 버튼이 보이게 크기를 고정한다."""
    base = str(request.base_url).rstrip("/")
    result = open_calc_window(
        f"{base}/calc",
        left=int(payload.get("left") or 80),
        top=int(payload.get("top") or 80),
        width=int(payload.get("width") or 280),
        height=int(payload.get("height") or 540),
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail="계산기 창을 열 브라우저를 찾지 못했습니다.")
    return result


@app.get("/api/logic")
def api_logic():
    return {"steps": get_logic_steps()}



@app.get("/api/used/links")
def api_used_links(q: str = "", cpu: str = "", gpu: str = ""):
    model = (q or "").strip()
    cpu_name = (cpu or "").strip()
    gpu_name = (gpu or "").strip()
    if not (model or cpu_name or gpu_name):
        raise HTTPException(status_code=400, detail="검색어(q) 또는 CPU/그래픽카드가 필요합니다.")
    try:
        return used_market_links(model, cpu=cpu_name, gpu=gpu_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e





@app.get("/api/update/check")
def api_update_check():
    """GitHub 원격 저장소에 새 커밋이 있는지 확인"""
    return check_update()


@app.post("/api/update/apply")
def api_update_apply():
    """승인 후 git pull 로 프로젝트 갱신"""
    return apply_update()


@app.post("/api/update/restart")
def api_update_restart():
    """의존성 확인 후 서버 재시작 (update.bat restart)"""
    return schedule_restart()


@app.post("/api/shutdown")
def api_shutdown():
    """백엔드 종료 · 포트 정리 · 캐시 삭제 · 앱 브라우저 창 닫기"""
    schedule_full_shutdown(delay_sec=0.5)
    return {
        "ok": True,
        "message": "서버 종료를 시작했습니다. 포트 정리·캐시 삭제·브라우저 닫기를 진행합니다.",
    }


@app.get("/api/today")
def api_today(monitor: int = 0, keyboard: int = 0, ram32: int = 0, setup: int = 0):
    result = fetch_today_quotes(
        include_monitor=bool(monitor),
        include_keyboard=bool(keyboard),
        include_ram32=bool(ram32),
        include_setup=bool(setup),
    )
    return today_quotes_to_dict(result)


@app.get("/api/today/{tier_key}")
def api_today_tier(
    tier_key: str, monitor: int = 0, keyboard: int = 0, ram32: int = 0, setup: int = 0
):
    try:
        result = fetch_today_quotes(
            tier_key=tier_key,
            include_monitor=bool(monitor),
            include_keyboard=bool(keyboard),
            include_ram32=bool(ram32),
            include_setup=bool(setup),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return today_quotes_to_dict(result)


@app.get("/api/quote-info")
def api_quote_info_get():
    return load_quote_info()


@app.post("/api/quote-info")
def api_quote_info_save(info: dict = Body(...)):
    return save_quote_info(info)


@app.delete("/api/quote-info")
def api_quote_info_delete():
    return reset_quote_info()


@app.get("/api/category/{cat_key}")
def api_category(cat_key: str, monitor: int = 0, keyboard: int = 0, setup: int = 0):
    # 구버전 UI가 /api/category/game-pc 로 호출해도 404 대신 위자드 유도
    if cat_key in ("game-pc", "game"):
        return {
            "quote_date": date.today().isoformat(),
            "event_url": GAMING_PC_URL,
            "event_title": "게임으로PC찾기",
            "series": "게임으로PC찾기",
            "disclaimer": QUOTE_DISCLAIMER,
            "logic_steps": get_logic_steps(),
            "quotes": [],
            "needs_game_wizard": True,
        }
    if cat_key in ("price", "price-search"):
        return {
            "quote_date": date.today().isoformat(),
            "event_url": "https://www.compuzone.co.kr/product/Allassembled.htm",
            "event_title": "금액대로 찾기",
            "series": "금액대로 찾기",
            "disclaimer": QUOTE_DISCLAIMER,
            "logic_steps": get_logic_steps(),
            "quotes": [],
            "needs_price_wizard": True,
        }
    if cat_key not in ASSEMBLED_CATEGORIES:
        raise HTTPException(status_code=404, detail=f"Unknown category: {cat_key}")
    result = fetch_assembled_quotes(
        cat_key,
        include_monitor=bool(monitor),
        include_keyboard=bool(keyboard),
        include_setup=bool(setup),
    )
    return today_quotes_to_dict(result)


@app.get("/api/game-pc/meta")
def api_game_pc_meta():
    try:
        return fetch_game_pc_meta()
    except (RuntimeError, OSError, TimeoutError) as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.post("/api/game-pc/recommend")
def api_game_pc_recommend(payload: dict = Body(...)):
    games = payload.get("games") or []
    resolution = payload.get("resolution") or ""
    titles = payload.get("titles") or []
    monitor = bool(payload.get("monitor", 0))
    keyboard = bool(payload.get("keyboard", 0))
    setup = bool(payload.get("setup", 0))
    try:
        result = fetch_game_pc_quotes(
            game_ids=[str(g) for g in games],
            resolution=str(resolution),
            include_monitor=monitor,
            include_keyboard=keyboard,
            include_setup=setup,
            game_titles=[str(t) for t in titles],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (OSError, TimeoutError) as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    data = today_quotes_to_dict(result)
    data["games"] = [str(g) for g in games]
    data["titles"] = [str(t) for t in titles]
    data["resolution"] = str(resolution).upper()
    return data


@app.post("/api/price-search")
def api_price_search(payload: dict = Body(...)):
    try:
        min_price = int(payload.get("min_price") or 0)
        max_price = int(payload.get("max_price") or 0)
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail="최저가와 최고가는 숫자로 입력해주세요.") from e
    
    target_type = payload.get("target_type", "assembled")
    monitor = bool(payload.get("monitor", 0))
    keyboard = bool(payload.get("keyboard", 0))
    setup = bool(payload.get("setup", 0))
    try:
        if target_type == "notebook":
            result, meta = fetch_notebook_quotes_by_price(
                min_price,
                max_price,
                limit=PRICE_SEARCH_LIMIT,
            )
        else:
            result, meta = fetch_assembled_quotes_by_price(
                min_price,
                max_price,
                include_monitor=monitor,
                include_keyboard=keyboard,
                include_setup=setup,
                limit=PRICE_SEARCH_LIMIT,
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (OSError, TimeoutError) as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    data = today_quotes_to_dict(result)
    data.update(meta)
    return data


@app.get("/api/apple/{cat_key}")
def api_apple(cat_key: str, monitor: int = 0, keyboard: int = 0, setup: int = 0, refresh: int = 0):
    try:
        result = fetch_apple_quotes(
            cat_key,
            include_monitor=bool(monitor),
            include_setup=bool(setup),
            force_refresh=bool(refresh),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return today_quotes_to_dict(result)


@app.get("/api/apple/refresh")
@app.post("/api/apple/refresh")
def api_apple_refresh(cat_key: str = "all"):
    """Apple 공식몰 실시간 크롤링 강제 갱신: 기존 캐시 및 이전 데이터를 완전 삭제하고 공홈 실시간 데이터로 100% 교체"""
    from app.apple_scraper import clear_apple_catalog_cache
    clear_apple_catalog_cache(cat_key if cat_key != "all" else None)
    result = fetch_apple_quotes(cat_key, force_refresh=True)
    return {
        "success": True,
        "message": "Apple 공식몰 최신 데이터로 완전 갱신 완료",
        "data": today_quotes_to_dict(result),
    }


@app.get("/api/apple/cto/{tier_key}")
def api_apple_cto(tier_key: str):
    options = fetch_apple_cto_options(tier_key)
    return {
        "tier_key": tier_key,
        "options": options,
        "source": "apple_official_catalog",
    }


@app.get("/api/download/excel")
def download_excel(
    cat: str = "ai",
    monitor: int = 0,
    keyboard: int = 0,
    ram32: int = 0,
    setup: int = 0,
    unitprice: int = 1,
    margin: int = CONSUMER_MARGIN_PERCENT,
    games: str = "",
    resolution: str = "",
    titles: str = "",
    min_price: int = 0,
    max_price: int = 0,
    price_type: str = "assembled",
    index: int = -1,
):
    """index 를 주면 해당 카드 한 건만, 없으면 카테고리 전체를 내려준다."""
    include_monitor = bool(monitor)
    include_keyboard = bool(keyboard)
    include_ram32 = bool(ram32)
    include_setup = bool(setup)
    hide_prices = not bool(unitprice)
    is_apple = cat.startswith("apple_") or cat == "apple"
    try:
        margin_pct = int(margin)
    except (TypeError, ValueError):
        margin_pct = CONSUMER_MARGIN_PERCENT
    if margin_pct not in (CONSUMER_MARGIN_PERCENT, 25, 20, 15, 10, 0):
        margin_pct = CONSUMER_MARGIN_PERCENT
    if cat == "ai":
        result = fetch_today_quotes(
            include_monitor=include_monitor,
            include_keyboard=include_keyboard,
            include_ram32=include_ram32,
            include_setup=include_setup,
        )
        kr_label = "오늘의견적"
    elif cat in ASSEMBLED_CATEGORIES:
        result = fetch_assembled_quotes(
            cat,
            include_monitor=include_monitor,
            include_keyboard=include_keyboard,
            include_setup=include_setup,
        )
        kr_label = f"{ASSEMBLED_CATEGORIES[cat]['label']}_견적"
    elif cat in ("game", "game-pc"):
        game_ids = [g for g in games.split("|") if g.strip()]
        title_list = [t for t in titles.split("|") if t.strip()]
        try:
            result = fetch_game_pc_quotes(
                game_ids=game_ids,
                resolution=resolution,
                include_monitor=include_monitor,
                include_keyboard=include_keyboard,
                include_setup=include_setup,
                game_titles=title_list,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except RuntimeError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        kr_label = f"게임으로PC찾기_{resolution or 'PC'}_견적"
    elif cat in ("price", "price-search"):
        is_notebook = price_type == "notebook"
        try:
            if is_notebook:
                result, _meta = fetch_notebook_quotes_by_price(
                    min_price,
                    max_price,
                    limit=PRICE_SEARCH_LIMIT,
                )
            else:
                result, _meta = fetch_assembled_quotes_by_price(
                    min_price,
                    max_price,
                    include_monitor=include_monitor,
                    include_keyboard=include_keyboard,
                    include_setup=include_setup,
                    limit=PRICE_SEARCH_LIMIT,
                )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except RuntimeError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        kind = "노트북금액대로찾기" if is_notebook else "금액대로찾기"
        kr_label = f"{kind}_{min_price}_{max_price}_견적"
    elif cat.startswith("apple_") or cat == "apple":
        apple_sub = cat.replace("apple_", "") if cat != "apple" else "macbook"
        try:
            result = fetch_apple_quotes(
                apple_sub,
                include_monitor=include_monitor,
                include_setup=include_setup,
            )
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        cat_name = APPLE_CATEGORIES.get(apple_sub, {}).get("label", "애플스토어")
        kr_label = f"Apple_{cat_name}_견적"
    else:
        raise HTTPException(status_code=404, detail=f"Unknown category: {cat}")

    apple_sub = cat.replace("apple_", "") if is_apple and cat != "apple" else ("macbook" if cat == "apple" else "")
    kr_label += _extra_label(
        include_monitor,
        include_keyboard,
        include_ram32,
        include_setup=include_setup,
        is_apple=is_apple,
        apple_cat=apple_sub,
    )
    result = (
        result
        if is_apple
        else apply_quote_margin(result, margin_pct)
    )
    if not is_apple:
        kr_label += _margin_filename_label(margin_pct, is_apple=False)
    if hide_prices:
        kr_label += "_단가미표시"

    # 카드별 다운로드: 선택한 항목 하나만 남긴다.
    if index >= 0:
        if index >= len(result.quotes):
            raise HTTPException(
                status_code=404, detail=f"{index}번 견적 항목이 없습니다."
            )
        picked = result.quotes[index]
        result = replace(result, quotes=[picked])
        # tab_label 만 쓰면 "표준형" 이 여러 건이라 파일명이 겹친다. 모델명을 함께 붙인다.
        model_head = (picked.model or "").split("(")[0].strip()
        for ch in '\\/:*?"<>|':
            model_head = model_head.replace(ch, " ")
        name_parts = [
            p for p in ((picked.tab_label or picked.tier or "").strip(), model_head) if p
        ]
        if name_parts:
            kr_label = f"{'_'.join(name_parts)}_{kr_label}"

    kind_label = "" if is_apple else margin_kind_label(margin_pct)
    buf = build_excel(result, hide_unit_prices=hide_prices, quote_kind_label=kind_label)
    ascii_name = f"quote_{cat}{f'_{index}' if index >= 0 else ''}_{result.quote_date}.xlsx"
    utf_name = quote(f"{kr_label}_{result.quote_date}.xlsx")
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{utf_name}'
            ),
        },
    )


# ---------------------------------------------------------------------------
# 컴퓨터 작업내역서 API
# ---------------------------------------------------------------------------
@app.get("/api/task-reports")
def api_get_task_reports():
    """저장된 작업내역서 목록 조회"""
    return {"reports": load_task_reports()}


@app.post("/api/task-reports")
def api_create_task_report(data: dict = Body(...)):
    """새 작업내역서 저장"""
    created = create_task_report(data)
    return {"status": "ok", "report": created}


@app.get("/api/task-reports/{report_id}")
def api_get_task_report_detail(report_id: str):
    """작업내역서 단건 조회"""
    report = get_task_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="작업내역서를 찾을 수 없습니다.")
    return {"status": "ok", "report": report}


@app.put("/api/task-reports/{report_id}")
def api_update_task_report(report_id: str, data: dict = Body(...)):
    """작업내역서 수정"""
    updated = update_task_report(report_id, data)
    if not updated:
        raise HTTPException(status_code=404, detail="작업내역서를 찾을 수 없습니다.")
    return {"status": "ok", "report": updated}


@app.delete("/api/task-reports/{report_id}")
def api_delete_task_report(report_id: str):
    """작업내역서 삭제"""
    deleted = delete_task_report(report_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="작업내역서를 찾을 수 없습니다.")
    return {"status": "ok", "message": "삭제되었습니다."}


@app.post("/api/task-reports/download/excel")
def api_download_task_report_excel(data: dict = Body(...)):
    """현재 작성 중인 작업내역서 데이터로 즉시 엑셀 파일 생성 다운로드"""
    buf = build_task_report_excel(data)
    cust_name = data.get("customer_name") or "고객"
    doc_no = data.get("doc_no") or date.today().strftime("%Y%m%d")
    filename = f"작업내역서_{cust_name}_{doc_no}.xlsx"
    utf_name = quote(filename)
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="task_report.xlsx"; filename*=UTF-8\'\'{utf_name}',
        },
    )


@app.get("/api/task-reports/{report_id}/download/excel")
def api_download_saved_task_report_excel(report_id: str):
    """저장된 작업내역서 ID로 엑셀 파일 다운로드"""
    report = get_task_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="작업내역서를 찾을 수 없습니다.")
    buf = build_task_report_excel(report)
    cust_name = report.get("customer_name") or "고객"
    doc_no = report.get("doc_no") or report_id
    filename = f"작업내역서_{cust_name}_{doc_no}.xlsx"
    utf_name = quote(filename)
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="task_report_{report_id}.xlsx"; filename*=UTF-8\'\'{utf_name}',
        },
    )

