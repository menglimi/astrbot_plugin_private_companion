"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../pages/宿主管理/app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "../pages/宿主管理/workspace.html"), "utf8");
const section = (start, end) => source.slice(source.indexOf(start), source.indexOf(end));

function harness(request = async () => ({})) {
  const nodes = new Map(), calls = [];
  const document = {visibilityState: "visible", getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, {textContent: "", innerHTML: ""});
    return nodes.get(id);
  }};
  const context = vm.createContext({document, request, calls});
  vm.runInContext(source.slice(0, source.indexOf("  let bridge = null;")) + `
    let status = {}, host = {prefix: "host"}, busy = false, runtimeStatusRead = false;
    async function api(method, endpoint) { calls.push([method, endpoint]); return request(); }
    function renderStatus(options) { calls.push(options); renderAccountConnections(); }
    const nativeId = value => String(value ?? "").trim();
    ${section("  const accountChoiceLabel =", "  const groupDisplayName =")}
    ${section("  function accountRuntimeReason(", "  function updateControls(")}
    ${section("  async function refreshRuntimeStatus(", "  function publicError(")}
    globalThis.render = value => { status = value; renderAccountConnections(); };
    globalThis.refresh = refreshRuntimeStatus;
    globalThis.setBusy = value => { busy = value; };
  })();`, context);
  return {render: context.render, refresh: context.refresh, setBusy: context.setBusy, nodes, document, calls};
}

const account = (id, state, rest = {}) => ({platform_id: `platform-${id}`,
  account_id: `account-${id}`, bot_id: `bot-${id}`, state, ...rest});

test("account workspace shows all configured accounts while one connection is unavailable", () => {
  const h = harness();
  h.render({account_connections: [account("a", "waiting", {reason_code: "host_platform_unavailable"}),
    account("b", "online")]});
  assert.equal(h.nodes.get("account-runtime-summary").textContent, "2 个已配置 · 1 个已接入");
  const content = h.nodes.get("account-runtime-list").innerHTML;
  assert.match(content, /account-a/);
  assert.match(content, /等待连接/);
  assert.match(content, /平台实例尚未加载/);
  assert.match(content, /account-b/);
  assert.match(content, /已接入/);
  assert.doesNotMatch(content, /已在线|连接正常/);
  assert.match(html, /客户端可用.*实际收发仍以消息记录为准/);
});

test("recovery refresh replaces the waiting state and records when the account recovered", () => {
  const h = harness();
  h.render({account_connections: [account("a", "recovering")]});
  assert.match(h.nodes.get("account-runtime-list").innerHTML, /恢复中/);
  h.render({account_connections: [account("a", "online", {last_recovered_at: "2026-01-02T03:04:05+00:00"})]});
  const content = h.nodes.get("account-runtime-list").innerHTML;
  assert.equal(h.nodes.get("account-runtime-summary").textContent, "1 个已配置 · 1 个已接入");
  assert.match(content, /最近恢复/);
  assert.match(content, /datetime="2026-01-02T03:04:05.000Z"/);
  assert.doesNotMatch(content, /恢复中|等待连接/);
});

test("identity changes and errors identify the account without rendering backend exception text", () => {
  const h = harness();
  h.render({account_connections: [account("a", "identity_changed", {reason_code: "host_account_identity_changed"}),
    account("b", "error", {reason_code: "unknown_backend_failure", reason: "synthetic private credential"})]});
  const content = h.nodes.get("account-runtime-list").innerHTML;
  assert.match(content, /账号身份变化/);
  assert.match(content, /实际 Bot 身份与已保存的账号不一致/);
  assert.match(content, /接入异常/);
  assert.match(content, /AstrBot 日志/);
  assert.doesNotMatch(content, /private credential|unknown_backend_failure/);
});

test("connection diagnostics distinguish identity verification from retryable connection and session failures", () => {
  const h = harness();
  const scenarios = [
    ["online", "", /客户端可用/],
    ["waiting", "host_account_offline", /账号连接当前不可用/],
    ["waiting", "host_account_identity_unavailable", /尚未提供可核验的账号身份.*等待验证/],
    ["identity_changed", "host_account_changed", /实际 Bot 身份或账号映射发生变化/],
    ["identity_changed", "host_platform_changed", /平台类型或标识.*不一致/],
    ["error", "host_platform_probe_failed", /无法读取平台连接状态.*自动重试/],
    ["error", "host_account_recovery_failed", /会话准备失败.*自动重试/],
  ];
  for (const [state, reason_code, expected] of scenarios) {
    h.render({account_connections: [account("a", state, {reason_code})]});
    assert.match(h.nodes.get("account-runtime-list").innerHTML, expected);
  }
});

test("missing connection data stays unconfirmed and is distinct from having no configured accounts", () => {
  const h = harness();
  h.render({});
  assert.equal(h.nodes.get("account-runtime-summary").textContent, "账号状态暂未提供");
  assert.equal(h.nodes.get("account-runtime-list").innerHTML, "");
  h.render({account_connections: []});
  assert.equal(h.nodes.get("account-runtime-summary").textContent, "尚未配置 Bot 账号");
});

test("account names remain text in the status cards", () => {
  const h = harness();
  h.render({account_connections: [account("a", "online", {account_name: '<img src=x onerror="run()">'})]});
  const content = h.nodes.get("account-runtime-list").innerHTML;
  assert.match(content, /&lt;img/);
  assert.doesNotMatch(content, /<img/);
});

test("visible runtime polling updates connections without reloading editable mappings or clearing feedback", async () => {
  let finish;
  const h = harness(() => new Promise(resolve => { finish = resolve; }));
  h.render({account_connections: [account("a", "waiting")]});
  const pending = h.refresh();
  await h.refresh();
  assert.equal(h.calls.length, 1, "an outstanding status read is not duplicated");
  assert.equal(JSON.stringify(h.calls[0]), JSON.stringify(["GET", "host/status"]));
  finish({account_connections: [account("a", "online")]});
  await pending;
  assert.equal(h.nodes.get("account-runtime-summary").textContent, "1 个已配置 · 1 个已接入");
  assert.equal(h.calls[1].showNotice, false);
  h.document.visibilityState = "hidden";
  await h.refresh();
  h.document.visibilityState = "visible";
  h.setBusy(true);
  await h.refresh();
  assert.equal(h.calls.length, 2, "hidden pages and foreground operations do not poll");
});

test("a failed background read marks previous connection results as stale", async () => {
  const h = harness(async () => { throw new Error("synthetic private failure"); });
  h.render({account_connections: [account("a", "online")]});
  const previous = h.nodes.get("account-runtime-list").innerHTML;
  await h.refresh();
  assert.match(h.nodes.get("account-runtime-summary").textContent, /状态刷新失败.*上次结果/);
  assert.equal(h.nodes.get("account-runtime-list").innerHTML, previous);
  assert.doesNotMatch(h.nodes.get("account-runtime-summary").textContent, /private failure/);
});
