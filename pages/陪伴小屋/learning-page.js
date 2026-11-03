/* 陪伴小屋 · 学习与反思（培育轨道）
 *
 * 这一页管的是「角色自己长出来的技能与长期目标」：从哪条来源提出、谁确认过、
 * 现在在什么阶段、下一步能做什么、有哪些进展等着你过目。
 *
 * 为什么又是新一套版式：
 *   记忆页的轴是生命周期（一条事实的四种归宿），联系页的轴是一天（24 格的节律），
 *   而这一域的轴是**有向推进**——proposed → active ⇄ paused → achieved，
 *   任何状态都能 retired。所以主体是一条轨道：左起"提出"，右到"达成"或"归档"，
 *   每张卡停在自己的位置上。状态不是标签，是坐标。
 *
 * 三条查出来的契约，直接决定了这一页不能自己拼按钮：
 *
 * ① **动作由后端给。** `RoleGrowthManagement._project()` 按当前状态算出 `actions`
 *    列表（`management.py:85-117`），且"来源还能不能读"会进一步收窄它：
 *    来源不可用时 `confirm`/`revise`/`pause`/`resume`/`complete` 全部消失，
 *    只剩 `withdraw`/`retire`。所以页面上每一个动作按钮必须是后端给的，
 *    自己按状态推断会给出一个后端一定拒绝的按钮。
 *
 * ② **来源过期时连标题都读不出来。** `_project()` 在 `unavailable is None` 之外
 *    把 `title`/`description`/`progress_note` 一律置 `null`。
 *    这不是"字段缺失"，是设计：读不到来源就不该把内容摊出来。
 *    页面必须把这个 null 说成「来源已不可读取」，而不是显示成空白卡片。
 *
 * ③ **权限开关管的是「能不能读写」，不是「发不发」。**
 *    `role.growth.read` / `role.growth.propose` 两个 window 层布尔，
 *    而 `proposals_enabled` 在 `read_enabled` 关闭时会被后端强制跟成同一份判定
 *    （`access_management.py:29-31`）——页面上要照实反映这个从属关系。
 *
 * 读写出口：
 *   读  GET  {role.growth_api}                     列表（state/kind/limit/after_id）
 *   读  GET  {role.growth_api}/progress            待审阅的进展
 *   写  POST {role.growth_api}/command             propose / confirm / pause / resume /
 *                                                  complete / retire / withdraw / revise / configure
 *   写  POST {role.growth_api}/progress/command    accept / reject
 *   权限在列表响应的 access 里一并回来（read 已 merge 进 list，见 growth_api.py:78-80）
 *
 * 动效分两层：
 *   克制的功能动效——轨道推进、卡片移出、保存确认；
 *   场景化重头戏——一条记录的阶段位移（状态变化时卡片沿轨道滑到新位置）、
 *   审阅队列的逐个落位、来源凭证的失效褪色。
 *   prefers-reduced-motion 下压时长到瞬时终态，信息不丢。
 */
