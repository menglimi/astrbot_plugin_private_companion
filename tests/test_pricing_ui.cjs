"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const PAGE_DIR = path.join(ROOT, "pages/陪伴小屋");
const maintSource = fs.readFileSync(path.join(PAGE_DIR, "maintenance-page.js"), "utf8");
const workbenchSource = fs.readFileSync(path.join(PAGE_DIR, "workbench-page.js"), "utf8");
const pricingSource = fs.readFileSync(path.join(PAGE_DIR, "pricing-workbench.js"), "utf8");
const modelsSource = fs.readFileSync(path.join(PAGE_DIR, "models-page.js"), "utf8");
const contactSource = fs.readFileSync(path.join(PAGE_DIR, "contact-page.js"), "utf8");
const flush = () => new Promise(resolve => setImmediate(resolve));

function localDay(date = new Date()) {
  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, "0"),
    String(date.getDate()).padStart(2, "0"),
  ].join("-");
}

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

function sampleV2Payload(overrides = {}) {
  return {
    version: 2,
    display_currency: "CNY",
    use_builtin_presets: true,
    pricing_timezone: "Asia/Shanghai",
    available_timezones: ["Asia/Shanghai", "UTC", "America/New_York", "Asia/Tokyo"],
    exchange_rate: {
      mode: "auto",
      manual_usd_cny: null,
      cached_usd_cny: 7.22,
      fallback_usd_cny: 7.2,
      effective_usd_cny: 7.22,
      fetched_at: "2026-11-21T08:00:00+00:00",
      source: "open.er-api.com",
      effective_source: "open.er-api.com",
      stale: false,
      ttl_hours: 12,
    },
    price_sync: {
      auto_daily: true,
      last_auto_sync_at: "2026-11-21T04:00:00+00:00",
    },
    sources: [
      {
        source_id: "modelsdev", kind: "public", label: "models.dev", url: "https://models.dev/catalog.json",
        enabled: true, status: "ok", model_count: 9534,
        last_sync_at: "2026-11-21T08:00:00+00:00", last_ok_at: "2026-11-21T08:00:00+00:00", error: null,
      },
      {
        source_id: "litellm", kind: "public", label: "LiteLLM", url: "https://example.invalid/litellm.json",
        enabled: true, status: "error", model_count: 3285,
        last_sync_at: "2026-11-21T08:00:00+00:00", last_ok_at: "2026-11-18T08:00:00+00:00", error: "连接超时",
      },
      {
        source_id: "openrouter", kind: "public", label: "OpenRouter", url: "https://openrouter.ai/api/v1/models",
        enabled: false, status: "disabled", model_count: 470,
        last_sync_at: null, last_ok_at: null, error: null,
      },
      {
        source_id: "newapi:relay-main", kind: "newapi", label: "New API · 中转主站", url: "https://relay.example.com/api/pricing",
        enabled: true, status: "ok", model_count: 128, provider_id: "relay-claude",
        last_sync_at: "2026-11-21T08:00:00+00:00", last_ok_at: "2026-11-21T08:00:00+00:00", error: null,
      },
    ],
    catalog_updated_at: "2026-11-21T08:00:00+00:00",
    provider_tariffs: {},
    pricing_rules: {},
    pricing_multipliers: {},
    pricing_schedules: {},
    price_selections: {},
    presets: [
      {
        preset_id: "claude-sonnet-4-5", vendor: "Anthropic", label: "Claude Sonnet 4.5",
        currency: "USD", input_per_million: 3, cached_input_per_million: 0.3,
        cache_creation_per_million: 3.75, output_per_million: 15,
      },
    ],
    clusters: [
      {
        cluster_id: "unused-src",
        name: "闲置供应商",
        provider_source_id: "unused-src",
        multiplier: 1,
        has_custom_multiplier: false,
        has_bound: false,
        active_count: 0,
        provider_count: 1,
        cluster_total_tokens: 0,
        providers: [
          {
            id: "idle-prov", model: "gpt-4o-mini", candidates: ["gpt-4o-mini"],
            configured: true, enabled: true, loaded: false, bound: false,
            cluster_id: "unused-src", supplier_name: "闲置供应商", has_api_base: false,
            total_tokens: 0, calls: 0, requests: 0, input_tokens: 0, cached_input_tokens: 0,
            cache_creation_tokens: 0, output_tokens: 0,
            effective_source: "builtin_preset", effective_mode: "per_token",
            effective_rule: {mode: "per_token", currency: "USD", input: 0.15, input_cached: 0.075, output: 0.6},
            effective_tariff_usd: {input_per_million: 0.15, cached_input_per_million: 0.075, cache_creation_per_million: null, output_per_million: 0.6},
            cluster_multiplier: 1, schedule_active: false, schedule_period_count: 0,
          },
        ],
      },
      {
        cluster_id: "relay-src",
        name: "中转主站",
        provider_source_id: "relay-src",
        multiplier: 1,
        has_custom_multiplier: false,
        has_bound: true,
        active_count: 2,
        provider_count: 3,
        cluster_total_tokens: 651400000,
        providers: [
          {
            id: "relay-idle", model: "deepseek-chat", candidates: ["deepseek-chat"],
            configured: true, enabled: true, loaded: true, bound: false,
            cluster_id: "relay-src", supplier_name: "中转主站", has_api_base: true,
            total_tokens: 0, calls: 0, requests: 0,
            effective_source: "builtin_preset", effective_mode: "per_token",
            effective_rule: {mode: "per_token", currency: "USD", input: 0.28, input_cached: 0.028, output: 0.42},
            cluster_multiplier: 1, schedule_active: false, schedule_period_count: 0,
          },
          {
            id: "relay-claude", model: "claude-sonnet-4-5", candidates: ["claude-sonnet-4-5", "claude-opus-4-6"],
            configured: true, enabled: true, loaded: true, bound: true,
            cluster_id: "relay-src", supplier_name: "中转主站", has_api_base: true,
            newapi_source_id: "newapi:relay-main",
            total_tokens: 25075403, calls: 320, requests: 180,
            input_tokens: 20000000, cached_input_tokens: 15000000, cache_creation_tokens: 1000000, output_tokens: 5075403,
            preset_match: {
              preset_id: "claude-sonnet-4-5", vendor: "Anthropic", label: "Claude Sonnet 4.5",
              currency: "USD", input_per_million: 3, cached_input_per_million: 0.3,
              cache_creation_per_million: 3.75, output_per_million: 15,
            },
            effective_source: "catalog_auto", effective_mode: "per_token",
            effective_rule: {mode: "per_token", currency: "USD", input: 3, input_cached: 0.3, cache_creation: 3.75, output: 15},
            effective_tariff_usd: {input_per_million: 3, cached_input_per_million: 0.3, cache_creation_per_million: 3.75, output_per_million: 15},
            cluster_multiplier: 1, schedule_active: false, schedule_period_count: 0,
          },
          {
            id: "relay-custom", model: "my-unpriced-model", candidates: ["my-unpriced-model"],
            configured: true, enabled: true, loaded: true, bound: true,
            cluster_id: "relay-src", supplier_name: "中转主站", has_api_base: true,
            total_tokens: 626324597, calls: 4200, requests: 2100,
            input_tokens: 500000000, cached_input_tokens: 0, cache_creation_tokens: 0, output_tokens: 126324597,
            preset_match: null,
            effective_source: "unconfigured", effective_mode: null,
            effective_rule: null, effective_tariff_usd: null,
            cluster_multiplier: 1, schedule_active: false, schedule_period_count: 0,
          },
        ],
      },
    ],
    deleted_providers: [
      {
        id: "old-small-prov", model: "legacy-small", candidates: ["legacy-small"],
        configured: false, enabled: false, loaded: false, bound: false,
        cluster_id: "old-small-prov", supplier_name: "old-small-prov",
        total_tokens: 12000, calls: 4, requests: 2,
        effective_source: "unconfigured", has_override: true, has_schedule: false, has_selection: false,
      },
      {
        id: "old-heavy-prov", model: "legacy-heavy", candidates: ["legacy-heavy"],
        configured: false, enabled: false, loaded: false, bound: false,
        cluster_id: "old-heavy-prov", supplier_name: "old-heavy-prov",
        total_tokens: 9800000, calls: 500, requests: 250,
        effective_source: "unconfigured", has_override: true, has_schedule: false, has_selection: false,
      },
    ],
    unpriced: [
      {
        provider_id: "old-heavy-prov", cluster_id: "old-heavy-prov", supplier_name: "old-heavy-prov",
        deleted: true, total_tokens: 9800000, calls: 500, model_count: 1,
        models: [{model: "legacy-heavy", total_tokens: 9800000, calls: 500}],
      },
      {
        provider_id: "relay-custom", cluster_id: "relay-src", supplier_name: "中转主站",
        deleted: false, total_tokens: 626324597, calls: 4200, model_count: 1,
        models: [{model: "my-unpriced-model", total_tokens: 626324597, calls: 4200}],
      },
    ],
    candidates: {
      "relay-claude": {
        "claude-sonnet-4-5": [
          {
            price_key: "newapi:relay-main::claude-sonnet-4-5",
            source_id: "newapi:relay-main",
            source_label: "New API · 中转主站",
            model_id: "claude-sonnet-4-5",
            vendor: "Anthropic",
            display_name: "Claude Sonnet 4.5",
            score: 1.0,
            is_own_gateway: true,
            rule: {mode: "per_token", currency: "USD", input: 2.4, input_cached: 0.24, cache_creation: 3.0, output: 12},
          },
          {
            price_key: "modelsdev::anthropic/claude-sonnet-4-5",
            source_id: "modelsdev",
            source_label: "models.dev",
            model_id: "anthropic/claude-sonnet-4-5",
            vendor: "Anthropic",
            display_name: "Claude Sonnet 4.5",
            score: 0.96,
            is_own_gateway: false,
            rule: {mode: "per_token", currency: "USD", input: 3, input_cached: 0.3, cache_creation: 3.75, output: 15},
          },
        ],
      },
    },
    auto_selected: {
      "relay-claude": {
        "claude-sonnet-4-5": {
          price_key: "newapi:relay-main::claude-sonnet-4-5",
          source_id: "newapi:relay-main",
          source_label: "New API · 中转主站",
          model_id: "claude-sonnet-4-5",
          score: 1.0,
          is_own_gateway: true,
          rule: {mode: "per_token", currency: "USD", input: 2.4, input_cached: 0.24, cache_creation: 3.0, output: 12},
        },
      },
    },
    selections: {},
    revision: "rev-v2-init",
    ...overrides,
  };
}

