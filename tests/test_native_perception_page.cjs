"use strict";

const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.resolve(__dirname, "../pages/陪伴小屋/perception-native-page.js"), "utf8");
const flush = () => new Promise(resolve => setImmediate(resolve));

function status(token = "revision-1", timezone = "Asia/Shanghai", locationName = "") {
  return {
    environment: {sources:{weather_ready:true, weather_provider:"open-meteo", alerts_ready:false}, settings:{
      planning_timezone:timezone, planning_annual_calendars:"", planning_weather_enabled:false,
      planning_weather_location:locationName ? JSON.stringify({name:locationName}) : "", planning_weather_max_age_minutes:120,
    }},
    model: {edit_token:token, settings:{calendar_enabled:true, calendar_timezone:timezone,
      health_enabled:false, health_proactive_enabled:false, holiday_enabled:true, holiday_country:"CN",
      lunar_enabled:true, automatic_solar_terms_enabled:false, almanac_enabled:false,
      platform_enabled:true, model_route_enabled:true, configured_modalities_enabled:true, guidance:""}},
     contact: {settings:{date_checkin_enabled:true, important_dates:"[]",
       important_date_lookahead_days:0, quiet_timezone:timezone, balance_awareness_enabled:true},
       effective:{automatic_allowed:true}},
     news: {edit_token:token, source:{ready:true, assembled:true, kind:"rss", label:"橘鸦AI早报"},
       diagnostic:{source:{ready:true, assembled:true, kind:"rss", label:"橘鸦AI早报"},
         network_status:"not_checked", last_attempt_at:null,
         last_checked_at:null, failure:null, item_count:0, candidate:false}, contact_enabled:false,
       automatic_enabled:false, settings:{news_share_enabled:false, "news.enabled":false,
         news_check_interval_minutes:60, news_source_kind:"host", news_source_uid:"",
         news_source_url:""}},
  };
}

function harness(windowKind="private") {
  const requests = [];
  const window = {
    CompanionProduct: {hosted:() => true, scopeKey:() => "role-1",
      selection:()=>({target_id:windowKind==="none"?null:"target-1",bot_id:"bot-1",role_id:"role-1"}),
      target:()=>windowKind==="none"?null:({target_id:"target-1",bot_id:windowKind==="other_bot"?"bot-2":"bot-1",scope_kind:windowKind==="group"?"group":"private"}),
      windowRole:()=>({role_id:"role-1",perception_native_api:"/astrbot_plugin_private_companion/perception-native"}),
      role:() => ({role_id:"role-1",perception_native_api:"/astrbot_plugin_private_companion/perception-native"}),
      /* 页面取桥先问 bridgeNow()，没有再 await bridgeFor()。 */
      bridgeNow:() => window.AstrBotPluginPage,
      bridgeFor:() => ({then:resolve => resolve(window.AstrBotPluginPage)})},
    AstrBotPluginPage: {
      apiGet:route => new Promise(resolve => requests.push({method:"GET", route, resolve})),
      apiPost:(route, body) => new Promise(resolve => requests.push({method:"POST", route, body, resolve})),
    },
  };
  vm.runInNewContext(source, {window, AbortController, crypto, TextEncoder, setTimeout, clearTimeout, Intl});
  function root() {
    const handlers = {};
    return {isConnected:true, innerHTML:"",
      addEventListener(type, handler) { handlers[type] = handler; },
      input(name, value, kind = "text") { handlers.input({target:{closest:() => ({dataset:{nativeField:name}, type:kind, value})}}); },
      toggle(name, checked) { handlers.input({target:{closest:() => ({dataset:{nativeField:name}, type:"checkbox", checked})}}); },
      submit() { handlers.submit({preventDefault() {}}); },
      click(action) { handlers.click({target:{closest:() => ({dataset:{nativeAction:action}})}}); },
    };
  }
  const environment = root();
  const calendar = root();
  const model = root();
  const news = root();
  window.CompanionNativePerceptionPage.mount({environment, calendar, model, news});
  return {requests, environment, calendar, model, news, page:window.CompanionNativePerceptionPage};
}

