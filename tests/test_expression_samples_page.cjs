"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "pages", "陪伴小屋", "expression-samples-page.js"), "utf8");
const HOST = "astrbot_plugin_private_companion";
const flush = () => new Promise(resolve => setTimeout(resolve, 0));

const sourceOf = (key, label, account) => ({key, scopeKey: "scope-" + key, label,
  replyApi: `/${HOST}/targets/profile-${account}/roles/role-${account}/reply`});

const sample = (handle, text) => ({handle, text, sender_id: "member-a",
  received_at: "2026-10-08T10:00:00+00:00", state: "pending", actions: ["approve", "reject"],
  eligible: true, view_token: "token"});

const runtimeOf = (changes = {}) => ({last_result: null, running: false, learning_enabled: true,
  application_enabled: true, minimum_samples: 8, eligible_samples: 1, new_messages: 3,
  required_new_messages: 2, group_batches_today: 1, group_daily_batch_limit: 4, ...changes});

const readable = items => ({status: "ok", data: {items, counts: {pending: items.length, approved: 0, rejected: 0},
  sample_review_enabled: false, next_cursor: null, runtime: runtimeOf()}});

/* The backend answers a group without a readable session with an empty page,
   a stable zeroed runtime block and the reason it is empty. */
const empty = reason => ({status: "ok", data: {items: [], counts: {}, next_cursor: null,
  unavailable_reason: reason, sample_review_enabled: false, runtime: runtimeOf({
    learning_enabled: false, application_enabled: false, minimum_samples: 0, eligible_samples: 0,
    new_messages: null, required_new_messages: null, group_batches_today: 0,
    group_daily_batch_limit: 0})}});

async function mount(bridge, sources) {
  const listeners = {};
  const root = {isConnected: true, innerHTML: "",
    addEventListener(type, handler) {listeners[type] = handler;},
    removeEventListener() {}, querySelector: () => null};
  const window = {CompanionProduct: {bridgeNow: () => bridge, unwrap: value => value}};
  const context = vm.createContext({window, setTimeout, clearTimeout, JSON, Date, Math, Number,
    Object, Array, String});
  vm.runInContext(source, context);
  const page = context.window.CompanionExpressionSamplesPage;
  await page.mount(root, {sources});
  for (let index = 0; index < 40 && page.state.busy; index++) await flush();
  return {page, root, click: event => listeners.click?.(event)};
}

const moreButton = () => ({target: {closest: selector => selector === "[data-sample-more]" ? {} : null}});

test("读取失败与暂时没有素材分开提示，失败来源保留错误码", async () => {
  const bridge = {
    apiGet: async route => {
      if (route.includes("profile-b")) {
        throw Object.assign(new Error("expression_group_context_required"),
          {code: "expression_group_context_required"});
      }
      if (route.includes("profile-c")) return empty("role_binding_missing");
      return readable([sample("input:a", "来源甲的消息素材")]);
    },
  };
  const {page, root} = await mount(bridge, [
    sourceOf("source-a", "来源甲", "a"),
    sourceOf("source-b", "来源乙", "b"),
    sourceOf("source-c", "来源丙", "c"),
  ]);
  assert.equal(page.state.error, true);
  assert.equal(page.state.notice, "1 个来源读取失败、1 个来源暂无素材，已显示其余来源。");
  assert.equal(page.state.items.length, 1);
  assert.match(root.innerHTML, /来源甲的消息素材/);
  assert.match(root.innerHTML, /lh-inline-status is-error/);
  assert.match(root.innerHTML, /1 个来源读取失败、1 个来源暂无素材，已显示其余来源。/);
  assert.match(root.innerHTML, /查看读取明细 · 2 个来源/);
  assert.match(root.innerHTML, /data-code="expression_group_context_required"/);
  assert.match(root.innerHTML, /data-code="role_binding_missing"/);
  assert.match(root.innerHTML, /这个来源还没有可用于表达学习的对话/);
  assert.match(root.innerHTML, /这个来源还没有绑定角色/);
  assert.match(root.innerHTML, /来源乙/);
  assert.match(root.innerHTML, /来源丙/);
  assert.doesNotMatch(root.innerHTML, /记录归属/);
  assert.doesNotMatch(root.innerHTML, /部分记录归属暂不可读/);
  page.unmount();
});

