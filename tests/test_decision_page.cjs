const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const pageRoot = path.resolve(__dirname, "../pages/陪伴小屋");
const source = fs.readFileSync(path.join(pageRoot, "decision-page.js"), "utf8");
const configSource = fs.readFileSync(path.join(pageRoot, "config-editor.js"), "utf8");

const CONFIG_API = "/astrbot_plugin_private_companion/config";
const REPLY_API = "/astrbot_plugin_private_companion/abc/reply";

/* 角色层挂载点解析出的取值（角色没设过时就是 registry 默认）。页面必须把它们
   读出来显示；在修好之前，这里放什么都不会出现在四张卡片上。 */
const PARTICIPATION_DEFAULTS = {enable_group_topic_threads: true, group_topic_thread_max: 12,
  enable_group_social_context: true, enable_group_mood_detection: true,
  enable_group_roleplay_strength: true, enable_group_moments: true,
  enable_group_moment_portrait: true, enable_group_joke_guard: true,
  enable_group_repeat_follow: true, group_repeat_trigger_threshold: 4,
  group_repeat_follow_probability: 0.18};
/* 角色层上报的是整组 28 个字段，其中插话三键是**角色层**的解析值
   （companion_v2/conversation/participation.py 的 CONFIG_DEFAULTS：默认关闭、
   间隔 180、每日上限 2）。角色层刷新/保存会重报这一整份。 */
const PARTICIPATION_ROLE_LAYER = {...PARTICIPATION_DEFAULTS, enable_group_interjection: false,
  group_interject_min_interval_minutes: 60, group_interject_max_daily: 0,
  enable_group_interjection_feedback: true, group_interject_feedback_window_seconds: 600};
/* 本群窗口覆盖：astrbot_host/group_reply_api.py 的 INTERJECTION_FIELDS，只有这 5 个键。 */
const PARTICIPATION_LOCAL = {enable_group_interjection: true,
  group_interject_min_interval_minutes: 180, group_interject_max_daily: 2,
  enable_group_interjection_feedback: true, group_interject_feedback_window_seconds: 600};

/* 真实适配层：加载生产 config-editor.js，只桩 window.CompanionWorkbench.create。
   实例句柄的键与 state 字段按 workbench-page.js:2735（返回对象）与
   :1117-1118（state 声明）的真实形状给，不再自造 state.draft。 */
function adapter(kinds = ["group_participation"], settings = null, extra = {}) {
  const window = {};
  vm.runInNewContext(configSource, {window});
  const instances = [];
  window.CompanionProduct = {
    role: () => ({config_api: CONFIG_API, reply_api: REPLY_API}),
    request: async () => ({sections: {}}),
  };
  window.CompanionWorkbench = {create(options) {
    const instance = {
      group: options.group,
      title: options.title,
      state: {status: settings ? {settings: {...settings}} : null,
        draft: settings ? {...settings} : null,
        base: settings ? {...settings} : null,
        loading: false, denied: false, bad: false, ...extra},
      visibleGroups: [],
      mount: () => null, attach() {}, stop() {}, start() {}, unmount() {},
      selectSection() {},
      refresh: async () => ({state: "ok"}),
      setVisibleGroups(ids, preferredId) { instance.visibleGroups.push({ids, preferredId}); },
      dirty: () => false, changedFields: () => [], save: async () => true,
    };
    instances.push(instance);
    return instance;
  }};
  const host = {classList: {add() {}}, textContent: "", appendChild() {}, querySelector: () => null};
  const editor = window.CompanionConfigEditors.create(kinds, () => host, {}, "");
  return {editor, instances, window};
}

