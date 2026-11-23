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
  const formatClock = (date = new Date()) => `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}:${String(date.getSeconds()).padStart(2, "0")}`;
  let root = null, ticket = 0, readTicket = 0, sources = [], getSourcesFn = null, semantic = null, data = null;
  function syncSources() {
    if (typeof getSourcesFn !== "function") return;
    try {
      const latest = getSourcesFn();
      if (Array.isArray(latest)) sources = latest.slice();
    } catch (_) {}
  }
  const when = value => typeof value === "string" && Number.isFinite(Date.parse(value))
    ? new Date(value).toLocaleString("zh-CN",{month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit",hour12:false}) : "时间未知";
  const clockOf = value => typeof value === "string" && Number.isFinite(Date.parse(value))
    ? new Date(value).toLocaleTimeString("zh-CN",{hour:"2-digit",minute:"2-digit",hour12:false}) : "";
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

  async function read(current, options = {}) {
    const manual = options.manual === true;
    if (!root || current !== ticket || data?.writing || (manual && data?.loading)) return;
    if (manual || data?.settled) syncSources();
    const request = ++readTicket;
    // Keep the last settled view while re-reading. Replacing it with zeros made
    // every refresh flash an empty KPI row and collapse the growth track, which
    // the browser then answered by clamping the scroll position to the top.
    const previous = data;
    const notice = manual ? "" : (previous?.notice || ""), error = manual ? false : !!previous?.error;
    data = previous
      ? {...previous, loading:true, refreshing:Boolean(previous.settled || manual), writing:false, confirming:null, notice, error}
      : {loading:true, refreshing:false, refreshedAt:"", settled:false, writing:false, confirming:null, notice, error, errors:0,
         failedSources:0, growth:[], counts:{pending:0,active:0,adopted:0,rules:null,scan:null}};
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
      const expression=value(5);
      const settings=expression?.settings, learning=expression?.learning||null;
      // A group source may have no growth route at all, so count only the reads
      // that were actually attempted. A source with no readable route cannot be
      // read at all: treat it as a failed owner, never as an empty one.
      const attempted=(growth?3:0)+(reply?3:0);
      const rejected=requests.filter(result => result.status === "rejected").length;
      return {
        errors: rejected + (attempted ? 0 : 1),
        failed: attempted === 0 || rejected >= attempted,
        growth: growthItems.map(item=>({...item,_scope:source.label,_source:source})),
        pending: (value(1)?.items||[]).length
          + (value(2)?.items||[]).filter(item=>item.kind==="ask_user").length
          + (value(3)?.items||[]).length,
        active: growthItems.filter(item=>item.state==="active").length,
        adopted: (value(4)?.items||[]).length,
        // The per-turn injection ceiling is role-wide policy, so it travels with
        // the learning status; only the local window switches live in `settings`.
        rules: Number.isInteger(learning?.rules_per_turn) ? learning.rules_per_turn
          : Number.isInteger(settings?.rules_per_turn) ? settings.rules_per_turn : null,
        learning,
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
    data.failedSources = sourceResults.reduce((total, result) => total +
      (result.status === "fulfilled" ? (result.value.failed ? 1 : 0) : 1), 0);
    // A failed refresh must not turn the last known numbers into zeros: with no
    // readable owner there is nothing left to recompute from, so keep the
    // previous view and let the error line explain it. A first load has none.
    if (!(sources.length > 0 && data.failedSources >= sources.length)) {
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
      data.updatedAt = new Date().toISOString();
    }
    data.settled = true;
    data.loading = false;
    data.refreshing = false;
    if (manual && data.errors === 0 && !(sources.length > 0 && data.failedSources >= sources.length)) {
      data.refreshedAt = formatClock();
    }
    render();
  }

  function duration(seconds) {
    if (!Number.isFinite(seconds)) return "—";
    if (seconds % 3600 === 0) return `${seconds / 3600} 小时`;
    if (seconds % 60 === 0) return `${seconds / 60} 分钟`;
    return `${seconds} 秒`;
  }

  const INTERNAL_SENTINEL_CODES = new Set(["owner_result", "ok", "succeeded", "completed", "done"]);
  const isInternalCode = value => typeof value === "string" && /^[a-z][a-z0-9_]{2,}$/.test(value.trim());

  const LEARNING_STATES = {
    not_run: "还没运行过",
    waiting_for_new_messages: "等待新消息",
    waiting_for_sample_review: "等待样本审核",
    insufficient_samples: "样本不足",
    source_not_selected: "当前私聊未选中为学习来源",
    learning_disabled: "该窗口未开启表达学习",
    capacity_reached: "待审与启用规则已达上限",
    daily_budget_reached: "今日群聊学习批次已用完",
    already_attempted: "本批素材已学习，等待新消息",
    succeeded: "上次学习已产出",
    retry_backoff: "上次失败，等待下次重试",
    retry_exhausted: "本批素材重试已达上限，等待新素材",
    failed: "上次学习失败",
    blocked: "来源校验未通过",
  };
  const HEALTHY_LEARNING_STATES = new Set(["succeeded", "already_attempted", "waiting_for_new_messages"]);
  const NEUTRAL_LEARNING_STATES = new Set(["not_run", "waiting_for_sample_review", "insufficient_samples"]);
  // 重试相关状态的中文文案已经把处置说清楚（等待或停止），不再回显
  // expression_retry_exhausted 这类机器码，否则同一行会同时出现中文与机器码。
  const RETRY_STATES = new Set(["retry_exhausted", "retry_backoff"]);
  const FAILURE_STATES = new Set(["failed", "blocked"]);
  // 失败码的中文原因：向用户直接说明原因与处置动作，原始 code 仅作为诊断属性留档对照，
  // 绝不向用户裸露下划线英文标识符。
  const LEARNING_FAILURE_CODES = {
    model_estimation_failed: "本地预估没能完成，请结合运行日志排查",
    host_model_limit_input: "要发送的材料超过本地输入上限；请调大「最大输入估算」或减少参考规则",
    host_model_limit_output: "输出上限不足以写完这次结果；请调高该任务的输出上限",
    model_output_truncated: "模型没写完就被输出上限截断：思考型模型会把上限大量花在思考上，可见输出只占一小部分。可把「模型 → 学习 → 表达学习」的「单次输出上限」调到约 4 倍以上（例如 4096 → 16384，仍失败就继续加倍），或在 Provider 处调低思考等级/关闭思考、改用非思考模型，也可减少参考规则与样本；改完到「表达学习 → 观察素材」点「立即学习」重试。",
    model_task_unregistered: "后台学习任务配置未就绪，请检查模型路由",
    bridge_timeout: "请求超时，稍后将自动重试",
    bridge_unavailable: "服务连接暂不可用",
  };
  function diagnosticCode(state, code) {
    if (!FAILURE_STATES.has(state) || RETRY_STATES.has(state)) return "";
    const trimmed = typeof code === "string" ? code.trim() : "";
    if (!trimmed || INTERNAL_SENTINEL_CODES.has(trimmed)) return "";
    return trimmed;
  }
  function learningStatusItem(item) {
    if (!item || typeof item !== "object" || !item.state) return null;
    const rawState = String(item.state);
    const label = LEARNING_STATES[rawState] || (isInternalCode(rawState) ? "状态更新中" : rawState);
    const counts = rawState === "capacity_reached" && Number.isInteger(item.active)
      ? `（${item.active}/${item.limit ?? "?"} 条规则）`
      : Number.isInteger(item.available)
        ? `（${item.available}/${Number.isInteger(item.required) ? item.required : "?"} 条样本）` : "";
    const code = diagnosticCode(rawState, item.code);
    const mappedReason = code && LEARNING_FAILURE_CODES[code]
      ? LEARNING_FAILURE_CODES[code]
      : (code && !isInternalCode(code) ? code : "");
    const reason = mappedReason ? `（${e(mappedReason)}）` : "";
    const last = item.last_run_at ? ` · 上次 ${when(item.last_run_at)}` : "";
    const tone = rawState === "failed" ? "is-error"
      : HEALTHY_LEARNING_STATES.has(rawState) ? "is-ok"
        : NEUTRAL_LEARNING_STATES.has(rawState) ? "is-neutral" : "is-warn";
    return {
      text: `表达学习：${e(label)}${e(counts)}${reason}${e(last)}`,
      tone,
      code,
    };
  }
  function learningLine(item) {
    return learningStatusItem(item)?.text || "";
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
  const HEALTHY_REVIEW_STATES = new Set(["succeeded", "completed", "already_attempted"]);
  const NEUTRAL_REVIEW_STATES = new Set(["not_run", "no_reviewable_window"]);
  const REVIEW_FAILURE_CODES = {
    self_review_output_invalid: "复盘输出格式不完整",
    self_review_material_changed: "复盘依据在运行期间发生了变化",
    self_review_binding_changed: "角色绑定已变化，等待重新同步",
    self_review_job_unavailable: "原始复盘任务材料暂不可用",
    self_review_closed: "复盘服务已停止",
    model_task_unregistered: "复盘模型路由尚未配置",
    model_estimation_failed: "本地预估没能完成，请结合运行日志排查",
    host_model_limit_input: "复盘材料超过模型输入上限",
    host_model_limit_output: "复盘模型输出上限不足",
    model_output_truncated: "复盘结果被模型输出上限截断",
    source_scope_mismatch: "复盘来源与当前会话范围不一致",
    relationship_required: "当前角色尚未开启复盘权限",
    bridge_timeout: "请求超时，稍后将自动重试",
    bridge_unavailable: "服务连接暂不可用",
  };
  function reviewStatusItem(item) {
    if (!item || typeof item !== "object" || !item.state) return null;
    const rawState = String(item.state);
    const label = REVIEW_STATES[rawState] || (isInternalCode(rawState) ? "状态更新中" : rawState);
    const code = diagnosticCode(rawState, item.code);
    const mappedReason = code && REVIEW_FAILURE_CODES[code]
      ? REVIEW_FAILURE_CODES[code]
      : (code && !isInternalCode(code) ? code : "");
    const reason = mappedReason ? `（${e(mappedReason)}）` : "";
    const last = item.last_run_at ? ` · 上次 ${when(item.last_run_at)}` : "";
    const next = rawState === "failed" && item.next_check_at
      ? ` · 下次 ${when(item.next_check_at)}` : "";
    const tone = rawState === "failed" ? "is-error"
      : HEALTHY_REVIEW_STATES.has(rawState) ? "is-ok"
        : NEUTRAL_REVIEW_STATES.has(rawState) ? "is-neutral" : "is-warn";
    return {
      text: `每日复盘：${e(label)}${reason}${e(last)}${e(next)}`,
      tone,
      code,
    };
  }
  function reviewLine(item) {
    return reviewStatusItem(item)?.text || "";
  }

  const GROWTH_REASONS = {
    authorization_missing: "未授权",
    disabled: "未开启",
    proposals_disabled: "未开启成长提案",
    read_disabled: "未开启读取权限",
    relationship_required: "尚未建立对应关系",
    scope_not_private: "仅私聊支持成长提案",
    scope_mismatch: "会话范围不匹配",
    core_access_required: "需要管理员权限",
    binding_revoked: "账号授权已撤销",
  };
  function growthLine(access) {
    if (!access || typeof access !== "object" || !access.settings) return "";
    const effective = access.effective || {};
    if (effective.proposals_enabled?.allowed) return "";
    const rawReason = effective.proposals_enabled?.reason || effective.read_enabled?.reason || "";
    const reason = rawReason
      ? (GROWTH_REASONS[rawReason] || (isInternalCode(rawReason) ? "权限尚未开启" : rawReason))
      : "";
    return reason
      ? `成长链路：成长提案权限不可用（${e(reason)}）；开启后角色才会提出技能或长期目标。`
      : "成长链路：尚未开启成长提案权限；开启后角色才会提出技能或长期目标。";
  }
  function growthStatusItem(access) {
    const text = growthLine(access);
    return text ? {text, tone: "is-warn", code: ""} : null;
  }
  function renderStatusLine(item) {
    if (!item || !item.text) return "";
    const codeAttr = item.code ? ` data-status-code="${e(item.code)}" title="诊断码：${e(item.code)}"` : "";
    return `<p class="lh-inline-status ${item.tone}" role="status"${codeAttr}><span class="lh-status-dot" aria-hidden="true"></span><span>${item.text}</span></p>`;
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
    // Every record owner failed: this is an outage, not an empty workspace, so
    // the page must not conclude that learning produced nothing yet.
    const allFailed=sources.length>0&&data.failedSources>=sources.length;
    const cards=[
      ["待裁决",counts.pending,"进展 · 表达 · 指导","clipboard-list","review"],
      ["培育中",counts.active,"技能与目标","sprout","chain"],
      ["规则注入",counts.rules===null?"—":`每轮 ${counts.rules} 条`,`${counts.adopted} 条已采纳`,"messages-square","expression"],
      ["语义扫描",duration(counts.scan),"群词义整理间隔","scan-search","semantic"],
    ];
    const hasGrowth=data.growth.length>0;
    const nothingYet=!data.errors&&!counts.pending&&!counts.active&&!counts.adopted;
    const lanes=STAGES.map(([state,label,color])=>{
      const items=data.growth.filter(item=>item.state===state);
      return `<section class="lh-overview-lane" data-state="${state}"><header><span class="lh-lane-dot" style="--lane:${color}"></span><strong>${label}</strong><span>${items.length}</span></header>
        <div class="lh-lane-items">${items.length?items.slice(0,4).map(growthCard).join(""):`<p class="lh-lane-empty">—</p>`}</div></section>`;
    }).join("");
    const summary=allFailed?"全部记录归属读取失败":data.errors?"部分记录归属暂不可用":`汇总 ${sources.length} 个记录归属`;
    const track=hasGrowth
      ? `<section class="lh-overview-track"><header><h2>培育轨道</h2><a href="#manage/learning/chain">全部记录</a></header>
        <div class="lh-track-axis" aria-hidden="true">${STAGES.map(([state,label])=>`<span data-state="${state}"><i></i><b>${label}</b></span>`).join("")}</div>
        <div class="lh-overview-lanes">${lanes}</div></section>`
      : allFailed
        ? `<section class="lh-overview-track is-empty"><header><h2>培育轨道</h2><a href="#manage/learning/chain">全部记录</a></header><div class="lh-track-empty"><strong>成长记录暂不可读</strong><p>来源读取失败，请先重新读取；已有记录不会因为这次失败被清空。</p></div></section>`
        : `<section class="lh-overview-track is-empty"><header><h2>培育轨道</h2><a href="#manage/learning/chain">全部记录</a></header>
        <div class="lh-track-empty"><strong>${nothingYet?"学习还没有产出":"还没有成长记录"}</strong><p>${counts.pending?`有 ${counts.pending} 条待裁决，先到审阅中心处理；成长记录会在通过后进入轨道。`:"每日复盘会在后台自动运行，表达学习可在「表达学习」点「立即学习」，成长记录需要先给对应用户开启成长提案权限。"}</p><div class="lh-track-actions"><a href="#manage/learning/review">去审阅中心</a><a href="#manage/learning/expression">去表达学习</a></div></div></section>`;
    const updated = !data.refreshedAt && data.updatedAt ? clockOf(data.updatedAt) : "";
    const statusItems = [
      learningStatusItem(data.learning),
      reviewStatusItem(data.review),
      growthStatusItem(data.access),
    ].filter(Boolean);
    const statusStack = statusItems.length
      ? `<div class="lh-status-stack">${statusItems.map(renderStatusLine).join("")}</div>` : "";
    root.setAttribute?.("aria-busy",data.loading?"true":"false");
    root.innerHTML=`<div class="lh-overview-toolbar"><span>${summary}${updated?` · 更新于 ${e(updated)}`:""}</span>
      <span class="refresh-cluster">${data.refreshedAt?`<span class="refresh-stamp" role="status">刚刚已刷新 ${e(data.refreshedAt)}</span>`:""}<button type="button" class="lh-refresh${data.refreshing?" is-refreshing":""}" data-lh-refresh aria-label="重新读取" title="重新读取" aria-busy="${data.refreshing?"true":"false"}" ${data.loading||data.writing?"disabled":""}><i data-lucide="refresh-cw"></i></button></span></div>
      ${allFailed?`<p class="lh-inline-status is-error" role="status"><span class="lh-status-dot" aria-hidden="true"></span><span>来源读取失败，请检查来源配置后重新读取。</span><button type="button" class="lh-command" data-lh-refresh ${data.loading||data.writing?"disabled":""}>重新读取</button></p>`
        :data.notice?`<p class="lh-inline-status ${data.error?"is-error":"is-ok"}" role="status"><span class="lh-status-dot" aria-hidden="true"></span><span>${e(data.notice)}</span></p>`:""}
      ${statusStack}
      <section class="lh-kpis" aria-label="学习数据">${cards.map(([label,value,detail,icon,route])=>`<a class="lh-kpi" href="#manage/learning/${route}">
        <span class="lh-kpi-icon" aria-hidden="true"><i data-lucide="${icon}"></i></span><span class="lh-kpi-label">${label}</span>
        <strong>${e(value)}</strong><small>${label==="规则注入"?e(detail):label==="待裁决"?"进展 · 表达 · 指导":label==="培育中"?"技能与目标":"群词义整理间隔"}</small></a>`).join("")}</section>
      ${track}
      ${data.confirming?`<div class="lh-review-confirm" role="alertdialog" aria-label="确认${ACTIONS[data.confirming.action]}"><span>${ACTIONS[data.confirming.action]}这条记录？</span><button type="button" data-lh-confirm="yes">确认${ACTIONS[data.confirming.action]}</button><button type="button" data-lh-confirm="no">取消</button></div>`:""}
      ${data.loading&&!data.settled?`<p class="lh-empty">正在读取…</p>`:""}`;
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
    if (event.target.closest("[data-lh-refresh]")) {
      if (data) { data.notice = ""; data.error = false; }
      void read(ticket, {manual:true});
      return;
    }
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
    unmount(); root=host; getSourcesFn=typeof options.getSources==="function"?options.getSources:null;
    sources=Array.isArray(options.sources)?options.sources.slice():(getSourcesFn?(()=>{try{const list=getSourcesFn();return Array.isArray(list)?list.slice():[];}catch(_){return [];}})():[]);
    semantic=options.semantic||null;
    root?.addEventListener("click",onClick);
    const current=++ticket;
    if(sources.length)void read(current);
    else{data={loading:false,refreshing:false,refreshedAt:"",settled:true,errors:0,failedSources:0,growth:[],counts:{pending:0,active:0,adopted:0,rules:null,scan:null}};render();}
  }
  function unmount() { ticket++;readTicket++; root?.removeEventListener("click",onClick); root=null; sources=[]; getSourcesFn=null; semantic=null; data=null; }
  return {mount,unmount,get state(){return data;}};
})();
