# -*- coding: utf-8 -*-
"""컴퓨존 견적서 Excel 내보내기

상세 시트 양식은 사용자 제공 '오늘의견적' 실무형 시트를 기준으로 한다:
  - A4 세로, 여백 L/R 0.75 · T/B 1.0 · 머리글/바닥글 0.5
  - 열 너비 A 6.625 / B 12.625 / C 36.625 / D 7.75 / E 7.625 / F 9.125 (고정)
  - 행 높이: 제목행 34.85, 그 외 26.25
  - 폰트: 맑은 고딕 (본문 10pt, 제목 24pt bold)
"""
import math
from io import BytesIO

from openpyxl import Workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.properties import PageSetupProperties

from app.config import QUOTE_DISCLAIMER
from app.quote_engine import TodayQuotesResult, fetch_today_quotes
from app.quote_info import load_quote_info

# 견적서마다 사용자가 개별 항목을 추가할 수 있는 빈 행 수
USER_BLANK_ROWS = 2

# 모든 시트 공통 문구. 표 헤더 바로 위의 빈 행에 넣어 행을 새로 만들지 않는다.
VALIDITY_NOTE = "▶ 견적유효기간 : 견적일로부터 24시간"

FONT_NAME = "맑은 고딕"
TITLE_ROW_HEIGHT = 34.85
BODY_ROW_HEIGHT = 26.25

# ── 인쇄 규격 ────────────────────────────────────────────────
# A4 210mm 에서 좌우 15mm 씩 빼면 인쇄 가능 폭은 180mm.
# 엑셀 열 픽셀 = round(width * 7) + 5 이므로 6열 기준 너비 합 약 88 이 상한이다.
PRINT_MARGIN_LR_MM = 15.0
PRINT_MARGIN_LR_INCH = PRINT_MARGIN_LR_MM / 25.4
MAX_TOTAL_COL_WIDTH = 88.0

# 줄바꿈으로 여러 줄이 된 행의 높이는 "줄 수 x 줄 높이 + 여백" 으로 잡는다.
# 기본 행 높이에 배수를 곱하면(26.25 x 2 = 52.5) 위아래 여백만 커져 어색해진다.
DEFAULT_ROW_HEIGHT = 15.0
LINE_HEIGHT = 14.5   # 맑은 고딕 10pt 한 줄
ROW_PADDING = 5.0
MAX_WRAP_LINES = 4

# 폼1: 다른 폼(기본 15pt)과 눈높이를 맞춘 값
TABLE_ROW_HEIGHT = 19.5  # 부품 표 본문 1줄
INFO_ROW_HEIGHT = 21.0   # 상단 견적처/공급자 정보
TOTAL_ROW_HEIGHT = 24.0  # 합계금액(14pt)
DETAIL_COL_WIDTHS = {
    "A": 6.625,
    "B": 12.625,
    "C": 36.625,
    "D": 7.75,
    "E": 7.625,
    "F": 9.125,
}

# 폼1: 상단 정보 영역 기준으로 배분한다.
#   A = "견 적 처" 같은 자간 라벨(8폭)이 한 줄에 들어가야 한다
#   F = 공급자명이 들어가는 칸이라 좁으면 3줄까지 접힌다
# 합 87.375 (상한 88)
FORM3_COL_WIDTHS = {
    "A": 9.5,
    "B": 12.625,
    "C": 30.0,
    "D": 7.75,
    "E": 9.5,
    "F": 18.0,
}

# 폼별 열 너비. 합계가 MAX_TOTAL_COL_WIDTH 를 넘으면 A4 인쇄 시 오른쪽이 잘린다.
FORM2_COL_WIDTHS = {"A": 4, "B": 15, "C": 35, "D": 8, "E": 10, "F": 12}      # 합 84
FORM_REPORT_COL_WIDTHS = {"A": 4, "B": 42, "C": 8, "D": 14, "E": 10, "F": 10}  # 합 88 (기존 94)
FORM4_COL_WIDTHS = {"A": 5, "B": 12, "C": 40, "D": 8, "E": 10, "F": 12}      # 합 87
FORM5_COL_WIDTHS = {"A": 3, "B": 18, "C": 40, "D": 8, "E": 5, "F": 14}       # 합 88 (기존 96)
FORM6_COL_WIDTHS = {"A": 13, "B": 32, "C": 6, "D": 11, "E": 12, "F": 8, "G": 6}   # 합 88
FORM7_COL_WIDTHS = {"A": 6, "B": 18, "C": 32, "D": 8, "E": 12, "F": 12}          # 합 88
FORM8_COL_WIDTHS = {"A": 5, "B": 15, "C": 35, "D": 8, "E": 12, "F": 13}          # 합 88


def _as_warranty_period(category: str) -> str:
    """부품 카테고리별 무상 A/S 기간을 반환한다."""
    cat = (category or "").upper()
    if "CPU" in cat:
        return "3년"
    elif "메인보드" in cat or "M.B" in cat:
        return "3년"
    elif "그래픽" in cat or "VGA" in cat or "VIDEO" in cat:
        return "3년"
    elif "SSD" in cat:
        return "2~5년"
    elif "HDD" in cat:
        return "2년"
    elif "파워" in cat or "POWER" in cat:
        return "5~7년"
    elif "쿨러" in cat or "케이스" in cat:
        return "1년"
    elif "메모리" in cat or "RAM" in cat:
        return "1년"
    elif "조립" in cat or "공임" in cat or "기술" in cat or "소프트웨어" in cat:
        return "-"
    return "1년"


def _spec_remark(category: str, name: str) -> str:
    """부품 카테고리 및 품명 기반 비고를 반환한다."""
    cat = (category or "").upper()
    nm = (name or "").upper()
    if "RAM" in cat or "메모리" in cat:
        if "32" in nm:
            return "총 32GB"
        elif "16" in nm:
            return "총 16GB"
        elif "64" in nm:
            return "총 64GB"
    elif "조립" in cat or "공임" in cat:
        return "초기불량 케어"
    elif "소프트웨어" in cat or "윈도우" in cat or "DSP" in nm:
        return "정품 라이선스"
    return ""


def _sheet_name(quote) -> str:
    label = quote.tab_label or quote.tier
    for ch in ("\\", "/", "*", "?", ":", "[", "]"):
        label = label.replace(ch, " ")
    return label[:31]


def _cell_display_width(value) -> float:
    if value is None:
        return 0.0
    width = 0.0
    for ch in str(value):
        width += 2.0 if ord(ch) > 127 else 1.0
    return width


def _auto_fit_columns(
    ws,
    col_limits: dict[int, tuple[float, float]] | None = None,
    default_limit: tuple[float, float] = (8.0, 50.0),
) -> None:
    col_limits = col_limits or {}
    max_widths: dict[int, float] = {}

    for row in ws.iter_rows():
        for cell in row:
            if isinstance(cell, MergedCell) or cell.value is None:
                continue
            col = cell.column
            max_widths[col] = max(max_widths.get(col, 0.0), _cell_display_width(cell.value))

    for col, content_width in max_widths.items():
        min_w, max_w = col_limits.get(col, default_limit)
        letter = get_column_letter(col)
        ws.column_dimensions[letter].width = min(max(content_width + 2.5, min_w), max_w)


def _set_cell(
    cell,
    *,
    font,
    border=None,
    alignment,
    number_format: str | None = None,
) -> None:
    cell.font = font
    if border is not None:
        cell.border = border
    cell.alignment = alignment
    if number_format:
        cell.number_format = number_format


def _draw_box(
    ws,
    r1: int,
    c1: int,
    r2: int,
    c2: int,
    outer: Side | None = None,
    inner: Side | None = None,
) -> None:
    """영역 외곽/내부 선으로 사각형 박스를 그린다."""
    outer = outer or Side(style="medium", color="333333")
    inner = inner or Side(style="thin", color="999999")
    for rr in range(r1, r2 + 1):
        for cc in range(c1, c2 + 1):
            ws.cell(row=rr, column=cc).border = Border(
                left=outer if cc == c1 else inner,
                right=outer if cc == c2 else inner,
                top=outer if rr == r1 else inner,
                bottom=outer if rr == r2 else inner,
            )


