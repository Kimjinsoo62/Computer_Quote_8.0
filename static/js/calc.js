/** 독립 창으로 뜨는 계산기. 기본 사칙연산 + 부가세 역계산. */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...(root.querySelectorAll?.(sel) || [])];

const VAT_RATE = 0.1;
// 10조(1e13)를 여유 있게 담도록 정수부 14자리까지 받는다.
const CALC_MAX_INT_DIGITS = 14;
const CALC_MAX_DECIMALS = 4;
const CALC_MAX = 10 ** CALC_MAX_INT_DIGITS - 1;
const CALC_OP_SIGN = { "+": "+", "-": "−", "*": "×", "/": "÷" };

const calcPanel = $("#calcPanel");
let calcEntry = "0";
let calcAcc = null;
let calcOp = null;
let calcFresh = true;
let calcError = "";
let calcVat = null;

/** "1234.5" → "1,234.5". 입력 중인 "1234." 의 끝점은 그대로 둔다. */
function calcFormatRaw(raw) {
  const neg = String(raw).startsWith("-");
  const body = neg ? String(raw).slice(1) : String(raw);
  const dot = body.indexOf(".");
  const int = dot < 0 ? body : body.slice(0, dot);
  const grouped = (int || "0").replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return (neg ? "-" : "") + grouped + (dot < 0 ? "" : body.slice(dot));
}

/** 계산 결과를 입력 원문으로. 범위를 넘거나 계산 불가면 null. */
function calcNumToRaw(value) {
  if (value === null || !Number.isFinite(value)) return null;
  if (Math.abs(value) > CALC_MAX) return null;
  let s = value.toFixed(CALC_MAX_DECIMALS);
  if (s.includes(".")) s = s.replace(/0+$/, "").replace(/\.$/, "");
  return s === "-0" ? "0" : s;
}

function calcRenderValue() {
  const el = $("#calcValue");
  if (!el) return;
  const text = calcError || calcFormatRaw(calcEntry);
  el.textContent = text;
  el.classList.toggle("calc-error", Boolean(calcError));
  el.style.fontSize = calcError
    ? ""
    : text.length > 17
      ? "0.78rem"
      : text.length > 14
        ? "0.9rem"
        : text.length > 11
          ? "1.05rem"
          : "1.2rem";
}

function calcSetExpr(text) {
  const el = $("#calcExpr");
  if (el) el.textContent = text || "\u00a0";
}

function calcRenderExpr() {
  calcSetExpr(
    calcOp && calcAcc !== null
      ? `${calcFormatRaw(calcNumToRaw(calcAcc) ?? "0")} ${CALC_OP_SIGN[calcOp]}`
      : ""
  );
}

function calcRenderVat(vat) {
  calcVat = vat;
  const put = (sel, v) => {
    const el = $(sel);
    if (!el) return;
    el.textContent = v == null ? "-" : `${calcFormatRaw(calcNumToRaw(v) ?? "0")}원`;
  };
  put("#calcVatSupply", vat?.supply);
  put("#calcVatTax", vat?.tax);
  put("#calcVatTotal", vat?.total);
}

function calcReset(clearVat) {
  calcEntry = "0";
  calcAcc = null;
  calcOp = null;
  calcFresh = true;
  calcError = "";
  if (clearVat) calcRenderVat(null);
  calcRenderValue();
  calcRenderExpr();
}

function calcCommitResult(value) {
  const raw = calcNumToRaw(value);
  if (raw === null) {
    calcError = value === null ? "계산할 수 없습니다" : "10조 자리를 넘었습니다";
    calcAcc = null;
    calcOp = null;
    calcFresh = true;
    calcRenderValue();
    calcRenderExpr();
    return false;
  }
  calcError = "";
  calcEntry = raw;
  calcFresh = true;
  calcRenderValue();
  return true;
}

function calcInputNum(digits) {
  if (calcError) calcReset(false);
  if (calcFresh) {
    calcEntry = "0";
    calcFresh = false;
  }
  for (const d of String(digits)) {
    if (calcEntry === "0") {
      if (d !== "0") calcEntry = d;
      continue;
    }
    const dot = calcEntry.indexOf(".");
    const intLen = (dot < 0 ? calcEntry : calcEntry.slice(0, dot)).replace("-", "").length;
    if (dot < 0 ? intLen >= CALC_MAX_INT_DIGITS : calcEntry.length - dot - 1 >= CALC_MAX_DECIMALS) {
      break;
    }
    calcEntry += d;
  }
  calcRenderValue();
}

function calcInputDot() {
  if (calcError) calcReset(false);
  if (calcFresh) {
    calcEntry = "0";
    calcFresh = false;
  }
  if (!calcEntry.includes(".")) calcEntry += ".";
  calcRenderValue();
}

function calcBackspace() {
  if (calcError || calcFresh) {
    calcError = "";
    calcEntry = "0";
    calcFresh = false;
    calcRenderValue();
    return;
  }
  calcEntry = calcEntry.slice(0, -1);
  if (calcEntry === "" || calcEntry === "-") calcEntry = "0";
  calcRenderValue();
}

function calcApply(a, op, b) {
  if (op === "+") return a + b;
  if (op === "-") return a - b;
  if (op === "*") return a * b;
  if (op === "/") return b === 0 ? null : a / b;
  return b;
}

