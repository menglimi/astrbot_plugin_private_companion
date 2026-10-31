"use strict";
window.CompanionExpressionSignalsPage = (() => {
  const PREFIX = "/astrbot_plugin_private_companion/";
  const DIMENSION = {
    sentence_rhythm: "句读节奏", emoji: "表情符号", punctuation: "标点",
    directness: "直接程度", diction: "措辞", formatting: "排版", other: "其他",
  };
  const CHANNEL = {private:"私聊", group:"群聊", proactive:"主动消息", qzone:"QQ 空间", tts:"语音"};
  const RELATIONSHIP = {stranger:"陌生", familiar:"熟悉", close:"亲近", any:"不限"};
  const EMOTION = {normal:"常态", positive:"积极", low:"低落", guarded:"谨慎", any:"不限"};
  const INTENT = {acknowledgement:"确认接话", question:"提问", request:"请求", help:"求助", comfort:"安慰",
    play:"玩笑", tease:"打趣", intimacy:"亲昵", boundary:"边界", emotion:"情绪表达", casual:"闲聊",
    proactive:"主动联系", any:"不限"};
  const STATE_FILTERS = [["pending", "待审"], ["active", "已启用"], ["retired", "已停用"], ["rejected", "已拒绝"]];
  const FILTERS = [
    ["all", "全部"], ...Object.entries(DIMENSION),
  ];
  const ERRORS = {
    bridge_unavailable: "页面连接暂不可用，请重新读取。",
    bridge_timeout: "页面连接超时，请重新读取。",
    expression_query_invalid: "规则筛选条件无效，请重新读取。",
    expression_revision_invalid: "请填写适用情境、具体说法或句式及使用方式。",
    expression_capacity_reached: "规则库已满，可先停用暂时不用的规则。",
    expression_duplicate_rule: "已有相同情境与说法的规则，请先合并或停用其中一条。",
    expression_view_stale: "规则已有变化，请重新读取后再操作。",
    expression_sample_changed: "依据已变化，规则暂不能恢复。",
    expression_signal_unavailable: "表达规则已不在原数据来源内。",
    expression_decided: "规则状态已经改变，请重新读取。",
    expression_pack_invalid: "分享包格式无效或版本不受支持。",
    expression_import_stale: "导入目标或规则库已变化，请重新预览。",
    expression_export_limit: "单次最多导出 200 条规则，请选择部分规则。",
    expression_export_invalid: "请选择不超过 200 条有效规则。",
    expression_export_empty: "没有来源仍有效的已启用规则可导出。",
    expression_operation_conflict: "导入操作编号已用于其他分享包，请重新预览。",
    expression_group_context_required: "该群还没有可用于表达学习的对话，先在该群正常聊一次再读取。",
    role_binding_missing: "这个来源还没有绑定角色，先在对应会话里正常聊一次，或重新读取角色与会话。",
    group_reply_target_required: "这个群目标与当前账号或角色不匹配，请重新读取角色与会话。",
    staging_not_initialized: "角色数据尚未加载，请重新读取。",
    staging_host_terminated: "陪伴服务已停止，请重新读取。",
    dashboard_identity_required: "当前登录状态不可用，请重新打开陪伴小屋。",
  };
  const e = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c]));
  const relative = value => typeof value === "string" && value.startsWith(PREFIX) ? value.slice(PREFIX.length) : "";
  const errorCode = value => typeof value === "string" && /^[a-z][a-z0-9_]{2,80}$/.test(value) ? value : "";
  const reason = value => ERRORS[errorCode(value)] || "原始依据已不可读取。";
  const formatTime = value => typeof value === "string" && Number.isFinite(Date.parse(value))
    ? new Date(value).toLocaleString("zh-CN", {hour12: false, month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit"})
    : "时间未知";
  const operationKey = action => `expression-${action}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

  let root = null;
  let mountTicket = 0;
  let readTicket = 0;
  let apiBase = "";
  let scopeKey = "";
  let sources = [];
  let importSourceKey = "";
  let filter = "all";
  let stateFilter = "active";
  let pages = {};
  let items = [];
  let capacity = null;
  let capacityBySource = {};
  let countsBySource = {};
  let busy = false;
  let writing = false;
  let notice = "";
  let error = false;
  let readIssues = [];
  let confirming = null;
  let transferOpen = false;
  let transferBusy = false;
  let transferNotice = "";
  let transferError = false;
  let importJSON = "";
  let importPreview = null;
  let exportPackage = null;
  let selectedExportIds = new Set();
  let editor = null;
  let kindFilter = "all";
  let sourceFilter = "all";

  function pageState() {
    const states = [stateFilter === "active" ? "adopted" : stateFilter];
    return Object.fromEntries(sources.map(source => [source.key,
      Object.fromEntries(states.map(state => [state, {items: [], cursor: "", done: false, loaded: false}]))]));
  }
  const sourceLabel = source => source?.label || "未命名范围";
  /* page bridge 只带 message 的旧形态也要能认出机器码；来源名优先用 broker
     回填的标签，它对应本次请求实际选择的目标与角色。 */
  const issueCode = problem => errorCode(problem?.code) || errorCode(problem?.message) || "";
  const issueLabel = (source, problem) => {
    const supplied = typeof problem?.sourceLabel === "string" ? problem.sourceLabel.trim() : "";
    return supplied.slice(0, 160) || sourceLabel(source);
  };
  const selectedImportSource = () => sources.find(source => source.key === importSourceKey) || sources[0] || null;
  function mounted(current = mountTicket) { return !!root?.isConnected && current === mountTicket; }
  async function bounded(promise) {
    let timer;
    try {
      return await Promise.race([promise, new Promise((_, reject) => {
        timer = setTimeout(() => reject(Object.assign(new Error("bridge_timeout"), {code: "bridge_timeout"})), 15000);
      })]);
    } finally { clearTimeout(timer); }
  }
  async function api(method, route, body) {
    const product = window.CompanionProduct;
    const bridge = product?.bridgeNow?.() || await product?.bridgeFor?.();
    if (!bridge?.apiGet) throw Object.assign(new Error("bridge_unavailable"), {code: "bridge_unavailable"});
    const response = await bounded(method === "GET" ? bridge.apiGet(route, body) : bridge.apiPost(route, body));
    if (response?.status === "error") {
      const detail = response.data && typeof response.data === "object" ? response.data : {};
      throw Object.assign(new Error(response.message || detail.code || "request_failed"), {code: errorCode(detail.code || response.code || response.message)});
    }
    let value = response;
    if (response?.status === "ok" && response.data !== undefined) value = response.data;
    else if (response?.data !== undefined && response?.status === undefined) value = response.data;
    value = product?.unwrap ? product.unwrap(value) : value;
    if (value?.status === "error") {
      const detail = value.data && typeof value.data === "object" ? value.data : {};
      throw Object.assign(new Error(value.message || detail.code || "request_failed"), {code: errorCode(detail.code || value.code || value.message)});
    }
    if (value?.status === "ok" && value.data !== undefined) value = value.data;
    if (!value || typeof value !== "object") throw Object.assign(new Error("invalid_response"), {code: "invalid_response"});
    return value;
  }
  function queryFor(state, cursor) {
    const query = {state, limit: 50};
    if (filter !== "all") query.dimension = filter;
    if (cursor) query.after_id = cursor;
    return query;
  }
  function rebuildItems() {
    const seen = new Set();
    items = Object.values(pages).flatMap(page => Object.values(page).flatMap(state => state.items)).filter(item => {
      const id = String(item?._key || item?.signal_id || "");
      if (!id || seen.has(id)) return false;
      seen.add(id); return true;
    }).sort((left, right) => {
      const a = Date.parse(left.decided_at || left.created_at || "") || 0;
      const b = Date.parse(right.decided_at || right.created_at || "") || 0;
      return b - a || String(right.signal_id).localeCompare(String(left.signal_id));
    });
  }
  function hasMore() { return Object.values(pages).some(page => Object.values(page).some(value => value.cursor && !value.done)); }
  async function read(current = mountTicket, {append = false} = {}) {
    if (!mounted(current) || !sources.length || writing || editor || (append && busy)) return false;
    const request = ++readTicket;
    const requestedFilter = filter;
    const requestedStateFilter = stateFilter;
    if (!append) { pages = pageState(); items = []; capacity = null; capacityBySource = {}; countsBySource={}; confirming = null; notice = ""; error = false; readIssues = []; selectedExportIds = new Set(); }
    const requestsToRead = sources.flatMap(source => Object.keys(pages[source.key] || {})
      .filter(state => !pages[source.key][state].loaded
        || append && pages[source.key][state].cursor && !pages[source.key][state].done)
      .map(state => ({source,state})));
    if (!requestsToRead.length) { busy = false; rebuildItems(); render(); return true; }
    busy = true; render();
    const results = await Promise.allSettled(requestsToRead.map(async ({source,state}) => {
      const page = await api("GET", source.apiBase + "/style", queryFor(state, pages[source.key][state].cursor));
      if (!Array.isArray(page.items)) throw Object.assign(new Error("invalid_response"), {code: "invalid_response"});
      return {source,state,page};
    }));
    if (!mounted(current) || request !== readTicket || requestedFilter !== filter
        || requestedStateFilter !== stateFilter) return false;
    const discovered = [];
    results.forEach((result, index) => {
      const {source,state} = requestsToRead[index];
      if (result.status === "rejected") {
        const code = issueCode(result.reason);
        discovered.push({kind: "error", label: issueLabel(source, result.reason), code,
          detail: reason(code || result.reason?.message)});
        return;
      }
      const page = result.value.page;
      const target = pages[source.key][state];
      if (typeof page.unavailable_reason === "string" && page.unavailable_reason) {
        const code = errorCode(page.unavailable_reason);
        discovered.push({kind: "unavailable", label: sourceLabel(source), code,
          detail: reason(code || page.unavailable_reason)});
        target.cursor = ""; target.done = true; target.loaded = true;
        return;
      }
      if(page.counts && typeof page.counts==="object") countsBySource[source.key]=page.counts;
      if (page.capacity && Number.isInteger(page.capacity.active) && Number.isInteger(page.capacity.limit)) {
        capacityBySource[source.key] = page.capacity;
      }
      if (Number.isInteger(page.legacy_orphan_count) && page.legacy_orphan_count > 0) {
        discovered.push({kind: "legacy", label: sourceLabel(source), code: "legacy_expression_scope",
          count: page.legacy_orphan_count,
          detail: `${page.legacy_orphan_count} 条旧版群规则保存在旧作用域，暂未生效；需要单独迁移后才能读取和生效。`});
      }
      const incoming = page.items.map(item => ({...item, _state: state, _source:source,
        _key:source.key + "::" + item.signal_id}));
      target.items = append ? target.items.concat(incoming) : incoming;
      target.cursor = typeof page.next_cursor === "string" && page.next_cursor ? page.next_cursor : "";
      target.done = !target.cursor;
      target.loaded = true;
    });
    const capacities=Object.values(capacityBySource);
    capacity=capacities.length?capacities.reduce((total,value)=>({active:total.active+value.active,limit:total.limit+value.limit}),{active:0,limit:0}):null;
    rebuildItems(); busy = false;
    readIssues = append ? readIssues.concat(discovered.filter(issue =>
      !readIssues.some(existing => existing.kind === issue.kind && existing.label === issue.label)))
      : discovered;
    const failedCount = readIssues.filter(issue => issue.kind === "error").length;
    const unavailableCount = readIssues.filter(issue => issue.kind === "unavailable").length;
    const legacyRules = readIssues.filter(issue => issue.kind === "legacy")
      .reduce((total, issue) => total + (Number(issue.count) || 0), 0);
    if (failedCount) {
      notice = failedCount === sources.length
        ? "全部来源都没有读取成功，请检查来源配置后重新读取。"
        : `${sources.length} 个来源中 ${failedCount} 个读取失败，已显示可用记录。`;
      error = true;
    } else if (legacyRules) {
      notice = `${legacyRules} 条旧版群规则保存在旧作用域，暂未生效。`;
      error = false;
    } else if (unavailableCount) {
      notice = `${unavailableCount} 个来源还没有可读取的表达学习记录，已显示其他来源。`;
      error = false;
    }
    render(); return failedCount < sources.length;
  }
  function renderEvidence(item) {
    const evidence = Array.isArray(item.evidence) ? item.evidence : [];
    if (!evidence.length || item.unavailable_reason) return "";
    return `<details class="lh-signal-evidence"><summary>依据 · ${evidence.length} 条</summary><ol>${evidence.slice(0, 8).map(entry => `<li>${e(entry)}</li>`).join("")}</ol></details>`;
  }
  function renderApplicability(app) {
    if (!app || typeof app !== "object") return "";
    const list = (value, labels) => Array.isArray(value) && value.length
      ? value.map(item => labels[item] || item).join("、") : "不限";
    const conditions = [
      ["频道", list(app.channels, CHANNEL)],
      ["关系", list(app.relationship_stages, RELATIONSHIP)],
      ["情绪", list(app.emotion_gates, EMOTION)],
      ["意图", INTENT[app.intent] || app.intent || "不限"],
    ];
    if (Array.isArray(app.keywords) && app.keywords.length) {
      conditions.push(["关键词", app.keywords.join("、")]);
    }
    return `<div class="lh-signal-applicability" aria-label="适用条件"><span class="lh-signal-applicability-label">适用条件</span>${conditions.map(([label, value]) => `<span class="lh-signal-condition"><b>${e(label)}</b>${e(value)}</span>`).join("")}</div>`;
  }
  function renderItem(item) {
    const adopted = item.state === "adopted";
    const pending = item.state === "pending";
    const unavailable = !!item.unavailable_reason;
    const actions = Array.isArray(item.actions) ? item.actions : [];
    const confirm = confirming === item._key ? `<div class="lh-signal-confirm" role="alertdialog">
      <span>停用这条规则？</span><button type="button" data-signal-confirm="yes" data-id="${e(item._key)}">确认</button>
      <button type="button" data-signal-confirm="no">取消</button></div>` : "";
    const form = item.expression;
    const title = unavailable ? "依据已失效" : (form?.pattern || item.proposal || item.observation || "表达规则");
    const body = unavailable ? "" : (form?.situation || item.observation || "");
    const exportChoice = adopted ? `<label class="lh-signal-export-choice"><input type="checkbox" data-signal-export="${e(item._key)}"${selectedExportIds.has(item._key) ? " checked" : ""} ${transferBusy ? "disabled" : ""}><span>导出</span></label>` : "";
    const source = item.source_kind === "legacy_expression" ? "学习记录" : item.source_kind === "shared_pack" ? "分享包"
      : item.source_kind === "conversation_input" ? "对话表达" : "角色表达";
    const action = pending
      ? `${actions.includes("adopt") ? `<button type="button" class="lh-command is-primary" data-signal-action="adopt" data-id="${e(item._key)}" ${busy ? "disabled" : ""}>采纳</button>` : ""}${actions.includes("reject") ? `<button type="button" class="lh-command" data-signal-action="reject" data-id="${e(item._key)}" ${busy ? "disabled" : ""}>拒绝</button>` : ""}`
      : adopted && actions.includes("retire")
      ? `<button type="button" class="lh-command" data-signal-action="retire" data-id="${e(item._key)}" ${busy ? "disabled" : ""}>停用</button>`
      : !adopted && actions.includes("restore")
        ? `<button type="button" class="lh-command is-primary" data-signal-action="restore" data-id="${e(item._key)}" ${busy ? "disabled" : ""}>恢复启用</button>` : actions.includes("reconsider")
          ? `<button type="button" class="lh-command" data-signal-action="reconsider" data-id="${e(item._key)}" ${busy ? "disabled" : ""}>重新审阅</button>` : "";
    const positive = Number.isInteger(item.positive_feedback) ? item.positive_feedback : 0;
    const negative = Number.isInteger(item.negative_feedback) ? item.negative_feedback : 0;
    const feedback = positive || negative ? `<p class="lh-signal-feedback">明确反馈 · 认可 ${positive} / 不喜欢 ${negative}</p>` : "";
    return `<article class="lh-signal-card ${pending ? "is-pending" : adopted ? "is-adopted" : "is-retired"}${unavailable ? " is-unavailable" : ""}">
      <header class="lh-signal-card-head"><div class="lh-signal-card-meta"><span class="lh-signal-dimension">${e(DIMENSION[item.dimension] || item.dimension || "表达规则")}</span>
        <span class="lh-signal-source">${source}</span><span class="lh-signal-scope">记录来源 · ${e(sourceLabel(item._source))}</span>
        <span class="lh-signal-state">${pending ? "待审" : adopted ? "已启用" : item.state === "rejected" ? "已拒绝" : "已停用"}</span>${form ? `<span class="lh-signal-kind">${form.kind === "grammar" ? "语法句式" : "情境表达"}</span>` : ""}</div><div class="lh-signal-card-tools">${exportChoice}<span class="lh-signal-uses">${e(item.injections || 0)} 次引用</span></div></header>
      <h3>${e(title)}</h3>${body ? `<p class="lh-signal-observation">${e(body)}</p>` : ""}
      ${form && !unavailable ? `<dl class="lh-signal-usage"><dt>怎么用</dt><dd>${e(item.proposal)}</dd>${form.avoid ? `<dt>何时不用</dt><dd>${e(form.avoid)}</dd>` : ""}</dl>` : ""}
      ${!unavailable ? renderApplicability(item.applicability) : ""}
      ${item.source_kind === "shared_pack" ? `<p class="lh-signal-import-origin">分享包 · ${e(item.source_title || "导入规则")}</p>` : ""}
      ${feedback}
      ${unavailable ? `<p class="lh-signal-unavailable">依据已失效，暂不可恢复</p>` : renderEvidence(item)}
      <footer class="lh-signal-card-foot"><div><span>${item.last_injected_at ? `最近注入 ${e(formatTime(item.last_injected_at))}` : "尚未注入"}</span>
        ${item.decided_at ? `<span>决定于 ${e(formatTime(item.decided_at))}</span>` : ""}</div><div class="lh-signal-actions">${item.can_revise ? `<button type="button" class="lh-command" data-signal-edit="${e(item._key)}">编辑</button>` : ""}${action}</div></footer>${confirm}</article>`;
  }
  function renderEditor() {
    if (!editor) return "";
    const draft = editor.changes;
    const field = (name, label, value, maximum) => `<label><span>${label}</span><textarea data-rule-field="${name}" maxlength="${maximum}" ${writing ? "disabled" : ""}>${e(value)}</textarea></label>`;
    const choices = (name, label, values, labels) => `<fieldset><legend>${label}</legend>${Object.entries(labels).map(([key,text])=>`<label><input type="checkbox" data-rule-condition="${name}" value="${e(key)}" ${values.includes(key)?"checked":""} ${writing?"disabled":""}>${e(text)}</label>`).join("")}</fieldset>`;
    return `<section class="lh-rule-editor" aria-label="编辑表达规则"><header><h3>编辑表达规则</h3><span>${e(sourceLabel(editor.item._source))}</span></header>
      <div class="lh-rule-editor-grid"><label><span>学习类型</span><select data-rule-field="kind"><option value="style" ${draft.expression.kind==="style"?"selected":""}>情境表达</option><option value="grammar" ${draft.expression.kind==="grammar"?"selected":""}>语法句式</option></select></label>
      <label><span>表达维度</span><select data-rule-field="dimension">${Object.entries(DIMENSION).map(([key,label])=>`<option value="${key}" ${key===draft.dimension?"selected":""}>${label}</option>`).join("")}</select></label>
      ${field("situation","适用情境",draft.expression.situation,240)}${field("pattern","具体说法或句式",draft.expression.pattern,500)}
      ${field("proposal","使用方式",draft.proposal,1200)}${field("avoid","何时不用",draft.expression.avoid,400)}</div>
      <details><summary>适用范围</summary><div class="lh-rule-editor-conditions">${choices("channels","使用频道",draft.applicability.channels,CHANNEL)}${choices("relationship_stages","关系阶段",draft.applicability.relationship_stages,RELATIONSHIP)}${choices("emotion_gates","对话氛围",draft.applicability.emotion_gates,EMOTION)}
      <label><span>回复意图</span><select data-rule-condition="intent">${Object.entries(INTENT).map(([key,label])=>`<option value="${key}" ${draft.applicability.intent===key?"selected":""}>${label}</option>`).join("")}</select></label>
      <label><span>情境提示词（逗号分隔）</span><input data-rule-condition="keywords" value="${e(draft.applicability.keywords.join("，"))}" maxlength="264"></label></div></details>
      <p class="lh-rule-editor-state">${editor.item.state==="adopted"?"保存后用于后续回复。":editor.item.state==="pending"?"保存后仍待审核。":"保存后保留当前状态。"}</p>
      ${editor.error ? `<p role="alert" class="lh-inline-status is-error">${e(editor.error)}</p>` : ""}
      <footer><button type="button" class="lh-command" data-rule-cancel ${writing?"disabled":""}>取消</button><button type="button" class="lh-command is-primary" data-rule-save ${writing?"disabled":""}>${writing?"正在保存…":"保存修改"}</button></footer></section>`;
  }
  function editRule(id) {
    const item = items.find(row=>row._key===id);
    if (!item?.can_revise || busy) return;
    editor = {item, changes: {dimension:item.dimension,proposal:item.proposal,
      expression: item.expression || {kind:item.dimension==="sentence_rhythm"?"grammar":"style",situation:item.observation||"",pattern:item.proposal||"",avoid:""},
      applicability:item.applicability || {channels:["private","group","proactive"],relationship_stages:["any"],emotion_gates:["any"],intent:"any",keywords:[]}}};
    editor.changes = JSON.parse(JSON.stringify(editor.changes));
    render(); root?.querySelector("[data-rule-field]")?.focus();
  }
  async function saveRule() {
    if (!editor || writing) return;
    const current = mountTicket, draft = editor;
    writing = true; render();
    try {
      await api("POST", draft.item._source.apiBase + "/style/revise", {signal_id:draft.item.signal_id,
        view_token:draft.item.view_token,operation_key:draft.operationKey || (draft.operationKey=operationKey("revise")),changes:draft.changes});
      if (!mounted(current)) return;
      editor = null; writing = false; await read(current);
      notice = "表达规则已更新。"; error = false; render();
    } catch (problem) {
      if (!mounted(current)) return;
      writing = false; draft.error = ERRORS[errorCode(problem?.code)] || "保存结果未确认，可重试保存或取消后重新读取。"; render();
    }
  }
  function render() {
    if (!root) return;
    const counts = Object.fromEntries(FILTERS.map(([key]) => [key, key === "all" ? items.length : items.filter(item => item.dimension === key).length]));
    const filters = FILTERS.map(([key, label]) => `<button type="button" class="lh-signal-filter${filter === key ? " is-active" : ""}" data-signal-filter="${key}" aria-pressed="${filter === key}"${writing ? " disabled" : ""}>${label}${filter === "all" || key === filter ? `<span>${counts[key] || 0}</span>` : ""}</button>`).join("");
    const stateTabs = STATE_FILTERS.map(([key, label]) => {
      const state=key==="active"?"adopted":key;
      const known=Object.values(countsBySource);
      const count=known.length?known.reduce((sum,source)=>sum+(Number(source[state])||0),0):key===stateFilter?items.length:null;
      return `<button type="button" class="lh-signal-state-tab${stateFilter === key ? " is-active" : ""}" data-signal-state="${key}" aria-pressed="${stateFilter === key}"${writing ? " disabled" : ""}>${label}${count===null?"":`<span>${count}</span>`}</button>`;
    }).join("");
    const visible = items.filter(item=>(kindFilter==="all" || item.expression?.kind===kindFilter)
      && (sourceFilter==="all" || item._source.key===sourceFilter));
    const rows = visible.map(renderItem).join("");
    const more = hasMore() ? `<button type="button" class="lh-signal-more lh-command" data-signal-more ${busy ? "disabled" : ""}>加载更多</button>` : "";
    const cap = capacity ? `待审与启用 ${capacity.active} / ${capacity.limit}` : `${items.length} 条`;
    const issueList = readIssues.length ? `<details class="lh-signal-evidence"><summary>查看读取明细 · ${readIssues.length} 个来源</summary><ol>${readIssues.map(issue => `<li data-code="${e(issue.code)}"><b>${e(issue.label)}</b>：${e(issue.detail)}</li>`).join("")}</ol></details>` : "";
    const importTargets=sources.length>1?`<label class="lh-expression-transfer-target"><span>导入目标</span><select data-share-target aria-label="规则导入目标">${sources.map(source=>`<option value="${e(source.key)}" ${source.key===importSourceKey?"selected":""}>${e(sourceLabel(source))}</option>`).join("")}</select></label>`:"";
    const transfer = transferOpen ? `<section class="lh-expression-transfer" aria-label="表达规则分享与导入">
      <div class="lh-expression-transfer-head"><strong>表达规则分享</strong><button type="button" class="lh-refresh" data-share-close aria-label="收起" title="收起"><i data-lucide="chevron-up"></i></button></div>
      <div class="lh-expression-transfer-export"><div class="lh-expression-transfer-actions">
        <button type="button" class="lh-command is-primary" data-share-export ${transferBusy ? "disabled" : ""}>${selectedExportIds.size ? `导出所选 ${selectedExportIds.size} 条` : "导出全部已启用规则"}</button>
        ${selectedExportIds.size ? `<button type="button" class="lh-command" data-share-clear-selection ${transferBusy ? "disabled" : ""}>清除所选</button>` : ""}
        ${exportPackage ? `<button type="button" class="lh-command" data-share-copy ${transferBusy ? "disabled" : ""}>复制 JSON</button><button type="button" class="lh-command" data-share-download ${transferBusy ? "disabled" : ""}>下载 JSON</button>` : ""}
      </div>${exportPackage ? `<p class="lh-expression-transfer-status" role="status">已生成 ${e(exportPackage.rule_count)} 条${exportPackage.excluded_count ? ` · ${e(exportPackage.excluded_count)} 条因原始依据失效而跳过` : ""}</p><textarea class="lh-expression-transfer-json" data-share-export-json readonly aria-label="导出的表达分享包">${e(JSON.stringify(exportPackage.package, null, 2))}</textarea>` : ""}</div>
      <div class="lh-expression-transfer-import">${importTargets}<label for="expressionShareFile">导入表达分享包</label><div class="lh-expression-transfer-file"><input id="expressionShareFile" type="file" accept=".json,application/json" data-share-file ${transferBusy ? "disabled" : ""}><span>JSON · 最大 250 KB</span></div>
        <textarea class="lh-expression-transfer-json" data-share-import-json placeholder="粘贴分享包 JSON" aria-label="待导入的表达分享包" ${transferBusy ? "disabled" : ""}>${e(importJSON)}</textarea>
        <div class="lh-expression-transfer-actions"><button type="button" class="lh-command" data-share-preview ${transferBusy || !importJSON.trim() ? "disabled" : ""}>预览导入</button><button type="button" class="lh-command is-primary" data-share-apply ${transferBusy || !importPreview?.importable_rule_count ? "disabled" : ""}>加入待审队列</button></div>
        ${importPreview ? `<div class="lh-expression-import-preview" role="status"><div><strong>${e(importPreview.title || "表达分享包")}</strong><span>${e(importPreview.importable_rule_count)} 条待导入 / ${e(importPreview.duplicate_rule_count)} 条重复 / ${e(importPreview.capacity_blocked_count)} 条超出容量</span></div>${importPreview.candidates?.length ? `<ul>${importPreview.candidates.slice(0, 6).map(item => `<li><b>${e(DIMENSION[item.dimension] || item.dimension)}</b><span>${e(item.observation)}</span><small>${e(item.proposal)}</small>${renderApplicability(item.applicability)}</li>`).join("")}</ul>` : ""}${importPreview.rejected_count ? `<details><summary>${e(importPreview.rejected_count)} 项未通过</summary><ul>${(importPreview.rejected || []).slice(0, 10).map(item => `<li>第 ${e(Number(item.group_index) + 1)} 组${item.rule_index == null ? "" : `第 ${e(Number(item.rule_index) + 1)} 条`}：${e(item.reason)}</li>`).join("")}</ul></details>` : ""}<small>确认后进入待审列表，采纳后才会用于回复。</small></div>` : ""}
      </div>${transferNotice ? `<p class="lh-inline-status ${transferError ? "is-error" : ""}" role="status">${e(transferNotice)}</p>` : ""}</section>` : "";
    root.innerHTML = `<section class="lh-signal-list" aria-label="表达规则库"><header class="lh-signal-head"><div><h3>表达规则库</h3><span class="lh-signal-capacity">${e(cap)}</span></div>
      <div class="lh-signal-head-tools"><a class="lh-command" href="#manage/learning/strategy">学习设置</a><button type="button" class="lh-command" data-share-toggle aria-expanded="${transferOpen}">${transferOpen ? "收起分享" : "分享 / 导入"}</button><button type="button" class="lh-refresh" data-signal-refresh aria-label="重新读取" title="重新读取" ${busy || transferBusy ? "disabled" : ""}><i data-lucide="refresh-cw"></i></button></div></header>
      <div class="lh-signal-state-filters" role="group" aria-label="规则状态">${stateTabs}</div>
      <div class="lh-signal-toolbar"><div class="lh-signal-filters" role="group" aria-label="表达规则筛选">${filters}</div></div>
      <div class="lh-signal-selects"><label>学习类型<select data-rule-kind-filter><option value="all">全部类型</option><option value="style" ${kindFilter==="style"?"selected":""}>情境表达</option><option value="grammar" ${kindFilter==="grammar"?"selected":""}>语法句式</option></select></label>
      ${sources.length>1?`<label>记录来源<select data-rule-source-filter><option value="all">全部来源</option>${sources.map(source=>`<option value="${e(source.key)}" ${sourceFilter===source.key?"selected":""}>${e(sourceLabel(source))}</option>`).join("")}</select></label>`:""}</div>
      ${transfer}
      ${renderEditor()}
      ${notice ? `<div class="lh-inline-status ${error ? "is-error" : ""}" role="status"><span>${e(notice)}</span>${issueList}</div>` : ""}
      ${rows || `<div class="lh-signal-empty">${busy ? "正在读取…" : kindFilter!=="all"||sourceFilter!=="all"?"当前筛选下没有表达规则":stateFilter === "pending" ? "还没有待审候选" : stateFilter === "retired" ? "还没有停用的表达规则" : stateFilter==="rejected"?"还没有拒绝的表达规则":"还没有已启用表达规则"}</div>`}${more}</section>`;
    window.lucide?.createIcons({attrs: {"aria-hidden": "true"}});
  }
  async function command(id, action) {
    const item = items.find(row => row._key === id);
    if (!item || !Array.isArray(item.actions) || !item.actions.includes(action) || busy) return;
    const current = mountTicket;
    const request = ++readTicket;
    busy = true; writing = true; notice = ""; error = false; render();
    let resultNotice = "", resultError = false;
    try {
      await api("POST", item._source.apiBase + "/style/command", {
        signal_id: item.signal_id, action, view_token: item.view_token, operation_key: operationKey(action),
      });
      resultNotice = action === "adopt" ? "规则已采纳并启用。" : action === "reject" ? "候选已拒绝。"
        : action === "retire" ? "规则已停用。" : action==="reconsider"?"已放回待审队列。":"规则已恢复启用。";
    } catch (problem) {
      resultNotice = ERRORS[errorCode(problem?.code)] || "操作结果未确认，请重新读取。";
      resultError = true;
    }
    if (!mounted(current) || request !== readTicket) return;
    busy = false; writing = false; confirming = null;
    const refreshed = await read(current);
    if (!mounted(current)) return;
    if (refreshed) { notice = resultNotice; error = resultError; }
    render();
  }
  async function exportRules() {
    if (transferBusy || busy || !sources.length) return;
    const current = mountTicket;
    transferBusy = true; transferNotice = ""; transferError = false; exportPackage = null; render();
    try {
      const grouped=new Map();
      if(selectedExportIds.size){
        for(const id of selectedExportIds){
          const item=items.find(row=>row._key===id);
          if(!item)continue;
          const group=grouped.get(item._source.key)||{source:item._source,ids:[]};
          group.ids.push(item.signal_id);grouped.set(item._source.key,group);
        }
      }else for(const source of sources)grouped.set(source.key,{source,ids:null});
      const requests=[...grouped.values()];
      if(requests.length>200)throw {code:"expression_export_limit"};
      const results=await Promise.allSettled(requests.map(({source,ids})=>api("POST",source.apiBase+"/style/export",ids?{signal_ids:ids}:{})));
      const valid=results.filter(result=>result.status==="fulfilled").map(result=>result.value);
      if(!valid.length)throw results.find(result=>result.status==="rejected")?.reason||{code:"expression_export_empty"};
      const groups=valid.flatMap(value=>Array.isArray(value.package?.rule_groups)?value.package.rule_groups:[]);
      if(groups.length>200)throw {code:"expression_export_limit"};
      groups.forEach((group,index)=>{group.id=`group-${String(index+1).padStart(3,"0")}`;});
      exportPackage={package:{...valid[0].package,title:"陪伴表达分享",exported_at:new Date().toISOString(),rule_groups:groups},
        rule_count:valid.reduce((sum,value)=>sum+Number(value.rule_count||0),0),
        excluded_count:valid.reduce((sum,value)=>sum+Number(value.excluded_count||0),0)};
      transferNotice=results.some(result=>result.status==="rejected")?"分享包已生成，部分来源没有可导出的规则。":"分享包已生成。";
    } catch (problem) {
      transferNotice = ERRORS[errorCode(problem?.code)] || "分享包生成失败，请重新读取。";
      transferError = true;
    }
    if (!mounted(current)) return;
    transferBusy = false; render();
  }
  async function previewImport() {
    const destination=selectedImportSource();
    if (transferBusy || busy || !destination || !importJSON.trim()) return;
    const current = mountTicket;
    try { JSON.parse(importJSON); } catch (_) { transferNotice = "JSON 格式无效。"; transferError = true; render(); return; }
    if (new TextEncoder().encode(importJSON).length > 250000) {
      transferNotice = "分享包超过 250 KB。"; transferError = true; render(); return;
    }
    transferBusy = true; transferNotice = ""; transferError = false; importPreview = null; render();
    try {
      importPreview = await api("POST", destination.apiBase + "/style/import/preview", {package_json: importJSON});
      transferNotice = "预览已更新；确认后将加入待审队列。";
    } catch (problem) {
      transferNotice = ERRORS[errorCode(problem?.code)] || "预览失败，请检查分享包。";
      transferError = true;
    }
    if (!mounted(current)) return;
    transferBusy = false; render();
  }
  async function applyImport() {
    const destination=selectedImportSource();
    if (transferBusy || busy || !destination || !importPreview?.preview_token || !importPreview.importable_rule_count) return;
    const current = mountTicket;
    transferBusy = true; transferNotice = ""; transferError = false; render();
    try {
      const result = await api("POST", destination.apiBase + "/style/import/apply", {
        package_json: importJSON, preview_token: importPreview.preview_token, operation_key: operationKey("import"),
      });
      const imported = Number(result.imported_count || 0);
      importPreview = null; importJSON = "";
      transferNotice = result.replayed ? `导入结果已核对：${imported} 条在待审队列。`
        : imported ? `${imported} 条已加入审阅中心，采纳后才会生效。` : "没有新增规则；目标中已有相同规则或容量已满。";
      transferError = false;
    } catch (problem) {
      transferNotice = ERRORS[errorCode(problem?.code)] || "导入结果未确认，请重新预览后核对。";
      transferError = true; importPreview = null;
    }
    if (!mounted(current)) return;
    transferBusy = false;
    if (!transferError) await read(current);
    render();
  }
  async function copyExport() {
    if (!exportPackage) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(exportPackage.package, null, 2));
      transferNotice = "分享包已复制。"; transferError = false;
    } catch (_) { transferNotice = "无法访问剪贴板，请复制文本框中的 JSON。"; transferError = true; }
    render();
  }
  function downloadExport() {
    if (!exportPackage) return;
    const blob = new Blob([JSON.stringify(exportPackage.package, null, 2)], {type: "application/json;charset=utf-8"});
    const url = URL.createObjectURL(blob), anchor = document.createElement("a");
    anchor.href = url; anchor.download = "private-companion-expression-pack.json";
    document.body.append(anchor); anchor.click(); anchor.remove(); URL.revokeObjectURL(url);
  }
  function onClick(event) {
    if (writing || transferBusy) return;
    if (event.target.closest("[data-rule-save]")) { void saveRule(); return; }
    if (event.target.closest("[data-rule-cancel]")) { editor=null; render(); return; }
    if (editor) return;
    const edit = event.target.closest("[data-signal-edit]");
    if (edit) { editRule(edit.dataset.signalEdit); return; }
    if (event.target.closest("[data-share-toggle]")) { transferOpen = !transferOpen; render(); return; }
    if (event.target.closest("[data-share-close]")) { transferOpen = false; render(); return; }
    if (event.target.closest("[data-share-export]")) { void exportRules(); return; }
    if (event.target.closest("[data-share-clear-selection]")) { selectedExportIds = new Set(); render(); return; }
    if (event.target.closest("[data-share-preview]")) { void previewImport(); return; }
    if (event.target.closest("[data-share-apply]")) { void applyImport(); return; }
    if (event.target.closest("[data-share-copy]")) { void copyExport(); return; }
    if (event.target.closest("[data-share-download]")) { downloadExport(); return; }
    const stateButton = event.target.closest("[data-signal-state]");
    if (stateButton) { const next = STATE_FILTERS.some(([key]) => key === stateButton.dataset.signalState) ? stateButton.dataset.signalState : "active"; if (next !== stateFilter) { stateFilter = next; void read(mountTicket); } return; }
    const filterButton = event.target.closest("[data-signal-filter]");
    if (filterButton) { const next = FILTERS.some(([key]) => key === filterButton.dataset.signalFilter) ? filterButton.dataset.signalFilter : "all"; if (next !== filter) { filter = next; void read(mountTicket); } return; }
    if (event.target.closest("[data-signal-refresh]")) { void read(mountTicket); return; }
    if (event.target.closest("[data-signal-more]")) { void read(mountTicket, {append: true}); return; }
    const action = event.target.closest("[data-signal-action]");
    if (action) { if (action.dataset.signalAction === "retire") { confirming = action.dataset.id; render(); } else void command(action.dataset.id, action.dataset.signalAction); return; }
    const confirm = event.target.closest("[data-signal-confirm]");
    if (confirm) { if (confirm.dataset.signalConfirm === "no") { confirming = null; render(); } else void command(confirm.dataset.id, "retire"); }
  }
  function onChange(event) {
    if (writing || transferBusy) return;
    if (updateDraft(event.target)) return;
    const kind = event.target.closest("[data-rule-kind-filter]");
    if (kind) {kindFilter=kind.value;render();return;}
    const source = event.target.closest("[data-rule-source-filter]");
    if (source) {sourceFilter=source.value;render();return;}
    const destination=event.target.closest("[data-share-target]");
    if(destination){importSourceKey=destination.value;importPreview=null;transferNotice="";transferError=false;return;}
    const checkbox = event.target.closest("[data-signal-export]");
    if (checkbox) {
      if (checkbox.checked) selectedExportIds.add(checkbox.dataset.signalExport);
      else selectedExportIds.delete(checkbox.dataset.signalExport);
      exportPackage = null; render(); return;
    }
    const fileInput = event.target.closest("[data-share-file]");
    const file = fileInput?.files?.[0];
    if (!file) return;
    if (file.size > 250000) {
      transferNotice = "分享包超过 250 KB。"; transferError = true; importJSON = ""; importPreview = null; render(); return;
    }
    const current = mountTicket;
    void file.text().then(text => {
      if (!mounted(current)) return;
      importJSON = text; importPreview = null; transferNotice = ""; transferError = false; render();
    }).catch(() => {
      if (!mounted(current)) return;
      transferNotice = "文件读取失败。"; transferError = true; render();
    });
  }
  function onInput(event) {
    if (updateDraft(event.target)) return;
    const textarea = event.target.closest("[data-share-import-json]");
    if (!textarea) return;
    importJSON = textarea.value; importPreview = null; transferNotice = ""; transferError = false;
    const preview = root?.querySelector(".lh-expression-import-preview");
    if (preview) preview.remove();
    const apply = root?.querySelector("[data-share-apply]");
    if (apply) apply.disabled = true;
  }
  function updateDraft(target) {
    if (!editor || writing) return false;
    const field=target.closest("[data-rule-field]");
    if (field) {
      const name=field.dataset.ruleField;
      if (name==="dimension"||name==="proposal") editor.changes[name]=field.value;
      else if (["kind","situation","pattern","avoid"].includes(name)) editor.changes.expression[name]=field.value;
      editor.operationKey=null; return true;
    }
    const condition=target.closest("[data-rule-condition]");
    if (!condition) return false;
    const name=condition.dataset.ruleCondition,app=editor.changes.applicability;
    if (name==="intent") app.intent=condition.value;
    else if (name==="keywords") app.keywords=condition.value.split(/[,，\n]/).map(value=>value.trim()).filter(Boolean);
    else if (["channels","relationship_stages","emotion_gates"].includes(name)) {
      let values=new Set(app[name]);
      if (condition.checked) {if (condition.value==="any") values=new Set();else values.delete("any");values.add(condition.value);}
      else values.delete(condition.value);
      app[name]=[...values];
      root?.querySelectorAll(`[data-rule-condition="${name}"]`).forEach(input=>{
        input.checked=values.has(input.value);
      });
    }
    editor.operationKey=null; return true;
  }
  function onUpdated(event) {
    if (sources.some(source=>source.scopeKey===event.detail?.scopeKey)) void read(mountTicket);
  }
  function mount(host, options = {}) {
    unmount(); root = host;
    const candidates=Array.isArray(options.sources)?options.sources:options.replyApi?[{replyApi:options.replyApi,scopeKey:options.scopeKey,label:"当前来源"}]:[];
    sources=candidates.map((source,index)=>{const value=relative(source.replyApi);return value?{...source,
      key:String(source.key||source.scopeKey||value||index),scopeKey:source.scopeKey||source.key||"",apiBase:value}:null;}).filter(Boolean);
    apiBase=sources[0]?.apiBase||"";scopeKey=sources[0]?.scopeKey||"";importSourceKey=sources[0]?.key||"";
    filter = FILTERS.some(([key]) => key === options.state) ? options.state : "all";
    items = []; pages = {}; capacity = null; notice = ""; busy = false; writing = false; error = false; readIssues = []; confirming = null;
    transferOpen = false; transferBusy = false; transferNotice = ""; transferError = false; importJSON = ""; importPreview = null;
    exportPackage = null; selectedExportIds = new Set();
    editor=null;kindFilter="all";sourceFilter="all";stateFilter="active";
    root?.addEventListener("click", onClick); root?.addEventListener("change", onChange); root?.addEventListener("input", onInput);
    window.addEventListener?.("companion-expression-updated", onUpdated); const current = ++mountTicket;
    if (sources.length) void read(current); else { notice = "没有可读取的表达规则来源。"; error = true; render(); }
  }
  function unmount() { mountTicket++; readTicket++; root?.removeEventListener("click", onClick); root?.removeEventListener("change", onChange); root?.removeEventListener("input", onInput); window.removeEventListener?.("companion-expression-updated", onUpdated); root = null; apiBase = ""; scopeKey = ""; sources=[];items = []; pages = {}; capacity = null;capacityBySource={}; readIssues=[]; busy = false; writing = false; transferBusy = false; confirming = null; }
  return {mount, unmount, get state() { return {items, busy, notice, scopeKey, filter, capacity,sources}; }};
})();
