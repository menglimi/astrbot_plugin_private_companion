"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "pages", "陪伴小屋", "expression-samples-page.js"), "utf8");
const HOST = "astrbot_plugin_private_companion";
const BASE_TIME = Date.parse("2026-10-08T10:00:00+00:00");
const CARD_HEIGHT = 120;
const flush = () => new Promise(resolve => setTimeout(resolve, 0));

const sourceOf = (key, label, account) => ({key, scopeKey: "scope-" + key, label,
  replyApi: `/${HOST}/targets/profile-${account}/roles/role-${account}/reply`});

/* index 越大越旧：素材按接收时间倒序展示，与后端分页的排序一致。 */
const sample = (handle, text, index = 0) => ({handle, text, sender_id: "member-a",
  received_at: new Date(BASE_TIME - index * 60000).toISOString(), state: "pending",
  actions: ["approve", "reject"], eligible: true, view_token: "token"});

const runtimeOf = (changes = {}) => ({last_result: null, running: false, learning_enabled: true,
  application_enabled: true, minimum_samples: 8, eligible_samples: 1, new_messages: 3,
  required_new_messages: 2, group_batches_today: 1, group_daily_batch_limit: 4, ...changes});

const readable = items => ({status: "ok", data: {items, counts: {pending: items.length, approved: 0, rejected: 0},
  sample_review_enabled: false, next_cursor: null, runtime: runtimeOf()}});

/* 一页素材：next 是后端返回的下一页游标（最后一页为 null）。 */
const page = (items, next = null, runtime = {}) => ({status: "ok", data: {items,
  counts: {pending: items.length, approved: 0, rejected: 0}, sample_review_enabled: false,
  next_cursor: next, runtime: runtimeOf(runtime)}});

/* The backend answers a group without a readable session with an empty page,
   a stable zeroed runtime block and the reason it is empty. */
const empty = reason => ({status: "ok", data: {items: [], counts: {}, next_cursor: null,
  unavailable_reason: reason, sample_review_enabled: false, runtime: runtimeOf({
    learning_enabled: false, application_enabled: false, minimum_samples: 0, eligible_samples: 0,
    new_messages: null, required_new_messages: null, group_batches_today: 0,
    group_daily_batch_limit: 0})}});

const ARTICLES = /<article class="lh-sample" data-row-key="([^"]*)"[\s\S]*?<\/article>/g;

/* 轻量 DOM 夹具：只实现页面真正用到的那一点——innerHTML、事件委托、卡片的
   `<details>` 展开状态与焦点、以及每张卡在文档里的纵向位置（卡片等高）。
   这样「重绘后阅读位置与展开状态是否保住」可以在 node --test 里断言，不必拉浏览器。 */
function createDom() {
  const listeners = new Map();
  const timers = new Map();
  let nextTimer = 1;
  const screen = {
    scrollY: 0,
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() {return true;},
    scrollBy(x, y) {this.scrollY += y;},
  };
  const cardsOf = html => [...html.matchAll(ARTICLES)].map((match, index) => {
    const position = index * CARD_HEIGHT;
    const details = {open: false};
    const summary = {focused: false, focus() {this.focused = true;}};
    return {dataset: {rowKey: match[1]}, details, summary, position,
      getBoundingClientRect: () => ({top: position - screen.scrollY,
        bottom: position - screen.scrollY + CARD_HEIGHT}),
      querySelector(selector) {
        if (selector === "details") return details;
        if (selector === "summary") return summary;
        return null;
      }};
  });
  const root = {
    isConnected: true, html: "", cards: [], ownerDocument: {activeElement: null},
    get innerHTML() {return this.html;},
    set innerHTML(value) {this.html = value; this.cards = cardsOf(value);},
    addEventListener(type, handler) {listeners.set(type, handler);},
    removeEventListener(type) {listeners.delete(type);},
    querySelector: () => null,
    querySelectorAll: selector => selector === ".lh-sample" ? root.cards : [],
  };
  return {screen, root, listeners,
    later(handler, delay) {const id = nextTimer++; timers.set(id, {handler, delay}); return id;},
    cancel(id) {timers.delete(id);},
    /* 只触发指定延时的定时器：页面的 4 秒学习轮询可以立即「到点」。 */
    fire(delay) {
      const due = [...timers].filter(([, timer]) => timer.delay === delay).map(([id]) => id);
      due.forEach(id => {const timer = timers.get(id); timers.delete(id); timer.handler();});
    },
    card: key => root.cards.find(card => card.dataset.rowKey === key) || null,
    keys: () => root.cards.map(card => card.dataset.rowKey)};
}

