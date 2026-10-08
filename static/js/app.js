const fmt = (n) => n.toLocaleString("ko-KR") + "원";

const tierClass = {
  entry: "tier-entry",
  standard: "tier-standard",
  pro: "tier-pro",
  expert: "tier-expert",
  "expert-5080": "tier-expert",
  "expert-5090": "tier-expert-5090",
  premium: "tier-expert",
  iworks: "tier-standard",
  recommend: "tier-pro",
  "game-s": "tier-standard",
  "game-p": "tier-expert",
  price: "tier-standard",
};

let currentStore = "compuzone"; // "compuzone" | "apple"
let quoteData = null;
let activeTab = 0;
let currentCat = "ai";
let includeMonitor = false;
let includeKeyboard = false; // 컴퓨존 기본: 무선키보드 제외
let includeRam32 = false; // AI PC 기본: 컴퓨존과 동일(메모리 ×1), 켜면 32GB(×2)
let includeSetup = false; // 기본: 초기 세팅비 제외 (모니터/키보드와 동일)
let showUnitPrices = true; // 기본: 품목별 단가·금액 표시
const CONSUMER_MARGIN_PERCENT = 30;
let quoteMarginPercent = CONSUMER_MARGIN_PERCENT; // 컴퓨존 기본: 총액의 30% 소비자견적
let hasLoadedOnce = false;
let quoteInfo = null; // 견적처/공급자 정보 (서버 저장)
let currentTrId = null; // 현재 작업내역서 ID

// 게임으로 PC 찾기
let gamePcMeta = null;
let selectedGames = []; // [{id, title}]
let selectedResolution = "";
let gameWizardStep = 1;
let lastGameQuery = null; // {games, titles, resolution}

// 금액대로 찾기
let lastPriceQuery = null; // {min_price, max_price, type} 원 단위
let priceTargetType = "assembled"; // 금액대로 찾기 대상: "assembled" | "notebook"
let resultView = "card"; // "card" | "list"

// CTO 관련 상태
let activeCtoTierKey = null;
let activeCtoIndex = null;
let currentCtoOptions = [];
let baseQuoteForCto = null;
let ctoLive = null; // 공식몰 구성하기 API 응답 {options, total, base_total, selected, parts}. 없으면 비교 옵션 방식

function isAppleQuote(q) {
  return currentStore === "apple" || (q && String(q.price_source || "").startsWith("apple"));
}

function hasLiveCto(q) {
  if (q?.cto_live) return true;
  const groups = q?.cto_groups;
  return Array.isArray(groups) && groups.some((g) => (g.options || g.choices || []).length >= 2);
}

function isMacCategory(cat) {
  return ["apple_macbook", "apple_imac", "apple_macmini", "apple_macstudio", "apple_all"].includes(cat);
}

function ceilToManwon(n) {
  const v = Number(n) || 0;
  if (v === 0) return 0;
  const unit = 10000;
  if (v > 0) return Math.ceil(v / unit) * unit;
  return -Math.ceil(Math.abs(v) / unit) * unit;
}

function shouldCeilManwon() {
  if (currentStore === "apple") return false;
  return currentStore === "compuzone" && quoteMarginPercent > 0;
}

function displayAmount(n, q) {
  const v = Number(n) || 0;
  if (currentStore === "apple" || isAppleQuote(q)) return v;
  return shouldCeilManwon() ? ceilToManwon(v) : v;
}

function activeMarginPercent() {
  return currentStore === "compuzone" ? quoteMarginPercent : 0;
}

function markedUpAmount(n, percent) {
  const v = Number(n) || 0;
  return percent > 0 ? Math.round(v * (100 + percent) / 100) : v;
}

function displayTotal(q) {
  if (isAppleQuote(q)) return Number(q.total) || 0;
  const base = markedUpAmount(q.total, activeMarginPercent());
  return shouldCeilManwon() ? ceilToManwon(base) : base;
}

/**
 * 표시용 품목 목록. 마진을 합계뿐 아니라 품목 단가에도 배분하고,
 * 만원 단위 올림으로 생긴 잔액은 번들할인 행이 흡수해 품목합 = 합계를 맞춘다.
 * Apple 정품 견적은 올림/반올림 없이 공홈 단가 그대로 표시한다.
 */
function displayParts(q) {
  if (isAppleQuote(q)) {
    return q.parts.map((p) => {
      const unit = Number(p.unit_price) || 0;
      return { ...p, unit_price: unit, amount: p.qty > 0 ? unit * p.qty : 0 };
    });
  }
  const pct = activeMarginPercent();
  const rows = q.parts.map((p) => {
    const unit = displayAmount(markedUpAmount(p.unit_price, pct));
    return { ...p, unit_price: unit, amount: p.qty > 0 ? unit * p.qty : 0 };
  });

  const diff = displayTotal(q) - rows.reduce((sum, r) => sum + r.amount, 0);
  if (diff !== 0) {
    for (let i = rows.length - 1; i >= 0; i--) {
      if (rows[i].category !== "할인") continue;
      rows[i].amount += diff;
      rows[i].unit_price = rows[i].amount;
      break;
    }
  }
  return rows;
}

function marginKindLabel(m) {
  if (m === CONSUMER_MARGIN_PERCENT) return "소비자견적";
  if (m === 0) return "원가견적";
  return `마진 ${m}% 견적`;
}

function badgeClass(tierKey) {
  const key = String(tierKey);
  if (key.startsWith("macbook") || key.startsWith("imac") ||
      key.startsWith("macmini") || key.startsWith("macstudio") ||
      key.startsWith("ipad") || key.startsWith("iphone") ||
      key.startsWith("apple")) {
    return "tier-apple";
  }
  if (key.startsWith("game-s")) return tierClass["game-s"];
  if (key.startsWith("game-p")) return tierClass["game-p"];
  if (key.startsWith("price")) return tierClass.price;
  return tierClass[key] || tierClass[key.split("-")[0]] || "";
}

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const LOADING_TEXT_COMPUZONE = "컴퓨존에서 실시간 견적 수집 중…";
const LOADING_TEXT_APPLE = "애플 공식홈페이지에서 검색 중…";

function setLoadingMessage(text) {
  const el = $("#loading")?.querySelector("p");
  if (el) el.textContent = text;
}

function loadingMessageFor(cat) {
  const key = String(cat || "");
  if (currentStore === "apple" || key.startsWith("apple_") || key === "apple") {
    return LOADING_TEXT_APPLE;
  }
  return LOADING_TEXT_COMPUZONE;
}

function updateEmptyStateCopy() {
  const p = $("#emptyState p");
  if (!p) return;
  if (currentStore === "apple") {
    p.innerHTML =
      "상단 <strong>MacBook · iMac · Mac mini · Mac Studio · iPad & iPhone</strong> 버튼을 눌러<br />애플 공식홈페이지에서 현재 판매 제품을 불러오세요.";
  } else {
    p.innerHTML =
      "상단 <strong>AI PC · 프리미엄PC · 아이웍스PC · 추천조립PC · 게임으로PC찾기 · 금액대로 찾기</strong> 버튼을 눌러<br />컴퓨존 실시간 견적을 불러오세요.";
  }
}

function escapeHtml(str) {
  if (str == null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function show(el) {
  if (!el) return;
  el.classList.remove("hidden");
  el.setAttribute("aria-hidden", "false");
}
function hide(el) {
  if (!el) return;
  el.classList.add("hidden");
  el.setAttribute("aria-hidden", "true");
}

// 우측 하단 작은 처리 상태 창 (Excel 생성·CTO 옵션·시세 검색·업데이트 대기 등)
let busyDepth = 0;
function showBusy(text) {
  busyDepth += 1;
  const t = $("#busyText");
  if (t) t.textContent = text;
  show($("#busyBox"));
}
function setBusyText(text) {
  const t = $("#busyText");
  if (t) t.textContent = text;
}
function hideBusy() {
  busyDepth = Math.max(0, busyDepth - 1);
  if (!busyDepth) hide($("#busyBox"));
}

/** 서버에서 파일을 받아 저장. 생성하는 동안 처리 상태 창을 보여 준다. */
async function downloadWithBusy(url, options = {}, fallbackName = "download.xlsx", label = "Excel 파일 생성 중…") {
  showBusy(label);
  try {
    const res = await fetch(url, options);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `파일 생성 실패 (${res.status})`);
    }
    const cd = res.headers.get("Content-Disposition") || "";
    const star = cd.match(/filename\*=UTF-8''([^;]+)/i);
    const plain = cd.match(/filename="([^"]+)"/i);
    const name = star ? decodeURIComponent(star[1]) : plain ? plain[1] : fallbackName;
    const blob = await res.blob();
    const href = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = href;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(href), 1000);
  } catch (e) {
    alert("다운로드 오류: " + e.message);
  } finally {
    hideBusy();
  }
}

function openCoupangSearch(keyword, event) {
  if (event) {
    event.preventDefault();
    event.stopPropagation();
  }
  const q = String(keyword || "").trim();
  if (!q) return;

  const fallback = `https://www.coupang.com/np/search?component=&q=${encodeURIComponent(q)}&channel=user`;
  window.open(fallback, "_blank", "noopener");
}

let usedMarketLinks = { keyword: "", bunjang_url: "", joongna_url: "" };

function looksAppleModel(model) {
  const text = String(model || "");
  const lower = text.toLowerCase();
  return lower.startsWith("apple") || /맥북|아이맥|맥미니|맥 미니|맥스튜디오|맥 스튜디오|아이패드|아이폰|애플/.test(text);
}

function usedSearchKeyword(model, cpu, gpu) {
  if (looksAppleModel(model)) return String(model || "").trim();
  /* 조립PC는 CPU + 그래픽카드만 사용 (모델명 폴백 없음) */
  const parts = [cpu, gpu].map(s => String(s || "").trim()).filter(Boolean);
  return parts.join(" ");
}

function usedSearchButtonAttrs(q) {
  const apple = isAppleQuote(q);
  const cpu = apple ? "" : encodeURIComponent(quotePartName(q, "CPU") || "");
  const vga = apple ? "" : encodeURIComponent(quotePartName(q, "그래픽카드") || "");
  return `data-keyword="${encodeURIComponent(q.model || "")}" data-cpu="${cpu}" data-vga="${vga}"`;
}

function fallbackUsedLinks(model, cpu, gpu) {
  const keyword = usedSearchKeyword(model, cpu, gpu);
  if (!keyword) return { keyword: "", bunjang_url: "", joongna_url: "" };
  return {
    keyword,
    bunjang_url: `https://m.bunjang.co.kr/search/products?order=score&q=${encodeURIComponent(keyword)}`,
    joongna_url: `https://web.joongna.com/search/${encodeURIComponent(keyword)}`,
  };
}

async function openUsedMarketDirect(source, market, event) {
  if (event) {
    event.preventDefault();
    event.stopPropagation();
  }
  let model = "";
  let cpu = "";
  let gpu = "";
  if (source && source.dataset) {
    model = decodeURIComponent(source.dataset.keyword || "");
    cpu = decodeURIComponent(source.dataset.cpu || "");
    gpu = decodeURIComponent(source.dataset.vga || "");
  } else {
    model = String(source || "").trim();
  }
  if (!model && !cpu && !gpu) return;
  let links = fallbackUsedLinks(model, cpu, gpu);
  showBusy("중고 시세 검색어 준비 중…");
  try {
    const params = new URLSearchParams();
    if (model) params.set("q", model);
    if (cpu) params.set("cpu", cpu);
    if (gpu) params.set("gpu", gpu);
    const res = await fetch(`/api/used/links?${params.toString()}`);
    const data = await res.json().catch(() => ({}));
    if (res.ok && data.bunjang_url) {
      links = {
        keyword: data.keyword || links.keyword,
        bunjang_url: data.bunjang_url,
        joongna_url: data.joongna_url || links.joongna_url,
      };
    }
  } catch (_) {
    /* 키워드 API 실패 시 CPU/그래픽카드로 만든 검색어를 그대로 쓴다 */
  } finally {
    hideBusy();
  }
  const url = market === "joongna" ? links.joongna_url : links.bunjang_url;
  if (url) window.open(url, "_blank", "noopener,noreferrer");
}

window.openUsedMarketDirect = openUsedMarketDirect;

function renderSummaryCards(quotes) {
  const container = $("#summaryCards");
  if (!container) return;

  container.innerHTML = quotes.map((q, i) => summaryCardHtml(q, i)).join("");
  bindSummaryCardEvents(container);
}

function isAppleLiveSource(src) {
  const s = String(src || "");
  return s.startsWith("apple_official") || s === "apple_cto";
}

