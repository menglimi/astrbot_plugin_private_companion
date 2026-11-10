"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const page = name => fs.readFileSync(path.join(__dirname, "../pages/陪伴小屋", name), "utf8");

function harness() {
  const location = {hash: ""}, writes = [], renders = [], scrolls = [], anchors = new Map(), targets = new Map();
  const manifest = {extensions: [], providers: []};
  const history = Object.fromEntries(["pushState", "replaceState"].map(method => [method,
    (_state, _title, hash) => { writes.push({method, hash}); location.hash = hash; }]));
  const dialog = {open: false, showModal() {this.open = true;}, close() {this.open = false;}};
  const nativeMounts = [], nativeUnmounts = [];
  const dialogContent = {innerHTML: "", querySelector(selector) {
    if (selector !== "[data-native-extension-host]") return null;
    const match = this.innerHTML.match(/<div data-native-extension-host="([^"]+)"/);
    return match ? {dataset: {nativeExtensionHost: match[1]}} : null;
  }};
  const outputTab = {dataset: {outputTab: "creation"}, attributes: {}, tabIndex: -1,
    setAttribute(name, value) {this.attributes[name] = value;}, focus() {this.focused = true;}};
  const outputPanel = {hidden: true};
  const document = {
    documentElement: {dataset: {}},
    addEventListener() {},
    querySelectorAll: selector => selector === "[data-output-tab]" ? [outputTab]
      : selector === "[data-output-panel]" ? [outputPanel] : [],
    querySelector: selector => selector === "#detailDialog" ? dialog
      : selector === "#dialogContent" ? dialogContent : targets.get(selector) || null,
    getElementById: id => anchors.get(id) || (id === "output-panel-creation" ? outputPanel : null),
  };
  const window = {location, history, addEventListener() {}, scrollTo(options) {scrolls.push(options);}, matchMedia: () => ({matches: true}),
    CompanionProduct: {scopeKey: () => "synthetic", workspace: (name, tab) => `${name}:${tab}`,
      manifest,
      extension: id => manifest.extensions.find(item => item.capability_id === id),
      provider: id => manifest.providers.find(item => item.capability_id === id),
      capability: id => manifest.extensions.find(item => item.capability_id === id)
        || manifest.providers.find(item => item.capability_id === id),
      providerDetail: id => ({title: manifest.extensions.find(item => item.capability_id === id)?.label || id,
        body: `<p>${id} capability status</p>`}),
      statusLabel: () => "状态已读取"},
    CompanionNativeExtensionPanel: {
      mount: (_root, options) => nativeMounts.push(options.extensionId),
      unmount: root => nativeUnmounts.push(root.dataset.nativeExtensionHost),
    },
  };
  const context = vm.createContext({window, document, location, history, console, renders,
    structuredClone, setTimeout, clearTimeout, requestAnimationFrame: callback => callback(), matchMedia: () => ({matches: true}),
    localStorage: {getItem: () => null}});
  vm.runInContext(page("shell.js"), context);
  vm.runInContext(page("page-guards.js"), context);
  // Run the navigation and render functions without starting the live page or SDK.
  const source = page("app.js");
  vm.runInContext(source.slice(0, source.indexOf("document.addEventListener('click'")), context);
  vm.runInContext("integrationSnapshot=window.CompanionProduct.manifest;", context);
  vm.runInContext(`render=()=>{
    if(route==='manage')window.CompanionShell.selectSection(manageTab);
    renders.push(currentNavigationHref());
  };renderShell=()=>renders.push(currentNavigationHref());`, context);
  const run = code => vm.runInContext(code, context);
  return {window, manifest, writes, renders, scrolls, dialog, dialogContent, outputTab, outputPanel,
    nativeMounts, nativeUnmounts, run,
    target(selector, target) {targets.set(selector, target);},
    visit(hash) {location.hash = hash; return run("syncFromHash()");},
    anchor(id) {
      const target = {hasAttribute: () => false, scrollIntoView() {this.scrolled = true;},
        focus() {this.focused = true;}};
      anchors.set(id, target);return target;
    }};
}

test("navigation is exposed to the setup guide and workspace modules", () => {
  const h = harness();
  assert.equal(typeof h.window.navigate, "function");
  assert.equal(h.window.navigate("manage", "data", "migration"), true);
  assert.equal(h.window.location.hash, "#manage/data/migration");
  assert.equal(h.run("currentNavigationHref()"), "#manage/data/migration");
});

test("the group archive destination renders the relationship workspace", () => {
  const h = harness();
  h.window.CompanionProduct.workspace = name => `<section data-workspace="${name}"></section>`;
  h.window.navigate("manage", "identity-feelings");
  assert.equal(h.window.location.hash, "#manage/identity-feelings");
  const html = h.run("renderManage()");
  assert.match(html, /data-workspace="relationships"/);
  assert.doesNotMatch(html, /relationshipProfileConfigWorkspace/);
});

