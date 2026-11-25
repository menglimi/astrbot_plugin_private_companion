/* Embedded workspaces share a page, but never a response channel or scope. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const HOST = "astrbot_plugin_private_companion";
const shellHtml = fs.readFileSync(path.join(__dirname, "../pages/陪伴小屋/index.html"), "utf8");
const shellCss = fs.readFileSync(path.join(__dirname, "../pages/陪伴小屋/shell.css"), "utf8");
const sidebarSource = fs.readFileSync(path.join(__dirname, "../pages/陪伴小屋/home-sidebar.js"), "utf8");
const source = fs.readFileSync(path.join(__dirname, "../pages/陪伴小屋/product-page.js"), "utf8");
const designerMemorySource = fs.readFileSync(path.join(__dirname, "../pages/陪伴小屋/persona-designer-memory.js"), "utf8");
const relationshipApi = "host/targets/first/roles/role-first/relationships";
const groupMemoryApi = "host/targets/first/roles/role-first/group-memory";
const hostSource = fs.readFileSync(path.join(__dirname, "../pages/宿主管理/app.js"), "utf8");

async function saveGroupSelection(h, directory, groups, {statusFailure = false} = {}) {
  const notifications = [], notices = [];
  const account = {platform_id: "qq-main", bot_id: "bot-a", account_id: "100"};
  const personas = [{bot_id: "bot-a", persona_id: "小伴", role_id: "synthetic-role"}];
  const context = vm.createContext({
    configuration: {enabled: true, accounts: [account], personas, groups: []}, groups, personas,
    selectedGroupTargets: () => groups, activeRoleAssignments: () => personas,
    async api(method, endpoint, body) {
      assert.equal(method, "POST"); assert.equal(endpoint, "accounts");
      assert.deepEqual(JSON.parse(JSON.stringify(body.groups)), groups);
      h.setDirectory(directory);
      return {configuration: body};
    },
    async readStatus() { if (statusFailure) throw new Error("status unavailable"); },
    notice: message => notices.push(message), publicError: error => error.message,
    setBusy() {}, renderGroupTargets() {}, updateGroupTargetControls() {},
    window: {parent: {postMessage(message) {
      notifications.push(h.events.message({source: h.host.contentWindow, data: message}));
    }}},
  });
  const section = (start, end) => hostSource.slice(hostSource.indexOf(start), hostSource.indexOf(end));
  vm.runInContext(`let bindingConfig = configuration, bindingsDirty = false,
    groupTargetsDirty = true, groupTargetDraft = groups, acquaintanceDirectory = {};
    const accountApi = "accounts";
    function renderBindingEditor(value) { bindingConfig = value; }
    ${section("  const groupRouteKey =", "  function groupTargetRows(")}
    ${section("  const sameGroupTargets =", "  function markGroupTargetsDirty(")}
    ${section("  async function saveGroupTargets(", "  function updateBindingCheck(")}
    globalThis.save = saveGroupTargets;`, context);
  await context.save();
  await Promise.all(notifications);
  return {notifications, notices};
}

async function harness(savedTheme = null, extensions = [], options = {}) {
  const events = {}, calls = [], frames = [], timers = [], warnings = [], loadedScripts = [], fetchCalls = [];
  const contextHandlers = new Set();
  let pending = null;
  let nextDirectory = null;
  const privateRole = {role_id: "synthetic-role", role_name: "小伴", persona_ids: ["小伴"],
    state_api: "/" + HOST + "/targets/first/roles/synthetic-role/state",
    life_api: "/" + HOST + "/targets/first/roles/synthetic-role/life",
    growth_api: "/" + HOST + "/targets/first/roles/synthetic-role/growth",
    memory_api: "/" + HOST + "/targets/first/roles/synthetic-role/memory",
    persona_api: "/" + HOST + "/targets/first/roles/synthetic-role/persona",
    model_api: "/" + HOST + "/targets/first/roles/synthetic-role/model",
    config_api: "/" + HOST + "/targets/first/roles/synthetic-role/config",
    relationships_api: options.groupMode ? null : "/" + HOST + "/" + relationshipApi,
    group_memory_api: null,
    reply_api: "/" + HOST + "/targets/first/roles/synthetic-role/reply"};
  const privateTarget = {target_id: "first", scope_kind: "private", participant_id: "200",
    platform_id: "qq-main", platform_name: "aiocqhttp", bot_id: "bot-a", account_id: "100", display_name: "朋友备注",
    roles: [privateRole]};
  const groupRole = {...privateRole, state_api: null, life_api: null, growth_api: null, memory_api: null,
    relationships_api: null, reply_api: "/" + HOST + "/targets/group/roles/synthetic-role/reply",
    model_api: "/" + HOST + "/targets/group/roles/synthetic-role/model",
    role_config_api: "/" + HOST + "/targets/group/roles/synthetic-role/config",
    group_memory_api: "/" + HOST + "/targets/group/roles/synthetic-role/group-memory"};
  const groupTarget = {target_id: "group", scope_kind: "group", group_id: "300", platform_id: "qq-main", platform_name: "aiocqhttp",
    bot_id: "bot-a", account_id: "100", display_name: "读书群", roles: [groupRole]};
  const catalog = {
    installation_id: "synthetic", operator_id: "dashboard:synthetic-admin", operator_name: "本地管理员",
    acquaintance_api: "/" + HOST + "/acquaintance",
    accounts_api: "/" + HOST + "/accounts",
    targets: options.pairedMode ? [privateTarget, groupTarget]
      : [{...(options.groupMode ? groupTarget : privateTarget), target_id: "first",
        roles: [options.groupMode ? {...groupRole,
          group_memory_api: "/" + HOST + "/" + groupMemoryApi,
          reply_api: "/" + HOST + "/targets/first/roles/synthetic-role/reply"} : privateRole]}],
  };
  if (options.wildcardFirstTarget) {
    catalog.targets.unshift({...privateTarget, target_id: "wildcard", participant_id: "*",
      display_name: "", roles: [privateRole]});
  }
  if (options.multipleRoles) {
    const extra = {...privateRole, role_id: "another-role", role_name: "夜航",
      persona_ids: ["夜航"], state_api: "/" + HOST + "/targets/first/roles/another-role/state"};
    catalog.targets[0].roles.push(extra);
  }
  if (options.mutateCatalog) options.mutateCatalog(catalog);
  const storage = options.storage || new Map();
  const cookieStorage = options.cookieStorage || new Map();
  const cookieKey = JSON.stringify([catalog.installation_id, catalog.operator_id]);
  const readBrowserPreferences = () => JSON.parse(cookieStorage.get(cookieKey) || '{"selection":null,"room_card_position":null}');
  if (options.savedSelection) cookieStorage.set(cookieKey, JSON.stringify({selection:options.savedSelection,room_card_position:null}));
  const scopeEvents = [];
  const navigations = [];
  let largeText = false;
  const window = {
    location: {href: "http://localhost/api/plugin/page/content/astrbot_plugin_private_companion/cottage/"},
    addEventListener(type, handler) { events[type] = handler; },
    CustomEvent: class { constructor(type, init) { this.type=type; this.detail=init?.detail; } },
    dispatchEvent(event) { scopeEvents.push(event); },
    navigate: (...route) => navigations.push(route),
    matchMedia: () => ({matches: false}),
    CompanionWorkspaceBundle: options.lazyBundles ? undefined
      : {host: "<p>host</p>", identity: "<p>directory</p>", relationships: "<p>relationship</p>", persona: "<p>persona</p>"},
    CompanionDisplayPreferences: {
      largeText: () => largeText,
      setLargeText(value) { largeText = Boolean(value); window.CompanionProduct?.displayPreferencesChanged?.(); },
    },
    AstrBotPluginPage: {
      ready: async () => ({pluginName: options.pluginName || HOST}),
      onContext(handler) { contextHandlers.add(handler); return () => contextHandlers.delete(handler); },
      apiGet: async (endpoint, params, requestOptions) => {
        calls.push({method: "GET", endpoint, params, requestOptions});
        if (options.getFailure && endpoint === options.getFailureEndpoint) throw options.getFailure;
        if (pending && endpoint === pending.endpoint) {
          const request = pending; pending = null; return request.promise;
        }
        if (options.summaryErrors?.[endpoint]) {
          return {status: "error", data: {code: options.summaryErrors[endpoint]}};
        }
        if (endpoint === "page/display-preferences") return options.displayPreferences || {preferences: null};
        if (endpoint === "page/browser-preferences") return {preferences:readBrowserPreferences()};
        if (endpoint === "page/workspace-entry") return options.workspaceEntry || {content_path:
          "/api/plugin/page/content/astrbot_plugin_private_companion/%E9%99%AA%E4%BC%B4%E5%B0%8F%E5%B1%8B/?asset_token=fresh"};
        if (endpoint === "page/targets") return options.directoryUnavailable
          ? {status: "error", data: {code: options.directoryError || "staging_not_initialized"}} : nextDirectory || catalog;
        if (endpoint === "integration/manifest") return {extensions, providers:[], summary: {connected_count: 0}};
        return {items: []};
      },
      apiPost: async (endpoint, body) => {
        calls.push({method: "POST", endpoint, body});
        if (endpoint === "page/browser-preferences") {
          if (options.browserPreferencesPost) await options.browserPreferencesPost(body);
          if (options.cookiesBlocked !== true) cookieStorage.set(cookieKey, JSON.stringify({...readBrowserPreferences(),...body}));
          return {preferences:readBrowserPreferences()};
        }
        if (options.postResponder) return options.postResponder(endpoint, body);
        if (options.postFailure) throw options.postFailure;
        return {status: "ok", data: {preferences: body}};
      },
    },
  };
  const stageRole = {innerHTML: ""};
  const document = {
    readyState: "interactive", documentElement: {dataset: {}},
    head: {appendChild(script) {
      loadedScripts.push(script);
      if (options.lazyBundles) {
        if (options.failWorkspaceLoads > 0) {
          options.failWorkspaceLoads -= 1;
          script.onerror?.();
          return;
        }
        const name = script.src.match(/workspace-bundles\/([^/.]+)\.js/)?.[1];
        window.CompanionWorkspaceBundle ||= {};
        window.CompanionWorkspaceBundle[name] = `<p>${name} lazy</p>`;
        script.onload?.();
      }
    }}, body: {classList: {add() {}, remove() {}, contains() { return false; }}},
    querySelector: selector => {
      const name = selector.match(/workspace-bundle="([a-z]+)"/)?.[1];
      return options.lazyBundles && name ? {content: {querySelector: () =>
        ({src: options.workspaceAssetUrl || `http://localhost/workspace-bundles/${name}.js?v=test`})}} : null;
    },
    getElementById: id => id === "stageRole" ? stageRole : null,
    querySelectorAll: selector => selector === ".product-workspace-frame" ? frames : [],
    createElement: () => ({addEventListener() {}, setAttribute() {}}), addEventListener() {},
  };
  vm.runInNewContext(sidebarSource, {window, document});
  const pageSetTimeout = options.captureTimers
    ? (callback, ms) => { const timer = {callback, ms, cleared: false}; timers.push(timer); return timer; }
    : setTimeout;
  const pageClearTimeout = options.captureTimers
    ? timer => { if (timer) timer.cleared = true; }
    : clearTimeout;
  const pageConsole = options.captureWarnings ? {warn: (...args) => warnings.push(args)} : console;
  const pageFetch = options.fetch || (async (...args) => {fetchCalls.push(args); return {ok: true, json: async () => ({})};});
  if (options.designerMemory) vm.runInNewContext(designerMemorySource, {window});
  vm.runInNewContext(source, {window, document, URL, navigator: {language: "zh-CN"},
    setTimeout: pageSetTimeout, clearTimeout: pageClearTimeout, console: pageConsole,
    fetch: pageFetch,
    matchMedia: () => ({matches: false}),
    localStorage: {getItem(key) {
      if (options.storageUnavailable) throw new Error("SecurityError");
      return key === "companion-v2-theme" ? savedTheme : storage.get(key) ?? null;
    }, setItem(key, value) {
      if (options.storageUnavailable) throw new Error("SecurityError");
      storage.set(key, value);
    }, removeItem(key) { storage.delete(key); }}});
  const product = window.CompanionProduct;
  const updates = [];
  await product.connect(reason => updates.push(reason));
  // Workspace fixtures explicitly open their initial window; the cottage itself starts without one.
  if (!options.unselectedWindow && !options.directoryUnavailable)
    await product.choose("first", product.selection().role_id, true);
  calls.length = 0;
  function addFrame(kind) {
    const messages = [];
    const workspaceStatus = {textContent: ""};
    const workspaceRetry = {hidden: true};
    const section = {dataset: {workspace: kind, workspaceTab: ""}, setAttribute() {},
      querySelector(selector) {
        return selector === "[data-workspace-status]" ? workspaceStatus
          : selector === "[data-workspace-load-retry]" ? workspaceRetry : null;
      }, workspaceStatus, workspaceRetry};
    const owner = {isConnected: true, style: {}, srcdoc: "",
      parentElement: section,
      contentWindow: {postMessage: message => messages.push(message)}, messages};
    frames.push(owner);
    return owner;
  }
  const identity = addFrame("identity"), relationships = addFrame("relationships"), host = addFrame("host"), persona = addFrame("persona");
  function request(owner, method, endpoint, requestId = "request-1", timeoutMs, body = {}) {
    return events.message({source: owner.contentWindow, data: {channel: "companion-workspace",
      kind: "request", requestId, method, path: endpoint, body, timeoutMs}});
  }
  return {product, storage, cookieStorage, cookieKey, scopeEvents, calls, timers, warnings, loadedScripts, fetchCalls, frames, addFrame, identity, relationships, host, persona, events, request, document, window, stageRole, navigations, updates, catalog,
    largeText: () => largeText,
    setDirectory(directory) {nextDirectory = directory;},
    hostTheme(isDark) {
      document.documentElement.dataset.theme = isDark ? "dark" : "light";
      for (const handler of contextHandlers) handler({pluginName: HOST, isDark});
    },
    contextHandlers,
    defer(endpoint = relationshipApi) {
      let resolve;
      pending = {endpoint, promise: new Promise(done => { resolve = done; })};
      return resolve;
    }};
}

test("designer is installation scoped and available before companion setup; relays only its allowlist", async () => {
  const h = await harness(null, [], {directoryUnavailable: true, designerMemory: true, lazyBundles: true});
  assert.match(h.product.workspace("designer"), /product-workspace-frame/);
  const designer = h.addFrame("designer"); h.product.mountWorkspace();
  await h.request(designer, "GET", "designer/options");
  assert.equal(h.calls.at(-1).endpoint, "persona-designer/options");
  const before = h.calls.length;
  await h.request(designer, "POST", "persona/command");
  assert.equal(designer.messages.at(-1).ok, false);
  await h.request(h.persona, "GET", "designer/client-state");
  assert.equal(h.persona.messages.at(-1).ok, false);
  assert.equal(h.calls.length, before);
});

test("designer state and late replies survive remount while leaving the extension clears memory", async () => {
  let resolve;
  const h = await harness(null, [], {designerMemory: true, lazyBundles: true,
    postResponder: endpoint => endpoint === "persona-designer/turn"
      ? new Promise(done => { resolve = done; }) : {status: "ok", data: {}}});
  const designer = h.addFrame("designer"); h.product.mountWorkspace();
  const snapshot = {version: 1, activeMode: "create", sessions: {create: {input: "still typing"}}};
  await h.request(designer, "POST", "designer/client-state", "cache", undefined, snapshot);
  const turn = h.request(designer, "POST", "designer/turn", "turn", 135000, {request_id: "a".repeat(32)});
  await new Promise(done => setImmediate(done));
  designer.isConnected = false; h.frames.splice(h.frames.indexOf(designer), 1);
  const replacement = h.addFrame("designer"); h.product.mountWorkspace();
  resolve({reply: "late result"}); await turn;
  await h.request(replacement, "GET", "designer/client-state", "restored");
  assert.equal(replacement.messages.at(-1).data.state.sessions.create.input, "still typing");
  await h.request(replacement, "GET", "designer/client-result", "receipt", undefined, {id: "turn:" + "a".repeat(32)});
  assert.equal(replacement.messages.at(-1).data.data.reply, "late result");
  assert.equal(h.calls.filter(call => call.endpoint === "persona-designer/turn").length, 1);
  assert.equal(h.calls.filter(call => call.endpoint === "persona-designer/drafts").length, 0);
  h.events.pagehide(); h.events.pageshow();
  await h.request(replacement, "GET", "designer/client-state", "fresh");
  assert.equal(replacement.messages.at(-1).data.state, null);
});

test("persona pickers keep in-frame listboxes without blocking parent wheel scroll chaining", () => {
  const designerMarkup = fs.readFileSync(path.join(__dirname, "../pages/陪伴小屋/workspaces/designer/index.html"), "utf8");
  const designerCss = fs.readFileSync(path.join(__dirname, "../pages/陪伴小屋/workspaces/designer/style.css"), "utf8");
  const personaMarkup = fs.readFileSync(path.join(__dirname, "../pages/陪伴小屋/workspaces/persona/index.html"), "utf8");
  const personaCss = fs.readFileSync(path.join(__dirname, "../pages/陪伴小屋/workspaces/persona/style.css"), "utf8");
  const profileDraft = fs.readFileSync(path.join(__dirname, "../pages/陪伴小屋/role-profile-draft.js"), "utf8");
  const selfCss = fs.readFileSync(path.join(__dirname, "../pages/陪伴小屋/self-page.css"), "utf8");
  assert.match(designerMarkup, /id="persona-select" size="8"/);
  assert.match(personaMarkup, /id="source-persona" size="6"/);
  assert.match(profileDraft, /data-role-profile-draft-persona size="6"/);
  assert.match(designerCss, /dialog\{[^}]*overflow:auto[^}]*overscroll-behavior:contain/);
  assert.doesNotMatch(designerCss, /html\s*,\s*body\{[^}]*overscroll-behavior/);
  assert.doesNotMatch(personaCss, /html\s*,\s*body\{[^}]*overscroll-behavior/);
  assert.doesNotMatch(designerCss, /#persona-select\{[^}]*overscroll-behavior/);
  assert.doesNotMatch(personaCss, /#source-persona\{[^}]*overscroll-behavior/);
  assert.match(selfCss, /\.role-profile-draft-persona select\{[^}]*overflow-y:auto[^}]*overscroll-behavior:contain/);
});

test("workspace iframe resize relaxes the 2400px cap and shrinks cleanly when content shortens", async () => {
  const h = await harness();
  h.product.mountWorkspace();
  await h.events.message({source: h.persona.contentWindow,
    data: {channel: "companion-workspace", kind: "resize", height: 3420}});
  assert.equal(h.persona.style.height, "3436px");
  await h.events.message({source: h.persona.contentWindow,
    data: {channel: "companion-workspace", kind: "resize", height: 520}});
  assert.equal(h.persona.style.height, "536px");
  await h.events.message({source: h.persona.contentWindow,
    data: {channel: "companion-workspace", kind: "resize", height: 180}});
  assert.equal(h.persona.style.height, "420px");
  await h.events.message({source: h.persona.contentWindow,
    data: {channel: "companion-workspace", kind: "resize", height: 99999}});
  assert.equal(h.persona.style.height, "16000px");
});

test("optional designer UI failure leaves the core account workspace usable", async () => {
  const h = await harness(null, [], {lazyBundles: true});
  const designer = h.addFrame("designer"); h.product.mountWorkspace();
  await h.request(designer, "GET", "designer/options");
  assert.equal(designer.messages.at(-1).code, "persona_designer_unavailable");
  await h.request(h.host, "GET", "host/status");
  assert.equal(h.host.messages.at(-1).ok, true);
  assert.equal(h.calls.at(-1).endpoint, "host/status");
});

test("directory and relationship workspaces both mount and receive appearance updates", async () => {
  const h = await harness();
  assert.match(h.product.workspace("identity"), /product-workspace-frame/,
    "the installation directory does not require an identity_api on a role");
  h.product.mountWorkspace();
  assert.equal(h.identity.srcdoc, "<p>directory</p>");
  assert.equal(h.relationships.srcdoc, "<p>relationship</p>");
  h.product.setAppearance("motion", "off");
  assert.equal(h.identity.messages.at(-1).name, "motion");
  assert.equal(h.relationships.messages.at(-1).name, "motion");
});

test("workspace bundles load only when that workspace is mounted", async () => {
  const h = await harness(null, [], {lazyBundles: true});
  h.frames.splice(0);
  const host = h.addFrame("host");
  assert.match(h.product.workspace("host"), /data-workspace-loading|product-workspace-loading/);
  assert.equal(h.loadedScripts.length, 0);
  h.product.mountWorkspace();
  assert.equal(h.loadedScripts.length, 1);
  assert.match(h.loadedScripts[0].src, /workspace-bundles\/host\.js/);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(host.srcdoc, "<p>host lazy</p>");
  assert.equal(h.loadedScripts.length, 1, "unvisited workspace bundles stay unloaded");
  await h.events.message({source: host.contentWindow, data: {channel: "companion-workspace", kind: "ready"}});
});

test("a rejected lazy bundle token refreshes once through AstrBot's authenticated page bridge", async () => {
  const h = await harness(null, [], {
    lazyBundles: true,
    failWorkspaceLoads: 1,
    workspaceAssetUrl: "http://localhost/workspace-bundles/host.js?v=test&asset_token=stale",
  });
  h.frames.splice(0);
  const host = h.addFrame("host");
  h.product.mountWorkspace();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.loadedScripts.length, 2);
  assert.equal(new URL(h.loadedScripts[0].src).searchParams.get("asset_token"), "stale");
  assert.equal(new URL(h.loadedScripts[1].src).searchParams.get("asset_token"), "fresh");
  assert.equal(h.calls.filter(call => call.endpoint === "page/workspace-entry").length, 1);
  assert.equal(h.fetchCalls.length, 0, "opaque-origin pages must not fetch Dashboard endpoints directly");
  assert.equal(host.srcdoc, "<p>host lazy</p>");
});

test("a denied workspace token refresh explains that the dashboard session must be renewed", async () => {
  const h = await harness(null, [], {lazyBundles: true, failWorkspaceLoads: 1,
    workspaceAssetUrl: "http://localhost/workspace-bundles/host.js?v=test&asset_token=stale",
    getFailureEndpoint: "page/workspace-entry",
    getFailure: Object.assign(new Error("dashboard_identity_required"), {code: "dashboard_identity_required"})});
  h.frames.splice(0);
  const host = h.addFrame("host");
  h.product.mountWorkspace();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(host.parentElement.dataset.loadState, "error");
  assert.match(host.parentElement.workspaceStatus.textContent, /重新登录控制台/);
  assert.doesNotMatch(host.parentElement.workspaceStatus.textContent, /asset_token|stale/);
});

test("a failed bundle without an asset token explains the missing authorization", async () => {
  const h = await harness(null, [], {lazyBundles: true, failWorkspaceLoads: 1});
  h.frames.splice(0);
  const host = h.addFrame("host");
  h.product.mountWorkspace();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(host.parentElement.dataset.loadState, "error");
  assert.match(host.parentElement.workspaceStatus.textContent, /缺少访问授权/);
  assert.equal(h.calls.some(call => call.endpoint === "page/workspace-entry"), false);
});

test("a bundle that still fails after token refresh identifies the remaining resource failure", async () => {
  const h = await harness(null, [], {lazyBundles: true, failWorkspaceLoads: 2,
    workspaceAssetUrl: "http://localhost/workspace-bundles/host.js?v=test&asset_token=stale",
    workspaceEntry: {content_path:
      "/api/plugin/page/content/astrbot_plugin_private_companion/%E9%99%AA%E4%BC%B4%E5%B0%8F%E5%B1%8B/?asset_token=fresh"}});
  h.frames.splice(0);
  const host = h.addFrame("host");
  h.product.mountWorkspace();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(host.parentElement.dataset.loadState, "error");
  assert.match(host.parentElement.workspaceStatus.textContent, /授权已刷新/);
  assert.match(host.parentElement.workspaceStatus.textContent, /插件资源加载日志/);
  assert.doesNotMatch(host.parentElement.workspaceStatus.textContent, /asset_token|fresh/);
});

test("a late home summary cannot replace a newer refresh in the same role", async () => {
  const h = await harness();
  const role = h.product.role();
  const endpoint = h.product.relative(role.state_api);
  const finishOld = h.defer(endpoint);
  const oldRefresh = h.product.refreshSummary();
  await new Promise(resolve => setImmediate(resolve));
  const latestRefresh = h.product.refreshSummary();
  assert.equal(await latestRefresh, true);
  finishOld({state: {values: {location: "过期的状态"}}});
  assert.equal(await oldRefresh, false);
  assert.equal(h.product.data.state?.state?.values?.location, undefined);
  assert.equal(h.updates.filter(reason => reason?.kind === "state-refresh").length, 1);
});

test("persona questionnaire reads the selected source and relays standardization only to its role API", async () => {
  const h = await harness();
  h.product.mountWorkspace();
  await h.request(h.persona, "GET", "persona", "persona-read");
  await h.request(h.persona, "POST", "persona/standardize", "persona-standardize");
  assert.equal(h.calls.at(-2).endpoint, "targets/first/roles/synthetic-role/persona");
  assert.equal(h.calls.at(-1).endpoint, "targets/first/roles/synthetic-role/persona/standardize");
  assert.equal(h.persona.messages.at(-1).ok, true);
});

test("persona questionnaire relays all model steps only within its selected role", async () => {
  const h = await harness();
  h.product.mountWorkspace();
  for (const operation of ["standardize", "tryouts", "style-rules"]) {
    await h.request(h.persona, "POST", "persona/" + operation, "questionnaire-" + operation, 180000);
    assert.equal(h.persona.messages.at(-1).ok, true, operation);
    assert.equal(h.calls.at(-1).endpoint, "targets/first/roles/synthetic-role/persona/" + operation);
  }
  const before = h.calls.length;
  for (const [frame, method, path] of [
    [h.persona, "GET", "persona/tryouts"],
    [h.persona, "POST", "persona/apply"],
    [h.persona, "POST", "persona/style-rules/extra"],
    [h.relationships, "POST", "persona/tryouts"],
  ]) {
    await h.request(frame, method, path);
    assert.equal(frame.messages.at(-1).ok, false, path);
  }
  assert.equal(h.calls.length, before);
});

test("a workspace request resolves the role API from the current directory", async () => {
  const h = await harness();
  h.product.mountWorkspace();
  const role = h.catalog.targets[0].roles[0];
  h.setDirectory({...h.catalog, targets: [{...h.catalog.targets[0],
    roles: [{...role, persona_api: "/" + HOST + "/targets/first/roles/synthetic-role/persona-v2"}]}]});
  await h.product.connect(() => {});
  await h.request(h.persona, "GET", "persona");
  assert.equal(h.persona.messages.at(-1).ok, true, "目录过期不该把请求判成 invalid_response");
  assert.equal(h.calls.at(-1).endpoint, "targets/first/roles/synthetic-role/persona-v2");
  assert.equal(h.calls.filter(call => call.endpoint === "persona").length, 0,
    "旧的角色接口地址不能再被使用");
});

test("a missing role API reads the directory once and then says what to fix", async () => {
  const h = await harness();
  h.product.mountWorkspace();
  const role = h.catalog.targets[0].roles[0];
  h.setDirectory({...h.catalog, targets: [{...h.catalog.targets[0],
    roles: [{...role, persona_api: null}]}]});
  await h.product.connect(() => {});
  const readTargets = () => h.calls.filter(call => call.endpoint === "page/targets").length;
  const before = readTargets();
  await h.request(h.persona, "GET", "persona", "persona-missing");
  assert.equal(readTargets(), before + 1, "缺失时重读一次角色目录");
  const response = h.persona.messages.at(-1);
  assert.equal(response.ok, false);
  assert.equal(response.code, "workspace_api_missing");
  assert.match(response.error, /重新保存角色映射/);

  await h.request(h.persona, "GET", "persona", "persona-missing-again");
  assert.equal(readTargets(), before + 1, "冷却期内不重复拉目录");
  assert.equal(h.persona.messages.at(-1).code, "workspace_api_missing");
});

test("server rejection envelopes stay confirmed through the product and workspace bridges", async () => {
  const direct = await harness(null, [], {summaryErrors: {"host/status": "core_access_required"}});
  await assert.rejects(direct.product.request("GET", "host/status"), error =>
    error.code === "core_access_required" && error.confirmed === true);

  const hosted = await harness(null, [], {
    getFailureEndpoint: "targets/first/roles/synthetic-role/persona",
    getFailure: Object.assign(new Error("denied"), {code: "core_access_required", confirmed: true}),
  });
  hosted.product.mountWorkspace();
  await hosted.request(hosted.persona, "GET", "persona", "persona-denied");
  const response = hosted.persona.messages.at(-1);
  assert.equal(response.ok, false);
  assert.equal(response.code, "core_access_required");
  assert.equal(response.confirmed, true);
});

test("a bridge-only machine code is recovered and labeled with its workspace source", async () => {
  /* The live Dashboard page bridge rejects with `new Error(message)` only:
     a status=error envelope loses `data.code` before a workspace sees it. The
     broker must recover the snake_case code and attach the source label so the
     embedded page can explain which source failed. */
  const h = await harness(null, [], {
    getFailureEndpoint: "targets/first/roles/synthetic-role/persona",
    getFailure: new Error("expression_group_context_required"),
  });
  h.product.mountWorkspace();
  await h.request(h.persona, "GET", "persona", "persona-machine-code");
  const response = h.persona.messages.find(message => message.requestId === "persona-machine-code");
  assert.equal(response.ok, false);
  assert.equal(response.code, "expression_group_context_required");
  assert.match(response.sourceLabel, /私聊/);
  assert.match(response.sourceLabel, /小伴/);
  assert.equal(response.reason, "expression_group_context_required");
});

