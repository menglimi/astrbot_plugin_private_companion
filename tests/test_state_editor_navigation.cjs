"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const crypto = require("node:crypto");

const source = fs.readFileSync(path.join(__dirname, "../pages/陪伴小屋/state-editor.js"), "utf8");

test("rendering a QQ status detail does not open or reopen its editor", () => {
  const window={CompanionStateMaintenance:{fields:()=>[],validField:()=>true,titles:{},canAlign:()=>false},CompanionStateContact:{}};
  vm.runInNewContext(source,{window,structuredClone,JSON,crypto,console});
  const editor=window.CompanionStateEditor.create({changed(){},failed(){},refresh:async()=>null,api:async()=>({})});
  editor.setScope({installation_id:"install",operator_id:"admin",path:"role/state",window_selected:true});
  editor.setStatus({edit_token:"window-token",role_edit_token:"role-token",
    settings:{typing_enabled:true,typing_refresh_seconds:3,presence_enabled:false},state:{values:{}}});
  const details=()=>editor.markup({feature:"presentation"});
  assert.match(details(),/data-state-edit="settings"/);
  assert.equal(editor.interacting,false,"render must not create a draft");

  const click=action=>{
    const target={dataset:{stateEdit:action},closest:selector=>selector==="[data-state-edit]"?target:null};
    return editor.click({target});
  };
  assert.equal(click("settings"),true);
  assert.match(details(),/id="state-edit-form"/);
  assert.equal(click("cancel"),true);
  assert.match(details(),/data-state-edit="settings"/);
  assert.doesNotMatch(details(),/id="state-edit-form"/);
  assert.equal(editor.interacting,false,"render after cancel must leave the editor closed");
  editor.destroy();
});