const settle = async page => {
  for (let index = 0; index < 80 && (page.state.busy || page.state.writing); index++) await flush();
};

async function mount(bridge, sources) {
  const dom = createDom();
  dom.screen.CompanionProduct = {bridgeNow: () => bridge, unwrap: value => value};
  const context = vm.createContext({window: dom.screen,
    CustomEvent: class {constructor(type, init = {}) {this.type = type; this.detail = init.detail;}},
    setTimeout: (handler, delay) => dom.later(handler, delay),
    clearTimeout: id => dom.cancel(id),
    JSON, Date, Math, Number, Object, Array, String});
  vm.runInContext(source, context);
  const page = context.window.CompanionExpressionSamplesPage;
  await page.mount(dom.root, {sources});
  await settle(page);
  return {page, root: dom.root, screen: dom.screen, dom,
    click: event => dom.listeners.get("click")?.(event),
    fire: delay => dom.fire(delay)};
}

const moreButton = () => ({target: {closest: selector => selector === "[data-sample-more]" ? {} : null}});
const refreshButton = () => ({target: {closest: selector => selector === "[data-sample-refresh]" ? {} : null}});
const actionButton = (rowKey, action) => ({target: {closest: selector => selector === "[data-sample-action]"
  ? {dataset: {rowKey, sampleAction: action}} : null}});
const filterButton = filter => ({target: {closest: selector => selector === "[data-sample-filter]"
  ? {dataset: {sampleFilter: filter}} : null}});

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
  await settle(page);
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

test("学习运行中的自动重读不再丢已加载的页", async () => {
  const calls = [];
  const bridge = {apiGet: async (route, body) => {
    calls.push(body?.after_id || null);
    return body?.after_id === "input:p2"
      ? page([sample("input:p3", "第二页一", 2), sample("input:p4", "第二页二", 3)], null, {running: true})
      : page([sample("input:p1", "第一页一", 0), sample("input:p2", "第一页二", 1)], "input:p2", {running: true});
  }};
  const {page: samples, root, click, fire} = await mount(bridge, [sourceOf("source-a", "来源甲", "a")]);
  assert.equal(samples.state.runtime.running, true);
  click(moreButton());
  await settle(samples);
  assert.equal(samples.state.items.length, 4);
  calls.length = 0;
  fire(4000);                        // 学习在途：页面自己排的 4 秒轮询到点
  await settle(samples);
  assert.equal(samples.state.items.length, 4);
  assert.match(root.innerHTML, /第一页一/);
  assert.match(root.innerHTML, /第二页二/);
  assert.deepEqual(calls, [null]);   // 只重读第一页原地更新，不再整表退回首屏
  samples.unmount();
});

test("允许学习后保留已加载的页并更新被操作的素材", async () => {
  const rows = [sample("input:a1", "待审一", 0), sample("input:a2", "待审二", 1),
    sample("input:b1", "待审三", 2), sample("input:b2", "待审四", 3)];
  const states = new Map(rows.map(row => [row.handle, "pending"]));
  const calls = [];
  const view = subset => subset.map(row => ({...row, state: states.get(row.handle),
    actions: states.get(row.handle) === "pending" ? ["approve", "reject"] : ["reject"]}));
  const bridge = {
    apiGet: async (route, body) => {
      calls.push(body?.after_id || null);
      return body?.after_id === "input:a2" ? page(view([rows[2], rows[3]]), null)
        : page(view([rows[0], rows[1]]), "input:a2");
    },
    apiPost: async (route, body) => {
      states.set(body.handle, body.action === "approve" ? "approved" : "rejected");
      return {status: "ok", data: {handle: body.handle, state: states.get(body.handle), replayed: false}};
    },
  };
  const {page: samples, root, click} = await mount(bridge, [sourceOf("source-a", "来源甲", "a")]);
  click(moreButton());
  await settle(samples);
  assert.equal(samples.state.items.length, 4);
  calls.length = 0;
  click(actionButton("source-a::input:a1", "approve"));
  await settle(samples);
  assert.equal(samples.state.items.length, 4);                 // 已加载的页没丢
  assert.equal(samples.state.items.find(item => item.handle === "input:a1").state, "approved");
  assert.equal(samples.state.notice, "素材已允许学习。");
  assert.equal(samples.state.error, false);
  assert.deepEqual(calls, [null, "input:a2"]);                 // 只重读已加载的两页
  assert.match(root.innerHTML, /data-state="approved"/);
  assert.match(root.innerHTML, /待审四/);
  samples.unmount();
});

