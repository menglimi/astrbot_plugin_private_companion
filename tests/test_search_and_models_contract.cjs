"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const PAGE_DIR = path.join(__dirname, "..", "pages", "陪伴小屋");
const readPage = name => fs.readFileSync(path.join(PAGE_DIR, name), "utf8");
const HAS_CJK = /[\u3400-\u9fff]/;

function createNavAndSearchContext(overrides = {}) {
  const location = {hash: ""};
  const writes = [];
  const renders = [];
  const toasts = [];
  const targets = new Map();
  const elementsById = new Map();
  const history = {
    pushState(_s, _t, hash) { writes.push({method: "pushState", hash}); location.hash = hash; },
    replaceState(_s, _t, hash) { writes.push({method: "replaceState", hash}); location.hash = hash; },
  };
  const toastNode = {textContent: "", classList: {add() {}, remove() {}}};
  const dialog = {open: false, showModal() { this.open = true; }, close() { this.open = false; }};
  const document = {
    body: {classList: {contains: () => false, add() {}, remove() {}, toggle() {}}},
    documentElement: {dataset: {}},
    addEventListener() {},
    querySelector(selector) {
      if (selector === "#toast") return toastNode;
      if (selector === "#detailDialog") return dialog;
      return targets.get(selector) || null;
    },
    querySelectorAll() { return []; },
    getElementById(id) { return elementsById.get(id) || null; },
  };
  const window = {
    location,
    history,
    addEventListener() {},
    scrollTo() {},
    matchMedia: () => ({matches: false}),
    CompanionProduct: {
      scopeKey: () => "synthetic",
      role: () => ({role_id: "role-1", reply_api: "/astrbot_plugin_private_companion/targets/t1/roles/role-1/reply", config_api: "/astrbot_plugin_private_companion/targets/t1/roles/role-1/config"}),
      target: () => ({target_id: "t1", scope_kind: "group"}),
      roleTarget: () => ({target_id: "t1", scope_kind: "private", bot_id: "b1"}),
      roleLabel: () => "角色",
      accountLabel: () => "账号",
      connection: () => ({state: "connected", label: "已连接"}),
      manifest: {extensions: [], providers: []},
      extension: () => null,
      provider: () => null,
      capability: () => null,
      workspace: () => '<section class="workspace-stub"></section>',
      statusLabel: () => "已连接",
      ...(overrides.product || {}),
    },
    ...(overrides.window || {}),
  };
  const context = vm.createContext({
    window,
    document,
    location,
    history,
    console,
    structuredClone,
    setTimeout,
    clearTimeout,
    requestAnimationFrame: cb => cb(),
    matchMedia: () => ({matches: false}),
    localStorage: {getItem: () => null, setItem() {}, removeItem() {}},
  });
  vm.runInContext(readPage("shell.js"), context, {filename: "shell.js"});
  vm.runInContext(readPage("page-guards.js"), context, {filename: "page-guards.js"});
  for (const name of [
    "config-editor.js",
    "reply-page.js",
    "host-options-page.js",
    "state-maintenance.js",
    "memory-settings-section.js",
    "perception-native-page.js",
    "contact-page.js",
  ]) {
    vm.runInContext(readPage(name), context, {filename: name});
  }
  const appSrc = readPage("app.js");
  vm.runInContext(appSrc.slice(0, appSrc.indexOf("document.addEventListener('click'")), context, {filename: "app.js"});
  vm.runInContext(`
    render = () => {
      if (route === 'manage') window.CompanionShell.selectSection(manageTab);
      window.__renders.push(currentNavigationHref());
    };
    renderShell = () => window.__renders.push(currentNavigationHref());
    toast = message => { window.__toasts.push(message); };
  `, context);
  window.__renders = renders;
  window.__toasts = toasts;
  return {
    context,
    window,
    document,
    location,
    writes,
    renders,
    toasts,
    targets,
    elementsById,
    run: code => vm.runInContext(code, context),
  };
}

function collectKnownDomIdsAndClasses(ctx) {
  const chunks = [
    ctx.run("renderPerceptionOriginal()"),
    ctx.run("renderOutputOriginal()"),
    ctx.run("renderExpressionOriginal()"),
    ctx.run("renderDecisionOriginal()"),
    ctx.run("['profile','relationship','knowledge'].map(p => { selfPane = p; return renderGovernanceOriginal(); }).join('\\n')"),
    ctx.run("renderModelsPage()"),
    ctx.run("renderContactPage()"),
    ctx.run("renderPhoneUsage()"),
    ctx.run("renderMobileConnection()"),
    ctx.run("renderIdentityAccessPage()"),
    ctx.run("renderUnderstanding()"),
    ctx.run("renderDecision()"),
    ctx.run("['overview','strategy','behavior'].map(t => { learningTab = t; return renderLearningPage(); }).join('\\n')"),
    ctx.run("qzoneConfigMarkup()"),
    ctx.run("(() => { memoryWorkspaceRoute = 'settings'; const h = renderMemoryPage(); memoryWorkspaceRoute = 'records'; return h; })()"),
    ctx.run("renderViewPanel('diary')"),
    ctx.run("renderViewPanel('state')"),
    readPage("decision-page.js"),
    readPage("understanding-page.js"),
    readPage("learning-page.js"),
  ];
  const combined = chunks.join("\n");
  const ids = new Set();
  for (const match of combined.matchAll(/\bid="([^"]+)"/g)) ids.add(match[1]);
  for (const match of combined.matchAll(/\bid='([^']+)'/g)) ids.add(match[1]);
  const classes = new Set();
  for (const match of combined.matchAll(/\bclass="([^"]+)"/g)) {
    for (const token of match[1].split(/\s+/)) if (token) classes.add(token);
  }
  // Identity settings dialogs mount dynamic config editor IDs from identitySettingsSpecs;
  // legacy fallback roots (#personConfigWorkspace, #privateAccessWorkspace, #memberClueWorkspace) also exist when detailDialog is absent.
  for (const id of [
    "identityPersonSettingsWorkspace",
    "identityPrivateAccessSettingsWorkspace",
    "identityStrangerAccessSettingsWorkspace",
    "identityMemberClueSettingsWorkspace",
    "personConfigWorkspace",
    "privateAccessWorkspace",
    "memberClueWorkspace",
  ]) ids.add(id);
  return {ids, classes, perceptionHtml: chunks[0], outputHtml: chunks[1], expressionHtml: chunks[2], governanceHtml: chunks[4]};
}