function summaryCardHtml(q, i) {
  const apple = isAppleQuote(q);
  const coupangKeyword = encodeURIComponent(q.model);
  const liveApple = isAppleLiveSource(q.price_source);
  const sourceClass = q.price_source === "live" || liveApple ? "source-live" : "source-fallback";
  const sourceText = q.price_source === "live"
    ? "● 실시간 수집"
    : (liveApple
      ? (q.price_source === "apple_official_snapshot"
        ? "● Apple 공식몰 스냅샷"
        : (q.price_source === "apple_cto" ? "● Apple 공식몰 + CTO" : "● Apple 공식몰 현재 판매가"))
      : "● 기본가/캐시");
  return `
    <div class="summary-card ${i === activeTab ? "active" : ""}" data-index="${i}">
      <span class="tier-badge ${badgeClass(q.tier_key)}">${q.tab_label || q.tier}</span>
      <h3>${escapeHtml(q.model)}</h3>
      <p class="model">${escapeHtml(q.description)}</p>
      <div class="price">${fmt(displayTotal(q))}</div>
      <p class="source ${sourceClass}">
        ${sourceText}
      </p>
      <div class="card-actions">
        ${apple ? `
        <a href="${q.url}" target="_blank" rel="noopener" class="btn btn-sm btn-buy" onclick="event.stopPropagation()">
          🛍️ 구입하기 ↗
        </a>
        ${hasLiveCto(q) ? `
        <button type="button" class="btn btn-sm btn-cto btn-open-cto" data-index="${i}" data-tierkey="${q.tier_key}" onclick="event.stopPropagation()">
          ⚙️ 사양 CTO 수정
        </button>` : ""}` : `
        <a href="${q.url}" target="_blank" rel="noopener" class="btn btn-sm btn-buy" onclick="event.stopPropagation()">
          🛍️ 상품상세 ↗
        </a>`}
        <button type="button" class="btn btn-sm btn-coupang btn-coupang-search" data-keyword="${coupangKeyword}" onclick="openCoupangSearch(decodeURIComponent(this.dataset.keyword), event)">
          🛒 쿠팡검색 ↗
        </button>
        <button type="button" class="btn btn-sm btn-bunjang" ${usedSearchButtonAttrs(q)} onclick="openUsedMarketDirect(this, 'bunjang', event)">
          ⚡ 번개장터
        </button>
        <button type="button" class="btn btn-sm btn-joongna" ${usedSearchButtonAttrs(q)} onclick="openUsedMarketDirect(this, 'joongna', event)">
          🟢 중고나라
        </button>
        <button type="button" class="btn btn-sm btn-excel-one" data-index="${i}" title="이 항목만 견적서 Excel 로 내려받습니다">
          📥 엑셀 견적서
        </button>
      </div>
    </div>`;
}

function bindSummaryCardEvents(container) {
  if (!container) return;
  container.querySelectorAll(".summary-card").forEach((card) => {
    card.addEventListener("click", () => {
      activeTab = parseInt(card.dataset.index, 10);
      if (!quoteData?.quotes) return;
      renderTabs(quoteData.quotes);
      renderDetail(quoteData.quotes);
      applyResultView();
    });
  });

  container.querySelectorAll(".btn-open-cto").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const idx = parseInt(btn.dataset.index, 10);
      const tierKey = btn.dataset.tierkey;
      openCtoModal(idx, tierKey);
    });
  });

  container.querySelectorAll(".btn-excel-one").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      downloadOneExcel(parseInt(btn.dataset.index, 10));
    });
  });
}

function renderEstimateForm(q) {
  const apple = isAppleQuote(q);
  const coupangKeyword = encodeURIComponent(q.model);
  const shownTotal = displayTotal(q);

  const rows = displayParts(q)
    .map((p) => {
      const isDiscount = p.category === "할인" || p.amount < 0 || p.unit_price < 0;
      const isSetup = p.category === "초기 세팅비";
      let unitPriceStr;
      let amountStr;
      // 애플스토어는 세부 단가를 사양으로만 표기한다. 초기 세팅비는 예외.
      if (!showUnitPrices || (apple && !isSetup)) {
        unitPriceStr = "-";
        amountStr = "-";
      } else {
        unitPriceStr = p.qty > 0 && (p.unit_price > 0 || isDiscount) ? fmt(p.unit_price) : "";
        amountStr = p.qty > 0 && (p.amount > 0 || isDiscount) ? fmt(p.amount) : "별도청구";
      }
      return `
    <tr${isDiscount ? ' class="discount-row"' : ""}>
      <td class="col-no">${p.no}</td>
      <td class="col-cat">${p.category}</td>
      <td class="col-name">${p.name}</td>
      <td class="col-qty">${p.qty > 0 ? p.qty : "1"}</td>
      <td class="col-price">${unitPriceStr}</td>
      <td class="col-amount">${amountStr}</td>
    </tr>`;
    })
    .join("");

  const storeHeaderLabel = apple ? "APPLE STORE ESTIMATE (사양 명세서)" : "COMPUZONE PC ESTIMATE";
  const storeFooterNotice = apple
    ? "※ 애플스토어 견적 표시 방식: 세부 품목별 단가는 사양으로 표시되며, 제품 구매가가 합계로 표기됩니다."
    : (quoteMarginPercent > 0
      ? `※ ${marginKindLabel(quoteMarginPercent)} · 컴퓨존 판매가 기준 품목 단가와 합계에 ${quoteMarginPercent}% 마진 적용`
      : "※ 컴퓨존 실시간 판매가 기준 · 아래 링크는 컴퓨존 견적 공유 URL입니다.");
  const unitPriceNotice = showUnitPrices
    ? ""
    : '<p class="estimate-disclaimer">※ 품목별 단가·금액은 표시하지 않고 합계금액만 안내합니다.</p>';
  return `
  <div class="estimate-form">
    <div class="estimate-header">
      <h2>견&nbsp;&nbsp;적&nbsp;&nbsp;서</h2>
      <p>${storeHeaderLabel}</p>
      <div class="card-actions" style="justify-content: center; margin-top: 6px;">
        ${apple ? `
        <a href="${q.url}" target="_blank" rel="noopener" class="btn btn-sm btn-buy">
          🛍️ 애플스토어에서 구입하기 ↗
        </a>
        ${hasLiveCto(q) ? `
        <button type="button" class="btn btn-sm btn-cto btn-open-cto-main" data-index="${activeTab}" data-tierkey="${q.tier_key}">
          ⚙️ 옵션 업그레이드 (CTO 사양 수정)
        </button>` : ""}` : `
        <a href="${q.url}" target="_blank" rel="noopener" class="btn btn-sm btn-buy">
          🛍️ 컴퓨존 상품상세 ↗
        </a>`}
        <button type="button" class="btn btn-sm btn-coupang btn-coupang-search" data-keyword="${coupangKeyword}" onclick="openCoupangSearch(decodeURIComponent(this.dataset.keyword), event)">
          🛒 쿠팡에서 검색하기 ↗
        </button>
        <button type="button" class="btn btn-sm btn-bunjang" ${usedSearchButtonAttrs(q)} onclick="openUsedMarketDirect(this, 'bunjang', event)">
          ⚡ 번개장터에서 검색 ↗
        </button>
        <button type="button" class="btn btn-sm btn-joongna" ${usedSearchButtonAttrs(q)} onclick="openUsedMarketDirect(this, 'joongna', event)">
          🟢 중고나라에서 검색 ↗
        </button>
      </div>
    </div>
    <div class="estimate-meta">
      <div class="meta-row"><span class="meta-label">견적일</span><span class="meta-value">${quoteInfo?.quote_date || q.quote_date}</span></div>
      <div class="meta-row"><span class="meta-label">견적구분</span><span class="meta-value">${q.tier}</span></div>
      <div class="meta-row"><span class="meta-label">모델명</span><span class="meta-value">${q.model}</span></div>
      <div class="meta-row"><span class="meta-label">상품번호</span><span class="meta-value">${q.product_no}</span></div>
      ${quoteInfo?.customer_name || quoteInfo?.customer_manager ? `
      <div class="meta-row"><span class="meta-label">견적처</span><span class="meta-value">${[quoteInfo.customer_name, quoteInfo.customer_manager].filter(Boolean).join(" · ")}</span></div>` : ""}
      ${quoteInfo?.supplier_name ? `
      <div class="meta-row"><span class="meta-label">공급자</span><span class="meta-value">${[quoteInfo.supplier_name, quoteInfo.supplier_manager, quoteInfo.supplier_phone].filter(Boolean).join(" · ")}</span></div>` : ""}
      ${quoteInfo?.supplier_email ? `
      <div class="meta-row"><span class="meta-label">이메일</span><span class="meta-value">${quoteInfo.supplier_email}</span></div>` : ""}
    </div>
    <div class="estimate-table-wrap">
      <table class="estimate-table">
        <thead>
          <tr>
            <th style="width: 50px;">No</th>
            <th style="width: 100px;">구분</th>
            <th>품명 및 규격 명세</th>
            <th style="width: 60px;">수량</th>
            <th style="width: 120px;">단가</th>
            <th style="width: 130px;">금액</th>
          </tr>
        </thead>
        <tbody>
          ${rows}
        </tbody>
        <tfoot>
          <tr>
            <td colspan="5" class="total-label">합계 (VAT 포함)</td>
            <td class="total-price">${fmt(shownTotal)}</td>
          </tr>
        </tfoot>
      </table>
    </div>
    <div class="estimate-footer">
      <p class="estimate-disclaimer">※ 윈도우, 오피스 별도 · 기술지원비 별도청구</p>
      ${unitPriceNotice}
      <p>${storeFooterNotice}</p>
      <p><a href="${q.url}" target="_blank" rel="noopener">${q.url}</a></p>
    </div>
  </div>`;
}

function renderTabs(quotes) {
  const container = $("#tabBar");
  if (!container) return;

  container.innerHTML = quotes
    .map(
      (q, i) => `
    <button class="tab-btn ${i === activeTab ? "active" : ""}" data-index="${i}">
      ${q.tab_label || q.tier} (${fmt(displayTotal(q))})
    </button>`
    )
    .join("");

  container.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      activeTab = parseInt(btn.dataset.index, 10);
      renderTabs(quotes);
      renderDetail(quotes);
      applyResultView();
    });
  });
}

function renderDetail(quotes) {
  const q = quotes[activeTab];
  if (!q) return;

  const detailArea = $("#quotePanels");
  if (!detailArea) return;

  detailArea.innerHTML = renderEstimateForm(q);

  detailArea.querySelector(".btn-open-cto-main")?.addEventListener("click", () => {
    openCtoModal(activeTab, q.tier_key);
  });
}

function excelQueryParams(index = -1) {
  const params = new URLSearchParams({
    cat: currentCat === "game-pc" ? "game" : currentCat,
    monitor: includeMonitor ? "1" : "0",
    keyboard: includeKeyboard ? "1" : "0",
    ram32: includeRam32 ? "1" : "0",
    setup: includeSetup ? "1" : "0",
    unitprice: showUnitPrices ? "1" : "0",
    margin: String(currentStore === "apple" ? 0 : quoteMarginPercent),
    t: String(Date.now()),
  });
  if (currentCat === "game-pc" && lastGameQuery) {
    params.set("games", lastGameQuery.games.join("|"));
    params.set("titles", lastGameQuery.titles.join("|"));
    params.set("resolution", lastGameQuery.resolution);
  }
  if (currentCat === "price" && lastPriceQuery) {
    params.set("min_price", String(lastPriceQuery.min_price));
    params.set("max_price", String(lastPriceQuery.max_price));
    params.set("price_type", lastPriceQuery.type || "assembled");
  }
  if (index >= 0) params.set("index", String(index));
  const cto = ctoUpgradesForExcel(index);
  if (cto) params.set("cto", cto);
  return params.toString();
}

/** 화면에서 적용한 Apple CTO 추가 품목을 {tier_key: [{category, name, amount}]} JSON 으로 만든다. */
function ctoUpgradesForExcel(index = -1) {
  const quotes = quoteData?.quotes || [];
  const picked = index >= 0 ? [quotes[index]] : quotes;
  const map = {};
  picked.forEach((q) => {
    const added = q?._ctoAdded || [];
    if (!q?._ctoOriginal || !q.tier_key || !added.length) return;
    map[q.tier_key] = added.map((p) => ({ category: p.category, name: p.name, amount: p.amount }));
  });
  return Object.keys(map).length ? JSON.stringify(map) : "";
}

/** 카드 한 건만 견적서로 내려받는다. 상단 버튼(전체)은 그대로 둔다. */
function downloadOneExcel(index) {
  downloadWithBusy(`/api/download/excel?${excelQueryParams(index)}`, {}, "견적서.xlsx", "견적서 Excel 생성 중…");
}

/** 모니터·무선키보드 공통 표시: "{이름} (포함됨|제외됨)" */
function extraStatusText(included) {
  return included ? "포함됨" : "제외됨";
}

function syncExtraToggleUI({ included, labelSel, btnSel, name, text, title }) {
  const status = extraStatusText(included);
  const label = $(labelSel);
  const btn = $(btnSel);
  if (label) label.textContent = text || `${name} (${status})`;
  if (btn) {
    btn.classList.toggle("extra-off", !included);
    btn.classList.toggle("extra-on", included);
    btn.setAttribute("aria-pressed", included ? "true" : "false");
    btn.title = title || `${name} ${status} — 클릭하여 전환`;
  }
}

function updateRam32Visibility() {
  const btn = $("#btnRam32Toggle");
  if (currentStore === "compuzone" && currentCat === "ai") {
    show(btn);
  } else {
    hide(btn);
  }
}

function updateExtraButtonsVisibility() {
  const apple = currentStore === "apple";
  const monitorBtn = $("#btnMonitorToggle");
  const keyboardBtn = $("#btnKeyboardToggle");
  const setupBtn = $("#btnSetupToggle");
  const unitPriceBtn = $("#btnUnitPriceToggle");
  const marginBtn = $("#btnMarginCycle");
  if (apple) {
    hide(monitorBtn);
    hide(keyboardBtn);
    hide($("#btnRam32Toggle"));
    if (isMacCategory(currentCat)) show(setupBtn);
    else hide(setupBtn);
    // Apple 견적은 세부 단가를 사양으로만 표기하므로 단가 토글·마진이 의미 없다.
    hide(unitPriceBtn);
    hide(marginBtn);
  } else if (currentCat === "price" && priceTargetType === "notebook") {
    // 노트북 검색에는 모니터·키보드·세팅비·메모리 옵션이 적용되지 않는다.
    hide(monitorBtn);
    hide(keyboardBtn);
    hide(setupBtn);
    hide($("#btnRam32Toggle"));
    show(unitPriceBtn);
    show(marginBtn);
  } else {
    show(monitorBtn);
    show(keyboardBtn);
    show(setupBtn);
    show(unitPriceBtn);
    show(marginBtn);
    updateRam32Visibility();
  }
}

