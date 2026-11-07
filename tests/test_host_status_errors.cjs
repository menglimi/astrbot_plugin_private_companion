"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../pages/宿主管理/app.js"), "utf8");

function harness() {
  const nodes = new Map(), notices = [];
  const document = {getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, {textContent: "", dataset: {}, hidden: false});
    return nodes.get(id);
  }};
  const section = (start, end) => source.slice(source.indexOf(start), source.indexOf(end));
  const context = vm.createContext({document, notices});
  vm.runInContext(source.slice(0, source.indexOf("  let bridge = null;")) + `
    let status = null, host = {}, busy = false;
    function isEnabled() { return status.enabled === true; }
    function isAccepting() { return status.accepting === true; }
    function stateLabel(value) { return value; }
    /* stateLabel 与 renderStatus 都会问「角色映射是否已保存」；与页面里同一份判定，
       少了它渲染阶段就会在 uninitialized 状态下抛 ReferenceError。 */
    function hasSavedRoleMappings() {
      return (status?.targets || []).some(target => Array.isArray(target?.roles) && target.roles.length > 0);
    }
    function paintStatusValue(id, value) { $(id).textContent = value; }
    function notice(message, kind) { notices.push({message, kind}); }
    ${section("  function codeOf(", "  async function api(")}
    ${section("  function renderStatus(", "  async function readStatus(")}
    ${section("  function publicError(", "  function toggleTopologyPin(")}
    globalThis.page = {unwrap, publicError, render(value) { status = value; renderStatus(); }};
  })();`, context);
  return {page: context.page, notices, nodes};
}

test("timeout stage survives a Dashboard bridge that forwards only the message", () => {
  const h = harness();
  for (const [stage, expected] of [["session_directory", "会话目录"], ["conversation_id", "当前会话编号"],
    ["conversation", "会话人格绑定"], ["session_settings", "会话服务设置"],
    ["persona", "会话生效人格"], ["conversation_verify", "会话切换校验"]]) {
    const code = "host_native_lookup_timeout__" + stage;
    let failure;
    try { h.page.unwrap({status: "error", message: code, reason: "synthetic private backend details"}); }
    catch (error) { failure = error; }
    assert.equal(failure.code, code);
    assert.match(h.page.publicError(failure), new RegExp(expected));
    assert.doesNotMatch(h.page.publicError(failure), /private backend details/);
  }
});

test("a successful status read identifies an earlier startup failure", () => {
  const h = harness();
  h.page.render({enabled: true, state: "uninitialized", accepting: false,
    last_failure: {code: "host_native_lookup_timeout__session_settings", operation: "start"}});
  assert.equal(h.nodes.get("connection").textContent, "插件连接正常");
  assert.equal(h.nodes.get("accepting").textContent, "未运行");
  assert.match(h.notices.at(-1).message, /上次启动失败.*会话服务设置/);
  h.page.render({enabled: true, state: "running", accepting: true, last_failure: null});
  assert.equal(h.notices.at(-1).message, "");
});

test("older timeout codes and pinned configuration reasons remain readable", () => {
  const h = harness();
  assert.match(h.page.publicError({code: "host_native_lookup_timeout"}), /原生状态超时/);
  assert.match(h.page.publicError({code: "staging_intent_invalid__bom"}), /UTF-8 BOM/);
});

test("unexpected initialization errors direct the operator to the host log", () => {
  const h = harness();
  h.page.render({enabled: true, accepting: false,
    last_failure: {code: "host_control_failed", operation: "initialize_profile",
      reason: "synthetic private exception details"}});
  assert.match(h.notices.at(-1).message, /上次准备角色失败.*AstrBot 日志.*CompanionV2/);
  assert.doesNotMatch(h.notices.at(-1).message, /核对角色与窗口设置|private exception/);
  assert.match(h.page.publicError({message: "host_control_failed"}), /AstrBot 日志/);
});

test("module loading failures identify plugin reload instead of role configuration", () => {
  const h = harness();
  h.page.render({enabled: true, accepting: false,
    last_failure: {code: "host_module_unavailable", operation: "load_existing"}});
  assert.match(h.notices.at(-1).message, /插件模块加载失败.*重新加载 V2 插件/);
  assert.doesNotMatch(h.notices.at(-1).message, /权限|人格映射/);
});

test("unrecognized lifecycle codes remain visible without exposing exception text", () => {
  const h = harness();
  h.page.render({enabled: true, accepting: false,
    last_failure: {code: "host_future_failure", operation: "initialize_profile"}});
  assert.match(h.notices.at(-1).message, /错误码：host_future_failure/);
  assert.match(h.page.publicError({code: "host_future_failure"}), /host_future_failure/);
  assert.doesNotMatch(h.page.publicError({message: "synthetic private exception details"}), /private exception/);
});

test("known role preparation failures show a specific recovery action", () => {
  const h = harness();
  assert.match(h.page.publicError({code: "host_mappings_required"}), /至少一个 Bot 账号与人格/);
  assert.match(h.page.publicError({code: "host_provider_required"}),
    /暂无可用的文本模型.*启用 Provider/);
  assert.match(h.page.publicError({code: "host_store_in_use"}), /实例与数据目录/);
});

test("account workspace exposes shared model bootstrap", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "pages", "\u5bbf\u4e3b\u7ba1\u7406", "workspace.html"), "utf8");
  assert.match(html, /shared-model-provider/);
  assert.match(source, /model-provider/);
});