test("Contract: ALL config search entries resolve to valid routes, subtabs, DOM containers and Chinese labels", () => {
  const h = createNavAndSearchContext();
  const dom = collectKnownDomIdsAndClasses(h);
  const appSrc = readPage("app.js");
  const pageLabelsMatch = appSrc.match(/const pageLabels=(\{[^}]+\});/);
  assert.ok(pageLabelsMatch, "pageLabels must exist in app.js");
  const pageLabels = vm.runInNewContext(`(${pageLabelsMatch[1]})`);

  const providers = [
    h.window.CompanionConfigEditors,
    h.window.CompanionReplyPage,
    h.window.CompanionHostOptionsPage,
    h.window.CompanionStateMaintenance,
    h.window.CompanionMemorySettingsSection,
    h.window.CompanionNativePerceptionPage,
  ];
  const validOutputTabs = new Set(["visual", "capabilities", "gallery", "materials", "creation"]);
  const validExpressionModules = new Set(["expression-text", "expression-voice", "expression-reaction", "expression-platform", "expression-group"]);
  const validDecisionViews = new Set(["overview", "response", "tempo", "safety", "guard", "group"]);
  const validUnderstandingTabs = new Set(["overview", "image", "video", "file", "web", "forwards"]);
  const validContactViews = new Set(["dial", "morning", "tests", "purposes", "gates", "budgets", "sources", "materials"]);
  const validModelsTabs = new Set(["fn", "voice", "image", "search", "laya", "pricing"]);
  const validSelfSections = new Set(h.run("SELF_PROFILE_SECTIONS.map(([id]) => id)"));

  let total = 0;
  const contactViewsSeen = new Set();
  const stateKindsSeen = new Set();
  const perceptionTabsSeen = new Set();
  const outputTabsSeen = new Set();
  const expressionModulesSeen = new Set();

  for (const provider of providers) {
    for (const entry of provider.searchEntries()) {
      total += 1;
      const loc = h.run(`resolveSettingTarget(${JSON.stringify(entry)})`);
      assert.ok(loc, `Entry ${entry.group}/${entry.section}/${entry.field} missing route target`);
      assert.ok(["life", "manage"].includes(loc.next), `Invalid route next=${loc.next} for ${entry.group}.${entry.field}`);

      const subtab = loc.modelsTab || loc.subtab || "";
      const dest = h.run(`navigationDestination(${JSON.stringify(loc.next)}, ${JSON.stringify(loc.tab)}, ${JSON.stringify(subtab)})`);
      assert.equal(dest.next, loc.next, `Route next fallback for ${entry.group}.${entry.field}`);
      assert.equal(dest.tab, loc.tab, `Route tab fallback for ${entry.group}.${entry.field}: ${loc.tab} -> ${dest.tab}`);

      assert.ok(pageLabels[loc.tab], `pageLabels missing tab=${loc.tab}`);
      assert.ok(HAS_CJK.test(pageLabels[loc.tab]), `pageLabels[${loc.tab}] must be Chinese`);

      if (loc.outputTab) {
        assert.ok(validOutputTabs.has(loc.outputTab), `Invalid outputTab=${loc.outputTab} for ${entry.group}.${entry.field}`);
        assert.ok(dom.outputHtml.includes(`id="output-panel-${loc.outputTab}"`), `Missing output panel ${loc.outputTab}`);
        outputTabsSeen.add(loc.outputTab);
      }
      if (loc.perceptionTab) {
        const [panelId, subpanelId = ""] = String(loc.perceptionTab).split(":", 2);
        assert.ok(dom.perceptionHtml.includes(`id="${panelId}"`), `Invalid perception panel id="${panelId}" for ${entry.group}.${entry.field}`);
        if (subpanelId) {
          assert.ok(dom.perceptionHtml.includes(`id="${subpanelId}"`), `Invalid perception subpanel id="${subpanelId}" for ${entry.group}.${entry.field}`);
        }
        perceptionTabsSeen.add(loc.perceptionTab);
      }
      if (loc.expressionModule) {
        assert.ok(validExpressionModules.has(loc.expressionModule), `Invalid expressionModule=${loc.expressionModule} for ${entry.group}.${entry.field}`);
        assert.ok(dom.expressionHtml.includes(`id="expression-panel-${loc.expressionModule}"`), `Missing expression panel ${loc.expressionModule}`);
        expressionModulesSeen.add(loc.expressionModule);
      }
      if (loc.decisionView) {
        assert.ok(validDecisionViews.has(loc.decisionView), `Invalid decisionView=${loc.decisionView} for ${entry.group}.${entry.field}`);
      }
      if (loc.understandingTab) {
        assert.ok(validUnderstandingTabs.has(loc.understandingTab), `Invalid understandingTab=${loc.understandingTab} for ${entry.group}.${entry.field}`);
      }
      if (loc.contactView) {
        assert.ok(validContactViews.has(loc.contactView), `Invalid contactView=${loc.contactView} for ${entry.group}.${entry.field}`);
        contactViewsSeen.add(loc.contactView);
      }
      if (loc.modelsTab) {
        assert.ok(validModelsTabs.has(loc.modelsTab), `Invalid modelsTab=${loc.modelsTab} for ${entry.group}.${entry.field}`);
      }
      if (loc.selfSection) {
        assert.ok(validSelfSections.has(loc.selfSection), `Invalid selfSection=${loc.selfSection} for ${entry.group}.${entry.field}`);
        assert.ok(dom.governanceHtml.includes(`id="self-chapter-tab-${loc.selfSection}"`), `Missing self chapter tab ${loc.selfSection}`);
      }
      if (loc.stateKind) stateKindsSeen.add(loc.stateKind);
      if (loc.root) {
        assert.notEqual(loc.root, "#contactPreferencesWorkspace", "Dead V1 selector #contactPreferencesWorkspace must not be used");
        if (loc.root.startsWith("#")) {
          assert.ok(dom.ids.has(loc.root.slice(1)), `Target root ${loc.root} not found in rendered DOM for ${entry.group}.${entry.field}`);
        } else if (loc.root.startsWith(".")) {
          assert.ok(dom.classes.has(loc.root.slice(1)), `Target class ${loc.root} not found in rendered DOM for ${entry.group}.${entry.field}`);
        }
      }
    }
  }

  assert.ok(total >= 795, `Expected at least 795 searchable config entries, got ${total}`);
  for (const v of ["dial", "morning", "purposes", "gates", "budgets", "sources"]) {
    assert.ok(contactViewsSeen.has(v), `Expected proactive.contact to map into contactView=${v}`);
  }
  assert.equal(stateKindsSeen.size, 12, "Expected all 12 role.state sections");
  assert.ok(perceptionTabsSeen.has("config-environment:perception-detail-calendar"));
  assert.ok(perceptionTabsSeen.has("config-models:perception-detail-context"));
  assert.ok(perceptionTabsSeen.has("config-recall:perception-detail-recalls"));
  assert.ok(perceptionTabsSeen.has("config-screen:perception-detail-screen"));
  for (const ot of ["visual", "capabilities", "materials", "creation"]) {
    assert.ok(outputTabsSeen.has(ot), `Expected output settings to target outputTab=${ot}`);
  }
  for (const em of ["expression-text", "expression-voice", "expression-reaction", "expression-platform", "expression-group"]) {
    assert.ok(expressionModulesSeen.has(em), `Expected expression settings to target ${em}`);
  }

  // Spot-check specific regressions from audit #22:
  const pokeFields = new Set(h.window.CompanionHostOptionsPage.searchEntries().filter(e => e.group === "poke").map(e => e.field));
  assert.ok(pokeFields.has("context_reply_enabled"), "poke must index context_reply_enabled");
  assert.ok(pokeFields.has("context_delay_seconds"), "poke must index context_delay_seconds");

  const relLoc = h.run(`resolveSettingTarget({group:"relationship_profile",section:"person",field:"default_nickname"})`);
  assert.equal(relLoc.fieldAttr, "data-self-relationship-field");
  assert.equal(relLoc.focusSection, "");

  const nativeImageLoc = h.run(`resolveSettingTarget({group:"conversation.presentation",section:"native_image",field:"astrbot_animated_image_understanding_enabled"})`);
  assert.equal(nativeImageLoc.tab, "understanding");
  assert.equal(nativeImageLoc.understandingTab, "image");

  const sceneLoc = h.run(`resolveSettingTarget({group:"image.output",section:"scenes",field:"scene_presets_json"})`);
  assert.equal(sceneLoc.outputTab, "capabilities");
  assert.equal(sceneLoc.root, ".output-scene-presets");

  const interjectLoc = h.run(`resolveSettingTarget({group:"group_participation",section:"interjection",field:"enable_group_interjection"})`);
  assert.equal(interjectLoc.scopeRequirement, "group");
  assert.ok(interjectLoc.scopeHint);

  const replyEntries = h.window.CompanionReplyPage.searchEntries();
  const replyFields = new Set(replyEntries.filter(e => e.group === "conversation.presentation").map(e => e.field));
  assert.ok(replyFields.has("private_tool_profile"), "conversation.presentation must index private_tool_profile");
  assert.ok(replyFields.has("private_tool_groups"), "conversation.presentation must index private_tool_groups");
  for (const field of ["private_tool_profile", "private_tool_groups"]) {
    const loc = h.run(`resolveSettingTarget({group:"conversation.presentation",section:"wakeup_direct",field:${JSON.stringify(field)}})`);
    assert.equal(loc.next, "manage");
    assert.equal(loc.tab, "decision");
    assert.equal(loc.decisionView, "response");
    assert.equal(loc.root, "#decisionReplyWorkspace");
  }
});

