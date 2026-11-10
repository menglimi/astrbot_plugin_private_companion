/* 陪伴小屋 · 事实与经历（档案库）
 *
 * 这一页管事实正本与整理策略。档案操作和策略配置使用同一入口、不同视图。
 *
 * 与旧版的关系：桥接层（whenBridgeReady / api / bounded）、全部 19 条端点的接法、
 * 待确认台账（localStorage 的 pending / pendingMutation）、幂等键与预期版本协议
 * 一律保留原样——那些是查出来的契约，不是可以重画的皮。
 * 重做的是呈现与交互层：
 *
 *   旧：六个平铺页签，内容全塞进一次性 innerHTML，无过渡，无键盘，列表与详情互相独立。
 *   新：左侧视图轨道 + 右侧档案库。事实治理改成 master-detail
 *       （可筛可搜的事实流 ↔ 事实详情含修订时间轴），
 *       整理批次画出六阶段状态机，归档/清理/导入共用同一套「预览→确认」形状。
 *
 * 动效分两层（详见 memory-page.css）：
 *   克制的功能动效——读取进度带、入场位移、保存确认、条目移出；
 *   场景化重头戏——语料比例带（四种生命周期占比）、六阶段轨道推进、状态环。
 *   prefers-reduced-motion 下全部退化为瞬时终态，信息不丢。
 */
