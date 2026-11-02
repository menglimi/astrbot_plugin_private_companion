"use strict";
/* 学习列表的分页保活回归：审阅中心（learning-review-page.js）。
 *
 * 覆盖两个用户可见缺陷：
 *   ① 裁决/批量裁决成功后会整表重读，已「加载更多」出来的页被丢掉；
 *   ② 同一次重读把「当前选中记录」与「已勾选记录」清空。
 * 期望行为：除用户主动切换筛选外，任何刷新都不减少已加载的内容；选中/勾选按 key 保留
 * （选中项已消失时回退到第一条）。
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "pages", "陪伴小屋",
  "learning-review-page.js"), "utf8");
const HOST = "astrbot_plugin_private_companion";
const PREFIX = `/${HOST}/`;
const flush = () => new Promise(resolve => setTimeout(resolve, 0));

const progressReview = id => ({review_id: id, growth_title: "目标 " + id, progress_note: "进展 " + id,
  actions: ["accept", "reject"], base_revision: 1, view_token: "token-" + id,
  created_at: "2026-10-08T10:00:00+00:00", unavailable_reason: null, evidence: []});

/* 按 review_id 升序、每页 2 条、after_id 是上一页最后一条 review_id 的假后端。 */
function progressPages(config = {}) {
  const decided = new Set(config.decided || []);
  const all = config.ids || ["r1", "r2", "r3", "r4", "r5", "r6"];
  const size = config.size || 2;
  const pending = () => all.filter(id => !decided.has(id));
  const at = cursor => {
    const list = pending();
    const index = cursor ? list.findIndex(id => id === cursor) : -1;
    const start = index + 1;
    const items = list.slice(start, start + size).map(progressReview);
    const next = start + items.length < list.length ? items[items.length - 1].review_id : null;
    return {status: "ok", data: {items, next_cursor: next}};
  };
  return {at, decided};
}

function harness(backend) {
  const bridge = {
    apiGet: async (route, body = {}) => (route.endsWith("/progress")
      ? backend.at(body.after_id || "")
      : {status: "ok", data: {items: [], next_cursor: null}}),
    apiPost: async () => ({status: "ok", data: {}}),
  };
  const listeners = new Map();
  const root = {isConnected: true, innerHTML: "",
    addEventListener(name, callback) {listeners.set(name, callback);},
    removeEventListener(name) {listeners.delete(name);},
    querySelector: () => null};
  const window = {CompanionProduct: {bridgeNow: () => bridge, unwrap: value => value},
    lucide: {createIcons() {}}};
  const context = vm.createContext({window, setTimeout, clearTimeout, Date, Math, Number, Object,
    Array, String, Set, Map});
  vm.runInContext(source, context);
  return {page: context.window.CompanionLearningReviewPage, root,
    emit: (type, event) => listeners.get(type)?.(event)};
}

const mountOptions = {sources: [{key: "role-a", label: "星缘 · 私聊",
  replyApi: `${PREFIX}targets/profile-a/roles/role-a/reply`,
  growthApi: `${PREFIX}targets/profile-a/roles/role-a/growth`}]};
const click = (emit, selector, dataset) => emit("click", {target: {closest: probe =>
  probe === selector ? {dataset} : null}});
const settle = async predicate => {
  for (let attempt = 0; attempt < 200 && !predicate(); attempt += 1) await flush();
};
const loadMore = async (page, emit) => {
  const before = page.state.items.length;
  click(emit, "[data-review-more]");
  await settle(() => !page.state.loading && page.state.items.length > before);
};

test("审阅中心裁决后保留已加载的页", async () => {
  const backend = progressPages();
  const {page, root, emit} = harness(backend);
  page.mount(root, {...mountOptions});
  await settle(() => !page.state.loading);
  assert.equal(page.state.items.length, 2);

  await loadMore(page, emit);
  await loadMore(page, emit);
  assert.equal(page.state.items.length, 6, "加载更多两次后应已加载 6 条");

  const target = page.state.items.find(item => item.review_id === "r5");
  assert.ok(target, "应能选中第 5 条");
  /* 详情面板的「确认」作用于当前选中的那条，所以先选中再确认。 */
  click(emit, "[data-review-select]", {reviewSelect: target._key});
  click(emit, "[data-review-stage]", {reviewKey: target._key, reviewStage: "accept"});
  click(emit, "[data-review-confirm]", {reviewConfirm: "yes"});
  backend.decided.add("r5");
  await settle(() => !page.state.loading && !page.state.writing);

  assert.deepEqual(page.state.items.map(item => item.review_id).sort(),
    ["r1", "r2", "r3", "r4", "r6"], "裁决一条后已加载的页必须保留，只少了被裁决的那条");
  page.unmount();
});

