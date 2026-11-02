"use strict";

const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.resolve(__dirname, "../pages/陪伴小屋/huawei-health-page.js"), "utf8");
const flush = async () => { for (let i = 0; i < 8; i += 1) await new Promise(resolve => setImmediate(resolve)); };

function status() {
  return {
    bound: false,
    cookie: {exists: false, count: 0, mtime: ""},
    token: {cached: false, left: 0, obtained_at: ""},
    credentials: {state: "absent", account_masked: ""},
    login: {state: "IDLE", message: "尚未开始登录", need: {}},
    browser: {found: true, path: "msedge.exe"},
    latest: null,
  };
}

function harness(requestOverride) {
  const requests = [];
  const listeners = new Map();
  const fields = new Map();
  const addField = (selector, kind, initial) => {
    const field = kind === "checkbox"
      ? {kind, checked: initial, defaultChecked: initial, value: ""}
      : {kind, value: initial || ""};
    fields.set(selector, field);
    return field;
  };
  addField("[data-hw-account]", "text", "");
  addField("[data-hw-password]", "text", "");
  addField("[data-hw-cookie-json]", "text", "");
  addField("[data-hw-sms]", "text", "");
  for (const name of ["CASTGC", "sid", "hwid_cas_sid", "HuaweiID_CAS_ISCASLOGIN", "CASLOGINSITE", "LOGINACCSITE"]) {
    addField(`[data-hw-cookie="${name}"]`, "text", "");
  }
  addField("[data-hw-login-auto-trust]", "checkbox", true);
  addField("[data-hw-login-prefer-auto]", "checkbox", false);
  addField("[data-hw-login-visible-fallback]", "checkbox", false);

  const root = {
    isConnected: true,
    _html: "",
    get innerHTML() { return this._html; },
    set innerHTML(value) {
      this._html = String(value);
      for (const field of fields.values()) {
        if (field.kind === "checkbox") field.checked = field.defaultChecked;
        else field.value = "";
      }
    },
    querySelector(selector) { return fields.get(selector) || null; },
    addEventListener(type, handler) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(handler);
    },
    removeEventListener(type, handler) {
      const handlers = listeners.get(type) || [];
      listeners.set(type, handlers.filter(item => item !== handler));
    },
    dispatch(type, event = {}) {
      for (const handler of listeners.get(type) || []) handler(event);
    },
  };
  const product = {
    request: requestOverride || (async (method, route, body, options) => {
      requests.push({method, route, body, options});
      if (route === "huawei-health/status") return status();
      if (route === "perception-native") return {model: {edit_token: "token", settings: {health_enabled: false}}};
      return {ok: true, status: status()};
    }),
  };
  const window = {CompanionProduct: product};
  vm.runInNewContext(source, {window, crypto, setInterval, clearInterval, setTimeout, clearTimeout, console});
  window.CompanionHuaweiHealthPage.mount(root);
  return {root, requests, page: window.CompanionHuaweiHealthPage, fields};
}

function click(root, data) {
  const button = {
    dataset: data,
    closest(selector) { return selector.includes("data-hw-action") || selector.includes("data-hw-trust") ? this : null; },
  };
  root.dispatch("click", {target: button});
}

test("Huawei binding card renders both cookie and account-password paths", async () => {
  const {root} = harness();
  await flush();
  assert.match(root.innerHTML, /运动健康/);
  assert.match(root.innerHTML, /方式一 · 账号密码登录/);
  assert.match(root.innerHTML, /方式二 · Cookie 导入/);
  assert.match(root.innerHTML, /导入 Cookie/);
  assert.match(root.innerHTML, /打开浏览器登录/);
  assert.match(root.innerHTML, /拉取最新健康数据/);
  assert.match(root.innerHTML, /data-hw-cookie="CASTGC"/);
  assert.match(root.innerHTML, /健康摘要进入当前私聊/);
  assert.match(root.innerHTML, /允许健康事件参与主动联系/);
});

test("buttons dispatch credential and cookie actions instead of doing nothing", async () => {
  const {root, requests, fields} = harness();
  await flush();
  fields.get("[data-hw-account]").value = "13378490269";
  fields.get("[data-hw-password]").value = "secret-pass";
  root.dispatch("input", {});
  click(root, {hwAction: "credential-save"});
  await flush();
  const save = requests.find(item => item.route === "huawei-health/credentials");
  assert.equal(JSON.stringify(save?.body), JSON.stringify({account: "13378490269", password: "secret-pass"}));
  assert.equal(fields.get("[data-hw-password]").value, "");

  fields.get('[data-hw-cookie="CASTGC"]').value = "cookie-value";
  fields.get("[data-hw-cookie-json]").value = '[{"name":"sid","value":"sid-value"}]';
  root.dispatch("input", {});
  click(root, {hwAction: "cookie-import"});
  await flush();
  const imported = requests.find(item => item.route === "huawei-health/cookie/import");
  assert.equal(imported?.body.json, '[{"name":"sid","value":"sid-value"}]');
  assert.equal(imported?.body.fields.CASTGC, "cookie-value");
  assert.equal(imported?.body.fields.sid, "");
});

test("cookie import shows the specific missing-cookie reason", async () => {
  const failure = Object.assign(new Error("huawei_health_cookie_invalid"), {
    data: {code: "huawei_health_cookie_invalid"},
    reason: "缺少必要的 cookie：CASTGC、sid。请把六项都填上，或改用整段 JSON 导出。",
  });
  const {root, fields} = harness(async (method, route) => {
    if (route === "huawei-health/status") return status();
    if (route === "perception-native") return {model: {edit_token: "token", settings: {health_enabled: false}}};
    throw failure;
  });
  await flush();
  assert.match(root.innerHTML, /\.id1\.cloud\.huawei\.com/);
  assert.match(root.innerHTML, /不在 health 域/);
  fields.get("[data-hw-cookie-json]").value = '[{"name":"CASLOGINSITE","value":"1"}]';
  root.dispatch("input", {});
  click(root, {hwAction: "cookie-import"});
  await flush();
  assert.match(root.innerHTML, /Cookie 导入失败：缺少必要的 cookie/);
  assert.match(root.innerHTML, /CASTGC/);
});

test("login actions forward checkbox choices and keep drafts across rerenders", async () => {
  const {root, requests, fields} = harness();
  await flush();
  fields.get("[data-hw-account]").value = "kept-account";
  fields.get("[data-hw-password]").value = "kept-password";
  fields.get("[data-hw-login-prefer-auto]").checked = true;
  fields.get("[data-hw-login-visible-fallback]").checked = true;
  root.dispatch("change", {});
  click(root, {hwAction: "login-start"});
  await flush();
  const started = requests.find(item => item.route === "huawei-health/login/start");
  assert.equal(JSON.stringify(started?.body), JSON.stringify({auto_trust: true, prefer_auto: true, visible_fallback: true}));
  assert.equal(fields.get("[data-hw-account]").value, "kept-account");
  assert.equal(fields.get("[data-hw-password]").value, "kept-password");
});

test("trust choices call the existing backend route", async () => {
  const {root, requests} = harness();
  await flush();
  click(root, {hwTrust: "later"});
  await flush();
  const trust = requests.find(item => item.route === "huawei-health/login/trust");
  assert.equal(JSON.stringify(trust?.body), JSON.stringify({choice: "later"}));
});
