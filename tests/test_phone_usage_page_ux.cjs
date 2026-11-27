"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const PAGES = path.join(ROOT, "pages", "陪伴小屋");
const readPage = name => fs.readFileSync(path.join(PAGES, name), "utf8");

function loadPhoneSetupModule() {
  const window = {
    CompanionProduct: {
      request: async () => ({}),
      errorLabel: code => code,
    },
  };
  const sandbox = vm.createContext({
    window,
    document: {},
    console,
    Array,
    Object,
    String,
    Boolean,
    Promise,
    FormData: class {},
    crypto: {randomUUID: () => "op-phone-ux-1"},
  });
  vm.runInContext(readPage("phone-setup.js"), sandbox);
  return sandbox.window.CompanionPhoneSetup;
}

const plain = value => JSON.parse(JSON.stringify(value));

function loadConfigEditorSpec() {
  const window = {};
  const sandbox = vm.createContext({window, console, Object, Array, Set, Map, JSON});
  vm.runInContext(readPage("config-editor.js"), sandbox);
  return sandbox.window.CompanionConfigEditors.spec;
}

function makeMiniElement(tagName) {
  const listeners = new Map();
  const attrs = new Map();
  const classes = new Set();
  const children = [];
  const el = {
    nodeType: 1,
    tagName: String(tagName || "div").toUpperCase(),
    dataset: {},
    hidden: false,
    disabled: false,
    type: "",
    value: "",
    title: "",
    textContent: "",
    children,
    parentElement: null,
    get className() { return [...classes].join(" "); },
    set className(val) {
      classes.clear();
      for (const token of String(val || "").split(/\s+/).filter(Boolean)) classes.add(token);
    },
    classList: {
      add: (...names) => names.forEach(name => classes.add(name)),
      remove: (...names) => names.forEach(name => classes.delete(name)),
      toggle(name, force) {
        const next = force === undefined ? !classes.has(name) : Boolean(force);
        if (next) classes.add(name); else classes.delete(name);
        return next;
      },
      contains: name => classes.has(name),
    },
    setAttribute(name, val) {
      attrs.set(name, String(val));
      if (name === "placeholder") el.placeholder = String(val);
    },
    getAttribute(name) { return attrs.has(name) ? attrs.get(name) : null; },
    append(...nodes) {
      for (const node of nodes) {
        if (!node) continue;
        node.parentElement = el;
        children.push(node);
      }
    },
    insertBefore(node, ref) {
      node.parentElement = el;
      const index = children.indexOf(ref);
      if (index >= 0) children.splice(index, 0, node);
      else children.push(node);
      return node;
    },
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(fn);
    },
    dispatchEvent(event) {
      const ev = event && typeof event === "object" ? event : {type: String(event)};
      if (!ev.target) ev.target = el;
      for (const fn of listeners.get(ev.type) || []) fn(ev);
      if (ev.bubbles && el.parentElement) el.parentElement.dispatchEvent(ev);
      return true;
    },
    matches(selector) {
      if (selector === ".wb-group") return classes.has("wb-group");
      if (selector === ".wb-group-head") return classes.has("wb-group-head");
      if (selector === ".wb-field:not([hidden])") return classes.has("wb-field") && !el.hidden;
      if (selector === ".wb-control") return classes.has("wb-control");
      if (selector === ".wb-textarea-wrap") return classes.has("wb-textarea-wrap");
      if (selector === ".phone-group-count, .wb-group-count") {
        return classes.has("phone-group-count") || classes.has("wb-group-count");
      }
      if (selector === '[data-wb-row="allowed_actions"]') return el.dataset.wbRow === "allowed_actions";
      if (selector === 'textarea[data-wb-field="allowed_actions"]') {
        return el.tagName === "TEXTAREA" && el.dataset.wbField === "allowed_actions";
      }
      if (selector === 'textarea[data-wb-field="allowed_subjects"]') {
        return el.tagName === "TEXTAREA" && el.dataset.wbField === "allowed_subjects";
      }
      if (selector === "[data-phone-action-picker]") return Object.hasOwn(el.dataset, "phoneActionPicker");
      if (selector === "[data-phone-action-key]") return Object.hasOwn(el.dataset, "phoneActionKey");
      if (selector === "[data-phone-action-summary]") return Object.hasOwn(el.dataset, "phoneActionSummary");
      if (selector === "[data-phone-action-warn]") return Object.hasOwn(el.dataset, "phoneActionWarn");
      if (selector === "[data-phone-action-warn-text]") return Object.hasOwn(el.dataset, "phoneActionWarnText");
      if (selector === "[data-phone-action-restore]") return Object.hasOwn(el.dataset, "phoneActionRestore");
      return false;
    },
    closest(selector) {
      let cur = el;
      while (cur) {
        if (cur.matches(selector)) return cur;
        cur = cur.parentElement;
      }
      return null;
    },
    querySelectorAll(selector) {
      const found = [];
      const walk = node => {
        for (const child of node.children) {
          if (child.matches(selector)) found.push(child);
          walk(child);
        }
      };
      walk(el);
      return found;
    },
    querySelector(selector) { return el.querySelectorAll(selector)[0] || null; },
  };
  return el;
}