test("审阅中心刷新后保留选中与勾选", async () => {
  const backend = progressPages();
  const {page, root, emit} = harness(backend);
  page.mount(root, {...mountOptions});
  await settle(() => !page.state.loading);
  await loadMore(page, emit);
  await loadMore(page, emit);

  const selected = page.state.items.find(item => item.review_id === "r3");
  const checked = page.state.items.find(item => item.review_id === "r6");
  click(emit, "[data-review-select]", {reviewSelect: selected._key});
  emit("change", {target: {checked: true, closest: probe =>
    probe === "[data-review-check]" ? {checked: true, dataset: {reviewCheck: checked._key}} : null}});
  assert.equal(page.state.selected, selected._key);
  assert.equal(page.state.checked.has(checked._key), true);

  /* 「重新读取」是最常见的刷新路径：既不能丢已加载页，也不能清掉选中与勾选。 */
  click(emit, "[data-review-refresh]");
  await settle(() => !page.state.loading);

  assert.equal(page.state.items.length, 6, "刷新不得丢已加载的页");
  assert.equal(page.state.selected, selected._key, "刷新后仍应停在用户选中的那条");
  assert.equal(page.state.checked.has(checked._key), true, "刷新后已勾选的记录不该被清空");
  page.unmount();
});

test("审阅中心刷新失败时保留已显示的内容", async () => {
  const backend = progressPages();
  let failing = false;
  const {page, root, emit} = harness({at: cursor => (failing
    ? {status: "error", data: {code: "bridge_timeout"}} : backend.at(cursor))});
  page.mount(root, {...mountOptions});
  await settle(() => !page.state.loading);
  await loadMore(page, emit);
  assert.equal(page.state.items.length, 4);

  failing = true;
  click(emit, "[data-review-refresh]");
  await settle(() => !page.state.loading);
  assert.equal(page.state.items.length, 4, "读取失败时不得清空已经显示的记录");
  page.unmount();
});

/* 防线用例（改前也绿）：刷新必须能重试「一直没读成功」的第 1 页。
   read 的 refresh 模式如果只看 loaded=true 的页，首次读取失败后点「重新读取」会什么都不做。 */
test("审阅中心首次读取失败后重新读取会重试第一页", async () => {
  const backend = progressPages();
  let failing = true;
  const {page, root, emit} = harness({at: cursor => (failing
    ? {status: "error", data: {code: "bridge_timeout"}} : backend.at(cursor))});
  page.mount(root, {...mountOptions});
  await settle(() => !page.state.loading);
  assert.equal(page.state.items.length, 0);

  failing = false;
  click(emit, "[data-review-refresh]");
  await settle(() => !page.state.loading);
  assert.equal(page.state.items.length, 2, "首次失败后重新读取应重试第一页");
  page.unmount();
});

/* 裁决期间有刷新在途：那份旧响应不能把刚裁决掉的行 concat 回队列。 */
test("裁决期间的刷新不会把刚裁决的行带回队列", async () => {
  const backend = progressPages({ids: ["r1", "r2", "r3", "r4"], size: 2});
  let gate = null;
  const {page, root, emit} = harness({at: cursor => {
    if (!gate) return backend.at(cursor);
    const current = gate;
    gate = null;
    return current.promise.then(() => current.payload);
  }});
  page.mount(root, {...mountOptions});
  await settle(() => !page.state.loading);
  assert.deepEqual(page.state.items.map(item => item.review_id), ["r1", "r2"]);

  /* 一次「重新读取」在途：它的响应是裁决前的快照，里面还有 r1。 */
  const pending = {payload: {status: "ok", data: {items: [progressReview("r1"), progressReview("r2")],
    next_cursor: "r2"}}};
  pending.promise = new Promise(resolve => {pending.release = resolve;});
  gate = pending;
  click(emit, "[data-review-refresh]");
  await settle(() => page.state.loading === true);
  assert.equal(page.state.loading, true, "这次刷新应该在途");

  const target = page.state.items.find(item => item.review_id === "r1");
  click(emit, "[data-review-select]", {reviewSelect: target._key});
  click(emit, "[data-review-stage]", {reviewKey: target._key, reviewStage: "accept"});
  click(emit, "[data-review-confirm]", {reviewConfirm: "yes"});
  backend.decided.add("r1");
  for (let index = 0; index < 200 && page.state.writing; index += 1) await flush();
  assert.equal(page.state.items.some(item => item.review_id === "r1"), false, "裁决后 r1 应离开队列");

  /* 放行那次在途读取的旧响应：它不得把 r1 带回来。 */
  pending.release();
  for (let index = 0; index < 200; index += 1) await flush();
  assert.equal(page.state.items.some(item => item.review_id === "r1"), false,
    "在途刷新返回的旧快照不得把已裁决的行 concat 回队列");
  page.unmount();
});
