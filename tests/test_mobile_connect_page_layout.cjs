"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const PAGES = path.join(ROOT, "pages", "陪伴小屋");
const panelSource = fs.readFileSync(path.join(PAGES, "native-extension-panel.js"), "utf8");
const panelCss = fs.readFileSync(path.join(PAGES, "native-extension-panel.css"), "utf8");
const phoneSetupSource = fs.readFileSync(path.join(PAGES, "phone-setup.js"), "utf8");
const schema = JSON.parse(fs.readFileSync(path.join(ROOT, "astrbot_host/native_extensions/reality/settings-schema.json"), "utf8"));
const defaults = JSON.parse(fs.readFileSync(path.join(ROOT, "astrbot_host/native_extensions/reality/settings-defaults.json"), "utf8"));

const settle = () => new Promise(resolve => setImmediate(resolve));

function interactiveRoot() {
  const listeners = new Map();
  let html = "";
  let fieldStates = new Map();
  let searchInput = {value: "", focus() {}};
  let clearBtn = {hidden: true, hasAttribute: attr => attr === "data-mobile-search-clear", dataset: {}, disabled: false};
  const root = {
    isConnected: true,
    get innerHTML() { return html; },
    set innerHTML(value) {
      html = String(value);
      fieldStates = new Map();
      const inputRe = /<input[^>]*id="([^"]+)"[^>]*data-native-field="(\d+)"([^>]*)>/g;
      let match;
      while ((match = inputRe.exec(html)) !== null) {
        const [, id, idx, rest] = match;
        const full = match[0];
        const isCheckbox = /type="checkbox"/.test(full);
        const valMatch = /value="([^"]*)"/.exec(full);
        fieldStates.set(id, {
          id,
          dataset: {nativeField: idx},
          type: isCheckbox ? "checkbox" : "text",
          checked: isCheckbox && /\bchecked\b/.test(full),
          value: valMatch ? valMatch[1] : "",
          disabled: false,
          focused: false,
          focus() { this.focused = true; },
          hasAttribute: attr => attr === "data-native-field",
        });
      }
      const searchVal = /data-native-search value="([^"]*)"/.exec(html);
      searchInput = {
        value: searchVal ? searchVal[1] : "",
        focused: false,
        focus() { this.focused = true; },
        hasAttribute: attr => attr === "data-native-search",
      };
      clearBtn = {
        hidden: /data-mobile-search-clear[^>]*\bhidden\b/.test(html),
        disabled: false,
        dataset: {},
        hasAttribute: attr => attr === "data-mobile-search-clear",
        closest: sel => sel === "button" ? clearBtn : null,
      };
    },
    addEventListener(type, fn) { listeners.set(type, fn); },
    removeEventListener(type) { listeners.delete(type); },
    contains() { return true; },
    setAttribute() {},
    querySelector(selector) {
      if (selector.startsWith("#")) return fieldStates.get(selector.slice(1)) || null;
      if (selector === "[data-native-search]") return searchInput;
      if (selector === "[data-mobile-search-clear]") return clearBtn;
      if (selector === "[data-native-save]") return {disabled: false};
      if (selector === "[data-native-reload]") return {disabled: false, textContent: ""};
      if (selector === "[data-native-notice]") return {textContent: "", classList: {toggle() {}}};
      if (selector === "[data-native-lifecycle]") return {innerHTML: ""};
      return null;
    },
    querySelectorAll() { return []; },
    fire(type, event) { listeners.get(type)?.(event); },
    fieldNode(id) { return fieldStates.get(id); },
    get searchInput() { return searchInput; },
    get clearBtn() { return clearBtn; },
  };
  return root;
}

function mountMobile(overrides = {}) {
  const record = {
    settings: structuredClone(defaults),
    schema: structuredClone(schema),
    revision: "rev-mobile-1",
    runtime: {state: "running", error_code: null},
    management: {
      configuration: {mobile: {enabled: false, running: false, bound_port: 6322, core_user_id: "1538054427"}},
      mobile_observations: [],
    },
    ...overrides,
  };
  const window = {
    CompanionProduct: {
      request: async () => ({extensions: {reality: structuredClone(record)}}),
      extension: () => ({owner: "companion_v2", assembled: true, desired_state: "active", installed_revision: 1}),
      errorLabel: () => "",
      refreshIntegrations: async () => {},
    },
  };
  const sandbox = vm.createContext({
    window,
    document: {activeElement: null, body: {}},
    console, Intl, Date, Object, Set, Map, JSON, structuredClone,
    setTimeout, clearTimeout,
    navigator: {clipboard: {writeText: async () => {}}},
    crypto: {randomUUID: () => "op-mobile-1"},
  });
  vm.runInContext(panelSource, sandbox);
  const root = interactiveRoot();
  const panel = sandbox.window.CompanionNativeExtensionPanel.mount(root, {extensionId: "reality", onlyGroup: "mobile"});
  return {panel, root, record};
}