test("Contract: searchIndex has zero dead links, zero duplicates, valid destinations, and covers all core pages & subtabs", () => {
  const h = createNavAndSearchContext();
  const dom = collectKnownDomIdsAndClasses(h);
  const index = h.run("searchIndex");
  const titles = new Set();
  const validHomeSections = new Set(["overview", "observation", "furniture"]);
  const validOutputTabs = new Set(["visual", "capabilities", "gallery", "materials", "creation"]);
  const validModelsTabs = new Set(["fn", "voice", "image", "search", "laya", "pricing"]);

  for (const entry of index) {
    const [title, desc, icon, kind, tab, opts = {}] = entry;
    assert.ok(title && HAS_CJK.test(title), `Page title must be non-empty Chinese: ${title}`);
    assert.ok(desc && desc.length > 0, `Page description missing for ${title}`);
    assert.ok(icon && typeof icon === "string", `Icon missing for ${title}`);
    assert.ok(!titles.has(title), `Duplicate searchIndex title: ${title}`);
    titles.add(title);

    // No dead V1 links
    assert.notEqual(tab, "notes", "Dead V1 link notes must be removed");
    assert.notEqual(tab, "photo-window", "Dead V1 link photo-window must be removed");
    assert.notEqual(title, "便签");
    assert.notEqual(title, "窗边的一页");

    if (kind === "home-section") {
      assert.ok(validHomeSections.has(tab), `Invalid home-section=${tab} for ${title}`);
    } else if (kind === "life" || kind === "manage") {
      const subtab = opts.modelsTab || opts.subtab || "";
      const dest = h.run(`navigationDestination(${JSON.stringify(kind)}, ${JSON.stringify(tab)}, ${JSON.stringify(subtab)})`);
      assert.equal(dest.next, kind, `searchIndex ${title} fell back next`);
      assert.equal(dest.tab, tab, `searchIndex ${title} fell back tab (${tab} -> ${dest.tab})`);
      if (subtab) assert.equal(dest.subtab, subtab, `searchIndex ${title} fell back subtab (${subtab} -> ${dest.subtab})`);
      if (opts.outputTab) {
        assert.ok(validOutputTabs.has(opts.outputTab), `Invalid outputTab in searchIndex ${title}`);
      }
      if (opts.modelsTab) {
        assert.ok(validModelsTabs.has(opts.modelsTab), `Invalid modelsTab in searchIndex ${title}`);
      }
      if (opts.perceptionTab) {
        const [panelId] = String(opts.perceptionTab).split(":", 2);
        assert.ok(dom.perceptionHtml.includes(`id="${panelId}"`), `Invalid perceptionTab in searchIndex ${title}`);
      }
    } else {
      assert.fail(`Unexpected null/unknown routeKind in searchIndex for ${title}: ${kind}/${tab}`);
    }
  }

  for (const required of [
    "计价与汇率", "产出图库", "参考素材与衣柜", "创作与作品",
    "每日巡视", "回复诊断", "理解", "学习与反思",
    "状态与日程", "账号与会话", "资料与维护", "备份与还原",
    "旧版资料迁入", "配置导入导出", "手机端连接", "全部功能",
    "关系与情感", "语音模型", "生图模型", "整理策略", "治理与维护",
  ]) {
    assert.ok(titles.has(required), `Missing required searchIndex entry: ${required}`);
  }
});