test("returning home clears configuration, diary and furniture selections", () => {
  const h = harness(), shell = h.window.CompanionShell;
  for (const destination of ["navigate('manage','models')", "navigate('life','diary','sleep')",
    "chooseFurniture('desk')", "goHomeSection('furniture')"]) {
    h.run(destination);h.run("backHome()");
    assert.equal(h.window.location.hash, "#life");
    assert.equal(shell.section, null);assert.equal(shell.view, null);assert.equal(shell.furniture, null);
  }
});

test("unsaved weather changes hold navigation until the in-page confirmation resolves", async () => {
  const h = harness(), decisions = [];
  h.window.CompanionWeatherSourcePage = {canLeave: () => new Promise(resolve => decisions.push(resolve))};

  const canceled = h.run("navigate('manage','models')");
  assert.equal(typeof canceled.then, "function");
  assert.equal(h.run("currentNavigationHref()"), "#life");
  assert.equal(h.renders.length, 0);
  decisions[0](false);
  assert.equal(await canceled, false);
  assert.equal(h.window.location.hash, "#life");
  assert.equal(h.run("currentNavigationHref()"), "#life");

  const accepted = h.run("navigate('manage','models')");
  decisions[1](true);
  assert.equal(await accepted, true);
  assert.equal(h.window.location.hash, "#manage/models");
  assert.equal(h.run("currentNavigationHref()"), "#manage/models");
  assert.deepEqual(h.renders, ["#manage/models"]);

  const backNavigation = h.visit("#life/diary");
  assert.equal(typeof backNavigation.then, "function");
  assert.equal(h.run("currentNavigationHref()"), "#manage/models");
  decisions[2](false);
  assert.equal(await backNavigation, false);
  assert.equal(h.window.location.hash, "#manage/models");
});

test("dirty navigation lists changed fields, saves once and continues to the requested route", async () => {
  const h = harness();
  let dirty = true, options, resolve, saves = 0;
  h.window.CompanionWorkbench = {dirtyEditors:() => dirty ? [{title:'设备观察契机',fields:['每日观察上限','观察冷却间隔'],
    save:async () => {saves++;dirty=false;return true;}}] : []};
  h.window.CompanionConfirm = config => {options=config;return new Promise(done => {resolve=done;});};
  const navigation = h.run("navigate('manage','models')");
  assert.equal(h.renders.length, 0);
  assert.equal(options.details[0].title, '设备观察契机');
  assert.deepEqual(Array.from(options.details[0].fields), ['每日观察上限','观察冷却间隔']);
  assert.equal(await options.onSave(), true);
  assert.equal(saves, 1);
  resolve(true);
  assert.equal(await navigation, true);
  assert.equal(h.window.location.hash, '#manage/models');
  assert.equal(h.run("navigate('manage','perception')"), true);
});

test("cancel preserves drafts; discard clears them and rapid navigation shares one dialog", async () => {
  const h = harness();
  let dirty = true, calls = 0, options, resolve;
  h.window.CompanionWorkbench = {dirtyEditors:() => dirty ? [{title:'设备观察契机',fields:['每日观察上限'],
    discard:() => {dirty=false;return true;}}] : []};
  h.window.CompanionConfirm = config => {calls++;options=config;return new Promise(done => {resolve=done;});};
  const canceled = h.run("navigate('manage','models')");
  resolve(false); assert.equal(await canceled, false);
  assert.equal(dirty, true);
  assert.equal(h.window.location.hash, '#life');
  const first = h.run("navigate('manage','models')");
  const second = h.run("navigate('manage','perception')");
  assert.equal(calls, 2);
  assert.equal(await options.onConfirm(), true);
  resolve(true);
  assert.equal(await first, false); assert.equal(await second, true);
  assert.equal(h.window.location.hash, '#manage/perception');
  h.run("navigate('manage','models')"); h.run("backHome()");
  assert.equal(calls, 2, 'discarded drafts must not block later navigation');
});

test("task prompts stay inside maintenance while direct links select the internal view", () => {
  const h = harness();
  const maintenanceButton = {attributes: {}, setAttribute(name, value) {this.attributes[name] = value;}};
  h.target('#shellNav [data-shell-section="maintenance"]', maintenanceButton);
  h.run("navigate('manage','maintenance','prompts')");
  assert.equal(h.window.location.hash, "#manage/maintenance/prompts");
  assert.equal(h.run("maintenancePane"), "prompts");
  let rail = h.run("window.CompanionShell.railHTML()");
  assert.doesNotMatch(rail, /提示词编排|data-shell-route="prompts"/);
  assert.match(rail, /data-shell-section="maintenance"[^>]*aria-pressed="true"/);

  h.visit("#manage/maintenance/cache");
  assert.equal(h.window.location.hash, "#manage/maintenance/cache");
  assert.equal(h.run("currentNavigationHref()"), "#manage/maintenance/cache");
  rail = h.run("window.CompanionShell.railHTML()");
  assert.doesNotMatch(rail, /提示词编排|data-shell-route="prompts"/);
  assert.match(rail, /data-shell-section="maintenance"[^>]*aria-pressed="true"/);
  h.run("onMaintenanceViewChange('overview')");
  assert.equal(h.window.location.hash, "#manage/maintenance");
  h.run("onMaintenanceViewChange('prompts')");
  assert.equal(maintenanceButton.attributes["aria-pressed"], "true");
  h.run("onMaintenanceViewChange('cache')");
  assert.equal(maintenanceButton.attributes["aria-pressed"], "true");

  h.visit("#manage/maintenance/unknown");
  assert.equal(h.window.location.hash, "#manage/maintenance");
});

