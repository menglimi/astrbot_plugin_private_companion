"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const PAGES = path.resolve(__dirname, "../pages/陪伴小屋");
const hintSource = fs.readFileSync(path.join(PAGES, "memory-companion-hint.js"), "utf8");
const setupSource = fs.readFileSync(path.join(PAGES, "setup-guide.js"), "utf8");
const migrationReviewSource = fs.readFileSync(path.join(PAGES, "migration-config-review.js"), "utf8");
const migrationTableSource = fs.readFileSync(path.join(PAGES, "migration-config-table.js"), "utf8");
const appearanceSource = fs.readFileSync(path.join(PAGES, "appearance-page.js"), "utf8");
const setupCssSource = fs.readFileSync(path.join(PAGES, "setup-guide.css"), "utf8");
const hintCssSource = fs.readFileSync(path.join(PAGES, "memory-companion-hint.css"), "utf8");
const hostSource = fs.readFileSync(path.resolve(__dirname, "../pages/宿主管理/app.js"), "utf8");

const flush = () => new Promise(resolve => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 12; i += 1) await flush(); };

function hintHarness(responses = {}) {
  const calls = [];
  const window = {
    CompanionProduct: {
      request(method, route, body) {
        calls.push({method, route, body});
        const key = `${method} ${route}`;
        const value = responses[key];
        return Promise.resolve(typeof value === "function" ? value(body) : value);
      },
    },
  };
  const context = vm.createContext({window, document: {}, Promise, Set, Map, JSON, String, Boolean, Error});
  vm.runInContext(hintSource, context);
  return {hint: context.window.CompanionMemoryCompanionHint, calls};
}

test("memory companion hint renders tailored cards for all states and persists permanent dismissal", async () => {
  const {hint, calls} = hintHarness({
    "GET page/memory-companion": {
      state: "missing",
      plugin_id: "astrbot_plugin_memory_companion",
      display_name: "我会牢牢记住你",
      repo_url: "https://github.com/menglimi/astrbot_plugin_memory_companion",
      hint_dismissed: false,
    },
    "POST page/hint-preferences": body => ({dismissedHints: {memoryCompanion: body.dismissed}}),
  });

  const status = await hint.load();
  assert.equal(status.state, "missing");
  assert.equal(hint.shouldShowBanner(status), true);

  const missingHtml = hint.renderCard(status, {compact: true, allowDismiss: true});
  assert.match(missingHtml, /当前仅启用轻量内置记忆/);
  assert.match(missingHtml, /我会牢牢记住你/);
  assert.match(missingHtml, /data-mc-hint-action="dismiss-permanent"/);
  assert.match(missingHtml, /data-mc-hint-action="open-guide"/);

  const disabledHtml = hint.renderCard({...status, state: "disabled"}, {allowDismiss: false});
  assert.match(disabledHtml, /已安装「我会牢牢记住你」，但尚未启用/);
  assert.match(disabledHtml, /查看启用步骤/);

  const bridgeHtml = hint.renderCard({...status, state: "bridge_disabled"}, {allowDismiss: false});
  assert.match(bridgeHtml, /协作桥接未开启/);

  const connectedHidden = hint.renderCard({...status, state: "connected"}, {allowDismiss: true});
  assert.equal(connectedHidden, "");

  const connectedVisible = hint.renderCard({...status, state: "connected"}, {showWhenConnected: true});
  assert.match(connectedVisible, /已接通/);

  await hint.setDismissed(true);
  assert.equal(calls.at(-1).route, "page/hint-preferences");
  assert.deepEqual(JSON.parse(JSON.stringify(calls.at(-1).body)), {key: "memoryCompanion", dismissed: true});
  assert.equal(hint.shouldShowBanner(), false);

  await hint.setDismissed(false);
  assert.equal(hint.shouldShowBanner(), true);
});