/* 页面侧的挂载点：nodes 里放 {companionConfigEditor}，和真实 DOM 上挂的形状一致。 */
function harness(nodes = {}) {
  const panels = new Map(["overview", "response", "tempo", "safety", "guard", "group"]
    .map(view => [view, {dataset:{decisionPanel:view}, innerHTML:"", hidden:false}]));
  const current = {textContent:""};
  /* 页面在根节点上挂事件；留住它们，才能真的点一次「重新读取」。
     同一个类型上会挂多个监听器（分区切换、防骚扰面板各一个），不能只留最后一个。 */
  const listeners = new Map();
  const root = {innerHTML:"",dataset:{},querySelector(selector){
    if(selector==="#groupParticipationConfigWorkspace")return nodes.defaults||null;
    if(selector==="#groupInterjectionConfigWorkspace")return nodes.local||null;
    if(selector==='[data-decision-current]')return current;
    const panel=selector.match(/^\[data-decision-panel="([^"]+)"\]$/);
    return panel?panels.get(panel[1])||null:null;
  },querySelectorAll(selector){
    return selector==="[data-decision-panel]"?[...panels.values()]:[];
  },
    addEventListener(type, handler){
      const list = listeners.get(type) || [];
      if (!list.includes(handler)) list.push(handler);
      listeners.set(type, list);
    },
    removeEventListener(type, handler){
      listeners.set(type, (listeners.get(type) || []).filter(item => item !== handler));
    }};
  const window = {lucide:{createIcons(){}}};
  vm.runInNewContext(source, {window});
  return {page:window.CompanionDecisionPage,root,panels,current,nodes,listeners};
}

/* 真实取值：话题线/氛围/复读三段来自角色层挂载点，插话那段来自本群挂载点。 */
function livePage(roleSettings = PARTICIPATION_DEFAULTS, localSettings = PARTICIPATION_LOCAL) {
  const defaults = adapter(["group_participation"], roleSettings);
  const local = adapter(["group_participation"], localSettings);
  const view = harness({defaults:{companionConfigEditor:defaults.editor},
    local:{companionConfigEditor:local.editor}});
  view.page.mount(view.root, {groupSettingsMarkup:'<section id="groupParticipationConfigWorkspace"></section>'});
  view.page.setView("group");
  return {...view, defaults, local};
}

/* 四张卡片在同一个网格里，按 <article> 切开，才能逐张断言**它自己的来源**。 */
function cards(html) {
  return [...html.matchAll(/<article class="decision-stat[^"]*">([\s\S]*?)<\/article>/g)].map(match => {
    const body = match[1];
    return {
      label: body.match(/<span>([^<]*)<\/span>/)?.[1] || "",
      value: body.match(/<strong>([\s\S]*?)<\/strong>/)?.[1] ?? "",
      note: body.match(/<small>([\s\S]*?)<\/small>/)?.[1] ?? "",
    };
  });
}
const cardsByLabel = html => Object.fromEntries(cards(html).map(card => [card.label, card]));

/* 两处挂载点各自的读取状态：ready 有值、loading 在途、failed 读失败
   （内核读取失败时把实例标成 denied 并清空 draft/base，见 workbench-page.js）。 */
const SOURCE_STATES = {
  role: {ready: PARTICIPATION_ROLE_LAYER, loading: null, failed: null},
  window: {ready: PARTICIPATION_LOCAL, loading: null, failed: null},
};
function sourcePage(rolePhase, windowPhase) {
  const extra = phase => phase === "failed" ? {denied: true, bad: true} : {};
  const defaults = adapter(["group_participation"], SOURCE_STATES.role[rolePhase], extra(rolePhase));
  const local = adapter(["group_participation"], SOURCE_STATES.window[windowPhase], extra(windowPhase));
  const view = harness({defaults:{companionConfigEditor:defaults.editor},
    local:{companionConfigEditor:local.editor}});
  view.page.mount(view.root, {groupSettingsMarkup:'<section id="groupParticipationConfigWorkspace"></section>'});
  view.page.setView("group");
  return {...view, defaults, local};
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
  const groupEditor = {state:{instances:[]}, visibleGroups:[],
    setVisibleGroups(groups){this.visibleGroups=groups;}};
  view.nodes.defaults = {companionConfigEditor:groupEditor};
  view.page.mount(view.root, {groupSettingsMarkup:'<section id="groupParticipationConfigWorkspace"></section>'});
  view.page.setView("group");

  assert.deepEqual(Array.from(groupEditor.visibleGroups), ["topics", "atmosphere", "repeat"]);
  view.page.unmount();
});