test("maintenance subpage changes keep direct links and browser history", () => {
  const h = harness();
  h.run("navigate('manage','maintenance')");
  const entriesBeforeViewChange = h.writes.length;
  h.run("onMaintenanceViewChange('cache')");
  assert.equal(h.window.location.hash, "#manage/maintenance/cache");
  assert.equal(h.writes.length, entriesBeforeViewChange + 1);
  assert.equal(h.writes.at(-1).method, "pushState");

  h.visit("#manage/maintenance/diagnostics");
  assert.equal(h.run("maintenancePane"), "diagnostics");
  assert.equal(h.window.location.hash, "#manage/maintenance/diagnostics");
  h.run("onMaintenanceViewChange('overview')");
  assert.equal(h.window.location.hash, "#manage/maintenance");
  assert.equal(h.writes.at(-1).method, "pushState");
});

test("reply efficiency is a deep-linkable maintenance subpage", () => {
  const h = harness();
  assert.equal(h.run("manageTabs.find(([id]) => id === 'maintenance')[2]"), "治理与维护");
  assert.match(h.run("navGroups.flatMap(([, items]) => items).find(([, id]) => id === 'maintenance')[3]"), /治理与维护/);
  h.run("navigate('manage','maintenance','efficiency')");
  assert.equal(h.window.location.hash, "#manage/maintenance/efficiency");
  assert.equal(h.run("currentNavigationHref()"), "#manage/maintenance/efficiency");
  h.visit("#manage/maintenance/efficiency");
  assert.equal(h.run("maintenancePane"), "efficiency");
  h.run("onMaintenanceViewChange('overview')");
  assert.equal(h.window.location.hash, "#manage/maintenance");
});

test("data page exposes three deep-linkable subpages", () => {
  const h = harness();
  for (const [pane, title, workspace] of [
    ["storage", "存储位置与清理策略", "storageWorkspace"],
    ["backup", "备份与还原", "backupWorkspace"],
    ["migration", "旧版资料迁入", "legacyMigrationWorkspace"],
  ]) {
    h.run(`navigate('manage','data','${pane}')`);
    assert.equal(h.window.location.hash, `#manage/data/${pane}`);
    assert.equal(h.run("currentNavigationHref()"), `#manage/data/${pane}`);
    const markup = h.run("renderDataOriginal()");
    assert.match(markup, new RegExp(`href="#manage/data/${pane}" aria-current="page"`));
    assert.match(markup, new RegExp(title));
    assert.match(markup, new RegExp(workspace));
    assert.equal((markup.match(/href="#manage\/data\/(?:storage|backup|migration)"/g) || []).length, 3);
    assert.doesNotMatch(markup, /各处记录的入口|data-record-grid/);
  }
  h.visit("#manage/data/records");
  assert.equal(h.window.location.hash, "#manage/data/storage");
  h.visit("#manage/data/unknown");
  assert.equal(h.window.location.hash, "#manage/data/storage");
});

test("settings jumps render the current single-page owner route", () => {
  const h = harness();
  const markup = h.run("settingsJump('语音与戳一戳','contact')");
  assert.match(markup, /data-go="manage"/);
  assert.match(markup, /data-manage="contact"/);
});

test("understanding configuration search opens the host-owned forward limits", () => {
  const h = harness();
  const location = h.run("configSearchLocation({group:'forward_intake'})");
  assert.equal(location.next, "manage");
  assert.equal(location.tab, "understanding");
  assert.equal(location.root, "#forwardIntakeWorkspace");
  const replyLocation = h.run("replySearchLocation({section:'forwards'})");
  assert.equal(replyLocation.tab, "understanding");
  assert.equal(replyLocation.root, "#contextImageWorkspace");
  for (const section of ["video_narration", "file_understanding"]) {
    const location = h.run(`replySearchLocation({section:'${section}'})`);
    assert.equal(location.tab, "understanding");
    assert.equal(location.root, "#contextImageWorkspace");
  }
});

test("quick setup reply links select the matching editable section", () => {
  const h = harness(), selected = [];
  h.window.CompanionReplyPage = {selectSection: (section, options) => selected.push({section, options})};
  h.run("navigateToReplySection('manage','understanding','','forwards')");
  assert.equal(h.window.location.hash, "#manage/understanding");
  assert.equal(selected.at(-1).section, "forwards");
  assert.equal(selected.at(-1).options.scroll, true);

  h.run("navigateToReplySection('manage','understanding','','image_narration')");
  assert.equal(h.window.location.hash, "#manage/understanding");
  assert.equal(selected.at(-1).section, "image_narration");
  assert.equal(selected.at(-1).options.scroll, true);
});