function setupHarness({status, responses = {}}) {
  const calls = [];
  const listeners = {};
  let html = "";
  let directoryRefreshes = 0;
  let currentStatus = status;
  const saveInlineBtn = {
    disabled: true,
    attrs: new Map(),
    setAttribute(k, v) { this.attrs.set(k, v); if (k === "disabled") this.disabled = true; },
    removeAttribute(k) { this.attrs.delete(k); if (k === "disabled") this.disabled = false; },
    hasAttribute(k) { return this.attrs.has(k); },
  };
  const syncInlineBtnFromHtml = markup => {
    const match = /<button[^>]*data-setup-action="save-inline-role"[^>]*>/.exec(markup || "");
    if (!match) return;
    const isDisabled = /\sdisabled(?:\s|=|>)/.test(match[0]);
    saveInlineBtn.disabled = isDisabled;
    if (isDisabled) saveInlineBtn.attrs.set("disabled", "");
    else saveInlineBtn.attrs.delete("disabled");
  };
  const node = {
    addEventListener: (name, fn) => { listeners[name] = fn; },
    removeEventListener: name => { delete listeners[name]; },
    querySelector: sel => sel === '[data-setup-action="save-inline-role"]' ? saveInlineBtn : null,
    querySelectorAll: () => [],
  };
  Object.defineProperty(node, "innerHTML", {get: () => html, set: v => { html = v; syncInlineBtnFromHtml(v); }});
  const product = {
    request(method, route, body) {
      calls.push({method, route, body});
      const key = `${method} ${route}`;
      let result = responses[key];
      if (Array.isArray(result)) result = result.length > 1 ? result.shift() : result[0];
      if (typeof result === "function") result = result(body);
      if (result !== undefined) return Promise.resolve(result);
      if (method === "GET" && route === "setup/status") return Promise.resolve(currentStatus);
      return Promise.resolve({});
    },
    targets: () => [],
    accounts: () => [],
    personas: () => [],
    async refreshDirectory() { directoryRefreshes += 1; },
    role: () => null,
    scopeKey: () => "",
    unwrap: value => value,
    relative: addr => addr.replace("/astrbot_plugin_private_companion/", ""),
  };
  const sandbox = {
    window: {CompanionProduct: product, AstrBotPluginPage: {}},
    document: {querySelector: () => null},
    Promise, Set, Map, String, Number, Object, Array, RegExp, Error, Date, JSON, Math,
    setTimeout, clearTimeout,
  };
  vm.runInNewContext(hintSource, sandbox);
  vm.runInNewContext(migrationReviewSource, sandbox);
  vm.runInNewContext(migrationTableSource, sandbox);
  vm.runInNewContext(setupSource, sandbox);
  return {
    calls,
    get html() { return html; },
    get directoryRefreshes() { return directoryRefreshes; },
    mount: () => sandbox.window.CompanionSetup.mount(node),
    unmount: () => sandbox.window.CompanionSetup.unmount(),
    clickAction(action) {
      listeners.click({target: {closest: sel => sel === "[data-setup-action]" ? {dataset: {setupAction: action}} : null}});
    },
    clickStep(step) {
      listeners.click({target: {closest: sel => sel === "[data-setup-goto-step]" ? {dataset: {setupGotoStep: step}} : null}});
    },
    clickSelectWindow(payload) {
      listeners.click({target: {closest: sel => sel === "[data-setup-select-window]"
        ? {dataset: {setupSelectWindow: JSON.stringify(payload)}} : null}});
    },
    change(name, value, dataset = {}) {
      listeners.change({target: {name, value, dataset}});
    },
    toggleRolePersona(personaId, checked = true) {
      listeners.change({target: {value: personaId, checked, dataset: {setupRole: "persona"}}});
    },
    inputRoleName(value) {
      listeners.input({target: {value, dataset: {setupRole: "name"}}});
    },
    get saveInlineBtn() { return saveInlineBtn; },
  };
}