test("手机使用页面 CSS 布局契约：取消不对称双列跨行并改为全宽分组白卡 + 均衡 2 列等高子卡", () => {
  const css = readPage("native-extension-panel.css");

  assert.doesNotMatch(css, /\.phone-usage-page\s+\[data-wb-group="master"\]\s*\{\s*grid-row:\s*span\s+2/,
    "不应保留导致左下 450px 大空白的 master grid-row: span 2 布局");
  assert.match(css, /\.phone-usage-page\s+\.wb-card-body\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/);
  assert.match(css, /\.phone-usage-page\s+\.wb-group\s*\{[^}]*background:\s*var\(--t-surface-raised\)/);
  assert.match(css, /\.phone-usage-page\s+\.wb-fields\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)[^}]*align-items:\s*stretch/);
  assert.match(css, /\.phone-usage-page\s+\[data-wb-group="typing"\]\s+\[data-wb-row="unicode_input"\]\s*\{\s*grid-column:\s*1\s*\/\s*-1;/);
  assert.match(css, /\.phone-usage-page\s+\[data-wb-group="scope"\]\s+\.wb-field:has\(textarea\)\s*\{\s*grid-column:\s*auto;/);
  assert.match(css, /@container\s+phone-page\s+\(max-width:\s*720px\)\s*\{[\s\S]*?\.phone-usage-page\s+\.wb-fields\s*\{\s*grid-template-columns:\s*minmax\(0,\s*1fr\)/);
  assert.match(css, /@media\s*\(prefers-reduced-motion:\s*reduce\)[\s\S]*?\.phone-action-chip/);
  assert.match(css, /:root\[data-motion="off"\]\s+\.phone-action-chip/);
});

test("device.phone 配置契约：保留全部 9 项字段、3 组语义说明与分段中文输入控件", () => {
  const phoneSpec = loadConfigEditorSpec()["device.phone"];
  assert.ok(phoneSpec);
  assert.equal(phoneSpec.order.length, 9);
  assert.deepEqual(plain(phoneSpec.segmentedChoices), ["unicode_input"]);
  assert.equal(phoneSpec.sections.length, 3);
  for (const section of phoneSpec.sections) {
    assert.ok(section.title && section.help, `分组 ${section.id} 必须具备标题与副说明`);
  }
  assert.deepEqual(plain(phoneSpec.sections.map(s => s.fields)), [
    ["enabled", "adb_path", "serial", "connect"],
    ["unicode_input", "ime_restore_seconds", "max_steps"],
    ["allowed_actions", "allowed_subjects"],
  ]);
});

test("允许的动作胶囊纯逻辑：覆盖全部 10 个后端动作、必选项保护、谨慎项切换与自定义扩展保留", () => {
  const PhoneSetup = loadPhoneSetupModule();
  const keys = plain(PhoneSetup.ACTION_CATALOG.map(item => item.key));
  assert.deepEqual(keys, [
    "launch", "tap_text", "tap", "swipe", "type", "key", "wait_text", "done", "fail", "force_stop",
  ]);

  const defaults = "launch,tap_text,tap,swipe,type,key,wait_text,done,fail";
  // 关闭普通动作 swipe
  const withoutSwipe = PhoneSetup.toggleAllowedAction(defaults, "swipe");
  assert.equal(withoutSwipe, "launch,tap_text,tap,type,key,wait_text,done,fail");

  // 重新开启普通动作 swipe，自动排在必选 done/fail 之前
  const withSwipeBack = PhoneSetup.toggleAllowedAction(withoutSwipe, "swipe");
  assert.equal(withSwipeBack, "launch,tap_text,tap,type,key,wait_text,swipe,done,fail");

  // 必选动作 done / fail 处于启用态时不可被误删
  assert.equal(PhoneSetup.toggleAllowedAction(defaults, "done"), defaults);
  assert.equal(PhoneSetup.toggleAllowedAction(defaults, "fail"), defaults);

  // 谨慎动作 force_stop 可显式开启与关闭
  const withForceStop = PhoneSetup.toggleAllowedAction(defaults, "force_stop");
  assert.equal(withForceStop, "launch,tap_text,tap,swipe,type,key,wait_text,force_stop,done,fail");
  assert.equal(PhoneSetup.toggleAllowedAction(withForceStop, "force_stop"), defaults);

  // 高级用户手动加入自定义扩展项时，切换其他胶囊严格保留自定义项
  const withCustom = "launch,tap_text,custom_macro,done,fail";
  const toggledCustom = PhoneSetup.toggleAllowedAction(withCustom, "swipe");
  assert.equal(toggledCustom, "launch,tap_text,custom_macro,swipe,done,fail");
  const analyzed = plain(PhoneSetup.analyzeAllowedActions(toggledCustom));
  assert.deepEqual(analyzed.customTokens, ["custom_macro"]);
  assert.deepEqual(analyzed.missingRequired, []);

  // 手动删掉 done/fail 时检出缺失并支持一键补回，同时保留自定义项
  const broken = "launch，custom_ext";
  const brokenInfo = plain(PhoneSetup.analyzeAllowedActions(broken));
  assert.deepEqual(brokenInfo.missingRequired, ["done", "fail"]);
  assert.deepEqual(brokenInfo.customTokens, ["custom_ext"]);
  assert.equal(PhoneSetup.restoreRequiredActions(broken), "launch,custom_ext,done,fail");
  assert.equal(PhoneSetup.toggleAllowedAction(broken, "done"), "launch,custom_ext,done");
});

test("可视化动作选择器 DOM 双向同步：点击胶囊更新文本框并触发 input，手改文本框同步胶囊与告警", () => {
  const window = {CompanionProduct: {request: async () => ({}), errorLabel: c => c}};
  const document = {createElement: makeMiniElement};
  const sandbox = vm.createContext({
    window,
    document,
    console,
    Array,
    Object,
    String,
    Boolean,
    Promise,
    Event: class { constructor(type, opts = {}) { this.type = type; this.bubbles = Boolean(opts.bubbles); } },
    FormData: class {},
    crypto: {randomUUID: () => "op-dom-1"},
  });
  vm.runInContext(readPage("phone-setup.js"), sandbox);
  const PhoneSetup = sandbox.window.CompanionPhoneSetup;

  const adbRoot = makeMiniElement("section");
  const group = makeMiniElement("section");
  group.className = "wb-group";
  const head = makeMiniElement("h4");
  head.className = "wb-group-head";
  head.textContent = "操作授权与访问范围";

  const actionRow = makeMiniElement("div");
  actionRow.className = "wb-field";
  actionRow.dataset.wbRow = "allowed_actions";
  const control = makeMiniElement("div");
  control.className = "wb-control";
  const wrap = makeMiniElement("div");
  wrap.className = "wb-textarea-wrap";
  const textarea = makeMiniElement("textarea");
  textarea.dataset.wbField = "allowed_actions";
  textarea.value = "launch,tap_text,tap,swipe,type,key,wait_text,done,fail";
  wrap.append(textarea);
  control.append(wrap);
  actionRow.append(control);

  const subjectRow = makeMiniElement("div");
  subjectRow.className = "wb-field";
  subjectRow.dataset.wbRow = "allowed_subjects";
  const subjectArea = makeMiniElement("textarea");
  subjectArea.dataset.wbField = "allowed_subjects";
  subjectRow.append(subjectArea);

  group.append(head, actionRow, subjectRow);
  adbRoot.append(group);

  let inputEvents = 0;
  textarea.addEventListener("input", () => { inputEvents += 1; });

  PhoneSetup.enhanceAdvancedWorkspace(adbRoot);
  // 重复调用保持幂等，不重复挂载
  PhoneSetup.enhanceAdvancedWorkspace(adbRoot);

  assert.equal(group.querySelectorAll(".phone-group-count, .wb-group-count").length, 1);
  assert.equal(group.querySelector(".phone-group-count, .wb-group-count").textContent, "2 项");
  assert.match(subjectArea.getAttribute("placeholder") || "", /onebot-user:10001/);

  const picker = control.querySelector("[data-phone-action-picker]");
  assert.ok(picker);
  assert.equal(picker.querySelectorAll("[data-phone-action-key]").length, 10);
  assert.equal(picker.querySelector("[data-phone-action-summary]").textContent, "已选 9 / 10 项");
  assert.equal(picker.querySelector("[data-phone-action-warn]").hidden, true);

  // 点击 force_stop 胶囊 -> 同步写入 textarea 并触发 input 事件
  const forceStopBtn = picker.querySelectorAll("[data-phone-action-key]")
    .find(btn => btn.dataset.phoneActionKey === "force_stop");
  assert.equal(forceStopBtn.getAttribute("aria-pressed"), "false");
  picker.dispatchEvent({type: "click", target: forceStopBtn});
  assert.equal(inputEvents, 1);
  assert.match(textarea.value, /\bforce_stop\b/);
  assert.equal(forceStopBtn.getAttribute("aria-pressed"), "true");
  assert.equal(picker.querySelector("[data-phone-action-summary]").textContent, "已选 10 / 10 项");

  // 点击必选 done -> 保持不可移除，不触发无效 input
  const doneBtn = picker.querySelectorAll("[data-phone-action-key]")
    .find(btn => btn.dataset.phoneActionKey === "done");
  picker.dispatchEvent({type: "click", target: doneBtn});
  assert.equal(inputEvents, 1);
  assert.match(textarea.value, /\bdone\b/);

  // 手动编辑 textarea 删除 done 并追加自定义项 -> 胶囊实时同步 + 告警显示
  textarea.value = "launch,custom_step,fail";
  textarea.dispatchEvent({type: "input", bubbles: true, target: textarea});
  assert.equal(doneBtn.getAttribute("aria-pressed"), "false");
  assert.equal(picker.querySelector("[data-phone-action-summary]").textContent, "已选 2 / 10 项 · 含 1 项自定义");
  const warn = picker.querySelector("[data-phone-action-warn]");
  assert.equal(warn.hidden, false);
  assert.match(picker.querySelector("[data-phone-action-warn-text]").textContent, /done/);

  // 点击「补回必选项」-> 自动补回 done 且保留 custom_step
  const restoreBtn = picker.querySelector("[data-phone-action-restore]");
  picker.dispatchEvent({type: "click", target: restoreBtn});
  assert.equal(textarea.value, "launch,custom_step,fail,done");
  assert.equal(warn.hidden, true);
  assert.equal(doneBtn.getAttribute("aria-pressed"), "true");
});

test("MutationObserver 防重入回归：自身挂载动作选择器与同步计数不会触发无限观测循环", () => {
  const events = [];
  class FakeMutationObserver {
    constructor(cb) {
      this.cb = cb;
      this.connected = false;
    }
    observe(target) {
      this.connected = true;
      events.push(["observe", target.tagName]);
    }
    disconnect() {
      this.connected = false;
      events.push(["disconnect"]);
    }
  }
  const setupRoot = makeMiniElement("section");
  setupRoot.isConnected = true;
  const adbRoot = makeMiniElement("section");
  const group = makeMiniElement("section");
  group.className = "wb-group";
  const head = makeMiniElement("h4");
  head.className = "wb-group-head";
  const row = makeMiniElement("div");
  row.className = "wb-field";
  row.dataset.wbRow = "allowed_actions";
  const control = makeMiniElement("div");
  control.className = "wb-control";
  const textarea = makeMiniElement("textarea");
  textarea.dataset.wbField = "allowed_actions";
  textarea.value = "launch,done,fail";
  control.append(textarea);
  row.append(control);
  group.append(head, row);
  adbRoot.append(group);

  const window = {CompanionProduct: {request: async () => ({}), errorLabel: c => c}};
  const document = {createElement: makeMiniElement};
  const sandbox = vm.createContext({
    window, document, console, Array, Object, String, Boolean, Promise,
    MutationObserver: FakeMutationObserver,
    FormData: class {},
    crypto: {randomUUID: () => "op-mo-1"},
  });
  // Minimal querySelector shims for setupRoot.innerHTML = markup()
  const origQ = setupRoot.querySelector;
  setupRoot.querySelector = sel => origQ(sel) || {hidden: true, textContent: "", classList: makeMiniElement("div").classList, replaceChildren() {}, append() {}};
  vm.runInContext(readPage("phone-setup.js"), sandbox);
  const setup = sandbox.window.CompanionPhoneSetup.mount(setupRoot, {
    configApi: "/astrbot_plugin_private_companion/targets/t1/roles/r1/config",
    editor: {state: {instances: [{state: {status: {settings: {enabled: true}}}}]}},
    adbRoot,
  });
  // Calling enhance() disconnects observer before DOM updates and re-observes cleanly after.
  events.length = 0;
  setup.enhance();
  assert.deepEqual(events, [["disconnect"], ["observe", "SECTION"]]);
  assert.equal(control.querySelectorAll("[data-phone-action-picker]").length, 1);
});
