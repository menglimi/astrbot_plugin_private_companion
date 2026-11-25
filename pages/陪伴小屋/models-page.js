/* Model configuration: task routing, voice, image generation and optional Laya decisions. */
"use strict";
window.CompanionModelsPage = (() => {
  const PREFIX = "/astrbot_plugin_private_companion/";
  const MODEL_TABS = new Set(["fn", "voice", "image", "search", "laya", "pricing", "quota"]);
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
      ["conversation.poke_reply", "戳一戳回复", "收到戳一戳时生成简短自然的回应"],
      ["conversation.context_image_caption", "图片摘要", "为已授权的历史图片提取可见内容"],
      ["conversation.image_narration", "当前图片转述", "为本轮已授权图片生成简短文字描述"],
      ["conversation.video_narration", "原生视频转述", "将原视频或压缩视频交给原生视频（如 Gemini）或 OpenAI 兼容视觉中转模型理解；支持主备降级与外置插件避让"]]],
    ["感知", [["screen.interpretation", "屏幕解读", "分析已授权截图与摄像头单帧，需要支持图片的模型"],
      ["conversation.image_review", "图片审核", "检查群聊成图的可见内容"],
      ["reality.text", "现实提醒文案", "现实提醒、唤醒回执和短回复的文字生成"]]],
    ["主动与整理", [["proactive.plan", "主动联系规划", "双阶段模式下判断时机并整理新回合切口"],
      ["proactive.compose", "主动联系写作", "按所选方式生成主动消息正文"],
      ["proactive.judge", "主动联系复核", "复核主动消息是否符合当前人格与来源"],
      ["memory.reflect", "事实整理", "从已授权素材整理认识"]]],
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
    model_defaults_invalid: "陪伴通用模型参数未通过校验，请重新选择模型提供方。",
    model_defaults_unavailable: "当前环境暂不支持在此修改陪伴通用模型，请到 AstrBot 插件配置页调整。",
    host_provider_unavailable: "所选 AstrBot 文本模型提供方当前不可用或暂不支持 V2。",
    account_config_save_failed: "全局默认模型写入失败，请检查插件配置文件权限。",
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
    model_image_output_unconfigured: "还没有可用的生图模型：先添加并启用一个在线模型，或配置 ComfyUI / SD WebUI，再打开总开关。",
    model_image_output_store_failed: "配置已读取，但写不回插件数据目录。请检查目录权限或被其它程序占用后重试。",
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
    laya_config_unreadable: "Laya 配置无法读取，请检查插件数据目录。", laya_endpoint_missing: "远程或 OpenAI 兼容网关模式需要填写服务地址。",
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
  const fail = (err, fallback) => errors[err.code] || errors[err.message]
    || window.CompanionPricingWorkbench?.ERRORS?.[err.code]
    || window.CompanionPricingWorkbench?.ERRORS?.[err.message]
    || window.CompanionQuotaWorkbench?.ERRORS?.[err.code]
    || window.CompanionQuotaWorkbench?.ERRORS?.[err.message]
    || err?.reason || fallback;
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
  let root = null, path = null, taskPath = null, key = null, state = null, version = 0, windowAvailable = true, onTabChange = null;
  let deferChangeRender = false, deferredRenderPending = false, pointerResetTimer = null;
  let wsShell = null, wsMain = null, wsDock = null, wsMask = null, wsDrawer = null, wsQuotaModalHost = null;
  let overlayCloseTimer = null, overlayWasOpen = false;
  let dockCloseTimer = null, dockWasOpen = false, dockHadReport = false, reportCloseTimer = null;
  let quickCloseTimer = null, advAnimTimer = null;
  const current = (ticket, s) => ticket === version && state === s && root?.isConnected;
  const taskOf = (s, kind) => s.status?.tasks.find(task => task.kind === kind) || null;
  const configSource = source => ({ default: "插件默认", host_conversation: "AstrBot 会话", installation: "全局默认", role: "角色默认", window: "会话设置" }[source] || "上级配置");
  const windowLabel = () => "仅此会话";
  function modelScopeHint(s, task = null, draft = null) {
    const workbench = window.CompanionWorkbench;
    const object = workbench.scopeObjectForEndpoint(PREFIX + taskPath);
    const layer = draft?.layer || s.layer;
    const lines = [`保存到：${workbench.saveScopeLabel(layer, object)}`];
    if (task && draft) {
      for (const name of fields) {
        const field = task.fields[name];
        if (field && draft.inherited[name]) lines.push(`${fieldLabel[name]}继承自：${configSource(field.inherited_source || "default")}`);
      }
    }
    lines.push("下一次任务生效");
    return workbench.scopeHintMarkup(lines.join("\n"));
  }
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
  const taskFields = task => coreReply(task) ? ["prompt", "replacement_rules"] : fields;
  /* A cleared field still has a row saved on this layer, so it counts as
     "configured here" exactly like an explicit one; the editor card already
     treats it that way (its draft is not inherited). */
  const ownsField = (task, name) => !!(task.fields[name]?.explicit
    || task.fields[name]?.cleared || task.fields[name]?.replaced);
  const fallbackFor = task => {
    if (coreReply(task)) return "";
    const field = task.fields.fallback;
    /* Recovery cleared a stored fallback that duplicated the primary; the task
       really has no fallback even though the inherited value is still visible.
       ``emptied`` covers rows inherited from another layer, which are not the
       editor's own choice but are just as unusable. */
    if (field?.cleared || field?.emptied) return "";
    if (field?.explicit) return field.value || "";
    return field?.value || field?.inherited_value || task.fields.provider?.inherited_value
      || task.fields.provider?.value || "";
  };
  const providerSupportsTask = (task, provider) => Boolean(provider) && (
    task.capabilities.every(cap => provider.capabilities.includes(cap))
    || (task.kind === "conversation.video_narration"
        && provider.supports_video_relay === true
        && provider.capabilities.includes("text"))
  );
  const available = (s, task) => s.status.providers.filter(p => providerSupportsTask(task, p));
  /* The inherited value is computed for the parent layer alone, so with a local
     primary override it can differ from the fallback this route actually runs.
     The projected value already reflects the real route; prefer it and fall back
     to the inherited value only when it is empty. */
  /* Output use of a field. An emptied fallback is not a fallback even though the
     parent view still carries a value for it, and for a settled primary the
     inherited value can be a route this layer will never try. */
  const effective = (task, draft, name) => {
    if (!draft.inherited[name]) return draft.values[name];
    if (name === "fallback") return fallbackShown(task, draft);
    return task.fields[name].inherited_value;
  };
  const providerFor = (task, draft) => coreReply(task) ? "" : effective(task, draft, "provider");
  /* The primary this draft would save: a local value, or the parent value when
     the user switches back to following it. */
  const draftedProvider = (task, draft) => draft.inherited.provider
    ? task.fields.provider.inherited_value : draft.values.provider;
  /* Recovery emptied an inherited fallback only because it collided with the
     primary this layer inherits. Moving the primary here — explicitly or by
     following the parent instead — makes it live again, so the draft has to
     account for it before saving. */
  const primaryMoved = (task, draft) =>
    draftedProvider(task, draft) !== task.fields.provider.value;
  /* Recovery emptied an inherited fallback because it collided with the primary
     this layer inherits. Moving the primary makes it live again as the profile's
     fallback default — unless that default is the new primary, which collides
     again. Returns null when the field is not an emptied inherited fallback. */
  const emptiedFallback = (task, draft, name) => {
    const field = task.fields[name];
    if (!field.emptied || field.cleared) return null;
    if (!primaryMoved(task, draft)) return "";
    const value = field.recovered_value || field.stored_value || "";
    return value === draftedProvider(task, draft) ? "" : value;
  };
  /* True when the draft resets a local fallback row but routing may still
     resolve it to something this page cannot name (stale parent row, changed
     primary). The host validates the save against the real route. */
  const fallbackUnknown = (task, draft) => {
    const field = task.fields.fallback;
    if (!draft.inherited.fallback) return false;
    if (field.explicit || field.replaced || field.cleared) {
      /* The draft decides the primary, not the saved snapshot: switching it to
         follow the parent is just as much a change as picking a value. */
      const primarySettledHere = !draft.inherited.provider;
      return !(primarySettledHere && field.parent_stored_usable);
    }
    if (primaryMoved(task, draft) && field.parent_stored && field.parent_stored_usable) {
      const parentPrimaryStable = ["role", "window"].includes(task.fields.provider.inherited_source);
      return draft.inherited.provider && !parentPrimaryStable;
    }
    return false;
  };
  /* The fallback the card should show and validate for this draft. A local row
     that is being reset gives way to the parent's *stored* value when that row
     is usable as-is; otherwise it is left unnamed rather than guessed. */
  const fallbackShown = (task, draft) => {
    if (!draft.inherited.fallback) return draft.values.fallback;
    const field = task.fields.fallback;
    const emptied = emptiedFallback(task, draft, "fallback");
    if (emptied !== null) return emptied;
    if (field.explicit || field.replaced || field.cleared) {
      return fallbackUnknown(task, draft) ? "" : field.parent_stored;
    }
    /* An inherited row can be repointed by recovery too; once this draft moves
       the primary, the parent's stored row is what runs again — unless that row
       is stale, in which case recovery keeps the value already projected. */
    if (primaryMoved(task, draft) && field.parent_stored) {
      if (!field.parent_stored_usable) return field.value || "";
      /* Following the parent re-runs recovery for the parent's primary. If that
         primary itself came back from recovery, the stored fallback may collide
         with it and be dropped; don't name a value the host will discard. */
      const parentPrimaryStable = ["role", "window"].includes(task.fields.provider.inherited_source);
      if (draft.inherited.provider && !parentPrimaryStable) return "";
      return field.parent_stored;
    }
    return field.value || field.inherited_value;
  };
  /* The routing ceiling of one draft: the strictest output capability among the
     providers this task can be sent to. Replacement-rule targets count too,
     because routing may pick one of them for the same task. */
  const limitProviders = (s, task, draft) => [...new Set([
      effective(task, draft, "provider"), effective(task, draft, "fallback"),
      ...ruleOf(s, task.kind).map(rule => String(rule.provider_id || ""))])]
    .filter(Boolean)
    .map(id => s.status.providers.find(provider => provider.id === id) || null);
  const providerLimit = provider => provider?.max_output_tokens || MAX_MODEL_OUTPUT_TOKENS;
  const limitFor = (s, task, draft) => Math.min(MAX_MODEL_OUTPUT_TOKENS,
    ...limitProviders(s, task, draft).map(providerLimit));
  /* The provider that actually caps this draft, so the hint can say where the
     number came from instead of naming every routed provider. */
  const limitOwner = (s, task, draft) => limitProviders(s, task, draft)
    .reduce((owner, provider) => !owner || providerLimit(provider) < providerLimit(owner) ? provider : owner, null);
  const limitExcess = (s, task, draft) => { const limit = limitFor(s, task, draft);
    const value = effective(task, draft, "max_output_tokens");
    return value > limit ? {value, limit, owner: limitOwner(s, task, draft)} : null; };
  /* A registry default is clamped by routing at send time, so a task may keep
     following it even when the ceiling is lower. */
  const limitIsDefault = task => (task.fields.max_output_tokens.inherited_source || "default") === "default";
  /* 回复任务的上限来自插件自己的「最大输出 Token」，不是 Provider 自报的能力：
     Provider 设置里根本没有这个数字，改那里也调不动它。提示必须指向插件配置，
     否则用户只会去模型提供方里找一个不存在的上限。旧后端没有 basis 字段时按
     Provider 处理，保留原有措辞。 */
  const pluginOutputSetting = "「配置 → 插件 → 我会永远陪着你 → 最大输出 Token」";
  const limitIsPluginGlobal = excess => excess.owner?.max_output_tokens_basis === "plugin_global";
  const limitOrigin = excess => limitIsPluginGlobal(excess)
    ? `插件全局『最大输出 Token』（当前 ${excess.owner.plugin_global_max_output_tokens}）`
    : `所选模型提供方的 ${excess.limit}`;
  /* 全局值低于结构任务的下限时，插件按地板值上报；不解释一句，界面上的数字
     就像凭空冒出来的。 */
  const limitFloorNote = excess => limitIsPluginGlobal(excess) && excess.owner.floor_applied
    ? `该任务至少需要 ${excess.owner.floor_applied}，插件按 ${excess.owner.floor_applied} 上报。` : "";
  const sentence = (...parts) => parts.filter(Boolean).join("");
  /* Only a value the user saved explicitly is clamped to the ceiling. An
     inherited value keeps following its source: pinning it would silently turn
     "follow the default" into a saved number, which is exactly what the larger
     registry defaults must not cause. */
  function fitLimit(s, task, draft) { const excess = limitExcess(s, task, draft);
    if (!excess) return null;
    if (draft.inherited.max_output_tokens) return {basis: "inherited", ...excess};
    draft.values.max_output_tokens = excess.limit; draft.inherited.max_output_tokens = false;
    return {basis: "adjusted", ...excess}; }
  function limitNotice(task, draft, fit) { const label = taskName(task.kind).label;
    if (fit.basis === "adjusted")
      return sentence(`${label} 的输出上限已调整为 ${fit.limit}，请核对后保存。`, limitFloorNote(fit));
    const from = limitOrigin(fit);
    return sentence(limitIsDefault(task)
      ? `${label}：继承的默认输出上限 ${fit.value} 高于${from}，发送时会按 ${fit.limit} 生效。`
      : `${label}：继承的输出上限 ${fit.value} 超过${from}；跟随上级不会被改写，请显式设置上限或改回上级。`,
      limitFloorNote(fit)); }
  function limitHint(s, task, draft) { const excess = limitExcess(s, task, draft); if (!excess) return "";
    const from = limitOrigin(excess), floor = limitFloorNote(excess);
    if (!draft.inherited.max_output_tokens)
      return limitIsPluginGlobal(excess)
        ? sentence(`输出上限 ${excess.value} 超过${from}。`,
          `请先把${pluginOutputSetting}调高，或把本任务上限调到 ${excess.limit}。`, floor)
        : `输出上限 ${excess.value} 超过${from}，保存会被拒绝。`;
    return sentence(limitIsDefault(task)
      ? `继承的默认输出上限 ${excess.value} 高于${from}，发送时会按 ${excess.limit} 生效。`
      : `继承的输出上限 ${excess.value} 超过${from}，发出时会被拒绝；请显式设置上限或改回上级。`, floor); }
  function limitProblem(s, task, draft) { const excess = limitExcess(s, task, draft); if (!excess) return "";
    const from = limitOrigin(excess);
    if (!draft.inherited.max_output_tokens)
      return limitIsPluginGlobal(excess)
        ? `输出上限 ${excess.value} 超过${from}，请先调高插件全局设置或把本任务上限调到 ${excess.limit}`
        : `输出上限 ${excess.value} 超过${from}，请调低后再保存`;
    return limitIsDefault(task) ? ""
      : `继承的输出上限 ${excess.value} 超过${from}，请显式设置上限或改回上级`; }
  const rowFallback = (task, draft) => {
    if (coreReply(task)) return "";
    if (draft && (!draft.inherited.fallback || draft.touched)) return effective(task, draft, "fallback");
    return fallbackFor(task);
  };
  const storageKey = kind => key + ":" + kind;
  const shown = (s, task) => { const info = taskName(task.kind), draft = s.drafts.get(task.kind);
    const custom = draft ? taskFields(task).some(name => !draft.inherited[name]) : taskFields(task).some(name => ownsField(task, name));
    const fb = rowFallback(task, draft);
    return (!s.providerFilter || s.providerFilter === providerFor(task, draft) || s.providerFilter === fb)
      && (!s.lane || s.lane === "all" || s.lane === task.lane)
      && (!s.custom
        || (s.custom === "custom" && custom)
        || (s.custom === "inherited" && !custom)
        || (s.custom === "nofb" && !coreReply(task) && !fb))
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
    /* ``cleared`` marks a stored value recovery stopped using: still in the
       layer, but not what dispatch runs, so it is not "following the parent". */
    inherited: Object.fromEntries(fields.map(name => [name, !task.fields[name].explicit
      && !task.fields[name].cleared && !task.fields[name].replaced])),
    /* Fields the user picked explicitly even though the value equals the
       projection (a cleared fallback reset to "no fallback"): patchFor must
       send them, otherwise the stored row survives untouched. */
    chosen: {}, touched: false, stale: false, conflicts: [], pending: readPending(task.kind) });
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
    for (const name of taskFields(task)) { const field = task.fields[name];
      /* A cleared field still has a stored row even though the projection marks
         it non-explicit: following the parent must reset it, and leaving it
         alone must not rewrite the row with the routed placeholder. */
      const stored = !!field.explicit || !!field.cleared || !!field.replaced;
      if (draft.inherited[name]) { if (stored) reset.push(name); }
      else if (!stored || draft.values[name] !== field.value || draft.chosen?.[name]) changes[name] = draft.values[name]; }
    /* A cleared row is only rewritten when this draft moves the primary: an
       untouched card must not report itself dirty and rewrite the row on the
       next navigation. */
    const primaryTouched = Object.hasOwn(changes, "provider") || reset.includes("provider");
    if (primaryTouched) for (const name of taskFields(task)) { const field = task.fields[name];
      if (!(field.cleared || field.replaced) || draft.inherited[name] || draft.chosen?.[name]) continue;
      if (draft.values[name] !== field.value) continue;
      /* An emptied row stays off; a substituted row is written back as the
         value the card shows, so changing the primary does not silently
         disable a fallback the operator can see. */
      changes[name] = field.replaced && field.value ? field.value : ""; }
    // A fallback identical to the primary provider is not a fallback.  Keeping
    // it explicit makes every later edit fail the page's duplicate check and
    // the host's model_fallback_duplicate guard; normalize it to "no fallback".
    const primary = draft.inherited.provider ? task.fields.provider.inherited_value : draft.values.provider;
    const fallback = draft.inherited.fallback ? task.fields.fallback.inherited_value : draft.values.fallback;
    if (!draft.inherited.fallback && fallback && fallback === primary) changes.fallback = "";
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
      /* Keep an explicit choice (clearing a recovered-away fallback): without it
         the rebuilt draft matches the projection and the save would drop it. */
      if (draft.chosen?.[name]) next.chosen[name] = true;
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
      /* 页面桥自带一个较短的默认超时（18 秒），长任务必须把调用方的超时透传给它；
         本地竞速比桥晚 5 秒，让桥先决定结果，避免两边同时超时后互相覆盖。 */
      const options = {timeoutMs: timeout};
      const response = await Promise.race([method === "GET" ? bridge.apiGet(endpoint, body, options) : bridge.apiPost(endpoint, body, options), new Promise((_, reject) => { timer = setTimeout(() => reject(failure("bridge_timeout")), timeout + 5000); })]);
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
    try { return await api("POST", taskPath + "/configure", payload, TASK_CONFIG_TIMEOUT_MS); }
    catch (err) {
      if (!isBridgeTimeout(err)) throw err;
      return api("POST", taskPath + "/configure", payload, TASK_CONFIG_RETRY_TIMEOUT_MS);
    }
  }
  function validateStatus(data) { if (!Array.isArray(data.tasks) || !Array.isArray(data.providers) || data.tasks.some(task =>
    typeof task.kind !== "string" || typeof task.edit_token !== "string" || task.edit_token.length !== 64
    || !task.fields || routing.some(name => !task.fields[name]) || routing.some(name => !Object.hasOwn(task.fields[name], "value")))) throw failure("invalid_response"); return data; }
  function globalDefaultsInfo(s) {
    const raw = s.status?.global_defaults;
    if (raw && typeof raw === "object") {
      return {
        provider_id: String(raw.provider_id || ""),
        fallback_provider_id: String(raw.fallback_provider_id || ""),
        effective_provider_id: String(raw.effective_provider_id || raw.provider_id || ""),
        effective_fallback_provider_id: String(raw.effective_fallback_provider_id || raw.fallback_provider_id || ""),
        editable: raw.editable === true,
      };
    }
    const sample = snapshot(s).find(t => !coreReply(t) && t.fields.provider?.inherited_source === "installation");
    const inferred = sample?.fields?.provider?.inherited_value || "";
    const inferredFb = sample?.fields?.fallback?.inherited_source === "installation" ? (sample.fields.fallback.inherited_value || "") : "";
    return {
      provider_id: inferred,
      fallback_provider_id: inferredFb,
      effective_provider_id: inferred,
      effective_fallback_provider_id: inferredFb === inferred ? "" : inferredFb,
      editable: false,
    };
  }
  function globalDefaultsDirty(s) {
    const g = globalDefaultsInfo(s);
    if (!g.editable || !s.globalDraft) return false;
    return s.globalDraft.provider_id !== g.provider_id || s.globalDraft.fallback_provider_id !== g.fallback_provider_id;
  }
  function syncGlobalDraft(s, force = false) {
    const g = globalDefaultsInfo(s);
    if (force || !s.globalDraft || !globalDefaultsDirty(s)) {
      s.globalDraft = { provider_id: g.provider_id, fallback_provider_id: g.fallback_provider_id };
    }
  }
  function setStatus(s, data) { s.status = validateStatus(normalize(data));
    for (const task of data.tasks) { const draft = s.drafts.get(task.kind);
      if (!draft) { s.drafts.set(task.kind, newDraft(task)); continue; }
      s.drafts.set(task.kind, reconcileDraft(task, draft)); }
    syncGlobalDraft(s);
    const kinds = new Set(data.tasks.map(task => task.kind)); s.selection = s.selection.filter(kind => kinds.has(kind));
    if (s.expanded && !kinds.has(s.expanded)) s.expanded = null; }
  const projected = (task, patch, name) => Object.hasOwn(patch.changes, name) ? patch.changes[name]
    : (patch.reset.includes(name) ? task.fields[name].inherited_value : task.fields[name].value);
  function validatePatch(task, draft, p) { if (!Object.keys(p.changes).length && !p.reset.length) return false;
    if (Object.hasOwn(p.changes, "prompt") && (typeof p.changes.prompt !== "string" || p.changes.prompt.length > 8000)) return false;
    if (Object.hasOwn(p.changes, "max_output_tokens") && (!Number.isInteger(p.changes.max_output_tokens) || p.changes.max_output_tokens < 1 || p.changes.max_output_tokens > MAX_MODEL_OUTPUT_TOKENS)) return false;
    if (Object.hasOwn(p.changes, "timeout_seconds") && (!Number.isInteger(p.changes.timeout_seconds) || p.changes.timeout_seconds < 1 || p.changes.timeout_seconds > 600)) return false;
    if (Object.hasOwn(p.changes, "replacement_scope") && !["plugin", "conversation", "all"].includes(p.changes.replacement_scope)) return false;
    if (Object.hasOwn(p.changes, "fallback_on_refusal") && typeof p.changes.fallback_on_refusal !== "boolean") return false;
    if (Object.hasOwn(p.changes, "replacement_rules") && (typeof p.changes.replacement_rules !== "string" || p.changes.replacement_rules.length > 24000)) return false;
    if (Object.hasOwn(p.changes, "refusal_terms") && (typeof p.changes.refusal_terms !== "string" || p.changes.refusal_terms.length > 4096)) return false;
    if (coreReply(task)) return Object.keys(p.changes).concat(p.reset).every(name => name === "prompt" || name === "replacement_rules");
    const primary = projected(task, p, "provider"), fallback = projected(task, p, "fallback"), limit = projected(task, p, "max_output_tokens");
    const picked = [primary, ...(fallback ? [fallback] : [])], providers = picked.map(id => state.status.providers.find(provider => provider.id === id));
    const fallbackExplicit = Object.hasOwn(p.changes, "fallback")
      || (!p.reset.includes("fallback")
        && (task.fields.fallback.explicit || task.fields.fallback.replaced));
    /* A registry default is clamped by routing at send time, so a task may keep
       following it even when the selected provider is smaller: the save only
       carries the provider, and the sent envelope fits. */
    const limitDefault = !Object.hasOwn(p.changes, "max_output_tokens")
      && (p.reset.includes("max_output_tokens") || !task.fields.max_output_tokens.explicit)
      && limitIsDefault(task);
    return (!fallbackExplicit || !fallback || fallback !== primary)
      && providers.every(provider => providerSupportsTask(task, provider))
      && (limitDefault || limit <= limitFor(state, task, draft)); }
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
    decision: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4"/><circle cx="12" cy="12" r="5"/><path d="m8.5 8.5 7 7M15.5 8.5l-7 7"/>',
    pricing: '<circle cx="12" cy="12" r="9"/><path d="M9 8.5 12 12l3-3.5M8.5 12.5h7M8.5 15.5h7M12 12v6"/>',
    quota: '<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M3 10h18"/><path d="M15 14.5h3.5"/>' };
  const groupIcon = name => ({ "对话": ICONS.reply, "主动与整理": ICONS.memory, "生活与成长": ICONS.life, "私人书写": ICONS.write }[name] || ICONS.lane);
  const CAP_SHORT = { text: "文", image: "图", audio: "音", video: "视", tools: "具", embedding: "向", vision: "视", video_relay: "转视" };
  function topbar(s) {
    return `<div class="model-topbar"><span class="model-topbar-t">任务模型</span>${window.CompanionWorkbench.scopeHintMarkup(Object.entries(window.CompanionWorkbench.scopeObjectForEndpoint(PREFIX + taskPath)).filter(([name,value]) => value && (name !== "target" || windowAvailable)).map(([,value]) => value).join(" · ") || "当前配置", "查看任务模型配置对象")}
      <span class="model-pill ${s.status?.host_state === "running" ? "ok" : "warn"}">${esc(s.status?.host_state === "running" ? "运行中" : s.status?.host_state || "未连接")}</span>
      <span class="model-grow"></span>
      <div class="model-switches" role="group" aria-label="配置作用域">${[["role", "角色默认"], ["window", windowLabel(s)]].map(([layer, label]) => `<button type="button" data-model-layer="${layer}" aria-pressed="${s.layer === layer}" class="${s.layer === layer ? "active" : ""}" ${s.busy || s.loading || layer === "window" && !windowAvailable ? "disabled" : ""}>${label}</button>`).join("")}${modelScopeHint(s)}</div>
      <button type="button" class="button small" data-model-action="refresh" ${s.loading || s.busy ? "disabled" : ""}>刷新配置</button></div>`; }
  function notice(s) { const text = s.notice || (s.loading ? "正在读取任务配置…" : "点击任意任务行即可在右侧抽屉精调；改动保存后在下一次任务生效。");
    return `<div class="model-notice ${s.bad ? "error" : ""}" role="status" aria-live="polite">${esc(text)}</div>`; }
  function globalDefaultsCard(s) {
    if (!s.status) return "";
    const g = globalDefaultsInfo(s);
    const providers = (s.status.providers || []).filter(p => (p.capabilities || []).includes("text"));
    const draft = s.globalDraft || { provider_id: g.provider_id, fallback_provider_id: g.fallback_provider_id };
    const dirtyGlobal = globalDefaultsDirty(s);
    const effPrimary = draft.provider_id || g.effective_provider_id;
    const hardCollide = !!draft.provider_id && draft.fallback_provider_id === draft.provider_id;
    const softCollide = !draft.provider_id && !!effPrimary && draft.fallback_provider_id === effPrimary;
    const locked = s.globalBusy || s.loading || s.denied;
    const providerOpt = (selectedId, emptyLabel) => [
      `<option value="" ${!selectedId ? "selected" : ""}>${esc(emptyLabel)}</option>`,
      ...providers.map(p => `<option value="${esc(p.id)}" ${selectedId === p.id ? "selected" : ""}>${esc(providerLabel(p))}</option>`),
      ...(selectedId && !providers.some(p => p.id === selectedId)
        ? [`<option value="${esc(selectedId)}" selected>${esc(selectedId)} · 当前未加载</option>`] : []),
    ].join("");
    const statusBadge = s.globalRestartRequired
      ? '<span class="model-tag a">已保存 · 重启插件后完全生效</span>'
      : dirtyGlobal
        ? '<span class="model-tag a">有未保存改动</span>'
        : g.editable
          ? '<span class="model-tag g">全局同步</span>'
          : '<span class="model-tag n">只读预览</span>';
    return `<section class="model-global-card" aria-label="陪伴通用模型全局默认">
      <div class="model-global-head">
        <div class="model-grow">
          <div class="model-eb">全局默认 · 陪伴通用模型<span class="st">${statusBadge}</span></div>
          <h3>陪伴通用模型（全局默认）</h3>
          <p class="model-global-desc">所有保持「跟随上级」且角色/会话层未单独覆写的任务，都会自动走这里配置的主模型与备用模型；保存会同步写入插件全局配置。</p>
        </div>
        ${g.editable ? `<div class="model-global-actions">
          <button type="button" class="button small" data-global-default-reset ${locked || !dirtyGlobal ? "disabled" : ""}>重置</button>
          <button type="button" class="button small primary" data-global-default-save ${locked || !dirtyGlobal || hardCollide ? "disabled" : ""}>${s.globalBusy ? "保存中…" : "保存全局默认"}</button>
        </div>` : `<div class="model-global-actions"><button type="button" class="button small" data-go="manage" data-manage="host">账号与会话 ↗</button></div>`}
      </div>
      ${g.editable ? `<div class="model-global-grid">
        <label class="model-fd"><span class="model-cap">全局主模型提供方 <em>留空则自动跟随 AstrBot 当前对话模型</em></span>
          <span class="model-msel"><i style="background:${pillColor(effPrimary)}"></i><select data-global-default-field="provider_id" aria-label="全局主模型提供方" ${locked ? "disabled" : ""}>${providerOpt(draft.provider_id, g.effective_provider_id ? `自动跟随 AstrBot 当前模型（当前：${g.effective_provider_id}）` : "自动跟随 AstrBot 当前对话模型")}</select></span></label>
        <label class="model-fd"><span class="model-cap">全局备用模型提供方 <em>主模型失败、超时或拒答时的兜底链路</em></span>
          <span class="model-msel"><i style="background:${pillColor(draft.fallback_provider_id)}"></i><select data-global-default-field="fallback_provider_id" aria-label="全局备用模型提供方" ${locked ? "disabled" : ""}>${providerOpt(draft.fallback_provider_id, "不使用全局备用模型")}</select></span></label>
      </div>` : `<div class="model-global-readonly">
        <span class="model-global-ro-item"><i>主模型</i>${providerPill(g.effective_provider_id, "跟随 AstrBot 当前模型", s.hoverProvider)}</span>
        <span class="model-rrow-arrow" aria-hidden="true">→</span>
        <span class="model-global-ro-item"><i>备用模型</i>${providerPill(g.effective_fallback_provider_id, "无全局备用", s.hoverProvider)}</span>
      </div>`}
      ${hardCollide ? '<div class="model-alert" role="alert">全局备用模型不能与全局主模型相同，请更换备用模型或选择「不使用全局备用模型」。</div>' : ""}
      ${softCollide ? '<div class="model-alert" role="alert">所选全局备用模型与当前生效的主模型相同，建议选择其他模型作为备用。</div>' : ""}
      ${s.globalNotice ? `<div class="model-notice ${s.globalBad ? "error" : ""}" role="status">${esc(s.globalNotice)}</div>` : ""}
    </section>`;
  }
  function toolbar(s) {
    const srcItems = [["all", "全部"], ["custom", "单独设置"], ["inherited", "跟随上级"], ["nofb", "仅无备用"]];
    const groupSeg = s.view === "tasks"
      ? `<div class="model-filters" role="group" aria-label="分组方式">${[["slot", "按业务分组"], ["lane", "按前台 / 后台"]]
          .map(([id, label]) => `<button type="button" data-model-av="${id}" aria-pressed="${s.av === id}" class="${s.av === id ? "active" : ""}">${label}</button>`).join("")}</div>`
      : "";
    return `<div class="model-toolbar"><label class="model-search"><span>搜索任务</span>
        <input type="search" data-model-search value="${esc(s.query)}" placeholder="搜索任务名称、用途或任务键"></label>
      <label class="model-provider-filter"><span>模型提供方</span><select data-model-provider-filter aria-label="按模型提供方筛选">
        <option value="" ${s.providerFilter ? "" : "selected"}>全部提供方</option>${(s.status?.providers || []).map(provider => `<option value="${esc(provider.id)}" ${s.providerFilter === provider.id ? "selected" : ""}>${esc(providerLabel(provider))}</option>`).join("")}</select></label>
      <div class="model-switches" role="group" aria-label="视图">${[["tasks", "按任务"], ["providers", "按提供方"]].map(([id, label]) => `<button type="button" data-model-view="${id}" aria-pressed="${s.view === id}" class="${s.view === id ? "active" : ""}">${label}</button>`).join("")}</div>
      ${groupSeg}
      <div class="model-filters" role="group" aria-label="状态筛选">${srcItems
        .map(([id, label]) => { const on = (id === "all" && !s.custom) || s.custom === id; return `<button type="button" data-model-custom="${id}" aria-pressed="${on}" class="${on ? "active" : ""}">${label}</button>`; }).join("")}</div>
      <div class="model-filters" role="group" aria-label="通道筛选">${[["all", "全部通道"], ["foreground", "前台"], ["background", "后台"]]
        .map(([id, label]) => `<button type="button" data-model-lane="${id}" aria-pressed="${s.lane === id}" class="${s.lane === id ? "active" : ""}">${label}</button>`).join("")}</div></div>`; }
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
  /* "上级"不是一个抽象层级：一个任务可能继承窗口、角色或安装级配置，也可能
     落到 AstrBot 当前会话模型。标签必须说清是哪一层，否则用户无法判断自己
     到底在跟随谁。失效的旧值会被后端替换成宿主模型，这里无需特判。
     层名复用 configSource：同一来源在同一张卡片上只能有一种说法。 */
  const parentRoute = field => {
    const layer = configSource(field?.inherited_source || "default");
    return field?.inherited_value ? `${layer}：${field.inherited_value}` : `${layer}（未设置）`;
  };
  /* The backend keeps a blank or unavailable provider on editable layers (role
     and window) as a live failure and only recovers installation-layer values,
     so following one produces a draft that cannot be saved. Mirror only that
     layer distinction: projection already fills default/host blanks. Every
     follow-parent entry point refuses it the same way. */
  const unfollowableParent = (s, task) => {
    const field = task.fields.provider;
    if (field.inherited_source !== "role" && field.inherited_source !== "window") return null;
    if (!field.inherited_value) return field;
    /* Same capability filter the preview and the save path use: a provider that
       exists but cannot serve this task is just as unusable as a removed one. */
    return available(s, task).some(provider => provider.id === field.inherited_value) ? null : field;
  };
  const unfollowableNotice = field => !field.inherited_value
    ? `上级（${configSource(field.inherited_source)}）保存的主模型是空值，跟随它无法路由；请先到上级修复。`
    : `上级（${configSource(field.inherited_source)}）保存的主模型 ${field.inherited_value} 当前不可用，跟随它无法保存；请先到上级修复。`;
  const capabilityLabel = capability => ({
    text: "文本生成", image: "图片输入", audio: "音频输入", tools: "工具调用",
    embedding: "向量嵌入", video: "视频理解", video_relay: "视觉转接视频", vision: "图像理解", "conversation.reply": "对话回复",
  }[capability] || capability);
  function providerOptions(s, task, draft, name) { const providers = available(s, task);
    const inherited = draft.inherited[name];
    /* The primary select carries an explicit "follow the parent" option so a
       task can leave its own value without hunting for the checkbox below.
       Its value is a sentinel, not the empty string: a legacy converter may
       have stored an explicit empty provider, and that broken state must not
       render as if it followed the parent. */
    /* A stored value recovery stopped using (it collided with the recovered
       primary) is neither an explicit nor an inherited value. Show it as a
       disabled marker so the user can see and replace the row that is still
       saved. It only applies while the draft still holds the routed value: the
       moment the user picks a provider, that choice must win in the select. */
    const cleared = !!task.fields[name]?.cleared && !inherited && !draft.chosen?.[name]
      && draft.values[name] === task.fields[name].value;
    const selected = cleared ? "__cleared__"
      : name === "provider"
        ? (inherited ? "__inherit__" : draft.values[name])
        : (inherited ? fallbackShown(task, draft) : draft.values[name]);
    let empty;
    if (name === "fallback") empty =
      (cleared ? '<option value="__cleared__" selected disabled>已停用（本层保存的备用与主模型重复）</option>' : "")
      /* Recovery may substitute a usable default; the stored row is still there
         and must stay visible so it can be removed. */
      + (task.fields[name].replaced
        ? `<option value="__replaced__" disabled>本层保存的 ${esc(task.fields[name].stored_value)} 已被恢复替换</option>` : "")
      + (fallbackUnknown(task, draft)
        ? '<option value="" selected disabled>跟随上级（保存后按当前主模型解析）</option>'
        : '<option value="">不使用备用模型</option>');
    else {
      const inheritedValue = task.fields.provider.inherited_value;
      const unavailable = !!inheritedValue && !providers.some(provider => provider.id === inheritedValue);
      empty = `${cleared ? '<option value="__cleared__" selected disabled>已停用（本层保存的主模型与备用冲突）</option>' : ""}`
        + `<option value="__inherit__" ${selected === "__inherit__" ? "selected" : ""}>跟随上级（${esc(parentRoute(task.fields.provider))}${unavailable ? " · 当前不可用" : ""}）</option>`;
    }
    const blank = name === "provider" && !inherited && selected === ""
      ? '<option value="" selected>未设置（本层保存为空值）· 当前不可用</option>' : "";
    return `${empty}${blank}${providers.map(provider => `<option value="${esc(provider.id)}" ${selected === provider.id ? "selected" : ""}>${esc(providerLabel(provider))}</option>`).join("")}
      ${selected && selected !== "__inherit__" && selected !== "__cleared__" && !providers.some(p => p.id === selected) ? `<option value="${esc(selected)}" selected>${esc(selected)} · 当前不可用</option>` : ""}`; }
  function conflictNotice(task, draft) {
    if (!draft.stale) return "";
    const value = field => String(field ?? "").slice(0, 200) || "空";
    const details = (draft.conflicts || []).map(name => `<p><b>${esc(fieldLabel[name] || name)}</b>：当前值「${esc(value(task.fields[name].value))}」；草稿「${esc(draft.inherited[name] ? "跟随上级" : value(draft.values[name]))}」</p>`).join("");
    return `<div class="model-alert" role="alert">${details ? "以下字段也在别处改过，请核对后选择保留草稿或放弃。" : "配置已更新，请重新读取后核对草稿。"}${details}</div>`;
  }
  /* ---------- compact routing list & right-side slide-over inspector drawer ---------- */
  const fieldDomId = (kind, name) => `mf-${String(kind || "").replace(/[^a-zA-Z0-9_-]/g, "_")}-${String(name || "").replace(/[^a-zA-Z0-9_-]/g, "_")}`;
  function fieldHeader(label, hint, inherited, name, kind, fieldLocked) {
    const controlId = fieldDomId(kind, name);
    const stateBadge = inherited
      ? `<span class="model-field-state" data-field-state="${esc(name)}" data-inherited="true">跟随上级</span>`
      : `<button type="button" class="model-inh-btn" data-model-inherit-reset="${esc(name)}" data-model-kind="${esc(kind)}" ${fieldLocked ? "disabled" : ""} title="清除本层覆写并改回跟随上级">↺ 恢复继承</button>`;
    return `<div class="model-cap-wrap"><div class="model-cap"><label class="model-cap-main" for="${esc(controlId)}"><b>${esc(label)}</b></label>${stateBadge}</div>${hint ? `<span class="model-cap-hint">${esc(hint)}</span>` : ""}</div>`;
  }
  const scopeLabelText = scope => ({ plugin: "仅插件调用", conversation: "仅普通对话模型", all: "全部生效" }[scope] || "全部生效");
  function taskDiffChips(s, task, draft) {
    const chips = [];
    if (draft?.pending) chips.push('<span class="model-tag a">待核对</span>');
    else if (draft?.stale) chips.push('<span class="model-tag r">有冲突</span>');
    else if (draft && dirty(task, draft)) chips.push('<span class="model-tag a">待保存</span>');
    if (coreReply(task)) {
      if (draft && !draft.inherited.prompt && String(effective(task, draft, "prompt") || "").trim()) chips.push('<span class="model-tag">自定义提示词</span>');
      const rules = ruleOf(s, task.kind).length;
      if (rules) chips.push(`<span class="model-tag n">${rules} 规则</span>`);
      return chips;
    }
    if (draft && limitHint(s, task, draft)) chips.push('<span class="model-tag a">上限提示</span>');
    const own = draft ? taskFields(task).some(name => !draft.inherited[name]) : taskFields(task).some(name => ownsField(task, name));
    if (own) chips.push('<span class="model-tag">单独设置</span>');
    if (draft && !draft.inherited.timeout_seconds) chips.push(`<span class="model-tag n">超时 ${esc(effective(task, draft, "timeout_seconds"))}s</span>`);
    if (draft && !draft.inherited.max_output_tokens) chips.push(`<span class="model-tag n">上限 ${esc(effective(task, draft, "max_output_tokens"))}</span>`);
    if (draft && !draft.inherited.prompt && String(effective(task, draft, "prompt") || "").trim()) chips.push('<span class="model-tag n">自定义提示词</span>');
    const rules = ruleOf(s, task.kind).length;
    if (rules) chips.push(`<span class="model-tag n">${rules} 规则</span>`);
    const refusal = draft
      ? (draft.inherited.fallback_on_refusal ? !!task.fields.fallback_on_refusal.inherited_value : !!draft.values.fallback_on_refusal)
      : task.fields.fallback_on_refusal.value === true;
    if (refusal) chips.push('<span class="model-tag a">拒答替代</span>');
    return chips;
  }
  function taskRow(s, task) {
    const info = taskName(task.kind), draft = s.drafts.get(task.kind);
    const isCore = coreReply(task);
    const picked = !isCore && s.selection.includes(task.kind);
    const active = s.expanded === task.kind;
    const waiting = draft && dirty(task, draft) && !draft.pending;
    const main = isCore ? "" : providerFor(task, draft);
    const fb = rowFallback(task, draft);
    const chips = taskDiffChips(s, task, draft);
    const capBadges = (task.capabilities || []).map(cap =>
      `<span class="model-cap-mini" title="${esc(capabilityLabel(cap))}">${esc(CAP_SHORT[cap] || capabilityLabel(cap))}</span>`).join("");
    return `<li class="model-rrow${active ? " is-active" : ""}${picked ? " is-picked" : ""}${waiting ? " is-dirty" : ""}" data-task-row="${esc(task.kind)}">
      <label class="model-rrow-ck"><input type="checkbox" data-model-pick="${esc(task.kind)}" ${picked ? "checked" : ""} ${isCore ? "disabled" : ""} aria-label="选择 ${esc(info.label)}"></label>
      <button type="button" class="model-rrow-main" data-model-open="${esc(task.kind)}" aria-label="编辑 ${esc(info.label)}">
        <span class="model-rrow-head"><b>${esc(info.label)}</b><span class="model-tag ${task.lane === "foreground" ? "g" : "n"}">${task.lane === "foreground" ? "前台" : "后台"}</span>${capBadges ? `<span class="model-cap-badges">${capBadges}</span>` : ""}</span>
        <small class="model-rrow-desc">${esc(info.description)}</small>
      </button>
      <div class="model-rrow-route">${isCore
        ? '<span class="model-tag g">AstrBot 核心</span><span class="model-rrow-core-note">当前会话模型 · 附加提示与策略</span>'
        : `${providerPill(main, "未设置", s.hoverProvider)}<span class="model-rrow-fb"><span class="model-rrow-arrow" aria-hidden="true">→</span>${providerPill(fb, "无回退", s.hoverProvider)}</span>`}</div>
      <div class="model-diff-chips">${chips.join("")}</div>
      <button type="button" class="model-rrow-edit" data-model-open="${esc(task.kind)}" aria-label="精调 ${esc(info.label)}">精调 ›</button>
    </li>`;
  }
  function routingList(s) {
    const tasks = snapshot(s);
    const groups2 = s.av === "lane"
      ? [["前台 · 用户可见", tasks.filter(t => t.lane === "foreground")], ["后台 · 静默运行", tasks.filter(t => t.lane === "background")]]
      : taskGroups(s).map(([g, items]) => [g, items.map(([kind]) => taskOf(s, kind)).filter(Boolean)]);
    let matchedTotal = 0;
    const sections = groups2.map(([g, ts]) => {
      const shownTasks = ts.filter(t => shown(s, t));
      if (!shownTasks.length) return "";
      matchedTotal += shownTasks.length;
      const gkey = s.av === "lane" ? "lane:" + (g.startsWith("前台") ? "foreground" : "background") : g;
      const selectable = shownTasks.filter(t => !coreReply(t));
      const selCount = selectable.filter(t => s.selection.includes(t.kind)).length;
      const allSel = selectable.length > 0 && selCount === selectable.length;
      const ownCount = shownTasks.filter(t => { const d = s.drafts.get(t.kind); return d && taskFields(t).some(nm => !d.inherited[nm]); }).length;
      const collapsed = !!s.groupCollapsed[gkey];
      const canBatch = s.av !== "lane" && selectable.length > 0;
      return `<section class="model-rgroup${collapsed ? " is-collapsed" : ""}${selCount ? " has-picked" : ""}" data-route-group="${esc(gkey)}">
        <header class="model-rgroup-head">
          <input type="checkbox" class="model-gck" data-model-gpick="${esc(gkey)}" ${allSel ? "checked" : ""} ${!selectable.length ? "disabled" : ""} aria-label="全选 ${esc(g)}">
          <button type="button" class="model-rgroup-toggle" data-model-group-toggle="${esc(gkey)}" aria-expanded="${!collapsed}">
            <span class="model-rgroup-chev" aria-hidden="true">▾</span>
            ${icon(groupIcon(g))}
            <b>${esc(g)}</b>
            <span class="model-rgroup-meta" data-group-base="${shownTasks.length} 项${ownCount ? ` · ${ownCount} 项已改` : ""}">${shownTasks.length} 项${ownCount ? ` · ${ownCount} 项已改` : ""}${selCount ? ` · 已选 ${selCount}` : ""}</span>
          </button>
          ${canBatch ? `<button type="button" class="model-lk" data-quick-group="${esc(g)}">本组批改</button>` : ""}
        </header>
        <div class="model-rgroup-body"><div class="model-rgroup-inner"${collapsed ? ' aria-hidden="true" inert' : ""}><ul class="model-rlist">${shownTasks.map(task => taskRow(s, task)).join("")}</ul></div></div>
      </section>`;
    }).filter(Boolean);
    if (!matchedTotal) return '<p class="model-empty">没有匹配的任务。</p>';
    return `<div class="model-route-groups">${sections.join("")}</div>`;
  }
  function inspectorHeader(s, task, info, draft, badgeMarkup) {
    const list = visible(s);
    const idx = list.findIndex(item => item.kind === task.kind);
    const prevDisabled = idx <= 0;
    const nextDisabled = idx < 0 || idx >= list.length - 1;
    const stepText = idx >= 0 ? `${idx + 1} / ${list.length}` : "";
    return `<header class="model-insp-head">
      <div class="model-insp-top">
        <div class="model-insp-crumb">
          <span class="model-eb">${draft.layer === "role" ? "角色默认" : windowLabel(s)} · ${esc(info.group)}</span>
          ${badgeMarkup}
        </div>
        <div class="model-insp-controls">
          <div class="model-insp-nav" role="group" aria-label="切换上下任务">
            <button type="button" class="model-insp-step" data-inspector-step="-1" ${prevDisabled ? "disabled" : ""} title="上一个任务 (↑)" aria-label="上一个任务">‹</button>
            ${stepText ? `<span class="model-insp-pos">${stepText}</span>` : ""}
            <button type="button" class="model-insp-step" data-inspector-step="1" ${nextDisabled ? "disabled" : ""} title="下一个任务 (↓)" aria-label="下一个任务">›</button>
          </div>
          <button type="button" class="x" data-inspector-close title="${dirty(task, draft) && !draft.pending ? "收起抽屉（未保存草稿会保留）" : "关闭抽屉 (Esc)"}" aria-label="关闭任务配置抽屉">×</button>
        </div>
      </div>
      <div class="model-insp-title">${icon(coreReply(task) ? ICONS.reply : groupIcon(info.group))}<h3>${esc(info.label)}</h3><span class="model-tag ${task.lane === "foreground" ? "g" : "n"}">${task.lane === "foreground" ? "前台" : "后台"}</span></div>
      <p class="model-insp-desc">${esc(coreReply(task) ? (task.routing_note || "普通回复采用 AstrBot 当前会话模型。") : info.description)}</p>
      <div class="model-insp-meta">
        <code>${esc(task.kind)}</code>
        ${!coreReply(task) ? `<div class="model-rl">${task.capabilities.map(cap => `<span>${esc(capabilityLabel(cap))}</span>`).join("") || "<span>无能力声明</span>"}</div>` : ""}
      </div>
    </header>`;
  }
  function coreReplyInspectorSpec(s, task) {
    const info = taskName(task.kind), draft = s.drafts.get(task.kind), locked = s.loading || s.busy || s.denied;
    const fieldLocked = locked || !!draft.pending;
    const waiting = dirty(task, draft) && !draft.pending;
    const promptValue = effective(task, draft, "prompt") || "";
    const rules = ruleOf(s, task.kind).length;
    const badge = '<span class="model-tag g">AstrBot 核心</span>';
    return {
      mode: "inspector",
      kind: task.kind,
      label: `配置 ${info.label}`,
      closeAttr: "data-inspector-close",
      extraClass: "model-inspector",
      html: `<article class="model-fncard open model-inspector-card${waiting ? " dirty" : ""}" data-fncard="${esc(task.kind)}">
          ${inspectorHeader(s, task, info, draft, badge)}
          <div class="model-insp-body">
            ${conflictNotice(task, draft)}
            ${draft.pending ? '<div class="model-alert">上次保存结果未确认，请核对原保存。</div>' : ""}
            <section class="model-insp-sec">
              <div class="model-insp-sec-h"><b>对话附加提示</b><span class="model-char-count" data-model-prompt-count="${esc(task.kind)}">${promptValue.length} / 8000</span></div>
              <div class="model-fd">${fieldHeader("对话附加提示", "直接注入核心回复 · 最多 8000 字", draft.inherited.prompt, "prompt", task.kind, fieldLocked)}
                <textarea id="${esc(fieldDomId(task.kind, "prompt"))}" rows="6" maxlength="8000" data-model-field="prompt" data-model-kind="${esc(task.kind)}" ${fieldLocked ? "disabled" : ""}>${esc(promptValue)}</textarea></div>
            </section>
            <div class="model-sc-rf">
              <div class="r"><b>模型与呈现</b><span>AstrBot 当前会话的模型、工具往返、流式、分段和插件装饰</span></div>
              <div class="r"><b>提示组成</b><span>在「治理与维护 → 任务提示词」查看 V2 附加层与背景规则；人格与历史在 AstrBot 管理。</span></div>
            </div>
            <div class="model-policy-link">
              <div><b>替换策略（时段换模）</b><small>当前已配置 ${rules} 条替换规则</small></div>
              <button type="button" class="button small" data-open-policy="${esc(task.kind)}">前往替换策略 ↗</button>
            </div>
          </div>
          <footer class="model-insp-foot">
            <button type="button" class="button small" data-model-action="discard" data-model-kind="${esc(task.kind)}" ${locked || draft.pending ? "disabled" : ""}>放弃</button>
            <span class="model-grow"></span>
            ${draft.stale && !draft.pending ? `<button type="button" class="button small" data-model-action="rebase" data-model-kind="${esc(task.kind)}">已核对，保留草稿</button>` : ""}
            <span class="wb-save-context"><button type="button" class="button small primary" data-model-action="save-one" data-model-kind="${esc(task.kind)}" ${locked || (!draft.pending && (draft.stale || !dirty(task, draft))) ? "disabled" : ""}>${draft.pending ? "核对保存" : "保存"}</button>${modelScopeHint(s, task, draft)}</span>
          </footer>
        </article>`,
    };
  }
  function taskInspectorSpec(s) {
    if (!s.expanded || !s.status) return null;
    const task = taskOf(s, s.expanded);
    if (!task) return null;
    if (coreReply(task)) return coreReplyInspectorSpec(s, task);
    const info = taskName(task.kind), draft = s.drafts.get(task.kind), locked = s.loading || s.busy || s.denied;
    if (!draft) return null;
    const fieldLocked = locked || !!draft.pending;
    const picked = s.selection.includes(task.kind), waiting = dirty(task, draft) && !draft.pending;
    const limit = limitFor(s, task, draft), limitMessage = limitHint(s, task, draft);
    const main = providerFor(task, draft), fallback = effective(task, draft, "fallback");
    const own = fields.some(name => !draft.inherited[name]);
    const testing = main && s.testing.has(main), result = main ? s.providerTests[main] : null;
    const timeoutVal = effective(task, draft, "timeout_seconds");
    const maxTokensVal = effective(task, draft, "max_output_tokens");
    const promptVal = draft.inherited.prompt ? task.fields.prompt.inherited_value : draft.values.prompt;
    const rules = ruleOf(s, task.kind).length;
    const refusalOn = draft.inherited.fallback_on_refusal ? !!task.fields.fallback_on_refusal.inherited_value : !!draft.values.fallback_on_refusal;
    const scopeVal = effective(task, draft, "replacement_scope");
    const advConflict = (draft.conflicts || []).some(name => ["timeout_seconds", "max_output_tokens", "prompt"].includes(name));
    const advOpen = Boolean(s.advOpen[task.kind] || limitMessage || advConflict);
    const badge = waiting ? '<span class="model-tag a">待保存</span>'
      : own ? '<span class="model-tag">单独设置</span>'
      : '<span class="model-tag n">跟随上级</span>';
    return {
      mode: "inspector",
      kind: task.kind,
      label: `配置 ${info.label}`,
      closeAttr: "data-inspector-close",
      extraClass: "model-inspector",
      html: `<article class="model-fncard open model-inspector-card${waiting ? " dirty" : ""}${picked ? " picked" : ""}" data-fncard="${esc(task.kind)}">
          ${inspectorHeader(s, task, info, draft, badge)}
          <div class="model-insp-body">
            ${conflictNotice(task, draft)}
            ${draft.pending ? '<div class="model-alert">上次保存结果未确认。请核对原操作，避免重复提交。</div>' : ""}
            <section class="model-insp-sec">
              <div class="model-insp-sec-h"><b>核心路由</b><span class="model-state ${draft.pending ? "warn" : ""}">${esc(draft.pending ? "待核对原保存" : adoption(task.application_state))}</span></div>
              <div class="model-fd">${fieldHeader("主模型提供方", `跟随上级时为 ${parentRoute(task.fields.provider)}`, draft.inherited.provider, "provider", task.kind, fieldLocked)}
                <span class="model-msel"><i style="background:${pillColor(main)}"></i><select id="${esc(fieldDomId(task.kind, "provider"))}" data-model-field="provider" data-model-kind="${esc(task.kind)}" aria-label="${esc(info.label)} 主模型提供方" ${fieldLocked ? "disabled" : ""}>${providerOptions(s, task, draft, "provider")}</select></span></div>
              <div class="model-fd">${fieldHeader("备用模型提供方", "主模型失败、超时或拒答时尝试一次", draft.inherited.fallback, "fallback", task.kind, fieldLocked)}
                <span class="model-msel"><i style="background:${pillColor(fallback)}"></i><select id="${esc(fieldDomId(task.kind, "fallback"))}" data-model-field="fallback" data-model-kind="${esc(task.kind)}" aria-label="${esc(info.label)} 备用模型提供方" ${fieldLocked ? "disabled" : ""}>${providerOptions(s, task, draft, "fallback")}</select></span></div>
              <div class="model-insp-probe">
                <div class="model-insp-route-preview"><span class="model-mono">${esc(main || "未设置")}${fallback ? " → " + esc(fallback) : ""}</span></div>
                <span class="model-res">${result ? `<span class="model-resk ${result.status === "ok" ? "ok" : "bad"}">${result.status === "ok" ? "✓" : "！"}</span>${esc(probeLabel(result))}` : testing ? '<span class="model-sp"></span>测试中' : ""}</span>
                <button type="button" class="button small" data-provider-test="${esc(main)}" ${!main || testing ? "disabled" : ""}>测试连接</button>
              </div>
            </section>
            <div class="model-adv${advOpen ? " is-open" : ""}"${advOpen ? " open" : ""}>
              <button type="button" class="model-adv-sum" data-adv-toggle="${esc(task.kind)}" aria-expanded="${advOpen}">
                <span class="model-adv-title"><span class="model-adv-chev" aria-hidden="true">▸</span><b>高级参数与附加提示词</b></span>
                <span class="model-adv-chips">
                  <span>超时 ${esc(timeoutVal)}s</span>
                  <span>上限 ${esc(maxTokensVal)}</span>
                  <span>${promptVal ? `提示词 ${promptVal.length} 字` : "提示词默认"}</span>
                  ${limitMessage ? '<span class="warn">上限提示</span>' : ""}
                </span>
              </button>
              <div class="model-adv-shell"><div class="model-adv-inner"${advOpen ? "" : ' aria-hidden="true" inert'}>
                <div class="model-adv-body">
                  <div class="model-duo">
                    <div class="model-fd">${fieldHeader("请求超时", "", draft.inherited.timeout_seconds, "timeout_seconds", task.kind, fieldLocked)}
                      <span class="model-unit"><input id="${esc(fieldDomId(task.kind, "timeout_seconds"))}" type="number" min="1" max="600" step="1" data-model-field="timeout_seconds" data-model-kind="${esc(task.kind)}" value="${esc(timeoutVal)}" ${fieldLocked ? "disabled" : ""}><i>秒</i></span></div>
                    <div class="model-fd">${fieldHeader("单次输出上限", "", draft.inherited.max_output_tokens, "max_output_tokens", task.kind, fieldLocked)}
                      <span class="model-unit"><input id="${esc(fieldDomId(task.kind, "max_output_tokens"))}" type="number" min="1" max="${limit}" step="1" data-model-field="max_output_tokens" data-model-kind="${esc(task.kind)}" value="${esc(maxTokensVal)}" ${fieldLocked ? "disabled" : ""}><i>令牌</i></span></div>
                  </div>
                  ${limitMessage ? `<div class="model-alert" role="alert">${esc(limitMessage)}</div>` : ""}
                  <div class="model-fd">${fieldHeader("任务附加提示词", `最多 8000 字`, draft.inherited.prompt, "prompt", task.kind, fieldLocked)}
                    <textarea id="${esc(fieldDomId(task.kind, "prompt"))}" rows="6" maxlength="8000" data-model-field="prompt" data-model-kind="${esc(task.kind)}" ${fieldLocked ? "disabled" : ""}>${esc(promptVal || "")}</textarea>
                    <span class="model-char-count" data-model-prompt-count="${esc(task.kind)}">${(promptVal || "").length} / 8000</span></div>
                </div>
              </div></div>
            </div>
            <div class="model-policy-link">
              <div><b>替换与拒答策略</b><small>替换规则 ${rules} 条 · 拒答替代 ${refusalOn ? "已开启" : "未开启"} · ${esc(scopeLabelText(scopeVal))}</small></div>
              <button type="button" class="button small" data-open-policy="${esc(task.kind)}">前往替换策略 ↗</button>
            </div>
          </div>
          <footer class="model-insp-foot">
            <button type="button" class="button small" data-model-action="discard" data-model-kind="${esc(task.kind)}" ${locked || draft.pending ? "disabled" : ""}>放弃</button>
            ${own && !draft.pending ? `<button type="button" class="button small" data-model-action="restore-one" data-model-kind="${esc(task.kind)}" ${locked ? "disabled" : ""}>全部恢复继承</button>` : ""}
            <span class="model-grow"></span>
            ${draft.stale && !draft.pending ? `<button type="button" class="button small" data-model-action="rebase" data-model-kind="${esc(task.kind)}">已核对，保留草稿</button>` : ""}
            <span class="wb-save-context"><button type="button" class="button small primary" data-model-action="save-one" data-model-kind="${esc(task.kind)}" ${locked || (!draft.pending && (draft.stale || !dirty(task, draft))) ? "disabled" : ""}>${draft.pending ? "核对保存" : "保存"}</button>${modelScopeHint(s, task, draft)}</span>
          </footer>
        </article>`,
    };
  }
  function providerView(s) { const providers = s.status?.providers || []; if (!providers.length) return '<p class="model-empty">尚未读取到模型提供方。</p>';
    const tasks = visible(s).filter(task => !coreReply(task)), filtered = !!(s.query || s.custom || s.lane !== "all" || s.providerFilter);
    const panels = providers.filter(provider => !s.providerFilter || s.providerFilter === provider.id).map(provider => {
      const capable = tasks.filter(task => providerSupportsTask(task, provider));
      const routed = tasks.filter(task => providerFor(task, s.drafts.get(task.kind)) === provider.id);
      if (filtered && !routed.length && s.providerFilter !== provider.id) return "";
      const caps = provider.supports_video_relay && !provider.capabilities.includes("video")
        ? [...provider.capabilities, "video_relay"] : provider.capabilities;
      return `<article class="model-provider-panel"><header><span><strong>${esc(provider.id)}</strong><em>${routed.length} / ${capable.length} 项任务走这里</em></span>
          <span class="model-state">上限 ${provider.max_output_tokens}</span></header>
        ${provider.model ? `<p class="model-provider-model">${esc(provider.model)}</p>` : ""}
        <p class="model-provider-caps">${caps.map(cap => `<small>${esc(capabilityLabel(cap))}</small>`).join("") || "<small>无能力声明</small>"}</p>
         <ul class="model-provider-tasks">${routed.map(task => { const info = taskName(task.kind);
           return `<li><button type="button" data-model-open="${esc(task.kind)}" aria-label="编辑 ${esc(info.label)} 的模型配置">${esc(info.label)}</button></li>`; }).join("") || '<li class="model-empty">还没有任务走这个模型提供方。</li>'}</ul>
         ${capable.length > routed.length ? `<p class="model-provider-note">还有 ${capable.length - routed.length} 项能力相符的任务由其他模型提供方承担。</p>` : ""}</article>`; }).filter(Boolean);
    return panels.length ? `<div class="model-providers">${panels.join("")}</div>` : '<p class="model-empty">没有匹配的任务。</p>'; }
  function body(s) { if (!s.status) return s.bad
    ? `<div class="model-empty" role="status"><p>任务配置尚未读取成功，请重新读取。</p><button type="button" class="button primary small" data-model-action="refresh" ${s.loading || s.busy ? "disabled" : ""}>重新读取</button></div>`
    : '<p class="model-empty">角色就绪后显示可用模型任务。</p>';
    if (!snapshot(s).length) return '<p class="model-empty">当前没有可用的模型任务。</p>';
    return s.view === "providers" ? providerView(s) : routingList(s); }
  function syncInput(kind) { const s = state; if (!root || !s) return;
    const slot = root.querySelector(".model-queue-slot");
    if (slot) {
      const hadQueue = Boolean(slot.querySelector?.(".model-queue"));
      const nextQueue = queue(s);
      slot.innerHTML = nextQueue;
      if (!hadQueue && nextQueue && useLiveDom() && !isReducedMotion()) {
        slot.querySelector?.(".model-queue")?.classList.add("is-entering");
      }
    }
    const task = taskOf(s, kind), draft = s.drafts.get(kind);
    const counter = root.querySelector(`[data-model-prompt-count="${kind}"]`);
    if (counter && task && draft) counter.textContent = `${String(effective(task, draft, "prompt") || "").length} / 8000`;
    const save = [...root.querySelectorAll('[data-model-action="save-one"]')].find(button => button.dataset.modelKind === kind);
    if (save && task && draft) { save.disabled = !draft.pending && (draft.stale || !dirty(task, draft)); save.textContent = draft.pending ? "核对保存" : "保存"; } }
  /* ---------- functional actions ---------- */
  async function refresh() { const s = state, ticket = version; if (!s || s.loading || s.busy) return; s.loading = true; s.notice = "正在读取任务配置…"; render();
    try { const data = await api("GET", taskPath, {layer: s.layer || "role"}, TASK_CONFIG_TIMEOUT_MS); if (!current(ticket, s)) return; setStatus(s, data); s.bad = false; s.denied = false; s.notice = "已读取当前任务配置。"; }
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
    const data = await api("GET", taskPath, {layer: s.layer || "role"}, TASK_CONFIG_TIMEOUT_MS);
    if (!current(ticket, s)) return false;
    setStatus(s, data); s.denied = false;
    const conflict = snapshot(s).find(task => s.drafts.get(task.kind)?.stale && !s.drafts.get(task.kind)?.pending);
    if (conflict) {
      s.sub = "cfg"; s.view = "tasks"; s.drawer = null; s.expanded = conflict.kind;
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
        if (!validatePatch(task, draft, patch)) { failed.push(`${taskName(kind).label}：${limitProblem(s, task, draft) || "字段校验未通过"}`); continue; }
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
  async function saveGlobalDefaults(s) {
    const g = globalDefaultsInfo(s);
    if (!g.editable || s.globalBusy || s.loading || s.denied || !globalDefaultsDirty(s)) return;
    const draft = s.globalDraft || { provider_id: g.provider_id, fallback_provider_id: g.fallback_provider_id };
    if (draft.provider_id && draft.fallback_provider_id === draft.provider_id) {
      s.globalBad = true;
      s.globalNotice = "全局备用模型不能与全局主模型相同，请更换备用模型或清空。";
      render();
      return;
    }
    const ticket = version;
    s.globalBusy = true; s.globalBad = false; s.globalNotice = "正在保存陪伴通用模型…"; render();
    try {
      const result = await api("POST", taskPath + "/defaults", {
        provider_id: draft.provider_id,
        fallback_provider_id: draft.fallback_provider_id,
      }, TASK_CONFIG_TIMEOUT_MS);
      if (!current(ticket, s)) return;
      if (result?.global_defaults && s.status) s.status.global_defaults = result.global_defaults;
      syncGlobalDraft(s, true);
      s.globalRestartRequired = Boolean(result?.restart_required);
      s.globalBad = false;
      const notes = [result?.restart_required
        ? "陪伴通用模型已保存；当前插件正在运行，重启插件后新全局默认将完全生效。"
        : "陪伴通用模型已保存，所有保持「跟随上级」的任务将采用新的全局默认。"];
      if (result?.fallback_cleared_duplicate) notes.push("全局备用与主模型重复，已自动清空全局备用。");
      s.globalNotice = notes.join(" ");
      try {
        const fresh = await api("GET", taskPath, { layer: s.layer || "role" }, TASK_CONFIG_TIMEOUT_MS);
        if (!current(ticket, s)) return;
        setStatus(s, fresh);
        if (result?.global_defaults && (!s.status.global_defaults || !s.status.global_defaults.editable)) {
          s.status.global_defaults = result.global_defaults;
        }
        syncGlobalDraft(s, true);
      } catch {
        // 保存已成功；静默刷新失败时保留已保存的全局默认与成功提示
      }
    } catch (err) {
      if (!current(ticket, s)) return;
      s.globalBad = true;
      s.globalNotice = fail(err, "陪伴通用模型保存失败，请检查配置后重试。");
    } finally {
      s.globalBusy = false;
      if (current(ticket, s)) render();
    }
  }
  const slotDraftOf = (s, g) => {
    const raw = s.slotDraft[g];
    if (typeof raw === "string") return { provider: raw || "__keep__", fallback: "__keep__" };
    if (raw && typeof raw === "object") return { provider: raw.provider || "__keep__", fallback: raw.fallback || "__keep__" };
    return { provider: "__keep__", fallback: "__keep__" };
  };
  function assign(s, providerId) { const kinds = selected(s); if (!providerId || !kinds.length) return;
    const skipped = []; let changed = 0;
    for (const kind of kinds) { const task = taskOf(s, kind), draft = s.drafts.get(kind); if (!task || !draft || draft.pending || draft.stale) continue;
      if (!providerSupportsTask(task, s.status.providers.find(p => p.id === providerId))) { skipped.push(taskName(kind).label); continue; }
      if (draft.values.provider === providerId && !draft.inherited.provider) continue;
      draft.inherited.provider = false; draft.values.provider = providerId; draft.touched = true; fitLimit(s, task, draft); changed++; }
    s.bad = skipped.length > 0;
    s.notice = changed ? `已把 ${changed} 项任务指派给 ${providerId}${skipped.length ? `；${skipped.length} 项能力不符已跳过：${skipped.join("、")}` : ""}。记得保存。`
      : (skipped.length ? `没有任务被改动；能力不符：${skipped.join("、")}。` : "没有任务被改动。");
    render(); }
  function assignFallback(s, choice) { const kinds = selected(s); if (!choice || !kinds.length) return;
    const skipped = [], providerObj = !choice.startsWith("__") ? s.status.providers.find(p => p.id === choice) : null;
    let changed = 0;
    for (const kind of kinds) {
      const task = taskOf(s, kind), draft = s.drafts.get(kind);
      if (!task || !draft || draft.pending || draft.stale) continue;
      if (choice === "__inherit__") {
        if (draft.inherited.fallback) continue;
        draft.inherited.fallback = true; delete draft.chosen.fallback; draft.touched = true; fitLimit(s, task, draft); changed++;
      } else if (choice === "__none__") {
        if (!draft.inherited.fallback && draft.values.fallback === "" && !task.fields.fallback?.cleared && !task.fields.fallback?.replaced) continue;
        draft.inherited.fallback = false; draft.values.fallback = "";
        if (task.fields.fallback?.cleared || task.fields.fallback?.replaced) draft.chosen.fallback = true;
        draft.touched = true; fitLimit(s, task, draft); changed++;
      } else {
        if (!providerSupportsTask(task, providerObj)) { skipped.push(`${taskName(kind).label}（能力不符）`); continue; }
        const curPrimary = providerFor(task, draft);
        if (curPrimary && curPrimary === choice) { skipped.push(`${taskName(kind).label}（与主模型相同）`); continue; }
        if (!draft.inherited.fallback && draft.values.fallback === choice && !task.fields.fallback?.replaced && !task.fields.fallback?.cleared) continue;
        draft.inherited.fallback = false; draft.values.fallback = choice;
        if (task.fields.fallback?.cleared || task.fields.fallback?.replaced) draft.chosen.fallback = true;
        draft.touched = true; fitLimit(s, task, draft); changed++;
      }
    }
    const label = choice === "__inherit__" ? "跟随上级默认" : choice === "__none__" ? "不使用备用模型" : choice;
    s.bad = skipped.length > 0 && changed === 0;
    s.notice = changed
      ? `已将 ${changed} 项任务的备用模型设为「${label}」${skipped.length ? `；${skipped.length} 项已跳过：${skipped.join("、")}` : ""}。记得保存。`
      : (skipped.length ? `没有任务被改动；已跳过：${skipped.join("、")}。` : "没有任务被改动。");
    render(); }
  function setFieldInherit(s, kind, inheritName, checked) {
    const draft = s.drafts.get(kind), task = taskOf(s, kind);
    if (!draft || draft.pending || !task || !taskFields(task).includes(inheritName)) return;
    if (checked && inheritName === "provider" && unfollowableParent(s, task)) {
      s.notice = unfollowableNotice(unfollowableParent(s, task));
      s.bad = false; render(); return;
    }
    draft.inherited[inheritName] = checked;
    if (checked) delete draft.chosen[inheritName];
    draft.touched = true;
    if (checked && (inheritName === "provider" || inheritName === "fallback")) {
      const adjusted = fitLimit(s, task, draft);
      if (adjusted) { s.notice = limitNotice(task, draft, adjusted); s.bad = false; }
    }
    render();
  }
  function discard(s, kinds) { let count = 0;
    for (const kind of kinds) { const task = taskOf(s, kind), draft = s.drafts.get(kind); if (!task || !draft || draft.pending) continue;
      s.drafts.set(kind, newDraft(task)); count++; }
    s.bad = false; s.notice = count ? `已放弃 ${count} 项任务的草稿。` : '没有可放弃的草稿。'; render(); }
  function restoreInherit(s, kinds) { let count = 0; const blocked = [];
    for (const kind of kinds) { const task = taskOf(s, kind), draft = s.drafts.get(kind); if (!task || !draft || draft.pending) continue;
      /* Same guard as the per-field entry points: a blank parent provider keeps
         its failure, so following it would only produce an unsaveable draft. */
      const blockedParent = unfollowableParent(s, task);
      if (blockedParent) {
        blocked.push(`${taskName(kind).label}（${blockedParent.inherited_value ? "上级模型不可用" : "上级主模型为空"}）`);
        continue;
      }
      /* Every field, the output limit included, follows the parent again, so
         there is nothing to re-fit: the ceiling comes from the same layer. */
      for (const name of taskFields(task)) { draft.inherited[name] = true; delete draft.chosen[name]; }
      draft.touched = true; count++; }
    s.bad = false;
    s.notice = (count ? `已把 ${count} 项任务改为跟随上级配置，保存后生效。` : '没有可调整的任务。')
      + (blocked.length ? `${count ? "" : " "}${blocked.length} 项已跳过：${blocked.join("、")}。` : "");
    render(); }
  function stepInspector(s, delta) {
    if (!s.expanded) return;
    const list = visible(s);
    const idx = list.findIndex(item => item.kind === s.expanded);
    const next = list[idx + delta];
    if (next) { s.expanded = next.kind; render(); }
  }
  /* ---------- functional events ---------- */
  function fnInput(event) { const s = state; if (!s || s.busy || s.loading || s.denied) return; const target = event.target;
    /* The policy pane owns toggles and selects, so it gets every input event. */
    if (s.sub === "pol") { event.type === "change" ? ruleChange(s, event) : ruleInput(s, event); return; }
    if (target.matches("[data-global-default-field]")) {
      if (s.globalBusy) return;
      const g = globalDefaultsInfo(s);
      s.globalDraft ||= { provider_id: g.provider_id, fallback_provider_id: g.fallback_provider_id };
      s.globalDraft[target.dataset.globalDefaultField] = target.value;
      s.globalNotice = ""; s.globalBad = false;
      render(); return;
    }
    if (target.matches("[data-model-search]")) { s.query = target.value; render(); const next = root.querySelector("[data-model-search]");
      if (next) { next.focus(); next.setSelectionRange(next.value.length, next.value.length); } return; }
    if (target.matches("[data-model-provider-filter]")) { if (event.type !== "change") return; s.providerFilter = target.value || null; render(); return; }
    if (target.matches("[data-model-slot]")) {
      const slot = slotDraftOf(s, target.dataset.modelSlot);
      s.slotDraft[target.dataset.modelSlot] = { ...slot, provider: target.value || "__keep__" };
      render(); return;
    }
    if (target.matches("[data-model-slot-fallback]")) {
      const slot = slotDraftOf(s, target.dataset.modelSlotFallback);
      s.slotDraft[target.dataset.modelSlotFallback] = { ...slot, fallback: target.value || "__keep__" };
      render(); return;
    }
    if (target.matches("[data-model-assign]")) { assign(s, target.value); target.value = ""; return; }
    if (target.matches("[data-model-assign-fallback]")) { assignFallback(s, target.value); target.value = ""; return; }
    const kind = target.dataset.modelKind;
    if (!kind) return;
    const inheritName = target.dataset.modelInherit;
    if (inheritName) { setFieldInherit(s, kind, inheritName, Boolean(target.checked)); return; }
    const name = target.dataset.modelField; if (!name || !fields.includes(name)) return;
    const draft = s.drafts.get(kind), task = taskOf(s, kind); if (!draft || !task || draft.pending) return;
    if (!taskFields(task).includes(name)) return;
    if (name === "provider" && target.value === "__inherit__") {
      setFieldInherit(s, kind, "provider", true); return;
    }
    draft.values[name] = ["max_output_tokens", "timeout_seconds"].includes(name)
      ? (Number.isFinite(target.valueAsNumber) ? target.valueAsNumber : draft.values[name]) : target.value;
    draft.inherited[name] = false;
    /* The user may deliberately choose the same value the projection shows
       (clearing or confirming a recovered row); remember it so the save emits
       it instead of leaving the old stored row in place. */
    if ((task.fields[name]?.cleared || task.fields[name]?.replaced)
        && draft.values[name] === task.fields[name].value) draft.chosen[name] = true;
    const adjusted = (name === "provider" || name === "fallback") && fitLimit(s, task, draft);
    if (adjusted) { s.notice = limitNotice(task, draft, adjusted); s.bad = false; }
    draft.touched = true;
    if (event.type === "change") {
      if (deferChangeRender && ["prompt", "timeout_seconds", "max_output_tokens"].includes(name)) {
        deferredRenderPending = true;
        syncInput(kind);
      } else {
        render();
      }
    } else syncInput(kind); }
  function fnClick(event) { const s = state; if (!s) return;
    if (event.target.closest("[data-drawer-close]")) { s.drawer = null; render(); return; }
    if (event.target.closest("[data-inspector-close]")) { s.expanded = null; render(); return; }
    const step = event.target.closest("[data-inspector-step]");
    if (step) { stepInspector(s, Number(step.dataset.inspectorStep) || 0); return; }
    const openPol = event.target.closest("[data-open-policy]");
    if (openPol) { s.pol = openPol.dataset.openPolicy; s.sub = "pol"; s.expanded = null; s.drawer = null; render(); return; }
    const pill = event.target.closest("[data-provider-pill]");
    if (pill) { s.expanded = null; s.drawer = s.drawer === pill.dataset.providerPill ? null : pill.dataset.providerPill; render(); return; }
    const goto = event.target.closest("[data-drawer-goto]");
    if (goto) { s.drawer = null; s.sub = "cfg"; s.view = "tasks"; s.providerFilter = goto.dataset.drawerGoto; render(); return; }
    const sub = event.target.closest("[data-model-sub]");
    if (sub) { s.sub = sub.dataset.modelSub === "map" ? "cfg" : sub.dataset.modelSub; s.expanded = null; render(); return; }
    const layer = event.target.closest("[data-model-layer]");
    if (layer) {
      const selectedLayer = layer.dataset.modelLayer;
      if (selectedLayer === s.layer || selectedLayer === "window" && !windowAvailable) return;
      const pending = [...s.drafts.entries()].some(([kind, draft]) => draft.pending || taskOf(s, kind) && dirty(taskOf(s, kind), draft));
      if (pending) { s.bad = true; s.notice = "请先保存或放弃当前范围的改动，再切换配置作用域。"; render(); return; }
      s.layer = selectedLayer; s.status = null; s.drafts.clear(); s.selection = []; s.expanded = null; s.drawer = null; refresh(); return;
    }
    const policyMode = event.target.closest("[data-model-pm]");
    if (policyMode) { s.pm = policyMode.dataset.modelPm; render(); return; }
    const topAction = event.target.closest("[data-model-action]")?.dataset.modelAction;
    if (topAction === "refresh") { refresh(); return; }
    if (s.sub === "pol") { ruleClick(s, event); return; }
    if (event.target.closest("[data-global-default-save]")) { void saveGlobalDefaults(s); return; }
    if (event.target.closest("[data-global-default-reset]")) { syncGlobalDraft(s, true); s.globalNotice = ""; s.globalBad = false; render(); return; }
    if (event.target.closest("[data-model-quick-toggle]")) {
      const opening = !s.quickOpen;
      s.quickOpen = opening;
      if (!toggleQuickBoxLive(s, opening)) render();
      return;
    }
    const quickGroup = event.target.closest("[data-quick-group]");
    if (quickGroup) {
      const g = quickGroup.dataset.quickGroup;
      const wasOpen = s.quickOpen;
      s.quickOpen = true;
      s.expOpen[g] = true;
      if (!wasOpen && toggleQuickBoxLive(s, true, g)) return;
      render();
      flashQuickCard(g);
      return;
    }
    if (event.target.closest("[data-model-legend-toggle]")) {
      s.legendOpen = !s.legendOpen;
      if (!toggleLegendLive(s)) render();
      return;
    }
    const groupToggle = event.target.closest("[data-model-group-toggle]");
    if (groupToggle) {
      const gk = groupToggle.dataset.modelGroupToggle;
      s.groupCollapsed[gk] = !s.groupCollapsed[gk];
      if (!toggleRouteGroupLive(s, gk)) render();
      return;
    }
    const advToggle = event.target.closest("[data-adv-toggle]");
    if (advToggle) {
      event.preventDefault?.();
      const k = advToggle.dataset.advToggle;
      const opening = !s.advOpen[k];
      s.advOpen[k] = opening;
      if (!toggleAdvLive(s, k, opening)) {
        render();
        if (opening) scrollAdvIntoView();
      }
      return;
    }
    const resetInherit = event.target.closest("[data-model-inherit-reset]");
    if (resetInherit) { setFieldInherit(s, resetInherit.dataset.modelKind, resetInherit.dataset.modelInheritReset, true); return; }
    const exp = event.target.closest("[data-model-exp]");
    if (exp) {
      const k = exp.dataset.modelExp;
      s.expOpen[k] = !s.expOpen[k];
      rememberExpanded(s, k);
      if (!toggleQuickExpLive(s, k)) render();
      return;
    }
    const gpick = event.target.closest("[data-model-gpick]");
    if (gpick) { const k = gpick.dataset.modelGpick;
      const kinds = (k.startsWith("lane:") ? snapshot(s).filter(t => t.lane === k.slice(5))
        : (taskGroups(s).find(([name]) => name === k) || [null, []])[1].map(([kind]) => taskOf(s, kind)).filter(Boolean))
        .filter(t => !coreReply(t) && shown(s, t)).map(t => t.kind);
      if (kinds.length) { const all = kinds.every(kind => s.selection.includes(kind));
        s.selection = all ? s.selection.filter(kind => !kinds.includes(kind)) : Array.from(new Set(s.selection.concat(kinds)));
        s.rule.report = null;
        if (!syncSelectionInPlace(s)) render(); } return; }
    const mapPick = event.target.closest("[data-map-pick]");
    if (mapPick) { const kind = mapPick.dataset.mapPick;
      s.selection = s.selection.includes(kind) ? s.selection.filter(item => item !== kind) : s.selection.concat(kind);
      s.rule.report = null; if (!syncSelectionInPlace(s)) render(); return; }
    /* Clicking any task opens its right-side Inspector Drawer cleanly. */
    const openTask = event.target.closest("[data-model-open]");
    if (openTask) { const kind = openTask.dataset.modelOpen, wasTaskList = s.sub === "cfg" && s.view === "tasks";
      s.sub = "cfg"; s.view = "tasks"; s.drawer = null; s.expanded = wasTaskList && s.expanded === kind ? null : kind; render();
      return; }
    const mapClear = event.target.closest("[data-map-clear]");
    if (mapClear) { s.selection = []; s.rule.report = null; if (!syncSelectionInPlace(s)) render(); return; }
    const reportClose = event.target.closest("[data-map-report-close]");
    if (reportClose) { if (!closeReportLive(s)) { s.rule.report = null; render(); } return; }
    const mapTest = event.target.closest("[data-map-test]"); if (mapTest) { runMapTest(s); return; }
    const providerTest = event.target.closest("[data-provider-test]");
    if (providerTest) { probeProvider(s, providerTest.dataset.providerTest); return; }
    const cm = event.target.closest("[data-model-cm]"); if (cm) { s.quickOpen = cm.dataset.modelCm === "quick"; render(); return; }
    const av = event.target.closest("[data-model-av]"); if (av) { s.av = av.dataset.modelAv; render(); return; }
    const af = event.target.closest("[data-model-af]");
    if (af) { const id = af.dataset.modelAf; s.custom = id === "all" ? null : id === "own" ? "custom" : id; render(); return; }
    const slotApply = event.target.closest("[data-model-slot-apply]"); if (slotApply) { applySlot(s, slotApply.dataset.modelSlotApply); return; }
    const pick = event.target.closest("[data-model-pick]");
    if (pick) { const kind = pick.dataset.modelPick; s.selection = s.selection.includes(kind) ? s.selection.filter(item => item !== kind) : s.selection.concat(kind); s.rule.report = null; if (!syncSelectionInPlace(s)) render(); return; }
    const custom = event.target.closest("[data-model-custom]");
    if (custom) { const id = custom.dataset.modelCustom; s.custom = id === "all" ? null : id; render(); return; }
    const lane = event.target.closest("[data-model-lane]");
    if (lane) { s.lane = lane.dataset.modelLane; render(); return; }
    const view = event.target.closest("[data-model-view]");
    if (view) { s.view = view.dataset.modelView; render(); return; }
    const action = topAction; if (!action || s.busy) return;
    if (action === "clear-selection") { s.selection = []; s.rule.report = null; if (!syncSelectionInPlace(s)) render(); return; }
    if (action === "inherit") { restoreInherit(s, selected(s)); return; }
    if (action === "discard-all") { discard(s, selected(s).length ? selected(s) : snapshot(s).map(task => task.kind)); return; }
    if (action === "save-all") { const kinds = snapshot(s).map(task => task.kind); saveTasks(s, kinds); return; }
    const kind = event.target.closest("[data-model-action]")?.dataset.modelKind; if (!kind || s.busy || s.loading) return;
    const draft = s.drafts.get(kind); if (!draft) return;
    if (action === "save-one") { saveTasks(s, [kind]); return; }
    if (action === "restore-one") { restoreInherit(s, [kind]); return; }
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
  const layaDraftFrom = data => ({enabled: !!data.enabled, mode: data.mode || "local",
    request_format: data.request_format || "native", remote_model: data.remote_model || "",
    endpoint: data.endpoint || "", local_model_path: data.local_model_path || "",
    api_key: "", proxy: data.proxy || "", timeout_seconds: Number(data.timeout_seconds) || 20,
    followup_confidence_threshold: Math.max(0, Math.min(100, Number(data.followup_confidence_threshold) || 0)),
    decision_fields: Object.fromEntries(LAYA_DECISION_FIELDS.map(([key]) => [key, data.decision_fields?.[key] !== false]))});
  const layaProxyValue = l => String(l.draft?.proxy ?? "").trim();
  const layaChanges = l => { const changes = {};
    ["enabled", "mode", "request_format", "remote_model", "endpoint", "timeout_seconds", "local_model_path", "followup_confidence_threshold"].forEach(key => {
      const current = key === "request_format" ? l.data[key] || "native"
        : key === "remote_model" || key === "local_model_path" ? l.data[key] || ""
        : key === "followup_confidence_threshold" ? Math.max(0, Math.min(100, Number(l.data[key]) || 0))
        : l.data[key];
      if (l.draft[key] !== current) changes[key] = l.draft[key]; });
    if (LAYA_DECISION_FIELDS.some(([key]) => l.draft.decision_fields[key] !== (l.data.decision_fields?.[key] !== false)))
      changes.decision_fields = l.draft.decision_fields;
    if (l.draft.api_key) changes.api_key = l.draft.api_key;
    /* 代理不是密钥：清空输入框就是「直连」，所以空串也要能提交；只有与已保存地址
       不同才算改动，否则保存按钮会一直亮着。 */
    if (layaProxyValue(l) !== String(l.data.proxy || "").trim()) changes.proxy = layaProxyValue(l);
    return changes; };
  const layaDirty = l => !!l.data && !!l.draft && Object.keys(layaChanges(l)).length > 0;
  const layaEndpointPath = value => String(value || "").trim().split(/[?#]/)[0].replace(/\/+$/, "");
  /* 两种官方服务都使用 SystemOne；按钮只填草稿，API Key 与保存仍由用户确认。 */
  const LAYA_PRESETS = {
    laya: {label: "Laya 官方", endpoint: "http://127.0.0.1:8000/v1/systemone", remote_model: "multilingual"},
    typesafe: {label: "TypeSafe Jev 官方", endpoint: "https://api.typesafe.ai/v1/systemone", remote_model: "jev-latest"},
  };
  function layaApplyPreset(s, name) { const l = s.laya, preset = LAYA_PRESETS[name];
    if (!preset || !l.draft || l.loading || l.busy) return;
    l.draft.endpoint = preset.endpoint; l.draft.remote_model = preset.remote_model; l.draft.request_format = "native";
    l.bad = false; l.notice = `已填入「${preset.label}」的 SystemOne 地址与模型名，请填写 API Key 后保存。`; render(); }
  function layaProtocolAlert(l, mode) { const path = layaEndpointPath(l.draft?.endpoint);
    if (mode === "jev" && path.endsWith("/v1/systemone"))
      return '<div class="model-alert">当前接入方式是「OpenAI 兼容网关」，但地址以 <code>/v1/systemone</code> 结尾。TypeSafe 官方 Jev 请切换到「远程 HTTP 服务」；只有提供 <code>/v1/chat/completions</code> 的第三方中转才使用此方式。</div>';
    if (mode === "remote" && path.endsWith("/v1/chat/completions"))
      return '<div class="model-alert">当前接入方式是「远程 HTTP 服务」，但地址以 <code>/v1/chat/completions</code> 结尾。请改为 <code>/v1/systemone</code>，或切换到「OpenAI 兼容网关」；后端会拒绝这种跨协议配置。</div>';
    return ""; }
  const isGroupLaya = s => s?.status?.scope_kind === "group" || s?.laya?.logsScopeKind === "group" || s?.laya?.logsUnavailable === true;
  const layaLogsParams = s => (isGroupLaya(s) ? undefined : {scope: s?.laya?.logsScope || "all"});
  async function layaRefresh(s) { const l = s.laya, ticket = version; if (l.loading) return; l.loading = true; l.notice = "正在读取 Laya 配置…"; render();
    try { const [config, history] = await Promise.allSettled([api("GET", path + "/laya"), api("GET", path + "/laya/logs", layaLogsParams(s))]); if (!current(ticket, s)) return;
      if (config.status === "rejected") throw config.reason;
      const data = config.value;
      if (!layaDirty(l) || l.stale) l.draft = layaDraftFrom(data); l.data = {...data, decision_fields: layaDraftFrom(data).decision_fields};
      l.logs = history.status === "fulfilled" ? history.value.events || [] : [];
      l.logsUnavailable = history.status === "fulfilled" && history.value.available === false;
      l.logsScopeKind = history.status === "fulfilled" ? history.value.scope_kind || "" : "";
      if (history.status === "fulfilled" && !isGroupLaya(s) && ["all", "current"].includes(history.value.scope)) l.logsScope = history.value.scope;
      l.logsError = history.status === "rejected";
      l.loaded = true; l.stale = false; l.bad = false;
      l.notice = l.logsError ? "Laya 配置已读取；近期记录暂不可用。" : "已读取 Laya 接入状态。"; }
    catch (err) { if (!current(ticket, s)) return; l.bad = true; l.notice = fail(err, "无法读取 Laya 配置。"); }
    finally { l.loading = false; if (current(ticket, s)) render(); } }
  async function layaSwitchLogsScope(s, nextScope) {
    const l = s.laya;
    if (!["all", "current"].includes(nextScope) || l.logsScope === nextScope || l.loading || isGroupLaya(s)) return;
    const ticket = version;
    l.logsScope = nextScope;
    l.logsError = false;
    l.logsLoading = true;
    render();
    try {
      const history = await api("GET", path + "/laya/logs", {scope: nextScope});
      if (!current(ticket, s) || l.logsScope !== nextScope) return;
      l.logs = history.events || [];
      l.logsUnavailable = history.available === false;
      l.logsScopeKind = history.scope_kind || "";
      l.logsError = false;
    } catch {
      if (!current(ticket, s) || l.logsScope !== nextScope) return;
      l.logsError = true;
    } finally {
      if (current(ticket, s) && l.logsScope === nextScope) { l.logsLoading = false; render(); }
    }
  }
  async function layaSave(s) { const l = s.laya; if (l.busy || l.loading || !l.data || l.stale || !layaDirty(l)) return;
    /* 保存时总是带上代理地址（含空串）：空串就是直连，不能让「没提交」被误读成
       「保持原值」。 */
    const ticket = version, changes = {...layaChanges(l), proxy: layaProxyValue(l)}; l.busy = true; l.notice = "正在保存 Laya 配置…"; render();
    try { const data = await api("POST", path + "/laya/save", {revision: l.data.revision, changes}); if (!current(ticket, s)) return;
      l.data = data; l.draft = layaDraftFrom(data); l.stale = false; l.bad = false; l.notice = "Laya 配置已保存。"; }
    catch (err) { if (!current(ticket, s)) return; l.bad = true; l.notice = fail(err, "Laya 配置保存未完成。"); }
    finally { l.busy = false; if (current(ticket, s)) render(); } }
  /* 远程服务返回的 reason 已由后端脱敏，比 codes 里的通用文案更有诊断价值；
     状态码解释只在 Laya 这里使用，避免影响其他配置页的错误提示。 */
  const LAYA_HTTP_HINTS = {
    401: "鉴权失败（401 Unauthorized），请核对 API Key 是否有效或已过期",
    403: "服务端拒绝访问（403 Forbidden），请核对 API Key、访问权限或服务端白名单",
    404: "接口路径不存在（404 Not Found），请核对服务地址是否带有正确的接口前缀",
    407: "代理要求先完成鉴权（407 Proxy Authentication Required），请核对代理账号与密码",
    429: "请求过于频繁或额度已用尽（429 Too Many Requests），请稍后重试或检查服务配额",
    451: "服务端按地区或法律限制拒绝（451 Unavailable For Legal Reasons），不是插件故障；可更换网络出口，或填写「代理地址」后重试",
  };
  function layaHttpHint(status) {
    if (LAYA_HTTP_HINTS[status]) return LAYA_HTTP_HINTS[status];
    if (status === 500) return "服务端内部错误（500 Internal Server Error），请稍后重试或联系服务提供方";
    if (status === 502) return "网关未能从上游取得有效响应（502 Bad Gateway），请稍后重试";
    if (status === 503) return "服务暂时不可用（503 Service Unavailable），请稍后重试";
    if (status === 504) return "网关等待上游响应超时（504 Gateway Timeout），请稍后重试";
    if (status >= 500) return `服务端返回服务器错误（${status}），请稍后重试或联系服务提供方`;
    return `服务端返回 HTTP ${status}，请核对服务地址、凭据与网络出口`;
  }
  function layaErrorText(err, fallback) {
    const detail = String(err?.reason || "").trim();
    const http = /^Laya HTTP (\d{3})/.exec(detail);
    if (http) {
      const status = Number(http[1]);
      const body = detail.slice(http[0].length).replace(/^[：:\s·]+/, "").slice(0, 200);
      return `Laya HTTP ${status} · ${layaHttpHint(status)}${body ? `；服务端原文：${body}` : ""}`;
    }
    if (detail && !CODE.test(detail)) return detail;
    return errors[err?.code] || errors[err?.message] || fallback;
  }
  async function layaTest(s) { const l = s.laya; if (l.busy || l.loading || !l.data) return;
    if (layaDirty(l)) { l.bad = true; l.notice = "请先保存当前 Laya 配置，再执行连接或模型下载测试。"; render(); return; }
    if ((l.draft?.mode || l.data.mode) === "local" && l.data.local_runtime_state === "gpu_unavailable") {
      l.bad = true;
      l.notice = "本地 Laya 无法测试：当前 AstrBot Python 的 PyTorch 不支持 CUDA。请安装 CUDA 版 PyTorch 并重启，或切换到远程 HTTP 服务。";
      render(); return;
    }
    const ticket = version; l.busy = true; l.test = {status: "testing"}; l.notice = l.data.mode === "local" ? "正在调用官方 Router；首次测试会自动下载检查点…" : l.data.mode === "jev" ? "正在调用 OpenAI 兼容网关…" : l.data.mode === "workers_ai" ? "正在调用 Cloudflare Workers AI…" : "正在调用官方 SystemOne HTTP 接口…"; render();
    try { const result = await api("POST", path + "/laya/test", {}, 600000); if (!current(ticket, s)) return;
      if (result?.status === "failed") {
        l.test = result; l.bad = true; l.notice = layaErrorText(result, "Laya 连接测试未完成。");
        render(); await layaRefreshQuiet(s, ticket); return;
      }
      l.test = result; l.notice = "Laya 调用完成；当前模式和检查点已验证。"; l.bad = false; await layaRefreshQuiet(s, ticket); }
    catch (err) { if (!current(ticket, s)) return; l.test = {status: "unknown", code: err.code || err.message, reason: err.reason || ""}; l.bad = true; l.notice = layaErrorText(err, "Laya 连接测试未完成。");
      /* 先把失败原因画出来，再补拉运行记录；日志读取慢时用户也不会一直停在等待态。 */
      render(); await layaRefreshQuiet(s, ticket); }
    finally { l.busy = false; if (current(ticket, s)) render(); } }
  async function layaRefreshQuiet(s, ticket) { const [config, history] = await Promise.allSettled([api("GET", path + "/laya"), api("GET", path + "/laya/logs", layaLogsParams(s))]);
    if (!current(ticket, s)) return;
    if (config.status === "fulfilled") { s.laya.data = config.value; s.laya.draft = layaDraftFrom(config.value); }
    if (history.status === "fulfilled") {
      s.laya.logs = history.value.events || [];
      s.laya.logsUnavailable = history.value.available === false;
      s.laya.logsScopeKind = history.value.scope_kind || "";
      if (!isGroupLaya(s) && ["all", "current"].includes(history.value.scope)) s.laya.logsScope = history.value.scope;
      s.laya.logsError = false;
    }
    else s.laya.logsError = true; }
  /* ---------- DeepSeek search binding ---------- */
  const searchDirty = x => !!x.data && !!x.draft
    && (x.draft.deepseek_provider_id !== x.data.deepseek_provider_id
        || (x.draft.source_priority || "auto") !== (x.data.source_priority || "auto"));
  async function searchRefresh(s) { const x = s.search, ticket = version; if (x.loading || x.busy) return;
    x.loading = true; x.notice = "正在读取搜索服务配置…"; render();
    try { const data = await api("GET", path + "/search"); if (!current(ticket, s)) return;
      if (!searchDirty(x) || x.stale) x.draft = {deepseek_provider_id: data.deepseek_provider_id || "", source_priority: data.source_priority || "auto"};
      x.data = data; x.loaded = true; x.stale = false; x.bad = false;
      x.notice = data.provider_available ? "已读取 DeepSeek 搜索 Provider。" : "当前选择的 Provider 未加载或已失效；搜索会改用 AstrBot 里配置的搜索服务。";
    } catch (err) { if (!current(ticket, s)) return; x.bad = true; x.notice = fail(err, "无法读取搜索服务配置。"); }
    finally { x.loading = false; if (current(ticket, s)) render(); } }
  async function searchSave(s) { const x = s.search; if (x.busy || x.loading || !x.data || x.stale || !searchDirty(x)) return;
    const ticket = version, changes = {deepseek_provider_id: x.draft.deepseek_provider_id,
      source_priority: x.draft.source_priority || "auto"};
    x.busy = true; x.notice = "正在保存搜索服务配置…"; render();
    try { const data = await api("POST", path + "/search/save", {revision: x.data.revision, changes}); if (!current(ticket, s)) return;
      x.data = data; x.draft = {deepseek_provider_id: data.deepseek_provider_id || "", source_priority: data.source_priority || "auto"}; x.stale = false; x.bad = false;
      x.notice = data.provider_available ? "搜索 Provider 已保存，后续搜索立即生效。" : "配置已保存，但 Provider 当前不可用；搜索会改用 AstrBot 里配置的搜索服务。";
    } catch (err) { if (!current(ticket, s)) return; x.bad = true; x.notice = fail(err, "搜索服务配置保存未完成。"); }
    finally { x.busy = false; if (current(ticket, s)) render(); } }
  function searchPane(s) { const x = s.search, data = x.data, draft = x.draft || {deepseek_provider_id: "", source_priority: "auto"};
    const providers = data?.providers || [], selected = draft.deepseek_provider_id || "";
    const unavailable = selected && !providers.some(item => item.id === selected);
    return `<div class="model-subpane model-laya-pane">
      <div class="model-card"><div class="model-phead"><div class="model-grow"><span class="model-eb">网页搜索与内容抓取</span><h3>DeepSeek 搜索服务</h3><p class="model-note">搜索使用 DeepSeek 官方 Search / Fetch API，不对应聊天模型任务。API 密钥由 AstrBot Provider 管理；V2 只保存 Provider 标识。优先级决定两条来源谁先试：自动＝优先 DeepSeek Provider（未选则 AstrBot 网页搜索）、优先 AstrBot＝先 AstrBot 网页搜索、优先 DeepSeek＝先 DeepSeek Provider。首选不可用时自动改用另一条。</p></div><button type="button" class="button small" data-search-refresh ${x.loading || x.busy ? "disabled" : ""}>刷新</button></div>
        <label class="model-fd"><span class="model-cap">DeepSeek API 凭据来源<em>只列出已加载且配置了 api.deepseek.com 的 Provider</em></span><select data-search-provider ${x.loading || x.busy || !data ? "disabled" : ""}>
          <option value="" ${selected ? "" : "selected"}>自动选择</option>${providers.map(item => `<option value="${esc(item.id)}" ${selected === item.id ? "selected" : ""}>${esc(item.name)} · ${esc(item.id)}</option>`).join("")}
          ${unavailable ? `<option value="${esc(selected)}" selected>${esc(selected)} · 当前不可用</option>` : ""}</select></label>
        ${data && !providers.length ? '<p class="model-note">未发现可用的 DeepSeek Provider。可在 AstrBot 模型提供方中配置官方 API，再刷新此页。</p>' : ""}
        ${unavailable ? '<div class="model-alert">已选 Provider 当前不可用；若 AstrBot 网页搜索已配置，搜索会改用它。</div>' : ""}
        <label class="model-fd"><span class="model-cap">搜索来源优先级<em>只影响“先试谁”，首选不可用时自动改用另一条</em></span><select data-search-priority ${x.loading || x.busy || !data ? "disabled" : ""}>
          ${[["auto","自动"],["astrbot","优先 AstrBot 网页搜索"],["deepseek","优先 DeepSeek Provider"]].map(([value,label])=>`<option value="${value}" ${(draft.source_priority || "auto") === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>
        <div class="model-editor-actions"><button type="button" class="button primary" data-search-save ${!data || !searchDirty(x) || x.busy || x.loading || x.stale ? "disabled" : ""}>保存搜索配置</button></div>
        ${x.stale ? '<div class="model-alert">配置已在别处变化，请刷新后核对。</div>' : ""}${x.notice ? `<div class="model-notice${x.bad || unavailable ? " error" : ""}">${esc(x.notice)}</div>` : ""}
      </div>
    </div>`; }
  function searchInput(s, event) { const x = s.search, target = event.target;
    if (!x.draft || x.loading || x.busy) return;
    if (target.matches("[data-search-provider]")) x.draft.deepseek_provider_id = target.value;
    else if (target.matches("[data-search-priority]")) x.draft.source_priority = target.value;
    else return;
    x.bad = false; x.notice = "";
    if (event.type === "change") render(); }
  function searchClick(s, event) { const target = event.target;
    if (target.closest("[data-search-refresh]")) { searchRefresh(s); return; }
    if (target.closest("[data-search-save]")) searchSave(s); }
  /* ---------- Pricing v2 workbench delegation ---------- */
  const pricingWorkbench = () => window.CompanionPricingWorkbench || null;
  function pricingCtx(s) {
    const wb = pricingWorkbench();
    s.pricing ||= wb?.createState?.() || {loaded: false, loading: false, busy: false, data: null, draft: null};
    return {
      state: s, p: s.pricing, path, api, render,
      isCurrent: ticket => current(ticket, s), ticket: () => version, root: () => root, fail,
    };
  }
  /* ---------- Multi-station Quota Dashboard delegation ---------- */
  const quotaWorkbench = () => window.CompanionQuotaWorkbench || null;
  function syncQuotaTabBadge(s) {
    if (!useLiveDom() || !wsMain) return;
    const btn = wsMain.querySelector?.('[data-model-tab="quota"]');
    if (!btn) return;
    const badge = quotaWorkbench()?.tabBadge?.(s.quota) || "";
    let pill = btn.querySelector?.(".c");
    if (badge) {
      if (!pill) {
        pill = document.createElement("span");
        pill.className = "c";
        btn.appendChild(pill);
      }
      pill.textContent = badge;
    } else if (pill) {
      pill.remove?.();
    }
  }
  function quotaCtx(s) {
    const wb = quotaWorkbench();
    s.quota ||= wb?.createState?.() || {loaded: false, loading: false, busy: false, data: null, wizard: null};
    return {
      state: s, q: s.quota, path, api, render,
      syncTabBadge: () => syncQuotaTabBadge(s),
      isCurrent: ticket => current(ticket, s), ticket: () => version, root: () => root, fail,
    };
  }
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
      if (result?.status === "failed") {
        l.preview = result; l.bad = true; l.notice = layaErrorText(result, "Laya 决策调用未完成。");
        render(); await layaRefreshQuiet(s, ticket); return;
      }
      l.preview = result; l.notice = result.status === "skipped" ? "这条测试消息被规则跳过。" : "已收到 Laya 决策。"; l.bad = false; await layaRefreshQuiet(s, ticket); }
    catch (err) { if (!current(ticket, s)) return; l.preview = {status: "unknown", code: err.code || err.message, reason: err.reason || ""}; l.bad = true; l.notice = layaErrorText(err, "Laya 决策调用未完成。");
      /* 先让失败原因可见，再补拉运行记录，避免日志读取拖住错误提示。 */
      render(); await layaRefreshQuiet(s, ticket); }
    finally { l.busy = false; if (current(ticket, s)) render(); } }
  function layaField(l, key, label, hint, type = "text", badge = "") {
    const value = l.draft?.[key] ?? "", disabled = l.loading || l.busy;
    if (type === "bool") return `<label class="model-fd"><span class="model-cap">${label}<em>${hint}</em></span><label class="model-switch"><input type="checkbox" data-laya-field="${key}" ${value ? "checked" : ""} ${disabled ? "disabled" : ""}><i></i></label></label>`;
    return `<label class="model-fd"><span class="model-cap">${label}${badge}<em>${hint}</em></span><input type="${type}" data-laya-field="${key}" value="${esc(value)}" ${disabled ? "disabled" : ""}></label>`;
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
    if (result.status !== "ok") return `<div class="model-notice error">${esc(layaErrorText(result, "Laya 结果未知。"))}</div>`;
    const decision = result.decision; if (!decision) return "";
    const meta = [];
    if (typeof result.action_confidence === "number" && Number.isFinite(result.action_confidence))
      meta.push(`行动置信度 ${(result.action_confidence * 100).toFixed(1)}%`);
    if (result.confidence_gate?.status === "below_threshold")
      meta.push(`低于连续接话阈值 ${result.confidence_gate.threshold_percent}%，已改为旁听`);
    else if (result.confidence_gate?.status === "confidence_unavailable")
      meta.push("当前服务未返回行动概率，阈值未强制执行");
    return `<div class="model-laya-result"><b>决策结果${meta.length ? ` · ${esc(meta.join(" · "))}` : ""}</b><code>${esc(JSON.stringify(decision, null, 2))}</code></div>`; }
  function layaMaskScopeId(id) {
    const text = String(id || "").trim();
    if (text.length <= 6) return text;
    return text.slice(0, 3) + "…" + text.slice(-3);
  }
  function layaScopeLabel(event) {
    const scopeKind = event.scope_info?.kind || (event.source === "probe" ? "test" : "");
    const nativeId = String(event.scope_info?.native_scope_id || "").trim();
    const shortId = nativeId ? layaMaskScopeId(nativeId) : "";
    if (scopeKind === "group") return shortId ? `群聊 ${shortId}` : "群聊";
    if (scopeKind === "private") return shortId ? `私聊 ${shortId}` : "私聊";
    if (scopeKind === "test") return "连接测试";
    return scopeKind || "";
  }
  function layaHistoryMarkup(l) { const events = Array.isArray(l.logs) ? l.logs : [];
    if (l.logsError) return '<p class="model-laya-history-empty">近期记录暂不可用；Laya 配置仍可查看和保存。</p>';
    if (l.logsUnavailable) return '<p class="model-laya-history-empty">当前群聊的近期决策记录暂不能按会话汇总；全局 Laya 配置仍可在此管理。</p>';
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
      const sourceLabel = sources[event.source] || event.source || "未知来源";
      const scopeLabel = layaScopeLabel(event);
      const metaParts = [scopeLabel, scopeLabel === sourceLabel ? "" : sourceLabel, event.mode].filter(Boolean);
      const action = event.decision && typeof event.decision === "object" && event.decision.action ? String(event.decision.action) : "";
      const conf = event.processing?.action_confidence;
      const confText = typeof conf === "number" && Number.isFinite(conf) ? ` (${(conf * 100).toFixed(1)}%)` : "";
      const actionTag = action ? ` <span class="model-tag ${action === "silent" ? "n" : action === "wait" ? "a" : "g"}">action=${esc(action)}${esc(confText)}</span>` : "";
      return `<li data-state="${esc(event.status)}"><details><summary><span><strong>${esc(labels[event.status] || event.status)}${actionTag}</strong><small>${esc(time)} · ${esc(metaParts.join(" · "))}</small></span><b>${event.elapsed_ms == null ? "—" : esc(event.elapsed_ms) + " ms"}</b></summary><div><small>${esc(event.reason_code || "分类结果")}</small>${enabledMarkup}<code>${esc(detail)}</code>${diagnostics}</div></details></li>`;
    }).join("")}</ol>`; }
  function layaContextMarkup() { const product = window.CompanionProduct, currentTarget = product?.target?.();
    if (!currentTarget) return "";
    const selection = product.selection?.(), choices = (product.targets?.() || []).filter(item =>
      item.roles?.some(role => role.role_id === selection?.role_id));
    const label = product.targetLabel?.(currentTarget) || "当前会话";
    return `<div class="model-laya-context"><span>当前会话</span>${choices.length > 1
      ? `<select data-laya-target aria-label="切换查看会话">${choices.map(item => `<option value="${esc(item.target_id)}" ${item.target_id === currentTarget.target_id ? "selected" : ""}>${esc(product.targetLabel(item))}</option>`).join("")}</select>`
      : `<strong>${esc(label)}</strong>`}</div>`; }
  function layaPane(s) { const l = s.laya, context = layaContextMarkup(); if (!l.data) return `<div class="model-subpane model-laya-pane">${context}<div class="model-card"><p class="model-empty">${esc(l.notice || "正在读取 Laya 配置…")}</p></div></div>`;
    const dirty = layaDirty(l), installed = l.data.package_installed, mode = l.draft?.mode || l.data.mode;
    const gpuUnavailable = mode === "local" && l.data.local_runtime_state === "gpu_unavailable";
    const runtimeLabel = gpuUnavailable ? "CUDA 不可用" : mode === "local" ? "本地 Python" : mode === "jev" ? "OpenAI 兼容网关" : mode === "workers_ai" ? "Cloudflare Workers AI" : "远程 HTTP";
    /* 三段式：用途 / 留空=直连 / 示例，旁边再给一个一眼可见的状态徽章。 */
    const proxyHint = "可选，仅 Laya 判断请求使用；留空表示直连；示例 http://127.0.0.1:7890";
    const proxyBadge = layaProxyValue(l)
      ? '<span class="model-pill ok">经代理</span>' : '<span class="model-pill">直连</span>';
    const packageKpi = mode === "local" ? installed ? `已安装 ${l.data.package_version || ""}` : "待安装" : "外部服务";
    const modelKpi = mode === "jev" ? l.data.default_model || "jev-latest"
      : mode === "workers_ai" ? l.data.default_model || "未填写"
      : mode === "remote" ? "远程管理" : l.data.model_cache_ready ? "已缓存" : "首次调用下载";
    const torchText = [l.data.torch_version, l.data.cuda_version ? `CUDA ${l.data.cuda_version}` : "无 CUDA"]
      .filter(Boolean).join(" · ");
    return `<div class="model-subpane model-laya-pane">${context}
      <div class="model-card"><div class="model-phead"><div class="model-grow"><span class="model-eb">可选能力 · 语义决策</span><h3>Laya 决策模型</h3><p class="model-note">在回复前判断是否接话、等待、沉默及表达形式。配置全局生效；仅群聊候选进入 Laya 判断，私聊始终走 AstrBot 核心回复，另由回复页的私聊收尾判断在纯道别时安静。模型不输出时间、段数等数值；行动概率只用于下方可选的连续接话阈值，执行仍遵循宿主配置和权限。接入方式支持本地 Laya 包、远程 HTTP 服务、OpenAI 兼容网关与 Cloudflare Workers AI，四者共用同一份决策合同；Laya 与 TypeSafe 官方 Jev 都走 SystemOne。</p></div><button type="button" class="button small" data-laya-refresh ${l.loading || l.busy ? "disabled" : ""}>刷新</button></div>
        <div class="model-kpis">${kpi(!l.data.enabled ? "未启用" : mode === "local" && l.data.local_runtime_state === "warming" ? "正在预热" : "已启用", "运行状态", l.data.enabled ? "g" : "a")}${kpi(packageKpi, mode === "local" ? "Python 包" : "依赖来源")}${kpi(modelKpi, mode === "jev" || mode === "workers_ai" ? "判断模型" : "模型权重")}${kpi(runtimeLabel, "接入方式", gpuUnavailable ? "r" : "")}</div>
        ${gpuUnavailable ? `<div class="model-alert"><b>本地 Laya 当前不可用：</b>当前 AstrBot Python 使用的是 CPU 版 PyTorch${torchText ? `（${esc(torchText)}）` : ""}。Laya 本地模式需要 CUDA；请安装与驱动匹配的 CUDA 版 PyTorch 后重启 AstrBot，或在上方把接入方式切到「远程 HTTP 服务」或「OpenAI 兼容网关」。
          <div class="model-editor-actions"><button type="button" class="button small" data-laya-copy="torch_install_command">复制 CUDA PyTorch 安装命令</button></div>
          ${l.data.torch_install_command ? `<code class="model-laya-cmd">${esc(l.data.torch_install_command)}</code><small>命令默认使用 cu128；请按显卡驱动把 cu128 换成 cu126 等兼容版本。安装会替换当前 CPU 版 torch，但不会改动已安装的 laya。</small>` : ""}</div>` : ""}
        <div class="model-laya-fields">${layaField(l, "enabled", "启用 Laya", "关闭时调用会跳过", "bool")}${layaSelect(l, "mode", "接入方式", "远程 HTTP 服务走 SystemOne；OpenAI 兼容网关仅用于只提供 /v1/chat/completions 的第三方中转，TypeSafe 官方 Jev 请选「远程 HTTP 服务」", [["local", "本地 Python 包"], ["remote", "远程 HTTP 服务"], ["jev", "OpenAI 兼容网关"], ["workers_ai", "Cloudflare Workers AI"]])}${mode === "remote" ? layaSelect(l, "request_format", "接口格式", "官方 SystemOne 平铺，或 Cloudflare Workers AI 的 input 包装", [["native", "Laya 官方 SystemOne"], ["cloudflare", "Cloudflare Workers AI"]]) : ""}${mode === "remote" ? layaField(l, "endpoint", "SystemOne 服务地址", (l.draft.request_format || l.data.request_format) === "cloudflare" ? "填到 /ai/run；请求会包 input，返回的 result 会自动拆开" : "可填服务根地址、/v1 或完整 /v1/systemone") : ""}${mode === "jev" ? layaField(l, "endpoint", "网关地址", "仅用于只提供 /v1/chat/completions 的第三方中转；可填服务根地址、/v1 或完整路径") : ""}${mode === "workers_ai" ? layaField(l, "endpoint", "Workers AI 地址", "填 accounts/<ACCOUNT_ID>/ai/run，或只填 accounts/<ACCOUNT_ID>；模型写在请求体里") : ""}${mode !== "local" ? layaField(l, "api_key", "API Key", l.data.api_key_set ? "已配置；留空保留现有密钥" : "按服务端要求填写；留空表示不鉴权", "password") : ""}${mode !== "local" ? layaField(l, "proxy", "代理地址", proxyHint, "text", proxyBadge) : ""}${mode !== "local" ? layaField(l, "remote_model", "判断模型名", mode === "jev" ? "留空使用 jev-latest；网关上的模型名可在此覆盖" : mode === "workers_ai" ? "留空使用 @cf/cloudflare/clef（吃每日免费神经元额度）；更快可用 @cf/cloudflare/clef-flash，第三方模型如 typesafe/jev-1.14.0 需单独付费" : "留空使用官方 multilingual；仅自建服务改名时才需要填写") : ""}${layaField(l, "timeout_seconds", "调用超时（秒）", "用于本地推理或远程连接，不参与语义判断", "number")}${layaPercentField(l, "followup_confidence_threshold", "连续接话置信阈值", "0 关闭；仅约束非点名续接。Laya 行动概率低于此值时改为旁听，建议从 40–55% 起按日志校准")}</div>
        ${mode === "remote" ? `<div class="model-editor-actions"><button type="button" class="button small" data-laya-preset="laya" ${l.loading || l.busy ? "disabled" : ""}>填入 Laya 官方</button><button type="button" class="button small" data-laya-preset="typesafe" ${l.loading || l.busy ? "disabled" : ""}>填入 TypeSafe Jev 官方</button></div>` : ""}
        ${layaProtocolAlert(l, mode)}
${mode === "remote" && (l.draft.request_format || l.data.request_format) === "cloudflare" ? '<p class="model-note">Cloudflare Workers AI：服务地址填到 <code>/ai/run</code>；请求体会自动包成 <code>{"model": "...", "input": {"state": "...", "questions": {...}}}</code>，HTTP 200 的 <code>result</code> 会自动拆开。模型名按 Workers AI 的模型标识填写。</p>' : ""}
        ${mode === "local" ? `<div class="model-laya-fields">${layaField(l, "local_model_path", "本地检查点目录", "可选择微调模型；留空使用官方多语言模型。所有会话共用此选择。")}</div>` : ""}
        <section class="model-laya-decisions" aria-label="决策分类配置"><div><strong>决策分类</strong><span>仅启用的分类会发送给 Laya；关闭的分类沿用宿主配置。</span></div><div class="model-laya-decision-grid">${LAYA_DECISION_FIELDS.map(([key, label, hint]) => `<label class="model-laya-option"><span><strong>${label}</strong><small>${hint}</small></span><span class="model-switch"><input type="checkbox" data-laya-decision="${key}" ${l.draft.decision_fields[key] ? "checked" : ""} ${l.loading || l.busy ? "disabled" : ""}><i></i></span></label>`).join("")}</div></section>
        <div class="model-editor-actions"><button type="button" class="button primary" data-laya-save ${!dirty || l.busy || l.loading || l.stale ? "disabled" : ""}>保存 Laya 配置</button><button type="button" class="button" data-laya-test ${l.busy || l.loading || gpuUnavailable ? "disabled" : ""}>${mode === "local" && installed && !l.data.model_cache_ready ? "下载并验证模型" : "测试 Laya 调用"}</button></div>
        ${l.stale ? '<div class="model-alert">配置已在别处变化，请刷新后核对。</div>' : ""}${l.notice ? `<div class="model-notice${l.bad ? " error" : ""}">${esc(l.notice)}</div>` : ""}${l.test ? layaResult(l.test) : ""}
      </div>
      ${mode === "workers_ai" ? `<div class="model-card"><div class="model-phead"><div class="model-grow"><span class="model-eb">外部判断服务</span><h3>Cloudflare Workers AI</h3></div></div><p class="model-note">Cloudflare Workers AI 把模型判断放在 <code>/ai/run</code>：模型名写在请求体里，<code>state</code> 与 <code>questions</code> 需要嵌进 <code>input</code> 对象，而不是与 <code>model</code> 平级。插件会按这个外壳发送，并把返回的 <code>result</code> 解包成同一份 <code>answers</code> 决策合同；判断失败只影响本轮判断，不会阻塞正常回复。</p>
        <div class="model-state">判断模型：<code>${esc(l.data.default_model || "@cf/cloudflare/clef")}</code> · 请求路径：<code>/ai/run</code> · 请求体：<code>{"model": ..., "input": {"state": ..., "questions": ...}}</code></div>
        <div class="model-state"><code>@cf/cloudflare/clef</code> 与 <code>clef-flash</code> 使用 Workers AI 每日免费神经元额度；<code>typesafe/jev-1.14.0</code> 等第三方模型不共享该额度，额度不足时 Cloudflare 返回 402，原因显示在下方运行记录里。</div>
      </div>` : ""}${mode === "jev" ? `<div class="model-card"><div class="model-phead"><div class="model-grow"><span class="model-eb">第三方中转</span><h3>OpenAI 兼容网关</h3></div></div><p class="model-note">这种接入方式把判断请求包进一次 OpenAI 兼容的 <code>chat/completions</code> 调用，只适用于只提供该接口的第三方中转：模型按 state 与分类问题返回每个分类的概率，answers 沿用同一份决策合同。官方 TypeSafe Jev 不提供 <code>chat/completions</code>，请使用「远程 HTTP 服务」并填写 <code>/v1/systemone</code> 地址。判断失败只影响本轮判断，不会阻塞正常回复，也不需要本地 Python 包或权重缓存。</p>
        <div class="model-state">判断模型：<code>${esc(l.data.default_model || "jev-latest")}</code> · 请求路径：<code>/v1/chat/completions</code> · 决策合同：<code>state + questions → answers</code></div>
        <div class="model-state">官方 TypeSafe Jev 走 SystemOne，不使用此接入方式。密钥只保存在插件数据目录的决策配置里，不写入调用日志；失败原因显示在下方运行记录。</div>
      </div>` : mode === "workers_ai" ? "" : `<div class="model-card"><div class="model-phead"><div class="model-grow"><span class="model-eb">官方依赖与权重缓存</span><h3>安装 Laya Python 包</h3></div></div><p class="model-note">插件发行包不包含 Laya 包或检查点权重。将下方命令复制到 AstrBot 所在环境执行；本地首次推理会由官方 Router 自动从 Hugging Face 下载所选检查点。</p>
        <div class="model-laya-install"><code>${esc(l.data.install_command || "")}</code><button type="button" class="button small" data-laya-copy="install_command" ${l.busy || l.loading ? "disabled" : ""}>复制安装命令</button></div>
        ${mode === "remote" ? `<p class="model-note">远程服务端使用 Laya 官方 SystemOne 接口，地址以 <code>/v1/systemone</code> 结尾；服务端需要单独部署，以下命令使用本机 AstrBot Python 作为示例。</p><div class="model-laya-install"><code>${esc(l.data.remote_install_command || "")}</code><button type="button" class="button small" data-laya-copy="remote_install_command" ${l.busy || l.loading ? "disabled" : ""}>复制服务端安装命令</button></div><div class="model-laya-install"><code>${esc(l.data.remote_start_command || "")}</code><button type="button" class="button small" data-laya-copy="remote_start_command" ${l.busy || l.loading ? "disabled" : ""}>复制服务启动命令</button></div>` : ""}
        <div class="model-state">Python：<code>${esc(l.data.python_executable || "未知")}</code> · 权重缓存：<code>${esc(l.data.model_cache_path || "由 Hugging Face 默认配置决定")}</code></div>
        <div class="model-state">上游源码与版本：<a href="https://github.com/NandhaKishorM/laya" target="_blank" rel="noreferrer">NandhaKishorM/laya</a> · <a href="https://huggingface.co/convaiinnovations/laya-multilingual" target="_blank" rel="noreferrer">Multilingual 权重</a></div>
      </div>`}
      <div class="model-card"><div class="model-phead"><div class="model-grow"><span class="model-eb">调用预览</span><h3>测试一次语义决策</h3></div></div><label class="model-fd"><span class="model-cap">测试消息<em>只提交这段文字与固定的文本类型事实</em></span><textarea data-laya-preview rows="4" ${l.busy || l.loading ? "disabled" : ""}>${esc(l.previewText || "")}</textarea></label><div class="model-editor-actions"><button type="button" class="button primary" data-laya-decide ${l.busy || l.loading || !l.data.enabled ? "disabled" : ""}>调用 Laya</button></div>${l.preview ? layaResult(l.preview) : ""}</div>
      <div class="model-card"><div class="model-phead"><div class="model-grow"><span class="model-eb">${l.logsUnavailable ? "当前会话" : isGroupLaya(s) ? "本群运行记录 · 最近 50 条" : l.logsScope === "current" ? "当前会话 · 最近 50 条" : "全部来源 · 最近 50 条"}</span><h3>Laya 调用日志</h3></div>${isGroupLaya(s) ? "" : `<div class="model-st2"><div class="model-seg" role="group" aria-label="日志来源筛选"><button type="button" class="${(l.logsScope || "all") === "all" ? "on" : ""}" data-laya-logs-scope="all" aria-pressed="${(l.logsScope || "all") === "all"}" ${l.loading || l.logsLoading ? "disabled" : ""}>全部来源</button><button type="button" class="${l.logsScope === "current" ? "on" : ""}" data-laya-logs-scope="current" aria-pressed="${l.logsScope === "current"}" ${l.loading || l.logsLoading ? "disabled" : ""}>仅当前会话</button></div></div>`}</div>${layaHistoryMarkup(l)}</div>
    </div>`; }
  function layaInput(s, event) { const l = s.laya, t = event.target;
    if (event.type === "change" && t.matches("[data-laya-target]")) {
      const product = window.CompanionProduct, roleId = product?.selection?.()?.role_id;
      if (roleId) void product.choose(t.value, roleId);
      return; }
    if (t.matches("[data-laya-preview]")) { l.previewText = t.value; return; }
    if (t.matches("[data-laya-decision]") && l.draft) { l.draft.decision_fields[t.dataset.layaDecision] = t.checked; if (event.type === "change") { l.bad = false; render(); } return; }
    const key = t.dataset.layaField; if (!key || !l.draft || l.loading || l.busy) return;
    const value = t.type === "checkbox" ? t.checked : t.type === "number" ? t.valueAsNumber : t.value;
    l.draft[key] = key === "followup_confidence_threshold"
      ? Math.max(0, Math.min(100, Number.isFinite(Number(value)) ? Math.round(Number(value)) : 0))
      : value;
    // request_format only applies to the remote slot; a hidden cloudflare
    // value would otherwise follow the user into the next mode.
    if (key === "mode" && value !== "remote") l.draft.request_format = "native";
    if (event.type === "change") { l.bad = false; render(); } }
  function layaClick(s, event) { const t = event.target;
    if (t.closest("[data-laya-refresh]")) { layaRefresh(s); return; }
    const logsScope = t.closest("[data-laya-logs-scope]"); if (logsScope) { layaSwitchLogsScope(s, logsScope.dataset.layaLogsScope); return; }
    if (t.closest("[data-laya-save]")) { layaSave(s); return; }
    const preset = t.closest("[data-laya-preset]"); if (preset) { layaApplyPreset(s, preset.dataset.layaPreset); return; }
    const copy = t.closest("[data-laya-copy]"); if (copy) { layaCopyInstall(s, copy.dataset.layaCopy); return; }
    if (t.closest("[data-laya-test]")) { layaTest(s); return; }
    if (t.closest("[data-laya-decide]")) { layaDecide(s); return; } }
  /* ---------- image model ---------- */
  const IMAGE_LABELS = {"image.generate": "文生图", "image.edit": "参考图改图", "image.mask_edit": "蒙版局部改图"};
  const IMAGE_VIEWS = {generate: {label: "文生图", operation: "generate", actions: ["image.generate"]},
    edit: {label: "改图", operation: "edit", actions: ["image.edit", "image.mask_edit"]}};
  const IMAGE_LOCAL_LABELS = {comfyui: "ComfyUI", sd_webui: "SD WebUI", nai: "NAI 生图插件"};
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
    if (selected === "nai") routes.push("nai");
    return {generate: [...routes], edit: [...routes]};
  };
  const IMAGE_PLATFORMS = {auto: "自动识别", openai: "OpenAI 兼容", openrouter: "OpenRouter", xai: "xAI Grok Imagine", agnes: "Agnes", sensenova: "商汤日日新", minimax: "MiniMax", bailian: "阿里云百炼", modelscope: "魔搭社区", doubao: "火山引擎 / 豆包", gemini: "Google Gemini"};
  const IMAGE_PROTOCOL_SUPPORT = {auto: "根据地址识别协议", openai: "文生图 · 改图", openrouter: "文生图 · 改图", xai: "文生图 · 改图（最多 5 张参考图）", agnes: "文生图 · 改图", sensenova: "文生图 · 改图", minimax: "仅文生图", doubao: "文生图 · 改图", bailian: "已保留配置 · 需异步协议适配", modelscope: "已保留配置 · 需协议适配", gemini: "已保留配置 · 需协议适配"};
  const imageModelFamily = model => {
    const parts = String(model || "").trim().toLowerCase().split(/[/:]+/).filter(Boolean);
    return parts.some(part => part.startsWith("gpt-image-") || part.startsWith("chatgpt-image-")) ? "gpt-image"
      : parts.some(part => part.startsWith("dall-e") || part.startsWith("dalle")) ? "dall-e" : "";
  };
  const imageQualityHelp = item => imageEffectivePlatform(item) === "xai"
    ? "xAI 仅 grok-imagine-image-2.0 接受 quality（自动/低/中）；1.0 或其它取值不会发送。"
    : imageModelFamily(item.model) === "gpt-image"
    ? "GPT Image 只接受自动/低/中/高；升级前保存的「标准/高清」会按服务端默认处理，不会发送（保存一次即修正），返回格式也不会发送。"
    : "默认不发送 quality；standard/hd 只对 dall-e 系列有效。";
  const imageResponseFormatHelp = item => imageEffectivePlatform(item) === "xai"
    ? "xAI 默认返回图片 URL；也可选 b64_json 内联返回。"
    : imageModelFamily(item.model) === "gpt-image"
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
    try {
      const host = new URL(String(endpoint?.base_url || "")).hostname.toLowerCase();
      if (host === "x.ai" || host.endsWith(".x.ai")) return "xai";
    } catch { /* a partial address falls through to the substring rules */ }
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
      endpoint.max_reference_images ??= 16;
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
    if (i.configLoading || i.configSaving || i.endpointTesting) return;
    if (imageConfigDirty(i)) {
      i.configBad = false;
      i.configNotice = "当前有未保存的生图配置草稿，已保留本地修改未覆盖；如需重新读取请先保存或放弃修改。";
      render();
      return;
    }
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
  function imageRouteAvailable(config, route, operation, nai = null) {
    if (route === "nai") return !nai?.available ? "NAI 插件未就绪" :
      !config.enabled ? "生图后端已关闭" : nai.operations?.includes(operation) ?
        (operation === "generate" ? "可用于文生图" : "可用于参考图改图") : "当前接入方式不支持";
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
    const options = `<optgroup label="已接入协议">${["auto", "openai", "openrouter", "xai", "agnes", "sensenova", "minimax", "doubao"].map(option).join("")}</optgroup><optgroup label="待适配 · 可保留原配置">${["bailian", "modelscope", "gemini"].map(option).join("")}</optgroup>`;
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
    if (ref === "nai") {
      const status = i.config?.nai || {}, support = imageRouteAvailable(config, ref, imageCurrentOperation(i), status);
      const service = status.call_mode === "openai" ? (status.api_profile === "starbridge" ? "星桥（Almoon）" : "OpenAI 兼容接口") : "nai.sta1n.cn 直连";
      return `<article class="model-img-route" data-img-route-card="nai"><header><span class="model-img-priority">${index + 1}</span><div class="model-img-route-title"><strong>NAI 生图插件</strong><div class="model-img-route-meta"><span>${esc(status.default_model || service)}</span></div></div><span class="model-img-route-support ${imageRouteSupportTone(support)}">${esc(support)}</span>${controls}</header><div class="model-note">配置来源：NAI 生图插件 · ${esc(service)}${status.default_size ? ` · ${esc(status.default_size)}` : ""}</div></article>`;
    }
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
      const effectivePlatform = imageEffectivePlatform(item);
      const xaiProtocol = effectivePlatform === "xai";
      const qualityChoices = xaiProtocol
        ? [["", "服务端默认（不发送）"], ["auto", "自动"], ["low", "低"], ["medium", "中"]]
        : [["", "服务端默认（不发送）"], ["auto", "自动"], ["low", "低"], ["medium", "中"], ["high", "高"], ["standard", "标准（dall-e 专用）"], ["hd", "高清（dall-e 专用）"]];
      const sizeLabelHelp = xaiProtocol
        ? "xAI 可填 1k、1.5k、2k 控制分辨率；宽高用于换算 aspect_ratio，画布不能整除时不发送尺寸参数。"
        : "服务商接受标签时保留原值；宽高用于通用协议";
      const referenceHelp = xaiProtocol
        ? "0 = 仅文生图；xAI 改图最多 5 张参考图。"
        : "0 = 仅文生图；GPT Image 最多 16 张，按服务商实际上限填写";
      const qualityField = effectivePlatform === "minimax" ? "" : `<label class="model-img-field"><span>图片质量</span><select data-img-config-field="${base}.quality"${disabled ? " disabled" : ""}>${qualityChoices.map(([value, label]) => `<option value="${value}"${item.quality === value ? " selected" : ""}>${label}</option>`).join("")}</select><small>${esc(imageQualityHelp(item))}</small>${imageLegacyQualityNote(item)}</label><label class="model-img-field"><span>返回格式</span><select data-img-config-field="${base}.response_format"${disabled ? " disabled" : ""}>${[["", "按服务端默认（推荐）"], ["b64_json", "b64_json（旧式 dall-e 兼容）"]].map(([value, label]) => `<option value="${value}"${item.response_format === value ? " selected" : ""}>${label}</option>`).join("")}</select><small>${esc(imageResponseFormatHelp(item))}</small></label>`;
      const modelFields = `${imageConfigField("模型 ID", `${base}.model`, item.model, "text", 'maxlength="240" placeholder="服务商提供的模型 ID"', "")}${imageConfigField("尺寸标签", `${base}.size_label`, item.size_label || `${item.width}x${item.height}`, "text", 'maxlength="64" placeholder="如 1024x1024、1k、2k、auto"', sizeLabelHelp)}${imageConfigField("宽度", `${base}.width`, item.width, "number", 'min="64" max="8192" step="64"', "像素")}${imageConfigField("高度", `${base}.height`, item.height, "number", 'min="64" max="8192" step="64"', "像素")}${qualityField}${imageConfigField("最多参考图", `${base}.max_reference_images`, item.max_reference_images, "number", 'min="0" max="16" step="1"', referenceHelp)}<label class="model-img-field model-img-inline"><span>启用模型</span><input type="checkbox" data-img-config-field="${base}.enabled"${item.enabled ? " checked" : ""}${disabled ? " disabled" : ""}></label>`;
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
      ...Object.entries(IMAGE_LOCAL_LABELS).filter(([key]) => !current.has(key)
        && (key !== "nai" || i.config.nai?.available && i.config.nai?.operations?.includes(operation))).map(([key, label]) => [key, label])];
    const dirty = imageConfigDirty(i), locked = i.configSaving || i.configLoading || !!i.endpointTesting;
    const nai = i.config.nai, naiSupported = nai?.available && nai.operations?.includes(operation);
    const naiState = !nai ? "状态暂不可读" : !nai.available ? "插件尚未就绪" :
      !naiSupported ? "当前接入方式不支持此功能" : [nai.default_model, nai.default_size].filter(Boolean).join(" · ") || "已就绪";
    const naiSource = current.has("nai") || nai?.installed === false ? "" : `<div class="model-img-nai-source" data-img-nai-source><div><strong>NAI 生图插件</strong><span>${esc(naiState)}</span></div><button type="button" class="button small" data-img-nai-add${locked || !naiSupported ? " disabled" : ""}><i data-lucide="plus" aria-hidden="true"></i>加入${IMAGE_VIEWS[i.view || "generate"].label}队列</button></div>`;
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
      ${naiSource}
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
    if (imageSchemaState === "loading" || imageSchemaState === "done") return;
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
      if (choice && !queue.includes(choice)) {
        queue.push(choice);
        if (choice === "nai") i.configDraft.enabled = true;
      }
      render(); return;
    }
    if (t.closest("[data-img-nai-add]") && i.configDraft && !i.configSaving && !i.configLoading && !i.endpointTesting) {
      const operation = imageCurrentOperation(i), nai = i.config?.nai;
      if (!nai?.available || !nai.operations?.includes(operation)) return;
      const queue = i.configDraft.operation_routes[operation];
      if (!queue.includes("nai")) queue.push("nai");
      i.configDraft.enabled = true;
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
  function tabBar(s) {
    const primaryTabs = [
      ["fn", "功能模型", ICONS.fn, snapshot(s).length, false],
      ["voice", "语音模型", ICONS.voice, s.voice.data ? s.voice.data.providers.length : "", false],
      ["image", "生图模型", ICONS.image, s.image.data ? s.image.data.actions.length : "", false],
      ["search", "搜索服务", ICONS.search, s.search?.data?.deepseek_provider_id ? "已绑定" : "", true],
      ["laya", "Laya 决策", ICONS.decision, s.laya.data ? (s.laya.data.enabled ? "启用" : "") : "", true],
    ];
    const opsTabs = [
      ["pricing", "计价与汇率", ICONS.pricing, pricingWorkbench()?.tabBadge?.(s.pricing) || "", false],
      ["quota", "额度看板", ICONS.quota, quotaWorkbench()?.tabBadge?.(s.quota) || "", false],
    ];
    const renderTab = ([id, label, ic, count, isDot]) =>
      `<button type="button" role="tab" data-model-tab="${id}" aria-selected="${s.tab === id}" class="${s.tab === id ? "on" : ""}">${icon(ic)}<span>${label}</span>${count !== "" ? (isDot ? `<span class="c-dot" title="${esc(count)}" aria-label="${esc(count)}"></span>` : `<span class="c">${esc(count)}</span>`) : ""}</button>`;
    return `<div class="model-tabs-wrap" data-model-tabs-wrap><div class="model-tabs" role="tablist" aria-label="模型配置分类">${primaryTabs.map(renderTab).join("")}<span class="model-tabs-sep" aria-hidden="true"></span>${opsTabs.map(renderTab).join("")}</div></div>`;
  }
  function syncModelTabsOverflow() {
    if (!root?.querySelector) return;
    const wrap = root.querySelector("[data-model-tabs-wrap]");
    const bar = wrap?.querySelector?.(".model-tabs");
    if (!wrap || !bar) return;
    const updateFade = () => {
      const maxScroll = Math.max(0, (bar.scrollWidth || 0) - (bar.clientWidth || 0));
      const left = bar.scrollLeft || 0;
      wrap.dataset.fadeLeft = left > 4 ? "1" : "0";
      wrap.dataset.fadeRight = maxScroll - left > 4 ? "1" : "0";
    };
    if (!bar._fadeBound && typeof bar.addEventListener === "function") {
      bar._fadeBound = true;
      bar.addEventListener("scroll", updateFade, { passive: true });
    }
    updateFade();
  }
  function handleModelTabsResize() {
    if (!root?.isConnected) return;
    syncModelTabsOverflow();
  }
  /* ---------- functional subtabs (2-level flattened IA) ---------- */
  function subtabBar(s) { const tabs = [
      ["cfg", "任务路由", "cfg", "统一查看主备链路，点击任意任务在右侧抽屉精调"],
      ["pol", "替换策略", "pol", "管理关键词换模、高价时段替代与拒答兜底"],
    ];
    return `<div class="model-subtabs" role="tablist" aria-label="功能模型视图">${tabs.map(([id, label, ic, desc]) => `<button type="button" role="tab" data-model-sub="${id}" aria-selected="${s.sub === id}" class="${s.sub === id ? "on" : ""}"><span class="model-subtab-icon">${icon(ICONS[ic])}</span><span class="model-subtab-copy"><b>${label}</b><small>${desc}</small></span></button>`).join("")}</div>`; }
  function kpi(n, label, tone = "") { return `<div class="model-kpi ${tone}"><b>${n}</b><span>${esc(label)}</span></div>`; }
  function fnKpis(s) { const tasks = snapshot(s); const total = tasks.length; const providers = s.status?.providers.length || 0;
    const withFb = tasks.filter(fallbackFor).length; const own = tasks.filter(t => taskFields(t).some(nm => ownsField(t, nm))).length;
    return kpi(total, "需要模型的功能") + kpi(providers, "可用模型") + kpi(withFb, "已设回退", "g")
      + kpi(total - withFb, "暂无回退", "a") + kpi(own, "单独设置"); }
  const providerPill = (id, missing, hovered) => id
    ? `<button type="button" class="model-pill m${hovered === id ? " is-hovered" : ""}" data-provider-pill="${esc(id)}" style="--mc:${pillColor(id)}" title="查看 ${esc(id)} 的路由分工">${esc(id)}</button>`
    : `<span class="model-pill m none">${missing || "无回退"}</span>`;
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
  function selectionDockInner(s) {
    if (!s || s.tab !== "fn" || s.sub === "pol") return { hasDock: false, hasReport: false, html: "" };
    const picked = selected(s);
    if (!picked.length && !s.rule.report) return { hasDock: false, hasReport: false, html: "" };
    const providers = new Set();
    picked.forEach(kind => { const task = taskOf(s, kind), draft = s.drafts.get(kind);
      const primary = providerFor(task, draft); if (primary) providers.add(primary);
      const fallback = effective(task, draft, "fallback"); if (fallback) providers.add(fallback); });
    const rows = (s.rule.report || []).map(line => { const st = line.provider ? s.providerTests[line.provider] : null;
      const running = s.rule.testing && (!st || st.status === "testing");
      const cls = running ? "run" : st && st.status === "ok" ? "ok" : "bad";
      const tail = !line.provider ? "无模型提供方"
        : running ? '<span class="model-sp"></span>测试中'
        : st ? esc(probeLabel(st)) : "结果未知";
      return `<div class="model-trow ${cls}"><span class="dotm" style="background:${pillColor(line.provider)}"></span><span class="mno">${esc(line.provider || "—")}</span>
        <span class="u">${esc(line.role)} · ${esc(line.label)}</span><span class="tr">${tail}</span></div>`; }).join("");
    const total = (s.rule.report || []).length;
    const done = (s.rule.report || []).filter(line => { const st = line.provider && s.providerTests[line.provider]; return st && st.status !== "testing"; }).length;
    const okCount = (s.rule.report || []).filter(line => s.providerTests[line.provider]?.status === "ok").length;
    const disabled = s.busy || s.loading || s.denied || s.rule.testing;
    const providerOpts = (s.status?.providers || []).map(provider => `<option value="${esc(provider.id)}">${esc(provider.id)}</option>`).join("");
    const html = `${picked.length ? `<div class="model-selbar" role="status">
      <span class="model-sel-count">已选 <strong>${picked.length}</strong> 项任务 · 涉及 <b>${providers.size}</b> 个提供方</span>
      ${total ? `<span class="model-bar${done === total && okCount === total ? " full" : ""}"><i style="width:${Math.round(done / total * 100)}%"></i></span>` : ""}
      <select data-model-assign aria-label="批量指派主模型提供方" ${disabled ? "disabled" : ""}><option value="">批量指派主模型…</option>${providerOpts}</select>
      <select data-model-assign-fallback aria-label="批量指派备用模型提供方" ${disabled ? "disabled" : ""}><option value="">批量指派备用模型…</option><option value="__inherit__">跟随上级默认</option><option value="__none__">不使用备用模型</option>${providerOpts}</select>
      <span class="model-grow"></span>
      <button type="button" class="button small" data-model-action="inherit" ${disabled ? "disabled" : ""}>恢复跟随上级</button>
      <button type="button" class="button small primary" data-map-test ${s.rule.testing ? "disabled" : ""}>${s.rule.testing ? "测试中…" : "批量测试"}</button>
      <button type="button" class="button small" data-model-action="clear-selection" data-map-clear ${s.rule.testing ? "disabled" : ""}>取消选择</button>
    </div>` : ""}
      ${s.rule.report ? `<div class="model-tres"><div class="th"><b>连通性测试</b><span class="s">${s.rule.testing ? `正在测试 ${picked.length} 项` : `${total} 项完成 · ${okCount} 通过${okCount < total ? " · " + (total - okCount) + " 失败" : ""}`}</span><span class="model-grow"></span><button type="button" class="model-lk" data-map-report-close ${s.rule.testing ? "disabled" : ""}>收起结果</button></div>
        <div class="tbody">${rows}</div></div>` : ""}`;
    return { hasDock: true, hasReport: Boolean(s.rule.report), html };
  }
  function selectionDock(s) {
    const info = selectionDockInner(s);
    return info.hasDock ? `<div class="model-dock is-open" role="region" aria-label="已选任务批量操作">${info.html}</div>` : "";
  }
  async function runMapTest(s) { if (s.rule.testing) return; const ticket = version, picked = selected(s);
    if (!picked.length) return;
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
  function quickExpListHtml(s, kinds) {
    return `<div class="model-exp"><ul>${kinds.map(k => { const t = taskOf(s, k), d = s.drafts.get(k);
      const isOwn = d && fields.some(name => !d.inherited[name]);
      const m = t ? providerFor(t, d) || "未设置" : "未注册";
      const f = t ? rowFallback(t, d) : "";
      return `<li><button type="button" class="model-lk" data-model-open="${esc(k)}">${esc(taskName(k)?.label || k)}</button><span>${esc(m)}${f ? " → " + esc(f) : " · 无备用"}${isOwn ? " · 已改" : ""}</span></li>`; }).join("")}</ul></div>`;
  }
  function quickBodyMarkup(s) {
    if (!s.status || !snapshot(s).length) return "";
    const providers = s.status.providers || [];
    const primaryOpts = val => [
      `<option value="__keep__" ${val === "__keep__" ? "selected" : ""}>保持各任务现有主模型不变</option>`,
      `<option value="__inherit__" ${val === "__inherit__" ? "selected" : ""}>跟随上级默认（清除本层覆写）</option>`,
      ...providers.map(p => `<option value="${esc(p.id)}" ${val === p.id ? "selected" : ""}>${esc(providerLabel(p))}</option>`),
    ].join("");
    const fallbackOpts = val => [
      `<option value="__keep__" ${val === "__keep__" ? "selected" : ""}>保持各任务现有备用不变（默认安全）</option>`,
      `<option value="__inherit__" ${val === "__inherit__" ? "selected" : ""}>跟随上级默认（清除本层覆写）</option>`,
      `<option value="__none__" ${val === "__none__" ? "selected" : ""}>不使用备用模型（清空备用）</option>`,
      ...providers.map(p => `<option value="${esc(p.id)}" ${val === p.id ? "selected" : ""}>${esc(providerLabel(p))}</option>`),
    ].join("");
    const cards = taskGroups(s).filter(([, entries]) => entries.some(([kind]) => !coreReply(taskOf(s, kind))))
      .map(([g, entries]) => { const items = entries.filter(([kind]) => !coreReply(taskOf(s, kind))); const kinds = items.map(([kind]) => kind);
      const slot = slotDraftOf(s, g);
      const curMain = kinds.map(k => { const t = taskOf(s, k), d = s.drafts.get(k); return t && d ? providerFor(t, d) : ""; });
      const distinctMain = [...new Set(curMain.filter(Boolean))];
      const sameMain = curMain.length && curMain.every(v => v && v === curMain[0]) ? curMain[0] : "";
      const curFb = kinds.map(k => { const t = taskOf(s, k), d = s.drafts.get(k); return t && d ? rowFallback(t, d) : ""; });
      const nonEmptyFb = curFb.filter(Boolean);
      const distinctFb = [...new Set(nonEmptyFb)];
      const sameFb = nonEmptyFb.length === curFb.length && curFb.every(v => v === curFb[0]) ? curFb[0] : "";
      const allNoFb = nonEmptyFb.length === 0;
      const own = kinds.filter(k => { const d = s.drafts.get(k); return d && fields.some(name => !d.inherited[name]); }).length;
      const routed = kinds.map(k => { const t = taskOf(s, k); return t ? parseRules(t.fields.replacement_rules.value).length : 0; }).reduce((a, b) => a + b, 0);
      const refusal = kinds.filter(k => { const t = taskOf(s, k); return t && t.fields.fallback_on_refusal.value === true; }).length;
      const open = !!s.expOpen[g];
      const mainChanged = slot.provider !== "__keep__";
      const fbChanged = slot.fallback !== "__keep__";
      const collide = !slot.provider.startsWith("__") && slot.fallback === slot.provider;
      const applyLabel = mainChanged && fbChanged ? "应用主备到本组" : mainChanged ? "仅应用主模型" : fbChanged ? "仅应用备用模型" : "应用到本组";
      return `<article class="model-sc" data-quick-card="${esc(g)}">
        <div class="model-sc-tp">
          <div class="model-eb">快速分组指派<span class="st">${own ? `<span class="model-tag a">${own} 项单独设置</span>` : '<span class="model-tag n">默认统一</span>'}</span></div>
          <div class="model-ti">${icon(groupIcon(g))}<b>${esc(g)}</b></div>
          <div class="model-ds">${esc(items.map(([, label]) => label).join("、"))}</div>
          <div class="model-rl"><span>${items.length} 项任务</span><span>替换规则 ${routed} 条</span>${refusal ? `<span>拒答替代 ${refusal} 项</span>` : ""}</div>
        </div>
        <div class="model-sc-bd">
          <div class="model-cur-grid">
            <div class="model-cur-item"><span>当前主路由</span><div class="model-cur${sameMain ? "" : " miss"}" title="${esc(sameMain || (distinctMain.length > 1 ? distinctMain.join("、") : "跟随上级"))}">${esc(sameMain || (distinctMain.length > 1 ? `混合（${distinctMain.length} 个模型）` : "跟随上级"))}</div></div>
            <div class="model-cur-item"><span>当前备用路由</span><div class="model-cur${sameFb ? "" : " miss"}" title="${esc(sameFb || (allNoFb ? "均无备用" : distinctFb.filter(Boolean).join("、") || "混合 / 部分无备用"))}">${esc(sameFb || (allNoFb ? "均无备用" : "混合 / 部分无备用"))}</div></div>
          </div>
          <label class="model-fd"><span class="model-cap">统一指派主模型 <em>选「保持不变」时不动各任务主模型</em></span>
            <select data-model-slot="${esc(g)}" aria-label="${esc(g)} 统一指派主模型" ${s.busy || s.loading || s.denied ? "disabled" : ""}>${primaryOpts(slot.provider)}</select></label>
          <label class="model-fd"><span class="model-cap">统一指派备用模型 <em>默认保持现有备用不变，防止误覆盖精细设置</em></span>
            <select data-model-slot-fallback="${esc(g)}" aria-label="${esc(g)} 统一指派备用模型" ${s.busy || s.loading || s.denied ? "disabled" : ""}>${fallbackOpts(slot.fallback)}</select></label>
          ${collide ? '<div class="model-alert" role="alert">主模型与备用模型不能相同，请调整其中一项。</div>' : ""}
        </div>
        <div class="model-sc-ft"><code>${esc(g)}</code><span class="model-grow"></span>
          <button type="button" class="model-lk" data-model-exp="${esc(g)}" aria-expanded="${open}"><span>${open ? "收起" : "覆盖"} ${items.length} 项${own ? ` · ${own} 项单独设置` : ""}</span><span class="model-exp-chev" aria-hidden="true">▾</span></button>
          <button type="button" class="button small primary" data-model-slot-apply="${esc(g)}" ${s.busy || s.loading || s.denied || (!mainChanged && !fbChanged) || collide ? "disabled" : ""}>${applyLabel}</button></div>
        <div class="model-collapse-shell${open ? " is-open" : ""}" data-model-exp-shell="${esc(g)}"><div class="model-collapse-inner"${open ? "" : ' aria-hidden="true" inert'}>${open ? quickExpListHtml(s, kinds) : ""}</div></div>
      </article>`; }).join("");
    const core = snapshot(s).find(coreReply);
    return `<div class="model-quick-body">${core ? '<p class="model-note">普通对话由 AstrBot 当前会话管理；快速分组指派仅作用于插件任务，对话附加提示请点击「对话回复」在右侧抽屉编辑。</p>' : ""}<div class="model-qgrid">${cards}</div></div>`;
  }
  function quickConfig(s) {
    if (!s.status || !snapshot(s).length) return "";
    return `<section class="model-quick-box${s.quickOpen ? " is-open" : ""}" aria-label="快速分组批量指派">
      <button type="button" class="model-quick-toggle" data-model-quick-toggle aria-expanded="${s.quickOpen}">
        <span class="model-quick-toggle-copy"><b>快速分组指派（批量主备路由）</b><small>按业务分组批量指定主模型或备用模型；备用默认「保持不变」，避免误覆盖已精调任务</small></span>
        <span class="model-quick-toggle-state"><span class="model-quick-state-txt">${s.quickOpen ? "收起" : "展开批量指派"}</span><span class="model-quick-chev" aria-hidden="true">▾</span></span>
      </button>
      <div class="model-collapse-shell"><div class="model-collapse-inner"${s.quickOpen ? "" : ' aria-hidden="true" inert'}>${s.quickOpen ? quickBodyMarkup(s) : ""}</div></div>
    </section>`; }
  async function applySlot(s, slotId) {
    const gp = taskGroups(s).find(([g]) => g === slotId); if (!gp) return;
    const slot = slotDraftOf(s, slotId);
    const pChoice = slot.provider, fChoice = slot.fallback;
    if (pChoice === "__keep__" && fChoice === "__keep__") return;
    if (!pChoice.startsWith("__") && pChoice === fChoice) {
      s.bad = true; s.notice = "主模型与备用模型不能相同，请调整其中一项。"; render(); return;
    }
    const pObj = !pChoice.startsWith("__") ? s.status.providers.find(p => p.id === pChoice) : null;
    const fObj = !fChoice.startsWith("__") ? s.status.providers.find(p => p.id === fChoice) : null;
    const kinds = [], skipped = [];
    for (const [kind] of gp[1]) {
      const task = taskOf(s, kind), draft = s.drafts.get(kind);
      if (!task || coreReply(task) || !draft) continue;
      if (draft.pending || draft.stale) { skipped.push(`${taskName(kind).label}（存在待核对/冲突草稿）`); continue; }
      let touched = false;
      if (pChoice !== "__keep__") {
        if (pChoice === "__inherit__") {
          const blockedParent = unfollowableParent(s, task);
          if (blockedParent) {
            skipped.push(`${taskName(kind).label}（${blockedParent.inherited_value ? "上级主模型不可用" : "上级主模型为空"}）`);
          } else {
            draft.inherited.provider = true; delete draft.chosen.provider; touched = true;
          }
        } else if (!providerSupportsTask(task, pObj)) {
          skipped.push(`${taskName(kind).label}（主模型能力不符）`);
        } else {
          draft.inherited.provider = false; draft.values.provider = pChoice; touched = true;
        }
      }
      if (fChoice !== "__keep__") {
        if (fChoice === "__inherit__") {
          draft.inherited.fallback = true; delete draft.chosen.fallback; touched = true;
        } else if (fChoice === "__none__") {
          draft.inherited.fallback = false; draft.values.fallback = "";
          if (task.fields.fallback?.cleared || task.fields.fallback?.replaced) draft.chosen.fallback = true;
          touched = true;
        } else if (!providerSupportsTask(task, fObj)) {
          skipped.push(`${taskName(kind).label}（备用模型能力不符）`);
        } else {
          const nextPrimary = providerFor(task, draft);
          if (nextPrimary && nextPrimary === fChoice) {
            skipped.push(`${taskName(kind).label}（备用与主模型 ${nextPrimary} 相同）`);
          } else {
            draft.inherited.fallback = false; draft.values.fallback = fChoice;
            if (task.fields.fallback?.cleared || task.fields.fallback?.replaced) draft.chosen.fallback = true;
            touched = true;
          }
        }
      }
      if (touched) {
        draft.touched = true;
        fitLimit(s, task, draft);
        if (dirty(task, draft)) kinds.push(kind);
      }
    }
    if (!kinds.length) {
      s.bad = skipped.length > 0;
      s.notice = skipped.length
        ? `没有任务可应用；已跳过 ${skipped.length} 项：${skipped.join("、")}。`
        : `「${slotId}」组内所有任务已与所选路由一致，无需保存。`;
      render(); return;
    }
    s.notice = `正在批量更新「${slotId}」组中 ${kinds.length} 项任务…`; render();
    await saveTasks(s, kinds);
    if (!s.bad) s.slotDraft[slotId] = { provider: "__keep__", fallback: "__keep__" };
    if (skipped.length) {
      s.notice = `${s.notice} 已跳过 ${skipped.length} 项：${skipped.join("、")}。`;
    }
    render();
  }
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
  /* The policy pane always has an active task: the explicit pick, else the first.
     对话回复也在列表里：纯时段规则由宿主在核心管线按轮临时切换 Provider。 */
  const policyTask = s => snapshot(s).find(task => task.kind === s.pol)
    || snapshot(s)[0] || null;
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
  function policyTaskOptions(s, picked) { return snapshot(s).map(task => {
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
    const modes = coreReply(task) ? [["kw", "关键词换模"], ["peak", "高价时段替代"]]
      : [["kw", "关键词换模"], ["peak", "高价时段替代"], ["refuse", "拒答替代"]];
    const scope = task && draft ? effective(task, draft, "replacement_scope") : "all";
    const locked = !task || s.busy || s.loading || s.denied || draft?.pending;
    const scopes = [["plugin", "仅插件调用"], ["conversation", "仅普通对话模型"], ["all", "全部生效"]];
    const panel = s.pm === "peak" ? peakPolicy(s) : s.pm === "refuse" && !coreReply(task) ? refusalPolicy(s) : keywordPolicy(s);
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
  function cfgPane(s) {
    const legend = (s.status?.providers || []).map(p => `<span><i style="background:${pillColor(p.id)}"></i><em>${esc(p.id)}</em></span>`).join("");
    const legendBox = (s.status?.providers || []).length
      ? `<section class="model-mlgd-box${s.legendOpen ? " is-open" : ""}"${s.legendOpen ? " open" : ""}><button type="button" class="model-mlgd-toggle" data-model-legend-toggle aria-expanded="${!!s.legendOpen}"><span class="model-mlgd-chev" aria-hidden="true">▸</span><span>模型提供方图例</span><small>${s.status.providers.length} 个 · ${s.legendOpen ? "收起" : "点击展开"}</small></button><div class="model-collapse-shell"><div class="model-collapse-inner"${s.legendOpen ? "" : ' aria-hidden="true" inert'}><div class="model-mlgd legend">${legend}</div></div></div></section>`
      : "";
    const inlineDock = useLiveDom() ? "" : selectionDock(s);
    return `<div class="model-subpane">${globalDefaultsCard(s)}<div class="model-kpis">${fnKpis(s)}</div>${!(s.bad || s.denied) ? notice(s) : ""}<div class="model-queue-slot">${queue(s)}</div>${quickConfig(s)}${toolbar(s)}${legendBox}${body(s)}${inlineDock}</div>`; }
  /* ---------- replacement policy: mutations ---------- */
  const ruleTarget = ref => { const [kind, index] = String(ref || "").split(":"); return { kind, index: Number(index) }; };
  /* Every rule edit funnels through here so the JSON the gateway reads stays canonical. */
  function editRules(s, kind, mutate) { const task = taskOf(s, kind), draft = s.drafts.get(kind);
    if (!task || !draft || draft.pending || s.busy || s.loading) return;
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
      s.notice = adjusted ? limitNotice(task, draft, adjusted)
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
  function fnPane(s) { const sub = s.sub === "pol" ? replacementPolicy(s) : cfgPane(s);
    return `${topbar(s)}${subtabBar(s)}${sub}`; }
  function pane(s) { if (s.tab !== "fn" && !path) return `<p class="model-empty">当前角色只有群聊模型入口，此处的模型连接服务暂不可用。功能模型仍可设置角色默认与本群覆盖。</p>`; if (s.tab === "voice") return voicePane(s); if (s.tab === "image") return imagePane(s); if (s.tab === "search") return searchPane(s); if (s.tab === "laya") return layaPane(s); if (s.tab === "pricing") return pricingWorkbench()?.renderPane?.(pricingCtx(s)) || '<p class="model-empty">计价模块未加载。</p>'; if (s.tab === "quota") return quotaWorkbench()?.renderPane?.(quotaCtx(s)) || '<p class="model-empty">额度看板模块未加载。</p>'; return fnPane(s); }
  function providerDrawerSpec(s) { const id = s.drawer; if (!id) return null;
    const provider = s.status?.providers.find(p => p.id === id); if (!provider) return null;
    const tasks = snapshot(s), asMain = tasks.filter(t => providerFor(t, s.drafts.get(t.kind)) === id);
    const asFb = tasks.filter(t => rowFallback(t, s.drafts.get(t.kind)) === id);
    return {
      mode: "provider",
      kind: id,
      label: `模型提供方 ${id}`,
      closeAttr: "data-drawer-close",
      extraClass: "",
      html: `<div class="dh"><div><h3>${esc(id)}</h3><p>${esc(provider.capabilities.map(capabilityLabel).join(" · ") || "无能力声明")}</p></div>
        <button type="button" class="x" data-drawer-close aria-label="关闭">×</button></div>
      <dl class="model-kv"><dt>单次输出上限</dt><dd>${provider.max_output_tokens}</dd>
        <dt>承担任务</dt><dd>${asMain.length} 项主路由 · ${asFb.length} 项备用</dd></dl>
      <div class="model-sec"><h4>作为主模型 · ${asMain.length} 项</h4>${asMain.map(t => `<div class="li"><button type="button" class="model-lk" data-model-open="${esc(t.kind)}">${esc(taskName(t.kind).label)}</button><span>${esc(taskName(t.kind).description)}</span></div>`).join("") || '<span class="model-mono">无</span>'}</div>
      <div class="model-sec"><h4>作为备用 · ${asFb.length} 项</h4>${asFb.map(t => `<div class="li"><button type="button" class="model-lk" data-model-open="${esc(t.kind)}">${esc(taskName(t.kind).label)}</button><span>${esc(taskName(t.kind).description)}</span></div>`).join("") || '<span class="model-mono">无</span>'}</div>
      <div class="model-sec"><button type="button" class="button primary" style="width:100%" data-drawer-goto="${esc(id)}">在任务路由中筛选此提供方</button></div>`,
    }; }
  function activeOverlaySpec(s) {
    const providerSpec = providerDrawerSpec(s);
    if (providerSpec) return providerSpec;
    if (s.tab === "fn" && s.sub !== "pol") return taskInspectorSpec(s);
    return null;
  }
  function overlayMarkup(spec) {
    if (!spec) return "";
    const cls = spec.extraClass ? `model-drawer ${spec.extraClass} on` : "model-drawer on";
    return `<div class="model-mask on" ${spec.closeAttr}></div><aside class="${cls}" role="dialog" aria-modal="true" aria-label="${esc(spec.label)}">${spec.html}</aside>`;
  }
  function isReducedMotion() {
    if (typeof document === "undefined") return true;
    const motion = document.documentElement?.dataset?.motion;
    if (motion === "off") return true;
    if (motion !== "low" && document.body?.classList?.contains?.("reduce-motion")) return true;
    return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  }
  function clearClosedDrawerContent() {
    if (overlayCloseTimer) { clearTimeout(overlayCloseTimer); overlayCloseTimer = null; }
    if (!overlayWasOpen && wsDrawer && !wsDrawer.classList.contains("on")) {
      wsDrawer.innerHTML = "";
      wsDrawer.className = "model-drawer";
      wsDrawer.removeAttribute("aria-label");
    }
  }
  function handleDrawerTransitionEnd(event) {
    if (event.target !== wsDrawer) return;
    if (event.propertyName === "transform" || event.propertyName === "opacity") {
      clearClosedDrawerContent();
    }
  }
  function clearClosedDockContent() {
    if (dockCloseTimer) { clearTimeout(dockCloseTimer); dockCloseTimer = null; }
    if (!dockWasOpen && wsDock && !wsDock.classList.contains("is-open")) {
      wsDock.innerHTML = "";
    }
  }
  function handleDockTransitionEnd(event) {
    if (event.target !== wsDock) return;
    if (event.propertyName === "transform" || event.propertyName === "opacity") {
      clearClosedDockContent();
    }
  }
  function syncLiveDock(s) {
    if (!wsDock) return;
    const info = selectionDockInner(s);
    if (info.hasDock) {
      if (dockCloseTimer) { clearTimeout(dockCloseTimer); dockCloseTimer = null; }
      if (reportCloseTimer) { clearTimeout(reportCloseTimer); reportCloseTimer = null; }
      const wasOpen = dockWasOpen && wsDock.classList.contains("is-open");
      const prevHadReport = dockHadReport;
      if (!wasOpen) {
        wsDock.className = "model-dock";
        wsDock.innerHTML = info.html;
        wsDock.setAttribute("aria-hidden", "false");
        wsDock.inert = false;
        void wsDock.offsetHeight;
        wsDock.classList.add("is-open");
      } else {
        wsDock.innerHTML = info.html;
        wsDock.classList.add("is-open");
        wsDock.setAttribute("aria-hidden", "false");
        wsDock.inert = false;
        if (!prevHadReport && info.hasReport && !isReducedMotion()) {
          wsDock.querySelector?.(".model-tres")?.classList.add("is-entering");
        }
      }
      dockWasOpen = true;
      dockHadReport = info.hasReport;
      return;
    }
    dockHadReport = false;
    if (dockWasOpen || wsDock.classList.contains("is-open")) {
      dockWasOpen = false;
      if (typeof document !== "undefined" && wsDock.contains(document.activeElement)) {
        try { document.activeElement.blur(); } catch { }
      }
      wsDock.classList.remove("is-open");
      wsDock.setAttribute("aria-hidden", "true");
      wsDock.inert = true;
      if (dockCloseTimer) clearTimeout(dockCloseTimer);
      if (isReducedMotion()) {
        clearClosedDockContent();
      } else {
        dockCloseTimer = setTimeout(clearClosedDockContent, 240);
      }
    }
  }
  function closeReportLive(s) {
    if (!useLiveDom() || !wsDock) return false;
    if (!selected(s).length || isReducedMotion()) {
      s.rule.report = null;
      syncLiveDock(s);
      return true;
    }
    const tres = wsDock.querySelector?.(".model-tres");
    if (!tres) {
      s.rule.report = null;
      syncLiveDock(s);
      return true;
    }
    tres.classList.remove("is-entering");
    tres.classList.add("is-leaving");
    if (reportCloseTimer) clearTimeout(reportCloseTimer);
    reportCloseTimer = setTimeout(() => {
      reportCloseTimer = null;
      if (state === s) {
        s.rule.report = null;
        syncLiveDock(s);
      }
    }, 180);
    return true;
  }
  function syncSelectionInPlace(s) {
    if (!useLiveDom() || !wsMain || s.tab !== "fn" || s.sub === "pol" || s.view !== "tasks") return false;
    const pickedSet = new Set(s.selection);
    wsMain.querySelectorAll("[data-task-row]").forEach(row => {
      const kind = row.dataset.taskRow;
      const picked = pickedSet.has(kind);
      row.classList.toggle("is-picked", picked);
      const ck = row.querySelector("[data-model-pick]");
      if (ck) ck.checked = picked;
    });
    wsMain.querySelectorAll("[data-route-group]").forEach(sec => {
      const cks = [...sec.querySelectorAll("[data-model-pick]:not(:disabled)")];
      const selCount = cks.filter(ck => ck.checked).length;
      const allSel = cks.length > 0 && selCount === cks.length;
      sec.classList.toggle("has-picked", selCount > 0);
      const gck = sec.querySelector("[data-model-gpick]");
      if (gck) gck.checked = allSel;
      const meta = sec.querySelector(".model-rgroup-meta");
      if (meta && meta.dataset.groupBase) {
        meta.textContent = selCount ? `${meta.dataset.groupBase} · 已选 ${selCount}` : meta.dataset.groupBase;
      }
    });
    const openCard = wsDrawer?.querySelector?.("[data-fncard]");
    if (openCard) openCard.classList.toggle("picked", pickedSet.has(openCard.dataset.fncard));
    syncLiveDock(s);
    return true;
  }
  function toggleRouteGroupLive(s, gk) {
    if (!useLiveDom() || !wsMain) return false;
    const sec = [...wsMain.querySelectorAll("[data-route-group]")].find(el => el.dataset.routeGroup === gk);
    if (!sec) return false;
    const collapsed = !!s.groupCollapsed[gk];
    sec.classList.toggle("is-collapsed", collapsed);
    sec.querySelector("[data-model-group-toggle]")?.setAttribute("aria-expanded", String(!collapsed));
    const inner = sec.querySelector(".model-rgroup-inner");
    if (inner) { inner.setAttribute("aria-hidden", String(collapsed)); inner.inert = collapsed; }
    return true;
  }
  function toggleLegendLive(s) {
    if (!useLiveDom() || !wsMain) return false;
    const box = wsMain.querySelector(".model-mlgd-box");
    if (!box) return false;
    const open = !!s.legendOpen;
    box.classList.toggle("is-open", open);
    if (open) box.setAttribute("open", ""); else box.removeAttribute("open");
    const btn = box.querySelector("[data-model-legend-toggle]");
    btn?.setAttribute("aria-expanded", String(open));
    const sm = btn?.querySelector("small");
    if (sm) sm.textContent = `${(s.status?.providers || []).length} 个 · ${open ? "收起" : "点击展开"}`;
    const inner = box.querySelector(".model-collapse-inner");
    if (inner) { inner.setAttribute("aria-hidden", String(!open)); inner.inert = !open; }
    return true;
  }
  function flashQuickCard(g) {
    try {
      const card = [...(root?.querySelectorAll?.("[data-quick-card]") || [])].find(el => el.dataset.quickCard === g);
      if (!card) return;
      card.scrollIntoView?.({ block: "nearest", behavior: isReducedMotion() ? "auto" : "smooth" });
      if (!isReducedMotion()) {
        card.classList.remove("is-flash");
        void card.offsetWidth;
        card.classList.add("is-flash");
      }
    } catch { }
  }
  function toggleQuickBoxLive(s, opening, flashGroup = "") {
    if (!useLiveDom() || !wsMain) return false;
    const box = wsMain.querySelector(".model-quick-box");
    const inner = box?.querySelector(":scope > .model-collapse-shell > .model-collapse-inner");
    if (!box || !inner) return false;
    if (quickCloseTimer) { clearTimeout(quickCloseTimer); quickCloseTimer = null; }
    const btn = box.querySelector("[data-model-quick-toggle]");
    btn?.setAttribute("aria-expanded", String(opening));
    const txt = btn?.querySelector(".model-quick-state-txt");
    if (txt) txt.textContent = opening ? "收起" : "展开批量指派";
    if (opening) {
      inner.innerHTML = quickBodyMarkup(s);
      inner.setAttribute("aria-hidden", "false");
      inner.inert = false;
      void box.offsetHeight;
      box.classList.add("is-open");
      window.lucide?.createIcons?.({attrs: {"aria-hidden": "true"}});
      if (flashGroup) setTimeout(() => flashQuickCard(flashGroup), isReducedMotion() ? 0 : 120);
    } else {
      box.classList.remove("is-open");
      inner.setAttribute("aria-hidden", "true");
      inner.inert = true;
      const cleanup = () => {
        quickCloseTimer = null;
        if (state === s && !s.quickOpen && !box.classList.contains("is-open")) inner.innerHTML = "";
      };
      if (isReducedMotion()) cleanup();
      else quickCloseTimer = setTimeout(cleanup, 260);
    }
    return true;
  }
  function toggleQuickExpLive(s, g) {
    if (!useLiveDom() || !wsMain) return false;
    const card = [...wsMain.querySelectorAll("[data-quick-card]")].find(el => el.dataset.quickCard === g);
    const shell = card?.querySelector("[data-model-exp-shell]");
    const inner = shell?.querySelector(".model-collapse-inner");
    const btn = card?.querySelector("[data-model-exp]");
    if (!card || !shell || !inner || !btn) return false;
    const open = !!s.expOpen[g];
    const gp = taskGroups(s).find(([name]) => name === g);
    const kinds = (gp?.[1] || []).map(([k]) => k).filter(k => !coreReply(taskOf(s, k)));
    const own = kinds.filter(k => { const d = s.drafts.get(k); return d && fields.some(name => !d.inherited[name]); }).length;
    btn.setAttribute("aria-expanded", String(open));
    const lbl = btn.querySelector("span");
    if (lbl) lbl.textContent = `${open ? "收起" : "覆盖"} ${kinds.length} 项${own ? ` · ${own} 项单独设置` : ""}`;
    if (open) {
      inner.innerHTML = quickExpListHtml(s, kinds);
      inner.setAttribute("aria-hidden", "false");
      inner.inert = false;
      void shell.offsetHeight;
      shell.classList.add("is-open");
    } else {
      shell.classList.remove("is-open");
      inner.setAttribute("aria-hidden", "true");
      inner.inert = true;
      const finish = () => {
        if (state === s && !s.expOpen[g] && !shell.classList.contains("is-open")) inner.innerHTML = "";
      };
      if (isReducedMotion()) finish();
      else setTimeout(finish, 240);
    }
    return true;
  }
  function scrollAdvIntoView() {
    try {
      const advEl = (wsDrawer || root)?.querySelector?.(".model-adv");
      advEl?.scrollIntoView?.({ block: "nearest", behavior: isReducedMotion() ? "auto" : "smooth" });
    } catch { }
  }
  function toggleAdvLive(s, kind, opening) {
    if (!useLiveDom() || !wsDrawer) return false;
    const adv = wsDrawer.querySelector(".model-adv");
    const btn = adv?.querySelector("[data-adv-toggle]");
    const inner = adv?.querySelector(".model-adv-inner");
    if (!adv || !btn || !inner) return false;
    if (advAnimTimer) { clearTimeout(advAnimTimer); advAnimTimer = null; }
    btn.setAttribute("aria-expanded", String(opening));
    if (opening) {
      inner.setAttribute("aria-hidden", "false");
      inner.inert = false;
      adv.setAttribute("open", "");
      if (isReducedMotion()) {
        adv.classList.remove("is-animating");
        adv.classList.add("is-open");
        scrollAdvIntoView();
      } else {
        adv.classList.add("is-animating");
        void adv.offsetHeight;
        adv.classList.add("is-open");
        advAnimTimer = setTimeout(() => {
          advAnimTimer = null;
          adv.classList.remove("is-animating");
          scrollAdvIntoView();
        }, 270);
      }
    } else {
      adv.classList.add("is-animating");
      adv.classList.remove("is-open");
      adv.removeAttribute("open");
      inner.setAttribute("aria-hidden", "true");
      inner.inert = true;
      if (isReducedMotion()) {
        adv.classList.remove("is-animating");
      } else {
        advAnimTimer = setTimeout(() => {
          advAnimTimer = null;
          adv.classList.remove("is-animating");
        }, 220);
      }
    }
    return true;
  }
  const useLiveDom = () => Boolean(root && typeof document !== "undefined" && typeof document.createElement === "function" && typeof root.appendChild === "function");
  function ensureLiveShell() {
    if (wsShell && wsShell.parentNode === root && wsMain && wsDock && wsMask && wsDrawer && wsQuotaModalHost) return;
    if (wsDrawer) wsDrawer.removeEventListener("transitionend", handleDrawerTransitionEnd);
    if (wsDock) wsDock.removeEventListener("transitionend", handleDockTransitionEnd);
    root.innerHTML = "";
    wsShell = document.createElement("div");
    wsShell.className = "model-workspace";
    wsMain = document.createElement("div");
    wsMain.className = "model-workspace-main";
    wsDock = document.createElement("div");
    wsDock.className = "model-dock";
    wsDock.setAttribute("role", "region");
    wsDock.setAttribute("aria-label", "已选任务批量操作");
    wsDock.setAttribute("aria-hidden", "true");
    wsDock.inert = true;
    wsDock.addEventListener("transitionend", handleDockTransitionEnd);
    wsMask = document.createElement("div");
    wsMask.className = "model-mask";
    wsMask.setAttribute("aria-hidden", "true");
    wsDrawer = document.createElement("aside");
    wsDrawer.className = "model-drawer";
    wsDrawer.setAttribute("role", "dialog");
    wsDrawer.setAttribute("aria-modal", "true");
    wsDrawer.setAttribute("aria-hidden", "true");
    wsDrawer.inert = true;
    wsDrawer.addEventListener("transitionend", handleDrawerTransitionEnd);
    wsQuotaModalHost = document.createElement("div");
    wsQuotaModalHost.className = "qw-modal-host";
    wsQuotaModalHost.setAttribute("data-qw-modal-host", "");
    wsQuotaModalHost.setAttribute("aria-hidden", "true");
    wsShell.appendChild(wsMain);
    wsShell.appendChild(wsDock);
    wsShell.appendChild(wsMask);
    wsShell.appendChild(wsDrawer);
    wsShell.appendChild(wsQuotaModalHost);
    root.appendChild(wsShell);
    overlayWasOpen = false;
    dockWasOpen = false;
    dockHadReport = false;
  }
  function syncLiveOverlay(spec) {
    if (!wsMask || !wsDrawer) return;
    if (spec) {
      if (overlayCloseTimer) { clearTimeout(overlayCloseTimer); overlayCloseTimer = null; }
      const prevCard = wsDrawer.querySelector?.("[data-fncard]");
      const prevKind = prevCard?.dataset?.fncard || prevCard?.getAttribute?.("data-fncard") || "";
      const prevInspScroll = prevKind ? (wsDrawer.querySelector?.(".model-insp-body")?.scrollTop || 0) : 0;
      if (spec.closeAttr === "data-drawer-close") {
        wsMask.setAttribute("data-drawer-close", "");
        wsMask.removeAttribute("data-inspector-close");
      } else {
        wsMask.setAttribute("data-inspector-close", "");
        wsMask.removeAttribute("data-drawer-close");
      }
      const wasOpen = overlayWasOpen && wsDrawer.classList.contains("on");
      const targetClass = spec.extraClass ? `model-drawer ${spec.extraClass}` : "model-drawer";
      if (!wasOpen) {
        wsDrawer.className = targetClass;
        wsDrawer.setAttribute("aria-label", spec.label);
        wsDrawer.innerHTML = spec.html;
        wsDrawer.setAttribute("aria-hidden", "false");
        wsDrawer.inert = false;
        wsMask.setAttribute("aria-hidden", "false");
        void wsDrawer.offsetWidth;
        wsMask.classList.add("on");
        wsDrawer.classList.add("on");
      } else {
        wsDrawer.className = `${targetClass} on`;
        wsDrawer.setAttribute("aria-label", spec.label);
        wsDrawer.innerHTML = spec.html;
        wsDrawer.setAttribute("aria-hidden", "false");
        wsDrawer.inert = false;
        wsMask.classList.add("on");
        wsMask.setAttribute("aria-hidden", "false");
        if (spec.mode === "inspector" && prevKind === spec.kind && prevInspScroll > 0) {
          const nextBody = wsDrawer.querySelector?.(".model-insp-body");
          if (nextBody) nextBody.scrollTop = prevInspScroll;
        }
      }
      overlayWasOpen = true;
      return;
    }
    if (overlayWasOpen || wsDrawer.classList.contains("on") || wsMask.classList.contains("on")) {
      overlayWasOpen = false;
      if (typeof document !== "undefined" && wsDrawer.contains(document.activeElement)) {
        try { document.activeElement.blur(); } catch { }
      }
      wsMask.classList.remove("on");
      wsMask.setAttribute("aria-hidden", "true");
      wsDrawer.classList.remove("on");
      wsDrawer.setAttribute("aria-hidden", "true");
      wsDrawer.inert = true;
      if (overlayCloseTimer) clearTimeout(overlayCloseTimer);
      if (isReducedMotion()) {
        clearClosedDrawerContent();
      } else {
        overlayCloseTimer = setTimeout(clearClosedDrawerContent, 300);
      }
    }
  }
  function render() { if (!root || !state) return; const s = state;
    const spec = activeOverlaySpec(s);
    const mainHtml = `${s.tab === "fn" ? semanticBanner() : ""}${tabBar(s)}${(s.bad || s.denied) ? notice(s) : ""}${pane(s)}`;
    if (useLiveDom()) {
      ensureLiveShell();
      const qwb = s.tab === "quota" && path ? quotaWorkbench() : null;
      if (qwb && typeof qwb.syncDom === "function") {
        let tabsHost = wsMain.querySelector?.("[data-quota-tabs-host]");
        let noticeHost = wsMain.querySelector?.("[data-quota-notice-host]");
        let mountHost = wsMain.querySelector?.("[data-quota-mount]");
        if (!tabsHost || !noticeHost || !mountHost) {
          wsMain.innerHTML = "";
          tabsHost = document.createElement("div");
          tabsHost.setAttribute("data-quota-tabs-host", "");
          noticeHost = document.createElement("div");
          noticeHost.setAttribute("data-quota-notice-host", "");
          mountHost = document.createElement("div");
          mountHost.setAttribute("data-quota-mount", "");
          wsMain.appendChild(tabsHost);
          wsMain.appendChild(noticeHost);
          wsMain.appendChild(mountHost);
          tabsHost.innerHTML = tabBar(s);
        } else {
          syncQuotaTabBadge(s);
        }
        const noticeHtml = (s.bad || s.denied) ? notice(s) : "";
        if (noticeHost.innerHTML !== noticeHtml) noticeHost.innerHTML = noticeHtml;
        qwb.syncDom(quotaCtx(s), mountHost, wsQuotaModalHost);
      } else {
        if (wsMain.querySelector?.("[data-quota-mount]")) {
          quotaWorkbench()?.detachDom?.(s.quota);
          if (wsQuotaModalHost) {
            wsQuotaModalHost.innerHTML = "";
            wsQuotaModalHost.setAttribute("aria-hidden", "true");
          }
        }
        wsMain.innerHTML = mainHtml;
      }
      syncLiveDock(s);
      syncLiveOverlay(spec);
      syncModelTabsOverflow();
    } else {
      const prevCard = root.querySelector?.("[data-fncard]");
      const prevKind = prevCard?.dataset?.fncard || prevCard?.getAttribute?.("data-fncard") || "";
      const prevInspScroll = prevKind ? (root.querySelector?.(".model-insp-body")?.scrollTop || 0) : 0;
      root.innerHTML = `<div class="model-workspace">${mainHtml}${overlayMarkup(spec)}</div>`;
      if (spec?.mode === "inspector" && prevKind === spec.kind && prevInspScroll > 0) {
        const nextBody = root.querySelector?.(".model-insp-body");
        if (nextBody) nextBody.scrollTop = prevInspScroll;
      }
    }
    window.lucide?.createIcons?.({attrs: {"aria-hidden": "true"}}); }
  function handleKeydown(event) {
    const s = state; if (!s || !root?.isConnected) return;
    if (s.tab === "quota") {
      if (quotaWorkbench()?.handleKeydown?.(quotaCtx(s), event)) event.preventDefault?.();
      return;
    }
    if (s.tab !== "fn") return;
    if (event.key === "Escape") {
      if (s.drawer) { s.drawer = null; render(); event.preventDefault?.(); return; }
      if (s.expanded) { s.expanded = null; render(); event.preventDefault?.(); return; }
    }
    if ((event.key === "ArrowUp" || event.key === "ArrowDown") && s.sub === "cfg" && s.expanded && !s.drawer) {
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      const tag = String(event.target?.tagName || "").toUpperCase();
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || event.target?.isContentEditable) return;
      stepInspector(s, event.key === "ArrowUp" ? -1 : 1);
      event.preventDefault?.();
    }
  }
  function input(event) { const s = state; if (!s || !root?.isConnected) return;
    if (s.tab === "voice") { voiceInput(s, event); return; }
    if (s.tab === "image") { imageInput(s, event); return; }
    if (s.tab === "search") { searchInput(s, event); return; }
    if (s.tab === "laya") { layaInput(s, event); return; }
    if (s.tab === "pricing") { pricingWorkbench()?.handleInput?.(pricingCtx(s), event); return; }
    if (s.tab === "quota") { quotaWorkbench()?.handleInput?.(quotaCtx(s), event); return; }
    fnInput(event); }
  function loadTabData(s, id) {
    if (!s) return;
    if (id === "fn") {
      if (!s.status && !s.loading) refresh().catch(() => {});
      return;
    }
    if (!path) return;
    if (id === "voice" && !s.voice.loaded) voiceRefresh(s);
    else if (id === "image" && !s.image.loaded) imageRefresh(s);
    else if (id === "search" && !s.search.loaded) searchRefresh(s);
    else if (id === "laya" && !s.laya.loaded) layaRefresh(s);
    else if (id === "pricing" && !s.pricing?.loaded) pricingWorkbench()?.refresh?.(pricingCtx(s));
    else if (id === "quota" && !s.quota?.loaded) quotaWorkbench()?.refresh?.(quotaCtx(s));
  }
  function selectTab(id, { notify = true } = {}) {
    if (!MODEL_TABS.has(id) || !state || !root?.isConnected) return false;
    if (state.tab !== id) {
      state.tab = id;
      if (notify) onTabChange?.(id);
      loadTabData(state, id);
      render();
      const activeTab = root.querySelector?.(`.model-tabs [data-model-tab="${id}"]`);
      activeTab?.scrollIntoView?.({ inline: "nearest", block: "nearest" });
      syncModelTabsOverflow();
    } else {
      loadTabData(state, id);
    }
    return true;
  }
  function focusQuota(section = "awareness") {
    if (!state || !root?.isConnected) return false;
    selectTab("quota", { notify: false });
    if (section === "awareness") quotaWorkbench()?.openAwareness?.(quotaCtx(state));
    return true;
  }
  function handlePointerDown(event) {
    if (event.target?.closest?.("button, [data-inspector-close], [data-drawer-close], [data-adv-toggle], .model-mask")) {
      if (pointerResetTimer) { clearTimeout(pointerResetTimer); pointerResetTimer = null; }
      deferChangeRender = true;
    }
  }
  function handlePointerUp() {
    if (!deferChangeRender) return;
    if (pointerResetTimer) clearTimeout(pointerResetTimer);
    pointerResetTimer = setTimeout(() => {
      pointerResetTimer = null;
      deferChangeRender = false;
      if (deferredRenderPending) {
        deferredRenderPending = false;
        render();
      }
    }, 0);
  }
  function click(event) {
    if (pointerResetTimer) { clearTimeout(pointerResetTimer); pointerResetTimer = null; }
    deferChangeRender = false;
    deferredRenderPending = false;
    if (window.CompanionWorkbench.scopeHintClick(event)) return; const s = state; if (!s || !root?.isConnected) return;
    const tab = event.target.closest("[data-model-tab]");
    if (tab) { selectTab(tab.dataset.modelTab); return; }
    if (s.tab === "voice") { voiceClick(s, event); return; }
    if (s.tab === "image") { imageClick(s, event); return; }
    if (s.tab === "search") { searchClick(s, event); return; }
    if (s.tab === "laya") { layaClick(s, event); return; }
    if (s.tab === "pricing") { pricingWorkbench()?.handleClick?.(pricingCtx(s), event); return; }
    if (s.tab === "quota") { quotaWorkbench()?.handleClick?.(quotaCtx(s), event); return; }
    fnClick(event); }
  function providerHover(event) {
    window.CompanionWorkbench.scopeHintReset(event);
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
  function unmount() { version++;
    if (pointerResetTimer) { clearTimeout(pointerResetTimer); pointerResetTimer = null; }
    if (overlayCloseTimer) { clearTimeout(overlayCloseTimer); overlayCloseTimer = null; }
    if (dockCloseTimer) { clearTimeout(dockCloseTimer); dockCloseTimer = null; }
    if (reportCloseTimer) { clearTimeout(reportCloseTimer); reportCloseTimer = null; }
    if (quickCloseTimer) { clearTimeout(quickCloseTimer); quickCloseTimer = null; }
    if (advAnimTimer) { clearTimeout(advAnimTimer); advAnimTimer = null; }
    if (wsDrawer) wsDrawer.removeEventListener("transitionend", handleDrawerTransitionEnd);
    if (wsDock) wsDock.removeEventListener("transitionend", handleDockTransitionEnd);
    wsShell = wsMain = wsDock = wsMask = wsDrawer = wsQuotaModalHost = null; overlayWasOpen = false; dockWasOpen = false; dockHadReport = false;
    deferChangeRender = false; deferredRenderPending = false;
    if (root) { window.CompanionWorkbench.dismissScopeHints(root); root.removeEventListener("input", input); root.removeEventListener("change", input); root.removeEventListener("click", click); root.removeEventListener("pointerdown", handlePointerDown); root.removeEventListener("pointerover", providerHover); root.removeEventListener("pointerout", providerHover); root.removeEventListener("keydown", window.CompanionWorkbench.scopeHintKeydown); root.removeEventListener("focusin", window.CompanionWorkbench.scopeHintReset); root.removeEventListener("focusout", window.CompanionWorkbench.scopeHintReset); }
    if (typeof document !== "undefined" && typeof document.removeEventListener === "function") { document.removeEventListener("keydown", handleKeydown); document.removeEventListener("pointerup", handlePointerUp); document.removeEventListener("pointercancel", handlePointerUp); }
    if (typeof window !== "undefined" && typeof window.removeEventListener === "function") { window.removeEventListener("resize", handleModelTabsResize); }
    if (state) { quotaWorkbench()?.unmount?.(state.quota); state.loading = false; state.busy = false; state.globalBusy = false; }
    root = null; state = null; path = taskPath = key = null; onTabChange = null; }
  function mount(element, options = {}) { unmount(); root = element; if (!root) return; onTabChange = typeof options.onTabChange === "function" ? options.onTabChange : null; const product = window.CompanionProduct;
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
    /* 任务配置跟随显式窗口；其他模型服务使用当前角色提供的完整接口。 */
    taskPath = endpoint.slice(PREFIX.length);
    const owner=product.roleTarget?.(),selected=product.role?.();
    const roleEndpoint=owner?.roles?.find(item=>item.role_id===selected?.role_id)?.model_api;
    path = !Object.hasOwn(options,"endpoint") && owner?.scope_kind === "group" ? null
      : !Object.hasOwn(options,"endpoint") && roleEndpoint?.startsWith(PREFIX)
        ? roleEndpoint.slice(PREFIX.length) : taskPath;
    windowAvailable=Object.hasOwn(options,"endpoint") || !product.selection || Boolean(product.selection()?.target_id);
    key = "companion-model-edit.v2:" + JSON.stringify([identity.installation_id, identity.operator_id, taskPath, windowAvailable]);
    state = states.get(key) || { status: null, drafts: new Map(), selection: [], expanded: null, layer: "role", view: "tasks", custom: null, lane: "all", providerFilter: null, query: "", sub: "cfg", av: "slot", pm: "kw", slotDraft: {}, quickOpen: false, legendOpen: false, groupCollapsed: {}, advOpen: {}, providerTests: {}, testing: new Set(),
      globalDraft: null, globalBusy: false, globalNotice: "", globalBad: false, globalRestartRequired: false,
      drawer: null, expOpen: {}, pol: null, rule: { query: "", picked: [], report: null, testing: false }, peak: { on: true, provider: "", start: "09:00", end: "12:00", windows: "09:00-12:00\n14:00-18:00" },
      tab: "fn",
      voice: { data: null, draft: null, loaded: false, loading: false, busy: false, testing: false, bad: false, notice: "", ui: { lang: "ja" },
        editorOpen: false, catalog: null, catalogLoading: false, editorId: null, editorValues: {}, creating: false, ttsBusy: false, ttsTesting: false, ttsTestResults: {}, createDraft: { type: "", id: "" } },
      image: { data: null, draft: null, loaded: false, loading: false, busy: false, testing: null, endpointTesting: null, endpointTests: {}, endpointTestKeys: {}, bad: false, notice: "",
        view: "generate", config: null, configBase: null, configDraft: null, configText: null, configTextBase: null,
        configLoading: false, configSaving: false, configBad: false, configNotice: "",
      },
      laya: { data: null, draft: null, logs: [], logsScope: "all", logsScopeKind: "", logsLoading: false, loaded: false, loading: false, busy: false, stale: false, bad: false, notice: "", test: null, preview: null, previewText: "" },
      search: { data: null, draft: null, loaded: false, loading: false, busy: false, stale: false, bad: false, notice: "" },
      pricing: pricingWorkbench()?.createState?.() || { loaded: false, loading: false, busy: false, data: null, draft: null },
      quota: quotaWorkbench()?.createState?.() || { loaded: false, loading: false, busy: false, data: null, wizard: null },
      loading: false, busy: false, bad: false, denied: false, notice: "" };
    state.layer ||= "role"; if(!windowAvailable)state.layer="role"; state.providerTests ||= {}; state.testing ||= new Set();
    state.quickOpen = Boolean(state.quickOpen); state.legendOpen = Boolean(state.legendOpen); state.groupCollapsed ||= {}; state.advOpen ||= {};
    state.globalDraft ??= null; state.globalBusy = false; state.globalNotice ||= ""; state.globalBad = Boolean(state.globalBad); state.globalRestartRequired = Boolean(state.globalRestartRequired);
    /* 展开状态按对象保存：刷新、切任务、切页回来都保持上次展开的分组，
       不再每次回到“全收起”或“全展开”。 */
    state.expOpen = { ...restoreExpanded(key), ...(state.expOpen || {}) }; state.voice.lastTest ||= null; state.voice.editorValues ||= {}; state.voice.ttsTestResults ||= {}; state.voice.ttsTesting ||= false; state.voice.createDraft ||= { type: "", id: "" }; state.image.endpointTests ||= {}; state.image.endpointTestKeys ||= {}; state.image.endpointTesting ||= null; state.configDraft ||= null; state.peak ||= { on: true, provider: "", start: "09:00", end: "12:00" }; state.testing ||= new Set(); state.peak.windows ||= "09:00-12:00\n14:00-18:00"; state.laya ||= { data: null, draft: null, logs: [], logsScope: "all", logsScopeKind: "", logsLoading: false, loaded: false, loading: false, busy: false, stale: false, bad: false, notice: "", test: null, preview: null, previewText: "" }; state.laya.logsScope ||= "all"; state.laya.logsScopeKind ||= ""; state.search ||= { data: null, draft: null, loaded: false, loading: false, busy: false, stale: false, bad: false, notice: "" }; state.pricing ||= pricingWorkbench()?.createState?.() || { loaded: false, loading: false, busy: false, data: null, draft: null }; state.quota ||= quotaWorkbench()?.createState?.() || { loaded: false, loading: false, busy: false, data: null, wizard: null }; state.hoverProvider = "";
    const requestedTab = typeof options.initialTab === "string" && MODEL_TABS.has(options.initialTab) ? options.initialTab : "";
    if (requestedTab) {
      state.tab = requestedTab;
      pricingWorkbench()?.consumePendingTab?.();
      quotaWorkbench()?.consumePendingTab?.();
    } else if (pricingWorkbench()?.consumePendingTab?.()) {
      state.tab = "pricing";
    } else if (quotaWorkbench()?.consumePendingTab?.()) {
      state.tab = "quota";
    } else if (!MODEL_TABS.has(state.tab)) {
      state.tab = "fn";
    }
    states.set(key, state);
    state.sub = options.sub === "pol" ? "pol" : "cfg";
    root.addEventListener("input", input); root.addEventListener("change", input); root.addEventListener("click", click); root.addEventListener("pointerdown", handlePointerDown); root.addEventListener("pointerover", providerHover); root.addEventListener("pointerout", providerHover); root.addEventListener("keydown", window.CompanionWorkbench.scopeHintKeydown); root.addEventListener("focusin", window.CompanionWorkbench.scopeHintReset); root.addEventListener("focusout", window.CompanionWorkbench.scopeHintReset);
    if (typeof document !== "undefined" && typeof document.addEventListener === "function") { document.addEventListener("keydown", handleKeydown); document.addEventListener("pointerup", handlePointerUp); document.addEventListener("pointercancel", handlePointerUp); }
    if (typeof window !== "undefined" && typeof window.addEventListener === "function") { window.addEventListener("resize", handleModelTabsResize, { passive: true }); }
    render();
    loadTabData(state, state.tab); }
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
    if (globalDefaultsDirty(s)) {
      const g = globalDefaultsInfo(s);
      if (s.globalDraft.provider_id !== g.provider_id) changed.push('陪伴通用模型 · 全局主模型提供方');
      if (s.globalDraft.fallback_provider_id !== g.fallback_provider_id) changed.push('陪伴通用模型 · 全局备用模型提供方');
    }
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
        remote_model:'判断模型名', api_key:'API Key', proxy:'代理地址', timeout_seconds:'请求超时', followup_confidence_threshold:'连续接话置信阈值'};
      for (const name of Object.keys(layaChanges(s.laya))) {
        if (name === 'decision_fields') changed.push(...LAYA_DECISION_FIELDS.filter(([key]) =>
          s.laya.draft.decision_fields[key] !== (s.laya.data.decision_fields?.[key] !== false)).map(([, label]) => `Laya · ${label}`));
        else changed.push(`Laya · ${labels[name] || name}`);
      }
    }
    if (s.search && searchDirty(s.search)) changed.push('联网搜索凭据来源');
    if (s.pricing && pricingWorkbench()?.changedFields) changed.push(...pricingWorkbench().changedFields(s.pricing));
    if (s.quota && quotaWorkbench()?.changedFields) changed.push(...quotaWorkbench().changedFields(s.quota));
    return changed;
  }
  function isDirty() {
    return changedFields().length > 0;
  }
  async function saveBeforeLeave() {
    const s = state;
    if (!s || !root) return true;
    if (s.busy || s.globalBusy || s.loading || s.voice?.busy || s.image?.busy || s.image?.configSaving || s.laya?.busy || s.search?.busy || pricingWorkbench()?.isBusy?.(s.pricing) || quotaWorkbench()?.isBusy?.(s.quota))
      return {error:'模型配置正在处理，请稍后保存。'};
    if ([...s.drafts.values()].some(draft => draft.pending) || s.voice?.pending)
      return {error:'上次保存结果待核对，请回到模型配置中核对原请求。'};
    if (globalDefaultsDirty(s)) {
      await saveGlobalDefaults(s);
      if (globalDefaultsDirty(s)) return {error:s.globalNotice || '陪伴通用模型尚未保存。'};
    }
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
      [s.pricing && pricingWorkbench()?.isDirty?.(s.pricing), () => pricingWorkbench().save(pricingCtx(s)), () => pricingWorkbench().isDirty(s.pricing), () => s.pricing.notice],
      [s.quota && quotaWorkbench()?.isDirty?.(s.quota), () => quotaWorkbench().save(quotaCtx(s)), () => quotaWorkbench().isDirty(s.quota), () => s.quota.wizard?.error || s.quota.notice],
    ]) {
      if (!needed) continue;
      await action(s);
      if (remaining())
        return {error:message() || '保存尚未确认，请回到配置中检查。'};
    }
    return !isDirty() ? true : {error:'还有配置未保存，改动仍保留。'};
  }
  function discardChanges() {
    if (state?.busy || state?.globalBusy || state?.voice?.busy || state?.image?.busy || state?.image?.configSaving || state?.laya?.busy || state?.search?.busy || pricingWorkbench()?.isBusy?.(state?.pricing) || quotaWorkbench()?.isBusy?.(state?.quota))
      return {error:'正在保存，请等待结果后再切换。'};
    if (key) states.delete(key);
    unmount();
    return true;
  }
  const isBusy = () => Boolean(state && (state.busy || state.globalBusy || state.voice?.busy || state.voice?.ttsBusy
    || state.voice?.ttsTesting || state.image?.busy || state.image?.configSaving || state.laya?.busy || state.search?.busy || pricingWorkbench()?.isBusy?.(state.pricing) || quotaWorkbench()?.isBusy?.(state.quota)));
  return { mount, unmount, selectTab, focusQuota, isDirty, isBusy, changedFields, saveBeforeLeave, discardChanges };
})();
