"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "pages", "陪伴小屋", "expression-signals-page.js"), "utf8");
const HOST = "astrbot_plugin_private_companion";
const flush = () => new Promise(resolve => setTimeout(resolve, 0));

test("规则库导出与 V1 分享包预览/导入使用已选择角色路由", async () => {
  const gets = [], posts = [], listeners = new Map();
  const bridge = {
    apiGet: async (route, query) => {
      gets.push({route, query});
      const items = query?.state === "adopted" ? [{signal_id:"signal-1",run_id:"run-1",dimension:"diction",
        source_kind:"conversation_input",observation:"晚间问候时使用轻松短句",proposal:"用轻松短句问候",
        state:"adopted",created_at:"2026-10-08T10:00:00+00:00",actions:["retire"],injections:3,can_revise:true,
        expression:{kind:"style",situation:"彼此有默契",pattern:"懂的都懂",avoid:"认真求助时不用"},
        positive_feedback:1,negative_feedback:2,evidence:[],view_token:"token"}] : [];
      return {status:"ok", data:{items, capacity:{active:1,limit:60}}};
    },
    apiPost: async (route, body) => {
      posts.push({route, body});
      if (route.endsWith("/style/export")) return {status:"ok", data:{package:{schema:"private-companion-expression-pack",version:1,rule_groups:[]},rule_count:0,excluded_count:0}};
      if (route.endsWith("/style/import/preview")) return {status:"ok", data:{title:"测试分享",group_count:1,rule_count:1,
        importable_rule_count:1,duplicate_rule_count:0,capacity_blocked_count:0,rejected_count:0,
        preview_token:"a".repeat(64),candidates:[{dimension:"diction",observation:"短称呼",proposal:"使用简短称呼"}]}};
      return {status:"ok", data:{imported_count:1,state:"pending_review",replayed:false}};
    },
  };
  const root = {isConnected:true,innerHTML:"",addEventListener:(name, callback)=>listeners.set(name, callback),
    removeEventListener:name=>listeners.delete(name),querySelector:()=>null};
  const window = {CompanionProduct:{bridgeNow:()=>bridge,unwrap:value=>value},lucide:{createIcons(){}}};
  const context = vm.createContext({window,setTimeout,clearTimeout,TextEncoder,JSON,Date,Math,Number,Object,Array,String,URLSearchParams});
  vm.runInContext(source, context);
  await context.window.CompanionExpressionSignalsPage.mount(root, {
    replyApi:`/${HOST}/targets/profile-a/roles/role-a/reply`,
    target:{scope_kind:"group",group_id:"group-a"},scopeKey:"scope-a",
  });
  for(let index=0;index<20&&!gets.length;index++)await flush();
  for(let index=0;index<30&&context.window.CompanionExpressionSignalsPage.state.busy;index++)await flush();
  assert.ok(gets.length);
  assert.ok(gets.every(call=>call.route===`targets/profile-a/roles/role-a/reply/style`));
  assert.ok(root.innerHTML.includes("明确反馈 · 认可 1 / 不喜欢 2"));
  assert.match(root.innerHTML,/懂的都懂/);
  assert.match(root.innerHTML,/彼此有默契/);
  for(const dimension of ["表情符号","标点","直接程度","排版","其他"])assert.ok(root.innerHTML.includes(dimension));
  assert.match(root.innerHTML,/已拒绝/);

  const click = async selector => listeners.get("click")?.({target:{closest:value=>value===selector?{}:null}});
  const change = target => listeners.get("change")?.({target});
  const input = target => listeners.get("input")?.({target});
  listeners.get("click")({target:{closest:selector=>selector==="[data-signal-edit]"?{dataset:{signalEdit:"scope-a::signal-1"}}:null}});
  assert.match(root.innerHTML,/编辑表达规则/);
  const field={value:"原来如此呀",dataset:{ruleField:"pattern"},closest:selector=>selector==="[data-rule-field]"?field:null};
  input(field);
  await click("[data-rule-save]");
  for(let index=0;index<30&&!root.innerHTML.includes("表达规则已更新");index++)await flush();
  const edit=posts.find(call=>call.route.endsWith("/style/revise"));
  assert.equal(edit.route,`targets/profile-a/roles/role-a/reply/style/revise`);
  assert.equal(edit.body.view_token,"token");
  assert.equal(edit.body.changes.expression.pattern,"原来如此呀");
  assert.equal(edit.body.changes.expression.avoid,"认真求助时不用");
  await click("[data-share-toggle]");
  await click("[data-share-export]");
  for(let index=0;index<30&&!root.innerHTML.includes("分享包已生成");index++)await flush();
  assert.ok(posts.some(call=>call.route===`targets/profile-a/roles/role-a/reply/style/export`));
  const json = JSON.stringify({schema:"private-companion-expression-pack",version:1,rule_groups:[{rules:[{kind:"style",label:"短称呼",pattern:"使用简短称呼"}]}]});
  input({value:json,closest:selector=>selector==="[data-share-import-json]"?{value:json}:null});
  await click("[data-share-preview]");
  for(let index=0;index<30&&!root.innerHTML.includes("预览已更新");index++)await flush();
  const preview = posts.find(call=>call.route.endsWith("/style/import/preview"));
  assert.equal(preview.body.package_json,json);
  await click("[data-share-apply]");
  for(let index=0;index<30&&!root.innerHTML.includes("已加入审阅中心");index++)await flush();
  assert.equal(posts.find(call=>call.route.endsWith("/style/import/apply")).body.preview_token,"a".repeat(64));
  assert.equal(root.innerHTML.includes("已加入审阅中心"),true);
  context.window.CompanionExpressionSignalsPage.unmount();
  assert.equal(listeners.size,0);
});

