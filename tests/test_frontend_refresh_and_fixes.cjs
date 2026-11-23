"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const COTTAGE = path.join(ROOT, "pages", "陪伴小屋");
const PREFIX = "/astrbot_plugin_private_companion/";
const read = (...parts) => fs.readFileSync(path.join(ROOT, ...parts), "utf8");
const flush = () => new Promise(resolve => setTimeout(resolve, 0));

const settle = async (predicate, max = 120) => {
  for (let i = 0; i < max && !predicate(); i += 1) await flush();
};

const makeClockRegex = () => /刚刚已刷新 \d{2}:\d{2}:\d{2}/;

/* ───────────────────── 1. Learning Hub Refresh Contract ───────────────────── */

test("审阅中心刷新：完整成功时权威替换移除已外部处理项、动态拾取新来源、展示刷新回执，部分失败仅保留失败源", async () => {
  const source = read("pages", "陪伴小屋", "learning-review-page.js");
  let storeA = ["r1", "r2"];
  let storeB = ["b1"];
  let failB = false;
  let availableSources = [
    {key: "role-a", label: "角色甲 · 私聊", growthApi: `${PREFIX}targets/a/roles/ra/growth`},
  ];
  const makeReview = id => ({
    review_id: id, growth_title: `目标 ${id}`, progress_note: `进展 ${id}`,
    actions: ["accept", "reject"], base_revision: 1, view_token: `tok-${id}`,
    created_at: "2026-10-08T10:00:00+00:00", unavailable_reason: null, evidence: [],
  });
  let releaseGate = null;
  const bridge = {
    apiGet: async route => {
      if (releaseGate) {
        const wait = releaseGate;
        releaseGate = null;
        await wait;
      }
      if (route.includes("targets/a/")) {
        if (route.endsWith("/progress")) return {status: "ok", data: {items: storeA.map(makeReview), next_cursor: null}};
        return {status: "ok", data: {items: [], next_cursor: null}};
      }
      if (route.includes("targets/b/")) {
        if (failB) throw {code: "bridge_timeout"};
        if (route.endsWith("/progress")) return {status: "ok", data: {items: storeB.map(makeReview), next_cursor: null}};
        return {status: "ok", data: {items: [], next_cursor: null}};
      }
      return {status: "ok", data: {items: [], next_cursor: null}};
    },
    apiPost: async () => ({status: "ok", data: {}}),
  };
  const listeners = new Map();
  const root = {
    isConnected: true, innerHTML: "",
    addEventListener(name, fn) { listeners.set(name, fn); },
    removeEventListener(name) { listeners.delete(name); },
    querySelector: () => null,
  };
  const win = {CompanionProduct: {bridgeNow: () => bridge, unwrap: v => v}, lucide: {createIcons() {}}};
  const ctx = vm.createContext({window: win, setTimeout, clearTimeout, Date, Math, Number, Object, Array, String, Set, Map});
  vm.runInContext(source, ctx);
  const page = ctx.window.CompanionLearningReviewPage;
  const click = selector => listeners.get("click")?.({target: {closest: probe => (probe === selector ? {dataset: {}} : null)}});

  page.mount(root, {sources: availableSources.slice(), getSources: () => availableSources.slice()});
  await settle(() => !page.state.loading);
  assert.deepEqual(page.state.items.map(i => i.review_id), ["r1", "r2"]);

  // 外部处理掉 r1 与 r2，同时新活跃来源 role-b 出现；并校验刷新进行中的 aria-busy / is-refreshing
  storeA = [];
  availableSources = [
    ...availableSources,
    {key: "role-b", label: "角色乙 · 私聊", growthApi: `${PREFIX}targets/b/roles/rb/growth`},
  ];
  let unlock;
  releaseGate = new Promise(r => { unlock = r; });
  page.state.notice = "旧错误";
  page.state.error = true;
  click("[data-review-refresh]");
  await settle(() => page.state.loading);
  assert.match(root.innerHTML, /class="lh-review-refresh is-refreshing"/);
  assert.match(root.innerHTML, /aria-busy="true"/);
  assert.equal(page.state.notice, "", "手动刷新应清除旧错误提示");
  unlock();
  await settle(() => !page.state.loading);

  assert.deepEqual(page.state.items.map(i => i.review_id), ["b1"], "完整刷新成功时应移除外部已处理的 r1/r2 并拾取新来源 b1");
  assert.match(root.innerHTML, makeClockRegex());

  // 部分失败：role-b 失败，role-a 新增 r3 -> role-b 旧项 b1 保留且提示失败源名称
  storeA = ["r3"];
  failB = true;
  click("[data-review-refresh]");
  await settle(() => !page.state.loading);
  assert.deepEqual(page.state.items.map(i => i.review_id).sort(), ["b1", "r3"]);
  assert.match(root.innerHTML, /角色乙 · 私聊/);
  page.unmount();
});

test("表达规则库刷新：完整成功权威移除外部停用规则、动态拾取新来源并显示刷新时间", async () => {
  const source = read("pages", "陪伴小屋", "expression-signals-page.js");
  let rulesA = ["sig-1", "sig-2"];
  let rulesB = ["sig-b1"];
  let activeSources = [
    {key: "s-a", scopeKey: "s-a", label: "来源甲", replyApi: `${PREFIX}targets/a/roles/ra/reply`},
  ];
  const makeRule = id => ({
    signal_id: id, run_id: "run-1", dimension: "diction", source_kind: "conversation_input",
    state: "adopted", observation: `观察 ${id}`, proposal: `用法 ${id}`,
    expression: {kind: "style", situation: "日常", pattern: `说法 ${id}`, avoid: ""},
    applicability: {channels: ["private"], relationship_stages: ["any"], emotion_gates: ["any"], intent: "any", keywords: []},
    created_at: "2026-10-08T10:00:00+00:00", injections: 1, can_revise: false,
    evidence: [], unavailable_reason: null, actions: ["retire"], view_token: `tok-${id}`,
  });
  const bridge = {
    apiGet: async route => {
      const list = route.includes("targets/a/") ? rulesA : rulesB;
      return {status: "ok", data: {items: list.map(makeRule), counts: {adopted: list.length}, capacity: {active: list.length, limit: 60}, next_cursor: null}};
    },
    apiPost: async () => ({status: "ok", data: {}}),
  };
  const listeners = new Map();
  const root = {
    isConnected: true, innerHTML: "",
    addEventListener(name, fn) { listeners.set(name, fn); },
    removeEventListener(name) { listeners.delete(name); },
    querySelector: () => null,
  };
  const win = {CompanionProduct: {bridgeNow: () => bridge, unwrap: v => v}, lucide: {createIcons() {}}, addEventListener() {}, removeEventListener() {}};
  const ctx = vm.createContext({window: win, setTimeout, clearTimeout, TextEncoder, JSON, Date, Math, Number, Object, Array, String, Set, Map, URLSearchParams});
  vm.runInContext(source, ctx);
  const page = ctx.window.CompanionExpressionSignalsPage;
  const click = selector => listeners.get("click")?.({target: {closest: probe => (probe === selector ? {dataset: {}} : null)}});

  page.mount(root, {sources: activeSources.slice(), getSources: () => activeSources.slice()});
  await settle(() => !page.state.busy);
  assert.deepEqual(page.state.items.map(i => i.signal_id).sort(), ["sig-1", "sig-2"]);

  rulesA = ["sig-1"];
  activeSources = [
    ...activeSources,
    {key: "s-b", scopeKey: "s-b", label: "来源乙", replyApi: `${PREFIX}targets/b/roles/rb/reply`},
  ];
  click("[data-signal-refresh]");
  await settle(() => !page.state.busy);
  assert.deepEqual(page.state.items.map(i => i.signal_id).sort(), ["sig-1", "sig-b1"], "外部移除的 sig-2 应消失且新来源 sig-b1 应出现");
  assert.match(root.innerHTML, makeClockRegex());
  page.unmount();
});