test("request timeouts are configurable and timeout diagnostics omit target and query identifiers", async () => {
  const h = await harness(null, [], {captureTimers: true, captureWarnings: true});
  h.timers.length = 0;

  await h.product.request("GET", "host/status");
  assert.equal(h.timers.at(-1).ms, 15000, "ordinary requests retain the default timeout");

  h.timers.length = 0;
  const pending = h.product.request("GET",
    "host/targets/secret-target/roles/secret-role/state?participant_id=secret-user", {}, {timeoutMs: 4700});
  assert.equal(h.timers.at(-1).ms, 4700, "the caller's timeout is applied to the bridge request");
  h.timers.at(-1).callback();
  await assert.rejects(pending, error => error.code === "bridge_timeout");

  assert.equal(h.warnings.length, 1);
  assert.equal(h.warnings[0][1].endpoint, "host/targets/:target/roles/:role/state");
  assert.equal(h.warnings[0][1].timeout_ms, 4700);
  assert.doesNotMatch(JSON.stringify(h.warnings[0]), /secret-target|secret-role|secret-user/);

  h.timers.length = 0;
  await h.product.request("GET", "host/status", {}, {timeoutMs: 900000});
  assert.equal(h.timers.at(-1).ms, 300000, "custom limits cannot exceed the five-minute cap");
});