test("多来源部分读取失败时保留失败来源与错误码", async () => {
  const bridge = {
    apiGet: async route => {
      if (route === "targets/profile-b/roles/role-b/reply/style") {
        /* 真实宿主桥的形态：status=error 只抛 message，code 已经丢失。 */
        throw new Error("expression_group_context_required");
      }
      if (route === "targets/profile-c/roles/role-c/reply/style") {
        throw Object.assign(new Error("future_source_failure"), {code: "future_source_failure"});
      }
      return {status: "ok", data: {items: [{signal_id: "signal-a", run_id: "run-a", dimension: "diction",
        source_kind: "conversation_input", observation: "来源甲可读", proposal: "继续使用", state: "adopted",
        created_at: "2026-10-08T10:00:00+00:00", actions: ["retire"], injections: 1, can_revise: true,
        expression: {kind: "style", situation: "日常", pattern: "来源甲规则", avoid: ""},
        evidence: [], view_token: "token"}], capacity: {active: 1, limit: 50}, counts: {adopted: 1}}};
    },
    apiPost: async () => ({status: "ok", data: {}}),
  };
  const root = {isConnected: true, innerHTML: "", addEventListener() {}, removeEventListener() {},
    querySelector: () => null};
  const window = {CompanionProduct: {bridgeNow: () => bridge, unwrap: value => value},
    lucide: {createIcons() {}}};
  const context = vm.createContext({window, setTimeout, clearTimeout, TextEncoder, JSON, Date, Math,
    Number, Object, Array, String, URLSearchParams});
  vm.runInContext(source, context);
  const page = context.window.CompanionExpressionSignalsPage;
  await page.mount(root, {sources: [
    {key: "source-a", scopeKey: "scope-a", label: "来源甲",
      replyApi: `/${HOST}/targets/profile-a/roles/role-a/reply`},
    {key: "source-b", scopeKey: "scope-b", label: "来源乙",
      replyApi: `/${HOST}/targets/profile-b/roles/role-b/reply`},
    {key: "source-c", scopeKey: "scope-c", label: "来源丙",
      replyApi: `/${HOST}/targets/profile-c/roles/role-c/reply`},
  ]});
  for (let index = 0; index < 40 && page.state.busy; index++) await flush();
  assert.equal(page.state.notice, "3 个来源中 2 个读取失败，已显示可用记录。");
  assert.match(root.innerHTML, /来源甲规则/);
  assert.match(root.innerHTML, /3 个来源中 2 个读取失败/);
  assert.match(root.innerHTML, /来源乙/);
  assert.match(root.innerHTML, /该群还没有可用于表达学习的对话/);
  assert.match(root.innerHTML, /来源丙/);
  assert.match(root.innerHTML, /原始依据已不可读取/);
  assert.match(root.innerHTML, /role="status"/);
  assert.match(root.innerHTML, /data-code="expression_group_context_required"/);
  assert.match(root.innerHTML, /data-code="future_source_failure"/);
  assert.doesNotMatch(root.innerHTML, /部分规则暂时无法读取，已显示可用记录。/);
  page.unmount();
});

test("空页的 unavailable_reason 作为中性说明而不是读取失败", async () => {
  const bridge = {
    apiGet: async route => route === "targets/profile-b/roles/role-b/reply/style"
      ? {status: "ok", data: {items: [], counts: {}, next_cursor: null,
        unavailable_reason: "role_binding_missing"}}
      : {status: "ok", data: {items: [{signal_id: "signal-a", run_id: "run-a", dimension: "diction",
        source_kind: "conversation_input", observation: "私聊规则可读", proposal: "继续使用", state: "adopted",
        created_at: "2026-10-08T10:00:00+00:00", actions: ["retire"], injections: 0, can_revise: true,
        expression: {kind: "style", situation: "日常", pattern: "私聊规则", avoid: ""},
        evidence: [], view_token: "token"}], capacity: {active: 1, limit: 50}, counts: {adopted: 1}}},
    apiPost: async () => ({status: "ok", data: {}}),
  };
  const root = {isConnected: true, innerHTML: "", addEventListener() {}, removeEventListener() {},
    querySelector: () => null};
  const window = {CompanionProduct: {bridgeNow: () => bridge, unwrap: value => value},
    lucide: {createIcons() {}}};
  const context = vm.createContext({window, setTimeout, clearTimeout, TextEncoder, JSON, Date, Math,
    Number, Object, Array, String, URLSearchParams});
  vm.runInContext(source, context);
  const page = context.window.CompanionExpressionSignalsPage;
  await page.mount(root, {sources: [
    {key: "source-a", scopeKey: "scope-a", label: "私聊来源",
      replyApi: `/${HOST}/targets/profile-a/roles/role-a/reply`},
    {key: "source-b", scopeKey: "scope-b", label: "群聊来源",
      replyApi: `/${HOST}/targets/profile-b/roles/role-b/reply`},
  ]});
  for (let index = 0; index < 40 && page.state.busy; index++) await flush();
  assert.equal(page.state.notice, "1 个来源还没有可读取的表达学习记录，已显示其他来源。");
  assert.match(root.innerHTML, /私聊规则/);
  assert.match(root.innerHTML, /群聊来源/);
  assert.match(root.innerHTML, /这个来源还没有绑定角色/);
  assert.match(root.innerHTML, /data-code="role_binding_missing"/);
  assert.doesNotMatch(root.innerHTML, /lh-inline-status is-error/);
  page.unmount();
});

