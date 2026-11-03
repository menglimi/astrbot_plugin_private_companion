"use strict";
const test=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");

const HOST="astrbot_plugin_private_companion";
const source=fs.readFileSync(path.join(__dirname,"..","pages","陪伴小屋","learning-overview.js"),"utf8");

const SOURCE={growthApi:`/${HOST}/targets/profile-a/roles/role-a/growth`,
  replyApi:`/${HOST}/targets/profile-a/roles/role-a/reply`,label:"私聊 · 角色 A"};

// `GET {reply}/expression` answers with the resolved *local window* settings,
// which only hold the participant/group switches, plus the role-wide learning
// status under `learning`. The page must read the per-turn rule count from
// there, so the fixtures keep the real shape instead of a convenient one.
function expression({switches={private_learning_enabled:true}, learning={}}={}) {
  return {settings:switches,learning};
}

// One settled overview: 4 pending, 1 active growth record, 4 adopted rules.
function settledBridge(calls=[]) {
  return {apiGet:async(route,query)=>{
    calls.push({route,query});
    const data=route.endsWith("/progress")?{items:[{},{}]}
      :route.endsWith("/self-review")?{items:[{kind:"ask_user"}],
        status:{state:"succeeded",last_run_at:"2026-10-31T02:00:00+00:00"}}
      :route.endsWith("/style")?{items:query.state==="pending"?[{},{}]:[{},{},{},{}]}
      :route.endsWith("/expression")?expression({learning:{state:"insufficient_samples",
        available:1,required:2,rules_per_turn:2}})
      :route==="semantic"?{settings:{learning_interval_seconds:21600}}
      :{items:[{growth_id:"one",kind:"skill",state:"active",title:"面向与发声",revision:2,
        source_count:3,updated_at:"2026-10-07T08:00:00+00:00",progress_note:"完成基础练习"}],
        access:{settings:{read_enabled:true,proposals_enabled:false},
          effective:{read_enabled:{allowed:true},proposals_enabled:{allowed:false,reason:"authorization_missing"}}}};
    return {status:"ok",data};
  }};
}

function harness(bridge, options={}) {
  let current=bridge;
  const window={CompanionProduct:{bridgeNow:()=>current,unwrap:value=>value.data}};
  const context=vm.createContext({window,setTimeout,clearTimeout});
  vm.runInContext(source,context);
  const listeners=new Map();
  const attributes={};
  const root={isConnected:true,innerHTML:"",
    addEventListener:(name,callback)=>listeners.set(name,callback),
    removeEventListener:name=>listeners.delete(name),
    setAttribute:(name,value)=>{attributes[name]=value;}};
  const flush=()=>new Promise(resolve=>setTimeout(resolve,0));
  const page=context.window.CompanionLearningOverviewPage;
  return {
    page,root,listeners,attributes,flush,
    setBridge:value=>{current=value;},
    mount:extra=>page.mount(root,{sources:[SOURCE],semantic:null,...options,...extra}),
    async settle() {
      for(let attempt=0;attempt<200;attempt+=1){
        if(page.state?.loading===false)return;
        await flush();
      }
      assert.fail("learning overview data did not settle");
    },
    click:selector=>listeners.get("click")({target:{closest:value=>
      value===selector?{dataset:{}}:null}}),
  };
}

test("总览汇总全局学习队列、成长阶段和全局策略",async()=>{
  const calls=[];
  const page=harness(settledBridge(calls),{semantic:{global:true}});
  page.mount();
  await page.settle();
  const state=page.page.state;
  assert.equal(state.counts.pending,5);
  assert.equal(state.counts.active,1);
  assert.equal(state.counts.adopted,4);
  assert.equal(state.counts.rules,2);
  assert.equal(state.counts.scan,21600);
  assert.ok(calls.some(item=>item.route==="semantic"&&item.query.global===true));
  assert.ok(calls.every(item=>item.route!=="semantic"||!item.query.target_id));
  assert.match(page.root.innerHTML,/待裁决/);
  assert.match(page.root.innerHTML,/面向与发声/);
  assert.match(page.root.innerHTML,/每轮 2 条/);
  assert.match(page.root.innerHTML,/6 小时/);
  assert.match(page.root.innerHTML,/表达学习/);
  assert.match(page.root.innerHTML,/样本不足/);
  assert.match(page.root.innerHTML,/每日复盘/);
  assert.match(page.root.innerHTML,/最近一次已产出/);
  assert.match(page.root.innerHTML,/成长提案权限不可用/);
  page.page.unmount();
  assert.equal(page.listeners.size,0);
});