test("workspace request timeout reaches the host request boundary", async () => {
  const h = await harness(null, [], {captureTimers: true});
  h.product.mountWorkspace();
  h.timers.length = 0;
  const endpoint = "targets/first/roles/synthetic-role/persona";
  const finish = h.defer(endpoint);
  const pending = h.request(h.persona, "GET", "persona", "persona-long-plan", 300000);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.timers.at(-1).ms, 300000, "workspace plans receive the caller's five-minute deadline");
  assert.equal(h.calls.at(-1).endpoint, endpoint);
  finish({items: []});
  await pending;
});

test("workspace transport failures log only the sanitized route and timing", async () => {
  const h = await harness(null, [], {captureWarnings: true,
    postFailure: Object.assign(new Error("timed out"), {code: "bridge_timeout"})});
  h.product.mountWorkspace();
  await h.request(h.identity, "POST", "acquaintance/settings", "settings-timeout", 60000);
  await new Promise(resolve => setImmediate(resolve));
  const response = h.identity.messages.find(message => message.requestId === "settings-timeout");
  assert.equal(response?.code, "bridge_timeout");
  const warning = h.warnings.find(([label]) => label === "[CompanionV2] Workspace request failed");
  assert.ok(warning);
  assert.equal(warning[1].endpoint, "acquaintance/settings");
  assert.equal(warning[1].timeout_ms, 60000);
  assert.doesNotMatch(JSON.stringify(warning), /subject|participant|operation_key/);
});