test("environment summary points to the role weather settings below", async () => {
  const {requests, environment} = harness();
  requests[0].resolve({status:"ok", data:status("revision-1", "Asia/Shanghai", "自定义测试区域")});
  await flush();

  assert.match(environment.innerHTML, /角色所在地[\s\S]*自定义测试区域/);
  assert.match(environment.innerHTML, /当前实况来源 · Open-Meteo/);
  assert.match(environment.innerHTML, /官方预警源尚未就绪 · 需和风凭据/);
  assert.match(environment.innerHTML, /同一角色的私聊共用/);
  assert.doesNotMatch(environment.innerHTML, /角色地点与观测时效、主动天气问候/);
  assert.doesNotMatch(environment.innerHTML, /统一在日程工作区维护/);
  assert.match(environment.innerHTML, /data-shell-furniture="calendar"/);
  assert.match(environment.innerHTML, /打开日程与规划/);
  assert.doesNotMatch(environment.innerHTML, /<form|data-native-field=/);
  assert.doesNotMatch(environment.innerHTML, /12\.34|56\.78/);
});

test("news status is diagnostic only and leaves one configuration editor below", async () => {
  const {requests, news, page} = harness();
  requests[0].resolve({status:"ok", data:status()});
  await flush();

  assert.match(news.innerHTML, /资讯源已装配 · 橘鸦AI早报/);
  assert.match(news.innerHTML, /网络状态：尚未检查/);
  assert.match(news.innerHTML, /检查来源不会发送消息/);
  assert.match(news.innerHTML, /检查来源/);
  assert.doesNotMatch(news.innerHTML, /data-native-field/);
  assert.doesNotMatch(news.innerHTML, /<form/);
  page.unmount();
});

for(const windowKind of ["none","group","other_bot"])test(`native perception keeps role settings but hides private contact data for ${windowKind}`,async()=>{
  const {requests,calendar,news,page}=harness(windowKind);
  const current=status();
  current.contact.dates='[{"label":"另一个窗口的日期"}]';
  requests[0].resolve({status:"ok",data:current});
  await flush();
  assert.match(calendar.innerHTML,/提供农历日期/);
  assert.match(news.innerHTML,/请先选择当前角色的私聊窗口/);
  assert.doesNotMatch(news.innerHTML,/橘鸦AI早报|data-native-action="diagnose"/);
  let snapshot;
  const unsubscribe=page.subscribe(value=>{snapshot=value;});
  assert.equal(snapshot.windowRequired,true);
  assert.deepEqual(JSON.parse(JSON.stringify(snapshot.contact)),{});
  assert.deepEqual(JSON.parse(JSON.stringify(snapshot.news)),{});
  news.click("diagnose");
  assert.equal(requests.length,1,"no diagnostic request may use the representative private window");
  calendar.toggle("lunar_enabled",false);calendar.submit();
  assert.equal(requests[1].body.section,"model");
  const saved=status("revision-2");saved.model.settings.lunar_enabled=false;
  requests[1].resolve({status:"ok",data:{status:saved,result:{}}});await flush();
  unsubscribe();page.unmount();
});

test("news diagnostics distinguish a failed latest attempt from the last success", async () => {
  const {requests, news, page} = harness();
  const current = status();
  current.news.diagnostic = {...current.news.diagnostic, network_status:"failed",
    last_attempt_at:"2026-10-02T10:00:00+08:00", last_checked_at:"2026-10-01T10:00:00+08:00",
    failure:{code:"news_timeout"}, item_count:3};
  requests[0].resolve({status:"ok", data:current});
  await flush();

  assert.match(news.innerHTML, /最近尝试失败 · 2026-10-02T10:00:00\+08:00/);
  assert.match(news.innerHTML, /上次成功检查 2026-10-01T10:00:00\+08:00/);
  let snapshot;
  const unsubscribe = page.subscribe(value => { snapshot = value; });
  assert.equal(snapshot.news.networkStatus, "failed");
  assert.equal(snapshot.news.assembled, true);
  unsubscribe();
  page.unmount();
});

