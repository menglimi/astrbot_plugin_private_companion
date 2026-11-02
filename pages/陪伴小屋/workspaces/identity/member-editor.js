"use strict";
(() => {
  const ROOT = "acquaintance";
  const MEMBER_SETTINGS_TIMEOUT_MS = 60000;
  const AMBIGUOUS_SAVE_CODES = new Set(["configuration_operation_incomplete"]);
  const keyOf = person => JSON.stringify([person.subject_id, person.platform, person.bot_id, person.account_id, person.role_id]);
  const routeKey = person => JSON.stringify([person.platform, person.bot_id, person.account_id, person.role_id]);
  const routeOf = person => Object.fromEntries(["platform", "bot_id", "account_id", "role_id"].map(key => [key, person[key]]));
  const parameters = person => ({subject_id: person.subject_id, ...routeOf(person)});
  const states = new Map();
  let current = null;
  const fieldLabels = {default_nickname: "对用户的称呼", user_gender: "性别", user_birthday: "生日或纪念日", user_occupation: "职业",
    role_relation: "关系定位", interaction: "相处方式", extra: "其他补充"};
  const replyLabels = {
    enabled: "启用消息分段", streaming: "启用模型流式传输", streaming_mode: "流式发送方式",
    llm_controlled: "由模型决定分段边界", rule_mode: "规则分段方式", threshold: "分段字数阈值",
    min_segment_chars: "短段合并参考字数", max_segments: "最多消息条数", part_gap_ms: "固定段间隔（毫秒）",
    part_gap_mode: "段间隔方式", part_gap_min_ms: "间隔下限（毫秒）", part_gap_max_ms: "间隔上限（毫秒）",
    part_gap_log_base: "长度调节强度", prompt: "分段提示词", split_words: "分段标记词",
    match_width_variants: "兼容全半角标点", rule_regex: "分段正则表达式",
    style_prompt: "表达风格补充", enable_humanized_expression: "启用日常表达", humanized_expression_prompt: "日常表达提示词",
    content_cleanup_enabled: "启用文字清理", content_cleanup_mode: "清理方式", content_cleanup_scope: "清理范围",
    content_cleanup_rule: "清理正则", content_cleanup_words: "清理词列表",
    content_replacement_enabled: "启用文字替换", content_replacements: "替换规则",
    reply_quote_enabled: "引用触发消息", reply_quote_first_part_only: "只在首段引用", reply_quote_skip_chars: "短回复不引用字数",
    image_position: "图片位置", reply_mention_speaker_enabled: "群聊提及发言者", forward_message_adaptation_enabled: "整理合并转发",
    voice_enabled: "启用语音回复", voice_trigger_mode: "语音触发方式", voice_generation_mode: "朗读内容",
    voice_segment_mode: "朗读段落", voice_delivery_mode: "发送形式", voice_position: "语音位置",
    voice_language: "语音语言", voice_foreign_text_mode: "外语文字处理",
    voice_provider_id: "通用语音扩展", voice_zh_provider_id: "中文语音扩展",
    voice_ja_provider_id: "日语语音扩展", voice_en_provider_id: "英语语音扩展",
    voice_private_zh_provider_id: "私聊 · 中文语音扩展",
    voice_private_ja_provider_id: "私聊 · 日语语音扩展",
    voice_private_en_provider_id: "私聊 · 英语语音扩展",
    voice_group_zh_provider_id: "群聊 · 中文语音扩展",
    voice_group_ja_provider_id: "群聊 · 日语语音扩展",
    voice_group_en_provider_id: "群聊 · 英语语音扩展",
    voice_probability: "语音触发概率", voice_cooldown_seconds: "语音冷却（秒）",
    voice_max_speech_chars: "单段朗读上限", voice_timeout_seconds: "合成超时（秒）",
    voice_trigger_keywords: "语音触发词", voice_extra_prompt: "语音表达偏好",
    enable_private_image_self_recognition: "启用图片转述增强",
    private_image_self_recognition_hint: "图片自我识别线索",
    enable_private_image_vision_cache: "复用重复图片转述", private_image_vision_cache_max_items: "图片转述缓存上限",
    private_image_vision_custom_prompt: "图片转述提示词补充", private_image_vision_max_chars: "图片转述长度上限",
    private_image_vision_wait_seconds: "图片转述等待时间（秒）",
    private_image_provider_failure_cooldown_seconds: "图片识别失败冷却（秒）",
    video_understanding_enabled: "启用视频理解", video_understanding_max_chars: "视频转述长度上限",
    video_understanding_wait_seconds: "视频转述等待时间（秒）", video_self_recognition_hint: "视频自我识别线索",
    video_understanding_prompt: "视频理解提示词补充", file_understanding_enabled: "启用文件理解",
    file_understanding_format_groups: "可读取文件格式", file_understanding_max_size_mb: "文件大小上限（MB）",
    file_understanding_max_chars: "文件正文长度上限", file_understanding_timeout_seconds: "文件读取等待时间（秒）",
    file_understanding_prompt: "文件阅读提示词补充",
    image_understanding_private_enabled: "私聊图片理解", astrbot_animated_image_understanding_enabled: "AstrBot 原生动图理解优化",
    video_understanding_private_enabled: "私聊视频理解",
    file_understanding_private_enabled: "私聊文件理解",
  };
  const replyChoices = {
    streaming_mode: {buffered: "收齐后分段发送", incremental: "边生成边发送"},
    rule_mode: {words: "词列表", regex: "正则表达式", sentences: "按句子", paragraphs: "按段落"},
    part_gap_mode: {fixed: "固定", random: "随机", log: "按文本长度"},
    content_cleanup_mode: {words: "按清理词", regex: "按正则"},
    content_cleanup_scope: {leading: "句首", middle: "句中", trailing: "句尾"},
    voice_trigger_mode: {always: "每轮", request: "按请求", adaptive: "模型择句"},
    voice_generation_mode: {plain: "朗读正文", model_tag: "模型语音标签"},
    voice_segment_mode: {each: "每一段", first: "仅首段", last: "仅末段"},
    voice_delivery_mode: {voice_and_text: "文字与语音", voice_only: "仅语音"},
    voice_position: {before_text: "文字前", after_text: "文字后", after_media: "所有组件后"},
    voice_language: {zh: "中文", ja: "日语", en: "英语"},
    voice_foreign_text_mode: {original: "原文", translation: "中文译文", bilingual: "双语"},
    image_position: {after_text: "文字后", before_text: "文字前"},
  };
  /* 主回复页已经停用「整段回复」，并把旧值 all 视为句中去重。
     成员页复用同一份回复设置，历史值读回时也要先归一，否则下拉框会
     多出一个原始英文选项，甚至在下一次修改其他字段时把旧值继续写回。 */
  const normalizeReplyField = (name, value) => name === "content_cleanup_scope"
    && String(value ?? "").trim().toLowerCase() === "all" ? "middle" : value;
  const normalizeReplyDraft = draft => {
    if (draft && Object.hasOwn(draft, "content_cleanup_scope")) {
      draft.content_cleanup_scope = normalizeReplyField(
        "content_cleanup_scope", draft.content_cleanup_scope);
    }
    return draft;
  };
  const contactPurposeGroups = [
    ["关系", [["affection", "想念与亲近"], ["care", "情绪关怀"], ["play", "轻松互动"],
      ["repair", "关系修复"], ["chat", "日常话题"]]],
    ["日程", [["greeting", "问候与纪念"], ["followup", "接回旧话题"], ["invitation", "共同活动邀请"],
      ["reminder", "约定与提醒"], ["meal", "饭点话题"]]],
    ["分享", [["life", "分享自己的生活"], ["creation", "作品与阅读"], ["news", "外部资讯"],
      ["environment", "环境与状态变化"], ["screen", "已授权屏幕观察"]]],
  ];
  const replyGroups = [
    {title: "表达风格", fields: ["style_prompt", "enable_humanized_expression", "humanized_expression_prompt"]},
    {title: "分段与节奏", fields: ["enabled", "streaming", "streaming_mode", "llm_controlled", "rule_mode",
      "threshold", "min_segment_chars", "max_segments", "part_gap_ms", "part_gap_mode", "part_gap_min_ms",
      "part_gap_max_ms", "part_gap_log_base", "prompt", "split_words", "match_width_variants", "rule_regex"]},
    {title: "文字处理", fields: ["content_cleanup_enabled", "content_cleanup_mode", "content_cleanup_scope",
      "content_cleanup_rule", "content_cleanup_words", "content_replacement_enabled", "content_replacements"]},
    {title: "语音回复", fields: ["voice_enabled", "voice_trigger_mode", "voice_generation_mode", "voice_segment_mode",
      "voice_delivery_mode", "voice_position", "voice_language", "voice_foreign_text_mode", "voice_provider_id",
      "voice_zh_provider_id", "voice_ja_provider_id", "voice_en_provider_id",
      "voice_private_zh_provider_id", "voice_private_ja_provider_id", "voice_private_en_provider_id",
      "voice_probability", "voice_cooldown_seconds", "voice_max_speech_chars", "voice_timeout_seconds",
      "voice_trigger_keywords", "voice_extra_prompt"]},
    {title: "图片理解", fields: ["image_understanding_private_enabled", "astrbot_animated_image_understanding_enabled",
      "enable_private_image_self_recognition", "private_image_self_recognition_hint",
      "enable_private_image_vision_cache", "private_image_vision_cache_max_items",
      "private_image_vision_custom_prompt", "private_image_vision_max_chars", "private_image_vision_wait_seconds",
      "private_image_provider_failure_cooldown_seconds"]},
    {title: "视频理解", fields: ["video_understanding_private_enabled", "video_understanding_enabled",
      "video_understanding_max_chars", "video_understanding_wait_seconds", "video_self_recognition_hint",
      "video_understanding_prompt"]},
    {title: "文件理解", fields: ["file_understanding_private_enabled", "file_understanding_enabled",
      "file_understanding_format_groups", "file_understanding_max_size_mb", "file_understanding_max_chars",
      "file_understanding_timeout_seconds", "file_understanding_prompt"]},
    {title: "引用与消息组件", fields: ["reply_quote_enabled", "reply_quote_first_part_only", "reply_quote_skip_chars",
      "forward_message_adaptation_enabled", "reply_mention_speaker_enabled", "image_position"]},
  ];
  const tierLabels = {standard: "普通用户", important: "重要用户", core: "核心用户"};
  const tierEyebrow = {standard: "STANDARD · 普通", important: "IMPORTANT · 重要", core: "CORE · 核心"};
  // 印鉴缩写：名片右侧那枚超大水印，是这一屏的视觉锚点。
  const tierSeal = {standard: "STD", important: "IMP", core: "CORE"};
  const tierStep = {standard: "01", important: "02", core: "03"};
  // 渲染顺序就是视觉顺序：核心在最上，逐级向下收窄。
  const tierOrder = ["core", "important", "standard"];
  // 名字来源：后端 directory_display_name 回传的 basis，用于说明这一行名字是怎么来的。
  const nameBasisLabels = {administrator: "管理员备注", platform: "平台昵称", legacy: "已保存称呼", account: "账号回退"};
  const nameBasisOf = person => person.name_basis || (person.admin_nickname ? "administrator"
    : person.display_name && person.display_name !== person.native_account_id ? "platform" : "account");
  const tierHelp = {standard: "普通用户，没有管理员权限。", important: "重要用户，可用普通功能；管理操作仍需核心身份。",
    core: "核心管理员，可管理本体配置、记忆及能力授权。"};
  const permissionLabels = {"conversation.reply": "允许私聊对话", "proactive.contact": "允许主动联系",
    "role.state.intervene": "允许影响角色状态", "calendar.intervene": "允许调整角色日程",
    "role.rest.wake": "允许休息时执行干预"};
  const permissionsHelp = {"conversation.reply": "关闭后按当前拒绝文案处理私聊。", "proactive.contact": "按该成员的联系偏好与节奏主动开口。",
    "role.state.intervene": "允许此窗口请求复核角色自己的即时状态；仍按人格与实际回执判断。",
    "calendar.intervene": "允许此窗口请求创建、移动或取消角色自己的计划。",
    "role.rest.wake": "与状态或日程干预授权同时开启时，可在角色休息期间执行干预。"};
  const permissionIcons = {"conversation.reply": "message-circle", "proactive.contact": "bell-ring",
    "role.state.intervene": "heart-pulse", "calendar.intervene": "calendar-clock", "role.rest.wake": "alarm-clock"};
  const tierIcons = {standard: "user", important: "star", core: "shield-check"};
  const capabilityLabels = {text: "文本模型", "message.delivery": "旧投递授权", "configuration.read": "读取配置",
    "screen.read": "读取屏幕", "screen.process": "处理屏幕", "extension.tool.invoke": "调用拓展工具"};
  const stateLabels = {active: "已允许", off: "未授权", revoked: "已关闭", expired: "已过期", pending: "待生效"};
  const capabilityLabel = name => permissionLabels[name] || capabilityLabels[name] || "其他能力";
  const capabilityGuidance = {
    text: {source: "模型与连接", detail: "文本模型由当前运行实例统一选择，不能在成员页单独关闭。",
      target: {tab: "models", label: "打开模型与连接"}},
    "configuration.read": {source: "账号与会话", detail: "这是运行实例读取配置的基础授权，由插件自动管理。",
      target: {tab: "host", label: "打开账号与会话"}},
    "screen.read": {source: "感知 · 设备与授权", detail: "屏幕读取按当前会话和拓展包统一授权。",
      target: {tab: "perception", subtab: "config-screen", label: "打开设备与授权"}},
    "screen.process": {source: "感知 · 设备与授权", detail: "屏幕处理按当前会话和拓展包统一授权。",
      target: {tab: "perception", subtab: "config-screen", label: "打开设备与授权"}},
    "extension.tool.invoke": {source: "感知 · 设备与授权", detail: "拓展工具调用由当前会话的能力包统一授权。",
      target: {tab: "perception", subtab: "config-screen", label: "打开设备与授权"}},
    "message.delivery": {source: "消息投递", detail: "兼容记录；V2 正式私聊投递不使用此项，无需配置。"},
  };
  const failureLabels = {configuration_revision_conflict: "设置已有更新，草稿已保留，请核对后重新保存。",
    authorization_revision_conflict: "权限已有更新，已读取最新状态。", identity_revision_conflict: "身份已有更新，已读取最新状态。",
    core_access_required: "当前账号没有管理权限。", dashboard_identity_required: "请重新登录 AstrBot 管理界面。",
    acquaintance_route_required: "请选择一个具体的 Bot、账号和角色。", acquaintance_route_unknown: "这个私聊窗口已变化，请重新读取目录。",
    acquaintance_window_unknown: "此角色的私聊窗口尚未建立。", acquaintance_subject_unknown: "此成员尚未建立私聊窗口。",
    supervisor_fenced: "运行实例当前未启动，启动后即可保存资料。", staging_not_initialized: "请先加载当前运行实例。",
    acquaintance_person_unknown: "此成员还没有熟人记录，暂时不能设置面板备注。",
    person_nickname_revision_conflict: "成员记录已有更新，请重新读取后保存备注。",
    invalid_configuration_value: "请核对字段长度和数值范围。", relationship_profile_configuration_invalid: "请核对资料长度，总字数不超过 3000。",
    reply_configuration_invalid: "请核对回复设置的取值范围。",
    configuration_operation_incomplete: "服务端仍在恢复原保存回执；可安全重试同一操作，不会重复写入。"};
  const memberSectionLabels = {profile: "关系资料", person: "熟人卡", person_learning: "熟人卡学习",
    contact: "主动联系", growth: "角色成长", expression: "表达学习", reply: "回复设置"};
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const nameOf = person => person.display_name || person.native_account_id || "未命名成员";
  // 头像用人物肖像，不取名字首字；剪影颜色继承所在容器的等级色（currentColor）。
  const PORTRAIT_SVG = '<svg viewBox="0 0 40 40" aria-hidden="true" focusable="false">'
    + '<path fill="currentColor" d="M20 4.6a7.6 7.6 0 0 1 7.6 7.6 7.6 7.6 0 0 1-7.6 7.6 7.6 7.6 0 0 1-7.6-7.6A7.6 7.6 0 0 1 20 4.6Z'
    + 'M20 21.8c8.4 0 15.2 5.6 15.2 12.5 0 1.2-1 2.3-2.2 2.3H7c-1.2 0-2.2-1.1-2.2-2.3 0-6.9 6.8-12.5 15.2-12.5Z"/></svg>';
  const routeEntries = person => Array.isArray(person?.display_routes) ? person.display_routes
    : person?.private_target === true && person?.settings_available === true ? [person] : [];
  const activeRoute = () => current?.route
    ? routeEntries(current.person).find(route => routeKey(route) === routeKey(current.route)) || current.route : null;
  const activePerson = () => {
    if (!current) return null;
    const person = {...current.person, ...(activeRoute() || {})};
    // Route snapshots keep revision and authorization scope; the shared card keeps
    // display fields current after a save.
    for (const key of ["display_name", "identity_tier"]) {
      if (key in current.person) person[key] = current.person[key];
    }
    if (Object.prototype.hasOwnProperty.call(current.person, "admin_nickname")) {
      person.admin_nickname = current.person.admin_nickname;
    }
    return person;
  };
  const routeLabel = route => [Array.isArray(route.persona_ids) ? route.persona_ids.map(value => value || "默认人格").join("、") : "当前人格",
    route.role_id ? `内部归属 ${route.role_id}` : ""].filter(Boolean).join(" · ");
  const personaLabel = person => Array.isArray(person?.persona_ids) && person.persona_ids.length
    ? person.persona_ids.map(value => value || "默认人格").join("、") : person?.role_name || "当前角色";
  const accountLabel = person => person.account_name || (/^\d+$/.test(String(person.account_id || ""))
    ? `QQ ${person.account_id}` : "当前 Bot 账号");
  const operation = () => "member:" + Date.now().toString(36) + ":" + Math.random().toString(36).slice(2, 10);
  const shortTime = value => value ? new Intl.DateTimeFormat("zh-CN", {month: "numeric", day: "numeric"}).format(new Date(value)) : "暂无";
  function unwrap(response) {
    const envelope = ["ok", "error"].includes(response?.status) && response?.data !== undefined ? response
      : ["ok", "error"].includes(response?.data?.status) && response?.data?.data !== undefined ? response.data : null;
    if (envelope?.status === "error") throw Object.assign(new Error(envelope.reason || envelope.message),
      {code: envelope.data?.code || envelope.message || envelope.error?.code, confirmedRejection: true});
    const data = envelope ? envelope.data : response;
    if (!data || typeof data !== "object") throw new Error("invalid_response");
    return data;
  }
  function errorText(error, fallback = "操作未完成，请核对当前状态后重试。") {
    const code = error?.code;
    const label = failureLabels[code];
    if (label) return label;
    // Keep the machine code visible even when this page has no translation:
    // the panel bridge sometimes only forwards ``message``.
    return code ? `${fallback.replace(/。$/, "")}（${code}）` : fallback;
  }
  function sectionErrorText(state, section) {
    const failure = state.sectionErrors?.[section];
    if (!failure) return "";
    const code = typeof failure === "string" ? failure : failure?.code;
    const label = memberSectionLabels[section] || "成员配置";
    return `${label}读取失败${code ? `（${code}）` : ""}，可以重新读取。`;
  }
  function dirtySectionNames(state) {
    return [...new Set([...state.dirty].map(token => token.slice(0, token.indexOf("."))))];
  }
  function hasUnreadDirtySections(state) {
    return dirtySectionNames(state).some(section => state.sectionErrors[section] || !state.sections[section]);
  }
  function stateFor(person) {
    const key = keyOf(person);
    if (!states.has(key)) states.set(key, {pane: "access", drafts: {}, sections: {}, authorization: null, resetToRole: new Set(),
      contactResetToInherit: new Set(),
      settingsError: "", sectionErrors: {}, authError: "", loading: false, loaded: false, busy: false, reconciling: false, dirty: new Set(),
      nickname: person.admin_nickname || "", tier: person.identity_tier || "standard", message: "", failed: false});
    return states.get(key);
  }
  const redraw = state => { if (current && stateFor(activePerson()) === state) render(); };
  function notify(state, message, failed = false) {
    state.message = message; state.failed = failed; redraw(state);
  }
  function adoptSettings(state, result, preserve, replaceSnapshot = false) {
    if (replaceSnapshot) state.sections = {};
    state.sectionErrors = result.section_errors && typeof result.section_errors === "object"
      ? {...result.section_errors} : {};
    state.tts = result.tts && typeof result.tts === "object" ? result.tts : state.tts || null;
    for (const [section, value] of Object.entries(result.sections || {})) {
      const retained = {};
      if (preserve) for (const [field, draft] of Object.entries(state.drafts[section] || {})) {
        if (state.dirty.has(section + "." + field)) retained[field] = draft;
      }
      state.sections[section] = value;
      const draft = {...value.settings, ...retained};
      if (section === "reply") normalizeReplyDraft(draft);
      state.drafts[section] = draft;
      if (section === "reply"
          && draft.content_cleanup_scope !== value.settings.content_cleanup_scope) {
        state.dirty.add("reply.content_cleanup_scope");
      }
    }
    for (const section of Object.keys(state.sectionErrors)) delete state.sections[section];
    if (!preserve) { state.dirty.clear(); state.resetToRole.clear(); state.contactResetToInherit.clear(); }
  }
  async function load(person, {preserve = true} = {}) {
    const state = stateFor(person), services = current.services;
    if (state.loading) return;
    state.loading = true; state.loaded = true;
    state.settingsError = state.authError = "";
    state.sectionErrors = {};
    redraw(state);
    const results = await Promise.allSettled([
      services.bridge.apiGet(ROOT + "/authorization", parameters(person), {timeoutMs: MEMBER_SETTINGS_TIMEOUT_MS}),
      services.bridge.apiGet(ROOT + "/settings", parameters(person), {timeoutMs: MEMBER_SETTINGS_TIMEOUT_MS}),
    ]);
    const [auth, settings] = results;
    try {
      if (auth.status === "rejected") throw auth.reason;
      state.authorization = unwrap(auth.value);
      state.tier = state.authorization.identity_tier || "standard";
      services.onChanged?.(person, {identity_tier: state.tier});
    } catch (error) {
      state.authError = errorText(error, "权限读取失败，请重新读取以核对状态。");
      state.authorization = null;
    }
    try {
      if (settings.status === "rejected") throw settings.reason;
      const data = unwrap(settings.value);
      if (!data.sections || typeof data.sections !== "object") throw Object.assign(new Error("invalid_response"), {code: "invalid_response"});
      adoptSettings(state, data, preserve, true);
    } catch (error) {
      const draftNote = state.dirty.size ? "未保存草稿仍保留；" : "";
      state.sections = {}; state.sectionErrors = {};
      state.settingsError = errorText(error,
        `成员配置读取失败。${draftNote}当前无法确认已保存值，请重新读取。`);
    }
    state.loading = false;
    redraw(state);
  }
  function heading(title, meta) {
    const head = element("div", undefined, "member-section-heading");
    const mark = element("span", undefined, "member-section-mark");
    mark.setAttribute("aria-hidden", "true");
    head.append(mark, element("h4", title));
    if (meta) head.append(element("small", meta));
    return head;
  }
  function block(title, meta) {
    const root = element("section", undefined, "member-block");
    const head = element("header", undefined, "member-block-head");
    const mark = element("span", undefined, "member-block-mark");
    mark.setAttribute("aria-hidden", "true");
    head.append(mark, element("h4", title));
    if (meta) head.append(element("small", meta));
    const body = element("div", undefined, "member-block-body");
    root.append(head, body);
    return {root, body};
  }
  // 概览条：每个分区顶部的「标题 + 说明 / 三格关键指标」，与 V1 用户页的
  // user-view-overview 同构，用来在切换分区时先给出这一屏的关键读数。
  function overview(title, description, stats, tier) {
    const root = element("section", undefined, "member-view-overview");
    if (tier) root.dataset.tier = tier;
    const heading = element("div", undefined, "member-view-heading");
    const mark = element("span", undefined, "member-view-mark");
    mark.setAttribute("aria-hidden", "true");
    const copy = element("div", undefined, "member-view-copy");
    copy.append(element("h3", title), element("p", description));
    heading.append(mark, copy);
    const cells = element("div", undefined, "member-view-stats");
    for (const [label, value, tone] of stats || []) {
      const cell = element("span", undefined, "member-view-stat");
      if (tone) cell.dataset.tone = tone;
      cell.title = String(value ?? "");
      cell.append(element("small", label), element("b", String(value ?? "—")));
      cells.append(cell);
    }
    root.append(heading, cells);
    return root;
  }
  // 键值对事实块：只读地呈现「当前实际生效的值」，与可编辑表单区分开。
  function facts(pairs) {
    const list = element("dl", undefined, "member-facts");
    for (const [label, value] of pairs) {
      const row = element("div", undefined, "member-facts-row");
      row.append(element("dt", label), element("dd", value === "" || value == null ? "—" : String(value)));
      list.append(row);
    }
    return list;
  }
  const onOff = value => value ? "已启用" : "未启用";
  const administratorState = (state, person) => {
    const saved = state.authorization?.identity_tier || person.identity_tier || "standard";
    const selected = state.tier || saved;
    const pending = selected !== saved;
    if (pending && selected === "core") return {label: "待保存 · 将开启", state: "pending", enabled: true};
    if (pending && saved === "core") return {label: "待保存 · 将关闭", state: "pending", enabled: false};
    if (selected === "core") return {label: "已开启 · 核心管理员", state: "active", enabled: true};
    return {label: "未开启", state: "off", enabled: false};
  };
  const settingOf = (state, section, name) => state.sections?.[section]?.settings?.[name];
  function findNamedControl(root, name) {
    const pending = [root];
    while (pending.length) {
      const item = pending.pop();
      if (item?.name === name) return item;
      pending.push(...(item?.children || []));
    }
    return null;
  }
  const replyVisibilityFields = new Set(["streaming", "llm_controlled", "rule_mode", "content_cleanup_enabled",
    "content_cleanup_mode", "content_replacement_enabled", "reply_quote_enabled", "voice_enabled",
    "enable_private_image_self_recognition", "video_understanding_enabled", "file_understanding_enabled"]);
  function updateDraft(state, section, field, value) {
    if (state.pendingSave) return;
    const focusedName = section === "reply" && replyVisibilityFields.has(field)
      ? document.activeElement?.name : "";
    state.drafts[section] ||= {};
    state.drafts[section][field] = value;
    const token = section + "." + field;
    if (["reply", "person", "person_learning"].includes(section)) state.resetToRole.delete(field);
    if (section === "contact") state.contactResetToInherit.delete(field);
    if (value === state.sections[section]?.settings?.[field]) state.dirty.delete(token);
    else state.dirty.add(token);
    state.pendingSave = null;
    current?.panel.querySelectorAll(".member-savebar").forEach(bar => {
      const replacement = savebar(state);
      bar.replaceChildren(...replacement.children);
    });
    if (section === "reply" && replyVisibilityFields.has(field)) {
      redraw(state);
      if (focusedName) findNamedControl(current?.panel, focusedName)?.focus?.({preventScroll: true});
    }
  }
  function localDateTimeValue(value) {
    const date = new Date(value);
    if (!value || Number.isNaN(date.getTime())) return "";
    return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  }
  function absoluteDateTimeValue(value) {
    const date = new Date(value);
    return value && !Number.isNaN(date.getTime()) ? date.toISOString() : "";
  }
  function field(state, section, name, label, {rows = 0, placeholder = "", suffix = "", choices = null,
    inputType = ""} = {}) {
    const definition = state.sections[section]?.fields?.[name] || {};
    const value = state.drafts[section]?.[name] ?? "";
    const boolean = definition.kind === "boolean" || typeof value === "boolean";
    const wrapper = element("label", undefined, boolean ? "member-switch-row" : "member-field");
    const caption = element("span", undefined, "member-field-caption");
    caption.append(element("span", label));
    if (suffix) caption.append(element("small", suffix));
    const options = choices || (section === "reply" ? replyChoices[name] : null);
    const input = element(rows ? "textarea" : options ? "select" : "input");
    input.name = name;
    input.setAttribute("aria-label", label);
    input.disabled = state.busy || state.loading || !!state.sectionErrors[section] || !!state.settingsError
      || !!state.pendingSave || !state.sections[section];
    if (boolean) { input.type = "checkbox"; input.checked = value; input.setAttribute("role", "switch"); }
    else if (options) {
      for (const [optionValue, text] of Object.entries(options)) {
        const option = element("option", text); option.value = optionValue; option.selected = optionValue === String(value);
        input.append(option);
      }
      if (!Object.hasOwn(options, String(value))) {
        const option = element("option", String(value)); option.value = String(value); option.selected = true; input.append(option);
      }
    } else {
      if (!rows) input.type = inputType || (["integer", "probability", "weight"].includes(definition.kind) || typeof value === "number" ? "number"
        : ["quiet_start", "quiet_end"].includes(name) ? "time" : "text");
      input.value = inputType === "datetime-local" ? localDateTimeValue(value) : value;
      input.placeholder = placeholder;
      if (rows) input.rows = rows;
      if (definition.maximum_chars) input.maxLength = definition.maximum_chars;
      if (definition.minimum != null) input.min = definition.minimum;
      if (definition.maximum != null) input.max = definition.maximum;
      if (input.type === "number") input.step = definition.decimal_places != null ? String(10 ** -definition.decimal_places) : "1";
      if (input.type === "datetime-local") input.step = "60";
    }
    input.addEventListener(boolean || options ? "change" : "input", () => updateDraft(state, section, name,
      boolean ? input.checked : input.type === "number" ? (input.value === "" ? "" : Number(input.value))
        : input.type === "datetime-local" ? absoluteDateTimeValue(input.value) : input.value));
    wrapper.append(caption, input);
    return wrapper;
  }
  function retry(state, person, message) {
    const row = element("div", undefined, "member-load-error");
    const uncertainSave = Boolean(state.pendingSave);
    const button = element("button", uncertainSave ? "核对并重试保存" : "重新读取", "secondary");
    button.type = "button"; button.disabled = state.loading || state.busy;
    button.addEventListener("click", () => uncertainSave
      ? verifyAndRetrySave(person, state) : load(person));
    row.append(element("p", message), button);
    return row;
  }
  function closeDialog({restoreFocus = true} = {}) {
    const open = current?.panel.querySelector(".member-dialog-host");
    if (!open) return;
    open.remove();
    if (current) current.card = false;
    const trigger = current?.panel.querySelector('[data-card-trigger="true"]');
    if (trigger) trigger.setAttribute("aria-expanded", "false");
    if (restoreFocus) trigger?.focus({preventScroll: true});
  }
  // 熟人卡只管理自动积累的互动记忆，不承载人工关系设定。
  function openCard(state, person) {
    closeDialog({restoreFocus: false});
    if (!current) return;
    current.card = true;
    const trigger = current.panel.querySelector('[data-card-trigger="true"]');
    if (trigger) trigger.setAttribute("aria-expanded", "true");
    const host = element("div", undefined, "member-dialog-host");
    host.setAttribute("role", "dialog");
    host.setAttribute("aria-modal", "true");
    host.setAttribute("aria-label", "熟人卡 · " + nameOf(person));
    const panel = element("article", undefined, "member-dialog");
    panel.dataset.tier = state.authorization?.identity_tier || person.identity_tier || "standard";
    panel.setAttribute("aria-labelledby", "member-dialog-heading");
    const head = element("header", undefined, "member-dialog-head");
    const copy = element("div", undefined, "member-dialog-copy");
    const title = element("h3", "熟人卡"); title.id = "member-dialog-heading";
    copy.append(element("span", "ACQUAINTANCE CARD", "member-dialog-eyebrow"),
      title, element("p", `${nameOf(person)} · ${personaLabel(person)}`));
    const close = element("button", undefined, "member-dialog-close"); close.type = "button";
    close.setAttribute("aria-label", "关闭熟人卡"); close.title = "关闭";
    close.innerHTML = '<svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">'
      + '<path d="M5.5 5.5l9 9M14.5 5.5l-9 9" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>';
    close.addEventListener("click", closeDialog);
    head.append(copy, close);
    const body = element("div", undefined, "member-dialog-body");
    body.append(...cardContent(state, person));
    panel.append(head, body);
    const foot = element("footer", undefined, "member-dialog-foot");
    foot.append(savebar(state));
    panel.append(foot);
    host.append(panel);
    host.addEventListener("click", event => { if (event.target === host) closeDialog(); });
    host.addEventListener("keydown", event => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        closeDialog();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = [...panel.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled), [tabindex]:not([tabindex="-1"])')];
      if (!focusable.length) return;
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first.focus();
      }
    });
    current.panel.append(host);
    window.lucide?.createIcons();
    close.focus?.();
  }
  function cardContent(state, person) {
    const memory = block("互动记忆", personaLabel(person));
    memory.body.append(facts([
      ["人格归属", personaLabel(person)],
      ["持续积累", state.sectionErrors.person_learning ? "暂未读取" : onOff(settingOf(state, "person_learning", "acquaintance_learning_enabled"))],
      ["往来消息", `${person.total?.msg_count ?? 0} 条`],
    ]));
    const usage = block("角色默认与个人设置", "");
    if (state.sectionErrors.person) usage.body.append(retry(state, person, sectionErrorText(state, "person")));
    else usage.body.append(field(state, "person", "acquaintance_enabled", "使用熟人卡"),
      overrideStatus(state, "person", "acquaintance_enabled"),
      resetToRoleButton(state, "person", "acquaintance_enabled"),
      field(state, "person", "acquaintance_card_chars", "熟人卡字数上限", {suffix: "字符"}),
      overrideStatus(state, "person", "acquaintance_card_chars"),
      resetToRoleButton(state, "person", "acquaintance_card_chars"));
    if (state.sectionErrors.person_learning) usage.body.append(retry(state, person, sectionErrorText(state, "person_learning")));
    else usage.body.append(field(state, "person_learning", "acquaintance_learning_enabled", "持续更新熟人卡"),
      overrideStatus(state, "person_learning", "acquaintance_learning_enabled"));
    const updated = block("最近互动", "");
    updated.body.append(facts([["最近往来", shortTime(person.total?.last_seen)],
      ["首次往来", shortTime(person.total?.first_seen)],
      ["记忆更新", shortTime(person.updated_at)]]));
    return [memory.root, usage.root, updated.root];
  }
  function resetToRoleButton(state, section, name) {
    const provenance = state.sections[section]?.provenance?.[name];
    if (!provenance?.explicit) return element("span", undefined, "member-reset-placeholder");
    const button = element("button", "恢复角色默认", "member-reset-field");
    button.type = "button";
    button.disabled = state.busy || state.loading || !!state.sectionErrors[section] || !!state.settingsError || !!state.pendingSave;
    button.addEventListener("click", () => {
      if (state.pendingSave) return;
      state.drafts[section][name] = state.sections[section].inherited_settings[name];
      state.resetToRole.add(name);
      state.dirty.add(section + "." + name);
      state.pendingSave = null;
      redraw(state);
    });
    return button;
  }
  function overrideStatus(state, section, name) {
    const provenance = state.sections[section]?.provenance?.[name] || {};
    const label = section === "person_learning" ? "仅对此用户生效"
      : provenance.legacy ? "历史窗口覆盖"
      : provenance.explicit ? "仅此用户覆盖" : "跟随角色默认";
    return element("span", label, "member-override-state");
  }
  function relationshipPane(state, person) {
    const pane = element("div", undefined, "member-relationship-pane");
    const profile = state.sections?.profile?.settings || {};
    const core = (state.authorization?.identity_tier || person.identity_tier) === "core";
    pane.append(overview("人工关系资料", personaLabel(person), [
      ["身份", tierLabels[state.authorization?.identity_tier || person.identity_tier] || "普通用户"],
      ["关系", profile.role_relation || "未设置"],
      ["称呼", profile.default_nickname || "未设置"],
    ], state.authorization?.identity_tier || person.identity_tier));
    const factsBlock = block("关系设定", "");
    if (core) {
      factsBlock.body.append(facts([
        ["对用户的称呼", profile.default_nickname], ["关系定位", profile.role_relation],
        ["性别", profile.user_gender], ["生日/纪念日", profile.user_birthday],
        ["职业", profile.user_occupation], ["相处方式", profile.interaction], ["其他补充", profile.extra],
      ]));
    } else {
      const naming = element("div", undefined, "member-fields");
      for (const name of ["default_nickname", "role_relation", "user_gender", "user_birthday", "user_occupation"])
        naming.append(field(state, "profile", name, fieldLabels[name]));
      factsBlock.body.append(naming,
        field(state, "profile", "interaction", fieldLabels.interaction, {rows: 4}),
        field(state, "profile", "extra", fieldLabels.extra, {rows: 3}));
    }
    pane.append(factsBlock.root);
    return pane;
  }
  function directoryRemark(state, person) {
    const remark = block("目录备注", "");
    const label = element("label", undefined, "member-field");
    label.append(element("span", "目录显示名称"));
    const input = element("input"); input.type = "text"; input.maxLength = 64; input.value = state.nickname;
    input.setAttribute("aria-label", "目录显示名称"); input.placeholder = "留空使用平台名称";
    input.disabled = state.busy || !person.revision;
    input.addEventListener("input", () => { state.nickname = input.value; });
    const button = element("button", "保存备注", "secondary"); button.type = "button";
    button.disabled = state.busy || !person.revision;
    button.addEventListener("click", () => saveNickname(person, state));
    label.append(input);
    remark.body.className += " member-remark-body";
    remark.body.append(label, button);
    return remark.root;
  }
  function permissionRow(state, person, item) {
    const row = element("label", undefined, "member-permission-row");
    row.dataset.capability = item.capability;
    row.dataset.state = item.effective_state || item.state;
    row.dataset.managedBy = item.managed_by;
    const iconWrap = element("span", undefined, "member-permission-icon");
    const glyph = element("i"); glyph.setAttribute("data-lucide", permissionIcons[item.capability] || "circle-dashed");
    glyph.setAttribute("aria-hidden", "true");
    iconWrap.append(glyph);
    const title = element("span", undefined, "member-permission-title");
    title.append(element("strong", capabilityLabel(item.capability)),
      element("small", item.identity_override ? "由当前管理身份自动允许"
        : item.managed_by === "configuration" ? "由运行实例配置签发，请在账号与会话中调整。" : permissionsHelp[item.capability] || ""));
    const status = element("span", stateLabels[item.effective_state || item.state] || "待核对", "permission-status state-" + (item.effective_state || item.state));
    const toggle = element("input"); toggle.type = "checkbox"; toggle.setAttribute("role", "switch");
    toggle.setAttribute("aria-label", capabilityLabel(item.capability));
    toggle.checked = (item.effective_state || item.state) === "active";
    toggle.disabled = Boolean(state.busy || state.loading || !item.revocable || item.identity_override);
    toggle.addEventListener("change", () => configureAuthorization(person, state, item, toggle.checked));
    row.append(iconWrap, title, status, toggle);
    return row;
  }
  function advancedAuthorityRow(item) {
    const guidance = capabilityGuidance[item.capability] || {
      source: item.managed_by === "configuration" ? "运行实例配置" : "关系与权限",
      detail: item.managed_by === "configuration" ? "这项能力由运行实例统一管理，不能在成员页单独修改。"
        : "这项能力没有可用的成员级开关。",
    };
    const row = element("div", undefined, "member-authority-record");
    row.dataset.capability = item.capability;
    const copy = element("span", undefined, "member-authority-copy");
    copy.append(element("strong", capabilityLabel(item.capability)),
      element("small", `${guidance.source} · ${guidance.detail}`));
    const actions = element("span", undefined, "member-authority-actions");
    const state = element("small", item.capability === "message.delivery" ? "兼容记录" : stateLabels[item.state] || "待核对",
      "authority-state state-" + (item.state || "unknown"));
    actions.append(state);
    if (guidance.target) {
      const button = element("button", guidance.target.label, "authority-config-link");
      button.type = "button";
      button.addEventListener("click", () => {
        window.parent?.postMessage({channel: "companion-workspace", kind: "navigate",
          target: {tab: guidance.target.tab, subtab: guidance.target.subtab || ""}}, "*");
      });
      actions.append(button);
    }
    row.append(copy, actions);
    return row;
  }
  function accessPane(state, person) {
    const pane = element("div", undefined, "member-access-pane");
    const locked = Boolean(state.busy || state.loading || !state.authorization);
    const tier = state.authorization?.identity_tier || person.identity_tier || "standard";
    const admin = administratorState(state, person);
    const stateOf = name => {
      const item = (state.authorization?.items || []).find(entry => entry.capability === name);
      return item ? (item.effective_state || item.state) : null;
    };
    const toneOf = value => value === "active" ? "ok" : value ? "warn" : "muted";
    pane.append(overview("身份与权限", "核心用户就是核心管理员；身份决定默认准入，下面的成员能力仍可单独签发或撤销。", [
      ["管理员权限", admin.label, admin.state === "active" ? "accent" : admin.state === "pending" ? "warn" : "muted"],
      ["身份级别", tierLabels[tier], "accent"],
      ["允许私聊对话", stateLabels[stateOf("conversation.reply")] || "待读取", toneOf(stateOf("conversation.reply"))],
      ["允许主动联系", stateLabels[stateOf("proactive.contact")] || "待读取", toneOf(stateOf("proactive.contact"))],
    ], tier));
    const cards = element("div", undefined, "member-tier-cards");
    cards.setAttribute("role", "radiogroup"); cards.setAttribute("aria-label", "身份级别");
    const cardMap = new Map();
    // 身份是一把阶梯，不是三个并排选项：从普通到核心逐级变宽、变亮。
    for (const name of tierOrder) {
      const text = tierLabels[name];
      const card = element("button", undefined, "member-tier-card");
      card.type = "button"; card.dataset.tier = name;
      card.setAttribute("role", "radio"); card.setAttribute("aria-checked", String(state.tier === name));
      card.disabled = locked;
      const step = element("span", tierStep[name], "member-tier-step");
      step.setAttribute("aria-hidden", "true");
      const glyph = element("i"); glyph.setAttribute("data-lucide", tierIcons[name] || "user");
      glyph.setAttribute("aria-hidden", "true");
      const copy = element("span", undefined, "member-tier-copy");
      copy.append(element("strong", text), element("small", tierHelp[name]));
      const check = element("span", undefined, "member-tier-check");
      check.setAttribute("aria-hidden", "true");
      card.append(step, glyph, copy, check);
      card.addEventListener("click", () => {
        if (card.disabled) return;
        state.tier = card.dataset.tier;
        for (const other of cardMap.values()) other.setAttribute("aria-checked", String(other.dataset.tier === state.tier));
        syncTierState();
      });
      cardMap.set(name, card);
      cards.append(card);
    }
    const actions = element("div", undefined, "member-tier-actions");
    const tierState = element("span", undefined, "member-tier-state");
    const save = element("button", "保存身份", "member-primary"); save.type = "button";
    save.addEventListener("click", () => saveIdentity(person, state));
    const administratorBlock = block("管理员权限", "核心用户就是核心管理员");
    const administratorRow = element("div", undefined, "member-admin-row");
    const administratorIcon = element("span", undefined, "member-admin-icon");
    const administratorGlyph = element("i"); administratorGlyph.setAttribute("data-lucide", "shield-check");
    administratorGlyph.setAttribute("aria-hidden", "true"); administratorIcon.append(administratorGlyph);
    const administratorCopy = element("span", undefined, "member-admin-copy");
    const administratorDescription = element("small");
    administratorCopy.append(element("strong", "本体管理员"), administratorDescription);
    const administratorStatus = element("span", undefined, "member-admin-status");
    const administratorAction = element("button", "设为核心用户", "secondary"); administratorAction.type = "button";
    administratorAction.addEventListener("click", () => {
      if (state.busy || state.loading || !state.authorization) return;
      state.tier = "core";
      for (const other of cardMap.values()) other.setAttribute("aria-checked", String(other.dataset.tier === state.tier));
      syncTierState();
    });
    administratorRow.append(administratorIcon, administratorCopy, administratorStatus, administratorAction);
    administratorBlock.body.append(administratorRow,
      element("p", "选择核心用户并保存后，该成员即可执行本体配置、记忆、能力授权等管理类操作；重要用户和普通用户不会获得管理员权限。", "member-admin-note"));
    const syncTierState = () => {
      const savedTier = state.authorization?.identity_tier;
      const changed = Boolean(savedTier) && state.tier !== savedTier;
      tierState.textContent = changed ? "身份有改动，尚未保存" : "与当前记录一致";
      tierState.classList.toggle("pending", changed);
      save.disabled = Boolean(state.busy || state.loading || !state.authorization || !changed);
      const currentAdmin = administratorState(state, person);
      administratorStatus.textContent = currentAdmin.label;
      administratorStatus.dataset.state = currentAdmin.state;
      administratorDescription.textContent = currentAdmin.enabled
        ? "核心管理员可管理本体配置、记忆、能力授权及管理类操作。"
        : "当前没有管理员权限；只有核心用户可以执行本体管理操作。";
      administratorAction.textContent = state.tier === "core"
        ? (savedTier === "core" ? "已是核心管理员" : "待保存为核心管理员") : "设为核心用户";
      administratorAction.disabled = Boolean(state.busy || state.loading || !state.authorization || state.tier === "core");
      administratorRow.dataset.state = currentAdmin.state;
    };
    syncTierState();
    actions.append(tierState, save);
    const identityBlock = block("身份级别", "当前运行实例内通用；核心用户即核心管理员");
    identityBlock.body.append(cards, actions);
    pane.append(administratorBlock.root, identityBlock.root);
    const permissionBlock = block("窗口权限", person.account_name || "当前 Bot 账号");
    pane.append(permissionBlock.root);
    if (state.authError) permissionBlock.body.append(retry(state, person, state.authError));
    else if (!state.authorization) permissionBlock.body.append(element("p", "正在读取权限…", "muted"));
    else {
      const items = state.authorization.items || [];
      if (!items.length) permissionBlock.body.append(element("p", "还没有单独签发或撤销过能力，实际准入还取决于运行实例默认值和角色配置。", "muted"));
      for (const name of Object.keys(permissionLabels)) {
        const item = items.find(item => item.capability === name);
        if (item) permissionBlock.body.append(permissionRow(state, person, item));
      }
      const advanced = items.filter(item => !permissionLabels[item.capability]);
      if (advanced.length) {
        const advancedBlock = block("其他能力授权", advanced.length + " 项 · 由运行实例统一签发");
        const details = element("details", undefined, "member-advanced-authority");
        details.open = false;
        details.append(element("summary", "展开查看全部"),
          element("p", "这里显示运行实例和拓展能力的统一授权。上方列出的窗口权限可按当前 Bot、账号、角色和成员单独配置；其他能力请使用对应入口。", "member-authority-note"));
        for (const item of advanced) details.append(advancedAuthorityRow(item));
        advancedBlock.body.append(details);
        pane.append(advancedBlock.root);
      }
    }
    pane.append(directoryRemark(state, person));
    return pane;
  }
  function contactPane(state) {
    const pane = element("div", undefined, "member-contact-pane");
    const contact = state.sections?.contact?.settings || {};
    const overrides = Object.values(state.sections?.contact?.provenance || {})
      .filter(item => item.explicit || item.layer === "relationship").length;
    pane.append(overview("联系偏好", overrides ? `仅对这位用户生效 · ${overrides} 项单独设置` : "跟随全局设置", [
      ["主动计划", onOff(contact.enabled), contact.enabled ? "ok" : "muted"],
      ["每日实际联系上限", contact.max_contacts_24h != null ? contact.max_contacts_24h + " 条" : "跟随全局"],
      ["最小间隔", contact.interval_seconds != null ? Math.round(contact.interval_seconds / 60) + " 分钟" : "跟随全局"],
    ]));
    pane.append(contactScheduleDiagnostic(state));
    const switchesBlock = block("主动联系", "仅此用户");
    const switches = element("div", undefined, "member-switches");
    for (const [name, label] of [["enabled", "启用主动计划"],
      ["automatic_enabled", "自动寻找联系契机"], ["quiet_enabled", "免打扰时段"]])
      switches.append(contactFieldBlock(state, name, label, {switchField: true}));
    switchesBlock.body.append(switches);
    const rhythmBlock = block("联系节奏与时段", "数值按这位成员单独生效");
    const grid = element("div", undefined, "member-fields");
    for (const [name, label, suffix] of [["max_contacts_24h", "每日实际联系上限", "条"],
      ["interval_seconds", "最小联系间隔", "秒"],
      ["idle_seconds", "空闲后联系", "秒"], ["unanswered_slowdown_start", "开始降频的未回应次数", "次"],
      ["unanswered_pause_after", "暂停联系的未回应次数（0 表示不暂停）", "次"],
      ["unanswered_max_interval_multiplier", "降频后的最大间隔倍数", "倍"],
      ["quiet_start", "免打扰开始", ""], ["quiet_end", "免打扰结束", ""],
      ["quiet_timezone", "时区", ""]])
      grid.append(contactFieldBlock(state, name, label, {suffix}));
    grid.append(contactFieldBlock(state, "pause_until", "临时暂停到（本地时间）", {inputType: "datetime-local"}));
    grid.append(contactFieldBlock(state, "note", "联系补充偏好", {rows: 3}));
    grid.append(contactFieldBlock(state, "style_prompt", "主动表达方式偏好", {rows: 3}));
    rhythmBlock.body.append(grid);
    rhythmBlock.body.append(element("p", "未回应会逐步延长间隔；达到暂停次数后，等对方再次发消息再恢复评估。", "member-block-note"));
    rhythmBlock.body.append(element("p", "暂停时间按浏览器本地时区录入；清空只清除本窗口的暂停，上级仍生效的暂停不会被覆盖。", "member-block-note"));
    pane.append(switchesBlock.root, rhythmBlock.root);
    pane.append(contactPurposesBlock(state));
    return pane;
  }
  function contactPurposesBlock(state) {
    const purposesBlock = block("联系契机", "15 类 · 仅此私聊窗口");
    purposesBlock.body.append(element("p",
      "关闭单项会跳过这类联系；权重只影响多个合适候选之间的选择，暂缓只作用于该类。",
      "member-block-note"));
    for (const [title, purposes] of contactPurposeGroups) {
      const category = element("details", undefined, "member-contact-purpose-category");
      category.append(element("summary", `${title} · ${purposes.length} 类`));
      const rows = element("div", undefined, "member-contact-purpose-list");
      for (const [name, label] of purposes) {
        const row = element("div", undefined, "member-contact-purpose-row");
        row.append(contactFieldBlock(state, name + ".enabled", label, {switchField: true}));
        const tuning = element("details", undefined, "member-contact-purpose-tuning");
        tuning.append(element("summary", "细调"));
        const fields = element("div", undefined, "member-contact-purpose-tuning-fields");
        fields.append(contactFieldBlock(state, name + ".weight", "相对权重"));
        fields.append(contactFieldBlock(state, name + ".pause_until", "暂缓到（本地时间）",
          {inputType: "datetime-local"}));
        tuning.append(fields);
        row.append(tuning);
        rows.append(row);
      }
      category.append(rows);
      purposesBlock.body.append(category);
    }
    return purposesBlock.root;
  }
  function contactFieldBlock(state, name, label, options = {}) {
    const provenance = state.sections.contact?.provenance?.[name] || {};
    const explicit = provenance.explicit === true;
    const pendingReset = state.contactResetToInherit.has(name);
    const wrapper = element("div", undefined, options.switchField ? "member-contact-switch" : "member-contact-field");
    if (explicit) wrapper.dataset.explicit = "true";
    wrapper.append(field(state, "contact", name, label, options));
    wrapper.append(element("small", pendingReset ? "保存后恢复继承"
      : explicit ? "此私聊窗口覆盖" : "跟随上级设置", "member-contact-source"));
    if (explicit) {
      const reset = element("button", pendingReset ? "取消恢复" : "恢复继承", "member-contact-reset");
      reset.type = "button";
      reset.disabled = state.busy || state.loading || !!state.sectionErrors.contact || !!state.settingsError || !!state.pendingSave;
      reset.setAttribute("aria-label", pendingReset ? `取消恢复${label}的继承值` : `恢复${label}的继承值`);
      reset.addEventListener("click", () => {
        if (state.pendingSave) return;
        const token = "contact." + name;
        if (pendingReset) {
          state.contactResetToInherit.delete(name);
          if (state.drafts.contact[name] === state.sections.contact.settings[name]) state.dirty.delete(token);
          else state.dirty.add(token);
        } else {
          state.contactResetToInherit.add(name);
          state.dirty.add(token);
        }
        state.pendingSave = null;
        redraw(state);
      });
      wrapper.append(reset);
    }
    return wrapper;
  }
  const scheduleReasons = {
    never_evaluated: ["此窗口还没有自动评估记录", "确认插件正在运行，并等待本窗口进入下一轮调度评估。"],
    newer_conversation_not_evaluated: ["最近一轮私聊尚未完成评估", "等当前私聊处理完成后重新读取调度状态。"],
    proactive_permission_denied: ["此私聊没有主动联系授权", "在身份与权限中允许角色主动联系。"],
    automatic_contact_disabled: ["后台自动联系已关闭", "打开此窗口的后台自动联系开关。"],
    contact_settings_disabled: ["主动联系总开关已关闭", "在角色或窗口的主动联系设置中打开总开关。"],
    contact_paused: ["主动联系正处于暂缓期", "等待暂停结束，或核对全局、窗口与联系目的的暂停时间。"],
    quiet_hours: ["当前处于免打扰时段", "等待免打扰结束，或核对时区与起止时间。"],
    contact_conditions_blocked: ["当前联系条件未满足", "核对联系总开关、目的开关、暂停和免打扰设置。"],
    idle_period_not_elapsed: ["仍在对话后的静默等待期", "等待静默时长结束；诊断中会显示预计时间。"],
    calendar_source_pending: ["日历契机仍在准备", "检查日历来源状态，来源完成后调度会继续。"],
    activity_source_pending: ["生活事件仍在准备", "检查角色生活状态和事件来源。"],
    journal_source_pending: ["日记契机仍在准备", "检查角色日记与生活工作区。"],
    appointment_silence: ["约定前后静默规则正在生效", "按约定静默结束后再评估。"],
    recent_attempt_cooldown: ["主动尝试间隔尚未结束", "等待最小联系间隔结束。"],
    unanswered_pause: ["未回复后的暂停规则正在生效", "收到新的私聊消息后会重置这项暂停。"],
    chat_purpose_disabled: ["日常话题联系目的已关闭", "启用日常话题，或等待其他已开启的契机。"],
    closing_silence: ["连续联系后的收尾静默正在生效", "等待收尾静默结束或等对方回复。"],
    slot_already_processed: ["当前节律时段已处理", "不会重放同一时段；等待下一时段。"],
    outside_contact_slot: ["当前不在节律评估窗口", "等待下一个节律时段。"],
    activity_curve_skipped: ["当前时段被活跃度曲线跳过", "检查本地时区和按小时活跃曲线，或等待下一时段。"],
    screen_observation_pending: ["正在等待屏幕观察结果", "观察完成后再判断是否形成主动消息。"],
    platform_circuit_open: ["平台发送通道暂时冷却", "检查 AstrBot 平台连接；冷却结束后会恢复评估。"],
    conversation_busy: ["角色当前处于忙碌时段", "忙碌状态结束后会恢复联系评估。"],
    opportunity_found: ["已找到主动联系候选", "继续查看后台任务与模型决策结果。"],
    candidate_queued: ["候选已进入后台执行", "若没有发出，请查看任务执行和投递回执诊断。"],
    contact_model_unregistered: ["主动联系模型任务尚未登记", "检查模型路由配置并重启插件。"],
    background_capacity_exhausted: ["后台执行槽位暂时占满", "等待现有后台任务结束后会继续评估。"],
    contact_attempt_budget: ["模型调用次数上限已触发", "请查看窗口每日实际联系额度。"],
    delivery_history_unavailable: ["当前私聊历史无法完成核验", "检查 AstrBot 会话历史是否可读取后再重新评估。"],
    contact_conversation_changed: ["私聊已变化，待发送计划已取消", "等待当前私聊完成后重新评估。"],
    scheduler_heartbeat_missing: ["尚未收到主动调度心跳", "确认插件已启动且主动联系模型已登记。"],
    scheduler_heartbeat_stale: ["主动调度心跳已中断", "检查插件调度器是否异常或已停止。"],
  };
  const scheduleStages = {permission: "权限", settings: "设置", conversation: "会话", sources: "来源",
    rhythm: "节律", validation: "校验", candidate: "候选", delivery_admission: "发送准入",
    busy_gate: "忙碌门槛", task_queue: "任务队列", scheduler: "调度器"};
  const scheduleOutcomes = {not_evaluated: "尚未评估", stale: "等待新会话评估", running: "运行中",
    blocked: "暂时受阻", candidate: "发现候选", queued: "已排队", pending: "等待来源",
    skipped: "按策略跳过", failed: "检查失败"};
  const schedulerStates = {running: "正常运行", blocked: "暂时受阻", stale: "心跳中断",
    not_seen: "尚未启动", stopped: "宿主未运行"};
  function diagnosticTime(value) {
    if (!value) return "无记录";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "时间无法解析" : new Intl.DateTimeFormat("zh-CN", {
      month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
    }).format(date);
  }
  function contactScheduleDiagnostic(state) {
    const section = element("section", undefined, "member-schedule-diagnostic");
    section.setAttribute("aria-label", "主动联系调度诊断");
    const diagnostic = state.sections?.contact?.schedule_diagnostic;
    section.append(element("h4", "最近自动调度"));
    if (!diagnostic) {
      const text = state.loading ? "正在读取调度状态…" : state.settingsError || state.sectionErrors.contact
        ? "联系配置读取失败，暂时无法显示调度诊断。重新读取成员配置后再试。"
        : "当前没有调度记录。请确认插件已启动，并在下次状态刷新时查看本窗口的评估结果。";
      section.append(element("p", text, "member-schedule-empty"));
      return section;
    }
    const scheduler = diagnostic.scheduler || {};
    const evaluation = diagnostic.evaluation || {};
    const issue = scheduleReasons[evaluation.reason_code] || ["调度记录了一个待核对的条件",
      "检查联系开关、来源状态和后台任务；若持续出现，可将评估时间与阶段提供给维护者。"];
    section.dataset.outcome = evaluation.outcome || "unknown";
    const details = element("dl", undefined, "member-schedule-facts");
    const rows = [
      ["调度器", `${schedulerStates[scheduler.state] || "状态待核对"} · ${diagnosticTime(scheduler.last_seen_at)}`],
      ["最近评估", diagnosticTime(evaluation.observed_at)],
      ["结果", `${scheduleOutcomes[evaluation.outcome] || "状态待核对"} · ${scheduleStages[evaluation.stage] || "未知阶段"}`
        + (evaluation.purpose ? ` · ${evaluation.purpose}` : "")],
    ];
    if (evaluation.next_at) rows.push(["预计恢复", diagnosticTime(evaluation.next_at)]);
    for (const [label, value] of rows) {
      const row = element("div");
      row.append(element("dt", label), element("dd", value));
      details.append(row);
    }
    section.append(details, element("p", issue[0], "member-schedule-issue"),
      element("p", issue[1], "member-schedule-next"));
    if (!scheduleReasons[evaluation.reason_code] && evaluation.reason_code) {
      section.append(element("small", `诊断代码：${evaluation.reason_code}`, "member-schedule-code"));
    }
    return section;
  }
  function historyPane(person, services) {
    const pane = element("div", undefined, "member-history-pane");
    const total = person.total || {};
    pane.append(overview("往来记录", "这份记录只做统计展示，不会因编辑而改变。", [
      ["累计消息", String(total.msg_count ?? 0)],
      ["活跃天数", String(total.active_days ?? 0)],
      ["最近往来", shortTime(total.last_seen)],
    ]));
    const factsBlock = block("往来明细");
    const summaryList = element("dl", undefined, "member-history-facts");
    for (const [label, value] of [["累计消息", String(person.total?.msg_count || 0)], ["活跃天数", String(person.total?.active_days || 0)],
      ["首次往来", shortTime(person.total?.first_seen)], ["最近往来", shortTime(person.total?.last_seen)]]) {
      const row = element("div"); row.append(element("dt", label), element("dd", value)); summaryList.append(row);
    }
    factsBlock.body.append(summaryList);
    // 私聊上下文此前完全没有出现在界面上，和群聊并列呈现。
    const privateEntry = person.private || null;
    const privateBlock = block("私聊往来", "只统计当前账号与人格下的私聊窗口");
    if (privateEntry) {
      privateBlock.body.append(facts([["消息数", String(privateEntry.msg_count ?? 0)],
        ["活跃天数", String(privateEntry.active_days ?? 0)], ["最近往来", shortTime(privateEntry.last_seen)]]));
    } else {
      privateBlock.body.append(element("p", "尚未建立可管理的私聊窗口。", "member-block-note"));
    }
    const groupEntries = Object.entries(person.groups || {});
    const groupsBlock = block("群聊往来", groupEntries.length ? `${groupEntries.length} 个群` : "暂无群聊往来");
    const groups = element("div", undefined, "member-history-groups");
    for (const [id, entry] of groupEntries) {
      const row = element("div", undefined, "member-group-row");
      const button = element("button", services.groupLabel?.(id) || id, "context-group"); button.type = "button";
      button.addEventListener("click", () => services.openGroup?.(id));
      const metrics = element("span", undefined, "member-group-metrics");
      metrics.append(element("span", `${entry?.msg_count ?? 0} 条`),
        element("span", `${entry?.active_days ?? 0} 天`),
        element("span", shortTime(entry?.last_seen)));
      row.append(button, metrics);
      groups.append(row);
    }
    if (!groupEntries.length) groups.append(element("p", "暂无群聊往来", "muted"));
    groupsBlock.body.append(groups);
    pane.append(factsBlock.root, privateBlock.root, groupsBlock.root);
    return pane;
  }
  function savebar(state) {
    const bar = element("footer", undefined, "member-savebar");
    const text = state.busy ? state.reconciling ? "正在核对保存结果…" : "正在保存…" : state.pendingSave && state.loading
      ? "正在核对保存状态…" : state.pendingSave ? "保存结果待核对，草稿已锁定"
      : state.settingsError
      ? state.dirty.size ? `${state.dirty.size} 项草稿待核对` : "配置读取失败，未确认同步"
      : hasUnreadDirtySections(state) && dirtySectionNames(state).some(section => state.sections[section] && !state.sectionErrors[section])
      ? `${state.dirty.size} 项草稿中，可读取分区会先保存`
      : hasUnreadDirtySections(state) ? `${state.dirty.size} 项草稿待对应分区恢复读取`
      : state.dirty.size ? `${state.dirty.size} 项未保存` : "资料已同步";
    bar.append(element("span", text, "member-draft-state"));
    const cancel = element("button", "撤销修改", "secondary"); cancel.type = "button";
    cancel.disabled = !state.dirty.size || state.busy || !!state.pendingSave;
    cancel.addEventListener("click", () => {
      if (state.pendingSave) return;
      adoptSettings(state, {sections: state.sections, section_errors: state.sectionErrors}, false);
      state.pendingSave = null; redraw(state);
    });
    const save = element("button", state.pendingSave
      ? state.busy || state.loading ? "正在核对…" : "核对并重试保存"
      : state.busy ? "保存中" : state.pane === "reply" ? "保存回复设置" : "保存资料", "member-primary"); save.type = "button";
    save.disabled = state.pendingSave
      ? state.busy || state.loading
      : !state.dirty.size || state.busy || state.loading || !!state.settingsError
        || !dirtySectionNames(state).some(section => state.sections[section] && !state.sectionErrors[section]);
    save.addEventListener("click", () => state.pendingSave
      ? verifyAndRetrySave(activePerson(), state) : saveSettings(activePerson(), state));
    bar.append(cancel, save);
    return bar;
  }
  function replyFieldBlock(state, name) {
    const provenance = state.sections.reply?.provenance?.[name] || {};
    const inherited = state.sections.reply?.inherited_settings?.[name];
    const rows = ["prompt", "style_prompt", "humanized_expression_prompt", "content_cleanup_words",
      "content_cleanup_rule", "content_replacements", "voice_trigger_keywords", "voice_extra_prompt",
      "private_image_self_recognition_hint"].includes(name) ? 3 : 0;
    const wrapper = element("div", undefined, "member-reply-field");
    if (provenance.explicit) wrapper.dataset.explicit = "true";
    const label = name === "voice_probability" ? "语音触发概率（0–1）" : replyLabels[name] || name;
    const providerFields = new Set(["voice_provider_id", "voice_zh_provider_id",
      "voice_ja_provider_id", "voice_en_provider_id", "voice_private_zh_provider_id",
      "voice_private_ja_provider_id", "voice_private_en_provider_id"]);
    const tts = Array.isArray(state.tts?.providers) ? state.tts.providers : [];
    const providerChoices = providerFields.has(name) && tts.length ? {
      "": name === "voice_provider_id" ? "跟随角色默认" : "使用通用扩展",
      ...Object.fromEntries(tts.filter(item => item && typeof item.id === "string" && item.id).map(item =>
        [item.id, [item.id, item.model, item.voice].filter(Boolean).join(" · ")])),
    } : null;
    wrapper.append(field(state, "reply", name, label, {rows, choices: providerChoices}));
    const source = element("small", provenance.explicit ? "此用户覆盖" : "跟随角色默认", "member-reply-source");
    wrapper.append(source);
    if (provenance.explicit) {
      const reset = element("button", state.resetToRole.has(name) ? "将随保存恢复角色默认" : "恢复角色默认", "member-reply-reset");
      reset.type = "button";
      reset.disabled = state.busy || state.loading || !!state.sectionErrors.reply || !!state.settingsError || !!state.pendingSave;
      reset.setAttribute("aria-label", `恢复${label}的角色默认值`);
      reset.addEventListener("click", () => {
        if (state.pendingSave) return;
        state.drafts.reply[name] = normalizeReplyField(name, inherited);
        state.resetToRole.add(name);
        state.dirty.add("reply." + name);
        state.pendingSave = null;
        redraw(state);
      });
      wrapper.append(reset);
    }
    return wrapper;
  }
  function replyPane(state, person) {
    const pane = element("div", undefined, "member-reply-pane");
    const settings = {...(state.sections.reply?.settings || {}), ...(state.drafts.reply || {})};
    const styleOverride = state.sections.reply?.provenance?.style_prompt?.explicit === true;
    const explicit = Object.values(state.sections.reply?.provenance || {}).filter(value => value.explicit).length;
    pane.append(overview("私聊回复", explicit ? `仅对此用户覆盖角色默认 · ${explicit} 项` : "全部跟随当前角色", [
      ["表达风格", styleOverride ? "此用户已覆盖" : "继承角色默认"],
      ["语音回复", onOff(settings.voice_enabled)], ["窗口覆盖", `${explicit} 项`],
    ]));
    const visible = new Set([
      "style_prompt", "enable_humanized_expression", "humanized_expression_prompt",
      "enabled", "streaming", "streaming_mode", "llm_controlled", "rule_mode", "threshold",
      "min_segment_chars", "max_segments", "part_gap_ms", "part_gap_mode", "part_gap_min_ms",
      "part_gap_max_ms", "part_gap_log_base", "prompt", "split_words", "match_width_variants", "rule_regex",
      "content_cleanup_enabled", "content_replacement_enabled", "voice_enabled", "reply_quote_enabled",
      "enable_private_image_self_recognition", "image_understanding_private_enabled",
      "astrbot_animated_image_understanding_enabled",
      "video_understanding_private_enabled", "video_understanding_enabled",
      "file_understanding_private_enabled", "file_understanding_enabled",
    ]);
    if (settings.streaming) visible.add("streaming_mode");
    if (settings.llm_controlled) visible.add("prompt");
    if (settings.rule_mode === "words") visible.add("split_words");
    if (settings.rule_mode === "regex") visible.add("rule_regex");
    if (settings.rule_mode === "words") visible.add("match_width_variants");
    if (settings.content_cleanup_enabled) for (const name of ["content_cleanup_mode", "content_cleanup_scope"])
      visible.add(name);
    if (settings.content_cleanup_enabled && settings.content_cleanup_mode === "regex") visible.add("content_cleanup_rule");
    if (settings.content_cleanup_enabled && settings.content_cleanup_mode !== "regex") visible.add("content_cleanup_words");
    if (settings.content_replacement_enabled) visible.add("content_replacements");
    if (settings.reply_quote_enabled) for (const name of ["reply_quote_first_part_only", "reply_quote_skip_chars"]) visible.add(name);
    if (settings.voice_enabled) for (const group of replyGroups.filter(group => group.title === "语音回复"))
      for (const name of group.fields) visible.add(name);
    if (settings.enable_private_image_self_recognition) visible.add("private_image_self_recognition_hint");
    if (settings.enable_private_image_self_recognition) for (const name of ["enable_private_image_vision_cache",
      "private_image_vision_cache_max_items", "private_image_vision_custom_prompt", "private_image_vision_max_chars",
      "private_image_vision_wait_seconds", "private_image_provider_failure_cooldown_seconds"]) visible.add(name);
    if (settings.video_understanding_enabled) for (const name of ["video_understanding_max_chars",
      "video_understanding_wait_seconds", "video_self_recognition_hint", "video_understanding_prompt"]) visible.add(name);
    if (settings.file_understanding_enabled) for (const name of ["file_understanding_format_groups",
      "file_understanding_max_size_mb", "file_understanding_max_chars", "file_understanding_timeout_seconds",
      "file_understanding_prompt"]) visible.add(name);
    for (const group of replyGroups) {
      const fields = group.fields.filter(name => visible.has(name) && Object.hasOwn(settings, name));
      if (!fields.length) continue;
      const section = block(group.title, "");
      const grid = element("div", undefined, "member-reply-fields");
      for (const name of fields) grid.append(replyFieldBlock(state, name));
      section.body.append(grid);
      pane.append(section.root);
    }
    return pane;
  }
  function render() {
    const {panel, services} = current;
    const person = activePerson();
    const state = stateFor(person);
    panel.replaceChildren();
    const tier = state.authorization?.identity_tier || person.identity_tier || "standard";
    const hero = element("section", undefined, "member-card");
    hero.dataset.tier = tier;
    const seal = element("span", tierSeal[tier] || "STD", "member-card-seal");
    seal.setAttribute("aria-hidden", "true");
    hero.append(seal);
    const heroTop = element("div", undefined, "member-card-main");
    const identity = element("div", undefined, "member-identity");
    const heroAvatar = element("span", undefined, "member-avatar-hero");
    heroAvatar.setAttribute("aria-hidden", "true");
    heroAvatar.innerHTML = PORTRAIT_SVG;
    const copy = element("div", undefined, "member-heading-copy");
    const nameRow = element("div", undefined, "member-name-row");
    nameRow.append(element("h3", nameOf(person)), element("span", tierLabels[tier], "member-tier-badge"));
    const metaRow = element("div", undefined, "member-meta-row");
    const metaTexts = [];
    for (const [glyph, text] of [["at-sign", "QQ " + (person.native_account_id || "未知")],
        ["bot", accountLabel(person)], ["sparkles", personaLabel(person)]]) {
      if (text && !metaTexts.some(entry => entry[1] === text)) metaTexts.push([glyph, text]);
    }
    for (const [glyph, text] of metaTexts) {
      const chip = element("span", undefined, "member-meta-chip");
      const icon = element("i"); icon.setAttribute("data-lucide", glyph); icon.setAttribute("aria-hidden", "true");
      chip.append(icon, element("span", text));
      metaRow.append(chip);
    }
    copy.append(element("span", tierEyebrow[tier] || "MEMBER", "member-hero-eyebrow"), nameRow,
      element("span", `名字来源 · ${nameBasisLabels[nameBasisOf(person)] || "—"}`, "member-card-source"), metaRow);
    const routes = routeEntries(current.person);
    if (routes.length > 1) {
      const selector = element("select", undefined, "member-route-select");
      selector.setAttribute("aria-label", "选择内部人格归属");
      const placeholder = element("option", "选择内部归属后读取资料与权限");
      placeholder.value = "";
      placeholder.selected = !current.route;
      selector.append(placeholder);
      routes.forEach(route => {
        const option = element("option", routeLabel(route));
        option.value = JSON.stringify(route);
        option.selected = Boolean(activeRoute() && routeKey(route) === routeKey(activeRoute()));
        selector.append(option);
      });
      selector.value = activeRoute() ? JSON.stringify(activeRoute()) : "";
      selector.disabled = state.busy || state.loading || !!state.pendingSave;
      selector.addEventListener("change", event => {
        const value = event?.target?.value ?? selector.value;
        const next = routes.find(route => JSON.stringify(route) === value) || null;
        current.route = next;
        render();
        if (next) void load(activePerson(), {preserve: false});
      });
      copy.append(selector);
    }
    identity.append(heroAvatar, copy);
    const isCore = (state.authorization?.identity_tier || person.identity_tier) === "core";
    heroTop.append(identity);
    // 熟人卡不再是一个配置分区，而是名片上的一张卡，点开是独立弹窗。
    const actions = element("div", undefined, "member-card-actions");
    const cardButton = element("button", undefined, "member-card-button"); cardButton.type = "button";
    cardButton.dataset.cardTrigger = "true";
    cardButton.setAttribute("aria-haspopup", "dialog");
    cardButton.setAttribute("aria-expanded", String(Boolean(current.card)));
    const cardGlyph = element("i"); cardGlyph.setAttribute("data-lucide", "id-card");
    cardGlyph.setAttribute("aria-hidden", "true");
    cardButton.append(cardGlyph, element("span", "熟人卡"));
    // 同步开卡、异步取数：先让卡片立刻出现，读取完成后重绘会就地把内容补齐。
    cardButton.addEventListener("click", () => {
      if (current.card) { closeDialog(); return; }
      const target = activePerson();
      openCard(stateFor(target), target);
      if (!state.loaded) void load(target);
    });
    const refresh = element("button", undefined, "member-icon-button"); refresh.type = "button";
    refresh.setAttribute("aria-label", "重新读取成员配置"); refresh.title = "重新读取成员配置";
    const icon = element("i"); icon.setAttribute("data-lucide", "refresh-cw"); refresh.append(icon);
    refresh.disabled = !current.route || state.loading || state.busy;
    refresh.addEventListener("click", () => { if (current.route) void load(person); });
    if (!isCore) actions.append(cardButton);
    actions.append(refresh);
    heroTop.append(actions);
    const total = current.person.total || {};
    const heroStats = element("div", undefined, "member-hero-stats");
    for (const [label, value] of [["累计消息", String(total.msg_count ?? 0)], ["活跃天数", String(total.active_days ?? 0)],
        ["最近往来", shortTime(total.last_seen)], ["首次往来", shortTime(total.first_seen)]]) {
      const cell = element("div", undefined, "member-hero-stat");
      cell.append(element("span", label), element("strong", value));
      heroStats.append(cell);
    }
    const groupCount = Object.keys(current.person.groups || {}).length;
    const relations = element("div", undefined, "member-card-relations");
    for (const [label, on] of [["私聊往来", current.person.private_chat === true],
        ["群聊成员", current.person.group_member === true]]) {
      const tag = element("span", undefined, "member-relation-tag");
      tag.dataset.on = String(on);
      const dot = element("i"); dot.setAttribute("aria-hidden", "true");
      tag.append(dot, element("span", label));
      relations.append(tag);
    }
    if (groupCount) relations.append(element("span", `共 ${groupCount} 个群`, "member-relation-note"));
    hero.append(heroTop, heroStats, relations);
    if (state.message) {
      const message = element("p", state.message, "member-feedback" + (state.failed ? " error" : ""));
      message.setAttribute("role", "status"); panel.append(message);
    }
    const tabs = element("nav", undefined, "member-tabs"); tabs.setAttribute("role", "tablist"); tabs.setAttribute("aria-label", "成员配置分区");
    const paneNames = ["access", "relationship", "reply", "contact", "history"];
    for (const [name, title] of [["access", "身份与权限"], ["relationship", "人工关系资料"],
      ["reply", "回复微调"], ["contact", "联系偏好"], ["history", "往来记录"]]) {
      const button = element("button", title); button.type = "button"; button.id = "member-tab-" + name;
      button.setAttribute("role", "tab"); button.setAttribute("aria-controls", "member-pane");
      button.setAttribute("aria-selected", String(state.pane === name)); button.tabIndex = state.pane === name ? 0 : -1;
      button.addEventListener("click", () => {state.pane = name; current.card = false; redraw(state);});
      button.addEventListener("keydown", event => {
        const index = paneNames.indexOf(name);
        const next = event.key === "ArrowRight" ? (index + 1) % paneNames.length
          : event.key === "ArrowLeft" ? (index + paneNames.length - 1) % paneNames.length
          : event.key === "Home" ? 0 : event.key === "End" ? paneNames.length - 1 : -1;
        if (next < 0) return; event.preventDefault(); state.pane = paneNames[next]; current.card = false; redraw(state);
        document.getElementById("member-tab-" + state.pane)?.focus();
      });
      tabs.append(button);
    }
    panel.append(hero, tabs);
    const body = element("section", undefined, "member-pane"); body.id = "member-pane";
    body.setAttribute("role", "tabpanel"); body.setAttribute("aria-labelledby", "member-tab-" + state.pane);
    const canConfigure = Boolean(current.route && current.route.private_target === true
      && current.route.settings_available === true);
    if (state.pane === "history") body.append(historyPane(current.person, services));
    else if (!current.route) body.append(element("p", routes.length > 1
      ? "此成员对应多个内部归属。选择一个后，页面会读取该归属的资料与权限。"
      : "此成员尚未建立当前账号与人格下可管理的 V2 私聊窗口，资料和权限暂时只读。", "member-unavailable"));
    else if (!canConfigure) body.append(element("p", "此成员尚未建立当前账号与人格下可管理的 V2 私聊窗口，资料和权限暂时只读。", "member-unavailable"));
    else if (state.pane === "access") body.append(accessPane(state, person));
    else if (state.pane === "relationship") {
      if (state.settingsError) body.append(retry(state, person, state.settingsError));
      else if (sectionErrorText(state, "profile")) body.append(retry(state, person, sectionErrorText(state, "profile")));
      else if (state.loading && !state.sections.profile) body.append(element("p", "正在读取人工关系资料…", "member-loading"));
      else body.append(relationshipPane(state, person));
    } else if (state.pane === "reply") {
      if (state.settingsError) body.append(retry(state, person, state.settingsError));
      else if (sectionErrorText(state, "reply")) body.append(retry(state, person, sectionErrorText(state, "reply")));
      else if (state.loading && !state.sections.reply) body.append(element("p", "正在读取私聊回复设置…", "member-loading"));
      else if (state.sections.reply) body.append(replyPane(state, person));
    } else {
      if (state.settingsError) body.append(retry(state, person, state.settingsError));
      else if (sectionErrorText(state, "contact")) body.append(retry(state, person, sectionErrorText(state, "contact")));
      if (state.loading && !Object.keys(state.sections).length) body.append(element("p", "正在读取成员配置…", "member-loading"));
      if (!state.settingsError && !sectionErrorText(state, "contact")) body.append(contactPane(state));
    }
    panel.append(body);
    if (canConfigure && (state.pendingSave || state.pane === "contact" || state.pane === "reply"
        || state.pane === "relationship" && !isCore
        || [...state.dirty].some(token => token.startsWith("person.") || token.startsWith("person_learning.")))) {
      panel.append(savebar(state));
    }
    // 熟人卡弹窗在重绘后重建，保证里面的草稿与保存条跟着最新状态。
    if (current.card) openCard(state, person);
    window.lucide?.createIcons();
  }
  async function saveSettings(person, state) {
    if (state.busy || !state.dirty.size) return;
    const sections = {};
    for (const token of state.dirty) {
      const separator = token.indexOf(".");
      const section = token.slice(0, separator), field = token.slice(separator + 1);
      if (state.sectionErrors[section] || !state.sections[section]) continue;
      sections[section] ||= {edit_token: state.sections[section].edit_token, changes: {}};
      if ((["reply", "person", "person_learning"].includes(section) && state.resetToRole.has(field))
          || (section === "contact" && state.contactResetToInherit.has(field))) {
        sections[section].reset_to_inherit ||= [];
        sections[section].reset_to_inherit.push(field);
      } else sections[section].changes[field] = state.drafts[section][field];
    }
    if (!Object.keys(sections).length) return;
    const changesOnly = Object.fromEntries(Object.entries(sections).map(([name, section]) => [name,
      {changes: section.changes, ...(section.reset_to_inherit ? {reset_to_inherit: section.reset_to_inherit} : {})}]));
    const fingerprint = JSON.stringify({subject_id: person.subject_id, route: routeOf(person), sections: changesOnly});
    if (state.pendingSave?.fingerprint !== fingerprint) {
      state.pendingSave = {fingerprint, request: {
        subject_id: person.subject_id, route: routeOf(person), operation_key: operation(),
        sections: JSON.parse(JSON.stringify(sections)),
      }};
    }
    await transmitPendingSave(person, state);
  }
  async function transmitPendingSave(person, state) {
    if (state.busy || !state.pendingSave) return;
    const services = current.services;
    state.busy = true;
    let recoveryAttempted = state.pendingSave.autoRecoveryAttempted === true;
    try {
      while (true) {
        state.reconciling = recoveryAttempted;
        redraw(state);
        try {
          const result = unwrap(await services.bridge.apiPost(ROOT + "/settings", state.pendingSave.request,
            {timeoutMs: MEMBER_SETTINGS_TIMEOUT_MS}));
          const submittedSections = new Set(Object.keys(state.pendingSave.request.sections));
          const refreshed = {...result, sections: {...(result.sections || {})}, section_errors: {...(result.section_errors || {})}};
          for (const [name, item] of Object.entries(result.results || {})) {
            if (submittedSections.has(name) && item?.status) {
              refreshed.sections[name] = item.status;
              delete refreshed.section_errors[name];
            }
          }
          adoptSettings(state, refreshed, true);
          for (const token of [...state.dirty]) {
            if (submittedSections.has(token.slice(0, token.indexOf(".")))) state.dirty.delete(token);
          }
          for (const [section, payload] of Object.entries(state.pendingSave.request.sections)) {
            for (const field of payload.reset_to_inherit || []) {
              if (![...state.dirty].some(token => token.endsWith("." + field))) {
                if (["reply", "person", "person_learning"].includes(section)) state.resetToRole.delete(field);
                if (section === "contact") state.contactResetToInherit.delete(field);
              }
            }
          }
          for (const section of submittedSections) {
            if (!state.sections[section]) continue;
            const draft = {...state.sections[section].settings};
            if (section === "reply") normalizeReplyDraft(draft);
            state.drafts[section] = draft;
            if (section === "reply"
                && draft.content_cleanup_scope !== state.sections[section].settings.content_cleanup_scope) {
              state.dirty.add("reply.content_cleanup_scope");
            }
          }
          state.pendingSave = null;
          notify(state, state.dirty.size ? "可读取分区已保存；未读取分区的草稿仍保留。"
            : state.pane === "reply" ? "私聊回复设置已保存。" : "成员资料已保存。");
          break;
        } catch (error) {
          const confirmedRejection = error.confirmedRejection || error.confirmed === true;
          const ambiguous = !confirmedRejection || AMBIGUOUS_SAVE_CODES.has(error.code);
          const canRecoverAutomatically = error.code === "bridge_timeout" || AMBIGUOUS_SAVE_CODES.has(error.code);
          if (!ambiguous) {
            state.pendingSave = null;
            notify(state, errorText(error, "服务端已拒绝本次保存，草稿仍保留，可修改后重试。"), true);
          } else if (!recoveryAttempted && canRecoverAutomatically) {
            recoveryAttempted = true;
            state.pendingSave.autoRecoveryAttempted = true;
            state.reconciling = true;
            notify(state, "保存回执未收到，正在用同一操作编号安全核对…", true);
            continue;
          } else notify(state, errorText(error,
            "保存结果仍未确认，草稿已锁定。可继续核对并重试原操作。"), true);
          if (error.code === "configuration_revision_conflict") {state.pendingSave = null; await load(person);}
          break;
        }
      }
    } finally {
      state.busy = false;
      state.reconciling = false;
      redraw(state);
    }
  }
  async function verifyAndRetrySave(person, state) {
    if (state.busy || state.loading || !state.pendingSave) return;
    await load(person);
    if (state.settingsError || !state.pendingSave || !state.dirty.size
        || Object.keys(state.pendingSave.request.sections).some(section => state.sectionErrors[section] || !state.sections[section])) return;
    await transmitPendingSave(person, state);
  }
  async function configureAuthorization(person, state, item, enabled) {
    if (state.busy) return;
    const services = current.services;
    const fingerprint = item.capability + ":" + enabled;
    if (state.pendingAuthorization?.fingerprint !== fingerprint) state.pendingAuthorization = {fingerprint, key: operation()};
    state.busy = true; redraw(state);
    try {
      const result = unwrap(await services.bridge.apiPost(ROOT + "/authorization", {
        subject_id: person.subject_id, route: routeOf(person), capability: item.capability, enabled,
        expected_revision: item.policy_revision, operation_key: state.pendingAuthorization.key}));
      state.authorization = result.status; state.pendingAuthorization = null;
      notify(state, `${permissionLabels[item.capability]}已${enabled ? "开启" : "关闭"}。`);
    } catch (error) {
      notify(state, errorText(error), true);
      if (error.code === "authorization_revision_conflict") {state.pendingAuthorization = null; await load(person);}
    }
    state.busy = false; redraw(state);
  }
  async function saveIdentity(person, state) {
    if (state.busy || state.tier === state.authorization?.identity_tier) return;
    const services = current.services, tier = state.tier;
    if (state.pendingIdentity?.tier !== tier) state.pendingIdentity = {tier, key: operation()};
    state.busy = true; redraw(state);
    try {
      const result = unwrap(await services.bridge.apiPost(ROOT + "/identity", {subject_id: person.subject_id,
        route: routeOf(person), tier, expected_revision: state.authorization.identity_revision, operation_key: state.pendingIdentity.key}));
      state.authorization = result.status; state.pendingIdentity = null;
      services.onChanged?.(person, {identity_tier: tier}, {identity: true});
      notify(state, `已保存为${tierLabels[tier]}。`);
    } catch (error) {
      notify(state, errorText(error), true);
      if (error.code === "identity_revision_conflict") {state.pendingIdentity = null; await load(person);}
    }
    state.busy = false; redraw(state);
  }
  async function saveNickname(person, state) {
    if (state.busy) return;
    const services = current.services, nickname = state.nickname;
    if (state.pendingNickname?.value !== nickname) state.pendingNickname = {value: nickname, key: operation()};
    state.busy = true; redraw(state);
    try {
      const result = unwrap(await services.bridge.apiPost(ROOT + "/admin-name", {subject_id: person.subject_id,
        ...routeOf(person), native_account_id: person.native_account_id, admin_nickname: nickname,
        expected_revision: person.revision, operation_key: state.pendingNickname.key}));
      state.pendingNickname = null;
      services.onChanged?.(person, result);
      notify(state, "面板备注已保存。");
    } catch (error) {notify(state, errorText(error), true);}
    state.busy = false; redraw(state);
  }
  function mount(panel, person, services) {
    const previousPane = current ? stateFor(activePerson()).pane : null;
    const routes = routeEntries(person);
    current = {panel, person, services, route: routes.length === 1 ? routes[0] : null, card: false};
    const routed = activePerson();
    const state = stateFor(routed);
    if (["access", "relationship", "reply", "contact", "history"].includes(previousPane)) state.pane = previousPane;
    render();
    if (current.route && !state.loaded) void load(routed);
  }
  function selectPane(name) {
    if (!current || !["access", "relationship", "reply", "contact", "history"].includes(name)) return;
    const state = stateFor(activePerson());
    state.pane = name; current.card = false;
    render();
  }
  function refresh() {
    for (const state of states.values()) {state.loaded = false; state.pendingSave = null;}
  }
  window.CompanionPrivateMembers = {mount, refresh, keyOf, selectPane};
})();