test("target labels prefer resolved names and retain native identifiers", async () => {
  const h = await harness();
  assert.equal(h.product.targetLabel({scope_kind: "private", participant_id: "200", display_name: "朋友备注"}),
    "私聊 朋友备注（200）");
  assert.equal(h.product.targetLabel({scope_kind: "group", group_id: "300", display_name: "读书群"}), "群聊 读书群（300）");
  assert.equal(h.product.targetLabel({scope_kind: "group", group_id: "300", display_name: ""}), "群聊 300");
  assert.equal(h.product.accountLabel({account_id: "100", account_name: "小伴"}), "小伴（100）");
  assert.equal(h.product.accountLabel({account_id: "internal-account", account_name: "小伴"}), "小伴");
  assert.equal(h.product.accountLabel({account_id: "internal-account"}), "当前账号");
  assert.equal(h.product.targetLabel({scope_kind: "private", participant_id: "onebot-user:internal"}), "私聊 当前对象");
  assert.equal(h.product.targetLabel({scope_kind: "private", participant_id: "onebot-user:internal", display_name: "朋友"}), "私聊 朋友");
  assert.equal(h.product.subjectLabel({participant_id: "onebot-user:internal"}), "当前聊天对象");
  assert.equal(h.product.platformLabel({platform_id: "default"}), "QQ · OneBot");
  assert.equal(h.product.platformLabel({platform_id: "platform-internal"}), "当前平台");
});

test("the cottage selects a role without asking for a conversation target", async () => {
  const h = await harness(null, [], {multipleRoles: true});
  h.product.renderScope();
  assert.match(h.stageRole.innerHTML, /小伴的小屋/);
  assert.match(h.stageRole.innerHTML, /aria-label="切换人格或角色"/);
  assert.match(h.stageRole.innerHTML, /小伴的小屋/);
  assert.match(h.stageRole.innerHTML, /<option[^>]*>夜航 · QQ 100<\/option>/);
  assert.doesNotMatch(h.stageRole.innerHTML, /会话对象|product-target|data-product-refresh|账号与会话/);
  assert.equal(h.product.roleChoices().length, 2);
});

test("a shared role prefers its concrete private window over a wildcard template", async () => {
  const h = await harness(null, [], {wildcardFirstTarget: true});
  const choices = h.product.roleChoices();
  assert.equal(choices.length, 1);
  assert.equal(choices[0].target.target_id, "first");
  assert.equal(h.product.target().target_id, "first");
  await h.product.choose("wildcard", "synthetic-role");
  assert.equal(h.product.target().target_id, "wildcard");
  assert.equal(h.product.roleTarget().target_id, "first");
  assert.match(h.product.role().state_api, /\/targets\/first\//);
});

test("the role cottage selector is inside the room and the outer status bar is absent", () => {
  const roomStart = shellHtml.indexOf('<div class="room-stage" id="stageScene">');
  const roleTitle = shellHtml.indexOf('id="stageRole"');
  const roomScene = shellHtml.indexOf('class="voxel-room"', roomStart);
  assert.ok(roomStart >= 0 && roleTitle > roomStart && roleTitle < roomScene,
    "role title and selector belong inside the room scene at its top layer");
  /* 取的是基础规则本身。原来的正则 `\.stage-role\s*\{` 会先命中任何以
     `.stage-role` 结尾的选择器（例如 `#homeShell[data-away="true"] .stage-role{display:none}`
     那条缩略态规则），于是断言随时序变化而指向另一条规则。锚到行首。 */
  const roleStyle = shellCss.match(/^\.stage-role\s*\{([^}]*)\}/m)?.[1] || "";
  assert.match(roleStyle, /position:\s*absolute/);
  assert.match(roleStyle, /top:\s*12px/);
  assert.match(roleStyle, /left:\s*12px/);
  assert.doesNotMatch(shellHtml, /class="topbar"|id="breadcrumb"|data-host-badge/);
});

test("a single role keeps a plain cottage title without a redundant selector", async () => {
  const h = await harness();
  h.product.renderScope("林间");
  assert.match(h.stageRole.innerHTML, /小伴的小屋/);
  assert.doesNotMatch(h.stageRole.innerHTML, /<select|data-product-refresh|账号与会话/);
});

test("choosing a role updates the cottage title and role-scoped data", async () => {
  const h = await harness(null, [], {multipleRoles: true});
  await h.product.chooseRole("another-role");
  h.product.renderScope();
  assert.equal(h.product.role().role_id, "another-role");
  assert.match(h.stageRole.innerHTML, /夜航的小屋/);
  assert.match(h.stageRole.innerHTML, /value="\[&quot;bot-a&quot;,&quot;another-role&quot;\]" selected/);
});

test("role-wide state routes survive a group context while group actions keep group routes", async () => {
  const h = await harness(null, [], {pairedMode: true});
  await h.product.choose("group", "synthetic-role", true);
  assert.equal(h.product.target().scope_kind, "group");
  assert.match(h.product.role().state_api, /targets\/first\/roles\/synthetic-role\/state$/);
  assert.match(h.product.role().reply_api, /targets\/group\/roles\/synthetic-role\/reply$/);
  assert.equal(h.product.role().group_memory_api,
    "/" + HOST + "/targets/group/roles/synthetic-role/group-memory");
  assert.equal(h.product.roleTarget().target_id, "first");
  assert.match(h.product.role().model_api, /targets\/group\/roles\/synthetic-role\/model$/);
  assert.match(h.product.windowRole().role_config_api, /targets\/group\/roles\/synthetic-role\/config$/);
  assert.match(h.product.role().config_api, /targets\/first\/roles\/synthetic-role\/config$/);
  assert(h.calls.some(call => call.endpoint === "targets/first/roles/synthetic-role/state"),
    "the role state summary uses the private-backed role endpoint");
  assert(!h.calls.some(call => call.endpoint === relationshipApi),
    "a group view must not fetch an arbitrary private relationship summary");
});

test("display labels preserve names while internal role and executor keys stay out of visible text", async () => {
  const h = await harness();
  assert.equal(h.product.roleLabel(), "小伴");
  assert.equal(h.product.roleLabel({role_id: "望舒"}), "望舒");
  for (const role_id of ["staging-role", "role-a", "a".repeat(64)]) {
    assert.equal(h.product.roleLabel({role_id}), "当前角色");
  }
  assert.equal(h.product.actorLabel("dashboard:synthetic-admin"), "本地管理员");
  assert.equal(h.product.executorLabel({executor_is_current: true}), "当前私聊窗口");
  assert.equal(h.product.executorLabel({executor_is_current: false,
    executor_window: {native_scope_id: "internal-session", account_id: "internal-account"}}), "同一角色的其他私聊窗口");
  h.product.mountWorkspace();
  await h.events.message({source: h.relationships.contentWindow,
    data: {channel: "companion-workspace", kind: "ready"}});
  const display = h.relationships.messages.find(message => message.kind === "context").context.display;
  assert.equal(display.role_name, "小伴");
  assert.equal(display.subject_name, "朋友备注");
  assert.doesNotMatch(JSON.stringify(display), /synthetic-role|dashboard:/);
  assert.equal(h.product.identity().operator_id, "dashboard:synthetic-admin");
});

test("the account workspace relays account discovery, mappings, relationship discoveries and startup", async () => {
  const h = await harness();
  h.product.mountWorkspace();
  for (const [method, endpoint] of [["GET", "accounts"], ["POST", "accounts"], ["POST", "accounts/model-provider"],
    ["POST", "accounts/window-link"], ["POST", "accounts/window-unlink"], ["POST", "host/start"]]) {
    await h.request(h.host, method, endpoint);
    assert.equal(h.host.messages.at(-1).ok, true);
  }
  await h.request(h.host, "GET", "acquaintance");
  assert.equal(h.host.messages.at(-1).ok, true);
  assert.deepEqual(h.calls.map(call => `${call.method} ${call.endpoint}`),
    ["GET accounts", "POST accounts", "POST accounts/model-provider", "POST accounts/window-link",
      "POST accounts/window-unlink", "POST host/start", "GET acquaintance"]);
  await h.request(h.host, "GET", "accounts/session");
  await h.request(h.host, "POST", "accounts/session/extra");
  await h.request(h.host, "POST", "acquaintance");
  await h.request(h.host, "GET", "acquaintance/authorization");
  assert.equal(h.calls.length, 7);
  assert.equal(h.host.messages.at(-1).ok, false);
});

test("an uninitialized directory leaves role mapping available for automatic preparation", async () => {
  const h = await harness(null, [], {directoryUnavailable: true});
  assert.match(h.product.errorLabel("staging_not_initialized"), /角色数据尚未就绪/);
  assert.doesNotMatch(h.product.errorLabel("staging_not_initialized"), /还没完成首次设置/);
  assert.match(h.product.workspace("host"), /product-workspace-frame/);
  for (const name of ["identity", "relationships"]) {
    assert.doesNotMatch(h.product.workspace(name), /product-workspace-frame/);
  }
  h.product.mountWorkspace();
  assert.equal(h.host.srcdoc, "<p>host</p>");
  for (const [method, endpoint] of [["GET", "accounts"], ["POST", "accounts"], ["POST", "accounts/model-provider"], ["GET", "host/status"]]) {
    await h.request(h.host, method, endpoint);
    assert.equal(h.host.messages.at(-1).ok, true);
  }
  assert.deepEqual(h.calls.map(call => `${call.method} ${call.endpoint}`),
    ["GET accounts", "POST accounts", "POST accounts/model-provider", "GET host/status"]);
});

test("directory failures keep the cottage title and a recovered single role has no selector", async () => {
  const options = {mode: "multi", directoryUnavailable: true};
  const h = await harness(null, [], options);
  h.product.renderScope();
  assert.match(h.stageRole.innerHTML, /陪伴的小屋/);
  assert.doesNotMatch(h.stageRole.innerHTML, /product-target|product-role|data-product-refresh|账号与会话/);
  options.directoryError = "core_access_required";
  await h.product.connect();h.product.renderScope();
  assert.match(h.stageRole.innerHTML, /陪伴的小屋/);
  assert.doesNotMatch(h.stageRole.innerHTML, /没有管理权限|dashboard:|core_access_required/);
  options.directoryUnavailable = false;
  await h.product.connect();h.product.renderScope();
  assert.match(h.stageRole.innerHTML, /小伴的小屋/);
  assert.doesNotMatch(h.stageRole.innerHTML, /<select/);
  assert(h.calls.every(call => call.method === "GET"
    || (call.method === "POST" && call.endpoint === "page/browser-preferences")));
});

