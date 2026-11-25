"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const PAGE = name => fs.readFileSync(
  path.join(__dirname, "..", "pages", "陪伴小屋", name), "utf8");

function makeClassList() {
  const set = new Set();
  return {
    add: (...names) => names.forEach(n => set.add(n)),
    remove: (...names) => names.forEach(n => set.delete(n)),
    toggle: (name, force) => {
      const on = force === undefined ? !set.has(name) : Boolean(force);
      if (on) set.add(name); else set.delete(name);
      return on;
    },
    contains: name => set.has(name),
  };
}

function makeFakeDom() {
  const listeners = {};
  const nodes = {
    "[data-phone-results]": {hidden: true, replaceChildren() {}, append() {}, querySelectorAll: () => []},
    "[data-phone-notice]": {textContent: "", classList: makeClassList()},
    "[data-phone-download]": {hidden: true},
    "[data-phone-enabled]": {hidden: true, textContent: "", classList: makeClassList()},
    '[name="code"]': {value: ""},
    "[data-phone-manual]": {open: false},
  };
  const advanced = {open: false};
  const root = {
    isConnected: true,
    innerHTML: "",
    textContent: "",
    parentElement: {querySelector: sel => sel === ".phone-advanced" ? advanced : null},
    querySelector: sel => nodes[sel] || null,
    querySelectorAll: () => [],
    addEventListener(type, fn) {
      listeners[type] = listeners[type] || [];
      listeners[type].push(fn);
    },
  };
  return {root, nodes, advanced, listeners};
}

function loadPhoneSetup({requestImpl}) {
  const window = {
    CompanionProduct: {
      request: requestImpl,
      errorLabel: code => `fallback:${code}`,
    },
  };
  const document = {
    createElement: () => ({innerHTML: "", querySelector: () => ({textContent: "", innerHTML: "", firstElementChild: {dataset: {}, setAttribute() {}}}), append() {}}),
  };
  vm.runInNewContext(PAGE("phone-setup.js"), {
    window, document, crypto: {randomUUID: () => "uuid-1"},
    Array, Object, String, Boolean, Promise, FormData: class {}, console,
  });
  return window.CompanionPhoneSetup;
}

test("phone-setup rebinds endpoint on role switch without stacking duplicate listeners", async () => {
  const calls = [];
  const PhoneSetup = loadPhoneSetup({
    requestImpl: async (method, endpoint, body) => {
      calls.push({method, endpoint, body});
      return {devices: []};
    },
  });
  const {root, listeners} = makeFakeDom();
  const editorA = {state: {instances: [{state: {status: {settings: {enabled: false}}}, dirty: () => false}]}, read: async () => {}};
  const editorB = {state: {instances: [{state: {status: {settings: {enabled: true}}}, dirty: () => false}]}, read: async () => {}};

  const mountedA = PhoneSetup.mount(root, {
    configApi: "/astrbot_plugin_private_companion/host/targets/t1/roles/role-a/config",
    editor: editorA,
  });
  assert.strictEqual(mountedA.endpoint, "host/targets/t1/roles/role-a/config/phone-connection");
  assert.strictEqual(listeners.click.length, 1);

  const mountedB = PhoneSetup.mount(root, {
    configApi: "/astrbot_plugin_private_companion/host/targets/t2/roles/role-b/config",
    editor: editorB,
  });
  assert.strictEqual(mountedB, mountedA);
  assert.strictEqual(mountedB.endpoint, "host/targets/t2/roles/role-b/config/phone-connection");
  assert.strictEqual(listeners.click.length, 1, "rebind must not attach duplicate DOM listeners");

  const scanButton = {
    dataset: {},
    hasAttribute: name => name === "data-phone-scan",
    closest: sel => sel === "button" ? scanButton : null,
  };
  listeners.click[0]({target: scanButton});
  await new Promise(resolve => setImmediate(resolve));
  assert.strictEqual(calls.length, 1);
  assert.strictEqual(calls[0].endpoint, "host/targets/t2/roles/role-b/config/phone-connection");
});