test("规则注入读学习策略里的每轮注入上限，字段缺失时显示 —",async()=>{
  const renderFor=async reply=>{
    const page=harness({apiGet:async route=>{
      const data=route.endsWith("/style")?{items:[]}
        :route.endsWith("/self-review")?{items:[],status:{state:"succeeded"}}
        :route.endsWith("/expression")?reply
        :{items:[]};
      return {status:"ok",data};
    }});
    page.mount();
    await page.settle();
    const state=page.page.state;
    const html=page.root.innerHTML;
    page.page.unmount();
    return {state,html};
  };

  // The strategy value wins when a page happens to see both spellings.
  const strategy=await renderFor(expression({learning:{state:"already_attempted",rules_per_turn:2}}));
  assert.equal(strategy.state.counts.rules,2);
  assert.match(strategy.html,/每轮 2 条/);

  // The local settings fallback keeps the card alive for an older backend.
  const fallback=await renderFor(expression({switches:{private_learning_enabled:true,rules_per_turn:9},
    learning:{state:"already_attempted"}}));
  assert.equal(fallback.state.counts.rules,9);
  assert.match(fallback.html,/每轮 9 条/);

  // Neither spelling: keep the em dash instead of inventing a count.
  const missing=await renderFor(expression({learning:{state:"already_attempted"}}));
  assert.equal(missing.state.counts.rules,null);
  assert.doesNotMatch(missing.html,/每轮 \d+ 条/);
  assert.match(missing.html,/规则注入<\/span>\s*<strong>—<\/strong>/);
});

test("总览为空时说明学习链路的三个入口",async()=>{
  const page=harness({apiGet:async route=>{
    const data=route.endsWith("/style")?{items:[]}
      :route.endsWith("/self-review")?{items:[],
        status:{state:"failed",code:"self_review_output_invalid",
          last_run_at:"2026-10-31T02:00:00+00:00",next_check_at:"2026-10-31T08:00:00+00:00"}}
      :route.endsWith("/expression")?expression({learning:{state:"insufficient_samples",
        available:1,required:2,rules_per_turn:2}})
      :route==="semantic"?{settings:{learning_interval_seconds:21600}}
      :{items:[]};
    return {status:"ok",data};
  }},{semantic:{global:true}});
  page.mount();
  await page.settle();
  assert.equal(page.page.state.counts.pending,0);
  assert.match(page.root.innerHTML,/学习还没有产出/);
  assert.match(page.root.innerHTML,/立即学习/);
  assert.match(page.root.innerHTML,/成长提案权限/);
  assert.match(page.root.innerHTML,/每日复盘：上次失败/);
  assert.match(page.root.innerHTML,/self_review_output_invalid/);
  page.page.unmount();
});

test("表达学习失败码给出中文原因并保留原始 code",async()=>{
  const renderFor=async code=>{
    const page=harness({apiGet:async route=>{
      const data=route.endsWith("/style")?{items:[]}
        :route.endsWith("/self-review")?{items:[],status:{state:"succeeded"}}
        :route.endsWith("/expression")?expression({learning:{state:"failed",code,
          last_run_at:"2026-11-01T07:08:39+08:00",rules_per_turn:2}})
        :route==="semantic"?{settings:{learning_interval_seconds:21600}}
        :{items:[]};
      return {status:"ok",data};
    }});
    page.mount();
    await page.settle();
    const html=page.root.innerHTML;
    page.page.unmount();
    return html;
  };
  const input=await renderFor("host_model_limit_input");
  assert.match(input,/上次学习失败/);
  assert.match(input,/超过本地输入上限/);
  assert.match(input,/host_model_limit_input/);
  const output=await renderFor("host_model_limit_output");
  assert.match(output,/输出上限不足以写完这次结果/);
  assert.match(output,/host_model_limit_output/);
  const estimation=await renderFor("model_estimation_failed");
  assert.match(estimation,/本地预估没能完成/);
  assert.match(estimation,/model_estimation_failed/);
  const truncated=await renderFor("model_output_truncated");
  assert.match(truncated,/被输出上限截断/);
  assert.match(truncated,/model_output_truncated/);
  const unknown=await renderFor("self_review_output_invalid");
  assert.match(unknown,/self_review_output_invalid/);
  assert.doesNotMatch(unknown,/本地预估没能完成|超过本地输入上限/);
});

