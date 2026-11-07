/* 相识详情页的「能力授权」面板。
 *
 * 这一页的行动逻辑跑在模块私有状态上，只能靠 vm 跑真实文件 + 假桥来驱动。
 * 夹具的职责是让「点开一个人真的会去读他的授权、点按钮真的会把参数发出去」，
 * 因为这一页最怕的两种失败都是静默的：
 *
 *   1. 面板把**配置派生的**授权画成可以撤销的按钮——撤销会被下一次加载复原，
 *      页面于是显示「已撤销」而能力还活着；
 *   2. 按钮发出去了，但 expected_revision 取错（取成 0、或取成别处的 revision），
 *      后端会以冲突拒绝，用户看到的是"点了没反应"。
 *
 * 所以断言落在**渲染出来的 HTML**与**真实请求参数**上，不落在布尔标志上。
 */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.resolve(__dirname, "../pages/关系与权限/app.js"), "utf8");
const memberSource = fs.readFileSync(path.resolve(__dirname, "../pages/关系与权限/member-editor.js"), "utf8");
const workspaceMarkup = fs.readFileSync(path.resolve(__dirname, "../pages/关系与权限/workspace.html"), "utf8");
const endpoint = "acquaintance";
const authorization = endpoint + "/authorization";

/* 目录里的一位私聊用户：字段就是后端 ``PersonAcquaintanceDirectory._person`` 的那几个。 */
function person(subjectId, name, extra = {}) {
  return {
    subject_id: subjectId, native_account_id: subjectId, display_name: name, name_basis: "platform",
    platform: "qq-main", account_id: "account-a", role_id: "role-a", bot_id: "bot-a", revision: 3,
    private_chat: true, private_target: true, settings_available: true, group_member: false,
    private: { msg_count: 12, active_days: 3, last_seen: "2026-09-30T10:00:00+00:00" },
    groups: {},
    total: { msg_count: 12, active_days: 3,
             first_seen: "2026-09-01T10:00:00+00:00", last_seen: "2026-09-30T10:00:00+00:00" },
    updated_at: "2026-09-30T10:00:00+00:00",
    ...extra,
  };
}

function directory(persons, groups = [], group_networks = [], group_access = {
  mode: "shared", editable: true, running: false,
  accounts: [{platform_id: "qq-main", account_id: "account-a", bot_id: "bot-a"}], persona_bots: ["bot-a"],
}) {
  const privateCount = persons.filter((item) => item.private_chat).length;
  const groupCount = persons.filter((item) => item.group_member).length;
  const bothCount = persons.filter((item) => item.private_chat && item.group_member).length;
  return { installation_id: "inst", profile_id: "profile-1", operator_name: "admin",
    persons, groups, group_networks, group_access, counts: { total: persons.length, private_chat: privateCount,
      group_member: groupCount, both: bothCount, private_only: privateCount - bothCount,
      group_only: groupCount - bothCount },
    page_managed_capabilities: ["conversation.reply", "message.delivery", "proactive.contact"] };
}

/* 一行授权。后端 ``PersonAuthorizationManagement._row`` 的形状。 */
function grantRow(capability, changes = {}) {
  return { capability, state: "active", managed_by: "page", revocable: true, tier: "standard",
    expires_at: null, valid_from: "2026-09-30T10:00:00+00:00",
    grant_id: "acquaintance-grant:" + capability, grant_revision: 1,
    scope: { kind: "private", native_scope_id: "subject-1", account_id: "login-a" },
    policy_revision: 7, ...changes };
}

function status(subjectId, items, companion = false) {
  const counts = {};
  for (const item of items) counts[item.state] = (counts[item.state] || 0) + 1;
  return { subject_id: subjectId, items, companion, counts, identity_tier: "standard", identity_revision: 1 };
}

/* ── 一个够用的 DOM 桩 ────────────────────────────────────────────────
   页面按 id 取固定节点、按标签造元素，并给卡片挂 click。真实 DOM 里这些
   都有语义，桩里只要让它们可断言即可：把元素按 id 记下来，事件处理器留着，
   测试从**真实注册的入口**驱动，而不是绕开接线直接调私有函数。 */
function element(tag) {
  const node = {
    tagName: String(tag).toUpperCase(), children: [], textContent: "", className: "",
    hidden: false, disabled: false, type: "", dataset: {}, style: { setProperty() {} },
    isConnected: true, open: false, focused: false, removed: false,
    showModal() { this.open = true; },
    close() { this.open = false; },
    remove() { this.isConnected = false; this.removed = true; },
    focus() { this.focused = true; },
    classList: {
      add(...tokens) { node.className = [...new Set([...node.className.split(/\s+/).filter(Boolean), ...tokens])].join(" "); },
      remove(...tokens) { node.className = node.className.split(/\s+/).filter((item) => item && !tokens.includes(item)).join(" "); },
      toggle(token, force) {
        const enabled = force === undefined ? !this.contains(token) : Boolean(force);
        if (enabled) this.add(token); else this.remove(token);
        return enabled;
      },
      contains(token) { return node.className.split(/\s+/).includes(token); },
    },
    setAttribute(name, value) {
      this[name] = String(value);
      if (name.startsWith("data-")) {
        const key = name.slice(5).replace(/-([a-z])/g, (_, char) => char.toUpperCase());
        this.dataset[key] = String(value);
      }
    },
    getAttribute(name) { return this[name]; },
    addEventListener(type, handler) { this.handlers = this.handlers || {}; this.handlers[type] = handler; },
    append(...items) { this.children.push(...items); },
    replaceChildren(...items) { this.children = items; },
    closest() { return this; },
    querySelectorAll(selector) {
      const selectors = String(selector).split(",").map(value => value.trim());
      const matches = item => selectors.some(query => {
        const classMatch = /^\.([\w-]+)$/.exec(query);
        if (classMatch) return item.className.split(/\s+/).includes(classMatch[1]);
        const dataMatch = /^\[data-([\w-]+)(?:="([^"]*)")?\]$/.exec(query);
        if (dataMatch) {
          const key = dataMatch[1].replace(/-([a-z])/g, (_, char) => char.toUpperCase());
          return item.dataset?.[key] !== undefined && (dataMatch[2] === undefined || item.dataset[key] === dataMatch[2]);
        }
        const focusMatch = /^(button|input|select):not\(:disabled\)$/.exec(query);
        if (focusMatch) return item.tagName === focusMatch[1].toUpperCase() && !item.disabled;
        if (query === '[tabindex]:not([tabindex="-1"])') return item.tabIndex !== undefined && item.tabIndex !== -1;
        return false;
      });
      const found = [];
      const visit = item => {
        if (matches(item)) found.push(item);
        for (const child of item.children || []) visit(child);
      };
      for (const child of this.children) visit(child);
      return found;
    },
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; },
  };
  return node;
}

function harness({ persons, groups = [], group_networks = [], group_access, authorizations = {}, memberSettings = {}, post,
                   refreshPending = false, strangers = [] } = {}) {
  const requests = [];
  const byId = new Map();
  const tabs = [element("button"), element("button"), element("button")];
  const chips = [element("button"), element("button"), element("button")];
  const graphFilters = [element("button"), element("button"), element("button")];
  const panels = [element("section"), element("section"), element("section")];
  tabs.forEach((tab, index) => { tab.dataset.tab = ["network", "people", "groups"][index]; });
  chips.forEach((chip, index) => { chip.dataset.filter = ["stable", "temporary", "discovered"][index]; });
  graphFilters.forEach((filter, index) => { filter.dataset.graphScope = ["all", "private", "group"][index]; });
  panels.forEach((panel, index) => { panel.id = ["panel-network", "panel-people", "panel-groups"][index]; });
  tabs.forEach((tab, index) => { tab.id = ["tab-network", "tab-people", "tab-groups"][index]; });
  for (const id of ["notice", "sync-state", "profile", "operator", "count-total", "count-private",
                    "count-group", "count-both", "count-split", "graph-summary", "graph-empty",
                    "relationship-graph", "directory-search", "clear-search", "people-result-count",
                    "people-grid", "person-detail", "groups-result-count", "groups-list", "group-detail",
                    "refresh", "chip-stable", "chip-temporary"]) {
    byId.set(id, element(id === "refresh" ? "button" : "div"));
  }
  for (const [, id] of workspaceMarkup.matchAll(/\bid="([^"]+)"/g)) {
    if (!byId.has(id)) byId.set(id, element("div"));
  }
  byId.get("directory-search").value = "";
  const relationshipApp = element("main");
  relationshipApp.className = "relationship-app";
  const body = element("body");
  const document = {
    getElementById: (id) => byId.get(id) || null,
    createElement: (tag) => element(tag),
    body,
    querySelector: (selector) => selector === ".relationship-app" ? relationshipApp : null,
    querySelectorAll: (selector) => selector === ".tab" ? tabs : selector === ".chip" ? chips
      : selector === ".graph-filter" ? graphFilters : selector === ".view-panel" ? panels : [],
    addEventListener() {},
  };
  const navigations = [];
  const documentEvents = {};
  document.addEventListener = (type, handler) => {documentEvents[type] = handler;};
  const window = {
    parent: {postMessage: (message, origin) => navigations.push({message, origin})},
    AstrBotPluginPage: {
      ready: () => Promise.resolve({ pluginName: "astrbot_plugin_private_companion" }),
      apiGet: (route, params, requestOptions) => new Promise((resolve, reject) => {
        requests.push({ method: "GET", route, params, requestOptions, resolve, reject });
      }),
      apiPost: (route, body, requestOptions) => new Promise((resolve, reject) => {
        requests.push({ method: "POST", route, body, requestOptions, resolve, reject });
      }),
    },
    document,
  };
  const context = vm.createContext({ window, document, console, Intl, Date, JSON, Math, Number, Object,
    Array, String, Set, Map, Boolean, Error, Promise, setTimeout, clearTimeout });
  vm.runInContext(memberSource, context);
  vm.runInContext(source, context);
  const answers = {
    [endpoint]: directory(persons || [], groups, group_networks, group_access),
    ...Object.fromEntries(Object.entries(authorizations).map(([subject, value]) =>
      [authorization + "|" + subject, value])),
  };
  answers[endpoint].directory_refresh_pending = refreshPending;
  const strangerList = {items: strangers};
  answers["acquaintance/strangers"] = strangerList;
  const settle = async () => { for (let i = 0; i < 12; i++) await new Promise((r) => setImmediate(r)); };
  const harness = {
    requests, document, byId, navigations, body, strangerList,
    element: (id) => byId.get(id),
    settle,
    /* 回答在飞的请求。目录与授权都按路由 + 参数分发，写请求交给用例的 post。
       顺序要紧：**先 settle 让请求发出来，再 answer**——answer 只处理已经在飞的
       请求，反过来写会让页面永远停在「正在读取」。 */
    answer(read) {
      for (const request of requests.slice()) {
        if (request.answered) continue;
        if (read && !read(request)) continue;
        request.answered = true;
        let value;
        try {
          value = request.method === "GET"
          ? request.route === "acquaintance/settings" ? memberSettings[request.params?.subject_id] || {sections: {}}
              : answers[request.route + (request.params?.subject_id ? "|" + request.params.subject_id : "")]
            : post?.(request);
        } catch (error) {
          request.reject(error);
          continue;
        }
        if (value === undefined) continue;
        request.resolve(value);
      }
    },
    card: (name) => cardFor(byId.get("people-grid"), name),
    mappingChanged: () => documentEvents["companion-mappings-change"](),
    fire: (type, detail = {}) => documentEvents[type]?.({detail}),
  };
  harness.send = async (read) => { await settle(); harness.answer(read); await settle(); };
  return harness;
}

/* 卡片是 button 且它的子树里含这个名字：桩没有 parentElement，所以直接遍历。 */
function cardFor(grid, name) {
  const stack = [...grid.children];
  while (stack.length) {
    const item = stack.shift();
    if (item.tagName === "BUTTON" && item.handlers?.click && flat(item).includes(name)) return item;
    stack.push(...(item.children || []));
  }
  return null;
}

function text(node) {
  return [node.textContent, ...(node.children || []).map(text)];
}

/* 桩树的文本拍平：跨 realm 的数组原型与本 realm 不同，比较前先变成一段字符串。 */
function flat(node) {
  return text(node).flat(Infinity).filter(Boolean).join(" ");
}

/* 把整棵桩树打平成一段可断言的 HTML 形态：类名 + 数据属性 + 文本 + 禁用态。
   递归到底，因为面板是嵌在详情卡里的（只拍一层会漏掉授权行）。 */
function markup(node) {
  const attributes = [["class", node.className],
    ["data-capability", node.dataset?.capability], ["data-state", node.dataset?.state],
    ["data-managed-by", node.dataset?.managedBy]]
    .filter(([, value]) => value !== undefined && value !== "")
    .map(([name, value]) => ` ${name}="${value}"`).join("");
  const own = node.textContent ? node.textContent : "";
  const inner = (node.children || []).map(markup).join("");
  return `<${node.tagName.toLowerCase()}${attributes}${node.disabled ? " disabled" : ""}>${own}${inner}</${node.tagName.toLowerCase()}>`;
}

