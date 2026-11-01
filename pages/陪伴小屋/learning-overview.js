"use strict";
window.CompanionLearningOverviewPage = (() => {
  const PREFIX = "/astrbot_plugin_private_companion/";
  const STAGES = [
    ["proposed", "提出", "var(--t-text-faint)"],
    ["active", "进行中", "var(--t-accent-solid)"],
    ["paused", "暂停", "var(--t-warn)"],
    ["achieved", "达成", "var(--t-ok)"],
    ["retired", "归档", "var(--t-line-firm)"],
  ];
  const ACTIONS = {confirm:"确认",pause:"暂停",resume:"继续",complete:"已完成",revise:"修订",withdraw:"撤回",retire:"归档"};
  const e = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const relative = value => typeof value === "string" && value.startsWith(PREFIX) ? value.slice(PREFIX.length) : "";
  let root = null, ticket = 0, readTicket = 0, sources = [], semantic = null, data = null;
  const when = value => typeof value === "string" && Number.isFinite(Date.parse(value))
    ? new Date(value).toLocaleString("zh-CN",{month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit",hour12:false}) : "时间未知";
  function routeFor(source, base, suffix = "") {
    return base + suffix;
  }

  async function api(method, route, values) {
    const product = window.CompanionProduct;
    const bridge = product?.bridgeNow?.() || await product?.bridgeFor?.();
    if (!bridge?.apiGet) throw new Error("bridge_unavailable");
    let timeout;
    let response;
    try {
      response = await Promise.race([method === "GET" ? bridge.apiGet(route, values) : bridge.apiPost(route, values),
        new Promise((_,reject)=>{timeout=setTimeout(()=>reject(new Error("bridge_timeout")),15000);})]);
    } finally { clearTimeout(timeout); }
    if (response?.status === "error") throw Object.assign(new Error("request_failed"), {code:response.data?.code || response.code || response.message});
    return response?.status === "ok" && response.data !== undefined
      ? response.data : product?.unwrap ? product.unwrap(response) : response?.data ?? response;
  }

  async function readPages(route, query) {
    const items = [];
    let after_id = null;
    const seen = new Set();
    for (let page = 0; page < 200; page += 1) {
      const value = await api("GET", route, {...query, ...(after_id ? {after_id} : {})});
      if (Array.isArray(value?.items)) items.push(...value.items);
      const next = typeof value?.next_cursor === "string" && value.next_cursor ? value.next_cursor : null;
      if (!next || seen.has(next)) return {...value, items};
      seen.add(next); after_id = next;
    }
    return {items};
  }

  async function read(current) {
    if (!root || current !== ticket || data?.writing) return;
    const request = ++readTicket;
    const notice = data?.notice || "", error = !!data?.error;
    data = {loading:true, writing:false, confirming:null, notice, error, errors:0, growth:[], counts:{pending:0,active:0,adopted:0,rules:null,scan:null}};
    render();
    const tasks = sources.map(async source => {
      const growth = relative(source.growthApi || source.api), reply = relative(source.replyApi);
      const requests = await Promise.allSettled([
        growth ? readPages(routeFor(source,growth), {limit:50}) : Promise.resolve({items:[]}),
        growth ? readPages(routeFor(source,growth,"/progress"), {limit:50}) : Promise.resolve({items:[]}),
        growth ? readPages(routeFor(source,growth,"/self-review"), {state:"pending",limit:50}) : Promise.resolve({items:[]}),
        reply ? readPages(routeFor(source,reply,"/style"), {state:"pending",limit:50}) : Promise.resolve({items:[]}),
        reply ? readPages(routeFor(source,reply,"/style"), {state:"adopted",limit:50}) : Promise.resolve({items:[]}),
        reply ? api("GET", routeFor(source,reply,"/expression")) : Promise.resolve(null),
      ]);
      const value = index => requests[index].status === "fulfilled" ? requests[index].value || {} : null;
      const growthItems=value(0)?.items||[];
      const settings=value(5)?.settings;
      return {
        errors: requests.filter(result => result.status === "rejected").length,
        growth: growthItems.map(item=>({...item,_scope:source.label,_source:source})),
        pending: (value(1)?.items||[]).length
          + (value(2)?.items||[]).filter(item=>item.kind==="ask_user").length
          + (value(3)?.items||[]).length,
        active: growthItems.filter(item=>item.state==="active").length,
        adopted: (value(4)?.items||[]).length,
        rules: Number.isInteger(settings?.rules_per_turn) ? settings.rules_per_turn : null,
        learning: value(5)?.learning || null,
        review: value(2)?.status || null,
        access: value(0)?.access || null,
      };
    });
    const selection=semantic?.global===true?{global:true}:semantic?.target&&semantic?.role
      ?{target_id:semantic.target.target_id,role_id:semantic.role.role_id}:null;
    const semanticTask=selection?api("GET","semantic",selection).then(value=>{
      const interval=value?.settings?.learning_interval_seconds;
      return {scan:Number.isFinite(interval)?interval:null, errors:0};
    }).catch(()=>({scan:null, errors:1})):Promise.resolve({scan:null,errors:0});
    const [sourceResults, semanticResult] = await Promise.all([
      Promise.allSettled(tasks), semanticTask,
    ]);
    if (!root || current !== ticket || request !== readTicket) return;
    const valid = sourceResults.filter(result => result.status === "fulfilled").map(result => result.value);
    data.errors = sourceResults.reduce((total, result) => total +
      (result.status === "fulfilled" ? result.value.errors : 1), 0) + semanticResult.errors;
    data.growth = valid.flatMap(result => result.growth);
    data.counts.pending = valid.reduce((total,result)=>total+result.pending,0);
    data.counts.active = valid.reduce((total,result)=>total+result.active,0);
    data.counts.adopted = valid.reduce((total,result)=>total+result.adopted,0);
    const configured = valid.find(result=>Number.isInteger(result.rules));
    data.counts.rules = configured?.rules ?? null;
    data.counts.scan = semanticResult.scan;
    data.learning = valid.map(result=>result.learning).find(item=>item&&typeof item==="object"&&item.state)||null;
    data.review = valid.map(result=>result.review).find(item=>item&&typeof item==="object"&&item.state)||null;
    data.access = valid.map(result=>result.access).find(item=>item&&typeof item==="object"&&item.settings)||null;
    data.loading=false;
    render();
  }

  function duration(seconds) {
    if (!Number.isFinite(seconds)) return "—";
    if (seconds % 3600 === 0) return `${seconds / 3600} 小时`;
    if (seconds % 60 === 0) return `${seconds / 60} 分钟`;
    return `${seconds} 秒`;
  }

  const LEARNING_STATES = {
    not_run: "还没运行过",
    waiting_for_new_messages: "等待新消息",
    waiting_for_sample_review: "等待样本审核",
    insufficient_samples: "样本不足",
    source_not_selected: "当前私聊未选中为学习来源",
    learning_disabled: "该窗口未开启表达学习",
    capacity_reached: "待审与启用规则已达上限",
    daily_budget_reached: "今日群聊学习批次已用完",
    failed: "上次学习失败",
    blocked: "来源校验未通过",
  };
  // 失败码的中文原因：只解释稳定 code 的含义与处置动作，原始 code 仍照常显示，
  // 便于和日志、台账对照。
  const LEARNING_FAILURE_CODES = {
    model_estimation_failed: "本地预估没能完成，先按日志里的 error_type 排查",
    host_model_limit_input: "要发送的材料超过本地输入上限；请调大「最大输入估算」或减少参考规则",
    host_model_limit_output: "输出上限不足以写完这次结果；请调高该任务的输出上限",
    model_output_truncated: "模型输出被输出上限截断；请调高该任务的输出上限",
  };
  function learningLine(item) {
    if (!item || typeof item !== "object" || !item.state) return "";
    const label = LEARNING_STATES[item.state] || item.state;
    const counts = item.state === "capacity_reached" && Number.isInteger(item.active)
      ? `（${item.active}/${item.limit ?? "?"} 条规则）`
      : Number.isInteger(item.available)
        ? `（${item.available}/${Number.isInteger(item.required) ? item.required : "?"} 条样本）` : "";
    const reason = item.code && LEARNING_FAILURE_CODES[item.code] ? `（${e(LEARNING_FAILURE_CODES[item.code])}）` : "";
    const failure = item.code ? ` · ${item.code}` : "";
    const last = item.last_run_at ? ` · 上次 ${when(item.last_run_at)}` : "";
    return `表达学习：${e(label)}${e(counts)}${reason}${e(failure)}${e(last)}`;
  }

  const REVIEW_STATES = {
    not_run: "还没运行过",
    failed: "上次失败",
    blocked: "来源校验未通过",
    no_reviewable_window: "暂无可复盘窗口",
    already_attempted: "该窗口已复盘过",
    succeeded: "最近一次已产出",
    completed: "最近一次已产出",
  };
  function reviewLine(item) {
    if (!item || typeof item !== "object" || !item.state) return "";
    const label = REVIEW_STATES[item.state] || item.state;
    const failure = item.code ? ` · ${item.code}` : "";
    const last = item.last_run_at ? ` · 上次 ${when(item.last_run_at)}` : "";
    const next = item.state === "failed" && item.next_check_at
      ? ` · 下次 ${when(item.next_check_at)}` : "";
    return `每日复盘：${e(label)}${e(failure)}${e(last)}${e(next)}`;
  }

  function growthLine(access) {
    if (!access || typeof access !== "object" || !access.settings) return "";
    const effective = access.effective || {};
    if (effective.proposals_enabled?.allowed) return "";
    const reason = effective.proposals_enabled?.reason || effective.read_enabled?.reason || "";
    return reason
      ? `成长链路：成长提案权限不可用（${reason}）；开启后角色才会提出技能或长期目标。`
      : "成长链路：尚未开启成长提案权限；开启后角色才会提出技能或长期目标。";
  }

  function growthCard(item) {
    const state=STAGES.find(([key])=>key===item.state);
    const title=item.unavailable_reason?"来源已不可读取":item.title||"未命名记录";
    const available = !item.unavailable_reason;
    const actions = (item.actions || []).filter(action=>ACTIONS[action]).slice(0,2);
    return `<article class="lh-growth-card" data-state="${e(item.state)}">
      <span class="lh-growth-card-line" aria-hidden="true"></span>
      <div class="lh-growth-card-meta"><span>${e(item.kind==="goal"?"目标":"技能")}</span><span>v${e(item.revision)}</span><time>${e(when(item.updated_at))}</time></div>
      <strong>${e(title)}</strong>
      ${item.progress_note&&!item.unavailable_reason?`<p>${e(item.progress_note)}</p>`:""}
      <div class="lh-growth-actions">${actions.map(action=>`<button type="button" data-lh-action="${action}" data-lh-id="${e(item.growth_id)}" data-lh-scope="${e(item._source.key||"")}" ${data.writing||data.loading?"disabled":""}>${ACTIONS[action]}</button>`).join("")}</div>
      <footer><span>${e(item.source_count??0)} 条来源</span><button type="button" data-lh-open="${e(item.growth_id)}" data-lh-scope="${e(item._source.key||"")}" ${data.writing?"disabled":""}>${available?"查看":"核对"}</button></footer>
    </article>`;
  }

  function render() {
    if (!root || !data) return;
    const counts=data.counts;
    const cards=[
      ["待裁决",counts.pending,"progress-review","clipboard-list"],
      ["培育中",counts.active,"active-growth","sprout"],
      ["规则注入",counts.rules===null?"—":`每轮 ${counts.rules} 条`,`${counts.adopted} 条已采纳`,"messages-square"],
      ["语义扫描",duration(counts.scan),"semantic-interval","scan-search"],
    ];
    const lanes=STAGES.map(([state,label,color])=>{
      const items=data.growth.filter(item=>item.state===state);
      return `<section class="lh-overview-lane" data-state="${state}"><header><span class="lh-lane-dot" style="--lane:${color}"></span><strong>${label}</strong><span>${items.length}</span></header>
        <div class="lh-lane-items">${items.length?items.slice(0,4).map(growthCard).join(""):`<p class="lh-lane-empty">—</p>`}</div></section>`;
    }).join("");
    root.innerHTML=`<div class="lh-overview-toolbar"><span>${data.errors?"部分记录归属暂不可用":`汇总 ${sources.length} 个记录归属`}</span>
      <button type="button" class="lh-refresh" data-lh-refresh aria-label="重新读取" title="重新读取" ${data.loading||data.writing?"disabled":""}><i data-lucide="refresh-cw"></i></button></div>
      ${data.notice?`<p class="lh-inline-status${data.error?" is-error":""}" role="status">${e(data.notice)}</p>`:""}
      ${data.learning?`<p class="lh-inline-status" role="status">${learningLine(data.learning)}</p>`:""}
      ${data.review?`<p class="lh-inline-status" role="status">${reviewLine(data.review)}</p>`:""}
      ${data.access?`<p class="lh-inline-status" role="status">${growthLine(data.access)}</p>`:""}
      <section class="lh-kpis" aria-label="学习数据">${cards.map(([label,value,detail,icon])=>`<article class="lh-kpi">
        <span class="lh-kpi-icon" aria-hidden="true"><i data-lucide="${icon}"></i></span><span class="lh-kpi-label">${label}</span>
        <strong>${e(value)}</strong><small>${label==="待裁决"?"进展 · 表达 · 指导":label==="培育中"?"技能与目标":label==="规则注入"?e(detail):label==="语义扫描"?"群词义整理间隔":""}</small></article>`).join("")}</section>
      <section class="lh-overview-track"><header><h2>培育轨道</h2><a href="#manage/learning/chain">全部记录</a></header>
        <div class="lh-track-axis" aria-hidden="true">${STAGES.map(([state,label])=>`<span data-state="${state}"><i></i><b>${label}</b></span>`).join("")}</div>
        <div class="lh-overview-lanes">${lanes}</div></section>
      ${data.confirming?`<div class="lh-review-confirm" role="alertdialog" aria-label="确认${ACTIONS[data.confirming.action]}"><span>${ACTIONS[data.confirming.action]}这条记录？</span><button type="button" data-lh-confirm="yes">确认${ACTIONS[data.confirming.action]}</button><button type="button" data-lh-confirm="no">取消</button></div>`:""}
      ${data.loading&&!data.growth.length?`<p class="lh-empty">正在读取…</p>`:""}
      ${!data.loading&&!data.growth.length&&!counts.pending&&!counts.active&&!counts.adopted
        ?`<p class="lh-empty">学习还没有产出。每日复盘会在后台自动运行，表达学习可在「表达学习」点「立即学习」，成长记录需要先给对应用户开启成长提案权限。</p>`
        :!data.loading&&!data.growth.length?`<p class="lh-empty">暂无成长记录</p>`:""}`;
    window.lucide?.createIcons({attrs:{"aria-hidden":"true"}});
  }

  function openRecord(item) {
    window.dispatchEvent(new CustomEvent("companion:learning-open",{detail:{
      target_id:item._source.target?.target_id,role_id:item._source.role?.role_id,growth_id:item.growth_id,
    }}));
  }
  async function command(item, action) {
    if(data?.writing||!item.actions?.includes(action))return;
    if(action==="revise"){openRecord(item);return;}
    const current=ticket;
    data.writing=true;data.confirming=null;render();
    try {
      const path=routeFor(item._source,relative(item._source.growthApi||item._source.api),"/command");
      await api("POST",path,{growth_id:item.growth_id,action,expected_revision:item.revision,view_token:item.view_token,
        operation_key:`learning-${action}-${Date.now()}-${Math.random().toString(36).slice(2,8)}`});
      if(!root||current!==ticket)return;
      data.writing=false;data.notice=`${ACTIONS[action]}已完成。`;data.error=false;
    } catch(error) {
      if(!root||current!==ticket)return;
      data.writing=false;data.notice=["growth_revision_conflict","growth_view_stale","growth_transition_invalid"].includes(error.code)
        ?"记录已变化，已重新读取。":"未收到操作确认，请重新读取后核对。";data.error=true;
    }
    await read(current);
  }
  function onClick(event) {
    if(data?.writing)return;
    const confirmation=event.target.closest("[data-lh-confirm]");
    if(confirmation&&data.confirming){
      const pending=data.confirming;data.confirming=null;
      if(confirmation.dataset.lhConfirm==="yes")void command(pending.item,pending.action);
      else render();
      return;
    }
    if (event.target.closest("[data-lh-refresh]")) { void read(ticket); return; }
    const action=event.target.closest("[data-lh-action]");
    if(action){
      const item=data?.growth.find(row=>row.growth_id===action.dataset.lhId&&row._source.key===action.dataset.lhScope);
      if(!item?.actions?.includes(action.dataset.lhAction))return;
      if(["retire","withdraw"].includes(action.dataset.lhAction)){data.confirming={item,action:action.dataset.lhAction};render();}
      else void command(item,action.dataset.lhAction);
      return;
    }
    const selected = event.target.closest("[data-lh-open]");
    if(selected){
      const item=data?.growth.find(row=>row.growth_id===selected.dataset.lhOpen&&row._source.key===selected.dataset.lhScope);
      if(item)openRecord(item);
    }
  }
  function mount(host, options = {}) {
    unmount(); root=host; sources=Array.isArray(options.sources)?options.sources:[]; semantic=options.semantic||null;
    root?.addEventListener("click",onClick);
    const current=++ticket;
    if(sources.length)void read(current);
    else{data={loading:false,errors:0,growth:[],counts:{pending:0,active:0,adopted:0,rules:null,scan:null}};render();}
  }
  function unmount() { ticket++;readTicket++; root?.removeEventListener("click",onClick); root=null; sources=[]; semantic=null; data=null; }
  return {mount,unmount,get state(){return data;}};
})();