test("retired routes open their current workspace instead of account management", () => {
  const h = harness();
  const destinations = [
    ["#manage/growth", "#manage/learning/chain", "learning"],
    ["#manage/learning/skills", "#manage/learning/chain", "learning"],
    ["#manage/reply", "#manage/expression", "expression"],
    ["#manage/preferences", "#manage/contact", "contact"],
    ["#manage/semantic", "#manage/perception", "perception"],
    ["#people", "#manage/identity", "identity"],
    ["#memories", "#manage/memory", "memory"],
    ["#manage/base", "#life/diary", "diary"],
    ["#manage/state", "#life/state", "state"],
    ["#life/schedule", "#life/state", "state"],
    ["#library", "#life/directory", "directory"],
    ["#activities", "#life/directory", "directory"],
    ["#manage/capabilities", "#life/directory", "directory"],
  ];
  for (const [old, canonical, selection] of destinations) {
    h.visit(old);
    assert.equal(h.window.location.hash, canonical, old);
    assert.equal(h.window.CompanionShell.section || h.window.CompanionShell.view, selection, old);
    assert.equal(h.writes.at(-1).method, "replaceState", "aliases replace the current entry");
  }
});

test("self subpages survive copied links and a second address synchronization", () => {
  const h = harness();
  for (const pane of ["relationship", "questionnaire", "knowledge"]) {
    h.run(`navigate('manage','governance','${pane}')`);
    const hash = h.window.location.hash;
    assert.equal(hash, "#manage/governance/" + pane);
    h.visit(hash);
    assert.equal(h.run("selfPane"), pane);
  }
  h.visit("#manage/persona");assert.equal(h.run("selfPane"), "questionnaire");
  h.visit("#manage/knowledge");assert.equal(h.run("selfPane"), "knowledge");
});

test("reading and sleep entry points set the embedded tab before rendering", () => {
  const h = harness();
  for (const [detail, tab] of [["reading", "reading"], ["sleep", "sleep"], ["desk", "archive"]]) {
    h.run(`showDetail('${detail}')`);
    assert.equal(h.window.CompanionShell.view, "diary");
    assert.equal(h.run("renderLifeWorkspace()"), "life:" + tab);
    const hash = h.window.location.hash;
    h.run("navigate('manage','models')");h.visit(hash);
    assert.equal(h.run("renderLifeWorkspace()"), "life:" + tab);
  }
});

test("token usage links open the matching feature configuration", () => {
  const h = harness();
  assert.ok(h.run("manageTabs.some(([id]) => id === 'understanding')"));
  h.run("window.CompanionProduct.role = () => ({reply_api: '/reply'})");
  const understanding = h.run("renderUnderstanding()");
  assert.match(understanding, /id="contextImageWorkspace"/);
  assert.match(understanding, /图片、视频与文件理解/);
  assert.doesNotMatch(understanding, /节点显示的账号未经身份认证/);
  assert.match(understanding, /data-understanding-tab="forwards"/);
  assert.match(understanding, /id="forwardIntakeWorkspace"/);
  assert.match(understanding, /data-manage="decision"/);
  assert.match(understanding, /关系站位/);
  assert.match(understanding, /data-manage="identity"/);
  assert.equal(h.run("openTokenTaskSettings('proactive.compose')"), true);
  assert.equal(h.window.location.hash, "#manage/contact");
  assert.equal(h.run("tokenTaskSettingsFor('proactive.compose').contactView"), "purposes");
  assert.equal(h.run("tokenTaskSettingsFor('conversation.video_narration').tab"), "understanding");

  h.run("openTokenTaskSettings('memory.reflect')");
  assert.equal(h.window.location.hash, "#manage/memory");
  assert.equal(h.run("memoryWorkspaceRoute"), "settings");
  assert.equal(h.run("tokenTaskSettingsFor('memory.reflect').memorySection"), "reflection");

  h.run("openTokenTaskSettings('role.reading.consume')");
  assert.equal(h.window.location.hash, "#life/diary/reading");
  assert.equal(h.run("tokenTaskSettingsFor('role.reading.consume').workbench.field"), "reading_enabled");

  const decisionViews = [];
  h.window.CompanionDecisionPage = {setView: view => decisionViews.push(view)};
  h.run("openTokenTaskSettings('conversation.group.review')");
  assert.equal(h.window.location.hash, "#manage/decision");
  assert.equal(decisionViews.at(-1), "group");
  assert.equal(h.run("tokenTaskSettingsFor('conversation.group.review').workbench.root"), "#groupInterjectionConfigWorkspace");
  assert.equal(h.run("tokenTaskSettingsFor('conversation.group.review').workbench.field"), "enable_group_interjection");

  h.run("openTokenTaskSettings('content.authoring.draft')");
  assert.equal(h.window.location.hash, "#manage/output");
  assert.equal(h.run("tokenTaskSettingsFor('content.authoring.draft').outputTab"), "creation");

  const knownKinds = ["conversation.reply", "conversation.group.review", "proactive.compose", "proactive.judge",
    "memory.reflect", "memory.reflection", "role.state.maintain", "role.state.maintenance", "calendar.plan",
    "role.calendar.plan", "role.reading.consume", "role.growth.progress", "role.journal.diary", "role.journal.dream",
    "role.journal.derive", "role.journal.select", "role.self_review", "role.expression.learn", "content.authoring.draft",
    "conversation.context_image_caption", "conversation.image_narration", "conversation.image_review", "image_review", "screen.interpretation", "screen.peek"];
  for (const kind of knownKinds) assert.ok(h.run(`tokenTaskSettings['${kind}']`), kind);
  assert.equal(h.run("tokenTaskSettingsFor('conversation.image_narration').tab"), 'understanding');
});