async function open(h, name, pane = "身份与权限") {
  await h.send((request) => request.route === endpoint);
  const grid = h.element("people-grid");
  const card = cardFor(grid, name);
  const seen = grid.children.map((item) => item.tagName + "/" + item.className).join(",");
  assert.ok(card?.handlers?.click,
    "目录里应当有 " + name + " 的卡片（grid=" + grid.children.length + " 个子节点：" + seen
      + "；sync=" + h.element("sync-state").textContent + "；notice=" + h.element("notice").textContent
      + "；requests=" + JSON.stringify(h.requests.map(request => [request.method, request.route])) + "）");
  card.handlers.click();
  /* 点开一个人会去读他的授权：同一套「先发出来、再回答」。 */
  await h.send((request) => request.route === authorization || request.route === "acquaintance/settings");
  findControl(h.element("person-detail"), pane).handlers.click();
  return h.element("person-detail");
}

function tree(node, depth = 0) {
  return [node.tagName + "." + node.className + (node.handlers?.click ? "[click]" : "")
    + (node.textContent ? "(" + node.textContent + ")" : ""),
    ...(node.children || []).map((child) => "  ".repeat(depth + 1) + tree(child, depth + 1))].join("\n");
}

test("关系图作为关系网默认视图，人物筛选保留", async () => {
  const html = fs.readFileSync(path.resolve(__dirname, "../pages/关系与权限/workspace.html"), "utf8");
  assert.deepEqual([...html.matchAll(/data-tab="([^"]+)"/g)].map(match => match[1]), ["network", "people", "groups"]);
  assert.deepEqual([...html.matchAll(/data-filter="([^"]+)"/g)].map(match => match[1]), ["stable", "temporary", "discovered"]);
  const h = harness();
  await h.send();
  assert.equal(h.document.querySelectorAll(".tab")[0].getAttribute("aria-selected"), "true");
  assert.equal(h.document.querySelectorAll(".view-panel").find(item => item.id === "panel-network").hidden, false);
  assert.equal(h.document.querySelectorAll(".view-panel").find(item => item.id === "panel-people").hidden, true);
});

test("本地关系先显示，慢速补充请求保留正在编辑的成员详情", async () => {
  const h = harness({persons: [person("subject-1", "已知成员")], refreshPending: true});
  await h.send(request => request.route === endpoint && request.params.read_mode === "stored");
  assert.equal(h.element("count-total").textContent, "1");
  assert.match(h.element("sync-state").className, /is-ready/);
  assert.equal(h.element("refresh").disabled, false);
  const pending = h.requests.find(request => request.route === endpoint && !request.params.read_mode);
  assert.ok(pending && !pending.answered);
  const input = element("input");
  input.value = "尚未保存的关系资料";
  h.element("person-detail").append(input);
  const group = {group_id: "300", display_name: "新发现的群", platform_id: "qq-main",
    account_id: "account-a", bot_id: "bot-a", discovered: true};
  pending.resolve(directory([person("subject-1", "最新备注")], [group]));
  await h.settle();
  assert.ok(h.card("最新备注"));
  assert.match(flat(h.element("groups-list")), /新发现的群/);
  assert.ok(h.element("person-detail").children.includes(input));
  assert.equal(input.value, "尚未保存的关系资料");
  assert.equal(h.element("sync-state").textContent, "V2 数据已同步");
});

test("补充资料读取失败后本地关系仍可查看", async () => {
  const h = harness({persons: [person("subject-1", "已知成员")], refreshPending: true});
  await h.send(request => request.route === endpoint && request.params.read_mode === "stored");
  const pending = h.requests.find(request => request.route === endpoint && !request.params.read_mode);
  pending.resolve({status: "error", data: {code: "acquaintance_native_directory_unavailable"}});
  await h.settle();
  assert.ok(h.card("已知成员"));
  assert.equal(h.element("count-total").textContent, "1");
  assert.match(h.element("sync-state").className, /is-ready/);
  assert.match(h.element("sync-state").textContent, /补充资料暂未更新/);
});

test("旧补充请求不会覆盖重新读取后的关系网", async () => {
  const h = harness({persons: [person("subject-1", "原成员")], refreshPending: true});
  await h.send(request => request.route === endpoint && request.params.read_mode === "stored");
  const pending = h.requests.find(request => request.route === endpoint && !request.params.read_mode);
  h.element("refresh").handlers.click();
  await h.settle();
  h.requests.filter(request => request.route === endpoint && request.params.read_mode === "stored").at(-1)
    .resolve(directory([person("subject-2", "新目录成员")]));
  await h.settle();
  pending.resolve(directory([person("subject-1", "过时名称")]));
  await h.settle();
  assert.ok(h.card("新目录成员"));
  assert.equal(h.card("过时名称"), null);
});

test("补充读取发现管理授权失效时清空关系数据", async () => {
  const h = harness({persons: [person("subject-1", "已知成员")], refreshPending: true});
  await h.send(request => request.route === endpoint && request.params.read_mode === "stored");
  const pending = h.requests.find(request => request.route === endpoint && !request.params.read_mode);
  pending.resolve({status: "error", data: {code: "core_access_required"}});
  await h.settle();
  assert.equal(h.element("count-total").textContent, "—");
  assert.equal(h.card("已知成员"), null);
  assert.match(h.element("sync-state").className, /is-error/);
});

test("成员页的错误提示不会串到群聊视图", async () => {
  const h = harness();
  await h.settle();
  const request = h.requests.find(item => item.route === endpoint);
  request.reject(new Error("directory unavailable"));
  await h.settle();
  assert.equal(h.element("notice").hidden, false);
  h.document.querySelectorAll(".tab")[2].handlers.click();
  assert.equal(h.element("notice").hidden, true);
});

test("同一人格的不同内部归属只显示一张成员卡，权限编辑仍按归属切换", async () => {
  const first = person("subject-1", "阿澈", {role_id: "role-a", persona_ids: ["星缘-私聊"], revision: 3});
  const second = person("subject-1", "阿澈", {role_id: "role-b", persona_ids: ["星缘-私聊"], revision: 8,
    total: {msg_count: 4, active_days: 1, first_seen: "2026-09-15T10:00:00+00:00",
      last_seen: "2026-10-01T10:00:00+00:00"}});
  const h = harness({persons: [first, second], authorizations: {"subject-1": status("subject-1", [])}});
  await h.send((request) => request.route === endpoint);
  assert.equal(findAll(h.element("people-grid"), "person-card").length, 1,
    tree(h.element("people-grid")) + "\nsync=" + h.element("sync-state").textContent
      + "\nnotice=" + h.element("notice").textContent
      + "\nrequests=" + JSON.stringify(h.requests.map(request => [request.method, request.route])));
  assert.match(flat(h.element("people-grid")), /2 个内部归属/);
  const card = cardFor(h.element("people-grid"), "阿澈");
  card.handlers.click();
  await h.send((request) => request.route === authorization || request.route === "acquaintance/settings");
  assert.equal(h.requests.filter((request) => request.method === "GET" && request.route === authorization).length, 0,
    "多个内部归属不能自动读取第一条路由的权限");
  const selector = findInput(h.element("person-detail"), "选择内部人格归属");
  assert.ok(selector && selector.children.length === 3);
  selector.value = selector.children[2].value;
  selector.handlers.change({target: selector});
  await h.send((request) => request.route === authorization || request.route === "acquaintance/settings");
  const authRead = h.requests.filter((request) => request.method === "GET" && request.route === authorization).at(-1);
  assert.ok(authRead, JSON.stringify(h.requests.map(request => ({method: request.method, route: request.route, params: request.params}))));
  assert.equal(authRead.params.role_id, "role-b");
});

test("没有已建立的 V2 私聊路由时成员资料与权限只读", async () => {
  const member = person("subject-1", "阿澈", {settings_available: false});
  const h = harness({persons: [member], authorizations: {"subject-1": status("subject-1", [])}});
  const detail = await open(h, "阿澈");
  assert.match(flat(detail), /尚未建立当前账号与人格下可管理的 V2 私聊窗口/);
  assert.equal(h.requests.filter(request => request.method === "GET"
    && [authorization, "acquaintance/settings"].includes(request.route)).length, 0);
  assert.equal(findAll(detail, "member-permission-row").length, 0);
});

test("稳定和临时只按成员往来类型筛选，跳转时保持对应分类", async () => {
  const stable = person("subject-stable", "稳定成员");
  const temporary = person("subject-temporary", "临时成员", {
    private_chat: false, private_target: false, settings_available: false, group_member: true, private: null,
    groups: {"1001": {msg_count: 2, active_days: 1}},
  });
  const both = person("subject-both", "两边成员", {group_member: true,
    groups: {"1001": {msg_count: 3, active_days: 2}}});
  const h = harness({persons: [stable, temporary, both], groups: [
    {group_id: "1001", display_name: "临时群", configured: false, member_count: 2, msg_count: 5},
  ]});
  await h.send();
  const chips = h.document.querySelectorAll(".chip");
  assert.equal(chips.length, 3);
  assert.equal(h.element("chip-stable").textContent, "2");
  assert.equal(h.element("chip-temporary").textContent, "2");
  assert.equal(h.element("people-result-count").textContent, "2 人");

  chips[1].handlers.click();
  assert.equal(h.element("people-result-count").textContent, "2 人");
  assert.match(flat(h.element("people-grid")), /临时成员/);
  assert.equal(chips[1].getAttribute("aria-pressed"), "true");

  const graph = h.element("relationship-graph");
  graph.handlers.click({target: {dataset: {personId: JSON.stringify(["subject-temporary", "qq-main", "bot-a", "account-a", "role-a"])}, closest(selector) {
    return selector === "[data-person-id]" ? this : null;
  }}});
  assert.equal(chips[1].getAttribute("aria-pressed"), "true");
  assert.equal(h.element("people-result-count").textContent, "2 人");

  h.document.querySelectorAll(".tab")[2].handlers.click();
  findAll(h.element("groups-list"), "group-card")[0].handlers.click();
  findAll(h.element("group-detail"), "member-chip").find((item) => flat(item).includes("临时成员")).handlers.click();
  assert.equal(chips[1].getAttribute("aria-pressed"), "true");
});

test("群名用于列表、图谱和搜索，历史群不会重复统计", async () => {
  const member = person("subject-1", "阿澈", {group_member: true, groups: {"1002": {msg_count: 2, active_days: 1}}});
  const h = harness({persons: [member], groups: [
    {group_id: "1002", display_name: "周末读书群", configured: false, platform_id: "qq-main",
      bot_id: "bot-a", account_id: "account-a", member_count: 1, msg_count: 2},
  ]});
  await h.send();
  assert.match(flat(h.element("groups-list")), /周末读书群.*群号 1002.*历史往来.*1 位群成员.*2 条消息/);
  assert.match(h.element("relationship-graph").innerHTML, /周末读书群/);
  assert.match(flat(h.card("阿澈")), /群聊 1/);
  const search = h.element("directory-search");
  search.handlers.input({target: {value: "周末读书"}});
  assert.equal(h.element("groups-result-count").textContent, "1 个群");
  assert.equal(h.element("people-result-count").textContent, "1 人");
});

test("群聊备注优先显示并用原始平台 ID 保存与清除", async () => {
  const group = {group_id: "SYNTHETIC_GROUP_OPENID", display_name: "平台群名", remark: "管理备注",
    remark_revision: 2, platform_id: "qq-main", bot_id: "bot-a", account_id: "account-a",
    configured: true, member_count: 0, msg_count: 0};
  const h = harness({groups: [group], post: (request) => {
    group.remark = request.body.remark;
    group.remark_revision = request.body.expected_revision + 1;
    return {status: "ok", data: {remark: group.remark, remark_revision: group.remark_revision}};
  }});
  await h.send();
  assert.match(flat(h.element("groups-list")), /管理备注/);
  assert.match(h.element("relationship-graph").innerHTML, /管理备注/);
  findAll(h.element("groups-list"), "group-card")[0].handlers.click();
  const detail = h.element("group-detail");
  assert.match(flat(detail), /管理备注.*平台 ID SYNTHETIC_GROUP_OPENID/);
  const form = findAll(detail, "group-remark-form")[0];
  const input = form.children.find(item => item.tagName === "INPUT");
  input.value = "新备注";
  input.handlers.input();
  form.handlers.submit({preventDefault() {}});
  await h.send(request => request.method === "POST");
  const write = h.requests.find(request => request.method === "POST");
  assert.equal(write.route, "acquaintance/group-remark");
  assert.deepEqual(JSON.parse(JSON.stringify(write.body)), {
    platform_id: "qq-main", bot_id: "bot-a", account_id: "account-a",
    group_id: "SYNTHETIC_GROUP_OPENID", remark: "新备注", expected_revision: 2,
  });
  await h.send(request => request.method === "GET" && request.route === endpoint);
  assert.match(flat(h.element("group-detail")), /新备注/);
  const clear = findControl(h.element("group-detail"), "清除备注");
  clear.handlers.click();
  const nextForm = findAll(h.element("group-detail"), "group-remark-form")[0];
  nextForm.handlers.submit({preventDefault() {}});
  await h.send(request => request.method === "POST" && !request.answered);
  const writes = h.requests.filter(request => request.method === "POST");
  assert.equal(writes[1].body.remark, "");
  assert.equal(writes[1].body.expected_revision, 3);
  await h.send(request => request.method === "GET" && request.route === endpoint);
  assert.match(flat(h.element("group-detail")), /平台群名/);
});

test("关系网展示自动接入状态，待映射群前往账号与会话", async () => {
  const h = harness({groups: [
    {group_id: "450", display_name: "项目讨论群", platform_id: "qq-main", bot_id: "bot-a",
      account_id: "account-a", role_id: "role-a", configured: false, discovered: true, member_count: 0, msg_count: 0},
  ]});
  await h.send();
  assert.match(flat(h.element("groups-list")), /项目讨论群.*AstrBot 已发现，待角色映射/);
  findAll(h.element("groups-list"), "group-card")[0].handlers.click();
  const detail = flat(h.element("group-detail"));
  assert.match(detail, /人格归入角色后会自动接入/);
  assert.match(detail, /成员线索和熟人卡只用于认识群友/);
  assert.equal(findControl(h.element("group-detail"), "加入 V2 群目标"), null);
  assert.equal(findControl(h.element("group-detail"), "移出 V2 群目标"), null);
  findControl(h.element("group-detail"), "账号与会话").handlers.click();
  assert.equal(h.navigations.at(-1).message.target.tab, "host");
  assert.equal(h.requests.filter(request => request.method === "POST").length, 0);
});

test("已自动接入的群提供本群回应与公共档案入口", async () => {
  const h = harness({groups: [{group_id: "450", display_name: "项目讨论群", platform_id: "qq-main",
    bot_id: "bot-a", account_id: "account-a", role_id: "role-a", configured: true,
    discovered: true, member_count: 0, msg_count: 0}]});
  await h.send();
  findAll(h.element("groups-list"), "group-card")[0].handlers.click();
  assert.match(flat(h.element("group-detail")), /V2 群聊接入 已接入/);
  findControl(h.element("group-detail"), "本群回应设置").handlers.click();
  assert.deepEqual(JSON.parse(JSON.stringify(h.navigations.at(-1).message.target)),
    {tab: "group-reply", platform_id: "qq-main", bot_id: "bot-a", account_id: "account-a",
      group_id: "450", role_id: "role-a"});
  findControl(h.element("group-detail"), "群公共档案").handlers.click();
  assert.deepEqual(JSON.parse(JSON.stringify(h.navigations.at(-1).message.target)),
    {tab: "group-memory", platform_id: "qq-main", bot_id: "bot-a", account_id: "account-a",
      group_id: "450", role_id: "role-a"});
  assert.equal(findControl(h.element("group-detail"), "移出 V2 群目标"), null);
});

test("运行状态不要求停机，未映射账号提供角色映射入口", async () => {
  const group = {group_id: "454", display_name: "待配置群", platform_id: "qq-main", bot_id: "bot-a",
    account_id: "account-a", configured: false, discovered: true, member_count: 0, msg_count: 0};
  for (const running of [true, false]) {
    const h = harness({groups: [{...group}], group_access: {
      editable: !running, running, accounts: [], persona_bots: []}});
    await h.send();
    findAll(h.element("groups-list"), "group-card")[0].handlers.click();
    const detail = h.element("group-detail");
    assert.match(flat(detail), /尚未归入角色/);
    assert.doesNotMatch(flat(detail), /停止 V2|手动接管/);
    findControl(detail, "账号与会话").handlers.click();
    assert.equal(h.navigations.at(-1).message.target.tab, "host");
  }
});

test("账号路由未明确的群只显示真实发现状态", async () => {
  const h = harness({groups: [{group_id: "451", display_name: "账号待确认的群", platform_id: "qq-main",
    bot_id: "", account_id: "", configured: false, discovered: true, member_count: 0, msg_count: 0}]});
  await h.send();
  assert.match(flat(h.element("groups-list")), /AstrBot 已发现，账号待确认/);
  findAll(h.element("groups-list"), "group-card")[0].handlers.click();
  assert.match(flat(h.element("group-detail")), /还没有为这个群确定唯一的 Bot 账号/);
  assert.equal(findAll(h.element("group-detail"), "group-access-button").length, 0);
});

test("角色映射更新后原地重读关系网并保留群选择", async () => {
  const group = {group_id: "456", display_name: "项目讨论群", platform_id: "qq-main", bot_id: "bot-a",
    account_id: "account-a", configured: false, discovered: true, member_count: 0, msg_count: 0};
  const h = harness({groups: [group]});
  await h.send();
  findAll(h.element("groups-list"), "group-card")[0].handlers.click();
  group.configured = true;
  h.mappingChanged();
  await h.send(request => request.method === "GET" && request.route === endpoint);
  assert.match(flat(h.element("group-detail")), /项目讨论群.*V2 群聊接入 已接入/);
  assert.equal(h.requests.filter(request => request.method === "GET" && request.route === endpoint).length, 2);
});

test("群拓扑合并关系网成员、熟人卡与线索，并显示互动边", async () => {
  const member = person("subject-700", "青禾", {native_account_id: "700", group_member: true,
    private_chat: true, groups: {"300": {msg_count: 4, active_days: 2}}});
  const network = {group_id: "300", platform_id: "qq-main", bot_id: "bot-a", account_id: "account-a",
    role_id: "role-a", configured: false, member_count: 2, interaction_count: 5,
    members: [
      {native_account_id: "700", subject_id: "subject-700", display_name: "青禾", aliases: ["小禾"],
        group_observation: {basis: "relationship_graph", message_count: 9, active_days: 3,
          last_seen: "2026-10-01T10:00:00+00:00", recent: [{text: "接上话题"}]},
        acquaintance: {message_count: 14, active_days: 4, private_chat: true},
        clues: [{entry_id: "clue-1", name: "青禾", basis: "declared_account", content: "常聊读书"}]},
      {native_account_id: "701", display_name: "木木", aliases: [],
        group_observation: {basis: "relationship_graph", message_count: 3, active_days: 1,
          last_seen: "2026-10-01T10:01:00+00:00", recent: []},
        acquaintance: {message_count: 0, private_chat: false}, clues: []},
    ],
    edges: [{a: "700", b: "701", count: 5, tone: "banter", summary: "近期有玩笑或接梗",
      last_seen: "2026-10-01T10:01:00+00:00"}],
    unmatched_clues: [{entry_id: "clue-2", name: "群里新朋友", basis: "unmatched", content: "还未对应账号"}],
  };
  const h = harness({persons: [member], group_networks: [network]});
  await h.send();
  findAll(h.element("groups-list"), "group-card")[0].handlers.click();
  const detail = h.element("group-detail");
  assert.match(flat(detail), /成员互动/);
  assert.match(flat(detail), /青禾 ↔ 木木.*5 次.*玩笑/);
  assert.match(flat(detail), /熟人卡 14 条 · 有私聊/);
  assert.match(flat(detail), /成员线索：青禾/);
  assert.match(flat(detail), /待确认成员线索.*群里新朋友/);
  assert.match(flat(detail), /(?:V2 成员记录|群聊成员) 2 人.*已观察发言 12 条/);
  assert.match(flat(detail), /群聊近况.*青禾.*接上话题/);
  assert.match(flat(detail), /活跃群友.*青禾.*9 条.*3 天/);
  assert.match(findAll(detail, "member-network-viewport")[0].innerHTML, /data-member-account="700"/);
  findAll(detail, "member-chip")[0].handlers.click();
  assert.equal(h.document.querySelectorAll(".tab")[1].getAttribute("aria-selected"), "true");
});

test("同一人格跨内部归属合并群关系网成员与互动边", async () => {
  const base = {group_id: "300", platform_id: "qq-main", bot_id: "bot-a", account_id: "account-a",
    configured: false, persona_ids: ["星缘-私聊"], role_name: "星缘-私聊"};
  const first = {...base, role_id: "role-a", member_count: 2, interaction_count: 3,
    members: [
      {native_account_id: "700", display_name: "青禾", group_observation: {message_count: 9, active_days: 3},
        acquaintance: {message_count: 14, private_chat: true}, clues: []},
      {native_account_id: "701", display_name: "木木", group_observation: {message_count: 3, active_days: 1}, clues: []},
    ], edges: [{a: "700", b: "701", count: 3, tone: "banter", summary: "接话", last_seen: "2026-10-01T10:00:00+00:00"}]};
  const second = {...base, role_id: "role-b", member_count: 2, interaction_count: 2,
    members: [
      {native_account_id: "700", display_name: "青禾", group_observation: {message_count: 4, active_days: 2},
        acquaintance: {message_count: 14, private_chat: true}, clues: []},
      {native_account_id: "702", display_name: "小满", group_observation: {message_count: 2, active_days: 1}, clues: []},
    ], edges: [{a: "700", b: "702", count: 2, tone: "ordinary", summary: "回应", last_seen: "2026-10-01T10:01:00+00:00"}]};
  const h = harness({group_networks: [first, second]});
  await h.send();
  assert.equal(h.element("groups-result-count").textContent, "1 个群");
  findAll(h.element("groups-list"), "group-card")[0].handlers.click();
  const detail = h.element("group-detail");
  assert.match(flat(detail), /3 人/);
  const networkMarkup = findAll(detail, "member-network-viewport")[0].innerHTML;
  assert.equal([...networkMarkup.matchAll(/data-member-account=/g)].length, 3);
  assert.match(networkMarkup, /data-member-account="700"/);
  assert.match(networkMarkup, /data-member-account="701"/);
  assert.match(networkMarkup, /data-member-account="702"/);
  assert.match(flat(detail), /青禾 ↔ 木木.*3 次/);
  assert.match(flat(detail), /青禾 ↔ 小满.*2 次/);
});

test("已接入群只保留当前角色，历史人格网络不生成重复卡片", async () => {
  const base = {group_id: "300", display_name: "周末读书群", platform_id: "qq-main",
    bot_id: "bot-a", account_id: "account-a", configured: true, discovered: true,
    member_count: 2, msg_count: 12};
  const current = {...base, role_id: "role-a", persona_ids: ["星缘-群聊", "星缘-私聊"],
    role_name: "星缘·群聊、星缘·私聊", configured: true, interaction_count: 3, members: [], edges: []};
  const stale = {...base, role_id: "星缘", persona_ids: [], role_name: "",
    configured: false, interaction_count: 1, members: [], edges: []};
  const h = harness({groups: [base], group_networks: [current, stale]});
  await h.send();
  const cards = findAll(h.element("groups-list"), "group-card");
  assert.equal(cards.length, 1, "a configured route must hide the stale historical duplicate");
  assert.equal(h.element("groups-result-count").textContent, "1 个群");
  assert.equal(h.element("group-summary-total").textContent, "1");
  assert.equal(h.element("group-summary-configured").textContent, "1");
  assert.equal(h.element("group-summary-history").textContent, "0");
  assert.match(flat(cards[0]), /周末读书群.*已接入.*星缘/);
  assert.doesNotMatch(flat(cards[0]), /人格分线|星缘·群聊|星缘·私聊/);
  cards[0].handlers.click();
  findControl(h.element("group-detail"), "本群回应设置").handlers.click();
  assert.equal(h.navigations.at(-1).message.target.role_id, "role-a");
  h.fire("companion-navigate-failed", {message: "这个群的人格或角色映射已经变化，请重新读取群聊目录后再试。"});
  assert.match(h.element("notice").textContent, /角色映射已经变化.*重新读取群聊目录/);
});

test("面板为每一项能力画一行，状态取自服务端读回的那一份", async () => {
  const people = [person("subject-1", "阿澈")];
  const h = harness({ persons: people, authorizations: { "subject-1": status("subject-1", [
    grantRow("conversation.reply", { managed_by: "configuration", revocable: false,
      grant_id: "astrbot-staging-grant:conversation.reply" }),
    grantRow("proactive.contact", { state: "revoked" }),
  ]) } });
  const detail = await open(h, "阿澈");
  const html = markup(detail);
  /* 首屏按本地模式读取，身份与权限仍由受信路由提供。 */
  const directoryRequest = h.requests.find((item) => item.route === endpoint);
  assert.equal(directoryRequest.params.read_mode, "stored");
  assert.match(html, /data-capability="conversation\.reply"[^>]*data-state="active"[^>]*data-managed-by="configuration"/);
  /* 撤销过就说撤销过，不是「关」也不是空白：那是记录里的事实。 */
  assert.match(html, /data-capability="proactive\.contact"[^>]*data-state="revoked"/);
  assert.match(JSON.stringify(text(detail)), /允许私聊对话/);
  assert.match(JSON.stringify(text(detail)), /已关闭/);
  /* 一项配置派生的授权不当场改名成「没有授权」：它是生效的。 */
  assert.match(JSON.stringify(text(detail)), /已允许/);
  assert.match(JSON.stringify(text(detail)), /普通用户/);
  assert.doesNotMatch(JSON.stringify(text(detail)), /conversation\.reply|proactive\.contact|login-a|档位 standard/);
});

test("日程干预可按当前成员与私聊窗口单独授权", async () => {
  const people = [person("subject-1", "阿澈")];
  let saved;
  const h = harness({persons: people, authorizations: {"subject-1": status("subject-1", [
    grantRow("calendar.intervene", {state: "off"}),
  ])}, post: request => {
    saved = request;
    return {status: "ok", data: {replayed: false, operation_id: "intervention-op",
      status: status("subject-1", [grantRow("calendar.intervene", {policy_revision: 8})])}};
  }});
  const detail = await open(h, "阿澈");
  const toggle = findPermission(detail, "calendar.intervene");
  assert.equal(toggle.checked, false);
  toggle.checked = true;
  toggle.handlers.change();
  await h.send(request => request.method === "POST");

  assert.equal(saved.route, "acquaintance/authorization");
  assert.equal(saved.body.capability, "calendar.intervene");
  assert.equal(saved.body.enabled, true);
  assert.equal(saved.body.expected_revision, 7);
  assert.equal(saved.body.route.bot_id, "bot-a");
  assert.equal(findPermission(h.element("person-detail"), "calendar.intervene").checked, true);
});

test("配置派生的那一行没有撤销按钮，并说清去哪里改", async () => {
  const people = [person("subject-1", "阿澈")];
  const h = harness({ persons: people, authorizations: { "subject-1": status("subject-1", [
    grantRow("conversation.reply", { managed_by: "configuration", revocable: false,
      grant_id: "astrbot-staging-grant:conversation.reply" }),
  ]) } });
  const detail = await open(h, "阿澈");
  const html = markup(detail);
  assert.doesNotMatch(html, /撤销授权/, "配置派生的行不许出现撤销控件");
  const flat = JSON.stringify(text(detail));
  /* 不是笼统的「只读」：要说清它由运行实例配置签发、改哪里。 */
  assert.match(flat, /运行实例配置签发/);
  assert.match(flat, /账号与会话中调整/);
  assert.equal(findPermission(detail, "conversation.reply").disabled, true);
  /* 本页拥有的那一行才有控件，并且按钮上写着它真的会做什么。 */
  const owned = harness({ persons: people, authorizations: { "subject-1": status("subject-1", [
    grantRow("proactive.contact"),
  ]) } });
  const ownedDetail = await open(owned, "阿澈");
  assert.equal(Boolean(findPermission(ownedDetail, "proactive.contact").disabled), false);
  assert.doesNotMatch(JSON.stringify(text(ownedDetail)), /运行实例配置签发/);
});

test("其他能力显示真实管理入口，而不是留下不能点击的状态行", async () => {
  const people = [person("subject-1", "阿澈")];
  const h = harness({persons: people, authorizations: {"subject-1": status("subject-1", [
    grantRow("text", {managed_by: "configuration", revocable: false}),
    grantRow("configuration.read", {managed_by: "configuration", revocable: false}),
    grantRow("screen.read", {managed_by: "configuration", revocable: false}),
    grantRow("screen.process", {managed_by: "configuration", revocable: false}),
    grantRow("extension.tool.invoke", {managed_by: "configuration", revocable: false}),
    grantRow("message.delivery", {managed_by: "configuration", revocable: false}),
  ])}});
  const detail = await open(h, "阿澈");
  const flatText = JSON.stringify(text(detail));
  assert.match(flatText, /运行实例和拓展能力的统一授权/);
  assert.match(flatText, /模型与连接/);
  assert.match(flatText, /账号与会话/);
  assert.match(flatText, /感知 · 设备与授权/);
  assert.match(flatText, /打开设备与授权/);
  assert.match(flatText, /兼容记录/);
  assert.doesNotMatch(flatText, /旧投递授权.*已允许/);
  assert.ok(findControl(detail, "打开设备与授权"), "屏幕能力应给出设备授权入口");
  assert.ok(findControl(detail, "打开模型与连接"), "模型能力应给出模型入口");
});

test("管理员称呼写入绑定目录路由与卡片 revision，空值清除并恢复平台名称", async () => {
  const people = [person("subject-1", "平台昵称")];
  let revision = 3;
  const h = harness({ persons: people, authorizations: { "subject-1": status("subject-1", []) },
    post: (request) => {
      revision += 1;
      return { status: "ok", data: { replayed: false, operation_id: "name-op",
        subject_id: request.body.subject_id, admin_nickname: request.body.admin_nickname || null,
        admin_nickname_updated_at: "2026-10-01T10:00:00+00:00",
        display_name: request.body.admin_nickname || "平台昵称",
        name_basis: request.body.admin_nickname ? "administrator" : "platform",
        revision, changed: true } };
    } });
  const detail = await open(h, "平台昵称", "身份与权限");
  const input = findInput(detail, "目录显示名称");
  assert.ok(input, "成员详情应显示称呼编辑框");
  input.value = "小月亮";
  input.handlers.input();
  findControl(detail, "保存备注").handlers.click();
  await h.send((request) => request.method === "POST");
  const first = h.requests.find((request) => request.method === "POST");
  assert.equal(first.route, "acquaintance/admin-name");
  assert.deepEqual(JSON.parse(JSON.stringify(first.body)), { operation_key: first.body.operation_key,
    subject_id: "subject-1", platform: "qq-main", bot_id: "bot-a", account_id: "account-a",
    role_id: "role-a", native_account_id: "subject-1", admin_nickname: "小月亮", expected_revision: 3 });
  assert.match(flat(h.element("person-detail")), /小月亮/);
  assert.match(flat(h.element("person-detail")), /目录备注/);

  const clearInput = findInput(h.element("person-detail"), "目录显示名称");
  clearInput.value = "";
  clearInput.handlers.input();
  findControl(h.element("person-detail"), "保存备注").handlers.click();
  await h.send((request) => request.method === "POST" && request.body.expected_revision === 4);
  const clear = h.requests.filter((request) => request.method === "POST")[1];
  assert.equal(clear.body.admin_nickname, "");
  assert.equal(clear.body.expected_revision, 4);
  assert.match(flat(h.element("person-detail")), /平台昵称/);
});

test("联系偏好显示该私聊的自动调度原因与下一步建议", async () => {
  const contact = {edit_token: "contact-edit-1", fields: {}, settings: {enabled: true, automatic_enabled: false},
    schedule_diagnostic: {
      scheduler: {state: "running", last_seen_at: "2026-10-12T09:58:00+08:00"},
      evaluation: {outcome: "blocked", stage: "settings", reason_code: "automatic_contact_disabled",
        observed_at: "2026-10-12T09:55:00+08:00", next_at: null, purpose: null},
    }};
  const h = harness({persons: [person("subject-1", "阿澈")],
    authorizations: {"subject-1": status("subject-1", [])},
    memberSettings: {"subject-1": {sections: {contact}}}});
  const detail = await open(h, "阿澈", "联系偏好");
  const diagnostic = findAll(detail, "member-schedule-diagnostic")[0];
  assert.ok(diagnostic, "联系页应显示最近自动调度诊断");
  assert.match(flat(diagnostic), /最近自动调度/);
  assert.match(flat(diagnostic), /正常运行/);
  assert.match(flat(diagnostic), /暂时受阻 · 设置/);
  assert.match(flat(diagnostic), /后台自动联系已关闭/);
  assert.match(flat(diagnostic), /打开此窗口的后台自动联系开关/);
  assert.doesNotMatch(flat(diagnostic), /automatic_contact_disabled/);
});

test("私聊联系页提供每日实际联系上限、未回应降频和恢复继承", async () => {
  const fields = {
    enabled: {kind: "boolean"}, automatic_enabled: {kind: "boolean"}, quiet_enabled: {kind: "boolean"},
    max_contacts_24h: {kind: "integer", minimum: 0, maximum: 100},
    interval_seconds: {kind: "integer", minimum: 60, maximum: 604800},
    idle_seconds: {kind: "integer", minimum: 0, maximum: 86400},
    unanswered_slowdown_start: {kind: "integer", minimum: 1, maximum: 10},
    unanswered_pause_after: {kind: "integer", minimum: 0, maximum: 50},
    unanswered_max_interval_multiplier: {kind: "weight", minimum: 1, maximum: 8, decimal_places: 6},
    quiet_start: {kind: "text"}, quiet_end: {kind: "text"}, quiet_timezone: {kind: "text"}, pause_until: {kind: "text"},
    note: {kind: "text"}, style_prompt: {kind: "text", maximum_chars: 4000},
  };
  const settings = {enabled: true, automatic_enabled: true, quiet_enabled: false,
    max_contacts_24h: 5, interval_seconds: 3600, idle_seconds: 900,
    unanswered_slowdown_start: 2, unanswered_pause_after: 5, unanswered_max_interval_multiplier: 3.5,
    quiet_start: "22:00", quiet_end: "08:00", quiet_timezone: "Asia/Shanghai", pause_until: "", note: "", style_prompt: ""};
  const purposeGroups = [
    [["affection", "想念与亲近"], ["care", "情绪关怀"], ["play", "轻松互动"], ["repair", "关系修复"], ["chat", "日常话题"]],
    [["greeting", "问候与纪念"], ["followup", "接回旧话题"], ["invitation", "共同活动邀请"], ["reminder", "约定与提醒"], ["meal", "饭点话题"]],
    [["life", "分享自己的生活"], ["creation", "作品与阅读"], ["news", "外部资讯"], ["environment", "环境与状态变化"], ["screen", "已授权屏幕观察"]],
  ];
  for (const [name] of purposeGroups.flat()) {
    fields[name + ".enabled"] = {kind: "boolean"};
    fields[name + ".weight"] = {kind: "weight"};
    fields[name + ".pause_until"] = {kind: "text"};
    settings[name + ".enabled"] = true;
    settings[name + ".weight"] = 1;
    settings[name + ".pause_until"] = "";
  }
  const inherited = {...settings, unanswered_slowdown_start: 1,
    unanswered_pause_after: 0, unanswered_max_interval_multiplier: 2.2};
  const unanswered = ["unanswered_slowdown_start", "unanswered_pause_after",
    "unanswered_max_interval_multiplier"];
  const provenance = Object.fromEntries(Object.keys(settings).map(name => [name,
    {layer: unanswered.includes(name) ? "window" : "installation", explicit: unanswered.includes(name)}]));
  const contact = {edit_token: "contact-edit-1", fields, settings, provenance};
  let saved;
  const h = harness({persons: [person("subject-1", "阿澈")],
    authorizations: {"subject-1": status("subject-1", [])},
    memberSettings: {"subject-1": {sections: {contact}}}, post: request => {
      saved = request;
      const section = request.body.sections.contact;
      const resets = section.reset_to_inherit || [];
      return {sections: {contact: {...contact, edit_token: "contact-edit-2",
        settings: {...settings, ...section.changes,
          ...Object.fromEntries(resets.map(name => [name, inherited[name]]))},
        provenance: {...provenance, ...Object.fromEntries(resets.map(name => [name,
          {layer: "installation", explicit: false}]))}}}};
    }});
  const detail = await open(h, "阿澈", "联系偏好");
  const contacts = findInput(detail, "每日实际联系上限");
  const slowdown = findInput(detail, "开始降频的未回应次数");
  const pause = findInput(detail, "暂停联系的未回应次数（0 表示不暂停）");
  const pauseUntil = findInput(detail, "临时暂停到（本地时间）");
  const multiplier = findInput(detail, "降频后的最大间隔倍数");
  const note = findInput(detail, "联系补充偏好");
  assert.ok(contacts && slowdown && pause && pauseUntil && multiplier && note);
  for (const [name] of purposeGroups.flat()) {
    assert.ok(inputByName(detail, name + ".enabled"), `${name} 的联系开关应在用户页提供`);
    assert.ok(inputByName(detail, name + ".weight"), `${name} 的联系权重应在用户页提供`);
    assert.ok(inputByName(detail, name + ".pause_until"), `${name} 的临时暂缓应在用户页提供`);
  }
  assert.equal(contacts.type, "number");
  assert.equal(contacts.min, 0);
  assert.equal(contacts.max, 100);
  assert.equal(slowdown.min, 1);
  assert.equal(slowdown.max, 10);
  assert.equal(pause.min, 0);
  assert.equal(pause.max, 50);
  assert.equal(pauseUntil.type, "datetime-local");
  assert.equal(multiplier.min, 1);
  assert.equal(multiplier.max, 8);
  assert.equal(multiplier.step, "0.000001");

  slowdown.value = "3"; slowdown.handlers.input();
  pause.value = "0"; pause.handlers.input();
  pauseUntil.value = "2026-10-20T12:30"; pauseUntil.handlers.input();
  multiplier.value = "4"; multiplier.handlers.input();
  note.value = "尽量避开工作时间联系"; note.handlers.input();
  const newsEnabled = inputByName(detail, "news.enabled");
  newsEnabled.checked = false; newsEnabled.handlers.change();
  const newsWeight = inputByName(detail, "news.weight");
  newsWeight.value = "1.5"; newsWeight.handlers.input();
  const newsPause = inputByName(detail, "news.pause_until");
  newsPause.value = "2026-10-21T18:00"; newsPause.handlers.input();
  const reset = findControl(detail, "恢复继承");
  assert.ok(reset, "显式窗口值应提供恢复继承入口");
  reset.handlers.click();
  const cancelReset = findControl(h.element("person-detail"), "取消恢复");
  assert.ok(cancelReset, "待保存的恢复操作应可撤销");
  cancelReset.handlers.click();
  findControl(h.element("person-detail"), "恢复继承").handlers.click();
  assert.equal(findInput(h.element("person-detail"), "每日实际联系上限").value, 5);
  assert.match(flat(h.element("person-detail")), /保存后恢复继承/);
  findControl(h.element("person-detail"), "保存资料").handlers.click();
  await h.send(request => request.method === "POST");

  assert.equal(saved.route, "acquaintance/settings");
  assert.deepEqual(JSON.parse(JSON.stringify(saved.body.sections.contact.changes)), {
    unanswered_pause_after: 0,
    unanswered_max_interval_multiplier: 4, pause_until: new Date(pauseUntil.value).toISOString(),
    note: "尽量避开工作时间联系", "news.enabled": false, "news.weight": 1.5,
    "news.pause_until": new Date(newsPause.value).toISOString(),
  });
  assert.deepEqual(JSON.parse(JSON.stringify(saved.body.sections.contact.reset_to_inherit)), ["unanswered_slowdown_start"]);
  assert.equal(findInput(h.element("person-detail"), "开始降频的未回应次数").value, 1);
});

test("私聊回复默认继承角色，字段可即时展开并单独覆盖或恢复", async () => {
  const fields = {style_prompt: {kind: "text", maximum_chars: 4000},
    streaming: {kind: "boolean"}, streaming_mode: {kind: "text"},
    file_understanding_enabled: {kind: "boolean"}, file_understanding_format_groups: {kind: "text"}};
  const reply = {edit_token: "reply-edit-1", scope: "window", fields,
    editable_fields: ["style_prompt", "streaming", "streaming_mode"],
    settings: {style_prompt: "这个用户尽量简短", streaming: false, streaming_mode: "buffered",
      file_understanding_enabled: true, file_understanding_format_groups: "文本"},
    inherited_settings: {style_prompt: "保持角色一贯的语气", streaming: false, streaming_mode: "buffered",
      file_understanding_enabled: false, file_understanding_format_groups: "文本"},
    provenance: {style_prompt: {layer: "window", explicit: true},
      streaming: {layer: "role", explicit: false}, streaming_mode: {layer: "role", explicit: false}}};
  const h = harness({persons: [person("subject-1", "阿澈")],
    authorizations: {"subject-1": status("subject-1", [])},
    memberSettings: {"subject-1": {sections: {reply}}}, post: request => ({sections: {reply: {
      ...reply, edit_token: "reply-edit-2",
      settings: {...reply.settings, ...request.body.sections.reply.changes,
        ...(request.body.sections.reply.reset_to_inherit || []).includes("style_prompt")
          ? {style_prompt: reply.inherited_settings.style_prompt} : {}},
      provenance: {...reply.provenance, style_prompt: {layer: "role", explicit: false}},
    }}})});
  const detail = await open(h, "阿澈", "回复微调");
  await h.settle();
  assert.match(flat(detail), /此用户已覆盖/);
  const streaming = findInput(detail, "启用模型流式传输");
  assert.ok(streaming);
  streaming.checked = true;
  streaming.handlers.change();
  const mode = findInput(detail, "流式发送方式");
  assert.ok(mode, "打开流式传输后应立即显示其细项");
  assert.ok(findInput(detail, "可读取文件格式"), "启用文件理解后可单独细调格式");
  mode.value = "incremental";
  mode.handlers.change();
  findControl(detail, "恢复角色默认").handlers.click();
  findControl(detail, "保存回复设置").handlers.click();
  await h.send(request => request.method === "POST");
  const written = h.requests.find(request => request.method === "POST");
  assert.equal(written.route, "acquaintance/settings");
  assert.deepEqual(JSON.parse(JSON.stringify(written.body.sections.reply.changes)),
    {streaming: true, streaming_mode: "incremental"});
  assert.deepEqual(JSON.parse(JSON.stringify(written.body.sections.reply.reset_to_inherit)), ["style_prompt"]);
});

test("旧整段清理范围在成员回复页归一为句中并随保存写回", async () => {
  const fields = {
    content_cleanup_enabled: {kind: "boolean"}, content_cleanup_mode: {kind: "text"},
    content_cleanup_scope: {kind: "text"}, content_cleanup_words: {kind: "text"},
  };
  const reply = {edit_token: "reply-edit-1", scope: "window", fields,
    editable_fields: ["content_cleanup_scope"],
    settings: {content_cleanup_enabled: true, content_cleanup_mode: "words",
      content_cleanup_scope: "all", content_cleanup_words: "嗯"},
    inherited_settings: {content_cleanup_enabled: true, content_cleanup_mode: "words",
      content_cleanup_scope: "middle", content_cleanup_words: "嗯"},
    provenance: {content_cleanup_enabled: {layer: "window", explicit: true},
      content_cleanup_mode: {layer: "window", explicit: true},
      content_cleanup_scope: {layer: "window", explicit: true},
      content_cleanup_words: {layer: "window", explicit: true}}};
  const h = harness({persons: [person("subject-1", "阿澈")],
    authorizations: {"subject-1": status("subject-1", [])},
    memberSettings: {"subject-1": {sections: {reply}}}, post: request => ({sections: {reply: {
      ...reply, edit_token: "reply-edit-2",
      settings: {...reply.settings, ...request.body.sections.reply.changes},
    }}})});
  const detail = await open(h, "阿澈", "回复微调");
  await h.settle();
  const scope = inputByName(detail, "content_cleanup_scope");
  assert.ok(scope, "清理范围应在开启文字清理后出现");
  assert.deepEqual(scope.children.map(option => option.value), ["leading", "middle", "trailing"]);
  assert.equal(scope.children.find(option => option.selected)?.value, "middle",
    "已停用的 all 应显示为规范值 middle，而不是多出一个原始英文选项");
  const save = findControl(detail, "保存回复设置");
  assert.equal(save.disabled, false, "归一后的差异应可直接保存回规范值");
  save.handlers.click();
  await h.send(request => request.method === "POST");
  const written = h.requests.find(request => request.method === "POST");
  assert.deepEqual(JSON.parse(JSON.stringify(written.body.sections.reply.changes)),
    {content_cleanup_scope: "middle"});
});

test("私聊回复配置读取失败时显示未确认状态并禁用保存", async () => {
  const h = harness({persons: [person("subject-1", "阿澈")],
    authorizations: {"subject-1": status("subject-1", [])},
    memberSettings: {"subject-1": {status: "error", data: {code: "simulated_read_failure"}}}});
  const detail = await open(h, "阿澈", "回复微调");
  assert.match(flat(detail), /成员配置读取失败/);
  assert.match(flat(detail), /重新读取/);
  assert.doesNotMatch(flat(detail), /草稿已保留/);
  const save = findControl(detail, "保存回复设置");
  assert.ok(save?.disabled, "配置未读取成功前不能提交窗口覆盖");
  assert.match(flat(findAll(detail, "member-savebar")[0]), /配置读取失败，未确认同步/);
});

test("其他成员配置分区读取失败不影响回复设置读写", async () => {
  const reply = {edit_token: "reply-edit-1", fields: {style_prompt: {kind: "text", maximum_chars: 4000}},
    settings: {style_prompt: "角色默认表达"}, inherited_settings: {style_prompt: "角色默认表达"}, provenance: {}};
  const h = harness({persons: [person("subject-1", "阿澈")],
    authorizations: {"subject-1": status("subject-1", [])},
    memberSettings: {"subject-1": {sections: {reply},
      section_errors: {growth: {code: "member_settings_section_failed"}}}},
    post: request => ({sections: {reply: {...reply, edit_token: "reply-edit-2",
      settings: {...reply.settings, ...request.body.sections.reply.changes}}}})});
  const detail = await open(h, "阿澈", "回复微调");
  const style = findInput(detail, "表达风格补充");
  assert.ok(style);
  assert.equal(style.disabled, false);
  style.value = "仅对这个用户保持温柔";
  style.handlers.input();
  const save = findControl(h.element("person-detail"), "保存回复设置");
  assert.equal(save.disabled, false);
  save.handlers.click();
  await h.send(request => request.method === "POST");
  const written = h.requests.find(request => request.method === "POST");
  assert.deepEqual(Object.keys(written.body.sections), ["reply"]);
  assert.equal(written.body.sections.reply.changes.style_prompt, "仅对这个用户保持温柔");
});

test("失败分区显示错误码与重读入口", async () => {
  const h = harness({persons: [person("subject-1", "阿澈")],
    authorizations: {"subject-1": status("subject-1", [])},
    memberSettings: {"subject-1": {sections: {},
      section_errors: {profile: {code: "member_settings_section_failed"}}}}});
  const detail = await open(h, "阿澈", "人工关系资料");
  assert.match(flat(detail), /关系资料读取失败（member_settings_section_failed）/);
  const reread = findControl(detail, "重新读取");
  assert.ok(reread);
  reread.handlers.click();
  await h.settle();
  assert.equal(h.requests.filter(request => request.method === "GET" && request.route === "acquaintance/settings").length, 2);
});

test("重读时分区失败会清除旧快照并保留草稿", async () => {
  const reply = {edit_token: "reply-edit-1", fields: {style_prompt: {kind: "text", maximum_chars: 4000}},
    settings: {style_prompt: "旧的已保存表达"}, inherited_settings: {style_prompt: "角色默认表达"}, provenance: {}};
  const memberSettings = {"subject-1": {sections: {reply}}};
  const h = harness({persons: [person("subject-1", "阿澈")],
    authorizations: {"subject-1": status("subject-1", [])}, memberSettings});
  const detail = await open(h, "阿澈", "回复微调");
  const style = findInput(detail, "表达风格补充");
  style.value = "仍要保留的未保存草稿";
  style.handlers.input();
  const refresh = detail.querySelectorAll(".member-icon-button")[0];
  refresh.handlers.click();
  memberSettings["subject-1"] = {sections: {}, section_errors: {reply: {code: "member_settings_section_failed"}}};
  await h.send(request => request.method === "GET"
    && (request.route === authorization || request.route === "acquaintance/settings"));
  assert.equal(findInput(h.element("person-detail"), "表达风格补充"), null,
    "失败分区不能继续展示旧已保存值");
  assert.match(flat(h.element("person-detail")), /回复设置读取失败（member_settings_section_failed）/);
  const savebar = findAll(h.element("person-detail"), "member-savebar")[0];
  assert.match(flat(savebar), /1 项草稿待对应分区恢复读取/);
  assert.equal(findControl(savebar, "保存回复设置").disabled, true);
  findControl(h.element("person-detail"), "重新读取").handlers.click();
  memberSettings["subject-1"] = {sections: {reply: {...reply, edit_token: "reply-edit-2"}}};
  await h.send(request => request.method === "GET"
    && (request.route === authorization || request.route === "acquaintance/settings"));
  assert.equal(findInput(h.element("person-detail"), "表达风格补充").value, "仍要保留的未保存草稿");
});

test("保存桥接超时会自动重放同一幂等请求并确认结果", async () => {
  const fields = {style_prompt: {kind: "text", maximum_chars: 4000}};
  const reply = {edit_token: "reply-edit-1", fields, settings: {style_prompt: "初始表达"},
    inherited_settings: {style_prompt: "角色默认"},
    provenance: {style_prompt: {layer: "window", explicit: true}}};
  const memberSettings = {"subject-1": {sections: {reply}}};
  let posts = 0;
  const h = harness({persons: [person("subject-1", "阿澈")],
    authorizations: {"subject-1": status("subject-1", [])}, memberSettings,
    post: request => {
      posts++;
      if (posts === 1) throw Object.assign(new Error("network timeout"), {code: "bridge_timeout"});
      return {sections: {reply: {...reply, edit_token: "reply-edit-3",
        settings: {...reply.settings, ...request.body.sections.reply.changes}}}};
    }});
  const detail = await open(h, "阿澈", "回复微调");
  assert.equal(h.requests.find(request => request.method === "GET" && request.route === "acquaintance/settings")
    ?.requestOptions?.timeoutMs, 60000, "成员设置读请求也使用同一较宽时限");
  const input = findInput(detail, "表达风格补充");
  input.value = "这次要保存的表达";
  input.handlers.input();
  findControl(h.element("person-detail"), "保存回复设置").handlers.click();
  await h.send(request => request.method === "POST");
  const first = h.requests.find(request => request.method === "POST");
  assert.equal(first.requestOptions.timeoutMs, 60000, "成员设置写请求使用较宽的桥接时限");
  await h.settle();
  const attempts = h.requests.filter(request => request.method === "POST");
  assert.equal(attempts.length, 2, "桥接超时后页面自动进行一次安全恢复");
  assert.match(flat(h.element("person-detail")), /正在核对保存结果/);
  assert.deepEqual(JSON.parse(JSON.stringify(attempts[1].body)), JSON.parse(JSON.stringify(first.body)));
  assert.equal(attempts[1].body.operation_key, first.body.operation_key,
    "自动恢复必须复用服务端幂等操作编号");
  await h.send(request => request.method === "POST" && request !== first);
  assert.equal(attempts.length, 2);
  assert.match(flat(h.element("person-detail")), /私聊回复设置已保存/);
  assert.equal(posts, 2);
});

test("自动核对仍超时后保留原操作，服务端确认拒绝时不重发", async () => {
  const fields = {style_prompt: {kind: "text", maximum_chars: 4000}};
  const reply = {edit_token: "reply-edit-1", fields, settings: {style_prompt: "初始表达"},
    inherited_settings: {style_prompt: "角色默认"},
    provenance: {style_prompt: {layer: "window", explicit: true}}};
  const memberSettings = {"subject-1": {sections: {reply}}};
  let posts = 0;
  const h = harness({persons: [person("subject-1", "阿澈")],
    authorizations: {"subject-1": status("subject-1", [])}, memberSettings,
    post: () => {posts++; throw Object.assign(new Error("network timeout"), {code: "bridge_timeout"});}});
  const detail = await open(h, "阿澈", "回复微调");
  const input = findInput(detail, "表达风格补充");
  input.value = "待核对内容";
  input.handlers.input();
  findControl(detail, "保存回复设置").handlers.click();
  await h.send(request => request.method === "POST");
  await h.send(request => request.method === "POST");
  assert.equal(posts, 2, "最多自动恢复一次");
  assert.match(flat(h.element("person-detail")), /保存结果仍未确认/);
  assert.ok(findControl(h.element("person-detail"), "核对并重试保存"));
  assert.equal(findInput(h.element("person-detail"), "表达风格补充").disabled, true);

  const rejected = harness({persons: [person("subject-1", "阿澈")],
    authorizations: {"subject-1": status("subject-1", [])}, memberSettings,
    post: () => Object.assign(new Error("invalid setting"), {code: "invalid_configuration_value", confirmed: true})});
  const rejectedDetail = await open(rejected, "阿澈", "回复微调");
  const rejectedInput = findInput(rejectedDetail, "表达风格补充");
  rejectedInput.value = "被拒绝的内容";
  rejectedInput.handlers.input();
  findControl(rejectedDetail, "保存回复设置").handlers.click();
  await rejected.send(request => request.method === "POST");
  assert.equal(rejected.requests.filter(request => request.method === "POST").length, 1,
    "服务端明确拒绝时不能进入自动重放");
  assert.equal(findInput(rejected.element("person-detail"), "表达风格补充").disabled, false,
    "服务端明确拒绝后草稿恢复可编辑");
});

test("服务端无法恢复操作回执时保留待核对请求", async () => {
  const fields = {style_prompt: {kind: "text", maximum_chars: 4000}};
  const reply = {edit_token: "reply-edit-1", fields, settings: {style_prompt: "初始表达"},
    inherited_settings: {style_prompt: "角色默认"},
    provenance: {style_prompt: {layer: "window", explicit: true}}};
  let posts = 0;
  const h = harness({persons: [person("subject-1", "阿澈")],
    authorizations: {"subject-1": status("subject-1", [])},
    memberSettings: {"subject-1": {sections: {reply}}},
    post: () => {
      posts++;
      return posts === 1 ? Promise.reject(Object.assign(new Error("timeout"), {code: "bridge_timeout"}))
        : {status: "error", data: {code: "configuration_operation_incomplete"}};
    }});
  const detail = await open(h, "阿澈", "回复微调");
  const input = findInput(detail, "表达风格补充");
  input.value = "需要核对的内容";
  input.handlers.input();
  findControl(detail, "保存回复设置").handlers.click();
  await h.send(request => request.method === "POST");
  await h.send(request => request.method === "POST");
  assert.equal(posts, 2);
  assert.match(flat(h.element("person-detail")), /仍在恢复原保存回执/);
  assert.ok(findControl(h.element("person-detail"), "核对并重试保存"));
  assert.equal(findInput(h.element("person-detail"), "表达风格补充").disabled, true,
    "未知写入状态不能被误判为确定拒绝");
});

test("核对读取失败会保留待确认请求，明确服务端拒绝后解除字段锁定", async () => {
  const fields = {style_prompt: {kind: "text", maximum_chars: 4000}};
  const reply = {edit_token: "reply-edit-1", fields, settings: {style_prompt: "初始表达"},
    inherited_settings: {style_prompt: "角色默认"}, provenance: {style_prompt: {layer: "window", explicit: true}}};
  const memberSettings = {"subject-1": {sections: {reply}}};
  let posts = 0;
  const h = harness({persons: [person("subject-1", "阿澈")],
    authorizations: {"subject-1": status("subject-1", [])}, memberSettings,
    post: () => {
      posts++;
      if (posts === 1) throw Object.assign(new Error("network timeout"), {code: "simulated_timeout"});
      return {status: "error", data: {code: "simulated_rejection"}};
    }});
  const detail = await open(h, "阿澈", "回复微调");
  const input = findInput(detail, "表达风格补充");
  input.value = "待确认内容";
  input.handlers.input();
  findControl(detail, "保存回复设置").handlers.click();
  await h.send(request => request.method === "POST");
  assert.equal(posts, 1);

  memberSettings["subject-1"] = {status: "error", data: {code: "simulated_read_failure"}};
  findControl(h.element("person-detail"), "核对并重试保存").handlers.click();
  await h.send(request => request.method === "GET"
    && (request.route === authorization || request.route === "acquaintance/settings"));
  assert.equal(posts, 1, "配置未读成功时不应重发写请求");
  assert.ok(findControl(h.element("person-detail"), "核对并重试保存"), "待核对请求必须保留");
  assert.equal(findInput(h.element("person-detail"), "表达风格补充"), null,
    "读取失败时编辑区保持隐藏，避免展示未经核对的配置");

  memberSettings["subject-1"] = {sections: {reply: {...reply, edit_token: "reply-edit-2"}}};
  findControl(h.element("person-detail"), "核对并重试保存").handlers.click();
  await h.send(request => request.method === "GET"
    && (request.route === authorization || request.route === "acquaintance/settings"));
  await h.send(request => request.method === "POST" && posts === 1);
  assert.equal(posts, 2);
  assert.equal(findInput(h.element("person-detail"), "表达风格补充").disabled, false,
    "明确拒绝后本地草稿仍在，但字段应恢复可编辑");
  assert.ok(findControl(h.element("person-detail"), "保存回复设置"));
});

test("熟人卡只管理互动记忆，人工关系资料独立呈现", async () => {
  const fields = {
    acquaintance_enabled: {kind: "boolean"},
    acquaintance_card_chars: {kind: "integer", minimum: 100, maximum: 4000},
  };
  const learningFields = {acquaintance_learning_enabled: {kind: "boolean"}};
  const memberSettings = {"subject-1": {sections: {
    person: {edit_token: "person-edit-1", fields, scope: "relationship", layers: ["installation", "role", "relationship"],
      settings: {acquaintance_enabled: true, acquaintance_card_chars: 600},
      inherited_settings: {acquaintance_enabled: true, acquaintance_card_chars: 800},
      provenance: {acquaintance_enabled: {explicit: false}, acquaintance_card_chars: {explicit: false}}},
    person_learning: {edit_token: "learning-edit-1", fields: learningFields,
      settings: {acquaintance_learning_enabled: true},
      provenance: {acquaintance_learning_enabled: {explicit: false}}},
  }}};
  const h = harness({persons: [person("subject-1", "阿澈")], memberSettings,
    authorizations: {"subject-1": status("subject-1", [])}, post: request => {
      const changes = request.body.sections;
      const personChanges = changes.person?.changes || {};
      const learningChanges = changes.person_learning?.changes || {};
      const reset = changes.person?.reset_to_inherit || [];
      return {sections: {
        person: {edit_token: "person-edit-2", fields, scope: "relationship", layers: ["installation", "role", "relationship"],
          settings: {...memberSettings["subject-1"].sections.person.settings, ...personChanges,
            ...Object.fromEntries(reset.map(name => [name, memberSettings["subject-1"].sections.person.inherited_settings[name]]))},
          inherited_settings: memberSettings["subject-1"].sections.person.inherited_settings,
          provenance: Object.fromEntries(Object.keys(fields).map(name => [name, {explicit: !reset.includes(name)
            && (name in personChanges || memberSettings["subject-1"].sections.person.provenance[name].explicit)}]))},
        person_learning: {edit_token: "learning-edit-2", fields: learningFields,
          settings: {...memberSettings["subject-1"].sections.person_learning.settings, ...learningChanges},
          provenance: {acquaintance_learning_enabled: {explicit: "acquaintance_learning_enabled" in learningChanges}}},
      }};
    }});
  const detail = await open(h, "阿澈", "熟人卡");
  assert.ok(findInput(detail, "使用熟人卡"));
  assert.ok(findInput(detail, "持续更新熟人卡"));
  assert.ok(findInput(detail, "熟人卡字数上限"));
  assert.doesNotMatch(flat(detail), /称呼与关系|相处方式|对用户的称呼/);
  findControl(detail, "联系偏好").handlers.click();
  assert.doesNotMatch(flat(detail), /使用熟人卡|持续更新熟人卡/);
  findControl(detail, "熟人卡").handlers.click();
  const enabled = findInput(detail, "使用熟人卡");
  assert.equal(enabled.checked, true);
  enabled.checked = false;
  enabled.handlers.change();
  const learning = findInput(detail, "持续更新熟人卡");
  learning.checked = false;
  learning.handlers.change();
  findControl(detail, "保存资料").handlers.click();
  await h.send(request => request.method === "POST");
  const saved = h.requests.find(request => request.method === "POST");
  assert.equal(saved.route, "acquaintance/settings");
  assert.deepEqual(JSON.parse(JSON.stringify(saved.body.sections.person.changes)), {acquaintance_enabled: false});
  assert.deepEqual(JSON.parse(JSON.stringify(saved.body.sections.person_learning.changes)),
    {acquaintance_learning_enabled: false});

  const restore = findControl(h.element("person-detail"), "恢复角色默认");
  assert.ok(restore);
  restore.handlers.click();
  findControl(h.element("person-detail"), "保存资料").handlers.click();
  await h.send(request => request.method === "POST");
  const reset = h.requests.filter(request => request.method === "POST").at(-1);
  assert.deepEqual(JSON.parse(JSON.stringify(reset.body.sections.person.reset_to_inherit)), ["acquaintance_enabled"]);
});

test("核心用户的人工关系资料只读，普通成员在独立页签编辑", async () => {
  const fields = Object.fromEntries(["default_nickname", "user_gender", "user_birthday", "user_occupation",
    "role_relation", "interaction", "extra"].map(name => [name, {kind: "text", maximum_chars: 900}]));
  const profile = {edit_token: "profile-edit-1", fields, settings: {default_nickname: "小屋主人",
    user_gender: "未说明", user_birthday: "", user_occupation: "", role_relation: "核心用户",
    interaction: "已经确认的相处方式", extra: ""}};
  const memberSettings = {"subject-1": {sections: {profile}}};
  const core = {...status("subject-1", []), identity_tier: "core"};
  const h = harness({persons: [person("subject-1", "阿澈", {identity_tier: "core"})], memberSettings,
    authorizations: {"subject-1": core}});
  let detail = await open(h, "阿澈", "人工关系资料");
  assert.doesNotMatch(flat(detail), /核心用户的关系资料在自我页编辑/);
  assert.equal(findControl(detail, "熟人卡"), null);
  assert.match(flat(detail), /已经确认的相处方式/);
  assert.equal(findInput(detail, "对用户的称呼"), null);
  assert.doesNotMatch(flat(detail), /保存资料/);

  const regular = {...person("subject-2", "阿澈的朋友"), subject_id: "subject-2", native_account_id: "subject-2"};
  const regularProfile = {...profile, edit_token: "profile-edit-2"};
  const regularSettings = {"subject-2": {sections: {profile: regularProfile}}};
  const other = harness({persons: [regular], memberSettings: regularSettings,
    authorizations: {"subject-2": status("subject-2", [])},
    post: request => ({sections: {profile: {...regularProfile,
      edit_token: "profile-edit-3", settings: {...regularProfile.settings, ...request.body.sections.profile.changes}}}})});
  detail = await open(other, "阿澈的朋友", "人工关系资料");
  const nickname = findInput(detail, "对用户的称呼");
  assert.ok(nickname);
  nickname.value = "阿澈的小伙伴";
  nickname.handlers.input();
  findControl(detail, "保存资料").handlers.click();
  await other.send(request => request.method === "POST");
  const saved = other.requests.find(request => request.method === "POST");
  assert.equal(saved.route, "acquaintance/settings");
  assert.deepEqual(JSON.parse(JSON.stringify(saved.body.sections.profile.changes)), {default_nickname: "阿澈的小伙伴"});
});

test("切换发出的就是这一项、这个人、这一次读到的 revision", async () => {
  const people = [person("subject-1", "阿澈")];
  const h = harness({ persons: people, authorizations: { "subject-1": status("subject-1", [
    grantRow("proactive.contact", { policy_revision: 11 }),
  ]) } });
  const detail = await open(h, "阿澈");
  const control = findPermission(detail, "proactive.contact");
  assert.ok(control?.handlers?.change, "应当有一个权限开关");
  control.checked = false;
  control.handlers.change();
  await h.settle();
  const written = h.requests.find((item) => item.method === "POST");
  assert.ok(written, "点击必须真的发出写请求");
  assert.equal(written.route, authorization);
  assert.equal(written.body.subject_id, "subject-1");
  assert.equal(written.body.capability, "proactive.contact");
  assert.equal(written.body.enabled, false);
  /* revision 必须来自**这一次读到的**那一行：取错就会被后端以冲突拒绝，
     用户看到的是"点了没反应"。 */
  assert.equal(written.body.expected_revision, 11);
  assert.equal(typeof written.body.operation_key, "string");
  assert.ok(written.body.operation_key.length > 0);
});

test("写回的是服务端返回的状态，页面不自己改本地副本", async () => {
  const people = [person("subject-1", "阿澈")];
  const h = harness({ persons: people, authorizations: { "subject-1": status("subject-1", [
    grantRow("proactive.contact"),
  ]) }, post: () => ({ status: "ok", data: { replayed: false, operation_id: "op-1",
    status: status("subject-1", [grantRow("proactive.contact", { state: "revoked",
      policy_revision: 12, grant_revision: 2 })]) } }) });
  const detail = await open(h, "阿澈");
  const toggle = findPermission(detail, "proactive.contact");
  toggle.checked = false; toggle.handlers.change();
  await h.send((request) => request.method === "POST");
  const html = markup(h.element("person-detail"));
  assert.match(html, /data-capability="proactive\.contact"[^>]*data-state="revoked"/);
  /* 新的 revision 已经就位：下一次点击用的是 12，不是上一次的 7。 */
  const control = findPermission(h.element("person-detail"), "proactive.contact");
  assert.equal(control.checked, false, "服务端确认撤销后开关应关闭");
});

test("一项授权都没有的人得到一句说明，而不是一排空行", async () => {
  const people = [person("subject-1", "阿澈")];
  const h = harness({ persons: people, authorizations: { "subject-1": status("subject-1", []) } });
  const detail = await open(h, "阿澈");
  const flat = JSON.stringify(text(detail));
  assert.match(flat, /还没有单独签发或撤销过能力/);
  /* 空态不能说成"他什么都不能"：准入还有运行实例里的默认口径，页面读不到那一层。 */
  assert.match(flat, /实际准入还取决于运行实例默认值和角色配置/);
  assert.doesNotMatch(flat, /不能接收消息/);
  assert.doesNotMatch(markup(detail), /data-capability=/, "空态不许画成一排空行");
});

test("身份与生效权限由服务端确认，并仅更新对应成员", async () => {
  const people = [person("subject-1", "阿澈"), person("subject-2", "小满")];
  const core = {...status("subject-1", [grantRow("conversation.reply", {state: "revoked",
    effective_state: "active", identity_override: true})]), identity_tier: "core"};
  const h = harness({ persons: people, authorizations: {
    "subject-1": core,
    "subject-2": status("subject-2", [grantRow("conversation.reply", {state: "revoked"})]),
  } });
  const detail = await open(h, "阿澈");
  assert.match(flat(detail), /核心用户/);
  assert.match(flat(detail), /由当前管理身份自动允许/);
  const overridden = findPermission(detail, "conversation.reply");
  assert.equal(overridden.checked, true);
  assert.equal(overridden.disabled, true);
  const badges = findAll(h.element("people-grid"), "badge companion");
  assert.equal(badges.length, 1);
  assert.equal(flat(badges[0]), "核心用户");
  const other = cardFor(h.element("people-grid"), "小满");
  other.handlers.click();
  await h.send((request) => request.route === authorization || request.route === "acquaintance/settings");
  findControl(h.element("person-detail"), "身份与权限").handlers.click();
  const otherDetail = h.element("person-detail");
  assert.equal(findPermission(otherDetail, "conversation.reply").checked, false);
  assert.doesNotMatch(flat(otherDetail), /由当前管理身份自动允许/);
  assert.doesNotMatch(flat(other), /核心用户/);
});

test("用户页明确显示管理员权限，设为核心用户仍沿身份接口保存", async () => {
  const people = [person("subject-1", "阿澈")];
  let saved;
  const h = harness({persons: people, authorizations: {"subject-1": status("subject-1", [])},
    post: request => {
      saved = request;
      return {status: "ok", data: {replayed: false, operation_id: "identity-op",
        status: {...status("subject-1", []), identity_tier: "core", identity_revision: 2}}};
    }});
  const detail = await open(h, "阿澈");
  assert.match(flat(detail), /管理员权限/);
  assert.match(flat(detail), /核心用户就是核心管理员/);
  assert.match(flat(detail), /未开启/);
  assert.match(flat(detail), /重要用户和普通用户不会获得管理员权限/);
  assert.ok(findAll(detail, "member-admin-row").length === 1);

  findControl(detail, "设为核心用户").handlers.click();
  assert.match(flat(detail), /待保存 · 将开启/);
  findControl(detail, "保存身份").handlers.click();
  await h.send(request => request.method === "POST");
  assert.equal(saved.route, "acquaintance/identity");
  assert.equal(saved.body.tier, "core");
  assert.equal(saved.body.expected_revision, 1);
  assert.match(flat(h.element("person-detail")), /已开启 · 核心管理员/);
});

test("缺少显示名称与未知能力状态时不会回显内部标识", async () => {
  const h = harness({persons: [person("subject-1", "阿澈")], authorizations: {
    "subject-1": status("subject-1", [grantRow("internal.capability", {state: "internal_state"})]),
  }});
  const detail = await open(h, "阿澈");
  assert.match(flat(detail), /当前角色/);
  assert.match(flat(detail), /其他能力/);
  assert.match(flat(detail), /待核对/);
  assert.doesNotMatch(flat(detail), /role-a|account-a|bot-a|internal\.capability|internal_state/);
  assert.doesNotMatch(flat(h.card("阿澈")), /role-a|account-a|bot-a/);
});

test("关系网和私聊、群聊视图按 V2 记录互相跳转", async () => {
  const member = person("subject-1", "阿澈", {
    group_member: true,
    groups: {
      "1001": { msg_count: 8, active_days: 3, last_seen: "2026-09-30T10:00:00+00:00" },
      "1002": { msg_count: 2, active_days: 1, last_seen: "2026-09-28T10:00:00+00:00" },
    },
    total: { msg_count: 22, active_days: 5,
      first_seen: "2026-09-01T10:00:00+00:00", last_seen: "2026-09-30T10:00:00+00:00" },
  });
  const h = harness({ persons: [member], groups: [
    { group_id: "1001", platform_id: "qq-main", bot_id: "bot-a", account_id: "account-a",
      member_count: 1, msg_count: 8 },
  ], authorizations: { "subject-1": status("subject-1", []) } });
  await h.send((request) => request.route === endpoint);

  const graph = h.element("relationship-graph");
  assert.match(graph.innerHTML, /data-group-id="1001"[^]*已自动接入/);
  assert.match(graph.innerHTML, /data-group-id="1002"[^]*历史群内记录/);
  assert.match(graph.innerHTML, /class="graph-edge group"/);
  assert.match(graph.innerHTML, /不推断成员彼此关系/);
  assert.equal(h.element("count-total").textContent, "1");
  assert.equal(h.element("count-both").textContent, "1");

  const groupTarget = { dataset: { groupId: "1001" }, closest(selector) {
    return selector === "[data-group-id]" ? this : null;
  } };
  graph.handlers.click({ target: groupTarget });
  assert.equal(h.document.querySelectorAll(".tab")[2].getAttribute("aria-selected"), "true");
  assert.match(flat(h.element("group-detail")), /群 1001/);
  const memberLink = findAll(h.element("group-detail"), "member-chip")[0];
  assert.ok(memberLink?.handlers?.click, "群成员应当可打开统一人物详情");
  memberLink.handlers.click();
  await h.send((request) => request.route === authorization || request.route === "acquaintance/settings");
  assert.equal(h.document.querySelectorAll(".tab")[1].getAttribute("aria-selected"), "true");
  assert.match(flat(h.element("person-detail")), /阿澈/);
  /* 熟人卡不是配置分区，而是名片上的一张卡：默认收起，点开是独立弹窗。 */
  const cardTrigger = findControl(h.element("person-detail"), "熟人卡");
  assert.equal(cardTrigger.getAttribute("aria-haspopup"), "dialog");
  assert.equal(cardTrigger.getAttribute("aria-expanded"), "false");
  assert.equal(findControl(h.element("person-detail"), "身份与权限").getAttribute("aria-selected"), "true");
  cardTrigger.handlers.click();
  assert.equal(findControl(h.element("person-detail"), "熟人卡").getAttribute("aria-expanded"), "true");
  assert.match(flat(h.element("person-detail")), /自动积累|持续更新熟人卡/);

  findControl(h.element("person-detail"), "往来记录").handlers.click();
  const contextGroup = findAll(h.element("person-detail"), "context-group").find((item) => flat(item).includes("1001"));
  assert.ok(contextGroup?.handlers?.click, "成员详情中的群记录应当可返回对应群详情");
  assert.doesNotThrow(() => contextGroup.handlers.click());
  assert.equal(h.document.querySelectorAll(".tab")[2].getAttribute("aria-selected"), "true");
  assert.match(flat(h.element("group-detail")), /群 1001/);

  const historicCard = findAll(h.element("groups-list"), "group-card").find((item) => flat(item).includes("1002"));
  assert.ok(historicCard?.handlers?.click, "历史群即使不在当前运行实例目标里也应可查看");
  historicCard.handlers.click();
  assert.match(flat(h.element("group-detail")), /历史群内往来记录/);
});

/* 按类名找节点：徽章是逐卡渲染的，断言要落在每个节点上，
   而不是落在"整棵树里出现过这个词"——后者读不出是哪一张卡。 */
function findAll(node, className) {
  const found = [];
  const stack = [node];
  while (stack.length) {
    const item = stack.shift();
    if (item.className === className) found.push(item);
    stack.push(...(item.children || []));
  }
  return found;
}

function findControl(node, label) {
  const stack = [node];
  while (stack.length) {
    const item = stack.shift();
    if (item.tagName === "BUTTON" && flat(item).includes(label)) return item;
    stack.push(...(item.children || []));
  }
  return null;
}

function findInput(node, label) {
  if (["INPUT", "TEXTAREA", "SELECT"].includes(node.tagName) && node.getAttribute("aria-label") === label) return node;
  for (const child of node.children || []) {
    const input = findInput(child, label);
    if (input) return input;
  }
  return null;
}

function inputByName(node, name) {
  const stack = [node];
  while (stack.length) {
    const item = stack.shift();
    if (["INPUT", "TEXTAREA", "SELECT"].includes(item.tagName) && item.name === name) return item;
    stack.push(...(item.children || []));
  }
  return null;
}

function findPermission(node, capability) {
  if (node.dataset?.capability === capability) {
    return (node.children || []).find(child => child.tagName === "INPUT" && child.type === "checkbox");
  }
  for (const child of node.children || []) {
    const control = findPermission(child, capability);
    if (control) return control;
  }
  return null;
}


test("原生窗口不冒充私聊用户，已迁入用户与目录计数一致", async () => {
  const migrated = person("old", "旧版用户", {private_chat: false, private_contact: true,
    legacy_private_contact: true, private: null, total: {msg_count: 0}});
  const discovered = Array.from({length: 76}, (_, i) => person("route-" + i, "宿主会话" + i, {
    private_chat: false, private_target: true, private: null, total: {msg_count: 0}}));
  const h = harness({persons: [migrated, ...discovered]});
  await h.send();
  assert.equal(h.element("count-total").textContent, "1");
  assert.equal(h.element("count-private").textContent, "1");
  assert.equal(h.element("chip-stable").textContent, "1");
  assert.equal(h.element("chip-discovered").textContent, "76");
  assert.equal(h.element("people-result-count").textContent, "1 人");
  assert.match(flat(h.element("people-grid")), /旧版用户/);
  assert.doesNotMatch(flat(h.element("people-grid")), /宿主会话/);
  h.document.querySelectorAll(".chip")[2].handlers.click();
  assert.equal(h.element("people-result-count").textContent, "76 人");
  assert.match(flat(h.element("people-grid")), /宿主会话0/);
});


/* ── 从被拒记录添加具体私聊目标 ───────────────────────────────────────
   这一行的危险在于"点错一条记录就把联系人加到了别的 Bot 上"，所以断言落在
   确认框里写死的路由、真正发出去的 body，以及刷新后的行状态。 */

const strangersPath = "acquaintance/strangers";
const strangerTargetPath = "acquaintance/stranger-target";

function refused(peer, changes = {}) {
  return {platform_id: "qq-main", login: "account-a", peer,
    first_seen_at: "2026-11-03T10:00:00+00:00", last_seen_at: "2026-11-03T10:05:00+00:00",
    attempts: 2, last_outcome: "stranger_notice_sent", target_state: "addable", target_id: null, ...changes};
}

function listRow(h, peer) {
  return h.element("stranger-list").children.find((row) => flat(row).includes(`账号 ${peer}`)) || null;
}

function firstButton(node) {
  const stack = [...(node.children || [])];
  while (stack.length) {
    const item = stack.shift();
    if (item.tagName === "BUTTON") return item;
    stack.push(...(item.children || []));
  }
  return null;
}

function buttonIn(node, label) {
  const stack = [...(node.children || [])];
  while (stack.length) {
    const item = stack.shift();
    if (item.tagName === "BUTTON" && item.textContent === label) return item;
    stack.push(...(item.children || []));
  }
  return null;
}

function openDialog(h) {
  return h.body.children.find((child) => child.tagName === "DIALOG" && child.isConnected) || null;
}

async function openRefused(h) {
  await h.send();
  await h.send((request) => request.route === strangersPath);
  return h.element("stranger-list");
}

test("被拒记录按当前配置显示可添加、已添加、已覆盖与收到方不可用", async () => {
  const h = harness({persons: [person("subject-1", "已知成员")], strangers: [
    refused("999"),
    refused("998", {target_state: "added", target_id: "private-existing"}),
    refused("997", {target_state: "covered"}),
    refused("996", {target_state: "unavailable"})]});

  const list = await openRefused(h);

  assert.equal(h.element("stranger-contacts").hidden, false);
  assert.equal(h.element("stranger-count").textContent, "4 人");
  assert.match(flat(listRow(h, "999")), /账号 999 · 收到方 account-a/);
  assert.match(flat(listRow(h, "999")), /平台 qq-main/);
  assert.equal(firstButton(listRow(h, "999")).disabled, false);
  for (const [peer, label] of [["998", "已添加"], ["997", "已覆盖"], ["996", "收到方不可用"]]) {
    const row = listRow(h, peer);
    assert.match(flat(row), new RegExp(label), peer + " 行应显示 " + label);
    assert.equal(firstButton(row).disabled, true, label + " 行不应还能再添加");
  }
});

test("确认框固定这条记录的账号、收到方与平台，取消不写配置", async () => {
  const h = harness({strangers: [refused("999")]});
  await openRefused(h);

  firstButton(listRow(h, "999")).handlers.click();
  await h.settle();

  const dialog = openDialog(h);
  assert.ok(dialog, "点「允许此账号私聊」应当打开确认框");
  const copy = flat(dialog);
  assert.match(copy, /发信账号 999/);
  assert.match(copy, /收到方 account-a/);
  assert.match(copy, /平台 qq-main/);
  assert.match(copy, /仅添加该账号到此收到方，权限沿用现有设置。/);

  buttonIn(dialog, "取消").handlers.click();
  await h.settle();

  assert.equal(openDialog(h), null, "取消后确认框应当关闭");
  assert.equal(h.requests.filter((request) => request.method === "POST").length, 0);
});

test("确认后只提交所点路由，提交中不再接受第二次添加", async () => {
  const h = harness({strangers: [refused("999"), refused("998")]});
  await openRefused(h);

  firstButton(listRow(h, "999")).handlers.click();
  await h.settle();
  buttonIn(openDialog(h), "确认添加").handlers.click();
  await h.settle();

  const posts = h.requests.filter((request) => request.method === "POST" && request.route === strangerTargetPath);
  assert.equal(posts.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(posts[0].body)),
    {platform_id: "qq-main", login: "account-a", peer: "999"});
  assert.equal(posts[0].requestOptions?.timeoutMs, 60000);
  assert.equal(rowButtonDisabled(h, "999"), true);
  assert.match(flat(listRow(h, "999")), /正在添加…/);
  assert.equal(rowButtonDisabled(h, "998"), true, "提交期间其他行的添加也要停住");

  firstButton(listRow(h, "998")).handlers.click();
  await h.settle();
  assert.equal(h.requests.filter((request) => request.method === "POST" && request.route === strangerTargetPath).length, 1);

  h.strangerList.items = [refused("999", {target_state: "added", target_id: "private-new"}), refused("998")];
  posts[0].resolve({status: "ok", data: {state: "added", target_id: "private-new", saved: true,
    requires_reload: false, requires_initialize: false, started: true, startup_error: null}});
  await h.settle();
  await h.send((request) => request.route === strangersPath);

  assert.equal(h.element("notice").textContent, "已添加，权限沿用现有设置");
  assert.doesNotMatch(h.element("notice").className, /error/);
  assert.match(flat(listRow(h, "999")), /已添加/);
  assert.equal(rowButtonDisabled(h, "999"), true);
  assert.equal(rowButtonDisabled(h, "998"), false, "提交结束后其他行恢复可用");
});

function rowButtonDisabled(h, peer) {
  return firstButton(listRow(h, peer)).disabled;
}

test("已覆盖、收到方不可用与初始化失败各有对应提示", async () => {
  const covered = harness({strangers: [refused("999")]});
  await openRefused(covered);
  firstButton(listRow(covered, "999")).handlers.click();
  await covered.settle();
  buttonIn(openDialog(covered), "确认添加").handlers.click();
  await covered.settle();
  covered.strangerList.items = [refused("999", {target_state: "covered"})];
  covered.requests.find((request) => request.route === strangerTargetPath)
    .resolve({status: "ok", data: {state: "covered", target_id: null, saved: false,
      requires_reload: false, requires_initialize: false, started: false, startup_error: null}});
  await covered.settle();
  await covered.send((request) => request.route === strangersPath);
  assert.match(covered.element("notice").textContent, /已覆盖/);
  assert.match(flat(listRow(covered, "999")), /已覆盖/);

  const unavailable = harness({strangers: [refused("999")]});
  await openRefused(unavailable);
  firstButton(listRow(unavailable, "999")).handlers.click();
  await unavailable.settle();
  buttonIn(openDialog(unavailable), "确认添加").handlers.click();
  await unavailable.settle();
  unavailable.strangerList.items = [refused("999", {target_state: "unavailable"})];
  unavailable.requests.find((request) => request.route === strangerTargetPath)
    .resolve({status: "ok", data: {state: "unavailable", target_id: null, saved: false,
      requires_reload: false, requires_initialize: false, started: false, startup_error: null}});
  await unavailable.settle();
  await unavailable.send((request) => request.route === strangersPath);
  const unavailableCopy = unavailable.element("notice").textContent;
  assert.match(unavailableCopy, /收到方不可用/);
  assert.match(unavailableCopy, /未配置或当前连接已断开/, "文案要同时覆盖未配置与断线两种收到方");
  assert.doesNotMatch(unavailableCopy, /不在当前 Bot 账号配置/);
  assert.doesNotMatch(unavailable.element("notice").className, /error/);
  assert.equal(rowButtonDisabled(unavailable, "999"), true);
  assert.match(firstButton(listRow(unavailable, "999")).title, /未配置或未连接/);

  const late = harness({strangers: [refused("999")]});
  await openRefused(late);
  firstButton(listRow(late, "999")).handlers.click();
  await late.settle();
  buttonIn(openDialog(late), "确认添加").handlers.click();
  await late.settle();
  late.strangerList.items = [refused("999", {target_state: "added", target_id: "private-new"})];
  late.requests.find((request) => request.route === strangerTargetPath)
    .resolve({status: "ok", data: {state: "added", target_id: "private-new", saved: true,
      requires_reload: false, requires_initialize: true, started: false,
      startup_error: "host_provider_required"}});
  await late.settle();
  await late.send((request) => request.route === strangersPath);
  const lateCopy = late.element("notice").textContent;
  assert.match(lateCopy, /^已添加，权限沿用现有设置/);
  assert.match(lateCopy, /宿主需要初始化/);
  assert.match(lateCopy, /host_provider_required/);
  assert.match(late.element("notice").className, /error/);
});

test("配置冲突显示可重试原因，并按重新读取的状态恢复操作", async () => {
  const h = harness({strangers: [refused("999")]});
  await openRefused(h);
  const listReads = () => h.requests.filter((request) => request.route === strangersPath).length;
  const before = listReads();

  firstButton(listRow(h, "999")).handlers.click();
  await h.settle();
  buttonIn(openDialog(h), "确认添加").handlers.click();
  await h.settle();
  h.strangerList.items = [refused("999", {target_state: "added", target_id: "private-other-admin"})];
  h.requests.find((request) => request.route === strangerTargetPath)
    .reject(Object.assign(new Error("插件配置在等待保存期间已变化"), {code: "legacy_plan_stale"}));
  await h.settle();
  await h.send((request) => request.route === strangersPath);

  assert.match(h.element("notice").textContent, /配置刚被其他操作更新/);
  assert.match(h.element("notice").className, /error/);
  assert.ok(listReads() > before, "失败后应重新读取被拒记录核对实际状态");
  assert.match(flat(listRow(h, "999")), /已添加/);
  assert.equal(rowButtonDisabled(h, "999"), true);
});

test("目标被移除后刷新恢复可添加", async () => {
  const h = harness({strangers: [refused("999", {target_state: "added", target_id: "private-old"})]});
  await openRefused(h);
  assert.equal(rowButtonDisabled(h, "999"), true);

  h.strangerList.items = [refused("999")];
  h.element("refresh").handlers.click();
  await h.settle();
  await h.send();
  await h.send((request) => request.route === strangersPath);

  assert.equal(rowButtonDisabled(h, "999"), false);
  assert.doesNotMatch(flat(listRow(h, "999")), /已添加/);
});

test("写失败后读取也失败时保留上次读到的记录，读取恢复后再更新", async () => {
  const h = harness({strangers: ["999", "998", "997", "996", "995"].map((peer) => refused(peer))});
  await openRefused(h);
  assert.equal(h.element("stranger-list").children.length, 5);

  firstButton(listRow(h, "999")).handlers.click();
  await h.settle();
  buttonIn(openDialog(h), "确认添加").handlers.click();
  await h.settle();
  const posts = h.requests.filter((request) => request.method === "POST" && request.route === strangerTargetPath);
  assert.equal(posts.length, 1);
  posts[0].reject(Object.assign(new Error("未收到执行结果，请核对后重试本次操作。"), {code: "bridge_timeout"}));
  await h.settle();
  h.requests.filter((request) => request.method === "GET" && request.route === strangersPath).pop()
    .reject(Object.assign(new Error("桥没有响应"), {code: "bridge_unavailable"}));
  await h.settle();

  assert.equal(h.element("stranger-contacts").hidden, false, "读取失败不能把历史记录整块藏掉");
  assert.equal(h.element("stranger-list").children.length, 5);
  assert.match(flat(listRow(h, "999")), /账号 999/);
  assert.equal(h.element("stranger-status").hidden, false);
  assert.match(flat(h.element("stranger-status")), /上一次读取/);
  assert.match(h.element("notice").textContent, /没有收到添加操作/);
  assert.doesNotMatch(flat(listRow(h, "999")), /正在添加/);
  assert.equal(rowButtonDisabled(h, "999"), false, "提交结束且行仍可添加时按钮恢复");
  assert.equal(h.requests.filter((request) => request.method === "POST" && request.route === strangerTargetPath).length, 1,
    "读取失败不会重放写请求");

  h.strangerList.items = [refused("999", {target_state: "added", target_id: "private-new"}),
    refused("998"), refused("997"), refused("996"), refused("995")];
  h.element("refresh").handlers.click();
  await h.send();
  await h.send((request) => request.route === strangersPath);

  assert.equal(h.element("stranger-status").hidden, true, "读取成功后撤掉失败状态");
  assert.equal(h.element("stranger-count").textContent, "5 人");
  assert.match(flat(listRow(h, "999")), /已添加/);
  assert.equal(rowButtonDisabled(h, "999"), true);
});

test("保存成功后列表读取失败仍按回执显示已添加，并保留初始化失败提示", async () => {
  const h = harness({strangers: [refused("999"), refused("998")]});
  await openRefused(h);

  firstButton(listRow(h, "999")).handlers.click();
  await h.settle();
  buttonIn(openDialog(h), "确认添加").handlers.click();
  await h.settle();
  const post = h.requests.find((request) => request.method === "POST" && request.route === strangerTargetPath);
  post.resolve({status: "ok", data: {state: "added", target_id: "private-new", saved: true,
    requires_reload: false, requires_initialize: true, started: false,
    startup_error: "host_provider_required"}});
  await h.settle();

  // 保存后宿主正在重启，目录与列表读取都会被拒；页面只剩这一份回执。
  for (const request of h.requests) {
    if (request.method === "GET" && !request.answered) {
      request.reject(Object.assign(new Error("staging not initialized"), {code: "staging_not_initialized"}));
    }
  }
  await h.settle();

  const copy = flat(listRow(h, "999"));
  assert.match(copy, /已添加/, "保存回执必须直接落到行上，不能等下一次读取");
  assert.match(copy, /共 2 次/, "回执更新不能抹掉历史次数");
  assert.equal(rowButtonDisabled(h, "999"), true);
  assert.equal(rowButtonDisabled(h, "998"), false, "其它记录保持上次读取的状态");
  const message = h.element("notice").textContent;
  assert.match(message, /^已添加，权限沿用现有设置/);
  assert.match(message, /宿主需要初始化/);
  assert.match(message, /host_provider_required/);
  assert.match(h.element("notice").className, /error/);
  assert.equal(h.requests.filter((request) => request.method === "POST").length, 1);
});

test("宿主没有宿主确认能力时，页面内联确认框照常取消与提交一次", async () => {
  const h = harness({strangers: [refused("999")]});
  await openRefused(h);

  firstButton(listRow(h, "999")).handlers.click();
  await h.settle();
  const dialog = openDialog(h);
  assert.ok(dialog, "拿不到宿主确认层时必须在页面里退回内联确认框");
  assert.match(flat(dialog), /仅添加该账号到此收到方，权限沿用现有设置/);

  buttonIn(dialog, "取消").handlers.click();
  await h.settle();
  assert.equal(openDialog(h), null);
  assert.equal(h.requests.filter((request) => request.method === "POST"
    && request.route === strangerTargetPath).length, 0, "取消不写配置");

  firstButton(listRow(h, "999")).handlers.click();
  await h.settle();
  buttonIn(openDialog(h), "确认添加").handlers.click();
  await h.settle();
  assert.equal(h.requests.filter((request) => request.method === "POST"
    && request.route === strangerTargetPath).length, 1, "确认只写一次");
});
