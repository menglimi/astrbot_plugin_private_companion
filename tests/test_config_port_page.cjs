"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "pages", "陪伴小屋", "config-port-page.js"), "utf8");

function button(attributes) {
  return {hasAttribute: name => attributes.includes(name), closest: () => null, disabled: false};
}

function setup({windowSelected = true} = {}) {
  const calls = [];
  const created = [];
  const confirmations = [];
  let selected = {target_id: windowSelected ? "private-second" : null, role_id: "role-shared", bot_id: "bot-second"};
  let roleSelected = {target_id: "private-second", role_id: "role-shared", bot_id: "bot-second"};
  const document = {
    createElement(tag) {
      const link = {tag, href: "", download: "", click() { created.push(link); }, remove() {}};
      return link;
    },
    body: {appendChild() {}},
  };
  const window = {
    document,
    CompanionProduct: {
      selection: () => selected,
      roleSelection: () => roleSelected,
      target: () => ({target_id: selected.target_id, bot_id: selected.bot_id}),
      roleTarget: () => ({target_id: roleSelected.target_id, bot_id: roleSelected.bot_id}),
      accountLabel: () => "测试 Bot 二",
      roleLabel: () => "测试角色",
      targetLabel: () => "私聊 测试用户",
      async request(method, route, body) {
        calls.push([method, route, body]);
        return method === "GET"
          ? {schema: "companion.config-export.v1", sections: {quota: {settings: {enabled: true}}}, unavailable: {}}
          : {applied: {quota: ["enabled"]}, failed: {}};
      },
    },
    CompanionConfirm: async options => { confirmations.push(options); return true; },
  };
  const context = {window, document, Blob, URL: {createObjectURL: () => "blob:config", revokeObjectURL() {}},
    setTimeout, clearTimeout, Promise, Object, Array, Error, Number, String, JSON, Date, Math};
  vm.runInNewContext(source, context);
  const handlers = {};
  const root = {isConnected: true, innerHTML: "", addEventListener(name, fn) { handlers[name] = fn; },
    contains() { return true; }, querySelector() { return null; }};
  window.CompanionConfigPortPage.mount(root);
  return {window, root, handlers, calls, created, confirmations,
    setSelection(value) { selected = value; roleSelected = value; }};
}

test("export downloads a JSON document from the portable config route", async () => {
  const {root, handlers, calls, created} = setup();
  assert.match(root.innerHTML, /配置导入导出/);
  assert.match(root.innerHTML, /data-config-port-export/);
  handlers.click({target: {closest: () => button(["data-config-port-export"])}});
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(calls[0][0], "GET");
  assert.equal(calls[0][1], "config/port/export");
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0][2])), {
    target_id: "private-second", role_id: "role-shared", bot_id: "bot-second", window_selected: "true",
  });
  assert.match(root.innerHTML, /测试 Bot 二 · 测试角色 · 私聊 测试用户/);
  assert.equal(created.length, 1);
  assert.match(created[0].download, /^companion-config-\d{8}-\d{4}\.json$/);
  assert.match(root.innerHTML, /已导出 1 个分区/);
});

test("import uploads the chosen document and reports applied sections", async () => {
  const {root, handlers, calls, confirmations} = setup();
  let clicked = 0;
  root.querySelector = selector => selector === "[data-config-port-file]"
    ? {click() { clicked += 1; }} : null;
  handlers.click({target: {closest: () => button(["data-config-port-import"])}});
  assert.equal(clicked, 1);
  const document = {schema: "companion.config-export.v1", sections: {quota: {settings: {enabled: true}}}};
  handlers.change({target: {matches: () => true, value: "x", files: [{size: 100,
    async text() { return JSON.stringify(document); }}]}});
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(calls.at(-1)[0], "POST");
  assert.equal(calls.at(-1)[1], "config/port/import");
  assert.equal(JSON.stringify(calls.at(-1)[2]), JSON.stringify({document,
    target_id: "private-second", role_id: "role-shared", bot_id: "bot-second", window_selected: true}));
  assert.match(confirmations[0].message, /测试 Bot 二 · 测试角色 · 私聊 测试用户/);
  assert.match(root.innerHTML, /已导入 1 个分区/);
});

test("role-only selection explicitly excludes window configuration", async () => {
  const {root, handlers, calls} = setup({windowSelected: false});
  assert.match(root.innerHTML, /角色默认 · 未选择窗口/);
  assert.match(root.innerHTML, /只处理角色与全局配置/);
  handlers.click({target: {closest: () => button(["data-config-port-export"])}});
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(calls[0][2].window_selected, "false");
  assert.equal(calls[0][2].target_id, "private-second");
});

test("changing Bot while confirming import does not write to either selection", async () => {
  const {window, handlers, calls, setSelection} = setup();
  window.CompanionConfirm = async () => {
    setSelection({target_id: "private-first", role_id: "role-shared", bot_id: "bot-first"});
    return true;
  };
  handlers.change({target: {matches: () => true, value: "x", files: [{size: 100,
    async text() { return JSON.stringify({schema: "companion.config-export.v1", sections: {}}); }}]}});
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(calls.length, 0);
});

test("unselected role cannot export or import configuration", () => {
  const {window, root, setSelection} = setup();
  setSelection({target_id: null, role_id: null, bot_id: null});
  window.CompanionConfigPortPage.mount(root);
  assert.match(root.innerHTML, /尚未选择角色/);
  assert.match(root.innerHTML, /data-config-port-export disabled/);
  assert.match(root.innerHTML, /data-config-port-import disabled/);
});
