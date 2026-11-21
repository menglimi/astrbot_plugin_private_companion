/* 状态与日程的边界：计划不能冒充执行，缺失数据不能补成生活事实。 */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const homeCss = fs.readFileSync(path.join(__dirname,"../pages/陪伴小屋/home.css"),"utf8");
const window = {};
const documentEvents = {};
const document = {addEventListener:(name,handler)=>{documentEvents[name]=handler;}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,"../pages/陪伴小屋/home-sidebar.js"),"utf8"), {window,document});
const {renderState,renderSchedule} = window.CompanionHomeSidebar;
const now = Date.parse("2026-09-30T04:00:00Z");
const status = items => ({settings:{enabled:true,planning_timezone:"Asia/Shanghai"}, state:{revision:1,values:{}},
  occurrences:{items,partial:false}, sleep_schedule:{enabled:false}});
const life = activities => ({activities,config:{diary_enabled:false}});
const event = (id,start,end,extra={}) => ({id,sequence:0,kind:"role_plan",state:"accepted",title:id,start,end,...extra});

test("结构化心情保留文字，零能量有真实读数，活动正文经过 HTML 转义", () => {
  const state=status([]);
  state.state.values={energy:0,mood:{description:"平静而专注",joy:5},location:"小屋",sleep:"清醒"};
  const input={now,state,life:life([{activity_id:"a",state:"active",kind:"reading",title:'<img src=x onerror="alert(1)">'}])};
  const html=renderState(input);
  assert.match(html,/平静而专注/);assert.match(html,/style="--home-state-level:0%"/);
  assert.match(renderSchedule(input),/&lt;img/);assert.doesNotMatch(html+renderSchedule(input),/<img|\[object Object\]|查看详情/);
});

test("当前日历计划没有执行确认时只标计划时段，待确认约定不进入当前活动", () => {
  const state=status([event("窗边读书","2026-09-30T03:30:00Z","2026-09-30T04:30:00Z")]);
  const html=renderSchedule({now,state,life:life([])});
  assert.match(html,/计划时段/);assert.match(html,/当前没有进行中的活动/);assert.doesNotMatch(html,/当前活动 · 进行中/);
  assert.doesNotMatch(renderState({now,state,life:life([])}),/窗边读书/,"当前计划已与日程合并，不占用状态区域");
  state.occurrences.items[0].state="proposed";
  assert.match(renderSchedule({now,state,life:life([])}),/待确认/);
});

test("今日时间线随当前时间前移，近期安排置顶，已过时段可折叠查看", () => {
  const state=status([
    event("傍晚安排","2026-09-30T10:00:00Z","2026-09-30T11:00:00Z"),
    event("早已过去","2026-09-30T01:00:00Z","2026-09-30T02:00:00Z",{detail:"<b>没有执行回执</b>"}),
    event("下午安排","2026-09-30T06:00:00Z","2026-09-30T07:00:00Z"),
    event("已取消安排","2026-09-30T05:00:00Z","2026-09-30T06:00:00Z",{state:"cancelled"}),
    event("明日安排","2026-10-01T02:00:00Z","2026-10-01T03:00:00Z"),
  ]);
  const activities=[
    {activity_id:"yesterday",state:"completed",title:"昨天完成",started_at:"2026-09-29T09:00:00Z",updated_at:"2026-09-29T10:00:00Z"},
    {activity_id:"done",state:"completed",title:"今早完成",started_at:"2026-09-30T00:30:00Z",updated_at:"2026-09-30T01:30:00Z",checkpoint:"读完两章"},
    {activity_id:"active",state:"active",title:"正在阅读",started_at:"2026-09-30T03:00:00Z",due_at:"2026-09-30T05:00:00Z"},
    {activity_id:"tomorrow",state:"planned",title:"明日活动",due_at:"2026-10-01T03:00:00Z"},
  ];
  const html=renderSchedule({now,state,life:life(activities)});
  assert.match(html,/>有活动正在进行<\/p>/);
  assert.doesNotMatch(html,/现在 12:00/);
  assert(html.indexOf("正在阅读")<html.indexOf("下午安排"));
  assert(html.indexOf("下午安排")<html.indexOf("傍晚安排"));
  assert.match(html,/查看已结束 2 项/);
  assert.doesNotMatch(html.split('<details class="home-agenda-more" data-home-agenda-past')[0],/今早完成|早已过去/);
  assert.match(renderSchedule({now,state,life:life([])}),/现在空档/);
  assert.doesNotMatch(renderSchedule({now,state,life:life([])}),/现在 12:00/);
  assert.doesNotMatch(html,/昨天完成|明日安排|明日活动|已取消安排|<b>没有执行回执/);
  const summary={parentElement:{open:false}};
  documentEvents.click({target:{closest:selector=>selector.includes("data-home-agenda-past")?summary:null}});
  const expanded=renderSchedule({now,state,life:life(activities)});
  assert.match(expanded,/data-home-agenda-past open><summary>收起已结束 2 项/);
  assert(expanded.indexOf("早已过去")<expanded.indexOf("今早完成"),"最近结束的安排先展示");
  summary.parentElement.open=true;
  documentEvents.click({target:{closest:selector=>selector.includes("data-home-agenda-past")?summary:null}});
  assert.match(renderSchedule({now,state,life:life(activities)}),/data-home-agenda-past ><summary>查看已结束 2 项/);
  const progressed=renderSchedule({now:Date.parse("2026-09-30T06:30:00Z"),state,life:life(activities)});
  assert.match(progressed,/下午安排/);
  assert.match(progressed,/当前计划时段/);
  assert.doesNotMatch(progressed,/现在 14:30/);
  const passed=renderSchedule({now:Date.parse("2026-09-30T07:10:00Z"),state,life:life(activities)});
  assert.match(passed,/已结束 3 项/);
  assert.doesNotMatch(passed.split('<details class="home-agenda-more" data-home-agenda-past')[0],/下午安排/);
  const finished=renderSchedule({now:Date.parse("2026-09-30T12:00:00Z"),state,life:life([])});
  assert.match(finished,/今日安排已结束/);
  assert.match(finished,/查看已结束 3 项/);
  assert.equal((finished.match(/今日安排已结束/g)||[]).length,1);
});

