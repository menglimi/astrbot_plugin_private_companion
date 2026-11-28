/* Shared pure currency/unit view resolver and cross-page sync helper for
 * Token Statistics (maintenance-page.js) and Quota Workbench (quota-workbench.js).
 * Zero network I/O, zero DOM assumptions, sandbox-safe localStorage access. */
"use strict";
window.CompanionQuotaCurrency = (() => {
  const EVENT_NAME = "companion:balance-unit-change";
  const CACHE_PREFIX = "companion-quota-unit.v1:";
  const FALLBACK_RATE = 7.2;
  const MIN_RATE = 1.0;
  const MAX_RATE = 30.0;

  function normalizeRate(raw) {
    const n = Number(raw);
    return Number.isFinite(n) && n >= MIN_RATE && n <= MAX_RATE ? n : FALLBACK_RATE;
  }

  function formatCompactRate(value) {
    const n = Number(value);
    if (!Number.isFinite(n) || n <= 0) return "";
    if (Math.abs(n - Math.round(n)) < 1e-6) return String(Math.round(n));
    return String(Number(n.toFixed(3)));
  }

  function safeStorage() {
    try {
      if (typeof localStorage !== "undefined" && localStorage && typeof localStorage.getItem === "function") {
        return localStorage;
      }
    } catch {}
    return null;
  }

  function getCachedUnit(scopeKey) {
    if (!scopeKey) return "";
    const store = safeStorage();
    if (!store) return "";
    try {
      const val = String(store.getItem(CACHE_PREFIX + scopeKey) || "").trim().toLowerCase();
      return ["native", "fiat", "cny", "usd"].includes(val) ? val : "";
    } catch {
      return "";
    }
  }

  function setCachedUnit(scopeKey, unit) {
    if (!scopeKey || !unit) return;
    const clean = String(unit).trim().toLowerCase();
    if (!["native", "fiat", "cny", "usd"].includes(clean)) return;
    const store = safeStorage();
    if (!store) return;
    try {
      store.setItem(CACHE_PREFIX + scopeKey, clean);
    } catch {}
  }

  function broadcastUnitChange({ scopeKey = "", stationId = "", unit = "", source = "" } = {}) {
    const clean = String(unit || "").trim().toLowerCase();
    if (!clean) return;
    if (scopeKey) setCachedUnit(scopeKey, clean);
    if (stationId && scopeKey !== `station:${stationId}`) {
      setCachedUnit(`station:${stationId}`, clean);
    }
    try {
      if (typeof window !== "undefined" && typeof window.dispatchEvent === "function") {
        const detail = { scopeKey, stationId, unit: clean, source };
        const ev = typeof CustomEvent === "function"
          ? new CustomEvent(EVENT_NAME, { detail })
          : { type: EVENT_NAME, detail };
        window.dispatchEvent(ev);
      }
    } catch {}
  }

  function normalizeUnit(unit, availableUnits, inferredFiat = "", displayType = "") {
    const avail = Array.isArray(availableUnits) && availableUnits.length
      ? availableUnits.map(u => String(u || "").trim().toLowerCase()).filter(Boolean)
      : ["native"];
    const raw = String(unit || "").trim().toLowerCase();
    const inferred = String(inferredFiat || "").trim().toUpperCase();
    const dtype = String(displayType || "").trim().toUpperCase();
    let fallback = dtype === "CNY" ? "cny" : dtype === "USD" ? "usd" : avail[0];
    if (!avail.includes(fallback)) fallback = avail[0];

    if (raw === "fiat") {
      const mapped = inferred === "USD" ? "usd" : inferred === "CNY" ? "cny" : fallback;
      return avail.includes(mapped) ? mapped : fallback;
    }
    if (raw === "native") {
      return avail.includes("native") ? "native" : fallback;
    }
    if (avail.includes(raw)) return raw;
    return fallback;
  }

  function convertFiat(amount, fromCur, toCur, rate) {
    const n = Number(amount);
    if (!Number.isFinite(n)) return null;
    const src = String(fromCur || "").toUpperCase();
    const dst = String(toCur || "").toUpperCase();
    if ((src !== "CNY" && src !== "USD") || (dst !== "CNY" && dst !== "USD")) return null;
    if (src === dst) return n;
    const r = normalizeRate(rate);
    return src === "USD" ? n * r : n / r;
  }

  function resolveViews(ctx, overrideUsdCnyRate = null) {
    if (!ctx || typeof ctx !== "object") return null;
    const dtype = String(ctx.display_type || "CREDIT").trim().toUpperCase();
    const rate = normalizeRate(overrideUsdCnyRate != null ? overrideUsdCnyRate : ctx.usd_cny_rate);
    const inferred = String(ctx.inferred_fiat_currency || (dtype === "CNY" || dtype === "USD" ? dtype : "")).trim().toUpperCase();
    const nativeAmount = ctx.native_amount != null && Number.isFinite(Number(ctx.native_amount)) ? Number(ctx.native_amount) : null;
    let cnyAmount = ctx.cny_amount != null && Number.isFinite(Number(ctx.cny_amount)) ? Number(ctx.cny_amount) : null;
    let usdAmount = ctx.usd_amount != null && Number.isFinite(Number(ctx.usd_amount)) ? Number(ctx.usd_amount) : null;

    if (overrideUsdCnyRate != null) {
      if (dtype === "CNY" && nativeAmount != null) {
        cnyAmount = nativeAmount;
        usdAmount = convertFiat(nativeAmount, "CNY", "USD", rate);
      } else if (dtype === "USD" && nativeAmount != null) {
        usdAmount = nativeAmount;
        cnyAmount = convertFiat(nativeAmount, "USD", "CNY", rate);
      } else if (dtype === "CREDIT") {
        if (inferred === "CNY" && cnyAmount != null) {
          usdAmount = convertFiat(cnyAmount, "CNY", "USD", rate);
        } else if (inferred === "USD" && usdAmount != null) {
          cnyAmount = convertFiat(usdAmount, "USD", "CNY", rate);
        }
      }
    }

    const availableUnits = Array.isArray(ctx.available_units) && ctx.available_units.length
      ? ctx.available_units.map(u => String(u || "").trim().toLowerCase()).filter(Boolean)
      : (cnyAmount != null && usdAmount != null
          ? (dtype === "CREDIT" ? ["native", "cny", "usd"] : ["cny", "usd"])
          : ["native"]);

    const preferredUnit = normalizeUnit(ctx.preferred_unit, availableUnits, inferred, dtype);
    return {
      scope_key: String(ctx.scope_key || (ctx.station_id ? `station:${ctx.station_id}` : "")),
      station_id: String(ctx.station_id || ""),
      display_type: dtype,
      native_symbol: String(ctx.native_symbol || (dtype === "USD" ? "$" : dtype === "CNY" ? "¥" : dtype === "TOKENS" ? "tokens" : "")),
      native_label: String(ctx.native_label || (dtype === "CREDIT" ? "站内原值" : dtype === "CNY" ? "人民币" : dtype === "USD" ? "美元" : "原始单位")),
      native_amount: nativeAmount,
      cny_amount: cnyAmount,
      usd_amount: usdAmount,
      usd_cny_rate: rate,
      inferred_fiat_currency: inferred,
      rate_caption: String(ctx.rate_caption || ""),
      preferred_unit: preferredUnit,
      available_units: availableUnits,
    };
  }

  function buildStationViews(station, snap, usdCnyRate = FALLBACK_RATE) {
    if (!snap || !snap.ok) return null;
    const dtype = String(snap.display_type || "CREDIT").trim().toUpperCase();
    const rate = normalizeRate(usdCnyRate);
    const inferred = dtype === "CNY" || dtype === "USD"
      ? dtype
      : String(station?.fiat_currency || snap.inferred_fiat_currency || snap.fiat_currency || "").trim().toUpperCase();
    const validFiat = inferred === "CNY" || inferred === "USD";
    const nativeSymbol = String(snap.symbol || (dtype === "USD" ? "$" : dtype === "CNY" ? "¥" : dtype === "TOKENS" ? "tokens" : "credit"));
    const rem = snap.remaining != null && Number.isFinite(Number(snap.remaining)) ? Number(snap.remaining) : null;
    const fiatRem = snap.fiat_remaining != null && Number.isFinite(Number(snap.fiat_remaining)) ? Number(snap.fiat_remaining) : null;

    let cnyAmount = null;
    let usdAmount = null;
    let availableUnits = ["native"];
    let rateCaption = "";

    if (dtype === "CNY") {
      cnyAmount = rem;
      usdAmount = rem != null ? convertFiat(rem, "CNY", "USD", rate) : null;
      availableUnits = rem != null ? ["cny", "usd"] : ["native"];
    } else if (dtype === "USD") {
      usdAmount = rem;
      cnyAmount = rem != null ? convertFiat(rem, "USD", "CNY", rate) : null;
      availableUnits = rem != null ? ["cny", "usd"] : ["native"];
    } else if (dtype === "CREDIT" && validFiat && fiatRem != null) {
      if (inferred === "CNY") {
        cnyAmount = fiatRem;
        usdAmount = convertFiat(fiatRem, "CNY", "USD", rate);
      } else {
        usdAmount = fiatRem;
        cnyAmount = convertFiat(fiatRem, "USD", "CNY", rate);
      }
      availableUnits = ["native", "cny", "usd"];
      const customRate = Number(snap.custom_exchange_rate);
      const pbu = Number(station?.fiat_per_base_unit ?? snap.site_recharge_price ?? snap.fiat_per_base_unit);
      if (Number.isFinite(customRate) && customRate > 0 && Number.isFinite(pbu) && pbu > 0) {
        const perFiat = formatCompactRate(customRate / pbu);
        if (perFiat) {
          rateCaption = `${perFiat} ${nativeSymbol} = ${inferred === "USD" ? "$" : "¥"}1`;
        }
      }
    }

    const scopeKey = station?.id ? `station:${station.id}` : "";
    const preferredUnit = normalizeUnit(station?.display_unit_mode || "native", availableUnits, inferred, dtype);
    return {
      scope_key: scopeKey,
      station_id: String(station?.id || ""),
      display_type: dtype,
      native_symbol: nativeSymbol,
      native_label: dtype === "CREDIT" ? "站内原值" : dtype === "CNY" ? "人民币" : dtype === "USD" ? "美元" : "原始 Token",
      native_amount: rem,
      cny_amount: cnyAmount,
      usd_amount: usdAmount,
      usd_cny_rate: rate,
      inferred_fiat_currency: validFiat ? inferred : "",
      rate_caption: rateCaption,
      preferred_unit: preferredUnit,
      available_units: availableUnits,
    };
  }

  function convertSecondaryAmount(nativeVal, fiatVal, views, activeUnit) {
    const unit = String(activeUnit || "native").toLowerCase();
    const nVal = nativeVal != null && Number.isFinite(Number(nativeVal)) ? Number(nativeVal) : null;
    const fVal = fiatVal != null && Number.isFinite(Number(fiatVal)) ? Number(fiatVal) : null;
    if (!views || unit === "native") return nVal;
    const targetCur = unit === "usd" ? "USD" : "CNY";
    const dtype = String(views.display_type || "CREDIT").toUpperCase();
    const rate = normalizeRate(views.usd_cny_rate);
    if (dtype === "CNY" || dtype === "USD") {
      return nVal != null ? convertFiat(nVal, dtype, targetCur, rate) : null;
    }
    if (dtype === "CREDIT" && (views.inferred_fiat_currency === "CNY" || views.inferred_fiat_currency === "USD")) {
      if (fVal != null) {
        return convertFiat(fVal, views.inferred_fiat_currency, targetCur, rate);
      }
      if (nVal != null && views.native_amount > 0) {
        const baseFiat = views.inferred_fiat_currency === "USD" ? views.usd_amount : views.cny_amount;
        if (baseFiat != null && Number.isFinite(baseFiat)) {
          return convertFiat((nVal / views.native_amount) * baseFiat, views.inferred_fiat_currency, targetCur, rate);
        }
      }
    }
    return nVal;
  }

  function formatAmount(amount, unit, views, { small = false } = {}) {
    const n = Number(amount);
    const u = String(unit || "native").toLowerCase();
    if (!Number.isFinite(n)) return { text: "—", num: "—", symbol: "", prefix: false, unit: u };

    if (u === "cny" || u === "usd") {
      const sym = u === "usd" ? "$" : "¥";
      if (small) {
        if (n === 0) return { text: `${sym}0`, num: "0", symbol: sym, prefix: true, unit: u };
        if (n > 0 && n < 0.01) {
          const tiny = n.toFixed(6).replace(/0+$/, "").replace(/\.$/, "");
          return { text: `${sym}${tiny}`, num: tiny, symbol: sym, prefix: true, unit: u };
        }
        const num = n.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 4 });
        return { text: `${sym}${num}`, num, symbol: sym, prefix: true, unit: u };
      }
      const num = n.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      return { text: `${sym}${num}`, num, symbol: sym, prefix: true, unit: u };
    }

    const dtype = String(views?.display_type || "CREDIT").toUpperCase();
    const sym = String(views?.native_symbol || (dtype === "USD" ? "$" : dtype === "CNY" ? "¥" : dtype === "TOKENS" ? "tokens" : "credit"));
    if (dtype === "TOKENS") {
      const num = Math.round(n).toLocaleString("zh-CN");
      return { text: `${num} tokens`, num, symbol: "tokens", prefix: false, unit: u };
    }
    if (dtype === "CNY" || dtype === "USD" || sym === "¥" || sym === "$") {
      const num = small && n > 0 && n < 0.01
        ? n.toFixed(6).replace(/0+$/, "").replace(/\.$/, "")
        : n.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: small ? 4 : 2 });
      return { text: `${sym}${num}`, num, symbol: sym, prefix: true, unit: u };
    }
    const num = n.toLocaleString("zh-CN", { maximumFractionDigits: small ? 4 : 2 });
    return { text: sym ? `${num} ${sym}` : num, num, symbol: sym, prefix: false, unit: u };
  }

  function formatUnitValue(views, activeUnit) {
    if (!views) return { text: "—", num: "—", symbol: "", prefix: false, unit: "native" };
    const u = normalizeUnit(activeUnit, views.available_units, views.inferred_fiat_currency, views.display_type);
    const val = u === "cny" ? views.cny_amount : u === "usd" ? views.usd_amount : views.native_amount;
    return formatAmount(val, u, views, { small: false });
  }

  function buildSubline(views, activeUnit) {
    if (!views) return { text: "", title: "" };
    const avail = Array.isArray(views.available_units) ? views.available_units : ["native"];
    const u = normalizeUnit(activeUnit, avail, views.inferred_fiat_currency, views.display_type);
    const rate = normalizeRate(views.usd_cny_rate);
    const rateStr = rate.toFixed(2);
    const titleParts = [];
    if (views.rate_caption) titleParts.push(`站内换算 ${views.rate_caption}`);
    if (avail.includes("cny") && avail.includes("usd")) titleParts.push(`1 USD ≈ ¥${rateStr}`);
    const title = titleParts.join(" · ");

    const hasNative = avail.includes("native") && views.native_amount != null;
    const hasCny = avail.includes("cny") && views.cny_amount != null;
    const hasUsd = avail.includes("usd") && views.usd_amount != null;

    if (hasNative && hasCny && hasUsd) {
      const nativeTxt = formatAmount(views.native_amount, "native", views).text;
      const cnyTxt = formatAmount(views.cny_amount, "cny", views).text;
      const usdTxt = formatAmount(views.usd_amount, "usd", views).text;
      if (u === "native") return { text: `≈ ${cnyTxt} · ${usdTxt}`, title };
      if (u === "cny") return { text: `原值 ${nativeTxt} · ≈ ${usdTxt}`, title };
      if (u === "usd") return { text: `原值 ${nativeTxt} · ≈ ${cnyTxt}（汇率 ${rateStr}）`, title };
    }
    if (hasCny && hasUsd) {
      const cnyTxt = formatAmount(views.cny_amount, "cny", views).text;
      const usdTxt = formatAmount(views.usd_amount, "usd", views).text;
      if (u === "cny") return { text: `≈ ${usdTxt}（1 USD ≈ ¥${rateStr}）`, title };
      if (u === "usd") return { text: `≈ ${cnyTxt}（1 USD ≈ ¥${rateStr}）`, title };
    }
    return { text: "", title };
  }

  function unitButtonMeta(unit, views) {
    const u = String(unit || "native").toLowerCase();
    if (u === "cny") {
      return { unit: "cny", label: "¥", title: "人民币 (CNY)", ariaLabel: "人民币 CNY" };
    }
    if (u === "usd") {
      return { unit: "usd", label: "$", title: "美元 (USD)", ariaLabel: "美元 USD" };
    }
    const sym = String(views?.native_symbol || "").trim();
    const label = sym && sym.length <= 4 ? sym : "原值";
    const desc = sym ? `站内原值 (${sym})` : "站内原值";
    return { unit: "native", label, title: desc, ariaLabel: desc };
  }

  return {
    EVENT_NAME,
    normalizeRate,
    formatCompactRate,
    normalizeUnit,
    convertFiat,
    resolveViews,
    buildStationViews,
    convertSecondaryAmount,
    formatAmount,
    formatUnitValue,
    buildSubline,
    unitButtonMeta,
    getCachedUnit,
    setCachedUnit,
    broadcastUnitChange,
  };
})();
