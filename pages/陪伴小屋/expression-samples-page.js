"use strict";
window.CompanionExpressionSamplesPage = (() => {
  const PREFIX = "/astrbot_plugin_private_companion/";
  const FILTERS = [["all", "全部"], ["pending", "待审"], ["approved", "已允许"], ["rejected", "已排除"]];
  const STATES = {pending: "待审", approved: "已允许", rejected: "已排除"};
  const ERRORS = {
    bridge_unavailable: "页面连接暂不可用，请重新读取。",
    bridge_timeout: "读取超时，请重试。",
    expression_sample_unavailable: "该来源暂无可读取的消息素材。",
    expression_sample_changed: "素材已变化，请重新读取后再操作。",
    expression_view_stale: "素材状态已更新，请重新读取。",
    expression_binding_changed: "这个来源的角色归属已变化，请重新读取。",
    expression_sample_review_invalid: "素材审核记录异常，请重新读取。",
    expression_group_context_required: "这个来源还没有可用于表达学习的对话，先正常聊一次再读取。",
    role_binding_missing: "这个来源还没有绑定角色，重新读取角色与会话后再看。",
    model_task_unregistered: "表达学习模型暂不可用，请检查模型连接。",
    staging_not_initialized: "角色数据尚未加载，请重新读取。",
    staging_host_terminated: "陪伴服务已停止。",
    dashboard_identity_required: "登录状态已失效，请重新打开陪伴小屋。",
  };
  const e = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c]));
  /* Only machine codes are meaningful; older page bridges forward the host
     message instead of the code, so both fields are inspected. */
  const errorCode = value => typeof value === "string" && /^[a-z][a-z0-9_]{2,80}$/.test(value) ? value : "";
  const issueCode = problem => errorCode(problem?.code) || errorCode(problem?.message) || "";
  const readReason = value => ERRORS[errorCode(value)] || "该来源暂时无法读取素材。";
  const time = value => Number.isFinite(Date.parse(value))
    ? new Date(value).toLocaleString("zh-CN", {hour12:false, month:"numeric", day:"numeric", hour:"2-digit", minute:"2-digit"}) : "时间未知";
  let root, apiBase = "", scopeKey = "", sources = [], rowsBySource = {}, ticket = 0, readTicket = 0, timer;
  let items = [], counts = {}, runtime = {}, filter = "all", reviewEnabled = false;
  let busy = false, writing = false, loaded = false, available = false, notice = "", error = false;
  let readIssues = [];
  const mounted = current => !!root?.isConnected && ticket === current;
  const message = code => ERRORS[code] || "操作未完成，请重新读取后核对。";
  const sourceLabel = source => source?.label || "未知来源";
  const canLearnSource = row => {
    const value=row?.runtime||{};
    const minimum=Number(value.minimum_samples);
    return row?.loaded&&!value.running&&value.learning_enabled&&Number.isFinite(minimum)&&minimum>0
      &&Number(value.eligible_samples||0)>=minimum
      &&(value.new_messages==null||Number(value.new_messages||0)>=Number(value.required_new_messages||0)
        &&Number(value.group_batches_today||0)<Number(value.group_daily_batch_limit||0));
  };
  function project(){
    const rows=Object.values(rowsBySource);
    items=rows.flatMap(row=>row.items);
    counts=rows.reduce((total,row)=>{for(const [key,value] of Object.entries(row.counts||{}))total[key]=(total[key]||0)+Number(value||0);return total;},{});
    const runtimes=rows.map(row=>row.runtime||{});
    runtime={running:runtimes.some(value=>value.running),learning_enabled:runtimes.some(value=>value.learning_enabled),
      application_enabled:runtimes.some(value=>value.application_enabled),eligible_samples:runtimes.reduce((sum,value)=>sum+Number(value.eligible_samples||0),0),
      minimum_samples:runtimes.reduce((sum,value)=>sum+Number(value.minimum_samples||0),0),
      new_messages:runtimes.some(value=>value.new_messages!=null)?runtimes.reduce((sum,value)=>sum+Number(value.new_messages||0),0):null,
      required_new_messages:runtimes.reduce((sum,value)=>sum+Number(value.required_new_messages||0),0),
      group_batches_today:runtimes.reduce((maximum,value)=>Math.max(maximum,Number(value.group_batches_today||0)),0),
      group_daily_batch_limit:runtimes.reduce((maximum,value)=>Math.max(maximum,Number(value.group_daily_batch_limit||0)),0)};
    loaded=rows.some(row=>row.loaded);
    /* A source that answered with ``unavailable_reason`` is a readable page
       with no session behind it, which the header must not report as a
       disabled expression application. */
    available=rows.some(row=>row.loaded&&!row.unavailable);
    reviewEnabled=rows.some(row=>row.reviewEnabled);
  }
  async function api(method, route, body) {
    let timeout;
    try {
      return await Promise.race([(async () => {
        const product = window.CompanionProduct;
        const bridge = product?.bridgeNow?.() || await product?.bridgeFor?.();
        if (!bridge?.apiGet || method !== "GET" && !bridge.apiPost) throw {code:"bridge_unavailable"};
        let response = await (method === "GET" ? bridge.apiGet(route, body) : bridge.apiPost(route, body));
        for (let depth = 0; depth < 3; depth++) {
          if (response?.status === "error") throw {code:response.data?.code || response.code || response.message};
          if (response?.data !== undefined && (!response.status || response.status === "ok")) response = response.data;
          else break;
        }
        response = product?.unwrap ? product.unwrap(response) : response;
        if (!response || typeof response !== "object") throw {code:"invalid_response"};
        return response;
      })(), new Promise((_, reject) => {timeout = setTimeout(() => reject({code:"bridge_timeout"}), 15000);})]);
    } finally { clearTimeout(timeout); }
  }
  function statusText(value = runtime.last_result) {
    if (loaded && !available) return "还没有可读取的素材";
    if (runtime.running || ["queued", "running", "created"].includes(value?.state)) return "学习中";
    const state = value?.state;
    if (state === "waiting_for_new_messages") return `新增消息 ${value.new_messages || 0} / ${value.required || runtime.required_new_messages}`;
    if (state === "waiting_for_sample_review" || state === "insufficient_samples") return `可用素材 ${value.available || 0} / ${value.required || runtime.minimum_samples}`;
    return {succeeded:"学习完成 · 候选已进入审阅中心", already_attempted:"本批素材已学习", learning_disabled:"该来源的学习已关闭",
      capacity_reached:"规则容量已满", daily_budget_reached:"今日学习批次已用完", failed:"学习失败", blocked:"学习暂不可用",
      cancelled:"学习已取消", timed_out:"学习超时"}[state] || (runtime.learning_enabled === false ? "学习已关闭" : "尚未学习");
  }
  function canLearn() {
    return loaded && !busy && !writing && !runtime.running&&Object.values(rowsBySource).some(canLearnSource);
  }
  function render() {
    if (!root) return;
    const disabled = busy || writing ? " disabled" : "";
    const application = !loaded ? "状态待读取" : available
      ? (runtime.application_enabled ? "表达应用开启" : "表达应用关闭") : "暂无可读来源";
    /* Same read-detail list as the expression rules page, so an operator can
       see which source failed and with which code. */
    const issueList = readIssues.length ? `<details class="lh-signal-evidence" aria-label="读取明细"><summary>查看读取明细 · ${readIssues.length} 个来源</summary><ol>${readIssues.map(issue => `<li data-code="${e(issue.code)}"><b>${e(issue.label)}</b>：${e(issue.detail)}</li>`).join("")}</ol></details>` : "";
    root.innerHTML = `<div class="lh-samples">
      <header class="lh-samples-head"><div><h3>观察素材</h3><span>${loaded ? reviewEnabled ? "先审核素材" : "自动取样" : "读取中"} · ${application}</span></div>
        <div class="lh-samples-tools"><button class="lh-command" data-sample-refresh${disabled}>重新读取</button>
          <button class="lh-command is-primary" data-sample-learn${canLearn() ? "" : " disabled"}>${runtime.running ? "学习中…" : "立即学习"}</button></div></header>
      <div class="lh-samples-progress" role="status"><span>${e(statusText())}</span><span>可用 ${Number(runtime.eligible_samples || 0)} / ${Number(runtime.minimum_samples || 0)}</span>
        ${runtime.new_messages == null ? "" : `<span>新增 ${Number(runtime.new_messages)} / ${Number(runtime.required_new_messages)}</span><span>今日 ${Number(runtime.group_batches_today)} / ${Number(runtime.group_daily_batch_limit)} 批</span>`}</div>
      <nav class="lh-review-filters" aria-label="素材审核状态">${FILTERS.map(([key, label]) => `<button data-sample-filter="${key}" aria-pressed="${filter === key}"${disabled}>${label}<span>${key === "all" ? Object.values(counts).reduce((a,b) => a + Number(b), 0) : Number(counts[key] || 0)}</span></button>`).join("")}</nav>
      ${notice ? `<div class="lh-inline-status${error ? " is-error" : ""}" role="status"><span>${e(notice)}</span>${issueList}</div>` : ""}
      <div class="lh-samples-list" aria-busy="${busy || writing}">${items.length ? items.map(item => `<article class="lh-sample" data-state="${e(item.state)}">
        <details><summary><span class="lh-sample-sender">${e(item.sender_id || "消息来源")}</span><time>${e(time(item.received_at))}</time><span class="lh-sample-state">${STATES[item.state] || "不可用"}${item.eligible ? " · 可学习" : ""}</span><span class="lh-sample-preview">${e(item.text)}</span></summary><p>${e(item.text)}</p><small class="lh-sample-scope">记录来源 · ${e(sourceLabel(item._source))}</small></details>
        <footer>${(item.actions || []).filter(action => ["approve", "reject"].includes(action)).map(action => `<button class="lh-command${action === "approve" ? " is-primary" : ""}" data-sample-action="${action}" data-row-key="${e(item._key)}"${disabled}>${action === "approve" ? item.state === "rejected" ? "重新允许" : "允许学习" : "排除素材"}</button>`).join("")}</footer></article>`).join("")
        : `<div class="lh-review-empty">${busy ? "正在读取素材…" : loaded ? "当前筛选下暂无素材" : "尚未读取素材"}</div>`}</div>
      ${Object.values(rowsBySource).some(row=>row.cursor) ? `<button class="lh-command" data-sample-more${disabled}>加载更多</button>` : ""}</div>`;
  }
  function updated(current, source) {
    if (mounted(current)) window.dispatchEvent?.(new CustomEvent("companion-expression-updated", {detail:{scopeKey:source?.scopeKey||scopeKey}}));
  }
  function poll(current) {
    clearTimeout(timer);
    if (mounted(current) && runtime.running) timer = setTimeout(() => { if (mounted(current)) void read(current); }, 4000);
  }
  async function read(current = ticket, append = false, keepNotice = false) {
    if (!mounted(current) || !sources.length || writing || busy) return;
    clearTimeout(timer);
    const request = ++readTicket;
    const query = {state:filter, limit:24};
    const pending=sources.filter(source=>!append||rowsBySource[source.key]?.cursor)
      .map(source=>({source,after_id:append?rowsBySource[source.key]?.cursor:null}));
    busy = true; render();
    const results=await Promise.allSettled(pending.map(async ({source,after_id})=>{
      const data=await api("GET",source.apiBase+"/style/samples",{...query,...(after_id?{after_id}:{})});
      /* A source without a readable session answers with an empty page plus
         ``unavailable_reason``; only a page that is neither readable nor
         explained counts as an invalid response. */
      const unavailable=typeof data.unavailable_reason==="string"?errorCode(data.unavailable_reason):"";
      if(!Array.isArray(data.items)||(!data.runtime&&!unavailable))throw {code:"invalid_response"};
      return {source,data,unavailable};
    }));
    if (!mounted(current) || request !== readTicket) return;
    /* One entry per source that could not deliver samples, kept so the page
       can tell "this source failed" from "this source has nothing yet". */
    const discovered=[];
    let finishedRunning=false;
    results.forEach((result,index)=>{
      const source=pending[index].source;
      if(result.status!=="fulfilled"){
        const code=issueCode(result.reason);
        discovered.push({kind:"error",label:sourceLabel(source),code,detail:readReason(code)});
        return;
      }
      const {data,unavailable}=result.value;
      if(unavailable){
        discovered.push({kind:"unavailable",label:sourceLabel(source),code:unavailable,
          detail:readReason(unavailable)});
        /* "Load more" only ever adds pages, so a source that answers the next
           page with an empty page keeps what the reader already sees instead of
           collapsing back to a blank source. */
        if(append&&rowsBySource[source.key]?.loaded)return;
        rowsBySource[source.key]={source,items:[],counts:{},runtime:{...(data.runtime||{})},
          cursor:null,reviewEnabled:data.sample_review_enabled===true,loaded:true,unavailable};
        return;
      }
      const previous=rowsBySource[source.key]||{items:[],runtime:{}};
      finishedRunning=finishedRunning||previous.runtime.running&&!data.runtime.running;
      const incoming=data.items.map(item=>({...item,_source:source,_key:source.key+"::"+item.handle}));
      const merged=append?[...previous.items,...incoming.filter(item=>!previous.items.some(old=>old._key===item._key))]:incoming;
      rowsBySource[source.key]={source,items:merged,counts:data.counts||{},runtime:{...data.runtime,
        minimum_samples:Number(data.runtime.minimum_samples||0),last_result:data.runtime.last_result},
        cursor:data.next_cursor||null,reviewEnabled:data.sample_review_enabled===true,loaded:true,
        unavailable:""};
    });
    project();
    readIssues=append?readIssues.concat(discovered.filter(issue=>
      !readIssues.some(known=>known.kind===issue.kind&&known.label===issue.label))):discovered;
    const failedCount=readIssues.filter(issue=>issue.kind==="error").length;
    const unavailableCount=readIssues.filter(issue=>issue.kind==="unavailable").length;
    /* One sentence for both kinds of issue, so a source with nothing to read
       is never hidden behind a source that failed. */
    if(failedCount){
      const empty=unavailableCount?`、${unavailableCount} 个来源暂无素材`:"";
      notice=failedCount>=sources.length&&!unavailableCount?"全部来源读取失败，请检查来源配置后重新读取。"
        :`${failedCount} 个来源读取失败${empty}，已显示其余来源。`;
      error=true;
    }
    /* An empty source is a state, not a failure: keep the page neutral so it
       does not read like a broken source. */
    else if(unavailableCount){notice=`${unavailableCount} 个来源还没有可读取的素材。`;error=false;}
    else if(error&&!keepNotice){notice="";error=false;}
    if(finishedRunning)sources.forEach(source=>updated(current,source));
    if (!mounted(current) || request !== readTicket) return;
    busy = false; render(); poll(current);
  }
  async function command(key, action) {
    const item = items.find(item => item._key === key);
    if (!item?.view_token || !item.actions?.includes(action) || busy || writing) return;
    const current = ticket;
    writing = true; notice = ""; error = false; clearTimeout(timer); render();
    try {
      await api("POST", item._source.apiBase + "/style/samples/command", {handle:item.handle, action, view_token:item.view_token,
        operation_key:`sample-${action}-${Date.now()}-${Math.random().toString(36).slice(2,10)}`});
      if (!mounted(current)) return;
      notice = action === "approve" ? "素材已允许学习。" : "素材已排除，相关规则已停止应用。";
      updated(current,item._source);
    } catch (problem) {
      if (!mounted(current)) return;
      notice = message(problem?.code); error = true;
    }
    if (!mounted(current)) return;
    writing = false; await read(current, false, true);
  }
  async function learn() {
    if (!canLearn()) return;
    const current = ticket;
    writing = true; notice = ""; error = false; render();
    let started=0,failed=0;
    for(const row of Object.values(rowsBySource)){
      if(!canLearnSource(row))continue;
      try{await api("POST",row.source.apiBase+"/style/learn",{});started++;updated(current,row.source);}
      catch(problem){failed++;if(!started)notice=message(problem?.code);}
      if(!mounted(current))return;
    }
    if(!failed||started)notice=`已为 ${started} 个来源启动学习${failed?`，${failed} 个暂未启动`:""}。`;
    error=failed>0&&started===0;
    if (!mounted(current)) return;
    writing = false; await read(current, false, true);
  }
  function click(event) {
    if (busy || writing) return;
    const action = event.target.closest("[data-sample-action]");
    if (action) {void command(action.dataset.rowKey, action.dataset.sampleAction); return;}
    const choice = event.target.closest("[data-sample-filter]");
    if (choice && FILTERS.some(([key]) => key === choice.dataset.sampleFilter)) {
      filter = choice.dataset.sampleFilter; items = []; Object.values(rowsBySource).forEach(row=>{row.items=[];row.cursor=null;row.loaded=false;}); void read(); return;
    }
    if (event.target.closest("[data-sample-refresh]")) {notice = ""; void read();}
    if (event.target.closest("[data-sample-more]")) void read(ticket, true);
    if (event.target.closest("[data-sample-learn]")) void learn();
  }
  function saved(event) {if (event.detail?.group === "expression.learning") void read();}
  function mount(host, options = {}) {
    unmount(); root = host;
    const candidates=Array.isArray(options.sources)?options.sources:options.replyApi?[{replyApi:options.replyApi,scopeKey:options.scopeKey,label:"当前来源"}]:[];
    sources=candidates.map((source,index)=>{const route=typeof source.replyApi==="string"&&source.replyApi.startsWith(PREFIX)&&source.replyApi.endsWith("/reply")?source.replyApi.slice(PREFIX.length):"";
      return route?{...source,key:String(source.key||source.scopeKey||route||index),scopeKey:source.scopeKey||source.key||"",apiBase:route}:null;}).filter(Boolean);
    apiBase=sources[0]?.apiBase||"";scopeKey=sources[0]?.scopeKey||"";
    rowsBySource=Object.fromEntries(sources.map(source=>[source.key,{source,items:[],counts:{},runtime:{},cursor:null,reviewEnabled:false,loaded:false,unavailable:""}]));
    items = []; counts = {}; runtime = {}; filter = "all"; loaded = false; available = false;
    notice = ""; error = false; readIssues = [];
    const current = ++ticket;
    root?.addEventListener("click", click); window.addEventListener?.("companion-workbench-saved", saved);
    if (sources.length) void read(current); else {notice = "没有可读取的表达素材。"; error = true; render();}
  }
  function unmount() {
    ticket++; readTicket++; clearTimeout(timer); root?.removeEventListener("click", click);
    window.removeEventListener?.("companion-workbench-saved", saved);
    root = null; apiBase = ""; scopeKey = ""; sources=[];rowsBySource={};busy = false; writing = false;
    loaded = false; available = false; notice = ""; error = false; readIssues = [];
  }
  return {mount, unmount, get state() {return {items, counts, runtime, busy, writing, scopeKey, sources,
    loaded, available, notice, error, readIssues};}};
})();