test("每日复盘刷新：更新 policy 与 status、完整成功权威替换、部分失败仅保留失败源并支持 getSources", async () => {
  const source = read("pages", "陪伴小屋", "self-review-page.js");
  let round = 1;
  let activeSources = [{key: "src-a", label: "角色甲", api: `${PREFIX}targets/a/roles/ra/growth`}];
  const makeItem = id => ({
    item_id: id, kind: "try_next_time", state: "accepted", automatic: true,
    observation: `观察 ${id}`, next_action: `建议 ${id}`, confidence: 0.9,
    created_at: "2026-10-08T10:00:00+00:00", expires_at: "2099-10-08T10:00:00+00:00",
    actions: ["withdraw"], view_token: `tok-${id}`, evidence: [],
  });
  const bridge = {
    ready: async () => ({pluginName: "astrbot_plugin_private_companion"}),
    apiGet: async (route, params) => {
      if (params?.state === "decided") return {status: "ok", data: {items: [], expired_in_page: 0}};
      if (route.includes("targets/a/")) {
        if (round === 1) {
          return {status: "ok", data: {
            items: [makeItem("i-1"), makeItem("i-2")], next_cursor: null,
            policy: {window_hours: 24, minimum_evidence: 3, empty_retry_seconds: 1800},
            status: {state: "not_run", code: ""}, enabled: true,
          }};
        }
        return {status: "ok", data: {
          items: [makeItem("i-1")], next_cursor: null,
          policy: {window_hours: 12, minimum_evidence: 5, empty_retry_seconds: 900},
          status: {state: "succeeded", code: ""}, enabled: true,
        }};
      }
      return {status: "ok", data: {
        items: [makeItem("b-1")], next_cursor: null,
        policy: {window_hours: 12, minimum_evidence: 5, empty_retry_seconds: 900},
        status: {state: "succeeded", code: ""}, enabled: true,
      }};
    },
    apiPost: async () => ({status: "ok", data: {}}),
  };
  const listeners = new Map();
  const root = {
    isConnected: true, innerHTML: "",
    addEventListener(name, fn) { listeners.set(name, fn); },
    removeEventListener(name) { listeners.delete(name); },
    querySelectorAll: () => [],
  };
  const win = {AstrBotPluginPage: bridge, lucide: {createIcons() {}}};
  const ctx = vm.createContext({window: win, setTimeout, clearTimeout, Date, Math, Number, Object, Array, String, Set, Map});
  vm.runInContext(source, ctx);
  const page = ctx.window.CompanionSelfReviewPage;

  await page.mount(root, {sources: activeSources.slice(), getSources: () => activeSources.slice()});
  assert.equal(page.state.data.policy.window_hours, 24);
  assert.equal(page.state.data.status.state, "not_run");
  assert.equal(page.state.data.items.length, 2);

  round = 2;
  activeSources = [...activeSources, {key: "src-b", label: "角色乙", api: `${PREFIX}targets/b/roles/rb/growth`}];
  listeners.get("click")?.({target: {closest: probe => (probe === "[data-review-refresh]" ? {} : null)}});
  await settle(() => !page.state.data.loading);

  assert.equal(page.state.data.policy.window_hours, 12, "刷新必须更新已有 policy");
  assert.equal(page.state.data.policy.minimum_evidence, 5);
  assert.equal(page.state.data.status.state, "succeeded", "刷新必须更新已有 status");
  assert.deepEqual([...page.state.data.items.map(i => i.item_id)].sort(), ["b-1", "i-1"], "已移除的 i-2 不再滞留且新来源 b-1 生效");
  assert.match(root.innerHTML, makeClockRegex());
  page.unmount();
});

test("成长链路：growth_view_stale/growth_progress_decided 自动刷新、动态来源与多源部分失败保留失败源", async () => {
  const source = read("pages", "陪伴小屋", "learning-page.js");
  let reviewCalls = 0;
  let failB = false;
  let itemsA = [{growth_id: "g-a1", kind: "goal", state: "active", revision: 1, title: "甲一", updated_at: "2026-10-08T10:00:00Z", actions: ["pause"], view_token: "t1"}];
  const itemsB = [{growth_id: "g-b1", kind: "goal", state: "active", revision: 1, title: "乙一", updated_at: "2026-10-08T09:00:00Z", actions: ["pause"], view_token: "t2"}];
  const activeSources = [
    {key: "a", label: "来源甲", growthApi: `${PREFIX}targets/a/roles/ra/growth`},
    {key: "b", label: "来源乙", growthApi: `${PREFIX}targets/b/roles/rb/growth`},
  ];
  const bridge = {
    ready: async () => ({pluginName: "astrbot_plugin_private_companion"}),
    apiGet: async route => {
      if (route.endsWith("/progress")) {
        reviewCalls += 1;
        return {status: "ok", data: {items: [{review_id: "rev-1", growth_title: "目标", base_revision: 1, actions: ["accept"], view_token: "vt", created_at: "2026-10-08T10:00:00Z"}], next_cursor: null}};
      }
      if (route.includes("targets/a/")) return {status: "ok", data: {items: itemsA, next_cursor: null}};
      if (failB) throw {code: "bridge_timeout"};
      return {status: "ok", data: {items: itemsB, next_cursor: null}};
    },
    apiPost: async route => {
      if (route.endsWith("/progress/command")) return {status: "error", data: {code: "growth_progress_decided"}};
      return {status: "ok", data: {}};
    },
  };
  const listeners = new Map();
  const root = {
    isConnected: true, innerHTML: "",
    addEventListener(name, fn) { listeners.set(name, fn); },
    removeEventListener(name) { listeners.delete(name); },
    querySelectorAll: () => [],
    querySelector: () => null,
  };
  const win = {AstrBotPluginPage: bridge, crypto: {randomUUID: () => "uuid-1"}};
  const ctx = vm.createContext({window: win, setTimeout, clearTimeout, Date, Math, Number, Object, Array, String, Set, Map});
  vm.runInContext(source, ctx);
  const page = ctx.window.CompanionLearningPage;

  await page.mount(root, {sources: activeSources, getSources: () => activeSources});
  assert.equal(page.state.data.items.length, 2);

  // 触发 growth_progress_decided 冲突错误 -> 中文文案 + 自动刷新审阅列表
  const beforeReviews = reviewCalls;
  const revRow = page.state.data.reviews[0];
  listeners.get("click")?.({target: {closest: probe => (probe === "[data-review]" ? {dataset: {review: "accept", id: revRow._rowKey}} : null)}});
  await settle(() => !page.state.data.writing && !page.state.data.loadingReviews);
  assert.ok(reviewCalls > beforeReviews, "growth_progress_decided 必须自动重读审阅列表");
  assert.match(page.state.data.notice, /已处理/);

  // 多源部分失败：来源乙失败、来源甲清空 -> 保留来源乙旧项 g-b1，移除来源甲旧项 g-a1
  itemsA = [];
  failB = true;
  listeners.get("click")?.({target: {closest: probe => (probe === "[data-action]" ? {dataset: {action: "refresh"}} : null)}});
  await settle(() => !page.state.data.loading);
  assert.deepEqual([...page.state.data.items.map(r => r.growth_id)], ["g-b1"]);
  assert.match(page.state.data.notice, /来源乙/);

  // 来源列表通过 getSources() 归零后点击刷新：清空旧列表/审阅/详情并给出中文提示
  activeSources.length = 0;
  listeners.get("click")?.({target: {closest: probe => (probe === "[data-action]" ? {dataset: {action: "refresh"}} : null)}});
  await flush();
  assert.equal(page.state.data.items.length, 0, "来源归零刷新后旧记录必须清空");
  assert.equal(page.state.data.reviews.length, 0, "来源归零刷新后旧审阅必须清空");
  assert.equal(page.state.data.detail, null);
  assert.equal(page.state.data.refreshedAt, "");
  assert.match(page.state.data.notice, /没有可读取的成长记录来源/);
  assert.match(root.innerHTML, /还没有可读取的成长记录/);
  page.unmount();
});

test("语义目录手动刷新：权威移除外部删除项并显示刷新时间", async () => {
  const source = read("pages", "陪伴小屋", "semantic-page.js");
  let serverItems = [
    {id: "r1", kind: "reaction", revision: 1, enabled: true, payload: {name: "表情一"}},
    {id: "r2", kind: "reaction", revision: 1, enabled: true, payload: {name: "表情二"}},
  ];
  const bridge = {
    apiGet: async () => ({
      state: "ready", selection_ready: true, reaction_ready: true,
      settings: {enabled: true, revision: 1}, providers: {}, catalog: {reaction: {total: serverItems.length, enabled: serverItems.length}},
      items: serverItems, next_after: null,
    }),
    apiPost: async () => ({}),
  };
  const listeners = new Map();
  const root = {
    isConnected: true, innerHTML: "",
    classList: {toggle() {}},
    addEventListener(name, fn) { listeners.set(name, fn); },
    querySelectorAll: () => [],
  };
  const win = {
    CompanionProduct: {
      hosted: () => true, scopeKey: () => "scope-1",
      selection: () => ({target_id: "t1", role_id: "r1"}), target: () => ({scope_kind: "private"}),
      bridgeNow: () => bridge, unwrap: v => v,
    },
    CompanionWorkbench: {scopeHintMarkup: () => "", scopeHintReset() {}, scopeHintKeydown() {}, scopeHintClick: () => false, dismissScopeHints() {}},
    lucide: {createIcons() {}},
  };
  const ctx = vm.createContext({window: win, AbortController, setTimeout, clearTimeout, Date, Math, Number, Object, Array, String, Set, Map});
  vm.runInContext(source, ctx);
  const page = ctx.window.CompanionSemanticPage;

  page.mount(root, {view: "expression"});
  await flush();
  assert.match(root.innerHTML, /表情二/);
  assert.match(root.innerHTML, /data-semantic="refresh"/);

  serverItems = [serverItems[0]];
  listeners.get("click")?.({target: {closest: probe => (probe === "[data-semantic]" ? {dataset: {semantic: "refresh"}} : null)}});
  await flush();
  assert.doesNotMatch(root.innerHTML, /表情二/, "手动刷新应权威移除外部已删除素材");
  assert.match(root.innerHTML, makeClockRegex());
  page.unmount();
});

