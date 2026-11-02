"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.resolve(__dirname, "../pages/陪伴小屋/screen-access.js"), "utf8");
const prefix = "/astrbot_plugin_private_companion";
const flush = () => new Promise(resolve => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 10; i += 1) await flush(); };

function harness({kind = "private", route = true, request, targets = [], confirm = async () => true} = {}) {
  const calls = [];
  const choices = [];
  const target = {scope_kind: kind, target_id: "window-a", bot_id: "bot-a"};
  const node = {
    innerHTML: "", isConnected: true, listeners: {},
    addEventListener(name, fn) { this.listeners[name] = fn; },
    removeEventListener(name) { delete this.listeners[name]; },
    fire(action) {
      this.listeners.click({target: {closest: () => ({dataset: {screenAccessAction: action}})}});
    },
  };
  const product = {
    hosted: () => true,
    target: () => target,
    targets: () => targets,
    targetLabel: item => item.display_name || item.target_id,
    choose: async (targetId, roleId) => { choices.push({targetId, roleId}); },
    selection: () => ({role_id: "role-a", target_id: target.target_id}),
    role: () => ({screen_access_api: route ? prefix + "/screen-access" : null}),
    relative(value) {
      if (typeof value !== "string" || !value.startsWith(prefix)) throw new Error("invalid route");
      return value.slice(prefix.length);
    },
    scopeKey: () => JSON.stringify(["install-a", target.target_id, "role-a"]),
    async request(method, path, body) {
      calls.push({method, path, body});
      if (request) return request(method, path, body);
      return method === "GET"
        ? {invokable: false, consented: false, can_manage: true, edit_token: "edit-a", reason: "capability_not_installed"}
        : {status: {invokable: body.enabled, consented: body.enabled, can_manage: true, edit_token: "edit-b"}};
    },
  };
  const window = {CompanionProduct: product, CompanionConfirm: confirm};
  const document = {};
  vm.runInNewContext(source, {window, document, Promise, Map, String, Math, Date, JSON, Object,
    crypto: {randomUUID: () => "operation-a"}});
  window.CompanionScreenAccess.mount(node);
  return {node, calls, choices, page: window.CompanionScreenAccess};
}

test("识屏授权明确选择同角色私聊，切换对象不提交授权", async () => {
  const {node, calls, choices} = harness({targets: [
    {target_id: "window-a", scope_kind: "private", display_name: "私聊甲", roles: [{role_id: "role-a"}]},
    {target_id: "window-b", scope_kind: "private", display_name: "私聊乙", roles: [{role_id: "role-a"}]},
    {target_id: "group-a", scope_kind: "group", display_name: "群聊", roles: [{role_id: "role-a"}]},
    {target_id: "other-role", scope_kind: "private", display_name: "其他角色", roles: [{role_id: "role-b"}]},
  ]});
  await settle();
  assert.match(node.innerHTML, /识屏授权私聊/);
  assert.match(node.innerHTML, /私聊乙/);
  assert.doesNotMatch(node.innerHTML, /value="(?:group-a|other-role)"/);
  node.listeners.change({target: {closest: () => ({value: "window-b"})}});
  await settle();
  assert.deepEqual(choices, [{targetId: "window-b", roleId: "role-a"}]);
  assert.equal(calls.filter(call => call.method === "POST").length, 0);
});

test("群聊显示私聊边界，不发送屏幕授权请求", async () => {
  const {node, calls} = harness({kind: "group", route: false});
  await settle();
  assert.match(node.innerHTML, /切换到私聊对象/);
  assert.doesNotMatch(node.innerHTML, /核对中/);
  assert.equal(calls.length, 0);
});

test("私聊目标缺少目录路由时说明下一步", async () => {
  const {node, calls} = harness({route: false});
  await settle();
  assert.match(node.innerHTML, /账号与会话中刷新目标/);
  assert.doesNotMatch(node.innerHTML, /核对中/);
  assert.equal(calls.length, 0);
});

test("缺少原生扩展时指导启用 native_v2 并重载", async () => {
  const {node} = harness({request: async () => {
    throw Object.assign(new Error("screen_provider_unavailable"), {code: "screen_provider_unavailable"});
  }});
  await settle();
  assert.match(node.innerHTML, /启用 native_v2 并重载/);
  assert.match(node.innerHTML, /暂不可用/);
});

test("只有查看权限时标明只读，不显示会被 CORE 权限拒绝的授权按钮", async () => {
  const {node, calls} = harness({request: async () => ({invokable: false, consented: false,
    can_manage: false, edit_token: "edit-a", reason: "capability_not_installed"})});
  await settle();
  assert.match(node.innerHTML, /当前账号没有管理此授权的权限/);
  assert.match(node.innerHTML, /只读/);
  assert.doesNotMatch(node.innerHTML, /data-screen-access-action="enable"/);
  assert.equal(calls.length, 1);
});