test("滚动时间线标出空档长度，并在数据不完整时停止推断", () => {
  const now=Date.parse("2026-09-30T04:00:00Z");
  const state=status([event("傍晚安排","2026-09-30T10:00:00Z","2026-09-30T11:00:00Z")]);
  const html=renderSchedule({now,state,life:life([])});
  assert.match(html,/class="home-agenda-overview"/);
  assert.match(html,/class="home-agenda-scroll" tabindex="0"/);
  assert.match(homeCss,/\.home-agenda-scroll\{[^}]*overflow-y:auto/);
  assert.match(homeCss,/\.home-agenda-item\[data-current="true"\]/);
  assert.match(homeCss,/\.home-agenda-gap\[data-current="true"\]/);
  assert.match(html,/现在空档/);
  assert.match(html,/下一项 18:00/);
  assert.match(html,/下一项 18:00/);

  const current=status([event("正在阅读","2026-09-30T02:00:00Z","2026-09-30T05:00:00Z")]);
  const currentHtml=renderSchedule({now,state:current,life:life([])});
  assert.match(currentHtml,/data-tone="current" data-current="true" aria-current="time"/);
  assert.doesNotMatch(currentHtml,/class="home-agenda-gap" data-current="true"/);

  state.occurrences.partial=true;
  const partial=renderSchedule({now,state,life:life([])});
  assert.match(partial,/日程数据不完整，空白暂不能判断为未安排/);
  assert.match(partial,/data-kind="unknown"/);
  assert.doesNotMatch(partial,/class="home-agenda-gap"/);
});

test("节目单把安排标题和说明放在时间之前，短空档不占主列表", () => {
  const state=status([
    event("早已过去","2026-09-30T01:00:00Z","2026-09-30T01:30:00Z"),
    event("傍晚安排","2026-09-30T10:00:00Z","2026-09-30T11:00:00Z"),
    event("稍后安排","2026-09-30T04:15:00Z","2026-09-30T04:45:00Z"),
  ]);
  const activities=[{activity_id:"active",state:"active",title:"正在阅读",started_at:"2026-09-30T03:30:00Z",due_at:"2026-09-30T05:00:00Z",checkpoint:"读完这一章"}];
  const html=renderSchedule({now,state,life:life(activities)});
  assert.match(html,/home-agenda-summary/);
  assert.match(html,/接下来/);
  assert.match(html,/已结束/);
  assert.match(html,/counter-reset:agenda-act 0/);
  assert.match(html,/正在阅读/);
  assert.match(html,/读完这一章/);
  assert.doesNotMatch(html,/30分钟/,"短空档不应占主列表");
  assert.doesNotMatch(html,/今天已结束/);
});

test("今日时间线按时间升序显示，即使接口返回倒序", () => {
  const state=status([
    event("晚上安排","2026-09-30T15:50:00Z","2026-09-30T16:20:00Z"),
    event("下午安排","2026-09-30T12:00:00Z","2026-09-30T12:30:00Z"),
    event("傍晚安排","2026-09-30T09:20:00Z","2026-09-30T09:50:00Z"),
  ]);
  const html=renderSchedule({now,state,life:life([])});
  assert(html.indexOf("傍晚安排")<html.indexOf("下午安排"));
  assert(html.indexOf("下午安排")<html.indexOf("晚上安排"));
});