test("a failed reread clears the previous native perception snapshot", async () => {
  const {requests, environment, page} = harness();
  requests[0].resolve({status:"ok", data:status("revision-1", "Asia/Shanghai", "第一次读取")});
  await flush();
  assert.match(environment.innerHTML, /第一次读取/);
  environment.click("refresh");
  requests[1].resolve({status:"error", message:"staging_not_initialized", data:{code:"staging_not_initialized"}});
  await flush();
  assert.doesNotMatch(environment.innerHTML, /第一次读取/);
  assert.match(environment.innerHTML, /角色数据尚未就绪/);
  let snapshot = null;
  const unsubscribe = page.subscribe(value => { snapshot = value; });
  assert.equal(snapshot.ready, false);
  unsubscribe();
  page.unmount();
});

/* 日期信息和模型上下文分开呈现，仍共用同一个 model 配置合同。 */
test("calendar controls are grouped on the time page and retain the model save contract", async () => {
  const {requests, calendar, model} = harness();
  requests[0].resolve({status:"ok", data:status()});
  await flush();

  assert.match(calendar.innerHTML, /提供农历日期/);
  assert.match(calendar.innerHTML, /提供节气日期/);
  assert.match(calendar.innerHTML, /提供黄历宜忌/);
  assert.match(calendar.innerHTML, /提供当前日期信息/);
  assert.match(calendar.innerHTML, /只提供日期背景，不修改角色日程/);
  assert.match(calendar.innerHTML, /节假日识别地区/);
  assert.doesNotMatch(model.innerHTML, /提供当前日期信息|提供农历日期/);
  assert.match(model.innerHTML, /标明当前平台/);
  assert.match(model.innerHTML, /标明模型路由/);
  assert.match(model.innerHTML, /感知表达偏好/);

  calendar.toggle("lunar_enabled", false);
  calendar.toggle("automatic_solar_terms_enabled", true);
  calendar.input("holiday_country", "WEEKEND_ONLY", "select");
  calendar.submit();
  assert.equal(requests[1].method, "POST");
  assert.equal(requests[1].body.section, "model");
  assert.equal(requests[1].body.command.changes.lunar_enabled, false);
  assert.equal(requests[1].body.command.changes.automatic_solar_terms_enabled, true);
  assert.equal(requests[1].body.command.changes.holiday_country, "WEEKEND_ONLY");
  /* 未改动的项不得混进变更集，否则每次保存都会覆盖别人刚改的值。 */
  assert.equal("almanac_enabled" in requests[1].body.command.changes, false);
  assert.equal("calendar_enabled" in requests[1].body.command.changes, false);
});

test("the overview subscription publishes display fields without exposing configured coordinates", async () => {
  const {requests, page} = harness();
  let snapshot = null;
  const unsubscribe = page.subscribe(value => { snapshot = value; });
  const next = status();
  next.environment.settings.planning_weather_location = JSON.stringify({name:"用户配置区域", latitude:12.34, longitude:56.78});
  next.contact.settings.important_dates = JSON.stringify([{calendar:"gregorian",month:10,day:3,label:"旧版手动日期"}]);
  next.contact.settings.important_date_lookahead_days = 4;
  next.contact.dates = JSON.stringify([{basis:"profile_role_birthday",label:"角色生日",
    date:"2026-10-03",days_until:2,calendar:"gregorian"}]);
  next.contact.lookaheadDays = 7;
  requests[0].resolve({status:"ok", data:next});
  await flush();

  assert.equal(snapshot.ready, true);
  assert.equal(snapshot.environment.locationName, "用户配置区域");
  assert.equal(snapshot.contact.lookaheadDays, 7);
  assert.equal(snapshot.contact.balanceAwarenessEnabled, true);
  assert.equal(snapshot.contact.automaticAllowed, true);
  assert.match(snapshot.contact.dates, /角色生日/);
  assert.doesNotMatch(snapshot.contact.dates, /旧版手动日期/);
  assert.doesNotMatch(JSON.stringify(snapshot), /balance_(?:low|critical)_threshold|balance_include_amount_in_message/);
  assert.doesNotMatch(JSON.stringify(snapshot), /latitude|longitude|113\.75|23\.04/);
  unsubscribe();
});