test("学习总览手动刷新：清旧错、拾取动态 getSources 并显示刷新回执", async () => {
  const source = read("pages", "陪伴小屋", "learning-overview.js");
  let activeSources = [{key: "a", label: "甲", growthApi: `${PREFIX}targets/a/roles/ra/growth`}];
  const bridge = {
    apiGet: async route => (route.endsWith("/expression") ? {settings: {}, learning: null} : {items: [], next_cursor: null}),
    apiPost: async () => ({}),
  };
  const listeners = new Map();
  const root = {
    isConnected: true, innerHTML: "",
    setAttribute() {},
    addEventListener(name, fn) { listeners.set(name, fn); },
    removeEventListener(name) { listeners.delete(name); },
  };
  const win = {CompanionProduct: {bridgeNow: () => bridge, unwrap: v => v}, lucide: {createIcons() {}}};
  const ctx = vm.createContext({window: win, setTimeout, clearTimeout, Date, Math, Number, Object, Array, String, Set, Map});
  vm.runInContext(source, ctx);
  const page = ctx.window.CompanionLearningOverviewPage;

  page.mount(root, {sources: activeSources.slice(), getSources: () => activeSources.slice()});
  await settle(() => !page.state.loading);
  page.state.notice = "过期错误";
  page.state.error = true;
  activeSources = [...activeSources, {key: "b", label: "乙", growthApi: `${PREFIX}targets/b/roles/rb/growth`}];

  listeners.get("click")?.({target: {closest: probe => (probe === "[data-lh-refresh]" ? {} : null)}});
  await settle(() => !page.state.loading);
  assert.equal(page.state.notice, "", "手动刷新应清空旧提示");
  assert.match(root.innerHTML, /汇总 2 个记录归属/);
  assert.match(root.innerHTML, makeClockRegex());
  page.unmount();
});

/* ───────────────────── 2. Perception, Weather, Output & Workspaces ───────────────────── */

test("原生感知：刷新防连点并发、显示回执，且节点断连后自动停止轮询", async () => {
  const source = read("pages", "陪伴小屋", "perception-native-page.js");
  let gets = 0, pendingResolve = null;
  let timerFn = null, cleared = 0;
  const payload = {
    environment: {sources: {weather_ready: true, weather_provider: "open-meteo", alerts_ready: false}, settings: {
      planning_timezone: "Asia/Shanghai", planning_annual_calendars: "", planning_weather_enabled: false,
      planning_weather_location: JSON.stringify({name: "上海"}), planning_weather_max_age_minutes: 60,
    }},
    model: {edit_token: "tok", settings: {calendar_enabled: true, calendar_timezone: "Asia/Shanghai",
      health_enabled: false, health_proactive_enabled: false, holiday_enabled: true, holiday_country: "CN",
      lunar_enabled: true, automatic_solar_terms_enabled: false, almanac_enabled: false,
      platform_enabled: true, model_route_enabled: true, configured_modalities_enabled: true, guidance: ""}},
    contact: {settings: {}, effective: {}},
    news: {edit_token: "tok", source: {ready: true, assembled: true, kind: "rss", label: "早报"},
      diagnostic: {network_status: "not_checked", item_count: 0, items: []}, contact_enabled: false, automatic_enabled: false,
      settings: {news_share_enabled: false, "news.enabled": false, news_check_interval_minutes: 60, news_source_kind: "host", news_source_uid: "", news_source_url: ""}},
  };
  const win = {
    CompanionProduct: {
      hosted: () => true, scopeKey: () => "r1",
      selection: () => ({target_id: "t1", bot_id: "b1", role_id: "r1"}),
      target: () => ({target_id: "t1", bot_id: "b1", scope_kind: "private"}),
      windowRole: () => ({role_id: "r1", perception_native_api: `${PREFIX}perception-native`}),
      role: () => ({role_id: "r1", perception_native_api: `${PREFIX}perception-native`}),
      bridgeNow: () => win.AstrBotPluginPage,
    },
    AstrBotPluginPage: {
      apiGet: () => { gets += 1; return new Promise(resolve => { pendingResolve = resolve; }); },
      apiPost: async () => ({status: "ok", data: {}}),
    },
    setInterval: fn => { timerFn = fn; return 42; },
    clearInterval: () => { cleared += 1; },
  };
  const activeField = {tagName: "INPUT"};
  const doc = {visibilityState: "visible", activeElement: null, addEventListener() {}};
  vm.runInNewContext(source, {window: win, document: doc, AbortController, crypto: {}, TextEncoder, setTimeout, clearTimeout, Intl, Date});
  const makeNode = () => {
    const handlers = {};
    let html = "", writes = 0;
    return {
      isConnected: true,
      get innerHTML() { return html; },
      set innerHTML(v) { html = v; writes += 1; },
      get writes() { return writes; },
      contains(target) { return target === activeField; },
      addEventListener(t, fn) { handlers[t] = fn; },
      click(a) { handlers.click?.({target: {closest: () => ({dataset: {nativeAction: a}})}}); },
    };
  };
  const env = makeNode(), cal = makeNode(), mod = makeNode(), news = makeNode();
  win.CompanionNativePerceptionPage.mount({environment: env, calendar: cal, model: mod, news});
  assert.equal(gets, 1);
  pendingResolve({status: "ok", data: payload});
  await flush();

  // 手动点击两次刷新 -> 防连点并发，仅发 1 次 GET，进行中带 is-refreshing + aria-busy
  env.click("refresh");
  env.click("refresh");
  assert.equal(gets, 2, "刷新在途时连点不得并发发第二次 GET");
  assert.match(env.innerHTML, /is-refreshing/);
  assert.match(env.innerHTML, /aria-busy="true"/);
  pendingResolve({status: "ok", data: payload});
  await flush();
  assert.match(env.innerHTML, makeClockRegex());

  // 30s 静默后台轮询：按钮不得闪灰或变「读取中…」，且焦点在表单内时不重写该表单 innerHTML
  doc.activeElement = activeField;
  const calWritesBefore = cal.writes;
  const envWritesBefore = env.writes;
  timerFn?.();
  assert.equal(gets, 3);
  assert.equal(env.writes, envWritesBefore, "静默后台轮询请求前不应重绘闪灰按钮");
  assert.doesNotMatch(env.innerHTML, /is-refreshing|读取中…/);
  pendingResolve({status: "ok", data: payload});
  await flush();
  assert.equal(cal.writes, calWritesBefore, "焦点位于表单内时后台轮询不得重写表单打断输入");
  doc.activeElement = null;

  // 节点全部断开连接后，30s 定时器触发应自动卸载且不再发请求
  env.isConnected = cal.isConnected = mod.isConnected = news.isConnected = false;
  timerFn?.();
  assert.equal(gets, 3, "游离 DOM 不得继续发轮询请求");
  assert.equal(cleared, 1, "游离 DOM 应自动清理 30s 定时器");
});

test("天气数据源重新读取：独立 loading 态防并发、提交按钮保持保存文案并显示刷新时间", async () => {
  const source = read("pages", "陪伴小屋", "weather-source-page.js");
  let gets = 0, pendingResolve = null;
  const payload = {
    settings: {provider: "auto", api_host: "", refresh_minutes: 30, jwt_credential_id: "", jwt_project_id: "", jwt_developer_id: "", has_jwt_private_key: false},
    edit_token: "a".repeat(64), has_credential: false, credential_kind: "api_key", credential_source: "none",
    source_state: "ready", actual_provider: "open-meteo",
  };
  const win = {
    CompanionProduct: {scopeKey: () => "s1", bridgeNow: () => ({apiGet: () => { gets += 1; return new Promise(r => { pendingResolve = r; }); }})},
    addEventListener() {},
  };
  vm.runInNewContext(source, {window: win, document: {dispatchEvent() {}}, AbortController, setTimeout, clearTimeout, Date});
  const handlers = {};
  const root = {isConnected: true, innerHTML: "", addEventListener(t, fn) { handlers[t] = fn; }};
  win.CompanionWeatherSourcePage.mount(root);
  pendingResolve({status: "ok", data: payload});
  await flush();

  handlers.click?.({target: {closest: () => ({dataset: {weatherSourceAction: "reload"}})}});
  handlers.click?.({target: {closest: () => ({dataset: {weatherSourceAction: "reload"}})}});
  assert.equal(gets, 2, "重新读取在途时连点不得并发");
  assert.match(root.innerHTML, /data-weather-source-action="reload"[^>]*aria-busy="true"/);
  assert.match(root.innerHTML, /保存数据源<\/button>/, "加载刷新时提交按钮不得误显示「正在保存…」");
  pendingResolve({status: "ok", data: payload});
  await flush();
  assert.match(root.innerHTML, makeClockRegex());
  win.CompanionWeatherSourcePage.unmount();
});

