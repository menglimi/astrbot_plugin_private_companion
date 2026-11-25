/* Multi-station quota dashboard & 2-step smart setup wizard workbench.
 * Mounted as the 7th tab ("额度看板") under 模型与连接 (CompanionModelsPage).
 *
 * Rendering architecture:
 * - Decouples persistent `.qw-main` and sibling `.qw-modal-host` so background refreshes,
 *   logo hydration, and card updates NEVER touch or re-animate an open modal.
 * - Mounts `.qw-modal-backdrop` and `.qw-modal` ONCE per open session (entrance keyframes
 *   play once via `.is-entering` and are never re-triggered).
 * - Patches test/save/detect/login/error/step slots in place with zero scroll jump or focus loss.
 * - Preserves full string-HTML `renderPane()` fallback for non-DOM test harnesses. */
"use strict";
window.CompanionQuotaWorkbench = (() => {
  const PENDING_TAB_KEY = "companion-models-pending-tab.v1";

  const ERRORS = {
    quota_config_conflict: "站点配置已在别处更新，请刷新后再保存。",
    quota_config_invalid: "站点参数未通过校验，请核对地址、令牌、阈值或代理格式。",
    quota_config_unreadable: "额度看板配置文件无法读取。",
    quota_http_not_allowed: "默认仅允许 HTTPS 站点；如为内网自建站，请勾选「允许局域网 HTTP」。",
    quota_station_not_found: "未找到指定的站点，请刷新列表。",
    quota_legacy_readonly: "旧版单站投影请前往「感知 → 余额感知」移除，或先点「编辑」保存迁移后再管理。",
    quota_provider_missing: "所选 AstrBot 供应商不存在或未配置接口地址。",
    quota_request_invalid: "额度看板请求参数无效，请刷新后重试。",
    login_2fa_required: "该站点开启了两步验证 (2FA)，请在浏览器登录后手动复制系统访问令牌。",
    login_failed: "站点账号或密码登录失败，请检查后重试。",
    token_exchange_failed: "换取系统访问令牌失败，请手动从站点个人设置复制。",
    ssrf_blocked: "安全策略拦截了该目标地址。",
  };

  const HEALTH_META = {
    normal: { label: "正常", cls: "ok" },
    low: { label: "偏低", cls: "warn" },
    critical: { label: "告急", cls: "risk" },
    error: { label: "异常", cls: "risk" },
    disabled: { label: "停用", cls: "" },
    pending: { label: "待刷新", cls: "" },
  };

  const AWARENESS_MODE_LABELS = {
    primary: "指定主站",
    aggregate_sum: "多站折算汇总",
    any_low: "任一偏低触发",
  };

  const DEFAULT_SCRIPT_TEMPLATE =
    '({\n' +
    '  request: {\n' +
    '    url: "{{baseUrl}}/user/balance",\n' +
    '    method: "GET",\n' +
    '    headers: {\n' +
    '      "Authorization": "Bearer {{apiKey}}",\n' +
    '      "User-Agent": "astrbot_plugin_private_companion"\n' +
    '    }\n' +
    '  },\n' +
    '  extractor: function(response) {\n' +
    '    return {\n' +
    '      isValid: response.is_active !== false,\n' +
    '      remaining: Number(response.balance),\n' +
    '      unit: "USD"\n' +
    '    };\n' +
    '  }\n' +
    '})';

  const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));

  const SAFE_ICON_DATA_URL_RE = /^data:image\/(png|jpeg|webp|x-icon|gif|svg\+xml);base64,[A-Za-z0-9+/=]+$/i;
  const isSafeIconDataUrl = url => typeof url === "string" && SAFE_ICON_DATA_URL_RE.test(url);
  const iconCache = new Map(); // `${id}:${version}` -> verified raster or sanitized static-SVG data_url

  /* Theme-adapted monochrome vector emblems (100% currentColor, zero hardcoded colors)
   * used as graceful fallbacks when an official station's remote icon is offline/unavailable. */
  const OFFICIAL_BRAND_SVGS = {
    deepseek:
      '<svg class="qw-brand-svg" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
      '<path d="M4.2 14.4c1.3 2.7 4.4 4.4 8 4.4 4.8 0 8.1-2.9 8.6-6.9.2-1.8-.8-3.4-2.5-3.9-1.1-.3-2.1.1-2.9.9-1.2-2.2-3.9-3.6-6.9-3.1-3.1.5-5.3 3-5.3 5.9 0 1 .3 1.9 1 2.7Z" stroke="currentColor" stroke-width="1.75" stroke-linejoin="round"/>' +
      '<circle cx="13.6" cy="11.2" r="1.15" fill="currentColor"/>' +
      '<path d="M3.2 10.6c.8-.9 1.9-1.3 3-1.1" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>' +
      "</svg>",
    openrouter:
      '<svg class="qw-brand-svg" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
      '<circle cx="5.5" cy="12" r="2.2" stroke="currentColor" stroke-width="1.8"/>' +
      '<circle cx="18.2" cy="6.5" r="2.1" stroke="currentColor" stroke-width="1.8"/>' +
      '<circle cx="18.2" cy="17.5" r="2.1" stroke="currentColor" stroke-width="1.8"/>' +
      '<path d="M7.7 11.2 16 7.4M7.7 12.8 16 16.6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>' +
      "</svg>",
    siliconflow:
      '<svg class="qw-brand-svg" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
      '<path d="M5 8.5c2.2-2.2 5.2-2.2 7 0s4.8 2.2 7 0" stroke="currentColor" stroke-width="1.85" stroke-linecap="round"/>' +
      '<path d="M5 12.5c2.2-2.2 5.2-2.2 7 0s4.8 2.2 7 0" stroke="currentColor" stroke-width="1.85" stroke-linecap="round"/>' +
      '<path d="M5 16.5c2.2-2.2 5.2-2.2 7 0s4.8 2.2 7 0" stroke="currentColor" stroke-width="1.85" stroke-linecap="round"/>' +
      "</svg>",
    moonshot:
      '<svg class="qw-brand-svg" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
      '<path d="M18.6 14.6A7.3 7.3 0 0 1 9.4 5.4a7.4 7.4 0 1 0 9.2 9.2Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>' +
      '<path d="M16.8 5.2v3.2M15.2 6.8h3.2" stroke="currentColor" stroke-width="1.65" stroke-linecap="round"/>' +
      "</svg>",
  };
  let wizardOpenSeq = 0;

  function isReducedMotion() {
    if (typeof document === "undefined") return true;
    const motion = document.documentElement?.dataset?.motion;
    if (motion === "off") return true;
    if (motion !== "low" && document.body?.classList?.contains?.("reduce-motion")) return true;
    return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function supportsCssExitAnimation() {
    return !isReducedMotion() && typeof getComputedStyle === "function";
  }

  function formatMoney(value, displayType, symbol) {
    const n = Number(value);
    if (!Number.isFinite(n)) return { num: "—", unit: "" };
    const dt = String(displayType || "CREDIT").toUpperCase();
    if (dt === "TOKENS") return { num: Math.round(n).toLocaleString("zh-CN"), unit: "tokens" };
    if (dt === "CREDIT") {
      return {
        num: n.toLocaleString("zh-CN", { maximumFractionDigits: 2 }),
        unit: symbol || "credit",
      };
    }
    const sym = symbol || (dt === "USD" ? "$" : "¥");
    return {
      num: n.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
      unit: sym,
    };
  }

  function formatSmallCost(value, displayType, symbol) {
    const n = Number(value);
    if (!Number.isFinite(n) || n < 0) return "";
    const dt = String(displayType || "CREDIT").toUpperCase();
    if (dt === "TOKENS") return `${Math.round(n).toLocaleString("zh-CN")} tokens`;
    if (dt === "CREDIT") return `${n.toLocaleString("zh-CN", { maximumFractionDigits: 4 })} ${symbol || "credit"}`;
    const sym = symbol || (dt === "USD" ? "$" : "¥");
    if (n === 0) return `${sym}0`;
    if (n < 0.01) return sym + n.toFixed(6).replace(/0+$/, "").replace(/\.$/, "");
    return sym + n.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 4 });
  }

  function formatShortTime(iso) {
    if (!iso || typeof iso !== "string") return "尚未检查";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso.slice(11, 19) || iso;
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    const ss = String(d.getSeconds()).padStart(2, "0");
    return `${hh}:${mm}:${ss}`;
  }

  function formatAgo(iso) {
    if (!iso) return "";
    const ms = new Date(iso).getTime();
    if (!Number.isFinite(ms)) return "";
    const diff = Date.now() - ms;
    if (diff < 60_000) return "刚刚";
    const min = Math.floor(diff / 60_000);
    if (min < 60) return `${min} 分钟前`;
    const hr = Math.floor(min / 60);
    if (hr < 24) return `${hr} 小时前`;
    return `${Math.floor(hr / 24)} 天前`;
  }

  function initials(name) {
    const s = String(name || "?").trim();
    if (!s) return "?";
    if (/[\u4e00-\u9fa5]/.test(s[0])) return s.slice(0, 1);
    return s.replace(/[^A-Za-z0-9]/g, "").slice(0, 2).toUpperCase() || "?";
  }

  function consumePendingTab() {
    try {
      if (typeof sessionStorage === "undefined") return false;
      if (sessionStorage.getItem(PENDING_TAB_KEY) === "quota") {
        sessionStorage.removeItem(PENDING_TAB_KEY);
        return true;
      }
    } catch {}
    return false;
  }

  function emptyDraft(overrides = {}) {
    return {
      id: "",
      name: "",
      adapter_id: "newapi",
      enabled: true,
      source_mode: "manual",
      astrbot_provider_id: "",
      base_url: "",
      allow_http: false,
      auth_token: "",
      has_token: false,
      clear_auth_token: false,
      user_id: "",
      user_id_header: "New-Api-User",
      custom_script: DEFAULT_SCRIPT_TEMPLATE,
      proxy_url: "",
      has_proxy: false,
      clear_proxy_url: false,
      display_type: "",
      quota_per_unit: "",
      fiat_currency: "",
      fiat_per_base_unit: "",
      display_unit_mode: "native",
      interval_sec: 300,
      low_threshold: "",
      low_percent: 10,
      critical_threshold: "",
      participate_in_awareness: false,
      ...overrides,
    };
  }

  function createState() {
    return {
      loaded: false,
      loading: false,
      busy: false,
      refreshingAll: false,
      refreshingId: "",
      autoRefresh: true,
      autoTimer: null,
      data: null,
      notice: "",
      noticeTone: "", // ok | warn | error | ""
      deleteConfirmId: "",
      awarenessOpen: false,
      awarenessDraft: null,
      awarenessSaving: false,
      pendingFocus: "",
      wizard: null,
      _dom: null,
    };
  }

  function tabBadge(q) {
    const summary = q?.data?.summary;
    if (!summary) return "";
    if (summary.critical_count > 0 || summary.error_count > 0) {
      return `${summary.ok_count}/${summary.total_stations}`;
    }
    return summary.total_stations ? String(summary.total_stations) : "";
  }

  function adapterById(q, id) {
    const list = q?.data?.adapters || [];
    return list.find(item => item.id === id) || list[0] || null;
  }

  function clearAutoTimer(q) {
    if (q?.autoTimer) {
      clearTimeout(q.autoTimer);
      q.autoTimer = null;
    }
  }

  function clearDomTimers(dom) {
    if (!dom) return;
    if (dom.closeTimer) { clearTimeout(dom.closeTimer); dom.closeTimer = null; }
    if (dom.enterTimer) { clearTimeout(dom.enterTimer); dom.enterTimer = null; }
    if (dom.switchTimer) { clearTimeout(dom.switchTimer); dom.switchTimer = null; }
  }

  function detachDom(q) {
    if (!q?._dom) return;
    clearDomTimers(q._dom);
    q._dom = null;
  }

  function getLiveDom(ctx) {
    const dom = ctx?.q?._dom;
    if (!dom || !dom.mountEl || !dom.rootEl) return null;
    if (dom.rootEl.parentNode !== dom.mountEl) {
      clearDomTimers(dom);
      ctx.q._dom = null;
      return null;
    }
    return dom;
  }

  function ensureLiveShell(ctx, mountEl, externalModalHost = null) {
    const q = ctx.q;
    if (
      q._dom
      && q._dom.mountEl === mountEl
      && q._dom.rootEl?.parentNode === mountEl
      && (!externalModalHost || q._dom.modalHost === externalModalHost)
    ) {
      return q._dom;
    }
    detachDom(q);
    mountEl.innerHTML = "";

    const rootEl = document.createElement("div");
    rootEl.className = "qw-root";
    rootEl.setAttribute("data-qw-root", "");

    const mainEl = document.createElement("div");
    mainEl.className = "qw-main";
    mainEl.setAttribute("data-qw-main", "");

    const heroSlot = document.createElement("div");
    heroSlot.className = "qw-hero-slot";
    heroSlot.setAttribute("data-qw-hero-slot", "");

    const gridEl = document.createElement("section");
    gridEl.className = "qw-grid";
    gridEl.setAttribute("data-qw-grid", "");
    gridEl.setAttribute("aria-label", "额度站点列表");

    mainEl.appendChild(heroSlot);
    mainEl.appendChild(gridEl);
    rootEl.appendChild(mainEl);

    let modalHost = externalModalHost;
    if (!modalHost) {
      modalHost = document.createElement("div");
      modalHost.className = "qw-modal-host";
      modalHost.setAttribute("data-qw-modal-host", "");
      modalHost.setAttribute("aria-hidden", "true");
      rootEl.appendChild(modalHost);
    }

    mountEl.appendChild(rootEl);

    q._dom = {
      mountEl,
      rootEl,
      mainEl,
      heroSlot,
      gridEl,
      modalHost,
      modal: null,
      closeTimer: null,
      enterTimer: null,
      switchTimer: null,
    };
    return q._dom;
  }

  /* ---------- Pure Markup Renderers (shared by live DOM patchers & string fallback) ---------- */

  function resolveEffectivePrimary(data) {
    const aw = data?.awareness || { mode: "primary", primary_station_id: "" };
    if (aw.mode !== "primary") return { station: null, explicit: false };
    const enabled = (data?.stations || []).filter(s => s.enabled);
    if (aw.primary_station_id) {
      const explicitMatch = enabled.find(s => s.id === aw.primary_station_id);
      if (explicitMatch) return { station: explicitMatch, explicit: true };
    }
    const autoMatch = enabled.find(s => s.participate_in_awareness) || null;
    return { station: autoMatch, explicit: false };
  }

  function formatCompactRate(value) {
    const n = Number(value);
    if (!Number.isFinite(n) || n <= 0) return "";
    return Math.abs(n - Math.round(n)) < 1e-6 ? String(Math.round(n)) : String(Number(n.toFixed(3)));
  }

  function resolveDualUnitContext(station, snap) {
    if (!snap || !snap.ok || snap.display_type !== "CREDIT") {
      return { canToggle: false, mode: "native", fiatCurrency: "", fiatSymbol: "", nativeSymbol: snap?.symbol || "credit", rateCaption: "" };
    }
    const fiatCurrency = String(station?.fiat_currency || snap.inferred_fiat_currency || "").toUpperCase();
    const validFiat = fiatCurrency === "CNY" || fiatCurrency === "USD";
    const canToggle = Boolean(validFiat && snap.fiat_remaining != null);
    const fiatSymbol = fiatCurrency === "USD" ? "$" : "¥";
    const nativeSymbol = snap.symbol || "credit";
    const mode = canToggle && station?.display_unit_mode === "fiat" ? "fiat" : "native";

    let rateCaption = "";
    const customRate = Number(snap.custom_exchange_rate);
    const pbu = Number(station?.fiat_per_base_unit || snap.site_recharge_price);
    if (validFiat && Number.isFinite(customRate) && customRate > 0 && Number.isFinite(pbu) && pbu > 0) {
      const perFiat = formatCompactRate(customRate / pbu);
      if (perFiat) rateCaption = `${perFiat} ${nativeSymbol} = ${fiatSymbol}1`;
    }
    return { canToggle, mode, fiatCurrency, fiatSymbol, nativeSymbol, rateCaption };
  }

  function renderHeroCore(ctx) {
    const q = ctx.q;
    const data = q.data || {};
    const summary = data.summary || {
      display_currency: "CNY",
      display_symbol: "¥",
      usd_cny_rate: 7.2,
      total_stations: 0,
      ok_count: 0,
      low_count: 0,
      critical_count: 0,
      error_count: 0,
      fiat_remaining: 0,
      cny_remaining: 0,
      usd_remaining: 0,
      cny_station_count: 0,
      usd_station_count: 0,
      non_fiat_station_count: 0,
    };
    const aw = data.awareness || { mode: "primary", primary_station_id: "", target_currency: "" };
    const stations = data.stations || [];
    const participatingCount = stations.filter(s => s.enabled && s.participate_in_awareness).length;
    const usageValues = Object.values(data.provider_usage || {});
    const totalCalls = usageValues.reduce((acc, row) => acc + (Number(row?.calls) || 0), 0);
    const totalTokens = usageValues.reduce((acc, row) => acc + (Number(row?.total_tokens) || 0), 0);

    const fiatParts = [];
    if (summary.cny_station_count > 0) fiatParts.push(`CNY ¥${Number(summary.cny_remaining).toFixed(2)}`);
    if (summary.usd_station_count > 0) fiatParts.push(`USD $${Number(summary.usd_remaining).toFixed(2)}`);
    if (summary.non_fiat_station_count > 0) {
      const inclNote = summary.custom_converted_count > 0 && summary.fiat_remaining_including_custom != null
        ? `（含折合共 ${summary.display_symbol}${Number(summary.fiat_remaining_including_custom).toFixed(2)}）`
        : "";
      fiatParts.push(`另含 ${summary.non_fiat_station_count} 站非现金积分${inclNote}`);
    }
    const rateNote = `汇率 1 USD ≈ ¥${Number(summary.usd_cny_rate || 7.2).toFixed(2)}`;
    const totalMoney = formatMoney(summary.fiat_remaining, summary.display_currency, summary.display_symbol);

    const healthDetails = [];
    if (summary.low_count > 0) healthDetails.push(`${summary.low_count} 偏低`);
    if (summary.critical_count > 0) healthDetails.push(`${summary.critical_count} 告急`);
    if (summary.error_count > 0) healthDetails.push(`${summary.error_count} 异常`);

    const effPrimary = resolveEffectivePrimary(data);
    const awModeLabel = AWARENESS_MODE_LABELS[aw.mode] || "指定主站";
    const awHeadline = aw.mode === "primary" && effPrimary.station
      ? `${effPrimary.explicit ? "主站" : "自动主站"} · ${effPrimary.station.name}`
      : awModeLabel;
    const awMetaText = aw.mode === "primary" && effPrimary.station
      ? `${effPrimary.explicit ? "已手动指定主站" : "未手动指定，自动取首个启用感知站"}${participatingCount ? ` · 已接入 ${participatingCount} 站` : ""}`
      : (participatingCount ? `已接入 ${participatingCount} 个启用站点` : "尚未勾选参与感知的站点");

    return `
      <div class="qw-hero-head">
        <div class="qw-hero-copy">
          <span class="qw-eyebrow">多站点额度看板 · 实时汇总</span>
          <h3>额度看板</h3>
          <p class="qw-subtitle">统一监测中转站与官方 API 剩余额度，按「计价与汇率」实时汇率折算，并可无缝反哺陪伴人格的余额感知。</p>
        </div>
        <div class="qw-toolbar">
          <button type="button" class="qw-btn primary" data-qw-open-wizard="manual">+ 添加站点</button>
          <button type="button" class="qw-btn soft" data-qw-open-wizard="astrbot_provider">从 AstrBot 导入</button>
          <button type="button" class="qw-btn" data-qw-refresh-all ${q.refreshingAll || q.loading ? "disabled" : ""}>${q.refreshingAll ? "刷新中…" : "立即刷新"}</button>
          <label class="qw-toggle-pill">
            <input type="checkbox" data-qw-auto-toggle ${q.autoRefresh ? "checked" : ""}>
            <span>自动刷新</span>
          </label>
          ${data.updated_at ? `<span class="qw-updated qw-num">更新于 ${esc(formatShortTime(data.updated_at))}</span>` : ""}
        </div>
      </div>

      ${data.legacy_projected ? `<div class="qw-notice is-warn">
        <span>已自动展示你在「感知 → 余额感知」中原先配置的单站余额源；点「+ 添加站点」即可平滑升级为多站点管理，原配置不会丢失。</span>
      </div>` : ""}

      ${q.notice ? `<div class="qw-notice ${q.noticeTone ? "is-" + q.noticeTone : ""}" role="status" aria-live="polite"><span>${esc(q.notice)}</span></div>` : ""}

      <div class="qw-kpis">
        <article class="qw-kpi is-accent">
          <span class="qw-kpi-label"><span>折合总余额（${esc(summary.display_currency)}）</span></span>
          <strong class="qw-kpi-value qw-num">${esc(totalMoney.unit)}${esc(totalMoney.num)}</strong>
          <span class="qw-kpi-meta">${esc(fiatParts.length ? `${fiatParts.join(" · ")} · ${rateNote}` : rateNote)}</span>
        </article>
        <article class="qw-kpi">
          <span class="qw-kpi-label"><span>站点健康</span></span>
          <strong class="qw-kpi-value qw-num">${summary.ok_count} / ${summary.total_stations}</strong>
          <span class="qw-kpi-meta">${esc(healthDetails.length ? healthDetails.join(" · ") : summary.total_stations ? "全部启用站点正常" : "尚未添加监测站点")}</span>
        </article>
        <article class="qw-kpi">
          <span class="qw-kpi-label"><span>关联供应商近期调用</span></span>
          <strong class="qw-kpi-value qw-num">${totalCalls.toLocaleString("zh-CN")} 次</strong>
          <span class="qw-kpi-meta qw-num">累计 ${totalTokens.toLocaleString("zh-CN")} Tokens</span>
          <button type="button" class="qw-btn small qw-kpi-action" data-model-tab="pricing">查看计价与汇率</button>
        </article>
        <article class="qw-kpi">
          <span class="qw-kpi-label"><span>陪伴余额感知联动</span></span>
          <strong class="qw-kpi-value" title="${esc(awHeadline)}">${esc(awHeadline)}</strong>
          <span class="qw-kpi-meta">${esc(awMetaText)}</span>
          <button type="button" class="qw-btn small qw-kpi-action" data-qw-toggle-awareness>${q.awarenessOpen ? "收起策略设置" : "配置感知策略"}</button>
        </article>
      </div>
    `;
  }

  function renderAwarenessPanel(ctx) {
    const q = ctx.q;
    const draft = q.awarenessDraft || { mode: "primary", primary_station_id: "", target_currency: "", participating_station_ids: [] };
    const stations = q.data?.stations || [];
    const selectedSet = new Set(draft.participating_station_ids || []);
    return `
      <section class="qw-awareness-panel" aria-label="陪伴余额感知联动策略">
        <div class="qw-awareness-head">
          <div>
            <strong>陪伴余额感知联动策略</strong>
            <small>决定角色在「主动低余额提醒」与「私聊被问余额」时如何读取多站点数据。</small>
          </div>
          <div class="qw-toolbar">
            <button type="button" class="qw-btn small" data-qw-toggle-awareness>取消</button>
            <button type="button" class="qw-btn small primary" data-qw-save-awareness ${q.awarenessSaving ? "disabled" : ""}>${q.awarenessSaving ? "保存中…" : "保存策略"}</button>
          </div>
        </div>
        <div class="qw-awareness-grid">
          <label class="qw-field">
            <span>聚合模式</span>
            <select data-qw-aw-field="mode">
              <option value="primary" ${draft.mode === "primary" ? "selected" : ""}>指定单个主站点（兼容经典模式）</option>
              <option value="aggregate_sum" ${draft.mode === "aggregate_sum" ? "selected" : ""}>勾选站点按汇率折算汇总</option>
              <option value="any_low" ${draft.mode === "any_low" ? "selected" : ""}>任一勾选站点偏低即提醒</option>
            </select>
          </label>
          <label class="qw-field">
            <span>${draft.mode === "primary" ? "指定主站点" : "汇总折算币种"}</span>
            ${draft.mode === "primary"
              ? `<select data-qw-aw-field="primary_station_id">
                  <option value="">自动取首个参与感知的启用站点</option>
                  ${stations.map(st => `<option value="${esc(st.id)}" ${draft.primary_station_id === st.id ? "selected" : ""}>${esc(st.name)}${st.enabled ? "" : "（已停用）"}</option>`).join("")}
                </select>`
              : `<select data-qw-aw-field="target_currency">
                  <option value="" ${!draft.target_currency ? "selected" : ""}>跟随「计价与汇率」展示币种</option>
                  <option value="CNY" ${draft.target_currency === "CNY" ? "selected" : ""}>人民币 (CNY)</option>
                  <option value="USD" ${draft.target_currency === "USD" ? "selected" : ""}>美元 (USD)</option>
                </select>`}
          </label>
          <div class="qw-field">
            <span>主动联系开关</span>
            <button type="button" class="qw-btn small" data-go="manage" data-manage="contact" data-contact-view="sources">前往主动联系 · 外部触发源</button>
          </div>
        </div>
        ${stations.length ? `
          <div class="qw-field">
            <span>参与余额感知的站点</span>
            <div class="qw-awareness-chips">
              ${stations.map(st => {
                const checked = selectedSet.has(st.id);
                return `<label class="qw-chip-check ${checked ? "is-checked" : ""}">
                  <input type="checkbox" data-qw-aw-station="${esc(st.id)}" ${checked ? "checked" : ""}>
                  <span>${esc(st.name)}</span>
                </label>`;
              }).join("")}
            </div>
          </div>
        ` : ""}
      </section>
    `;
  }

  function renderHero(ctx) {
    const q = ctx.q;
    return `
      <section class="qw-hero" aria-label="多站点额度概览">
        <div class="qw-hero-core" data-qw-hero-core>${renderHeroCore(ctx)}</div>
        <div class="qw-awareness-slot" data-qw-awareness-slot>${q.awarenessOpen ? renderAwarenessPanel(ctx) : ""}</div>
      </section>
    `;
  }

  function renderRepairButton(station, snap) {
    if (!snap || snap.ok || !station.enabled) return "";
    if (snap.error_code === "requires_user_id") {
      return `<button type="button" class="qw-btn small primary" data-qw-repair="uid" data-qw-station="${esc(station.id)}">一键补充用户 ID</button>`;
    }
    if (snap.error_code === "sk_key_rejected" || snap.error_code === "auth_failed" || snap.error_code === "missing_credentials") {
      return `<button type="button" class="qw-btn small soft" data-qw-repair="token" data-qw-station="${esc(station.id)}">更新访问令牌</button>`;
    }
    if (snap.error_code === "unsupported_official") {
      return `<button type="button" class="qw-btn small" data-model-tab="pricing">查看计价与汇率估算</button>`;
    }
    return `<button type="button" class="qw-btn small" data-qw-refresh-one="${esc(station.id)}">重试刷新</button>`;
  }

  function avatarMarkupFor(station, snap) {
    const logoKey = snap?.logo_version ? `${station.id}:${snap.logo_version}` : "";
    const cachedLogo = logoKey ? iconCache.get(logoKey) : "";
    const logoUrl = isSafeIconDataUrl(cachedLogo) ? cachedLogo : "";
    if (logoUrl) {
      return { hasLogo: true, isBrand: false, title: station.name, html: `<img src="${esc(logoUrl)}" alt="" loading="lazy" decoding="async">` };
    }
    const brandSvg = OFFICIAL_BRAND_SVGS[String(station?.adapter_id || "").toLowerCase()] || "";
    if (brandSvg) {
      return {
        hasLogo: false,
        isBrand: true,
        title: snap?.logo_error ? `${station.name}（内置品牌徽标）` : station.name,
        html: brandSvg,
      };
    }
    return {
      hasLogo: false,
      isBrand: false,
      title: snap?.logo_error ? "图标未加载：" + snap.logo_error : station.name,
      html: esc(initials(station.name)),
    };
  }

  function renderStationCardParts(ctx, station) {
    const q = ctx.q;
    const data = q.data || {};
    const snap = data.snapshots?.[station.id] || null;
    const summary = data.summary || {};
    const health = summary.health_by_id?.[station.id] || (station.enabled ? "pending" : "disabled");
    const hMeta = HEALTH_META[health] || HEALTH_META.pending;
    const adapter = adapterById(q, station.adapter_id);
    const converted = summary.converted_remaining_by_id?.[station.id];
    const providerInfo = station.astrbot_provider_id
      ? (data.astrbot_providers || []).find(p => p.id === station.astrbot_provider_id)
      : null;
    const usage = station.astrbot_provider_id ? data.provider_usage?.[station.astrbot_provider_id] : null;
    const isRefreshing = q.refreshingId === station.id || (q.refreshingAll && station.enabled);
    const confirmingDelete = q.deleteConfirmId === station.id;

    const isLegacyProjected = Boolean(data.legacy_projected && station.id === "legacy-default");
    const aw = data.awareness || { mode: "primary", primary_station_id: "" };
    const effPrimary = resolveEffectivePrimary(data);
    const isEffectivePrimary = effPrimary.station?.id === station.id;
    const isExplicitPrimary = isEffectivePrimary && effPrimary.explicit;
    const showSetPrimary = station.enabled && !isLegacyProjected && !(aw.mode === "primary" && isExplicitPrimary);

    const avatar = avatarMarkupFor(station, snap);
    const avatarCls = `qw-avatar${avatar.hasLogo ? " has-logo" : ""}${avatar.isBrand ? " is-brand" : ""}`;
    const avatarHtml = `<div class="${avatarCls}" data-qw-avatar="${esc(station.id)}" title="${esc(avatar.title)}">${avatar.html}</div>`;

    const dual = resolveDualUnitContext(station, snap);
    const activeDisplayType = dual.mode === "fiat" ? dual.fiatCurrency : (snap?.display_type || "CREDIT");
    const activeSymbol = dual.mode === "fiat" ? dual.fiatSymbol : (snap?.symbol || "");

    let balanceHtml = "";
    if (snap && snap.ok) {
      if (snap.remaining != null) {
        const displayRem = dual.mode === "fiat" && snap.fiat_remaining != null ? snap.fiat_remaining : snap.remaining;
        const displayUsed = dual.mode === "fiat" && snap.fiat_used != null ? snap.fiat_used : snap.used;
        const displayTotal = dual.mode === "fiat" && snap.fiat_total != null ? snap.fiat_total : snap.total;
        const m = formatMoney(displayRem, activeDisplayType, activeSymbol);
        const showConv = !dual.canToggle && converted != null && snap.display_type !== summary.display_currency;
        const valCls = health === "critical" ? "is-critical" : health === "low" ? "is-low" : "";
        const pct = snap.remaining_percent != null
          ? Math.max(0, Math.min(100, Number(snap.remaining_percent)))
          : (snap.total > 0 ? Math.max(0, Math.min(100, (snap.remaining / snap.total) * 100)) : null);
        const usedM = displayUsed != null ? formatMoney(displayUsed, activeDisplayType, activeSymbol) : null;
        const totalM = displayTotal != null ? formatMoney(displayTotal, activeDisplayType, activeSymbol) : null;

        let dualSubHtml = "";
        if (dual.canToggle) {
          const altText = dual.mode === "fiat"
            ? `${formatMoney(snap.remaining, "CREDIT", dual.nativeSymbol).num} ${dual.nativeSymbol}`
            : `≈ ${dual.fiatSymbol}${Number(snap.fiat_remaining).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
          dualSubHtml = `<span class="qw-balance-conv qw-num">${esc(altText)}</span>` +
            `<button type="button" class="qw-unit-toggle${dual.mode === "fiat" ? " is-fiat" : ""}" data-qw-toggle-unit="${esc(station.id)}" aria-pressed="${dual.mode === "fiat"}" title="切换原币与折合法币显示">${esc(dual.nativeSymbol)} ↔ ${esc(dual.fiatSymbol)}</button>`;
        }

        const extraText = dual.canToggle && dual.rateCaption
          ? `${dual.mode === "fiat" ? `${formatMoney(snap.remaining, "CREDIT", dual.nativeSymbol).num} ${dual.nativeSymbol}` : `≈ ${dual.fiatSymbol}${Number(snap.fiat_remaining).toFixed(2)}`} · ${dual.rateCaption}`
          : snap.extra;

        balanceHtml = `
          <div class="qw-balance-block">
            <div class="qw-balance-row">
              <span class="qw-balance-value qw-num ${valCls}">${esc(m.unit === "¥" || m.unit === "$" ? m.unit + m.num : m.num)}</span>
              ${m.unit !== "¥" && m.unit !== "$" ? `<span class="qw-balance-unit">${esc(m.unit)}</span>` : ""}
              ${dualSubHtml}
              ${showConv ? `<span class="qw-balance-conv qw-num">≈ ${esc(summary.display_symbol)}${Number(converted).toFixed(2)}</span>` : ""}
            </div>
            ${pct !== null ? `
              <div class="qw-progress-wrap">
                <div class="qw-progress-track" role="progressbar" aria-valuenow="${pct.toFixed(0)}" aria-valuemin="0" aria-valuemax="100">
                  <div class="qw-progress-fill ${valCls}" style="width:${pct.toFixed(1)}%"></div>
                </div>
                <div class="qw-progress-meta qw-num">
                  <span>${usedM ? `已用 ${esc(usedM.unit === "¥" || usedM.unit === "$" ? usedM.unit + usedM.num : usedM.num)}` : ""}</span>
                  <span>${totalM ? `总额 ${esc(totalM.unit === "¥" || totalM.unit === "$" ? totalM.unit + totalM.num : totalM.num)} · ` : ""}剩 ${pct.toFixed(1)}%</span>
                </div>
              </div>
            ` : ""}
            ${extraText ? `<div class="qw-extra-line">${esc(extraText)}</div>` : ""}
          </div>
        `;
      } else {
        const usedM = snap.used != null ? formatMoney(snap.used, snap.display_type, snap.symbol) : null;
        const usedStr = usedM ? (usedM.unit === "¥" || usedM.unit === "$" ? usedM.unit + usedM.num : `${usedM.num} ${usedM.unit}`.trim()) : "";
        balanceHtml = `
          <div class="qw-balance-block">
            <div class="qw-balance-row">
              <span class="qw-balance-value">不限额</span>
              ${usedStr ? `<span class="qw-balance-conv qw-num">已用 ${esc(usedStr)}</span>` : ""}
            </div>
            ${snap.extra ? `<div class="qw-extra-line">${esc(snap.extra)}</div>` : ""}
          </div>
        `;
      }
    } else if (!station.enabled) {
      balanceHtml = `<div class="qw-balance-block"><span class="qw-balance-value qw-num">—</span><div class="qw-extra-line">已暂停监测</div></div>`;
    } else if (!snap) {
      balanceHtml = `<div class="qw-balance-block"><span class="qw-balance-value qw-num">—</span><div class="qw-extra-line">等待首次刷新…</div></div>`;
    } else {
      balanceHtml = `<div class="qw-balance-block"><span class="qw-balance-value is-error qw-num">—</span></div>`;
    }

    const subs = Array.isArray(snap?.subscriptions) ? snap.subscriptions.filter(item => item && item.remaining != null) : [];
    let subscriptionHtml = "";
    if (subs.length) {
      const subRemSum = subs.reduce((acc, item) => acc + (Number(item.remaining) || 0), 0);
      const hasSubFiat = subs.every(item => item.fiat_remaining != null);
      const subFiatSum = hasSubFiat ? subs.reduce((acc, item) => acc + (Number(item.fiat_remaining) || 0), 0) : null;
      const subNames = subs.map(item => item.name).filter(Boolean).join(" / ");
      const subNativeM = formatMoney(subRemSum, snap.display_type, snap.symbol);
      const subNativeStr = subNativeM.unit === "¥" || subNativeM.unit === "$"
        ? `${subNativeM.unit}${subNativeM.num}`
        : `${subNativeM.num} ${subNativeM.unit}`.trim();
      const subFiatSym = dual.fiatSymbol || (snap.inferred_fiat_currency === "USD" ? "$" : "¥");
      const subMainText = dual.canToggle && subFiatSum != null
        ? (dual.mode === "fiat"
            ? `${subFiatSym}${subFiatSum.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}（${subNativeStr}）`
            : `${subNativeStr} ≈ ${subFiatSym}${subFiatSum.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`)
        : subNativeStr;
      subscriptionHtml = `
        <div class="qw-sub-strip" data-qw-subscriptions title="${esc(subNames ? "活跃订阅套餐：" + subNames : "站点活跃订阅套餐余量")}">
          <span class="qw-sub-label">订阅余量</span>
          <strong class="qw-num">${esc(subMainText)}</strong>
          <span class="qw-sub-meta">· ${subs.length} 个套餐${subNames ? `（${esc(subNames)}）` : ""}</span>
        </div>
      `;
    }

    const lastCostVal = dual.mode === "fiat" && snap?.fiat_last_used_cost != null ? snap.fiat_last_used_cost : snap?.last_used_cost;
    const lastUseHtml = snap && (snap.last_used_at || snap.last_used_error)
      ? `<div class="qw-lastuse" title="${esc(snap.last_used_error ? "日志接口：" + snap.last_used_error : "来自站点自身消费日志")}">
          <span class="qw-lastuse-label">最近消费</span>
          ${snap.last_used_at
            ? `<span class="qw-num">${esc(formatShortTime(snap.last_used_at))}</span>
               <span>· ${esc(formatAgo(snap.last_used_at))}</span>
               ${snap.last_used_model ? `<span>·</span><span class="qw-lastuse-model" title="${esc(snap.last_used_model)}">${esc(snap.last_used_model)}</span>` : ""}
               ${lastCostVal != null ? `<span>·</span><span class="qw-lastuse-cost qw-num">${esc(formatSmallCost(lastCostVal, activeDisplayType, activeSymbol))}</span>` : ""}`
            : `<span>读取失败（${esc(String(snap.last_used_error).slice(0, 42))}）</span>`}
        </div>`
      : "";

    const providerHtml = station.source_mode === "astrbot_provider"
      ? `<div class="qw-provider-strip">
          <span>复用 AstrBot：<strong>${esc(providerInfo?.label || station.astrbot_provider_id)}</strong></span>
          ${usage?.calls ? `<span class="qw-num">近期 ${usage.calls} 次调用</span>` : ""}
        </div>`
      : "";

    const errorHtml = snap && !snap.ok && station.enabled
      ? `<div class="qw-error-box ${snap.error_code === "unsupported_official" ? "is-info" : ""}">
          <p><strong>${esc(snap.error || "查询失败")}</strong></p>
          ${snap.repair_hint ? `<p>${esc(snap.repair_hint)}</p>` : ""}
          <div class="qw-error-actions">${renderRepairButton(station, snap)}</div>
        </div>`
      : "";

    const subtitleUrl = station.base_url || providerInfo?.api_origin || (station.source_mode === "astrbot_provider" ? `AstrBot · ${station.astrbot_provider_id}` : "自定义脚本");

    const innerHtml = `
      <header class="qw-card-head">
        ${avatarHtml}
        <div class="qw-card-title-wrap">
          <h4 class="qw-card-title" title="${esc(station.name)}">${esc(station.name)}</h4>
          <div class="qw-card-url qw-mono" title="${esc(subtitleUrl)}">${esc(subtitleUrl)}</div>
          <div class="qw-badges">
            <span class="qw-pill">${esc(adapter?.short_name || station.adapter_id)}</span>
            ${isEffectivePrimary ? `<span class="qw-pill accent" data-qw-primary-badge>${isExplicitPrimary ? "★ 主站" : "默认主站"}</span>` : ""}
            ${isLegacyProjected ? `<span class="qw-pill warn">旧版投影</span>` : ""}
            ${snap?.group_name ? `<span class="qw-pill accent">${esc(snap.group_name)}</span>` : ""}
            ${snap?.is_key_scoped ? `<span class="qw-pill warn">单 Key 限额</span>` : ""}
            ${station.participate_in_awareness ? `<span class="qw-pill ok">余额感知</span>` : ""}
          </div>
        </div>
        <span class="qw-status-pill ${hMeta.cls}">${esc(hMeta.label)}</span>
      </header>

      ${balanceHtml}
      ${subscriptionHtml}
      ${errorHtml}
      ${lastUseHtml}
      ${providerHtml}

      <footer class="qw-card-foot">
        <span class="qw-foot-meta qw-num">${snap?.checked_at ? `${esc(formatShortTime(snap.checked_at))}${snap.latency_ms ? ` · ${snap.latency_ms} ms` : ""}` : "未检查"}</span>
        <div class="qw-foot-actions">
          ${showSetPrimary ? `<button type="button" class="qw-btn small soft" data-qw-set-primary="${esc(station.id)}" ${q.awarenessSaving ? "disabled" : ""}>设为主站</button>` : ""}
          <button type="button" class="qw-btn small" data-qw-refresh-one="${esc(station.id)}" ${isRefreshing || !station.enabled ? "disabled" : ""}>${isRefreshing ? "刷新中…" : "刷新"}</button>
          <button type="button" class="qw-btn small" data-qw-edit="${esc(station.id)}">${isLegacyProjected ? "编辑并迁移" : "编辑"}</button>
          ${isLegacyProjected
            ? ""
            : (confirmingDelete
                ? `<button type="button" class="qw-btn small danger" data-qw-delete-confirm="${esc(station.id)}" ${q.busy ? "disabled" : ""}>确认删除</button>
                   <button type="button" class="qw-btn small" data-qw-delete-cancel>取消</button>`
                : `<button type="button" class="qw-btn small" data-qw-delete-arm="${esc(station.id)}">删除</button>`)}
        </div>
      </footer>
    `;
    return { className: `qw-card is-${health}`, innerHtml };
  }

  function renderStationCard(ctx, station) {
    const parts = renderStationCardParts(ctx, station);
    return `<article class="${esc(parts.className)}" data-qw-card="${esc(station.id)}">${parts.innerHtml}</article>`;
  }

  function renderGridContent(ctx) {
    const stations = ctx.q.data?.stations || [];
    if (!stations.length) {
      return `<div class="qw-empty">
        <h4>还没有监测中的额度站点</h4>
        <p>支持一键复用 AstrBot 已有官方供应商（DeepSeek / OpenRouter / 硅基流动 / Moonshot 免填密钥），也支持自动识别 NewAPI / OneAPI / 神人站等中转站与自定义脚本。</p>
        <div class="qw-empty-actions">
          <button type="button" class="qw-btn primary" data-qw-open-wizard="astrbot_provider">从 AstrBot 供应商一键导入</button>
          <button type="button" class="qw-btn" data-qw-open-wizard="manual">手动添加中转站</button>
        </div>
      </div>`;
    }
    return stations.map(st => renderStationCard(ctx, st)).join("");
  }

  function renderWizardStep1Source(ctx) {
    const q = ctx.q;
    const w = q.wizard;
    const d = w.draft;
    const providers = q.data?.astrbot_providers || [];
    if (d.source_mode === "astrbot_provider") {
      return `
        <div class="qw-field">
          <span>选择已配置的 AstrBot 供应商（后端直读最新地址、Key 与代理，绝不复制密钥）</span>
          ${providers.length ? `
            <div class="qw-provider-list">
              ${providers.map(p => `
                <button type="button" class="qw-provider-option ${d.astrbot_provider_id === p.id ? "is-selected" : ""}" data-qw-pick-provider="${esc(p.id)}">
                  <strong>${esc(p.label)}${p.enabled ? "" : "（已停用）"}</strong>
                  <small class="qw-mono">${esc(p.api_origin || "未配置地址")}</small>
                  ${p.model_count ? `<small>${p.model_count} 个模型${p.models_preview?.length ? " · " + esc(p.models_preview.slice(0, 2).join(", ")) : ""}</small>` : ""}
                </button>
              `).join("")}
            </div>
          ` : `<div class="qw-notice"><span>当前未发现可用的 AstrBot 聊天供应商，请切到「手动填写站点地址」。</span></div>`}
        </div>
      `;
    }
    return `
      <div class="qw-field">
        <label for="qw-step1-base-url">站点根地址</label>
        <div class="qw-detect-row">
          <input id="qw-step1-base-url" type="url" data-qw-field="base_url" placeholder="https://api.example.com" value="${esc(d.base_url)}" autocomplete="url">
          <button type="button" class="qw-btn soft" data-qw-detect ${w.detecting ? "disabled" : ""}>${w.detecting ? "识别中…" : "智能识别"}</button>
        </div>
        <span class="qw-field-hint">只需填根地址（带不带 /v1 均可），系统会自动探测站点类型、币种与是否需要用户 ID。</span>
      </div>
      <label class="qw-check-row">
        <input type="checkbox" data-qw-check="allow_http" ${d.allow_http ? "checked" : ""}>
        <span>允许局域网 HTTP / 内网地址（仅限内网自建中转站）</span>
      </label>
    `;
  }

  function renderWizardDetectNotice(w) {
    const det = w?.detectResult;
    if (!det) return "";
    return `
      <div class="qw-notice ${det.ok ? (det.confidence === "high" ? "is-ok" : "is-warn") : "is-error"}">
        <div>
          <strong>${esc(det.reason || "识别结果")}</strong>
          ${det.auth_hint ? `<div class="qw-field-hint">${esc(det.auth_hint)}</div>` : ""}
          ${(det.warnings || []).map(warn => `<div class="qw-field-hint">⚠ ${esc(warn)}</div>`).join("")}
        </div>
      </div>
    `;
  }

  function renderWizardStep1(ctx) {
    const q = ctx.q;
    const w = q.wizard;
    const d = w.draft;
    const adapters = (q.data?.adapters || []).filter(a => a.id !== "unsupported_official" || d.adapter_id === "unsupported_official");

    return `
      <div class="qw-segment" role="tablist" aria-label="站点连接来源">
        <button type="button" role="tab" aria-selected="${d.source_mode === "astrbot_provider"}" class="${d.source_mode === "astrbot_provider" ? "is-active" : ""}" data-qw-source-mode="astrbot_provider">复用 AstrBot 供应商（推荐）</button>
        <button type="button" role="tab" aria-selected="${d.source_mode === "manual"}" class="${d.source_mode === "manual" ? "is-active" : ""}" data-qw-source-mode="manual">手动填写站点地址</button>
      </div>

      <div class="qw-source-slot" data-qw-source-slot>${renderWizardStep1Source(ctx)}</div>

      <div class="qw-detect-slot" data-qw-detect-slot>${renderWizardDetectNotice(w)}</div>

      <div class="qw-field">
        <span>站点协议类型（已自动选中，也可手动点选预设）</span>
        <div class="qw-presets" role="radiogroup" aria-label="站点协议预设">
          ${adapters.map(a => `
            <button type="button" class="qw-preset-chip ${d.adapter_id === a.id ? "is-active" : ""}" data-qw-pick-adapter="${esc(a.id)}" title="${esc(a.summary)}">${esc(a.short_name)}</button>
          `).join("")}
        </div>
        <span class="qw-field-hint" data-qw-adapter-summary>${esc(adapterById(q, d.adapter_id)?.summary || "")}</span>
      </div>
    `;
  }

  function renderDynamicField(field, d, w) {
    const val = d[field.name] ?? field.default ?? "";
    const isHighlight = w.highlightField === field.name;
    const reqMark = field.required ? " *" : "";

    if (field.name === "auth_token") {
      const adapter = w._adapter;
      if (d.source_mode === "astrbot_provider" && adapter?.capabilities?.supports_provider_reuse && !field.required) {
        return `<div class="qw-notice is-ok">
          <span>已绑定 AstrBot 供应商，后端查询时自动读取最新 API Key 与代理，无需重复填写密钥。</span>
        </div>`;
      }
      return `
        <label class="qw-field ${isHighlight ? "is-highlight" : ""}" data-qw-field-wrap="auth_token">
          <span>${esc(field.label)}${reqMark}</span>
          <input type="password" data-qw-field="auth_token" autocomplete="new-password"
            placeholder="${esc(d.has_token ? "已保存密钥；留空保留当前值，输入新值可更换" : field.placeholder)}"
            value="${esc(d.auth_token)}">
          ${field.hint ? `<span class="qw-field-hint">${esc(field.hint)}</span>` : ""}
        </label>
        ${d.has_token ? `<label class="qw-check-row"><input type="checkbox" data-qw-check="clear_auth_token" ${d.clear_auth_token ? "checked" : ""}><span>清除已保存的令牌 / 密钥</span></label>` : ""}
      `;
    }

    if (field.kind === "select") {
      return `
        <label class="qw-field ${isHighlight ? "is-highlight" : ""}" data-qw-field-wrap="${esc(field.name)}">
          <span>${esc(field.label)}${reqMark}</span>
          <select data-qw-field="${esc(field.name)}">
            ${(field.options || []).map(opt => `<option value="${esc(opt.value)}" ${String(val) === String(opt.value) ? "selected" : ""}>${esc(opt.label)}</option>`).join("")}
          </select>
          ${field.hint ? `<span class="qw-field-hint">${esc(field.hint)}</span>` : ""}
        </label>
      `;
    }

    if (field.kind === "textarea") {
      return `
        <label class="qw-field ${isHighlight ? "is-highlight" : ""}" data-qw-field-wrap="${esc(field.name)}">
          <span>${esc(field.label)}${reqMark}</span>
          <textarea data-qw-field="${esc(field.name)}" spellcheck="false" placeholder="${esc(field.placeholder)}">${esc(val)}</textarea>
          ${field.hint ? `<span class="qw-field-hint">${esc(field.hint)}</span>` : ""}
        </label>
      `;
    }

    return `
      <label class="qw-field ${isHighlight ? "is-highlight" : ""}" data-qw-field-wrap="${esc(field.name)}">
        <span>${esc(field.label)}${reqMark}</span>
        <input type="${field.kind === "number" ? "number" : "text"}" step="any" data-qw-field="${esc(field.name)}"
          placeholder="${esc(field.placeholder)}" value="${esc(val)}" autocomplete="off">
        ${field.hint ? `<span class="qw-field-hint">${esc(field.hint)}</span>` : ""}
      </label>
    `;
  }

  function renderTestResultMarkup(w, { fresh = false } = {}) {
    if (!w) return "";
    if (w.testing) {
      return `<div class="qw-notice" role="status" aria-live="polite"><span>正在测试站点连通性与读取余额…</span></div>`;
    }
    const snap = w.testResult;
    if (!snap) return "";
    const freshCls = fresh ? " is-fresh" : "";
    if (snap.ok) {
      let previewAmount = "不限额";
      if (snap.remaining != null) {
        const m = formatMoney(snap.remaining, snap.display_type, snap.symbol);
        previewAmount = m.unit === "¥" || m.unit === "$" ? m.unit + m.num : `${m.num} ${m.unit}`.trim();
      } else if (snap.used != null) {
        const usedM = formatMoney(snap.used, snap.display_type, snap.symbol);
        const usedStr = usedM.unit === "¥" || usedM.unit === "$" ? usedM.unit + usedM.num : `${usedM.num} ${usedM.unit}`.trim();
        previewAmount = `不限额（已用 ${usedStr}）`;
      }
      return `
        <div class="qw-test-preview${freshCls}" role="status" aria-live="polite">
          <div><strong>连通性测试通过</strong>${snap.latency_ms ? ` · <span class="qw-num">${snap.latency_ms} ms</span>` : ""}</div>
          <div class="qw-test-preview-amount qw-num">${esc(previewAmount)}</div>
          <div>${[snap.group_name ? `分组/套餐：${snap.group_name}` : "", snap.account_name ? `账号：${snap.account_name}` : "", snap.extra || ""].filter(Boolean).map(esc).join(" · ")}</div>
          ${snap.last_used_at ? `<div class="qw-num">最近消费：${esc(formatShortTime(snap.last_used_at))}${snap.last_used_model ? " · " + esc(snap.last_used_model) : ""}</div>` : ""}
        </div>
      `;
    }
    const isSelfHealed = snap.error_code === "requires_user_id";
    return `
      <div class="qw-test-preview ${isSelfHealed ? "is-warn" : "is-fail"}${freshCls}" role="alert">
        <strong>${esc(isSelfHealed ? `检测到该站点要求携带 ${snap.detected_header || "New-Api-User"}，已为您展开「站点用户 ID」` : (snap.error || "测试未通过"))}</strong>
        ${snap.repair_hint ? `<div>${esc(snap.repair_hint)}</div>` : ""}
      </div>
    `;
  }

  function renderLoginNoticeMarkup(w) {
    if (!w?.loginMsg) return "";
    return `<div class="qw-notice ${w.loginBad ? "is-error" : "is-ok"}" role="status"><span>${esc(w.loginMsg)}</span></div>`;
  }

  function renderProtocolBannerMarkup(adapter, adapterId) {
    return `
      <div class="qw-notice">
        <span>当前协议：<strong>${esc(adapter?.name || adapterId)}</strong></span>
        <button type="button" class="qw-btn small" data-qw-step="1">修改类型 / 来源</button>
      </div>
      ${adapter?.auth_guide ? `<div class="qw-field-hint">${esc(adapter.auth_guide)}</div>` : ""}
    `;
  }

  function renderWizardStep2(ctx) {
    const q = ctx.q;
    const w = q.wizard;
    const d = w.draft;
    const adapter = adapterById(q, d.adapter_id);
    w._adapter = adapter;
    const allFields = adapter?.fields || [];
    const primaryFields = allFields.filter(f => f.group !== "advanced");
    const advancedFields = allFields.filter(f => f.group === "advanced");
    const supportsLogin = Boolean(adapter?.capabilities?.supports_one_time_login);

    return `
      <div data-qw-protocol-banner>${renderProtocolBannerMarkup(adapter, d.adapter_id)}</div>

      <label class="qw-field" data-qw-field-wrap="name">
        <span>站点名称 *</span>
        <input type="text" data-qw-field="name" maxlength="64" placeholder="例如：主力中转站 / DeepSeek 官方" value="${esc(d.name)}">
      </label>

      ${d.source_mode === "manual" && d.adapter_id !== "custom_script" ? `
        <label class="qw-field" data-qw-field-wrap="base_url">
          <span>站点地址 *</span>
          <input type="url" data-qw-field="base_url" maxlength="2048" placeholder="https://api.example.com" value="${esc(d.base_url)}">
        </label>
      ` : ""}

      <div data-qw-primary-fields>${primaryFields.map(f => renderDynamicField(f, d, w)).join("")}</div>

      ${supportsLogin ? `
        <details class="qw-details" data-qw-login-details ${w.loginOpen ? "open" : ""}>
          <summary data-qw-toggle-login>没有手动找令牌？用账号密码一次性获取（密码绝不保存）</summary>
          <div class="qw-details-body">
            <div class="qw-field-grid">
              <label class="qw-field">
                <span>站点用户名</span>
                <input type="text" data-qw-login-field="loginUser" value="${esc(w.loginUser)}" autocomplete="off" placeholder="登录用户名">
              </label>
              <label class="qw-field">
                <span>站点密码（仅内存使用一次）</span>
                <input type="password" data-qw-login-field="loginPass" value="${esc(w.loginPass)}" autocomplete="new-password" placeholder="换取后立即销毁">
              </label>
            </div>
            <div class="qw-toolbar">
              <button type="button" class="qw-btn small soft" data-qw-exchange-token ${w.loginBusy ? "disabled" : ""}>${w.loginBusy ? "正在登录换取…" : "一次性换取令牌并读取 UID"}</button>
              <span class="qw-field-hint">若站点开启了 2FA 或 Turnstile，请手动复制系统访问令牌。</span>
            </div>
          </div>
        </details>
      ` : ""}
      <div class="qw-login-slot" data-qw-login-slot>${renderLoginNoticeMarkup(w)}</div>

      <details class="qw-details" data-qw-advanced-details ${w.advancedOpen ? "open" : ""}>
        <summary data-qw-toggle-advanced>高级与告警设置（换算覆盖 / 刷新间隔 / 告警阈值 / 代理 / 余额感知）</summary>
        <div class="qw-details-body">
          <div data-qw-advanced-fields>${advancedFields.map(f => renderDynamicField(f, d, w)).join("")}</div>
          ${(d.adapter_id === "newapi" || d.adapter_id === "newapi_user_id") ? `
            <div class="qw-field-grid">
              <label class="qw-field">
                <span>自定义币种折算法币</span>
                <select data-qw-field="fiat_currency">
                  <option value="" ${!d.fiat_currency ? "selected" : ""}>自动识别（优先按充值渠道）</option>
                  <option value="CNY" ${d.fiat_currency === "CNY" ? "selected" : ""}>人民币 (CNY ¥)</option>
                  <option value="USD" ${d.fiat_currency === "USD" ? "selected" : ""}>美元 (USD $)</option>
                </select>
              </label>
              <label class="qw-field">
                <span>每 1 基准单位折合法币（可选）</span>
                <input type="number" min="0" step="any" data-qw-field="fiat_per_base_unit" placeholder="留空自动读取站内充值价（如 0.8 对应 8 折）" value="${esc(d.fiat_per_base_unit)}">
                <span class="qw-field-hint">例：10 🍪 = ¥1 站默认填留空；若享 8 折充值可填 0.8（即 12.5 🍪 = ¥1）。</span>
              </label>
            </div>
          ` : ""}
          <div class="qw-field-grid">
            <label class="qw-field">
              <span>自动刷新间隔（秒）</span>
              <input type="number" min="60" max="86400" step="30" data-qw-field="interval_sec" value="${esc(d.interval_sec)}">
            </label>
            <label class="qw-field">
              <span>偏低比例阈值（%）</span>
              <input type="number" min="0" max="100" step="1" data-qw-field="low_percent" value="${esc(d.low_percent)}">
            </label>
            <label class="qw-field">
              <span>偏低金额阈值（可选）</span>
              <input type="number" min="0" step="any" data-qw-field="low_threshold" placeholder="留空按比例判定" value="${esc(d.low_threshold)}">
            </label>
            <label class="qw-field">
              <span>告急金额阈值（可选）</span>
              <input type="number" min="0" step="any" data-qw-field="critical_threshold" placeholder="低于此值标红告急" value="${esc(d.critical_threshold)}">
            </label>
          </div>
          <label class="qw-field">
            <span>站点专用代理（可选）</span>
            <input type="password" data-qw-field="proxy_url" autocomplete="new-password"
              placeholder="${esc(d.has_proxy ? "已保存代理；留空保留，输入可更换" : "http://host:port 或 socks5://user:pass@host:port")}"
              value="${esc(d.proxy_url)}">
          </label>
          ${d.has_proxy ? `<label class="qw-check-row"><input type="checkbox" data-qw-check="clear_proxy_url" ${d.clear_proxy_url ? "checked" : ""}><span>清除已保存代理，改为直连</span></label>` : ""}
          <label class="qw-check-row">
            <input type="checkbox" data-qw-check="allow_http" ${d.allow_http ? "checked" : ""}>
            <span>允许局域网 HTTP / 内网地址</span>
          </label>
          <label class="qw-check-row">
            <input type="checkbox" data-qw-check="enabled" ${d.enabled ? "checked" : ""}>
            <span>启用该站点额度监测</span>
          </label>
          <label class="qw-check-row">
            <input type="checkbox" data-qw-check="participate_in_awareness" ${d.participate_in_awareness ? "checked" : ""}>
            <span>参与陪伴人格「余额感知」（低余额主动温柔提醒 / 私聊查询）</span>
          </label>
        </div>
      </details>

      <div class="qw-test-result-slot" data-qw-test-result-slot>${renderTestResultMarkup(w)}</div>
    `;
  }

  function renderWizardFooterMarkup(w) {
    if (w.step === 1) {
      return `
        <button type="button" class="qw-btn" data-qw-close-wizard>取消</button>
        <div class="qw-modal-foot-right">
          <button type="button" class="qw-btn primary" data-qw-step="2">下一步：填写配置 →</button>
        </div>
      `;
    }
    const busy = Boolean(w.testing || w.saving);
    return `
      <button type="button" class="qw-btn" data-qw-step="1" ${w.saving ? "disabled" : ""}>← 上一步</button>
      <div class="qw-modal-foot-right">
        <button type="button" class="qw-btn soft" data-qw-test-draft ${busy ? "disabled" : ""}>${w.testing ? "测试中…" : "测试连通性"}</button>
        <button type="button" class="qw-btn primary" data-qw-save-station ${busy ? "disabled" : ""}>${w.saving ? "保存并刷新中…" : "保存站点"}</button>
      </div>
    `;
  }

  function renderWizardErrorMarkup(w) {
    if (!w?.error) return "";
    return `<div class="qw-notice is-error" role="alert"><span>${esc(w.error)}</span></div>`;
  }

  function wizardTitleFor(w) {
    return w.mode === "edit" ? `编辑站点 · ${w.draft.name || w.draft.id}` : "添加监测站点";
  }

  function renderWizardModal(ctx) {
    const w = ctx.q.wizard;
    if (!w) return "";
    const title = wizardTitleFor(w);
    return `
      <div class="qw-modal-backdrop" data-qw-modal-backdrop>
        <section class="qw-modal" role="dialog" aria-modal="true" aria-label="${esc(title)}">
          <header class="qw-modal-head">
            <h4 data-qw-modal-title>${esc(title)}</h4>
            <button type="button" class="qw-btn small" data-qw-close-wizard aria-label="关闭">关闭</button>
          </header>
          <div class="qw-steps" aria-label="配置步骤">
            <span class="qw-step ${w.step === 1 ? "is-active" : "is-done"}" data-qw-step-ind="1"><span class="qw-step-num">1</span>选站与智能识别</span>
            <span class="qw-step-sep"></span>
            <span class="qw-step ${w.step === 2 ? "is-active" : ""}" data-qw-step-ind="2"><span class="qw-step-num">2</span>精简配置与连通测试</span>
          </div>
          <div class="qw-modal-body" data-qw-modal-body>
            <div class="qw-error-slot" data-qw-modal-error>${renderWizardErrorMarkup(w)}</div>
            <div class="qw-step-content" data-qw-step-content>${w.step === 1 ? renderWizardStep1(ctx) : renderWizardStep2(ctx)}</div>
          </div>
          <footer class="qw-modal-foot" data-qw-modal-foot>${renderWizardFooterMarkup(w)}</footer>
        </section>
      </div>
    `;
  }

  /* String-HTML fallback for lightweight non-DOM test harnesses */
  function renderPane(ctx) {
    const q = ctx.q;
    if (q.loading && !q.loaded) {
      return `<div class="qw-root"><div class="qw-main"><section class="qw-hero"><p class="qw-subtitle">正在读取多站点额度看板…</p></section></div><div class="qw-modal-host" aria-hidden="true"></div></div>`;
    }
    return `
      <div class="qw-root">
        <div class="qw-main">
          ${renderHero(ctx)}
          <section class="qw-grid" aria-label="额度站点列表">${renderGridContent(ctx)}</section>
        </div>
        <div class="qw-modal-host" aria-hidden="${q.wizard ? "false" : "true"}">${renderWizardModal(ctx)}</div>
      </div>
    `;
  }

  /* ---------- Persistent Live DOM Patchers (ZERO Flicker / ZERO Modal Rebuild) ---------- */

  function syncMainDom(ctx, { includeAwareness = true } = {}) {
    const dom = getLiveDom(ctx);
    if (!dom) {
      ctx.render();
      return;
    }
    const q = ctx.q;
    if (q.loading && !q.loaded) {
      dom.heroSlot.innerHTML = `<section class="qw-hero"><p class="qw-subtitle">正在读取多站点额度看板…</p></section>`;
      dom.gridEl.innerHTML = "";
      ctx.syncTabBadge?.();
      return;
    }

    // Preserve awareness slot DOM if open and only data refreshed!
    let coreEl = dom.heroSlot.querySelector?.("[data-qw-hero-core]");
    let awSlotEl = dom.heroSlot.querySelector?.("[data-qw-awareness-slot]");
    if (!coreEl || !awSlotEl) {
      dom.heroSlot.innerHTML = renderHero(ctx);
    } else {
      coreEl.innerHTML = renderHeroCore(ctx);
      if (includeAwareness) {
        const awHtml = q.awarenessOpen ? renderAwarenessPanel(ctx) : "";
        if (awSlotEl.innerHTML !== awHtml) awSlotEl.innerHTML = awHtml;
      }
    }

    // Reconcile station cards in place by ID when list order matches (zero reflow flicker!)
    const stations = q.data?.stations || [];
    const existingCards = [...(dom.gridEl.children || [])].filter(el => el.dataset?.qwCard);
    const sameList = stations.length > 0
      && existingCards.length === stations.length
      && stations.every((st, idx) => existingCards[idx].dataset.qwCard === st.id);

    if (sameList) {
      for (let i = 0; i < stations.length; i++) {
        const parts = renderStationCardParts(ctx, stations[i]);
        const cardEl = existingCards[i];
        if (cardEl.className !== parts.className) cardEl.className = parts.className;
        if (cardEl.innerHTML !== parts.innerHtml) cardEl.innerHTML = parts.innerHtml;
      }
    } else {
      dom.gridEl.innerHTML = renderGridContent(ctx);
    }

    ctx.syncTabBadge?.();
  }

  function patchAwarenessDom(ctx) {
    const dom = getLiveDom(ctx);
    if (!dom) {
      ctx.render();
      return;
    }
    const q = ctx.q;
    const awSlotEl = dom.heroSlot.querySelector?.("[data-qw-awareness-slot]");
    if (!awSlotEl) {
      syncMainDom(ctx, { includeAwareness: true });
      return;
    }
    awSlotEl.innerHTML = q.awarenessOpen ? renderAwarenessPanel(ctx) : "";
    const toggleBtn = dom.heroSlot.querySelector?.("[data-qw-toggle-awareness]");
    if (toggleBtn) toggleBtn.textContent = q.awarenessOpen ? "收起策略设置" : "配置感知策略";
  }

  function patchRefreshButtons(ctx, stationId = "") {
    const dom = getLiveDom(ctx);
    if (!dom) {
      ctx.render();
      return;
    }
    const q = ctx.q;
    const allBtn = dom.heroSlot.querySelector?.("[data-qw-refresh-all]");
    if (allBtn) {
      allBtn.disabled = Boolean(q.refreshingAll || q.loading);
      allBtn.textContent = q.refreshingAll ? "刷新中…" : "立即刷新";
    }
    for (const st of q.data?.stations || []) {
      if (stationId && st.id !== stationId) continue;
      const btn = dom.gridEl.querySelector?.(`[data-qw-refresh-one="${st.id}"]`);
      if (!btn) continue;
      const busy = q.refreshingId === st.id || (q.refreshingAll && st.enabled);
      btn.disabled = Boolean(busy || !st.enabled);
      btn.textContent = busy ? "刷新中…" : "刷新";
    }
  }

  function patchSingleCardDom(ctx, stationId) {
    const dom = getLiveDom(ctx);
    if (!dom) {
      ctx.render();
      return;
    }
    const st = (ctx.q.data?.stations || []).find(item => item.id === stationId);
    const cardEl = dom.gridEl.querySelector?.(`[data-qw-card="${stationId}"]`);
    if (!st || !cardEl) {
      syncMainDom(ctx, { includeAwareness: false });
      return;
    }
    const parts = renderStationCardParts(ctx, st);
    cardEl.className = parts.className;
    cardEl.innerHTML = parts.innerHtml;
  }

  function patchHydratedLogos(ctx, updatedIds) {
    const dom = getLiveDom(ctx);
    if (!dom) {
      ctx.render();
      return;
    }
    const q = ctx.q;
    const snaps = q.data?.snapshots || {};
    for (const sid of updatedIds) {
      const st = (q.data?.stations || []).find(item => item.id === sid);
      const avatarEl = dom.gridEl.querySelector?.(`[data-qw-avatar="${sid}"]`);
      if (!st || !avatarEl) continue;
      const av = avatarMarkupFor(st, snaps[sid]);
      avatarEl.classList?.toggle?.("has-logo", av.hasLogo);
      avatarEl.classList?.toggle?.("is-brand", av.isBrand);
      avatarEl.title = av.title;
      avatarEl.innerHTML = av.html;
    }
  }

  function clearModalHostNow(dom) {
    if (!dom) return;
    clearDomTimers(dom);
    dom.modal = null;
    dom.modalHost.innerHTML = "";
    dom.modalHost.setAttribute("aria-hidden", "true");
  }

  function closeModalDom(ctx) {
    const q = ctx.q;
    q.wizard = null;
    const dom = getLiveDom(ctx);
    if (!dom) {
      ctx.render();
      return;
    }
    if (!dom.modal) {
      clearModalHostNow(dom);
      return;
    }
    const { backdropEl, modalEl } = dom.modal;
    if (typeof document !== "undefined" && backdropEl.contains?.(document.activeElement)) {
      try { document.activeElement.blur?.(); } catch {}
    }
    dom.modalHost.setAttribute("aria-hidden", "true");
    if (!supportsCssExitAnimation()) {
      clearModalHostNow(dom);
      return;
    }
    clearDomTimers(dom);
    backdropEl.classList.remove("is-entering");
    modalEl.classList.remove("is-entering");
    backdropEl.classList.add("is-leaving");
    modalEl.classList.add("is-leaving");
    dom.modal = null;
    dom.closeTimer = setTimeout(() => {
      dom.closeTimer = null;
      if (!ctx.q.wizard) {
        dom.modalHost.innerHTML = "";
      }
    }, 170);
  }

  function syncModalStepIndicators(m, step) {
    m.step1Ind.className = `qw-step ${step === 1 ? "is-active" : "is-done"}`;
    m.step2Ind.className = `qw-step ${step === 2 ? "is-active" : ""}`;
  }

  function mountFreshModalDom(ctx, dom) {
    const w = ctx.q.wizard;
    clearDomTimers(dom);
    dom.modalHost.innerHTML = "";

    const title = wizardTitleFor(w);
    const backdropEl = document.createElement("div");
    backdropEl.className = isReducedMotion() ? "qw-modal-backdrop" : "qw-modal-backdrop is-entering";
    backdropEl.setAttribute("data-qw-modal-backdrop", "");

    const modalEl = document.createElement("section");
    modalEl.className = isReducedMotion() ? "qw-modal" : "qw-modal is-entering";
    modalEl.setAttribute("role", "dialog");
    modalEl.setAttribute("aria-modal", "true");
    modalEl.setAttribute("aria-label", title);

    const headEl = document.createElement("header");
    headEl.className = "qw-modal-head";
    const titleEl = document.createElement("h4");
    titleEl.setAttribute("data-qw-modal-title", "");
    titleEl.textContent = title;
    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "qw-btn small";
    closeBtn.setAttribute("data-qw-close-wizard", "");
    closeBtn.setAttribute("aria-label", "关闭");
    closeBtn.textContent = "关闭";
    headEl.appendChild(titleEl);
    headEl.appendChild(closeBtn);

    const stepsEl = document.createElement("div");
    stepsEl.className = "qw-steps";
    stepsEl.setAttribute("aria-label", "配置步骤");
    const step1Ind = document.createElement("span");
    step1Ind.setAttribute("data-qw-step-ind", "1");
    step1Ind.innerHTML = `<span class="qw-step-num">1</span>选站与智能识别`;
    const stepSep = document.createElement("span");
    stepSep.className = "qw-step-sep";
    const step2Ind = document.createElement("span");
    step2Ind.setAttribute("data-qw-step-ind", "2");
    step2Ind.innerHTML = `<span class="qw-step-num">2</span>精简配置与连通测试`;
    stepsEl.appendChild(step1Ind);
    stepsEl.appendChild(stepSep);
    stepsEl.appendChild(step2Ind);

    const bodyEl = document.createElement("div");
    bodyEl.className = "qw-modal-body";
    bodyEl.setAttribute("data-qw-modal-body", "");

    const errorSlot = document.createElement("div");
    errorSlot.className = "qw-error-slot";
    errorSlot.setAttribute("data-qw-modal-error", "");
    errorSlot.innerHTML = renderWizardErrorMarkup(w);

    const stepContent = document.createElement("div");
    stepContent.className = "qw-step-content";
    stepContent.setAttribute("data-qw-step-content", "");
    stepContent.innerHTML = w.step === 1 ? renderWizardStep1(ctx) : renderWizardStep2(ctx);

    bodyEl.appendChild(errorSlot);
    bodyEl.appendChild(stepContent);

    const footEl = document.createElement("footer");
    footEl.className = "qw-modal-foot";
    footEl.setAttribute("data-qw-modal-foot", "");
    footEl.innerHTML = renderWizardFooterMarkup(w);

    modalEl.appendChild(headEl);
    modalEl.appendChild(stepsEl);
    modalEl.appendChild(bodyEl);
    modalEl.appendChild(footEl);
    backdropEl.appendChild(modalEl);
    dom.modalHost.appendChild(backdropEl);
    dom.modalHost.setAttribute("aria-hidden", "false");

    const m = {
      openId: w.openId,
      backdropEl,
      modalEl,
      titleEl,
      stepsEl,
      step1Ind,
      step2Ind,
      bodyEl,
      errorSlot,
      stepContent,
      footEl,
      renderedStep: w.step,
      renderedAdapterId: w.draft.adapter_id,
    };
    syncModalStepIndicators(m, w.step);
    dom.modal = m;

    if (!isReducedMotion()) {
      const clearEnter = () => {
        if (dom.enterTimer) { clearTimeout(dom.enterTimer); dom.enterTimer = null; }
        backdropEl.classList.remove("is-entering");
        modalEl.classList.remove("is-entering");
      };
      modalEl.addEventListener?.("animationend", clearEnter, { once: true });
      dom.enterTimer = setTimeout(clearEnter, 280);
    }
  }

  function syncModalDom(ctx) {
    const dom = getLiveDom(ctx);
    if (!dom) {
      ctx.render();
      return;
    }
    const w = ctx.q.wizard;
    if (!w) {
      if (dom.modal) closeModalDom(ctx);
      return;
    }
    if (!dom.modal || dom.modal.openId !== w.openId) {
      mountFreshModalDom(ctx, dom);
    }
  }

  function patchModalTitle(ctx) {
    const dom = getLiveDom(ctx);
    const w = ctx.q.wizard;
    if (!dom?.modal || !w) return;
    const title = wizardTitleFor(w);
    dom.modal.titleEl.textContent = title;
    dom.modal.modalEl.setAttribute("aria-label", title);
  }

  function patchModalError(ctx) {
    const dom = getLiveDom(ctx);
    if (!dom) {
      ctx.render();
      return;
    }
    if (!dom.modal || !ctx.q.wizard) return;
    dom.modal.errorSlot.innerHTML = renderWizardErrorMarkup(ctx.q.wizard);
  }

  function patchModalFooterButtons(ctx) {
    const dom = getLiveDom(ctx);
    if (!dom) {
      ctx.render();
      return;
    }
    const w = ctx.q.wizard;
    if (!dom.modal || !w) return;
    const foot = dom.modal.footEl;
    if (w.step === 2) {
      const busy = Boolean(w.testing || w.saving);
      const backBtn = foot.querySelector?.('[data-qw-step="1"]');
      const testBtn = foot.querySelector?.("[data-qw-test-draft]");
      const saveBtn = foot.querySelector?.("[data-qw-save-station]");
      if (backBtn) backBtn.disabled = Boolean(w.saving);
      if (testBtn) {
        testBtn.disabled = busy;
        testBtn.textContent = w.testing ? "测试中…" : "测试连通性";
      }
      if (saveBtn) {
        saveBtn.disabled = busy;
        saveBtn.textContent = w.saving ? "保存并刷新中…" : "保存站点";
      }
    }
  }

  function patchModalStep(ctx) {
    const dom = getLiveDom(ctx);
    if (!dom) {
      ctx.render();
      return;
    }
    const w = ctx.q.wizard;
    if (!dom.modal || !w) return;
    const m = dom.modal;
    syncModalStepIndicators(m, w.step);
    m.errorSlot.innerHTML = renderWizardErrorMarkup(w);
    m.stepContent.innerHTML = w.step === 1 ? renderWizardStep1(ctx) : renderWizardStep2(ctx);
    m.footEl.innerHTML = renderWizardFooterMarkup(w);
    m.bodyEl.scrollTop = 0;
    m.renderedStep = w.step;
    m.renderedAdapterId = w.draft.adapter_id;
    if (!isReducedMotion()) {
      m.stepContent.classList.remove("is-switching");
      void m.stepContent.offsetWidth;
      m.stepContent.classList.add("is-switching");
      if (dom.switchTimer) clearTimeout(dom.switchTimer);
      dom.switchTimer = setTimeout(() => {
        dom.switchTimer = null;
        m.stepContent.classList.remove("is-switching");
      }, 220);
    }
  }

  function patchStep1Source(ctx) {
    const dom = getLiveDom(ctx);
    if (!dom) {
      ctx.render();
      return;
    }
    const w = ctx.q.wizard;
    if (!dom.modal || !w) return;
    const content = dom.modal.stepContent;
    content.querySelectorAll?.("[data-qw-source-mode]").forEach(btn => {
      const active = btn.dataset.qwSourceMode === w.draft.source_mode;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-selected", String(active));
    });
    const sourceSlot = content.querySelector?.("[data-qw-source-slot]");
    if (sourceSlot) sourceSlot.innerHTML = renderWizardStep1Source(ctx);
    const detectSlot = content.querySelector?.("[data-qw-detect-slot]");
    if (detectSlot) detectSlot.innerHTML = renderWizardDetectNotice(w);
    dom.modal.errorSlot.innerHTML = renderWizardErrorMarkup(w);
  }

  function patchAdapterSelection(ctx) {
    const dom = getLiveDom(ctx);
    if (!dom) {
      ctx.render();
      return;
    }
    const q = ctx.q;
    const w = q.wizard;
    if (!dom.modal || !w) return;
    const content = dom.modal.stepContent;
    content.querySelectorAll?.("[data-qw-pick-adapter]").forEach(chip => {
      chip.classList.toggle("is-active", chip.dataset.qwPickAdapter === w.draft.adapter_id);
    });
    const summaryEl = content.querySelector?.("[data-qw-adapter-summary]");
    if (summaryEl) summaryEl.textContent = adapterById(q, w.draft.adapter_id)?.summary || "";
    dom.modal.errorSlot.innerHTML = renderWizardErrorMarkup(w);
  }

  function patchDetectSlot(ctx) {
    const dom = getLiveDom(ctx);
    if (!dom) {
      ctx.render();
      return;
    }
    const q = ctx.q;
    const w = q.wizard;
    if (!dom.modal || !w) return;
    const content = dom.modal.stepContent;
    const detectBtn = content.querySelector?.("[data-qw-detect]");
    if (detectBtn) {
      detectBtn.disabled = Boolean(w.detecting);
      detectBtn.textContent = w.detecting ? "识别中…" : "智能识别";
    }
    const detectSlot = content.querySelector?.("[data-qw-detect-slot]");
    if (detectSlot) detectSlot.innerHTML = renderWizardDetectNotice(w);
    const urlInput = content.querySelector?.('[data-qw-field="base_url"]');
    if (urlInput && w.draft.source_mode === "manual" && urlInput.value !== w.draft.base_url) {
      urlInput.value = w.draft.base_url;
    }
    patchAdapterSelection(ctx);
  }

  function patchFieldHighlights(ctx) {
    const dom = getLiveDom(ctx);
    const w = ctx.q.wizard;
    if (!dom?.modal || !w) return;
    dom.modal.stepContent.querySelectorAll?.("[data-qw-field-wrap]").forEach(wrap => {
      wrap.classList.toggle("is-highlight", Boolean(w.highlightField && wrap.dataset.qwFieldWrap === w.highlightField));
    });
  }

  function patchTestResultSlot(ctx, { fresh = false } = {}) {
    const dom = getLiveDom(ctx);
    if (!dom) {
      ctx.render();
      return;
    }
    const w = ctx.q.wizard;
    if (!dom.modal || !w) return;
    const slot = dom.modal.stepContent.querySelector?.("[data-qw-test-result-slot]");
    if (slot) slot.innerHTML = renderTestResultMarkup(w, { fresh });
    patchFieldHighlights(ctx);
  }

  function patchDynamicFieldsAfterSelfHeal(ctx) {
    const dom = getLiveDom(ctx);
    if (!dom) {
      ctx.render();
      return;
    }
    const q = ctx.q;
    const w = q.wizard;
    if (!dom.modal || !w) return;
    const prevScroll = dom.modal.bodyEl.scrollTop;
    const d = w.draft;
    const adapter = adapterById(q, d.adapter_id);
    w._adapter = adapter;
    const allFields = adapter?.fields || [];
    const primaryFields = allFields.filter(f => f.group !== "advanced");
    const advancedFields = allFields.filter(f => f.group === "advanced");

    const bannerEl = dom.modal.stepContent.querySelector?.("[data-qw-protocol-banner]");
    if (bannerEl) bannerEl.innerHTML = renderProtocolBannerMarkup(adapter, d.adapter_id);
    const primarySlot = dom.modal.stepContent.querySelector?.("[data-qw-primary-fields]");
    if (primarySlot) primarySlot.innerHTML = primaryFields.map(f => renderDynamicField(f, d, w)).join("");
    const advSlot = dom.modal.stepContent.querySelector?.("[data-qw-advanced-fields]");
    if (advSlot) advSlot.innerHTML = advancedFields.map(f => renderDynamicField(f, d, w)).join("");
    dom.modal.renderedAdapterId = d.adapter_id;
    dom.modal.bodyEl.scrollTop = prevScroll;
  }

  function patchLoginSlot(ctx) {
    const dom = getLiveDom(ctx);
    if (!dom) {
      ctx.render();
      return;
    }
    const w = ctx.q.wizard;
    if (!dom.modal || !w) return;
    const content = dom.modal.stepContent;
    const exchBtn = content.querySelector?.("[data-qw-exchange-token]");
    if (exchBtn) {
      exchBtn.disabled = Boolean(w.loginBusy);
      exchBtn.textContent = w.loginBusy ? "正在登录换取…" : "一次性换取令牌并读取 UID";
    }
    const slot = content.querySelector?.("[data-qw-login-slot]");
    if (slot) slot.innerHTML = renderLoginNoticeMarkup(w);
    const passInput = content.querySelector?.('[data-qw-login-field="loginPass"]');
    if (passInput && passInput.value !== w.loginPass) passInput.value = w.loginPass;
    const tokenInput = content.querySelector?.('[data-qw-field="auth_token"]');
    if (tokenInput && w.draft.auth_token && tokenInput.value !== w.draft.auth_token) {
      tokenInput.value = w.draft.auth_token;
    }
    const uidInput = content.querySelector?.('[data-qw-field="user_id"]');
    if (uidInput && w.draft.user_id && uidInput.value !== w.draft.user_id) {
      uidInput.value = w.draft.user_id;
    }
    const clearTokenCk = content.querySelector?.('[data-qw-check="clear_auth_token"]');
    if (clearTokenCk) clearTokenCk.checked = Boolean(w.draft.clear_auth_token);
    const detailsEl = content.querySelector?.("[data-qw-login-details]");
    if (detailsEl && !w.loginOpen) {
      detailsEl.open = false;
      detailsEl.removeAttribute?.("open");
    }
  }

  function syncDom(ctx, mountEl, externalModalHost = null) {
    ensureLiveShell(ctx, mountEl, externalModalHost);
    syncMainDom(ctx, { includeAwareness: true });
    syncModalDom(ctx);
  }

  /* ---------- Async Workflows ---------- */

  function scheduleAutoRefresh(ctx) {
    const q = ctx.q;
    clearAutoTimer(q);
    if (!q.autoRefresh || !q.data) return;
    const enabled = (q.data.stations || []).filter(s => s.enabled);
    if (!enabled.length) return;
    const minSec = Math.max(60, Math.min(...enabled.map(s => Number(s.interval_sec) || 300)));
    const ticket = ctx.ticket();
    q.autoTimer = setTimeout(() => {
      if (!ctx.isCurrent(ticket) || !q.autoRefresh) return;
      if (ctx.state?.tab !== "quota" || (typeof document !== "undefined" && document.hidden)) {
        scheduleAutoRefresh(ctx);
        return;
      }
      void refreshStations(ctx, "", false);
    }, minSec * 1000);
  }

  async function hydrateLogos(ctx) {
    const q = ctx.q;
    const snaps = q.data?.snapshots || {};
    const ticket = ctx.ticket();
    const pending = [];
    const updatedIds = [];
    for (const st of q.data?.stations || []) {
      const snap = snaps[st.id];
      if (!snap?.logo_version) continue;
      const cacheKey = `${st.id}:${snap.logo_version}`;
      if (iconCache.has(cacheKey)) continue;
      pending.push(
        ctx.api("GET", `${ctx.path}/quota/icon`, { id: st.id, v: snap.logo_version }, 8000)
          .then(res => {
            if (res?.available && isSafeIconDataUrl(res?.data_url)) iconCache.set(cacheKey, res.data_url);
            else iconCache.set(cacheKey, "");
            updatedIds.push(st.id);
          })
          .catch(() => {})
      );
    }
    if (pending.length) {
      await Promise.all(pending);
      if (ctx.isCurrent(ticket) && updatedIds.length) patchHydratedLogos(ctx, updatedIds);
    }
  }

  async function refresh(ctx) {
    const q = ctx.q;
    if (q.loading) return;
    const ticket = ctx.ticket();
    q.loading = true;
    q.notice = "";
    q.noticeTone = "";
    syncMainDom(ctx, { includeAwareness: false });
    try {
      const data = await ctx.api("GET", `${ctx.path}/quota`, undefined, 15000);
      if (!ctx.isCurrent(ticket)) return;
      q.data = data;
      q.loaded = true;
      q.loading = false;
      syncAwarenessDraft(q);
      if (q.pendingFocus === "awareness") {
        q.awarenessOpen = true;
        q.pendingFocus = "";
      }
      scheduleAutoRefresh(ctx);
      syncMainDom(ctx, { includeAwareness: true });
      void hydrateLogos(ctx);
    } catch (err) {
      if (!ctx.isCurrent(ticket)) return;
      q.loading = false;
      q.notice = ctx.fail(err, "读取多站点额度看板失败，请刷新重试。");
      q.noticeTone = "error";
      syncMainDom(ctx, { includeAwareness: false });
    }
  }

  async function refreshStations(ctx, stationId = "", force = true) {
    const q = ctx.q;
    if (stationId ? q.refreshingId === stationId : q.refreshingAll) return;
    const ticket = ctx.ticket();
    if (stationId) q.refreshingId = stationId;
    else q.refreshingAll = true;
    // Patch ONLY refresh button states — never rebuild cards or touch open modal!
    patchRefreshButtons(ctx, stationId);
    try {
      const data = await ctx.api("POST", `${ctx.path}/quota/refresh`, { station_id: stationId, force }, 25000);
      if (!ctx.isCurrent(ticket)) return;
      q.data = data;
      q.loaded = true;
      if (force) {
        q.notice = stationId ? "已刷新该站点额度。" : "已刷新全部启用站点额度。";
        q.noticeTone = "ok";
      }
      scheduleAutoRefresh(ctx);
      void hydrateLogos(ctx);
    } catch (err) {
      if (!ctx.isCurrent(ticket)) return;
      if (force) {
        q.notice = ctx.fail(err, "刷新站点额度失败。");
        q.noticeTone = "error";
      }
    } finally {
      if (ctx.isCurrent(ticket)) {
        if (stationId) q.refreshingId = "";
        else q.refreshingAll = false;
        // Update ONLY .qw-main (never touches .qw-modal-host or open awareness form!)
        syncMainDom(ctx, { includeAwareness: false });
      }
    }
  }

  function syncAwarenessDraft(q) {
    const aw = q.data?.awareness || { mode: "primary", primary_station_id: "", target_currency: "" };
    const participating = (q.data?.stations || []).filter(s => s.participate_in_awareness).map(s => s.id);
    q.awarenessDraft = {
      mode: aw.mode || "primary",
      primary_station_id: aw.primary_station_id || "",
      target_currency: aw.target_currency || "",
      participating_station_ids: participating,
    };
  }

  async function saveAwareness(ctx) {
    const q = ctx.q;
    if (!q.data || !q.awarenessDraft || q.awarenessSaving) return;
    const ticket = ctx.ticket();
    q.awarenessSaving = true;
    const dom = getLiveDom(ctx);
    if (dom) {
      const btn = dom.heroSlot.querySelector?.("[data-qw-save-awareness]");
      if (btn) { btn.disabled = true; btn.textContent = "保存中…"; }
    } else {
      ctx.render();
    }
    try {
      const data = await ctx.api("POST", `${ctx.path}/quota/awareness`, {
        edit_token: q.data.edit_token,
        awareness: q.awarenessDraft,
      }, 15000);
      if (!ctx.isCurrent(ticket)) return;
      q.data = data;
      syncAwarenessDraft(q);
      q.awarenessOpen = false;
      q.notice = "已保存陪伴余额感知联动策略并立即生效。";
      q.noticeTone = "ok";
    } catch (err) {
      if (!ctx.isCurrent(ticket)) return;
      q.notice = ctx.fail(err, "保存余额感知策略失败。");
      q.noticeTone = "error";
    } finally {
      if (ctx.isCurrent(ticket)) {
        q.awarenessSaving = false;
        syncMainDom(ctx, { includeAwareness: true });
      }
    }
  }

  function openAwareness(ctx) {
    const q = ctx.q;
    if (!q.data) {
      q.pendingFocus = "awareness";
      return;
    }
    q.awarenessOpen = true;
    syncAwarenessDraft(q);
    patchAwarenessDom(ctx);
    const run = () => {
      const container = getLiveDom(ctx)?.heroSlot || ctx.root();
      const target = container?.querySelector?.('[data-qw-aw-field="primary_station_id"]') || container?.querySelector?.(".qw-awareness-panel");
      target?.scrollIntoView?.({ behavior: isReducedMotion() ? "auto" : "smooth", block: "center" });
      if (target && typeof target.focus === "function") target.focus();
    };
    if (typeof requestAnimationFrame === "function") requestAnimationFrame(run);
    else run();
  }

  async function setPrimaryStation(ctx, stationId) {
    const q = ctx.q;
    if (!q.data || q.awarenessSaving) return;
    const st = (q.data.stations || []).find(item => item.id === stationId);
    if (!st || !st.enabled) return;
    const ticket = ctx.ticket();
    q.awarenessSaving = true;
    const aw = q.data.awareness || {};
    const participating = [...new Set([
      ...(q.data.stations || []).filter(item => item.participate_in_awareness).map(item => item.id),
      stationId,
    ])];
    try {
      const data = await ctx.api("POST", `${ctx.path}/quota/awareness`, {
        edit_token: q.data.edit_token,
        awareness: {
          mode: "primary",
          primary_station_id: stationId,
          target_currency: aw.target_currency || "",
          participating_station_ids: participating,
        },
      }, 15000);
      if (!ctx.isCurrent(ticket)) return;
      q.data = data;
      syncAwarenessDraft(q);
      q.notice = `已将「${st.name}」设为陪伴余额感知主站。`;
      q.noticeTone = "ok";
    } catch (err) {
      if (!ctx.isCurrent(ticket)) return;
      q.notice = ctx.fail(err, "设置主站失败。");
      q.noticeTone = "error";
    } finally {
      if (ctx.isCurrent(ticket)) {
        q.awarenessSaving = false;
        syncMainDom(ctx, { includeAwareness: true });
      }
    }
  }

  async function toggleUnitMode(ctx, stationId) {
    const q = ctx.q;
    if (!q.data) return;
    const st = (q.data.stations || []).find(item => item.id === stationId);
    if (!st) return;
    q.unitSavingIds ||= new Set();
    q.unitPendingModes ||= new Map();
    const prevMode = st.display_unit_mode === "fiat" ? "fiat" : "native";
    const nextMode = prevMode === "fiat" ? "native" : "fiat";
    st.display_unit_mode = nextMode;
    patchSingleCardDom(ctx, stationId);
    q.unitPendingModes.set(stationId, nextMode);
    if (q.unitSavingIds.has(stationId)) return;

    const ticket = ctx.ticket();
    let committedMode = prevMode;
    q.unitSavingIds.add(stationId);
    try {
      while (q.unitPendingModes.has(stationId)) {
        const desiredMode = q.unitPendingModes.get(stationId);
        q.unitPendingModes.delete(stationId);
        const curSt = (q.data?.stations || []).find(item => item.id === stationId);
        if (!curSt) break;
        const payload = {
          ...curSt,
          auth_token: "",
          proxy_url: "",
          display_unit_mode: desiredMode,
        };
        delete payload.has_token;
        delete payload.has_proxy;
        const data = await ctx.api("POST", `${ctx.path}/quota/stations`, {
          edit_token: q.data.edit_token,
          station: payload,
          probe_after_save: false,
        }, 15000);
        if (!ctx.isCurrent(ticket)) return;
        committedMode = desiredMode;
        if (data?.edit_token) q.data.edit_token = data.edit_token;
        if (Array.isArray(data?.stations)) q.data.stations = data.stations;
        if (data?.summary) q.data.summary = data.summary;
        if (q.unitPendingModes.has(stationId)) {
          const queuedMode = q.unitPendingModes.get(stationId);
          const refreshedSt = (q.data?.stations || []).find(item => item.id === stationId);
          if (refreshedSt) refreshedSt.display_unit_mode = queuedMode;
        }
      }
    } catch {
      q.unitPendingModes.delete(stationId);
      if (!ctx.isCurrent(ticket)) return;
      const curSt = (q.data?.stations || []).find(item => item.id === stationId);
      if (curSt) curSt.display_unit_mode = committedMode;
      patchSingleCardDom(ctx, stationId);
    } finally {
      q.unitSavingIds.delete(stationId);
    }
  }

  function openWizard(ctx, { mode = "add", station = null, sourceMode = "manual", step = 1, focusField = "", highlightField = "", overrideAdapter = "", overrideHeader = "" } = {}) {
    const q = ctx.q;
    const draft = station
      ? emptyDraft({
          id: station.id,
          name: station.name,
          adapter_id: overrideAdapter || station.adapter_id || "newapi",
          enabled: station.enabled !== false,
          source_mode: station.source_mode || "manual",
          astrbot_provider_id: station.astrbot_provider_id || "",
          base_url: station.base_url || "",
          allow_http: station.allow_http === true,
          auth_token: "",
          has_token: station.has_token === true,
          clear_auth_token: false,
          user_id: station.user_id || "",
          user_id_header: overrideHeader || station.user_id_header || "New-Api-User",
          custom_script: station.custom_script || DEFAULT_SCRIPT_TEMPLATE,
          proxy_url: "",
          has_proxy: station.has_proxy === true,
          clear_proxy_url: false,
          display_type: station.display_type || "",
          quota_per_unit: station.quota_per_unit ? String(station.quota_per_unit) : "",
          fiat_currency: station.fiat_currency || "",
          fiat_per_base_unit: station.fiat_per_base_unit != null ? String(station.fiat_per_base_unit) : "",
          display_unit_mode: station.display_unit_mode || "native",
          interval_sec: station.interval_sec || 300,
          low_threshold: station.low_threshold != null ? String(station.low_threshold) : "",
          low_percent: station.low_percent != null ? station.low_percent : 10,
          critical_threshold: station.critical_threshold != null ? String(station.critical_threshold) : "",
          participate_in_awareness: station.participate_in_awareness === true,
        })
      : emptyDraft({
          source_mode: sourceMode,
          participate_in_awareness: !(q.data?.stations || []).length,
        });

    q.wizard = {
      openId: ++wizardOpenSeq,
      mode,
      step: station ? step : 1,
      draft,
      detecting: false,
      detectResult: null,
      testing: false,
      testResult: null,
      saving: false,
      error: "",
      focusField,
      highlightField,
      advancedOpen: false,
      loginOpen: false,
      loginUser: "",
      loginPass: "",
      loginBusy: false,
      loginMsg: "",
      loginBad: false,
    };
    syncModalDom(ctx);
    applyWizardFocus(ctx);
  }

  function applyWizardFocus(ctx) {
    const w = ctx.q.wizard;
    if (!w?.focusField) return;
    const fieldName = w.focusField;
    w.focusField = "";
    const run = () => {
      const container = getLiveDom(ctx)?.modal?.modalEl || ctx.root();
      const el = container?.querySelector?.(`[data-qw-field="${fieldName}"]`);
      if (el && typeof el.focus === "function") el.focus();
    };
    if (typeof requestAnimationFrame === "function") requestAnimationFrame(run);
    else run();
  }

  async function runDetect(ctx) {
    const w = ctx.q.wizard;
    if (!w || w.detecting) return;
    const d = w.draft;
    if (d.source_mode === "astrbot_provider" && !d.astrbot_provider_id) {
      w.error = "请先选择一个 AstrBot 供应商。";
      patchModalError(ctx);
      return;
    }
    if (d.source_mode === "manual" && !d.base_url.trim()) {
      w.error = "请先填写站点地址（例如 https://api.example.com）。";
      patchModalError(ctx);
      return;
    }
    const ticket = ctx.ticket();
    const openId = w.openId;
    w.detecting = true;
    w.error = "";
    w.detectResult = null;
    patchDetectSlot(ctx);
    try {
      const res = await ctx.api("POST", `${ctx.path}/quota/detect`, {
        base_url: d.source_mode === "manual" ? d.base_url.trim() : "",
        astrbot_provider_id: d.source_mode === "astrbot_provider" ? d.astrbot_provider_id : "",
        allow_http: d.allow_http === true,
        proxy_url: d.proxy_url.trim(),
      }, 10000);
      if (!ctx.isCurrent(ticket) || ctx.q.wizard?.openId !== openId) return;
      w.detectResult = res;
      if (res.ok) {
        d.adapter_id = res.adapter_id || d.adapter_id;
        if (res.normalized_url && d.source_mode === "manual") d.base_url = res.normalized_url;
        if (!d.name.trim() && res.suggested_name) d.name = res.suggested_name;
        if (res.user_id_header) d.user_id_header = res.user_id_header;
        if (res.display_type && !d.display_type) d.display_type = "";
      } else {
        w.error = res.reason || "未能完成站点识别。";
      }
    } catch (err) {
      if (!ctx.isCurrent(ticket) || ctx.q.wizard?.openId !== openId) return;
      w.error = ctx.fail(err, "站点智能识别失败，可直接从下方选择预设类型。");
    } finally {
      if (ctx.isCurrent(ticket) && ctx.q.wizard?.openId === openId) {
        w.detecting = false;
        patchDetectSlot(ctx);
      }
    }
  }

  function buildStationPayload(d) {
    const toOptNum = v => {
      const s = String(v ?? "").trim();
      if (!s) return null;
      const n = Number(s);
      return Number.isFinite(n) ? n : s;
    };
    return {
      id: d.id || "",
      name: d.name.trim(),
      adapter_id: d.adapter_id,
      enabled: d.enabled !== false,
      source_mode: d.source_mode,
      astrbot_provider_id: d.source_mode === "astrbot_provider" ? d.astrbot_provider_id : "",
      base_url: d.source_mode === "manual" ? d.base_url.trim() : "",
      allow_http: d.allow_http === true,
      auth_token: d.auth_token.trim(),
      clear_auth_token: d.clear_auth_token === true,
      user_id: d.user_id.trim(),
      user_id_header: d.user_id_header || "New-Api-User",
      custom_script: d.adapter_id === "custom_script" ? d.custom_script : "",
      proxy_url: d.proxy_url.trim(),
      clear_proxy_url: d.clear_proxy_url === true,
      display_type: d.display_type || "",
      quota_per_unit: String(d.quota_per_unit ?? "").trim() ? Number(d.quota_per_unit) : 0,
      fiat_currency: d.fiat_currency || "",
      fiat_per_base_unit: toOptNum(d.fiat_per_base_unit),
      display_unit_mode: d.display_unit_mode || "native",
      interval_sec: Number(d.interval_sec) || 300,
      low_threshold: toOptNum(d.low_threshold),
      low_percent: Number(d.low_percent ?? 10),
      critical_threshold: toOptNum(d.critical_threshold),
      participate_in_awareness: d.participate_in_awareness === true,
    };
  }

  async function runDraftTest(ctx) {
    const w = ctx.q.wizard;
    if (!w || w.testing || w.saving) return;
    const ticket = ctx.ticket();
    const openId = w.openId;
    const prevAdapterId = w.draft.adapter_id;
    w.testing = true;
    w.error = "";
    w.testResult = null;
    w.highlightField = "";
    // Surgical in-place pending state: ZERO modal/form rebuild, ZERO scroll jump, ZERO focus loss!
    if (getLiveDom(ctx)) {
      patchModalError(ctx);
      patchModalFooterButtons(ctx);
      patchTestResultSlot(ctx);
    } else {
      ctx.render();
    }
    try {
      const res = await ctx.api("POST", `${ctx.path}/quota/test`, {
        station: buildStationPayload(w.draft),
      }, 15000);
      if (!ctx.isCurrent(ticket) || ctx.q.wizard?.openId !== openId) return;
      const snap = res?.snapshot || null;
      w.testResult = snap;
      // Self-healing UX: if upstream demands New-Api-User / Rix-Api-User / Veloera-User, auto-expand & focus user_id!
      if (snap && !snap.ok && snap.error_code === "requires_user_id") {
        w.draft.adapter_id = "newapi_user_id";
        if (snap.detected_header) w.draft.user_id_header = snap.detected_header;
        w.highlightField = "user_id";
        w.focusField = "user_id";
      } else if (snap && !snap.ok && (snap.error_code === "sk_key_rejected" || snap.error_code === "missing_credentials")) {
        w.highlightField = "auth_token";
        w.focusField = "auth_token";
      }
    } catch (err) {
      if (!ctx.isCurrent(ticket) || ctx.q.wizard?.openId !== openId) return;
      w.error = ctx.fail(err, "连通性测试失败，请检查配置。");
    } finally {
      if (ctx.isCurrent(ticket) && ctx.q.wizard?.openId === openId) {
        w.testing = false;
        if (getLiveDom(ctx)) {
          patchModalFooterButtons(ctx);
          patchModalError(ctx);
          if (w.draft.adapter_id !== prevAdapterId) {
            patchDynamicFieldsAfterSelfHeal(ctx);
          }
          patchTestResultSlot(ctx, { fresh: true });
        } else {
          ctx.render();
        }
        applyWizardFocus(ctx);
      }
    }
  }

  async function runOneTimeLogin(ctx) {
    const w = ctx.q.wizard;
    if (!w || w.loginBusy) return;
    if (!w.loginUser.trim() || !w.loginPass) {
      w.loginMsg = "请输入站点用户名与密码。";
      w.loginBad = true;
      patchLoginSlot(ctx);
      return;
    }
    const ticket = ctx.ticket();
    const openId = w.openId;
    w.loginBusy = true;
    w.loginMsg = "";
    w.loginBad = false;
    patchLoginSlot(ctx);
    try {
      const res = await ctx.api("POST", `${ctx.path}/quota/exchange-token`, {
        base_url: w.draft.source_mode === "manual" ? w.draft.base_url.trim() : "",
        astrbot_provider_id: w.draft.source_mode === "astrbot_provider" ? w.draft.astrbot_provider_id : "",
        username: w.loginUser.trim(),
        password: w.loginPass,
        user_id_header: w.draft.user_id_header || "New-Api-User",
        allow_http: w.draft.allow_http === true,
        proxy_url: w.draft.proxy_url.trim(),
      }, 15000);
      if (!ctx.isCurrent(ticket) || ctx.q.wizard?.openId !== openId) return;
      if (res?.access_token) {
        w.draft.auth_token = res.access_token;
        w.draft.clear_auth_token = false;
      }
      if (res?.user_id) {
        w.draft.user_id = String(res.user_id);
      }
      // Immediately clear ephemeral password from memory!
      w.loginPass = "";
      w.loginOpen = false;
      w.loginMsg = `已获取系统访问令牌${res?.user_id ? `并自动填入用户 ID（${res.user_id}）` : ""}，密码未保存。`;
      w.loginBad = false;
    } catch (err) {
      if (!ctx.isCurrent(ticket) || ctx.q.wizard?.openId !== openId) return;
      w.loginPass = "";
      w.loginMsg = ctx.fail(err, "登录换取令牌失败，请手动复制系统访问令牌。");
      w.loginBad = true;
    } finally {
      if (ctx.isCurrent(ticket) && ctx.q.wizard?.openId === openId) {
        w.loginBusy = false;
        patchLoginSlot(ctx);
      }
    }
  }

  async function saveWizardStation(ctx) {
    const q = ctx.q;
    const w = q.wizard;
    if (!w || !q.data || w.saving) return;
    if (!w.draft.name.trim()) {
      w.error = "请填写站点名称。";
      w.focusField = "name";
      patchModalError(ctx);
      applyWizardFocus(ctx);
      return;
    }
    const ticket = ctx.ticket();
    const openId = w.openId;
    w.saving = true;
    w.error = "";
    // Surgical in-place saving state: ONLY button & error slot update!
    if (getLiveDom(ctx)) {
      patchModalError(ctx);
      patchModalFooterButtons(ctx);
    } else {
      ctx.render();
    }
    try {
      const data = await ctx.api("POST", `${ctx.path}/quota/stations`, {
        edit_token: q.data.edit_token,
        station: buildStationPayload(w.draft),
        probe_after_save: true,
      }, 25000);
      if (!ctx.isCurrent(ticket) || ctx.q.wizard?.openId !== openId) return;
      q.data = data;
      q.loaded = true;
      syncAwarenessDraft(q);
      q.notice = "站点已保存并完成实时刷新。";
      q.noticeTone = "ok";
      scheduleAutoRefresh(ctx);
      closeModalDom(ctx);
      if (getLiveDom(ctx)) {
        syncMainDom(ctx, { includeAwareness: true });
      } else {
        ctx.render();
      }
      void hydrateLogos(ctx);
    } catch (err) {
      if (!ctx.isCurrent(ticket) || ctx.q.wizard?.openId !== openId) return;
      w.saving = false;
      w.error = ctx.fail(err, "保存站点失败，请核对表单项。");
      if (getLiveDom(ctx)) {
        patchModalFooterButtons(ctx);
        patchModalError(ctx);
      } else {
        ctx.render();
      }
    }
  }

  async function deleteStation(ctx, stationId) {
    const q = ctx.q;
    if (!q.data || q.busy) return;
    const ticket = ctx.ticket();
    q.busy = true;
    patchSingleCardDom(ctx, stationId);
    try {
      const data = await ctx.api("POST", `${ctx.path}/quota/stations/delete`, {
        edit_token: q.data.edit_token,
        station_id: stationId,
      }, 15000);
      if (!ctx.isCurrent(ticket)) return;
      q.data = data;
      q.deleteConfirmId = "";
      syncAwarenessDraft(q);
      q.notice = "已删除该站点。";
      q.noticeTone = "ok";
      scheduleAutoRefresh(ctx);
    } catch (err) {
      if (!ctx.isCurrent(ticket)) return;
      q.notice = ctx.fail(err, "删除站点失败。");
      q.noticeTone = "error";
    } finally {
      if (ctx.isCurrent(ticket)) {
        q.busy = false;
        syncMainDom(ctx, { includeAwareness: true });
      }
    }
  }

  /* ---------- Event Delegation ---------- */

  function handleInput(ctx, event) {
    const q = ctx.q;
    const t = event.target;
    if (!t) return;

    if (t.matches("[data-qw-auto-toggle]")) {
      q.autoRefresh = t.checked === true;
      scheduleAutoRefresh(ctx);
      return;
    }

    if (t.matches("[data-qw-aw-field]") && q.awarenessDraft) {
      const fieldName = t.dataset.qwAwField;
      q.awarenessDraft[fieldName] = t.value;
      if (fieldName === "mode") patchAwarenessDom(ctx);
      return;
    }
    if (t.matches("[data-qw-aw-station]") && q.awarenessDraft) {
      const sid = t.dataset.qwAwStation;
      const set = new Set(q.awarenessDraft.participating_station_ids || []);
      if (t.checked) set.add(sid);
      else set.delete(sid);
      q.awarenessDraft.participating_station_ids = [...set];
      const chip = t.closest?.(".qw-chip-check");
      if (chip) chip.classList?.toggle?.("is-checked", Boolean(t.checked));
      else if (!getLiveDom(ctx)) ctx.render();
      return;
    }

    const w = q.wizard;
    if (!w) return;
    if (t.matches("[data-qw-field]")) {
      const name = t.dataset.qwField;
      w.draft[name] = t.value;
      if (w.highlightField === name) {
        w.highlightField = "";
        patchFieldHighlights(ctx);
      }
      if (name === "name" && w.mode === "edit") {
        patchModalTitle(ctx);
      }
      return;
    }
    if (t.matches("[data-qw-check]")) {
      w.draft[t.dataset.qwCheck] = t.checked === true;
      // Native checkbox already updated visually; only re-render in string-only mock harness!
      if (!getLiveDom(ctx)) ctx.render();
      return;
    }
    if (t.matches("[data-qw-login-field]")) {
      w[t.dataset.qwLoginField] = t.value;
    }
  }

  function handleClick(ctx, event) {
    const q = ctx.q;
    const t = event.target;
    if (!t) return;

    const openWiz = t.closest("[data-qw-open-wizard]");
    if (openWiz) {
      openWizard(ctx, { mode: "add", sourceMode: openWiz.dataset.qwOpenWizard || "manual" });
      return;
    }
    if (t.closest("[data-qw-refresh-all]")) {
      void refreshStations(ctx, "", true);
      return;
    }
    const refreshOne = t.closest("[data-qw-refresh-one]");
    if (refreshOne) {
      void refreshStations(ctx, refreshOne.dataset.qwRefreshOne, true);
      return;
    }
    if (t.closest("[data-qw-toggle-awareness]")) {
      q.awarenessOpen = !q.awarenessOpen;
      if (q.awarenessOpen) syncAwarenessDraft(q);
      patchAwarenessDom(ctx);
      return;
    }
    if (t.closest("[data-qw-save-awareness]")) {
      void saveAwareness(ctx);
      return;
    }
    const setPrimaryBtn = t.closest("[data-qw-set-primary]");
    if (setPrimaryBtn) {
      void setPrimaryStation(ctx, setPrimaryBtn.dataset.qwSetPrimary);
      return;
    }
    const toggleUnitBtn = t.closest("[data-qw-toggle-unit]");
    if (toggleUnitBtn) {
      void toggleUnitMode(ctx, toggleUnitBtn.dataset.qwToggleUnit);
      return;
    }
    const editBtn = t.closest("[data-qw-edit]");
    if (editBtn) {
      const st = (q.data?.stations || []).find(s => s.id === editBtn.dataset.qwEdit);
      if (st) openWizard(ctx, { mode: "edit", station: st, step: 2 });
      return;
    }
    const repairBtn = t.closest("[data-qw-repair]");
    if (repairBtn) {
      const st = (q.data?.stations || []).find(s => s.id === repairBtn.dataset.qwStation);
      const snap = q.data?.snapshots?.[repairBtn.dataset.qwStation];
      if (st) {
        if (repairBtn.dataset.qwRepair === "uid") {
          openWizard(ctx, {
            mode: "edit",
            station: st,
            step: 2,
            overrideAdapter: "newapi_user_id",
            overrideHeader: snap?.detected_header || "New-Api-User",
            focusField: "user_id",
            highlightField: "user_id",
          });
        } else {
          openWizard(ctx, {
            mode: "edit",
            station: st,
            step: 2,
            focusField: "auth_token",
            highlightField: "auth_token",
          });
        }
      }
      return;
    }
    const delArm = t.closest("[data-qw-delete-arm]");
    if (delArm) {
      const prevId = q.deleteConfirmId;
      q.deleteConfirmId = delArm.dataset.qwDeleteArm;
      if (prevId && prevId !== q.deleteConfirmId) patchSingleCardDom(ctx, prevId);
      patchSingleCardDom(ctx, q.deleteConfirmId);
      return;
    }
    if (t.closest("[data-qw-delete-cancel]")) {
      const prevId = q.deleteConfirmId;
      q.deleteConfirmId = "";
      if (prevId) patchSingleCardDom(ctx, prevId);
      return;
    }
    const delConfirm = t.closest("[data-qw-delete-confirm]");
    if (delConfirm) {
      void deleteStation(ctx, delConfirm.dataset.qwDeleteConfirm);
      return;
    }

    // Wizard modal interactions
    const w = q.wizard;
    if (!w) return;
    if (t.closest("[data-qw-close-wizard]") || (t === event.target && t.matches("[data-qw-modal-backdrop]"))) {
      closeModalDom(ctx);
      return;
    }
    const srcBtn = t.closest("[data-qw-source-mode]");
    if (srcBtn) {
      w.draft.source_mode = srcBtn.dataset.qwSourceMode;
      w.detectResult = null;
      w.error = "";
      patchStep1Source(ctx);
      return;
    }
    const pickProv = t.closest("[data-qw-pick-provider]");
    if (pickProv) {
      const pid = pickProv.dataset.qwPickProvider;
      const prov = (q.data?.astrbot_providers || []).find(p => p.id === pid);
      w.draft.astrbot_provider_id = pid;
      if (prov && !w.draft.name.trim()) w.draft.name = prov.label || pid;
      w.error = "";
      const dom = getLiveDom(ctx);
      if (dom?.modal) {
        dom.modal.stepContent.querySelectorAll?.("[data-qw-pick-provider]").forEach(el => {
          el.classList.toggle("is-selected", el.dataset.qwPickProvider === pid);
        });
        patchModalError(ctx);
      } else {
        ctx.render();
      }
      void runDetect(ctx);
      return;
    }
    if (t.closest("[data-qw-detect]")) {
      void runDetect(ctx);
      return;
    }
    const pickAdapter = t.closest("[data-qw-pick-adapter]");
    if (pickAdapter) {
      w.draft.adapter_id = pickAdapter.dataset.qwPickAdapter;
      w.error = "";
      patchAdapterSelection(ctx);
      return;
    }
    const stepBtn = t.closest("[data-qw-step]");
    if (stepBtn) {
      const nextStep = Number(stepBtn.dataset.qwStep);
      if (nextStep === 2) {
        if (w.draft.source_mode === "astrbot_provider" && !w.draft.astrbot_provider_id) {
          w.error = "请先选择一个 AstrBot 供应商。";
          patchModalError(ctx);
          return;
        }
        if (w.draft.source_mode === "manual" && w.draft.adapter_id !== "custom_script" && !w.draft.base_url.trim()) {
          w.error = "请先填写站点地址。";
          patchModalError(ctx);
          return;
        }
        if (!w.draft.name.trim()) {
          const prov = (q.data?.astrbot_providers || []).find(p => p.id === w.draft.astrbot_provider_id);
          w.draft.name = prov?.label || w.detectResult?.suggested_name || w.draft.base_url.replace(/^https?:\/\//i, "").split("/")[0] || "新建站点";
        }
      }
      w.step = nextStep;
      w.error = "";
      patchModalStep(ctx);
      return;
    }
    if (t.closest("[data-qw-toggle-login]")) {
      w.loginOpen = !w.loginOpen;
      return;
    }
    if (t.closest("[data-qw-toggle-advanced]")) {
      w.advancedOpen = !w.advancedOpen;
      return;
    }
    if (t.closest("[data-qw-exchange-token]")) {
      void runOneTimeLogin(ctx);
      return;
    }
    if (t.closest("[data-qw-test-draft]")) {
      void runDraftTest(ctx);
      return;
    }
    if (t.closest("[data-qw-save-station]")) {
      void saveWizardStation(ctx);
    }
  }

  function handleKeydown(ctx, event) {
    if (event.key === "Escape" && ctx.q.wizard) {
      closeModalDom(ctx);
      return true;
    }
    return false;
  }

  function unmount(q) {
    clearAutoTimer(q);
    detachDom(q);
  }

  const isDirty = q => Boolean(q?.wizard && (q.wizard.draft.name || q.wizard.draft.base_url || q.wizard.draft.auth_token));
  const isBusy = q => Boolean(q && (q.loading || q.busy || q.refreshingAll || q.refreshingId || q.awarenessSaving || q.wizard?.saving || q.wizard?.testing || q.wizard?.detecting));
  const changedFields = q => (isDirty(q) ? ["额度看板 · 未保存站点草稿"] : []);

  return {
    ERRORS,
    createState,
    tabBadge,
    consumePendingTab,
    refresh,
    syncDom,
    detachDom,
    renderPane,
    handleInput,
    handleClick,
    handleKeydown,
    unmount,
    isDirty,
    isBusy,
    changedFields,
    save: saveWizardStation,
    openWizard,
    openAwareness,
  };
})();