function updateExtrasUI() {
  // 모니터 버튼 = 무선키보드 버튼과 동일한 로직·표시
  syncExtraToggleUI({
    included: includeMonitor,
    labelSel: "#monitorToggleLabel",
    btnSel: "#btnMonitorToggle",
    name: "모니터",
  });
  syncExtraToggleUI({
    included: includeKeyboard,
    labelSel: "#keyboardToggleLabel",
    btnSel: "#btnKeyboardToggle",
    name: "무선키보드",
  });
  syncExtraToggleUI({
    included: includeRam32,
    labelSel: "#ram32ToggleLabel",
    btnSel: "#btnRam32Toggle",
    name: "메모리32GB로 추가",
  });
  syncExtraToggleUI({
    included: includeSetup,
    labelSel: "#setupToggleLabel",
    btnSel: "#btnSetupToggle",
    name: "초기 세팅비",
  });
  syncExtraToggleUI({
    included: showUnitPrices,
    labelSel: "#unitPriceToggleLabel",
    btnSel: "#btnUnitPriceToggle",
    name: "단가",
    text: showUnitPrices ? "단가표시" : "단가미표시",
    title: showUnitPrices
      ? "품목별 단가·금액을 표시 중입니다 — 클릭하면 숨깁니다"
      : "품목별 단가·금액을 숨기고 합계만 보여 줍니다 — 클릭하면 표시합니다",
  });
  updateExtraButtonsVisibility();
  syncMarginUI();

  const excelBtn = $("#btnDownloadExcel");
  if (excelBtn) excelBtn.href = `/api/download/excel?${excelQueryParams()}`;
}

function toggleExtraAndReload(kind) {
  if (kind === "monitor") includeMonitor = !includeMonitor;
  else if (kind === "keyboard") includeKeyboard = !includeKeyboard;
  else if (kind === "ram32") includeRam32 = !includeRam32;
  else if (kind === "setup") includeSetup = !includeSetup;
  else return;

  updateExtrasUI();
  if (!hasLoadedOnce) return;
  if (currentCat === "game-pc" && lastGameQuery) {
    submitGamePcRecommend();
  } else if (currentCat === "price" && lastPriceQuery) {
    submitPriceSearch({ keepView: true });
  } else {
    // Apple 세팅비 토글처럼 같은 목록을 다시 불러올 때 적용해 둔 CTO 구성을 유지한다.
    fetchQuotes(currentCat, false, { ctoCarry: snapshotCto(quoteData?.quotes) });
  }
}

$("#btnMonitorToggle")?.addEventListener("click", () => toggleExtraAndReload("monitor"));
$("#btnKeyboardToggle")?.addEventListener("click", () => toggleExtraAndReload("keyboard"));
$("#btnRam32Toggle")?.addEventListener("click", () => {
  if (currentCat !== "ai") return;
  toggleExtraAndReload("ram32");
});
$("#btnSetupToggle")?.addEventListener("click", () => toggleExtraAndReload("setup"));

// 단가 표시 여부는 화면 표기만 바꾸므로 견적을 다시 불러오지 않는다.
$("#btnUnitPriceToggle")?.addEventListener("click", () => {
  showUnitPrices = !showUnitPrices;
  updateExtrasUI();
  if (quoteData?.quotes) renderDetail(quoteData.quotes);
});

// 클릭할 때마다 이 순서대로 순환한다.
const MARGIN_CYCLE = [CONSUMER_MARGIN_PERCENT, 25, 20, 15, 10, 0];

/** 버튼 면에 들어가는 짧은 표기 */
function marginButtonLabel(m) {
  return m === CONSUMER_MARGIN_PERCENT ? "C" : String(m);
}

/** 툴팁에서 뜻을 풀어 쓰는 표기 */
function marginFullLabel(m) {
  if (m === CONSUMER_MARGIN_PERCENT) return `C (소비자견적 ${m}%)`;
  if (m === 0) return "0 (원가견적)";
  return `${m} (마진 ${m}%)`;
}

function syncMarginUI() {
  const label = $("#marginCycleLabel");
  const btn = $("#btnMarginCycle");
  if (label) label.textContent = marginButtonLabel(quoteMarginPercent);
  if (!btn) return;
  const next = MARGIN_CYCLE[(MARGIN_CYCLE.indexOf(quoteMarginPercent) + 1) % MARGIN_CYCLE.length];
  btn.classList.toggle("margin-cost", quoteMarginPercent === 0);
  btn.title =
    `현재 ${marginFullLabel(quoteMarginPercent)} — 클릭하면 ${marginFullLabel(next)}(으)로 바뀝니다\n` +
    MARGIN_CYCLE.map(marginButtonLabel).join(" → ") + " 순으로 순환";
}

function setQuoteMargin(percent) {
  const next = parseInt(percent, 10);
  if (!MARGIN_CYCLE.includes(next)) return;
  quoteMarginPercent = next;
  syncMarginUI();
  if (quoteData?.quotes) {
    renderTabs(quoteData.quotes);
    renderDetail(quoteData.quotes);
    applyResultView();
  }
  updateExtrasUI();
}

function cycleQuoteMargin() {
  const at = MARGIN_CYCLE.indexOf(quoteMarginPercent);
  setQuoteMargin(MARGIN_CYCLE[(at + 1) % MARGIN_CYCLE.length]);
}

$("#btnMarginCycle")?.addEventListener("click", (e) => {
  e.preventDefault();
  cycleQuoteMargin();
});

updateExtrasUI();

// ---------- 견적처/공급자 정보 관리 모달 ----------
const qiModal = $("#quoteInfoModal");
const qiFields = {
  customer_name: "#qiCustomerName",
  customer_manager: "#qiCustomerManager",
  customer_address: "#qiCustomerAddress",
  customer_phone: "#qiCustomerPhone",
  customer_email: "#qiCustomerEmail",
  quote_date: "#qiQuoteDate",
  supplier_name: "#qiSupplierName",
  supplier_manager: "#qiSupplierManager",
  supplier_address: "#qiSupplierAddress",
  supplier_phone: "#qiSupplierPhone",
  supplier_email: "#qiSupplierEmail",
  monitor_price: "#qiMonitorPrice",
  keyboard_price: "#qiKeyboardPrice",
  setup_price: "#qiSetupPrice",
};

// 사용자가 앱 실행 후 입력 및 저장했는지 상태 관리
let isQuoteInfoSaved = false;
let isTaskReportSaved = false;

// 가짜 데이터 판별용 기준 리스트 (상호, 대표/담당자, 주소, 연락처, 이메일)
const DUMMY_VALUES = {
  names: ["대한민국주식회사", "0000", "0000 ", "하나시스템"],
  managers: ["홍길동대표", "홍길동", "애플아저씨", "김대건 대표", "김진수 실장"],
  addresses: ["서울시", "인천광역시 부평구 부개로 71-12 제2동 제1층 152호, 153호", "인천 동구 방축로 37길 30, 35동 131호"],
  phones: ["010-1234-5678", "01012345678", "010-4226-9692", "010-0000-0000", "010 - 4226 - 9692"],
  emails: ["email1234@gmail.com", "hello@example.com", "kimdaekun@gmail.com"],
};

function isDummyValue(val, dummyList) {
  if (!val || !String(val).trim()) return true;
  const clean = String(val).replace(/[\s-]/g, "").toLowerCase();
  return dummyList.some((d) => clean === String(d).replace(/[\s-]/g, "").toLowerCase());
}

// 견적처 또는 공급자가 진짜 데이터로 변경·저장되었는지 검사
function isQuoteInfoDummy(info) {
  // 사용자가 앱 실행 후 정보를 입력하고 저장을 완료했다면 깜박임 즉시 중지!
  if (isQuoteInfoSaved) return false;
  if (!info) return true;

  // 상호명이 기본 가짜 데이터(대한민국주식회사 등)이거나 비어있으면 미갱신으로 간주
  const custDummy = isDummyValue(info.customer_name, DUMMY_VALUES.names);
  const suppDummy = isDummyValue(info.supplier_name, DUMMY_VALUES.names);

  return custDummy || suppDummy;
}

// 작업내역서의 업체정보가 저장되었거나 실제 데이터인지 검사
function isTaskReportCompanyDummy(name, addr, phone) {
  // 작업내역서가 저장되었거나, 견적정보가 입력·저장되었으면 깜박임 즉시 중지!
  if (isTaskReportSaved || isQuoteInfoSaved) return false;

  // 상호명이 기본 가짜 데이터이거나 비어있으면 미갱신으로 간주
  return isDummyValue(name, DUMMY_VALUES.names);
}

// 깜박임(Blink Warning) 상태 통합 갱신 함수
function updateBlinkWarnings() {
  // 1. 메인창 "견적정보" 버튼 및 글자 깜박임 처리
  const qiDummy = isQuoteInfoDummy(quoteInfo);
  const btnQi = $("#btnQuoteInfo");
  const txtQi = $("#quoteInfoBtnText");
  const bannerQi = $("#qiWarningNotice");

  if (btnQi) btnQi.classList.toggle("blink-warning", qiDummy);
  if (txtQi) txtQi.classList.toggle("blink-warning-text", qiDummy);
  if (bannerQi) bannerQi.classList.toggle("hidden", !qiDummy);

  // 2. 메인창 "작업내역서" 버튼 및 작업내역서 업체정보 영역 깜박임 처리
  const trName = $("#trCompanyName")?.value || quoteInfo?.supplier_name;
  const trAddr = $("#trCompanyAddress")?.value || quoteInfo?.supplier_address;
  const trPhone = $("#trCompanyPhone")?.value || quoteInfo?.supplier_phone;
  const trDummy = isTaskReportCompanyDummy(trName, trAddr, trPhone);

  const btnTr = $("#btnOpenTaskReport");
  const txtTr = $("#taskReportBtnText");
  const brandBox = $("#trBrandInfoBox");
  const bannerTr = $("#trCompanyWarningNotice");

  if (btnTr) btnTr.classList.toggle("blink-warning", trDummy);
  if (txtTr) txtTr.classList.toggle("blink-warning-text", trDummy);
  if (brandBox) brandBox.classList.toggle("blink-warning", trDummy);
  if (bannerTr) bannerTr.classList.toggle("hidden", !trDummy);

  // 작업내역서 모달 내부 입력칸 각각의 깜박임 효과 (저장되면 모두 해제)
  const inpName = $("#trCompanyName");
  const inpAddr = $("#trCompanyAddress");
  const inpPhone = $("#trCompanyPhone");
  if (inpName) inpName.classList.toggle("blink-warning-input", trDummy && isDummyValue(trName, DUMMY_VALUES.names));
  if (inpAddr) inpAddr.classList.toggle("blink-warning-input", trDummy && isDummyValue(trAddr, DUMMY_VALUES.addresses));
  if (inpPhone) inpPhone.classList.toggle("blink-warning-input", trDummy && isDummyValue(trPhone, DUMMY_VALUES.phones));
}

// 견적정보 -> 작업내역서 업체정보 동기화
function syncQuoteInfoToTaskReport(force = false) {
  if (!quoteInfo) return;
  // 신규 작성 중이거나 강제 동기화(저장/초기화 시)인 경우 업체정보 반영
  if (!currentTrId || force) {
    if ($("#trCompanyName")) $("#trCompanyName").value = quoteInfo.supplier_name || "대한민국주식회사";
    if ($("#trCompanyAddress")) $("#trCompanyAddress").value = quoteInfo.supplier_address || "서울시";
    if ($("#trCompanyPhone")) $("#trCompanyPhone").value = quoteInfo.supplier_phone || "010-1234-5678";
  }
  updateBlinkWarnings();
}

function qiSetStatus(msg, ok = true) {
  const el = $("#quoteInfoStatus");
  if (!el) return;
  el.textContent = msg;
  el.style.color = ok ? "var(--accent, #4caf50)" : "#e53935";
  if (msg) setTimeout(() => { el.textContent = ""; }, 3000);
}

function qiFillForm(info) {
  for (const [key, sel] of Object.entries(qiFields)) {
    const input = $(sel);
    if (input) input.value = info?.[key] ?? "";
  }
  updateBlinkWarnings();
}

function qiReloadQuotesIfNeeded() {
  if (!hasLoadedOnce) return;
  if (currentCat === "game-pc" && lastGameQuery) {
    submitGamePcRecommend();
  } else if (currentCat === "price" && lastPriceQuery) {
    submitPriceSearch({ keepView: true });
  } else {
    fetchQuotes(currentCat);
  }
}

async function loadQuoteInfo() {
  try {
    const res = await fetch("/api/quote-info");
    if (res.ok) {
      quoteInfo = await res.json();
      // 기존에 저장된 데이터가 기본 더미 데이터가 아니라면 저장된 상태로 간주
      if (
        quoteInfo &&
        !isDummyValue(quoteInfo.supplier_name, DUMMY_VALUES.names) &&
        !isDummyValue(quoteInfo.customer_name, DUMMY_VALUES.names)
      ) {
        isQuoteInfoSaved = true;
      }
      syncQuoteInfoToTaskReport();
      updateBlinkWarnings();
    }
  } catch (e) {
    console.warn("quote-info 로드 실패:", e);
  }
}

function qiOpen() {
  if (!qiModal) return;
  qiFillForm(quoteInfo);
  show(qiModal);
  updateBlinkWarnings();
}

function qiClose() {
  if (!qiModal) return;
  hide(qiModal);
}

function qiRerender() {
  if (quoteData && quoteData.quotes) {
    renderDetail(quoteData.quotes);
  }
}

