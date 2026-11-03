const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const pageRoot = path.resolve(__dirname, "../pages/陪伴小屋");
const source = fs.readFileSync(path.join(pageRoot, "decision-page.js"), "utf8");

function harness() {
  const panels = new Map(["overview", "response", "tempo", "safety", "group"]
    .map(view => [view, {dataset:{decisionPanel:view}, innerHTML:"", hidden:false}]));
  const groupEditor = {state:{draft:{enable_group_topic_threads:true,group_topic_thread_max:12,
    enable_group_social_context:true,enable_group_mood_detection:true,enable_group_roleplay_strength:true,
    enable_group_moments:true,enable_group_moment_portrait:true,enable_group_interjection:false,
    enable_group_repeat_follow:true,group_repeat_trigger_threshold:3,group_repeat_follow_probability:0.5}},
    visibleGroups:[],setVisibleGroups(groups){this.visibleGroups=groups;}};
  const current = {textContent:""};
  const root = {innerHTML:"",dataset:{},querySelector(selector){
    if(selector==="#groupParticipationConfigWorkspace")return {companionConfigEditor:groupEditor};
    if(selector==='[data-decision-current]')return current;
    const panel=selector.match(/^\[data-decision-panel="([^"]+)"\]$/);
    return panel?panels.get(panel[1])||null:null;
  },querySelectorAll(selector){
    return selector==="[data-decision-panel]"?[...panels.values()]:[];
  },
    addEventListener(){},removeEventListener(){}};
  const window = {lucide:{createIcons(){}}};
  vm.runInNewContext(source, {window});
  return {page:window.CompanionDecisionPage,root,panels,groupEditor};
}

test("decision page owns group participation and does not duplicate other domains", () => {
  const view = harness();
  view.page.mount(view.root, {groupSettingsMarkup:'<section id="decisionReplyWorkspace"></section><section id="groupParticipationConfigWorkspace"></section>'});

  assert.doesNotMatch(view.root.innerHTML, /perceptionOverviewWorkspace/);
  assert.match(view.root.innerHTML, /groupParticipationConfigWorkspace/);
  assert.match(view.root.innerHTML, /decisionReplyWorkspace/);
  for (const tab of ["overview", "response", "tempo", "safety", "group"])
    assert.match(view.root.innerHTML, new RegExp(`data-decision-tab="${tab}"`));
  assert.match(view.root.innerHTML, /<div class="decision-tab-list" role="tablist">/);
  assert.match(view.root.innerHTML, /<\/div>\s*<span class="decision-current"/);
  assert.doesNotMatch(view.root.innerHTML,
    /roleStateWorkspace|lifeConfigWorkspace|memorySettingsHost|personConfigWorkspace|privateAccessWorkspace/);
  view.page.unmount();
});

test("group participation exposes topic thread settings and their bounded behavior", () => {
  const view = harness();
  view.page.mount(view.root, {groupSettingsMarkup:'<section id="groupParticipationConfigWorkspace"></section>'});
  view.page.setView("group");

  assert.deepEqual(Array.from(view.groupEditor.visibleGroups), ["topics", "atmosphere", "repeat"]);
  assert.match(view.panels.get("group").innerHTML, /群聊话题线/);
  assert.match(view.panels.get("group").innerHTML, /最多保留 12 条 · 90 分钟无更新后失效/);
  assert.match(view.panels.get("overview").innerHTML, /群聊话题线/);
  view.page.unmount();
});

