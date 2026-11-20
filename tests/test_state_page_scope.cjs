"use strict";
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const test=require("node:test");
const vm=require("node:vm");

const source=fs.readFileSync(path.join(__dirname,"../pages/陪伴小屋/state-page.js"),"utf8");
function harness(windowKind="none",section="state") {
  const requests=[],scopes=[],statuses=[];
  const targets=[
    {target_id:"representative",bot_id:"bot-1",scope_kind:"private"},
    {target_id:"selected",bot_id:windowKind==="other_bot"?"bot-2":"bot-1",scope_kind:windowKind==="group"?"group":"private"},
  ].map(target=>({...target,roles:[{role_id:"role-1",state_api:`/astrbot_plugin_private_companion/state/${target.target_id}`}]}));
  const status={state:{values:{energy:70}},settings:{enabled:true,presence_enabled:true,online_status_enabled:false,
    typing_enabled:true,typing_refresh_seconds:7},sleep_schedule:{},role_edit_token:"role-edit",edit_token:"window-edit",
    effective:{typing_enabled:{allowed:true},presence_enabled:{allowed:true}},
    presentation_diagnostics:{typing:{state:"refreshing"},custom_status:{state:"idle"}},
    presentation:{"typing:representative":{state:"accepted"},"presence:bot-1":{state:"accepted"}},
    contact:{settings:{label:"private-only"}}};
  const bridge={ready:async()=>({pluginName:"astrbot_plugin_private_companion"}),
    apiGet:async route=>{requests.push(route);return {status:"ok",data:status};}};
  const window={CompanionProduct:{hosted:()=>true,scopeKey:()=>`${windowKind}:role-1`,bridgeNow:()=>bridge,
    directory:()=>({installation_id:"install",operator_id:"operator",targets}),
    selection:()=>({target_id:windowKind==="none"?null:"selected",bot_id:"bot-1",role_id:"role-1"}),roleTarget:()=>targets[0]},
    CompanionStateEditor:{create:()=>({setScope:value=>scopes.push(value),setStatus:value=>{if(value)statuses.push(value);},
      invalidate(){},markup:()=>"",destroy(){},discardIfPristine:()=>true,interacting:false})}};
  const document={addEventListener(){},querySelector:()=>null,body:{classList:{toggle(){}}},hidden:false};
  const root={isConnected:true,innerHTML:"",addEventListener(){},removeEventListener(){},querySelector:()=>null,
    querySelectorAll:()=>[],contains:()=>false};
  vm.runInNewContext(source,{window,document,Promise,Intl,Date,console,setTimeout:()=>1,clearTimeout(){}});
  const page=window.CompanionStatePage;
  return {page,root,requests,scopes,statuses,ready:page.mount(root,{section,feature:"presentation"})};
}

for(const kind of ["none","group","other_bot"])test(`state role defaults exclude private window controls for ${kind}`,async()=>{
  const h=harness(kind);await h.ready;
  assert.deepEqual(h.requests,["state/representative"]);
  assert.equal(h.scopes.at(-1).window_selected,false);
  const current=h.statuses.at(-1);
  assert.equal(current.role_edit_token,"role-edit");
  assert.equal(current.settings.presence_enabled,true);
  assert.equal(current.settings.typing_enabled,undefined);
  assert.equal(current.settings.typing_refresh_seconds,undefined);
  assert.equal(current.edit_token,null);
  assert.equal(current.contact,null);
  assert.equal(current.presentation_diagnostics.typing,undefined);
  assert.deepEqual(Object.keys(current.presentation),["presence:bot-1"]);
  assert.match(h.root.innerHTML,/未选择私聊窗口/);
  h.page.unmount();
});

test("state uses the selected private window instead of the role representative",async()=>{
  const h=harness("private");await h.ready;
  assert.deepEqual(h.requests,["state/selected"]);
  assert.equal(h.scopes.at(-1).window_selected,true);
  assert.equal(h.statuses.at(-1).settings.typing_refresh_seconds,7);
  h.page.unmount();
});

for(const kind of ["none","group","other_bot"])test(`contact state never reads a representative window for ${kind}`,async()=>{
  const h=harness(kind,"contact");await h.ready;
  assert.deepEqual(h.requests,[]);
  assert.match(h.root.innerHTML,/请先选择当前角色的私聊窗口/);
  h.page.unmount();
});
