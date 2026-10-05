# -*- coding: utf-8 -*-
"""중고 시세 비교용 공개 검색 링크. 외부 사이트를 수집하지 않는다."""
from __future__ import annotations

import re
import urllib.parse

def _is_apple(name: str) -> bool:
    name = name.lower()
    return any(x in name for x in ["macbook", "mac mini", "imac", "mac studio", "mac pro", "apple"])
from app.daangn_search import normalize_daangn_keyword

BUNJANG_SEARCH = "https://m.bunjang.co.kr/search/products?order=score&q={query}"
JOONGNA_SEARCH = "https://web.joongna.com/search/{query}"

_PAREN_RE = re.compile(r"\(([^)]*)\)")
_HANGUL_RE = re.compile(r"[가-힣]")
_CPU_X3D_RE = re.compile(r"\b(\d{4,5}\s*X3D)\b", re.I)
_CPU_INTEL_I_RE = re.compile(r"\b(i[3579]\s*-\s*\d{4,5}[A-Z]{0,3})\b", re.I)
# 컴퓨존은 '울트라5-225F', 'Ultra 5 225F' 둘 다 쓴다. F/KF/H/HX/V 접미사 유지.
_CPU_ULTRA_RE = re.compile(
    r"(?:Ultra|울트라)\s*([3579])\s*-?\s*(\d{3}(?:KF|HX|HK|HL|KS|[A-Z]{1,2})?)",
    re.I,
)
_CPU_ULTRA_CHIP_RE = re.compile(
    r"\b(\d{3}(?:KF|HX|HK|HL|KS|F|K|H|U|V|T))\b",
    re.I,
)
_CPU_CHIP_RE = re.compile(r"\b(\d{4,5}(?:X3D|[A-Z]{1,2}))\b", re.I)
_GPU_NVIDIA_RE = re.compile(
    r"\b((?:RTX|GTX)\s*\d{3,4}(?:\s*Ti)?(?:\s*SUPER)?)\b",
    re.I,
)
_GPU_AMD_RE = re.compile(r"\b(RX\s*\d{3,4}(?:\s*(?:XTX|XT|GRE))?)\b", re.I)
_GPU_ARC_RE = re.compile(r"\b(Arc\s*[AB]?\d{3,4})\b", re.I)
_IGPU_RE = re.compile(
    r"내장\s*그래픽|온보드|UHD\s*Graphics|Iris|Radeon\s+Graphics",
    re.I,
)


def build_bunjang_search_url(keyword: str) -> str:
    return BUNJANG_SEARCH.format(query=urllib.parse.quote(str(keyword or "").strip()))





def build_joongna_search_url(keyword: str) -> str:
    return JOONGNA_SEARCH.format(
        query=urllib.parse.quote(str(keyword or "").strip(), safe="")
    )


def _norm_chip(text: str) -> str:
    token = re.sub(r"\s+", " ", (text or "").strip())
    token = token.upper()
    return re.sub(r"\bTI\b", "Ti", token)


def short_cpu_name(name: str) -> str:
    """컴퓨존 긴 CPU 품명을 중고몰에서 통하는 칩명만 남긴다."""
    text = (name or "").strip()
    if not text:
        return ""
    match = _CPU_X3D_RE.search(text)
    if match:
        return match.group(1).replace(" ", "").upper()
    match = _CPU_INTEL_I_RE.search(text)
    if match:
        return re.sub(r"\s+", "", match.group(1))
    match = _CPU_ULTRA_RE.search(text)
    if match:
        return f"Ultra {match.group(1)} {match.group(2).replace(' ', '').upper()}"
    match = _CPU_ULTRA_CHIP_RE.search(_PAREN_RE.sub(" ", text))
    if match:
        return match.group(1).upper()
    match = _CPU_CHIP_RE.search(_PAREN_RE.sub(" ", text))
    if match:
        return match.group(1).upper()
    return ""


def short_gpu_name(name: str) -> str:
    """컴퓨존 긴 그래픽카드 품명을 RTX/RX 칩명만 남긴다. 내장은 버린다."""
    text = (name or "").strip()
    if not text:
        return ""
    if _IGPU_RE.search(text) and not _GPU_NVIDIA_RE.search(text) and not _GPU_AMD_RE.search(text):
        return ""
    match = _GPU_NVIDIA_RE.search(text)
    if match:
        return _norm_chip(match.group(1))
    match = _GPU_AMD_RE.search(text)
    if match:
        return _norm_chip(match.group(1))
    match = _GPU_ARC_RE.search(text)
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip()
    return ""


def _parts_from_model_paren(model: str) -> tuple[str, str]:
    """모델명 괄호의 CPU/GPU 표기를 폴백으로 쓴다."""
    match = _PAREN_RE.search(model or "")
    if not match:
        return "", ""
    tokens = [p.strip() for p in match.group(1).split("/") if p.strip()]
    keep = [
        p for p in tokens
        if not _HANGUL_RE.search(p) or short_cpu_name(p) or short_gpu_name(p)
    ]
    if not keep:
        return "", ""
    cpu = short_cpu_name(keep[0]) or keep[0]
    gpu = ""
    if len(keep) > 1:
        gpu = short_gpu_name(keep[1]) or keep[1]
    return cpu, gpu


def used_pc_keyword(model: str = "", cpu: str = "", gpu: str = "") -> str:
    """조립PC는 CPU+그래픽카드, Apple은 기존 모델 키워드."""
    model = (model or "").strip()
    if model and _is_apple(model):
        return normalize_daangn_keyword(model)

    cpu_s = short_cpu_name(cpu)
    gpu_s = short_gpu_name(gpu)
    if not cpu_s or not gpu_s:
        fb_cpu, fb_gpu = _parts_from_model_paren(model)
        cpu_s = cpu_s or fb_cpu
        gpu_s = gpu_s or fb_gpu
    keyword = " ".join(part for part in (cpu_s, gpu_s) if part)
    return re.sub(r"\s+", " ", keyword).strip()


def used_market_links(model: str = "", cpu: str = "", gpu: str = "") -> dict[str, str]:
    keyword = used_pc_keyword(model, cpu=cpu, gpu=gpu)
    if not keyword:
        raise ValueError("검색어가 비어 있습니다.")
    return {
        "keyword": keyword,
        "bunjang_url": build_bunjang_search_url(keyword),
        "joongna_url": build_joongna_search_url(keyword),
    }