test("表达学习已学过的批次显示中文状态而不是机器码",async()=>{
  const page=harness({apiGet:async route=>{
    const data=route.endsWith("/style")?{items:[]}
      :route.endsWith("/self-review")?{items:[],status:{state:"succeeded"}}
      :route.endsWith("/expression")?expression({learning:{state:"already_attempted",
        last_run_at:"2026-11-01T19:27:28+08:00",rules_per_turn:2}})
      :route==="semantic"?{settings:{learning_interval_seconds:21600}}
      :{items:[]};
    return {status:"ok",data};
  }});
  page.mount();
  await page.settle();
  const html=page.root.innerHTML;
  assert.match(html,/本批素材已学习/);
  assert.doesNotMatch(html,/表达学习[^<]*already_attempted/);
  assert.match(html,/上次 11\/1 19:27/);
  page.page.unmount();
});

test("重新读取期间保留上一轮 KPI 与培育轨道，不清零不重画",async()=>{
  const page=harness(settledBridge());
  page.mount();
  await page.settle();
  const settled=page.page.state;
  assert.equal(settled.counts.pending,5);
  assert.equal(page.root.innerHTML.includes("面向与发声"),true);

  // Hold the next read open so the loading tick can be inspected as it renders.
  let release;
  const gate=new Promise(resolve=>{release=resolve;});
  const replay=settledBridge();
  page.setBridge({apiGet:async(route,query)=>{await gate;return replay.apiGet(route,query);}});
  page.click("[data-lh-refresh]");

  // The refresh must keep the settled view instead of flashing zeros and
  // collapsing the growth track, which used to jump the scroll position.
  assert.equal(page.page.state.loading,true);
  assert.equal(page.page.state.counts.pending,5);
  assert.equal(page.page.state.counts.adopted,4);
  assert.equal(page.page.state.growth.length,1);
  assert.equal(page.attributes["aria-busy"],"true");
  assert.match(page.root.innerHTML,/每轮 2 条/);
  assert.match(page.root.innerHTML,/面向与发声/);
  assert.doesNotMatch(page.root.innerHTML,/正在读取/);
  assert.match(page.root.innerHTML,/规则注入<\/span>\s*<strong>每轮 2 条<\/strong>/);
  assert.doesNotMatch(page.root.innerHTML,/待裁决<\/span>\s*<strong>0<\/strong>/);

  release();
  await page.settle();
  assert.equal(page.page.state.counts.pending,5);
  assert.equal(page.attributes["aria-busy"],"false");
  assert.match(page.root.innerHTML,/面向与发声/);
  page.page.unmount();
});

test("全部记录归属读取失败时进入错误态且不下空态结论",async()=>{
  const failing={apiGet:async()=>{throw new Error("bridge_unavailable");}};
  const whole=harness(failing);
  whole.mount();
  await whole.settle();
  assert.equal(whole.page.state.loading,false);
  assert.equal(whole.page.state.failedSources,1);
  assert.match(whole.root.innerHTML,/全部记录归属读取失败/);
  assert.match(whole.root.innerHTML,/重新读取/);
  assert.match(whole.root.innerHTML,/lh-inline-status is-error/);
  assert.doesNotMatch(whole.root.innerHTML,/部分记录归属暂不可用/);
  assert.doesNotMatch(whole.root.innerHTML,/学习还没有产出/);
  assert.doesNotMatch(whole.root.innerHTML,/暂无成长记录/);
  assert.doesNotMatch(whole.root.innerHTML,/data-lh-refresh[^>]*disabled/);
  whole.page.unmount();

  // A single unavailable owner is still a partial view, so keep saying so.
  const partial=harness({apiGet:async route=>{
    if(route.includes("role-b"))throw new Error("bridge_unavailable");
    const data=route.endsWith("/style")?{items:[]}
      :route.endsWith("/self-review")?{items:[],status:{state:"succeeded"}}
      :route.endsWith("/expression")?expression({learning:{state:"already_attempted",rules_per_turn:2}})
      :{items:[]};
    return {status:"ok",data};
  }});
  partial.mount({sources:[SOURCE,{growthApi:`/${HOST}/targets/profile-b/roles/role-b/growth`,
    replyApi:`/${HOST}/targets/profile-b/roles/role-b/reply`,label:"私聊 · 角色 B"}]});
  await partial.settle();
  assert.equal(partial.page.state.failedSources,1);
  assert.match(partial.root.innerHTML,/部分记录归属暂不可用/);
  assert.doesNotMatch(partial.root.innerHTML,/全部记录归属读取失败/);
  partial.page.unmount();

  // A group source has no growth route; its three reply reads are the whole
  // attempted set, so a reply outage is still a full failure for that owner.
  const group=harness(failing);
  group.mount({sources:[{replyApi:`/${HOST}/targets/profile-a/roles/role-a/reply`,label:"群聊 · 角色 A"}]});
  await group.settle();
  assert.equal(group.page.state.failedSources,1);
  assert.match(group.root.innerHTML,/全部记录归属读取失败/);
  assert.doesNotMatch(group.root.innerHTML,/学习还没有产出/);
  group.page.unmount();
});