test("旧作用域规则只作为可展开诊断，不冒充读取失败", async () => {
  const bridge = {
    apiGet: async route => route === "targets/profile-b/roles/role-b/reply/style"
      ? {status: "ok", data: {items: [], counts: {}, next_cursor: null, legacy_orphan_count: 4}}
      : {status: "ok", data: {items: [{signal_id: "signal-a", run_id: "run-a", dimension: "diction",
        source_kind: "conversation_input", observation: "私聊规则可读", proposal: "继续使用", state: "adopted",
        created_at: "2026-10-08T10:00:00+00:00", actions: ["retire"], injections: 0, can_revise: true,
        expression: {kind: "style", situation: "日常", pattern: "私聊规则", avoid: ""},
        evidence: [], view_token: "token"}], capacity: {active: 1, limit: 50}, counts: {adopted: 1},
        legacy_orphan_count: 0}},
    apiPost: async () => ({status: "ok", data: {}}),
  };
  const root = {isConnected: true, innerHTML: "", addEventListener() {}, removeEventListener() {},
    querySelector: () => null};
  const window = {CompanionProduct: {bridgeNow: () => bridge, unwrap: value => value},
    lucide: {createIcons() {}}};
  const context = vm.createContext({window, setTimeout, clearTimeout, TextEncoder, JSON, Date, Math,
    Number, Object, Array, String, URLSearchParams});
  vm.runInContext(source, context);
  const page = context.window.CompanionExpressionSignalsPage;
  await page.mount(root, {sources: [
    {key: "source-a", scopeKey: "scope-a", label: "私聊来源",
      replyApi: `/${HOST}/targets/profile-a/roles/role-a/reply`},
    {key: "source-b", scopeKey: "scope-b", label: "群聊来源",
      replyApi: `/${HOST}/targets/profile-b/roles/role-b/reply`},
  ]});
  for (let index = 0; index < 40 && page.state.busy; index++) await flush();
  assert.equal(page.state.notice, "4 条旧版群规则保存在旧作用域，暂未生效。");
  assert.match(root.innerHTML, /群聊来源/);
  assert.match(root.innerHTML, /4 条旧版群规则保存在旧作用域，暂未生效；需要单独迁移后才能读取和生效。/);
  assert.match(root.innerHTML, /data-code="legacy_expression_scope"/);
  assert.doesNotMatch(root.innerHTML, /lh-inline-status is-error/);
  page.unmount();
});

/* ---------------------------------------------------------------------------
 * 分页保活回归：写操作 / 跨模块刷新通知 / 追加去重 / 全部来源不可读的措辞。
 * ------------------------------------------------------------------------- */

const SIGNAL_RULE = (id, overrides = {}) => ({signal_id: id, run_id: "run-" + id, dimension: "diction",
  source_kind: "conversation_input", observation: "观察 " + id, proposal: "说法 " + id, state: "adopted",
  created_at: "2026-10-08T10:00:00+00:00", actions: ["retire"], injections: 0, can_revise: false,
  expression: {kind: "style", situation: "日常", pattern: "说法 " + id, avoid: ""},
  evidence: [], view_token: "token-" + id, confidence: 0.5, applicability: null, ...overrides});

/* 每页 2 条、after_id 是上一页最后一条 signal_id 的假后端；retired 集合里的规则从结果里消失。 */
function pagedSignals(ids = ["signal-1", "signal-2", "signal-3", "signal-4", "signal-5", "signal-6"]) {
  const retired = new Set();
  const calls = [];
  const live = () => ids.filter(id => !retired.has(id));
  const pageAt = cursor => {
    const list = live();
    const index = cursor ? list.findIndex(id => id === cursor) : -1;
    const start = index + 1;
    const items = list.slice(start, start + 2).map(id => SIGNAL_RULE(id));
    const next = start + items.length < list.length ? items[items.length - 1].signal_id : null;
    return {items, next_cursor: next, counts: {adopted: list.length},
      capacity: {active: list.length, limit: 480}};
  };
  const bridge = {
    apiGet: async (route, query = {}) => {
      calls.push({route, query});
      if (Array.isArray(query.state) || Array.isArray(query.after_id)) throw new Error("repeated query");
      return {status: "ok", data: pageAt(query.after_id || "")};
    },
    apiPost: async () => ({status: "ok", data: {}}),
  };
  return {bridge, calls, retired, live};
}

function signalsHarness(backend, rootOverride = null) {
  const listeners = new Map();
  const windowListeners = new Map();
  const root = rootOverride || {isConnected: true, innerHTML: "",
    addEventListener(name, callback) {listeners.set(name, callback);},
    removeEventListener(name) {listeners.delete(name);},
    querySelector: () => null, querySelectorAll: () => []};
  const window = {CompanionProduct: {bridgeNow: () => backend.bridge, unwrap: value => value},
    lucide: {createIcons() {}},
    addEventListener(name, callback) {windowListeners.set(name, callback);},
    removeEventListener(name) {windowListeners.delete(name);}};
  const context = vm.createContext({window, setTimeout, clearTimeout, TextEncoder, JSON, Date, Math,
    Number, Object, Array, String, URLSearchParams, Set, Map});
  vm.runInContext(source, context);
  const page = context.window.CompanionExpressionSignalsPage;
  const emit = (selector, dataset) => listeners.get("click")?.({target: {closest: probe =>
    probe === selector ? {dataset} : null}});
  return {page, root, emit,
    /* 输入事件按真实路径派发（不直接调页面内部函数）。 */
    input: target => listeners.get("input")?.({target}),
    change: target => listeners.get("change")?.({target}),
    dispatch: (name, detail) => windowListeners.get(name)?.({detail})};
}

/* 分享面板需要的那一点 DOM：`innerHTML` 写入时按标记重建按钮桩，`disabled` 取自
   标记里是否带该属性；之后页面就地同步的 `button.disabled = …` 直接反映在桩上。
   这样用例读的是「渲染出的属性 + 就地同步」，不是页面的内部状态。 */
