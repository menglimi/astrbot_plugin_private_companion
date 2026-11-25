"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const PAGE_DIR = path.join(ROOT, "pages/陪伴小屋");
const workbenchSource = fs.readFileSync(path.join(PAGE_DIR, "workbench-page.js"), "utf8");
const pricingSource = fs.readFileSync(path.join(PAGE_DIR, "pricing-workbench.js"), "utf8");
const quotaSource = fs.readFileSync(path.join(PAGE_DIR, "quota-workbench.js"), "utf8");
const modelsSource = fs.readFileSync(path.join(PAGE_DIR, "models-page.js"), "utf8");
const flush = () => new Promise(resolve => setImmediate(resolve));

function makeStorage() {
  const map = new Map();
  return {
    map,
    getItem: k => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: k => map.delete(k),
  };
}

function assertNoNestedLabels(html, contextLabel) {
  let depth = 0;
  const tagRe = /<(\/?)label\b[^>]*>/gi;
  let match;
  while ((match = tagRe.exec(html)) !== null) {
    if (match[1] === "/") {
      depth = Math.max(0, depth - 1);
    } else {
      depth += 1;
      assert.ok(depth <= 1, `${contextLabel}: detected nested <label> at offset ${match.index}`);
    }
  }
}

function sampleAdapters() {
  return [
    {
      id: "newapi",
      name: "NewAPI / OneAPI 标准站",
      short_name: "NewAPI",
      category: "relay",
      summary: "通过个人访问令牌查询余额、已用与最近一次消耗。",
      auth_guide: "请填写个人访问令牌（非 sk- 模型 Key）。",
      capabilities: { supports_provider_reuse: false, supports_one_time_login: true },
      fields: [
        { name: "auth_token", label: "个人访问令牌", kind: "secret", required: true, placeholder: "nap_..." },
        { name: "quota_per_unit", label: "额度换算除数", kind: "number", required: false, default: 500000, group: "advanced" },
      ],
    },
    {
      id: "newapi_user_id",
      name: "NewAPI 衍生站 / 需用户 ID 站",
      short_name: "衍生站 / UID",
      category: "relay",
      summary: "兼容需要 New-Api-User / Rix-Api-User / Veloera-User 请求头的衍生站。",
      auth_guide: "请填写系统访问令牌与站点数字用户 ID。",
      capabilities: { supports_provider_reuse: false, supports_one_time_login: true },
      fields: [
        { name: "auth_token", label: "系统访问令牌", kind: "secret", required: true, placeholder: "输入访问令牌" },
        { name: "user_id", label: "站点用户 ID", kind: "text", required: true, placeholder: "数字用户 ID" },
        {
          name: "user_id_header",
          label: "用户 ID 请求头",
          kind: "select",
          required: false,
          options: [
            { value: "New-Api-User", label: "New-Api-User" },
            { value: "Rix-Api-User", label: "Rix-Api-User" },
            { value: "Veloera-User", label: "Veloera-User" },
          ],
        },
      ],
    },
    {
      id: "deepseek",
      name: "DeepSeek 官方",
      short_name: "DeepSeek",
      category: "official",
      summary: "官方余额接口，可直接复用 AstrBot API Key。",
      auth_guide: "复用 AstrBot 供应商时无需重复填写 API Key。",
      capabilities: { supports_provider_reuse: true, supports_one_time_login: false },
      fields: [
        { name: "auth_token", label: "DeepSeek API Key", kind: "secret", required: false, placeholder: "sk-..." },
      ],
    },
    {
      id: "unsupported_official",
      name: "无公开余额接口平台",
      short_name: "官方暂不支持",
      category: "unsupported",
      summary: "该平台暂未提供余额查询接口。",
      capabilities: { supports_provider_reuse: false, supports_one_time_login: false },
      fields: [],
    },
  ];
}

function sampleQuotaPayload(overrides = {}) {
  return {
    edit_token: "tok-v1",
    legacy_projected: false,
    updated_at: "2026-11-24T12:00:00+08:00",
    adapters: sampleAdapters(),
    astrbot_providers: [
      { id: "ds_official", label: "DeepSeek 主线", api_origin: "https://api.deepseek.com", model_count: 2, models_preview: ["deepseek-chat"], enabled: true },
      { id: "relay_main", label: "中转主站", api_origin: "https://relay.example.com", model_count: 5, models_preview: ["claude-sonnet-4"], enabled: true },
    ],
    provider_usage: {
      ds_official: { calls: 42, total_tokens: 128500 },
      relay_main: { calls: 15, total_tokens: 34200 },
    },
    awareness: {
      mode: "primary",
      primary_station_id: "st-ds",
      target_currency: "CNY",
    },
    summary: {
      display_currency: "CNY",
      display_symbol: "¥",
      usd_cny_rate: 7.2,
      total_stations: 3,
      ok_count: 1,
      low_count: 1,
      critical_count: 0,
      error_count: 1,
      fiat_remaining: 172.0,
      cny_remaining: 100.0,
      usd_remaining: 10.0,
      cny_station_count: 1,
      usd_station_count: 1,
      non_fiat_station_count: 1,
      health_by_id: {
        "st-ds": "ok",
        "st-relay": "low",
        "st-shenren": "error",
      },
      converted_remaining_by_id: {
        "st-ds": 100.0,
        "st-relay": 72.0,
      },
    },
    stations: [
      {
        id: "st-ds",
        name: "DeepSeek 官方",
        adapter_id: "deepseek",
        source_mode: "astrbot_provider",
        astrbot_provider_id: "ds_official",
        base_url: "https://api.deepseek.com",
        enabled: true,
        participate_in_awareness: true,
        low_threshold: 10,
        critical_threshold: 2,
        has_token: false,
        has_proxy: false,
      },
      {
        id: "st-relay",
        name: "主力中转站",
        adapter_id: "newapi",
        source_mode: "manual",
        astrbot_provider_id: "relay_main",
        base_url: "https://relay.example.com",
        enabled: true,
        participate_in_awareness: true,
        low_threshold: 15,
        critical_threshold: 3,
        has_token: true,
        has_proxy: false,
      },
      {
        id: "st-shenren",
        name: "某衍生中转站",
        adapter_id: "newapi",
        source_mode: "manual",
        astrbot_provider_id: "",
        base_url: "https://rix.example.com",
        enabled: true,
        participate_in_awareness: false,
        low_threshold: 10,
        critical_threshold: 2,
        has_token: true,
        has_proxy: false,
      },
    ],
    snapshots: {
      "st-ds": {
        station_id: "st-ds",
        ok: true,
        remaining: 100.0,
        total: 200.0,
        used: 100.0,
        display_type: "CNY",
        symbol: "¥",
        group_name: "按量付费",
        checked_at: "2026-11-24T11:58:00+08:00",
        latency_ms: 145,
        logo_version: "v1-ds",
      },
      "st-relay": {
        station_id: "st-relay",
        ok: true,
        remaining: 10.0,
        total: 50.0,
        used: 40.0,
        display_type: "USD",
        symbol: "$",
        group_name: "vip",
        last_used_at: "2026-11-24T11:55:00+08:00",
        last_used_model: "claude-sonnet-4",
        last_used_cost: 0.042,
        checked_at: "2026-11-24T11:59:00+08:00",
        latency_ms: 210,
      },
      "st-shenren": {
        station_id: "st-shenren",
        ok: false,
        error_code: "requires_user_id",
        error: "站点要求携带 Rix-Api-User 请求头",
        repair_hint: "点击下方按钮补充用户 ID。",
        detected_header: "Rix-Api-User",
        checked_at: "2026-11-24T11:59:30+08:00",
      },
    },
    ...overrides,
  };
}