$("#btnQuoteInfo")?.addEventListener("click", qiOpen);
$("#quoteInfoClose")?.addEventListener("click", qiClose);
$("#quoteInfoCancel")?.addEventListener("click", qiClose);
qiModal?.addEventListener("click", (e) => {
  if (e.target === qiModal) qiClose();
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (qiModal && !qiModal.classList.contains("hidden")) qiClose();
  if (ctoModal && !ctoModal.classList.contains("hidden")) closeCtoModal();
  if (trModal && !trModal.classList.contains("hidden")) closeTrModal();
  if (netModal && !netModal.classList.contains("hidden")) hide(netModal);
});

$("#quoteInfoSave")?.addEventListener("click", async () => {
  const payload = {};
  for (const [key, sel] of Object.entries(qiFields)) {
    payload[key] = $(sel)?.value.trim() || "";
  }
  try {
    const res = await fetch("/api/quote-info", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`저장 실패 (${res.status})`);
    quoteInfo = await res.json();
    isQuoteInfoSaved = true;
    syncQuoteInfoToTaskReport(true);
    updateBlinkWarnings();
    qiSetStatus("✔ 저장되었습니다. 모든 견적서 및 작업내역서에 적용됩니다.");
    qiRerender();
    qiReloadQuotesIfNeeded();
  } catch (e) {
    qiSetStatus(e.message, false);
  }
});

$("#quoteInfoReset")?.addEventListener("click", async () => {
  if (!confirm("저장된 견적처/공급자 정보를 초기화할까요?")) return;
  try {
    const res = await fetch("/api/quote-info", { method: "DELETE" });
    if (!res.ok) throw new Error("초기화 실패");
    quoteInfo = await res.json();
    isQuoteInfoSaved = false;
    qiFillForm(quoteInfo);
    syncQuoteInfoToTaskReport(true);
    updateBlinkWarnings();
    qiSetStatus("✔ 초기화되었습니다.");
    qiRerender();
    qiReloadQuotesIfNeeded();
  } catch (e) {
    qiSetStatus(e.message, false);
  }
});



// ---------- 네트워크 접속 안내 모달 ----------
let netModal = null;

function initNetworkModal() {
  netModal = $("#networkModal");
  const btnNetShare = $("#btnNetworkShare");
  const netClose = $("#networkModalClose");
  const netOk = $("#networkModalOk");
  const btnCopyNet = $("#btnCopyNetUrl");
  const btnCopyLocal = $("#btnCopyLocalUrl");
  const netInput = $("#netIpInput");
  const localInput = $("#localIpInput");

  function openNetModal() {
    if (netModal) show(netModal);
  }
  function closeNetModal() {
    if (netModal) hide(netModal);
  }

  btnNetShare?.addEventListener("click", openNetModal);
  netClose?.addEventListener("click", closeNetModal);
  netOk?.addEventListener("click", closeNetModal);
  netModal?.addEventListener("click", (e) => {
    if (e.target === netModal) closeNetModal();
  });

  async function copyToClipboard(inputEl, btnEl) {
    if (!inputEl) return;
    const text = inputEl.value;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        inputEl.select();
        document.execCommand("copy");
      }
      if (btnEl) {
        const orig = btnEl.textContent;
        btnEl.textContent = "✔ 복사됨!";
        setTimeout(() => { btnEl.textContent = orig; }, 2000);
      }
    } catch {
      inputEl.select();
      document.execCommand("copy");
      if (btnEl) {
        const orig = btnEl.textContent;
        btnEl.textContent = "✔ 복사됨!";
        setTimeout(() => { btnEl.textContent = orig; }, 2000);
      }
    }
  }

  btnCopyNet?.addEventListener("click", () => copyToClipboard(netInput, btnCopyNet));
  btnCopyLocal?.addEventListener("click", () => copyToClipboard(localInput, btnCopyLocal));
}

// ---------- CTO 커스텀 모달 ----------
const ctoModal = $("#ctoModal");

async function openCtoModal(idx, tierKey) {
  if (!ctoModal) return;
  activeCtoIndex = idx;
  activeCtoTierKey = tierKey;

  const baseQ = quoteData?.quotes?.[idx];
  if (!baseQ) return;
  const original = baseQ._ctoOriginal || baseQ;
  baseQuoteForCto = JSON.parse(JSON.stringify(original));
  delete baseQuoteForCto._ctoOriginal;

  const infoEl = $("#ctoModelInfo");
  if (infoEl) {
    infoEl.innerHTML = `
      <h4 style="margin:0 0 4px 0; font-size:1.05rem; color:var(--text); font-weight:700;">${baseQ.model}</h4>
      <p style="margin:0; font-size:1rem; color:var(--muted);">${baseQ.description}</p>
    `;
  }

  showBusy("CTO 사양 옵션 불러오는 중…");
  try {
    ctoLive = null;
    // 이미 적용한 구성이 있으면 그 선택으로 다시 연다.
    const sel = baseQ._ctoSel ? `?sel=${encodeURIComponent(JSON.stringify(baseQ._ctoSel))}` : "";
    const res = await fetch(`/api/apple/cto/${encodeURIComponent(tierKey)}${sel}`);
    const data = await res.json();
    if (data.live) {
      renderLiveCto(data);
      show(ctoModal);
      return;
    }
    currentCtoOptions = data.options || [];
    if (!currentCtoOptions.length && Array.isArray(baseQ.cto_groups)) {
      currentCtoOptions = baseQ.cto_groups;
    }

    const body = $("#ctoFormContainer");
    if (!body) return;

    if (!currentCtoOptions.length) {
      body.innerHTML = '<p class="empty-state">선택 가능한 CTO 옵션이 없습니다.</p>';
      $("#ctoTotalPrice").textContent = fmt(baseQ.total);
      show(ctoModal);
      return;
    }

    body.innerHTML = currentCtoOptions
      .map((grp) => {
        const opts = grp.options || grp.choices || [];
        return `
      <div class="cto-group" style="margin-bottom: 20px;">
        <label style="display: block; font-weight: 700; margin-bottom: 8px; color: var(--accent);">${grp.name}</label>
        <select id="cto_select_${grp.id}" class="cto-select" data-group-id="${grp.id}">
          ${opts
            .map((opt, optIdx) => {
              const delta = opt.delta !== undefined ? opt.delta : (opt.delta_price || 0);
              return `<option value="${optIdx}" data-price="${delta}" data-name="${opt.name_override || ""}">${opt.label}</option>`;
            })
            .join("")}
        </select>
      </div>`;
      })
      .join("");

    currentCtoOptions.forEach((grp) => {
      $(`#cto_select_${grp.id}`)?.addEventListener("change", updateCtoTotalPrice);
    });

    updateCtoTotalPrice();
    show(ctoModal);
  } catch (e) {
    alert("CTO 옵션 정보를 가져오는데 실패했습니다: " + e.message);
  } finally {
    hideBusy();
  }
}

/** 구성하기 API 응답으로 선택 상자를 그린다. 값을 바꾸면 전체 선택으로 다시 물어 정확한 총액을 받는다. */
function renderLiveCto(data) {
  ctoLive = data;
  currentCtoOptions = data.options || [];
  const body = $("#ctoFormContainer");
  if (!body) return;
  body.innerHTML = currentCtoOptions
    .map(
      (grp) => `
      <div class="cto-group" style="margin-bottom: 20px;">
        <label style="display: block; font-weight: 700; margin-bottom: 8px; color: var(--accent);">${grp.name}</label>
        <select id="cto_select_${grp.id}" class="cto-select" data-group-id="${grp.id}">
          ${grp.options
            .map((opt) => `<option value="${opt.value}"${opt.selected ? " selected" : ""}>${opt.label}</option>`)
            .join("")}
        </select>
      </div>`
    )
    .join("");
  currentCtoOptions.forEach((grp) => {
    $(`#cto_select_${grp.id}`)?.addEventListener("change", () => refreshLiveCto(null, grp.id));
  });
  showLiveCtoTotal();
}

function showLiveCtoTotal() {
  if (!ctoLive || !baseQuoteForCto) return;
  // 카드 합계에 초기 세팅비 등 Apple 본체 외 금액이 있으면 그대로 더한다.
  const extra = Math.max(0, (baseQuoteForCto.total || 0) - (ctoLive.base_total || 0));
  $("#ctoTotalPrice").textContent = fmt((ctoLive.total || 0) + extra);
}

async function refreshLiveCto(selections, changed = "") {
  if (!ctoLive) return;
  const sel = selections || {};
  if (!selections) {
    currentCtoOptions.forEach((grp) => {
      const el = $(`#cto_select_${grp.id}`);
      if (el) sel[grp.id] = el.value;
    });
  }
  $("#ctoTotalPrice").textContent = "계산 중…";
  $$(".cto-select").forEach((el) => (el.disabled = true));
  try {
    const res = await fetch(
      `/api/apple/cto/${encodeURIComponent(activeCtoTierKey)}?sel=${encodeURIComponent(JSON.stringify(sel))}&changed=${encodeURIComponent(changed)}`
    );
    const data = await res.json();
    if (!data.live) throw new Error("구성 가격을 받지 못했습니다.");
    renderLiveCto(data);
  } catch (e) {
    alert("CTO 가격 계산 실패: " + e.message);
    renderLiveCto(ctoLive);
  }
}

function updateCtoTotalPrice() {
  if (ctoLive) return showLiveCtoTotal();
  if (!baseQuoteForCto) return;
  let totalDelta = 0;

  currentCtoOptions.forEach((grp) => {
    const sel = $(`#cto_select_${grp.id}`);
    if (sel) {
      const selectedOption = sel.options[sel.selectedIndex];
      if (selectedOption) {
        const delta = parseInt(selectedOption.dataset.price, 10) || 0;
        totalDelta += delta;
      }
    }
  });

  const finalTotal = baseQuoteForCto.total + totalDelta;
  $("#ctoTotalPrice").textContent = fmt(finalTotal);
}

function applyCtoChanges() {
  if (activeCtoIndex === null || !quoteData?.quotes?.[activeCtoIndex]) return;

  const targetQ = quoteData.quotes[activeCtoIndex];
  let totalDelta = 0;
  const addedParts = [];

  if (ctoLive) {
    // 구성하기 API 가 계산한 총액과 추가 품목 행을 그대로 쓴다.
    totalDelta = (ctoLive.total || 0) - (ctoLive.base_total || 0);
    (ctoLive.parts || []).forEach((p) =>
      addedParts.push({ category: p.category, name: p.name, qty: 1, unit_price: p.amount, amount: p.amount })
    );
    targetQ._ctoSel = addedParts.length ? { ...ctoLive.selected } : null;
  }

  if (!ctoLive) currentCtoOptions.forEach((grp) => {
    const sel = $(`#cto_select_${grp.id}`);
    if (sel) {
      const opts = grp.options || grp.choices || [];
      const selectedIdx = parseInt(sel.value, 10);
      const choice = opts[selectedIdx];
      if (choice) {
        const delta = choice.delta !== undefined ? choice.delta : (choice.delta_price || 0);
        if (delta) {
          totalDelta += delta;
          addedParts.push({
            category: grp.name,
            name: choice.name_override || `[CTO 업그레이드] ${choice.label}`,
            qty: 1,
            unit_price: delta,
            amount: delta,
          });
        }
      }
    }
  });

  if (!targetQ._ctoOriginal) {
    targetQ._ctoOriginal = JSON.parse(JSON.stringify(baseQuoteForCto));
  }
  // 화면·Excel 이 같은 규칙으로 합치도록 원래 추가 품목도 따로 기억한다.
  targetQ._ctoAdded = addedParts.map((p) => ({ category: p.category, name: p.name, amount: p.amount }));
  targetQ.parts = mergeCtoParts(baseQuoteForCto.parts, targetQ._ctoAdded);
  targetQ.total = baseQuoteForCto.total + totalDelta;
  targetQ.price_source =
    totalDelta || addedParts.length ? "apple_cto" : (baseQuoteForCto.price_source || "apple_official");

  closeCtoModal();
  renderTabs(quoteData.quotes);
  renderSummaryCards(quoteData.quotes);
  renderDetail(quoteData.quotes);
}

// CTO 항목 → 같은 사양을 나타내는 기본 사양 행 (서버 main.py CTO_BASE_ROW 와 같게 유지)
const CTO_BASE_ROW = { "통합 메모리": "메모리", "저장장치": "SSD", "프로세서": "CPU/SoC" };

/** 기본 사양 행이 있는 CTO 항목은 그 행을 업그레이드 사양으로 바꾸고 금액을 더한다. 나머지는 아래에 붙인다. */
function mergeCtoParts(baseParts, added) {
  const parts = JSON.parse(JSON.stringify(baseParts || []));
  (added || []).forEach((a) => {
    const row = parts.find((p) => p.category === CTO_BASE_ROW[a.category]);
    if (row) {
      row.name = a.name;
      row.amount = (row.amount || 0) + (a.amount || 0);
      row.unit_price = row.amount;
    } else {
      parts.push({ category: a.category, name: a.name, qty: 1, unit_price: a.amount, amount: a.amount });
    }
  });
  parts.forEach((p, i) => (p.no = i + 1));
  return parts;
}

/** 적용된 CTO(추가 부품·추가 금액)를 tier_key 별로 기억해 둔다. 옵션 토글로 다시 불러올 때 쓴다. */
function snapshotCto(quotes) {
  const carry = {};
  (quotes || []).forEach((q) => {
    const orig = q._ctoOriginal;
    if (!orig || !q.tier_key) return;
    const delta = (q.total || 0) - (orig.total || 0);
    const added = q._ctoAdded || [];
    if (!delta && !added.length) return;
    carry[q.tier_key] = {
      delta,
      parts: JSON.parse(JSON.stringify(added)),
      priceSource: q.price_source,
      sel: q._ctoSel || null,
    };
  });
  return carry;
}

