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
  const docHandlers = {};
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
          scrollIntoView() {},
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
