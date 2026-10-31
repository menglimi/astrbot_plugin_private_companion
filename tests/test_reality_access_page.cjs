"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.resolve(__dirname, "../pages/陪伴小屋/reality-access.js"), "utf8");
const prefix = "/astrbot_plugin_private_companion";
const capabilities = new Map([
  ["", "reality.snapshot.read"],
  ["/camera", "reality.camera.observe"],
  ["/device-list", "reality.device.list"],
  ["/device-status", "reality.device.status"],
  ["/device-control", "reality.device.control"],
  ["/device-action", "reality.device.action"],
  ["/operation-lookup", "reality.operation.lookup"],
  ["/operation-cancel", "reality.operation.cancel"],
]);
const flush = () => new Promise(resolve => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 12; i += 1) await flush(); };

function harness(request) {
  const calls = [];
  const node = {
    innerHTML: "", isConnected: true, listeners: {},
    addEventListener(name, fn) { this.listeners[name] = fn; },
    removeEventListener(name) { delete this.listeners[name]; },
    fire(action) {
      this.listeners.click({target: {closest: () => ({dataset: {realityAccessAction: action}})}});
    },
  };
  const product = {
    hosted: () => true,
    role: () => ({reality_access_api: prefix + "/reality-access"}),
    relative(value) {
      if (typeof value !== "string" || !value.startsWith(prefix)) throw new Error("invalid route");
      return value.slice(prefix.length);
    },
    scopeKey: () => "install-a:target-a:role-a",
    scopeLabel: () => "当前私聊窗口",
    async request(method, route, body) {
      calls.push({method, route, body});
      const suffix = method === "GET" ? route.slice("/reality-access".length) : "";
      return request(method, suffix, body);
    },
  };
  const window = {CompanionProduct: product};
  vm.runInNewContext(source, {window, Promise, Map, Set, String, Object, JSON, Number, Array,
    crypto: {randomUUID: () => "operation-a"}});
  window.CompanionRealityAccess.mount(node);
  return {node, calls, page: window.CompanionRealityAccess};
}

function panelStatus(suffix, changes = {}) {
  return {action: capabilities.get(suffix), invokable: false, consented: false, can_manage: true,
    installed: true, active: true, role_enabled: false, role_revision: 0, auto_consent: false,
    edit_token: "edit-a", reason: "capability_role_disabled", ...changes};
}

test("只读账号能读取 Reality 状态，但看不到会被 CORE 权限拒绝的授权按钮", async () => {
  const {node, calls} = harness(async (method, suffix) => ({
    action: capabilities.get(suffix), invokable: false, consented: false, can_manage: false,
    edit_token: "edit-a", reason: "capability_not_installed",
  }));
  await settle();

  assert.equal(calls.length, capabilities.size);
  assert.ok(calls.every(call => call.method === "GET"));
  assert.equal((node.innerHTML.match(/只读/g) || []).length, capabilities.size);
  assert.match(node.innerHTML, /当前账号没有管理此授权的权限/);
  assert.doesNotMatch(node.innerHTML, /data-reality-access-action="(?:mobile|camera|deviceList|deviceStatus|deviceControl|deviceAction|operationLookup|operationCancel)-enable"/);
});

test("核心管理员的授权面板用角色启用/停用按钮替代恒真的撤回", async () => {
  const {node, calls} = harness(async (method, suffix, body) => {
    if (method === "GET") return panelStatus(suffix, {auto_consent: true, consented: true});
    return {replayed: false, status: panelStatus(suffix, {
      auto_consent: true, consented: true, invokable: body.enabled, role_enabled: body.enabled,
      role_revision: body.expected_revision + 1,
    })};
  });
  await settle();
  assert.equal((node.innerHTML.match(/启用此角色/g) || []).length, capabilities.size);
  assert.doesNotMatch(node.innerHTML, /撤回手机读取授权/);
  assert.doesNotMatch(node.innerHTML, /data-reality-access-action="mobile-enable"/);

  node.fire("mobile-role-enable");
  await settle();
  const enable = calls.find(call => call.method === "POST");
  assert.equal(enable.route, "/reality-access/role-selection");
  assert.equal(JSON.stringify(enable.body), JSON.stringify({operation_key: "reality-role-mobile-operation-a",
    expected_revision: 0, enabled: true}));
  assert.match(node.innerHTML, /停用此角色/);
  assert.match(node.innerHTML, /已启用/);

  node.fire("mobile-role-disable");
  await settle();
  const disable = calls.filter(call => call.method === "POST").at(-1);
  assert.equal(disable.body.enabled, false);
  assert.equal(disable.body.expected_revision, 1);
  assert.match(node.innerHTML, /启用此角色/);
  assert.match(node.innerHTML, /未启用/);
});

test("核心管理员未装配时按钮显示未装配，角色冲突给出可读文案", async () => {
  let installed = false;
  const {node, calls} = harness(async (method, suffix, body) => {
    if (method === "GET") return panelStatus(suffix, {
      auto_consent: true, consented: true, installed, role_enabled: false,
      reason: installed ? "capability_role_disabled" : "capability_not_installed",
    });
    throw Object.assign(new Error("capability_role_revision_conflict"),
      {code: "capability_role_revision_conflict"});
  });
  await settle();
  assert.equal((node.innerHTML.match(/<button class="button small" type="button" disabled>未装配<\/button>/g) || []).length,
    capabilities.size);
  assert.doesNotMatch(node.innerHTML, /撤回手机读取授权/);

  installed = true;
  node.fire("refresh");
  await settle();
  assert.match(node.innerHTML, /启用此角色/);
  node.fire("mobile-role-enable");
  await settle();
  assert.match(node.innerHTML, /角色启用状态已变化/);
  assert.equal(calls.filter(call => call.method === "POST").length, 1);
});