test("token usage opens the loaded workbench section and focuses its switch", () => {
  const h = harness();
  const selectors = [];
  const tab = {dataset: {wbSection: "reflection"}, getAttribute: () => "false", clicked: false,
    click() {this.clicked = true;}};
  const field = {dataset: {wbField: "reflection_enabled"}, scrolled: false, focused: false,
    matches: () => true, scrollIntoView() {this.scrolled = true;}, focus() {this.focused = true;}};
  h.target("#memorySettingsHost", {querySelectorAll: selector => {
    selectors.push(selector);
    return selector === "[data-wb-section]" ? [tab] : selector === "[data-wb-field]" ? [field] : [];
  }});
  h.run("openTokenTaskSettings('memory.reflect')");
  assert.deepEqual(selectors, ["[data-wb-section]", "[data-wb-group]", "[data-wb-field]"]);
  assert.equal(tab.clicked, true);
  assert.equal(field.scrolled, true);
  assert.equal(field.focused, true);
});

test("global search includes hidden content destinations and registered setting metadata", () => {
  const h = harness(), input = {value: "余额感知"}, results = {innerHTML: ""};
  h.target("#globalSearch", input);h.target("#searchResults", results);
  h.window.CompanionConfigEditors = {searchEntries: () => [{group: "proactive.contact",
    groupTitle: "主动联系偏好", field: "balance_awareness_enabled", label: "允许因余额变化联系",
    help: "余额感知开关", section: "balance", sectionTitle: "余额感知", sectionCount: 23, aliases: []}]};
  h.run("renderSearch()");
  assert.match(results.innerHTML, /允许因余额变化联系/);
  assert.match(results.innerHTML, /主动联系/);
  input.value = "便签";h.run("renderSearch()");
  assert.match(results.innerHTML, /便签/);
});

test("global search ranks editable settings and can limit results to settings or pages", () => {
  const h = harness(), input = {value: "语音"}, results = {innerHTML: ""};
  h.target("#globalSearch", input);h.target("#searchResults", results);
  h.window.CompanionConfigEditors = {searchEntries: () => [{group: "voice_reply",
    groupTitle: "语音默认值", field: "enabled", label: "允许语音回复", section: "defaults",
    sectionTitle: "全局总开关", sectionCount: 1, aliases: ["TTS"]}]};
  h.run("renderSearch()");
  assert.equal(h.run("visibleSearchResults[0].kind"), "setting");
  assert.match(results.innerHTML, /配置项 · 表达/);

  h.run("setSearchScope('pages')");
  assert.ok(h.run("visibleSearchResults.length > 0"));
  assert.equal(h.run("visibleSearchResults.every(result => result.kind === 'page')"), true);
  h.run("setSearchScope('settings')");
  assert.equal(h.run("visibleSearchResults.length"), 1);
  assert.equal(h.run("visibleSearchResults[0].entry.label"), "允许语音回复");
});

test("global search moves the active result with arrow keys and opens it with Enter", () => {
  const h = harness(), input = {value: "主动联系", attributes: {}, setAttribute(name, value) {this.attributes[name] = value;}};
  const options = [0, 1].map(index => ({id: `search-option-${index}`, attributes: {}, clicks: 0,
    setAttribute(name, value) {this.attributes[name] = value;},
    scrollIntoView() {this.scrolled = true;}, click() {this.clicks += 1;}}));
  h.target("#globalSearch", input);
  h.target("#searchResults", {innerHTML: "", querySelectorAll: () => options});
  h.window.CompanionConfigEditors = {searchEntries: () => [
    {group: "proactive.contact", groupTitle: "主动联系偏好", field: "enabled",
      label: "主动联系开关", section: "general", sectionTitle: "联系节奏", aliases: []},
    {group: "proactive.contact", groupTitle: "主动联系偏好", field: "cooldown",
      label: "主动联系冷却时间", section: "general", sectionTitle: "联系节奏", aliases: []},
  ]};
  h.run("setSearchScope('settings')");
  assert.equal(h.run("visibleSearchResults.length"), 2);
  h.run("handleGlobalSearchKeydown({key:'ArrowDown',preventDefault(){}})");
  assert.equal(h.run("activeSearchResultIndex"), 1);
  assert.equal(input.attributes["aria-activedescendant"], "search-option-1");
  assert.equal(options[1].attributes["aria-selected"], "true");
  h.run("handleGlobalSearchKeydown({key:'Enter',preventDefault(){}})");
  assert.equal(options[1].clicks, 1);
});

test("global search opens a setting section and focuses the matching field", () => {
  const h = harness();
  const field = {dataset: {wbField: "dialogue_default"}, matches: () => true,
    scrollIntoView() {this.scrolled = true;}, focus() {this.focused = true;}};
  h.target("#privateAccessWorkspace", {querySelectorAll: selector =>
    selector === "[data-wb-section]" ? [] : [field]});
  h.run(`openSearchSetting({entry:{group:"private_access",section:"defaults",field:"dialogue_default"},
    location:{next:"manage",tab:"identity",root:"#privateAccessWorkspace"}})`);
  assert.equal(h.window.location.hash, "#manage/identity");
  assert.equal(field.scrolled, true);
  assert.equal(field.focused, true);
});

