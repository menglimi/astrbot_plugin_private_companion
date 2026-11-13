"use strict";
window.CompanionStatePage = (() => {
  const HOST = "astrbot_plugin_private_companion";
  const PREFIX = "/" + HOST + "/";
  const phaseNames = {menstrual:"经期", follicular:"卵泡期", pre_ovulation:"排卵前", ovulation:"排卵期",
    luteal:"黄体期", pms:"经前期", recovery:"恢复期"};
  const errors = {
    dashboard_identity_required:"请从已登录的 AstrBot 管理界面打开陪伴小屋。",
    staging_not_initialized:"角色数据尚未就绪，请在「账号与会话」保存角色映射；已保存可重新读取状态。",
    staging_host_terminated:"插件已停止，请重新打开拓展页。",
    core_access_required:"当前账号没有角色状态管理权限。",
    state_not_enabled:"请先在状态设置中启用状态维护。",
    state_not_bound:"当前角色尚未绑定状态工作区，请核对账号与人格映射。",
    record_revision_conflict:"状态已变化，请重新读取后再试。",
    binding_revoked:"管理账号的授权已撤销。", binding_rebound:"管理账号的绑定已变化，请重新进入。",
    page_request_invalid:"目标目录请求无效，请重新打开页面。", bridge_unavailable:"页面还没有接上 AstrBot。若这是从管理界面打开的插件页，刷新一次通常就好；若反复出现，请确认插件已启用。",
    bridge_timeout:"未收到状态确认，请刷新核对。", invalid_response:"服务返回的数据不完整，请刷新核对。",
  };
  const presentationReasons = {
    presentation_disabled:"开关已关闭", presence_port_missing:"未安装 QQ 展示端口", host_not_running:"角色尚未运行或已停止",
    authorization_denied:"当前窗口缺少展示授权", binding_revoked:"当前身份授权已撤销", binding_rebound:"当前身份绑定已变化",
    role_binding_changed:"当前角色绑定已变化，请重新打开页面",
    multiple_presence_owners:"同一 QQ 账号有多个展示窗口，请保留一个窗口启用自定义短状态",
    presence_plan_conflict:"当前有多个带公开文案的日程重叠，请核对日程",
    presence_calendar_partial:"当前日程未完整读取，暂不选择公开文案", presence_no_public_wording:"当前没有显式公开文案",
    presence_context_unbound:"当前会话正在自动准备", typing_batch_suppressed:"本轮确认失败或未知，已停止自动刷新",
    typing_turn_closed:"当前会话的回复已结束", presence_changed:"发送前公开文案已变化",
    presence_scope_mismatch:"原账号或收件人与请求不一致", presence_transport_unavailable:"原账号的 QQ 连接不可用",
    presence_authorization_changed:"发送前授权或平台环境未通过复核", presence_wording_invalid:"公开文案需为 1–8 个字",
    presence_action_rejected:"平台拒绝了展示请求", presence_ack_unknown:"平台未返回可确认的结果",
    presence_interrupted:"请求被中断，结果需要人工核对", typing_failed:"输入提示未获得可确认的结果",
  };
  const e = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const when = value => value && Number.isFinite(Date.parse(value))
    ? new Date(value).toLocaleString("zh-CN", {hour12:false}) : "未安排";
  const reason = code => errors[code] || (code ? "当前状态未能确认，请刷新核对。" : "暂时无法读取角色状态。请刷新核对。");
  let root = null, bridge = null, version = 0, timer = null, data = null, editor = null, calendarEditor = null;
  let section = "state", activeFeature = null;
  let settingsOpen=false,settingsReturnFeature=null,settingsReturnFocus=null;

  /* 状态页切换到其它工作区时会被卸载。最近一次成功状态只在当前页面
     内存保留并按角色/会话隔离，返回时先显示旧读数，后台再核对最新状态。 */
  const stateByScope = new Map();
  let stateScopeKey = "";
  function currentStateScope() {
    try { return window.CompanionProduct?.scopeKey?.() || "default"; } catch (_) { return "default"; }
  }
  function rememberState() {
    if (!data || !stateScopeKey || (!data.catalog && !data.status)) return;
    stateByScope.set(stateScopeKey, {...data, loading:false, error:false, notice:""});
  }

  function blank() { return {catalog:null,target:0,role:0,status:null,cursor:"",loading:false,error:false,notice:"",ready:false,
    pendingMaintenance:null,maintenanceSubmitting:false}; }
  function target() { return data?.catalog?.targets[data.target]; }
  function role() { return target()?.roles[data.role]; }
  function personaLabel() { return window.CompanionProduct?.roleLabel?.(role()) || "当前角色"; }
  /* 同 memory-page.js：桥要插件相对路径，所以切掉插件目录名。 */
  function endpoint() {
    const path = role()?.state_api;
    if (typeof path !== "string" || !path.startsWith(PREFIX)) throw Object.assign(new Error("invalid_response"),{code:"invalid_response"});
    const relative = path.slice(PREFIX.length);
    if (!relative || relative.split("/").some(part => !part || part === "." || part === "..") || /[?#%\\:]/.test(relative)) {
      throw Object.assign(new Error("invalid_response"),{code:"invalid_response"});
    }
    return relative;
  }
  async function bounded(promise) {
    let timeout;
    try { return await Promise.race([promise,new Promise((_,reject)=>{timeout=setTimeout(()=>reject(Object.assign(new Error("bridge_timeout"),{code:"bridge_timeout"})),15000);})]); }
    finally { clearTimeout(timeout); }
  }
  /* 后端把机器码放在信封的 message 上（页面桥只转发 message），取码要同时看
     data.code 与 message，不能只认前者。 */
  const CODE = /^[a-z][a-z0-9_]{2,60}$/;
  const codeOf = wrapped => wrapped?.data?.code
    || (CODE.test(wrapped?.message || "") ? wrapped.message : "") || "";
  async function api(method,path,body) {
    const response = await bounded(method === "GET" ? bridge.apiGet(path,body) : bridge.apiPost(path,body));
    const wrapped = ["ok","error"].includes(response?.status) && response?.data !== undefined ? response : ["ok","error"].includes(response?.data?.status) && response?.data?.data !== undefined ? response.data : null;
    if (wrapped?.status === "error") {const code=codeOf(wrapped)||"invalid_response";throw Object.assign(new Error(code),{code,confirmed:true});}
    const value = wrapped ? wrapped.data : response;
    if (!value || typeof value !== "object" || Array.isArray(value)) throw Object.assign(new Error("invalid_response"),{code:"invalid_response"});
    return value;
  }
  function live(ticket) { return ticket === version && root?.isConnected; }
  function stopTimer() { clearTimeout(timer); timer = null; }
  function notice(message,error=false) { data.notice=message; data.error=error; }
  function statusLabel(status) {
    return ({active:"运行中",disabled:"状态维护未启用",unknown:"尚无状态记录",unavailable:"当前状态不可用"})[status] || "角色状态";
  }
  function valueLabel(value,enabled=true) { return !enabled ? "未启用" : value == null || value === "" ? "尚未记录" : String(value); }
  function phaseLabel(status) {
    const settings=status.settings||{}, state=status.state||{}, timeline=state.cycle_timeline;
    if (!settings.cycle_enabled) return "未启用";
    if (!timeline) return state.state === "unavailable" ? "暂不可读取" : "尚无阶段记录";
    if (timeline.applicability === "not_applicable") return "不适用";
    if (timeline.applicability !== "applicable") return "尚未定位";
    const phase=phaseNames[timeline.phase]||"周期阶段";
    return Number.isInteger(timeline.phase_day)&&Number.isInteger(timeline.phase_days)
      ? `${phase} · 第 ${timeline.phase_day} / ${timeline.phase_days} 天` : phase;
  }
  function metric(name,value,detail="") {
    return `<article class="state-metric"><span>${name}</span><strong>${e(value)}</strong>${detail?`<small>${e(detail)}</small>`:""}</article>`;
  }
  function stateGauge(name,value,detail="",tone="accent") {
    const known=!(value==null||value===""),numeric=typeof value==="number"&&Number.isFinite(value);
    const label=!known?"尚未记录":numeric?`${value} / 100`:String(value);
    const meter=numeric?`<div class="state-gauge-track" aria-hidden="true"><i style="width:${Math.max(0,Math.min(100,value))}%"></i></div>`
      :known?"":'<div class="state-gauge-track is-unmeasured" aria-hidden="true"></div>';
    return `<article class="state-gauge ${tone}${!known?" is-unknown":known&&!numeric?" is-qualitative":""}"><div class="state-gauge-head"><span>${name}</span><strong>${e(label)}</strong></div>${meter}<small>${e(detail||"暂无可用记录")}</small></article>`;
  }
  const moodAxes = {joy:"喜", anger:"怒", sorrow:"哀", fear:"惧"};
  const moodOrder = ["joy", "anger", "sorrow", "fear"];
  function moodText(mood) {
    return typeof mood === "string" ? mood : mood?.description || "";
  }
  function moodNumbers(mood) {
    return moodOrder.map(key=>`${moodAxes[key]} ${mood[key]}`).join(" · ");
  }
  function moodDetail(status) {
    const mood=status.state?.values?.mood, dynamics=status.state?.mood_dynamics;
    if (!moodEnabled(status) || !mood || typeof mood !== "object") return "";
    const reference=dynamics?.reference;
    const fallback=reference?`无新互动时的回落参考：${moodOrder.map(key=>`${moodAxes[key]} ${reference[key]}`).join(" · ")}`:"";
    const marks=[moodNumbers(mood),`基线 ${mood.baseline}`,mood.intensity||null,dynamics?.neutral?"处于中性带":null,
      dynamics?.cross_window?"跨窗口共享本体":null].filter(Boolean).join(" · ");
    return [marks,fallback].filter(Boolean).join("；");
  }
  function moodEnabled(status) { return !!status?.settings?.mood_enabled; }
  function workspaceOverview(status,feature) {
    const settings=status.settings||{},state=status.state||{},values=state.values||{};
    const timeline=state.cycle_timeline;
    const content={
      energy:{label:"当前能量",value:settings.energy_enabled===false?"未启用":typeof values.energy==="number"&&Number.isFinite(values.energy)?`${values.energy} / 100`:valueLabel(values.energy),detail:"精力会结合休息、活动与近期互动渐进变化。",scope:"当前人格 · 已授权会话"},
      health:{label:"当前健康感受",value:valueLabel(values.health,settings.enabled&&settings.health_enabled!==false),detail:"记录角色自身的主观体感，不作为诊断。",scope:"当前人格"},
      hunger:{label:"当前饥饿感受",value:valueLabel(values.hunger,settings.enabled&&settings.hunger_enabled!==false),detail:"结合角色的生活节奏与活动变化。",scope:"当前人格"},
      cycle:{label:"当前阶段",value:phaseLabel(status),detail:timeline?.cycle_day?`周期第 ${timeline.cycle_day} / ${timeline.cycle_days} 天；时长调整从下一轮起采用。`:"周期定位与阶段表达按角色单独维护。",scope:"当前角色"},
      mood:{label:"当前心情",value:!settings.mood_enabled?"未启用":values.mood&&typeof values.mood==="object"
        ?moodText(values.mood)||moodNumbers(values.mood):moodText(values.mood)||"尚未记录",
        detail:moodDetail(status)||"心情会随互动变化，并逐步回归基线。",scope:"当前人格 · 跨窗口共享"},
      patrol:{label:"巡视安排",value:settings.daily_state_patrol_enabled?`每天 ${settings.daily_state_patrol_time||"未设时刻"}`:"未启用",detail:`使用角色本地时区；记录保留 ${settings.daily_state_patrol_retention_days??"未提供"} 天。`,scope:"当前人格 · 已授权会话"},
    }[feature];
    if(!content)return "";
    const running=feature==="energy"?!settings.enabled?"后台维护已关闭":settings.energy_enabled===false?"能量模拟已关闭":"能量模拟已启用"
      :feature==="health"?!settings.enabled?"后台维护未启用":settings.health_enabled?"健康模拟已启用":"健康模拟已关闭"
        :feature==="hunger"?!settings.enabled?"后台维护未启用":settings.hunger_enabled?"饥饿模拟已启用":"饥饿模拟已关闭"
          :feature==="cycle"?settings.cycle_enabled?"周期模拟已启用":"周期模拟未启用"
            :feature==="mood"?settings.mood_enabled?"心情模拟已启用":"心情模拟未启用"
              :settings.daily_state_patrol_enabled?"每日巡视已启用":"每日巡视未启用";
    const summary=window.CompanionStateMaintenance?.actionSummary(feature==="energy"?"intensity":feature,status,String)||"查看当前设置";
    const rows=feature==="cycle"?[["运行状态",running],["阶段模式",`${settings.cycle_mode==="six_phase"?"六阶段":"基础三阶段"} · 周期共 ${Object.keys(window.CompanionStateMaintenance?.phases||{}).reduce((sum,key)=>sum+Number(settings[`cycle_${key}_days`]||0),0)} 天`],["周期时区",`${settings.cycle_timezone||"尚未配置"} · ${{natural:"自然定位",unknown:"暂不定位",first_day:"从第 1 天开始"}[settings.cycle_initialization]||"定位方式待确认"}`],["状态更新时间",state.updated_at?when(state.updated_at):"尚无记录"]]
      :[["运行状态",running],["生效范围",content.scope],[feature==="mood"?"动力学参数":"配置摘要",summary],["状态更新时间",state.updated_at?when(state.updated_at):"尚无记录"]];
    const gauge=feature==="energy"&&settings.energy_enabled!==false&&typeof values.energy==="number"&&Number.isFinite(values.energy)
      ?`<meter min="0" max="100" value="${Math.max(0,Math.min(100,values.energy))}" aria-label="当前能量 ${e(values.energy)} / 100"></meter>`:"";
    return overviewMarkup(feature,content.label,content.value,content.detail,rows,gauge);
  }
  function overviewMarkup(feature,label,value,detail,rows=[],extra="") {
    return `<section class="state-workspace-overview${rows.length?"":" state-workspace-overview-readonly"}" aria-label="${e(featureInfo[feature]?.[0]||"状态")}当前情况"><div class="state-workspace-current"><span>${e(label)}</span><strong>${e(value)}</strong><small>${e(detail)}</small>${extra}</div>${rows.length?`<dl>${rows.map(([key,text])=>`<div><dt>${e(key)}</dt><dd>${e(text)}</dd></div>`).join("")}</dl>`:""}</section>`;
  }
  function relatedSettings(cards) {
    return `<nav class="state-config-related" aria-label="相关设置"><h4>相关设置</h4>${cards.map(([title,description,icon,attribute])=>`<button type="button" class="state-config-link" ${attribute}><i data-lucide="${icon}" aria-hidden="true"></i><span><strong>${title}</strong><small>${description}</small></span><i data-lucide="chevron-right" aria-hidden="true"></i></button>`).join("")}</nav>`;
  }
  function sleepMarkup(status) {
    const plan=status.sleep_schedule||{}, execution=status.body_sleep;
    const variation=Number(plan.variation_minutes||0);
    const planned=plan.enabled ? `${e(plan.time||"未设定")}${variation?` · 自然浮动 ±${e(variation)} 分钟`:""} · ${e(plan.timezone||"")} · ${e(plan.duration_minutes)} 分钟` : "自动睡眠日程未启用";
    const rhythmLabels={awake:"清醒",sleepy:"逐渐困倦",winding_down:"准备休息",sleeping:"睡眠中",recovering:"醒来恢复"};
    const rhythm=plan.rhythm;
    const rhythmText=rhythm?.phase ? `${rhythmLabels[rhythm.phase]||"节律观察中"}${rhythm.basis==="execution_receipt"?" · 执行回执":" · 作息参考"}${rhythm.sleep_debt_minutes>0?" · 上次睡眠偏短":""}` : "尚无节律参考";
    const executor=!plan.enabled ? "未安排" : plan.executor_state === "available"
      ? `${e(window.CompanionProduct?.executorLabel?.(plan) || "已选定私聊窗口")}${plan.executor_is_current?"（当前窗口）":""}`
      : "暂无有效授权窗口";
    const actual=execution?.state === "sleeping" ? `睡眠中 · 计划醒来 ${when(execution.planned_wake_at)}`
      : execution?.state === "awake" ? `最近一次睡眠已结束 · ${when(execution.ended_at)}`
        : execution?.state === "interrupted" ? "最近一次睡眠已中断" : "当前没有进行中的睡眠";
    return `<section id="state-sleep-schedule" class="state-sleep-summary"><header><h4>睡眠作息</h4></header>
      <dl><div><dt>日程</dt><dd>${planned}</dd></div><div><dt>执行窗口</dt><dd>${executor}</dd></div>
      <div><dt>下次入睡 / 计划醒来</dt><dd>${plan.next_sleep_at?`${when(plan.next_sleep_at)} / ${when(plan.next_wake_at)}`:"未安排"}</dd></div>
      <div><dt>节律阶段</dt><dd>${e(rhythmText)}</dd></div><div><dt>当前执行</dt><dd>${actual}</dd></div></dl></section>`;
  }
  function presentationMarkup(status,{receiptsOnly=false}={}) {
    const receipts=status.presentation||{};
    const diagnostics=status.presentation_diagnostics, settings=status.settings||{}, effective=status.effective||{};
    const explain=code=>presentationReasons[code]||reason(code);
    const configured=option=>!settings[option]?"开关已关闭":effective[option]?.allowed
      ?"已开启 · 授权通过":effective[option]?.reason?`已开启 · ${explain(effective[option].reason)}`:"已开启 · 授权待核对";
    /* 三条通道的 idle 含义不同：正在输入是「本轮没有回复」，在线状态是
       「这轮没轮到它」（短文案或日程文案优先），短状态是「没有待展示文案」。
       分开写，别让在线状态那一行显示「暂无待展示文案」。 */
    const activity=(value,channel="custom")=>{
      if(!value)return "尚无运行诊断";
      const idle={typing:"当前会话没有进行中的回复",online:"当前不展示在线状态（日程文案或短状态优先）",custom:"暂无待展示文案"};
      const labels={
        typing:{waiting:"等待刷新任务",refreshing:"刷新任务运行中",suppressed:"本轮已停止刷新"},
        online:{waiting:"等待上报在线状态",recorded:"相同状态已有请求记录，不自动重发",deduplicated:"账号已有相同状态记录，不重复请求"},
        custom:{waiting:"等待处理当前文案",recorded:"相同文案已有请求记录，不自动重发",deduplicated:"账号已有相同文案记录，不重复请求"},
      }[channel]||{};
      return [value.state==="idle"?idle[channel]:labels[value.state]
        ||({disabled:"未启用",unavailable:"展示端口不可用",blocked:"当前条件未满足",paused:"展示已暂停"})[value.state]||"状态待核对",
        value.reason?explain(value.reason):null].filter(Boolean).join(" · ");
    };
    const typing=diagnostics?.typing, custom=diagnostics?.custom_status, online=diagnostics?.online_status;
    const basis=value=>({calendar_plan:"当前日程的公开文案",role_simulation:"角色模拟的公开文案"})[value]||"公开文案";
    /* 在线模式的四个档位与适配层的映射一致（delivery 侧 status 10/30/50/40）。 */
    const onlineMode=value=>({online:"在线",away:"离开",busy:"忙碌",invisible:"隐身"})[value]||value;
    const configuration=`<dl class="state-presentation-settings"><div><dt>正在输入 · ${e(configured("typing_enabled"))}</dt>
      <dd>${e(activity(typing,"typing"))}</dd><dd class="state-presentation-note">配置刷新间隔：${e(typing?.refresh_seconds??settings.typing_refresh_seconds??"未提供")} 秒</dd></div>
      <div><dt>在线状态 · ${e(configured("online_status_enabled"))}</dt><dd>${e(activity(online,"online"))}</dd>
      ${online?.desired?.mode?`<dd class="state-presentation-note">待展示：${e(onlineMode(online.desired.mode))} · ${e(basis(online.desired.basis))}</dd>`:""}
      <dd class="state-presentation-note">与自定义短状态共用同一路：同一时刻只报一种，在线状态优先；日程里写了公开文案时以日程为准。</dd></div>
      <div><dt>自定义短状态 · ${e(configured("presence_enabled"))}</dt><dd>${e(activity(custom))}</dd>
      ${custom?.desired?`<dd class="state-presentation-note">待展示：${e(custom.desired.wording)} · ${e(basis(custom.desired.basis))}</dd>`:""}</div></dl>`;
    const items=Object.entries(receipts).filter(([,result])=>result&&typeof result==="object");
    /* 回复确认按账号存一份，三种通道共用它；标名字要靠请求内容本身，
       不能只看 key——否则在线状态的确认会被写成「自定义短状态」，
       而请求文案一栏对模式类的请求是空的（没有 wording 字段）。 */
    const channelName=(key,result)=>key.startsWith("typing:")?"正在输入"
      :result?.desired?.mode?"在线状态":"自定义短状态";
    const desiredText=result=>result.desired?.wording
      ?`${e(result.desired.wording)} · ${e(basis(result.desired.basis))}`
      :result.desired?.mode?`${e(onlineMode(result.desired.mode))} · ${e(basis(result.desired.basis))}`:"";
    const label=(key,result)=>result.state==="unknown"?(key.startsWith("typing:")?"确认未知，本轮不再重试":"确认未知，相同文案不自动重发")
      :({accepted:"平台已受理",failed:"平台拒绝",not_started:"未发送（授权或环境未满足）",blocked:"展示被阻止，未发送"})[result.state]||"状态未识别";
    return `<section class="state-presentation${receiptsOnly?" state-config-group":""}"><header>${receiptsOnly?'<span class="state-config-index">02</span><div>':""}<h4>${receiptsOnly?"平台展示确认":"QQ 平台展示确认"}</h4>
      <p>受理确认不代表客户端当前展示状态。${diagnostics?.observed_at?`诊断时间：${when(diagnostics.observed_at)}`:""}</p>${receiptsOnly?"</div>":""}</header>
      ${receiptsOnly?"":configuration}${receipts.state==="preview"?"<p>当前为本地预览，未连接 QQ 展示能力。</p>":items.length?`<ul class="state-receipts">${items.map(([key,result],index)=>`<li>
        <strong>${e(channelName(key,result))}</strong><div><span class="state-receipt-result ${e(result.state)}">${e(label(key,result))}</span>
        <small class="state-receipt-detail">${result.recorded_at?`记录于 ${when(result.recorded_at)}`:"旧确认未记录时间"}${key.startsWith("typing:")?" · 仅保留本次插件运行记录":""}</small>
        ${result.code?`<small class="state-receipt-detail">${e(explain(result.code))}</small>`:""}
        ${desiredText(result)?`<small class="state-receipt-detail">请求内容：${desiredText(result)}</small>`:""}
        ${result.operation_state==="started"?"<small class=\"state-receipt-detail\">仅记录了请求开始，尚无完成确认；时间为开始记录时间。</small>":""}
        ${result.operation_id||result.batch_id?`<details class="state-receipt-reference" data-receipt="${e(result.operation_id||result.batch_id)}"><summary id="state-receipt-summary-${index}">查看执行详情</summary><p>执行结果：${e(label(key,result))}</p>${result.batch_id?"<p>关联当前会话的回复。</p>":""}</details>`:""}</div></li>`).join("")}</ul>`:"<p>尚无 QQ 展示确认。</p>"}</section>`;
  }
  const featureInfo={energy:["能量","battery-medium"],health:["健康","heart-pulse"],hunger:["饥饿","utensils"],
    sleep:["睡眠","moon-star"],cycle:["生理期","calendar-heart"],busy:["忙碌","briefcase-business"],
    location:["位置","map-pin"],mood:["心情","smile"],planning:["日程与规划","calendar-days"],weather:["天气感知","cloud-sun"],
    datetime:["时间与日期","calendar-clock"],patrol:["每日巡视","scan-heart"],presentation:["QQ 状态","radio-tower"]};
  const featureEditors={energy:"intensity",health:"health",hunger:"hunger",cycle:"cycle",mood:"mood",
    planning:"planning",datetime:"datetime",patrol:"patrol",presentation:"settings"};
  /* 头部副标题：与「当前情况」的 detail 同源，落到设计稿 5 段骨架第 1 段。 */
  const featureSubtitles={
    energy:"日常精力随休息、活动与互动自然演化，可单独设置表达强度。",
    health:"记录角色自身的主观体感，仅作陪伴表达，不构成任何医学诊断。",
    hunger:"结合角色的生活节奏与活动变化，渐进调整进食表达。",
    sleep:"查看睡眠作息与当前睡眠状态，休息时如何回复可单独设置。",
    cycle:"周期模拟与阶段表达；精力偏移和氛围词只是趋势参考，不替角色下结论。",
    busy:"忙碌状态由日程形成，可设置忙碌时的回复方式。",
    location:"对话中记录的人格位置模拟，随人格在窗口间共享。",
    mood:"心情随互动变化并逐步回归基线，不记录聊天对象的情绪。",
    planning:"维护日程与公开文案计划，连续陪伴模式可在这里设置。",
    weather:"让长期安排参考所在地实况；天气凭据由宿主管理。",
    datetime:"查看角色当地时间与日期，设置小屋时区与对话历法背景。",
    patrol:"每天在设定时刻巡视角色状态，保留一段时间的记录。",
    presentation:"正在输入按当前私聊窗口，QQ 在线状态与自定义短状态按角色共享，需授权通过。",
  };
  /* 右端「生效范围」胶囊：与 workspaceOverview 的 scope 保持一致。 */
  const featureScopes={
    energy:"当前人格",health:"当前人格",hunger:"当前人格",sleep:"当前角色 · 共享作息",cycle:"当前人格 · 共享模拟",
    busy:"只读",location:"只读",mood:"当前人格 · 跨窗口共享",planning:"当前角色 · 私聊隔离",weather:"当前角色 · 私聊共享",datetime:"当前人格",patrol:"当前人格",presentation:"当前角色 · QQ 展示",
  };
  function openFeatureEditor(feature) {
    const kind=featureEditors[feature];
    if(kind)editor?.open(kind);
  }
  function dashboardItem(feature,label,value,icon,detail="",tone="") {
    const energy=feature==="energy"&&typeof value==="number";
    const description=[label,value,detail].filter(Boolean).join(" · ");
    return `<button type="button" id="state-status-${feature}" class="state-board-point ${tone}" data-state-feature="${feature}"
      aria-expanded="${activeFeature===feature}" aria-controls="state-feature-detail" aria-label="${e(description)}" title="${e(description)}">
      <i data-lucide="${icon}" aria-hidden="true"></i><span>${label}</span><strong>${e(energy?`${value} / 100`:value)}</strong>
      ${energy?`<meter min="0" max="100" value="${Math.max(0,Math.min(100,value))}" aria-label="能量 ${value} / 100"></meter>`:""}</button>`;
  }
  function maintenanceFeedback(status) {
    if(!status.settings?.enabled)return {label:"状态维护已关闭",tone:"is-pending",scheduled:false};
    if(status.host_state&&status.host_state!=="running")return {label:"状态维护已暂停",tone:"is-pending",scheduled:false};
    const result=status.maintenance?.result;
    const failed={expired:"上次维护已过期",failed:"上次维护失败",dead_letter:"上次维护失败",cancelled:"上次维护已取消",uncertain:"上次维护结果未确认"};
    const code=result?.code||result?.reason;
    if(result?.state==="waiting"&&["state_role_context_unavailable","state_context_unbound"].includes(code))return {
      label:"角色状态计划已保留，等待可信运行上下文",tone:"is-pending",scheduled:true};
    if(result?.state==="blocked") {
      const stage=({binding:"等待角色绑定",authorization:"权限检查",settings:"设置检查",schedule_refresh:"计划检查",budget:"预算检查"})[result.stage];
      const detail=CODE.test(code||"")?`（${code}）`:"";
      const hint=result.stage==="authorization"&&code==="authorization_denied"
        ?(status.settings?.maintenance_grant_scope==="role"
          ?"；请在本窗口把后台状态维护关掉再打开并保存"
          :"；请在本窗口把后台状态维护关掉再打开并保存，或把授权范围改为整个角色"):"";
      return {label:`状态维护预检未通过${stage?` · ${stage}`:""}${detail}${hint}`,tone:"is-warning",scheduled:true};
    }
    if(result?.state==="deferred")return {
      label:"状态维护等待本地预算"+(result.retry_at?` · ${when(result.retry_at)} 自动检查恢复`:" · 本地限制解除后自动恢复"),
      tone:"is-pending",scheduled:true};
    if(result?.state==="expired"&&code==="deadline_before_start")return {
      label:"上次维护未开始：等待后台执行席位超时",tone:"is-warning",scheduled:true};
    if(failed[result?.state]) {
      const detail=({timeout:"执行超时",worker_error:"执行出错",worker_lost:"执行任务失联",
        owner_unavailable:"原任务材料已不可用",state_foreground_active:"对话正在使用当前状态",
        model_budget_exhausted:"插件本地硬预算已触发，请查看治理与维护中的预算设置",
        model_budget_soft_deferred:"插件本地软限额已触发，等待下一轮维护",
        model_usage_unknown:"模型调用未完成确认，可能超时或返回格式不符；保留上次状态",
        response_contract_rejected:"模型返回格式不符合状态要求，保留上次状态",
        provider_wait_timed_out:"等待模型超时，保留上次状态"})[code];
      return {label:failed[result.state]+(detail?`：${detail}`:CODE.test(code||"")?`（${code}）`:""),tone:"is-warning",scheduled:true};
    }
    return {label:statusLabel(status.state?.state),tone:status.state?.state==="unknown"?"is-pending":"",scheduled:true};
  }
  function dashboardMarkup(status) {
    const values=status.state?.values||{},settings=status.settings||{};
    const maintenance=maintenanceFeedback(status);
    const energy=settings.energy_enabled===false?"未启用":typeof values.energy==="number"&&Number.isFinite(values.energy)?values.energy:valueLabel(values.energy);
    const sleep=status.body_sleep?.state==="sleeping"?"睡眠中":settings.enabled===false?"维护已关闭":settings.sleep_enabled===false?"未启用":valueLabel(values.sleep);
    const health=!settings.enabled?"维护已关闭":settings.health_enabled===false?"已关闭":valueLabel(values.health);
    const hunger=!settings.enabled?"维护已关闭":settings.hunger_enabled===false?"已关闭":valueLabel(values.hunger);
    const rawMood=values.mood;
    const mood=!settings.enabled?"维护已关闭":!settings.mood_enabled?"未启用"
      :typeof rawMood==="string"?rawMood:rawMood?.description||"尚未记录";
    return `<section class="state-dashboard" aria-labelledby="state-dashboard-title">
      <header><div><span class="state-eyebrow">${e(personaLabel())}</span><h2 id="state-dashboard-title">状态看板</h2></div>
        <span class="state-dashboard-updated">${e(status.state?.updated_at?`更新于 ${when(status.state.updated_at)}`:statusLabel(status.state?.state))}</span></header>
      <div class="state-dashboard-grid">
        ${dashboardItem("energy","能量",energy,"battery-medium",`表达强度 ${settings.humanized_state_intensity??50} / 100`,"tone-energy")}
        ${dashboardItem("health","健康",health,"heart-pulse","人格共享模拟","tone-health")}
        ${dashboardItem("hunger","饥饿",hunger,"utensils","人格共享模拟","tone-hunger")}
        ${dashboardItem("sleep","睡眠",sleep,"moon-star",`${settings.enabled===false||settings.sleep_enabled===false?"状态模拟未运行":"状态模拟已启用"} · ${status.sleep_schedule?.enabled?"自动作息已启用":"自动作息未启用"}`,"tone-sleep")}
        ${dashboardItem("cycle","生理期",phaseLabel(status),"calendar-heart",settings.cycle_enabled?"人格共享模拟":"周期模拟未开启","tone-cycle")}
        ${dashboardItem("location","位置",valueLabel(values.location,settings.location_enabled!==false),"map-pin","对话中记录的人格位置","tone-location")}
        ${dashboardItem("mood","心情",mood,"smile","心情模拟设置","tone-mood")}
        ${dashboardItem("busy","忙碌",valueLabel(values.busy,settings.busy_enabled!==false),"briefcase-business","日程状态与忙碌处理","tone-busy")}
      </div>
      <footer><span class="state-dashboard-status ${maintenance.tone}" role="status"><i aria-hidden="true"></i>${e(maintenance.label)}</span>
        <span>${maintenance.scheduled&&status.maintenance?.enabled!==false&&status.maintenance?.next_at?`下次更新 ${e(when(status.maintenance.next_at))}`:""}</span></footer>
    </section>`;
  }
  function extraButtons(status) {
    const settings=status.settings||{};
    const zone=settings.planning_timezone||"Asia/Shanghai";
    const local=options=>{try{return new Intl.DateTimeFormat("zh-CN",{timeZone:zone,...options}).format(new Date());}catch{return "时区待核对";}};
    const location=window.CompanionStateMaintenance?.weatherLocationParts(settings.planning_weather_location)?.name;
    const cards=[
      {name:"presentation",label:"QQ 状态",icon:"radio-tower",value:`${[settings.typing_enabled,settings.presence_enabled,settings.online_status_enabled].filter(Boolean).length} 项已开启`,help:"正在输入提示 · 在线状态 · 自定义短状态"},
      {name:"planning",label:"日程配置",icon:"calendar-days",value:settings.calendar_enabled?(settings.schedule_simulation_mode==="continuous"?"连续日程":"日程编排"):"日程已关闭",help:"规划方式 · 安排与公开文案"},
      {name:"weather",label:"天气",icon:"cloud-sun",value:settings.planning_weather_enabled?(location||"地点待配置"):"天气感知已关闭",help:"角色所在地 · 实况信息时效"},
      {name:"datetime",label:"时间与日期",icon:"calendar-clock",value:local({hour:"2-digit",minute:"2-digit",hourCycle:"h23"})+" · "+local({month:"long",day:"numeric",weekday:"long"}),help:zone+" · 日期背景"},
    ];
    return `<nav class="state-extra-nav" aria-label="日程与环境状态设置">${cards.map(({name,label,icon,value,help,action})=>
      `<button type="button" id="state-extra-${name}" class="state-extra-card" ${action||`data-state-feature="${name}" aria-expanded="${activeFeature===name}" aria-controls="state-feature-detail"`}><i data-lucide="${icon}" aria-hidden="true"></i><span class="state-extra-copy"><strong>${label}</strong><span>${e(value)}</span><small>${e(help)}</small></span><i class="state-extra-arrow" data-lucide="chevron-right" aria-hidden="true"></i></button>`).join("")}</nav>`;
  }
  function featurePanelMarkup(status,feature=activeFeature) {
    if(!feature||!featureInfo[feature])return "";
    const [title,icon]=featureInfo[feature],values=status.state?.values||{},settings=status.settings||{};
    let body="";
    if(feature==="energy")body=workspaceOverview(status,feature)+(editor?.markup({feature,related:relatedSettings([["生理期 · 周期精力偏移","六阶段的精力偏移在「生理期」子页面里单独设置。","calendar-heart",'data-state-open-feature="cycle"']])})||"");
    else if(feature==="sleep") {
      const plan=status.sleep_schedule||{},execution=status.body_sleep;
      const rhythmNames={awake:"清醒",sleepy:"逐渐困倦",winding_down:"准备休息",sleeping:"睡眠中",recovering:"醒来恢复"};
      const current=execution?.state==="sleeping"?"睡眠中":execution?.state==="interrupted"?"最近一次睡眠已中断":valueLabel(values.sleep,settings.enabled!==false&&settings.sleep_enabled!==false);
      const details=execution?.state==="sleeping"?`计划醒来 ${when(execution.planned_wake_at)}；这是实际睡眠回执。`:"状态模拟负责更新困意与主观睡眠感受；自动入睡按单独的角色作息执行，并记录睡眠回执。";
      const simulation=settings.enabled===false?"后台状态维护已关闭":settings.sleep_enabled===false?"未启用":"已启用";
      const schedule=plan.enabled?`${plan.time||"未设时刻"} 入睡 · ${plan.duration_minutes??"未提供"} 分钟`:"未启用";
      body=overviewMarkup(feature,"当前睡眠",current,details,[["睡眠状态模拟",simulation],["按角色作息自动入睡",schedule],["执行窗口",plan.executor_state==="available"?`${window.CompanionProduct?.executorLabel?.(plan)||"已选定私聊窗口"}${plan.executor_is_current?"（当前窗口）":""}`:plan.enabled?"暂无有效授权窗口":"自动作息未启用"],["时区与自然浮动",`${plan.timezone||"未提供"} · ±${plan.variation_minutes??0} 分钟`],["节律阶段",plan.rhythm?.phase?`${rhythmNames[plan.rhythm.phase]||"节律观察中"} · ${plan.rhythm.basis==="execution_receipt"?"执行回执":"作息参考"}`:"尚无节律参考"]]);
      body+=`<section class="state-config-group"><header><span class="state-config-index">01</span><div><h4>作息概览</h4><p>以下是当前生效的作息参数；就寝时刻与时长在「日记与生活」编辑。</p></div></header><dl class="state-config-readonly">${[["就寝时刻",plan.time||"尚未配置"],["睡眠时长",plan.duration_minutes!=null?`${plan.duration_minutes} 分钟`:"尚未配置"],["自然浮动",plan.variation_minutes!=null?`±${plan.variation_minutes} 分钟`:"尚未配置"],["作息时区",plan.timezone||"尚未配置"]].map(([label,value])=>`<div><dt>${label}</dt><dd>${e(value)}</dd></div>`).join("")}</dl><details class="state-config-sleep-timing"><summary>查看下次入睡与执行详情</summary>${sleepMarkup(status)}</details></section>`;
      body+=relatedSettings([["日记与生活 · 睡眠与休息","编辑就寝时刻、睡眠时长与自然浮动。","book-open","data-state-open-life-sleep"],["开口闸门 · 睡眠时是否回复","睡眠中是否回复、如何回复，在「主动联系」里设置。","message-square",'data-state-gate="sleep"']]);
    } else if(feature==="busy")body=overviewMarkup(feature,"当前状态",valueLabel(values.busy,settings.busy_enabled!==false),"忙碌状态参考已接受的日程；忙碌时如何回复单独设置。")
      +`<section class="state-config-note"><h4>为什么不能直接编辑</h4><p>忙碌是日程的产物。要改忙碌时段就改日程；要改忙碌时怎么回复，就去开口闸门。</p></section>`
      +relatedSettings([["日程与规划 · 查看日程","查看与编辑角色的日程安排、重复规则与例外。","calendar-days",'data-state-open-feature="planning"'],["开口闸门 · 忙碌时是否回复","忙碌中是否回复、如何回复，在「主动联系」里设置。","message-square",'data-state-gate="busy"']]);
    else if(feature==="location")body=overviewMarkup(feature,"人格位置",valueLabel(values.location,settings.location_enabled!==false),"由对话内容自然形成，随人格在窗口间共享。")
      +`<section class="state-config-note"><h4>关于位置</h4><p>位置只在角色自己的语境里使用，不会读取或记录聊天对象的真实位置。</p></section>`;
    else if(feature==="planning") {
      const plan=status.planning,continuous=settings.schedule_simulation_mode==="continuous";
      const running=!settings.calendar_enabled?"角色日程已关闭":continuous?"对话时接续现有计划":!settings.planning_enabled?"自动规划已关闭":plan?.running?"正在规划":plan?"等待下次检查":"运行状态尚未提供";
      body=overviewMarkup(feature,"当前运行",running,"日程配置对同一角色的所有私聊生效，各私聊安排仍相互隔离。",[["所选模式",continuous?"连续日程（HDSI）":"日程编排"],["安排节奏",continuous?"接续已接受的计划；自动编排暂停":`每天安排 · 每周补充长期节点 · 提前 ${settings.planning_refine_minutes??"未提供"} 分钟细化`],["规划范围",`${settings.planning_horizon_days??"未提供"} 天 · 每次最多 ${settings.planning_max_items??"未提供"} 项`],["下次检查",plan?.next_check?when(plan.next_check):"尚未排程"]]);
      body+=(editor?.markup({feature,related:relatedSettings([["天气感知","长期安排会参考所在地天气；前往调整开关、地点与信息时效。","cloud-sun","data-state-open-weather"]])})||"")+(editor?.plansMarkup?.()||"");
      body+=`<details class="state-config-diagnostics"><summary>查看规划运行记录</summary>${window.CompanionStateMaintenance?.planningSummary(status,e)||""}</details>`;
    } else if(feature==="weather") {
      const location=window.CompanionStateMaintenance?.weatherLocationParts(settings.planning_weather_location)||{};
      body=overviewMarkup(feature,"天气参考",settings.planning_weather_enabled?`已开启${location.name?" · "+location.name:" · 地点待配置"}`:"已关闭",`信息沿用 ${settings.planning_weather_max_age_minutes??"未提供"} 分钟；超时会按授权重新读取。`,[["数据来源","在感知页配置和风 / Open-Meteo"],["角色天气地点",[location.name,location.alert_area].filter(Boolean).join(" · ")||"尚未配置"],["信息时效",`${settings.planning_weather_max_age_minutes??"未提供"} 分钟`],["状态更新时间",status.state?.updated_at?when(status.state.updated_at):"尚无记录"]])+(editor?.markup({feature,related:section==='weather'?'':relatedSettings([["数据源与和风凭据","配置天气源、API Host、JWT / API Key 和气象预警。","cloud-sun","data-state-source-settings"]])})||"");
    } else if(feature==="datetime") {
      const zone=settings.planning_timezone||"Asia/Shanghai";
      const local=options=>{try{return new Intl.DateTimeFormat("zh-CN",{timeZone:zone,...options}).format(new Date());}catch{return "时区待核对";}};
      body=overviewMarkup(feature,"当地时间与日期",local({hour:"2-digit",minute:"2-digit",hourCycle:"h23"})+" · "+local({year:"numeric",month:"long",day:"numeric",weekday:"long"}),"按角色时区计算；对话日期背景在感知设置中维护。",[["小屋与日程时区",zone],["日期来源","当前系统时间按时区换算"]])
        +(editor?.markup({feature,related:relatedSettings([["日期与历法背景","设置对话日期时区、日期开关、节假日、农历与节气。","calendar-days","data-state-date-settings"]])})||"");
    } else if(feature==="presentation") {
      const diag=status.presentation_diagnostics,enabled=[settings.typing_enabled,settings.presence_enabled].filter(Boolean).length;
      const channel=(name,value)=>{
        if(!settings[name])return "开关已关闭";
        if(!status.effective?.[name]?.allowed)return `已开启 · ${presentationReasons[status.effective?.[name]?.reason]||"授权待核对"}`;
        if(!value)return "已开启 · 授权通过";
        const label=({idle:"已开启 · 当前空闲",waiting:"已开启 · 等待任务",refreshing:"已开启 · 刷新中",suppressed:"本轮已停止刷新",recorded:"相同文案已有请求记录",deduplicated:"账号已有相同文案记录",blocked:"当前条件未满足",paused:"已暂停",unavailable:"展示端口不可用"})[value.state]||"运行状态待确认";
        return [label,value.reason?presentationReasons[value.reason]||reason(value.reason):null].filter(Boolean).join(" · ");
      };
      body=overviewMarkup(feature,"展示通道",`${enabled} 项开关已开启`,"受理确认只说明请求被平台接受，不代表客户端当前展示状态。",[["正在输入",channel("typing_enabled",diag?.typing)],["自定义短状态",channel("presence_enabled",diag?.custom_status)],["输入提示刷新间隔",`${settings.typing_refresh_seconds??"未提供"} 秒（取值 1–10）`],["诊断时间",diag?.observed_at?when(diag.observed_at):"尚无诊断记录"]])+(editor?.markup({feature,details:presentationMarkup(status,{receiptsOnly:true})})||"");
    } else body=workspaceOverview(status,feature)+(editor?.markup({feature})||"");
    const scopePill=featureScopes[feature]||"当前角色";
    const subtitle=featureSubtitles[feature]||"";
    return `<section id="state-feature-detail" class="state-feature-detail state-config-page" data-state-config-feature="${feature}" aria-labelledby="state-feature-title">
      <header><button type="button" class="state-feature-back" data-state-feature-close aria-label="收起${title}" title="返回状态看板" ${editor?.busy?"disabled":""}><i data-lucide="arrow-left" aria-hidden="true"></i></button><div class="state-feature-head-main">
          <span class="state-eyebrow">状态 · ${title}</span>
          <h3 id="state-feature-title"><i data-lucide="${icon}" aria-hidden="true"></i>${title}</h3>
          ${subtitle?`<p class="state-feature-subtitle">${e(subtitle)}</p>`:""}
        </div>
        <div class="state-feature-head-aside">
          <span class="state-feature-scope-pill">${e(scopePill)}</span>
          <button type="button" class="state-feature-close" data-state-feature-close aria-label="收起${title}" title="收起" ${editor?.busy?"disabled":""}><i data-lucide="x" aria-hidden="true"></i></button>
        </div></header>
      <div class="state-feature-content">${body}</div>
    </section>`;
  }
  function syncSettingsButton() {
    const button=document.getElementById?.("state-settings-button");
    if(!button)return;
    button.disabled=!root?.isConnected||section!=="state"||!data?.status||data.loading||!!editor?.busy;
    button.setAttribute("aria-expanded",String(settingsOpen));
  }
  function homeSettingsMarkup() {
    return `<dialog id="state-home-settings" class="state-home-settings state-config-page" aria-labelledby="state-home-settings-title">
      <header><div><span class="state-eyebrow">${e(personaLabel())} · 人格共享</span><h3 id="state-home-settings-title"><i data-lucide="settings-2" aria-hidden="true"></i>状态功能与首页展示</h3></div><button type="button" class="state-feature-close" data-state-close-home-settings aria-label="关闭状态设置" title="关闭" ${editor?.busy?"disabled":""}><i data-lucide="x" aria-hidden="true"></i></button></header>
      <div class="state-home-settings-body">${data?.status?editor?.markup({feature:"home"})||"":`<p class="memory-notice" role="status">${e(data?.notice||"正在读取状态设置…")}</p><button type="button" class="memory-button" id="state-home-settings-refresh" ${data?.loading?"disabled":""}>重新读取状态</button>`}</div>
    </dialog>`;
  }
  /* 地址栏的落点：`#life/state/<feature>` 进来时只切图块，不打开编辑表单
     （打开表单是搜索落点的行为，见 openEditor）。 */
  function openFeature(feature) {
    if(!data?.status||data.loading||section!=="state"||!root?.isConnected||settingsOpen)return false;
    if(!featureInfo[feature])return false;
    if(editor&&!editor.discardIfPristine())return false;
    if(activeFeature===feature)return true;
    activeFeature=feature;
    openFeatureEditor(feature);
    render();schedule();
    return true;
  }
  /* 全局搜索的落地钩子：搜索命中 role.state 的某个字段时，先切到对应图块，
     再打开它的编辑表单，最后把焦点落到那个输入框上。
     与「点图块 → 点配置」是同一条路径（featureEditors 把 kind 映射回图块）。 */
  function openEditor(kind, field) {
    if(!data?.status||data.loading||section!=="state"||!root?.isConnected||settingsOpen)return false;
    if(editor&&!editor.discardIfPristine())return false;
    const feature=Object.keys(featureEditors).find(name=>featureEditors[name]===kind)||null;
    if(feature)activeFeature=feature;
    editor?.open(kind);
    render();schedule();
    if(!field)return true;
    const attempt=(tries=0)=>{
      const input=root?.querySelector(`[data-state-field="${field}"]`);
      if(input){input.focus({preventScroll:false});input.scrollIntoView({block:"center",behavior:"smooth"});return;}
      if(tries<20)requestAnimationFrame(()=>attempt(tries+1));
    };
    requestAnimationFrame(()=>attempt());
    return true;
  }
  function openSettings(trigger) {
    if(!data?.status||data.loading||section!=="state"||!root?.isConnected||settingsOpen)return;
    if(editor&&!editor.discardIfPristine())return;
    settingsReturnFeature=activeFeature;settingsReturnFocus=trigger||document.activeElement;
    settingsOpen=true;activeFeature=null;
    render();schedule();
  }
  function closeSettings() {
    if(!settingsOpen)return;
    if(editor&&!editor.discardIfPristine())return;
    const dialog=root?.querySelector("#state-home-settings");
    if(dialog?.open)dialog.close();
    settingsOpen=false;activeFeature=settingsReturnFeature;settingsReturnFeature=null;
    render();schedule();
    if(settingsReturnFocus?.isConnected)settingsReturnFocus.focus({preventScroll:true});
    settingsReturnFocus=null;
  }
  function render() {
    if (!root?.isConnected || !data) return;
    const status=data.status,roleTarget=target(),contactView=section==="contact",weatherView=section==="weather",patrolView=section==="patrol";
    if(role()?.state_api&&data.catalog)editor?.setScope({installation_id:data.catalog.installation_id,operator_id:data.catalog.operator_id,path:endpoint()});
    else editor?.invalidate();
    editor?.setStatus(status,data.loading);
    if(!contactView&&editor?.requiredFeature) {
      const required=editor.requiredFeature;
      if(required==="home"){settingsOpen=true;activeFeature=null;}
      else activeFeature=required==="health"&&activeFeature==="hunger"?"hunger":required;
    }
    const oldDialog=root.querySelector("#state-home-settings");
    const dialogScroll=oldDialog?.querySelector(".state-home-settings-body")?.scrollTop||0;
    const focused=root.contains(document.activeElement)?document.activeElement.id:null;
    const expanded=new Set([...root.querySelectorAll(".state-receipt-reference[open]")].map(item=>item.dataset.receipt));
    const available=!!role()?.state_api;
    let content;
    if (!data.ready) content=`<div class="panel state-empty">${data.loading?"正在读取授权目标…":"刷新目标后即可查看角色状态。"}</div>`;
    else if (!available) content=`<div class="panel state-empty"><h3>当前角色尚未绑定状态工作区</h3><p>请在账号与会话中核对 Bot 账号和人格映射。</p></div>`;
    else if (!status) content=`<div class="panel state-empty">${data.loading?"正在读取状态…":"尚未读取状态。"}</div>`;
    else if(contactView)content=editor?.markup({section:"contact"})||"";
    else if(weatherView)content=featurePanelMarkup(status,"weather");
    else if(patrolView)content=featurePanelMarkup(status,"patrol");
    else {
      const values=status.state?.values||{};
      const temporary=Array.isArray(values.conditions)&&values.conditions.length
        ?`<section class="state-conditions-inline"><h3>当前临时状态</h3><ul>${values.conditions.map(item=>`<li><strong>${e(item.kind)}</strong><span>${e(item.text)}</span><small>${item.expires_at?`记录至 ${when(item.expires_at)}`:"未设结束时间"}</small></li>`).join("")}</ul></section>`:"";
      content=`${dashboardMarkup(status)}${extraButtons(status)}
        ${activeFeature?featurePanelMarkup(status):""}${temporary}`;
    }
    const selectedScope=window.CompanionProduct?.selection?.();
    const accessScope=roleTarget ? (window.CompanionProduct?.targetLabel?.(roleTarget) || (roleTarget.scope_kind === "group" ? "当前群聊" : "当前私聊窗口")) : "尚未选择真实会话";
    const scopeMarkup=contactView?`<div class="panel memory-scope state-scope"><div class="state-scope-current"><span>当前会话</span><strong>${e(accessScope)}</strong><small>联系安排适用于当前会话。</small></div>${!selectedScope?`<button type="button" class="button small" data-go="manage" data-manage="host">去账号与会话选择</button>`:""}</div>`
      :"";
    if(oldDialog?.open)oldDialog.close();
    const pending=data.pendingMaintenance,maintenanceOff=status?.settings?.enabled===false;
    const maintainDisabled=!status||data.loading||data.maintenanceSubmitting||editor?.busy||pending&&!pending.retryable;
    const maintainLabel=data.maintenanceSubmitting?"正在请求…":pending?(pending.retryable?"重试更新":"更新中…")
      :maintenanceOff?"前往设置":"立即更新";
    const maintainIcon=maintenanceOff?"settings-2":"sparkles";
    const noticeText=data.notice||(maintenanceOff?"状态维护已关闭，启用后才会生成新的状态记录。":"");
    const pageHint=contactView?"设置当前会话的联系频率与打扰边界。"
      :weatherView?"设置天气来源与时效，日程会参考所在地实况。"
      :patrolView?"设置每日巡视的时刻与保留时长。"
      :"点按任意状态查看设置；下方入口可调整 QQ 展示、日程、天气与时间。";
    root.innerHTML=`<div class="state-page-heading"><div><span class="state-badge">${contactView?"联系安排":weatherView?"天气感知":patrolView?"维护设置":"角色状态"}</span><p class="state-page-hint">${e(pageHint)}</p></div><div class="state-page-actions">${section==="state"?`<button type="button" class="memory-button state-maintain-button" id="state-maintain" ${maintainDisabled?"disabled":""} ${maintenanceOff?`title="在状态设置中启用后台状态维护"`:""}><i data-lucide="${maintainIcon}" aria-hidden="true"></i><span>${maintainLabel}</span></button>`:""}<button type="button" class="memory-button" id="state-refresh" title="重新读取当前状态" ${data.loading||editor?.busy?"disabled":""}>${contactView?"刷新联系设置":weatherView?"刷新天气设置":patrolView?"刷新巡视设置":"刷新状态"}</button></div></div>
      ${scopeMarkup}<div class="memory-notice ${data.error?"error":""}" id="state-notice" role="status" aria-live="polite" ${noticeText?"":"hidden"}>${e(noticeText)}</div>${content}${settingsOpen?homeSettingsMarkup():""}`;
    root.querySelectorAll(".state-receipt-reference").forEach(item=>{item.open=expanded.has(item.dataset.receipt);});
    const settingsDialog=root.querySelector("#state-home-settings");
    if(settingsDialog){
      settingsDialog.addEventListener("cancel",event=>{event.preventDefault();closeSettings();});
      settingsDialog.addEventListener("click",event=>{
        if(event.target!==settingsDialog)return;
        const box=settingsDialog.getBoundingClientRect();
        if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom)closeSettings();
      });
      settingsDialog.showModal();
      settingsDialog.querySelector(".state-home-settings-body").scrollTop=dialogScroll;
    }
    if(focused)root.querySelector("#"+focused)?.focus({preventScroll:true});
    syncSettingsButton();
    window.lucide?.createIcons({attrs:{"aria-hidden":"true"}});
  }
  function schedule() {
    stopTimer();
    if(data?.ready&&role()?.state_api&&!document.hidden&&!editor?.interacting)timer=setTimeout(()=>{if(!data.loading)load();else schedule();},data?.pendingMaintenance?3000:15000);
  }
  async function refreshAfterSave() {
    const result=await load();
    if(result)await window.CompanionProduct?.refreshSummary?.();
    return result;
  }
  function failure(error) {
    data.status=null; data.error=true; data.notice=reason(error?.code);
    if(["dashboard_identity_required","core_access_required","authorization_denied","role_binding_changed","binding_revoked","binding_rebound","staging_host_terminated"].includes(error?.code)) {
      data.catalog=null; data.ready=false; editor?.invalidate(); stopTimer();
      settingsOpen=false;settingsReturnFeature=settingsReturnFocus=null;
    }
    stateByScope.delete(stateScopeKey);
  }
  async function directory() {
    const ticket=++version; stopTimer();
    const cached=stateByScope.get(stateScopeKey), fresh=blank();
    if(cached){fresh.catalog=cached.catalog;fresh.target=cached.target;fresh.role=cached.role;fresh.status=cached.status;fresh.cursor=cached.cursor;
      fresh.pendingMaintenance=cached.pendingMaintenance;fresh.ready=!!cached.catalog;}
    data=fresh; data.loading=true; render();
    try {
      const catalog=window.CompanionProduct?.directory?.() || await api("GET","page/targets");
      if(!live(ticket))return;
      if(!Array.isArray(catalog.targets)||!catalog.installation_id||catalog.targets.some(item=>!Array.isArray(item.roles))) throw Object.assign(new Error("invalid_response"),{code:"invalid_response"});
      data.catalog=catalog;data.ready=true;data.loading=false;
      const product=window.CompanionProduct;
      const selected=product?.selection();
      const owner=product?.roleTarget?.();
      const targetIndex=catalog.targets.findIndex(item=>item.target_id===(owner?.target_id||selected?.target_id));
      const roleIndex=targetIndex>=0?catalog.targets[targetIndex].roles.findIndex(item=>item.role_id===selected?.role_id):-1;
      if(targetIndex>=0&&roleIndex>=0){data.target=targetIndex;data.role=roleIndex;}
      if(targetIndex>=0&&roleIndex>=0&&role()?.state_api){endpoint();await load();}else {data.notice="当前角色尚未绑定状态工作区，请核对账号与人格映射。";render();}
    } catch(error) { if(live(ticket)){failure(error);data.loading=false;render();} }
  }
  async function load(cursor=data?.cursor||"") {
    if(!root||!data.ready||!role()?.state_api||data.loading)return;
    const ticket=++version;stopTimer();data.cursor=cursor;data.loading=true;render();
    try {
      const result=await api("GET",endpoint(),{after_id:data.cursor});
      if(!live(ticket))return;
      if(!result.state||!result.settings||!result.sleep_schedule)throw Object.assign(new Error("invalid_response"),{code:"invalid_response"});
      data.status={...result,request_cursor:data.cursor};
      if(data.pendingMaintenance){
        const pending=data.pendingMaintenance,revision=result.state?.revision;
        const currentResult=JSON.stringify(result.maintenance?.result??null);
        const failedStates=["failed","cancelled","expired","dead_letter","uncertain","rejected","blocked"];
        const completedStates=["succeeded","completed","saved"];
        if(Number.isInteger(revision)&&revision>pending.expected_revision){
          data.pendingMaintenance=null;notice("状态已更新。");
          void Promise.resolve(window.CompanionProduct?.refreshSummary?.()).catch(()=>{});
        }
        else if(result.maintenance?.result&&currentResult!==pending.resultSnapshot&&failedStates.includes(result.maintenance.result.state)){
          data.pendingMaintenance=null;notice("状态更新失败，请查看维护状态。",true);
        }else if(result.maintenance?.result&&currentResult!==pending.resultSnapshot&&completedStates.includes(result.maintenance.result.state)){
          data.pendingMaintenance=null;notice("维护完成，状态保持不变。");
        }else notice("状态更新中…");
      }else notice("");
      stateScopeKey=currentStateScope(); rememberState();
      return result;
    } catch(error) { if(live(ticket))failure(error);return null; }
    finally { if(live(ticket)){data.loading=false;render();schedule();} }
  }
  async function requestMaintenance() {
    if(!root?.isConnected||!data?.status||data.loading||data.maintenanceSubmitting||section!=="state")return;
    if(editor&&!editor.discardIfPristine())return;
    if(data.status.settings?.enabled===false){notice("请先在状态设置中启用状态维护。",true);render();return;}
    const current=data.status.state;
    if(!Number.isInteger(current?.revision)||typeof current?.view_token!=="string"){
      notice("当前状态尚未就绪，请重新读取后再试。",true);render();return;
    }
    let pending=data.pendingMaintenance;
    if(pending&&!pending.retryable)return;
    if(!pending)pending={operation_key:crypto.randomUUID(),expected_revision:current.revision,view_token:current.view_token,
      resultSnapshot:JSON.stringify(data.status.maintenance?.result??null),retryable:false};
    pending.retryable=false;data.pendingMaintenance=pending;data.maintenanceSubmitting=true;
    notice("正在安排状态更新…");render();
    try {
      await api("POST",endpoint()+"/command",{action:"maintain",operation_key:pending.operation_key,
        view_token:pending.view_token,expected_revision:pending.expected_revision});
      if(!root?.isConnected||data.pendingMaintenance?.operation_key!==pending.operation_key)return;
      data.maintenanceSubmitting=false;notice("状态更新已排队。");render();schedule();
    } catch(error) {
      if(!root?.isConnected||data.pendingMaintenance?.operation_key!==pending.operation_key)return;
      data.maintenanceSubmitting=false;
      if(error?.confirmed){data.pendingMaintenance=null;notice(reason(error.code),true);}
      else {pending.retryable=true;notice("未收到确认，重试会复用本次请求。",true);}
      render();schedule();
    }
  }
  function changeScope(targetIndex,roleIndex) {
    if(!data.catalog?.targets[targetIndex]?.roles[roleIndex])return;
    ++version;stopTimer();const catalog=data.catalog;
    data={...blank(),catalog,target:targetIndex,role:roleIndex,ready:true};
    window.CompanionProduct?.choose(catalog.targets[targetIndex].target_id,catalog.targets[targetIndex].roles[roleIndex].role_id,true);
    if(role()?.state_api){try{endpoint();load();}catch(error){failure(error);render();}}else render();
  }
  function click(event) {
    if(event.target.closest("[data-state-close-home-settings]")){closeSettings();return;}
    if(event.target.closest("[data-state-date-settings]")) {
      if(editor&&!editor.discardIfPristine())return;
      window.navigate?.("manage","perception");
      requestAnimationFrame(()=>window.CompanionPerceptionTabs?.activate?.("config-models:perception-detail-context",{focus:true,scroll:true}));
      return;
    }
    const gate=event.target.closest("[data-state-gate]");
    if(gate) {
      if(editor&&!editor.discardIfPristine())return;
      window.navigate?.("manage","contact");
      window.CompanionContactPage?.openGateSettingsView?.();
      return;
    }
    if(event.target.closest("[data-state-open-life-sleep]")) {
      if(editor&&!editor.discardIfPristine())return;
      window.navigate?.("life","diary","sleep");
      return;
    }
    const openFeature=event.target.closest("[data-state-open-feature]");
    if(openFeature) {
      if(editor&&!editor.discardIfPristine())return;
      activeFeature=openFeature.dataset.stateOpenFeature;
      openFeatureEditor(activeFeature);
      render();schedule();
      root?.querySelector("#state-feature-detail")?.scrollIntoView({behavior:matchMedia("(prefers-reduced-motion: reduce)").matches?"auto":"smooth",block:"nearest"});
      /* 打开的功能图块能进地址栏（见 app.js 的 secondarySubtab），
         这样刷新或把链接发出去都落到同一屏。 */
      window.dispatchEvent(new CustomEvent("companion:state-route"));
      return;
    }
    const sourceSettings=event.target.closest("[data-state-source-settings]");
    if(sourceSettings){
      if(!editor?.discardIfPristine())return;
      window.navigate?.("manage","perception");
      requestAnimationFrame(()=>window.CompanionPerceptionTabs?.activate("config-environment:perception-detail-place",{focus:true,scroll:true}));
      return;
    }
    const openWeather=event.target.closest("[data-state-open-weather]");
    if(openWeather) {
      if(editor&&!editor.discardIfPristine())return;
      section="state";activeFeature="weather";render();schedule();
      root?.querySelector("#state-feature-detail")?.scrollIntoView({behavior:matchMedia("(prefers-reduced-motion: reduce)").matches?"auto":"smooth",block:"nearest"});
      return;
    }
    const close=event.target.closest("[data-state-feature-close]");
    if(close) {
      if(editor&&!editor.discardIfPristine())return;
      if(section==="patrol"){window.navigate?.("manage","maintenance");return;}
      const feature=activeFeature||"weather";section="state";activeFeature=null;render();schedule();
      root?.querySelector(`#state-status-${feature}, #state-extra-${feature}`)?.focus({preventScroll:true});
      return;
    }
    const featureButton=event.target.closest("[data-state-feature]");
    if(featureButton) {
      if(editor&&!editor.discardIfPristine())return;
      const feature=featureButton.dataset.stateFeature;
      /* A card means "show this feature"; closing is the back/X button.
         生活日历 mounts 状态页 with feature=planning already open, so the old
         toggle made the first click on the same card collapse it. */
      if(activeFeature===feature)return;
      activeFeature=feature;
      render();schedule();
      root?.querySelector("#state-feature-detail")?.scrollIntoView({
        behavior:matchMedia("(prefers-reduced-motion: reduce)").matches?"auto":"smooth",block:"nearest"});
      return;
    }
    const cycleViewTab=event.target.closest("[data-cycle-view]");
    if(cycleViewTab) {
      const id=cycleViewTab.id;
      window.CompanionStateMaintenance?.selectCycleView(cycleViewTab.dataset.cycleView);
      render();root?.querySelector("#"+id)?.focus({preventScroll:true});
      return;
    }
    const phaseTab=event.target.closest("[data-state-phase]");
    if(phaseTab) {
      const id=phaseTab.id;
      window.CompanionStateMaintenance?.selectPhase(phaseTab.dataset.statePhase);
      render();root?.querySelector("#"+id)?.focus({preventScroll:true});
      return;
    }
    if(event.target.closest("[data-calendar-edit]")&&!calendarEditor?.interacting&&editor&&!editor.discardIfPristine())return;
    if(calendarEditor?.click(event))return;
    if(editor?.click(event)){
      if(settingsOpen&&!editor.dirty&&event.target.closest('[data-state-edit="cancel"]'))closeSettings();
      return;
    }
    const maintain=event.target.closest("#state-maintain");
    if(maintain){if(data.status?.settings?.enabled===false)openSettings(maintain);else requestMaintenance();return;}
    if(event.target.closest("#state-refresh, #state-home-settings-refresh")&&!editor?.busy){if(data.ready)load();else directory();}
    const page=event.target.closest("[data-state-page]");
    if(page&&!page.disabled&&(!editor||editor.discardIfPristine()))load(page.dataset.statePage);
  }
  function keydown(event) {
    const cycleViewTab=event.target.closest("[data-cycle-view]");
    if(cycleViewTab) {
      const views=["plan","expression"],index=views.indexOf(cycleViewTab.dataset.cycleView);
      const next=event.key==="ArrowRight"?(index+1)%views.length:event.key==="ArrowLeft"?(index+views.length-1)%views.length
        :event.key==="Home"?0:event.key==="End"?views.length-1:-1;
      if(next<0)return;
      event.preventDefault();window.CompanionStateMaintenance.selectCycleView(views[next]);render();
      root?.querySelector(`#state-cycle-view-${views[next]}`)?.focus({preventScroll:true});
      return;
    }
    const phaseTab=event.target.closest("[data-state-phase]");
    if(phaseTab) {
      const phases=Object.keys(window.CompanionStateMaintenance?.phases||{}),index=phases.indexOf(phaseTab.dataset.statePhase);
      const next=event.key==="ArrowRight"?(index+1)%phases.length:event.key==="ArrowLeft"?(index+phases.length-1)%phases.length
        :event.key==="Home"?0:event.key==="End"?phases.length-1:-1;
      if(next<0||!phases.length)return;
      event.preventDefault();window.CompanionStateMaintenance.selectPhase(phases[next]);render();
      root?.querySelector(`#state-cycle-phase-${phases[next]}`)?.focus({preventScroll:true});
      return;
    }
  }
  function input(event) {calendarEditor?.input(event);editor?.input(event);
    if(event.target.matches('[data-state-field^="cycle_"][data-state-field$="_days"]'))window.CompanionStateMaintenance?.syncDurationPreview(root);}
  function submit(event) {if(calendarEditor?.submit(event))return;editor?.submit(event);}
  function change(event) {
    calendarEditor?.change(event);editor?.input(event);
    if(event.target.matches('[data-state-field^="cycle_"][data-state-field$="_days"]'))window.CompanionStateMaintenance?.syncDurationPreview(root);
  }
  function setChrome(active) {
    document.body.classList.toggle("state-connected",active);
    /* 外壳徽标在场时由 product.updateHostChrome() 统一渲染，这里不抢。
       只有在单独嵌入（没有外壳徽标）时才自己补一条状态——而且不能写
       「设计预览」，那正是「连不上」的观感来源。 */
    if(window.CompanionProduct?.hosted()&&document.querySelector("[data-host-badge]"))return;
    const badge=document.querySelector(".preview-badge");
    if(badge)badge.innerHTML=active?"<span></span>角色状态读取正常 <b>V2</b>":"<span></span>角色状态读取待检查 <b>V2</b>";
    if(active)document.title="陪伴小屋";
  }
  async function mount(element,options={}) {
    unmount();section=["contact","weather","patrol"].includes(options.section)?options.section:"state";activeFeature=Object.hasOwn(featureInfo,options.feature)?options.feature:null;root=element;stateScopeKey=currentStateScope();data=blank();root.addEventListener("click",click);root.addEventListener("keydown",keydown);root.addEventListener("change",change);
    root.addEventListener("input",input);root.addEventListener("submit",submit);
    calendarEditor=section==="state"?window.CompanionCalendarEditor?.create({api,changed:()=>{render();schedule();},refresh:refreshAfterSave,
      failed:error=>{failure(error);render();}}):null;
    editor=window.CompanionStateEditor?.create({api,calendarEditor,changed:()=>{render();schedule();},refresh:refreshAfterSave,
      failed:error=>{failure(error);render();}});
    const ticket=version;
    try {
      /* 等宿主把桥注进来，而不是「现在没有就当作没有」。
         桥的注入晚于页面脚本执行是正常时序：早前这里一看 bridge 为空就直接
         渲染「设计预览」，于是真实宿主里也会看到「请从管理界面打开」——
         用户以为是自己打开方式不对，其实是时序问题。
         等不到（超时）才算真的没接上，那时报 bridge_unavailable 并给重试。 */
      bridge=window.CompanionProduct?.bridgeNow?.()||await window.CompanionProduct?.bridgeFor?.();
      if(!live(ticket))return;
      if(!bridge?.ready)throw Object.assign(new Error("bridge_unavailable"),{code:"bridge_unavailable"});
      const context=await bounded(bridge.ready());if(!live(ticket))return;
      /* 桥在、但不是这一支插件的桥：这确实是环境问题，如实报错。 */
      if(context?.pluginName!==HOST)throw Object.assign(new Error("bridge_unavailable"),{code:"bridge_unavailable"});
      setChrome(true);await directory();
    } catch(error) { if(live(ticket)){failure(error);data.loading=false;render();} }
  }
  function unmount() { rememberState();++version;stopTimer();root?.querySelector("#state-home-settings")?.close();settingsOpen=false;settingsReturnFeature=settingsReturnFocus=null;editor?.destroy();editor=null;calendarEditor=null;root?.removeEventListener("click",click);root?.removeEventListener("keydown",keydown);root?.removeEventListener("change",change);root?.removeEventListener("input",input);root?.removeEventListener("submit",submit);root=null;data=null;bridge=null;activeFeature=null;stateScopeKey="";syncSettingsButton();setChrome(false); }
  document.addEventListener("visibilitychange",()=>{if(!data?.ready)return;if(document.hidden)stopTimer();else if(!data.loading&&!editor?.interacting)load();});
  window.addEventListener?.("beforeunload",event=>{if(editor?.dirty){event.preventDefault();event.returnValue="";}});
  return {mount,unmount,openSettings,openEditor,openFeature,currentFeature:()=>activeFeature,
    canLeave:()=>editor?.discardIfPristine()??true};
})();