test("产出页观测刷新：即使作品列表已载入，重新读取仍同步更新内容摘要并展示刷新回执", async () => {
  const source = read("pages", "陪伴小屋", "output-page.js");
  let worksCount = 2;
  const statNodes = {works: {textContent: ""}, candidates: {textContent: ""}, updated: {textContent: ""}, preview: {textContent: ""}};
  const obsHost = {innerHTML: ""};
  const worksHost = {textContent: "", innerHTML: ""};
  const obsBtn = {disabled: false, attrs: {}, classes: new Set(),
    classList: {toggle(c, on) { on ? obsBtn.classes.add(c) : obsBtn.classes.delete(c); }},
    setAttribute(k, v) { obsBtn.attrs[k] = v; }};
  const listeners = new Map();
  const root = {
    isConnected: true,
    addEventListener(t, fn) { listeners.set(t, fn); },
    querySelector(sel) {
      if (sel === "#outputObservationWorkspace") return obsHost;
      if (sel === "[data-output-works-list]") return worksHost;
      return null;
    },
    querySelectorAll(sel) {
      if (sel === '[data-output-native-action="observability"]') return [obsBtn];
      if (sel === '[data-output-content-stat="works"]') return [statNodes.works];
      if (sel === '[data-output-content-stat="candidates"]') return [statNodes.candidates];
      if (sel === '[data-output-content-stat="updated"]') return [statNodes.updated];
      if (sel === '[data-output-content-preview]') return [statNodes.preview];
      return [];
    },
  };
  let obsGate = null;
  const win = {
    CompanionProduct: {
      scopeKey: () => "s1",
      role: () => ({role_id: "r1", model_api: `${PREFIX}model`}),
      roleTarget: () => ({target_id: "t1", scope_kind: "private"}),
      relative: v => v.slice(PREFIX.length),
      request: async (_m, ep) => {
        if (ep === "works/library") return {works: [{title: "旧作", state: "writing", created_at: "2026-10-01T00:00:00Z"}]};
        if (ep === "model/image/observability") {
          if (obsGate) {
            const wait = obsGate;
            obsGate = null;
            await wait;
          }
          return {images: {total: 3, items: [{action: "image.generate", state: "succeeded", created_at: "2026-10-12T04:00:00Z", elapsed_ms: 1200}]}, content: {available: true, works_count: worksCount, candidate_count: 1, updated_at: "2026-10-12T04:05:00Z"}};
        }
        if (ep === "model/image/output") return {settings: {backend: "auto", enabled: false, endpoints: [], comfyui: {}, sd_webui: {}}, revision: "r1", host_registered: false};
        return {providers: []};
      },
    },
    dispatchEvent() {},
    lucide: {createIcons() {}},
  };
  vm.runInNewContext(source, {window: win, document: {addEventListener() {}}, AbortController, Event: class {}, setTimeout, clearTimeout, Date});
  const page = win.CompanionOutputPage;
  page.mount(root);
  await flush();
  page.showWorks();
  await flush();
  assert.equal(statNodes.works.textContent, "1");
  assert.match(obsHost.innerHTML, /3 次调用/);

  worksCount = 5;
  let releaseObs;
  obsGate = new Promise(r => { releaseObs = r; });
  const refreshing = page.refreshObservability(true);
  assert.match(obsHost.innerHTML, /3 次调用/, "已有观测记录时刷新期间不得闪白清空塌陷");
  assert.doesNotMatch(obsHost.innerHTML, /正在读取最近调用/);
  assert.equal(obsBtn.classes.has("is-refreshing"), true);
  assert.equal(obsBtn.attrs["aria-busy"], "true");
  releaseObs();
  await refreshing;
  assert.equal(statNodes.works.textContent, "5", "worksLoaded 为 true 时观测刷新仍必须更新内容摘要");
  assert.match(obsHost.innerHTML, makeClockRegex());
  page.unmount();
});