test("加载更多的游标失效时自动按已加载页重读，而不是误报来源故障", async () => {
  const calls = [];
  let rolled = false;
  const bridge = {apiGet: async (route, body) => {
    calls.push(body?.after_id || null);
    if (body?.after_id) {
      rolled = true;
      throw Object.assign(new Error("expression_view_stale"), {code: "expression_view_stale"});
    }
    return rolled
      ? page([sample("input:s0", "滚动后的新素材", -1), sample("input:s1", "旧素材一", 1)], "input:s1")
      : page([sample("input:s1", "旧素材一", 1), sample("input:s2", "旧素材二", 2)], "input:s2");
  }};
  const {page: samples, root, click} = await mount(bridge, [sourceOf("source-a", "来源甲", "a")]);
  assert.equal(samples.state.items.length, 2);
  calls.length = 0;
  click(moreButton());
  await settle(samples);
  /* 游标滚出时间窗不是来源故障：退回第一页重读，已显示的素材一条不撤。 */
  assert.equal(samples.state.error, false);
  assert.equal(samples.state.notice, "素材已更新，已重新读取。");
  assert.deepEqual([...samples.state.readIssues], []);
  /* 重读以服务端窗口为准：已经滚出窗口的旧素材（s2）随之离开列表，
     不再留一张拿着失效 view_token 的卡片。 */
  assert.deepEqual(samples.state.items.map(item => item.handle), ["input:s0", "input:s1"]);
  assert.deepEqual(calls, ["input:s2", null]);
  assert.match(root.innerHTML, /滚动后的新素材/);
  assert.doesNotMatch(root.innerHTML, /全部来源读取失败/);
  assert.doesNotMatch(root.innerHTML, /lh-inline-status is-error/);
  samples.unmount();
});

test("已展开的素材在加载更多与重读之后保持展开，焦点仍在同一张卡上", async () => {
  const bridge = {apiGet: async (route, body) => body?.after_id === "input:o2"
    ? page([sample("input:o3", "第三页", 2)], null)
    : page([sample("input:o1", "展开一", 0), sample("input:o2", "展开二", 1)], "input:o2")};
  const {page: samples, root, click, dom} = await mount(bridge, [sourceOf("source-a", "来源甲", "a")]);
  const key = "source-a::input:o1";
  dom.card(key).details.open = true;                       // 用户展开第一张卡
  root.ownerDocument.activeElement = {closest: selector => selector === ".lh-sample" ? dom.card(key) : null};
  click(moreButton());
  await settle(samples);
  assert.equal(samples.state.items.length, 3);
  assert.equal(dom.card(key).details.open, true);
  assert.equal(dom.card("source-a::input:o2").details.open, false);
  assert.equal(dom.card(key).summary.focused, true);
  click(refreshButton());                                  // 重新读取：原地重读已加载的页
  await settle(samples);
  assert.equal(dom.card(key).details.open, true);
  samples.unmount();
});

