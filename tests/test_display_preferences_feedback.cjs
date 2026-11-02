/* 外观偏好保存失败必须看得见。
 *
 * appearance-page.js 明确承诺「保存到当前账号 · 同一账号换设备也会同步」，
 * 但 product-page.js 的显示偏好保存只 console.warn。桥接请求失败时用户以为
 * 已保存，换设备才发现回到默认。这里守两件事：
 *   · 失败仍保留 console.warn 诊断；
 *   · 失败通过现有的 changed() 事件通道发出可见提示（app.js 用 toast 呈现）。
 */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const SOURCE = fs.readFileSync(
  path.join(__dirname, "..", "pages", "陪伴小屋", "product-page.js"), "utf8");
const HOST = "astrbot_plugin_private_companion";

function harness() {
  const warnings = [];
  const window = {
    addEventListener() {}, removeEventListener() {},
    matchMedia: () => ({matches: false, addEventListener() {}}),
    location: {hash: "", href: "http://localhost/#life"},
    history: {replaceState() {}},
    innerWidth: 1440, innerHeight: 900,
    requestAnimationFrame: callback => { callback(0); return 0; }, cancelAnimationFrame() {},
    sessionStorage: {getItem: () => null, setItem() {}},
    localStorage: {getItem: () => null, setItem() {}, removeItem() {}},
  };
  const document = {
    readyState: "interactive", documentElement: {dataset: {}},
    head: {appendChild() {}}, body: {classList: {add() {}, remove() {}, contains: () => false}, appendChild() {}},
    querySelector: () => null, querySelectorAll: () => [],
    createElement: () => ({dataset: {}, classList: {add() {}}, addEventListener() {}, setAttribute() {}, remove() {}}),
    addEventListener() {},
  };
  const bridge = {
    ready: async () => ({pluginName: HOST}),
    apiGet: async route => {
      if (route === "page/display-preferences") return {preferences: null};
      if (route === "page/targets") return {installation_id: "installation-a", targets: []};
      if (route === "integration/manifest") return {summary: {connected_count: 0}, extensions: []};
      return {};
    },
    apiPost: async route => {
      if (route === "page/display-preferences") {
        const error = new Error("display preferences unavailable");
        error.code = "display_preferences_unavailable";
        throw error;
      }
      return {};
    },
  };
  window.AstrBotPluginPage = bridge;
  const consoleMock = {...console, warn: (...args) => warnings.push(args)};
  vm.runInNewContext(SOURCE, {window, document, navigator: {language: "zh-CN"},
    setTimeout, clearTimeout, Promise, Object, JSON, Array, String, Number, Boolean,
    Math, Date, Error, console: consoleMock, matchMedia: window.matchMedia,
    localStorage: window.localStorage, sessionStorage: window.sessionStorage});
  return {product: window.CompanionProduct, warnings};
}

test("a failed display preference save keeps diagnostics and emits a visible reason", async () => {
  const {product, warnings} = harness();
  const events = [];
  await product.connect(reason => events.push(reason));
  product.setAppearance("theme", "night");
  await new Promise(resolve => setTimeout(resolve, 200));
  assert.ok(warnings.some(args => /were not saved/.test(String(args[0]))),
    "保存失败仍应保留 console.warn 诊断");
  const failure = events.find(reason => reason?.kind === "display-preferences-error");
  assert.ok(failure, "保存失败应通过 changed() 发出可见提示事件");
  assert.match(String(failure.message), /外观偏好/);
  assert.equal(failure.code, "display_preferences_unavailable");
});
