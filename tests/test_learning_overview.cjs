"use strict";
const test=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");

const HOST="astrbot_plugin_private_companion";
const source=fs.readFileSync(path.join(__dirname,"..","pages","陪伴小屋","learning-overview.js"),"utf8");

test("总览汇总全局学习队列、成长阶段和全局策略",async()=>{
  const calls=[];
  const bridge={apiGet:async(route,query)=>{
    calls.push({route,query});
    const data=route.endsWith("/progress")?{items:[{},{}]}
      :route.endsWith("/self-review")?{items:[{kind:"ask_user"}],
        status:{state:"succeeded",last_run_at:"2026-10-31T02:00:00+00:00"}}
      :route.endsWith("/style")?{items:query.state==="pending"?[{},{}]:[{},{},{},{}]}
      :route.endsWith("/expression")?{settings:{rules_per_turn:2},
        learning:{state:"insufficient_samples",available:1,required:2}}
      :route==="semantic"?{settings:{learning_interval_seconds:21600}}
      :{items:[{growth_id:"one",kind:"skill",state:"active",title:"面向与发声",revision:2,
        source_count:3,updated_at:"2026-10-07T08:00:00+00:00",progress_note:"完成基础练习"}]};
    return {status:"ok",data};
  }};
  const window={CompanionProduct:{bridgeNow:()=>bridge,unwrap:value=>value.data}};
  const context=vm.createContext({window,setTimeout,clearTimeout});
  vm.runInContext(source,context);
  const listeners=new Map();
  const root={isConnected:true,innerHTML:"",addEventListener:(name,callback)=>listeners.set(name,callback),
    removeEventListener:name=>listeners.delete(name)};
  const flush=()=>new Promise(resolve=>setTimeout(resolve,0));
  const waitFor=async predicate=>{
    for(let attempt=0;attempt<50;attempt++){
      if(predicate())return;
      await flush();
    }
    assert.fail("learning overview data did not settle");
  };
  await context.window.CompanionLearningOverviewPage.mount(root,{
    sources:[{growthApi:`/${HOST}/targets/profile-a/roles/role-a/growth`,
      replyApi:`/${HOST}/targets/profile-a/roles/role-a/reply`,label:"私聊 · 角色 A"}],
    semantic:{global:true},
  });
  await waitFor(()=>context.window.CompanionLearningOverviewPage.state?.loading===false);
  const state=context.window.CompanionLearningOverviewPage.state;
  assert.equal(state.counts.pending,5);
  assert.equal(state.counts.active,1);
  assert.equal(state.counts.adopted,4);
  assert.equal(state.counts.rules,2);
  assert.equal(state.counts.scan,21600);
  assert.ok(calls.some(item=>item.route==="semantic"&&item.query.global===true));
  assert.ok(calls.every(item=>item.route!=="semantic"||!item.query.target_id));
  assert.match(root.innerHTML,/待裁决/);
  assert.match(root.innerHTML,/面向与发声/);
  assert.match(root.innerHTML,/每轮 2 条/);
  assert.match(root.innerHTML,/6 小时/);
  assert.match(root.innerHTML,/表达学习/);
  assert.match(root.innerHTML,/样本不足/);
  assert.match(root.innerHTML,/每日复盘/);
  assert.match(root.innerHTML,/最近一次已产出/);
  context.window.CompanionLearningOverviewPage.unmount();
  assert.equal(listeners.size,0);
});

test("总览为空时说明学习链路的三个入口",async()=>{
  const bridge={apiGet:async(route,query)=>{
    const data=route.endsWith("/style")?{items:[]}
      :route.endsWith("/self-review")?{items:[],
        status:{state:"failed",code:"self_review_output_invalid",
          last_run_at:"2026-10-31T02:00:00+00:00",next_check_at:"2026-10-31T08:00:00+00:00"}}
      :route.endsWith("/expression")?{settings:{rules_per_turn:2},
        learning:{state:"insufficient_samples",available:1,required:2}}
      :route==="semantic"?{settings:{learning_interval_seconds:21600}}
      :{items:[]};
    return {status:"ok",data};
  }};
  const window={CompanionProduct:{bridgeNow:()=>bridge,unwrap:value=>value.data}};
  const context=vm.createContext({window,setTimeout,clearTimeout});
  vm.runInContext(source,context);
  const root={isConnected:true,innerHTML:"",addEventListener(){},removeEventListener(){}};
  const flush=()=>new Promise(resolve=>setTimeout(resolve,0));
  await context.window.CompanionLearningOverviewPage.mount(root,{
    sources:[{growthApi:`/${HOST}/targets/profile-a/roles/role-a/growth`,
      replyApi:`/${HOST}/targets/profile-a/roles/role-a/reply`,label:"私聊 · 角色 A"}],
    semantic:{global:true},
  });
  for(let attempt=0;attempt<50;attempt++){
    if(context.window.CompanionLearningOverviewPage.state?.loading===false)break;
    await flush();
  }
  assert.equal(context.window.CompanionLearningOverviewPage.state.counts.pending,0);
  assert.match(root.innerHTML,/学习还没有产出/);
  assert.match(root.innerHTML,/立即学习/);
  assert.match(root.innerHTML,/成长提案权限/);
  assert.match(root.innerHTML,/每日复盘：上次失败/);
  assert.match(root.innerHTML,/self_review_output_invalid/);
  context.window.CompanionLearningOverviewPage.unmount();
});