test("手机端连接顶部双卡 Bento 结构：全局与状态内嵌左卡头栏、6 项微指标卡、右卡配对与模块分层", async () => {
  const {panel, root} = mountMobile();
  await settle(); await settle();
  const html = root.innerHTML;

  // Left card integrates title, 全局 pill, status badge, and refresh button cleanly.
  assert.match(html, /class="mobile-gateway-head-main"[\s\S]*?<h3 id="mobileGatewayTitle">连接状态<\/h3>[\s\S]*?class="mobile-scope-pill">全局<\/span>[\s\S]*?class="native-extension-status idle">未启用<\/span>/);
  assert.match(html, /data-mobile-refresh/);

  // 6 structured metric tiles with address & health spans.
  assert.equal((html.match(/class="mobile-gateway-tile\b/g) || []).length, 6);
  assert.match(html, /class="mobile-gateway-tile is-wide"><dt>连接地址<\/dt>/);
  assert.match(html, /class="mobile-gateway-tile is-full mobile-gateway-address"><dt>健康检查<\/dt>/);

  // Right pairing card displays chip, guidance, primary generate action, and bottom lifecycle slot.
  assert.match(html, /class="mobile-pairing-head"[\s\S]*?<h3 id="mobilePairingTitle">配对令牌<\/h3>[\s\S]*?class="mobile-pairing-chip ">待生成<\/span>/);
  assert.match(html, /data-mobile-token-action="generate"[\s\S]*?生成并启用网关/);
  assert.match(html, /class="mobile-gateway-lifecycle" data-native-lifecycle/);

  // Compact search box includes icon and hidden clear button.
  assert.match(html, /class="native-extension-search is-mobile-search"[\s\S]*?data-lucide="search"[\s\S]*?data-native-search[\s\S]*?data-mobile-search-clear[^>]*hidden/);

  // Disabled gateway shows 1 full-width gateway section with accurate item count and data-field hook.
  assert.equal((html.match(/data-mobile-config-section=/g) || []).length, 1);
  assert.match(html, /class="mobile-config-count">1 项<\/span>/);
  assert.match(html, /data-field="mobile\.enabled"/);

  panel.stop();
});

test("手机端连接：切换启用网关开关实时展开全部 5 组配置并保留搜索清空交互", async () => {
  const {panel, root} = mountMobile();
  await settle(); await settle();

  // Live toggle mobile.enabled ON reveals all 5 sections and exposes data-field hooks.
  const enabledMeta = panel.fields.find(item => item.path.join(".") === "mobile.enabled");
  assert.ok(enabledMeta);
  const checkbox = root.fieldNode(enabledMeta.id);
  checkbox.checked = true;
  root.fire("input", {type: "input", target: checkbox});

  assert.equal(panel.dirty, true);
  assert.equal((root.innerHTML.match(/data-mobile-config-section=/g) || []).length, 5);
  assert.match(root.innerHTML, /data-field="mobile\.host"/);
  assert.match(root.innerHTML, /data-field="mobile\.pairing_token"/);
  assert.match(root.innerHTML, /data-field="mobile\.location_ttl_seconds"/);
  assert.match(root.innerHTML, /data-field="mobile\.activity_enabled"/);
  assert.match(root.innerHTML, /data-field="mobile\.proxy_rooms"/);

  // Typing a search query unhides clear button; clicking clear resets query cleanly.
  root.searchInput.value = "高德";
  root.fire("input", {type: "input", target: root.searchInput});
  assert.equal(panel.query, "高德");
  assert.equal(root.clearBtn.hidden, false);

  root.fire("click", {target: root.clearBtn});
  assert.equal(panel.query, "");
  assert.equal(root.clearBtn.hidden, true);
  assert.equal(root.searchInput.focused, true);

  panel.stop();
});

test("手机端连接与手机使用 CSS 布局契约：全通栏卡片零右半空白、双卡等高、零 rgba、容器断点完整", () => {
  // Orphaned global badge hidden on mobile section.
  assert.match(panelCss, /#homeShell\[data-section="mobile"\] \.role-context-badge\s*\{\s*display:\s*none;\s*\}/);

  // Top Bento overview stretches both cards to equal height.
  assert.match(panelCss, /\.mobile-connection-overview\s*\{[^}]*align-items:\s*stretch/);
  assert.match(panelCss, /\.mobile-pairing-card\s*\{[^}]*display:\s*flex;\s*flex-direction:\s*column/);
  assert.match(panelCss, /\.mobile-gateway-lifecycle\s*\{[^}]*margin-top:\s*auto/);

  // Config sections strictly single-column full width (eliminating 48% left-half void).
  assert.match(panelCss, /\.mobile-config-sections\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/);
  assert.doesNotMatch(panelCss, /\.mobile-config-sections\s*\{[^}]*repeat\(2/);

  // Inside each full-width section, 2-col equal-height grid + full-span odd/single tail field.
  assert.match(panelCss, /\.mobile-config-section-body\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\);[^}]*align-items:\s*stretch/);
  assert.match(panelCss, /\.mobile-config-section-body > \.native-extension-field:last-child:nth-child\(odd\)/);

  // Compact search input styling & safe bottom spacing.
  assert.match(panelCss, /\.mobile-config-heading \.native-extension-search\.is-mobile-search\s*\{[^}]*min-height:\s*36px/);
  assert.match(panelCss, /\.is-mobile-connection\s*\{[^}]*padding-bottom:\s*var\(--sp-6\)/);

  // Container queries cover gateway, config, and page breakpoints.
  assert.match(panelCss, /@container mobile-gateway \(max-width: 480px\)/);
  assert.match(panelCss, /@container mobile-config \(max-width: 660px\)/);
  assert.match(panelCss, /@container mobile-page \(max-width: 820px\)/);

  // Adjacent phone usage page upgraded to full-width Bento card + enabled status pill.
  assert.match(panelCss, /\.phone-setup\s*\{[^}]*max-width:\s*none;[^}]*background:\s*var\(--t-surface-raised\)/);
  assert.match(panelCss, /\.phone-setup-head span\.is-enabled\s*\{[^}]*background:\s*var\(--t-ok-quiet\)/);

  // Zero hardcoded rgba in native-extension-panel.css.
  assert.doesNotMatch(panelCss, /rgba\s*\(/i);
});

test("手机使用页用编辑器的角色入口扫描，并显示 ADB 配置来源", async () => {
  const classes = new Set();
  const statusEl = {
    hidden: true,
    textContent: "",
    classList: {
      toggle(name, force) {
        if (force) classes.add(name);
        else classes.delete(name);
      },
    },
  };
  const listeners = new Map();
  const statusNotice = {textContent: "", classList: {toggle() {}}};
  const download = {hidden: true};
  const root = {
    isConnected: true,
    innerHTML: "",
    addEventListener(type, listener) { listeners.set(type, listener); },
    querySelector(sel) {
      if (sel === "[data-phone-enabled]") return statusEl;
      if (sel === "[data-phone-notice]") return statusNotice;
      if (sel === "[data-phone-download]") return download;
      return {hidden: true, textContent: "", classList: {toggle() {}}, replaceChildren() {}, append() {}};
    },
    querySelectorAll() { return []; },
  };
  const requests = [];
  const window = {CompanionProduct: {async request(method, path, body) {
    requests.push({method, path, body});
    return {adb_error: {code:"adb_not_found", adb_path:"E:/Android/平台工具/adb.exe",
      source:"role", role_id:"role-t1"}};
  }}};
  const sandbox = vm.createContext({window, document: {}, console});
  vm.runInContext(phoneSetupSource, sandbox);
  const setup = sandbox.window.CompanionPhoneSetup.mount(root, {
    editor: {endpoint: "/astrbot_plugin_private_companion/targets/t1/roles/r1/config",
      state: {instances: [{state: {status: {settings: {enabled: false}}}}]}},
  });
  assert.equal(statusEl.textContent, "未启用");
  assert.equal(classes.has("is-enabled"), false);

  setup.syncStatus({settings: {enabled: true}});
  assert.equal(statusEl.textContent, "已启用");
  assert.equal(classes.has("is-enabled"), true);

  const scan = {dataset:{}, hasAttribute:name => name === "data-phone-scan"};
  listeners.get("click")({target:{closest: () => scan}});
  await settle();
  assert.equal(requests[0].path, "targets/t1/roles/r1/config/phone-connection");
  assert.equal(requests[0].body.include_diagnostics, true);
  assert.match(statusNotice.textContent, /E:\/Android\/平台工具\/adb\.exe.*角色层.*role-t1/);
  assert.equal(download.hidden, false);
});