test("reconnect paints the cached section summary before refreshing it", async () => {
  const h = await harness(null, [], {captureTimers: true});
  h.calls.length = 0;
  const beforeTimers = h.timers.length;
  await h.product.connect();
  const summaryCalls = h.calls.filter(call => /\/(state|life|growth|memory|relationships)$/.test(call.endpoint));
  assert.equal(summaryCalls.length, 0, "cached summary should paint without waiting for the section endpoints");
  assert.ok(h.timers.length > beforeTimers, "a background refresh should still be scheduled");
});

test("runtime loading errors stay distinct from first-run completion in the home overview", async () => {
  const h = await harness(null, [], {summaryErrors: {
    "targets/first/roles/synthetic-role/state": "staging_not_initialized",
    "targets/first/roles/synthetic-role/life": "staging_not_initialized",
  }});
  assert.equal(h.product.unavailableCode("state"), "staging_not_initialized");
  assert.equal(h.product.unavailableCode("life"), "staging_not_initialized");
  const overview = h.product.stateCard() + h.product.scheduleCard();
  assert.match(overview, /角色数据尚未就绪/);
  assert.match(overview, /data-go="manage" data-manage="host"/);
  assert.doesNotMatch(overview, /还没完成首次设置/);
});

test("an unverified host cannot mount or relay account management requests", async () => {
  const h = await harness(null, [], {pluginName: "another-plugin"});
  assert.doesNotMatch(h.product.workspace("host"), /product-workspace-frame/);
  h.product.mountWorkspace();
  assert.equal(h.host.srcdoc, "");
  await h.request(h.host, "GET", "accounts");
  assert.equal(h.calls.length, 0);
});

test("host context updates preserve the browser theme and synchronize every workspace", async () => {
  const h = await harness("walnut");
  h.product.mountWorkspace();
  h.hostTheme(false);
  assert.equal(h.document.documentElement.dataset.theme, "walnut");
  assert.equal(h.product.appearance().theme, "walnut");
  assert.equal(h.identity.messages.at(-1).appearance.theme, "walnut");
  assert.equal(h.relationships.messages.at(-1).appearance.theme, "walnut");
  h.hostTheme(true);
  assert.equal(h.document.documentElement.dataset.theme, "walnut");
  await h.product.connect();
  assert.equal(h.contextHandlers.size, 1, "reconnecting replaces the context subscription");
});

test("account display preferences restore after a sandboxed iframe reload and later changes save", async () => {
  const saved = {
    appearance: {theme: "walnut", density: "roomy", radius: "round", typeScale: "l", motion: "low"},
    followLight: true,
    largeText: true,
  };
  const h = await harness(null, [], {displayPreferences: {preferences: saved}});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.document.documentElement.dataset.theme, "walnut");
  for (const [name, value] of Object.entries(saved.appearance)) {
    assert.equal(h.product.appearance()[name], value, `${name} did not restore`);
  }
  assert.equal(h.product.followLight(), true);
  assert.equal(h.largeText(), true, "large text restores with the same account preference document");

  h.product.setAppearance("theme", "night");
  h.product.setFollowLight(false);
  await new Promise(resolve => setTimeout(resolve, 160));
  const saves = h.calls.filter(call => call.method === "POST"
    && call.endpoint === "page/display-preferences");
  assert.equal(saves.length, 1, "one debounced save carries the complete preference document");
  assert.equal(saves[0].body.appearance.theme, "night");
  assert.equal(saves[0].body.followLight, false);
  assert.equal(saves[0].body.largeText, true);
});

test("each workspace sends requests only to its own API and response channel", async () => {
  const h = await harness();
  h.product.mountWorkspace();
  await h.request(h.identity, "GET", "acquaintance", "directory-request");
  await h.request(h.relationships, "GET", "relationships", "relationship-request");
  assert.deepEqual(h.calls.map(call => call.endpoint), ["acquaintance", relationshipApi]);
  assert.equal(h.identity.messages.at(-1).requestId, "directory-request");
  assert.equal(h.relationships.messages.at(-1).requestId, "relationship-request");
  await h.request(h.identity, "POST", "acquaintance");
  await h.request(h.identity, "GET", "relationships");
  assert.equal(h.calls.length, 2, "a read-only directory cannot relay mutation or relationship requests");
  assert.equal(h.identity.messages.at(-1).ok, false);
  await h.events.message({source: {}, data: {channel: "companion-workspace", kind: "request",
    requestId: "unrelated", method: "GET", path: "acquaintance", body: {}}});
  assert.equal(h.calls.length, 2, "messages from unrelated frames are ignored");
});

test("group relationship workspace switches to the bounded collective archive routes", async () => {
  const h = await harness(null, [], {groupMode: true});
  h.product.mountWorkspace();
  await h.request(h.relationships, "GET", "group-memory", "group-list");
  await h.request(h.relationships, "POST", "group-memory/detail", "group-detail");
  await h.request(h.relationships, "POST", "group-memory/retract", "group-retract");
  await h.request(h.relationships, "GET", "group-memory/safety", "group-safety");
  await h.request(h.relationships, "GET", "group-memory/safety", "group-safety-reviewed");
  await h.request(h.relationships, "POST", "group-memory/safety/review", "group-review");
  await h.request(h.relationships, "GET", "group-memory/operation", "group-receipt");
  assert.deepEqual(h.calls.map(call => call.endpoint), [groupMemoryApi, groupMemoryApi + "/detail",
    groupMemoryApi + "/retract", groupMemoryApi + "/safety", groupMemoryApi + "/safety",
    groupMemoryApi + "/safety/review",
    groupMemoryApi + "/operation"]);
  await h.request(h.relationships, "POST", "group-memory/command", "rejected-group-command");
  await h.request(h.relationships, "GET", "relationships", "rejected-private-api");
  assert.equal(h.calls.length, 7, "group scope cannot relay private relationship or unregistered commands");
});

test("identity workspace opens only the selected configured group's reply and memory workspaces", async () => {
  const h = await harness(null, [], {groupMode: true});
  h.product.mountWorkspace();
  await h.events.message({source: h.identity.contentWindow, data: {channel: "companion-workspace",
    kind: "navigate", target: {tab: "group-reply", platform_id: "qq-main", bot_id: "bot-a",
      account_id: "100", group_id: "300", role_id: "synthetic-role"}}});
  assert.deepEqual(h.navigations, [["manage", "decision", ""]]);
  await h.events.message({source: h.identity.contentWindow, data: {channel: "companion-workspace",
    kind: "navigate", target: {tab: "group-memory", platform_id: "qq-main", bot_id: "bot-a",
      account_id: "100", group_id: "300", role_id: "synthetic-role"}}});
  assert.deepEqual(h.navigations, [["manage", "decision", ""], ["manage", "identity-feelings", ""]]);
  await h.events.message({source: h.identity.contentWindow, data: {channel: "companion-workspace",
    kind: "navigate", target: {tab: "group-reply", platform_id: "qq-main", bot_id: "bot-a",
      account_id: "100", group_id: "300", role_id: "another-role"}}});
  await h.events.message({source: h.identity.contentWindow, data: {channel: "companion-workspace",
    kind: "navigate", target: {tab: "group-memory", platform_id: "qq-main", bot_id: "wrong-bot",
      account_id: "100", group_id: "300", role_id: "synthetic-role"}}});
  assert.deepEqual(h.navigations, [["manage", "decision", ""], ["manage", "identity-feelings", ""]],
    "unknown roles and group routes cannot redirect the workspace");
  const navigationFailures = h.identity.messages.filter(message => message.kind === "navigate-failed");
  assert.equal(navigationFailures.length, 2);
  assert.match(navigationFailures[0].message, /角色映射已经变化.*重新读取群聊目录/);
});

for (const statusFailure of [false, true]) test(`saving a group makes its buttons usable without reloading (status failure: ${statusFailure})`, async () => {
  const h = await harness();
  h.product.mountWorkspace();
  const directory = JSON.parse(JSON.stringify(h.catalog));
  const group = {target_id: "added-group", scope_kind: "group", group_id: "300",
    platform_id: "qq-main", bot_id: "bot-a", account_id: "100", roles: [{role_id: "synthetic-role",
      reply_api: "/" + HOST + "/targets/added-group/roles/synthetic-role/reply",
      group_memory_api: "/" + HOST + "/targets/added-group/roles/synthetic-role/group-memory"}]};
  directory.targets.push(group);
  const selection = {platform_id: group.platform_id, bot_id: group.bot_id,
    account_id: group.account_id, group_id: group.group_id};
  const saved = await saveGroupSelection(h, directory, [selection], {statusFailure});
  assert.equal(saved.notifications.length, 1, "saving must refresh the cottage's navigation directory");
  for (const tab of ["group-reply", "group-memory"]) {
    await h.events.message({source: h.identity.contentWindow, data: {channel: "companion-workspace",
      kind: "navigate", target: {...selection, role_id: "synthetic-role", tab}}});
  }
  assert.equal(h.identity.messages.filter(message => message.kind === "navigate-failed").length, 0);
  assert.equal(h.product.selection().target_id, group.target_id);
  assert.deepEqual(h.navigations, [["manage", "decision", ""], ["manage", "identity-feelings", ""]]);
  assert.match(h.product.workspace("relationships"), /product-workspace-frame/);
  await h.request(h.relationships, "GET", "group-memory");
  assert.equal(h.calls.at(-1).endpoint, "targets/added-group/roles/synthetic-role/group-memory");
  h.product.unmountWorkspace();
});

test("group archive mounts with only a group memory endpoint and never falls back to private records", async () => {
  const h = await harness(null, [], {groupMode: true});
  assert.equal(h.product.role().relationships_api, null);
  assert.match(h.product.workspace("relationships"), /product-workspace-frame/);
  h.catalog.targets[0].roles[0].group_memory_api = null;
  h.catalog.targets[0].roles[0].relationships_api = "/" + HOST + "/" + relationshipApi;
  assert.doesNotMatch(h.product.workspace("relationships"), /product-workspace-frame/);
});

test("identity workspace can navigate to first connection for group-target setup", async () => {
  const h = await harness();
  h.product.mountWorkspace();
  await h.events.message({source: h.identity.contentWindow, data: {channel: "companion-workspace",
    kind: "navigate", target: {tab: "setup"}}});
  assert.deepEqual(h.navigations, [["manage", "setup", ""]]);
});