def _apply_page_setup(ws) -> None:
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = "portrait"
    ws.page_margins.left = PRINT_MARGIN_LR_INCH
    ws.page_margins.right = PRINT_MARGIN_LR_INCH
    ws.page_margins.top = 1.0
    ws.page_margins.bottom = 1.0
    ws.page_margins.header = 0.5
    ws.page_margins.footer = 0.5
    # 열 너비를 규격에 맞춰도 폰트/환경 차이로 넘칠 수 있어 가로 1페이지 맞춤을 함께 건다.
    # (세로는 0 = 자동, 내용만큼 여러 장으로 출력)
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.print_options.horizontalCentered = True


def _lines_needed(value, usable_width: float) -> int:
    """wrap_text 셀이 몇 줄로 표시될지 추정한다. 한글은 2칸으로 센다."""
    if value is None or usable_width <= 0:
        return 1
    total = 0
    for segment in str(value).split("\n"):
        seg_width = _cell_display_width(segment)
        total += max(1, math.ceil(seg_width / usable_width))
    return max(1, total)


def _auto_fit_row_heights(ws) -> None:
    """내용이 두 줄 이상으로 접히는 행의 높이를 줄 수만큼 키운다.

    행 높이가 1줄 기준으로 고정돼 있으면 긴 품명이 잘려 보인다.
    병합된 셀은 병합 범위 전체 너비를 기준으로 판단한다.
    """
    merged_width: dict[tuple[int, int], float] = {}
    for rng in ws.merged_cells.ranges:
        width = 0.0
        for col in range(rng.min_col, rng.max_col + 1):
            dim = ws.column_dimensions.get(get_column_letter(col))
            width += (dim.width if dim and dim.width else 8.43)
        merged_width[(rng.min_row, rng.min_col)] = width

    for row in ws.iter_rows():
        if not row:
            continue
        row_no = row[0].row
        needed = 0.0
        for cell in row:
            if isinstance(cell, MergedCell) or cell.value is None:
                continue
            alignment = cell.alignment
            if not (alignment and alignment.wrap_text):
                continue
            width = merged_width.get((cell.row, cell.column))
            if width is None:
                dim = ws.column_dimensions.get(get_column_letter(cell.column))
                width = dim.width if dim and dim.width else 8.43
            # 열 너비는 기준 폰트(10pt) 기준이라 큰 글꼴은 그만큼 좁게 봐야 한다.
            # 이걸 빼먹으면 16pt 합계 금액이 1줄로 계산되고 실제로는 2줄로 접힌다.
            size = (cell.font.size if cell.font and cell.font.size else 10.0)
            scale = max(size / 10.0, 1.0)
            usable = max(width - 1.0, 1.0) / scale
            lines = min(_lines_needed(cell.value, usable), MAX_WRAP_LINES)
            needed = max(needed, lines * LINE_HEIGHT * scale + ROW_PADDING)

        if needed <= 0:
            continue
        current = ws.row_dimensions[row_no].height or DEFAULT_ROW_HEIGHT
        ws.row_dimensions[row_no].height = max(current, needed)


def _write_validity_note(ws, row: int, last_col: str = "F") -> None:
    """견적유효기간 문구를 기존 빈 행에 채운다(행을 새로 삽입하지 않는다)."""
    ws.merge_cells(f"A{row}:{last_col}{row}")
    cell = ws[f"A{row}"]
    cell.value = VALIDITY_NOTE
    cell.font = Font(name=FONT_NAME, size=10, bold=True, color="000080")
    cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = INFO_ROW_HEIGHT


def _assert_print_width(ws) -> float:
    """열 너비 합을 반환한다. MAX_TOTAL_COL_WIDTH 초과 시 인쇄에서 잘릴 수 있다."""
    return sum(
        (dim.width or 0.0)
        for dim in ws.column_dimensions.values()
    )


# 최대값 합이 MAX_TOTAL_COL_WIDTH(88) 을 넘지 않아야 A4 인쇄에서 잘리지 않는다.
# URL 은 폭을 넓히는 대신 줄바꿈으로 접고 행 높이를 키운다.
SUMMARY_COL_LIMITS = {
    1: (4.0, 5.0),    # No
    2: (8.0, 11.0),   # 구분
    3: (18.0, 20.0),  # 모델명
    4: (10.0, 12.0),  # 상품번호
    5: (12.0, 14.0),  # 견적합계
    6: (14.0, 26.0),  # URL — 87자 전문이 4줄 안에 들어가야 인쇄물에서 읽을 수 있다
}