test("共享睡眠以确认标记睡眠中，跨日计划有日期，并说明缺少执行窗口", () => {
  const state=status([]);
  state.body_sleep={state:"sleeping",started_at:"2026-09-30T00:00:00Z",planned_wake_at:"2026-09-30T03:00:00Z"};
  state.sleep_schedule={enabled:true,next_sleep_at:"2026-09-30T10:00:00Z",next_wake_at:"2026-10-01T00:30:00Z",executor_state:"no_authorized_window"};
  const plannedLife=life([]);plannedLife.config.diary_enabled=true;plannedLife.next_diary_at="2026-10-01T01:00:00Z";
  const html=renderSchedule({now,state,life:plannedLife});
  assert.match(html,/睡眠中 · 活动确认/);assert.match(html,/18:00 – 10月1日 08:30/);
  assert.match(html,/等待授权窗口/);assert.doesNotMatch(html,/已完成/);
  assert.doesNotMatch(html,/整理今日日记/);
  assert.match(renderState({now,state,life:life([])}),/睡眠中/);
  const sameWindow=life([{activity_id:"sleep",state:"active",kind:"sleep",title:"睡眠休息",started_at:state.body_sleep.started_at,due_at:state.body_sleep.planned_wake_at}]);
  assert.equal((renderSchedule({now,state,life:sameWindow}).match(/<strong>睡眠休息<\/strong>/g)||[]).length,2,"实际睡眠与下次计划各出现一次");
});

test("加载、空记录、权限失败与部分读取各有独立说明，关闭的心情维护不冒出旧心情", () => {
  assert.match(renderSchedule({now,loading:true}),/正在读取日程/);
  assert.match(renderSchedule({now,state:status([]),life:life([])}),/今日暂无安排/);
  assert.match(renderSchedule({now,unavailable:()=>"当前对象没有此私聊工作区"}),/当前对象没有此私聊工作区/);
  const state=status([]);state.occurrences.partial=true;state.settings.mood_enabled=false;state.state.values.mood="旧心情";
  const partialSchedule=renderSchedule({now,state,life:life([])});
  assert.match(partialSchedule,/日程未完整读取/);
  assert.match(partialSchedule,/部分日历安排未完整读取/);
  assert.doesNotMatch(partialSchedule,/今日暂无安排/);
  assert.doesNotMatch(renderState({now,state,life:life([])}),/旧心情/);
  assert.doesNotMatch(renderState({now,state,life:life([])}),/data-state-kind="mood"/);
});

test("首页分项开关与特殊状态展示彼此独立，正常、否定和未知值不冒充特殊状态", () => {
  const state=status([]);
  for(const name of ["health","hunger","sleep","cycle","busy"])state.settings["home_only_special_"+name]=true;
  state.state.cycle_timeline={applicability:"applicable",phase:"follicular",phase_day:2};
  state.state.values={health:"没有不适",hunger:"不饿",sleep:"没有失眠",busy:"不忙",energy:75,mood:"平静",location:"小屋"};
  const render=()=>renderState({now,state,life:life([])});
  const has=kind=>new RegExp(`data-state-kind="${kind}"`);
  for(const kind of ["health","hunger","sleep","cycle","busy"])assert.doesNotMatch(render(),has(kind),kind);
  state.state.values={health:"有点发烧",hunger:"有点饿",sleep:"难以入睡",busy:"忙着整理书架",energy:75,mood:"平静",location:"小屋"};
  state.state.cycle_timeline.phase="menstrual";
  for(const kind of ["health","hunger","sleep","cycle","busy"])assert.match(render(),has(kind),kind);
  state.settings.home_show_hunger=false;
  state.settings.health_enabled=false;
  state.settings.energy_enabled=false;
  state.settings.home_show_mood=false;
  for(const kind of ["hunger","health","energy","mood"])assert.doesNotMatch(render(),has(kind),kind);
  state.body_sleep={state:"sleeping"};
  assert.doesNotMatch(render(),has("sleep"),"实际睡眠回执优先于旧失眠感受");
  state.body_sleep=null;
  state.state.values.sleep="没有失眠，暂无睡眠不足";
  state.state.values.busy="不忙，没什么忙碌的事";
  assert.doesNotMatch(render(),has("sleep"));assert.doesNotMatch(render(),has("busy"));
  state.state.values.sleep=null;state.state.values.busy=null;
  assert.doesNotMatch(render(),has("sleep"));assert.doesNotMatch(render(),has("busy"));
  state.settings.home_only_special_sleep=false;
  assert.match(render(),has("sleep"),"始终展示仍允许显示尚未记录");
});

