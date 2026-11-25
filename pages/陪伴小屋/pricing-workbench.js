/* Pricing v2 master-detail workbench: multi-source catalog, 5 modes, schedules, FX, and residue management. */
"use strict";
window.CompanionPricingWorkbench = (() => {
  const PRICING_DISPLAY_CACHE_KEY = "companion-pricing-display.v1";
  const PRICING_UNIT_VIEW_KEY = "companion-pricing-unit-view.v1";
  const MODELS_PENDING_TAB_KEY = "companion-models-pending-tab.v1";

  const MODE_OPTIONS = [
    ["per_token", "按 Token"],
    ["per_tier", "阶梯定价"],
    ["tiered_expr", "表达式"],
    ["per_turn", "按调用轮次"],
    ["per_request", "按请求次数"],
  ];

  const WEEKDAYS = [
    [1, "一"],
    [2, "二"],
    [3, "三"],
    [4, "四"],
    [5, "五"],
    [6, "六"],
    [7, "日"],
  ];

  const DEFAULT_TIMEZONES = [
    "Asia/Shanghai",
    "UTC",
    "Asia/Hong_Kong",
    "Asia/Tokyo",
    "Asia/Singapore",
    "America/New_York",
    "America/Los_Angeles",
    "Europe/London",
    "Europe/Berlin",
  ];

  const EXPR_VARIABLES = [
    ["p", "未命中输入"],
    ["c", "输出"],
    ["cr", "缓存读"],
    ["cc", "缓存写"],
    ["cc1h", "1h缓存写"],
    ["len", "总输入长度"],
    ["tier", "服务档位"],
  ];

  const ERRORS = {
    pricing_config_conflict: "计价配置已在别处变化，请刷新后再保存。",
    pricing_config_invalid: "计价配置未通过校验，请核对单价、倍率、分时时段与汇率范围。",
    pricing_config_unreadable: "模型计价配置文件无法读取。",
    pricing_config_unavailable: "无法读取模型计价配置。",
    pricing_sync_invalid: "同步请求参数无效，请刷新后重试。",
    pricing_source_invalid: "价格源切换请求无效。",
    pricing_source_not_found: "未找到指定的价格源。",
    pricing_detect_unavailable: "该供应商未配置可探测的接口地址。",
    pricing_detect_invalid: "New API 探测请求无效。",
    pricing_candidate_not_found: "未在当前价格目录中找到该候选价格。",
    pricing_select_invalid: "价格源选择请求无效。",
    pricing_expr_invalid: "计价表达式未通过语法校验。",
    pricing_expr_eval_failed: "计价表达式试算失败。",
    pricing_schedule_invalid: "分时定价配置无效，请核对时区、星期与时段。",
    pricing_schedule_overlap: "分时定价存在时间重叠，请调整时段范围。",
    pricing_reset_invalid: "重置计价配置请求无效。",
    pricing_residue_invalid: "清理已删除供应商残留请求无效。",
  };

  const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));

  const clone = value => value == null ? value : JSON.parse(JSON.stringify(value));

  function numOrNull(value) {
    if (value === "" || value === null || value === undefined) return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : NaN;
  }

  function intOrNull(value) {
    if (value === "" || value === null || value === undefined) return null;
    const n = Number(value);
    return Number.isFinite(n) ? Math.round(n) : NaN;
  }

  function formatTokensFull(value) {
    const n = Math.max(0, Math.round(Number(value) || 0));
    return new Intl.NumberFormat("zh-CN", {maximumFractionDigits: 0}).format(n);
  }

  function formatTokensCompact(value) {
    const n = Math.max(0, Number(value) || 0);
    if (n === 0) return "0";
    if (n >= 1e9) return (n / 1e9).toFixed(n >= 1e10 ? 1 : 2).replace(/\.0+$/, "") + "B";
    if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 1 : 2).replace(/\.0+$/, "") + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e4 ? 1 : 1).replace(/\.0+$/, "") + "K";
    return String(Math.round(n));
  }

  function formatRateNumber(value) {
    if (value === null || value === undefined || value === "") return "—";
    const n = Number(value);
    if (!Number.isFinite(n)) return "—";
    if (n === 0) return "0";
    if (Math.abs(n) >= 100) return String(Number(n.toFixed(2)));
    if (Math.abs(n) >= 1) return String(Number(n.toFixed(4)));
    return String(Number(n.toFixed(6)));
  }

  function formatMoney(microusd, currency = "CNY", usdCnyRate = 7.2) {
    if (microusd === null || microusd === undefined || !Number.isFinite(Number(microusd))) {
      return "未设单价";
    }
    const cur = currency === "USD" ? "USD" : "CNY";
    const rate = Number.isFinite(Number(usdCnyRate)) && Number(usdCnyRate) >= 1 ? Number(usdCnyRate) : 7.2;
    const usd = Number(microusd) / 1e6;
    const amount = cur === "CNY" ? usd * rate : usd;
    const sym = cur === "CNY" ? "¥" : "US$";
    const abs = Math.abs(amount);
    if (abs === 0) return sym + "0.00";
    if (abs >= 1) return sym + amount.toFixed(2);
    if (abs >= 0.0001) return sym + amount.toFixed(4);
    return sym + amount.toFixed(6);
  }

  function formatShortTime(iso) {
    if (!iso || typeof iso !== "string") return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso.slice(0, 16).replace("T", " ");
    try {
      return new Intl.DateTimeFormat("zh-CN", {
        month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
      }).format(d);
    } catch {
      return iso.slice(5, 16).replace("T", " ");
    }
  }

  function currencySymbol(currency) {
    return currency === "CNY" ? "¥" : "$";
  }

  function rememberDisplay(data) {
    try {
      if (typeof localStorage === "undefined") return;
      const currency = data?.display_currency === "USD" ? "USD" : "CNY";
      const rate = Number(data?.exchange_rate?.effective_usd_cny) || 7.2;
      localStorage.setItem(PRICING_DISPLAY_CACHE_KEY, JSON.stringify({currency, usd_cny_rate: rate}));
    } catch {}
  }

  function consumePendingTab() {
    try {
      if (typeof sessionStorage === "undefined") return false;
      if (sessionStorage.getItem(MODELS_PENDING_TAB_KEY) === "pricing") {
        sessionStorage.removeItem(MODELS_PENDING_TAB_KEY);
        return true;
      }
    } catch {}
    return false;
  }

  function readUnitViewPrefs() {
    try {
      if (typeof localStorage === "undefined") return {};
      const raw = localStorage.getItem(PRICING_UNIT_VIEW_KEY);
      if (!raw) return {};
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
      const out = {};
      for (const [k, v] of Object.entries(parsed)) {
        if (typeof k === "string" && (v === "fiat" || v === "native")) out[k] = v;
      }
      return out;
    } catch {
      return {};
    }
  }

  function saveUnitViewPrefs(map) {
    try {
      if (typeof localStorage === "undefined") return;
      localStorage.setItem(PRICING_UNIT_VIEW_KEY, JSON.stringify(map || {}));
    } catch {}
  }

  function createState() {
    return {
      data: null,
      draft: null,
      loaded: false,
      loading: false,
      busy: false,
      syncingRates: false,
      syncingCatalog: false,
      togglingSource: "",
      updatingSourceCurrency: "",
      detectingProvider: "",
      selectingKey: "",
      validatingKey: "",
      resetting: false,
      clearingResidue: false,
      resetArmed: false,
      stale: false,
      bad: false,
      notice: "",
      syncNotice: "",
      selectedCluster: "",
      expandedProvider: "",
      activeModelByProvider: {},
      openCandidates: {},
      clusterMultDraft: {},
      sourceUnitView: readUnitViewPrefs(),
      sourceRateDraft: {},
      exprResults: {},
      search: "",
      residueOpen: false,
      presetsOpen: false,
      pulseProvider: "",
      pulseTimer: null,
    };
  }

  /* ---------- Strict active-first + token-DESC sorting & payload normalization ---------- */

  function sortConfiguredProviders(items) {
    return items.slice().sort((a, b) => {
      const aActive = Boolean(a.bound || (Number(a.total_tokens) || 0) > 0);
      const bActive = Boolean(b.bound || (Number(b.total_tokens) || 0) > 0);
      if (aActive !== bActive) return aActive ? -1 : 1;
      if (aActive) {
        const diff = (Number(b.total_tokens) || 0) - (Number(a.total_tokens) || 0);
        if (diff !== 0) return diff;
        if (Boolean(a.bound) !== Boolean(b.bound)) return a.bound ? -1 : 1;
      }
      return String(a.id || "").toLowerCase().localeCompare(String(b.id || "").toLowerCase());
    });
  }

  function sortDeletedProviders(items) {
    return items.slice().sort((a, b) => {
      const diff = (Number(b.total_tokens) || 0) - (Number(a.total_tokens) || 0);
      if (diff !== 0) return diff;
      return String(a.id || "").toLowerCase().localeCompare(String(b.id || "").toLowerCase());
    });
  }

  function sortUnpriced(items) {
    return items.slice().sort((a, b) => {
      const diff = (Number(b.total_tokens) || 0) - (Number(a.total_tokens) || 0);
      if (diff !== 0) return diff;
      return String(a.provider_id || "").toLowerCase().localeCompare(String(b.provider_id || "").toLowerCase());
    });
  }

  function sortClusters(clusters) {
    return clusters.slice().sort((a, b) => {
      const aActive = (Number(a.cluster_total_tokens) || 0) > 0 || Boolean(a.has_bound);
      const bActive = (Number(b.cluster_total_tokens) || 0) > 0 || Boolean(b.has_bound);
      if (aActive !== bActive) return aActive ? -1 : 1;
      const tokDiff = (Number(b.cluster_total_tokens) || 0) - (Number(a.cluster_total_tokens) || 0);
      if (tokDiff !== 0) return tokDiff;
      const actDiff = (Number(b.active_count) || 0) - (Number(a.active_count) || 0);
      if (actDiff !== 0) return actDiff;
      return String(a.name || a.cluster_id || "").toLowerCase()
        .localeCompare(String(b.name || b.cluster_id || "").toLowerCase());
    });
  }

  function v1TariffToV2Rule(v1) {
    if (!v1 || typeof v1 !== "object") return null;
    const free = Boolean(v1.free);
    const mult = Number.isFinite(Number(v1.multiplier)) ? Number(v1.multiplier) : 1;
    const currency = v1.currency === "CNY" ? "CNY" : "USD";
    if (free) {
      return {
        mode: "per_token", currency,
        input: 0, input_cached: 0, cache_creation: 0, output: 0,
        multiplier: mult, free: true,
        configured: {input: true, input_cached: true, cache_creation: true, output: true},
      };
    }
    const inp = numOrNull(v1.input_per_million);
    const cached = numOrNull(v1.cached_input_per_million);
    const creation = numOrNull(v1.cache_creation_per_million);
    const out = numOrNull(v1.output_per_million);
    return {
      mode: "per_token", currency,
      input: Number.isFinite(inp) ? inp : null,
      input_cached: Number.isFinite(cached) ? cached : null,
      cache_creation: Number.isFinite(creation) ? creation : null,
      output: Number.isFinite(out) ? out : null,
      multiplier: mult, free: false,
      configured: {
        input: Number.isFinite(inp),
        input_cached: Number.isFinite(cached),
        cache_creation: Number.isFinite(creation),
        output: Number.isFinite(out),
      },
    };
  }

  function normalizePayload(raw) {
    const data = clone(raw && typeof raw === "object" ? raw : {});
    data.display_currency = data.display_currency === "USD" ? "USD" : "CNY";
    data.use_builtin_presets = data.use_builtin_presets !== false;
    data.exchange_rate = {
      mode: data.exchange_rate?.mode === "manual" ? "manual" : "auto",
      manual_usd_cny: data.exchange_rate?.manual_usd_cny ?? null,
      cached_usd_cny: Number(data.exchange_rate?.cached_usd_cny) || 7.2,
      fallback_usd_cny: Number(data.exchange_rate?.fallback_usd_cny) || 7.2,
      effective_usd_cny: Number(data.exchange_rate?.effective_usd_cny) || 7.2,
      fetched_at: data.exchange_rate?.fetched_at || null,
      source: data.exchange_rate?.source || "static_fallback",
      effective_source: data.exchange_rate?.effective_source || data.exchange_rate?.source || "static_fallback",
      stale: Boolean(data.exchange_rate?.stale),
      ttl_hours: Number(data.exchange_rate?.ttl_hours) || 12,
    };
    data.pricing_timezone = data.pricing_timezone || "Asia/Shanghai";
    data.available_timezones = Array.isArray(data.available_timezones) && data.available_timezones.length
      ? data.available_timezones : DEFAULT_TIMEZONES.slice();
    data.price_sync = {
      auto_daily: data.price_sync?.auto_daily !== false,
      last_auto_sync_at: data.price_sync?.last_auto_sync_at || null,
    };
    data.pricing_multipliers = (data.pricing_multipliers && typeof data.pricing_multipliers === "object")
      ? data.pricing_multipliers : {};
    data.pricing_schedules = (data.pricing_schedules && typeof data.pricing_schedules === "object")
      ? data.pricing_schedules : {};
    data.price_selections = (data.price_selections && typeof data.price_selections === "object")
      ? data.price_selections : {};
    data.candidates = (data.candidates && typeof data.candidates === "object") ? data.candidates : {};
    data.auto_selected = (data.auto_selected && typeof data.auto_selected === "object") ? data.auto_selected : {};
    data.selections = (data.selections && typeof data.selections === "object") ? data.selections : {};
    data.presets = Array.isArray(data.presets) ? data.presets : [];
    data.sources = Array.isArray(data.sources) ? data.sources : [];

    // Migrate v1-only mock payloads smoothly if pricing_rules is absent.
    if (!data.pricing_rules || typeof data.pricing_rules !== "object") {
      data.pricing_rules = {};
      for (const [pid, tariff] of Object.entries(data.provider_tariffs || {})) {
        const converted = v1TariffToV2Rule(tariff);
        if (converted) data.pricing_rules[pid] = converted;
      }
    }

    // Build clusters if missing (v1 fallback) and enforce strict active-first + token-DESC sort.
    if (Array.isArray(data.clusters)) {
      data.clusters = sortClusters(data.clusters.map(cluster => ({
        ...cluster,
        providers: sortConfiguredProviders(Array.isArray(cluster.providers) ? cluster.providers : []),
      })));
    } else {
      const map = new Map();
      const rawProviders = Array.isArray(data.providers) ? data.providers : [];
      for (const prov of rawProviders) {
        if (prov.configured === false) continue;
        const cid = prov.cluster_id || prov.provider_source_id || prov.id;
        const entry = map.get(cid) || {
          cluster_id: cid,
          name: prov.supplier_name || cid,
          provider_source_id: prov.provider_source_id || null,
          multiplier: Number(data.pricing_multipliers[cid] ?? 1),
          has_custom_multiplier: Object.hasOwn(data.pricing_multipliers, cid) && Number(data.pricing_multipliers[cid]) !== 1,
          has_bound: false,
          active_count: 0,
          provider_count: 0,
          cluster_total_tokens: 0,
          providers: [],
        };
        const item = {
          configured: true, enabled: true, loaded: Boolean(prov.loaded), bound: Boolean(prov.bound),
          total_tokens: Number(prov.total_tokens) || 0, calls: Number(prov.calls) || 0, requests: Number(prov.requests) || 0,
          cluster_id: cid, supplier_name: entry.name, candidates: prov.candidates || (prov.model ? [prov.model] : []),
          ...prov,
        };
        entry.providers.push(item);
        entry.provider_count += 1;
        entry.cluster_total_tokens += item.total_tokens;
        if (item.bound) entry.has_bound = true;
        if (item.bound || item.total_tokens > 0) entry.active_count += 1;
        map.set(cid, entry);
      }
      data.clusters = sortClusters([...map.values()].map(c => ({
        ...c, providers: sortConfiguredProviders(c.providers),
      })));
    }

    data.deleted_providers = sortDeletedProviders(
      Array.isArray(data.deleted_providers)
        ? data.deleted_providers
        : (Array.isArray(data.providers) ? data.providers.filter(item => item.configured === false) : []),
    );
    data.unpriced = sortUnpriced(Array.isArray(data.unpriced) ? data.unpriced : []);
    return data;
  }

  /* ---------- Draft creation & canonical serialization ---------- */

  function cloneRuleForDraft(rule) {
    if (!rule || typeof rule !== "object") return null;
    const mode = rule.mode || "per_token";
    if (mode === "per_token") {
      const cfg = rule.configured && typeof rule.configured === "object" ? rule.configured : {};
      const free = Boolean(rule.free);
      return {
        mode: "per_token",
        currency: rule.currency === "CNY" ? "CNY" : "USD",
        input: free ? "0" : (cfg.input === false || rule.input == null ? "" : String(rule.input)),
        input_cached: free ? "0" : (cfg.input_cached === false || rule.input_cached == null ? "" : String(rule.input_cached)),
        cache_creation: free ? "0" : (cfg.cache_creation === false || rule.cache_creation == null ? "" : String(rule.cache_creation)),
        output: free ? "0" : (cfg.output === false || rule.output == null ? "" : String(rule.output)),
        multiplier: rule.multiplier == null ? "1" : String(rule.multiplier),
        free,
      };
    }
    if (mode === "per_tier") {
      const base = rule.base || {};
      return {
        mode: "per_tier",
        currency: rule.currency === "CNY" ? "CNY" : "USD",
        multiplier: rule.multiplier == null ? "1" : String(rule.multiplier),
        base: {
          input: base.input == null ? "" : String(base.input),
          input_cached: base.input_cached == null ? "" : String(base.input_cached),
          cache_creation: base.cache_creation == null ? "" : String(base.cache_creation),
          output: base.output == null ? "" : String(base.output),
        },
        context_tiers: (Array.isArray(rule.context_tiers) ? rule.context_tiers : []).map(t => ({
          threshold_tokens: t.threshold_tokens == null ? "" : String(t.threshold_tokens),
          input: t.input == null ? "" : String(t.input),
          input_cached: t.input_cached == null ? "" : String(t.input_cached),
          cache_creation: t.cache_creation == null ? "" : String(t.cache_creation),
          output: t.output == null ? "" : String(t.output),
        })),
        service_tiers: (Array.isArray(rule.service_tiers) ? rule.service_tiers : []).map(t => ({
          match: String(t.match || ""),
          input_multiplier: t.input_multiplier == null ? "" : String(t.input_multiplier),
          input_cached_multiplier: t.input_cached_multiplier == null ? "" : String(t.input_cached_multiplier),
          cache_creation_multiplier: t.cache_creation_multiplier == null ? "" : String(t.cache_creation_multiplier),
          output_multiplier: t.output_multiplier == null ? "" : String(t.output_multiplier),
          input: t.input == null ? "" : String(t.input),
          input_cached: t.input_cached == null ? "" : String(t.input_cached),
          cache_creation: t.cache_creation == null ? "" : String(t.cache_creation),
          output: t.output == null ? "" : String(t.output),
        })),
      };
    }
    if (mode === "tiered_expr") {
      return {
        mode: "tiered_expr",
        currency: rule.currency === "CNY" ? "CNY" : "USD",
        expr: rule.expr == null ? "" : String(rule.expr),
        tiers: (Array.isArray(rule.tiers) ? rule.tiers : []).map((t, idx) => ({
          name: String(t.name || `tier_${idx + 1}`),
          condition: String(t.condition || ""),
          expr: String(t.expr || ""),
        })),
        default_expr: rule.default_expr == null ? "" : String(rule.default_expr),
        ...(rule.cluster_multiplier != null && Number(rule.cluster_multiplier) !== 1
          ? {cluster_multiplier: Number(rule.cluster_multiplier)} : {}),
      };
    }
    return {
      mode: mode === "per_request" ? "per_request" : "per_turn",
      currency: rule.currency === "CNY" ? "CNY" : "USD",
      price: rule.price == null ? "" : String(rule.price),
      multiplier: rule.multiplier == null ? "1" : String(rule.multiplier),
    };
  }

  function cloneScheduleForDraft(sched, fallbackTz = "Asia/Shanghai") {
    if (!sched || typeof sched !== "object") return null;
    return {
      enabled: sched.enabled !== false,
      timezone: String(sched.timezone || fallbackTz || "Asia/Shanghai"),
      periods: (Array.isArray(sched.periods) ? sched.periods : []).map((period, idx) => ({
        id: String(period.id || `period_${idx + 1}`),
        name: String(period.name || ""),
        days: Array.isArray(period.days) ? period.days.map(Number).filter(d => d >= 1 && d <= 7) : [1, 2, 3, 4, 5, 6, 7],
        all_day: Boolean(period.all_day),
        start: String(period.start || "00:00"),
        end: String(period.end || "08:00"),
        mode: period.mode === "override" ? "override" : "multiplier",
        multiplier: period.multiplier == null ? "0.5" : String(period.multiplier),
        override: period.override ? cloneRuleForDraft(period.override) : cloneRuleForDraft({mode: "per_token", currency: "USD"}),
      })),
    };
  }

  function draftFromData(data) {
    const rules = {};
    for (const [k, r] of Object.entries(data.pricing_rules || {})) {
      rules[k] = cloneRuleForDraft(r);
    }
    const schedules = {};
    for (const [k, sc] of Object.entries(data.pricing_schedules || {})) {
      schedules[k] = cloneScheduleForDraft(sc, data.pricing_timezone);
    }
    const multipliers = {};
    for (const [k, v] of Object.entries(data.pricing_multipliers || {})) {
      if (Number.isFinite(Number(v)) && Number(v) !== 1) multipliers[k] = Number(v);
    }
    return {
      display_currency: data.display_currency === "USD" ? "USD" : "CNY",
      use_builtin_presets: data.use_builtin_presets !== false,
      pricing_timezone: data.pricing_timezone || "Asia/Shanghai",
      exchange_rate: {
        mode: data.exchange_rate?.mode === "manual" ? "manual" : "auto",
        manual_usd_cny: data.exchange_rate?.manual_usd_cny == null ? "" : String(data.exchange_rate.manual_usd_cny),
      },
      price_sync: {
        auto_daily: data.price_sync?.auto_daily !== false,
      },
      pricing_rules: rules,
      pricing_schedules: schedules,
      pricing_multipliers: multipliers,
    };
  }

  function canonicalizeRule(draftRule) {
    if (!draftRule || typeof draftRule !== "object") return null;
    const mode = draftRule.mode || "per_token";
    if (mode === "per_token") {
      const currency = draftRule.currency === "CNY" ? "CNY" : "USD";
      const free = Boolean(draftRule.free);
      const multRaw = numOrNull(draftRule.multiplier);
      const multiplier = multRaw === null ? 1 : multRaw;
      if (free) {
        return {
          mode: "per_token", currency,
          input: 0, input_cached: 0, cache_creation: 0, output: 0,
          multiplier, free: true,
          configured: {input: true, input_cached: true, cache_creation: true, output: true},
        };
      }
      const inp = numOrNull(draftRule.input);
      const cached = numOrNull(draftRule.input_cached);
      const creation = numOrNull(draftRule.cache_creation);
      const out = numOrNull(draftRule.output);
      return {
        mode: "per_token", currency,
        input: inp, input_cached: cached, cache_creation: creation, output: out,
        multiplier, free: false,
        configured: {
          input: inp !== null,
          input_cached: cached !== null,
          cache_creation: creation !== null,
          output: out !== null,
        },
      };
    }
    if (mode === "per_tier") {
      const currency = draftRule.currency === "CNY" ? "CNY" : "USD";
      const multRaw = numOrNull(draftRule.multiplier);
      const base = draftRule.base || {};
      return {
        mode: "per_tier",
        currency,
        multiplier: multRaw === null ? 1 : multRaw,
        base: {
          input: numOrNull(base.input),
          input_cached: numOrNull(base.input_cached),
          cache_creation: numOrNull(base.cache_creation),
          output: numOrNull(base.output),
        },
        context_tiers: (Array.isArray(draftRule.context_tiers) ? draftRule.context_tiers : []).map(t => ({
          threshold_tokens: intOrNull(t.threshold_tokens),
          input: numOrNull(t.input),
          input_cached: numOrNull(t.input_cached),
          cache_creation: numOrNull(t.cache_creation),
          output: numOrNull(t.output),
        })),
        service_tiers: (Array.isArray(draftRule.service_tiers) ? draftRule.service_tiers : []).map(t => ({
          match: String(t.match || "").trim(),
          input_multiplier: numOrNull(t.input_multiplier),
          input_cached_multiplier: numOrNull(t.input_cached_multiplier),
          cache_creation_multiplier: numOrNull(t.cache_creation_multiplier),
          output_multiplier: numOrNull(t.output_multiplier),
          input: numOrNull(t.input),
          input_cached: numOrNull(t.input_cached),
          cache_creation: numOrNull(t.cache_creation),
          output: numOrNull(t.output),
        })),
      };
    }
    if (mode === "tiered_expr") {
      const exprText = String(draftRule.expr || "").trim();
      const defaultText = String(draftRule.default_expr || "").trim();
      const tiers = (Array.isArray(draftRule.tiers) ? draftRule.tiers : [])
        .filter(t => String(t.expr || "").trim() || String(t.condition || "").trim())
        .map((t, idx) => ({
          name: String(t.name || `tier_${idx + 1}`).trim() || `tier_${idx + 1}`,
          condition: String(t.condition || "").trim(),
          expr: String(t.expr || "").trim(),
        }));
      const out = {
        mode: "tiered_expr",
        currency: draftRule.currency === "CNY" ? "CNY" : "USD",
        expr: exprText || null,
        tiers,
        default_expr: defaultText || null,
      };
      if (draftRule.cluster_multiplier != null && Number(draftRule.cluster_multiplier) !== 1) {
        out.cluster_multiplier = Number(draftRule.cluster_multiplier);
      }
      return out;
    }
    const multRaw = numOrNull(draftRule.multiplier);
    return {
      mode: mode === "per_request" ? "per_request" : "per_turn",
      currency: draftRule.currency === "CNY" ? "CNY" : "USD",
      price: numOrNull(draftRule.price),
      multiplier: multRaw === null ? 1 : multRaw,
    };
  }

  function canonicalizeSchedule(draftSched, fallbackTz = "Asia/Shanghai") {
    if (!draftSched || typeof draftSched !== "object") return null;
    return {
      enabled: Boolean(draftSched.enabled),
      timezone: String(draftSched.timezone || fallbackTz || "Asia/Shanghai").trim() || "Asia/Shanghai",
      periods: (Array.isArray(draftSched.periods) ? draftSched.periods : []).map((period, idx) => {
        const allDay = Boolean(period.all_day);
        const days = [...new Set((period.days || []).map(Number).filter(d => Number.isInteger(d) && d >= 1 && d <= 7))].sort((a, b) => a - b);
        const entry = {
          id: String(period.id || `period_${idx + 1}`).trim() || `period_${idx + 1}`,
          name: String(period.name || "").trim(),
          days,
          all_day: allDay,
          start: allDay ? "00:00" : String(period.start || "00:00").trim(),
          end: allDay ? "24:00" : String(period.end || "24:00").trim(),
          mode: period.mode === "override" ? "override" : "multiplier",
        };
        if (entry.mode === "multiplier") {
          entry.multiplier = numOrNull(period.multiplier);
        } else {
          entry.override = canonicalizeRule(period.override);
        }
        return entry;
      }),
    };
  }

  function canonicalizeRulesMap(rawMap) {
    const out = {};
    for (const k of Object.keys(rawMap || {}).sort()) {
      const val = canonicalizeRule(rawMap[k]);
      if (val) out[k] = val;
    }
    return out;
  }

  function canonicalizeSchedulesMap(rawMap, fallbackTz) {
    const out = {};
    for (const k of Object.keys(rawMap || {}).sort()) {
      const val = canonicalizeSchedule(rawMap[k], fallbackTz);
      if (val) out[k] = val;
    }
    return out;
  }

  function canonicalizeMultipliersMap(rawMap) {
    const out = {};
    for (const k of Object.keys(rawMap || {}).sort()) {
      const n = Number(rawMap[k]);
      if (Number.isFinite(n) && n !== 1) out[k] = Number(n.toFixed(6));
    }
    return out;
  }

  function computeChanges(p) {
    if (!p?.data || !p?.draft) return {};
    const changes = {};
    if (p.draft.display_currency !== p.data.display_currency) {
      changes.display_currency = p.draft.display_currency;
    }
    if (Boolean(p.draft.use_builtin_presets) !== Boolean(p.data.use_builtin_presets)) {
      changes.use_builtin_presets = Boolean(p.draft.use_builtin_presets);
    }
    const baseMode = p.data.exchange_rate?.mode === "manual" ? "manual" : "auto";
    const baseManual = p.data.exchange_rate?.manual_usd_cny ?? null;
    const draftMode = p.draft.exchange_rate?.mode === "manual" ? "manual" : "auto";
    const draftManual = numOrNull(p.draft.exchange_rate?.manual_usd_cny);
    if (draftMode !== baseMode || draftManual !== baseManual) {
      changes.exchange_rate = {mode: draftMode, manual_usd_cny: draftManual};
    }
    if (Boolean(p.draft.price_sync?.auto_daily) !== Boolean(p.data.price_sync?.auto_daily !== false)) {
      changes.price_sync = {auto_daily: Boolean(p.draft.price_sync?.auto_daily)};
    }
    const baseRules = canonicalizeRulesMap(
      Object.fromEntries(Object.entries(p.data.pricing_rules || {}).map(([k, v]) => [k, cloneRuleForDraft(v)])),
    );
    const draftRules = canonicalizeRulesMap(p.draft.pricing_rules || {});
    if (JSON.stringify(draftRules) !== JSON.stringify(baseRules)) {
      changes.pricing_rules = draftRules;
    }
    const tz = p.data.pricing_timezone || "Asia/Shanghai";
    const baseScheds = canonicalizeSchedulesMap(
      Object.fromEntries(Object.entries(p.data.pricing_schedules || {}).map(([k, v]) => [k, cloneScheduleForDraft(v, tz)])),
      tz,
    );
    const draftScheds = canonicalizeSchedulesMap(p.draft.pricing_schedules || {}, tz);
    if (JSON.stringify(draftScheds) !== JSON.stringify(baseScheds)) {
      changes.pricing_schedules = draftScheds;
    }
    const baseMults = canonicalizeMultipliersMap(p.data.pricing_multipliers || {});
    const draftMults = canonicalizeMultipliersMap(p.draft.pricing_multipliers || {});
    if (JSON.stringify(draftMults) !== JSON.stringify(baseMults)) {
      changes.pricing_multipliers = draftMults;
    }
    return changes;
  }

  function isDirty(p) {
    return Boolean(p?.data && p?.draft && Object.keys(computeChanges(p)).length > 0);
  }

  function changedFields(p) {
    if (!isDirty(p)) return [];
    const labels = {
      display_currency: "计价 · 显示币种",
      use_builtin_presets: "计价 · 内置主流模型参考价",
      exchange_rate: "计价 · USD→CNY 汇率",
      price_sync: "计价 · 每日自动同步",
      pricing_rules: "计价 · 模型定价规则",
      pricing_schedules: "计价 · 分时定价",
      pricing_multipliers: "计价 · 供应商聚类倍率",
    };
    return Object.keys(computeChanges(p)).map(k => labels[k] || `计价 · ${k}`);
  }

  function isBusy(p) {
    return Boolean(p && (
      p.busy || p.syncingRates || p.syncingCatalog || p.togglingSource || p.updatingSourceCurrency
      || p.detectingProvider || p.selectingKey || p.resetting || p.clearingResidue
    ));
  }

  function validateRuleClient(rule, label) {
    if (!rule || typeof rule !== "object") return `${label} 计价规则无效。`;
    const checkPrice = (v, fieldName) => {
      if (v === null) return "";
      if (!Number.isFinite(v) || v < 0 || v > 1000000) return `${label} 的${fieldName}必须在 0 到 1000000 之间。`;
      return "";
    };
    if (rule.mode === "per_token") {
      if (!Number.isFinite(rule.multiplier) || rule.multiplier < 0 || rule.multiplier > 100) {
        return `${label} 的倍率必须在 0 到 100 之间。`;
      }
      if (!rule.free) {
        for (const [k, name] of [["input", "输入单价"], ["input_cached", "缓存命中单价"], ["cache_creation", "缓存写入单价"], ["output", "输出单价"]]) {
          const err = checkPrice(rule[k], name);
          if (err) return err;
        }
      }
      return "";
    }
    if (rule.mode === "per_tier") {
      if (!Number.isFinite(rule.base?.input) || !Number.isFinite(rule.base?.output)) {
        return `${label} 的阶梯定价必须填写基础输入与基础输出单价。`;
      }
      for (const [k, name] of [["input", "基础输入单价"], ["input_cached", "基础缓存命中单价"], ["cache_creation", "基础缓存写入单价"], ["output", "基础输出单价"]]) {
        const err = checkPrice(rule.base?.[k] ?? null, name);
        if (err) return err;
      }
      const seen = new Set();
      for (let i = 0; i < (rule.context_tiers || []).length; i++) {
        const t = rule.context_tiers[i];
        if (!Number.isInteger(t.threshold_tokens) || t.threshold_tokens < 1) {
          return `${label} 的第 ${i + 1} 个上下文阶梯阈值必须是正整数。`;
        }
        if (seen.has(t.threshold_tokens)) {
          return `${label} 的上下文阶梯阈值 ${t.threshold_tokens} 重复。`;
        }
        seen.add(t.threshold_tokens);
      }
      for (let i = 0; i < (rule.service_tiers || []).length; i++) {
        const st = rule.service_tiers[i];
        if (!st.match) return `${label} 的第 ${i + 1} 个服务档位需要填写档位标识（如 priority）。`;
      }
      return "";
    }
    if (rule.mode === "tiered_expr") {
      if (!rule.expr && !(rule.tiers && rule.tiers.length)) {
        return `${label} 的表达式定价需要填写主表达式或至少一个条件档位。`;
      }
      return "";
    }
    if (rule.mode === "per_turn" || rule.mode === "per_request") {
      if (!Number.isFinite(rule.price) || rule.price < 0 || rule.price > 1000000) {
        return `${label} 的固定单价必须是 0 到 1000000 之间的数字。`;
      }
      return "";
    }
    return `${label} 的计价模式不受支持。`;
  }

  function validateDraftBeforeSave(changes) {
    if (changes.exchange_rate) {
      const fx = changes.exchange_rate;
      if (fx.mode === "manual") {
        if (!Number.isFinite(fx.manual_usd_cny) || fx.manual_usd_cny < 1 || fx.manual_usd_cny > 100) {
          return "手动汇率必须是 1.00 到 100.00 之间的数字。";
        }
      } else if (fx.manual_usd_cny !== null && (!Number.isFinite(fx.manual_usd_cny) || fx.manual_usd_cny < 1 || fx.manual_usd_cny > 100)) {
        return "手动汇率必须是 1.00 到 100.00 之间的数字。";
      }
    }
    if (changes.pricing_rules) {
      for (const [key, rule] of Object.entries(changes.pricing_rules)) {
        const err = validateRuleClient(rule, key);
        if (err) return err;
      }
    }
    if (changes.pricing_schedules) {
      const hhmm = /^([01]\d|2[0-3]):[0-5]\d$/;
      const hhmmEnd = /^(([01]\d|2[0-3]):[0-5]\d|24:00)$/;
      for (const [key, sched] of Object.entries(changes.pricing_schedules)) {
        if (!sched.timezone) return `${key} 的分时定价需要指定 IANA 时区。`;
        for (let i = 0; i < (sched.periods || []).length; i++) {
          const period = sched.periods[i];
          if (!period.days || !period.days.length) return `${key} 第 ${i + 1} 个分时时段至少选择一个星期。`;
          if (!period.all_day) {
            if (!hhmm.test(period.start) || !hhmmEnd.test(period.end)) {
              return `${key} 第 ${i + 1} 个分时时段起止时间须为 HH:MM 格式。`;
            }
            if (period.start === period.end) {
              return `${key} 第 ${i + 1} 个分时时段起止时间不能相同。`;
            }
          }
          if (period.mode === "multiplier") {
            if (!Number.isFinite(period.multiplier) || period.multiplier < 0 || period.multiplier > 100) {
              return `${key} 第 ${i + 1} 个分时时段倍率必须在 0 到 100 之间。`;
            }
          } else {
            const err = validateRuleClient(period.override, `${key} 第 ${i + 1} 个分时覆盖规则`);
            if (err) return err;
          }
        }
      }
    }
    return "";
  }

  /* ---------- Provider / model lookup & rule summary helpers ---------- */

  function allProviders(data) {
    if (!data) return [];
    const list = [];
    for (const c of data.clusters || []) {
      for (const item of c.providers || []) list.push(item);
    }
    for (const item of data.deleted_providers || []) list.push(item);
    return list;
  }

  function findProvider(data, pid) {
    return allProviders(data).find(item => item.id === pid) || null;
  }

  function activeModelFor(p, item) {
    const pid = item.id;
    const saved = p.activeModelByProvider?.[pid];
    if (saved && (item.candidates || []).includes(saved)) return saved;
    return item.model || (item.candidates && item.candidates[0]) || "";
  }

  function ruleKeyFor(pid, model) {
    return model ? `${pid}|${model}` : pid;
  }

  function lookupDraftRule(p, item, model) {
    const rules = p.draft?.pricing_rules || {};
    const exact = model ? `${item.id}|${model}` : "";
    if (exact && Object.hasOwn(rules, exact)) return {key: exact, rule: rules[exact], isCustom: true};
    if ((!model || model === item.model) && Object.hasOwn(rules, item.id)) {
      return {key: item.id, rule: rules[item.id], isCustom: true};
    }
    return {key: exact || item.id, rule: null, isCustom: false};
  }

  function lookupDraftSchedule(p, item, model) {
    const schedules = p.draft?.pricing_schedules || {};
    const exact = model ? `${item.id}|${model}` : "";
    if (exact && Object.hasOwn(schedules, exact)) return {key: exact, schedule: schedules[exact]};
    if ((!model || model === item.model) && Object.hasOwn(schedules, item.id)) {
      return {key: item.id, schedule: schedules[item.id]};
    }
    return {key: exact || item.id, schedule: null};
  }

  function fallbackRuleFor(data, item, model) {
    const pid = item.id;
    const sel = data?.selections?.[pid]?.[model];
    if (sel?.rule) return {source: "catalog_selected", label: sel.source_label || sel.source_id, rule: sel.rule};
    const auto = data?.auto_selected?.[pid]?.[model];
    if (auto?.rule) return {source: "catalog_auto", label: auto.source_label || auto.source_id, rule: auto.rule};
    if (data?.use_builtin_presets !== false && item.preset_match && (!model || model === item.model)) {
      return {
        source: "builtin_preset",
        label: item.preset_match.label || item.preset_match.preset_id,
        rule: {
          mode: "per_token", currency: "USD",
          input: item.preset_match.input_per_million,
          input_cached: item.preset_match.cached_input_per_million,
          cache_creation: item.preset_match.cache_creation_per_million,
          output: item.preset_match.output_per_million,
        },
      };
    }
    return null;
  }

  function convertRateCurrency(value, fromCur, toCur, usdCny) {
    if (value == null || !Number.isFinite(Number(value))) return null;
    const num = Number(value);
    if (fromCur === toCur) return num;
    const rate = Number.isFinite(Number(usdCny)) && Number(usdCny) >= 1 ? Number(usdCny) : 7.2;
    if (fromCur === "USD" && toCur === "CNY") return Number((num * rate).toFixed(6));
    if (fromCur === "CNY" && toCur === "USD") return Number((num / rate).toFixed(6));
    return num;
  }

  function inheritedPlaceholders(data, item, model, targetCurrency) {
    const fb = fallbackRuleFor(data, item, model);
    if (!fb?.rule) return {input: "", input_cached: "", cache_creation: "", output: ""};
    const r = fb.rule;
    const fromCur = r.currency === "CNY" ? "CNY" : "USD";
    const src = r.mode === "per_tier" && r.base ? r.base : r;
    const usdCny = data?.exchange_rate?.effective_usd_cny || 7.2;
    const fmt = v => {
      const c = convertRateCurrency(v, fromCur, targetCurrency, usdCny);
      return c == null ? "" : `继承 ${formatRateNumber(c)}`;
    };
    return {
      input: fmt(src.input),
      input_cached: fmt(src.input_cached),
      cache_creation: src.cache_creation != null ? fmt(src.cache_creation) : "默认同输入价",
      output: fmt(src.output),
    };
  }

  function ruleSummary(rule) {
    if (!rule || typeof rule !== "object") return "未设单价";
    const mode = rule.mode || "per_token";
    const sym = currencySymbol(rule.currency === "CNY" ? "CNY" : "USD");
    if (mode === "per_token") {
      if (rule.free || Number(rule.multiplier) === 0) return "免费";
      const inp = rule.input != null ? `${sym}${formatRateNumber(rule.input)}` : "继承";
      const cached = rule.input_cached != null ? `${sym}${formatRateNumber(rule.input_cached)}` : "—";
      const out = rule.output != null ? `${sym}${formatRateNumber(rule.output)}` : "继承";
      return `入 ${inp} / 读 ${cached} / 出 ${out} /M`;
    }
    if (mode === "per_tier") {
      const b = rule.base || {};
      const ctxN = (rule.context_tiers || []).length;
      const svcN = (rule.service_tiers || []).length;
      const countText = [ctxN ? `${ctxN} 上下文档` : "", svcN ? `${svcN} 服务档` : ""].filter(Boolean).join(" · ");
      return `阶梯 · 基价 ${sym}${formatRateNumber(b.input)}/${sym}${formatRateNumber(b.output)}${countText ? ` (${countText})` : ""}`;
    }
    if (mode === "tiered_expr") {
      const n = (rule.tiers || []).length;
      return n ? `${sym} 表达式 (${n} 档)` : `${sym} 表达式公式`;
    }
    if (mode === "per_turn") {
      return `${sym}${formatRateNumber(rule.price)} / 次调用`;
    }
    if (mode === "per_request") {
      return `${sym}${formatRateNumber(rule.price)} / 次请求`;
    }
    return "已配置";
  }

  function customUnitInfoForSource(src) {
    if (!src || src.kind !== "newapi") return null;
    const smeta = src.site_meta && typeof src.site_meta === "object" ? src.site_meta : null;
    if (!smeta) return null;
    const qdt = String(smeta.quota_display_type || "").trim().toUpperCase();
    if (!["CUSTOM", "POINTS", "POINT", "CREDIT"].includes(qdt)) return null;
    const symbol = String(smeta.custom_currency_symbol || "").trim();
    if (!symbol || symbol === "¤") return null;
    const customRate = Number(smeta.custom_currency_exchange_rate);
    if (!Number.isFinite(customRate) || customRate <= 0) return null;
    const fiatCurrency = src.currency === "CNY" ? "CNY" : "USD";
    const fiatSym = currencySymbol(fiatCurrency);
    const effScaleCandidates = [
      src.effective_unit_scale,
      src.fiat_per_base_unit,
      smeta.effective_unit_scale,
      smeta.quota_fiat_per_base_unit,
      smeta.site_recharge_price,
      smeta.recharge_price,
    ];
    let effectiveFiatPerBase = 1;
    for (const c of effScaleCandidates) {
      const n = Number(c);
      if (Number.isFinite(n) && n > 0) {
        effectiveFiatPerBase = n;
        break;
      }
    }
    const nativePerFiat = customRate / effectiveFiatPerBase;
    if (!Number.isFinite(nativePerFiat) || nativePerFiat <= 0) return null;
    return {
      sourceId: src.source_id,
      symbol,
      customRate,
      fiatCurrency,
      fiatSym,
      fiatLabel: fiatCurrency === "CNY" ? "¥ 人民币" : "$ 美元",
      effectiveFiatPerBase,
      nativePerFiat,
      ratioText: `${formatRateNumber(nativePerFiat)} ${symbol} = ${fiatSym}1`,
      hasManualRate: src.fiat_per_base_unit != null && Number(src.fiat_per_base_unit) > 0,
    };
  }

  function ruleSummaryCustomUnit(rule, customInfo) {
    if (!rule || typeof rule !== "object" || !customInfo) return "";
    const cur = rule.currency === "CNY" ? "CNY" : "USD";
    if (cur !== customInfo.fiatCurrency) return "";
    const mode = rule.mode || "per_token";
    const mult = customInfo.nativePerFiat;
    const sym = customInfo.symbol;
    const fmt = v => (v == null || !Number.isFinite(Number(v))) ? null : `${formatRateNumber(Number(v) * mult)} ${sym}`;
    if (mode === "per_token") {
      if (rule.free || Number(rule.multiplier) === 0) return "";
      const inp = fmt(rule.input);
      const cached = fmt(rule.input_cached);
      const out = fmt(rule.output);
      if (!inp && !out) return "";
      return `入 ${inp || "继承"} / 读 ${cached || "—"} / 出 ${out || "继承"} /M`;
    }
    if (mode === "per_tier") {
      const b = rule.base || {};
      const inp = fmt(b.input);
      const out = fmt(b.output);
      if (!inp || !out) return "";
      return `阶梯 · 基价 ${inp}/${out}`;
    }
    if (mode === "per_turn") {
      const p = fmt(rule.price);
      return p ? `${p} / 次调用` : "";
    }
    if (mode === "per_request") {
      const p = fmt(rule.price);
      return p ? `${p} / 次请求` : "";
    }
    return "";
  }

  function dualSummaryParts(rule, customInfo, viewMode = "fiat") {
    const fiatText = rule ? ruleSummary(rule) : "点击配置单价";
    const nativeText = (rule && customInfo) ? ruleSummaryCustomUnit(rule, customInfo) : "";
    if (!nativeText) {
      return {dual: false, main: fiatText, sub: "", fiatText, nativeText: ""};
    }
    const isNative = viewMode === "native";
    return {
      dual: true,
      main: isNative ? nativeText : fiatText,
      sub: isNative ? `（约 ${fiatText}）` : `（${nativeText}）`,
      fiatText,
      nativeText,
    };
  }

  function renderDualSummaryInner(rule, customInfo, viewMode = "fiat") {
    const parts = dualSummaryParts(rule, customInfo, viewMode);
    if (!parts.dual) return esc(parts.main);
    return `<span class="pw-dual-main">${esc(parts.main)}</span><span class="pw-dual-sub">${esc(parts.sub)}</span>`;
  }

  function syncUnitViewInPlace(ctx, sourceId, nextMode) {
    const {p, render, root} = ctx;
    p.sourceUnitView ||= {};
    p.sourceUnitView[sourceId] = nextMode;
    saveUnitViewPrefs(p.sourceUnitView);
    try {
      const container = root?.();
      if (!container?.querySelectorAll) {
        render();
        return;
      }
      const toggleBtns = [...container.querySelectorAll("[data-pricing-unit-view]")].filter(
        el => el.dataset?.pricingUnitView === sourceId,
      );
      if (!toggleBtns.length) {
        render();
        return;
      }
      for (const btn of toggleBtns) {
        const on = btn.dataset?.unitMode === nextMode;
        btn.classList?.toggle?.("on", on);
        btn.setAttribute?.("aria-pressed", on ? "true" : "false");
      }
      const dualEls = [...container.querySelectorAll("[data-pw-dual-src]")].filter(
        el => el.dataset?.pwDualSrc === sourceId,
      );
      for (const el of dualEls) {
        const fiatText = el.dataset?.pwFiat || "";
        const nativeText = el.dataset?.pwNative || "";
        if (!fiatText || !nativeText) continue;
        const isNative = nextMode === "native";
        const mainEl = el.querySelector?.(".pw-dual-main");
        const subEl = el.querySelector?.(".pw-dual-sub");
        if (mainEl) mainEl.textContent = isNative ? nativeText : fiatText;
        if (subEl) subEl.textContent = isNative ? `（约 ${fiatText}）` : `（${nativeText}）`;
      }
    } catch {
      render();
    }
  }

  function estimateProviderCostMicrousd(item, usdCnyRate = 7.2) {
    const rate = Number.isFinite(Number(usdCnyRate)) && Number(usdCnyRate) >= 1 ? Number(usdCnyRate) : 7.2;
    const rule = item?.effective_rule;
    if (!rule) return null;
    const mode = rule.mode || "per_token";
    const cur = rule.currency === "CNY" ? "CNY" : "USD";
    const toUsd = v => cur === "CNY" ? (v / rate) : v;

    if (mode === "per_turn") {
      if (rule.price == null) return null;
      const mult = Number.isFinite(Number(rule.multiplier)) ? Number(rule.multiplier) : 1;
      return Math.round((Number(item.calls) || 0) * toUsd(Number(rule.price)) * mult * 1e6);
    }
    if (mode === "per_request") {
      if (rule.price == null) return null;
      const mult = Number.isFinite(Number(rule.multiplier)) ? Number(rule.multiplier) : 1;
      return Math.round((Number(item.requests) || Number(item.calls) || 0) * toUsd(Number(rule.price)) * mult * 1e6);
    }
    if (mode === "tiered_expr") return null;

    const tariff = item.effective_tariff_usd;
    if (tariff && tariff.input_per_million != null && tariff.output_per_million != null) {
      const inp = Math.max(0, Number(item.input_tokens) || 0);
      const out = Math.max(0, Number(item.output_tokens) || 0);
      const cached = Math.min(inp, Math.max(0, Number(item.cached_input_tokens) || 0));
      const rem = inp - cached;
      const creation = Math.min(rem, Math.max(0, Number(item.cache_creation_tokens) || 0));
      const uncached = rem - creation;
      const inR = Number(tariff.input_per_million) || 0;
      const cachedR = tariff.cached_input_per_million == null ? inR : Number(tariff.cached_input_per_million);
      const ccR = tariff.cache_creation_per_million == null ? inR : Number(tariff.cache_creation_per_million);
      const outR = Number(tariff.output_per_million) || 0;
      if (inp === 0 && out === 0 && (Number(item.total_tokens) || 0) > 0) {
        return Math.round(Number(item.total_tokens) * inR);
      }
      return Math.round(uncached * inR + cached * cachedR + creation * ccR + out * outR);
    }
    return null;
  }

  function statusBadgeFor(p, item) {
    const model = activeModelFor(p, item);
    const {rule: draftRule, isCustom} = lookupDraftRule(p, item, model);
    if (isCustom && draftRule) {
      if (draftRule.mode === "per_token" && draftRule.free) return {text: "免费", tone: "ok"};
      return {text: "自定义", tone: "accent"};
    }
    const src = item.effective_source || "unconfigured";
    if (src === "provider_free") return {text: "免费", tone: "ok"};
    if (src === "model_override" || src === "provider_override") return {text: "自定义", tone: "accent"};
    if (src === "catalog_selected") return {text: "已选源", tone: "accent"};
    if (src === "catalog_auto") return {text: "自动匹配", tone: "ok"};
    if (src === "builtin_preset") return {text: "内置参考", tone: "muted"};
    if (src === "legacy_config") return {text: "旧版配置", tone: "muted"};
    return {text: "未定价", tone: "warn"};
  }

  function isProviderCardDirty(p, item) {
    if (!p?.data || !p?.draft) return false;
    const tz = p.data.pricing_timezone || "Asia/Shanghai";
    const pid = item.id;
    const keys = new Set([
      ...Object.keys(p.data.pricing_rules || {}),
      ...Object.keys(p.draft.pricing_rules || {}),
    ].filter(k => k === pid || k.startsWith(pid + "|")));
    for (const k of keys) {
      const a = p.data.pricing_rules?.[k] ? canonicalizeRule(cloneRuleForDraft(p.data.pricing_rules[k])) : null;
      const b = p.draft.pricing_rules?.[k] ? canonicalizeRule(p.draft.pricing_rules[k]) : null;
      if (JSON.stringify(a) !== JSON.stringify(b)) return true;
    }
    const skeys = new Set([
      ...Object.keys(p.data.pricing_schedules || {}),
      ...Object.keys(p.draft.pricing_schedules || {}),
    ].filter(k => k === pid || k.startsWith(pid + "|")));
    for (const k of skeys) {
      const a = p.data.pricing_schedules?.[k] ? canonicalizeSchedule(cloneScheduleForDraft(p.data.pricing_schedules[k], tz), tz) : null;
      const b = p.draft.pricing_schedules?.[k] ? canonicalizeSchedule(p.draft.pricing_schedules[k], tz) : null;
      if (JSON.stringify(a) !== JSON.stringify(b)) return true;
    }
    return false;
  }

  function ensureSelectedCluster(p) {
    const clusters = p.data?.clusters || [];
    if (!clusters.length) { p.selectedCluster = ""; return null; }
    const found = clusters.find(c => c.cluster_id === p.selectedCluster);
    if (found) return found;
    p.selectedCluster = clusters[0].cluster_id;
    return clusters[0];
  }

  function syncDataPreserveDraft(p, rawData, {forceResetDraft = false} = {}) {
    const normalized = normalizePayload(rawData);
    const keepDirty = !forceResetDraft && isDirty(p);
    p.data = normalized;
    if (!keepDirty || !p.draft || p.stale) {
      p.draft = draftFromData(normalized);
    }
    p.loaded = true;
    p.stale = false;
    rememberDisplay(normalized);
    ensureSelectedCluster(p);
  }

  /* ---------- Async API actions ---------- */

  async function refresh(ctx) {
    const {p, path, api, render, isCurrent, ticket, fail} = ctx;
    if (p.loading || isBusy(p)) return;
    const t = ticket();
    p.loading = true;
    p.bad = false;
    p.resetArmed = false;
    p.notice = "正在读取模型计价、价格目录与汇率配置…";
    render();
    try {
      const data = await api("GET", path + "/pricing");
      if (!isCurrent(t)) return;
      syncDataPreserveDraft(p, data);
      p.bad = false;
      p.notice = "";
    } catch (err) {
      if (!isCurrent(t)) return;
      p.bad = true;
      p.notice = fail(err, "无法读取模型计价配置。");
    } finally {
      p.loading = false;
      if (isCurrent(t)) render();
    }
  }

  async function save(ctx) {
    const {p, path, api, render, isCurrent, ticket, fail} = ctx;
    if (p.loading || isBusy(p) || !p.data || p.stale) return;
    const changes = computeChanges(p);
    if (!Object.keys(changes).length) return;
    const validationError = validateDraftBeforeSave(changes);
    if (validationError) {
      p.bad = true;
      p.notice = validationError;
      render();
      return;
    }
    const t = ticket();
    p.busy = true;
    p.bad = false;
    p.notice = "正在保存计价与汇率配置…";
    render();
    try {
      const data = await api("POST", path + "/pricing", {
        revision: p.data.revision,
        changes,
      });
      if (!isCurrent(t)) return;
      syncDataPreserveDraft(p, data, {forceResetDraft: true});
      p.bad = false;
      p.notice = "计价与汇率配置已保存，费用估算已按最新规则生效。";
    } catch (err) {
      if (!isCurrent(t)) return;
      if ((err?.code || err?.message) === "pricing_config_conflict") p.stale = true;
      p.bad = true;
      p.notice = fail(err, "计价与汇率配置保存未完成。");
    } finally {
      p.busy = false;
      if (isCurrent(t)) render();
    }
  }

  async function applyClusterMultiplier(ctx, clusterId, rawValue) {
    const {p, path, api, render, isCurrent, ticket, fail} = ctx;
    if (!p.data || !p.draft || p.loading || isBusy(p) || p.stale) return;
    const num = rawValue === "" || rawValue === null ? 1 : Number(rawValue);
    if (!Number.isFinite(num) || num < 0 || num > 100) {
      p.bad = true;
      p.notice = "聚类倍率必须在 0 到 100 之间（填 0 表示免费，1 表示原价）。";
      render();
      return;
    }
    const nextMults = canonicalizeMultipliersMap(p.data.pricing_multipliers || {});
    if (num === 1) delete nextMults[clusterId];
    else nextMults[clusterId] = Number(num.toFixed(6));

    // Preserve user input in clusterMultDraft until POST succeeds; never corrupt draft on rejection.
    p.clusterMultDraft[clusterId] = String(num);

    const t = ticket();
    p.busy = true;
    p.bad = false;
    p.notice = `正在更新聚类倍率（${formatRateNumber(num)}×）…`;
    render();
    try {
      const data = await api("POST", path + "/pricing", {
        revision: p.data.revision,
        changes: {pricing_multipliers: nextMults},
      });
      if (!isCurrent(t)) return;
      delete p.clusterMultDraft[clusterId];
      syncDataPreserveDraft(p, data);
      if (p.draft) p.draft.pricing_multipliers = canonicalizeMultipliersMap(p.data.pricing_multipliers || {});
      p.bad = false;
      p.notice = num === 1 ? "已恢复供应商聚类倍率为 1×。" : `已应用供应商聚类倍率 ${formatRateNumber(num)}×。`;
    } catch (err) {
      if (!isCurrent(t)) return;
      if ((err?.code || err?.message) === "pricing_config_conflict") p.stale = true;
      p.bad = true;
      p.notice = fail(err, "更新聚类倍率失败。");
    } finally {
      p.busy = false;
      if (isCurrent(t)) render();
    }
  }

  async function syncRates(ctx) {
    const {p, path, api, render, isCurrent, ticket, fail} = ctx;
    if (p.loading || isBusy(p)) return;
    const t = ticket();
    p.syncingRates = true;
    p.bad = false;
    p.notice = "正在获取最新实时 USD→CNY 汇率…";
    render();
    try {
      const result = await api("POST", path + "/pricing/sync-rates", {}, 25000);
      if (!isCurrent(t)) return;
      const sync = result?.sync || {};
      syncDataPreserveDraft(p, result);
      if (sync.synced) {
        p.bad = false;
        p.notice = `已更新实时汇率：1 USD = ¥${Number(sync.usd_cny_rate || p.data.exchange_rate.effective_usd_cny).toFixed(4)}（来源 ${sync.source || "在线汇率"}）。`;
      } else {
        p.bad = true;
        p.notice = sync.error || "汇率刷新失败，继续使用当前缓存或默认参考汇率。";
      }
    } catch (err) {
      if (!isCurrent(t)) return;
      p.bad = true;
      p.notice = fail(err, "汇率刷新失败，继续使用当前汇率。");
    } finally {
      p.syncingRates = false;
      if (isCurrent(t)) render();
    }
  }

  async function syncCatalog(ctx, sourceIds = null) {
    const {p, path, api, render, isCurrent, ticket, fail} = ctx;
    if (p.loading || isBusy(p)) return;
    const t = ticket();
    p.syncingCatalog = true;
    p.syncNotice = "正在拉取在线与 New API 价格目录…";
    render();
    try {
      const body = Array.isArray(sourceIds) && sourceIds.length ? {source_ids: sourceIds} : {};
      const result = await api("POST", path + "/pricing/sync", body, 60000);
      if (!isCurrent(t)) return;
      const sync = result?.sync || {};
      syncDataPreserveDraft(p, result);
      const okCount = Number(sync.ok_count) || 0;
      const errCount = Number(sync.error_count) || 0;
      p.syncNotice = errCount > 0
        ? `同步完成：成功 ${okCount} 个，失败 ${errCount} 个（悬停失败芯片查看详情）。`
        : okCount > 0
          ? `同步完成：已刷新 ${okCount} 个价格源。`
          : "当前没有已启用的价格源。";
    } catch (err) {
      if (!isCurrent(t)) return;
      p.syncNotice = fail(err, "拉取最新价格失败，请检查网络或代理设置。");
    } finally {
      p.syncingCatalog = false;
      if (isCurrent(t)) render();
    }
  }

  async function toggleSource(ctx, sourceId) {
    const {p, path, api, render, isCurrent, ticket, fail} = ctx;
    if (!p.data || p.loading || isBusy(p)) return;
    const source = (p.data.sources || []).find(item => item.source_id === sourceId);
    if (!source) return;
    const nextEnabled = !source.enabled;
    const t = ticket();
    p.togglingSource = sourceId;
    p.syncNotice = nextEnabled ? `正在启用并同步 ${source.label}…` : `正在停用 ${source.label}…`;
    render();
    try {
      const result = await api("POST", path + "/pricing/sources/toggle", {
        source_id: sourceId,
        enabled: nextEnabled,
        sync: nextEnabled,
        revision: p.data.revision,
      }, 45000);
      if (!isCurrent(t)) return;
      syncDataPreserveDraft(p, result);
      p.syncNotice = nextEnabled
        ? (result?.sync?.error_count ? `${source.label} 已启用，但同步出现异常。` : `${source.label} 已启用并完成同步。`)
        : `${source.label} 已停用。`;
    } catch (err) {
      if (!isCurrent(t)) return;
      p.syncNotice = fail(err, "切换价格源状态失败。");
    } finally {
      p.togglingSource = "";
      if (isCurrent(t)) render();
    }
  }

  async function updateSourceCurrency(ctx, sourceId, currencyOverride, opts = {}) {
    const {p, path, api, render, isCurrent, ticket, fail} = ctx;
    if (!p.data || p.loading || isBusy(p)) return;
    const source = (p.data.sources || []).find(item => item.source_id === sourceId);
    if (!source) return;
    const nextOverride = currencyOverride === "CNY" || currencyOverride === "USD" ? currencyOverride : "auto";
    const hasFiatRate = Object.hasOwn(opts, "fiatPerBaseUnit");
    const rateOnly = Boolean(opts.rateOnly);
    const reqBody = {
      source_id: sourceId,
      currency_override: nextOverride,
      sync: true,
      revision: p.data.revision,
    };
    if (hasFiatRate) {
      reqBody.fiat_per_base_unit = opts.fiatPerBaseUnit;
    }
    const t = ticket();
    p.updatingSourceCurrency = sourceId;
    p.syncNotice = rateOnly
      ? `正在更新 ${source.label} 的折算率并重新同步…`
      : `正在更新 ${source.label} 的结算币种并重新同步…`;
    render();
    try {
      const result = await api("POST", path + "/pricing/sources/currency", reqBody, 45000);
      if (!isCurrent(t)) return;
      if (hasFiatRate && p.sourceRateDraft) delete p.sourceRateDraft[sourceId];
      syncDataPreserveDraft(p, result);
      const labelText = nextOverride === "CNY" ? "人民币 CNY (¥)" : nextOverride === "USD" ? "美元 USD ($)" : "自动识别";
      p.bad = false;
      if (rateOnly) {
        p.syncNotice = result?.sync?.error_count
          ? `${source.label} 折算率已更新，但重新同步出现异常。`
          : opts.fiatPerBaseUnit == null
            ? `${source.label} 已恢复自动折算率并完成重新同步。`
            : `${source.label} 折算率已更新并完成重新同步。`;
      } else {
        p.syncNotice = result?.sync?.error_count
          ? `${source.label} 币种已设为 ${labelText}，但重新同步出现异常。`
          : `${source.label} 结算币种已切换为 ${labelText} 并完成重新同步。`;
      }
    } catch (err) {
      if (!isCurrent(t)) return;
      if ((err?.code || err?.message) === "pricing_config_conflict") p.stale = true;
      p.bad = true;
      p.syncNotice = fail(err, rateOnly ? "更新价格源折算率失败。" : "更新价格源币种失败。");
    } finally {
      p.updatingSourceCurrency = "";
      if (isCurrent(t)) render();
    }
  }

  async function detectNewApi(ctx, providerId) {
    const {p, path, api, render, isCurrent, ticket, fail} = ctx;
    if (!p.data || p.loading || isBusy(p)) return;
    const t = ticket();
    p.detectingProvider = providerId;
    p.syncNotice = `正在探测 ${providerId} 的 New API /api/pricing 接口…`;
    render();
    try {
      const result = await api("POST", path + "/pricing/sources/detect", {
        provider_id: providerId,
        enable: true,
      }, 35000);
      if (!isCurrent(t)) return;
      const det = result?.detection || {};
      syncDataPreserveDraft(p, result);
      if (det.ok) {
        const displayName = det.site_name || det.source_id;
        let curNote = "";
        if (det.quota_display_type === "CUSTOM" && det.custom_currency_symbol) {
          const sym = det.custom_currency_symbol;
          const fiatLabel = det.currency === "CNY" ? "CNY ¥" : "USD $";
          const crate = Number(det.custom_currency_exchange_rate);
          const scale = Number(det.effective_unit_scale || det.recharge_price || 1);
          const ratioStr = Number.isFinite(crate) && crate > 0 && Number.isFinite(scale) && scale > 0
            ? `（${formatRateNumber(crate / scale)} ${sym} = ${det.currency === "CNY" ? "¥" : "$"}1）`
            : "";
          curNote = ` · ${fiatLabel}${ratioStr}`;
        } else if (det.currency === "CNY") {
          curNote = " · 人民币 CNY";
        } else if (det.currency === "USD") {
          curNote = " · 美元 USD";
        }
        const dedupNote = det.deduplicated ? "（已复用同域名价格源）" : "";
        const groupNote = det.detected_group && det.detected_group_ratio != null
          ? ` · 推荐分组 ${det.detected_group} (${formatRateNumber(det.detected_group_ratio)}×)`
          : "";
        const skipNote = Number(det.skipped_expr_count) > 0
          ? ` · 跳过 ${Number(det.skipped_expr_count)} 个特殊私有公式`
          : "";
        const keyNote = det.needs_key ? " · 返回 0 条模型，可能需要网关密钥或配置可见价格" : "";
        p.bad = false;
        p.syncNotice = `探测成功：已接入 ${displayName}（${Number(det.models) || 0} 个模型${curNote}）${dedupNote}${groupNote}${skipNote}${keyNote}。`;
      } else {
        p.bad = true;
        p.syncNotice = det.error || "未探测到兼容的 New API /api/pricing 接口。";
      }
    } catch (err) {
      if (!isCurrent(t)) return;
      p.bad = true;
      p.syncNotice = fail(err, "New API 价格源探测失败。");
    } finally {
      p.detectingProvider = "";
      if (isCurrent(t)) render();
    }
  }

  async function selectCandidate(ctx, providerId, model, priceKey) {
    const {p, path, api, render, isCurrent, ticket, fail} = ctx;
    if (!p.data || p.loading || isBusy(p)) return;
    const t = ticket();
    p.selectingKey = `${providerId}|${model}`;
    render();
    try {
      const result = await api("POST", path + "/pricing/select", {
        provider_id: providerId,
        model,
        price_key: priceKey,
        revision: p.data.revision,
      });
      if (!isCurrent(t)) return;
      syncDataPreserveDraft(p, result);
      p.bad = false;
      p.notice = `已为 ${providerId}（${model}）选用目录价格来源。`;
    } catch (err) {
      if (!isCurrent(t)) return;
      p.bad = true;
      p.notice = fail(err, "选用目录价格失败。");
    } finally {
      p.selectingKey = "";
      if (isCurrent(t)) render();
    }
  }

  async function resetSelection(ctx, providerId, model) {
    const {p, path, api, render, isCurrent, ticket, fail} = ctx;
    if (!p.data || p.loading || isBusy(p)) return;
    const t = ticket();
    p.selectingKey = `${providerId}|${model || ""}`;
    render();
    try {
      const body = {provider_id: providerId, revision: p.data.revision};
      if (model) body.model = model;
      const result = await api("POST", path + "/pricing/select/reset", body);
      if (!isCurrent(t)) return;
      syncDataPreserveDraft(p, result);
      p.bad = false;
      p.notice = `已清除 ${providerId} 的目录价格锁定，将恢复自动匹配或默认参考价。`;
    } catch (err) {
      if (!isCurrent(t)) return;
      p.bad = true;
      p.notice = fail(err, "清除目录选择失败。");
    } finally {
      p.selectingKey = "";
      if (isCurrent(t)) render();
    }
  }

  async function validateExpression(ctx, providerId) {
    const {p, path, api, render, isCurrent, ticket, fail} = ctx;
    if (!p.data || !p.draft || p.validatingKey) return;
    const item = findProvider(p.data, providerId);
    if (!item) return;
    const model = activeModelFor(p, item);
    const {key, rule} = lookupDraftRule(p, item, model);
    const baseRule = baseRuleForModel(p, item, model);
    const activeRule = rule || (baseRule?.mode === "tiered_expr" ? cloneRuleForDraft(baseRule) : null);
    if (!activeRule) {
      p.exprResults[key] = {valid: false, error: "请先输入计价表达式再试算。"};
      render();
      return;
    }
    const canon = canonicalizeRule(activeRule);
    // Strict API contract: POST /pricing/expr/validate ONLY allows expr, tiers, default_expr, cluster_multiplier, currency, sample!
    const body = {};
    if (canon.expr) body.expr = canon.expr;
    if (canon.tiers && canon.tiers.length) body.tiers = canon.tiers;
    if (canon.default_expr) body.default_expr = canon.default_expr;
    if (canon.currency === "CNY" || canon.currency === "USD") body.currency = canon.currency;
    const clusterMult = Number(p.draft.pricing_multipliers?.[item.cluster_id] ?? item.cluster_multiplier ?? 1);
    if (Number.isFinite(clusterMult) && clusterMult !== 1) body.cluster_multiplier = clusterMult;

    const t = ticket();
    p.validatingKey = key;
    render();
    try {
      const result = await api("POST", path + "/pricing/expr/validate", body);
      if (!isCurrent(t)) return;
      p.exprResults[key] = result;
    } catch (err) {
      if (!isCurrent(t)) return;
      p.exprResults[key] = {valid: false, error: fail(err, "表达式试算请求失败。")};
    } finally {
      p.validatingKey = "";
      if (isCurrent(t)) render();
    }
  }

  async function resetAllPricing(ctx) {
    const {p, path, api, render, isCurrent, ticket, fail} = ctx;
    if (!p.data || p.loading || isBusy(p)) return;
    const t = ticket();
    p.resetting = true;
    p.resetArmed = false;
    p.bad = false;
    p.notice = "正在重置全部自定义计价规则、分时定价、聚类倍率与目录选择…";
    render();
    try {
      const result = await api("POST", path + "/pricing/reset", {
        scope: "all",
        revision: p.data.revision,
      });
      if (!isCurrent(t)) return;
      p.exprResults = {};
      p.clusterMultDraft = {};
      syncDataPreserveDraft(p, result, {forceResetDraft: true});
      p.bad = false;
      p.notice = "已重置全部自定义计价配置。";
    } catch (err) {
      if (!isCurrent(t)) return;
      p.bad = true;
      p.notice = fail(err, "重置计价配置失败。");
    } finally {
      p.resetting = false;
      if (isCurrent(t)) render();
    }
  }

  async function clearResidue(ctx, providerIds = null) {
    const {p, path, api, render, isCurrent, ticket, fail} = ctx;
    if (!p.data || p.loading || isBusy(p)) return;
    const t = ticket();
    p.clearingResidue = true;
    p.bad = false;
    render();
    try {
      const body = {revision: p.data.revision};
      if (Array.isArray(providerIds) && providerIds.length) body.provider_ids = providerIds;
      const result = await api("POST", path + "/pricing/residue/clear", body);
      if (!isCurrent(t)) return;
      const cleared = Array.isArray(result?.cleared_provider_ids) ? result.cleared_provider_ids : [];
      syncDataPreserveDraft(p, result, {forceResetDraft: !isDirty(p)});
      if (p.draft && cleared.length) {
        const clearedSet = new Set(cleared);
        for (const k of Object.keys(p.draft.pricing_rules || {})) {
          if (clearedSet.has(k.split("|")[0])) delete p.draft.pricing_rules[k];
        }
        for (const k of Object.keys(p.draft.pricing_schedules || {})) {
          if (clearedSet.has(k.split("|")[0])) delete p.draft.pricing_schedules[k];
        }
        for (const cid of cleared) delete p.draft.pricing_multipliers[cid];
      }
      p.bad = false;
      p.notice = cleared.length
        ? `已清理 ${cleared.length} 个已删除供应商的残留计价配置（历史 Token 审计记录保留不变）。`
        : "没有需要清理的残留计价覆盖配置。";
    } catch (err) {
      if (!isCurrent(t)) return;
      p.bad = true;
      p.notice = fail(err, "清理残留配置失败。");
    } finally {
      p.clearingResidue = false;
      if (isCurrent(t)) render();
    }
  }

  function discard(ctx) {
    const {p, render} = ctx;
    if (!p.data) return;
    p.draft = draftFromData(p.data);
    p.clusterMultDraft = {};
    p.exprResults = {};
    p.stale = false;
    p.bad = false;
    p.resetArmed = false;
    p.notice = "已放弃未保存的计价草稿。";
    render();
  }

  /* ---------- Draft mutation helpers ---------- */

  function baseRuleForModel(p, item, model) {
    const fb = fallbackRuleFor(p.data, item, model);
    if (fb?.rule) return fb.rule;
    if ((!model || model === item.model) && item.effective_rule) return item.effective_rule;
    return null;
  }

  function ensureDraftRule(p, item, model) {
    const {key, rule, isCustom} = lookupDraftRule(p, item, model);
    if (isCustom && rule) return {key, rule};
    const targetKey = ruleKeyFor(item.id, model);
    const baseRule = baseRuleForModel(p, item, model);
    let seed;
    if (baseRule && baseRule.mode && baseRule.mode !== "per_token") {
      seed = cloneRuleForDraft(baseRule);
    } else {
      seed = {
        mode: "per_token",
        currency: p.draft?.display_currency === "CNY" ? "CNY" : "USD",
        input: "", input_cached: "", cache_creation: "", output: "",
        multiplier: "1", free: false,
      };
    }
    p.draft.pricing_rules[targetKey] = seed;
    return {key: targetKey, rule: seed};
  }

  function switchDraftMode(p, item, model, nextMode) {
    const {rule} = ensureDraftRule(p, item, model);
    if (rule.mode === nextMode) return;
    const prevCurrency = rule.currency === "CNY" ? "CNY" : "USD";
    const baseRule = baseRuleForModel(p, item, model);
    const fbSrc = baseRule?.mode === "per_tier" ? baseRule.base : baseRule;
    const targetKey = lookupDraftRule(p, item, model).key;

    if (nextMode === "per_token") {
      const fromBase = rule.mode === "per_tier" ? (rule.base || {}) : {};
      p.draft.pricing_rules[targetKey] = {
        mode: "per_token",
        currency: prevCurrency,
        input: fromBase.input ?? "",
        input_cached: fromBase.input_cached ?? "",
        cache_creation: fromBase.cache_creation ?? "",
        output: fromBase.output ?? "",
        multiplier: rule.multiplier || "1",
        free: false,
      };
      return;
    }
    if (nextMode === "per_tier") {
      const baseIn = rule.mode === "per_token" && rule.input !== "" ? rule.input : (fbSrc?.input != null ? String(fbSrc.input) : "");
      const baseCached = rule.mode === "per_token" && rule.input_cached !== "" ? rule.input_cached : (fbSrc?.input_cached != null ? String(fbSrc.input_cached) : "");
      const baseCreate = rule.mode === "per_token" && rule.cache_creation !== "" ? rule.cache_creation : (fbSrc?.cache_creation != null ? String(fbSrc.cache_creation) : "");
      const baseOut = rule.mode === "per_token" && rule.output !== "" ? rule.output : (fbSrc?.output != null ? String(fbSrc.output) : "");
      p.draft.pricing_rules[targetKey] = {
        mode: "per_tier",
        currency: prevCurrency,
        multiplier: rule.multiplier || "1",
        base: {input: baseIn, input_cached: baseCached, cache_creation: baseCreate, output: baseOut},
        context_tiers: [],
        service_tiers: [],
      };
      return;
    }
    if (nextMode === "tiered_expr") {
      const existingExpr = baseRule?.mode === "tiered_expr" ? cloneRuleForDraft(baseRule) : null;
      p.draft.pricing_rules[targetKey] = existingExpr || {
        mode: "tiered_expr",
        currency: prevCurrency,
        expr: "",
        tiers: [],
        default_expr: "",
      };
      return;
    }
    p.draft.pricing_rules[targetKey] = {
      mode: nextMode === "per_request" ? "per_request" : "per_turn",
      currency: prevCurrency,
      price: rule.price != null ? String(rule.price) : "",
      multiplier: rule.multiplier || "1",
    };
  }

  function ensureDraftSchedule(p, item, model) {
    const {key, schedule} = lookupDraftSchedule(p, item, model);
    if (schedule) return {key, schedule};
    const targetKey = ruleKeyFor(item.id, model);
    const created = {
      enabled: true,
      timezone: p.data?.pricing_timezone || "Asia/Shanghai",
      periods: [{
        id: "period_1",
        name: "夜间折扣",
        days: [1, 2, 3, 4, 5, 6, 7],
        all_day: false,
        start: "23:00",
        end: "07:00",
        mode: "multiplier",
        multiplier: "0.5",
        override: cloneRuleForDraft({mode: "per_token", currency: "USD"}),
      }],
    };
    p.draft.pricing_schedules[targetKey] = created;
    return {key: targetKey, schedule: created};
  }

  function fillPresetIntoDraft(p, item, model) {
    if (!item?.preset_match) return;
    const preset = item.preset_match;
    const targetKey = ruleKeyFor(item.id, model);
    const existing = p.draft.pricing_rules[targetKey];
    p.draft.pricing_rules[targetKey] = {
      mode: "per_token",
      currency: "USD",
      input: String(preset.input_per_million ?? ""),
      input_cached: String(preset.cached_input_per_million ?? ""),
      cache_creation: preset.cache_creation_per_million == null ? "" : String(preset.cache_creation_per_million),
      output: String(preset.output_per_million ?? ""),
      multiplier: existing?.multiplier || "1",
      free: Boolean(preset.input_per_million === 0 && preset.output_per_million === 0),
    };
  }

  function clearProviderOverride(p, item, model) {
    const exact = model ? `${item.id}|${model}` : "";
    if (exact) {
      delete p.draft.pricing_rules[exact];
      delete p.draft.pricing_schedules[exact];
    }
    if (!model || model === item.model) {
      delete p.draft.pricing_rules[item.id];
      delete p.draft.pricing_schedules[item.id];
    }
  }

  function discardProviderDraft(p, item) {
    const pid = item.id;
    const tz = p.data?.pricing_timezone || "Asia/Shanghai";
    for (const k of Object.keys(p.draft.pricing_rules || {})) {
      if (k === pid || k.startsWith(pid + "|")) delete p.draft.pricing_rules[k];
    }
    for (const [k, v] of Object.entries(p.data?.pricing_rules || {})) {
      if (k === pid || k.startsWith(pid + "|")) p.draft.pricing_rules[k] = cloneRuleForDraft(v);
    }
    for (const k of Object.keys(p.draft.pricing_schedules || {})) {
      if (k === pid || k.startsWith(pid + "|")) delete p.draft.pricing_schedules[k];
    }
    for (const [k, v] of Object.entries(p.data?.pricing_schedules || {})) {
      if (k === pid || k.startsWith(pid + "|")) p.draft.pricing_schedules[k] = cloneScheduleForDraft(v, tz);
    }
  }

  function triggerPulse(ctx, pid) {
    const {p, render, root, isCurrent, ticket} = ctx;
    const t = typeof ticket === "function" ? ticket() : null;
    if (p.pulseTimer) {
      clearTimeout(p.pulseTimer);
      p.pulseTimer = null;
    }
    p.pulseProvider = pid;
    render();
    try {
      const container = root();
      const cards = container?.querySelectorAll ? [...container.querySelectorAll("[data-pw-card]")] : [];
      const card = cards.find(el => el.dataset?.pwCard === pid) || null;
      card?.scrollIntoView?.({behavior: "smooth", block: "nearest"});
    } catch {}
    p.pulseTimer = setTimeout(() => {
      p.pulseProvider = "";
      p.pulseTimer = null;
      if (isCurrent && !isCurrent(t)) return;
      try {
        const container = root();
        const cards = container?.querySelectorAll ? [...container.querySelectorAll("[data-pw-card]")] : [];
        for (const el of cards) {
          if (el.dataset?.pwCard === pid || el.classList?.contains?.("is-pulse")) {
            el.classList?.remove?.("is-pulse");
          }
        }
      } catch {}
    }, 1600);
  }

  /* ---------- Rendering: Hero & Global Controls ---------- */

  function renderHeroAndControls(p) {
    const data = p.data;
    const draft = p.draft;
    const locked = p.loading || isBusy(p);
    const dirty = isDirty(p);
    const changesCount = dirty ? Object.keys(computeChanges(p)).length : 0;
    const fx = data.exchange_rate;
    const effectiveRate = Number(fx.effective_usd_cny) || 7.2;
    const fxSourceLabel = fx.mode === "manual"
      ? "手动指定"
      : fx.effective_source === "static_fallback"
        ? "离线参考"
        : `自动 · ${fx.effective_source}`;
    const fxUpdated = fx.fetched_at ? ` · ${formatShortTime(fx.fetched_at)}` : "";
    const manualMode = draft.exchange_rate.mode === "manual";
    const presetCount = (data.presets || []).length;

    return `<section class="pw-hero">
      <div class="pw-hero-head">
        <div class="pw-hero-copy">
          <span class="pw-eyebrow">模型计价与汇率</span>
          <h3>计价工作台</h3>
          <p class="pw-subtitle">统一管理显示币种、实时汇率、多源价格目录、五种计价模式与分时倍率；保存后立即重算费用估算。</p>
        </div>
        <div class="pw-hero-actions">
          <button type="button" class="button small" data-pricing-refresh ${locked ? "disabled" : ""}>刷新配置</button>
          ${dirty ? `<button type="button" class="button small" data-pricing-discard ${locked ? "disabled" : ""}>放弃草稿</button>` : ""}
          <button type="button" class="button small primary" data-pricing-save ${locked || p.stale || !dirty ? "disabled" : ""}>${p.busy ? "保存中…" : dirty ? `保存更改 (${changesCount})` : "保存更改"}</button>
        </div>
      </div>
      ${p.stale ? '<div class="pw-alert is-warn" role="alert">配置已在其他位置更新，草稿已保留；请先「刷新配置」后核对再保存。</div>' : ""}
      ${p.notice ? `<div class="pw-notice ${p.bad ? "is-error" : "is-ok"}" role="status" aria-live="polite">${esc(p.notice)}</div>` : ""}
      <div class="pw-settings-grid">
        <div class="pw-ctrl-card">
          <div class="pw-ctrl-head">
            <span class="pw-ctrl-title">显示币种</span>
            <span class="pw-pill is-accent">${draft.display_currency === "USD" ? "US$ 美元" : "¥ 人民币"}</span>
          </div>
          <div class="pw-seg" role="group" aria-label="显示币种">
            <button type="button" data-pricing-currency="CNY" aria-pressed="${draft.display_currency === "CNY"}" class="${draft.display_currency === "CNY" ? "on" : ""}" ${locked ? "disabled" : ""}>¥ 人民币 (CNY)</button>
            <button type="button" data-pricing-currency="USD" aria-pressed="${draft.display_currency === "USD"}" class="${draft.display_currency === "USD" ? "on" : ""}" ${locked ? "disabled" : ""}>$ 美元 (USD)</button>
          </div>
          <p class="pw-ctrl-help">治理维护总览、调用明细与主动联系共用同一显示币种。</p>
        </div>

        <div class="pw-ctrl-card">
          <div class="pw-ctrl-head">
            <span class="pw-ctrl-title">USD → CNY 汇率</span>
            <span class="pw-rate-badge${fx.stale && !manualMode ? " is-warn" : ""}" title="${esc(fxSourceLabel + fxUpdated)}">1 USD = ¥${effectiveRate.toFixed(4)}</span>
          </div>
          <div class="pw-fx-row">
            <div class="pw-seg" role="group" aria-label="汇率模式">
              <button type="button" data-pricing-rate-mode="auto" aria-pressed="${!manualMode}" class="${!manualMode ? "on" : ""}" ${locked ? "disabled" : ""}>自动实时汇率</button>
              <button type="button" data-pricing-rate-mode="manual" aria-pressed="${manualMode}" class="${manualMode ? "on" : ""}" ${locked ? "disabled" : ""}>手动指定汇率</button>
            </div>
            <button type="button" class="button small" data-pricing-sync-rates ${locked ? "disabled" : ""}>${p.syncingRates ? "刷新中…" : "立即刷新汇率"}</button>
          </div>
          ${manualMode ? `<div class="pw-fx-manual">
            <span class="pw-inline-cap">手动汇率 (1 USD =)</span>
            <span class="pw-unit-input"><input type="number" min="1" max="100" step="0.0001" data-pricing-manual-rate value="${esc(draft.exchange_rate.manual_usd_cny)}" placeholder="${effectiveRate.toFixed(4)}" ${locked ? "disabled" : ""}><i>CNY</i></span>
          </div>` : ""}
          <p class="pw-ctrl-help">${esc(fxSourceLabel)}${esc(fxUpdated)}${fx.stale && !manualMode ? " · 已超过刷新周期，可手动刷新" : ""}</p>
        </div>

        <div class="pw-ctrl-card">
          <div class="pw-switch-head">
            <span class="pw-ctrl-title">启用内置主流模型参考价</span>
            <label class="model-switch"><input type="checkbox" data-pricing-builtin-presets ${draft.use_builtin_presets ? "checked" : ""} ${locked ? "disabled" : ""} aria-label="启用内置主流模型参考价"><i></i></label>
          </div>
          <p class="pw-ctrl-help pw-switch-help">当模型未单独配置且在线价格目录未命中时，按内置主流模型公开参考价估算费用。</p>
          <div class="pw-ctrl-foot">
            <button type="button" class="pw-link-btn" data-pricing-toggle-presets>${p.presetsOpen ? "收起" : "查看"}内置参考价表 (${presetCount})</button>
          </div>
        </div>
      </div>
    </section>`;
  }

  /* ---------- Rendering: Price Sources Bar ---------- */

  function sourceStatusMeta(src) {
    if (!src.enabled || src.status === "disabled") return {label: "未启用", cls: "is-muted"};
    if (src.status === "ok") return {label: "正常", cls: "is-ok"};
    if (src.status === "error") return {label: "失败", cls: "is-risk"};
    return {label: "待同步", cls: "is-warn"};
  }

  function renderSourcesBar(p) {
    const data = p.data;
    const locked = p.loading || isBusy(p);
    const sources = data.sources || [];
    const chips = sources.map(src => {
      const meta = sourceStatusMeta(src);
      const busyThis = p.togglingSource === src.source_id;
      const tipParts = [
        `${src.label} (${src.source_id})`,
        `状态：${meta.label}`,
        `模型数：${formatTokensFull(src.model_count)}`,
      ];
      if (src.kind === "newapi") {
        const smeta = src.site_meta && typeof src.site_meta === "object" ? src.site_meta : {};
        const curStr = src.currency === "CNY" ? "CNY (¥)" : "USD ($)";
        const qdt = smeta.quota_display_type || "";
        tipParts.push(`结算币种：${curStr}${src.currency_override && src.currency_override !== "auto" ? "（手动覆盖）" : qdt === "CUSTOM" ? `（站内自定义单位 ${smeta.custom_currency_symbol || ""}）` : ""}`);
        if (smeta.recharge_price != null) tipParts.push(`站内参考充值价：1 单位 = ¥${formatRateNumber(smeta.recharge_price)}`);
      }
      if (src.last_ok_at) tipParts.push(`最近成功：${formatShortTime(src.last_ok_at)}`);
      if (src.last_sync_at && src.last_sync_at !== src.last_ok_at) tipParts.push(`最近尝试：${formatShortTime(src.last_sync_at)}`);
      if (src.error) tipParts.push(`错误：${src.error}`);
      if (src.needs_key) tipParts.push("提示：该网关返回 0 条模型，可能需要鉴权密钥");
      tipParts.push("点击切换启用/停用");
      const curBadgeHtml = src.kind === "newapi" && src.currency
        ? `<span class="pw-source-cur">${esc(src.currency === "CNY" ? "¥" : "$")}</span>`
        : "";
      return `<button type="button" class="pw-source-chip ${meta.cls}${src.enabled ? " is-enabled" : ""}" data-pricing-source-toggle="${esc(src.source_id)}" title="${esc(tipParts.join("\n"))}" ${locked ? "disabled" : ""}>
        <span class="pw-source-dot" aria-hidden="true"></span>
        <span class="pw-source-status">${esc(busyThis ? "切换中" : meta.label)}</span>
        <span class="pw-source-label">${esc(src.label)}</span>
        ${curBadgeHtml}
        <span class="pw-source-count">${formatTokensFull(src.model_count)}</span>
      </button>`;
    }).join("");

    const updatedText = data.catalog_updated_at
      ? `最近目录更新：${formatShortTime(data.catalog_updated_at)}`
      : "尚未拉取在线价格目录";

    return `<section class="pw-sources-bar" aria-label="价格源同步">
      <div class="pw-sources-main">
        <div class="pw-sources-title-row">
          <strong>价格源</strong>
          <span class="pw-sources-hint">点击芯片切换启用状态，悬停查看错误与同步详情</span>
        </div>
        <div class="pw-sources-chips">${chips || '<span class="pw-muted">暂无价格源</span>'}</div>
      </div>
      <div class="pw-sources-actions">
        <label class="pw-inline-check"><input type="checkbox" data-pricing-auto-daily ${p.draft.price_sync.auto_daily ? "checked" : ""} ${locked ? "disabled" : ""}><span>每日自动同步</span></label>
        <button type="button" class="button small primary" data-pricing-sync-catalog ${locked ? "disabled" : ""}>${p.syncingCatalog ? "正在拉取…" : "拉取最新价格"}</button>
      </div>
      <div class="pw-sources-meta">
        <span>${esc(updatedText)}</span>
        ${p.syncNotice ? `<span class="pw-sync-feedback" role="status">${esc(p.syncNotice)}</span>` : ""}
      </div>
    </section>`;
  }

  /* ---------- Rendering: Unpriced Usage Alert ---------- */

  function renderUnpricedAlert(p) {
    const unpriced = p.data?.unpriced || [];
    if (!unpriced.length) return "";
    const totalTokens = unpriced.reduce((sum, item) => sum + (Number(item.total_tokens) || 0), 0);
    const rows = unpriced.map(item => {
      const topModel = item.models?.[0]?.model || "";
      const modelNote = item.model_count > 1
        ? `${topModel || "多模型"} 等 ${item.model_count} 个模型`
        : (topModel || "未记录模型名");
      return `<button type="button" class="pw-unpriced-row" data-pricing-jump-provider="${esc(item.provider_id)}" data-pricing-jump-cluster="${esc(item.cluster_id || item.provider_id)}" data-pricing-jump-deleted="${item.deleted ? "1" : "0"}" data-pricing-jump-model="${esc(topModel)}">
        <span class="pw-unpriced-left">
          <strong>${esc(item.provider_id)}</strong>
          <span class="pw-unpriced-model">${esc(modelNote)}</span>
          ${item.deleted ? '<span class="pw-badge is-warn">已删除供应商残留</span>' : ""}
        </span>
        <span class="pw-unpriced-right">
          <span class="pw-num">${formatTokensFull(item.total_tokens)} token</span>
          <span class="pw-jump-cue">点击跳转 ▸</span>
        </span>
      </button>`;
    }).join("");

    return `<section class="pw-unpriced-card" role="region" aria-label="未定价告警">
      <div class="pw-unpriced-head">
        <div>
          <strong>未定价告警</strong>
          <span>${unpriced.length} 个提供方产生 ${formatTokensFull(totalTokens)} token 用量但尚未命中单价，点击条目可快速定位配置</span>
        </div>
      </div>
      <div class="pw-unpriced-list">${rows}</div>
    </section>`;
  }

  /* ---------- Rendering: Nested 5-Mode Rule Editor (reused for card & schedule override) ---------- */

  function renderNestedOverrideEditor(pid, idx, overrideRule, locked) {
    const rule = overrideRule || cloneRuleForDraft({mode: "per_token", currency: "USD"});
    const mode = rule.mode || "per_token";
    const cur = rule.currency === "CNY" ? "CNY" : "USD";
    const sym = currencySymbol(cur);
    let body = "";
    if (mode === "per_token") {
      body = `<div class="pw-rate-grid pw-rate-grid--compact">
        ${[["input", "输入"], ["input_cached", "缓存命中"], ["output", "输出"], ["cache_creation", "缓存写入"]].map(([f, lbl]) => `
          <div class="pw-field">
            <span class="pw-label">${lbl} (${sym}/1M)</span>
            <input type="number" min="0" max="1000000" step="0.0001" data-pricing-sched-ovr-field="${f}" data-pid="${esc(pid)}" data-idx="${idx}" value="${esc(rule[f] ?? "")}" placeholder="留空继承" ${locked ? "disabled" : ""}>
          </div>`).join("")}
      </div>`;
    } else if (mode === "per_tier") {
      const base = rule.base || {};
      body = `<div class="pw-rate-grid pw-rate-grid--compact">
        ${[["input", "基础输入"], ["input_cached", "基础缓存命中"], ["output", "基础输出"], ["cache_creation", "基础缓存写入"]].map(([f, lbl]) => `
          <div class="pw-field">
            <span class="pw-label">${lbl} (${sym}/1M)</span>
            <input type="number" min="0" max="1000000" step="0.0001" data-pricing-sched-ovr-base="${f}" data-pid="${esc(pid)}" data-idx="${idx}" value="${esc(base[f] ?? "")}" placeholder="必填" ${locked ? "disabled" : ""}>
          </div>`).join("")}
      </div>`;
    } else if (mode === "tiered_expr") {
      body = `<div class="pw-field">
        <span class="pw-label">覆盖表达式 (${cur} 微单位口径)</span>
        <textarea rows="2" class="pw-mono-input" spellcheck="false" data-pricing-sched-ovr-expr data-pid="${esc(pid)}" data-idx="${idx}" placeholder="例如：tier(&quot;standard&quot;, p * 0.75 + cr * 0.075 + c * 3.75)" ${locked ? "disabled" : ""}>${esc(rule.expr || "")}</textarea>
      </div>`;
    } else {
      body = `<div class="pw-field">
        <span class="pw-label">${mode === "per_request" ? "每次请求单价" : "每次调用单价"} (${sym})</span>
        <input type="number" min="0" max="1000000" step="0.0001" data-pricing-sched-ovr-price data-pid="${esc(pid)}" data-idx="${idx}" value="${esc(rule.price ?? "")}" placeholder="0.01" ${locked ? "disabled" : ""}>
      </div>`;
    }
    return `<div class="pw-nested-override">
      <div class="pw-nested-row">
        <div class="pw-field">
          <span class="pw-label">替换计价模式</span>
          <select data-pricing-sched-ovr-mode data-pid="${esc(pid)}" data-idx="${idx}" ${locked ? "disabled" : ""}>
            ${MODE_OPTIONS.map(([m, lbl]) => `<option value="${m}" ${mode === m ? "selected" : ""}>${lbl}</option>`).join("")}
          </select>
        </div>
        <div class="pw-field">
          <span class="pw-label">单价币种</span>
          <select data-pricing-sched-ovr-currency data-pid="${esc(pid)}" data-idx="${idx}" ${locked ? "disabled" : ""}>
            <option value="USD" ${cur === "USD" ? "selected" : ""}>USD ($)</option>
            <option value="CNY" ${cur === "CNY" ? "selected" : ""}>CNY (¥)</option>
          </select>
        </div>
      </div>
      ${body}
    </div>`;
  }

  /* ---------- Rendering: Time-of-Day Schedule Section ---------- */

  function renderScheduleSection(p, item, model, locked) {
    const pid = item.id;
    const {schedule} = lookupDraftSchedule(p, item, model);
    const enabled = Boolean(schedule?.enabled);
    const tzList = p.data?.available_timezones || DEFAULT_TIMEZONES;
    const tz = schedule?.timezone || p.data?.pricing_timezone || "Asia/Shanghai";
    const isPresetTz = tzList.includes(tz);

    const periodsHtml = schedule ? (schedule.periods || []).map((period, idx) => {
      const days = new Set((period.days || []).map(Number));
      const allDay = Boolean(period.all_day);
      const crossMidnight = !allDay && period.start && period.end && period.start > period.end;
      const isOverride = period.mode === "override";
      return `<div class="pw-period-card" data-pw-period-idx="${idx}">
        <div class="pw-period-top">
          <input type="text" class="pw-period-name" data-pricing-sched-name data-pid="${esc(pid)}" data-idx="${idx}" value="${esc(period.name || "")}" placeholder="时段名称（如：夜间优惠）" maxlength="64" ${locked ? "disabled" : ""}>
          <button type="button" class="button small" data-pricing-sched-del="${esc(pid)}" data-idx="${idx}" ${locked ? "disabled" : ""}>删除时段</button>
        </div>
        <div class="pw-period-row">
          <span class="pw-label">生效星期</span>
          <div class="pw-weekday-pills" role="group" aria-label="生效星期">
            ${WEEKDAYS.map(([d, label]) => `<button type="button" class="pw-day-pill${days.has(d) ? " on" : ""}" data-pricing-sched-day="${esc(pid)}" data-idx="${idx}" data-day="${d}" aria-pressed="${days.has(d)}" ${locked ? "disabled" : ""}>${label}</button>`).join("")}
          </div>
        </div>
        <div class="pw-period-row pw-period-time">
          <label class="pw-inline-check"><input type="checkbox" data-pricing-sched-allday data-pid="${esc(pid)}" data-idx="${idx}" ${allDay ? "checked" : ""} ${locked ? "disabled" : ""}><span>全天</span></label>
          ${allDay ? '<span class="pw-muted">00:00 – 24:00 全天生效</span>' : `
            <div class="pw-time-range">
              <span class="pw-label">开始</span>
              <input type="time" data-pricing-sched-start data-pid="${esc(pid)}" data-idx="${idx}" value="${esc(period.start || "00:00")}" ${locked ? "disabled" : ""}>
              <span class="pw-label">结束</span>
              <input type="time" data-pricing-sched-end data-pid="${esc(pid)}" data-idx="${idx}" value="${esc(period.end === "24:00" ? "23:59" : (period.end || "08:00"))}" ${locked ? "disabled" : ""}>
            </div>
            ${crossMidnight ? `<span class="pw-badge is-accent">跨午夜 · 次日 00:00–${esc(period.end)} 归属前一日星期</span>` : ""}
          `}
        </div>
        <div class="pw-period-row">
          <div class="pw-seg" role="group" aria-label="时段计价调整方式">
            <button type="button" data-pricing-sched-mode="${esc(pid)}" data-idx="${idx}" data-mode="multiplier" aria-pressed="${!isOverride}" class="${!isOverride ? "on" : ""}" ${locked ? "disabled" : ""}>基础价倍率</button>
            <button type="button" data-pricing-sched-mode="${esc(pid)}" data-idx="${idx}" data-mode="override" aria-pressed="${isOverride}" class="${isOverride ? "on" : ""}" ${locked ? "disabled" : ""}>替换定价规则</button>
          </div>
          ${!isOverride ? `<div class="pw-inline-mult">
            <span class="pw-unit-input"><input type="number" min="0" max="100" step="0.01" data-pricing-sched-mult data-pid="${esc(pid)}" data-idx="${idx}" value="${esc(period.multiplier ?? "0.5")}" ${locked ? "disabled" : ""}><i>×</i></span>
            <span class="pw-muted">填 0 表示该时段免费</span>
          </div>` : ""}
        </div>
        ${isOverride ? renderNestedOverrideEditor(pid, idx, period.override, locked) : ""}
      </div>`;
    }).join("") : "";

    return `<div class="pw-schedule-section">
      <div class="pw-switch-head">
        <div>
          <strong class="pw-section-title">启用分时定价</strong>
          <p class="pw-ctrl-help">按 IANA 当地时区对特定星期与时段叠加倍率或替换定价规则；支持跨午夜区间。</p>
        </div>
        <label class="model-switch"><input type="checkbox" data-pricing-sched-enabled="${esc(pid)}" ${enabled ? "checked" : ""} ${locked ? "disabled" : ""} aria-label="启用分时定价"><i></i></label>
      </div>
      ${enabled ? `<div class="pw-schedule-body">
        <div class="pw-tz-row">
          <div class="pw-field">
            <span class="pw-label">IANA 时区</span>
            <select data-pricing-sched-tz-select="${esc(pid)}" ${locked ? "disabled" : ""}>
              ${tzList.map(z => `<option value="${esc(z)}" ${tz === z ? "selected" : ""}>${esc(z)}</option>`).join("")}
              <option value="__custom__" ${!isPresetTz ? "selected" : ""}>自定义 IANA 时区…</option>
            </select>
          </div>
          <div class="pw-field">
            <span class="pw-label">自定义时区名</span>
            <input type="text" data-pricing-sched-tz-custom="${esc(pid)}" value="${esc(tz)}" placeholder="如 Asia/Shanghai" maxlength="64" ${locked ? "disabled" : ""}>
          </div>
        </div>
        <div class="pw-period-list">${periodsHtml || '<p class="pw-muted">尚未添加分时时段。</p>'}</div>
        <div class="pw-sched-foot">
          <button type="button" class="button small" data-pricing-sched-add="${esc(pid)}" ${locked ? "disabled" : ""}>+ 添加时段</button>
        </div>
      </div>` : ""}
    </div>`;
  }

  /* ---------- Rendering: Expanded Card Body (All 5 Modes + Catalog Candidates) ---------- */

  function renderCatalogSection(p, item, model, locked) {
    const pid = item.id;
    const cands = p.data?.candidates?.[pid]?.[model] || [];
    const sel = p.data?.selections?.[pid]?.[model] || null;
    const auto = p.data?.auto_selected?.[pid]?.[model] || null;
    const busySel = p.selectingKey === `${pid}|${model}`;
    const open = Boolean(p.openCandidates[pid]);

    let summaryPill = '<span class="pw-muted">未命中在线价格目录</span>';
    let quickAction = "";
    if (sel) {
      summaryPill = `<span class="pw-badge is-accent">已选来源 · ${esc(sel.source_label || sel.source_id)} · ${esc(sel.model_id)}</span>`;
      quickAction = `<button type="button" class="button small" data-pricing-select-reset="${esc(pid)}" data-model-name="${esc(model)}" ${locked || busySel ? "disabled" : ""}>清除选择</button>`;
    } else if (auto) {
      const pct = Math.round((Number(auto.score) || 0) * 100);
      summaryPill = `<span class="pw-badge is-ok">自动匹配 · ${esc(auto.source_label || auto.source_id)} · ${esc(auto.model_id)} (${pct}%)</span>`;
      quickAction = `<button type="button" class="button small" data-pricing-select-candidate="${esc(pid)}" data-model-name="${esc(model)}" data-price-key="${esc(auto.price_key)}" ${locked || busySel ? "disabled" : ""}>锁定此源</button>`;
    } else if (item.preset_match) {
      summaryPill = `<span class="pw-badge is-muted">内置参考 · ${esc(item.preset_match.label || item.preset_match.preset_id)}</span>`;
    }

    const sourcesById = new Map((p.data?.sources || []).map(s => [s.source_id, s]));
    const tableRows = cands.map(c => {
      const isSelected = sel && sel.price_key === c.price_key;
      const pct = Math.round((Number(c.score) || 0) * 100);
      const candCustom = customUnitInfoForSource(sourcesById.get(c.source_id));
      const candViewMode = candCustom ? (p.sourceUnitView?.[candCustom.sourceId] === "native" ? "native" : "fiat") : "fiat";
      const candParts = dualSummaryParts(c.rule, candCustom, candViewMode);
      const candDualAttrs = candParts.dual
        ? ` data-pw-dual-src="${esc(candCustom.sourceId)}" data-pw-fiat="${esc(candParts.fiatText)}" data-pw-native="${esc(candParts.nativeText)}"`
        : "";
      return `<tr class="${isSelected ? "is-selected" : ""}">
        <td>
          <span class="pw-cand-src">${esc(c.source_label || c.source_id)}</span>
          ${c.is_own_gateway ? ' <span class="pw-badge is-ok">本网关</span>' : ""}
        </td>
        <td class="pw-mono">${esc(c.model_id)}</td>
        <td class="pw-num"${candDualAttrs}>${renderDualSummaryInner(c.rule, candCustom, candViewMode)}</td>
        <td class="pw-num">${pct}%</td>
        <td class="pw-cand-op">
          ${isSelected
            ? '<span class="pw-badge is-accent">已选用</span>'
            : `<button type="button" class="button small" data-pricing-select-candidate="${esc(pid)}" data-model-name="${esc(model)}" data-price-key="${esc(c.price_key)}" ${locked || busySel ? "disabled" : ""}>选用</button>`}
        </td>
      </tr>`;
    }).join("");

    return `<div class="pw-catalog-box">
      <div class="pw-catalog-head">
        <div class="pw-catalog-status">${summaryPill}</div>
        <div class="pw-catalog-actions">
          ${quickAction}
          ${cands.length ? `<button type="button" class="button small" data-pricing-toggle-cands="${esc(pid)}">${open ? "收起候选对比" : `候选价格对比 (${cands.length})`}</button>` : ""}
        </div>
      </div>
      ${open && cands.length ? `<div class="pw-table-wrap">
        <table class="pw-cand-table">
          <thead><tr><th>来源</th><th>模型标识</th><th>规则摘要</th><th>匹配度</th><th>操作</th></tr></thead>
          <tbody>${tableRows}</tbody>
        </table>
      </div>` : ""}
    </div>`;
  }

  function renderModeEditor(p, item, model, activeRule, ruleKey, locked) {
    const pid = item.id;
    const mode = activeRule.mode || "per_token";
    const cur = activeRule.currency === "CNY" ? "CNY" : "USD";
    const sym = currencySymbol(cur);

    if (mode === "per_token") {
      const ph = inheritedPlaceholders(p.data, item, model, cur);
      const disabledRate = locked || Boolean(activeRule.free);
      const hasCustomRuleMult = activeRule.multiplier !== "" && Number(activeRule.multiplier) !== 1;
      return `<div class="pw-mode-body">
        <div class="pw-rate-grid">
          <div class="pw-field">
            <span class="pw-label">输入 (${sym}/1M)</span>
            <input type="number" min="0" max="1000000" step="0.0001" data-pricing-rate-field="input" data-pid="${esc(pid)}" value="${esc(activeRule.input ?? "")}" placeholder="${esc(ph.input || "例如 3.00")}" ${disabledRate ? "disabled" : ""}>
          </div>
          <div class="pw-field">
            <span class="pw-label">缓存命中 (${sym}/1M)</span>
            <input type="number" min="0" max="1000000" step="0.0001" data-pricing-rate-field="input_cached" data-pid="${esc(pid)}" value="${esc(activeRule.input_cached ?? "")}" placeholder="${esc(ph.input_cached || "例如 0.30")}" ${disabledRate ? "disabled" : ""}>
          </div>
          <div class="pw-field">
            <span class="pw-label">输出 (${sym}/1M)</span>
            <input type="number" min="0" max="1000000" step="0.0001" data-pricing-rate-field="output" data-pid="${esc(pid)}" value="${esc(activeRule.output ?? "")}" placeholder="${esc(ph.output || "例如 15.00")}" ${disabledRate ? "disabled" : ""}>
          </div>
          <div class="pw-field">
            <span class="pw-label">缓存写入 (${sym}/1M)</span>
            <input type="number" min="0" max="1000000" step="0.0001" data-pricing-rate-field="cache_creation" data-pid="${esc(pid)}" value="${esc(activeRule.cache_creation ?? "")}" placeholder="${esc(ph.cache_creation || "默认同输入价")}" ${disabledRate ? "disabled" : ""}>
          </div>
        </div>
        ${hasCustomRuleMult ? `<div class="pw-inline-row">
          <span class="pw-label">单条规则倍率</span>
          <span class="pw-unit-input"><input type="number" min="0" max="100" step="0.01" data-pricing-rate-field="multiplier" data-pid="${esc(pid)}" value="${esc(activeRule.multiplier)}" ${locked ? "disabled" : ""}><i>×</i></span>
          <span class="pw-muted">建议优先使用供应商聚类倍率；此处保留兼容。</span>
        </div>` : ""}
        <p class="pw-ctrl-help">留空自动继承目录或内置参考价；填 <code>0</code> 表示显式免费；缓存写入留空默认按输入单价计。</p>
      </div>`;
    }

    if (mode === "per_tier") {
      const base = activeRule.base || {};
      const ctxRows = (activeRule.context_tiers || []).map((t, idx) => `<div class="pw-tier-row">
        <div class="pw-field"><span class="pw-label">上下文阈值 &gt; (Token)</span><input type="number" min="1" max="1000000000" step="1000" data-pricing-ctx-field="threshold_tokens" data-pid="${esc(pid)}" data-idx="${idx}" value="${esc(t.threshold_tokens)}" placeholder="如 128000" ${locked ? "disabled" : ""}></div>
        <div class="pw-field"><span class="pw-label">输入</span><input type="number" min="0" max="1000000" step="0.0001" data-pricing-ctx-field="input" data-pid="${esc(pid)}" data-idx="${idx}" value="${esc(t.input)}" placeholder="沿用基础" ${locked ? "disabled" : ""}></div>
        <div class="pw-field"><span class="pw-label">缓存命中</span><input type="number" min="0" max="1000000" step="0.0001" data-pricing-ctx-field="input_cached" data-pid="${esc(pid)}" data-idx="${idx}" value="${esc(t.input_cached)}" placeholder="沿用基础" ${locked ? "disabled" : ""}></div>
        <div class="pw-field"><span class="pw-label">输出</span><input type="number" min="0" max="1000000" step="0.0001" data-pricing-ctx-field="output" data-pid="${esc(pid)}" data-idx="${idx}" value="${esc(t.output)}" placeholder="沿用基础" ${locked ? "disabled" : ""}></div>
        <div class="pw-field"><span class="pw-label">缓存写入</span><input type="number" min="0" max="1000000" step="0.0001" data-pricing-ctx-field="cache_creation" data-pid="${esc(pid)}" data-idx="${idx}" value="${esc(t.cache_creation)}" placeholder="沿用基础" ${locked ? "disabled" : ""}></div>
        <button type="button" class="button small" data-pricing-del-ctx-tier="${esc(pid)}" data-idx="${idx}" ${locked ? "disabled" : ""}>删除</button>
      </div>`).join("");

      const svcRows = (activeRule.service_tiers || []).map((t, idx) => `<div class="pw-tier-row pw-tier-row--svc">
        <div class="pw-field"><span class="pw-label">服务档位 match</span><input type="text" data-pricing-svc-field="match" data-pid="${esc(pid)}" data-idx="${idx}" value="${esc(t.match)}" placeholder="如 priority / flex" maxlength="64" ${locked ? "disabled" : ""}></div>
        <div class="pw-field"><span class="pw-label">输入倍率 ×</span><input type="number" min="0" max="100" step="0.05" data-pricing-svc-field="input_multiplier" data-pid="${esc(pid)}" data-idx="${idx}" value="${esc(t.input_multiplier)}" placeholder="如 2" ${locked ? "disabled" : ""}></div>
        <div class="pw-field"><span class="pw-label">缓存读倍率 ×</span><input type="number" min="0" max="100" step="0.05" data-pricing-svc-field="input_cached_multiplier" data-pid="${esc(pid)}" data-idx="${idx}" value="${esc(t.input_cached_multiplier)}" placeholder="可选" ${locked ? "disabled" : ""}></div>
        <div class="pw-field"><span class="pw-label">输出倍率 ×</span><input type="number" min="0" max="100" step="0.05" data-pricing-svc-field="output_multiplier" data-pid="${esc(pid)}" data-idx="${idx}" value="${esc(t.output_multiplier)}" placeholder="如 2" ${locked ? "disabled" : ""}></div>
        <div class="pw-field"><span class="pw-label">绝对输入 (${sym})</span><input type="number" min="0" max="1000000" step="0.0001" data-pricing-svc-field="input" data-pid="${esc(pid)}" data-idx="${idx}" value="${esc(t.input)}" placeholder="可选" ${locked ? "disabled" : ""}></div>
        <div class="pw-field"><span class="pw-label">绝对输出 (${sym})</span><input type="number" min="0" max="1000000" step="0.0001" data-pricing-svc-field="output" data-pid="${esc(pid)}" data-idx="${idx}" value="${esc(t.output)}" placeholder="可选" ${locked ? "disabled" : ""}></div>
        <button type="button" class="button small" data-pricing-del-svc-tier="${esc(pid)}" data-idx="${idx}" ${locked ? "disabled" : ""}>删除</button>
      </div>`).join("");

      return `<div class="pw-mode-body">
        <div class="pw-subblock">
          <div class="pw-subblock-head"><strong>基础单价 (${sym}/1M)</strong><span class="pw-muted">未命中高阶上下文时使用</span></div>
          <div class="pw-rate-grid">
            ${[["input", "基础输入 *"], ["input_cached", "基础缓存命中"], ["output", "基础输出 *"], ["cache_creation", "基础缓存写入"]].map(([f, lbl]) => `
              <div class="pw-field">
                <span class="pw-label">${lbl}</span>
                <input type="number" min="0" max="1000000" step="0.0001" data-pricing-tier-base="${f}" data-pid="${esc(pid)}" value="${esc(base[f] ?? "")}" placeholder="${f === "input" || f === "output" ? "必填" : "可选"}" ${locked ? "disabled" : ""}>
              </div>`).join("")}
          </div>
        </div>
        <div class="pw-subblock">
          <div class="pw-subblock-head">
            <strong>上下文长度阶梯</strong>
            <button type="button" class="button small" data-pricing-add-ctx-tier="${esc(pid)}" ${locked ? "disabled" : ""}>+ 添加阶梯</button>
          </div>
          ${ctxRows || '<p class="pw-muted">暂无上下文阶梯，例如输入超过 200,000 Token 时切换更高单价。</p>'}
        </div>
        <div class="pw-subblock">
          <div class="pw-subblock-head">
            <strong>服务档位 (Service Tier)</strong>
            <button type="button" class="button small" data-pricing-add-svc-tier="${esc(pid)}" ${locked ? "disabled" : ""}>+ 添加服务档位</button>
          </div>
          ${svcRows || '<p class="pw-muted">暂无服务档位，可按响应中的 service_tier（如 priority / flex）配置倍率或绝对单价。</p>'}
        </div>
      </div>`;
    }

    if (mode === "tiered_expr") {
      const exprResult = p.exprResults[ruleKey] || null;
      const validating = p.validatingKey === ruleKey;
      const tiers = Array.isArray(activeRule.tiers) ? activeRule.tiers : [];
      const tierRows = tiers.map((t, idx) => `<div class="pw-expr-tier-row">
        <input type="text" data-pricing-expr-tier="name" data-pid="${esc(pid)}" data-idx="${idx}" value="${esc(t.name)}" placeholder="档位名" ${locked ? "disabled" : ""}>
        <input type="text" class="pw-mono-input" spellcheck="false" data-pricing-expr-tier="condition" data-pid="${esc(pid)}" data-idx="${idx}" value="${esc(t.condition)}" placeholder="条件（如 len > 200000）" ${locked ? "disabled" : ""}>
        <input type="text" class="pw-mono-input" spellcheck="false" data-pricing-expr-tier="expr" data-pid="${esc(pid)}" data-idx="${idx}" value="${esc(t.expr)}" placeholder="计价表达式" ${locked ? "disabled" : ""}>
        <button type="button" class="button small" data-pricing-del-expr-tier="${esc(pid)}" data-idx="${idx}" ${locked ? "disabled" : ""}>删除</button>
      </div>`).join("");

      let previewHtml = "";
      if (exprResult) {
        if (exprResult.valid) {
          const sampleRows = (exprResult.samples || []).map(s => `<tr>
            <td>${esc(s.label)}</td>
            <td class="pw-num">$${Number(s.cost_usd || 0).toFixed(6)}</td>
            <td class="pw-num">¥${Number(s.cost_cny || 0).toFixed(6)}</td>
            <td><span class="pw-badge is-muted">${esc(s.matched_tier || "expr")}</span></td>
          </tr>`).join("");
          previewHtml = `<div class="pw-expr-preview is-ok">
            <div class="pw-expr-preview-head">
              <strong>表达式校验通过</strong>
              <span>引用变量：${esc((exprResult.used_variables || []).join(", ") || "无")} · 汇率 1 USD = ¥${Number(exprResult.usd_cny_rate || 7.2).toFixed(4)}</span>
            </div>
            ${sampleRows ? `<div class="pw-table-wrap"><table class="pw-cand-table"><thead><tr><th>试算样本</th><th>美元 (USD)</th><th>人民币 (CNY)</th><th>命中档位</th></tr></thead><tbody>${sampleRows}</tbody></table></div>` : ""}
          </div>`;
        } else {
          previewHtml = `<div class="pw-expr-preview is-error" role="alert"><strong>校验未通过：</strong>${esc(exprResult.error || "表达式语法错误")}</div>`;
        }
      }

      return `<div class="pw-mode-body">
        <div class="pw-var-legend" aria-label="可用表达式变量">
          <span class="pw-label">变量说明：</span>
          ${EXPR_VARIABLES.map(([v, desc]) => `<span class="pw-var-chip"><code>${v}</code>${desc}</span>`).join("")}
        </div>
        <div class="pw-field">
          <span class="pw-label">计价表达式（兼容 New API <code>billing_expr</code> / <code>tier()</code> / <code>v1:</code> 语法，按所选 ${cur} 微单位口径结算）</span>
          <textarea rows="3" class="pw-mono-input" spellcheck="false" data-pricing-expr-field="expr" data-pid="${esc(pid)}" placeholder="例如：len &lt;= 200000 ? tier(&quot;standard&quot;, p * 2 + cr * 0.2 + c * 12) : tier(&quot;long_context&quot;, p * 4 + cr * 0.4 + c * 18)" ${locked ? "disabled" : ""}>${esc(activeRule.expr || "")}</textarea>
        </div>
        <div class="pw-subblock">
          <div class="pw-subblock-head">
            <strong>条件分档表达式（可选）</strong>
            <button type="button" class="button small" data-pricing-add-expr-tier="${esc(pid)}" ${locked ? "disabled" : ""}>+ 添加条件档位</button>
          </div>
          ${tierRows}
          ${tiers.length ? `<div class="pw-field">
            <span class="pw-label">兜底默认表达式 (default_expr)</span>
            <input type="text" class="pw-mono-input" spellcheck="false" data-pricing-expr-field="default_expr" data-pid="${esc(pid)}" value="${esc(activeRule.default_expr || "")}" placeholder="所有档位未命中时使用" ${locked ? "disabled" : ""}>
          </div>` : ""}
        </div>
        <div class="pw-expr-actions">
          <button type="button" class="button small primary" data-pricing-expr-validate="${esc(pid)}" ${locked || validating ? "disabled" : ""}>${validating ? "试算中…" : "验证并试算"}</button>
          <span class="pw-muted">表达式结果 ÷ 1,000,000 × 聚类倍率 = 单次 ${cur === "CNY" ? "人民币 (¥)" : "美元 ($)"} 费用；可在上方单价币种处切换 USD / CNY。</span>
        </div>
        ${previewHtml}
      </div>`;
    }

    const isReq = mode === "per_request";
    return `<div class="pw-mode-body">
      <div class="pw-fixed-price-row">
        <div class="pw-field">
          <span class="pw-label">${isReq ? "每次请求单价" : "每次调用单价"}</span>
          <span class="pw-unit-input">
            <input type="number" min="0" max="1000000" step="0.0001" data-pricing-rule-price="${esc(pid)}" value="${esc(activeRule.price ?? "")}" placeholder="例如 0.01" ${locked ? "disabled" : ""}>
            <i>${esc(cur)} / ${isReq ? "次请求" : "次调用"}</i>
          </span>
        </div>
      </div>
      <p class="pw-ctrl-help">${isReq
        ? "按完整任务/对话回合去重计费（同一回合内多次工具调用仅按 1 次请求计）。"
        : "按每次底层 LLM API 调用次数计费，与输入/输出 Token 数量无关。"}</p>
    </div>`;
  }

  /* ---------- Rendering: Provider Model Card ---------- */

  function renderProviderCard(p, item, {isDeleted = false, linkedCustomInfo = null} = {}) {
    const pid = item.id;
    const isOpen = p.expandedProvider === pid;
    const isPulsing = p.pulseProvider === pid;
    const isActive = Boolean(item.bound || (Number(item.total_tokens) || 0) > 0);
    const model = activeModelFor(p, item);
    const {key: ruleKey, rule: draftRule, isCustom} = lookupDraftRule(p, item, model);
    const {schedule: draftSched} = lookupDraftSchedule(p, item, model);
    const schedCount = draftSched?.enabled ? (draftSched.periods || []).length : 0;
    const badge = statusBadgeFor(p, item);
    const cardDirty = isProviderCardDirty(p, item);
    const locked = p.loading || isBusy(p);

    const baseRule = baseRuleForModel(p, item, model);
    const effectiveRuleForSummary = isCustom && draftRule ? canonicalizeRule(draftRule) : baseRule;
    const cardUnitView = linkedCustomInfo ? (p.sourceUnitView?.[linkedCustomInfo.sourceId] === "native" ? "native" : "fiat") : "fiat";
    const summaryParts = dualSummaryParts(effectiveRuleForSummary, linkedCustomInfo, cardUnitView);
    const summaryDualAttrs = summaryParts.dual
      ? ` data-pw-dual-src="${esc(linkedCustomInfo.sourceId)}" data-pw-fiat="${esc(summaryParts.fiatText)}" data-pw-native="${esc(summaryParts.nativeText)}"`
      : "";
    const summaryInnerHtml = renderDualSummaryInner(effectiveRuleForSummary, linkedCustomInfo, cardUnitView);
    const displayCur = p.draft?.display_currency || p.data?.display_currency || "CNY";
    const fxRate = p.data?.exchange_rate?.effective_usd_cny || 7.2;
    const estMicro = estimateProviderCostMicrousd(item, fxRate);
    const costLabel = item.total_tokens > 0
      ? (estMicro != null ? formatMoney(estMicro, displayCur, fxRate) : (item.effective_mode === "tiered_expr" ? "表达式计费" : "未设单价"))
      : "";

    let bodyHtml = "";
    if (isOpen) {
      const displayRule = draftRule || (
        baseRule && baseRule.mode && baseRule.mode !== "per_token"
          ? cloneRuleForDraft(baseRule)
          : {
            mode: "per_token",
            currency: p.draft?.display_currency === "CNY" ? "CNY" : "USD",
            input: "", input_cached: "", cache_creation: "", output: "",
            multiplier: "1", free: false,
          }
      );
      const activeMode = displayRule.mode || "per_token";
      const cur = displayRule.currency === "CNY" ? "CNY" : "USD";
      const candModels = Array.isArray(item.candidates) && item.candidates.length ? item.candidates : (model ? [model] : []);
      const dirtyAny = isDirty(p);

      bodyHtml = `<div class="pw-card-body">
        ${candModels.length > 1 ? `<div class="pw-model-pills" role="group" aria-label="模型子项切换">
          <span class="pw-label">模型：</span>
          ${candModels.map(m => `<button type="button" class="pw-model-pill${m === model ? " on" : ""}" data-pricing-select-model="${esc(pid)}" data-model-name="${esc(m)}">${esc(m)}</button>`).join("")}
        </div>` : ""}

        <div class="pw-card-toolbar">
          <div class="pw-toolbar-left">
            <div class="pw-inline-field">
              <span class="pw-label">单价币种</span>
              <select data-pricing-rule-currency="${esc(pid)}" ${locked ? "disabled" : ""} title="选择单价录入币种">
                <option value="USD" ${cur === "USD" ? "selected" : ""}>USD ($)</option>
                <option value="CNY" ${cur === "CNY" ? "selected" : ""}>CNY (¥)</option>
              </select>
            </div>
            <div class="pw-inline-toggle">
              <span class="pw-label">免费模型</span>
              <label class="model-switch"><input type="checkbox" data-pricing-rule-free="${esc(pid)}" ${activeMode === "per_token" && displayRule.free ? "checked" : ""} ${locked ? "disabled" : ""} aria-label="设为免费模型"><i></i></label>
            </div>
          </div>
          <div class="pw-toolbar-right">
            ${item.has_api_base ? `<button type="button" class="button small" data-pricing-detect-newapi="${esc(pid)}" ${locked ? "disabled" : ""}>${p.detectingProvider === pid ? "探测中…" : "探测 New API 价格源"}</button>` : ""}
            ${item.preset_match ? `<button type="button" class="button small" data-pricing-fill-preset="${esc(pid)}" ${locked ? "disabled" : ""}>填入参考价</button>` : ""}
            ${isCustom || schedCount > 0 ? `<button type="button" class="button small" data-pricing-clear-rule="${esc(pid)}" ${locked ? "disabled" : ""}>清除覆盖</button>` : ""}
            ${isDeleted ? `<button type="button" class="button small" data-pricing-residue-clear-one="${esc(pid)}" ${locked ? "disabled" : ""}>清理此项残留</button>` : ""}
          </div>
        </div>

        ${renderCatalogSection(p, item, model, locked)}

        <div class="pw-mode-tabs" role="tablist" aria-label="计价模式">
          ${MODE_OPTIONS.map(([m, lbl]) => `<button type="button" role="tab" aria-selected="${activeMode === m}" class="${activeMode === m ? "on" : ""}" data-pricing-rule-mode="${esc(pid)}" data-mode="${m}" ${locked ? "disabled" : ""}>${lbl}</button>`).join("")}
        </div>

        ${renderModeEditor(p, item, model, displayRule, ruleKey, locked)}

        ${renderScheduleSection(p, item, model, locked)}

        <div class="pw-card-foot">
          <span class="pw-foot-summary">当前规则键：<code>${esc(ruleKey)}</code> · 聚类倍率 ${formatRateNumber(item.cluster_multiplier ?? 1)}×</span>
          <div class="pw-foot-actions">
            ${cardDirty ? `<button type="button" class="button small" data-pricing-discard-card="${esc(pid)}" ${locked ? "disabled" : ""}>撤回此卡改动</button>` : ""}
            <button type="button" class="button small primary" data-pricing-save ${locked || p.stale || !dirtyAny ? "disabled" : ""}>${p.busy ? "保存中…" : "保存更改"}</button>
          </div>
        </div>
      </div>`;
    }

    return `<article class="pw-card${isOpen ? " is-open" : ""}${isPulsing ? " is-pulse" : ""}${cardDirty ? " is-dirty" : ""}${isDeleted ? " is-deleted" : ""}" data-pw-card="${esc(pid)}">
      <button type="button" class="pw-card-head" data-pricing-toggle-card="${esc(pid)}" aria-expanded="${isOpen}">
        <span class="pw-card-head-main">
          <span class="pw-card-title-row">
            <strong>${esc(pid)}</strong>
            ${model ? `<code class="pw-model-id">${esc(model)}</code>` : ""}
            ${isDeleted
              ? '<span class="pw-badge is-warn">已删除供应商残留</span>'
              : (isActive ? '<span class="pw-badge is-ok">在用</span>' : '<span class="pw-badge is-muted">暂未调用</span>')}
            <span class="pw-badge is-${badge.tone}">${esc(badge.text)}</span>
            ${schedCount > 0 ? `<span class="pw-badge is-accent">分时 × ${schedCount}</span>` : ""}
            ${cardDirty ? '<span class="pw-badge is-warn">待保存</span>' : ""}
          </span>
          <span class="pw-card-meta-row">
            <span class="pw-num">${formatTokensCompact(item.total_tokens)} tok</span>
            ${item.calls > 0 ? `<span class="pw-muted">${formatTokensFull(item.calls)} 次调用</span>` : ""}
            ${costLabel ? `<span class="pw-cost-pill">${esc(costLabel)}</span>` : ""}
            <span class="pw-price-summary"${summaryDualAttrs}>${summaryInnerHtml}</span>
          </span>
        </span>
        <span class="pw-chevron" aria-hidden="true">${isOpen ? "▾" : "▸"}</span>
      </button>
      ${bodyHtml}
    </article>`;
  }

  /* ---------- Rendering: 2-Pane Master-Detail Workbench ---------- */

  function renderWorkbench(p) {
    const data = p.data;
    const locked = p.loading || isBusy(p);
    const rawClusters = data.clusters || [];
    const query = String(p.search || "").trim().toLowerCase();

    const filteredClusters = query
      ? rawClusters.map(c => {
          const clusterMatch = String(c.name || "").toLowerCase().includes(query)
            || String(c.cluster_id || "").toLowerCase().includes(query);
          const matchedProviders = clusterMatch
            ? c.providers
            : (c.providers || []).filter(item =>
                String(item.id || "").toLowerCase().includes(query)
                || String(item.model || "").toLowerCase().includes(query)
                || (item.candidates || []).some(m => String(m).toLowerCase().includes(query)),
              );
          return matchedProviders.length ? {...c, providers: matchedProviders} : null;
        }).filter(Boolean)
      : rawClusters;

    const totalConfiguredModels = rawClusters.reduce((sum, c) => sum + (c.providers?.length || 0), 0);
    const unpricedCount = (data.unpriced || []).length;

    let selected = filteredClusters.find(c => c.cluster_id === p.selectedCluster) || filteredClusters[0] || null;
    if (selected && !query) p.selectedCluster = selected.cluster_id;

    // Left rail items with active vs unused divider
    const activeClusters = filteredClusters.filter(c => (Number(c.cluster_total_tokens) || 0) > 0 || c.has_bound);
    const unusedClusters = filteredClusters.filter(c => !((Number(c.cluster_total_tokens) || 0) > 0 || c.has_bound));

    const renderRailItem = cluster => {
      const cid = cluster.cluster_id;
      const isSelected = selected && selected.cluster_id === cid;
      const mult = Number(p.draft?.pricing_multipliers?.[cid] ?? cluster.multiplier ?? 1);
      const hasMult = Number.isFinite(mult) && mult !== 1;
      const hasUnpriced = (data.unpriced || []).some(u => !u.deleted && (u.cluster_id === cid || (cluster.providers || []).some(pr => pr.id === u.provider_id)));
      return `<button type="button" class="pw-rail-item${isSelected ? " is-active" : ""}" data-pricing-select-cluster="${esc(cid)}" aria-current="${isSelected ? "true" : "false"}">
        <span class="pw-rail-top">
          <span class="pw-rail-name" title="${esc(cluster.name)}">${esc(cluster.name)}</span>
          <span class="pw-rail-count">${cluster.providers.length}</span>
        </span>
        <span class="pw-rail-bottom">
          <span class="pw-num">${formatTokensCompact(cluster.cluster_total_tokens)} tok</span>
          ${hasMult ? `<span class="pw-badge is-accent">${formatRateNumber(mult)}×</span>` : ""}
          ${hasUnpriced ? '<span class="pw-unpriced-dot" title="包含未定价用量" aria-label="包含未定价用量"></span>' : ""}
        </span>
      </button>`;
    };

    const railHtml = filteredClusters.length
      ? `${activeClusters.map(renderRailItem).join("")}${activeClusters.length && unusedClusters.length ? '<div class="pw-rail-divider">暂未产生用量</div>' : ""}${unusedClusters.map(renderRailItem).join("")}`
      : '<p class="pw-empty-note">没有匹配的供应商或模型。</p>';

    // Right pane for selected cluster
    let detailHtml = '<div class="pw-empty-pane">当前没有已配置的模型供应商；完成 AstrBot 模型配置后将自动出现。</div>';
    if (selected) {
      const cid = selected.cluster_id;
      const mult = Number(p.draft?.pricing_multipliers?.[cid] ?? selected.multiplier ?? 1);
      const multInputVal = Object.hasOwn(p.clusterMultDraft, cid)
        ? p.clusterMultDraft[cid]
        : String(Number.isFinite(mult) ? mult : 1);
      const probeTarget = (selected.providers || []).find(item => item.has_api_base) || null;
      const linkedNewApi = selected.newapi_source_id
        || (selected.providers || []).find(item => item.newapi_source_id)?.newapi_source_id
        || null;
      const linkedSrc = linkedNewApi
        ? (data.sources || []).find(s => s.source_id === linkedNewApi) || null
        : null;
      const linkedMeta = (linkedSrc && linkedSrc.site_meta && typeof linkedSrc.site_meta === "object")
        ? linkedSrc.site_meta
        : null;
      const groupRatioMap = (linkedMeta && linkedMeta.group_ratio && typeof linkedMeta.group_ratio === "object")
        ? linkedMeta.group_ratio
        : {};
      const groupEntries = Object.entries(groupRatioMap)
        .filter(([, r]) => Number.isFinite(Number(r)))
        .sort((a, b) => Number(a[1]) - Number(b[1]) || a[0].localeCompare(b[0]));
      const groupHint = selected.newapi_group_hint && typeof selected.newapi_group_hint === "object"
        ? selected.newapi_group_hint
        : null;
      const linkedCur = linkedSrc?.currency === "CNY" ? "CNY" : "USD";
      const linkedCurOvr = linkedSrc?.currency_override === "CNY" || linkedSrc?.currency_override === "USD"
        ? linkedSrc.currency_override
        : "auto";
      const linkedQdt = String(linkedMeta?.quota_display_type || "").toUpperCase();
      const linkedCustomSym = String(linkedMeta?.custom_currency_symbol || "").trim();
      const linkedRechargePrice = linkedMeta?.site_recharge_price ?? linkedMeta?.recharge_price;
      const linkedCustomInfo = customUnitInfoForSource(linkedSrc);
      const linkedCnyHint = (linkedMeta?.cny_recharge_hint && typeof linkedMeta.cny_recharge_hint === "object")
        ? linkedMeta.cny_recharge_hint
        : null;
      const linkedQuotaName = String(linkedMeta?.quota_station_name || "").trim();
      const linkedSourceBadge = linkedSrc
        ? (linkedCustomInfo
          ? `${linkedSrc.label} · ${linkedCur === "CNY" ? "CNY ¥" : "USD $"}（${linkedCustomInfo.ratioText}）`
          : `${linkedSrc.label} · ${linkedCur === "CNY" ? "CNY ¥" : linkedQdt === "CUSTOM" && linkedCurOvr === "auto" ? `自定义${linkedCustomSym ? " " + linkedCustomSym : ""}` : "USD $"}`)
        : "New API 源";

      let newApiBarHtml = "";
      if (linkedSrc && (groupEntries.length > 0 || groupHint || linkedQdt === "CUSTOM" || linkedCustomInfo || linkedCnyHint || linkedMeta)) {
        const autoCurDesc = linkedCur === "CNY"
          ? (linkedCustomInfo ? `CNY ¥ · ${linkedCustomInfo.ratioText}` : "CNY ¥")
          : linkedQdt === "CUSTOM"
            ? `自定义${linkedCustomSym ? " " + linkedCustomSym : ""}`
            : "USD $";
        const hintRatio = groupHint && Number.isFinite(Number(groupHint.ratio)) ? Number(groupHint.ratio) : null;
        const showApplyHint = Boolean(groupHint?.group && hintRatio !== null && Math.abs(hintRatio - mult) > 1e-6);
        const unitViewMode = linkedCustomInfo && p.sourceUnitView?.[linkedSrc.source_id] === "native" ? "native" : "fiat";
        const rateInputVal = linkedCustomInfo
          ? (Object.hasOwn(p.sourceRateDraft || {}, linkedSrc.source_id)
            ? p.sourceRateDraft[linkedSrc.source_id]
            : formatRateNumber(linkedCustomInfo.nativePerFiat))
          : "";
        const dualToggleHtml = linkedCustomInfo
          ? `<div class="pw-dual-toggle" role="group" aria-label="双单位单价视图切换">
              <span class="pw-label">单价视图</span>
              <div class="pw-seg">
                <button type="button" data-pricing-unit-view="${esc(linkedSrc.source_id)}" data-unit-mode="fiat" aria-pressed="${unitViewMode === "fiat"}" class="${unitViewMode === "fiat" ? "on" : ""}">${esc(linkedCustomInfo.fiatLabel)}</button>
                <button type="button" data-pricing-unit-view="${esc(linkedSrc.source_id)}" data-unit-mode="native" aria-pressed="${unitViewMode === "native"}" class="${unitViewMode === "native" ? "on" : ""}">${esc(linkedCustomInfo.symbol)} 原币</button>
              </div>
            </div>
            <div class="pw-dual-rate-editor">
              <span class="pw-label">换算率</span>
              <span class="pw-unit-input">
                <input type="number" min="0.0001" max="100000" step="0.01" data-pricing-custom-rate-input="${esc(linkedSrc.source_id)}" value="${esc(rateInputVal)}" aria-label="自定义币种折算率" ${locked ? "disabled" : ""}>
                <i>${esc(linkedCustomInfo.symbol)} = ${esc(linkedCustomInfo.fiatSym)}1</i>
              </span>
              <button type="button" class="button small" data-pricing-custom-rate-apply="${esc(linkedSrc.source_id)}" ${locked ? "disabled" : ""}>应用</button>
              ${linkedCustomInfo.hasManualRate ? `<button type="button" class="button small" data-pricing-custom-rate-reset="${esc(linkedSrc.source_id)}" ${locked ? "disabled" : ""}>恢复自动</button>` : ""}
            </div>`
          : "";
        const customNoticeHtml = linkedQdt === "CUSTOM" && linkedCurOvr === "auto" && linkedCur !== "CNY"
          ? `<span class="pw-newapi-hint is-warn">该站使用自定义代币${linkedCustomSym ? ` ${esc(linkedCustomSym)}` : ""}${linkedRechargePrice != null ? `（站内充值参考：1 单位 = ¥${formatRateNumber(linkedRechargePrice)}）` : ""}，如按人民币结算请在此一键切换为 CNY。</span>`
          : "";
        const cnyHintHtml = linkedCnyHint && linkedCurOvr === "auto" && linkedCur === "USD" && linkedCnyHint.recharge_price != null
          ? `<div class="pw-newapi-hint-row">
              <span class="pw-newapi-hint is-warn">检测到该站在线充值价为 ¥${formatRateNumber(linkedCnyHint.recharge_price)} / 基准单位，若按人民币结算可一键切换。</span>
              <button type="button" class="button small" data-pricing-apply-cny-hint="${esc(linkedSrc.source_id)}" data-recharge-price="${esc(linkedCnyHint.recharge_price)}" ${locked ? "disabled" : ""}>按充值价切换为 CNY ¥</button>
            </div>`
          : "";
        const customRulesWarnHtml = showApplyHint && selected.has_custom_rules
          ? '<span class="pw-newapi-hint">提示：聚类倍率会同时作用于该供应商下的已自定义模型规则。</span>'
          : "";
        newApiBarHtml = `<div class="pw-newapi-bar" aria-label="New API 网关分组与币种辅助">
          <div class="pw-newapi-bar-row">
            ${linkedQuotaName ? `<span class="pw-badge is-ok">已联动额度看板 · ${esc(linkedQuotaName)}</span>` : ""}
            ${groupHint?.group && hintRatio !== null ? `<div class="pw-newapi-chip-group">
              <span class="pw-badge is-accent">识别分组 · ${esc(groupHint.group)} (${formatRateNumber(hintRatio)}×)</span>
              ${showApplyHint ? `<button type="button" class="button small" data-pricing-apply-group-mult="${esc(cid)}" data-ratio="${esc(hintRatio)}" ${locked ? "disabled" : ""}>一键应用 ${formatRateNumber(hintRatio)}×</button>` : '<span class="pw-muted">当前倍率已匹配</span>'}
            </div>` : ""}
            ${groupEntries.length > 0 ? `<div class="pw-inline-field">
              <span class="pw-label">站点分组倍率</span>
              <select data-pricing-group-select="${esc(cid)}" aria-label="按站点分组填入聚类倍率" ${locked ? "disabled" : ""}>
                <option value="">选择分组填入倍率…</option>
                ${groupEntries.map(([g, r]) => `<option value="${esc(r)}">${esc(g)} (${formatRateNumber(r)}×)</option>`).join("")}
              </select>
            </div>` : ""}
            <div class="pw-inline-field">
              <span class="pw-label">价格源币种</span>
              <select data-pricing-source-currency="${esc(linkedSrc.source_id)}" aria-label="New API 价格源结算币种" ${locked ? "disabled" : ""}>
                <option value="auto" ${linkedCurOvr === "auto" ? "selected" : ""}>自动识别 (${esc(autoCurDesc)})</option>
                <option value="CNY" ${linkedCurOvr === "CNY" ? "selected" : ""}>CNY (¥ 人民币)</option>
                <option value="USD" ${linkedCurOvr === "USD" ? "selected" : ""}>USD ($ 美元)</option>
              </select>
            </div>
            ${dualToggleHtml}
          </div>
          ${customNoticeHtml}${cnyHintHtml}${customRulesWarnHtml}
        </div>`;
      }

      const activeItems = selected.providers.filter(item => item.bound || (Number(item.total_tokens) || 0) > 0);
      const unusedItems = selected.providers.filter(item => !(item.bound || (Number(item.total_tokens) || 0) > 0));

      detailHtml = `<div class="pw-cluster-head">
        <div class="pw-cluster-info">
          <div class="pw-cluster-title-row">
            <h4>${esc(selected.name)}</h4>
            <span class="pw-badge is-muted">${selected.providers.length} 个模型</span>
            <span class="pw-badge ${mult !== 1 ? "is-accent" : "is-muted"}">当前 ${formatRateNumber(mult)}×</span>
            ${linkedNewApi ? `<span class="pw-badge is-ok" title="${esc(linkedNewApi)}">已接 ${esc(linkedSourceBadge)}</span>` : ""}
          </div>
          <span class="pw-cluster-sub">累计 ${formatTokensFull(selected.cluster_total_tokens)} token · ${selected.active_count} 个在用</span>
        </div>
        <div class="pw-cluster-actions">
          <div class="pw-mult-editor">
            <span class="pw-label">聚类倍率</span>
            <span class="pw-unit-input"><input type="number" min="0" max="100" step="0.01" data-pricing-cluster-mult-input="${esc(cid)}" value="${esc(multInputVal)}" aria-label="设置聚类倍率" ${locked ? "disabled" : ""}><i>×</i></span>
            <button type="button" class="button small primary" data-pricing-cluster-mult-apply="${esc(cid)}" ${locked ? "disabled" : ""}>应用</button>
            ${mult !== 1 ? `<button type="button" class="button small" data-pricing-cluster-mult-reset="${esc(cid)}" ${locked ? "disabled" : ""}>恢复 1×</button>` : ""}
          </div>
          ${probeTarget ? `<button type="button" class="button small" data-pricing-detect-newapi="${esc(probeTarget.id)}" ${locked ? "disabled" : ""}>${p.detectingProvider === probeTarget.id ? "探测中…" : (linkedNewApi ? "重新探测 New API 价格源" : "探测并启用 New API 价格源")}</button>` : ""}
        </div>
      </div>
      ${newApiBarHtml}
      <div class="pw-cards">
        ${activeItems.map(item => renderProviderCard(p, item, {linkedCustomInfo})).join("")}
        ${activeItems.length && unusedItems.length ? '<div class="pw-list-divider">已配置 · 暂未产生调用</div>' : ""}
        ${unusedItems.map(item => renderProviderCard(p, item, {linkedCustomInfo})).join("")}
      </div>`;
    }

    return `<section class="pw-workbench" aria-label="供应商定价工作台">
      <div class="pw-wb-head">
        <div class="pw-wb-title">
          <strong>供应商定价</strong>
          <span class="pw-badge is-muted">${rawClusters.length} 个供应商</span>
          <span class="pw-badge is-muted">${totalConfiguredModels} 个模型</span>
          ${unpricedCount > 0 ? `<span class="pw-badge is-warn">${unpricedCount} 个未定价</span>` : ""}
        </div>
        <div class="pw-wb-tools">
          <input type="search" class="pw-search" data-pricing-search value="${esc(p.search)}" placeholder="搜索供应商、Provider 或模型名…" aria-label="搜索供应商或模型">
          ${p.resetArmed
            ? `<button type="button" class="button small pw-btn-danger" data-pricing-reset-confirm ${locked ? "disabled" : ""}>${p.resetting ? "重置中…" : "确认重置全部定价"}</button>
               <button type="button" class="button small" data-pricing-reset-cancel ${locked ? "disabled" : ""}>取消</button>`
            : `<button type="button" class="button small" data-pricing-reset-all ${locked ? "disabled" : ""}>重置全部定价</button>`}
        </div>
      </div>
      <div class="pw-split">
        <aside class="pw-rail" aria-label="供应商聚类列表">${railHtml}</aside>
        <div class="pw-detail">${detailHtml}</div>
      </div>
    </section>`;
  }

  /* ---------- Rendering: Deleted Provider Residue ---------- */

  function renderResidueSection(p) {
    const deleted = p.data?.deleted_providers || [];
    if (!deleted.length) return "";
    const locked = p.loading || isBusy(p);
    const totalTokens = deleted.reduce((sum, item) => sum + (Number(item.total_tokens) || 0), 0);
    const hasOverrides = deleted.some(item => item.has_override || item.has_schedule || item.has_selection);

    return `<section class="pw-residue-section" aria-label="已删除供应商残留">
      <div class="pw-residue-head">
        <button type="button" class="pw-residue-toggle" data-pricing-toggle-residue aria-expanded="${p.residueOpen}">
          <span class="pw-chevron" aria-hidden="true">${p.residueOpen ? "▾" : "▸"}</span>
          <strong>已删除供应商残留</strong>
          <span class="pw-badge is-warn">${deleted.length} 项</span>
          <span class="pw-muted">历史累计 ${formatTokensFull(totalTokens)} token · 按 Token 从高到低排列</span>
        </button>
        <button type="button" class="button small" data-pricing-residue-clear-all ${locked || !hasOverrides ? "disabled" : ""} title="${hasOverrides ? "清理所有已删除供应商的孤立定价覆盖配置" : "当前无孤立覆盖配置需清理"}">${p.clearingResidue ? "清理中…" : "清理残留配置"}</button>
      </div>
      ${p.residueOpen ? `<div class="pw-residue-body">
        <p class="pw-ctrl-help">以下供应商已不在 AstrBot 当前配置中，但仍有历史调用或残留单价规则；可补配单价以保持历史费用准确，或一键清理孤立覆盖。</p>
        <div class="pw-cards">${deleted.map(item => renderProviderCard(p, item, {isDeleted: true})).join("")}</div>
      </div>` : ""}
    </section>`;
  }

  /* ---------- Rendering: Collapsible Built-in Reference Catalog ---------- */

  function renderPresetsSection(p) {
    if (!p.presetsOpen) return "";
    const presets = p.data?.presets || [];
    if (!presets.length) return "";
    const rows = presets.map(item => `<tr>
      <td>${esc(item.vendor)}</td>
      <td class="pw-mono">${esc(item.preset_id)}</td>
      <td class="pw-num">$${formatRateNumber(item.input_per_million)}</td>
      <td class="pw-num">$${formatRateNumber(item.cached_input_per_million)}</td>
      <td class="pw-num">${item.cache_creation_per_million == null ? "同输入" : "$" + formatRateNumber(item.cache_creation_per_million)}</td>
      <td class="pw-num">$${formatRateNumber(item.output_per_million)}</td>
    </tr>`).join("");
    return `<section class="pw-presets-section" aria-label="内置主流模型参考价表">
      <div class="pw-presets-head">
        <strong>内置主流模型公开参考价（USD / 1M Token）</strong>
        <button type="button" class="button small" data-pricing-toggle-presets>收起</button>
      </div>
      <div class="pw-table-wrap">
        <table class="pw-cand-table">
          <thead><tr><th>厂商</th><th>模型标识</th><th>输入</th><th>缓存命中</th><th>缓存写入</th><th>输出</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </section>`;
  }

  function renderPane(ctx) {
    const p = ctx.p;
    if (!p.data) {
      return `<div class="model-subpane pw-root">
        <div class="pw-hero">
          <div class="pw-hero-head">
            <div class="pw-hero-copy">
              <span class="pw-eyebrow">模型计价与汇率</span>
              <h3>计价工作台</h3>
            </div>
            <div class="pw-hero-actions">
              <button type="button" class="button small" data-pricing-refresh ${p.loading ? "disabled" : ""}>重新读取</button>
            </div>
          </div>
          <div class="pw-notice ${p.bad ? "is-error" : ""}" role="status">${esc(p.notice || (p.loading ? "正在读取模型计价、价格目录与汇率配置…" : "正在准备计价工作台…"))}</div>
        </div>
      </div>`;
    }
    return `<div class="model-subpane pw-root">
      ${renderHeroAndControls(p)}
      ${renderSourcesBar(p)}
      ${renderUnpricedAlert(p)}
      ${renderWorkbench(p)}
      ${renderResidueSection(p)}
      ${renderPresetsSection(p)}
    </div>`;
  }

  /* ---------- Event handling (Click + Input/Change) ---------- */

  function syncSaveButtonsInPlace(ctx) {
    try {
      const container = ctx.root();
      if (!container?.querySelectorAll) return;
      const dirty = isDirty(ctx.p);
      const disabled = ctx.p.loading || isBusy(ctx.p) || ctx.p.stale || !dirty;
      for (const btn of container.querySelectorAll("[data-pricing-save]")) {
        btn.disabled = disabled;
      }
    } catch {}
  }

  function handleInput(ctx, event) {
    const {p, render, root} = ctx;
    const t = event.target;
    if (!t || !p.data || !p.draft || p.loading || isBusy(p)) return;
    const isChange = event.type === "change";

    if (t.matches("[data-pricing-search]")) {
      p.search = t.value || "";
      render();
      try {
        const next = root()?.querySelector?.("[data-pricing-search]");
        if (next && typeof next.focus === "function") {
          next.focus();
          next.setSelectionRange?.(next.value.length, next.value.length);
        }
      } catch {}
      return;
    }

    if (t.matches("[data-pricing-builtin-presets]")) {
      p.draft.use_builtin_presets = Boolean(t.checked);
      p.bad = false; p.notice = "";
      render();
      return;
    }

    if (t.matches("[data-pricing-auto-daily]")) {
      p.draft.price_sync.auto_daily = Boolean(t.checked);
      p.bad = false; p.notice = "";
      render();
      return;
    }

    if (t.matches("[data-pricing-manual-rate]")) {
      p.draft.exchange_rate.manual_usd_cny = t.value;
      p.bad = false; p.notice = "";
      if (isChange) render(); else syncSaveButtonsInPlace(ctx);
      return;
    }

    const multInputCid = t.dataset?.pricingClusterMultInput;
    if (multInputCid) {
      p.clusterMultDraft[multInputCid] = t.value;
      if (isChange) render();
      return;
    }

    const groupSelectCid = t.dataset?.pricingGroupSelect;
    if (groupSelectCid) {
      if (t.value !== "") {
        p.clusterMultDraft[groupSelectCid] = t.value;
        p.bad = false;
        p.notice = `已将所选分组倍率 ${formatRateNumber(Number(t.value))}× 填入输入框，点击「应用」生效。`;
        render();
      }
      return;
    }

    const srcCurId = t.dataset?.pricingSourceCurrency;
    if (srcCurId) {
      if (p.sourceRateDraft) delete p.sourceRateDraft[srcCurId];
      void updateSourceCurrency(ctx, srcCurId, t.value, {fiatPerBaseUnit: null});
      return;
    }

    const customRateSid = t.dataset?.pricingCustomRateInput;
    if (customRateSid) {
      p.sourceRateDraft ||= {};
      p.sourceRateDraft[customRateSid] = t.value;
      return;
    }

    // Rule currency select (enabled for all 5 modes including tiered_expr)
    const curPid = t.dataset?.pricingRuleCurrency;
    if (curPid) {
      const item = findProvider(p.data, curPid);
      if (!item) return;
      const {key, rule} = ensureDraftRule(p, item, activeModelFor(p, item));
      rule.currency = t.value === "CNY" ? "CNY" : "USD";
      delete p.exprResults[key];
      p.bad = false; p.notice = "";
      render();
      return;
    }

    // Rule free toggle
    const freePid = t.dataset?.pricingRuleFree;
    if (freePid) {
      const item = findProvider(p.data, freePid);
      if (!item) return;
      const model = activeModelFor(p, item);
      const {key} = ensureDraftRule(p, item, model);
      if (t.checked) {
        p.draft.pricing_rules[key] = {
          mode: "per_token",
          currency: p.draft.pricing_rules[key]?.currency === "CNY" ? "CNY" : "USD",
          input: "0", input_cached: "0", cache_creation: "0", output: "0",
          multiplier: "1", free: true,
        };
      } else {
        p.draft.pricing_rules[key] = {
          ...p.draft.pricing_rules[key],
          mode: "per_token",
          free: false,
          input: "", input_cached: "", cache_creation: "", output: "",
        };
      }
      p.bad = false; p.notice = "";
      render();
      return;
    }

    // per_token rate inputs
    const rateField = t.dataset?.pricingRateField;
    if (rateField && t.dataset?.pid) {
      const item = findProvider(p.data, t.dataset.pid);
      if (!item) return;
      const {rule} = ensureDraftRule(p, item, activeModelFor(p, item));
      rule[rateField] = t.value;
      p.bad = false; p.notice = "";
      if (isChange) render(); else syncSaveButtonsInPlace(ctx);
      return;
    }

    // per_tier base inputs
    const tierBaseField = t.dataset?.pricingTierBase;
    if (tierBaseField && t.dataset?.pid) {
      const item = findProvider(p.data, t.dataset.pid);
      if (!item) return;
      const {rule} = ensureDraftRule(p, item, activeModelFor(p, item));
      rule.base ||= {input: "", input_cached: "", cache_creation: "", output: ""};
      rule.base[tierBaseField] = t.value;
      p.bad = false; p.notice = "";
      if (isChange) render(); else syncSaveButtonsInPlace(ctx);
      return;
    }

    // per_tier context_tiers
    const ctxField = t.dataset?.pricingCtxField;
    if (ctxField && t.dataset?.pid) {
      const item = findProvider(p.data, t.dataset.pid);
      if (!item) return;
      const {rule} = ensureDraftRule(p, item, activeModelFor(p, item));
      const idx = Number(t.dataset.idx);
      if (rule.context_tiers?.[idx]) rule.context_tiers[idx][ctxField] = t.value;
      p.bad = false; p.notice = "";
      if (isChange) render(); else syncSaveButtonsInPlace(ctx);
      return;
    }

    // per_tier service_tiers
    const svcField = t.dataset?.pricingSvcField;
    if (svcField && t.dataset?.pid) {
      const item = findProvider(p.data, t.dataset.pid);
      if (!item) return;
      const {rule} = ensureDraftRule(p, item, activeModelFor(p, item));
      const idx = Number(t.dataset.idx);
      if (rule.service_tiers?.[idx]) rule.service_tiers[idx][svcField] = t.value;
      p.bad = false; p.notice = "";
      if (isChange) render(); else syncSaveButtonsInPlace(ctx);
      return;
    }

    // tiered_expr main fields
    const exprField = t.dataset?.pricingExprField;
    if (exprField && t.dataset?.pid) {
      const item = findProvider(p.data, t.dataset.pid);
      if (!item) return;
      const {key, rule} = ensureDraftRule(p, item, activeModelFor(p, item));
      rule[exprField] = t.value;
      delete p.exprResults[key];
      p.bad = false; p.notice = "";
      if (isChange) render(); else syncSaveButtonsInPlace(ctx);
      return;
    }

    // tiered_expr tiers
    const exprTierField = t.dataset?.pricingExprTier;
    if (exprTierField && t.dataset?.pid) {
      const item = findProvider(p.data, t.dataset.pid);
      if (!item) return;
      const {key, rule} = ensureDraftRule(p, item, activeModelFor(p, item));
      const idx = Number(t.dataset.idx);
      if (rule.tiers?.[idx]) rule.tiers[idx][exprTierField] = t.value;
      delete p.exprResults[key];
      p.bad = false; p.notice = "";
      if (isChange) render(); else syncSaveButtonsInPlace(ctx);
      return;
    }

    // per_turn / per_request price
    const pricePid = t.dataset?.pricingRulePrice;
    if (pricePid) {
      const item = findProvider(p.data, pricePid);
      if (!item) return;
      const {rule} = ensureDraftRule(p, item, activeModelFor(p, item));
      rule.price = t.value;
      p.bad = false; p.notice = "";
      if (isChange) render(); else syncSaveButtonsInPlace(ctx);
      return;
    }

    // Schedule enabled toggle
    const schedEnabledPid = t.dataset?.pricingSchedEnabled;
    if (schedEnabledPid) {
      const item = findProvider(p.data, schedEnabledPid);
      if (!item) return;
      const model = activeModelFor(p, item);
      if (t.checked) {
        const {schedule} = ensureDraftSchedule(p, item, model);
        schedule.enabled = true;
      } else {
        const {key, schedule} = lookupDraftSchedule(p, item, model);
        if (schedule) {
          schedule.enabled = false;
          if (!(schedule.periods || []).length) delete p.draft.pricing_schedules[key];
        }
      }
      p.bad = false; p.notice = "";
      render();
      return;
    }

    // Schedule timezone select/custom
    const tzSelectPid = t.dataset?.pricingSchedTzSelect;
    if (tzSelectPid) {
      const item = findProvider(p.data, tzSelectPid);
      if (!item) return;
      const {schedule} = ensureDraftSchedule(p, item, activeModelFor(p, item));
      if (t.value !== "__custom__") schedule.timezone = t.value;
      render();
      return;
    }
    const tzCustomPid = t.dataset?.pricingSchedTzCustom;
    if (tzCustomPid) {
      const item = findProvider(p.data, tzCustomPid);
      if (!item) return;
      const {schedule} = ensureDraftSchedule(p, item, activeModelFor(p, item));
      schedule.timezone = t.value;
      if (isChange) render(); else syncSaveButtonsInPlace(ctx);
      return;
    }

    // Schedule period fields
    const pid = t.dataset?.pid;
    const idx = Number(t.dataset?.idx);
    if (!pid || !Number.isInteger(idx)) return;
    const item = findProvider(p.data, pid);
    if (!item) return;
    const {schedule} = lookupDraftSchedule(p, item, activeModelFor(p, item));
    const period = schedule?.periods?.[idx];
    if (!period) return;

    if (t.matches("[data-pricing-sched-name]")) {
      period.name = t.value;
      if (isChange) render(); else syncSaveButtonsInPlace(ctx);
      return;
    }
    if (t.matches("[data-pricing-sched-allday]")) {
      period.all_day = Boolean(t.checked);
      if (period.all_day) { period.start = "00:00"; period.end = "24:00"; }
      else if (period.end === "24:00") { period.start = "23:00"; period.end = "07:00"; }
      render();
      return;
    }
    if (t.matches("[data-pricing-sched-start]")) {
      period.start = t.value || "00:00";
      render();
      return;
    }
    if (t.matches("[data-pricing-sched-end]")) {
      period.end = t.value || "24:00";
      render();
      return;
    }
    if (t.matches("[data-pricing-sched-mult]")) {
      period.multiplier = t.value;
      if (isChange) render(); else syncSaveButtonsInPlace(ctx);
      return;
    }
    if (t.matches("[data-pricing-sched-ovr-mode]")) {
      const nextMode = t.value;
      const prevCur = period.override?.currency === "CNY" ? "CNY" : "USD";
      period.override = cloneRuleForDraft(
        nextMode === "per_tier" ? {mode: "per_tier", currency: prevCur, base: {input: 0, output: 0}, context_tiers: [], service_tiers: []}
          : nextMode === "tiered_expr" ? {mode: "tiered_expr", currency: prevCur, expr: ""}
          : nextMode === "per_turn" || nextMode === "per_request" ? {mode: nextMode, currency: prevCur, price: 0}
          : {mode: "per_token", currency: prevCur},
      );
      render();
      return;
    }
    if (t.matches("[data-pricing-sched-ovr-currency]")) {
      period.override ||= cloneRuleForDraft({mode: "per_token", currency: "USD"});
      period.override.currency = t.value === "CNY" ? "CNY" : "USD";
      render();
      return;
    }
    if (t.dataset?.pricingSchedOvrField) {
      period.override ||= cloneRuleForDraft({mode: "per_token", currency: "USD"});
      period.override[t.dataset.pricingSchedOvrField] = t.value;
      if (isChange) render(); else syncSaveButtonsInPlace(ctx);
      return;
    }
    if (t.dataset?.pricingSchedOvrBase) {
      period.override ||= cloneRuleForDraft({mode: "per_tier", currency: "USD", base: {}});
      period.override.base ||= {};
      period.override.base[t.dataset.pricingSchedOvrBase] = t.value;
      if (isChange) render(); else syncSaveButtonsInPlace(ctx);
      return;
    }
    if (t.matches("[data-pricing-sched-ovr-expr]")) {
      period.override ||= cloneRuleForDraft({mode: "tiered_expr", currency: "USD", expr: ""});
      period.override.expr = t.value;
      if (isChange) render(); else syncSaveButtonsInPlace(ctx);
      return;
    }
    if (t.matches("[data-pricing-sched-ovr-price]")) {
      period.override ||= cloneRuleForDraft({mode: "per_turn", currency: "USD", price: ""});
      period.override.price = t.value;
      if (isChange) render(); else syncSaveButtonsInPlace(ctx);
    }
  }

  function handleClick(ctx, event) {
    const {p, render} = ctx;
    const t = event.target;
    if (!t || !t.closest) return;

    if (t.closest("[data-pricing-refresh]")) { void refresh(ctx); return; }
    if (t.closest("[data-pricing-save]")) { void save(ctx); return; }
    if (t.closest("[data-pricing-discard]")) { discard(ctx); return; }
    if (t.closest("[data-pricing-sync-rates]")) { void syncRates(ctx); return; }
    if (t.closest("[data-pricing-sync-catalog]")) { void syncCatalog(ctx); return; }
    if (t.closest("[data-pricing-toggle-presets]")) { p.presetsOpen = !p.presetsOpen; render(); return; }
    if (t.closest("[data-pricing-toggle-residue]")) { p.residueOpen = !p.residueOpen; render(); return; }
    if (t.closest("[data-pricing-residue-clear-all]")) { void clearResidue(ctx); return; }

    const clearOneBtn = t.closest("[data-pricing-residue-clear-one]");
    if (clearOneBtn) { void clearResidue(ctx, [clearOneBtn.dataset.pricingResidueClearOne]); return; }

    if (t.closest("[data-pricing-reset-all]")) { p.resetArmed = true; render(); return; }
    if (t.closest("[data-pricing-reset-cancel]")) { p.resetArmed = false; render(); return; }
    if (t.closest("[data-pricing-reset-confirm]")) { void resetAllPricing(ctx); return; }

    const srcToggle = t.closest("[data-pricing-source-toggle]");
    if (srcToggle) { void toggleSource(ctx, srcToggle.dataset.pricingSourceToggle); return; }

    const curBtn = t.closest("[data-pricing-currency]");
    if (curBtn && p.draft) {
      const next = curBtn.dataset.pricingCurrency;
      if (next === "CNY" || next === "USD") {
        p.draft.display_currency = next;
        p.bad = false; p.notice = "";
        render();
      }
      return;
    }

    const rateModeBtn = t.closest("[data-pricing-rate-mode]");
    if (rateModeBtn && p.draft) {
      const mode = rateModeBtn.dataset.pricingRateMode;
      if (mode === "auto" || mode === "manual") {
        p.draft.exchange_rate.mode = mode;
        if (mode === "manual" && !p.draft.exchange_rate.manual_usd_cny) {
          p.draft.exchange_rate.manual_usd_cny = String(Number(p.data?.exchange_rate?.effective_usd_cny || 7.2).toFixed(4));
        }
        p.bad = false; p.notice = "";
        render();
      }
      return;
    }

    // Unpriced alert click-to-jump
    const jumpBtn = t.closest("[data-pricing-jump-provider]");
    if (jumpBtn && p.data) {
      const pid = jumpBtn.dataset.pricingJumpProvider;
      const cid = jumpBtn.dataset.pricingJumpCluster;
      const isDel = jumpBtn.dataset.pricingJumpDeleted === "1";
      const model = jumpBtn.dataset.pricingJumpModel || "";
      p.search = "";
      if (isDel) {
        p.residueOpen = true;
      } else if (cid) {
        p.selectedCluster = cid;
      }
      if (model) p.activeModelByProvider[pid] = model;
      p.expandedProvider = pid;
      triggerPulse(ctx, pid);
      return;
    }

    const selectClusterBtn = t.closest("[data-pricing-select-cluster]");
    if (selectClusterBtn) {
      p.selectedCluster = selectClusterBtn.dataset.pricingSelectCluster;
      render();
      return;
    }

    const multApply = t.closest("[data-pricing-cluster-mult-apply]");
    if (multApply) {
      const cid = multApply.dataset.pricingClusterMultApply;
      const raw = Object.hasOwn(p.clusterMultDraft, cid)
        ? p.clusterMultDraft[cid]
        : (p.draft?.pricing_multipliers?.[cid] ?? 1);
      void applyClusterMultiplier(ctx, cid, raw);
      return;
    }
    const multReset = t.closest("[data-pricing-cluster-mult-reset]");
    if (multReset) {
      void applyClusterMultiplier(ctx, multReset.dataset.pricingClusterMultReset, 1);
      return;
    }

    const applyGroupBtn = t.closest("[data-pricing-apply-group-mult]");
    if (applyGroupBtn) {
      const cid = applyGroupBtn.dataset.pricingApplyGroupMult;
      const ratio = applyGroupBtn.dataset.ratio;
      if (cid && ratio != null && ratio !== "") {
        void applyClusterMultiplier(ctx, cid, ratio);
      }
      return;
    }

    const unitViewBtn = t.closest("[data-pricing-unit-view]");
    if (unitViewBtn) {
      const sid = unitViewBtn.dataset.pricingUnitView;
      const nextMode = unitViewBtn.dataset.unitMode === "native" ? "native" : "fiat";
      if (sid) syncUnitViewInPlace(ctx, sid, nextMode);
      return;
    }

    const customRateApplyBtn = t.closest("[data-pricing-custom-rate-apply]");
    if (customRateApplyBtn && p.data) {
      const sid = customRateApplyBtn.dataset.pricingCustomRateApply;
      const src = (p.data.sources || []).find(s => s.source_id === sid);
      const customInfo = customUnitInfoForSource(src);
      if (src && customInfo) {
        const raw = Object.hasOwn(p.sourceRateDraft || {}, sid)
          ? p.sourceRateDraft[sid]
          : formatRateNumber(customInfo.nativePerFiat);
        const nativePerFiat = Number(raw);
        if (!Number.isFinite(nativePerFiat) || nativePerFiat <= 0 || nativePerFiat > 100000) {
          p.bad = true;
          p.syncNotice = "自定义币种折算率必须是大于 0 且不超过 100000 的有效数字。";
          render();
          return;
        }
        const fiatPerBase = Number((customInfo.customRate / nativePerFiat).toFixed(6));
        if (!Number.isFinite(fiatPerBase) || fiatPerBase <= 0 || fiatPerBase > 100000) {
          p.bad = true;
          p.syncNotice = "折算后的法币单价超出允许范围，请核对输入数值。";
          render();
          return;
        }
        const curOvr = src.currency_override === "CNY" || src.currency_override === "USD"
          ? src.currency_override
          : "auto";
        void updateSourceCurrency(ctx, sid, curOvr, {fiatPerBaseUnit: fiatPerBase, rateOnly: true});
      }
      return;
    }

    const customRateResetBtn = t.closest("[data-pricing-custom-rate-reset]");
    if (customRateResetBtn && p.data) {
      const sid = customRateResetBtn.dataset.pricingCustomRateReset;
      const src = (p.data.sources || []).find(s => s.source_id === sid);
      if (src) {
        const curOvr = src.currency_override === "CNY" || src.currency_override === "USD"
          ? src.currency_override
          : "auto";
        void updateSourceCurrency(ctx, sid, curOvr, {fiatPerBaseUnit: null, rateOnly: true});
      }
      return;
    }

    const applyCnyHintBtn = t.closest("[data-pricing-apply-cny-hint]");
    if (applyCnyHintBtn && p.data) {
      const sid = applyCnyHintBtn.dataset.pricingApplyCnyHint;
      const rechargePrice = Number(applyCnyHintBtn.dataset.rechargePrice);
      const opts = Number.isFinite(rechargePrice) && rechargePrice > 0
        ? {fiatPerBaseUnit: Number(rechargePrice.toFixed(6))}
        : {};
      if (sid) void updateSourceCurrency(ctx, sid, "CNY", opts);
      return;
    }

    const detectBtn = t.closest("[data-pricing-detect-newapi]");
    if (detectBtn) { void detectNewApi(ctx, detectBtn.dataset.pricingDetectNewapi); return; }

    const toggleCardBtn = t.closest("[data-pricing-toggle-card]");
    if (toggleCardBtn) {
      const pid = toggleCardBtn.dataset.pricingToggleCard;
      p.expandedProvider = p.expandedProvider === pid ? "" : pid;
      render();
      return;
    }

    const selectModelBtn = t.closest("[data-pricing-select-model]");
    if (selectModelBtn) {
      p.activeModelByProvider[selectModelBtn.dataset.pricingSelectModel] = selectModelBtn.dataset.modelName || "";
      render();
      return;
    }

    const toggleCandsBtn = t.closest("[data-pricing-toggle-cands]");
    if (toggleCandsBtn) {
      const pid = toggleCandsBtn.dataset.pricingToggleCands;
      p.openCandidates[pid] = !p.openCandidates[pid];
      render();
      return;
    }

    const selCandBtn = t.closest("[data-pricing-select-candidate]");
    if (selCandBtn) {
      void selectCandidate(ctx, selCandBtn.dataset.pricingSelectCandidate, selCandBtn.dataset.modelName || "", selCandBtn.dataset.priceKey || "");
      return;
    }

    const selResetBtn = t.closest("[data-pricing-select-reset]");
    if (selResetBtn) {
      void resetSelection(ctx, selResetBtn.dataset.pricingSelectReset, selResetBtn.dataset.modelName || "");
      return;
    }

    const fillBtn = t.closest("[data-pricing-fill-preset]");
    if (fillBtn && p.data && p.draft) {
      const item = findProvider(p.data, fillBtn.dataset.pricingFillPreset);
      if (item) {
        fillPresetIntoDraft(p, item, activeModelFor(p, item));
        p.bad = false; p.notice = "";
        render();
      }
      return;
    }

    const clearRuleBtn = t.closest("[data-pricing-clear-rule]");
    if (clearRuleBtn && p.data && p.draft) {
      const item = findProvider(p.data, clearRuleBtn.dataset.pricingClearRule);
      if (item) {
        clearProviderOverride(p, item, activeModelFor(p, item));
        p.bad = false; p.notice = "已清除该项自定义覆盖，点击「保存更改」生效。";
        render();
      }
      return;
    }

    const discardCardBtn = t.closest("[data-pricing-discard-card]");
    if (discardCardBtn && p.data && p.draft) {
      const item = findProvider(p.data, discardCardBtn.dataset.pricingDiscardCard);
      if (item) { discardProviderDraft(p, item); render(); }
      return;
    }

    const modeBtn = t.closest("[data-pricing-rule-mode]");
    if (modeBtn && p.data && p.draft) {
      const pid = modeBtn.dataset.pricingRuleMode;
      const item = findProvider(p.data, pid);
      if (item) {
        switchDraftMode(p, item, activeModelFor(p, item), modeBtn.dataset.mode);
        p.bad = false; p.notice = "";
        render();
      }
      return;
    }

    const addCtxBtn = t.closest("[data-pricing-add-ctx-tier]");
    if (addCtxBtn && p.data && p.draft) {
      const item = findProvider(p.data, addCtxBtn.dataset.pricingAddCtxTier);
      if (item) {
        const {rule} = ensureDraftRule(p, item, activeModelFor(p, item));
        rule.context_tiers ||= [];
        const prevMax = rule.context_tiers.reduce((m, t) => Math.max(m, Number(t.threshold_tokens) || 0), 0);
        rule.context_tiers.push({threshold_tokens: String(prevMax ? prevMax * 2 : 128000), input: "", input_cached: "", cache_creation: "", output: ""});
        render();
      }
      return;
    }
    const delCtxBtn = t.closest("[data-pricing-del-ctx-tier]");
    if (delCtxBtn && p.data && p.draft) {
      const item = findProvider(p.data, delCtxBtn.dataset.pricingDelCtxTier);
      if (item) {
        const {rule} = ensureDraftRule(p, item, activeModelFor(p, item));
        rule.context_tiers?.splice(Number(delCtxBtn.dataset.idx), 1);
        render();
      }
      return;
    }

    const addSvcBtn = t.closest("[data-pricing-add-svc-tier]");
    if (addSvcBtn && p.data && p.draft) {
      const item = findProvider(p.data, addSvcBtn.dataset.pricingAddSvcTier);
      if (item) {
        const {rule} = ensureDraftRule(p, item, activeModelFor(p, item));
        rule.service_tiers ||= [];
        rule.service_tiers.push({
          match: "priority", input_multiplier: "2", input_cached_multiplier: "", cache_creation_multiplier: "",
          output_multiplier: "2", input: "", input_cached: "", cache_creation: "", output: "",
        });
        render();
      }
      return;
    }
    const delSvcBtn = t.closest("[data-pricing-del-svc-tier]");
    if (delSvcBtn && p.data && p.draft) {
      const item = findProvider(p.data, delSvcBtn.dataset.pricingDelSvcTier);
      if (item) {
        const {rule} = ensureDraftRule(p, item, activeModelFor(p, item));
        rule.service_tiers?.splice(Number(delSvcBtn.dataset.idx), 1);
        render();
      }
      return;
    }

    const addExprTier = t.closest("[data-pricing-add-expr-tier]");
    if (addExprTier && p.data && p.draft) {
      const item = findProvider(p.data, addExprTier.dataset.pricingAddExprTier);
      if (item) {
        const {rule} = ensureDraftRule(p, item, activeModelFor(p, item));
        rule.tiers ||= [];
        rule.tiers.push({name: `tier_${rule.tiers.length + 1}`, condition: "len > 200000", expr: ""});
        render();
      }
      return;
    }
    const delExprTier = t.closest("[data-pricing-del-expr-tier]");
    if (delExprTier && p.data && p.draft) {
      const item = findProvider(p.data, delExprTier.dataset.pricingDelExprTier);
      if (item) {
        const {rule} = ensureDraftRule(p, item, activeModelFor(p, item));
        rule.tiers?.splice(Number(delExprTier.dataset.idx), 1);
        render();
      }
      return;
    }

    const valExprBtn = t.closest("[data-pricing-expr-validate]");
    if (valExprBtn) { void validateExpression(ctx, valExprBtn.dataset.pricingExprValidate); return; }

    // Schedule period actions
    const addSchedBtn = t.closest("[data-pricing-sched-add]");
    if (addSchedBtn && p.data && p.draft) {
      const item = findProvider(p.data, addSchedBtn.dataset.pricingSchedAdd);
      if (item) {
        const {schedule} = ensureDraftSchedule(p, item, activeModelFor(p, item));
        const nextNum = (schedule.periods?.length || 0) + 1;
        schedule.periods.push({
          id: `period_${nextNum}`, name: `时段 ${nextNum}`,
          days: [1, 2, 3, 4, 5], all_day: false, start: "00:00", end: "06:00",
          mode: "multiplier", multiplier: "0.5",
          override: cloneRuleForDraft({mode: "per_token", currency: "USD"}),
        });
        render();
      }
      return;
    }
    const delSchedBtn = t.closest("[data-pricing-sched-del]");
    if (delSchedBtn && p.data && p.draft) {
      const item = findProvider(p.data, delSchedBtn.dataset.pricingSchedDel);
      if (item) {
        const {schedule} = lookupDraftSchedule(p, item, activeModelFor(p, item));
        schedule?.periods?.splice(Number(delSchedBtn.dataset.idx), 1);
        render();
      }
      return;
    }
    const dayBtn = t.closest("[data-pricing-sched-day]");
    if (dayBtn && p.data && p.draft) {
      const item = findProvider(p.data, dayBtn.dataset.pricingSchedDay);
      if (item) {
        const {schedule} = lookupDraftSchedule(p, item, activeModelFor(p, item));
        const period = schedule?.periods?.[Number(dayBtn.dataset.idx)];
        if (period) {
          const d = Number(dayBtn.dataset.day);
          const set = new Set((period.days || []).map(Number));
          if (set.has(d)) set.delete(d); else set.add(d);
          period.days = [...set].sort((a, b) => a - b);
          render();
        }
      }
      return;
    }
    const schedModeBtn = t.closest("[data-pricing-sched-mode]");
    if (schedModeBtn && p.data && p.draft) {
      const item = findProvider(p.data, schedModeBtn.dataset.pricingSchedMode);
      if (item) {
        const {schedule} = lookupDraftSchedule(p, item, activeModelFor(p, item));
        const period = schedule?.periods?.[Number(schedModeBtn.dataset.idx)];
        if (period) {
          period.mode = schedModeBtn.dataset.mode === "override" ? "override" : "multiplier";
          if (period.mode === "override" && !period.override) {
            period.override = cloneRuleForDraft({mode: "per_token", currency: "USD"});
          }
          render();
        }
      }
    }
  }

  function tabBadge(p) {
    if (!p?.data) return "";
    return p.data.display_currency === "USD" ? "USD" : "CNY";
  }

  return {
    ERRORS,
    createState,
    consumePendingTab,
    tabBadge,
    isDirty,
    changedFields,
    isBusy,
    refresh,
    save,
    discard,
    renderPane,
    handleClick,
    handleInput,
    // Pure helpers exported for deterministic unit testing
    normalizePayload,
    computeChanges,
    validateDraftBeforeSave,
    formatTokensCompact,
    formatTokensFull,
    formatMoney,
    ruleSummary,
    customUnitInfoForSource,
    dualSummaryParts,
    estimateProviderCostMicrousd,
  };
})();