def build_excel(
    result: TodayQuotesResult | None = None,
    *,
    hide_unit_prices: bool = False,
    quote_kind_label: str = "",
) -> BytesIO:
    """hide_unit_prices=True 면 품목별 단가·금액을 비우고 합계만 남긴다."""
    if result is None:
        result = fetch_today_quotes()

    def _maybe_price(value):
        return None if hide_unit_prices else value

    # 저장된 견적처/공급자 정보 (모든 폼 시트에 적용)
    info = load_quote_info()
    quote_date = info["quote_date"] or result.quote_date

    wb = Workbook()
    wb.remove(wb.active)

    thin = Side(style="thin", color="999999")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_fill = PatternFill("solid", fgColor="1F4E79")
    total_fill = PatternFill("solid", fgColor="FFF2CC")
    header_font = Font(name=FONT_NAME, size=10, bold=True, color="FFFFFF")
    normal = Font(name=FONT_NAME, size=10)
    bold = Font(name=FONT_NAME, size=10, bold=True)
    title_font = Font(name=FONT_NAME, size=24, bold=True)
    label_font = Font(name=FONT_NAME, size=10, bold=True, color="1F4E79")
    total_font = Font(name=FONT_NAME, size=10, bold=True)
    valid_font = Font(name=FONT_NAME, size=10, bold=True, color="3057B9")
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left = Alignment(horizontal="left", vertical="center", wrap_text=True)
    right = Alignment(horizontal="right", vertical="center")

    # ---------------- 견적요약 시트 ----------------
    ws_sum = wb.create_sheet("견적요약", 0)
    _apply_page_setup(ws_sum)
    ws_sum.merge_cells("A1:F1")
    kind_suffix = f" — {quote_kind_label}" if quote_kind_label else ""
    ws_sum["A1"] = f"{result.event_title}{kind_suffix} — 오늘의 견적 — {result.quote_date}"
    ws_sum["A1"].font = Font(name=FONT_NAME, size=16, bold=True, color="1F4E79")
    ws_sum["A1"].alignment = center
    ws_sum.row_dimensions[1].height = TITLE_ROW_HEIGHT

    meta = [
        ("견적일", result.quote_date),
        ("구분", result.event_title),
        ("시리즈", result.series),
    ]
    r = 3
    for k, v in meta:
        ws_sum[f"A{r}"] = k
        ws_sum[f"B{r}"] = v
        ws_sum.merge_cells(f"B{r}:F{r}")
        ws_sum[f"A{r}"].font = bold
        ws_sum.row_dimensions[r].height = BODY_ROW_HEIGHT
        r += 1

    _write_validity_note(ws_sum, r)  # 헤더 직전 빈 행
    r += 1
    for i, h in enumerate(["No", "구분", "모델명", "상품번호", "견적합계", "URL"], 1):
        c = ws_sum.cell(row=r, column=i, value=h)
        c.font = header_font
        c.fill = header_fill
        c.border = border
        c.alignment = center
    ws_sum.row_dimensions[r].height = BODY_ROW_HEIGHT
    r += 1
    for idx, q in enumerate(result.quotes, 1):
        row = [idx, q.tier, q.model, q.product_no, q.total, q.url]
        for i, val in enumerate(row, 1):
            c = ws_sum.cell(row=r, column=i, value=val)
            align = right if i == 5 else left
            _set_cell(c, font=normal, border=border, alignment=align, number_format="#,##0" if i == 5 else None)
        ws_sum.row_dimensions[r].height = BODY_ROW_HEIGHT
        r += 1

    _auto_fit_columns(ws_sum, SUMMARY_COL_LIMITS)

    label_fill = PatternFill("solid", fgColor="D9E2F3")
    disclaimer_font = Font(name=FONT_NAME, size=10, bold=True, color="000080")
    sum_label_font = Font(name=FONT_NAME, size=12, bold=True)
    sum_value_font = Font(name=FONT_NAME, size=12, bold=True)

    def _new_detail_sheet(title: str):
        ws = wb.create_sheet(title)
        _apply_page_setup(ws)
        for letter, width in DETAIL_COL_WIDTHS.items():
            ws.column_dimensions[letter].width = width
        return ws

    thin_black = Side(style="thin", color="000000")
    border_black = Border(left=thin_black, right=thin_black, top=thin_black, bottom=thin_black)

    def _write_parts_table(
        ws,
        q,
        hr: int,
        mono: bool = False,
        show_header: bool = True,
        header_labels: list[str] | None = None,
        dashed: bool = False,
        body_font=None,
        custom_header_fill=None,
        custom_total_fill=None,
        custom_total_font=None,
    ) -> int:
        """부품 표 + 빈 행 + 합계 + 하단 문구. 마지막 행 번호 반환."""
        tbl_border = border_black if mono else border
        fnt = body_font or normal
        bold_fnt = custom_total_font or Font(name=fnt.name, size=11, bold=True)
        h_fill = custom_header_fill or (None if mono else header_fill)
        t_fill = custom_total_fill or (None if mono else total_fill)

        if dashed:
            dash = Side(style="dashed", color="888888")
            v_side = thin_black if mono else thin
            row_border = Border(left=v_side, right=v_side, top=dash, bottom=dash)
        else:
            row_border = tbl_border

        if show_header:
            labels = header_labels or ["No", "구분", "품명", "수량", "단가", "금액"]
            for i, h in enumerate(labels, 1):
                c = ws.cell(row=hr, column=i, value=h or None)
                if mono and not h_fill:
                    c.font = Font(name=fnt.name, size=10, bold=True)
                else:
                    c.font = Font(name=fnt.name, size=10, bold=True, color="FFFFFF" if h_fill else "000000")
                    c.fill = h_fill or PatternFill("solid", fgColor="FFFFFF")
                c.border = tbl_border
                c.alignment = center
        else:
            for i in range(1, 7):
                c = ws.cell(row=hr, column=i)
                c.border = tbl_border

        r = hr + 1
        for part in q.parts:
            has_unit = part.qty > 0 or part.unit_price < 0
            has_amount = part.qty > 0 or part.amount < 0
            unit_val = _maybe_price(part.unit_price) if has_unit else None
            amt_val = _maybe_price(part.amount) if has_amount else None
            values = [
                (1, part.no, center, None),
                (2, part.category, center, None),
                (3, part.name, left, None),
                (4, part.qty if part.qty > 0 else None, center, None),
                (5, unit_val, right, "#,##0"),
                (6, amt_val, right, "#,##0"),
            ]
            for col, val, align, num_fmt in values:
                c = ws.cell(row=r, column=col, value=val)
                _set_cell(c, font=fnt, border=row_border, alignment=align, number_format=num_fmt)
            r += 1

        next_no = len(q.parts) + 1
        for blank_i in range(USER_BLANK_ROWS):
            for col in range(1, 7):
                c = ws.cell(row=r, column=col, value=next_no + blank_i if col == 1 else None)
                align = center if col in (1, 2, 4) else (left if col == 3 else right)
                num_fmt = "#,##0" if col in (5, 6) else None
                _set_cell(c, font=fnt, border=row_border, alignment=align, number_format=num_fmt)
            r += 1

        ws.merge_cells(f"A{r}:E{r}")
        ws[f"A{r}"] = "합계 (VAT 포함)"
        ws[f"F{r}"] = q.total
        if t_fill:
            ws[f"A{r}"].fill = t_fill
            ws[f"F{r}"].fill = t_fill
        _set_cell(ws[f"A{r}"], font=bold_fnt, border=tbl_border, alignment=center)
        _set_cell(ws[f"F{r}"], font=bold_fnt, border=tbl_border, alignment=right, number_format="#,##0")

        r += 2
        ws.merge_cells(f"A{r}:F{r}")
        ws[f"A{r}"] = QUOTE_DISCLAIMER
        _set_cell(ws[f"A{r}"], font=disclaimer_font, alignment=left)

        r += 1
        ws.merge_cells(f"A{r}:F{r}")
        ws[f"A{r}"] = "※ 컴퓨존 실시간 판매가 기준 (VAT 포함)"
        _set_cell(ws[f"A{r}"], font=normal, alignment=left)

        r += 1
        ws.merge_cells(f"A{r}:F{r}")
        ws[f"A{r}"] = q.url
        _set_cell(ws[f"A{r}"], font=normal, alignment=left)

        # 표 영역(hr 부터)만 손댄다. 2행부터 일괄 지정하면 상단 정보 영역까지 덮어써서
        # 보내는쪽/받는쪽 칸의 위아래 여백이 과하게 벌어진다.
        for rn in range(hr, r + 1):
            ws.row_dimensions[rn].height = TABLE_ROW_HEIGHT
        return r

                    # ---------------- 상세 견적 시트 (5종 디자인 폼 - 완전 차별화된 레이아웃) ----------------
    for q in result.quotes:
        
        # Helper variables
        date_str = quote_date
        cust_name = info["customer_name"]
        cust_mgr = info["customer_manager"]
        sup_name = info["supplier_name"]
        sup_mgr = info["supplier_manager"]
        sup_phone = info["supplier_phone"]
        sup_email = (info.get("supplier_email") or "").strip()

        # =====================================================================
        # [폼 1] 표준 B2B 양식 (Standard Classic) - 기존에 가장 익숙한 형태
        # =====================================================================
        ws1 = _new_detail_sheet(f"{_sheet_name(q)[:24]} 폼1(표준)")
        for letter, width in FORM3_COL_WIDTHS.items():
            ws1.column_dimensions[letter].width = width
            
        ws1["A1"] = f"견   적   서{('  ·  ' + quote_kind_label) if quote_kind_label else ''}"
        ws1.merge_cells("A1:F1")
        ws1.row_dimensions[1].height = TITLE_ROW_HEIGHT
        ws1["A1"].font = Font(name=FONT_NAME, size=24, bold=True, color="1F4E79")
        ws1["A1"].alignment = center
        
        c_fill = PatternFill("solid", fgColor="E7E6E6")
        for rn, lk, lv, rk, rv in [
            (3, "견 적 처", cust_name, "공 급 자", sup_name),
            (4, "담 당 자", cust_mgr, "담 당 자", sup_mgr),
            (5, "견 적 일", date_str, "연 락 처", sup_phone),
        ]:
            ws1[f"A{rn}"] = lk
            ws1[f"B{rn}"] = lv
            ws1[f"E{rn}"] = rk
            ws1[f"F{rn}"] = rv
            ws1.merge_cells(f"B{rn}:C{rn}")
            ws1.row_dimensions[rn].height = INFO_ROW_HEIGHT
            for col in ("A", "E"):
                ws1[f"{col}{rn}"].fill = c_fill
                _set_cell(ws1[f"{col}{rn}"], font=bold, border=border, alignment=center)
            # 값은 좌우 모두 왼쪽 정렬로 통일한다(한쪽만 가운데면 행마다 글자 위치가 어긋난다).
            for col in ("B", "F"):
                _set_cell(ws1[f"{col}{rn}"], font=normal, border=border, alignment=left)

        ws1["E6"] = "이 메 일"
        ws1["F6"] = sup_email
        ws1.row_dimensions[6].height = INFO_ROW_HEIGHT
        ws1["E6"].fill = c_fill
        _set_cell(ws1["E6"], font=bold, border=border, alignment=center)
        _set_cell(ws1["F6"], font=normal, border=border, alignment=left)

        ws1["A7"] = "합계금액 (부가세포함)"
        ws1["D7"] = q.total
        ws1.merge_cells("A7:C7")
        ws1.merge_cells("D7:F7")
        for ref in ("A7", "D7"):
            ws1[ref].fill = PatternFill("solid", fgColor="FFF2CC")
        _set_cell(ws1["A7"], font=Font(name=FONT_NAME, size=12, bold=True), border=border, alignment=center)
        _set_cell(ws1["D7"], font=Font(name=FONT_NAME, size=14, bold=True), border=border, alignment=right, number_format="#,##0")
        
        ws1.row_dimensions[7].height = TOTAL_ROW_HEIGHT

        _write_validity_note(ws1, 8)  # 표 헤더(10행) 직전 빈 행
        
        _write_parts_table(ws1, q, hr=10, custom_header_fill=PatternFill("solid", fgColor="1F4E79"))

        # =====================================================================
        # [폼 2] 모던 인보이스형 (Modern Invoice) - 송장 스타일, 깔끔한 레이아웃
        # =====================================================================
        ws2 = _new_detail_sheet(f"{_sheet_name(q)[:23]} 폼2(인보이스)")
        for letter, width in FORM2_COL_WIDTHS.items():
            ws2.column_dimensions[letter].width = width
            
        ws2["A1"] = "INVOICE"
        ws2.merge_cells("A1:C2")
        ws2["A1"].font = Font(name="Arial", size=28, bold=True, color="333333")
        ws2["A1"].alignment = Alignment(horizontal="left", vertical="top")
        
        ws2["D1"] = sup_name
        ws2.merge_cells("D1:F1")
        _set_cell(ws2["D1"], font=Font(name=FONT_NAME, size=14, bold=True), alignment=right)
        ws2["D2"] = f"Tel: {sup_phone} / 담당: {sup_mgr}"
        ws2.merge_cells("D2:F2")
        _set_cell(ws2["D2"], font=normal, alignment=right)
        if sup_email:
            ws2["D3"] = f"E-mail: {sup_email}"
            ws2.merge_cells("D3:F3")
            _set_cell(ws2["D3"], font=normal, alignment=right)

        ws2["A4"] = "BILL TO:"
        _set_cell(ws2["A4"], font=bold, alignment=left)
        ws2["B4"] = cust_name
        ws2["B5"] = f"Attn: {cust_mgr}"
        ws2["E4"] = "DATE:"
        ws2["F4"] = date_str
        
        ws2.merge_cells("B4:C4")
        ws2.merge_cells("B5:C5")
        
        _write_validity_note(ws2, 6)  # 표 헤더(7행) 직전 빈 행

        # Invoice Table
        r = 7
        headers = ["No", "ITEM", "DESCRIPTION", "QTY", "RATE", "AMOUNT"]
        inv_head_fill = PatternFill("solid", fgColor="2C3E50")
        for i, h in enumerate(headers, 1):
            c = ws2.cell(row=r, column=i, value=h)
            _set_cell(c, font=Font(name="Arial", size=10, bold=True, color="FFFFFF"), alignment=center)
            c.fill = inv_head_fill
            
        r += 1
        sub_total = 0
        inv_border = Border(bottom=Side(style="thin", color="DDDDDD"))
        for i, p in enumerate(q.parts, 1):
            ws2.cell(row=r, column=1, value=i).border = inv_border
            ws2.cell(row=r, column=2, value=p.category).border = inv_border
            ws2.cell(row=r, column=3, value=p.name).border = inv_border
            ws2.cell(row=r, column=4, value=p.qty).border = inv_border
            ws2.cell(row=r, column=5, value=_maybe_price(p.unit_price)).border = inv_border
            ws2.cell(row=r, column=6, value=_maybe_price(p.amount)).border = inv_border
            
            _set_cell(ws2.cell(row=r, column=1), font=normal, alignment=center)
            _set_cell(ws2.cell(row=r, column=2), font=normal, alignment=center)
            _set_cell(ws2.cell(row=r, column=3), font=normal, alignment=left)
            _set_cell(ws2.cell(row=r, column=4), font=normal, alignment=center)
            _set_cell(ws2.cell(row=r, column=5), font=normal, alignment=right, number_format="#,##0")
            _set_cell(ws2.cell(row=r, column=6), font=normal, alignment=right, number_format="#,##0")
            sub_total += p.amount
            r += 1
            
        r += 1
        ws2.merge_cells(f"D{r}:E{r}")
        ws2[f"D{r}"] = "SUBTOTAL"
        ws2[f"F{r}"] = q.total if hide_unit_prices else sub_total
        _set_cell(ws2[f"D{r}"], font=bold, alignment=right)
        _set_cell(ws2[f"F{r}"], font=normal, alignment=right, number_format="#,##0")
        
        r += 1
        ws2.merge_cells(f"D{r}:E{r}")
        ws2[f"D{r}"] = "TAX (VAT)"
        ws2[f"F{r}"] = "Included"
        _set_cell(ws2[f"D{r}"], font=bold, alignment=right)
        _set_cell(ws2[f"F{r}"], font=normal, alignment=right)
        
        r += 1
        ws2.merge_cells(f"D{r}:E{r}")
        ws2[f"D{r}"] = "TOTAL DUE"
        ws2[f"F{r}"] = q.total
        _set_cell(ws2[f"D{r}"], font=Font(name="Arial", size=12, bold=True, color="2C3E50"), alignment=right)
        _set_cell(ws2[f"F{r}"], font=Font(name="Arial", size=14, bold=True, color="2C3E50"), alignment=right, number_format="#,##0")
        
        ws2.merge_cells(f"A{r+2}:F{r+2}")
        ws2[f"A{r+2}"] = "Thank you for your business!"
        _set_cell(ws2[f"A{r+2}"], font=Font(name="Arial", size=10, italic=True), alignment=center)

        # =====================================================================
        # [폼 3] 요약 보고서형 (Executive Summary) - 심플하고 가독성 극대화
        # =====================================================================
        ws3 = _new_detail_sheet(f"{_sheet_name(q)[:23]} 폼3(보고서)")
        for letter, width in FORM_REPORT_COL_WIDTHS.items():
            ws3.column_dimensions[letter].width = width
            
        ws3["A1"] = "PROJECT QUOTATION"
        ws3.merge_cells("A1:F1")
        ws3["A1"].font = Font(name="Malgun Gothic", size=20, bold=True, color="E67E22")
        ws3["A1"].alignment = left
        
        # 큰 박스에 요약 정보
        _draw_box(ws3, 3, 1, 5, 6, outer=Side(style="medium", color="E67E22"), inner=Side(style="thin", color="FFFFFF"))
        for rr in range(3, 6):
            for cc in range(1, 7):
                ws3.cell(row=rr, column=cc).fill = PatternFill("solid", fgColor="FDF2E9")
                
        ws3["A3"] = f"To: {cust_name} ({cust_mgr})"
        ws3.merge_cells("A3:B3")
        _set_cell(ws3["A3"], font=bold, alignment=left)
        
        ws3["A4"] = f"From: {sup_name} ({sup_mgr} / {sup_phone})"
        ws3.merge_cells("A4:B4")
        _set_cell(ws3["A4"], font=normal, alignment=left)
        if sup_email:
            ws3["A5"] = f"E-mail: {sup_email}"
            ws3.merge_cells("A5:B5")
            _set_cell(ws3["A5"], font=normal, alignment=left)

        ws3["D3"] = "TOTAL ESTIMATE"
        ws3.merge_cells("D3:F3")
        _set_cell(ws3["D3"], font=Font(name=FONT_NAME, size=10, color="666666"), alignment=right)
        
        ws3["D4"] = q.total
        ws3.merge_cells("D4:F5")
        _set_cell(ws3["D4"], font=Font(name=FONT_NAME, size=22, bold=True, color="E67E22"), alignment=right, number_format="₩ #,##0")
        
        _write_validity_note(ws3, 6)  # 표 헤더(7행) 직전 빈 행

        r = 7
        ws3["A7"] = "Description"
        ws3["C7"] = "Qty"
        ws3["D7"] = "Amount"
        ws3.merge_cells("A7:B7")
        
        th_font = Font(name=FONT_NAME, size=11, bold=True, color="FFFFFF")
        th_fill = PatternFill("solid", fgColor="D35400")
        for c in [1, 3, 4]:
            cell = ws3.cell(row=r, column=c)
            cell.font = th_font
            cell.fill = th_fill
            cell.alignment = center
            
        r += 1
        for p in q.parts:
            ws3.merge_cells(f"A{r}:B{r}")
            ws3[f"A{r}"] = f"[{p.category}] {p.name}"
            ws3[f"C{r}"] = p.qty
            ws3[f"D{r}"] = _maybe_price(p.amount)
            
            _set_cell(ws3[f"A{r}"], font=normal, alignment=left)
            _set_cell(ws3[f"C{r}"], font=normal, alignment=center)
            _set_cell(ws3[f"D{r}"], font=bold, alignment=right, number_format="#,##0")
            r += 1
            
        ws3.merge_cells(f"A{r+1}:F{r+1}")
        ws3[f"A{r+1}"] = QUOTE_DISCLAIMER
        _set_cell(ws3[f"A{r+1}"], font=Font(name=FONT_NAME, size=9, color="888888"), alignment=left)

        # =====================================================================
        # [폼 4] 기술 명세서형 (Technical Spec) - 부품과 상세규격을 나누어 표시
        # =====================================================================
        ws4 = _new_detail_sheet(f"{_sheet_name(q)[:22]} 폼4(명세서)")
        for letter, width in FORM4_COL_WIDTHS.items():
            ws4.column_dimensions[letter].width = width
            
        ws4["A1"] = "기술 명세 및 견적서 (Technical Proposal)"
        ws4.merge_cells("A1:F1")
        ws4["A1"].font = Font(name=FONT_NAME, size=20, bold=True, color="27AE60")
        ws4["A1"].alignment = center
        
        ws4["A3"] = f"Date: {date_str}  |  Ref: {q.product_no}"
        ws4.merge_cells("A3:F3")
        _set_cell(ws4["A3"], font=normal, alignment=right)
        
        _draw_box(ws4, 4, 1, 5, 6, outer=Side(style="medium", color="27AE60"))
        ws4["A4"] = f" 수신 (To) : {cust_name} - {cust_mgr} 귀하"
        ws4["A5"] = (
            f" 발신 (From): {sup_name} - {sup_mgr} ({sup_phone}"
            + (f" / {sup_email}" if sup_email else "")
            + ")"
        )
        ws4.merge_cells("A4:C4")
        ws4.merge_cells("A5:C5")
        _set_cell(ws4["A4"], font=bold, alignment=left)
        _set_cell(ws4["A5"], font=normal, alignment=left)
        
        # 라벨과 금액을 한 셀에 넣으면 16pt 에서 두 줄로 접힌다. 폼3 처럼 위아래로 나눈다.
        ws4["D4"] = "총 견적금액"
        ws4.merge_cells("D4:F4")
        _set_cell(
            ws4["D4"],
            font=Font(name=FONT_NAME, size=10, bold=True, color="666666"),
            alignment=center,
        )

        ws4["D5"] = q.total
        ws4.merge_cells("D5:F5")
        _set_cell(
            ws4["D5"],
            font=Font(name=FONT_NAME, size=16, bold=True, color="27AE60"),
            alignment=center,
            number_format='#,##0" 원"',
        )
        
        _write_validity_note(ws4, 6)  # 표 헤더(7행) 직전 빈 행

        r = 7
        t4_fill = PatternFill("solid", fgColor="27AE60")
        for i, h in enumerate(["No", "Component", "Specification", "Q'ty", "Unit", "Total"], 1):
            c = ws4.cell(row=r, column=i, value=h)
            _set_cell(c, font=Font(name=FONT_NAME, size=10, bold=True, color="FFFFFF"), alignment=center, border=border)
            c.fill = t4_fill
            
        r += 1
        dash_border = Border(bottom=Side(style="dashed", color="999999"))
        for i, p in enumerate(q.parts, 1):
            ws4.cell(row=r, column=1, value=i).border = dash_border
            ws4.cell(row=r, column=2, value=p.category).border = dash_border
            ws4.cell(row=r, column=3, value=p.name).border = dash_border
            ws4.cell(row=r, column=4, value=p.qty).border = dash_border
            ws4.cell(row=r, column=5, value=_maybe_price(p.unit_price)).border = dash_border
            ws4.cell(row=r, column=6, value=_maybe_price(p.amount)).border = dash_border
            
            _set_cell(ws4.cell(row=r, column=1), font=normal, alignment=center)
            _set_cell(ws4.cell(row=r, column=2), font=bold, alignment=center)
            _set_cell(ws4.cell(row=r, column=3), font=normal, alignment=left)
            _set_cell(ws4.cell(row=r, column=4), font=normal, alignment=center)
            _set_cell(ws4.cell(row=r, column=5), font=normal, alignment=right, number_format="#,##0")
            _set_cell(ws4.cell(row=r, column=6), font=bold, alignment=right, number_format="#,##0")
            r += 1

        # =====================================================================
        # [폼 5] 퀵 리테일 영수증형 (Quick Receipt) - 매우 심플, 테두리 최소화
        # =====================================================================
        ws5 = _new_detail_sheet(f"{_sheet_name(q)[:24]} 폼5(리테일)")
        for letter, width in FORM5_COL_WIDTHS.items():
            ws5.column_dimensions[letter].width = width
            
        ws5["A1"] = "견 적 (ESTIMATE)"
        ws5.merge_cells("A1:F2")
        ws5["A1"].font = Font(name=FONT_NAME, size=22, bold=True, color="34495E")
        ws5["A1"].alignment = center
        
        ws5["A4"] = f"To: {cust_name} ({cust_mgr})"
        ws5.merge_cells("A4:C4")
        _set_cell(ws5["A4"], font=bold, alignment=left)
        
        ws5["E4"] = f"Date: {date_str}"
        ws5.merge_cells("E4:F4")
        _set_cell(ws5["E4"], font=normal, alignment=right)
        
        _write_validity_note(ws5, 5)  # 표 헤더(6행) 직전 빈 행

        thick_bot = Border(bottom=Side(style="thick", color="34495E"))
        ws5["A6"] = "Item Description"
        ws5.merge_cells("A6:C6")
        ws5.cell(row=6, column=1).border = thick_bot
        ws5.cell(row=6, column=2).border = thick_bot
        ws5.cell(row=6, column=3).border = thick_bot
        
        ws5["D6"] = "Qty"
        ws5.merge_cells("D6:E6")
        ws5.cell(row=6, column=4).border = thick_bot
        ws5.cell(row=6, column=5).border = thick_bot
        
        ws5["F6"] = "Amount"
        ws5.cell(row=6, column=6).border = thick_bot
        
        _set_cell(ws5["A6"], font=bold, alignment=left)
        _set_cell(ws5["D6"], font=bold, alignment=center)
        _set_cell(ws5["F6"], font=bold, alignment=right)
        
        r = 7
        thin_bot = Border(bottom=Side(style="hair", color="CCCCCC"))
        for p in q.parts:
            ws5.merge_cells(f"A{r}:C{r}")
            ws5[f"A{r}"] = f"[{p.category}] {p.name}"
            ws5.merge_cells(f"D{r}:E{r}")
            ws5[f"D{r}"] = p.qty
            ws5[f"F{r}"] = _maybe_price(p.amount)
            
            for c in range(1, 7):
                ws5.cell(row=r, column=c).border = thin_bot
                
            _set_cell(ws5[f"A{r}"], font=normal, alignment=left)
            _set_cell(ws5[f"D{r}"], font=normal, alignment=center)
            _set_cell(ws5[f"F{r}"], font=normal, alignment=right, number_format="#,##0")
            r += 1
            
        r += 1
        ws5.merge_cells(f"D{r}:E{r}")
        ws5[f"D{r}"] = "TOTAL"
        ws5[f"F{r}"] = q.total
        _set_cell(ws5[f"D{r}"], font=Font(name=FONT_NAME, size=14, bold=True), alignment=right)
        _set_cell(ws5[f"F{r}"], font=Font(name=FONT_NAME, size=14, bold=True, color="000080"), alignment=right, number_format="#,##0")
        
        r += 2
        ws5.merge_cells(f"A{r}:F{r}")
        ws5[f"A{r}"] = (
            f"{sup_name} / 담당: {sup_mgr} / Tel: {sup_phone}"
            + (f" / E-mail: {sup_email}" if sup_email else "")
        )
        _set_cell(ws5[f"A{r}"], font=normal, alignment=center)

        # =====================================================================
        # [폼 6] 조립PC 실무형 (Custom Built PC) - 조립컴퓨터_견적서_양식
        # =====================================================================
        ws6 = _new_detail_sheet(f"{_sheet_name(q)[:23]} 폼6(조립PC)")
        for letter, width in FORM6_COL_WIDTHS.items():
            ws6.column_dimensions[letter].width = width

        # A1:G1 타이틀
        ws6["A1"] = "조 립   컴 퓨 터   견 적 서"
        ws6.merge_cells("A1:G1")
        ws6.row_dimensions[1].height = 32.0
        ws6["A1"].fill = PatternFill("solid", fgColor="1F4E79")
        _set_cell(ws6["A1"], font=Font(name=FONT_NAME, size=16, bold=True, color="FFFFFF"), alignment=center)

        # 1. 견적 개요 및 판매자 정보
        ws6["A2"] = "1. 견적 개요 및 판매자 정보"
        ws6.merge_cells("A2:G2")
        ws6.row_dimensions[2].height = 22.0
        ws6["A2"].fill = PatternFill("solid", fgColor="D9E1F2")
        _set_cell(ws6["A2"], font=Font(name=FONT_NAME, size=11, bold=True), alignment=left)

        info_header_fill = PatternFill("solid", fgColor="F2F2F2")
        ws6.row_dimensions[3].height = 20.0
        ws6.row_dimensions[4].height = 20.0
        ws6.row_dimensions[5].height = 20.0
        ws6.row_dimensions[6].height = 20.0

        # Row 3
        ws6["A3"] = "견적 일자"
        ws6["B3"] = date_str
        ws6.merge_cells("B3:C3")
        ws6["D3"] = "상 호 명"
        ws6["E3"] = sup_name
        ws6.merge_cells("E3:G3")

        # Row 4
        ws6["A4"] = "고 객 명"
        ws6["B4"] = cust_name
        ws6.merge_cells("B4:C4")
        ws6["D4"] = "대표자명"
        ws6["E4"] = sup_mgr
        ws6.merge_cells("E4:G4")

        # Row 5
        ws6["A5"] = "연 락 처"
        ws6["B5"] = cust_mgr or ""
        ws6.merge_cells("B5:C5")
        ws6["D5"] = "연 락 처"
        ws6["E5"] = f"{sup_phone} / {sup_email}".strip(" /") if sup_email else sup_phone
        ws6.merge_cells("E5:G5")

        # Row 6
        ws6["A6"] = "용    도"
        ws6["B6"] = "[  ] 게이밍   [  ] 사무/인터넷   [  ] 영상편집/작업"
        ws6.merge_cells("B6:C6")
        ws6["D6"] = "매장주소"
        ws6["E6"] = "인천광역시 동구 방축로 37번길 30, 35동 131호"
        ws6.merge_cells("E6:G6")

        for rn in range(3, 7):
            for col in range(1, 8):
                c = ws6.cell(row=rn, column=col)
                c.border = border
                if col in (1, 4):
                    c.fill = info_header_fill
                    _set_cell(c, font=bold, alignment=center)
                else:
                    _set_cell(c, font=normal, alignment=left)

        # 2. 조립 부품 세부 견적 내역
        ws6["A7"] = "2. 조립 부품 세부 견적 내역"
        ws6.merge_cells("A7:G7")
        ws6.row_dimensions[7].height = 22.0
        ws6["A7"].fill = PatternFill("solid", fgColor="D9E1F2")
        _set_cell(ws6["A7"], font=Font(name=FONT_NAME, size=11, bold=True), alignment=left)

        headers6 = ["부품 구분", "제조사 / 제품명 (상세 스펙)", "수량", "단가 (원)", "금액 (원)", "무상 A/S", "비고"]
        ws6.row_dimensions[8].height = 22.0
        for i, h in enumerate(headers6, 1):
            c = ws6.cell(row=8, column=i, value=h)
            c.fill = info_header_fill
            _set_cell(c, font=bold, border=border, alignment=center)

        r6 = 9
        for p in q.parts:
            as_text = _as_warranty_period(p.category)
            remark_text = _spec_remark(p.category, p.name)
            ws6.cell(row=r6, column=1, value=p.category)
            ws6.cell(row=r6, column=2, value=p.name)
            ws6.cell(row=r6, column=3, value=p.qty if p.qty > 0 else 1)
            ws6.cell(row=r6, column=4, value=_maybe_price(p.unit_price))
            ws6.cell(row=r6, column=5, value=_maybe_price(p.amount))
            ws6.cell(row=r6, column=6, value=as_text)
            ws6.cell(row=r6, column=7, value=remark_text)

            _set_cell(ws6.cell(row=r6, column=1), font=normal, border=border, alignment=center)
            _set_cell(ws6.cell(row=r6, column=2), font=normal, border=border, alignment=left)
            _set_cell(ws6.cell(row=r6, column=3), font=normal, border=border, alignment=center)
            _set_cell(ws6.cell(row=r6, column=4), font=normal, border=border, alignment=right, number_format="#,##0")
            _set_cell(ws6.cell(row=r6, column=5), font=normal, border=border, alignment=right, number_format="#,##0")
            _set_cell(ws6.cell(row=r6, column=6), font=normal, border=border, alignment=center)
            _set_cell(ws6.cell(row=r6, column=7), font=normal, border=border, alignment=center)
            ws6.row_dimensions[r6].height = 20.0
            r6 += 1

        # 사용자 빈 행 추가
        for _ in range(USER_BLANK_ROWS):
            for col in range(1, 8):
                c = ws6.cell(row=r6, column=col)
                _set_cell(c, font=normal, border=border, alignment=center)
            ws6.row_dimensions[r6].height = 20.0
            r6 += 1

        # 합계 행
        ws6.merge_cells(f"A{r6}:D{r6}")
        ws6[f"A{r6}"] = "합 계 금 액 (VAT 포함)"
        ws6.merge_cells(f"E{r6}:G{r6}")
        ws6[f"E{r6}"] = q.total
        ws6.row_dimensions[r6].height = 24.0
        for col in range(1, 8):
            c = ws6.cell(row=r6, column=col)
            c.fill = PatternFill("solid", fgColor="FFF2CC")
            c.border = border
        _set_cell(ws6[f"A{r6}"], font=Font(name=FONT_NAME, size=11, bold=True), alignment=center)
        _set_cell(ws6[f"E{r6}"], font=Font(name=FONT_NAME, size=12, bold=True), alignment=right, number_format="₩ #,##0")
        r6 += 1

        # 3. 유의사항 및 안내사항
        ws6[f"A{r6}"] = "3. 유의사항 및 안내사항"
        ws6.merge_cells(f"A{r6}:G{r6}")
        ws6.row_dimensions[r6].height = 22.0
        ws6[f"A{r6}"].fill = PatternFill("solid", fgColor="D9E1F2")
        _set_cell(ws6[f"A{r6}"], font=Font(name=FONT_NAME, size=11, bold=True), alignment=left)
        r6 += 1

        notices6 = [
            "1) 본 견적서는 발행일로부터 3일간 유효하며, 부품 시장 시세 변동에 따라 단가가 변경될 수 있습니다.",
            "2) 모든 부품은 100% 정품/새제품만 사용하며, 조립 완료 후 남은 정품 박스 및 구성품은 모두 함께 제공해 드립니다.",
            "3) 무상 A/S 기간은 부품 제조사/수입사 기준이며, 매장 자체 초기불량 보증 기간은 구매일로부터 14일입니다.",
            "4) 결제 방식: 현금영수증 및 세금계산서 발행 가능 (계좌입금 / 카드결제 동일)",
        ]
        for note in notices6:
            ws6[f"A{r6}"] = note
            ws6.merge_cells(f"A{r6}:G{r6}")
            _set_cell(ws6[f"A{r6}"], font=Font(name=FONT_NAME, size=9), alignment=left)
            ws6.row_dimensions[r6].height = 18.0
            r6 += 1

        # =====================================================================
        # [폼 7] 데스크탑 상세 스펙형 (Desktop Spec Proposal) - 기본 견적서_데스크탑1
        # =====================================================================
        ws7 = _new_detail_sheet(f"{_sheet_name(q)[:22]} 폼7(데스크탑)")
        for letter, width in FORM7_COL_WIDTHS.items():
            ws7.column_dimensions[letter].width = width

        ws7["A1"] = "견     적     서"
        ws7.merge_cells("A1:F1")
        ws7.row_dimensions[1].height = 28.0
        _set_cell(ws7["A1"], font=Font(name=FONT_NAME, size=20, bold=True, color="1F4E79"), alignment=center)

        ws7["A2"] = "Estimate  |  Philippians 4:13  I can do all this through him who gives me strength."
        ws7.merge_cells("A2:F2")
        ws7.row_dimensions[2].height = 16.0
        _set_cell(ws7["A2"], font=Font(name=FONT_NAME, size=9, italic=True, color="555555"), alignment=center)

        # 공급자 정보 테이블
        ws7["A4"] = f"견적일 : {date_str}"
        ws7["A5"] = f"수  신 : {cust_name} ({cust_mgr})"
        ws7["A6"] = "아래와 같이 견적하여 드립니다."
        ws7.merge_cells("A4:C4")
        ws7.merge_cells("A5:C5")
        ws7.merge_cells("A6:C6")
        _set_cell(ws7["A4"], font=bold, alignment=left)
        _set_cell(ws7["A5"], font=Font(name=FONT_NAME, size=11, bold=True), alignment=left)
        _set_cell(ws7["A6"], font=normal, alignment=left)

        ws7["D4"] = "등록번호"
        ws7["E4"] = "121-18-24250"
        ws7.merge_cells("E4:F4")
        ws7["D5"] = "상  호"
        ws7["E5"] = f"{sup_name} (대표: {sup_mgr})"
        ws7.merge_cells("E5:F5")
        ws7["D6"] = "사업장/연락처"
        ws7["E6"] = (
            f"{sup_phone} / {sup_email} / 인천 동구 방축로 37길 30"
            if sup_email
            else f"{sup_phone} / 인천 동구 방축로 37길 30"
        )
        ws7.merge_cells("E6:F6")

        for rn in range(4, 7):
            ws7.row_dimensions[rn].height = 20.0
            for col in range(4, 7):
                c = ws7.cell(row=rn, column=col)
                c.border = border
                if col == 4:
                    c.fill = PatternFill("solid", fgColor="E7E6E6")
                    _set_cell(c, font=bold, alignment=center)
                else:
                    _set_cell(c, font=normal, alignment=left)

        # 메인 요약표
        ws7.row_dimensions[8].height = 22.0
        sum_headers = ["No", "구분/품명", "상세 모델명", "수량", "공급가(원)", "합계금액(VAT포함)"]
        sum_h_fill = PatternFill("solid", fgColor="61D1C6")
        for i, h in enumerate(sum_headers, 1):
            c = ws7.cell(row=8, column=i, value=h)
            c.fill = sum_h_fill
            _set_cell(c, font=Font(name=FONT_NAME, size=10, bold=True, color="FFFFFF"), border=border, alignment=center)

        supply_price = int(round(q.total / 1.1))
        ws7.row_dimensions[9].height = 24.0
        ws7.cell(row=9, column=1, value=1)
        ws7.cell(row=9, column=2, value="데스크탑 컴퓨터")
        ws7.cell(row=9, column=3, value=q.model or q.tier)
        ws7.cell(row=9, column=4, value=1)
        ws7.cell(row=9, column=5, value=supply_price)
        ws7.cell(row=9, column=6, value=q.total)

        _set_cell(ws7.cell(row=9, column=1), font=normal, border=border, alignment=center)
        _set_cell(ws7.cell(row=9, column=2), font=bold, border=border, alignment=center)
        _set_cell(ws7.cell(row=9, column=3), font=normal, border=border, alignment=left)
        _set_cell(ws7.cell(row=9, column=4), font=normal, border=border, alignment=center)
        _set_cell(ws7.cell(row=9, column=5), font=normal, border=border, alignment=right, number_format="#,##0")
        _set_cell(ws7.cell(row=9, column=6), font=bold, border=border, alignment=right, number_format="#,##0")

        # 합계 행
        ws7.merge_cells("A10:E10")
        ws7["A10"] = "합   계 (VAT 포함)"
        ws7["F10"] = q.total
        ws7.row_dimensions[10].height = 24.0
        for col in range(1, 7):
            c = ws7.cell(row=10, column=col)
            c.fill = PatternFill("solid", fgColor="FFF2CC")
            c.border = border
        _set_cell(ws7["A10"], font=Font(name=FONT_NAME, size=11, bold=True), alignment=center)
        _set_cell(ws7["F10"], font=Font(name=FONT_NAME, size=12, bold=True), alignment=right, number_format="₩ #,##0")

        # 조건 안내
        ws7["A11"] = "■ 납품/보증: 발주 후 3~7일 이내 납품 | 모든 신품 하드웨어 1년 무상 보증 (당사 구매 제품 무상 점검 지원)"
        ws7.merge_cells("A11:F11")
        ws7.row_dimensions[11].height = 20.0
        _set_cell(ws7["A11"], font=Font(name=FONT_NAME, size=9, bold=True, color="333333"), alignment=left)

        # 제품 상세 SPEC 표 헤더
        ws7["A13"] = "【 제 품  상 세  S P E C 】"
        ws7.merge_cells("A13:F13")
        ws7.row_dimensions[13].height = 22.0
        ws7["A13"].fill = PatternFill("solid", fgColor="2E75B6")
        _set_cell(ws7["A13"], font=Font(name=FONT_NAME, size=11, bold=True, color="FFFFFF"), alignment=center)

        spec_headers = ["No", "구 분", "부품 및 상세 사양", "수량", "금액", "비고"]
        ws7.row_dimensions[14].height = 20.0
        for i, h in enumerate(spec_headers, 1):
            c = ws7.cell(row=14, column=i, value=h)
            c.fill = PatternFill("solid", fgColor="D9E1F2")
            _set_cell(c, font=bold, border=border, alignment=center)

        r7 = 15
        for i, p in enumerate(q.parts, 1):
            ws7.cell(row=r7, column=1, value=i)
            ws7.cell(row=r7, column=2, value=p.category)
            ws7.cell(row=r7, column=3, value=p.name)
            ws7.cell(row=r7, column=4, value=p.qty if p.qty > 0 else 1)
            ws7.cell(row=r7, column=5, value=_maybe_price(p.amount))
            ws7.cell(row=r7, column=6, value=_spec_remark(p.category, p.name))

            _set_cell(ws7.cell(row=r7, column=1), font=normal, border=border, alignment=center)
            _set_cell(ws7.cell(row=r7, column=2), font=bold, border=border, alignment=center)
            _set_cell(ws7.cell(row=r7, column=3), font=normal, border=border, alignment=left)
            _set_cell(ws7.cell(row=r7, column=4), font=normal, border=border, alignment=center)
            _set_cell(ws7.cell(row=r7, column=5), font=normal, border=border, alignment=right, number_format="#,##0")
            _set_cell(ws7.cell(row=r7, column=6), font=normal, border=border, alignment=center)
            ws7.row_dimensions[r7].height = 20.0
            r7 += 1

        # 발주확인란
        r7 += 1
        ws7.merge_cells(f"A{r7}:F{r7}")
        ws7[f"A{r7}"] = "※ 제품 발주시 아래 발주확인 란 작성 후 회신 바랍니다."
        _set_cell(ws7[f"A{r7}"], font=Font(name=FONT_NAME, size=9, bold=True), alignment=left)
        r7 += 1

        ws7.merge_cells(f"A{r7}:B{r7}")
        ws7[f"A{r7}"] = "발주 일자 :        년    월    일"
        ws7.merge_cells(f"C{r7}:D{r7}")
        ws7[f"C{r7}"] = f"상호/성명 : {cust_name}"
        ws7.merge_cells(f"E{r7}:F{r7}")
        ws7[f"E{r7}"] = "서명/직인 :             (인)"
        for col in (1, 3, 5):
            _set_cell(ws7.cell(row=r7, column=col), font=normal, alignment=left)
        ws7.row_dimensions[r7].height = 22.0
        r7 += 1

        # 푸터 연락처
        ws7.merge_cells(f"A{r7}:F{r7}")
        ws7[f"A{r7}"] = (
            f"영업담당: {sup_mgr} / Tel: {sup_phone}"
            + (f" / E-mail: {sup_email}" if sup_email else "")
        )
        _set_cell(ws7[f"A{r7}"], font=Font(name=FONT_NAME, size=9, color="666666"), alignment=center)
        ws7.row_dimensions[r7].height = 18.0

        # =====================================================================
        # [폼 8] 클래식 B2B 견적서 (Classic Grid & Business License) - 기본 견적서
        # =====================================================================
        ws8 = _new_detail_sheet(f"{_sheet_name(q)[:22]} 폼8(클래식)")
        for letter, width in FORM8_COL_WIDTHS.items():
            ws8.column_dimensions[letter].width = width

        ws8["A1"] = "견    적    서"
        ws8.merge_cells("A1:F1")
        ws8.row_dimensions[1].height = 32.0
        _set_cell(ws8["A1"], font=Font(name=FONT_NAME, size=22, bold=True, color="1F4E79"), alignment=center)

        # 좌측 고객 정보 & 우측 공급자 세로 표 (Row 3 to 6)
        ws8["A3"] = "상  호"
        ws8["B3"] = cust_name
        ws8.merge_cells("B3:C3")
        ws8["A4"] = "내  용"
        ws8["B4"] = q.model or "컴퓨터 견적 외"
        ws8.merge_cells("B4:C4")
        ws8["A5"] = "담  당"
        ws8["B5"] = f"{cust_mgr} 귀하"
        ws8.merge_cells("B5:C5")
        ws8["A6"] = "일  자"
        ws8["B6"] = date_str
        ws8.merge_cells("B6:C6")

        for rn in range(3, 7):
            ws8.cell(row=rn, column=1).border = border
            ws8.cell(row=rn, column=2).border = border
            ws8.cell(row=rn, column=3).border = border
            ws8.cell(row=rn, column=1).fill = PatternFill("solid", fgColor="F2F2F2")
            _set_cell(ws8.cell(row=rn, column=1), font=bold, alignment=center)
            _set_cell(ws8.cell(row=rn, column=2), font=normal, alignment=left)

        # 우측 공급자 박스 (D3:D6 병합)
        ws8.merge_cells("D3:D6")
        ws8["D3"] = "공\n\n급\n\n자"
        _set_cell(ws8["D3"], font=bold, border=border, alignment=center)
        ws8["D3"].fill = PatternFill("solid", fgColor="E7E6E6")

        sup_addr = (info.get("supplier_address") or "서울시").strip()
        sup_rows = [
            ("등록번호", "121-18-24250"),
            ("상    호", sup_name),
            ("사업장", sup_addr),
            (
                "연락처/이메일" if sup_email else "연락처/FAX",
                (
                    f"{sup_phone} / {sup_email} / FAX 0504-282-9692"
                    if sup_email
                    else f"{sup_phone} / 0504-282-9692"
                ),
            ),
        ]
        for idx, (lbl, val) in enumerate(sup_rows, 3):
            ws8[f"E{idx}"] = lbl
            ws8[f"F{idx}"] = val
            ws8.cell(row=idx, column=5).border = border
            ws8.cell(row=idx, column=6).border = border
            ws8.cell(row=idx, column=5).fill = PatternFill("solid", fgColor="F2F2F2")
            _set_cell(ws8[f"E{idx}"], font=bold, alignment=center)
            _set_cell(ws8[f"F{idx}"], font=normal, alignment=left)
            ws8.row_dimensions[idx].height = 20.0

        # Row 7: VAT 포함 합계금액 배너
        ws8.merge_cells("A7:C7")
        ws8["A7"] = "합계금액 (VAT 포함)"
        ws8.merge_cells("D7:F7")
        ws8["D7"] = q.total
        ws8.row_dimensions[7].height = 26.0
        for col in range(1, 7):
            c = ws8.cell(row=7, column=col)
            c.border = border
            if col <= 3:
                c.fill = PatternFill("solid", fgColor="E7E6E6")
            else:
                c.fill = PatternFill("solid", fgColor="FFF2CC")
        _set_cell(ws8["A7"], font=Font(name=FONT_NAME, size=11, bold=True), alignment=center)
        _set_cell(ws8["D7"], font=Font(name=FONT_NAME, size=14, bold=True, color="1F4E79"), alignment=right, number_format="₩ #,##0")

        # Row 8: 표 헤더
        headers8 = ["NO", "품  목", "규격 및 상세 사양", "수 량", "단  가", "금  액"]
        ws8.row_dimensions[8].height = 22.0
        for i, h in enumerate(headers8, 1):
            c = ws8.cell(row=8, column=i, value=h)
            c.fill = PatternFill("solid", fgColor="1F4E79")
            _set_cell(c, font=Font(name=FONT_NAME, size=10, bold=True, color="FFFFFF"), border=border, alignment=center)

        r8 = 9
        for i, p in enumerate(q.parts, 1):
            ws8.cell(row=r8, column=1, value=i)
            ws8.cell(row=r8, column=2, value=p.category)
            ws8.cell(row=r8, column=3, value=p.name)
            ws8.cell(row=r8, column=4, value=p.qty if p.qty > 0 else 1)
            ws8.cell(row=r8, column=5, value=_maybe_price(p.unit_price))
            ws8.cell(row=r8, column=6, value=_maybe_price(p.amount))

            _set_cell(ws8.cell(row=r8, column=1), font=normal, border=border, alignment=center)
            _set_cell(ws8.cell(row=r8, column=2), font=bold, border=border, alignment=center)
            _set_cell(ws8.cell(row=r8, column=3), font=normal, border=border, alignment=left)
            _set_cell(ws8.cell(row=r8, column=4), font=normal, border=border, alignment=center)
            _set_cell(ws8.cell(row=r8, column=5), font=normal, border=border, alignment=right, number_format="#,##0")
            _set_cell(ws8.cell(row=r8, column=6), font=bold, border=border, alignment=right, number_format="#,##0")
            ws8.row_dimensions[r8].height = 20.0
            r8 += 1

        # 최소 15행까지 빈 그리드 행 채우기 (클래식 양식)
        target_grid_rows = max(len(q.parts) + 2, 15)
        for blank_idx in range(len(q.parts) + 1, target_grid_rows + 1):
            for col in range(1, 7):
                c = ws8.cell(row=r8, column=col)
                if col == 1:
                    c.value = blank_idx
                _set_cell(c, font=normal, border=border, alignment=center if col in (1, 4) else (left if col in (2, 3) else right))
            ws8.row_dimensions[r8].height = 18.0
            r8 += 1

        # TOTAL 행
        ws8.merge_cells(f"A{r8}:E{r8}")
        ws8[f"A{r8}"] = "TOTAL (부가세 포함)"
        ws8[f"F{r8}"] = q.total
        ws8.row_dimensions[r8].height = 24.0
        for col in range(1, 7):
            c = ws8.cell(row=r8, column=col)
            c.fill = PatternFill("solid", fgColor="FFF2CC")
            c.border = border
        _set_cell(ws8[f"A{r8}"], font=Font(name=FONT_NAME, size=11, bold=True), alignment=center)
        _set_cell(ws8[f"F{r8}"], font=Font(name=FONT_NAME, size=12, bold=True), alignment=right, number_format="₩ #,##0")
        r8 += 1

        # 첨부사항
        ws8.merge_cells(f"A{r8}:F{r8}")
        ws8[f"A{r8}"] = "■ 첨부 및 안내사항"
        _set_cell(ws8[f"A{r8}"], font=bold, alignment=left)
        r8 += 1

        notices8 = [
            "1. 납품일자 : 기간 협의                         | 2. 유효기간 : 견적일로부터 7일",
            "3. 결제조건 : 선결제 (세금계산서 발행)",
        ]
        for note in notices8:
            ws8.merge_cells(f"A{r8}:F{r8}")
            ws8[f"A{r8}"] = note
            _set_cell(ws8[f"A{r8}"], font=Font(name=FONT_NAME, size=9), alignment=left)
            ws8.row_dimensions[r8].height = 18.0
            r8 += 1

    # 모든 내용을 채운 뒤에 행 높이를 맞춘다(열 너비·병합이 확정된 상태여야 한다).
    for ws in wb.worksheets:
        _auto_fit_row_heights(ws)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