test("setup model step never silently discards dirty model selection when prepared=true and supports probe + vision selector", async () => {
  const initial = {
    step: "model",
    binding_confirmed: true,
    prepared: true,
    ready: false,
    completed: [],
    skipped: [],
    model: {
      provider_id: "old-model",
      fallback_provider_id: "",
      effective_chat_model: {id: "chat-main", name: "AstrBot 主模型", model: "deepseek-v3"},
      providers: [
        {id: "old-model", name: "Old Model", model: "old-1", capabilities: ["text", "image"]},
        {id: "deepseek-text", name: "DeepSeek", model: "deepseek-chat", capabilities: ["text"]},
        {id: "gpt-vision", name: "GPT Vision", model: "gpt-4o", capabilities: ["text", "image"]},
      ],
    },
  };
  const updated = {...initial, model: {...initial.model, provider_id: "deepseek-text"}};
  const page = setupHarness({
    status: initial,
    responses: {
      "GET setup/status": [initial, updated],
      "GET setup/model-tasks": {providers: initial.model.providers, tasks: []},
      "POST setup/model/probe": {
        provider_id: "deepseek-text", ok: true, status: "ok", latency_ms: 215, model: "deepseek-chat",
      },
      "POST setup/model": {provider_id: "deepseek-text", prepared: true, restart_required: false, vision_applied: ["screen.interpretation"]},
    },
  });
  page.mount();
  await settle();

  assert.match(page.html, /日常对话主模型（只读）/);
  assert.match(page.html, /AstrBot 主模型/);
  // Initially clean while prepared -> complete-step is primary.
  assert.match(page.html, /data-setup-action="complete-step"/);

  // User changes provider to text-only model: vision warning appears + primary button switches to save-model!
  page.change("setup-provider", "deepseek-text");
  assert.match(page.html, /当前所选通用模型不支持图片理解/);
  assert.match(page.html, /name="setup-vision-provider"/);
  assert.match(page.html, /data-setup-action="save-model"[^>]*>保存并应用模型/);

  // Live connectivity probe.
  page.clickAction("probe-model");
  await settle();
  assert.match(page.html, /连通正常 · 耗时 215ms/);

  // Select vision model and save.
  page.change("setup-vision-provider", "gpt-vision");
  page.clickAction("save-model");
  await settle();
  const saved = page.calls.find(item => item.method === "POST" && item.route === "setup/model");
  assert.deepEqual(JSON.parse(JSON.stringify(saved.body)), {
    provider_id: "deepseek-text",
    vision_provider_id: "gpt-vision",
  });
  assert.ok(page.directoryRefreshes >= 1, "saving model must refresh page directory");
  page.unmount();
});

test("setup wizard supports clickable left rail, direct private window select, skip-test finish, and post-ready reopening", async () => {
  const ready = {
    step: "complete",
    binding_confirmed: true,
    prepared: true,
    ready: true,
    completed: ["model"],
    skipped: [],
    finished_at: "2026-11-24T12:00:00Z",
    host: {accepting: true},
    binding: {platform_id: "qq-main", account_id: "100", participant_id: "200", persona_id: "晚星"},
    model: {provider_id: "gpt-vision", providers: [{id: "gpt-vision", name: "GPT Vision", capabilities: ["text", "image"]}]},
    memory_companion: {state: "missing", plugin_id: "astrbot_plugin_memory_companion", display_name: "我会牢牢记住你"},
  };
  const page = setupHarness({
    status: ready,
    responses: {
      "GET setup/targets": {
        accounts: [{platform_id: "qq-main", account_id: "100", bot_id: "bot-1", account_name: "主 Bot"}],
        personas: [{platform_id: "qq-main", account_id: "100", bot_id: "bot-1", persona_id: "晚星", mapped: true}],
        targets: [{target_id: "p-1", scope_kind: "private", platform_id: "qq-main", account_id: "100",
          participant_id: "300", display_name: "小舟", roles: [{persona_ids: ["晚星"]}]}],
      },
      "POST setup/binding/select": {confirmed: true},
      "GET setup/health": {
        can_finish: true,
        checks: [
          {id: "account", label: "机器人与人格映射", status: "ok", summary: "已连接 1 个机器人账号", fix_step: "binding"},
          {id: "binding", label: "核心管理员私聊", status: "ok", summary: "已绑定", fix_step: "binding"},
          {id: "model", label: "插件通用与识图模型", status: "ok", summary: "已配置", fix_step: "model"},
          {id: "runtime", label: "陪伴运行实例", status: "ok", summary: "已启动", fix_step: "proactive_test"},
          {id: "memory", label: "长期记忆搭档", status: "warn", summary: "未安装「我会牢牢记住你」", fix_step: "knowledge"},
          {id: "proactive", label: "主动联系与作息", status: "ok", summary: "已就绪", fix_step: "proactive"},
        ],
      },
    },
  });
  page.mount();
  await settle();

  // Fix verified: 7 groups instead of hardcoded 6 groups, plus reopen button!
  assert.match(page.html, /整理了 7 组常用设置/);
  assert.doesNotMatch(page.html, /整理了 6 组常用设置/);
  assert.match(page.html, /data-setup-action="reopen-wizard"/);
  assert.match(page.html, /我会牢牢记住你/);

  // Reopen wizard cleanly.
  page.clickAction("reopen-wizard");
  await settle();
  assert.match(page.html, /data-setup-action="close-wizard"/);
  assert.match(page.html, /data-setup-goto-step="model"/);
  assert.match(page.html, /直接从已发现的私聊窗口选择/);

  // Direct window select works without QQ command.
  page.clickSelectWindow({platform_id: "qq-main", account_id: "100", participant_id: "300", persona_id: "晚星"});
  await settle();
  const selectCall = page.calls.find(item => item.method === "POST" && item.route === "setup/binding/select");
  assert.equal(selectCall.body.participant_id, "300");

  // Jump via clickable left rail to step 3 (knowledge -> shows memory companion card) and step 8 (health).
  page.clickStep("knowledge");
  await settle();
  assert.match(page.html, /我会牢牢记住你/);
  page.clickStep("proactive_test");
  await settle();
  assert.match(page.html, /data-setup-action="close-wizard"/);

  // Close wizard returns to post-ready hub.
  page.clickAction("close-wizard");
  assert.match(page.html, /开始使用 · 常用设置导览/);
  page.unmount();
});

