/* The cottage environment uses the selected role's real clock and weather only. */
"use strict";
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const test=require("node:test");
const vm=require("node:vm");

function fixture(){
  const root={dataset:{},innerHTML:"",handler:null,addEventListener(_name,handler){this.handler=handler;},querySelectorAll(){return [];}};
  const timers=[];
  const window={lucide:{createIcons(){}}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,"../pages/陪伴小屋/home-environment.js"),"utf8"),
    {window,setTimeout:callback=>{timers.push(callback);return timers.length;},clearTimeout(){}});
  return {root,view:window.CompanionHomeEnvironment,timers};
}
const base={state:{settings:{planning_timezone:"Asia/Shanghai",planning_weather_enabled:true,
  planning_weather_location:JSON.stringify({name:"东莞市",latitude:23.02,longitude:113.75})}},life:null};

test("the top bar shows the configured location, live observation, role time, and refresh state",async()=>{
  const {root,view}=fixture();
  view.render(root,{...base,scopeKey:"role-a",requestWeather:async()=>({state:"live",location:"东莞市",condition:"多云",temperature_c:32,
    observed_at:new Date().toISOString(),valid_until:new Date(Date.now()+3600000).toISOString(),server_time:new Date().toISOString()})});
  await new Promise(resolve=>setImmediate(resolve));
  assert.match(root.innerHTML,/东莞市 · 多云 · 32°C/);
  assert.match(root.innerHTML,/实况天气/);
  const today=new Intl.DateTimeFormat("zh-CN",{timeZone:"Asia/Shanghai",month:"long",day:"numeric",weekday:"short"}).format(new Date());
  assert.ok(root.innerHTML.includes(`class="home-environment-date">${today}</small>`));
  assert.match(root.innerHTML,/Asia\/Shanghai/);
  assert.match(root.innerHTML,/data-home-weather-refresh/);
  assert.match(root.innerHTML,/aria-busy="false"/);
});

test("an unnamed official alert uses a readable label instead of raw unknown severity", async()=>{
  const {root,view}=fixture();
  view.render(root,{...base,scopeKey:"role-alert",requestWeather:async()=>({state:"live",location:"东莞市",condition:"阴",
    temperature_c:18,observed_at:new Date().toISOString(),valid_until:new Date(Date.now()+3600000).toISOString(),
    alerts:[{event_name:"其他预警",severity:"unknown",ends_at:new Date(Date.now()+3600000).toISOString()}]})});
  await new Promise(resolve=>setImmediate(resolve));
  assert.match(root.innerHTML,/天气预警/);
  assert.match(root.innerHTML,/级别待核对/);
  assert.doesNotMatch(root.innerHTML,/其他预警|unknown/);
});

test("disabling role weather never asks for or displays a preview observation",()=>{
  const {root,view}=fixture();
  let requests=0;
  view.render(root,{...base,scopeKey:"role-disabled",state:{settings:{...base.state.settings,planning_weather_enabled:false}},
    requestWeather:async()=>{requests++;return {state:"live",condition:"晴"};}});
  assert.match(root.innerHTML,/天气未启用/);
  assert.match(root.innerHTML,/disabled/);
  assert.equal(requests,0);
});

test("late weather from the previous role cannot replace the selected role",async()=>{
  const {root,view}=fixture();
  let finishFirst;
  view.render(root,{...base,scopeKey:"role-a",requestWeather:()=>new Promise(resolve=>{finishFirst=resolve;})});
  view.render(root,{...base,scopeKey:"role-b",requestWeather:async()=>({state:"live",location:"东莞市",condition:"小雨",temperature_c:24,
    observed_at:new Date().toISOString(),valid_until:new Date(Date.now()+3600000).toISOString()})});
  await new Promise(resolve=>setImmediate(resolve));
  finishFirst({state:"live",location:"东莞市",condition:"大雪",temperature_c:-20,
    observed_at:new Date().toISOString(),valid_until:new Date(Date.now()+3600000).toISOString()});
  await new Promise(resolve=>setImmediate(resolve));
  assert.match(root.innerHTML,/小雨 · 24°C/);
  assert.doesNotMatch(root.innerHTML,/大雪|-20°C/);
});

test("automatic cottage lighting follows the selected role's local daypart",()=>{
  const state={settings:{planning_timezone:"Asia/Shanghai"}};
  const at=(hour)=>Date.parse(`2026-10-04T${String(hour).padStart(2,"0")}:00:00Z`);
  assert.equal(fixture().view.sceneForTime(at(21),state,null),"night");
  assert.equal(fixture().view.sceneForTime(at(22),state,null),"dawn");
  assert.equal(fixture().view.sceneForTime(at(4),state,null),"day");
  assert.equal(fixture().view.sceneForTime(at(10),state,null),"evening");
});

test("automatic lighting updates when the role clock crosses a daypart boundary",()=>{
  const {root,view,timers}=fixture(),changes=[];
  const input={...base,now:Date.parse("2026-10-04T04:59:00Z"),autoScene:true,currentScene:"night",
    state:{settings:{planning_timezone:"Asia/Shanghai",planning_weather_enabled:false}},
    onSceneChange:scene=>{changes.push(scene);return true;}};
  view.render(root,input);
  assert.deepEqual(changes,["day"]);
  input.now=Date.parse("2026-10-04T11:00:00Z");
  timers.at(-1)();
  assert.equal(changes.at(-1),"night");
});