test("phone-setup surfaces structured adb_not_found diagnostics from error.details and status fallback", async () => {
  let nextError = null;
  const PhoneSetup = loadPhoneSetup({
    requestImpl: async () => { throw nextError; },
  });
  const {root, nodes, advanced, listeners} = makeFakeDom();
  const statusObj = {
    settings: {enabled: false},
    adb_diagnostic: {
      available: false,
      reason: "explicit_path_missing",
      configured_adb_path: "D:/missing/adb.exe",
      config_source: "role_config",
      resolved_role_id: "role-2517047265-abc",
      resolved_bot_id: "bot-2517047265",
    },
  };
  const editor = {state: {instances: [{state: {status: statusObj}, dirty: () => false}]}, read: async () => {}};
  PhoneSetup.mount(root, {
    configApi: "/astrbot_plugin_private_companion/host/targets/t1/roles/role-2517047265-abc/config",
    editor,
  });

  const scanButton = {
    dataset: {},
    hasAttribute: name => name === "data-phone-scan",
    closest: sel => sel === "button" ? scanButton : null,
  };

  // Case 1: direct error.details carries explicit_path_is_dir
  nextError = Object.assign(new Error("adb_not_found"), {
    code: "adb_not_found",
    details: {
      reason: "explicit_path_is_dir",
      configured_adb_path: "C:/Android/platform-tools",
      config_source: "role_config",
      resolved_role_id: "role-2517047265-abc",
      resolved_bot_id: "bot-2517047265",
    },
  });
  listeners.click[0]({target: scanButton});
  await new Promise(resolve => setImmediate(resolve));
  assert.match(nodes["[data-phone-notice]"].textContent, /填成了文件夹/);
  assert.match(nodes["[data-phone-notice]"].textContent, /C:\/Android\/platform-tools/);
  assert.match(nodes["[data-phone-notice]"].textContent, /role-2517047265-abc/);
  assert.strictEqual(advanced.open, true);
  assert.strictEqual(nodes["[data-phone-download]"].hidden, true);

  // Case 2: legacy bridge drops error.details -> falls back to status.adb_diagnostic
  advanced.open = false;
  nextError = Object.assign(new Error("adb_not_found"), {code: "adb_not_found"});
  listeners.click[0]({target: scanButton});
  await new Promise(resolve => setImmediate(resolve));
  assert.match(nodes["[data-phone-notice]"].textContent, /找不到高级设置里填写的 ADB 文件/);
  assert.match(nodes["[data-phone-notice]"].textContent, /D:\/missing\/adb\.exe/);
  assert.strictEqual(advanced.open, true);
  assert.strictEqual(nodes["[data-phone-download]"].hidden, false);

  // Case 3: auto_discovery_empty guides download and custom path
  const msg = PhoneSetup.adbNotFoundMessage({
    reason: "auto_discovery_empty",
    configured_adb_path: "",
    config_source: "default",
    resolved_role_id: "role-1",
    resolved_bot_id: "bot-1",
  });
  assert.match(msg, /未在系统 PATH 或常见目录找到 ADB/);
  assert.match(msg, /默认自动查找/);
});

test("CompanionProduct.request preserves structured failure data on error.details", async () => {
  const window = {
    addEventListener() {}, removeEventListener() {},
    matchMedia: () => ({matches: false, addEventListener() {}}),
    location: {hash: "", href: "http://localhost/#actions"},
    history: {replaceState() {}}, innerWidth: 1440, innerHeight: 900,
    requestAnimationFrame: cb => { cb(0); return 0; }, cancelAnimationFrame() {},
    AstrBotPluginPage: {
      ready: async () => ({pluginName: "astrbot_plugin_private_companion"}),
      apiGet: async () => ({status: "ok", data: {}}),
      apiPost: async () => ({
        status: "error",
        message: "adb_not_found",
        reason: "the configured adb path is a directory",
        data: {
          code: "adb_not_found",
          reason: "explicit_path_is_dir",
          configured_adb_path: "D:/adb",
          config_source: "role_config",
          resolved_role_id: "role-x",
          resolved_bot_id: "bot-x",
        },
      }),
    },
  };
  const document = {
    readyState: "interactive", documentElement: {dataset: {}},
    head: {appendChild() {}}, body: {classList: {add() {}, remove() {}}},
    querySelector: () => null, querySelectorAll: () => [],
    createElement: () => ({addEventListener() {}, setAttribute() {}}),
    addEventListener() {},
  };
  vm.runInNewContext(PAGE("product-page.js"), {
    window, document, navigator: {language: "zh-CN"}, setTimeout, clearTimeout,
    Promise, Object, JSON, Array, String, Number, Boolean, Math, Date, Error, console,
    matchMedia: () => ({matches: false}),
    localStorage: {getItem: () => null, setItem() {}, removeItem() {}},
  });
  await window.CompanionProduct.connect(() => {});
  await assert.rejects(
    () => window.CompanionProduct.request("POST", "host/targets/t1/roles/role-x/config/phone-connection", {action: "scan"}),
    error => {
      assert.strictEqual(error.code, "adb_not_found");
      assert.strictEqual(error.details?.reason, "explicit_path_is_dir");
      assert.strictEqual(error.details?.configured_adb_path, "D:/adb");
      assert.strictEqual(error.details?.config_source, "role_config");
      return true;
    },
  );
});