function shareDom() {
  const listeners = new Map();
  const stubs = new Map();
  const SELECTORS = ["[data-share-preview]", "[data-share-apply]", "[data-share-export]"];
  const root = {
    isConnected: true,
    _html: "",
    get innerHTML() {return this._html;},
    set innerHTML(value) {
      this._html = value;
      SELECTORS.forEach(selector => {
        const attribute = selector.slice(1, -1);
        const tag = value.match(new RegExp(`<button[^>]*\\b${attribute}\\b[^>]*>`));
        stubs.set(selector, tag ? {disabled: /\sdisabled(?=[\s=>])/.test(tag[0])} : null);
      });
    },
    addEventListener(name, callback) {listeners.set(name, callback);},
    removeEventListener(name) {listeners.delete(name);},
    querySelector: selector => stubs.get(selector) || null,
    querySelectorAll: () => [],
  };
  return {
    root,
    button: selector => root.querySelector(selector),
    /* 真浏览器不会给 disabled 的按钮派发 click —— 已知 disabled 的桩同样拒绝，避免用例假绿。 */
    click: selector => {
      const element = root.querySelector(selector);
      if (element && element.disabled) return false;
      listeners.get("click")?.({target: {closest: probe => probe === selector ? {dataset: {}} : null}});
      return true;
    },
    input: value => listeners.get("input")?.({target: {value,
      closest: probe => probe === "[data-share-import-json]" ? {value} : null}}),
  };
}

const singleSource = [{key: "source-a", scopeKey: "scope-a", label: "私聊来源",
  replyApi: `/${HOST}/targets/profile-a/roles/role-a/reply`}];

const settleSignals = async page => {
  for (let attempt = 0; attempt < 200 && page.state.busy; attempt += 1) await flush();
};

test("规则库停用一条规则后不会把已加载的页丢回第一页", async () => {
  const backend = pagedSignals();
  const {page, root, emit} = signalsHarness(backend);
  page.mount(root, {sources: singleSource});
  await settleSignals(page);
  assert.equal(page.state.items.length, 2);

  emit("[data-signal-more]");
  await settleSignals(page);
  emit("[data-signal-more]");
  await settleSignals(page);
  assert.equal(page.state.items.length, 6, "加载更多两次后应已加载 6 条");

  const target = page.state.items.find(item => item.signal_id === "signal-1");
  emit("[data-signal-action]", {id: target._key, signalAction: "retire"});
  emit("[data-signal-confirm]", {id: target._key, signalConfirm: "yes"});
  backend.retired.add("signal-1");
  await settleSignals(page);

  assert.deepEqual(page.state.items.map(item => item.signal_id).sort(),
    ["signal-2", "signal-3", "signal-4", "signal-5", "signal-6"],
    "停用一条规则后已加载的页必须保留，只少了被停用的那条");
  page.unmount();
});

test("规则库收到其他工作区的更新通知后不会把列表打回第一页", async () => {
  const backend = pagedSignals();
  const {page, root, emit, dispatch} = signalsHarness(backend);
  page.mount(root, {sources: singleSource});
  await settleSignals(page);
  emit("[data-signal-more]");
  await settleSignals(page);
  emit("[data-signal-more]");
  await settleSignals(page);
  assert.equal(page.state.items.length, 6);

  const before = backend.calls.length;
  dispatch("companion-expression-updated", {scopeKey: "scope-a"});
  await settleSignals(page);
  const refreshed = backend.calls.slice(before);

  assert.equal(page.state.items.length, 6, "跨工作区通知触发的刷新不得丢已加载的页");
  assert.ok(refreshed.some(call => call.query.after_id),
    "刷新应重读已加载的页（带 after_id），而不是只重读第一页");
  page.unmount();
});