"use strict";
window.CompanionMemoryPage = (() => {
  const HOST = "astrbot_plugin_private_companion";
  const PREFIX = "/" + HOST + "/";
  const STORE = "companion-memory-page-v1:";
  const askConfirm = options => typeof window.CompanionConfirm === "function"
    ? window.CompanionConfirm(options) : Promise.resolve(false);
  /* 事实预读：这一层不做服务端分页（后端 list 只支持游标 + limit<=50），
     而是把前几页一次读进内存，之后筛选、搜索、计数全在本地完成——
     筛选时不用再等一个往返，比例带也才有完整分母。 */
  const PRELOAD_PAGES = 4, PAGE_SIZE = 50;
  const errors = {
    dashboard_identity_required: "请从已登录的 AstrBot 管理界面打开陪伴小屋。",
    staging_not_initialized: "角色数据尚未就绪，请在「账号与会话」保存角色映射；已保存可重新读取状态。",
    staging_host_terminated: "插件已停止，请重新打开拓展页。",
    core_access_required: "当前账号没有记忆管理权限。",
    binding_revoked: "管理账号的授权已撤销。", binding_rebound: "管理账号的绑定已变化，请重新进入。",
    memory_reflection_unavailable: "这条整理记录已不在当前授权范围。",
    memory_reflection_uncertain: "原模型调用结果尚未确认。请先核对原任务，不能重复发起。",
    memory_reflection_failed: "本次整理未完成，需要明确发起修复。",
    memory_reflection_closed: "记忆整理服务尚未就绪，请重新读取角色状态。",
    application_not_running: "角色尚未就绪，请重新读取状态。",
    memory_settings_changed: "记忆设置已变化，需要重新准备材料。",
    memory_reflection_stale: "相关记忆已更新，旧分析不能继续写入。",
    source_revoked: "原来源已撤回。", source_expired: "原来源已过期。",
    memory_evidence_forgotten: "相关来源已被遗忘。",
    memory_schedule_changed: "整理计划已变化，需要重新准备材料。",
    model_task_unregistered: "尚未配置记忆整理模型。",
    model_response_contract_rejected: "模型返回格式不完整，已有内容保留，未完成项可以单独修复。",
    memory_request_invalid: "请求与当前接口不匹配，请更新页面后重试。",
    memory_revision_conflict: "事实已有新版本，已刷新当前记录。",
    memory_view_stale: "身份或授权版本已变化，请重新打开事实。",
    memory_clear_stale: "预览后记忆已有变化，已刷新范围预览，请核对后再确认。",
    memory_settings_conflict: "配置已有其他更新，已读取最新值。",
    /* 冷归档：可逆地把长期闲置的持久事实移出工作集。 */
    memory_archive_stale: "预览后记忆已有变化，已刷新归档预览，请核对后再确认。",
    memory_archive_restore_invalid: "请选择要移回工作集的事实。",
    memory_archive_restore_unavailable: "有一条原记录已不可读取，无法移回。",
    memory_archive_restore_conflict: "有一条记录并不在归档状态，未做改动。",
    /* 记忆包：跨安装迁移。 */
    memory_bundle_invalid: "这不是本版支持的记忆包，未做任何写入。",
    memory_bundle_too_large: "记忆包超出体积上限，未被接受。",
    memory_import_stale: "预览后事实已有变化，已重新校验，请核对后再确认。",
    memory_operation_conflict: "这个操作标识已经用于另一份内容，未重复写入。",
    memory_operation_unavailable: "原操作已不可读取，无法核对结果。",
    memory_source_unavailable: "原始来源当前不可读取，可能已撤回或过期。",
    memory_query_budget_exhausted: "本次查询已达到读取预算，请开始新的查询。",
    memory_cursor_stale: "查询索引或记录已变化，请重新提交查询。",
    page_request_invalid: "目标目录请求无效，请重新打开页面。",
    memory_ownership_unavailable: "记忆归属暂时读不出来。这时按「V2 自己提供记忆」处理：注入与工具都会照常出现。",
    bridge_unavailable: "页面还没有接上 AstrBot。若这是从管理界面打开的插件页，刷新一次通常就好；若反复出现，请确认插件已启用。",
    bridge_timeout: "未收到服务确认，请刷新核对。",
    invalid_response: "服务返回的数据不完整，请刷新核对。",
  };
  /* 旧桥接会将机器码放在信封的 message 上。 */
  const CODE = /^[a-z][a-z0-9_]{2,60}$/;
  const stateLabels = {selecting:"正在准备材料", prepared:"待整理", running:"调用结果待核对", received:"已收到，待解析",
    modelled:"待写入", failed:"整理失败", partial:"部分已保存", complete:"整理完成", empty:"没有可整理材料"};
  const jobLabels = {queued:"后台已登记", complete:"本次处理完成", blocked:"本次处理受阻"};
  const phases = {source_index:"整理来源索引",sources:"筛选来源",facts:"核对相关事实",ready:"准备完成"};
  /* 生命周期：后端 memory_atoms.state 的取值就是这四种归宿。
     颜色档位在 memory-page.css 的 .mem-state-* 里，四处共用（比例带/状态条/状态环/徽章）。 */
  const LIFECYCLE = [
    {key:"active",     label:"有效",   tone:"mem-state-active"},
    {key:"superseded", label:"被取代", tone:"mem-state-superseded"},
    {key:"retracted",  label:"已撤回", tone:"mem-state-retracted"},
    {key:"forgotten",  label:"已遗忘", tone:"mem-state-forgotten"},
  ];
  const retentionLabels = {session:"本次会话",short:"短期",durable:"长期",protected:"受保护"};
  const sourceLabels = {human_message:"对方消息",bot_message:"角色消息",source:"原始资料",observation:"观察记录"};
  const queryLabels = {fact:"事实",source:"原始来源",experience:"共同经历"};
  /* 整理流程的六个阶段。running 是「在途」，failed/empty 是另一类结局，
     所以它们不进这条线性轨道，而是单独标注。 */
  const STAGES = ["selecting","prepared","running","received","modelled","complete"];
  const formatErrors = new Set(["invalid_json","duplicate_json_field","nonfinite_json_number","invalid_shape",
    "invalid_summary","invalid_proposals","invalid_decisions","invalid_proposal","invalid_details",
    "invalid_qualifiers","invalid_item_id","duplicate_item_id","related_item_unresolved","invalid_decision",
    "invalid_anniversaries","unknown_evidence","unknown_target","ambiguous_target","unexpected_repair_item",
    "conflicting_target","overlapping_pending_decision","output_invalid","output_length","output_oversized",
    "missing_repair_item"]);
  const denied = new Set(["dashboard_identity_required", "core_access_required", "binding_revoked", "binding_rebound", "staging_host_terminated"]);
  const e = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const count = value => Number.isInteger(value) && value >= 0 ? String(value) : "尚未统计";
  const num = value => Number.isFinite(value) && value >= 0 ? String(value) : "—";
  const when = value => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString("zh-CN", {hour12:false}) : "待定";
  const day = value => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleDateString("zh-CN") : "待定";
  const reason = code => errors[code] || (formatErrors.has(code) ? "模型输出有未完成或格式不合规的条目，已有结果保留，可修复剩余项。" : code ? "当前状态未能确认，请刷新核对。" : "暂无具体原因");
  const badge = (text, tone="") => `<span class="mem-badge ${tone}">${e(text)}</span>`;
  const button = (id, text, disabled=false, kind="") => `<button type="button" id="${id}" class="mem-btn${kind?" is-"+kind:""}"${disabled?" disabled":""}>${text}</button>`;
  const problem = code => Object.assign(new Error(reason(code)), {code});
  /* 图标内联成字符串：这一页不引外部图标库，避免多一次请求与一个依赖。
     只在「去向确实不同」的地方用图标，不做装饰。 */
  const ICON = {
    reflections:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h16M4 12h10M4 18h7"/><circle cx="18" cy="17" r="3"/></svg>',
    facts:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M5 4h11l3 3v13H5z"/><path d="M8 9h8M8 13h8M8 17h5"/></svg>',
    query:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="6"/><path d="m20 20-4.3-4.3"/></svg>',
    archive:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="4" rx="1"/><path d="M5 8v11h14V8"/><path d="M10 12h4"/></svg>',
    migrate:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M4 19h16"/></svg>',
    clear:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M5 7h14M9 7V4h6v3"/><path d="M7 7v13h10V7"/><path d="M11 11v6M13 11v6"/></svg>',
    back:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="m14 6-6 6 6 6"/></svg>',
    refresh:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12a7 7 0 1 1-2.1-5"/><path d="M19 4v4h-4"/></svg>',
    search:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><circle cx="10.5" cy="10.5" r="5.5"/><path d="m17 17 3 3"/></svg>',
    info:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 8h.01"/></svg>',
    alert:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5v5M12 16h.01"/></svg>',
  };
  let root = null, bridge = null, version = 0, timer = null, data = null;

  function blank() {
    return {catalog:null, target:0, role:0, area:"facts", tab:"facts",
      /* 语料：一次预读多页，本地筛选。corpus 是全量（含已归档提示），
         filter 只影响渲染，不改动数据。 */
      corpus:[], cursors:[""], page:0, next:null, hasMore:false, overview:null, history:false,
      filter:"all", search:"", selId:null,
      detail:null, detailId:null, factDetail:null, revisions:[], clearPreview:null,
      queryText:"", queryKind:"all", queryResult:null, querySession:null, sourceResult:null,
      archivePreview:null, archiveIdleDays:180, archivedResult:null,
      exportBundle:null, exportIncludeArchived:true, importPreview:null,
      /* 记忆归属：null = 还没读到；{owner:"self"} 读到但没读全。归属是安装级的事实，
         所以它不进 load() 的轮询——那些轮询只刷数据，不该让这句话闪。 */
      ownership:null,
      addOpen:false, addText:"",
      pending:null, pendingMutation:null, busy:false, loading:false, notice:"", error:false, ready:false};
  }
  function current() { return data?.catalog?.targets[data.target]; }
  function role() { return current()?.roles[data.role]; }
  /* 桥要的是**插件相对路径**：目录给的是绝对地址
     `/astrbot_plugin_private_companion/host/targets/...`，交给桥之前
     要切掉插件目录名，让它变成 `host/targets/...`。
     真实 SDK（AstrBot 的 plugin_page_bridge.js）自己会把插件目录名拼回去。
     这一条是查出来的，不是猜的：`tools/check_product_page.cjs` 的合成 SDK
     按 `'/'+HOST+'/'+relative` 还原绝对地址，RPC 只接受裸路由名；
     `tests/demo_host_process.py` 同理。docs/CORE_HOST_MANAGEMENT_PAGE_20260924.md
     也写着「所有请求使用插件相对路径」。 */
  function endpoint() {
    const path = role()?.memory_api;
    if (typeof path !== "string" || !path.startsWith(PREFIX)) throw problem("invalid_response");
    const relative = path.slice(PREFIX.length);
    if (relative.split("/").some(part => !part || part === "." || part === "..") || /[?#%\\:]/.test(relative)) throw problem("invalid_response");
    return relative;
  }
  function storageKey() {
    return STORE + JSON.stringify([data.catalog.installation_id, data.catalog.operator_id, endpoint()]);
  }
  function restorePending() {
    try {
      const value = JSON.parse(localStorage.getItem(storageKey()));
      if (value && [value.batch_id, value.operation_key, value.tracked_batch_id].every(s => typeof s === "string" && s.length > 0 && s.length <= 256)
          && typeof value.uncertain === "boolean") return value;
    } catch { /* Storage is optional; no memory content is cached. */ }
    return null;
  }
  function savePending() {
    try {
      if (data.pending) localStorage.setItem(storageKey(), JSON.stringify(data.pending));
      else localStorage.removeItem(storageKey());
      return true;
    } catch { return false; }
  }
  function mutationStorageKey() { return storageKey() + ":mutation"; }
  function restoreMutation() {
    try {
      const value = JSON.parse(localStorage.getItem(mutationStorageKey()));
      if (value && typeof value.operation_key === "string" && value.operation_key.length <= 256
          && typeof value.kind === "string") return value;
    } catch { /* Storage is optional; never cache memory text here. */ }
    return null;
  }
  function saveMutation() {
    try {
      if (data.pendingMutation) {
        const marker = data.pendingMutation;
        const safe = {kind:marker.kind,operation_key:marker.operation_key};
        for (const key of ["atom_id","action","base_revision","view_token","revision_token"]) {
          if (marker[key] !== undefined) safe[key] = marker[key];
        }
        localStorage.setItem(mutationStorageKey(), JSON.stringify(safe));
      }
      else localStorage.removeItem(mutationStorageKey());
      return true;
    } catch { return false; }
  }
  async function bounded(promise) {
    let timeout;
    try {
      return await Promise.race([promise, new Promise((_, reject) => {
        timeout = setTimeout(() => reject(problem("bridge_timeout")), 15000);
      })]);
    } finally { clearTimeout(timeout); }
  }
  async function api(method, path, body) {
    const response = await bounded(method === "GET" ? bridge.apiGet(path, body) : bridge.apiPost(path, body));
    const wrapped = ["ok","error"].includes(response?.status) && response?.data !== undefined ? response : ["ok","error"].includes(response?.data?.status) && response?.data?.data !== undefined ? response.data : null;
    /* 后端把机器码放在信封的 message 上（页面桥只转发 message），所以取码要同时
       看 data.code 与 message，不能只认前者。 */
    if (wrapped?.status === "error") throw problem(CODE.test(wrapped.data?.code || wrapped.message || "")
      ? (wrapped.data?.code || wrapped.message) : "invalid_response");
    const value = wrapped ? wrapped.data : response;
    if (!value || typeof value !== "object") throw problem("invalid_response");
    return value;
  }
  function live(ticket) { return ticket === version && root?.isConnected; }
  function stopTimer() { clearTimeout(timer); timer = null; }
  function schedule() {
    stopTimer();
    /* 只在「整理与修复」里轮询：那是有后台任务的视图。事实流是被动数据，
       用户自己在看，8 秒刷一次只会把筛选与滚动位置晃掉。 */
    if (data?.ready && role()?.memory_api && !document.hidden && !data.addOpen && ["reflections","facts"].includes(data.area)) {
      // Poll only reads. Neither a timer nor a page reload can queue a model call.
      const quiet = data.area === "facts" && (data.search || data.filter !== "all");
      timer = setTimeout(() => { if (!data.busy && !data.loading && !quiet) load(); else schedule(); }, 8000);
    }
  }
  function notice(message, error=false) { data.notice = message; data.error = error; }
  function failure(error) {
    if (data.area === "reflections") { data.items = []; data.detail = null; }
    if (data.area === "facts") { data.corpus = []; data.factDetail = null; data.revisions = []; }
    data.queryResult = null; data.sourceResult = null; data.next = null;
    if (error.code === "memory_reflection_unavailable") data.detailId = null;
    notice(error.code ? reason(error.code) : "连接中断，已清空旧内容。请刷新核对。", true);
    if (denied.has(error.code)) {
      data.pending = null; savePending(); data.catalog = null; data.ready = false; stopTimer();
    }
  }
  /* 记忆归属的文案。
   *
   * 用户看到的是「记忆现在由谁对模型说」这件事，而不是我们能拿到多少字段，
   * 所以每一种判定都必须说清**谁在提供**以及**为什么轮到它**——「两边都没注入
   * 而没人说明」正是这句话要防的那个故障。名字一律取后端给的 display_name，
   * 页面不写死别家插件的名字：那边改了名，这里跟着改，不用发版。 */
  function ownerName(value, fallback) {
    const name = typeof value === "string" ? value.trim() : "";
    return (name || fallback).slice(0, 80);
  }
  function ownerNotice() {
    const value = data.ownership;
    if (!data.ready) return "";
    if (!value || typeof value.owner !== "string") return errors.memory_ownership_unavailable;
    const name = ownerName(value.display_name, "我会牢牢记住你");
    if (value.owner === "external") {
      return `长期记忆现在由〈${name}〉提供：每轮注入的记忆，以及模型能调用的记忆工具，都来自它——`
        + "那是外部的记忆插件，与这一页记下的事实和经历不是同一份库，两边各存各的。"
        + "这里的事实、来源和整理记录仍然照常保存，换回 V2 提供时可以直接接着用。";
    }
    const reason = {
      owner_module_absent: "它当前没有加载，",
      owner_declaration_absent: "它没有给出可用的记忆声明，",
      owner_handle_disabled: "它的记忆桥没有启用，",
      owner_handle_unreadable: "它的记忆句柄当前读不到，",
    }[value.reason] || "它的记忆声明当前不可用，";
    return `长期记忆现在由 V2 提供：注入与记忆工具都来自本插件。${reason}所以这一页管的是 V2 自己的事实与经历，`
      + `与〈${name}〉的库不是同一份——那是外部记忆插件，不是这里的记录。`
      + "它恢复可用后，注入与工具会立刻回到它那边。";
  }
  /* 徽标与面包屑。
   *
   * 外壳接手之后顶栏徽标由 product.updateHostChrome() 统一渲染，
   * 这里不再抢过去改写——它原先会写一个「设计预览」的文案，那正是
   * 「连不上」的观感来源。所以宿主徽标在场时直接返回。
   * 只有当这个工作区被单独嵌入（没有外壳徽标）时才自己补一条状态。
   *
   * 文案只说「V2 记忆库读取」：这句话读的是这一页连不连得上 V2 的事实库，
   * 不是「谁在给模型提供记忆」——后者由 ownerNotice() 按归属判定说，
   * 徽标抢过去说会变成一句不成立的独占声明。 */
  function setChrome(active) {
    if(window.CompanionProduct?.hosted()&&document.querySelector("[data-host-badge]"))return;
    document.body.classList.toggle("memory-connected",active);
    const label = document.querySelector(".preview-badge");
    if (label) label.innerHTML = active ? "<span></span>V2 记忆库读取正常 <b>V2</b>" : "<span></span>V2 记忆库待检查 <b>V2</b>";
    if (active) {
      const breadcrumb = document.querySelector("#breadcrumb strong");
      if (breadcrumb) breadcrumb.textContent = "事实与经历";
      document.title = "陪伴小屋";
    }
  }

  /* ================= 语料：本地派生 ================= */

  function stateOf(item) {
    const value = item?.state;
    return LIFECYCLE.some(entry => entry.key === value) ? value : "superseded";
  }
  function tally(items) {
    const out = {total:items.length};
    for (const entry of LIFECYCLE) out[entry.key] = 0;
    for (const item of items) out[stateOf(item)] += 1;
    return out;
  }
  /* 筛选与搜索。搜索覆盖表述、谓词、对象三个字段——用户记得的可能只是
     当时那句话里的任何一个词，不限制字段才找得到。 */
  function visibleFacts() {
    const needle = data.search.trim().toLowerCase();
    return data.corpus.filter(item => {
      if (data.filter !== "all" && stateOf(item) !== data.filter) return false;
      if (!needle) return true;
      return [item.representation, item.predicate, item.object, item.subject]
        .some(field => typeof field === "string" && field.toLowerCase().includes(needle));
    });
  }
  function selectedFact() {
    return data.corpus.find(item => item.atom_id === data.selId) || null;
  }

  /* ================= 视图：左轨道 ================= */

  const AREAS = [
    ["reflections","整理与修复",ICON.reflections,"整理批次、失败修复与后台接续"],
    ["facts","事实治理",ICON.facts,"已保存事实的更正、钉选、撤回与遗忘"],
    ["query","来源与经历",ICON.query,"按关键词检索事实、原始来源与共同经历"],
    ["archive","冷归档",ICON.archive,"把长期闲置的持久事实移出工作集，可随时移回"],
    ["migrate","迁移",ICON.migrate,"导出可审阅的记忆包，或校验后导入另一安装的包"],
    ["clear","范围清理",ICON.clear,"按范围逻辑遗忘当前私聊关系的事实"],
  ];
  function railMarkup() {
    const pendingBatches = data.area === "reflections" && data.items.length ? data.items.length : 0;
    return `<nav class="mem-rail" aria-label="事实与经历工作区">${AREAS.map(([id,label,icon,hint])=>{
      const working = id === "reflections" && pendingBatches > 0;
      return `<button id="memory-area-${id}" type="button" title="${e(hint)}"
        aria-pressed="${data.area===id}"${working?' class="is-working"':""}>
        <span class="mem-rail-icon">${icon}</span>
        <span class="mem-rail-label">${e(label)}</span>
        ${id==="reflections"?`<span class="mem-rail-count"${pendingBatches?"":" hidden"}>${num(pendingBatches)}</span>`:""}
      </button>`;
    }).join("")}</nav>`;
  }

  /* ================= 视图：页头 ================= */

  function headMarkup() {
    const target = current();
    /* 页头不放说明句。
       切到哪个视图，标题就是那件事，左侧轨道已经写了它是干什么的；
       再加一行「这里可以……每条都带……」是把导航重说一遍，占掉了第一屏。
       真正需要当场知道的规则（比如「这不是删除」）写在动作旁边，不写在这里。 */
    const AREA_TITLE = {
      reflections:"整理与修复", facts:"事实治理", query:"来源与经历",
      archive:"冷归档", migrate:"迁移", clear:"范围清理",
    };
    return `<header class="mem-head">
      <div class="mem-head-title"><h2>${e(AREA_TITLE[data.area])}</h2></div>
      <div class="mem-scope">
        <label>会话对象<select id="memory-target" ${!data.catalog?"disabled":""}>${
          data.catalog?.targets.map((t,i)=>`<option value="${i}" ${i===data.target?"selected":""}>${e((window.CompanionProduct?.targetLabel?.(t) || (t.scope_kind === "group"?"群聊 ":"私聊 ")+(t.display_name || (/^\d+$/.test(String(t.participant_id || t.group_id || "")) ? t.participant_id || t.group_id : "当前对象"))) + " · " + (window.CompanionProduct?.accountLabel?.(t) || t.account_name || "当前账号"))}</option>`).join("") || '<option>尚未读取目标</option>'
        }</select></label>
        <label>角色<select id="memory-role" ${!target?"disabled":""}>${
          target?.roles.map((r,i)=>`<option value="${i}" ${i===data.role?"selected":""}>${e(window.CompanionProduct?.roleLabel?.(r,i) || "当前角色")}</option>`).join("") || '<option>尚未选择角色</option>'
        }</select></label>
      </div>
    </header>`;
  }

  /* ================= 视图：事实治理（master-detail） ================= */

  /* 语料比例带：四种生命周期的占比条。宽度在渲染后用一次 requestAnimationFrame
     从 0 过渡到目标值，让「占比」这件事被看见一次，之后不再重播。 */
  function vitalsMarkup() {
    const stats = tally(data.corpus);
    const total = Math.max(1, stats.total);
    const seg = (key, tone) => stats[key]
      ? `<i class="mem-state-${key}" data-w="${(stats[key]/total*100).toFixed(2)}" style="background:currentColor;width:0"></i>`
      : "";
    return `<section class="mem-block">
      <div class="mem-block-head"><h3>当前语料</h3>${
        data.hasMore?``:""}</div>
      <div class="mem-block-body">
        <div class="mem-vitals">
          <div class="mem-vitals-figure"><b data-count="${stats.total}">0</b><span>条事实<br>共 ${num(stats.total)} 条</span></div>
          <div class="mem-vitals-rest">
            <div class="mem-band">${LIFECYCLE.map(entry=>seg(entry.key,entry.tone)).join("")}</div>
            <div class="mem-legend">${LIFECYCLE.map(entry=>
              `<span class="mem-state-${entry.key}"><em></em>${e(entry.label)} <b>${num(stats[entry.key])}</b></span>`).join("")}</div>
          </div>
        </div>
      </div>
    </section>`;
  }

  function overviewMarkup() {
    const overview = data.overview;
    if (!overview) return `<section class="mem-block"><div class="mem-block-body"><p class="mem-plain">${
      data.loading ? "正在读取全量统计与记忆联动状态…" : "全量统计暂不可用，下面仍显示当前读取到的事实。"}</p></div></section>`;
    const local = overview.local || {};
    const facts = local.facts || {};
    const experiences = local.experiences || {};
    const sources = local.sources || {};
    const reflections = local.reflections || {};
    const states = reflections.by_state || {};
    const countValue = value => Number.isInteger(value) && value >= 0 ? num(value) : "尚未统计";
    const historyCounts = ["complete", "empty", "failed", "partial", "running", "prepared", "received", "modelled", "selecting"]
      .filter(key => Number.isInteger(states[key]) && states[key] > 0)
      .map(key => `${stateLabels[key] || key} ${num(states[key])}`).join(" · ") || "尚无整理批次";
    const linkage = overview.linkage || {};
    let linkageTitle = "外部记忆摘要未接通";
    let linkageNote = "本地事实与经历仍由 V2 保存。";
    let linkageTone = "is-warn";
    if (linkage.state === "ready") {
      linkageTitle = `${ownerName(linkage.display_name, "记忆插件")}摘要已接通`;
      linkageNote = "按当前私聊对象只读分类计数，不读取或展示记忆正文。";
      linkageTone = "is-ok";
    } else if (linkage.state === "not_owner") {
      linkageTitle = "当前由 V2 提供长期记忆";
      linkageNote = "外部记忆插件没有接管当前角色，因此没有读取其摘要。";
    } else if (linkage.reason === "unsupported_scope") {
      linkageTitle = "当前对象不支持外部私聊摘要";
      linkageNote = "切换到已授权的私聊对象后可读取对应摘要。";
    } else if (linkage.reason) {
      linkageNote = `读取状态：${String(linkage.reason).replace(/_/g, " ")}。本地记忆不受影响。`;
    }
    const categoryLabels = {profile:"个人资料", preference:"偏好", relationship:"关系", private_conversation:"私聊内容", other:"其他"};
    const categories = Array.isArray(linkage.categories) ? linkage.categories : [];
    return `<section class="mem-block mem-overview-block">
      <div class="mem-block-head"><h3>当前记忆概况</h3><span class="mem-badge">当前对象 · 当前角色</span></div>
      <div class="mem-overview-grid">
        <div><span>已保存事实 / 全部事实</span><strong>${countValue(facts.active)} / ${countValue(facts.total)}</strong></div>
        <div><span>共同经历</span><strong>${countValue(experiences.active)} / ${countValue(experiences.total)}</strong></div>
        <div><span>可用对话消息</span><strong>${countValue(sources.available_messages)}</strong></div>
        <div><span>整理批次</span><strong>${countValue(reflections.total)}</strong><small>${e(historyCounts)}</small></div>
      </div>
      <section class="mem-linkage" aria-label="外部记忆联动">
        <div class="mem-linkage-head"><div><h4>记忆插件联动</h4><p>${e(linkageNote)}</p></div><span class="mem-badge ${linkageTone}">${e(linkageTitle)}</span></div>
        ${linkage.state === "ready" ? `<dl class="mem-linkage-counts">${categories.map(item =>
          `<div><dt>${e(categoryLabels[item.key] || "其他")}</dt><dd>${countValue(item.count)}</dd></div>`).join("")}
          <div><dt>合计</dt><dd>${countValue(linkage.counts?.total)}</dd></div></dl>` : ""}
      </section>
    </section>`;
  }

  function filtersMarkup() {
    const stats = tally(data.corpus);
    const chips = [["all","全部",stats.total],...LIFECYCLE.map(entry=>[entry.key,entry.label,stats[entry.key]])];
    return `<div class="mem-filters">
      ${chips.map(([key,label,n])=>`<button type="button" class="mem-chip" data-filter="${key}"
        aria-pressed="${data.filter===key}">${e(label)} <b>${num(n)}</b></button>`).join("")}
      <span class="mem-search">${ICON.search}<input id="memory-search" type="search"
        value="${e(data.search)}" placeholder="按表述、谓词或对象搜索" aria-label="搜索事实"></span>
    </div>`;
  }

  function corpusMarkup() {
    const list = visibleFacts();
    if (!list.length) {
      const empty = data.corpus.length
        ? {title:"没有匹配的事实",copy:"换一个状态筛选或清空搜索词再试。"}
        : {title:"当前范围暂无已保存事实",copy:"等她与这个对象积累一些对话，整理完成后事实会出现在这里。"};
      return `<div class="mem-empty"><h3>${e(empty.title)}</h3></div>`;
    }
    return list.map(item=>{
      const state = stateOf(item);
      const entry = LIFECYCLE.find(x=>x.key===state);
      const pinned = item.context_mode === "pinned";
      return `<button type="button" class="mem-card ${entry.tone}" data-fact-id="${e(item.atom_id)}"
        aria-current="${data.selId===item.atom_id}">
        <span class="mem-card-line">
          <span class="mem-pin"${pinned?"":' hidden'} title="核心记忆"></span>
          <strong>${e(item.predicate || "记忆记录")}</strong>
        </span>
        <span class="mem-card-q">${e(item.representation ?? "来源已不可读取，正文已隐藏。")}</span>
        <span class="mem-card-meta">
          <span>${e(entry.label)}</span>
          <span>v${e(item.revision)}</span>
          ${item.unavailable_reason?`<span>${e(reason(item.unavailable_reason))}</span>`:""}
        </span>
      </button>`;
    }).join("");
  }

  /* 状态环：conic-gradient 画置信度，环本身的颜色由 .mem-state-* 给出
     （生命周期）。两个维度各占一个通道，不互相借用。 */
  function ringMarkup(value) {
    const confidence = Number.isFinite(value.confidence) ? value.confidence : null;
    const pct = confidence === null ? 100 : Math.round(Math.max(0, Math.min(1, confidence)) * 100);
    const state = stateOf(value);
    return `<div class="mem-ring ${LIFECYCLE.find(x=>x.key===state).tone}"
      style="background:conic-gradient(currentColor ${pct*3.6}deg, var(--t-surface-sunken) 0)"
      title="${confidence===null?"没有可用置信度":"置信度 "+pct+"%"}"><i>${confidence===null?"—":pct+"%"}</i></div>`;
  }

  function stageTrackMarkup(state) {
    if (!STAGES.includes(state)) {
      const label = stateLabels[state] || "待核对";
      return `<ul class="mem-stages"><li class="is-failed" style="flex:1">${e(label)}</li></ul>`;
    }
    const now = STAGES.indexOf(state);
    return `<ul class="mem-stages">${STAGES.map((stage,index)=>{
      const cls = index < now ? "is-done" : index === now ? (state==="complete"?"is-done is-now":"is-now") : "";
      return `<li class="${cls}">${e(stateLabels[stage])}</li>`;
    }).join("")}</ul>`;
  }

  function sourceButtons(sources, sessionId) {
    if (!sources?.length) return `<p class="mem-plain">来源不可读取。</p>`;
    return `<div class="mem-results">${sources.map((source,index)=>
      `<button type="button" class="mem-source-link" data-source-handle="${e(source.handle)}" data-source-session="${e(sessionId)}">
        ${ICON.info}查看来源 ${index+1}${source.source_kind?` · ${e(sourceLabels[source.source_kind] || "其他来源")}`:""}${source.start!==undefined?` · ${e(source.start)}–${e(source.end)}`:""}
      </button>`).join("")}</div>`;
  }

  function factDetailMarkup() {
    const value = data.factDetail?.item;
    if (!value) {
      return `<div class="mem-detail-empty"><div class="mem-empty"><h3>选择一条事实</h3></div></div>`;
    }
    const state = stateOf(value);
    const entry = LIFECYCLE.find(x=>x.key===state);
    const active = value.state === "active";
    const pinned = value.context_mode === "pinned";
    return `<div class="mem-detail-top">
      <button type="button" class="mem-btn is-quiet mem-detail-close" id="memory-detail-close">${ICON.back}返回列表</button>
      ${ringMarkup(value)}
      <h3 id="memory-detail-heading" tabindex="-1">${e(value.predicate || "记忆记录")}</h3>
      <span class="mem-badge ${entry.tone}">${e(entry.label)}</span>
      ${pinned?badge("核心记忆","is-accent"):""}
    </div>
    <blockquote class="mem-quote">${e(value.representation || "来源不可读取，正文已隐藏。")}</blockquote>
    <dl class="mem-fields">
      <div><dt>对象</dt><dd>${e(value.object || "—")}</dd></div>
      <div><dt>版本</dt><dd class="is-mono">v${e(value.revision)}</dd></div>
      <div><dt>归属</dt><dd>${e(value.subject || "—")}</dd></div>
      <div><dt>保留方式</dt><dd>${e(retentionLabels[value.retention] || value.retention || "—")}</dd></div>
    </dl>
    ${data.sourceResult?`<blockquote class="mem-source-text"><small>原始来源 · 字符 ${e(data.sourceResult.start)}–${e(data.sourceResult.end)} / ${e(data.sourceResult.total_chars)}</small>${e(data.sourceResult.text)}${
      data.sourceResult.next_offset!==null?`<div style="margin-top:9px">${`<button type="button" class="mem-source-link" data-source-handle="${e(data.sourceResult.handle)}" data-source-session="${e(data.factDetail.session_id)}" data-source-start="${e(data.sourceResult.next_offset)}">继续查看后续</button>`}</div>`:""}</blockquote>`:""}
    <div class="mem-actions">
      ${active?button("memory-fact-pin",pinned?"取消置顶":"置顶为核心记忆",data.busy||data.loading):""}
      ${active?button("memory-fact-retract","撤回",data.busy||data.loading):""}
      ${active?button("memory-fact-forget","遗忘",data.busy||data.loading,"danger"):""}
      ${button("memory-revisions",data.revisions.length?`收起版本历史（${data.revisions.length}）`:"查看版本历史",data.busy||data.loading)}
    </div>
    ${active?`<form id="memory-correction-form" class="mem-form">
      <label>事实对象<input id="memory-correct-object" maxlength="8192" value="${e(value.object || "")}" required></label>
      <label>展示表述<textarea id="memory-correct-representation" maxlength="8192" rows="3" required>${e(value.representation || "")}</textarea></label>
      <div class="mem-form-actions"><button class="mem-btn is-primary" type="submit" ${data.busy||data.loading?"disabled":""}>保存为新版本</button></div>
    </form>`:""}
    <h4 class="mem-sub">原始来源</h4>
    ${sourceButtons(data.factDetail.sources, data.factDetail.session_id)}
    ${data.revisions.length?`<h4 class="mem-sub">版本历史<small>最新在上</small></h4>
      <ul class="mem-timeline">${[...data.revisions].reverse().map(row=>`<li>
        <time>第 ${e(row.revision)} 版</time>
        <p><span class="mem-timeline-op">${e(LIFECYCLE.find(entry=>entry.key===row.state)?.label || "历史版本")}</span>${e(row.representation || (row.unavailable_reason?reason(row.unavailable_reason):"该版本正文已按遗忘规则清除。"))}</p>
        ${row.object?`<p style="color:var(--t-text-faint);font-size:11px">对象：${e(row.object)}</p>`:""}
      </li>`).join("")}</ul>`:""}
    ${data.factDetail.sources?.length?`<p class="mem-plain">关联 ${data.factDetail.sources.length} 份原始来源</p>`:""}`;
  }

  function addFormMarkup() {
    return `<form class="mem-add-form" id="memory-add-form" aria-label="手动新增记忆">
      <label class="mem-add-field"><span>记忆内容（关于当前私聊对象）</span><textarea id="memory-add-text" rows="3" maxlength="2000"
        placeholder="例如：她喜欢在下雨天听旧歌。">${e(data.addText)}</textarea></label>
      <p class="mem-plain">以管理员身份记录，来源标注为手动记录，不会伪装成对方说过的话；角色自己的设定请到角色档案维护。</p>
      <div class="mem-actions">${button("memory-add-submit","保存记忆",data.busy||data.loading,"primary")}${button("memory-add-cancel","取消",data.busy||data.loading)}</div>
    </form>`;
  }

  function factsMarkup() {
    return `${overviewMarkup()}${vitalsMarkup()}
      <section class="mem-block">
        <div class="mem-add-row">${button("memory-add-open","手动新增一条记忆",data.busy||data.loading)}</div>
        ${data.addOpen?addFormMarkup():""}
        <div class="mem-split">
          <div>
            ${filtersMarkup()}
            <div class="mem-corpus" role="listbox" aria-label="事实列表">${
              data.loading && !data.corpus.length
                ? `<div class="mem-skeleton"><i></i><i></i><i></i><i></i></div>`
                : corpusMarkup()}</div>
          </div>
          <section class="mem-detail${data.selId?" is-open":""}" aria-label="事实详情">${factDetailMarkup()}</section>
        </div>
      </section>`;
  }

  /* ================= 视图：整理与修复 ================= */

  function detailMarkup() {
    const item = data.detail;
    if (!item) {
      return `<div class="mem-detail-empty"><div class="mem-empty"><h3>选择一个批次</h3></div></div>`;
    }
    const job = item.continuation;
    const receipts = Object.entries(item.receipts || {});
    const issues = [...(item.issues || []), ...(item.unresolved_items || [])];
    const canQueue = (item.resumable || item.retryable) && job?.state !== "queued" && !data.pending?.uncertain;
    let action = item.retryable ? "修复未完成项" : "继续整理";
    if (job?.state === "blocked") action = item.retryable ? "再次修复未完成项" : "再次继续整理";
    return `<div class="mem-detail-top">
      <button type="button" class="mem-btn is-quiet mem-detail-close" id="memory-detail-close">${ICON.back}返回列表</button>
      <h3 id="memory-detail-heading" tabindex="-1">${e(item.state_label || stateLabels[item.state] || "待核对")}</h3>
      <span class="mem-badge">${e(jobLabels[job?.state] || "尚未登记后台处理")}</span>
    </div>
    ${stageTrackMarkup(item.state)}
    <p class="mem-quote" style="margin-top:17px">${e(item.summary ?? (
      item.state === "failed" && item.captured
        ? "模型原始输出已保留，但未通过校验，尚未写入记忆；请查看下方原因并修复未完成项。"
        : item.state === "partial"
          ? "部分条目已写入记忆，未完成项可在下方单独修复。"
          : item.captured
            ? "模型结果已保存，正在解析或等待写入。"
            : item.state === "failed"
              ? "本次整理未完成，没有可复用的模型结果；可明确发起修复。"
              : "等待本批次的整理结果。"))}</p>
    <dl class="mem-fields">
      <div><dt>已处理项</dt><dd class="is-mono">${num(receipts.length)}</dd></div>
      <div><dt>剩余来源</dt><dd class="is-mono">${e(count(item.remaining_sources))}</dd></div>
      <div><dt>暂缓来源</dt><dd class="is-mono">${e(count(item.deferred_sources))}</dd></div>
      <div><dt>不可用来源</dt><dd class="is-mono">${e(count(item.unavailable_sources))}</dd></div>
    </dl>
    ${item.selection?.phase ? `<p class="mem-plain">材料准备：${e(phases[item.selection.phase] || "正在准备")} · 已检查 ${e(count(item.selection.examined))} 项</p>` : ""}
    ${item.continuation_needed ? '<p class="mem-plain">仍有后续材料待处理。</p>' : ""}
    ${item.state === "running" ? `<p class="mem-notice is-error" style="margin-top:15px">${ICON.alert}<span>${e(reason("memory_reflection_uncertain"))}</span></p>` : ""}
    ${job ? `<div class="mem-job"><strong>${e(jobLabels[job.state] || "待核对")}</strong>
      <p>更新于 ${e(when(job.updated_at))}</p>
      ${job.state === "queued"?`<p>下次检查：${e(when(job.next_at))}</p>`:""}
      ${job.result_code?`<p>${e(reason(job.result_code))}</p>`:""}</div>` : ""}
    ${item.selection?.blocked_code ? `<p class="mem-notice is-error" style="margin-top:15px">${ICON.alert}<span>${e(reason(item.selection.blocked_code))}</span></p>` : ""}
    ${issues.length ? `<h4 class="mem-sub">待处理原因<small>${issues.length} 项</small></h4>
      <ul class="mem-list">${issues.map((issue, index)=>`<li>待处理项 ${index + 1}：${e(reason(issue.code))}</li>`).join("")}</ul>` : ""}
    <div class="mem-actions">
      ${item.resumable || item.retryable ? button("memory-queue",data.busy?"正在提交…":action,!canQueue || data.busy || data.loading,"primary") : ""}
      ${item.repair_batch?button("memory-repair-link","查看后续修复",data.busy || data.loading):""}
      ${button("memory-detail-refresh","刷新状态",data.busy || data.loading)}
    </div>
    ${canQueue ? '<p class="mem-plain">继续或修复可能调用已配置的记忆模型；仅处理当前对象与角色的这批材料。</p>' : ""}
    ${receipts.length ? `<h4 class="mem-sub">处理确认<small>${receipts.length} 项</small></h4>
      <ul class="mem-list">${receipts.map(([,r], index)=>`<li>处理记录 ${index + 1}：${e(typeof r === "object" && r ? (r.code ? reason(r.code) : stateLabels[r.state] || jobLabels[r.state] || "已有确认") : "已有确认")}</li>`).join("")}</ul>` : ""}
    ${item.retry_of ? '<p class="mem-plain">这批材料来自一次明确的修复操作。</p>' : ""}`;
  }

  function reflectionsMarkup() {
    const list = data.items;
    const byState = data.overview?.local?.reflections?.by_state || {};
    const pendingCount = Object.entries(byState).reduce((sum, [key, value]) =>
      sum + (["complete", "empty"].includes(key) ? 0 : Number(value) || 0), 0);
    return `<section class="mem-block">
      <div class="mem-reflection-views" role="group" aria-label="整理批次范围">
        <button type="button" data-reflection-view="pending" aria-pressed="${!data.history}">待处理 <b>${num(pendingCount)}</b></button>
        <button type="button" data-reflection-view="history" aria-pressed="${data.history}">全部批次 <b>${num(data.overview?.local?.reflections?.total || 0)}</b></button>
      </div>
      <div class="mem-split">
        <div>
          <div class="mem-filters" style="justify-content:space-between">
            <span style="font-size:11.5px;color:var(--t-text-faint)">本页 ${num(list.length)} 批${
              data.corpus.length?` · 语料 ${num(data.corpus.length)} 条事实`:""}</span>
            <span style="display:flex;gap:7px">${button("memory-previous","上一页",data.page===0 || data.busy || data.loading)}
            <span style="font-size:11.5px;color:var(--t-text-faint);align-self:center">第 ${data.page+1} 页</span>
            ${button("memory-next","下一页",!data.next || data.busy || data.loading)}</span>
          </div>
          <div class="mem-corpus" role="listbox" aria-label="整理批次">${
            data.loading && !list.length
              ? `<div class="mem-skeleton"><i></i><i></i><i></i></div>`
              : list.length
                ? list.map((item,i)=>{
                    const job = jobLabels[item.continuation?.state] || (item.retryable?"可修复":"待接续");
                    const failing = item.state === "failed" || item.retryable;
                    return `<button type="button" class="mem-batch" data-batch-index="${i}"
                      aria-current="${item.batch_id===data.detailId}">
                      <span class="mem-batch-top"><strong>${e(item.state_label || stateLabels[item.state] || "待核对")}</strong>
                        <span class="mem-badge${failing?" is-risk":""}">${e(job)}</span>
                        ${item.self_reference?`<span class="mem-badge">${e(item.self_reference)}</span>`:""}</span>
                      <span>${e(item.summary ?? "摘要尚不可读取或仍待整理。")}</span>
                      <small>已处理 ${num(Object.keys(item.receipts || {}).length)} 项 · 剩余来源 ${e(count(item.remaining_sources))}</small>
                    </button>`;
                  }).join("")
                : `<div class="mem-empty"><h3>${data.history ? "当前范围还没有整理记录" : "没有待处理的整理批次"}</h3></div>`}</div>
        </div>
        <section class="mem-detail${data.detailId?" is-open":""}" aria-label="整理详情">${detailMarkup()}</section>
      </div>
    </section>`;
  }

  /* ================= 视图：来源与经历 ================= */

  function queryMarkup() {
    const result = data.queryResult;
    return `<section class="mem-block">
      <div class="mem-block-head"><h3>检索</h3></div>
      <div class="mem-block-body">
        <form id="memory-query-form" class="mem-query-form">
          <label>查询内容<input id="memory-query-text" maxlength="2000" value="${e(data.queryText)}" placeholder="输入关键词或短语" required></label>
          <label>资料类型<select id="memory-query-kind">${[["facts","事实"],["sources","原始来源"],["experiences","共同经历"],["all","混合"]].map(([key,label])=>`<option value="${key}" ${key===data.queryKind?"selected":""}>${label}</option>`).join("")}</select></label>
          <button type="submit" class="mem-btn is-primary" ${data.busy||data.loading?"disabled":""}>开始查询</button>
        </form>
        ${result?`<div class="mem-filters" style="padding:0;border:0;margin-top:15px">
          <span class="mem-badge${result.index_state==="ready"?" is-ok":" is-warn"}">${e(result.index_state==="ready"?"索引就绪":result.index_state==="ranking"?"正在排序":"索引准备中")}</span>
          <span style="font-size:11px;color:var(--t-text-faint)">检查 ${num(result.candidates_examined)} 项 · ${result.complete?"查询完成":"仍有后续材料"}${result.unavailable_sources?" · 部分来源不可用":""}</span>
        </div>
        <ul class="mem-results">${(result.items||[]).length?(result.items||[]).map((item,index)=>{
          const sources = item.kind==="source"&&item.handle ? [{handle:item.handle,source_kind:item.source_kind}]
            : item.kind==="experience" ? (item.statements||[]).flatMap(statement=>(statement.sources||[]).map(handle=>({handle})))
            : (item.sources||[]).map(handle=>({handle}));
          return `<li class="mem-result" style="animation-delay:${Math.min(index*24,180)}ms">
            <div class="mem-result-head">
              <span class="mem-badge">${e(queryLabels[item.kind] || "记录")}</span>
              ${item.revision?`<span class="mem-badge mem-mono">v${e(item.revision)}</span>`:""}
              ${item.message_time?`<span style="font-size:10.5px;color:var(--t-text-faint)">${e(when(item.message_time))}</span>`:""}
            </div>
            <h4>${e(item.predicate||item.title||sourceLabels[item.source_kind]||"查询结果")}</h4>
            <p>${e(item.representation||item.excerpt||item.text||item.statements?.map(s=>s.text).join("；")||"内容不可读取")}</p>
            ${sourceButtons(sources,data.querySession)}
          </li>`;
        }).join(""):`<li class="mem-empty"><h3>${e(result.index_state!=="ready"?"索引正在准备":"没有找到匹配记录")}</h3>
          <p>${e(result.index_state!=="ready"?"稍后重试，或换一个更具体的词。":"换一个词，或把资料类型改成「混合」再试一次。")}</p></li>`}</ul>
        ${result.next_cursor?`<div style="margin-top:13px">${button("memory-query-next","继续读取下一页",data.busy||data.loading)}</div>`:""}`:""}
        ${data.sourceResult?`<blockquote class="mem-source-text"><small>原始来源 · 字符 ${e(data.sourceResult.start)}–${e(data.sourceResult.end)} / ${e(data.sourceResult.total_chars)}</small>${e(data.sourceResult.text)}${
          data.sourceResult.next_offset!==null?`<div style="margin-top:9px">${`<button type="button" class="mem-source-link" data-source-handle="${e(data.sourceResult.handle)}" data-source-session="${e(data.querySession)}" data-source-start="${e(data.sourceResult.next_offset)}">继续查看后续</button>`}</div>`:""}</blockquote>`:""}
      </div>
    </section>`;
  }

  /* ================= 视图：冷归档 ================= */

  function archiveMarkup() {
    const snapshot = data.archivePreview;
    const label = (item, index) => item.unavailable_reason ? `记忆记录 ${index + 1}`
      : item.predicate || data.corpus.find(row=>row.atom_id===item.atom_id)?.predicate || `记忆记录 ${index + 1}`;
    const archived = snapshot?.archived_items || [];
    return `<section class="mem-block">
      <div class="mem-block-head"><h3>归档预览</h3></div>
      <div class="mem-block-body">
        <form id="memory-archive-form" class="mem-query-form">
          <label>闲置天数<input id="memory-archive-idle" type="number" min="30" max="3650" value="${e(data.archiveIdleDays)}"></label>
          <span></span>
          <button class="mem-btn" type="submit" ${data.busy||data.loading?"disabled":""}>刷新归档预览</button>
        </form>
        ${snapshot?`<div class="mem-preview" style="margin-top:15px">
          <dl class="mem-preview-facts">
            <div><dt>可归档</dt><dd>${num(snapshot.counts?.eligible)}</dd></div>
            <div><dt>已归档</dt><dd>${num(snapshot.counts?.already_archived)}</dd></div>
            <div><dt>保留</dt><dd>${num(snapshot.counts?.retained)}</dd></div>
          </dl>
          <p class="mem-plain" style="margin:0">当前关系的持久事实 · 闲置 ${e(snapshot.idle_days)} 天 · 保留原始内容</p>
          ${(snapshot.items||[]).length?`<ul class="mem-list">${(snapshot.items||[]).map((item, index)=>`
            <li><strong>${e(label(item, index))}</strong>
              <p>${e(item.representation || (item.unavailable_reason ? reason(item.unavailable_reason) : ""))}</p>
              <small>闲置自 ${e(day(item.idle_since))}</small></li>`).join("")}</ul>
            <div class="mem-preview-actions">
              ${button("memory-archive-confirm","归档全部可归档条目",data.busy||data.loading,"primary")}
            </div>`
          :`<p class="mem-empty">当前条件下无可归档事实。</p>`}
        </div>`:`<p class="mem-empty">尚未预览归档范围。</p>`}
        ${data.archivedResult?`<p class="mem-notice is-ok" style="margin-top:13px">${ICON.info}<span>已归档 ${num(data.archivedResult.archived_count)} 条</span></p>`:""}
      </div>
    </section>
    <section class="mem-block">
      <div class="mem-block-head"><h3>移回工作集</h3></div>
      <div class="mem-block-body">
        <form id="memory-archive-restore-form" class="mem-form" style="margin:0;padding:0;border:0">
          ${archived.length?`<ul class="mem-pick">${archived.map((item, index)=>`<li><label>
            <input type="checkbox" data-archive-restore="${e(item.atom_id)}" ${data.busy||data.loading?"disabled":""}>
            <span><strong>${e(label(item, index))}</strong><small>${e(item.representation || (item.unavailable_reason ? reason(item.unavailable_reason) : "已归档"))}</small></span>
          </label></li>`).join("")}</ul>`:`<p class="mem-empty">${snapshot ? "当前没有已归档的事实。" : "正在读取已归档事实…"}</p>`}
          <div class="mem-form-actions">${button("memory-archive-restore-submit","移回选中记录",data.busy||data.loading||!archived.length,"primary")}</div>
        </form>
      </div>
    </section>`;
  }

  /* ================= 视图：迁移 ================= */

  function exportMarkup() {
    const bundle = data.exportBundle;
    return `<section class="mem-block">
      <div class="mem-block-head"><h3>导出记忆包</h3></div>
      <div class="mem-block-body">
        <label style="display:inline-flex;align-items:center;gap:9px;font-size:12px;color:var(--t-text-soft)">
          <input type="checkbox" id="memory-export-include" ${data.exportIncludeArchived?"checked":""} style="accent-color:var(--t-accent-solid)">
          包含已归档条目
        </label>
        <div class="mem-actions" style="border:0;padding-top:13px">${button("memory-export-run","生成记忆包",data.busy||data.loading,"primary")}</div>
        ${bundle?`<div class="mem-preview" style="margin-top:15px">
          <dl class="mem-preview-facts">
            <div><dt>条目</dt><dd>${num(bundle.atom_count)}</dd></div>
            <div><dt>含归档</dt><dd>${bundle.includes_archived?"是":"否"}</dd></div>
          </dl>
          <p class="mem-plain">导出时间 ${e(when(bundle.bundle?.exported_at))}</p>
          <div class="mem-preview-actions">
            ${button("memory-export-copy","复制 JSON",data.busy||data.loading)}
            ${button("memory-export-download","下载文件",data.busy||data.loading)}
          </div>
        </div>`:""}
      </div>
    </section>`;
  }

  function importMarkup() {
    const preview = data.importPreview;
    return `<section class="mem-block">
      <div class="mem-block-head"><h3>导入记忆包</h3></div>
      <div class="mem-block-body">
        <form id="memory-import-form" class="mem-form" style="margin:0;padding:0;border:0">
          <label>记忆包 JSON<textarea id="memory-import-text" rows="6" placeholder="粘贴记忆包 JSON"></textarea></label>
          <div class="mem-form-actions">${button("memory-import-preview","校验记忆包",data.busy||data.loading)}</div>
        </form>
        ${preview?`<div class="mem-preview" style="margin-top:15px">
          <dl class="mem-preview-facts">
            <div><dt>包内条目</dt><dd>${num(preview.counts?.in_bundle)}</dd></div>
            <div><dt>将新增</dt><dd>${num(preview.counts?.new)}</dd></div>
            <div><dt>已存在</dt><dd>${num(preview.counts?.already_present)}</dd></div>
          </dl>
          <p class="mem-plain" style="margin:0">每条导入事实都会保留记忆包引用。</p>
          ${(preview.items||[]).length?`<ul class="mem-list" style="max-height:230px;overflow-y:auto">
            ${(preview.items||[]).slice(0,40).map((item, index)=>`<li>${e(item.decision==="new"?"新增":"已存在")} · ${e(item.predicate||`记忆记录 ${index + 1}`)}</li>`).join("")}
            ${(preview.items||[]).length>40?`<li>另有 ${num((preview.items||[]).length-40)} 条未列出</li>`:""}</ul>`:""}
          <div class="mem-preview-actions">
            ${button("memory-import-confirm","确认导入",data.busy||data.loading,"primary")}
            ${button("memory-import-discard","放弃这次校验",data.busy||data.loading)}
          </div>
        </div>`:`<p class="mem-empty">尚未校验记忆包。</p>`}
      </div>
    </section>`;
  }

  /* ================= 视图：范围清理 ================= */

  function clearMarkup() {
    const snapshot = data.clearPreview;
    const active = Number(snapshot?.counts?.active) || 0;
    return `<section class="mem-block">
      <div class="mem-block-head"><h3>清理当前对象的记忆</h3></div>
      <div class="mem-block-body">
        ${snapshot?`<div class="mem-preview">
          <dl class="mem-preview-facts">
            <div><dt>仍有效</dt><dd>${num(active)}</dd></div>
            <div><dt>已失效</dt><dd>${num(snapshot.counts?.already_inactive)}</dd></div>
          </dl>
          <p class="mem-plain" style="margin:0">当前私聊关系的事实与版本记录 · 来源原文不会由本操作物理删除。</p>
          <p class="mem-token">revision_token ${e(String(snapshot.revision_token||"").slice(0,16))}…</p>
          <div class="mem-preview-actions">
            ${active
              ? button("memory-clear-confirm",`确认遗忘这 ${active} 条`,"danger")
              : `<span class="mem-plain" style="margin:0">当前范围没有仍有效的事实，无需清理。</span>`}
          </div>
        </div>`:`<p class="mem-empty">尚未预览清理范围。</p>`}
        <div class="mem-actions" style="border:0;padding-top:13px">${button("memory-clear-preview","刷新范围预览",data.busy||data.loading)}</div>
      </div>
    </section>`;
  }

  /* ================= 渲染 ================= */

  const AREA_VIEW = {reflections:reflectionsMarkup, facts:factsMarkup, query:queryMarkup,
    archive:archiveMarkup, migrate:()=>`${exportMarkup()}${importMarkup()}`, clear:clearMarkup};

  function render() {
    if (!root?.isConnected || !data) return;
    /* 焦点与滚动位置跨重绘保留：这一页每次状态变化都整块重建 innerHTML，
       不做这一步的话，用户点了「下一页」焦点就掉到 body，
       键盘与读屏都要重新找位置。 */
    const active = document.activeElement;
    const focused = root.contains(active) && active?.id ? active.id : null;
    const corpus = root.querySelector(".mem-corpus");
    const scroll = corpus?.scrollTop || 0;
    const target = current();
    const ownership = ownerNotice();
    root.innerHTML = `<div class="mem-app">
      ${railMarkup()}
      <div class="mem-main">
        <div class="mem-progress"${data.loading?"":" hidden"} role="progressbar" aria-label="正在读取…"><i></i></div>
        ${headMarkup()}
        ${ownership?`<p class="mem-ownership" role="status">${e(ownership)}</p>`:""}
        ${data.notice?`<div id="memory-notice" class="mem-notice${data.error?" is-error":""}" role="status" aria-live="polite">${data.error?ICON.alert:ICON.info}<span>${e(data.notice)}</span></div>`:""}
        ${data.pending ? `<div class="mem-recovery"><span>${data.pending.uncertain?"上次提交结果未确认。":"保留了上次提交的位置，可继续查看后台结果。"}</span>${button("memory-recover",data.pending.uncertain?"核对上次提交":"查看上次提交",data.busy || data.loading)}</div>` : ""}
        ${data.pendingMutation ? `<div class="mem-recovery"><span>${data.pendingMutation.kind==="clear"?"上次范围清理结果未确认，请核对。":"上次记忆变更结果未确认，请刷新核对。"}</span>${button("memory-pending-refresh","刷新核对",data.busy||data.loading)}${button("memory-pending-dismiss","已核对，关闭提示",data.busy||data.loading)}</div>` : ""}
        ${!data.ready
          ? `<section class="mem-block"><div class="mem-skeleton"><i></i><i></i><i></i></div>
             <p class="mem-empty">${data.loading?"正在读取授权目标…":"刷新目标后即可查看记录。"}</p></section>`
          : !role()?.memory_api
            ? `<section class="mem-block"><div class="mem-empty"><h3>群聊记忆独立管理</h3>
               <p>请选择有记忆权限的私聊对象。</p></div></section>`
            : AREA_VIEW[data.area]()}
      </div>
    </div>`;
    if (corpus) { const next = root.querySelector(".mem-corpus"); if (next) next.scrollTop = scroll; }
    if (focused) root.querySelector("#"+CSS.escape(focused))?.focus({preventScroll:true});
    animate();
  }

  /* 动效编排。
     每次重绘都会跑，所以只做「本帧新出现的东西」：
     比例带从 0 展开、数字滚动到终值。两者都是幂等的，
     重复进入同一视图只会重播一次计数，不会累积。 */
  function animate() {
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
    const band = root?.querySelector(".mem-band");
    if (band) {
      const bars = [...band.querySelectorAll("i")];
      const paint = () => bars.forEach(bar => { bar.style.width = bar.dataset.w + "%"; });
      if (reduce) paint(); else requestAnimationFrame(paint);
    }
    /* 数字滚动：只在这个元素确实带 data-count 时跑，72 0ms 内到位。
       用 requestAnimationFrame 而非 CSS，因为终点值来自数据不是样式。 */
    for (const el of root?.querySelectorAll("[data-count]") || []) {
      const final = Number(el.dataset.count) || 0;
      if (reduce || final === 0) { el.textContent = String(final); continue; }
      const started = performance.now(), span = Math.min(720, 180 + final * 3);
      const step = now => {
        if (!el.isConnected) return;
        const t = Math.min(1, (now - started) / span);
        el.textContent = String(Math.round(final * (1 - Math.pow(1 - t, 3))));
        if (t < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    }
  }

  /* ================= 数据层 ================= */

  async function directory() {
    const ticket = ++version;
    stopTimer();
    data = blank(); data.loading = true; render();
    try {
      /* 归属是独立的一次读，而且**先读**：它决定这一页顶上那句话，读不到也要
         有个兜底口径，所以不能让它跟着后面 `page/targets` 的失败一起丢掉——
         目录失败时页面确实没有目标，但「记忆由谁提供」仍然该说出来。
         它自己 catch，绝不改 data.error / data.notice。 */
      const [, catalog] = await Promise.all([
        loadOwnership(),
        window.CompanionProduct?.directory?.() || api("GET", "page/targets"),
      ]);
      if (!live(ticket)) return;
      if (!Array.isArray(catalog.targets) || !catalog.installation_id || !catalog.operator_id
          || catalog.targets.some(t=>!Array.isArray(t.roles))) throw problem("invalid_response");
      data.catalog = catalog; data.ready = true; data.loading = false;
      const selected=window.CompanionProduct?.selection();
      if(selected){const index=catalog.targets.findIndex(t=>t.target_id===selected.target_id);if(index>=0){data.target=index;data.role=Math.max(0,catalog.targets[index].roles.findIndex(r=>r.role_id===selected.role_id));}}
      if (role()?.memory_api) {
        endpoint(); data.pending = restorePending(); data.pendingMutation = restoreMutation(); data.detailId = data.pending?.tracked_batch_id || null;
        await load();
      } else render();
    } catch (error) {
      if (live(ticket)) { failure(error); data.loading = false; render(); }
    }
  }

  /* 记忆归属只读一次：它是安装级的事实（谁在给模型提供记忆），不是随目标变化的
     数据，所以不跟 load() 的 8 秒轮询刷新。读不到时留空，由 ownerNotice() 说出
     「归属暂时读不出来，按 V2 自己提供处理」——不静默、也不假装知道。 */
  async function loadOwnership() {
    try {
      const value = await api("GET", "page/ownership");
      if (root && value && typeof value.owner === "string") data.ownership = value;
    } catch (error) { /* 归属读不到不影响这一页的事实管理；提示里已经写了兜底口径。 */ }
  }

  /* 事实预读：连着翻最多 PRELOAD_PAGES 页。任何一页失败都保留已读到的部分
     并在通知里说明——半份语料比空屏有用，但必须说清它不是全部。 */
  async function preloadFacts(ticket) {
    const path = endpoint();
    const collected = [];
    let cursor = "", more = false, failed = null;
    for (let i = 0; i < PRELOAD_PAGES; i++) {
      let page;
      try { page = await api("GET", path, {limit:PAGE_SIZE, ...(cursor?{after_id:cursor}:{})}); }
      catch (error) { failed = error; break; }
      if (!live(ticket)) return null;
      if (!Array.isArray(page.items)) throw problem("invalid_response");
      collected.push(...page.items);
      cursor = page.next_cursor || "";
      more = Boolean(page.next_cursor);
      if (!more) break;
    }
    return {items:collected, next:cursor || null, hasMore:more, failed};
  }

  async function load() {
    if (!root || !data.ready || !role()?.memory_api || data.busy) return;
    const ticket = ++version;
    stopTimer(); data.loading = true; render();
    try {
      const path = endpoint();
      if (data.area === "facts") {
        const [preloadResult, overviewResult] = await Promise.allSettled([
          preloadFacts(ticket), api("GET", path + "/overview"),
        ]);
        if (!live(ticket)) return;
        if (preloadResult.status === "rejected") throw preloadResult.reason;
        const preload = preloadResult.value;
        data.corpus = preload.items; data.next = preload.next; data.hasMore = preload.hasMore;
        data.overview = overviewResult.status === "fulfilled" ? overviewResult.value : null;
        /* 已打开的事实如果被改过版本就关掉详情，避免显示过期内容。 */
        if (data.selId && !data.corpus.some(item=>item.atom_id===data.selId)) {
          data.selId = null; data.factDetail = null; data.revisions = []; data.sourceResult = null;
        } else if (data.factDetail && data.factDetail.item) {
          const newest = data.corpus.find(item=>item.atom_id===data.factDetail.item.atom_id);
          if (!newest || newest.revision !== data.factDetail.item.revision) {
            data.factDetail = null; data.revisions = []; data.sourceResult = null;
          }
        }
        if (preload.failed) notice(`只读到了最近 ${preload.items.length} 条事实，更早的部分读取失败：${reason(preload.failed.code)}`, true);
        else if (overviewResult.status === "rejected") notice("事实列表已读取；全量统计与外部记忆联动暂不可用。", true);
        else notice("");
      } else if (data.area === "reflections") {
        const query = {limit:20, history:data.history ? "1" : "0"};
        if (data.cursors[data.page]) query.after_id = data.cursors[data.page];
        const [listedResult, detailResult, overviewResult] = await Promise.allSettled([
          api("GET", path + "/reflections", query),
          data.detailId ? api("GET", path + "/reflections", {batch_id:data.detailId}) : null,
          api("GET", path + "/overview"),
        ]);
        if (!live(ticket)) return;
        if (listedResult.status === "rejected") throw listedResult.reason;
        const listed = listedResult.value;
        if (data.detailId && detailResult.status === "rejected") throw detailResult.reason;
        const detail = detailResult.status === "fulfilled" ? detailResult.value : null;
        if (!Array.isArray(listed.items) || (detail && detail.batch_id !== data.detailId)) throw problem("invalid_response");
        data.items = listed.items; data.next = listed.next_cursor || null;
        data.overview = overviewResult.status === "fulfilled" ? overviewResult.value : null;
        data.detail = detail || listed.items[0] || null;
        data.detailId = data.detail?.batch_id || null;
        notice(overviewResult.status === "rejected" ? "整理批次已读取；全量状态统计暂不可用。" : "",
          overviewResult.status === "rejected");
      } else {
        const query = {limit:20};
        if (data.cursors[data.page]) query.after_id = data.cursors[data.page];
        const listed = await api("GET", path, query);
        if (!live(ticket)) return;
        if (!Array.isArray(listed.items)) throw problem("invalid_response");
        data.corpus = listed.items; data.next = listed.next_cursor || null; data.hasMore = Boolean(listed.next_cursor);
        notice("");
      }
    } catch (error) { if (live(ticket)) failure(error); }
    finally { if (live(ticket)) { data.loading = false; render(); schedule(); } }
  }

  /* 继续读取更早的事实（超出预读窗口时用）。追加而非替换，
     这样筛选与滚动位置都还在。 */
  async function loadMoreFacts() {
    if (!data.next || data.busy || data.loading) return;
    const ticket = ++version; data.loading = true; render();
    try {
      const page = await api("GET", endpoint(), {limit:PAGE_SIZE, after_id:data.next});
      if (!live(ticket)) return;
      if (!Array.isArray(page.items)) throw problem("invalid_response");
      const seen = new Set(data.corpus.map(item=>item.atom_id));
      data.corpus = [...data.corpus, ...page.items.filter(item=>!seen.has(item.atom_id))];
      data.next = page.next_cursor || null; data.hasMore = Boolean(page.next_cursor);
      notice("");
    } catch (error) { if (live(ticket)) failure(error); }
    finally { if (live(ticket)) { data.loading = false; render(); schedule(); } }
  }

  /* 手动新增：管理员明确登记一条事实，来源标成 management.memory，
     不冒充用户说过的话，也不覆盖已有事实。 */
  async function addMemory() {
    if (data.busy || data.loading) return;
    const body = (root.querySelector("#memory-add-text")?.value || "").trim();
    data.addText = body;
    if (!body) { notice("先写点要记住的内容。", true); render(); return; }
    data.busy = true; render();
    try {
      const result = await api("POST", endpoint() + "/add", {
        operation_key: "memory-add-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10),
        subject: "self", predicate: "手动记录", object: body, representation: body,
      });
      data.addOpen = false; data.addText = "";
      notice(`已新增一条记忆${Number.isInteger(result?.revision) ? "（修订 " + result.revision + "）" : ""}。`);
      data.busy = false;
      await load();
      return;
    } catch (error) { notice(reason(error.code), true); }
    data.busy = false; render();
  }

  /* 条目移出的可见过渡。用户按下「撤回/遗忘」后那条从列表消失，
     直接重绘会让它凭空不见——先播 260ms 的塌陷动画，再做真正的重绘。
     reduced-motion 下 CSS 把时长压到 0.001ms，这里照常走完同一个 await，
     所以只有 timing 不同，行为不分叉。 */
  function leaveThen(card, work) {
    if (!card) return work();
    card.classList.add("is-leaving");
    return new Promise(resolve => setTimeout(resolve, 260)).then(work);
  }

  async function selectBatch(id, focus=true) {
    const ticket = ++version;
    stopTimer(); data.detailId = id; data.detail = null; data.loading = true; render();
    try {
      const item = await api("GET",endpoint()+"/reflections",{batch_id:id});
      if (!live(ticket)) return;
      if (item.batch_id !== id) throw problem("invalid_response");
      data.detail = item; notice("");
    } catch (error) { if (live(ticket)) failure(error); }
    finally {
      if (live(ticket)) {
        data.loading = false; render(); schedule();
        if (focus) focusDetail();
      }
    }
  }

  /* 窄屏详情是覆盖层，打开后焦点必须进到详情里，
     否则键盘用户在列表上按 Tab 会穿到被盖住的内容后面。 */
  function focusDetail() {
    const detail = root?.querySelector(".mem-detail");
    if (!detail) return;
    detail.classList.add("is-open");
    const heading = detail.querySelector("[id$='-heading']");
    if (window.matchMedia?.("(max-width: 880px)")?.matches) (heading || detail).focus?.({preventScroll:true});
  }

  async function selectFact(id) {
    if (!id || data.busy) return;
    const item = data.corpus.find(value=>value.atom_id===id);
    if (!item) return;
    const ticket = ++version;
    stopTimer(); data.selId = id; data.factDetail = null; data.revisions = []; data.sourceResult = null;
    data.loading = true; render(); focusDetail();
    try {
      const detail = await api("POST",endpoint()+"/detail",{atom_id:id,revision:item.revision});
      if (!live(ticket)) return;
      if (detail.item?.atom_id !== id || detail.item?.revision !== item.revision || !Array.isArray(detail.sources)) throw problem("invalid_response");
      detail.item.view_token = item.view_token;
      /* context_mode 由列表项带过来：/detail 不重复下发它，
         但「是不是核心记忆」是详情要显示的东西。 */
      if (item.context_mode !== undefined && detail.item.context_mode === undefined) detail.item.context_mode = item.context_mode;
      data.factDetail = detail; notice("");
      /* 打开一条事实就顺手取版本历史：版本是这一页的核心信息，
         让用户再点一次才看得到，等于藏起来。 */
      await loadRevisions({silent:true});
    } catch (error) { if (live(ticket)) failure(error); }
    finally { if (live(ticket)) { data.loading = false; render(); schedule(); } }
  }

  async function loadRevisions({silent=false}={}) {
    const item = data.factDetail?.item;
    if (!item || data.busy) return;
    if (!silent && data.revisions.length) { data.revisions = []; render(); return; }
    const ticket = silent ? version : ++version;
    if (!silent) { data.loading = true; render(); }
    try {
      const result = await api("GET",endpoint()+"/revisions",{atom_id:item.atom_id,limit:64});
      if (!live(ticket)) return;
      if (!Array.isArray(result.items)) throw problem("invalid_response");
      data.revisions = result.items;
    } catch (error) { if (live(ticket) && !silent) failure(error); }
    finally { if (live(ticket)) { data.loading = false; render(); } }
  }

  async function loadClearPreview() {
    if (!role()?.memory_api || data.busy || data.loading) return;
    const ticket = ++version; data.loading = true; stopTimer(); render();
    try {
      const result = await api("GET",endpoint()+"/clear/preview");
      if (!live(ticket)) return;
      if (!result.counts || !result.revision_token) throw problem("invalid_response");
      data.clearPreview = result; notice("");
    } catch (error) { if (live(ticket)) failure(error); }
    finally { if (live(ticket)) { data.loading = false; render(); } }
  }

  async function runQuery({next=false}={}) {
    if (!role()?.memory_api || data.busy || data.loading) return;
    const input = root.querySelector("#memory-query-text");
    const kind = root.querySelector("#memory-query-kind");
    if (input) data.queryText = input.value.trim();
    if (kind) data.queryKind = kind.value;
    if (!data.queryText) { notice("请输入查询内容。",true); render(); return; }
    const previous = next ? data.queryResult : null;
    const body = {query:data.queryText,kind:data.queryKind};
    if (next && previous?.next_cursor && data.querySession) Object.assign(body,{session_id:data.querySession,cursor:previous.next_cursor});
    const ticket = ++version; data.loading = true; stopTimer(); render();
    try {
      const result = await api("POST",endpoint()+"/query",body);
      if (!live(ticket)) return;
      if (!Array.isArray(result.items) || !result.session_id) throw problem("invalid_response");
      data.querySession = result.session_id;
      data.queryResult = previous ? {...result,items:[...(previous.items||[]),...result.items]} : result;
      data.sourceResult = null; notice("");
    } catch (error) { if (live(ticket)) failure(error); }
    finally { if (live(ticket)) { data.loading = false; render(); } }
  }

  async function readSource(handle, sessionId, start) {
    if (data.busy || data.loading || !handle || !sessionId) return;
    const ticket = ++version; data.loading = true; render();
    try {
      const body = {handle,session_id:sessionId,max_chars:4000};
      if (start !== undefined) body.start = Number(start);
      const result = await api("POST",endpoint()+"/source",body);
      if (!live(ticket)) return;
      if (typeof result.text !== "string") throw problem("invalid_response");
      data.sourceResult = result; notice("");
    } catch (error) { if (live(ticket)) failure(error); }
    finally { if (live(ticket)) { data.loading = false; render(); } }
  }

  async function mutateFact(action, value) {
    const item = data.factDetail?.item;
    if (!item || item.state !== "active" || data.busy || data.loading) return;
    const confirmationVersion = version;
    if (["retract","forget"].includes(action)) {
      const accepted = await askConfirm({
        title:action==="forget"?"遗忘这条事实":"撤回这条事实",
        message:action==="forget"?"这条事实会从当前记忆中隐藏，并保留可审计的遗忘记录。":"这会产生一个新版本。",
        confirmText:action==="forget"?"遗忘事实":"撤回事实",danger:true,
      });
      if (!accepted || !live(confirmationVersion) || data.factDetail?.item?.atom_id!==item.atom_id) return;
    }
    const restored = data.pendingMutation?.kind === "fact" && data.pendingMutation.atom_id===item.atom_id && data.pendingMutation.action===action;
    if (restored && !data.pendingMutation.body) { notice("此操作曾提交但确认未确认；请先刷新事实、查看版本历史，再由你决定是否重新操作。",true); render(); return; }
    const pending = restored ? data.pendingMutation : {kind:"fact",operation_key:crypto.randomUUID(),atom_id:item.atom_id,action,
      base_revision:item.revision,view_token:item.view_token};
    const payload = pending.body || {atom_id:item.atom_id,expected_revision:item.revision,view_token:item.view_token,
      operation_key:pending.operation_key,action,...(value?{value}:{})};
    pending.body = payload; data.pendingMutation = pending; saveMutation();
    const removedId = ["retract","forget"].includes(action) ? item.atom_id : null;
    const card = removedId ? root.querySelector(`.mem-card[data-fact-id="${CSS.escape(removedId)}"]`) : null;
    const ticket = ++version; stopTimer(); data.busy = true; notice("正在保存记忆变更…"); render();
    try {
      await api("POST",endpoint()+"/command",payload);
      if (!live(ticket)) return;
      data.pendingMutation = null; saveMutation(); data.factDetail = null; data.revisions = []; data.sourceResult = null;
      data.selId = null;
      notice("已保存；正在刷新事实列表与版本。"); data.busy = false;
      await leaveThen(card, load);
    } catch (error) {
      if (live(ticket)) {
        if (error.code && !["bridge_timeout","invalid_response"].includes(error.code)) {
          data.pendingMutation = null; saveMutation(); failure(error);
          if (["memory_revision_conflict","memory_view_stale"].includes(error.code)) { data.factDetail=null; load(); }
        } else {
          notice("尚未收到保存确认。请核对当前版本；本次操作标识已保留，重试会沿用同一请求。",true);
        }
      }
    } finally { if (live(ticket)) { data.busy = false; render(); schedule(); } }
  }

  /* === 冷归档：preview → token → commit，可逆 ===
     commit 要用预览时的 token；后端在事务里重算一遍，对不上就报 stale。 */
  async function loadArchivePreview() {
    if (!role()?.memory_api || data.busy || data.loading) return;
    const input = root.querySelector("#memory-archive-idle");
    const days = Number(input?.value ?? data.archiveIdleDays);
    if (!Number.isInteger(days) || days < 30 || days > 3650) { notice("闲置天数需要在 30 到 3650 之间。", true); render(); return; }
    data.archiveIdleDays = days;
    const ticket = ++version; data.loading = true; stopTimer(); render();
    try {
      const result = await api("GET",endpoint()+"/archive/preview",{idle_days:days});
      if (!live(ticket)) return;
      if (!result.counts || !result.archive_token) throw problem("invalid_response");
      data.archivePreview = result;
      notice("");
    } catch (error) { if (live(ticket)) failure(error); }
    finally { if (live(ticket)) { data.loading = false; render(); } }
  }
  async function commitArchive() {
    const snapshot = data.archivePreview;
    if (!snapshot || data.busy || data.loading) return;
    const chosen = snapshot.items || [];
    if (!chosen.length) { notice("当前没有可归档的条目。"); render(); return; }
    /* 后端以预览版本核对并归档全部符合条件的条目。 */
    const confirmationVersion = version;
    if (!await askConfirm({title:"归档长期闲置事实",message:`将归档 ${chosen.length} 条长期闲置的持久事实。它们会移出工作集，但可以随时移回；不会删除任何内容。`,confirmText:"归档事实"})
        || !live(confirmationVersion) || data.archivePreview!==snapshot) return;
    const restored = data.pendingMutation?.kind === "archive";
    const pending = restored ? data.pendingMutation
      : {kind:"archive",operation_key:crypto.randomUUID(),revision_token:snapshot.archive_token,idle_days:snapshot.idle_days};
    const payload = {operation_key:pending.operation_key,expected_archive_token:pending.revision_token,idle_days:pending.idle_days};
    pending.body = payload; data.pendingMutation = pending; saveMutation();
    const ticket = ++version; data.busy = true; stopTimer(); notice("正在按预览版本执行归档…"); render();
    try {
      const result = await api("POST",endpoint()+"/archive",payload);
      if (!live(ticket)) return;
      data.pendingMutation = null; saveMutation();
      data.archivedResult = result;
      notice(`已归档 ${result.archived_count||0} 条；可随时移回工作集。`); data.busy = false;
      await loadArchivePreview();
    } catch (error) {
      if (live(ticket)) {
        if (error.code && !["bridge_timeout","invalid_response"].includes(error.code)) {
          data.pendingMutation = null; saveMutation(); failure(error);
          if (error.code === "memory_archive_stale") loadArchivePreview();
        } else notice("归档结果尚未确认。请刷新预览核对；重试会沿用相同操作标识与版本。", true);
      }
    } finally { if (live(ticket)) { data.busy = false; render(); } }
  }
  async function restoreArchive() {
    if (data.busy || data.loading) return;
    const ids = [...root.querySelectorAll("[data-archive-restore]:checked")].map(item=>item.dataset.archiveRestore);
    if (!ids.length) { notice("请选择要移回的记忆记录。", true); return; }
    if (ids.length > 512) { notice("一次最多移回 512 条。", true); render(); return; }
    const confirmationVersion = version;
    if (!await askConfirm({title:"移回工作集",message:`将 ${ids.length} 条事实移回工作集。它们会重新参与检索。`,confirmText:"移回事实"})
        || !live(confirmationVersion)) return;
    const ticket = ++version; data.busy = true; stopTimer(); render();
    try {
      const result = await api("POST",endpoint()+"/archive/restore",{atom_ids:ids,operation_key:crypto.randomUUID()});
      if (!live(ticket)) return;
      notice(`已移回 ${(result.restored||result.items||[]).length} 条。`); data.busy = false;
      await loadArchivePreview();
    } catch (error) { if (live(ticket)) { data.busy = false; failure(error); } }
    finally { if (live(ticket)) { data.busy = false; render(); } }
  }

  /* === 记忆包：导出只读，导入先校验 === */
  async function runExport() {
    if (data.busy || data.loading) return;
    const check = root.querySelector("#memory-export-include");
    if (check) data.exportIncludeArchived = check.checked;
    const ticket = ++version; data.loading = true; render();
    try {
      /* include_archived 只接受字符串 "true"/"false"（见 memory_api.memory_export），
         不要传 JS 布尔——序列化形态由桥接决定，传字符串就不用赌。 */
      const result = await api("GET",endpoint()+"/export",{include_archived:data.exportIncludeArchived?"true":"false"});
      if (!live(ticket)) return;
      if (!result.bundle) throw problem("invalid_response");
      data.exportBundle = result; notice("");
    } catch (error) { if (live(ticket)) failure(error); }
    finally { if (live(ticket)) { data.loading = false; render(); } }
  }
  async function copyExport() {
    const text = JSON.stringify(data.exportBundle?.bundle ?? {}, null, 2);
    try { await navigator.clipboard.writeText(text); notice("记忆包 JSON 已复制。"); }
    catch { notice("剪贴板不可用，请改用下载。", true); }
    render();
  }
  function downloadExport() {
    const text = JSON.stringify(data.exportBundle?.bundle ?? {}, null, 2);
    try {
      const url = URL.createObjectURL(new Blob([text],{type:"application/json"}));
      const link = document.createElement("a");
      link.href = url; link.download = `companion-memory-${Date.now()}.json`;
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(()=>URL.revokeObjectURL(url), 0);
      notice("已开始下载记忆包。");
    } catch { notice("当前环境无法下载，请改用复制。", true); }
    render();
  }
  async function previewImport() {
    if (data.busy || data.loading) return;
    const raw = root.querySelector("#memory-import-text")?.value?.trim() || "";
    if (!raw) { notice("请先粘贴记忆包 JSON。", true); render(); return; }
    let bundle;
    try { bundle = JSON.parse(raw); }
    catch { notice("这段内容不是合法 JSON，未做任何写入。", true); render(); return; }
    const ticket = ++version; data.loading = true; render();
    try {
      const result = await api("POST",endpoint()+"/import/preview",{bundle});
      if (!live(ticket)) return;
      if (!result.counts || !result.import_token) throw problem("invalid_response");
      /* 校验通过才留住包体：确认时要原样再发一遍，token 是按这份内容算的。 */
      data.importPreview = {...result, bundle}; notice("");
    } catch (error) { if (live(ticket)) failure(error); }
    finally { if (live(ticket)) { data.loading = false; render(); } }
  }
  async function commitImport() {
    const preview = data.importPreview;
    if (!preview || data.busy || data.loading) return;
    const confirmationVersion = version;
    if (!await askConfirm({title:"导入记忆包",message:`将导入 ${preview.counts?.new ?? 0} 条新事实（已存在的会跳过）。`,confirmText:"导入事实"})
        || !live(confirmationVersion) || data.importPreview!==preview) return;
    const ticket = ++version; data.busy = true; notice("正在导入记忆包…"); render();
    try {
      const result = await api("POST",endpoint()+"/import",{bundle:preview.bundle,
        expected_import_token:preview.import_token, operation_key:crypto.randomUUID()});
      if (!live(ticket)) return;
      data.importPreview = null;
      notice(`已导入 ${result.imported_count ?? (result.imported||[]).length} 条，跳过 ${result.skipped ?? 0} 条。`);
      data.busy = false; await load();
    } catch (error) {
      if (live(ticket)) { data.busy = false; failure(error); if (error.code === "memory_import_stale") data.importPreview = null; }
    }
    finally { if (live(ticket)) { data.busy = false; render(); } }
  }
  async function clearMemory() {
    const snapshot=data.clearPreview;
    if(!snapshot || data.busy || data.loading) return;
    const active=Number(snapshot.counts?.active)||0;
    const restored=data.pendingMutation?.kind==="clear";
    if(!active && !restored) {notice("当前范围没有仍有效的事实。",false);render();return;}
    const confirmationVersion = version;
    if(!await askConfirm({title:restored?"核对上次清理结果":"遗忘当前私聊事实",
      message:restored?"上次清理结果尚未确认。将沿用相同操作标识和预览版本重新核对结果。":`将遗忘当前私聊关系中的 ${active} 条有效事实。来源原文不会由此物理删除。`,
      confirmText:restored?"重新核对":"遗忘事实",danger:true})||!live(confirmationVersion)||data.clearPreview!==snapshot)return;
    const pending=data.pendingMutation?.kind==="clear"?data.pendingMutation:{kind:"clear",operation_key:crypto.randomUUID(),revision_token:snapshot.revision_token};
    const payload={operation_key:pending.operation_key,expected_revision_token:pending.revision_token};
    pending.body=payload;data.pendingMutation=pending;saveMutation();
    const ticket=++version;data.busy=true;stopTimer();notice("正在按预览版本执行逻辑遗忘…");render();
    try {
      const result=await api("POST",endpoint()+"/clear",payload);
      if(!live(ticket))return;
      data.pendingMutation=null;saveMutation();data.clearPreview=null;
      notice(`已逻辑遗忘 ${result.forgotten_count||0} 条事实；来源未做物理擦除。`);data.busy=false;
      await loadClearPreview();
      /* 清理动了正本，本地语料已经失效；切回事实流重读一次。 */
      data.corpus=[];data.selId=null;data.factDetail=null;
    } catch(error) {
      if(live(ticket)) {if(error.code && !["bridge_timeout","invalid_response"].includes(error.code)){data.pendingMutation=null;saveMutation();failure(error);if(error.code==="memory_clear_stale")loadClearPreview();}else notice("清理结果尚未确认。请刷新预览核对；重试会沿用相同操作标识与版本。",true);}
    } finally {if(live(ticket)){data.busy=false;render();}}
  }
  async function queue(recover=false) {
    if (data.busy || data.loading || !role()?.memory_api) return;
    if (!recover && (!(data.detail?.resumable || data.detail?.retryable) || data.pending?.uncertain || data.detail.continuation?.state === "queued")) return;
    if (recover && !data.pending?.uncertain) return;
    const ticket = ++version, path = endpoint();
    stopTimer();
    if (!recover) data.pending = {batch_id:data.detail.batch_id, operation_key:crypto.randomUUID(), tracked_batch_id:data.detail.batch_id, uncertain:true};
    const persisted = savePending(), request = {...data.pending};
    data.busy = true; notice("正在登记后台处理…"); render();
    try {
      const receipt = await api("POST",path+"/reflections/queue",{batch_id:request.batch_id,operation_key:request.operation_key});
      if (!live(ticket)) return;
      if (typeof receipt.batch_id !== "string" || !receipt.batch_id || !jobLabels[receipt.state]) throw problem("invalid_response");
      data.pending = {...request,tracked_batch_id:receipt.batch_id,uncertain:false}; savePending();
      data.detailId = receipt.batch_id; data.detail = null; data.area = "reflections"; data.tab = "reflections";
      data.busy = false;
      await load();
      if (root && data.pending?.operation_key === request.operation_key && !data.error) {
        notice((jobLabels[receipt.state] || "已登记")+"。"+(persisted?"关闭页面后也可回到上次提交。":"浏览器无法保存操作标识，请在离开前核对结果。")); render();
      }
    } catch (error) {
      if (live(ticket)) {
        if (error.code && !["bridge_timeout","invalid_response"].includes(error.code)) {
          data.pending = null; savePending(); failure(error);
        } else notice("未收到提交确认。请点击「核对上次提交」，沿用原请求确认结果。",true);
      }
    } finally { if (live(ticket)) { data.busy = false; render(); schedule(); } }
  }
  function changeScope(targetIndex, roleIndex) {
    if (!data.catalog?.targets[targetIndex]?.roles[roleIndex]) return;
    ++version; stopTimer();
    const catalog = data.catalog;
    data = {...blank(),catalog,target:targetIndex,role:roleIndex,ready:true};
    window.CompanionProduct?.choose(catalog.targets[targetIndex].target_id,catalog.targets[targetIndex].roles[roleIndex].role_id,true);
    if (role()?.memory_api) {
      try { data.pending = restorePending(); data.pendingMutation=restoreMutation(); data.detailId = data.pending?.tracked_batch_id || null; load(); }
      catch (error) { failure(error); render(); }
    } else render();
  }

  /* ================= 事件 ================= */

  async function switchArea(area) {
    if (data.area === area) return;
    data.area = area; data.tab = area;
    data.sourceResult = null; data.notice = ""; data.error = false;
    data.cursors = [""]; data.page = 0;
    if (area === "facts" || area === "reflections") { data.items = []; await load(); return; }
    if (area === "clear") { render(); await loadClearPreview(); return; }
    if (area === "archive") { render(); await loadArchivePreview(); return; }
    render();
  }

  /* 键盘：列表里上下键换条、左右键换视图、Esc 关掉窄屏详情。
     列表是 listbox，用户按键盘时才符合预期；鼠标用户不受影响。 */
  function keydown(event) {
    if (!root?.contains(event.target)) return;
    const inCorpus = event.target.closest?.(".mem-corpus");
    if (event.key === "Escape") {
      const detail = root.querySelector(".mem-detail.is-open");
      if (detail && window.matchMedia?.("(max-width: 880px)")?.matches) { closeDetail(); event.preventDefault(); }
      return;
    }
    if (!inCorpus) return;
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const cards = [...root.querySelectorAll(".mem-card")];
    const index = cards.indexOf(event.target.closest(".mem-card"));
    const next = cards[Math.max(0, Math.min(cards.length - 1, (index < 0 ? 0 : index) + (event.key === "ArrowDown" ? 1 : -1)))];
    if (next) { next.focus({preventScroll:false}); if (next.dataset.factId) selectFact(next.dataset.factId); }
  }

  function closeDetail() {
    data.selId = null; data.detailId = null; data.factDetail = null; data.revisions = []; data.sourceResult = null;
    render();
  }

  async function click(event) {
    const el = event.target.closest("button");
    if (!el || !root.contains(el) || el.disabled) return;
    const id = el.id;
    if (id === "memory-directory-refresh") { directory(); return; }
    if (id === "memory-refresh" || id === "memory-detail-refresh") {
      if (id === "memory-detail-refresh" && data.area === "reflections" && data.detailId) selectBatch(data.detailId); else load();
      return;
    }
    if (id === "memory-detail-close") { closeDetail(); return; }
    if (el.dataset.reflectionView) {
      const history = el.dataset.reflectionView === "history";
      if (history !== data.history) {
        data.history = history; data.cursors = [""]; data.page = 0;
        data.items = []; data.detailId = null; data.detail = null;
        load();
      }
      return;
    }
    if (id === "memory-queue") { queue(); return; }
    if (id === "memory-recover") {
      if (data.pending.uncertain) queue(true);
      else { data.area = "reflections"; data.tab = "reflections"; selectBatch(data.pending.tracked_batch_id); }
      return;
    }
    if (id === "memory-repair-link") { selectBatch(data.detail.repair_batch); return; }
    if (el.dataset.factId) { selectFact(el.dataset.factId); return; }
    if (id === "memory-fact-pin") { mutateFact(data.factDetail?.item?.context_mode==="pinned"?"unpin":"pin"); return; }
    if (id === "memory-fact-retract") { mutateFact("retract"); return; }
    if (id === "memory-fact-forget") { mutateFact("forget"); return; }
    if (id === "memory-add-open") { data.addOpen = true; render(); return; }
    if (id === "memory-add-cancel") { data.addOpen = false; render(); return; }
    if (id === "memory-add-submit") { addMemory(); return; }
    if (id === "memory-revisions") { loadRevisions(); return; }
    if (id === "memory-clear-preview") { loadClearPreview(); return; }
    if (id === "memory-clear-confirm") { clearMemory(); return; }
    if (id === "memory-query-next") { runQuery({next:true}); return; }
    if (el.dataset.sourceHandle) { readSource(el.dataset.sourceHandle,el.dataset.sourceSession,el.dataset.sourceStart); return; }
    if (id === "memory-pending-dismiss") { data.pendingMutation=null;saveMutation();notice("已关闭待确认提示。",false);render();return; }
    if (id === "memory-pending-refresh") {
      if(data.pendingMutation?.kind==="clear") loadClearPreview();
      else if(data.pendingMutation?.kind==="fact") { const atomId=data.pendingMutation.atom_id; data.area="facts";data.tab="facts";await load();if(root&&data.corpus.some(item=>item.atom_id===atomId))selectFact(atomId); }
      return;
    }
    if (el.dataset.batchIndex !== undefined) { selectBatch(data.items[Number(el.dataset.batchIndex)].batch_id); return; }
    if (el.dataset.filter !== undefined) { data.filter = el.dataset.filter; render(); return; }
    if (id === "memory-archive-confirm") { commitArchive(); return; }
    if (id === "memory-archive-restore-submit") { restoreArchive(); return; }
    if (id === "memory-export-run") { runExport(); return; }
    if (id === "memory-export-copy") { copyExport(); return; }
    if (id === "memory-export-download") { downloadExport(); return; }
    if (id === "memory-import-preview") { previewImport(); return; }
    if (id === "memory-import-confirm") { commitImport(); return; }
    if (id === "memory-import-discard") { data.importPreview = null; notice("已放弃这次校验结果，未做任何写入。"); render(); return; }
    if (id === "memory-previous" || id === "memory-next") {
      if (id === "memory-next") { data.cursors[data.page+1] = data.next; data.page++; } else data.page--;
      data.items = []; data.detailId = null; data.detail = null; load(); return;
    }
    if (id.startsWith("memory-area-")) { switchArea(id.slice("memory-area-".length)); return; }
  }

  function change(event) {
    const el = event.target;
    if (el.id === "memory-target") { changeScope(Number(el.value),0); return; }
    if (el.id === "memory-role") { changeScope(data.target,Number(el.value)); return; }
    if (el.id === "memory-export-include") { data.exportIncludeArchived = el.checked; return; }
  }

  /* 搜索输入：只重绘语料区，不重绘整页——否则每敲一个字输入框都会失焦。
     这是「整块重绘」架构下唯一必须开的口子。 */
  let searchTimer = null;
  function input(event) {
    if (event.target.id === "memory-add-text") { data.addText = event.target.value; return; }
    if (event.target.id !== "memory-search") return;
    data.search = event.target.value;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      const corpus = root?.querySelector(".mem-corpus");
      if (!corpus) { render(); return; }
      corpus.innerHTML = data.loading && !data.corpus.length
        ? `<div class="mem-skeleton"><i></i><i></i><i></i></div>` : corpusMarkup();
      const chips = root.querySelector(".mem-filters");
      const fresh = document.createElement("div");
      fresh.innerHTML = filtersMarkup();
      if (chips) chips.replaceWith(fresh.firstElementChild);
      const search = root.querySelector("#memory-search");
      if (search && document.activeElement !== search) { /* 保持原输入框，不重建 */ }
    }, 140);
  }

  function submit(event) {
    if (!root.contains(event.target)) return;
    if(event.target.id==="memory-correction-form") {
      event.preventDefault();
      mutateFact("correct",{object:root.querySelector("#memory-correct-object")?.value||"",
        representation:root.querySelector("#memory-correct-representation")?.value||""});
    } else if(event.target.id==="memory-query-form") {event.preventDefault();runQuery();}
    else if(event.target.id==="memory-archive-form") {event.preventDefault();loadArchivePreview();}
    else if(event.target.id==="memory-archive-restore-form") {event.preventDefault();restoreArchive();}
    else if(event.target.id==="memory-import-form") {event.preventDefault();previewImport();}
    else if(event.target.id==="memory-add-form") {event.preventDefault();addMemory();}
  }

  /* 滚动到底自动续读：语料超出预读窗口时，用户滚到底就该继续拿到更早的条目，
     而不是去找一个「下一页」按钮。 */
  function scroll(event) {
    const box = event.target;
    if (!box.classList?.contains("mem-corpus")) return;
    if (data.area !== "facts" || !data.hasMore || data.loading || data.busy) return;
    if (box.scrollTop + box.clientHeight < box.scrollHeight - 120) return;
    if (data.search || data.filter !== "all") return;
    loadMoreFacts();
  }

  /* 挂载。
   *
   * 这里原先有一条「布局预览」退路：桥不在时调用挂载方传进来的 preview()
   * 回调，渲染一份示例记录，并说明「真实记忆请从拓展页进入」。那条路在
   * 真实宿主里也会走到——因为桥是宿主异步注入的，页面常常先于桥执行完，
   * 于是用户在正经的拓展页里看到的是示例数据。
   *
   * 现在改成：等桥出现（最多 10 秒），等到了就正常读取；等不到就报
   * bridge_unavailable，由页面把这句话连同「刷新核对」一起显示出来。
   * 也就是说「没接上」不再伪装成内容，而是一条会消失的真实错误。 */
  async function mount(element) {
    unmount(); root = element; data = blank();
    root.addEventListener("click",click); root.addEventListener("change",change);
    root.addEventListener("submit",submit); root.addEventListener("input",input);
    root.addEventListener("keydown",keydown); root.addEventListener("scroll",scroll,true);
    /* 票号必须在 unmount() 之后取，而且要++：unmount() 自己会 ++version，
       若在它之前取票（或取了不自增），下面第一处 live(ticket) 立即为假，
       mount 会在读到目录之前就返回——页面看着挂上了，其实一个请求都没发。 */
    const ticket = ++version;
    /* 先把加载态画出来再等桥与目录。少了这一句，从挂载到第一个响应回来之间
       整块是空的：宿主稍慢一点，用户看到的就是「点了没反应」。
       render() 认得 !data.ready 这个状态，会画骨架屏与「正在读取授权目标…」。 */
    render();
    try {
      bridge = await whenBridgeReady(ticket);
      if (!live(ticket)) return;
      const context = await bounded(bridge.ready());
      if (!live(ticket)) return;
      if (context?.pluginName !== HOST) throw problem("bridge_unavailable");
      setChrome(true); await directory();
    } catch (error) { if (live(ticket)) { notice(reason(error.code),true); render(); } }
  }
  /* 等宿主把桥注进来。桥的注入晚于脚本执行是正常时序，
     所以「现在没有」不等于「永远没有」。
   *
   * 必须先问 product，再自己轮询：**自己拉 SDK 的逻辑在 product-page.js 里**，
   * 只轮询 window.AstrBotPluginPage 的话，页面若先于 product 挂载，
   * 就没有人去插那条 <script>，十秒后照样超时。product 不在时才退回轮询
   * （测试夹具与独立打开的工作区走这条）。 */
  async function whenBridgeReady(ticket, timeout = 10000) {
    if (window.AstrBotPluginPage?.apiGet) return window.AstrBotPluginPage;
    const product = window.CompanionProduct;
    if (product?.bridgeNow || product?.bridgeFor) {
      try {
        const found = product.bridgeNow?.() || await product.bridgeFor();
        if (found?.apiGet) return found;
      } catch (error) {
        /* 交给下面的轮询再试一次：product 的超时可能比这里短。 */
      }
      if (window.AstrBotPluginPage?.apiGet) return window.AstrBotPluginPage;
    }
    return new Promise((resolve, reject) => {
      const started = Date.now();
      const tick = () => {
        if (!live(ticket)) return;
        const found = window.AstrBotPluginPage;
        if (found?.apiGet) return resolve(found);
        if (Date.now() - started >= timeout) return reject(problem("bridge_unavailable"));
        setTimeout(tick, 80);
      };
      tick();
    });
  }
  function unmount() {
    ++version; stopTimer(); clearTimeout(searchTimer);
    root?.removeEventListener("click",click); root?.removeEventListener("change",change);
    root?.removeEventListener("submit",submit); root?.removeEventListener("input",input);
    root?.removeEventListener("keydown",keydown); root?.removeEventListener("scroll",scroll,true);
    root = null; data = null; bridge = null; setChrome(false);
  }
  document.addEventListener("visibilitychange",()=>{
    if (!data?.ready) return;
    if (document.hidden) { stopTimer(); return; }
    if (!data.busy) load();
  });
  /* `state.data` 只为测试驱动而暴露：本页的 data 是模块私有的，
     测试要断言归档/导入这些流程时没有别的入口。 */
  return {mount,unmount,get state(){return {data};}};
})();