/** 새로 불러온 견적에 기억해 둔 CTO 를 다시 얹는다. */
function reapplyCto(quotes, carry) {
  (quotes || []).forEach((q) => {
    const c = carry[q.tier_key];
    if (!c) return;
    q._ctoOriginal = JSON.parse(JSON.stringify(q));
    q._ctoAdded = c.parts;
    q.parts = mergeCtoParts(q.parts, c.parts);
    q.total = (q.total || 0) + c.delta;
    q.price_source = c.priceSource || "apple_cto";
    if (c.sel) q._ctoSel = c.sel;
  });
}

function closeCtoModal() {
  if (!ctoModal) return;
  hide(ctoModal);
}

$("#ctoClose")?.addEventListener("click", closeCtoModal);
$("#ctoCancelBtn")?.addEventListener("click", closeCtoModal);
$("#ctoResetBtn")?.addEventListener("click", () => {
  if (ctoLive) return refreshLiveCto({}); // 빈 선택 = 공식몰 기본 구성
  currentCtoOptions.forEach((grp) => {
    const sel = $(`#cto_select_${grp.id}`);
    if (sel) sel.value = "0";
  });
  updateCtoTotalPrice();
});
$("#ctoApplyBtn")?.addEventListener("click", applyCtoChanges);
ctoModal?.addEventListener("click", (e) => {
  if (e.target === ctoModal) closeCtoModal();
});

// Store switcher handler
function switchStore(store) {
  currentStore = store;
  const btnCompuzone = $("#storeBtnCompuzone");
  const btnApple = $("#storeBtnApple");
  const groupCompuzone = $("#compuzoneCatGroup");
  const groupApple = $("#appleCatGroup");
  hidePriceResultChrome();
  updateEmptyStateCopy();
  // 자동으로 여는 첫 카테고리 버튼을 선택 상태로 표시한다.
  const firstCat = store === "compuzone" ? "ai" : "apple_macbook";
  $$(".cat-btn, .game-cat-btn, .price-cat-btn, .apple-cat-btn").forEach((b) =>
    b.classList.toggle("active", b.dataset.cat === firstCat)
  );
  if (store === "compuzone") {
    btnCompuzone?.classList.add("active");
    btnApple?.classList.remove("active");
    show(groupCompuzone);
    hide(groupApple);
    updateExtrasUI();
    fetchQuotes(firstCat);
  } else {
    btnApple?.classList.add("active");
    btnCompuzone?.classList.remove("active");
    show(groupApple);
    hide(groupCompuzone);
    updateExtrasUI();
    fetchQuotes(firstCat);
  }
}

$("#storeBtnCompuzone")?.addEventListener("click", () => switchStore("compuzone"));
$("#storeBtnApple")?.addEventListener("click", () => switchStore("apple"));

$$(".cat-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    const cat = btn.dataset.cat;
    if (!cat || cat === "game-pc" || cat === "price") return;
    $$(".cat-btn").forEach((b) => b.classList.remove("active"));
    $$(".game-cat-btn").forEach((b) => b.classList.remove("active"));
    $$(".price-cat-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    fetchQuotes(cat);
  });
});

$("#btnGamePc")?.addEventListener("click", () => {
  $$(".cat-btn").forEach((b) => b.classList.remove("active"));
  $$(".game-cat-btn").forEach((b) => b.classList.add("active"));
  $$(".price-cat-btn").forEach((b) => b.classList.remove("active"));
  openGamePcModal();
});

$("#btnPriceSearch")?.addEventListener("click", () => {
  $$(".cat-btn").forEach((b) => b.classList.remove("active"));
  $$(".game-cat-btn").forEach((b) => b.classList.remove("active"));
  $$(".price-cat-btn").forEach((b) => b.classList.remove("active"));
  $("#btnPriceSearch")?.classList.add("active");
  activatePriceSearch("assembled");
});

$("#btnNotebookPriceSearch")?.addEventListener("click", () => {
  $$(".cat-btn").forEach((b) => b.classList.remove("active"));
  $$(".game-cat-btn").forEach((b) => b.classList.remove("active"));
  $$(".price-cat-btn").forEach((b) => b.classList.remove("active"));
  $("#btnNotebookPriceSearch")?.classList.add("active");
  activatePriceSearch("notebook");
});

$$(".apple-cat-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".apple-cat-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    fetchQuotes(btn.dataset.cat);
  });
});

const btnAppleRefresh = $("#btnAppleRefresh");
if (btnAppleRefresh) {
  btnAppleRefresh.addEventListener("click", () => {
    const targetCat = currentCat && currentCat.startsWith("apple_") ? currentCat : "apple_all";
    fetchQuotes(targetCat, true);
  });
}

function extrasQuery() {
  return `monitor=${includeMonitor ? 1 : 0}&keyboard=${includeKeyboard ? 1 : 0}&ram32=${includeRam32 ? 1 : 0}&setup=${includeSetup ? 1 : 0}`;
}

function hideResultChrome() {
  hide($("#resultViewBar"));
  hide($("#resultListWrap"));
}

function hidePriceSearchBar() {
  hide($("#priceSearchBar"));
}

function hidePriceResultChrome() {
  hidePriceSearchBar();
  hideResultChrome();
}

/** 서버가 보낸 Apple 공식몰 수집 경고(개정 의심·접속 실패·새 제품군)를 결과 위에 보여 준다. */
function renderCatalogWarnings(warnings) {
  const box = $("#catalogWarn");
  const list = $("#catalogWarnList");
  if (!box || !list) return;
  const items = Array.isArray(warnings) ? warnings : [];
  list.innerHTML = items.map((w) => `<li>${escapeHtml(w)}</li>`).join("");
  if (items.length) show(box);
  else hide(box);
}
$("#catalogWarnClose")?.addEventListener("click", () => hide($("#catalogWarn")));

function applyQuotePayload(data) {
  renderCatalogWarnings(data?.warnings);
  quoteData = data;
  hasLoadedOnce = true;
  activeTab = 0;
  renderTabs(quoteData.quotes);
  renderDetail(quoteData.quotes);
  hide($("#loading"));
  applyResultView();
  updateExtrasUI();
}

async function fetchQuotes(cat = "ai", forceRefresh = false, opts = {}) {
  if (cat === "game-pc" || cat === "game") {
    openGamePcModal();
    return;
  }
  if (cat === "price" || cat === "price-search") {
    activatePriceSearch(priceTargetType);
    return;
  }

  currentCat = cat;
  updateExtrasUI();
  hidePriceSearchBar();
  hideResultChrome();
  const loading = $("#loading");
  const errorBox = $("#error");
  const emptyState = $("#emptyState");
  const summaryCards = $("#summaryCards");
  const quoteTabs = $("#quoteTabs");

  show(loading);
  setLoadingMessage(loadingMessageFor(cat));
  hide(errorBox);
  hide(emptyState);
  hide(summaryCards);
  hide(quoteTabs);

  try {
    let endpoint = `/api/category/${cat}`;
    if (cat === "ai") {
      endpoint = `/api/today`;
    } else if (cat.startsWith("apple_") || cat === "apple") {
      const appleCat = cat.replace("apple_", "");
      endpoint = `/api/apple/${appleCat}`;
    }

    let qStr = extrasQuery();
    if (forceRefresh) {
      qStr += (qStr ? "&" : "") + "refresh=1";
    }

    const res = await fetch(`${endpoint}?${qStr}`);
    if (!res.ok) throw new Error(`HTTP error ${res.status}`);
    quoteData = await res.json();

    // 구버전/우회 호출로 game-pc category 응답이 온 경우 위자드 실행
    if (quoteData.needs_game_wizard) {
      hide(loading);
      openGamePcModal();
      return;
    }
    if (quoteData.needs_price_wizard) {
      hide(loading);
      activatePriceSearch(priceTargetType);
      return;
    }

    if (opts.ctoCarry) reapplyCto(quoteData.quotes, opts.ctoCarry);
    applyQuotePayload(quoteData);
  } catch (e) {
    hide(loading);
    hide(summaryCards);
    hide(quoteTabs);
    hideResultChrome();
    show(emptyState);
    if (errorBox) {
      errorBox.textContent = `견적 수집 실패: ${e.message}. 다시 시도해주세요.`;
      show(errorBox);
    }
  }
}

// ---------- 게임으로 PC 찾기 ----------
const gamePcModal = $("#gamePcModal");

function gameSetStatus(msg, ok = true) {
  const el = $("#gamePcStatus");
  if (!el) return;
  el.textContent = msg || "";
  el.style.color = ok ? "var(--accent, #4caf50)" : "#e53935";
}

function renderGameSelectedChips() {
  const box = $("#gameSelectedChips");
  if (!box) return;
  if (!selectedGames.length) {
    box.innerHTML = '<span class="game-chip-empty">선택된 게임 없음</span>';
    return;
  }
  box.innerHTML = selectedGames
    .map(
      (g) => `
    <span class="game-chip">
      ${g.title}
      <button type="button" class="game-chip-x" data-id="${g.id}" aria-label="제거">×</button>
    </span>`
    )
    .join("");
  box.querySelectorAll(".game-chip-x").forEach((btn) => {
    btn.addEventListener("click", () => toggleGame(btn.dataset.id));
  });
}

function renderGameGrid() {
  const grid = $("#gameGrid");
  if (!grid || !gamePcMeta) return;
  grid.innerHTML = gamePcMeta.games
    .map((g) => {
      const on = selectedGames.some((s) => s.id === g.id);
      return `
      <button type="button" class="game-card ${on ? "on" : ""}" data-id="${g.id}" title="${g.title}">
        <img src="${g.image}" alt="${g.title}" loading="lazy" />
        <span>${g.title}</span>
      </button>`;
    })
    .join("");
  grid.querySelectorAll(".game-card").forEach((btn) => {
    btn.addEventListener("click", () => toggleGame(btn.dataset.id));
  });
}

function renderResolutionGrid() {
  const grid = $("#resolutionGrid");
  if (!grid || !gamePcMeta) return;
  grid.innerHTML = gamePcMeta.resolutions
    .map((r) => {
      const on = selectedResolution === r.id;
      return `
      <button type="button" class="resolution-card ${on ? "on" : ""}" data-id="${r.id}">
        <strong>${r.id}</strong>
        <span>${r.label}</span>
      </button>`;
    })
    .join("");
  grid.querySelectorAll(".resolution-card").forEach((btn) => {
    btn.addEventListener("click", () => {
      selectedResolution = btn.dataset.id;
      renderResolutionGrid();
      gameSetStatus("");
    });
  });
}

function toggleGame(id) {
  const meta = gamePcMeta?.games?.find((g) => g.id === id);
  if (!meta) return;
  const idx = selectedGames.findIndex((g) => g.id === id);
  if (idx >= 0) {
    selectedGames.splice(idx, 1);
  } else {
    if (selectedGames.length >= (gamePcMeta.max_games || 4)) {
      gameSetStatus("게임은 최대 4개까지 선택 가능합니다.", false);
      return;
    }
    selectedGames.push({ id: meta.id, title: meta.title });
  }
  gameSetStatus("");
  renderGameGrid();
  renderGameSelectedChips();
}

function setGameWizardStep(step) {
  gameWizardStep = step;
  $$(".game-step-tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.step === String(step));
  });
  const step1 = $("#gameStep1");
  const step2 = $("#gameStep2");
  const prev = $("#gamePcPrev");
  const next = $("#gamePcNext");
  const submit = $("#gamePcSubmit");
  if (step === 1) {
    show(step1);
    hide(step2);
    hide(prev);
    show(next);
    hide(submit);
  } else {
    hide(step1);
    show(step2);
    show(prev);
    hide(next);
    show(submit);
  }
}

async function ensureGamePcMeta() {
  if (gamePcMeta) return gamePcMeta;
  gameSetStatus("게임 목록을 불러오는 중…");
  const res = await fetch("/api/game-pc/meta");
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `게임 목록 로드 실패 (${res.status})`);
  }
  gamePcMeta = await res.json();
  gameSetStatus("");
  return gamePcMeta;
}

async function openGamePcModal() {
  if (!gamePcModal) return;
  try {
    await ensureGamePcMeta();
    renderGameGrid();
    renderGameSelectedChips();
    renderResolutionGrid();
    setGameWizardStep(selectedGames.length && selectedResolution ? 2 : 1);
    show(gamePcModal);
  } catch (e) {
    alert(e.message);
  }
}

function closeGamePcModal() {
  hide(gamePcModal);
}

async function submitGamePcRecommend() {
  if (!selectedGames.length) {
    gameSetStatus("게임을 1개 이상 선택해주세요.", false);
    setGameWizardStep(1);
    return;
  }
  if (!selectedResolution) {
    gameSetStatus("해상도를 선택해주세요.", false);
    setGameWizardStep(2);
    return;
  }

  closeGamePcModal();
  currentCat = "game-pc";
  updateExtrasUI();
  lastGameQuery = {
    games: selectedGames.map((g) => g.id),
    titles: selectedGames.map((g) => g.title),
    resolution: selectedResolution,
  };

  const loading = $("#loading");
  const errorBox = $("#error");
  const emptyState = $("#emptyState");
  const summaryCards = $("#summaryCards");
  const quoteTabs = $("#quoteTabs");
  const loadingText = loading?.querySelector("p");

  show(loading);
  if (loadingText) loadingText.textContent = "게임에 맞는 PC 견적을 수집 중…";
  hide(errorBox);
  hide(emptyState);
  hide(summaryCards);
  hide(quoteTabs);
  hidePriceSearchBar();
  hideResultChrome();

  try {
    const res = await fetch("/api/game-pc/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        games: lastGameQuery.games,
        titles: lastGameQuery.titles,
        resolution: lastGameQuery.resolution,
        monitor: includeMonitor ? 1 : 0,
        keyboard: includeKeyboard ? 1 : 0,
        setup: includeSetup ? 1 : 0,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `HTTP error ${res.status}`);
    applyQuotePayload(data);
  } catch (e) {
    hide(loading);
    hide(summaryCards);
    hide(quoteTabs);
    show(emptyState);
    if (errorBox) {
      errorBox.textContent = `게임PC 추천 실패: ${e.message}`;
      show(errorBox);
    }
  } finally {
    setLoadingMessage(LOADING_TEXT_COMPUZONE);
  }
}