test("整块重绘之后保留滚动位置与视口里那张卡", async () => {
  const older = [sample("input:m1", "素材一", 1), sample("input:m2", "素材二", 2)];
  let arrived = false;
  const bridge = {apiGet: async (route, body) => {
    if (body?.after_id === "input:m2") {
      return page([sample("input:m3", "素材三", 3), sample("input:m4", "素材四", 4)], null);
    }
    return arrived ? page([sample("input:m0", "新到的素材", 0), ...older], "input:m2")
      : page(older, "input:m2");
  }};
  const {page: samples, click, screen, dom} = await mount(bridge, [sourceOf("source-a", "来源甲", "a")]);
  click(moreButton());
  await settle(samples);
  assert.equal(samples.state.items.length, 4);
  screen.scrollY = 200;                        // 用户滑到第二张卡的开头
  arrived = true;
  click(refreshButton());                      // 重读时上方插进一条新素材
  await settle(samples);
  assert.equal(samples.state.items.length, 5);
  assert.equal(screen.scrollY, 320);           // 补回被顶下去的 120px
  const anchor = dom.card("source-a::input:m2");
  assert.equal(anchor.getBoundingClientRect().top, -80);   // 视口里还是同一张卡、同一位置
  samples.unmount();
});

test("多来源加载更多后按接收时间合并，已显示的素材一条不丢", async () => {
  const bridge = {apiGet: async (route, body) => {
    if (route.includes("profile-a")) {
      return body?.after_id === "input:a2"
        ? page([sample("input:a3", "甲页三", 4), sample("input:a4", "甲页四", 5)], null)
        : page([sample("input:a1", "甲页一", 0), sample("input:a2", "甲页二", 1)], "input:a2");
    }
    /* 乙来源的两条卡在甲来源两页之间：按来源分块拼接会得到
       [a1,a2,a3,a4,b1,b2]，按时间合并才是 [a1,a2,b1,b2,a3,a4]。 */
    return page([sample("input:b1", "乙页一", 2), sample("input:b2", "乙页二", 3)], null);
  }};
  const {page: samples, click, dom} = await mount(bridge, [
    sourceOf("source-a", "来源甲", "a"),
    sourceOf("source-b", "来源乙", "b"),
  ]);
  const handles = () => samples.state.items.map(item => item.handle);
  assert.deepEqual(handles(), ["input:a1", "input:a2", "input:b1", "input:b2"]);
  click(moreButton());
  await settle(samples);
  /* 多来源各自分页时，新一页按时间插进整张表，而不是接在该来源块的末尾。 */
  assert.deepEqual(handles(), ["input:a1", "input:a2", "input:b1", "input:b2", "input:a3", "input:a4"]);
  assert.deepEqual(dom.keys(), ["source-a::input:a1", "source-a::input:a2", "source-b::input:b1",
    "source-b::input:b2", "source-a::input:a3", "source-a::input:a4"]);
  samples.unmount();
});
test("待审筛选下允许学习后素材离开列表，不会拿着失效的 view_token 反复失败", async () => {
  const rows = [sample("input:a1", "待审一", 0), sample("input:a2", "待审二", 1),
    sample("input:a3", "待审三", 2)];
  const states = new Map(rows.map(row => [row.handle, "pending"]));
  /* 后端在决定之后会给这一行换掉 view_token；拿旧 token 再来一次就是
     expression_view_stale，和真实后端一致。 */
  const tokens = new Map(rows.map(row => [row.handle, "token-0"]));
  const posts = [];
  const pending = () => rows.filter(row => states.get(row.handle) === "pending")
    .map(row => ({...row, state: states.get(row.handle), view_token: tokens.get(row.handle),
      actions: ["approve", "reject"]}));
  const bridge = {
    apiGet: async () => page(pending(), null),
    apiPost: async (route, body) => {
      if (body.view_token !== tokens.get(body.handle)) {
        throw Object.assign(new Error("expression_view_stale"), {code: "expression_view_stale"});
      }
      posts.push(body);
      states.set(body.handle, body.action === "approve" ? "approved" : "rejected");
      tokens.set(body.handle, "token-" + posts.length);
      return {status: "ok", data: {handle: body.handle, state: states.get(body.handle),
        replayed: false}};
    },
  };
  const {page: samples, root, click} = await mount(bridge, [sourceOf("source-a", "来源甲", "a")]);
  click(filterButton("pending"));
  await settle(samples);
  assert.equal(samples.state.items.length, 3);
  click(actionButton("source-a::input:a1", "approve"));
  await settle(samples);
  assert.equal(samples.state.error, false);
  assert.equal(samples.state.notice, "素材已允许学习。");
  /* 状态变了的条目不再属于当前筛选窗口：列表里不能留一张拿着旧 token 的卡片。 */
  assert.ok(!samples.state.items.some(item => item.handle === "input:a1"));
  assert.doesNotMatch(root.innerHTML, /data-row-key="source-a::input:a1"/);
  /* 其余待审素材仍然可操作，不会因为旧 token 报 expression_view_stale。 */
  click(actionButton("source-a::input:a2", "reject"));
  await settle(samples);
  assert.equal(samples.state.error, false);
  assert.equal(samples.state.notice, "素材已排除，相关规则已停止应用。");
  assert.equal(posts.length, 2);
  samples.unmount();
});