test("三个工作区（生活/关系与权限/新的她）具备刷新防并发、防乱序与时间回执", () => {
  const life = read("pages", "日记与生活", "app.js");
  assert.match(life, /let .*loadSeq = 0/);
  assert.match(life, /if \(manual && loading\) return/);
  assert.match(life, /if \(seq !== loadSeq\) return/);
  assert.match(life, /is-refreshing/);
  assert.match(life, /刚刚已刷新/);

  const identity = read("pages", "关系与权限", "app.js");
  assert.match(identity, /let strangerSequence = 0/);
  assert.match(identity, /if \(seq !== strangerSequence \|\| dirSeq !== requestSequence\) return/);
  assert.match(identity, /if \(manual\) \{\s*await loadStrangers\(\)/);
  assert.match(identity, /is-refreshing/);

  const designer = read("pages", "新的她", "app.js");
  assert.match(designer, /let optionsBusy = false, optionsSeq = 0/);
  assert.match(designer, /if \(optionsBusy\) return/);
  assert.match(designer, /if \(disposed \|\| seq !== optionsSeq\) return/);
  assert.match(designer, /\$\("refresh-options"\)\.addEventListener\("click", \(\) => loadOptions\(true\)\)/);
  assert.match(designer, /刚刚已刷新/);
});

/* ───────────────────── 3. Security, Lifecycle, Concurrency & UX Fixes ───────────────────── */

test("状态编辑器：待核对变更明细转义防 XSS，且天气摘要不双重转义", async () => {
  const source = read("pages", "陪伴小屋", "state-editor.js");
  const maintenanceSrc = read("pages", "陪伴小屋", "state-maintenance.js");
  const storage = new Map();
  const win = {
    localStorage: {getItem: k => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: k => storage.delete(k)},
    crypto: {randomUUID: () => "op-1"},
    CompanionProduct: {target: () => ({scope_kind: "private", target_id: "t1", bot_id: "b1"}), selection: () => ({target_id: "t1", bot_id: "b1", role_id: "r1"}), windowRole: () => ({role_id: "r1"})},
  };
  const ctx = vm.createContext({window: win, localStorage: win.localStorage, crypto: win.crypto, TextEncoder, structuredClone, Date, Math, Number, Object, Array, String, Set, Map, JSON});
  vm.runInContext(maintenanceSrc, ctx);
  vm.runInContext(source, ctx);

  const editor = ctx.window.CompanionStateEditor.create({
    api: () => new Promise(() => {}),
    refresh: async () => null,
    changed() {},
  });
  editor.setScope({key: "s1", path: "state", storage: "st-1"});
  editor.setStatus({
    role_edit_token: "tok",
    edit_token: "tok",
    settings: {
      health_enabled: true, health_expression_prompt: "正常",
      planning_weather_enabled: true,
      planning_weather_location: JSON.stringify({name: "A&B<城>"}),
      planning_weather_max_age_minutes: 60,
    },
  });
  // 天气摘要只转义一次：应为 &lt;城&gt;，不得出现 &amp;lt;
  const weatherHtml = editor.markup({section: "weather"});
  assert.match(weatherHtml, /A&amp;B&lt;城&gt;/);
  assert.doesNotMatch(weatherHtml, /&amp;lt;/, "不得双重转义");

  // 注入 XSS 载荷保存 -> pendingDetail 必须经 e(...) 转义
  editor.open("health");
  editor.input({target: {matches: sel => sel === "[data-state-field]", dataset: {stateField: "health_expression_prompt"}, type: "textarea", value: '<img src=x onerror="alert(1)">'}});
  editor.submit({target: {id: "state-edit-form"}, preventDefault() {}});
  const healthHtml = editor.markup({section: "state", feature: "health"});
  assert.doesNotMatch(healthHtml, /<img src=x/, "原始 HTML 标签不得注入 DOM");
  assert.match(healthHtml, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
  editor.destroy();
});

test("生命周期清理：app.js 卸载工作区与 7 个面板模块，perception-overview 游离 DOM 自清定时器", () => {
  const app = read("pages", "陪伴小屋", "app.js");
  assert.match(app, /product\.unmountWorkspace\?\.\(\)/, "paintPanel 替换内容前应卸载嵌入式工作区");
  for (const mod of ["unmountSetupGuide", "CompanionBackupPage?.unmount", "CompanionStoragePage?.unmount",
    "CompanionConfigPortPage?.unmount", "CompanionModelsPage?.unmount", "CompanionPerceptionOverviewPage?.unmount",
    "CompanionNativePerceptionPage?.unmount"]) {
    assert.ok(app.includes(mod), `app.js 必须补齐 ${mod} 卸载分支`);
  }
  const overview = read("pages", "陪伴小屋", "perception-overview.js");
  assert.match(overview, /if\s*\(!root\?\.isConnected\)\s*\{[^}]*clearInterval\(clockTimer\)/);
});

test("决策页防骚扰：跨窗口切换重置 loading 并丢弃旧窗口慢响应，杜绝串写", async () => {
  const source = read("pages", "陪伴小屋", "decision-page.js");
  const pendingGets = [];
  const bridge = {
    apiGet: ep => new Promise(resolve => pendingGets.push({ep, resolve})),
    apiPost: async () => ({status: "ok", data: {}}),
  };
  const win = {
    CompanionProduct: {bridgeNow: () => bridge, bridgeFor: async () => bridge},
    lucide: {createIcons() {}},
  };
  vm.runInNewContext(source, {window: win, AbortController, setTimeout, clearTimeout, Date, Object, Array, String, Number, Set, Map, JSON});
  const page = win.CompanionDecisionPage;
  const makeHost = () => {
    const guardHost = {innerHTML: ""};
    const host = {
      isConnected: true, innerHTML: "", dataset: {},
      addEventListener() {}, removeEventListener() {},
      querySelector: sel => (sel === '[data-decision-panel="guard"]' || sel === "#decision-panel-guard" ? guardHost : null),
      querySelectorAll: () => [],
      guardHost,
    };
    return host;
  };

  const hostA = makeHost();
  page.mount(hostA, {groupSettingsMarkup: "", harassmentApi: `${PREFIX}targets/a/roles/r1/harassment`, scopeKind: "private"});
  assert.equal(pendingGets.length, 1);

  // A 仍在途时切换到窗口 B
  const hostB = makeHost();
  page.mount(hostB, {groupSettingsMarkup: "", harassmentApi: `${PREFIX}targets/b/roles/r1/harassment`, scopeKind: "private"});
  assert.equal(pendingGets.length, 2, "切换到窗口 B 后不得被旧 loading 锁死，必须立即发起 B 请求");

  // 窗口 B 先返回，随后窗口 A 的慢响应才返回 -> A 不得覆盖 B
  pendingGets[1].resolve({status: "ok", data: {settings: {enabled: false, dry_run: false, cooldown_seconds: 600, strike_block_at: 3}, entries: [{user_id: "b-user", scope: "s-b", kind: "block", reasons: []}], revision: "rev-b"}});
  await flush();
  pendingGets[0].resolve({status: "ok", data: {settings: {enabled: true, dry_run: false, cooldown_seconds: 120, strike_block_at: 1}, entries: [{user_id: "a-user", scope: "s-a", kind: "block", reasons: []}, {user_id: "a-user-2", scope: "s-a", kind: "block", reasons: []}], revision: "rev-a"}});
  await flush();
  const rendered = hostB.innerHTML + hostB.guardHost.innerHTML;
  assert.match(rendered, /10 分钟冷却/, "窗口 B 的配置应正确渲染");
  assert.match(rendered, /1 人/, "窗口 B 的黑名单计数应保持为 1");
  assert.doesNotMatch(rendered, /2 分钟冷却/, "窗口 A 慢响应不得串写覆盖窗口 B");
  page.unmount();
});

test("脚本补引、HTTP UUID 回退、维护深链、SDK 失败清理与 postMessage 防护", () => {
  const html = read("pages", "陪伴小屋", "index.html");
  assert.match(html, /src="role-profile-transfer\.js/);
  assert.match(html, /src="reply-diagnostics\.js/);

  // embedded-bridge.js 明文 HTTP 下安装 crypto.randomUUID polyfill
  const bridgeSrc = read("pages", "陪伴小屋", "embedded-bridge.js");
  const fakeCrypto = {getRandomValues(arr) { for (let i = 0; i < arr.length; i++) arr[i] = (i * 17 + 3) & 255; return arr; }};
  const fakeParent = {postMessage() {}};
  const win = {crypto: fakeCrypto, parent: fakeParent, addEventListener() {}, location: {search: ""}};
  vm.runInNewContext(bridgeSrc, {window: win, parent: fakeParent, document: {documentElement: {dataset: {}, style: {setProperty() {}}, setAttribute() {}}, addEventListener() {}, dispatchEvent() {}}, crypto: fakeCrypto, Uint8Array, Object, Math, Date, Promise, Map, Set, URLSearchParams, CustomEvent: class {}, Event: class {}, setTimeout, clearTimeout});
  assert.equal(typeof fakeCrypto.randomUUID, "function");
  assert.match(fakeCrypto.randomUUID(), /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);

  // 维护子页签白名单对齐
  const app = read("pages", "陪伴小屋", "app.js");
  assert.match(app, /'system-prompts'/);
  assert.match(app, /'logs'/);

  // product-page.js SDK 失败清理 + postMessage try/catch + role-profile-draft.js 竞态防护
  const productJs = read("pages", "陪伴小屋", "product-page.js");
  assert.match(productJs, /failedSdkTags/);
  assert.match(productJs, /script\.remove\?\.\(\)/);
  const draftJs = read("pages", "陪伴小屋", "role-profile-draft.js");
  assert.match(draftJs, /personaChoicesSeq/);
});

test("群聊记忆页：手动刷新并发去重、单次列表渲染、缺失 ID 不全行误选与竞态乱序丢弃", async () => {
  const source = read("pages", "关系与记忆", "group-memory.js");
  const resolvers = [];
  const bridge = {
    apiGet: () => new Promise(r => resolvers.push(r)),
    apiPost: async () => ({status: "ok", data: {}}),
  };
  const makeEl = (tag = "div") => {
    const handlers = {};
    const el = {
      tagName: tag.toUpperCase(), innerHTML: "", textContent: "", className: "", hidden: false, disabled: false, value: "",
      dataset: {}, attrs: {}, children: [], classes: new Set(), replaceCount: 0, handlers,
      setAttribute(k, v) { el.attrs[k] = v; },
      addEventListener(type, fn) { handlers[type] = fn; },
      replaceChildren(...items) { el.replaceCount += 1; el.children = [...items]; },
      append(...items) { el.children.push(...items); },
    };
    el.classList = {toggle(c, on) { on ? el.classes.add(c) : el.classes.delete(c); }};
    return el;
  };
  const byId = new Map();
  for (const id of ["gm-list", "gm-detail", "gm-count", "gm-notice", "gm-refresh", "gm-refresh-stamp", "gm-filter", "gm-previous", "gm-next", "gm-page", "gm-list-heading"]) {
    byId.set(id, makeEl());
  }
  const mainEl = makeEl("main");
  const win = {addEventListener() {}, crypto: {}};
  const doc = {
    getElementById: id => byId.get(id) || null,
    querySelector: sel => (sel === "main" ? mainEl : null),
    querySelectorAll: () => [],
    createElement: tag => makeEl(tag),
    addEventListener() {},
  };
  vm.runInNewContext(source, {window: win, document: doc, Date, Math, Number, Object, Array, String, Set, Map, Promise, setTimeout, clearTimeout});
  const gm = win.CompanionGroupMemoryPage;
  gm.mount(bridge, {display: {target_label: "测试群", role_name: "角色"}});
  assert.equal(resolvers.length, 1);

  // 在途时手动 refresh() 必须被并发去重，不发第二次 GET
  gm.refresh();
  assert.equal(resolvers.length, 1, "加载在途时手动 refresh() 不得并发重复拉取");

  // 切换筛选触发第二次非手动加载（与第一次并发），模拟第二次先返回、第一次后返回
  byId.get("gm-filter").value = "retracted";
  byId.get("gm-filter").handlers.change?.();
  assert.equal(resolvers.length, 2);
  const replaceBefore = byId.get("gm-list").replaceCount;
  resolvers[1]({status: "ok", data: {items: [{summary: "条目一"}, {summary: "条目二"}], next_cursor: null}});
  await flush();
  assert.equal(byId.get("gm-list").replaceCount - replaceBefore, 1, "成功路径应只重建一次列表 DOM");

  resolvers[0]({status: "ok", data: {items: [{memory_id: "old", summary: "过时条目"}], next_cursor: null}});
  await flush();
  const listChildren = byId.get("gm-list").children;
  const serialized = JSON.stringify(listChildren);
  assert.doesNotMatch(serialized, /过时条目/, "慢响应不得覆盖新结果");
  assert.equal(listChildren.filter(row => /selected|active/.test(row.className) || row.attrs["aria-pressed"] === "true").length, 0,
    "undefined === undefined 不得误选所有行");

  // 空闲态调用公开 refresh() 正常触发并产出时间回执
  gm.refresh();
  assert.equal(resolvers.length, 3);
  resolvers[2]({status: "ok", data: {items: [{memory_id: "m1", summary: "最新条目"}], next_cursor: null}});
  await flush();
  assert.match(byId.get("gm-refresh-stamp").textContent, makeClockRegex());
});

test("华为健康：链式 setTimeout 轮询且登录阶段未变化时不重绘 DOM 抢焦点", async () => {
  const source = read("pages", "陪伴小屋", "huawei-health-page.js");
  let polls = 0;
  let scheduled = null;
  let setHtmlCount = 0;
  let html = "";
  const stateEl = {textContent: ""};
  const deadlineEl = {textContent: ""};
  const msgEl = {textContent: "", hidden: false};
  const rootHandlers = {};
  const root = {
    isConnected: true,
    get innerHTML() { return html; },
    set innerHTML(v) { html = v; setHtmlCount += 1; },
    addEventListener(type, fn) { rootHandlers[type] = fn; },
    removeEventListener(type) { delete rootHandlers[type]; },
    querySelector(sel) {
      if (sel === "[data-hw-login-state]") return stateEl;
      if (sel === "[data-hw-login-deadline]") return deadlineEl;
      if (sel === "[data-hw-login-message]") return msgEl;
      return null;
    },
    querySelectorAll: () => [],
  };
  let deadline = 60;
  const makeStatus = () => ({
    bound: false, token: {cached: false}, cookie: {exists: false, count: 0},
    credentials: {state: "none"}, browser: {found: true},
    login: {state: "NEED_SMS", need: {sms: true, trust: false}, message: "请输入短信验证码", deadline_left: deadline},
  });
  const win = {
    CompanionProduct: {
      hosted: () => true,
      role: () => ({role_id: "r1", perception_native_api: `${PREFIX}perception-native`}),
      target: () => ({target_id: "t1", scope_kind: "private"}),
      roleTarget: () => ({target_id: "t1", scope_kind: "private"}),
      request: async (_method, ep) => {
        if (ep === "huawei-health/status") { polls += 1; return makeStatus(); }
        if (ep === "huawei-health/login/start") return {status: makeStatus()};
        if (ep === "perception-native") return {model: {edit_token: "t", settings: {health_enabled: true, health_proactive_enabled: false}}};
        return {};
      },
    },
    setTimeout(fn, delay) { if (delay === 2000) scheduled = fn; return 1; },
    clearTimeout() {},
  };
  vm.runInNewContext(source, {window: win, AbortController, setTimeout: win.setTimeout, clearTimeout: win.clearTimeout, Date, Object, Array, String, Number, Set, Map, JSON});
  win.CompanionHuaweiHealthPage.mount(root);
  await flush();
  rootHandlers.click?.({target: {closest: sel => (sel.includes("[data-hw-action]") ? {dataset: {hwAction: "login-start"}} : null)}});
  await flush();
  await flush();
  const rendersAfterStart = setHtmlCount;
  assert.equal(typeof scheduled, "function", "登录进行中应使用链式 setTimeout 轮询");

  // 触发一次轮询，登录结构未变、仅倒计时变化 -> 就地更新文案，不重绘整个 innerHTML 抢输入焦点
  deadline = 58;
  await scheduled();
  await flush();
  assert.equal(polls, 2);
  assert.equal(setHtmlCount, rendersAfterStart, "轮询返回相同登录阶段时不得重写 root.innerHTML 抢输入焦点");
  assert.equal(deadlineEl.textContent, "剩余 58 秒");
  win.CompanionHuaweiHealthPage.unmount();
});

/* ───────────────────── 4. v7.3.0 Regressions, Config I18n & Hygiene ───────────────────── */

test("v7.3.0 前端修复：home-loading.css 伪元素扫光与 svg 选择器、home-sidebar 0 能量与睡眠保活、qzone.owner_uin 与 phone 错误码", () => {
  const loadingCss = read("pages", "陪伴小屋", "home-loading.css");
  assert.match(loadingCss, /\.home-state-grid::after/);
  assert.match(loadingCss, /overflow:\s*hidden/);
  assert.doesNotMatch(loadingCss, /#homeHeroCard\[data-loading="true"\]\s+\.home-state-grid\{[^}]*home-loading-sweep/);
  assert.match(loadingCss, /\.home-state-glyph svg/);
  assert.match(loadingCss, /\.home-state-row-icon svg/);
  assert.match(loadingCss, /\.home-state-hero-icon svg/);
  assert.match(loadingCss, /\.home-environment-icon svg/);
  assert.match(loadingCss, /prefers-reduced-motion:\s*reduce/);
  assert.match(loadingCss, /:root\[data-motion="off"\]/);

  // 统一刷新 CSS 动效限定在按钮且响应减弱动效
  const consoleCss = read("pages", "陪伴小屋", "console.css");
  const wsCss = read("pages", "陪伴小屋", "workspace-theme.css");
  assert.match(consoleCss, /button\.is-refreshing svg/);
  assert.match(consoleCss, /:root\[data-motion="off"\] button\.is-refreshing svg/);
  assert.match(wsCss, /button\.is-refreshing svg/);

  // home-sidebar: energy=0 不显示读取中，刷新不覆盖已有睡眠态
  const sidebarSrc = read("pages", "陪伴小屋", "home-sidebar.js");
  const sbCtx = vm.createContext({window: {}, Object, Array, String, Number, Math, Date});
  vm.runInContext(sidebarSrc, sbCtx);
  const energyVital = sbCtx.window.CompanionHomeSidebar.vital("energy", 0, {loading: true});
  assert.equal(energyVital.label, "较低", "能量为 0 时应显示真实档位而非读取中");
  assert.notEqual(energyVital.visual, "loading");
  const boardHtml = sbCtx.window.CompanionHomeSidebar.renderState({
    loading: true,
    state: {state: {values: {energy: 0, sleep: "睡眠中"}}, settings: {enabled: true, home_show_energy: true, home_show_sleep: true}},
    life: {activities: [{kind: "sleep", state: "active", started_at: new Date().toISOString()}]},
  });
  assert.doesNotMatch(boardHtml, /data-loading="true"/, "已有状态快照时后台刷新不得覆写为加载骨架");
  assert.doesNotMatch(boardHtml, /home-state-hero[^>]*data-visual="loading"/, "已有睡眠态时后台刷新不得覆写睡眠展示");
  assert.match(boardHtml, /睡眠中/, "后台刷新期间已有睡眠态应保持可见");

  // config-editor qzone.owner_uin + workbench-page 错误映射
  const cfgSrc = read("pages", "陪伴小屋", "config-editor.js");
  assert.match(cfgSrc, /owner_uin:\s*"主人 QQ 号"/);
  assert.match(cfgSrc, /qzone_owner_uin_invalid/);
  const wbSrc = read("pages", "陪伴小屋", "workbench-page.js");
  for (const code of ["qzone_owner_uin_invalid", "device_phone_scope_invalid", "device_phone_target_changed", "device_phone_configuration_invalid"]) {
    assert.ok(wbSrc.includes(code), `workbench-page.js 必须包含 ${code} 中文映射`);
  }
});

test("代码卫生：pages/ 目录下全部 JS/CSS 文件无 UTF-8 BOM", () => {
  const walk = dir => fs.readdirSync(dir, {withFileTypes: true}).flatMap(entry => {
    const full = path.join(dir, entry.name);
    return entry.isDirectory() ? walk(full) : /\.(js|css)$/.test(entry.name) ? [full] : [];
  });
  const files = walk(path.join(ROOT, "pages"));
  const withBom = files.filter(file => {
    const buf = fs.readFileSync(file);
    return buf.length >= 3 && buf[0] === 0xEF && buf[1] === 0xBB && buf[2] === 0xBF;
  });
  assert.deepEqual(withBom, [], `存在 UTF-8 BOM 文件：${withBom.join(", ")}`);
});

test("工作区卸载隔离：首次配置引导卸载不误伤表达/理解/决策/感知与状态工作区，表达页签不污染内容区", () => {
  const app = read("pages", "陪伴小屋", "app.js");
  const mountFnStart = app.indexOf("function mountPanelWorkspaces()");
  const mountFnEnd = app.indexOf("\nfunction notifyAppearanceChanged()", mountFnStart);
  assert.ok(mountFnStart > 0 && mountFnEnd > mountFnStart, "应能定位 mountPanelWorkspaces");
  const mountFn = app.slice(mountFnStart, mountFnEnd);

  const setupUnmountPos = mountFn.indexOf("unmountSetupGuide()");
  const stateMountPos = mountFn.indexOf("CompanionStatePage?.mount");
  const replyMountPos = mountFn.indexOf("CompanionReplyPage?.mount");
  assert.ok(setupUnmountPos > 0 && stateMountPos > 0 && replyMountPos > 0);
  assert.ok(setupUnmountPos < stateMountPos, "首次配置引导卸载必须早于 CompanionStatePage 挂载");
  assert.ok(setupUnmountPos < replyMountPos, "首次配置引导卸载必须早于 CompanionReplyPage 挂载");
  assert.equal(mountFn.indexOf("unmountSetupGuide()", replyMountPos), -1, "挂载回复工作区后不得再调用 unmountSetupGuide");

  // 表达页签选择器限定在 [role="tab"]，避免命中 <section id="replyWorkspace">
  assert.match(app, /function activateExpressionModule[\s\S]{0,220}\$\$\('\[role="tab"\]\[data-expression-module\]'\)/);
  assert.doesNotMatch(app, /\$\$\('\[data-expression-module\]'\)/);

  // 实际执行 setup-guide.js：未挂载快捷子工作区时 unmount() 绝不卸载外部 CompanionReplyPage / CompanionStatePage
  let replyUnmounts = 0, stateUnmounts = 0;
  const setupSrc = read("pages", "陪伴小屋", "setup-guide.js");
  const setupWin = {
    CompanionReplyPage: {mount() {}, unmount() { replyUnmounts += 1; }},
    CompanionStatePage: {mount() {}, unmount() { stateUnmounts += 1; }},
    CompanionProduct: {hosted: () => false, targets: () => [], role: () => null, target: () => null},
    addEventListener() {}, removeEventListener() {},
  };
  const setupCtx = vm.createContext({
    window: setupWin,
    document: {addEventListener() {}, removeEventListener() {}, querySelector() { return null; }},
    console, setTimeout, clearTimeout, Promise, Object, Array, String, Number, Boolean, Math, Date, Map, Set, URLSearchParams, JSON,
  });
  vm.runInContext(setupSrc, setupCtx);
  assert.equal(typeof setupWin.CompanionSetup?.unmount, "function");
  setupWin.CompanionSetup.unmount();
  assert.equal(replyUnmounts, 0, "未持有快捷回复工作区时 CompanionSetup.unmount() 不得误卸载 CompanionReplyPage");
  assert.equal(stateUnmounts, 0, "未持有快捷状态工作区时 CompanionSetup.unmount() 不得误卸载 CompanionStatePage");

  // 治理与维护页面不使用未打包的 lucide 图标名 image-check
  const maintSrc = read("pages", "陪伴小屋", "maintenance-page.js");
  assert.doesNotMatch(maintSrc, /data-lucide="[^"]*image-check|"image-check"/);
});

test("全站右侧滑出抽屉双向丝滑动效：detailDialog、semantic-editor-drawer 与 mem-detail 均支持平滑开合与减弱动效", async () => {
  // 1. #detailDialog / .detail-dialog (page-motion.css + app.js)
  const motionCss = read("pages", "陪伴小屋", "page-motion.css");
  assert.match(motionCss, /:is\(#detailDialog,\s*\.detail-dialog\)\s*\{[^}]*translate3d\(100%,\s*0,\s*0\)[^}]*220ms cubic-bezier\(0\.4,\s*0,\s*1,\s*1\)/);
  assert.match(motionCss, /:is\(#detailDialog,\s*\.detail-dialog\)\[open\]\s*\{[^}]*translate3d\(0,\s*0,\s*0\)[^}]*260ms cubic-bezier\(0\.22,\s*1,\s*0\.36,\s*1\)/);
  assert.match(motionCss, /@starting-style\s*\{[\s\S]*?:is\(#detailDialog,\s*\.detail-dialog\)\[open\]\s*\{[^}]*translate3d\(100%,\s*0,\s*0\)/);
  assert.match(motionCss, /:root\[data-motion="off"\]\s*:is\(#detailDialog,\s*\.detail-dialog/);
  assert.match(motionCss, /@media\s*\(prefers-reduced-motion:\s*reduce\)[\s\S]*?:is\(#detailDialog,\s*\.detail-dialog/);
  const appJs = read("pages", "陪伴小屋", "app.js");
  assert.match(appJs, /detailCloseTimer/);

  // 2. .semantic-editor-drawer (semantic-page.css + expression-config.css + semantic-page.js 实跑 DOM 生命周期)
  const semCss = read("pages", "陪伴小屋", "semantic-page.css");
  assert.match(semCss, /\.semantic-editor-drawer\{[^}]*pointer-events:none[^}]*visibility:hidden/);
  assert.match(semCss, /\.semantic-editor-drawer\.is-open\s+\.semantic-editor\{[^}]*translate3d\(0,0,0\)[^}]*260ms cubic-bezier\(0\.22,1,0\.36,1\)/);
  assert.match(semCss, /\.semantic-editor\{[^}]*translate3d\(100%,0,0\)[^}]*220ms cubic-bezier\(0\.4,0,1,1\)/);
  assert.match(semCss, /:root\[data-motion="off"\]\s*\.semantic-editor-drawer/);
  assert.match(semCss, /:root\[data-motion="low"\]\s*\.semantic-editor-drawer\s+\.semantic-editor/);
  const expCss = read("pages", "陪伴小屋", "expression-config.css");
  assert.doesNotMatch(expCss, /\.expression-reaction-workspace\s+\.semantic-editor\{[^}]*transform:/, "expression-config.css 不得覆写 .semantic-editor 的滑出 transform");

  // 实跑 semantic-page.js 持久抽屉壳：打开先挂壳下一帧加 .is-open、关闭先撤 .is-open 保活内容再清空
  const semSrc = read("pages", "陪伴小屋", "semantic-page.js");
  const rafs = [];
  const timers = [];
  function makeNode(tag = "div") {
    const classes = new Set();
    const attrs = {};
    const handlers = {};
    const node = {
      tagName: tag.toUpperCase(),
      isConnected: true,
      parentNode: null,
      children: [],
      offsetWidth: 440,
      _html: "",
      get firstChild() { return node.children[0] || null; },
      get className() { return [...classes].join(" "); },
      set className(v) { classes.clear(); String(v || "").split(/\s+/).filter(Boolean).forEach(c => classes.add(c)); },
      get innerHTML() { return node._html; },
      set innerHTML(v) { node._html = String(v); if (v === "") node.children.length = 0; },
      classList: {
        add: c => classes.add(c),
        remove: c => classes.delete(c),
        contains: c => classes.has(c),
        toggle: (c, on) => on ? classes.add(c) : classes.delete(c),
      },
      setAttribute: (k, v) => { attrs[k] = String(v); },
      getAttribute: k => attrs[k] ?? null,
      appendChild(child) { child.parentNode = node; node.children.push(child); return child; },
      removeChild(child) { const i = node.children.indexOf(child); if (i >= 0) node.children.splice(i, 1); child.parentNode = null; return child; },
      insertAdjacentHTML(_pos, html) {
        const stub = makeNode("section");
        stub._html = String(html);
        const idx = node.parentNode ? node.parentNode.children.indexOf(node) : -1;
        if (idx >= 0) { stub.parentNode = node.parentNode; node.parentNode.children.splice(idx, 0, stub); }
      },
      addEventListener(type, fn) { handlers[type] = fn; },
      querySelectorAll: () => [],
      querySelector: () => null,
      _handlers: handlers,
    };
    return node;
  }
  const docHandlers = {};
  const fakeDoc = {
    documentElement: {dataset: {motion: "full"}},
    body: {classList: {contains: () => false}},
    createElement: tag => makeNode(tag),
    addEventListener(type, fn) { docHandlers[type] = fn; },
  };
  const bridge = {
    apiGet: async (_r, params) => {
      if (params?.id) return {item: {id: params.id, kind: "reaction", revision: 1, enabled: true, payload: {name: "笑脸"}}, image_url: ""};
      return {
        state: "ready", selection_ready: true, reaction_ready: true,
        settings: {revision: 1}, providers: {}, catalog: {reaction: {enabled: 1, total: 1}},
        items: [{id: "r1", kind: "reaction", revision: 1, enabled: true, payload: {name: "笑脸"}}],
        next_after: null,
      };
    },
    apiPost: async () => ({ok: true}),
  };
  const semWin = {
    CompanionProduct: {
      hosted: () => true, scopeKey: () => "scope-1",
      selection: () => ({target_id: "t1", role_id: "r1"}),
      target: () => ({scope_kind: "private"}),
      unwrap: v => v, bridgeNow: () => bridge, bridgeFor: async () => bridge,
    },
    CompanionWorkbench: {scopeHintMarkup: () => "", scopeHintReset() {}, scopeHintKeydown() {}, scopeHintClick: () => false, dismissScopeHints() {}},
    lucide: {createIcons() {}},
    matchMedia: () => ({matches: false}),
  };
  const semCtx = vm.createContext({
    window: semWin, document: fakeDoc, AbortController,
    requestAnimationFrame: fn => { rafs.push(fn); return rafs.length; },
    cancelAnimationFrame() {},
    setTimeout: (fn, ms) => { timers.push({fn, ms}); return timers.length; },
    clearTimeout() {},
    Promise, Object, Array, String, Number, Boolean, Math, Date, Map, Set, FormData: class {}, FileReader: class {},
  });
  vm.runInContext(semSrc, semCtx);
  const host = makeNode("div");
  semWin.CompanionSemanticPage.mount(host, {view: "expression"});
  await flush();
  const drawer = host.children.find(c => c.classList.contains("semantic-editor-drawer"));
  assert.ok(drawer, "首次渲染后应挂载持久 .semantic-editor-drawer 容器");
  assert.equal(drawer.classList.contains("is-open"), false);
  assert.equal(drawer.getAttribute("aria-hidden"), "true");

  // 点击打开条目 -> 先写入内容但不立刻 is-open，rAF 后切 .is-open 触发滑入过渡
  host._handlers.click({target: {closest: s => s === "[data-semantic-open]" ? {dataset: {semanticOpen: "r1", semanticKind: "reaction"}} : null}});
  await flush();
  assert.match(drawer.innerHTML, /id="semanticEditor"/);
  assert.equal(drawer.getAttribute("aria-hidden"), "false");
  assert.equal(drawer.classList.contains("is-open"), false, "打开首帧应先处于滑出起点，由下一帧触发滑入过渡");
  assert.ok(rafs.length >= 1);
  rafs.pop()();
  assert.equal(drawer.classList.contains("is-open"), true, "下一帧后进入 .is-open 完成平滑滑入");

  // 按 Escape 关闭 -> 立即撤掉 .is-open 与置 aria-hidden="true"，但保留内容 240ms 供平滑滑出后再清空
  docHandlers.keydown({key: "Escape", preventDefault() {}});
  assert.equal(drawer.classList.contains("is-open"), false);
  assert.equal(drawer.getAttribute("aria-hidden"), "true");
  assert.match(drawer.innerHTML, /id="semanticEditor"/, "关闭过渡期间应保留抽屉内容避免闪断");
  assert.ok(timers.length >= 1);
  timers.pop().fn();
  assert.equal(drawer.innerHTML, "", "滑出过渡完成后清空隐藏抽屉内容");
  semWin.CompanionSemanticPage.unmount();

  // 3. .mem-detail (memory-page.css)
  const memCss = read("pages", "陪伴小屋", "memory-page.css");
  assert.match(memCss, /\.mem-detail\{[^}]*translate3d\(100%,0,0\)[^}]*220ms cubic-bezier\(0\.4,0,1,1\)/);
  assert.match(memCss, /\.mem-detail\.is-open\{[^}]*translate3d\(0,0,0\)[^}]*260ms cubic-bezier\(0\.22,1,0\.36,1\)/);
  assert.match(memCss, /:root\[data-motion="off"\]\s*\.mem-detail/);
  assert.match(memCss, /@media\(prefers-reduced-motion:reduce\)\{\.mem-detail/);
});

test("主动与联系节律盘排版精修：桌面三卡等高配平、移除 1460px 误折行、四圈交互图例与生效节律速览", () => {
  const css = read("pages", "陪伴小屋", "contact-page.css").replace(/\/\*[\s\S]*?\*\//g, "");
  assert.doesNotMatch(css, /@media\s*\(\s*max-width:\s*1460px\s*\)/, "不得保留按外层视口 1460px 强压 .ct-controls 为单列的过时规则");
  assert.match(css, /\.ct-dial-wrap\{[^}]*align-items:stretch/);
  assert.match(css, /\.ct-dial-stage\{[^}]*border:1px solid var\(--t-line\)[^}]*border-radius:var\(--r-md\)/);
  assert.match(css, /\.ct-dial-face\{[^}]*position:relative/);
  assert.match(css, /\.ct-dial-legend\{[^}]*repeat\(2,minmax\(0,1fr\)\)/);
  assert.match(css, /\.ct-controls\{[^}]*align-items:stretch/);
  assert.match(css, /\.ct-global-summary\{[^}]*margin:auto 0 0/);
  const js = read("pages", "陪伴小屋", "contact-page.js");
  assert.match(js, /class="ct-dial-face"/);
  assert.match(js, /class="ct-dial-legend"/);
  assert.match(js, /data-ring-jump="\$\{ring\}"/);
  assert.match(js, /class="ct-global-summary"/);
});

test("公共工作区统一动效令牌与弹窗/覆层/折叠卡双向平滑开合（不含模型页与全局搜索）", () => {
  const motionCss = read("pages", "陪伴小屋", "page-motion.css");
  const wsThemeCss = read("pages", "陪伴小屋", "workspace-theme.css");
  for (const token of [
    "--motion-ease-out",
    "--motion-ease-in",
    "--motion-dur-enter",
    "--motion-dur-exit",
    "--motion-dur-expand",
    "--motion-dur-collapse",
  ]) {
    assert.ok(motionCss.includes(`${token}:`), `page-motion.css 应声明 ${token}`);
    assert.ok(wsThemeCss.includes(`${token}:`), `workspace-theme.css 应声明 ${token}`);
  }

  // 1. 通用模态 <dialog> 双向开合（排除右侧抽屉与独立全局搜索弹窗），旧单向 keyframes 不再覆写
  assert.match(motionCss, /dialog:not\(#detailDialog\):not\(\.detail-dialog\):not\(#searchDialog\)\s*\{[^}]*allow-discrete/);
  assert.match(motionCss, /dialog:not\(#detailDialog\):not\(\.detail-dialog\):not\(#searchDialog\)\[open\]\s*\{[^}]*scale\(1\)/);
  assert.match(motionCss, /@starting-style\s*\{[\s\S]*?dialog:not\(#detailDialog\):not\(\.detail-dialog\):not\(#searchDialog\)\[open\]/);
  assert.doesNotMatch(read("pages", "陪伴小屋", "confirmation-dialog.css"), /@keyframes\s+confirmation-enter/);
  assert.doesNotMatch(read("pages", "陪伴小屋", "self-page.css"), /@keyframes\s+role-profile-dialog-in/);

  // 2. 全部 <details> 折叠卡（含对话框内与语音页全局语音默认值）平滑双向展开，展开后 overflow:visible 不裁切
  assert.match(motionCss, /:is\(#homeShell,\s*dialog\)\s+details:not\(\.mp-group\):not\(\.mp-legend\):not\(\.mp-drawer-advanced\)::details-content/);
  assert.match(motionCss, /:is\(#homeShell,\s*dialog\)\s+details:not\(\.mp-group\):not\(\.mp-legend\):not\(\.mp-drawer-advanced\)\[open\]::details-content\s*\{[^}]*height:\s*auto;[^}]*overflow:\s*visible/);
  const voiceCss = read("pages", "陪伴小屋", "voice-page.css");
  assert.doesNotMatch(voiceCss, /\.expression-voice-workspace\s+\.voice-global\{[^}]*overflow:\s*clip/,
    "全局语音默认值展开后不得因 overflow:clip 裁切控件或焦点环");
  assert.match(voiceCss, /\.voice-global-summary::after\{[^}]*var\(--motion-ease-out/);

  // 3. 非 <dialog> 覆层弹窗、popover、端点折叠卡与退场防竞态
  assert.match(motionCss, /\.reply-preview-modal\.is-open[\s\S]*?\.output-album-lightbox\.is-open/);
  assert.match(motionCss, /\.wb-context-tip:popover-open/);
  assert.match(motionCss, /\.output-endpoint-collapse\.is-open\s*\{[^}]*grid-template-rows:\s*1fr/);
  assert.match(motionCss, /\.output-endpoint-collapse\s*>\s*\.output-endpoint-edit\s*\{[^}]*overflow:\s*hidden;[^}]*transition:\s*overflow\s+0s\s+allow-discrete\s+0s/);
  assert.match(motionCss, /\.output-endpoint-collapse\.is-open\s*>\s*\.output-endpoint-edit\s*\{[^}]*overflow:\s*visible;[^}]*transition:\s*overflow\s+0s\s+allow-discrete\s+var\(--motion-dur-expand\)/);
  assert.match(motionCss, /details:not\(\.mp-group\):not\(\.mp-legend\):not\(\.mp-drawer-advanced\)\[open\]::details-content\s*\{[^}]*overflow\s+0s\s+allow-discrete\s+var\(--motion-dur-expand\)/);
  assert.match(wsThemeCss, /details\[open\]::details-content\{[^}]*overflow 0s allow-discrete var\(--motion-dur-expand\)/);
  assert.match(read("pages", "陪伴小屋", "reply-page.js"), /previewModalCloseTimer/);
  assert.match(read("pages", "陪伴小屋", "output-page.js"), /albumLightboxCloseTimer/);
  assert.match(read("pages", "陪伴小屋", "output-page.js"), /data-output-endpoint-collapse/);
  assert.match(read("pages", "陪伴小屋", "state-page.js"), /settingsCloseTimer/);
  assert.match(read("pages", "陪伴小屋", "contact-page.js"), /testDialogCloseTimer/);
  assert.match(read("pages", "关系与权限", "member-editor.js"), /dismissAnimatedOverlay/);
  assert.match(read("pages", "关系与权限", "style.css"), /\.member-dialog-host\.is-open\s+\.member-dialog/);

  // 4. 嵌入式工作区统一双向 <dialog> 与 <details> 过渡
  assert.match(wsThemeCss, /dialog\[open\]\{animation:none;opacity:1/);
  assert.match(wsThemeCss, /@starting-style\{dialog\[open\]/);
  assert.match(wsThemeCss, /details\[open\]::details-content\{height:auto;overflow:visible/);

  // 5. 减弱动效与系统减少动效下：不误清展开箭头旋转与 #toast 水平居中
  assert.match(motionCss, /:root\[data-motion="off"\]\s+\.output-endpoint-toggle\s+svg/);
  assert.match(motionCss, /:root\[data-motion="off"\]\s+#toast[\s\S]*?translate\(-50%,\s*0\)/);
  assert.match(motionCss, /@media\s*\(prefers-reduced-motion:\s*reduce\)[\s\S]*?#toast\s*\{\s*transform:\s*translate\(-50%,\s*0\);?\s*\}/);
});