test("inline role creator unlocks submit button immediately on typing role name after selecting persona", async () => {
  const page = setupHarness({
    status: {
      ready: false,
      step: "binding",
      completed: [],
      skipped: [],
      binding_confirmed: false,
      prepared: false,
      binding: {bound: false, confirmed: false, pending: false},
      model: {provider_id: "", fallback_provider_id: "", providers: []},
    },
    responses: {
      "GET setup/targets": {
        accounts: [{platform_id: "qq-main", account_id: "100", bot_id: "bot-100", display_name: "主号"}],
        personas: [{bot_id: "bot-100", persona_id: "p_wanxing", label: "晚星人格", mapped: false}],
        targets: [],
      },
      "POST accounts/role": {saved: true, role_id: "role-wanxing"},
    },
  });
  page.mount();
  await settle();

  page.clickAction("toggle-role-creator");
  assert.equal(page.saveInlineBtn.disabled, true);

  // Select persona first while name is still empty -> button stays disabled.
  page.toggleRolePersona("p_wanxing", true);
  assert.equal(page.saveInlineBtn.disabled, true);

  // Typing a name via input event unlocks the submit button in place without needing another checkbox click!
  page.inputRoleName("晚星");
  assert.equal(page.saveInlineBtn.disabled, false);
  assert.equal(page.saveInlineBtn.hasAttribute("disabled"), false);

  // Clearing name re-disables the button.
  page.inputRoleName("   ");
  assert.equal(page.saveInlineBtn.disabled, true);

  // Typing valid name again enables and submits POST accounts/role.
  page.inputRoleName("晚星");
  assert.equal(page.saveInlineBtn.disabled, false);
  page.clickAction("save-inline-role");
  await settle();
  const roleSave = page.calls.find(item => item.method === "POST" && item.route === "accounts/role");
  assert.ok(roleSave, "expected POST accounts/role call");
  assert.equal(roleSave.body.role_id, "晚星");
  assert.deepEqual(JSON.parse(JSON.stringify(roleSave.body.persona_ids)), ["p_wanxing"]);
  page.unmount();
});