function calcSetOp(op) {
  if (calcError) return;
  const cur = Number(calcEntry);
  if (calcOp !== null && calcAcc !== null && !calcFresh) {
    if (!calcCommitResult(calcApply(calcAcc, calcOp, cur))) return;
    calcAcc = Number(calcEntry);
  } else {
    calcAcc = cur;
  }
  calcOp = op;
  calcFresh = true;
  calcRenderExpr();
}

function calcEquals() {
  if (calcError || calcOp === null || calcAcc === null) return;
  const expr =
    `${calcFormatRaw(calcNumToRaw(calcAcc) ?? "0")} ${CALC_OP_SIGN[calcOp]} ` +
    `${calcFormatRaw(calcEntry)} =`;
  const ok = calcCommitResult(calcApply(calcAcc, calcOp, Number(calcEntry)));
  calcAcc = null;
  calcOp = null;
  if (ok) calcSetExpr(expr);
}

function calcPercent() {
  if (calcError) return;
  const base = (calcOp === "+" || calcOp === "-") && calcAcc !== null ? calcAcc : 1;
  calcCommitResult((base * Number(calcEntry)) / 100);
}

function calcVatExtract() {
  if (calcError) return;
  const total = Math.round(Number(calcEntry));
  if (!Number.isFinite(total)) return;
  const supply = Math.round(total / (1 + VAT_RATE));
  calcRenderVat({ supply, tax: total - supply, total });
  calcAcc = null;
  calcOp = null;
  if (calcCommitResult(supply)) {
    calcSetExpr(`합계 ${calcFormatRaw(String(total))} → 공급가액`);
  }
}

function calcVatAdd() {
  if (calcError) return;
  const supply = Math.round(Number(calcEntry));
  if (!Number.isFinite(supply)) return;
  const tax = Math.round(supply * VAT_RATE);
  calcRenderVat({ supply, tax, total: supply + tax });
  calcAcc = null;
  calcOp = null;
  if (calcCommitResult(supply + tax)) {
    calcSetExpr(`공급가액 ${calcFormatRaw(String(supply))} → 합계`);
  }
}

function calcRunAction(act) {
  if (act === "clear") calcReset(true);
  else if (act === "back") calcBackspace();
  else if (act === "percent") calcPercent();
  else if (act === "dot") calcInputDot();
  else if (act === "equals") calcEquals();
}

function calcClose() {
  try {
    window.close();
  } catch (_) {}
}

function calcFitWindow() {
  if (!document.documentElement?.classList?.contains?.("calc-html")) return;
  if (typeof window.resizeTo !== "function") return;
  const panel = calcPanel || $("#calcPanel");
  if (!panel) return;
  const rect = panel.getBoundingClientRect();
  const extraW = Math.max(8, (window.outerWidth || 0) - (window.innerWidth || 0));
  const extraH = Math.max(36, (window.outerHeight || 0) - (window.innerHeight || 0));
  const w = Math.ceil(rect.width || panel.offsetWidth || 272);
  const h = Math.ceil(rect.height || panel.offsetHeight || 500);
  try {
    window.resizeTo(w + extraW, h + extraH);
  } catch (_) {}
}

function calcFlashKey(sel) {
  const btn = sel && $("#calcKeys")?.querySelector?.(sel);
  if (!btn) return;
  btn.classList.add("calc-key-hit");
  setTimeout(() => btn.classList.remove("calc-key-hit"), 110);
}

$("#calcClose")?.addEventListener("click", calcClose);
$("#calcVatExtract")?.addEventListener("click", calcVatExtract);
$("#calcVatAdd")?.addEventListener("click", calcVatAdd);

$("#calcKeys")?.addEventListener("click", (e) => {
  const btn = e.target?.closest?.(".calc-key");
  if (!btn) return;
  if (btn.dataset.num !== undefined) calcInputNum(btn.dataset.num);
  else if (btn.dataset.op !== undefined) calcSetOp(btn.dataset.op);
  else calcRunAction(btn.dataset.act);
});

$$(".calc-vat-row").forEach((row) => {
  row.addEventListener("click", () => {
    const raw = calcNumToRaw(calcVat?.[row.dataset.vat] ?? null);
    if (raw === null) return;
    calcError = "";
    calcEntry = raw;
    calcFresh = true;
    calcRenderValue();
  });
});

document.addEventListener("keydown", (e) => {
  const k = e.key;
  let sel = null;
  if (k >= "0" && k <= "9") {
    calcInputNum(k);
    sel = `[data-num="${k}"]`;
  } else if (k === "." || k === ",") {
    calcInputDot();
    sel = '[data-act="dot"]';
  } else if (CALC_OP_SIGN[k]) {
    calcSetOp(k);
    sel = `[data-op="${k}"]`;
  } else if (k === "Enter" || k === "=") {
    calcEquals();
    sel = '[data-act="equals"]';
  } else if (k === "Backspace") {
    calcBackspace();
    sel = '[data-act="back"]';
  } else if (k === "%") {
    calcPercent();
    sel = '[data-act="percent"]';
  } else if (k === "Delete" || k === "c" || k === "C") {
    calcReset(true);
    sel = '[data-act="clear"]';
  } else if (k === "Escape") {
    calcClose();
    return;
  } else {
    return;
  }
  e.preventDefault();
  calcFlashKey(sel);
});

window.addEventListener?.("load", () => {
  calcFitWindow();
  requestAnimationFrame(calcFitWindow);
  setTimeout(calcFitWindow, 80);
  setTimeout(calcFitWindow, 250);
});

calcReset(true);