$("#gamePcClose")?.addEventListener("click", closeGamePcModal);
$("#gamePcCancel")?.addEventListener("click", closeGamePcModal);
gamePcModal?.addEventListener("click", (e) => {
  if (e.target === gamePcModal) closeGamePcModal();
});
$("#gamePcPrev")?.addEventListener("click", () => setGameWizardStep(1));
$("#gamePcNext")?.addEventListener("click", () => {
  if (!selectedGames.length) {
    gameSetStatus("게임을 1개 이상 선택해주세요.", false);
    return;
  }
  gameSetStatus("");
  setGameWizardStep(2);
});
$("#gamePcSubmit")?.addEventListener("click", submitGamePcRecommend);
$$(".game-step-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    const step = parseInt(tab.dataset.step, 10);
    if (step === 2 && !selectedGames.length) {
      gameSetStatus("게임을 1개 이상 선택해주세요.", false);
      return;
    }
    setGameWizardStep(step);
  });
});

// ---------- 금액대로 찾기 · 카드/리스트 보기 ----------
function parseManwonInput(raw) {
  const n = Number(String(raw || "").replace(/[^\d]/g, ""));
  return Number.isFinite(n) ? n : 0;
}

function manwonToWon(man) {
  return parseManwonInput(man) * 10000;
}

function wonToManwon(won) {
  return Math.round((Number(won) || 0) / 10000);
}

function updateManwonPreview() {
  [
    ["#priceSearchMin", "#priceSearchMinWon"],
    ["#priceSearchMax", "#priceSearchMaxWon"],
  ].forEach(([inputSel, previewSel]) => {
    const preview = $(previewSel);
    if (!preview) return;
    const man = parseManwonInput($(inputSel)?.value);
    preview.textContent = man ? `= ${(man * 10000).toLocaleString("ko-KR")}원` : "";
  });
}

function fillManwonInputs() {
  if (!lastPriceQuery) {
    updateManwonPreview();
    return;
  }
  const minEl = $("#priceSearchMin");
  const maxEl = $("#priceSearchMax");
  if (minEl) minEl.value = String(wonToManwon(lastPriceQuery.min_price) || "");
  if (maxEl) maxEl.value = String(wonToManwon(lastPriceQuery.max_price) || "");
  updateManwonPreview();
}

function priceSetStatus(msg, ok = true) {
  const el = $("#priceSearchStatus");
  if (!el) return;
  el.textContent = msg || "";
  el.style.color = ok ? "var(--accent, #4caf50)" : "#e53935";
}

function quotePartName(q, category) {
  const row = (q.parts || []).find((p) => p.category === category);
  return row ? row.name : "";
}

function isPriceSearchResult(data = quoteData) {
  return data?.event_title === "금액대로 찾기";
}

function priceSearchMetaText() {
  if (!lastPriceQuery) return "";
  const matched = Number(quoteData?.matched_count || quoteData?.quotes?.length || 0);
  const shown = quoteData?.quotes?.length || 0;
  const lo = wonToManwon(lastPriceQuery.min_price);
  const hi = wonToManwon(lastPriceQuery.max_price);
  if (matched > shown) {
    return `${lo}만 원 ~ ${hi}만 원 · 총 ${matched.toLocaleString("ko-KR")}건 중 금액 낮은 순 ${shown}건`;
  }
  return `${lo}만 원 ~ ${hi}만 원 · ${shown}건`;
}

function resultMetaText() {
  const shown = quoteData?.quotes?.length || 0;
  if (currentCat === "price") return priceSearchMetaText();
  if (currentCat === "game-pc" && lastGameQuery) {
    return `${lastGameQuery.titles.join(", ")} · ${lastGameQuery.resolution} · ${shown}건`;
  }
  const title = quoteData?.event_title || quoteData?.series || "";
  return title ? `${title} · ${shown}건` : `${shown}건`;
}

function renderResultListBody(quotes) {
  const tbody = $("#resultListBody");
  if (!tbody) return;
  tbody.innerHTML = quotes
    .map((q, i) => {
      const cpu = quotePartName(q, "CPU") || "-";
      const vga = quotePartName(q, "그래픽카드") || "-";
      return `
      <tr class="${i === activeTab ? "active" : ""}" data-index="${i}">
        <td class="col-rank">${escapeHtml(q.tab_label || q.tier || `${i + 1}`)}</td>
        <td class="col-model">${escapeHtml(q.model)}</td>
        <td class="col-spec">${escapeHtml(cpu)}</td>
        <td class="col-spec">${escapeHtml(vga)}</td>
        <td class="col-price">${fmt(displayTotal(q))}</td>
      </tr>`;
    })
    .join("");
  tbody.querySelectorAll("tr").forEach((row) => {
    row.addEventListener("click", () => {
      activeTab = parseInt(row.dataset.index, 10);
      if (!quoteData?.quotes) return;
      renderTabs(quoteData.quotes);
      renderDetail(quoteData.quotes);
      applyResultView();
    });
  });
}

function setResultView(mode) {
  resultView = mode === "list" ? "list" : "card";
  applyResultView();
}

function applyResultView() {
  const quotes = quoteData?.quotes;
  const bar = $("#resultViewBar");
  const meta = $("#resultViewMeta");
  const toggle = $("#btnResultViewToggle");
  const cards = $("#summaryCards");
  const list = $("#resultListWrap");

  if (!quotes?.length) {
    hide(bar);
    hide(list);
    hide(cards);
    return;
  }

  if (currentStore !== "compuzone") {
    hide(bar);
    hide(list);
    renderSummaryCards(quotes);
    show(cards);
    show($("#quoteTabs"));
    return;
  }

  const isList = resultView === "list";
  if (meta) meta.textContent = resultMetaText();
  if (toggle) toggle.textContent = isList ? "카드로보기" : "리스트로보기";
  show(bar);

  if (isList) {
    hide(cards);
    show(list);
    renderResultListBody(quotes);
  } else {
    show(cards);
    hide(list);
    renderSummaryCards(quotes);
  }
  show($("#quoteTabs"));
}

const PRICE_SCOPE_TEXT = {
  assembled: "추천조립 · 아이웍스 · 프리미엄 전체를 판매가 기준으로 검색합니다.",
  notebook: "컴퓨존 노트북 전체를 판매가 기준으로 검색합니다.",
};

function activatePriceSearch(targetType = "assembled") {
  priceTargetType = targetType;
  currentCat = "price";
  const scope = $("#priceSearchScope");
  if (scope) scope.textContent = PRICE_SCOPE_TEXT[targetType] || PRICE_SCOPE_TEXT.assembled;
  updateExtrasUI();

  hide($("#error"));
  hide($("#loading"));
  hide($("#emptyState"));
  show($("#priceSearchBar"));
  fillManwonInputs();
  $("#priceSearchMin")?.focus();

  if (isPriceSearchResult() && quoteData?.quotes?.length) {
    applyResultView();
    priceSetStatus("");
  } else {
    hide($("#summaryCards"));
    hide($("#quoteTabs"));
    hideResultChrome();
    priceSetStatus("");
  }
  $("#quote")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function submitPriceSearch(opts = {}) {
  const keepView = Boolean(opts.keepView);
  const minMan = parseManwonInput($("#priceSearchMin")?.value);
  const maxMan = parseManwonInput($("#priceSearchMax")?.value);
  if (!minMan && !maxMan) {
    priceSetStatus("최저가와 최고가를 만원 단위로 입력해주세요. 예: 50 ~ 70", false);
    return;
  }
  if (!maxMan) {
    priceSetStatus("최고가를 만원 단위로 입력해주세요.", false);
    return;
  }
  if (maxMan < minMan) {
    priceSetStatus("최고가는 최저가보다 크거나 같아야 합니다.", false);
    return;
  }

  const minPrice = manwonToWon(minMan);
  const maxPrice = manwonToWon(maxMan);
  lastPriceQuery = { min_price: minPrice, max_price: maxPrice, type: priceTargetType };
  currentCat = "price";
  if (!keepView) resultView = "card";
  updateExtrasUI();
  $$(".cat-btn").forEach((b) => b.classList.remove("active"));
  $$(".game-cat-btn").forEach((b) => b.classList.remove("active"));
  $$(".price-cat-btn").forEach((b) => b.classList.toggle("active", b.dataset.type === priceTargetType));
  show($("#priceSearchBar"));
  updateManwonPreview();

  const loading = $("#loading");
  const errorBox = $("#error");
  const emptyState = $("#emptyState");
  const summaryCards = $("#summaryCards");
  const quoteTabs = $("#quoteTabs");
  const loadingText = loading?.querySelector("p");
  const submitBtn = $("#priceSearchSubmit");

  priceSetStatus(
    priceTargetType === "notebook"
      ? "노트북을 검색하는 중… 컴퓨존 노트북 목록을 확인합니다."
      : "조립PC를 검색하는 중… 추천·아이웍스·프리미엄 목록을 확인합니다."
  );
  if (submitBtn) submitBtn.disabled = true;
  show(loading);
  if (loadingText) {
    if (priceTargetType === "notebook") loadingText.textContent = "금액대에 맞는 노트북을 검색하는 중…";
    else loadingText.textContent = "금액대에 맞는 조립PC를 검색하는 중…";
  }
  hide(errorBox);
  hide(emptyState);
  hide(summaryCards);
  hide(quoteTabs);
  hideResultChrome();

  try {
    const res = await fetch("/api/price-search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        min_price: minPrice,
        max_price: maxPrice,
        target_type: priceTargetType,
        monitor: includeMonitor ? 1 : 0,
        keyboard: includeKeyboard ? 1 : 0,
        setup: includeSetup ? 1 : 0,
      }),
    });
    const data = await res.json().catch(() => ({}));
    const detail = Array.isArray(data.detail)
      ? data.detail.map((x) => x.msg || x).join(" ")
      : data.detail;
    if (!res.ok) throw new Error(detail || `HTTP error ${res.status}`);
    applyQuotePayload(data);
    const shown = data.quotes?.length || 0;
    const matched = Number(data.matched_count || shown);
    priceSetStatus(
      matched > shown
        ? `총 ${matched.toLocaleString("ko-KR")}건 중 상위 ${shown}건을 표시합니다.`
        : `${shown}건을 찾았습니다.`
    );
  } catch (e) {
    hide(loading);
    hide(summaryCards);
    hide(quoteTabs);
    hideResultChrome();
    show($("#priceSearchBar"));
    priceSetStatus(e.message, false);
    if (errorBox) {
      errorBox.textContent = `금액대 검색 실패: ${e.message}`;
      show(errorBox);
    }
  } finally {
    if (submitBtn) submitBtn.disabled = false;
    setLoadingMessage(LOADING_TEXT_COMPUZONE);
  }
}

$("#priceSearchSubmit")?.addEventListener("click", () => submitPriceSearch());
$("#btnResultViewToggle")?.addEventListener("click", () => {
  setResultView(resultView === "list" ? "card" : "list");
});
["#priceSearchMin", "#priceSearchMax"].forEach((sel) => {
  const el = $(sel);
  el?.addEventListener("input", updateManwonPreview);
  el?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      updateManwonPreview();
      submitPriceSearch();
    }
  });
});

document.addEventListener("DOMContentLoaded", () => {
  // [종료] 후 새로고침된 페이지: 견적을 불러오지 않고 서버 종료 · 창 닫기만 한다
  if (new URLSearchParams(window.location.search).get("shutdown") === "1") {
    finishShutdown();
    return;
  }
  initNetworkModal();
  updateExtrasUI();
  loadQuoteInfo();
  loadTrReportsList();
  fetchQuotes("ai");
});

const logicSection = document.getElementById("logic");
const navLogic = document.getElementById("navLogic");
const navQuote = document.getElementById("navQuote");

function setLogicOpen(open) {
  if (open) {
    show(logicSection);
    logicSection.setAttribute("aria-hidden", "false");
    navLogic?.classList.add("is-active");
  } else {
    hide(logicSection);
    logicSection.setAttribute("aria-hidden", "true");
    navLogic?.classList.remove("is-active");
  }
}

navLogic?.addEventListener("click", (e) => {
  e.preventDefault();
  const willOpen = logicSection.classList.contains("hidden");
  setLogicOpen(willOpen);
  if (willOpen) {
    navQuote?.classList.remove("is-active");
    logicSection.scrollIntoView({ behavior: "smooth", block: "start" });
  }
});

navQuote?.addEventListener("click", (e) => {
  e.preventDefault();
  setLogicOpen(false);
  navQuote.classList.add("is-active");
  document.getElementById("quote").scrollIntoView({ behavior: "smooth", block: "start" });
});

if (window.location.hash === "#logic") {
  setLogicOpen(true);
}

// ---------- 앱 종료 (백엔드 · 포트 · 캐시 · 브라우저) ----------
async function clearClientCaches() {
  try {
    sessionStorage.clear();
  } catch (_) {}
  try {
    localStorage.clear();
  } catch (_) {}
  try {
    if (typeof caches !== "undefined") {
      const keys = await caches.keys();
      await Promise.all(keys.map((k) => caches.delete(k)));
    }
  } catch (_) {}
}

function showShutdownDonePage() {
  document.body.innerHTML = `
    <div style="min-height:100vh;display:flex;align-items:center;justify-content:center;background:#070b14;color:#e8edf5;font-family:'Noto Sans KR',sans-serif;padding:24px;text-align:center;">
      <div>
        <h1 style="font-size:1.6rem;margin-bottom:12px;">서버가 종료되었습니다</h1>
        <p style="color:#8b9cb3;margin-bottom:8px;">백엔드 종료 · 포트 정리 · 캐시 삭제를 완료했습니다.</p>
        <p style="color:#8b9cb3;">이 창이 자동으로 닫히지 않으면 직접 닫아 주세요.</p>
      </div>
    </div>`;
}

