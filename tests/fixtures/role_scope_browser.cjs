"use strict";

const prefix = "/astrbot_plugin_private_companion/";
const labels = {a: "测试角色甲", b: "测试角色乙"};
const token = "c".repeat(64);
const roleKey = id => JSON.stringify(["bot-" + id, "shared-role"]);

function fixture() {
  const calls = [];
  const target = (id, group = false) => {
    const targetId = (group ? "group-" : "private-") + id;
    const base = `${prefix}host/targets/${targetId}/roles/shared-role/`;
    return {target_id: targetId, bot_id: "bot-" + id, account_id: id === "a" ? "10001" : "10002",
      account_name: "测试账号" + id, platform_id: "qq_official", scope_kind: group ? "group" : "private",
      display_name: (group ? "测试群" : "测试用户") + id, ...(group ? {group_id:"group-1"} : {participant_id:"user-" + id}),
      roles: [{role_id:"shared-role",role_name:labels[id],persona_ids:[labels[id]],
        ...Object.fromEntries(["life", "state", "growth", "memory", "relationships", "reply", "model", "config"].map(name => [name + "_api", base + name]))}]};
  };
  const targets = [target("a"), target("a", true), target("b")];
  const directory = {installation_id:"synthetic-role-scope",operator_id:"dashboard:synthetic",operator_name:"测试管理员",targets};
  const field = value => ({value, inherited_value:value,source:"role",inherited_source:"installation",explicit:false});
  const task = (id,layer = "role") => ({kind:"memory.reflect",lane:"background",capabilities:["text"],edit_token:token,
    application_state:"applied",configuration_layer:layer,fields:{provider:field("model-" + id),fallback:field(""),
      prompt:field(""),max_output_tokens:field(4096),timeout_seconds:field(60),replacement_rules:field("[]"),
      fallback_on_refusal:field(false),refusal_terms:field(""),replacement_scope:field("all")}});
  function rpc(method, endpoint, params = {}) {
    params ||= {};
    calls.push({method, endpoint, params});
    const id = /(?:private|group)-b/.test(endpoint) || params.target_id === "private-b" ? "b" : "a";
    if (endpoint === "host/performance") return {enabled:false,mode:"standard"};
    if (endpoint === "page/display-preferences") return {preferences:{motion:"off"}};
    if (endpoint === "page/targets") return directory;
    if (endpoint === "integration/manifest") return {product:{version:"test"},host:{enabled:true},core:[],extensions:[],providers:[],summary:{}};
    if (endpoint === "page/ownership") return {owner:"self",reason:"owner_module_absent"};
    if (endpoint === "host/status") return {enabled:true,state:"running",mode:"multi",targets};
    if (endpoint === "accounts") return {accounts:[],personas:[],groups:[targets[1]],sessions:[targets[0],targets[2]],discovered:{targets}};
    if (endpoint === "semantic") return {state:"ready",selection_ready:true,settings:{revision:1,enabled:true,reactions_enabled:true},
      catalog:{reaction:{total:1,enabled:1}},providers:{embedding:[],rerank:[],analysis:[]},sessions:[],next_after:null,
      items:[{id:"reaction-"+id,kind:"reaction",revision:1,enabled:true,payload:{name:labels[id]+"的表情",description:"合成表情资料"}}]};
    if (endpoint.endsWith("/model")) return {tasks:[task(id,params.layer)],providers:[{id:"model-"+id,capabilities:["text"],max_output_tokens:8192}],host_state:"running"};
    if (endpoint.endsWith("/config")) return {sections:Object.fromEntries(String(params.sections || "").split(",").map(section => [section,
      {section,settings:{},fields:{},provenance:{},edit_token:token,revision:1,scope:"role",layers:["installation","role"]}]))};
    if (endpoint.endsWith("/memory/overview")) return {facts:{active:1,total:1},episodes:{total:0},external_memory:{}};
    if (endpoint.endsWith("/memory")) return {items:[{atom_id:"fact-"+id,state:"active",revision:1,predicate:"当前事实",representation:labels[id]+"的事实"}],next_cursor:null};
    if (endpoint.endsWith("/growth")) return {role_id:"shared-role",items:[{growth_id:"growth-"+id,revision:1,kind:"goal",state:"active",
      title:labels[id]+"的成长",description:"合成成长目标",progress_note:"",source_count:1,view_token:"growth-"+id,actions:[]}],next_cursor:null};
    if (endpoint.endsWith("/life")) return {role_id:"shared-role",participant:"user-"+id,host_state:"running",view_token:"life",edit_token:"life-edit",
      config:{enabled:false,diary_enabled:false},sleep_schedule:{enabled:false,owner:{bot_id:"bot-"+id}},journals:[],activities:[],receipts:[],running:[]};
    if (endpoint.endsWith("/state")) return {state:{state:"ready",revision:1,values:{energy:75,mood:"平静",health:"稳定",hunger:"不饿",sleep:"清醒",busy:"空闲"}},settings:{},sleep_schedule:{enabled:false,owner:{bot_id:"bot-"+id}},presentation:{state:"preview"}};
    if (endpoint.endsWith("/reply")) return {settings:{enabled:true,llm_controlled:true,threshold:1,max_segments:10,min_segment_chars:1,rule_mode:"sentences",prompt:"",streaming:false,part_gap_ms:0,image_position:"after_text"},edit_token:token,recent:[],tts:{state:"ready",providers:[]}};
    if (endpoint.endsWith("/relationships")) return {role_id:"shared-role",subject_id:"user-"+id,items:[],next_cursor:null};
    return {items:[],events:[],candidates:[],signals:[],next_cursor:null,settings:{},revision:1};
  }
  return {calls,rpc,directory};
}

module.exports = {fixture, labels, roleKey};