test("appearance page exposes and toggles the memory companion hint restore switch", async () => {
  const calls = [];
  let dismissed = true;
  const handlers = {};
  const switches = {};
  const root = {
    innerHTML: "",
    addEventListener(type, fn) { handlers[type] = fn; },
    removeEventListener(type) { delete handlers[type]; },
    querySelector(sel) {
      if (sel === "[data-ap-hint='memoryCompanion']") return switches.mc || null;
      return null;
    },
    querySelectorAll: () => [],
  };
  const window = {
    CompanionTheme: {THEMES: [{id: "courtyard", label: "庭院", note: "清新", light: "day", mode: "light"}], label: () => "庭院"},
    CompanionDisplayPreferences: {largeText: () => false, setLargeText: () => false},
    CompanionProduct: {
      appearance: () => ({theme: "courtyard", density: "standard", radius: "standard", typeScale: "m", motion: "full"}),
      setAppearance: (_, v) => v,
      resetAppearance: () => ({theme: "courtyard", density: "standard", radius: "standard", typeScale: "m", motion: "full"}),
      followLight: () => false,
      setFollowLight: v => v,
    },
    CompanionMemoryCompanionHint: {
      current: () => ({state: "missing", hint_dismissed: dismissed}),
      load: async () => ({state: "missing", hint_dismissed: dismissed}),
      setDismissed: async flag => {
        calls.push(flag);
        dismissed = flag;
        return dismissed;
      },
    },
    matchMedia: () => ({matches: false}),
    setTimeout, clearTimeout,
  };
  const ctx = vm.createContext({window, matchMedia: window.matchMedia, setTimeout, clearTimeout, console});
  vm.runInContext(appearanceSource, ctx);

  ctx.window.CompanionAppearancePage.mount(root);
  await settle();
  assert.match(root.innerHTML, /提示与引导/);
  assert.match(root.innerHTML, /aria-checked="false" data-ap-hint="memoryCompanion"/);

  const attrs = {"data-ap-hint": "memoryCompanion", "aria-checked": "false"};
  const sw = {
    dataset: {apHint: "memoryCompanion"},
    classList: {toggle() {}},
    setAttribute(k, v) { attrs[k] = v; },
    getAttribute: k => attrs[k],
    closest: sel => sel === "[data-ap-hint]" ? sw : null,
  };
  switches.mc = sw;
  handlers.click({target: sw});
  await settle();
  assert.deepEqual(calls, [false]);
  assert.equal(attrs["aria-checked"], "true");
  ctx.window.CompanionAppearancePage.unmount();
});

test("host management shared model card distinguishes immediate hot apply from restart_required", async () => {
  assert.match(hostSource, /宿主运行中，保存后将即时热生效/);
  assert.match(hostSource, /陪伴通用模型（全局默认）已保存并即时生效/);
  assert.match(hostSource, /已保存，需重载插件后生效/);
  assert.doesNotMatch(hostSource, /宿主已在运行，保存后将在下次启动或重载时生效/);
});

