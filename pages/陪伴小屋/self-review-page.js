/* Daily review queue for evidence-bound role guidance. */
"use strict";
window.CompanionSelfReviewPage = (() => {
  const HOST = "astrbot_plugin_private_companion";
  const PREFIX = "/" + HOST + "/";
  const errors = {
    dashboard_identity_required:"当前登录状态不可用，请重新打开陪伴小屋。",
    staging_not_initialized:"角色数据尚未加载，请重新读取。",
    staging_host_terminated:"陪伴服务已停止，请重新读取。",
    relationship_required:"当前角色还没有可用的复盘权限。",
    growth_management_scope:"当前范围不能管理这个角色的成长记录。",
    growth_target_changed:"角色或对象已经切换，请重新进入这一页。",
    source_scope_mismatch:"来源与当前角色不在同一个会话范围，无法继续。",
    self_review_query_invalid:"复盘列表条件无效，请刷新后重试。",
    self_review_command_invalid:"复盘操作未被接受，请刷新后重试。",
    self_review_item_invalid:"这条复盘记录的条件无效，请刷新后重试。",
    self_review_item_unavailable:"这条复盘记录的来源已不可用。",
    self_review_evidence_unavailable:"这条复盘记录的原始依据已不可读取。",
    self_review_source_scope:"复盘依据属于另一个会话范围，不能用于当前角色。",
    self_review_reply_owner_unavailable:"复盘依据对应的回复已不可读取，请稍后重试。",
    self_review_view_stale:"复盘内容已有变化，请刷新后重新核对。",
    self_review_evidence_changed:"原始依据已变化或撤回，不能继续采纳。",
    self_review_expired:"这条短期建议已过期。",
    self_review_decided:"这条建议已经处理。",
    core_access_required:"当前账号没有复盘管理权限。",
    binding_revoked:"管理账号的授权已撤销。",
    binding_rebound:"管理账号的绑定已变化，请重新进入。",
    bridge_unavailable:"页面连接暂不可用，请重新读取。",
    bridge_timeout:"未收到服务确认，请刷新核对。",
    invalid_response:"服务返回的数据不完整，请刷新核对。",
  };
  /* 请求根本没走完与「后端明确拒绝」是两件事：前者没有错误码，不能借用后者的话。 */
  const UNFINISHED = "读取失败：页面与服务之间的请求没有完成。";
  const CODE = /^[a-z][a-z0-9_]{2,60}$/;
  const KIND = {try_next_time:"下次尝试", keep:"值得保持", ask_user:"需要先询问", no_change:"无需调整"};
  const REVIEW_STATE = {not_run:"还没运行过", failed:"上次失败", blocked:"来源校验未通过",
    no_reviewable_window:"暂无可复盘窗口", already_attempted:"该窗口已复盘过",
    succeeded:"最近一次已产出", completed:"最近一次已产出"};
  const e = value => String(value ?? "").replace(/[&<>"']/g, c =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;", "'":"&#39;"}[c]));
  const problem = code => Object.assign(new Error(reason(code)), {code});
  const when = value => typeof value === "string" && Number.isFinite(Date.parse(value))
    ? new Date(value).toLocaleString("zh-CN", {hour12:false, month:"numeric", day:"numeric", hour:"2-digit", minute:"2-digit"})
    : "时间未知";
  let root = null, bridge = null, generation = 0, operation = 0, data = null;

  function fresh() {
    return {sources:[], items:[], stopped:[], policy:null, status:null, phase:"loading",
      loading:false, writing:false, confirming:"", notice:"", error:false, sourceErrors:[], expired:0};
  }
  function live(ticket) { return ticket === generation && root?.isConnected; }
  function note(message, error = false) { data.notice = message; data.error = error; }
  function rel(absolute) {
    if (typeof absolute !== "string" || !absolute.startsWith(PREFIX)) throw problem("invalid_response");
    const path = absolute.slice(PREFIX.length);
    if (!path || /[?#%\\:]/.test(path) || path.split("/").some(part => !part || part === "." || part === "..")) {
      throw problem("invalid_response");
    }
    return path;
  }
  async function bounded(promise) {
    let timeout;
    try {
      return await Promise.race([promise, new Promise((_, reject) => {
        timeout = setTimeout(() => reject(problem("bridge_timeout")), 15000);
      })]);
    } finally { clearTimeout(timeout); }
  }
  async function ready(ticket) {
    if (window.AstrBotPluginPage?.apiGet) return window.AstrBotPluginPage;
    const product = window.CompanionProduct;
    try {
      const found = product?.bridgeNow?.() || await product?.bridgeFor?.();
      if (found?.apiGet) return found;
    } catch { /* wait for the host bridge below */ }
    return new Promise((resolve, reject) => {
      const started = Date.now();
      const tick = () => {
        if (!live(ticket)) return reject(problem("bridge_unavailable"));
        if (window.AstrBotPluginPage?.apiGet) return resolve(window.AstrBotPluginPage);
        if (Date.now() - started >= 10000) return reject(problem("bridge_unavailable"));
        setTimeout(tick, 80);
      };
      tick();
    });
  }
  async function api(source, method, suffix, body) {
    const response = await bounded(method === "GET"
      ? bridge.apiGet(source.path + suffix, body)
      : bridge.apiPost(source.path + suffix, body));
    const wrapped = ["ok", "error"].includes(response?.status) && response?.data !== undefined ? response
      : ["ok", "error"].includes(response?.data?.status) && response?.data?.data !== undefined ? response.data : null;
    if (wrapped?.status === "error") {
      const code = wrapped.data?.code || wrapped.message || "invalid_response";
      throw problem(CODE.test(code) ? code : "invalid_response");
    }
    const value = wrapped ? wrapped.data : response;
    if (!value || typeof value !== "object") throw problem("invalid_response");
    return value;
  }
  function reason(code) {
    if (!code) return UNFINISHED;
    return errors[code] || "服务返回了未收录的状态（" + code + "），请刷新核对。";
  }
  /* 后端把这一角色最近一次复盘的结果随列表一起返回（state + 失败码）；页面此前
     从未消费它，于是「复盘机制在，但最近一次没跑成」这件事一直看不出来。 */
  function reviewLabel(status) {
    if (!status || typeof status !== "object" || typeof status.state !== "string" || !status.state) return "—";
    const label = REVIEW_STATE[status.state] || "状态未收录";
    return typeof status.code === "string" && status.code ? `${label}（${status.code}）` : label;
  }
  /* 一条建议的身份是「哪个记录归属 + 哪个 item_id」：同一批数据可能同时出现在
     活动列表与已停止列表里，跨来源的 item_id 也可能撞号。 */
  function itemKey(item) { return `${item?._sourceKey || ""}\u0000${item?.item_id || ""}`; }
  /* 按 key 合并：新响应里的字段覆盖旧的，旧条目只要还在旧列表里就保留。
     于是「某一页这次变短了」或「某个来源这次失败了」都不会让已经显示的条目消失。 */
  function mergeRows(previous, incoming) {
    const merged = previous.slice();
    const positions = new Map(merged.map((item, position) => [itemKey(item), position]));
    incoming.forEach(item => {
      const position = positions.get(itemKey(item));
      if (position === undefined) { positions.set(itemKey(item), merged.length); merged.push(item); }
      else merged[position] = item;
    });
    return merged;
  }
  /* 重绘是整块 innerHTML（见 render），滚动位置只留在数字上：列表上方高度一变，
     用户看到的就是另一批建议。重绘前记住「视口里第一条建议 + 它在屏幕上的位置
     + 展开了哪些依据块」，重绘后按同一把 key 复位。没有 DOM 的测试夹具会安全跳过。 */
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
  function actions(item, sourceKey) {
    const actionKey = sourceKey + "\u0000" + item.item_id;
    if (item.unavailable_reason) return `<span class="sr-state is-stopped">证据失效 · 暂停注入</span>`;
    if (item.state !== "pending") return `<span class="sr-state${item.expires_at&&Date.parse(item.expires_at)<=Date.now()?" is-stopped":""}">${item.kind === "no_change" && item.state === "rejected" ? "已收起" : item.state === "accepted" ? item.expires_at&&Date.parse(item.expires_at)<=Date.now()?"已过期":"生效中" : "已驳回"}</span>`;
    if (data.confirming === actionKey) return `<div class="sr-actions">
      <span>采纳后会在短期内作为该角色的工作指导。</span>
      <button class="button small primary" type="button" data-review-action="confirm" data-review-id="${e(item.item_id)}" data-review-source="${e(sourceKey)}">确认采纳</button>
      <button class="button small" type="button" data-review-action="cancel" data-review-id="${e(item.item_id)}" data-review-source="${e(sourceKey)}">取消</button></div>`;
    const choices = item.actions || [];
    return `<div class="sr-actions">${choices.includes("accept")
      ? `<button class="button small primary" type="button" data-review-action="accept" data-review-id="${e(item.item_id)}" data-review-source="${e(sourceKey)}">采纳为短期指导</button>` : ""}${choices.includes("reject")
      ? `<button class="button small" type="button" data-review-action="reject" data-review-id="${e(item.item_id)}" data-review-source="${e(sourceKey)}">${item.kind === "no_change" ? "收起结论" : "驳回"}</button>` : ""}</div>`;
  }
  function card(item) {
    const unavailable = item.unavailable_reason;
    const basis = Array.isArray(item.evidence) ? item.evidence : [];
    const confidence = Number.isFinite(Number(item.confidence)) ? Math.round(Number(item.confidence) * 100) : null;
    const expired = item.expires_at && Date.parse(item.expires_at) <= Date.now();
    return `<article class="sr-item" data-view-key="${e(itemKey(item))}">
      <header class="sr-item-head"><span class="sr-kind">${e(KIND[item.kind] || "复盘建议")}</span>
        <span class="sr-source">记录归属 · ${e(item._sourceLabel || "数据来源")}</span>
        <time datetime="${e(item.created_at || "")}">${e(when(item.created_at))}</time>
        ${confidence === null ? "" : `<span class="sr-confidence">置信度 ${confidence}%</span>`}
        ${expired ? `<span class="sr-expired">已过期</span>` : ""}</header>
      ${item.summary ? `<div class="sr-summary"><strong>本次复盘</strong><p>${e(item.summary)}</p></div>` : ""}
      ${unavailable ? `<p class="sr-unavailable">${e(reason(unavailable))}详情已隐藏。</p>` : `<div class="sr-review-body">
        <div><h3>观察</h3><p>${e(item.observation || "暂无观察内容")}</p></div>
        <div><h3>建议</h3><p>${e(item.next_action || "暂无后续建议")}</p></div>
      </div>${basis.length ? `<details class="sr-evidence"><summary>引用依据 · ${basis.length} 条</summary><ol>${basis.map(text => `<li>${e(text)}</li>`).join("")}</ol></details>` : ""}`}
      ${actions(item, item._sourceKey)}
    </article>`;
  }
  function render() {
    if (!root || !data) return;
    const view = snapshotView();
    const valid=data.items.filter(item=>!item.unavailable_reason&&item.expires_at&&Date.parse(item.expires_at)>Date.now());
    const policy=data.policy||{};
    const duration=seconds=>Number.isFinite(seconds)?seconds%3600===0?`${seconds/3600} 小时`:seconds%60===0?`${seconds/60} 分钟`:`${seconds} 秒`:"—";
    /* 读取失败时计数没有可信来源：写 0 等于把「没读到」说成「没有」。 */
    const count=value=>data.phase==="failed"?"—":value;
    const metrics=[["自动应用",count(data.items.filter(item=>item.automatic&&!item.unavailable_reason).length),"条"],
      ["生效中",count(valid.length),"条"],["复盘周期",Number.isFinite(policy.window_hours)?`${policy.window_hours} 小时`:"—",""],
      ["空结果重查",duration(policy.empty_retry_seconds),"后重查"]];
    const mechanisms=[
      ["复盘周期",Number.isFinite(policy.window_hours)?`${policy.window_hours} 小时`:"—"],
      ["证据门槛",Number.isInteger(policy.minimum_evidence)?`至少 ${policy.minimum_evidence} 条`:"—"],
      ["空结果重查",duration(policy.empty_retry_seconds)+" 后重查"],
      ["最近一次复盘状态",reviewLabel(data.status)],
      ["指导类型","下次尝试 · 保持 · 询问 · 无需调整"],
      ["生效方式","低风险建议自动应用；询问项进入审阅中心"],
    ];
    /* 读成功且一条都没有，才叫「暂无复盘记录」；读失败不能借这句话收场。 */
    const emptyCopy=data.phase==="loading"?"正在读取…"
      :data.phase==="ready"&&!data.items.length&&!data.stopped.length?"暂无复盘记录":"暂无生效中的指导";
    const active=valid.length?`<div class="sr-list">${valid.map(card).join("")}</div>`:`<div class="sr-empty">${emptyCopy}</div>`;
    const stopped=data.stopped.length?`<section class="sr-stopped"><h3>已停止注入</h3><div class="sr-list">${data.stopped.map(card).join("")}</div></section>`:"";
    /* 读态三分：正在读 / 一条都没读到 / 读到了。整页读不到时，「0 条」与
       「暂无生效中的指导」都会把失败说成空，所以只报错并给一次重试。 */
    const blind=data.phase==="failed"&&!data.items.length&&!data.stopped.length;
    const failed=`<p class="sr-status is-error" role="status" aria-live="polite">${e(data.notice||UNFINISHED)}</p>
      <div class="sr-more"><button class="button small" type="button" data-review-refresh ${data.loading?"disabled":""}>重新读取</button></div>`;
    const read=`${data.notice?`<p class="sr-status${data.error?" is-error":""}" role="status" aria-live="polite">${e(data.notice)}</p>`:""}
      ${data.sources.length?`<section class="sr-kpis" aria-label="复盘状态">${metrics.map(([label,value,unit])=>`<article><span>${label}</span><strong>${e(value)}${unit&&typeof value==="number"?` ${unit}`:""}</strong></article>`).join("")}</section>
        <section class="sr-mechanism"><header><h2>复盘运行机制</h2><span>${data.loading?"":"已连接"}</span></header><div>${mechanisms.map(([label,value])=>`<article><strong>${label}</strong><span>${e(value)}</span></article>`).join("")}</div></section>
        <section class="sr-effective"><header><h2>生效中的自我指导</h2><span>${valid.length} 条</span></header>${active}</section>${stopped}`
        :`<div class="sr-empty">${emptyCopy}</div>`}`;
    root.innerHTML=`<div class="sr-toolbar"><span>${data.sources.length} 个记录归属${data.loading?" · 正在读取":""}</span>
      <button class="lh-refresh" type="button" data-review-refresh aria-label="重新读取" title="重新读取" ${data.loading||data.writing?"disabled":""}><i data-lucide="refresh-cw"></i></button></div>
      ${blind?failed:read}
      ${!blind&&data.sources.some(source=>source.next)?`<div class="sr-more"><button class="button small" type="button" data-review-more ${data.loading?"disabled":""}>加载更多</button></div>`:""}`;
    window.lucide?.createIcons({nodes:root.querySelectorAll("[data-lucide]")});
    restoreView(view);
  }
  let loadGeneration = 0;
  /* refresh 的重读页数有上限：已加载页数会被「加载更多」一路抬高，
     而刷新是写操作之后的常见路径，不能按页数无上限地串行发请求。 */
  const MAX_REFRESH_PAGES = 8;
  /* 三种读法，区别只在「从哪一页开始」与「读到的行怎么并进现有列表」：
       reset   换来源或首次读取：清空重来，从每个来源的第一页开始；
       append  「加载更多」：每个来源各取自己的下一页，按身份去重后接上；
       refresh 重新读取与写操作之后：把**这次已经读过的页数**原地重读一遍，
               新数据按身份覆盖旧行，这次没读到的旧行保留。
     三种模式都不会让已经显示过的建议凭空消失——除非用户自己裁决了它。 */
  async function load({mode = "reset", force = false} = {}) {
    if (data.loading && !force) return;
    if (!data.sources.length) { data.phase = "failed"; note("暂无可读取的复盘记录。", true); render(); return; }
    const ticket = generation;
    const query = ++loadGeneration;
    const reset = mode === "reset", append = mode === "append";
    const previous = reset ? null : data.items.slice();
    data.loading = true;
    if (reset) {
      data.items = []; data.stopped=[];data.expired = 0; data.sourceErrors = [];data.policy=null;
      data.status=null; data.phase = "loading"; note("");
      data.sources.forEach(source => { source.next = ""; source.pages = 0; });
    }
    render();
    const targets = append ? data.sources.filter(source => source.next) : data.sources;
    const fetched = [], failures = [], stoppedKeys = new Set(data.stopped.map(itemKey));
    let expired = 0, succeeded = 0;
    for (let offset = 0; offset < targets.length; offset += 8) {
      const batch = targets.slice(offset, offset + 8);
      const results = await Promise.allSettled(batch.map(async source => {
        const rows = [];
        const loadedBefore = Math.max(1, Number(source.pages || 1));
        let cursor = append ? (source.next || "") : "";
        let first = null, pages = 0;
        const rounds = append ? 1 : reset ? 1 : Math.min(MAX_REFRESH_PAGES, loadedBefore);
        for (let index = 0; index < rounds; index++) {
          const page = await api(source, "GET", "/self-review", {
            state:"active", limit:50, ...(cursor ? {after_id:cursor} : {}),
          });
          if (!Array.isArray(page.items)) throw problem("invalid_response");
          if (!first) first = page;
          pages++;
          rows.push(...page.items);
          cursor = typeof page.next_cursor === "string" ? page.next_cursor : "";
          if (!cursor) break;
        }
        const decided = append?{items:[]}:await api(source,"GET","/self-review",{state:"decided",limit:50});
        /* 「已经读过几页」要累加：append 只读一页，但它把已读页数 +1。 */
        return {source, rows, cursor, pages: append ? loadedBefore + 1 : pages,
          policy:first?.policy||null, status:first?.status||null, decided};
      }));
      if (!live(ticket) || query !== loadGeneration) return;
      results.forEach((settled, index) => {
        const source = batch[index];
        if (settled.status === "rejected") {
          failures.push(`${source.label}：${reason(settled.reason?.code)}`);
          return;
        }
        succeeded++;
        const {rows,cursor,pages,policy,status,decided}=settled.value;
        source.next = cursor;
        source.pages = pages;
        if(!data.policy&&policy)data.policy=policy;
        if(!data.status&&status)data.status=status;
        if (Number.isInteger(decided.expired_in_page)) expired += decided.expired_in_page;
        for (const item of rows) {
          const enriched = {...item, _sourceKey:source.key, _sourceLabel:source.label};
          const isExpired = item.expires_at && Date.parse(item.expires_at) <= Date.now();
          if (item.unavailable_reason || isExpired) {
            const key = itemKey(enriched);
            if (!stoppedKeys.has(key)) { data.stopped.push(enriched); stoppedKeys.add(key); }
          } else fetched.push(enriched);
        }
        for(const item of Array.isArray(decided.items)?decided.items:[]){
          if(item.state==="accepted"&&(item.unavailable_reason||item.expires_at&&Date.parse(item.expires_at)<=Date.now())){
            const enriched={...item,_sourceKey:source.key,_sourceLabel:source.label};
            const key=itemKey(enriched);
            if(!stoppedKeys.has(key)){data.stopped.push(enriched);stoppedKeys.add(key);}
          }
        }
      });
    }
    if (!live(ticket) || query !== loadGeneration) return;
    data.items = (reset ? fetched : mergeRows(previous, fetched))
      .sort((left, right) => Date.parse(right.created_at || "") - Date.parse(left.created_at || ""));
    /* 同一条建议不能同时出现在「生效中」与「已停止注入」两处：
       · 这次读到的行若已恢复可读，就从 stopped 里摘掉——refresh 只增不减会让它重复渲染；
       · 这次被判为失效 / 不可读的行，也不再以旧行留在生效列表里，免得状态说得过时。 */
    const activeKeys = new Set(data.items.map(itemKey));
    data.stopped = data.stopped.filter(item => !activeKeys.has(itemKey(item)));
    const stoppedKeysNow = new Set(data.stopped.map(itemKey));
    data.items = data.items.filter(item => !stoppedKeysNow.has(itemKey(item)));
    data.expired = append ? data.expired + expired : expired;
    data.sourceErrors = failures;
    /* 只要有任意一个归属真的读到了，这一页就是 ready；整轮读不到才是 failed。
       空列表只有读成功之后才能说成「暂无复盘记录」。 */
    if (targets.length) data.phase = succeeded ? "ready" : "failed";
    if (failures.length) note(succeeded
      ? `部分记录归属暂不可读（${failures.length}/${targets.length}）；已显示其余记录。`
      : `复盘记录暂时无法读取：${failures[0]}`, true);
    data.loading = false;
    render();
  }
  function operationKey(action, itemId) {
    return `self-review:${action}:${itemId}:${operation++}`;
  }
  /* 写回执先落到本地那一行：采纳的变成「生效中」，驳回的离开生效列表。
     之后的 refresh 只重读已经读过的页，不再整表重来。 */
  function applyDecision(item, action) {
    if (!item) return;
    if (action === "reject") {
      data.items = data.items.filter(row => itemKey(row) !== itemKey(item));
      return;
    }
    item.state = "accepted";
    item.actions = [];
  }
  async function decide(sourceKey, itemId, action) {
    const item = data.items.find(row => row.item_id === itemId && row._sourceKey === sourceKey);
    const source = data.sources.find(row => row.key === sourceKey);
    if (!item || data.writing || !item.actions?.includes(action)) return;
    if (!source) return;
    const ticket = generation;
    data.writing = true;
    data.confirming = "";
    note("");
    render();
    try {
      await api(source, "POST", "/self-review/command", {
        item_id:item.item_id, action, view_token:item.view_token,
        operation_key:operationKey(action, item.item_id),
      });
      if (!live(ticket)) return;
      applyDecision(item, action);
      await load({mode:"refresh", force:true});
      if (live(ticket)) note(action === "accept" ? "已采纳，短期指导已生效。" : item.kind === "no_change" ? "结论已收起。" : "已驳回。");
    } catch (failure) {
      if (live(ticket)) note(reason(failure.code), true);
    } finally {
      if (live(ticket)) { data.writing = false; render(); }
    }
  }
  function onClick(event) {
    if (event.target.closest("[data-review-refresh]")) { load({mode:"refresh", force:true}); return; }
    if (event.target.closest("[data-review-more]")) { load({mode:"append"}); return; }
    const action = event.target.closest("[data-review-action]");
    if (!action) return;
    const itemId = action.dataset.reviewId;
    const sourceKey = action.dataset.reviewSource;
    const actionKey = sourceKey + "\u0000" + itemId;
    if (action.dataset.reviewAction === "accept") { data.confirming = actionKey; render(); }
    else if (action.dataset.reviewAction === "cancel") { data.confirming = ""; render(); }
    else if (action.dataset.reviewAction === "confirm") decide(sourceKey, itemId, "accept");
    else if (action.dataset.reviewAction === "reject") decide(sourceKey, itemId, "reject");
  }
  async function mount(host, options = {}) {
    unmount();
    root = host; data = fresh();
    const seen = new Set();
    for (const [index, item] of (Array.isArray(options.sources) ? options.sources : []).entries()) {
      try {
        const path = rel(item?.api);
        const key = typeof item.key === "string" && item.key ? item.key : path;
        if (seen.has(key)) continue;
        seen.add(key);
        data.sources.push({path, key, label:typeof item.label === "string" && item.label ? item.label : `角色 ${index + 1}`, next:"", pages:0});
      } catch { /* an invalid manifest entry cannot become a request route */ }
    }
    const ticket = ++generation;
    root.addEventListener("click", onClick);
    render();
    try {
      bridge = await ready(ticket);
      if (!live(ticket)) return;
      const context = await bounded(bridge.ready());
      if (!live(ticket)) return;
      if (context?.pluginName !== HOST) throw problem("bridge_unavailable");
      await load();
    } catch (failure) {
      if (live(ticket)) { data.phase = "failed"; note(reason(failure.code), true); data.loading = false; render(); }
    }
  }
  function unmount() {
    generation++;
    loadGeneration++;
    if (root) root.removeEventListener("click", onClick);
    root = null; bridge = null; data = null;
  }
  return {mount, unmount, get state() { return {data}; }};
})();