test("来源暂时没有会话时作为中性说明，不把页面标红", async () => {
  const bridge = {
    apiGet: async route => route.includes("profile-c") ? empty("role_binding_missing")
      : readable([sample("input:a", "私聊来源的素材")]),
  };
  const {page, root} = await mount(bridge, [
    sourceOf("source-a", "私聊来源", "a"),
    sourceOf("source-c", "群聊来源", "c"),
  ]);
  assert.equal(page.state.error, false);
  assert.equal(page.state.notice, "1 个来源还没有可读取的素材。");
  assert.equal(page.state.available, true);
  assert.match(root.innerHTML, /私聊来源的素材/);
  assert.match(root.innerHTML, /群聊来源/);
  assert.match(root.innerHTML, /1 个来源还没有可读取的素材。/);
  assert.match(root.innerHTML, /这个来源还没有绑定角色/);
  assert.match(root.innerHTML, /data-code="role_binding_missing"/);
  assert.doesNotMatch(root.innerHTML, /lh-inline-status is-error/);
  assert.doesNotMatch(root.innerHTML, /记录归属/);
  page.unmount();
});

test("全部来源都还没有会话时仍是空页而不是失败", async () => {
  const bridge = {
    apiGet: async route => empty(route.includes("profile-b") ? "expression_group_context_required"
      : "role_binding_missing"),
  };
  const {page, root} = await mount(bridge, [
    sourceOf("source-a", "来源甲", "a"),
    sourceOf("source-b", "来源乙", "b"),
  ]);
  assert.equal(page.state.error, false);
  assert.equal(page.state.notice, "2 个来源还没有可读取的素材。");
  assert.equal(page.state.items.length, 0);
  assert.equal(page.state.available, false);
  assert.match(root.innerHTML, /暂无可读来源/);
  assert.match(root.innerHTML, /还没有可读取的素材/);
  assert.match(root.innerHTML, /data-sample-learn disabled/);
  assert.doesNotMatch(root.innerHTML, /lh-inline-status is-error/);
  page.unmount();
});

test("全部来源读取失败时提示重新读取而不是汇总空页", async () => {
  const bridge = {
    apiGet: async () => {
      throw Object.assign(new Error("bridge_timeout"), {code: "bridge_timeout"});
    },
  };
  const {page, root} = await mount(bridge, [
    sourceOf("source-a", "来源甲", "a"),
    sourceOf("source-b", "来源乙", "b"),
  ]);
  assert.equal(page.state.error, true);
  assert.equal(page.state.notice, "全部来源读取失败，请检查来源配置后重新读取。");
  assert.match(root.innerHTML, /lh-inline-status is-error/);
  assert.match(root.innerHTML, /data-code="bridge_timeout"/);
  assert.doesNotMatch(root.innerHTML, /记录归属/);
  page.unmount();
});

test("加载更多遇到来源转为空页时保留已显示的素材与游标", async () => {
  const first = readable([sample("input:a", "第一页素材")]);
  first.data.next_cursor = "cursor-1";
  const bridge = {
    /* The first page is readable; the next page of the same source comes back
       as an empty page because the group lost its session in between. */
    apiGet: async (route, body) => body?.after_id ? empty("role_binding_missing") : first,
  };
  const {page, root, click} = await mount(bridge, [sourceOf("source-a", "来源甲", "a")]);
  assert.equal(page.state.items.length, 1);
  assert.equal(page.state.counts.pending, 1);
  assert.equal(page.state.error, false);
  click(moreButton());
  for (let index = 0; index < 40 && page.state.busy; index++) await flush();
  /* "Load more" never retracts a row the reader already sees: the items,
     counts and cursor survive, and only the read detail reports the empty page. */
  assert.equal(page.state.items.length, 1);
  assert.equal(page.state.counts.pending, 1);
  assert.equal(page.state.notice, "1 个来源还没有可读取的素材。");
  assert.equal(page.state.error, false);
  assert.match(root.innerHTML, /第一页素材/);
  assert.match(root.innerHTML, /data-sample-more/);
  assert.match(root.innerHTML, /data-code="role_binding_missing"/);
  assert.doesNotMatch(root.innerHTML, /lh-inline-status is-error/);
  page.unmount();
});