test("the four participation cards show the values the editors actually read", () => {
  const view = livePage();
  const html = view.panels.get("group").innerHTML;

  for (const card of ["群聊话题线", "氛围参考", "主动插话", "复读跟读"])
    assert.match(html, new RegExp(card));
  assert.match(html, /最多保留 12 条 · 90 分钟无更新后失效/);
  assert.match(html, /4 \/ 4 开/);
  assert.match(html, /间隔 180 分钟 · 每日上限 2 次/);
  assert.match(html, /阈值 4 人次 · 概率 0.18/);
  /* 有值的时候四张卡片都不该再出现占位符，也不该出现那句含混的提示。 */
  assert.doesNotMatch(html, /<strong>—<\/strong>/);
  assert.doesNotMatch(html, /读取配置后显示/);
  assert.match(view.panels.get("overview").innerHTML, /氛围参考/);
  assert.match(view.panels.get("overview").innerHTML, /话题线 开启/);
  view.page.unmount();
});

test("the configuration adapter keeps only instances on its state", () => {
  const {editor, instances} = adapter(["group_participation"], PARTICIPATION_DEFAULTS);

  assert.deepEqual(Object.keys(editor.state), ["instances"]);
  assert.equal(editor.state.instances.length, 1);
  assert.equal(editor.state.instances[0], instances[0]);
  assert.equal(editor.state.instances[0].state.draft.enable_group_topic_threads, true);
});

test("the status reported by the editors lights up the participation cards", () => {
  const view = harness();
  view.page.mount(view.root, {groupSettingsMarkup:'<section id="groupParticipationConfigWorkspace"></section>'});
  view.page.setView("group");
  assert.match(view.panels.get("group").innerHTML, /未选择群聊/);

  /* 两处挂载点各自上报自己那一段；来源既可由调用方点名，也在上报表的 scope 上。 */
  view.page.updateParticipationStatus({scope: "role", settings: {...PARTICIPATION_ROLE_LAYER}}, "role");
  view.page.updateParticipationStatus({scope: "window", settings: {...PARTICIPATION_LOCAL}}, "window");
  const html = view.panels.get("group").innerHTML;
  assert.match(html, /最多保留 12 条 · 90 分钟无更新后失效/);
  assert.match(html, /间隔 180 分钟 · 每日上限 2 次/);
  assert.match(html, /阈值 4 人次 · 概率 0.18/);
  assert.doesNotMatch(html, /未选择群聊|读取配置后显示/);
  view.page.unmount();
});

/* 缺陷一：两处挂载点写同一个槽时，角色层任何一次刷新/保存（重报整组 28 个
   字段，插话三键是角色层值）都会把本群窗口的覆盖值打回角色层默认。 */
test("a later role-layer report cannot roll back this group's window overrides", () => {
  const view = sourcePage("loading", "loading");
  const report = (settings, mount) => view.page.updateParticipationStatus({settings}, mount);
  const interjection = () => cardsByLabel(view.panels.get("group").innerHTML)["主动插话"];

  report({...PARTICIPATION_ROLE_LAYER}, "role");
  report({...PARTICIPATION_LOCAL}, "window");
  assert.equal(interjection().value, "开启");
  assert.match(interjection().note, /间隔 180 分钟 · 每日上限 2 次/);

  /* 角色层保存后重报：这一份里的插话三键是「关闭 · 60 分钟 · 每日 0 次」。 */
  report({...PARTICIPATION_ROLE_LAYER}, "role");
  assert.equal(interjection().value, "开启");
  assert.match(interjection().note, /间隔 180 分钟 · 每日上限 2 次/);
  assert.doesNotMatch(interjection().note, /该能力未开启/);

  /* 反向到达顺序同样成立：合并按来源定优先级，不按到达先后。 */
  const reversed = sourcePage("loading", "loading");
  const reportReversed = (settings, mount) =>
    reversed.page.updateParticipationStatus({settings}, mount);
  reportReversed({...PARTICIPATION_LOCAL}, "window");
  reportReversed({...PARTICIPATION_ROLE_LAYER}, "role");
  const reversedCard = cardsByLabel(reversed.panels.get("group").innerHTML)["主动插话"];
  assert.equal(reversedCard.value, "开启");
  assert.match(reversedCard.note, /间隔 180 分钟 · 每日上限 2 次/);
  reversed.page.unmount();
  view.page.unmount();
});

