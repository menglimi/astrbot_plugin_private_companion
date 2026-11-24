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
  const node = {
    addEventListener: (name, fn) => { listeners[name] = fn; },
    removeEventListener: name => { delete listeners[name]; },
    querySelector: () => null,
    querySelectorAll: () => [],
  };
  Object.defineProperty(node, "innerHTML", {get: () => html, set: v => { html = v; }});
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