test("全部来源失败的刷新保留上一轮 KPI 与轨道，只更新错误状态",async()=>{
  const page=harness(settledBridge(),{semantic:{global:true}});
  page.mount();
  await page.settle();
  const settled=page.page.state;
  assert.equal(settled.counts.pending,5);
  assert.equal(settled.counts.active,1);
  assert.equal(settled.counts.adopted,4);
  assert.equal(settled.counts.rules,2);
  assert.equal(settled.counts.scan,21600);
  assert.equal(settled.growth.length,1);

  page.setBridge({apiGet:async()=>{throw new Error("bridge_unavailable");}});
  page.click("[data-lh-refresh]");
  await page.settle();

  // The failed refresh must not replace the last known numbers with zeros: the
  // error line says the read failed, and the previous view stays on screen.
  const state=page.page.state;
  assert.equal(state.loading,false);
  assert.equal(state.failedSources,1);
  assert.equal(state.counts.pending,5);
  assert.equal(state.counts.active,1);
  assert.equal(state.counts.adopted,4);
  assert.equal(state.counts.rules,2);
  assert.equal(state.counts.scan,21600);
  assert.equal(state.growth.length,1);
  assert.match(page.root.innerHTML,/全部记录归属读取失败/);
  assert.match(page.root.innerHTML,/每轮 2 条/);
  assert.match(page.root.innerHTML,/面向与发声/);
  assert.match(page.root.innerHTML,/样本不足/);
  assert.match(page.root.innerHTML,/最近一次已产出/);
  assert.match(page.root.innerHTML,/成长提案权限不可用/);
  assert.doesNotMatch(page.root.innerHTML,/学习还没有产出/);
  assert.doesNotMatch(page.root.innerHTML,/待裁决<\/span>\s*<strong>0<\/strong>/);
  page.page.unmount();
});

test("来源没有任何可读路由时按读取失败处理，不落入空态结论",async()=>{
  // A damaged profile can hand over a record owner with neither route. It cannot
  // be read at all, so it must count as a failed owner instead of looking empty.
  const broken=harness({apiGet:async()=>({status:"ok",data:{items:[]}})});
  broken.mount({sources:[{label:"私聊 · 无路由"}]});
  await broken.settle();
  assert.equal(broken.page.state.failedSources,1);
  assert.ok(broken.page.state.errors>=1);
  assert.equal(broken.page.state.counts.pending,0);
  assert.match(broken.root.innerHTML,/全部记录归属读取失败/);
  assert.doesNotMatch(broken.root.innerHTML,/学习还没有产出/);
  assert.doesNotMatch(broken.root.innerHTML,/暂无成长记录/);
  broken.page.unmount();

  // Next to a readable owner it is a partial view, so the page must say so
  // instead of concluding that learning produced nothing.
  const partial=harness({apiGet:async route=>{
    const data=route.endsWith("/style")?{items:[]}
      :route.endsWith("/self-review")?{items:[],status:{state:"succeeded"}}
      :route.endsWith("/expression")?expression({learning:{state:"already_attempted",rules_per_turn:2}})
      :{items:[]};
    return {status:"ok",data};
  }});
  partial.mount({sources:[{label:"私聊 · 无路由"},SOURCE]});
  await partial.settle();
  assert.equal(partial.page.state.failedSources,1);
  assert.match(partial.root.innerHTML,/部分记录归属暂不可用/);
  assert.doesNotMatch(partial.root.innerHTML,/学习还没有产出/);
  partial.page.unmount();
});