test("Req 7: first-class #manage/models/<tab> routing works end-to-end without sessionStorage", () => {
  const h = createNavAndSearchContext();

  assert.equal(h.run("navHref('manage','models','fn')"), "#manage/models");
  assert.equal(h.run("navHref('manage','models','pricing')"), "#manage/models/pricing");
  assert.equal(h.run("navHref('manage','models','voice')"), "#manage/models/voice");
  assert.equal(h.run("navHref('manage','models','image')"), "#manage/models/image");
  assert.equal(h.run("navHref('manage','models','search')"), "#manage/models/search");
  assert.equal(h.run("navHref('manage','models','laya')"), "#manage/models/laya");
  assert.equal(h.run("navHref('manage','models','invalid')"), "#manage/models");

  h.run("navigate('manage','models','pricing')");
  assert.equal(h.location.hash, "#manage/models/pricing");
  assert.equal(h.run("modelsPane"), "pricing");
  assert.equal(h.run("currentNavigationHref()"), "#manage/models/pricing");

  h.run("onModelsTabChange('voice')");
  assert.equal(h.location.hash, "#manage/models/voice");
  assert.equal(h.run("modelsPane"), "voice");

  h.run("onModelsTabChange('fn')");
  assert.equal(h.location.hash, "#manage/models");
  assert.equal(h.run("modelsPane"), "fn");

  // Direct hash visit restores subtab cleanly
  h.location.hash = "#manage/models/laya";
  h.run("syncFromHash()");
  assert.equal(h.run("manageTab"), "models");
  assert.equal(h.run("modelsPane"), "laya");

  // Host options links use first-class #manage/models/voice
  const hostOptionsSrc = readPage("host-options-page.js");
  assert.match(hostOptionsSrc, /href="#manage\/models\/voice"/);
  assert.doesNotMatch(hostOptionsSrc, /href="#manage\/models"[^>]*发音人与音色/);

  // Maintenance pricing button uses first-class route attributes without sessionStorage
  const maintSrc = readPage("maintenance-page.js");
  assert.match(maintSrc, /data-open-pricing-settings\s+data-go="manage"\s+data-manage="models"\s+data-manage-subtab="pricing"/);
  assert.doesNotMatch(maintSrc, /MODELS_PENDING_TAB_KEY/);
});

test("Req 7: models-page mount(initialTab) & switching back to fn works when sessionStorage throws SecurityError", () => {
  const requests = [];
  const tabChanges = [];
  const root = {
    isConnected: true,
    innerHTML: "",
    listeners: {},
    addEventListener(type, fn) { this.listeners[type] = fn; },
    removeEventListener(type) { delete this.listeners[type]; },
    querySelectorAll() { return []; },
  };
  const blockedStorage = {
    getItem() { throw new Error("SecurityError: sandboxed iframe"); },
    setItem() { throw new Error("SecurityError: sandboxed iframe"); },
    removeItem() { throw new Error("SecurityError: sandboxed iframe"); },
  };
  const ctx = vm.createContext({
    window: {},
    document: {querySelectorAll: () => []},
    console,
    setTimeout,
    clearTimeout,
    sessionStorage: blockedStorage,
    localStorage: blockedStorage,
    URL,
  });
  ctx.window = ctx;
  ctx.CompanionWorkbench = {
    scopeHintClick: () => false,
    scopeHintReset() {},
    scopeHintKeydown() {},
    dismissScopeHints() {},
    scopeObjectForEndpoint: () => ({}),
    saveScopeLabel: () => "当前角色",
    scopeHintMarkup: () => "",
  };
  const fakeBridge = {
    apiGet: (route, body) => {
      requests.push({method: "GET", route, body});
      return Promise.resolve({status: "ok", data: {tasks: [], providers: [], pricing: {}}});
    },
    apiPost: (route, body) => {
      requests.push({method: "POST", route, body});
      return Promise.resolve({status: "ok", data: {}});
    },
  };
  ctx.CompanionProduct = {
    hosted: () => true,
    identity: () => ({installation_id: "inst", operator_id: "op"}),
    role: () => ({role_id: "r1", model_api: "/astrbot_plugin_private_companion/targets/t1/roles/r1/model"}),
    roleTarget: () => ({scope_kind: "private", roles: [{role_id: "r1", model_api: "/astrbot_plugin_private_companion/targets/t1/roles/r1/model"}]}),
    selection: () => ({target_id: "t1", role_id: "r1"}),
    bridgeNow: () => fakeBridge,
    bridgeFor: () => Promise.resolve(fakeBridge),
  };
  vm.runInContext(readPage("pricing-workbench.js"), ctx, {filename: "pricing-workbench.js"});
  vm.runInContext(readPage("models-page.js"), ctx, {filename: "models-page.js"});

  ctx.CompanionModelsPage.mount(root, {initialTab: "pricing", onTabChange: tab => tabChanges.push(tab)});
  assert.match(root.innerHTML, /data-model-tab="pricing"[^>]*aria-selected="true"/);
  assert.ok(requests.some(r => r.route.endsWith("pricing")), "Mounting with initialTab=pricing should immediately load pricing");
  assert.ok(!requests.some(r => r.route.endsWith("/model")), "Mounting directly on pricing should not load fn tasks yet");

  // Switching back to fn tab must fire onTabChange AND trigger fn refresh!
  ctx.CompanionModelsPage.selectTab("fn");
  assert.deepEqual(tabChanges, ["fn"]);
  assert.match(root.innerHTML, /data-model-tab="fn"[^>]*aria-selected="true"/);
  assert.ok(requests.some(r => r.route.endsWith("/model")), "Switching from pricing back to fn must trigger fn refresh()");
  ctx.CompanionModelsPage.unmount();
});