/* 缺陷二：一侧读成功、另一侧读失败时，失败那侧的卡只能说「读取失败」，
   不能借「该能力未开启」把「读不到」说成「没开」。 */
test("one unreadable source reports a read failure without claiming the ability is off", () => {
  const view = sourcePage("failed", "ready");
  const byLabel = cardsByLabel(view.panels.get("group").innerHTML);

  for (const label of ["群聊话题线", "氛围参考", "复读跟读"]) {
    assert.equal(byLabel[label].value, "—");
    assert.match(byLabel[label].note, /读取失败/);
    assert.match(byLabel[label].note, /data-participation-retry="role"/);
    assert.match(byLabel[label].note, /重新读取/);
  }
  assert.equal(byLabel["主动插话"].value, "开启");
  assert.match(byLabel["主动插话"].note, /间隔 180 分钟 · 每日上限 2 次/);
  assert.doesNotMatch(view.panels.get("group").innerHTML, /该能力未开启/);

  /* 重新读取只重读失败的那一处，另一处不该被连带重读。 */
  let roleReads = 0, windowReads = 0;
  const roleRead = view.defaults.editor.read.bind(view.defaults.editor);
  const windowRead = view.local.editor.read.bind(view.local.editor);
  view.defaults.editor.read = (...args) => { roleReads += 1; return roleRead(...args); };
  view.local.editor.read = (...args) => { windowReads += 1; return windowRead(...args); };
  view.listeners.get("click").forEach(handler => handler({target:{closest:selector =>
    selector === "[data-participation-retry]" ? {dataset:{participationRetry:"role"}} : null}}));
  assert.equal(roleReads, 1);
  assert.equal(windowReads, 0);
  view.page.unmount();

  /* 反过来：角色层读成功、本群窗口读失败时，只有「主动插话」这一张说读取失败。 */
  const mirrored = sourcePage("ready", "failed");
  const mirroredCards = cardsByLabel(mirrored.panels.get("group").innerHTML);
  assert.equal(mirroredCards["群聊话题线"].value, "开启");
  assert.match(mirroredCards["群聊话题线"].note, /最多保留 12 条/);
  assert.equal(mirroredCards["复读跟读"].value, "开启");
  assert.equal(mirroredCards["主动插话"].value, "—");
  assert.match(mirroredCards["主动插话"].note, /读取失败/);
  assert.match(mirroredCards["主动插话"].note, /data-participation-retry="window"/);
  assert.doesNotMatch(mirrored.panels.get("group").innerHTML, /该能力未开启/);
  mirrored.page.unmount();
});

/* 缺陷二（矩阵）：四张卡各自的来源与三态，逐格对一遍。来源按配置分区声明：
   话题线 / 氛围 / 复读在角色层挂载点，主动插话在本群窗口挂载点。 */
test("each participation card follows the state of its own source", () => {
  const expectation = (phase, mount) => phase === "ready"
    ? {value: value => value !== "—", note: /^(?!.*(读取失败|正在读取|该能力未开启|未选择群聊)).*$/s}
    : phase === "loading"
      ? {value: value => value === "—", note: /<span aria-busy="true">正在读取…<\/span>/}
      : {value: value => value === "—", note: new RegExp(`^读取失败 <button[^>]*data-participation-retry="${mount}"` +
          `[^>]*>重新读取</button>$`)};
  for (const rolePhase of ["ready", "loading", "failed"]) {
    for (const windowPhase of ["ready", "loading", "failed"]) {
      const view = sourcePage(rolePhase, windowPhase);
      const html = view.panels.get("group").innerHTML;
      const rendered = cards(html);
      assert.deepEqual(rendered.map(card => card.label), ["群聊话题线", "氛围参考", "主动插话", "复读跟读"],
        `role=${rolePhase} window=${windowPhase}`);
      for (const [index, mount, phase] of [[0, "role", rolePhase], [1, "role", rolePhase],
        [2, "window", windowPhase], [3, "role", rolePhase]]) {
        const want = expectation(phase, mount);
        assert.equal(rendered[index].label, ["群聊话题线", "氛围参考", "主动插话", "复读跟读"][index]);
        assert.ok(want.value(rendered[index].value),
          `role=${rolePhase} window=${windowPhase} card=${rendered[index].label} value=${rendered[index].value}`);
        assert.match(rendered[index].note, want.note,
          `role=${rolePhase} window=${windowPhase} card=${rendered[index].label} note=${rendered[index].note}`);
      }
      view.page.unmount();
    }
  }
});