// 확인 팝업 없이 바로 종료
async function shutdownApp() {
  closeCalcWindow();

  const buttons = [$("#btnShutdown"), $("#btnShutdownNav")].filter(Boolean);
  buttons.forEach((b) => {
    b.disabled = true;
    b.textContent = "종료 중…";
  });

  await clearClientCaches();

  // 브라우저 새로고침 → 새로 뜬 페이지가 finishShutdown() 으로 서버 종료 후 창을 닫는다
  window.location.replace("/?shutdown=1");
}

async function finishShutdown() {
  // 주소를 "/" 로 되돌려 둔다 (탭 복원 시 다시 종료되지 않도록)
  history.replaceState(null, "", "/");
  showShutdownDonePage();

  try {
    await fetch("/api/shutdown", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      keepalive: true,
    });
  } catch (_) {
    // 서버가 바로 내려가도 정상
  }

  // 앱 전용 창이면 닫히고, 일반 탭이면 안내 페이지가 남는다
  setTimeout(() => {
    try {
      window.open("", "_self");
      window.close();
    } catch (_) {}
    showShutdownDonePage();
  }, 400);
}

$("#btnShutdown")?.addEventListener("click", shutdownApp);
$("#btnShutdownNav")?.addEventListener("click", shutdownApp);

// ==========================================================================
// 🔄 업데이트 (Update)
// ==========================================================================
// 1) GitHub 확인 → 2) 업데이트 유무 팝업 · 승인 → 3) git pull → 4) 재시작 안내 팝업
async function updateApp() {
  let info;
  showBusy("GitHub에서 업데이트 확인 중…");
  try {
    const res = await fetch("/api/update/check", { cache: "no-store" });
    info = await res.json();
  } catch (err) {
    info = { ok: false, message: "서버에 연결할 수 없습니다." };
  } finally {
    hideBusy();
  }
  if (!info.ok) {
    alert("업데이트 확인 실패\n\n" + info.message);
    return;
  }
  if (!info.behind) {
    alert("현재 최신 버전입니다.\n받을 업데이트가 없습니다.");
    return;
  }

  const more = info.behind > info.commits.length ? `\n… 외 ${info.behind - info.commits.length}건` : "";
  const dirtyNote = info.dirty ? "\n\n※ 이 PC에서 수정한 파일이 있어 업데이트가 실패할 수 있습니다." : "";
  if (!confirm(`새 업데이트가 ${info.behind}건 있습니다.\n\n${info.commits.join("\n")}${more}${dirtyNote}\n\n지금 업데이트할까요?`)) {
    return;
  }

  let result;
  showBusy("업데이트 다운로드 중 (git pull)…");
  try {
    const res = await fetch("/api/update/apply", { method: "POST" });
    result = await res.json();
  } catch (err) {
    result = { ok: false, message: "서버에 연결할 수 없습니다." };
  } finally {
    hideBusy();
  }
  if (!result.ok) {
    alert("업데이트 실패\n\n" + result.message);
    return;
  }

  const depsNote = result.deps_changed ? "\n필요한 구성요소(의존성)도 재시작할 때 자동으로 설치됩니다." : "";
  if (!confirm(`업데이트가 완료되었습니다. (변경 파일 ${result.changed_files}개)\n변경 내용을 적용하려면 프로그램을 재시작해야 합니다.${depsNote}\n\n지금 재시작할까요?`)) {
    alert("다음에 프로그램을 재시작하면 업데이트가 적용됩니다.");
    return;
  }
  try {
    await fetch("/api/update/restart", { method: "POST" });
  } catch (_) {}
  reloadWhenServerBack();
}

// 업데이트 후 재시작된 서버가 응답하면 이 창을 새로고침 (START.bat은 새 창을 열지 않음)
function reloadWhenServerBack() {
  const startedAt = Date.now();
  let wentDown = false;
  showBusy("재시작 중… 서버 응답 대기");
  const timer = setInterval(async () => {
    let up = false;
    try {
      const r = await fetch("/api/server-info", { cache: "no-store" });
      up = r.ok;
    } catch (_) {
      up = false;
    }
    if (!up) wentDown = true;
    const elapsed = Date.now() - startedAt;
    const sec = Math.round(elapsed / 1000);
    setBusyText(wentDown ? `재시작 중… 구성요소 확인 후 서버 대기 (${sec}초)` : `재시작 준비 중… (${sec}초)`);
    // 기존 서버가 내려간 뒤 다시 올라왔거나, 3분이 지나면 새로고침
    if ((wentDown && up) || elapsed > 180000) {
      clearInterval(timer);
      window.location.reload();
    }
  }, 1500);
}

$("#btnUpdateApp")?.addEventListener("click", updateApp);

$("#btnDownloadExcel")?.addEventListener("click", (e) => {
  e.preventDefault();
  // href 는 CTO 적용 전 값일 수 있으므로 누를 때 다시 만든다.
  downloadWithBusy(`/api/download/excel?${excelQueryParams()}`, {}, "견적서.xlsx", "견적서 Excel 생성 중…");
});

// ==========================================================================
// 📋 컴퓨터 작업내역서 (Task Report) 프론트엔드 컨트롤러
// ==========================================================================
let trReportsList = [];

const trModal = $("#taskReportModal");
const trTabFormBtn = $("#trTabFormBtn");
const trTabListBtn = $("#trTabListBtn");
const trFormPanel = $("#trFormPanel");
const trListPanel = $("#trListPanel");

// 서명 캔버스 초기화
const signCanvasCust = $("#trSignCanvasCust");
const signCanvasTech = $("#trSignCanvasTech");

function initSignCanvas(canvas) {
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  ctx.strokeStyle = "#0f172a";
  ctx.lineWidth = 2;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";

  let isDrawing = false;
  let lastX = 0, lastY = 0;

  function getPos(e) {
    const rect = canvas.getBoundingClientRect();
    const clientX = e.touches ? e.touches[0].clientX : e.clientX;
    const clientY = e.touches ? e.touches[0].clientY : e.clientY;
    return [clientX - rect.left, clientY - rect.top];
  }

  function startDraw(e) {
    isDrawing = true;
    [lastX, lastY] = getPos(e);
    if (e.type === "touchstart") e.preventDefault();
  }

  function draw(e) {
    if (!isDrawing) return;
    const [x, y] = getPos(e);
    ctx.beginPath();
    ctx.moveTo(lastX, lastY);
    ctx.lineTo(x, y);
    ctx.stroke();
    [lastX, lastY] = [x, y];
    if (e.type === "touchmove") e.preventDefault();
  }

  function stopDraw() {
    isDrawing = false;
  }

  canvas.addEventListener("mousedown", startDraw);
  canvas.addEventListener("mousemove", draw);
  canvas.addEventListener("mouseup", stopDraw);
  canvas.addEventListener("mouseleave", stopDraw);

  canvas.addEventListener("touchstart", startDraw, { passive: false });
  canvas.addEventListener("touchmove", draw, { passive: false });
  canvas.addEventListener("touchend", stopDraw);
}

function clearSignCanvas(type) {
  const canvas = type === "cust" ? signCanvasCust : signCanvasTech;
  if (canvas) {
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
  }
}

// 작업 행 렌더링 & 계산
function renderTrTaskRows(tasks = []) {
  const tbody = $("#trTasksTbody");
  if (!tbody) return;
  tbody.innerHTML = "";

  const items = tasks.length > 0 ? tasks : [
    { no: 1, description: "", parts: "", cost: 0 },
    { no: 2, description: "", parts: "", cost: 0 },
    { no: 3, description: "", parts: "", cost: 0 },
  ];

  items.forEach((task, idx) => {
    const tr = document.createElement("tr");
    tr.className = "tr-task-row";
    tr.innerHTML = `
      <td style="text-align:center;">
        <span class="tr-row-no">${idx + 1}</span>
        ${items.length > 1 ? `<button type="button" class="tr-task-del-btn no-print" title="행 삭제" onclick="deleteTrTaskRow(${idx})">✕</button>` : ''}
      </td>
      <td><input type="text" class="tr-inp tr-task-desc" value="${escapeHtml(task.description || '')}" placeholder="작업 내용 (예: SSD 교체 및 윈도우 재설치)" /></td>
      <td><input type="text" class="tr-inp tr-task-parts" value="${escapeHtml(task.parts || '')}" placeholder="부품 (예: SK하이닉스 500GB)" /></td>
      <td><input type="number" class="tr-inp tr-task-cost" style="text-align:right;" value="${task.cost || ''}" placeholder="0" oninput="calcTrTotal()" /></td>
    `;
    tbody.appendChild(tr);
  });

  calcTrTotal();
}

function addTrTaskRow() {
  const currentTasks = getTrTasksFromForm();
  currentTasks.push({ no: currentTasks.length + 1, description: "", parts: "", cost: 0 });
  renderTrTaskRows(currentTasks);
}

window.deleteTrTaskRow = function(idx) {
  const currentTasks = getTrTasksFromForm();
  if (currentTasks.length <= 1) return;
  currentTasks.splice(idx, 1);
  renderTrTaskRows(currentTasks);
};

function getTrTasksFromForm() {
  const rows = document.querySelectorAll("#trTasksTbody tr");
  const tasks = [];
  rows.forEach((tr, idx) => {
    const desc = tr.querySelector(".tr-task-desc")?.value.trim() || "";
    const parts = tr.querySelector(".tr-task-parts")?.value.trim() || "";
    const cost = parseInt(tr.querySelector(".tr-task-cost")?.value, 10) || 0;
    tasks.push({ no: idx + 1, description: desc, parts: parts, cost: cost });
  });
  return tasks;
}

function calcTrTotal() {
  const tasks = getTrTasksFromForm();
  const total = tasks.reduce((sum, t) => sum + (t.cost || 0), 0);
  const display = $("#trTotalCostDisplay");
  if (display) display.textContent = total.toLocaleString("ko-KR");
  return total;
}

// 폼 데이터 수집 / 채우기
function getTrFormData() {
  const companyName = $("#trCompanyName")?.value.trim() || "0000 ";
  const companyAddress = $("#trCompanyAddress")?.value.trim() || "";
  const companyPhone = $("#trCompanyPhone")?.value.trim() || "";
  const businessNumber = $("#trBusinessNumber")?.value.trim() || "";
  const docNo = $("#trDocNo")?.value.trim() || "";
  const receiptDate = $("#trReceiptDate")?.value || new Date().toISOString().split("T")[0];

  const customerName = $("#trCustName")?.value.trim() || "";
  const customerPhone = $("#trCustPhone")?.value.trim() || "";
  const customerAddress = $("#trCustAddress")?.value.trim() || "";
  const customerEmail = $("#trCustEmail")?.value.trim() || "";
  const contactMethod = document.querySelector('input[name="trContactMethod"]:checked')?.value || "전화";

  const deviceType = document.querySelector('input[name="trDeviceType"]:checked')?.value || "데스크탑";
  const deviceModel = $("#trDeviceModel")?.value.trim() || "";
  const serialNumber = $("#trSerialNumber")?.value.trim() || "";
  const purchaseDate = $("#trPurchaseDate")?.value || "";

  const accessories = [];
  document.querySelectorAll(".tr-acc-check:checked").forEach((chk) => accessories.push(chk.value));
  const accessoriesCustom = $("#trAccCustom")?.value.trim() || "";

  const symptoms = $("#trSymptoms")?.value.trim() || "";
  const tasks = getTrTasksFromForm();
  const totalCost = calcTrTotal();

  const status = document.querySelector('input[name="trStatus"]:checked')?.value || "완료";
  const expectedDate = $("#trExpectedDate")?.value || receiptDate;
  const technician = $("#trTechnician")?.value.trim() || "김대건";
  const warrantyPeriod = $("#trWarrantyPeriod")?.value.trim() || "3개월";

  const customerSign = $("#trSignTextCust")?.value.trim() || "";
  const technicianSign = $("#trSignTextTech")?.value.trim() || "";

  return {
    id: currentTrId,
    company_name: companyName,
    company_address: companyAddress,
    company_phone: companyPhone,
    business_number: businessNumber,
    doc_no: docNo,
    receipt_date: receiptDate,
    customer_name: customerName,
    customer_phone: customerPhone,
    customer_address: customerAddress,
    customer_email: customerEmail,
    contact_method: contactMethod,
    device_type: deviceType,
    device_model: deviceModel,
    serial_number: serialNumber,
    purchase_date: purchaseDate,
    accessories: accessories,
    accessories_custom: accessoriesCustom,
    symptoms: symptoms,
    tasks: tasks,
    total_cost: totalCost,
    status: status,
    expected_date: expectedDate,
    technician: technician,
    warranty_period: warrantyPeriod,
    customer_sign: customerSign,
    technician_sign: technicianSign,
  };
}