"use strict";
window.CompanionLearningPage = (() => {
  const HOST = "astrbot_plugin_private_companion";
  const PREFIX = "/" + HOST + "/";
  const STORE = "companion-learning-page-v1:";
  /* 五个阶段按推进方向排，轨道就按这个顺序画。
     `retired` 不在主线末端——它是从任何位置都能去的旁支，所以单独一条支路。 */
  const STAGES = [
    {key:"proposed", label:"提出", tone:"lea-proposed",
     hint:"由来源提出，等你确认"},
    {key:"active",   label:"进行中", tone:"lea-active",
     hint:"已确认，正在推进"},
    {key:"paused",   label:"暂停",  tone:"lea-paused",
     hint:"暂时停下，可随时继续"},
    {key:"achieved", label:"达成",  tone:"lea-achieved",
     hint:"目标已完成"},
  ];
  const RETIRED = {key:"retired", label:"归档", tone:"lea-retired"};
  const STATE_BY_KEY = Object.fromEntries([...STAGES, RETIRED].map(s => [s.key, s]));
  const KIND_LABEL = {skill:"技能", goal:"目标"};
  /* 动作文案。**准入状态不写在这里**——那由后端的 actions 决定，
     这里只负责把码翻译成人话，以及说明这个动作会带来什么后果。 */
  const ACTIONS = {
    confirm:  {label:"确认",   kind:"primary", note:"确认后进入进行中。"},
    resume:   {label:"继续",   kind:"primary", note:"从暂停恢复到进行中。"},
    pause:    {label:"暂停",   kind:"",        note:"暂停后不再计入日程参考，可随时继续。"},
    complete: {label:"已完成", kind:"primary", note:"只对目标开放；完成后进入达成。"},
    revise:   {label:"修订",   kind:"",        note:"改动标题、描述与进展，会生成新版本。"},
    withdraw: {label:"撤回",   kind:"quiet",   note:"只对未确认的记录开放；撤回后归档。"},
    retire:   {label:"归档",   kind:"quiet",   note:"归档后不再出现在活跃列表，记录保留。"},
  };
  const REVIEW_ACTIONS = {
    accept: {label:"采纳",   kind:"primary", note:"采纳后这条进展写进目标。"},
    reject: {label:"不采纳", kind:"quiet",   note:"不采纳也会记下这次审阅。"},
  };
  /* 来源失效的码。后端把它们放在 unavailable_reason 上；
     这一页只需把它翻译成"为什么读不出来"，不参与判定。 */
  const errors = {
    dashboard_identity_required:"当前登录状态不可用，请重新打开陪伴小屋。",
    staging_not_initialized:"角色数据尚未加载，请重新读取。",
    staging_host_terminated:"陪伴服务已停止，请重新读取。",
    core_access_required:"当前账号没有成长记录权限。",
    binding_revoked:"管理账号的授权已撤销。", binding_rebound:"管理账号的绑定已变化，请重新进入。",
    relationship_required:"这个对象还没有建立可读写的关系，请先在关系与权限页建立。",
    growth_query_invalid:"列表筛选条件不被接受，请更新页面后重试。",
    growth_command_invalid:"这条成长指令与接口不匹配，未做任何写入。",
    growth_progress_query_invalid:"审阅列表的查询条件不被接受，请刷新后重试。",
    growth_progress_command_invalid:"这条审阅指令与接口不匹配，未做任何写入。",
    growth_request_invalid:"请求与当前接口不匹配，请更新页面后重试。",
    growth_configuration_invalid:"权限开关只接受布尔值。",
    growth_record_unavailable:"这条记录已不在当前授权范围。",
    growth_revision_conflict:"记录已有新版本，已读取最新值。",
    growth_operation_conflict:"这个操作标识已经用于另一份内容，未重复写入。",
    growth_scope_changed:"成长记录归属已变化，请重新读取。",
    growth_transition_invalid:"当前状态不接受这个动作，已刷新可用动作。",
    growth_source_unavailable:"这条记录的原来源已不可读取，内容暂不展示。",
    growth_source_expired:"原来源已过期。",
    growth_source_revoked:"原来源已撤回。",
    source_unavailable:"原来源不可读取。", source_expired:"原来源已过期。", source_revoked:"原来源已撤回。",
    source_dependency_expired:"原来源的依赖已过期。",
    growth_review_unavailable:"这条审阅依据已不可读取，只能不采纳。",
    growth_review_conflict:"这条审阅已有结论，已刷新。",
    growth_review_missing:"这条审阅已不在待处理列表。",
    unauthorized:"当前对象尚未获得这项权限。",
    permission_denied:"当前对象尚未获得这项权限。",
    bridge_unavailable:"页面连接暂不可用，请重新读取。",
    bridge_timeout:"未收到服务确认，请刷新核对。",
    invalid_response:"服务返回的数据不完整，请刷新核对。",
  };
  const CODE = /^[a-z][a-z0-9_]{2,60}$/;
  const denied = new Set(["dashboard_identity_required","core_access_required","binding_revoked",
    "binding_rebound","staging_host_terminated","relationship_required"]);
  const e = v => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const problem = code => Object.assign(new Error(errors[code] || "当前状态未能确认，请刷新核对。"), {code});
  const reason = code => errors[code] || (code ? "当前状态未能确认，请刷新核对。" : "暂无具体原因");
  const actorLabel = id => window.CompanionProduct?.actorLabel?.(id) || (id?.startsWith("dashboard:") ? "管理员" : "授权操作者");
  const when = value => {
    if (typeof value !== "string" || !Number.isFinite(Date.parse(value))) return "时间未知";
    return new Date(value).toLocaleString("zh-CN", {hour12:false, month:"numeric", day:"numeric",
      hour:"2-digit", minute:"2-digit"});
  };
  const ICON = {
    track:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M5 17V7M12 17V4M19 17v-6"/><path d="M3 20h18"/></svg>',
    review:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="13" rx="2"/><path d="M8 9h8M8 13h5"/><path d="m9 20 3-3 3 3"/></svg>',
    source:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h16M4 12h16M4 18h9"/><circle cx="18" cy="18" r="2.4"/></svg>',
    refresh:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12a7 7 0 1 1-2.1-5"/><path d="M19 4v4h-4"/></svg>',
    check:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12.5 4.5 4.5L19 7"/></svg>',
    alert:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5v5M12 16h.01"/></svg>',
    close:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="m6 6 12 12M18 6 6 18"/></svg>',
    sourceKey:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="12" r="3.4"/><path d="M11.5 12H20M17 12v3.4"/></svg>',
  };
  let root = null, bridge = null, version = 0, data = null;

  function blank() {
    return {view:"track",layout:"default",roleLabel:"",
      path:"", profileId:"", operatorName:"",
      sources:[], listCursors:{}, reviewCursors:{},
      /* 每个来源已经读过几页：原地刷新时按它把读过的页重读一遍，
         而不是退回第一页——用户翻到哪，刷新之后就还在哪。 */
      listPages:{}, reviewPages:{},
      accesses:{},
      recordFocus:null,
      items:[], cursor:"", next:null, stateFilter:"", kindFilter:"",
      reviews:[], reviewCursor:"", reviewNext:null,
      detailId:null, detail:null,
      evidenceId:"",evidence:[],evidenceLoading:false,evidenceError:false,
      form:null, busy:false, loading:false, loadingReviews:false, writing:false, ready:false,
      notice:"", error:false, entered:new Set(), confirm:null};
  }
  /* 插件相对路径。目录给的是绝对地址，交给桥之前切掉插件目录名——
     带上它宿主会拼两次，整族 404。 */
  function rel(absolute) {
    if (typeof absolute !== "string" || !absolute.startsWith(PREFIX)) throw problem("invalid_response");
    const route = absolute.slice(PREFIX.length);
    if (!route || /[?#%\\:]/.test(route) || route.split("/").some(p => !p || p === "." || p === "..")) {
      throw problem("invalid_response");
    }
    return route;
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
    const wrapped = ["ok","error"].includes(response?.status) && response?.data !== undefined ? response
      : ["ok","error"].includes(response?.data?.status) && response?.data?.data !== undefined ? response.data : null;
    if (wrapped?.status === "error") throw problem(CODE.test(wrapped.data?.code || wrapped.message || "")
      ? (wrapped.data?.code || wrapped.message) : "invalid_response");
    const value = wrapped ? wrapped.data : response;
    if (!value || typeof value !== "object") throw problem("invalid_response");
    return value;
  }
  function live(ticket) { return ticket === version && root?.isConnected; }
  function validAccess(value) {
    return !!value && typeof value === "object"
      && !!value.settings && typeof value.settings === "object"
      && typeof value.settings.read_enabled === "boolean"
      && typeof value.settings.proposals_enabled === "boolean"
      && !!value.effective && typeof value.effective === "object"
      && typeof value.effective.read_enabled?.allowed === "boolean"
      && typeof value.effective.proposals_enabled?.allowed === "boolean"
      && typeof value.edit_token === "string" && /^[0-9a-f]{64}$/.test(value.edit_token);
  }
  function notice(message, error = false) { data.notice = message; data.error = error; }
  let opCounter = 0;
  function operationKey(kind) {
    const nonce = window.crypto?.randomUUID?.()
      || Date.now().toString(36) + "-" + Math.random().toString(36).slice(2) + "-" + opCounter++;
    return STORE + kind + ":" + nonce;
  }
  function item(id) { return data.items.find(row => row._rowKey === id || row.growth_id === id) || null; }
  function sourcePath(source, suffix = "") { return source?.path ? source.path + suffix : ""; }
  function sourceKey(source) { return source?.key || source?.path || "default"; }
  function rowKey(row) { return data.sources.length > 1 ? row?._rowKey || row?.growth_id || "" : row?.growth_id || ""; }
  function attachRow(source, row, id) {
    return {...row, _source:source, _rowKey:data.sources.length > 1 ? sourceKey(source) + "::" + id : id};
  }
  function byUpdatedAt(left, right) {
    return (Date.parse(right.updated_at || right.created_at || "") || 0)
      - (Date.parse(left.updated_at || left.created_at || "") || 0);
  }
  /* 原地刷新：新读到的行按 _rowKey 覆盖旧的，这次没读到的旧行保留。
     某一页这次变短了、或者某个来源这次失败，已经显示过的记录都不能凭空消失。 */
  function mergeKept(previous, incoming) {
    const byKey = new Map(incoming.map(row => [row._rowKey, row]));
    const kept = previous.filter(row => !byKey.has(row._rowKey));
    return [...kept, ...incoming].sort(byUpdatedAt);
  }
  /* 后端已经接受这次状态动作，先把这一行改成本地已知的新状态：
     列表不必等一次整表重读才反映用户的点击；随后的 refresh 再用后端值校正。 */
  const TRANSITIONS = {confirm:"active", resume:"active", pause:"paused",
    complete:"achieved", withdraw:"retired", retire:"retired"};
  function applyTransition(row, action, payload, extra) {
    if (!row) return;
    if (TRANSITIONS[action]) row.state = TRANSITIONS[action];
    if (extra) Object.assign(row, extra);
    row.revision = Number(payload?.revision) || Number(row.revision || 0) + 1;
    row.updated_at = new Date().toISOString();
    row.actions = [];
  }
  /* 重绘是整块 innerHTML（见 render）：滚动位置只留在数字上，列表高度一变，
     用户看到的就是另一批记录。重绘前记住「视口里第一条记录 + 它的屏幕位置
     + 展开了哪些块」，重绘后按同一把 key 复位。没有 DOM 的测试夹具会安全跳过。 */
  const viewNodes = () => (root?.querySelectorAll ? [...root.querySelectorAll("[data-view-key]")] : []);
  function snapshotView() {
    if (!root?.querySelectorAll) return null;
    const nodes = viewNodes();
    const anchor = nodes.find(node => node.getBoundingClientRect().bottom > 0);
    return {
      key: anchor?.dataset.viewKey || "",
      top: anchor ? anchor.getBoundingClientRect().top : null,
      open: nodes.flatMap(node => [...node.querySelectorAll("details")]
        .map((details, index) => details.open ? node.dataset.viewKey + "#" + index : ""))
        .filter(Boolean),
    };
  }
  function restoreView(view) {
    if (!view || !root?.querySelectorAll) return;
    if (view.open.length) {
      const wanted = new Set(view.open);
      viewNodes().forEach(node => [...node.querySelectorAll("details")].forEach((details, index) => {
        if (wanted.has(node.dataset.viewKey + "#" + index)) details.open = true;
      }));
    }
    if (!view.key || view.top === null) return;
    const anchor = viewNodes().find(node => node.dataset.viewKey === view.key);
    if (!anchor) return;
    const shift = anchor.getBoundingClientRect().top - view.top;
    if (Math.abs(shift) > 1) window.scrollBy?.(0, shift);
  }

  /* ---- 读 ------------------------------------------------------------- */
  function listQuery(afterId = "") {
    return {
      ...(afterId ? {after_id: afterId} : {}),
      ...(data.stateFilter ? {state: data.stateFilter} : {}),
      ...(data.kindFilter ? {kind: data.kindFilter} : {}),
    };
  }
  /* 读与写各有各的标志，而且**读的护栏不能看写的标志**。
     这是一个踩过两次的坑：写操作在开始时置上忙标志，结束时调用 load() 刷新，
     而 load() 的护栏正是那个忙标志——于是它立即早退，忙标志再也没人清，
     整页从此卡住：动作发出去了、结果也回来了，界面却停在原地。
     两个标志职责不同：loading 管"正在读"，writing 管"正在写"。 */
  /* 两种读法，区别只在「从哪一页开始」与「读到的行怎么并进现有列表」：
       reset   换视图、换筛选、首次读取：清空重来，每个来源从第一页开始；
       refresh 刷新与写操作之后：把这次已经读过的页数原地重读一遍，
               新数据按 _rowKey 覆盖旧行，这次没读到的旧行保留。
     两种模式都不会让已经显示的记录凭空消失。
     refresh 的重读页数有上限：已加载页数会被「加载更多」一路抬高，而刷新是动作后的
     常见路径——不设上限就等于一次动作串行发几十次请求。上限之内的页重读不到的行
     由合并保留，游标退回上限处，用户再点「加载更多」会顺着往后走（合并会去重）。 */
  const MAX_REFRESH_PAGES = 8;
  async function load({mode = "reset"} = {}) {
    if (!data.sources.length || data.loading) return;
    const ticket = version;
    const reset = mode === "reset";
    const previous = reset ? [] : data.items.slice();
    if (reset) { data.listCursors = {}; data.listPages = {}; data.accesses = {}; }
    data.loading = true; render();
    try {
      const requests = await Promise.allSettled(data.sources.map(async source => {
        const listed = Math.max(1, Number(data.listPages[sourceKey(source)] || 1));
        const rounds = reset ? 1 : Math.min(MAX_REFRESH_PAGES, listed);
        const rows = [];
        let cursor = null, first = null, pages = 0;
        for (let index = 0; index < rounds; index++) {
          const page = await api("GET", source.path, listQuery(cursor || ""));
          if (!Array.isArray(page.items)) throw problem("invalid_response");
          if (!first) first = page;
          pages++;
          rows.push(...page.items.map(row => attachRow(source, row, row.growth_id)));
          cursor = page.next_cursor || null;
          if (!cursor) break;
        }
        /* 深链：目标记录不在前几页时继续往后读（保持原行为）。
           两个约束：① 扫过的页不算「已加载页」，否则之后每次刷新都会按页数重读；
           ② 目标记录已经在列表里就不再扫——挂载时扫一次足够，每次刷新/动作都重扫
           等于串行发 N 次请求（N 上限 200）。 */
        const focusLoaded = previous.some(row => row.growth_id === data.recordFocus?.growth_id
          && row._source?.target?.target_id === data.recordFocus?.target_id
          && row._source?.role?.role_id === data.recordFocus?.role_id);
        let guard = 0;
        while (!focusLoaded && data.layout === "chain" && data.recordFocus?.growth_id
          && data.recordFocus.target_id === source.target?.target_id
          && data.recordFocus.role_id === source.role?.role_id
          && !rows.some(row => row.growth_id === data.recordFocus.growth_id)
          && cursor && guard++ < 200) {
          const page = await api("GET", source.path, listQuery(cursor));
          if (!Array.isArray(page.items)) throw problem("invalid_response");
          rows.push(...page.items.map(row => attachRow(source, row, row.growth_id)));
          cursor = page.next_cursor || null;
        }
        return {source, rows, cursor, pages, access:first?.access || null,
          profileId:first?.profile_id || "", operatorName:first?.operator_name || ""};
      }));
      if (!live(ticket)) return;
      const valid = requests.filter(result => result.status === "fulfilled").map(result => result.value);
      const failed = requests.length - valid.length;
      if (!valid.length) throw requests.find(result => result.status === "rejected")?.reason || problem("invalid_response");
      const merged = [...new Map(valid.flatMap(result => result.rows).map(row => [row._rowKey, row])).values()];
      data.items = reset ? merged.sort(byUpdatedAt) : mergeKept(previous, merged);
      data.accesses = Object.fromEntries(valid.filter(result => validAccess(result.access))
        .map(result => [sourceKey(result.source), result.access]));
      for (const result of valid) {
        const key = sourceKey(result.source);
        data.listCursors[key] = result.cursor;
        data.listPages[key] = result.pages;
      }
      data.cursor = data.stateFilter || data.kindFilter ? "" : "all";
      data.next = Object.values(data.listCursors).some(Boolean) ? "more" : null;
      data.profileId = "";
      data.operatorName = valid[0]?.operatorName || "";
      data.ready = true;
      if (failed) notice(`部分记录归属暂不可读取（${failed} 处）。`, true);
      /* 详情跟着列表刷新：列表里的那条已经变了，详情不能停在旧版本。 */
      if (data.detailId && !item(data.detailId)) {
        data.detailId = null;data.form=null;
        notice("所选记录已不在当前可读范围。", true);
      }
      if (data.recordFocus?.growth_id && !data.detailId) {
        const focused = data.items.find(row => row.growth_id === data.recordFocus.growth_id
          && row._source?.target?.target_id === data.recordFocus.target_id
          && row._source?.role?.role_id === data.recordFocus.role_id);
        if (focused) data.detailId = rowKey(focused);
      }
      if (!data.detailId && data.layout==="chain")data.detailId=rowKey(data.items[0])||null;
      data.detail=data.detailId?item(data.detailId):null;
      if(data.layout==="chain"&&data.detailId)await loadEvidence(data.detailId);
    } catch (error) {
      if (!live(ticket)) return;
      /* 只有 reset 才清空：刷新失败时保留已经显示的记录，一次网络抖动不该把
         用户翻出来的第二页抹掉；下方那条提示已经说明了原因。 */
      if (reset) { data.items = []; data.detail = null; data.next = null; }
      const failure = error.code ? reason(error.code) : "连接中断，请刷新核对。";
      notice(reset ? failure : failure + "（已保留当前内容）", true);
      if (denied.has(error.code)) { data.ready = false; data.sources = []; data.path = ""; }
    } finally {
      if (live(ticket)) { data.loading = false; render(); }
    }
  }
  async function loadEvidence(id) {
    const row=item(id);
    data.evidenceId=id;data.evidence=[];data.evidenceError=false;
    const path=sourcePath(row?._source, "/evidence");
    if(!row||row.unavailable_reason||!path){render();return;}
    const ticket=version;
    data.evidenceLoading=true;render();
    try{
      const result=await api("GET",path,{growth_id:row.growth_id});
      if(!live(ticket)||data.detailId!==id)return;
      if(!Array.isArray(result.items))throw problem("invalid_response");
      data.evidence=result.items;
    }catch(error){
      if(!live(ticket)||data.detailId!==id)return;
      data.evidenceError=true;
    }finally{
      if(live(ticket)&&data.detailId===id){data.evidenceLoading=false;render();}
    }
  }
  async function loadMore() {
    if (!data.next || data.loading || !data.sources.length) return;
    const ticket = version;
    data.loading = true; render();
    try {
      const sources=data.sources.filter(source=>data.listCursors[sourceKey(source)]);
      const pages=await Promise.allSettled(sources.map(async source=>{
        const cursor=data.listCursors[sourceKey(source)];
        const page=await api("GET",source.path,listQuery(cursor));
        if(!Array.isArray(page.items))throw problem("invalid_response");
        return {source,page};
      }));
      if (!live(ticket)) return;
      const valid=pages.filter(result=>result.status==="fulfilled").map(result=>result.value);
      if(!valid.length&&pages.length)throw pages[0].reason||problem("invalid_response");
      const incoming=valid.flatMap(({source,page})=>page.items.map(row=>attachRow(source,row,row.growth_id)));
      data.items=[...new Map(data.items.concat(incoming).map(row=>[row._rowKey,row])).values()].sort(byUpdatedAt);
      for(const {source,page} of valid){
        const key=sourceKey(source);
        data.listCursors[key]=page.next_cursor||null;
        data.listPages[key]=Number(data.listPages[key]||1)+1;
      }
      data.next=Object.values(data.listCursors).some(Boolean)?"more":null;
      if(valid.length<pages.length)notice("部分记录归属暂不可读取，已显示其余记录。",true);
    } catch (error) {
      if (live(ticket)) notice(error.code ? reason(error.code) : "下一页没有读到。", true);
    } finally {
      if (live(ticket)) { data.loading = false; render(); }
    }
  }
  /* 列表与审阅是两条独立的读取，所以**各有各的忙标志**。
     共用一个的话，挂载时 `await load()` 期间它一直是 true，
     随后调用的 loadReviews() 会直接早退——审阅队列永远是空的，
     而用户看到的只是"没有待审阅"，不会报错。 */
  /* 审阅队列与列表一样分页：reset 从第一页重来，refresh 把已经读过的页数原地重读，
     新数据按 _rowKey 覆盖旧行、没读到的旧行保留。 */
  async function loadReviews({mode = "reset"} = {}) {
    if (!data.sources.length || data.loadingReviews) return;
    const ticket = version;
    const reset = mode === "reset";
    const previous = reset ? [] : data.reviews.slice();
    if (reset) { data.reviewCursors = {}; data.reviewPages = {}; }
    data.loadingReviews = true; render();
    try {
      const results=await Promise.allSettled(data.sources.map(async source=>{
        const listed = Math.max(1, Number(data.reviewPages[sourceKey(source)] || 1));
        /* 与列表同一条上限：审阅队列也可能被「加载更多」堆到十几页。 */
        const rounds = reset ? 1 : Math.min(MAX_REFRESH_PAGES, listed);
        const rows = [];
        let cursor = null, pages = 0;
        for (let index = 0; index < rounds; index++) {
          const page = await api("GET", source.path + "/progress", cursor ? {after_id:cursor} : undefined);
          if(!Array.isArray(page.items))throw problem("invalid_response");
          pages++;
          rows.push(...page.items.map(row=>attachRow(source,row,row.review_id)));
          cursor = page.next_cursor || null;
          if (!cursor) break;
        }
        return {source, rows, cursor, pages};
      }));
      if (!live(ticket)) return;
      const valid=results.filter(result=>result.status==="fulfilled").map(result=>result.value);
      if(!valid.length&&results.length)throw results.find(result=>result.status==="rejected")?.reason||problem("invalid_response");
      const merged=[...new Map(valid.flatMap(result=>result.rows).map(row=>[row._rowKey,row])).values()];
      data.reviews=(reset?merged:mergeKept(previous,merged)).sort(byUpdatedAt);
      for (const result of valid) {
        const key = sourceKey(result.source);
        data.reviewCursors[key] = result.cursor;
        data.reviewPages[key] = result.pages;
      }
      data.reviewNext=Object.values(data.reviewCursors).some(Boolean)?"more":null;
      if(valid.length<results.length)notice("部分审阅记录归属暂不可读取。",true);
      data.ready = true;
    } catch (error) {
      if (!live(ticket)) return;
      if (reset) data.reviews = [];
      const failure = error.code ? reason(error.code) : "审阅列表没有读到。";
      notice(reset ? failure : failure + "（已保留当前内容）", true);
    } finally {
      if (live(ticket)) { data.loadingReviews = false; render(); }
    }
  }
  async function loadMoreReviews() {
    if (!data.reviewNext || data.loadingReviews) return;
    const ticket = version;
    data.loadingReviews = true; render();
    try {
      const sources=data.sources.filter(source=>data.reviewCursors[sourceKey(source)]);
      const pages=await Promise.allSettled(sources.map(async source=>{
        const page=await api("GET",source.path+"/progress",{after_id:data.reviewCursors[sourceKey(source)]});
        if(!Array.isArray(page.items))throw problem("invalid_response");
        return {source,page};
      }));
      if (!live(ticket)) return;
      const valid=pages.filter(result=>result.status==="fulfilled").map(result=>result.value);
      if(!valid.length&&pages.length)throw pages[0].reason||problem("invalid_response");
      const incoming=valid.flatMap(({source,page})=>page.items.map(row=>attachRow(source,row,row.review_id)));
      data.reviews=mergeKept(data.reviews,incoming);
      for(const {source,page} of valid){
        const key=sourceKey(source);
        data.reviewCursors[key]=page.next_cursor||null;
        data.reviewPages[key]=Number(data.reviewPages[key]||1)+1;
      }
      data.reviewNext=Object.values(data.reviewCursors).some(Boolean)?"more":null;
    } catch (error) {
      if (live(ticket)) notice(error.code ? reason(error.code) : "下一页审阅没有读到。", true);
    } finally {
      if (live(ticket)) { data.loadingReviews = false; render(); }
    }
  }

  /* ---- 写 ------------------------------------------------------------- */
  /* 状态动作。载荷照 growth_api.command 的合同：
     {action, operation_key, view_token, growth_id, expected_revision}，
     只有 revise 额外带 title/description/progress_note。 */
  async function act(id, action, extra) {
    const row = item(id);
    if (!row || data.writing) return;
    if (!row.actions || !row.actions.includes(action)) {
      /* 后端没给这个动作就绝不发。发出去一定被拒，而且会把可用动作刷一遍，
         用户看到按钮跳走却不知道为什么。 */
      notice("当前状态不接受「" + (ACTIONS[action]?.label || "这项操作") + "」，已刷新可用动作。", true);
      await load({mode:"refresh"}); return;
    }
    const ticket = version;
    data.writing = true; render();
    try {
      const body = {action, operation_key:operationKey(action), view_token:row.view_token,
        growth_id:row.growth_id, expected_revision:row.revision};
      if (extra) Object.assign(body, extra);
      const payload = await api("POST", sourcePath(row._source, "/command"), body);
      if (!live(ticket)) return;
      /* 先把这一行改成已知的新状态，再原地刷新：列表不必整表重来，
         用户翻到第几页、看的是哪条记录，动作之后都还在原处。 */
      applyTransition(row, action, payload, extra);
      data.detailId = data.layout === "chain" ? id : null;
      data.detail = null; data.form = null;
      data.entered.add(id);
      data.writing = false;
      notice((ACTIONS[action]?.label || "操作") + "已完成，版本 " + (payload?.revision ?? row.revision + 1) + "。");
      await load({mode:"refresh"});
    } catch (error) {
      if (!live(ticket)) return;
      data.writing = false;
      const code = error.code || "";
      notice(reason(code), true);
      if (code === "growth_revision_conflict" || code === "growth_transition_invalid") await load({mode:"refresh"});
      else render();
    }
  }
  /* 提出新记录。sources 是一到八条完整来源引用——这里不做"选来源"的界面，
     因为它需要 ScopeRef 与 VersionRef 的完整结构，页面上拼不出来：
     来源凭证由对话侧或迁移侧签发，页面只负责把它们带上。
     所以提出入口只在你确实拿到了凭证时才可用，并用一行说明这件事。 */
  async function propose(form) {
    if (data.writing || !data.sources.length) return;
    const ticket = version;
    data.writing = true; render();
    try {
      const body = {action:"propose", operation_key:operationKey("propose"),
        view_token:form.view_token, kind:form.kind, title:form.title, description:form.description,
        progress_note:form.progress_note, sources:form.sources};
      const payload = await api("POST", sourcePath(data.sources[0], "/command"), body);
      if (!live(ticket)) return;
      data.form = null;
      data.writing = false;
      notice("已提出，等待确认。");
      await load({mode:"refresh"});
    } catch (error) {
      if (!live(ticket)) return;
      data.writing = false; notice(reason(error.code), true); render();
    }
  }
  /* 审阅裁决。载荷只有五个字段，一个不多（growth_api.py 对 progress_command
     做的是 `set(data) != fields` 的严格相等校验）。 */
  /* 裁决是**写**，所以它不能挂在读取的标志上。
     `loadingReviews` 会由点开审阅页签触发的那次读取置上，
     用它当护栏等于"读的时候把自己要操作的按钮禁掉了"：
     点开页签、立刻点采纳，什么都不会发生，也没有任何提示。
     读与写各有各的标志，互不阻塞——写完照例重新读一遍。 */
  async function review(row, action) {
    if (data.writing) return;
    if (!row.actions || !row.actions.includes(action)) {
      notice("这条审阅当前只能「不采纳」。", true); render(); return;
    }
    const ticket = version;
    data.writing = true; render();
    try {
      await api("POST", sourcePath(row._source, "/progress/command"), {
        review_id:row.review_id, action, expected_revision:row.base_revision,
        view_token:row.view_token, operation_key:operationKey("review")});
      if (!live(ticket)) return;
      /* 被裁决的那一条先本地移除（它是「待审阅」，裁决完就不该在这里），
         其余的原地刷新——已经翻出来的第二页审阅记录不能因为这次裁决消失。 */
      data.reviews = data.reviews.filter(entry => entry._rowKey !== row._rowKey);
      data.writing = false;
      notice(action === "accept" ? "已采纳，进展写进目标。" : "已记下这次审阅。");
      await load({mode:"refresh"});
      await loadReviews({mode:"refresh"});
    } catch (error) {
      if (!live(ticket)) return;
      data.writing = false; notice(reason(error.code), true);
      if (error.code === "growth_review_conflict") await loadReviews({mode:"refresh"}); else render();
    }
  }

  async function updateAccess(source, field, enabled) {
    const key = sourceKey(source), access = data.accesses[key];
    if (data.writing || !access || !["read_enabled", "proposals_enabled"].includes(field)
        || typeof enabled !== "boolean" || access.settings[field] === enabled) return;
    const ticket = version;
    data.writing = true; render();
    try {
      const result = await api("POST", sourcePath(source, "/command"), {
        action:"configure", operation_key:operationKey("configure-" + field),
        edit_token:access.edit_token, changes:{[field]:enabled},
      });
      if (!live(ticket)) return;
      if (!validAccess(result.access)) throw problem("invalid_response");
      data.accesses[key] = result.access;
      data.writing = false;
      notice("已更新「" + (source.label || data.roleLabel || "当前私聊对象") + "」的成长权限。");
      render();
    } catch (error) {
      if (!live(ticket)) return;
      data.writing = false;
      notice(reason(error.code), true);
      if (error.code === "configuration_revision_conflict") await load({mode:"refresh"});
      else render();
    }
  }

  /* ---- 渲染 ------------------------------------------------------------ */
  const VIEWS = [["track","培育轨道",ICON.track],["reviews","待审阅",ICON.review],
                 ["sources","来源与凭证",ICON.source]];
  /* 列表分页。后端 limit 上限是 20（页面传 20），游标是 after_id。
     这一页不做本地全量预读（记忆页那样）——因为成长记录条数天然远小于事实，
     而且每一条都可能带动作，预读多页会把动作列表读旧。 */
  function headMarkup() {
    const label = VIEWS.find(([id]) => id === data.view)?.[1] || "培育轨道";
    const pending = data.reviews.length;
    return `<header class="lea-head">
      <div class="lea-head-main">
        <h2>${e(label)}</h2>
        ${pending ? `<span class="lea-pill">${pending} 条待审阅</span>` : ""}
        ${data.sources.length > 1 ? `<span class="lea-id">汇总 ${data.sources.length} 个记录归属</span>` : ""}
      </div>
      <div class="lea-head-actions">
        <button type="button" class="lea-btn" data-action="refresh"${data.loading || data.writing ? " disabled" : ""}>${ICON.refresh}<span>重新读取</span></button>
      </div>
    </header>`;
  }
  function gateView() {
    const rows = data.sources.flatMap(source => {
      const access = data.accesses[sourceKey(source)];
      if (!access) return [];
      const read = access.settings.read_enabled === true;
      const label = source.label || data.roleLabel || "当前私聊对象";
      const toggle = (field, title, description) => {
        const checked = access.settings[field] === true;
        const effective = access.effective[field]?.allowed === true;
        return `<div class="lea-switch${field === "proposals_enabled" && !read ? " is-following" : ""}">
          <label><input type="checkbox" data-access="${field}" data-source="${e(sourceKey(source))}"
            aria-label="${e(label)}：${e(title)}"${checked ? " checked" : ""}${data.writing ? " disabled" : ""}>
            <span class="lea-switch-track" aria-hidden="true"><i></i></span>
            <span class="lea-switch-text"><strong>${e(title)}</strong><small>${e(description)}</small></span></label>
          <p class="lea-switch-note${effective ? "" : " is-warn"}">${effective ? "当前已生效" : "当前未生效"}</p>
        </div>`;
      };
      return [`<section class="lea-gate" aria-label="${e(label)}成长授权">
        <header class="lea-gate-head"><div><h3>私聊对象授权</h3><p>仅影响此对象与当前角色，不会修改其他私聊窗口。</p></div><span>${e(label)}</span></header>
        <div class="lea-gate-list">
          ${toggle("read_enabled", "读取成长记录", "允许角色在对话中读取已确认的技能与目标。")}
          ${toggle("proposals_enabled", "提出成长建议", read
            ? "允许角色依据当前对话提出新技能或长期目标，仍需你确认。"
            : "读取权限关闭时，提案授权暂不生效；开启读取后按此设置恢复。")}
        </div>
      </section>`];
    }).join("");
    return rows;
  }
  function railMarkup() {
    return `<nav class="lea-rail" aria-label="学习与反思工作台">${VIEWS.map(([id, text, icon]) =>
      `<button type="button" data-view="${id}" aria-pressed="${data.view === id}">
        <span class="lea-rail-icon">${icon}</span><span class="lea-rail-label">${e(text)}</span>
        ${id === "reviews" && data.reviews.length ? `<em class="lea-count">${data.reviews.length}</em>` : ""}</button>`).join("")}</nav>`;
  }
  function noticeMarkup() {
    if (!data.notice) return "";
    return `<p class="lea-notice${data.error ? " is-error" : ""}" role="status">${data.error ? ICON.alert : ICON.check}`
      + `<span>${e(data.notice)}</span><button type="button" data-action="dismiss" aria-label="关闭">×</button></p>`;
  }
  function emptyMarkup() {
    if (data.loading) return `<div class="lea-empty"><span class="lea-spinner"></span>正在读取成长记录…</div>`;
    if (!data.sources.length) return `<div class="lea-empty">${ICON.alert}<strong>还没有可读取的成长记录</strong></div>`;
    return `<div class="lea-empty">${ICON.alert}<strong>还没有读到记录</strong>
      <p>${e(data.notice || "暂无成长记录")}</p>
      <button type="button" class="lea-btn" data-action="refresh">${ICON.refresh}<span>重新读取</span></button></div>`;
  }
  /* 轨道。四段主线一列，归档是旁支挂在末段右侧。
     每段上方写该段的条数——空段也留着，因为"这一段现在没有"本身是信息，
     而按有内容的段动态缩短轨道会让人以为流程变了。 */
  function trackView() {
    const groups = STAGES.map(stage => ({stage, rows:data.items.filter(row => row.state === stage.key)}));
    const retired = data.items.filter(row => row.state === RETIRED.key);
    const unknown = data.items.filter(row => !STATE_BY_KEY[row.state]);
    return `<div class="lea-track">
      ${filterBar()}
      <div class="lea-lanes">
        ${groups.map(({stage, rows}) => lane(stage, rows)).join("")}
        ${retired.length || data.stateFilter === "retired" ? lane(RETIRED, retired) : ""}
        ${unknown.length ? `<section class="lea-lane is-unknown"><header>
          <h3>状态未识别</h3><em>${unknown.length}</em></header>
          <p class="lea-lane-warn">这些记录的状态暂时无法确认，请重新读取后核对。</p>
          ${unknown.map(row => card(row)).join("")}</section>` : ""}
      </div>
      ${confirmMarkup()}
      ${data.next ? `<button type="button" class="lea-btn is-wide" data-action="more"${data.loading ? " disabled" : ""}>${ICON.refresh}<span>读取下一页</span></button>` : ""}
    </div>`;
  }
  function filterBar() {
    const states = [...STAGES, RETIRED];
    return `<div class="lea-filters" role="group" aria-label="筛选">
      <span class="lea-filters-label">阶段</span>
      <button type="button" data-filter-state="" aria-pressed="${!data.stateFilter}">全部</button>
      ${states.map(s => `<button type="button" data-filter-state="${s.key}" aria-pressed="${data.stateFilter === s.key}">${e(s.label)}</button>`).join("")}
      <span class="lea-filters-label">类型</span>
      <button type="button" data-filter-kind="" aria-pressed="${!data.kindFilter}">全部</button>
      ${Object.entries(KIND_LABEL).map(([key, text]) =>
        `<button type="button" data-filter-kind="${key}" aria-pressed="${data.kindFilter === key}">${e(text)}</button>`).join("")}
    </div>`;
  }
  function lane(stage, rows) {
    return `<section class="lea-lane ${stage.tone}" data-lane="${stage.key}">
      <header><span class="lea-lane-dot"></span>
        <h3>${e(stage.label)}</h3><em>${rows.length}</em>
        <span class="lea-lane-hint">${e(stage.hint)}</span></header>
      ${rows.length ? rows.map(row => card(row)).join("")
        : `<p class="lea-lane-empty">这一段暂时是空的。</p>`}
    </section>`;
  }
  function card(row) {
    const stage = STATE_BY_KEY[row.state] || {};
    /* 来源不可用时后端把 title/description 都置 null。这不是缺字段，
       所以文案必须说清"读不到"而不是显示成一张空卡。 */
    const blind = !!row.unavailable_reason;
    const key = rowKey(row);
    const entering = data.entered.has(key);
    return `<article class="lea-card${blind ? " is-blind" : ""}${entering ? " is-entered" : ""}"
      data-view-key="${e(key)}" data-id="${e(key)}" tabindex="0" role="button"
      aria-label="${e(blind ? "来源不可读取的记录" : row.title || "未命名记录")}">
      <div class="lea-card-top">
        <span class="lea-kind">${e(KIND_LABEL[row.kind] || "成长记录")}</span>
        <span class="lea-rev">v${e(row.revision)}</span>
      </div>
      <h4 class="lea-card-title">${blind ? `<span class="lea-blind-label">来源已不可读取</span>` : e(row.title || "未命名记录")}</h4>
      ${blind ? `<p class="lea-card-why">${e(reason(row.unavailable_reason))}</p>`
        : row.progress_note ? `<p class="lea-card-note">${e(row.progress_note)}</p>` : ""}
      <div class="lea-card-foot">
        <span class="lea-card-meta">记录归属 · ${e(row._source?.label||"")} · ${e(row.source_count ?? 0)} 条依据 · ${e(when(row.updated_at))}</span>
        ${(row.actions || []).length ? `<span class="lea-card-actions">${(row.actions || []).slice(0, 2).map(a =>
          `<button type="button" class="lea-mini${ACTIONS[a]?.kind === "primary" ? " is-primary" : ""}"
            data-act="${e(a)}" data-id="${e(key)}">${e(ACTIONS[a]?.label || "处理记录")}</button>`).join("")}</span>` : ""}
      </div>
    </article>`;
  }

  /* 详情。整页唯一的"深度"视图：一张卡的全部字段、完整的动作列表、
     以及修订与来源。动作列表在这里完整给出（卡片上只给前两个）。 */
  function detailView() {
    const row = data.detail || (data.detailId ? item(data.detailId) : null)
      || (data.layout === "chain" ? data.items[0] : null);
    if (!row) return `<div class="lea-detail"><p class="lea-lane-empty">暂无成长记录</p></div>`;
    const stage = STATE_BY_KEY[row.state] || {};
    const blind = !!row.unavailable_reason;
    return `<div class="lea-detail">
      ${data.layout==="chain"?"":`<button type="button" class="lea-btn is-quiet" data-action="close-detail">${ICON.close}<span>返回轨道</span></button>`}
      <header class="lea-detail-head ${stage.tone || ""}">
        <span class="lea-pill">${e(stage.label || "状态待核对")}</span>
        <span class="lea-kind">${e(KIND_LABEL[row.kind] || "成长记录")}</span>
        <span class="lea-rev">v${e(row.revision)}</span>
        <span class="lea-review-target">记录归属 · ${e(row._source?.label||"")}</span>
      </header>
      ${blind ? `<div class="lea-blind">
          ${ICON.sourceKey}
          <div><strong>原来源已不可读取</strong>
            <p>来源不可读；仅可归档。${e(reason(row.unavailable_reason))}</p>
            </div></div>`
        : `<h3 class="lea-detail-title">${e(row.title || "未命名记录")}</h3>
           ${row.description ? `<p class="lea-detail-desc">${e(row.description)}</p>` : ""}
           ${row.progress_note ? `<div class="lea-detail-note"><span>进展</span><p>${e(row.progress_note)}</p></div>` : ""}`}
      ${row.proposed_by || row.confirmed_by || row.changed_by ? `<dl class="lea-chain">
        ${row.proposed_by ? `<div><dt>提出</dt><dd>${e(actorLabel(row.proposed_by))}</dd></div>` : ""}
        ${row.confirmed_by ? `<div><dt>确认</dt><dd>${e(actorLabel(row.confirmed_by))}</dd></div>` : ""}
        ${row.changed_by ? `<div><dt>最近变更</dt><dd>${e(actorLabel(row.changed_by))}</dd></div>` : ""}
        </dl>` : ""}
      <dl class="lea-times">
        <div><dt>创建</dt><dd>${e(when(row.created_at))}</dd></div>
        <div><dt>更新</dt><dd>${e(when(row.updated_at))}</dd></div>
        <div><dt>来源</dt><dd>${e(row.source_count ?? 0)} 条</dd></div>
      </dl>
      <section class="lea-detail-actions">
        ${(row.actions || []).length ? `<div class="lea-action-row">${(row.actions || []).map(a => {
          const spec = ACTIONS[a] || {label:"处理记录", kind:"", note:""};
          return `<button type="button" class="lea-btn${spec.kind === "primary" ? " is-primary" : spec.kind === "quiet" ? " is-quiet" : ""}"
            data-act="${e(a)}" data-id="${e(rowKey(row))}"${data.writing ? " disabled" : ""}>
            <span>${e(spec.label)}</span></button>`;
        }).join("")}</div>`
        : `<p class="lea-lane-empty">当前状态没有可用动作。</p>`}
      </section>
      ${confirmMarkup()}
      ${data.layout!=="chain"&&(row.actions || []).includes("revise") ? reviseForm(row) : ""}
    </div>`;
  }

  function chainWorkView() {
    const current=data.detailId?item(data.detailId):data.items[0];
    const rows=data.items.map(row=>{
      const stage=STATE_BY_KEY[row.state]||{};
      const key=rowKey(row);
      return `<button type="button" class="lea-chain-row${rowKey(current)===key?" is-selected":""}"
        data-view-key="${e(key)}" data-open="${e(key)}" aria-pressed="${rowKey(current)===key}">
        <span class="lea-lane-dot ${stage.tone||""}" aria-hidden="true"></span>
        <span class="lea-chain-row-copy"><strong>${e(row.unavailable_reason?"来源已不可读取":row.title||"未命名记录")}</strong>
        <small>记录归属 · ${e(row._source?.label||"")} · ${e(KIND_LABEL[row.kind]||"成长记录")} · v${e(row.revision)} · ${e(row.source_count??0)} 条依据 · ${e(when(row.updated_at))}</small></span>
        <span class="lea-pill ${stage.tone||""}">${e(stage.label||"待核对")}</span></button>`;
    }).join("");
    const evidence=rowEvidenceMarkup(current);
    return `<div class="lea-chain-grid">
      <div class="lea-chain-left"><section class="lea-chain-list lea-block"><header><h3>记录列表</h3><span>${data.items.length} 条</span></header>
        ${data.loading&&!data.items.length?`<p class="lea-lane-empty">正在读取…</p>`:rows||`<p class="lea-lane-empty">暂无成长记录</p>`}
        ${data.next?`<button type="button" class="lea-btn is-wide" data-action="more"${data.loading?" disabled":""}>读取下一页</button>`:""}</section>
        <section class="lea-chain-evidence lea-block"><header><h3>依据与凭证</h3><span>${current?e(current.source_count??0)+" 条记录":"—"}</span></header>${evidence}</section></div>
      <div class="lea-chain-right"><section class="lea-chain-detail lea-block"><header><h3>记录详情 · 工作台</h3><button type="button" class="lh-refresh" data-action="refresh" aria-label="重新读取" title="重新读取"${data.loading||data.writing?" disabled":""}>${ICON.refresh}</button></header>${detailView()}</section>
      ${current?.actions?.includes("revise")?`<section class="lea-chain-revision"><header><h3>修订 · 生成新版本</h3></header>${reviseForm(current)}</section>`:""}</div>
    </div>
    ${data.notice?noticeMarkup():""}`;
  }

  function rowEvidenceMarkup(row) {
    if(!row)return `<p class="lea-lane-empty">选择一条记录</p>`;
    if(row.unavailable_reason)return `<p class="lea-card-why">${e(reason(row.unavailable_reason))}</p>`;
    if(data.evidenceLoading&&data.evidenceId===rowKey(row))return `<p class="lea-lane-empty">正在读取依据…</p>`;
    if(data.evidenceError&&data.evidenceId===rowKey(row))return `<p class="lea-card-why">依据暂时无法读取。</p>`;
    if(data.evidenceId!==rowKey(row)||!data.evidence.length)return `<p class="lea-lane-empty">没有可显示的来源内容</p>`;
    return `<ol>${data.evidence.map(item=>`<li><p>${e(item.text)}</p><time>${e(when(item.issued_at))}</time></li>`).join("")}</ol>`;
  }
  function reviseForm(row) {
    const draft = data.form?.id === rowKey(row) ? data.form : null;
    return `<form class="lea-revise" data-form="revise">
      <h4>修订</h4>
      <label class="lea-field"><span>标题</span>
        <input type="text" name="title" maxlength="200" required value="${e(draft?.title ?? row.title ?? "")}"></label>
      <label class="lea-field"><span>描述</span>
        <textarea name="description" rows="3" maxlength="2000">${e(draft?.description ?? row.description ?? "")}</textarea></label>
      <label class="lea-field"><span>进展</span>
        <textarea name="progress_note" rows="3" maxlength="2000">${e(draft?.progress_note ?? row.progress_note ?? "")}</textarea></label>
      <div class="lea-form-actions">
        <button type="submit" class="lea-btn is-primary"${data.writing ? " disabled" : ""}>保存修订</button>
        <button type="button" class="lea-btn" data-action="cancel-revision"${data.writing ? " disabled" : ""}>取消</button>
      </div></form>`;
  }

  /* 审阅队列。每一条是一条**待裁决的进展**，不是一条记录：
     它挂在某个目标上，带自己的依据来源。依据读不出来时只剩「不采纳」——
     后端就是这么给的（`progress_management.py:94`），页面照实显示。 */
  function reviewsView() {
    if (!data.reviews.length) {
      return `<div class="lea-reviews-empty">${ICON.check}
        <strong>没有待审阅的进展</strong>
        </div>`;
    }
    return `<div class="lea-reviews">
      <p class="lea-reviews-sum"><strong>${data.reviews.length}</strong> 条待审阅</p>
      ${data.reviews.map((row, index) => {
        const blind = !!row.unavailable_reason;
        return `<article class="lea-review${blind ? " is-blind" : ""}" style="--i:${index}"
          data-view-key="${e(row._rowKey||row.review_id)}">
          <header>
            <span class="lea-review-target">${e(row.growth_title || "目标已不可读取")}</span>
            <span class="lea-review-time">${e(row._source?.label||"")}</span>
            <span class="lea-rev">依据 v${e(row.base_revision)}</span>
            <span class="lea-review-time">${e(when(row.created_at))}</span>
          </header>
          ${blind ? `<p class="lea-card-why">${ICON.sourceKey}<span>${e(reason(row.unavailable_reason))}——只能不采纳。</span></p>`
            : row.progress_note ? `<p class="lea-review-note">${e(row.progress_note)}</p>`
            : ``}
          <footer>
            ${(row.actions || []).map(a => `<button type="button"
              class="lea-btn${REVIEW_ACTIONS[a]?.kind === "primary" ? " is-primary" : REVIEW_ACTIONS[a]?.kind === "quiet" ? " is-quiet" : ""}"
              data-review="${e(a)}" data-id="${e(row._rowKey||row.review_id)}"${data.writing ? " disabled" : ""}>
              <span>${e(REVIEW_ACTIONS[a]?.label || "审阅记录")}</span></button>`).join("")}
          </footer>
          ${(row.actions || []).length === 1 && row.actions[0] === "reject"
            ? `<p class="lea-review-warn">依据不可读取，无法采纳。</p>` : ""}
        </article>`;
      }).join("")}
      ${data.reviewNext ? `<button type="button" class="lea-btn is-wide" data-action="more-reviews"${data.loadingReviews ? " disabled" : ""}>读取下一页审阅</button>` : ""}
    </div>`;
  }

  /* 来源与凭证。这一屏是**解释性**的：它说明为什么有的记录读不出内容，
     以及来源凭证从哪里来。不摆假控件——页面确实拼不出 ScopeRef/VersionRef。 */
  function sourcesView() {
    const blind = data.items.filter(row => row.unavailable_reason);
    return `<div class="lea-sources">
      <section class="lea-block">
        <h3>当前读不到内容的记录</h3>
        ${blind.length ? `
          <ul class="lea-blind-list">${blind.map((row,index) => `<li>
            <strong>成长记录 ${index+1}</strong>
            <span>${e(reason(row.unavailable_reason))}</span>
            <em>${e(row.source_count ?? 0)} 条来源 · v${e(row.revision)}</em>
            <button type="button" class="lea-mini" data-open="${e(rowKey(row))}">查看</button></li>`).join("")}</ul>`
        : `<p class="lea-note is-ok">${ICON.check}<span>当前所有记录的来源都可读取。</span></p>`}
      </section>
    </div>`;
  }

  function body() {
    if (!data.ready) return emptyMarkup();
    if (data.detailId) return detailView();
    return {"track":trackView, "reviews":reviewsView, "sources":sourcesView}[data.view]();
  }
  function render() {
    if (!root || !data) return;
    const view = snapshotView();
    if(data.layout==="chain"){
      root.innerHTML=`<div class="lea-chain-workspace">${gateView()}${!data.ready?emptyMarkup():chainWorkView()}</div>`;
      restoreView(view);
      return;
    }
    root.innerHTML = `<div class="lea-app">${railMarkup()}<div class="lea-main">${headMarkup()}${noticeMarkup()}${gateView()}${body()}</div></div>`;
    restoreView(view);
  }

  /* ---- 交互 ------------------------------------------------------------ */
  /* 只注册一个 click：确认条的按键与其余点击**必须**在同一个处理器里按顺序判，
     分成两个监听就会依赖注册顺序，而且卸载时要记得摘两次。
     确认条排在最前面——它浮在别的东西上面，优先级最高。 */
  function onClick(event) {
    if (event.target.closest("[data-confirm]")) { confirmClick(event); return; }
    clicked(event);
  }
  function clicked(event) {
    if(data.writing)return;
    const view = event.target.closest("[data-view]");
    if (view) {
      data.view = view.dataset.view; data.detailId = null; data.form = null; data.notice = ""; render();
      if (data.view === "reviews") loadReviews();
      return;
    }
    const action = event.target.closest("[data-action]")?.dataset.action;
    if (action === "refresh") {
      data.notice = "";
      if (data.view === "reviews" || data.view === "track") loadReviews({mode:"refresh"});
      load({mode:"refresh"}); return;
    }
    if (action === "dismiss") { data.notice = ""; render(); return; }
    if (action === "cancel-revision") {data.form=null;render();return;}
    if (action === "more") { loadMore(); return; }
    if (action === "more-reviews") { loadMoreReviews(); return; }
    if (action === "close-detail") { data.detailId = null; data.form = null; render(); return; }
    const filterState = event.target.closest("[data-filter-state]")?.dataset.filterState;
    if (filterState !== undefined) { data.stateFilter = filterState; data.detailId=null; data.form=null; load(); return; }
    const filterKind = event.target.closest("[data-filter-kind]")?.dataset.filterKind;
    if (filterKind !== undefined) { data.kindFilter = filterKind; data.detailId=null; data.form=null; load(); return; }
    const open = event.target.closest("[data-open]")?.dataset.open;
    if (open) { data.detailId = open; data.detail = item(open); data.form = null; data.evidence=[];
      if(data.layout==="chain")void loadEvidence(open);render();return; }
    /* 状态动作。卡片上的与详情里的是同一条路径。
       变量名不能叫 `act`：那是本模块的状态动作函数，
       同名会把它遮蔽掉，`act(id, name)` 就变成「调用一个 dataset」。 */
    const actionButton = event.target.closest("[data-act]");
    if (actionButton) {
      event.stopPropagation();
      const id = actionButton.dataset.id, name = actionButton.dataset.act;
      if (name === "revise") {
        if(data.detailId!==id)data.form=null;
        data.detailId = id; data.detail = item(id); render();
        root.querySelector(".lea-revise input")?.focus();
        return;
      }
      if (name === "withdraw" || name === "retire") {
        /* 两个不可逆动作走一次确认：它们把记录移出活跃列表。
           确认条在链式版式里挂在详情下方，与点下去的那颗按钮不在同一个容器，
           所以渲染完把焦点交给「确认」，键盘用户不用重新找。 */
        data.notice = ""; data.confirm = {id, name}; render();
        root?.querySelector?.('[data-confirm="yes"]')?.focus?.({preventScroll:true});
        return;
      }
      act(id, name); return;
    }
    const reviewAction = event.target.closest("[data-review]");
    if (reviewAction) {
      const row = data.reviews.find(r => r._rowKey === reviewAction.dataset.id || r.review_id === reviewAction.dataset.id);
      if (row) review(row, reviewAction.dataset.review);
      return;
    }
    /* 点卡片本体开详情；点按钮不算（上面已经 stopPropagation）。 */
    const card = event.target.closest("[data-id]");
    if (card && !event.target.closest("[data-act]")) {
      data.detailId = card.dataset.id; data.detail = item(card.dataset.id); data.form = null; render();
    }
  }
  /* 确认条用 state 里的一条标记驱动，而不是 document.createElement：
     这一页其余部分全是字符串模板，混进一个 DOM 构造会让测试夹具
     必须多实现一套 API，而多出来的那套没有别的东西在验证。
     渲染位置跟着版式走，但三个入口都用同一个 confirmMarkup：
     轨道视图挂在被点卡片所属那一段下方（trackView），
     详情视图与链式版式挂在详情动作区下方（detailView）——
     后两者此前漏了，于是链式版式点「归档 / 撤回」只置状态、不发请求。 */
  function confirmMarkup() {
    const pending = data.confirm;
    if (!pending) return "";
    const spec = ACTIONS[pending.name] || {label:pending.name, note:""};
    const row = item(pending.id);
    const stage = row ? (STATE_BY_KEY[row.state] || {}) : {};
    return `<div class="lea-confirm" role="alertdialog" aria-label="确认${e(spec.label)}">
      <span class="lea-confirm-text">${e(spec.note)}</span>
      <span class="lea-confirm-target">${e(stage.label || "")} · ${e(row?.title || (row?.unavailable_reason ? "来源已不可读取的记录" : "当前成长记录"))}</span>
      <button type="button" class="lea-mini is-primary" data-confirm="yes">确认${e(spec.label)}</button>
      <button type="button" class="lea-mini" data-confirm="no">取消</button></div>`;
  }
  function change(event) {
    const input = event.target.closest("[data-access]");
    if (!input) return;
    const source = data.sources.find(item => sourceKey(item) === input.dataset.source);
    if (source) void updateAccess(source, input.dataset.access, input.checked === true);
  }
  function input(event) {
    const form=event.target.closest('[data-form="revise"]');
    if(!form||!["title","description","progress_note"].includes(event.target.name))return;
    const row=data.detail||item(data.detailId);
    if(!row)return;
    if(data.form?.id!==rowKey(row))data.form={id:rowKey(row),title:row.title||"",description:row.description||"",progress_note:row.progress_note||""};
    data.form[event.target.name]=event.target.value;
  }
  function submit(event) {
    const form = event.target.closest('[data-form="revise"]');
    if (!form) return;
    event.preventDefault();
    const row = data.detail || item(data.detailId);
    if (!row) return;
    const values = new FormData(form);
    const title = String(values.get("title") || "").trim();
    if (!title) { notice("标题不能为空。", true); render(); return; }
    act(rowKey(row), "revise", {title, description:String(values.get("description") || ""),
      progress_note:String(values.get("progress_note") || "")});
  }
  function confirmClick(event) {
    const button = event.target.closest("[data-confirm]");
    if (!button || !data.confirm) return;
    const {id, name} = data.confirm;
    data.confirm = null;
    if (button.dataset.confirm === "yes") act(id, name);
    else render();
  }
  function keydown(event) {
    if (event.key === "Escape") {
      if (data.notice) { data.notice = ""; render(); return; }
      if (data.confirm) { data.confirm = null; render(); return; }
      if (data.detailId) { data.detailId = null; data.form = null; render(); return; }
    }
    const card = event.target.closest(".lea-card");
    if (card && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      data.detailId = card.dataset.id; data.detail = item(card.dataset.id); data.form = null; render();
    }
    const rail = event.target.closest("[data-view]");
    if (rail && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
      event.preventDefault();
      const index = VIEWS.findIndex(([id]) => id === data.view);
      const next = (index + (event.key === "ArrowDown" ? 1 : VIEWS.length - 1)) % VIEWS.length;
      data.view = VIEWS[next][0]; data.detailId = null; render();
      root.querySelector('[data-view="' + data.view + '"]')?.focus();
    }
  }

  /* ---- 挂载 ------------------------------------------------------------ */
  async function whenBridgeReady(ticket, timeout = 10000) {
    if (window.AstrBotPluginPage?.apiGet) return window.AstrBotPluginPage;
    const product = window.CompanionProduct;
    if (product?.bridgeNow || product?.bridgeFor) {
      try {
        const found = product.bridgeNow?.() || await product.bridgeFor();
        if (found?.apiGet) return found;
      } catch { /* 交给下面的轮询 */ }
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
  async function mount(host, options = {}) {
    /* 顺序有讲究：unmount() 会 ++version 并清掉 data，所以
       ① 先 unmount，② 再建新 data，③ 才取票号。
       在 unmount() 之前取票会让下面第一处 live(ticket) 立即为假。 */
    unmount();
    root = host; data = blank();
    data.layout=options.layout==="chain"?"chain":"default";
    data.detailId=null;
    data.roleLabel=typeof options.roleLabel==="string"?options.roleLabel:"";
    const ticket = ++version;
    root.addEventListener("click", onClick);
    root.addEventListener("change", change);
    root.addEventListener("input", input);
    root.addEventListener("submit", submit);
    root.addEventListener("keydown", keydown);
    render();
    try {
      bridge = await whenBridgeReady(ticket);
      if (!live(ticket)) return;
      const context = await bounded(bridge.ready());
      if (!live(ticket)) return;
      if (context?.pluginName !== HOST) throw problem("bridge_unavailable");
      const provided=Array.isArray(options.sources)?options.sources:[];
      const sources=provided.map(source=>{
        const absolute=source?.growthApi||source?.api;
        if(typeof absolute!=="string"||!absolute)return null;
        return {...source,path:rel(absolute)};
      }).filter(Boolean);
      if(!sources.length&&typeof options.growthApi==="string"&&options.growthApi){
        const path=rel(options.growthApi);
        sources.push({key:path,path,label:options.roleLabel||"当前记录"});
      }
      if(!sources.length)throw problem("relationship_required");
      data.sources=sources;
      data.path=sources[0].path;
      data.recordFocus=options.recordFocus||null;
      if(typeof options.recordId==="string"&&options.recordId){
        data.recordFocus={growth_id:options.recordId};
      }
      await load(); await loadReviews();
      if (live(ticket)) render();
    } catch (error) {
      if (!live(ticket)) return;
      notice(reason(error.code), true);
      render();
    }
  }
  function unmount() {
    ++version;
    if (!root) { data = null; bridge = null; return; }
    root.removeEventListener("click", onClick);
    root.removeEventListener("change", change);
    root.removeEventListener("input", input);
    root.removeEventListener("submit", submit);
    root.removeEventListener("keydown", keydown);
    root = null; bridge = null; data = null;
  }
  return {mount, unmount,
    get state() { return {data}; },
    _internals:{STAGES, RETIRED, ACTIONS, REVIEW_ACTIONS, KIND_LABEL, when}};
})();