test("规则库加载更多不会在页内堆叠重复行", async () => {
  const backend = pagedSignals(["signal-1", "signal-2", "signal-3"]);
  /* 让首页的 next_cursor 指回已加载的第一行，模拟后端窗口漂移：
     追加回来的那一页会重新包含 signal-1 / signal-2，不按 key 去重就会在页内堆出重复行。 */
  const original = backend.bridge.apiGet;
  backend.bridge.apiGet = async (route, query = {}) => {
    const value = await original(route, query);
    if (!query.after_id) value.data.next_cursor = "signal-1";
    return value;
  };
  const {page, root, emit} = signalsHarness(backend);
  page.mount(root, {sources: singleSource});
  await settleSignals(page);
  const rows = () => page._internals.rows()["source-a"].adopted;
  assert.equal(rows().items.length, 2, "首页两条");

  emit("[data-signal-more]");
  await settleSignals(page);
  assert.equal(rows().items.map(item => String(item.signal_id)).sort().join(","),
    "signal-1,signal-2,signal-3", "追加回来的页与已加载行重叠时，页内按 key 去重");
  assert.equal(page.state.items.length, 3);
  page.unmount();
});
test("全部来源都还没有可读取记录时不再声称「已显示其他来源」", async () => {
  const unavailable = () => ({status: "ok", data: {items: [], counts: {}, next_cursor: null,
    unavailable_reason: "role_binding_missing"}});
  const backend = {bridge: {apiGet: async () => unavailable(), apiPost: async () => ({status: "ok", data: {}})}};
  const {page, root} = signalsHarness(backend);
  page.mount(root, {sources: [
    {key: "source-a", scopeKey: "scope-a", label: "来源甲",
      replyApi: `/${HOST}/targets/profile-a/roles/role-a/reply`},
    {key: "source-b", scopeKey: "scope-b", label: "来源乙",
      replyApi: `/${HOST}/targets/profile-b/roles/role-b/reply`},
  ]});
  await settleSignals(page);

  assert.equal(page.state.notice, "全部来源都还没有可读取的表达学习记录。");
  assert.doesNotMatch(root.innerHTML, /已显示其他来源/);
  assert.doesNotMatch(root.innerHTML, /role_binding_missing(?![^<]*")/, "内部错误码不应出现在可见文本里");
  assert.match(root.innerHTML, /这个来源还没有绑定角色/);
  page.unmount();
});

test("规则库首次读取失败后重新读取会重试第 1 页", async () => {
  const backend = pagedSignals();
  const original = backend.bridge.apiGet;
  let failing = true;
  backend.bridge.apiGet = async (route, query = {}) => {
    if (failing) return {status: "error", data: {code: "bridge_timeout"}};
    return original(route, query);
  };
  const {page, root, emit} = signalsHarness(backend);
  page.mount(root, {sources: singleSource});
  await settleSignals(page);
  assert.equal(page.state.items.length, 0);

  failing = false;
  emit("[data-signal-refresh]");
  await settleSignals(page);
  assert.equal(page.state.items.length, 2, "首次失败后重新读取应重试第 1 页");
  page.unmount();
});

test("粘贴分享包后「预览导入」立即可用，清空回到灰色，提交只发一次", async () => {
  const SHARE_PACK = JSON.stringify({schema: "private-companion-expression-pack", version: 1,
    rule_groups: [{rules: [{kind: "style", label: "短称呼", pattern: "使用简短称呼"}]}]});
  const posts = [];
  const bridge = {
    apiGet: async () => ({status: "ok", data: {items: [], counts: {adopted: 0},
      capacity: {active: 0, limit: 480}, next_cursor: null}}),
    apiPost: async (route, body) => {
      posts.push({route, body});
      if (route.endsWith("/style/import/preview")) return {status: "ok", data: {title: "测试分享",
        importable_rule_count: 1, duplicate_rule_count: 0, capacity_blocked_count: 0, rejected_count: 0,
        preview_token: "a".repeat(64),
        candidates: [{dimension: "diction", observation: "短称呼", proposal: "使用简短称呼"}]}};
      if (route.endsWith("/style/import/apply")) return {status: "ok", data: {imported_count: 1, replayed: false}};
      return {status: "ok", data: {}};
    },
  };
  const dom = shareDom();
  const {page} = signalsHarness({bridge}, dom.root);
  page.mount(dom.root, {sources: singleSource});
  await settleSignals(page);

  dom.click("[data-share-toggle]");
  assert.equal(dom.button("[data-share-preview]").disabled, true, "刚打开分享面板时「预览导入」应是灰色");

  /* 粘贴（真实路径：input 事件 + 读渲染出的 disabled 属性） */
  dom.input(SHARE_PACK);
  assert.equal(dom.button("[data-share-preview]").disabled, false, "粘贴合法分享包后「预览导入」应立即可点");
  assert.equal(dom.button("[data-share-apply]").disabled, true, "还没预览时不能提交");

  /* 清空回灰 */
  dom.input("   ");
  assert.equal(dom.button("[data-share-preview]").disabled, true, "清空后「预览导入」应回到灰色");

  /* 再粘贴 → 真点一次 → 只发一次预览请求 */
  dom.input(SHARE_PACK);
  assert.equal(dom.click("[data-share-preview]"), true, "按钮可点时应能派发点击");
  for (let index = 0; index < 120 && dom.button("[data-share-apply]").disabled !== false; index++) {
    await flush();
  }
  assert.equal(posts.filter(call => call.route.endsWith("/style/import/preview")).length, 1,
    "点一次「预览导入」只应发一次预览请求");
  assert.equal(dom.button("[data-share-apply]").disabled, false, "预览成功后「加入待审队列」应可用");

  /* 提交一次，之后不应再发第二次 */
  assert.equal(dom.click("[data-share-apply]"), true);
  for (let index = 0; index < 120 && dom.button("[data-share-apply]").disabled !== true; index++) {
    await flush();
  }
  assert.equal(posts.filter(call => call.route.endsWith("/style/import/apply")).length, 1,
    "点一次「加入待审队列」只应发一次提交请求");
  assert.equal(dom.button("[data-share-apply]").disabled, true, "提交后按钮应回到不可点");
  assert.equal(dom.click("[data-share-apply]"), false, "不可点时不应派发第二次提交");
  assert.equal(posts.filter(call => call.route.endsWith("/style/import/apply")).length, 1);
  page.unmount();
});

/* 同一条规则被同一角色的两个窗口同时列出：全部来源只显示一张卡，但筛到任一来源都要能看到它。 */
test("同一条规则被两个来源同时列出时，筛到第二个来源仍能看到它", async () => {
  const bridge = {
    apiGet: async route => {
      const own = route.includes("profile-a") ? "signal-a" : "signal-b";
      return {status: "ok", data: {items: [SIGNAL_RULE("signal-shared"), SIGNAL_RULE(own)],
        counts: {adopted: 2}, capacity: {active: 2, limit: 480}, next_cursor: null}};
    },
    apiPost: async () => ({status: "ok", data: {}}),
  };
  const {page, root, change} = signalsHarness({bridge});
  page.mount(root, {sources: [
    {key: "source-a", scopeKey: "scope-a", label: "来源甲",
      replyApi: `/${HOST}/targets/profile-a/roles/role-a/reply`},
    {key: "source-b", scopeKey: "scope-b", label: "来源乙",
      replyApi: `/${HOST}/targets/profile-b/roles/role-b/reply`},
  ]});
  await settleSignals(page);
  const cards = () => (root.innerHTML.match(/data-signal-action="retire"/g) || []).length;
  assert.equal(cards(), 3, "全部来源时同一条规则只显示一张卡");

  change({closest: probe => probe === "[data-rule-source-filter]" ? {value: "source-b"} : null});
  assert.equal(cards(), 2, "筛到来源乙时，共享规则与来源乙自己的规则都应显示");
  assert.match(root.innerHTML, /说法 signal-shared/);
  assert.doesNotMatch(root.innerHTML, /当前筛选下没有表达规则/);
  page.unmount();
});

/* 刷新时链中某一页失败：已显示的后页不能被永久踢出刷新范围。 */
test("刷新中途某页失败后，已加载的后页仍会继续刷新", async () => {
  const backend = pagedSignals();
  const original = backend.bridge.apiGet;
  let failingCursor = null;
  backend.bridge.apiGet = async (route, query = {}) => {
    if (query.after_id && query.after_id === failingCursor) throw {code: "bridge_timeout"};
    return original(route, query);
  };
  const {page, root, emit} = signalsHarness(backend);
  page.mount(root, {sources: singleSource});
  await settleSignals(page);
  emit("[data-signal-more]");
  await settleSignals(page);
  emit("[data-signal-more]");
  await settleSignals(page);
  assert.equal(page.state.items.length, 6, "先加载三页");

  failingCursor = "signal-2";
  emit("[data-signal-refresh]");
  await settleSignals(page);
  assert.ok(backend.calls.some(call => call.query.after_id === "signal-2"), "第二页确实失败过");

  failingCursor = null;
  const mark = backend.calls.length;
  emit("[data-signal-refresh]");
  await settleSignals(page);
  const cursors = backend.calls.slice(mark).map(call => call.query.after_id || "").sort();
  assert.deepEqual(cursors, ["", "signal-2", "signal-4"],
    "失败过的页与它后面的页都要继续刷新，不能只留在成功前缀上");
  page.unmount();
});

function signalsPage(bridge) {
  const root = {isConnected: true, innerHTML: "", listeners: new Map(),
    addEventListener(name, callback) { this.listeners.set(name, callback); },
    removeEventListener(name) { this.listeners.delete(name); },
    querySelector: () => null};
  const window = {CompanionProduct: {bridgeNow: () => bridge, unwrap: value => value},
    lucide: {createIcons() {}}};
  const context = vm.createContext({window, setTimeout, clearTimeout, TextEncoder, JSON, Date, Math,
    Number, Object, Array, String, URLSearchParams});
  vm.runInContext(source, context);
  return {root, page: context.window.CompanionExpressionSignalsPage};
}

const settle = async page => {
  for (let index = 0; index < 40 && page.state.busy; index++) await flush();
};

const unavailableRule = (state, signalId, actions) => ({signal_id: signalId, run_id: "run-1",
  dimension: "diction", source_kind: "conversation_input", state,
  observation: "群里习惯把话说完再收尾", proposal: "用一句完整的话收尾",
  expression: {kind: "style", situation: "群里聊到熟悉的话题时", pattern: "一句话说完",
    avoid: "认真求助时不用"},
  applicability: {channels: ["group"], relationship_stages: ["any"], emotion_gates: ["any"],
    intent: "any", keywords: []},
  created_at: "2026-10-08T10:00:00+00:00", injections: 0, can_revise: false,
  evidence: [], unavailable_reason: "expression_sample_changed", actions, view_token: "token"});

test("依据原文不可读时保留规则正文并按状态说明还能做什么", async () => {
  const items = {
    adopted: [unavailableRule("adopted", "signal-adopted", ["retire"])],
    retired: [unavailableRule("retired", "signal-retired", [])],
    pending: [unavailableRule("pending", "signal-pending", ["reject"])],
  };
  const bridge = {
    apiGet: async (route, query) => ({status: "ok", data: {items: items[query?.state] || [],
      capacity: {active: 1, limit: 60}, counts: {}}}),
    apiPost: async () => ({status: "ok", data: {}}),
  };
  const {root, page} = signalsPage(bridge);
  await page.mount(root, {sources: [{key: "source-a", scopeKey: "scope-a", label: "私聊来源",
    replyApi: `/${HOST}/targets/profile-a/roles/role-a/reply`}]});
  await settle(page);
  const html = () => root.innerHTML;
  // 已启用：正文与用法都保留，停用不受影响，也不再出现错误的失效承诺。
  assert.match(html(), /一句话说完/);
  assert.match(html(), /群里聊到熟悉的话题时/);
  assert.match(html(), /用一句完整的话收尾/);
  assert.match(html(), /依据原文不可读取，不影响这条规则继续生效，仍可停用。/);
  assert.match(html(), /data-signal-action="retire"/);
  assert.doesNotMatch(html(), /data-signal-edit/);
  assert.doesNotMatch(html(), /依据已失效|暂不可恢复/);
  assert.doesNotMatch(html(), /expression_sample_changed|lh-sample-changed/);
  const clickState = async key => {
    root.listeners.get("click")({target: {closest: selector => selector === "[data-signal-state]"
      ? {dataset: {signalState: key}} : null}});
    await settle(page);
  };
  // 已停用：说明无法恢复启用，也不再给恢复按钮。
  await clickState("retired");
  assert.match(html(), /依据原文不可读取，无法恢复启用。/);
  assert.match(html(), /一句话说完/);
  assert.doesNotMatch(html(), /data-signal-action="restore"/);
  // 待审：只能拒绝，不能采纳一条无法复核依据的候选。
  await clickState("pending");
  assert.match(html(), /依据原文不可读取，这条候选只能拒绝。/);
  assert.match(html(), /data-signal-action="reject"/);
  assert.doesNotMatch(html(), /data-signal-action="adopt"/);
  page.unmount();
});

test("早于上下文重置的依据仍可展开并标注为重置前的记录", async () => {
  // 合成时间戳：夹具不得带入任何真实重置时间。
  const resetAt = "2026-10-31T12:00:00+00:00";
  const bridge = {
    apiGet: async () => ({status: "ok", data: {items: [{signal_id: "signal-reset", run_id: "run-1",
      dimension: "diction", source_kind: "conversation_input", state: "adopted",
      observation: "群里习惯把话说完再收尾", proposal: "用一句完整的话收尾",
      expression: {kind: "style", situation: "群里聊到熟悉的话题时", pattern: "一句话说完", avoid: ""},
      applicability: {channels: ["group"], relationship_stages: ["any"], emotion_gates: ["any"],
        intent: "any", keywords: []},
      created_at: "2026-10-08T10:00:00+00:00", injections: 3, can_revise: true,
      evidence: ["嗯哼，这样的说法很轻松"],
      evidence_reset_at: resetAt, actions: ["retire"], view_token: "token"}],
      capacity: {active: 1, limit: 60}, counts: {adopted: 1}}}),
    apiPost: async () => ({status: "ok", data: {}}),
  };
  const {root, page} = signalsPage(bridge);
  await page.mount(root, {sources: [{key: "source-a", scopeKey: "scope-a", label: "私聊来源",
    replyApi: `/${HOST}/targets/profile-a/roles/role-a/reply`}]});
  await settle(page);
  const shown = new Date(resetAt).toLocaleString("zh-CN",
    {hour12: false, month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit"});
  assert.match(root.innerHTML, /嗯哼，这样的说法很轻松/);
  assert.ok(root.innerHTML.includes(`依据早于 ${shown} 的上下文重置，原消息仍可读取。`));
  assert.match(root.innerHTML, /data-signal-edit/);
  assert.doesNotMatch(root.innerHTML, /依据已失效|lh-signal-unavailable/);
  page.unmount();
});

/* ---------------------------------------------------------------------------
 * 轮换规则：容量行按「另有 N 条仅原会话窗口」显示，卡片按 injectable 说明是否生效。
 * 这组用例来自 fix/expression-dialogue-rule-scope，随该分支一起变基到新主线。
 * ------------------------------------------------------------------------- */

function rotatedMountPage(bridge) {
  const listeners = new Map();
  const root = {isConnected: true, innerHTML: "", querySelector: () => null,
    addEventListener: (name, callback) => listeners.set(name, callback),
    removeEventListener: name => listeners.delete(name)};
  const window = {CompanionProduct: {bridgeNow: () => bridge, unwrap: value => value},
    lucide: {createIcons() {}}};
  const context = vm.createContext({window, setTimeout, clearTimeout, TextEncoder, JSON, Date, Math,
    Number, Object, Array, String, URLSearchParams});
  vm.runInContext(source, context);
  const page = context.window.CompanionExpressionSignalsPage;
  return {root, page, listeners};
}
async function rotatedSettle(page, root, options) {
  await page.mount(root, options);
  for (let index = 0; index < 50 && page.state.busy; index++) await flush();
}
const rotatedSingleSource = [{key: "source-a", scopeKey: "scope-a", label: "测试来源",
  replyApi: `/${HOST}/targets/profile-a/roles/role-a/reply`}];
async function rotatedMountWithData(data, sources = rotatedSingleSource) {
  const bridge = {apiGet: async () => ({status: "ok", data}),
    apiPost: async () => ({status: "ok", data: {}})};
  const {root, page} = rotatedMountPage(bridge);
  await rotatedSettle(page, root, {sources});
  return {root, page};
}

test("轮换规则的容量行使用另有计数，说明不再注入且不承诺释放容量", async () => {
  const rotated = {
    signal_id: "signal-rotated", run_id: "run-rotated", dimension: "diction",
    source_kind: "conversation_input", observation: "群里接话时", proposal: "懂的都懂",
    state: "adopted", created_at: "2026-10-08T10:00:00+00:00", actions: ["retire"],
    injections: 0, can_revise: false, injectable: false,
    expression: {kind: "style", situation: "群里接话时", pattern: "懂的都懂", avoid: "认真求助时不用"},
    evidence: [], unavailable_reason: "expression_rule_window_rotated", view_token: "token-rotated",
  };
  const {root, page} = await rotatedMountWithData({
    items: [rotated], next_cursor: null,
    capacity: {active: 0, limit: 60}, counts: {adopted: 0}, rotated_rules: 1,
  });

  assert.equal(page.state.capacity.active, 0);
  assert.match(root.innerHTML, /待审与启用 0 \/ 60 · 另有 1 条仅原会话窗口/);
  assert.doesNotMatch(root.innerHTML, /其中 1 条/);
  assert.match(root.innerHTML, /已启用<span>0<\/span>/,
    "the enabled tab counts the same injectable set as the capacity");
  assert.match(root.innerHTML, /已启用 · 仅原会话窗口/);
  assert.match(root.innerHTML, /该会话窗口已轮换，之后不再注入；停用后无法恢复。/);
  assert.doesNotMatch(root.innerHTML, /停用可释放容量/,
    "retiring a rotated rule does not free capacity because it never occupied one");
  assert.doesNotMatch(root.innerHTML, /不影响这条规则继续生效/);
  assert.match(root.innerHTML, /懂的都懂/,
    "a rotated rule keeps its reviewed form and does not fall back to 依据已失效");
  assert.doesNotMatch(root.innerHTML, /依据已失效/);
  page.unmount();
});

test("依据不可读但可注入的规则保留正文和继续生效说明", async () => {
  const {root, page} = await rotatedMountWithData({
    items: [{
      signal_id: "signal-injectable", run_id: "run-injectable", dimension: "diction",
      source_kind: "shared_pack", observation: "私下接话时", proposal: "用原来如此呀接话",
      state: "adopted", created_at: "2026-10-08T10:00:00+00:00", actions: ["retire"],
      injections: 1, can_revise: false, injectable: true,
      expression: {kind: "style", situation: "私下接话时", pattern: "原来如此呀", avoid: ""},
      evidence: [], unavailable_reason: "expression_sample_changed",
      view_token: "token-injectable",
    }],
    next_cursor: null, capacity: {active: 1, limit: 60}, counts: {adopted: 1}, rotated_rules: 0,
  });

  assert.match(root.innerHTML, /依据原文不可读取，不影响这条规则继续生效，仍可停用。/,
    "an injectable rule keeps the existing promise");
  assert.match(root.innerHTML, /原来如此呀/, "the reviewed body stays visible");
  assert.doesNotMatch(root.innerHTML, /依据已失效/);
  assert.doesNotMatch(root.innerHTML, /不会注入/);
  page.unmount();
});

test("依据不可读且不可注入的规则保留正文但明确说明不会注入", async () => {
  const {root, page} = await rotatedMountWithData({
    items: [{
      signal_id: "signal-revoked", run_id: "run-revoked", dimension: "diction",
      source_kind: "conversation_input", observation: "群里接话时", proposal: "懂的都懂",
      state: "adopted", created_at: "2026-10-08T10:00:00+00:00", actions: ["retire"],
      injections: 0, can_revise: false, injectable: false,
      expression: {kind: "style", situation: "群里接话时", pattern: "懂的都懂", avoid: ""},
      evidence: [], unavailable_reason: "expression_rule_review_revoked",
      view_token: "token-revoked",
    }],
    next_cursor: null, capacity: {active: 0, limit: 60}, counts: {adopted: 0}, rotated_rules: 0,
  });

  assert.doesNotMatch(root.innerHTML, /不影响这条规则继续生效/,
    "a rule that will not be injected must not claim that it still takes effect");
  assert.match(root.innerHTML, /懂的都懂/, "the reviewed body stays visible");
  assert.match(root.innerHTML, /依据原文不可读取，这条规则不会注入；可停用。/);
  assert.match(root.innerHTML, /data-signal-action="retire"/,
    "the card still offers the action that is actually available");
  page.unmount();
});

test("依据可读但不可注入的已启用规则不再声称继续生效", async () => {
  const {root, page} = await rotatedMountWithData({
    items: [{
      signal_id: "signal-readable-blocked", run_id: "run-readable-blocked", dimension: "diction",
      source_kind: "conversation_input", observation: "群里接话时", proposal: "懂的都懂",
      state: "adopted", created_at: "2026-10-08T10:00:00+00:00", actions: ["retire"],
      injections: 0, can_revise: true, injectable: false,
      expression: {kind: "style", situation: "群里接话时", pattern: "懂的都懂", avoid: ""},
      evidence: ["原会话里的那句接话"], unavailable_reason: null, view_token: "token-readable-blocked",
    }],
    next_cursor: null, capacity: {active: 0, limit: 60}, counts: {adopted: 0}, rotated_rules: 0,
  });

  assert.match(root.innerHTML, /懂的都懂/, "the readable body stays visible");
  assert.doesNotMatch(root.innerHTML, /继续生效/);
  assert.match(root.innerHTML, /这条规则在当前会话不会注入；可停用。/);
  page.unmount();
});

test("多来源重复列出的轮换规则按 signal_id 只计一次", async () => {
  const rotated = {
    signal_id: "signal-shared", run_id: "run-shared", dimension: "diction",
    source_kind: "conversation_input", observation: "群里接话时", proposal: "懂的都懂",
    state: "adopted", created_at: "2026-10-08T10:00:00+00:00", actions: ["retire"],
    injections: 0, can_revise: false, injectable: false,
    expression: {kind: "style", situation: "群里接话时", pattern: "懂的都懂", avoid: ""},
    evidence: [], unavailable_reason: "expression_rule_window_rotated", view_token: "token-shared",
  };
  const bridge = {
    apiGet: async () => ({status: "ok", data: {
      items: [rotated], next_cursor: null,
      capacity: {active: 0, limit: 30}, counts: {adopted: 0}, rotated_rules: 1,
    }}),
    apiPost: async () => ({status: "ok", data: {}}),
  };
  const {root, page, listeners} = rotatedMountPage(bridge);
  await rotatedSettle(page, root, {sources: [
    {key: "source-a", scopeKey: "scope-a", label: "来源甲",
      replyApi: `/${HOST}/targets/profile-a/roles/role-a/reply`},
    {key: "source-b", scopeKey: "scope-b", label: "来源乙",
      replyApi: `/${HOST}/targets/profile-b/roles/role-b/reply`},
  ]});

  assert.match(root.innerHTML, /另有 1 条仅原会话窗口/,
    "the same signal listed by two sources is one rotated rule");
  assert.doesNotMatch(root.innerHTML, /另有 2 条/);
  assert.doesNotMatch(root.innerHTML, /其中 1 条/);
  assert.equal((root.innerHTML.match(/<article class="lh-signal-card /g) || []).length, 1,
    "the duplicate card is folded into one, matching the deduped count");
  listeners.get("change")({target: {closest: selector =>
    selector === "[data-rule-source-filter]" ? {value: "source-b"} : null}});
  assert.match(root.innerHTML, /记录来源 · 来源乙/,
    "filtering to one source keeps that source's own copy");
  page.unmount();
});
