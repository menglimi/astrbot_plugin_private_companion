const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const pageRoot = path.resolve(__dirname, "../pages/陪伴小屋");
const source = fs.readFileSync(path.join(pageRoot, "decision-page.js"), "utf8");

function harness() {
  const panels = new Map(["overview", "response", "tempo", "safety", "guard", "group"]
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
  return {page:window.CompanionDecisionPage,root,panels,groupEditor,window};
}

/* 页面桥把 endpoint 当插件相对路径用：不带前导斜杠、不带插件目录名，
   插件名由宿主自己补，所以这里带的是 host/… 这一段。 */
const privateEndpoint = "host/targets/t1/roles/r1/reply/harassment";
const guardSnapshot = () => ({settings:{enabled:true, dry_run:false, cooldown_seconds:600,
  strike_block_at:3, keywords_strong:"", keywords_weak:"", entries:[]}, entries:[], audit:[], reasons:{}});

async function settle() { await new Promise(resolve => setImmediate(resolve)); }

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
  assert.match(app, /CompanionDecisionPage\?\.mount\(decisionHost,\{groupSettingsMarkup:renderDecisionOriginal\(\),harassmentApi:harassmentEndpoint\(\),scopeKind:product\.target\?\.\(\)\?\.scope_kind/);
  /* 防骚扰的接口地址只在私聊窗口给，而且必须是插件相对路径：
     目录里的 reply 路由带着插件目录名，交给页面桥会被宿主再补一次，
     两个作用域都会得到「未找到该路由」。 */
  const guardRoute = app.slice(app.indexOf("function harassmentEndpoint(){"),
    app.indexOf("function allFeatureFieldSpec("));
  assert.match(guardRoute, /if\(product\.target\?\.\(\)\?\.scope_kind!=='private'\)return '';/);
  assert.match(guardRoute, /pluginRelativePath\(product\.role\?\.\(\)\?\.reply_api\)/);
  assert.doesNotMatch(guardRoute, /reply_api\+'\/harassment'/);
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

test("harassment stays private: the group window drops the tab and sends nothing", async () => {
  const view = harness();
  const requests = [];
  view.window.CompanionProduct = {bridgeNow: () => ({apiGet: async path => {
    requests.push(path);
    return guardSnapshot();
  }})};
  view.page.mount(view.root, {scopeKind: "group", harassmentApi: privateEndpoint});
  view.page.setView("guard");
  await settle();

  assert.doesNotMatch(view.root.innerHTML, /data-decision-tab="guard"/);
  for (const tab of ["overview", "response", "tempo", "safety", "group"])
    assert.match(view.root.innerHTML, new RegExp(`data-decision-tab="${tab}"`));
  assert.deepEqual(requests, []);
  const markup = view.panels.get("guard").innerHTML;
  assert.match(markup, /防骚扰只对私聊窗口生效/);
  assert.doesNotMatch(markup, /data-guard-retry/);
  assert.doesNotMatch(markup, /aria-busy/);
  view.page.unmount();
});

test("private scope asks for the harassment snapshot exactly once, plugin-relative", async () => {
  const view = harness();
  const requests = [];
  view.window.CompanionProduct = {bridgeNow: () => ({apiGet: async path => {
    requests.push(path);
    return guardSnapshot();
  }})};
  view.page.mount(view.root, {scopeKind: "private", harassmentApi: privateEndpoint});
  await settle();

  assert.match(view.root.innerHTML, /data-decision-tab="guard"/);
  assert.deepEqual(requests, ["host/targets/t1/roles/r1/reply/harassment"]);
  const markup = view.panels.get("guard").innerHTML;
  assert.match(markup, /启用防骚扰/);
  assert.doesNotMatch(markup, /data-guard-retry/);
  view.page.unmount();
});

test("harassment separates reading, read failure and not applicable", async () => {
  const view = harness();
  let release = () => {};
  view.window.CompanionProduct = {bridgeNow: () => ({apiGet: () => new Promise(resolve => {
    release = () => resolve(guardSnapshot());
  })})};
  view.page.mount(view.root, {scopeKind: "private", harassmentApi: privateEndpoint});

  assert.match(view.panels.get("guard").innerHTML, /aria-busy="true"/);
  assert.doesNotMatch(view.panels.get("guard").innerHTML, /data-guard-retry/);

  release();
  await settle();
  assert.doesNotMatch(view.panels.get("guard").innerHTML, /aria-busy/);
  view.page.unmount();

  const broken = harness();
  broken.window.CompanionProduct = {bridgeNow: () => ({apiGet: async () => {
    throw new Error("网络中断");
  }})};
  broken.page.mount(broken.root, {scopeKind: "private", harassmentApi: privateEndpoint});
  await settle();
  assert.match(broken.panels.get("guard").innerHTML, /data-guard-retry/);
  assert.match(broken.panels.get("guard").innerHTML, /暂时无法读取防骚扰设置/);
  broken.page.unmount();
});

test("a missing harassment route explains itself and offers no retry", async () => {
  /* 宿主匹配不到路由时回 {status:"error", message:"未找到该路由"}：新版桥把它
     抛成 Error，旧桥把整封回执原样交回来，带不带 data 都有可能；也有外层 ok、
     内层才是失败的双层形态。四种形态都不该出现「重试」或「请稍后重试」。 */
  const broke = apiGet => {
    const view = harness();
    view.window.CompanionProduct = {bridgeNow: () => ({apiGet})};
    view.page.mount(view.root, {scopeKind: "private", harassmentApi: privateEndpoint});
    return view;
  };
  const throwsBridge = broke(async () => {
    throw new Error("未找到该路由");
  });
  const resolvedEnvelope = broke(async () =>
    ({status: "error", message: "未找到该路由", data: {}}));
  /* 没有 data 的失败回执：旧代码会把它当成一份空快照。 */
  const bareEnvelope = broke(async () => ({status: "error", message: "未找到该路由"}));
  /* 外层 ok、内层失败：两层都要认，否则这张失败回执会被当成快照渲染空表单。 */
  const nestedEnvelope = broke(async () =>
    ({status: "ok", data: {status: "error", message: "未找到该路由"}}));
  await settle();

  for (const view of [throwsBridge, resolvedEnvelope, bareEnvelope, nestedEnvelope]) {
    const markup = view.panels.get("guard").innerHTML;
    assert.match(markup, /当前插件版本未提供防骚扰接口/);
    assert.match(markup, /只对私聊窗口提供/);
    assert.doesNotMatch(markup, /data-guard-retry/);
    assert.doesNotMatch(markup, /请稍后重试/);
    view.page.unmount();
  }
});