function fillTrForm(data) {
  currentTrId = data?.id || null;
  const badge = $("#trCurrentStatusBadge");
  if (badge) {
    badge.textContent = currentTrId ? `작업내역서 #${data.doc_no || currentTrId}` : "신규 작성중";
  }

  // 작업내역서의 업체정보: 견적정보의 공급자 정보를 우선 기입
  if ($("#trCompanyName")) $("#trCompanyName").value = data?.company_name || quoteInfo?.supplier_name || "대한민국주식회사";
  if ($("#trCompanyAddress")) $("#trCompanyAddress").value = data?.company_address || quoteInfo?.supplier_address || "서울시";
  if ($("#trCompanyPhone")) $("#trCompanyPhone").value = data?.company_phone || quoteInfo?.supplier_phone || "010-1234-5678";
  if ($("#trBusinessNumber")) $("#trBusinessNumber").value = data?.business_number || "121-18-24250";

  const todayStr = new Date().toISOString().split("T")[0];
  if ($("#trDocNo")) $("#trDocNo").value = data?.doc_no || `TR-${todayStr.replace(/-/g, "")}-${Math.floor(Math.random()*900+100)}`;
  if ($("#trReceiptDate")) $("#trReceiptDate").value = data?.receipt_date || todayStr;

  if ($("#trCustName")) $("#trCustName").value = data?.customer_name || (!currentTrId && quoteInfo?.customer_name ? quoteInfo.customer_name : "");
  if ($("#trCustPhone")) $("#trCustPhone").value = data?.customer_phone || (!currentTrId && quoteInfo?.customer_phone ? quoteInfo.customer_phone : "");
  if ($("#trCustAddress")) $("#trCustAddress").value = data?.customer_address || (!currentTrId && quoteInfo?.customer_address ? quoteInfo.customer_address : "");
  if ($("#trCustEmail")) $("#trCustEmail").value = data?.customer_email || (!currentTrId && quoteInfo?.customer_email ? quoteInfo.customer_email : "");

  // 라디오 선택
  const cm = data?.contact_method || "전화";
  const cmRadio = document.querySelector(`input[name="trContactMethod"][value="${cm}"]`);
  if (cmRadio) cmRadio.checked = true;

  const dt = data?.device_type || "데스크탑";
  const dtRadio = document.querySelector(`input[name="trDeviceType"][value="${dt}"]`);
  if (dtRadio) dtRadio.checked = true;

  if ($("#trDeviceModel")) $("#trDeviceModel").value = data?.device_model || "";
  if ($("#trSerialNumber")) $("#trSerialNumber").value = data?.serial_number || "";
  if ($("#trPurchaseDate")) $("#trPurchaseDate").value = data?.purchase_date || "";

  // 체크박스
  const acc = data?.accessories || [];
  document.querySelectorAll(".tr-acc-check").forEach((chk) => {
    chk.checked = acc.includes(chk.value);
  });
  if ($("#trAccCustom")) $("#trAccCustom").value = data?.accessories_custom || "";

  if ($("#trSymptoms")) $("#trSymptoms").value = data?.symptoms || "";

  // 작업 내역 행
  renderTrTaskRows(data?.tasks || []);

  const st = data?.status || "완료";
  const stRadio = document.querySelector(`input[name="trStatus"][value="${st}"]`);
  if (stRadio) stRadio.checked = true;

  if ($("#trExpectedDate")) $("#trExpectedDate").value = data?.expected_date || todayStr;
  if ($("#trTechnician")) $("#trTechnician").value = data?.technician || "김대건";
  if ($("#trWarrantyPeriod")) $("#trWarrantyPeriod").value = data?.warranty_period || "3개월";

  if ($("#trSignTextCust")) $("#trSignTextCust").value = data?.customer_sign || "";
  if ($("#trSignTextTech")) $("#trSignTextTech").value = data?.technician_sign || "";

  clearSignCanvas("cust");
  clearSignCanvas("tech");
  updateBlinkWarnings();
}

function resetTrForm() {
  fillTrForm(null);
}

// 탭 전환
function switchTrTab(tab) {
  if (tab === "form") {
    trTabFormBtn?.classList.add("active", "btn-primary");
    trTabFormBtn?.classList.remove("btn-outline");
    trTabListBtn?.classList.remove("active", "btn-primary");
    trTabListBtn?.classList.add("btn-outline");
    show(trFormPanel);
    hide(trListPanel);
  } else {
    trTabListBtn?.classList.add("active", "btn-primary");
    trTabListBtn?.classList.remove("btn-outline");
    trTabFormBtn?.classList.remove("active", "btn-primary");
    trTabFormBtn?.classList.add("btn-outline");
    hide(trFormPanel);
    show(trListPanel);
    loadTrReportsList();
  }
}

// 작업내역서 모달 열기/닫기
function openTrModal() {
  if (!trModal) return;
  initSignCanvas(signCanvasCust);
  initSignCanvas(signCanvasTech);
  if (!currentTrId) {
    resetTrForm();
  }
  switchTrTab("form");
  loadTrReportsList();
  show(trModal);
  updateBlinkWarnings();
}

function closeTrModal() {
  if (!trModal) return;
  hide(trModal);
}

// API 서버 통신
async function loadTrReportsList() {
  try {
    const res = await fetch("/api/task-reports");
    if (!res.ok) throw new Error("목록 조회 실패");
    const data = await res.json();
    trReportsList = data.reports || [];
    if (trReportsList.length > 0) {
      const hasRealReport = trReportsList.some((r) => !isDummyValue(r.company_name, DUMMY_VALUES.names));
      if (hasRealReport) {
        isTaskReportSaved = true;
      }
    } else {
      isTaskReportSaved = false;
    }
    renderTrListTable(trReportsList);
    const countEl = $("#trListCount");
    if (countEl) countEl.textContent = trReportsList.length;
    updateBlinkWarnings();
  } catch (e) {
    console.warn("작업내역서 목록 로드 오류:", e);
  }
}

function renderTrListTable(reports) {
  const tbody = $("#trListTbody");
  if (!tbody) return;
  tbody.innerHTML = "";

  if (reports.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding:32px; color:var(--muted);">저장된 작업 내역서가 없습니다.</td></tr>`;
    return;
  }

  reports.forEach((rep) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(rep.receipt_date || "-")}</td>
      <td><strong>${escapeHtml(rep.doc_no || rep.id)}</strong></td>
      <td>${escapeHtml(rep.customer_name || "-")}</td>
      <td>${escapeHtml(rep.customer_phone || "-")}</td>
      <td>${escapeHtml(rep.device_type || "")} ${escapeHtml(rep.device_model || "")}</td>
      <td><span class="badge-status">${escapeHtml(rep.status || "접수")}</span></td>
      <td style="font-weight:700; color:#fbbf24;">${(rep.total_cost || 0).toLocaleString("ko-KR")} 원</td>
      <td>
        <div class="tr-list-actions">
          <button type="button" class="btn btn-xs btn-primary" onclick="loadTrReportItem('${rep.id}')">📂 열기</button>
          <button type="button" class="btn btn-xs btn-outline" onclick="downloadTrExcelById('${rep.id}')">📥 Excel</button>
          <button type="button" class="btn btn-xs btn-shutdown" onclick="deleteTrReportItem('${rep.id}')">✕ 삭제</button>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

window.loadTrReportItem = async function(id) {
  try {
    const res = await fetch(`/api/task-reports/${id}`);
    if (!res.ok) throw new Error("내역 조회 실패");
    const data = await res.json();
    fillTrForm(data.report);
    switchTrTab("form");
  } catch (e) {
    alert(e.message);
  }
};

window.deleteTrReportItem = async function(id) {
  if (!confirm("이 작업내역서를 삭제하시겠습니까?")) return;
  try {
    const res = await fetch(`/api/task-reports/${id}`, { method: "DELETE" });
    if (!res.ok) throw new Error("삭제 실패");
    if (currentTrId === id) resetTrForm();
    loadTrReportsList();
  } catch (e) {
    alert(e.message);
  }
};

window.downloadTrExcelById = function(id) {
  downloadWithBusy(`/api/task-reports/${id}/download/excel`, {}, "작업내역서.xlsx", "작업내역서 Excel 생성 중…");
};

// 저장
async function saveTrForm() {
  const data = getTrFormData();
  if (!data.customer_name) {
    alert("고객명을 입력해 주세요.");
    $("#trCustName")?.focus();
    return;
  }

  const isUpdate = Boolean(currentTrId);
  const url = isUpdate ? `/api/task-reports/${currentTrId}` : "/api/task-reports";
  const method = isUpdate ? "PUT" : "POST";

  try {
    const btn = $("#trBtnSave");
    if (btn) { btn.disabled = true; btn.textContent = "저장 중…"; }

    const res = await fetch(url, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });

    if (!res.ok) throw new Error("저장에 실패했습니다.");
    const result = await res.json();
    currentTrId = result.report.id;
    isTaskReportSaved = true;
    fillTrForm(result.report);
    loadTrReportsList();
    updateBlinkWarnings();
    alert("✔ 작업내역서가 성공적으로 저장되었습니다.");
  } catch (e) {
    alert(e.message);
  } finally {
    const btn = $("#trBtnSave");
    if (btn) { btn.disabled = false; btn.textContent = "💾 저장하기"; }
  }
}

// 인쇄 / PDF 출력
function printTrPaper() {
  // 폼의 최신 입력값들을 인쇄 전 DOM value에 정확히 매핑
  window.print();
}

// 실시간 엑셀 다운로드
async function downloadTrExcelDirect() {
  const data = getTrFormData();
  const custName = data.customer_name || "고객";
  const docNo = data.doc_no || "01";
  await downloadWithBusy(
    "/api/task-reports/download/excel",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) },
    `작업내역서_${custName}_${docNo}.xlsx`,
    "작업내역서 Excel 생성 중…"
  );
}

// 이벤트 바인딩
$("#btnOpenTaskReport")?.addEventListener("click", openTrModal);
$("#trModalClose")?.addEventListener("click", closeTrModal);
trModal?.addEventListener("click", (e) => {
  if (e.target === trModal) closeTrModal();
});

trTabFormBtn?.addEventListener("click", () => switchTrTab("form"));
trTabListBtn?.addEventListener("click", () => switchTrTab("list"));

$("#trBtnNew")?.addEventListener("click", () => {
  if (confirm("새 작업내역서를 작성하시겠습니까? (현재 작성 내용 초기화)")) {
    resetTrForm();
  }
});
$("#trBtnAddTaskRow")?.addEventListener("click", addTrTaskRow);
$("#trBtnSave")?.addEventListener("click", saveTrForm);
$("#trBtnPrint")?.addEventListener("click", printTrPaper);
$("#trBtnExcel")?.addEventListener("click", downloadTrExcelDirect);
$("#trBtnRefreshList")?.addEventListener("click", loadTrReportsList);

// 작업내역서 업체 정보 변경 시 실시간 깜박임 갱신
["#trCompanyName", "#trCompanyAddress", "#trCompanyPhone"].forEach((sel) => {
  $(sel)?.addEventListener("input", () => {
    updateBlinkWarnings();
  });
});

// 견적 정보 입력 필드 변경 시 실시간 경고 배너 갱신
Object.values(qiFields).forEach((sel) => {
  $(sel)?.addEventListener("input", () => {
    const currentValues = {};
    for (const [key, fieldSel] of Object.entries(qiFields)) {
      currentValues[key] = $(fieldSel)?.value.trim() || "";
    }
    const isStillDummy = isQuoteInfoDummy(currentValues);
    $("#qiWarningNotice")?.classList.toggle("hidden", !isStillDummy);
  });
});

// 검색 필터
$("#trSearchInput")?.addEventListener("input", (e) => {
  const q = e.target.value.toLowerCase().trim();
  if (!q) {
    renderTrListTable(trReportsList);
    return;
  }
  const filtered = trReportsList.filter((rep) => {
    return (
      (rep.customer_name || "").toLowerCase().includes(q) ||
      (rep.customer_phone || "").toLowerCase().includes(q) ||
      (rep.doc_no || "").toLowerCase().includes(q) ||
      (rep.device_model || "").toLowerCase().includes(q)
    );
  });
  renderTrListTable(filtered);
});
// ---------- 독립 창 계산기 ----------
const CALC_POPUP_NAME = "quoteCalcPopup";
const CALC_POPUP_W = 280;
const CALC_POPUP_H = 540;
let calcWin = null;

function calcPopupPos() {
  const availLeft = window.screen.availLeft || 0;
  const availTop = window.screen.availTop || 0;
  const availW = window.screen.availWidth || window.screen.width;
  const availH = window.screen.availHeight || window.screen.height;
  let left = Math.round((window.screenX || 0) + (window.outerWidth || 0) + 8);
  let top = Math.round((window.screenY || 0) + 72);
  if (left + CALC_POPUP_W > availLeft + availW) left = availLeft + availW - CALC_POPUP_W - 8;
  if (left < availLeft) left = availLeft + 8;
  if (top + CALC_POPUP_H > availTop + availH) top = availTop + availH - CALC_POPUP_H - 8;
  if (top < availTop) top = availTop + 8;
  return { left, top, width: CALC_POPUP_W, height: CALC_POPUP_H };
}

function closeCalcWindow() {
  try {
    if (calcWin && !calcWin.closed) calcWin.close();
  } catch (_) {}
  calcWin = null;
}

function placeCalcWindow(win, pos) {
  if (!win || win.closed) return;
  try {
    win.resizeTo(pos.width, pos.height);
    win.moveTo(pos.left, pos.top);
  } catch (_) {}
}

async function openCalcWindow() {
  const pos = calcPopupPos();
  try {
    const res = await fetch("/api/calc/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(pos),
    });
    if (res.ok) return;
  } catch (_) {}
  if (calcWin && !calcWin.closed) {
    try { calcWin.focus(); } catch (_) {}
    return;
  }
  const features = [
    "popup=true",
    "width=" + pos.width,
    "height=" + pos.height,
    "left=" + pos.left,
    "top=" + pos.top,
    "resizable=yes",
    "scrollbars=no",
  ].join(",");
  const win = window.open("/calc", CALC_POPUP_NAME, features);
  if (!win || win === window) {
    alert("계산기 창이 차단되었습니다. 브라우저에서 이 사이트의 팝업을 허용해 주세요.");
    return;
  }
  calcWin = win;
  placeCalcWindow(win, pos);
  try { calcWin.focus(); } catch (_) {}
}

$("#btnOpenCalc")?.addEventListener("click", openCalcWindow);