test("Req 20: openSearchSetting awaits dirty-draft confirmation, syncs selfSection, expands nested details, and warns on private scope for group-only settings", async () => {
  let resolveConfirm;
  let dirty = true;
  const selfSectionsSelected = [];
  const h = createNavAndSearchContext({
    product: {
      target: () => ({target_id: "t1", scope_kind: "private"}),
    },
  });
  h.window.CompanionWorkbench = {
    dirtyEditors: () => dirty ? [{title: "未保存配置", fields: ["字段A"], discard: () => { dirty = false; return true; }}] : [],
  };
  h.window.CompanionConfirm = () => new Promise(resolve => { resolveConfirm = resolve; });
  h.run("selectSelfProfileSection = sec => window.__selfSections.push(sec);");
  h.window.__selfSections = selfSectionsSelected;

  // 1) Cancel dirty-draft confirmation -> must NOT switch selfSection or focus
  const profileEntry = {group: "role_profile", section: "boundaries", field: "taboos"};
  const canceledPromise = h.run(`openSearchSetting({entry: ${JSON.stringify(profileEntry)}})`);
  assert.equal(selfSectionsSelected.length, 0);
  resolveConfirm(false);
  assert.equal(await canceledPromise, false);
  assert.equal(selfSectionsSelected.length, 0);

  // 2) Confirm dirty-draft confirmation -> navigates, calls selectSelfProfileSection, expands nested <details> and highlights
  const outerDetails = {tagName: "DETAILS", open: false, parentElement: null};
  const innerDetails = {tagName: "DETAILS", open: false, parentElement: outerDetails};
  const classes = new Set();
  const field = {
    dataset: {wbField: "taboos"},
    parentElement: innerDetails,
    closest: sel => sel === "details" ? innerDetails : null,
    matches: () => true,
    classList: {add: c => classes.add(c), remove: c => classes.delete(c)},
    scrollIntoView() { this.scrolled = true; },
    focus() { this.focused = true; },
  };
  h.targets.set("#roleProfileConfigWorkspace", {
    parentElement: innerDetails,
    closest: sel => sel === "details" ? innerDetails : null,
    querySelectorAll: sel => sel === "[data-wb-section]" || sel === "[data-wb-group]" ? [] : [field],
  });
  const acceptedPromise = h.run(`openSearchSetting({entry: ${JSON.stringify(profileEntry)}})`);
  dirty = false;
  resolveConfirm(true);
  assert.equal(await acceptedPromise, true);
  assert.deepEqual(selfSectionsSelected, ["boundaries"]);
  assert.equal(innerDetails.open, true);
  assert.equal(outerDetails.open, true);
  assert.equal(field.focused, true);
  assert.equal(field.scrolled, true);
  assert.ok(classes.has("is-search-target"), "Focused search target must receive .is-search-target highlight");

  // 3) Private scope searching group-only interjection -> navigates to decision and shows toast hint
  const interjectEntry = {group: "group_participation", section: "interjection", field: "enable_group_interjection"};
  const ok = await h.run(`openSearchSetting({entry: ${JSON.stringify(interjectEntry)}})`);
  assert.equal(ok, true);
  assert.equal(h.location.hash, "#manage/decision");
  assert.ok(h.toasts.some(msg => msg.includes("群聊专属")), "Private scope must toast group-only hint");

  // 4) Reduced motion & .is-search-target pulse CSS contract
  const motionCss = readPage("page-motion.css");
  assert.match(motionCss, /@keyframes search-target-pulse/);
  assert.match(motionCss, /\.is-search-target\s*\{/);
  assert.match(motionCss, /2\.4s/);
  assert.match(motionCss, /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.is-search-target/);
});

test("Req 20: state-page queues openEditor while loading and auto-switches cycle phase & home settings", async () => {
  const flush = () => new Promise(r => setImmediate(r));
  let resolveStateGet;
  const selectedCycleViews = [];
  const selectedPhases = [];
  const openedKinds = [];
  const fieldClasses = new Set();
  const annualDetails = {tagName: "DETAILS", open: false, parentElement: null};
  const lutealInput = {
    parentElement: annualDetails,
    closest: sel => sel === "details" ? annualDetails : null,
    classList: {add: c => fieldClasses.add(c), remove: c => fieldClasses.delete(c)},
    scrollIntoView() { this.scrolled = true; },
    focus() { this.focused = true; },
  };
  const root = {
    isConnected: true,
    innerHTML: "",
    addEventListener() {},
    removeEventListener() {},
    contains: () => false,
    querySelectorAll: () => [],
    querySelector: sel => sel === '[data-state-field="cycle_luteal_mood"]' ? lutealInput : null,
  };
  const ctx = vm.createContext({
    window: {},
    document: {
      hidden: false,
      activeElement: null,
      documentElement: {dataset: {}},
      body: {classList: {contains: () => false, toggle() {}}},
      addEventListener() {},
      querySelector: () => null,
      getElementById: () => null,
    },
    console,
    setTimeout,
    clearTimeout,
    requestAnimationFrame: cb => cb(),
    matchMedia: () => ({matches: false}),
  });
  ctx.window = ctx;
  ctx.CompanionProduct = {
    hosted: () => true,
    scopeKey: () => "scope-1",
    selection: () => ({target_id: "t1", bot_id: "b1", role_id: "r1"}),
    roleTarget: () => ({target_id: "t1", bot_id: "b1", scope_kind: "private"}),
    directory: () => ({
      installation_id: "inst",
      operator_id: "op",
      targets: [{target_id: "t1", bot_id: "b1", scope_kind: "private", roles: [{role_id: "r1", state_api: "/astrbot_plugin_private_companion/targets/t1/roles/r1/state"}]}],
    }),
    bridgeNow: () => ({
      ready: async () => ({pluginName: "astrbot_plugin_private_companion"}),
      apiGet: () => new Promise(resolve => { resolveStateGet = resolve; }),
    }),
  };
  vm.runInContext(readPage("state-maintenance.js"), ctx, {filename: "state-maintenance.js"});
  const origSelectCycleView = ctx.CompanionStateMaintenance.selectCycleView;
  const origSelectPhase = ctx.CompanionStateMaintenance.selectPhase;
  ctx.CompanionStateMaintenance.selectCycleView = view => { selectedCycleViews.push(view); return origSelectCycleView(view); };
  ctx.CompanionStateMaintenance.selectPhase = phase => { selectedPhases.push(phase); return origSelectPhase(phase); };
  ctx.CompanionStateEditor = {
    create: () => ({
      busy: false,
      dirty: false,
      interacting: false,
      setScope() {},
      setStatus() {},
      invalidate() {},
      destroy() {},
      discardIfPristine: () => true,
      markup: () => "",
      open: kind => openedKinds.push(kind),
    }),
  };
  vm.runInContext(readPage("state-page.js"), ctx, {filename: "state-page.js"});

  try {
    const mountPromise = ctx.CompanionStatePage.mount(root, {section: "state"});
    await flush();
    await flush();

    // Queue openEditor BEFORE load() finishes
    assert.equal(ctx.CompanionStatePage.openEditor("cycle", "cycle_luteal_mood"), true);
    assert.equal(openedKinds.length, 0, "Should stay queued until status loads");

    resolveStateGet({
      status: "ok",
      data: {
        state: {revision: 1, values: {}},
        settings: {enabled: true},
        sleep_schedule: {},
        calendar: {items: []},
      },
    });
    await mountPromise;
    await flush();

    assert.deepEqual(openedKinds, ["cycle"]);
    assert.deepEqual(selectedCycleViews, ["expression"]);
    assert.deepEqual(selectedPhases, ["luteal"]);
    assert.equal(annualDetails.open, true);
    assert.equal(lutealInput.focused, true);
    assert.equal(lutealInput.scrolled, true);
    assert.ok(fieldClasses.has("is-search-target"));

    // Maintenance kind remaps to hunger/health
    ctx.CompanionStatePage.openEditor("maintenance", "hunger_enabled");
    assert.equal(openedKinds.at(-1), "hunger");
    ctx.CompanionStatePage.openEditor("maintenance", "health_enabled");
    assert.equal(openedKinds.at(-1), "health");
  } finally {
    ctx.CompanionStatePage.unmount();
  }
});

test("Req 20: contact-page focusField queues while loading, clears purpose filters, and highlights target", async () => {
  const flush = () => new Promise(r => setImmediate(r));
  let resolveConfig;
  const fieldClasses = new Set();
  const fieldEl = {
    parentElement: null,
    closest: () => null,
    matches: () => true,
    classList: {add: c => fieldClasses.add(c), remove: c => fieldClasses.delete(c)},
    scrollIntoView() { this.scrolled = true; },
    focus() { this.focused = true; },
  };
  const root = {
    isConnected: true,
    innerHTML: "",
    addEventListener() {},
    removeEventListener() {},
    querySelectorAll: () => [],
    querySelector: sel => sel === '[data-field="balance_low_threshold"]' ? fieldEl : null,
  };
  const ctx = vm.createContext({
    window: {},
    document: {
      documentElement: {dataset: {}},
      body: {classList: {contains: () => false}},
      addEventListener() {},
      getElementById: () => null,
    },
    console,
    setTimeout,
    clearTimeout,
    requestAnimationFrame: cb => cb(),
    matchMedia: () => ({matches: false}),
    history: {state: null, replaceState() {}},
    sessionStorage: {getItem: () => null, setItem() {}, removeItem() {}},
    localStorage: {getItem: () => null, setItem() {}, removeItem() {}},
  });
  ctx.window = ctx;
  ctx.CompanionProduct = {
    hosted: () => true,
    scopeKey: () => "c-scope",
    identity: () => ({installation_id: "inst", operator_id: "op"}),
    bridgeNow: () => ctx.AstrBotPluginPage,
    bridgeFor: () => Promise.resolve(ctx.AstrBotPluginPage),
  };
  ctx.AstrBotPluginPage = {
    ready: () => Promise.resolve({pluginName: "astrbot_plugin_private_companion"}),
    apiGet: route => route === "config"
      ? new Promise(resolve => { resolveConfig = resolve; })
      : Promise.resolve({status: "ok", data: {}}),
  };
  vm.runInContext(readPage("contact-page.js"), ctx, {filename: "contact-page.js"});

  const mountPromise = ctx.CompanionContactPage.mount(root);
  await flush();
  await flush();

  // Queue focusField while loadContract() is still pending
  assert.equal(ctx.CompanionContactPage.focusField("balance_low_threshold", "balance"), true);
  assert.equal(ctx.CompanionContactPage.state.data.view, "sources");

  resolveConfig({
    status: "ok",
    data: {
      sections: {
        "proactive.contact": {
          scope: "installation",
          edit_token: "tok-1",
          settings: {enabled: true, balance_low_threshold: 10},
        },
      },
    },
  });
  await mountPromise;
  await flush();

  assert.equal(ctx.CompanionContactPage.state.data.view, "sources");
  assert.equal(fieldEl.focused, true);
  assert.equal(fieldEl.scrolled, true);
  assert.ok(fieldClasses.has("is-search-target"));

  // Focusing a purpose clears active purpose filters
  ctx.CompanionContactPage.state.data.purposeQuery = "xyz";
  ctx.CompanionContactPage.state.data.purposeFilter = "off";
  ctx.CompanionContactPage.focusField("affection.enabled", "bond");
  assert.equal(ctx.CompanionContactPage.state.data.view, "purposes");
  assert.equal(ctx.CompanionContactPage.state.data.purposeQuery, "");
  assert.equal(ctx.CompanionContactPage.state.data.purposeFilter, "all");
  ctx.CompanionContactPage.unmount();
});

test("Req 20: relationship_profile, split native perception, multi-group host disambiguation, and openSearchPage subtabs", async () => {
  const h = createNavAndSearchContext();
  const activatedPerception = [];
  const activatedOutput = [];
  h.run("activatePerceptionTab = (dest, opts) => window.__perceptionCalls.push([dest, opts]);");
  h.run("activateOutputTab = (id, opts) => window.__outputCalls.push([id, opts]);");
  h.window.__perceptionCalls = activatedPerception;
  h.window.__outputCalls = activatedOutput;

  // 1) relationship_profile uses data-self-relationship-field directly without waiting for [data-wb-section]
  const relClasses = new Set();
  const relInput = {
    dataset: {selfRelationshipField: "default_nickname"},
    matches: () => true,
    classList: {add: c => relClasses.add(c), remove: c => relClasses.delete(c)},
    scrollIntoView() { this.scrolled = true; },
    focus() { this.focused = true; },
  };
  h.targets.set("#relationshipProfileConfigWorkspace", {
    querySelector: sel => sel === '[data-self-relationship-field="default_nickname"]' ? relInput : null,
    querySelectorAll: () => [],
  });
  await h.run(`openSearchSetting({entry: {group: "relationship_profile", section: "person", sectionCount: 2, field: "default_nickname"}})`);
  assert.equal(h.location.hash, "#manage/governance/relationship");
  assert.equal(relInput.focused, true);
  assert.ok(relClasses.has("is-search-target"));

  // 2) conversation.perception splits calendar (environment) and model (models) with data-native-field
  const calInput = {dataset: {nativeField: "calendar_enabled"}, matches: () => true, scrollIntoView() { this.scrolled = true; }, focus() { this.focused = true; }};
  const modInput = {dataset: {nativeField: "guidance"}, matches: () => true, scrollIntoView() { this.scrolled = true; }, focus() { this.focused = true; }};
  h.targets.set("#nativeCalendarWorkspace", {
    querySelector: sel => sel === '[data-native-field="calendar_enabled"]' ? calInput : null,
    querySelectorAll: () => [],
  });
  h.targets.set("#nativeModelWorkspace", {
    querySelector: sel => sel === '[data-native-field="guidance"]' ? modInput : null,
    querySelectorAll: () => [],
  });
  await h.run(`openSearchSetting({entry: {group: "conversation.perception", section: "environment", sectionCount: 2, field: "calendar_enabled"}})`);
  assert.equal(activatedPerception.at(-1)[0], "config-environment:perception-detail-calendar");
  assert.equal(calInput.focused, true);

  await h.run(`openSearchSetting({entry: {group: "conversation.perception", section: "models", sectionCount: 2, field: "guidance"}})`);
  assert.equal(activatedPerception.at(-1)[0], "config-models:perception-detail-context");
  assert.equal(modInput.focused, true);

  // 3) Multi-group host (#materialControlWorkspace) disambiguates wardrobe.enabled vs photo.reference.enabled
  const wardrobeEnabled = {dataset: {wbField: "enabled"}, matches: () => true, scrollIntoView() {}, focus() { this.focused = true; }};
  const photoEnabled = {dataset: {wbField: "enabled"}, matches: () => true, scrollIntoView() {}, focus() { this.focused = true; }};
  const groupHolders = {
    '[data-config-host="wardrobe"]': {
      querySelectorAll: sel => sel === "[data-wb-group]" ? [{dataset: {wbGroup: "switches"}}] : sel === "[data-wb-field]" ? [wardrobeEnabled] : [],
    },
    '[data-config-host="photo.reference"]': {
      querySelectorAll: sel => sel === "[data-wb-group]" ? [{dataset: {wbGroup: "switches"}}] : sel === "[data-wb-field]" ? [photoEnabled] : [],
    },
  };
  h.targets.set("#materialControlWorkspace", {
    querySelector: sel => groupHolders[sel] || null,
    querySelectorAll: () => [],
  });
  await h.run(`openSearchSetting({entry: {group: "photo.reference", section: "switches", sectionCount: 2, field: "enabled"}})`);
  assert.equal(activatedOutput.at(-1)[0], "materials");
  assert.equal(photoEnabled.focused, true);
  assert.equal(Boolean(wardrobeEnabled.focused), false);

  // 4) openSearchPage routes directly to subtabs (pricing, output gallery, reality perception)
  const pricingPage = h.run(`searchIndex.find(item => item[0] === "计价与汇率")`);
  await h.run(`openSearchPage(${JSON.stringify(pricingPage)})`);
  assert.equal(h.location.hash, "#manage/models/pricing");
  assert.equal(h.run("modelsPane"), "pricing");

  const galleryPage = h.run(`searchIndex.find(item => item[0] === "产出图库")`);
  await h.run(`openSearchPage(${JSON.stringify(galleryPage)})`);
  assert.equal(h.location.hash, "#manage/output");
  assert.equal(activatedOutput.at(-1)[0], "gallery");

  const realityPage = h.run(`searchIndex.find(item => item[0] === "现实触及")`);
  await h.run(`openSearchPage(${JSON.stringify(realityPage)})`);
  assert.equal(h.location.hash, "#manage/perception");
  assert.equal(activatedPerception.at(-1)[0], "config-screen");
});

test("Modal UX: state-home-settings and detail-dialog-settings enforce three-tier flex layout with overflow:clip and zero bottom dead space", async () => {
  const stateConfigCss = readPage("state-config.css");
  const stylesCss = readPage("styles.css");
  const statePageSrc = readPage("state-page.js");
  const stateEditorSrc = readPage("state-editor.js");

  // 1) Outer dialog uses flex column + overflow:clip + var(--scrim) backdrop + 0 padding
  assert.match(stateConfigCss, /\.state-home-settings\{[^}]*max-height:min\(86dvh,780px\)[^}]*padding:0[^}]*overflow:hidden;overflow:clip/);
  assert.match(stateConfigCss, /\.state-home-settings\[open\]\{display:flex;flex-direction:column\}/);
  assert.match(stateConfigCss, /\.state-home-settings::backdrop\{background:var\(--scrim\)/);

  // 2) Fixed Header with balanced padding & rounded ghost close button
  assert.match(stateConfigCss, /\.state-home-settings\.state-config-page>header\{display:flex;align-items:center;justify-content:space-between;flex:0 0 auto;[^}]*padding:var\(--sp-4\) var\(--sp-5\);border-bottom:1px solid var\(--t-line\);background:var\(--t-surface-soft\)\}/);
  assert.match(statePageSrc, /data-state-close-home-settings\s+aria-label="关闭"/);

  // 3) Dedicated scrollable body .state-config-body wrapping grouped fieldset + flush pinned footer
  assert.match(stateEditorSrc, /class="state-config-body"/);
  assert.match(stateConfigCss, /\.state-home-settings \.state-config-body\{flex:1 1 auto;min-height:0;display:flex;flex-direction:column;[^}]*overflow-y:auto;/);
  assert.match(stateConfigCss, /\.state-home-settings \.state-config-actions\{position:static;flex:0 0 auto;display:flex;align-items:center;justify-content:flex-end;[^}]*margin:0;padding:var\(--sp-3\) var\(--sp-5\);border-top:1px solid var\(--t-line\);background:var\(--t-surface-soft\)\}/);

  // 4) Narrow-screen specificity: .state-home-settings.state-config-page overrides .state-config-page narrow rule
  const narrowGenericIdx = stateConfigCss.indexOf(".state-config-page{padding:var(--sp-3);margin:var(--sp-3) 0}");
  const narrowHomeIdx = stateConfigCss.indexOf(".state-home-settings.state-config-page{width:calc(100vw - 24px);max-height:90dvh;margin:auto;padding:0}");
  assert.ok(narrowGenericIdx > 0 && narrowHomeIdx > narrowGenericIdx,
    "Mobile .state-home-settings.state-config-page override must come after generic .state-config-page mobile padding");

  // 5) Matching three-tier flex structure for .detail-dialog.detail-dialog-settings
  assert.match(stylesCss, /\.detail-dialog\.detail-dialog-settings\{[^}]*overflow:hidden;overflow:clip\}/);
  assert.match(stylesCss, /\.detail-dialog\.detail-dialog-settings\[open\]\{display:flex;flex-direction:column\}/);
  assert.match(stylesCss, /\.detail-dialog\.detail-dialog-settings \.dialog-head\{flex:0 0 auto;position:static;align-items:center;/);
  assert.match(stylesCss, /\.detail-dialog\.detail-dialog-settings \.dialog-body\{flex:1 1 auto;min-height:0;overflow-y:auto;/);

  // 6) Runtime check: openEditor("home", "maintenance_minutes") scrolls .state-config-body without shifting dialog.scrollTop
  const flush = () => new Promise(r => setImmediate(r));
  let bodyScrolledTo = null;
  const fakeDialog = {
    open: false,
    scrollTop: 48,
    showModal() { this.open = true; },
    close() { this.open = false; },
    addEventListener() {},
    querySelector: sel => sel === ".state-config-body" ? fakeScrollBody : null,
  };
  const fakeScrollBody = {
    scrollTop: 0,
    getBoundingClientRect: () => ({top: 100, height: 400}),
    scrollTo(opts) { bodyScrolledTo = opts; this.scrollTop = opts.top; },
  };
  const targetInput = {
    closest: sel => sel === "dialog" ? fakeDialog : sel.includes(".state-config-body") ? fakeScrollBody : null,
    getBoundingClientRect: () => ({top: 460, height: 40}),
    classList: {add() {}, remove() {}},
    focus() { this.focused = true; },
    scrollIntoView() { this.usedFallbackScrollIntoView = true; },
  };
  const root = {
    isConnected: true,
    innerHTML: "",
    addEventListener() {},
    removeEventListener() {},
    contains: () => false,
    querySelectorAll: () => [],
    querySelector: sel => sel === "#state-home-settings" ? fakeDialog
      : sel === '[data-state-field="maintenance_minutes"]' ? targetInput : null,
  };
  const ctx = vm.createContext({
    window: {},
    document: {
      hidden: false, activeElement: null,
      documentElement: {dataset: {}},
      body: {classList: {contains: () => false, toggle() {}}},
      addEventListener() {}, querySelector: () => null, getElementById: () => null,
    },
    console, setTimeout, clearTimeout, requestAnimationFrame: cb => cb(), matchMedia: () => ({matches: false}),
  });
  ctx.window = ctx;
  ctx.CompanionProduct = {
    hosted: () => true, scopeKey: () => "scope-modal",
    selection: () => ({target_id: "t1", bot_id: "b1", role_id: "r1"}),
    roleTarget: () => ({target_id: "t1", bot_id: "b1", scope_kind: "private"}),
    directory: () => ({
      installation_id: "inst", operator_id: "op",
      targets: [{target_id: "t1", bot_id: "b1", scope_kind: "private", roles: [{role_id: "r1", state_api: "/astrbot_plugin_private_companion/targets/t1/roles/r1/state"}]}],
    }),
    bridgeNow: () => ({
      ready: async () => ({pluginName: "astrbot_plugin_private_companion"}),
      apiGet: async () => ({status: "ok", data: {state: {revision: 1, values: {}}, settings: {enabled: true}, sleep_schedule: {}, calendar: {items: []}}}),
    }),
  };
  vm.runInContext(readPage("state-maintenance.js"), ctx, {filename: "state-maintenance.js"});
  ctx.CompanionStateEditor = {
    create: () => ({
      busy: false, dirty: false, interacting: false,
      setScope() {}, setStatus() {}, invalidate() {}, destroy() {},
      discardIfPristine: () => true, markup: () => "", open() {},
    }),
  };
  vm.runInContext(readPage("state-page.js"), ctx, {filename: "state-page.js"});
  try {
    await ctx.CompanionStatePage.mount(root, {section: "state"});
    await flush();
    assert.equal(ctx.CompanionStatePage.openEditor("home", "maintenance_minutes"), true);
    assert.equal(fakeDialog.open, true);
    assert.equal(fakeDialog.scrollTop, 0, "Outer dialog.scrollTop must stay pinned at 0");
    assert.ok(bodyScrolledTo && bodyScrolledTo.top > 0, "Inner .state-config-body must scroll to center the field");
    assert.equal(targetInput.focused, true);
    assert.equal(Boolean(targetInput.usedFallbackScrollIntoView), false, "Should scroll inner body directly instead of scrollIntoView on dialog");
  } finally {
    ctx.CompanionStatePage.unmount();
  }
});
