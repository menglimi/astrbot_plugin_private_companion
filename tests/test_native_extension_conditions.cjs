/* 扩展设置 schema 的 condition 求值。
 *
 * reality 的 settings-schema.json 用 `condition` 描述字段可见性（例如网关关闭时
 * 令牌/监听地址/端口不应出现）。面板此前完全不处理 condition，于是默认关闭的
 * 网关仍把令牌、端口和地址全部渲染成可编辑项。
 *
 * 这里用最小的 DOM 替身驱动真实 Panel：只关心渲染出的 HTML 与 collect() 结果，
 * 不依赖 playwright。断言两件事：
 *   · condition 不满足的字段不再渲染（跟随现有「隐藏字段」习惯）；
 *   · 隐藏字段已填的值仍保留在草稿里，保存别的项时不会被清空。
 */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const PAGES = path.join(ROOT, "pages", "陪伴小屋");
const source = fs.readFileSync(path.join(PAGES, "native-extension-panel.js"), "utf8");
const base = path.join(ROOT, "astrbot_host", "native_extensions", "reality");
const schema = JSON.parse(fs.readFileSync(path.join(base, "settings-schema.json"), "utf8"));
const settings = JSON.parse(fs.readFileSync(path.join(base, "settings-defaults.json"), "utf8"));

function fakeRoot() {
  let html = "";
  const root = {
    isConnected: true,
    get innerHTML() { return html; },
    set innerHTML(value) { html = String(value); },
    addEventListener() {}, removeEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
    setAttribute() {},
  };
  return root;
}

function harness() {
  const record = {settings: structuredClone(settings), schema: structuredClone(schema),
    revision: "revision-reality", runtime: {state: "running", error_code: null}};
  const window = {
    CompanionProduct: {
      request: async () => ({extensions: {reality: structuredClone(record)}}),
      provider: () => ({owner: "companion_v2", assembled: true, desired_state: "active"}),
      extension: () => ({owner: "companion_v2", assembled: true, desired_state: "active"}),
      refreshIntegrations: async () => {},
    },
  };
  const context = vm.createContext({window, document: {querySelector() { return null; }},
    console, setTimeout, clearTimeout, structuredClone});
  vm.runInContext(source, context);
  return {panelApi: context.window.CompanionNativeExtensionPanel, record};
}

const settle = () => new Promise(resolve => setImmediate(resolve));

test("conditionMet follows the equality conditions the schemas actually use", () => {
  const {panelApi} = harness();
  assert.equal(typeof panelApi.conditionMet, "function", "面板应导出 condition 求值供测试与渲染共用");
  assert.equal(panelApi.conditionMet(undefined, {}), true, "没有 condition 的字段始终可见");
  assert.equal(panelApi.conditionMet(null, {}), true);
  assert.equal(panelApi.conditionMet({}, {}), true);
  assert.equal(panelApi.conditionMet({enabled: true}, {enabled: true}), true);
  assert.equal(panelApi.conditionMet({enabled: true}, {enabled: false}), false);
  assert.equal(panelApi.conditionMet({enabled: true}, {}), false, "缺失值不能当成满足条件");
  assert.equal(panelApi.conditionMet({enabled: true, amap_reverse_geocode_enabled: true},
    {enabled: true, amap_reverse_geocode_enabled: false}), false, "条件必须是 AND");
  assert.equal(panelApi.conditionMet({enabled: true, amap_reverse_geocode_enabled: true},
    {enabled: true, amap_reverse_geocode_enabled: true}), true);
});

test("mobile fields hide behind the gateway switch and keep unsaved values", async () => {
  const {panelApi} = harness();
  const root = fakeRoot();
  const panel = panelApi.mount(root, {extensionId: "reality", onlyGroup: "mobile"});
  await settle(); await settle();
  assert.match(root.innerHTML, /启用移动端网关/);
  assert.doesNotMatch(root.innerHTML, /移动端配对令牌/, "网关关闭时不应渲染令牌字段");
  assert.doesNotMatch(root.innerHTML, /移动端网关监听地址/, "网关关闭时不应渲染监听地址");
  assert.doesNotMatch(root.innerHTML, /移动端网关端口/, "网关关闭时不应渲染端口");

  /* 模拟用户先填过令牌、随后把网关关掉：隐藏字段的值必须留在草稿里。 */
  panel.draft.mobile.pairing_token = "synthetic-kept-token";
  panel.draft.mobile.enabled = false;
  panel.render();
  assert.doesNotMatch(root.innerHTML, /移动端配对令牌/);
  assert.equal(panel.collect().mobile.pairing_token, "synthetic-kept-token",
    "隐藏字段的值不能被 collect() 丢掉");

  panel.draft.mobile.enabled = true;
  panel.render();
  assert.match(root.innerHTML, /移动端配对令牌/);
  assert.match(root.innerHTML, /移动端网关监听地址/);
  assert.equal(panel.collect().mobile.pairing_token, "synthetic-kept-token");

  panel.draft.mobile.amap_reverse_geocode_enabled = false;
  panel.render();
  assert.doesNotMatch(root.innerHTML, /高德地图 Web 服务 API Key/, "逆地理编码关闭时 key 应隐藏");
  panel.draft.mobile.amap_reverse_geocode_enabled = true;
  panel.render();
  assert.match(root.innerHTML, /高德地图 Web 服务 API Key/);
});

test("nested camera conditions use the camera object as their scope", async () => {
  const {panelApi} = harness();
  const root = fakeRoot();
  const panel = panelApi.mount(root, {extensionId: "reality"});
  await settle(); await settle();
  assert.doesNotMatch(root.innerHTML, /摄像头索引/, "摄像头关闭时索引应隐藏");
  panel.draft.camera.enabled = true;
  panel.render();
  assert.match(root.innerHTML, /摄像头索引/);
  assert.doesNotMatch(root.innerHTML, /允许直接主动单帧的最低陪伴档位/,
    "主动视觉好奇关闭时其下级字段应隐藏");
  panel.draft.camera.proactive_curiosity_enabled = true;
  panel.render();
  assert.match(root.innerHTML, /允许直接主动单帧的最低陪伴档位/);
});