function maintHarness(responses) {
  const listeners = {};
  const requests = [];
  const localStore = makeStorage();
  const sessionStore = makeStorage();
  const bridge = {
    apiGet: async rawRoute => {
      const route = (rawRoute || "").startsWith("/") ? rawRoute : "/" + rawRoute;
      requests.push(["GET", route]);
      if (route.endsWith("/maintenance")) return responses.maintenance;
      if (route.endsWith("/balance")) return {configured: false, available: false, state: "not_configured"};
      if (route.endsWith("/voice")) return {providers: []};
      if (route.endsWith("/image")) return {actions: []};
      if (route === "/model") return {tasks: [], providers: []};
      throw new Error("unexpected GET " + route);
    },
    apiPost: async (rawRoute, body) => {
      const route = (rawRoute || "").startsWith("/") ? rawRoute : "/" + rawRoute;
      requests.push(["POST", route, body]);
      const handler = responses.posts?.[route];
      return typeof handler === "function" ? handler(body) : (handler || {});
    },
  };
  const window = {
    CompanionProduct: {
      role: () => ({model_api: "/astrbot_plugin_private_companion/model", config_api: "/astrbot_plugin_private_companion/config", role_id: "role-a"}),
      bridgeNow: () => bridge,
    },
    lucide: {createIcons() {}},
    localStorage: localStore,
    sessionStorage: sessionStore,
  };
  const root = {
    innerHTML: "",
    addEventListener(type, fn) { listeners[type] = fn; },
    removeEventListener(type) { delete listeners[type]; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    click(selector, dataset = {}) {
      listeners.click({target: {closest: sel => sel === selector ? {dataset} : null}});
    },
  };
  vm.runInNewContext(maintSource, {
    window, localStorage: localStore, sessionStorage: sessionStore,
    Intl, Date, Math, Number, String, Array, Object, Map, Set, JSON, Promise,
    URLSearchParams, setTimeout, clearTimeout,
  });
  return {page: window.CompanionMaintenancePage, root, requests, localStore, sessionStore};
}

function modelsHarness() {
  const requests = [];
  const localStore = makeStorage();
  const sessionStore = makeStorage();
  const scrolled = [];
  const removedPulse = [];
  const timers = [];
  const window = {
    CompanionProduct: {
      hosted: () => true,
      role: () => ({model_api: "/astrbot_plugin_private_companion/model"}),
      identity: () => ({installation_id: "inst", operator_id: "operator"}),
      bridgeNow: () => window.AstrBotPluginPage,
      bridgeFor: () => ({then: resolve => resolve(window.AstrBotPluginPage)}),
    },
    AstrBotPluginPage: {
      apiGet: (route, params) => new Promise((resolve, reject) => requests.push({method: "GET", route, params, resolve, reject})),
      apiPost: (route, values) => new Promise((resolve, reject) => requests.push({method: "POST", route, values, resolve, reject})),
    },
    localStorage: localStore,
    sessionStorage: sessionStore,
    crypto: {randomUUID: () => "op-" + requests.length},
    lucide: {createIcons() {}},
  };
  const fakeSetTimeout = (fn, ms) => {
    const id = timers.length + 1;
    timers.push({id, fn, ms, cleared: false});
    return id;
  };
  const fakeClearTimeout = id => {
    const item = timers.find(t => t.id === id);
    if (item) item.cleared = true;
  };
  vm.runInNewContext(workbenchSource, {window, setTimeout: fakeSetTimeout, clearTimeout: fakeClearTimeout});
  vm.runInNewContext(pricingSource, {
    window, localStorage: localStore, sessionStorage: sessionStore,
    Intl, Date, Math, Number, String, Array, Object, Map, Set, JSON, Promise,
    setTimeout: fakeSetTimeout, clearTimeout: fakeClearTimeout,
  });
  vm.runInNewContext(modelsSource, {
    window, localStorage: localStore, sessionStorage: sessionStore,
    crypto: window.crypto, AbortController,
    setTimeout: fakeSetTimeout, clearTimeout: fakeClearTimeout,
  });
  const handlers = {};
  const root = {
    isConnected: true,
    innerHTML: "",
    addEventListener(type, fn) { handlers[type] = fn; },
    removeEventListener(type) { delete handlers[type]; },
    querySelector() { return null; },
    querySelectorAll(selector) {
      if (selector === "[data-pw-card]") {
        const matches = [...root.innerHTML.matchAll(/data-pw-card="([^"]+)"/g)];
        return matches.map(m => ({
          dataset: {pwCard: m[1]},
          scrollIntoView: opts => scrolled.push({id: m[1], opts}),
          classList: {
            contains: cls => cls === "is-pulse",
            remove: cls => { if (cls === "is-pulse") removedPulse.push(m[1]); },
          },
        }));
      }
      return [];
    },
    click(selector, dataset = {}) {
      handlers.click({target: {closest: sel => sel === selector ? {dataset} : null}});
    },
    input(selector, value, {dataset = {}, checked = false, type = "change"} = {}) {
      handlers[type]({
        type,
        target: {
          value, checked, dataset,
          matches: sel => sel === selector,
          closest: sel => sel === selector ? {dataset} : null,
        },
      });
    },
  };
  return {
    page: window.CompanionModelsPage,
    wb: window.CompanionPricingWorkbench,
    root, requests, localStore, sessionStore, scrolled, removedPulse, timers,
  };
}

test("maintenance page currency formatter & switcher respect CNY/USD, FX pill, and pricing tab shortcut", async () => {
  const today = localDay();
  const h = maintHarness({
    maintenance: {
      generated_at: today + "T12:00:00+08:00",
      execution_path: "v2",
      cost_availability: "estimated_local",
      pricing: {
        display_currency: "CNY", usd_cny_rate: 7.25, rate_mode: "auto",
        rate_source: "open.er-api.com", rate_updated_at: today + "T08:00:00+08:00",
        rate_stale: false, use_builtin_presets: true, configured_provider_count: 1, revision: "rev-1",
      },
      summary: {calls: 1, tokens: 1000000, input_tokens: 800000, output_tokens: 200000, cost_microusd: 1000000, cost_basis: "estimated_local"},
      daily: [{
        key: today, day: today, calls: 2, tokens: 1050000, input_tokens: 840000, output_tokens: 210000, cost_microusd: 1000000, cost_basis: "estimated_local",
        providers: [
          {key: "p-priced", calls: 1, tokens: 1000000, input_tokens: 800000, output_tokens: 200000, cost_microusd: 1000000, cost_basis: "estimated_local"},
          {key: "p-unpriced", calls: 1, tokens: 50000, input_tokens: 40000, output_tokens: 10000, cost_microusd: null, cost_basis: "unconfigured"},
        ],
        tasks: [{key: "memory.reflect", calls: 1, tokens: 1000000, input_tokens: 800000, output_tokens: 200000, cost_microusd: 1000000, cost_basis: "estimated_local"}],
      }],
      history: [{
        key: today, day: today, calls: 2, tokens: 1050000, input_tokens: 840000, output_tokens: 210000, cost_microusd: 1000000, cost_basis: "estimated_local",
        providers: [
          {key: "p-priced", calls: 1, tokens: 1000000, input_tokens: 800000, output_tokens: 200000, cost_microusd: 1000000, cost_basis: "estimated_local"},
          {key: "p-unpriced", calls: 1, tokens: 50000, input_tokens: 40000, output_tokens: 10000, cost_microusd: null, cost_basis: "unconfigured"},
        ],
        tasks: [{key: "memory.reflect", calls: 1, tokens: 1000000, input_tokens: 800000, output_tokens: 200000, cost_microusd: 1000000, cost_basis: "estimated_local"}],
      }],
      providers: [
        {key: "p-priced", calls: 1, tokens: 1000000, input_tokens: 800000, output_tokens: 200000, cost_microusd: 1000000, cost_basis: "estimated_local"},
        {key: "p-unpriced", calls: 1, tokens: 50000, input_tokens: 40000, output_tokens: 10000, cost_microusd: null, cost_basis: "unconfigured"},
      ],
      tasks: [{key: "memory.reflect", calls: 1, tokens: 1000000, input_tokens: 800000, output_tokens: 200000, cost_microusd: 1000000, cost_basis: "estimated_local"}],
      recent: [{at: today + "T11:30:00+08:00", kind: "memory.reflect", provider: "p-priced", state: "completed", tokens: 1000000, input_tokens: 800000, output_tokens: 200000, cost_microusd: 1000000, cost_basis: "estimated_local"}],
      windows: {
        today: {
          providers: [
            {key: "p-priced", calls: 1, tokens: 1000000, input_tokens: 800000, output_tokens: 200000, cost_microusd: 1000000, cost_basis: "estimated_local"},
            {key: "p-unpriced", calls: 1, tokens: 50000, input_tokens: 40000, output_tokens: 10000, cost_microusd: null, cost_basis: "unconfigured"},
          ],
          tasks: [{key: "memory.reflect", calls: 1, tokens: 1000000, input_tokens: 800000, output_tokens: 200000, cost_microusd: 1000000, cost_basis: "estimated_local"}],
        },
      },
    },
    posts: {
      "/model/pricing": body => ({
        display_currency: body.changes.display_currency,
        exchange_rate: {effective_usd_cny: 7.25, mode: "auto", effective_source: "open.er-api.com"},
        revision: "rev-2",
      }),
      "/model/pricing/sync-rates": () => ({
        display_currency: "USD",
        exchange_rate: {effective_usd_cny: 7.31, mode: "auto", effective_source: "frankfurter.app", fetched_at: today + "T12:05:00+08:00", stale: false},
        sync: {synced: true, usd_cny_rate: 7.31},
        revision: "rev-2",
      }),
    },
  });

  assert.equal(h.page.formatCurrencyAmount(1000000, {currency: "CNY", rate: 7.25}), "¥7.25");
  assert.equal(h.page.formatCurrencyAmount(1000000, {currency: "USD", rate: 7.25}), "US$1.00");
  assert.equal(h.page.formatCurrencyAmount(null, {currency: "CNY", rate: 7.25}), "未设单价");

  h.page.mount(h.root);
  await flush();
  await flush();
  assert.match(h.root.innerHTML, /¥7\.25/);
  assert.match(h.root.innerHTML, /1 USD = ¥7\.25/);
  assert.match(h.root.innerHTML, /设置模型单价/);

  h.root.click("[data-token-detail]", {tokenDetail: "providers"});
  assert.match(h.root.innerHTML, /maint-cost-unconfigured">未设单价/);
  assert.doesNotMatch(h.root.innerHTML, /US\$0\.000000/);

  h.root.click("[data-currency-switch]", {currencySwitch: "USD"});
  await flush();
  await flush();
  assert.match(h.root.innerHTML, /US\$1\.00/);
  const saveCall = h.requests.find(r => r[0] === "POST" && r[1] === "/model/pricing");
  assert.ok(saveCall);
  assert.equal(saveCall[2].changes.display_currency, "USD");

  h.root.click("[data-pricing-sync-rates]");
  await flush();
  await flush();
  assert.match(h.root.innerHTML, /1 USD = ¥7\.31/);

  assert.match(h.root.innerHTML, /data-open-pricing-settings\s+data-go="manage"\s+data-manage="models"\s+data-manage-subtab="pricing"/);
  h.root.click("[data-open-pricing-settings]");
  assert.equal(h.sessionStore.getItem("companion-models-pending-tab.v1"), null);
  h.page.unmount();
});

test("Pricing v2 workbench enforces active-first + token-DESC order across clusters, models, unpriced alerts & deleted residue", async () => {
  const h = modelsHarness();
  h.sessionStore.setItem("companion-models-pending-tab.v1", "pricing");
  h.page.mount(h.root);
  assert.equal(h.sessionStore.getItem("companion-models-pending-tab.v1"), null);

  const req = h.requests.find(r => r.method === "GET" && r.route === "model/pricing");
  assert.ok(req);
  req.resolve(sampleV2Payload());
  await flush();

  const html = h.root.innerHTML;
  assert.match(html, /计价工作台/);
  assert.match(html, /启用内置主流模型参考价/);
  // Left rail: active cluster '中转主站' MUST appear before unused '闲置供应商', separated by divider!
  const idxActiveCluster = html.indexOf('data-pricing-select-cluster="relay-src"');
  const idxDivider = html.indexOf("暂未产生用量");
  const idxIdleCluster = html.indexOf('data-pricing-select-cluster="unused-src"');
  assert.ok(idxActiveCluster > 0 && idxDivider > idxActiveCluster && idxIdleCluster > idxDivider);

  // Right pane: relay-custom (626M) -> relay-claude (25M) -> divider -> relay-idle (0)
  const idxHeavy = html.indexOf('data-pw-card="relay-custom"');
  const idxMid = html.indexOf('data-pw-card="relay-claude"');
  const idxUnusedDiv = html.indexOf("已配置 · 暂未产生调用");
  const idxIdle = html.indexOf('data-pw-card="relay-idle"');
  assert.ok(idxHeavy > 0 && idxMid > idxHeavy && idxUnusedDiv > idxMid && idxIdle > idxUnusedDiv);

  // Unpriced alerts: 626M relay-custom BEFORE 9.8M old-heavy-prov!
  const idxAlertHeavy = html.indexOf('data-pricing-jump-provider="relay-custom"');
  const idxAlertDel = html.indexOf('data-pricing-jump-provider="old-heavy-prov"');
  assert.ok(idxAlertHeavy > 0 && idxAlertDel > idxAlertHeavy);
  assert.match(html, /626,324,597 token/);

  // Deleted residue section sorted DESC when expanded
  h.root.click("[data-pricing-toggle-residue]");
  const residueHtml = h.root.innerHTML;
  const idxOldHeavy = residueHtml.indexOf('data-pw-card="old-heavy-prov"');
  const idxOldSmall = residueHtml.indexOf('data-pw-card="old-small-prov"');
  assert.ok(idxOldHeavy > 0 && idxOldSmall > idxOldHeavy);
  assertNoNestedLabels(residueHtml, "overview+residue");
  h.page.unmount();
});

test("price sources bar toggles sources, syncs catalog, and saves auto_daily cleanly", async () => {
  const h = modelsHarness();
  h.page.mount(h.root);
  h.requests[0].resolve({tasks: [], providers: [], host_state: "running"});
  await flush();
  h.root.click("[data-model-tab]", {modelTab: "pricing"});
  h.requests.find(r => r.route === "model/pricing").resolve(sampleV2Payload());
  await flush();

  // Chips render statuses & counts
  assert.match(h.root.innerHTML, /models\.dev/);
  assert.match(h.root.innerHTML, /9,534/);
  assert.match(h.root.innerHTML, /失败/);
  assert.match(h.root.innerHTML, /连接超时/);

  // Click openrouter chip (disabled -> enable + sync)
  h.root.click("[data-pricing-source-toggle]", {pricingSourceToggle: "openrouter"});
  const toggleReq = h.requests.find(r => r.method === "POST" && r.route === "model/pricing/sources/toggle");
  assert.ok(toggleReq);
  assert.deepEqual(JSON.parse(JSON.stringify(toggleReq.values)), {
    source_id: "openrouter", enabled: true, sync: true, revision: "rev-v2-init",
  });
  toggleReq.resolve({
    ...sampleV2Payload({revision: "rev-v2-toggled"}),
    source_id: "openrouter", enabled: true, sync: {ok_count: 1, error_count: 0},
  });
  await flush();
  assert.match(h.root.innerHTML, /OpenRouter 已启用并完成同步/);

  // Click 拉取最新价格
  h.root.click("[data-pricing-sync-catalog]");
  const syncReq = h.requests.find(r => r.method === "POST" && r.route === "model/pricing/sync");
  assert.ok(syncReq);
  syncReq.resolve({...sampleV2Payload({revision: "rev-v2-synced"}), sync: {ok_count: 3, error_count: 0}});
  await flush();
  assert.match(h.root.innerHTML, /已刷新 3 个价格源/);

  // Toggle auto_daily and save (strictly only auto_daily in price_sync)
  h.root.input("[data-pricing-auto-daily]", "", {checked: false, type: "change"});
  assert.equal(h.page.isDirty(), true);
  h.root.click("[data-pricing-save]");
  const saveReq = h.requests.find(r => r.method === "POST" && r.route === "model/pricing");
  assert.ok(saveReq);
  assert.deepEqual(JSON.parse(JSON.stringify(saveReq.values.changes)), {price_sync: {auto_daily: false}});
  saveReq.resolve(sampleV2Payload({price_sync: {auto_daily: false, last_auto_sync_at: null}, revision: "rev-v2-auto"}));
  await flush();
  assert.equal(h.page.isDirty(), false);
  h.page.unmount();
});

test("unpriced alert click-to-jump selects cluster, expands card, pulses & scrolls into view (configured & deleted)", async () => {
  const h = modelsHarness();
  h.sessionStore.setItem("companion-models-pending-tab.v1", "pricing");
  h.page.mount(h.root);
  h.requests.find(r => r.route === "model/pricing").resolve(sampleV2Payload());
  await flush();

  // Switch away to unused cluster first
  h.root.click("[data-pricing-select-cluster]", {pricingSelectCluster: "unused-src"});
  assert.doesNotMatch(h.root.innerHTML, /data-pw-card="relay-custom"/);

  // Jump to configured unpriced provider
  h.root.click("[data-pricing-jump-provider]", {
    pricingJumpProvider: "relay-custom",
    pricingJumpCluster: "relay-src",
    pricingJumpDeleted: "0",
    pricingJumpModel: "my-unpriced-model",
  });
  assert.match(h.root.innerHTML, /pw-card is-open is-pulse/);
  assert.equal(h.scrolled.at(-1)?.id, "relay-custom");

  // Jump to deleted unpriced provider opens residue section and expands target
  h.root.click("[data-pricing-jump-provider]", {
    pricingJumpProvider: "old-heavy-prov",
    pricingJumpCluster: "old-heavy-prov",
    pricingJumpDeleted: "1",
    pricingJumpModel: "legacy-heavy",
  });
  assert.match(h.root.innerHTML, /data-pw-card="old-heavy-prov"/);
  assert.equal(h.scrolled.at(-1)?.id, "old-heavy-prov");
  h.page.unmount();
});

test("cluster multiplier, New API probe, and catalog candidate selection/reset match PR-FEAT-1A contracts", async () => {
  const h = modelsHarness();
  h.sessionStore.setItem("companion-models-pending-tab.v1", "pricing");
  h.page.mount(h.root);
  h.requests.find(r => r.route === "model/pricing").resolve(sampleV2Payload());
  await flush();

  // Apply cluster multiplier 0.25
  h.root.input("[data-pricing-cluster-mult-input]", "0.25", {
    dataset: {pricingClusterMultInput: "relay-src"}, type: "change",
  });
  h.root.click("[data-pricing-cluster-mult-apply]", {pricingClusterMultApply: "relay-src"});
  const multReq = h.requests.find(r => r.method === "POST" && r.route === "model/pricing");
  assert.ok(multReq);
  assert.deepEqual(JSON.parse(JSON.stringify(multReq.values.changes)), {pricing_multipliers: {"relay-src": 0.25}});
  multReq.resolve(sampleV2Payload({
    pricing_multipliers: {"relay-src": 0.25},
    clusters: sampleV2Payload().clusters.map(c => c.cluster_id === "relay-src" ? {...c, multiplier: 0.25, has_custom_multiplier: true} : c),
    revision: "rev-mult",
  }));
  await flush();
  assert.match(h.root.innerHTML, /当前 0\.25×/);

  // Probe New API source
  h.root.click("[data-pricing-detect-newapi]", {pricingDetectNewapi: "relay-claude"});
  const detReq = h.requests.find(r => r.method === "POST" && r.route === "model/pricing/sources/detect");
  assert.ok(detReq);
  assert.deepEqual(JSON.parse(JSON.stringify(detReq.values)), {provider_id: "relay-claude", enable: true});
  detReq.resolve({
    ...sampleV2Payload({revision: "rev-det"}),
    detection: {ok: true, source_id: "newapi:relay-main", deduplicated: true, models: 128, needs_key: false},
  });
  await flush();
  assert.match(h.root.innerHTML, /探测成功：已接入 newapi:relay-main（128 个模型）（已复用同域名价格源）/);

  // Expand relay-claude, open candidate comparison table, verify 本网关 badge, select & reset
  h.root.click("[data-pricing-toggle-card]", {pricingToggleCard: "relay-claude"});
  h.root.click("[data-pricing-toggle-cands]", {pricingToggleCands: "relay-claude"});
  assert.match(h.root.innerHTML, /本网关/);
  assert.match(h.root.innerHTML, /models\.dev/);
  h.root.click("[data-pricing-select-candidate]", {
    pricingSelectCandidate: "relay-claude",
    modelName: "claude-sonnet-4-5",
    priceKey: "newapi:relay-main::claude-sonnet-4-5",
  });
  const selReq = h.requests.find(r => r.method === "POST" && r.route === "model/pricing/select");
  assert.ok(selReq);
  assert.deepEqual(JSON.parse(JSON.stringify(selReq.values)), {
    provider_id: "relay-claude", model: "claude-sonnet-4-5",
    price_key: "newapi:relay-main::claude-sonnet-4-5", revision: "rev-det",
  });
  selReq.resolve(sampleV2Payload({
    revision: "rev-sel",
    selections: {
      "relay-claude": {
        "claude-sonnet-4-5": {
          price_key: "newapi:relay-main::claude-sonnet-4-5",
          source_id: "newapi:relay-main", source_label: "New API · 中转主站", model_id: "claude-sonnet-4-5",
          rule: {mode: "per_token", currency: "USD", input: 2.4, output: 12},
        },
      },
    },
  }));
  await flush();
  assert.match(h.root.innerHTML, /清除选择/);

  h.root.click("[data-pricing-select-reset]", {pricingSelectReset: "relay-claude", modelName: "claude-sonnet-4-5"});
  const resetSelReq = h.requests.find(r => r.method === "POST" && r.route === "model/pricing/select/reset");
  assert.ok(resetSelReq);
  assert.deepEqual(JSON.parse(JSON.stringify(resetSelReq.values)), {
    provider_id: "relay-claude", model: "claude-sonnet-4-5", revision: "rev-sel",
  });
  resetSelReq.resolve(sampleV2Payload({revision: "rev-sel-reset"}));
  await flush();
  h.page.unmount();
});

test("all 5 pricing modes (per_token blank vs 0, per_tier, tiered_expr validate, per_turn, per_request) & time-of-day schedule work end-to-end", async () => {
  const h = modelsHarness();
  h.sessionStore.setItem("companion-models-pending-tab.v1", "pricing");
  h.page.mount(h.root);
  h.requests.find(r => r.route === "model/pricing").resolve(sampleV2Payload());
  await flush();

  // Open relay-claude card
  h.root.click("[data-pricing-toggle-card]", {pricingToggleCard: "relay-claude"});

  // 1. per_token: explicit 0 on input_cached + blank cache_creation (inherit!)
  h.root.input("[data-pricing-rate-field]", "2.5", {dataset: {pricingRateField: "input", pid: "relay-claude"}, type: "change"});
  h.root.input("[data-pricing-rate-field]", "0", {dataset: {pricingRateField: "input_cached", pid: "relay-claude"}, type: "change"});
  h.root.input("[data-pricing-rate-field]", "10", {dataset: {pricingRateField: "output", pid: "relay-claude"}, type: "change"});
  assertNoNestedLabels(h.root.innerHTML, "per_token");

  const changesPerToken = h.wb.computeChanges({data: sampleV2Payload(), draft: null});
  assert.deepEqual(JSON.parse(JSON.stringify(changesPerToken)), {});
  h.root.click("[data-pricing-save]");
  let post = h.requests.filter(r => r.method === "POST" && r.route === "model/pricing").at(-1);
  const ptRule = post.values.changes.pricing_rules["relay-claude|claude-sonnet-4-5"];
  assert.equal(ptRule.mode, "per_token");
  assert.equal(ptRule.input, 2.5);
  assert.equal(ptRule.input_cached, 0);
  assert.equal(ptRule.configured.input_cached, true, "explicit 0 must stay configured=true (free)");
  assert.equal(ptRule.cache_creation, null);
  assert.equal(ptRule.configured.cache_creation, false, "blank field must stay configured=false (inherit)");
  post.resolve(sampleV2Payload({pricing_rules: post.values.changes.pricing_rules, revision: "rev-m1"}));
  await flush();

  // 2. per_tier: context_tiers + service_tiers
  h.root.click("[data-pricing-rule-mode]", {pricingRuleMode: "relay-claude", mode: "per_tier"});
  h.root.click("[data-pricing-add-ctx-tier]", {pricingAddCtxTier: "relay-claude"});
  h.root.input("[data-pricing-ctx-field]", "200000", {dataset: {pricingCtxField: "threshold_tokens", pid: "relay-claude", idx: "0"}, type: "change"});
  h.root.input("[data-pricing-ctx-field]", "6", {dataset: {pricingCtxField: "input", pid: "relay-claude", idx: "0"}, type: "change"});
  h.root.click("[data-pricing-add-svc-tier]", {pricingAddSvcTier: "relay-claude"});
  assertNoNestedLabels(h.root.innerHTML, "per_tier");
  h.root.click("[data-pricing-save]");
  post = h.requests.filter(r => r.method === "POST" && r.route === "model/pricing").at(-1);
  const tierRule = post.values.changes.pricing_rules["relay-claude|claude-sonnet-4-5"];
  assert.equal(tierRule.mode, "per_tier");
  assert.equal(tierRule.context_tiers[0].threshold_tokens, 200000);
  assert.equal(tierRule.context_tiers[0].input, 6);
  assert.equal(tierRule.service_tiers[0].match, "priority");
  post.resolve(sampleV2Payload({pricing_rules: post.values.changes.pricing_rules, revision: "rev-m2"}));
  await flush();

  // 3. tiered_expr: currency selectable (CNY/USD) + validate endpoint sends currency (NO mode!)
  h.root.click("[data-pricing-rule-mode]", {pricingRuleMode: "relay-claude", mode: "tiered_expr"});
  assert.doesNotMatch(h.root.innerHTML, /data-pricing-rule-currency="relay-claude"\s+disabled/);
  assertNoNestedLabels(h.root.innerHTML, "tiered_expr");
  h.root.input("[data-pricing-rule-currency]", "CNY", {dataset: {pricingRuleCurrency: "relay-claude"}, type: "change"});
  h.root.input("[data-pricing-expr-field]", "v1: tier('标准', p * 0.75 + c * 3.75)", {dataset: {pricingExprField: "expr", pid: "relay-claude"}, type: "change"});
  h.root.click("[data-pricing-expr-validate]", {pricingExprValidate: "relay-claude"});
  const valReq = h.requests.find(r => r.method === "POST" && r.route === "model/pricing/expr/validate");
  assert.ok(valReq);
  assert.equal(valReq.values.expr, "v1: tier('标准', p * 0.75 + c * 3.75)");
  assert.equal(valReq.values.currency, "CNY");
  assert.equal("mode" in valReq.values, false, "must NOT send mode to /pricing/expr/validate");
  valReq.resolve({
    valid: true, error: null, currency: "CNY", used_variables: ["c", "p"], usd_cny_rate: 7.22,
    samples: [{label: "标准短上下文", cost_usd: 0.04, cost_cny: 0.2888, matched_tier: "标准"}],
  });
  await flush();
  assert.match(h.root.innerHTML, /表达式校验通过/);
  assert.match(h.root.innerHTML, /¥0\.288800/);

  // 4. per_turn & 5. per_request
  h.root.click("[data-pricing-rule-mode]", {pricingRuleMode: "relay-claude", mode: "per_turn"});
  h.root.input("[data-pricing-rule-price]", "0.02", {dataset: {pricingRulePrice: "relay-claude"}, type: "change"});
  assertNoNestedLabels(h.root.innerHTML, "per_turn");
  h.root.click("[data-pricing-rule-mode]", {pricingRuleMode: "relay-claude", mode: "per_request"});
  h.root.input("[data-pricing-rule-price]", "0.05", {dataset: {pricingRulePrice: "relay-claude"}, type: "change"});
  assertNoNestedLabels(h.root.innerHTML, "per_request");

  // Time-of-day schedule: enable, cross-midnight hint, weekday toggle, and nested override mode
  h.root.input("[data-pricing-sched-enabled]", "", {dataset: {pricingSchedEnabled: "relay-claude"}, checked: true, type: "change"});
  assert.match(h.root.innerHTML, /跨午夜/);
  h.root.input("[data-pricing-sched-tz-select]", "America/New_York", {dataset: {pricingSchedTzSelect: "relay-claude"}, type: "change"});
  h.root.click("[data-pricing-sched-day]", {pricingSchedDay: "relay-claude", idx: "0", day: "7"});
  h.root.click("[data-pricing-sched-add]", {pricingSchedAdd: "relay-claude"});
  h.root.click("[data-pricing-sched-mode]", {pricingSchedMode: "relay-claude", idx: "1", mode: "override"});
  assertNoNestedLabels(h.root.innerHTML, "schedule_override");

  h.root.click("[data-pricing-save]");
  post = h.requests.filter(r => r.method === "POST" && r.route === "model/pricing").at(-1);
  assert.equal(post.values.revision, "rev-m2");
  assert.equal(post.values.changes.pricing_rules["relay-claude|claude-sonnet-4-5"].mode, "per_request");
  assert.equal(post.values.changes.pricing_rules["relay-claude|claude-sonnet-4-5"].price, 0.05);
  const sched = post.values.changes.pricing_schedules["relay-claude|claude-sonnet-4-5"];
  assert.equal(sched.timezone, "America/New_York");
  assert.deepEqual([...sched.periods[0].days], [1, 2, 3, 4, 5, 6]);
  assert.equal(sched.periods[0].start, "23:00");
  assert.equal(sched.periods[0].end, "07:00");
  assert.equal(sched.periods[1].mode, "override");
  assert.equal("multiplier" in sched.periods[1], false);
  post.resolve(sampleV2Payload({
    pricing_rules: post.values.changes.pricing_rules,
    pricing_schedules: post.values.changes.pricing_schedules,
    revision: "rev-m3",
  }));
  await flush();
  assert.equal(h.page.isDirty(), false);
  h.page.unmount();
});

test("2-step armed reset all & deleted provider residue clear work safely", async () => {
  const h = modelsHarness();
  h.sessionStore.setItem("companion-models-pending-tab.v1", "pricing");
  h.page.mount(h.root);
  h.requests.find(r => r.route === "model/pricing").resolve(sampleV2Payload());
  await flush();

  // Step 1: arm only (no POST yet)
  h.root.click("[data-pricing-reset-all]");
  assert.equal(h.requests.some(r => r.route === "model/pricing/reset"), false);
  assert.match(h.root.innerHTML, /确认重置全部定价/);

  // Cancel disarms
  h.root.click("[data-pricing-reset-cancel]");
  assert.doesNotMatch(h.root.innerHTML, /确认重置全部定价/);

  // Arm + Confirm POSTs scope=all with revision
  h.root.click("[data-pricing-reset-all]");
  h.root.click("[data-pricing-reset-confirm]");
  const resetReq = h.requests.find(r => r.method === "POST" && r.route === "model/pricing/reset");
  assert.ok(resetReq);
  assert.deepEqual(JSON.parse(JSON.stringify(resetReq.values)), {scope: "all", revision: "rev-v2-init"});
  resetReq.resolve(sampleV2Payload({revision: "rev-after-reset"}));
  await flush();

  // Clear deleted residue
  h.root.click("[data-pricing-residue-clear-all]");
  const clearReq = h.requests.find(r => r.method === "POST" && r.route === "model/pricing/residue/clear");
  assert.ok(clearReq);
  assert.deepEqual(JSON.parse(JSON.stringify(clearReq.values)), {revision: "rev-after-reset"});
  clearReq.resolve({...sampleV2Payload({deleted_providers: [], revision: "rev-after-clear"}), cleared_provider_ids: ["old-heavy-prov", "old-small-prov"]});
  await flush();
  assert.match(h.root.innerHTML, /已清理 2 个已删除供应商的残留计价配置/);
  h.page.unmount();
});

test("XSS payloads in provider/model/source/error/schedule fields are 100% escaped", async () => {
  const h = modelsHarness();
  const payload = sampleV2Payload();
  const xss = '<img src=x onerror="alert(1)">';
  const xssAttr = '"><script>evil()</script>';
  payload.sources[1].label = `Evil ${xss}`;
  payload.sources[1].error = `Err ${xssAttr}`;
  payload.clusters[1].name = `Supplier ${xss}`;
  payload.clusters[1].providers[1].id = `prov-${xss}`;
  payload.clusters[1].providers[1].model = `model-${xssAttr}`;
  payload.clusters[1].providers[1].candidates = [`model-${xssAttr}`];
  payload.unpriced[1].provider_id = `prov-${xss}`;
  payload.unpriced[1].models[0].model = `model-${xssAttr}`;

  h.sessionStore.setItem("companion-models-pending-tab.v1", "pricing");
  h.page.mount(h.root);
  h.requests.find(r => r.route === "model/pricing").resolve(payload);
  await flush();

  assert.doesNotMatch(h.root.innerHTML, /<img src=x/);
  assert.doesNotMatch(h.root.innerHTML, /<script>evil\(\)<\/script>/);
  assert.match(h.root.innerHTML, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
  assertNoNestedLabels(h.root.innerHTML, "xss");
  h.page.unmount();
});

test("contact page cost formatting & CSS/HTML registration contracts remain clean", () => {
  assert.doesNotMatch(contactSource, /`成本 \$\$\{/);
  assert.doesNotMatch(contactSource, /`\$\$\{\(Number\(usage\.cost_microusd\)/);
  assert.match(contactSource, /formatContactCost/);
  assert.match(contactSource, /未设单价/);

  const pricingCss = fs.readFileSync(path.join(PAGE_DIR, "pricing-workbench.css"), "utf8");
  const maintCss = fs.readFileSync(path.join(PAGE_DIR, "maintenance-page.css"), "utf8");
  const cssSources = JSON.parse(fs.readFileSync(path.join(PAGE_DIR, "css-sources.json"), "utf8"));
  const indexHtml = fs.readFileSync(path.join(PAGE_DIR, "index.html"), "utf8");

  // Zero hardcoded colors in pricing-workbench.css
  const stripped = pricingCss.replace(/\/\*[\s\S]*?\*\//g, "");
  assert.doesNotMatch(stripped, /#[0-9a-fA-F]{3,8}\b/, "pricing-workbench.css must have 0 hardcoded hex colors");
  assert.doesNotMatch(stripped, /\b(?:rgba?|hsla?)\(/, "pricing-workbench.css must have 0 hardcoded rgb/hsl colors");
  assert.match(stripped, /@container\s+pricing-wb/);
  assert.match(stripped, /@media\s*\(max-width:\s*780px\)/);

  // Registration order
  assert.equal(cssSources.indexOf("pricing-workbench.css"), cssSources.indexOf("models-page.css") + 1);
  assert.ok(indexHtml.indexOf("pricing-workbench.js") > 0);
  assert.ok(indexHtml.indexOf("pricing-workbench.js") < indexHtml.indexOf("models-page.js"));

  // Maintenance 1-line pricing controls bar
  assert.match(maintCss, /\.maint-token-details\s*>\s*\.maint-subhead\s*>\s*\.maint-pricing-controls,\s*\.maint-pricing-controls\s*\{[^}]*display:flex[^}]*flex-wrap:wrap/s);
});

test("manual FX rate, optimistic revision conflict preserves draft, and saveBeforeLeave saves dirty pricing", async () => {
  const h = modelsHarness();
  h.sessionStore.setItem("companion-models-pending-tab.v1", "pricing");
  h.page.mount(h.root);
  h.requests.find(r => r.route === "model/pricing").resolve(sampleV2Payload());
  await flush();

  // Switch FX mode to manual and enter 7.35
  h.root.click("[data-pricing-rate-mode]", {pricingRateMode: "manual"});
  h.root.input("[data-pricing-manual-rate]", "7.35", {type: "change"});
  assert.equal(h.page.isDirty(), true);
  assert.ok(h.page.changedFields().some(f => f.includes("汇率")));

  // Simulate revision conflict -> draft MUST stay preserved & stale alert appears
  h.root.click("[data-pricing-save]");
  const firstPost = h.requests.filter(r => r.method === "POST" && r.route === "model/pricing").at(-1);
  assert.deepEqual(JSON.parse(JSON.stringify(firstPost.values.changes)), {
    exchange_rate: {mode: "manual", manual_usd_cny: 7.35},
  });
  firstPost.reject(Object.assign(new Error("pricing_config_conflict"), {code: "pricing_config_conflict"}));
  await flush();
  assert.match(h.root.innerHTML, /配置已在其他位置更新/);
  assert.equal(h.page.isDirty(), true, "conflict must preserve unsaved draft");

  // Refresh updates revision and keeps user able to edit & save via saveBeforeLeave
  h.root.click("[data-pricing-refresh]");
  h.requests.filter(r => r.method === "GET" && r.route === "model/pricing").at(-1).resolve(
    sampleV2Payload({revision: "rev-fresh"}),
  );
  await flush();
  h.root.click("[data-pricing-currency]", {pricingCurrency: "USD"});
  assert.equal(h.page.isDirty(), true);

  const leavePromise = h.page.saveBeforeLeave();
  const secondPost = h.requests.filter(r => r.method === "POST" && r.route === "model/pricing").at(-1);
  assert.equal(secondPost.values.revision, "rev-fresh");
  assert.equal(secondPost.values.changes.display_currency, "USD");
  secondPost.resolve(sampleV2Payload({display_currency: "USD", revision: "rev-final"}));
  const leaveResult = await leavePromise;
  assert.equal(leaveResult, true);
  assert.equal(h.page.isDirty(), false);
  h.page.unmount();
});

test("search filter, multi-model pill switching, free toggle, fill preset, clear override & invalid expr error", async () => {
  const h = modelsHarness();
  h.sessionStore.setItem("companion-models-pending-tab.v1", "pricing");
  h.page.mount(h.root);
  h.requests.find(r => r.route === "model/pricing").resolve(sampleV2Payload());
  await flush();

  // Search filters down to idle-prov
  h.root.input("[data-pricing-search]", "gpt-4o-mini", {type: "input"});
  assert.match(h.root.innerHTML, /data-pw-card="idle-prov"/);
  assert.doesNotMatch(h.root.innerHTML, /data-pw-card="relay-claude"/);
  h.root.input("[data-pricing-search]", "", {type: "input"});

  // Open relay-claude -> switch candidate model pill to claude-opus-4-6
  h.root.click("[data-pricing-toggle-card]", {pricingToggleCard: "relay-claude"});
  h.root.click("[data-pricing-select-model]", {pricingSelectModel: "relay-claude", modelName: "claude-opus-4-6"});
  assert.match(h.root.innerHTML, /relay-claude\|claude-opus-4-6/);

  // Toggle 免费模型 -> sets free=true and 0 rates
  h.root.input("[data-pricing-rule-free]", "", {dataset: {pricingRuleFree: "relay-claude"}, checked: true, type: "change"});
  assert.match(h.root.innerHTML, /免费/);

  // Switch back to claude-sonnet-4-5, fill preset, then clear override
  h.root.click("[data-pricing-select-model]", {pricingSelectModel: "relay-claude", modelName: "claude-sonnet-4-5"});
  h.root.click("[data-pricing-fill-preset]", {pricingFillPreset: "relay-claude"});
  assert.match(h.root.innerHTML, /value="3\.75"/);
  h.root.click("[data-pricing-clear-rule]", {pricingClearRule: "relay-claude"});
  assert.match(h.root.innerHTML, /已清除该项自定义覆盖/);

  // Invalid expression validation renders Chinese syntax error
  h.root.click("[data-pricing-rule-mode]", {pricingRuleMode: "relay-claude", mode: "tiered_expr"});
  h.root.input("[data-pricing-expr-field]", "v1: p ** 2", {dataset: {pricingExprField: "expr", pid: "relay-claude"}, type: "change"});
  h.root.click("[data-pricing-expr-validate]", {pricingExprValidate: "relay-claude"});
  const valReq = h.requests.filter(r => r.route === "model/pricing/expr/validate").at(-1);
  valReq.resolve({valid: false, error: "表达式不支持幂运算符 '**'", used_variables: [], samples: []});
  await flush();
  assert.match(h.root.innerHTML, /校验未通过/);
  assert.match(h.root.innerHTML, /表达式不支持幂运算符/);

  // Built-in reference table toggle
  h.root.click("[data-pricing-toggle-presets]");
  assert.match(h.root.innerHTML, /内置主流模型公开参考价/);
  assertNoNestedLabels(h.root.innerHTML, "presets_open");
  h.page.unmount();
});

test("client validation blocks out-of-range FX, duplicate context thresholds, and empty schedule weekdays", () => {
  const h = modelsHarness();
  assert.match(h.wb.validateDraftBeforeSave({exchange_rate: {mode: "manual", manual_usd_cny: 0.5}}), /手动汇率必须是 1\.00 到 100\.00/);
  assert.match(
    h.wb.validateDraftBeforeSave({
      pricing_rules: {
        "p|m": {
          mode: "per_tier", currency: "USD", multiplier: 1,
          base: {input: 1, input_cached: null, cache_creation: null, output: 2},
          context_tiers: [
            {threshold_tokens: 128000, input: 2, input_cached: null, cache_creation: null, output: 4},
            {threshold_tokens: 128000, input: 3, input_cached: null, cache_creation: null, output: 6},
          ],
          service_tiers: [],
        },
      },
    }),
    /阈值 128000 重复/,
  );
  assert.match(
    h.wb.validateDraftBeforeSave({
      pricing_schedules: {
        "p|m": {
          enabled: true, timezone: "Asia/Shanghai",
          periods: [{id: "p1", name: "", days: [], all_day: true, start: "00:00", end: "24:00", mode: "multiplier", multiplier: 0.5}],
        },
      },
    }),
    /至少选择一个星期/,
  );
});

test("regressions: failed cluster multiplier keeps draft clean, non-per_token base rule stays consistent across edits/pills, and pulse timer respects unmount", async () => {
  const h = modelsHarness();
  const payload = sampleV2Payload();
  // Provider with non-per_token effective_rule & NO auto_selected/selections entry, plus a 2nd candidate model
  payload.clusters[1].providers.push({
    id: "relay-tier-multi",
    model: "gemini-2.5-pro",
    candidates: ["gemini-2.5-pro", "gpt-4o"],
    configured: true, enabled: true, loaded: true, bound: true,
    cluster_id: "relay-src", supplier_name: "中转主站", has_api_base: false,
    total_tokens: 1000000, calls: 10, requests: 5,
    effective_source: "legacy_config", effective_mode: "per_tier",
    effective_rule: {
      mode: "per_tier", currency: "USD", multiplier: 1,
      base: {input: 2, input_cached: 0.2, cache_creation: null, output: 12},
      context_tiers: [{threshold_tokens: 200000, input: 4, input_cached: 0.4, cache_creation: null, output: 18}],
      service_tiers: [],
    },
    cluster_multiplier: 1, schedule_active: false, schedule_period_count: 0,
  });

  h.sessionStore.setItem("companion-models-pending-tab.v1", "pricing");
  h.page.mount(h.root);
  h.requests.find(r => r.route === "model/pricing").resolve(payload);
  await flush();

  // Regression 1: rejected cluster multiplier POST must NOT corrupt draft/isDirty/badges, while preserving input for retry
  h.root.input("[data-pricing-cluster-mult-input]", "0.5", {
    dataset: {pricingClusterMultInput: "relay-src"}, type: "change",
  });
  h.root.click("[data-pricing-cluster-mult-apply]", {pricingClusterMultApply: "relay-src"});
  const failedPost = h.requests.filter(r => r.method === "POST" && r.route === "model/pricing").at(-1);
  assert.ok(failedPost);
  failedPost.reject(Object.assign(new Error("network_failed"), {code: "network_failed"}));
  await flush();
  assert.equal(h.page.isDirty(), false, "rejected cluster multiplier must not leave draft dirty");
  assert.match(h.root.innerHTML, /当前 1×/, "header must keep persisted 1× after rejected save");
  assert.doesNotMatch(h.root.innerHTML, /当前 0\.5×/, "header must not falsely show rejected 0.5×");
  assert.match(h.root.innerHTML, /data-pricing-cluster-mult-input="relay-src" value="0\.5"/, "input keeps typed value for retry");

  // Regression 2A: editing per_tier base input without auto_selected must stay per_tier & preserve context_tiers
  h.root.click("[data-pricing-toggle-card]", {pricingToggleCard: "relay-tier-multi"});
  assert.match(h.root.innerHTML, /data-pricing-tier-base="input"/);
  h.root.input("[data-pricing-tier-base]", "3.5", {
    dataset: {pricingTierBase: "input", pid: "relay-tier-multi"}, type: "change",
  });
  assert.match(h.root.innerHTML, /data-pricing-rule-mode="relay-tier-multi" data-mode="per_tier"/);
  assert.match(h.root.innerHTML, /data-pricing-tier-base="input"[^>]*value="3\.5"/, "must keep per_tier edit instead of snapping to per_token");
  assert.match(h.root.innerHTML, /value="200000"/, "must preserve existing context_tiers");

  // Regression 2B: switching candidate model pill to gpt-4o must not bleed gemini-2.5-pro's per_tier editor
  h.root.click("[data-pricing-select-model]", {pricingSelectModel: "relay-tier-multi", modelName: "gpt-4o"});
  assert.doesNotMatch(h.root.innerHTML, /data-pricing-tier-base="input"/, "secondary model must not inherit primary model's per_tier editor");
  assert.match(h.root.innerHTML, /data-pricing-rate-field="input"/, "secondary model renders its own per_token editor");

  // Regression 3A: mounted pulseTimer clears is-pulse IN PLACE without full render() so input focus is never stolen
  h.root.click("[data-pricing-jump-provider]", {
    pricingJumpProvider: "relay-custom",
    pricingJumpCluster: "relay-src",
    pricingJumpDeleted: "0",
    pricingJumpModel: "my-unpriced-model",
  });
  const mountedPulseTimer = h.timers.filter(t => t.ms === 1600 && !t.cleared).at(-1);
  assert.ok(mountedPulseTimer, "jump should schedule 1600ms pulse timeout");
  const htmlBeforePulseEnd = h.root.innerHTML;
  mountedPulseTimer.fn();
  assert.equal(h.root.innerHTML, htmlBeforePulseEnd, "pulse end must NOT rebuild root.innerHTML / steal input focus");
  assert.ok(h.removedPulse.includes("relay-custom"), "pulse end must remove is-pulse class in place");

  // Regression 3B: pulseTimer callback after unmount must not touch DOM or invoke stale render
  h.root.click("[data-pricing-jump-provider]", {
    pricingJumpProvider: "relay-custom",
    pricingJumpCluster: "relay-src",
    pricingJumpDeleted: "0",
    pricingJumpModel: "my-unpriced-model",
  });
  const pulseTimer = h.timers.filter(t => t.ms === 1600 && !t.cleared).at(-1);
  assert.ok(pulseTimer, "second jump should schedule 1600ms pulse timeout");
  const removedCountBeforeUnmount = h.removedPulse.length;
  h.page.unmount();
  h.root.innerHTML = "<sentinel-unmounted />";
  pulseTimer.fn();
  assert.equal(h.root.innerHTML, "<sentinel-unmounted />", "pulse timer must not render after unmount");
  assert.equal(h.removedPulse.length, removedCountBeforeUnmount, "pulse timer must not touch DOM after unmount");
});

test("New API gateway bar: enriched probe notice, recommended group ratio chip, custom-rules hint, group dropdown, and source currency override", async () => {
  const h = modelsHarness();
  const base = sampleV2Payload();
  const payload = sampleV2Payload({
    sources: [
      ...base.sources.filter(s => s.source_id !== "newapi:relay-main"),
      {
        source_id: "newapi:relay-main",
        kind: "newapi",
        label: "示例公益站",
        enabled: true,
        status: "ok",
        model_count: 120,
        last_ok_at: "2026-11-24T12:00:00Z",
        provider_id: "relay-claude",
        currency: "USD",
        currency_override: null,
        auth_mode: "anonymous",
        site_meta: {
          system_name: "示例公益站",
          quota_display_type: "CUSTOM",
          custom_currency_symbol: "🍪",
          recharge_price: 1.0,
          group_ratio: {default: 1, gemini: 0.245, geminiultra: 0.5},
        },
      },
    ],
    clusters: base.clusters.map(c => c.cluster_id === "relay-src" ? {
      ...c,
      has_custom_rules: true,
      newapi_source_id: "newapi:relay-main",
      newapi_group_hint: {group: "gemini", ratio: 0.245, confidence: "high"},
    } : c),
  });

  h.sessionStore.setItem("companion-models-pending-tab.v1", "pricing");
  h.page.mount(h.root);
  h.requests.find(r => r.route === "model/pricing").resolve(payload);
  await flush();

  // New API bar shows recommended group badge, one-click apply button, custom-rules caution, CUSTOM token hint, and currency selector
  assert.match(h.root.innerHTML, /识别分组 · gemini \(0\.245×\)/);
  assert.match(h.root.innerHTML, /一键应用 0\.245×/);
  assert.match(h.root.innerHTML, /聚类倍率会同时作用于该供应商下的已自定义模型规则/);
  assert.match(h.root.innerHTML, /该站使用自定义代币 🍪/);
  assert.match(h.root.innerHTML, /1 单位 = ¥1/);
  assert.match(h.root.innerHTML, /data-pricing-source-currency="newapi:relay-main"/);
  assertNoNestedLabels(h.root.innerHTML, "newapi_bar");

  // 1) Selecting a site group from dropdown fills multiplier draft input (does NOT auto-POST)
  const postCountBefore = h.requests.filter(r => r.method === "POST").length;
  h.root.input("[data-pricing-group-select]", "0.5", {
    dataset: {pricingGroupSelect: "relay-src"}, type: "change",
  });
  assert.equal(h.requests.filter(r => r.method === "POST").length, postCountBefore);
  assert.match(h.root.innerHTML, /data-pricing-cluster-mult-input="relay-src" value="0\.5"/);

  // 2) Clicking one-click apply recommended group ratio posts cluster multiplier 0.245
  h.root.click("[data-pricing-apply-group-mult]", {pricingApplyGroupMult: "relay-src", ratio: "0.245"});
  const applyReq = h.requests.filter(r => r.method === "POST" && r.route === "model/pricing").at(-1);
  assert.ok(applyReq);
  assert.deepEqual(JSON.parse(JSON.stringify(applyReq.values.changes)), {pricing_multipliers: {"relay-src": 0.245}});
  applyReq.resolve({
    ...payload,
    revision: "rev-grp",
    pricing_multipliers: {"relay-src": 0.245},
    clusters: payload.clusters.map(c => c.cluster_id === "relay-src" ? {...c, multiplier: 0.245, has_custom_multiplier: true} : c),
  });
  await flush();
  assert.match(h.root.innerHTML, /当前倍率已匹配/);

  // 3) Switching New API source currency sends POST model/pricing/sources/currency
  h.root.input("[data-pricing-source-currency]", "CNY", {
    dataset: {pricingSourceCurrency: "newapi:relay-main"}, type: "change",
  });
  const curReq = h.requests.find(r => r.method === "POST" && r.route === "model/pricing/sources/currency");
  assert.ok(curReq);
  assert.deepEqual(JSON.parse(JSON.stringify(curReq.values)), {
    source_id: "newapi:relay-main",
    currency_override: "CNY",
    fiat_per_base_unit: null,
    sync: true,
    revision: "rev-grp",
  });
  curReq.resolve({
    ...payload,
    revision: "rev-cur",
    sources: payload.sources.map(s => s.source_id === "newapi:relay-main" ? {...s, currency: "CNY", currency_override: "CNY"} : s),
  });
  await flush();
  assert.match(h.root.innerHTML, /结算币种已切换为 人民币 CNY \(¥\)/);
  assert.match(h.root.innerHTML, /<span class="pw-source-cur">¥<\/span>/);
  assert.match(h.root.innerHTML, /CNY ¥/);

  // 3B) Optimistic revision conflict on currency switch marks state stale consistently with applyClusterMultiplier
  h.root.input("[data-pricing-source-currency]", "USD", {
    dataset: {pricingSourceCurrency: "newapi:relay-main"}, type: "change",
  });
  const conflictCurReq = h.requests.filter(r => r.method === "POST" && r.route === "model/pricing/sources/currency").at(-1);
  assert.ok(conflictCurReq);
  conflictCurReq.reject(Object.assign(new Error("pricing_config_conflict"), {code: "pricing_config_conflict"}));
  await flush();
  assert.match(h.root.innerHTML, /data-pricing-save[^>]*disabled/);
  // Clear stale flag via a successful fresh probe below (syncDataPreserveDraft clears p.stale)

  // 4) Enriched New API probe notice surfaces site_name, CNY currency, recommended group, and skipped expr count
  h.root.click("[data-pricing-detect-newapi]", {pricingDetectNewapi: "relay-claude"});
  const detReq = h.requests.find(r => r.method === "POST" && r.route === "model/pricing/sources/detect");
  assert.ok(detReq);
  detReq.resolve({
    ...payload,
    revision: "rev-det-rich",
    detection: {
      ok: true,
      source_id: "newapi:relay-main",
      deduplicated: true,
      models: 120,
      needs_key: false,
      site_name: "示例公益站",
      currency: "CNY",
      detected_group: "gemini",
      detected_group_ratio: 0.245,
      skipped_expr_count: 2,
    },
  });
  await flush();
  assert.match(h.root.innerHTML, /探测成功：已接入 示例公益站（120 个模型 · 人民币 CNY）（已复用同域名价格源） · 推荐分组 gemini \(0\.245×\) · 跳过 2 个特殊私有公式。/);
  h.page.unmount();
});

test("pricing-workbench.css mobile responsive contracts: smooth horizontal cand-table, <=520px full-width selects/inline fields, compact padding, and 32x32 day-pill touch target", () => {
  const css = fs.readFileSync(path.join(PAGE_DIR, "pricing-workbench.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  assert.match(css, /\.pw-table-wrap\s*\{[^}]*overflow-x:\s*auto[^}]*overscroll-behavior-x:\s*contain/s);
  assert.match(css, /\.pw-cand-table\s*\{[^}]*min-width:\s*520px/s);
  assert.match(css, /\.pw-cand-table\s+\.pw-num\s*\{[^}]*white-space:\s*nowrap/s);
  assert.match(
    css,
    /@container\s+pricing-wb\s*\(\s*max-width:\s*520px\s*\)\s*\{[\s\S]*?\.pw-hero[\s\S]*?padding:\s*var\(--sp-3\)[\s\S]*?\.pw-inline-field\s+select,\s*\.pw-newapi-bar\s+select\s*\{[^}]*width:\s*100%[^}]*max-width:\s*none[\s\S]*?\.pw-day-pill\s*\{[^}]*min-width:\s*32px[^}]*height:\s*32px/s,
  );
  assert.match(
    css,
    /@media\s*\(\s*max-width:\s*520px\s*\)\s*\{[\s\S]*?\.pw-hero[\s\S]*?padding:\s*var\(--sp-3\)[\s\S]*?\.pw-inline-field\s+select,\s*\.pw-newapi-bar\s+select\s*\{[^}]*width:\s*100%[^}]*max-width:\s*none[\s\S]*?\.pw-day-pill\s*\{[^}]*min-width:\s*32px[^}]*height:\s*32px/s,
  );
  assert.match(css, /@media\s*\(\s*pointer:\s*coarse\s*\)\s*\{[^}]*\.pw-day-pill\s*\{[^}]*min-width:\s*32px[^}]*height:\s*32px/s);
});

test("CUSTOM New API source renders CNY dual-unit badge, toggles ¥ ↔ 🍪 in place with zero innerHTML rebuild, edits conversion rate, and handles Level 3 USD RMB hint", async () => {
  const h = modelsHarness();
  const base = sampleV2Payload();
  const cookieSrc = {
    source_id: "newapi:cookie-relay",
    kind: "newapi",
    label: "Cookie Relay",
    url: "https://cookie.example.test/api/pricing",
    enabled: true,
    status: "ok",
    model_count: 138,
    currency: "CNY",
    currency_override: "auto",
    fiat_per_base_unit: null,
    effective_unit_scale: 1.0,
    auth_mode: "anonymous",
    site_meta: {
      schema_version: 2,
      system_name: "Cookie Relay",
      quota_display_type: "CUSTOM",
      custom_currency_symbol: "🍪",
      custom_currency_exchange_rate: 10,
      recharge_price: 1.0,
      site_recharge_price: 1.0,
      currency_source: "topup_custom_cny",
      quota_station_id: "st-cookie",
      quota_station_name: "Cookie 站点",
      group_ratio: {default: 1},
    },
  };
  const cookieRule = {mode: "per_token", currency: "CNY", input: 0.75, input_cached: 0.0375, output: 3.75, multiplier: 1};
  const payload = sampleV2Payload({
    sources: [...base.sources, cookieSrc],
    clusters: base.clusters.map(c => c.cluster_id === "relay-src" ? {
      ...c,
      newapi_source_id: "newapi:cookie-relay",
      providers: c.providers.map(pr => pr.id === "relay-claude" ? {
        ...pr,
        effective_rule: cookieRule,
        effective_source: "catalog_auto",
      } : pr),
    } : c),
    auto_selected: {
      ...base.auto_selected,
      "relay-claude": {
        "claude-sonnet-4-5": {
          price_key: "newapi:cookie-relay:claude-sonnet-4-5",
          source_id: "newapi:cookie-relay",
          source_label: "Cookie Relay",
          model_id: "claude-sonnet-4-5",
          score: 1.0,
          is_own_gateway: true,
          rule: cookieRule,
        },
      },
    },
  });

  h.sessionStore.setItem("companion-models-pending-tab.v1", "pricing");
  h.page.mount(h.root);
  h.requests.find(r => r.route === "model/pricing").resolve(payload);
  await flush();

  // Header badge shows CNY ¥ (10 🍪 = ¥1), Quota Watch linked pill, dual-unit toggle, and editable rate input
  assert.match(h.root.innerHTML, /已接 Cookie Relay · CNY ¥（10 🍪 = ¥1）/);
  assert.match(h.root.innerHTML, /已联动额度看板 · Cookie 站点/);
  assert.match(h.root.innerHTML, /data-pricing-unit-view="newapi:cookie-relay" data-unit-mode="fiat"/);
  assert.match(h.root.innerHTML, /data-pricing-unit-view="newapi:cookie-relay" data-unit-mode="native"/);
  assert.match(h.root.innerHTML, /入 ¥0\.75 \/ 读 ¥0\.0375 \/ 出 ¥3\.75 \/M/);
  assert.match(h.root.innerHTML, /（入 7\.5 🍪 \/ 读 0\.375 🍪 \/ 出 37\.5 🍪 \/M）/);
  assert.match(h.root.innerHTML, /data-pricing-custom-rate-input="newapi:cookie-relay" value="10"/);

  // Build live in-place DOM stubs on h.root.querySelectorAll to assert ZERO innerHTML rebuilds on unit toggle!
  let renderWrites = 0;
  const origDesc = Object.getOwnPropertyDescriptor(h.root, "innerHTML");
  let backingHtml = h.root.innerHTML;
  Object.defineProperty(h.root, "innerHTML", {
    configurable: true,
    get() { return backingHtml; },
    set(v) { renderWrites += 1; backingHtml = String(v); },
  });
  const fiatBtnStub = {
    dataset: {pricingUnitView: "newapi:cookie-relay", unitMode: "fiat"},
    _classes: new Set(["on"]),
    _attrs: {"aria-pressed": "true"},
    classList: {toggle(c, on) { if (on) fiatBtnStub._classes.add(c); else fiatBtnStub._classes.delete(c); }},
    setAttribute(k, v) { fiatBtnStub._attrs[k] = v; },
  };
  const nativeBtnStub = {
    dataset: {pricingUnitView: "newapi:cookie-relay", unitMode: "native"},
    _classes: new Set(),
    _attrs: {"aria-pressed": "false"},
    classList: {toggle(c, on) { if (on) nativeBtnStub._classes.add(c); else nativeBtnStub._classes.delete(c); }},
    setAttribute(k, v) { nativeBtnStub._attrs[k] = v; },
  };
  const mainSpan = {textContent: "入 ¥0.75 / 读 ¥0.0375 / 出 ¥3.75 /M"};
  const subSpan = {textContent: "（入 7.5 🍪 / 读 0.375 🍪 / 出 37.5 🍪 /M）"};
  const dualElStub = {
    dataset: {
      pwDualSrc: "newapi:cookie-relay",
      pwFiat: "入 ¥0.75 / 读 ¥0.0375 / 出 ¥3.75 /M",
      pwNative: "入 7.5 🍪 / 读 0.375 🍪 / 出 37.5 🍪 /M",
    },
    querySelector(sel) {
      if (sel === ".pw-dual-main") return mainSpan;
      if (sel === ".pw-dual-sub") return subSpan;
      return null;
    },
  };
  const origQsa = h.root.querySelectorAll;
  h.root.querySelectorAll = sel => {
    if (sel === "[data-pricing-unit-view]") return [fiatBtnStub, nativeBtnStub];
    if (sel === "[data-pw-dual-src]") return [dualElStub];
    return origQsa ? origQsa.call(h.root, sel) : [];
  };

  // Toggle to 🍪 native view: 0 innerHTML rebuilds, instant in-place text & pressed state swap!
  h.root.click("[data-pricing-unit-view]", {pricingUnitView: "newapi:cookie-relay", unitMode: "native"});
  assert.equal(renderWrites, 0, "Dual-unit toggle must update in place without rebuilding innerHTML");
  assert.equal(nativeBtnStub._classes.has("on"), true);
  assert.equal(nativeBtnStub._attrs["aria-pressed"], "true");
  assert.equal(fiatBtnStub._classes.has("on"), false);
  assert.equal(mainSpan.textContent, "入 7.5 🍪 / 读 0.375 🍪 / 出 37.5 🍪 /M");
  assert.equal(subSpan.textContent, "（约 入 ¥0.75 / 读 ¥0.0375 / 出 ¥3.75 /M）");

  // Restore original innerHTML & querySelectorAll for subsequent rate-edit assertions
  if (origDesc) Object.defineProperty(h.root, "innerHTML", origDesc);
  h.root.querySelectorAll = origQsa;

  // Edit conversion rate to 12.5 🍪 = ¥1 (80% cost -> fiat_per_base_unit = 10 / 12.5 = 0.8)
  h.root.input("[data-pricing-custom-rate-input]", "12.5", {
    dataset: {pricingCustomRateInput: "newapi:cookie-relay"}, type: "input",
  });
  h.root.click("[data-pricing-custom-rate-apply]", {pricingCustomRateApply: "newapi:cookie-relay"});
  const rateReq = h.requests.filter(r => r.method === "POST" && r.route === "model/pricing/sources/currency").at(-1);
  assert.ok(rateReq);
  assert.deepEqual(JSON.parse(JSON.stringify(rateReq.values)), {
    source_id: "newapi:cookie-relay",
    currency_override: "auto",
    fiat_per_base_unit: 0.8,
    sync: true,
    revision: "rev-v2-init",
  });
  rateReq.resolve({
    ...payload,
    revision: "rev-rate-08",
    sources: payload.sources.map(s => s.source_id === "newapi:cookie-relay" ? {
      ...s, fiat_per_base_unit: 0.8, effective_unit_scale: 0.8,
    } : s),
  });
  await flush();
  assert.match(h.root.innerHTML, /12\.5 🍪 = ¥1/);
  assert.match(h.root.innerHTML, /data-pricing-custom-rate-reset="newapi:cookie-relay"/);

  // Reset manual conversion rate posts fiat_per_base_unit: null
  h.root.click("[data-pricing-custom-rate-reset]", {pricingCustomRateReset: "newapi:cookie-relay"});
  const resetRateReq = h.requests.filter(r => r.method === "POST" && r.route === "model/pricing/sources/currency").at(-1);
  assert.ok(resetRateReq);
  assert.deepEqual(JSON.parse(JSON.stringify(resetRateReq.values)), {
    source_id: "newapi:cookie-relay",
    currency_override: "auto",
    fiat_per_base_unit: null,
    sync: true,
    revision: "rev-rate-08",
  });
  resetRateReq.resolve({
    ...payload,
    revision: "rev-rate-auto",
    sources: payload.sources.map(s => s.source_id === "newapi:cookie-relay" ? {
      ...s,
      currency: "USD",
      currency_override: "auto",
      fiat_per_base_unit: null,
      effective_unit_scale: 1.0,
      site_meta: {
        schema_version: 2,
        system_name: "中文折扣站",
        quota_display_type: "USD",
        recharge_price: 2.0,
        site_recharge_price: 2.0,
        cny_recharge_hint: {recharge_price: 2.0, suggested_currency: "CNY"},
      },
    } : s),
  });
  await flush();

  // Level 3 gentle RMB recharge hint renders 1-click button and posts CNY + fiat_per_base_unit=2
  assert.match(h.root.innerHTML, /检测到该站在线充值价为 ¥2 \/ 基准单位/);
  assert.match(h.root.innerHTML, /data-pricing-apply-cny-hint="newapi:cookie-relay"/);
  h.root.click("[data-pricing-apply-cny-hint]", {pricingApplyCnyHint: "newapi:cookie-relay", rechargePrice: "2"});
  const hintReq = h.requests.filter(r => r.method === "POST" && r.route === "model/pricing/sources/currency").at(-1);
  assert.ok(hintReq);
  assert.deepEqual(JSON.parse(JSON.stringify(hintReq.values)), {
    source_id: "newapi:cookie-relay",
    currency_override: "CNY",
    fiat_per_base_unit: 2,
    sync: true,
    revision: "rev-rate-auto",
  });
  hintReq.resolve({
    ...payload,
    revision: "rev-hint-cny",
    sources: payload.sources.map(s => s.source_id === "newapi:cookie-relay" ? {
      ...s,
      currency: "CNY",
      currency_override: "CNY",
      fiat_per_base_unit: 2.0,
      effective_unit_scale: 2.0,
      site_meta: {
        schema_version: 2,
        system_name: "中文折扣站",
        quota_display_type: "USD",
        recharge_price: 2.0,
        site_recharge_price: 2.0,
        cny_recharge_hint: {recharge_price: 2.0, suggested_currency: "CNY"},
      },
    } : s),
  });
  await flush();
  assert.match(h.root.innerHTML, /结算币种已切换为 人民币 CNY \(¥\) 并完成重新同步/);

  // Switching currency dropdown back to auto explicitly clears fiat_per_base_unit=null so Level 3 USD is never stuck at 2x
  h.root.input("[data-pricing-source-currency]", "auto", {
    dataset: {pricingSourceCurrency: "newapi:cookie-relay"}, type: "change",
  });
  const revertReq = h.requests.filter(r => r.method === "POST" && r.route === "model/pricing/sources/currency").at(-1);
  assert.ok(revertReq);
  assert.deepEqual(JSON.parse(JSON.stringify(revertReq.values)), {
    source_id: "newapi:cookie-relay",
    currency_override: "auto",
    fiat_per_base_unit: null,
    sync: true,
    revision: "rev-hint-cny",
  });
  revertReq.resolve({
    ...payload,
    revision: "rev-hint-auto",
    sources: payload.sources.map(s => s.source_id === "newapi:cookie-relay" ? {
      ...s,
      currency: "USD",
      currency_override: "auto",
      fiat_per_base_unit: null,
      effective_unit_scale: 1.0,
      site_meta: {
        schema_version: 2,
        system_name: "中文折扣站",
        quota_display_type: "USD",
        recharge_price: 2.0,
        site_recharge_price: 2.0,
        cny_recharge_hint: {recharge_price: 2.0, suggested_currency: "CNY"},
      },
    } : s),
  });
  await flush();
  assert.match(h.root.innerHTML, /结算币种已切换为 自动识别 并完成重新同步/);
  h.page.unmount();
});
