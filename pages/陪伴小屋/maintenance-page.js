"use strict";
window.CompanionMaintenancePage = (() => {
  const PREFIX = "/astrbot_plugin_private_companion";
  const MEMORY_SAMPLE_INTERVAL_MS = 10000;
  const MEMORY_SAMPLE_DURATION_MS = 180000;
  const MEMORY_SAMPLE_LIMIT = 19;
  const MEMORY_GROWTH_WATCH_BYTES = 4 * 1024 * 1024;
  const MEMORY_GROWTH_SUSPECT_BYTES = 16 * 1024 * 1024;
  const $ = (root, selector) => root.querySelector(selector);
  const fmt = (source, ...values) => source.replace(/\{\{(\d+)\}\}/g, (_, index) => String(values[index] ?? ""));
  const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
  const number = value => new Intl.NumberFormat("zh-CN", {maximumFractionDigits: 0}).format(Number(value) || 0);
  const memorySize = value => Number.isFinite(Number(value))
    ? (Number(value) / (1024 * 1024)).toFixed(1) + " MiB" : "未采集";
  const balanceAmount = value => {
    const amount = value == null || String(value).trim() === "" ? NaN : Number(value);
    return Number.isFinite(amount) ? new Intl.NumberFormat("zh-CN", {maximumFractionDigits: 6}).format(amount) : "—";
  };
  const cost = value => value == null ? "费用未观测" : "US$" + ((Number(value) || 0) / 1000000).toFixed(6);
  const percent = value => value == null ? "—" : Math.max(0, Number(value) || 0).toFixed(1) + "%";
  const localDay = value => {
    const date = value instanceof Date ? value : new Date(value || Date.now());
    if (Number.isNaN(date.getTime())) return new Date().toLocaleDateString("en-CA");
    return [date.getFullYear(), String(date.getMonth() + 1).padStart(2, "0"), String(date.getDate()).padStart(2, "0")].join("-");
  };
  const time = value => {
    if (!value) return "尚无确认";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "时间未知" : new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    }).format(date);
  };
  const taskName = value => ({
    "conversation.reply": "对话回复", "proactive.compose": "主动联系",
    "conversation.astrbot_core": "AstrBot 核心对话",
    "proactive.judge": "主动联系复核", "memory.reflect": "记忆整理",
    "role.state.maintain": "角色状态", "calendar.plan": "日程规划",
    "role.reading.consume": "阅读理解", "role.growth.progress": "成长进展",
    "role.journal.diary": "日记", "role.journal.dream": "梦境",
    "role.journal.derive": "书写整理", "role.journal.select": "素材选择",
    "role.self_review": "自我复盘", "role.expression.learn": "表达学习",
    "role.expression.review": "回复表达复核", "role.expression.contact_review": "主动表达复核",
    "content.authoring.draft": "内容起草", "conversation.context_image_caption": "图片摘要",
    "conversation.image_narration": "当前图片转述", "conversation.video_narration": "原生视频转述",
    "conversation.image_review": "图片审核", "image_review": "图片审核", "screen.interpretation": "屏幕理解",
    "screen.peek": "屏幕速览", "role.state.maintenance": "状态维护",
    "role.calendar.plan": "日历规划", "memory.reflection": "记忆反思",
    "conversation.group.review": "群聊审核",
    "reality_touch_camera": "摄像头视觉分析",
    "reality_touch": "现实陪伴文案", "wakeup_alarm_message": "起床提醒文案",
    "wakeup_feedback_intent": "起床反馈理解", "live_bili_reply": "直播回复",
    "game_persona_reply": "游戏角色回复", "game_room_model": "海龟汤主持",
    "game_draw_guess": "游戏画面识别",
  })[value] || "其他模型任务";
  const promptTaskGroups = [
    ["对话", [["conversation.reply", "对话回复", "AstrBot 核心回复的 V2 附加提示"],
      ["conversation.image_narration", "当前图片转述", "为本轮已授权图片生成简短文字描述"],
      ["conversation.video_narration", "原生视频转述", "将原视频交给支持视频输入的模型直接理解"]]],
    ["主动与记忆", [["proactive.compose", "主动联系", "有真实契机时组织联系内容"], ["proactive.judge", "主动联系复核", "复核是否符合当前人格与来源"], ["memory.reflect", "记忆整理", "从已授权素材整理认识"]]],
    ["生活与成长", [["calendar.plan", "日程规划", "基于已有目标规划下一步"], ["role.state.maintain", "角色状态", "维护角色的连续生活状态"], ["role.reading.consume", "阅读理解", "阅读实际提供的原文"], ["role.growth.progress", "成长进展", "根据已完成确认提出进展"], ["role.self_review", "自我复盘", "回看已完成的角色生活记录"], ["role.expression.learn", "表达学习", "从已授权表达样本提炼风格"]]],
    ["私人书写", [["role.journal.diary", "日记", "保存私人书写"], ["role.journal.dream", "梦境", "保存梦境记录"], ["role.journal.derive", "书写整理", "整理已有文字素材"], ["role.journal.select", "素材选择", "选择本次书写素材"]]],
    ["视觉与媒体", [["conversation.context_image_caption", "图片摘要", "从已授权图片提取可见事实"], ["image_review", "图片审核", "按合同检查图片内容"], ["conversation.image_review", "图片审核", "按合同检查图片内容"], ["screen.interpretation", "屏幕理解", "理解已授权屏幕画面"], ["screen.peek", "屏幕速览", "对已授权屏幕进行短摘要"]]],
    ["内容工作", [["content.authoring.draft", "内容起草", "根据已提供素材起草内容"]]],
  ];
  const promptTaskNames = new Map(promptTaskGroups.flatMap(([group, items]) => items.map(([kind, label, description]) => [kind, {group, label, description}])));
  const promptTaskInfo = kind => promptTaskNames.get(kind) || {group: "其他任务", label: taskName(kind), description: "V2 注册任务"};
  const stateName = value => ({
    generated: "已完成", discarded: "已收束", unknown: "结果未知",
    returned: "模型已返回", failed: "失败", started: "调用中",
    dispatched: "处理中", reserved: "已预留", not_started: "未发送",
  })[value] || "状态待核对";
  const flowStateName = value => ({
    collecting: "等待收口", generating: "正在生成", delivering: "正在投递",
    complete: "已完成", blocked: "已阻断", closed: "已关闭",
  })[value] || "状态待核对";
  const flowReasonName = value => ({
    debounce: "安静窗合并", busy_delay: "忙时延后", provider_failure: "Provider 失败",
    cancelled: "已取消", owner_closed: "流程关闭", owner_unavailable: "运行端不可用",
    capacity_wait: "等待执行额度", worker_pending: "生成任务处理中", image_review_pending: "图片核对中",
    part_spacing: "分段发送间隔", confirmed_delivery: "已确认发送", message_delivery_unresolved: "发送结果未确认",
    group_participation_skipped: "群聊选择静听",
  })[value] || "原因待核对";
  const noReplyReasonName = value => ({
    staging_terminated: "插件已停止",
    staging_disabled: "V2 未启用",
    staging_settings_invalid: "V2 配置无效",
    staging_settings_unavailable: "V2 配置不可用",
    staging_not_accepting: "V2 当前没有接收入站消息",
    staging_session_unavailable: "V2 会话未加载",
    staging_application_not_running: "V2 应用未运行",
    staging_target_not_claimed: "V2 未接管已配置目标",
    private_target_unmatched: "私聊未匹配 V2 配置目标",
    group_target_unmatched: "群聊未匹配 V2 配置目标",
    session_reply_disabled: "会话回复开关关闭",
    session_reply_check_failed: "读取会话回复开关失败",
    private_access_refusal_not_sent: "私聊权限拒绝消息未发出",
    calendar_command_reply_not_sent: "日程命令结果未发出",
    stranger_notice_disabled: "陌生私聊婉拒开关关闭",
    stranger_notice_session_unavailable: "陌生私聊婉拒服务未运行",
    stranger_notice_settings_unavailable: "陌生私聊婉拒配置不可用",
    stranger_notice_delivery_unavailable: "陌生私聊婉拒通道不可用",
    stranger_notice_cooldown: "陌生私聊婉拒处于冷却期",
    stranger_notice_daily_cap: "陌生私聊婉拒达到每日上限",
    stranger_notice_send_failed: "陌生私聊婉拒发送失败",
    stranger_notice_processing_failed: "陌生私聊婉拒处理异常",
    inbound_processing_failed: "入站处理失败",
    conversation_task_failed: "对话任务失败",
    conversation_task_expired: "对话任务超时",
    core_reply_send_denied: "已生成回复，发送前复核未通过",
  })[value] || ("未回复 · " + String(value || "原因未知"));
  // 复核拒绝的是“这条已经写好的回复”，原因码决定用户该修什么：
  // 连接断开等下一次就好，目标被删除需要改配置。
  const denialName = value => ({
    host_client_offline: "该账号的原生连接已断开",
    host_not_claimed: "事件已不在配置目标内",
    host_not_accepting: "V2 已停止接收",
    host_not_running: "V2 未运行",
    host_terminated: "V2 正在停止",
    host_target_mismatch: "本轮目标已改变",
    core_reply_target_changed: "本轮目标已改变",
    core_reply_event_changed: "原生事件已改变",
    core_reply_context_changed: "身份或路由已改变",
    core_reply_instruction_changed: "人格提示词已改变",
    host_persona_changed: "人格已改变",
    message_withdrawn: "触发消息已撤回",
    group_llm_blocked: "该群已开启模型封锁",
    private_access_dialogue_denied: "该用户已失去对话权限",
  })[value];
  const failureName = value => denialName(value) || ({
    model_budget_exhausted: "插件本地硬预算已触发",
    model_budget_soft_deferred: "插件本地软限额已触发",
    model_budget_policy_changed: "模型预算策略已变化",
    request_preparation_failed: "请求准备失败", provider_request_failed: "Provider 请求失败",
    native_response_parse_failed: "响应解析失败", provider_recheck_failed: "Provider 状态复核失败",
    role_recheck_failed: "角色状态复核失败", response_contract_rejected: "响应未通过合同校验",
    adapter_failed: "适配器异常", provider_wait_interrupted: "Provider 等待被中断",
    request_recheck_failed: "请求状态复核失败", runtime_interrupted: "运行时中断",
  })[value] || "调用异常";
  const tokenSources = [
    ["companion", "角色总用量", "已连接"],
    ["conversation", "对话 · AstrBot 核心与历史", "已连接"],
    ["astrbot_core", "AstrBot 核心回复", "已连接"],
    ["legacy_conversation", "历史 V2 回复", "已连接"],
    ["plugin", "插件附加任务", "已连接"],
    ["memory", "外部记忆插件", "未接入"],
    ["together", "一起房间", "未接入"],
    ["reality", "现实观察", "未接入"],
    ["live", "直播与表演", "未接入"],
    ["game", "共同游玩", "未接入"],
    ["main", "AstrBot 全部会话", "待同步"],
  ];
  const nativeTokenSources = new Set(["together", "reality", "live", "game"]);
  const localSourceNames = {companion: "角色总用量", conversation: "对话 · AstrBot 核心与历史",
    astrbot_core: "AstrBot 核心回复", legacy_conversation: "历史 V2 回复", plugin: "插件附加任务"};
  const sourceIcons = {companion: "layers-3", conversation: "messages-square", plugin: "shield-check",
    astrbot_core: "workflow", legacy_conversation: "history", memory: "brain", together: "users-round",
    reality: "camera", live: "radio", game: "gamepad-2", main: "workflow"};
  const diagnosticCategories = [
    ["all", "全部", "所有运行信号"], ["model", "模型链路", "Provider、失败与未确认"],
    ["identity", "身份隔离", "管理身份与角色账本边界"],
    ["tasks", "后台任务", "任务路由与能力"], ["delivery", "消息收口", "回复片段与投递回执"],
    ["proactive", "主动触达", "已记录的主动任务"], ["image_generation", "生图", "原生图片工具注册"],
    ["voice", "语音", "TTS Provider 与合成"],
    ["storage", "SQLite", "存储连接与完整性"], ["prompt", "上下文注入", "仅来源元数据"],
    ["backup", "备份与磁盘", "最近成功备份、是否跨盘与磁盘余量"],
    ["mobile", "手机网关", "启用、配对令牌与模块运行状态"],
    ["budget", "预算", "硬预算与后台软限额"], ["cache", "缓存", "Provider 实报与低命中样本"],
  ];
  const IMAGE_GENERATION_ACTION = "image.generate";
  const imageActionReady = (actions, action) => actions.some(row => row.action === action && row.registered && row.current);
  const gibibytes = value => {
    const amount = Number(value);
    return Number.isFinite(amount) && amount >= 0 ? (amount / (1024 ** 3)).toFixed(1) + " GiB" : "未知";
  };
  const mobileModuleName = value => ({
    running: "运行中", starting: "正在启动", inactive: "未运行", failed: "启动失败",
    unavailable: "不可用",
  })[value] || "状态未知";
  const hostChecksOf = data => data?.host_checks && typeof data.host_checks === "object" ? data.host_checks : {};
  const backupCheckOf = data => {
    const value = hostChecksOf(data).backup;
    return value && typeof value === "object" ? value : null;
  };
  const mobileCheckOf = data => {
    const value = hostChecksOf(data).mobile;
    return value && typeof value === "object" ? value : null;
  };
  const sourceDescriptions = {
    conversation: ["对话 · AstrBot 核心与历史", "分项统计尚未同步。", "重新加载 V2 后刷新统计。"],
    astrbot_core: ["AstrBot 核心回复", "核心回复观测尚未同步。", "本页只统计已映射到当前角色的核心回复，收到实际模型回执后显示用量。"],
    legacy_conversation: ["历史 V2 回复", "历史统计尚未同步。", "旧 V2 回复账本保留供历史对照。"],
    plugin: ["插件附加任务", "分项统计尚未同步。", "重新加载 V2 后刷新统计。"],
    memory: ["外部记忆插件 Token", "该独立插件账本没有接入 V2 的授权统计合同。", "接入后才能显示记忆任务、Provider、最近调用与余额；当前角色数据不会替代它。"],
    together: ["一起房间 Token", "一起房间内部统计暂不可用。", "内置能力提供房间任务、Provider 与实际用量回执；单独统计，不计入角色预算。"],
    reality: ["现实观察 Token", "现实观察内部统计暂不可用。", "摄像头视觉分析与现实提醒文案按模型真实回执记录；没有回执时显示未知，不计入普通核心回复。"],
    live: ["直播与表演 Token", "直播与表演内部统计暂不可用。", "直播模型任务按实际回执单独统计，缺失用量和缓存字段保留未知，不计入普通核心回复。"],
    game: ["共同游玩 Token", "共同游玩内部统计暂不可用。", "游戏回复、海龟汤和画面识别按实际模型回执单独统计，不计入普通核心回复。"],
    main: ["AstrBot 全部会话", "AstrBot 全局用量暂不可用。", "重新读取宿主用量记录。"],
  };
  const routeError = code => ({
    dashboard_identity_required: "请从已登录的 AstrBot 管理页面打开。",
    staging_not_initialized: "先在账号与会话中初始化一个运行实例。",
    model_provider_unavailable: "这个 Provider 当前没有可用的 V2 适配器。",
    model_probe_timeout: "探测超过 30 秒，结果未知。",
    model_probe_failed: "Provider 没有通过连接探测。",
    model_voice_provider_unavailable: "这个 TTS Provider 当前没有加载。",
    model_voice_audio_invalid: "TTS Provider 没有返回可试听的音频。",
    model_voice_audio_read_failed: "语音文件已生成，但读取试听音频失败。",
    model_voice_probe_timeout: "合成测试超时，结果未知。",
    model_voice_probe_failed: "TTS Provider 没有完成合成测试。",
    model_storage_check_failed: "SQLite 完整性检查没有通过。",
    model_storage_check_unavailable: "SQLite 检查未完成，请稍后重试。",
    configuration_revision_conflict: "配置在其他窗口发生了变化，请先重新读取最新任务状态。",
    model_configuration_invalid: "提示词或模型配置没有通过校验，请检查长度和内容。",
    model_task_unavailable: "这个模型任务当前没有注册，无法保存提示词。",
    model_configuration_busy: "配置正在处理，请稍后重试。",
  })[code] || "检查失败，请重新读取状态后再试。";

  let root = null;
  let state = null;
  let generation = 0;
  const DIAGNOSTIC_RESULT_TTL_MS = 15 * 60 * 1000;
  /* 排障页同样会被路由卸载。缓存只存在当前页面生命周期，按安装/对象/角色
     隔离；它保存最近成功读取和未过期的诊断结果，不保存宿主令牌或音频原文。 */
  const maintenanceCache = new Map();
  let maintenanceScope = "";
  function currentMaintenanceScope() {
    try {
      if (typeof window.CompanionProduct?.scopeKey === "function") {
        const scoped = window.CompanionProduct.scopeKey();
        if (scoped != null && String(scoped).trim()) return String(scoped);
      }
      const role = window.CompanionProduct?.role?.() || {};
      const selection = window.CompanionProduct?.selection?.() || {};
      const fallback = [selection.installation_id, selection.target_id, role.installation_id,
        role.target_id, role.role_id, role.model_api].filter(value => value != null && String(value).trim());
      return fallback.length ? fallback.join(":") : "default";
    } catch (_) { return "default"; }
  }
  function cacheMaintenanceState() {
    if (!state || !maintenanceScope) return;
    maintenanceCache.set(maintenanceScope, {
      data: state.data, integrations: state.integrations, modelStatus: state.modelStatus,
      balance: state.balance, balanceLoaded: state.balanceLoaded,
      test: state.test, voiceTest: state.voiceTest ? {...state.voiceTest, audioUrl: ""} : null,
      imageTest: state.imageTest, storageTest: state.storageTest, proactiveTest: state.proactiveTest,
      proactiveNotice: state.proactiveNotice, memorySamples: Array.isArray(state.memorySamples) ? [...state.memorySamples] : [],
      memoryComplete: state.memoryComplete, memoryError: state.memoryError, memoryStopReason: state.memoryStopReason,
      diagnosticNotice: state.diagnosticNotice, diagnosticResultExpiresAt: state.diagnosticResultExpiresAt,
      provider: state.provider, voiceProvider: state.voiceProvider, imageAction: state.imageAction,
    });
  }

  function localUsage(data, key) {
    if (key === "companion") return data;
    return localSourceNames[key] && data.usage_sources?.[key] ? {...data, ...data.usage_sources[key]} : null;
  }

  function selectedUsage(data = state.data || {}) {
    if (!localSourceNames[state.tokenSource]) return data;
    return localUsage(data, state.tokenSource) || {...data, summary: {}, totals: {}, daily: [], history: [],
      windows: {}, by_provider: [], by_task: [], hourly: [], recent: [], cache_diagnostics: {}};
  }

  function sourceTabsMarkup() {
    return tokenSources.map(([key, label, baseStatus]) => {
      const source = state.data?.external_token_sources?.[key];
      const status = localSourceNames[key] ? (localUsage(state.data || {}, key) ? baseStatus : "待同步")
        : source?.available ? "已连接" : source?.installed ? "暂不可用" : key === "main" ? "待同步"
        : nativeTokenSources.has(key) ? "未就绪" : "未安装";
      return fmt(
      /* 统计来源是 tablist，受控面板是下面整块统计区（id 见 tokenDashboardControlsMarkup）。 */
      '<button type="button" data-token-source="{{0}}" role="tab" aria-controls="maint-token-source-panel" aria-selected="{{1}}" class="{{2}}"><i data-lucide="{{3}}"></i><span>{{4}}</span><small data-state="{{5}}">{{5}}</small></button>',
      key, state.tokenSource === key, state.tokenSource === key ? "is-active" : "", sourceIcons[key], label, status);
    }).join("");
  }

  function unavailableSourceMarkup(source, summary) {
    const local = Boolean(localSourceNames[source]);
    const [title, reason, detail] = sourceDescriptions[source] || sourceDescriptions.memory;
    const pluginTitle = summary?.display_name ? summary.display_name + " Token" : title;
    const status = summary?.reason || (summary?.installed
      ? nativeTokenSources.has(source) ? "内部能力已装配，统计暂不可用" : "插件已加载，统计桥暂不可用"
      : reason);
    const explanation = summary?.reason || detail;
    return '<div class="maint-source-unavailable" role="status"><div class="maint-source-glyph"><i data-lucide="plug-zap"></i></div><div class="maint-source-copy">' +
      '<span class="maint-eyebrow">来源 / ' + (summary?.installed ? "桥不可用" : "未连接") + '</span><strong>' + escapeHtml(pluginTitle) + '</strong><p>' + escapeHtml(status) + '</p><small>' + escapeHtml(explanation) +
      '</small><div class="maint-source-boundary">' + (local
        ? '<span><i data-lucide="layers-3"></i>当前角色分项</span><span><i data-lucide="receipt"></i>原始回执已保留</span>'
        : '<span><i data-lucide="circle-off"></i>独立账本未混算</span><span><i data-lucide="wallet-cards"></i>服务端余额未接入</span><span><i data-lucide="shield-check"></i>V2 角色预算保持独立</span>') + '</div>' +
      '<button type="button" class="maint-source-return" data-token-source="companion"><i data-lucide="arrow-left"></i><span>返回 V2 角色账本</span></button></div></div>';
  }

  function externalBucketForPeriod(source, data) {
    const rows = Array.isArray(source.history) ? source.history : Array.isArray(source.daily) ? source.daily : [];
    const today = localDay(data.generated_at);
    if (state.period === "total") return source.totals || {};
    if (state.period === "today" || state.period === "date") {
      const day = state.period === "date" ? state.selectedDate : today;
      return rows.find(row => (row.day || row.key) === day) || {day, calls: 0, tokens: 0};
    }
    const days = state.period === "7d" ? 7 : 30;
    const start = new Date(today + "T00:00:00");
    start.setDate(start.getDate() - days + 1);
    const startDay = localDay(start);
    return aggregateUsage(rows.filter(row => {
      const day = row.day || row.key || "";
      return day >= startDay && day <= today;
    }));
  }

  function externalDimensions(source, bucket, property) {
    const rows = Array.isArray(source.history) ? source.history : [];
    if (state.period === "total") return Array.isArray(source[property]) ? source[property] : [];
    if (state.period === "today" || state.period === "date") return Array.isArray(bucket[property]) ? bucket[property] : [];
    const days = state.period === "7d" ? 7 : 30;
    const end = localDay(state.data?.generated_at);
    const start = new Date(end + "T00:00:00");
    start.setDate(start.getDate() - days + 1);
    const startDay = localDay(start);
    const totals = new Map();
    for (const day of rows.filter(row => (row.day || row.key || "") >= startDay && (row.day || row.key || "") <= end)) {
      for (const item of Array.isArray(day[property]) ? day[property] : []) {
        const key = String(item.key || "未知");
        const value = totals.get(key) || {key, samples: []};
        value.samples.push(item);
        totals.set(key, value);
      }
    }
    return [...totals.values()].map(item => ({key: item.key, ...aggregateUsage(item.samples)}))
      .sort((a, b) => (b.tokens || 0) - (a.tokens || 0)).slice(0, 32);
  }

  function externalRankingMarkup(items) {
    const rows = Array.isArray(items) ? items.slice(0, 8) : [];
    if (!rows.length) return '<div class="maint-empty"><span class="maint-empty-mark">—</span><p>这个时间范围没有分项记录。</p></div>';
    const total = Math.max(1, rows.reduce((sum, row) => sum + (Number(row.tokens) || 0), 0));
    return '<div class="maint-ranking">' + rows.map((row, index) => {
      const share = Math.max(0, Math.min(100, Math.round((Number(row.tokens) || 0) * 1000 / total) / 10));
      return fmt('<div class="maint-ranking-row" data-series="{{4}}"><span class="maint-ranking-label">{{0}}</span><span class="maint-ranking-track"><i style="--rank-width:{{1}}%"></i></span><strong>{{2}}</strong><small>{{3}}%</small></div>',
        escapeHtml(row.key || "未知"), share, row.tokens == null ? "暂无回执" : number(row.tokens), share, index % 7 + 1);
    }).join("") + '</div>';
  }

  function cacheRateFor(row) {
    const denominator = Number(row?.cache_input_tokens) || 0;
    const reportedCalls = Number(row?.cache_reported_calls) || 0;
    return reportedCalls > 0 && denominator > 0
      ? Math.round((Number(row?.cached_input_tokens) || 0) * 1000 / denominator) / 10
      : null;
  }

  function externalDimensionTable(items, label) {
    const rows = Array.isArray(items) ? items : [];
    if (!rows.length) return '<div class="maint-empty"><span class="maint-empty-mark">—</span><p>暂无 ' + escapeHtml(label) + ' 明细。</p></div>';
    return '<div class="maint-table-wrap"><table class="maint-table maint-table--pin"><thead><tr><th>' + escapeHtml(label) + '</th><th>Token</th>' + pairHead("输入", "输出") +
      '<th>调用</th><th>失败</th><th>缓存命中</th><th>缓存写入</th>' + pairHead("已确认", "估算") + '<th>平均延迟</th></tr></thead><tbody>' +
      rows.map(row => '<tr>' + textCell(escapeHtml(row.key || "未知")) + numCell(row.tokens, "col-primary") +
        pairCell(row.input_tokens, row.output_tokens) + numCell(row.calls) + numCell(row.failures) +
        '<td>' + (cacheRateFor(row) == null ? "暂无" : percent(cacheRateFor(row))) + '</td>' +
        numCell(row.cache_creation_tokens) + pairCell(row.reported_tokens, row.estimated_tokens) +
        '<td>' + (row.avg_latency_ms == null ? "—" : number(row.avg_latency_ms) + " ms") + '</td></tr>').join("") +
      '</tbody></table></div>';
  }

  function externalHistoryMarkup(rows) {
    const visible = (Array.isArray(rows) ? rows : []).slice(-30).reverse();
    if (!visible.length) return '<div class="maint-empty"><span class="maint-empty-mark">—</span><p>该来源还没有逐日记录。</p></div>';
    return '<div class="maint-table-wrap"><table class="maint-table maint-table--pin"><thead><tr><th>日期</th><th>Token</th>' + pairHead("已确认", "估算") +
      pairHead("输入", "输出") + pairHead("缓存命中", "缓存写入") + '<th>调用</th><th>失败</th><th>平均延迟</th></tr></thead><tbody>' +
      visible.map(row => '<tr><td><button type="button" class="maint-text-action" data-select-day="' + escapeHtml(row.day || row.key) + '">' + escapeHtml(row.day || row.key) + '</button></td>' +
        numCell(row.tokens, "col-primary") + pairCell(row.reported_tokens, row.estimated_tokens) +
        pairCell(row.input_tokens, row.output_tokens) + pairCell(row.cached_input_tokens, row.cache_creation_tokens) +
        numCell(row.calls) + numCell(row.failures) +
        '<td>' + (row.avg_latency_ms == null ? "—" : number(row.avg_latency_ms) + " ms") + '</td></tr>').join("") +
      '</tbody></table></div>';
  }

  function externalRecentMarkup(rows, limit = 40) {
    const visible = Array.isArray(rows) ? rows.slice(0, limit) : [];
    if (!visible.length) return '<div class="maint-empty"><span class="maint-empty-mark">—</span><p>这个范围没有最近调用回执。</p></div>';
    return '<div class="maint-table-wrap"><table class="maint-table maint-table--pin"><thead><tr><th>时间</th><th class="col-text">任务</th><th class="col-text">Provider</th><th>Token</th>' +
      pairHead("输入", "输出") + '<th class="col-text">缓存状态</th>' + pairHead("已确认", "估算") + '<th class="col-text">状态</th><th>耗时</th></tr></thead><tbody>' +
      visible.map(row => '<tr>' + textCell(escapeHtml(time(row.at))) +
        textCell(escapeHtml(taskName(row.kind)), "col-text") + textCell(escapeHtml(row.provider), "col-text") +
        numCell(row.tokens, "col-primary") + pairCell(row.input_tokens, row.output_tokens) +
        textCell(row.cache_hit_rate == null ? (row.cache_hit_reported ? "0%" : "暂无回执") : percent(row.cache_hit_rate), "col-text") +
        pairCell(row.reported_tokens, row.estimated_tokens) + textCell(escapeHtml(stateName(row.state)) +
          (row.tokens == null ? '<small>用量回执未知</small>' : ""), "col-text") +
        '<td>' + (row.latency_ms == null ? "—" : number(row.latency_ms) + " ms") + '</td></tr>').join("") +
      '</tbody></table></div>';
  }

  function externalCacheUnavailableMarkup(source) {
    const name = escapeHtml(source?.display_name || "外部来源");
    return '<div class="maint-source-unavailable" role="status"><div class="maint-source-glyph"><i data-lucide="receipt-text"></i></div><div class="maint-source-copy">' +
      '<span class="maint-eyebrow">缓存 / 外部来源</span><strong>' + name + '缓存诊断</strong><p>该来源没有提供请求前缀签名和完整缓存诊断数据。</p>' +
      '<small>Token 统计页仅展示该来源实际回传的缓存回执；V2 缓存诊断保持独立。</small><div class="maint-source-boundary"><span><i data-lucide="receipt"></i>来源回执独立展示</span><span><i data-lucide="circle-off"></i>不套用 V2 缓存数据</span></div>' +
      '<button type="button" class="maint-source-return" data-maint-nav="overview"><i data-lucide="arrow-left"></i><span>返回 Token 统计</span></button></div></div>';
  }

  function periodControlsMarkup(today, label) {
    const buttons = [["today", "今日"], ["7d", "7 日"], ["30d", "30 日"], ["total", "总计"], ["date", "按日期"]].map(item =>
      fmt('<button type="button" data-period="{{0}}" aria-pressed="{{1}}" class="{{2}}">{{3}}</button>',
        item[0], state.period === item[0], state.period === item[0] ? "is-active" : "", item[1])).join("");
    return '<div class="maint-token-range"><span>时间范围</span><div class="maint-token-controls"><div class="maint-periods" role="group" aria-label="' + escapeHtml(label || "Token 统计视图") + '">' + buttons +
      '</div><label class="maint-date-control"><span>查看日期</span><input type="date" data-selected-date value="' + escapeHtml(state.selectedDate || today) + '" max="' + escapeHtml(today) + '" ' +
      (state.period === "date" ? "" : "disabled") + '></label></div></div>';
  }

  function tokenDetailTabsMarkup(active, entries) {
    const tabs = entries.map(([key, label]) => '<button type="button" role="tab" data-token-detail="' + key + '" aria-selected="' + (active === key) + '" aria-controls="token-detail-panel-' + key + '">' + label + '</button>').join("");
    return '<div class="maint-token-detail-tabs" role="tablist" aria-label="Token 明细类型">' + tabs + '</div>';
  }

  function tokenDetailPanel(key, active, content) {
    return '<div id="token-detail-panel-' + key + '" class="maint-token-detail-panel" role="tabpanel" data-token-detail-panel="' + key + '"' + (active === key ? "" : " hidden") + '>' + content + '</div>';
  }

  function numCell(value, cls) {
    if (value == null) return '<td class="' + (cls || "") + '">—</td>';
    const zero = Number(value) === 0 ? " is-zero" : "";
    return '<td class="' + (cls ? cls + zero : zero.trim()) + '">' + number(value) + '</td>';
  }

  function pairHead(labelA, labelB) {
    return '<th class="maint-th-pair"><span>' + labelA + '</span><span>' + labelB + '</span></th>';
  }

  function pairCell(a, b) {
    const span = value => value == null
      ? '<span class="is-zero">—</span>'
      : '<span' + (Number(value) === 0 ? ' class="is-zero"' : '') + '>' + number(value) + '</span>';
    return '<td class="maint-cell-pair">' + span(a) + span(b) + '</td>';
  }

  function textCell(content, cls) {
    return '<td' + (cls ? ' class="' + cls + '"' : '') + '>' + content + '</td>';
  }

  function budgetMarkup(budget, spent, held, limit, budgetPct, softEnabled, softPct, softLimit, softState) {
    const used = spent + held;
    const budgetState = limit && used / limit >= 0.9 ? "warn" : "ok";
    const softPos = limit && softEnabled ? Math.max(0, Math.min(100, Math.round(softLimit * 100 / limit))) : null;
    let meterline;
    if (limit) {
      meterline = '<div class="maint-budget-meterline">' +
        '<div class="maint-budget-limits" aria-hidden="true">' +
        (softEnabled ? '<span class="maint-budget-limit--soft">软限额 ' + number(softLimit) + '</span>' : '') +
        '<span class="maint-budget-limit--hard">硬限额 ' + number(limit) + '</span></div>' +
        '<div class="maint-budget-track" role="progressbar" aria-label="今日后台插件任务 Token 预算，横条以硬限额为满刻度' +
        (softEnabled ? '，橙色刻度为软限额' : '') + '" aria-valuemin="0" aria-valuemax="' + limit + '" aria-valuenow="' + Math.min(limit, used) + '"><i style="--budget-progress:' + budgetPct + '%"></i></div>' +
        (softEnabled ? '<b class="maint-budget-tick" style="--limit-pos:' + softPos + '%"></b>' : '') +
        '</div>';
    } else {
      meterline = '<div class="maint-budget-track" aria-hidden="true"><i style="--budget-progress:0%"></i></div>';
    }
    const hardLimit = limit ? number(limit) + ' Token' : '硬预算关闭';
    const pending = Number(budget.pending_usage_calls) || 0;
    const sources = Array.isArray(budget.reservation_sources) ? budget.reservation_sources : [];
    const pendingDetail = pending ? '<details><summary>等待用量回执 ' + number(pending) + ' 次 · 占用预留 ' + number(budget.pending_usage_tokens) + ' Token</summary>' +
      sources.map(item => '<div class="maint-budget-values"><span>' + escapeHtml(taskName(item.kind)) + ' · ' + escapeHtml(stateName(item.state)) + '</span><span>' + number(item.calls) + ' 次 · <b>' + number(item.held_tokens) + ' Token</b></span></div>').join('') + '</details>' : '';
    return '<div class="maint-budget-block maint-token-summary-card" data-state="' + budgetState + '" data-focus-target="budget-block"><div class="maint-token-card-heading"><span>今日插件任务预算</span><small>仅插件任务 · V2 后台任务</small></div>' +
      '<div class="maint-budget-reading"><strong>' + (limit ? budgetPct + '%' : '关闭') + '</strong><span>' + (limit ? number(used) + ' / ' + hardLimit : '仅统计用量 · 硬预算关闭') + '</span></div>' + meterline +
      '<div class="maint-budget-values"><span>已入账 <b>' + number(spent) + '</b></span><span>预留中 <b>' + number(held) + '</b></span><span>硬上限 <b>' + (limit ? number(limit) : '关闭') + '</b></span></div>' +
      pendingDetail +
      '<div class="maint-soft-limit" data-state="' + softState + '"><div><span>后台软限额</span><strong>' + (softEnabled ? softPct + '%' : '关闭') + '</strong></div>' +
      (limit && softEnabled ? '' : '<div class="maint-soft-meter"><i style="--soft-progress:' + softPct + '%"></i></div>') +
      '<small>' +
      (softEnabled ? softPct >= 100 ? '已触发 · 暂缓新建可选后台任务' : '阈值 ' + number(softLimit) + ' Token · 不影响正在运行的任务' : limit ? '软限额关闭 · 本地硬预算已启用' : '本地限额已关闭 · 保留用量统计') + '</small></div></div>';
  }

  function memoryLinkageMarkup(linkage) {
    if (!linkage) return "";
    if (linkage.state === "ready") {
      const categories = Array.isArray(linkage.categories) ? linkage.categories : [];
      const labels = {profile: "个人资料", preference: "偏好", relationship: "关系", private_conversation: "私聊内容", other: "其他"};
      return '<section class="maint-memory-linkage" aria-label="V2 记忆联动计数"><div><strong>V2 联动摘要</strong><span>' +
        escapeHtml(linkage.display_name || "记忆插件") + ' · 只读分类计数，不含记忆正文</span></div><dl>' +
        categories.map(item => '<div><dt>' + escapeHtml(labels[item.key] || "其他") + '</dt><dd>' + number(item.count) + '</dd></div>').join("") +
        '<div><dt>合计</dt><dd>' + number(linkage.counts?.total) + '</dd></div></dl></section>';
    }
    if (linkage.state === "not_owner") {
      return '<section class="maint-memory-linkage is-muted"><div><strong>V2 联动摘要</strong><span>当前长期记忆由 V2 提供，外部摘要未读取。</span></div></section>';
    }
    return '<section class="maint-memory-linkage is-muted"><div><strong>V2 联动摘要暂不可用</strong><span>外部 Token 账本与 V2 角色预算仍分别统计。</span></div></section>';
  }

  function externalTokenMarkup(sourceKey) {
    const source = state.data?.external_token_sources?.[sourceKey];
    const linkageMarkup = sourceKey === "memory" ? memoryLinkageMarkup(source?.linkage) : "";
    if (!source?.available) return linkageMarkup + unavailableSourceMarkup(sourceKey, source);
    const data = {generated_at: state.data.generated_at, history: source.history || source.daily || [], daily: source.daily || [],
      hourly: source.hourly || [], recent: source.recent || [], windows: {}, totals: source.totals || {}};
    const row = externalBucketForPeriod(source, data);
    const rows = Array.isArray(data.history) ? data.history : [];
    const providerRows = externalDimensions(source, row, "providers");
    const taskRows = externalDimensions(source, row, "tasks");
    const day = state.period === "date" ? state.selectedDate : localDay(data.generated_at);
    const label = ({today: "今日", "7d": "近 7 日", "30d": "近 30 日", total: "累计", date: day})[state.period] || "今日";
    const stats = {tokens: row.tokens == null ? null : Number(row.tokens) || 0,
      day: state.period === "date" ? state.selectedDate : localDay(data.generated_at)};
    const compare = comparisonMarkup(usageComparison(data, stats));
    const selectedRecent = data.recent.filter(item => {
      const at = String(item.at || "").slice(0, 10);
      if (state.period === "total") return true;
      if (state.period === "date") return at === state.selectedDate;
      const days = state.period === "7d" ? 7 : state.period === "30d" ? 30 : 1;
      const start = new Date(localDay(data.generated_at) + "T00:00:00");
      start.setDate(start.getDate() - days + 1);
      return at >= localDay(start) && at <= localDay(data.generated_at);
    });
    const cacheRate = cacheRateFor(row);
    const cacheCoverage = Number(row.calls) ? Math.round((Number(row.cache_telemetry_calls) || 0) * 1000 / Number(row.calls)) / 10 : null;
    const trendEndDay = state.period === "date" ? state.selectedDate : localDay(data.generated_at);
    const externalMetric = (key, suffix = "") => row[key] == null ? "无回执" : number(row[key]) + suffix;
    const activeDetail = ["daily", "providers", "tasks", "recent"].includes(state.tokenDetailView) ? state.tokenDetailView : "daily";
    const externalMetrics = '<div class="maint-external-kpis"><div class="maint-token-card-heading"><span>来源计量</span><small>以模型回执为准</small></div><div><span>已确认 Token</span><strong>' + externalMetric("reported_tokens") + '</strong></div><div><span>估算 Token</span><strong>' + externalMetric("estimated_tokens") + '</strong></div><div><span>输入 / 输出</span><strong>' + (row.input_tokens == null && row.output_tokens == null ? "无回执" : externalMetric("input_tokens") + ' / ' + externalMetric("output_tokens")) + '</strong></div><div><span>失败 / 平均延迟</span><strong>' + (row.failures == null ? "无回执" : number(row.failures)) + ' / ' + (row.avg_latency_ms == null ? "无回执" : number(row.avg_latency_ms) + ' ms') + '</strong></div></div>';
    return '<div class="maint-external-source"><div class="maint-token-source-note"><i data-lucide="layers-3"></i><span>' +
      escapeHtml(source.display_name || tokenSources.find(([key]) => key === sourceKey)?.[1] || "模型来源") + ' 独立账本 · 不计入 V2 角色预算 · Provider 本地价格未提供</span></div>' +
      linkageMarkup +
      (source.note ? '<p class="maint-token-source-note">' + escapeHtml(source.note) + '</p>' : '') +
      '<div class="maint-token-hero maint-token-summary maint-external-hero"><div class="maint-usage-total maint-token-summary-card"><span>' + escapeHtml(label) + ' · 来源 Token</span><strong>' + (row.tokens == null ? "未知" : number(row.tokens)) + '</strong><small>Token</small><div class="maint-usage-subline"><span>' + number(row.calls) + (sourceKey === "main" ? ' 次回复' : ' 次调用') + '</span><i></i><span>费用未提供</span>' +
      (Number(row.unconfirmed_calls) ? '<i></i><span>用量回执未知 ' + number(row.unconfirmed_calls) + ' 次</span>' : "") + '</div></div>' +
      externalMetrics + '</div>' +
      '<details class="maint-token-drill"><summary>周期对比与缓存回执</summary><div class="maint-token-drill-content">' + compare + '<div class="maint-token-kpis maint-external-cache-kpis"><div><span>缓存命中</span><strong>' + (row.cached_input_tokens == null ? "暂无回执" : number(row.cached_input_tokens)) + '</strong></div><div><span>命中率</span><strong>' + (cacheRate == null ? "暂无命中率" : percent(cacheRate)) + '</strong></div><div><span>缓存写入</span><strong>' + (row.cache_creation_tokens == null ? "暂无回执" : number(row.cache_creation_tokens)) + '</strong></div><div><span>回执覆盖</span><strong>' + (cacheCoverage == null ? "暂无" : percent(cacheCoverage)) + '</strong></div></div></div></details>' +
      '<div class="maint-token-analytics" id="maint-trends">' + trendPanelMarkup(data, stats, rows, trendEndDay, "用量趋势", "选择日期查看该日统计") +
      breakdownPanelMarkup(providerRows, taskRows, label, [], [], true) + '</div>' +
      '<section class="maint-history-panel maint-token-details" id="maint-history"><div class="maint-subhead"><div><strong>调用与用量明细</strong><span>最多保留一年逐日统计；外部状态与计量未作推断</span></div></div>' +
      tokenDetailTabsMarkup(activeDetail, [["daily", "每日明细"], ["providers", "Provider 明细"], ["tasks", "任务明细"], ["recent", "最近调用"]]) +
      tokenDetailPanel("daily", activeDetail, externalHistoryMarkup(rows)) + tokenDetailPanel("providers", activeDetail, externalDimensionTable(providerRows, "Provider")) +
      tokenDetailPanel("tasks", activeDetail, externalDimensionTable(taskRows, "任务")) + tokenDetailPanel("recent", activeDetail, externalRecentMarkup(selectedRecent)) + '</section></div>';
  }

  function sourceComparisonMarkup(data) {
    const sourceRows = tokenSources.map(([key, label, baseStatus]) => {
      if (localSourceNames[key]) {
        const report = localUsage(data, key);
        const usage = report ? statsFor(report) : null;
        return {key, label, status: report ? "当前角色" : "待同步", available: Boolean(report),
          tokens: usage?.tokens, calls: usage?.calls, cache: usage?.cacheRate, coverage: usage?.cacheCoverage,
          scope: key === "companion" ? "对话合计 + 插件附加任务" : key === "conversation" ? "核心回复 + 历史 V2 回复" :
            key === "astrbot_core" ? "真实核心请求与工具续轮" : key === "legacy_conversation" ? "旧 V2 模型账本" : "主动、记忆、日程等附加调用"};
      }
      const source = data.external_token_sources?.[key];
      if (!source?.available) return {key, label, status: source?.installed ? "桥不可用" : baseStatus,
        available: false, tokens: null, calls: null, cache: null, coverage: null,
        scope: "独立账本未混算"};
      const bucket = externalBucketForPeriod(source, data) || {};
      const denominator = Number(bucket.cache_input_tokens) || 0;
      const receiptCalls = Number(bucket.cache_reported_calls) || 0;
      const telemetryCalls = Number(bucket.cache_telemetry_calls) || 0;
      return {key, label: source.display_name || label, status: "已连接", available: true,
        tokens: bucket.tokens == null ? null : Number(bucket.tokens) || 0, calls: Number(bucket.calls) || 0,
        cache: receiptCalls > 0 && denominator > 0 ? Math.round((Number(bucket.cached_input_tokens) || 0) * 1000 / denominator) / 10 : null,
        coverage: Number(bucket.calls) > 0 ? Math.round(telemetryCalls * 1000 / Number(bucket.calls)) / 10 : null,
        scope: "独立账本 · 不计入 V2 预算"};
    });
    const periodLabel = ({today: "今日", "7d": "近 7 日", "30d": "近 30 日", total: "累计", date: state.selectedDate})[state.period] || "当前范围";
    const rows = sourceRows.map(row => {
      const value = row.available ? row.tokens == null ? "未知" : number(row.tokens) : "—";
      const calls = row.available ? number(row.calls) : "—";
      const cache = row.cache == null ? "暂无" : percent(row.cache);
      const coverage = row.coverage == null ? "暂无" : percent(row.coverage);
      return '<button type="button" class="maint-source-compare-row ' + (state.tokenSource === row.key ? "is-active" : "") + '" data-token-source="' + escapeHtml(row.key) + '" aria-pressed="' + (state.tokenSource === row.key) + '">' +
        '<span class="maint-source-compare-name"><i data-lucide="' + (sourceIcons[row.key] || "layers-3") + '"></i><strong>' + escapeHtml(row.label) + '</strong><small>' + escapeHtml(row.scope) + '</small></span>' +
        '<span><b>' + value + '</b><small>Token</small></span><span><b>' + calls + '</b><small>调用</small></span><span><b>' + cache + '</b><small>命中率</small></span><span><b>' + coverage + '</b><small>回执覆盖</small></span><em data-state="' + (row.available ? "ok" : "unknown") + '">' + escapeHtml(row.status) + '</em></button>';
    }).join("");
    return '<section class="maint-source-comparison" aria-label="Token 来源同周期对照"><div class="maint-subhead"><strong>来源同周期对照</strong><span>' + escapeHtml(periodLabel) + ' · 各账本独立统计</span></div><div class="maint-source-compare-head"><span>来源</span><span>Token</span><span>调用</span><span>命中率</span><span>回执覆盖</span><span>状态</span></div><div class="maint-source-compare-list">' + rows + '</div><p class="maint-source-compare-note"><i data-lucide="info"></i>角色总用量 = 对话主链 + 插件附加任务。外部插件账本分别列出；未接入来源不使用角色数据填充。</p></section>';
  }

  function modelPath() {
    const endpoint = window.CompanionProduct?.role()?.model_api;
    if (typeof endpoint !== "string" || !endpoint.startsWith(PREFIX + "/")
        || /[?#%\\:]/.test(endpoint) || endpoint.split("/").slice(1).some(part => !part || part === "." || part === "..")) {
      throw new Error("当前对象没有模型管理接口。请先选择已初始化的会话角色。");
    }
    return endpoint.slice(PREFIX.length);
  }

  async function api(method, path, body) {
    const product = window.CompanionProduct;
    const bridge = product?.bridgeNow?.() || await product?.bridgeFor?.();
    if (!bridge) throw new Error("页面桥尚未就绪，请重新读取。");
    const response = method === "GET" ? await bridge.apiGet(path) : await bridge.apiPost(path, body || {});
    let value = response;
    for (let depth = 0; depth < 3; depth += 1) {
      if (value?.status === "error") {
        const code = value?.data?.code || value?.code || "";
        const error = new Error(routeError(code));
        error.code = code;
        throw error;
      }
      if (value?.status === "ok" && Object.hasOwn(value, "data")) {
        value = value.data;
        continue;
      }
      return value;
    }
    return value;
  }

  const PROMPT_LIMIT = 8000;
  const PROMPT_SOURCE_NAMES = {default: "内置默认", installation: "安装设置", role: "角色设置",
    relationship: "关系偏好", window: "窗口覆盖", host_conversation: "宿主会话"};
  const DEFAULT_PROMPT_LAYERS = [
    {key: "role_instruction", label: "角色指令", editable: false,
      description: "当前角色的人格与身份设定，作为独立系统消息发送。"},
    {key: "request_instruction", label: "业务任务指令", editable: false,
      description: "由业务流程按任务和本轮场景生成；此处不读取或展示单次请求正文。"},
    {key: "task_prompt", label: "模型任务提示", editable: true,
      description: "本页编辑的提示字段，运行时接在业务任务指令之后；自定义值替换该字段的内置默认值。"},
    {key: "output_contract", label: "输出合同", editable: false,
      description: "按任务类型加入自然语言、JSON 字段及证据等格式约束。"},
    {key: "capability_instruction", label: "能力指令", editable: false,
      description: "需要工具、图片等模型能力时，由运行端追加对应协议。"},
  ];
  const PROMPT_DYNAMIC_FIELDS = {
    task_input: "本次任务的结构化输入，由调用方提供。",
    role_instruction: "当前角色的角色指令，由 V2 运行端按权限提供。",
    conversation_context: "本轮会话上下文，只包含当前请求允许携带的内容。",
    recent_history: "当前任务允许使用的近期历史摘要。",
    persona_context: "当前角色的已确认人格资料。",
    image_observations: "本次请求中已授权、实际提供的图像观察结果。",
    creative_project: "当前内容创作项目及其已确认约束。",
  };

  function normalizePrompt(value) {
    return String(value ?? "").replace(/\r\n?/g, "\n");
  }

  function promptDynamicFields(prompt) {
    const fields = [];
    const seen = new Set();
    const pattern = /\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}/g;
    let match;
    while ((match = pattern.exec(prompt))) {
      const name = match[1];
      if (seen.has(name)) continue;
      seen.add(name);
      fields.push({name, description: PROMPT_DYNAMIC_FIELDS[name] || "由 V2 调用方按当前任务合同提供的动态字段。"});
    }
    return fields;
  }

  function promptTasks() {
    const tasks = Array.isArray(state?.modelStatus?.tasks) ? state.modelStatus.tasks : [];
    return tasks.map(task => {
      const info = promptTaskInfo(task?.kind);
      const field = task?.fields?.prompt || {};
      const current = normalizePrompt(field.value);
      const inherited = normalizePrompt(field.inherited_value);
      const builtinPromptAvailable = typeof task?.default_prompt === "string";
      const builtin = normalizePrompt(builtinPromptAvailable ? task.default_prompt : "");
      const promptLayers = Array.isArray(task?.prompt_layers) && task.prompt_layers.length
        ? task.prompt_layers : DEFAULT_PROMPT_LAYERS;
      const promptSections = Array.isArray(task?.prompt_sections) ? task.prompt_sections.map(section => ({
        ...section, key: String(section.key || ""), prompt: normalizePrompt(section.prompt),
        default_prompt: normalizePrompt(section.default_prompt), editable: section.editable === true,
      })).filter(section => section.key) : [];
      return {...task, kind: String(task?.kind || ""), group: info.group, label: info.label,
        description: info.description, promptField: field, prompt: current, inherited,
        builtinPrompt: builtin, builtinPromptAvailable, promptVersion: String(task?.prompt_version || "1"),
        promptLayers, promptSections,
        dynamicFields: promptDynamicFields(builtin),
        customized: field.explicit === true || String(field.source || "default") !== "default" ||
          String(field.inherited_source || "default") !== "default" || promptSections.some(section =>
            section.editable && (section.field?.explicit || section.field?.source && section.field.source !== "default"))};
    }).filter(task => task.kind);
  }

  function promptSelectedTask() {
    const tasks = promptTasks();
    return tasks.find(task => task.kind === state?.promptSelectedKind) || tasks[0] || null;
  }

  function promptEditTarget(task = promptSelectedTask()) {
    if (!task) return null;
    const sections = task.promptSections.filter(section => section.editable);
    const section = sections.find(item => item.key === state.promptSelectedSection);
    if (!section) return {...task, sectionKey: "task_prompt", sectionLabel: "模型任务提示", sectionVariables: []};
    const field = section.field || {};
    return {...task, sectionKey: section.key, sectionLabel: section.label, sectionCondition: section.condition,
      sectionVariables: section.variables || [], prompt: section.prompt, inherited: normalizePrompt(field.inherited_value),
      builtinPrompt: section.default_prompt, builtinPromptAvailable: true, promptField: field,
      edit_token: section.edit_token, inheritanceLabel: section.inheritance_label || "上级继承值",
      customized: field.explicit === true || field.source && field.source !== "default"};
  }

  function loadPromptDraft(task) {
    state.promptSelectedKind = task.kind;
    state.promptDraft = task.prompt;
    state.promptInitial = task.prompt;
    state.promptEditToken = String(task.edit_token || "");
    state.promptInitialized = true;
    state.promptStale = false;
    state.promptOperationKey = "";
    state.promptOperationKind = "";
  }

  function promptDirty() {
    return Boolean(state?.promptInitialized && state.promptDraft !== state.promptInitial);
  }

  function promptVisibleTasks(tasks) {
    const query = String(state.promptQuery || "").trim().toLocaleLowerCase("zh-CN");
    return tasks.filter(task => {
      if (state.promptMode === "customized" && !task.customized) return false;
      if (state.promptGroup !== "all" && task.group !== state.promptGroup) return false;
      if (!query) return true;
      return [task.label, task.kind, task.group, task.description, task.prompt, task.builtinPrompt,
        task.promptField?.source, task.promptField?.inherited_source,
        ...task.promptSections.flatMap(section => [section.label, section.condition, section.prompt, section.default_prompt])]
        .some(value => String(value || "").toLocaleLowerCase("zh-CN").includes(query));
    });
  }

  function reconcilePromptState() {
    const selected = promptSelectedTask();
    if (selected && (!state.promptInitialized || state.promptSelectedKind !== selected.kind)) {
      state.promptSelectedSection = selected.promptSections.find(section => section.editable)?.key || "task_prompt";
    }
    const task = promptEditTarget(selected);
    if (!task) {
      if (state.promptInitialized && promptDirty()) {
        state.promptStale = true;
        return;
      }
      state.promptSelectedKind = "";
      state.promptDraft = "";
      state.promptInitial = "";
      state.promptEditToken = "";
      state.promptInitialized = false;
      return;
    }
    if (!state.promptInitialized || state.promptSelectedKind !== task.kind) {
      loadPromptDraft(task);
      return;
    }
    const serverChanged = state.promptEditToken && task.edit_token &&
      (state.promptEditToken !== String(task.edit_token) || task.prompt !== state.promptInitial);
    if (promptDirty() && serverChanged) {
      state.promptStale = true;
      state.promptMessage = "服务器上的任务配置已变化，当前草稿仍保留；确认最新值后再决定是否覆盖。";
      state.promptMessageKind = "warn";
      return;
    }
    if (!promptDirty()) {
      state.promptDraft = task.prompt;
      state.promptInitial = task.prompt;
      state.promptEditToken = String(task.edit_token || "");
      state.promptStale = false;
    }
  }

  function promptOperationKey() {
    const task = promptEditTarget();
    if (!task) return "";
    const target = task.kind + ":" + task.sectionKey;
    if (!state.promptOperationKey || state.promptOperationKind !== target) {
      state.promptOperationKind = target;
      state.promptOperationKey = "maintenance-prompt-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 9);
    }
    return state.promptOperationKey;
  }

  function promptGroupMarkup(tasks) {
    const groups = new Map();
    for (const task of tasks) groups.set(task.group, (groups.get(task.group) || 0) + 1);
    const ordered = promptTaskGroups.map(([group]) => group).filter(group => groups.has(group));
    for (const group of groups.keys()) if (!ordered.includes(group)) ordered.push(group);
    const allCount = state.promptMode === "customized" ? tasks.filter(task => task.customized).length : tasks.length;
    return [['all', "全部", allCount], ...ordered.map(group => [group, group,
      tasks.filter(task => task.group === group && (state.promptMode !== "customized" || task.customized)).length])]
      .map(([key, label, count]) => fmt('<button type="button" class="maint-prompt-group{{0}}" data-prompt-group="{{1}}" aria-pressed="{{2}}" {{3}}><span>{{4}}</span><b>{{5}}</b></button>',
        state.promptGroup === key ? " is-active" : "", escapeHtml(key), state.promptGroup === key,
        state.promptSaving ? "disabled" : "", escapeHtml(label), count)).join("");
  }

  function promptCompositionMarkup(task) {
    const layers = Array.isArray(task.promptLayers) ? task.promptLayers : DEFAULT_PROMPT_LAYERS;
    return '<section class="maint-prompt-composition" aria-label="运行时提示组成"><div class="maint-prompt-subhead"><strong>运行时提示组成</strong><span>不读取单次请求正文</span></div><ol class="maint-prompt-layers">' +
      layers.map(layer => '<li><strong>' + escapeHtml(layer.label || layer.key) + '</strong><span data-editable="' + Boolean(layer.editable) + '">' +
        (layer.editable ? "本页可编辑" : "运行端提供") + '</span><p>' + escapeHtml(layer.description || "") + '</p></li>').join("") +
      '</ol><div class="maint-prompt-dynamics"><div class="maint-prompt-subhead"><strong>本轮动态内容</strong></div><p>' +
      (task.execution_path === "astrbot_core" ? "人格、历史、图片与音视频由 AstrBot 构造；V2 按窗口权限附加角色、关系、记忆、生活和主动灵感。本页编辑对话附加提示并查看 V2 背景规则。" :
        "角色设定、已选由头、新闻与历史材料按当前会话注入；此处查看模板，不包含某次调用的完整 messages。") + '</p></div></section>';
  }

  function promptViewMarkup() {
    return '<div class="maint-prompt-tabs" role="group" aria-label="提示词视图">' +
      [["edit", "模板编辑", "file-pen-line"], ["rules", "固定规则", "list-checks"], ["composition", "提示组成", "layers-3"]].map(([key, label, icon]) =>
        '<button type="button" data-prompt-view="' + key + '" aria-pressed="' +
        (state.promptView === key) + '" class="' + (state.promptView === key ? 'is-active' : '') + '"><i data-lucide="' + icon + '"></i><span>' + label + '</span></button>').join("") + '</div>';
  }

  function promptRulesMarkup(task) {
    const sections = task.promptSections.filter(section => !section.editable);
    if (!sections.length) return '<div class="maint-prompt-list-state">当前任务尚未发布业务模板目录。</div>';
    const selected = sections.find(section => section.key === state.promptRuleKey) || sections[0];
    return '<div class="maint-prompt-rules"><label class="maint-prompt-selector"><span>固定规则</span><select data-prompt-rule>' +
      sections.map(section => '<option value="' + escapeHtml(section.key) + '"' + (section.key === selected.key ? ' selected' : '') +
        '>' + escapeHtml(section.label) + '</option>').join("") + '</select></label><p class="maint-prompt-condition">' +
      escapeHtml(selected.condition) + '</p><pre class="maint-prompt-fulltext">' + escapeHtml(selected.prompt) + '</pre></div>';
  }

  function promptEditorMarkup(task) {
    if (!task) return '<div class="maint-prompt-editor-empty"><i data-lucide="list-checks"></i><strong>选择一个任务</strong><span>从左侧目录选择要调整的 V2 模型任务。</span></div>';
    const selectedTask = task;
    task = promptEditTarget(task);
    const dirty = promptDirty();
    const overLimit = state.promptDraft.length > PROMPT_LIMIT;
    const message = state.promptMessage || (state.promptStale ? "配置已过期，请重新读取后再保存。" :
      dirty ? "有未保存更改" : task.customized ? "已自定义提示" : "使用内置默认值");
    const messageKind = state.promptMessageKind || (state.promptStale ? "warn" : dirty ? "dirty" : "ok");
    const canSave = dirty && !state.promptSaving && !state.promptStale && !overLimit && Boolean(state.promptEditToken);
    const canReset = !state.promptSaving && !state.promptReloading && !state.promptStale && (task.customized || dirty);
    const source = task.promptField?.source || "default";
    const inheritedSource = task.promptField?.inherited_source || "default";
    const metadata = '<details class="maint-prompt-default"><summary>配置来源与版本</summary><dl class="maint-prompt-meta"><div><dt>任务键</dt><dd><code>' + escapeHtml(task.kind) + '</code></dd></div><div><dt>当前来源</dt><dd><code>' + escapeHtml(source) + '</code></dd></div><div><dt>' + (task.sectionKey === "task_prompt" ? "角色级继承来源" : "上级继承来源") + '</dt><dd><code>' + escapeHtml(inheritedSource) + '</code></dd></div><div><dt>内置版本</dt><dd><code>v' + escapeHtml(task.promptVersion) + '</code></dd></div><div><dt>版本令牌</dt><dd><code>' + (state.promptEditToken ? escapeHtml(state.promptEditToken.slice(0, 12)) + "…" : "未读取") + '</code></dd></div></dl></details>';
    const dynamicFields = task.dynamicFields.length
      ? '<div class="maint-prompt-dynamics"><div class="maint-prompt-subhead"><strong>动态字段</strong><span>由 V2 运行端注入</span></div><div class="maint-prompt-dynamic-list">' +
        task.dynamicFields.map(field => '<div><code>{{' + escapeHtml(field.name) + '}}</code><span>' + escapeHtml(field.description) + '</span></div>').join("") + '</div></div>'
      : '<div class="maint-prompt-dynamics"><div class="maint-prompt-subhead"><strong>动态字段</strong><span>当前正文未声明模板变量</span></div><p>' +
        (task.execution_path === "astrbot_core" ? "AstrBot 提供当前人格和会话历史，V2 附加获准的陪伴背景；本页不读取单次消息。" :
          "运行时上下文由任务运行端注入，本页不会展示消息正文或动态值。") + '</p></div>';
    const header = '<div class="maint-prompt-editor-head"><div class="maint-prompt-task-identity"><div class="maint-prompt-task-meta"><span>' + escapeHtml(task.group) + '</span><code>' + escapeHtml(task.kind) + '</code></div><h4>' + escapeHtml(task.label) + '</h4><p>' + escapeHtml(task.description) + '</p></div><span class="maint-prompt-badge" data-state="' + (task.customized ? "custom" : "default") + '"><i data-lucide="' + (task.customized ? "circle-check" : "circle-dot") + '"></i><span>' + (task.customized ? "已自定义" : "继承默认") + '</span></span></div>';
    const tabs = promptViewMarkup();
    const panel = content => '<div class="maint-prompt-view">' + content + '</div>';
    if (state.promptView === "rules") return header + tabs + panel(promptRulesMarkup(selectedTask));
    if (state.promptView === "composition") return header + tabs + panel(promptCompositionMarkup(selectedTask));
    const sections = selectedTask.promptSections.filter(section => section.editable);
    const selector = sections.length ? '<label class="maint-prompt-selector"><span>表达模板</span><select data-prompt-section' + (state.promptSaving ? ' disabled' : '') + '>' +
      [...sections.map(section => [section.key, section.label]), ["task_prompt", "模型任务提示"]].map(([key, label]) =>
        '<option value="' + escapeHtml(key) + '"' + (task.sectionKey === key ? ' selected' : '') + '>' + escapeHtml(label) + '</option>').join("") +
      '</select></label><p class="maint-prompt-condition">' + escapeHtml(task.sectionCondition || "接在业务任务指令之后") + '</p>' : "";
    const variables = task.sectionVariables.length ? '<div class="maint-prompt-variables">' + task.sectionVariables.map(name =>
      '<code>{' + escapeHtml(name) + '}</code><span>' + (name === "chars" ? "当前强度档位的总字数，请保留该变量" : "运行时注入") + '</span>').join("") + '</div>' : "";
    return header + tabs + panel(selector +
      '<div class="maint-prompt-provenance" aria-label="提示词继承来源"><span><small>当前窗口</small><strong>' + escapeHtml(PROMPT_SOURCE_NAMES[source] || source) + '</strong></span><i data-lucide="arrow-right"></i><span><small>继承自</small><strong>' + escapeHtml(PROMPT_SOURCE_NAMES[inheritedSource] || inheritedSource) + '</strong></span></div>' +
      '<label class="maint-prompt-field"><span><strong>' + escapeHtml(task.sectionLabel) + '</strong><small>最多 ' + PROMPT_LIMIT + ' 字</small></span><textarea data-prompt-draft rows="16" maxlength="' + PROMPT_LIMIT + '" spellcheck="false"' + (state.promptSaving || state.promptReloading ? ' disabled' : '') + ' placeholder="留空后保存将恢复继承值。">' + escapeHtml(state.promptDraft) + '</textarea></label>' + variables +
      '<div class="maint-prompt-editor-foot"><span class="maint-prompt-status" data-state="' + escapeHtml(messageKind) + '" role="status" aria-live="polite"><span>' + escapeHtml(message) + '</span><b>' + number(state.promptDraft.length) + ' / ' + PROMPT_LIMIT + ' 字</b></span><div class="maint-prompt-actions">' +
      (state.promptStale ? '<button type="button" class="maint-prompt-secondary" data-prompt-discard ' + (state.promptReloading ? 'disabled' : '') + '><i data-lucide="rotate-ccw"></i><span>' + (state.promptReloading ? "正在读取最新配置" : "以最新配置重载") + '</span></button>' : '') +
      '<button type="button" class="maint-prompt-secondary" data-prompt-reset title="' + (task.sectionKey === "task_prompt" ? "恢复角色级继承值" : "恢复上级继承值") + '" ' + (canReset ? '' : 'disabled') + '><i data-lucide="rotate-ccw"></i><span>恢复继承值</span></button><button type="button" class="maint-prompt-primary" data-prompt-save ' + (canSave ? '' : 'disabled') + '><i data-lucide="save"></i><span>' + (state.promptSaving ? "正在保存" : "保存提示词") + '</span></button></div></div>' +
      metadata +
      '<details class="maint-prompt-default"><summary>查看内置默认模板</summary><section class="maint-prompt-preview" aria-label="模型任务内置默认提示"><div class="maint-prompt-subhead"><strong>模型任务内置默认提示</strong><span>V2 运行端 · v' + escapeHtml(task.promptVersion) + '</span></div><pre>' + escapeHtml(task.builtinPromptAvailable ? task.builtinPrompt || "（内置任务提示为空）" : "（内置提示未返回）") + '</pre></section></details>' +
      (task.sectionKey === "task_prompt" ? dynamicFields + '<details class="maint-prompt-default"><summary>运行时提示组成</summary>' + promptCompositionMarkup(selectedTask) + '</details>' : ""));
  }

  function promptWorkspaceMarkup() {
    const tasks = promptTasks();
    reconcilePromptState();
    const selected = promptSelectedTask();
    const visible = promptVisibleTasks(tasks);
    const empty = !state.modelStatus ? "正在读取模型任务目录……" : !tasks.length ? "当前没有可管理的 V2 模型任务。" : !visible.length ? "当前筛选下没有匹配的任务。" : "";
    const customizedCount = tasks.filter(task => task.customized).length;
    const taskRows = visible.map(task => '<button type="button" class="maint-prompt-task' + (task.kind === state.promptSelectedKind ? ' is-active' : '') + '" data-prompt-task="' + escapeHtml(task.kind) + '" aria-pressed="' + (task.kind === state.promptSelectedKind) + '" title="' + escapeHtml(task.description) + '" ' + (state.promptSaving ? 'disabled' : '') + '><span class="maint-prompt-task-copy"><strong>' + escapeHtml(task.label) + '</strong><small>' + escapeHtml(task.description) + '</small><code>' + escapeHtml(task.kind) + '</code></span><span class="maint-prompt-task-state" data-state="' + (task.customized ? 'custom' : 'default') + '"><i aria-hidden="true"></i><small>' + (task.customized ? '已自定义' : '默认') + '</small></span></button>').join("");
    return '<section class="maint-prompt-section" aria-labelledby="maint-prompt-heading"><div class="maint-panel-heading"><div><span class="maint-eyebrow">运行配置</span><h3 id="maint-prompt-heading">任务提示词</h3><p class="maint-scope-note"><i data-lucide="shield-check"></i>当前窗口</p></div><div class="maint-prompt-summary"><span><strong>' + tasks.length + '</strong> 个任务</span><span><strong>' + customizedCount + '</strong> 个已自定义</span></div></div><div class="maint-prompt-workspace"><aside class="maint-prompt-catalog" aria-label="任务提示词目录"><div class="maint-prompt-catalog-tools"><label><i data-lucide="search"></i><span class="sr-only">搜索任务</span><input type="search" data-prompt-search value="' + escapeHtml(state.promptQuery) + '" placeholder="搜索任务或提示内容" ' + (state.promptSaving ? 'disabled' : '') + '></label><div class="maint-prompt-mode" role="group" aria-label="提示词状态筛选"><button type="button" data-prompt-mode="all" aria-pressed="' + (state.promptMode === 'all') + '" class="' + (state.promptMode === 'all' ? 'is-active' : '') + '" ' + (state.promptSaving ? 'disabled' : '') + '>全部</button><button type="button" data-prompt-mode="customized" aria-pressed="' + (state.promptMode === 'customized') + '" class="' + (state.promptMode === 'customized' ? 'is-active' : '') + '" ' + (state.promptSaving ? 'disabled' : '') + '>已自定义</button></div></div><nav class="maint-prompt-groups" aria-label="任务分组">' + promptGroupMarkup(tasks) + '</nav><div class="maint-prompt-task-list">' + (empty ? '<div class="maint-prompt-list-state">' + escapeHtml(empty) + '</div>' : taskRows) + '</div></aside><section class="maint-prompt-editor" aria-label="提示词编辑器">' + promptEditorMarkup(selected) + '</section></div></section>';
  }

  function selectedDay(data) {
    return state.period === "date" ? state.selectedDate || localDay(data.generated_at) : localDay(data.generated_at);
  }

  function selectedBucket(data) {
    if (state.period === "total") return data.totals || data.summary || {};
    if (state.period === "7d" || state.period === "30d") {
      const window = data.windows?.[state.period];
      if (window?.totals) return window.totals;
      const days = Number(state.period.slice(0, -1)) || 0;
      return aggregateUsage((Array.isArray(data.daily) ? data.daily : []).slice(-days));
    }
    const day = selectedDay(data);
    const rows = Array.isArray(data.history) ? data.history : Array.isArray(data.daily) ? data.daily : [];
    return rows.find(row => row.day === day || row.key === day) || {day, calls: 0, tokens: 0};
  }

  function aggregateUsage(rows) {
    const fields = ["calls", "tokens", "cost_microusd", "unconfirmed_calls", "failures", "unknown_calls",
      "active_calls", "cache_reported_calls", "cache_telemetry_calls", "cache_input_tokens", "cached_input_tokens",
      "cache_creation_tokens", "cache_other_input_tokens", "cache_low_calls", "input_tokens", "output_tokens", "reported_tokens", "estimated_tokens"];
    const result = Object.fromEntries(fields.map(name => [name, 0]));
    let latencyWeighted = 0;
    let latencySamples = 0;
    let pricedRows = 0;
    for (const row of rows) {
      for (const name of fields) result[name] += Number(row[name]) || 0;
      if (row.cost_microusd != null) pricedRows += 1;
      const samples = Number(row.latency_samples) || (row.avg_latency_ms == null ? 0 : Number(row.calls) || 0);
      latencyWeighted += (Number(row.avg_latency_ms) || 0) * samples;
      latencySamples += samples;
    }
    result.avg_latency_ms = latencySamples ? Math.round(latencyWeighted / latencySamples) : null;
    for (const name of ["tokens", "reported_tokens", "input_tokens", "output_tokens", "cached_input_tokens", "cache_creation_tokens"]) {
      if (rows.length && !rows.some(row => row[name] != null)) result[name] = null;
    }
    if (rows.length && !pricedRows) result.cost_microusd = null;
    return result;
  }

  function statsFor(data) {
    const row = selectedBucket(data);
    const calls = Number(row.calls) || 0;
    const unconfirmed = Number(row.unconfirmed_calls) || 0;
    const knownCalls = Math.max(0, calls - unconfirmed);
    const cacheCalls = Number(row.cache_reported_calls) || 0;
    const cacheInput = Number(row.cache_input_tokens) || 0;
    const cached = Number(row.cached_input_tokens) || 0;
    return {
      row, day: selectedDay(data), calls, knownCalls,
      confirmedRate: calls ? Math.round(knownCalls * 1000 / calls) / 10 : null,
      tokens: Number(row.tokens) || 0,
      cost: data.execution_path === "astrbot_core" || row.cost_microusd == null ? null : Number(row.cost_microusd) || 0,
      failures: Number(row.failures) || 0,
      unconfirmed, unknown: Number(row.unknown_calls) || 0, active: Number(row.active_calls) || 0,
      cacheCalls, cacheTelemetryCalls: Number(row.cache_telemetry_calls) || 0,
      cacheInput, cached, creation: Number(row.cache_creation_tokens) || 0,
      cacheLowCalls: Number(row.cache_low_calls) || 0,
      other: Number(row.cache_other_input_tokens) || 0, input: Number(row.input_tokens) || 0,
      output: Number(row.output_tokens) || 0, latency: row.avg_latency_ms,
      cacheRate: cacheCalls > 0 && cacheInput ? Math.round(cached * 1000 / cacheInput) / 10 : null,
      cacheCoverage: knownCalls ? Math.round(cacheCalls * 1000 / knownCalls) / 10 : null,
    };
  }

  function usageComparison(data, stats) {
    if (state.period === "total") return null;
    const days = state.period === "30d" ? 30 : state.period === "7d" ? 7 : 1;
    const end = state.period === "date" ? stats.day : localDay(data.generated_at);
    const startDate = new Date(end + "T00:00:00");
    startDate.setDate(startDate.getDate() - days + 1);
    const currentStart = localDay(startDate);
    startDate.setDate(startDate.getDate() - days);
    const previousStart = localDay(startDate);
    const previousEndDate = new Date(currentStart + "T00:00:00");
    previousEndDate.setDate(previousEndDate.getDate() - 1);
    const previousEnd = localDay(previousEndDate);
    const history = Array.isArray(data.history) ? data.history : Array.isArray(data.daily) ? data.daily : [];
    const previousRows = history.filter(row => {
      const day = row.day || row.key || "";
      return day >= previousStart && day <= previousEnd;
    });
    if (!previousRows.length) return {label: days === 1 ? "较前一日" : "较前 " + days + " 日", empty: true};
    const previous = aggregateUsage(previousRows);
    if (stats.tokens == null || previous.tokens == null) return {
      label: days === 1 ? "较前一日" : "较前 " + days + " 日", unavailable: true};
    const delta = stats.tokens - previous.tokens;
    const rate = previous.tokens ? delta * 100 / previous.tokens : null;
    return {label: days === 1 ? "较前一日" : "较前 " + days + " 日", previous: previous.tokens, delta, rate};
  }

  function comparisonMarkup(comparison) {
    if (!comparison) return "";
    if (comparison.empty) return '<div class="maint-period-comparison" data-state="unknown"><span>周期对比</span><strong>前期暂无记录</strong><small>' + escapeHtml(comparison.label) + '</small></div>';
    if (comparison.unavailable) return '<div class="maint-period-comparison" data-state="unknown"><span>周期对比</span><strong>用量回执不完整</strong><small>' + escapeHtml(comparison.label) + '</small></div>';
    const tone = comparison.delta > 0 ? "up" : comparison.delta < 0 ? "down" : "flat";
    const change = comparison.rate == null
      ? comparison.delta > 0 ? "新增 " + number(comparison.delta) + " Token" : comparison.delta < 0 ? "减少 " + number(Math.abs(comparison.delta)) + " Token" : "持平"
      : (comparison.delta > 0 ? "+" : "") + comparison.rate.toFixed(1) + "%";
    return '<div class="maint-period-comparison" data-state="' + tone + '"><span>周期对比</span><strong>' + escapeHtml(change) + '</strong><small>' + escapeHtml(comparison.label) + ' · 前期 ' + number(comparison.previous) + ' Token</small></div>';
  }

  function chart(rows, endDay) {
    const source = Array.isArray(rows) ? rows : [];
    if (!source.length) return '<div class="maint-empty"><span class="maint-empty-mark">—</span><p>暂无每日统计数据。</p></div>';
    const count = state.period === "7d" ? 7 : state.period === "today" ? 14 : 30;
    const end = new Date((endDay || localDay()) + "T12:00:00");
    if (Number.isNaN(end.getTime())) return '<div class="maint-empty"><span class="maint-empty-mark">—</span><p>暂时无法确定每日趋势范围。</p></div>';
    const byDay = new Map(source.map(row => [row.day || row.key, row]));
    const visible = Array.from({length: count}, (_, index) => {
      const day = new Date(end.getTime());
      day.setDate(day.getDate() - (count - index - 1));
      const key = localDay(day);
      return byDay.get(key) || {day: key, key, tokens: 0, calls: 0};
    });
    const peak = Math.max(0, ...visible.map(row => Number(row.tokens) || 0));
    const componentsPeak = Math.max(0, ...visible.map(row =>
      (Number(row.input_tokens) || 0) + (Number(row.output_tokens) || 0)));
    const maximum = Math.max(peak, componentsPeak);
    const rawStep = maximum ? maximum / 5 : 1;
    const magnitude = Math.pow(10, Math.floor(Math.log10(rawStep)));
    const stepRatio = rawStep / magnitude;
    const axisStep = (stepRatio <= 1 ? 1 : stepRatio <= 2 ? 2 : stepRatio <= 2.5 ? 2.5 : stepRatio <= 5 ? 5 : 10) * magnitude;
    const axisMaximum = maximum ? axisStep * 5 : 1;
    const scaleMaximum = axisMaximum || 1;
    const chartHeight = 108;
    const labelStep = visible.length > 20 ? 5 : visible.length > 10 ? 2 : 1;
    const tickLabel = value => {
      if (value >= 1000000) return (value / 1000000).toFixed(value % 1000000 ? 1 : 0).replace(/\.0$/, "") + "M";
      if (value >= 1000) return (value / 1000).toFixed(value % 1000 ? 1 : 0).replace(/\.0$/, "") + "K";
      return value < 10 && value % 1 ? value.toFixed(1) : number(value);
    };
    const axisLabels = Array.from({length: 6}, (_, index) => {
      const value = axisMaximum * (5 - index) / 5;
      const label = maximum ? tickLabel(value) : index === 5 ? "0" : "";
      return '<span style="--tick-position:' + index * 20 + '%">' + escapeHtml(label) + '</span>';
    }).join("");
    const gridLines = Array.from({length: 6}, (_, index) => {
      const y = (chartHeight * index / 5).toFixed(2);
      return '<line class="' + (index === 5 ? "maint-chart-grid-line is-baseline" : "maint-chart-grid-line") + '" x1="0" y1="' + y + '" x2="1000" y2="' + y + '"></line>';
    }).join("");
    const points = visible.map((row, index) => {
      if (row.tokens == null || !Number.isFinite(Number(row.tokens))) return null;
      return {
        index,
        x: ((index + 0.5) * 1000 / visible.length).toFixed(2),
        y: (chartHeight - Math.max(0, Number(row.tokens)) / scaleMaximum * chartHeight).toFixed(2),
      };
    });
    const lineRuns = [];
    let lineRun = [];
    points.forEach(point => {
      if (point) lineRun.push(point);
      else if (lineRun.length) { lineRuns.push(lineRun); lineRun = []; }
    });
    if (lineRun.length) lineRuns.push(lineRun);
    const lines = lineRuns.filter(run => run.length > 1).map((run, index) =>
      '<polyline class="maint-chart-total-line" pathLength="100" style="--line-delay:' + Math.min(index * 100, 300) + 'ms" points="' +
      run.map(point => point.x + "," + point.y).join(" ") + '"></polyline>').join("");
    const dots = points.map(point => point
      ? '<circle class="maint-chart-total-dot" style="--point-delay:' + Math.min(point.index * 22, 440) + 'ms" cx="' + point.x + '" cy="' + point.y + '" r="3"></circle>' : "").join("");
    return '<div class="maint-chart-summary"><span>最高日 ' + number(peak) + ' Token</span><span>近 ' + visible.length + ' 日 · 可选柱条查看日期</span></div>' +
      '<div class="maint-chart-frame"><div class="maint-chart-axis" aria-hidden="true">' + axisLabels + '</div>' +
      '<div class="maint-chart" data-count="' + visible.length + '" data-slots="' + visible.length + '" style="--day-count:' + visible.length + '" aria-label="最近 ' + visible.length + ' 日 Token 趋势">' +
      '<svg class="maint-chart-overlay" viewBox="0 0 1000 ' + chartHeight + '" preserveAspectRatio="none" aria-hidden="true">' + gridLines + lines + dots + '</svg>' +
      visible.map((row, index) => {
        const tokens = Number(row.tokens) || 0;
        const day = row.day || row.key || "";
        const tokensKnown = row.tokens != null && Number.isFinite(Number(row.tokens));
        const totalHeight = tokensKnown && tokens ? Math.max(2, Math.round(tokens / scaleMaximum * chartHeight)) : 0;
        const input = Math.max(0, Number(row.input_tokens) || 0);
        const cached = Math.min(input, Math.max(0, Number(row.cached_input_tokens) || 0));
        const uncached = Math.max(0, input - cached);
        const output = Math.max(0, Number(row.output_tokens) || 0);
        const componentsKnown = row.input_tokens != null && row.output_tokens != null && (input || output || !tokens);
        const cachedHeight = Math.round(cached / scaleMaximum * chartHeight);
        const uncachedHeight = Math.round(uncached / scaleMaximum * chartHeight);
        const outputHeight = Math.round(output / scaleMaximum * chartHeight);
        const bar = componentsKnown
          ? '<i data-series="cached" style="--segment-height:' + cachedHeight + 'px;--segment-offset:0px"></i><i data-series="input" style="--segment-height:' + uncachedHeight + 'px;--segment-offset:' + cachedHeight + 'px"></i><i data-series="output" style="--segment-height:' + outputHeight + 'px;--segment-offset:' + (cachedHeight + uncachedHeight) + 'px"></i>'
          : '<i data-series="total" style="--segment-height:' + totalHeight + 'px"></i>';
        const point = tokensKnown
          ? '<span class="maint-chart-day-point" style="--point-height:' + totalHeight + 'px" aria-hidden="true"></span>' : "";
        const parsed = new Date(day + "T00:00:00");
        const label = Number.isNaN(parsed.getTime()) ? day : new Intl.DateTimeFormat("zh-CN", {month: "numeric", day: "numeric"}).format(parsed);
        const selected = state.period === "date" && state.selectedDate === day;
        const showLabel = index === visible.length - 1 || index % labelStep === 0
          && (visible.length <= 10 || index !== visible.length - 2);
        const cacheComplete = Number(row.calls) > 0 && Number(row.cache_reported_calls) >= Number(row.calls);
        const remainderLabel = cacheComplete ? "未命中缓存输入" : "其余输入（含缓存未知）";
        const detail = componentsKnown
          ? ' · 缓存输入 ' + number(cached) + ' · ' + remainderLabel + ' ' + number(uncached) + ' · 输出 ' + number(output) : "";
        const title = day + " · " + (tokensKnown ? number(tokens) : "未知") + " Token · " + number(row.calls) + " 次调用" + detail;
        return fmt('<button class="maint-chart-day" type="button" data-select-day="{{0}}" aria-pressed="{{5}}" aria-label="选择 {{1}}，{{2}} Token，{{3}} 次调用" title="{{12}}"><span class="maint-chart-column{{9}}" style="--total-height:{{4}}px;--trend-delay:{{10}}ms">{{6}}{{11}}</span><small {{7}}>{{8}}</small></button>',
          escapeHtml(day), escapeHtml(day), row.tokens == null ? "未知" : number(tokens), number(row.calls), totalHeight, selected, bar,
          showLabel ? "" : "aria-hidden=\"true\"", escapeHtml(label), tokensKnown ? " has-total" : "", Math.min(index * 22, 440),
          point, escapeHtml(title));
      }).join("") + "</div></div>";
  }

  function hourlyMarkup(data, stats) {
    const rows = (Array.isArray(data.hourly) ? data.hourly : []).filter(row => row.day === stats.day);
    if (!rows.length) return '<div class="maint-empty"><span class="maint-empty-mark">24</span><p>这一天还没有小时级调用记录。</p></div>';
    const byHour = new Map(rows.map(row => [String(row.hour).padStart(2, "0"), row]));
    const values = Array.from({length: 24}, (_, hour) => {
      const key = String(hour).padStart(2, "0");
      return {hour: key, ...(byHour.get(key) || {tokens: 0, calls: 0})};
    });
    const maximum = Math.max(1, ...values.map(row => Number(row.tokens) || 0));
    return '<div class="maint-hour-chart" aria-label="' + escapeHtml(stats.day) + ' 每小时 Token 趋势">' + values.map(row => {
      const height = Number(row.tokens) ? Math.max(4, Math.round(row.tokens / maximum * 56)) : 2;
      return fmt('<div class="maint-hour-bar" title="{{0}}:00 · {{1}} Token · {{2}} 次"><span style="--bar-height:{{3}}px"></span><small>{{4}}</small></div>',
        row.hour, row.tokens == null ? "未知" : number(row.tokens), number(row.calls), height, Number(row.hour) % 3 === 0 ? row.hour : "");
    }).join("") + "</div>";
  }

  function recentHoursMarkup(data) {
    const rows = Array.isArray(data.hourly) ? data.hourly : [];
    const byHour = new Map(rows.map(row => [String(row.day || "") + "T" + String(row.hour).padStart(2, "0"), row]));
    const anchor = new Date(data.generated_at || Date.now());
    if (Number.isNaN(anchor.getTime())) return '<div class="maint-empty"><span class="maint-empty-mark">48</span><p>暂时无法确定小时趋势范围。</p></div>';
    anchor.setMinutes(0, 0, 0);
    const values = Array.from({length: 48}, (_, index) => {
      const date = new Date(anchor.getTime() - (47 - index) * 3600000);
      const day = localDay(date);
      const hour = String(date.getHours()).padStart(2, "0");
      const key = day + "T" + hour;
      return {key, day, hour, ...(byHour.get(key) || {tokens: 0, calls: 0})};
    });
    const maximum = Math.max(1, ...values.map(row => Number(row.tokens) || 0));
    return '<div class="maint-hour-chart-wrap"><div class="maint-hour-chart maint-hour-chart-48" data-slots="48" aria-label="最近 48 小时 Token 趋势">' +
      values.map((row, index) => {
        const height = Number(row.tokens) ? Math.max(3, Math.round(row.tokens / maximum * 56)) : 2;
        const label = index % 6 === 0 || index === 47 ? row.hour : "";
      const selected = state.selectedHour === row.key;
      return fmt('<button class="maint-hour-bar{{7}}" type="button" data-select-hour="{{0}}" data-select-day="{{1}}" aria-pressed="{{8}}" aria-label="聚焦 {{2}}，{{3}} Token，{{4}} 次调用" title="{{2}} · {{3}} Token · {{4}} 次调用"><span style="--bar-height:{{5}}px"></span><small>{{6}}</small></button>',
        escapeHtml(row.key), escapeHtml(row.day), escapeHtml(row.key.replace("T", " ") + ":00"), number(row.tokens), number(row.calls), height, label,
        selected ? " is-selected" : "", selected);
      }).join("") + "</div></div>";
  }

  function trendHourlyMarkup(data, stats) {
    if (state.period === "date") return hourlyMarkup(data, stats);
    return recentHoursMarkup(data);
  }

  function dimensions(data, stats, key) {
    if (state.period === "total") return Array.isArray(stats.row[key]) ? stats.row[key] : [];
    if ((state.period === "7d" || state.period === "30d") && Array.isArray(data.windows?.[state.period]?.[key])) {
      return data.windows[state.period][key];
    }
    return Array.isArray(stats.row[key]) ? stats.row[key] : [];
  }

  function rankingMarkup(items, kind, periodLabel, sevenDayItems) {
    const rows = Array.isArray(items) ? items.slice(0, 8) : [];
    if (!rows.length) {
      const dimension = kind === "providers" ? "Provider" : "任务";
      const periodAction = state.period !== "7d" && Array.isArray(sevenDayItems) && sevenDayItems.length
        ? '<button type="button" class="maint-text-action" data-period="7d">查看近 7 日排行</button>' : "";
      return '<div class="maint-empty maint-ranking-empty"><span class="maint-empty-mark">—</span><div class="maint-ranking-empty-copy"><p>' +
        escapeHtml(periodLabel) + '暂无' + dimension + '消耗记录。</p>' + periodAction + '</div></div>';
    }
    const total = Math.max(1, rows.reduce((sum, row) => sum + (Number(row.tokens) || 0), 0));
    return '<div class="maint-ranking">' + rows.map((row, index) => {
      const key = row.key || "未知";
      const label = kind === "tasks" ? taskName(key) : key;
      const share = Math.max(0, Math.min(100, Math.round((Number(row.tokens) || 0) * 1000 / total) / 10));
      if (kind === "tasks") return fmt('<div class="maint-ranking-row maint-ranking-row--task" data-series="{{5}}"><button type="button" class="maint-ranking-feature" data-task-settings-kind="{{0}}" aria-label="打开 {{1}} 设置" title="打开设置：{{1}}"><span class="maint-ranking-label">{{1}}</span><i data-lucide="settings-2" aria-hidden="true"></i></button><span class="maint-ranking-track" aria-hidden="true"><i style="--rank-width:{{2}}%"></i></span><strong>{{3}}</strong><small>{{4}} 次</small><button type="button" class="maint-ranking-filter" data-rank-kind="tasks" data-rank-key="{{0}}" aria-label="筛选最近调用：{{1}}" title="筛选最近调用"><i data-lucide="list-filter" aria-hidden="true"></i></button></div>',
        escapeHtml(key), escapeHtml(label), share, number(row.tokens), number(row.calls), index % 7 + 1);
      return fmt('<button class="maint-ranking-row" data-series="{{6}}" type="button" data-rank-kind="{{0}}" data-rank-key="{{1}}" title="筛选最近调用：{{2}} · {{5}}%"><span class="maint-ranking-label">{{2}}</span><span class="maint-ranking-track"><i style="--rank-width:{{3}}%"></i></span><strong>{{4}}</strong><small>{{5}}%</small></button>',
        kind, escapeHtml(key), escapeHtml(label), share, number(row.tokens), share, index % 7 + 1);
    }).join("") + "</div>";
  }

  function compositionMarkup(items, kind, periodLabel, sevenDayItems, external = false) {
    const rows = (Array.isArray(items) ? items : []).slice(0, 8);
    const total = rows.reduce((sum, row) => sum + (Number(row.tokens) || 0), 0);
    const names = rows.map(row => kind === "tasks" ? taskName(row.key) : row.key || "未知 Provider");
    let stop = 0;
    const segments = rows.map((row, index) => {
      const next = index === rows.length - 1 ? 100 : stop + (total ? (Number(row.tokens) || 0) * 100 / total : 0);
      const segment = 'var(--maint-token-series-' + (index % 7 + 1) + ') ' + stop.toFixed(2) + '% ' + next.toFixed(2) + '%';
      stop = next;
      return segment;
    });
    const donut = total
      ? '<div class="maint-composition-ring" style="--composition-gradient:conic-gradient(' + segments.join(",") + ')" role="img" aria-label="Top ' + rows.length + ' ' + (kind === "tasks" ? "任务" : "Provider") + ' 分项合计 ' + number(total) + ' Token"><div><small>Top ' + rows.length + '</small><strong>' + number(total) + '</strong><span>Token</span></div></div>'
      : '<div class="maint-composition-ring is-empty" role="img" aria-label="' + escapeHtml(periodLabel) + '暂无可分组的 Token 记录"><div><small>分项用量</small><strong>—</strong><span>暂无记录</span></div></div>';
    const list = external ? externalRankingMarkup(rows) : rankingMarkup(rows, kind, periodLabel, sevenDayItems);
    return '<div class="maint-composition-layout">' + donut + '<div class="maint-composition-list">' + list +
      (names.length ? '<p class="maint-composition-note">按可用分项计算；中心值为当前展示分项合计。</p>' : '') + '</div></div>';
  }

  function trendPanelMarkup(data, stats, rows, endDay, title, hint) {
    const active = state.tokenTrendMode === "hourly" ? "hourly" : "daily";
    const daily = chart(rows, endDay);
    const hourly = trendHourlyMarkup(data, stats);
    return '<section class="maint-trend-panel maint-token-analytics-panel"><div class="maint-subhead"><div><strong>' + escapeHtml(title) + '</strong><span>' + escapeHtml(hint) +
      '</span></div><div class="maint-trend-tabs" role="group" aria-label="Token 趋势粒度"><button type="button" data-trend-mode="daily" aria-pressed="' + (active === "daily") + '">按日</button><button type="button" data-trend-mode="hourly" aria-pressed="' + (active === "hourly") + '">按时</button></div></div>' +
      '<div class="maint-token-trend-panel" data-trend-panel="daily"' + (active === "daily" ? "" : " hidden") + '><div class="maint-chart-legend"><span data-series="cached">缓存输入</span><span data-series="input">其余输入</span><span data-series="output">输出</span><span data-series="total">总计</span></div>' + daily + '</div>' +
      '<div class="maint-token-trend-panel" data-trend-panel="hourly"' + (active === "hourly" ? "" : " hidden") + '><div class="maint-chart-legend"><span data-series="total">Token 总量</span><small>小时回执仅按可用总量显示</small></div>' + hourly + '</div></section>';
  }

  function breakdownPanelMarkup(providerRows, taskRows, label, sevenDayProviders, sevenDayTasks, external = false) {
    const active = state.tokenBreakdown === "tasks" ? "tasks" : "providers";
    const rows = [
      ["providers", "按 Provider", providerRows, sevenDayProviders, "Provider 消耗排行"],
      ["tasks", "按任务", taskRows, sevenDayTasks, "任务消耗排行"],
    ];
    return '<section class="maint-rank-panel maint-token-analytics-panel"><div class="maint-subhead"><div><strong>Token 构成</strong><span>' + escapeHtml(label) + ' · 按可用账本分项</span></div>' +
      '<div class="maint-breakdown-tabs" role="group" aria-label="Token 构成维度">' + rows.map(item => '<button type="button" data-token-breakdown="' + item[0] + '" aria-pressed="' + (active === item[0]) + '" title="' + item[4] + '">' + item[1] + '</button>').join("") + '</div></div>' +
      rows.map(item => '<div class="maint-token-breakdown-panel" data-token-breakdown-panel="' + item[0] + '"' + (active === item[0] ? "" : " hidden") + '><div class="maint-subhead maint-breakdown-subhead"><strong>' + item[4] + '</strong><span>' + escapeHtml(label) + ' · ' + number(item[2].length) + ' 项</span></div>' +
        compositionMarkup(item[2], item[0], label, item[3], external) + '</div>').join("") + '</section>';
  }

  function recentPreviewMarkup(data) {
    const rows = recentRows(data).slice(0, 20);
    if (!rows.length) return '<div class="maint-empty"><span class="maint-empty-mark">—</span><p>当前时间范围没有最近调用回执。</p></div>';
    return '<div class="maint-table-wrap"><table class="maint-table maint-table--pin"><thead><tr><th>时间</th><th class="col-text">任务</th><th class="col-text">Provider</th><th>Token</th>' +
      pairHead("输入", "输出") + '<th class="col-text">状态</th><th>本地计价</th></tr></thead><tbody>' +
      rows.map(row => '<tr>' + textCell(escapeHtml(time(row.at))) +
        textCell(escapeHtml(taskName(row.kind)), "col-text") + textCell(escapeHtml(row.provider || "未知"), "col-text") +
        (row.tokens == null ? '<td class="col-primary is-zero">未知</td>' : numCell(row.tokens, "col-primary")) +
        pairCell(row.input_tokens, row.output_tokens) +
        textCell(escapeHtml(stateName(row.state)), "col-text") +
        '<td>' + (row.cost_microusd == null ? "—" : cost(row.cost_microusd)) + '</td></tr>').join("") +
      '</tbody></table></div><button type="button" class="maint-open-recent" data-maint-nav="recent">打开最近调用筛选</button>';
  }

  function detailTable(items, kind) {
    const rows = Array.isArray(items) ? items : [];
    if (!rows.length) return '<div class="maint-empty"><span class="maint-empty-mark">—</span><p>暂无可展开的明细。</p></div>';
    return '<div class="maint-table-wrap"><table class="maint-table maint-table--pin"><thead><tr><th>' +
      (kind === "providers" ? "Provider" : "任务") +
      '</th><th>Token</th>' + pairHead("输入", "输出") + '<th>调用</th><th>失败</th><th>缓存命中</th><th>平均延迟</th><th>本地计价</th></tr></thead><tbody>' +
      rows.map(row => '<tr>' + textCell(escapeHtml(kind === "tasks" ? taskName(row.key) : row.key || "未知 Provider")) +
        numCell(row.tokens, "col-primary") + pairCell(row.input_tokens, row.output_tokens) +
        numCell(row.calls) + numCell(row.failures) +
        '<td>' + percent(Number(row.cache_input_tokens) ? Number(row.cached_input_tokens) * 100 / Number(row.cache_input_tokens) : null) + '</td>' +
        '<td>' + (row.avg_latency_ms == null ? "—" : number(row.avg_latency_ms) + " ms") + '</td>' +
        '<td>' + cost(row.cost_microusd) + '</td></tr>').join("") +
      '</tbody></table></div>';
  }

  function historyMarkup(data) {
    const rows = (Array.isArray(data.history) ? data.history : Array.isArray(data.daily) ? data.daily : []).slice(-30).reverse();
    if (!rows.length) return '<div class="maint-empty"><span class="maint-empty-mark">—</span><p>尚无历史用量。</p></div>';
    return '<div class="maint-table-wrap"><table class="maint-table maint-table--pin"><thead><tr><th>日期</th><th>确认 Token</th>' + pairHead("输入", "输出") +
      '<th>调用</th>' + pairHead("失败", "未确认") + pairHead("缓存命中", "缓存写入") + '<th>平均延迟</th><th>本地计价</th></tr></thead><tbody>' +
      rows.map(row => '<tr><td><button type="button" class="maint-text-action" data-select-day="' + escapeHtml(row.day || row.key) + '">' + escapeHtml(row.day || row.key) + '</button></td>' +
        numCell(row.tokens, "col-primary") + pairCell(row.input_tokens, row.output_tokens) +
        numCell(row.calls) + pairCell(row.failures, row.unconfirmed_calls) +
        pairCell(row.cached_input_tokens, row.cache_creation_tokens) +
        '<td>' + (row.avg_latency_ms == null ? "—" : number(row.avg_latency_ms) + " ms") + '</td>' +
        '<td>' + cost(row.cost_microusd) + '</td></tr>').join("") +
      '</tbody></table></div>';
  }

  function recentRows(data) {
    const rows = Array.isArray(data.recent) ? data.recent : [];
    if (state.period === "total") return rows;
    if (state.period === "date") return rows.filter(row => String(row.at || "").slice(0, 10) === selectedDay(data));
    const today = localDay(data.generated_at);
    const days = state.period === "7d" ? 7 : state.period === "30d" ? 30 : 1;
    const start = new Date(today + "T00:00:00");
    start.setDate(start.getDate() - days + 1);
    const startDay = localDay(start);
    return rows.filter(row => {
      const day = String(row.at || "").slice(0, 10);
      return day >= startDay && day <= today;
    });
  }

  function matchingRecent(data) {
    let rows = recentRows(data);
    if (state.traceFilter?.kind === "providers") rows = rows.filter(row => row.provider === state.traceFilter.key);
    if (state.traceFilter?.kind === "tasks") rows = rows.filter(row => row.kind === state.traceFilter.key);
    if (state.callFilter === "failed") rows = rows.filter(row => row.failure);
    if (state.callFilter === "uncertain") rows = rows.filter(row => row.state === "unknown" || row.tokens == null);
    if (state.callFilter === "cache-low") rows = rows.filter(row => row.cache_hit_rate != null && Number(row.input_tokens) >= 1000 && Number(row.cache_hit_rate) < 20);
    if (state.callQuery) {
      const query = state.callQuery.trim().toLocaleLowerCase();
      rows = rows.filter(row => (String(row.provider || "") + " " + taskName(row.kind) + " " + failureName(row.failure) + " " + String(row.failure || "") + " " + String(row.at || ""))
        .toLocaleLowerCase().includes(query));
    }
    if (state.selectedHour) rows = rows.filter(row => String(row.at || "").slice(0, 13) === state.selectedHour);
    return rows;
  }

  function filteredRecent(data) {
    return matchingRecent(data).slice(0, state.showAllCalls ? 80 : 10);
  }

  function recentMarkup(data) {
    const rows = filteredRecent(data);
    if (!rows.length) return '<div class="maint-empty"><span class="maint-empty-mark">—</span><p>当前筛选范围没有调用记录。</p></div>';
    return '<div class="maint-call-list">' + rows.map((row, index) => {
      const tokens = row.tokens == null ? "用量未知" : number(row.tokens) + " Token";
      const cache = row.cached_input_tokens != null && row.input_tokens != null
        ? "缓存命中 " + number(row.cached_input_tokens) + " · " + (row.cache_hit_rate == null ? "无可计算比例" : row.cache_hit_rate + "%")
        : row.cache_creation_tokens != null ? "缓存写入 " + number(row.cache_creation_tokens) + " · 命中字段不完整" : "Provider 未报告缓存用量";
      const failure = row.failure ? '<span class="maint-call-failure">' + escapeHtml(failureName(row.failure)) + "</span>" : "";
      const diagnostic = row.response_diagnostics;
      const states = {not_received: "未收到完整响应", raw_observed: "原始响应已取得", native_parsed: "宿主已解析",
        native_rejected: "宿主解析失败", stream_rejected: "流式校验失败", contract_rejected: "回复格式校验失败",
        validated: "回复已通过校验", tools: "工具请求已通过校验", captured: "已交给记忆解析", refusal: "模型拒绝"};
      const response = diagnostic ? (() => {
        const finishReason = !diagnostic.completion_present || diagnostic.finish_reason === "missing"
          ? "未返回" : diagnostic.finish_reason || "未记录";
        const errorMeta = [diagnostic.status_code ? "HTTP " + number(diagnostic.status_code) : "",
          diagnostic.error_type || "", diagnostic.error_code || ""].filter(Boolean).join(" · ") || "错误类型未记录";
        const textStages = diagnostic.completion_present
          ? ["原始", "宿主解析", "待呈现"].map((label, index) => {
              const value = [diagnostic.raw, diagnostic.parsed, diagnostic.visible][index];
              return label + " " + (value ? number(value.chars) + " 字" +
                (value.has_internal_markers ? " · 含标签样式标记" : "") : "未记录");
            }).join(" · ")
          : "未收到响应正文";
        return '<div class="maint-call-response" data-state="' + escapeHtml(diagnostic.parse_state || "unknown") + '">' +
          '<div><strong>' + escapeHtml(states[diagnostic.parse_state] || "解析状态未知") + '</strong><span>结束原因 ' +
          escapeHtml(finishReason) + '</span><span>' + escapeHtml(errorMeta) + '</span></div><small>' +
          escapeHtml(textStages) + '</small></div>';
      })() : "";
      return fmt('<details class="maint-call-row" {{0}}><summary><span class="maint-call-time">{{1}}</span><span class="maint-call-task">{{2}}</span><span class="maint-call-provider">{{3}}</span><span class="maint-call-total">{{4}}</span><span class="maint-call-state" data-state="{{5}}">{{6}}</span></summary><div class="maint-call-detail"><div class="maint-call-facts"><span>输入 {{7}} · 输出 {{8}}</span><span>{{9}}</span><span>{{10}}</span><span>{{11}}</span>{{12}}</div>{{13}}</div></details>',
        index === 0 ? "open" : "", escapeHtml(time(row.at)), escapeHtml(taskName(row.kind)),
        escapeHtml(row.provider || "未知 Provider"), escapeHtml(tokens), escapeHtml(row.state), escapeHtml(stateName(row.state)),
        row.input_tokens == null ? "未观测" : number(row.input_tokens), row.output_tokens == null ? "未观测" : number(row.output_tokens), escapeHtml(cache),
        row.cost_microusd == null ? "费用未观测" : cost(row.cost_microusd) + " 本地计价",
        row.latency_ms == null ? "耗时未记录" : number(row.latency_ms) + " ms", failure, response);
    }).join("") + "</div>";
  }

  function diagnosticItems(data, stats) {
    const items = [];
    const add = (severity, category, title, detail, action) => items.push({severity, category, title, detail, action: action || ""});
    if (stats.failures) add("error", "model", "模型调用出现失败", number(stats.failures) + " 次失败 · 可从事件线定位任务与 Provider。", "failed");
    if (stats.active) add("warn", "model", "存在未收束的模型调用", number(stats.active) + " 条记录仍处于预留或处理中状态。", "uncertain");
    if (stats.unconfirmed || stats.unknown) add("warn", "model", "部分调用还没有确认结果",
      number(stats.unconfirmed) + " 条用量未确认 · " + number(stats.unknown) + " 条结果未知；未知结果不会自动重放。", "uncertain");
    if (!(data.providers || []).length) add("warn", "model", "没有已加载的 V2 文本 Provider", "模型任务目前无法通过 V2 适配器执行。", "provider");
    const modelStatus = state.modelStatus;
    const identityScope = data.runtime?.identity_scope || {};
    if (identityScope.dashboard_operator !== "verified" || identityScope.role_access !== "verified"
        || identityScope.token_ledger !== "current_role") {
      add("error", "identity", "身份或账本边界尚未确认", "本次管理请求缺少完整的身份与当前角色范围回执。", "identity");
    }
    if (modelStatus && Array.isArray(modelStatus.tasks)) {
      const providers = Array.isArray(modelStatus.providers) ? modelStatus.providers : [];
      const byId = new Map(providers.map(item => [item.id, item]));
      for (const task of modelStatus.tasks) {
        const primary = task.fields?.provider?.value || "";
        const fallback = task.fields?.fallback?.value || "";
        const required = Array.isArray(task.capabilities) ? task.capabilities : [];
        const primaryModel = byId.get(primary);
        const title = taskName(task.kind) + "路由";
        if (!primary) add("warn", task.kind === "conversation.reply" ? "model" : "tasks", title + "没有主 Provider", "请检查下方模型任务配置中的主路由。", "route");
        else if (!primaryModel) add("warn", task.kind === "conversation.reply" ? "model" : "tasks", title + "的 Provider 不可用", primary + " 未出现在当前 V2 模型路由中。", "route");
        else if (!required.every(capability => (primaryModel.capabilities || []).includes(capability))) {
          add("error", task.kind === "conversation.reply" ? "model" : "tasks", title + "能力不匹配", primary + " 缺少此任务要求的能力。", "route");
        }
        const outputLimit = Number(task.fields?.max_output_tokens?.value) || 0;
        if (primaryModel && outputLimit > Number(primaryModel.max_output_tokens || 0)) {
          add("error", task.kind === "conversation.reply" ? "model" : "tasks", title + "输出上限过高",
            number(outputLimit) + " 超过 Provider 可用上限 " + number(primaryModel.max_output_tokens) + "。", "route");
        }
        if (fallback && !byId.has(fallback)) add("warn", task.kind === "conversation.reply" ? "model" : "tasks", title + "的备用 Provider 不可用", fallback + " 未出现在当前 V2 模型路由中。", "route");
      }
    }
    const limit = data.budget?.hard_limit_enabled === true ? Number(data.budget.limit_tokens) || 0 : 0;
    const used = (Number(data.budget?.spent_tokens) || 0) + (Number(data.budget?.held_tokens) || 0);
    if (limit && used / limit >= .9) add("warn", "budget", "今日硬预算接近上限", number(used) + " / " + number(limit) + " Token。", "budget");
    else if (data.budget?.soft_limit_enabled && Number(data.budget?.soft_limit_tokens) > 0 && used >= Number(data.budget.soft_limit_tokens)) {
      add("info", "budget", "后台任务软限额已触发", "部分可延后任务可能暂缓。", "budget");
    }
    if (stats.cacheCalls && stats.cacheRate != null && stats.cacheRate < 20) {
      add("warn", "cache", "缓存命中偏低", "有效命中率 " + percent(stats.cacheRate) + "；稳定前缀与动态上下文可作为排查方向。", "cache-low");
    }
    if (stats.knownCalls && stats.cacheCoverage != null && stats.cacheCoverage < 50) {
      add("info", "cache", "缓存确认覆盖有限", number(stats.cacheCalls) + " / " + number(stats.knownCalls) + " 次已确认调用提供可计算命中字段。", "cache");
    }
    const runtime = data.runtime || {};
    const storage = runtime.storage || {};
    if (storage.journal_mode && storage.journal_mode !== "wal") {
      add("warn", "storage", "SQLite 未使用 WAL", "当前连接模式为 " + storage.journal_mode.toUpperCase() + "；可运行完整性检查核对数据库状态。", "storage");
    }
    if (storage.foreign_keys === false) add("error", "storage", "SQLite 外键保护未启用", "当前存储连接没有启用外键约束。", "storage");
    /* 备份、磁盘与手机网关属于安装级事实；后端随同一个 maintenance 读取返回
       host_checks（不新增轮询），页面只做判定与文案。 */
    const backup = backupCheckOf(data);
    const mobile = mobileCheckOf(data);
    /* 旧后端没有 host_checks 时保持原来 9 类检查的表述，不凭空报「读取失败」。 */
    const hostChecksAvailable = Object.keys(hostChecksOf(data)).length > 0;
    if (!hostChecksAvailable) {
      // No additive host facts in this response (older host): skip silently.
    } else if (!backup || backup.available !== true) {
      add("info", "backup", "备份状态未能读取", "后端没有返回可核对的快照摘要或磁盘位置；重新同步后再检查。", "backup");
    } else {
      const snapshots = Number(backup.snapshot_count) || 0;
      if (!snapshots) {
        add("error", "backup", "还没有可用的备份快照",
          "运行资料还没有成功快照；在「资料与维护 → 备份与还原」创建第一份后再核对还原路径。", "backup");
      } else if (!backup.latest_success_at || Number.isNaN(new Date(backup.latest_success_at).getTime())) {
        add("warn", "backup", "最近成功备份缺少时间",
          "已有 " + number(snapshots) + " 份快照，但无法确认最近一次成功时间；请重新检查备份页。", "backup");
      } else if (Date.now() - new Date(backup.latest_success_at).getTime() > 7 * 86400000) {
        add("warn", "backup", "最近成功备份已超过 7 天",
          "最近成功快照 " + time(backup.latest_success_at) + "；建议创建新快照并确认备份在其他磁盘。", "backup");
      }
      if (backup.same_volume === true) {
        add("warn", "backup", "备份与运行资料在同一块磁盘",
          "备份目录 " + (backup.backup_root || "默认目录") + " 与运行资料同盘；磁盘故障会同时影响两份。可在资料与维护中改用另一块磁盘。", "backup");
      }
      if (Number(backup.problem_count) > 0) {
        add("warn", "backup", "备份目录存在不完整快照",
          number(backup.problem_count) + " 个快照缺少完整载荷；可在备份页核对或隔离。", "backup");
      }
      const disk = hostChecksOf(data).disk?.data_root;
      const free = Number(disk?.free_bytes), total = Number(disk?.total_bytes);
      if (Number.isFinite(free) && free >= 0) {
        const ratio = Number.isFinite(total) && total > 0 ? free / total : null;
        const size = gibibytes(free) + " / " + gibibytes(total) + (ratio == null ? "" : "（" + percent(ratio * 100) + "）");
        if (free < 2 * 1024 ** 3 || (ratio != null && ratio < .03)) {
          add("error", "backup", "运行资料所在磁盘余量不足", "剩余 " + size + "；清理旧快照或更换存储位置后再继续。", "backup");
        } else if (free < 10 * 1024 ** 3 || (ratio != null && ratio < .10)) {
          add("warn", "backup", "运行资料所在磁盘余量偏低", "剩余 " + size + "；建议清理或把数据与备份迁到更大的磁盘。", "backup");
        }
      }
    }
    if (!hostChecksAvailable) {
      // Older host without host_checks: no mobile gateway signal to show.
    } else if (!mobile || mobile.available !== true) {
      add("info", "mobile", "手机网关状态未能读取", "无法读取现实触及的手机连接设置；重新同步后再检查。", "mobile");
    } else if (!mobile.enabled) {
      add("info", "mobile", "手机网关未启用",
        "手机端陪伴终端当前不可用；需要时在「手机连接」生成配对令牌并启用网关。", "mobile");
    } else if (mobile.pairing_token_configured !== true) {
      add("error", "mobile", "手机网关已启用但缺少配对令牌",
        "网关需要至少 24 位配对令牌才能启动；请重新生成后再配对。", "mobile");
    } else if (mobile.module_state && mobile.module_state !== "running") {
      add("warn", "mobile", "手机网关已配置但模块未运行",
        "现实触及模块状态为" + mobileModuleName(mobile.module_state) + "；启用模块后再配对。", "mobile");
    }
    const flows = Array.isArray(runtime.conversation) ? runtime.conversation : [];
    const failedParts = flows.reduce((sum, row) => sum + (Number(row.failed_parts) || 0), 0);
    const uncertainParts = flows.reduce((sum, row) => sum + (Number(row.unknown_parts) || 0), 0);
    const pendingParts = flows.reduce((sum, row) => sum + (Number(row.pending_parts) || 0), 0);
    const directNoReplies = Array.isArray(runtime.inbound_no_replies) ? runtime.inbound_no_replies.length : 0;
    const flowNoReplies = flows.filter(row => row.no_reply).length;
    if (directNoReplies || flowNoReplies) add("warn", "delivery", "近期入站存在未回复", number(directNoReplies + flowNoReplies) + " 条明确未回复记录；可按事件时间和原因码查看。", "delivery");
    if (failedParts) add("error", "delivery", "回复消息投递失败", number(failedParts) + " 个片段收到失败回执；可检查最近收口轨迹。", "delivery");
    if (uncertainParts) add("warn", "delivery", "回复投递结果未知", number(uncertainParts) + " 个片段没有确定回执；未知结果不会自动重发。", "delivery");
    if (pendingParts) add("info", "delivery", "回复仍在等待投递", number(pendingParts) + " 个片段处于待发送或发送中状态。", "delivery");
    const voice = state.integrations?.voice;
    const image = state.integrations?.image;
    if (!voice) add("info", "voice", "TTS 状态未能读取", "重新同步后可检查独立的语音 Provider。", "voice");
    else if (!(voice.providers || []).length) add("info", "voice", "没有已加载的 TTS Provider", "语音合成测试暂不可用。", "voice");
    if (!image) add("info", "image_generation", "图片扩展状态未能读取", "可重新同步原生图片工具注册状态。", "image");
    else {
      const actions = Array.isArray(image.actions) ? image.actions : [];
      if (!imageActionReady(actions, IMAGE_GENERATION_ACTION)) {
        add("warn", "image_generation", "没有当前注册的生图工具", "这里仅验证工具注册；不会提交真实生成请求。", "image");
      }
    }
    if (!items.length) add("info", "model", "当前范围没有待处理信号", "角色账本、任务路由、Provider 与确认状态暂未发现异常。", "");
    return items;
  }

  function categoryEvidence(key, items, data, stats) {
    data = data || {};
    const runtime = data.runtime || {};
    const actions = Array.isArray(state.integrations?.image?.actions) ? state.integrations.image.actions : [];
    const voices = Array.isArray(state.integrations?.voice?.providers) ? state.integrations.voice.providers : [];
    const flows = Array.isArray(runtime.conversation) ? runtime.conversation : [];
    const inboundNoReplies = Array.isArray(runtime.inbound_no_replies) ? runtime.inbound_no_replies : [];
    const materials = Array.isArray(runtime.prompt_materials) ? runtime.prompt_materials : [];
    const storage = runtime.storage || {};
    const proactive = runtime.proactive || {};
    const backup = backupCheckOf(data);
    const mobile = mobileCheckOf(data);
    const backupEvidence = !backup || backup.available !== true ? "状态未知"
      : number(backup.snapshot_count) + " 份快照 · " + (backup.same_volume === true ? "同盘" : "跨盘");
    const mobileEvidence = !mobile || mobile.available !== true ? "状态未知"
      : !mobile.enabled ? "未启用" : mobile.pairing_token_configured === true ? "已配对" : "缺少令牌";
    return ({
      all: number(items.length) + " 项信号",
      model: number(items.filter(item => item.category === "model").length) + " 项信号",
      identity: runtime.identity_scope?.token_ledger === "current_role" ? "角色范围已核对" : "等待身份回执",
      tasks: number(items.filter(item => item.category === "tasks").length) + " 项信号",
      proactive: number(proactive.calls) + " 条已记录",
      image_generation: imageActionReady(actions, IMAGE_GENERATION_ACTION) ? "工具已注册" : "工具未就绪",
      voice: voices.length ? number(voices.length) + " 个 Provider" : "暂无 Provider",
      storage: storage.journal_mode ? String(storage.journal_mode).toUpperCase() : "状态未知",
      backup: backupEvidence,
      mobile: mobileEvidence,
      delivery: number(inboundNoReplies.length + flows.length) + " 条轨迹",
      prompt: number(materials.length) + " 类来源",
      budget: data.budget?.soft_limit_enabled ? "软限额已开" : "软限额未开",
      cache: Number(stats?.cacheTelemetryCalls) > 0 ? (Number(stats?.cacheCoverage) >= 100 ? "已收回执" : "部分覆盖") : "等待回执",
    })[key] || "";
  }

  function categoriesMarkup(items, data, stats) {
    return diagnosticCategories.map(([key, label, description]) => {
      const count = key === "all" ? items.length : items.filter(item => item.category === key).length;
      const evidence = categoryEvidence(key, items, data, stats);
      return fmt('<button type="button" data-diagnostic-category="{{0}}" aria-pressed="{{1}}" class="{{2}}" title="{{3}}"><span>{{4}}</span><b>{{5}}</b><small>{{6}}</small></button>',
        key, state.category === key, state.category === key ? "is-active" : "", escapeHtml(description + " · " + evidence), escapeHtml(label), count, escapeHtml(evidence));
    }).join("");
  }

  function categoryFocusMarkup(data, stats, items) {
    const runtime = data.runtime || {};
    const storage = runtime.storage || {};
    const flows = Array.isArray(runtime.conversation) ? runtime.conversation : [];
    const inboundNoReplies = Array.isArray(runtime.inbound_no_replies) ? runtime.inbound_no_replies : [];
    const materials = Array.isArray(runtime.prompt_materials) ? runtime.prompt_materials : [];
    const proactive = runtime.proactive || {};
    const identity = runtime.identity_scope || {};
    const debounce = Array.isArray(runtime.debounce_trace) ? runtime.debounce_trace : [];
    const routes = Array.isArray(state.modelStatus?.tasks) ? state.modelStatus.tasks : [];
    const textProviders = Array.isArray(data.providers) ? data.providers : [];
    const voiceProviders = Array.isArray(state.integrations?.voice?.providers) ? state.integrations.voice.providers : [];
    const imageActions = Array.isArray(state.integrations?.image?.actions) ? state.integrations.image.actions : [];
    const backupCheck = backupCheckOf(data) || {};
    const mobileCheck = mobileCheckOf(data) || {};
    const currentCategory = state.category;
    const category = diagnosticCategories.find(item => item[0] === currentCategory) || diagnosticCategories[0];
    let facts = [];
    let actions = [];
    let note = "";
    const addAction = (target, label) => actions.push({target, label});
    if (currentCategory === "model") {
      facts = [["失败调用", number(stats.failures)], ["待确认", number(stats.unconfirmed + stats.unknown)], ["文本 Provider", number(textProviders.length)]];
      addAction("test-zone", "测试 Provider"); addAction("task-audit", "检查任务路由");
      note = "未知结果保留在账本中，不会自动重放。";
    } else if (currentCategory === "tasks") {
      facts = [["已配置任务", number(routes.length)], ["路由信号", number(items.filter(item => item.category === "tasks").length)], ["后台软限额", data.budget?.soft_limit_enabled ? "已启用" : "未启用"]];
      addAction("task-audit", "查看任务路由"); addAction("budget-block", "查看后台限额");
      note = "任务路由检查只读取 Provider 与能力元数据。";
    } else if (currentCategory === "identity") {
      const identity = runtime.identity_scope || {};
      facts = [["管理身份", identity.dashboard_operator === "verified" ? "已验证" : "未知"],
        ["V2 管理授权", identity.role_access === "verified" ? "已通过" : "未知"],
        ["用量范围", identity.token_ledger === "current_role" ? "当前角色" : "未知"]];
      addAction("identity-evidence", "查看隔离证据");
      note = "仅展示这次已授权请求的校验状态，不输出身份、账号或会话标识。";
    } else if (currentCategory === "delivery") {
      const failed = flows.reduce((sum, row) => sum + (Number(row.failed_parts) || 0), 0);
      const unknown = flows.reduce((sum, row) => sum + (Number(row.unknown_parts) || 0), 0);
      const noReplies = inboundNoReplies.length + flows.filter(row => row.no_reply).length;
      const debounce = Array.isArray(runtime.debounce_trace) ? runtime.debounce_trace : [];
      facts = [["未回复", number(noReplies)], ["投递失败", number(failed)], ["防抖记录", number(debounce.length)]];
      addAction("delivery-evidence", "查看投递轨迹"); addAction("debounce-evidence", "查看消息收口");
      note = "查看等待窗、合并状态与投递回执；不展示消息正文，未知结果不会自动重发。";
    } else if (currentCategory === "proactive") {
      facts = [["已记录触达", number(proactive.calls)], ["最近记录", time(proactive.latest_at)], ["拦截原因", "尚未接入"]];
      addAction("proactive-evidence", "查看触达记录");
      note = "当前 V2 保存触达总数与最近时间，候选评分链尚未接入。";
    } else if (currentCategory === "image_generation") {
      const found = imageActions.filter(row => row.action === IMAGE_GENERATION_ACTION && row.registered && row.current).length;
      facts = [["当前动作", "生图"], ["已注册", number(found)], ["提交生成请求", "否"]];
      addAction("test-zone", "检查图片工具");
      note = "这里核对扩展声明状态，不会生成或识别真实图片。";
    } else if (currentCategory === "voice") {
      facts = [["TTS Provider", number(voiceProviders.length)], ["最近测试", state.voiceTest?.title || "尚未运行"], ["发送给联系人", "否"]];
      addAction("test-zone", "测试 TTS");
      note = "合成测试只生成短音频，不发送给联系人；Provider 可能计费。";
    } else if (currentCategory === "storage") {
      facts = [["日志模式", String(storage.journal_mode || "未知").toUpperCase()], ["外键", storage.foreign_keys ? "开启" : "关闭"], ["最近检查", state.storageTest?.title || "尚未运行"]];
      addAction("test-zone", "运行只读检查");
      note = "完整性检查使用 quick_check 与外键核对，不修改数据库。";
    } else if (currentCategory === "backup") {
      facts = [["快照", number(backupCheck.snapshot_count) + " 份"],
        ["最近成功", time(backupCheck.latest_success_at)],
        ["位置", backupCheck.available !== true ? "状态未知" : backupCheck.same_volume === true ? "与运行资料同盘" : backupCheck.same_volume === false ? "另一块磁盘" : "状态未知"]];
      addAction("backup", "查看备份与磁盘");
      note = "只读取已完成快照的摘要与磁盘余量；创建、删除或还原快照仍在资料与维护中手动执行。";
    } else if (currentCategory === "mobile") {
      facts = [["网关", mobileCheck.available !== true ? "状态未知" : mobileCheck.enabled ? "已启用" : "未启用"],
        ["配对令牌", mobileCheck.pairing_token_configured === true ? "已配置" : "缺少"],
        ["模块", mobileModuleName(mobileCheck.module_state)]];
      addAction("mobile", "查看手机网关");
      note = "只读取手机连接设置与模块状态，不启动网关、不回显令牌，也不触发配对。";
    } else if (currentCategory === "prompt") {
      facts = [["材料来源", number(materials.length)], ["统计范围", "最近 20 条输入"], ["显示正文", "从不展示"]];
      addAction("prompt-evidence", "查看来源元数据");
      note = "仅列来源、用途与纳入状态，不保留提示词或消息正文。";
    } else if (currentCategory === "budget") {
      const budget = data.budget || {};
      facts = [["已入账", number(budget.spent_tokens)], ["预留中", number(budget.held_tokens)], ["硬上限", number(budget.limit_tokens)]];
      addAction("budget-block", "查看预算配置");
      note = "后台软限额只影响新建的可选任务；正在运行的任务会继续完成。";
    } else if (currentCategory === "cache") {
      facts = [["命中率", percent(stats.cacheRate)], ["确认覆盖", percent(stats.cacheCoverage)], ["低命中样本", number(recentRows(data).filter(row => row.cache_hit_rate != null && Number(row.input_tokens) >= 1000 && Number(row.cache_hit_rate) < 20).length)]];
      addAction("cache-panel", "查看缓存诊断"); addAction("low-cache-calls", "查看低命中调用");
      note = "检测请求结构变化并对照 Provider 命中回执；低命中或结构变化只是排查线索。";
    } else {
      facts = [["错误", number(items.filter(item => item.severity === "error").length)], ["警告", number(items.filter(item => item.severity === "warn").length)], ["已配置任务", number(routes.length)]];
      addAction("test-zone", "打开链路测试"); addAction("task-audit", "检查模型路由");
      note = "检查数据限定为已选 V2 身份与角色。";
    }
    const factMarkup = facts.map(([label, value]) => fmt('<div><span>{{0}}</span><strong>{{1}}</strong></div>', escapeHtml(label), escapeHtml(value))).join("");
    const actionMarkup = actions.map(item => item.target === "low-cache-calls"
      ? '<button type="button" data-set-call-filter="cache-low">' + escapeHtml(item.label) + '</button>'
      : fmt('<button type="button" data-focus-panel="{{0}}">{{1}}</button>', item.target, escapeHtml(item.label))).join("");
    const workspaceTitle = currentCategory === "delivery" ? "消息收口工作区" : category[1] + "工作区";
    return '<section class="maint-diagnostic-focus" data-category="' + escapeHtml(currentCategory) + '" id="maint-diagnostic-focus-pane"><div class="maint-diagnostic-focus-copy"><span class="maint-eyebrow">当前排查范围</span><h4>' + escapeHtml(workspaceTitle) + '</h4><p>' + escapeHtml(note) + '</p></div><div class="maint-diagnostic-focus-facts">' + factMarkup + '</div><div class="maint-diagnostic-focus-actions">' + actionMarkup + '</div></section>';
  }

  function timelineMarkup(data) {
    const events = [];
    for (const row of recentRows(data)) {
      const rowCategory = row.kind === "conversation.reply" ? "model" : "tasks";
      if (row.failure) events.push({severity: "error", category: rowCategory, title: failureName(row.failure), row});
      else if (row.state === "unknown" || row.tokens == null) events.push({severity: "warn", category: rowCategory, title: "用量或结果未确认", row});
      else if (["reserved", "dispatched"].includes(row.state)) events.push({severity: "warn", category: rowCategory, title: "调用尚未收束", row});
      if (row.cache_hit_rate != null && Number(row.input_tokens) >= 1000 && Number(row.cache_hit_rate) < 20) {
        events.push({severity: "info", category: "cache", title: "缓存命中 " + row.cache_hit_rate + "%", row});
      }
      if (events.length >= 12) break;
    }
    for (const row of (data.runtime?.inbound_no_replies || [])) {
      const code = row.code ? " · " + failureName(row.code) + " (" + row.code + ")" : "";
      events.push({severity: "warn", category: "delivery", title: noReplyReasonName(row.reason_code), at: row.observed_at,
        detail: (row.scope === "unmatched" ? "未匹配到 V2 目标" : "当前角色") + " · 事件 " + String(row.event_ref || "").slice(0, 8) + code});
    }
    for (const row of (data.runtime?.conversation || [])) {
      if (row.no_reply) events.push({severity: "warn", category: "delivery", title: "入站消息未收到回复", at: row.created_at,
        detail: flowReasonName(row.flow_reason) + " · " + number(row.input_count) + " 条输入"});
      else if (row.failed_parts) events.push({severity: "error", category: "delivery", title: "回复片段投递失败", at: row.created_at,
        detail: number(row.failed_parts) + " 个失败 · " + number(row.input_count) + " 条输入"});
      else if (row.unknown_parts) events.push({severity: "warn", category: "delivery", title: "回复投递回执未知", at: row.created_at,
        detail: number(row.unknown_parts) + " 个结果未知 · 不自动重发"});
      else if (row.flow_state === "blocked" || row.flow_state === "closed") events.push({severity: "warn", category: "delivery", title: "消息收口 " + row.flow_state,
        at: row.created_at, detail: row.flow_reason || "流程已结束"});
      else if (row.delivered_parts) events.push({severity: "info", category: "delivery", title: "回复已收到投递回执", at: row.created_at,
        detail: number(row.delivered_parts) + " / " + number(row.part_count) + " 个片段已送达"});
    }
    events.sort((a, b) => String(b.at || b.row?.at || "").localeCompare(String(a.at || a.row?.at || "")));
    const visible = events.filter(item => (state.category === "all" || state.category === item.category)
      && (state.severity === "all" || state.severity === item.severity));
    if (!visible.length) return '<div class="maint-empty"><span class="maint-empty-mark">—</span><p>这个问题类型与严重程度下，没有对应的近期事件回执。</p></div>';
    return '<div class="maint-event-list">' + visible.slice(0, 16).map(item => {
      const detail = item.row ? taskName(item.row.kind) + " · " + (item.row.provider || "未知 Provider") + " · " + stateName(item.row.state) : item.detail;
      return fmt('<article data-severity="{{0}}" data-category="{{1}}"><i></i><time>{{2}}</time><div><strong>{{3}}</strong><small>{{4}}</small></div></article>',
        item.severity, item.category, escapeHtml(time(item.at || item.row?.at)), escapeHtml(item.title), escapeHtml(detail));
    }).join("") + "</div>";
  }

  function operationsMarkup(data) {
    const runtime = data.runtime || {};
    const storage = runtime.storage || {};
    const flows = Array.isArray(runtime.conversation) ? runtime.conversation : [];
    const inboundNoReplies = Array.isArray(runtime.inbound_no_replies) ? runtime.inbound_no_replies : [];
    const materials = Array.isArray(runtime.prompt_materials) ? runtime.prompt_materials : [];
    const proactive = runtime.proactive || {};
    const identity = runtime.identity_scope || {};
    const debounce = Array.isArray(runtime.debounce_trace) ? runtime.debounce_trace : [];
    const sync = ({0: "关闭", 1: "普通", 2: "完整", 3: "额外完整"})[storage.synchronous] || "未知";
    const backupCheck = backupCheckOf(data) || {};
    const mobileCheck = mobileCheckOf(data) || {};
    const category = state.category;
    const show = (categories) => category === "all" || categories.includes(category);
    const flowRows = flows.length ? flows.slice(0, 6).map(row => {
      const tone = row.no_reply || row.failed_parts ? "error" : row.unknown_parts || row.pending_parts || row.flow_state === "blocked" ? "warn" : "ok";
      return fmt('<article data-state="{{0}}"><time>{{1}}</time><strong>{{2}} · {{3}}</strong><small>{{4}} 输入 · {{5}} 已送达 / {{6}} 失败 / {{7}} 未知</small></article>',
        tone, escapeHtml(time(row.created_at)), escapeHtml(row.flow_state || row.batch_state), escapeHtml(row.flow_reason || "消息收口"),
        number(row.input_count), number(row.delivered_parts), number(row.failed_parts), number(row.unknown_parts));
    }).join("") : '<div class="maint-empty"><span class="maint-empty-mark">—</span><p>当前角色没有已保存的消息收口轨迹。</p></div>';
    const noReplyRows = inboundNoReplies.length ? inboundNoReplies.map(row => fmt(
      '<article data-state="warn"><time>{{0}}</time><strong>{{1}}</strong><small>{{2}} · 事件 {{3}}{{4}}</small></article>',
      escapeHtml(time(row.observed_at)), escapeHtml(noReplyReasonName(row.reason_code)),
      row.scope === "unmatched" ? "未匹配到 V2 目标" : (row.channel === "group" ? "当前角色 · 群聊" : "当前角色 · 私聊"),
      escapeHtml(String(row.event_ref || "").slice(0, 8)),
      row.code ? " · " + escapeHtml(failureName(row.code)) + " (" + escapeHtml(row.code) + ")" : "")).join("") :
      '<div class="maint-empty"><span class="maint-empty-mark">—</span><p>当前保留范围内没有入口阶段的未回复记录。</p></div>';
    const materialRows = materials.length ? materials.slice(0, 8).map(row => fmt(
      '<article><span>{{0}}<small>{{1}}</small></span><b>{{2}}</b><i>{{3}} 次</i></article>',
      escapeHtml(row.owner), escapeHtml(row.purpose), escapeHtml(row.state), number(row.uses))).join("") :
      '<div class="maint-empty"><span class="maint-empty-mark">—</span><p>最近 20 条输入没有可列出的上下文材料元数据。</p></div>';
    const proofState = value => value === "verified" || value === "current_role" ? "ok" : "unknown";
    const identityRows = [
      ["管理身份", identity.dashboard_operator, "Dashboard 登录校验"],
      ["V2 管理授权", identity.role_access, "当前管理角色授权"],
      ["Token 账本", identity.token_ledger, "当前角色范围"],
    ].map(([label, value, detail]) => fmt(
      '<article data-state="{{0}}"><i></i><span><strong>{{1}}</strong><small>{{2}}</small></span><b>{{3}}</b></article>',
      proofState(value), escapeHtml(label), escapeHtml(detail),
      value === "verified" ? "已验证" : value === "current_role" ? "已隔离" : "未知")).join("");
    const debounceRows = debounce.length ? debounce.map(row => {
      const tone = row.flow_state === "blocked" ? "warn" : ["complete", "closed"].includes(row.flow_state) ? "ok" : "active";
      const timing = [
        row.quiet_window_ms == null ? "安静窗未记录" : "安静窗 " + number(row.quiet_window_ms) + " ms",
        row.merge_window_ms == null ? "合并上限未记录" : "合并上限 " + number(row.merge_window_ms) + " ms",
      ].join(" · ");
      return fmt('<article data-state="{{0}}"><time>{{1}}</time><div><strong>{{2}} · {{3}}</strong><small>{{4}} 条输入 / {{5}} 个片段 · {{6}}</small></div><span>{{7}}</span></article>',
        tone, escapeHtml(time(row.at)), escapeHtml(flowStateName(row.flow_state)), escapeHtml(flowReasonName(row.reason)),
        number(row.input_count), number(row.part_count), escapeHtml(timing),
        row.flow_state === "collecting" ? "预计收口 " + time(row.due_at) : "时限 " + time(row.deadline_at));
    }).join("") : '<div class="maint-empty"><span class="maint-empty-mark">—</span><p>当前角色还没有可展示的消息收口防抖轨迹。</p></div>';
    const sections = [];
    if (show(["identity"])) sections.push('<section data-evidence-kind="identity" data-focus-target="identity-evidence"><div class="maint-subhead"><strong>统一身份与隔离</strong><span>本次管理请求</span></div>' +
      '<div class="maint-identity-checks">' + identityRows + '</div><p class="maint-evidence-note">只显示鉴权与账本边界的通过状态，不返回身份、账号、会话或角色标识。</p></section>');
    if (show(["storage", "model"])) sections.push('<section data-evidence-kind="storage" data-focus-target="storage-block"><div class="maint-subhead"><strong>V2 SQLite 存储</strong><span>' +
      escapeHtml(String(storage.journal_mode || "状态未知").toUpperCase()) + '</span></div><div class="maint-evidence-stats">' +
      '<div><span>同步级别</span><b>' + escapeHtml(sync) + '</b></div><div><span>锁等待</span><b>' + number(storage.busy_timeout_ms) + ' ms</b></div>' +
      '<div><span>外键</span><b>' + (storage.foreign_keys ? "开启" : "关闭") + '</b></div><div><span>数据库页</span><b>' + number(storage.page_count) + '</b></div></div>' +
      '</section>');
    if (show(["backup"]) && backupCheckOf(data)) sections.push('<section data-evidence-kind="backup" data-focus-target="backup"><div class="maint-subhead"><strong>备份与磁盘</strong><span>' +
      escapeHtml(backupCheck.available !== true ? "状态未知" : number(backupCheck.snapshot_count) + " 份快照") + '</span></div><div class="maint-evidence-stats">' +
      '<div><span>最近成功</span><b>' + escapeHtml(time(backupCheck.latest_success_at)) + '</b></div>' +
      '<div><span>备份位置</span><b>' + escapeHtml(backupCheck.same_volume === true ? "与运行资料同盘" : backupCheck.same_volume === false ? "另一块磁盘" : "状态未知") + '</b></div>' +
      '<div><span>运行资料余量</span><b>' + escapeHtml(gibibytes(hostChecksOf(data).disk?.data_root?.free_bytes)) + '</b></div>' +
      '<div><span>不完整快照</span><b>' + number(backupCheck.problem_count) + '</b></div></div>' +
      '<p class="maint-evidence-note">只读取已完成快照的摘要与磁盘余量；不会创建、删除或还原快照。</p></section>');
    if (show(["mobile"]) && mobileCheckOf(data)) sections.push('<section data-evidence-kind="mobile" data-focus-target="mobile"><div class="maint-subhead"><strong>手机网关</strong><span>' +
      escapeHtml(mobileCheck.available !== true ? "状态未知" : mobileCheck.enabled ? "已启用" : "未启用") + '</span></div><div class="maint-evidence-stats">' +
      '<div><span>配对令牌</span><b>' + (mobileCheck.pairing_token_configured === true ? "已配置" : "缺少") + '</b></div>' +
      '<div><span>模块状态</span><b>' + escapeHtml(mobileModuleName(mobileCheck.module_state)) + '</b></div>' +
      '<div><span>监听端口</span><b>' + number(mobileCheck.port || 6322) + '</b></div>' +
      '<div><span>监听地址</span><b>' + escapeHtml(mobileCheck.host || "未记录") + '</b></div></div>' +
      '<p class="maint-evidence-note">不显示令牌内容，也不会启动网关或发起配对；需要时在「手机连接」手动生成。</p></section>');
    if (show(["delivery", "model"])) sections.push('<section data-evidence-kind="delivery" data-focus-target="delivery-evidence"><div class="maint-subhead"><strong>消息收口与投递</strong><span>当前身份 / 角色范围</span></div><div class="maint-evidence-flow">' + flowRows + '</div></section>');
    if (show(["delivery"])) sections.push('<section data-evidence-kind="no-reply" data-focus-target="delivery-evidence"><div class="maint-subhead"><strong>未回复记录</strong><span>最多保留 500 条</span></div><div class="maint-evidence-flow">' + noReplyRows + '</div><p class="maint-evidence-note">仅显示时间、类别、原因和随机事件编号；不含消息正文或用户、群号。</p></section>');
    if (show(["delivery"])) sections.push('<section data-evidence-kind="debounce" data-focus-target="debounce-evidence"><div class="maint-subhead"><strong>消息聚合与防抖追踪</strong><span>最近 ' + number(debounce.length) + ' 条</span></div>' +
      '<div class="maint-debounce-list">' + debounceRows + '</div><p class="maint-evidence-note">只显示收口状态、等待窗和计数；不展示消息内容。未知投递结果不会在这里触发重发。</p></section>');
    if (show(["prompt", "model"])) sections.push('<section data-evidence-kind="prompt" data-focus-target="prompt-evidence"><div class="maint-subhead"><strong>上下文注入来源</strong><span>最近 20 条输入</span></div><p class="maint-evidence-note">只显示材料来源与状态，不显示任何提示词、消息或注入正文。</p><div class="maint-material-list">' + materialRows + '</div></section>');
    if (show(["proactive"])) sections.push('<section data-evidence-kind="proactive" data-focus-target="proactive-evidence"><div class="maint-subhead"><strong>主动触达记录</strong><span>当前身份 / 角色范围</span></div><div class="maint-proactive-snapshot"><strong>' + number(proactive.calls) + '</strong><span>条已记录主动触达</span><small>最近记录 · ' + escapeHtml(time(proactive.latest_at)) + '</small></div><p class="maint-evidence-note">主动候选拦截原因与完整评分链尚未接入此 V2 面板。</p></section>');
    if (!sections.length) return "";
    return '<details class="maint-evidence-drawer"><summary><span>详细运行证据</span><small>身份隔离、消息收口、上下文来源与主动触达</small></summary>' +
      '<div class="maint-evidence-grid">' + sections.join("") + '</div></details>';
  }

  function taskAuditMarkup() {
    const tasks = Array.isArray(state.modelStatus?.tasks) ? state.modelStatus.tasks : [];
    if (!tasks.length) return '<div class="maint-empty"><span class="maint-empty-mark">?</span><p>模型任务快照未读取到；刷新后可检查各任务 Provider 路由。</p></div>';
    const providers = new Map((Array.isArray(state.modelStatus.providers) ? state.modelStatus.providers : []).map(item => [item.id, item]));
    return '<div class="maint-task-audit-list">' + tasks.map(task => {
      const primary = String(task.fields?.provider?.value || "");
      const fallback = String(task.fields?.fallback?.value || "");
      const route = providers.get(primary);
      const required = Array.isArray(task.capabilities) ? task.capabilities : [];
      const compatible = route && required.every(capability => (route.capabilities || []).includes(capability));
      const outputLimit = Number(task.fields?.max_output_tokens?.value) || 0;
      const outputFits = !route || !outputLimit || outputLimit <= Number(route.max_output_tokens || 0);
      const result = !primary || !route ? "warn" : !compatible || !outputFits ? "error" : "ok";
      const label = result === "ok" ? "路由正常" : result === "error" ? "能力或上限不匹配" : "Provider 不可用";
      const routeText = primary ? primary + (fallback ? " · 备用 " + fallback : " · 无备用") : "未设置主 Provider";
      const meta = [required.join(" + ") || "无额外能力", task.application_state || "应用状态未知"].join(" · ");
      return fmt('<article data-state="{{0}}"><div><strong>{{1}}</strong><small>{{2}} · {{3}}</small></div><span>{{4}}</span></article>',
        result, escapeHtml(taskName(task.kind)), escapeHtml(routeText), escapeHtml(meta), escapeHtml(label));
    }).join("") + "</div>";
  }

  function resultMarkup(item) {
    if (!item) return "";
    const elapsed = item.latency_ms ?? item.elapsed_ms;
    const facts = [];
    if (elapsed != null) facts.push("耗时 " + number(elapsed) + " ms");
    if (item.code) facts.push("错误码 " + String(item.code));
    if (item.stage) facts.push("阶段 " + String(item.stage));
    if (item.exception_type) facts.push("异常 " + String(item.exception_type));
    if (item.error) facts.push("详细错误 " + String(item.error));
    if (item.request_id) facts.push("诊断号 " + String(item.request_id));
    if (Array.isArray(item.integrity)) facts.push("完整性 " + item.integrity.join(" · "));
    if (item.foreign_key_issues != null) facts.push("外键问题 " + number(item.foreign_key_issues));
    const audio = item.audioUrl ? '<div class="maint-audio-preview"><span>本次结果 · 试听</span><audio controls preload="none" src="' +
      escapeHtml(item.audioUrl) + '" aria-label="TTS 测试合成结果试听"></audio></div>' : "";
    return '<div class="maint-test-result" data-state="' + escapeHtml(item.status) + '" role="status"><div><strong>' + escapeHtml(item.title) + '</strong><small>' + escapeHtml(item.message) + '</small></div>' +
      (facts.length ? '<ul>' + facts.map(value => '<li>' + escapeHtml(value) + '</li>').join("") + '</ul>' : "") +
      audio + (item.suggestion ? '<p><b>下一步</b> ' + escapeHtml(item.suggestion) + '</p>' : "") + '</div>';
  }

  function clearVoiceAudio(target = state) {
    const url = target?.voiceTest?.audioUrl;
    if (url && typeof window.URL?.revokeObjectURL === "function") window.URL.revokeObjectURL(url);
  }

  function voiceAudioUrl(result) {
    if (result.status !== "ok" || typeof result.audio_base64 !== "string" || !result.audio_base64
        || typeof window.atob !== "function" || typeof window.Blob !== "function"
        || typeof window.URL?.createObjectURL !== "function") return "";
    try {
      const binary = window.atob(result.audio_base64), bytes = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index);
      const mime = /^audio\/[a-z0-9.+-]+$/i.test(result.audio_mime_type || "") ? result.audio_mime_type : "";
      return window.URL.createObjectURL(new window.Blob([bytes], {type: mime}));
    } catch {
      return "";
    }
  }

  function integrationsMarkup(data) {
    const voice = state.integrations?.voice;
    const image = state.integrations?.image;
    const tts = Array.isArray(voice?.providers) ? voice.providers : [];
    const allActions = Array.isArray(image?.actions) ? image.actions : [];
    const actions = state.category === "image_generation" ? allActions.filter(item => item.action === IMAGE_GENERATION_ACTION) : allActions;
    const textProviders = Array.isArray(data.providers) ? data.providers : [];
    const showText = ["all", "model", "tasks"].includes(state.category);
    const showVoice = ["all", "voice"].includes(state.category);
    const showImage = ["all", "image_generation"].includes(state.category);
    const textOptions = textProviders.map(item => fmt('<option value="{{0}}" {{1}}>{{0}}{{2}}</option>',
      escapeHtml(item.id), item.id === state.provider ? "selected" : "", item.model ? " · " + escapeHtml(item.model) : "")).join("");
    const voiceOptions = tts.map(item => fmt('<option value="{{0}}" {{1}}>{{0}}</option>',
      escapeHtml(item.id), item.id === state.voiceProvider ? "selected" : "")).join("");
    const imageOptions = actions.map(item => fmt('<option value="{{0}}" {{1}}>{{0}}</option>',
      escapeHtml(item.action), item.action === state.imageAction ? "selected" : "")).join("");
    const storage = data.runtime?.storage || {};
    const showStorage = ["all", "storage"].includes(state.category);
    let imageResult = null;
    if (state.imageTest) {
      const ok = state.imageTest.registered && state.imageTest.current;
      imageResult = {status: ok ? "ok" : "failed", title: ok ? "注册检查通过" : "注册检查未通过",
        message: state.imageTest.error || (state.imageTest.action + " · " + (state.imageTest.registered ? "已注册" : "未注册") +
          " · " + (state.imageTest.current ? "当前声明有效" : "声明已变化或不可用")),
        suggestion: ok ? "当前动作已就绪，可继续检查调用侧权限与输入。" : "刷新运行态后确认对应扩展已启用并完成注册。"};
    }
    const tests = [];
    if (showText) tests.push('<div class="maint-test-row maint-test-row--provider"><div><strong>文本 Provider</strong><small>固定连接探测，不生成角色回复；服务商可能计费。</small></div><div class="maint-test-controls">' +
      '<select data-provider-select ' + (textOptions ? "" : "disabled") + '>' + (textOptions || '<option value="">无可用 Provider</option>') + '</select>' +
      '<button class="maint-probe-button" type="button" data-provider-probe ' + (!textOptions || state.testing ? "disabled" : "") + '><i data-lucide="' + (state.testing ? "loader-circle" : "scan-line") + '"></i><span>' + (state.testing ? "探测中" : "测试") + '</span></button></div></div>' + resultMarkup(state.test));
    if (showVoice) tests.push('<div class="maint-test-row maint-test-row--voice"><div><strong>TTS 合成</strong><small>合成一句短测试音频但不发送给联系人；服务商可能计费。</small></div><div class="maint-test-controls">' +
      '<select data-voice-provider ' + (voiceOptions ? "" : "disabled") + '>' + (voiceOptions || '<option value="">无可用 TTS</option>') + '</select>' +
      '<select data-voice-language aria-label="测试语音语言"><option value="zh">中文</option><option value="ja">日语</option><option value="en">英语</option></select>' +
      '<button class="maint-probe-button" type="button" data-voice-test ' + (!voiceOptions || state.voiceTesting ? "disabled" : "") + '><i data-lucide="' + (state.voiceTesting ? "loader-circle" : "audio-lines") + '"></i><span>' + (state.voiceTesting ? "合成中" : "测试") + '</span></button></div></div>' + resultMarkup(state.voiceTest));
    if (showImage) tests.push('<div class="maint-test-row maint-test-row--image"><div><strong>原生图片工具</strong><small>只验证扩展注册状态，不会提交图片生成请求。</small></div><div class="maint-test-controls">' +
      '<select data-image-action ' + (imageOptions ? "" : "disabled") + '>' + (imageOptions || '<option value="">未发现图片工具</option>') + '</select>' +
      '<button class="maint-probe-button" type="button" data-image-test ' + (!imageOptions || state.imageTesting ? "disabled" : "") + '><i data-lucide="' + (state.imageTesting ? "loader-circle" : "image-check") + '"></i><span>' + (state.imageTesting ? "检查中" : "检查") + '</span></button></div></div>' + resultMarkup(imageResult));
    if (showStorage) tests.push('<div class="maint-test-row maint-test-row--storage"><div><strong>SQLite 完整性</strong><small>' +
      escapeHtml(String(storage.journal_mode || "状态未知").toUpperCase()) + ' · quick_check 与外键核对，只读且不会改写数据库。</small></div><div class="maint-test-controls">' +
      '<button class="maint-probe-button" type="button" data-storage-test ' + (state.storageTesting ? "disabled" : "") + '><i data-lucide="' +
      (state.storageTesting ? "loader-circle" : "database-zap") + '"></i><span>' + (state.storageTesting ? "检查中" : "检查") + '</span></button></div></div>' + resultMarkup(state.storageTest));
    const body = tests.length ? tests.join("") : '<div class="maint-test-empty"><span data-lucide="activity"></span><div><strong>此问题类型没有安全的主动测试</strong><small>检查结果来自运行回执与只读证据；不会对用户发送消息或重放未知请求。</small></div></div>';
    return '<div class="maint-test-zone" data-focus-target="test-zone"><div class="maint-test-head"><div><span class="maint-eyebrow">组件检测</span><h4>Provider、语音、图片与存储</h4></div><span>按需运行 · 不会自动触发</span></div>' + body + '</div>';
  }

  function diagnosticFaqMarkup(data, stats) {
    const definitions = {
      all: [
        ["用量未知代表什么？", "账本已记录调用，但 Provider 没有提供可确认的 Token 确认。结果未知不会自动重放，避免重复副作用。", "test-zone", "检查 Provider"],
        ["排障数据来自哪里？", "只读取当前 V2 身份与角色的调用、路由和运行回执；外部插件数据不会混入。", "task-audit", "查看路由"],
        ["测试会产生费用或发送消息吗？", "文本探测与 TTS 合成会调用所选 Provider，可能计费；图片检查与 SQLite 检查只读，不会向联系人发送内容。", "test-zone", "查看测试"],
      ],
      identity: [
        ["管理身份校验为什么只显示结论？", "管理身份与角色授权通过后才会返回本页；页面只给出当前请求的校验结论，不显示身份、账号、会话或角色标识。", "identity-evidence", "查看隔离证据"],
        ["Token 统计范围包含哪些对象？", "当前角色的 AstrBot 核心回复、历史 V2 回复和插件附加任务组成角色总用量，各来源只合计一次。其他 AstrBot 会话和外部插件不并入。", "identity-evidence", "查看账本边界"],
      ],
      model: [
        ["探测通过就代表真实回复正常吗？", "探测只确认 Provider 可响应，不会走角色提示、消息接收和投递链路；异常时再结合调用回执定位。", "test-zone", "测试 Provider"],
        ["结果未知为什么不能重试？", "请求可能已到达 Provider，但回执未能确认。自动重放可能重复计费或产生重复回复，因此保留原记录供核对。", "task-audit", "检查模型路由"],
      ],
      tasks: [
        ["任务路由检查什么？", "核对主/备用 Provider 是否存在、能力是否匹配，以及输出上限是否超过 Provider 声明值；不读取提示词内容。", "task-audit", "查看任务路由"],
        ["后台任务为什么会延后？", "启用软限额后，达到阈值会暂停新建的可选后台任务。已经运行的任务会继续完成。", "budget-block", "查看软限额"],
      ],
      delivery: [
        ["部分片段失败后会自动补发吗？", "未知或失败的投递回执不会自动重发，先核对原收口轨迹与平台回执，再决定是否由用户发起新一轮。", "delivery-evidence", "查看投递轨迹"],
        ["没有投递轨迹表示什么？", "V2 只展示已保存的角色级收口证据；没有记录不代表平台一定没有处理该消息。", "delivery-evidence", "查看收口证据"],
        ["消息为什么会等待后再合并？", "防抖安静窗会把短时间连续输入收进同一轮；最大等待和轮次时限有上界。这里展示状态与时窗，不显示消息正文。", "debounce-evidence", "查看防抖追踪"],
      ],
      proactive: [
        ["为什么没有主动消息？", "这里能核对已记录的主动触达次数与最近时间；静默、冷却、候选评分和拦截原因尚未接入本页。", "proactive-evidence", "查看触达记录"],
        ["能从这里发送测试主动消息吗？", "可以。先选择用途并确认真实投递；系统会使用该用途对应生产器的有效来源，再经过角色模型、人格复核并投递到核心管理员私聊。没有当前来源时不会调用模型或生成通用文案，结果未知时不会重试。", "proactive-test", "查看主动消息测试"],
      ],
      image_generation: [
        ["注册检查会实际生成图片吗？", "不会。它只核对生图动作是否已注册且声明仍有效，不发起外部生成请求。", "test-zone", "检查生图工具"],
        ["动作未就绪时先检查什么？", "刷新运行快照，确认图片扩展已启用并完成加载，再检查相应动作声明。", "test-zone", "重新检查"],
      ],
      voice: [
        ["TTS 测试会把语音发给谁？", "不会发送给联系人。测试仅合成短音频并返回状态；所选服务商可能计费。", "test-zone", "运行 TTS 测试"],
        ["TTS Provider 不可用？", "确认 AstrBot 已加载对应语音 Provider，并检查所选语言绑定后重新探测。", "test-zone", "检查语音绑定"],
      ],
      storage: [
        ["SQLite 检查会修改数据吗？", "不会。检查通过只读快照执行 quick_check 和外键一致性核对。", "test-zone", "运行只读检查"],
        ["WAL 状态异常怎么办？", "先运行完整检查并核对插件连接状态；不要在页面中清理或重置数据库。", "test-zone", "检查存储"],
      ],
      backup: [
        ["备份检查会创建快照吗？", "不会。这里只读取已完成快照的摘要、位置与磁盘余量；创建、删除或还原仍在「资料与维护 → 备份与还原」中手动执行。", "backup", "查看备份与磁盘"],
        ["为什么备份要放在另一块磁盘？", "默认备份目录跟随运行资料，同一块磁盘故障会同时影响数据和备份。填写外部绝对路径后，快照会落到另一块磁盘的独立安装目录。", "backup", "查看备份位置"],
      ],
      mobile: [
        ["网关关闭时还能看到令牌字段吗？", "不能。令牌、监听地址和端口都依赖网关开关，网关关闭时不再渲染；已经填过的值会留在草稿里，不会因为隐藏而被清空。", "mobile", "查看手机网关"],
        ["提示缺少配对令牌怎么办？", "在「手机连接」点「生成并启用网关」；生成后会同时启用网关并显示一次令牌，复制到手机端完成配对。", "mobile", "打开手机连接"],
      ],
      prompt: [
        ["为什么看不到提示词正文？", "本页只保留材料来源、用途与纳入状态，不显示或记录提示词、消息和注入正文。", "prompt-evidence", "查看来源"],
        ["来源标记 omitted 是什么？", "该轮材料被记录但没有纳入最终上下文；这里只提供来源计数，不推断具体文本原因。", "prompt-evidence", "查看材料状态"],
      ],
      budget: [
        ["软限额会中断正在运行的任务吗？", "不会。软限额只影响达到阈值后新建的可选后台任务；已运行任务继续完成。", "budget-block", "查看限额"],
        ["Provider 余额等于本地预算吗？", "不等于。本页只显示 V2 本地角色预算，当前没有统一授权的 Provider 余额接口。", "budget-block", "查看本地预算"],
      ],
      cache: [
        ["低命中能证明缓存被破坏吗？", "不能。低命中和结构签名只提供排查线索，命中变化也不证明某个请求字段导致缓存失效。", "cache-panel", "查看缓存样本"],
        ["稳定时命中率如何计算？", "按同一会话、同一任务的相邻请求比较签名维度；仅统计该维度未变化且 Provider 同时报告输入量与缓存命中的请求。", "cache-panel", "查看签名诊断"],
      ],
    };
    const items = definitions[state.category] || definitions.all;
    return items.map(item => fmt('<article><div><strong>{{0}}</strong><p>{{1}}</p></div><button type="button" data-focus-panel="{{2}}">{{3}}</button></article>',
      escapeHtml(item[0]), escapeHtml(item[1]), item[2], escapeHtml(item[3]))).join("");
  }

  function memoryAssessment(samples, running, error) {
    if (error) return {state: "warn", label: "采样中断", detail: error};
    if (!samples.length) return {state: "unknown", label: "尚未采样", detail: "开始后会连续观察 3 分钟。"};
    const delta = Number(samples[samples.length - 1].rss_bytes) - Number(samples[0].rss_bytes);
    if (running) return {state: "sampling", label: "正在采样", detail: "本窗口净变化 " + (delta >= 0 ? "+" : "") + memorySize(delta)};
    if (samples.length < 4) return {state: "unknown", label: "样本不足", detail: "至少需要 4 个有效样本才能观察趋势。"};
    if (delta >= MEMORY_GROWTH_SUSPECT_BYTES) {
      return {state: "warn", label: "出现增长嫌疑", detail: "进程工作集在本窗口净增 " + memorySize(delta) + "；建议在相同负载下复测。"};
    }
    if (delta >= MEMORY_GROWTH_WATCH_BYTES) {
      return {state: "watch", label: "有轻微增长", detail: "进程工作集净增 " + memorySize(delta) + "；可在静置后重复观察。"};
    }
    return {state: "ok", label: "未见明显净增长", detail: "本窗口结束时与起点相差 " + (delta >= 0 ? "+" : "") + memorySize(delta) + "。"};
  }

  function memoryChartMarkup(samples) {
    if (!samples.length) return '<div class="maint-memory-chart-empty">开始采样后显示 RSS 变化</div>';
    const values = samples.map(item => Number(item.rss_bytes)).filter(Number.isFinite);
    const low = Math.min(...values);
    const high = Math.max(...values);
    const bars = samples.map((item, index) => {
      const value = Number(item.rss_bytes);
      if (!Number.isFinite(value)) return "";
      const height = high === low ? 50 : 14 + Math.round((value - low) * 86 / (high - low));
      return '<span style="--sample-height:' + height + '%" title="样本 ' + (index + 1) + ' · ' + memorySize(value) + '"></span>';
    }).join("");
    return '<div class="maint-memory-chart" role="img" aria-label="AstrBot 进程 RSS 内存变化">' + bars + '</div>';
  }

  function memoryDiagnosticMarkup() {
    const samples = Array.isArray(state.memorySamples) ? state.memorySamples : [];
    const latest = samples[samples.length - 1];
    const assessment = memoryAssessment(samples, state.memoryRunning, state.memoryError);
    const progress = Math.min(100, Math.round(samples.length * 100 / MEMORY_SAMPLE_LIMIT));
    const heap = latest?.python_tracing_active
      ? memorySize(latest.python_heap_current_bytes) : latest ? "未开启" : "—";
    const source = latest?.rss_source === "psutil" ? "psutil" : latest?.rss_source === "windows_working_set"
      ? "Windows 工作集" : latest?.rss_source === "proc_statm" ? "Linux /proc" : latest ? "系统接口" : "等待采样";
    const startDisabled = state.memoryRunning ? "disabled" : "";
    const stopHidden = state.memoryRunning ? "" : "hidden";
    const status = state.memoryRunning ? "采样中 · " + samples.length + " / " + MEMORY_SAMPLE_LIMIT + " 个样本"
      : state.memoryComplete ? "检测完成 · " + samples.length + " 个样本"
        : state.memoryStopReason ? state.memoryStopReason + " · " + samples.length + " 个样本"
          : samples.length ? "已采集 " + samples.length + " 个样本" : "尚未运行";
    return '<section class="maint-memory-diagnostic" aria-labelledby="maint-memory-heading"><div class="maint-memory-heading"><div><span class="maint-eyebrow">进程 / 内存</span><h4 id="maint-memory-heading">内存增长检测</h4><p>按需观察 AstrBot 进程 RSS；包含 AstrBot 与所有插件，不能单独归因到 V2。</p></div><div class="maint-memory-actions"><button type="button" data-memory-start ' + startDisabled + '><i data-lucide="play"></i><span>' + (state.memoryRunning ? "采样进行中" : "开始 3 分钟检测") + '</span></button><button type="button" data-memory-stop ' + stopHidden + '><i data-lucide="square"></i><span>停止</span></button></div></div>' +
      '<div class="maint-memory-result" data-state="' + assessment.state + '"><strong>' + escapeHtml(assessment.label) + '</strong><span>' + escapeHtml(assessment.detail) + '</span></div>' +
      '<div class="maint-memory-content"><div class="maint-memory-chart-wrap">' + memoryChartMarkup(samples) + '<div class="maint-memory-progress" role="progressbar" aria-label="内存采样进度" aria-valuemin="0" aria-valuemax="' + MEMORY_SAMPLE_LIMIT + '" aria-valuenow="' + samples.length + '"><i style="--progress:' + progress + '%"></i></div></div>' +
      '<div class="maint-memory-metrics"><div><span>当前 RSS</span><strong>' + (latest ? memorySize(latest.rss_bytes) : "—") + '</strong></div><div><span>Python 堆（仅已开启追踪）</span><strong>' + heap + '</strong></div><div><span>采样来源</span><strong>' + escapeHtml(source) + '</strong></div><div><span>检测状态</span><strong>' + escapeHtml(status) + '</strong></div></div></div>' +
      '<p class="maint-memory-note">每 10 秒读取一次，共 3 分钟；净增 4 MiB 提示关注，净增 16 MiB 标记增长嫌疑。增长只代表本次窗口的进程内存变化，不足以证明泄漏；本功能不会启动全局堆追踪。离开排障页会停止采样。</p></section>';
  }

  async function takeMemorySample(current, ticket) {
    if (!current || current !== state || ticket !== generation || !current.memoryRunning || current.memorySampling) return;
    current.memorySampling = true;
    try {
      const sample = await api("GET", modelPath() + "/memory");
      if (ticket !== generation || state !== current || !current.memoryRunning) return;
      if (sample?.rss_bytes == null || !Number.isFinite(Number(sample.rss_bytes))) {
        throw new Error("当前平台无法读取 AstrBot 进程 RSS。");
      }
      current.memorySamples.push(sample);
      if (Date.now() - current.memoryStartedAt >= MEMORY_SAMPLE_DURATION_MS || current.memorySamples.length >= MEMORY_SAMPLE_LIMIT) {
        current.memoryRunning = false;
        current.memoryComplete = true;
        current.memoryStopReason = "";
      }
    } catch (error) {
      if (ticket === generation && state === current && current.memoryRunning) {
        current.memoryError = routeError(error.code || error.message);
        current.memoryRunning = false;
        current.memoryStopReason = "采样已中断";
      }
    } finally {
      if (ticket === generation && state === current) {
        current.memorySampling = false;
        if (current.memoryRunning) {
          current.memoryTimer = window.setTimeout(() => {
            current.memoryTimer = null;
            if (typeof document !== "undefined" && document.visibilityState === "hidden") {
              stopMemoryDiagnostic("页面隐藏，采样已停止", {target: current});
              return;
            }
            void takeMemorySample(current, ticket);
          }, MEMORY_SAMPLE_INTERVAL_MS);
        } else if (hasDiagnosticResults(current)) {
          scheduleDiagnosticAutoClear(current, ticket);
        }
        render();
      }
    }
  }

  function startMemoryDiagnostic() {
    if (!state || state.memoryRunning) return;
    clearDiagnosticExpiry(state);
    state.memorySamples = [];
    state.memoryError = "";
    state.memoryComplete = false;
    state.memoryStopReason = "";
    state.memoryStartedAt = Date.now();
    state.memoryRunning = true;
    render();
    void takeMemorySample(state, generation);
  }

  function stopMemoryDiagnostic(reason = "已手动停止", {render: shouldRender = true, target = state} = {}) {
    if (!target) return;
    if (target.memoryTimer != null) window.clearTimeout?.(target.memoryTimer);
    target.memoryTimer = null;
    if (target.memoryRunning) {
      target.memoryRunning = false;
      target.memoryStopReason = reason;
      if (target === state && hasDiagnosticResults(target)) scheduleDiagnosticAutoClear(target);
      if (shouldRender && target === state) render();
    }
  }

  function diagnosticsMarkup(data, stats) {
    const items = diagnosticItems(data, stats);
    const visible = items.filter(item => (state.category === "all" || item.category === state.category)
      && (state.severity === "all" || item.severity === state.severity));
    const errors = items.filter(item => item.severity === "error").length;
    const warnings = items.filter(item => item.severity === "warn").length;
    const selectedErrors = visible.filter(item => item.severity === "error").length;
    const selectedWarnings = visible.filter(item => item.severity === "warn").length;
    const voice = state.integrations?.voice;
    const image = state.integrations?.image;
    const taskCount = Array.isArray(state.modelStatus?.tasks) ? state.modelStatus.tasks.length : null;
    const routeCount = Array.isArray(state.modelStatus?.providers) ? state.modelStatus.providers.length : null;
    const identityScope = data.runtime?.identity_scope || {};
    const identityReady = identityScope.dashboard_operator === "verified" && identityScope.role_access === "verified"
      && identityScope.token_ledger === "current_role";
    const debounceCount = Array.isArray(data.runtime?.debounce_trace) ? data.runtime.debounce_trace.length : null;
    const backupSummary = backupCheckOf(data);
    const mobileSummary = mobileCheckOf(data);
    const runtime = [
      ["all", "管理与记录", number(stats.calls) + " 次调用 · 当前角色隔离", "ok"],
      ["identity", "统一身份与隔离", identityReady ? "授权通过 · 当前角色账本" : "身份或范围未确认", identityReady ? "ok" : "warn"],
      ["tasks", "模型任务", taskCount == null ? "路由状态未知" : number(taskCount) + " 项已配置", taskCount == null ? "unknown" : taskCount ? "ok" : "warn"],
      ["model", "文本 Provider", number((data.providers || []).length) + " 个 V2 适配器" + (routeCount == null ? "" : " · 路由快照已读"), data.providers?.length ? "ok" : "warn"],
      ["delivery", "消息收口", debounceCount == null ? "防抖状态未知" : number(debounceCount) + " 条防抖记录", debounceCount == null ? "unknown" : "ok"],
      ["voice", "TTS Provider", voice ? number((voice.providers || []).length) + " 个已加载" : "状态未知", voice ? "ok" : "unknown"],
      ["image_generation", "图片工具", image ? number((image.actions || []).filter(row => row.registered && row.current).length) + " 个当前注册" : "状态未知", image ? "ok" : "unknown"],
      ["backup", "备份快照", backupSummary?.available !== true ? "状态未知"
        : number(backupSummary.snapshot_count) + " 份 · " + (backupSummary.same_volume === true ? "同盘" : "跨盘"),
        backupSummary?.available !== true ? "unknown" : number(backupSummary.snapshot_count) ? "ok" : "warn"],
      ["mobile", "手机网关", mobileSummary?.available !== true ? "状态未知"
        : !mobileSummary.enabled ? "未启用" : mobileSummary.pairing_token_configured === true ? "已配对" : "缺少令牌",
        mobileSummary?.available !== true ? "unknown" : !mobileSummary.enabled ? "idle"
          : mobileSummary.pairing_token_configured === true ? "ok" : "warn"],
    ].filter(item => state.category === "all" || item[0] === state.category
      || (state.category === "model" && item[0] === "all"));
    const severityButtons = [["all", "全部"], ["error", "错误"], ["warn", "警告"], ["info", "信息"]].map(([key, label]) =>
      fmt('<button type="button" data-diagnostic-severity="{{0}}" aria-pressed="{{1}}" class="{{2}}">{{3}}</button>',
        key, state.severity === key, state.severity === key ? "is-active" : "", label)).join("");
    const issueRows = visible.length ? visible.map(item => {
      let action = "";
      if (item.action === "failed") action = '<button type="button" data-set-call-filter="failed">查看失败</button>';
      if (item.action === "uncertain") action = '<button type="button" data-set-call-filter="uncertain">查看记录</button>';
      if (item.action === "cache-low") action = '<button type="button" data-set-call-filter="cache-low">查看样本</button>';
      if (item.action === "route") action = '<button type="button" data-focus-panel="task-audit">查看路由</button>';
      if (item.action === "identity") action = '<button type="button" data-focus-panel="identity-evidence">查看边界</button>';
      if (item.action === "provider") action = '<button type="button" data-focus-panel="test-zone">定位测试</button>';
      if (item.action === "budget") action = '<button type="button" data-focus-panel="budget-block">查看限额</button>';
      if (item.action === "backup") action = '<button type="button" data-go="manage" data-manage="data">打开资料与维护</button>';
      if (item.action === "mobile") action = '<button type="button" data-go="manage" data-manage="mobile">打开手机连接</button>';
      return fmt('<article class="maint-diagnostic-item" data-severity="{{0}}"><span class="maint-severity-mark">{{1}}</span><div><strong>{{2}}</strong><p>{{3}}</p></div>{{4}}</article>',
        item.severity, item.severity === "error" ? "!" : item.severity === "warn" ? "△" : "i",
        escapeHtml(item.title), escapeHtml(item.detail), action);
    }).join("") : '<div class="maint-empty"><span class="maint-empty-mark">✓</span><p>此分类与严重程度下没有待处理信号。</p></div>';
    const audit = ["all", "model", "tasks"].includes(state.category)
      ? '<details class="maint-task-audit" data-focus-target="task-audit"' + (state.category === "tasks" ? " open" : "") + '><summary><strong>模型任务路由</strong><span>' +
        (taskCount == null ? "状态未知" : number(taskCount) + " 项") + '</span></summary>' + taskAuditMarkup() + '</details>' : "";
    const refreshButton = '<button type="button" class="maint-diagnostic-refresh" data-refresh ' + (state.loading ? "disabled" : "") + '><i data-lucide="refresh-cw"></i><span>' + (state.loading ? "正在检查" : "重新检查") + '</span></button>';
    const clearResultsButton = '<button type="button" class="maint-diagnostic-clear" data-clear-diagnostic-results ' +
      (hasDiagnosticResults() ? "" : "disabled") + ' title="清除本页测试结果，不删除运行账本" aria-label="清除本页测试结果"><i data-lucide="eraser"></i><span>清除本次结果</span></button>';
    const resultNotice = state.diagnosticNotice
      ? '<p class="maint-diagnostic-notice" role="status" aria-live="polite">' + escapeHtml(state.diagnosticNotice) + '</p>' : "";
    const faqOpen = state.category === "all" ? "" : " open";
    const categoryLabel = escapeHtml(state.category === "all" ? "全部分类"
      : (diagnosticCategories.find(item => item[0] === state.category) || ["", "问题类型"])[1]);
    const summaryMarkup = '<div class="maint-diagnostic-summary"><strong>' +
      (selectedErrors ? selectedErrors + " 项错误待处理" : selectedWarnings ? selectedWarnings + " 项信号待关注"
        : visible.length ? "当前筛选下没有待处理信号" : "没有待处理信号") +
      '</strong><span>' + categoryLabel + ' · 本类 ' + visible.length + ' 项 · 全局错误 ' + errors + ' / 警告 ' + warnings + '</span></div>';
    const overviewMarkup = '<aside class="maint-diagnostic-rail" aria-label="问题类型与筛选状态">' +
      summaryMarkup + '<div class="maint-diagnostic-nav-heading"><strong>排查范围</strong><span>' + items.length + ' 项信号</span></div>' +
      '<nav class="maint-diagnostic-categories" role="group" aria-label="问题类型">' +
      categoriesMarkup(items, data, stats) + '</nav></aside>';
    const signalsMarkup = '<section class="maint-diagnostic-signals" aria-labelledby="maint-diagnostic-signals-heading">' +
      '<div class="maint-diagnostic-section-heading"><div><span class="maint-eyebrow">信号 / 02</span>' +
      '<h4 id="maint-diagnostic-signals-heading">其他运行诊断</h4><p>先处理当前分类信号，再运行按需检测</p></div>' +
      '<div class="maint-diagnostic-filter" role="group" aria-label="按严重程度筛选">' + severityButtons + '</div></div>' +
      '<div class="maint-diagnostic-items">' + issueRows + '</div></section>';
    const checksMarkup = '<section class="maint-diagnostic-checks" aria-labelledby="maint-diagnostic-checks-heading">' +
      '<div class="maint-diagnostic-section-heading"><div><span class="maint-eyebrow">检查 / 03</span>' +
      '<h4 id="maint-diagnostic-checks-heading">按需检测</h4><p>真实投递需确认；Provider 与 TTS 可能计费。</p></div></div>' +
      proactiveTestMarkup() + memoryDiagnosticMarkup() + integrationsMarkup(data) + '</section>';
    const laneTitle = state.category === "all" ? "事件时间线"
      : "最近 · " + escapeHtml((diagnosticCategories.find(item => item[0] === state.category) || ["", "问题"])[1]);
    const activityMarkup = '<div class="maint-diagnostic-activity" aria-label="近期事件与运行快照"><section class="maint-event-lane"><div class="maint-subhead"><strong>' +
      laneTitle + '</strong><span>当前角色真实回执</span></div>' + timelineMarkup(data) +
      '</section><section class="maint-runtime-lane"><div class="maint-subhead"><strong>运行快照</strong><span>' +
      escapeHtml(time(data.generated_at)) + '</span></div><div class="maint-runtime-grid">' +
      runtime.map(item => fmt('<div data-state="{{0}}" data-runtime-category="{{1}}"><span>{{2}}</span><strong>{{3}}</strong></div>',
        item[3], item[0], escapeHtml(item[1]), escapeHtml(item[2]))).join("") +
      '</div><p>当前数据来自 V2 已选角色账本；路由检查仅读元数据。</p>' + audit + '</section></div>';
    const evidenceMarkup = operationsMarkup(data);
    const faqMarkup = '<details class="maint-faq"' + faqOpen + '><summary>诊断说明与常见问题 · ' +
      escapeHtml((diagnosticCategories.find(item => item[0] === state.category) || ["", "全部"])[1]) +
      '</summary><div>' + diagnosticFaqMarkup(data, stats) + '</div></details>';
    return '<section class="maint-diagnostics-panel" aria-labelledby="maint-diagnostics-heading"><div class="maint-panel-heading"><div>' +
      '<span class="maint-eyebrow">诊断 / 01</span><h3 id="maint-diagnostics-heading">排障与测试</h3></div>' +
      '<div class="maint-diagnostic-heading-actions"><span class="maint-check-state" data-state="' + (errors || warnings ? "warn" : "ok") + '">' +
      (errors ? errors + " 项错误" : warnings ? warnings + " 项关注" : "运行正常") + '</span>' + clearResultsButton + refreshButton +
      '</div></div>' + resultNotice + '<div class="maint-diagnostic-workspace">' + overviewMarkup +
      '<div class="maint-diagnostic-main">' + categoryFocusMarkup(data, stats, items) +
      '<div class="maint-diagnostic-inspection">' + signalsMarkup + activityMarkup + '</div>' +
      checksMarkup + evidenceMarkup + faqMarkup + '</div></div></section>';
  }

  const proactivePurposes = [
    ["affection", "想念与亲近"], ["care", "情绪关怀"], ["play", "轻松互动"],
    ["repair", "关系修复"], ["chat", "日常话题"], ["greeting", "问候与纪念"],
    ["life", "分享自己的生活"], ["creation", "作品与阅读"], ["news", "外部资讯"],
    ["followup", "接回旧话题"], ["invitation", "共同活动邀请"], ["reminder", "约定与提醒"],
    ["environment", "环境与状态变化"], ["meal", "饭点话题"], ["screen", "已授权屏幕观察"],
  ];

  function proactiveTestMarkup() {
    const result = state.proactiveTest;
    const purposeLabel = proactivePurposes.find(item => item[0] === state.proactivePurpose)?.[1] || state.proactivePurpose;
    const skipReasonLabels = {
      duplicate: "与近期已发送内容重复", low_confidence: "模型把握不足，按沉默阈值暂缓发送",
      continuation_after_silence: "刚回复过且用户未再发言，这次主动只是在接自己的话，已跳过",
      persona_review: "人格复核未放行", framework_guard: "正文含框架标记，发送前检查拦截",
      presentation_empty: "文字清理后正文为空", forbidden_word: "命中当前禁用词规则",
      source_coverage: "来源材料不足以支持当前表述", no_sendable_content: "处理后没有可发送正文",
      model_decision: "模型判断当前不适合发送", unknown: "系统记录为跳过，但没有保存具体原因",
    };
    const modelSkipReasonLabels = {
      insufficient_context: "可用材料不足", no_natural_opening: "当前没有自然的开口点",
      purpose_mismatch: "材料与测试目的不匹配", repetitive: "内容可能重复",
      respect_user_boundary: "为尊重对方边界而保持沉默", other: "模型选择暂不发送",
    };
    const failureTitles = {
      dashboard_identity_required: "Dashboard 登录身份不可用",
      unbound_identity: "Dashboard 身份尚未完成绑定",
      binding_revoked: "Dashboard 身份绑定已撤销",
      binding_expired: "Dashboard 身份绑定已过期",
      binding_not_active: "Dashboard 身份绑定尚未生效",
      binding_rebound: "Dashboard 身份已重新绑定",
      scope_installation_mismatch: "Dashboard 身份不属于当前安装",
      scope_not_granted: "当前 Dashboard 身份未获该配置范围授权",
      core_access_required: "当前 Dashboard 身份没有核心管理权限",
      authorization_denied: "当前 Dashboard 身份没有配置管理权限",
      authorization_revoked: "当前 Dashboard 身份的管理授权已撤销",
      authorization_expired: "当前 Dashboard 身份的管理授权已过期",
      authorization_not_active: "当前 Dashboard 身份的管理授权尚未生效",
      setup_binding_confirmation_required: "核心管理员窗口尚未绑定确认",
      contact_attempt_budget: "主动模型调用次数上限已触发",
      contact_delivery_budget: "本私聊今日实际联系额度已用完",
      contact_permission_required: "主动联系授权未通过",
      contact_purpose_paused: "所选联系目的当前已暂停",
      contact_quiet_hours: "当前处于免打扰时段",
      model_task_unregistered: "主动联系模型任务未配置",
      contact_diagnostic_source_required: "当前没有可测试的私聊窗口或事件机会",
    };
    const failureExplanations = {
      dashboard_identity_required: "真实主动消息测试需要已登录的 Dashboard 用户身份。插件 API Key 不会被当作管理员账号使用。",
      unbound_identity: "当前登录身份还没有在这份 AstrBot 安装中完成绑定；重新打开当前管理界面后才会建立可核对的本体身份。",
      binding_revoked: "当前登录身份与本体的绑定已被撤销，测试不会自动恢复它；请由仍有核心权限的管理员重新确认。",
      binding_expired: "当前登录身份与本体的绑定已经过期；请重新登录并由仍有核心权限的管理员确认当前身份。",
      binding_not_active: "当前登录身份的本体绑定尚未到生效时间；刷新状态后再运行测试。",
      binding_rebound: "当前登录身份已经被重新绑定到其他主体；刷新页面并核对当前安装、账号与角色映射。",
      scope_installation_mismatch: "当前登录身份属于另一份 AstrBot 安装，不能读取或投递这份插件的核心配置。",
      scope_not_granted: "当前登录身份通过了 Dashboard 登录，但没有本次主动测试所需的配置范围授权。",
      core_access_required: "Dashboard 登录成功，但这个身份没有本体核心管理权限；请使用有权限的账号，或由其他管理员恢复授权。",
      authorization_denied: "当前身份无法管理主动联系配置；请由仍有核心权限的管理员检查该身份的配置授权。",
      authorization_revoked: "这项管理授权曾被撤销，系统不会在测试时自动恢复；请由另一位仍有权限的管理员重新授权。",
      authorization_expired: "这项管理授权已经过期；请由仍有核心权限的管理员重新授权后再测。",
      authorization_not_active: "这项管理授权尚未生效；刷新状态或等待授权生效后再测。",
      setup_binding_confirmation_required: "用户页的“管理员权限”决定谁是核心管理员；“开始使用”里的私聊窗口确认决定消息投递到哪扇可信窗口，两者是独立前置条件。本次失败表示窗口确认缺失：回到开始使用确认同一私聊窗口；若用户页仍显示未开启，再把目标成员设为核心用户并保存。",
      contact_attempt_budget: "这条记录来自早先的模型调用次数上限；现在仅按每日实际联系额度限制发送。",
      contact_delivery_budget: "本私聊今日实际联系额度已用完；已确认发送计联系，待回执发送暂时占位，明确未发送后释放。",
      contact_diagnostic_source_required: "关系表达类不要求固定关键词，但需要已完成的私聊窗口；事件类需要所选生产器的当前有效来源。没有可测试机会时不会调用模型。",
    };
    const skipReason = result?.skip_reason;
    const confidence = Number(result?.decision_confidence), threshold = Number(result?.silence_threshold);
    const skipDetail = skipReason === "model_decision"
      ? modelSkipReasonLabels[result?.model_skip_reason] || "模型未提供可识别的原因类别"
      : skipReason === "low_confidence" && Number.isFinite(confidence) && Number.isFinite(threshold)
        ? '模型把握度 ' + confidence.toFixed(2) + ' 低于沉默阈值 ' + threshold.toFixed(2)
        : result?.judge_reason && skipReason === "persona_review" ? result.judge_reason : "";
    const stageNames = {authorization: "Dashboard 身份与核心权限", settings: "主动联系授权", purpose: "目的开关", execution: "端到端执行",
      trusted_candidate: "测试机会", contact_admission: "主动联系准入", attempt_budget: "模型调用次数上限",
      delivery_budget: "本窗口每日实际联系额度", model_route: "主动联系模型任务", model_generation: "模型生成",
      persona_review: "人格复核", delivery: "消息投递"};
    const stageStates = {passed: "通过", failed: "失败", pending: "处理中", skipped: "未执行",
      not_required: "无需复核", not_started: "未开始", checking: "检查中", running: "运行中"};
    const stages = Array.isArray(result?.stages) ? result.stages.map(item =>
      '<li data-state="' + escapeHtml(item.state) + '"><span>' + escapeHtml(stageNames[item.stage] || item.stage) + '</span><strong>' +
      escapeHtml(stageStates[item.state] || item.state) + '</strong></li>').join("") : "";
    const failureTitle = result?.failure_title || failureTitles[result?.code] || "测试未通过";
    const failureExplanation = result?.explanation || failureExplanations[result?.code] || "";
    const hasFailureTitle = Boolean(result?.failure_title || failureTitles[result?.code]);
    const stageSuffix = result?.stage && !(result.state === "failed" && hasFailureTitle)
      ? " · " + escapeHtml(stageNames[result.stage] || result.stage) : "";
    const usage = result?.budget_usage;
    const usageDetail = usage && Number.isFinite(Number(usage.used)) && Number.isFinite(Number(usage.limit))
      ? '<p>当前额度：已用 ' + number(usage.used) + ' / ' + number(usage.limit) + ' 次（' + escapeHtml(usage.date || "今日") +
        (usage.timezone ? ' · ' + escapeHtml(usage.timezone) : '') + '）</p>' : "";
    const technicalDetails = result?.code || result?.error
      ? '<details class="maint-proactive-technical"><summary>展开技术详情</summary>' +
        (result.code ? '<code>' + escapeHtml(result.code) + '</code>' : "") +
        (result.error ? '<pre>' + escapeHtml(result.error) + '</pre>' : "") + '</details>' : "";
    const detail = result ? '<div class="maint-proactive-result" data-state="' + escapeHtml(result.state) + '">' +
      '<strong>' + escapeHtml(result.state === "passed" ? "链路完成并确认投递" : result.state === "skipped" ? "本次未投递 · " + (skipReasonLabels[skipReason] || "原因未记录") : result.state === "pending" ? "仍在处理中" : "测试未通过 · " + failureTitle) +
      stageSuffix + '</strong>' +
      (result.state === "skipped" && skipDetail ? '<p class="maint-proactive-skip-reason">原因：' + escapeHtml(skipDetail) + '</p>' : "") +
      (result.purpose_label ? '<p>目的：' + escapeHtml(result.purpose_label) + (result.variant?.label ? ' · 子类：' + escapeHtml(result.variant.label) : '') + ' · 收件人：核心管理员</p>' : "") +
      (result.state === "failed" && failureExplanation ? '<p class="maint-proactive-skip-reason">' + escapeHtml(failureExplanation) + '</p>' : "") +
      usageDetail +
      (result.delivery_state ? '<p>任务：' + escapeHtml(result.task_state || "处理中") + ' · 投递回执：' + escapeHtml(result.delivery_state) + (result.receipt_ref ? ' · ' + escapeHtml(result.receipt_ref) : '') + '</p>' : "") +
      (result.suggestion ? '<p>' + escapeHtml(result.suggestion) + '</p>' : "") +
      (stages ? '<ol>' + stages + '</ol>' : "") + technicalDetails + '</div>' : "";
    const confirmation = state.proactiveConfirming ? '<div class="maint-proactive-confirm" role="group" aria-labelledby="maint-proactive-confirm-heading"><strong id="maint-proactive-confirm-heading">确认真实投递</strong>' +
      '<p>用途：' + escapeHtml(purposeLabel) + '。确认后将调用当前角色模型，并向核心管理员私聊发送一条真实消息。该操作不可撤回；结果未知时不会自动重试。</p>' +
      '<div class="maint-proactive-confirm-actions"><button type="button" data-proactive-confirm>确认并开始投递</button><button type="button" class="maint-proactive-confirm-cancel" data-proactive-cancel>取消</button></div></div>' : "";
    const running = state.proactiveTesting ? '<div class="maint-proactive-running" role="status" aria-live="polite"><strong>正在运行完整主动消息链路</strong>' +
      '<p>正在等待模型生成、人格复核和平台投递回执。请勿重复启动；若结果未知，请先核对核心管理员私聊。</p></div>' : "";
    const notice = state.proactiveNotice ? '<p class="maint-proactive-notice" role="status" aria-live="polite">' + escapeHtml(state.proactiveNotice) + '</p>' : "";
    return '<section class="maint-proactive-test" data-focus-target="proactive-test" aria-labelledby="maint-proactive-test-heading"><div class="maint-subhead"><strong id="maint-proactive-test-heading">主动消息链路测试</strong><span>投递到核心管理员私聊</span></div>' +
      '<div class="maint-proactive-test-controls"><label>测试用途<select data-proactive-purpose ' + (state.proactiveTesting || state.proactiveConfirming ? "disabled" : "") + '>' +
      proactivePurposes.map(([key, label]) => '<option value="' + key + '"' + (state.proactivePurpose === key ? " selected" : "") + '>' + label + '</option>').join("") +
      '</select></label><button type="button" data-proactive-test ' + (state.proactiveTesting || state.proactiveConfirming ? "disabled" : "") + '>' +
      (state.proactiveTesting ? "正在运行完整链路" : state.proactiveConfirming ? "等待确认" : "运行并真实投递") + '</button></div>' +
      '<p class="maint-proactive-test-note">手动测试不占日常联系额度；关系表达类按已确认私聊窗口和用途设置验证，事件类按当前有效来源验证。没有有效测试机会时不会调用模型。确认后会真实投递到核心管理员私聊，结果未知时不会自动重试。</p>' + confirmation + running + notice + detail + '</section>';
  }

  function clearDiagnosticExpiry(target = state) {
    if (!target) return;
    if (target.diagnosticClearTimer != null) window.clearTimeout?.(target.diagnosticClearTimer);
    target.diagnosticClearTimer = null;
    target.diagnosticResultExpiresAt = 0;
  }

  function hasDiagnosticResults(target = state) {
    return Boolean(target?.proactiveTest || target?.proactiveNotice || target?.test ||
      target?.voiceTest || target?.imageTest || target?.storageTest || target?.memorySamples?.length || target?.memoryError);
  }

  function scheduleDiagnosticAutoClear(target = state, ticket = generation) {
    if (!target || target !== state || ticket !== generation) return;
    const previousExpiry = target.diagnosticResultExpiresAt;
    clearDiagnosticExpiry(target);
    if (!hasDiagnosticResults(target) || typeof window.setTimeout !== "function") return;
    const now = Date.now();
    target.diagnosticResultExpiresAt = previousExpiry > now ? previousExpiry : now + DIAGNOSTIC_RESULT_TTL_MS;
    const expectedState = target;
    target.diagnosticClearTimer = window.setTimeout(() => {
      if (ticket !== generation || state !== expectedState) return;
      expectedState.diagnosticClearTimer = null;
      clearDiagnosticResults("auto", expectedState);
    }, Math.max(1, target.diagnosticResultExpiresAt - now));
  }

  function clearDiagnosticResults(mode = "manual", target = state) {
    if (!target || target !== state) return;
    clearDiagnosticExpiry(target);
    stopMemoryDiagnostic("", {render: false, target});
    clearVoiceAudio(target);
    target.test = null;
    target.voiceTest = null;
    target.imageTest = null;
    target.storageTest = null;
    target.proactiveTest = null;
    target.proactiveNotice = "";
    target.memorySamples = [];
    target.memoryError = "";
    target.memoryComplete = false;
    target.memoryStopReason = "";
    target.diagnosticNotice = mode === "auto"
      ? "本页测试结果已自动清除（15 分钟）；运行账本没有改动。"
      : "本页测试结果已清除；运行账本没有改动。";
    cacheMaintenanceState();
    render();
  }

  function requestProactiveConfirmation() {
    if (!state || state.proactiveTesting || state.proactiveConfirming) return;
    state.proactiveConfirming = true;
    state.diagnosticNotice = "";
    state.proactiveNotice = "";
    state.proactiveTest = null;
    scheduleDiagnosticAutoClear();
    render();
  }

  function cancelProactiveConfirmation() {
    if (!state || !state.proactiveConfirming) return;
    state.proactiveConfirming = false;
    state.diagnosticNotice = "";
    state.proactiveNotice = "已取消，本次未调用模型，也未发送消息。";
    render();
  }

  async function testProactive() {
    if (!state || state.proactiveTesting || !state.proactiveConfirming) return;
    const current = state;
    const ticket = generation;
    const purpose = current.proactivePurpose;
    clearDiagnosticExpiry(current);
    current.diagnosticNotice = "";
    current.proactiveConfirming = false;
    current.proactiveTesting = true;
    current.proactiveNotice = "";
    current.proactiveTest = null;
    render();
    try {
      current.proactiveTest = await api("POST", "/setup/proactive-diagnostic", {purpose, confirm_delivery: true});
    } catch (error) {
      if (ticket !== generation || state !== current) return;
      current.proactiveTest = {state: "failed", stage: "request", code: error.code || "proactive_test_failed",
        error: error.message || String(error), suggestion: "先核对核心管理员私聊和诊断状态；确认没有送达前不要再次启动，以免重复发送。", stages: []};
    } finally {
      if (ticket === generation && state === current) {
        current.proactiveTesting = false;
        scheduleDiagnosticAutoClear(current, ticket);
        render();
      }
    }
  }

  function cacheDiagnosticRange(data) {
    if (state.period === "total") return null;
    const end = state.period === "date" ? selectedDay(data) : localDay(data.generated_at);
    if (state.period === "date") return [end, end];
    const days = state.period === "30d" ? 30 : state.period === "7d" ? 7 : 1;
    const start = new Date(end + "T00:00:00");
    start.setDate(start.getDate() - days + 1);
    return [localDay(start), end];
  }

  function cacheDiagnosticStats(data) {
    const diagnostics = data.cache_diagnostics || {};
    const range = cacheDiagnosticRange(data);
    const rows = (Array.isArray(diagnostics.daily) ? diagnostics.daily : []).filter(row => {
      const day = row.day || "";
      return !range || day >= range[0] && day <= range[1];
    });
    const fields = ["signature_calls", "compared_calls", "baseline_calls", "changed_calls",
      "high_severity_events", "medium_severity_events", "low_severity_events",
      "system_prompt_changes", "system_order_drifts", "tool_definition_changes", "tool_order_drifts",
      "model_changes", "context_resets", "context_order_drifts", "context_prefix_changes", "context_tail_trims",
      "context_availability_changes",
      "provider_changes",
      "cache_hit_comparable_changes", "cache_hit_drop_after_change_calls", "cache_hit_delta_points_total",
      "context_stats_calls", "context_text_stats_calls", "request_stats_calls",
      "context_availability_calls", "context_estimate_calls", "system_estimate_calls", "tool_estimate_calls",
      "message_estimate_calls", "request_estimate_calls",
      "context_est_tokens_total", "system_est_tokens_total", "tool_est_tokens_total",
      "message_est_tokens_total", "request_est_tokens_total", "context_materials_total",
      "request_profile_calls", "profile_context_est_tokens_total", "profile_message_est_tokens_total",
      "profile_system_est_tokens_total", "profile_tool_est_tokens_total", "profile_request_est_tokens_total",
      "context_chars_total", "context_bytes_total", "message_chars_total", "message_bytes_total",
      "deferred_materials_total", "unavailable_materials_total", "messages_total", "system_messages_total",
      "system_chars_total", "system_bytes_total", "tools_total", "tool_bytes_total",
      "images_total", "audio_total", "video_total"];
    for (const dimension of ["route", "prefix", "system", "tools", "materials", "availability"]) {
      fields.push(`${dimension}_compared_calls`, `${dimension}_stable_calls`, `${dimension}_cache_calls`,
        `${dimension}_cache_input_tokens_total`, `${dimension}_cached_input_tokens_total`);
    }
    const totals = Object.fromEntries(fields.map(key => [key, 0]));
    for (const row of rows) for (const key of fields) totals[key] += Number(row[key]) || 0;
    const events = (Array.isArray(diagnostics.recent_events) ? diagnostics.recent_events : []).filter(row => {
      const day = row.day || String(row.at || "").slice(0, 10);
      return !range || day >= range[0] && day <= range[1];
    });
    return {...totals, events, eventSampleLimit: Number(diagnostics.event_sample_limit) || 0};
  }

  function cacheAverage(total, calls) {
    if (!(Number(calls) > 0)) return "—";
    return new Intl.NumberFormat("zh-CN", {maximumFractionDigits: 1}).format((Number(total) || 0) / Number(calls));
  }

  function cacheSize(bytes) {
    if (bytes == null || !Number.isFinite(Number(bytes))) return "—";
    const value = Math.max(0, Number(bytes));
    return value >= 1024 ? (value / 1024).toFixed(1) + " KiB" : value.toFixed(0) + " B";
  }

  function cacheRateComparison(data, stats) {
    if (state.period === "total") return null;
    const days = state.period === "30d" ? 30 : state.period === "7d" ? 7 : 1;
    const end = state.period === "date" ? stats.day : localDay(data.generated_at);
    const start = new Date(end + "T00:00:00");
    start.setDate(start.getDate() - days * 2 + 1);
    const previousStart = localDay(start);
    const previousEndDate = new Date(end + "T00:00:00");
    previousEndDate.setDate(previousEndDate.getDate() - days);
    const previousEnd = localDay(previousEndDate);
    const history = Array.isArray(data.history) ? data.history : Array.isArray(data.daily) ? data.daily : [];
    const rows = history.filter(row => {
      const day = row.day || row.key || "";
      return day >= previousStart && day <= previousEnd;
    });
    if (!rows.length) return {label: days === 1 ? "较前一日" : "较前 " + days + " 日", empty: true};
    const previous = aggregateUsage(rows);
    const previousRate = previous.cache_input_tokens
      ? Math.round(previous.cached_input_tokens * 1000 / previous.cache_input_tokens) / 10 : null;
    if (stats.cacheRate == null || previousRate == null) {
      return {label: days === 1 ? "较前一日" : "较前 " + days + " 日", empty: true};
    }
    return {label: days === 1 ? "较前一日" : "较前 " + days + " 日", previousRate,
      delta: Math.round((stats.cacheRate - previousRate) * 10) / 10};
  }

  function cacheMetricTrend(data, field, label) {
    const source = Array.isArray(data.history) ? data.history : Array.isArray(data.daily) ? data.daily : [];
    const end = state.period === "date" ? selectedDay(data) : localDay(data.generated_at);
    const count = state.period === "30d" ? 30 : state.period === "7d" ? 7 : state.period === "total" ? 30 : 7;
    const byDay = new Map(source.map(row => [row.day || row.key, row]));
    const endDate = new Date(end + "T12:00:00");
    const values = [];
    for (let offset = count - 1; offset >= 0; offset -= 1) {
      const day = new Date(endDate);
      day.setDate(day.getDate() - offset);
      const key = localDay(day);
      const row = byDay.get(key);
      values.push(Math.max(0, Number(typeof field === "function" ? field(row) : row?.[field]) || 0));
    }
    const ceiling = Math.max(1, ...values);
    const points = values.map((value, index) => {
      const x = values.length === 1 ? 50 : index * 100 / (values.length - 1);
      const y = 22 - value * 19 / ceiling;
      return x.toFixed(1) + "," + y.toFixed(1);
    }).join(" ");
    return '<svg viewBox="0 0 100 24" preserveAspectRatio="none" role="img" aria-label="' +
      escapeHtml(label + "近 " + count + " 日趋势") + '"><polyline points="' + points + '"></polyline></svg>';
  }

  function cacheMetricMarkup(data, name, field, value, series, detail, isPercent = false) {
    return '<div class="maint-cache-metric" data-series="' + series + '"><span>' + name +
      '</span><strong>' + (isPercent ? percent(value) : number(value)) + '</strong><small>' +
      escapeHtml(detail) + '</small>' + cacheMetricTrend(data, field, name) + '</div>';
  }

  function cacheZipperMarkup(parts, label, emptyLabel) {
    const values = parts.map(([key, name, value]) => [key, name, Math.max(0, Number(value) || 0)]);
    const total = values.reduce((sum, part) => sum + part[2], 0);
    if (!total) return '<div class="maint-cache-zip-empty">' + escapeHtml(emptyLabel) + '</div>';
    const active = values.filter(part => part[2] > 0);
    const exactCounts = active.map(part => part[2] * 28 / total);
    const counts = exactCounts.map(Math.floor);
    const remainderOrder = exactCounts.map((value, partIndex) => partIndex)
      .sort((a, b) => (exactCounts[b] - counts[b]) - (exactCounts[a] - counts[a]));
    const extraCount = 28 - counts.reduce((sum, count) => sum + count, 0);
    for (let index = 0; index < extraCount; index += 1) counts[remainderOrder[index]] += 1;
    for (let index = 0; index < counts.length; index += 1) {
      if (counts[index] > 0) continue;
      const donor = counts.map((count, partIndex) => partIndex)
        .filter(partIndex => counts[partIndex] > 1)
        .sort((a, b) => (counts[b] - exactCounts[b]) - (counts[a] - exactCounts[a]))[0];
      if (donor !== undefined) {
        counts[donor] -= 1;
        counts[index] = 1;
      }
    }
    const usable = 1000 - (active.length - 1) * 4;
    let x = 0;
    let index = 0;
    const groups = active.map(([key, name, value], partIndex) => {
      const width = value * usable / total;
      const count = counts[partIndex];
      const cell = width / count;
      const title = name + " · " + cacheAverage(value, 1) + " Token · " + percent(value * 100 / total);
      let polygons = "";
      for (let offset = 0; offset < count; offset += 1) {
        const triangleIndex = index++;
        const up = triangleIndex % 2 === 0;
        const left = x + offset * cell;
        const points = [[left + cell / 2, up ? 2 : 22], [left + cell + 2, up ? 22 : 2],
          [left - 2, up ? 22 : 2]].map(point => point.map(value => value.toFixed(3)).join(",")).join(" ");
        polygons += '<polygon class="' + (up ? "up" : "down") + '" points="' + points +
          '" style="animation-delay:' + ((triangleIndex + 1) * 24) + 'ms"></polygon>';
      }
      x += width + 4;
      return '<g data-part="' + key + '" data-value="' + value + '"><title>' + escapeHtml(title) +
        '</title>' + polygons + '</g>';
    }).join("");
    const description = values.map(([, name, value]) => name + " " + cacheAverage(value, 1) + " Token（" +
      percent(value * 100 / total) + "）").join("，");
    return '<div class="maint-cache-zip" data-facets="28"><svg viewBox="0 0 1000 24" preserveAspectRatio="none" role="img" aria-label="' +
      escapeHtml(label + "：" + description + "；共 28 枚圆角三角形") + '"><path class="maint-cache-zip-axis" d="M0 12H1000"></path>' + groups + '</svg></div>';
  }

  function cacheControlsMarkup(today) {
    const sources = [["companion", "角色总用量"], ["conversation", "对话合计"], ["astrbot_core", "核心回复"], ["legacy_conversation", "历史 V2"], ["plugin", "插件任务"]].map(([key, label]) =>
      '<button type="button" data-token-source="' + key + '" aria-pressed="' + (state.tokenSource === key) +
      '" class="' + (state.tokenSource === key ? "is-active" : "") + '">' + label + '</button>').join("");
    return '<div class="maint-cache-filters"><div class="maint-cache-source-control"><span>当前角色来源</span><div class="maint-periods" role="group" aria-label="缓存诊断来源">' +
      sources + '</div></div>' + periodControlsMarkup(today, "缓存诊断时间范围") + '</div>';
  }

  function cacheMarkup(stats, data) {
    const noTelemetry = stats.cacheTelemetryCalls === 0;
    const noHitSamples = stats.cacheCalls === 0;
    const recentLow = recentRows(data).filter(row => row.cached_input_tokens != null && row.input_tokens != null &&
      row.cache_hit_rate != null && Number.isFinite(Number(row.cache_hit_rate)) &&
      Number(row.input_tokens) >= 1000 && Number(row.cache_hit_rate) < 20);
    const lowSamples = recentLow.slice(0, 5);
    const providers = dimensions(data, stats, "providers").filter(row => row.cache_telemetry_calls || row.cache_reported_calls).slice(0, 5);
    const diagnostic = cacheDiagnosticStats(data);
    const hitComparison = cacheRateComparison(data, stats);
    const tokenParts = [["hit", "缓存命中", stats.cached], ["write", "缓存写入", stats.creation],
      ["miss", "普通输入", stats.other], ["output", "输出", stats.output]];
    const tokenTotal = tokenParts.reduce((sum, part) => sum + part[2], 0);
    const legend = tokenParts.map(([key, label, value]) =>
      fmt('<div data-part="{{0}}"><span><i></i>{{1}} <b>{{2}}</b></span><strong>{{3}}</strong><small>Token</small></div>',
        key, label, percent(tokenTotal > 0 ? value * 100 / tokenTotal : null), number(value))).join("");
    const providerRows = providers.length ? '<div class="maint-cache-table-wrap" tabindex="0" role="region" aria-label="Provider 缓存表现表"><table class="maint-cache-table maint-cache-provider-table"><thead><tr><th scope="col">Provider</th><th scope="col">命中率</th><th scope="col">可计算确认</th><th scope="col">写入 Token</th></tr></thead><tbody>' + providers.map(row => {
      const rate = Number(row.cache_input_tokens) > 0 ? Number(row.cached_input_tokens) * 100 / Number(row.cache_input_tokens) : null;
      return fmt('<tr><td title="{{0}}">{{0}}</td><td><strong>{{1}}</strong><i class="maint-cache-mini-meter" aria-hidden="true"><i style="--cache-share:{{2}}%"></i></i></td><td>{{3}} 次</td><td>{{4}}</td></tr>',
        escapeHtml(row.key), percent(rate), Math.max(0, Math.min(100, rate || 0)),
        number(row.cache_reported_calls), number(row.cache_creation_tokens));
    }).join("") + '</tbody></table></div>' :
      '<div class="maint-empty"><span class="maint-empty-mark">—</span><p>当前范围没有 Provider 缓存明细。</p></div>';
    const lowRows = lowSamples.length ? '<div class="maint-cache-table-wrap" tabindex="0" role="region" aria-label="低命中调用表"><table class="maint-cache-table maint-cache-low-table"><thead><tr><th scope="col">任务 / Provider</th><th scope="col">输入 Token</th><th scope="col">命中率</th><th scope="col">时间</th><th scope="col">操作</th></tr></thead><tbody>' + lowSamples.map(row =>
      fmt('<tr><td><strong>{{0}}</strong><small title="{{1}}">{{1}}</small></td><td>{{3}}</td><td><strong class="maint-cache-low-rate">{{4}}</strong></td><td>{{2}}</td><td><button type="button" class="maint-text-action" data-set-call-filter="cache-low">查看调用</button></td></tr>',
        escapeHtml(taskName(row.kind)), escapeHtml(row.provider || "Provider"), escapeHtml(time(row.at)), number(row.input_tokens), percent(Number(row.cache_hit_rate)))).join("") + '</tbody></table></div>' :
      '<div class="maint-empty"><span class="maint-empty-mark">↗</span><p>' + (stats.cacheLowCalls > 0
        ? "本周期精确统计到 " + number(stats.cacheLowCalls) + " 条；近期 80 条调用样本没有覆盖这些记录。"
        : noTelemetry ? "收到缓存用量后，这里会列出低命中请求。" : "当前范围没有符合条件的低命中调用。") + '</p></div>';
    const note = (data.execution_path === "astrbot_core"
      ? "核心请求签名来自实际 Agent 本轮上下文与工具；SDK 默认缓存 0 不作为缓存实报。" : "") + (noTelemetry
      ? stats.calls === 0 ? "这个范围没有模型调用。收到 Provider 缓存确认后，会显示真实读写与命中数据。" : "当前范围没有缓存字段确认；命中率只在 Provider 同时报告输入量和缓存命中量时计算。"
      : noHitSamples ? "收到缓存相关字段，但没有可计算命中率的完整确认；写入量仍按 Provider 实报展示。"
        : "命中率与 Token 均来自 Provider 回执；低命中是排查线索，不等于缓存一定失效。");
    const changeLabels = {provider_change: "Provider 切换", system_prompt_change: "system 指令变化", system_order_drift: "system 顺序变化",
      tool_definition_change: "工具定义变化", tool_order_drift: "工具顺序变化", model_change: "模型切换",
      context_reset: "参考上下文骤减", context_order_drift: "参考材料顺序变化",
      context_prefix_change: "参考材料前缀变化", context_tail_trim: "参考材料尾部收缩",
      context_availability_change: "资料可用性变化"};
    const eventSnapshot = snapshot => {
      if (!snapshot) return {context: "上下文快照不可用", request: "请求规模不可用"};
      const estimate = value => value == null ? "未采集估算" : "约 " + number(value) + " Token";
      const context = "材料 " + (snapshot.context_materials == null ? "—" : number(snapshot.context_materials) + " 条") +
        " · " + (snapshot.context_chars == null ? "—" : number(snapshot.context_chars) + " 字") +
        " · " + cacheSize(snapshot.context_bytes) + " · " + estimate(snapshot.context_est_tokens) +
        " · 延后 " + (snapshot.deferred_materials == null ? "—" : number(snapshot.deferred_materials)) +
        " · 不可用 " + (snapshot.unavailable_materials == null ? "—" : number(snapshot.unavailable_materials));
      const request = "消息正文（不含参考材料） " + estimate(snapshot.message_est_tokens) + " · 完整请求 " + estimate(snapshot.request_est_tokens) +
        " · system " + estimate(snapshot.system_est_tokens) + " · 工具 " + estimate(snapshot.tool_est_tokens) +
        " · 请求消息 " + (snapshot.messages == null ? "—" : number(snapshot.messages)) + " 条" +
        " · 媒体 " + [snapshot.images, snapshot.audio, snapshot.video].map(value => value == null ? "—" : number(value)).join("/");
      return {context, request};
    };
    const fingerprintFields = [["model_fingerprint", "model"], ["system_fingerprint", "system"],
      ["tools_fingerprint", "tools"], ["context_fingerprint", "context"],
      ["availability_fingerprint", "availability"]];
    const eventRows = diagnostic.events.slice(0, 20).map(row => {
      const before = eventSnapshot(row.before);
      const after = eventSnapshot(row.after);
      const providerLabel = row.previous_provider && row.previous_provider !== row.provider
        ? row.previous_provider + " → " + (row.provider || "Provider") : row.provider || "Provider";
      const changes = Array.isArray(row.changes) ? row.changes : [];
      const chips = changes.map(change => '<i title="' + escapeHtml(change) + '">' +
        escapeHtml(changeLabels[change] || change) + '</i>').join("");
      const severity = ["high", "medium", "low"].includes(row.severity) ? row.severity : "low";
      const fingerprints = fingerprintFields.filter(([key]) => row.before?.[key] != null && row.after?.[key] != null &&
        row.before[key] !== row.after[key]).map(([key, label]) => label + " " + row.before[key] + " → " + row.after[key]).join(" · ") || "无签名指纹变化";
      const hitStatus = row.cache_hit_rate == null ? "Provider 未报告本次命中率" : row.cache_hit_delta == null
          ? row.previous_provider && row.previous_provider !== row.provider
            ? "Provider 已切换 · 不跨 Provider 比较 · 本次命中 " + row.cache_hit_rate + "%"
            : (row.changes || []).includes("model_change")
              ? "模型已切换 · 不跨模型比较 · 本次命中 " + row.cache_hit_rate + "%"
            : "本次缓存命中 " + row.cache_hit_rate + "% · 前次无可比回执"
          : "本次缓存命中 " + row.cache_hit_rate + "% · 较前次 " +
            (row.cache_hit_delta > 0 ? "+" : "") + row.cache_hit_delta + " 个百分点";
      return fmt('<tr class="maint-cache-signature-event" data-severity="{{0}}"><td><span class="maint-cache-change-list">{{3}}</span><small>{{11}}</small></td><td><b class="maint-cache-severity" data-severity="{{0}}" title="{{0}}">{{12}}</b></td><td title="{{4}}"><code>{{13}}</code></td><td><time>{{1}}</time></td><td><details class="maint-cache-event-detail"><summary><strong>{{9}}</strong><small>{{2}}</small><small>{{10}}</small><span class="maint-cache-event-toggle">查看快照</span></summary><div><p>前 {{5}} → 后 {{6}}</p><p>前 {{7}} → 后 {{8}}</p></div></details></td></tr>',
        severity, escapeHtml(time(row.at)), escapeHtml(providerLabel), chips,
        escapeHtml(row.session_ref ? "会话 " + row.session_ref : "会话未知"),
        escapeHtml(before.context), escapeHtml(after.context),
        escapeHtml(before.request), escapeHtml(after.request), escapeHtml(fingerprints), escapeHtml(hitStatus),
        escapeHtml(taskName(row.kind)), {high: "高", medium: "中", low: "低"}[severity], escapeHtml(row.session_ref || "未知"));
    }).join("");
    const noSignatures = diagnostic.signature_calls === 0;
    const signatureState = noSignatures ? "waiting" : diagnostic.compared_calls === 0 ? "sampling"
      : diagnostic.changed_calls ? "warn" : "ok";
    const signatureStatus = noSignatures ? stats.calls ? "升级后尚未采集" : "等待首次调用"
      : diagnostic.compared_calls === 0 ? "已采集 " + number(diagnostic.signature_calls) + " 次 · 等待可比调用"
        : diagnostic.changed_calls ? number(diagnostic.changed_calls) + " 次调用观察到变化"
          : "已比较 " + number(diagnostic.compared_calls) + " 次 · 暂未见结构变化";
    const eventEmpty = diagnostic.changed_calls === 0
      ? '<div class="maint-empty"><span class="maint-empty-mark">✓</span><p>' + (noSignatures
        ? "签名从本次升级后开始采集，旧调用不会回填。"
        : diagnostic.compared_calls === 0 ? "已有首个基线；后续同会话调用会开始比较。" : "所选周期暂未观察到结构变化。") + '</p></div>'
      : '<div class="maint-empty"><span class="maint-empty-mark">…</span><p>所选周期检测到变化，但最近 ' + number(diagnostic.eventSampleLimit) + ' 条事件样本未覆盖这些日期。</p></div>';
    const systemChanges = diagnostic.system_prompt_changes + diagnostic.system_order_drifts;
    const toolChanges = diagnostic.tool_definition_changes + diagnostic.tool_order_drifts;
    const contextOrderChanges = diagnostic.context_order_drifts + diagnostic.context_prefix_changes;
    const contextCountAvg = cacheAverage(diagnostic.context_materials_total, diagnostic.context_stats_calls);
    const contextCharsAvg = cacheAverage(diagnostic.context_chars_total, diagnostic.context_text_stats_calls);
    const contextBytesAvg = diagnostic.context_stats_calls
      ? cacheSize(diagnostic.context_bytes_total / diagnostic.context_stats_calls) : "—";
    const contextEstimateAvg = cacheAverage(diagnostic.profile_context_est_tokens_total, diagnostic.request_profile_calls);
    const systemBytesAvg = diagnostic.request_stats_calls
      ? cacheSize(diagnostic.system_bytes_total / diagnostic.request_stats_calls) : "—";
    const systemEstimateAvg = cacheAverage(diagnostic.profile_system_est_tokens_total, diagnostic.request_profile_calls);
    const systemMessagesAvg = cacheAverage(diagnostic.system_messages_total, diagnostic.request_stats_calls);
    const toolsAvg = cacheAverage(diagnostic.tools_total, diagnostic.request_stats_calls);
    const toolBytesAvg = diagnostic.request_stats_calls
      ? cacheSize(diagnostic.tool_bytes_total / diagnostic.request_stats_calls) : "—";
    const toolEstimateAvg = cacheAverage(diagnostic.profile_tool_est_tokens_total, diagnostic.request_profile_calls);
    const messageEstimateAvg = cacheAverage(diagnostic.profile_message_est_tokens_total, diagnostic.request_profile_calls);
    const requestEstimateAvg = cacheAverage(diagnostic.profile_request_est_tokens_total, diagnostic.request_profile_calls);
    const messageCharsAvg = cacheAverage(diagnostic.message_chars_total, diagnostic.message_estimate_calls);
    const messageBytesAvg = diagnostic.message_estimate_calls
      ? cacheSize(diagnostic.message_bytes_total / diagnostic.message_estimate_calls) : "—";
    const deferredAvg = cacheAverage(diagnostic.deferred_materials_total, diagnostic.context_availability_calls);
    const unavailableAvg = cacheAverage(diagnostic.unavailable_materials_total, diagnostic.context_availability_calls);
    const cacheHitDeltaAvg = diagnostic.cache_hit_comparable_changes
      ? cacheAverage(diagnostic.cache_hit_delta_points_total, diagnostic.cache_hit_comparable_changes) : "—";
    const stabilityCard = (label, dimension) => {
      const inputTokens = diagnostic[dimension + "_cache_input_tokens_total"];
      const cachedTokens = diagnostic[dimension + "_cached_input_tokens_total"];
      const rate = inputTokens > 0 ? cachedTokens * 100 / inputTokens : null;
      const changes = {prefix: diagnostic.changed_calls,
        route: diagnostic.provider_changes + diagnostic.model_changes,
        system: systemChanges, tools: toolChanges, materials: contextOrderChanges,
        availability: diagnostic.context_availability_changes}[dimension];
      return '<div class="maint-cache-stability" data-dimension="' + dimension + '"><span>' + label +
        '</span><strong>' + percent(rate) + '</strong><small>稳定 ' + number(diagnostic[dimension + "_stable_calls"]) +
        ' / 可判定 ' + number(diagnostic[dimension + "_compared_calls"]) + ' · 命中回执 ' +
        number(diagnostic[dimension + "_cache_calls"]) + ' 次</small><i class="maint-cache-mini-meter" aria-hidden="true"><i style="--cache-share:' +
        Math.max(0, Math.min(100, rate || 0)) + '%"></i></i><small>观察到变化 ' + number(changes) + ' 次</small></div>';
    };
    const messagesAvg = cacheAverage(diagnostic.messages_total, diagnostic.request_stats_calls);
    const mediaAvg = ["images_total", "audio_total", "video_total"].map(key =>
      cacheAverage(diagnostic[key], diagnostic.request_stats_calls)).join(" / ");
    const contextCountLabel = contextCountAvg === "—" ? "—" : contextCountAvg + " 条";
    const toolsLabel = toolsAvg === "—" ? "—" : toolsAvg + " 个";
    const messagesLabel = messagesAvg === "—" ? "—" : messagesAvg + " 条消息";
    const tokenEstimateLabel = value => value === "—" ? "—" : "约 " + value + " Token";
    const requestProfileTotal = diagnostic.profile_request_est_tokens_total;
    const contextShare = total => requestProfileTotal > 0 ? percent(total * 100 / requestProfileTotal) : "—";
    const comparisonText = !hitComparison ? "累计范围不提供周期对比"
      : hitComparison.empty ? hitComparison.label + "无可计算缓存回执"
        : hitComparison.label + " " + (hitComparison.delta > 0 ? "+" : "") + hitComparison.delta.toFixed(1) +
          ' 个百分点 · 前期 ' + percent(hitComparison.previousRate);
    const profileMean = field => diagnostic.request_profile_calls > 0 ? diagnostic[field] / diagnostic.request_profile_calls : 0;
    const contextParts = [["materials", "参考材料", profileMean("profile_context_est_tokens_total")],
      ["messages", "消息上下文", profileMean("profile_message_est_tokens_total")],
      ["system", "system 消息", profileMean("profile_system_est_tokens_total")],
      ["tools", "工具定义", profileMean("profile_tool_est_tokens_total")]];
    const contextCard = (key, label, average, total, detail, extra = "") => {
      const share = requestProfileTotal > 0 ? Math.max(0, Math.min(100, total * 100 / requestProfileTotal)) : 0;
      return '<div data-part="' + key + '"><span>' + label + '<b>' + contextShare(total) + '</b></span><strong title="' +
        tokenEstimateLabel(average) + '">' + tokenEstimateLabel(average) +
        '</strong><i class="maint-cache-mini-meter" aria-hidden="true"><i style="--cache-share:' + share +
        '%"></i></i><small>' + detail + '</small>' + (extra ? '<small>' + extra + '</small>' : "") + '</div>';
    };
    const eventChangeCounts = [
      ["provider_change", "provider_changes"], ["model_change", "model_changes"],
      ["system_prompt_change", "system_prompt_changes"], ["system_order_drift", "system_order_drifts"],
      ["tool_definition_change", "tool_definition_changes"], ["tool_order_drift", "tool_order_drifts"],
      ["context_reset", "context_resets"], ["context_order_drift", "context_order_drifts"],
      ["context_prefix_change", "context_prefix_changes"], ["context_tail_trim", "context_tail_trims"],
      ["context_availability_change", "context_availability_changes"],
    ].map(([kind, field]) => ({label: changeLabels[kind], count: Number(diagnostic[field]) || 0}))
      .filter(item => item.count > 0).sort((a, b) => b.count - a.count).slice(0, 3);
    const eventChangeMarkup = eventChangeCounts.length
      ? eventChangeCounts.map(item => '<span><i>' + escapeHtml(item.label) + '</i><b>' + number(item.count) + '</b></span>').join("")
      : '<span class="is-empty">所选范围暂无变化标签</span>';
    const eventOverview = '<div class="maint-cache-event-overview" aria-label="缓存结构变化概况">' +
      '<div class="maint-cache-event-overview-item"><span>本期结构变化</span><strong>' + number(diagnostic.changed_calls) +
      '<small>次调用</small></strong></div>' +
      '<div class="maint-cache-event-overview-item"><span>事件等级</span><div class="maint-cache-event-severity-counts">' +
      [['high', '高', diagnostic.high_severity_events], ['medium', '中', diagnostic.medium_severity_events],
        ['low', '低', diagnostic.low_severity_events]].map(([key, label, value]) =>
          '<b data-severity="' + key + '">' + label + ' ' + number(value) + '</b>').join("") + '</div></div>' +
      '<div class="maint-cache-event-overview-item"><span>变化后命中率</span><strong>' +
      (diagnostic.cache_hit_comparable_changes ? number(diagnostic.cache_hit_drop_after_change_calls) + ' / ' +
        number(diagnostic.cache_hit_comparable_changes) : '—') + '<small>' +
      (diagnostic.cache_hit_comparable_changes ? '次可比事件下降' : '暂无同路由可比回执') + '</small></strong></div>' +
      '<div class="maint-cache-event-breakdown"><span>常见变化标签</span>' + eventChangeMarkup + '</div>' +
      '<small class="maint-cache-event-overview-note">一条调用可包含多个变化标签；命中率变化只比较同模型、同 Provider 的相邻回执。</small></div>';
    const eventMarkup = '<section class="maint-cache-section"><div class="maint-subhead"><strong><i data-lucide="scan-search"></i>缓存结构变化事件</strong><span>本期 ' +
      number(diagnostic.changed_calls) + ' 次 · 最近显示 ' + Math.min(20, diagnostic.events.length) + ' 条 · 样本池 ' +
      number(diagnostic.eventSampleLimit) + ' 条</span></div>' + eventOverview +
      (eventRows ? '<div class="maint-cache-table-wrap" tabindex="0" role="region" aria-label="缓存结构变化事件表"><table class="maint-cache-table maint-cache-event-table"><thead><tr><th scope="col">变化类型 / 任务</th><th scope="col">严重度</th><th scope="col">会话</th><th scope="col">时间</th><th scope="col">前后变化 / Provider</th></tr></thead><tbody>' + eventRows + '</tbody></table></div>' : eventEmpty) +
      '<small class="maint-cache-signature-note">仅保存 SHA-256 摘要、体量和估算值，不保存提示词正文；事件只显示摘要前 8 位。</small></section>';
    const ringRate = Math.max(0, Math.min(100, stats.cacheRate || 0));
    const coverageTrend = row => {
      const known = Math.max(0, (Number(row?.calls) || 0) - (Number(row?.unconfirmed_calls) || 0));
      return known ? (Number(row.cache_reported_calls) || 0) * 100 / known : 0;
    };
    return '<section class="maint-cache-panel" aria-labelledby="maint-cache-heading"><div class="maint-panel-heading"><div><span class="maint-eyebrow">缓存 / 02</span><h3 id="maint-cache-heading">缓存诊断</h3><p class="maint-scope-note">' + escapeHtml(localSourceNames[state.tokenSource] || "角色总用量") + '</p></div>' +
      fmt('<span class="maint-cache-chip" data-state="{{0}}">{{1}}</span></div>',
        noTelemetry || noHitSamples || stats.cacheRate == null ? "unknown" : stats.cacheRate < 40 ? "warn" : "ok",
        noTelemetry ? "等待确认" : stats.cacheRate == null ? "暂无命中率" : percent(stats.cacheRate) + " 命中") + cacheControlsMarkup(localDay(data.generated_at)) +
      '<div class="maint-cache-top"><section class="maint-cache-section"><div class="maint-subhead"><strong><i data-lucide="circle-gauge"></i>缓存命中率总览</strong><span>按输入 Token 加权</span></div><div class="maint-cache-rate-body"><div class="maint-cache-donut" role="img" aria-label="' +
      escapeHtml(stats.cacheRate == null ? "暂无可计算命中率" : "缓存命中率 " + percent(stats.cacheRate)) +
      '"><svg viewBox="0 0 132 132" aria-hidden="true"><defs><linearGradient id="maint-cache-rate-gradient" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="var(--maint-token-series-1)"></stop><stop offset="52%" stop-color="var(--maint-token-series-2)"></stop><stop offset="100%" stop-color="var(--maint-token-series-3)"></stop></linearGradient></defs><circle class="maint-cache-ring-track" cx="66" cy="66" r="52"></circle><circle class="maint-cache-ring-fill" cx="66" cy="66" r="52" pathLength="100" stroke="url(#maint-cache-rate-gradient)" stroke-dasharray="' + ringRate + ' 100" style="--cache-rate:' + ringRate + '" stroke-linecap="round"' +
      (ringRate ? '' : ' visibility="hidden"') + ' transform="rotate(-90 66 66)"></circle></svg><div><strong>' + percent(stats.cacheRate) +
      '</strong><span>缓存命中率</span></div></div><div class="maint-cache-rate-copy"><span>有效输入缓存命中</span><div><strong>' + number(stats.cached) +
      '</strong><span> / ' + number(stats.cacheInput) + '</span></div><i class="maint-cache-mini-meter" aria-hidden="true"><i style="--cache-share:' + ringRate +
      '%"></i></i><small class="maint-cache-period-comparison" data-direction="' + (!hitComparison || hitComparison.empty ? "unknown" : hitComparison.delta >= 0 ? "up" : "down") + '">' +
      escapeHtml(comparisonText) + '</small><small>仅统计有可计算缓存回执的输入</small></div></div></section>' +
      '<section class="maint-cache-section"><div class="maint-subhead"><strong><i data-lucide="chart-no-axes-combined"></i>缓存读写与确认</strong><span>按日趋势</span></div><div class="maint-cache-metrics">' +
      cacheMetricMarkup(data, "缓存读取", "cached_input_tokens", stats.cached, "cached", "Provider 实报 Token") +
      cacheMetricMarkup(data, "缓存写入", "cache_creation_tokens", stats.creation, "write", "Provider 实报 Token") +
      cacheMetricMarkup(data, "普通输入", "cache_other_input_tokens", stats.other, "other", "缓存回执内未命中 Token") +
      cacheMetricMarkup(data, "确认覆盖", coverageTrend, stats.cacheCoverage, "coverage", number(stats.cacheCalls) + " / " + number(stats.knownCalls) + " 次已确认调用", true) +
      '</div></section></div><p class="maint-cache-note">' + escapeHtml(note) + '</p>' +
      '<section class="maint-cache-section maint-cache-composition-section"><div class="maint-subhead"><strong><i data-lucide="chart-column-stacked"></i>Token 构成</strong><span>缓存回执输入与已确认输出</span></div><div class="maint-cache-composition-total"><strong>' + number(tokenTotal) +
      '</strong><span>可分解 Token</span><small>可分解输入 <b>' + number(tokenTotal - stats.output) + '</b> · 输出 <b>' + number(stats.output) + '</b></small></div>' +
      cacheZipperMarkup(tokenParts, "Token 构成拉链图", "收到用量后显示 Token 构成") + '<div class="maint-cache-zip-legend">' + legend +
      '</div><small class="maint-cache-axis-note">输入分段来自缓存回执；未提供缓存字段的输入不计入构成。</small></section>' +
      '<div class="maint-cache-split"><section class="maint-cache-section"><div class="maint-subhead"><strong><i data-lucide="server"></i>Provider 缓存表现</strong><span>最多显示 5 个</span></div>' + providerRows +
      '</section><section class="maint-cache-section"><div class="maint-subhead"><strong><i data-lucide="triangle-alert"></i>低命中调用</strong><span>本期 ' + number(stats.cacheLowCalls) + ' 条</span></div><p class="maint-cache-table-note">输入 ≥ 1,000 Token、命中率低于 20% · 近期样本最多显示 5 条</p>' + lowRows +
      '</section></div><section class="maint-cache-section maint-cache-caveat" data-state="' + signatureState + '"><div class="maint-cache-signature-head"><span><i data-lucide="fingerprint"></i>请求前缀签名</span><strong>' + escapeHtml(signatureStatus) + '</strong><small>按相同会话与任务比较；各稳定命中率按该维度与前次相同的调用统计</small></div>' +
      '<div class="maint-cache-signature-grid">' + stabilityCard("完整前缀", "prefix") + stabilityCard("服务路由", "route") +
      stabilityCard("system 前缀", "system") + stabilityCard("工具定义", "tools") + stabilityCard("参考材料签名", "materials") +
      stabilityCard("资料可用性", "availability") +
      '<div><span>上下文重置</span><strong>' + number(diagnostic.context_resets) + ' 次</strong></div><div><span>材料尾部收缩</span><strong>' + number(diagnostic.context_tail_trims) + ' 次</strong><small>材料前缀 / 顺序变化 ' + number(contextOrderChanges) + ' 次</small></div><div><span>变化后命中率</span><strong>' + number(diagnostic.cache_hit_drop_after_change_calls) + ' 次下降</strong><small>' + number(diagnostic.cache_hit_comparable_changes) + ' 个同模型、同 Provider 可比事件 · 均值 ' + cacheHitDeltaAvg + ' 个百分点</small></div></div>' +
      '<details class="maint-cache-method"><summary>稳定命中率统计口径</summary><p>稳定命中率只统计同一会话与任务内、该维度前后可比较且维度未变、Provider 与模型也未变、Provider 同时报输入与缓存命中的请求，并按输入 Token 加权；完整前缀要求服务路由、system、工具、参考材料与资料可用性均未变。这是请求关联统计，不证明某一维度导致命中或失效。</p></details></section>' +
      eventMarkup +
      '<section class="maint-cache-section maint-cache-context-section"><div class="maint-subhead"><strong><i data-lucide="layers"></i>请求上下文统计</strong><span>完整采集 ' + number(diagnostic.request_profile_calls) + ' 次 · 单次请求均值</span></div>' +
      cacheZipperMarkup(contextParts, "请求上下文拉链图", diagnostic.request_profile_calls > 0 ? "已采集请求的文本估算为 0 Token" : "等待完整采集的请求剖面") +
      '<div class="maint-cache-axis-note"><span>各段文本估算占比</span><span>平均合计 ' + tokenEstimateLabel(requestEstimateAvg) + '</span></div>' +
      '<div class="maint-cache-context-stats" aria-label="请求上下文统计">' +
      contextCard("materials", "参考材料 / 请求", contextEstimateAvg, diagnostic.profile_context_est_tokens_total,
        contextCountLabel + ' · 平均 ' + contextCharsAvg + ' 字 · ' + contextBytesAvg, '延后 ' + deferredAvg + ' · 不可用 ' + unavailableAvg) +
      contextCard("messages", "消息上下文", messageEstimateAvg, diagnostic.profile_message_est_tokens_total,
        '平均 ' + messagesLabel + ' · ' + messageCharsAvg + ' 字 · ' + messageBytesAvg, '不含参考材料文本') +
      contextCard("system", "system 消息", systemEstimateAvg, diagnostic.profile_system_est_tokens_total,
        '平均 ' + systemMessagesAvg + ' 条 · ' + systemBytesAvg) +
      contextCard("tools", "工具定义", toolEstimateAvg, diagnostic.profile_tool_est_tokens_total,
        '平均 ' + toolsLabel + ' · 结构体量 ' + toolBytesAvg) +
      contextCard("total", "完整请求", requestEstimateAvg, requestProfileTotal,
        '前四项文本估算合计', '图像 / 音频 / 视频 ' + mediaAvg + ' 项，数量单列') +
      '</div><p class="maint-cache-table-note">材料文本 + 不含材料文本的消息估算 + system + 工具文本。各项按同一批请求合计；含消息包装结构估算，不含多模态载荷 Token。估算不替代 Provider 实际用量。</p></section></section>';
  }

  function balanceMarkup() {
    const balance = state.balance || {};
    const providerLabel = balance.provider_label || "NewAPI";
    const title = providerLabel === "服务余额" ? "服务账户余额" : providerLabel + " 账户余额";
    const status = !state.balanceLoaded ? "尚未查询" : !balance.configured ? "未配置"
      : balance.available ? "已连接" : balance.state === "configuration_invalid" ? "配置无效" : "查询失败";
    const value = balance.available
      ? balance.amount == null || String(balance.amount).trim() === "" ? "未提供余额" : balanceAmount(balance.amount) + " " + (balance.currency_label || "额度")
      : status;
    const detail = !state.balanceLoaded ? "读取已配置的模型服务余额；查询结果与 V2 本地角色预算分开显示。"
      : !balance.configured ? "未配置余额端点；NewAPI 需要单独的用户访问令牌。"
      : balance.available ? "查询于 " + time(balance.observed_at) + "；不会并入 V2 本地角色预算。"
      : balance.state === "configuration_invalid" ? "余额查询配置无效，请检查余额查询配置。"
      : "查询失败，请检查 HTTPS 地址、访问令牌与额度单位配置。";
    return '<div class="maint-balance-availability" data-state="' + (balance.available ? "ok" : balance.configured ? "warn" : "unknown") + '">' +
      '<div><i data-lucide="wallet-cards"></i><span><small>ACCOUNT BALANCE</small><strong>' + escapeHtml(title) + '</strong></span></div>' +
      '<p><b>' + escapeHtml(value) + '</b><span>' + escapeHtml(detail) + '</span></p>' +
      '<button class="maint-balance-query" type="button" data-balance-refresh ' + (state.balanceLoading ? "disabled" : "") +
      ' aria-label="查询 ' + escapeHtml(providerLabel) + ' 账户余额" title="查询账户余额"><i data-lucide="refresh-cw"></i><span>' +
      (state.balanceLoading ? "查询中" : state.balanceLoaded ? "重新查询" : "查询余额") + '</span></button></div>';
  }

  function tokenDashboardControlsMarkup(today, roleData) {
    /* 工具栏之后紧跟的统计区就是「统计来源」这一组 tab 的受控面板。 */
    return '<div class="maint-token-toolbar"><div class="maint-token-source-control"><span>统计来源</span><div class="maint-source-tabs" role="tablist" aria-label="Token 统计来源">' + sourceTabsMarkup() + '</div></div>' +
      periodControlsMarkup(today, "Token 统计时间范围") + '</div><div id="maint-token-source-panel" role="tabpanel" aria-label="Token 统计明细"><div class="maint-token-calibration-row"><span><i data-lucide="shield-check"></i>Provider 已确认用量；未知调用不估算，外部账本独立统计。</span>' +
      '<details class="maint-source-details"><summary><i data-lucide="info"></i><span>统计口径</span></summary><div class="maint-source-grid"><article data-state="active"><span>纳入统计</span><strong>当前角色的核心回复与插件任务</strong><small>核心观测、历史 V2 回复和插件任务分列；对话合计与角色总量各计一次</small></article><article data-state="limited"><span>其他来源</span><strong>AstrBot 全部会话与外部插件</strong><small>全局统计未接入；外部来源不并入角色预算</small></article><article data-state="limited"><span>可用性</span><strong>实际多轮与最后响应分列</strong><small>核心费用未知；SDK 默认缓存 0 不等同缓存实报</small></article></div></details>' +
      '<details class="maint-token-source-comparison"><summary>来源同周期对照</summary>' + sourceComparisonMarkup(roleData) + '</details></div></div>';
  }

  function localTokenDashboardMarkup(data, stats, label, today, periodComparison, providerRows, taskRows, chartData) {
    const budget = data.budget || {};
    const limit = budget.hard_limit_enabled === true ? Math.max(0, Number(budget.limit_tokens) || 0) : 0;
    const spent = Math.max(0, Number(budget.spent_tokens) || 0);
    const held = Math.max(0, Number(budget.held_tokens) || 0);
    const budgetPct = limit ? Math.min(100, Math.round((spent + held) * 100 / limit)) : 0;
    const softLimit = Math.max(0, Number(budget.soft_limit_tokens) || 0);
    const softEnabled = Boolean(budget.soft_limit_enabled && softLimit);
    const softPct = softEnabled ? Math.min(100, Math.round((spent + held) * 100 / softLimit)) : 0;
    const softState = !softEnabled ? "off" : spent + held >= softLimit ? "warn" : "ok";
    const endDay = state.period === "date" ? stats.day : today;
    const activeDetail = ["daily", "providers", "tasks", "recent"].includes(state.tokenDetailView) ? state.tokenDetailView : "daily";
    const coreCoverage = data.execution_path === "astrbot_core" ? data.coverage : data.usage_sources?.astrbot_core?.coverage;
    const priceNote = data.execution_path === "astrbot_core" ? "核心费用未提供" : stats.cost == null ? "费用未观测" :
      cost(stats.cost) + " 已计价部分" + (data.cost_availability === "partial" ? " · 核心费用未提供" : "");
    const observationNote = coreCoverage ? '<p class="maint-scope-note">核心累计观测 ' + number(coreCoverage.turns) + ' 回合 · ' +
      number(coreCoverage.observed_requests) + ' 次实际请求 · ' + number(coreCoverage.fallback_usage_observations) +
      ' 条汇总或最后响应。' + (coreCoverage.partial_request_observation_turns ? number(coreCoverage.partial_request_observation_turns) +
        ' 回合仅观测部分请求，含工具参数重查。' : "") + '无法观测的多轮次数保持未知，核心费用不计为零。</p>' : "";
    return '<div class="maint-token-hero maint-token-summary maint-token-dashboard-summary">' + balanceMarkup() + '<div class="maint-usage-total maint-token-summary-card"><span>' + escapeHtml(label) + ' · Provider 已确认 Token</span><strong>' + number(stats.tokens) + '</strong><small>Token</small><div class="maint-usage-subline"><span>已确认 ' + number(stats.knownCalls) + ' 次</span><i></i><span>未确认 ' + number(stats.unconfirmed) + ' 次</span><i></i><span>' + priceNote + '</span></div></div>' + budgetMarkup(budget, spent, held, limit, budgetPct, softEnabled, softPct, softLimit, softState) + '</div>' + observationNote +
      '<details class="maint-token-drill"><summary>更多用量指标与周期对比</summary><div class="maint-token-drill-content">' + periodComparison + '<div class="maint-token-kpis"><div><span>输入 / 输出</span><strong>' + number(stats.input) + ' / ' + number(stats.output) + '</strong></div><div><span>平均每次</span><strong>' +
      (stats.calls ? number(stats.tokens / stats.calls) : "—") + ' Token</strong></div><div><span>平均延迟</span><strong>' + (stats.latency == null ? "—" : number(stats.latency) + " ms") +
      '</strong></div><div><span>失败 / 未确认调用</span><strong>' + number(stats.failures) + ' / ' + number(stats.unconfirmed) + '</strong></div><div><span>估算 Token</span><strong>不估算</strong></div><div><span>缓存命中 / 命中率</span><strong>' +
      (stats.cacheRate == null ? "暂无回执" : number(stats.cached) + ' / ' + percent(stats.cacheRate)) + '</strong></div><div><span>缓存写入 / 回执覆盖</span><strong>' +
      number(stats.creation) + ' / ' + (stats.cacheCoverage == null ? "暂无" : percent(stats.cacheCoverage)) + '</strong></div><div><span>调用确认率</span><strong>' + percent(stats.confirmedRate) + '</strong></div></div></div></details>' +
      '<div class="maint-token-analytics" id="maint-trends">' + trendPanelMarkup(data, stats, chartData, endDay,
        state.period === "7d" ? "近 7 日用量趋势" : state.period === "today" ? "近 14 日用量趋势" : state.period === "date" ? label + "用量趋势" : "近 30 日用量趋势",
        "按确认 Token 分段；选择日期查看该日用量") +
      breakdownPanelMarkup(providerRows, taskRows, label, data.windows?.["7d"]?.providers, data.windows?.["7d"]?.tasks) + '</div>' +
      '<section class="maint-history-panel maint-token-details" id="maint-history"><div class="maint-subhead"><div><strong>调用与用量明细</strong><span>Provider 回执和本地计价分列展示</span></div></div>' +
      tokenDetailTabsMarkup(activeDetail, [["daily", "每日明细"], ["providers", "Provider 明细"], ["tasks", "任务明细"], ["recent", "最近调用"]]) +
      tokenDetailPanel("daily", activeDetail, historyMarkup(data)) + tokenDetailPanel("providers", activeDetail, detailTable(providerRows, "providers")) +
      tokenDetailPanel("tasks", activeDetail, detailTable(taskRows, "tasks")) + tokenDetailPanel("recent", activeDetail, recentPreviewMarkup(data)) + '</section>';
  }

  function efficiencyDuration(value) {
    if (!Number.isFinite(value) || value < 0) return "未观测";
    const milliseconds = Math.round(value);
    if (milliseconds < 1000) return milliseconds + " 毫秒";
    if (milliseconds < 60000) return (milliseconds / 1000).toFixed(1) + " 秒";
    const minutes = Math.floor(milliseconds / 60000);
    const seconds = Math.round((milliseconds % 60000) / 1000);
    return seconds ? minutes + " 分 " + seconds + " 秒" : minutes + " 分";
  }

  function replyEfficiencyMarkup(report) {
    const summary = report?.summary || {};
    const coverage = report?.coverage || {};
    const samples = Array.isArray(report?.recent) ? report.recent : [];
    const sampleCount = Number(summary.samples) || 0;
    const hasCore = Boolean(report?.by_source?.astrbot_core?.samples || coverage.core_samples);
    const hasLegacy = !hasCore || Boolean(report?.by_source?.legacy_v2_conversation?.samples);
    const metrics = [
      ...(hasCore ? [["核心 P50 发送返回", "median_end_to_end_send_returned_ms"], ["核心 P95 发送返回", "p95_end_to_end_send_returned_ms"],
        ["核心 P50 首次发送返回", "median_first_send_returned_ms"], ["核心 P50 首 Token", "median_ttft_ms"],
        ["P50 插件准备", "median_plugin_preparation_ms"], ["P50 已观测模型请求", "median_model_request_ms"],
        ["P50 等待 LLM 至插件准备", "median_waiting_llm_to_request_hook_ms"],
        ["P50 插件准备至 Agent", "median_request_hook_to_agent_ms"],
        ["P50 工具执行累计", "median_tool_ms"], ["P50 Agent 总时长", "median_agent_ms"]] : []),
      ...(hasLegacy ? [[hasCore ? "历史 P50 平台回执" : "P50 端到端", "median_end_to_end_ms"], [hasCore ? "历史 P95 平台回执" : "P95 端到端", "p95_end_to_end_ms"],
      [hasCore ? "历史 P50 首段回执" : "P50 首段送达", "median_first_reply_ms"],
      ["P50 模型至发送", "median_post_model_ms"], ["P50 分段投递", "median_delivery_ms"]] : []),
      ["P50 消息至模型", "median_pre_model_ms"],
      ...(hasLegacy ? [["P50 末条消息至模型", "median_last_input_to_model_ms"]] : []), ["P50 模型往返区间", "median_model_ms"],
      ...(hasCore ? [["P50 发送区间", "median_send_ms"]] : []),
      ["P50 Laya 决策", "median_laya_ms"],
    ];
    const coverageStages = [
      ["received", "消息收到"], ["model_started", "模型启动"], ["model_finished", "模型完成"],
      ["first_dispatched", "开始发送"], ["first_delivered", "首段回执"], ["final_delivered", "完整回执"],
      ...(hasCore ? [["core_send_returned", "核心发送返回"], ["core_request_observation", "核心逐轮观测"],
        ["core_preparation", "插件准备明细"]] : []),
    ];
    const outcomeLabels = {
      completed: ["已送达", "ok"], partial: ["部分送达", "warn"], unknown: ["结果未知", "unknown"],
      pending: ["处理中", "pending"], no_reply: ["未回复", "warn"], incomplete: ["未完成", "unknown"],
      failed: ["发送失败", "error"], denied: ["未获准发送", "warn"],
    };
    const metricMarkup = metrics.map(([label, key]) => '<div class="maint-efficiency-metric"><span>' + label +
      '</span><strong>' + efficiencyDuration(summary[key]) + '</strong></div>').join("");
    const coverageMarkup = coverageStages.map(([key, label]) => {
      const total = key.startsWith("core_") ? Number(coverage.core_samples) || 0 :
        hasCore && ["first_delivered", "final_delivered"].includes(key) ? Math.max(0, sampleCount - (Number(coverage.core_samples) || 0)) : sampleCount;
      const observed = Math.min(total, Math.max(0, Number(coverage[key]) || 0));
      const width = total ? Math.round(observed * 100 / total) : 0;
      return '<div class="maint-efficiency-coverage-row"><span>' + label + '</span><strong>' + number(observed) +
        ' / ' + number(total) + '</strong><i style="--coverage-width:' + width + '%"></i></div>';
    }).join("");
    const daily = Array.isArray(report?.daily) ? report.daily : [];
    const dailyValues = daily.flatMap(item => [
      hasCore ? item.median_end_to_end_send_returned_ms : null,
      hasLegacy ? item.median_end_to_end_ms : null,
    ]).filter(value => Number.isFinite(value) && value >= 0);
    const dailyObservedMax = Math.max(0, ...dailyValues);
    const targetTick = dailyObservedMax > 0 ? dailyObservedMax / 4 : 1;
    const tickMagnitude = 10 ** Math.floor(Math.log10(targetTick));
    const tickRatio = targetTick / tickMagnitude;
    const dailyTick = (tickRatio <= 1 ? 1 : tickRatio <= 2 ? 2 : tickRatio <= 5 ? 5 : 10) * tickMagnitude;
    const dailyMax = dailyTick * 4;
    const dailyUnit = dailyMax < 1000 ? [1, '毫秒'] : [1000, '秒'];
    const dailyAxis = [4, 3, 2, 1, 0].map(step => '<span style="--tick-position:' + (4 - step) * 25 + '%">' +
      Number((dailyTick * step / dailyUnit[0]).toFixed(2)) + '</span>').join('');
    const dayLabel = day => /^\d{4}-\d{2}-\d{2}$/.test(day || '')
      ? Number(day.slice(5, 7)) + '/' + Number(day.slice(8, 10)) : day || '';
    const sameMonth = new Set(daily.map(item => String(item.day || '').slice(0, 7))).size === 1;
    const dailyRange = daily.length ? dayLabel(daily[0].day) + ' – ' + dayLabel(daily[daily.length - 1].day) : '';
    const dailyLegend = [
      ...(hasCore ? [['core', '核心发送返回']] : []),
      ...(hasLegacy ? [['legacy', hasCore ? '历史平台回执' : '平台回执']] : []),
    ].map(([key, label]) => '<span class="maint-efficiency-legend-item"><i data-series="' + key + '" aria-hidden="true"></i>' + label + '</span>').join("");
    const dailyMarkup = daily.map(item => {
      const day = typeof item.day === 'string' ? item.day : '';
      const label = dayLabel(day);
      const compactLabel = sameMonth && /^\d{4}-\d{2}-\d{2}$/.test(day) ? Number(day.slice(8, 10)) + '日' : label;
      const points = [
        ...(hasCore ? [['core', '核心发送返回', item.median_end_to_end_send_returned_ms, item.p95_end_to_end_send_returned_ms]] : []),
        ...(hasLegacy ? [['legacy', hasCore ? '历史平台回执' : '平台回执', item.median_end_to_end_ms, item.p95_end_to_end_ms]] : []),
      ];
      const readouts = points.filter(([, , value]) => Number.isFinite(value) && value >= 0);
      const daySummary = readouts.length
        ? day + '：' + readouts.map(([, label, value, p95]) => label + ' P50 ' + efficiencyDuration(value) +
          (Number.isFinite(p95) && p95 >= 0 ? '，P95 ' + efficiencyDuration(p95) : '')).join('；')
        : day + '：回复时长未观测';
      const detail = '<strong>' + escapeHtml(day) + ' · ' + number(item.samples) + ' 条对话</strong>' + points.map(([key, label, value, p95]) =>
        '<span class="maint-efficiency-day-readout"><span><i data-series="' + key + '"></i>' + label + '</span><b>' +
        (Number.isFinite(value) && value >= 0 ? 'P50 ' + efficiencyDuration(value) : '未观测') + '</b>' +
        (Number.isFinite(p95) && p95 >= 0 ? '<small>P95 ' + efficiencyDuration(p95) + '</small>' : '') + '</span>').join('');
      const bars = points.map(([key, label, value, p95]) => {
        if (!Number.isFinite(value) || value < 0) {
          return '<span class="maint-efficiency-trend-gap" data-series="' + key + '" aria-hidden="true"></span>';
        }
        const height = Math.max(value === 0 ? 1 : 0, Math.round(value * 100 / dailyMax));
        const tooltip = label + ' · ' + day + ' · P50 ' + efficiencyDuration(value) +
          (Number.isFinite(p95) && p95 >= 0 ? ' · P95 ' + efficiencyDuration(p95) : '');
        return '<i class="maint-efficiency-trend-bar" data-series="' + key + '" style="--bar-height:' + height +
          '%" title="' + escapeHtml(tooltip) + '" aria-hidden="true"></i>';
      }).join('');
      return '<div class="maint-efficiency-day" role="group" aria-label="' + escapeHtml(daySummary) + '" tabindex="0">' +
        '<div class="maint-efficiency-day-bars">' + bars + '</div><time datetime="' + escapeHtml(day) + '">' +
        '<span class="maint-efficiency-date-full">' + escapeHtml(label) + '</span><span class="maint-efficiency-date-compact" aria-hidden="true">' +
        escapeHtml(compactLabel) + '</span></time><small>' + number(item.samples) + ' 条</small><div class="maint-efficiency-day-detail" aria-hidden="true">' + detail + '</div></div>';
    }).join('');
    const recentMarkup = samples.map(sample => {
      const core = sample.execution_path === "astrbot_core";
      const status = core && sample.outcome === "completed" ? ["核心发送已返回", "ok"] :
        core && sample.outcome === "partial" ? ["部分发送返回", "warn"] : outcomeLabels[sample.outcome] || ["状态未知", "unknown"];
      const times = sample.timestamps || {};
      const stages = sample.stages || {};
      const activeCalls = Math.max(0, Number(sample.active_model_calls) || 0);
      const trace = sample.trace || {};
      const efficiencyTimestamp = value => {
        if (!value) return {text: "未观测", observed: false};
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return {text: "时间未知", observed: false};
        return {text: new Intl.DateTimeFormat("zh-CN", {
          month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
        }).format(date), observed: true};
      };
      const eventRow = (key, label) => {
        const timestamp = efficiencyTimestamp(times[key]);
        const isActiveModel = key === "last_model_finished_at" && !timestamp.observed && activeCalls > 0;
        const value = isActiveModel ? "调用中 · " + number(activeCalls) + " 次活跃" : timestamp.text;
        const observed = timestamp.observed || isActiveModel;
        return '<li class="maint-efficiency-timeline-event" data-event="' + key + '" data-observed="' + observed + '">' +
          '<span class="maint-efficiency-timeline-pin" aria-hidden="true"></span><div class="maint-efficiency-event-copy">' +
          '<span>' + label + '</span><time' + (timestamp.observed ? ' datetime="' + escapeHtml(times[key]) + '"' : "") + '>' +
          escapeHtml(value) + '</time></div></li>';
      };
      const stageRow = (key, label) => {
        const stageKey = key.endsWith("_ms") ? key : key + "_ms";
        const traceStage = ["input_wait", "queue_wait", "preparation"].includes(key);
        const value = traceStage ? trace[stageKey] : stages[stageKey];
        const observed = Number.isFinite(value) && value >= 0;
        return '<li class="maint-efficiency-timeline-stage" data-stage="' + key + '" data-observed="' + observed + '">' +
          '<span class="maint-efficiency-timeline-rail" aria-hidden="true"></span><div class="maint-efficiency-stage-copy">' +
          '<span>' + label + '</span><strong>' + efficiencyDuration(value) + '</strong></div></li>';
      };
      const materials = Array.isArray(trace.materials) ? trace.materials : [];
      const offeredTools = Array.isArray(trace.offered_tools) ? trace.offered_tools : [];
      const materialStateLabels = {ready: "已注入", deferred: "已延后", unavailable: "不可用", empty: "无可用素材", not_injected: "未注入", unknown: "状态未知"};
      const materialLabels = {expression_preferences: "表达偏好", task_prompt: "对话附加提示", role_profile: "角色档案",
        person_card: "相识资料", perception: "时空与环境", proactive_inspirations: "主动灵感", relationship_profile: "关系档案",
        memory: "记忆召回", relationship: "相处背景", life: "角色生活", growth: "角色成长", self_review: "自我提醒",
        open_items: "未完成事项", role_state: "角色状态", group_participation: "群聊参与", group_speaker: "当前发言者",
        group_network: "群成员与互动", group_memory: "群公共记忆", expression_style: "表达习惯"};
      const materialRows = materials.map(item => {
        const source = core && materialLabels[item.purpose] ? materialLabels[item.purpose] :
          [item.owner, item.purpose].filter(Boolean).map(value => String(value)).join(" / ") || "提示素材";
        const state = materialStateLabels[item.state] ? item.state : "unknown";
        const details = [materialStateLabels[state]];
        if (Number.isFinite(item.elapsed_ms)) details.push(efficiencyDuration(item.elapsed_ms));
        if (Number.isFinite(item.chars) && item.chars > 0) details.push(number(item.chars) + ' 字符');
        return '<li class="maint-efficiency-material" data-state="' + state + '"><span>' + escapeHtml(source) +
          '</span><small>' + details.join(' · ') + '</small></li>';
      }).join("");
      const toolRows = offeredTools.map(action => '<li class="maint-efficiency-offered-tool"><span>' +
        escapeHtml(String(action)) + '</span><small>可调用</small></li>').join("");
      const offeredToolCount = trace.offered_tool_count;
      const toolCountLabel = Number.isFinite(offeredToolCount)
        ? number(offeredToolCount) + ' 项可用工具' : '工具数量未观测';
      const readyMaterials = materials.filter(item => item.state === "ready").length;
      const injectionMarkup = materialRows || toolRows
        ? '<ul class="maint-efficiency-injection-list">' + materialRows + toolRows + '</ul>'
        : '<p class="maint-efficiency-trace-empty">' + (core ? "补充素材目录未观测；工具数量以核心请求签名为准。" : "无可归类的补充素材或可用工具") + '</p>';
      const callStateLabels = {
        generated: "完成", dispatched: "进行中", reserved: "等待中", not_started: "未启动", discarded: "已跳过",
        queued: "排队中", running: "执行中", succeeded: "完成", failed: "失败", skipped: "跳过", unknown: "结果未知", started: "执行中", returned: "已返回",
      };
      const callFailureLabels = {
        provider_request_failed: "Provider 请求失败", provider_wait_timed_out: "Provider 等待超时",
        native_response_parse_failed: "宿主响应解析失败", response_contract_rejected: "响应校验未通过",
        request_preparation_failed: "请求准备失败", provider_recheck_failed: "Provider 状态复核失败",
        role_recheck_failed: "角色状态复核失败", provider_wait_interrupted: "Provider 等待中断",
        adapter_failed: "适配器异常", request_recheck_failed: "请求状态复核失败",
      };
      const parseStateLabels = {
        not_received: "未收到响应", raw_observed: "已收到原始响应", native_parsed: "宿主已解析",
        native_rejected: "宿主解析失败", stream_rejected: "流式校验失败", contract_rejected: "回复校验失败",
        validated: "回复已通过校验", tools: "工具请求已通过校验", captured: "已交给记忆解析", refusal: "模型拒绝",
      };
      const callDetails = [];
      const preparationLabels = {context_build: "素材准备", request_authorization: "请求授权", prompt_injection: "提示词注入",
        tool_injection: "工具注入", send_tool_injection: "发送工具注入"};
      (Array.isArray(trace.preparation_steps) ? trace.preparation_steps : []).forEach(step => {
        callDetails.push({at: step.started_at, kind: "preparation", label: "插件准备 · " + (preparationLabels[step.name] || "准备阶段"),
          state: step.state, elapsed: step.elapsed_ms});
      });
      (Array.isArray(trace.model_calls) ? trace.model_calls : []).forEach((call, index) => {
        const usageObservation = core && call.measurement_basis && call.measurement_basis !== "provider_response";
        callDetails.push({
          at: call.started_at,
          kind: "model",
          label: (usageObservation ? "用量观察 " : "模型请求 ") + number(Number.isFinite(call.position) ? call.position + (core ? 0 : 1) : index + 1),
          state: call.state,
          elapsed: call.elapsed_ms,
          record: call,
        });
      });
      (Array.isArray(trace.tool_calls) ? trace.tool_calls : []).forEach(call => {
        const extension = call.kind === "extension";
        callDetails.push({
          at: extension ? (call.extension_started_at || call.started_at) : call.started_at,
          kind: extension ? "extension" : "builtin",
          label: (extension ? "扩展插件 · " : core ? "核心工具 · " : "内置工具 · ") + String(call.action || "未命名工具"),
          state: call.state,
          elapsed: extension ? call.extension_elapsed_ms : call.elapsed_ms,
        });
      });
      const laya = trace.laya || {};
      (Array.isArray(laya.events) ? laya.events : []).forEach(event => {
        const decision = event.decision && typeof event.decision === "object"
          ? Object.entries(event.decision).map(([key, value]) => key + " " + value).join(" · ") : "";
        callDetails.push({
          at: event.observed_at,
          kind: "laya",
          label: "Laya 决策 · " + (event.mode || "未知模式"),
          state: event.status,
          elapsed: event.elapsed_ms,
          record: {failure_code: event.reason_code, detail: event.reason || decision},
        });
      });
      callDetails.sort((left, right) => {
        const leftTime = Date.parse(left.at || "");
        const rightTime = Date.parse(right.at || "");
        return (Number.isFinite(leftTime) ? leftTime : Number.MAX_SAFE_INTEGER) -
          (Number.isFinite(rightTime) ? rightTime : Number.MAX_SAFE_INTEGER);
      });
      const callDetailsMarkup = callDetails.map(call => {
        const callTime = efficiencyTimestamp(call.at);
        const state = callStateLabels[call.state] || "状态未知";
        const row = call.record || {};
        const failure = row.failure_code ? (callFailureLabels[row.failure_code] || "调用失败") : "";
        const detail = row.detail || "";
        const provider = row.provider ? "Provider " + row.provider : "";
        const usage = (row.tokens == null ? "用量未确认" : number(row.tokens) + " Token") +
          (row.measurement_basis === "last_response_only" ? " · 仅最后响应" : row.measurement_basis === "runner_aggregate" ? " · Agent 汇总，调用次数未知" :
            row.measurement_basis === "last_tool_requery_only" ? " · 仅末次工具参数重查，次数未知" : "");
        const tokenParts = "输入 " + (row.input_tokens == null ? "未确认" : number(row.input_tokens)) +
          " · 输出 " + (row.output_tokens == null ? "未确认" : number(row.output_tokens));
        const diag = row.response_diagnostics;
        const responseMeta = diag ? [
          parseStateLabels[diag.parse_state] || "解析状态未知",
          "结束原因 " + (diag.completion_present && diag.finish_reason !== "missing" ? diag.finish_reason : "未返回"),
          diag.status_code ? "HTTP " + number(diag.status_code) : "",
          diag.error_type || "",
          diag.error_code || "",
        ].filter(Boolean).join(" · ") : "响应诊断未采集";
        const textCounts = diag ? (diag.completion_present
          ? ["原始", "宿主解析", "待呈现"].map((label, index) => {
              const summary = [diag.raw, diag.parsed, diag.visible][index];
              return label + " " + (summary ? number(summary.chars) + " 字" +
                (summary.has_internal_markers ? " · 含标签样式标记" : "") : "未取得");
            }).join(" · ")
          : "未收到完整响应内容") : "";
        return '<li class="maint-efficiency-call" data-kind="' + call.kind + '" data-state="' + escapeHtml(call.state || "unknown") + '"><div><strong>' +
          escapeHtml(call.label) + '</strong><small>' + escapeHtml(callTime.text) + ' · ' + state +
          (failure ? ' · <em>' + escapeHtml(failure) + '</em>' : "") + '</small>' +
          (call.kind === "model" ? '<small>' + escapeHtml([provider, usage, tokenParts].filter(Boolean).join(" · ")) + '</small>' +
            '<small class="maint-efficiency-call-diagnostic">' + escapeHtml(responseMeta) +
            (textCounts ? ' · ' + escapeHtml(textCounts) : "") + '</small>' :
            call.kind === "laya" && detail ? '<small class="maint-efficiency-call-diagnostic">' + escapeHtml(detail) + '</small>' : "") +
          '</div><b>' + efficiencyDuration(call.elapsed) + '</b></li>';
      }).join("");
      const originLabel = core ? "原生消息" : sample.received_time_source === "input_event" ? "首条消息" : "批次创建";
      const multipleInputs = Number(sample.input_count) > 1;
      const receivedLabel = sample.received_time_source === "input_event" ? "首条消息" : "批次创建";
      const deliveryLabel = sample.outcome === "completed" ? "末段回执" :
        Number(sample.delivered_parts) > 0 ? "末次成功回执" : "完整回执";
      const firstReceipt = times.first_delivered_at;
      const lastReceipt = times.last_delivered_at;
      const sameReceipt = Boolean(firstReceipt && lastReceipt && Date.parse(firstReceipt) === Date.parse(lastReceipt));
      const mergedReceipt = sameReceipt && sample.outcome === "completed";
      const timelineRows = core ? [
        ["received_at", "原生消息收到"], ["waiting_llm_at", "进入 AstrBot 请求队列"],
        ["generation_started_at", "插件请求准备完成"], ["agent_started_at", "Agent 启动"],
        ["first_model_started_at", "模型启动"], ["last_model_finished_at", "末次模型返回（已观测）"], ["agent_finished_at", "Agent 完成"],
        ["first_dispatched_at", "首次开始发送"], ["first_send_returned_at", "首次发送返回"], ["last_send_returned_at", "末次发送返回"],
      ].sort(([left], [right]) => (Date.parse(times[left]) || Infinity) - (Date.parse(times[right]) || Infinity))
        .map(([key, label]) => eventRow(key, label)).join("") : [
        eventRow("received_at", receivedLabel),
        ...(multipleInputs ? [eventRow("last_input_received_at", "末条消息")] : []),
        stageRow("input_wait", "末条消息 → 防抖结束"),
        eventRow("flow_due_at", "防抖等待结束"),
        stageRow("queue_wait", "等待执行名额"),
        eventRow("generation_started_at", "回复工作开始"),
        stageRow("preparation", "上下文与模型请求准备"),
        eventRow("first_model_started_at", "模型启动"),
        stageRow("model", "模型请求与工具往返区间"),
        eventRow("last_model_finished_at", "模型完成"),
        stageRow("post_model", "模型完成 → 开始发送"),
        eventRow("first_dispatched_at", "开始发送"),
        stageRow("delivery", "开始发送 → " + deliveryLabel),
        eventRow("first_delivered_at", mergedReceipt ? "首段 / 末段回执" : "首段回执"),
        ...(mergedReceipt ? [] : [eventRow("last_delivered_at", deliveryLabel)]),
      ].join("");
      const cumulativeMarkup = [
        ["到模型启动", "pre_model_ms"], [core ? "到首次发送返回" : "到首段回执", core ? "first_send_returned_ms" : "first_reply_ms"],
        [core ? "到末次发送返回" : "到完整回执", core ? "end_to_end_send_returned_ms" : "end_to_end_ms"],
      ].map(([label, key]) => '<div class="maint-efficiency-cumulative-item"><span>' + label + '</span><strong>' +
        efficiencyDuration(stages[key]) + '</strong></div>').join("");
      const stageBreakdown = core ? '<section class="maint-efficiency-cumulative" aria-label="单次回复分项耗时">' +
        '<div class="maint-efficiency-cumulative-head"><strong>分项耗时</strong><span>累计与区间存在重叠</span></div>' +
        '<div class="maint-efficiency-cumulative-grid">' + [["插件准备", "plugin_preparation_ms"], ["已观测模型请求累计", "model_request_ms"],
          ["等待 LLM 至插件准备", "waiting_llm_to_request_hook_ms"], ["插件准备至 Agent", "request_hook_to_agent_ms"],
          ["工具执行累计", "tool_ms"], ["Agent 总时长", "agent_ms"], ["发送区间", "send_ms"], ["首 Token", "ttft_ms"]]
        .map(([label, key]) => '<div class="maint-efficiency-cumulative-item"><span>' + label + '</span><strong>' +
          efficiencyDuration(stages[key]) + '</strong></div>').join("") + '</div></section>' : "";
      const pluginCallCount = Array.isArray(trace.tool_calls)
        ? number(trace.tool_calls.filter(call => core || call.kind === "extension").length) + ' 次'
        : '未观测';
      const modelCountLabel = core && sample.model_call_count_complete !== true
        ? '已观测请求 ' + number(sample.model_calls) + ' 次 · 完整次数未知'
        : '模型调用 ' + number(sample.model_calls) + ' 次';
      const usageObservationCount = sample.usage_observation_count ??
        (Array.isArray(trace.model_calls) ? trace.model_calls.length : 0);
      const sampleFacts = [number(sample.input_count) + ' 条输入',
        core ? number(sample.send_returned_parts) + ' 次发送返回' : number(sample.delivered_parts) + ' / ' + number(sample.part_count) + ' 段送达',
        modelCountLabel,
        (core ? '核心工具 ' : '插件 ') + pluginCallCount];
      if (core) sampleFacts.push('AstrBot 核心 · 平台确认未观测');
      if (laya.calls || laya.skipped) sampleFacts.push('Laya ' + number(laya.calls) + ' 次调用 / ' + number(laya.skipped) + ' 次跳过');
      if (Number(sample.failed_parts) > 0) sampleFacts.push('失败 ' + number(sample.failed_parts) + ' 段');
      if (Number(sample.unknown_parts) > 0) sampleFacts.push((core ? '发送结果未知 ' : '回执未知 ') + number(sample.unknown_parts) + ' 段');
      if (Number(sample.pending_parts) > 0) sampleFacts.push((core ? '等待返回 ' : '待回执 ') + number(sample.pending_parts) + ' 段');
      const sampleKey = String(sample.turn_id || sample.batch_id || sample.received_at || "");
      return '<details class="maint-efficiency-sample" data-reply-sample="' + escapeHtml(sampleKey) + '"' +
        (state.openReplySamples?.has(sampleKey) ? ' open' : '') + '><summary><div class="maint-efficiency-sample-overview">' +
        '<div class="maint-efficiency-sample-overview-head"><time>' + escapeHtml(time(sample.received_at)) +
        '</time><span class="maint-efficiency-status" data-state="' + status[1] + '">' + status[0] +
        '</span></div><span class="maint-efficiency-sample-facts">' + sampleFacts.join(' · ') +
        '</span></div><div class="maint-efficiency-sample-timings"><div><span>' + (core ? '首次发送返回' : '首段响应') + '</span><strong>' +
        efficiencyDuration(core ? stages.first_send_returned_ms : stages.first_reply_ms) + '</strong></div><div><span>' + (core ? '末次发送返回' : '完整送达') + '</span><strong>' +
        efficiencyDuration(core ? stages.end_to_end_send_returned_ms : stages.end_to_end_ms) + '</strong></div></div></summary><div class="maint-efficiency-detail">' +
        '<section class="maint-efficiency-cumulative" aria-label="起点累计耗时"><div class="maint-efficiency-cumulative-head">' +
        '<strong>起点累计</strong><span>从' + originLabel + '起算</span></div><div class="maint-efficiency-cumulative-grid">' +
        cumulativeMarkup + '</div></section>' + stageBreakdown + '<section class="maint-efficiency-flow" aria-label="单次回复时间线">' +
        '<div class="maint-efficiency-flow-head"><strong>回复流程</strong><span>' + modelCountLabel +
        (activeCalls ? ' · 活跃 ' + number(activeCalls) + ' 次' : "") + '</span></div><ol class="maint-efficiency-timeline">' +
        timelineRows + '</ol><div class="maint-efficiency-trace-grid"><section class="maint-efficiency-trace-section" aria-label="上下文与工具注入">' +
        '<div class="maint-efficiency-trace-head"><strong>上下文与工具注入</strong><span>' + number(readyMaterials) +
        ' 项素材已注入 · ' + escapeHtml(toolCountLabel) + '</span></div>' + injectionMarkup +
        '</section><section class="maint-efficiency-trace-section" aria-label="模型和插件调用明细">' +
        '<div class="maint-efficiency-trace-head"><strong>模型与插件调用</strong><span>' + (core ? '用量观察 ' +
        number(usageObservationCount) + ' 条' : '模型 ' + number(Array.isArray(trace.model_calls) ? trace.model_calls.length : 0) + ' 次') + ' · 工具 ' +
        number(Array.isArray(trace.tool_calls) ? trace.tool_calls.length : 0) + ' 次</span></div>' +
        (callDetailsMarkup ? '<ol class="maint-efficiency-call-list">' + callDetailsMarkup + '</ol>' :
          '<p class="maint-efficiency-trace-empty">调用明细未观测</p>') +
        '<p class="maint-efficiency-trace-note">宿主其他插件钩子未采集</p></section></div></section></div></details>';
    }).join("");
    const emptyMarkup = sampleCount
      ? (samples.length ? "" : '<div class="maint-empty"><span class="maint-empty-mark">—</span><p>暂无可展示的近期样本。</p></div>')
      : '<div class="maint-empty"><span class="maint-empty-mark">—</span><p>' +
        (report ? "近 7 日暂无对话样本。" : "回复效率数据暂不可用。") + '</p></div>';
    return '<div class="maint-efficiency-page"><section class="maint-efficiency-panel" aria-label="近七日耗时概览">' +
      '<div class="maint-panel-heading"><div><span class="maint-eyebrow">回复 / 近 7 天</span><h3>近 7 日耗时概览</h3></div><span class="maint-efficiency-count">' +
      number(sampleCount) + ' 条对话 · ' + (hasCore ? number(summary.send_returned_samples) + ' 条核心发送返回 · ' +
        number(report?.by_source?.legacy_v2_conversation?.completed) + ' 条历史完整送达' : number(summary.completed) + ' 条完整送达') + ' · Laya ' + number(summary.laya_calls || 0) +
      ' 次调用 / ' + number(summary.laya_skipped || 0) + ' 次跳过 / ' + number(summary.laya_failed || 0) + ' 次失败</span></div><div class="maint-efficiency-metrics">' +
      metricMarkup + '</div>' + (hasCore ? '<p class="maint-scope-note">核心指标以原生发送方法返回为终点；流式发送可能早于 Agent 完成。平台确认未观测，历史平台回执另列。</p>' : "") + '</section><div class="maint-efficiency-analysis"><section class="maint-efficiency-panel" aria-label="阶段观测覆盖率">' +
      '<div class="maint-panel-heading"><div><span class="maint-eyebrow">可观测性</span><h3>阶段观测覆盖</h3></div></div>' +
      (coverageStages.length && sampleCount ? '<div class="maint-efficiency-coverage">' + coverageMarkup + '</div>' : emptyMarkup) +
      '</section><section class="maint-efficiency-panel maint-efficiency-trend-panel" aria-label="每日回复趋势"><div class="maint-panel-heading"><div><span class="maint-eyebrow">每日 / P50</span><h3>每日回复时长</h3></div><span class="maint-efficiency-count">每日中位数</span></div>' +
      (dailyValues.length ? '<div class="maint-efficiency-daily"><div class="maint-efficiency-legend">' + dailyLegend +
        '</div><div class="maint-efficiency-trend-meta"><span>单位：' + dailyUnit[1] + '</span><span>' + escapeHtml(dailyRange) + '</span></div><div class="maint-efficiency-trend-chart" role="group" aria-label="每日回复时长中位数柱状图，共用纵轴"><div class="maint-efficiency-trend-axis" aria-hidden="true">' +
        dailyAxis + '</div><div class="maint-efficiency-trend-plot"><div class="maint-efficiency-trend-guides" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i></div><div class="maint-efficiency-trend-columns" style="--day-count:' + daily.length + '">' +
        dailyMarkup + '</div></div></div></div>' : '<div class="maint-empty"><span class="maint-empty-mark">—</span><p>' +
        (hasCore ? '暂无核心发送返回或历史回执样本。' : '暂无完整送达样本。') + '</p></div>') +
      '</section></div><section class="maint-efficiency-panel maint-efficiency-recent" aria-label="近期回复样本"><div class="maint-panel-heading"><div><span class="maint-eyebrow">最近 / ' +
      number(samples.length) + '</span><h3>近期回复样本</h3></div></div>' +
      (recentMarkup || emptyMarkup) + '</section></div>';
  }

  function render() {
    if (!root || !state) return;
    const samples = root.querySelectorAll?.("details[data-reply-sample]");
    if (samples?.length) state.openReplySamples = new Set(Array.from(samples)
      .filter(item => item.open).map(item => item.dataset.replySample));
    if (!state.data && state.loading) {
      root.innerHTML = '<div class="maint-loading"><span class="maint-loading-mark"><i data-lucide="activity"></i></span><strong>正在读取角色账本与运行快照</strong><span class="maint-loading-line"></span></div>';
      window.lucide?.createIcons?.();
      return;
    }
    if (!state.data && state.error) {
      root.innerHTML = '<div class="maint-error" role="alert"><i data-lucide="cloud-off"></i><div><strong>维护面板暂时离线</strong><p>' + escapeHtml(state.error) + '</p></div><button class="maint-probe-button" type="button" data-refresh>重新读取</button></div>';
      window.lucide?.createIcons?.();
      return;
    }
    const roleData = state.data || {};
    const localSource = localUsage(roleData, state.tokenSource);
    const data = selectedUsage(roleData);
    const stats = statsFor(data);
    const periodComparison = comparisonMarkup(usageComparison(data, stats));
    const today = localDay(data.generated_at);
    const label = ({today: "今日", "7d": "近 7 日", "30d": "近 30 日", total: "全部历史"})[state.period]
      || (state.period === "date" ? stats.day : "今日");
    const providerRows = dimensions(data, stats, "providers");
    const taskRows = dimensions(data, stats, "tasks");
    const externalSource = data.external_token_sources?.[state.tokenSource];
    const tokenScopeNote = localSource ? localSourceNames[state.tokenSource] + " · 当前角色"
      : externalSource?.available ? (externalSource.display_name || "外部插件") + " 独立账本 · 不计入 V2 角色预算"
        : "所选来源暂不可用 · 当前角色数据不会代替它";
    const recentData = localSourceNames[state.tokenSource]
      ? localSource ? data : null
      : externalSource?.available ? {...externalSource, generated_at: roleData.generated_at} : null;
    const recentSourceLabel = localSourceNames[state.tokenSource] || externalSource?.display_name
      || tokenSources.find(([key]) => key === state.tokenSource)?.[1] || "所选来源";
    const recentMatches = recentData ? matchingRecent(recentData) : [];
    const recentView = !recentData ? unavailableSourceMarkup(state.tokenSource, externalSource)
      : localSourceNames[state.tokenSource] ? recentMarkup(recentData)
        : externalRecentMarkup(filteredRecent(recentData), state.showAllCalls ? 80 : 10);
    const ledgerNote = state.activeMaintenanceNav === "efficiency"
      ? "时间源：核心模型与发送观测 · 历史 V2 回执 · 发送返回与平台确认分列"
      : localSource ? "数据源：AstrBot 核心观测与 V2 插件账本 · 只计已观测用量 · 缓存以 Provider 实报为准"
      : externalSource?.available ? "数据源：" + (externalSource.display_name || "外部插件") + "独立账本 · 外部费用未提供"
        : "数据源：所选来源尚未接入 · 不展示其他账本记录";
    const exportMarkup = state.activeMaintenanceNav === "efficiency" ? ""
      : '<button class="maint-export" type="button" data-export-tokens ' + (localSource || state.tokenSource === "main" && externalSource?.available ? "" : "disabled") +
        ' title="导出当前统计 CSV"><i data-lucide="download"></i><span>导出 CSV</span></button>';
    const cacheView = localSource ? cacheMarkup(stats, data)
      : cacheControlsMarkup(today) + (externalSource?.available ? externalCacheUnavailableMarkup(externalSource)
        : unavailableSourceMarkup(state.tokenSource, externalSource));
    const role = window.CompanionProduct?.roleLabel?.() || "当前角色";
    const chartData = Array.isArray(data.history) ? data.history : Array.isArray(data.daily) ? data.daily : [];
    const navItems = [
      ["overview", "Token 统计"], ["diagnostics", "排障"], ["cache", "缓存诊断"],
      ["efficiency", "回复效率"], ["recent", "最近调用"], ["prompts", "任务提示词"],
    ];
    const navMarkup = '<nav class="maint-subnav" aria-label="治理与维护二级导航">' + navItems.map(item =>
      '<button type="button" data-maint-nav="' + item[0] + '" aria-current="' + (state.activeMaintenanceNav === item[0] ? "page" : "false") + '">' + item[1] + '</button>').join("") + '</nav>';
    const boardTitles = {overview: "Token 管理", diagnostics: "运行排障", cache: "缓存诊断", efficiency: "近期回复效率",
      recent: "最近调用", prompts: "任务提示词"};
    const boardDescriptions = {efficiency: "近 7 日 · 核心发送耗时与历史投递耗时"};
    const boardTitle = boardTitles[state.activeMaintenanceNav] || "运行态势";
    root.innerHTML = '<div class="maint-board"><header class="maint-board-head"><div class="maint-board-copy"><span class="maint-eyebrow">陪伴 / 信号室</span><h2>' + boardTitle + '</h2><p>' +
      (boardDescriptions[state.activeMaintenanceNav] || "运行态势 · 当前角色用量、预算与调用构成。") + '</p></div>' +
      '<div class="maint-board-actions"><span class="maint-live" data-state="' + (state.error ? "warn" : "ok") + '"><i></i>' + (state.loading ? "正在同步" : state.error ? "同步异常" : "已连接") +
      '</span><span class="maint-updated">同步于 ' + escapeHtml(time(data.generated_at)) + '</span>' + exportMarkup + '<button class="maint-refresh" type="button" data-refresh ' + (state.loading ? "disabled" : "") + ' aria-label="刷新运行统计" title="刷新运行统计"><i data-lucide="refresh-cw"></i><span>' + (state.loading ? "同步中" : "刷新") +
      '</span></button></div></header>' + navMarkup + '<section class="maint-view" data-maint-view="overview"' + (state.activeMaintenanceNav === "overview" ? "" : " hidden") + '><section class="maint-token-section" id="maint-token-section" aria-labelledby="maint-usage-heading"><div class="maint-panel-heading"><div><span class="maint-eyebrow">令牌 / 00</span><h3 id="maint-usage-heading">Token 统计管理</h3><p class="maint-scope-note"><i data-lucide="shield-check"></i> ' +
      escapeHtml(tokenScopeNote) +
      '</p></div></div>' +
      '<div class="maint-token-redesign">' + tokenDashboardControlsMarkup(today, roleData) + (localSource
        ? localTokenDashboardMarkup(data, stats, label, today, periodComparison, providerRows, taskRows, chartData)
        : localSourceNames[state.tokenSource] ? unavailableSourceMarkup(state.tokenSource) : externalTokenMarkup(state.tokenSource)) + '</div></section></section>' +
      '<section class="maint-view" data-maint-view="prompts"' + (state.activeMaintenanceNav === "prompts" ? "" : " hidden") + '>' + promptWorkspaceMarkup() + '</section>' +
      '<section class="maint-view" data-maint-view="diagnostics"' + (state.activeMaintenanceNav === "diagnostics" ? "" : " hidden") + '>' + diagnosticsMarkup(roleData, statsFor(roleData)) + '</section>' +
      '<section class="maint-view" data-maint-view="cache"' + (state.activeMaintenanceNav === "cache" ? "" : " hidden") + '>' + cacheView + '</section>' +
      '<section class="maint-view" data-maint-view="efficiency"' + (state.activeMaintenanceNav === "efficiency" ? "" : " hidden") + '>' +
      replyEfficiencyMarkup(roleData.reply_efficiency) + '</section>' +
      '<section class="maint-view" data-maint-view="recent"' + (state.activeMaintenanceNav === "recent" ? "" : " hidden") + '><section class="maint-recent-panel" id="maint-recent" aria-labelledby="maint-recent-heading"><div class="maint-panel-heading"><div><span class="maint-eyebrow">追踪 / 03</span><h3 id="maint-recent-heading">最近调用</h3></div><div class="maint-trace-controls"><span class="maint-recent-count">' +
      escapeHtml(recentSourceLabel) + ' · ' + number(recentMatches.length) + ' / ' + number(recentData?.recent?.length || 0) + ' 条</span><input type="search" data-call-search aria-label="搜索 Provider、任务、失败类型或日期" placeholder="筛 Provider / 任务 / 失败" value="' + escapeHtml(state.callQuery) + '"><select data-call-filter aria-label="筛选最近调用"><option value="all" ' + (state.callFilter === "all" ? "selected" : "") + '>全部记录</option><option value="failed" ' + (state.callFilter === "failed" ? "selected" : "") +
      '>失败</option><option value="uncertain" ' + (state.callFilter === "uncertain" ? "selected" : "") + '>未确认</option><option value="cache-low" ' + (state.callFilter === "cache-low" ? "selected" : "") + '>低缓存命中</option></select>' +
      (state.traceFilter ? '<button type="button" class="maint-text-action" data-clear-trace>清除排行筛选</button>' : "") + '<button type="button" class="maint-text-action" data-toggle-calls>' + (state.showAllCalls ? "收起" : "查看更多") +
      '</button></div></div>' + (state.traceFilter ? '<div class="maint-trace-filter-note">已按排行筛选：' + escapeHtml(state.traceFilter.kind === "tasks" ? taskName(state.traceFilter.key) : state.traceFilter.key) + '</div>' : "") +
      (state.selectedHour ? '<div class="maint-trace-filter-note">已聚焦时段：' + escapeHtml(state.selectedHour.replace("T", " ") + ":00") + ' <button type="button" class="maint-text-action" data-clear-hour>清除时段</button></div>' : "") +
      recentView + '</section></section><footer class="maint-board-footer"><span>当前角色 / ' + escapeHtml(role) + '</span><span>' + escapeHtml(ledgerNote) + '</span></footer></div>';
    if (!state.provider && data.providers?.[0]) state.provider = data.providers[0].id;
    if (!state.voiceProvider && state.integrations?.voice?.providers?.[0]) state.voiceProvider = state.integrations.voice.providers[0].id;
    if (!state.imageAction && state.integrations?.image?.actions?.[0]) state.imageAction = state.integrations.image.actions[0].action;
    window.lucide?.createIcons?.();
  }

  async function refresh() {
    if (!state || state.loading) return;
    const current = state;
    const ticket = generation;
    current.loading = true;
    current.error = "";
    let queryBalance = false;
    render();
    try {
      const path = modelPath();
      queryBalance = !current.balanceLoaded;
      const values = await Promise.allSettled([api("GET", path + "/maintenance"), api("GET", path + "/voice"),
        api("GET", path + "/image"), api("GET", path)]);
      if (ticket !== generation || state !== current) return;
      if (values[0].status === "rejected") throw values[0].reason;
      const data = values[0].value;
      if (!data || !data.summary || !Array.isArray(data.daily)) throw new Error("返回的数据结构不完整。");
      current.data = data;
      current.integrations = {
        voice: values[1].status === "fulfilled" ? values[1].value : null,
        image: values[2].status === "fulfilled" ? values[2].value : null,
      };
      current.modelStatus = values[3].status === "fulfilled" ? values[3].value : null;
      current.provider = current.provider || data.providers?.[0]?.id || "";
      current.voiceProvider = current.voiceProvider || current.integrations.voice?.providers?.[0]?.id || "";
      current.imageAction = current.imageAction || current.integrations.image?.actions?.[0]?.action || "";
    } catch (error) {
      if (ticket !== generation || state !== current) return;
      /* 主统计读取失败后必须进入明确的错误页；继续保留旧账本、集成状态或
         模型快照会让 Token、缓存和最近调用看起来像刚刚同步成功。余额是独立
         查询，仍由 refreshBalance() 单独维护。 */
      current.data = null;
      current.integrations = {};
      current.modelStatus = null;
      current.error = routeError(error.code || error.message);
    } finally {
      if (ticket === generation && state === current) {
        current.loading = false;
        render();
        if (queryBalance) void refreshBalance();
      }
    }
  }

  async function refreshBalance() {
    if (!state || state.balanceLoading) return;
    const current = state;
    const ticket = generation;
    current.balanceLoading = true;
    render();
    try {
      current.balance = await api("GET", modelPath() + "/balance");
    } catch (_error) {
      if (ticket !== generation || state !== current) return;
      current.balance = {configured: true, available: false, provider_label: "NewAPI", state: "unavailable"};
    } finally {
      if (ticket === generation && state === current) {
        current.balanceLoaded = true;
        current.balanceLoading = false;
        /* 余额是独立于账本刷新结果的成功读数；落缓存后切换页面不会退回
           到“尚未查询”，但下一次显式失败仍会覆盖为失败状态。 */
        cacheMaintenanceState();
        render();
      }
    }
  }

  async function probe() {
    if (!state || state.testing || !state.provider) return;
    const current = state;
    const ticket = generation;
    clearDiagnosticExpiry(current);
    current.diagnosticNotice = "";
    current.testing = true;
    current.test = null;
    render();
    try {
      const result = await api("POST", modelPath() + "/test", {provider_id: current.provider});
      if (ticket !== generation || state !== current) return;
      current.test = {...result, status: result.status || "unknown", title: result.status === "ok" ? "文本 Provider 探测通过" : "文本 Provider 探测未通过",
        message: result.status === "ok" ? number(result.latency_ms) + " ms · 未生成角色回复，服务商可能计费。" : routeError(result.code),
        suggestion: result.status === "ok" ? "Provider 可响应；若真实回复仍失败，请继续检查任务路由与回执。" : "核对 Provider 是否在线、接口配置和当前网络后再运行探测。"};
    } catch (error) {
      if (ticket !== generation || state !== current) return;
      current.test = {status: "failed", code: error.code || "model_probe_failed", title: "文本 Provider 探测失败", message: routeError(error.code || error.message),
        suggestion: "核对 Provider 状态与接口配置后重试。"};
    } finally {
      if (ticket === generation && state === current) {
        current.testing = false;
        scheduleDiagnosticAutoClear(current, ticket);
        render();
      }
    }
  }

  async function testVoice() {
    if (!state || state.voiceTesting || !state.voiceProvider) return;
    const current = state;
    const ticket = generation;
    clearDiagnosticExpiry(current);
    current.diagnosticNotice = "";
    current.voiceTesting = true;
    clearVoiceAudio(current);
    current.voiceTest = null;
    render();
    try {
      const result = await api("POST", modelPath() + "/voice/test", {provider_id: current.voiceProvider, language: current.voiceLanguage});
      if (ticket !== generation || state !== current) return;
      const audioUrl = voiceAudioUrl(result);
      const status = result.status === "ok" && !audioUrl ? "unavailable" : result.status || "unknown";
      const safeResult = {...result};
      delete safeResult.audio_base64;
      delete safeResult.audio_mime_type;
      const code = result.code || (status === "unavailable" ? "model_voice_audio_invalid" : "");
      current.voiceTest = {...safeResult, status, code, audioUrl,
        title: status === "ok" ? "TTS 合成通过" : "TTS 合成未通过",
        message: status === "ok" ? number(result.latency_ms) + " ms · 下方可试听；音频未发送给联系人。" : routeError(code),
        suggestion: status === "ok" ? "使用结果卡片中的播放器试听这次合成。" : "检查 AstrBot 中 Provider 是否加载，并确认语言绑定后重试。"};
    } catch (error) {
      if (ticket !== generation || state !== current) return;
      current.voiceTest = {status: "failed", code: error.code || "model_voice_probe_failed", title: "TTS 合成失败", message: routeError(error.code || error.message),
        suggestion: "检查语音 Provider 是否加载，以及所选语言是否已配置。"};
    } finally {
      if (ticket === generation && state === current) {
        current.voiceTesting = false;
        scheduleDiagnosticAutoClear(current, ticket);
        render();
      }
    }
  }

  async function testImage() {
    if (!state || state.imageTesting || !state.imageAction) return;
    const current = state;
    const ticket = generation;
    clearDiagnosticExpiry(current);
    current.diagnosticNotice = "";
    current.imageTesting = true;
    current.imageTest = null;
    render();
    try {
      const result = await api("POST", modelPath() + "/image/test", {action: current.imageAction});
      if (ticket !== generation || state !== current) return;
      current.imageTest = result;
    } catch (error) {
      if (ticket !== generation || state !== current) return;
      current.imageTest = {action: current.imageAction, registered: false, current: false, error: routeError(error.code || error.message)};
    } finally {
      if (ticket === generation && state === current) {
        current.imageTesting = false;
        scheduleDiagnosticAutoClear(current, ticket);
        render();
      }
    }
  }

  async function testStorage() {
    if (!state || state.storageTesting) return;
    const current = state;
    const ticket = generation;
    clearDiagnosticExpiry(current);
    current.diagnosticNotice = "";
    current.storageTesting = true;
    current.storageTest = null;
    render();
    try {
      const result = await api("POST", modelPath() + "/test/storage", {check: "sqlite"});
      if (ticket !== generation || state !== current) return;
      current.storageTest = {...result, status: result.status || "unknown",
        title: result.status === "ok" ? "SQLite 完整性检查通过" : "SQLite 完整性检查发现问题",
        message: result.status === "ok"
          ? "quick_check 与外键检查通过 · " + number(result.elapsed_ms) + " ms"
          : (Array.isArray(result.integrity) ? result.integrity.join(" · ") : "") +
            (result.foreign_key_issues ? " · 存在外键异常" : " · 检查未通过"),
        suggestion: result.status === "ok" ? "只读完整性检查通过，数据库页数未被更改。" : "保留数据库现状并检查插件日志与外键异常样本。"};
    } catch (error) {
      if (ticket !== generation || state !== current) return;
      current.storageTest = {status: "failed", code: error.code || "model_storage_check_failed", title: "SQLite 完整性检查失败", message: routeError(error.code || error.message),
        suggestion: "确认当前管理身份仍有效，并稍后重新运行只读检查。"};
    } finally {
      if (ticket === generation && state === current) {
        current.storageTesting = false;
        scheduleDiagnosticAutoClear(current, ticket);
        render();
      }
    }
  }

  function updatePromptTask(result, reset) {
    const updated = result?.task;
    if (!updated || !updated.kind) return;
    const tasks = Array.isArray(state.modelStatus?.tasks) ? state.modelStatus.tasks : [];
    state.modelStatus = {...state.modelStatus, tasks: tasks.map(task => task.kind === updated.kind ? updated : task)};
    const task = promptEditTarget(promptTasks().find(item => item.kind === updated.kind));
    if (!task) return;
    loadPromptDraft(task);
    state.promptMessage = reset ? (task.sectionKey === "task_prompt" ? "已恢复角色级继承值。" : "已恢复上级继承值。") : "提示词已保存。";
    state.promptMessageKind = "ok";
  }

  async function savePrompt(reset = false) {
    const task = promptEditTarget();
    if (!task || state.promptSaving || state.promptReloading || state.promptStale || !state.promptEditToken) return;
    const restoreDefault = !reset && state.promptDraft.trim() === "";
    if (!reset && (!promptDirty() || state.promptStale || state.promptDraft.length > PROMPT_LIMIT)) return;
    if (reset && !task.customized && !promptDirty()) return;
    if (restoreDefault && !task.customized && !promptDirty()) return;
    const current = state;
    const ticket = generation;
    const operationKey = promptOperationKey();
    current.promptSaving = true;
    current.promptMessage = reset || restoreDefault ? "正在恢复继承值……" : "正在保存任务提示……";
    current.promptMessageKind = "busy";
    render();
    try {
      const body = {kind: task.kind, operation_key: operationKey, edit_token: current.promptEditToken,
        changes: reset || restoreDefault ? {} : {prompt: current.promptDraft},
        reset: reset || restoreDefault ? ["prompt"] : []};
      if (task.sectionKey !== "task_prompt") body.section = task.sectionKey;
      const result = await api("POST", modelPath() + "/configure", body);
      if (ticket !== generation || state !== current) return;
      updatePromptTask(result, reset || restoreDefault);
    } catch (error) {
      if (ticket !== generation || state !== current) return;
      const known = Boolean(error.code);
      current.promptStale = error.code === "configuration_revision_conflict";
      current.promptMessageKind = current.promptStale ? "warn" : known ? "error" : "unknown";
      current.promptMessage = current.promptStale
        ? "保存时发现版本冲突，当前草稿已保留；请重新读取最新配置。"
        : error.code === "invalid_contact_preference" ? "模板无效：篇幅模板需保留 {chars}，仅支持这个变量；请核对文本。"
        : known ? routeError(error.code) : "保存结果未知，草稿与操作键已保留；确认状态后可重试。";
    } finally {
      if (ticket === generation && state === current) { current.promptSaving = false; render(); }
    }
  }

  async function reloadPromptFromServer() {
    const selectedKind = state?.promptSelectedKind;
    if (!selectedKind || state.promptReloading || state.promptSaving) return;
    const current = state;
    const ticket = generation;
    current.promptReloading = true;
    current.promptMessage = "正在读取最新服务器配置……";
    current.promptMessageKind = "busy";
    render();
    try {
      const latest = await api("GET", modelPath());
      if (ticket !== generation || state !== current) return;
      const latestTask = Array.isArray(latest?.tasks) ? latest.tasks.find(item => item?.kind === selectedKind) : null;
      if (!latestTask) throw new Error("当前任务已从 V2 注册目录移除，请刷新维护页。" );
      const accepted = typeof window.CompanionConfirm === "function"
        && await window.CompanionConfirm({title:"载入最新任务配置",message:"确认后会替换当前草稿，未保存内容将丢失。",confirmText:"替换草稿",danger:true});
      if (ticket !== generation || state !== current) return;
      if (!accepted) {
        current.promptMessage = "已保留当前草稿。";
        current.promptMessageKind = "warn";
        return;
      }
      current.modelStatus = latest;
      current.promptSelectedKind = selectedKind;
      const task = promptEditTarget(promptTasks().find(item => item.kind === selectedKind));
      if (!task) throw new Error("当前任务已从 V2 注册目录移除，请刷新维护页。" );
      loadPromptDraft(task);
      current.promptMessage = "已载入最新服务器配置。";
      current.promptMessageKind = "ok";
      current.promptOperationKey = "";
      current.promptOperationKind = "";
    } catch (error) {
      if (ticket !== generation || state !== current) return;
      current.promptMessage = error.code ? routeError(error.code) : "读取最新配置失败，当前草稿已保留。";
      current.promptMessageKind = error.code ? "error" : "unknown";
    } finally {
      if (ticket === generation && state === current) { current.promptReloading = false; render(); }
    }
  }

  function exportTokens() {
    const globalSource = state?.tokenSource === "main" ? state.data?.external_token_sources?.main : null;
    if (!state?.data || !localUsage(state.data, state.tokenSource) && !globalSource?.available || typeof document === "undefined" || typeof URL === "undefined" || typeof URL.createObjectURL !== "function") return;
    const data = globalSource ? {...globalSource, generated_at: state.data.generated_at} : selectedUsage();
    const bucket = globalSource ? externalBucketForPeriod(globalSource, data) : null;
    const stats = bucket ? {tokens: bucket.tokens, calls: bucket.calls, failures: bucket.failures,
      input: bucket.input_tokens, cached: bucket.cached_input_tokens, creation: bucket.cache_creation_tokens,
      output: bucket.output_tokens, cost: bucket.cost_microusd, latency: bucket.avg_latency_ms,
      day: state.period === "date" ? state.selectedDate : localDay(data.generated_at)} : statsFor(data);
    const rows = [["类型", "日期或名称", "Token", "调用", "失败", "输入", "缓存命中", "缓存写入", "输出", "已计价部分微美元（未知留空）", "平均延迟毫秒", "Provider / 任务"]];
    rows.push(["统计范围", state.period, stats.tokens, stats.calls, stats.failures, stats.input, stats.cached,
      stats.creation, stats.output, stats.cost, stats.latency ?? "", globalSource?.display_name || localSourceNames[state.tokenSource]]);
    for (const item of globalSource ? externalDimensions(globalSource, bucket, "providers") : dimensions(data, stats, "providers")) {
      rows.push(["Provider", item.key || "未知", item.tokens, item.calls, item.failures, item.input_tokens,
        item.cached_input_tokens, item.cache_creation_tokens, item.output_tokens, item.cost_microusd, item.avg_latency_ms ?? "", ""]);
    }
    for (const item of globalSource ? externalDimensions(globalSource, bucket, "tasks") : dimensions(data, stats, "tasks")) {
      rows.push(["任务", taskName(item.key), item.tokens, item.calls, item.failures, item.input_tokens,
        item.cached_input_tokens, item.cache_creation_tokens, item.output_tokens, item.cost_microusd, item.avg_latency_ms ?? "", item.key || ""]);
    }
    const history = Array.isArray(data.history) ? data.history : [];
    const periodWindow = data.windows?.[state.period];
    const firstDay = new Date(localDay(data.generated_at) + "T00:00:00");
    firstDay.setDate(firstDay.getDate() - (state.period === "7d" ? 6 : 29));
    const selectedHistory = state.period === "date" ? history.filter(row => (row.day || row.key) === stats.day)
      : state.period === "today" ? history.filter(row => (row.day || row.key) === localDay(data.generated_at))
        : periodWindow ? history.filter(row => (row.day || row.key) >= periodWindow.start_day && (row.day || row.key) <= periodWindow.end_day)
          : state.period === "total" ? history : history.filter(row => (row.day || row.key) >= localDay(firstDay)
            && (row.day || row.key) <= localDay(data.generated_at));
    for (const item of selectedHistory) {
      rows.push(["每日", item.day || item.key, item.tokens, item.calls, item.failures, item.input_tokens,
        item.cached_input_tokens, item.cache_creation_tokens, item.output_tokens, item.cost_microusd, item.avg_latency_ms ?? "", ""]);
    }
    for (const item of matchingRecent(data)) {
      rows.push(["调用", item.at || "", item.tokens ?? "", 1, item.failure || "", item.input_tokens ?? "",
        item.cached_input_tokens ?? "", item.cache_creation_tokens ?? "", item.output_tokens ?? "", item.cost_microusd ?? "",
        item.latency_ms ?? "", taskName(item.kind) + " / " + (item.provider || "未知 Provider")]);
    }
    const csv = "\uFEFF" + rows.map(row => row.map(value => '"' + String(value ?? "").replace(/"/g, '""') + '"').join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob([csv], {type: "text/csv;charset=utf-8"}));
    const link = document.createElement("a");
    link.href = url;
    link.download = "companion-v2-tokens-" + (state.tokenSource === "companion" ? "" : state.tokenSource + "-") + localDay(data.generated_at) + ".csv";
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  function setMaintenanceView(view) {
    if (!state) return;
    const views = ["overview", "diagnostics", "cache", "efficiency", "recent", "prompts"];
    const next = views.includes(view) ? view : "overview";
    const changed = state.activeMaintenanceNav !== next;
    if (changed && next !== "diagnostics" && state.memoryRunning) {
      stopMemoryDiagnostic("已离开排障页，采样已停止", {render: false});
    }
    state.activeMaintenanceNav = next;
    if (changed) state.onViewChange?.(next);
  }

  function click(event) {
    const target = event.target;
    const sectionNav = target.closest("[data-maint-nav]");
    if (sectionNav && state) {
      const requestedView = sectionNav.dataset.maintNav || "overview";
      setMaintenanceView(requestedView);
      render();
      return;
    }
    const promptMode = target.closest("[data-prompt-mode]");
    if (promptMode && state) {
      state.promptMode = promptMode.dataset.promptMode === "customized" ? "customized" : "all";
      render();
      return;
    }
    const promptGroup = target.closest("[data-prompt-group]");
    if (promptGroup && state) {
      state.promptGroup = promptGroup.dataset.promptGroup || "all";
      render();
      return;
    }
    const promptTask = target.closest("[data-prompt-task]");
    if (promptTask && state) {
      if (state.promptSaving || state.promptReloading) return;
      if (promptTask.dataset.promptTask === state.promptSelectedKind) return;
      if (promptTask.dataset.promptTask !== state.promptSelectedKind && promptDirty()) {
        state.promptMessage = "当前任务还有未保存更改，请先保存或恢复继承值后再切换。";
        state.promptMessageKind = "warn";
        render();
        return;
      }
      state.promptSelectedKind = promptTask.dataset.promptTask || "";
      state.promptInitialized = false;
      state.promptView = "edit";
      state.promptRuleKey = "";
      state.promptMessage = "";
      state.promptMessageKind = "";
      render();
      return;
    }
    if (target.closest("[data-prompt-save]") && state) { void savePrompt(false); return; }
    if (target.closest("[data-prompt-reset]") && state) { void savePrompt(true); return; }
    if (target.closest("[data-prompt-discard]") && state) { reloadPromptFromServer(); return; }
    const promptView = target.closest("[data-prompt-view]");
    if (promptView && state) {
      state.promptView = ["edit", "rules", "composition"].includes(promptView.dataset.promptView) ? promptView.dataset.promptView : "edit";
      render();
      return;
    }
    const trendMode = target.closest("[data-trend-mode]");
    if (trendMode && state) {
      state.tokenTrendMode = trendMode.dataset.trendMode === "hourly" ? "hourly" : "daily";
      render();
      return;
    }
    const breakdown = target.closest("[data-token-breakdown]");
    if (breakdown && state) {
      state.tokenBreakdown = breakdown.dataset.tokenBreakdown === "tasks" ? "tasks" : "providers";
      render();
      return;
    }
    const detail = target.closest("[data-token-detail]");
    if (detail && state) {
      state.tokenDetailView = ["daily", "providers", "tasks", "recent"].includes(detail.dataset.tokenDetail) ? detail.dataset.tokenDetail : "daily";
      render();
      return;
    }
    const period = target.closest("[data-period]");
    if (period && state) { state.period = period.dataset.period; state.selectedHour = ""; render(); return; }
    const source = target.closest("[data-token-source]");
    if (source && state && tokenSources.some(([key]) => key === source.dataset.tokenSource)) {
      state.tokenSource = source.dataset.tokenSource;
      state.traceFilter = null;
      state.selectedHour = "";
      render();
      return;
    }
    const hour = target.closest("[data-select-hour]");
    if (hour && state) {
      state.period = "date";
      state.selectedDate = hour.dataset.selectDay;
      state.selectedHour = hour.dataset.selectHour;
      state.callFilter = "all";
      state.showAllCalls = true;
      setMaintenanceView("recent");
      render();
      return;
    }
    const day = target.closest("[data-select-day]");
    if (day && state) { state.period = "date"; state.selectedDate = day.dataset.selectDay; state.selectedHour = ""; render(); return; }
    const category = target.closest("[data-diagnostic-category]");
    if (category && state) {
      state.category = category.dataset.diagnosticCategory;
      if (state.category === "image_generation") {
        state.imageAction = IMAGE_GENERATION_ACTION;
        state.imageTest = null;
        scheduleDiagnosticAutoClear();
      }
      render();
      return;
    }
    const severity = target.closest("[data-diagnostic-severity]");
    if (severity && state) { state.severity = severity.dataset.diagnosticSeverity; render(); return; }
    const filter = target.closest("[data-set-call-filter]");
    if (filter && state) { state.callFilter = filter.dataset.setCallFilter; state.showAllCalls = true; setMaintenanceView("recent"); render(); return; }
    if (target.closest("[data-clear-hour]") && state) { state.selectedHour = ""; setMaintenanceView("recent"); render(); return; }
    if (target.closest("[data-clear-diagnostic-results]") && state) { clearDiagnosticResults(); return; }
    const ranking = target.closest("[data-rank-kind]");
    if (ranking && state) { state.traceFilter = {kind: ranking.dataset.rankKind, key: ranking.dataset.rankKey}; state.callFilter = "all"; state.showAllCalls = true; setMaintenanceView("recent"); render(); return; }
    if (target.closest("[data-clear-trace]") && state) { state.traceFilter = null; setMaintenanceView("recent"); render(); return; }
    if (target.closest("[data-toggle-calls]") && state) { state.showAllCalls = !state.showAllCalls; render(); return; }
    if (target.closest("[data-export-tokens]")) { exportTokens(); return; }
    if (target.closest("[data-balance-refresh]")) { void refreshBalance(); return; }
    if (target.closest("[data-memory-start]")) { startMemoryDiagnostic(); return; }
    if (target.closest("[data-memory-stop]")) { stopMemoryDiagnostic(); return; }
    const focus = target.closest("[data-focus-panel]");
    if (focus && root) {
      const targetSelector = ({"task-audit": '[data-focus-target="task-audit"]', "test-zone": '[data-focus-target="test-zone"]',
        "budget-block": ".maint-budget-block", "storage-block": '[data-focus-target="storage-block"]',
        "identity-evidence": '[data-focus-target="identity-evidence"]',
        "delivery-evidence": '[data-focus-target="delivery-evidence"]', "proactive-evidence": '[data-focus-target="proactive-evidence"]',
        "proactive-test": '[data-focus-target="proactive-test"]',
        "debounce-evidence": '[data-focus-target="debounce-evidence"]',
        "prompt-evidence": '[data-focus-target="prompt-evidence"]', "cache-panel": ".maint-cache-panel"})[focus.dataset.focusPanel];
      const focusView = ({"budget-block": "overview", "cache-panel": "cache"})[focus.dataset.focusPanel] || "diagnostics";
      if (state && state.activeMaintenanceNav !== focusView) {
        setMaintenanceView(focusView);
        render();
      }
      const focusTarget = root.querySelector(targetSelector);
      const evidenceDrawer = focusTarget?.closest?.("details.maint-evidence-drawer");
      const routeDrawer = focusTarget?.closest?.("details.maint-task-audit");
      if (evidenceDrawer) evidenceDrawer.open = true;
      if (routeDrawer) routeDrawer.open = true;
      focusTarget?.scrollIntoView?.({behavior: "smooth", block: "center"});
      return;
    }
    if (target.closest("[data-refresh]")) { void refresh(); return; }
    if (target.closest("[data-provider-probe]")) { void probe(); return; }
    if (target.closest("[data-voice-test]")) { void testVoice(); return; }
    if (target.closest("[data-image-test]")) void testImage();
    if (target.closest("[data-storage-test]")) void testStorage();
    if (target.closest("[data-proactive-test]")) { requestProactiveConfirmation(); return; }
    if (target.closest("[data-proactive-cancel]")) { cancelProactiveConfirmation(); return; }
    if (target.closest("[data-proactive-confirm]")) void testProactive();
  }

  function change(event) {
    const target = event.target;
    if (!state) return;
    if (target.matches("[data-prompt-section]")) {
      if (state.promptSaving || state.promptReloading) return;
      if (promptDirty()) {
        state.promptMessage = "当前模板有未保存更改，请先保存或恢复继承值。";
        state.promptMessageKind = "warn";
        render();
        return;
      }
      const task = promptSelectedTask();
      if (target.value !== "task_prompt" && !task?.promptSections.some(section => section.key === target.value && section.editable)) return;
      state.promptSelectedSection = target.value;
      loadPromptDraft(promptEditTarget(task));
      state.promptMessage = "";
      state.promptMessageKind = "";
      render();
      return;
    }
    if (target.matches("[data-prompt-rule]")) { state.promptRuleKey = target.value; render(); return; }
    if (target.matches("[data-selected-date]")) {
      if (target.value) { state.selectedDate = target.value; state.period = "date"; state.selectedHour = ""; render(); }
    } else if (target.matches("[data-provider-select]")) { state.provider = target.value; state.test = null; scheduleDiagnosticAutoClear(); }
    else if (target.matches("[data-voice-provider]")) { clearVoiceAudio(); state.voiceProvider = target.value; state.voiceTest = null; scheduleDiagnosticAutoClear(); render(); }
    else if (target.matches("[data-voice-language]")) { clearVoiceAudio(); state.voiceLanguage = target.value; state.voiceTest = null; scheduleDiagnosticAutoClear(); render(); }
    else if (target.matches("[data-image-action]")) { state.imageAction = target.value; state.imageTest = null; scheduleDiagnosticAutoClear(); }
    else if (target.matches("[data-proactive-purpose]")) { state.proactivePurpose = target.value; state.proactiveTest = null; state.proactiveNotice = ""; render(); }
    else if (target.matches("[data-call-filter]")) { state.callFilter = target.value; state.showAllCalls = target.value !== "all"; render(); }
  }

  function input(event) {
    const target = event.target;
    if (!state) return;
    if (target.matches("[data-prompt-search]")) {
      const selection = [target.selectionStart, target.selectionEnd];
      state.promptQuery = target.value || "";
      render();
      const replacement = root?.querySelector?.("[data-prompt-search]");
      replacement?.focus?.();
      if (selection.every(Number.isInteger)) replacement?.setSelectionRange?.(...selection);
      return;
    }
    if (target.matches("[data-prompt-draft]")) {
      if (state.promptSaving || state.promptReloading) return;
      const selection = [target.selectionStart, target.selectionEnd];
      state.promptDraft = normalizePrompt(target.value);
      state.promptMessage = "";
      state.promptMessageKind = "";
      render();
      const replacement = root?.querySelector?.("[data-prompt-draft]");
      replacement?.focus?.();
      if (selection.every(Number.isInteger)) replacement?.setSelectionRange?.(...selection);
      return;
    }
    if (!target.matches("[data-call-search]")) return;
    const selection = [target.selectionStart, target.selectionEnd];
    state.callQuery = target.value || "";
    state.showAllCalls = Boolean(state.callQuery);
    render();
    const replacement = root?.querySelector?.("[data-call-search]");
    replacement?.focus?.();
    if (selection.every(Number.isInteger)) replacement?.setSelectionRange?.(...selection);
  }

  function cachePartHover(event) {
    if (!state || !root) return;
    const item = event.target?.closest?.("[data-part]");
    const section = item?.closest?.(".maint-cache-composition-section, .maint-cache-context-section");
    if (!section) return;
    if (event.type === "pointerover") {
      section.dataset.activePart = item.dataset.part;
      return;
    }
    const next = event.relatedTarget?.closest?.("[data-part]");
    if (next?.closest?.(".maint-cache-composition-section, .maint-cache-context-section") === section) {
      section.dataset.activePart = next.dataset.part;
      return;
    }
    delete section.dataset.activePart;
  }

  function unmount() {
    generation += 1;
    cacheMaintenanceState();
    if (state?.refreshTimer) window.clearInterval?.(state.refreshTimer);
    if (state?.memoryTimer != null) window.clearTimeout?.(state.memoryTimer);
    clearDiagnosticExpiry(state);
    clearVoiceAudio();
    if (root) {
      root.removeEventListener("click", click);
      root.removeEventListener("change", change);
      root.removeEventListener("input", input);
      root.removeEventListener("pointerover", cachePartHover);
      root.removeEventListener("pointerout", cachePartHover);
    }
    root = null;
    state = null;
    maintenanceScope = "";
  }

  function mount(element, options = {}) {
    unmount();
    root = element;
    if (!root) return;
    maintenanceScope = currentMaintenanceScope();
    generation += 1;
    state = {data: null, integrations: {}, error: "", loading: false, testing: false, voiceTesting: false, imageTesting: false,
      proactiveTesting: false, proactiveConfirming: false, proactiveNotice: "", proactivePurpose: "care", proactiveTest: null,
      test: null, voiceTest: null, imageTest: null, provider: "", voiceProvider: "", imageAction: "", voiceLanguage: "zh",
      storageTest: null, storageTesting: false, balance: null, balanceLoaded: false, balanceLoading: false,
      diagnosticNotice: "", diagnosticClearTimer: null, diagnosticResultExpiresAt: 0,
      memorySamples: [], memoryRunning: false, memorySampling: false, memoryComplete: false,
      memoryError: "", memoryStopReason: "", memoryTimer: null, memoryStartedAt: 0,
      tokenSource: "companion", tokenTrendMode: "daily", tokenBreakdown: "providers", tokenDetailView: "daily",
      activeMaintenanceNav: ["overview", "diagnostics", "cache", "efficiency", "recent", "prompts"].includes(options.activeView) ? options.activeView : "overview",
      onViewChange: typeof options.onViewChange === "function" ? options.onViewChange : null,
      period: "today", selectedDate: localDay(), selectedHour: "", category: "all", severity: "all", callFilter: "all",
      callQuery: "", traceFilter: null, showAllCalls: false, modelStatus: null, openReplySamples: new Set(),
      promptQuery: "", promptMode: "all", promptGroup: "all", promptSelectedKind: "",
      promptSelectedSection: "task_prompt", promptView: "edit", promptRuleKey: "",
      promptDraft: "", promptInitial: "", promptEditToken: "", promptInitialized: false,
      promptSaving: false, promptReloading: false, promptStale: false, promptMessage: "", promptMessageKind: "",
      promptOperationKey: "", promptOperationKind: ""};
    const cached = maintenanceCache.get(maintenanceScope);
    if (cached) Object.assign(state, cached);
    state.onViewChange?.(state.activeMaintenanceNav);
    root.addEventListener("click", click);
    root.addEventListener("change", change);
    root.addEventListener("input", input);
    root.addEventListener("pointerover", cachePartHover);
    root.addEventListener("pointerout", cachePartHover);
    if (typeof window.setInterval === "function") {
      state.refreshTimer = window.setInterval(() => {
        if ((typeof document === "undefined" || document.visibilityState === "visible") && state && !state.loading) void refresh();
      }, 45000);
    }
    if (hasDiagnosticResults(state)) {
      if (state.diagnosticResultExpiresAt > Date.now()) scheduleDiagnosticAutoClear(state, generation);
      else { state.diagnosticResultExpiresAt = 0; clearDiagnosticResults("auto", state); }
    }
    void refresh();
  }

  return {mount, unmount};
})();
