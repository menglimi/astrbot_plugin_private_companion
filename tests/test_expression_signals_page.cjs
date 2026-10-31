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
