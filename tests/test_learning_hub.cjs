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
  assert.match(renderer, /const scopeControls=scopeKey\?learningScopeControls/);
  assert.match(renderer, /learningRoleControls\(scopeKey,'筛选记录',scopeControls\)/);
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