test("授权和撤回沿用当前私聊的版本令牌", async () => {
  const {node, calls} = harness();
  await settle();
  assert.match(node.innerHTML, /允许当前私聊按需看屏/);
  node.fire("enable");
  await settle();
  assert.equal(calls[1].method, "POST");
  assert.equal(calls[1].body.enabled, true);
  assert.equal(calls[1].body.edit_token, "edit-a");
  assert.match(node.innerHTML, /撤回当前私聊授权/);
  node.fire("disable");
  await settle();
  assert.equal(calls[2].body.enabled, false);
  assert.equal(calls[2].body.edit_token, "edit-b");
});

test("版本变化时仅通过确认后提交精确旧包信息升级", async () => {
  const confirmations = [];
  let upgraded = false;
  const {node, calls} = harness({confirm: async options => { confirmations.push(options); return true; },
    request: async (method) => {
      if (method === "GET" && !upgraded) return {invokable: false, consented: true, can_manage: true,
        edit_token: "edit-a", reason: "screen_package_changed", package_version: "3.3.1",
        upgrade_available: true, installed_package_version: "3.3.0",
        installed_artifact_hash: "old-hash", installed_revision: 2};
      if (method === "POST") { upgraded = true; return {upgraded: true, package_version: "3.3.1"}; }
      return {invokable: true, consented: true, can_manage: true, edit_token: "edit-b",
        package_version: "3.3.1", upgrade_available: false, reason: ""};
    }});
  await settle();
  assert.match(node.innerHTML, /升级屏幕陪伴/);
  assert.match(node.innerHTML, /已装配版本 3\.3\.0，当前版本 3\.3\.1/);
  node.fire("upgrade");
  await settle();
  assert.equal(confirmations.length, 1);
  assert.match(confirmations[0].message, /当前私聊授权会保留/);
  assert.equal(calls[1].method, "POST");
  assert.equal(calls[1].path, "staging/upgrade-screen");
  assert.equal(JSON.stringify(calls[1].body), JSON.stringify({
    previous_version: "3.3.0", previous_hash: "old-hash", expected_revision: 2,
  }));
  assert.match(node.innerHTML, /屏幕陪伴已升级到 3\.3\.1/);
  assert.doesNotMatch(node.innerHTML, /data-screen-access-action="upgrade"/);
});

test("核心管理员的授权面板用角色启用/停用按钮替代恒真的撤回", async () => {
  const {node, calls} = harness({request: async (method, path, body) => method === "GET"
    ? {installed: true, active: true, role_enabled: false, role_revision: 0, auto_consent: true,
      invokable: false, consented: true, can_manage: true, edit_token: "edit-a",
      reason: "capability_role_disabled"}
    : {replayed: false, status: {installed: true, active: true, role_enabled: body.enabled,
      role_revision: body.expected_revision + 1, auto_consent: true, invokable: body.enabled,
      consented: true, can_manage: true, edit_token: "edit-b"}}});
  await settle();
  assert.match(node.innerHTML, /启用此角色/);
  assert.doesNotMatch(node.innerHTML, /撤回当前私聊授权/);
  assert.doesNotMatch(node.innerHTML, /data-screen-access-action="enable"/);
  node.fire("role-enable");
  await settle();
  const enable = calls.filter(call => call.method === "POST").at(-1);
  assert.equal(enable.path, "/screen-access/role-selection");
  assert.equal(JSON.stringify(enable.body), JSON.stringify({operation_key: "screen-role-operation-a",
    expected_revision: 0, enabled: true}));
  assert.match(node.innerHTML, /停用此角色/);
  assert.match(node.innerHTML, /已启用/);
  node.fire("role-disable");
  await settle();
  assert.equal(calls.filter(call => call.method === "POST").at(-1).body.enabled, false);
  assert.match(node.innerHTML, /启用此角色/);
  assert.match(node.innerHTML, /未启用/);
});

test("核心管理员未装配时按钮显示未装配，角色冲突给出可读文案", async () => {
  let installed = false;
  const {node, calls} = harness({request: async (method) => {
    if (method === "GET") return {installed, active: installed, role_enabled: false, role_revision: 0,
      auto_consent: true, invokable: false, consented: true, can_manage: true, edit_token: "edit-a",
      reason: installed ? "capability_role_disabled" : "capability_not_installed"};
    throw Object.assign(new Error("capability_role_revision_conflict"),
      {code: "capability_role_revision_conflict"});
  }});
  await settle();
  assert.match(node.innerHTML, /未装配/);
  assert.doesNotMatch(node.innerHTML, /撤回当前私聊授权/);
  installed = true;
  node.fire("refresh");
  await settle();
  assert.match(node.innerHTML, /启用此角色/);
  node.fire("role-enable");
  await settle();
  assert.match(node.innerHTML, /角色启用状态已变化/);
  assert.equal(calls.filter(call => call.method === "POST").length, 1);
});

test("已确认的屏幕授权在路由返回时先保留，再重新核对", async () => {
  const {node, calls, page} = harness({request: async (method) => method === "GET"
    ? {invokable: true, consented: true, can_manage: true, edit_token: "edit-a"}
    : {status: {invokable: true, consented: true, can_manage: true, edit_token: "edit-b"}}});
  await settle();
  page.unmount();
  page.mount(node);
  assert.match(node.innerHTML, /已授权/);
  await settle();
  assert.equal(calls.filter(call => call.method === "GET").length, 2);
  assert.match(node.innerHTML, /已授权/);
  page.unmount();
});