test("relay settings render in access rules and search focuses the requested field", () => {
  const h = harness();
  h.window.CompanionProduct.role = () => ({config_api: "/astrbot_plugin_private_companion/targets/first/roles/role/config"});
  const html = h.run("renderIdentityAccessPage()");
  assert.match(html, /id="relayConfigWorkspace"/);
  const field = {dataset: {wbField: "allowed_targets"}, matches: () => true,
    scrollIntoView() {this.scrolled = true;}, focus() {this.focused = true;}};
  h.target("#relayConfigWorkspace", {querySelectorAll: selector =>
    selector === "[data-wb-section]" ? [] : [field]});
  const entry = {group: "relay", section: "relay", field: "allowed_targets"};
  h.run("openSearchSetting({entry:" + JSON.stringify(entry) + ",location:configSearchLocation(" + JSON.stringify(entry) + ")})");
  assert.equal(h.window.location.hash, "#manage/identity-access");
  assert.equal(field.scrolled, true);
  assert.equal(field.focused, true);
});

test("global search expands relationship network settings before focusing member clues", () => {
  const h = harness();
  const disclosure = {open: false};
  const field = {dataset: {wbField: "member_match_aliases"}, matches: () => true,
    scrollIntoView() {this.scrolled = true;}, focus() {this.focused = true;}};
  h.target("#memberClueWorkspace", {closest: () => disclosure, querySelectorAll: selector =>
    selector === "[data-wb-section]" ? [] : [field]});
  const entry = {group: "knowledge", section: "members", field: "member_match_aliases"};
  h.run("openSearchSetting({entry:" + JSON.stringify(entry) + ",location:configSearchLocation(" + JSON.stringify(entry) + ")})");
  assert.equal(disclosure.open, true);
  assert.equal(field.scrolled, true);
  assert.equal(field.focused, true);
});

test("reply gate setting links select the gate view before focusing the editor", () => {
  const h = harness();
  const gateButton = {clicks: 0, click() {this.clicks += 1;}};
  h.target('#contactWorkspace [data-view="gates"]', gateButton);

  const busyGate = h.run("configSearchLocation({group:'busy_gate',section:'reply'})");
  assert.equal(busyGate.contactView, 'gates');
  h.run("openSearchSetting({entry:{group:'busy_gate',section:'reply',field:'busy_enabled'},location:configSearchLocation({group:'busy_gate',section:'reply'})})");
  assert.equal(gateButton.clicks, 1, '全局搜索应先打开开口闸门视图');

  assert.equal(h.run("configSearchLocation({group:'life',section:'rest'})").contactView, 'gates');
  assert.equal(h.run("configSearchLocation({group:'life',section:'backlog'})").contactView, 'gates');
});

test("global search opens the host voice and received-poke settings in their expression tabs", () => {
  const h = harness();
  const voiceField = {dataset: {wbField: "group_probability"}, matches: () => true,
    scrollIntoView() {this.scrolled = true;}, focus() {this.focused = true;}};
  const pokeField = {dataset: {wbField: "trigger_probability"}, matches: () => true,
    scrollIntoView() {this.scrolled = true;}, focus() {this.focused = true;}};
  h.window.CompanionHostOptionsPage = {searchEntries: () => [
    {group: "voice_reply", groupTitle: "宿主语音默认值", field: "group_probability", label: "群聊默认概率",
      section: "defaults", sectionTitle: "语音默认值", sectionCount: 1, aliases: ["TTS", "群聊"]},
    {group: "poke", groupTitle: "平台动作", field: "trigger_probability", label: "回复概率",
      section: "received", sectionTitle: "收到戳一戳", sectionCount: 1, aliases: ["戳一戳"]},
  ]};
  h.target("#voiceReplyPolicyWorkspace", {querySelectorAll: selector => selector === "[data-wb-section]" ? [] : [voiceField]});
  h.target("#pokePolicyWorkspace", {querySelectorAll: selector => selector === "[data-wb-section]" ? [] : [pokeField]});

  const voiceEntry = h.window.CompanionHostOptionsPage.searchEntries()[0];
  h.run(`openSearchSetting({entry:${JSON.stringify(voiceEntry)},location:configSearchLocation(${JSON.stringify(voiceEntry)})})`);
  assert.equal(h.window.location.hash, "#manage/expression/voice");
  assert.equal(h.run("expressionModule"), "expression-voice");
  assert.equal(voiceField.focused, true);

  const pokeEntry = h.window.CompanionHostOptionsPage.searchEntries()[1];
  h.run(`openSearchSetting({entry:${JSON.stringify(pokeEntry)},location:configSearchLocation(${JSON.stringify(pokeEntry)})})`);
  assert.equal(h.window.location.hash, "#manage/expression/platform");
  assert.equal(h.run("expressionModule"), "expression-platform");
  assert.equal(h.run("replyPane"), "");
  assert.equal(pokeField.focused, true);
});