test("QQ presence saves at role scope while typing stays window scoped", async () => {
  const window={CompanionStateMaintenance:{fields:()=>[],validField:()=>true,titles:{}},CompanionStateContact:{}};
  vm.runInNewContext(source,{window,structuredClone,JSON,crypto,console,sessionStorage:{getItem:()=>null,setItem(){},removeItem(){}}});
  const settings={typing_enabled:false,typing_refresh_seconds:4,presence_enabled:false,online_status_enabled:false};
  const status={edit_token:"window-edit",role_edit_token:"role-edit",settings,state:{values:{}}};
  const posts=[];
  const editor=window.CompanionStateEditor.create({changed(){},failed(){},refresh:async()=>status,
    api:async(_method,_route,body)=>{posts.push(body);return {operation_id:"presence-config"};}});
  editor.setScope({installation_id:"install",operator_id:"admin",path:"role/state",window_selected:true});
  editor.setStatus(status);
  const input=(name,value,type="checkbox")=>editor.input({target:{dataset:{stateField:name},type,
    checked:value,value:String(value),matches:()=>true,hasAttribute:()=>false,closest:()=>null}});
  editor.open("settings");
  input("presence_enabled",true);
  editor.submit({target:{id:"state-edit-form"},preventDefault(){}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(posts.length,1);
  assert.equal(posts[0].action,"configure_role");
  assert.equal(posts[0].role_edit_token,"role-edit");
  assert.equal(Object.hasOwn(posts[0],"edit_token"),false);
  assert.deepEqual(JSON.parse(JSON.stringify(posts[0].changes)),{presence_enabled:true});

  editor.open("settings");
  input("typing_enabled",true);
  editor.submit({target:{id:"state-edit-form"},preventDefault(){}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(posts.length,2);
  assert.equal(posts[1].action,"configure");
  assert.equal(posts[1].edit_token,"window-edit");
  assert.equal(Object.hasOwn(posts[1],"role_edit_token"),false);
  assert.deepEqual(JSON.parse(JSON.stringify(posts[1].changes)),{typing_enabled:true});
  editor.destroy();
});

test("role-only QQ settings never display or submit a representative window's typing fields", async () => {
  const window={CompanionStateMaintenance:{fields:()=>[],validField:()=>true,canAlign:()=>false,titles:{}},CompanionStateContact:{}};
  vm.runInNewContext(source,{window,structuredClone,JSON,crypto,console,sessionStorage:{getItem:()=>null,setItem(){},removeItem(){}}});
  const status={role_edit_token:"role-edit",settings:{presence_enabled:false,online_status_enabled:false},state:{values:{}}};
  const posts=[];
  const editor=window.CompanionStateEditor.create({changed(){},failed(){},refresh:async()=>status,
    api:async(_method,_route,body)=>{posts.push(body);return {operation_id:"role-presence"};}});
  editor.setScope({installation_id:"install",operator_id:"admin",path:"role/state",window_selected:false});
  editor.setStatus(status);editor.open("settings");
  assert.match(editor.markup({feature:"presentation"}),/未选择私聊窗口/);
  assert.doesNotMatch(editor.markup({feature:"presentation"}),/data-state-field="typing_/);
  for(const name of ["presence_enabled","typing_enabled"])editor.input({target:{dataset:{stateField:name},
    type:"checkbox",checked:true,matches:()=>true,hasAttribute:()=>false,closest:()=>null}});
  editor.submit({target:{id:"state-edit-form"},preventDefault(){}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(posts.length,1);
  assert.equal(posts[0].action,"configure_role");
  assert.deepEqual(JSON.parse(JSON.stringify(posts[0].changes)),{presence_enabled:true});
  editor.destroy();
});

test("state drafts only block switching after a field changes", () => {
  const window = {CompanionStateMaintenance:{
    fields:kind=>kind === "intensity" ? ["energy"] : [],
    validField:()=>true,
    normalized:(_kind, values)=>({...values}),
    titles:{intensity:"精力"},
  }, CompanionStateContact:{}};
  vm.runInNewContext(source, {window, structuredClone, JSON, console});
  const editor = window.CompanionStateEditor.create({changed() {}, failed() {}, refresh:async()=>null,
    api:async()=>({})});
  editor.setStatus({edit_token:"window-token", role_edit_token:"role-token",
    settings:{energy:50}, state:{values:{}}});
  editor.open("intensity");
  assert.equal(editor.interacting, true);
  assert.equal(editor.dirty, false);
  assert.equal(editor.discardIfPristine(), true);
  assert.equal(editor.interacting, false);

  editor.open("intensity");
  editor.input({target:{dataset:{stateField:"energy"}, type:"number", value:"60", matches:()=>true}});
  assert.equal(editor.dirty, true);
  assert.equal(editor.discardIfPristine(), false);
  editor.destroy();
});

test("weather location and switches share one role-wide state command", async () => {
  const window={};
  const context=vm.createContext({window,structuredClone,Intl,Date,TextEncoder,JSON,crypto,
    sessionStorage:{getItem:()=>null,setItem(){},removeItem(){}}});
  for(const name of ["state-maintenance.js","state-editor.js"])
    vm.runInContext(fs.readFileSync(path.join(__dirname,"../pages/陪伴小屋",name),"utf8"),context,{filename:name});
  const settings={calendar_enabled:true,schedule_simulation_mode:"orchestrated",planning_enabled:false,
    planning_timezone:"Asia/Shanghai",planning_horizon_days:90,planning_refine_minutes:15,planning_max_items:12,
    planning_prompt:"",planning_annual_calendars:"",planning_weather_enabled:false,planning_weather_location:"",
    planning_weather_max_age_minutes:120};
  const status={edit_token:"window-edit",role_edit_token:"role-edit",settings,state:{revision:1,values:{}},calendar:{items:[]},
    planning:{enabled:false,calendar_enabled:true,running:false,next_check:null,last_result:null}};
  const posts=[];let refreshes=0;
  const editor=window.CompanionStateEditor.create({changed(){},failed(){},refresh:async()=>{refreshes+=1;return status;},
    api:async(_method,_route,body)=>{posts.push(body);return {operation_id:"state-config"};}});
  editor.setScope({installation_id:"install",operator_id:"admin",path:"role/state",window_selected:true});
  editor.setStatus(status);editor.open("weather");
  const part=(name,value)=>editor.input({target:{dataset:{stateWeatherLocation:name},value,matches:()=>false}});
  part("name","札幌");part("latitude","43.06");part("longitude","141.35");
  editor.input({target:{dataset:{stateField:"planning_weather_enabled"},type:"checkbox",checked:true,matches:()=>true}});
  editor.submit({target:{id:"state-edit-form"},preventDefault(){}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(posts.length,1);
  assert.equal(posts[0].action,"configure_role");
  assert.equal(posts[0].role_edit_token,"role-edit");
  assert.deepEqual(JSON.parse(JSON.stringify(posts[0].changes)),{
    planning_weather_enabled:true,
    planning_weather_location:JSON.stringify({name:"札幌",latitude:43.06,longitude:141.35}),
  });
  assert.equal(refreshes,1,"状态配置保存后应重新读取状态");
  editor.destroy();
});

test("perception weather controls edit the role settings through the state command", async () => {
  const window={};
  const context=vm.createContext({window,structuredClone,Intl,Date,TextEncoder,JSON,crypto,
    sessionStorage:{getItem:()=>null,setItem(){},removeItem(){}}});
  for(const name of ["state-maintenance.js","state-editor.js"])
    vm.runInContext(fs.readFileSync(path.join(__dirname,"../pages/陪伴小屋",name),"utf8"),context,{filename:name});
  const settings={planning_weather_enabled:false,planning_weather_location:"",planning_weather_max_age_minutes:120};
  const status={edit_token:"window-weather-edit",role_edit_token:"role-weather-edit",
    settings,state:{revision:1,values:{}}};
  const posts=[];
  const editor=window.CompanionStateEditor.create({changed(){},failed(){},refresh:async()=>null,
    api:async(_method,_route,body)=>{posts.push(body);return {operation_id:"weather-config"};}});
  editor.setScope({installation_id:"install",operator_id:"admin",path:"role/state",window_selected:true});
  editor.setStatus(status);
  assert.match(editor.markup({section:"weather"}),/data-state-edit="weather"/);
  assert.doesNotMatch(editor.markup({section:"weather"}),/id="state-edit-form"/);
  editor.open("weather");
  assert.match(editor.markup({section:"weather"}),/state-weather-location-name/);
  const part=(name,value)=>editor.input({target:{dataset:{stateWeatherLocation:name},value,matches:()=>false}});
  part("name","杭州");part("latitude","30.25");part("longitude","120.17");
  editor.input({target:{dataset:{stateField:"planning_weather_enabled"},type:"checkbox",checked:true,matches:()=>true}});
  editor.input({target:{dataset:{stateField:"planning_weather_max_age_minutes"},type:"number",value:"60",matches:()=>true}});
  editor.submit({target:{id:"state-edit-form"},preventDefault(){}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(posts.length,1);
  assert.equal(posts[0].action,"configure_role");
  assert.equal(posts[0].role_edit_token,"role-weather-edit");
  assert.deepEqual(JSON.parse(JSON.stringify(posts[0].changes)),{
    planning_weather_enabled:true,
    planning_weather_location:JSON.stringify({name:"杭州",latitude:30.25,longitude:120.17}),
    planning_weather_max_age_minutes:60,
  });
  editor.destroy();
});

test("mixed state settings keep role defaults and window controls on their own targets", async () => {
  const window={CompanionStateMaintenance:{
    fields:kind=>kind==="home"?["typing_enabled","maintenance_minutes"]:[],
    validField:()=>true,
    normalized:(_kind,values)=>({...values,maintenance_minutes:Number(values.maintenance_minutes)}),
    titles:{home:"状态与首页展示"},
  },CompanionStateContact:{}};
  vm.runInNewContext(source,{window,structuredClone,JSON,crypto,console});
  const status={edit_token:"window-edit",role_edit_token:"role-edit",
    settings:{typing_enabled:false,maintenance_minutes:30},state:{values:{}}};
  const posts=[];
  const editor=window.CompanionStateEditor.create({changed(){},failed(){},refresh:async()=>status,
    api:async(_method,_route,body)=>{posts.push(body);return {operation_id:"mixed-config"};}});
  editor.setScope({installation_id:"install",operator_id:"admin",path:"role/state",window_selected:true});
  editor.setStatus(status);
  editor.open("home");
  const input=(name,value,type="checkbox")=>editor.input({target:{dataset:{stateField:name},type,
    checked:value,value:String(value),matches:()=>true,hasAttribute:()=>false,closest:()=>null}});
  input("typing_enabled",true);
  input("maintenance_minutes",45,"number");
  editor.submit({target:{id:"state-edit-form"},preventDefault(){}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(posts.length,1);
  assert.equal(posts[0].action,"configure");
  assert.equal(posts[0].edit_token,"window-edit");
  assert.equal(posts[0].role_edit_token,"role-edit");
  assert.deepEqual(JSON.parse(JSON.stringify(posts[0].changes)),{typing_enabled:true,maintenance_minutes:45});
  editor.destroy();
});