test("identity workspace relay preserves backend rejection code and reason", async () => {
  const failure = Object.assign(new Error("group target rejected by host"), {code: "account_config_running"});
  const h = await harness(null, [], {postFailure: failure});
  h.product.mountWorkspace();
  await h.request(h.identity, "POST", "acquaintance/authorization", "authorization-failure");
  const response = h.identity.messages.at(-1);
  assert.equal(response.ok, false);
  assert.equal(response.code, "account_config_running");
  assert.equal(response.reason, "group target rejected by host");
  assert.match(response.error, /group target rejected by host/);
});

test("a response from a previous mount cannot enter the replacement workspace", async () => {
  const h = await harness();
  h.product.mountWorkspace();
  const resolve = h.defer();
  const request = h.request(h.relationships, "GET", "relationships");
  h.product.unmountWorkspace();
  h.product.mountWorkspace();
  resolve({items: [{title: "old scope"}]});
  await request;
  assert.equal(h.relationships.messages.length, 0);
  assert.equal(h.identity.messages.length, 0);
});

test("mapping save refreshes directory, capabilities and summary while keeping account editor mounted", async () => {
  const h = await harness();
  h.product.mountWorkspace();
  const next = JSON.parse(JSON.stringify(h.catalog));
  next.targets[0].target_id = "replacement";
  const nextRole = next.targets[0].roles[0];
  nextRole.role_id = "merged-role"; nextRole.role_name = "星缘";
  nextRole.relationships_api = "/" + HOST + "/targets/replacement/roles/merged-role/relationships";
  h.setDirectory(next);
  const source = h.host.srcdoc;
  await h.events.message({source: h.host.contentWindow,
    data: {channel: "companion-workspace", kind: "host-mappings-updated"}});
  assert.equal(h.host.srcdoc, source);
  assert.equal(h.product.selection().role_id, "merged-role");
  assert.match(h.stageRole.innerHTML, /星缘的小屋/);
  assert.ok(h.calls.some(call => call.endpoint === "page/targets"));
  assert.ok(h.calls.some(call => call.endpoint === "integration/manifest"));
  assert.ok(h.calls.some(call => call.endpoint.endsWith("/state")));
  assert.equal(h.updates.at(-1).kind, "host-mappings");
  assert.equal(h.host.messages.at(-1).kind, "host-mappings-refreshed");
  await h.request(h.host, "GET", "accounts", "fresh-accounts");
  assert.equal(h.host.messages.at(-1).ok, true);
  assert.equal(h.product.selection().target_id, null);
  await h.product.choose("replacement", "merged-role", true);
  await h.request(h.relationships, "GET", "relationships", "fresh-relationships");
  assert.equal(h.relationships.messages.at(-1).ok, true);
  assert.equal(h.calls.at(-1).endpoint, "targets/replacement/roles/merged-role/relationships");
});

test("a role switch during mapping refresh prevents an old directory from replacing the chosen role", async () => {
  const h = await harness(null, [], {multipleRoles: true});
  h.product.mountWorkspace();
  const resolve = h.defer("page/targets");
  const refresh = h.events.message({source: h.host.contentWindow,
    data: {channel: "companion-workspace", kind: "host-mappings-updated"}});
  await h.product.chooseRole("another-role", true);
  resolve({installation_id: "stale", targets: []});
  await refresh;
  assert.equal(h.product.selection().role_id, "another-role");
  await h.request(h.host, "GET", "accounts");
  assert.equal(h.host.messages.at(-1).ok, true);
});

test("requests in flight during a mapping refresh receive a bounded stale-scope error", async () => {
  const h = await harness();
  h.product.mountWorkspace();
  const resolve = h.defer();
  const read = h.request(h.relationships, "GET", "relationships", "old-read");
  await h.events.message({source: h.host.contentWindow,
    data: {channel: "companion-workspace", kind: "host-mappings-updated"}});
  resolve({items: [{title: "old scope"}]});
  await read;
  assert.equal(h.relationships.messages.at(-1).requestId, "old-read");
  assert.equal(h.relationships.messages.at(-1).ok, false);
  assert.equal(h.relationships.messages.at(-1).code, "scope_changed");
});

test("account editor cannot relay manual runtime operations or native session edits", async () => {
  const h = await harness();
  h.product.mountWorkspace();
  for (const endpoint of ["host/probe", "host/initialize", "host/stop", "accounts/session"]) {
    await h.request(h.host, "POST", endpoint);
    assert.equal(h.host.messages.at(-1).ok, false);
  }
  assert.equal(h.calls.length, 0);
});

test("the seven V2 capabilities stay in the cottage even with legacy plugin page metadata", async () => {
  const h = await harness(null, [
    {capability_id: "screen", installed: true, plugin_id: "installed_screen", pages: ["屏幕工作台", "观察记录"]},
    {capability_id: "image", installed: true, plugin_id: "installed_image", pages: []},
    {capability_id: "game", installed: false, plugin_id: "missing_game", pages: ["游戏"]},
  ]);
  assert.equal(h.product.providerAddress("screen", "屏幕伴侣"), "");
  assert.equal(h.product.providerAddress("screen", "观察记录"), "");
  assert.equal(h.product.providerAddress("image"), "");
  assert.equal(h.product.providerAddress("game"), "");
  assert.equal(h.product.providerAddress("unknown"), "");
  const detail=h.product.providerDetail("screen").body;
  assert.doesNotMatch(detail, /installed_screen|data-copy-address|plugin-page|extension\/plugins/);
  assert.doesNotMatch(detail, /target="_blank"/, "the Dashboard sandbox blocks popup navigation");
  assert.match(h.product.providerDetail("image").body, /data-output-target="materials"/,
    "an image provider without an independent page opens the existing materials workspace");
  for (const capability of ["content", "reality", "together", "game", "live"]) {
    const native = await harness(null, [{capability_id:capability, installed:true,
      plugin_id:"legacy_" + capability, pages:["旧插件页"]}]);
    assert.equal(native.product.providerAddress(capability), "");
    assert.doesNotMatch(native.product.providerDetail(capability).body, /legacy_|plugin-page|extension\/plugins/);
  }
});

test("workspace links remain inside the cottage and preserve embedded tab destinations", async () => {
  const h = await harness();
  assert.equal(h.product.workspaceAddress("life", "reading"), "#life/diary/reading");
  assert.equal(h.product.workspaceAddress("life", "sleep"), "#life/diary/sleep");
  assert.equal(h.product.workspaceAddress("persona"), "#manage/governance/questionnaire");
  assert.equal(h.product.workspaceAddress("relationships"), "#manage/identity-feelings");
  assert.equal(h.product.workspaceAddress("missing"), "");
});

test("bundled Image and Screen furniture use the V2 lifecycle route and never link to provider plugins", async () => {
  const extensions = [
    {capability_id: "image", owner: "companion_v2", extension_id: "image",
      assembled: false, installed_revision: 0, integration_state: "unassembled", reason: "尚未装配"},
    {capability_id: "screen", owner: "companion_v2", extension_id: "companion.screen.native",
      assembled: true, installed_revision: 4, version: "2.0.0", installed_package_version: "1.9.9",
      update_available: true, integration_state: "degraded", reason: "已装配内容与当前 V2 内置版不同"},
  ];
  const h = await harness(null, extensions);
  assert.equal(h.product.providerAddress("image"), "");
  assert.equal(h.product.providerAddress("screen"), "");
  assert.match(h.product.providerDetail("screen").body, /V2 内置版本[\s\S]*2\.0\.0[\s\S]*装配版本[\s\S]*1\.9\.9/);
  await h.product.manageExtension("image", "assemble", 0, "image-assemble");
  const lifecycle = h.calls.find(call => call.endpoint === "integration/lifecycle");
  assert.equal(lifecycle.method, "POST");
  assert.equal(JSON.stringify(lifecycle.body), JSON.stringify({extension_id: "image", action: "assemble",
    expected_revision: 0, operation_key: "image-assemble", role_id: "synthetic-role", target_id: "first"}));
  await h.product.manageExtension("screen", "update", 4, "screen-sync");
  const update = h.calls.filter(call => call.endpoint === "integration/lifecycle").at(-1);
  assert.equal(JSON.stringify(update.body), JSON.stringify({extension_id: "companion.screen.native", action: "update",
    expected_revision: 4, operation_key: "screen-sync", role_id: "synthetic-role", target_id: "first"}));
  assert.equal(h.calls.at(-1).endpoint, "integration/manifest", "家具状态来自真实 V2 manifest 刷新");
});

test("Content, Reality, Together, Game and Live share the V2 lifecycle route", async () => {
  const names = ["content", "reality", "together", "game", "live"];
  const h = await harness(null, names.map(name => ({capability_id:name, owner:"companion_v2",
    extension_id:name, assembled:true, desired_state:"active", installed_revision:3})));
  for (const name of names) {
    await h.product.manageExtension(name, "pause", 3, name + "-pause");
    const command = h.calls.filter(call => call.endpoint === "integration/lifecycle").at(-1);
    assert.equal(JSON.stringify(command.body), JSON.stringify({extension_id:name, action:"pause",
      expected_revision:3, operation_key:name + "-pause", role_id:"synthetic-role", target_id:"first"}));
  }
});

/* 相识详情页的授权面板要写，而这一页的主要通道是只读的。放行的必须是**它自己的
   那一个地址**，而不是"identity 工作区从此可以写"：地址放错会让面板上的按钮
   静默变成 invalid_response（用户看到的是"点了没反应"）。
   endpoint 与目录一样是**插件内相对路径**（前导斜杠与目录名都剥掉），
   这是桥自己的口径，别处（reply-page 等）也照这一条。 */
test("the identity workspace relays only its member configuration and authorization routes", async () => {
  const h = await harness();
  h.product.mountWorkspace();
  await h.request(h.identity, "POST", "acquaintance/authorization", "authorization-write");
  assert.deepEqual(h.calls.map(call => call.method + " " + call.endpoint),
    ["POST acquaintance/authorization"],
    "授权写入要落到目录端点族下的第二段，而不是目录本身");
  assert.equal(h.identity.messages.at(-1).ok, true);
  assert.equal(h.identity.messages.at(-1).requestId, "authorization-write");
  // 读也要能走那个地址（面板先读再写），但别的地址一律不放行。
  await h.request(h.identity, "GET", "acquaintance/authorization", "authorization-read");
  assert.deepEqual(h.calls.map(call => call.method + " " + call.endpoint),
    ["POST acquaintance/authorization", "GET acquaintance/authorization"]);
  for (const [method, path] of [["POST", "acquaintance"], ["GET", "acquaintance/command"],
                                ["POST", "acquaintance/authorization/extra"]]) {
    await h.request(h.identity, method, path, "rejected-" + method + path);
    assert.equal(h.identity.messages.at(-1).ok, false, method + " " + path + " 不该被放行");
  }
  assert.equal(h.calls.length, 2, "被拒的请求不许真的发出去");
  for (const [method, path] of [["GET", "acquaintance/settings"], ["POST", "acquaintance/settings"],
                                ["POST", "acquaintance/identity"], ["POST", "acquaintance/admin-name"]]) {
    await h.request(h.identity, method, path, "member-" + method + path);
    assert.equal(h.identity.messages.at(-1).ok, true, method + " " + path);
    assert.equal(h.calls.at(-1).endpoint, path);
  }
  for (const [method, path] of [["GET", "acquaintance/identity"], ["GET", "acquaintance/admin-name"],
                                ["POST", "acquaintance/settings/extra"], ["POST", "configuration/person"]]) {
    await h.request(h.identity, method, path, "rejected-member-" + method + path);
    assert.equal(h.identity.messages.at(-1).ok, false, method + " " + path);
  }
  await h.request(h.identity, "POST", "accounts/group-target", "group-target-write");
  assert.equal(h.identity.messages.at(-1).ok, false);
  await h.request(h.identity, "GET", "accounts/group-target", "rejected-group-target-read");
  assert.equal(h.identity.messages.at(-1).ok, false, "group target settings are write-only from the identity workspace");
  assert.equal(h.calls.length, 6);
});