test("profile and world details open the self editor and knowledge subpage", () => {
  const h = harness();
  h.run("showDetail('profile')");assert.equal(h.window.location.hash, "#manage/governance");
  h.run("showDetail('world')");assert.equal(h.window.location.hash, "#manage/governance/knowledge");
  h.run("showDetail('growth')");assert.equal(h.window.location.hash, "#manage/learning/chain");
  h.run("navigate('manage','expression','voice')");
  assert.equal(h.window.location.hash, "#manage/expression/voice");
  h.visit("#manage/reply/voice");assert.equal(h.run("replyPane"), "voice");
});

test("section anchors preserve the current page and focus their destination", () => {
  const h = harness(), target = h.anchor("expression-voice");
  h.run("navigate('manage','expression')");
  const renders = h.renders.length;
  h.visit("#expression-voice");
  assert.equal(h.window.location.hash, "#manage/expression");
  assert.equal(h.renders.length, renders);
  assert.equal(target.focused, true);assert.equal(target.scrolled, true);
});

test("page anchors expand collapsed disclosure sections before focusing them", () => {
  const h = harness();
  const details = {tagName: "DETAILS", open: false, parentElement: null};
  const target = h.anchor("nested-setting");
  target.parentElement = details;

  assert.equal(h.run("scrollToPageAnchor('nested-setting')"), true);
  assert.equal(details.open, true);
  assert.equal(target.focused, true);
});

test("user navigation records history and repeated synchronization preserves the editor", () => {
  const h = harness();
  h.run("navigate('manage','models')");
  const renders = h.renders.length;
  h.run("syncFromHash();syncFromHash()");
  assert.equal(h.renders.length, renders);
  assert.equal(h.writes[0].method, "pushState");
  h.visit("#life");assert.equal(h.window.CompanionShell.section, null);
});

test("reopening the active route does not reset the page scroll", () => {
  const h = harness();
  h.run("navigate('manage','models')");
  assert.equal(h.scrolls.length, 1, "entering a different route returns to its start");
  h.run("navigate('manage','models')");
  assert.equal(h.scrolls.length, 1, "the active route keeps its current scroll position");
});

test("wardrobe furniture exposes its room and reference-material actions", () => {
  const markup = harness().run("renderFurniturePanel('wardrobe')");
  assert.match(markup, /data-detail="arrange"/);
  assert.match(markup, /data-manage="output" data-output-target="materials"/);
});

test("home calendar action opens the calendar furniture with planning expanded", () => {
  const h = harness();
  h.window.CompanionProduct.scheduleCard = () => "schedule";
  assert.match(h.run("homeScheduleBand()"), /data-shell-furniture="calendar"/);
  assert.match(h.run("renderFurniturePanel('calendar')"), /data-state-feature="planning"/);
});

test("native companion furniture opens its real workbench or settings surface", () => {
  const h = harness();
  const ids = ["content", "image", "screen", "reality", "together", "game", "live"];
  h.manifest.extensions = ids.map(id => ({capability_id:id, label:`${id} label`, reason:`${id} ready`,
    owner:"companion_v2", extension_id:id, assembled:true, desired_state:"active",
    integration_state:"connected", installed_revision:1}));

  const destinations = JSON.parse(h.run("JSON.stringify(directory.filter(item=>item.cap).map(({id,detail})=>[id,detail]))"));
  assert.deepEqual(destinations, [["content", "books"], ["image", "cap-image"], ["together", "cap-together"],
    ["game", "cap-game"], ["screen", "cap-screen"], ["reality", "cap-reality"], ["live", "cap-live"],
    ["video-vision", "cap-video_vision"]]);

  /* 创作书架和其他家具走同一条路：先给真实的装配状态与作品库入口。未装配时
     直接跳产出页只剩一张静态卡片，用户再也找不到装配按钮。 */
  h.dialog.classList = {add() {}, remove() {}};
  h.run("showDetail('books')");
  assert.equal(h.dialog.open, true, "Content's bookshelf entry opens its real assembly state");
  assert.match(h.dialogContent.innerHTML, /创作书架/);
  assert.match(h.dialogContent.innerHTML,
    /data-go="manage" data-manage="output" data-output-target="creation"/,
    "the shelf entry still points at the creation workspace");
  h.run("activateOutputTab('creation')");
  assert.equal(h.run("outputTabId"), "creation", "that entry target is a real output tab");
  assert.equal(h.outputPanel.hidden, false);
  assert.equal(h.outputTab.attributes["aria-selected"], "true");

  for (const id of ids) {
    h.run(`showDetail('cap-${id}')`);
    assert.equal(h.dialog.open, true, `${id} opens its capability detail`);
    if (id === "image") {
      assert.match(h.dialogContent.innerHTML, /V2 内置能力/);
      assert.match(h.dialogContent.innerHTML, /data-output-target="visual"/);
      assert.match(h.dialogContent.innerHTML, /data-output-target="materials"/);
    } else if (id === "screen") {
      assert.match(h.dialogContent.innerHTML, /V2 内置能力/);
      assert.match(h.dialogContent.innerHTML, /data-perception-tab-target="config-screen"/);
    } else if (["reality", "together", "game", "live"].includes(id)) {
      assert.match(h.dialogContent.innerHTML, new RegExp(`data-native-extension-host="${id}"`));
      assert.equal(h.nativeMounts.at(-1), id, `${id} mounts its settings panel`);
    } else assert.match(h.dialogContent.innerHTML, /V2 内置能力/);
  }
  assert.deepEqual(h.nativeMounts, ["reality", "together", "game", "live"]);
  assert.deepEqual(h.nativeUnmounts, ["reality", "together", "game"]);
});