function quotaHarness() {
  const requests = [];
  const timers = [];
  const focused = [];
  const scrollCalls = [];
  const docHandlers = {};
  const winHandlers = new Map();
  const localStore = makeStorage();
  const sessionStore = makeStorage();
  const document = {
    hidden: false,
    activeElement: null,
    documentElement: { dataset: {} },
    body: { classList: { contains: () => false } },
    addEventListener(type, fn) { docHandlers[type] = fn; },
    removeEventListener(type) { delete docHandlers[type]; },
  };
  const window = {
    document,
    addEventListener(type, fn) {
      if (!winHandlers.has(type)) winHandlers.set(type, new Set());
      winHandlers.get(type).add(fn);
    },
    removeEventListener(type, fn) {
      winHandlers.get(type)?.delete(fn);
    },
    CompanionProduct: {
      hosted: () => true,
      role: () => ({ model_api: "/astrbot_plugin_private_companion/model" }),
      identity: () => ({ installation_id: "inst", operator_id: "operator" }),
      bridgeNow: () => window.AstrBotPluginPage,
      bridgeFor: () => ({ then: resolve => resolve(window.AstrBotPluginPage) }),
    },
    AstrBotPluginPage: {
      apiGet: (route, params) => new Promise((resolve, reject) => {
        if (/[?#]/.test(route)) return reject(new Error("invalid plugin endpoint"));
        requests.push({ method: "GET", route, params, resolve, reject });
      }),
      apiPost: (route, values) => new Promise((resolve, reject) => {
        if (/[?#]/.test(route)) return reject(new Error("invalid plugin endpoint"));
        requests.push({ method: "POST", route, values, resolve, reject });
      }),
    },
    localStorage: localStore,
    sessionStorage: sessionStore,
    crypto: { randomUUID: () => "op-" + requests.length },
    lucide: { createIcons() {} },
  };
  const fakeSetTimeout = (fn, ms) => {
    const id = timers.length + 1;
    timers.push({ id, fn, ms, cleared: false });
    return id;
  };
  const fakeClearTimeout = id => {
    const item = timers.find(t => t.id === id);
    if (item) item.cleared = true;
  };
  const baseGlobals = {
    window, document, localStorage: localStore, sessionStorage: sessionStore,
    Intl, Date, Math, Number, String, Array, Object, Map, Set, JSON, Promise, URL,
    setTimeout: fakeSetTimeout, clearTimeout: fakeClearTimeout,
    setInterval: fakeSetTimeout, clearInterval: fakeClearTimeout,
    requestAnimationFrame: cb => cb(),
    crypto: window.crypto, AbortController,
  };
  vm.runInNewContext(workbenchSource, { ...baseGlobals });
  vm.runInNewContext(pricingSource, { ...baseGlobals });
  vm.runInNewContext(quotaSource, { ...baseGlobals });
  vm.runInNewContext(modelsSource, { ...baseGlobals });

  const handlers = {};
  const root = {
    isConnected: true,
    innerHTML: "",
    addEventListener(type, fn) { handlers[type] = fn; },
    removeEventListener(type) { delete handlers[type]; },
    querySelector(selector) {
      const m = selector.match(/data-qw-field="([^"]+)"/);
      if (m && root.innerHTML.includes(`data-qw-field="${m[1]}"`)) {
        return {
          focus() { focused.push(m[1]); },
          scrollIntoView(opts) { scrollCalls.push({ selector, opts }); },
        };
      }
      if (selector === '[data-qw-aw-field="primary_station_id"]' && root.innerHTML.includes('data-qw-aw-field="primary_station_id"')) {
        return {
          focus() { focused.push("primary_station_id"); },
          scrollIntoView(opts) { scrollCalls.push({ selector, opts }); },
        };
      }
      return null;
    },
    querySelectorAll() { return []; },
    click(selector, dataset = {}) {
      const target = {
        dataset,
        matches: sel => sel === selector,
        closest: sel => sel === selector ? { dataset } : null,
      };
      handlers.click({ target });
    },
    input(selector, value, { dataset = {}, checked = false, type = "change" } = {}) {
      handlers[type]({
        type,
        target: {
          value, checked, dataset,
          matches: sel => sel === selector,
          closest: sel => sel === selector ? { dataset } : null,
        },
      });
    },
    keydown(key) {
      let prevented = false;
      docHandlers.keydown?.({ key, preventDefault() { prevented = true; } });
      return prevented;
    },
  };
  return {
    page: window.CompanionModelsPage,
    wb: window.CompanionQuotaWorkbench,
    document, winHandlers, scrollCalls,
    root, requests, timers, focused, localStore, sessionStore,
  };
}

test("Quota tab mounts cleanly, renders 4 KPIs with FX breakdown, station cards, icon hydration, and zero nested labels", async () => {
  const h = quotaHarness();
  const tabChanges = [];
  h.page.mount(h.root, { initialTab: "quota", onTabChange: tab => tabChanges.push(tab) });
  await flush();

  const getReq = h.requests.find(r => r.method === "GET" && r.route === "model/quota");
  assert.ok(getReq, "Mounting on initialTab=quota should immediately request GET model/quota");
  assert.ok(!h.requests.some(r => r.route === "model"), "Should not eagerly load fn tasks when mounting directly on quota");

  getReq.resolve(sampleQuotaPayload());
  await flush();
  await flush();

  const html = h.root.innerHTML;
  assertNoNestedLabels(html, "quota main view");
  assert.doesNotMatch(html, /¤/, "Placeholder currency glyph must never appear in UI");
  assert.match(html, /data-model-tab="quota"[^>]*aria-selected="true"/);
  assert.match(html, /1\/3/, "Tab badge should surface ok/total ratio");
  assert.match(html, /折合总余额（CNY）/);
  assert.match(html, /¥172\.00/);
  assert.match(html, /CNY ¥100\.00 · USD \$10\.00 · 另含 1 站非现金积分/);
  assert.match(html, /1 \/ 3/, "Health KPI should show ok / total");
  assert.match(html, /57 次/, "Linked provider usage KPI should sum calls (42 + 15)");
  assert.match(html, /DeepSeek 官方/);
  assert.match(html, /主力中转站/);
  assert.match(html, /claude-sonnet-4/, "Last consumption model should be displayed on station card");
  assert.match(html, /一键补充用户 ID/, "requires_user_id error card should expose 1-click repair button");

  // Lazy logo hydration must pass id & v via params object (never ? in route)
  const iconReq = h.requests.find(r => r.method === "GET" && r.route === "model/quota/icon");
  assert.ok(iconReq, "Should lazily hydrate station icons via backend base64 endpoint");
  assert.deepEqual({ ...iconReq.params }, { id: "st-ds", v: "v1-ds" });
  iconReq.resolve({ available: true, data_url: "data:image/png;base64,iVBORw0KGgo=" });
  await flush();
  assert.match(h.root.innerHTML, /data:image\/png;base64,iVBORw0KGgo=/);

  h.page.unmount();
});

test("2-step wizard supports AstrBot provider import, hides redundant key on official reuse, and saves cleanly", async () => {
  const h = quotaHarness();
  h.page.mount(h.root, { initialTab: "quota" });
  await flush();
  h.requests.find(r => r.route === "model/quota").resolve(sampleQuotaPayload());
  await flush();

  // Open wizard directly in astrbot_provider mode
  h.root.click("[data-qw-open-wizard]", { qwOpenWizard: "astrbot_provider" });
  await flush();
  assert.match(h.root.innerHTML, /选择已配置的 AstrBot 供应商/);

  // Pick DeepSeek provider -> triggers /quota/detect
  h.root.click("[data-qw-pick-provider]", { qwPickProvider: "ds_official" });
  await flush();
  const detectReq = h.requests.find(r => r.method === "POST" && r.route === "model/quota/detect");
  assert.ok(detectReq, "Picking an AstrBot provider should auto-detect adapter");
  assert.equal(detectReq.values.astrbot_provider_id, "ds_official");
  detectReq.resolve({
    ok: true,
    adapter_id: "deepseek",
    confidence: "high",
    suggested_name: "DeepSeek 官方",
    reason: "已识别为 DeepSeek 官方接口",
    auth_hint: "可直接复用 AstrBot 密钥，无需重复填写。",
  });
  await flush();
  assert.match(h.root.innerHTML, /已识别为 DeepSeek 官方接口/);

  // Proceed to Step 2
  h.root.click("[data-qw-step]", { qwStep: "2" });
  await flush();
  assertNoNestedLabels(h.root.innerHTML, "wizard step 2 official reuse");
  assert.match(h.root.innerHTML, /无需重复填写密钥/);
  assert.doesNotMatch(h.root.innerHTML, /data-qw-field="auth_token"/, "Redundant API key input must be hidden when reusing official AstrBot provider");

  // Save station
  h.root.click("[data-qw-save-station]");
  await flush();
  const saveReq = h.requests.find(r => r.method === "POST" && r.route === "model/quota/stations");
  assert.ok(saveReq, "Clicking save should POST model/quota/stations");
  assert.equal(saveReq.values.edit_token, "tok-v1");
  assert.equal(saveReq.values.station.adapter_id, "deepseek");
  assert.equal(saveReq.values.station.astrbot_provider_id, "ds_official");
  saveReq.resolve(sampleQuotaPayload({ edit_token: "tok-v2" }));
  await flush();
  assert.doesNotMatch(h.root.innerHTML, /qw-modal-backdrop/, "Wizard modal should close after save");

  h.page.unmount();
});

test("Self-healing test loop upgrades newapi -> newapi_user_id, focuses user_id, and one-time login clears password", async () => {
  const h = quotaHarness();
  h.page.mount(h.root, { initialTab: "quota" });
  await flush();
  h.requests.find(r => r.route === "model/quota").resolve(sampleQuotaPayload());
  await flush();

  // Click 1-click repair on requires_user_id error card -> jumps straight into Step 2 with newapi_user_id & focus
  h.root.click("[data-qw-repair]", { qwRepair: "uid", qwStation: "st-shenren" });
  await flush();
  assert.match(h.root.innerHTML, /data-qw-field="user_id"/);
  assert.match(h.root.innerHTML, /Rix-Api-User/);
  assert.ok(h.focused.includes("user_id"), "1-click repair should immediately focus user_id field");

  // Close with Escape key
  assert.equal(h.root.keydown("Escape"), true, "Escape should close open quota wizard");
  assert.doesNotMatch(h.root.innerHTML, /qw-modal-backdrop/);

  // Now open manual newapi wizard and test self-healing directly from /quota/test response
  h.focused.length = 0;
  h.root.click("[data-qw-open-wizard]", { qwOpenWizard: "manual" });
  h.root.input("[data-qw-field]", "https://custom-relay.example.com", { dataset: { qwField: "base_url" }, type: "input" });
  h.root.click("[data-qw-pick-adapter]", { qwPickAdapter: "newapi" });
  h.root.click("[data-qw-step]", { qwStep: "2" });
  h.root.input("[data-qw-field]", "example-token-value", { dataset: { qwField: "auth_token" }, type: "input" });

  h.root.click("[data-qw-test-draft]");
  await flush();
  const testReq = h.requests.find(r => r.method === "POST" && r.route === "model/quota/test");
  assert.ok(testReq, "Should invoke POST model/quota/test");
  testReq.resolve({
    snapshot: {
      ok: false,
      error_code: "requires_user_id",
      error: "请携带 Veloera-User 用户 ID",
      detected_header: "Veloera-User",
    },
  });
  await flush();

  assert.match(h.root.innerHTML, /data-qw-field="user_id"/, "Self-healing should auto-expand user_id input");
  assert.match(h.root.innerHTML, /is-highlight/, "Self-healing should visually highlight user_id field");
  assert.ok(h.focused.includes("user_id"), "Self-healing should auto-focus user_id input");

  // Test one-time login exchange: populates token & user_id, never persists password
  h.root.input("[data-qw-login-field]", "alice", { dataset: { qwLoginField: "loginUser" }, type: "input" });
  h.root.input("[data-qw-login-field]", "ephemeral-pass-val", { dataset: { qwLoginField: "loginPass" }, type: "input" });
  h.root.click("[data-qw-exchange-token]");
  await flush();
  const exchReq = h.requests.find(r => r.method === "POST" && r.route === "model/quota/exchange-token");
  assert.ok(exchReq, "Should call POST model/quota/exchange-token");
  assert.equal(exchReq.values.username, "alice");
  assert.equal(exchReq.values.password, "ephemeral-pass-val");
  exchReq.resolve({ ok: true, access_token: "minted-access-tok", user_id: "8848" });
  await flush();
  assert.match(h.root.innerHTML, /8848/);
  assert.doesNotMatch(h.root.innerHTML, /ephemeral-pass-val/, "One-time password must be wiped immediately from UI memory");

  h.page.unmount();
});

test("Awareness policy panel updates mode & station participation and delete flow requires arm+confirm", async () => {
  const h = quotaHarness();
  h.page.mount(h.root, { initialTab: "quota" });
  await flush();
  h.requests.find(r => r.route === "model/quota").resolve(sampleQuotaPayload());
  await flush();

  // Open awareness policy panel
  h.root.click("[data-qw-toggle-awareness]");
  await flush();
  assertNoNestedLabels(h.root.innerHTML, "awareness panel");
  h.root.input("[data-qw-aw-field]", "aggregate_sum", { dataset: { qwAwField: "mode" }, type: "change" });
  h.root.input("[data-qw-aw-station]", "", { dataset: { qwAwStation: "st-shenren" }, checked: true, type: "change" });
  h.root.click("[data-qw-save-awareness]");
  await flush();

  const awReq = h.requests.find(r => r.method === "POST" && r.route === "model/quota/awareness");
  assert.ok(awReq, "Saving awareness policy should POST model/quota/awareness");
  assert.equal(awReq.values.awareness.mode, "aggregate_sum");
  assert.ok(awReq.values.awareness.participating_station_ids.includes("st-shenren"));
  awReq.resolve(sampleQuotaPayload({
    awareness: { mode: "aggregate_sum", primary_station_id: "", target_currency: "CNY" },
  }));
  await flush();
  assert.match(h.root.innerHTML, /多站折算汇总/);

  // Two-step inline delete (arm -> confirm)
  h.root.click("[data-qw-delete-arm]", { qwDeleteArm: "st-shenren" });
  assert.match(h.root.innerHTML, /确认删除/);
  h.root.click("[data-qw-delete-confirm]", { qwDeleteConfirm: "st-shenren" });
  await flush();
  const delReq = h.requests.find(r => r.method === "POST" && r.route === "model/quota/stations/delete");
  assert.ok(delReq);
  assert.equal(delReq.values.station_id, "st-shenren");

  h.page.unmount();
});

test("quota-workbench.css uses 100% semantic design tokens with container queries and reduced-motion support", () => {
  const css = fs.readFileSync(path.join(PAGE_DIR, "quota-workbench.css"), "utf8");
  const stripped = css.replace(/\/\*[\s\S]*?\*\//g, "");
  assert.doesNotMatch(stripped, /#[0-9a-fA-F]{3,8}\b/, "Must not contain hardcoded hex colors");
  assert.doesNotMatch(stripped, /\brgba?\s*\(/i, "Must not contain hardcoded rgb/rgba colors");
  assert.doesNotMatch(stripped, /\bhsla?\s*\(/i, "Must not contain hardcoded hsl/hsla colors");
  assert.match(stripped, /container-type:\s*inline-size/);
  assert.match(stripped, /container-name:\s*quota-wb/);
  assert.match(stripped, /@container\s+quota-wb/);
  assert.match(stripped, /prefers-reduced-motion:\s*reduce/);
  assert.match(stripped, /\.qw-status-pill\.ok\b/);
  assert.match(stripped, /\.qw-status-pill\.warn\b/);
  assert.match(stripped, /\.qw-status-pill\.risk\b/);
  // Mobile responsive polish contract (360px–600px)
  assert.match(stripped, /@media\s*\(max-width:\s*600px\)\s*\{[\s\S]*?\.qw-modal-backdrop\s*\{[^}]*padding:\s*var\(--sp-2\)/);
  assert.match(stripped, /@media\s*\(max-width:\s*600px\)\s*\{[\s\S]*?\.qw-modal\s*\{[^}]*max-height:\s*92dvh/);
  assert.match(stripped, /@media\s*\(max-width:\s*600px\)\s*\{[\s\S]*?\.qw-detect-row\s*\{[^}]*flex-direction:\s*column/);
  assert.match(stripped, /@media\s*\(max-width:\s*600px\)\s*\{[\s\S]*?\.qw-field-grid\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/);
  assert.match(stripped, /@media\s*\(max-width:\s*600px\)\s*\{[\s\S]*?\.qw-modal-foot\s*\{[^}]*env\(safe-area-inset-bottom\)/);
  assert.match(stripped, /@media\s*\(max-width:\s*600px\)\s*\{[\s\S]*?\.qw-modal-foot-right\s*\{[^}]*flex-wrap:\s*wrap/);
  assert.match(stripped, /@media\s*\(max-width:\s*560px\)\s*\{[\s\S]*?\.qw-card\s*\{[^}]*padding:\s*var\(--sp-3\)/);
  assert.match(stripped, /@media\s*\(max-width:\s*560px\)\s*\{[\s\S]*?\.qw-card-head\s*\{[^}]*flex-wrap:\s*wrap/);
  assert.match(stripped, /@media\s*\(max-width:\s*420px\)\s*\{[\s\S]*?\.qw-segment\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/);
  // Entrance animation must ONLY be scoped to .is-entering (never unconditional on .qw-modal-backdrop / .qw-modal)
  const backdropBase = stripped.match(/\.qw-modal-backdrop\s*\{([^}]*)\}/);
  const modalBase = stripped.match(/\.qw-modal\s*\{([^}]*)\}/);
  assert.ok(backdropBase && !/\banimation\s*:/i.test(backdropBase[1]), ".qw-modal-backdrop base rule must not define unconditional animation");
  assert.ok(modalBase && !/\banimation\s*:/i.test(modalBase[1]), ".qw-modal base rule must not define unconditional animation");
  assert.match(stripped, /\.qw-modal-backdrop\.is-entering\s*\{[^}]*animation:\s*qwFadeIn/);
  assert.match(stripped, /\.qw-modal\.is-entering\s*\{[^}]*animation:\s*qwSlideUp/);
  assert.match(stripped, /\.qw-test-preview\.is-fresh\s*\{[^}]*animation:\s*qwSoftReveal/);
  // Modal body & <details> must never clip expanded disclosures via flex-shrink or overflow:hidden
  assert.match(stripped, /\.qw-modal-body\s*\{[^}]*min-height:\s*0[^}]*overflow-y:\s*auto[^}]*overscroll-behavior:\s*contain/);
  assert.match(stripped, /\.qw-modal-body\s*>\s*\*\s*\{[^}]*flex-shrink:\s*0/);
  const detailsBlock = stripped.match(/\.qw-details\s*\{([^}]*)\}/);
  assert.ok(detailsBlock, ".qw-details rule must exist");
  assert.match(detailsBlock[1], /flex-shrink:\s*0/);
  assert.doesNotMatch(detailsBlock[1], /overflow\s*:\s*hidden/i, ".qw-details must not use overflow:hidden that clips expanded fields");
});

test("OpenRouter unlimited key (ok=true, remaining=null), legacy projection card, advancedOpen persistence, sanitized SVG + brand emblem fallback, and unsafe URI defense", async () => {
  const h = quotaHarness();
  h.page.mount(h.root, { initialTab: "quota" });
  await flush();

  const payload = sampleQuotaPayload({
    legacy_projected: true,
    stations: [
      {
        id: "legacy-default",
        name: "旧版单站",
        adapter_id: "newapi",
        source_mode: "manual",
        astrbot_provider_id: "",
        base_url: "https://legacy.example.test",
        enabled: true,
        participate_in_awareness: true,
        has_token: true,
        has_proxy: false,
      },
      {
        id: "st-or-unlim",
        name: "OpenRouter 无限额",
        adapter_id: "openrouter",
        source_mode: "astrbot_provider",
        astrbot_provider_id: "ds_official",
        base_url: "https://openrouter.ai/api/v1",
        enabled: true,
        participate_in_awareness: false,
        has_token: false,
        has_proxy: false,
      },
      {
        id: "st-cat-svg",
        name: "示例中转站",
        adapter_id: "newapi",
        source_mode: "manual",
        astrbot_provider_id: "",
        base_url: "https://cat.example.test",
        enabled: true,
        participate_in_awareness: false,
        has_token: true,
        has_proxy: false,
      },
      {
        id: "st-cookie-custom",
        name: "饼干自定义币站",
        adapter_id: "newapi",
        source_mode: "manual",
        astrbot_provider_id: "",
        base_url: "https://cookie.example.test",
        enabled: true,
        participate_in_awareness: false,
        has_token: true,
        has_proxy: false,
      },
    ],
    snapshots: {
      "legacy-default": {
        station_id: "legacy-default",
        ok: true,
        remaining: 25.0,
        display_type: "USD",
        symbol: "$",
        checked_at: "2026-11-24T12:00:00+08:00",
        logo_version: "v-unsafe-uri",
      },
      "st-or-unlim": {
        station_id: "st-or-unlim",
        ok: true,
        remaining: null,
        used: 3.25,
        display_type: "USD",
        symbol: "$",
        extra: "当前 Key 未设置消费上限",
        checked_at: "2026-11-24T12:00:00+08:00",
      },
      "st-cat-svg": {
        station_id: "st-cat-svg",
        ok: true,
        remaining: 48.92,
        display_type: "CNY",
        symbol: "¥",
        checked_at: "2026-11-24T12:00:00+08:00",
        logo_version: "v-cat-svg",
      },
      "st-cookie-custom": {
        station_id: "st-cookie-custom",
        ok: true,
        remaining: 732.98,
        used: 2614.72,
        total: 3347.7,
        display_type: "CREDIT",
        symbol: "🍪",
        extra: "站内换算 10 🍪 = 1 基准单位 · 参考充值约 ¥73.30",
        checked_at: "2026-11-24T12:00:00+08:00",
      },
    },
    summary: {
      ...sampleQuotaPayload().summary,
      health_by_id: { "legacy-default": "normal", "st-or-unlim": "normal", "st-cat-svg": "normal", "st-cookie-custom": "normal" },
    },
  });
  h.requests.find(r => r.route === "model/quota").resolve(payload);
  await flush();
  await flush();

  const html = h.root.innerHTML;
  // Legacy projected card shows badge + migrate label and hides delete button
  assert.match(html, /旧版投影/);
  assert.match(html, /编辑并迁移/);
  assert.doesNotMatch(html, /data-qw-delete-arm="legacy-default"/);

  // OpenRouter unlimited key renders as healthy "不限额" + used + extra AND official inline brand SVG fallback
  assert.match(html, /不限额/);
  assert.match(html, /已用 \$3\.25/);
  assert.match(html, /当前 Key 未设置消费上限/);
  assert.doesNotMatch(html, /qw-balance-value is-error/);
  assert.match(html, /qw-avatar is-brand[^>]*data-qw-avatar="st-or-unlim"[\s\S]*?<svg class="qw-brand-svg"/);

  // Custom currency card displays custom symbol & transparent conversion note
  assert.match(html, /732\.98/);
  assert.match(html, /qw-balance-unit">🍪</);
  assert.match(html, /站内换算 10 🍪 = 1 基准单位 · 参考充值约 ¥73\.30/);

  // Non-base64 / text/html data URIs MUST still be rejected, while server-sanitized base64 SVG data URI renders safely inside <img>
  const unsafeReq = h.requests.find(r => r.route === "model/quota/icon" && r.params?.id === "legacy-default");
  const svgReq = h.requests.find(r => r.route === "model/quota/icon" && r.params?.id === "st-cat-svg");
  assert.ok(unsafeReq && svgReq);
  unsafeReq.resolve({ available: true, data_url: "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==" });
  svgReq.resolve({ available: true, data_url: "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciPjwvc3ZnPg==" });
  await flush();
  assert.doesNotMatch(h.root.innerHTML, /data:text\/html/i);
  assert.match(h.root.innerHTML, /<img src="data:image\/svg\+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciPjwvc3ZnPg=="/);

  // Step 1 manual URL uses <label for="qw-step1-base-url"> (not wrapping button/div)
  h.root.click("[data-qw-open-wizard]", { qwOpenWizard: "manual" });
  assert.match(h.root.innerHTML, /<label for="qw-step1-base-url">站点根地址<\/label>/);
  assert.doesNotMatch(h.root.innerHTML, /<label[^>]*>(?:(?!<\/label>)[\s\S])*data-qw-detect(?:(?!<\/label>)[\s\S])*<\/label>/);

  // Step 2: advancedOpen persists across inner checkbox render + unlimited draft test preview succeeds
  h.root.input("[data-qw-field]", "https://openrouter.ai/api/v1", { dataset: { qwField: "base_url" }, type: "input" });
  h.root.click("[data-qw-step]", { qwStep: "2" });
  h.root.click("[data-qw-toggle-advanced]");
  h.root.input("[data-qw-check]", "", { dataset: { qwCheck: "allow_http" }, checked: true, type: "change" });
  assert.match(h.root.innerHTML, /<details class="qw-details"[^>]*\bopen\b/);

  h.root.click("[data-qw-test-draft]");
  await flush();
  h.requests.find(r => r.route === "model/quota/test").resolve({
    snapshot: { ok: true, remaining: null, used: 3.25, display_type: "USD", symbol: "$", extra: "无限额 Key", latency_ms: 95 },
  });
  await flush();
  assert.match(h.root.innerHTML, /连通性测试通过/);
  assert.match(h.root.innerHTML, /不限额（已用 \$3\.25）/);
  assert.doesNotMatch(h.root.innerHTML, /qw-test-preview is-fail/);

  h.page.unmount();
});

test("XSS payloads in station names, URLs, error text, and models are 100% HTML-escaped", async () => {
  const h = quotaHarness();
  h.page.mount(h.root, { initialTab: "quota" });
  await flush();
  const payload = sampleQuotaPayload();
  payload.stations[0].name = `<img src=x onerror="alert(1)">`;
  payload.stations[0].base_url = `https://evil.example/?q="><script>alert(2)</script>`;
  payload.snapshots["st-ds"].group_name = `<b>vip</b>`;
  payload.snapshots["st-relay"].last_used_model = `<svg onload=alert(3)>`;
  payload.snapshots["st-shenren"].error = `"><iframe src=javascript:alert(4)>`;
  h.requests.find(r => r.route === "model/quota").resolve(payload);
  await flush();

  const html = h.root.innerHTML;
  assert.doesNotMatch(html, /<script>alert\(2\)<\/script>/i);
  assert.doesNotMatch(html, /onerror="alert\(1\)"/i);
  assert.doesNotMatch(html, /<svg onload=/i);
  assert.doesNotMatch(html, /<iframe/i);
  assert.match(html, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
  h.page.unmount();
});

function createMiniDom() {
  const VOID_TAGS = new Set(["input", "img", "br", "hr", "meta", "link", "source"]);
  const docHandlers = {};
  const document = {
    hidden: false,
    activeElement: null,
    documentElement: { dataset: {} },
    body: null,
    addEventListener(type, fn) { (docHandlers[type] ||= []).push(fn); },
    removeEventListener(type, fn) {
      if (!docHandlers[type]) return;
      docHandlers[type] = docHandlers[type].filter(f => f !== fn);
    },
    dispatchEvent(type, ev) {
      for (const fn of [...(docHandlers[type] || [])]) fn(ev);
    },
    createElement(tag) {
      return new MiniElement(tag, document);
    },
  };

  function toCamel(k) {
    return k.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
  }
  function parseAttrs(raw) {
    const map = new Map();
    const re = /([a-zA-Z0-9_:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;
    let m;
    while ((m = re.exec(raw)) !== null) {
      const name = m[1].toLowerCase();
      const val = m[2] ?? m[3] ?? m[4] ?? "";
      map.set(name, val);
    }
    return map;
  }
  function decodeHtml(s) {
    return String(s).replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
  }
  function parseHtmlInto(parent, html) {
    parent.children = [];
    parent._text = "";
    const str = String(html || "");
    const stack = [parent];
    const tokenRe = /<(\/?)([a-zA-Z][a-zA-Z0-9-]*)((?:\s+[^>"']*(?:"[^"]*"|'[^']*')?)*?)\s*(\/?)>|([^<]+)/g;
    let m;
    while ((m = tokenRe.exec(str)) !== null) {
      const top = stack[stack.length - 1];
      if (m[5] !== undefined) {
        top._text += decodeHtml(m[5]);
        continue;
      }
      const isClose = m[1] === "/";
      const tag = m[2].toLowerCase();
      const attrStr = m[3] || "";
      const selfClose = m[4] === "/" || VOID_TAGS.has(tag);
      if (isClose) {
        for (let i = stack.length - 1; i >= 1; i--) {
          if (stack[i].tagName.toLowerCase() === tag) {
            stack.length = i;
            break;
          }
        }
      } else {
        const el = new MiniElement(tag, parent._doc);
        const attrs = parseAttrs(attrStr);
        for (const [k, v] of attrs.entries()) {
          el.setAttribute(k, decodeHtml(v));
        }
        top.appendChild(el);
        if (!selfClose) stack.push(el);
      }
    }
  }

  function matchSingleSelector(el, sel) {
    const s = sel.trim();
    if (!s) return false;
    // Support combined tag/class/attr selectors like button[data-qw-step="1"], .qw-field.is-highlight
    let rest = s;
    const tagMatch = rest.match(/^([a-zA-Z][a-zA-Z0-9-]*)/);
    if (tagMatch) {
      if (el.tagName.toLowerCase() !== tagMatch[1].toLowerCase()) return false;
      rest = rest.slice(tagMatch[1].length);
    }
    const parts = rest.match(/(\.[a-zA-Z0-9_-]+|#[a-zA-Z0-9_-]+|\[[^\]]+\])/g) || [];
    for (const part of parts) {
      if (part.startsWith(".")) {
        if (!el.classList.contains(part.slice(1))) return false;
      } else if (part.startsWith("#")) {
        if (el.getAttribute("id") !== part.slice(1)) return false;
      } else if (part.startsWith("[")) {
        const am = part.match(/^\[([a-zA-Z0-9_-]+)(?:=(?:"([^"]*)"|'([^']*)'|([^\]]+)))?\]$/);
        if (!am) return false;
        const attrName = am[1].toLowerCase();
        const want = am[2] ?? am[3] ?? am[4];
        if (!el._attrs.has(attrName)) return false;
        if (want !== undefined && el._attrs.get(attrName) !== want) return false;
      }
    }
    return true;
  }

  class MiniElement {
    constructor(tag, doc) {
      this.tagName = String(tag).toUpperCase();
      this._doc = doc;
      this.parentNode = null;
      this.children = [];
      this._attrs = new Map();
      this._listeners = {};
      this._text = "";
      this._htmlRaw = "";
      this.scrollTop = 0;
      this.offsetWidth = 640;
      this.offsetHeight = 400;
      this.value = "";
      this.checked = false;
      this.disabled = false;
      this.open = false;
      this.inert = false;
      this.isConnected = true;
      const self = this;
      this.dataset = new Proxy({}, {
        get(_, prop) {
          if (typeof prop !== "string") return undefined;
          const attr = "data-" + prop.replace(/[A-Z]/g, c => "-" + c.toLowerCase());
          return self._attrs.get(attr);
        },
        set(_, prop, val) {
          const attr = "data-" + String(prop).replace(/[A-Z]/g, c => "-" + c.toLowerCase());
          self.setAttribute(attr, String(val));
          return true;
        },
      });
      this.classList = {
        _set() {
          return new Set((self._attrs.get("class") || "").split(/\s+/).filter(Boolean));
        },
        _save(set) {
          self._attrs.set("class", [...set].join(" "));
        },
        contains(c) { return this._set().has(c); },
        add(...cs) { const s = this._set(); cs.forEach(c => c && s.add(c)); this._save(s); },
        remove(...cs) { const s = this._set(); cs.forEach(c => s.delete(c)); this._save(s); },
        toggle(c, force) {
          const s = this._set();
          const next = force !== undefined ? Boolean(force) : !s.has(c);
          if (next) s.add(c); else s.delete(c);
          this._save(s);
          return next;
        },
      };
    }
    get className() { return this._attrs.get("class") || ""; }
    set className(v) { this._attrs.set("class", String(v || "")); }
    get title() { return this._attrs.get("title") || ""; }
    set title(v) { this._attrs.set("title", String(v || "")); }
    get textContent() {
      if (this.children.length === 0) return this._text;
      return this._text + this.children.map(c => c.textContent).join("");
    }
    set textContent(v) {
      this.children = [];
      this._text = String(v ?? "");
      this._htmlRaw = this._text;
    }
    get innerHTML() {
      return this._htmlRaw;
    }
    set innerHTML(v) {
      this._htmlRaw = String(v ?? "");
      parseHtmlInto(this, this._htmlRaw);
    }
    setAttribute(name, value) {
      const k = String(name).toLowerCase();
      const v = String(value ?? "");
      this._attrs.set(k, v);
      if (k === "value") this.value = v;
      if (k === "disabled") this.disabled = true;
      if (k === "checked") this.checked = true;
      if (k === "open") this.open = true;
    }
    getAttribute(name) {
      const k = String(name).toLowerCase();
      return this._attrs.has(k) ? this._attrs.get(k) : null;
    }
    removeAttribute(name) {
      const k = String(name).toLowerCase();
      this._attrs.delete(k);
      if (k === "disabled") this.disabled = false;
      if (k === "checked") this.checked = false;
      if (k === "open") this.open = false;
    }
    appendChild(child) {
      if (child.parentNode) child.remove();
      child.parentNode = this;
      this.children.push(child);
      return child;
    }
    remove() {
      if (!this.parentNode) return;
      this.parentNode.children = this.parentNode.children.filter(c => c !== this);
      this.parentNode = null;
    }
    contains(node) {
      let cur = node;
      while (cur) {
        if (cur === this) return true;
        cur = cur.parentNode;
      }
      return false;
    }
    matches(selector) {
      return String(selector).split(",").some(s => matchSingleSelector(this, s));
    }
    closest(selector) {
      let cur = this;
      while (cur) {
        if (cur.matches(selector)) return cur;
        cur = cur.parentNode;
      }
      return null;
    }
    querySelectorAll(selector) {
      const out = [];
      const walk = node => {
        for (const child of node.children) {
          if (child.matches(selector)) out.push(child);
          walk(child);
        }
      };
      walk(this);
      return out;
    }
    querySelector(selector) {
      return this.querySelectorAll(selector)[0] || null;
    }
    addEventListener(type, fn, opts) {
      (this._listeners[type] ||= []).push({ fn, once: Boolean(opts?.once) });
    }
    removeEventListener(type, fn) {
      if (!this._listeners[type]) return;
      this._listeners[type] = this._listeners[type].filter(item => item.fn !== fn);
    }
    dispatch(type, event = {}) {
      const ev = { type, target: this, preventDefault() {}, ...event };
      let cur = this;
      while (cur) {
        const list = [...(cur._listeners[type] || [])];
        for (const item of list) {
          if (item.once) cur.removeEventListener(type, item.fn);
          item.fn(ev);
        }
        cur = cur.parentNode;
      }
    }
    focus() { this._doc.activeElement = this; }
    blur() { if (this._doc.activeElement === this) this._doc.activeElement = null; }
  }

  document.body = new MiniElement("body", document);
  return { document, MiniElement };
}

test("Live DOM: modal shell persists (===) with zero re-animation, zero scroll jump, and zero focus loss across test/save/error/step/autoRefresh", async () => {
  const { document } = createMiniDom();
  const requests = [];
  const timers = [];
  const localStore = makeStorage();
  const sessionStore = makeStorage();
  const window = {
    document,
    CompanionProduct: {
      hosted: () => true,
      role: () => ({ model_api: "/astrbot_plugin_private_companion/model" }),
      identity: () => ({ installation_id: "inst", operator_id: "operator" }),
      bridgeNow: () => window.AstrBotPluginPage,
      bridgeFor: () => ({ then: resolve => resolve(window.AstrBotPluginPage) }),
    },
    AstrBotPluginPage: {
      apiGet: (route, params) => new Promise((resolve, reject) => requests.push({ method: "GET", route, params, resolve, reject })),
      apiPost: (route, values) => new Promise((resolve, reject) => requests.push({ method: "POST", route, values, resolve, reject })),
    },
    localStorage: localStore,
    sessionStorage: sessionStore,
    crypto: { randomUUID: () => "op-" + requests.length },
    lucide: { createIcons() {} },
    matchMedia: () => ({ matches: false }),
  };
  const fakeSetTimeout = (fn, ms) => {
    const id = timers.length + 1;
    timers.push({ id, fn, ms, cleared: false });
    return id;
  };
  const fakeClearTimeout = id => {
    const item = timers.find(t => t.id === id);
    if (item) item.cleared = true;
  };
  const baseGlobals = {
    window, document, localStorage: localStore, sessionStorage: sessionStore,
    Intl, Date, Math, Number, String, Array, Object, Map, Set, JSON, Promise, URL,
    setTimeout: fakeSetTimeout, clearTimeout: fakeClearTimeout,
    setInterval: fakeSetTimeout, clearInterval: fakeClearTimeout,
    requestAnimationFrame: cb => cb(),
    matchMedia: window.matchMedia,
    crypto: window.crypto, AbortController,
  };
  vm.runInNewContext(workbenchSource, { ...baseGlobals });
  vm.runInNewContext(pricingSource, { ...baseGlobals });
  vm.runInNewContext(quotaSource, { ...baseGlobals });
  vm.runInNewContext(modelsSource, { ...baseGlobals });

  const root = document.createElement("div");
  document.body.appendChild(root);

  window.CompanionModelsPage.mount(root, { initialTab: "quota" });
  await flush();
  requests.find(r => r.method === "GET" && r.route === "model/quota").resolve(sampleQuotaPayload());
  await flush();

  // Verify decoupled .qw-main inside .model-workspace-main and sibling .qw-modal-host physically outside wsMain
  const wsMain = root.querySelector(".model-workspace-main");
  const mainEl = root.querySelector("[data-qw-main]");
  const modalHost = root.querySelector("[data-qw-modal-host]");
  const cardRelayBefore = root.querySelector('[data-qw-card="st-relay"]');
  assert.ok(wsMain && mainEl && modalHost && cardRelayBefore);
  assert.equal(wsMain.contains(modalHost), false, ".qw-modal-host must live outside wsMain so wsMain updates never destroy an open modal");
  assert.equal(mainEl.contains(modalHost), false, ".qw-modal-host must be outside .qw-main");

  // Open edit modal on st-relay
  root.querySelector('[data-qw-edit="st-relay"]').dispatch("click");
  const backdropRef = modalHost.querySelector("[data-qw-modal-backdrop]");
  const modalRef = modalHost.querySelector(".qw-modal");
  const bodyRef = modalHost.querySelector("[data-qw-modal-body]");
  const nameInputRef = modalRef.querySelector('[data-qw-field="name"]');
  const testBtnRef = modalRef.querySelector("[data-qw-test-draft]");
  const saveBtnRef = modalRef.querySelector("[data-qw-save-station]");
  assert.ok(backdropRef && modalRef && bodyRef && nameInputRef && testBtnRef && saveBtnRef);
  assert.ok(backdropRef.classList.contains("is-entering"), "Initial open should trigger one-shot entrance animation");
  assert.ok(modalRef.classList.contains("is-entering"));

  // Finish one-shot entrance animation -> is-entering removed
  modalRef.dispatch("animationend");
  assert.equal(backdropRef.classList.contains("is-entering"), false);
  assert.equal(modalRef.classList.contains("is-entering"), false);

  // User scrolls modal body and focuses name input
  bodyRef.scrollTop = 240;
  nameInputRef.focus();
  assert.equal(document.activeElement, nameInputRef);

  // 1) Click「测试连通性」: pending -> success (ZERO rebuild, ZERO scroll jump, ZERO focus loss!)
  testBtnRef.dispatch("click");
  await flush();
  assert.equal(modalHost.querySelector("[data-qw-modal-backdrop]"), backdropRef, "Backdrop must be exact same DOM node during test pending");
  assert.equal(modalHost.querySelector(".qw-modal"), modalRef, "Modal must be exact same DOM node during test pending");
  assert.equal(modalHost.querySelector("[data-qw-test-draft]"), testBtnRef, "Test button itself must patch in place");
  assert.equal(testBtnRef.disabled, true);
  assert.equal(testBtnRef.textContent, "测试中…");
  assert.equal(backdropRef.classList.contains("is-entering"), false);
  assert.equal(modalRef.classList.contains("is-entering"), false);
  assert.equal(bodyRef.scrollTop, 240, "Scroll position must not jump on test pending");
  assert.equal(document.activeElement, nameInputRef, "Input focus must not be lost on test pending");

  const testReq1 = requests.filter(r => r.route === "model/quota/test").pop();
  testReq1.resolve({ snapshot: { ok: true, remaining: 42.5, display_type: "USD", symbol: "$", latency_ms: 88 } });
  await flush();
  assert.equal(modalHost.querySelector("[data-qw-modal-backdrop]"), backdropRef);
  assert.equal(modalHost.querySelector(".qw-modal"), modalRef);
  assert.equal(modalRef.querySelector('[data-qw-field="name"]'), nameInputRef);
  assert.equal(testBtnRef.disabled, false);
  assert.equal(testBtnRef.textContent, "测试连通性");
  assert.equal(bodyRef.scrollTop, 240, "Scroll position must remain 240 after test success");
  assert.equal(document.activeElement, nameInputRef, "Input focus must remain intact after test success");
  assert.equal(modalRef.classList.contains("is-entering"), false);
  assert.ok(modalRef.querySelector(".qw-test-preview.is-fresh"), "Only test result slot should softly reveal");

  // 2) Click「保存站点」: pending -> server error (ZERO modal rebuild/re-pop!)
  saveBtnRef.dispatch("click");
  await flush();
  assert.equal(modalHost.querySelector(".qw-modal"), modalRef);
  assert.equal(saveBtnRef.disabled, true);
  assert.equal(saveBtnRef.textContent, "保存并刷新中…");
  assert.equal(bodyRef.scrollTop, 240);
  assert.equal(modalRef.classList.contains("is-entering"), false);

  const saveErrReq = requests.filter(r => r.route === "model/quota/stations").pop();
  saveErrReq.reject(Object.assign(new Error("quota_config_conflict"), { code: "quota_config_conflict" }));
  await flush();
  assert.equal(modalHost.querySelector("[data-qw-modal-backdrop]"), backdropRef);
  assert.equal(modalHost.querySelector(".qw-modal"), modalRef);
  assert.equal(saveBtnRef.disabled, false);
  assert.equal(saveBtnRef.textContent, "保存站点");
  assert.match(modalRef.querySelector("[data-qw-modal-error]").innerHTML, /已在别处更新/);
  assert.equal(bodyRef.scrollTop, 240);
  assert.equal(document.activeElement, nameInputRef);
  assert.equal(modalRef.classList.contains("is-entering"), false);

  // 3) Background autoRefresh fires while modal is STILL open -> main panel reconciles cards in place, modal untouched!
  const autoTimer = timers.filter(t => !t.cleared && t.ms >= 60000).pop();
  assert.ok(autoTimer, "Auto refresh timer should be scheduled");
  autoTimer.fn();
  await flush();
  const bgReq = requests.filter(r => r.route === "model/quota/refresh").pop();
  assert.ok(bgReq);
  const refreshedPayload = sampleQuotaPayload();
  refreshedPayload.snapshots["st-relay"].remaining = 8.88;
  bgReq.resolve(refreshedPayload);
  await flush();
  assert.equal(root.querySelector('[data-qw-card="st-relay"]'), cardRelayBefore, "Existing card should be reconciled in place without grid flash");
  assert.match(cardRelayBefore.innerHTML, /\$8\.88/);
  assert.equal(modalHost.querySelector("[data-qw-modal-backdrop]"), backdropRef, "Background refresh must never touch modal backdrop");
  assert.equal(modalHost.querySelector(".qw-modal"), modalRef, "Background refresh must never touch modal shell");
  assert.equal(bodyRef.scrollTop, 240);
  assert.equal(document.activeElement, nameInputRef);
  assert.equal(modalRef.classList.contains("is-entering"), false);

  // 4) Step 2 -> Step 1 -> Step 2 switch keeps exact same backdrop & modal DOM shell (zero popup replay) and resets scrollTop cleanly
  modalRef.querySelector('[data-qw-step="1"]').dispatch("click");
  assert.equal(modalHost.querySelector("[data-qw-modal-backdrop]"), backdropRef);
  assert.equal(modalHost.querySelector(".qw-modal"), modalRef);
  assert.equal(bodyRef.scrollTop, 0);
  assert.equal(modalRef.classList.contains("is-entering"), false);

  modalRef.querySelector('[data-qw-step="2"]').dispatch("click");
  assert.equal(modalHost.querySelector("[data-qw-modal-backdrop]"), backdropRef);
  assert.equal(modalHost.querySelector(".qw-modal"), modalRef);
  assert.equal(modalRef.classList.contains("is-entering"), false);

  // 4b) Self-healing requires_user_id test inside Live DOM expands user_id without recreating modal or losing scroll
  bodyRef.scrollTop = 180;
  modalRef.querySelector("[data-qw-test-draft]").dispatch("click");
  await flush();
  const healReq = requests.filter(r => r.route === "model/quota/test").pop();
  healReq.resolve({
    snapshot: { ok: false, error_code: "requires_user_id", error: "需要 Rix-Api-User", detected_header: "Rix-Api-User" },
  });
  await flush();
  assert.equal(modalHost.querySelector("[data-qw-modal-backdrop]"), backdropRef);
  assert.equal(modalHost.querySelector(".qw-modal"), modalRef);
  assert.equal(bodyRef.scrollTop, 180);
  assert.equal(modalRef.classList.contains("is-entering"), false);
  const uidInput = modalRef.querySelector('[data-qw-field="user_id"]');
  assert.ok(uidInput, "Self-healing should expand user_id in place");
  assert.equal(document.activeElement, uidInput, "Self-healing should focus expanded user_id input");

  // 5) Save succeeds -> modal closes cleanly ONCE, main panel refreshes
  modalRef.querySelector("[data-qw-save-station]").dispatch("click");
  await flush();
  const saveOkReq = requests.filter(r => r.route === "model/quota/stations").pop();
  saveOkReq.resolve(sampleQuotaPayload({ edit_token: "tok-v3" }));
  await flush();
  assert.equal(modalHost.getAttribute("aria-hidden"), "true");
  assert.equal(modalHost.querySelector("[data-qw-modal-backdrop]"), null);
  assert.match(mainEl.querySelector("[data-qw-hero-core]").innerHTML, /站点已保存并完成实时刷新/);

  // 6) Step 1 (添加监测站点): source mode toggle, AstrBot provider pick, manual URL detect (pending->done), preset pick, and Step 1->2 keep exact same modal DOM shell
  mainEl.querySelector('[data-qw-open-wizard="manual"]').dispatch("click");
  const addBackdrop = modalHost.querySelector("[data-qw-modal-backdrop]");
  const addModal = modalHost.querySelector(".qw-modal");
  assert.ok(addBackdrop && addModal);
  assert.ok(addBackdrop.classList.contains("is-entering"));
  addModal.dispatch("animationend");
  assert.equal(addBackdrop.classList.contains("is-entering"), false);

  // Switch source mode manual -> astrbot_provider -> pick provider -> auto-detect pending & done
  addModal.querySelector('[data-qw-source-mode="astrbot_provider"]').dispatch("click");
  assert.equal(modalHost.querySelector("[data-qw-modal-backdrop]"), addBackdrop);
  assert.equal(modalHost.querySelector(".qw-modal"), addModal);
  assert.equal(addModal.classList.contains("is-entering"), false);

  addModal.querySelector('[data-qw-pick-provider="ds_official"]').dispatch("click");
  await flush();
  assert.equal(modalHost.querySelector(".qw-modal"), addModal);
  assert.equal(addModal.classList.contains("is-entering"), false);
  const detProvReq = requests.filter(r => r.route === "model/quota/detect").pop();
  detProvReq.resolve({ ok: true, adapter_id: "deepseek", confidence: "high", reason: "已识别为 DeepSeek" });
  await flush();
  assert.equal(modalHost.querySelector(".qw-modal"), addModal);
  assert.match(addModal.querySelector("[data-qw-detect-slot]").innerHTML, /已识别为 DeepSeek/);

  // Switch back to manual, focus URL input, run manual detect pending -> done without losing focus or re-animating modal
  addModal.querySelector('[data-qw-source-mode="manual"]').dispatch("click");
  const urlInput = addModal.querySelector('[data-qw-field="base_url"]');
  urlInput.value = "https://relay.example.com";
  urlInput.focus();
  urlInput.dispatch("input");
  const detectBtn = addModal.querySelector("[data-qw-detect]");
  detectBtn.dispatch("click");
  await flush();
  assert.equal(modalHost.querySelector(".qw-modal"), addModal);
  assert.equal(addModal.querySelector("[data-qw-detect]"), detectBtn);
  assert.equal(detectBtn.disabled, true);
  assert.equal(detectBtn.textContent, "识别中…");
  assert.equal(document.activeElement, urlInput, "URL input focus must survive manual detect");
  const detManReq = requests.filter(r => r.route === "model/quota/detect").pop();
  detManReq.resolve({ ok: true, adapter_id: "newapi", confidence: "high", reason: "已识别为 NewAPI" });
  await flush();
  assert.equal(modalHost.querySelector(".qw-modal"), addModal);
  assert.equal(detectBtn.disabled, false);
  assert.equal(detectBtn.textContent, "智能识别");
  assert.equal(document.activeElement, urlInput);

  // Pick protocol preset chip & proceed to Step 2 inside exact same modal shell
  addModal.querySelector('[data-qw-pick-adapter="newapi_user_id"]').dispatch("click");
  assert.equal(modalHost.querySelector(".qw-modal"), addModal);
  assert.ok(addModal.querySelector('[data-qw-pick-adapter="newapi_user_id"]').classList.contains("is-active"));
  addModal.querySelector('[data-qw-step="2"]').dispatch("click");
  assert.equal(modalHost.querySelector("[data-qw-modal-backdrop]"), addBackdrop);
  assert.equal(modalHost.querySelector(".qw-modal"), addModal);
  assert.equal(addModal.classList.contains("is-entering"), false);
  assert.ok(addModal.querySelector('[data-qw-field="user_id"]'));

  // Close add modal cleanly
  addModal.querySelector("[data-qw-close-wizard]").dispatch("click");
  assert.equal(modalHost.getAttribute("aria-hidden"), "true");

  // 7) Awareness policy panel toggles & edits without destroying station cards
  const cardBeforeAw = root.querySelector('[data-qw-card="st-relay"]');
  mainEl.querySelector("[data-qw-toggle-awareness]").dispatch("click");
  assert.ok(mainEl.querySelector(".qw-awareness-panel"));
  assert.equal(root.querySelector('[data-qw-card="st-relay"]'), cardBeforeAw, "Opening awareness panel must not rebuild station cards");

  window.CompanionModelsPage.unmount();
});

test("Dual-unit toggle switches custom currency display in-place, persists preference, and renders active subscription sub-line", async () => {
  const h = quotaHarness();
  const payload = sampleQuotaPayload({
    summary: {
      display_currency: "CNY",
      display_symbol: "¥",
      usd_cny_rate: 7.2,
      total_stations: 1,
      ok_count: 1,
      low_count: 0,
      critical_count: 0,
      error_count: 0,
      fiat_remaining: 0,
      fiat_remaining_including_custom: 63.61,
      custom_converted_remaining: 63.61,
      custom_converted_count: 1,
      cny_remaining: 0,
      usd_remaining: 0,
      cny_station_count: 0,
      usd_station_count: 0,
      non_fiat_station_count: 1,
      health_by_id: { "st-cookie": "ok" },
      converted_remaining_by_id: { "st-cookie": 63.61 },
    },
    stations: [
      {
        id: "st-cookie",
        name: "饼干中转站",
        adapter_id: "newapi",
        source_mode: "manual",
        astrbot_provider_id: "",
        base_url: "https://cookie.example.com",
        enabled: true,
        participate_in_awareness: true,
        low_threshold: 10,
        critical_threshold: 2,
        has_token: true,
        has_proxy: false,
        fiat_currency: "CNY",
        fiat_per_base_unit: 0.1,
        display_unit_mode: "native",
      },
    ],
    snapshots: {
      "st-cookie": {
        station_id: "st-cookie",
        ok: true,
        remaining: 636.06,
        total: 1000,
        used: 363.94,
        display_type: "CREDIT",
        symbol: "🍪",
        fiat_currency: "CNY",
        fiat_symbol: "¥",
        fiat_per_base_unit: 0.1,
        fiat_remaining: 63.61,
        fiat_total: 100.0,
        fiat_used: 36.39,
        fiat_last_used_cost: 0.15,
        last_used_model: "claude-sonnet-4",
        last_used_cost: 1.5,
        last_used_at: "2026-11-25T06:00:00+08:00",
        subscriptions: [
          { title: "月卡 Plus", remaining: 4180, total: 5000, fiat_remaining: 418, active: true },
          { title: "周卡", remaining: 820, total: 1000, fiat_remaining: 82, active: true },
        ],
        checked_at: "2026-11-25T06:10:00+08:00",
      },
    },
    awareness: { mode: "primary", primary_station_id: "st-cookie", target_currency: "CNY" },
  });

  h.page.mount(h.root, { initialTab: "quota" });
  await flush();
  h.requests.find(r => r.route === "model/quota").resolve(payload);
  await flush();

  assert.match(h.root.innerHTML, /qw-balance-value[^>]*>636\.06<\/span>[\s\S]*?qw-balance-unit">🍪<\/span>/);
  assert.match(h.root.innerHTML, /≈ ¥63\.61/);
  assert.match(h.root.innerHTML, /data-qw-toggle-unit="st-cookie"/);
  assert.match(h.root.innerHTML, /🍪 ↔ ¥/);
  assert.match(h.root.innerHTML, /qw-sub-strip/);
  assert.match(h.root.innerHTML, /订阅余量[\s\S]*?5,000 🍪 ≈ ¥500/);
  assert.match(h.root.innerHTML, /2 个套餐/);
  assert.match(h.root.innerHTML, /含折合共 ¥63\.61/);

  // Toggle unit mode native -> fiat
  h.root.click("[data-qw-toggle-unit]", { qwToggleUnit: "st-cookie" });
  await flush();

  assert.match(h.root.innerHTML, /qw-balance-value[^>]*>¥63\.61<\/span>/);
  assert.match(h.root.innerHTML, /qw-balance-conv[^>]*>636\.06 🍪<\/span>/);
  const saveReq = h.requests.find(r => r.method === "POST" && r.route === "model/quota/stations");
  assert.ok(saveReq, "Unit toggle should silently persist display_unit_mode");
  assert.equal(saveReq.values.probe_after_save, false);
  assert.equal(saveReq.values.station.display_unit_mode, "fiat");

  h.page.unmount();
});

test("Primary station KPI, 1-click set primary, focusQuota(awareness), and 5+2 models tabs polish work together", async () => {
  const h = quotaHarness();
  h.page.mount(h.root, { initialTab: "quota" });
  await flush();
  h.requests.find(r => r.route === "model/quota").resolve(sampleQuotaPayload());
  await flush();

  const html = h.root.innerHTML;
  // 5+2 tab separator wrapper & divider
  assert.match(html, /class="model-tabs-wrap"/);
  assert.match(html, /class="model-tabs-sep"/);

  // Hero KPI #4 and primary card badge + 1-click set primary button
  assert.match(html, /主站 · DeepSeek 官方/);
  assert.match(html, /★ 主站/);
  assert.match(html, /data-qw-set-primary="st-relay"/);
  assert.match(html, /设为主站/);

  // 1-click set primary on st-relay
  h.root.click("[data-qw-set-primary]", { qwSetPrimary: "st-relay" });
  await flush();
  const awReq = h.requests.find(r => r.method === "POST" && r.route === "model/quota/awareness");
  assert.ok(awReq, "Clicking 设为主站 should POST model/quota/awareness");
  assert.equal(awReq.values.awareness.mode, "primary");
  assert.equal(awReq.values.awareness.primary_station_id, "st-relay");
  assert.ok(awReq.values.awareness.participating_station_ids.includes("st-relay"));
  awReq.resolve(sampleQuotaPayload({
    edit_token: "tok-v2",
    awareness: { mode: "primary", primary_station_id: "st-relay", target_currency: "CNY" },
  }));
  await flush();
  assert.match(h.root.innerHTML, /主站 · 主力中转站/);

  // Deep-link focusQuota("awareness") opens the awareness policy panel
  h.page.focusQuota("awareness");
  await flush();
  assert.match(h.root.innerHTML, /qw-awareness-panel/);
  assert.match(h.root.innerHTML, /data-qw-aw-field="primary_station_id"/);

  // Switch to search tab with deepseek_provider_id bound -> renders compact green .c-dot instead of wide text pill
  h.root.click("[data-model-tab]", { modelTab: "search" });
  await flush();
  const searchReq = h.requests.find(r => r.method === "GET" && r.route === "model/search");
  if (searchReq) {
    searchReq.resolve({ deepseek_provider_id: "ds_official", providers: [] });
    await flush();
    assert.match(h.root.innerHTML, /data-model-tab="search"[\s\S]*?<span class="c-dot" title="已绑定"/);
  }

  h.page.unmount();
});

test("Polish: openAwareness honors reduced motion, resize listener cleans up on unmount, and toggleUnitMode serializes rapid clicks", async () => {
  const h = quotaHarness();
  h.document.documentElement.dataset.motion = "off";
  const payload = sampleQuotaPayload({
    edit_token: "tok-u1",
    stations: [
      {
        id: "st-cookie",
        name: "饼干站",
        adapter_id: "newapi",
        source_mode: "manual",
        astrbot_provider_id: "",
        base_url: "https://cookie.example.com",
        enabled: true,
        participate_in_awareness: true,
        has_token: true,
        has_proxy: false,
        fiat_currency: "CNY",
        fiat_per_base_unit: 0.1,
        display_unit_mode: "native",
      },
    ],
    snapshots: {
      "st-cookie": {
        station_id: "st-cookie",
        ok: true,
        remaining: 100,
        display_type: "CREDIT",
        symbol: "🍪",
        fiat_currency: "CNY",
        fiat_symbol: "¥",
        fiat_per_base_unit: 0.1,
        fiat_remaining: 10.0,
        checked_at: "2026-11-25T08:00:00+08:00",
      },
    },
    awareness: { mode: "primary", primary_station_id: "st-cookie", target_currency: "CNY" },
  });

  h.page.mount(h.root, { initialTab: "quota" });
  await flush();
  assert.equal(h.winHandlers.get("resize")?.size, 1, "mount should register one window resize listener");

  h.requests.find(r => r.route === "model/quota").resolve(payload);
  await flush();

  // 1) openAwareness respects reduced motion -> scrollIntoView({ behavior: "auto" })
  h.page.focusQuota("awareness");
  await flush();
  assert.equal(h.scrollCalls.length, 1);
  assert.equal(h.scrollCalls[0].opts.behavior, "auto");

  // 2) Rapid double-click on toggleUnitMode: native -> fiat -> native -> ONLY 1 in-flight POST at a time,
  //    then serializes 2nd POST with updated edit_token ("tok-u2") instead of racing with stale "tok-u1"!
  h.root.click("[data-qw-toggle-unit]", { qwToggleUnit: "st-cookie" });
  h.root.click("[data-qw-toggle-unit]", { qwToggleUnit: "st-cookie" });
  await flush();
  const posts1 = h.requests.filter(r => r.method === "POST" && r.route === "model/quota/stations");
  assert.equal(posts1.length, 1, "Rapid clicks must not fire concurrent POSTs");
  assert.equal(posts1[0].values.edit_token, "tok-u1");
  assert.equal(posts1[0].values.station.display_unit_mode, "fiat");

  // Resolve 1st POST with refreshed edit_token -> queued 2nd toggle ("native") fires automatically with "tok-u2"
  posts1[0].resolve({
    edit_token: "tok-u2",
    stations: [{ ...payload.stations[0], display_unit_mode: "fiat" }],
    summary: payload.summary,
  });
  await flush();
  const posts2 = h.requests.filter(r => r.method === "POST" && r.route === "model/quota/stations");
  assert.equal(posts2.length, 2, "Queued second toggle should flush after first POST finishes");
  assert.equal(posts2[1].values.edit_token, "tok-u2");
  assert.equal(posts2[1].values.station.display_unit_mode, "native");
  posts2[1].resolve({
    edit_token: "tok-u3",
    stations: [{ ...payload.stations[0], display_unit_mode: "native" }],
    summary: payload.summary,
  });
  await flush();
  assert.match(h.root.innerHTML, /qw-balance-value[^>]*>100<\/span>/);

  // 3) Unmount removes window resize listener cleanly
  h.page.unmount();
  assert.equal(h.winHandlers.get("resize")?.size || 0, 0, "unmount must remove window resize listener");
});