test("narrow decision navigation scrolls tabs without covering the status", () => {
  const css = fs.readFileSync(path.join(pageRoot, "decision-page.css"), "utf8");
  assert.match(css, /\.decision-tab-list\{[^}]*overflow-x:auto/s);
  assert.match(css, /\.decision-current\{position:static;justify-self:end/);
  assert.doesNotMatch(css, /\.decision-current\{position:sticky/);
});

test("the perception overview stays on perception while decision settings remain target-aware", () => {
  const app = fs.readFileSync(path.join(pageRoot, "app.js"), "utf8");
  assert.match(app, /CompanionDecisionPage\?\.mount\(decisionHost,\{groupSettingsMarkup:renderDecisionOriginal\(\),harassmentApi:/);
  assert.match(app, /function renderDecisionOriginal\(\)\{[\s\S]*?pageNeedsTarget\('群聊参与'\)/);
  assert.match(app, /function renderDecisionOriginal\(\)[\s\S]*?configEditors\('groupParticipationConfigWorkspace',\['group_participation'\],'群聊参与'\)/);
  assert.match(app, /const endpoint=role\.reply_api\+'\/participation'/);
  assert.match(app, /id="groupInterjectionConfigWorkspace"[\s\S]*?data-config-api="\$\{escapeHTML\(endpoint\)\}"/);
  assert.match(app, /if\(replyHost\)[\s\S]*?view:'expression',section:replyPane[\s\S]*?else if\(decisionReplyHost\)[\s\S]*?view:'decision',[\s\S]*?updateReplyStatus\?\.\(status\)[\s\S]*?else if\(contextImageHost\)[\s\S]*?view:'perception'/);
  assert.match(app, /id="contextImageWorkspace"/);
  assert.match(app, /sectionIds:\['topics','atmosphere','joke_guard','repeat','history'\]/);
  assert.match(app, /sectionIds:\['interjection'\][\s\S]*?requestSection:null[\s\S]*?resetKey:'reset_to_inherit'/);
  assert.match(source, /data-decision-group-only/);
  assert.match(source, /groupInterjectionConfigWorkspace/);
  assert.match(app, /sectionIds:\['denoise'\],title:'群聊表达去噪'/);
  assert.match(app, /decision:'群聊中判断何时回应、如何参与并守住边界。'/);
  assert.match(app, /function renderPerceptionOriginal\(\)[\s\S]*?id="perceptionOverviewWorkspace"/);
  assert.match(app, /if\(\$\('#perceptionOverviewWorkspace'\)\)guarded\('感知上下文',[\s\S]*?CompanionPerceptionOverviewPage\?\.mount/);
  assert.doesNotMatch(app, /决策页复用感知页的只读上下文流/);
  assert.match(app, /decisionView:entry\.section==='joke_guard'\?'safety':'group'/);
  assert.match(app, /const decisionView=\['wakeup_direct','wakeup_interest','wakeup_members'\]/);
  const searchOpen = app.slice(app.indexOf("function openSearchSetting("), app.indexOf("function openTokenTaskSettings("));
  const tokenOpen = app.slice(app.indexOf("function openTokenTaskSettings("), app.indexOf("function openSearch("));
  assert.match(searchOpen, /if\(location\.decisionView\)window\.CompanionDecisionPage\?\.setView/);
  assert.doesNotMatch(searchOpen, /target\.decisionView/);
  assert.match(tokenOpen, /if\(target\.decisionView\)window\.CompanionDecisionPage\?\.setView/);
});

test("settings are hosted by their matching domain pages", () => {
  const app = fs.readFileSync(path.join(pageRoot, "app.js"), "utf8");
  const memory = app.slice(app.indexOf("function renderMemoryPage(){"), app.indexOf("/* 学习与反思。", app.indexOf("function renderMemoryPage(){")));
  const diary = app.slice(app.indexOf("if(name==='diary')"), app.indexOf("return renderHomePanel();", app.indexOf("if(name==='diary')")));
  const identity = app.slice(app.indexOf("function renderIdentityPageOriginal(){"), app.indexOf("/* 主动与联系。", app.indexOf("function renderIdentityPageOriginal(){")));

  assert.match(memory, /data-memory-route="settings"/);
  assert.match(memory, /memorySettingsHost/);
  assert.match(diary, /lifeConfigWorkspace/);
  assert.match(identity, /personConfigWorkspace/);
  assert.match(identity, /privateAccessWorkspace/);
  assert.match(app, /if\(name==='state'\)[\s\S]*?renderRoleState\(\)/);
});

test("knowledge import and member attribution settings use separate input and self views", () => {
  const app = fs.readFileSync(path.join(pageRoot, "app.js"), "utf8");
  assert.match(app, /configEditors\('memberClueWorkspace',\['knowledge'\]/);
  assert.match(app, /knowledgeConfigWorkspace[\s\S]*?sectionIds:\['import','inject'\]/);
  assert.match(app, /memberClueWorkspace[\s\S]*?sectionIds:\['members'\]/);
});

test("memory view tabs support keyboard navigation", () => {
  const app = fs.readFileSync(path.join(pageRoot, "app.js"), "utf8");
  assert.match(app, /\[role="tab"\]\[data-memory-route\]/);
  assert.match(app, /data-memory-route="records"/);
  assert.match(app, /data-memory-route="settings"/);
  assert.match(app, /ArrowRight.*tabs\.length-1/s);
});

test("private access editors separate known users from unbound accounts", () => {
  const configSource = fs.readFileSync(path.join(pageRoot, "config-editor.js"), "utf8");
  const window = {};
  vm.runInNewContext(configSource, {window});
  const role = window.CompanionConfigEditors.spec.private_access;
  const installation = window.CompanionConfigEditors.spec.private_access_installation;
  for (const spec of [role, installation]) {
    const fields = Array.from(spec.sections).flatMap(section => Array.from(section.fields));
    assert.deepEqual(fields, Array.from(spec.order));
  }
  assert.deepEqual(Array.from(role.sections, section => section.title),
    ["新用户默认权限", "无权限回复"]);
  assert.deepEqual(Array.from(installation.sections, section => section.title), ["陌生号处理"]);
  assert.match(installation.note, /插件安装全局生效/);
  assert.equal(window.CompanionConfigEditors.endpointFor(null, "", ["private_access_installation"]),
    "/astrbot_plugin_private_companion/config");
});
