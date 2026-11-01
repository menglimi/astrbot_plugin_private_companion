/* Model configuration: task routing, voice, image generation and optional Laya decisions. */
"use strict";
window.CompanionModelsPage = (() => {
  const PREFIX = "/astrbot_plugin_private_companion/";
  const routing = ["provider", "fallback", "max_output_tokens"];
  const MAX_MODEL_OUTPUT_TOKENS = 65536;
  const TASK_CONFIG_TIMEOUT_MS = 30000;
  const TASK_CONFIG_RETRY_TIMEOUT_MS = 30000;
  const VOICE_SAVE_TIMEOUT_MS = 30000;
  const fields = ["provider", "fallback", "prompt", "max_output_tokens", "timeout_seconds", "replacement_rules", "fallback_on_refusal", "refusal_terms", "replacement_scope"];
  const fieldLabel = { provider: "主模型提供方", fallback: "备用模型提供方", prompt: "附加提示词",
    max_output_tokens: "输出上限", timeout_seconds: "请求超时", replacement_rules: "替换规则",
    fallback_on_refusal: "拒答替代", refusal_terms: "拒答匹配词", replacement_scope: "替换作用范围" };
  const groups = [
    ["对话", [["conversation.reply", "对话回复", "AstrBot 核心回复与 V2 附加提示"],
      ["conversation.context_image_caption", "图片摘要", "为已授权的历史图片提取可见内容"],
      ["conversation.image_narration", "当前图片转述", "为本轮已授权图片生成简短文字描述"],
      ["conversation.video_narration", "原生视频转述", "将原视频交给支持视频输入的模型直接理解"]]],
    ["感知", [["screen.interpretation", "屏幕解读", "分析本次已授权截图，需要支持图片的模型"],
      ["screen.peek", "屏幕速览", "为已授权屏幕画面生成简短摘要"],
      ["image_review", "图片审核", "检查群聊成图的可见内容"],
      ["conversation.image_review", "图片审核", "检查群聊成图的可见内容"]]],
    ["主动与记忆", [["proactive.compose", "主动联系", "有真实契机时组织联系内容"],
      ["proactive.judge", "主动联系复核", "复核主动消息是否符合当前人格与来源"],
      ["memory.reflect", "记忆整理", "从已授权素材整理认识"]]],
    ["生活与成长", [["calendar.plan", "日程规划", "基于已有目标规划下一步"], ["role.state.maintain", "角色状态", "维护角色的连续生活状态"], ["role.reading.consume", "阅读理解", "阅读实际提供的原文"], ["role.growth.progress", "成长进展", "根据已完成确认提出进展"]]],
    ["私人书写", [["role.journal.diary", "日记", "保存私人书写"], ["role.journal.dream", "梦境", "保存梦境记录"], ["role.journal.derive", "书写整理", "整理已有文字素材"], ["role.journal.select", "素材选择", "选择本次书写素材"]]],
    ["学习", [["role.expression.learn", "表达学习", "从当前会话的原始素材提炼表达规则"],
      ["role.expression.review", "回复表达复核", "发送前检查规则应用是否自然"],
      ["role.expression.contact_review", "主动表达复核", "主动消息发送前检查表达"],
      ["role.self_review", "每日复盘", "从近期执行记录提炼调整"]]],
    ["内容工作", [["content.authoring.draft", "内容起草", "根据用户要求与已有素材创作或续写作品"]]],
  ];
  const names = new Map(groups.flatMap(([group, items]) => items.map(([kind, label, description]) => [kind, { group, label, description }])));
  const errors = {
    model_configuration_host_managed: "普通回复模型由 AstrBot 当前会话管理；此处只保存对话附加提示。",
    configuration_revision_conflict: "配置已变化，草稿保留；核对当前值后再保存。", idempotency_key_conflict: "原保存请求与当前内容不一致，请刷新后核对。",
    model_configuration_invalid: "请核对任务配置、模型提供方和输出上限。", model_task_unavailable: "此任务已不再注册，请刷新列表。",
    model_provider_unavailable: "所选 AstrBot 模型未加载或暂不可用于 V2。", model_capability_mismatch: "所选模型提供方不支持这项任务。", model_output_limit: "输出上限超过所选模型提供方能力。",
    model_catalog_unavailable: "无法读取已加载的 AstrBot 文本模型提供方。",
    model_fallback_duplicate: "备用模型提供方必须与主模型提供方不同。", core_access_required: "当前账号没有管理权限。", dashboard_identity_required: "请从已登录的 AstrBot 管理界面打开小屋。",
    staging_not_initialized: "角色数据尚未就绪。请先在「账号与会话」完成角色映射并准备启动；共享启动模型会自动跟随 AstrBot 当前模型，也可在账号页固定。", staging_host_terminated: "插件已停止，请重新打开插件。", bridge_timeout: "连接超时，请核对原保存结果。",
    model_route_invalid: "替换规则未通过校验：请检查规则默认模型提供方、匹配方式与时段。",
    model_refused: "所选模型提供方拒绝了这次请求。",
    model_probe_timeout: "测试超时，结果未知。", model_probe_failed: "模型提供方未返回可确认结果。",
    model_provider_changed: "测试期间模型提供方已变化，请重新测试。",
    model_voice_probe_timeout: "语音测试超时，结果未知。", model_voice_probe_failed: "语音合成未返回可确认结果。",
    model_voice_provider_unavailable: "语音服务尚未加载。", model_voice_audio_invalid: "语音服务未返回有效音频。",
    model_voice_audio_read_failed: "语音文件已生成，但读取试听音频失败。",
    model_voice_provider_changed: "测试期间语音服务已变化，请重新测试。",
    model_image_store_failed: "生图开关保存失败，请检查插件数据目录权限。",
    model_image_output_unavailable: "生图扩展尚未提供配置接口。", model_image_output_conflict: "生图配置已在其他页面更新，请重新读取后再保存。",
    model_image_output_busy: "当前有生图任务正在执行，请稍后保存。", model_image_output_invalid: "生图配置未通过校验，请检查模型、地址、参数与优先级。",
    model_image_output_failed: "生图模型配置读取或保存失败。",
    model_request_invalid: "请求未通过校验，请刷新后重试。",
    staging_profile_conflict: "当前角色映射已变化，请在账号与会话重新读取。",
    tts_provider_invalid: "语音服务配置未通过校验，请核对必填项。", tts_provider_missing: "语音服务不存在，请刷新列表。",
    tts_provider_exists: "服务标识已存在，请换一个。", tts_provider_unsupported: "当前 AstrBot 版本不支持动态管理语音服务。",
    tts_provider_test_timeout: "连接测试超时，请检查网络、代理和上游服务状态。",
    tts_provider_test_failed: "连接测试未通过，请核对地址、密钥、代理和模型参数。",
    tts_provider_test_unsupported: "该语音服务不支持连接自检，可继续使用测试合成验证。",
    tts_provider_unloaded: "语音服务尚未加载，请先启用并刷新宿主状态。",
    laya_config_conflict: "Laya 配置已变化，请刷新后再保存。", laya_config_invalid: "Laya 配置未通过校验，请核对接入方式。",
    laya_config_unreadable: "Laya 配置无法读取，请检查插件数据目录。", laya_endpoint_missing: "远程或 Jev 模式需要填写判断服务地址。",
    laya_endpoint_invalid: "Laya 服务地址必须是 HTTP(S) 地址。", laya_model_invalid: "Laya 检查点选择无效。",
    laya_mode_invalid: "Laya 接入方式无效。", laya_package_missing: "当前 AstrBot Python 环境未安装 Laya。",
    laya_initialization_failed: "Laya Router 初始化失败，请检查安装和运行环境。", laya_inference_failed: "Laya 本地推理失败，请查看详细日志。",
    laya_initializing: "Laya 本地模型正在预热，请就绪后再测试；聊天继续按原有流程处理。",
    laya_model_path_invalid: "本地检查点目录不完整，请选择含有 Laya 权重、配置和分词器的文件夹。",
    laya_inference_timeout: "Laya 本地判断超过调用超时，本轮继续普通回复流程。",
    laya_transport_failed: "Laya 连接失败，请检查地址、网络和远程服务状态；部分网关会直接断开无效密钥，请同时核对 API Key。", laya_http_error: "Laya HTTP 服务返回错误，详细信息见下方。",
    laya_auth_failed: "判断服务鉴权失败，请核对 API Key 与服务端访问权限。",
    laya_response_invalid: "Laya 返回缺少官方 SystemOne 所需分类。", laya_contract_invalid: "Laya 分类超出插件决策合同，已拒绝采用。",
    laya_rule_skip: "这类事件由程序规则处理，不调用 Laya。", laya_no_decision_fields: "没有启用任何 Laya 决策分类。",
    search_config_conflict: "搜索配置已变化，请刷新后再保存。", search_config_invalid: "搜索配置无效，请刷新并重新选择 Provider。",
    search_config_unreadable: "搜索配置文件无法读取。", search_config_unavailable: "无法读取搜索服务配置。",
  };
  /* Real dashboard bridges reject failed requests with the error code carried in
     Error.message; the mocked bridge resolves error envelopes with Error.code. */
  const fail = (err, fallback) => errors[err.code] || errors[err.message] || err?.reason || fallback;
  /* 后端把机器码放在信封的 message 上（页面桥只转发 message），所以取码要同时
     看 data.code 与 message；reason 里若是人话则原样保留。 */
  const CODE = /^[a-z][a-z0-9_]{2,60}$/;
  const codeOf = wrapped => wrapped?.data?.code
    || (CODE.test(wrapped?.message || "") ? wrapped.message : "") || "";
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const failure = (code, confirmed = false, details = {}) => Object.assign(new Error(code), { code, confirmed, ...details });
  const states = new Map();
  const EXPAND_KEY = "companion-model-expanded.v1";
  function expandStorageKey(scopeKey) { return EXPAND_KEY + ":" + scopeKey; }
  function restoreExpanded(scopeKey) {
    try { const raw = localStorage.getItem(expandStorageKey(scopeKey)); const parsed = raw ? JSON.parse(raw) : null;
      return parsed && typeof parsed === "object" ? parsed : {}; } catch { return {}; }
  }
  function rememberExpanded(state, key) {
    try { const scopeKey = state.__scopeKey; if (!scopeKey) return;
      localStorage.setItem(expandStorageKey(scopeKey), JSON.stringify(state.expOpen || {})); } catch { }
  }
  let root = null, path = null, key = null, state = null, version = 0;
  const current = (ticket, s) => ticket === version && state === s && root?.isConnected;
  const taskOf = (s, kind) => s.status?.tasks.find(task => task.kind === kind) || null;
  const configSource = source => ({ default: "插件默认", host_conversation: "AstrBot 会话", installation: "安装级", role: "角色级", window: "当前对象" }[source] || "上级配置");
  const windowLabel = s => s.status?.scope_kind === "group" ? "当前群聊微调" : "当前私聊微调";
  const adoption = state => ({ applied: "已采用", next_task: "下次任务", consumer_not_started: "尚未运行", failed: "采用失败", pending: "等待采用", restart_required: "需重启" }[state] || state);
  const taskName = kind => names.get(kind) || { group: "其他任务", label: "其他模型任务", description: "V2 注册任务" };
  const taskGroups = s => {
    const registered = new Set(snapshot(s).map(task => task.kind));
    const known = groups.map(([group, items]) => [group, items.filter(([kind]) => registered.has(kind))])
      .filter(([, items]) => items.length);
    const other = snapshot(s).filter(task => !names.has(task.kind))
      .map(task => [task.kind, taskName(task.kind).label, taskName(task.kind).description]);
    return other.length ? [...known, ["其他任务", other]] : known;
  };
  const snapshot = s => s?.status?.tasks || [];
  const coreReply = task => task?.execution_path === "astrbot_core";
  const taskFields = task => coreReply(task) ? ["prompt"] : fields;
  const fallbackFor = task => {
    if (coreReply(task)) return "";
    const field = task.fields.fallback;
    if (field?.explicit) return field.value || "";
    return field?.value || field?.inherited_value || task.fields.provider?.inherited_value
      || task.fields.provider?.value || "";
  };
  const available = (s, task) => s.status.providers.filter(p => task.capabilities.every(cap => p.capabilities.includes(cap)));
  const effective = (task, draft, name) => draft.inherited[name] ? task.fields[name].inherited_value : draft.values[name];
  const providerFor = (task, draft) => coreReply(task) ? "" : effective(task, draft, "provider");
  const limitFor = (s, task, draft) => Math.min(MAX_MODEL_OUTPUT_TOKENS, ...["provider", "fallback"]
    .map(name => effective(task, draft, name)).filter(Boolean)
    .map(id => s.status.providers.find(provider => provider.id === id)?.max_output_tokens || MAX_MODEL_OUTPUT_TOKENS));
  function fitLimit(s, task, draft) { const limit = limitFor(s, task, draft);
    if (effective(task, draft, "max_output_tokens") <= limit) return false;
    draft.values.max_output_tokens = limit; draft.inherited.max_output_tokens = false; return true; }
  const storageKey = kind => key + ":" + kind;
  const shown = (s, task) => { const info = taskName(task.kind), custom = taskFields(task).some(name => !s.drafts.get(task.kind).inherited[name]);
    return (!s.providerFilter || s.providerFilter === providerFor(task, s.drafts.get(task.kind)))
      && (!s.lane || s.lane === "all" || s.lane === task.lane)
      && (!s.custom || s.custom === "custom" && custom || s.custom === "inherited" && !custom)
      && (info.label + info.group + info.description + task.kind).toLocaleLowerCase().includes(s.query.toLocaleLowerCase()); };
  const visible = s => snapshot(s).filter(task => shown(s, task));
  const selected = s => s.selection.filter(kind => taskOf(s, kind) && !coreReply(taskOf(s, kind)));
  const pendingCount = s => snapshot(s).reduce((count, task) => { const draft = s.drafts.get(task.kind); return count + (draft && !draft.pending && dirty(task, draft) ? 1 : 0); }, 0);
  const pendingSaveCount = s => snapshot(s).reduce((count, task) => count + (s.drafts.get(task.kind)?.pending ? 1 : 0), 0);
  function readPending(kind) { try { const p = JSON.parse(sessionStorage.getItem(storageKey(kind)));
    if (!p || p.kind !== kind || typeof p.operation_key !== "string" || typeof p.edit_token !== "string" || !p.changes || !Array.isArray(p.reset)) return null;
    if (p.layer === undefined) p.layer = "window";
    return p; } catch { return null; } }
  function storePending(kind, p) { try { if (p) sessionStorage.setItem(storageKey(kind), JSON.stringify(p)); else sessionStorage.removeItem(storageKey(kind)); } catch { } }
  const newDraft = task => ({ layer: task.configuration_layer || "role", token: task.edit_token,
    base: Object.fromEntries(Object.entries(task.fields).map(([name, field]) => [name, {...field}])),
    values: Object.fromEntries(fields.map(name => [name, task.fields[name].value])),
    inherited: Object.fromEntries(fields.map(name => [name, !task.fields[name].explicit])), touched: false, stale: false, conflicts: [], pending: readPending(task.kind) });
  /* The replacement fields are optional in the payload: newer hosts expose them,
     older ones only ship the routing fields. Missing ones degrade to inherited. */
  const plainField = value => ({ value, inherited_value: value, source: "default", inherited_source: "default", explicit: false });
  function normalize(data) { const tasks = Array.isArray(data?.tasks) ? data.tasks : [];
    for (const task of tasks) { if (!task || typeof task !== "object") continue;
      task.configuration_layer = task.configuration_layer || data.configuration_layer || "role";
      task.fields = task.fields && typeof task.fields === "object" ? task.fields : {};
      if (task.fields.replacement_rules === undefined) task.fields.replacement_rules = plainField("[]");
      if (task.fields.fallback_on_refusal === undefined) task.fields.fallback_on_refusal = plainField(false);
      if (task.fields.refusal_terms === undefined) task.fields.refusal_terms = plainField("");
      if (task.fields.timeout_seconds === undefined) task.fields.timeout_seconds = plainField(60);
      if (task.fields.replacement_scope === undefined) task.fields.replacement_scope = plainField("all"); }
    return data; }
  function patchFor(task, draft) { const changes = {}, reset = [];
    for (const name of taskFields(task)) { const field = task.fields[name]; if (draft.inherited[name]) { if (field.explicit) reset.push(name); }
      else if (!field.explicit || draft.values[name] !== field.value) changes[name] = draft.values[name]; }
    return { changes, reset }; }
  const dirty = (task, draft) => { const p = patchFor(task, draft); return Object.keys(p.changes).length > 0 || p.reset.length > 0; };
  const sameField = (a, b) => JSON.stringify([a?.value, !!a?.explicit, a?.inherited_value, a?.source, a?.inherited_source])
    === JSON.stringify([b?.value, !!b?.explicit, b?.inherited_value, b?.source, b?.inherited_source]);
  function reconcileDraft(task, draft) {
    // Unknown saves keep their exact operation and baseline until reconciled.
    if (draft.pending) { draft.stale ||= draft.token !== task.edit_token; return draft; }
    const base = draft.base || task.fields, patch = patchFor({...task, fields: base}, draft);
    const edited = [...Object.keys(patch.changes), ...patch.reset], next = newDraft(task);
    for (const name of edited) {
      next.values[name] = draft.values[name]; next.inherited[name] = draft.inherited[name];
      const alreadyCurrent = draft.inherited[name] ? !task.fields[name].explicit
        : task.fields[name].explicit && draft.values[name] === task.fields[name].value;
      if ((!sameField(base[name], task.fields[name]) || draft.conflicts?.includes(name)) && !alreadyCurrent) next.conflicts.push(name);
    }
    next.touched = edited.length > 0;
    if (next.conflicts.length || next.layer !== draft.layer) {
      next.token = draft.token; next.stale = true;
    }
    return next;
  }
  /* The Dashboard bridge normally returns the inner ``data`` object.  Some
     older bridges and tests still return the complete response envelope.  A
     voice probe's inner result also has ``status: "ok"``; only treat a value
     as an envelope when its shape is actually envelope-shaped, otherwise the
     successful audio result would be reduced to ``undefined``. */
  const ENVELOPE_KEYS = new Set(["status", "message", "reason", "data", "error", "code"]);
  const hasOwn = (value, name) => !!value && typeof value === "object"
    && Object.prototype.hasOwnProperty.call(value, name);
  const isEnvelope = value => !!value && typeof value === "object" && !Array.isArray(value)
    && ["ok", "error"].includes(value.status) && hasOwn(value, "data")
    && Object.keys(value).every(name => ENVELOPE_KEYS.has(name));
  const responseContent = response => {
    if (isEnvelope(response)) return {wrapped: response, value: response.data};
    if (isEnvelope(response?.data)) return {wrapped: response.data, value: response.data.data};
    /* A few bridge shims preserve one anonymous `data` layer without the
       envelope fields.  Unwrap it only when it is the whole response. */
    if (response && typeof response === "object" && !Array.isArray(response)
        && !hasOwn(response, "status") && hasOwn(response, "data")
        && response.data && typeof response.data === "object" && !Array.isArray(response.data)) {
      return {wrapped: null, value: response.data};
    }
    return {wrapped: null, value: response};
  };
  async function api(method, endpoint, body, timeout = 30000) { let timer;
    try { const bridge = window.CompanionProduct.bridgeNow() || await window.CompanionProduct.bridgeFor();
      const response = await Promise.race([method === "GET" ? bridge.apiGet(endpoint, body) : bridge.apiPost(endpoint, body), new Promise((_, reject) => { timer = setTimeout(() => reject(failure("bridge_timeout")), timeout); })]);
      const {wrapped, value} = responseContent(response);
      if (wrapped?.status === "error") {
        const code = codeOf(wrapped) || "invalid_response";
        throw failure(code, true, {reason: wrapped.reason || "", error: wrapped.reason || ""});
      }
      if (!value || typeof value !== "object" || Array.isArray(value)) throw failure("invalid_response");
      return value;
    } finally { clearTimeout(timer); } }
  const isBridgeTimeout = err => (err?.code || err?.message) === "bridge_timeout";
  async function configureTask(payload) {
    // The host makes configure idempotent by operation_key. A bridge timeout
    // is therefore an unknown result, not a failed write: re-send that exact
    // operation once before asking the user to reconcile it manually.
    try { return await api("POST", path + "/configure", payload, TASK_CONFIG_TIMEOUT_MS); }
    catch (err) {
      if (!isBridgeTimeout(err)) throw err;
      return api("POST", path + "/configure", payload, TASK_CONFIG_RETRY_TIMEOUT_MS);
    }
  }
  function validateStatus(data) { if (!Array.isArray(data.tasks) || !Array.isArray(data.providers) || data.tasks.some(task =>
    typeof task.kind !== "string" || typeof task.edit_token !== "string" || task.edit_token.length !== 64
    || !task.fields || routing.some(name => !task.fields[name]) || routing.some(name => !Object.hasOwn(task.fields[name], "value")))) throw failure("invalid_response"); return data; }
  function setStatus(s, data) { s.status = validateStatus(normalize(data));
    for (const task of data.tasks) { const draft = s.drafts.get(task.kind);
      if (!draft) { s.drafts.set(task.kind, newDraft(task)); continue; }
      s.drafts.set(task.kind, reconcileDraft(task, draft)); }
    const kinds = new Set(data.tasks.map(task => task.kind)); s.selection = s.selection.filter(kind => kinds.has(kind));
    if (s.expanded && !kinds.has(s.expanded)) s.expanded = null; }
  const projected = (task, patch, name) => Object.hasOwn(patch.changes, name) ? patch.changes[name]
    : (patch.reset.includes(name) ? task.fields[name].inherited_value : task.fields[name].value);
  function validatePatch(task, p) { if (!Object.keys(p.changes).length && !p.reset.length) return false;
    if (Object.hasOwn(p.changes, "prompt") && (typeof p.changes.prompt !== "string" || p.changes.prompt.length > 8000)) return false;
    if (Object.hasOwn(p.changes, "max_output_tokens") && (!Number.isInteger(p.changes.max_output_tokens) || p.changes.max_output_tokens < 1 || p.changes.max_output_tokens > MAX_MODEL_OUTPUT_TOKENS)) return false;
    if (Object.hasOwn(p.changes, "timeout_seconds") && (!Number.isInteger(p.changes.timeout_seconds) || p.changes.timeout_seconds < 1 || p.changes.timeout_seconds > 600)) return false;
    if (Object.hasOwn(p.changes, "replacement_scope") && !["plugin", "conversation", "all"].includes(p.changes.replacement_scope)) return false;
    if (Object.hasOwn(p.changes, "fallback_on_refusal") && typeof p.changes.fallback_on_refusal !== "boolean") return false;
    if (Object.hasOwn(p.changes, "replacement_rules") && (typeof p.changes.replacement_rules !== "string" || p.changes.replacement_rules.length > 24000)) return false;
    if (Object.hasOwn(p.changes, "refusal_terms") && (typeof p.changes.refusal_terms !== "string" || p.changes.refusal_terms.length > 4096)) return false;
    if (coreReply(task)) return Object.keys(p.changes).concat(p.reset).every(name => name === "prompt");
    const primary = projected(task, p, "provider"), fallback = projected(task, p, "fallback"), limit = projected(task, p, "max_output_tokens");
    const picked = [primary, ...(fallback ? [fallback] : [])], providers = picked.map(id => state.status.providers.find(provider => provider.id === id));
    const fallbackExplicit = Object.hasOwn(p.changes, "fallback")
      || (!p.reset.includes("fallback") && task.fields.fallback.explicit);
    return (!fallbackExplicit || !fallback || fallback !== primary)
      && providers.every(provider => provider && task.capabilities.every(cap => provider.capabilities.includes(cap)) && limit <= provider.max_output_tokens); }
  /* ---------- functional markup (design alignment: MODEL_CONFIG_UI_UX_DESIGN_20260926) ---------- */
  /* Stable per-provider hue for the colored routing pills and map dots. */
  const hue = id => { let h = 0; for (const ch of String(id || "")) h = (h * 31 + ch.charCodeAt(0)) % 360; return h; };
  const pillColor = id => id ? `hsl(${hue(id)} 52% 46%)` : "var(--m-line2)";
  const icon = (paths, extra = "") => `<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>${extra}`;
  const ICONS = {
    fn: '<path d="M4 6h16M4 12h10M4 18h7"/><circle cx="18" cy="17" r="3"/>',
    voice: '<path d="M12 3a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3Z"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
    image: '<rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="9" cy="10" r="1.6"/><path d="m4 18 5-5 3 3 3-3 5 5"/>',
    search: '<circle cx="10.8" cy="10.8" r="6.8"/><path d="m16 16 4.5 4.5"/>',
    map: '<circle cx="12" cy="12" r="2.2"/><path d="M12 3a9 9 0 1 0 9 9"/><path d="M12 7a5 5 0 1 0 5 5"/>',
    cfg: '<path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/>',
    pol: '<path d="M4 7h4l3 10h4M14 7h6M17 4l3 3-3 3M8 17H4M5 14l-3 3 3 3"/>',
    reply: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2Z"/>',
    memory: '<circle cx="12" cy="12" r="9"/><path d="M12 8v4l2.5 2.5"/>',
    life: '<rect x="3" y="4" width="18" height="17" rx="2"/><path d="M8 2v4M16 2v4M3 10h18"/>',
    write: '<path d="M4 20l4-1 10-10-3-3L5 16l-1 4Z"/>',
    lane: '<path d="M4 6h16M4 12h16M4 18h16"/>',
    decision: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4"/><circle cx="12" cy="12" r="5"/><path d="m8.5 8.5 7 7M15.5 8.5l-7 7"/>' };
  const groupIcon = name => ({ "对话": ICONS.reply, "主动与记忆": ICONS.memory, "生活与成长": ICONS.life, "私人书写": ICONS.write }[name] || ICONS.lane);
  function topbar(s) {
    return `<div class="model-topbar"><span class="model-topbar-t">当前对象与角色 · 任务 → 模型提供方路由</span>
      <span class="model-pill ${s.status?.host_state === "running" ? "ok" : "warn"}">${esc(s.status?.host_state === "running" ? "运行中" : s.status?.host_state || "未连接")}</span>
      <button type="button" class="button small" data-model-action="refresh" ${s.loading || s.busy ? "disabled" : ""}>刷新配置</button></div>`; }
  function overview(s) { const tasks = snapshot(s), custom = tasks.filter(task => taskFields(task).some(name => task.fields[name].explicit)).length;
    const withFb = tasks.filter(task => fallbackFor(task)).length;
    return `<div class="model-overview"><div><strong>${tasks.length}</strong><span>已注册任务</span></div>
      <div><strong>${s.status?.providers.length || 0}</strong><span>可用模型提供方</span></div>
      <div><strong>${withFb}</strong><span>已设回退</span></div>
      <div><strong>${custom}</strong><span>单独设置的任务</span></div></div>`; }
  function notice(s) { const text = s.notice || (s.loading ? "正在读取任务配置…" : "改动进入待保存队列，按下“保存”后在下一次启动的任务生效。");
    return `<div class="model-notice ${s.bad ? "error" : ""}" role="status" aria-live="polite">${esc(text)}</div>`; }
  function toolbar(s) { const count = selected(s).length;
    return `<div class="model-toolbar"><label class="model-search"><span>搜索任务</span>
        <input type="search" data-model-search value="${esc(s.query)}" placeholder="名称、用途或任务键"></label>
      <div class="model-switches" role="group" aria-label="配置作用域">${[["role", "角色默认"], ["window", windowLabel(s)]].map(([layer, label]) => `<button type="button" data-model-layer="${layer}" aria-pressed="${s.layer === layer}" class="${s.layer === layer ? "active" : ""}" ${s.busy || s.loading ? "disabled" : ""}>${label}</button>`).join("")}</div>
      <label class="model-provider-filter"><span>模型提供方</span><select data-model-provider-filter aria-label="按模型提供方筛选">
        <option value="" ${s.providerFilter ? "" : "selected"}>全部提供方</option>${(s.status?.providers || []).map(provider => `<option value="${esc(provider.id)}" ${s.providerFilter === provider.id ? "selected" : ""}>${esc(providerLabel(provider))}</option>`).join("")}</select></label>
      <div class="model-switches" role="group" aria-label="视图">${[["tasks", "按任务"], ["providers", "按提供方"]].map(([id, label]) => `<button type="button" data-model-view="${id}" aria-pressed="${s.view === id}" class="${s.view === id ? "active" : ""}">${label}</button>`).join("")}</div>
      <div class="model-filters" role="group" aria-label="来源筛选">${[["all", "全部"], ["custom", "单独设置"], ["inherited", "跟随上级"]]
        .map(([id, label]) => { const on = (id === "all" && !s.custom) || s.custom === id; return `<button type="button" data-model-custom="${id}" aria-pressed="${on}" class="${on ? "active" : ""}">${label}</button>`; }).join("")}</div>
      <div class="model-filters" role="group" aria-label="通道筛选">${[["all", "全部通道"], ["foreground", "前台"], ["background", "后台"]]
        .map(([id, label]) => `<button type="button" data-model-lane="${id}" aria-pressed="${s.lane === id}" class="${s.lane === id ? "active" : ""}">${label}</button>`).join("")}</div></div>
    ${count ? `<div class="model-selection" role="status">已选 <strong>${count}</strong> 项任务
      <select data-model-assign aria-label="批量指派模型提供方" ${s.busy || s.loading || s.denied ? "disabled" : ""}><option value="">批量指派模型提供方…</option>${(s.status?.providers || []).map(provider => `<option value="${esc(provider.id)}">${esc(provider.id)}</option>`).join("")}</select>
      <button type="button" class="button small" data-model-action="inherit" ${s.busy || s.loading || s.denied ? "disabled" : ""}>恢复跟随上级</button>
      <button type="button" class="button small" data-model-action="clear-selection">取消选择</button></div>` : ""}`; }
  function queue(s) { const count = pendingCount(s), pending = pendingSaveCount(s); if (!count && !pending) return "";
    const status = [count ? `<b>${count}</b> 项改动待保存` : "",
      pending ? `<b>${pending}</b> 项保存结果待核对` : ""].filter(Boolean).join(" · ");
    return `<div class="model-queue ${s.busy ? "busy" : ""}" role="status">
      <span>${status}${count ? " · 保存前不影响正在进行的任务" : " · 可重试原操作，服务端会按操作键去重"}</span>
      <button type="button" class="button primary small" data-model-action="save-all" ${s.busy || s.loading ? "disabled" : ""}>${pending ? (count ? "核对并保存" : "核对保存") : "全部保存"}</button>
      ${count ? `<button type="button" class="button small" data-model-action="discard-all" ${s.busy || s.loading ? "disabled" : ""}>全部放弃</button>` : ""}</div>`; }
  /* Provider 的显示名：`id · 模型名`。
     只写 id 时，同一个 AstrBot 上挂两个同类型 Provider（很常见：一个便宜一个强）
     在下拉里是两行一模一样的字，选错只能靠试。模型名才是区分它们的信息。
     模型名允许为空（本地测试适配器没有这个概念），所以后缀是可选的而不是拼死的。 */
  const providerLabel = provider => provider && provider.model
    ? `${provider.id} · ${provider.model}` : provider ? provider.id : "";
  const capabilityLabel = capability => ({
    text: "文本生成", image: "图片输入", audio: "音频输入", tools: "工具调用",
    embedding: "向量嵌入", video: "视频理解", vision: "图像理解", "conversation.reply": "对话回复",
  }[capability] || capability);
  function providerOptions(s, task, draft, name) { const providers = available(s, task);
    const selected = draft.inherited[name] ? task.fields[name].inherited_value : draft.values[name];
    return `${name === "fallback" ? '<option value="">不使用备用模型</option>' : ''}${providers.map(provider => `<option value="${esc(provider.id)}" ${selected === provider.id ? "selected" : ""}>${esc(providerLabel(provider))}</option>`).join("")}
      ${selected && !providers.some(p => p.id === selected) ? `<option value="${esc(selected)}" selected>${esc(selected)} · 当前不可用</option>` : ""}`; }
  function sourceCell(s, task, draft) { const field = task.fields.provider;
    if (draft.inherited.provider) return `<span class="model-source inherit">跟随 ${esc(configSource(field.inherited_source || "default"))}</span>`;
    if (field.explicit) return `<span class="model-source own">${draft.layer === "role" ? "角色单独设置" : windowLabel(s) + "单独设置"}</span>`;
    return `<span class="model-source pending">待保存</span>`; }
  function conflictNotice(task, draft) {
    if (!draft.stale) return "";
    const value = field => String(field ?? "").slice(0, 200) || "空";
    const details = (draft.conflicts || []).map(name => `<p><b>${esc(fieldLabel[name] || name)}</b>：当前值「${esc(value(task.fields[name].value))}」；草稿「${esc(draft.inherited[name] ? "跟随上级" : value(draft.values[name]))}」</p>`).join("");
    return `<div class="model-alert" role="alert">${details ? "以下字段也在别处改过，请核对后选择保留草稿或放弃。" : "配置已更新，请重新读取后核对草稿。"}${details}</div>`;
  }
  /* Design: precise config uses one card per task (sc.fn), not a table. */
  function coreReplyCard(s, task) {
    const draft = s.drafts.get(task.kind), locked = s.loading || s.busy || s.denied;
    const fieldLocked = locked || !!draft.pending;
    return `<article class="model-fncard${s.expanded === task.kind ? " open" : ""}" data-fncard="${esc(task.kind)}">
      <div class="model-sc-tp"><div class="model-eb">对话<span class="st"><span class="model-tag g">AstrBot 核心</span></span></div>
        <div class="model-ti">${icon(ICONS.reply)}<b>对话回复</b></div><p class="model-ds">${esc(task.routing_note || "普通回复采用 AstrBot 当前会话模型。")}</p></div>
      <div class="model-sc-bd">
        ${conflictNotice(task, draft)}
        ${draft.pending ? '<div class="model-alert">上次保存结果未确认，请核对原保存。</div>' : ""}
        <label class="model-fd dash"><span class="model-cap">对话附加提示 <em>直接注入核心回复 · 最多 8000 字</em></span>
          <textarea rows="5" maxlength="8000" data-model-field="prompt" data-model-kind="${esc(task.kind)}" ${fieldLocked ? "disabled" : ""}>${esc(effective(task, draft, "prompt"))}</textarea></label>
        <div class="model-inh"><label><input type="checkbox" data-model-inherit="prompt" data-model-kind="${esc(task.kind)}" ${draft.inherited.prompt ? "checked" : ""} ${fieldLocked ? "disabled" : ""}>附加提示跟随上级</label></div>
      </div><div class="model-sc-rf"><div class="r"><b>模型与呈现</b><span>AstrBot 当前会话的模型、工具往返、流式、分段和插件装饰</span></div>
        <div class="r"><b>提示组成</b><span>在「治理与维护 → 任务提示词」查看 V2 附加层与背景规则；人格与历史在 AstrBot 管理。</span></div></div>
      <div class="model-sc-ft"><code>${esc(task.kind)}</code><span class="model-grow"></span>
        ${draft.stale && !draft.pending ? `<button type="button" class="button small" data-model-action="rebase" data-model-kind="${esc(task.kind)}">已核对，保留草稿</button>` : ""}
        <button type="button" class="button small" data-model-action="discard" data-model-kind="${esc(task.kind)}" ${locked || draft.pending ? "disabled" : ""}>放弃</button>
        <button type="button" class="button small primary" data-model-action="save-one" data-model-kind="${esc(task.kind)}" ${locked || (!draft.pending && (draft.stale || !dirty(task, draft))) ? "disabled" : ""}>${draft.pending ? "核对保存" : "保存"}</button>
      </div></article>`;
  }
  function fnCard(s, task) { const info = taskName(task.kind), draft = s.drafts.get(task.kind), locked = s.loading || s.busy || s.denied;
    if (coreReply(task)) return coreReplyCard(s, task);
    const fieldLocked = locked || !!draft.pending;
    const picked = s.selection.includes(task.kind), open = s.expanded === task.kind, waiting = dirty(task, draft) && !draft?.pending;
    const limit = limitFor(s, task, draft);
    const main = providerFor(task, draft), fallback = effective(task, draft, "fallback");
    const own = fields.some(name => !draft.inherited[name]);
    const caps = available(s, task);
    const testing = main && s.testing.has(main), result = main ? s.providerTests[main] : null;
    return `<article class="model-fncard${open ? " open" : ""}${waiting ? " dirty" : ""}${picked ? " picked" : ""}" data-fncard="${esc(task.kind)}">
      <div class="model-sc-tp">
        <div class="model-eb">${draft.layer === "role" ? "角色默认" : windowLabel(s)} · ${esc(info.group)}<span class="st">${own ? '<span class="model-tag a">单独设置</span>' : '<span class="model-tag n">跟随上级</span>'}</span></div>
        <div class="model-ti">${icon(groupIcon(info.group))}<b>${esc(info.label)}</b>
          <label class="model-pick"><input type="checkbox" data-model-pick="${esc(task.kind)}" ${picked ? "checked" : ""} aria-label="选择 ${esc(info.label)}"></label>
          <span class="model-tag ${task.lane === "foreground" ? "g" : "n"}">${task.lane === "foreground" ? "前台" : "后台"}</span></div>

        <div class="model-rl">${task.capabilities.map(cap => `<span>${esc(capabilityLabel(cap))}</span>`).join("") || "<span>无能力声明</span>"}</div>
      </div>
      <div class="model-sc-bd">
        ${conflictNotice(task, draft)}
        ${draft.pending ? '<div class="model-alert">上次保存结果未确认。请核对原操作，避免重复提交。</div>' : ""}
        <label class="model-fd"><span class="model-cap">主模型提供方 <em>上级默认 ${esc(task.fields.provider.inherited_value || "未设置")}</em></span>
          <span class="model-msel"><i style="background:${pillColor(main)}"></i><select data-model-field="provider" data-model-kind="${esc(task.kind)}" aria-label="${esc(info.label)} 主模型提供方" ${fieldLocked ? "disabled" : ""}>${providerOptions(s, task, draft, "provider")}</select></span></label>
        <label class="model-fd"><span class="model-cap">备用模型提供方 <em>主模型失败、超时或拒答时尝试一次</em></span>
          <span class="model-msel"><i style="background:${pillColor(fallback)}"></i><select data-model-field="fallback" data-model-kind="${esc(task.kind)}" aria-label="${esc(info.label)} 备用模型提供方" ${fieldLocked ? "disabled" : ""}>${providerOptions(s, task, draft, "fallback")}</select></span></label>
        <div class="model-duo">
          <label class="model-fd"><span class="model-cap">请求超时</span>
            <span class="model-unit"><input type="number" min="1" max="600" step="1" data-model-field="timeout_seconds" data-model-kind="${esc(task.kind)}" value="${esc(effective(task, draft, "timeout_seconds"))}" ${fieldLocked ? "disabled" : ""}><i>秒</i></span></label>
          <label class="model-fd"><span class="model-cap">单次输出上限</span>
            <span class="model-unit"><input type="number" min="1" max="${limit}" step="1" data-model-field="max_output_tokens" data-model-kind="${esc(task.kind)}" value="${esc(effective(task, draft, "max_output_tokens"))}" ${fieldLocked ? "disabled" : ""}><i>令牌</i></span></label>
        </div>
        <label class="model-fd dash"><span class="model-cap">任务附加提示词 <em>最多 8000 字</em></span>
          <textarea rows="3" maxlength="8000" data-model-field="prompt" data-model-kind="${esc(task.kind)}" ${fieldLocked ? "disabled" : ""}>${esc(draft.inherited.prompt ? task.fields.prompt.inherited_value : draft.values.prompt)}</textarea></label>
        <div class="model-inh"><span class="lab">未单独设置时跟随上级</span>
          ${fields.map(name => `<label><input type="checkbox" data-model-inherit="${name}" data-model-kind="${esc(task.kind)}" ${draft.inherited[name] ? "checked" : ""} ${fieldLocked ? "disabled" : ""}>${esc(fieldLabel[name])}</label>`).join("")}</div>
      </div>
      <div class="model-sc-rf">
        <div class="r"><b>来源</b><span>${sourceCell(s, task, draft)}</span></div>
        <div class="r"><b>当前使用</b><span class="model-mono">${esc(main || "未设置")}${fallback ? " → " + esc(fallback) : ""}</span></div>
        <div class="r"><b>采用</b><span class="model-state ${draft?.pending ? "warn" : ""}">${esc(draft?.pending ? "待核对原保存" : adoption(task.application_state))}</span></div>
      </div>
      <div class="model-sc-ft"><code>${esc(task.kind)}</code>
        <span class="model-res">${result ? `<span class="model-resk ${result.status === "ok" ? "ok" : "bad"}">${result.status === "ok" ? "✓" : "！"}</span>${esc(probeLabel(result))}` : testing ? '<span class="model-sp"></span>测试中' : ""}</span>
        <button type="button" class="button small" data-provider-test="${esc(main)}" ${!main || testing ? "disabled" : ""}>测试</button>
        <span class="model-grow"></span>
        ${draft.stale && !draft.pending ? `<button type="button" class="button small" data-model-action="rebase" data-model-kind="${esc(task.kind)}">已核对，保留草稿</button>` : ""}
        <button type="button" class="button small" data-model-action="discard" data-model-kind="${esc(task.kind)}" ${locked || draft.pending ? "disabled" : ""}>放弃</button>
        <button type="button" class="button small primary" data-model-action="save-one" data-model-kind="${esc(task.kind)}" ${locked || (!draft.pending && (draft.stale || !dirty(task, draft))) ? "disabled" : ""}>${draft.pending ? "核对保存" : "保存"}</button>
      </div>
    </article>`; }
  function taskCards(s) { const rows = visible(s); if (!rows.length) return '<p class="model-empty">没有匹配的任务。</p>';
    return `<div class="model-qgrid">${rows.map(task => fnCard(s, task)).join("")}</div>`; }
  function providerView(s) { const providers = s.status?.providers || []; if (!providers.length) return '<p class="model-empty">尚未读取到模型提供方。</p>';
    const tasks = visible(s).filter(task => !coreReply(task)), filtered = !!(s.query || s.custom || s.lane !== "all" || s.providerFilter);
    const panels = providers.filter(provider => !s.providerFilter || s.providerFilter === provider.id).map(provider => {
      const capable = tasks.filter(task => task.capabilities.every(cap => provider.capabilities.includes(cap)));
      const routed = tasks.filter(task => providerFor(task, s.drafts.get(task.kind)) === provider.id);
      if (filtered && !routed.length && s.providerFilter !== provider.id) return "";
      return `<article class="model-provider-panel"><header><span><strong>${esc(provider.id)}</strong><em>${routed.length} / ${capable.length} 项任务走这里</em></span>
          <span class="model-state">上限 ${provider.max_output_tokens}</span></header>
        ${provider.model ? `<p class="model-provider-model">${esc(provider.model)}</p>` : ""}
        <p class="model-provider-caps">${provider.capabilities.map(cap => `<small>${esc(capabilityLabel(cap))}</small>`).join("") || "<small>无能力声明</small>"}</p>
         <ul class="model-provider-tasks">${routed.map(task => { const info = taskName(task.kind);
           return `<li><button type="button" data-model-open="${esc(task.kind)}" aria-label="编辑 ${esc(info.label)} 的模型配置">${esc(info.label)}</button></li>`; }).join("") || '<li class="model-empty">还没有任务走这个模型提供方。</li>'}</ul>
         ${capable.length > routed.length ? `<p class="model-provider-note">还有 ${capable.length - routed.length} 项能力相符的任务由其他模型提供方承担。</p>` : ""}</article>`; }).filter(Boolean);
    return panels.length ? `<div class="model-providers">${panels.join("")}</div>` : '<p class="model-empty">没有匹配的任务。</p>'; }
  function body(s) { if (!s.status) return s.bad
    ? `<div class="model-empty" role="status"><p>任务配置尚未读取成功，请重新读取。</p><button type="button" class="button primary small" data-model-action="refresh" ${s.loading || s.busy ? "disabled" : ""}>重新读取</button></div>`
    : '<p class="model-empty">角色就绪后显示可用模型任务。</p>';
    if (!snapshot(s).length) return '<p class="model-empty">当前没有可用的模型任务。</p>';
    return s.view === "providers" ? providerView(s) : taskCards(s); }
  function syncInput(kind) { const s = state; if (!root || !s) return;
    const slot = root.querySelector(".model-queue-slot"); if (slot) slot.innerHTML = queue(s);
    const task = taskOf(s, kind), draft = s.drafts.get(kind);
    const save = [...root.querySelectorAll('[data-model-action="save-one"]')].find(button => button.dataset.modelKind === kind);
    if (save && task && draft) { save.disabled = !draft.pending && (draft.stale || !dirty(task, draft)); save.textContent = draft.pending ? "核对保存" : "保存"; } }
  /* ---------- functional actions ---------- */
  async function refresh() { const s = state, ticket = version; if (!s || s.loading || s.busy) return; s.loading = true; s.notice = "正在读取任务配置…"; render();
    try { const data = await api("GET", path, {layer: s.layer || "role"}, TASK_CONFIG_TIMEOUT_MS); if (!current(ticket, s)) return; setStatus(s, data); s.bad = false; s.denied = false; s.notice = "已读取当前任务配置。"; }
    catch (err) { if (!current(ticket, s)) return;
      /* 失败后的页面只能显示错误与重试入口，不能把上一次的任务路由、Provider
         和可编辑草稿继续当成当前真实配置。未提交草稿和待核对保存仍保留，
         下一次成功读取会由 setStatus() 按令牌重新核对。 */
      s.status = null;
      s.selection = [];
      s.expanded = null;
      for (const draft of s.drafts.values()) {
        if (draft && (draft.touched || draft.pending)) draft.stale = true;
      }
      s.bad = true; s.denied = false; s.notice = fail(err, "无法读取任务配置，请点击重新读取。"); }
    finally { s.loading = false; if (current(ticket, s)) render(); } }
  async function reloadConflict(s, ticket) {
    const data = await api("GET", path, {layer: s.layer || "role"}, TASK_CONFIG_TIMEOUT_MS);
    if (!current(ticket, s)) return false;
    setStatus(s, data); s.denied = false;
    const conflict = snapshot(s).find(task => s.drafts.get(task.kind)?.stale && !s.drafts.get(task.kind)?.pending);
    if (conflict) {
      s.sub = "cfg"; s.cm = "precise"; s.view = "tasks"; s.expanded = conflict.kind;
      s.query = ""; s.providerFilter = null; s.custom = null; s.lane = "all";
    }
    return true;
  }
  async function rebase(s, kind) {
    const ticket = version; s.busy = true; s.notice = "正在核对最新配置…"; render();
    try {
      if (!await reloadConflict(s, ticket)) return;
      const task = taskOf(s, kind), draft = s.drafts.get(kind);
      if (!task || !draft || draft.pending || draft.layer !== (task.configuration_layer || "role")) return;
      draft.token = task.edit_token; draft.base = newDraft(task).base; draft.stale = false; draft.conflicts = [];
      s.notice = "已读取最新配置并保留草稿，点击保存后生效。"; s.bad = false;
    } catch (err) { if (current(ticket, s)) { s.bad = true; s.notice = fail(err, "未能读取最新配置，请重新读取后再核对。"); } }
    finally { s.busy = false; if (current(ticket, s)) render(); }
  }
  async function saveTasks(s, kinds) {
    if (s.busy || s.loading) return;
    if (s.denied) { s.bad = true; s.notice = "当前尚未读取到任务配置，请先点击重新读取。"; render(); return; }
    const entries = kinds.map(kind => ({kind, task: taskOf(s, kind), draft: s.drafts.get(kind)}))
      .filter(item => item.task && item.draft);
    const pending = entries.filter(item => item.draft.pending);
    const unsaved = entries.filter(item => !item.draft.pending && !item.draft.stale && dirty(item.task, item.draft));
    const stale = entries.filter(item => !item.draft.pending && item.draft.stale);
    if (!pending.length && !unsaved.length) {
      s.bad = true;
      s.notice = stale.length ? `${stale.length} 项草稿已过期，请核对最新配置后再保存。`
        : "这些任务没有待保存的改动。";
      render(); return;
    }
    const ticket = version; s.busy = true; s.bad = false;
    s.notice = pending.length && !unsaved.length ? `正在核对 ${pending.length} 项原保存结果…`
      : `正在保存 ${pending.length + unsaved.length} 项任务路由…`;
    render();
    const failed = []; let saved = 0, confirmed = 0, conflict = false, layerChanged = false;
    for (const kind of pending.concat(unsaved).map(item => item.kind)) {
      if (ticket !== version) return;
      const task = taskOf(s, kind), draft = s.drafts.get(kind); if (!task || !draft) continue;
      let payload = draft.pending;
      if (!payload) {
        const patch = patchFor(task, draft);
        if (!validatePatch(task, patch)) { failed.push(`${taskName(kind).label}：字段校验未通过`); continue; }
        payload = {kind, layer: draft.layer, operation_key: crypto.randomUUID(), edit_token: draft.token, ...patch};
        draft.pending = payload; storePending(kind, payload); render();
      }
      try { const result = await configureTask(payload);
        if (ticket !== version) return;
        if (result?.task?.kind !== kind || typeof result.replayed !== "boolean") throw failure("invalid_response");
        storePending(kind, null); draft.pending = null;
        if (result.task.configuration_layer && result.task.configuration_layer !== s.layer) {
          layerChanged = true;
        } else {
          const index = s.status.tasks.findIndex(t => t.kind === kind); if (index >= 0) s.status.tasks[index] = result.task;
          s.drafts.set(kind, newDraft(result.task));
        }
        saved++; if (result.replayed) confirmed++;
      } catch (err) { if (ticket !== version) return;
        if (err.confirmed) { storePending(kind, null); draft.pending = null;
          if ((err.code || err.message) === "configuration_revision_conflict") { draft.stale = true; conflict = true; } }
        failed.push(`${taskName(kind).label}：${fail(err, "保存未通过")}`); }
      render();
    }
    if (ticket !== version) return;
    if (conflict) { try { await reloadConflict(s, ticket); } catch { /* Keep the draft locked until a successful read. */ } }
    if (ticket !== version) return;
    if (layerChanged) { s.busy = false; await refresh(); if (!s.bad) s.notice = "当前作用域已重新读取，请核对保留的草稿。"; return; }
    s.busy = false; s.bad = failed.length > 0;
    const leftPending = kinds.reduce((count, kind) => count + (s.drafts.get(kind)?.pending ? 1 : 0), 0);
    const recovery = leftPending
      ? `其中 ${leftPending} 项保存结果仍待核对；可再次点击全部保存复用原操作键重试。`
      : conflict ? "有冲突的字段已标出，请核对后继续保存。"
      : "改动仍保留，可修改后继续保存。";
    if (failed.length) {
      s.notice = `已保存 ${saved} 项；${failed.length} 项未通过：${failed.join("；")} ${recovery}`;
    } else {
      s.notice = confirmed && saved === confirmed
        ? `原保存已确认（${confirmed} 项）。下一次任务会采用当前配置。`
        : `已保存 ${saved} 项。下一次启动的任务会采用当前配置。`;
      s.bad = false;
    }
    render(); }
  async function retry(s, kind) { return saveTasks(s, [kind]); }
  function assign(s, providerId) { const kinds = selected(s); if (!providerId || !kinds.length) return;
    const skipped = []; let changed = 0;
    for (const kind of kinds) { const task = taskOf(s, kind), draft = s.drafts.get(kind); if (!task || !draft || draft.pending || draft.stale) continue;
      if (!task.capabilities.every(cap => s.status.providers.find(p => p.id === providerId)?.capabilities.includes(cap))) { skipped.push(taskName(kind).label); continue; }
      if (draft.values.provider === providerId && !draft.inherited.provider) continue;
      draft.inherited.provider = false; draft.values.provider = providerId; draft.touched = true; fitLimit(s, task, draft); changed++; }
    s.bad = skipped.length > 0;
    s.notice = changed ? `已把 ${changed} 项任务指派给 ${providerId}${skipped.length ? `；${skipped.length} 项能力不符已跳过：${skipped.join("、")}` : ""}。记得保存。`
      : (skipped.length ? `没有任务被改动；能力不符：${skipped.join("、")}。` : "没有任务被改动。");
    render(); }
  function discard(s, kinds) { let count = 0;
    for (const kind of kinds) { const task = taskOf(s, kind), draft = s.drafts.get(kind); if (!task || !draft || draft.pending) continue;
      s.drafts.set(kind, newDraft(task)); count++; }
    s.bad = false; s.notice = count ? `已放弃 ${count} 项任务的草稿。` : '没有可放弃的草稿。'; render(); }
  function restoreInherit(s, kinds) { let count = 0;
    for (const kind of kinds) { const task = taskOf(s, kind), draft = s.drafts.get(kind); if (!task || !draft || draft.pending) continue;
      for (const name of taskFields(task)) draft.inherited[name] = true;
      draft.touched = true; count++; }
    s.bad = false; s.notice = count ? `已把 ${count} 项任务改为跟随上级配置，保存后生效。` : '没有可调整的任务。'; render(); }
  /* ---------- functional events ---------- */
  function fnInput(event) { const s = state; if (!s || s.busy || s.loading || s.denied) return; const target = event.target;
    /* The policy pane owns toggles and selects, so it gets every input event. */
    if (s.sub === "pol") { event.type === "change" ? ruleChange(s, event) : ruleInput(s, event); return; }
    if (target.matches("[data-model-search]")) { s.query = target.value; render(); const next = root.querySelector("[data-model-search]");
      if (next) { next.focus(); next.setSelectionRange(next.value.length, next.value.length); } return; }
    if (target.matches("[data-model-provider-filter]")) { if (event.type !== "change") return; s.providerFilter = target.value || null; render(); return; }
    if (target.matches("[data-model-slot]")) { s.slotDraft[target.dataset.modelSlot] = target.value; render(); return; }
    const kind = target.dataset.modelKind;
    if (target.matches("[data-model-assign]")) { assign(s, target.value); target.value = ""; return; }
    if (!kind) return;
    const inheritName = target.dataset.modelInherit;
    if (inheritName) { const draft = s.drafts.get(kind); if (draft && !draft.pending && taskFields(taskOf(s, kind)).includes(inheritName)) { draft.inherited[inheritName] = target.checked; draft.touched = true; render(); } return; }
    const name = target.dataset.modelField; if (!name || !fields.includes(name)) return;
    const draft = s.drafts.get(kind), task = taskOf(s, kind); if (!draft || !task || draft.pending) return;
    if (!taskFields(task).includes(name)) return;
    draft.values[name] = ["max_output_tokens", "timeout_seconds"].includes(name)
      ? (Number.isFinite(target.valueAsNumber) ? target.valueAsNumber : draft.values[name]) : target.value;
    draft.inherited[name] = false;
    const adjusted = (name === "provider" || name === "fallback") && fitLimit(s, task, draft);
    if (adjusted) { s.notice = `${taskName(kind).label} 的输出上限已调整为 ${draft.values.max_output_tokens}，请核对后保存。`; s.bad = false; }
    draft.touched = true;
    if (event.type === "change") render(); else syncInput(kind); }
  function fnClick(event) { const s = state; if (!s) return;
    if (event.target.closest("[data-drawer-close]")) { s.drawer = null; render(); return; }
    const pill = event.target.closest("[data-provider-pill]");
    if (pill) { s.drawer = s.drawer === pill.dataset.providerPill ? null : pill.dataset.providerPill; render(); return; }
    const goto = event.target.closest("[data-drawer-goto]");
    if (goto) { s.drawer = null; s.sub = "cfg"; s.cm = "precise"; s.view = "tasks"; s.providerFilter = goto.dataset.drawerGoto; render(); return; }
    const exp = event.target.closest("[data-model-exp]");
    if (exp) { const key = exp.dataset.modelExp; s.expOpen[key] = !s.expOpen[key]; rememberExpanded(s, key); render(); return; }
    const gpick = event.target.closest("[data-model-gpick]");
    if (gpick) { const key = gpick.dataset.modelGpick;
      const kinds = key.startsWith("lane:") ? snapshot(s).filter(t => t.lane === key.slice(5)).map(t => t.kind)
        : (taskGroups(s).find(([name]) => name === key) || [null, []])[1].map(([kind]) => kind);
      if (kinds.length) { const all = kinds.every(kind => s.rule.picked.includes(kind));
        s.rule.picked = all ? s.rule.picked.filter(kind => !kinds.includes(kind)) : Array.from(new Set(s.rule.picked.concat(kinds)));
        s.rule.report = null; render(); } return; }
    const sub = event.target.closest("[data-model-sub]"); if (sub) { s.sub = sub.dataset.modelSub; render(); return; }
    /* The select-all checkbox lives inside the row button, so the pick has to win. */
    const mapPick = event.target.closest("[data-map-pick]");
    if (mapPick) { const kind = mapPick.dataset.mapPick;
      s.rule.picked = s.rule.picked.includes(kind) ? s.rule.picked.filter(item => item !== kind) : s.rule.picked.concat(kind);
      s.rule.report = null; render(); return; }
    /* Opening a task jumps to its precise editor, from any subtab or provider view. */
    const openTask = event.target.closest("[data-model-open]");
    if (openTask) { const kind = openTask.dataset.modelOpen, wasOpen = s.sub === "cfg" && s.cm === "precise" && s.view === "tasks";
      s.sub = "cfg"; s.view = "tasks"; s.cm = "precise"; s.expanded = wasOpen && s.expanded === kind ? null : kind; render();
      if (s.expanded) { try { const card = root.querySelector('[data-fncard="' + kind + '"]');
        if (card && card.scrollIntoView) card.scrollIntoView({ block: "nearest", behavior:
          (typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches) ? "auto" : "smooth" }); } catch { } }
      return; }
    const policyMode = event.target.closest("[data-model-pm]");
    if (policyMode) { s.pm = policyMode.dataset.modelPm; render(); return; }
    if (s.sub === "pol") { ruleClick(s, event); return; }
    const mapClear = event.target.closest("[data-map-clear]"); if (mapClear) { s.rule.picked = []; s.rule.report = null; render(); return; }
    const mapTest = event.target.closest("[data-map-test]"); if (mapTest) { runMapTest(s); return; }
    const providerTest = event.target.closest("[data-provider-test]");
    if (providerTest) { probeProvider(s, providerTest.dataset.providerTest); return; }
    const cm = event.target.closest("[data-model-cm]"); if (cm) { s.cm = cm.dataset.modelCm; render(); return; }
    const av = event.target.closest("[data-model-av]"); if (av) { s.av = av.dataset.modelAv; render(); return; }
    const af = event.target.closest("[data-model-af]"); if (af) { s.af = af.dataset.modelAf; render(); return; }
    const slotApply = event.target.closest("[data-model-slot-apply]"); if (slotApply) { applySlot(s, slotApply.dataset.modelSlotApply); return; }
    const pick = event.target.closest("[data-model-pick]");
    if (pick) { const kind = pick.dataset.modelPick; s.selection = s.selection.includes(kind) ? s.selection.filter(item => item !== kind) : s.selection.concat(kind); render(); return; }
    const custom = event.target.closest("[data-model-custom]");
    if (custom) { const id = custom.dataset.modelCustom; s.custom = id === "all" ? null : id; render(); return; }
    const layer = event.target.closest("[data-model-layer]");
    if (layer) {
      const selectedLayer = layer.dataset.modelLayer;
      if (selectedLayer === s.layer) return;
      const pending = [...s.drafts.entries()].some(([kind, draft]) => draft.pending || taskOf(s, kind) && dirty(taskOf(s, kind), draft));
      if (pending) { s.bad = true; s.notice = "请先保存或放弃当前范围的改动，再切换配置作用域。"; render(); return; }
      s.layer = selectedLayer; s.drafts.clear(); s.selection = []; s.expanded = null; refresh(); return;
    }
    const lane = event.target.closest("[data-model-lane]");
    if (lane) { s.lane = lane.dataset.modelLane; render(); return; }
    const view = event.target.closest("[data-model-view]");
    if (view) { s.view = view.dataset.modelView; render(); return; }
    const action = event.target.closest("[data-model-action]")?.dataset.modelAction; if (!action || s.busy && action !== "refresh") return;
    if (action === "refresh") { refresh(); return; }
    if (action === "clear-selection") { s.selection = []; render(); return; }
    if (action === "inherit") { restoreInherit(s, selected(s)); return; }
    if (action === "discard-all") { discard(s, selected(s).length ? selected(s) : snapshot(s).map(task => task.kind)); return; }
    if (action === "save-all") { const kinds = snapshot(s).map(task => task.kind); saveTasks(s, kinds); return; }
    const kind = event.target.closest("[data-model-action]")?.dataset.modelKind; if (!kind || s.busy || s.loading) return;
    const draft = s.drafts.get(kind); if (!draft) return;
    if (action === "save-one") { saveTasks(s, [kind]); return; }
    if (action === "discard") { s.drafts.set(kind, newDraft(taskOf(s, kind))); s.notice = "草稿已放弃。"; s.bad = false; render(); }
    if (action === "rebase") { rebase(s, kind); return; }
    if (action === "retry") { retry(s, kind); return; } }
  /* ---------- voice model ---------- */
  const voiceCurrent = v => v.data.languages.find(l => l.k === v.ui.lang) || v.data.languages[0];
  const voiceBinding = (v, lang) => v.draft.bindings[lang.k] ?? "";
  const voiceChannelBinding = (v, channel, lang) => v.draft.channels?.[channel]?.[lang.k] ?? "";
  const voiceChanges = v => { const changes = {};
    for (const lang of v.data.languages) { const name = `voice_${lang.k}_provider_id`;
      if (voiceBinding(v, lang) !== lang.bind) changes[name] = voiceBinding(v, lang); }
    for (const channel of v.data.channels || []) for (const lang of v.data.languages) {
      const name = `voice_${channel.k}_${lang.k}_provider_id`;
      const current = voiceChannelBinding(v, channel.k, lang), saved = channel.bindings?.[lang.k] ?? "";
      if (current !== saved) changes[name] = current;
    }
    if (v.draft.timeout_seconds !== v.data.timeout_seconds) changes.voice_timeout_seconds = v.draft.timeout_seconds;
    return changes; };
  const voiceDirty = v => !!v.draft && Object.keys(voiceChanges(v)).length > 0;
  const voiceDraft = data => ({bindings: Object.fromEntries(data.languages.map(lang => [lang.k, lang.bind])),
    channels: Object.fromEntries((data.channels || []).map(channel => [channel.k, {...(channel.bindings || {})}])),
    timeout_seconds: data.timeout_seconds});
  async function voiceRefresh(s) { const v = s.voice, ticket = version; if (v.loading) return; v.loading = true; v.notice = "正在读取语音配置…"; render();
    try { const data = await api("GET", path + "/voice"); if (!current(ticket, s)) return;
      if (!Array.isArray(data.languages) || !Array.isArray(data.providers) || typeof data.edit_token !== "string") throw failure("invalid_response");
      if (v.draft && voiceDirty(v)) v.stale = v.data.edit_token !== data.edit_token;
      else v.draft = voiceDraft(data);
      v.data = data; v.loaded = true; v.bad = false; v.notice = "已读取当前语音服务绑定。"; }
    catch (err) { if (!current(ticket, s)) return; v.bad = true; v.notice = fail(err, "无法读取语音配置。"); }
    finally { v.loading = false; if (current(ticket, s)) render(); } }
  async function voiceSave(s, retry = false) { const v = s.voice;
    if (v.busy || v.loading || !v.data || v.stale || (!retry && !voiceDirty(v))) return;
    if (!Number.isInteger(v.draft.timeout_seconds) || v.draft.timeout_seconds < 1 || v.draft.timeout_seconds > 120) {
      v.bad = true; v.notice = "语音超时需要设置为 1–120 秒。"; render(); return; }
    const ticket = version, command = retry ? v.pending : {operation_key: crypto.randomUUID(), edit_token: v.data.edit_token, changes: voiceChanges(v)};
    if (!command) return; v.pending = command; v.busy = true; v.bad = false; v.notice = "正在保存语音配置…"; render();
    let uncertain = retry;
    try {
      let data;
      try { data = await api("POST", path + "/voice/save", command, VOICE_SAVE_TIMEOUT_MS); }
      catch (err) {
        if (err?.code !== "bridge_timeout" || retry || !current(ticket, s)) throw err;
        uncertain = true;
        v.notice = "保存超时，正在核对原保存结果…"; render();
        data = await api("POST", path + "/voice/save", command, VOICE_SAVE_TIMEOUT_MS);
      }
      if (!current(ticket, s)) return;
      if (!Array.isArray(data.languages) || typeof data.edit_token !== "string") throw failure("invalid_response");
      v.data = data; v.draft = voiceDraft(data); v.pending = null; v.stale = false; v.notice = data.replayed ? "原保存已确认。" : "语音配置已保存，下一次语音回复生效。"; }
    catch (err) { if (!current(ticket, s)) return; v.bad = true;
      if (err.code === "host_busy") {
        /* 核对请求没写入不代表最初的超时请求没写入，仍保留原操作。 */
        if (!uncertain) { v.pending = null; v.stale = false; }
        v.notice = uncertain ? "宿主正忙，原保存结果仍待核对，请稍后核对原保存结果。"
          : "宿主正在启动或切换，请稍后重试；本次没有写入，草稿已保留。";
      } else {
        if (err.confirmed) { v.pending = null; v.stale = true; }
        v.notice = err.confirmed ? (fail(err, "保存未通过，请刷新核对配置。")) : "保存结果未确认，可重试同一请求核对结果。";
      } }
    finally { v.busy = false; if (current(ticket, s)) render(); } }
  function clearVoiceTest(v) { if (v.lastTest?.audioUrl && typeof window.URL?.revokeObjectURL === "function") window.URL.revokeObjectURL(v.lastTest.audioUrl); v.lastTest = null; }
  function voiceAudioUrl(result) {
    if (result.status !== "ok" || typeof result.audio_base64 !== "string" || !result.audio_base64
        || typeof window.atob !== "function" || typeof window.Blob !== "function"
        || typeof window.URL?.createObjectURL !== "function") return "";
    const binary = window.atob(result.audio_base64), bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index);
    const mime = /^audio\/[a-z0-9.+-]+$/i.test(result.audio_mime_type || "") ? result.audio_mime_type : "";
    return window.URL.createObjectURL(new window.Blob([bytes], {type: mime}));
  }
  async function voiceTest(s) { const v = s.voice, lang = voiceCurrent(v), providerId = voiceBinding(v, lang);
    if (!providerId || v.testing || !v.data.providers.some(p => p.id === providerId)) return;
    const ticket = version; v.testing = true; clearVoiceTest(v); v.notice = ""; render();
    try { const result = await api("POST", path + "/voice/test", {provider_id: providerId, language: lang.k}, 125000);
      if (!current(ticket, s)) return;
      const audioUrl = voiceAudioUrl(result);
      const status = result.status === "ok" && !audioUrl ? "unavailable" : result.status || "unknown";
      const safeResult = {...result};
      delete safeResult.audio_base64; delete safeResult.audio_mime_type;
      v.lastTest = {...safeResult, status,
        code: result.code || (status === "unavailable" ? "model_voice_audio_invalid" : ""), audioUrl}; }
    catch (err) { if (!current(ticket, s)) return;
      v.lastTest = {status: "unknown", code: err.code || err.message || "model_voice_probe_failed",
        stage: "request", exception_type: err.name || "Error", error: err.error || err.reason || err.message || "",
        suggestion: "刷新语音配置并确认 Provider 状态后重试。", request_id: err.request_id || ""}; }
    finally { v.testing = false; if (current(ticket, s)) render(); } }
  /* V1-parity diagnostics: the outcome names the failure stage instead of a bare error. */
  const VOICE_SAMPLES = {"zh": "你好。", "ja": "こんにちは。", "en": "Hello."};
  const voiceStage = {provider_lookup: "Provider 查找", synthesis: "调用 Provider 合成", audio_read: "读取音频", provider_recheck: "Provider 复核", request: "请求"};
  const voiceTestLabel = r => r.status === "ok" ? `语音合成成功 · ${r.latency_ms} ms`
    : r.code ? (errors[r.code] || "语音合成结果未知。") : "语音合成结果未知。";
  const voiceTestDiagnostic = r => r.status === "ok" ? "" : `<span class="model-voice-diagnostic">
    ${r.stage ? `<b>阶段：${esc(voiceStage[r.stage] || r.stage)}</b>` : ""}
    ${r.exception_type ? `<span>异常：${esc(r.exception_type)}</span>` : ""}
    ${r.error ? `<span>错误：${esc(r.error)}</span>` : ""}
    ${r.suggestion ? `<span>建议：${esc(r.suggestion)}</span>` : ""}
    ${r.request_id ? `<span>诊断号：${esc(r.request_id)}</span>` : ""}
  </span>`;
  /* ---------- TTS provider management (V1 parity; AstrBot owns provider configs) ---------- */
  async function ttsCatalogLoad(s) { const v = s.voice; if (v.catalogLoading) return;
    const ticket = version; v.catalogLoading = true; render();
    try { const data = await api("GET", path + "/tts/providers"); if (!current(ticket, s)) return;
      v.catalog = data; }
    catch (err) { if (!current(ticket, s)) return; v.catalog = { items: [], templates: [], error: fail(err, "无法读取语音服务配置列表。") }; }
    finally { v.catalogLoading = false; if (current(ticket, s)) render(); } }

  const ttsSelected = v => (v.catalog && v.catalog.items || []).find(item => item.id === v.editorId) || null;
  function ttsValues(v, item) { if (!v.editorValues[item.id]) { const values = {};
      for (const field of item.fields) values[field.key] = field.secret ? "" : (field.value === null || field.value === undefined ? "" : field.value);
      values.enable = item.enable; v.editorValues[item.id] = values; }
    return v.editorValues[item.id]; }

  const ttsFieldRank = field => {
    const key = String(field?.key || "").toLowerCase();
    const exact = {api_base: 0, api_key: 1, proxy: 2, timeout: 3, model: 0,
      "fishaudio-tts-character": 1, "fishaudio-tts-reference-id": 2};
    if (Object.hasOwn(exact, key)) return exact[key];
    if (key.includes("model")) return 4;
    if (key.includes("voice") || key.includes("character")) return 5;
    if (key.includes("reference")) return 6;
    return 20;
  };
  const ttsFieldValue = (field, value) => {
    if (field.type === "object" || field.type === "list") {
      if (typeof value === "string") return value;
      try { return JSON.stringify(value ?? (field.type === "object" ? {} : []), null, 2); }
      catch { return String(value ?? ""); }
    }
    return value === undefined || value === null ? "" : value;
  };
  const ttsField = (field, values) => {
    const attr = `data-tts-field="${esc(field.key)}"`;
    if (field.type === "bool") return `<label class="model-fd"><span class="model-cap">${esc(field.label)}</span>
      <label class="model-switch"><input type="checkbox" ${attr} ${values[field.key] ? "checked" : ""}><i></i></label></label>`;
    if (field.options.length) {
      const current = String(values[field.key] ?? "");
      const known = field.options.some(option => String(option.value) === current);
      return `<label class="model-fd"><span class="model-cap">${esc(field.label)}${field.hint ? ` <em>${esc(field.hint)}</em>` : ""}</span>
        <select ${attr}>${field.options.map(option => `<option value="${esc(option.value)}" ${String(option.value) === current ? "selected" : ""}>${esc(option.label)}</option>`).join("")}${current && !known ? `<option value="${esc(current)}" selected>${esc(current)} · 当前值</option>` : ""}</select></label>`;
    }
    if (field.secret) return `<label class="model-fd"><span class="model-cap">${esc(field.label)} <em>${field.set ? "已配置 · 留空表示保留" : "未配置"}</em></span>
      <input type="password" ${attr} value="${esc(values[field.key] || "")}" placeholder="${field.set ? "已配置，留空表示保留" : "请输入"}" autocomplete="new-password"></label>`;
    const hint = field.hint ? ` <em>${esc(field.hint)}</em>` : "";
    if (field.type === "object" || field.type === "list") return `<label class="model-fd"><span class="model-cap">${esc(field.label)}${hint}</span>
      <textarea ${attr} rows="6" spellcheck="false">${esc(ttsFieldValue(field, values[field.key]))}</textarea></label>`;
    const type = field.type === "int" || field.type === "float" ? "number" : "text";
    const numeric = type === "number";
    const attrs = numeric ? ` step="${esc(field.step ?? (field.type === "int" ? "1" : "any"))}"${field.min !== null && field.min !== undefined ? ` min="${esc(field.min)}"` : ""}${field.max !== null && field.max !== undefined ? ` max="${esc(field.max)}"` : ""}` : "";
    return `<label class="model-fd"><span class="model-cap">${esc(field.label)}${hint}</span>
      <input type="${type}" ${attr} value="${esc(ttsFieldValue(field, values[field.key]))}"${attrs}></label>`; };

  function ttsEditor(s) { const v = s.voice;
    if (!v.editorOpen) return "";
    if (v.catalogLoading) return '<div class="model-card"><p class="model-empty">正在读取语音服务配置…</p></div>';
    if (!v.catalog) return "";
    if (v.catalog.error) return `<div class="model-card"><p class="model-notice error">${esc(v.catalog.error)}</p></div>`;
    const items = v.catalog.items || [];
    const item = ttsSelected(v) || items[0] || null;
    if (item) v.editorId = item.id;
     const creating = v.creating;
     const templates = v.catalog.templates || [];
     const createType = v.createDraft.type || (templates[0] && templates[0].type) || "";
     const fields = item && item.fields ? [...item.fields].sort((left, right) => ttsFieldRank(left) - ttsFieldRank(right)) : [];
     const values = item ? ttsValues(v, item) : null;
     const fieldGroups = [["connection", "连接与凭据"], ["voice", "模型与音色"], ["advanced", "高级参数"]];
     const fieldMarkup = fieldGroups.map(([group, label]) => {
       const groupFields = fields.filter(field => (field.group || "advanced") === group);
       return groupFields.length ? `<section class="model-tts-group"><h4>${label}</h4><div class="model-tts-fields">${groupFields.map(field => ttsField(field, values)).join("")}</div></section>` : "";
     }).join("");
     const testResult = item ? v.ttsTestResults?.[item.id] : null;
     return `<div class="model-card tts-editor"><div class="model-phead"><div class="model-grow"><span class="model-eb">连接管理</span><h3>语音服务配置</h3></div>
        <button type="button" class="button small" data-tts-create-toggle ${v.ttsBusy ? "disabled" : ""}>${creating ? "收起新建" : "+ 新建语音服务"}</button></div>
      ${creating ? `<div class="model-vcreate">
          <label class="model-fd"><span class="model-cap">提供商类型</span>
            <select data-tts-type>${templates.map(template => `<option value="${esc(template.type)}" ${template.type === createType ? "selected" : ""}>${esc(template.label)}</option>`).join("")}</select></label>
          <label class="model-fd"><span class="model-cap">服务标识 <em>字母、数字、点、下划线、冒号、短横线</em></span>
            <input data-tts-id value="${esc(v.createDraft.id)}" placeholder="例如 fishaudio-ja"></label>
          <div class="model-editor-actions"><button type="button" class="button small primary" data-tts-create ${v.ttsBusy || !createType ? "disabled" : ""}>创建（默认停用）</button></div>
        </div>` : ""}
      ${items.length ? `<label class="model-fd"><span class="model-cap">编辑对象</span>
          <select data-tts-pick>${items.map(option => `<option value="${esc(option.id)}" ${option.id === (item && item.id) ? "selected" : ""}>${esc(option.id)} · ${esc(option.label)}${option.enable ? " · 已启用" : " · 停用"}${option.loaded ? "" : " · 未加载"}</option>`).join("")}</select></label>` : '<p class="model-empty">还没有可管理的语音服务。</p>'}
      ${item && values ? `<div class="model-tts-fields">
          <div class="model-fd"><span class="model-cap">启用 <em>停用的语音服务不能被语种绑定使用</em></span>
            <label class="model-switch"><input type="checkbox" aria-label="启用语音服务" data-tts-enable ${values.enable ? "checked" : ""}><i></i></label></div>
           ${fieldMarkup}
        </div>
         <div class="model-editor-actions">
           <button type="button" class="button primary" data-tts-save ${v.ttsBusy ? "disabled" : ""}>保存语音服务配置</button>
           <button type="button" class="button" data-tts-clone ${v.ttsBusy ? "disabled" : ""}>克隆为${esc(voiceCurrent(v).n)}专用</button>
           <button type="button" class="button" data-tts-test ${v.ttsBusy || !item.loaded ? "disabled" : ""}>${v.ttsBusy && v.ttsTesting ? "测试中…" : "测试连接"}</button></div>
         ${testResult ? `<div class="model-tts-test-result ${testResult.status === "ok" ? "ok" : "error"}"><b>${testResult.status === "ok" ? "连接测试通过" : "连接测试未通过"}</b><span>${esc(testResult.error || testResult.detail || "Provider 自检完成")}</span>${testResult.suggestion ? `<small>${esc(testResult.suggestion)}</small>` : ""}</div>` : ""}
         <p class="model-note">新建或克隆后需启用。保存对所有会话生效。</p>` : ""}
    </div>`; }

  async function ttsCreate(s) { const v = s.voice; if (v.ttsBusy) return;
    const providerType = v.createDraft.type || (v.catalog && v.catalog.templates && v.catalog.templates[0] && v.catalog.templates[0].type) || "";
    const providerId = String(v.createDraft.id || "").trim();
    if (!providerType || !providerId) { v.bad = true; v.notice = "先选择类型并填写服务标识。"; render(); return; }
    const ticket = version; v.ttsBusy = true; v.notice = "正在创建语音服务…"; render();
    try { const data = await api("POST", path + "/tts/providers/create", {type: providerType, id: providerId}, 30000);
      if (!current(ticket, s)) return;
      v.catalog = data; v.editorValues = {}; v.editorId = providerId; v.creating = false;
      v.createDraft = { type: "", id: "" }; v.bad = false; v.notice = "语音服务已创建（默认停用），填写连接并启用。"; }
    catch (err) { if (!current(ticket, s)) return; v.bad = true; v.notice = fail(err, "语音服务创建未完成。"); }
    finally { v.ttsBusy = false; if (current(ticket, s)) render(); } }

  async function ttsSave(s) { const v = s.voice, item = ttsSelected(v); if (!item || v.ttsBusy) return;
    const values = ttsValues(v, item), lang = voiceCurrent(v), binding = voiceBinding(v, lang);
    const shared = binding === item.id && v.data.languages.some(other => other.k !== lang.k && voiceBinding(v, other) === item.id);
    const ticket = version; v.ttsBusy = true; v.notice = shared ? "检测到多个语种共用此服务，正在拆分当前语种配置…" : "正在保存语音服务配置…"; render();
    try {
      let data;
      if (shared) {
        data = await api("POST", path + "/tts/providers/clone", {
          provider_id: item.id, language: lang.k,
          config: {enable: Boolean(values.enable), values: {...values}},
        }, 30000);
        const cloneId = data.created_id || data.provider_id;
        if (!cloneId) throw failure("invalid_response");
        v.catalog = data; v.editorValues = {}; v.editorId = cloneId;
        v.draft.bindings[lang.k] = cloneId;
        /* The provider clone and language binding are one user action.  Keep
           the same edit-token/retry semantics as the ordinary voice save. */
        await voiceSave(s);
        v.notice = `已将 ${lang.n} 拆分为 ${cloneId}，其他语种仍使用原服务。`;
      } else {
        data = await api("POST", path + "/tts/providers/update", {provider_id: item.id, values}, 30000);
        if (!current(ticket, s)) return;
        v.catalog = data; delete v.editorValues[item.id]; v.bad = false; v.notice = "语音服务配置已保存，并已写入 AstrBot。";
      }
      if (!current(ticket, s)) return;
      if (!shared) v.bad = false;
      }
    catch (err) { if (!current(ticket, s)) return; v.bad = true; v.notice = fail(err, "语音服务配置保存未完成。"); }
    finally { v.ttsBusy = false; if (current(ticket, s)) render(); } }

  async function ttsClone(s) { const v = s.voice, item = ttsSelected(v); if (!item || v.ttsBusy) return;
    const lang = voiceCurrent(v), values = ttsValues(v, item), ticket = version; v.ttsBusy = true; v.notice = "正在克隆语音服务…"; render();
    try { const data = await api("POST", path + "/tts/providers/clone", {provider_id: item.id, language: lang.k,
        config: {enable: Boolean(values.enable), values: {...values}}}, 30000);
      if (!current(ticket, s)) return;
      v.catalog = data; v.editorValues = {}; v.editorId = data.created_id || v.editorId;
      if (v.draft) v.draft.bindings[lang.k] = data.created_id || "";
      v.bad = false; v.notice = `已克隆为 ${v.editorId}（默认停用），当前语种草稿已指向它；启用并保存语种绑定后生效。`; }
    catch (err) { if (!current(ticket, s)) return; v.bad = true; v.notice = fail(err, "语音服务克隆未完成。"); }
    finally { v.ttsBusy = false; if (current(ticket, s)) render(); } }

  async function ttsTest(s) { const v = s.voice, item = ttsSelected(v); if (!item || v.ttsBusy || !item.loaded) return;
    const ticket = version; v.ttsBusy = true; v.ttsTesting = true; v.notice = "正在测试语音服务连接…"; render();
    try { const result = await api("POST", path + "/tts/providers/test", {provider_id: item.id}, 95000);
      if (!current(ticket, s)) return;
      v.ttsTestResults = {...(v.ttsTestResults || {}), [item.id]: result};
      v.bad = result.status !== "ok";
      v.notice = result.status === "ok" ? "语音服务连接测试通过。" : (result.error || "语音服务连接测试未通过。");
    } catch (err) { if (!current(ticket, s)) return;
      v.ttsTestResults = {...(v.ttsTestResults || {}), [item.id]: {status: "unknown", code: err.code || err.message || ""}};
      v.bad = true; v.notice = fail(err, "语音服务连接测试未完成。");
    } finally { v.ttsBusy = false; v.ttsTesting = false; if (current(ticket, s)) render(); } }

  function voicePane(s) { const v = s.voice; if (!v.data || !v.draft) return `<p class="model-empty">${esc(v.notice || "正在读取语音配置…")}</p>`;
    const lang = voiceCurrent(v), binding = voiceBinding(v, lang), loaded = v.data.providers.some(p => p.id === binding);
    const bound = v.data.languages.filter(item => voiceBinding(v, item)).length;
    const missing = binding && !loaded;
    const shared = binding ? v.data.languages.filter(item => item.k !== lang.k && voiceBinding(v, item) === binding) : [];
    const providerLabel = item => [item?.id, item?.model, item?.voice].filter(Boolean).join(" · ");
    const dot = item => { const b = voiceBinding(v, item); return !b ? "var(--m-line2)" : v.data.providers.some(p => p.id === b) ? "var(--m-green)" : "var(--m-red)"; };
    const sub = item => { const b = voiceBinding(v, item); const provider = v.data.providers.find(p => p.id === b);
      return b ? esc(provider ? providerLabel(provider) : b) + (provider ? " · 已加载" : " · 未加载") : "跟随当前会话的语音服务"; };
    const test = v.lastTest;
    const channels = v.data.channels || [];
    const voiceOptions = selected => ['<option value="">跟随语种绑定</option>',
      ...v.data.providers.map(p => `<option value="${esc(p.id)}" ${p.id === selected ? "selected" : ""}>${esc(providerLabel(p))}</option>`),
      ...(selected && !v.data.providers.some(p => p.id === selected)
        ? [`<option value="${esc(selected)}" selected>${esc(selected)} · 未加载</option>`] : [])].join("");
    const channelMatrix = channels.length ? `<div class="model-card model-vmatrix-card">
      <div class="model-phead"><div class="model-grow"><span class="model-eb">按渠道指定语音合成</span><h3>私聊 / 群聊 TTS 总览</h3></div>
        <span class="model-tag n">留空沿用语种绑定</span></div>
      <p class="model-note">不必切换上方语种标签，可在这里一次看清并设置每个语种在私聊、群聊分别使用哪个 TTS。列头的「全部跟随」只清空该渠道的覆盖。</p>
      <div class="model-vmatrix" style="--voice-channels:${channels.length}">
        <div class="model-vmatrix-head"><span>语种</span>${channels.map(channel => `<span>${esc(channel.n)}
          <button type="button" class="model-vmatrix-clear" data-voice-clear-channel="${esc(channel.k)}" ${v.busy || v.loading || v.pending ? "disabled" : ""}>全部跟随</button></span>`).join("")}</div>
        ${v.data.languages.map(item => `<div class="model-vmatrix-row"><b>${esc(item.n)}</b>${channels.map(channel => `<label class="model-vmatrix-cell">
          <em class="model-vmatrix-channel">${esc(channel.n)} · ${esc(item.n)}</em>
          <select data-voice-channel="${esc(channel.k)}" data-voice-matrix-lang="${esc(item.k)}" ${v.busy || v.loading || v.pending ? "disabled" : ""}>${voiceOptions(voiceChannelBinding(v, channel.k, item))}</select>
          <small>${voiceChannelBinding(v, channel.k, item) ? "渠道覆盖" : `跟随${esc(item.n)}语种`}</small></label>`).join("")}</div>`).join("")}
      </div></div>` : "";
    return `<div class="model-voice">
      <div class="model-card"><div class="model-phead"><div class="model-grow"><span class="model-eb">合成后端</span><h3>语音模型</h3></div>
          <button type="button" class="button small" data-go="manage" data-manage="expression" data-manage-subtab="voice">语音增强设置 ↗</button>
          <button type="button" class="button small" data-voice-refresh ${v.loading || v.busy ? "disabled" : ""}>刷新</button></div>
        <div class="model-kpis">${kpi(v.data.voice_enabled ? "已开启" : "未开启", "语音回复")}${kpi(bound + "/3", "专用语种绑定")}${kpi(v.data.providers.length, "已加载语音服务")}${kpi(v.data.timeout_seconds + "s", "当前合成超时")}</div></div>
      <div class="model-card"><div class="model-phead"><div class="model-grow"><span class="model-eb">按语种配置语音合成</span><h3>${esc(lang.n)}</h3></div>
          <span class="model-tag ${missing ? "r" : binding ? "g" : "n"}">${missing ? "绑定未加载" : binding ? "已加载" : "跟随会话"}</span>
          <button type="button" class="button small" data-voice-editor ${v.loading || v.busy ? "disabled" : ""}>${v.editorOpen ? "收起连接管理" : "新建 / 编辑连接"}</button></div>
        <div class="model-vtabs">${v.data.languages.map(item => `<button type="button" data-voice-lang="${esc(item.k)}" class="${item.k === lang.k ? "on" : ""}" title="${sub(item)}"><b><i class="d" style="background:${dot(item)}"></i>${esc(item.n)}</b><small>${sub(item)}</small></button>`).join("")}</div>
        ${shared.length ? `<div class="model-sharewarn"><span style="flex:1">该语音服务同时绑定到${shared.map(item => item.n).join("、")}。V2 按语种独立保存，修改当前语种不会影响其他语种。</span>
          <button type="button" class="button small" data-voice-unbind ${v.busy || v.loading || v.pending ? "disabled" : ""}>解除${shared.map(item => item.n).join("、")}绑定</button></div>` : ""}
        <div class="model-vbar"><label class="model-fd"><span class="model-cap">${esc(lang.n)} 语音服务 <em>连接、凭据和声音由 AstrBot 语音配置管理</em></span>
          <select data-voice-provider ${v.busy || v.loading || v.pending ? "disabled" : ""}><option value="">跟随 AstrBot 当前会话的语音服务</option>
            ${v.data.providers.map(p => `<option value="${esc(p.id)}" ${p.id === binding ? "selected" : ""}>${esc(providerLabel(p))}</option>`).join("")}
            ${missing ? `<option value="${esc(binding)}" selected>${esc(binding)} · 未加载</option>` : ""}</select></label></div>
        ${(v.data.channels || []).map(channel => `<div class="model-vbar"><label class="model-fd"><span class="model-cap">${esc(channel.n)} · ${esc(lang.n)} 覆盖 <em>留空跟随上面的语种绑定</em></span>
          <select data-voice-channel="${esc(channel.k)}" ${v.busy || v.loading || v.pending ? "disabled" : ""}><option value="">跟随语种绑定</option>
            ${v.data.providers.map(p => `<option value="${esc(p.id)}" ${voiceChannelBinding(v, channel.k, lang) === p.id ? "selected" : ""}>${esc(providerLabel(p))}</option>`).join("")}
            ${voiceChannelBinding(v, channel.k, lang) && !v.data.providers.some(p => p.id === voiceChannelBinding(v, channel.k, lang)) ? `<option value="${esc(voiceChannelBinding(v, channel.k, lang))}" selected>${esc(voiceChannelBinding(v, channel.k, lang))} · 未加载</option>` : ""}</select></label></div>`).join("")}
        <div class="model-vfoot"><span class="model-sample">合成样句：${esc(VOICE_SAMPLES[lang.k] || "")}</span>
          <span class="model-res">${v.testing ? '<span class="model-sp"></span>测试中'
            : test ? `<span class="model-resk ${test.status === "ok" ? "ok" : "bad"}">${test.status === "ok" ? "✓" : "！"}</span>${esc(voiceTestLabel(test))}` : ""}</span>
          ${test ? voiceTestDiagnostic(test) : ""}
          ${test?.audioUrl ? `<span class="model-audio-label">本次结果 · 试听</span><audio class="model-test-audio" controls preload="none" src="${esc(test.audioUrl)}" aria-label="${esc(lang.n)}语音合成结果试听"></audio>` : ""}
          <span class="model-grow"></span>
          <span class="model-note">${binding ? "测试音频只在此处试听，不会发送到聊天。" : "未指定时由 AstrBot 当前会话选择语音合成服务。"}</span>
          <button type="button" class="button small primary" data-ve-test ${!loaded || v.testing ? "disabled" : ""}>${v.testing ? "测试中…" : "测试合成"}</button></div>
      </div>
      ${channelMatrix}
      ${v.editorOpen ? ttsEditor(s) : ""}
      <div class="model-card"><div class="model-phead"><div class="model-grow"><span class="model-eb">语音请求</span><h3>合成超时与保存</h3></div></div>
        <div class="model-fd"><span class="model-cap">合成超时（秒）</span>
          <span class="model-unit"><input type="number" min="1" max="120" data-voice-timeout value="${esc(v.draft.timeout_seconds)}" ${v.busy || v.loading || v.pending ? "disabled" : ""}><i>秒</i></span></div>
        <p class="model-note">${!v.data.voice_enabled ? "语音回复未开启，请到「表达」启用。" : "发送策略见「表达」。"}</p>
        <div class="model-editor-actions"><button type="button" class="button primary" data-voice-save ${v.busy || v.loading || v.pending || v.stale || !voiceDirty(v) ? "disabled" : ""}>保存语音配置</button>
          ${v.pending ? '<button type="button" class="button" data-voice-retry>核对原保存结果</button>' : ""}
          ${v.stale ? '<button type="button" class="button" data-voice-rebase>核对后用当前版本保存</button>' : ""}</div></div>
      ${v.notice ? `<div class="model-notice ${v.bad ? "error" : ""}" role="status">${esc(v.notice)}</div>` : ""}</div>`; }
  function voiceInput(s, event) { const v = s.voice; if (v.loading || v.busy) return; const t = event.target;
    if (t.matches("[data-voice-provider]")) { if (!v.draft || v.pending) return; v.draft.bindings[voiceCurrent(v).k] = t.value; clearVoiceTest(v); render(); }
    if (t.matches("[data-voice-channel]")) { if (!v.draft || v.pending) return;
      const channel = t.dataset.voiceChannel; if (!v.draft.channels[channel]) v.draft.channels[channel] = {};
      const lang = t.dataset.voiceMatrixLang || voiceCurrent(v).k;
      v.draft.channels[channel][lang] = t.value; clearVoiceTest(v); render(); }
    if (t.matches("[data-voice-timeout]")) { if (!v.draft || v.pending) return; v.draft.timeout_seconds = t.valueAsNumber; if (event.type === "change") render(); }
    if (t.matches("[data-tts-field]")) { const item = ttsSelected(v); if (!item || v.ttsBusy) return;
      const values = ttsValues(v, item), key = t.dataset.ttsField;
      values[key] = t.type === "checkbox" ? t.checked : (t.type === "number" && Number.isFinite(t.valueAsNumber) ? t.valueAsNumber : t.value);
      if (event.type === "change") render(); return; }
    if (t.matches("[data-tts-enable]")) { const item = ttsSelected(v); if (!item || v.ttsBusy) return;
      ttsValues(v, item).enable = t.checked; if (event.type === "change") render(); return; }
    if (t.matches("[data-tts-type]")) { v.createDraft.type = t.value; render(); return; }
    if (t.matches("[data-tts-id]")) { v.createDraft.id = t.value; return; }
    if (t.matches("[data-tts-pick]")) { v.editorId = t.value; if (event.type === "change") render(); return; } }
  function voiceClick(s, event) { const v = s.voice, t = event.target;
    const clearChannel = t.closest("[data-voice-clear-channel]");
    if (clearChannel) {
      if (v.busy || v.loading || v.pending || !v.draft) return;
      const channel = clearChannel.dataset.voiceClearChannel;
      if (!v.draft.channels[channel]) v.draft.channels[channel] = {};
      for (const item of v.data.languages) v.draft.channels[channel][item.k] = "";
      clearVoiceTest(v); render(); return;
    }
    const lang = t.closest("[data-voice-lang]"); if (lang) { if (v.ui.lang !== lang.dataset.voiceLang) clearVoiceTest(v); v.ui.lang = lang.dataset.voiceLang; render(); return; }
    if (t.closest("[data-voice-editor]")) { v.editorOpen = !v.editorOpen; if (v.editorOpen && !v.catalog) ttsCatalogLoad(s); render(); return; }
    if (t.closest("[data-tts-create-toggle]")) { v.creating = !v.creating; render(); return; }
    if (t.closest("[data-tts-create]")) { ttsCreate(s); return; }
    if (t.closest("[data-tts-save]")) { ttsSave(s); return; }
    if (t.closest("[data-tts-clone]")) { ttsClone(s); return; }
    if (t.closest("[data-tts-test]")) { ttsTest(s); return; }
    const unbind = t.closest("[data-voice-unbind]");
    if (unbind) { const current = voiceBinding(v, voiceCurrent(v));
      if (current) v.data.languages.forEach(item => { if (item.k !== v.ui.lang && voiceBinding(v, item) === current) v.draft.bindings[item.k] = ""; });
      clearVoiceTest(v); v.bad = false; v.notice = "已解除其他语种的同一服务绑定，保存语音配置后生效。"; render(); return; }
    if (t.closest("[data-voice-refresh]")) { voiceRefresh(s); return; }
    if (t.closest("[data-voice-save]")) { voiceSave(s); return; }
    if (t.closest("[data-voice-retry]")) { voiceSave(s, true); return; }
    if (t.closest("[data-voice-rebase]")) { v.stale = false; render(); return; }
    if (t.closest("[data-ve-test]")) voiceTest(s); }
  /* ---------- optional Laya decision model ---------- */
  const LAYA_DECISION_FIELDS = [
    ["turn_state", "判断是否说完", "完整表达、明确补充中或暂不确定"],
    ["action", "判断对话行为", "回复、等待、静默、澄清或群聊插话"],
    ["reply_intent", "判断回复意图", "回答、确认、追问、安慰、解释或总结"],
    ["reply_style", "判断表达风格", "简洁、自然、详细、认真或轻松"],
    ["reply_form", "判断回复形式", "文字、语音、文字加语音或图片"],
    ["segment_mode", "判断分段倾向", "单条、自然分段或保持内容完整"],
    ["tool_need", "判断能力需求", "只提供能力提示，不负责授权或发起工具"],
  ];
  const layaDraftFrom = data => ({enabled: !!data.enabled, mode: data.mode || "local", endpoint: data.endpoint || "",
    local_model_path: data.local_model_path || "", remote_model: data.remote_model || "",
    api_key: "", timeout_seconds: Number(data.timeout_seconds) || 20,
    followup_confidence_threshold: Math.max(0, Math.min(100, Number(data.followup_confidence_threshold) || 0)),
    decision_fields: Object.fromEntries(LAYA_DECISION_FIELDS.map(([key]) => [key, data.decision_fields?.[key] !== false]))});
  const layaChanges = l => { const changes = {};
    ["enabled", "mode", "endpoint", "timeout_seconds", "local_model_path", "remote_model", "followup_confidence_threshold"].forEach(key => {
      const current = key === "local_model_path" || key === "remote_model" ? l.data[key] || ""
        : key === "followup_confidence_threshold" ? Math.max(0, Math.min(100, Number(l.data[key]) || 0))
        : l.data[key];
      if (l.draft[key] !== current) changes[key] = l.draft[key]; });
    if (LAYA_DECISION_FIELDS.some(([key]) => l.draft.decision_fields[key] !== (l.data.decision_fields?.[key] !== false)))
      changes.decision_fields = l.draft.decision_fields;
    if (l.draft.api_key) changes.api_key = l.draft.api_key;
    return changes; };
  const layaDirty = l => !!l.data && !!l.draft && Object.keys(layaChanges(l)).length > 0;
  async function layaRefresh(s) { const l = s.laya, ticket = version; if (l.loading) return; l.loading = true; l.notice = "正在读取 Laya 配置…"; render();
    try { const [data, history] = await Promise.all([api("GET", path + "/laya"), api("GET", path + "/laya/logs")]); if (!current(ticket, s)) return;
      if (!layaDirty(l) || l.stale) l.draft = layaDraftFrom(data); l.data = {...data, decision_fields: layaDraftFrom(data).decision_fields}; l.logs = history.events || []; l.loaded = true; l.stale = false; l.bad = false; l.notice = "已读取 Laya 接入状态与近期记录。"; }
    catch (err) { if (!current(ticket, s)) return; l.bad = true; l.notice = fail(err, "无法读取 Laya 配置。"); }
    finally { l.loading = false; if (current(ticket, s)) render(); } }
  async function layaSave(s) { const l = s.laya; if (l.busy || l.loading || !l.data || l.stale || !layaDirty(l)) return;
    const ticket = version, changes = layaChanges(l); l.busy = true; l.notice = "正在保存 Laya 配置…"; render();
    try { const data = await api("POST", path + "/laya/save", {revision: l.data.revision, changes}); if (!current(ticket, s)) return;
      l.data = data; l.draft = layaDraftFrom(data); l.stale = false; l.bad = false; l.notice = "Laya 配置已保存。"; }
    catch (err) { if (!current(ticket, s)) return; l.bad = true; l.notice = fail(err, "Laya 配置保存未完成。"); }
    finally { l.busy = false; if (current(ticket, s)) render(); } }
  async function layaTest(s) { const l = s.laya; if (l.busy || l.loading || !l.data) return;
    if (layaDirty(l)) { l.bad = true; l.notice = "请先保存当前 Laya 配置，再执行连接或模型下载测试。"; render(); return; }
    if ((l.draft?.mode || l.data.mode) === "local" && l.data.local_runtime_state === "gpu_unavailable") {
      l.bad = true;
      l.notice = "本地 Laya 无法测试：当前 AstrBot Python 的 PyTorch 不支持 CUDA。请安装 CUDA 版 PyTorch 并重启，或切换到远程 HTTP 服务。";
      render(); return;
    }
    const ticket = version; l.busy = true; l.test = {status: "testing"}; l.notice = l.data.mode === "local" ? "正在调用官方 Router；首次测试会自动下载检查点…" : l.data.mode === "jev" ? "正在调用 Jev 判断服务…" : l.data.mode === "workers_ai" ? "正在调用 Cloudflare Workers AI…" : "正在调用官方 SystemOne HTTP 接口…"; render();
    try { const result = await api("POST", path + "/laya/test", {}, 600000); if (!current(ticket, s)) return;
      l.test = result; l.notice = "Laya 调用完成；当前模式和检查点已验证。"; l.bad = false; await layaRefreshQuiet(s, ticket); }
    catch (err) { if (!current(ticket, s)) return; l.test = {status: "unknown", code: err.code || err.message, reason: err.reason || ""}; l.bad = true; l.notice = fail(err, "Laya 连接测试未完成。"); }
    finally { l.busy = false; if (current(ticket, s)) render(); } }
  async function layaRefreshQuiet(s, ticket) { try { const [data, history] = await Promise.all([api("GET", path + "/laya"), api("GET", path + "/laya/logs")]); if (current(ticket, s)) { s.laya.data = data; s.laya.draft = layaDraftFrom(data); s.laya.logs = history.events || []; } } catch {} }
  /* ---------- DeepSeek search binding ---------- */
  const searchDirty = x => !!x.data && !!x.draft
    && x.draft.deepseek_provider_id !== x.data.deepseek_provider_id;
  async function searchRefresh(s) { const x = s.search, ticket = version; if (x.loading || x.busy) return;
    x.loading = true; x.notice = "正在读取搜索服务配置…"; render();
    try { const data = await api("GET", path + "/search"); if (!current(ticket, s)) return;
      if (!searchDirty(x) || x.stale) x.draft = {deepseek_provider_id: data.deepseek_provider_id || ""};
      x.data = data; x.loaded = true; x.stale = false; x.bad = false;
      x.notice = data.provider_available ? "已读取 DeepSeek 搜索 Provider。" : "当前选择的 Provider 未加载或已失效；搜索不会自动切换。";
    } catch (err) { if (!current(ticket, s)) return; x.bad = true; x.notice = fail(err, "无法读取搜索服务配置。"); }
    finally { x.loading = false; if (current(ticket, s)) render(); } }
  async function searchSave(s) { const x = s.search; if (x.busy || x.loading || !x.data || x.stale || !searchDirty(x)) return;
    const ticket = version, changes = {deepseek_provider_id: x.draft.deepseek_provider_id};
    x.busy = true; x.notice = "正在保存搜索服务配置…"; render();
    try { const data = await api("POST", path + "/search/save", {revision: x.data.revision, changes}); if (!current(ticket, s)) return;
      x.data = data; x.draft = {deepseek_provider_id: data.deepseek_provider_id || ""}; x.stale = false; x.bad = false;
      x.notice = data.provider_available ? "搜索 Provider 已保存，后续搜索立即生效。" : "配置已保存，但 Provider 当前不可用；搜索不会自动切换。";
    } catch (err) { if (!current(ticket, s)) return; x.bad = true; x.notice = fail(err, "搜索服务配置保存未完成。"); }
    finally { x.busy = false; if (current(ticket, s)) render(); } }
  function searchPane(s) { const x = s.search, data = x.data, draft = x.draft || {deepseek_provider_id: ""};
    const providers = data?.providers || [], selected = draft.deepseek_provider_id || "";
    const unavailable = selected && !providers.some(item => item.id === selected);
    return `<div class="model-subpane model-laya-pane">
      <div class="model-card"><div class="model-phead"><div class="model-grow"><span class="model-eb">网页搜索与内容抓取</span><h3>DeepSeek 搜索服务</h3><p class="model-note">搜索使用 DeepSeek 官方 Search / Fetch API，不对应聊天模型任务。API 密钥由 AstrBot Provider 管理；V2 只保存 Provider 标识。选择自动时沿用环境变量、当前 DeepSeek Provider 和 AstrBot 网页搜索回退。</p></div><button type="button" class="button small" data-search-refresh ${x.loading || x.busy ? "disabled" : ""}>刷新</button></div>
        <label class="model-fd"><span class="model-cap">DeepSeek API 凭据来源<em>只列出已加载且配置了 api.deepseek.com 的 Provider</em></span><select data-search-provider ${x.loading || x.busy || !data ? "disabled" : ""}>
          <option value="" ${selected ? "" : "selected"}>自动选择</option>${providers.map(item => `<option value="${esc(item.id)}" ${selected === item.id ? "selected" : ""}>${esc(item.name)} · ${esc(item.id)}</option>`).join("")}
          ${unavailable ? `<option value="${esc(selected)}" selected>${esc(selected)} · 当前不可用</option>` : ""}</select></label>
        ${data && !providers.length ? '<p class="model-note">未发现可用的 DeepSeek Provider。可在 AstrBot 模型提供方中配置官方 API，再刷新此页。</p>' : ""}
        ${unavailable ? '<div class="model-alert">已选 Provider 当前不可用。请重新加载该 Provider，或改选其他来源。</div>' : ""}
        <div class="model-editor-actions"><button type="button" class="button primary" data-search-save ${!data || !searchDirty(x) || x.busy || x.loading || x.stale ? "disabled" : ""}>保存搜索配置</button></div>
        ${x.stale ? '<div class="model-alert">配置已在别处变化，请刷新后核对。</div>' : ""}${x.notice ? `<div class="model-notice${x.bad || unavailable ? " error" : ""}">${esc(x.notice)}</div>` : ""}
      </div>
    </div>`; }
  function searchInput(s, event) { const x = s.search, target = event.target;
    if (!target.matches("[data-search-provider]") || !x.draft || x.loading || x.busy) return;
    x.draft.deepseek_provider_id = target.value; x.bad = false; x.notice = "";
    if (event.type === "change") render(); }
  function searchClick(s, event) { const target = event.target;
    if (target.closest("[data-search-refresh]")) { searchRefresh(s); return; }
    if (target.closest("[data-search-save]")) searchSave(s); }
  async function layaDecide(s) { const l = s.laya; if (l.busy || l.loading || !l.data) return;
    if (layaDirty(l)) { l.bad = true; l.notice = "请先保存当前 Laya 配置，再执行决策预览。"; render(); return; }
    if ((l.draft?.mode || l.data.mode) === "local" && l.data.local_runtime_state === "gpu_unavailable") {
      l.bad = true;
      l.notice = "本地 Laya 无法调用：当前 AstrBot Python 的 PyTorch 不支持 CUDA。请安装 CUDA 版 PyTorch 并重启，或切换到远程 HTTP 服务。";
      render(); return;
    }
    const text = String(l.previewText || "").trim(); if (!text) { l.bad = true; l.notice = "先输入一段测试消息。"; render(); return; }
    const ticket = version; l.busy = true; l.preview = {status: "testing"}; l.notice = "正在请求 Laya 进行语义分类…"; render();
    try { const result = await api("POST", path + "/laya/decide", {facts: {message_kind: "text", user_text: text}}, 125000); if (!current(ticket, s)) return;
      l.preview = result; l.notice = result.status === "skipped" ? "这条测试消息被规则跳过。" : "已收到 Laya 决策。"; l.bad = false; await layaRefreshQuiet(s, ticket); }
    catch (err) { if (!current(ticket, s)) return; l.preview = {status: "unknown", code: err.code || err.message}; l.bad = true; l.notice = fail(err, "Laya 决策调用未完成。"); }
    finally { l.busy = false; if (current(ticket, s)) render(); } }
  function layaField(l, key, label, hint, type = "text") {
    const value = l.draft?.[key] ?? "", disabled = l.loading || l.busy;
    if (type === "bool") return `<label class="model-fd"><span class="model-cap">${label}<em>${hint}</em></span><label class="model-switch"><input type="checkbox" data-laya-field="${key}" ${value ? "checked" : ""} ${disabled ? "disabled" : ""}><i></i></label></label>`;
    return `<label class="model-fd"><span class="model-cap">${label}<em>${hint}</em></span><input type="${type}" data-laya-field="${key}" value="${esc(value)}" ${disabled ? "disabled" : ""}></label>`;
  }
  function layaPercentField(l, key, label, hint) {
    const value = l.draft?.[key] ?? 0, disabled = l.loading || l.busy;
    return `<label class="model-fd"><span class="model-cap">${label}<em>${hint}</em></span><span class="model-unit"><input type="number" min="0" max="100" step="1" data-laya-field="${key}" value="${esc(value)}" ${disabled ? "disabled" : ""}><i>%</i></span></label>`;
  }
  function layaSelect(l, key, label, hint, options) { const value = l.draft?.[key] || "", disabled = l.loading || l.busy;
    return `<label class="model-fd"><span class="model-cap">${label}<em>${hint}</em></span><select data-laya-field="${key}" ${disabled ? "disabled" : ""}>${options.map(([id, name]) => `<option value="${id}" ${value === id ? "selected" : ""}>${name}</option>`).join("")}</select></label>`; }
  async function layaCopyInstall(s, field) { const command = s.laya.data?.[field || "install_command"] || ""; if (!command) return;
    try { await navigator.clipboard.writeText(command); s.laya.notice = field === "torch_install_command" ? "已复制 CUDA 版 PyTorch 安装命令；请按驱动确认 cu128 是否兼容，安装后重启 AstrBot。" : field === "remote_start_command" ? "已复制 Laya 服务启动命令。" : field === "remote_install_command" ? "已复制远程 Laya 服务依赖安装命令。" : "已复制当前 AstrBot Python 环境的安装命令。安装后重启 AstrBot，再刷新状态。"; s.laya.bad = false; }
    catch { s.laya.notice = "无法访问剪贴板，请选中并复制下方命令。"; s.laya.bad = false; } render(); }
  function layaResult(result) { if (!result) return ""; if (result.status === "testing") return '<div class="model-notice">正在等待 Laya…</div>'; if (result.status === "skipped") return `<div class="model-notice">本次未调用 Laya：${esc(result.reason || "程序规则已处理")}</div>`;
    if (result.status !== "ok") return `<div class="model-notice error">${esc(fail(result, "Laya 结果未知。"))}</div>`;
    const decision = result.decision; if (!decision) return "";
    const meta = [];
    if (typeof result.action_confidence === "number" && Number.isFinite(result.action_confidence))
      meta.push(`行动置信度 ${(result.action_confidence * 100).toFixed(1)}%`);
    if (result.confidence_gate?.status === "below_threshold")
      meta.push(`低于连续接话阈值 ${result.confidence_gate.threshold_percent}%，已改为旁听`);
    else if (result.confidence_gate?.status === "confidence_unavailable")
      meta.push("当前服务未返回行动概率，阈值未强制执行");
    return `<div class="model-laya-result"><b>决策结果${meta.length ? ` · ${esc(meta.join(" · "))}` : ""}</b><code>${esc(JSON.stringify(decision, null, 2))}</code></div>`; }
  function layaHistoryMarkup(l) { const events = Array.isArray(l.logs) ? l.logs : [];
    if (!events.length) return '<p class="model-laya-history-empty">暂无 Laya 调用记录。</p>';
    const labels = {ok: "成功", skipped: "跳过", failed: "失败"}, sources = {conversation: "实际对话", dashboard_preview: "页面预览", probe: "连接测试"};
    const diagnostic = (label, value, empty) => `<div><small>${label}</small><code>${value == null ? empty : esc(JSON.stringify(value, null, 2))}</code></div>`;
    return `<ol class="model-laya-history">${events.map(event => {
      const detail = event.decision ? JSON.stringify(event.decision, null, 2) : event.reason || event.reason_code || "无附加信息";
      const enabled = event.enabled_fields && typeof event.enabled_fields === "object"
        ? LAYA_DECISION_FIELDS.filter(([key]) => event.enabled_fields[key] === true).map(([, label]) => label) : null;
      const time = event.observed_at ? new Date(event.observed_at).toLocaleString() : "时间未知";
      const enabledMarkup = enabled ? `<small>当时启用：${enabled.length ? enabled.map(esc).join(" · ") : "无决策分类"}</small>` : "";
      const hasDiagnostics = event.request || event.response || event.processing;
      const diagnostics = hasDiagnostics
        ? `<div class="model-laya-diagnostics">${diagnostic("模型输入 · state 与分类问题", event.request, "未记录请求")}${diagnostic("模型原始响应", event.response, "未收到模型响应")}${diagnostic("处理结果 · 合同解析、范围修正与实际应用", event.processing, "未记录处理过程")}</div>`
        : '<small>该记录生成于详细诊断字段启用前。</small>';
      return `<li data-state="${esc(event.status)}"><details><summary><span><strong>${esc(labels[event.status] || event.status)}</strong><small>${esc(time)} · ${esc(sources[event.source] || event.source || "未知来源")} · ${esc(event.mode)}</small></span><b>${event.elapsed_ms == null ? "—" : esc(event.elapsed_ms) + " ms"}</b></summary><div><small>${esc(event.reason_code || "分类结果")}</small>${enabledMarkup}<code>${esc(detail)}</code>${diagnostics}</div></details></li>`;
    }).join("")}</ol>`; }
  function layaPane(s) { const l = s.laya; if (!l.data) return `<div class="model-card"><p class="model-empty">${esc(l.notice || "正在读取 Laya 配置…")}</p></div>`;
    const dirty = layaDirty(l), installed = l.data.package_installed, mode = l.draft?.mode || l.data.mode;
    const gpuUnavailable = mode === "local" && l.data.local_runtime_state === "gpu_unavailable";
    const runtimeLabel = gpuUnavailable ? "CUDA 不可用" : mode === "local" ? "本地 Python" : mode === "jev" ? "Jev 判断服务" : mode === "workers_ai" ? "Cloudflare Workers AI" : "远程 HTTP";
    const packageKpi = mode === "local" ? installed ? `已安装 ${l.data.package_version || ""}` : "待安装" : "外部服务";
    const modelKpi = mode === "jev" ? l.data.default_model || "jev-latest"
      : mode === "workers_ai" ? l.data.default_model || "未填写"
      : mode === "remote" ? "远程管理" : l.data.model_cache_ready ? "已缓存" : "首次调用下载";
    const torchText = [l.data.torch_version, l.data.cuda_version ? `CUDA ${l.data.cuda_version}` : "无 CUDA"]
      .filter(Boolean).join(" · ");
    return `<div class="model-subpane model-laya-pane">
      <div class="model-card"><div class="model-phead"><div class="model-grow"><span class="model-eb">可选能力 · 语义决策</span><h3>Laya 决策模型</h3><p class="model-note">在回复前判断是否接话、等待、沉默及表达形式。配置全局生效；仅群聊候选进入 Laya 判断，私聊始终走 AstrBot 核心回复，另由回复页的私聊收尾判断在纯道别时安静。模型不输出时间、段数等数值；行动概率只用于下方可选的连续接话阈值，执行仍遵循宿主配置和权限。接入方式支持本地 Laya 包、官方 SystemOne 服务、Jev 判断服务与 Cloudflare Workers AI，四者共用同一份决策合同。</p></div><button type="button" class="button small" data-laya-refresh ${l.loading || l.busy ? "disabled" : ""}>刷新</button></div>
        <div class="model-kpis">${kpi(!l.data.enabled ? "未启用" : mode === "local" && l.data.local_runtime_state === "warming" ? "正在预热" : "已启用", "运行状态", l.data.enabled ? "g" : "a")}${kpi(packageKpi, mode === "local" ? "Python 包" : "依赖来源")}${kpi(modelKpi, mode === "jev" || mode === "workers_ai" ? "判断模型" : "模型权重")}${kpi(runtimeLabel, "接入方式", gpuUnavailable ? "r" : "")}</div>
        ${gpuUnavailable ? `<div class="model-alert"><b>本地 Laya 当前不可用：</b>当前 AstrBot Python 使用的是 CPU 版 PyTorch${torchText ? `（${esc(torchText)}）` : ""}。Laya 本地模式需要 CUDA；请安装与驱动匹配的 CUDA 版 PyTorch 后重启 AstrBot，或在上方把接入方式切到「远程 HTTP 服务」或「Jev 判断服务」。
          <div class="model-editor-actions"><button type="button" class="button small" data-laya-copy="torch_install_command">复制 CUDA PyTorch 安装命令</button></div>
          ${l.data.torch_install_command ? `<code class="model-laya-cmd">${esc(l.data.torch_install_command)}</code><small>命令默认使用 cu128；请按显卡驱动把 cu128 换成 cu126 等兼容版本。安装会替换当前 CPU 版 torch，但不会改动已安装的 laya。</small>` : ""}</div>` : ""}
        <div class="model-laya-fields">${layaField(l, "enabled", "启用 Laya", "关闭时调用会跳过", "bool")}${layaSelect(l, "mode", "接入方式", "选择 AstrBot 本地 Python、官方 SystemOne 服务、Jev 判断服务或 Cloudflare Workers AI", [["local", "本地 Python 包"], ["remote", "远程 HTTP 服务"], ["jev", "Jev 判断服务"], ["workers_ai", "Cloudflare Workers AI"]])}${mode === "remote" ? layaField(l, "endpoint", "SystemOne 服务地址", "可填服务根地址、/v1 或完整 /v1/systemone") : ""}${mode === "jev" ? layaField(l, "endpoint", "判断服务地址", "可填服务根地址、/v1 或完整 /v1/chat/completions；走 OpenAI 兼容接口") : ""}${mode === "workers_ai" ? layaField(l, "endpoint", "Workers AI 地址", "填 accounts/<ACCOUNT_ID>/ai/run，或只填 accounts/<ACCOUNT_ID>；模型写在请求体里") : ""}${mode !== "local" ? layaField(l, "api_key", "API Key", l.data.api_key_set ? "已配置；留空保留现有密钥" : "按服务端要求填写；留空表示不鉴权", "password") : ""}${mode !== "local" ? layaField(l, "remote_model", "判断模型名", mode === "jev" ? "留空使用 jev-latest；网关上的模型名可在此覆盖" : mode === "workers_ai" ? "留空使用 @cf/cloudflare/clef（吃每日免费神经元额度）；更快可用 @cf/cloudflare/clef-flash，第三方模型如 typesafe/jev-1.14.0 需单独付费" : "留空使用官方 multilingual；仅自建服务改名时才需要填写") : ""}${layaField(l, "timeout_seconds", "调用超时（秒）", "用于本地推理或远程连接，不参与语义判断", "number")}${layaPercentField(l, "followup_confidence_threshold", "连续接话置信阈值", "0 关闭；仅约束非点名续接。Laya 行动概率低于此值时改为旁听，建议从 40–55% 起按日志校准")}</div>
        ${mode === "local" ? `<div class="model-laya-fields">${layaField(l, "local_model_path", "本地检查点目录", "可选择微调模型；留空使用官方多语言模型。所有会话共用此选择。")}</div>` : ""}
        <section class="model-laya-decisions" aria-label="决策分类配置"><div><strong>决策分类</strong><span>仅启用的分类会发送给 Laya；关闭的分类沿用宿主配置。</span></div><div class="model-laya-decision-grid">${LAYA_DECISION_FIELDS.map(([key, label, hint]) => `<label class="model-laya-option"><span><strong>${label}</strong><small>${hint}</small></span><span class="model-switch"><input type="checkbox" data-laya-decision="${key}" ${l.draft.decision_fields[key] ? "checked" : ""} ${l.loading || l.busy ? "disabled" : ""}><i></i></span></label>`).join("")}</div></section>
        <div class="model-editor-actions"><button type="button" class="button primary" data-laya-save ${!dirty || l.busy || l.loading || l.stale ? "disabled" : ""}>保存 Laya 配置</button><button type="button" class="button" data-laya-test ${l.busy || l.loading || gpuUnavailable ? "disabled" : ""}>${mode === "local" && installed && !l.data.model_cache_ready ? "下载并验证模型" : "测试 Laya 调用"}</button></div>
        ${l.stale ? '<div class="model-alert">配置已在别处变化，请刷新后核对。</div>' : ""}${l.notice ? `<div class="model-notice${l.bad ? " error" : ""}">${esc(l.notice)}</div>` : ""}${l.test ? layaResult(l.test) : ""}
      </div>
      ${mode === "workers_ai" ? `<div class="model-card"><div class="model-phead"><div class="model-grow"><span class="model-eb">外部判断服务</span><h3>Cloudflare Workers AI</h3></div></div><p class="model-note">Cloudflare Workers AI 把模型判断放在 <code>/ai/run</code>：模型名写在请求体里，<code>state</code> 与 <code>questions</code> 需要嵌进 <code>input</code> 对象，而不是与 <code>model</code> 平级。插件会按这个外壳发送，并把返回的 <code>result</code> 解包成同一份 <code>answers</code> 决策合同；判断失败只影响本轮判断，不会阻塞正常回复。</p>
        <div class="model-state">判断模型：<code>${esc(l.data.default_model || "@cf/cloudflare/clef")}</code> · 请求路径：<code>/ai/run</code> · 请求体：<code>{"model": ..., "input": {"state": ..., "questions": ...}}</code></div>
        <div class="model-state"><code>@cf/cloudflare/clef</code> 与 <code>clef-flash</code> 使用 Workers AI 每日免费神经元额度；<code>typesafe/jev-1.14.0</code> 等第三方模型不共享该额度，额度不足时 Cloudflare 返回 402，原因显示在下方运行记录里。</div>
      </div>` : ""}${mode === "jev" ? `<div class="model-card"><div class="model-phead"><div class="model-grow"><span class="model-eb">外部判断服务</span><h3>Jev 判断服务</h3></div></div><p class="model-note">Jev 是结构化判断模型：不生成文本，也不识别像素，只按 state 与分类问题返回每个分类的概率。插件把这份判断请求包进一次 OpenAI 兼容的 <code>chat/completions</code> 调用，再把 answers 按同一份决策合同解析；判断失败只影响本轮判断，不会阻塞正常回复。这种接入方式不需要本地 Python 包或权重缓存。</p>
        <div class="model-state">判断模型：<code>${esc(l.data.default_model || "jev-latest")}</code> · 请求路径：<code>/v1/chat/completions</code> · 决策合同：<code>state + questions → answers</code></div>
        <div class="model-state">密钥只保存在插件数据目录的决策配置里，不写入调用日志；失败原因显示在下方运行记录。</div>
      </div>` : mode === "workers_ai" ? "" : `<div class="model-card"><div class="model-phead"><div class="model-grow"><span class="model-eb">官方依赖与权重缓存</span><h3>安装 Laya Python 包</h3></div></div><p class="model-note">插件发行包不包含 Laya 包或检查点权重。将下方命令复制到 AstrBot 所在环境执行；本地首次推理会由官方 Router 自动从 Hugging Face 下载所选检查点。</p>
        <div class="model-laya-install"><code>${esc(l.data.install_command || "")}</code><button type="button" class="button small" data-laya-copy="install_command" ${l.busy || l.loading ? "disabled" : ""}>复制安装命令</button></div>
        ${mode === "remote" ? `<p class="model-note">远程服务端使用 Laya 官方 SystemOne 接口，地址以 <code>/v1/systemone</code> 结尾；服务端需要单独部署，以下命令使用本机 AstrBot Python 作为示例。</p><div class="model-laya-install"><code>${esc(l.data.remote_install_command || "")}</code><button type="button" class="button small" data-laya-copy="remote_install_command" ${l.busy || l.loading ? "disabled" : ""}>复制服务端安装命令</button></div><div class="model-laya-install"><code>${esc(l.data.remote_start_command || "")}</code><button type="button" class="button small" data-laya-copy="remote_start_command" ${l.busy || l.loading ? "disabled" : ""}>复制服务启动命令</button></div>` : ""}
        <div class="model-state">Python：<code>${esc(l.data.python_executable || "未知")}</code> · 权重缓存：<code>${esc(l.data.model_cache_path || "由 Hugging Face 默认配置决定")}</code></div>
        <div class="model-state">上游源码与版本：<a href="https://github.com/NandhaKishorM/laya" target="_blank" rel="noreferrer">NandhaKishorM/laya</a> · <a href="https://huggingface.co/convaiinnovations/laya-multilingual" target="_blank" rel="noreferrer">Multilingual 权重</a></div>
      </div>`}
      <div class="model-card"><div class="model-phead"><div class="model-grow"><span class="model-eb">调用预览</span><h3>测试一次语义决策</h3></div></div><label class="model-fd"><span class="model-cap">测试消息<em>只提交这段文字与固定的文本类型事实</em></span><textarea data-laya-preview rows="4" ${l.busy || l.loading ? "disabled" : ""}>${esc(l.previewText || "")}</textarea></label><div class="model-editor-actions"><button type="button" class="button primary" data-laya-decide ${l.busy || l.loading || !l.data.enabled ? "disabled" : ""}>调用 Laya</button></div>${l.preview ? layaResult(l.preview) : ""}</div>
      <div class="model-card"><div class="model-phead"><div class="model-grow"><span class="model-eb">运行记录 · 最近 50 条</span><h3>Laya 调用日志</h3></div></div>${layaHistoryMarkup(l)}</div>
    </div>`; }
  function layaInput(s, event) { const l = s.laya, t = event.target;
    if (t.matches("[data-laya-preview]")) { l.previewText = t.value; return; }
    if (t.matches("[data-laya-decision]") && l.draft) { l.draft.decision_fields[t.dataset.layaDecision] = t.checked; if (event.type === "change") { l.bad = false; render(); } return; }
    const key = t.dataset.layaField; if (!key || !l.draft || l.loading || l.busy) return;
    const value = t.type === "checkbox" ? t.checked : t.type === "number" ? t.valueAsNumber : t.value;
    l.draft[key] = key === "followup_confidence_threshold"
      ? Math.max(0, Math.min(100, Number.isFinite(Number(value)) ? Math.round(Number(value)) : 0))
      : value;
    if (event.type === "change") { l.bad = false; render(); } }
  function layaClick(s, event) { const t = event.target;
    if (t.closest("[data-laya-refresh]")) { layaRefresh(s); return; }
    if (t.closest("[data-laya-save]")) { layaSave(s); return; }
    const copy = t.closest("[data-laya-copy]"); if (copy) { layaCopyInstall(s, copy.dataset.layaCopy); return; }
    if (t.closest("[data-laya-test]")) { layaTest(s); return; }
    if (t.closest("[data-laya-decide]")) { layaDecide(s); return; } }
  /* ---------- image model ---------- */
  const IMAGE_LABELS = {"image.generate": "文生图", "image.edit": "参考图改图", "image.mask_edit": "蒙版局部改图"};
  const IMAGE_VIEWS = {generate: {label: "文生图", operation: "generate", actions: ["image.generate"]},
    edit: {label: "改图", operation: "edit", actions: ["image.edit", "image.mask_edit"]}};
  const IMAGE_LOCAL_LABELS = {comfyui: "ComfyUI", sd_webui: "SD WebUI"};
  const IMAGE_DIAGNOSTICS = {native_registry_unavailable: "V2 原生能力注册表尚未就绪",
    native_extension_unavailable: "Image 模块未创建，启动过程未提供具体错误码",
    native_provider_unavailable: "Image 模块没有创建生图 Provider",
    native_configuration_invalid: "Image 模块配置文件无效",
    native_image_declaration_missing: "当前会话没有发现 Image 能力声明",
    native_image_operation_unavailable: "当前生图模型队列未提供此操作",
    native_declaration_stale: "Image 能力声明已失效",
    native_host_registration_missing: "AstrBot 尚未注册该生图动作",
    capability_descriptor_conflict: "Image 能力目录与现有声明冲突",
    capability_identity_conflict: "Image 能力身份与现有记录冲突"};
  const imageDiagnostic = code => IMAGE_DIAGNOSTICS[code] || (code ? `诊断码：${code}` : "请刷新状态并检查 V2 模块启动记录");
  function imageModuleNotice(data) {
    const unresolved = data?.actions?.find(item => item.enabled && !["ready", "unsupported"].includes(item.state));
    if (!unresolved) return "";
    const module = data.module || {}, code = module.issue_code || module.error_code ||
      unresolved.diagnostic_code || (module.state === "unavailable" ? "native_extension_unavailable" : "native_image_declaration_missing");
    const state = module.state === "failed" ? "启动失败" : module.state === "inactive" ? "尚未启动" :
      module.state === "running" ? "运行中" : "不可用";
    return `Image 模块${state}：${imageDiagnostic(code)}。刷新仍未恢复时，请查看 V2 模块启动日志。`;
  }
  const imageDirty = i => i.data && i.draft && Object.keys(IMAGE_LABELS).some(action => i.draft[action] !== i.data.enabled[action]);
  const imageConfigDirty = i => !!i.configBase && !!i.configDraft
    && (JSON.stringify(i.configDraft) !== JSON.stringify(i.configBase)
      || JSON.stringify(i.configText || {}) !== JSON.stringify(i.configTextBase || {}));
  const imageDefaultRoutes = config => {
    const selected = config.backend || "auto", routes = [];
    if (["auto", "online_api"].includes(selected)) routes.push(...(config.endpoints || []).map(item => `online:${item.id}`));
    if (["auto", "comfyui"].includes(selected)) routes.push("comfyui");
    if (["auto", "sd_webui"].includes(selected)) routes.push("sd_webui");
    return {generate: [...routes], edit: [...routes]};
  };
  const IMAGE_PLATFORMS = {auto: "自动识别", openai: "OpenAI 兼容", openrouter: "OpenRouter", agnes: "Agnes", sensenova: "商汤日日新", minimax: "MiniMax", bailian: "阿里云百炼", modelscope: "魔搭社区", doubao: "火山引擎 / 豆包", gemini: "Google Gemini"};
  const IMAGE_PROTOCOL_SUPPORT = {auto: "根据地址识别协议", openai: "文生图 · 改图", openrouter: "文生图 · 改图", agnes: "文生图 · 改图", sensenova: "文生图 · 改图", minimax: "仅文生图", doubao: "文生图 · 改图", bailian: "已保留配置 · 需异步协议适配", modelscope: "已保留配置 · 需协议适配", gemini: "已保留配置 · 需协议适配"};
  const imageModelFamily = model => {
    const parts = String(model || "").trim().toLowerCase().split(/[/:]+/).filter(Boolean);
    return parts.some(part => part.startsWith("gpt-image-") || part.startsWith("chatgpt-image-")) ? "gpt-image"
      : parts.some(part => part.startsWith("dall-e") || part.startsWith("dalle")) ? "dall-e" : "";
  };
  const imageQualityHelp = item => imageModelFamily(item.model) === "gpt-image"
    ? "GPT Image 只接受自动/低/中/高；升级前保存的「标准/高清」会按服务端默认处理，不会发送（保存一次即修正），返回格式也不会发送。"
    : "默认不发送 quality；standard/hd 只对 dall-e 系列有效。";
  const imageResponseFormatHelp = item => imageModelFamily(item.model) === "gpt-image"
    ? "GPT Image 不支持 response_format；升级前保存的 b64_json 会被忽略（保存一次即修正）。"
    : "默认不发送 response_format；base64 与图片 URL 都能读取。";
  const imageLegacyQualityNote = item => item.legacy_protocol_defaults
    || (imageModelFamily(item.model) === "gpt-image"
      && (["standard", "hd"].includes(item.quality) || item.response_format === "b64_json"))
    ? '<small class="model-img-legacy-note">升级前保存的「标准/高清」或 b64_json 在 GPT Image 上不会发送；保存一次即按服务端默认清理。</small>'
    : "";
  function imageEffectivePlatform(endpoint) {
    if (endpoint?.platform !== "auto") return endpoint?.platform;
    const url = String(endpoint?.base_url || "").toLowerCase(), model = String(endpoint?.model || "").toLowerCase();
    if (url.includes("minimax") || ["image-01", "image-01-live"].includes(model)) return "minimax";
    if (url.includes("openrouter.ai")) return "openrouter";
    if (url.includes("agnes-ai.com") || model.startsWith("agnes-image-")) return "agnes";
    if (url.includes("sensenova.cn") || model.startsWith("senova-")) return "sensenova";
    if (url.includes("volces.com") || url.includes("volcengine")) return "doubao";
    if (url.includes("dashscope.aliyuncs.com")) return "bailian";
    if (url.includes("modelscope.cn")) return "modelscope";
    if (url.includes("generativelanguage.googleapis.com")) return "gemini";
    return "openai";
  }
  const imageNormalized = config => {
    const value = JSON.parse(JSON.stringify(config));
    value.endpoints = Array.isArray(value.endpoints) ? value.endpoints : [];
    for (const endpoint of value.endpoints) {
      endpoint.quality ??= "";
      endpoint.response_format ??= "";
      endpoint.max_reference_images ??= 1;
      endpoint.size_label ??= `${endpoint.width || 1024}x${endpoint.height || 1024}`;
    }
    value.comfyui = value.comfyui && typeof value.comfyui === "object" ? value.comfyui : {};
    value.sd_webui = value.sd_webui && typeof value.sd_webui === "object" ? value.sd_webui : {};
    if (!value.operation_routes || typeof value.operation_routes !== "object") value.operation_routes = imageDefaultRoutes(value);
    for (const operation of ["generate", "edit"]) {
      if (!Array.isArray(value.operation_routes[operation])) value.operation_routes[operation] = imageDefaultRoutes(value)[operation];
    }
    return value;
  };
  function imageConfigText(config) {
    const stringify = value => JSON.stringify(value && typeof value === "object" ? value : {}, null, 2);
    const sd = config.sd_webui?.parameters || {}, shared = Object.fromEntries(Object.entries(sd)
      .filter(([key]) => !["generate", "edit", "mask_edit"].includes(key)));
    const opParams = operation => sd[operation] && typeof sd[operation] === "object" ? sd[operation] : shared;
    const workflows = config.comfyui?.workflows || {};
    return {"sd.generate": stringify(opParams("generate")), "sd.edit": stringify(opParams("edit")),
      "comfy.generate": stringify(workflows.generate), "comfy.edit": stringify(workflows.edit),
      "comfy.mask_edit": stringify(workflows.mask_edit), "legacy.options": stringify(config.legacy_options),
      ...Object.fromEntries((config.endpoints || []).map(item => [`headers.${item.id}`, ""]))};
  }
  function imageConfigSet(s, data) {
    const i = s.image, normalized = imageNormalized(data.settings);
    if (i.config?.revision !== data.revision) {
      /* A new revision invalidates the results and the keys: the stored request
         fingerprint only carries endpoint_id, not base_url/api_key, so replaying
         an old key would show the previous result instead of testing this one. */
      const previous = !!i.config;
      i.endpointTests = {}; i.endpointTestKeys = {};
      if (previous) imageTestPersist(i);
    } else {
      const known = new Set((normalized.endpoints || []).map(item => item.id));
      for (const id of Object.keys(i.endpointTestKeys || {})) if (!known.has(id)) delete i.endpointTestKeys[id];
    }
    i.endpointTesting = null;
    i.config = data; i.configBase = JSON.parse(JSON.stringify(normalized));
    i.configDraft = JSON.parse(JSON.stringify(normalized));
    i.configTextBase = imageConfigText(normalized); i.configText = {...i.configTextBase};
    imageTestRestore(i);
  }
  async function imageConfigRefresh(s) {
    const i = s.image;
    if (i.configLoading || i.configSaving || i.endpointTesting || imageConfigDirty(i)) return;
    const ticket = version; i.configLoading = true; i.configNotice = "正在读取各功能的模型配置…"; i.configBad = false; render();
    try {
      const data = await api("GET", path + "/image/output");
      if (!current(ticket, s)) return;
      if (!data?.settings || typeof data.revision !== "string") throw failure("invalid_response");
      imageConfigSet(s, data); i.configNotice = "模型配置已读取。";
    } catch (err) { if (!current(ticket, s)) return; i.configBad = true; i.configNotice = fail(err, "无法读取生图模型配置。"); }
    finally { i.configLoading = false; if (current(ticket, s)) render(); }
  }
  async function imageRefresh(s) { const i = s.image, ticket = version; if (i.loading) return; void loadImageSchema(s); i.loading = true; i.notice = "正在读取内置生图能力…"; render();
    try { const data = await api("GET", path + "/image"); if (!current(ticket, s)) return;
      if (!Array.isArray(data.actions) || !data.enabled || Object.keys(IMAGE_LABELS).some(action => typeof data.enabled[action] !== "boolean")) throw failure("invalid_response");
      if (!imageDirty(i)) i.draft = {...data.enabled}; i.data = data; i.loaded = true; i.bad = false; i.notice = "已读取当前内置动作状态。"; }
    catch (err) { if (!current(ticket, s)) return; i.bad = true; i.notice = fail(err, "无法读取生图能力。"); }
    finally { i.loading = false; if (current(ticket, s)) { render(); void imageConfigRefresh(s); } } }
  async function imageSave(s) { const i = s.image; if (i.busy || i.loading || !imageDirty(i)) return;
    const ticket = version; i.busy = true; i.bad = false; i.notice = "正在保存内置生图开关…"; render();
    try { const data = await api("POST", path + "/image/save", {enabled: {...i.draft}}); if (!current(ticket, s)) return;
      if (!Array.isArray(data.actions) || !data.enabled) throw failure("invalid_response");
      i.data = data; i.draft = {...data.enabled}; i.notice = "生图开关已保存，后续调用会采用当前设置。"; }
    catch (err) { if (!current(ticket, s)) return; i.bad = true; i.notice = fail(err, "生图开关保存结果未确认，请刷新核对。"); }
    finally { i.busy = false; if (current(ticket, s)) render(); } }
  async function imageTest(s, action) { const i = s.image, ticket = version; if (i.testing || !IMAGE_LABELS[action]) return;
    i.testing = action; i.notice = `正在检查${IMAGE_LABELS[action]}注册状态…`; render();
    try { const result = await api("POST", path + "/image/test", {action}); if (!current(ticket, s)) return;
      i.bad = result.state !== "ready"; i.notice = result.state === "ready" ? `${IMAGE_LABELS[action]}已注册，可由后续真实任务调用。`
        : result.state === "disabled" ? `${IMAGE_LABELS[action]}当前已关闭。`
        : `${IMAGE_LABELS[action]}未就绪：${imageDiagnostic(result.diagnostic_code)}。`; }
    catch (err) { if (!current(ticket, s)) return; i.bad = true; i.notice = fail(err, "注册检查未完成。"); }
    finally { i.testing = null; if (current(ticket, s)) render(); } }
  /* Endpoint tests are idempotent by operation key: the service stores one
     operation per (scope, request_id=operation_key), replays it when the
     fingerprint matches, and never dispatches a duplicate.  A key therefore
     belongs to one endpoint and one configuration revision, and may only be
     reused while the result is still unsettled.  A settled result or a changed
     revision allocates a new key: the stored request fingerprint carries only
     endpoint_id (not base_url/api_key), so an old key would replay the previous
     result instead of testing the saved configuration. */
  const IMAGE_TEST_PENDING = new Set(["pending", "prepared", "dispatching"]);
  const IMAGE_TEST_TERMINAL = new Set(["succeeded", "failed", "stopped"]);
  const IMAGE_TEST_STORAGE = "companion-model-image-tests.v1";
  const imageTestUnsettled = status => status === "unknown" || IMAGE_TEST_PENDING.has(status);
  const imageTestStorage = () => key ? `${IMAGE_TEST_STORAGE}:${key}` : "";
  const imageTestElapsed = test => test?.recheck && Number(test.elapsed_ms) < 50
    ? "沿用上次结果" : `${Number(test?.elapsed_ms) || 0} ms`;
  function imageEndpointTestStatus(value) {
    const status = typeof value === "string" ? value : "";
    if (IMAGE_TEST_TERMINAL.has(status) || status === "unknown") return status;
    return IMAGE_TEST_PENDING.has(status) ? "pending" : "";
  }
  function imageEndpointTestKey(i, endpointId) {
    const revision = typeof i.config?.revision === "string" ? i.config.revision : "";
    const previous = i.endpointTests?.[endpointId];
    const settled = !previous || IMAGE_TEST_TERMINAL.has(previous.status);
    const slots = (i.endpointTestKeys ||= {});
    if (!slots[endpointId] || slots[endpointId].revision !== revision || settled)
      slots[endpointId] = {revision, key: crypto.randomUUID()};
    return slots[endpointId].key;
  }
  /* Only an unsettled test survives a reload.  Persisting a settled result
     would make the next "generate" click replay it instead of generating. */
  function imageTestPersist(i, storage = imageTestStorage()) {
    if (!storage) return;
    const revision = typeof i.config?.revision === "string" ? i.config.revision : "";
    const records = {};
    for (const [id, test] of Object.entries(i.endpointTests || {})) {
      const slot = i.endpointTestKeys?.[id];
      if (!slot || typeof slot.key !== "string" || !slot.key || slot.revision !== revision
          || !imageTestUnsettled(test?.status)) continue;
      records[id] = {revision: slot.revision, key: slot.key, status: test.status,
        error: test.error || "", elapsed_ms: test.elapsed_ms};
    }
    try {
      if (Object.keys(records).length) sessionStorage.setItem(storage, JSON.stringify(records));
      else sessionStorage.removeItem(storage);
    } catch { }
  }
  function imageTestRestore(i) {
    const storage = imageTestStorage();
    if (!storage) return;
    let records = null;
    try { records = JSON.parse(sessionStorage.getItem(storage) || "null"); } catch { return; }
    if (!records || typeof records !== "object" || Array.isArray(records)) return;
    const revision = typeof i.config?.revision === "string" ? i.config.revision : "";
    const known = new Set((i.configDraft?.endpoints || []).map(item => item.id));
    for (const [id, record] of Object.entries(records)) {
      if (!known.has(id) || !record || record.revision !== revision || typeof record.key !== "string"
          || !record.key || !imageTestUnsettled(record.status) || i.endpointTests?.[id]) continue;
      (i.endpointTestKeys ||= {})[id] = {revision, key: record.key};
      (i.endpointTests ||= {})[id] = {status: record.status, error: record.error || "",
        elapsed_ms: record.elapsed_ms, recheck: true};
    }
    /* Drop entries that no longer match this revision or endpoint set. */
    imageTestPersist(i, storage);
  }
  async function imageEndpointTest(s, endpointId) {
    const i = s.image, endpoint = i.config?.settings?.endpoints?.find(item => item.id === endpointId);
    if (!endpoint || !endpoint.enabled || i.endpointTesting || imageConfigDirty(i)) return;
    const ticket = version, storage = imageTestStorage();
    const recheck = imageTestUnsettled(i.endpointTests?.[endpointId]?.status);
    const operationKey = imageEndpointTestKey(i, endpointId);
    i.endpointTests ||= {}; i.endpointTests[endpointId] = {status: "testing", recheck};
    i.endpointTesting = endpointId; render();
    try {
      const timeout = Math.max(30000, (Number(endpoint.timeout_seconds) || 180) * 1000 + 10000);
      const result = await api("POST", path + "/image/output/test",
        {endpoint_id: endpointId, operation_key: operationKey}, timeout);
      const status = imageEndpointTestStatus(result?.status);
      /* prepared/dispatching are the same unsettled operation as pending;
         stopped means it never left the queue.  Anything unrecognised stays
         unknown: the request may already have reached the provider, and a
         fresh key would submit (and bill) a second generation. */
      i.endpointTests[endpointId] = status
        ? {status, elapsed_ms: result.elapsed_ms, error: result.error || "", recheck}
        : {status: "unknown", error: "返回状态无法识别，请核对原测试", recheck};
    } catch (err) {
      /* A bridge timeout/transport failure is not an error state: the service
         may have accepted the request.  Keeping the same key makes the next
         click a read-only recheck instead of a second paid generation. */
      i.endpointTests[endpointId] = {status: "unknown", recheck,
        error: `${err?.code === "bridge_timeout" ? "请求可能已被受理" : "测试结果未确认"}，请核对原测试`};
    } finally {
      i.endpointTesting = null; imageTestPersist(i, storage);
      if (current(ticket, s)) render();
    }
  }
  function imageConfigSaveValue(i) {
    const value = JSON.parse(JSON.stringify(i.configDraft));
    const parse = name => {
      try { const parsed = JSON.parse(i.configText[name] || "{}");
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw Error();
        return parsed;
      } catch { throw failure("model_image_output_invalid", false, {reason: `${name} 必须是 JSON 对象。`}); }
    };
    const baseText = i.configTextBase || {};
    for (const operation of ["generate", "edit"]) {
      if (i.configText[`sd.${operation}`] !== baseText[`sd.${operation}`]) {
        const parsed = parse(`sd.${operation}`);
        const edited = value.sd_webui.parameters?.[operation] || {};
        const baseline = i.configBase.sd_webui?.parameters?.[operation] || {};
        for (const name of Object.keys(imageSchema?.sd_webui?.fields || {})) {
          if (JSON.stringify(edited[name]) !== JSON.stringify(baseline[name]) && Object.hasOwn(edited, name)) {
            parsed[name] = edited[name];
          }
        }
        value.sd_webui.parameters ||= {}; value.sd_webui.parameters[operation] = parsed;
      }
    }
    for (const key of ["generate", "edit", "mask_edit"]) {
      const name = `comfy.${key}`;
      if (i.configText[name] !== baseText[name]) {
        value.comfyui.workflows ||= {}; value.comfyui.workflows[key] = parse(name);
      }
    }
    if (i.configText["legacy.options"] !== baseText["legacy.options"])
      value.legacy_options = parse("legacy.options");
    for (const endpoint of value.endpoints || []) {
      endpoint.name = String(endpoint.name || "").trim();
      const key = `headers.${endpoint.id}`;
      if (i.configText[key] !== baseText[key] && i.configText[key]?.trim()) {
        const parsed = parse(key);
        endpoint.custom_headers = parsed;
        endpoint.clear_custom_headers = false;
      }
    }
    return value;
  }
  async function imageConfigSave(s) {
    const i = s.image;
    if (!imageConfigDirty(i) || i.configSaving || i.configLoading) return;
    let changes;
    try { changes = imageConfigSaveValue(i); }
    catch (error) { i.configBad = true; i.configNotice = error.reason || "生图参数 JSON 格式不正确。"; render(); return; }
    const ticket = version; i.configSaving = true; i.configBad = false; i.configNotice = "正在保存生图模型配置…"; render();
    try {
      const data = await api("POST", path + "/image/output/save", {revision: i.config.revision, changes}, 45000);
      if (!current(ticket, s)) return;
      imageConfigSet(s, data); i.configNotice = "生图模型与优先级已保存，后续任务将按各自队列尝试。";
    } catch (err) { if (!current(ticket, s)) return; i.configBad = true; i.configNotice = fail(err, "生图模型配置保存结果未确认，请重新读取核对。"); }
    finally { i.configSaving = false; if (current(ticket, s)) render(); }
  }
  const imageCurrentOperation = i => IMAGE_VIEWS[i.view || "generate"].operation;
  const imageEndpointLabel = (item, fallback = "未命名图片模型") =>
    String(item?.name || "").trim() || String(item?.model || "").trim() || fallback;
  function imageRouteAvailable(config, route, operation) {
    if (route.startsWith("online:")) {
      const endpoint = config.endpoints.find(item => item.id === route.slice(7));
      if (!endpoint) return "模型配置缺失";
      const platform = imageEffectivePlatform(endpoint);
      if (["bailian", "modelscope", "gemini"].includes(platform)) return "协议未接入 · 当前不可调用";
      if (operation === "edit" && platform === "minimax") return "仅支持文生图";
      if (operation === "edit" && Number(endpoint.max_reference_images) < 1) return "未启用参考图改图";
      if (!endpoint.base_url || !endpoint.model) return "待补齐连接";
      if (!endpoint.enabled) return "模型已停用";
      if (!config.enabled) return "生图后端已关闭";
      return operation === "generate" ? "可用于文生图" : "可用于改图";
    }
    if (route === "sd_webui") return !config.sd_webui?.base_url ? "待配置地址" :
      config.enabled ? "可用于文生图 · 改图 · 蒙版局部改图" : "生图后端已关闭";
    if (route === "comfyui") {
      const flows = config.comfyui?.workflows || {};
      if (!config.comfyui?.base_url) return "待配置地址";
      const support = operation === "generate" ? (flows.generate ? ["文生图"] : [])
        : [flows.edit ? "改图" : "", flows.mask_edit ? "蒙版局部改图" : ""].filter(Boolean);
      return !support.length ? "此功能未绑定工作流" : config.enabled ? "可用于" + support.join(" · ") : "生图后端已关闭";
    }
    return "";
  }
  function imageConfigField(label, field, value, type = "text", attrs = "", help = "", disabled = false) {
    const disable = disabled ? " disabled" : "";
    return `<label class="model-img-field"><span>${label}</span><input type="${type}" data-img-config-field="${esc(field)}" value="${esc(value ?? "")}" ${attrs}${disable}>${help ? `<small>${help}</small>` : ""}</label>`;
  }
  function imageEndpointConnectionFields(i, item, base, disabled) {
    const option = value => `<option value="${value}"${item.platform === value ? " selected" : ""}>${IMAGE_PLATFORMS[value]}</option>`;
    const options = `<optgroup label="已接入协议">${["auto", "openai", "openrouter", "agnes", "sensenova", "minimax", "doubao"].map(option).join("")}</optgroup><optgroup label="待适配 · 可保留原配置">${["bailian", "modelscope", "gemini"].map(option).join("")}</optgroup>`;
    const headerKey = `headers.${item.id}`;
    const headerCount = item.custom_header_names?.length || 0;
    const headerPlaceholder = item.custom_headers_set ? `已保存 ${headerCount} 个请求头 · 留空保留` : "可选：填写服务商要求的自定义请求头";
    const keyField = `${imageConfigField("API Key", `${base}.api_key`, "", "password",
      `autocomplete="new-password" placeholder="${item.api_key_set ? "已保存 · 留空保留" : "按服务商要求填写"}"`,
      item.api_key_set ? "输入新密钥会替换当前密钥。" : "不要求鉴权时可留空。", disabled)}${item.api_key_set ?
      `<label class="model-img-field model-img-inline"><span>密钥</span><span><input type="checkbox" data-img-config-field="${base}.clear_api_key"${item.clear_api_key ? " checked" : ""}${disabled ? " disabled" : ""}>清除已保存密钥</span></label>` : ""}`;
    const headersField = `<label class="model-img-field model-img-json"><span>自定义请求头（JSON）</span><textarea rows="3" spellcheck="false" data-img-config-json="${esc(headerKey)}" placeholder="${esc(headerPlaceholder)}"${disabled ? " disabled" : ""}>${esc(i.configText?.[headerKey] || "")}</textarea><small>${item.custom_headers_set ? `已保存：${esc((item.custom_header_names || []).join("、"))}。` : "可留空。"}填写新对象会替换现有自定义请求头。</small></label>${item.custom_headers_set ?
      `<label class="model-img-field model-img-inline"><span>请求头</span><span><input type="checkbox" data-img-config-field="${base}.clear_custom_headers"${item.clear_custom_headers ? " checked" : ""}${disabled ? " disabled" : ""}>清除已保存请求头</span></label>` : ""}`;
    return `<label class="model-img-field"><span>协议</span><select data-img-config-field="${base}.platform"${disabled ? " disabled" : ""}>${options}</select><small>${esc(IMAGE_PROTOCOL_SUPPORT[item.platform] || "请按服务商文档选择")}</small></label>${imageConfigField("API 根地址", `${base}.base_url`, item.base_url, "url", 'placeholder="https://api.example.com/v1"', "可填根地址或完整 images 地址；查询参数会保留，不会重复拼接路径。", disabled)}${imageConfigField("超时秒数", `${base}.timeout_seconds`, item.timeout_seconds, "number", 'min="1" max="1800" step="1"', "本模型请求按此超时；任务等待预算取所有启用模型中的最长值（1–1800 秒），保存后下一次任务生效，正在执行的任务保持原时限。", disabled)}${keyField}${headersField}`;
  }
  const IMAGE_ROUTE_TONES = ["#4f7f64", "#5b70a6", "#9a6a3d", "#795b96", "#a65353", "#3f7b89"];
  function imageRouteHost(value) {
    try { return new URL(String(value || "")).host || "未配置地址"; }
    catch { return String(value || "").replace(/^https?:\/\//, "").split("/")[0] || "未配置地址"; }
  }
  function imageRouteSupportTone(value) {
    return /可用于|可调用|就绪|可用/.test(String(value || "")) ? "is-ready" : "is-off";
  }
  function imageRouteToggle(ref, expanded, label) {
    return `<summary data-img-route-expand="${esc(ref)}" class="model-img-route-toggle" aria-expanded="${expanded}"><span class="model-img-route-toggle-mark"><i data-lucide="${expanded ? "chevron-up" : "chevron-down"}" aria-hidden="true"></i></span><span class="model-img-route-toggle-copy"><b>${expanded ? "收起配置" : "展开配置"}</b><small>${esc(label)}</small></span><span class="model-img-route-toggle-state">${expanded ? "收起" : "展开"}</span></summary>`;
  }
  function imageRouteCard(i, ref, index, config) {
    const disabled = i.configSaving || i.configLoading || !!i.endpointTesting;
    const expanded = i.expandedImageRoutes?.[ref] ?? index === 0;
    const controls = `<div class="model-img-route-tools"><button type="button" class="output-icon-button" data-img-route-move="up" data-img-route-index="${index}" aria-label="上移优先级"${disabled || index === 0 ? " disabled" : ""}><i data-lucide="arrow-up" aria-hidden="true"></i></button><button type="button" class="output-icon-button" data-img-route-move="down" data-img-route-index="${index}" aria-label="下移优先级"${disabled || index === (config.operation_routes[imageCurrentOperation(i)] || []).length - 1 ? " disabled" : ""}><i data-lucide="arrow-down" aria-hidden="true"></i></button><button type="button" class="output-icon-button" data-img-route-remove="${esc(ref)}" aria-label="从当前功能移除" title="从当前功能移除"${disabled ? " disabled" : ""}><i data-lucide="x" aria-hidden="true"></i></button></div>`;
    if (ref.startsWith("online:")) {
      const endpointId = ref.slice(7), endpointIndex = config.endpoints.findIndex(item => item.id === endpointId), item = config.endpoints[endpointIndex];
      if (!item) return `<article class="model-img-route"><header><span class="model-img-priority">${index + 1}</span><strong>模型配置缺失</strong>${controls}</header></article>`;
      const base = `endpoints.${endpointIndex}`;
      const test = i.endpointTests?.[item.id], testing = i.endpointTesting === item.id;
      const recheckable = !!test && imageTestUnsettled(test.status);
      const recheckMode = recheckable || (testing && !!test?.recheck);
      const testLabel = test?.status === "succeeded" ? `测试成功 · ${imageTestElapsed(test)}`
        : test?.status === "failed" ? `生成失败 · ${test.error || "服务商拒绝请求"}`
        : test?.status === "unknown" ? `结果未知 · ${test.error || "请核对原测试"}`
        : test?.status === "pending" ? "请求已提交，结果待确认 · 请核对原测试"
        : test?.status === "stopped" ? "测试已中断 · 未发出生成请求，可重新测试" : "";
      const testDisabled = disabled || imageConfigDirty(i) || !config.enabled || !item.enabled ||
        ["bailian", "modelscope", "gemini"].includes(imageEffectivePlatform(item));
      const connectionFields = imageEndpointConnectionFields(i, item, base, disabled);
      const modelFields = `${imageConfigField("模型 ID", `${base}.model`, item.model, "text", 'maxlength="240" placeholder="服务商提供的模型 ID"', "")}${imageConfigField("尺寸标签", `${base}.size_label`, item.size_label || `${item.width}x${item.height}`, "text", 'maxlength="64" placeholder="如 1024x1024、1K、auto"', "服务商接受标签时保留原值；宽高用于通用协议")}${imageConfigField("宽度", `${base}.width`, item.width, "number", 'min="64" max="8192" step="64"', "像素")}${imageConfigField("高度", `${base}.height`, item.height, "number", 'min="64" max="8192" step="64"', "像素")}${item.platform === "minimax" ? "" : `<label class="model-img-field"><span>图片质量</span><select data-img-config-field="${base}.quality"${disabled ? " disabled" : ""}>${[["", "服务端默认（不发送）"], ["auto", "自动"], ["low", "低"], ["medium", "中"], ["high", "高"], ["standard", "标准（dall-e 专用）"], ["hd", "高清（dall-e 专用）"]].map(([value, label]) => `<option value="${value}"${item.quality === value ? " selected" : ""}>${label}</option>`).join("")}</select><small>${esc(imageQualityHelp(item))}</small>${imageLegacyQualityNote(item)}</label><label class="model-img-field"><span>返回格式</span><select data-img-config-field="${base}.response_format"${disabled ? " disabled" : ""}>${[["", "按服务端默认（推荐）"], ["b64_json", "b64_json（旧式 dall-e 兼容）"]].map(([value, label]) => `<option value="${value}"${item.response_format === value ? " selected" : ""}>${label}</option>`).join("")}</select><small>${esc(imageResponseFormatHelp(item))}</small></label>${imageConfigField("最多参考图", `${base}.max_reference_images`, item.max_reference_images, "number", 'min="0" max="8" step="1"', "设为 0 时此模型仅用于文生图")}`}<label class="model-img-field model-img-inline"><span>启用模型</span><input type="checkbox" data-img-config-field="${base}.enabled"${item.enabled ? " checked" : ""}${disabled ? " disabled" : ""}></label>`;
      const tone = IMAGE_ROUTE_TONES[index % IMAGE_ROUTE_TONES.length];
      const support = imageRouteAvailable(config, ref, imageCurrentOperation(i));
      return `<article class="model-img-route" data-img-route-card="${esc(ref)}" style="--route-tone:${tone}"><header><span class="model-img-priority">${index + 1}</span><div class="model-img-route-title"><span class="model-img-route-name-wrap" title="点击修改模型显示名称；留空时显示模型 ID"><input type="text" class="model-img-route-name" data-img-config-field="${base}.name" value="${esc(item.name || "")}" maxlength="240" placeholder="${esc(imageEndpointLabel(item, "自定义名称"))}" aria-label="自定义模型名称"${disabled ? " disabled" : ""}><i data-lucide="pencil" aria-hidden="true"></i></span><div class="model-img-route-meta"><span class="model-img-route-id" title="${esc(item.model || "")}">${esc(item.model || "未设置模型 ID")}</span><span class="model-img-route-kind">${esc(IMAGE_PLATFORMS[item.platform] || "自动识别")}</span><span class="model-img-route-host" title="${esc(item.base_url || "")}">${esc(imageRouteHost(item.base_url))}</span></div></div><span class="model-img-route-support ${imageRouteSupportTone(support)}">${esc(support)}</span>${controls}</header>
        <details class="model-img-route-details"${expanded ? " open" : ""}>${imageRouteToggle(ref, expanded, "连接、测试与生图参数")}
        <div class="model-img-endpoint-test"><button type="button" class="button small" data-img-endpoint-test="${esc(item.id)}" title="${recheckMode ? "核对同一次测试，不会再次提交生成请求" : "真实生成一张图片，可能产生费用"}"${testDisabled ? " disabled" : ""}>${testing ? (test?.recheck ? "核对中…" : "生成中…") : recheckable ? "核对原测试" : "生成测试图"}</button><span role="status" data-state="${esc(test?.status || "")}">${esc(testLabel)}</span></div>
        <fieldset class="model-schema-group"><legend>连接</legend><div class="model-img-route-grid">${connectionFields}</div></fieldset>
        <fieldset class="model-schema-group"><legend>生图参数</legend><div class="model-img-route-grid">${modelFields}</div></fieldset><div class="model-img-route-delete"><button type="button" class="button small danger" data-img-endpoint-remove="${esc(item.id)}"${disabled ? " disabled" : ""}>删除此模型</button></div></details></article>`;
    }
    const name = IMAGE_LOCAL_LABELS[ref], item = config[ref] || {}, workflows = item.workflows || {};
    const op = imageCurrentOperation(i), disable = disabled;
    const flowFields = ref === "comfyui" ? (op === "generate" ? ["generate"] : ["edit", "mask_edit"]).map(operation => {
      const title = operation === "mask_edit" ? "蒙版局部改图工作流" : operation === "edit" ? "普通改图工作流" : "文生图工作流";
      const key = `comfy.${operation}`;
      const flow = workflows[operation], nodes = flow?.workflow && typeof flow.workflow === "object" ? Object.keys(flow.workflow).length : 0;
      const summary = nodes ? `已绑定 · ${nodes} 个节点` : "未绑定";
      return `<details class="model-img-workflow"><summary><span>${title}</span><small>${summary}</small></summary><label class="model-img-field model-img-json"><span>工作流图与输入映射（JSON）</span><textarea rows="8" spellcheck="false" data-img-config-json="${key}"${disable ? " disabled" : ""}>${esc(i.configText?.[key] || JSON.stringify(flow || {}, null, 2))}</textarea></label></details>`;
    }).join("") : `<details class="model-img-field model-img-json model-img-advanced"><summary>高级 JSON 参数</summary><textarea rows="6" spellcheck="false" data-img-config-json="sd.${op}"${disable ? " disabled" : ""}>${esc(i.configText?.[`sd.${op}`] || "{}")}</textarea></details>`;
    const connectionFields = `${imageConfigField("API 地址", `${ref}.base_url`, item.base_url, "url", 'placeholder="http://127.0.0.1:8188"', "")}${imageConfigField("超时秒数", `${ref}.timeout_seconds`, item.timeout_seconds || 300, "number", 'min="1" max="1800" step="1"', "")}${imageConfigField("API Key", `${ref}.api_key`, "", "password", `autocomplete="new-password" placeholder="${item.api_key_set ? "已保存 · 留空保留" : "可选"}"`, item.api_key_set ? "输入新密钥会替换当前密钥。" : "", disabled)}${item.api_key_set ? `<label class="model-img-field model-img-inline"><span>密钥</span><span><input type="checkbox" data-img-config-field="${ref}.clear_api_key"${item.clear_api_key ? " checked" : ""}${disabled ? " disabled" : ""}>清除已保存密钥</span></label>` : ""}`;
    const workflowSection = ref === "comfyui"
      ? `<fieldset class="model-schema-group"><legend>工作流</legend><div class="model-img-route-grid">${flowFields}</div></fieldset>`
      : flowFields;
    const tone = IMAGE_ROUTE_TONES[index % IMAGE_ROUTE_TONES.length];
    const support = imageRouteAvailable(config, ref, op);
    return `<article class="model-img-route" data-img-route-card="${esc(ref)}" style="--route-tone:${tone}"><header><span class="model-img-priority">${index + 1}</span><div class="model-img-route-title"><strong>${name}</strong><div class="model-img-route-meta"><span class="model-img-route-kind">${ref === "comfyui" ? "ComfyUI" : "SD WebUI"}</span><span class="model-img-route-host" title="${esc(item.base_url || "")}">${esc(imageRouteHost(item.base_url))}</span></div></div><span class="model-img-route-support ${imageRouteSupportTone(support)}">${esc(support)}</span>${controls}</header><details class="model-img-route-details"${expanded ? " open" : ""}>${imageRouteToggle(ref, expanded, "连接与工作流")}<fieldset class="model-schema-group"><legend>连接</legend><div class="model-img-route-grid">${connectionFields}</div></fieldset>${workflowSection}</details></article>`;
  }
  function imageConfigEditor(s) {
    const i = s.image, config = i.configDraft;
    if (i.configLoading) return '<div class="model-img-loading" role="status">正在读取文生图与改图配置…</div>';
    if (!config) return `<div class="model-img-loading" role="status">${esc(i.configNotice || "尚未读取生图模型配置。")}<button type="button" class="button small" data-img-config-refresh>读取模型配置</button></div>`;
    const operation = imageCurrentOperation(i), routes = config.operation_routes[operation] || [], current = new Set(routes);
    const choices = [...config.endpoints.filter(item => !current.has(`online:${item.id}`)).map(item => [`online:${item.id}`, `${imageEndpointLabel(item, "图片模型")} · ${item.model || item.id}`]),
      ...Object.entries(IMAGE_LOCAL_LABELS).filter(([key]) => !current.has(key)).map(([key, label]) => [key, label])];
    const dirty = imageConfigDirty(i), locked = i.configSaving || i.configLoading || !!i.endpointTesting;
    const legacyOptions = config.legacy_options || {}, selectedWorkflows = legacyOptions.image_companion_selected_workflows || {};
    const extraWorkflows = legacyOptions.image_companion_extra_workflows || [];
    const workflowLabels = {generate: "文生图", edit: "改图", mask_edit: "蒙版局部改图"};
    const legacyWorkflows = Object.entries(selectedWorkflows).map(([key, name]) =>
      `<span><b>${workflowLabels[key] || esc(key)}</b>${esc(name)}</span>`).join("");
    const legacySummary = legacyWorkflows || extraWorkflows.length
      ? `<div class="model-img-legacy-list">${legacyWorkflows}${extraWorkflows.length ? `<span><b>其他工作流</b>${esc(extraWorkflows.join("、"))}</span>` : ""}</div>` : "";
    const legacyLabels = {sdgen: "SDGen", tool_call: "自定义工具", nai: "NovelAI", anima_master: "Anima Master"};
    const legacyLabel = legacyLabels[config.legacy_backend];
    const migrationBanner = legacyLabel && !config.enabled && !routes.length
      ? `<div class="model-img-migration" role="status"><strong>${legacyLabel} 方式待接入</strong><span>原配置已保留。请在下方添加可用模型并补齐连接，再启用生图后端。</span></div>`
      : "";
    return `<section class="model-img-route-editor" aria-label="生图模型与优先级">
      ${migrationBanner}
      <header class="model-imgconfig-head"><div><span class="model-eb">${operation === "generate" ? "文本提示生成图片" : "参考图片编辑"}</span><h3>${IMAGE_VIEWS[i.view || "generate"].label}模型与优先级</h3><p>${operation === "generate" ? "按列表顺序选择文生图模型；首个成功执行的模型结束本次尝试。" : "普通改图与蒙版局部改图共用模型队列；不支持当前操作的模型会跳过。"}</p></div><label class="model-img-enable"><input type="checkbox" data-img-config-field="enabled"${config.enabled ? " checked" : ""}${locked ? " disabled" : ""}>启用 V2 生图后端</label></header>
      <div class="model-img-main"><div class="model-img-sectionhead"><div><b>当前功能的模型顺序</b><span>数字越小越先尝试 · 使用上下移按钮调整优先级</span></div><span class="model-tag">${routes.length} 个模型</span></div>
        <div class="model-img-routes">${routes.map((route, index) => imageRouteCard(i, route, index, config)).join("") || '<div class="model-img-empty">此功能尚未配置模型。添加在线图片模型或本地后端后，调用会按此列表顺序尝试。</div>'}</div>
        <div class="model-img-addbar"><label><span>添加已配置模型</span><select data-img-route-choice aria-label="选择要加入当前功能的模型"${locked || !choices.length ? " disabled" : ""}><option value="">选择模型或后端</option>${choices.map(([value, label]) => `<option value="${esc(value)}">${esc(label)}</option>`).join("")}</select></label><button type="button" class="button small" data-img-route-add${locked || !choices.length ? " disabled" : ""}>添加</button><button type="button" class="button small primary" data-img-model-add${locked || config.endpoints.length >= (i.config.limits?.max_endpoints || 24) ? " disabled" : ""}>新增在线模型</button></div>
        <details class="model-img-advanced"><summary>工作流与网络选项</summary>${legacySummary}<div class="model-img-route-grid"><label class="model-img-field"><span>图片下载代理</span><input type="url" data-img-config-field="network.download_proxy" value="" placeholder="${config.network?.download_proxy_set ? "已保存 · 留空保留" : "可选，例如 http://127.0.0.1:7890"}"${locked ? " disabled" : ""}><small>留空保留已保存代理</small></label><label class="model-img-field model-img-inline"><span>继承环境代理</span><input type="checkbox" data-img-config-field="network.use_environment_proxy"${config.network?.use_environment_proxy ? " checked" : ""}${locked ? " disabled" : ""}></label>${config.network?.download_proxy_set ? `<label class="model-img-field model-img-inline"><span>代理</span><span><input type="checkbox" data-img-config-field="network.clear_download_proxy"${config.network?.clear_download_proxy ? " checked" : ""}${locked ? " disabled" : ""}>清除已保存代理</span></label>` : ""}</div>${config.legacy_backend ? `<p class="model-note">已保存的生图方式：${esc(config.legacy_backend)}</p>` : ""}${Object.keys(legacyOptions).length ? `<details class="model-img-workflow model-img-legacy-raw"><summary><span>查看兼容参数</span></summary><pre>${esc(JSON.stringify(legacyOptions, null, 2))}</pre></details>` : ""}</details>
        <div class="model-img-savebar"><span role="status" class="${i.configBad ? "is-error" : ""}">${esc(i.configNotice || (dirty ? "有未保存的改动" : "已保存"))}</span><div><button type="button" class="button small" data-img-config-refresh${locked || dirty ? " disabled" : ""}>重新读取</button><button type="button" class="button small primary" data-img-config-save${locked || !dirty ? " disabled" : ""}>${i.configSaving ? "保存中…" : "保存模型配置"}</button></div></div>
        <p class="model-note">普通改图与蒙版局部改图共用此模型顺序；不支持当前操作的模型会跳过。密钥留空保留原值。测试会发送真实文生图请求，可能产生费用；结果只显示状态，不会发到聊天。</p>${imageSchemaBlock(i, i.configDraft)}</div></section>`;
  }
  function imageActionRow(i, action, index) {
    const item = i.data.actions.find(value => value.action === action);
    if (!item) return "";
    const enabled = !!i.draft[action], changed = enabled !== i.data.enabled[action];
    const stateLabel = !enabled ? "已关闭" : item.state === "ready" ? "已注册"
      : item.state === "unsupported" ? "当前队列未提供" : item.loaded ? "注册未就绪" : "扩展未加载";
    const diag = (item.state === "unsupported"
      ? [["已加载", item.loaded], ["队列可用", false]]
      : [["已加载", item.loaded], ["声明一致", item.current], ["已注册", item.registered]])
      .map(([label, ok]) => `<span class="model-diag ${ok ? "ok" : "no"}><i>${ok ? "✓" : "—"}</i>${label}</span>`).join("");
    const route = !enabled ? "V2 不会调用此动作" : item.state === "ready" ? "可调用 · 每次调用前校验授权"
      : item.state === "unsupported" ? imageDiagnostic(item.diagnostic_code)
      : item.loaded ? `声明与注册不一致 · ${imageDiagnostic(item.diagnostic_code)}`
      : `扩展未加载 · ${imageDiagnostic(item.diagnostic_code)}`;
    return `<li class="model-pv${enabled && item.state === "ready" ? " main" : ""}${enabled ? "" : " off"}"><span class="ix">${index + 1}</span><span class="nm"><strong>${esc(IMAGE_LABELS[action])}</strong><small>${esc(action)} · ${esc(item.owner || "尚无扩展提供")}${item.provider_id ? " · " + esc(item.provider_id) : ""}</small><span class="caps"><span class="model-tag ${enabled && item.state === "ready" ? "g" : enabled ? "a" : "n"}">${esc(stateLabel)}${changed ? " · 待保存" : ""}</span>${diag}</span><small class="rt">${esc(route)}</small></span><span class="mt"><label class="model-switch"><input type="checkbox" data-img-toggle="${esc(action)}"${enabled ? " checked" : ""}${i.loading || i.busy ? " disabled" : ""} aria-label="启用${esc(IMAGE_LABELS[action])}"><i></i></label><button type="button" class="button small" data-img-test="${esc(action)}"${i.testing ? " disabled" : ""}>${i.testing === action ? "检查中…" : "检查注册"}</button></span></li>`;
  }
  function imagePane(s) {
    const i = s.image;
    if (!i.data) return '<p class="model-empty">正在读取生图模型配置…</p>';
    const actions = i.data.actions, loaded = actions.filter(item => item.loaded).length, ready = actions.filter(item => item.state === "ready").length;
    const view = i.view || "generate", active = IMAGE_VIEWS[view];
    const configState = i.configLoading ? "读取配置中" : i.configDraft ? (i.configDraft.enabled ? "生图后端已启用" : "生图后端未启用") : "配置未读取";
    const configTone = i.configDraft?.enabled ? "is-ready" : "is-off";
    return `<div class="model-image">
      <section class="model-img-overview" aria-label="生图模型概览">
        <div class="model-img-overview-copy"><span class="model-eb">模型与连接</span><div class="model-img-overview-title"><h3>生图模型</h3><span class="model-img-backend-state ${configTone}">${configState}</span></div><p>文生图与改图各自维护模型队列。队列顺序决定优先尝试的模型，失败时按顺序继续。</p></div>
        <div class="model-img-overview-side"><div class="model-img-statusline"><span><b>${actions.length}</b>内置动作</span><span><b>${loaded}/${actions.length}</b>已加载</span><span><b>${ready}/${actions.length}</b>可调用</span><span><b>${i.data.authorization === "checked_for_each_invocation" ? "逐次" : "待确认"}</b>调用授权</span></div><button type="button" class="button small" data-img-refresh${i.loading || i.busy ? " disabled" : ""}>刷新状态</button></div>
      </section>
      ${imageModuleNotice(i.data) ? `<p class="model-img-module-note" role="status">${esc(imageModuleNotice(i.data))}</p>` : ""}
      <section class="model-imgconfig"><header class="model-imgconfig-head"><div><span class="model-eb">按功能独立设置模型</span><h3>文生图与改图</h3><p>两个功能分别维护模型、连接参数与优先级；改图包含普通改图和蒙版局部改图。</p></div><span class="model-tag">${active.label}</span></header><div class="model-img-workbench"><nav class="model-img-nav" role="tablist" aria-label="生图功能">${Object.entries(IMAGE_VIEWS).map(([id, item], index) => `<button type="button" role="tab" data-img-view="${id}" aria-selected="${view === id}" class="${view === id ? "on" : ""}"><span class="model-img-nav-no">0${index + 1}</span><span><b>${item.label}</b><small>${id === "generate" ? "文本生成图片" : "普通与局部改图 · 共用模型顺序"}</small></span><i>›</i></button>`).join("")}</nav><div class="model-img-main"><section class="model-img-view model-img-capabilities"><div class="model-img-sectionhead"><div><b>${active.label}能力</b><span>${view === "generate" ? "检查文生图动作是否已注册并开启。" : "参考图改图与蒙版局部改图分别检查能力，模型队列共用。"}</span></div><span class="model-tag">${active.actions.length} 个动作</span></div><ul class="model-pvl">${active.actions.map((action, index) => imageActionRow(i, action, index)).join("")}</ul><div class="model-editor-actions"><button type="button" class="button primary" data-img-save${i.busy || i.loading || !imageDirty(i) ? " disabled" : ""}>保存能力开关</button></div>${imageConfigEditor(s)}</section></div></div></section>
      ${i.bad ? `<div class="model-notice error" role="status">${esc(i.notice)}</div>` : i.notice ? `<div class="model-notice" role="status">${esc(i.notice)}</div>` : ""}
    </div>`;
  }
  function imageInput(s, event) {
    const i = s.image, t = event.target;
    if (t.matches("[data-img-toggle]") && IMAGE_LABELS[t.dataset.imgToggle] && !i.loading && !i.busy) {
      i.draft[t.dataset.imgToggle] = t.checked; render(); return;
    }
    if (t.matches("[data-img-config-field]") && i.configDraft && !i.configSaving) {
      const keys = t.dataset.imgConfigField.split("."); let target = i.configDraft;
      for (const key of keys.slice(0, -1)) { target[key] ??= {}; target = target[key]; }
      const key = keys.at(-1), value = t.type === "checkbox" ? t.checked : t.type === "number" ? (t.value === "" ? "" : Number(t.value)) : t.value;
      target[key] = value;
      i.configBad = false; i.configNotice = "";
      if (key === "clear_api_key" && value) target.api_key = "";
      if (key === "api_key" && value) target.clear_api_key = false;
      if (key === "clear_download_proxy" && value) target.download_proxy = "";
      if (key === "download_proxy" && value) target.clear_download_proxy = false;
      if (event.type === "change") render(); else imageConfigSaveState(i);
      return;
    }
    if (t.matches("[data-img-config-json]") && i.configText && !i.configSaving) {
      i.configText[t.dataset.imgConfigJson] = t.value;
      i.configBad = false; i.configNotice = ""; imageConfigSaveState(i);
    }
  }
  function imageConfigSaveState(i) {
    const dirty = imageConfigDirty(i);
    root?.querySelectorAll("[data-img-config-save]").forEach(button => { button.disabled = i.configSaving || !dirty; });
    root?.querySelectorAll(".model-img-savebar [role=status]").forEach(node => { node.textContent = i.configNotice || (dirty ? "有未保存的改动" : "已保存"); });
  }
  let imageSchema = null, imageSchemaState = "idle";
  async function loadImageSchema(s) {
    if (imageSchemaState !== "idle") return;
    imageSchemaState = "loading";
    try {
      const data = await api("GET", path + "/image-schema");
      imageSchema = data?.image_provider_group || null;
    } catch { imageSchema = null; }
    imageSchemaState = imageSchema ? "done" : "failed";
    if (imageSchema && s) render();
  }

  /* 生图参数表单直接写入已有配置草稿；高级 JSON 用于声明外参数。 */
  function imageSchemaKinds(i) {
    const config = i?.configDraft;
    if (!config) return [];
    const operation = imageCurrentOperation(i);
    const routes = config.operation_routes?.[operation] || [];
    const kinds = [];
    for (const ref of routes) {
      if (IMAGE_LOCAL_LABELS[ref]) kinds.push(ref);
      else if (ref.startsWith("online:")) kinds.push("openai_images");
    }
    return [...new Set(kinds)];
  }
  function imageSchemaBlock(i, config = null) {
    if (!imageSchema?.sd_webui || !config?.sd_webui) return "";
    const active = imageSchemaKinds(i);
    const ordered = active.includes("sd_webui") ? ["sd_webui"] : [];
    const rows = ordered.map(kind => {
      const entry = imageSchema[kind];
      const groups = new Map();
      for (const [name, spec] of Object.entries(entry.fields || {})) {
        if (spec.storage !== "parameters" || !["integer", "number", "text", "select"].includes(spec.kind)) continue;
        const group = spec.group || "sampling";
        if (!groups.has(group)) groups.set(group, []);
        groups.get(group).push([name, spec]);
      }
      const groupLabels = {connection: "连接", workflow: "工作流", sampling: "采样参数"};
      const operation = imageCurrentOperation(i);
      const forms = [...groups].map(([group, fields]) => `<fieldset class="model-schema-group"><legend>${groupLabels[group] || esc(group)}</legend>
        <div class="model-img-route-grid">${fields.map(([name, spec]) => {
          const path = `sd_webui.parameters.${operation}.${name}`;
          const current = config.sd_webui.parameters?.[operation]?.[name]
            ?? config.sd_webui.parameters?.[name] ?? spec.default ?? "";
          const attrs = `${spec.min !== undefined ? ` min="${spec.min}"` : ""}${spec.max !== undefined ? ` max="${spec.max}"` : ""}${spec.step !== undefined ? ` step="${spec.step}"` : spec.kind === "integer" ? ' step="1"' : ""}`;
          if (spec.kind === "select") return `<label class="model-img-field"><span>${esc(spec.label || name)}</span><select data-img-config-field="${esc(path)}">${(spec.choices || []).map(choice => `<option value="${esc(choice)}"${current === choice ? " selected" : ""}>${esc(choice)}</option>`).join("")}</select><code>${esc(name)}</code></label>`;
          const type = spec.kind === "integer" || spec.kind === "number" ? "number" : "text";
          return imageConfigField(spec.label || name, path, current, type, attrs, `<code>${esc(name)}</code>`);
        }).join("")}</div></fieldset>`).join("");
      return forms ? `<section class="model-schema-provider"><h4>${esc(entry.label || kind)}</h4>${forms}</section>` : "";
    }).join("");
    return rows ? `<section class="model-schema" aria-label="生图参数">${rows}</section>` : "";
  }

  function imageClick(s, event) {
    const i = s.image, t = event.target;
    const view = t.closest("[data-img-view]");
    if (view && IMAGE_VIEWS[view.dataset.imgView]) {
      i.view = view.dataset.imgView;
      render(); return;
    }
    const expand = t.closest("[data-img-route-expand]");
    if (expand) {
      event.preventDefault?.();
      const ref = expand.dataset.imgRouteExpand;
      const index = i.configDraft?.operation_routes?.[imageCurrentOperation(i)]?.indexOf(ref) ?? -1;
      i.expandedImageRoutes ||= {};
      i.expandedImageRoutes[ref] = !(i.expandedImageRoutes[ref] ?? (index === 0));
      render(); return;
    }
    if (t.closest("[data-img-refresh]")) { imageRefresh(s); return; }
    if (t.closest("[data-img-save]")) { imageSave(s); return; }
    if (t.closest("[data-img-config-refresh]")) { void imageConfigRefresh(s); return; }
    if (t.closest("[data-img-config-save]")) { void imageConfigSave(s); return; }
    const endpointTest = t.closest("[data-img-endpoint-test]");
    if (endpointTest) { void imageEndpointTest(s, endpointTest.dataset.imgEndpointTest); return; }
    const move = t.closest("[data-img-route-move]");
    if (move && i.configDraft && !i.configSaving) {
      const queue = i.configDraft.operation_routes[imageCurrentOperation(i)], index = Number(move.dataset.imgRouteIndex), next = index + (move.dataset.imgRouteMove === "up" ? -1 : 1);
      if (next >= 0 && next < queue.length) [queue[index], queue[next]] = [queue[next], queue[index]];
      render(); return;
    }
    const removeRoute = t.closest("[data-img-route-remove]");
    if (removeRoute && i.configDraft && !i.configSaving) {
      const queue = i.configDraft.operation_routes[imageCurrentOperation(i)]; queue.splice(queue.indexOf(removeRoute.dataset.imgRouteRemove), 1); render(); return;
    }
    const removeEndpoint = t.closest("[data-img-endpoint-remove]");
    if (removeEndpoint && i.configDraft && !i.configSaving) {
      const id = removeEndpoint.dataset.imgEndpointRemove;
      i.configDraft.endpoints = i.configDraft.endpoints.filter(item => item.id !== id);
      for (const operation of ["generate", "edit"]) i.configDraft.operation_routes[operation] = i.configDraft.operation_routes[operation].filter(route => route !== `online:${id}`);
      render(); return;
    }
    if (t.closest("[data-img-route-add]") && i.configDraft && !i.configSaving) {
      const choice = root?.querySelector("[data-img-route-choice]")?.value, queue = i.configDraft.operation_routes[imageCurrentOperation(i)];
      if (choice && !queue.includes(choice)) queue.push(choice);
      render(); return;
    }
    if (t.closest("[data-img-model-add]") && i.configDraft && !i.configSaving) {
      const max = i.config.limits?.max_endpoints || 24;
      if (i.configDraft.endpoints.length >= max) return;
      const id = `image-${crypto.randomUUID().replace(/-/g, "").slice(0, 16)}`;
      i.configDraft.endpoints.push({id, name: "", enabled: false,
        platform: "openai", model: "", width: 1024, height: 1024, base_url: "", api_key_set: false,
        timeout_seconds: 180, quality: "", response_format: ""});
      i.expandedImageRoutes ||= {};
      i.expandedImageRoutes["online:" + id] = true;
      i.configDraft.operation_routes[imageCurrentOperation(i)].push(`online:${id}`); render(); return;
    }
    const testBtn = t.closest("[data-img-test]"); if (testBtn) imageTest(s, testBtn.dataset.imgTest);
  }
  /* ---------- top-level render & routing ---------- */
  function semanticBanner() {
    return `<div class="model-note cfg"><span>向量嵌入、候选重排和视觉分析不在任务路由里，而在「学习与反思 → 语义目录」统一配置；列表为空时先在 AstrBot 模型服务中加载对应模型。</span><button type="button" class="button small" data-go="manage" data-manage="learning" data-manage-subtab="semantic">打开向量模型配置</button></div>`;
  }
  function tabBar(s) { const tabs = [["fn", "功能模型", ICONS.fn, snapshot(s).length],
      ["voice", "语音模型", ICONS.voice, s.voice.data ? s.voice.data.providers.length : ""],
      ["image", "生图模型", ICONS.image, s.image.data ? s.image.data.actions.length : ""],
      ["search", "搜索服务", ICONS.search, s.search?.data?.deepseek_provider_id ? "已绑定" : ""],
      ["laya", "Laya 决策", ICONS.decision, s.laya.data ? (s.laya.data.enabled ? "启用" : "") : ""]];
    return `<div class="model-tabs" role="tablist" aria-label="模型配置分类">${tabs.map(([id, label, ic, count]) =>
      `<button type="button" role="tab" data-model-tab="${id}" aria-selected="${s.tab === id}" class="${s.tab === id ? "on" : ""}">${icon(ic)}${label}${count !== "" ? `<span class="c">${count}</span>` : ""}</button>`).join("")}</div>`; }
  /* ---------- functional subtabs (design migration) ---------- */
  function subtabBar(s) { const tabs = [
      ["map", "态势图", "map", "查看任务当前路由、备用与风险"],
      ["cfg", "模型配置", "cfg", "逐项编辑主模型、备用链与提示"],
      ["pol", "替换策略", "pol", "统一管理回退、拒答替代与窗口"],
    ];
    return `<div class="model-subtabs" role="tablist" aria-label="功能模型视图">${tabs.map(([id, label, ic, desc]) => `<button type="button" role="tab" data-model-sub="${id}" aria-selected="${s.sub === id}" class="${s.sub === id ? "on" : ""}"><span class="model-subtab-icon">${icon(ICONS[ic])}</span><span class="model-subtab-copy"><b>${label}</b><small>${desc}</small></span></button>`).join("")}</div>`; }
  function kpi(n, label, tone = "") { return `<div class="model-kpi ${tone}"><b>${n}</b><span>${esc(label)}</span></div>`; }
  function fnKpis(s) { const tasks = snapshot(s); const total = tasks.length; const providers = s.status?.providers.length || 0;
    const withFb = tasks.filter(fallbackFor).length; const own = tasks.filter(t => taskFields(t).some(nm => t.fields[nm]?.explicit)).length;
    return kpi(total, "需要模型的功能") + kpi(providers, "可用模型") + kpi(withFb, "已设回退", "g")
      + kpi(total - withFb, "暂无回退", "a") + kpi(own, "单独设置"); }
  function sitFilters(s) { const avSeg = [["slot", "按能力槽"], ["lane", "按前台 / 后台"]]; const afSeg = [["all", "全部"], ["nofb", "仅无备用"], ["own", "仅单独设置"]];
    const seg = (cls, attr, arr, cur) => `<div class="${cls}" role="group">${arr.map(([id, label]) => `<button type="button" data-model-${attr}="${id}" aria-pressed="${cur === id}" class="${cur === id ? "active" : ""}">${label}</button>`).join("")}</div>`;
    return `<div class="model-map-filters"><label class="model-search"><span class="sr-only">搜索任务</span><input type="search" data-model-search value="${esc(s.query)}" placeholder="按名称或用途搜索"></label>${seg("model-filters", "av", avSeg, s.av)}${seg("model-filters", "af", afSeg, s.af)}</div>`; }
  const providerPill = (id, missing, hovered) => id
    ? `<button type="button" class="model-pill m${hovered === id ? " is-hovered" : ""}" data-provider-pill="${esc(id)}" style="--mc:${pillColor(id)}" title="查看 ${esc(id)} 的路由分工">${esc(id)}</button>`
    : `<span class="model-pill m none">${missing || "无回退"}</span>`;
  function situationMap(s) { const tasks = snapshot(s); const q = s.query.toLocaleLowerCase();
    const match = t => { if (s.af === "nofb" && (coreReply(t) || fallbackFor(t))) return false; if (s.af === "own" && !taskFields(t).some(nm => t.fields[nm]?.explicit)) return false;
      if (!q) return true; const info = taskName(t.kind); return (info.label + info.group + info.description + t.kind).toLocaleLowerCase().includes(q); };
    let groups2;
    if (s.av === "lane") groups2 = [["前台 · 用户可见", tasks.filter(t => t.lane === "foreground")], ["后台 · 静默运行", tasks.filter(t => t.lane === "background")]];
    else groups2 = taskGroups(s).map(([g, items]) => [g, items.map(([kind]) => taskOf(s, kind))]);
    const list = groups2.filter(([, ts]) => ts.length).map(([g, ts]) => {
      const shownTasks = ts.filter(match);
      const items = shownTasks.map(t => { const info = taskName(t.kind); const main = coreReply(t) ? "" : t.fields.provider?.value || ""; const fb = fallbackFor(t);
        const rules = parseRules(t.fields.replacement_rules.value).length, refusal = t.fields.fallback_on_refusal.value === true;
        const picked = s.rule.picked.includes(t.kind);
        return `<li class="model-item${picked ? " seled" : ""}"><label class="model-item-sel"><input type="checkbox" data-map-pick="${esc(t.kind)}" ${picked ? "checked" : ""} ${coreReply(t) ? "disabled" : ""} aria-label="选择 ${esc(info.label)} 参与批量测试"></label>
          <span class="model-item-nm"><button type="button" class="model-map-open" data-model-open="${esc(t.kind)}" aria-label="编辑 ${esc(info.label)}"><b>${esc(info.label)}</b></button></span>
          <span class="model-item-ch">${coreReply(t) ? '<span class="model-ln"><i>回复模型</i><span>AstrBot 当前会话</span></span><span class="model-ln"><i>附加提示</i><span>本页可编辑</span></span>' : `
            <span class="model-ln"><i>默认模型</i><span class="model-ps">${providerPill(main, "未设置", s.hoverProvider)}</span></span>
            <span class="model-ln"><i>备用模型</i><span class="model-ps">${providerPill(fb, "", s.hoverProvider)}${rules ? `<span class="model-tag">${rules} 规则</span>` : ""}${refusal ? '<span class="model-tag a">拒答替代</span>' : ""}</span></span>
          `}</span></li>`; }).join("");
      const sel = ts.filter(t => s.rule.picked.includes(t.kind)).length;
      const all = shownTasks.length && shownTasks.every(t => s.rule.picked.includes(t.kind));
      const gkey = s.av === "lane" ? "lane:" + (g.startsWith("前台") ? "foreground" : "background") : g;
      return `<section class="model-grp"><header><input type="checkbox" class="model-gck" data-model-gpick="${esc(gkey)}" ${all ? "checked" : ""} aria-label="全选 ${esc(g)}">${icon(groupIcon(g))}<b>${esc(g)}</b><span class="cnt">${ts.length} 项${sel ? " · 已选 " + sel : ""}</span></header>
        ${items || '<p class="model-empty">没有匹配的任务。</p>'}</section>`; }).join("");
    const legend = (s.status?.providers || []).map(p => `<span><i style="background:${pillColor(p.id)}"></i><em>${esc(p.id)}</em></span>`).join("");
    return `<div class="model-subpane"><div class="model-kpis">${fnKpis(s)}</div>${sitFilters(s)}
      <div class="model-mlgd legend">${legend}</div>
      <div class="model-rgrid">${list}</div>${testBar(s)}</div>`; }
  const probeLabel = result => result.status === "ok" ? `连接成功 · ${result.latency_ms} ms`
    : result.status === "testing" ? "测试中…" : errors[result.code] || "结果未知";
  async function probeProvider(s, provider) {
    if (!provider || s.testing.has(provider) || !s.status?.providers.some(item => item.id === provider)) return;
    const ticket = version, endpoint = path + "/test";
    s.testing.add(provider); s.providerTests[provider] = {status: "testing"}; render();
    try { const result = await api("POST", endpoint, {provider_id: provider}, 35000);
      if (!current(ticket, s)) return;
      if (result.provider_id !== provider || !["ok", "unknown", "unavailable"].includes(result.status)) throw failure("invalid_response");
      s.providerTests[provider] = result;
    } catch (err) { if (!current(ticket, s)) return; s.providerTests[provider] = {status: "unknown", code: err.code || err.message}; }
    finally { s.testing.delete(provider); if (current(ticket, s)) render(); }
  }
  function testBar(s) { const picked = s.rule.picked.filter(kind => taskOf(s, kind) && !coreReply(taskOf(s, kind))); if (!picked.length) return "";
    const providers = new Set();
    picked.forEach(kind => { const task = taskOf(s, kind), draft = s.drafts.get(kind);
      const primary = providerFor(task, draft); if (primary) providers.add(primary);
      const fallback = effective(task, draft, "fallback"); if (fallback) providers.add(fallback); });
    const rows = (s.rule.report || []).map(line => { const st = line.provider ? s.providerTests[line.provider] : null;
      const running = s.rule.testing && (!st || st.status === "testing");
      const cls = running ? "run" : st && st.status === "ok" ? "ok" : running ? "run" : "bad";
      const tail = !line.provider ? "无模型提供方"
        : running ? '<span class="model-sp"></span>测试中'
        : st ? esc(probeLabel(st)) : "结果未知";
      return `<div class="model-trow ${cls}"><span class="dotm" style="background:${pillColor(line.provider)}"></span><span class="mno">${esc(line.provider || "—")}</span>
        <span class="u">${esc(line.role)} · ${esc(line.label)}</span><span class="tr">${tail}</span></div>`; }).join("");
    const total = (s.rule.report || []).length;
    const done = (s.rule.report || []).filter(line => { const st = line.provider && s.providerTests[line.provider]; return st && st.status !== "testing"; }).length;
    const okCount = (s.rule.report || []).filter(line => s.providerTests[line.provider]?.status === "ok").length;
    return `<div class="model-dock"><div class="model-selbar"><span>已选 <b>${picked.length}</b> 项任务 · 涉及 <b>${providers.size}</b> 个模型提供方</span>
      <span class="model-bar${total && done === total && okCount === total ? " full" : ""}"><i style="width:${total ? Math.round(done / total * 100) : 0}%"></i></span>
      <span class="model-grow"></span>
      <button type="button" class="button small" data-map-clear ${s.rule.testing ? "disabled" : ""}>清空</button>
      <button type="button" class="button small primary" data-map-test ${s.rule.testing ? "disabled" : ""}>${s.rule.testing ? "测试中…" : "批量测试"}</button></div>
      ${s.rule.report ? `<div class="model-tres"><div class="th"><b>测试</b><span class="s">${s.rule.testing ? `正在测试 ${picked.length} 项` : `${total} 项完成 · ${okCount} 通过${okCount < total ? " · " + (total - okCount) + " 失败" : ""}`}</span></div>
        <div class="tbody">${rows}</div></div>` : ""}</div>`; }
  async function runMapTest(s) { if (s.rule.testing) return; const ticket = version, picked = s.rule.picked.filter(kind => taskOf(s, kind) && !coreReply(taskOf(s, kind)));
    const report = [];
    picked.forEach(kind => { const task = taskOf(s, kind), draft = s.drafts.get(kind), info = taskName(kind);
      const primary = providerFor(task, draft), fallback = effective(task, draft, "fallback");
      report.push({ provider: primary, role: "主模型提供方", label: info.label });
      if (fallback) report.push({ provider: fallback, role: "备用模型提供方", label: info.label }); });
    s.rule.report = report; s.rule.testing = true; s.bad = false; render();
    for (const provider of new Set(report.map(line => line.provider).filter(Boolean))) {
      await probeProvider(s, provider); if (!current(ticket, s)) return;
    }
    s.rule.testing = false;
    s.bad = report.some(line => !line.provider || s.providerTests[line.provider]?.status !== "ok");
    s.notice = s.bad ? "部分模型提供方未通过测试，请核对结果。" : "所选任务涉及的模型提供方均已通过测试。"; render(); }
  function quickConfig(s) { const providers = s.status?.providers || [];
    const opts = id => `<option value="">跟随分组默认</option>${providers.map(p => `<option value="${esc(p.id)}" ${s.slotDraft[id] === p.id ? "selected" : ""}>${esc(p.id)}</option>`).join("")}`;
    const cards = taskGroups(s).filter(([, entries]) => entries.some(([kind]) => !coreReply(taskOf(s, kind))))
      .map(([g, entries]) => { const items = entries.filter(([kind]) => !coreReply(taskOf(s, kind))); const kinds = items.map(([kind]) => kind);
      const cur = kinds.map(k => { const t = taskOf(s, k), d = s.drafts.get(k); return t && d ? providerFor(t, d) : null; }).filter(Boolean);
      const same = cur.length && cur.every(v => v === cur[0]) ? cur[0] : "";
      const own = kinds.filter(k => { const d = s.drafts.get(k); return d && fields.some(name => !d.inherited[name]); }).length;
      const routed = kinds.map(k => { const t = taskOf(s, k); return t ? parseRules(t.fields.replacement_rules.value).length : 0; }).reduce((a, b) => a + b, 0);
      const refusal = kinds.filter(k => { const t = taskOf(s, k); return t && t.fields.fallback_on_refusal.value === true; }).length;
      const open = !!s.expOpen[g];
      return `<article class="model-sc">
        <div class="model-sc-tp">
          <div class="model-eb">快速配置<span class="st">${own ? `<span class="model-tag a">${own} 项单独设置</span>` : '<span class="model-tag n">默认统一</span>'}</span></div>
          <div class="model-ti">${icon(groupIcon(g))}<b>${esc(g)}</b></div>
          <div class="model-ds">${esc(items.map(([, label]) => label).join("、"))}</div>
          <div class="model-rl"><span>${items.length} 项任务</span><span>替换规则 ${routed} 条</span>${refusal ? `<span>拒答替代 ${refusal} 项</span>` : ""}</div>
        </div>
        <div class="model-sc-bd">
          <label class="model-fd"><span class="model-cap">统一指派模型提供方 <em>写入每项任务的主模型提供方</em></span>
            <select data-model-slot="${esc(g)}" aria-label="${esc(g)} 统一指派">${opts(g)}</select></label>
          <div class="model-fd"><span class="model-cap">当前统一路由</span>
            <div class="model-cur${same ? "" : " miss"}">${esc(same || "混合 / 跟随上级")}</div></div>
        </div>
        <div class="model-sc-rf">
          <div class="r"><b>用途</b><span>${esc(items.map(([, label, desc]) => `${label}：${desc}`).join("；"))}</span></div>
          <div class="r"><b>覆盖</b><span>只写入各任务的主模型提供方，保留各自的备用、提示词与替换规则；可随时在精确配置改回跟随上级。</span></div>
        </div>
        <div class="model-sc-ft"><code>${esc(g)}</code><span class="model-grow"></span>
          <button type="button" class="model-lk" data-model-exp="${esc(g)}">${open ? "收起" : "覆盖"} ${items.length} 项${own ? ` · ${own} 项单独设置` : ""} ▾</button>
          <button type="button" class="button small primary" data-model-slot-apply="${esc(g)}" ${s.busy || s.loading || s.denied || !s.slotDraft[g] ? "disabled" : ""}>应用到本组</button></div>
        ${open ? `<div class="model-exp"><ul>${kinds.map(k => { const t = taskOf(s, k), d = s.drafts.get(k);
          const isOwn = d && fields.some(name => !d.inherited[name]);
          return `<li>${esc(taskName(k)?.label || k)}<span>${t ? providerFor(t, d) || "未设置" : "未注册"}${isOwn ? " · 单独设置" : " · 跟随本组"}</span></li>`; }).join("")}</ul></div>` : ""}
      </article>`; }).join("");
    const core = snapshot(s).find(coreReply);
    return `<div class="model-subpane">${core ? '<p class="model-note">普通对话由 AstrBot 当前会话管理；下方快速指派只覆盖插件模型任务。对话附加提示在精确配置中编辑。</p>' : ""}<div class="model-qgrid">${cards}</div></div>`; }
  async function applySlot(s, slotId) { const gp = taskGroups(s).find(([g]) => g === slotId); if (!gp) return; const providerId = s.slotDraft[slotId]; if (!providerId) return;
    const kinds = [];
    for (const [kind] of gp[1]) { const task = taskOf(s, kind), draft = s.drafts.get(kind); if (!task || coreReply(task) || !draft || draft.pending || draft.stale) continue;
      if (!task.capabilities.every(cap => s.status.providers.find(p => p.id === providerId)?.capabilities.includes(cap))) continue;
      draft.inherited.provider = false; draft.values.provider = providerId; draft.touched = true; fitLimit(s, task, draft); kinds.push(kind); }
    if (!kinds.length) { s.bad = true; s.notice = "这些任务没有可改动的草稿或能力不符。"; render(); return; }
    s.notice = `正在把「${slotId}」组中 ${kinds.length} 项任务指派给 ${providerId}…`; render();
    await saveTasks(s, kinds); }
  /* ---------- replacement policy (keyword / time-window rules, refusal retry) ---------- */
  /* Rules live in one JSON string per task; the gateway's replacement_rules.build_rules
     reads the same keys, so the editor round-trips them verbatim. */
  const RULE_SEED = { id: "rule-1", provider_id: "", model: "", priority: 0, keywords: [], match_mode: "contains", keyword_logic: "any", case_sensitive: false, enabled: true };
  const MATCH_MODES = [["contains", "包含"], ["exact", "精确"], ["regex", "正则"]];
  const LOGICS = [["any", "任一"], ["all", "全部"]];
  const WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
  const DEFAULT_WINDOW = "09:00-12:00\n14:00-18:00";
  const refusalTerms = text => { const seen = new Set();
    return String(text || "").replace(/\r\n?/g, "\n").split(/[\n;；]+/).map(term => term.trim()).filter(term => {
      const key = term.toLocaleLowerCase(); if (!key || seen.has(key)) return false; seen.add(key); return true; }); };
  const ruleMode = rule => MATCH_MODES.some(([id]) => id === rule.match_mode) ? rule.match_mode : "contains";
  const ruleKeywordText = rule => Array.isArray(rule.keywords) ? rule.keywords.join("，") : "";
  function parseRules(text) { if (typeof text !== "string" || !text.trim()) return [];
    try { const parsed = JSON.parse(text); const list = Array.isArray(parsed) ? parsed : parsed && typeof parsed === "object" ? [parsed] : [];
      return list.filter(item => item && typeof item === "object" && !Array.isArray(item)); }
    catch { return []; } }
  const serializeRules = rules => JSON.stringify(rules, null, 2);
  const keywordChips = text => String(text || "").split(/[,，;；\n]+/).map(word => word.trim()).filter(Boolean);
  function parseWindows(text) { const cells = new Set();
    String(text || "").split(/[\n,;，；]+/).map(part => part.trim()).filter(Boolean).forEach(part => {
      const match = part.match(/^(\d{1,2}):(\d{2})\s*[-–~至]\s*(\d{1,2}):(\d{2})$/); if (!match) return;
      let start = Number(match[1]), end = Number(match[3]);
      if (!Number.isInteger(start) || !Number.isInteger(end) || start > 23 || end > 24) return;
      if (end <= start) end += 24;
      for (let hour = start; hour < end; hour++) cells.add(hour % 24); });
    return cells; }
  function formatWindows(cells) { if (!cells.size) return "";
    const hours = [...cells].sort((a, b) => a - b), spans = [];
    let start = hours[0], previous = hours[0];
    for (let index = 1; index <= hours.length; index++) { const current = hours[index];
      if (current === previous + 1) { previous = current; continue; }
      spans.push(String(start).padStart(2, "0") + ":00-" + String((previous + 1) % 24).padStart(2, "0") + ":00");
      start = previous = current; }
    if (spans.length === 1 && spans[0] === "00:00-00:00") return "00:00-12:00\n12:00-00:00";
    return spans.join("\n"); }
  const peakWindows = peak => parseWindows(peak.windows);
  const timeValid = value => /^\d{2}:\d{2}$/.test(value) && Number(value.slice(0, 2)) < 24 && Number(value.slice(3)) < 60;
  const peakSegments = text => String(text || "").split(/\n+/).map(span => span.trim()).filter(Boolean)
    .map(span => span.split("-")).filter(parts => parts.length === 2 && parts.every(timeValid) && parts[0] !== parts[1]);
  const ruleOf = (s, kind) => { const task = taskOf(s, kind); if (!task || !s.drafts.has(kind)) return [];
    const draft = s.drafts.get(kind);
    return parseRules(draft.inherited.replacement_rules ? task.fields.replacement_rules.inherited_value : draft.values.replacement_rules); };
  const dayLabel = days => Array.isArray(days) && days.length === 7 ? "每天"
    : Array.isArray(days) && days.length ? days.map(day => WEEKDAYS[day] || day).join(" ") : "每天";
  function timeText(rule) { const window = rule.time_window; if (!window || typeof window !== "object") return "";
    return `${window.start || ""}-${window.end || ""}`; }
  /* ---------- replacement policy: markup ---------- */
  /* The policy pane always has an active task: the explicit pick, else the first. */
  const policyTask = s => snapshot(s).filter(task => !coreReply(task)).find(task => task.kind === s.pol)
    || snapshot(s).find(task => !coreReply(task)) || null;
  function ruleCard(s, task, rule, index) {
    const info = taskName(task.kind), draft = s.drafts.get(task.kind), locked = s.loading || s.busy || s.denied || draft?.pending;
    const providers = available(s, task), picked = typeof rule.provider_id === "string" ? rule.provider_id : "";
    const extra = providers.some(provider => provider.id === picked) ? "" : picked ? `<option value="${esc(picked)}" selected>${esc(picked)} · 当前不可用</option>` : "";
    const window = rule.time_window && typeof rule.time_window === "object" ? rule.time_window : null;
    const ref = `${esc(task.kind)}:${index}`;
    return `<li class="model-kw${rule.enabled === false ? " off" : ""}" data-rule-card="${ref}">
      <div class="model-kwh">
        <span class="model-ix">#${index + 1}</span>
        <input class="model-kn" value="${esc(rule.id || "")}" data-rule-id="${ref}" aria-label="规则名" ${locked ? "disabled" : ""}>
        <label class="model-switch sm"><input type="checkbox" data-rule-enabled="${ref}" ${rule.enabled === false ? "" : "checked"} ${locked ? "disabled" : ""}><i></i></label>
        <button type="button" class="button small danger" data-rule-del="${ref}" ${locked ? "disabled" : ""}>删除</button>
      </div>
      <div class="model-kwc">
        <span class="model-cap">关键词</span>
        <textarea class="model-kk" rows="1" data-rule-keywords="${ref}" placeholder="写代码，代码报错（逗号或换行分隔）" ${locked ? "disabled" : ""}>${esc(ruleKeywordText(rule))}</textarea>
        <span class="model-arw">→</span>
        <select data-rule-provider="${ref}" aria-label="规则目标模型提供方" ${locked ? "disabled" : ""}><option value="">未选择 · 规则不生效</option>${providers.map(provider => `<option value="${esc(provider.id)}" ${provider.id === picked ? "selected" : ""}>${esc(provider.id)}</option>`).join("")}${extra}</select>
      </div>
      <div class="model-kwc">
        <span class="model-cap">匹配</span>
        <select data-rule-match="${ref}" aria-label="匹配方式" ${locked ? "disabled" : ""}>${MATCH_MODES.map(([id, label]) => `<option value="${id}" ${ruleMode(rule) === id ? "selected" : ""}>${label}</option>`).join("")}</select>
        <span class="model-cap">条件</span>
        <select data-rule-logic="${ref}" aria-label="条件" ${locked ? "disabled" : ""}>${LOGICS.map(([id, label]) => `<option value="${id}" ${(rule.keyword_logic === "all" ? "all" : "any") === id ? "selected" : ""}>${label}</option>`).join("")}</select>
        <span class="model-cap">优先级</span>
        <input class="model-num" type="number" step="1" data-rule-priority="${ref}" value="${esc(Number.isInteger(rule.priority) ? rule.priority : 0)}" ${locked ? "disabled" : ""}>
        <span class="model-cap">模型覆盖</span>
        <input class="model-mo" data-rule-model="${ref}" value="${esc(rule.model || "")}" placeholder="${esc(picked && providers.find(provider => provider.id === picked)?.supports_model_override ? "该模型提供方支持覆盖" : "留空沿用默认")}" ${locked ? "disabled" : ""}>
      </div>
      <div class="model-rule-time">
        <label class="model-cap"><input type="checkbox" data-rule-window="${ref}" ${window ? "checked" : ""} ${locked ? "disabled" : ""}> 限定生效时段</label>
        ${window ? `<div class="model-band" role="img" aria-label="${esc(timeText(window))}">${Array.from({ length: 24 }, (_, hour) =>
          `<span class="model-band-cell${parseWindows(timeText(window)).has(hour) ? " on" : ""}${hour % 3 === 0 ? " tick" : ""}" title="${hour}:00">${hour % 3 === 0 ? hour : ""}</span>`).join("")}</div>
        <div class="model-kwc">
          <span class="model-cap">开始</span><input class="model-num" data-rule-start="${ref}" value="${esc(window.start || "")}" maxlength="5" placeholder="09:00" ${locked ? "disabled" : ""}>
          <span class="model-cap">结束</span><input class="model-num" data-rule-end="${ref}" value="${esc(window.end || "")}" maxlength="5" placeholder="18:00" ${locked ? "disabled" : ""}>
          <span class="model-cap">时区</span><input class="model-mo" data-rule-zone="${ref}" value="${esc(window.timezone || "Asia/Shanghai")}" ${locked ? "disabled" : ""}>
        </div>
        <p class="model-note">生效日：${esc(dayLabel(window.weekdays))} · 未选关键词时整段生效</p>` : ``}
      </div>
      ${Array.isArray(rule.keywords) && rule.keywords.length ? `<div class="model-chips">${rule.keywords.map(word => `<span class="model-kchip">${esc(word)}</span>`).join("")}</div>` : ""}
      <p class="model-rule-src">${esc(info.label)} · ${esc(task.kind)}</p></li>`; }
  function policyTaskOptions(s, picked) { return snapshot(s).filter(task => !coreReply(task)).map(task => {
    const info = taskName(task.kind), count = ruleOf(s, task.kind).length;
    return `<option value="${esc(task.kind)}" ${task.kind === picked?.kind ? "selected" : ""}>${esc(info.label)} · ${esc(task.kind)}${count ? " · ${count} 条规则" : ""}</option>`;
  }).join(""); }
  function keywordPolicy(s) { const task = policyTask(s); if (!task) return '<p class="model-empty">当前没有可用的模型任务。</p>';
    const draft = s.drafts.get(task.kind), locked = s.loading || s.busy || s.denied || draft?.pending;
    const rules = ruleOf(s, task.kind), custom = !draft.inherited.replacement_rules;
    const inherited = task.fields.replacement_rules.inherited_value || "[]";
    return `<article class="model-pol-card"><header class="model-rule-title"><div><h3>关键词替换规则</h3>
        <p class="model-note">命中时使用目标模型提供方。</p></div>
        <div class="model-rule-actions"><button type="button" class="button small" data-rule-add ${locked ? "disabled" : ""}>+ 新增规则</button></div></header>
      <ul class="model-rules">${rules.length ? rules.map((rule, index) => ruleCard(s, task, rule, index)).join("") : '<li class="model-empty">这项任务还没有替换规则。</li>'}</ul>
      <p class="model-note">上级默认规则 ${parseRules(inherited).length} 条 · 当前任务 ${rules.length} 条；${custom ? "已单独设置" : "跟随上级"}。</p>
      <details class="model-rule-json"><summary>JSON 源码</summary>
        <textarea rows="7" spellcheck="false" data-model-pol-json ${locked ? "disabled" : ""}>${esc(serializeRules(rules))}</textarea>
        <p class="model-note">失焦后提交；格式错误时保留原值。</p></details></article>`; }
  function peakPolicy(s) { const task = policyTask(s); if (!task) return '<p class="model-empty">当前没有可用的模型任务。</p>';
    const draft = s.drafts.get(task.kind), locked = s.loading || s.busy || s.denied || draft?.pending;
    const rules = ruleOf(s, task.kind), windows = rules.filter(rule => !ruleKeywordText(rule) && rule.time_window);
    const providers = available(s, task), alternative = s.peak.provider;
    const extra = providers.some(provider => provider.id === alternative) ? "" : alternative ? `<option value="${esc(alternative)}" selected>${esc(alternative)} · 当前不可用</option>` : "";
    return `<article class="model-pol-card">
      <div class="model-phead"><div class="model-grow"><span class="model-eb">成本路由 · 可选</span><h3>高价时段替代</h3></div>
        <div class="model-st2"><span class="model-tag ${s.peak.on ? (s.peak.provider ? "a" : "a") : "n"}">${s.peak.on ? (s.peak.provider ? "已启用" : "等待选择替代模型提供方") : "未启用"}</span>
          <label class="model-switch"><input type="checkbox" data-peak-switch ${s.peak.on ? "checked" : ""} ${locked ? "disabled" : ""} aria-label="启用高价时段替代"><i></i></label></div></div>
      ${s.peak.on ? `<div class="model-pol-body">
        <label class="model-fd"><span class="model-cap">高价时段替代模型提供方 <em>时段内整体改走这里</em></span>
          <select data-peak-provider ${locked ? "disabled" : ""}><option value="">请选择替代模型提供方</option>${providers.map(provider => `<option value="${esc(provider.id)}" ${provider.id === alternative ? "selected" : ""}>${esc(provider.id)}</option>`).join("")}${extra}</select></label>
        <div class="model-fd"><span class="model-cap">高价时段 <em>点击格子切换 · 每行一个区间 · 支持跨午夜</em></span>
          <div class="model-band" data-peak-band>${Array.from({ length: 24 }, (_, hour) => {
            const on = peakWindows(s.peak).has(hour);
            return `<button type="button" class="model-band-cell${on ? " on" : ""}${hour % 3 === 0 ? " tick" : ""}" data-peak-hour="${hour}" title="${hour}:00" aria-label="${hour}:00–${(hour + 1) % 24}:00" aria-pressed="${on}" ${locked ? "disabled" : ""}>${hour % 3 === 0 ? hour : ""}</button>`; }).join("")}</div>
          <div class="model-hrs">${Array.from({ length: 24 }, (_, hour) => `<span>${hour % 3 === 0 ? hour : ""}</span>`).join("")}</div>
          <div class="model-hrl">
            <span class="lg"><i class="hi"></i>高价时段</span><span class="lg"><i class="lo"></i>默认路由</span>
            <span class="model-grow"></span>
            <button type="button" class="button small" data-peak-preset ${locked ? "disabled" : ""}>默认 09–12 / 14–18</button>
            <button type="button" class="button small" data-peak-clear ${locked ? "disabled" : ""}>清空时段</button></div>
        </div>
        <div class="model-kwc">
          <span class="model-cap">开始</span><input class="model-num" data-peak-start value="${esc(s.peak.start)}" maxlength="5" placeholder="09:00" ${locked ? "disabled" : ""}>
          <span class="model-cap">结束</span><input class="model-num" data-peak-end value="${esc(s.peak.end)}" maxlength="5" placeholder="18:00" ${locked ? "disabled" : ""}>
          <span class="model-grow"></span>
          <button type="button" class="button small primary" data-peak-apply ${locked ? "disabled" : ""}>写入这项任务的时段规则</button></div>
        <div class="model-runstat">
          <div class="rs"><span>当前选择</span><b>${esc(s.peak.windows.replaceAll(String.fromCharCode(10), " · ") || "无")}</b></div>
          <div class="rs"><span>已配置时段规则</span><b>${windows.length} 条</b></div>
          <div class="rs"><span>写入行为</span><b>替换本任务原有的纯时段规则</b></div>
        </div></div>` : ""}
      </article>`; }
  function refusalPolicy(s) { const task = policyTask(s); if (!task) return '<p class="model-empty">当前没有可用的模型任务。</p>';
    const draft = s.drafts.get(task.kind), locked = s.loading || s.busy || s.denied || draft?.pending;
    const on = draft.inherited.fallback_on_refusal ? !!task.fields.fallback_on_refusal.inherited_value : !!draft.values.fallback_on_refusal;
    const fallback = effective(task, draft, "fallback"), providers = available(s, task);
    const termsText = effective(task, draft, "refusal_terms") || "", terms = refusalTerms(termsText);
    return `<article class="model-pol-card">
      <div class="model-phead"><div class="model-grow"><span class="model-eb">安全兜底</span><h3>拒答替代</h3></div>
        <div class="model-st2"><span class="model-tag ${on ? (fallback ? "g" : "a") : "n"}">${on ? (fallback ? "已启用 · 可重试一次" : "已开启 · 没有备用链路") : "未启用"}</span>
          <label class="model-switch"><input type="checkbox" data-refusal-switch ${on ? "checked" : ""} ${locked ? "disabled" : ""}><i></i></label></div></div>
      ${on ? `<div class="model-pol-body"><div class="model-rf2">
        <label class="model-fd model-refusal-provider"><span class="model-cap">拒答替代模型</span>
          <select data-refusal-provider aria-label="拒答替代模型" ${locked ? "disabled" : ""}><option value="">不使用备用模型</option>${providers.map(provider => `<option value="${esc(provider.id)}" ${provider.id === fallback ? "selected" : ""}>${esc(providerLabel(provider))}</option>`).join("")}${fallback && !providers.some(provider => provider.id === fallback) ? `<option value="${esc(fallback)}" selected>${esc(fallback)} · 当前不可用</option>` : ""}</select></label>
        <label class="model-fd"><span class="model-cap">拒答匹配词 <em>每行或用分号分隔</em></span>
          <textarea rows="6" maxlength="4096" spellcheck="false" data-refusal-terms ${locked ? "disabled" : ""}>${esc(termsText)}</textarea></label>
        <div class="model-fd"><span class="model-cap">已识别 <b data-refusal-count>${terms.length}</b> 条</span>
          <div class="model-rfchips" data-refusal-chips>${terms.map(term => `<span class="model-kchip">${esc(term)}</span>`).join("")}</div></div>
      </div></div>` : ""}
      <div class="model-policy-foot">命中后使用备用模型重试一次</div></article>`; }
  function replacementPolicy(s) { const task = policyTask(s), draft = task && s.drafts.get(task.kind);
    const rules = task ? ruleOf(s, task.kind) : [];
    const modes = [["kw", "关键词换模"], ["peak", "高价时段替代"], ["refuse", "拒答替代"]];
    const scope = task && draft ? effective(task, draft, "replacement_scope") : "all";
    const locked = !task || s.busy || s.loading || s.denied || draft?.pending;
    const scopes = [["plugin", "仅插件调用"], ["conversation", "仅普通对话模型"], ["all", "全部生效"]];
    const panel = s.pm === "peak" ? peakPolicy(s) : s.pm === "refuse" ? refusalPolicy(s) : keywordPolicy(s);
    const status = s.loading || (s.notice && s.notice !== "已读取当前任务配置。") ? notice(s) : "";
    return `<div class="model-subpane model-policy">
      <div class="model-policy-bar">
        <div class="model-seg model-policy-modes" role="group" aria-label="替换策略">${modes.map(([id, label]) =>
          `<button type="button" data-model-pm="${id}" aria-pressed="${s.pm === id}" class="${s.pm === id ? "on" : ""}">${label}</button>`).join("")}</div>
        <label class="model-policy-task"><span>任务</span><select data-model-pol-task aria-label="当前编辑任务" ${s.loading || s.busy ? "disabled" : ""}>${policyTaskOptions(s, task)}</select></label>
        <span class="model-grow"></span><span class="model-policy-scope-label">作用范围</span>
        <div class="model-seg model-policy-scope" role="group" aria-label="替换策略作用范围">${scopes.map(([id, label]) =>
          `<button type="button" data-model-scope="${id}" aria-pressed="${scope === id}" class="${scope === id ? "on" : ""}" ${locked ? "disabled" : ""}>${label}</button>`).join("")}</div>
        ${task ? `<div class="model-pol-actions"><button type="button" class="button primary" data-policy-save="${esc(task.kind)}" ${s.busy || s.loading || s.denied || draft.stale || !dirty(task, draft) ? "disabled" : ""}>保存</button>
          <button type="button" class="button" data-policy-discard="${esc(task.kind)}" ${s.busy || s.loading || draft.pending ? "disabled" : ""}>放弃草稿</button></div>` : ""}
      </div>
      ${status}
      <div class="model-policy-editor">${task ? panel : '<div class="model-empty">当前没有可用的模型任务。</div>'}</div>
    </div>`; }
  function cfgPane(s) { const tasks = snapshot(s);
    const own = tasks.filter(t => taskFields(t).some(nm => t.fields[nm]?.explicit)).length;
    const note = s.status ? `生效中 · ${own} / ${tasks.length} 项任务单独设置了模型` : "";
    const cmSeg = [["quick", "快速配置"], ["precise", "精确配置"]];
    const seg = `<div class="model-seg" role="group" aria-label="配置模式">${cmSeg.map(([id, label]) => `<button type="button" data-model-cm="${id}" aria-pressed="${s.cm === id}" class="${s.cm === id ? "on" : ""}">${label}</button>`).join("")}</div>`;
    const body2 = s.cm === "quick" ? quickConfig(s) : `${toolbar(s)}<div class="model-queue-slot">${queue(s)}</div>${body(s)}`;
    return `<div class="model-subpane"><div class="model-note cfg">${seg}</div>${notice(s)}${s.cm === "quick" ? "" : overview(s)}${body2}</div>`; }
  /* ---------- replacement policy: mutations ---------- */
  const ruleTarget = ref => { const [kind, index] = String(ref || "").split(":"); return { kind, index: Number(index) }; };
  /* Every rule edit funnels through here so the JSON the gateway reads stays canonical. */
  function editRules(s, kind, mutate) { const task = taskOf(s, kind), draft = s.drafts.get(kind);
    if (!task || coreReply(task) || !draft || draft.pending || s.busy || s.loading) return;
    const rules = ruleOf(s, kind).slice(); if (mutate(rules) === false) return;
    draft.values.replacement_rules = serializeRules(rules); draft.inherited.replacement_rules = false; draft.touched = true;
    s.bad = false; s.notice = `${taskName(kind).label} 的替换规则已改动，保存本任务后生效。`; render(); }
  function rulePatch(rule, patch) { Object.assign(rule, patch); }
  function ruleInput(s, event) { const t = event.target;
    if (t.matches("[data-model-pol-search]")) { s.rule.query = t.value; render();
      const next = root.querySelector("[data-model-pol-search]");
      if (next) { next.focus(); next.setSelectionRange(next.value.length, next.value.length); } return; }
    if (t.matches("[data-model-pol-json]")) { if (event.type !== "change") return; const task = policyTask(s); if (!task) return;
      const parsed = parseRules(t.value);
      try { JSON.parse(t.value || "[]"); } catch { s.bad = true; s.notice = "JSON 解析失败，已保留原有规则。"; render(); return; }
      editRules(s, task.kind, rules => { rules.length = 0; parsed.forEach(rule => rules.push(rule)); }); return; }
    if (t.matches("[data-peak-provider]")) { s.peak.provider = t.value; render(); return; }
    if (t.matches("[data-refusal-provider]")) { const task = policyTask(s), draft = task && s.drafts.get(task.kind);
      if (!task || !draft || draft.pending || s.busy || s.loading || s.denied) return;
      draft.values.fallback = t.value; draft.inherited.fallback = false;
      const adjusted = fitLimit(s, task, draft); draft.touched = true; s.bad = false;
      s.notice = adjusted ? `${taskName(task.kind).label} 的输出上限已调整为 ${draft.values.max_output_tokens}。`
        : `${taskName(task.kind).label} 的拒答备用模型已改动，保存本任务后生效。`;
      render(); return; }
    if (t.matches("[data-peak-start]") || t.matches("[data-peak-end]")) { s.peak[t.matches("[data-peak-start]") ? "start" : "end"] = t.value;
      if (event.type === "change") { if (timeValid(s.peak.start) && timeValid(s.peak.end) && s.peak.start !== s.peak.end)
        s.peak.windows = `${s.peak.start}-${s.peak.end}`; render(); } return; }
    if (t.matches("[data-refusal-terms]")) { const task = policyTask(s), draft = task && s.drafts.get(task.kind);
      if (!task || !draft || draft.pending || s.busy || s.loading || s.denied) return;
      draft.values.refusal_terms = t.value; draft.inherited.refusal_terms = false; draft.touched = true;
      s.bad = false; s.notice = `${taskName(task.kind).label} 的拒答匹配词已改动，保存本任务后生效。`;
      const terms = refusalTerms(t.value), count = root.querySelector("[data-refusal-count]"), chips = root.querySelector("[data-refusal-chips]");
      if (count) count.textContent = String(terms.length);
      if (chips) chips.innerHTML = terms.length ? terms.map(term => `<span class="model-kchip">${esc(term)}</span>`).join("")
        : '<span class="model-empty">未填写词条 · 仍识别模型提供方的明确拒答标记</span>';
      const save = [...root.querySelectorAll("[data-policy-save]")].find(button => button.dataset.policySave === task.kind);
      if (save) save.disabled = draft.stale || draft.pending || !dirty(task, draft);
      if (event.type === "change") render(); return; }
    const idRef = t.dataset.ruleId, kwRef = t.dataset.ruleKeywords, matchRef = t.dataset.ruleMatch, logicRef = t.dataset.ruleLogic,
      prioRef = t.dataset.rulePriority, provRef = t.dataset.ruleProvider, modelRef = t.dataset.ruleModel,
      startRef = t.dataset.ruleStart, endRef = t.dataset.ruleEnd, zoneRef = t.dataset.ruleZone;
    const ref = idRef || kwRef || matchRef || logicRef || prioRef || provRef || modelRef || startRef || endRef || zoneRef;
    if (!ref) return;
    const { kind, index } = ruleTarget(ref);
    const name = t.matches("[data-rule-id]") ? "id" : t.matches("[data-rule-keywords]") ? "keywords" : t.matches("[data-rule-match]") ? "match_mode"
      : t.matches("[data-rule-logic]") ? "keyword_logic" : t.matches("[data-rule-priority]") ? "priority" : t.matches("[data-rule-provider]") ? "provider_id"
      : t.matches("[data-rule-model]") ? "model" : "window";
    const value = name === "keywords" ? keywordChips(t.value) : name === "priority" ? (Number.isFinite(t.valueAsNumber) ? t.valueAsNumber : 0) : t.value;
    /* Free-text fields must not re-render on every keystroke or the caret is lost. */
    const typing = (name === "id" || name === "keywords" || name === "model") && event.type === "input";
    if (typing) { const task = taskOf(s, kind), draft = s.drafts.get(kind);
      if (!task || !draft || draft.pending) return;
      const rules = ruleOf(s, kind).slice(); if (!rules[index]) return;
      rules[index][name] = value;
      draft.values.replacement_rules = serializeRules(rules); draft.inherited.replacement_rules = false; draft.touched = true;
      return; }
    editRules(s, kind, rules => { const rule = rules[index]; if (!rule) return false;
      if (name !== "window") { rulePatch(rule, { [name]: value }); return; }
      if (!rule.time_window || typeof rule.time_window !== "object") rule.time_window = { timezone: "Asia/Shanghai", start: "09:00", end: "18:00", weekdays: [0, 1, 2, 3, 4, 5, 6] };
      if (t.matches("[data-rule-start]")) rule.time_window.start = value;
      else if (t.matches("[data-rule-end]")) rule.time_window.end = value;
      else rule.time_window.timezone = value || "Asia/Shanghai"; }); }
  function ruleClick(s, event) { const t = event.target;
    const save = t.closest("[data-policy-save]"); if (save) { saveTasks(s, [save.dataset.policySave]); return; }
    const discardButton = t.closest("[data-policy-discard]"); if (discardButton) { discard(s, [discardButton.dataset.policyDiscard]); return; }
    const add = t.closest("[data-rule-add]"); if (add) { const task = policyTask(s); if (!task) return;
      const providers = available(s, task), rules = ruleOf(s, task.kind);
      const used = new Set(rules.map(rule => rule.id));
      let seed = 1; while (used.has("rule-" + seed)) seed++;
      editRules(s, task.kind, list => list.push({ ...RULE_SEED, id: "rule-" + seed, provider_id: providers[0]?.id || "" })); return; }
    const del = t.closest("[data-rule-del]"); if (del) { const { kind, index } = ruleTarget(del.dataset.ruleDel);
      editRules(s, kind, rules => { if (!rules[index]) return false; rules.splice(index, 1); }); return; }
    const hour = t.closest("[data-peak-hour]"); if (hour) { const cells = peakWindows(s.peak);
      const value = Number(hour.dataset.peakHour); cells.has(value) ? cells.delete(value) : cells.add(value);
      s.peak.windows = formatWindows(cells); const first = peakSegments(s.peak.windows)[0];
      s.peak.start = first?.[0] || ""; s.peak.end = first?.[1] || "";
      render(); return; }
    const preset = t.closest("[data-peak-preset]"); if (preset) { s.peak.windows = "09:00-12:00\n14:00-18:00"; s.peak.start = "09:00"; s.peak.end = "12:00"; render(); return; }
    const clear = t.closest("[data-peak-clear]"); if (clear) { s.peak.windows = ""; s.peak.start = ""; s.peak.end = ""; render(); return; }
    const apply = t.closest("[data-peak-apply]"); if (apply) { const task = policyTask(s); if (!task) return;
      if (s.peak.on && !s.peak.provider) { s.bad = true; s.notice = "先选择时段替代的模型提供方。"; render(); return; }
      const segments = peakSegments(s.peak.windows);
      if (s.peak.on && !segments.length) { s.bad = true; s.notice = "先设置至少一个有效时段。"; render(); return; }
      editRules(s, task.kind, rules => { for (let index = rules.length - 1; index >= 0; index--)
        if (!ruleKeywordText(rules[index]) && rules[index].time_window) rules.splice(index, 1);
        if (s.peak.on) segments.forEach(([start, end]) => rules.push({ ...RULE_SEED,
          id: "peak-" + start.replace(":", "") + "-" + end.replace(":", ""), provider_id: s.peak.provider,
          time_window: { timezone: "Asia/Shanghai", start, end, weekdays: [0, 1, 2, 3, 4, 5, 6] } })); }); return; }
    const scope = t.closest("[data-model-scope]"); if (scope) { const task = policyTask(s), draft = task && s.drafts.get(task.kind);
      if (!draft || draft.pending || s.busy || s.loading || s.denied) return;
      draft.values.replacement_scope = scope.dataset.modelScope; draft.inherited.replacement_scope = false; draft.touched = true;
      s.notice = `${taskName(task.kind).label} 的替换范围已改动，保存本任务后生效。`; s.bad = false; render(); return; } }
  function ruleChange(s, event) { const t = event.target;
    if (t.matches("[data-model-pol-task]")) { s.pol = t.value; render(); return; }
    if (t.matches("[data-peak-switch]")) { s.peak.on = t.checked; render(); return; }
    if (t.matches("[data-refusal-switch]")) { const task = policyTask(s); const draft = task && s.drafts.get(task.kind);
      if (!task || !draft || draft.pending || s.busy || s.loading) return;
      draft.values.fallback_on_refusal = t.checked; draft.inherited.fallback_on_refusal = false; draft.touched = true;
      s.bad = false; s.notice = t.checked ? `${taskName(task.kind).label} 已开启拒答替代，保存本任务后生效。` : `${taskName(task.kind).label} 已关闭拒答替代，保存本任务后生效。`;
      render(); return; }
    const enabled = t.dataset.ruleEnabled, window = t.dataset.ruleWindow;
    if (enabled) { const { kind, index } = ruleTarget(enabled);
      editRules(s, kind, rules => { const rule = rules[index]; if (!rule) return false; rule.enabled = t.checked; }); return; }
    if (window) { const { kind, index } = ruleTarget(window);
      editRules(s, kind, rules => { const rule = rules[index]; if (!rule) return false;
        if (t.checked) rule.time_window = { timezone: "Asia/Shanghai", start: "09:00", end: "18:00", weekdays: [0, 1, 2, 3, 4, 5, 6] };
        else delete rule.time_window; }); return; }
    ruleInput(s, event); }
  function fnPane(s) { const sub = s.sub === "map" ? situationMap(s) : s.sub === "pol" ? replacementPolicy(s) : cfgPane(s);
    return `${topbar(s)}${subtabBar(s)}${sub}`; }
  function pane(s) { if (s.tab === "voice") return voicePane(s); if (s.tab === "image") return imagePane(s); if (s.tab === "search") return searchPane(s); if (s.tab === "laya") return layaPane(s); return fnPane(s); }
  function drawer(s) { const id = s.drawer; if (!id) return "";
    const provider = s.status?.providers.find(p => p.id === id); if (!provider) return "";
    const tasks = snapshot(s), asMain = tasks.filter(t => providerFor(t, s.drafts.get(t.kind)) === id);
    const asFb = tasks.filter(t => effective(t, s.drafts.get(t.kind), "fallback") === id);
    return `<div class="model-mask on" data-drawer-close></div><aside class="model-drawer on" role="dialog" aria-label="模型提供方 ${esc(id)}">
      <div class="dh"><div><h3>${esc(id)}</h3><p>${esc(provider.capabilities.map(capabilityLabel).join(" · ") || "无能力声明")}</p></div>
        <button type="button" class="x" data-drawer-close aria-label="关闭">×</button></div>
      <dl class="model-kv"><dt>单次输出上限</dt><dd>${provider.max_output_tokens}</dd>
        <dt>承担任务</dt><dd>${asMain.length} 项主路由 · ${asFb.length} 项备用</dd></dl>
      <div class="model-sec"><h4>作为主模型 · ${asMain.length} 项</h4>${asMain.map(t => `<div class="li">${esc(taskName(t.kind).label)}<span>${esc(taskName(t.kind).description)}</span></div>`).join("") || '<span class="model-mono">无</span>'}</div>
      <div class="model-sec"><h4>作为备用 · ${asFb.length} 项</h4>${asFb.map(t => `<div class="li">${esc(taskName(t.kind).label)}<span>${esc(taskName(t.kind).description)}</span></div>`).join("") || '<span class="model-mono">无</span>'}</div>
      <div class="model-sec"><button type="button" class="button primary" style="width:100%" data-drawer-goto="${esc(id)}">在精确配置中调整</button></div></aside>`; }
  function render() { if (!root || !state) return; const s = state;
    root.innerHTML = `<div class="model-workspace">${s.tab === "fn" ? semanticBanner() : ""}${tabBar(s)}${(s.bad || s.denied) ? notice(s) : ""}${pane(s)}${drawer(s)}</div>`;
    window.lucide?.createIcons?.({attrs: {"aria-hidden": "true"}}); }
  function input(event) { const s = state; if (!s || !root?.isConnected) return;
    if (s.tab === "voice") { voiceInput(s, event); return; }
    if (s.tab === "image") { imageInput(s, event); return; }
    if (s.tab === "search") { searchInput(s, event); return; }
    if (s.tab === "laya") { layaInput(s, event); return; }
    fnInput(event); }
  function click(event) { const s = state; if (!s || !root?.isConnected) return;
    const tab = event.target.closest("[data-model-tab]");
    if (tab) { const id = tab.dataset.modelTab; if (s.tab !== id) { s.tab = id;
        if (id === "voice" && !state.voice.loaded) voiceRefresh(s); else if (id === "image" && !state.image.loaded) imageRefresh(s); else if (id === "search" && !state.search.loaded) searchRefresh(s); else if (id === "laya" && !state.laya.loaded) layaRefresh(s);
        render(); } return; }
    if (s.tab === "voice") { voiceClick(s, event); return; }
    if (s.tab === "image") { imageClick(s, event); return; }
    if (s.tab === "search") { searchClick(s, event); return; }
    if (s.tab === "laya") { layaClick(s, event); return; }
    fnClick(event); }
  function providerHover(event) {
    const s = state; if (!s || !root?.isConnected) return;
    const pill = event.target.closest?.("[data-provider-pill]");
    if (event.type === "pointerover") {
      if (!pill) return;
      s.hoverProvider = pill.dataset.providerPill;
    } else {
      if (!pill || s.hoverProvider !== pill.dataset.providerPill) return;
      const next = event.relatedTarget?.closest?.("[data-provider-pill]");
      if (next?.dataset.providerPill === s.hoverProvider) return;
      s.hoverProvider = "";
    }
    root.querySelectorAll("[data-provider-pill]").forEach(item =>
      item.classList.toggle("is-hovered", !!s.hoverProvider && item.dataset.providerPill === s.hoverProvider));
  }
  /* ---------- lifecycle ---------- */
  function unmount() { version++; if (root) { root.removeEventListener("input", input); root.removeEventListener("change", input); root.removeEventListener("click", click); root.removeEventListener("pointerover", providerHover); root.removeEventListener("pointerout", providerHover); }
    if (state) { state.loading = false; state.busy = false; }
    root = null; state = null; path = key = null; }
  function mount(element, options = {}) { unmount(); root = element; if (!root) return; const product = window.CompanionProduct;
    /* 这里原先先判断 hosted()，桥还没注入时直接写「从 AstrBot 管理界面打开陪伴小屋后…」。
       桥的注入晚于脚本执行是正常时序，所以那句话在正经的拓展页里也出现过。
       现在改成读不到就说读不到，并给一个重新读取的入口——刷新之后桥通常就在了。 */
    const identity = product.identity();
    const endpoint = Object.hasOwn(options, "endpoint") ? options.endpoint : product.role()?.model_api;
    if (!product.hosted() || typeof endpoint !== "string" || !endpoint.startsWith(PREFIX) || /[?#%\\:]/.test(endpoint) || endpoint.split("/").slice(1).some(p => !p || p === "." || p === "..") || !identity.operator_id) {
      const hint = product.workspaceHint?.() || "";
      const message = product.hosted() ? (hint || "当前对象没有模型配置工作区。请选择已关联角色的私聊窗口。")
        : "角色与会话目录暂未读到，请稍后重新读取。";
      root.innerHTML = `<div class="model-empty">${message}<div class="dialog-actions"><button class="button small" type="button" data-workspace-retry>重新读取</button><button class="button small" type="button" data-go="manage" data-manage="host">账号与会话</button></div></div>`; return;
    }
    /* path 是每一次请求的基址（model_api + /configure、/voice、/tts/providers…），
       也是 localStorage 键的一部分。桥要插件相对路径，所以切掉插件目录名。 */
    path = endpoint.slice(PREFIX.length); key = "companion-model-edit.v2:" + JSON.stringify([identity.installation_id, identity.operator_id, path]);
    state = states.get(key) || { status: null, drafts: new Map(), selection: [], expanded: null, layer: "role", view: "tasks", custom: null, lane: "all", providerFilter: null, query: "", sub: "map", cm: "precise", av: "slot", af: "all", pm: "kw", slotDraft: {}, providerTests: {}, testing: new Set(),
      drawer: null, expOpen: {}, pol: null, rule: { query: "", picked: [], report: null, testing: false }, peak: { on: true, provider: "", start: "09:00", end: "12:00", windows: "09:00-12:00\n14:00-18:00" },
      tab: "fn",
      voice: { data: null, draft: null, loaded: false, loading: false, busy: false, testing: false, bad: false, notice: "", ui: { lang: "ja" },
        editorOpen: false, catalog: null, catalogLoading: false, editorId: null, editorValues: {}, creating: false, ttsBusy: false, ttsTesting: false, ttsTestResults: {}, createDraft: { type: "", id: "" } },
      image: { data: null, draft: null, loaded: false, loading: false, busy: false, testing: null, endpointTesting: null, endpointTests: {}, endpointTestKeys: {}, bad: false, notice: "",
        view: "generate", config: null, configBase: null, configDraft: null, configText: null, configTextBase: null,
        configLoading: false, configSaving: false, configBad: false, configNotice: "",
      },
      laya: { data: null, draft: null, logs: [], loaded: false, loading: false, busy: false, stale: false, bad: false, notice: "", test: null, preview: null, previewText: "" },
      search: { data: null, draft: null, loaded: false, loading: false, busy: false, stale: false, bad: false, notice: "" },
      loading: false, busy: false, bad: false, denied: false, notice: "" };
    state.layer ||= "role"; state.providerTests ||= {}; state.testing ||= new Set();
    /* 展开状态按对象保存：刷新、切任务、切页回来都保持上次展开的分组，
       不再每次回到“全收起”或“全展开”。 */
    state.expOpen = { ...restoreExpanded(key), ...(state.expOpen || {}) }; state.voice.lastTest ||= null; state.voice.editorValues ||= {}; state.voice.ttsTestResults ||= {}; state.voice.ttsTesting ||= false; state.voice.createDraft ||= { type: "", id: "" }; state.image.endpointTests ||= {}; state.image.endpointTestKeys ||= {}; state.image.endpointTesting ||= null; state.configDraft ||= null; state.peak ||= { on: true, provider: "", start: "09:00", end: "12:00" }; state.testing ||= new Set(); state.peak.windows ||= "09:00-12:00\n14:00-18:00"; state.laya ||= { data: null, draft: null, loaded: false, loading: false, busy: false, stale: false, bad: false, notice: "", test: null, preview: null, previewText: "" }; state.search ||= { data: null, draft: null, loaded: false, loading: false, busy: false, stale: false, bad: false, notice: "" }; state.hoverProvider = "";
    states.set(key, state);
    state.sub = ["map", "cfg", "pol"].includes(options.sub) ? options.sub : "map";
    root.addEventListener("input", input); root.addEventListener("change", input); root.addEventListener("click", click); root.addEventListener("pointerover", providerHover); root.addEventListener("pointerout", providerHover); render();
    if (state.tab && state.tab !== "fn") { if (state.tab === "voice" && !state.voice.loaded) voiceRefresh(state); else if (state.tab === "image" && !state.image.loaded) imageRefresh(state); else if (state.tab === "search" && !state.search.loaded) searchRefresh(state); else if (state.tab === "laya" && !state.laya.loaded) layaRefresh(state); }
    else { refresh().catch(() => {}); } }
  /* Only the mounted scope can block navigation. Retained drafts belonging to
     another role or a closed page must not trigger a warning on every route. */
  function imageChangedFields(i) {
    const changed = [], same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
    const labels = {enabled:'启用开关', backend:'后端模式', base_url:'API 地址', model:'模型 ID', name:'自定义名称',
      platform:'协议', timeout_seconds:'超时秒数', api_key:'API Key', clear_api_key:'清除已保存密钥',
      custom_headers:'自定义请求头', clear_custom_headers:'清除已保存请求头', quality:'图片质量',
      response_format:'返回格式',
      size_label:'尺寸标签', width:'宽度', height:'高度', max_reference_images:'最多参考图',
      comfyui:'ComfyUI', sd_webui:'SD WebUI', network:'下载网络', download_proxy:'下载代理',
      clear_download_proxy:'清除已保存代理', use_environment_proxy:'使用系统代理',
      parameters:'生图参数', operation_routes:'调用顺序', generate:'文生图', edit:'改图',
      mask_edit:'蒙版局部改图', workflows:'工作流', legacy_options:'兼容参数'};
    const compare = (base, draft, prefix) => {
      for (const name of new Set([...Object.keys(base || {}), ...Object.keys(draft || {})])) {
        if (same(base?.[name], draft?.[name])) continue;
        const label = `${prefix} · ${labels[name] || imageSchema?.sd_webui?.fields?.[name]?.label || name}`;
        if (['network','comfyui','sd_webui','parameters','operation_routes','generate','edit'].includes(name)
            && !Array.isArray(base?.[name]) && !Array.isArray(draft?.[name])) compare(base?.[name], draft?.[name], label);
        else changed.push(label);
      }
    };
    const {endpoints: before = [], ...base} = i.configBase;
    const {endpoints: after = [], ...draft} = i.configDraft;
    compare(base, draft, '生图模型');
    for (const id of new Set([...before.map(item => item.id), ...after.map(item => item.id)])) {
      const old = before.find(item => item.id === id), value = after.find(item => item.id === id);
      const label = `生图模型 · ${imageEndpointLabel(value || old, '图片模型')}`;
      if (!old || !value) changed.push(`${label}（${value ? '新增' : '删除'}）`);
      else compare(old, value, label);
    }
    const textLabels = {'sd.generate':'SD WebUI · 文生图高级参数', 'sd.edit':'SD WebUI · 改图高级参数',
      'comfy.generate':'ComfyUI · 文生图工作流', 'comfy.edit':'ComfyUI · 改图工作流',
      'comfy.mask_edit':'ComfyUI · 蒙版局部改图工作流', 'legacy.options':'兼容参数'};
    for (const name of new Set([...Object.keys(i.configTextBase || {}), ...Object.keys(i.configText || {})])) {
      if (same(i.configTextBase?.[name], i.configText?.[name])) continue;
      const endpoint = name.startsWith('headers.') && [...after, ...before].find(item => item.id === name.slice(8));
      if (endpoint && !after.some(item => item.id === endpoint.id)) continue;
      changed.push(`生图模型 · ${endpoint ? `${imageEndpointLabel(endpoint, '图片模型')} · 自定义请求头` : textLabels[name] || name}`);
    }
    return [...new Set(changed)];
  }
  function changedFields() {
    const s = state;
    if (!root || !s) return [];
    const changed = [];
    for (const task of snapshot(s)) {
      const draft = s.drafts.get(task.kind);
      if (!draft) continue;
      const patch = patchFor(task, draft);
      for (const field of new Set([...Object.keys(patch.changes), ...patch.reset]))
        changed.push(`${taskName(task.kind).label} · ${fieldLabel[field] || field}`);
      if (draft.pending) changed.push(`${taskName(task.kind).label} · 保存结果待核对`);
    }
    if (s.voice && voiceDirty(s.voice)) {
      const labels = {voice_timeout_seconds:'语音合成超时'};
      for (const lang of s.voice.data?.languages || []) labels[`voice_${lang.k}_provider_id`] = `${lang.label || lang.k}语音模型`;
      changed.push(...Object.keys(voiceChanges(s.voice)).map(name => labels[name] || name));
    }
    if (s.image && imageDirty(s.image)) for (const [action, label] of Object.entries(IMAGE_LABELS)) {
      if (s.image.draft[action] !== s.image.data.enabled[action]) changed.push(`${label}能力开关`);
    }
    if (s.image && imageConfigDirty(s.image)) changed.push(...imageChangedFields(s.image));
    if (s.laya && layaDirty(s.laya)) {
      const labels = {enabled:'启用决策', mode:'运行方式', endpoint:'服务地址', local_model_path:'本地模型路径',
        remote_model:'判断模型名', api_key:'API Key', timeout_seconds:'请求超时', followup_confidence_threshold:'连续接话置信阈值'};
      for (const name of Object.keys(layaChanges(s.laya))) {
        if (name === 'decision_fields') changed.push(...LAYA_DECISION_FIELDS.filter(([key]) =>
          s.laya.draft.decision_fields[key] !== (s.laya.data.decision_fields?.[key] !== false)).map(([, label]) => `Laya · ${label}`));
        else changed.push(`Laya · ${labels[name] || name}`);
      }
    }
    if (s.search && searchDirty(s.search)) changed.push('联网搜索凭据来源');
    return changed;
  }
  function isDirty() {
    return changedFields().length > 0;
  }
  async function saveBeforeLeave() {
    const s = state;
    if (!s || !root) return true;
    if (s.busy || s.loading || s.voice?.busy || s.image?.busy || s.image?.configSaving || s.laya?.busy || s.search?.busy)
      return {error:'模型配置正在处理，请稍后保存。'};
    if ([...s.drafts.values()].some(draft => draft.pending) || s.voice?.pending)
      return {error:'上次保存结果待核对，请回到模型配置中核对原请求。'};
    const kinds = snapshot(s).filter(task => {const draft = s.drafts.get(task.kind); return draft && dirty(task, draft);}).map(task => task.kind);
    if (kinds.length) {
      await saveTasks(s, kinds);
      if (kinds.some(kind => dirty(taskOf(s, kind), s.drafts.get(kind)))) return {error:s.notice || '任务路由尚未保存。'};
    }
    for (const [needed, action, remaining, message] of [
      [s.voice && voiceDirty(s.voice), voiceSave, () => voiceDirty(s.voice) || s.voice.pending, () => s.voice.notice],
      [s.image && imageDirty(s.image), imageSave, () => imageDirty(s.image), () => s.image.notice],
      [s.image && imageConfigDirty(s.image), imageConfigSave, () => imageConfigDirty(s.image), () => s.image.configNotice],
      [s.laya && layaDirty(s.laya), layaSave, () => layaDirty(s.laya), () => s.laya.notice],
      [s.search && searchDirty(s.search), searchSave, () => searchDirty(s.search), () => s.search.notice],
    ]) {
      if (!needed) continue;
      await action(s);
      if (remaining())
        return {error:message() || '保存尚未确认，请回到配置中检查。'};
    }
    return !isDirty() ? true : {error:'还有配置未保存，改动仍保留。'};
  }
  function discardChanges() {
    if (state?.busy || state?.voice?.busy || state?.image?.busy || state?.image?.configSaving || state?.laya?.busy || state?.search?.busy)
      return {error:'正在保存，请等待结果后再切换。'};
    if (key) states.delete(key);
    unmount();
    return true;
  }
  const isBusy = () => Boolean(state && (state.busy || state.voice?.busy || state.voice?.ttsBusy
    || state.voice?.ttsTesting || state.image?.busy || state.image?.configSaving || state.laya?.busy || state.search?.busy));
  return { mount, unmount, isDirty, isBusy, changedFields, saveBeforeLeave, discardChanges };
})();