test("participation cards tell loading, failure and no-group-window apart", () => {
  const loadingView = sourcePage("loading", "loading");
  const loadingHtml = loadingView.panels.get("group").innerHTML;
  assert.equal(cards(loadingHtml).length, 4);
  for (const card of cards(loadingHtml)) {
    assert.equal(card.value, "—");
    assert.match(card.note, /aria-busy="true"/);
    assert.match(card.note, /正在读取…/);
  }
  assert.doesNotMatch(loadingHtml, /读取配置后显示|读取失败|未选择群聊|该能力未开启/);
  loadingView.page.unmount();

  const failedView = sourcePage("failed", "failed");
  const failedHtml = failedView.panels.get("group").innerHTML;
  for (const card of cards(failedHtml)) {
    assert.equal(card.value, "—");
    assert.match(card.note, /读取失败/);
    assert.match(card.note, /重新读取/);
  }
  assert.match(failedHtml, /data-participation-retry="role"/);
  assert.match(failedHtml, /data-participation-retry="window"/);
  assert.doesNotMatch(failedHtml, /读取配置后显示|正在读取|未选择群聊|该能力未开启/);
  let reads = 0;
  const read = failedView.local.editor.read.bind(failedView.local.editor);
  failedView.local.editor.read = (...args) => { reads += 1; return read(...args); };
  failedView.listeners.get("click").forEach(handler => handler({target:{closest:selector =>
    selector === "[data-participation-retry]" ? {dataset:{participationRetry:"window"}} : null}}));
  assert.equal(reads, 1);
  failedView.page.unmount();

  const emptyView = harness();
  emptyView.page.mount(emptyView.root,
    {groupSettingsMarkup:'<section id="groupParticipationConfigWorkspace"></section>'});
  emptyView.page.setView("group");
  const emptyHtml = emptyView.panels.get("group").innerHTML;
  assert.match(emptyHtml, /未选择群聊/);
  assert.doesNotMatch(emptyHtml, /读取配置后显示|正在读取|读取失败|阈值 — 人次/);
  emptyView.page.unmount();

  /* 没有群窗口时，只有本群那一段缺席：角色层三张卡照常显示值，
     「主动插话」说「未选择群聊」，而不是一句「该能力未开启」。 */
  const noGroup = harness({defaults:{companionConfigEditor:
    adapter(["group_participation"], PARTICIPATION_DEFAULTS).editor}});
  noGroup.page.mount(noGroup.root,
    {groupSettingsMarkup:'<section id="groupParticipationConfigWorkspace"></section>'});
  noGroup.page.setView("group");
  const noGroupCards = cardsByLabel(noGroup.panels.get("group").innerHTML);
  assert.equal(noGroupCards["群聊话题线"].value, "开启");
  assert.equal(noGroupCards["复读跟读"].value, "开启");
  assert.equal(noGroupCards["主动插话"].value, "—");
  assert.equal(noGroupCards["主动插话"].note, "未选择群聊");
  assert.doesNotMatch(noGroup.panels.get("group").innerHTML, /该能力未开启/);
  noGroup.page.unmount();
});

test("setVisibleGroups reaches every configuration instance", () => {
  const {editor, instances} = adapter(["group_participation", "quota"], PARTICIPATION_DEFAULTS);

  assert.equal(instances.length, 2);
  editor.setVisibleGroups(["topics", "repeat"], "topics");
  for (const instance of instances)
    assert.deepEqual(instance.visibleGroups, [{ids:["topics", "repeat"], preferredId:"topics"}]);
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
