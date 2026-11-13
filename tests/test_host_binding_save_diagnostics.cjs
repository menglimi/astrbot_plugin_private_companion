"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../pages/宿主管理/app.js"), "utf8");
const account = (id, state = "ready") => ({platform_id: id, account_id: id,
  bot_id: "bot-" + id, connection_status: state});
function harness(snapshot, failure = null) {
  const calls = [], notices = [];
  const context = vm.createContext({snapshot, calls, notices, failure});
  const start = source.indexOf("  function accountConnectionLabel(");
  assert.notEqual(start, -1, "connection diagnostics are missing");
  const end = source.indexOf("  function accountBindingCard(", start);
  const save = source.slice(source.indexOf("  async function saveBindingConfig("), source.indexOf("  function renderStatus("));
  vm.runInContext(`
    let bindingsDirty = true, bindingConfig = snapshot, acquaintanceDirectory = null;
    const accountApi = "accounts", window = {parent: {postMessage() {}}};
    const bindingAccountKey = row => JSON.stringify([row.platform_id, row.account_id, row.bot_id]);
    const codeOf = error => error?.code || error?.message || "";
    const publicError = error => codeOf(error);
    const accountChoiceLabel = row => row.platform_id;
    const mappedAccounts = () => snapshot.selected || [];
    const activeRoleAssignments = () => [{bot_id: "bot-native", persona_id: "synthetic", role_id: "role"}];
    function setBusy() {}
    function updateBindingCheck() {}
    function notice(message, kind) { notices.push({message, kind}); }
    function renderBindingEditor(value) { calls.push("render"); bindingConfig = value; }
    async function readStatus() { calls.push("status"); }
    async function api(method, endpoint) {
      calls.push(method);
      if (method === "POST" && failure) throw failure;
      return method === "GET" ? snapshot : {configuration: snapshot, started: true};
    }
    ${source.slice(start, end)}
    ${save}
    globalThis.page = {accountConnectionLabel, accountMappingLabel, bindingSaveError,
      saveBindingConfig, dirty: () => bindingsDirty};`, context);
  return {page: context.page, calls, notices};
}
test("discovered accounts are distinct from persisted role mappings", () => {
  const native = account("native"), official = account("official", "client_unavailable");
  const h = harness({accounts: [native], personas: [{bot_id: native.bot_id}],
    discovered: {accounts: [native, official]}});
  assert.match(h.page.accountMappingLabel(native), /已保存/);
  assert.match(h.page.accountMappingLabel(official), /仅发现.*未保存/);
  assert.match(h.page.accountConnectionLabel("client_unavailable"), /未提供.*客户端/);
  assert.equal(h.page.accountConnectionLabel("private backend details"), "连接状态未确认");
});
test("a message-only bridge failure names the selected unavailable account and keeps the draft", async () => {
  const native = account("native"), official = account("official", "client_unavailable");
  const h = harness({selected: [native, official], discovered: {accounts: [native, official]}},
    new Error("host_account_unavailable"));
  await h.page.saveBindingConfig();
  assert.deepEqual(h.calls, ["POST", "GET"]);
  assert.match(h.notices.at(-1).message, /official.*未提供.*客户端.*未保存/);
  assert.doesNotMatch(h.notices.at(-1).message, /native：/);
  assert.equal(h.page.dirty(), true);
});
test("an unselected unavailable account does not block or get blamed for a Native save", async () => {
  const native = account("native"), official = account("official", "client_unavailable");
  const h = harness({selected: [native], discovered: {accounts: [native, official]}});
  await h.page.saveBindingConfig();
  assert.deepEqual(h.calls, ["POST", "render", "status"]);
  assert.equal(h.page.dirty(), false);
  assert.equal(h.notices.at(-1).kind, "success");
});
test("a stale connection snapshot directs the operator to logs instead of blaming another account", async () => {
  const native = account("native"), official = account("official", "client_unavailable");
  const h = harness({selected: [native], discovered: {accounts: [native, official]}},
    Object.assign(new Error("secret backend detail"), {code: "host_account_unavailable"}));
  await h.page.saveBindingConfig();
  assert.match(h.notices.at(-1).message, /日志/);
  assert.doesNotMatch(h.notices.at(-1).message, /official|secret backend detail/);
  assert.equal(h.page.dirty(), true);
});
test("an unknown save outcome is never described as definitely unsaved", async () => {
  const h = harness({selected: [account("native")]},
    Object.assign(new Error("timeout"), {code: "host_operation_result_unknown"}));
  await h.page.saveBindingConfig();
  assert.deepEqual(h.calls, ["POST"]);
  assert.doesNotMatch(h.notices.at(-1).message, /本次.*未保存/);
});