test("furniture directory filters, search and attention counts use the same records", () => {
  const h = harness();
  const labels = {content: "创作与 QQ 空间", image: "生图与相册", together: "共处与实时活动",
    game: "共同游玩", screen: "屏幕陪伴", reality: "现实触及", live: "直播与表演"};
  h.manifest.extensions = Object.entries(labels).map(([capability_id, label]) => ({capability_id,
    label, owner: "companion_v2", assembled: true, desired_state: "active", integration_state: "connected"}));
  h.manifest.providers = [{capability_id: "image", label: "旧版图像插件", integration_state: "missing"}];
  const extension = id => h.window.CompanionProduct.extension(id);
  const ids = () => Array.from(h.run("filteredFurniture().map(item=>item.id)"));
  h.run("furnitureFilter='extensions'");
  assert.deepEqual(ids(),
    ["content", "image", "together", "game", "screen", "reality", "live", "video-vision"]);
  h.run("furnitureFilter='base'");
  assert.equal(ids().length, 7);
  h.run("furnitureFilter='all';furnitureQuery='生图与相册'");
  assert.deepEqual(ids(), ["image"]);
  h.run("furnitureQuery='';furnitureFilter='attention';state.modules.image='removed'");
  /* 外部提供方没有自报 connected 就留在待检查：这份夹具里没有它的状态记录，
     所以「观影机」和其余没连上的能力走同一个判据，不额外开后门。 */
  assert.deepEqual(ids(), ["video-vision"], "native service state owns availability despite an old local furniture flag or missing external plugin");
  Object.assign(extension("together"), {desired_state: "paused", integration_state: "stopped"});
  Object.assign(extension("game"), {desired_state: "discarded", assembled: false, integration_state: "unassembled"});
  Object.assign(extension("screen"), {integration_state: "degraded"});
  Object.assign(extension("live"), {desired_state: "paused", integration_state: "stopped"});
  assert.deepEqual(ids(), ["together", "game", "screen", "live", "video-vision"]);
  assert.equal(h.run("capabilityStatus('image')[0]"), "已装配");
  assert.equal(h.run("capabilityStatus('together')[0]"), "已暂停");
  assert.equal(h.run("capabilityStatus('game')[0]"), "未装配");
  const html = h.run("renderDirectory()");
  assert.match(html, /data-furniture-filter="attention"[^>]*>待检查<span>5<\/span>/);
  assert.match(html, /显示 5 \/ 15 件/);
  Object.assign(extension("together"), {desired_state: "active", integration_state: "connected"});
  assert.deepEqual(ids(), ["game", "screen", "live", "video-vision"]);
  assert.match(h.run("renderDirectory()"), /待检查<span>4<\/span>/);
  h.run("furnitureQuery='不存在的家具'");
  assert.match(h.run("renderDirectory()"), /没有符合条件的家具/);
});

test("the video understanding card opens its external provider detail", () => {
  /* 这张卡没有 tab，也不属于基础生活：点开必须落到 providerDetail() 的插件资料，
     而不是「这件事在别处」的兜底弹层。 */
  const h = harness();
  h.manifest.providers = [{capability_id: "video_vision", plugin_id: "astrbot_plugin_video_vision",
    label: "基米看得见视频", version: "1.2.3", installed: true, pages: [],
    integration_state: "installed", enabled: null, runtime_ready: null}];
  h.dialog.classList = {add() {}, remove() {}};
  const card = JSON.parse(h.run("JSON.stringify(directory.find(item=>item.id==='video-vision'))"));
  assert.equal(card.cap, "video_vision");
  assert.equal(card.detail, "cap-video_vision");
  assert.equal(card.tab, undefined, "tab 会让卡片切页签，读不到插件状态");
  assert.equal(card.group, undefined, "外部提供方不是基础生活家具");
  /* 行内说明与搜索都消费 text：缺了它会渲染成 undefined，卡片看起来像坏了一半。 */
  const row = h.run("renderDirectory()");
  assert.match(row, /观影机[\s\S]{0,200}?可选外部视频扩展；本插件已原生理解视频/);
  assert.doesNotMatch(row, /undefined/, "卡片被消费的字段必须都有值");
  h.run("openFurniture('video-vision')");
  assert.equal(h.dialog.open, true, "the card opens a detail dialog");
  assert.match(h.dialogContent.innerHTML, /video_vision capability status/,
    "the dialog comes from providerDetail(), not the 这件事在别处 fallback");
  assert.deepEqual(h.nativeMounts, [], "an external provider mounts no native settings panel");
});