test("a first visit selects a role without inventing a current window", async () => {
  const h = await harness(null, [], {pairedMode:true, unselectedWindow:true});
  assert.deepEqual(JSON.parse(JSON.stringify(h.product.selection())),
    {target_id:null, role_id:"synthetic-role", bot_id:"bot-a"});
  assert.equal(h.product.target(), undefined);
  assert.equal(h.product.windowRole(), null);
  assert.equal(h.product.roleSelection().target_id, "first");
  assert.match(h.product.role().state_api, /targets\/first\//);
  assert.match(h.product.workspace("relationships"), /窗口未选择/);
  assert.doesNotMatch(h.product.workspace("relationships"), /product-workspace-frame/);
});

test("the same role ID on two bots remains two independent choices", async () => {
  const h = await harness(null, [], {mutateCatalog(directory) {
    const first = directory.targets[0];
    directory.targets.push({...first, bot_id:"bot-b", account_id:"101", target_id:"second",
      roles:first.roles.map(role => ({...role, state_api:role.state_api.replace("first", "second")}))});
  }});
  const choices = h.product.roleChoices();
  assert.equal(choices.length, 2);
  const second = choices.find(choice => choice.target.bot_id === "bot-b");
  await h.product.chooseRoleKey(h.product.roleChoiceKey(second));
  assert.equal(h.product.selection().bot_id, "bot-b");
  assert.equal(h.product.selection().target_id, null);
  assert.equal(h.product.roleTarget().target_id, "second");
  assert.match(h.product.role().state_api, /targets\/second\//);
  h.product.renderScope();
  assert.match(h.stageRole.innerHTML, /小伴 · QQ 100/);
  assert.match(h.stageRole.innerHTML, /小伴 · QQ 101/);
  assert.equal((h.stageRole.innerHTML.match(/ selected/g) || []).length, 1);
  assert.equal(h.scopeEvents.at(-1).detail.bot_id, "bot-b");
});

test("a role switch retains only a compatible explicitly selected window", async () => {
  const h = await harness(null, [], {pairedMode:true, multipleRoles:true});
  await h.product.chooseRole("another-role");
  assert.equal(h.product.selection().target_id, "first", "both roles are mapped to the selected private window");
  await h.product.choose("group", "synthetic-role");
  await h.product.chooseRole("another-role");
  assert.equal(h.product.selection().target_id, null, "an incompatible group must not turn into a private window");
  await h.product.chooseRole("synthetic-role");
  assert.equal(h.product.selection().target_id, null, "selecting a role does not implicitly select its representative window");
});

test("Cookie selection restores the exact bot, role and explicit window", async () => {
  const cookieStorage = new Map();
  const first = await harness(null, [], {cookieStorage, storageUnavailable:true, pairedMode:true, multipleRoles:true, unselectedWindow:true});
  await first.product.choose("group", "synthetic-role");
  const second = await harness(null, [], {cookieStorage, storageUnavailable:true, pairedMode:true, multipleRoles:true, unselectedWindow:true});
  assert.equal(second.product.selection().target_id, "group");
  assert.equal(second.product.selection().bot_id, "bot-a");
  assert.equal(second.product.selection().role_id, "synthetic-role");
  await second.product.clearWindow();
  const third = await harness(null, [], {cookieStorage, storageUnavailable:true, pairedMode:true, unselectedWindow:true});
  assert.equal(third.product.selection().target_id, null);
});

test("browser selection is isolated by installation and Dashboard operator", async () => {
  const cookieStorage = new Map();
  const first = await harness(null, [], {cookieStorage, pairedMode:true});
  await first.product.choose("group", "synthetic-role");
  for (const changes of [{installation_id:"another-installation"}, {operator_id:"dashboard:another-admin"}]) {
    const other = await harness(null, [], {cookieStorage, pairedMode:true, unselectedWindow:true,
      mutateCatalog:directory => Object.assign(directory, changes)});
    assert.notEqual(other.product.browserPreferencesIdentity(), first.product.browserPreferencesIdentity());
    assert.equal(other.product.selection().target_id, null);
  }
});

test("an obsolete saved window keeps its role and asks for a new window", async () => {
  const h = await harness(null, [], {unselectedWindow:true,
    savedSelection:{bot_id:"bot-a",role_id:"synthetic-role",target_id:"removed-group"}});
  assert.equal(h.product.selection().role_id, "synthetic-role");
  assert.equal(h.product.selection().target_id, null);
  assert.match(h.product.selectionNotice(), /窗口已不可用/);
  assert.equal(JSON.parse(h.cookieStorage.get(h.cookieKey)).selection.target_id, null);
});

test("an obsolete saved bot cannot resolve a matching role ID from another account", async () => {
  const h = await harness(null, [], {unselectedWindow:true,
    savedSelection:{bot_id:"removed-bot",role_id:"synthetic-role",target_id:"first"}});
  assert.equal(h.product.selection().bot_id, "bot-a");
  assert.equal(h.product.selection().target_id, null);
  assert.match(h.product.selectionNotice(), /角色或账号已不可用/);
});

test("role and window changes use the same draft guard and cancellation preserves selection", async () => {
  const h = await harness(null, [], {pairedMode:true,multipleRoles:true});
  const before = JSON.stringify(h.product.selection());
  let attempts = 0;
  h.product.setSelectionGuard(async () => { attempts++; return false; });
  assert.equal(await h.product.chooseRole("another-role"), false);
  assert.equal(await h.product.choose("group", "synthetic-role", true), false);
  assert.equal(await h.product.clearWindow(), false);
  assert.equal(attempts, 3);
  assert.equal(JSON.stringify(h.product.selection()), before);
  h.product.setSelectionGuard(async () => true);
  assert.equal(await h.product.choose("group", "synthetic-role"), true);
  assert.equal(h.product.selection().target_id, "group");
});

test("a late draft decision cannot replace a more recent selection intent", async () => {
  const h = await harness(null, [], {pairedMode:true,multipleRoles:true});
  let allowFirst;
  h.product.setSelectionGuard(() => new Promise(resolve => { allowFirst=resolve; }));
  const earlier = h.product.chooseRole("another-role");
  h.product.setSelectionGuard(() => true);
  await h.product.choose("group", "synthetic-role");
  allowFirst(true);
  assert.equal(await earlier, false);
  assert.equal(h.product.selection().target_id, "group");
});

test("blocked Cookies still permit selection and explain that it cannot persist", async () => {
  const h = await harness(null, [], {unselectedWindow:true,storageUnavailable:true,cookiesBlocked:true,multipleRoles:true});
  await h.product.chooseRole("another-role");
  assert.equal(h.product.selection().role_id, "another-role");
  assert.match(h.product.browserPreferencesNotice(), /Cookie 未能保存/);
});

test("a failed Cookie read does not overwrite preferences and a later reconnect restores them", async () => {
  const options = {unselectedWindow:true,multipleRoles:true,getFailure:new Error("offline"),
    getFailureEndpoint:"page/browser-preferences",
    savedSelection:{bot_id:"bot-a",role_id:"another-role",target_id:"first"}};
  const h = await harness(null, [], options);
  assert.match(h.product.browserPreferencesNotice(), /未能读取/);
  assert.equal(JSON.parse(h.cookieStorage.get(h.cookieKey)).selection.role_id, "another-role");
  assert.equal(h.product.selection().target_id, null);
  delete options.getFailure;
  await h.product.connect();
  assert.equal(h.product.selection().role_id, "another-role");
  assert.equal(h.product.selection().target_id, "first");
  assert.equal(h.product.browserPreferencesNotice(), "");
});

test("rapid role and position changes save in order within the same Cookie", async () => {
  const options = {unselectedWindow:true,multipleRoles:true,pairedMode:true,storageUnavailable:true};
  const h = await harness(null, [], options);
  let started, release;
  const pending = new Promise(resolve => { started=resolve; });
  options.browserPreferencesPost = () => {
    delete options.browserPreferencesPost;
    started();
    return new Promise(resolve => { release=resolve; });
  };
  const earlier = h.product.chooseRole("another-role");
  await pending;
  const latest = h.product.choose("group", "synthetic-role");
  const position = h.product.saveBrowserPreferences({room_card_position:{left:200,top:160}});
  release();
  await Promise.all([earlier, latest, position]);
  const saved = JSON.parse(h.cookieStorage.get(h.cookieKey));
  assert.equal(saved.selection.role_id, "synthetic-role");
  assert.equal(saved.selection.target_id, "group");
  assert.deepEqual(saved.room_card_position, {left:200,top:160});
  await h.product.clearWindow();
  assert.deepEqual(JSON.parse(h.cookieStorage.get(h.cookieKey)).room_card_position, saved.room_card_position);
  await h.product.saveBrowserPreferences({room_card_position:null});
  assert.equal(JSON.parse(h.cookieStorage.get(h.cookieKey)).selection.role_id, "synthetic-role");
  assert.equal(h.storage.size, 0, "role and card preferences never use localStorage");
});

test("a later successful Cookie write clears the storage failure notice", async () => {
  const options = {unselectedWindow:true,multipleRoles:true,cookiesBlocked:true};
  const h = await harness(null, [], options);
  assert.match(h.product.browserPreferencesNotice(), /Cookie 未能保存/);
  options.cookiesBlocked = false;
  await h.product.chooseRole("another-role");
  assert.equal(h.product.browserPreferencesNotice(), "");
  assert.equal(JSON.parse(h.cookieStorage.get(h.cookieKey)).selection.role_id, "another-role");
});

test("saving only the card after a failed initial read does not replace the saved role on reconnect", async () => {
  const options = {unselectedWindow:true,multipleRoles:true,getFailure:new Error("offline"),
    getFailureEndpoint:"page/browser-preferences",
    savedSelection:{bot_id:"bot-a",role_id:"another-role",target_id:"first"}};
  const h = await harness(null, [], options);
  delete options.getFailure;
  await h.product.saveBrowserPreferences({room_card_position:{left:180,top:220}});
  await h.product.connect();
  assert.equal(h.product.selection().role_id, "another-role");
  assert.equal(h.product.selection().target_id, "first");
  const saved = JSON.parse(h.cookieStorage.get(h.cookieKey));
  assert.equal(saved.selection.role_id, "another-role");
  assert.deepEqual(saved.room_card_position, {left:180,top:220});
});

test("saving the role also retries a failed card-position edit in the same Cookie", async () => {
  const options = {unselectedWindow:true,multipleRoles:true};
  const h = await harness(null, [], options);
  options.cookiesBlocked = true;
  assert.equal(await h.product.saveBrowserPreferences({room_card_position:{left:100,top:100}}), false);
  options.cookiesBlocked = false;
  await h.product.chooseRole("another-role");
  const saved = JSON.parse(h.cookieStorage.get(h.cookieKey));
  assert.equal(saved.selection.role_id, "another-role");
  assert.deepEqual(saved.room_card_position, {left:100,top:100});
  assert.equal(h.product.browserPreferencesNotice(), "");
});

test("a role save after an initial read failure also restores the untouched card Cookie", async () => {
  const options = {unselectedWindow:true,multipleRoles:true,getFailure:new Error("offline"),
    getFailureEndpoint:"page/browser-preferences"};
  const h = await harness(null, [], options);
  h.cookieStorage.set(h.cookieKey, JSON.stringify({selection:null,room_card_position:{left:120,top:230}}));
  delete options.getFailure;
  await h.product.chooseRole("another-role");
  assert.equal(h.product.browserPreferences().room_card_position.left, 120);
  assert.equal(h.product.browserPreferencesNotice(), "");
  await h.product.connect();
  assert.equal(h.product.selection().role_id, "another-role");
  assert.equal(h.product.browserPreferences().room_card_position.top, 230);
});


test("reselecting the current role cancels a pending role-switch intent", async () => {
  const h = await harness(null, [], {multipleRoles:true});
  let allow;
  h.product.setSelectionGuard(() => new Promise(resolve => { allow=resolve; }));
  const earlier = h.product.chooseRole("another-role");
  assert.equal(await h.product.chooseRole("synthetic-role"), true);
  allow(true);
  assert.equal(await earlier, false);
  assert.equal(h.product.selection().role_id, "synthetic-role");
});


test("a group-only role without an explicit window exposes only APIs that support role defaults", async () => {
  const h = await harness(null, [], {groupMode:true,unselectedWindow:true});
  assert.equal(h.product.selection().target_id, null);
  assert.equal(h.product.windowRole(), null);
  assert.equal(h.product.role().reply_api, null, "group reply settings are always window overrides");
  assert.equal(h.product.role().group_memory_api, null, "group archive needs an explicit group selection");
  assert.equal(h.product.role().model_api, h.catalog.targets[0].roles[0].model_api,
    "the group model API supports layer=role without private targets");
  assert.equal(h.product.role().config_api, h.catalog.targets[0].roles[0].config_api,
    "the group config API exposes role-owned system.prompts");
  assert.equal(h.product.roleSelection().target_id, "first");
  await h.product.choose("first", "synthetic-role");
  assert.equal(h.product.role().reply_api, h.catalog.targets[0].roles[0].reply_api);
  assert.equal(h.product.role().group_memory_api, h.catalog.targets[0].roles[0].group_memory_api);
  await h.product.clearWindow();
  assert.equal(h.product.role().reply_api, null);
});


test("cancelling the draft guard on a group shortcut leaves the current page without a mapping error", async () => {
  const h = await harness(null, [], {pairedMode:true});
  h.product.mountWorkspace();
  h.product.setSelectionGuard(() => false);
  await h.events.message({source:h.identity.contentWindow, data:{channel:"companion-workspace",
    kind:"navigate", target:{tab:"group-reply", platform_id:"qq-main", bot_id:"bot-a",
      account_id:"100", group_id:"300", role_id:"synthetic-role"}}});
  assert.equal(h.product.selection().target_id, "first");
  assert.deepEqual(h.navigations, []);
  assert.equal(h.identity.messages.filter(message => message.kind === "navigate-failed").length, 0);
  h.product.unmountWorkspace();
});

test("member navigation switches the cottage role and sends the exact member route", async () => {
  const h=await harness(null,[],{multipleRoles:true});
  h.product.mountWorkspace();
  const choice=h.product.roleChoices().find(item=>item.role.role_id==="another-role");
  assert.ok(choice);
  const target={tab:"member-edit",subject_id:"member-a",platform:"qq-main",bot_id:choice.target.bot_id,
    role_id:"another-role",account_id:"100"};
  await h.events.message({source:h.identity.contentWindow,data:{channel:"companion-workspace",kind:"navigate",target}});
  assert.equal(h.product.selection().role_id,"another-role");
  const last=h.identity.messages.filter(item=>item.kind==="context").at(-1).context;
  assert.equal(last.selection.role_id,"another-role");
  assert.equal(last.member_edit.subject_id,"member-a");
});

test("workspace drafts join the outer guard and only the owning frame can resolve a save", async () => {
  const h=await harness(); h.product.mountWorkspace();
  await h.events.message({source:h.identity.contentWindow,data:{channel:"companion-workspace",kind:"draft-state",
    draft:{dirty:true,title:"璃与用户甲",fields:["备注"]}}});
  const drafts=h.product.workspaceDirtyDrafts();
  assert.equal(drafts.length,1);assert.equal(drafts[0].title,"璃与用户甲");
  const saving=drafts[0].save();
  const request=h.identity.messages.at(-1);
  assert.equal(request.kind,"draft-action");assert.equal(request.action,"save");
  await h.events.message({source:h.host.contentWindow,data:{channel:"companion-workspace",kind:"draft-action-result",
    requestId:request.requestId,result:true}});
  await h.events.message({source:h.identity.contentWindow,data:{channel:"companion-workspace",kind:"draft-action-result",
    requestId:request.requestId,result:{error:"未保存"}}});
  assert.equal((await saving).error,"未保存");
  await h.events.message({source:h.identity.contentWindow,data:{channel:"companion-workspace",kind:"draft-state",draft:{dirty:false}}});
  assert.equal(h.product.workspaceDirtyDrafts().length,0);
});

test("unmounting a workspace ends pending draft actions and clears their timers",async()=>{
  const h=await harness();h.product.mountWorkspace();
  await h.events.message({source:h.identity.contentWindow,data:{channel:"companion-workspace",kind:"draft-state",
    draft:{dirty:true,title:"成员资料"}}});
  const pending=h.product.workspaceDirtyDrafts()[0].save();
  h.product.unmountWorkspace();
  assert.match((await pending).error,/已关闭/);
  assert.equal(h.product.workspaceDirtyDrafts().length,0);
});
test("identity account-link bridge forwards all four exact routes with their original payloads", async () => {
  const h = await harness(); h.product.mountWorkspace();
  const params = {subject_id: "synthetic-user", platform: "qq", bot_id: "bot-a", account_id: "100", role_id: "synthetic-role"};
  for (const endpoint of ["acquaintance/account-links", "acquaintance/account-links/history"]) {
    const resolve = h.defer(endpoint);
    const response = {status: "ok", data: endpoint.endsWith("/history")
      ? {records: [], next_cursor: null} : {revision: 0, group: null, candidates: []}};
    const pending = h.request(h.identity, "GET", endpoint, "read-" + endpoint, undefined, params);
    resolve(response); await pending;
    assert.equal(h.identity.messages.at(-1).ok, true, JSON.stringify(h.identity.messages.at(-1)));
    const call = h.calls.at(-1);
    assert.equal(call.endpoint, endpoint);
    assert.equal(JSON.stringify(call.params), JSON.stringify(params));
    assert.equal(h.identity.messages.at(-1).ok, true);
    assert.equal(h.identity.messages.at(-1).data, response, "do not drop the account-link envelope");
  }
  const command = {subject_id: "synthetic-user", route: {platform: "qq", bot_id: "bot-a", account_id: "100", role_id: "synthetic-role"},
    expected_revision: 0, operation_key: "same-safe-retry", share_context: false,
    proactive: {mode: "random", preferred: null, weights: []}};
  for (const endpoint of ["acquaintance/account-links", "acquaintance/account-links/unlink"]) {
    await h.request(h.identity, "POST", endpoint, "write-" + endpoint, undefined, command);
    assert.equal(h.identity.messages.at(-1).ok, true);
    assert.equal(h.calls.at(-1).endpoint, endpoint);
    assert.equal(JSON.stringify(h.calls.at(-1).body), JSON.stringify(command));
  }
  assert.equal(h.calls.length, 4);
});

test("identity account-link bridge still rejects wrong methods, extra suffixes and traversal", async () => {
  const h = await harness(); h.product.mountWorkspace();
  for (const [method, path] of [["GET", "acquaintance/account-links/unlink"],
    ["POST", "acquaintance/account-links/history"], ["DELETE", "acquaintance/account-links"],
    ["GET", "acquaintance/account-links/extra"], ["POST", "acquaintance/account-links/unlink/extra"],
    ["GET", "acquaintance/account-links/../settings"], ["GET", "acquaintance/account-links?admin=true"],
    ["GET", "acquaintance/account-links%2fhistory"], ["POST", "configuration/person"]]) {
    await h.request(h.identity, method, path, "reject-" + method + path);
    assert.equal(h.identity.messages.at(-1).ok, false, method + " " + path);
  }
  assert.equal(h.calls.length, 0);
});

test("account-link routes are not exposed through other workspace channels", async () => {
  const h = await harness(); h.product.mountWorkspace();
  for (const owner of [h.host, h.relationships, h.persona]) {
    await h.request(owner, "GET", "acquaintance/account-links");
    assert.equal(owner.messages.at(-1).ok, false);
    await h.request(owner, "POST", "acquaintance/account-links/unlink");
    assert.equal(owner.messages.at(-1).ok, false);
  }
  assert.equal(h.calls.length, 0);
});

test("QQ Official keeps its numeric account label for either transport", async () => {
  const h = await harness();
  for (const platform_name of ["qq_official", "qq_official_webhook"]) {
    assert.equal(h.product.accountLabel({platform_name, account_id: "123"}), "QQ 123");
  }
});

test("Telegram account labels follow the adapter family for numeric and named instances", async () => {
  const h = await harness();
  for (const account_id of ["12345", "private-bot"]) {
    const row = {platform_id:"default", platform_name:"telegram", account_id};
    assert.equal(h.product.accountLabel(row), "Telegram " + account_id);
    assert.equal(h.product.platformLabel(row), "Telegram");
  }
});