test("setup wizard and memory companion dialog provide mobile capsule rail, auto-scroll, and narrow-screen stacking", async () => {
  // 1. Mobile capsule rail replaces display:none at <=900px, places content on row 3,
  //    and avoids unscoped `.setup-progress li` grid specificity collisions.
  assert.doesNotMatch(setupCssSource, /\.setup-progress\s*\{\s*display\s*:\s*none/);
  assert.doesNotMatch(setupCssSource, /\.setup-progress\s+li\s*\{/);
  assert.match(setupCssSource, /\.setup-progress\s+li\.setup-progress-step\s*\{[^}]*display:\s*block;[^}]*grid-template-columns:\s*none;/);
  assert.match(setupCssSource, /@media\s*\(max-width:\s*900px\)[\s\S]*\.setup-content\s*\{[^}]*grid-row\s*:\s*3/);
  assert.match(setupCssSource, /@media\s*\(max-width:\s*900px\)[\s\S]*\.setup-progress\s*\{[\s\S]*display:\s*flex;[\s\S]*overflow-x:\s*auto;/);
  assert.match(setupCssSource, /@media\s*\(max-width:\s*900px\)[\s\S]*\.setup-progress\s+li\.setup-progress-step\s*\{[\s\S]*flex:\s*0 0 auto;[\s\S]*display:\s*flex;/);
  assert.match(setupCssSource, /@media\s*\(max-width:\s*900px\)[\s\S]*\.setup-progress-btn\s*\{[\s\S]*flex:\s*0 0 auto;[\s\S]*border-radius:\s*var\(--r-pill\);[\s\S]*white-space:\s*nowrap;/);

  // 2. <=600px stacks model select row, wraps header, tightens stage padding, and keeps manual source disclosure static.
  assert.match(setupCssSource, /@media\s*\(max-width:\s*600px\)[\s\S]*\.setup-header\s*\{[^}]*flex-wrap:\s*wrap;/);
  assert.match(setupCssSource, /@media\s*\(max-width:\s*600px\)[\s\S]*\.setup-stage\s*\{[^}]*padding:\s*var\(--sp-3\);/);
  assert.match(setupCssSource, /@media\s*\(max-width:\s*600px\)[\s\S]*\.setup-model-select-row\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\);/);
  assert.match(setupCssSource, /@media\s*\(max-width:\s*600px\)[\s\S]*\.setup-migration-source-manual\[open\]\s*\{[\s\S]*position:\s*static;[\s\S]*width:\s*100%;/);

  // 3. Memory companion dialog uses dvh cap and compact mobile surface/actions.
  assert.match(hintCssSource, /max-height:\s*calc\(100dvh - 32px\)/);
  assert.match(hintCssSource, /@media\s*\(max-width:\s*640px\)[\s\S]*\.mc-dialog-surface\s*\{[\s\S]*padding:\s*var\(--sp-3\);/);
  assert.match(hintCssSource, /@media\s*\(max-width:\s*640px\)[\s\S]*\.mc-dialog-actions\s+\.button\s*\{[\s\S]*width:\s*100%;/);

  // 4. Header copy works on both desktop and mobile and switching steps auto-scrolls the active pill into view.
  const scrollCalls = [];
  const listeners = {};
  let html = "";
  const activePill = {offsetLeft: 420, offsetWidth: 140};
  const railEl = {
    scrollWidth: 920,
    clientWidth: 360,
    scrollLeft: 0,
    querySelector: sel => sel === '.setup-progress-btn[aria-current="step"]' ? activePill : null,
    scrollTo: opts => { scrollCalls.push(opts); },
  };
  const node = {
    addEventListener: (name, fn) => { listeners[name] = fn; },
    removeEventListener: name => { delete listeners[name]; },
    querySelector: sel => sel === ".setup-progress" ? railEl : null,
    querySelectorAll: () => [],
  };
  Object.defineProperty(node, "innerHTML", {get: () => html, set: v => { html = v; }});
  const status = {
    step: "model",
    binding_confirmed: true,
    prepared: true,
    ready: false,
    completed: ["binding"],
    skipped: [],
    binding: {bound: true, confirmed: true, platform_id: "qq", account_id: "100", participant_id: "200"},
    model: {provider_id: "p1", fallback_provider_id: "", providers: [{id: "p1", name: "P1", capabilities: ["text", "image"]}]},
    host: {state: "running", accepting: true},
  };
  const product = {
    request: (method, route) => Promise.resolve(method === "GET" && route === "setup/status" ? status : {}),
    targets: () => [],
    accounts: () => [],
    personas: () => [],
    refreshDirectory: async () => {},
    role: () => null,
    scopeKey: () => "",
    unwrap: v => v,
    relative: addr => addr.replace("/astrbot_plugin_private_companion/", ""),
  };
  const sandbox = {
    window: {CompanionProduct: product, AstrBotPluginPage: {}, matchMedia: () => ({matches: false})},
    document: {documentElement: {dataset: {motion: "full"}}, querySelector: () => null},
    Promise, Set, Map, String, Number, Object, Array, RegExp, Error, Date, JSON, Math,
    setTimeout, clearTimeout,
  };
  vm.runInNewContext(hintSource, sandbox);
  vm.runInNewContext(migrationReviewSource, sandbox);
  vm.runInNewContext(migrationTableSource, sandbox);
  vm.runInNewContext(setupSource, sandbox);

  sandbox.window.CompanionSetup.mount(node);
  await settle();
  assert.match(html, /点击步骤可随时来回切换/);
  assert.doesNotMatch(html, /点击左侧步骤可随时来回切换/);
  assert.ok(scrollCalls.length >= 1, "expected horizontal step rail to auto-scroll active pill");
  assert.equal(scrollCalls.at(-1).behavior, "smooth");
  assert.equal(scrollCalls.at(-1).left, 310);

  // Reduced motion switches scroll behavior to "auto".
  sandbox.document.documentElement.dataset.motion = "off";
  listeners.click({target: {closest: sel => sel === "[data-setup-goto-step]" ? {dataset: {setupGotoStep: "knowledge"}} : null}});
  await settle();
  assert.equal(scrollCalls.at(-1).behavior, "auto");
  sandbox.window.CompanionSetup.unmount();
});

test("probe-model updates button and result slot in place with zero full-page redraws and watchProactive dedupes identical polls", async () => {
  const listeners = {};
  let html = "";
  let innerHtmlWrites = 0;
  let scrollCalls = 0;
  let resolveProbe = null;
  const probeBtn = {
    disabled: false,
    textContent: "测一下",
    attrs: new Map(),
    setAttribute(k, v) { this.attrs.set(k, v); },
    removeAttribute(k) { this.attrs.delete(k); },
  };
  const probeSlot = {innerHTML: ""};
  const railEl = {
    scrollWidth: 920,
    clientWidth: 360,
    scrollLeft: 0,
    querySelector: sel => sel === '.setup-progress-btn[aria-current="step"]' ? {offsetLeft: 200, offsetWidth: 100} : null,
    scrollTo: () => { scrollCalls += 1; },
  };
  const node = {
    addEventListener: (name, fn) => { listeners[name] = fn; },
    removeEventListener: name => { delete listeners[name]; },
    querySelector(sel) {
      if (sel === '[data-setup-action="probe-model"]') return probeBtn;
      if (sel === "[data-setup-probe-slot]") return probeSlot;
      if (sel === ".setup-progress") return railEl;
      return null;
    },
    querySelectorAll: () => [],
  };
  Object.defineProperty(node, "innerHTML", {
    get: () => html,
    set: v => { html = v; innerHtmlWrites += 1; },
  });

  const status = {
    step: "model",
    binding_confirmed: true,
    prepared: true,
    ready: false,
    completed: ["binding"],
    skipped: [],
    binding: {bound: true, confirmed: true, platform_id: "qq", account_id: "100", participant_id: "200"},
    model: {provider_id: "p1", fallback_provider_id: "", providers: [{id: "p1", name: "P1", capabilities: ["text", "image"]}]},
    host: {state: "running", accepting: true},
  };
  const pollResponses = [
    {ready: false, proactive_test: {attempt_id: "att-1", state: "started", stages: [{stage: "model_route", state: "running"}]}},
    {ready: false, proactive_test: {attempt_id: "att-1", state: "started", stages: [{stage: "model_route", state: "running"}]}},
    {ready: false, proactive_test: {attempt_id: "att-1", state: "started", stages: [{stage: "model_route", state: "passed"}]}},
    {ready: false, proactive_test: {attempt_id: "att-1", state: "delivered", stages: [{stage: "delivery", state: "passed"}]}},
  ];
  const product = {
    request(method, route) {
      if (method === "GET" && route === "setup/status") return Promise.resolve(status);
      if (method === "POST" && route === "setup/model/probe") {
        return new Promise(resolve => { resolveProbe = resolve; });
      }
      if (method === "POST" && route === "setup/proactive-test") {
        return Promise.resolve({ready: false, state: "started", attempt_id: "att-1"});
      }
      if (method === "GET" && route === "setup/proactive-test/status") {
        return Promise.resolve(pollResponses.shift() || {ready: false, proactive_test: {attempt_id: "att-1", state: "delivered"}});
      }
      return Promise.resolve({});
    },
    targets: () => [],
    accounts: () => [],
    personas: () => [],
    refreshDirectory: async () => {},
    role: () => null,
    scopeKey: () => "",
    unwrap: v => v,
    relative: addr => addr.replace("/astrbot_plugin_private_companion/", ""),
  };
  const immediateTimeout = fn => { fn(); return 1; };
  const sandbox = {
    window: {CompanionProduct: product, AstrBotPluginPage: {}, matchMedia: () => ({matches: false})},
    document: {documentElement: {dataset: {motion: "full"}}, querySelector: () => null},
    Promise, Set, Map, String, Number, Object, Array, RegExp, Error, Date, JSON, Math,
    setTimeout: immediateTimeout, clearTimeout: () => {},
  };
  vm.runInNewContext(hintSource, sandbox);
  vm.runInNewContext(migrationReviewSource, sandbox);
  vm.runInNewContext(migrationTableSource, sandbox);
  vm.runInNewContext(setupSource, sandbox);

  sandbox.window.CompanionSetup.mount(node);
  await settle();
  const writesBeforeProbe = innerHtmlWrites;
  const scrollsBeforeProbe = scrollCalls;

  listeners.click({target: {closest: sel => sel === "[data-setup-action]" ? {dataset: {setupAction: "probe-model"}} : null}});
  assert.equal(probeBtn.disabled, true);
  assert.equal(probeBtn.textContent, "测试中…");
  assert.equal(innerHtmlWrites, writesBeforeProbe, "probe start must not rebuild full root.innerHTML");
  assert.equal(scrollCalls, scrollsBeforeProbe, "probe start must not re-scroll step rail");

  resolveProbe({provider_id: "p1", ok: true, status: "ok", latency_ms: 95, model: "gpt-4o"});
  await settle();
  assert.equal(probeBtn.disabled, false);
  assert.equal(probeBtn.textContent, "测一下");
  assert.match(probeSlot.innerHTML, /连通正常 · 耗时 95ms/);
  assert.equal(innerHtmlWrites, writesBeforeProbe, "probe completion must not rebuild full root.innerHTML");
  assert.equal(scrollCalls, scrollsBeforeProbe, "probe completion must not re-scroll step rail");

  // Trigger proactive-test and verify identical started polls do NOT cause extra redraws.
  status.step = "proactive_test";
  listeners.click({target: {closest: sel => sel === "[data-setup-goto-step]" ? {dataset: {setupGotoStep: "proactive_test"}} : null}});
  await settle();
  listeners.change({target: {checked: true, dataset: {setupProactiveOptIn: ""}}});
  const writesBeforeTest = innerHtmlWrites;
  listeners.click({target: {closest: sel => sel === "[data-setup-action]" ? {dataset: {setupAction: "proactive-test"}} : null}});
  await settle();
  // Breakdown: 1 (act busy) + 1 (started notice) + 1 (poll #1 first stage snapshot) + 0 (poll #2 identical!)
  //            + 1 (poll #3 stage passed) + 1 (poll #4 delivered terminal) + 1 (act finally) = 6 writes total
  // (Previously: poll #2 re-rendered AND poll #4 double-rendered -> >= 8 writes).
  assert.equal(innerHtmlWrites - writesBeforeTest, 6, "identical poll skipped and terminal poll renders once");
  sandbox.window.CompanionSetup.unmount();
});

test("memory companion dialog recheck preserves scrollTop and updates in place when state is unchanged", async () => {
  let stateToReturn = "missing";
  let dialogInnerHtmlWrites = 0;
  let dialogHtml = "";
  let dialogClick = null;
  const statusEl = {textContent: ""};
  const recheckBtn = {
    dataset: {mcDialogAction: "recheck"},
    disabled: false,
    textContent: "我已安装 / 启用，重新检测",
    closest: sel => sel === "[data-mc-dialog-action]" ? recheckBtn : null,
  };
  const dialogNode = {
    className: "",
    isConnected: true,
    open: false,
    scrollTop: 0,
    setAttribute() {},
    addEventListener(type, fn) { if (type === "click") dialogClick = fn; },
    showModal() { this.open = true; },
    close() { this.open = false; },
    querySelector: sel => sel === "[data-mc-dialog-status]" ? statusEl : null,
  };
  Object.defineProperty(dialogNode, "innerHTML", {
    get: () => dialogHtml,
    set: v => { dialogHtml = v; dialogInnerHtmlWrites += 1; },
  });
  const sandbox = {
    window: {
      CompanionProduct: {
        request: () => Promise.resolve({
          state: stateToReturn,
          plugin_id: "astrbot_plugin_memory_companion",
          display_name: "我会牢牢记住你",
          repo_url: "https://github.com/menglimi/astrbot_plugin_memory_companion",
          hint_dismissed: false,
        }),
      },
    },
    document: {
      createElement: () => dialogNode,
      body: {appendChild() {}},
    },
    Promise, Set, Map, String, Number, Object, Array, RegExp, Error, Date, JSON,
  };
  vm.runInNewContext(hintSource, sandbox);

  const hint = sandbox.window.CompanionMemoryCompanionHint;
  await hint.load();
  hint.openGuideDialog();
  assert.equal(dialogInnerHtmlWrites, 1);

  // Scroll down inside the dialog, then recheck when state is still "missing".
  dialogNode.scrollTop = 180;
  await dialogClick({target: recheckBtn});
  assert.equal(dialogInnerHtmlWrites, 1, "unchanged state recheck must not rewrite dialogNode.innerHTML");
  assert.equal(dialogNode.scrollTop, 180);
  assert.equal(recheckBtn.disabled, false);
  assert.equal(recheckBtn.textContent, "我已安装 / 启用，重新检测");
  assert.match(statusEl.textContent, /暂未检测到/);

  // Now simulate plugin becoming connected: innerHTML updates once and scrollTop is restored.
  stateToReturn = "connected";
  await dialogClick({target: recheckBtn});
  assert.equal(dialogInnerHtmlWrites, 2, "state transition updates dialog markup once");
  assert.equal(dialogNode.scrollTop, 180, "dialog scrollTop must be restored across state transition");
  assert.match(statusEl.textContent, /成功接通/);
});
