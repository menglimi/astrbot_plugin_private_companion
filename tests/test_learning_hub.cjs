"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.join(__dirname, "..");
const PAGE = path.join(ROOT, "pages", "陪伴小屋");
const HOST = "astrbot_plugin_private_companion";
const {assertStylesheetBundled} = require("./page-styles.cjs");

test("学习工作台按导航加载每个真实数据模块", () => {
  const app = fs.readFileSync(path.join(PAGE, "app.js"), "utf8");
  const html = fs.readFileSync(path.join(PAGE, "index.html"), "utf8");
  const review = fs.readFileSync(path.join(PAGE, "self-review-page.js"), "utf8");
  const learningReview = fs.readFileSync(path.join(PAGE, "learning-review-page.js"), "utf8");
  const overview = fs.readFileSync(path.join(PAGE, "learning-overview.js"), "utf8");
  for (const section of ["overview", "strategy", "review", "chain", "expression", "behavior", "semantic", "reflection"]) {
    assert.match(app, new RegExp(`\\['${section}',`));
  }
  assert.match(app, /id="selfReviewWorkspace"/);
  assert.match(app, /id="behaviorLearningWorkspace"/);
  assert.match(html, /behavior-page\.js/);
  assert.match(app, /sectionIds:\['behavior'\]/);
  assert.match(app, /CompanionSelfReviewPage\?\.mount\(selfReviewHost,\{sources:reviewSources,state:'active'\}\)/);
  const rendererStart = app.indexOf('function renderLearningPage(){');
  const rendererEnd = app.indexOf('/* 2026-09-30 接回：', rendererStart);
  const renderer = app.slice(rendererStart, rendererEnd);
  assert.doesNotMatch(renderer, /data-learning-scope-target|learningScopeControls|learningRoleControls/);
  const strategyStart = renderer.indexOf("learningTab==='strategy'");
  const reviewStart = renderer.indexOf("learningTab==='review'", strategyStart);
  assert.ok(strategyStart >= 0 && reviewStart > strategyStart);
  assert.doesNotMatch(renderer.slice(strategyStart, reviewStart),
    /learningScopeControls|learningRoleControls|roleConfigApi/);
  assert.doesNotMatch(app, /growthApi:skillScope\.role\.growth_api/);
  assert.match(app, /learningReviewSources\(\)/);
  assert.match(app, /sources:learningReviewSources\(\{includeGroups:true\}\),semantic:\{global:true\}/);
  assert.match(app, /CompanionLearningOverviewPage\?\.mount/);
  assert.match(app, /CompanionLearningReviewPage\?\.mount/);
  assert.match(app, /globalSettingsOnly:true/);
  assert.match(app, /endpoint=roleConfigApi\(current\)/);
  assert.match(app, /configEditors\('expressionLearningConfigWorkspace',\['expression.learning'\]/);
  assert.match(review, /state:"active"/);
  assert.match(review, /state:"decided"/);
  assert.doesNotMatch(review, /CompanionProduct\.role|CompanionProduct\.target/);
  assertStylesheetBundled("self-review-page.css");
  assert.match(html, /self-review-page\.js/);
  assertStylesheetBundled("learning-hub.css");
  assert.match(html, /learning-overview\.js/);
  assert.match(html, /learning-review-page\.js/);
  assert.match(html, /expression-signals-page\.js/);
  assert.doesNotMatch(learningReview, /session_id=/);
  assert.doesNotMatch(overview, /session_id=/);
});

test("每日复盘汇总多个数据来源中仍有效的指导及真实运行参数", async () => {
  const pageSource = fs.readFileSync(path.join(PAGE, "self-review-page.js"), "utf8");
  const makeReview = (id, state = "accepted", extra = {}) => ({
    item_id:id, run_id:"run-" + id, kind:"try_next_time", state,
    observation:"完成记录与回复结论有一处不一致 <script>",
    next_action:"记录前再核对一次完成状态。", confidence:0.82,
    expires_at:new Date(Date.now() + 86400000).toISOString(),
    created_at:new Date().toISOString(), summary:"有一条结论需要依据补齐。",
    evidence:["可核验的插件工作回执。"],
    automatic:extra.automatic===true, unavailable_reason:extra.unavailable_reason||null,
    actions:state === "pending" ? ["accept", "reject"] : [], view_token:"view-" + id,
    ...extra,
  });
  const getCalls = [];
  const sources = [
    {api:`/${HOST}/targets/profile-a/roles/role-a/growth`,key:"a",label:"对象 A · 角色 A"},
    {api:`/${HOST}/targets/profile-b/roles/role-b/growth`,key:"b",label:"对象 B · 角色 B"},
  ];
  const bridge = {
    ready:async () => ({pluginName:HOST}),
    apiGet:async (route, query) => {
      getCalls.push({route, query});
      const id = route.includes("profile-b") ? "review-b" : "review-a";
      const items=query.state==="active"
        ?[makeReview(id,"accepted",{automatic:id==="review-a"})]
        :[makeReview("expired-"+id,"accepted",{expires_at:new Date(Date.now()-3600000).toISOString()})];
      return {status:"ok",data:{items,next_cursor:null,expired_in_page:0,
        policy:{window_hours:24,scan_seconds:86400,empty_retry_seconds:3600,minimum_evidence:1}}};
    },
  };
  const window = {
    AstrBotPluginPage:bridge,
    CompanionProduct:{
      bridgeNow:() => bridge,
      target:() => ({scope_kind:"group"}),
      role:() => ({growth_api:`/${HOST}/current-chat/growth`}),
    },
  };
  const context = vm.createContext({window, setTimeout, clearTimeout});
  vm.runInContext(pageSource, context);
  const listeners = new Map();
  const root = {
    isConnected:true, innerHTML:"",
    addEventListener:(name, callback) => listeners.set(name, callback),
    removeEventListener:(name) => listeners.delete(name),
  };
  await context.window.CompanionSelfReviewPage.mount(root, {sources,state:"active"});
  assert.deepEqual(getCalls.map(call => `${call.route}:${call.query.state}`).sort(), [
    "targets/profile-a/roles/role-a/growth/self-review:active",
    "targets/profile-a/roles/role-a/growth/self-review:decided",
    "targets/profile-b/roles/role-b/growth/self-review:active",
    "targets/profile-b/roles/role-b/growth/self-review:decided",
  ]);
  assert.match(root.innerHTML, /对象 A · 角色 A/);
  assert.match(root.innerHTML, /对象 B · 角色 B/);
  assert.match(root.innerHTML, /复盘周期/);
  assert.match(root.innerHTML, /记录归属/);
  assert.doesNotMatch(root.innerHTML, /复盘窗口|角色范围/);
  assert.match(root.innerHTML, /至少 1 条/);
  assert.match(root.innerHTML, /空结果重查/);
  assert.match(root.innerHTML, /生效中的自我指导/);
  assert.match(root.innerHTML, /自动应用/);
  assert.match(root.innerHTML, /已过期/);
  assert.match(root.innerHTML, /引用依据 · 1 条/);
  assert.match(root.innerHTML, /可核验的插件工作回执/);
  assert.doesNotMatch(root.innerHTML, /<script>/);
  assert.equal(root.innerHTML.includes("data-review-action=\"accept\""),false);
  context.window.CompanionSelfReviewPage.unmount();
  assert.equal(listeners.size, 0);
});

test("审阅中心批量通过逐条走进度、表达和指导的原始命令接口", async () => {
  const source=fs.readFileSync(path.join(PAGE,"learning-review-page.js"),"utf8");
  const now=new Date().toISOString();
  const posts=[];
  const growth={review_id:"growth-review",growth_title:"练习记录",progress_note:"完成三次练习",base_revision:3,
    created_at:now,actions:["accept","reject"],view_token:"growth-token",evidence:["练习回执"]};
  const style={signal_id:"style-signal",proposal:"缩短句子",observation:"连续回复较短",dimension:"sentence_rhythm",
    created_at:now,actions:["adopt","reject"],view_token:"style-token",evidence:["已送达回复"]};
  const self={item_id:"self-review",kind:"ask_user",observation:"计划仍未确认",next_action:"询问用户是否继续",
    created_at:now,actions:["accept","reject"],view_token:"self-token",evidence:["日程状态"]};
  const bridge={
    ready:async()=>({pluginName:HOST}),
    apiGet:async route=>({status:"ok",data:{items:route.endsWith("/progress")?[growth]
      :route.endsWith("/style")?[style]:[self]}}),
    apiPost:async(route,body)=>{posts.push({route,body});return {status:"ok",data:{state:"accepted"}};},
  };
  const window={AstrBotPluginPage:bridge,CompanionProduct:{bridgeNow:()=>bridge}};
  const context=vm.createContext({window,setTimeout,clearTimeout});
  vm.runInContext(source,context);
  const listeners=new Map();
  const root={isConnected:true,innerHTML:"",addEventListener:(name,callback)=>listeners.set(name,callback),
    removeEventListener:name=>listeners.delete(name)};
  const click=(selector,dataset={})=>listeners.get("click")?.({target:{closest:value=>value===selector?{dataset}:null}});
  const changeAll=()=>{
    const target={checked:true,closest:selector=>selector==="[data-review-check-all]"?target:null};
    listeners.get("change")?.({target});
  };
  const flush=()=>new Promise(resolve=>setTimeout(resolve,0));
  const waitFor=async predicate=>{
    for(let attempt=0;attempt<50;attempt++){
      if(predicate())return;
      await flush();
    }
    assert.fail("learning review data did not settle");
  };
  await context.window.CompanionLearningReviewPage.mount(root,{sources:[{
    key:"role-a",label:"私聊 · 角色 A",growthApi:`/${HOST}/targets/profile-a/roles/role-a/growth`,
    replyApi:`/${HOST}/targets/profile-a/roles/role-a/reply`,
  }]});
  await waitFor(()=>context.window.CompanionLearningReviewPage.state?.loading===false);
  changeAll();
  click("[data-review-bulk]",{reviewBulk:"accept"});
  click("[data-review-confirm]",{reviewConfirm:"yes"});
  await flush();await flush();
  assert.equal(posts.length,3);
  assert.deepEqual(posts.map(item=>[item.route,item.body.action]).sort(),[
    ["targets/profile-a/roles/role-a/growth/progress/command","accept"],
    ["targets/profile-a/roles/role-a/reply/style/command","adopt"],
    ["targets/profile-a/roles/role-a/growth/self-review/command","accept"],
  ].sort());
  assert.equal(posts.find(item=>item.body.signal_id)?.body.view_token,"style-token");
  assert.equal(posts.find(item=>item.body.review_id)?.body.expected_revision,3);
  context.window.CompanionLearningReviewPage.unmount();
  assert.equal(listeners.size,0);
});

test('学习记录与表情库只跟随小屋的 Bot 和角色，成长记录要求显式窗口', () => {
  const app = fs.readFileSync(path.join(PAGE, 'app.js'), 'utf8');
  const role = id => ({role_id: id, reply_api: '/' + id + '/reply', growth_api: '/' + id + '/growth'});
  const targets = [
    {bot_id: 'bot-a', target_id: 'a-private', scope_kind: 'private', roles: [role('same-role')]},
    {bot_id: 'bot-a', target_id: 'a-group', scope_kind: 'group', roles: [role('same-role')]},
    {bot_id: 'bot-b', target_id: 'b-private', scope_kind: 'private', roles: [role('same-role')]},
    {bot_id: 'bot-a', target_id: 'c-private', scope_kind: 'private', roles: [role('other-role')]},
  ];
  let selected = {bot_id: 'bot-a', role_id: 'same-role', target_id: null};
  const product = {
    targets: () => targets, role: () => role(selected.role_id),
    roleTarget: () => targets.find(target => target.bot_id === selected.bot_id && target.roles.some(item => item.role_id === selected.role_id)),
    selection: () => selected,
    roleSelection: () => ({...selected, target_id: product.roleTarget().target_id}),
    targetLabel: target => target.target_id, roleLabel: item => item.role_id,
    choose: (target_id, role_id) => { selected = {...selected, target_id, role_id}; return true; },
    clearWindow: () => { selected.target_id = null; return true; },
  };
  const context = vm.createContext({product, window: {CompanionProduct: product, addEventListener() {}}});
  vm.runInContext(app.slice(app.indexOf('function learningRoleApi('), app.indexOf('function renderLearningPage(')), context);
  assert.equal(context.learningScope('expression').target.target_id, 'a-private');
  assert.equal(context.learningScope('growth'), null);
  assert.deepEqual(Array.from(context.learningReviewSources({includeGroups: true}), source => source.target.target_id), ['a-private', 'a-group']);
  assert.deepEqual(Array.from(context.learningExpressionSources(), source => source.target.target_id), ['a-private', 'a-group']);
  product.choose('a-private', 'same-role');
  assert.equal(context.learningScope('growth').target.target_id, 'a-private');
  selected = {bot_id: 'bot-b', role_id: 'same-role', target_id: null};
  assert.equal(context.learningScope('expression').target.target_id, 'b-private');
  assert.deepEqual(Array.from(context.learningReviewSources({includeGroups: true}), source => source.target.target_id), ['b-private']);
  assert.equal(context.learningChoices('growth').some(item => item.target.target_id === 'a-private'), false);
});

test('学习工作台顶部 8 Tab 胶囊导航、总览间距语义色与角色策略政策卡片契约', () => {
  const app = fs.readFileSync(path.join(PAGE, 'app.js'), 'utf8');
  const hubCss = fs.readFileSync(path.join(PAGE, 'learning-hub.css'), 'utf8');
  const pageCss = fs.readFileSync(path.join(PAGE, 'learning-page.css'), 'utf8');
  const policyCss = fs.readFileSync(path.join(PAGE, 'learning-policy.css'), 'utf8');

  // 1. 顶部 8 Tab 去掉 01..08 编号前缀，外框圆角与内层滚动轨解耦，消除悬停双选中错觉
  const rendererStart = app.indexOf('function renderLearningPage(){');
  const rendererEnd = app.indexOf('/* 2026-09-30 接回：', rendererStart);
  const renderer = app.slice(rendererStart, rendererEnd);
  assert.doesNotMatch(renderer, /padStart\(2,'0'\)/, '学习工作台顶部 8 Tab 不应再带 01..08 序号前缀');
  assert.match(renderer, /<nav class="learning-hub-nav"[^>]*><div class="learning-hub-tabs">/);
  assert.doesNotMatch(hubCss, /\.learning-hub-nav\{[^}]*mask-image/, '不得对带边框圆角的外壳 .learning-hub-nav 直接施以 mask-image');
  assert.match(hubCss, /\.learning-hub-tabs\{[^}]*display:flex;flex-wrap:nowrap[^}]*overflow-x:auto/);
  assert.match(hubCss, /\.learning-hub-tabs\{[\s\S]*?mask-image:linear-gradient\(to right,black 0,black calc\(100% - 22px\),transparent 100%\)/);
  assert.match(hubCss, /\.learning-hub-tab:hover:not\(\.is-active\)\{[^}]*color-mix\(in srgb,var\(--t-accent\) 9%,transparent\)/);
  assert.doesNotMatch(hubCss, /\.learning-hub-tab:hover\{background:var\(--t-surface\)/);
  assert.doesNotMatch(pageCss, /\.learning-hub-tab:hover\{background:var\(--t-surface\)/);

  // 2. 总览容器拼写修复与语义状态色
  assert.doesNotMatch(hubCss, /\.lh-overview-workspace\b/, '必须修复 .lh-overview-workspace 拼写错误');
  assert.match(hubCss, /\.learning-overview-workspace\{display:flex;flex-direction:column;gap:var\(--sp-3\);min-width:0\}/);
  assert.match(hubCss, /\.lh-inline-status\.is-ok\{[^}]*background:var\(--t-ok-quiet\);color:var\(--t-ok-ink\)/);

  // 3. 角色策略停用 journal 与 outputConfigView，改用 policy 卡片、静态底栏、双向避让悬浮胶囊与移动端单行 3 等分分段控件
  assert.doesNotMatch(app, /'role\.growth\.skills':outputConfigView/);
  assert.doesNotMatch(app, /'expression\.learning':outputConfigView/);
  assert.match(app, /'role\.growth\.skills':\{[\s\S]*?presentation:'policy'[\s\S]*?listFields:\{custom_skills:\{maxItems:8,maxLength:24/);
  assert.match(app, /'expression\.learning':\{[\s\S]*?presentation:'policy'/);
  assert.match(renderer, /id="learningPolicySaveDock" class="learning-policy-dock"/);
  assert.match(policyCss, /\.learning-policy-workspaces\{[^}]*padding-bottom:96px/);
  assert.match(policyCss, /\.learning-policy-workspaces \.wb-policy \.wb-field-main\{[^}]*align-items:stretch/);
  assert.match(policyCss, /\.learning-policy-workspaces \.wb-policy \.wb-field-head\{[^}]*width:100%/);
  assert.match(policyCss, /\.learning-policy-workspaces \.wb-policy \.wb-label\{[^}]*justify-content:flex-start[^}]*text-align:left/);
  assert.match(policyCss, /\.learning-policy-workspaces \.wb-actions\{[^}]*position:static/);
  assert.match(policyCss, /#homeShell\[data-away="true"\] \.learning-policy-workspaces \.wb-actions\{padding-right:0\}/);
  assert.match(policyCss, /\.learning-policy-dock\{[^}]*--lp-dock-left:[^}]*--lp-dock-right:[^}]*max-width:560px/);
  assert.match(policyCss, /@media\(min-width:1121px\)\{[\s\S]*?--lp-dock-left:calc\(var\(--shell-rail, 220px\)/);
  assert.match(policyCss, /@media\(min-width:761px\) and \(max-width:1120px\)\{[\s\S]*?--lp-dock-left:calc\(var\(--shell-rail-collapsed, 68px\)/);
  assert.match(policyCss, /@media\(max-width:760px\)\{[\s\S]*?\.learning-policy-workspaces \.wb-policy \.wb-card-body\{padding:0\}/);
  assert.match(policyCss, /@media\(max-width:760px\)\{[\s\S]*?\.learning-policy-workspaces \.wb-policy \.wb-segmented\{[^}]*grid-auto-flow:column;grid-auto-columns:minmax\(0,1fr\)/);
});

test('角色策略居中悬浮保存胶囊仅在有未保存改动时出现，重复编辑原地复用节点不重播动画，并可联动放弃与保存', async () => {
  const app = fs.readFileSync(path.join(PAGE, 'app.js'), 'utf8');
  const start = app.indexOf('let learningPolicyDockCleanup=null;');
  const end = app.indexOf('function renderConfigEditors(){', start);
  assert.ok(start >= 0 && end > start);

  let dirtyList = [];
  let discarded = [];
  let saved = [];
  const windowListeners = new Map();
  const dockListeners = new Map();
  const classes = new Set();

  function makeNode(tag) {
    const node = {
      tagName: tag.toUpperCase(),
      className: '',
      type: '',
      dataset: {},
      attributes: {},
      children: [],
      parentNode: null,
      hidden: false,
      disabled: false,
      isConnected: true,
      _text: '',
      get textContent() {
        return this.children.length ? this.children.map(child => child.textContent).join(' ') : this._text;
      },
      set textContent(value) {
        this._text = String(value ?? '');
        if (this.children.length) {
          for (const child of this.children) child.parentNode = null;
          this.children = [];
        }
      },
      setAttribute(name, value) { this.attributes[name] = String(value); },
      appendChild(child) {
        child.parentNode = this;
        this.children.push(child);
        return child;
      },
      remove() {
        if (!this.parentNode) return;
        this.parentNode.children = this.parentNode.children.filter(item => item !== this);
        this.parentNode = null;
      },
      find(predicate) {
        for (const child of this.children) {
          if (predicate(child)) return child;
          const nested = child.find(predicate);
          if (nested) return nested;
        }
        return null;
      },
    };
    return node;
  }

  const dock = makeNode('div');
  dock.hidden = true;
  dock.classList = {
    add: name => classes.add(name),
    remove: name => classes.delete(name),
    toggle: (name, flag) => (flag ? classes.add(name) : classes.delete(name)),
  };
  dock.addEventListener = (type, fn) => dockListeners.set(type, fn);
  dock.removeEventListener = type => dockListeners.delete(type);

  const context = vm.createContext({
    configEditorMounts: [],
    roleProfileConfigMount: null,
    document: {createElement: makeNode},
    $: selector => (selector === '#learningPolicySaveDock' ? dock : null),
    escapeHTML: value => String(value ?? ''),
    window: {
      CompanionWorkbench: {dirtyEditors: () => dirtyList},
      addEventListener: (type, fn) => windowListeners.set(type, fn),
      removeEventListener: type => windowListeners.delete(type),
    },
  });
  vm.runInContext(app.slice(start, end), context);
  context.mountLearningPolicyDock();

  // 无修改时保持隐藏且不创建内层节点
  assert.equal(dock.hidden, true);
  assert.equal(dock.children.length, 0);

  const makeDirty = (group, title, fields) => ({
    group, title, fields,
    discard: () => { discarded.push(group); dirtyList = dirtyList.filter(item => item.group !== group); return true; },
    save: async () => { saved.push(group); dirtyList = dirtyList.filter(item => item.group !== group); return true; },
  });

  // 首次编辑：挂载一次 .learning-policy-dock-inner
  dirtyList = [
    makeDirty('role.growth.skills', '技能与日程影响', ['启用技能域']),
    makeDirty('unrelated.section', '其他页面', ['字段']),
  ];
  windowListeners.get('companion:workbench-state')?.();
  assert.equal(dock.hidden, false);
  assert.equal(classes.has('is-visible'), true);
  const firstInner = dock.find(node => node.className === 'learning-policy-dock-inner');
  assert.ok(firstInner, '首次进入脏态应创建 .learning-policy-dock-inner');
  assert.equal(dock.find(node => node.tagName === 'STRONG').textContent, '已修改 1 项');
  assert.equal(dock.find(node => node.dataset.policyDock === 'save').textContent, '保存更改');

  // 后续继续编辑：重复触发 companion:workbench-state 必须原地复用同一 .learning-policy-dock-inner 节点
  dirtyList = [
    makeDirty('role.growth.skills', '技能与日程影响', ['启用技能域', '自定义技能域']),
    makeDirty('expression.learning', '表达学习策略', ['学习强度']),
    makeDirty('unrelated.section', '其他页面', ['字段']),
  ];
  windowListeners.get('companion:workbench-state')?.();
  const secondInner = dock.find(node => node.className === 'learning-policy-dock-inner');
  assert.strictEqual(secondInner, firstInner, '重复编辑必须复用同一 .learning-policy-dock-inner 节点，禁止重播入场动画与丢焦点');
  assert.equal(dock.find(node => node.tagName === 'STRONG').textContent, '已修改 3 项');
  assert.match(dock.textContent, /技能与日程影响、表达学习策略/);
  assert.doesNotMatch(dock.textContent, /其他页面/);
  assert.equal(dock.find(node => node.dataset.policyDock === 'save').textContent, '保存全部更改');

  // 放弃修改：清掉策略卡草稿并隐藏胶囊
  await dockListeners.get('click')?.({
    target: {closest: sel => (sel === '[data-policy-dock]' ? {dataset: {policyDock: 'discard'}} : null)},
  });
  assert.deepEqual(discarded, ['role.growth.skills', 'expression.learning']);
  assert.equal(dock.hidden, true);
  assert.equal(dock.children.length, 0);

  // 再次修改单卡并保存
  dirtyList = [makeDirty('role.growth.skills', '技能与日程影响', ['自定义技能域'])];
  windowListeners.get('companion:workbench-state')?.();
  assert.equal(dock.hidden, false);
  assert.equal(dock.find(node => node.tagName === 'STRONG').textContent, '已修改 1 项');
  assert.equal(dock.find(node => node.dataset.policyDock === 'save').textContent, '保存更改');
  await dockListeners.get('click')?.({
    target: {closest: sel => (sel === '[data-policy-dock]' ? {dataset: {policyDock: 'save'}} : null)},
  });
  assert.deepEqual(saved, ['role.growth.skills']);
  assert.equal(dock.hidden, true);

  // 卸载清理事件监听
  context.unmountConfigEditors();
  assert.equal(dockListeners.size, 0);
  assert.equal(windowListeners.size, 0);
});