test("全部筛选下允许学习后卡片保留，紧接着排除用的是重读后的新 view_token", async () => {
  const rows = [sample("input:b1", "素材一", 0), sample("input:b2", "素材二", 1)];
  const states = new Map(rows.map(row => [row.handle, "pending"]));
  const tokens = new Map(rows.map(row => [row.handle, "token-0"]));
  const posts = [];
  const view = () => rows.map(row => ({...row, state: states.get(row.handle),
    view_token: tokens.get(row.handle),
    actions: states.get(row.handle) === "pending" ? ["approve", "reject"] : ["reject"]}));
  const bridge = {
    apiGet: async () => page(view(), null),
    apiPost: async (route, body) => {
      if (body.view_token !== tokens.get(body.handle)) {
        throw Object.assign(new Error("expression_view_stale"), {code: "expression_view_stale"});
      }
      posts.push(body);
      states.set(body.handle, body.action === "approve" ? "approved" : "rejected");
      tokens.set(body.handle, "token-" + posts.length);
      return {status: "ok", data: {handle: body.handle, state: states.get(body.handle),
        replayed: false}};
    },
  };
  const {page: samples, click} = await mount(bridge, [sourceOf("source-a", "来源甲", "a")]);
  click(actionButton("source-a::input:b1", "approve"));
  await settle(samples);
  assert.equal(samples.state.error, false);
  assert.equal(samples.state.items.find(item => item.handle === "input:b1").state, "approved");
  /* 卡片留下了，但手里的 token 必须是重读换回来的那个，否则下一次操作必然失败。 */
  assert.notEqual(samples.state.items.find(item => item.handle === "input:b1").view_token, "token-0");
  click(actionButton("source-a::input:b1", "reject"));
  await settle(samples);
  assert.equal(samples.state.error, false);
  assert.equal(samples.state.notice, "素材已排除，相关规则已停止应用。");
  assert.equal(samples.state.items.find(item => item.handle === "input:b1").state, "rejected");
  assert.equal(posts.length, 2);
  samples.unmount();
});

test("来源暂时不可读时轮询保留已显示素材，重新读取才按服务端结果清空", async () => {
  let unreadable = false;
  const bridge = {apiGet: async () => unreadable ? empty("role_binding_missing")
    : page([sample("input:c1", "素材一", 0), sample("input:c2", "素材二", 1)], null, {running: true})};
  const {page: samples, click, fire} = await mount(bridge, [sourceOf("source-a", "来源甲", "a")]);
  assert.equal(samples.state.items.length, 2);
  unreadable = true;
  fire(4000);                                   // 学习在途的轮询（merge）
  await settle(samples);
  assert.equal(samples.state.items.length, 2);  // 一次后台读取不清空列表
  assert.equal(samples.state.notice, "1 个来源还没有可读取的素材。");
  assert.equal(samples.state.error, false);
  click(refreshButton());                        // 用户显式重新读取
  await settle(samples);
  assert.equal(samples.state.items.length, 0);  // 服务端说这个来源现在没有可读素材
  assert.equal(samples.state.available, false);
  assert.equal(samples.state.error, false);
  samples.unmount();
});