test("角色数据未就绪时保留首次设置状态，并提供账号与会话入口", () => {
  const unavailableCode = name => ["state", "life"].includes(name) ? "staging_not_initialized" : "";
  const unavailable = () => "角色数据尚未就绪，请核对角色映射；首次设置记录仍会保留。";
  const stateHtml = renderState({now,unavailable,unavailableCode});
  assert.match(stateHtml,/角色数据尚未就绪/);
  assert.match(stateHtml,/data-go="manage" data-manage="host"/);
  assert.doesNotMatch(stateHtml,/还没完成首次设置/);

  const scheduleHtml = renderSchedule({now,unavailable,unavailableCode});
  assert.match(scheduleHtml,/日历与活动暂不可读取/);
  assert.match(scheduleHtml,/data-go="manage" data-manage="host"/);
  assert.equal((scheduleHtml.match(/data-manage="host"/g)||[]).length,1);
  assert.doesNotMatch(scheduleHtml,/还没完成首次设置/);
});

test("空日程仍说明自动规划状态，并区分调度巡检与日程覆盖", () => {
  const state=status([]);
  state.planning={enabled:true,calendar_enabled:true,next_check:"2026-09-30T08:30:00Z"};
  const html=renderSchedule({now,state,life:life([])});
  assert.match(html,/自动规划已开启 · 调度巡检 16:30/);
  assert.match(html,/今日暂无安排/);
  assert.doesNotMatch(html,/class="home-agenda-date"/);
  state.planning.calendar_enabled=false;
  assert.match(renderSchedule({now,state,life:life([])}),/日历写入尚未开启/);
});

test("当日规划失败时显示原因和后续评估，不把空日历画成全天空闲", () => {
  const state=status([]);
  state.planning={enabled:true,calendar_enabled:true,next_check:"2026-09-30T04:01:00Z",
    daily_result:{state:"failed",code:"provider_wait_timed_out"},next_review_at:"2026-09-30T10:00:00Z"};
  const html=renderSchedule({now,state,life:life([])});
  assert.match(html,/今日规划尚未生成/);
  assert.match(html,/当日规划未完成：模型等待超时/);
  assert.match(html,/18:00 重新评估剩余时段/);
  assert.doesNotMatch(html,/今日暂无安排|当前未安排|调度巡检/);
  state.planning.running=true;
  assert.match(renderSchedule({now,state,life:life([])}),/今日规划正在生成/);
  state.planning.running=false;state.planning.daily_result={state:"succeeded",saved:0};
  assert.match(renderSchedule({now,state,life:life([])}),/今日暂无安排/);
});

test("四项图标用不同状态呈现，无法归类的自由描述原样保留在详情中", () => {
  const state=status([]);state.state.values={energy:12,health:"恢复中",hunger:"有点饿",sleep:"睡不着"};
  const html=renderState({now,state,life:life([])});
  for(const visual of ["energy-depleted","recovering","hungry","restless"])assert.match(html,new RegExp('data-visual="'+visual+'"'));
  assert.equal((html.match(/class="home-state-point"/g)||[]).length,8);
  assert.match(html,/能量 12 \/ 100/);
  assert.match(html,/style="--home-state-level:12%"/);
  assert.doesNotMatch(html,/<meter|有记录/);
  assert.match(homeCss,/\.home-state-board\s*\{[^}]*grid-template-columns:\s*repeat\(4,minmax\(0,1fr\)\)/);
  assert.match(homeCss,/\.home-state-point\[data-tone="warn"\]>strong\{color:var\(--t-warn-ink\)\}/);
  assert.match(homeCss,/\.home-state-point\[data-visual\^="energy-"\]\[data-tone="risk"\].*linear-gradient\(to top/);
  state.state.values={energy:88,health:"没有不适",hunger:"不饿",sleep:"不困"};
  const normal=renderState({now,state,life:life([])});
  for(const visual of ["energy-full","well","sated","awake"])assert.match(normal,new RegExp('data-visual="'+visual+'"'));
  state.state.values.health="胃部像一团小小的棉花";
  const prose=renderState({now,state,life:life([])});
  assert.match(prose,/data-visual="recorded"/);assert.match(prose,/胃部像一团小小的棉花/);assert.doesNotMatch(prose,/有记录/);
  state.state.values.health="身体状态正常，暂无明显不适";
  const noSymptoms=renderState({now,state,life:life([])});
  assert.match(noSymptoms,/data-visual="well"[^>]*title="健康 · 身体状态正常，暂无明显不适"/);
  assert.match(noSymptoms,/data-visual="well"/);assert.doesNotMatch(noSymptoms,/data-visual="strained"/);
});



