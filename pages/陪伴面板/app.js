const HTTP_API = "/astrbot_plugin_private_companion/page";
const PAGE_ENDPOINT_PREFIX = "page";
const PAGE_PLUGIN_NAME = "astrbot_plugin_private_companion";
const TRANSPARENT_IMAGE = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==";
let cachedPageBridge = null;
let cachedPageEndpointStyle = "";
let pageBridgeProbePromise = null;
let loadAllRequestSeq = 0;

const state = {
  overview: null,
  users: [],
  groups: [],
  diagnostics: [],
  troubleshooting: null,
  availableProviders: [],
  tokenStats: null,
  tokenStatsPartial: false,
  bookshelfUnlocked: null,
  bookshelfAccessToken: "",
  selectedBook: null,
  bookshelfPage: "shelf",
  creativeEditing: false,
  selectedBookSpreadIndex: 0,
  selectedDiaryDate: "",
  selectedBrowsingIndex: 0,
  selectedUserId: "",
  selectedGroupId: "",
  featureDraft: {},
  selectedFeatureKey: "",
  providerFilter: "",
  providerMode: "all",
  providerConfigMode: "",
  providerDraft: {},
  proactiveCandidateFilter: "all",
  imageCacheItems: [],
  imageCacheTotal: 0,
  imageCacheScopes: [],
  imageCacheFilter: "",
  imageCacheScope: "all",
  imageCacheLoaded: false,
  selectedImageCacheKey: "",
  troubleshootingFilter: "all",
  tokenView: "today",
  tokenDate: "",
  worldbookLivingMemory: {},
  worldbookLivingMemoryRequestSeq: 0,
  roleplayPersonaDraft: null,
  configImportPackage: null,
  configImportPreview: null,
  configBackups: [],
  configLastChecks: [],
  activeTab: "dashboard",
  lazyLoaded: {
    diagnostics: false,
    providers: false,
    tokenStats: false,
    configBackups: false,
  },
  pageFontFamily: "original",
  pageTheme: "classic",
};

const hiddenCompatibilityConfigKeys = new Set([
  "enable_semantic_message_debounce",
  "semantic_message_debounce_seconds",
  "skill_growth_passive_injection",
  "skill_growth_custom_skills",
]);

const featureSwitchNotes = {
  enable_skill_growth_simulation: "自定义技能不在这里填写，请到观察页的“技能成长”卡片新增、隐藏、冻结成长或合并别名。",
  enable_food_menu_recommendation: "候选菜单在本功能详情页管理；观察页不再展示这块内容。",
};

const providerLabels = {
  FAST_RESPONSE_PROVIDER_ID: "快速响应模型",
  COMPLEX_REASONING_PROVIDER_ID: "复杂推理模型",
  CREATIVE_MODEL_PROVIDER_ID: "创作模型",
  LLM_PROVIDER_ID: "主模型",
  MAI_STYLE_PROVIDER_ID: "陪伴通用模型",
  DAILY_PLAN_PROVIDER_ID: "日程生成",
  DETAIL_ENHANCEMENT_PROVIDER_ID: "日程细化",
  DREAM_DIARY_PROVIDER_ID: "日记与梦境",
  CREATIVE_PROVIDER_ID: "私下创作",
  CREATIVE_OUTLINE_PROVIDER_ID: "创作大纲",
  CREATIVE_REVIEW_PROVIDER_ID: "创作审校/分析",
  VOICE_PROMPT_PROVIDER_ID: "主动语音文案文本",
  tts_conversion_provider_id: "TTS 转换文本",
  PHOTO_PROMPT_PROVIDER_ID: "生图提示词",
  NARRATION_PROVIDER_ID: "工具结果转述",
  HISTORY_SUMMARY_PROVIDER_ID: "昨日对话摘要",
  RESPONSE_REVIEW_PROVIDER_ID: "回复/主动复核",
  SMART_SILENCE_PROVIDER_ID: "智能沉默判定",
  PROACTIVE_PERSONA_JUDGE_PROVIDER_ID: "主动人格判定",
  TROUBLESHOOTING_PROVIDER_ID: "排障检查",
  SMART_MESSAGE_DEBOUNCE_PROVIDER_ID: "智能收口判断",
  REST_WAKEUP_PROVIDER_ID: "休息醒来判断",
  RELATIONSHIP_ANALYSIS_PROVIDER_ID: "关系站位分析",
  EMOTION_JUDGEMENT_PROVIDER_ID: "情绪变化判断",
  COMPANION_MEMORY_PROVIDER_ID: "长期画像整理",
  DIALOGUE_EPISODE_PROVIDER_ID: "私聊片段整理",
  GROUP_INTERJECT_PROVIDER_ID: "群聊主动插话",
  GROUP_EPISODE_PROVIDER_ID: "群聊片段整理",
  GROUP_SLANG_PROVIDER_ID: "群内黑话释义",
  GROUP_FOLLOWUP_JUDGE_PROVIDER_ID: "群聊续接判断",
  FORWARD_MESSAGE_PROVIDER_ID: "合并消息转述",
  PLUGIN_VISION_PROVIDER_ID: "插件识图模型",
  PRIVATE_READING_VISION_PROVIDER_ID: "夹层阅读视觉模型",
  NEWS_PROVIDER_ID: "新闻整理",
  WEB_EXPLORATION_PROVIDER_ID: "搜索决策/整理",
};

function isProviderConfigKey(key) {
  return Object.prototype.hasOwnProperty.call(providerLabels, key);
}

const privateReadingConfigKeys = new Set([
  "enable_private_reading_integration",
  "enable_private_reading_boredom_read",
  "enable_private_reading_ask_recommendation",
  "enable_private_reading_preference_influence",
  "private_reading_min_interval_hours",
  "private_reading_max_photo_count",
  "private_reading_share_probability",
  "private_reading_ask_probability",
  "private_reading_preference_min_ratings",
  "private_reading_preference_max_terms",
  "private_reading_default_keywords",
  "private_reading_blocked_tags",
  "PRIVATE_READING_VISION_PROVIDER_ID",
]);

const noFallbackProviderKeys = new Set([
  "PRIVATE_READING_VISION_PROVIDER_ID",
]);

const optionalNoFallbackProviderKeys = new Set([
  "FAST_RESPONSE_PROVIDER_ID",
  "COMPLEX_REASONING_PROVIDER_ID",
  "CREATIVE_MODEL_PROVIDER_ID",
  "NARRATION_PROVIDER_ID",
]);

const quickProviderKeys = new Set([
  "FAST_RESPONSE_PROVIDER_ID",
  "COMPLEX_REASONING_PROVIDER_ID",
  "CREATIVE_MODEL_PROVIDER_ID",
  "PLUGIN_VISION_PROVIDER_ID",
]);

const providerPreferenceMeta = {
  speed: {
    label: "低延迟优先",
    className: "speed",
    text: "适合选响应快、成本低、格式稳定的小到中型模型。",
  },
  quality: {
    label: "效果优先",
    className: "quality",
    text: "适合选推理更强、上下文理解更稳、质量更好的模型。",
  },
  balanced: {
    label: "均衡即可",
    className: "balanced",
    text: "优先稳定和成本，不必追求最强模型。",
  },
};

const providerPassiveImpactMeta = {
  direct: {
    label: "影响被动速度",
    className: "direct",
    text: "普通回复或图片/转发阅读前可能会等待它，想更快就选低延迟模型。",
  },
  conditional: {
    label: "特定场景会等待",
    className: "conditional",
    text: "只在对应功能开启或命中时影响本轮回复速度。",
  },
  async: {
    label: "后台异步",
    className: "async",
    text: "通常不阻塞本轮被动回复。",
  },
};

function providerNeedsLowLatency(key) {
  const guide = providerGuides[key] || {};
  return guide.preference === "speed" || ["direct", "conditional"].includes(guide.passiveImpact || "");
}

function normalizeProviderConfigMode(value) {
  const text = String(value || "").trim().toLowerCase();
  const aliases = {
    fast: "quick",
    simple: "quick",
    "快速": "quick",
    "快速配置": "quick",
    precise: "precision",
    advanced: "precision",
    "精准": "precision",
    "精准配置": "precision",
    "分流": "precision",
  };
  const normalized = aliases[text] || text;
  return normalized === "precision" ? "precision" : "quick";
}

function inferProviderConfigMode(overview = state.overview || {}) {
  const settings = overview.settings || {};
  const explicit = String(settings.provider_config_mode || "").trim();
  if (explicit) return normalizeProviderConfigMode(explicit);
  const providers = {
    ...(overview.providers || {}),
    ...(state.providerDraft || {}),
  };
  const hasPrecisionProvider = Object.keys(providerLabels)
    .some((key) => !quickProviderKeys.has(key) && visibleConfigKey(key) && Boolean(providers[key]));
  return hasPrecisionProvider ? "precision" : "quick";
}

function currentProviderConfigMode() {
  return normalizeProviderConfigMode(state.providerConfigMode || inferProviderConfigMode());
}

function providerAllowedInCurrentMode(key) {
  const mode = currentProviderConfigMode();
  return mode === "quick" ? quickProviderKeys.has(key) : !quickProviderKeys.has(key);
}

function syncProviderConfigModeControls() {
  const mode = currentProviderConfigMode();
  const quick = mode === "quick";
  document.querySelectorAll("[data-provider-config-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.providerConfigMode === mode);
  });
  const toolbar = document.querySelector(".provider-toolbar");
  toolbar?.classList.toggle("is-quick-mode", quick);
  const filter = document.getElementById("providerFilter");
  if (filter && quick) filter.value = "";
  document.querySelectorAll("[data-provider-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.providerMode === (state.providerMode || "all"));
  });
  const hint = document.getElementById("providerConfigModeHint");
  if (hint) {
    hint.textContent = quick
      ? "快速配置只显示 4 个场景模型；细分任务会按运行态自动套用这些入口。"
      : "精准配置显示各任务单独 Provider；快速入口不会参与本模式保存。";
  }
}

function setProviderConfigMode(mode, { render = true } = {}) {
  state.providerConfigMode = normalizeProviderConfigMode(mode);
  if (state.providerConfigMode === "quick") {
    state.providerMode = "all";
    state.providerFilter = "";
    const filter = document.getElementById("providerFilter");
    if (filter) filter.value = "";
  }
  syncProviderConfigModeControls();
  if (render) renderProviders();
}

function isPrivateReadingAvailable() {
  return Boolean(state.overview?.private_reading?.available);
}

function toBool(value) {
  if (typeof value === "string") {
    const text = value.trim().toLowerCase();
    if (["true", "1", "yes", "y", "on", "enable", "enabled", "启用", "开启", "开", "是"].includes(text)) return true;
    if (["false", "0", "no", "n", "off", "disable", "disabled", "停用", "关闭", "关", "否", ""].includes(text)) return false;
  }
  return Boolean(value);
}

function normalizeFeatureDraft(features = {}) {
  return Object.fromEntries(Object.entries(features || {}).map(([key, value]) => [key, toBool(value)]));
}

function featureDraftFromOverview(overview = {}) {
  const draft = normalizeFeatureDraft(overview.features || {});
  hiddenCompatibilityConfigKeys.forEach((key) => {
    delete draft[key];
  });
  const settings = overview.settings || {};
  const settingBackedFeatureKeys = [
    "enable_rest_reply_simulation",
    "enable_worldview_perception",
    "enable_group_injection_guard",
    "enable_group_persona_denoise",
    "auto_voice_enabled",
    "auto_voice_full_conversion_enabled",
    "enable_tts_local_playback",
    "enable_tts_local_playback_live_only",
    "enable_tts_live_subtitle_sync",
    "group_repeat_count_distinct_users_only",
  ];
  settingBackedFeatureKeys.forEach((key) => {
    if (Object.prototype.hasOwnProperty.call(settings, key)) {
      draft[key] = toBool(settings[key]);
    }
  });
  return draft;
}

const pluginIntegrationAvailabilityRules = {
  enable_yesterday_screen_diary_context: () => Boolean(state.overview?.screen_companion?.available),
  enable_livingmemory_integration: () => Boolean(state.overview?.livingmemory?.available),
  enable_bilibili_integration: () => Boolean(state.overview?.bilibili?.available),
  enable_bilibili_boredom_watch: () => Boolean(state.overview?.bilibili?.available),
  enable_qzone_integration: () => true,
  enable_qzone_life_publish: () => true,
  enable_qzone_generated_image_publish: () => true,
  enable_qzone_comment_inbox: () => true,
  enable_qzone_emotional_vent_publish: () => Boolean(state.overview?.qzone?.available && toBool(state.featureDraft?.enable_emotion_simulation)),
};

function unavailablePluginIntegrationOwner(key) {
  if (pluginIntegrationAvailabilityRules[key]) {
    return pluginIntegrationAvailabilityRules[key]() ? "" : key;
  }
  for (const [featureKey, settingKeys] of Object.entries(featureSettingGroups || {})) {
    if (!pluginIntegrationAvailabilityRules[featureKey]) continue;
    if ((settingKeys || []).includes(key) && !pluginIntegrationAvailabilityRules[featureKey]()) {
      return featureKey;
    }
  }
  return "";
}

function visibleConfigKey(key) {
  if (hiddenCompatibilityConfigKeys.has(key)) return false;
  if (unavailablePluginIntegrationOwner(key)) return false;
  return isPrivateReadingAvailable() || !privateReadingConfigKeys.has(key);
}

const providerGuides = {
  FAST_RESPONSE_PROVIDER_ID: {
    preference: "speed",
    passiveImpact: "direct",
    purpose: "快速配置入口。给短判断、轻量复核、智能沉默/收口、群聊续接、语音短句、工具转述、新闻和搜索整理等低延迟任务兜底。",
    fit: "适合便宜、响应快、短 JSON/YES-NO 稳定、中文短句自然的小到中型模型。",
    fallback: "只作为未单独填写任务的默认值；单项模型已填写时优先使用单项配置。",
  },
  COMPLEX_REASONING_PROVIDER_ID: {
    preference: "quality",
    passiveImpact: "conditional",
    purpose: "快速配置入口。给主模型兜底、日程生成/细化、长期记忆整理、关系分析、合并消息转述等复杂理解任务兜底。",
    fit: "适合推理更强、长上下文理解和结构化输出更稳的模型。",
    fallback: "只作为未单独填写任务的默认值；主模型或单项模型已填写时优先使用对应配置。",
  },
  CREATIVE_MODEL_PROVIDER_ID: {
    preference: "quality",
    passiveImpact: "async",
    purpose: "快速配置入口。给私下创作、创作大纲/审校、日记梦境和生图提示词等表达/画面感任务兜底。",
    fit: "适合文风稳定、画面感好、中文表达自然、能遵守角色边界的模型。",
    fallback: "只作为未单独填写任务的默认值；创作相关单项模型已填写时优先使用单项配置。",
  },
  LLM_PROVIDER_ID: {
    preference: "quality",
    passiveImpact: "direct",
    purpose: "插件的基础兜底文本模型，主动消息和未单独指定的内部任务都会回退到这里。",
    fit: "适合选综合能力稳定、上下文理解好、指令遵循可靠的主聊天模型。",
    fallback: "留空时使用 AstrBot 默认会话模型。",
  },
  MAI_STYLE_PROVIDER_ID: {
    preference: "quality",
    passiveImpact: "conditional",
    purpose: "私聊互动策略通用模型，也是多数分项能力留空后的第一层兜底。",
    fit: "适合稳定、成本可控、中文口语自然、能守住人格边界的模型。",
    fallback: "留空时跟随主模型。",
  },
  DAILY_PLAN_PROVIDER_ID: {
    preference: "quality",
    passiveImpact: "async",
    purpose: "每天生成粗日程，并在格式异常时做日程重试纠偏。",
    fit: "适合结构化 JSON 稳定、能理解人格/世界观、长一点提示词也不容易跑偏的模型。",
    fallback: "留空时跟随陪伴通用模型。",
  },
  DETAIL_ENHANCEMENT_PROVIDER_ID: {
    preference: "speed",
    passiveImpact: "async",
    purpose: "把当前日程段展开成细节事件、状态变化和可能主动开口的契机。",
    fit: "适合便宜、低延迟、JSON 输出稳定的小到中型模型。",
    fallback: "留空时跟随主模型。",
  },
  DREAM_DIARY_PROVIDER_ID: {
    preference: "quality",
    passiveImpact: "async",
    purpose: "生成每日 Bot 日记、生活碎片、梦境碎片、强化梦境和梦后余韵。",
    fit: "适合短文本意象好、口语自然、能按要求输出 JSON 的模型。",
    fallback: "留空时跟随陪伴通用模型。",
  },
  CREATIVE_PROVIDER_ID: {
    preference: "quality",
    passiveImpact: "async",
    purpose: "生成私下创作项目设定，以及闲暇时的小说、诗、随笔、剧本等正文片段。",
    fit: "适合文风稳定、有创作能力、能遵守角色身份边界的模型。",
    fallback: "留空时跟随陪伴通用模型。",
  },
  CREATIVE_OUTLINE_PROVIDER_ID: {
    preference: "speed",
    passiveImpact: "async",
    purpose: "每次续写前整理本段短大纲，帮助作品沿着项目设定推进。",
    fit: "适合便宜、低延迟、结构化短输出稳定的模型。",
    fallback: "留空时跟随私下创作模型。",
  },
  CREATIVE_REVIEW_PROVIDER_ID: {
    preference: "quality",
    passiveImpact: "async",
    purpose: "检查创作片段是否重复、是否推进、是否违背人工大纲/角色表，并抽取故事档案。",
    fit: "适合 JSON 稳定、审稿判断可靠的模型。",
    fallback: "留空时跟随私下创作模型。",
  },
  VOICE_PROMPT_PROVIDER_ID: {
    preference: "speed",
    passiveImpact: "conditional",
    purpose: "生成主动语音短句，并修复 TTS 标签、日语或双语格式；这是文本模型，不负责合成音频。",
    fit: "适合短句口语感强、格式遵循稳、不会写得太长的文本模型。",
    note: "阿里云百炼/CosyVoice 等语音合成模型需要在 AstrBot 的 TTS provider 配置里设置，本插件这里只读取当前会话 TTS provider。",
    fallback: "留空时跟随陪伴通用模型。",
  },
  tts_conversion_provider_id: {
    preference: "speed",
    passiveImpact: "conditional",
    purpose: "用于 TTS 后处理判断、翻译、语种修正和中文释义补全；这是文本模型，不负责合成音频。",
    fit: "适合低延迟、短句翻译自然、格式遵循稳定的小到中型模型。",
    fallback: "留空时后处理模式保持纯文本，显式语音标签仍可由插件处理。",
  },
  PHOTO_PROMPT_PROVIDER_ID: {
    preference: "quality",
    passiveImpact: "async",
    purpose: "只负责生成 photo_text 的画面提示词和画面描述，不影响普通聊天或日程。",
    fit: "适合视觉描述、审美词汇和画面构图更稳定的模型。",
    fallback: "留空时跟随主模型。",
  },
  NARRATION_PROVIDER_ID: {
    preference: "speed",
    passiveImpact: "conditional",
    purpose: "把识屏等工具结果转成可供最终主动消息使用的自然语言上下文。",
    fit: "适合摘要稳、保留关键事实、不会添油加醋的便宜模型。",
    fallback: "留空时不单独转述，直接使用工具摘要。",
  },
  HISTORY_SUMMARY_PROVIDER_ID: {
    preference: "balanced",
    passiveImpact: "async",
    purpose: "把昨日或最近完整对话压成能延续到日程、梦境和主动理解里的摘要。",
    fit: "适合长上下文整理稳定、成本较低、能保留人物和时间线的模型。",
    fallback: "留空时跟随日程生成模型。",
  },
  RESPONSE_REVIEW_PROVIDER_ID: {
    preference: "speed",
    passiveImpact: "conditional",
    purpose: "用于被动回复轻量自检，以及主动消息发送前判断是否值得现在发送、是否需要轻改写/延后/取消。",
    fit: "适合便宜、短文本改写自然、边界判断稳的模型。",
    fallback: "留空时回退到陪伴通用模型，再回退到主模型。",
  },
  SMART_SILENCE_PROVIDER_ID: {
    preference: "speed",
    passiveImpact: "conditional",
    purpose: "用户疑似表达不想继续当前话题时，判断这条待发送回复该不该直接静默取消。",
    fit: "适合低延迟、短 JSON 稳定、边界判断保守的小模型。",
    fallback: "留空时跟随回复/主动复核模型，再回退到智能收口或陪伴通用模型。",
  },
  PROACTIVE_PERSONA_JUDGE_PROVIDER_ID: {
    preference: "speed",
    passiveImpact: "async",
    purpose: "主动计划到点后，先判断这个念头是否贴合当前角色、世界观、关系温度和打扰边界。",
    fit: "适合 JSON 输出稳定、角色边界感强、短文本判断保守的小到中型模型。",
    fallback: "留空时复用回复/主动复核模型，再回退到陪伴通用模型。",
  },
  TROUBLESHOOTING_PROVIDER_ID: {
    preference: "speed",
    passiveImpact: "async",
    purpose: "扩展页排障中心的模型复核，例如技能相似项、配置异常和后续诊断类检查。",
    fit: "适合便宜、低延迟、指令遵循稳定、分类判断保守的小模型。",
    fallback: "留空时先跟随回复/主动复核模型，再回退到陪伴通用模型和主模型。",
  },
  SMART_MESSAGE_DEBOUNCE_PROVIDER_ID: {
    preference: "speed",
    passiveImpact: "direct",
    purpose: "智能收口只在本地规则不确定时调用，用来判断用户是不是还没说完。",
    fit: "适合低延迟、YES/NO 或短 JSON 稳定、误判少的小模型。",
    fallback: "留空时跟随插件主模型。",
  },
  REST_WAKEUP_PROVIDER_ID: {
    preference: "speed",
    passiveImpact: "conditional",
    purpose: "休息/睡眠状态下判断这轮消息是否值得醒来回复。",
    fit: "适合低延迟、分类保守、能识别紧急/明确呼唤的小模型。",
    fallback: "留空时优先跟随回复/主动复核模型，再回退主模型。",
  },
  RELATIONSHIP_ANALYSIS_PROVIDER_ID: {
    preference: "quality",
    passiveImpact: "async",
    purpose: "分析关系阶段、亲近度、打扰边界和互动站位，影响后续语气判断。",
    fit: "适合情绪和关系判断细腻、分类稳定、不会过度脑补的模型。",
    fallback: "留空时跟随陪伴通用模型。",
  },
  EMOTION_JUDGEMENT_PROVIDER_ID: {
    preference: "speed",
    passiveImpact: "async",
    purpose: "可选复核用户消息是否会触发 Bot 自身短期情绪余波，只做分类判断，不生成回复。",
    fit: "适合便宜、低延迟、JSON 稳定、对玩笑/反讽/第三方抱怨判断保守的小模型。",
    fallback: "留空时先跟随排障检查模型，再回退到关系站位、陪伴通用和主模型。",
  },
  COMPANION_MEMORY_PROVIDER_ID: {
    preference: "quality",
    passiveImpact: "async",
    purpose: "把原始私聊记忆整理成用户画像、兴趣、边界、关系备注和说话习惯。",
    fit: "适合结构化抽取能力好、便宜、能区分事实和推测的模型。",
    fallback: "留空时跟随陪伴通用模型。",
  },
  DIALOGUE_EPISODE_PROVIDER_ID: {
    preference: "quality",
    passiveImpact: "async",
    purpose: "把私聊片段整理成共同经历、情绪余味、可续话头和未完成约定。",
    fit: "适合对话摘要稳、能保留细节和情绪温度的模型。",
    fallback: "留空时跟随陪伴通用模型。",
  },
  GROUP_INTERJECT_PROVIDER_ID: {
    preference: "speed",
    passiveImpact: "conditional",
    purpose: "群聊主动插话专用，用来生成很短、自然、不突兀的群聊发言。",
    fit: "适合低延迟、短文本质量好、中文群聊语感稳的模型。",
    fallback: "留空时跟随陪伴通用模型。",
  },
  GROUP_EPISODE_PROVIDER_ID: {
    preference: "balanced",
    passiveImpact: "async",
    purpose: "整理群聊最近片段、群氛围、话题线、活跃群友和短期避免重复内容。",
    fit: "适合群聊摘要、多人关系和话题归纳稳定的便宜模型。",
    fallback: "留空时跟随陪伴通用模型。",
  },
  GROUP_SLANG_PROVIDER_ID: {
    preference: "speed",
    passiveImpact: "async",
    purpose: "根据群聊样例解释群内黑话、梗、简称和成员称呼。",
    fit: "适合小模型；重点是分类/释义稳定、别把玩笑当事实。",
    fallback: "留空时跟随陪伴通用模型。",
  },
  GROUP_FOLLOWUP_JUDGE_PROVIDER_ID: {
    preference: "speed",
    passiveImpact: "direct",
    purpose: "判断群里用户后续没 @ 的话是否仍在和 Bot 对话，只在规则不确定时调用。",
    fit: "适合便宜、低延迟、YES/NO 分类准确、指令遵循稳定的小模型。",
    fallback: "留空时先跟随快速响应模型；快速响应模型也留空时只使用规则判断。",
  },
  FORWARD_MESSAGE_PROVIDER_ID: {
    preference: "balanced",
    passiveImpact: "direct",
    purpose: "合并消息选择“转述”模式时，先把合并转发读成自然记录，再交给主模型回应。",
    fit: "适合长上下文整理稳定、成本较低、能保留人物和时间线的模型。",
    fallback: "留空时跟随陪伴通用模型。",
  },
  PLUGIN_VISION_PROVIDER_ID: {
    preference: "quality",
    passiveImpact: "direct",
    purpose: "插件自己的通用视觉理解模型，用于私聊图片/表情包、引用图片、合并消息图片和识屏。",
    fit: "适合确认支持图片输入、视觉描述可靠、能简短转述关键信息的多模态模型。",
    fallback: "留空时先尝试 AstrBot 本体图片转述模型，再回退到工具结果转述或主模型。",
  },
  PRIVATE_READING_VISION_PROVIDER_ID: {
    preference: "quality",
    passiveImpact: "async",
    purpose: "夹层阅读专用：理解封面和抽样页，生成页边批注、读后感、评分和偏好标签。",
    fit: "必须是支持图片输入的视觉模型，最好能稳定输出 JSON，并能看懂漫画页图细节。",
    fallback: "不回退。留空或模型不可用时，不生成私密阅读批注和读后感。",
  },
  NEWS_PROVIDER_ID: {
    preference: "speed",
    passiveImpact: "async",
    purpose: "从新闻标题和摘要候选里挑选适合分享的内容，并整理成 Bot 的内部印象。",
    fit: "适合便宜、稳定、短 JSON 输出可靠、能做轻量筛选的小模型。",
    fallback: "留空时跟随主动转述/主模型。",
  },
  WEB_EXPLORATION_PROVIDER_ID: {
    preference: "speed",
    passiveImpact: "async",
    purpose: "不负责联网检索，只决定 Bot 想搜索什么，并把搜索结果整理成探索笔记。",
    fit: "适合便宜、稳定、短 JSON 输出可靠、能归纳搜索结果的模型。",
    fallback: "留空时跟随新闻整理/主模型。",
  },
};

const providerGroups = [
  {
    id: "quick",
    title: "快速配置",
    desc: "按使用场景配置 4 个模型；高级单项留空时会自动套用这里的配置。",
    keys: ["FAST_RESPONSE_PROVIDER_ID", "COMPLEX_REASONING_PROVIDER_ID", "CREATIVE_MODEL_PROVIDER_ID", "PLUGIN_VISION_PROVIDER_ID"],
  },
  {
    id: "core",
    title: "基础与兜底",
    desc: "主模型、陪伴通用和最终回复前后的基础能力。",
    keys: ["LLM_PROVIDER_ID", "MAI_STYLE_PROVIDER_ID", "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID", "SMART_SILENCE_PROVIDER_ID", "RESPONSE_REVIEW_PROVIDER_ID", "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID", "REST_WAKEUP_PROVIDER_ID", "TROUBLESHOOTING_PROVIDER_ID", "NARRATION_PROVIDER_ID"],
  },
  {
    id: "daily",
    title: "日程与表达",
    desc: "决定 Bot 每天做什么、怎么把生活片段和主动表达写出来。",
    keys: ["DAILY_PLAN_PROVIDER_ID", "DETAIL_ENHANCEMENT_PROVIDER_ID", "DREAM_DIARY_PROVIDER_ID", "CREATIVE_PROVIDER_ID", "CREATIVE_OUTLINE_PROVIDER_ID", "CREATIVE_REVIEW_PROVIDER_ID", "VOICE_PROMPT_PROVIDER_ID", "tts_conversion_provider_id", "PHOTO_PROMPT_PROVIDER_ID"],
  },
  {
    id: "memory",
    title: "记忆与关系",
    desc: "整理长期画像、对话片段和关系站位。",
    keys: ["HISTORY_SUMMARY_PROVIDER_ID", "RELATIONSHIP_ANALYSIS_PROVIDER_ID", "EMOTION_JUDGEMENT_PROVIDER_ID", "COMPANION_MEMORY_PROVIDER_ID", "DIALOGUE_EPISODE_PROVIDER_ID"],
  },
  {
    id: "group",
    title: "群聊能力",
    desc: "处理群聊插话、片段整理、黑话释义和续接判断。",
    keys: ["GROUP_INTERJECT_PROVIDER_ID", "GROUP_EPISODE_PROVIDER_ID", "GROUP_SLANG_PROVIDER_ID", "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID", "FORWARD_MESSAGE_PROVIDER_ID"],
  },
  {
    id: "media",
    title: "视觉与外界信息",
    desc: "识图、新闻和主动搜索相关模型。",
    keys: ["PRIVATE_READING_VISION_PROVIDER_ID", "NEWS_PROVIDER_ID", "WEB_EXPLORATION_PROVIDER_ID"],
  },
];

const providerGroupByKey = providerGroups.reduce((acc, group) => {
  group.keys.forEach((key) => { acc[key] = group; });
  return acc;
}, {});

const featureMeta = {
  enable_proactive_only_mode: ["仅保留主动能力", "只让本插件负责主动私聊调度、生成和发送；普通私聊/群聊放行给默认主链或其他插件。"],
  enable_mai_style_integration: ["私聊互动策略", "把相处分寸、偏好和本轮接话方式注入回复。"],
  enable_companion_memory: ["长期画像", "沉淀用户偏好、边界、关系线索和可复用事实。"],
  enable_expression_learning: ["表达节奏学习", "统计用户句长、标点、句尾和短句节奏，只影响回复口感。"],
  enable_expression_manual_review: ["表达样本审核", "新样本先进入私聊对象的待审核列表，通过后才会参与表达画像。"],
  enable_expression_style_review: ["表达发送前审核", "发送前检查表达学习过头、异常断句、照抄样本等问题。"],
  enable_intent_emotion_analysis: ["本地意图/情绪快判", "用带置信度的本地规则识别求助、低落、玩笑、亲近和边界。"],
  enable_response_self_review: ["回复/主动复核", "被动回复做轻量自检；主动消息发送前判断是否值得现在发送、是否需要改写或延后。"],
  enable_smart_silence: ["智能沉默", "用户明确不想继续话题时，让小模型决定本轮是否直接不回复。"],
  enable_llm_timer_scheduling: ["对话临时预约", "把聊天里自然形成的稍后提醒、叫醒、回头说等约定转写成 AstrBot 官方定时计划；插件本身不再单独调度。"],
  enable_passive_topic_suppression: ["话题抑制", "避免短时间反复主动提同一个话题。"],
  enable_relationship_state_machine: ["关系距离感", "根据亲近、冷淡、边界和回应情况调整相处分寸。"],
  enable_emotion_simulation: ["情绪模拟", "维护 Bot 自身被刺到、缓和、恢复和短暂回避的余波。"],
  enable_dialogue_episode_memory: ["私聊片段", "把连续对话整理成共同经历和可续话头。"],
  enable_open_loop_tracking: ["未完话头", "记住对话里还留着、之后可能会回头接的事。"],
  enable_user_habit_learning: ["用户习惯画像", "学习用户常在什么时段做什么、问什么；被动只在相关时理解，主动可到点关心。"],
  enable_food_menu_recommendation: ["吃什么候选", "管理常吃菜、菜馆和外卖；用户纠结吃什么时，只取少量贴合项作为回复参考。"],
  enable_humanized_states: ["拟人身体状态", "生成精力、睡眠、梦境、健康、饥饿和周期等扮演状态，影响日程、主动消息和被动语气。"],
  enable_health_state: ["健康/不适状态", "开启后视为可用，允许当前扮演状态出现生病、不舒服或恢复尾声。"],
  enable_hunger_state: ["饥饿/胃口状态", "开启后视为可用，允许当前扮演状态出现饿、胃口不好或想吃东西。"],
  enable_qq_presence_sync: ["同步 QQ 在线状态", "让日程细化把在线/忙碌等基础状态同步到 QQ；不包含自定义短状态。"],
  enable_qq_custom_presence_sync: ["同步 QQ 自定义短状态", "默认关闭；仅协议端明确支持时再开启，用于“专注中/休息中”等短状态。"],
  enable_segmented_proactive_reply: ["分段发送", "按作用范围把主动消息或全部 LLM 纯文本回复拆成更像聊天的短句，并合并过短片段。"],
  inject_passive_states: ["被动状态注入", "普通聊天前注入“当前扮演状态”，只影响语气、长短和节奏。"],
  enable_passive_state_delta_injection: ["被动状态增量注入", "同一会话只在状态首次出现、明显变化或用户询问近况时注入短状态摘要，减少重复动态提示词。"],
  enable_cycle_state: ["生理期模拟", "开启后即视为适用，允许当前扮演状态偶尔加入生理期前、处于生理期或生理期后的状态。"],
  enable_skill_growth_simulation: ["技能成长", "能力状态与边界；自定义技能请到观察页的技能成长卡片管理。"],
  enable_message_debounce: ["消息收口防抖", "把文本、图片、转发后的补充说明合并进同一轮；旧版语义收口等待已并入文本补话等待。"],
  enable_smart_message_debounce: ["智能文本收口", "先本地快判明确完整文本；“知道吗/问你个事/你猜”等短引子会先等补话。"],
  enable_recall_enhancement: ["撤回增强", "感知撤回事件，支持发送前取消回复、短期防撤回转述和违禁词自动撤回。"],
  enable_recall_cancel_reply: ["撤回取消回复", "撤回增强的子能力：触发/唤醒消息在 Bot 发出回复前被撤回时，静默取消本次回复和后续分段。"],
  enable_recall_message_cache: ["撤回消息缓存", "撤回增强的子能力：短期缓存消息摘要，撤回后可在过期前转述。"],
  enable_recall_transcribe_command: ["撤回转述命令", "允许通过命令查看当前会话最近撤回消息。"],
  enable_forbidden_word_recall: ["违禁词自动撤回", "命中配置词表时，拦截 Bot 待发送内容或尝试撤回群聊/自身消息。"],
  enable_private_image_self_recognition: ["图片转述增强", "处理私聊单图、引用图片、合并转发图片和 GIF 抽帧，并辅助判断角色归属。"],
  enable_environment_perception: ["环境感知", "注入当前时间、日期语境、平台、群聊/私聊和消息媒介信息。"],
  enable_holiday_perception: ["节假日感知", "识别工作日、周末、节假日和调休，影响生活节奏判断。"],
  enable_platform_perception: ["平台感知", "识别 QQ/平台、私聊/群聊、群号群名以及图片语音视频消息。"],
  enable_model_perception: ["模型感知", "识别当前会话 LLM、视觉转述模型和生图后端/图片模型配置。"],
  enable_worldview_perception: ["世界观适配感知", "把插件能力和生活语境转换成当前人设世界观说法，默认关闭，避免和 AstrBot 人设重复。"],
  enable_lunar_perception: ["农历感知", "可用时注入农历日期，辅助节日、生活氛围和日记语境。"],
  enable_solar_term_perception: ["节气感知", "注入当天或临近节气，让日程和表达更贴合时令。"],
  enable_almanac_perception: ["轻量黄历", "生成宜/忌氛围标签，默认关闭，避免玄学感太强。"],
  enable_yesterday_screen_diary_context: ["昨日屏幕日记", "每天只读取 screen_companion 的昨日观察日记脱敏摘要，作为今日状态和日程背景，不读取实时屏幕。"],
  enable_group_companion: ["群聊总开关", "控制是否处理群聊观察、黑话和上下文注入。"],
  enable_group_slang_learning: ["群黑话学习", "记录群内常用梗、简称和特殊表达。"],
  enable_group_member_profiles: ["群内成员观察", "记录成员在当前群里的近期发言、短句和活跃痕迹。"],
  enable_group_context_injection: ["群上下文注入", "在群聊回复时加入群氛围、话题和成员信息。"],
  enable_group_injection_guard: ["群聊防注入", "拦截群友通过改称呼、改语气、改设定或改输出格式污染群聊上下文和长期观察。"],
  enable_group_persona_denoise: ["群聊人格降噪", "降低群聊里的私聊腔、状态汇报和私聊关系外溢。"],
  enable_forward_message_adaptation: ["合并消息阅读", "读取合并转发节点并整理成自然聊天记录，让 Bot 能理解转发里的发言顺序、人物和话题。"],
  enable_group_scene_awareness: ["群聊场景感知", "推断当前消息是在对 Bot、某个群友还是整个群说话，减少误以为别人都在问自己。"],
  enable_group_reality_promise_guard: ["阻止群聊现实承诺", "群聊里避免承诺自己能拉人、修网、开房间或操作现实设备；私聊扮演不受影响。"],
  enable_group_wakeup_enhancement: ["群聊唤醒强化", "通过强唤醒词、弱相关唤醒词和兴趣关键词，让 Bot 在群里被自然叫到或碰到感兴趣话题时进入回复链。"],
  enable_group_high_intensity_mode: ["群聊高强度收口", "短时间连续被 @、引用或增强唤醒后，按配置合并后续唤醒消息；冷却残留只降载，不延迟单条明确消息。"],
  enable_group_conversation_followup: ["连续对话保持", "群里叫过 Bot 后，短时间内判断同一用户没继续 @ 的话是否仍在对 Bot 说。"],
  enable_group_interjection: ["群主动插话", "允许 Bot 在群聊里主动插一句。谨慎开启。"],
  enable_group_repeat_follow: ["复读处理", "同一句话连续复读达到阈值时，可跟读一次或打断一次。"],
  enable_group_topic_threads: ["群话题线", "维护当前群聊正在聊什么，以及话题如何变化。"],
  enable_group_episode_memory: ["群聊片段", "把群聊阶段性内容整理成摘要片段。"],
  enable_group_interjection_feedback: ["插话反馈", "记录群友对主动插话的反应，后续调整频率。"],
  enable_group_slang_meanings: ["黑话释义", "解释群内黑话。"],
  enable_group_slang_web_search: ["黑话联网参考", "为已有黑话候选搜索外部解释，并判断是否匹配本群用法。默认关闭。"],
  enable_group_relationship_graph: ["群友互动图", "记录成员之间近期谁常互相接话、玩梗或争论。"],
  enable_group_privacy_guard: ["群隐私保护", "保护私聊信息。"],
  enable_worldbook_member_recognition: ["群聊关系网", "以 QQ 号确认稳定身份，关系备注和重要记忆都放在这里。"],
  enable_cross_user_memory_bridge: ["跨用户记忆互通", "主人可在私聊中查询 Bot 与某个用户或群聊的近期互动摘要；只读，不发送消息。"],
  enable_atrelay_tools: ["跨群转述与 @ 群友", "整合艾特群友能力，可让模型查询群成员、按关系网解析 @ 对象并发送群聊/私聊消息。"],
  enable_livingmemory_integration: ["LivingMemory 协同", "引导模型按需调用长期记忆工具，避免重复造轮子。"],
  enable_bilibili_integration: ["B站 AI Bot 联动", "读取 B站 AI Bot 观看日志，并在合适节点私聊分享。"],
  enable_bilibili_boredom_watch: ["无聊刷 B 站", "空档看视频。"],
  enable_news_integration: ["新闻阅读", "低频读取 RSS/Atom 新闻源，形成近期见闻和主动分享素材。"],
  enable_news_daily_hot_read: ["每日热点", "随日程或后台检查读取热点候选，形成当天的时讯见闻。"],
  enable_news_boredom_read: ["无聊看新闻", "空档或无聊时扫几条新闻，按人格决定是否私聊提起。"],
  enable_ai_daily_watch: ["AI 日报/早报追踪", "按配置时间读取黑鸦早报和橘鸦日报，到点后当天只尝试一次。"],
  enable_external_event_self_link: ["外界信息自我关联", "让新闻和搜索结果先变成“这和我有什么关系”的内部意愿，再进入主动候选。"],
  enable_web_exploration: ["主动搜索", "按人格兴趣、最近话题、日程和心情低频使用 AstrBot 网页搜索，形成探索笔记。"],
  enable_web_exploration_boredom_search: ["空档自主搜索", "空闲或无聊时先自行决定搜索主题，再调用网页搜索了解新鲜事物。"],
  enable_qzone_integration: ["QQ 空间动态", "整合查看、点赞、评论和发布说说入口。"],
  enable_qzone_life_publish: ["生活说说", "根据状态、日程和日记余味低频发布公开生活动态。"],
  enable_qzone_generated_image_publish: ["说说配图", "发布生活说说时可按概率调用主动生图能力生成配图。"],
  enable_qzone_comment_inbox: ["评论收件箱", "低频查看自己说说下的新评论，并按需公开追加回复。"],
  enable_photo_text_action: ["主动拍照/生图", "允许 Bot 在合适的主动动机下生成真实图片；本地 ComfyUI 可在电脑忙时自动延后。"],
  enable_private_reading_integration: ["夹层阅读素材", "检测到可用素材能力时，允许作为低频私下阅读来源。"],
  enable_private_reading_boredom_read: ["私下阅读", "空档、无聊或夜里低频自己搜索并阅读，形成内部印象。"],
  enable_private_reading_ask_recommendation: ["征求推荐", "空档或无聊时，低频私聊询问用户有没有合适的私密阅读推荐。"],
  enable_private_reading_preference_influence: ["私密偏好影响", "评分样本足够后，把稳定偏好作为私聊私密互动的弱背景。"],
  enable_unanswered_screen_peek_followup: ["沉默后窥屏", "主动消息后用户长时间没回、且 Bot 正好无聊时，可免日次数窥屏确认用户在做什么。"],
  enable_tts_enhancement: ["TTS强化", "支持中文聊天文本搭配外语语音块，统一处理生成路径、<tts> 标签规范化、语种控制、朗读文本清洗和主用户触发。"],
  enable_proactive_quote_trigger_message: ["引用触发消息", "群聊回复、群主动插话和可追溯的私聊主动消息会引用触发消息；复读跟读/打断不引用。"],
  enable_creative_writing: ["私下创作", "闲暇时可选地因生活小事、日记碎片或梦境灵感写一点文本作品。"],
  creative_hidden_mode: ["低调创作模式", "默认不汇报创作，只在节点或用户询问时自然提起。"],
};

const featureGroups = [
  {
    title: "通用能力",
    note: "私聊、群聊和主动链路都会参考的状态、媒介、收口与发送能力。",
    keys: [
      "enable_humanized_states",
      "enable_segmented_proactive_reply",
      "inject_passive_states",
      "enable_cycle_state",
      "enable_skill_growth_simulation",
      "enable_message_debounce",
      "enable_recall_enhancement",
      "enable_private_image_self_recognition",
      "enable_forward_message_adaptation",
      "enable_llm_timer_scheduling",
      "enable_proactive_quote_trigger_message",
      "enable_tts_enhancement",
    ],
  },
  {
    title: "私聊陪伴",
    note: "关系、记忆、回复策略和自然表达。",
    keys: [
      "enable_mai_style_integration",
      "enable_companion_memory",
      "enable_expression_learning",
      "enable_intent_emotion_analysis",
      "enable_response_self_review",
      "enable_passive_topic_suppression",
      "enable_relationship_state_machine",
      "enable_dialogue_episode_memory",
      "enable_open_loop_tracking",
      "enable_user_habit_learning",
      "enable_food_menu_recommendation",
    ],
  },
  {
    title: "情绪模拟",
    note: "Bot 自身短期情绪余波、收敛和可选公开心情动态。",
    keys: [
      "enable_emotion_simulation",
    ],
  },
  {
    title: "群聊观察",
    note: "群氛围、黑话、话题线、插话和隐私边界。",
    keys: [
      "enable_group_companion",
      "enable_group_context_injection",
      "enable_group_injection_guard",
      "enable_group_persona_denoise",
      "enable_group_scene_awareness",
      "enable_group_reality_promise_guard",
      "enable_group_wakeup_enhancement",
      "enable_group_high_intensity_mode",
      "enable_group_conversation_followup",
      "enable_group_slang_learning",
      "enable_group_slang_meanings",
      "enable_group_member_profiles",
      "enable_group_topic_threads",
      "enable_group_episode_memory",
      "enable_group_relationship_graph",
      "enable_group_interjection",
      "enable_group_repeat_follow",
      "enable_group_interjection_feedback",
      "enable_group_privacy_guard",
    ],
  },
  {
    title: "环境感知",
    note: "时间、节假日、农历节气、平台和消息媒介。",
    keys: [
      "enable_environment_perception",
      "enable_holiday_perception",
      "enable_platform_perception",
      "enable_model_perception",
      "enable_worldview_perception",
      "enable_lunar_perception",
      "enable_solar_term_perception",
      "enable_almanac_perception",
      "enable_yesterday_screen_diary_context",
    ],
  },
  {
    title: "身份与记忆联动",
    note: "QQ 关系网、外部长期记忆和身份稳定识别。",
    keys: [
      "enable_worldbook_member_recognition",
      "enable_cross_user_memory_bridge",
      "enable_atrelay_tools",
      "enable_livingmemory_integration",
    ],
  },
  {
    title: "长线主动",
    note: "外部动作和低频分享。",
    keys: [
      "enable_bilibili_integration",
      "enable_bilibili_boredom_watch",
      "enable_news_integration",
      "enable_news_daily_hot_read",
      "enable_ai_daily_watch",
      "enable_news_boredom_read",
      "enable_external_event_self_link",
      "enable_web_exploration",
      "enable_web_exploration_boredom_search",
      "enable_qzone_integration",
      "enable_qzone_life_publish",
      "enable_qzone_comment_inbox",
      "enable_photo_text_action",
      "enable_private_reading_integration",
      "enable_private_reading_boredom_read",
      "enable_private_reading_ask_recommendation",
      "enable_private_reading_preference_influence",
      "enable_unanswered_screen_peek_followup",
      "enable_creative_writing",
      "creative_hidden_mode",
    ],
  },
];

const embeddedFeatureParentByKey = {
  inject_passive_states: "enable_humanized_states",
  enable_passive_state_delta_injection: "inject_passive_states",
  enable_health_state: "enable_humanized_states",
  enable_hunger_state: "enable_humanized_states",
  enable_cycle_state: "enable_humanized_states",
  enable_rest_reply_simulation: "enable_humanized_states",
  enable_recall_cancel_reply: "enable_recall_enhancement",
  enable_recall_message_cache: "enable_recall_enhancement",
  enable_recall_transcribe_command: "enable_recall_enhancement",
  enable_forbidden_word_recall: "enable_recall_enhancement",
  enable_semantic_message_debounce: "enable_message_debounce",
  enable_smart_message_debounce: "enable_message_debounce",
  enable_private_image_gif_enhancement: "enable_private_image_self_recognition",
  enable_holiday_perception: "enable_environment_perception",
  enable_platform_perception: "enable_environment_perception",
  enable_model_perception: "enable_environment_perception",
  enable_worldview_perception: "enable_environment_perception",
  enable_lunar_perception: "enable_environment_perception",
  enable_solar_term_perception: "enable_environment_perception",
  enable_almanac_perception: "enable_environment_perception",
  enable_group_context_injection: "enable_group_companion",
  enable_group_injection_guard: "enable_group_companion",
  enable_group_persona_denoise: "enable_group_companion",
  enable_group_reality_promise_guard: "enable_group_companion",
  enable_group_high_intensity_mode: "enable_group_wakeup_enhancement",
  enable_group_conversation_followup: "enable_group_scene_awareness",
  enable_group_slang_meanings: "enable_group_slang_learning",
  enable_group_interjection_feedback: "enable_group_interjection",
  group_repeat_trigger_threshold: "enable_group_repeat_follow",
  group_repeat_count_distinct_users_only: "enable_group_repeat_follow",
  enable_bilibili_boredom_watch: "enable_bilibili_integration",
  enable_news_daily_hot_read: "enable_news_integration",
  enable_ai_daily_watch: "enable_news_integration",
  enable_news_boredom_read: "enable_news_integration",
  enable_external_event_self_link: "enable_news_integration",
  enable_web_exploration_boredom_search: "enable_web_exploration",
  enable_qzone_life_publish: "enable_qzone_integration",
  enable_qzone_generated_image_publish: "enable_qzone_integration",
  enable_qzone_comment_inbox: "enable_qzone_integration",
  enable_qzone_emotional_vent_publish: "enable_emotion_simulation",
  enable_private_reading_boredom_read: "enable_private_reading_integration",
  enable_private_reading_ask_recommendation: "enable_private_reading_integration",
  enable_private_reading_preference_influence: "enable_private_reading_integration",
  auto_voice_enabled: "enable_tts_enhancement",
  auto_voice_full_conversion_enabled: "enable_tts_enhancement",
  enable_tts_local_playback: "enable_tts_enhancement",
  enable_tts_local_playback_live_only: "enable_tts_enhancement",
  enable_tts_live_subtitle_sync: "enable_tts_enhancement",
  creative_hidden_mode: "enable_creative_writing",
};

const embeddedFeatureKeys = new Set(Object.keys(embeddedFeatureParentByKey));

const proactiveOnlyLockedFeatureKeys = new Set([
  "inject_passive_states",
  "enable_intent_emotion_analysis",
  "enable_llm_timer_scheduling",
  "enable_passive_topic_suppression",
  "enable_environment_perception",
  "enable_message_debounce",
  "enable_recall_enhancement",
  "enable_private_image_self_recognition",
  "enable_forward_message_adaptation",
  "enable_group_companion",
  "enable_skill_growth_passive_injection",
  "enable_private_reading_preference_influence",
  "enable_worldbook_member_recognition",
  "enable_atrelay_tools",
  "enable_livingmemory_integration",
  "enable_tts_enhancement",
  "enable_segmented_proactive_reply",
]);

function proactiveOnlyModeEnabled() {
  return toBool(state.featureDraft?.enable_proactive_only_mode);
}

function featureLockedByProactiveOnlyMode(key) {
  if (key === "enable_proactive_only_mode") return false;
  return proactiveOnlyModeEnabled() && (
    proactiveOnlyLockedFeatureKeys.has(key)
    || proactiveOnlyLockedFeatureKeys.has(topLevelFeatureKey(key))
  );
}

function proactiveOnlyUnlockedKeys() {
  const items = state.overview?.proactive_only?.unlocked || [];
  return new Set((Array.isArray(items) ? items : []).map((item) => String(item.key || item || "").trim()).filter(Boolean));
}

function featureTemporarilyUnlockedByProactiveOnly(key) {
  if (!proactiveOnlyModeEnabled()) return false;
  const unlocks = proactiveOnlyUnlockedKeys();
  return unlocks.has("all") || unlocks.has(key) || unlocks.has(topLevelFeatureKey(key));
}

function proactiveOnlyRelatedUnlocks(key) {
  const related = state.overview?.proactive_only?.related || {};
  return Array.isArray(related[key]) ? related[key] : [];
}

function visibleFeatureSwitchKey(key) {
  if (key === "enable_proactive_only_mode") return false;
  if (hiddenCompatibilityConfigKeys.has(key)) return false;
  const detailSettingKeys = new Set(Object.values(featureSettingGroups || {}).flat());
  const groupedFeatureKeys = new Set(featureGroups.flatMap((group) => group.keys));
  if (detailSettingKeys.has(key) && !groupedFeatureKeys.has(key)) return false;
  const looksLikeFeatureToggle = key.startsWith("enable_") || key === "creative_hidden_mode" || key === "group_repeat_count_distinct_users_only";
  return visibleConfigKey(key) && !embeddedFeatureKeys.has(key) && (looksLikeFeatureToggle || !detailSettingKeys.has(key));
}

function topLevelFeatureKey(key) {
  let current = key;
  const seen = new Set();
  while (embeddedFeatureParentByKey[current] && !seen.has(current)) {
    seen.add(current);
    current = embeddedFeatureParentByKey[current];
  }
  return current;
}

function visibleTopLevelFeatureKeys(source = state.featureDraft || {}) {
  return Object.keys(source || {}).filter(visibleFeatureSwitchKey);
}

function featureSearchText(key) {
  const childText = Object.entries(embeddedFeatureParentByKey)
    .filter(([childKey]) => topLevelFeatureKey(childKey) === key)
    .map(([childKey]) => `${childKey} ${featureLabel(childKey)} ${featureDescription(childKey)}`)
    .join(" ");
  const settingText = (featureSettingGroups[key] || [])
    .map((settingKey) => `${settingKey} ${configLabel(settingKey)} ${configDescriptions[settingKey] || ""}`)
    .join(" ");
  return `${key} ${featureLabel(key)} ${featureDescription(key)} ${childText} ${settingText}`.toLowerCase();
}

const safeFeatureKeys = [
  "enable_mai_style_integration",
  "enable_companion_memory",
  "enable_expression_learning",
  "enable_response_self_review",
  "enable_group_privacy_guard",
  "enable_relationship_state_machine",
  "enable_dialogue_episode_memory",
  "enable_open_loop_tracking",
  "enable_user_habit_learning",
  "enable_food_menu_recommendation",
  "enable_private_image_self_recognition",
  "enable_environment_perception",
  "enable_worldbook_member_recognition",
  "enable_cross_user_memory_bridge",
  "enable_atrelay_tools",
];

const configLabels = {
  enabled_user_count: "启用私聊对象",
  user_count: "私聊对象总数",
  require_opt_in: "是否需要私聊确认",
  default_style: "默认语气",
  reply_style_prompt: "回复风格提示词",
  plugin_specific_persona_id: "插件指定人格 ID",
  private_user_aliases: "私聊身份别名归并",
  private_user_delivery_aliases: "私聊主动发送目标映射",
  proactive_intensity_preset: "主动强度预设",
  schedule_persona_prompt: "角色资料补充",
  schedule_worldview_prompt: "世界观/生活背景",
  roleplay_user_profile_prompt: "用户与关系补充",
  max_daily_messages: "每日主动上限",
  enable_llm_proactive_message: "主动文本使用 LLM 生成",
  proactive_prompt_template: "主动生成提示词模板",
  enable_llm_proactive_persona_judge: "主动人格/世界观判定",
  PROACTIVE_PERSONA_JUDGE_PROVIDER_ID: "主动人格判定模型",
  proactive_persona_judge_send_threshold: "人格判定放行阈值",
  proactive_persona_judge_cache_minutes: "人格判定缓存分钟",
  timer_pre_silence_minutes: "预约前静默窗口",
  enable_tts_enhancement: "TTS强化",
  tts_generation_mode: "TTS生成路径",
  tts_voice_language: "TTS语音语种",
  tts_conversion_provider_id: "TTS文本转换模型",
  tts_extra_prompt: "TTS补充规则",
  EMOTION_JUDGEMENT_PROVIDER_ID: "情绪变化判断模型",
  enable_llm_emotion_judgement: "模型复核情绪变化",
  emotion_judgement_mode: "情绪复核范围",
  emotional_gate_hurt_threshold: "伤心触发阈值",
  emotional_gate_refuse_threshold: "生气触发阈值",
  emotional_gate_recovery_per_hour: "每小时缓和量",
  emotional_gate_max_hurt_minutes: "最长收敛分钟",
  enable_qzone_emotional_vent_publish: "公开心情动态",
  qzone_emotional_vent_threshold: "心情动态触发阈值",
  qzone_emotional_vent_cooldown_hours: "心情动态冷却小时",
  qzone_emotional_vent_probability: "心情动态触发概率",
  enable_food_menu_recommendation: "吃什么候选",
  response_review_mode: "回复/主动复核模式",
  SMART_SILENCE_PROVIDER_ID: "智能沉默小模型",
  smart_silence_min_confidence: "智能沉默置信度",
  smart_silence_model_timeout_seconds: "智能沉默超时秒数",
  proactive_review_strength: "主动复核强度",
  proactive_review_hard_risk_threshold: "硬拦截风险阈值",
  proactive_review_low_score_threshold: "低价值分数阈值",
  proactive_review_pressure_threshold: "打扰压力阈值",
  response_review_max_chars: "被动回复长度阈值",
  tts_frequency_control_mode: "TTS频率控制模式",
  tts_constraint_mode: "TTS约束强度",
  tts_session_min_interval_seconds: "TTS会话最小间隔秒数",
  tts_private_min_interval_seconds: "私聊TTS最小间隔秒数",
  tts_group_min_interval_seconds: "群聊TTS最小间隔秒数",
  tts_trigger_probability: "TTS全局触发概率(%)",
  tts_private_trigger_probability: "私聊TTS触发概率(%)",
  tts_group_trigger_probability: "群聊TTS触发概率(%)",
  enable_tts_local_playback: "TTS生成后本机播放",
  enable_tts_local_playback_live_only: "直播时仅播放直播回应消息",
  tts_local_playback_volume: "本机播放音量",
  enable_tts_live_subtitle_sync: "同步到直播打字机字幕",
  tts_live_subtitle_url: "直播字幕推送地址",
  tts_local_playback_min_interval_seconds: "本机播放最小间隔秒数",
  auto_voice_enabled: "自动语音转换",
  auto_voice_full_conversion_enabled: "自动语音完整转换",
  auto_voice_probability: "自动语音触发概率(旧)",
  auto_voice_max_chars: "自动语音最大字数",
  auto_voice_cooldown_seconds: "自动语音冷却秒数",
  main_user_voice_probability: "主用户触发概率(%)",
  main_user_mention_voice_keywords: "@主用户语音关键词",
  main_user_mention_voice_probability: "@主用户关键词触发概率(%)",
  main_user_mention_voice_prompt: "@主用户关键词提示词",
  inbound_message_debounce_seconds: "重复上报去重秒数",
  enable_message_debounce: "消息收口防抖",
  enable_smart_message_debounce: "智能文本收口",
  SMART_MESSAGE_DEBOUNCE_PROVIDER_ID: "智能收口小模型",
  smart_message_debounce_model_timeout_seconds: "模型超时秒数",
  smart_message_debounce_wait_seconds: "智能等待秒数",
  smart_message_debounce_learning_window_seconds: "误判学习窗口秒数",
  smart_message_debounce_examples_limit: "学习样本提示数量",
  enable_recall_enhancement: "撤回增强",
  enable_recall_cancel_reply: "撤回取消回复",
  enable_recall_message_cache: "撤回消息缓存",
  enable_recall_transcribe_command: "撤回转述命令",
  recall_message_cache_ttl_seconds: "撤回缓存秒数",
  recall_message_cache_max_items: "撤回缓存上限",
  recall_message_image_cache_max_mb: "撤回图片磁盘上限",
  enable_forbidden_word_recall: "违禁词自动撤回",
  recall_forbidden_words: "撤回违禁词表",
  recall_forbidden_scope: "违禁词撤回范围",
  recall_forbidden_word_case_sensitive: "违禁词大小写敏感",
  enable_proactive_only_mode: "仅保留主动能力",
  text_message_debounce_seconds: "文本补话等待秒数",
  image_message_debounce_seconds: "图片补话等待秒数",
  forward_message_debounce_seconds: "转发补话等待秒数",
  text_message_debounce_max_wait_seconds: "文本最长等待秒数",
  message_debounce_max_merge_messages: "最大合并消息数",
  enable_semantic_message_debounce: "旧版收口兼容开关",
  semantic_message_debounce_seconds: "旧版文本等待秒数",
  enable_proactive_quote_trigger_message: "引用触发消息",
  enable_quote_group_reply: "群回复引用",
  enable_quote_group_interjection: "群主动插话引用",
  enable_quote_private_proactive: "私聊主动引用",
  quote_skip_short_reply_chars: "短回复不引用阈值",
  quote_target_strategy: "引用目标策略",
  private_image_vision_wait_seconds: "单图等待识图秒数",
  private_image_provider_timeout_seconds: "单个识图模型超时秒数",
  enable_private_image_gif_enhancement: "GIF 动图强化",
  private_image_gif_max_frames: "GIF 抽帧数",
  enable_private_image_self_recognition: "图片转述增强",
  private_image_self_recognition_hint: "角色自我识别线索",
  enable_private_image_vision_cache: "重复图片转述缓存",
  private_image_vision_cache_max_items: "图片转述缓存上限",
  enable_segmented_proactive_reply: "分段发送",
  segmented_proactive_scope: "分段作用范围",
  segmented_proactive_chat_scope: "分段会话范围",
  segmented_proactive_threshold: "不分段字数阈值",
  segmented_proactive_min_segment_chars: "短片段合并阈值",
  segmented_proactive_max_segments: "文本最多分段数",
  segmented_proactive_send_as_forward: "分段后合并发送",
  segmented_proactive_split_mode: "分段模式",
  segmented_proactive_regex: "分段正则",
  segmented_proactive_split_words: "分段词列表",
  enable_segmented_proactive_content_cleanup: "分段内容清理",
  segmented_proactive_content_cleanup_scope: "清理范围",
  segmented_proactive_content_cleanup_rule: "清理正则",
  segmented_proactive_content_cleanup_words: "清理词列表",
  segmented_proactive_interval_method: "分段间隔方式",
  segmented_proactive_interval_min: "最小间隔秒数",
  segmented_proactive_interval_max: "最大间隔秒数",
  segmented_proactive_log_base: "对数间隔底数",
  group_conversation_followup_seconds: "群聊续接判断秒数",
  group_conversation_followup_max_turns: "群聊连续续接上限",
  enable_group_conversation_followup: "启用连续对话保持",
  enable_group_context_injection: "群上下文注入",
  enable_group_injection_guard: "群聊防注入",
  enable_group_persona_denoise: "群聊人格降噪",
  enable_group_scene_awareness: "群聊场景感知",
  enable_group_privacy_guard: "群隐私保护",
  forward_message_mode: "合并消息适配方式",
  forward_message_max_messages: "合并消息最多读取条数",
  forward_message_max_chars: "合并消息注入字数上限",
  forward_message_parse_nested: "展开嵌套合并消息",
  forward_message_image_vision: "合并消息图片视觉",
  forward_message_image_limit: "合并消息视觉图片上限",
  max_group_recent_messages: "群聊最近消息上限",
  max_group_slang_terms: "群黑话上限",
  group_slang_web_search_terms: "黑话联网候选扫描数",
  group_slang_web_search_results: "每词搜索摘要条数",
  daily_token_limit: "每日 Token 限额",
  enable_daily_token_soft_limit: "启用每日 Token 软限额",
  daily_token_soft_limit: "每日 Token 软限额",
  humanized_state_intensity: "拟人状态强度",
  enable_humanized_states: "拟人身体状态",
  enable_health_state: "健康/不适状态",
  enable_hunger_state: "饥饿/胃口状态",
  enable_qq_presence_sync: "同步 QQ 在线状态",
  enable_qq_custom_presence_sync: "同步 QQ 自定义短状态",
  inject_passive_states: "被动状态注入",
  enable_passive_state_delta_injection: "被动状态增量注入",
  passive_injection_position: "动态提示词注入位置",
  framework_session_lock_mode: "主链会话锁兼容模式",
  enable_rest_reply_simulation: "休息回复闸门",
  rest_reply_mode: "休息回复判定模式",
  rest_reply_probability: "休息中概率回复(%)",
  rest_reply_llm_threshold: "模型醒来阈值",
  rest_reply_active_windows: "休息闸门生效时段",
  rest_reply_awake_grace_minutes: "醒后免重判分钟数",
  enable_rest_backlog_reply: "醒后补看私聊",
  rest_backlog_max_messages: "醒后最多补看条数",
  REST_WAKEUP_PROVIDER_ID: "休息醒来判断模型",
  enable_cycle_state: "生理期模拟",
  worldview_adaptation_mode: "世界观适配模式",
  worldview_adaptation_prompt: "自定义世界观适配",
  enable_worldview_perception: "世界观适配感知",
  environment_perception_timezone: "环境感知时区",
  holiday_country: "节假日地区",
  enable_holiday_perception: "节假日/工作日",
  enable_platform_perception: "平台与消息类型",
  enable_model_perception: "当前模型配置",
  enable_lunar_perception: "农历",
  enable_solar_term_perception: "节气",
  enable_almanac_perception: "轻量黄历",
  enable_yesterday_screen_diary_context: "昨日屏幕日记",
  screen_diary_context_max_chars: "昨日屏幕日记上下文字数",
  passive_topic_memory_hours: "话题抑制记忆小时",
  proactive_intensity_preset: "主动强度预设",
  idle_minutes: "空闲门槛分钟",
  min_interval_minutes: "最小主动间隔分钟",
  proactive_unanswered_slowdown_start: "未回应降频起点",
  proactive_unanswered_max_interval_multiplier: "未回应最大间隔倍率",
  friend_unanswered_max_cooldown_hours: "朋友未回应最长冷却",
  timer_pre_silence_minutes: "预约前静默窗口",
  check_interval_seconds: "后台检查间隔秒",
  enabled: "群聊总开关",
  group_count: "群记录总数",
  enabled_group_count: "启用群数量",
  access_mode: "名单模式",
  whitelist: "白名单",
  blacklist: "黑名单",
  interjection_enabled: "群主动插话",
  repeat_follow_enabled: "复读跟读",
  group_interject_min_interval_minutes: "群插话最小间隔",
  group_interject_max_daily: "每群每日插话上限",
  group_repeat_trigger_threshold: "复读触发阈值",
  group_repeat_count_distinct_users_only: "复读只计不同用户",
  group_repeat_follow_probability: "跟读初始概率",
  group_repeat_interrupt_probability: "打断初始概率",
  group_repeat_interrupt_probability_step: "概率共同递增",
  group_repeat_interrupt_text: "打断文本",
  group_repeat_interrupt_image_path: "打断表情包路径",
  group_scene_recent_limit: "场景感知消息数",
  group_wakeup_direct_words: "强唤醒词",
  group_wakeup_context_words: "弱相关唤醒词",
  group_wakeup_interest_keywords: "手动兴趣关键词",
  group_wakeup_interest_probability: "兴趣唤醒概率",
  enable_group_wakeup_question: "解惑唤醒",
  group_wakeup_question_threshold: "解惑强度阈值",
  enable_group_wakeup_cold_group: "冷群唤醒",
  group_wakeup_cold_group_threshold: "冷群强度阈值",
  group_wakeup_cold_group_idle_minutes: "冷群判定分钟",
  group_wakeup_cooldown_seconds: "唤醒冷却秒数",
  group_wakeup_generated_keyword_limit: "自动兴趣词上限",
  group_wakeup_topic_interest_max_boost: "话题兴趣权重上限",
  group_wakeup_debounce_pending_penalty: "收口等待兴趣降权",
  group_wakeup_fatigue_limit: "唤醒疲劳阈值",
  group_wakeup_fatigue_decay_minutes: "疲劳恢复分钟",
  group_wakeup_log_limit: "唤醒记录上限",
  group_wakeup_short_text_wait_seconds: "短唤醒补话等待",
  enable_group_high_intensity_mode: "群聊高强度收口",
  group_high_intensity_wakeup_window_seconds: "高强度窗口秒数",
  group_high_intensity_wakeup_threshold: "高强度唤醒阈值",
  group_high_intensity_cooldown_seconds: "收口持续秒数",
  group_high_intensity_merge_seconds: "合并等待秒数",
  group_high_intensity_max_merge_messages: "高强度最大合并数",
  group_high_intensity_merge_scope: "高强度合并范围",
  worldbook_auto_import: "启动时刷新关系网",
  worldbook_member_match_aliases: "允许别名辅助匹配",
  worldbook_self_registration: "允许群聊自登记",
  worldbook_self_registration_block_words: "自登记拒绝词",
  worldbook_self_registration_block_reply: "自登记拒绝回复",
  worldbook_auto_pending_observations: "低频待确认观察",
  worldbook_member_inject_limit: "单次注入节点数",
  worldbook_config_paths: "关系网配置路径",
  cross_user_memory_owner_only: "仅主人可用",
  atrelay_require_worldbook_first: "优先按关系网解析",
  atrelay_member_cache_minutes: "群成员缓存分钟",
  atrelay_sensitive_confirm: "敏感转述确认",
  enable_atrelay_llm_rewrite: "使用模型转述正文",
  atrelay_default_relay_style: "默认转述方式",
  atrelay_multi_target_limit: "多目标单次上限",
  memory_refresh_interval_minutes: "长期画像整理间隔",
  max_companion_memory_items: "长期画像条目上限",
  expression_learning_mode: "表达学习模式",
  max_learned_expression_items: "表达节奏样本上限",
  episode_memory_refresh_messages: "片段整理消息阈值",
  episode_memory_refresh_minutes: "片段整理时间阈值",
  max_dialogue_episodes: "私聊片段上限",
  user_habit_min_count: "习惯成型次数",
  enable_food_menu_recommendation: "吃什么候选",
  user_habit_max_items: "习惯条目上限",
  skill_growth_rate: "技能成长倍率",
  skill_growth_custom_skills: "自定义技能",
  enable_skill_growth_passive_injection: "被动回复技能认知",
  enable_skill_growth_schedule_influence: "能力状态影响日程",
  skill_growth_schedule_influence_strength: "日程影响强度",
  bilibili_boredom_min_interval_hours: "B 站触发间隔",
  bilibili_share_probability: "视频分享概率",
  bilibili_share_min_score: "视频分享最低评分",
  news_min_interval_hours: "新闻读取间隔",
  news_share_probability: "新闻分享概率",
  enable_external_event_self_link: "外界信息自我关联",
  external_event_self_link_probability: "自我关联分享欲倍率",
  external_event_self_link_cooldown_hours: "自我关联冷却",
  news_max_items_per_source: "单源读取条数",
  news_sources: "新闻源",
  enable_ai_daily_watch: "AI 日报/早报追踪",
  ai_daily_sources: "AI 日报/早报来源",
  ai_daily_source_uid: "兼容旧版 UP 主 UID",
  ai_daily_check_window: "旧版检查窗口",
  ai_daily_check_interval_minutes: "旧版检查间隔",
  ai_daily_prefer_text_version: "优先文字版",
  enable_news_daily_hot_read: "每日获取热点",
  enable_news_boredom_read: "无聊看新闻",
  news_hot_sources: "热点来源",
  news_hot_max_items: "热点候选数量",
  web_exploration_min_interval_hours: "网页探索间隔",
  web_exploration_share_probability: "探索分享概率",
  web_exploration_max_results: "搜索结果数",
  web_exploration_interests: "探索兴趣倾向",
  WEB_EXPLORATION_API_BASE_URL: "主动搜索接口地址",
  WEB_EXPLORATION_API_KEY: "主动搜索接口 API Key",
  WEB_EXPLORATION_API_MODEL: "主动搜索接口模型",
  enable_web_exploration_boredom_search: "空档自主搜索",
  QZONE_COOKIE: "QQ 空间手动 Cookie",
  qzone_life_publish_min_interval_hours: "说说最小间隔",
  qzone_life_publish_probability: "说说触发概率",
  enable_qzone_generated_image_publish: "说说配图",
  qzone_generated_image_probability: "说说配图概率",
  enable_qzone_comment_inbox: "空间评论收件箱",
  qzone_comment_inbox_interval_minutes: "评论检查间隔",
  qzone_comment_inbox_recent_posts: "扫描最近说说数",
  qzone_comment_inbox_max_replies_per_tick: "每轮最多回复",
  enable_photo_text_action: "主动拍照/生图",
  photo_action_max_daily: "每日主动生图上限",
  proactive_photo_text_probability: "主动带图触发概率",
  photo_generation_backend: "主动生图后端",
  COMFYUI_TEXT2IMG_WORKFLOW_NAME: "文生图工作流",
  COMFYUI_SELFIE_WORKFLOW_NAME: "自拍工作流",
  photo_persona_reference_image_path: "人设参考图路径",
  enable_daily_outfit_photo: "每日穿搭照片",
  daily_outfit_photo_prompt: "每日穿搭提示词",
  enable_natural_language_photo_generation: "自然语言生图/改图",
  natural_language_photo_generation_max_daily: "自然语言生图上限",
  natural_language_photo_extra_prompt: "自然语言生图附加提示词",
  comfyui_photo_wait_seconds: "本地生图等待秒数",
  enable_local_photo_load_guard: "电脑高负荷保护",
  local_photo_cpu_busy_percent: "CPU 忙碌阈值",
  local_photo_memory_busy_percent: "内存忙碌阈值",
  local_photo_defer_minutes: "忙时延后分钟数",
  external_image_api_platform: "在线生图平台",
  EXTERNAL_IMAGE_API_BASE_URL: "在线图片 API 地址",
  EXTERNAL_IMAGE_API_KEY: "在线图片 API Key",
  EXTERNAL_IMAGE_API_MODEL: "在线图片模型",
  external_image_api_size: "在线生图尺寸",
  external_image_api_timeout_seconds: "在线生图超时秒数",
  external_image_api_custom_headers: "在线生图自定义请求头",
  enable_backup_external_image_api: "启用备选在线 API",
  backup_external_image_api_platform: "备选在线生图平台",
  BACKUP_EXTERNAL_IMAGE_API_BASE_URL: "备选在线 API 地址",
  BACKUP_EXTERNAL_IMAGE_API_KEY: "备选在线 API Key",
  BACKUP_EXTERNAL_IMAGE_API_MODEL: "备选在线图片模型",
  backup_external_image_api_size: "备选在线生图尺寸",
  backup_external_image_api_timeout_seconds: "备选在线超时秒数",
  backup_external_image_api_custom_headers: "备选在线生图自定义请求头",
  photo_generation_style: "主动生图风格",
  photo_generation_style_custom_prompt: "自定义风格说明",
  photo_generation_fixed_prompt: "固定附加提示词",
  photo_generation_scene_presets: "生图场景预设",
  private_reading_min_interval_hours: "阅读最小间隔",
  private_reading_max_photo_count: "页数上限",
  private_reading_share_probability: "主动提起概率",
  private_reading_default_keywords: "默认搜索关键词",
  private_reading_blocked_tags: "过滤标签",
  private_reading_ask_probability: "征求推荐概率",
  enable_private_reading_preference_influence: "私密偏好影响",
  private_reading_preference_min_ratings: "偏好生效最少评分数",
  private_reading_preference_max_terms: "偏好注入最多词条",
  unanswered_screen_peek_after_minutes: "沉默多久后窥屏",
  unanswered_screen_peek_cooldown_minutes: "沉默窥屏冷却",
  creative_inspiration_probability: "创作灵感概率",
  creative_share_probability: "创作透露概率",
  creative_chars_per_session: "每次创作字数",
  creative_max_active_projects: "同时创作项目上限",
  active_projects: "进行中创作",
  project_count: "创作项目",
  boredom_watch_enabled: "无聊刷视频",
  hidden_mode: "低调模式",
};

const configDescriptions = {
  enable_proactive_only_mode: "开启后，本插件只保留主动私聊的日程、主动生成和发送链路；普通私聊、群聊消息不会再被本插件做状态/TTS/图片/转发/群聊上下文注入，也不会触发本插件的被动回复增强，但不会阻止 AstrBot 默认回复或其他插件处理。用户回复主动消息时仍会被轻量记为已回应。适合只想使用主动陪伴、或担心本插件被动链路误接管/误识别的场景。",
  enable_llm_proactive_message: "开启后，主动调度只负责挑选动机和时机，真正文本会调用 AstrBot 人格生成；关闭时回退为本地模板，更省但更机械。",
  proactive_prompt_template: "自定义主动消息生成提示词。留空使用内置模板；适合把角色口吻、世界观约束和“不要像回复空气”这类要求固定下来。",
  enable_llm_proactive_persona_judge: "主动计划到点后，先让模型判断这个念头是否符合角色、世界观、关系温度和当下打扰边界；可放行、改写、延后或丢弃。",
  PROACTIVE_PERSONA_JUDGE_PROVIDER_ID: "用于主动人格/世界观判定的轻量模型。建议选择 JSON 稳定、判断保守、理解角色边界的小到中型模型。",
  proactive_persona_judge_send_threshold: "模型判定为 send 但分数低于该阈值时，会自动转为延后。越高越克制，越低越容易放行。",
  proactive_persona_judge_cache_minutes: "同一主动计划在该时间内复用模型判定，减少重复调用；计划内容、语义或触发来源变化后会自动失效。",
  default_style: "没有单独学习到用户偏好时，插件用于生成日程、状态和主动行为的基础语气参考。",
  reply_style_prompt: "注入到普通被动回复和主动消息生成中的表达约束，适合写句数、简洁度、语言和社交媒体口语习惯；复杂问题或用户要求详细说明时，可在这里允许模型放宽。",
  plugin_specific_persona_id: "填写 AstrBot 人格 ID 后，插件会优先使用该人格作为主回复人格；留空则继承 AstrBot 当前默认人格。不同于世界知识，它会影响私聊被动回复、群聊人格和关系判断。",
  private_user_aliases: "把临时会话 ID、异常 sender_id 或机器人侧误报 ID 归并到主 QQ。每行一个映射，例如：688C2CE7...=100012345。",
  private_user_delivery_aliases: "只改变主动消息/主动测试的发送出口，不改变记忆归属。每行一个映射，例如：大号QQ=小号QQ。",
  schedule_persona_prompt: "给陪伴插件的日程、状态、主动行为、识图、生图和创作提供角色资料补充；不会覆盖 AstrBot 主人格或群聊人格。",
  schedule_worldview_prompt: "给陪伴插件判断生活背景和世界规则，适合写所在世界、日常规则、居住/学校/城市环境和与用户的生活关系。",
  roleplay_user_profile_prompt: "描述角色如何称呼用户、用户身份、彼此关系和相处方式；不会作为图片自我识别的外观线索。",
  humanized_state_intensity: "控制睡眠不佳、健康、饥饿、周期等状态出现概率和能量影响强度，范围 0-100。",
  enable_humanized_states: "总开关。关闭后不再生成拟人身体/梦境状态，只保留基础平稳状态。",
  enable_health_state: "开启后健康/不适状态视为可用，拟人状态可能出现生病、不舒服、头疼或恢复尾声；关闭后自动生成和手动增添都会跳过这类状态。",
  enable_hunger_state: "开启后饥饿/胃口状态视为可用，拟人状态可能出现饿、胃口不好、想吃东西或想吃甜的；关闭后不会生成吃什么类身体小需求，手动增添也会拦截饥饿状态。",
  enable_qq_presence_sync: "开启后，日程细化会通过 OneBot 的 set_online_status 尝试同步在线/忙碌等基础 QQ 状态；不包含自定义短状态。离开、隐身、请勿打扰会自动降级为在线。",
  enable_qq_custom_presence_sync: "默认关闭。开启后才会尝试同步“专注中/休息中”等 QQ 自定义短状态；这依赖协议端支持非标准 OneBot 扩展。为避免 NapCat 兼容问题，插件不会调用 set_custom_online_status。",
  inject_passive_states: "开启后普通聊天会参考“当前扮演状态”；关闭后状态主要影响日程和主动行为。",
  enable_passive_state_delta_injection: "开启后，同一会话只在状态首次出现、明显变化或用户问近况时注入短状态摘要；状态未变时不重复塞完整日程和生活背景。关闭后恢复每轮完整状态注入。",
  passive_injection_position: "选择被动状态、环境感知、TTS 本轮频控、转发/引用上下文等动态片段的注入位置。当前请求末尾会进入统一动态块并按稳定顺序排列，更利于缓存；系统提示词约束更强但更容易降低缓存命中。若同时启用长期记忆/记忆召回，推荐使用当前请求末尾，让召回内容与动态状态在尾部自然结合。",
  framework_session_lock_mode: "旧版 AstrBot 会话库并发锁兼容项。新版本通常不需要，开启会让同一会话主链请求排队并增加回复延迟。auto 只在识别到受影响旧版本时启用；always 仅建议仍遇到 database is locked 的旧版用户使用。",
  enable_rest_reply_simulation: "开启后，日程处于睡眠、午休或休息段时，普通被动回复会先经过休息闸门；未放行时静默不回复。",
  rest_reply_mode: "仅概率醒来只按概率放行；模型判断会让模型按消息重要性、是否明确叫醒、情绪/安全需要等打分。",
  rest_reply_probability: "仅概率醒来模式使用。越低越不容易在睡眠/休息中被普通消息叫醒。",
  rest_reply_llm_threshold: "模型判断模式使用。模型输出 0-100 分，达到该阈值才醒来回复；建议 60-75。",
  rest_reply_active_windows: "只有当前时间落入这些时段时，日程里的睡眠/午休/休息才触发休息闸门。多个时段用逗号分隔，如 23:00-08:30,12:20-13:40；留空则按旧行为全天跟随日程休息词。",
  rest_reply_awake_grace_minutes: "休息中被明确叫醒、模型放行或概率命中并回复后，这段时间内不再对紧接着的消息重复判定是否被吵醒。",
  enable_rest_backlog_reply: "休息闸门静默拦截的目标私聊会暂存成简短摘要；下一次醒来或被叫醒回复时，Bot 会像刚补看消息一样自然接上。只记录私聊，不记录群聊。",
  rest_backlog_max_messages: "休息期间最多保留多少条未回复私聊。超过后只留最近几条，避免醒来后被旧消息淹没。",
  REST_WAKEUP_PROVIDER_ID: "可选。用于休息醒来判断的轻量模型；留空时优先使用回复审校模型，再回退主模型。",
  enable_cycle_state: "开启后即视为适用，可能在“当前扮演状态”里出现生理期前、处于生理期或生理期后的相关状态；它只影响语气、精力和回复节奏，不是医学记录或真实日期追踪。",
  environment_perception_timezone: "用于判断当前时段、日期语境、节假日和日程跨日。默认 Asia/Shanghai。",
  holiday_country: "节假日识别地区。目前主要用于 CN，未安装依赖时会自动退化为周末/工作日。",
  enable_holiday_perception: "开启后会把节假日、调休和工作日判断注入环境感知。",
  enable_platform_perception: "开启后会识别平台、私聊/群聊和消息媒介类型。",
  enable_model_perception: "开启后会把当前会话 LLM、视觉转述模型，以及可用的生图后端/在线图片模型作为环境信息注入；只供 Bot 判断能力边界，不要求主动报告模型名。",
  enable_worldview_perception: "开启后才会把世界观适配片段注入被动回复。若 AstrBot 人设已经写了世界观，建议关闭以避免重复。",
  enable_lunar_perception: "开启后在依赖可用时注入农历日期。",
  enable_solar_term_perception: "开启后注入当天或近三天节气提示。",
  enable_almanac_perception: "开启后生成轻量宜忌氛围标签，只作表达参考。",
  enable_yesterday_screen_diary_context: "读取 screen_companion 的昨日屏幕观察日记脱敏摘要，作为今日状态、日程和生活节奏背景；不会读取今天实时屏幕。",
  screen_diary_context_max_chars: "注入给状态和日程模型的昨日屏幕观察摘要最大字符数。建议较短，只保留活动类型和节奏。",
  TROUBLESHOOTING_PROVIDER_ID: "用于排障中心的模型复核。留空时先跟随回复/主动复核模型，再回退到陪伴通用/主模型。",
  proactive_intensity_preset: "默认关闭，完全沿用手动参数。开启后只在运行态调整私聊主动、群聊唤醒和插话的有效频率，并会在排障页显示当前预设；不会绕过免打扰、休息、用户拒绝、隐私和成本闸门。",
  idle_minutes: "用户多久没有活跃后，才被视为适合主动触达或分享的空闲状态。",
  min_interval_minutes: "同一私聊对象两次主动消息之间的最小间隔，避免频繁打扰。",
  proactive_unanswered_slowdown_start: "用户连续几次不回应 Bot 主动消息后，开始自动降低主动频率。",
  proactive_unanswered_max_interval_multiplier: "连续未回应时，最小主动间隔最多放大到多少倍。",
  friend_unanswered_max_cooldown_hours: "朋友用户持续未回应时，主动消息最长可延后到多少小时内再尝试。",
  timer_pre_silence_minutes: "已有聊天临时预约时，距离预约时间不足该分钟数会暂停普通主动、链式追问和未回复补一句，避免抢在官方定时计划前打扰。若预约文本带有休息/睡觉/起床语义，会从预约创建起静默到到点。",
  max_daily_messages: "每个私聊对象每天最多收到多少条插件主动消息。",
  passive_topic_memory_hours: "记录最近被动回复主题的时间窗口，用来判断短时间内是否又在重复同类话题。",
  tts_generation_mode: "先决定语音从哪里来。快速标签模式追求低延迟：主模型可写 <pc_tts>，插件发送前轻处理。后处理模式追求稳定：主模型只写普通回复，发送前由 TTS 文本模型判断是否需要语音并完成翻译/改写。",
  tts_voice_language: "控制真正送入 TTS 的语音正文语种。可让聊天文本保留中文，<pc_tts> 内使用日语、中文或英语朗读；日语模式会尽量避免明显非日语文本直接进入 TTS，并会给缺少说明的外语语音块补中文释义。",
  tts_conversion_provider_id: "用于后处理判断+翻译、快速标签自动语音、语种修正和中文释义补全的文本模型，不是语音合成模型。转换时会参考当前 AstrBot 人格的语气、称呼和距离感；留空时后处理模式会保持纯文本，显式标签仍可由插件处理。",
  tts_extra_prompt: "只填写本人格或声线的额外要求。基础 <pc_tts> 格式、目标语种和 provider 情绪标签适配规则会自动生成，留空最稳。",
  tts_frequency_control_mode: "选择频率规则。全局频控：用概率影响快速标签模式下 LLM 是否倾向输出 TTS，并控制后处理模式是否进入判断+翻译；弱约束下显式 <pc_tts>/<tts> 不再被概率剥离，只受 provider 和会话间隔保护；强约束下按约束强度硬拦。",
  tts_constraint_mode: "仅快速标签模式 + 全局频控生效。弱约束只在概率命中时注入语音规则；强约束会在冷却内反向提示本轮禁止语音，并在发送前阻止语音生成。后处理模式不使用该项。",
  tts_session_min_interval_seconds: "仅全局频控生效。私聊/群聊未单独覆盖时使用的默认最小间隔；0 表示不限制。",
  tts_private_min_interval_seconds: "仅全局频控生效。私聊会话的最小间隔覆盖值；-1 表示继承默认间隔，0 表示私聊不限制。",
  tts_group_min_interval_seconds: "仅全局频控生效。群聊会话的最小间隔覆盖值；-1 表示继承默认间隔，0 表示群聊不限制。建议群聊比私聊更长。",
  tts_trigger_probability: "仅全局频控生效。私聊/群聊未单独覆盖时使用的默认触发概率。概率未命中时本轮不注入 TTS 提示词，也不进入后处理语音判断；弱约束不会剥离已输出的语音标签。",
  tts_private_trigger_probability: "仅全局频控生效。私聊触发概率覆盖值；-1 表示继承默认概率，0 表示私聊默认不主动使用 TTS。",
  tts_group_trigger_probability: "仅全局频控生效。群聊触发概率覆盖值；-1 表示继承默认概率。建议群聊低于私聊，避免打扰。",
  enable_tts_local_playback: "开启后，TTS 音频生成成功时会在运行 AstrBot 的电脑上直接播放。默认关闭，避免群聊自动语音频繁出声。",
  enable_tts_local_playback_live_only: "默认关闭：启用本机播放后，所有来源的 TTS 都会尝试在本机出声。开启后，只播放直播插件生成的直播回应语音，普通私聊、群聊或主动消息的 TTS 不会在本机播放。",
  tts_local_playback_volume: "TTS 生成后在本机播放时使用的音量百分比。默认 35，避免突然满音量播放；0 表示静音。",
  enable_tts_live_subtitle_sync: "开启后，TTS 生成音频时会把朗读文本同步推送到“我会直播圈米养你”的打字机字幕 overlay。",
  tts_live_subtitle_url: "直播插件字幕 overlay 的 /show 接口地址。默认对应 127.0.0.1:18081/show。",
  tts_local_playback_min_interval_seconds: "两次 TTS 本机播放之间的最小间隔。0 表示不限制。",
  auto_voice_enabled: "仅快速标签模式生效。开启后，当模型没有写 <pc_tts>/<tts> 时，普通纯文本回复可以进入自动语音转换；后处理模式不依赖这个开关。",
  auto_voice_full_conversion_enabled: "仅快速标签模式的自动语音生效。开启后，自动语音尽量把整条回复完整转换成一段语音；关闭时更偏向混合文本+语音。",
  auto_voice_probability: "仅旧版行为下生效。控制快速标签自动语音的旧触发概率；全局频控下请使用 TTS 全局触发概率。",
  auto_voice_max_chars: "仅快速标签模式的自动语音生效。普通回复不超过该字数才参与自动语音；填 0 表示不限制。旧版主用户单独概率命中时不受此限制。",
  auto_voice_cooldown_seconds: "仅旧版行为下生效。同一会话成功触发自动语音后，需要等待多少秒才能再次触发；全局频控下请使用 TTS 会话最小间隔秒数。",
  main_user_voice_probability: "仅旧版行为下作为强触发概率使用。群聊中主用户本人发言或被 @ 到时，提高快速标签自动语音倾向；填 -1 表示继承旧自动语音概率。",
  main_user_mention_voice_keywords: "仅旧版行为下作为强触发条件使用。群聊 @ 到主用户且命中这些关键词时，参与主用户关键词语音判定。多个关键词可用逗号、空格或换行分隔。",
  main_user_mention_voice_probability: "仅旧版行为下生效。命中 @主用户语音关键词后的触发概率，填写 0-100。",
  main_user_mention_voice_prompt: "主用户关键词规则命中后注入给模型的补充要求，例如更短、更贴近、使用某种声线。",
  daily_token_limit: "插件内部 LLM 任务的每日硬限额，达到后跳过非豁免后台调用。0 表示不限。",
  enable_daily_token_soft_limit: "作为达到限额就停止插件/停止后台链路的替代方案。开启后，达到软限额时暂缓新闻、网页探索、创作、群整理、自检和主动生图等低优先级后台任务，优先保留用户当下触发的回复。",
  daily_token_soft_limit: "今日插件内部 LLM 消耗达到该值后进入软降载。0 表示关闭软限额，只保留每日硬限额。",
  inbound_message_debounce_seconds: "只用于去掉平台或适配器短时间重复上报的同一条消息；不是等待用户补话的时间。",
  enable_message_debounce: "消息收口总开关。开启后，文本、图片、合并转发会按各自等待秒数给用户留补充说明的时间。",
  enable_smart_message_debounce: "开启后，普通文本先走本地快判：明确完整的问候、短互动和问题会直接放行；“知道吗/问你个事/你猜”等短引子会短等补话，其他疑似半句话才调用小模型。默认关闭。",
  SMART_MESSAGE_DEBOUNCE_PROVIDER_ID: "用于判断“疑似没说完”的轻量文本 Provider。明确完整文本不会调用模型，短引子会直接等待补话；留空时跟随插件主模型。",
  smart_message_debounce_model_timeout_seconds: "小模型判断的最长等待时间。超时后立刻使用本地启发式兜底，避免正常回复被拖慢。",
  smart_message_debounce_wait_seconds: "判断用户还没说完时的总等待预算；小模型判断耗时会计入这个时间，不会判断完再额外等满。",
  smart_message_debounce_learning_window_seconds: "如果模型刚判断已说完，但用户在该窗口内继续补话，就记录为误判样本。",
  smart_message_debounce_examples_limit: "每次判断时带给小模型的近期误判样本数量。0 表示不注入历史样本。",
  enable_recall_enhancement: "撤回相关能力总开关。包括撤回触发消息时取消回复、短期缓存撤回消息用于转述、违禁词自动撤回。",
  enable_recall_cancel_reply: "开启后，如果 QQ/OneBot 通知某条触发或唤醒消息已撤回，而 Bot 的回复还没真正发出，就静默取消这次回复和剩余分段。",
  enable_recall_message_cache: "开启后短期缓存普通消息的文本摘要；含图片消息会临时写入 recall_message_images，便于撤回后转述原图，并按缓存秒数和磁盘上限清理。",
  enable_recall_transcribe_command: "允许使用“陪伴 撤回消息”或“陪伴群 撤回消息”查看当前会话最近撤回消息。群聊需要管理权限。",
  recall_message_cache_ttl_seconds: "撤回消息摘要、撤回记录和临时撤回图片保留多久。过期后无法转述，也不再用于取消待发送回复。",
  recall_message_cache_max_items: "最多缓存多少条消息摘要。0 表示不按数量限制，但仍受缓存秒数限制。",
  recall_message_image_cache_max_mb: "撤回图片临时目录的最大体积。0 表示不按体积限制，但仍受缓存秒数限制。",
  enable_forbidden_word_recall: "开启且词表非空时，Bot 自己待发送消息会先被拦截；已进入事件流的群聊消息或 Bot 自己消息会尝试调用平台撤回。",
  recall_forbidden_words: "命中任一词就触发违禁词撤回。建议一行一个词；为空时不会执行自动撤回。",
  recall_forbidden_scope: "bot_only 只检查 Bot 自己消息；group_only 检查群聊消息；bot_and_group 同时检查 Bot 自己消息和群聊消息。",
  recall_forbidden_word_case_sensitive: "关闭时英文大小写不区分；开启时按原样匹配。",
  text_message_debounce_seconds: "普通文本后的补话等待时间。旧版“语义收口等待秒数”会作为该项的兼容默认值；设为 0 时文本不固定等待。",
  image_message_debounce_seconds: "只发图片、截图或表情包后的补话等待时间，适合保留几秒给用户先图后文。",
  forward_message_debounce_seconds: "只发合并转发/聊天记录后的补话等待时间。设为 0 表示转发不额外等待。",
  text_message_debounce_max_wait_seconds: "普通文本和群聊文本滑动等待的总时长上限。用户持续补话也不会超过该时间；0 表示只使用内部安全上限。",
  message_debounce_max_merge_messages: "一次收口最多合并多少条补充消息。达到上限后会立刻结束等待进入回复链；0 表示不限制。",
  enable_semantic_message_debounce: "旧版兼容项，已并入消息收口防抖，不再单独配置。",
  semantic_message_debounce_seconds: "旧版兼容项，读取时会迁移为文本补话等待秒数。",
  enable_proactive_quote_trigger_message: "开启后，群聊被 @、引用、唤醒或连续对话保持时，Bot 的普通回复会引用当前触发消息；群聊主动插话会引用触发消息；模型预约的私聊主动若能追溯到同一私聊消息，也会引用。复读跟读/打断不会引用。",
  enable_quote_group_reply: "控制普通群聊回复是否自动带引用。只在总开关开启后生效。",
  enable_quote_group_interjection: "控制群主动插话是否引用触发它的群消息。复读跟读/打断仍不会引用。",
  enable_quote_private_proactive: "控制可追溯到某条私聊消息的主动发送是否带引用。",
  quote_skip_short_reply_chars: "回复正文不超过该字数时不附带引用。0 表示不按长度跳过。",
  quote_target_strategy: "current 引用用户当前这条触发消息；quoted/auto 在用户引用 Bot 旧消息追问时优先引用那条旧消息。",
  private_image_vision_wait_seconds: "私聊单图确认没有继续补充后，最多等待视觉转述多久。不是图片收口时间；视觉提前完成会立刻进入主链。",
  private_image_provider_timeout_seconds: "每个视觉 provider 单次最多等待多久；超时后会临时降权并切下一个视觉模型，避免某个上游 503 或重试过久拖慢整条单图回复。",
  enable_private_image_gif_enhancement: "图片转述增强的可选子功能。开启后动态 GIF 会抽取代表帧，让视觉模型理解动作、表情变化和文字变化；关闭后按普通 GIF/图片路径处理。",
  private_image_gif_max_frames: "动态 GIF 进入视觉转述时最多抽取多少个代表帧。帧数越多越能理解动作变化，但会增加识图耗时和视觉输入量。",
  private_image_self_recognition_hint: "只补充当前角色自己的外观、头像、名字、表情包特征或聊天截图昵称，让视觉转述更容易判断图里是不是当前角色。不要写用户资料。",
  enable_private_image_vision_cache: "开启后，同一张图片或表情包会按内容哈希复用上次视觉摘要，避免重复调用识图模型；会保留压缩预览图用于管理，不保留原始大图，也不会缓存最终聊天回复。",
  private_image_vision_cache_max_items: "最多保留多少条图片视觉摘要缓存。达到上限后会清理最久未命中的旧缓存，0 表示不限制。",
  segmented_proactive_threshold: "纯文本短于或等于该字数时才考虑分段；太长的内容保持一整条，避免读起来散。",
  segmented_proactive_scope: "插件主动只影响插件主动消息的文本部分；全部 LLM 回复会额外拆普通模型纯文本回复，首段随主链立即发送，剩余片段后台按间隔补发。图片、语音、AT 或工具转述等复杂消息本身不会被拆；插件主动媒体会在文本分段后继续单独发送，创作分享会自动保持整段。",
  segmented_proactive_chat_scope: "控制分段在哪类会话生效：全部、仅私聊或仅群聊。不匹配的会话会保持整条发送。",
  segmented_proactive_min_segment_chars: "分段后短于或等于该字数的片段会并入相邻消息，避免“哈哈”“我也觉得”这类附和语单独发出。",
  segmented_proactive_max_segments: "一次主动文本最多拆成几条。默认 3，过高会显得刷屏；图片、语音和附加组件不占用这个文本段数。",
  segmented_proactive_send_as_forward: "开启后，切出多段时优先打包成合并转发消息发送；平台不支持时自动回退为普通逐条分段。",
  segmented_proactive_split_mode: "regex 使用正则切句；words 使用分段词列表，更适合清理句号、空格等固定分隔符。网址会自动保护，不会被按点号或斜杠拆开。",
  segmented_proactive_regex: "分段模式为 regex 时使用的切分正则。",
  segmented_proactive_split_words: "分段模式为 words 时使用的分段词。推荐一行一个；中文逗号要单独写一行，或写“逗号”。英文点号会把连续 ... 当成一个省略号边界；配置了“……”时会自动兼容单个“…”。网址内部字符会自动保护，完整网址结束处可作为自然断点；括号、标题引号和 <image>/<video> 这类尖括号媒体块内部字符会跳过。",
  enable_segmented_proactive_content_cleanup: "开启后会在分段时清理分隔符或无意义字符。",
  segmented_proactive_content_cleanup_scope: "全段清理会移除片段内所有匹配内容；仅句尾清理只移除每段末尾连续出现的清理词/正则。",
  segmented_proactive_content_cleanup_rule: "regex 模式下的后清理正则。",
  segmented_proactive_content_cleanup_words: "words 模式下的后清理词。配合“仅句尾清理”时，适合只去掉句尾句号、省略号或换行。",
  segmented_proactive_interval_method: "log 会按分段长度计算自然间隔；random 会在最小/最大间隔之间随机。普通 LLM 回复的后续片段会在后台等待，不阻塞首段发送。",
  segmented_proactive_interval_min: "两段消息之间的最小等待秒数；普通 LLM 回复只影响后台补发片段。",
  segmented_proactive_interval_max: "两段消息之间的最大等待秒数；普通 LLM 回复只影响后台补发片段。",
  segmented_proactive_log_base: "对数间隔的底数。数值越小，长句间隔增长越明显。",
  group_conversation_followup_seconds: "群里用户叫过 Bot 后，后续未 @ 的消息在多久内可能被判断为仍在对 Bot 说。",
  group_conversation_followup_max_turns: "一次群聊连续对话最多自动续接几轮，防止 Bot 一直卷进对话。",
  enable_group_context_injection: "开启后，群聊回复会参考最近群消息、当前话题、活跃成员和群内氛围；关闭后只按当前单条消息理解。",
  enable_group_injection_guard: "开启后，会识别群里试图改称呼、改语气、改设定或改输出格式的注入话术；这些内容不会写进群观察、黑话、话题线或后续 prompt。",
  enable_group_persona_denoise: "开启后，会主动压低群聊里的私聊腔、状态汇报和过于贴身的关系投射，让群聊发言更像在公共场合说话。",
  enable_group_scene_awareness: "开启后，会判断当前这句话是在对 Bot、某个群友还是整个群说话，并结合上下文减少误接话。",
  enable_group_privacy_guard: "开启后，群聊回复会额外防止把私聊记忆、私下关系细节或只适合一对一场景的信息带进群里。",
  group_interject_min_interval_minutes: "同一群两次主动插话之间的最小间隔。",
  group_interject_max_daily: "每个群每天最多允许几次主动插话。",
  group_repeat_trigger_threshold: "同一句话连续出现达到多少次后，才开始按概率跟读或打断。必须大于 2，默认 4；开启“复读只计不同用户”后，这里表示不同参与者数量。",
  group_repeat_count_distinct_users_only: "开启后，同一个人连续重复同一句话只算 1 个参与者；需要不同群友一起复读且达到阈值才会触发 Bot 跟读或打断，避免一个人刷屏把 Bot 带进复读。关闭后按复读消息次数累计。",
  group_repeat_follow_probability: "群里同一句话达到复读触发阈值后，Bot 跟读一次的基础概率。这里以百分比填写。",
  group_repeat_interrupt_probability: "同一句话达到复读触发阈值后，Bot 打断复读的基础概率。这里以百分比填写。",
  group_repeat_interrupt_probability_step: "复读越久，跟读/打断概率共同增加的步进。这里以百分比填写。",
  group_repeat_interrupt_text: "选择文本打断时发送的句子，例如“禁止复读”。",
  group_repeat_interrupt_image_path: "表情包路径。填写后可用图片代替打断文本。",
  group_scene_recent_limit: "判断群聊场景时参考最近多少条群消息。",
  enable_group_reality_promise_guard: "仅群聊生效。开启后 Bot 不会承诺自己能拉人、修网、开房间、登录或操作现实设备；私聊扮演不受影响。",
  group_wakeup_direct_words: "消息中出现即唤醒 Bot。适合填写 Bot 名字、昵称、固定称呼。多个词可用换行、逗号或顿号分隔。",
  group_wakeup_context_words: "与 Bot 身份、称呼或设定弱相关的关键词。命中后不会直接回复，而是先结合群聊上下文、关系网和句式判断是否适合自然接话。适合填写“机器人”“bot”、外号、作品名、设定称呼或常被拿来指代 Bot 的梗；不适合填“你怎么看”“问问你”这类泛请求句。",
  group_wakeup_interest_keywords: "手动补充 Bot 感兴趣的话题关键词。命中后按概率唤醒，不会每次都抢话。",
  group_wakeup_interest_probability: "群聊出现兴趣关键词时进入回复链的基础概率，填写 0-100。",
  enable_group_wakeup_question: "群里有人抛出开放疑问、求助或“有没有人懂”这类问题时，按强度阈值决定是否进入回复链。",
  group_wakeup_question_threshold: "开放疑问或求助会先计算 0-100 的强度分，达到该阈值才进入回复链。越低越容易被求助问题叫到。",
  enable_group_wakeup_cold_group: "群聊安静一段时间后有人重新开口时，按开场/问候/求助强度决定是否进入回复链。默认关闭，避免冷群突然冒泡。",
  group_wakeup_cold_group_threshold: "冷群重新开口会先计算 0-100 的强度分，达到该阈值才进入回复链。越高越保守。",
  group_wakeup_cold_group_idle_minutes: "距离上一条群消息超过多少分钟，才认为当前消息处于冷群重新开口场景。",
  group_wakeup_cooldown_seconds: "判断唤醒和兴趣唤醒的冷却时间，防止群聊里连续关键词刷屏。",
  group_wakeup_generated_keyword_limit: "自动从人格兴趣、技能、群话题和黑话中抽取多少个兴趣关键词参与判断。",
  group_wakeup_topic_interest_max_boost: "兴趣词如果同时出现在当前句、近几句或活跃话题线里，最多额外提高多少百分比的兴趣唤醒概率。",
  group_wakeup_debounce_pending_penalty: "同一群友正在补话等待时，兴趣唤醒概率降低多少百分比，避免等补充时又抢话。",
  group_wakeup_fatigue_limit: "短时间多次唤醒累计到多少点后，Bot 会更保守、更省力。强唤醒词仍然能叫到它。",
  group_wakeup_fatigue_decay_minutes: "每隔多少分钟自然恢复 1 点唤醒疲劳。数值越大，越会保留“刚被频繁叫到”的感觉。",
  group_wakeup_log_limit: "每个群最多保留多少条唤醒命中、冷却拦截和兴趣未触发记录。",
  group_wakeup_short_text_wait_seconds: "群聊里已经判定在叫 Bot、但内容只有 1-2 个字且不像完整短互动时，复用消息收口缓冲等待同一群友补充。设为 0 可关闭。",
  enable_group_high_intensity_mode: "短时间连续被明确叫到后自动进入收口降载；只有近期仍在连续唤醒时才合并等待，冷却残留只暂停弱相关后台动作。",
  group_high_intensity_wakeup_window_seconds: "统计连续唤醒的时间窗口。默认 60 秒，即一分钟内连续被叫到才进入高强度收口。",
  group_high_intensity_wakeup_threshold: "窗口内达到多少次唤醒后进入收口。默认 3 次，用于减少连续 @、连续引用造成的多次 LLM 调用。",
  group_high_intensity_cooldown_seconds: "进入收口降载后维持多久。冷却期间会暂停弱相关后台动作；若近期唤醒已经断开，单条明确 @ 不再额外等待。",
  group_high_intensity_merge_seconds: "高强度连续唤醒期间第一条明确叫到 Bot 的消息最多等待多久。这是固定合并窗口，不会因持续补话无限延长。",
  group_high_intensity_max_merge_messages: "高强度期间同一轮最多合并多少条叫 Bot 的消息。达到上限会立刻结束等待进入回复链；0 表示不限制。",
  group_high_intensity_merge_scope: "高强度期间如何合并连续叫 Bot 的消息：按全群合并，或只合并同一发送者的补话。",
  forward_message_mode: "注入：把合并消息摘要塞进主模型上下文；转述：先用专门模型读一遍再交给主模型。",
  forward_message_max_messages: "合并消息最多读取多少条节点，过多会截断。",
  forward_message_max_chars: "注入模式下放进主模型上下文的最大字符数。",
  forward_message_parse_nested: "是否继续展开合并消息里的嵌套合并消息。",
  forward_message_image_vision: "合并消息里出现图片时，按出现顺序交给视觉模型生成简短说明，再作为消息集上下文交给 Bot。",
  forward_message_image_limit: "单次合并消息最多转述多少张图片，超过上限的图片仍会保留占位。",
  max_group_recent_messages: "每个群保存的最近消息数量，用于场景、话题和插话判断。",
  max_group_slang_terms: "每个群最多保留多少条黑话/简称候选。",
  group_slang_web_search_terms: "黑话释义联网参考开启时，每次最多从多少个候选词里挑选待查词；实际每轮只联网搜索 1 个词，并缓存结果/失败冷却，避免短时间连发搜索。",
  group_slang_web_search_results: "黑话释义联网参考开启时，每个候选词最多保留多少条网页摘要给模型判断匹配程度。",
  memory_refresh_interval_minutes: "长期画像整理的最小间隔，越短越容易产生模型调用。",
  max_companion_memory_items: "每个私聊对象最多保留多少条长期画像条目。",
  expression_learning_mode: "light 更克制；balanced 保持当前自然学习；aggressive 会参考更多通过审核的短句和句尾样本，建议搭配手动审核。",
  enable_expression_manual_review: "开启后，新表达样本先进入用户详情的待审核列表，通过后才会参与表达注入。",
  enable_expression_style_review: "开启后，回复复核会额外处理表达学习过头、异常逗号/断句、照抄样本等问题。",
  max_learned_expression_items: "每个私聊对象最多保留多少条短句、句尾和标点节奏样本；不作为长期记忆事实。",
  episode_memory_refresh_messages: "累计多少条私聊消息后尝试整理一次对话片段。",
  episode_memory_refresh_minutes: "距离上次整理多久后允许再次整理私聊片段。",
  max_dialogue_episodes: "每个私聊对象最多保留多少条对话片段；实际回复时只择要使用最近或相关片段。",
  user_habit_min_count: "同一时段同类行为至少出现多少次，才被视为用户习惯；旧习惯会衰减，被动回复还要求当前时段和话题相关。",
  enable_food_menu_recommendation: "用户明确纠结吃什么、点什么或夜宵时，才从候选菜单里取少量贴合项作回复参考。候选本身在这个功能详情页管理。",
  user_habit_max_items: "每个私聊对象最多保留多少条行为习惯模式。",
  skill_growth_rate: "技能经验增长倍率。1 为默认速度，越高升级越快。",
  skill_growth_custom_skills: "手动补充技能名，可用逗号、换行或 JSON 列表表达。",
  enable_skill_growth_passive_injection: "开启后普通聊天会注入 Bot 当前能力状态和自我认知。默认关闭；关闭时技能成长仍会结算，并可继续影响日程和能力边界。",
  enable_skill_growth_schedule_influence: "开启后能力状态会约束日程表现，例如基本熟练的物理不再被常规物理题难住。",
  skill_growth_schedule_influence_strength: "能力状态影响日程生成的强度，0 表示只记录不约束。",
  bilibili_boredom_min_interval_hours: "Bot 无聊刷 B 站的最小间隔。",
  bilibili_share_probability: "看完视频后主动分享给用户的概率，按百分比填写。",
  bilibili_share_min_score: "视频评分达到多少才考虑分享。",
  enable_news_daily_hot_read: "每日随日程生成或后台检查读取一次热点，形成新闻见闻。",
  enable_news_boredom_read: "开启后 Bot 空闲或无聊时会低频读取新闻。",
  news_min_interval_hours: "无聊看新闻的最小间隔。",
  news_share_probability: "新闻阅读后主动私聊分享的概率，按百分比填写。",
  enable_external_event_self_link: "开启后，Bot 会把新闻和搜索结果先与自己的模型、能力、兴趣、创作、日程或关系做关联判断，再决定是否产生主动分享欲。不是关键词硬触发。",
  external_event_self_link_probability: "自我关联判断通过后进入主动候选的概率倍率，按百分比填写。越高越容易因为与自己有关的新鲜事来找用户。",
  external_event_self_link_cooldown_hours: "同一用户两次因外界信息自我关联而主动找人的最小间隔。",
  news_max_items_per_source: "每个新闻源最多读取多少条候选。",
  news_sources: "新闻源地址。可填 RSS/Atom、B 站空间链接、bilibili:UID、bvid:BV... 或单条 B 站视频链接。AI 日报/早报建议使用定时来源，避免普通新闻阅读反复访问 UP 空间。",
  enable_ai_daily_watch: "开启后按来源配置的固定时间读取 AI 日报/早报；默认 12:00 黑鸦Heya早报，23:00 橘鸦Juya日报。",
  ai_daily_sources: "每行一个来源：名称|UP主名|UID|关键词|HH:MM。到点后当天只尝试一次，会优先文字版，再尝试字幕和视频公开信息。",
  ai_daily_source_uid: "旧版单 UP 配置兼容项。新版本请优先使用 AI 日报/早报来源。",
  ai_daily_check_window: "旧版窗口轮询兼容项。定时来源按每行 HH:MM 执行。",
  ai_daily_check_interval_minutes: "旧版窗口轮询兼容项。定时来源到点后当天只尝试一次。",
  ai_daily_prefer_text_version: "开启后优先读取视频简介里的文字版链接，失败时尝试公开视频字幕，再退回视频公开信息。",
  news_hot_sources: "热点来源配置。用于每日热点候选。",
  news_hot_max_items: "热点候选最多抓取多少条。",
  web_exploration_interests: "主动搜索时的兴趣倾向。不是硬名单，Bot 会结合人格、日程和最近聊天自行决定。",
  enable_web_exploration_boredom_search: "开启后 Bot 空闲或无聊时会自己决定搜索主题并调用网页搜索。",
  web_exploration_min_interval_hours: "两次自主搜索之间的最小间隔。",
  web_exploration_share_probability: "完成探索后，主动私聊分享的概率，按百分比填写。",
  web_exploration_max_results: "每次主动搜索最多读取多少条结果；AstrBot 全局网页搜索和自定义接口都会按这个数量裁剪。",
  WEB_EXPLORATION_API_BASE_URL: "可选自定义搜索接口地址。只影响主动搜索空档探索的联网检索；留空继续使用 AstrBot 全局网页搜索。支持 POST JSON，也支持 GET 占位/尾插，例如 https://example.com/search?q={query} 或 https://example.com/search?q=。",
  WEB_EXPLORATION_API_KEY: "自定义搜索接口鉴权 Key。填写后请求头会带 Authorization: Bearer；本地接口无需鉴权可留空。",
  WEB_EXPLORATION_API_MODEL: "传给自定义搜索接口的模型名，不影响主动搜索的选题/整理模型。",
  QZONE_COOKIE: "可填写浏览器 QQ 空间 Cookie，作为查看、点赞、评论和发布说说的优先凭据；留空时仍使用 OneBot 自动 Cookie。需包含 uin/p_uin 与 p_skey 或 skey，填写后可在 QQ 空间页刷新 Cookies 或到排障中心测试链路。",
  qzone_life_publish_min_interval_hours: "两次低频生活说说之间的最小间隔。",
  qzone_life_publish_probability: "满足条件时发布生活说说的概率，按百分比填写。",
  enable_qzone_generated_image_publish: "开启后，生活说说或情绪说说发布前可按概率调用主动生图能力生成一张配图。需要同时启用 QQ 空间动态和可用的主动生图后端。",
  qzone_generated_image_probability: "满足发说说条件后尝试生成配图的概率，按百分比填写。",
  enable_qzone_comment_inbox: "默认关闭。开启后低频拉取自己最近说说详情，解析评论列表，首次只记录已见评论；后续新评论由模型判断是否需要公开追加回复。",
  qzone_comment_inbox_interval_minutes: "两次评论收件箱自动检查之间的最小间隔。",
  qzone_comment_inbox_recent_posts: "每次向前扫描多少条自己的最近说说详情。",
  qzone_comment_inbox_max_replies_per_tick: "每次后台检查最多公开回复多少条新评论，建议保持 1，避免刷屏。",
  photo_action_max_daily: "每个私聊对象每天最多生成几张主动图片。真实生成成功就消耗额度，避免失败重试时反复生图。",
  proactive_photo_text_probability: "在主动生图可用、额度未用完，且本轮主动有生活画面或视觉切口时，把普通文字主动升级成带图的概率，按百分比填写。",
  photo_generation_backend: "auto 会在在线图片 API 配置完整时优先尝试在线 API，失败后回退本地 ComfyUI/SDGen；未配置在线 API 时使用本地后端。comfyui/sdgen/external 可指定单一后端。",
  COMFYUI_TEXT2IMG_WORKFLOW_NAME: "用于普通随手拍、风景、桌面小物等 photo_text 的 ComfyUI 工作流名。",
  COMFYUI_SELFIE_WORKFLOW_NAME: "用于自拍或人像类 photo_text 的 ComfyUI 工作流名。若配置了人设参考图，会优先寻找 images=1 的自拍工作流。",
  photo_persona_reference_image_path: "png/jpg/jpeg/webp 本地文件路径或 http(s) 图片 URL；URL 会在首次自拍/人像生图前下载一次并自动回写为本地缓存路径。仅在自拍/人像类主动生图时使用。ComfyUI 会把它作为图片输入传给支持 images=1 的自拍工作流；在线图片 API 会优先尝试 OpenAI 兼容 /images/edits 参考图接口，不支持时回退纯文生图；SDGen 仍按提示词生成。",
  enable_daily_outfit_photo: "开启后，每天日程生成并保存后额外调用一次自拍/人像生图能力，根据当天日程、天气和状态生成角色当天穿搭照片，并替换拓展页左上角 Logo。失败会记录当天结果，不会因为刷新页面反复请求。",
  daily_outfit_photo_prompt: "可选。给每日穿搭补充偏好，例如校服、便服、季节感、配色或固定饰品；留空则优先根据当天日程里的上课、出门、居家、雨天、换衣和饰品线索自动组织。",
  enable_natural_language_photo_generation: "默认关闭，避免和独立生图插件抢触发。开启后，只有私聊里明确说“帮我画一张/重新画张图片/生成图片/来张图/用插件能力画”等图片请求才会调用生图；普通“生成/画/改成”不会触发。随消息带图或引用图片并明确要求改图、修图、重绘，或说“改成/加上/去掉……”时才会尝试改图。该入口只对主人私聊开放。",
  natural_language_photo_generation_max_daily: "独立于主动生图额度和每日穿搭。成功生成或已实际请求后端但失败的情况会计入，避免接口异常时被反复请求。0 表示关闭自然语言生图/改图。",
  natural_language_photo_extra_prompt: "只作用于自然语言生图/改图入口，会接在用户要求后、风格与场景预设前。留空则不追加这段；全局固定附加提示词仍在所有生图最后追加。",
  comfyui_photo_wait_seconds: "本地 ComfyUI 工作流最多等待多久。超时后不会假装已经拍照。",
  enable_local_photo_load_guard: "开启后，本地 ComfyUI/SDGen 生图前读取 CPU/内存负载；负载偏高时延后本次主动计划，或在 auto 模式下改走在线图片 API。",
  local_photo_cpu_busy_percent: "CPU 使用率达到该百分比时，暂缓本地 ComfyUI/SDGen 生图。需要 psutil 可用；不可用时会放行。",
  local_photo_memory_busy_percent: "内存使用率达到该百分比时，暂缓本地 ComfyUI/SDGen 生图。",
  local_photo_defer_minutes: "只有本地 ComfyUI/SDGen 可用且电脑忙时，保留原主动计划并延后这么久再重试。",
  external_image_api_platform: "可填 auto、openai、bailian。auto 会根据 API 地址和模型名自动判断。",
  EXTERNAL_IMAGE_API_BASE_URL: "在线生图接口地址。OpenAI 兼容可填完整 /images/generations 地址或 API 根地址；百炼可填 /api/v1 根地址或完整生图接口。",
  EXTERNAL_IMAGE_API_KEY: "在线图片 API 的鉴权 Key。保存后会写入插件配置；请只在可信本机环境填写。",
  EXTERNAL_IMAGE_API_MODEL: "必须填写该平台的图片模型名，不能填写 gpt-5.5、deepseek、claude、qwen 等聊天/文本模型。填写后配合 API 地址和 Key 可作为 external 或 auto 的生图后端。",
  external_image_api_size: "在线生图尺寸，例如 1024x1024、768x1344。",
  external_image_api_timeout_seconds: "等待在线图片 API 返回结果的最长时间。",
  external_image_api_custom_headers: "可选。每行一个请求头，格式：Key: Value。会追加到在线生图 API 请求；下载同源结果图时也会安全复用。",
  enable_backup_external_image_api: "开启后，主在线图片 API 请求失败、超时或未配置完整时，会先尝试这组备选 API，再回退本地 ComfyUI/SDGen。",
  backup_external_image_api_platform: "可填 auto、openai、bailian。含义与主在线生图平台一致，只在备选 API 生效时使用。",
  BACKUP_EXTERNAL_IMAGE_API_BASE_URL: "备选在线生图接口地址。主在线 API 失败后才会使用。",
  BACKUP_EXTERNAL_IMAGE_API_KEY: "备选在线图片 API 的鉴权 Key。留空则不会启用备选后端。",
  BACKUP_EXTERNAL_IMAGE_API_MODEL: "备选平台的图片模型名，不要填写聊天/文本模型。",
  backup_external_image_api_size: "备选在线生图尺寸，例如 1024x1024、768x1344。",
  backup_external_image_api_timeout_seconds: "等待备选在线图片 API 返回结果的最长时间。",
  backup_external_image_api_custom_headers: "可选。每行一个请求头，格式：Key: Value。仅在备选在线图片 API 生效时使用。",
  photo_generation_style: "影响主动生图提示词的整体风格倾向，可填 真实、二次元 或 其他。",
  photo_generation_style_custom_prompt: "当风格为“其他”时，把这里作为额外风格要求注入生图提示词。",
  photo_generation_fixed_prompt: "所有生图提交后端前都会追加这段固定提示词，包括主动随手拍、每日穿搭、自然语言文生图和引用/携带图片改图。适合放固定画质、角色细节、安全区或负面约束；留空不追加。",
  photo_generation_scene_presets: "格式参考通用生图插件，一行一个：预设名:提示词。内置已有角色自拍、COS自拍、镜前穿搭、头像特写、房间日常、可拍画面、表情包场景；自定义同名会覆盖内置。",
  private_reading_min_interval_hours: "两次私下阅读之间的最小间隔。",
  private_reading_max_photo_count: "只阅读页数不超过该值的素材，避免视觉理解成本过高。",
  private_reading_share_probability: "读完后主动提起阅读体验的概率，按百分比填写。",
  private_reading_default_keywords: "私下阅读时默认搜索关键词。多个词可用逗号或换行分隔。",
  private_reading_blocked_tags: "过滤标签。匹配到这些标签时跳过对应素材。",
  private_reading_ask_probability: "无聊时向用户征求推荐的概率，按百分比填写。",
  enable_private_reading_preference_influence: "开启后，夹层阅读评分样本足够时会把稳定偏好作为私聊私密互动的弱背景；关闭后评分只用于素材挑选。",
  private_reading_preference_min_ratings: "累计评分达到这个数量后，偏好画像才会影响私聊私密互动。",
  private_reading_preference_max_terms: "每次注入最多参考多少个稳定偏好词，避免上下文太长或风格偏移。",
  unanswered_screen_peek_after_minutes: "主动消息发出后，用户沉默多久才允许尝试识屏观察。",
  unanswered_screen_peek_cooldown_minutes: "沉默识屏触发后的冷却时间。",
  creative_inspiration_probability: "从生活小事、梦境或日记里长出创作灵感的概率，按百分比填写。",
  creative_share_probability: "创作达到节点后自然透露给用户的概率，按百分比填写。",
  creative_chars_per_session: "每次闲暇创作行为大约写多少字；实际字数会受人格和当天能量影响。",
  creative_max_active_projects: "同时保留多少个进行中的创作项目。",
  worldbook_auto_import: "启动或打开页面时自动从关系网资料源刷新用户/群资料。",
  worldbook_member_match_aliases: "提到别名或称呼时辅助匹配 QQ 锚点，但 QQ 号仍是身份主锚点。",
  worldbook_self_registration: "群成员 @Bot 说“我是 XX”时，允许进入二次确认的自登记流程。",
  worldbook_self_registration_block_words: "命中这些词的自报名字、别名或原始自登记文本会被直接拒绝，不进入关系网待确认流程。",
  worldbook_self_registration_block_reply: "自登记命中屏蔽词、称呼不合规或疑似冒领/冲突时，Bot 返回给用户的统一拒绝文案。留空时回退到默认回复。",
  worldbook_auto_pending_observations: "根据低频互动生成待确认观察，不直接写死到资料正文。",
  worldbook_member_inject_limit: "单次回复最多自动注入多少个相关用户词条。",
  worldbook_config_paths: "关系网资料来源路径。用于读取既有资料，不应写死在代码里。",
  cross_user_memory_owner_only: "开启后，只有主人能在私聊里查询 Bot 与其他用户或群聊之间的近期互动摘要；关闭后所有目标私聊用户都可查询。",
  atrelay_require_worldbook_first: "转述或 @ 群友时优先用关系网解析，避免群名片变化导致认错人。",
  atrelay_member_cache_minutes: "群成员列表缓存时间，减少频繁查询。",
  atrelay_sensitive_confirm: "敏感、私密或带情绪的转述是否先向用户确认。",
  enable_atrelay_llm_rewrite: "开启后先用模型把要转述的话改成 Bot 自然会说的短句；关闭后直接发送解析出的正文，速度更快。",
  atrelay_default_relay_style: "默认转述方式：persona 按人格改写，soft 委婉，original 原话。",
  atrelay_multi_target_limit: "一次转述最多允许几个目标，防止刷屏。",
  response_review_mode: "控制回复/主动复核范围。主动消息发送前统一复核；被动侧只处理严重复读、泄露内部提示等保护性问题；full 会额外让较长被动回复参与模型改写，延迟更高。",
  SMART_SILENCE_PROVIDER_ID: "用于用户表达“不想继续这个话题 / 别问了 / 换个话题 / 不用回复”时的发送前沉默判定。留空时跟随回复/主动复核模型。",
  smart_silence_min_confidence: "小模型判定 silent 且达到该置信度才会真正吞掉回复。值越高越保守，按百分比填写。",
  smart_silence_model_timeout_seconds: "智能沉默判定最长等待时间。超时默认放行，避免正常回复被拖慢。",
  proactive_review_strength: "控制主动消息发送前复核的拦截力度。默认宽松，避免模型过度保守导致主动消息归零。",
  proactive_review_hard_risk_threshold: "本地语义风险达到该值时会硬拦截主动候选。值越高越少拦截，按百分比填写。",
  proactive_review_low_score_threshold: "标准/严格强度下，候选价值分低于该值且压力较高时会延后。值越低越少延后，按百分比填写。",
  proactive_review_pressure_threshold: "标准/严格强度下，打扰压力达到该值且候选分偏低时会延后。值越高越少延后，按百分比填写。",
  response_review_max_chars: "用于判断普通被动回复是否偏长。默认模式会处理短闲聊被扩写成建议清单、天气小作文的情况；full 模式会更积极复核普通偏长回复。",
  emotional_gate_hurt_threshold: "用户消息明确刺到 Bot、并达到该阈值后才会短期变安静；轻度玩笑和普通边界不会直接触发。",
  emotional_gate_refuse_threshold: "累计刺痛感达到该阈值后才会明显不满或短暂回避；建议保持较高。",
  emotional_gate_recovery_per_hour: "情绪余波每小时自然缓和多少分；数值越高越不容易长时间低落。",
  emotional_gate_max_hurt_minutes: "单次刺痛事件最长收敛/暂停主动的分钟数；道歉或安抚可以提前恢复。",
  enable_llm_emotion_judgement: "可选使用模型异步复核用户消息是否会改变 Bot 自身短期情绪余波；本轮被动回复仍使用缓存状态。",
  emotion_judgement_mode: "模型复核范围：可疑项更省消耗，总是复核更细但更耗；结果主要影响后续轮次。",
  EMOTION_JUDGEMENT_PROVIDER_ID: "用于异步复核用户消息是否会改变 Bot 自身短期情绪余波。建议选择便宜、低延迟、JSON 稳定、分类保守的小模型；留空会先回退到排障检查模型，再回退到关系站位/陪伴通用/主模型。",
  enable_qzone_emotional_vent_publish: "短期余波很重时是否允许低频发布公开心情说说。",
  qzone_emotional_vent_threshold: "触发公开心情动态所需的短期余波强度。",
  qzone_emotional_vent_cooldown_hours: "两次公开心情动态之间的最小间隔。",
  qzone_emotional_vent_probability: "达到条件后实际尝试公开心情动态的概率，按百分比填写。",
};

const featureSettingGroups = {
  enable_mai_style_integration: [
    "default_style",
    "reply_style_prompt",
    "enable_companion_memory",
    "memory_refresh_interval_minutes",
    "max_companion_memory_items",
    "enable_expression_learning",
    "expression_learning_mode",
    "enable_expression_manual_review",
    "enable_expression_style_review",
    "max_learned_expression_items",
    "enable_intent_emotion_analysis",
    "enable_response_self_review",
    "enable_passive_topic_suppression",
    "passive_topic_memory_hours",
    "enable_relationship_state_machine",
    "enable_dialogue_episode_memory",
    "episode_memory_refresh_messages",
    "episode_memory_refresh_minutes",
    "max_dialogue_episodes",
    "enable_open_loop_tracking",
    "enable_user_habit_learning",
    "user_habit_min_count",
    "user_habit_max_items",
    "enable_food_menu_recommendation",
  ],
  enable_companion_memory: ["memory_refresh_interval_minutes", "max_companion_memory_items"],
  enable_expression_learning: ["expression_learning_mode", "enable_expression_manual_review", "enable_expression_style_review", "max_learned_expression_items"],
  enable_intent_emotion_analysis: [],
  enable_response_self_review: ["response_review_mode", "enable_smart_silence", "SMART_SILENCE_PROVIDER_ID", "smart_silence_min_confidence", "smart_silence_model_timeout_seconds", "proactive_review_strength", "proactive_review_hard_risk_threshold", "proactive_review_low_score_threshold", "proactive_review_pressure_threshold", "response_review_max_chars"],
  enable_passive_topic_suppression: ["passive_topic_memory_hours"],
  enable_relationship_state_machine: ["proactive_unanswered_slowdown_start", "proactive_unanswered_max_interval_multiplier", "friend_unanswered_max_cooldown_hours"],
  enable_emotion_simulation: ["enable_llm_emotion_judgement", "emotion_judgement_mode", "EMOTION_JUDGEMENT_PROVIDER_ID", "emotional_gate_hurt_threshold", "emotional_gate_refuse_threshold", "emotional_gate_recovery_per_hour", "emotional_gate_max_hurt_minutes", "enable_qzone_emotional_vent_publish", "qzone_emotional_vent_threshold", "qzone_emotional_vent_cooldown_hours", "qzone_emotional_vent_probability"],
  enable_dialogue_episode_memory: ["episode_memory_refresh_messages", "episode_memory_refresh_minutes", "max_dialogue_episodes"],
  enable_open_loop_tracking: ["max_dialogue_episodes"],
  enable_user_habit_learning: ["user_habit_min_count", "user_habit_max_items"],
  enable_food_menu_recommendation: [],
  enable_proactive_only_mode: ["enable_llm_proactive_message", "proactive_prompt_template", "enable_llm_proactive_persona_judge", "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID", "proactive_persona_judge_send_threshold", "proactive_persona_judge_cache_minutes"],
  enable_humanized_states: ["humanized_state_intensity", "enable_health_state", "enable_hunger_state", "enable_qq_presence_sync", "enable_qq_custom_presence_sync", "inject_passive_states", "enable_passive_state_delta_injection", "enable_rest_reply_simulation", "rest_reply_mode", "rest_reply_probability", "rest_reply_llm_threshold", "rest_reply_active_windows", "rest_reply_awake_grace_minutes", "enable_rest_backlog_reply", "rest_backlog_max_messages", "REST_WAKEUP_PROVIDER_ID", "enable_cycle_state"],
  enable_rest_reply_simulation: ["rest_reply_mode", "rest_reply_probability", "rest_reply_llm_threshold", "rest_reply_active_windows", "rest_reply_awake_grace_minutes", "enable_rest_backlog_reply", "rest_backlog_max_messages", "REST_WAKEUP_PROVIDER_ID"],
  enable_segmented_proactive_reply: ["segmented_proactive_scope", "segmented_proactive_chat_scope", "segmented_proactive_threshold", "segmented_proactive_min_segment_chars", "segmented_proactive_max_segments", "segmented_proactive_send_as_forward", "segmented_proactive_split_mode", "segmented_proactive_regex", "segmented_proactive_split_words", "enable_segmented_proactive_content_cleanup", "segmented_proactive_content_cleanup_scope", "segmented_proactive_content_cleanup_rule", "segmented_proactive_content_cleanup_words", "segmented_proactive_interval_method", "segmented_proactive_interval_min", "segmented_proactive_interval_max", "segmented_proactive_log_base"],
  inject_passive_states: ["humanized_state_intensity", "enable_passive_state_delta_injection"],
  enable_health_state: ["humanized_state_intensity"],
  enable_hunger_state: ["humanized_state_intensity"],
  enable_cycle_state: ["humanized_state_intensity"],
  enable_skill_growth_simulation: ["skill_growth_rate", "enable_skill_growth_passive_injection", "enable_skill_growth_schedule_influence", "skill_growth_schedule_influence_strength"],
  enable_message_debounce: ["inbound_message_debounce_seconds", "text_message_debounce_seconds", "image_message_debounce_seconds", "forward_message_debounce_seconds", "text_message_debounce_max_wait_seconds", "message_debounce_max_merge_messages", "enable_smart_message_debounce", "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID", "smart_message_debounce_model_timeout_seconds", "smart_message_debounce_wait_seconds", "smart_message_debounce_learning_window_seconds", "smart_message_debounce_examples_limit"],
  enable_recall_enhancement: ["enable_recall_cancel_reply", "enable_recall_message_cache", "enable_recall_transcribe_command", "recall_message_cache_ttl_seconds", "recall_message_cache_max_items", "recall_message_image_cache_max_mb", "enable_forbidden_word_recall", "recall_forbidden_words", "recall_forbidden_scope", "recall_forbidden_word_case_sensitive"],
  enable_recall_cancel_reply: ["recall_message_cache_ttl_seconds"],
  enable_recall_message_cache: ["enable_recall_transcribe_command", "recall_message_cache_ttl_seconds", "recall_message_cache_max_items", "recall_message_image_cache_max_mb"],
  enable_forbidden_word_recall: ["recall_forbidden_words", "recall_forbidden_scope", "recall_forbidden_word_case_sensitive"],
  enable_proactive_quote_trigger_message: ["enable_quote_group_reply", "enable_quote_group_interjection", "enable_quote_private_proactive", "quote_skip_short_reply_chars", "quote_target_strategy"],
  enable_private_image_self_recognition: ["private_image_vision_wait_seconds", "private_image_provider_timeout_seconds", "enable_private_image_gif_enhancement", "private_image_gif_max_frames", "enable_private_image_vision_cache", "private_image_vision_cache_max_items", "private_image_self_recognition_hint"],
  enable_private_image_gif_enhancement: ["private_image_gif_max_frames"],
  enable_environment_perception: ["environment_perception_timezone", "holiday_country", "enable_holiday_perception", "enable_platform_perception", "enable_model_perception", "enable_worldview_perception", "enable_lunar_perception", "enable_solar_term_perception", "enable_almanac_perception"],
  enable_holiday_perception: ["holiday_country"],
  enable_platform_perception: [],
  enable_model_perception: [],
  enable_lunar_perception: ["environment_perception_timezone"],
  enable_solar_term_perception: ["environment_perception_timezone"],
  enable_almanac_perception: ["environment_perception_timezone"],
  enable_yesterday_screen_diary_context: ["screen_diary_context_max_chars"],
  enable_group_companion: [
    "max_group_recent_messages",
    "max_group_slang_terms",
    "enable_group_context_injection",
    "enable_group_injection_guard",
    "enable_group_persona_denoise",
    "enable_group_scene_awareness",
    "group_scene_recent_limit",
    "enable_group_conversation_followup",
    "group_conversation_followup_seconds",
    "group_conversation_followup_max_turns",
    "enable_group_reality_promise_guard",
    "enable_group_wakeup_enhancement",
    "group_wakeup_direct_words",
    "group_wakeup_context_words",
    "group_wakeup_interest_keywords",
    "group_wakeup_interest_probability",
    "enable_group_wakeup_question",
    "group_wakeup_question_threshold",
    "enable_group_wakeup_cold_group",
    "group_wakeup_cold_group_threshold",
    "group_wakeup_cold_group_idle_minutes",
    "group_wakeup_cooldown_seconds",
    "group_wakeup_generated_keyword_limit",
    "group_wakeup_topic_interest_max_boost",
    "group_wakeup_debounce_pending_penalty",
    "group_wakeup_fatigue_limit",
    "group_wakeup_fatigue_decay_minutes",
    "group_wakeup_log_limit",
    "group_wakeup_short_text_wait_seconds",
    "enable_group_high_intensity_mode",
    "group_high_intensity_wakeup_window_seconds",
    "group_high_intensity_wakeup_threshold",
    "group_high_intensity_cooldown_seconds",
    "group_high_intensity_merge_seconds",
    "group_high_intensity_max_merge_messages",
    "group_high_intensity_merge_scope",
    "enable_group_slang_learning",
    "enable_group_slang_meanings",
    "enable_group_member_profiles",
    "enable_group_topic_threads",
    "enable_group_episode_memory",
    "enable_group_relationship_graph",
    "enable_group_interjection",
    "enable_group_interjection_feedback",
    "enable_group_repeat_follow",
    "enable_group_privacy_guard",
  ],
  enable_group_context_injection: ["max_group_recent_messages", "group_scene_recent_limit"],
  enable_group_injection_guard: [],
  enable_group_persona_denoise: [],
  enable_forward_message_adaptation: ["forward_message_mode", "forward_message_max_messages", "forward_message_max_chars", "forward_message_parse_nested", "forward_message_image_vision", "forward_message_image_limit"],
  enable_group_scene_awareness: ["group_scene_recent_limit", "enable_group_conversation_followup", "group_conversation_followup_seconds", "group_conversation_followup_max_turns"],
  enable_group_wakeup_enhancement: ["group_wakeup_direct_words", "group_wakeup_context_words", "group_wakeup_interest_keywords", "group_wakeup_interest_probability", "enable_group_wakeup_question", "group_wakeup_question_threshold", "enable_group_wakeup_cold_group", "group_wakeup_cold_group_threshold", "group_wakeup_cold_group_idle_minutes", "group_wakeup_topic_interest_max_boost", "group_wakeup_debounce_pending_penalty", "group_wakeup_short_text_wait_seconds", "group_wakeup_cooldown_seconds", "group_wakeup_generated_keyword_limit", "group_wakeup_fatigue_limit", "group_wakeup_fatigue_decay_minutes", "group_wakeup_log_limit", "enable_group_high_intensity_mode", "group_high_intensity_wakeup_window_seconds", "group_high_intensity_wakeup_threshold", "group_high_intensity_cooldown_seconds", "group_high_intensity_merge_seconds", "group_high_intensity_max_merge_messages", "group_high_intensity_merge_scope", "group_scene_recent_limit"],
  enable_group_high_intensity_mode: ["group_high_intensity_wakeup_window_seconds", "group_high_intensity_wakeup_threshold", "group_high_intensity_cooldown_seconds", "group_high_intensity_merge_seconds", "group_high_intensity_max_merge_messages", "group_high_intensity_merge_scope"],
  enable_group_conversation_followup: ["group_conversation_followup_seconds", "group_conversation_followup_max_turns"],
  enable_group_slang_learning: ["max_group_slang_terms", "max_group_recent_messages", "enable_group_slang_meanings", "enable_group_slang_web_search", "group_slang_web_search_terms", "group_slang_web_search_results"],
  enable_group_slang_meanings: ["max_group_slang_terms", "enable_group_slang_web_search"],
  enable_group_slang_web_search: ["group_slang_web_search_terms", "group_slang_web_search_results"],
  enable_group_member_profiles: ["max_group_recent_messages"],
  enable_group_topic_threads: ["max_group_recent_messages"],
  enable_group_episode_memory: ["max_group_recent_messages"],
  enable_group_relationship_graph: ["max_group_recent_messages"],
  enable_group_interjection: ["group_interject_min_interval_minutes", "group_interject_max_daily", "enable_group_interjection_feedback"],
  enable_group_repeat_follow: ["group_repeat_trigger_threshold", "group_repeat_count_distinct_users_only", "group_repeat_follow_probability", "group_repeat_interrupt_probability", "group_repeat_interrupt_probability_step", "group_repeat_interrupt_text", "group_repeat_interrupt_image_path"],
  enable_group_interjection_feedback: ["group_interject_min_interval_minutes", "group_interject_max_daily"],
  enable_group_privacy_guard: [],
  enable_worldbook_member_recognition: ["worldbook_auto_import", "worldbook_member_match_aliases", "worldbook_self_registration", "worldbook_self_registration_block_words", "worldbook_self_registration_block_reply", "worldbook_auto_pending_observations", "worldbook_member_inject_limit", "worldbook_config_paths"],
  enable_cross_user_memory_bridge: ["cross_user_memory_owner_only"],
  enable_atrelay_tools: ["atrelay_require_worldbook_first", "atrelay_member_cache_minutes", "atrelay_sensitive_confirm", "enable_atrelay_llm_rewrite", "atrelay_default_relay_style", "atrelay_multi_target_limit"],
  enable_livingmemory_integration: [],
  enable_bilibili_integration: ["enable_bilibili_boredom_watch", "bilibili_boredom_min_interval_hours", "bilibili_share_probability", "bilibili_share_min_score"],
  enable_bilibili_boredom_watch: ["bilibili_boredom_min_interval_hours", "bilibili_share_probability", "bilibili_share_min_score"],
  enable_news_integration: ["enable_news_daily_hot_read", "enable_ai_daily_watch", "enable_news_boredom_read", "enable_external_event_self_link", "news_hot_sources", "news_hot_max_items", "news_sources", "ai_daily_sources", "ai_daily_prefer_text_version", "news_min_interval_hours", "news_share_probability", "external_event_self_link_probability", "external_event_self_link_cooldown_hours", "news_max_items_per_source"],
  enable_news_daily_hot_read: ["news_hot_sources", "news_hot_max_items", "enable_ai_daily_watch", "ai_daily_sources"],
  enable_ai_daily_watch: ["ai_daily_sources", "ai_daily_prefer_text_version"],
  enable_news_boredom_read: ["news_min_interval_hours", "news_share_probability", "enable_external_event_self_link", "external_event_self_link_probability", "external_event_self_link_cooldown_hours", "news_max_items_per_source"],
  enable_external_event_self_link: ["external_event_self_link_probability", "external_event_self_link_cooldown_hours", "news_share_probability", "web_exploration_share_probability"],
  enable_web_exploration: ["web_exploration_interests", "enable_web_exploration_boredom_search", "web_exploration_min_interval_hours", "web_exploration_share_probability", "enable_external_event_self_link", "external_event_self_link_probability", "external_event_self_link_cooldown_hours", "web_exploration_max_results", "WEB_EXPLORATION_API_BASE_URL", "WEB_EXPLORATION_API_KEY", "WEB_EXPLORATION_API_MODEL"],
  enable_web_exploration_boredom_search: ["web_exploration_interests", "web_exploration_min_interval_hours", "enable_external_event_self_link", "external_event_self_link_probability", "external_event_self_link_cooldown_hours", "web_exploration_max_results", "WEB_EXPLORATION_API_BASE_URL", "WEB_EXPLORATION_API_KEY", "WEB_EXPLORATION_API_MODEL"],
  enable_qzone_integration: ["QZONE_COOKIE", "enable_qzone_life_publish", "qzone_life_publish_min_interval_hours", "qzone_life_publish_probability", "enable_qzone_generated_image_publish", "qzone_generated_image_probability", "enable_qzone_comment_inbox", "qzone_comment_inbox_interval_minutes", "qzone_comment_inbox_recent_posts", "qzone_comment_inbox_max_replies_per_tick"],
  enable_qzone_life_publish: ["qzone_life_publish_min_interval_hours", "qzone_life_publish_probability"],
  enable_qzone_generated_image_publish: ["qzone_generated_image_probability"],
  enable_qzone_comment_inbox: ["qzone_comment_inbox_interval_minutes", "qzone_comment_inbox_recent_posts", "qzone_comment_inbox_max_replies_per_tick"],
  enable_photo_text_action: ["photo_action_max_daily", "proactive_photo_text_probability", "photo_generation_backend", "COMFYUI_TEXT2IMG_WORKFLOW_NAME", "COMFYUI_SELFIE_WORKFLOW_NAME", "photo_persona_reference_image_path", "enable_daily_outfit_photo", "daily_outfit_photo_prompt", "enable_natural_language_photo_generation", "natural_language_photo_generation_max_daily", "natural_language_photo_extra_prompt", "comfyui_photo_wait_seconds", "enable_local_photo_load_guard", "local_photo_cpu_busy_percent", "local_photo_memory_busy_percent", "local_photo_defer_minutes", "external_image_api_platform", "EXTERNAL_IMAGE_API_BASE_URL", "EXTERNAL_IMAGE_API_KEY", "EXTERNAL_IMAGE_API_MODEL", "external_image_api_size", "external_image_api_timeout_seconds", "external_image_api_custom_headers", "enable_backup_external_image_api", "backup_external_image_api_platform", "BACKUP_EXTERNAL_IMAGE_API_BASE_URL", "BACKUP_EXTERNAL_IMAGE_API_KEY", "BACKUP_EXTERNAL_IMAGE_API_MODEL", "backup_external_image_api_size", "backup_external_image_api_timeout_seconds", "backup_external_image_api_custom_headers", "photo_generation_style", "photo_generation_style_custom_prompt", "photo_generation_fixed_prompt", "photo_generation_scene_presets"],
  enable_backup_external_image_api: ["backup_external_image_api_platform", "BACKUP_EXTERNAL_IMAGE_API_BASE_URL", "BACKUP_EXTERNAL_IMAGE_API_KEY", "BACKUP_EXTERNAL_IMAGE_API_MODEL", "backup_external_image_api_size", "backup_external_image_api_timeout_seconds", "backup_external_image_api_custom_headers"],
  enable_private_reading_integration: ["enable_private_reading_boredom_read", "enable_private_reading_ask_recommendation", "private_reading_min_interval_hours", "private_reading_max_photo_count", "private_reading_ask_probability", "private_reading_default_keywords", "private_reading_blocked_tags", "enable_private_reading_preference_influence", "private_reading_preference_min_ratings", "private_reading_preference_max_terms"],
  enable_private_reading_boredom_read: ["private_reading_min_interval_hours", "private_reading_max_photo_count", "private_reading_share_probability", "private_reading_default_keywords", "private_reading_blocked_tags", "enable_private_reading_preference_influence", "private_reading_preference_min_ratings", "private_reading_preference_max_terms"],
  enable_private_reading_ask_recommendation: ["private_reading_ask_probability"],
  enable_private_reading_preference_influence: ["private_reading_preference_min_ratings", "private_reading_preference_max_terms"],
  enable_unanswered_screen_peek_followup: ["unanswered_screen_peek_after_minutes", "unanswered_screen_peek_cooldown_minutes"],
  enable_tts_enhancement: ["tts_generation_mode", "tts_voice_language", "tts_conversion_provider_id", "tts_extra_prompt", "tts_frequency_control_mode", "tts_constraint_mode", "tts_session_min_interval_seconds", "tts_private_min_interval_seconds", "tts_group_min_interval_seconds", "tts_trigger_probability", "tts_private_trigger_probability", "tts_group_trigger_probability", "enable_tts_local_playback", "enable_tts_local_playback_live_only", "tts_local_playback_volume", "enable_tts_live_subtitle_sync", "tts_live_subtitle_url", "tts_local_playback_min_interval_seconds", "auto_voice_enabled", "auto_voice_full_conversion_enabled", "auto_voice_max_chars", "auto_voice_cooldown_seconds", "main_user_voice_probability", "main_user_mention_voice_keywords", "main_user_mention_voice_probability", "main_user_mention_voice_prompt"],
  enable_tts_local_playback: ["enable_tts_local_playback_live_only", "tts_local_playback_volume", "tts_local_playback_min_interval_seconds"],
  enable_creative_writing: ["creative_hidden_mode", "creative_inspiration_probability", "creative_share_probability", "creative_chars_per_session", "creative_max_active_projects"],
  creative_hidden_mode: ["creative_share_probability"],
};

const featureSettingSections = {
  enable_proactive_only_mode: [
    {
      title: "保留主动链路",
      note: "开启仅保留主动能力后，仍保留主动念头、调度和主动文本生成。",
      keys: ["enable_llm_proactive_message", "proactive_prompt_template"],
    },
    {
      title: "人格/世界观复核",
      note: "到点后先检查这个念头是否像当前角色会说、是否越界、是否该延后。",
      keys: ["enable_llm_proactive_persona_judge", "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID", "proactive_persona_judge_send_threshold", "proactive_persona_judge_cache_minutes"],
    },
  ],
  enable_mai_style_integration: [
    {
      title: "回复基座",
      note: "控制私聊接话策略和基础语气。",
      keys: ["default_style", "reply_style_prompt"],
    },
    {
      title: "记忆与表达",
      note: "沉淀长期画像、表达习惯和共同经历。",
      keys: ["enable_companion_memory", "memory_refresh_interval_minutes", "max_companion_memory_items", "enable_expression_learning", "expression_learning_mode", "enable_expression_manual_review", "enable_expression_style_review", "max_learned_expression_items", "enable_dialogue_episode_memory", "episode_memory_refresh_messages", "episode_memory_refresh_minutes", "max_dialogue_episodes"],
    },
    {
      title: "回复策略",
      note: "意图画像、回复/主动复核和重复话题抑制。",
      keys: ["enable_intent_emotion_analysis", "enable_response_self_review", "response_review_mode", "enable_smart_silence", "SMART_SILENCE_PROVIDER_ID", "smart_silence_min_confidence", "smart_silence_model_timeout_seconds", "proactive_review_strength", "proactive_review_hard_risk_threshold", "proactive_review_low_score_threshold", "proactive_review_pressure_threshold", "response_review_max_chars", "enable_passive_topic_suppression", "passive_topic_memory_hours"],
    },
    {
      title: "关系与习惯",
      note: "关系距离、未完话头和用户时段习惯。Bot 自身短期余波在“情绪模拟”里配置。",
      keys: ["enable_relationship_state_machine", "proactive_unanswered_slowdown_start", "proactive_unanswered_max_interval_multiplier", "friend_unanswered_max_cooldown_hours", "enable_open_loop_tracking", "enable_user_habit_learning", "user_habit_min_count", "user_habit_max_items", "enable_food_menu_recommendation"],
    },
  ],
  enable_humanized_states: [
    {
      title: "状态生成",
      note: "控制身体余波和强度，只作为扮演状态，不当成真实用户事实。",
      keys: ["humanized_state_intensity", "enable_health_state", "enable_hunger_state", "enable_cycle_state"],
    },
    {
      title: "QQ 状态同步",
      note: "基础在线状态使用标准接口；自定义短状态默认关闭，避免协议端不支持时断连。",
      keys: ["enable_qq_presence_sync", "enable_qq_custom_presence_sync"],
    },
    {
      title: "被动状态注入",
      note: "控制普通聊天是否参考当前状态，以及是否只在变化时注入。",
      keys: ["inject_passive_states", "enable_passive_state_delta_injection"],
    },
    {
      title: "休息回复闸门",
      note: "睡眠、午休或休息段是否静默，以及醒来后如何补看消息。",
      keys: ["enable_rest_reply_simulation", "rest_reply_mode", "rest_reply_probability", "rest_reply_llm_threshold", "rest_reply_active_windows", "rest_reply_awake_grace_minutes", "enable_rest_backlog_reply", "rest_backlog_max_messages", "REST_WAKEUP_PROVIDER_ID"],
    },
  ],
  enable_message_debounce: [
    {
      title: "重复上报去重",
      note: "只处理平台重复上报同一条消息，不影响用户补话等待。",
      keys: ["inbound_message_debounce_seconds"],
    },
    {
      title: "补话等待",
      note: "分别控制文本、图片和合并转发后等待用户继续补充的时间；开启智能文本收口后，文本固定等待会隐藏。",
      keys: ["text_message_debounce_seconds", "image_message_debounce_seconds", "forward_message_debounce_seconds", "text_message_debounce_max_wait_seconds", "message_debounce_max_merge_messages"],
    },
    {
      title: "智能文本收口",
      note: "先用本地快判放行完整文本；只有疑似半句话才短时调用轻量模型。",
      keys: ["enable_smart_message_debounce", "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID", "smart_message_debounce_model_timeout_seconds", "smart_message_debounce_wait_seconds", "smart_message_debounce_learning_window_seconds", "smart_message_debounce_examples_limit"],
    },
  ],
  enable_recall_enhancement: [
    {
      title: "撤回处理",
      note: "控制发送前取消、短期缓存和用户主动查看撤回内容。",
      keys: ["enable_recall_cancel_reply", "enable_recall_message_cache", "enable_recall_transcribe_command", "recall_message_cache_ttl_seconds", "recall_message_cache_max_items", "recall_message_image_cache_max_mb"],
    },
    {
      title: "违禁词撤回",
      note: "命中配置词时拦截待发送内容或尝试撤回已发消息。",
      keys: ["enable_forbidden_word_recall", "recall_forbidden_words", "recall_forbidden_scope", "recall_forbidden_word_case_sensitive"],
    },
  ],
  enable_proactive_quote_trigger_message: [
    {
      title: "生效场景",
      note: "总开关开启后，再按场景决定是否真正附带引用。",
      keys: ["enable_quote_group_reply", "enable_quote_group_interjection", "enable_quote_private_proactive"],
    },
    {
      title: "引用策略",
      note: "控制短回复是否跳过引用，以及用户引用 Bot 旧消息追问时应该挂当前消息还是旧消息。",
      keys: ["quote_skip_short_reply_chars", "quote_target_strategy"],
    },
  ],
  enable_environment_perception: [
    {
      title: "基础环境",
      note: "时间、平台、模型和消息媒介感知。",
      keys: ["environment_perception_timezone", "enable_platform_perception", "enable_model_perception", "enable_worldview_perception"],
    },
    {
      title: "日期与时令",
      note: "节假日、农历、节气和轻量黄历氛围。",
      keys: ["enable_holiday_perception", "holiday_country", "enable_lunar_perception", "enable_solar_term_perception", "enable_almanac_perception"],
    },
  ],
  enable_private_image_self_recognition: [
    {
      title: "视觉等待",
      note: "收口结束后等待图片转述结果；视觉提前完成会直接进入主链。",
      keys: ["private_image_vision_wait_seconds", "private_image_provider_timeout_seconds"],
    },
    {
      title: "GIF 动图强化",
      note: "可选抽帧理解动态表情包和动图，不开启时按普通图片/GIF 处理。",
      keys: ["enable_private_image_gif_enhancement", "private_image_gif_max_frames"],
    },
    {
      title: "重复图片缓存",
      note: "复用相同图片的视觉摘要，避免表情包反复触发识图模型。",
      keys: ["enable_private_image_vision_cache", "private_image_vision_cache_max_items"],
    },
    {
      title: "角色自我识别",
      note: "把当前角色名字、人设和自定义线索交给视觉模型，辅助判断图里是不是当前角色自己。",
      keys: ["private_image_self_recognition_hint"],
    },
  ],
  enable_group_companion: [
    {
      title: "基础群聊",
      note: "控制群聊消息量、黑话容量和基础上下文。",
      keys: ["max_group_recent_messages", "max_group_slang_terms", "enable_group_context_injection", "enable_group_injection_guard", "enable_group_persona_denoise", "enable_group_privacy_guard"],
    },
    {
      title: "场景与续接",
      note: "判断谁在对谁说话，并维持短时间连续对话。",
      keys: ["enable_group_scene_awareness", "group_scene_recent_limit", "enable_group_conversation_followup", "group_conversation_followup_seconds", "group_conversation_followup_max_turns", "enable_group_reality_promise_guard"],
    },
    {
      title: "唤醒与高强度",
      note: "控制被叫到、兴趣关键词和连续唤醒后的收口。",
      keys: ["enable_group_wakeup_enhancement", "group_wakeup_direct_words", "group_wakeup_context_words", "group_wakeup_interest_keywords", "group_wakeup_interest_probability", "enable_group_wakeup_question", "group_wakeup_question_threshold", "enable_group_wakeup_cold_group", "group_wakeup_cold_group_threshold", "group_wakeup_cold_group_idle_minutes", "group_wakeup_cooldown_seconds", "group_wakeup_generated_keyword_limit", "group_wakeup_topic_interest_max_boost", "group_wakeup_debounce_pending_penalty", "group_wakeup_fatigue_limit", "group_wakeup_fatigue_decay_minutes", "group_wakeup_log_limit", "enable_group_high_intensity_mode", "group_high_intensity_wakeup_window_seconds", "group_high_intensity_wakeup_threshold", "group_high_intensity_cooldown_seconds", "group_high_intensity_merge_seconds", "group_high_intensity_max_merge_messages", "group_high_intensity_merge_scope"],
    },
    {
      title: "群记忆与互动",
      note: "黑话、成员观察、话题线、群片段、插话和复读。",
      keys: ["enable_group_slang_learning", "enable_group_slang_meanings", "enable_group_member_profiles", "enable_group_topic_threads", "enable_group_episode_memory", "enable_group_relationship_graph", "enable_group_interjection", "enable_group_interjection_feedback", "enable_group_repeat_follow"],
    },
  ],
  enable_group_slang_learning: [
    {
      title: "学习范围",
      note: "控制群内黑话候选的容量，以及用于判断上下文的最近消息量。",
      keys: ["max_group_slang_terms", "max_group_recent_messages"],
    },
    {
      title: "释义整理",
      note: "把已记录的黑话、简称和梗整理成可读含义。",
      keys: ["enable_group_slang_meanings"],
    },
    {
      title: "联网参考",
      note: "默认关闭；只为已有黑话候选查外部解释，再判断是否匹配本群用法。",
      keys: ["enable_group_slang_web_search", "group_slang_web_search_terms", "group_slang_web_search_results"],
    },
  ],
  enable_news_integration: [
    {
      title: "读取来源",
      note: "普通新闻源、热点源和 AI 日报/早报定时源。",
      keys: ["news_sources", "enable_news_daily_hot_read", "news_hot_sources", "news_hot_max_items", "enable_ai_daily_watch", "ai_daily_sources", "ai_daily_prefer_text_version"],
    },
    {
      title: "主动分享",
      note: "控制新闻是否在空档阅读、如何形成主动候选。",
      keys: ["enable_news_boredom_read", "news_min_interval_hours", "news_share_probability", "news_max_items_per_source", "enable_external_event_self_link", "external_event_self_link_probability", "external_event_self_link_cooldown_hours"],
    },
  ],
  enable_web_exploration: [
    {
      title: "搜索兴趣",
      note: "控制主动搜索的主题、频率和结果规模。",
      keys: ["web_exploration_interests", "enable_web_exploration_boredom_search", "web_exploration_min_interval_hours", "web_exploration_share_probability", "web_exploration_max_results"],
    },
    {
      title: "自定义搜索接口",
      note: "只接管主动搜索的联网检索；选题和整理仍使用上方“搜索决策/整理”模型。",
      keys: ["WEB_EXPLORATION_API_BASE_URL", "WEB_EXPLORATION_API_KEY", "WEB_EXPLORATION_API_MODEL"],
    },
    {
      title: "外界信息自我关联",
      note: "把搜索和新闻结果先转成内部意愿，再进入主动候选。",
      keys: ["enable_external_event_self_link", "external_event_self_link_probability", "external_event_self_link_cooldown_hours"],
    },
  ],
  enable_qzone_integration: [
    {
      title: "连接",
      note: "QQ 空间能力凭据。",
      keys: ["QZONE_COOKIE"],
    },
    {
      title: "生活说说",
      note: "根据状态、日程和日记余味低频发布公开生活动态。",
      keys: ["enable_qzone_life_publish", "qzone_life_publish_min_interval_hours", "qzone_life_publish_probability", "enable_qzone_generated_image_publish", "qzone_generated_image_probability"],
    },
    {
      title: "评论收件箱",
      note: "低频查看自己说说下的新评论，按需公开追加一句回复。",
      keys: ["enable_qzone_comment_inbox", "qzone_comment_inbox_interval_minutes", "qzone_comment_inbox_recent_posts", "qzone_comment_inbox_max_replies_per_tick"],
    },
  ],
  enable_emotion_simulation: [
    {
      title: "情绪余波",
      note: "控制被刺到后的收敛、缓和和主动暂停节奏。",
      keys: ["enable_llm_emotion_judgement", "emotion_judgement_mode", "EMOTION_JUDGEMENT_PROVIDER_ID", "emotional_gate_hurt_threshold", "emotional_gate_refuse_threshold", "emotional_gate_recovery_per_hour", "emotional_gate_max_hurt_minutes"],
    },
    {
      title: "公开心情动态",
      note: "默认关闭；仅主人可触发，且必须同时满足 QZone 可用、冷却、阈值和概率。",
      keys: ["enable_qzone_emotional_vent_publish", "qzone_emotional_vent_threshold", "qzone_emotional_vent_cooldown_hours", "qzone_emotional_vent_probability"],
    },
  ],
  enable_private_reading_integration: [
    {
      title: "自主阅读",
      note: "控制空档私下阅读、用户推荐请求和基础素材范围。",
      keys: ["enable_private_reading_boredom_read", "enable_private_reading_ask_recommendation", "private_reading_min_interval_hours", "private_reading_max_photo_count", "private_reading_ask_probability", "private_reading_default_keywords", "private_reading_blocked_tags"],
    },
    {
      title: "偏好影响",
      note: "评分样本足够后，把稳定偏好作为弱背景。",
      keys: ["enable_private_reading_preference_influence", "private_reading_preference_min_ratings", "private_reading_preference_max_terms"],
    },
  ],
  enable_creative_writing: [
    {
      title: "创作方式",
      note: "控制私下创作触发、是否低调提起和单次推进规模。",
      keys: ["creative_hidden_mode", "creative_inspiration_probability", "creative_share_probability", "creative_chars_per_session", "creative_max_active_projects"],
    },
  ],
  enable_segmented_proactive_reply: [
    {
      title: "切分规则",
      note: "决定主动消息什么时候拆、按什么拆，以及短片段是否并回去。",
      keys: ["segmented_proactive_scope", "segmented_proactive_chat_scope", "segmented_proactive_threshold", "segmented_proactive_min_segment_chars", "segmented_proactive_max_segments", "segmented_proactive_split_mode", "segmented_proactive_regex", "segmented_proactive_split_words"],
    },
    {
      title: "发送方式",
      note: "切出多段后，可选打包成合并转发以减少刷屏；不支持时会回退普通分段。",
      keys: ["segmented_proactive_send_as_forward"],
    },
    {
      title: "内容清理",
      note: "用于去掉句尾分隔符、空格或换行；括号和双引号内的内容会被保护。",
      keys: ["enable_segmented_proactive_content_cleanup", "segmented_proactive_content_cleanup_scope", "segmented_proactive_content_cleanup_rule", "segmented_proactive_content_cleanup_words"],
    },
    {
      title: "发送间隔",
      note: "控制分段之间等多久，避免像刷屏，也避免间隔太死板。",
      keys: ["segmented_proactive_interval_method", "segmented_proactive_interval_min", "segmented_proactive_interval_max", "segmented_proactive_log_base"],
    },
  ],
  enable_group_wakeup_enhancement: [
    {
      title: "唤醒词",
      note: "决定哪些群消息可能把 Bot 拉进回复链。",
      keys: ["group_wakeup_direct_words", "group_wakeup_context_words"],
    },
    {
      title: "兴趣唤醒",
      note: "让 Bot 碰到自己感兴趣的话题时低概率接话。",
      keys: ["group_wakeup_interest_keywords", "group_wakeup_interest_probability", "group_wakeup_topic_interest_max_boost", "group_wakeup_generated_keyword_limit"],
    },
    {
      title: "解惑与冷群",
      note: "开放疑问和冷群开场都会按强度阈值判断；冷群唤醒默认关闭，适合很安静的小群。",
      keys: ["enable_group_wakeup_question", "group_wakeup_question_threshold", "enable_group_wakeup_cold_group", "group_wakeup_cold_group_threshold", "group_wakeup_cold_group_idle_minutes"],
    },
    {
      title: "节流与拟人感",
      note: "控制冷却、收口等待和被频繁叫到后的疲劳感。",
      keys: ["group_wakeup_cooldown_seconds", "group_wakeup_short_text_wait_seconds", "group_wakeup_debounce_pending_penalty", "group_wakeup_fatigue_limit", "group_wakeup_fatigue_decay_minutes"],
    },
    {
      title: "高强度收口",
      note: "连续被叫到时，按配置合并后续唤醒消息，减少多次 LLM 调用和后台成本。",
      keys: ["enable_group_high_intensity_mode", "group_high_intensity_wakeup_window_seconds", "group_high_intensity_wakeup_threshold", "group_high_intensity_cooldown_seconds", "group_high_intensity_merge_seconds", "group_high_intensity_max_merge_messages", "group_high_intensity_merge_scope"],
    },
    {
      title: "记录与上下文",
      note: "控制页面记录量和场景判断参考消息数。",
      keys: ["group_wakeup_log_limit", "group_scene_recent_limit"],
    },
  ],
  enable_photo_text_action: [
    {
      title: "触发与额度",
      note: "控制多久会把合适的主动消息升级成带图，以及每天最多生成几张。",
      keys: ["proactive_photo_text_probability", "photo_action_max_daily"],
    },
    {
      title: "后端选择",
      note: "本地 ComfyUI、SDGen 和在线图片 API 的优先关系。",
      keys: ["photo_generation_backend"],
    },
    {
      title: "本地 ComfyUI",
      note: "用于主动图片生成的工作流和等待时间。",
      keys: ["COMFYUI_TEXT2IMG_WORKFLOW_NAME", "COMFYUI_SELFIE_WORKFLOW_NAME", "photo_persona_reference_image_path", "comfyui_photo_wait_seconds"],
    },
    {
      title: "每日穿搭",
      note: "日程生成后额外生成一张角色当天穿搭照，用作拓展页左上角图像。",
      keys: ["enable_daily_outfit_photo", "daily_outfit_photo_prompt"],
    },
    {
      title: "自然语言生图/改图",
      note: "私聊明确请求时直接调用生图后端；默认关闭，避免和独立生图插件抢触发。",
      keys: ["enable_natural_language_photo_generation", "natural_language_photo_generation_max_daily", "natural_language_photo_extra_prompt"],
    },
    {
      title: "电脑负载保护",
      note: "电脑忙时抑制或延后本地生图，避免影响正在使用的机器。",
      keys: ["enable_local_photo_load_guard", "local_photo_cpu_busy_percent", "local_photo_memory_busy_percent", "local_photo_defer_minutes"],
    },
    {
      title: "在线图片 API",
      note: "作为 external 后端，或 auto 模式下本地忙时的备选后端。",
      keys: ["external_image_api_platform", "EXTERNAL_IMAGE_API_BASE_URL", "EXTERNAL_IMAGE_API_KEY", "EXTERNAL_IMAGE_API_MODEL", "external_image_api_size", "external_image_api_timeout_seconds", "external_image_api_custom_headers"],
    },
    {
      title: "备选在线图片 API",
      note: "主在线 API 失败后先尝试这组配置，再回退本地后端。",
      keys: ["enable_backup_external_image_api", "backup_external_image_api_platform", "BACKUP_EXTERNAL_IMAGE_API_BASE_URL", "BACKUP_EXTERNAL_IMAGE_API_KEY", "BACKUP_EXTERNAL_IMAGE_API_MODEL", "backup_external_image_api_size", "backup_external_image_api_timeout_seconds", "backup_external_image_api_custom_headers"],
    },
    {
      title: "画面风格",
      note: "只影响提示词组织，不改变后端配置。",
      keys: ["photo_generation_style", "photo_generation_style_custom_prompt", "photo_generation_fixed_prompt", "photo_generation_scene_presets"],
    },
  ],
  enable_tts_enhancement: [
    {
      title: "1. 生成路径",
      note: "两条主路径：快速标签追求低延迟，后处理判断+翻译追求稳定。",
      keys: ["tts_generation_mode", "tts_voice_language", "tts_conversion_provider_id", "tts_extra_prompt"],
    },
    {
      title: "2. 频率策略",
      note: "全局频控用默认概率和间隔控制双路径；私聊/群聊覆盖项填 -1 则继承默认值。",
      keys: ["tts_frequency_control_mode", "tts_constraint_mode", "tts_session_min_interval_seconds", "tts_trigger_probability", "tts_private_min_interval_seconds", "tts_private_trigger_probability", "tts_group_min_interval_seconds", "tts_group_trigger_probability"],
    },
    {
      title: "3. 快速标签自动语音",
      note: "仅快速标签模式使用：模型没写 <pc_tts>/<tts> 时，是否把普通短文本转成语音。",
      keys: ["auto_voice_enabled", "auto_voice_max_chars", "auto_voice_full_conversion_enabled"],
    },
    {
      title: "4. 旧版频率细项",
      note: "仅“旧版行为”使用：保留旧的快速标签自动语音概率、冷却和主用户强触发。",
      keys: ["auto_voice_probability", "auto_voice_cooldown_seconds", "main_user_voice_probability", "main_user_mention_voice_keywords", "main_user_mention_voice_probability", "main_user_mention_voice_prompt"],
    },
    {
      title: "5. 本机与直播联动",
      note: "TTS 音频生成后可在运行 AstrBot 的电脑播放，并同步推送到直播插件打字机字幕。",
      keys: ["enable_tts_local_playback", "enable_tts_local_playback_live_only", "tts_local_playback_volume", "tts_local_playback_min_interval_seconds", "enable_tts_live_subtitle_sync", "tts_live_subtitle_url"],
    },
  ],
};

const featureSettingTypes = {
  forward_message_mode: { type: "select", options: [["inject", "注入"], ["transcribe", "转述"]] },
  tts_generation_mode: { type: "select", options: [["fast_tag", "快速标签：主模型写私有标签"], ["postprocess", "后处理：判断+翻译模型"]] },
  tts_frequency_control_mode: { type: "select", options: [["global", "全局频控：间隔+概率控制双路径"], ["legacy", "旧版行为：按各路径原逻辑触发"]] },
  tts_constraint_mode: { type: "select", options: [["weak", "弱约束：提示词引导"], ["strong", "强约束：硬禁语音"]] },
  rest_reply_mode: { type: "select", options: [["probability", "仅概率醒来"], ["llm", "模型判断是否醒来"]] },
  rest_reply_awake_grace_minutes: { type: "number", min: 0, max: 240, step: 5 },
  passive_injection_position: { type: "select", options: [["prompt", "当前请求末尾"], ["system_prompt", "系统提示词"], ["auto", "自动（缓存优先）"]] },
  framework_session_lock_mode: { type: "select", options: [["auto", "自动（仅旧版兼容）"], ["off", "关闭（新版本推荐）"], ["always", "始终启用（旧版排障）"]] },
  expression_learning_mode: { type: "select", options: [["light", "轻量：只学节奏"], ["balanced", "标准：当前行为"], ["aggressive", "激进：参考审核样本"]] },
  response_review_mode: { type: "select", options: [["severe_only", "主动统一复核"], ["local_only", "仅本地识别并丢弃"], ["full", "含被动积极自检（延迟更高）"]] },
  proactive_intensity_preset: { type: "select", options: [["off", "关闭：手动参数"], ["balanced", "标准偏主动"], ["high_private", "私聊高频"], ["high_group", "群聊活跃"], ["live", "在线陪伴"]] },
  proactive_review_strength: { type: "select", options: [["lenient", "宽松：减少取消"], ["balanced", "标准：保留延后"], ["strict", "严格：按模型拦截"]] },
  emotion_judgement_mode: { type: "select", options: [["suspicious", "仅复核可疑项"], ["always", "总是复核普通文本"], ["off", "关闭复核"]] },
  smart_silence_min_confidence: { type: "number", min: 0, max: 100, step: 1 },
  smart_silence_model_timeout_seconds: { type: "number", min: 0.2, max: 5, step: 0.1 },
  group_high_intensity_merge_scope: { type: "select", options: [["group", "全群连续叫 Bot 合并"], ["same_user", "只合并同一发送者补话"]] },
  EMOTION_JUDGEMENT_PROVIDER_ID: { type: "provider" },
  SMART_SILENCE_PROVIDER_ID: { type: "provider" },
  PROACTIVE_PERSONA_JUDGE_PROVIDER_ID: { type: "provider" },
  reply_style_prompt: { type: "textarea" },
  proactive_prompt_template: { type: "textarea" },
  proactive_persona_judge_send_threshold: { type: "number", min: 0, max: 100, step: 1 },
  proactive_persona_judge_cache_minutes: { type: "number", min: 5, max: 720, step: 5 },
  natural_language_photo_generation_max_daily: { type: "number", min: 0, max: 100, step: 1 },
  quote_target_strategy: { type: "select", options: [["current", "引用当前触发消息"], ["quoted", "引用 Bot 被回复的旧消息"], ["auto", "自动：回复 Bot 旧消息时引用旧消息"]] },
  quote_skip_short_reply_chars: { type: "number", min: 0, max: 120, step: 1 },
  rest_backlog_max_messages: { type: "number", min: 1, max: 12, step: 1 },
  REST_WAKEUP_PROVIDER_ID: { type: "provider" },
  tts_voice_language: { type: "select", options: [["ja", "日语"], ["zh", "中文"], ["en", "英语"]] },
  tts_conversion_provider_id: { type: "provider" },
  tts_session_min_interval_seconds: { type: "number", min: 0, max: 3600, step: 1 },
  tts_private_min_interval_seconds: { type: "number", min: -1, max: 3600, step: 1 },
  tts_group_min_interval_seconds: { type: "number", min: -1, max: 3600, step: 1 },
  tts_private_trigger_probability: { type: "number", min: -1, max: 100, step: 1 },
  tts_group_trigger_probability: { type: "number", min: -1, max: 100, step: 1 },
  SMART_MESSAGE_DEBOUNCE_PROVIDER_ID: { type: "provider" },
  segmented_proactive_chat_scope: { type: "select", options: [["all", "全部"], ["private", "仅私聊"], ["group", "仅群聊"]] },
  photo_generation_backend: { type: "select", options: [["auto", "auto"], ["comfyui", "ComfyUI"], ["sdgen", "SDGen"], ["external", "在线图片 API"]] },
  external_image_api_platform: { type: "select", options: [["auto", "auto"], ["openai", "OpenAI 兼容"], ["bailian", "阿里云百炼"]] },
  backup_external_image_api_platform: { type: "select", options: [["auto", "auto"], ["openai", "OpenAI 兼容"], ["bailian", "阿里云百炼"]] },
  EXTERNAL_IMAGE_API_KEY: { type: "password" },
  BACKUP_EXTERNAL_IMAGE_API_KEY: { type: "password" },
  WEB_EXPLORATION_API_KEY: { type: "password" },
  photo_generation_style: { type: "select", options: [["真实", "真实"], ["二次元", "二次元"], ["其他", "其他"]] },
  segmented_proactive_scope: { type: "select", options: [["proactive_only", "仅插件主动"], ["all_llm", "全部 LLM 纯文本回复"]] },
  segmented_proactive_send_as_forward: { type: "checkbox" },
  segmented_proactive_split_mode: { type: "select", options: [["regex", "正则"], ["words", "分段词列表"]] },
  segmented_proactive_interval_method: { type: "select", options: [["log", "按字数对数"], ["random", "随机"]] },
  segmented_proactive_content_cleanup_scope: { type: "select", options: [["all", "全段清理"], ["trailing", "仅句尾清理"]] },
  recall_forbidden_scope: { type: "select", options: [["bot_and_group", "Bot 自己 + 群聊"], ["bot_only", "仅 Bot 自己"], ["group_only", "仅群聊"]] },
  atrelay_default_relay_style: { type: "select", options: [["persona", "语气转译"], ["soft", "委婉转述"], ["original", "原话模式"]] },
  worldbook_config_paths: { type: "textarea" },
  worldbook_self_registration_block_words: { type: "textarea" },
  private_user_aliases: { type: "textarea" },
  private_user_delivery_aliases: { type: "textarea" },
  tts_extra_prompt: { type: "textarea" },
  main_user_mention_voice_keywords: { type: "textarea" },
  main_user_mention_voice_prompt: { type: "textarea" },
  skill_growth_custom_skills: { type: "textarea" },
  QZONE_COOKIE: { type: "textarea" },
  news_sources: { type: "textarea" },
  news_hot_sources: { type: "textarea" },
  web_exploration_interests: { type: "textarea" },
  external_image_api_custom_headers: { type: "textarea" },
  backup_external_image_api_custom_headers: { type: "textarea" },
  group_wakeup_direct_words: { type: "textarea" },
  group_wakeup_context_words: { type: "textarea" },
  group_wakeup_interest_keywords: { type: "textarea" },
  recall_forbidden_words: { type: "textarea" },
  roleplay_user_profile_prompt: { type: "textarea" },
  private_image_self_recognition_hint: { type: "textarea" },
  daily_outfit_photo_prompt: { type: "textarea" },
  photo_generation_style_custom_prompt: { type: "textarea" },
  photo_generation_fixed_prompt: { type: "textarea" },
  natural_language_photo_extra_prompt: { type: "textarea" },
  photo_generation_scene_presets: { type: "textarea" },
  segmented_proactive_regex: { type: "textarea" },
  segmented_proactive_split_words: { type: "textarea" },
  segmented_proactive_content_cleanup_rule: { type: "textarea" },
  segmented_proactive_content_cleanup_words: { type: "textarea" },
  private_reading_default_keywords: { type: "textarea" },
  private_reading_blocked_tags: { type: "textarea" },
  group_repeat_trigger_threshold: { type: "number", min: 3, max: 20, step: 1 },
  group_wakeup_question_threshold: { type: "number", min: 0, max: 100, step: 1 },
  group_repeat_interrupt_text: { type: "text" },
  group_repeat_interrupt_image_path: { type: "text" },
  group_wakeup_cold_group_threshold: { type: "number", min: 0, max: 100, step: 1 },
};

const probabilitySettingKeys = new Set([
  "share_probability",
  "bilibili_share_probability",
  "news_share_probability",
  "external_event_self_link_probability",
  "web_exploration_share_probability",
  "qzone_life_publish_probability",
  "qzone_generated_image_probability",
  "qzone_emotional_vent_probability",
  "proactive_review_hard_risk_threshold",
  "proactive_review_low_score_threshold",
  "proactive_review_pressure_threshold",
  "private_reading_share_probability",
  "private_reading_ask_probability",
  "creative_inspiration_probability",
  "creative_share_probability",
  "skill_growth_schedule_influence_strength",
]);

function isFractionalPercentSetting(key) {
  return probabilitySettingKeys.has(key);
}

function isPercentInputSetting(key) {
  return isFractionalPercentSetting(key) || percentSettingKeys.has(key);
}

function displaySettingValue(key, value) {
  if (isPercentInputSetting(key)) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return value ?? "";
    if (numeric < 0) return String(numeric);
    const percent = numeric <= 1 ? numeric * 100 : numeric;
    return Number.isInteger(percent) ? String(percent) : String(Number(percent.toFixed(2)));
  }
  if (["private_user_aliases", "private_user_delivery_aliases"].includes(key) && value && typeof value === "object" && !Array.isArray(value)) {
    return Object.entries(value)
      .map(([alias, target]) => `${String(alias || "").trim()}=${String(target || "").trim()}`)
      .filter((line) => line !== "=")
      .join("\n");
  }
  return value ?? "";
}

function collectSettingValue(key, input) {
  if (!input) return "";
  if (input.type === "checkbox") return input.checked;
  if (input.type === "number") {
    const raw = input.value === "" ? 0 : Number(input.value);
    if (isFractionalPercentSetting(key)) {
      return Math.max(0, Math.min(1, raw / 100));
    }
    return raw;
  }
  return input.value;
}

const percentSettingKeys = new Set([
  "group_repeat_follow_probability",
  "group_repeat_interrupt_probability",
  "group_repeat_interrupt_probability_step",
  "group_wakeup_interest_probability",
  "group_wakeup_topic_interest_max_boost",
  "group_wakeup_debounce_pending_penalty",
  "tts_trigger_probability",
  "tts_private_trigger_probability",
  "tts_group_trigger_probability",
  "rest_reply_probability",
  "auto_voice_probability",
  "main_user_voice_probability",
  "main_user_mention_voice_probability",
  "proactive_photo_text_probability",
  "proactive_share_probability",
  "local_photo_cpu_busy_percent",
  "local_photo_memory_busy_percent",
]);

const presetCatalog = {
  safe: {
    label: "保守低打扰",
    tone: "calm",
    tagline: "少打扰，保留必要的陪伴感。",
    bestFor: "适合刚开始使用、担心主动消息太频繁，或希望先稳定观察一段时间。",
    rhythm: "主动消息更少，间隔更长。",
    cost: "低消耗",
    changes: ["降低每日主动次数", "关闭群聊主动插话", "保留记忆与表达学习"],
  },
  standard: {
    label: "标准陪伴",
    tone: "balanced",
    tagline: "日常使用的均衡模式。",
    bestFor: "适合大多数私聊陪伴场景，主动、记忆、群聊理解都不过度。",
    rhythm: "主动频率适中，私聊片段会正常沉淀。",
    cost: "中等消耗",
    changes: ["保持私聊学习", "开启片段与话头追踪", "群聊以理解上下文为主"],
  },
  active: {
    label: "高互动学习",
    tone: "warm",
    tagline: "更主动，也更愿意学习相处细节。",
    bestFor: "适合想让 Bot 更有存在感、愿意接受更高调用量的陪伴场景。",
    rhythm: "主动间隔更短，学习和复盘更频繁。",
    cost: "较高消耗",
    changes: ["提高主动消息上限", "加强表达和意图学习", "允许少量群聊插话"],
  },
  group_observer: {
    label: "群聊观察优先",
    tone: "group",
    tagline: "多看群聊，少主动打断。",
    bestFor: "适合群里信息量大、希望 Bot 更懂群内人物和梗，但不想频繁插话。",
    rhythm: "群聊记录更完整，默认不主动冒泡。",
    cost: "中等偏高",
    changes: ["强化群聊上下文", "学习黑话和话题线", "维护群成员与关系网"],
  },
};

const tokenTaskLabels = {
  daily_plan: "日程生成",
  detail: "日程细化",
  dream: "梦境内容",
  diary: "日记整理",
  memory_profile: "长期画像",
  dialogue_episode: "私聊片段",
  response_review: "回复/主动复核",
  emotion_judgement: "情绪判断",
  relationship: "关系分析",
  group_interject: "群聊插话",
  group_episode: "群聊片段",
  group_slang: "黑话释义",
  group_slang_learning: "群黑话学习",
  group_slang_meaning: "黑话释义",
  group_question_wakeup_reply_review: "群聊答疑复核",
  group_followup_judge: "群聊续接判断",
  worldbook_registration: "关系网自登记",
  web_exploration_query: "探索选题",
  web_exploration_digest: "探索笔记",
  external_event_self_link: "外界信息关联",
  news_digest: "新闻整理",
  creative_project: "创作立项",
  creative_outline: "创作大纲",
  creative_writing: "文本创作",
  creative_review: "创作审校",
  creative_extract: "创作抽取",
  photo_prompt: "生图提示",
  screen_narration: "识屏转述",
  forward_message: "合并转发转述",
  private_reading_vision: "夹层视觉",
  private_image_vision: "私聊图片识别",
  private_image_only_framework: "单图回复主链",
  private_image_only_fallback: "单图兜底回复",
  voice: "语音文本",
  proactive_framework: "主动主回复",
  proactive_persona_judge: "主动人格判定",
  voice_framework: "框架语音",
  voice_repair: "语音格式修复",
  smart_message_debounce: "智能收口防抖",
  rest_wakeup_judge: "休息醒来判断",
  yesterday_summary: "昨日摘要",
  full_test_detail: "完整测试细化",
  provider_test: "模型测试",
  qzone_comment: "空间评论",
  qzone_comment_inbox_decision: "空间评论判断",
  qzone_publish: "空间说说",
  qzone_publish_test: "空间发布测试",
  qzone_publish_sanitize: "空间文案清理",
  companion_manual_diagnosis: "陪伴答疑",
  memory_summary: "记忆阶段总结",
  memory_decay_summary: "记忆衰减整理",
  memory_rerank: "记忆重排",
  memory_embedding: "记忆向量索引",
  memory_embedding_query: "记忆向量查询",
  astrbot_private_reply: "非插件私聊主回复",
  astrbot_group_reply: "非插件群聊主回复",
  astrbot_reply: "非插件主回复",
  other: "其他调用",
};

const $ = (selector) => document.querySelector(selector);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function cleanInterjectionText(value) {
  const text = String(value ?? "").trim().replace(/^["'“”‘’` ]+|["'“”‘’` ]+$/g, "");
  if (!text || /^[.。…~～\s"'“”‘’`-]{0,12}$/.test(text)) return "";
  return text;
}

function bookshelfImageTag(src, alt) {
  const imageSrc = String(src || "");
  if (!imageSrc) return "";
  return `<img src="${TRANSPARENT_IMAGE}" data-bookshelf-image-src="${escapeHtml(imageSrc)}" alt="${escapeHtml(alt || "书柜图片")}" loading="lazy" />`;
}

function bookshelfImageDataPath(src) {
  const raw = String(src || "");
  if (!raw) return "";
  if (raw.startsWith("data:")) return raw;
  try {
    const url = new URL(raw, window.location.origin);
    if (url.pathname.endsWith("/bookshelf/image")) {
      return `/bookshelf/image_data${url.search}`;
    }
    const marker = "/bookshelf/image?";
    const markerIndex = raw.indexOf(marker);
    if (markerIndex >= 0) {
      return `/bookshelf/image_data?${raw.slice(markerIndex + marker.length)}`;
    }
  } catch (error) {
    const marker = "/bookshelf/image?";
    const markerIndex = raw.indexOf(marker);
    if (markerIndex >= 0) return `/bookshelf/image_data?${raw.slice(markerIndex + marker.length)}`;
  }
  return raw;
}

async function hydrateBookshelfImages(root = document) {
  const images = [...root.querySelectorAll("img[data-bookshelf-image-src]")];
  await Promise.all(images.map(async (img) => {
    if (img.dataset.loaded === "1" || img.dataset.loading === "1") return;
    const source = img.dataset.bookshelfImageSrc || "";
    if (!source) return;
    img.dataset.loading = "1";
    const endpoint = bookshelfImageDataPath(source);
    try {
      if (endpoint.startsWith("data:")) {
        img.src = endpoint;
      } else if (endpoint.startsWith("/bookshelf/image_data")) {
        const result = await fetchJson(endpoint);
        if (result?.data_url) img.src = result.data_url;
      } else {
        img.src = source;
      }
      img.dataset.loaded = "1";
    } catch (error) {
      img.dataset.loaded = "0";
      img.alt = `${img.alt || "书柜图片"}（加载失败）`;
    } finally {
      img.dataset.loading = "0";
    }
  }));
}

async function hydrateDailyOutfitLogo() {
  if (window.PrivateCompanionDailyOutfit?.hydrateDailyOutfitLogo) {
    await window.PrivateCompanionDailyOutfit.hydrateDailyOutfitLogo({ state, fetchJson, document });
  }
}

async function fetchJson(path, options = {}) {
  const bridge = await getPageBridge();
  const method = (options.method || "GET").toUpperCase();
  let payload;

  if (bridge && typeof bridge.apiGet === "function" && typeof bridge.apiPost === "function") {
    payload = await bridgeRequest(bridge, path, method, options.body);
  } else if (isDebugHttpMode()) {
    const response = await fetch(`${HTTP_API}${path}`, {
      cache: "no-store",
      headers: options.body ? { "Content-Type": "application/json" } : undefined,
      ...options,
    });
    const text = await response.text();
    try {
      payload = text ? JSON.parse(text) : {};
    } catch (error) {
      throw new Error(response.ok ? "返回内容不是 JSON" : `HTTP ${response.status}: ${text.slice(0, 120)}`);
    }
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  } else {
    throw new Error("未检测到 AstrBot 官方插件 Page 桥接，请从 AstrBot 后台的插件拓展页打开");
  }

  payload = normalizeResponse(payload);
  if (!payload.success) throw new Error(payload.error || "请求失败");
  return payload.data;
}

function getBridge() {
  if (window.AstrBotPluginPage) return window.AstrBotPluginPage;
  try {
    if (window.parent && window.parent !== window && window.parent.AstrBotPluginPage) {
      return window.parent.AstrBotPluginPage;
    }
  } catch (error) {
    return null;
  }
  return null;
}

function isDebugHttpMode() {
  return new URLSearchParams(window.location.search).get("debug_http") === "1";
}

function isUsableBridge(bridge) {
  return Boolean(bridge && typeof bridge.apiGet === "function" && typeof bridge.apiPost === "function");
}

async function getPageBridge(timeoutMs = 2500) {
  if (isUsableBridge(cachedPageBridge)) return cachedPageBridge;

  const bridge = getBridge();
  if (isUsableBridge(bridge)) {
    cachedPageBridge = bridge;
    return cachedPageBridge;
  }

  if (isDebugHttpMode()) return null;

  if (!pageBridgeProbePromise) {
    pageBridgeProbePromise = waitForBridge(timeoutMs)
      .then((resolvedBridge) => {
        if (isUsableBridge(resolvedBridge)) {
          cachedPageBridge = resolvedBridge;
          return cachedPageBridge;
        }
        return null;
      })
      .finally(() => {
        pageBridgeProbePromise = null;
      });
  }

  return pageBridgeProbePromise;
}

async function waitForBridge(timeoutMs = 2500) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const bridge = getBridge();
    if (bridge && typeof bridge.apiGet === "function" && typeof bridge.apiPost === "function") {
      return bridge;
    }
    await new Promise((resolve) => setTimeout(resolve, 80));
  }
  return getBridge();
}

async function bridgeRequest(bridge, path, method, body) {
  const url = new URL(path, "https://astrbot-plugin-page.local/");
  const routePath = url.pathname.replace(/^\/+/, "");
  const endpointCandidates = bridgeEndpointCandidates(routePath);
  const errors = [];

  if (method === "GET") {
    const params = Object.fromEntries(url.searchParams.entries());
    for (const candidate of endpointCandidates) {
      try {
        const payload = await bridge.apiGet(candidate.endpoint, Object.keys(params).length ? params : undefined);
        if (isRouteMissingPayload(payload)) {
          errors.push(payload.message || payload.error || "未找到该路由");
          continue;
        }
        cachedPageEndpointStyle = candidate.style;
        return payload;
      } catch (error) {
        if (isRouteMissingError(error)) {
          errors.push(error.message || String(error));
          continue;
        }
        throw error;
      }
    }
    throw new Error(errors[0] || "未找到可用的页面 API 路由");
  }

  let payload = body || {};
  if (typeof payload === "string") {
    try {
      payload = JSON.parse(payload);
    } catch (error) {
      payload = {};
    }
  }
  for (const candidate of endpointCandidates) {
    try {
      const result = await bridge.apiPost(candidate.endpoint, payload);
      if (isRouteMissingPayload(result)) {
        errors.push(result.message || result.error || "未找到该路由");
        continue;
      }
      cachedPageEndpointStyle = candidate.style;
      return result;
    } catch (error) {
      if (isRouteMissingError(error)) {
        errors.push(error.message || String(error));
        continue;
      }
      throw error;
    }
  }
  throw new Error(errors[0] || "未找到可用的页面 API 路由");
}

function normalizeResponse(payload) {
  if (payload && typeof payload === "object" && Object.prototype.hasOwnProperty.call(payload, "success")) {
    return payload;
  }
  if (payload && typeof payload === "object") {
    const status = String(payload.status || "").trim().toLowerCase();
    if (["error", "fail", "failed"].includes(status) || payload.ok === false) {
      return {
        success: false,
        error: payload.message || payload.error || "请求失败",
        data: payload.data || {},
      };
    }
  }
  return { success: true, data: payload };
}

function bridgeEndpointCandidates(routePath) {
  const cleanRoute = String(routePath || "").replace(/^\/+/, "");
  const byStyle = {
    cached: cachedPageEndpointStyle ? endpointForStyle(cachedPageEndpointStyle, cleanRoute) : "",
    page: `${PAGE_ENDPOINT_PREFIX}/${cleanRoute}`,
    bare: cleanRoute,
    slash: `/${cleanRoute}`,
    full: `${PAGE_PLUGIN_NAME}/${PAGE_ENDPOINT_PREFIX}/${cleanRoute}`,
    fullSlash: `/${PAGE_PLUGIN_NAME}/${PAGE_ENDPOINT_PREFIX}/${cleanRoute}`,
  };
  const ordered = [
    ["cached", byStyle.cached],
    ["page", byStyle.page],
    ["bare", byStyle.bare],
    ["slash", byStyle.slash],
    ["full", byStyle.full],
    ["fullSlash", byStyle.fullSlash],
  ];
  const seen = new Set();
  return ordered
    .map(([style, endpoint]) => ({ style: style === "cached" ? cachedPageEndpointStyle : style, endpoint: String(endpoint || "").replace(/\/+/g, "/") }))
    .filter((item) => item.style && item.endpoint && !seen.has(item.endpoint) && seen.add(item.endpoint));
}

function endpointForStyle(style, routePath) {
  const cleanRoute = String(routePath || "").replace(/^\/+/, "");
  switch (style) {
    case "page":
      return `${PAGE_ENDPOINT_PREFIX}/${cleanRoute}`;
    case "bare":
      return cleanRoute;
    case "slash":
      return `/${cleanRoute}`;
    case "full":
      return `${PAGE_PLUGIN_NAME}/${PAGE_ENDPOINT_PREFIX}/${cleanRoute}`;
    case "fullSlash":
      return `/${PAGE_PLUGIN_NAME}/${PAGE_ENDPOINT_PREFIX}/${cleanRoute}`;
    default:
      return "";
  }
}

function isRouteMissingPayload(payload) {
  if (!payload || typeof payload !== "object") return false;
  const text = `${payload.error || ""} ${payload.message || ""} ${payload.detail || ""}`.toLowerCase();
  return /未找到.*路由|route.*not.*found|not.*found.*route|404/.test(text);
}

function isRouteMissingError(error) {
  const text = String(error?.message || error || "").toLowerCase();
  return /未找到.*路由|route.*not.*found|not.*found.*route|http\s*404|\b404\b/.test(text);
}

function postJson(path, body) {
  return fetchJson(path, { method: "POST", body: JSON.stringify(body) });
}

function downloadJson(filename, data) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function unwrapConfigPackagePayload(value) {
  let current = value;
  for (let index = 0; index < 8; index += 1) {
    if (!current || typeof current !== "object" || Array.isArray(current)) return current;
    if (current.kind === "private_companion_config_backup" || (current.overview && (current.users || current.groups))) return current;
    const next = current.package || current.data || current.payload || current.result;
    if (!next || typeof next !== "object" || Array.isArray(next) || next === current) return current;
    current = next;
  }
  return current;
}

function formatBackupTime(value) {
  const timestamp = Number(value || 0);
  if (!timestamp) return "未知时间";
  return new Date(timestamp * 1000).toLocaleString();
}

function selectedConfigExportSections() {
  const sections = [...document.querySelectorAll("[data-export-section]")]
    .filter((input) => input.checked)
    .map((input) => input.dataset.exportSection)
    .filter(Boolean);
  return sections.length ? sections : ["basic", "relations", "food_skills"];
}

function allowConfigChecksumMismatch() {
  return Boolean($("#configAllowChecksumMismatch")?.checked);
}

function migrationDiffText(item) {
  const added = Number(item?.added || 0);
  const overwritten = Number(item?.overwritten || 0);
  const unchanged = Number(item?.unchanged || 0);
  return `新增 ${added} · 覆盖 ${overwritten} · 不变 ${unchanged}`;
}

function migrationSectionLabel(value) {
  return {
    basic: "基础配置",
    relations: "关系/群资料",
    food_skills: "候选菜单/技能",
    providers: "模型配置",
    sensitive: "敏感配置",
  }[value] || value;
}

function migrationLevelText(level) {
  return {
    ok: "正常",
    warn: "注意",
    error: "错误",
    unknown: "未知",
  }[level] || "提示";
}

function renderConfigBackups() {
  const box = $("#configBackupList");
  if (!box) return;
  const items = Array.isArray(state.configBackups) ? state.configBackups : [];
  if (!items.length) {
    box.innerHTML = "暂无自动备份。";
    return;
  }
  box.innerHTML = items.map((item) => {
    const sections = Array.isArray(item.included_sections) && item.included_sections.length
      ? item.included_sections.map(migrationSectionLabel).join("、")
      : "旧备份";
    const checksum = item.checksum_ok ? "校验通过" : "未校验";
    return `
      <div class="migration-backup-item">
        <div>
          <b>${escapeHtml(item.name || item.id)}</b>
          <span>${escapeHtml(formatBackupTime(item.exported_at || item.mtime))} · ${escapeHtml(item.version || "未知版本")} · ${escapeHtml(checksum)}</span>
          <small>${escapeHtml(sections)}</small>
        </div>
        <button type="button" data-config-restore="${escapeHtml(item.id)}">恢复</button>
      </div>
    `;
  }).join("");
}

function renderConfigImportChecks() {
  const box = $("#configImportChecks");
  if (!box) return;
  const checks = Array.isArray(state.configLastChecks) ? state.configLastChecks : [];
  if (!checks.length) {
    box.innerHTML = "导入后会在这里显示轻量检查结果。";
    return;
  }
  box.innerHTML = checks.map((item) => `
    <div class="migration-check-item ${escapeHtml(item.level || "ok")}">
      <b>${escapeHtml(item.title || migrationLevelText(item.level))}</b>
      <span>${escapeHtml(item.detail || "")}</span>
    </div>
  `).join("");
}

function renderConfigMigrationPreview() {
  const box = $("#configMigrationPreview");
  if (!box) return;
  const modeSelect = $("#configImportMode");
  const conflictSelect = $("#configImportConflict");
  if (conflictSelect) {
    conflictSelect.disabled = (modeSelect?.value || "merge") === "replace";
  }
  const preview = state.configImportPreview;
  if (!state.configImportPackage) {
    box.innerHTML = "还没有选择备份或快照文件。";
    $("#previewConfigImportBtn").disabled = true;
    $("#applyConfigImportBtn").disabled = true;
    return;
  }
  $("#previewConfigImportBtn").disabled = false;
  $("#applyConfigImportBtn").disabled = !preview;
  if (!preview) {
    box.innerHTML = "已选择备份/快照文件，先点“预览导入”看看会改动哪些内容。";
    return;
  }
  const sections = Array.isArray(preview.sections) ? preview.sections : [];
  if (preview.legacy_snapshot && modeSelect && modeSelect.value === "replace") {
    modeSelect.value = "merge";
    if (conflictSelect) conflictSelect.disabled = false;
  }
  const sectionHtml = sections.length
    ? sections.map((item) => `
        <span>
          <b>${escapeHtml(item.label || item.key)}</b>
          ${Number(item.count || 0)} 条
          <small>${escapeHtml(migrationDiffText(item))}</small>
        </span>
      `).join("")
    : "<em>没有可迁移资料段</em>";
  const configDiff = preview.config_diff || {};
  const compatibility = preview.compatibility || {};
  const checksumLine = preview.checksum
    ? (preview.checksum_ok ? "文件校验：通过" : (preview.checksum_bypassed ? "文件校验：失败，已按风险选项继续" : "文件校验：失败"))
    : "文件校验：旧备份未提供";
  const checksumWarn = preview.checksum_bypassed
    ? `<p class="migration-warn">这份备份的校验值与当前内容不一致。仅当你确认文件只是被手动脱敏或删改敏感字段、且 JSON 内容完整时，才继续导入。</p>`
    : "";
  const compatibilityHtml = `
    <p class="migration-note">
      备份来自 ${escapeHtml(compatibility.backup_version || preview.version || "未知")}，当前版本 ${escapeHtml(compatibility.current_version || preview.current_version || "未知")}。
      ${escapeHtml(compatibility.message || "")}
    </p>
    <p class="${compatibility.level === "warn" || preview.checksum_bypassed ? "migration-warn" : "migration-note"}">${escapeHtml(checksumLine)}</p>
    ${checksumWarn}
  `;
  const included = Array.isArray(preview.included_sections) && preview.included_sections.length
    ? `<p class="migration-note">备份包含：${escapeHtml(preview.included_sections.map(migrationSectionLabel).join("、"))}</p>`
    : "";
  const legacy = preview.legacy_snapshot
    ? `<p class="migration-warn">这是旧版页面快照，只会按合并方式导入可识别的配置、名单、开关和模型指向；页面里的私聊/群聊摘要不会写回数据文件。</p>`
    : "";
  const ignored = Array.isArray(preview.ignored) && preview.ignored.length
    ? `<p class="migration-warn">已忽略 ${preview.ignored.length} 个未知字段：${escapeHtml(preview.ignored.slice(0, 8).join("、"))}</p>`
    : "";
  box.innerHTML = `
    <div class="migration-preview-top">
      <b>备份版本 ${escapeHtml(preview.version || "未知")}</b>
      <span>${escapeHtml(formatBackupTime(preview.exported_at))}</span>
    </div>
    <div class="migration-counts">
      <span>配置 ${Number(preview.config_count || 0)} 项 <small>${escapeHtml(migrationDiffText(configDiff))}</small></span>
      <span>开关 ${Number(preview.features_count || 0)} 项</span>
      <span>模型 ${Number(preview.providers_count || 0)} 项</span>
    </div>
    <div class="migration-section-list">${sectionHtml}</div>
    ${compatibilityHtml}
    ${included}
    ${legacy}
    ${ignored}
    <p class="migration-note">导入前会自动保存当前可迁移配置；Token、缓存、最近消息、审计日志和临时队列不会导入。</p>
  `;
}

async function handleConfigExport() {
  const params = new URLSearchParams();
  params.set("sections", selectedConfigExportSections().join(","));
  const data = await fetchJson(`/config/export?${params.toString()}`);
  const date = new Date().toISOString().slice(0, 10);
  downloadJson(`private-companion-config-${date}.json`, data);
  showToast("配置备份已导出");
}

async function readConfigImportFile(file) {
  const text = await file.text();
  let data = null;
  try {
    data = JSON.parse(text.replace(/^\uFEFF/, ""));
  } catch (error) {
    throw new Error("备份文件不是有效 JSON");
  }
  state.configImportPackage = unwrapConfigPackagePayload(data);
  state.configImportPreview = null;
  renderConfigMigrationPreview();
  showToast("备份/快照文件已读取，请先预览");
}

async function previewConfigImport() {
  if (!state.configImportPackage) {
    showToast("请先选择备份文件", "error");
    return;
  }
  const result = await postJson("/config/import/preview", {
    package: state.configImportPackage,
    allow_checksum_mismatch: allowConfigChecksumMismatch(),
  });
  state.configImportPreview = result;
  renderConfigMigrationPreview();
  showToast("预览完成");
}

async function applyConfigImport() {
  if (!state.configImportPackage || !state.configImportPreview) {
    showToast("请先预览备份内容", "error");
    return;
  }
  const mode = state.configImportPreview?.legacy_snapshot ? "merge" : ($("#configImportMode")?.value || "merge");
  const conflict = $("#configImportConflict")?.value || "use_backup";
  const conflictText = {
    use_backup: "使用备份内容",
    keep_current: "保留当前内容",
    fill_empty: "只补当前空字段",
  }[conflict] || "使用备份内容";
  const message = mode === "replace"
    ? "将覆盖可迁移资料段。已确认要继续吗？"
    : `将合并导入备份内容，字段冲突时：${conflictText}。已确认要继续吗？`;
  if (state.configImportPreview?.checksum_bypassed && !allowConfigChecksumMismatch()) {
    showToast("这份备份校验失败，确认导入前需要保持风险选项勾选", "error");
    return;
  }
  const finalMessage = state.configImportPreview?.checksum_bypassed
    ? `${message}\n\n注意：这份备份校验失败，只建议在确认它只是被手动脱敏/删改敏感字段时继续。`
    : message;
  if (!window.confirm(finalMessage)) return;
  const result = await postJson("/config/import/apply", {
    package: state.configImportPackage,
    mode,
    conflict,
    allow_checksum_mismatch: allowConfigChecksumMismatch(),
  });
  state.configImportPreview = null;
  renderConfigMigrationPreview();
  if (result) {
    const requestSeq = ++loadAllRequestSeq;
    state.overview = result;
    state.configBackups = result.migration_backups || state.configBackups;
    state.configLastChecks = result.post_import_checks || [];
    renderAll();
    void loadUserGroupLists(requestSeq);
  }
  showToast("配置已导入，已自动备份导入前状态");
}

async function restoreConfigBackup(id) {
  const backupId = String(id || "").trim();
  if (!backupId) return;
  if (!window.confirm(`将从自动备份恢复：${backupId}\n恢复前也会再次备份当前状态。继续吗？`)) return;
  const result = await postJson("/config/restore", { id: backupId });
  if (result) {
    const requestSeq = ++loadAllRequestSeq;
    state.overview = result;
    state.configBackups = result.migration_backups || state.configBackups;
    state.configLastChecks = result.post_import_checks || [];
    renderAll();
    void loadUserGroupLists(requestSeq);
  }
  showToast("已从备份恢复");
}

async function loadImageCache() {
  const params = new URLSearchParams();
  params.set("limit", "300");
  if (state.imageCacheFilter) params.set("q", state.imageCacheFilter);
  if (state.imageCacheScope && state.imageCacheScope !== "all") params.set("scope", state.imageCacheScope);
  const data = await fetchJson(`/image_cache/list?${params.toString()}`);
  state.imageCacheItems = Array.isArray(data.items) ? data.items : [];
  state.imageCacheTotal = Number(data.total || 0);
  state.imageCacheScopes = Array.isArray(data.scopes) ? data.scopes : [];
  state.imageCacheLoaded = true;
  if (state.selectedImageCacheKey && !state.imageCacheItems.some((item) => item.key === state.selectedImageCacheKey)) {
    state.selectedImageCacheKey = "";
  }
  if (!state.selectedImageCacheKey && state.imageCacheItems[0]) {
    state.selectedImageCacheKey = state.imageCacheItems[0].key;
  }
  renderImageCache();
  return data;
}

async function loadTroubleshooting() {
  const [data, overview] = await Promise.all([
    fetchJson("/troubleshooting"),
    fetchJson("/overview").catch(() => null),
  ]);
  state.troubleshooting = data || null;
  if (overview) {
    state.overview = overview;
    state.featureDraft = featureDraftFromOverview(overview);
  }
  renderTroubleshooting();
  return data;
}

function applyOverviewData(overview) {
  state.overview = overview;
  hydrateTokenStatsFromOverview(overview);
  state.featureDraft = featureDraftFromOverview(overview);
  state.providerConfigMode = inferProviderConfigMode(overview);
  state.pageFontFamily = String(overview?.settings?.page_font_family || "original").trim().toLowerCase() === "cheng" ? "cheng" : "original";
  state.pageTheme = normalizePageTheme(overview?.settings?.page_theme);
  applyPageFontFamily();
  applyPageTheme();
}

function applyPageFontFamily() {
  const font = state.pageFontFamily === "cheng" ? "cheng" : "original";
  document.documentElement.dataset.pageFont = font;
  try { localStorage.setItem("pc_font", font); } catch (e) {}
  const appearanceSelect = document.getElementById("appearanceFontSelect");
  if (appearanceSelect) appearanceSelect.value = font;
}

const PAGE_THEMES = [
  { value: "classic", label: "经典蓝", swatch: ["#eef3fb", "#1a4b8c", "#2c6cb0"] },
  { value: "dark", label: "暗夜深空", swatch: ["#0d1117", "#58a6ff", "#1c2330"] },
  { value: "warm", label: "暖阳书房", swatch: ["#faf5ef", "#b86b2e", "#c0793c"] },
  { value: "forest", label: "森林绿径", swatch: ["#eef4ee", "#2d6a4f", "#40916c"] },
  { value: "sakura", label: "樱粉日记", swatch: ["#fdf0f3", "#d4517a", "#fce8ed"] },
  { value: "ocean", label: "深海渐变", swatch: ["#0a1929", "#00b4d8", "#0077b6"] },
  { value: "lavender", label: "薰衣草", swatch: ["#f5f0fa", "#7c5cbf", "#a78bda"] },
  { value: "ink", label: "水墨黑白", swatch: ["#f8f8f6", "#1a1a1a", "#4a4a4a"] },
  { value: "sunset", label: "日落晚霞", swatch: ["#fff4e6", "#e85d3c", "#f4a261"] },
];

function normalizePageTheme(raw) {
  const allowed = PAGE_THEMES.map((t) => t.value);
  const text = String(raw || "classic").trim().toLowerCase();
  return allowed.includes(text) ? text : "classic";
}

function applyPageTheme() {
  const theme = normalizePageTheme(state.pageTheme);
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("pc_theme", theme); } catch (e) {}
}

function renderAppearanceSettings() {
  const current = normalizePageTheme(state.pageTheme);
  const grid = document.getElementById("appearanceThemeGrid");
  if (grid) {
    grid.innerHTML = PAGE_THEMES.map((t) => {
      const swatches = t.swatch.map((c) => `<span style="background:${c}"></span>`).join("");
      return `<div class="appearance-theme-option${t.value === current ? " is-active" : ""}" data-theme-value="${t.value}" role="radio" aria-checked="${t.value === current}" tabindex="0">
        <div class="appearance-theme-swatch">${swatches}</div>
        <div class="appearance-theme-label"><span>${escapeHtml(t.label)}</span></div>
      </div>`;
    }).join("");
  }
}

function applyUserGroupLists(users, groups) {
  state.users = Array.isArray(users?.items) ? users.items : [];
  state.groups = Array.isArray(groups?.items) ? groups.items : [];
  if (!state.selectedUserId && state.users[0]) state.selectedUserId = state.users[0].user_id;
  if (!state.selectedGroupId && state.groups[0]) state.selectedGroupId = state.groups[0].group_id;
}

async function loadUserGroupLists(requestSeq = loadAllRequestSeq, options = {}) {
  const { showErrors = false } = options;
  try {
    const [users, groups] = await Promise.all([
      fetchJson("/users?limit=300"),
      fetchJson("/groups?limit=300"),
    ]);
    if (requestSeq !== loadAllRequestSeq) return null;
    applyUserGroupLists(users, groups);
    renderAll();
    const overview = state.overview || {};
    $("#subtitle").textContent = `${overview.plugin?.bot_name || "Private Companion"} · ${new Date().toLocaleString()}`;
    return { users, groups };
  } catch (error) {
    if (requestSeq !== loadAllRequestSeq) return null;
    console.warn("[PrivateCompanionPage] 用户/群聊列表加载失败", error);
    const overview = state.overview || {};
    $("#subtitle").textContent = `${overview.plugin?.bot_name || "Private Companion"} · 总览已加载，名单加载失败`;
    if (showErrors) showToast(`名单加载失败：${error.message}`, "error");
    return null;
  }
}

async function loadAll(options = {}) {
  const requestSeq = ++loadAllRequestSeq;
  const { waitForLists = false } = options;
  $("#subtitle").textContent = "读取运行态中...";
  try {
    const overview = await fetchJson("/overview");
    if (requestSeq !== loadAllRequestSeq) return;
    applyOverviewData(overview);
    renderAll();
    void ensureTabData(state.activeTab, true);
    $("#subtitle").textContent = `${overview.plugin?.bot_name || "Private Companion"} · 总览已加载，正在补全名单...`;
    const listPromise = loadUserGroupLists(requestSeq, { showErrors: true });
    if (waitForLists) await listPromise;
  } catch (error) {
    if (requestSeq !== loadAllRequestSeq) return;
    $("#subtitle").textContent = `加载失败：${error.message}`;
  }
}

function hydrateTokenStatsFromOverview(overview) {
  const tokenStats = overview?.token_stats;
  if (!tokenStats || typeof tokenStats !== "object") return;
  state.tokenStats = tokenStats;
  state.tokenStatsPartial = true;
  state.lazyLoaded.tokenStats = false;
}

function renderAll() {
  hydrateDailyOutfitLogo();
  renderStats();
  renderDashboard();
  renderActiveTab(state.activeTab);
}

function renderActiveTab(tabName = state.activeTab || "dashboard") {
  if (tabName === "private") {
    renderUsers();
  } else if (tabName === "group") {
    renderGroups();
  } else if (tabName === "worldbook") {
    renderWorldbook();
  } else if (tabName === "memory") {
    renderMemory();
  } else if (tabName === "proactive") {
    renderProactiveCandidates();
  } else if (tabName === "bookshelf") {
    renderBookshelf();
  } else if (tabName === "qzone") {
    if (window.PrivateCompanionQzonePanel?.render) {
      window.PrivateCompanionQzonePanel.render({ fetchJson, postJson, showToast, escapeHtml });
    }
  } else if (tabName === "image-cache") {
    renderImageCache();
  } else if (tabName === "troubleshooting") {
    renderTroubleshooting();
  } else if (tabName === "tokens") {
    renderTokens();
  } else if (tabName === "roleplay" || tabName === "modules") {
    renderModuleSettings();
    renderRoleplayPersonaDraftPanel();
  } else if (tabName === "config") {
    renderConfig();
  } else if (tabName === "models") {
    renderProviders();
  }
}

async function loadDiagnostics(force = false) {
  if (state.lazyLoaded.diagnostics && !force) return state.diagnostics;
  const diagnostics = await fetchJson("/diagnostics");
  state.diagnostics = diagnostics.items || [];
  state.lazyLoaded.diagnostics = true;
  if (state.activeTab === "troubleshooting") {
    renderDiagnostics();
  }
  renderDashboardPulse();
  return state.diagnostics;
}

async function loadTokenStats(force = false) {
  if (state.lazyLoaded.tokenStats && !force && !state.tokenStatsPartial) return state.tokenStats;
  const tokenStats = await fetchJson("/token/stats");
  state.tokenStats = tokenStats || null;
  state.tokenStatsPartial = false;
  state.lazyLoaded.tokenStats = true;
  renderStats();
  if (state.activeTab === "tokens") renderTokens();
  if (state.activeTab === "dashboard") renderDashboardPulse();
  return state.tokenStats;
}

async function loadAvailableProviders(force = false) {
  if (state.lazyLoaded.providers && !force) return state.availableProviders;
  const availableProviders = await fetchJson("/providers/available");
  state.availableProviders = availableProviders.items || [];
  state.lazyLoaded.providers = true;
  if (state.activeTab === "models") renderProviders();
  if (state.activeTab === "modules" || state.activeTab === "roleplay") renderModuleSettings();
  return state.availableProviders;
}

async function loadConfigBackups(force = false) {
  if (state.lazyLoaded.configBackups && !force) return state.configBackups;
  const configBackups = await fetchJson("/config/backups").catch(() => ({ items: [] }));
  state.configBackups = configBackups.items || [];
  state.lazyLoaded.configBackups = true;
  if (state.activeTab === "config") renderConfigBackups();
  return state.configBackups;
}

async function ensureTabData(tabName, force = false) {
  if (tabName === "dashboard") {
    return;
  }
  if (tabName === "tokens") {
    await loadTokenStats(force);
  } else if (tabName === "models") {
    await loadAvailableProviders(force);
  } else if (tabName === "modules" || tabName === "roleplay") {
    loadAvailableProviders(force).catch(() => {});
  } else if (tabName === "config") {
    await loadConfigBackups(force);
  } else if (tabName === "image-cache") {
    renderImageCache();
    await loadImageCache();
  } else if (tabName === "troubleshooting") {
    renderTroubleshooting();
    loadDiagnostics(force).catch(() => {});
    await loadTroubleshooting();
  }
}

function renderStats() {
  const overview = state.overview || {};
  const privateInfo = overview.private || {};
  const groupInfo = overview.group || {};
  const daily = overview.daily_state || {};
  const budget = state.tokenStats?.budget || {};
  const dailyUsed = Number(budget.used || 0);
  const dailyLimit = Number(budget.limit || 0);
  const featureKeys = visibleTopLevelFeatureKeys(overview.features || {});
  const enabledFeatures = featureKeys.filter((key) => overview.features?.[key]).length;
  const energyLabel = roleplayEnergyLabel(daily.energy);
  const energyNumber = daily.energy === undefined || daily.energy === "" ? "" : `${formatNumber(Number(daily.energy || 0))}/100`;
  const mood = normalizeRoleplayStateText(daily.mood_bias) || daily.note || "暂无状态";
  $("#stats").innerHTML = [
    statCard(`私聊 ${privateInfo.enabled_user_count || 0} · 群聊 ${groupInfo.enabled_group_count || 0}`, `总数：对象 ${privateInfo.user_count || 0} · 群聊 ${groupInfo.group_count || 0}`, "private"),
    statCard(dailyLimit > 0 ? `${formatCompactNumber(dailyUsed)} / ${formatCompactNumber(dailyLimit)}` : `${formatCompactNumber(dailyUsed)} / 不限`, "今日 Token 消耗 / 上限", "tokens"),
    statCard(energyLabel || "-", `心理能量${energyNumber ? ` ${energyNumber}` : ""} · ${mood}`, "memory"),
    statCard(`${enabledFeatures}/${featureKeys.length}`, "开启主开关 / 主开关总数", "config"),
  ].join("");
}

function statCard(value, label, jumpTab = "") {
  return `<article class="stat ${jumpTab ? "is-link" : ""}" ${jumpTab ? `data-jump-tab="${escapeHtml(jumpTab)}"` : ""}><b>${escapeHtml(value)}</b><span>${escapeHtml(label)}</span></article>`;
}

function renderDashboard() {
  renderDashboardPulse();
  renderStrategyOverview();
  renderHealthPanel();
  renderUxReviewPanel();
  renderRelationshipChart();
  renderGroupBubbleChart();
  renderQuotaChart();
  renderFeatureMatrix();
  renderNewsInsightPanel();
  renderWebExplorationPanel();
  renderActivityHeatmap();
}

function renderStrategyOverview() {
  const overview = state.overview || {};
  renderPrivateStrategyOverview("#privateConfig", overview.private || {});
  renderGroupStrategyOverview("#groupConfig", overview.group || {});
  renderLongTermStrategyOverview("#longTermConfig", {
    creative: overview.creative || {},
    bili: overview.bilibili || {},
    qzone: overview.qzone || {},
    privateReading: overview.private_reading || {},
  });
}

function renderDashboardPulse() {
  const overview = state.overview || {};
  const daily = overview.daily_state || {};
  const life = overview.life_observation || {};
  const current = life.current_plan || {};
  const proactive = overview.proactive_candidates || {};
  const proactiveCounts = proactive.counts || {};
  const news = overview.news || {};
  const exploration = overview.web_exploration || {};
  const creative = overview.creative || {};
  const bookshelf = state.bookshelfUnlocked || overview.bookshelf || {};
  const nextUser = state.users
    .filter((item) => Number(item.next_proactive_ts || 0) > 0)
    .sort((a, b) => Number(a.next_proactive_ts || 0) - Number(b.next_proactive_ts || 0))[0];
  const newsTitle = news.last_digest?.headline || news.last_digest?.topic || "";
  const explorationTitle = exploration.last_digest?.topic || exploration.last_query?.query || "";
  const bookshelfNote = [
    creative.latest_title || "",
    bookshelf.secret_count ? `夹层 ${bookshelf.secret_count}` : "",
  ].filter(Boolean).join(" · ");
  const cards = [
    {
      tone: "life",
      layout: "wide tall",
      label: "此刻状态",
      value: current.activity || "暂无当前日程",
      note: [current.time, current.mood, current.message_seed].filter(Boolean).join(" · ") || daily.note || "暂无细化",
      jump: "memory",
    },
    {
      tone: "proactive",
      layout: "wide compact",
      label: "下一次主动",
      value: nextUser ? (nextUser.nickname || nextUser.user_id) : "暂无计划",
      note: nextUser ? `${nextUser.next_proactive} · ${proactiveActionLabel(nextUser.planned_action || "message")}` : `${proactiveCounts.accepted || 0} 个候选已进入计划`,
      jump: "proactive",
    },
    {
      tone: "news",
      layout: "wide tall",
      label: "今日见闻",
      value: newsTitle || explorationTitle || "暂无记录",
      note: newsTitle && explorationTitle ? `搜索：${explorationTitle}` : (news.last_read_at || exploration.last_explore_at || "新闻阅读和主动搜索会在这里留下痕迹"),
      jump: "bookshelf",
    },
    {
      tone: "bookcase",
      layout: "wide compact",
      label: "书柜与长线",
      value: creative.latest_title || "暂无新书",
      note: bookshelfNote || `${creative.active_projects || 0} 个创作进行中`,
      jump: "bookshelf",
    },
  ];
  $("#dashboardPulse").innerHTML = cards.map((card) => `
    <button type="button" class="pulse-card ${escapeHtml(card.tone)} ${escapeHtml(card.layout || "")}" data-pulse-kind="${escapeHtml(card.tone)}" data-jump-tab="${escapeHtml(card.jump)}">
      <span>${escapeHtml(card.label)}</span>
      <b>${escapeHtml(card.value)}</b>
      <small>${escapeHtml(card.note)}</small>
    </button>
  `).join("");

  const shortcuts = [
    ["group", "群聊观测", `${overview.group?.enabled_group_count || 0}/${overview.group?.group_count || 0} 个群`],
    ["worldbook", "关系网", `${overview.worldbook?.enabled_member_count || 0}/${overview.worldbook?.member_count || 0} 个节点`],
    [
      "tokens",
      "Token",
      state.tokenStats
        ? `${formatCompactNumber(state.tokenStats?.totals?.total_tokens || 0)} · ${formatCompactNumber(state.tokenStats?.totals?.calls || 0)} 次`
        : "后台加载中",
    ],
    [
      "troubleshooting",
      "排障中心",
      state.lazyLoaded.diagnostics
        ? `${(state.diagnostics || []).filter((item) => ["warn", "error"].includes(item.level)).length} 个诊断项`
        : "去排障页查看",
    ],
    ["image-cache", "图片缓存", `${overview.cache?.private_image_vision?.items || 0}/${overview.cache?.private_image_vision?.max_items || "不限"} 条`],
    ["modules", "模块工作台", moduleShortcutNote(overview)],
    ["models", "模型分流", providerShortcutNote(overview.providers || {})],
    ["config", "名单与开关", `${overview.group?.access_mode || "whitelist"} · 白 ${overview.group?.whitelist?.length || 0} / 黑 ${overview.group?.blacklist?.length || 0}`],
  ];
  $("#dashboardShortcuts").innerHTML = shortcuts.map(([tab, label, note]) => `
    <button type="button" class="shortcut-chip" data-jump-tab="${escapeHtml(tab)}">
      <b>${escapeHtml(label)}</b>
      <span>${escapeHtml(note)}</span>
    </button>
  `).join("");
}

function formatCompactNumber(value) {
  const number = Number(value || 0);
  if (number >= 1000000) return `${(number / 1000000).toFixed(number >= 10000000 ? 0 : 1)}M`;
  if (number >= 1000) return `${(number / 1000).toFixed(number >= 10000 ? 0 : 1)}K`;
  return String(Math.round(number));
}

function insightStatus(value) {
  const text = String(value || "").trim();
  const labels = {
    read: "已阅读",
    explored: "已搜索",
    no_items: "暂无候选",
    no_results: "无结果",
    search_failed: "搜索失败",
    digest_failed: "整理失败",
    web_search_disabled_or_unconfigured: "搜索未配置",
    waiting_schedule: "等待定时",
    all_sources_done: "今日来源已处理",
    waiting_window: "等待窗口",
    checking: "正在检查",
    waiting_today_video: "等待今日视频",
    today_video_without_text: "今日视频暂无文字版",
    already_read_today_video: "今日已读",
    missed_today_ai_daily: "今日窗口已过",
  };
  return labels[text] || text || "暂无";
}

function renderNewsInsightPanel() {
  const news = state.overview?.news || {};
  const digest = news.last_digest || {};
  const items = Array.isArray(news.latest_items) ? news.latest_items : [];
  const enabled = Boolean(news.enabled);
  const dailyHot = Boolean(news.daily_hot_enabled);
  const aiDaily = news.ai_daily || {};
  const aiDailyTextStatus = news.ai_daily_enabled
    ? (aiDaily.last_text_readable
      ? `文字版 ${formatCompactNumber(aiDaily.last_text_chars || 0)}字`
      : (aiDaily.last_video_subtitle_readable
        ? `字幕 ${formatCompactNumber(aiDaily.last_video_subtitle_chars || 0)}字`
        : (aiDaily.last_video_link
          ? (aiDaily.last_video_context_chars
            ? `视频信息 ${formatCompactNumber(aiDaily.last_video_context_chars || 0)}字`
            : (aiDaily.last_video_subtitle_status === "missing" ? "字幕暂无，按视频公开信息" : "视频正文暂无"))
          : (aiDaily.last_text_link ? "文字版未读到正文" : "文字版暂无"))))
    : "";
  const aiDailyBasisStatus = news.ai_daily_enabled && aiDaily.last_read_basis
    ? `依据：${aiDaily.last_read_basis}`
    : "";
  const headline = digest.headline || digest.topic || "暂无新闻见闻";
  const impression = digest.impression || (enabled ? "暂无整理结果。" : "新闻阅读未开启");
  const itemHtml = items.length
    ? items.slice(0, 6).map((item) => `
      <li>
        <span>${escapeHtml(item.source || "来源")}</span>
        <b>${escapeHtml(item.title || "未命名")}</b>
      </li>
    `).join("")
    : `<li class="empty-line">暂无候选记录</li>`;
  $("#newsInsightPanel").innerHTML = `
    <div class="insight-head">
      <div>
        <span class="eyebrow">${enabled ? "新闻阅读" : "未开启"}</span>
        <b>${escapeHtml(headline)}</b>
      </div>
      <small>${escapeHtml(news.last_read_at || "未阅读")}</small>
    </div>
    <p>${escapeHtml(impression)}</p>
    <div class="insight-meta">
      <span>${escapeHtml(insightStatus(news.last_status))}</span>
      <span>${dailyHot ? "每日热点开启" : "每日热点关闭"}</span>
      <span>${news.ai_daily_enabled ? `AI日报/早报：${escapeHtml(insightStatus(aiDaily.status))}` : "AI日报/早报关闭"}</span>
      ${aiDailyTextStatus ? `<span>${escapeHtml(aiDailyTextStatus)}</span>` : ""}
      ${aiDailyBasisStatus ? `<span>${escapeHtml(aiDailyBasisStatus)}</span>` : ""}
      <span>${news.boredom_read_enabled ? "空档阅读开启" : "空档阅读关闭"}</span>
    </div>
    <ul class="insight-list">${itemHtml}</ul>
  `;
}

function renderWebExplorationPanel() {
  const exploration = state.overview?.web_exploration || {};
  const digest = exploration.last_digest || {};
  const query = exploration.last_query || {};
  const history = Array.isArray(exploration.history) ? exploration.history : [];
  const enabled = Boolean(exploration.enabled);
  const recentWebHistory = history.slice().reverse().find((item) => item && item.source !== "news") || {};
  const displayTitle = digest.topic || recentWebHistory.title || query.query || "暂无主动搜索记录";
  const displayNote = digest.note || recentWebHistory.intro || recentWebHistory.content || (enabled ? "等待下一次主动搜索留下笔记。" : "主动搜索未开启");
  const displayTime = exploration.last_explore_at || recentWebHistory.generated_at || recentWebHistory.date || "未搜索";
  const backendLabel = exploration.search_backend === "custom"
    ? "自定义搜索接口"
    : exploration.search_backend === "astrbot"
      ? "AstrBot 网页搜索"
      : "搜索未配置";
  const historyHtml = history.length
    ? history.slice().reverse().map((item) => {
      const sourceLabel = item.source_label || (item.source === "news" ? "新闻阅读" : "主动搜索");
      const queryText = item.query ? `搜索：${item.query}` : "";
      const sourceText = item.source_title ? `来源：${item.source_title}` : "";
      return `
        <li class="browsing-history-item ${escapeHtml(item.source || "web_exploration")}">
          <div>
            <span class="history-badge">${escapeHtml(sourceLabel)}</span>
            <small>${escapeHtml(item.generated_at || item.date || "")}</small>
          </div>
          <b>${escapeHtml(item.title || item.query || "浏览记录")}</b>
          <p>${escapeHtml(item.intro || item.content || "这次没有留下详细记录。")}</p>
          ${queryText || sourceText ? `<footer>${escapeHtml([queryText, sourceText].filter(Boolean).join(" · "))}</footer>` : ""}
        </li>
      `;
    }).join("")
    : `<li class="empty-line browsing-history-empty">暂无浏览记录</li>`;
  $("#webExplorationPanel").innerHTML = `
    <div class="insight-head">
      <div>
        <span class="eyebrow">${enabled ? "主动搜索" : "未开启"}</span>
        <b>${escapeHtml(displayTitle)}</b>
      </div>
      <small>${escapeHtml(displayTime)}</small>
    </div>
    <p>${escapeHtml(displayNote)}</p>
    <div class="insight-meta">
      <span>${escapeHtml(insightStatus(exploration.last_status))}</span>
      <span>${escapeHtml(backendLabel)}</span>
      <span>${exploration.boredom_search_enabled ? "空档搜索开启" : "空档搜索关闭"}</span>
      <span>历史 ${escapeHtml(exploration.history_count ?? history.length)} 条</span>
    </div>
    <ul class="browsing-history-list">${historyHtml}</ul>
  `;
}

function moduleShortcutNote(overview) {
  const settings = overview?.settings || {};
  const features = overview?.features || {};
  const items = [
    ["主动触达", Number(settings.max_daily_messages || 0) > 0 || Boolean(features.enable_group_companion || settings.enable_group_companion)],
    ["状态日程", Boolean(features.enable_humanized_states || settings.enable_humanized_states)],
    ["群聊观察", Boolean(features.enable_group_companion || settings.enable_group_companion)],
    ["关系网", Boolean(features.enable_worldbook_member_recognition || settings.enable_worldbook_member_recognition)],
    ["记忆学习", Boolean(features.enable_companion_memory || settings.enable_companion_memory)],
    ["长期创作", Boolean(features.enable_creative_writing || settings.enable_creative_writing)],
    ["新闻阅读", Boolean(features.enable_news_integration || settings.enable_news_integration)],
    ["主动搜索", Boolean(features.enable_web_exploration || settings.enable_web_exploration)],
    ["QQ 空间", Boolean(features.enable_qzone_integration || settings.enable_qzone_integration)],
  ];
  if (overview?.private_reading?.available) {
    items.push(["夹层阅读", Boolean(features.enable_private_reading_integration || settings.enable_private_reading_integration)]);
  }
  const enabledItems = items.filter(([, enabled]) => enabled);
  const preview = enabledItems.slice(0, 3).map(([label]) => label).join(" / ");
  const external = overview?.external_abilities?.enabled_count || 0;
  const suffix = external ? ` · 外部 ${external}` : "";
  return `${enabledItems.length}/${items.length} 个主要模块开启${preview ? ` · ${preview}` : ""}${suffix}`;
}

function providerShortcutNote(providers) {
  const configured = Object.values(providers || {}).filter((value) => String(value || "").trim()).length;
  return configured ? `${configured} 个 Provider 已指定` : "使用默认回退链";
}

function renderHealthPanel() {
  const overview = state.overview || {};
  const providers = overview.providers || {};
  const group = overview.group || {};
  const privateInfo = overview.private || {};
  const features = overview.features || {};
  const bili = overview.bilibili || {};
  const creative = overview.creative || {};
  const cache = overview.cache || {};
  const imageCache = cache.private_image_vision || {};
  const imagePrivateMetric = imageCache.private || {};
  const imageForwardMetric = imageCache.forward || {};
  const imageHits = Number(imagePrivateMetric.hits || 0) + Number(imageForwardMetric.hits || 0);
  const imageMisses = Number(imagePrivateMetric.misses || 0) + Number(imageForwardMetric.misses || 0);
  const imageTotal = imageHits + imageMisses;
  const imageHitRate = imageTotal ? Math.round((imageHits / imageTotal) * 100) : 0;
  const items = [
    {
      level: providers.LLM_PROVIDER_ID ? "ok" : "warn",
      title: providers.LLM_PROVIDER_ID ? "主模型已配置" : "主模型未单独配置",
      text: providers.LLM_PROVIDER_ID || "会回退到 AstrBot 默认模型",
    },
    {
      level: privateInfo.max_daily_messages > 0 ? "ok" : "warn",
      title: privateInfo.max_daily_messages > 0 ? "私聊主动可用" : "私聊主动已禁用",
      text: `每日主动上限：${privateInfo.max_daily_messages || 0}`,
    },
    {
      level: group.enabled ? "ok" : "warn",
      title: group.enabled ? "群聊观察已开启" : "群聊观察未开启",
      text: `${group.access_mode || "whitelist"} 模式，记录 ${group.group_count || 0} 个群`,
    },
    {
      level: features.enable_livingmemory_integration && overview.livingmemory?.available ? "ok" : "info",
      title: "LivingMemory 协同",
      text: livingMemoryHealthText(overview.livingmemory),
    },
    {
      level: features.enable_bilibili_integration ? (bili.available ? "ok" : "info") : "info",
      title: "B 站主动联动",
      text: features.enable_bilibili_integration
        ? `${bili.available ? "已检测" : "未检测"} · 最新 ${bili.latest_video?.title || "暂无"}`
        : "联动开关未启用",
    },
    {
      level: imageCache.enabled ? (imageTotal ? "ok" : "info") : "info",
      title: "缓存命中",
      text: imageCache.enabled
        ? `图片视觉 ${imageCache.items || 0}/${imageCache.max_items || "不限"} 条 · 命中率 ${imageTotal ? `${imageHitRate}%` : "暂无样本"}`
        : "图片视觉缓存未开启",
    },
    {
      level: features.enable_creative_writing ? "ok" : "info",
      title: "私下创作",
      text: features.enable_creative_writing
        ? `${creative.active_projects || 0} 个进行中 · ${creative.hidden_mode ? "节点自然提起" : "普通模式"}`
        : "创作行为未启用",
    },
  ];
  $("#healthPanel").innerHTML = items.map((item) => `
    <div class="health-item ${escapeHtml(item.level)}">
      <b>${escapeHtml(item.title)}</b>
      <span>${escapeHtml(item.text)}</span>
    </div>
  `).join("");
}

function livingMemoryHealthText(livingmemory) {
  if (!livingmemory?.enabled) return "协同开关未启用";
  if (!livingmemory?.available) return `未检测到可用插件：${livingmemory?.plugin_dir || "未知路径"}`;
  return `可用 · ${livingmemory.tool_name || "recall_long_term_memory"}`;
}

function renderDiagnostics() {
  const items = state.diagnostics || [];
  $("#diagnosticPanel").innerHTML = items.length
    ? items.map((item) => `
      <div class="diagnostic-item ${escapeHtml(item.level || "info")}">
        <span class="diag-dot"></span>
        <div>
          <b>${escapeHtml(item.title || "")}</b>
          <p>${escapeHtml(item.text || "")}</p>
          ${item.action ? `<small>${escapeHtml(item.action)}</small>` : ""}
        </div>
      </div>
    `).join("")
    : `<div class="empty small">暂无诊断项</div>`;
}

function renderUxReviewPanel() {
  const overview = state.overview || {};
  const settings = overview.settings || {};
  const features = overview.features || {};
  const group = overview.group || {};
  const news = overview.news || {};
  const aiDaily = news.ai_daily || {};
  const cache = overview.cache || {};
  const imageCache = cache.private_image_vision || {};
  const featureKeys = visibleTopLevelFeatureKeys(features);
  const enabledFeatureCount = featureKeys.filter((key) => features[key]).length;
  const activeFeatureGroupCount = featureGroups.filter((group) =>
    group.keys.some((key) => visibleFeatureSwitchKey(key) && features[key])
  ).length;
  const targetUsers = Array.isArray(settings.target_user_ids)
    ? settings.target_user_ids.filter(Boolean)
    : String(settings.target_user_ids || "").split(/[\s,，]+/).filter(Boolean);
  const hasPrivateTargets = targetUsers.length || state.users.some((user) => user.is_qq_user || user.user_id);
  const personaText = String(settings.schedule_persona_prompt || "");
  const roleFilled = ["姓名", "性别", "生日", "识别点", "职业/身份", "性格描述", "核心欲望/目标", "爱好"]
    .filter((label) => labeledRoleplayValuePresent(personaText, label)).length;
  const personaReady = roleFilled >= 6 || personaText.trim().length >= 220;
  const worldReady = String(settings.schedule_worldview_prompt || "").trim().length >= 40;
  const userProfileText = String(settings.roleplay_user_profile_prompt || "").trim()
    || String(settings.private_image_self_recognition_hint || "").trim();
  const userReady = userProfileText.length >= 30;
  const imageEnhanceEnabled = Boolean(settings.enable_private_image_self_recognition || features.enable_private_image_self_recognition);
  const items = [
    {
      level: personaReady && worldReady && userReady ? "ok" : "warn",
      title: "世界知识仍是最大收益点",
      text: personaReady && worldReady && userReady
        ? (roleFilled >= 6 ? `角色资料 ${roleFilled}/8 项、世界观和用户关系都有内容` : "旧版文案较完整；标准化字段可按需补齐")
        : `角色资料 ${roleFilled}/8 项；建议补齐外貌识别点、爱好、禁忌和用户关系`,
      tab: "roleplay",
    },
    {
      level: Number(settings.max_daily_messages || 0) <= 0 || hasPrivateTargets ? "ok" : "warn",
      title: "私聊主动目标",
      text: Number(settings.max_daily_messages || 0) <= 0
        ? "私聊主动关闭，不需要目标列表"
        : (hasPrivateTargets ? `已识别 ${targetUsers.length || state.users.length} 个私聊对象` : "主动开启但目标列表为空"),
      tab: "modules",
    },
    {
      level: features.enable_group_companion ? (Number(group.enabled_group_count || 0) > 0 ? "ok" : "warn") : "info",
      title: "群聊入口",
      text: features.enable_group_companion
        ? `${group.enabled_group_count || 0}/${group.group_count || 0} 个群正在观测`
        : "群聊观察关闭，群聊相关页只保留管理视图",
      tab: "group",
    },
    {
      level: imageEnhanceEnabled
        ? (settings.enable_private_image_vision_cache ? "ok" : "warn")
        : "info",
      title: "图片转述增强",
      text: imageEnhanceEnabled
        ? `缓存 ${imageCache.items || 0}/${imageCache.max_items || "不限"} 条；${settings.enable_private_image_vision_cache ? "重复图会复用视觉摘要" : "缓存关闭，重复表情包会重复调用"}`
        : "图片转述增强关闭",
      tab: "image-cache",
    },
    {
      level: featureKeys.length ? "ok" : "info",
      title: "功能开关已分组",
      text: featureKeys.length
        ? `${enabledFeatureCount}/${featureKeys.length} 个主开关开启，已归入 ${activeFeatureGroupCount}/${featureGroups.length} 个分类；子开关和具体参数进详情页查看`
        : "等待功能开关数据",
      tab: "config",
    },
    {
      level: news.ai_daily_enabled
        ? (aiDaily.last_text_readable || aiDaily.status === "already_read_today_video" ? "ok" : "warn")
        : "info",
      title: "AI 日报/早报可解释性",
      text: news.ai_daily_enabled
        ? (aiDaily.last_text_readable ? `文字版已读 ${formatCompactNumber(aiDaily.last_text_chars || 0)} 字` : `状态：${insightStatus(aiDaily.status)}`)
        : "AI 日报/早报追踪关闭",
      tab: "dashboard",
    },
  ];
  $("#uxReviewPanel").innerHTML = items.map((item) => `
    <button type="button" class="ux-review-item ${escapeHtml(item.level)}" data-jump-tab="${escapeHtml(item.tab)}">
      <span>${escapeHtml(item.level === "ok" ? "已处理" : item.level === "warn" ? "需关注" : "信息")}</span>
      <b>${escapeHtml(item.title)}</b>
      <small>${escapeHtml(item.text)}</small>
    </button>
  `).join("");
}

function labeledRoleplayValuePresent(text, label) {
  const source = String(text || "");
  const escaped = escapeRegExp(label);
  const pattern = new RegExp(`(?:^|\\n)\\s*${escaped}\\s*[：:]\\s*([^\\n]+)`, "u");
  const match = source.match(pattern);
  return Boolean(match && String(match[1] || "").trim());
}

function renderTroubleshooting() {
  const summaryEl = $("#troubleshootingSummary");
  const checksEl = $("#troubleshootingChecks");
  const eventsEl = $("#troubleshootingEvents");
  const sqliteEl = $("#troubleshootingSqlite");
  const chainEl = $("#troubleshootingChainTests");
  const injectionsEl = $("#troubleshootingPromptInjections");
  const debounceEl = $("#troubleshootingDebounceTrace");
  const faqEl = $("#troubleshootingFaq");
  if (!summaryEl || !checksEl || !eventsEl || !sqliteEl || !chainEl || !injectionsEl) return;
  const data = state.troubleshooting || {};
  const summary = data.summary || {};
  const counts = summary.counts || {};
  const level = summary.level || "info";
  const selected = state.troubleshootingFilter || "all";
  const checks = Array.isArray(data.checks) ? data.checks : [];
  const events = Array.isArray(data.recent_events) ? data.recent_events : [];
  const filteredChecks = selected === "all" ? checks : checks.filter((item) => item.level === selected);
  const filteredEvents = selected === "all" ? events : events.filter((item) => item.level === selected);
  const reasonItems = troubleshootingReasonItems(filteredChecks, filteredEvents, selected);
  summaryEl.innerHTML = `
    <section class="troubleshooting-head-card ${escapeHtml(level)}">
      <div>
        <span>${escapeHtml(summary.generated_at || "等待检查")}</span>
        <b>${escapeHtml(summary.headline || "尚未加载排障信息")}</b>
        <small>${escapeHtml(troubleshootingSummaryText(counts))}</small>
      </div>
      <button type="button" data-troubleshooting-refresh>重新检查</button>
    </section>
    ${["error", "warn", "info", "ok"].map((name) => `
      <button type="button" class="troubleshooting-count ${escapeHtml(name)} ${selected === name ? "is-active" : ""}" data-troubleshooting-filter="${escapeHtml(name)}">
        <b>${escapeHtml(counts[name] || 0)}</b>
        <span>${escapeHtml(troubleshootingLevelLabel(name))}</span>
      </button>
    `).join("")}
    <section class="troubleshooting-reasons">
      <header>
        <b>${escapeHtml(selected === "all" ? "近 2 小时待处理原因" : `${troubleshootingLevelLabel(selected)}原因`)}</b>
        <span>${escapeHtml(selected === "all" ? "只展示近期需要处理的错误和警告；普通信息可点信息查看" : "筛选同时作用于常见问题检查和最近问题")}</span>
      </header>
      ${reasonItems.length ? reasonItems.map((item) => `
        <button type="button" class="${escapeHtml(item.level || "info")}" ${item.jump ? `data-jump-tab="${escapeHtml(item.jump)}"` : ""}>
          <b>${escapeHtml(item.title || "-")}</b>
          <span>${escapeHtml(item.text || "")}</span>
        </button>
      `).join("") : `<div class="empty small">${escapeHtml(selected === "all" ? "暂无需要处理的原因" : "这个筛选下暂无原因")}</div>`}
    </section>
  `;
  document.querySelectorAll("[data-troubleshooting-filter]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.troubleshootingFilter === selected);
  });
  checksEl.innerHTML = filteredChecks.length
    ? filteredChecks.map((item) => troubleshootingCheckMarkup(item)).join("")
    : `<div class="empty small">暂无${escapeHtml(selected === "all" ? "" : troubleshootingLevelLabel(selected))}常见问题检查项</div>`;
  eventsEl.innerHTML = filteredEvents.length
    ? filteredEvents.map((item) => troubleshootingEventMarkup(item)).join("")
    : `<div class="empty small">暂无${escapeHtml(selected === "all" ? "" : troubleshootingLevelLabel(selected))}最近问题记录</div>`;
  const sqlite = data.sqlite || {};
  const sqliteItems = Array.isArray(sqlite.items) ? sqlite.items : [];
  sqliteEl.innerHTML = sqliteItems.length
    ? sqliteItems.map((item) => `
      <section class="troubleshooting-sqlite-row ${escapeHtml(item.level || "info")}">
        <b>${escapeHtml(item.name || "database")}</b>
        <span>${escapeHtml(item.text || "-")}</span>
        <small>${escapeHtml(item.path || "")}</small>
      </section>
    `).join("")
    : `<div class="empty small">没有检测到候选 SQLite 数据库文件</div>`;
  chainEl.innerHTML = troubleshootingChainTestMarkup(
    data.chain_tests || {},
    data.recent_photo_generations || [],
    data.screen_companion || state.overview?.screen_companion || {},
    data.qzone || state.overview?.qzone || {},
  );
  injectionsEl.innerHTML = troubleshootingPromptInjectionMarkup(data.prompt_injections || {});
  if (faqEl) faqEl.innerHTML = troubleshootingFaqMarkup(data);
  if (debounceEl) {
    debounceEl.innerHTML = troubleshootingDebounceTraceMarkup(state.overview?.message_debounce || {});
  }
}

function troubleshootingReasonItems(checks, events, selected) {
  const importantLevels = selected === "all" ? new Set(["error", "warn"]) : new Set([selected]);
  const rows = [];
  for (const item of checks || []) {
    if (!importantLevels.has(item.level || "info")) continue;
    rows.push({
      level: item.level || "info",
      title: troubleshootingDisplayTitle(item.title || "-", item),
      text: item.action || item.text || "",
      jump: item.jump || "",
    });
  }
  for (const item of events || []) {
    if (!importantLevels.has(item.level || "info")) continue;
    rows.push({
      level: item.level || "info",
      title: troubleshootingDisplayTitle(item.title || "-", item),
      text: item.detail || item.action || "",
      jump: item.jump || "",
    });
  }
  const rank = { error: 0, warn: 1, info: 2, ok: 3 };
  rows.sort((a, b) => (rank[a.level] ?? 9) - (rank[b.level] ?? 9));
  return rows.slice(0, 6);
}

function troubleshootingDisplayTitle(title, item = {}) {
  const rawTitle = String(title || "").trim();
  const task = String(item.task || item.key || "").trim();
  if (task) {
    const label = tokenTaskLabel(task);
    if (label && label !== task) {
      return rawTitle.includes("失败") ? `${label}失败` : label;
    }
  }
  const match = rawTitle.match(/^([A-Za-z0-9_.:-]+)\s*(失败|异常|错误|调用失败)$/);
  if (match) {
    const label = tokenTaskLabel(match[1]);
    if (label && label !== match[1]) return `${label}${match[2]}`;
  }
  return rawTitle || "-";
}

function troubleshootingSummaryText(counts) {
  if (!counts) return "等待数据";
  const error = Number(counts.error || 0);
  const warn = Number(counts.warn || 0);
  const info = Number(counts.info || 0);
  const ok = Number(counts.ok || 0);
  return `错误 ${error} · 警告 ${warn} · 信息 ${info} · 正常 ${ok}`;
}

function troubleshootingLevelLabel(level) {
  return {
    error: "错误",
    warn: "警告",
    info: "信息",
    ok: "正常",
  }[level] || level || "未知";
}

function formatBytes(value) {
  const size = Number(value || 0);
  if (!Number.isFinite(size) || size <= 0) return "0 B";
  if (size < 1024) return `${Math.round(size)} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(2)} MB`;
}

function troubleshootingCheckMarkup(item) {
  const level = item.level || "info";
  return `
    <section class="troubleshooting-check ${escapeHtml(level)}">
      <div class="troubleshooting-check-icon">${escapeHtml(level === "ok" ? "✓" : level === "error" ? "!" : level === "warn" ? "!" : "i")}</div>
      <div>
        <b>${escapeHtml(item.title || "-")}</b>
        <p>${escapeHtml(item.text || "")}</p>
        ${item.action ? `<small>${escapeHtml(item.action)}</small>` : ""}
      </div>
      ${item.jump ? `<button type="button" data-jump-tab="${escapeHtml(item.jump)}">查看</button>` : ""}
    </section>
  `;
}

function troubleshootingEventMarkup(item) {
  return `
    <section class="troubleshooting-event ${escapeHtml(item.level || "info")}">
      <header>
        <span>${escapeHtml(item.source || "事件")}</span>
        <small>${escapeHtml(item.time || "")}</small>
      </header>
      <b>${escapeHtml(item.title || "-")}</b>
      ${item.detail ? `<p>${escapeHtml(item.detail)}</p>` : ""}
      <footer>
        ${item.action ? `<span>${escapeHtml(item.action)}</span>` : ""}
        ${item.jump ? `<button type="button" data-jump-tab="${escapeHtml(item.jump)}">查看</button>` : ""}
      </footer>
    </section>
  `;
}

function troubleshootingFaqMarkup(data = {}) {
  const chainTests = data.chain_tests || {};
  const proactiveTest = chainTests.proactive_message || {};
  const imageTest = chainTests.image_generation_text2img || chainTests.image_generation || {};
  const promptMessages = Array.isArray(data.prompt_injections?.messages) ? data.prompt_injections.messages : [];
  const proactiveMeta = proactiveTest.pending
    ? "最近主动测试：已预约，稍后刷新看结果"
    : proactiveTest.ran_at_text
      ? `最近主动测试：${proactiveTest.ok ? "通过" : "失败"} · ${proactiveTest.ran_at_text}`
      : "尚未运行主动链路测试";
  const imageMeta = imageTest.ran_at_text
    ? `最近文生图测试：${imageTest.ok ? "通过" : "失败"} · ${imageTest.ran_at_text}`
    : "尚未运行文生图链路测试";
  const injectionMeta = promptMessages.length
    ? `当前保留最近 ${Math.min(promptMessages.length, 10)} 次回复的注入链路`
    : "当前没有可展示的注入链路记录";
  const items = [
    {
      title: "为什么没有主动消息？",
      meta: proactiveMeta,
      body: [
        "先确认私聊对象已启用，并且没有处在静默、休息、冷却、每日上限、未回复降频或主动人格判定拦截中。",
        "再看上方“最近问题”和主动审计记录：如果显示“已放弃/已延后”，通常是时间窗、关系压力、内容重复或链路依赖不可用。",
        "最直接的验证方式是点“测试主动消息”，它会预约一次临时主动，检查生成、复核、发送和归档是否完整。",
      ],
      actions: [
        { label: "看主动页", tab: "proactive" },
        { label: "看私聊对象", tab: "private" },
        { label: "测试主动消息", test: "proactive_message" },
      ],
    },
    {
      title: "为什么生图失败？",
      meta: imageMeta,
      body: [
        "先看在线/本地后端是否选对：在线后端要检查平台、Base URL、Key、模型名、尺寸和超时；本地 ComfyUI 要检查工作流名称和服务是否可连。",
        "如果是自拍或参考图链路，再检查参考图路径/URL 是否可读，以及本地负载保护有没有因为 CPU、内存或队列压力而延后。",
        "排障里的“测试文生图/测试自拍”会实际生成文件，比只看配置更可靠。",
      ],
      actions: [
        { label: "测试文生图", test: "image_generation_text2img", workflowKind: "text2img" },
        { label: "测试自拍", test: "image_generation_selfie", workflowKind: "selfie" },
        { label: "看模型页", tab: "models" },
      ],
    },
    {
      title: "为什么回复像被注入或带偏？",
      meta: "优先看“最近注入内容”和群聊防注入记录",
      body: [
        "先点开最近回复对应的注入链路，区分真实用户消息、插件动态注入、TTS 规则和模型生成内容。",
        "群聊里如果有人要求改称呼、改设定、改格式或要求忽略人格，确认“群聊防注入”和“群聊人格降噪”开启。",
        "如果污染来自关系网自登记、黑话或成员画像，去关系网/群聊页检查对应条目。",
      ],
      actions: [
        { label: "看群聊页", tab: "group" },
        { label: "看关系网", tab: "worldbook" },
      ],
    },
    {
      title: "为什么排障页没有注入记录？",
      meta: injectionMeta,
      body: [
        "注入记录按最近回复聚合，只保留最近 10 次；需要实际发生一次主链请求或请求级提示词注入后才会出现。",
        "非陪伴私聊现在会放行默认主链，这类消息不会强行生成陪伴注入记录，这是正常现象。",
        "如果刚刚才触发回复，先点“重新检查”；如果仍没有，说明该轮可能没有进入插件被动增强链路，或被主动-only/休息/目标用户判断提前放行。",
      ],
      actions: [
        { label: "重新检查", refresh: true },
        { label: "看最近注入", anchor: "troubleshootingPromptInjections" },
      ],
    },
  ];
  return items.map((item, index) => `
    <details class="troubleshooting-faq-item" ${index < 2 ? "open" : ""}>
      <summary>
        <span>
          <b>${escapeHtml(item.title)}</b>
          <small>${escapeHtml(item.meta)}</small>
        </span>
      </summary>
      <div class="troubleshooting-faq-body">
        ${item.body.map((line) => `<p>${escapeHtml(line)}</p>`).join("")}
        <footer>
          ${item.actions.map((action) => {
            if (action.tab) return `<button type="button" data-jump-tab="${escapeHtml(action.tab)}">${escapeHtml(action.label)}</button>`;
            if (action.test) {
              return `<button type="button" data-troubleshooting-test="${escapeHtml(action.test)}"${action.workflowKind ? ` data-troubleshooting-workflow-kind="${escapeHtml(action.workflowKind)}"` : ""}>${escapeHtml(action.label)}</button>`;
            }
            if (action.refresh) return `<button type="button" data-troubleshooting-refresh>${escapeHtml(action.label)}</button>`;
            if (action.anchor) return `<button type="button" data-scroll-target="${escapeHtml(action.anchor)}">${escapeHtml(action.label)}</button>`;
            return "";
          }).join("")}
        </footer>
      </div>
    </details>
  `).join("");
}

function troubleshootingChainTestMarkup(results, recentPhotoGenerations = [], screenCompanion = {}, qzone = {}) {
  const tests = [
    {
      type: "image_generation_text2img",
      workflowKind: "text2img",
      title: "文生图",
      text: "实际调用普通文生图链路并检查返回文件",
      button: "测试文生图",
    },
    {
      type: "image_generation_selfie",
      workflowKind: "selfie",
      title: "自拍参考图",
      text: "实际调用自拍/人像链路；有参考图时会测试参考图输入",
      button: "测试自拍",
    },
    {
      type: "tts_generation",
      title: "TTS 生成",
      text: "实际调用当前会话 TTS provider 并检查音频文件",
      button: "测试 TTS 生成",
    },
    ...(screenCompanion?.available ? [{
      type: "screen_peek",
      title: "窥屏",
      text: "实际调用 screen_companion 识屏入口，确认屏幕观察链路可用",
      button: "测试窥屏",
    }] : []),
    {
      type: "qzone_integration",
      title: "QQ 空间",
      text: qzone?.enabled
        ? "检查 Cookie、读取链路、发布工具空参数模拟和评论收件箱状态；不会真实发布或回复评论"
        : "检查 QQ 空间配置状态；未启用时会显示缺失原因，不会真实发布或回复评论",
      button: "测试 QQ 空间",
    },
    {
      type: "proactive_message",
      title: "主动消息",
      text: "预约 1 分钟后的临时主动私聊，检查生成、复核、发送和历史归档",
      button: "测试主动消息",
    },
    {
      type: "model_diagnostics",
      title: "模型数据排障",
      text: "检查技能、群黑话、关系网、长期画像和表达学习里的模型理解杂音；只给建议，不自动修改",
      button: "运行模型排障",
    },
  ];
  const testsMarkup = tests.map((test) => {
    const result = results?.[test.type]
      || (test.type === "image_generation_text2img" ? results?.image_generation : null)
      || (test.type === "model_diagnostics" ? results?.skill_similarity : null)
      || {};
    const ok = Boolean(result.ok);
    const pending = Boolean(result.pending);
    const hasResult = Boolean(result.ran_at || result.ran_at_text || result.error || result.detail);
    const status = hasResult ? (pending ? "info" : ok ? "ok" : "error") : "info";
    const meta = [
      result.backend || result.provider || "",
      result.image_model ? `模型 ${result.image_model}` : "",
      result.workflow_kind ? `类型 ${result.workflow_kind}` : "",
      result.used_reference ? "已带参考图" : "",
      result.context_chars ? `摘要 ${result.context_chars} 字` : "",
      result.elapsed_ms ? `${result.elapsed_ms}ms` : "",
      result.file_size ? `${formatBytes(result.file_size)}` : "",
      result.ran_at_text || "",
    ].filter(Boolean).join(" · ");
    const stepsMarkup = troubleshootingChainStepsMarkup(result.steps);
    const previewMarkup = troubleshootingChainPreviewMarkup(test.type, result);
    const detailText = troubleshootingChainDetailText(test, result, hasResult);
    return `
      <section class="troubleshooting-chain-test ${escapeHtml(status)}">
        <div>
          <b>${escapeHtml(test.title)}</b>
          <p>${escapeHtml(detailText)}</p>
          ${meta ? `<small>${escapeHtml(meta)}</small>` : ""}
          ${result.path ? `<small class="path">${escapeHtml(result.path)}</small>` : ""}
          ${previewMarkup}
          ${stepsMarkup}
        </div>
        <button type="button" data-troubleshooting-test="${escapeHtml(test.type)}" ${test.workflowKind ? `data-troubleshooting-workflow-kind="${escapeHtml(test.workflowKind)}"` : ""}>${escapeHtml(test.button)}</button>
      </section>
    `;
  }).join("");
  return `${testsMarkup}${troubleshootingRecentPhotoGenerationMarkup(recentPhotoGenerations)}`;
}

function troubleshootingChainDetailText(test, result, hasResult) {
  if (!hasResult) return test.text;
  if (result.error) return result.error;
  if ((test.type === "skill_similarity" || test.type === "model_diagnostics") && result.ok) {
    const localCount = Number(result.local_count || 0);
    const modelCount = Number(result.model_count || 0);
    return result.detail || `本地发现 ${localCount} 条候选，模型给出 ${modelCount} 条建议`;
  }
  return result.detail || test.text;
}

function troubleshootingChainStepsMarkup(stepsRaw) {
  const steps = Array.isArray(stepsRaw) ? stepsRaw : [];
  if (!steps.length) return "";
  return `
    <details class="chain-test-steps">
      <summary>查看链路阶段</summary>
      ${steps.map((step) => `
        <div class="${escapeHtml(step.status || "info")}">
          <b>${escapeHtml(step.name || "-")}</b>
          <span>${escapeHtml(step.detail || "")}</span>
        </div>
      `).join("")}
    </details>
  `;
}

function troubleshootingChainPreviewMarkup(type, result) {
  if (type === "screen_peek") {
    if (!result.text_preview) return "";
    return `
      <details class="chain-test-steps chain-test-preview">
        <summary>查看识屏摘要</summary>
        <p>${escapeHtml(result.text_preview || "")}</p>
      </details>
    `;
  }
  if (type === "qzone_integration") {
    if (!result.text_preview) return "";
    return `
      <details class="chain-test-steps chain-test-preview">
        <summary>查看 QQ 空间测试摘要</summary>
        <p>${escapeHtml(result.text_preview || "")}</p>
      </details>
    `;
  }
  if (type === "skill_similarity" || type === "model_diagnostics") {
    const sections = Array.isArray(result.sections) ? result.sections.filter(Boolean) : [];
    const suggestions = Array.isArray(result.suggestions) ? result.suggestions.filter(Boolean) : [];
    if (!suggestions.length && !result.text_preview) return "";
    const sectionMarkup = sections.length ? sections.map((section) => {
      const items = Array.isArray(section.suggestions) ? section.suggestions.filter(Boolean) : [];
      const meta = [
        Number(section.local_count || 0) ? `本地 ${Number(section.local_count || 0)}` : "",
        Number(section.model_count || 0) ? `模型 ${Number(section.model_count || 0)}` : "",
      ].filter(Boolean).join(" · ");
      return `
        <div class="chain-test-preview-section">
          <b>${escapeHtml(section.title || "排障项")}${meta ? ` · ${escapeHtml(meta)}` : ""}</b>
          ${items.length ? items.map((item) => `<p>${escapeHtml(item)}</p>`).join("") : `<p class="muted">暂无明显问题</p>`}
        </div>
      `;
    }).join("") : "";
    return `
      <details class="chain-test-steps chain-test-preview">
        <summary>查看模型排障建议${result.extra_count ? `（另有 ${escapeHtml(result.extra_count)} 条未展示）` : ""}</summary>
        ${sectionMarkup || (suggestions.length ? suggestions.map((item) => `<p>${escapeHtml(item)}</p>`).join("") : `<p>${escapeHtml(result.text_preview || "")}</p>`)}
      </details>
    `;
  }
  const parts = [];
  if (result.original_text_preview || result.final_text_preview) {
    const original = result.original_text_preview || result.text_preview || "";
    const finalText = result.final_text_preview || result.text_preview || "";
    parts.push(`
      <details class="chain-test-steps chain-test-preview">
        <summary>查看主动改写对比</summary>
        ${original ? `<p><b>原候选：</b>${escapeHtml(original)}</p>` : ""}
        ${finalText ? `<p><b>最终发送：</b>${escapeHtml(finalText)}</p>` : ""}
      </details>
    `);
  }
  if (result.text_preview && !result.final_text_preview) parts.push(`<small class="path">文本预览：${escapeHtml(result.text_preview)}</small>`);
  if (result.prompt) parts.push(`<small class="path">提示词：${escapeHtml(result.prompt)}</small>`);
  return parts.join("");
}

function troubleshootingRecentPhotoGenerationMarkup(itemsRaw) {
  const items = Array.isArray(itemsRaw) ? itemsRaw.filter(Boolean) : [];
  if (!items.length) {
    return `
      <section class="troubleshooting-chain-test info">
        <div>
          <b>最近生图提示词</b>
          <p>暂无真实生图记录；运行一次主动拍照、每日穿搭、自然语言生图或生图排障后会显示。</p>
        </div>
      </section>
    `;
  }
  return `
    <section class="troubleshooting-chain-test info">
      <div>
        <b>最近生图提示词</b>
        <p>展示最近 ${escapeHtml(String(items.length))} 次真实生图调用，便于排查构图、模型、参考图和后端回退问题。</p>
        <details class="chain-test-steps chain-test-preview">
          <summary>查看最近生图记录</summary>
          ${items.map((item) => {
            const meta = [
              item.ok ? "成功" : "失败",
              item.backend || "",
              item.kind ? `类型 ${item.kind}` : "",
              item.image_size ? `尺寸 ${item.image_size}` : "",
              item.reference ? "带参考图" : "",
              item.elapsed_ms ? `${item.elapsed_ms}ms` : "",
              item.time || "",
              item.trace ? `trace ${item.trace}` : "",
            ].filter(Boolean).join(" · ");
            return `
              <div class="chain-test-preview-section">
                <b>${escapeHtml(meta || "生图记录")}</b>
                ${item.note ? `<p>${escapeHtml(item.note)}</p>` : ""}
                ${item.path ? `<small class="path">${escapeHtml(item.path)}</small>` : ""}
                ${item.reference_path ? `<small class="path">参考图：${escapeHtml(item.reference_path)}</small>` : ""}
                <p>${escapeHtml(item.prompt || item.prompt_preview || "")}</p>
              </div>
            `;
          }).join("")}
        </details>
      </div>
    </section>
  `;
}

function troubleshootingDebounceTraceMarkup(data) {
  const logs = Array.isArray(data.recent_logs) ? data.recent_logs : [];
  const examples = Array.isArray(data.examples) ? data.examples : [];
  const status = data.smart_enabled
    ? `智能收口开启 · 等待 ${Number(data.smart_wait || 0).toFixed(1)}s · 学习窗口 ${Number(data.learning_window || 0).toFixed(1)}s`
    : data.enabled
      ? "消息收口开启，智能文本收口未开启"
      : "消息收口未开启";
  const provider = data.provider_id || "跟随默认模型";
  const limitText = data.enabled
    ? `最长 ${Number(data.max_wait || 0).toFixed(1)}s · 最多 ${Number(data.max_merge || 0)} 条`
    : "";
  const logMarkup = logs.length ? logs.slice(0, 8).map((item) => {
    const tone = item.outcome === "wait" || item.outcome === "extend_wait"
      ? "warn"
      : item.outcome === "reply_now"
        ? "ok"
        : item.outcome === "learned" || item.outcome === "timeout_single"
          ? "info"
          : "";
    const confidence = Number(item.confidence || 0);
    const meta = [
      debounceOutcomeLabel(item.outcome),
      item.source || "",
      confidence ? `置信 ${(confidence * 100).toFixed(0)}%` : "",
      item.wait_seconds ? `等待 ${Number(item.wait_seconds).toFixed(1)}s` : "",
      item.message_count ? `${item.message_count} 条` : "",
      item.time || "",
    ].filter(Boolean).join(" · ");
    return `
      <section class="debounce-log ${escapeHtml(tone)}">
        <header>
          <b>${escapeHtml(debounceDecisionLabel(item.decision))}</b>
          <span>${escapeHtml(item.scope || item.chat || "-")}</span>
        </header>
        <p>${escapeHtml(item.text || item.note || "-")}</p>
        <small>${escapeHtml(meta)}</small>
        ${item.reason ? `<small>原因：${escapeHtml(item.reason)}</small>` : ""}
        ${item.raw ? `<small>原始返回：${escapeHtml(item.raw)}</small>` : ""}
      </section>
    `;
  }).join("") : `<div class="empty small">暂无智能防抖决策记录</div>`;
  const exampleMarkup = examples.length ? `
    <details class="debounce-examples">
      <summary>误判学习样本 ${escapeHtml(examples.length)}</summary>
      ${examples.slice(0, 6).map((item) => `
        <section>
          <b>${escapeHtml(item.kind || "-")}</b>
          <span>${escapeHtml(item.note || "")}</span>
          <small>${escapeHtml((item.messages || []).join(" / "))}</small>
        </section>
      `).join("")}
    </details>
  ` : "";
  return `
    <div class="debounce-status">
      <span>${escapeHtml(status)}</span>
      ${limitText ? `<span>${escapeHtml(limitText)}</span>` : ""}
      <span>模型：${escapeHtml(provider)}</span>
    </div>
    <div class="debounce-log-list">${logMarkup}</div>
    ${exampleMarkup}
  `;
}

function debounceDecisionLabel(decision) {
  return {
    complete: "放行",
    incomplete: "等待",
    fixed: "固定等待",
  }[decision] || decision || "记录";
}

function promptInjectionKindLabel(kind) {
  return {
    request: "请求附加",
    passive: "被动回复",
    proactive: "主动消息",
    tts: "TTS 规则",
  }[kind] || kind || "注入记录";
}

function promptInjectionMessagePreview(value) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (!text) return "未记录触发消息";
  const internalMarkers = ["【语音消息规则】", "<pc_tts>", "</pc_tts>", "语音消息规则", "提示词片段", "当前语音正文目标语种"];
  if (internalMarkers.some((marker) => text.includes(marker))) return "未记录触发消息";
  return text.length > 72 ? `${text.slice(0, 72)}…` : text;
}

function troubleshootingPromptInjectionMarkup(data) {
  const messages = Array.isArray(data?.messages) ? data.messages.slice(0, 10) : [];
  const total = Number(data?.message_total || messages.length || 0);
  return `
    <section class="troubleshooting-injection-group">
      <header>
        <div>
          <b>最近回复的 10 次消息</b>
          <span>按消息聚合展示注入链路；点开某条消息即可查看这次回复的完整注入模块。当前 ${escapeHtml(Math.min(total || messages.length, 10))} 条</span>
        </div>
      </header>
      ${messages.length ? messages.map((item) => troubleshootingPromptInjectionMessageMarkup(item)).join("") : `<div class="empty small">暂无可查看的注入记录</div>`}
    </section>
  `;
}

function troubleshootingPromptInjectionMessageMarkup(message) {
  const items = Array.isArray(message?.items) ? message.items : [];
  const kinds = Array.isArray(message?.kinds) ? message.kinds.map((kind) => promptInjectionKindLabel(kind)).filter(Boolean) : [];
  const meta = [
    message?.time || "",
    message?.sender_label || "",
    items.length ? `${items.length} 段注入` : "",
    Number(message?.module_count || 0) ? `${Number(message.module_count)} 个模块` : "",
    kinds.length ? kinds.join(" / ") : "",
  ].filter(Boolean).join(" · ");
  const messagePreview = promptInjectionMessagePreview(message?.message_preview || (items[0]?.metadata?.["触发消息"]) || "");
  const metaRows = [
    ["发送者", message?.sender_label || ""],
    ["会话", message?.session || ""],
    ["跟踪", message?.trace_id || ""],
    ["首次记录", message?.first_time || ""],
    ["最后更新", message?.time || ""],
  ].filter(([, value]) => value);
  return `
    <details class="troubleshooting-injection-item troubleshooting-injection-message">
      <summary>
        <span>
          <b>${escapeHtml(messagePreview)}</b>
          <small>${escapeHtml(message?.session || "")}</small>
        </span>
        <em>${escapeHtml(meta)}</em>
      </summary>
      ${metaRows.length ? `
        <dl>
          ${metaRows.map(([key, value]) => `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}
        </dl>
      ` : ""}
      ${items.map((item) => troubleshootingPromptInjectionItemMarkup(item, { nested: true })).join("")}
    </details>
  `;
}

function troubleshootingPromptInjectionItemMarkup(item, options = {}) {
  const nested = Boolean(options?.nested);
  const metadata = item?.metadata && typeof item.metadata === "object" ? item.metadata : {};
  const metaRows = Object.entries(metadata).filter(([key, value]) => key && value);
  const modules = Array.isArray(item?.modules) ? item.modules : [];
  const meta = [
    nested ? "" : promptInjectionKindLabel(item.kind || ""),
    item.time || "",
    item.mode ? `mode=${item.mode}` : "",
    item.chars ? `${item.chars} chars` : "",
    modules.length ? `${modules.length} 个模块` : "",
    item.truncated ? "已截断" : "",
  ].filter(Boolean).join(" · ");
  const title = nested
    ? `${promptInjectionKindLabel(item.kind || "")} · ${item.title || "注入内容"}`
    : (item.title || "注入内容");
  return `
    <details class="troubleshooting-injection-item">
      <summary>
        <span>
          <b>${escapeHtml(title)}</b>
          <small>${escapeHtml(item.session || "")}</small>
        </span>
        <em>${escapeHtml(meta)}</em>
      </summary>
      ${metaRows.length ? `
        <dl>
          ${metaRows.map(([key, value]) => `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}
        </dl>
      ` : ""}
      ${item.preview ? `<p>${escapeHtml(item.preview)}</p>` : ""}
      ${modules.length ? `
        <div class="troubleshooting-injection-modules">
          ${modules.map((module) => troubleshootingPromptInjectionModuleMarkup(module)).join("")}
        </div>
      ` : `<pre>${escapeHtml(item.content || "")}</pre>`}
      ${modules.length ? `
        <details class="troubleshooting-injection-raw">
          <summary>查看完整合并文本${item.truncated ? "（已截断）" : ""}</summary>
          <pre>${escapeHtml(item.content || "")}</pre>
        </details>
      ` : ""}
    </details>
  `;
}

function troubleshootingPromptInjectionModuleMarkup(module) {
  const content = String(module?.content || "");
  const chars = Number(module?.chars || content.length || 0);
  const meta = [
    module?.source ? `source=${module.source}` : "",
    module?.key ? `key=${module.key}` : "",
    Number.isFinite(Number(module?.priority)) ? `priority=${module.priority}` : "",
    chars ? `${chars} chars` : "",
    module?.truncated ? "已截断" : "",
  ].filter(Boolean).join(" · ");
  const shouldOpen = content.length > 0 && content.length <= 900;
  return `
    <details class="troubleshooting-injection-module" ${shouldOpen ? "open" : ""}>
      <summary>
        <span>
          <b>${escapeHtml(module?.title || "提示词片段")}</b>
          ${module?.description ? `<small>${escapeHtml(module.description)}</small>` : ""}
        </span>
        <em>${escapeHtml(meta)}</em>
      </summary>
      ${module?.preview && content.length > 900 ? `<p>${escapeHtml(module.preview)}</p>` : ""}
      <pre>${escapeHtml(content)}</pre>
    </details>
  `;
}

function debounceOutcomeLabel(outcome) {
  return {
    reply_now: "立即回复",
    wait: "进入等待",
    extend_wait: "延长等待",
    merged_followup: "等到补话",
    timeout_single: "误等样本",
    learned: "学习样本",
  }[outcome] || outcome || "";
}

function imageCacheScopeLabel(scope) {
  const value = String(scope || "private_image");
  const labels = {
    private_image: "私聊图片",
    private_image_query: "私聊追问",
    forward_image: "合并图片",
    screen_peek: "识屏",
  };
  return labels[value] || value;
}

function renderImageCache() {
  const summaryEl = $("#imageCacheSummary");
  const listEl = $("#imageCacheList");
  const detailEl = $("#imageCacheDetail");
  if (!summaryEl || !listEl || !detailEl) return;
  const overviewCache = state.overview?.cache?.private_image_vision || {};
  const items = state.imageCacheItems || [];
  const selected = items.find((item) => item.key === state.selectedImageCacheKey) || null;
  const hasActiveCacheQuery = Boolean(state.imageCacheLoaded || state.imageCacheFilter || (state.imageCacheScope && state.imageCacheScope !== "all") || state.imageCacheTotal || items.length);
  const totalDisplay = hasActiveCacheQuery ? state.imageCacheTotal : (overviewCache.items || 0);
  summaryEl.innerHTML = `
    <div class="image-cache-stat">
      <b>${escapeHtml(String(totalDisplay))}</b>
      <span>缓存条目</span>
    </div>
    <div class="image-cache-stat">
      <b>${escapeHtml(overviewCache.max_items ? `${overviewCache.max_items}` : "不限")}</b>
      <span>上限</span>
    </div>
    <div class="image-cache-stat ${overviewCache.enabled ? "ok" : "warn"}">
      <b>${escapeHtml(overviewCache.enabled ? "开启" : "关闭")}</b>
      <span>重复图片缓存</span>
    </div>
    <div class="image-cache-stat">
      <b>${escapeHtml(String(items.reduce((sum, item) => sum + Number(item.hits || 0), 0)))}</b>
      <span>当前列表命中</span>
    </div>
  `;
  const scopeSelect = $("#imageCacheScope");
  if (scopeSelect) {
    const current = state.imageCacheScope || "all";
    scopeSelect.innerHTML = [
      `<option value="all">全部范围</option>`,
      ...state.imageCacheScopes.map((scope) => `<option value="${escapeHtml(scope)}">${escapeHtml(imageCacheScopeLabel(scope))}</option>`),
    ].join("");
    scopeSelect.value = current;
  }
  const filterInput = $("#imageCacheFilter");
  if (filterInput && filterInput.value !== state.imageCacheFilter) {
    filterInput.value = state.imageCacheFilter || "";
  }
  listEl.innerHTML = items.length
    ? items.map((item) => imageCacheListItemMarkup(item)).join("")
    : `<div class="empty image-cache-empty"><b>暂无缓存条目</b><span>收到图片或表情包并完成视觉转述后会出现在这里。</span></div>`;
  detailEl.innerHTML = selected ? imageCacheDetailMarkup(selected) : `
    <div class="empty image-cache-empty">
      <b>选择一条缓存</b>
      <span>左侧点击图片/表情包缓存后，可以编辑摘要或删除。</span>
    </div>
  `;
}

function imageCacheListItemMarkup(item) {
  const selected = item.key === state.selectedImageCacheKey;
  const title = item.image_type || imageCacheScopeLabel(item.scope);
  const preview = item.visible || item.intent || item.text || "无摘要";
  const ownership = item.ownership ? `<span>${escapeHtml(item.ownership)}</span>` : "";
  const thumb = item.preview_url
    ? `<span class="image-cache-thumb has-image"><img src="${escapeHtml(item.preview_url)}" alt="${escapeHtml(title)}" loading="lazy" /></span>`
    : `<span class="image-cache-thumb">${escapeHtml((item.image_type || "图").slice(0, 2))}</span>`;
  return `
    <button type="button" class="image-cache-row ${selected ? "is-active" : ""}" data-image-cache-key="${escapeHtml(item.key)}">
      ${thumb}
      <span class="image-cache-row-main">
        <b>${escapeHtml(title)}</b>
        <small>${escapeHtml(preview)}</small>
        <i>${escapeHtml(imageCacheScopeLabel(item.scope))} · 命中 ${escapeHtml(item.hits || 0)} · ${escapeHtml(item.last_hit || item.created || "未知时间")}</i>
      </span>
      ${ownership}
    </button>
  `;
}

function imageCacheDetailMarkup(item) {
  const aliases = Array.isArray(item.image_aliases) ? item.image_aliases : [];
  const keys = Array.isArray(item.image_keys) ? item.image_keys : [];
  const previewMeta = item.preview_url
    ? [
        item.preview_width && item.preview_height ? `${item.preview_width}x${item.preview_height}` : "",
        item.preview_size ? formatBytes(item.preview_size) : "",
      ].filter(Boolean).join(" · ")
    : "";
  return `
    <form class="image-cache-editor" data-image-cache-editor="${escapeHtml(item.key)}">
      <header>
        <div>
          <span class="module-badge">${escapeHtml(imageCacheScopeLabel(item.scope))}</span>
          <h3>${escapeHtml(item.image_type || "图片缓存")}</h3>
          <p>${escapeHtml(item.intent || item.visible || "可编辑这张图片/表情包的视觉摘要。")}</p>
        </div>
        <button type="button" class="danger" data-image-cache-delete="${escapeHtml(item.key)}">删除缓存</button>
      </header>
      ${item.preview_url ? `
        <figure class="image-cache-preview">
          <img src="${escapeHtml(item.preview_url)}" alt="${escapeHtml(item.image_type || "图片缓存预览")}" />
          ${previewMeta ? `<figcaption>${escapeHtml(previewMeta)} · 压缩预览</figcaption>` : `<figcaption>压缩预览</figcaption>`}
        </figure>
      ` : ""}
      <div class="image-cache-meta">
        <div><dt>Provider</dt><dd>${escapeHtml(item.provider_id || "未记录")}</dd></div>
        <div><dt>创建</dt><dd>${escapeHtml(item.created || "-")}</dd></div>
        <div><dt>最近命中</dt><dd>${escapeHtml(item.last_hit || "未命中")}</dd></div>
        <div><dt>编辑</dt><dd>${escapeHtml(item.edited || "未编辑")}</dd></div>
        <div><dt>图片数</dt><dd>${escapeHtml(item.image_count || keys.length || 1)}</dd></div>
        <div><dt>Prompt</dt><dd>${escapeHtml(item.prompt_sig || "-")}</dd></div>
      </div>
      <label>缓存范围
        <select name="scope">
          ${["private_image", "private_image_query", "forward_image", "screen_peek"].map((scope) => `
            <option value="${escapeHtml(scope)}" ${item.scope === scope ? "selected" : ""}>${escapeHtml(imageCacheScopeLabel(scope))}</option>
          `).join("")}
        </select>
      </label>
      <label>Provider ID
        <input name="provider_id" value="${escapeHtml(item.provider_id || "")}" />
      </label>
      <label class="wide-field">视觉摘要
        <textarea name="text" rows="10">${escapeHtml(item.text || "")}</textarea>
      </label>
      <details class="image-cache-keys wide-field">
        <summary>缓存键和别名</summary>
        <code>${escapeHtml(item.key)}</code>
        ${keys.length ? `<p>${keys.map((key) => `<span>${escapeHtml(key)}</span>`).join("")}</p>` : ""}
        ${aliases.length ? `<p>${aliases.map((alias) => `<span>${escapeHtml(alias)}</span>`).join("")}</p>` : ""}
      </details>
      <div class="actions">
        <button type="button" data-copy-text="${escapeHtml(item.text || "")}">复制摘要</button>
        <button type="submit">保存缓存</button>
      </div>
    </form>
  `;
}

function renderRelationshipChart() {
  const buckets = { 亲近: 0, 熟悉: 0, 陌生: 0, 未分层: 0 };
  state.users.forEach((user) => {
    const stage = user.relationship_stage || "未分层";
    buckets[Object.prototype.hasOwnProperty.call(buckets, stage) ? stage : "未分层"] += 1;
  });
  $("#relationshipChart").innerHTML = horizontalBars(buckets, Math.max(1, state.users.length));
}

function renderQuotaChart() {
  const maxDaily = Number(state.overview?.private?.max_daily_messages || 0);
  const quotaUsers = state.users.filter((user) => user.is_qq_user);
  const rows = (quotaUsers.length ? quotaUsers : state.users).slice(0, 12).map((user) => {
    const used = Number(user.sent_today || 0);
    const pct = maxDaily > 0 ? Math.min(100, Math.round((used / maxDaily) * 100)) : 0;
    return `
      <div class="meter-row">
        <span title="${escapeHtml(user.user_id || "")}">${escapeHtml(userQuotaLabel(user))}</span>
        <div class="meter"><i style="width:${pct}%"></i></div>
        <b>${escapeHtml(used)}${maxDaily ? `/${escapeHtml(maxDaily)}` : ""}</b>
      </div>
    `;
  });
  $("#quotaChart").innerHTML = rows.length ? rows.join("") : `<div class="empty small">暂无私聊对象</div>`;
}

function userQuotaLabel(user) {
  const id = String(user?.user_id || "");
  if (user?.display_name && !String(user.display_name).startsWith("临时会话")) return user.display_name;
  if (user?.display_name && !user?.is_qq_user) return user.display_name;
  const nickname = String(user?.nickname || "").trim();
  const genericNames = new Set(["用户", "主人", "默认用户"]);
  if (!nickname || genericNames.has(nickname)) return id || "未命名";
  return id ? `${nickname} · ${id.slice(-4)}` : nickname;
}

function renderGroupBubbleChart() {
  const groups = state.groups.slice(0, 12);
  if (!groups.length) {
    $("#groupBubbleChart").innerHTML = `<div class="empty small">暂无群聊数据</div>`;
    return;
  }
  const enabledCount = groups.filter((group) => group.enabled).length;
  const totalMessages = groups.reduce((sum, group) => sum + Number(group.message_count || 0), 0);
  const totalTopics = groups.reduce((sum, group) => sum + Number(group.topic_count || 0), 0);
  const maxMessages = Math.max(1, ...groups.map((group) => Number(group.message_count || 0)));
  $("#groupBubbleChart").innerHTML = `
    <div class="group-observe-summary">
      <section><span>观测中</span><b>${escapeHtml(enabledCount)} / ${escapeHtml(groups.length)}</b></section>
      <section><span>累计消息</span><b>${escapeHtml(totalMessages)}</b></section>
      <section><span>话题线</span><b>${escapeHtml(totalTopics)}</b></section>
    </div>
    <div class="group-observe-list">
      ${groups.map((group) => renderGroupObserveRow(group, maxMessages)).join("")}
    </div>
  `;
  document.querySelectorAll("[data-observe-group]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedGroupId = button.dataset.observeGroup;
      switchTab("group");
      renderGroups();
      renderGroupDetail(true);
    });
  });
}

function renderGroupObserveRow(group, maxMessages) {
  const messageCount = Number(group.message_count || 0);
  const pct = Math.max(4, Math.round((messageCount / Math.max(1, maxMessages)) * 100));
  const name = group.name || group.group_name || group.title || group.group_id || "未命名群";
  const metrics = [
    `${messageCount} 消息`,
    `${Number(group.episode_count || 0)} 片段`,
    `${Number(group.topic_count || 0)} 话题`,
    `${Number(group.slang_count || 0)} 黑话`,
  ];
  return `
    <button type="button" class="group-observe-row ${group.enabled ? "" : "off"}" data-observe-group="${escapeHtml(group.group_id)}">
      <div class="group-observe-main">
        <span class="group-observe-state">${group.enabled ? "开启" : "停用"}</span>
        <b>${escapeHtml(name)}</b>
        <small>${escapeHtml(group.group_id || "")}</small>
      </div>
      <div class="group-observe-meter">
        <i style="width:${pct}%"></i>
      </div>
      <div class="group-observe-meta">
        ${metrics.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}
      </div>
      <small class="group-observe-time">${escapeHtml(group.last_seen || "暂无活跃")}</small>
    </button>
  `;
}

function groupTopicThreadsView(threads) {
  const items = Array.isArray(threads) ? threads : [];
  if (!items.length) {
    return `<div class="empty small">暂无话题线</div>`;
  }
  return `
    <div class="group-topic-list">
      ${items.map((item) => {
        const examples = Array.isArray(item.recent_examples) ? item.recent_examples : [];
        const participants = Array.isArray(item.participants) ? item.participants : [];
        const heat = Math.max(0, Math.min(100, Number(item.heat || 0)));
        return `
          <article class="group-topic-card ${item.status === "活跃" ? "active" : ""}">
            <div class="group-topic-rail">
              <span>${escapeHtml(item.rank || "")}</span>
              <i style="height:${escapeHtml(Math.max(14, heat))}%"></i>
            </div>
            <div class="group-topic-main">
              <header>
                <div>
                  <span class="group-topic-status">${escapeHtml(item.status || "话题")}</span>
                  <h3>${escapeHtml(item.title || "未命名话题")}</h3>
                </div>
                <dl>
                  <div><dt>消息</dt><dd>${escapeHtml(item.message_count || 0)}</dd></div>
                  <div><dt>参与</dt><dd>${escapeHtml(item.participant_count || participants.length || 0)}</dd></div>
                  <div><dt>持续</dt><dd>${escapeHtml(item.duration || "-")}</dd></div>
                </dl>
              </header>
              ${item.summary ? `<p class="group-topic-summary">${escapeHtml(item.summary)}</p>` : ""}
              <div class="group-topic-meta">
                <span>最近 ${escapeHtml(item.last_seen || "-")}</span>
                <span>开始 ${escapeHtml(item.started || "-")}</span>
                ${item.bot_joined ? `<span>Bot 参与过</span>` : ""}
              </div>
              ${participants.length ? `
                <div class="group-topic-people">
                  ${participants.slice(0, 6).map((person) => `<span title="${escapeHtml(person.id || "")}">${escapeHtml(person.name || person.id || "-")}</span>`).join("")}
                  ${participants.length > 6 ? `<span>+${escapeHtml(participants.length - 6)}</span>` : ""}
                </div>
              ` : ""}
              ${examples.length ? `
                <div class="group-topic-examples">
                  ${examples.map((example) => `
                    <p><b>${escapeHtml(example.name || "群友")}</b><span>${escapeHtml(example.text || "")}</span><small>${escapeHtml(example.time || "")}</small></p>
                  `).join("")}
                </div>
              ` : ""}
            </div>
          </article>
        `;
      }).join("")}
    </div>
  `;
}

function renderFeatureMatrix() {
  const groups = [
    ["陪伴", ["enable_mai_style_integration", "enable_expression_learning", "enable_response_self_review", "enable_dialogue_episode_memory"]],
    ["群聊", ["enable_group_companion", "enable_group_context_injection", "enable_group_injection_guard", "enable_group_slang_learning", "enable_group_topic_threads", "enable_group_relationship_graph"]],
    ["记忆", ["enable_companion_memory", "enable_open_loop_tracking", "enable_livingmemory_integration"]],
    ["主动联动", ["enable_proactive_quote_trigger_message", "enable_unanswered_screen_peek_followup", "enable_bilibili_integration", "enable_bilibili_boredom_watch", "enable_news_integration", "enable_ai_daily_watch", "enable_private_reading_integration", "enable_private_reading_boredom_read", "enable_private_reading_ask_recommendation", "enable_creative_writing", "creative_hidden_mode"]],
  ];
  $("#featureMatrix").innerHTML = groups.map(([label, keys]) => `
    <section>
      <h3>${escapeHtml(label)}</h3>
      <div class="feature-dot-list">
        ${keys.filter(visibleConfigKey).map((key) => `<span class="feature-dot ${state.overview?.features?.[key] ? "on" : "off"}" title="${escapeHtml(`${featureLabel(key)}：${featureDescription(key)} (${key})`)}">${escapeHtml(featureLabel(key))}</span>`).join("")}
      </div>
    </section>
  `).join("");
}

function renderActivityHeatmap() {
  const now = Math.floor(Date.now() / 1000);
  const buckets = Array.from({ length: 24 }, (_, hour) => ({ hour, count: 0 }));
  [...state.users, ...state.groups].forEach((item) => {
    const ts = Number(item.last_seen_ts || 0);
    if (!ts || now - ts > 86400) return;
    buckets[new Date(ts * 1000).getHours()].count += 1;
  });
  const max = Math.max(1, ...buckets.map((item) => item.count));
  $("#activityHeatmap").innerHTML = buckets.map((item) => {
    const level = Math.min(4, Math.ceil((item.count / max) * 4));
    return `<span class="heat level-${level}" title="${item.hour}:00-${item.hour + 1}:00 · ${item.count}">${item.hour}</span>`;
  }).join("");
}

function horizontalBars(data, total) {
  return Object.entries(data).map(([label, count]) => {
    const pct = Math.round((count / total) * 100);
    return `
      <div class="meter-row">
        <span>${escapeHtml(label)}</span>
        <div class="meter"><i style="width:${pct}%"></i></div>
        <b>${escapeHtml(count)}</b>
      </div>
    `;
  }).join("");
}

function renderTokens() {
  const stats = state.tokenStats || {};
  const scope = tokenScopeData(stats);
  const totals = scope.totals || {};
  const totalTokens = Number(totals.total_tokens || 0);
  const calls = Number(totals.calls || 0);
  const errors = Number(totals.errors || 0);
  const estimatedRatio = Number(totals.estimated_ratio || 0);
  const cachedTokens = Number(totals.cached_tokens || 0);
  const cacheReadTokens = Number(totals.cache_read_tokens || 0);
  const cacheWriteTokens = Number(totals.cache_write_tokens || 0);
  const cachedRatio = Number(totals.cached_ratio || 0);
  const externalScope = externalTokenScopeData(stats, scope);
  const memoryPluginScope = memoryPluginTokenScopeData(stats, scope);
  const externalTokens = Number(externalScope?.totals?.total_tokens || 0);
  const memoryPluginTokens = Number(memoryPluginScope?.totals?.total_tokens || 0);
  const budget = stats.budget || {};
  const dailyLimit = Number(budget.limit || 0);
  const dailyUsed = Number(budget.used || 0);
  const softLimit = Number(budget.soft_limit || 0);
  const softRemaining = budget.soft_remaining == null ? null : Number(budget.soft_remaining || 0);
  const exemptUsed = Number(budget.exempt_used || 0);
  const dailyRemaining = budget.remaining == null ? null : Number(budget.remaining || 0);
  const tokenLoadingNote = state.tokenStatsPartial
    ? `<div class="empty small">已先显示总览中的 Token 摘要，正在加载完整明细...</div>`
    : (!state.tokenStats && !state.lazyLoaded.tokenStats ? `<div class="empty small">正在加载 Token 统计...</div>` : "");
  renderTokenToolbar(stats);
  const showHourlyTrend = state.tokenView === "total";
  const hourlyPanel = $("#tokenHourlyPanel");
  if (hourlyPanel) hourlyPanel.hidden = !showHourlyTrend;
  $("#tokenSummary").innerHTML = `${tokenLoadingNote}${tokenSummaryBoard({
    scope,
    externalScope,
    memoryPluginScope,
    totalTokens,
    externalTokens,
    memoryPluginTokens,
    totals,
    calls,
    errors,
    estimatedRatio,
    cachedTokens,
    cacheReadTokens,
    cacheWriteTokens,
    cachedRatio,
    dailyLimit,
    dailyUsed,
    dailyRemaining,
    softLimit,
    softRemaining,
    softEnabled: Boolean(budget.soft_enabled),
    softActive: Boolean(budget.soft_active),
    deferredCalls: Number(budget.deferred_calls || 0),
    proactiveMessages: exemptUsed,
  })}`;

  renderTokenChart("#tokenProviderChart", scope.providers || [], "暂无 Provider 消耗数据", (item) => item.key || "default");
  renderTokenChart("#tokenTaskChart", scope.tasks || [], "暂无任务消耗数据", (item) => tokenTaskLabel(item.key));
  if (showHourlyTrend) {
    renderTokenHourlyChart(scope.hours || []);
  } else {
    $("#tokenHourlyChart").innerHTML = "";
  }
  renderTokenDailyChart(stats.by_day || []);
  renderTokenDailyTable(stats.by_day_detail || stats.by_day || []);
  renderTokenProviderTable(scope.providers || []);
  renderTokenTaskTable(scope.tasks || []);
  renderTokenRecentTable(scope.recent || []);
  renderTokenExternalSessionTable(externalScope.sessions || []);
  renderTokenExternalRecentTable(externalScope.recent || []);
  renderTokenMemoryPluginSummary(memoryPluginScope, stats.memory_plugin || {});
  renderTokenMemoryPluginTaskTable(memoryPluginScope.tasks || [], memoryPluginScope.available !== false);
  renderTokenMemoryPluginProviderTable(memoryPluginScope.providers || [], memoryPluginScope.available !== false);
  renderTokenMemoryPluginRecentTable(memoryPluginScope.recent || [], memoryPluginScope.available !== false);
}

function tokenSummaryBoard({
  scope,
  externalScope,
  memoryPluginScope,
  totalTokens,
  externalTokens,
  memoryPluginTokens,
  totals,
  calls,
  errors,
  estimatedRatio,
  cachedTokens,
  cacheReadTokens,
  cacheWriteTokens,
  cachedRatio,
  dailyLimit,
  dailyUsed,
  dailyRemaining,
  softLimit,
  softRemaining,
  softEnabled,
  softActive,
  deferredCalls,
  proactiveMessages,
}) {
  const avgTokens = Math.round(Number(totals.avg_tokens || 0));
  const avgLatency = Math.round(Number(totals.avg_latency_ms || 0));
  const hardPercent = dailyLimit > 0 ? Math.min(100, Math.round((dailyUsed / Math.max(1, dailyLimit)) * 100)) : 0;
  const remainingValue = dailyRemaining == null
    ? (dailyLimit > 0 ? Math.max(0, dailyLimit - dailyUsed) : null)
    : dailyRemaining;
  const softUsed = softEnabled && softLimit > 0 && softRemaining != null ? Math.max(0, softLimit - softRemaining) : 0;
  const softPercent = softEnabled && softLimit > 0 ? Math.min(100, Math.round((softUsed / Math.max(1, softLimit)) * 100)) : 0;
  const scopeNote = scope.isToday
    ? (dailyLimit > 0 ? `硬限额 ${formatCompactNumber(dailyLimit)} · 剩 ${formatCompactNumber(remainingValue)}` : "今日不限额")
    : `${scope.mode === "total" ? "全部历史" : "选定日期"} · ${formatNumber(calls)} 次调用`;
  const hasKnownCacheStats = totalTokens > 0 && estimatedRatio < 1;
  return `
    <section class="token-summary-board">
      <article class="token-primary-card">
        <span>${escapeHtml(scope.label)}</span>
        <b>${escapeHtml(formatNumber(totalTokens))}</b>
        <small>${escapeHtml(scopeNote)}</small>
        ${scope.isToday && dailyLimit > 0 ? `<div class="token-progress"><i style="width:${hardPercent}%"></i></div>` : ""}
      </article>
      <article class="token-budget-card">
        <div class="token-budget-head">
          <span>预算</span>
          <b>${escapeHtml(scope.isToday && dailyLimit > 0 ? `${hardPercent}%` : "概览")}</b>
        </div>
        <div class="token-budget-lines">
          ${tokenBudgetLine("今日上限", dailyLimit > 0 ? formatCompactNumber(dailyLimit) : "不限", hardPercent, scope.isToday && dailyLimit > 0)}
          ${tokenBudgetLine("今日剩余", remainingValue == null ? "不限" : formatCompactNumber(remainingValue), dailyLimit > 0 ? 100 - hardPercent : 0, scope.isToday && dailyLimit > 0)}
          ${tokenBudgetLine(
            softActive ? "软限额接管" : "每日软限额",
            softEnabled && softLimit > 0 ? (softActive ? `暂缓 ${formatNumber(deferredCalls)} 次` : `剩 ${formatCompactNumber(softRemaining)}`) : "关闭",
            softPercent,
            softEnabled && softLimit > 0,
            softActive ? "warn" : "",
          )}
        </div>
      </article>
      <div class="token-metric-grid">
        ${tokenMetricCard(externalScope.label, formatNumber(externalTokens))}
        ${tokenMetricCard(memoryPluginScope.label, memoryPluginScope.available === false ? "未接入" : formatNumber(memoryPluginTokens))}
        ${tokenMetricCard("主动消息", formatCompactNumber(proactiveMessages))}
        ${hasKnownCacheStats ? tokenMetricCard("缓存命中", `${formatCompactNumber(cachedTokens || cacheReadTokens)} · ${Math.round(cachedRatio * 100)}%`) : ""}
        ${cacheReadTokens > 0 || cacheWriteTokens > 0 ? tokenMetricCard("缓存读 / 写", `${formatCompactNumber(cacheReadTokens)} / ${formatCompactNumber(cacheWriteTokens)}`) : ""}
        ${tokenMetricCard("调用次数", formatNumber(calls))}
        ${tokenMetricCard("平均 Token", formatNumber(avgTokens))}
        ${tokenMetricCard("平均延迟", `${formatNumber(avgLatency)} ms`)}
        ${tokenMetricCard("估算 / 失败", `${Math.round(estimatedRatio * 100)}% · ${formatNumber(errors)}`)}
      </div>
    </section>
  `;
}

function tokenBudgetLine(label, value, percent, active, tone = "") {
  return `
    <div class="token-budget-line ${active ? "active" : "off"} ${escapeHtml(tone)}">
      <span>${escapeHtml(label)}</span>
      <b>${escapeHtml(value)}</b>
      <em><i style="width:${Math.max(0, Math.min(100, Number(percent || 0)))}%"></i></em>
    </div>
  `;
}

function tokenMetricCard(label, value) {
  return `
    <span class="token-metric-card">
      <small>${escapeHtml(label)}</small>
      <b>${escapeHtml(value)}</b>
    </span>
  `;
}

function externalTokenScopeData(stats, pluginScope) {
  const external = stats.external || {};
  const view = state.tokenView || "today";
  const today = stats.budget?.day || todayKeyLocal();
  if (view === "total") {
    return {
      label: "非插件累计",
      totals: external.totals || {},
      sessions: external.by_session || [],
      recent: external.recent || [],
    };
  }
  const selectedDay = pluginScope?.mode === "date" ? state.tokenDate : today;
  const dayRows = external.by_day_detail || external.by_day || [];
  const day = dayRows.find((item) => String(item.key || "") === selectedDay) || { key: selectedDay };
  return {
    label: selectedDay === today ? "非插件今日" : "非插件同日",
    totals: day,
    sessions: day.sessions || [],
    recent: (external.recent || []).filter((item) => recentItemDayKey(item) === selectedDay),
  };
}

function memoryPluginTokenScopeData(stats, pluginScope) {
  const plugin = stats.memory_plugin || {};
  const view = state.tokenView || "today";
  const today = stats.budget?.day || todayKeyLocal();
  const displayName = plugin.display_name || "我会牢牢记住你";
  const available = plugin.available !== false;
  if (view === "total") {
    return {
      mode: "total",
      label: `${displayName}累计`,
      displayName,
      available,
      totals: plugin.totals || {},
      providers: plugin.by_provider || [],
      tasks: plugin.by_task || [],
      recent: plugin.recent || [],
    };
  }
  const selectedDay = pluginScope?.mode === "date" ? state.tokenDate : today;
  const dayRows = plugin.by_day_detail || plugin.by_day || [];
  const day = dayRows.find((item) => String(item.key || "") === selectedDay) || { key: selectedDay };
  return {
    mode: pluginScope?.mode || "today",
    label: selectedDay === today ? `${displayName}今日` : `${displayName}同日`,
    displayName,
    available,
    totals: day,
    providers: day.providers || [],
    tasks: day.tasks || [],
    recent: (plugin.recent || []).filter((item) => recentItemDayKey(item) === selectedDay),
  };
}

function tokenScopeData(stats) {
  const view = state.tokenView || "today";
  const today = stats.budget?.day || todayKeyLocal();
  const dayRows = stats.by_day_detail || stats.by_day || [];
  const availableDates = dayRows.map((item) => String(item.key || "")).filter(Boolean);
  if (!state.tokenDate || !availableDates.includes(state.tokenDate)) {
    state.tokenDate = availableDates.includes(today) ? today : (availableDates[availableDates.length - 1] || today);
  }
  if (view === "total") {
    return {
      mode: "total",
      label: "累计 Token",
      totals: stats.totals || {},
      providers: stats.by_provider || [],
      tasks: stats.by_task || [],
      hours: stats.by_hour || [],
      recent: stats.recent || [],
      isToday: false,
    };
  }
  const selectedDay = view === "date" ? state.tokenDate : today;
  const day = dayRows.find((item) => String(item.key || "") === selectedDay) || { key: selectedDay };
  const recent = (stats.recent || []).filter((item) => recentItemDayKey(item) === selectedDay);
  const hours = (stats.by_hour || []).filter((item) => String(item.key || "").startsWith(`${selectedDay}T`));
  return {
    mode: view,
    label: selectedDay === today ? "今日 Token" : `${selectedDay} Token`,
    totals: day,
    providers: day.providers || [],
    tasks: day.tasks || [],
    hours,
    recent,
    isToday: selectedDay === today,
  };
}

function renderTokenToolbar(stats) {
  const dayRows = stats.by_day_detail || stats.by_day || [];
  const dates = dayRows.map((item) => String(item.key || "")).filter(Boolean);
  const fallbackDate = todayKeyLocal();
  const select = $("#tokenDateSelect");
  if (select) {
    select.innerHTML = dates.length
      ? dates.slice().reverse().map((date) => `<option value="${escapeHtml(date)}" ${date === state.tokenDate ? "selected" : ""}>${escapeHtml(date)}</option>`).join("")
      : `<option value="${escapeHtml(fallbackDate)}">${escapeHtml(fallbackDate)}</option>`;
    select.disabled = state.tokenView !== "date";
  }
  document.querySelectorAll("[data-token-view]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.tokenView === state.tokenView);
  });
}

function todayKeyLocal() {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function recentItemDayKey(item) {
  const ts = Number(item?.ts || 0);
  if (ts > 0) {
    const date = new Date(ts * 1000);
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }
  return String(item?.time || "").slice(0, 10);
}

function renderTokenChart(selector, rows, emptyText, labeler) {
  const topRows = rows.slice(0, 8);
  const max = Math.max(1, ...topRows.map((item) => Number(item.total_tokens || 0)));
  $(selector).innerHTML = topRows.length
    ? topRows.map((item) => {
      const tokens = Number(item.total_tokens || 0);
      const pct = Math.max(2, Math.round((tokens / max) * 100));
      return `
        <div class="token-rank-row">
          <span title="${escapeHtml(labeler(item))}">${escapeHtml(labeler(item))}</span>
          <div class="meter"><i style="width:${pct}%"></i></div>
          <b>${escapeHtml(formatNumber(tokens))}</b>
        </div>
      `;
    }).join("")
    : `<div class="empty small">${escapeHtml(emptyText)}</div>`;
}

function renderTokenHourlyChart(rows) {
  const normalized = rows.slice(-48);
  const max = Math.max(1, ...normalized.map((item) => Number(item.total_tokens || 0)));
  $("#tokenHourlyChart").innerHTML = normalized.length
    ? tokenHourlySvg(normalized, max)
    : `<div class="empty small">暂无小时趋势数据</div>`;
}

function renderTokenDailyChart(rows) {
  const normalized = rows.slice(-30);
  const max = Math.max(1, ...normalized.map((item) => Number(item.total_tokens || 0)));
  $("#tokenDailyChart").innerHTML = normalized.length
    ? tokenDailySvg(normalized, max)
    : `<div class="empty small">暂无每日统计数据</div>`;
}

function tokenDailySvg(rows, max) {
  const chartHeight = 176;
  const chartTop = 12;
  const chartBottom = 36;
  const axisLeft = 74;
  const rightPad = 18;
  const barStep = 34;
  const width = Math.max(760, axisLeft + rightPad + rows.length * barStep);
  const height = chartHeight + chartTop + chartBottom;
  const plotHeight = chartHeight - chartTop;
  const plotBottom = chartTop + plotHeight;
  const labelStep = Math.max(1, Math.ceil(rows.length / 10));
  const ticks = [1, 0.75, 0.5, 0.25, 0];
  const grid = ticks.map((ratio) => {
    const y = chartTop + (1 - ratio) * plotHeight;
    const value = Math.round(max * ratio);
    return `
      <line class="token-grid-line" x1="${axisLeft}" y1="${y}" x2="${width - rightPad}" y2="${y}"></line>
      <text class="token-axis-label y" x="${axisLeft - 10}" y="${y + 4}">${escapeHtml(formatNumber(value))}</text>
    `;
  }).join("");
  const bars = rows.map((item, index) => {
    const tokens = Number(item.total_tokens || 0);
    const barHeight = Math.max(tokens > 0 ? 4 : 0, Math.round((tokens / max) * (plotHeight - 4)));
    const x = axisLeft + index * barStep + 8;
    const y = plotBottom - barHeight;
    const label = String(item.key || "");
    const labelNode = index % labelStep === 0 || index === rows.length - 1
      ? `<text class="token-axis-label x" x="${x + 8}" y="${height - 8}">${escapeHtml(label.slice(5))}</text>`
      : "";
    return `
      <g class="token-hour-group">
        <title>${escapeHtml(label)} · ${escapeHtml(formatNumber(tokens))} token · ${escapeHtml(item.calls || 0)} 次</title>
        <rect x="${x}" y="${y}" width="18" height="${barHeight}" rx="5"></rect>
        ${labelNode}
      </g>
    `;
  }).join("");
  return `
    <svg class="token-hourly-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="每日 Token 消耗趋势">
      <line class="token-axis-line" x1="${axisLeft}" y1="${chartTop}" x2="${axisLeft}" y2="${plotBottom}"></line>
      <line class="token-axis-line" x1="${axisLeft}" y1="${plotBottom}" x2="${width - rightPad}" y2="${plotBottom}"></line>
      ${grid}
      ${bars}
    </svg>
  `;
}

function tokenHourlySvg(rows, max) {
  const chartHeight = 176;
  const chartTop = 12;
  const chartBottom = 34;
  const axisLeft = 74;
  const rightPad = 18;
  const barStep = 30;
  const width = Math.max(760, axisLeft + rightPad + rows.length * barStep);
  const height = chartHeight + chartTop + chartBottom;
  const plotHeight = chartHeight - chartTop;
  const plotBottom = chartTop + plotHeight;
  const labelStep = Math.max(1, Math.ceil(rows.length / 12));
  const ticks = [1, 0.75, 0.5, 0.25, 0];
  const grid = ticks.map((ratio) => {
    const y = chartTop + (1 - ratio) * plotHeight;
    const value = Math.round(max * ratio);
    return `
      <line class="token-grid-line" x1="${axisLeft}" y1="${y}" x2="${width - rightPad}" y2="${y}"></line>
      <text class="token-axis-label y" x="${axisLeft - 10}" y="${y + 4}">${escapeHtml(formatNumber(value))}</text>
    `;
  }).join("");
  const bars = rows.map((item, index) => {
      const tokens = Number(item.total_tokens || 0);
    const barHeight = Math.max(tokens > 0 ? 4 : 0, Math.round((tokens / max) * (plotHeight - 4)));
      const label = formatHourKey(item.key);
    const x = axisLeft + index * barStep + 7;
    const y = plotBottom - barHeight;
    const labelNode = index % labelStep === 0 || index === rows.length - 1
      ? `<text class="token-axis-label x" x="${x + 8}" y="${height - 8}">${escapeHtml(label.slice(-5))}</text>`
      : "";
      return `
      <g class="token-hour-group">
        <title>${escapeHtml(label)} · ${escapeHtml(formatNumber(tokens))} token · ${escapeHtml(item.calls || 0)} 次</title>
        <rect x="${x}" y="${y}" width="16" height="${barHeight}" rx="5"></rect>
        ${labelNode}
      </g>
      `;
  }).join("");
  return `
    <svg class="token-hourly-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="近 48 小时 Token 消耗趋势">
      <line class="token-axis-line" x1="${axisLeft}" y1="${chartTop}" x2="${axisLeft}" y2="${plotBottom}"></line>
      <line class="token-axis-line" x1="${axisLeft}" y1="${plotBottom}" x2="${width - rightPad}" y2="${plotBottom}"></line>
      ${grid}
      ${bars}
    </svg>
  `;
}

function renderTokenProviderTable(rows) {
  $("#tokenProviderTable").innerHTML = tokenTable(
    ["Provider", "总 Token", "缓存", "输入", "输出", "调用", "估算", "平均延迟"],
    rows,
    (item) => [
      item.key || "default",
      formatNumber(item.total_tokens),
      tokenCacheText(item),
      formatNumber(item.prompt_tokens),
      formatNumber(item.completion_tokens),
      formatNumber(item.calls),
      `${Math.round(Number(item.estimated_ratio || 0) * 100)}%`,
      `${formatNumber(Math.round(Number(item.avg_latency_ms || 0)))} ms`,
    ],
    "暂无 Provider 明细"
  );
}

function renderTokenTaskTable(rows) {
  $("#tokenTaskTable").innerHTML = tokenTable(
    ["任务", "总 Token", "缓存", "输入", "输出", "调用", "失败", "平均 Token"],
    rows,
    (item) => [
      tokenTaskLabel(item.key),
      formatNumber(item.total_tokens),
      tokenCacheText(item),
      formatNumber(item.prompt_tokens),
      formatNumber(item.completion_tokens),
      formatNumber(item.calls),
      formatNumber(item.errors),
      formatNumber(Math.round(Number(item.avg_tokens || 0))),
    ],
    "暂无任务明细"
  );
}

function renderTokenDailyTable(rows) {
  $("#tokenDailyTable").innerHTML = tokenTable(
    ["日期", "总 Token", "调用", "失败", "主要任务", "主要 Provider"],
    rows.slice().reverse(),
    (item) => [
      item.key || "-",
      formatNumber(item.total_tokens),
      formatNumber(item.calls),
      formatNumber(item.errors),
      tokenTopList(item.tasks, tokenTaskLabel),
      tokenTopList(item.providers, (key) => key || "default"),
    ],
    "暂无每日明细"
  );
}

function tokenTopList(rows, labeler) {
  if (!Array.isArray(rows) || !rows.length) return "-";
  return rows.slice(0, 3).map((item) => `${labeler(item.key)} ${formatCompactNumber(item.total_tokens)}`).join(" / ");
}

function renderTokenRecentTable(rows) {
  $("#tokenRecentTable").innerHTML = tokenTable(
    ["时间", "任务", "Provider", "Token", "缓存", "延迟", "状态"],
    rows,
    (item) => [
      formatRecentTime(item.ts, item.time),
      tokenTaskLabel(item.task),
      item.provider || "default",
      `${formatNumber(item.total_tokens)}${item.estimated ? " 估" : ""}`,
      tokenCacheText(item),
      `${formatNumber(Math.round(Number(item.elapsed_ms || item.latency_ms || 0)))} ms`,
      item.success ? "成功" : `失败 ${item.error || ""}`.trim(),
    ],
    "暂无最近调用"
  );
}

function renderTokenExternalSessionTable(rows) {
  $("#tokenExternalSessionTable").innerHTML = tokenTable(
    ["会话", "总 Token", "缓存", "调用", "失败", "平均延迟"],
    rows,
    (item) => [
      item.key || "-",
      formatNumber(item.total_tokens),
      tokenCacheText(item),
      formatNumber(item.calls),
      formatNumber(item.errors),
      `${formatNumber(Math.round(Number(item.avg_latency_ms || 0)))} ms`,
    ],
    "暂无 AstrBot 主链会话统计"
  );
}

function renderTokenExternalRecentTable(rows) {
  $("#tokenExternalRecentTable").innerHTML = tokenTable(
    ["时间", "会话", "发送者", "类型", "Provider", "Token", "缓存", "延迟", "状态"],
    rows,
    (item) => [
      formatRecentTime(item.ts, item.time),
      item.session || "-",
      item.sender || "-",
      item.message_type === "private" ? "私聊" : (item.message_type === "group" ? "群聊" : "-"),
      item.provider || "default",
      `${formatNumber(item.total_tokens)}${item.estimated ? " 估" : ""}`,
      tokenCacheText(item),
      `${formatNumber(Math.round(Number(item.elapsed_ms || item.latency_ms || 0)))} ms`,
      item.success ? "成功" : `失败 ${item.error || ""}`.trim(),
    ],
    "暂无 AstrBot 主链最近对话"
  );
}

function renderTokenMemoryPluginSummary(scope, plugin) {
  const box = $("#tokenMemoryPluginSummary");
  if (!box) return;
  const available = scope?.available !== false;
  if (!available) {
    box.innerHTML = `
      <div class="empty small">
        未检测到可读取的“${escapeHtml(plugin?.display_name || "我会牢牢记住你")}”Token 统计。
        ${plugin?.reason ? `原因：${escapeHtml(plugin.reason)}` : ""}
      </div>
    `;
    return;
  }
  const totals = scope?.totals || {};
  const displayName = scope?.displayName || plugin?.display_name || "我会牢牢记住你";
  const totalTokens = Number(totals.total_tokens || 0);
  const calls = Number(totals.calls || 0);
  const errors = Number(totals.errors || 0);
  const avgTokens = Math.round(Number(totals.avg_tokens || 0));
  const estimatedRatio = Math.round(Number(totals.estimated_ratio || 0) * 100);
  const updatedAt = plugin?.updated_at ? `最近更新：${plugin.updated_at}` : "还没有统计更新时间";
  box.innerHTML = `
    <div class="token-plugin-note">
      <b>${escapeHtml(displayName)}</b>
      <span>${escapeHtml(plugin?.note || "仅展示记忆插件自身模型消耗，不计入陪伴插件每日 Token 限额。")}</span>
    </div>
    <div class="token-plugin-stats">
      ${tokenMetricCard("Token", formatNumber(totalTokens))}
      ${tokenMetricCard("调用次数", formatNumber(calls))}
      ${tokenMetricCard("平均 Token", formatNumber(avgTokens))}
      ${tokenMetricCard("估算 / 失败", `${estimatedRatio}% · ${formatNumber(errors)}`)}
    </div>
    <p class="muted small">${escapeHtml(updatedAt)}</p>
  `;
}

function renderTokenMemoryPluginTaskTable(rows, available) {
  const box = $("#tokenMemoryPluginTaskTable");
  if (!box) return;
  if (!available) {
    box.innerHTML = `<div class="empty small">记忆插件 Token 统计未接入</div>`;
    return;
  }
  box.innerHTML = tokenTable(
    ["记忆任务", "总 Token", "输入", "输出", "调用", "失败"],
    rows,
    (item) => [
      tokenTaskLabel(item.key),
      formatNumber(item.total_tokens),
      formatNumber(item.prompt_tokens),
      formatNumber(item.completion_tokens),
      formatNumber(item.calls),
      formatNumber(item.errors),
    ],
    "暂无记忆插件任务明细"
  );
}

function renderTokenMemoryPluginProviderTable(rows, available) {
  const box = $("#tokenMemoryPluginProviderTable");
  if (!box) return;
  if (!available) {
    box.innerHTML = `<div class="empty small">记忆插件 Token 统计未接入</div>`;
    return;
  }
  box.innerHTML = tokenTable(
    ["Provider", "总 Token", "缓存", "调用", "估算", "平均延迟"],
    rows,
    (item) => [
      item.key || "default",
      formatNumber(item.total_tokens),
      tokenCacheText(item),
      formatNumber(item.calls),
      `${Math.round(Number(item.estimated_ratio || 0) * 100)}%`,
      `${formatNumber(Math.round(Number(item.avg_latency_ms || 0)))} ms`,
    ],
    "暂无记忆插件 Provider 明细"
  );
}

function renderTokenMemoryPluginRecentTable(rows, available) {
  const box = $("#tokenMemoryPluginRecentTable");
  if (!box) return;
  if (!available) {
    box.innerHTML = `<div class="empty small">记忆插件 Token 统计未接入</div>`;
    return;
  }
  box.innerHTML = tokenTable(
    ["时间", "记忆任务", "Provider", "Token", "缓存", "延迟", "状态"],
    rows,
    (item) => [
      formatRecentTime(item.ts, item.time),
      tokenTaskLabel(item.task),
      item.provider || "default",
      `${formatNumber(item.total_tokens)}${item.estimated ? " 估" : ""}`,
      tokenCacheText(item),
      `${formatNumber(Math.round(Number(item.elapsed_ms || item.latency_ms || 0)))} ms`,
      item.success ? "成功" : `失败 ${item.error || ""}`.trim(),
    ],
    "暂无记忆插件最近调用"
  );
}

function tokenCacheText(item) {
  const cached = Number(item?.cached_tokens || 0);
  const read = Number(item?.cache_read_tokens || 0);
  const write = Number(item?.cache_write_tokens || 0);
  const total = Number(item?.total_tokens || 0);
  if (cached <= 0 && read <= 0 && write <= 0) {
    const estimated = Boolean(item?.estimated) || Number(item?.estimated_ratio || 0) >= 1;
    return total > 0 && !estimated ? "0 · 0%" : "-";
  }
  const ratio = Number(item?.cached_ratio || 0) || (total > 0 ? (cached || read) / total : 0);
  const hit = cached || read;
  const parts = [`命中 ${formatCompactNumber(hit)}`];
  if (ratio > 0) parts.push(`${Math.round(ratio * 100)}%`);
  if (read > 0 || write > 0) parts.push(`读/写 ${formatCompactNumber(read)}/${formatCompactNumber(write)}`);
  return parts.join(" · ");
}

function tokenTable(headers, rows, mapper, emptyText) {
  if (!rows.length) return `<div class="empty small">${escapeHtml(emptyText)}</div>`;
  return `
    <table>
      <thead><tr>${headers.map((item) => `<th>${escapeHtml(item)}</th>`).join("")}</tr></thead>
      <tbody>
        ${rows.map((row) => `<tr>${mapper(row).map((value) => `<td>${escapeHtml(value)}</td>`).join("")}</tr>`).join("")}
      </tbody>
    </table>
  `;
}

function tokenTaskLabel(key) {
  const normalized = String(key || "").trim();
  if (!normalized) return "其他调用";
  if (tokenTaskLabels[normalized]) return tokenTaskLabels[normalized];
  if (normalized.startsWith("qzone_") && normalized.endsWith("_photo_prompt")) return "空间配图提示";
  if (normalized.startsWith("qzone_")) return "QQ 空间任务";
  if (normalized.startsWith("astrbot_")) return "AstrBot 主回复";
  if (normalized.startsWith("private_image_")) return "私聊图片处理";
  if (normalized.startsWith("web_exploration_")) return "主动搜索";
  return normalized;
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString("zh-CN");
}

function formatPercent(value) {
  const num = Number(value || 0);
  return `${Math.round(num * 100)}%`;
}

function formatHourKey(key) {
  const text = String(key || "");
  if (!text.includes("T")) return text || "-";
  return text.replace("T", " ");
}

function formatRecentTime(ts, fallback) {
  const num = Number(ts || 0);
  if (!num) return fallback || "-";
  return new Date(num * 1000).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function renderUsers() {
  const keyword = ($("#userFilter").value || "").trim().toLowerCase();
  const rows = state.users.filter((user) => {
    const text = `${user.user_id} ${user.nickname} ${user.umo}`.toLowerCase();
    return !keyword || text.includes(keyword);
  });
  $("#userRows").innerHTML = rows.length
    ? rows.map((user) => `
      <tr data-user-id="${escapeHtml(user.user_id)}" class="${user.user_id === state.selectedUserId ? "is-selected" : ""}">
        <td class="user-cell identity"><strong title="${escapeHtml(user.display_name || user.nickname || user.user_id)}">${escapeHtml(user.display_name || user.nickname || user.user_id)}</strong>${user.is_qq_user ? "" : ` <span class="badge off">非 QQ</span>`}${Array.isArray(user.alias_user_ids) && user.alias_user_ids.length ? ` <span class="badge ok" title="${escapeHtml(user.alias_user_ids.join("\\n"))}">已合并 ${escapeHtml(user.alias_user_ids.length)} 个身份</span>` : ""}<br><span class="user-id-line"><span class="muted mono" title="${escapeHtml(user.user_id)}">${escapeHtml(user.user_id)}</span><button type="button" class="copy-id-btn" data-copy-user-id="${escapeHtml(user.user_id)}">复制</button></span></td>
        <td class="user-cell relation"><span class="badge ${user.enabled ? "" : "off"}">${escapeHtml(user.enabled ? "启用" : "停用")}</span> <span class="badge">${escapeHtml(user.relationship_role_label || "朋友")}</span> <span class="muted">${escapeHtml(user.relationship_stage || "未分层")}</span><br><span>分数 ${escapeHtml(user.relationship_score)}</span></td>
        <td class="user-cell compact">入站 ${escapeHtml(user.inbound_count)} · 回复 ${escapeHtml(user.reply_count)}<br><span class="muted">记忆 ${escapeHtml(user.memory_items)} 条</span></td>
        <td class="user-cell proactive"><span>今日 ${escapeHtml(user.sent_today)} · 总计 ${escapeHtml(user.proactive_sent_count)}</span><br><span class="muted truncate" title="${escapeHtml(user.next_proactive || "")}">${escapeHtml(user.next_proactive)}</span></td>
        <td class="user-cell recent">${escapeHtml(user.last_seen)}<br><span class="muted">上次主动 ${escapeHtml(user.last_sent)}</span></td>
        <td class="user-cell action"><button type="button" class="table-action ${user.enabled ? "danger-outline" : ""}" data-user-toggle="${escapeHtml(user.user_id)}">${escapeHtml(user.enabled ? "停用" : "启用")}</button></td>
      </tr>
    `).join("")
    : `<tr><td class="empty" colspan="6">暂无私聊对象</td></tr>`;
  document.querySelectorAll("[data-user-toggle]").forEach((button) => {
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      const user = state.users.find((item) => item.user_id === button.dataset.userToggle);
      if (!user) return;
      await runAction(() => postJson("/user/update", {
        user_id: user.user_id,
        enabled: !user.enabled,
      }), !user.enabled ? "已启用私聊对象" : "已停用私聊对象", button);
    });
  });
  document.querySelectorAll("[data-copy-user-id]").forEach((button) => {
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      await copyTextToClipboard(button.dataset.copyUserId || "", "已复制用户 ID");
    });
  });
  document.querySelectorAll("[data-user-id]").forEach((row) => {
    row.addEventListener("click", async () => {
      state.selectedUserId = row.dataset.userId;
      renderUsers();
      await renderUserDetail(true);
    });
  });
  renderUserDetail();
}

async function renderUserDetail(forceFetch = false) {
  const box = $("#userDetail");
  if (!state.selectedUserId) {
    box.innerHTML = "";
    return;
  }
  let detail = state.users.find((user) => user.user_id === state.selectedUserId);
  if (forceFetch || !detail?.formatted) {
    try {
      detail = await fetchJson(`/user?user_id=${encodeURIComponent(state.selectedUserId)}`);
    } catch (error) {
      box.innerHTML = `<p class="muted">详情读取失败：${escapeHtml(error.message)}</p>`;
      return;
    }
  }
  box.innerHTML = `
    <div class="toolbar">
      <button data-user-action="toggle">${escapeHtml(detail.enabled ? "停用私聊陪伴" : "启用私聊陪伴")}</button>
      <button data-user-action="reset_daily">重置今日额度</button>
      <button data-user-action="clear_schedule">清空主动计划</button>
      <button data-user-action="clear_emotion_state">重置情绪状态</button>
      <button data-user-action="clear_learning" class="danger">清空学习记忆</button>
      <button data-user-action="delete" class="danger">删除私聊用户</button>
    </div>
    <form id="userEditForm" class="inline-form">
      <label>称呼 <input name="nickname" value="${escapeHtml(detail.nickname || "")}" placeholder="例如 主人 / 名字" /></label>
      <label>语气 <input name="style" value="${escapeHtml(detail.style || "")}" placeholder="温柔 / 活泼 / 工作" /></label>
      <label>关系角色
        <select name="relationship_role">
          <option value="owner" ${detail.relationship_role === "owner" ? "selected" : ""}>主人</option>
          <option value="friend" ${detail.relationship_role !== "owner" ? "selected" : ""}>朋友</option>
        </select>
      </label>
      <label>每日主动 <input name="proactive_daily_limit" type="number" min="-1" max="30" step="1" value="${escapeHtml(detail.proactive_daily_limit ?? -1)}" /></label>
      <button type="submit">保存</button>
    </form>
    <div class="visual-strip">
      ${scoreGauge("关系分", detail.relationship_score || 0, -20, 40)}
      ${scoreGauge("今日主动", detail.sent_today || 0, 0, Math.max(1, detail.effective_daily_limit || state.overview?.private?.max_daily_messages || 8))}
      ${miniStat("片段", detail.dialogue_episode_count || (detail.dialogue_episodes || []).length)}
      ${miniStat("未完话头", Array.isArray(detail.open_loops) ? activeOpenLoopItems(detail.open_loops).length : (detail.open_loop_count || 0))}
      ${miniStat("习惯", detail.habit_count || detail.behavior_habits?.items?.length || 0)}
    </div>
    <div class="detail-grid">
      ${detailBlock("关系和主动", detail.formatted?.relationship || "", [["角色", detail.relationship_role_label || ""], ["有效主动上限", `${detail.effective_daily_limit ?? "-"} / 天`], ["下次主动", detail.formatted?.next_proactive || detail.next_proactive], ["动作偏好", detail.formatted?.action_affinity || ""]])}
      ${emotionGateBlock(detail)}
      ${userWorldbookBlock(detail.worldbook_member)}
      ${detailBlock("行为习惯", detail.behavior_habits?.updated_at ? `更新于 ${detail.behavior_habits.updated_at}` : "", userHabitPairs(detail.behavior_habits))}
      ${detailBlock("最近对话", "", [["用户消息", detail.last_user_message || ""], ["陪伴回复", detail.last_companion_message || ""]])}
      ${detailBlock("对话片段", "", (detail.dialogue_episodes || []).map((item, index) => [`#${index + 1}`, item.summary || item.title || JSON.stringify(item)]))}
      ${renderExpressionProfileBlock(detail)}
      ${renderOpenLoopBlock(detail)}
    </div>
  `;
  bindUserActions(detail);
}

function userWorldbookBlock(item) {
  if (!item) {
    return `
      <section class="detail-block user-worldbook-block">
        <h2>关系网词条</h2>
        <div class="empty small">暂无对应关系节点</div>
      </section>
    `;
  }
  const aliases = Array.isArray(item.aliases) ? item.aliases : [];
  const observed = Array.isArray(item.observed_names) ? item.observed_names : [];
  const memories = Array.isArray(item.important_memories) ? item.important_memories : [];
  const chips = [
    ...aliases.map((name) => `别名：${name}`),
    ...observed.map((name) => `群名片：${name}`),
    ...(Array.isArray(item.external_ids) ? item.external_ids.map((id) => `外部身份：${id}`) : []),
  ].slice(0, 12);
  const previewItems = worldbookMemberPreviewItems(item, memories);
  return `
    <section class="detail-block user-worldbook-block">
      <div class="user-worldbook-head">
        <div>
          <h2>关系网词条</h2>
          <p>${escapeHtml(item.name || item.user_id || "未命名成员")}</p>
        </div>
        <span class="badge ${item.enabled ? "ok" : "off"}">${escapeHtml(item.enabled ? "启用" : "停用")}</span>
      </div>
      <div class="worldbook-compact-meta">
        <span>${escapeHtml(item.identity_type === "external" ? "外部身份" : "身份 QQ")} ${escapeHtml(item.user_id || "-")}</span>
        <span>优先级 ${escapeHtml(item.priority ?? "-")}</span>
        ${item.gender ? `<span>性别：${escapeHtml(item.gender)}</span>` : ""}
        ${item.pending_observation_count ? `<span>${escapeHtml(item.pending_observation_count)} 条待确认观察</span>` : ""}
      </div>
      <div class="worldbook-chip-row">
        ${chips.length ? chips.map((chip) => `<span>${escapeHtml(chip)}</span>`).join("") : `<span>暂无别名记录</span>`}
      </div>
      ${previewItems.length ? `
        <div class="worldbook-member-preview-list">
          ${previewItems.map(([label, value]) => `<p><b>${escapeHtml(label)}</b><span>${escapeHtml(value)}</span></p>`).join("")}
        </div>
      ` : `<div class="empty small">暂无词条正文或记忆</div>`}
      ${memories.length ? `
        <div class="user-worldbook-memory-list">
          ${memories.slice(0, 4).map((memory) => `
            <p><b>${escapeHtml(memory.title || "重要记忆")}</b><span>${escapeHtml(memory.content || "")}</span></p>
          `).join("")}
        </div>
      ` : ""}
    </section>
  `;
}

function emotionGateBlock(detail) {
  const rel = detail.relationship_state && typeof detail.relationship_state === "object" ? detail.relationship_state : {};
  const intent = detail.intent_profile && typeof detail.intent_profile === "object" ? detail.intent_profile : {};
  const mode = rel.mode || "normal";
  const moodScore = Number(rel.mood_score || 0);
  const hurtUntil = Number(rel.hurt_until || 0);
  const now = Math.floor(Date.now() / 1000);
  const remaining = hurtUntil > now ? `${Math.ceil((hurtUntil - now) / 60)} 分钟` : "无";
  const pairs = [
    ["状态", mode],
    ["余波值", moodScore ? String(moodScore) : "0"],
    ["收敛轮数", rel.silence_turns || 0],
    ["剩余收敛", remaining],
    ["上次事件", rel.last_emotion_event || intent.emotion_event || "neutral"],
    ["对象", rel.last_emotion_target || intent.emotion_target || "-"],
    ["规则", rel.last_emotion_rule || intent.emotion_rule || "-"],
    ["强度", rel.last_emotion_intensity ?? intent.emotion_intensity ?? 0],
    ["意图置信度", rel.last_intent_confidence ?? intent.confidence ?? "-"],
    ["情绪置信度", rel.last_emotion_confidence ?? intent.emotion_confidence ?? "-"],
    ["原因", rel.last_emotion_reason || intent.emotion_reason || rel.last_hurt_reason || ""],
  ];
  const preText = rel.last_hurt_text ? `最近留下余波的话：${rel.last_hurt_text}` : "";
  return detailBlock("情绪余波", preText, pairs);
}

function userHabitPairs(habits) {
  const items = Array.isArray(habits?.items) ? habits.items : [];
  return items.length
    ? items.map((item) => [
      `${item.bucket || "-"} ${item.avg_time || ""}`,
      `${item.category || "习惯"}｜${item.topic || "-"}｜${item.count || 0} 次${item.last_seen ? `｜最近 ${item.last_seen}` : ""}${item.last_seen_text ? `｜${item.last_seen_text}` : ""}`,
    ])
    : [["-", habits?.enabled ? "暂无达到阈值的习惯样本" : "习惯学习未开启"]];
}

function normalizeOpenLoopItem(item) {
  const raw = item && typeof item === "object" ? item : {};
  return {
    text: String(raw.text || raw.topic || raw.summary || "").trim(),
    status: String(raw.status || "待自然延续").trim() || "待自然延续",
    source: String(raw.source || "").trim(),
  };
}

function activeOpenLoopItems(items) {
  return (Array.isArray(items) ? items : [])
    .map(normalizeOpenLoopItem)
    .filter((item) => item.text && !["已完成", "已取消"].includes(item.status));
}

function resolvedOpenLoopItems(items) {
  return (Array.isArray(items) ? items : [])
    .map(normalizeOpenLoopItem)
    .filter((item) => item.text && ["已完成", "已取消"].includes(item.status));
}

function openLoopStatusClass(status) {
  if (status === "已完成") return "ok";
  if (status === "已取消") return "off";
  return "";
}

function openLoopStatusText(item) {
  const parts = [item.status || "待自然延续"];
  if (item.source === "dialogue_episode") parts.push("片段整理");
  if (item.source === "user_message") parts.push("即时记录");
  return parts.join("｜");
}

function renderOpenLoopBlock(detail) {
  const activeItems = activeOpenLoopItems(detail?.open_loops);
  const resolvedItems = resolvedOpenLoopItems(detail?.open_loops);
  return `
    <section class="detail-block open-loop-block">
      <div class="detail-block-head">
        <h2>未完话头</h2>
        ${activeItems.length ? `
          <div class="open-loop-actions">
            <button type="button" class="danger-outline" data-open-loop-clear="${escapeHtml(detail.user_id || "")}">清空未完话头</button>
          </div>
        ` : ""}
      </div>
      ${activeItems.length ? `
        <div class="open-loop-list">
          ${activeItems.map((item, index) => `
            <article class="open-loop-item">
              <div class="open-loop-main">
                <b class="open-loop-text">${escapeHtml(item.text)}</b>
                <span class="badge ${openLoopStatusClass(item.status)}">${escapeHtml(openLoopStatusText(item))}</span>
              </div>
              <button type="button" class="danger-outline" data-open-loop-remove="${escapeHtml(item.text)}" data-open-loop-index="${index}">删除</button>
            </article>
          `).join("")}
        </div>
      ` : `<div class="empty small">暂无未完话头</div>`}
      ${resolvedItems.length ? `
        <details class="open-loop-archive">
          <summary>已结束话头 ${escapeHtml(resolvedItems.length)}</summary>
          <div class="open-loop-list archived">
            ${resolvedItems.map((item) => `
              <article class="open-loop-item archived">
                <div class="open-loop-main">
                  <b class="open-loop-text">${escapeHtml(item.text)}</b>
                  <span class="badge ${openLoopStatusClass(item.status)}">${escapeHtml(openLoopStatusText(item))}</span>
                </div>
              </article>
            `).join("")}
          </div>
        </details>
      ` : ""}
    </section>
  `;
}

function renderExpressionProfileBlock(detail) {
  const profile = detail?.expression_profile && typeof detail.expression_profile === "object" ? detail.expression_profile : {};
  const pending = Array.isArray(profile.pending_samples) ? profile.pending_samples : [];
  const samples = Array.isArray(profile.samples) ? profile.samples : [];
  const endings = Array.isArray(profile.endings) ? profile.endings.filter(Boolean) : [];
  const phrases = Array.isArray(profile.recent_phrases) ? profile.recent_phrases.filter(Boolean) : [];
  return `
    <section class="detail-block expression-profile-block">
      <div class="detail-block-head">
        <h2>表达画像</h2>
        <div class="open-loop-actions">
          <span class="badge">${escapeHtml(profile.mode || "balanced")}</span>
          ${profile.manual_review ? `<span class="badge ok">手动审核</span>` : ""}
          ${pending.length ? `<button type="button" class="danger-outline" data-expression-action="clear_pending">清空待审</button>` : ""}
        </div>
      </div>
      <p class="muted small">已入库 ${escapeHtml(profile.sample_count || samples.length || 0)} 条 · 待审核 ${escapeHtml(profile.pending_count || pending.length || 0)} 条 · ${profile.style_review ? "发送前审核开启" : "发送前审核关闭"}</p>
      ${profile.prompt_preview ? `<pre class="compact-pre">${escapeHtml(profile.prompt_preview)}</pre>` : `<div class="empty small">暂无足够表达样本</div>`}
      ${endings.length || phrases.length ? `
        <div class="worldbook-chip-row">
          ${endings.slice(0, 8).map((item) => `<span>句尾：${escapeHtml(item)}</span>`).join("")}
          ${phrases.slice(0, 8).map((item) => `<span>短句：${escapeHtml(item)}</span>`).join("")}
        </div>
      ` : ""}
      ${pending.length ? `
        <h3 class="subhead">待审核样本</h3>
        <div class="open-loop-list">
          ${pending.map((item, index) => expressionSampleItem(item, index, true)).join("")}
        </div>
      ` : `<div class="empty small">暂无待审核表达样本</div>`}
      ${samples.length ? `
        <details class="open-loop-archive">
          <summary>已入库样本 ${escapeHtml(samples.length)}</summary>
          <div class="open-loop-list archived">
            ${samples.map((item, index) => expressionSampleItem(item, index, false)).join("")}
          </div>
        </details>
      ` : ""}
    </section>
  `;
}

function expressionSampleItem(item, index, pending) {
  const text = item?.text || item?.phrase || item?.ending || "";
  const meta = [
    item?.time || "",
    item?.length ? `${item.length} 字` : "",
    item?.punctuation ? `标点 ${item.punctuation}` : "",
    item?.ending ? `句尾 ${item.ending}` : "",
  ].filter(Boolean).join("｜");
  return `
    <article class="open-loop-item ${pending ? "" : "archived"}">
      <div class="open-loop-main">
        <b class="open-loop-text">${escapeHtml(text || "空样本")}</b>
        <span class="badge">${escapeHtml(meta || `#${index + 1}`)}</span>
      </div>
      <div class="open-loop-actions">
        ${pending ? `<button type="button" data-expression-action="approve" data-expression-sample-id="${escapeHtml(item?.id || "")}" data-expression-sample-index="${escapeHtml(index)}">通过</button>` : ""}
        <button type="button" class="danger-outline" data-expression-action="${pending ? "reject" : "delete_sample"}" data-expression-sample-id="${escapeHtml(item?.id || "")}" data-expression-sample-index="${escapeHtml(index)}">删除</button>
      </div>
    </article>
  `;
}

function bindUserActions(detail) {
  const refreshSelectedUserDetail = async () => {
    if (state.selectedUserId === detail.user_id) {
      await renderUserDetail(true);
    }
  };
  $("#userEditForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const selectedRole = form.get("relationship_role");
    await runAction(() => postJson("/user/update", {
      user_id: detail.user_id,
      nickname: form.get("nickname"),
      style: form.get("style"),
      relationship_role: selectedRole,
      proactive_daily_limit: Number(form.get("proactive_daily_limit") || -1),
    }), "已保存私聊对象", event.submitter);
    await refreshSelectedUserDetail();
  });
  document.querySelectorAll("[data-user-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      const action = button.dataset.userAction;
      const body = { user_id: detail.user_id };
      if (action === "toggle") body.enabled = !detail.enabled;
      if (action === "reset_daily") body.reset_daily = true;
      if (action === "clear_schedule") body.clear_schedule = true;
      if (action === "clear_emotion_state") {
        if (!requireSecondClick(button, `user-clear-emotion:${detail.user_id}`, "再次点击重置该用户的情绪状态", "再次点击重置")) return;
        body.clear_emotion_state = true;
      }
      if (action === "clear_learning") {
        if (!requireSecondClick(button, `user-clear:${detail.user_id}`, "再次点击清空该用户的学习记忆", "再次点击清空")) return;
        body.clear_learning = true;
      }
      if (action === "delete") {
        if (!requireSecondClick(button, `user-delete:${detail.user_id}`, "再次点击删除该私聊用户、目标名单和相关映射", "再次点击删除")) return;
        await runAction(async () => {
          await postJson("/user/delete", body);
          state.selectedUserId = "";
          await loadAll();
        }, "已删除私聊用户", button);
        return;
      }
      await runAction(() => postJson("/user/update", body), "已更新私聊对象", button);
      await refreshSelectedUserDetail();
    });
  });
  document.querySelectorAll("[data-open-loop-remove]").forEach((button) => {
    button.addEventListener("click", async () => {
      const text = String(button.dataset.openLoopRemove || "").trim();
      if (!text) return;
      if (!requireSecondClick(button, `open-loop:${detail.user_id}:${text}`, "再次点击删除这条未完话头", "再次点击删除")) return;
      await runAction(
        () => postJson("/user/update", { user_id: detail.user_id, remove_open_loop_text: text }),
        "",
        button,
      );
      await refreshSelectedUserDetail();
    });
  });
  document.querySelectorAll("[data-open-loop-clear]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!requireSecondClick(button, `open-loop-clear:${detail.user_id}`, "再次点击清空该用户的未完话头", "再次点击清空")) return;
      await runAction(
        () => postJson("/user/update", { user_id: detail.user_id, clear_open_loops: true }),
        "",
        button,
      );
      await refreshSelectedUserDetail();
    });
  });
  document.querySelectorAll("[data-expression-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      const action = button.dataset.expressionAction || "";
      if (action === "clear_pending" && !requireSecondClick(button, `expression-clear:${detail.user_id}`, "再次点击清空待审核表达样本", "再次点击清空")) return;
      if (action === "delete_sample" && !requireSecondClick(button, `expression-delete:${detail.user_id}:${button.dataset.expressionSampleId || button.dataset.expressionSampleIndex || ""}`, "再次点击删除这条表达样本", "再次点击删除")) return;
      await runAction(
        () => postJson("/user/update", {
          user_id: detail.user_id,
          expression_action: action,
          sample_id: button.dataset.expressionSampleId || "",
          sample_index: Number(button.dataset.expressionSampleIndex || -1),
        }),
        action === "approve" ? "已通过表达样本" : "",
        button,
      );
      await refreshSelectedUserDetail();
    });
  });
}

function renderGroups() {
  const keyword = ($("#groupFilter").value || "").trim().toLowerCase();
  const rows = state.groups.filter((group) => {
    const haystack = [
      group.group_id,
      group.name,
      group.group_name,
      group.atmosphere?.mood,
      group.atmosphere?.last_summary,
    ].join(" ").toLowerCase();
    return !keyword || haystack.includes(keyword);
  });
  if (!rows.length) {
    state.selectedGroupId = "";
  } else if (!rows.some((group) => String(group.group_id) === String(state.selectedGroupId))) {
    state.selectedGroupId = rows[0].group_id;
  }
  const count = $("#groupListCount");
  if (count) {
    count.textContent = `${rows.length}/${state.groups.length} 个群`;
  }
  $("#groupRows").innerHTML = rows.length
    ? rows.map((group) => `
      <button type="button" data-group-id="${escapeHtml(group.group_id)}" class="group-card ${String(group.group_id) === String(state.selectedGroupId) ? "is-selected" : ""} ${group.enabled ? "" : "is-off"} ${group.allowed_by_mode ? "" : "is-blocked"}">
        <header>
          <span class="group-card-title">
            <b>${escapeHtml(groupDisplayName(group))}</b>
            <small>${escapeHtml(groupIdText(group))}</small>
          </span>
          <span class="badge ${group.enabled ? "" : "off"}">${escapeHtml(group.enabled ? "观测中" : "停用")}</span>
        </header>
        <p class="group-card-summary">${escapeHtml(group.atmosphere?.last_summary || group.atmosphere?.mood || "暂无群聊氛围摘要")}</p>
        <div class="group-card-metrics">
          <span>消息 <b>${escapeHtml(group.message_count || 0)}</b></span>
          <span>群友 <b>${escapeHtml(group.member_count || 0)}</b></span>
          <span>话题 <b>${escapeHtml(group.topic_count || 0)}</b></span>
        </div>
        ${groupWakeupCardLine(group)}
        <footer>
          <span class="${group.allowed_by_mode ? "" : "warn-text"}">${escapeHtml(group.allowed_by_mode ? "名单允许" : "名单拦截")}</span>
          <span>插话 ${escapeHtml(group.interject_today || 0)}</span>
          <span>${escapeHtml(group.last_seen || "暂无活跃")}</span>
        </footer>
      </button>
    `).join("")
    : `<div class="empty small">暂无群聊观测数据</div>`;
  document.querySelectorAll("[data-group-id]").forEach((row) => {
    row.addEventListener("click", async () => {
      state.selectedGroupId = row.dataset.groupId;
      renderGroups();
      await renderGroupDetail(true);
    });
  });
  renderGroupDetail();
}

function groupDisplayName(group) {
  const id = String(group?.group_id || "").trim();
  const name = String(group?.display_name || group?.name || group?.group_name || "").trim();
  if (name && name !== id && name !== `群 ${id}`) return name;
  return "未命名群聊";
}

function groupIdText(group) {
  const id = String(group?.group_id || "").trim();
  return id ? `群号 ${id}` : "群号未知";
}

function groupWakeupCardLine(group) {
  const wake = group.last_group_wakeup || {};
  const fatigue = group.wakeup_fatigue || {};
  if (!wake.word && !group.wakeup_log_count && !fatigue.value) return "";
  const wakeText = wake.word
    ? `${wake.strength_label || "唤醒"}｜${wake.word}｜${wake.time || ""}`
    : "暂无命中";
  const fatigueText = fatigue.label ? `疲劳 ${fatigue.label}${fatigue.value ? ` ${fatigue.value}/${fatigue.limit || "-"}` : ""}` : "";
  return `
    <div class="group-card-wakeup">
      <span>${escapeHtml(wakeText)}</span>
      <span>${escapeHtml(fatigueText)}</span>
    </div>
  `;
}

async function renderGroupDetail(forceFetch = false) {
  const box = $("#groupDetail");
  if (!state.selectedGroupId) {
    box.innerHTML = `<div class="empty">选择左侧群聊后查看话题线、关系网和插话状态</div>`;
    return;
  }
  let detail = state.groups.find((group) => String(group.group_id) === String(state.selectedGroupId));
  if (forceFetch || !detail?.formatted) {
    try {
      detail = await fetchJson(`/group?group_id=${encodeURIComponent(state.selectedGroupId)}`);
    } catch (error) {
      box.innerHTML = `<p class="muted">详情读取失败：${escapeHtml(error.message)}</p>`;
      return;
    }
  }
  const groupName = groupDisplayName(detail);
  box.innerHTML = `
    <div class="group-detail-hero">
      <div class="group-detail-title">
        <span class="eyebrow">群聊详情</span>
        <h2>${escapeHtml(groupName)}</h2>
        <div class="group-detail-status">
          <span>${escapeHtml(groupIdText(detail))}</span>
          <span class="${detail.enabled ? "ok-text" : "warn-text"}">${escapeHtml(detail.enabled ? "观测中" : "已停用")}</span>
          <span class="${detail.allowed_by_mode ? "ok-text" : "warn-text"}">${escapeHtml(detail.allowed_by_mode ? "名单允许" : "名单拦截")}</span>
          <span>最近 ${escapeHtml(detail.last_seen || "暂无")}</span>
        </div>
      </div>
      <div class="group-detail-actions">
        <button data-group-action="toggle">${escapeHtml(detail.enabled ? "停用" : "启用")}</button>
        <button data-group-action="reset_interjection">重置插话</button>
        <button data-group-action="clear_observation" class="danger">清空观测</button>
        <button data-group-action="delete" class="danger">删除群聊</button>
      </div>
    </div>
    <div class="visual-strip group-visual-strip">
      ${miniStat("消息", detail.message_count || 0)}
      ${miniStat("群友", detail.member_count || Object.keys(detail.members || {}).length)}
      ${miniStat("已识别", detail.recognized_member_count || 0)}
      ${miniStat("黑话", detail.slang_count || (detail.slang_terms || []).length)}
      ${miniStat("话题", detail.topic_count || (detail.topic_threads || []).length)}
    </div>
    <div class="detail-grid group-detail-grid">
      ${groupDetailPanel("群状态", groupStateOverview(detail), { wide: true, className: "group-state-panel" })}
      ${groupDetailPanel("黑话检视", groupSlangManagerView(detail.slang_items || []), { wide: true, className: "group-slang-panel", collapsed: true, meta: `${(detail.slang_items || []).length || 0} 条` })}
      ${groupDetailPanel("活跃群友", groupActiveMembersView(detail.members || {}), { className: "group-compact-panel" })}
      ${groupDetailPanel("插话反馈", groupInterjectionFeedbackView(detail), { className: "group-compact-panel" })}
      ${groupDetailPanel("消息活跃", groupMessageActivityView(detail.recent_messages || []), { wide: true, className: "group-message-panel" })}
      ${groupDetailPanel("群聊片段", groupEpisodesView(detail.group_episodes || []), { wide: true, collapsed: true, meta: `${(detail.group_episodes || []).length || 0} 条` })}
      ${groupDetailPanel("唤醒记录", groupWakeupPanel(detail), { wide: true, collapsed: true, meta: `${detail.wakeup_log_count || (detail.group_wakeup_logs || []).length || 0} 条` })}
      ${groupDetailPanel("话题线", groupTopicThreadsView(detail.topic_threads || []), { wide: true, collapsed: true, meta: `${(detail.topic_threads || []).length || 0} 条` })}
      ${groupDetailPanel("关系网", relationshipGraphView(detail.relationship_edges || {}, detail.members || {}), { wide: true, collapsed: true, meta: `${Object.keys(detail.relationship_edges || {}).length} 条关系` })}
    </div>
  `;
  bindGroupActions(detail);
}

function groupDetailPanel(title, content, options = {}) {
  const className = ["detail-block", options.wide ? "wide" : "", options.className || "", options.collapsed ? "group-collapsible" : ""]
    .filter(Boolean)
    .join(" ");
  if (options.collapsed) {
    return `
      <details class="${escapeHtml(className)}">
        <summary><h2>${escapeHtml(title)}</h2>${options.meta ? `<span>${escapeHtml(options.meta)}</span>` : ""}</summary>
        <div class="detail-block-body">${content}</div>
      </details>
    `;
  }
  return `<section class="${escapeHtml(className)}"><h2>${escapeHtml(title)}</h2>${content}</section>`;
}

function groupStateOverview(detail) {
  const atmosphere = detail.atmosphere || {};
  const chips = [
    ["群陪伴", detail.enabled ? "开启" : "停用", detail.enabled ? "ok" : "warn"],
    ["名单", detail.allowed_by_mode ? "允许" : "拦截", detail.allowed_by_mode ? "ok" : "warn"],
    ["气氛", atmosphere.mood || "暂无", ""],
    ["节奏", atmosphere.heat || atmosphere.pace || "暂无", ""],
    ["最近", detail.last_seen || "暂无", ""],
  ];
  const summary = atmosphere.last_summary || "这个群还没有形成稳定的氛围摘要。";
  return `
    <div class="group-state-overview">
      <div class="group-state-chips">
        ${chips.map(([label, value, tone]) => `<span class="${escapeHtml(tone || "")}"><small>${escapeHtml(label)}</small><b>${escapeHtml(value)}</b></span>`).join("")}
      </div>
      <p>${escapeHtml(summary)}</p>
      <div class="group-state-metrics">
        ${groupMetricTile("累计消息", detail.message_count || 0)}
        ${groupMetricTile("群友", detail.member_count || Object.keys(detail.members || {}).length)}
        ${groupMetricTile("已识别", detail.recognized_member_count || 0)}
        ${groupMetricTile("话题线", detail.topic_count || (detail.topic_threads || []).length)}
        ${groupMetricTile("黑话", detail.slang_count || (detail.slang_terms || []).length)}
      </div>
    </div>
  `;
}

function groupMetricTile(label, value) {
  return `<article><span>${escapeHtml(label)}</span><b>${escapeHtml(value)}</b></article>`;
}

function groupSlangTermsView(items) {
  const terms = (Array.isArray(items) ? items : [])
    .map((item) => ({
      text: slangTermText(item),
      count: Number(item?.count || 0),
    }))
    .filter((item) => item.text)
    .sort((a, b) => b.count - a.count)
    .slice(0, 18);
  if (!terms.length) return `<div class="empty small">暂无常用词</div>`;
  const max = Math.max(1, ...terms.map((item) => item.count));
  return `
    <div class="group-chip-cloud">
      ${terms.map((item) => `<span style="--weight:${Math.max(0.72, Math.min(1.12, 0.72 + item.count / max * 0.4)).toFixed(2)}">${escapeHtml(item.text)}${item.count ? `<small>${escapeHtml(item.count)}</small>` : ""}</span>`).join("")}
    </div>
  `;
}

function groupSlangManagerView(items) {
  const rows = Array.isArray(items) ? items : [];
  const counts = rows.reduce((acc, item) => {
    const status = item.status || "pending";
    acc[status] = (acc[status] || 0) + 1;
    return acc;
  }, {});
  const summary = rows.length
    ? `${rows.length} 个候选，${counts.injectable || 0} 个会注入，${counts.pending || 0} 个待释义`
    : "暂无已学习黑话";
  return `
    <div class="group-slang-manager">
      <header>
        <div>
          <p>${escapeHtml(summary)}</p>
          <small>这里展示群内学到的词、模型释义、联网参考和是否会进入提示词。</small>
        </div>
        <details class="group-slang-add">
          <summary>手动补一条</summary>
          <div class="group-slang-editor" data-slang-new>
            ${groupSlangInput("term", "词", "")}
            ${groupSlangInput("meaning", "含义", "")}
            ${groupSlangInput("usage", "用法", "")}
            ${groupSlangInput("type", "类型", "")}
            ${groupSlangInput("confidence", "置信度", "0.85", "number")}
            <button type="button" data-slang-add>保存</button>
          </div>
        </details>
      </header>
      ${rows.length ? `<div class="group-slang-list">${rows.map(groupSlangRow).join("")}</div>` : `<div class="empty small">还没有学到群内黑话。开启群黑话学习后，常见梗、简称和特殊称呼会出现在这里。</div>`}
    </div>
  `;
}

function groupSlangRow(item) {
  const sourceLabel = {
    llm_slang: "模型释义",
    explicit_correction: "群内纠正",
    manual: "手动校正",
  }[item.source] || item.source || "未释义";
  const statusClass = `status-${item.status || "pending"}`;
  const webMatch = Number(item.web_match || 0);
  return `
    <details class="group-slang-row ${escapeHtml(statusClass)}" data-slang-term="${escapeHtml(item.term || "")}">
      <summary class="group-slang-main">
        <div>
          <b>${escapeHtml(item.term || "-")}</b>
          <span>${escapeHtml(item.status_label || "待确认")}</span>
        </div>
        <p>${escapeHtml(item.meaning || "还没有稳定释义")}</p>
        <footer>
          <span>出现 ${escapeHtml(item.count || 0)} 次</span>
          <span>最近 ${escapeHtml(item.last_seen || "暂无")}</span>
          <span>${escapeHtml(sourceLabel)}</span>
          ${item.type ? `<span>${escapeHtml(item.type)}</span>` : ""}
          <span>置信度 ${escapeHtml(Math.round(Number(item.confidence || 0) * 100))}%</span>
          ${webMatch ? `<span>联网匹配 ${escapeHtml(Math.round(webMatch * 100))}%</span>` : ""}
        </footer>
      </summary>
      <div class="group-slang-body">
        ${item.usage || item.evidence || item.web_evidence || item.not_owner ? `
          <div class="group-slang-notes">
            ${item.usage ? `<p><b>用法</b>${escapeHtml(item.usage)}</p>` : ""}
            ${item.not_owner ? `<p><b>不是</b>${escapeHtml(item.not_owner)}</p>` : ""}
            ${item.evidence ? `<p><b>群内证据</b>${escapeHtml(item.evidence)}</p>` : ""}
            ${item.web_evidence ? `<p><b>联网参考</b>${escapeHtml(item.web_evidence)}</p>` : ""}
          </div>
        ` : `<div class="empty small">暂无更多证据或用法说明</div>`}
        <details class="group-slang-edit">
          <summary>编辑</summary>
          <div class="group-slang-editor">
            ${groupSlangInput("meaning", "含义", item.meaning || "")}
            ${groupSlangInput("usage", "用法", item.usage || "")}
            ${groupSlangInput("type", "类型", item.type || "")}
            ${groupSlangInput("not_owner", "不是谁", item.not_owner || "")}
            ${groupSlangInput("evidence", "群内证据", item.evidence || "")}
            ${groupSlangInput("web_evidence", "联网参考", item.web_evidence || "")}
            ${groupSlangInput("confidence", "置信度", item.confidence || 0.85, "number")}
            ${groupSlangInput("web_match", "联网匹配", item.web_match || 0, "number")}
            <div class="group-slang-actions">
              <button type="button" data-slang-save>保存校正</button>
              <button type="button" class="danger-outline" data-slang-delete>删除</button>
            </div>
          </div>
        </details>
      </div>
    </details>
  `;
}

function groupSlangInput(name, label, value, type = "text") {
  const multiline = ["meaning", "usage", "not_owner", "evidence", "web_evidence"].includes(name);
  if (multiline) {
    return `<label>${escapeHtml(label)} <textarea data-slang-field="${escapeHtml(name)}" rows="2">${escapeHtml(value ?? "")}</textarea></label>`;
  }
  const attrs = type === "number" ? ` type="number" min="0" max="1" step="0.01"` : "";
  return `<label>${escapeHtml(label)} <input data-slang-field="${escapeHtml(name)}"${attrs} value="${escapeHtml(value ?? "")}" /></label>`;
}

function groupSlangPayloadFromEditor(editor, groupId, term = "") {
  const payload = { group_id: groupId, term };
  editor.querySelectorAll("[data-slang-field]").forEach((input) => {
    const key = input.dataset.slangField;
    if (!key) return;
    payload[key] = input.value;
  });
  if (!payload.term) payload.term = "";
  return payload;
}

function groupActiveMembersView(members) {
  const items = Object.entries(members || {})
    .map(([id, raw]) => {
      const item = raw && typeof raw === "object" ? raw : {};
      return {
        id,
        name: item.identity_name || item.display_name || item.nickname || item.name || item.card || id,
        count: Number(item.count || item.message_count || 0),
        known: Boolean(item.identity_known),
        phrase: Array.isArray(item.recent_phrases) ? item.recent_phrases[0] : "",
      };
    })
    .sort((a, b) => b.count - a.count)
    .slice(0, 8);
  if (!items.length) return `<div class="empty small">暂无活跃群友</div>`;
  return `
    <div class="group-member-list">
      ${items.map((item) => `
        <article>
          <div class="relation-avatar">${escapeHtml(shortName(item.name, 2))}</div>
          <div>
            <b>${escapeHtml(item.name)}</b>
            <small>${escapeHtml(item.known ? "已识别" : item.id)}${item.phrase ? ` · ${escapeHtml(item.phrase)}` : ""}</small>
          </div>
          <span>${escapeHtml(item.count)}</span>
        </article>
      `).join("")}
    </div>
  `;
}

function groupInterjectionFeedbackView(detail) {
  const feedback = detail.interjection_feedback || {};
  const last = detail.last_bot_interjection || {};
  const replies = Number(feedback.replies_after || 0);
  const positive = Number(feedback.positive || 0);
  const negative = Number(feedback.negative || 0);
  const total = Math.max(1, positive + negative);
  return `
    <div class="group-feedback-panel">
      <div class="group-feedback-score">
        <article><span>后续回复</span><b>${escapeHtml(replies)}</b></article>
        <article><span>正向</span><b>${escapeHtml(positive)}</b></article>
        <article><span>负向</span><b>${escapeHtml(negative)}</b></article>
      </div>
      <div class="group-feedback-meter" title="正向 / 负向">
        <i style="width:${Math.round((positive / total) * 100)}%"></i>
      </div>
      ${cleanInterjectionText(last.text) ? `<p><span>上次插话</span>${escapeHtml(cleanInterjectionText(last.text))}</p>` : `<p class="muted">暂无最近插话内容</p>`}
      ${last.reason ? `<p><span>插话原因</span>${escapeHtml(last.reason)}</p>` : ""}
    </div>
  `;
}

function groupEpisodesView(episodes) {
  const items = (Array.isArray(episodes) ? episodes : []).slice(0, 5);
  if (!items.length) return `<div class="empty small">暂无群聊片段</div>`;
  return `
    <div class="group-episode-list">
      ${items.map((item, index) => {
        const title = item.title || `片段 ${index + 1}`;
        const summary = item.summary || item.content || JSON.stringify(item);
        return `<article><b>${escapeHtml(title)}</b><p>${escapeHtml(summary)}</p></article>`;
      }).join("")}
    </div>
  `;
}

function groupWakeupPanel(detail) {
  const logs = Array.isArray(detail.group_wakeup_logs) ? detail.group_wakeup_logs : [];
  const fatigue = detail.wakeup_fatigue || {};
  const high = fatigue.high_intensity || {};
  const ratio = Math.max(0, Math.min(100, Number(fatigue.ratio || 0) * 100));
  const last = detail.last_group_wakeup || {};
  const highTitle = high.active
    ? high.merge_active
      ? "合并等待中"
      : "仅降载中"
    : "未触发";
  const highMeta = high.active
    ? [
        `近期 ${Number(high.recent_wakeups || 0)}/${Number(high.threshold || 0)}`,
        high.merge_recent_floor ? `合并门槛 ${Number(high.merge_recent_floor)}` : "",
        high.reason ? `原因 ${high.reason}` : "",
        high.remaining_seconds ? `剩余 ${Number(high.remaining_seconds).toFixed(0)}s` : "",
      ].filter(Boolean).join(" · ")
    : "只有近期连续叫到 Bot 时才会合并等待";
  return `
    <div class="group-wakeup-overview">
      <article>
        <span>最近唤醒</span>
        <b>${escapeHtml(last.word || "暂无")}</b>
        <small>${escapeHtml([last.strength_label, last.sender_name, last.time].filter(Boolean).join(" · ") || "还没有记录")}</small>
        ${last.reason_detail || last.reason_label ? `<small>${escapeHtml(last.reason_detail || last.reason_label)}</small>` : ""}
      </article>
      <article>
        <span>唤醒疲劳</span>
        <b>${escapeHtml(fatigue.label || "无")}</b>
        <div class="fatigue-meter"><i style="width:${ratio}%"></i></div>
        <small>${escapeHtml(`${fatigue.value || 0}/${fatigue.limit || "-"} · ${fatigue.updated || "暂无"}`)}</small>
      </article>
      <article>
        <span>高强度状态</span>
        <b>${escapeHtml(highTitle)}</b>
        <small>${escapeHtml(highMeta)}</small>
      </article>
      <article>
        <span>记录数</span>
        <b>${escapeHtml(detail.wakeup_log_count || logs.length || 0)}</b>
        <small>命中、冷却、阈值未达和概率未触发</small>
      </article>
    </div>
    ${logs.length ? `
      <div class="group-wakeup-log-list">
        ${logs.map(groupWakeupLogItem).join("")}
      </div>
    ` : `<div class="empty small">暂无群聊唤醒记录</div>`}
  `;
}

function groupWakeupLogItem(item) {
  const resultLabel = {
    woke: "已唤醒",
    blocked: "冷却拦截",
    missed: "未触发",
  }[item.result] || item.result || "记录";
  const isDim = item.result !== "woke";
  const topicWeight = item.topic_weight || {};
  const weightText = topicWeight.reason
    ? `权重 ${Math.round(Number(topicWeight.multiplier || 1) * 100)}%：${topicWeight.reason}`
    : "";
  const reasonText = item.reason_detail || item.reason_label || item.reason || "";
  return `
    <article class="group-wakeup-log ${isDim ? "is-dim" : ""}">
      <header>
        <b>${escapeHtml(resultLabel)}</b>
        <span>${escapeHtml(item.strength_label || item.type || "-")}</span>
        <time>${escapeHtml(item.time || "")}</time>
      </header>
      <p>${escapeHtml(item.text || "-")}</p>
      <footer>
        <span>${escapeHtml(item.sender_name || item.sender_id || "-")}</span>
        <span>${escapeHtml(item.word ? `词：${item.word}` : item.reason || "")}</span>
        ${reasonText ? `<span>${escapeHtml(`原因：${reasonText}`)}</span>` : ""}
        ${item.probability ? `<span>${escapeHtml(`概率 ${Math.round(Number(item.probability || 0) * 100)}%`)}</span>` : ""}
        ${item.score ? `<span>${escapeHtml(`强度 ${item.score}/${item.threshold || "-"}`)}</span>` : ""}
        ${item.help_type ? `<span>${escapeHtml(`类型 ${item.help_type}`)}</span>` : ""}
        ${weightText ? `<span>${escapeHtml(weightText)}</span>` : ""}
        ${item.fatigue_label ? `<span>${escapeHtml(`疲劳 ${item.fatigue_label}`)}</span>` : ""}
      </footer>
      ${item.note ? `<small>${escapeHtml(item.note)}</small>` : ""}
    </article>
  `;
}

function bindGroupActions(detail) {
  document.querySelectorAll("[data-group-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      const action = button.dataset.groupAction;
      const body = { group_id: detail.group_id };
      if (action === "toggle") body.enabled = !detail.enabled;
      if (action === "reset_interjection") body.reset_interjection = true;
      if (action === "clear_observation") {
        if (!requireSecondClick(button, `group-clear:${detail.group_id}`, "再次点击清空该群的观测数据", "再次点击清空")) return;
        body.clear_observation = true;
      }
      if (action === "delete") {
        if (!requireSecondClick(button, `group-delete:${detail.group_id}`, "再次点击删除该群聊记录和名单 ID", "再次点击删除")) return;
        await runAction(async () => {
          await postJson("/group/delete", body);
          state.selectedGroupId = "";
          await loadAll();
        }, "已删除群聊记录", button);
        return;
      }
      await runAction(() => postJson("/group/update", body), "已更新群聊观测", button);
    });
  });
  document.querySelectorAll("[data-slang-add]").forEach((button) => {
    button.addEventListener("click", async () => {
      const editor = button.closest("[data-slang-new]");
      if (!editor) return;
      const payload = groupSlangPayloadFromEditor(editor, detail.group_id);
      if (!String(payload.term || "").trim()) {
        button.textContent = "先填词";
        setTimeout(() => { button.textContent = "保存"; }, 1200);
        return;
      }
      await runAction(() => postJson("/group/slang/update", payload), "已保存黑话", button);
      await renderGroupDetail(true);
    });
  });
  document.querySelectorAll("[data-slang-save]").forEach((button) => {
    button.addEventListener("click", async () => {
      const row = button.closest("[data-slang-term]");
      const editor = button.closest(".group-slang-editor");
      if (!row || !editor) return;
      const payload = groupSlangPayloadFromEditor(editor, detail.group_id, row.dataset.slangTerm || "");
      await runAction(() => postJson("/group/slang/update", payload), "已保存黑话校正", button);
      await renderGroupDetail(true);
    });
  });
  document.querySelectorAll("[data-slang-delete]").forEach((button) => {
    button.addEventListener("click", async () => {
      const row = button.closest("[data-slang-term]");
      const term = row?.dataset?.slangTerm || "";
      if (!term) return;
      if (!requireSecondClick(button, `group-slang-delete:${detail.group_id}:${term}`, "再次点击删除这条黑话", "再次点击删除")) return;
      await runAction(() => postJson("/group/slang/update", { group_id: detail.group_id, term, delete: true }), "已删除黑话", button);
      await renderGroupDetail(true);
    });
  });
}

function renderWorldbook() {
  const worldbook = state.overview?.worldbook || {};
  const members = Array.isArray(worldbook.members) ? worldbook.members : [];
  const groups = Array.isArray(worldbook.groups) ? worldbook.groups : [];
  const keyword = ($("#worldbookMemberFilter")?.value || "").trim().toLowerCase();
  const filteredMembers = members.filter((item) => {
    const haystack = [
      item.user_id,
      item.name,
      item.identity_type,
      item.linked_qq_user_id,
      item.linked_bili_profile_id,
      ...(Array.isArray(item.external_ids) ? item.external_ids : []),
      ...(Array.isArray(item.aliases) ? item.aliases : []),
      ...(Array.isArray(item.observed_names) ? item.observed_names : []),
      item.content,
    ].join(" ").toLowerCase();
    return !keyword || haystack.includes(keyword);
  });

  $("#worldbookSummary").innerHTML = [
    worldbookStat("身份节点", worldbook.enabled_member_count || 0, `${worldbook.member_count || 0} 个关系节点`),
    worldbookStat("群资料", worldbook.group_count || 0, "可用于群聊上下文"),
    worldbookStat("待确认观察", worldbook.pending_observation_total || 0, "确认后才写入重要记忆"),
    worldbookStat("自登记拒绝词", worldbook.self_registration_block_word_count || 0, worldbook.self_registration ? "命中后直接拒绝" : "自登记已关闭"),
    worldbookStat("识别方式", worldbook.enabled ? "QQ 精确" : "关闭", worldbook.match_aliases ? "称呼辅助开启" : "仅 QQ 确认"),
  ].join("");
  const clearPendingButton = $("#worldbookClearPendingBtn");
  if (clearPendingButton) clearPendingButton.disabled = !(worldbook.pending_observation_total > 0);
  $("#worldbookSourceFiles").textContent = Array.isArray(worldbook.source_files) && worldbook.source_files.length
    ? `资料路径：${worldbook.source_files.join("；")}`
    : "资料路径：默认路径尚未读取到可用配置";
  $("#worldbookMemberCount").textContent = `${filteredMembers.length}/${members.length} 个成员`;
  renderDl("#worldbookImportState", {
    last_import: worldbook.last_import || "未导入",
    auto_import: worldbook.auto_import ? "开启" : "关闭",
    self_registration: worldbook.self_registration ? "开启" : "关闭",
    self_registration_block_word_count: worldbook.self_registration_block_word_count || 0,
    inject_limit: worldbook.inject_limit || 0,
  });
  $("#worldbookMembers").innerHTML = filteredMembers.length
    ? filteredMembers.map(worldbookMemberCard).join("")
    : `<div class="empty small">暂无匹配关系节点</div>`;
  $("#worldbookGroups").innerHTML = groups.length
    ? groups.map((item) => `
      <section class="worldbook-group-card">
        <div class="worldbook-member-head">
          <div>
            <b>${escapeHtml(item.group_id || "-")}</b>
            <span>${escapeHtml(item.name || "未命名群资料")} · 优先级 ${escapeHtml(item.priority ?? "-")}</span>
          </div>
          <button type="button" data-worldbook-group-delete="${escapeHtml(item.group_id || "")}" class="danger-outline">删除</button>
        </div>
        <div class="worldbook-compact-meta">
          <span>${escapeHtml(item.content ? "有群资料正文" : "无群资料正文")}</span>
        </div>
        <details class="worldbook-editor">
          <summary>编辑群资料</summary>
          <label>名称 <input data-worldbook-group-name="${escapeHtml(item.group_id || "")}" value="${escapeHtml(item.name || "")}" /></label>
          <label>优先级 <input data-worldbook-group-priority="${escapeHtml(item.group_id || "")}" type="number" value="${escapeHtml(item.priority ?? 110)}" /></label>
          <label>资料正文 <textarea data-worldbook-group-content="${escapeHtml(item.group_id || "")}" rows="5">${escapeHtml(item.content || "")}</textarea></label>
          <button type="button" data-worldbook-group-save="${escapeHtml(item.group_id || "")}">保存群资料</button>
        </details>
      </section>
    `).join("")
    : `<div class="empty small">暂无群资料</div>`;
}

function worldbookStat(label, value, note) {
  return `
    <article class="worldbook-stat">
      <span>${escapeHtml(label)}</span>
      <b>${escapeHtml(value)}</b>
      <small>${escapeHtml(note)}</small>
    </article>
  `;
}

function worldbookMemberCard(item) {
  const aliases = Array.isArray(item.aliases) ? item.aliases : [];
  const observed = Array.isArray(item.observed_names) ? item.observed_names : [];
  const externalIds = Array.isArray(item.external_ids) ? item.external_ids : [];
  const memories = Array.isArray(item.important_memories) ? item.important_memories : [];
  const pending = Array.isArray(item.pending_observations) ? item.pending_observations : [];
  const chips = [...aliases.map((name) => `别名：${name}`), ...observed.map((name) => `群名片：${name}`)].slice(0, 12);
  const sourceEntries = Array.isArray(item.source_entries) ? item.source_entries : [];
  const detailId = `worldbook-editor-${String(item.user_id || "").replace(/[^A-Za-z0-9_-]/g, "_")}`;
  const previewItems = worldbookMemberPreviewItems(item, memories);
  const isExternal = item.identity_type === "external" || !/^\d+$/.test(String(item.user_id || ""));
  const identityLabel = isExternal ? "外部身份" : "身份 QQ";
  const genderText = String(item.gender || "").trim();
  const bindLine = item.linked_qq_user_id
    ? ` · 已绑定 QQ ${escapeHtml(item.linked_qq_user_id)}`
    : (item.linked_bili_profile_id ? ` · B站 ${escapeHtml(item.linked_bili_profile_id)}` : "");
  return `
    <section class="worldbook-member-card ${item.enabled ? "" : "off"}" data-worldbook-user-id="${escapeHtml(item.user_id || "")}">
      <div class="worldbook-member-head">
        <div>
          <b>${escapeHtml(item.name || item.user_id || "未命名成员")}</b>
          <span>${identityLabel} ${escapeHtml(item.user_id || "-")} · 优先级 ${escapeHtml(item.priority ?? "-")}${bindLine}</span>
        </div>
        <div class="worldbook-card-actions">
          <button type="button" data-worldbook-living-memory="${escapeHtml(item.user_id || "")}">长期记忆</button>
          <button type="button" data-worldbook-edit="${escapeHtml(detailId)}">编辑</button>
          <button type="button" data-worldbook-member="${escapeHtml(item.user_id || "")}" data-enabled="${item.enabled ? "0" : "1"}">
            ${escapeHtml(item.enabled ? "停用" : "启用")}
          </button>
          <button type="button" data-worldbook-delete="${escapeHtml(item.user_id || "")}" class="danger-outline">删除</button>
        </div>
      </div>
      <div class="worldbook-compact-meta">
        <span>${escapeHtml(aliases.length)} 个别名</span>
        <span>${escapeHtml(observed.length)} 个曾见群名片</span>
        ${genderText ? `<span>性别：${escapeHtml(genderText)}</span>` : ""}
        ${externalIds.length ? `<span>${escapeHtml(externalIds.length)} 个外部身份</span>` : ""}
        <span>${escapeHtml((item.important_memories || []).length)} 条记忆</span>
        ${pending.length ? `<span>${escapeHtml(pending.length)} 条待确认观察</span>` : ""}
        ${sourceEntries.length ? `<span>${escapeHtml(sourceEntries.slice(0, 2).join(" / "))}</span>` : ""}
      </div>
      ${previewItems.length ? `
        <div class="worldbook-member-preview-list">
          ${previewItems.map(([label, value]) => `
            <p><b>${escapeHtml(label)}</b><span>${escapeHtml(value)}</span></p>
          `).join("")}
        </div>
      ` : ""}
      <div data-worldbook-living-memory-panel="${escapeHtml(item.user_id || "")}">
        ${worldbookLivingMemoryPanel(item.user_id || "")}
      </div>
      <details class="worldbook-editor" id="${escapeHtml(detailId)}">
        <summary>编辑关系节点</summary>
        <div class="worldbook-chip-row">
          ${chips.length ? chips.map((chip) => `<span>${escapeHtml(chip)}</span>`).join("") : `<span>暂无别名记录</span>`}
        </div>
        <label>别名
          <textarea data-worldbook-aliases="${escapeHtml(item.user_id || "")}" rows="3">${escapeHtml(aliases.join("\n"))}</textarea>
        </label>
        <label>名称
          <input data-worldbook-name="${escapeHtml(item.user_id || "")}" value="${escapeHtml(item.name || "")}" />
        </label>
        <label>性别
          <input data-worldbook-gender="${escapeHtml(item.user_id || "")}" placeholder="可填男、女、未知或自定义描述" value="${escapeHtml(item.gender || "")}" />
        </label>
        <label>注入优先级
          <input data-worldbook-priority="${escapeHtml(item.user_id || "")}" type="number" value="${escapeHtml(item.priority ?? 120)}" />
        </label>
        <label>资料正文
          <textarea data-worldbook-content="${escapeHtml(item.user_id || "")}" rows="5">${escapeHtml(item.content || "")}</textarea>
        </label>
        <label>身份说明
          <textarea data-worldbook-identity-note="${escapeHtml(item.user_id || "")}" rows="3">${escapeHtml(item.identity_note || "")}</textarea>
        </label>
        <label>互动边界（可选）
          <textarea data-worldbook-boundary-note="${escapeHtml(item.user_id || "")}" rows="3">${escapeHtml(item.boundary_note || "")}</textarea>
        </label>
        ${isExternal ? `
          <label>绑定到已有 QQ（可选）
            <input data-worldbook-linked-qq="${escapeHtml(item.user_id || "")}" placeholder="填已有 QQ 后保存，会合并到该关系节点" value="${escapeHtml(item.linked_qq_user_id || "")}" />
          </label>
        ` : `
          <label>已关联外部身份
            <input readonly value="${escapeHtml(externalIds.join(" / ") || item.linked_bili_profile_id || "暂无")}" />
          </label>
        `}
        <div class="worldbook-memory-list">
          ${pending.length ? pending.map((obs) => worldbookPendingObservationCard(item.user_id || "", obs)).join("") : ""}
          ${memories.length ? memories.map((memory, index) => worldbookMemoryCard(item.user_id || "", memory, index)).join("") : `<div class="empty small">暂无重要记忆</div>`}
        </div>
        <div class="worldbook-editor-actions">
          <button type="button" data-worldbook-save="${escapeHtml(item.user_id || "")}">保存关系节点</button>
          <button type="button" data-worldbook-delete="${escapeHtml(item.user_id || "")}" class="danger-outline">删除节点</button>
        </div>
      </details>
    </section>
  `;
}

function worldbookPendingObservationCard(userId, obs) {
  return `
    <section class="worldbook-memory-card pending">
      <div>
        <b>${escapeHtml(obs.title || "待确认观察")}</b>
        <p>${escapeHtml(obs.content || obs.evidence || "")}</p>
        <span>${escapeHtml(obs.group_id ? `群 ${obs.group_id}` : "群聊观察")} · ${escapeHtml(obs.created_at || "")}${obs.count > 1 ? ` · ${escapeHtml(obs.count)} 次` : ""}</span>
      </div>
      <div class="actions compact">
        <button type="button" data-worldbook-observation-accept="${escapeHtml(userId)}" data-observation-id="${escapeHtml(obs.id || "")}">确认</button>
        <button type="button" class="danger-outline" data-worldbook-observation-reject="${escapeHtml(userId)}" data-observation-id="${escapeHtml(obs.id || "")}">忽略</button>
      </div>
    </section>
  `;
}

function worldbookMemberPreviewItems(item, memories = []) {
  const rows = [];
  const add = (label, value, limit = 120) => {
    const text = shortName(String(value || "").trim(), limit);
    if (text && !rows.some(([, existing]) => existing === text)) rows.push([label, text]);
  };
  add("资料", item.content || item.note, 130);
  add("性别", item.gender, 60);
  add("身份", item.identity_note, 120);
  add("边界", item.boundary_note, 110);
  const memory = memories.find((entry) => entry && entry.enabled !== false && String(entry.content || "").trim());
  if (memory) add("记忆", `${memory.title ? `${memory.title}：` : ""}${memory.content || ""}`, 120);
  return rows.slice(0, 4);
}

function worldbookMemoryCard(userId, memory, index) {
  return `
    <section class="worldbook-memory-card ${memory.enabled === false ? "off" : ""}">
      <div>
        <b>${escapeHtml(memory.title || "重要记忆")}</b>
        <p>${escapeHtml(memory.content || "")}</p>
        <span>${escapeHtml(memory.privacy || "internal")} · 权重 ${escapeHtml(memory.weight ?? 50)}${memory.source ? ` · ${escapeHtml(memory.source)}` : ""}</span>
      </div>
      <div class="actions compact">
        <button type="button" data-worldbook-memory-toggle="${escapeHtml(userId)}" data-memory-index="${escapeHtml(index)}">
          ${escapeHtml(memory.enabled === false ? "启用" : "停用")}
        </button>
        <button type="button" class="danger-outline" data-worldbook-memory-delete="${escapeHtml(userId)}" data-memory-index="${escapeHtml(index)}">删除</button>
      </div>
    </section>
  `;
}

function worldbookLivingMemoryPanel(userId) {
  const result = state.worldbookLivingMemory?.[userId];
  if (!result) return "";
  if (result.loading) {
    return `
      <section class="worldbook-livingmemory-panel">
        <div class="worldbook-livingmemory-head">
          <div>
            <b>LivingMemory</b>
            <span>正在检索对应记忆...</span>
          </div>
          <button type="button" data-worldbook-living-memory-close="${escapeHtml(userId)}" aria-label="收起 LivingMemory">收起</button>
        </div>
      </section>
    `;
  }
  if (result.error) {
    return `
      <section class="worldbook-livingmemory-panel error">
        <div class="worldbook-livingmemory-head">
          <div>
            <b>LivingMemory</b>
            <span>${escapeHtml(result.error)}</span>
          </div>
          <button type="button" data-worldbook-living-memory-close="${escapeHtml(userId)}" aria-label="收起 LivingMemory">收起</button>
        </div>
      </section>
    `;
  }
  const items = Array.isArray(result.items) ? result.items : [];
  const tokens = Array.isArray(result.tokens) ? result.tokens : [];
  const primaryTokens = Array.isArray(result.primary_tokens) ? result.primary_tokens : [];
  return `
    <section class="worldbook-livingmemory-panel">
      <div class="worldbook-livingmemory-head">
        <div>
          <b>LivingMemory 相关记忆</b>
          <span>${escapeHtml(result.available === false ? (result.message || "未找到 LivingMemory 数据库") : `${items.length} 条结果 · 严格匹配`)}</span>
        </div>
        <button type="button" data-worldbook-living-memory-close="${escapeHtml(userId)}" aria-label="收起 LivingMemory">收起</button>
      </div>
      ${result.filter_note ? `<p class="worldbook-livingmemory-note">${escapeHtml(result.filter_note)}</p>` : ""}
      ${tokens.length ? `
        <div class="worldbook-livingmemory-tokens">
          ${tokens.slice(0, 12).map((token) => `<span class="${primaryTokens.includes(token) ? "primary" : ""}">${escapeHtml(token)}</span>`).join("")}
        </div>
      ` : ""}
      <div class="worldbook-livingmemory-list">
        ${items.length ? items.map(worldbookLivingMemoryCard).join("") : `<div class="empty small">没有检索到对应 LivingMemory 记忆</div>`}
      </div>
    </section>
  `;
}

function worldbookLivingMemoryCard(item) {
  const tags = [
    item.source_label || item.source,
    item.persona_id,
    item.session_id,
    item.importance !== undefined && item.importance !== null ? `重要性 ${Number(item.importance).toFixed(2)}` : "",
    item.confidence !== undefined && item.confidence !== null ? `置信 ${Number(item.confidence).toFixed(2)}` : "",
    item.score !== undefined && item.score !== null ? `命中 ${Number(item.score).toFixed(1)}` : "",
  ].filter(Boolean);
  const topics = Array.isArray(item.topics) ? item.topics : [];
  const keyFacts = Array.isArray(item.key_facts) ? item.key_facts : [];
  const matchedTokens = Array.isArray(item.matched_tokens) ? item.matched_tokens : [];
  const content = String(item.content || "");
  const preview = item.preview || shortName(content, 260);
  const canExpand = content && content !== preview;
  return `
    <article class="worldbook-livingmemory-card">
      <div class="worldbook-livingmemory-card-meta">
        ${tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}
      </div>
      ${matchedTokens.length ? `<div class="worldbook-livingmemory-tags matched">${matchedTokens.map((tag) => `<span>${escapeHtml(`命中：${tag}`)}</span>`).join("")}</div>` : ""}
      <p>${escapeHtml(preview || "无内容")}</p>
      ${topics.length ? `<div class="worldbook-livingmemory-tags">${topics.slice(0, 6).map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}</div>` : ""}
      ${keyFacts.length ? `<ul>${keyFacts.slice(0, 4).map((fact) => `<li>${escapeHtml(fact)}</li>`).join("")}</ul>` : ""}
      ${canExpand ? `
        <details>
          <summary>展开片段</summary>
          <pre>${escapeHtml(content)}</pre>
        </details>
      ` : ""}
    </article>
  `;
}

function getWorldbookMember(userId) {
  const members = state.overview?.worldbook?.members || [];
  return members.find((item) => item.user_id === userId);
}

function findWorldbookLivingMemoryPanel(userId) {
  return Array.from(document.querySelectorAll("[data-worldbook-living-memory-panel]"))
    .find((panel) => panel.dataset.worldbookLivingMemoryPanel === userId);
}

function closeWorldbookLivingMemory(userId) {
  if (!userId) return;
  delete state.worldbookLivingMemory[userId];
  const panel = findWorldbookLivingMemoryPanel(userId);
  if (panel) panel.innerHTML = "";
}

async function loadWorldbookLivingMemory(userId, button) {
  if (!userId) return;
  if (state.worldbookLivingMemory[userId] && !state.worldbookLivingMemory[userId].loading) {
    closeWorldbookLivingMemory(userId);
    return;
  }
  const panel = findWorldbookLivingMemoryPanel(userId);
  const requestId = ++state.worldbookLivingMemoryRequestSeq;
  state.worldbookLivingMemory[userId] = { loading: true, requestId };
  if (panel) panel.innerHTML = worldbookLivingMemoryPanel(userId);
  setActionBusy(button, true);
  try {
    const result = await fetchJson(`/worldbook/member/livingmemory?user_id=${encodeURIComponent(userId)}&limit=24`);
    if (state.worldbookLivingMemory[userId]?.requestId !== requestId) return;
    state.worldbookLivingMemory[userId] = result || { items: [] };
    if (panel) panel.innerHTML = worldbookLivingMemoryPanel(userId);
    showToast(result?.message || "LivingMemory 查询完成");
  } catch (error) {
    if (state.worldbookLivingMemory[userId]?.requestId !== requestId) return;
    state.worldbookLivingMemory[userId] = { error: error.message || "查询失败", items: [] };
    if (panel) panel.innerHTML = worldbookLivingMemoryPanel(userId);
    showToast(`查询失败：${error.message}`, "error");
  } finally {
    setActionBusy(button, false);
  }
}

async function handleWorldbookMemberAction(button) {
  const editTarget = button.dataset.worldbookEdit;
  if (editTarget) {
    const details = document.getElementById(editTarget);
    if (details) {
      details.open = true;
      details.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
    return;
  }
  if (button.dataset.worldbookLivingMemory !== undefined) {
    await loadWorldbookLivingMemory(button.dataset.worldbookLivingMemory, button);
    return;
  }
  if (button.dataset.worldbookLivingMemoryClose !== undefined) {
    closeWorldbookLivingMemory(button.dataset.worldbookLivingMemoryClose);
    return;
  }
  if (button.dataset.worldbookMember !== undefined) {
    const userId = button.dataset.worldbookMember;
    if (!userId) return;
    await runAction(() => postJson("/worldbook/member/update", {
      user_id: userId,
      enabled: button.dataset.enabled === "1",
    }), button.dataset.enabled === "1" ? "已启用关系节点" : "已停用关系节点", button);
    return;
  }
  if (button.dataset.worldbookSave !== undefined) {
    const userId = button.dataset.worldbookSave;
    if (!userId) return;
    const aliasBox = findWorldbookField("aliases", userId);
    const identityInput = findWorldbookField("identity-note", userId);
    const boundaryInput = findWorldbookField("boundary-note", userId);
    const nameInput = findWorldbookField("name", userId);
    const genderInput = findWorldbookField("gender", userId);
    const priorityInput = findWorldbookField("priority", userId);
    const contentInput = findWorldbookField("content", userId);
    const linkedQqInput = findWorldbookField("linked-qq", userId);
    const aliases = String(aliasBox?.value || "")
      .split(/[\n,，;；]+/)
      .map((item) => item.trim())
      .filter(Boolean);
    const payload = {
      user_id: userId,
      name: nameInput?.value || "",
      gender: genderInput?.value || "",
      priority: Number(priorityInput?.value || 120),
      content: contentInput?.value || "",
      identity_note: identityInput?.value || "",
      boundary_note: boundaryInput?.value || "",
      aliases,
    };
    if (linkedQqInput && String(linkedQqInput.value || "").trim()) {
      payload.linked_qq_user_id = String(linkedQqInput.value || "").trim();
    }
    await runAction(() => postJson("/worldbook/member/update", payload), "已保存关系节点", button);
    return;
  }
  if (button.dataset.worldbookMemoryToggle !== undefined) {
    const userId = button.dataset.worldbookMemoryToggle;
    const index = Number(button.dataset.memoryIndex || -1);
    const member = getWorldbookMember(userId);
    const memories = Array.isArray(member?.important_memories) ? member.important_memories.map((item) => ({ ...item })) : [];
    if (!userId || index < 0 || index >= memories.length) return;
    memories[index].enabled = memories[index].enabled === false;
    await runAction(() => postJson("/worldbook/member/update", { user_id: userId, important_memories: memories }), "已更新重要记忆", button);
    return;
  }
  if (button.dataset.worldbookMemoryDelete !== undefined) {
    const userId = button.dataset.worldbookMemoryDelete;
    const index = Number(button.dataset.memoryIndex || -1);
    const member = getWorldbookMember(userId);
    const memories = Array.isArray(member?.important_memories) ? member.important_memories.map((item) => ({ ...item })) : [];
    if (!userId || index < 0 || index >= memories.length) return;
    memories.splice(index, 1);
    await runAction(() => postJson("/worldbook/member/update", { user_id: userId, important_memories: memories }), "已删除重要记忆", button);
    return;
  }
  if (button.dataset.worldbookObservationAccept !== undefined || button.dataset.worldbookObservationReject !== undefined) {
    const accepting = button.dataset.worldbookObservationAccept !== undefined;
    const userId = accepting ? button.dataset.worldbookObservationAccept : button.dataset.worldbookObservationReject;
    const observationId = button.dataset.observationId || "";
    if (!userId || !observationId) return;
    await runAction(() => postJson("/worldbook/member/update", {
      user_id: userId,
      [accepting ? "accept_pending_observation_id" : "reject_pending_observation_id"]: observationId,
    }), accepting ? "已写入重要记忆" : "已忽略待确认观察", button);
    return;
  }
  if (button.dataset.worldbookDelete !== undefined) {
    const userId = button.dataset.worldbookDelete || button.closest("[data-worldbook-user-id]")?.dataset.worldbookUserId || "";
    if (!userId) {
      showToast("没有找到要删除的关系节点 ID", "error");
      return;
    }
    const now = Date.now();
    const armed = button.dataset.deleteArmed === userId && now - Number(button.dataset.deleteArmedAt || 0) < 6000;
    if (!armed) {
      button.dataset.deleteArmed = userId;
      button.dataset.deleteArmedAt = String(now);
      button.dataset.originalText = button.textContent || "删除";
      button.textContent = "再次点击删除";
      showToast(`再次点击删除关系节点 ${userId}`);
      window.clearTimeout(button._deleteArmedTimer);
      button._deleteArmedTimer = window.setTimeout(() => {
        if (button.dataset.deleteArmed === userId) {
          delete button.dataset.deleteArmed;
          delete button.dataset.deleteArmedAt;
          button.textContent = button.dataset.originalText || "删除";
          delete button.dataset.originalText;
        }
      }, 6000);
      return;
    }
    delete button.dataset.deleteArmed;
    delete button.dataset.deleteArmedAt;
    await runAction(() => postJson("/worldbook/member/update", { user_id: userId, delete: true }), "已删除关系节点", button);
  }
}

function findWorldbookField(field, id) {
  return [...document.querySelectorAll(`[data-worldbook-${field}]`)]
    .find((item) => item.getAttribute(`data-worldbook-${field}`) === id);
}

function findWorldbookGroupField(field, id) {
  return [...document.querySelectorAll(`[data-worldbook-group-${field}]`)]
    .find((item) => item.getAttribute(`data-worldbook-group-${field}`) === id);
}

async function handleWorldbookGroupAction(button) {
  if (button.dataset.worldbookGroupSave !== undefined) {
    const groupId = button.dataset.worldbookGroupSave;
    if (!groupId) return;
    await runAction(() => postJson("/worldbook/group/update", {
      group_id: groupId,
      name: findWorldbookGroupField("name", groupId)?.value || "",
      priority: Number(findWorldbookGroupField("priority", groupId)?.value || 110),
      content: findWorldbookGroupField("content", groupId)?.value || "",
    }), "已保存群资料", button);
    return;
  }
  if (button.dataset.worldbookGroupDelete !== undefined) {
    const groupId = button.dataset.worldbookGroupDelete;
    if (!groupId) return;
    const now = Date.now();
    const armed = button.dataset.deleteArmed === groupId && now - Number(button.dataset.deleteArmedAt || 0) < 6000;
    if (!armed) {
      button.dataset.deleteArmed = groupId;
      button.dataset.deleteArmedAt = String(now);
      button.dataset.originalText = button.textContent || "删除";
      button.textContent = "再次点击删除";
      showToast(`再次点击删除群资料 ${groupId}`);
      window.clearTimeout(button._deleteArmedTimer);
      button._deleteArmedTimer = window.setTimeout(() => {
        if (button.dataset.deleteArmed === groupId) {
          delete button.dataset.deleteArmed;
          delete button.dataset.deleteArmedAt;
          button.textContent = button.dataset.originalText || "删除";
          delete button.dataset.originalText;
        }
      }, 6000);
      return;
    }
    delete button.dataset.deleteArmed;
    delete button.dataset.deleteArmedAt;
    await runAction(() => postJson("/worldbook/group/update", { group_id: groupId, delete: true }), "已删除群资料", button);
  }
}

function renderMemory() {
  const overview = state.overview || {};
  const daily = overview.daily_state || {};
  const life = overview.life_observation || {};
  $("#livingMemoryBox").textContent = overview.livingmemory?.status || "未读取到 LivingMemory 状态";
  renderLifeHero(daily, life);
  renderDreamCard(life.dream || {});
  renderStatePillBoard(daily);
  renderDiaryCards(life.diaries || []);
  renderDreamFragments(life.dream_fragments || []);
  renderDl("#dailyState", normalizeDailyStateForDisplay(daily));
  renderDailyTimeline();
  renderSkillGrowth();
  renderInteractionImpact();
  renderMemoryComposition();
  renderSlangCloud();
}

function foodMenuFeaturePanelHtml() {
  const menu = state.overview?.food_menu || {};
  const items = Array.isArray(menu.items) ? menu.items : [];
  const enabled = toBool(state.featureDraft?.enable_food_menu_recommendation)
    || featureTemporarilyUnlockedByProactiveOnly("enable_food_menu_recommendation");
  return `
    <article class="feature-detail-card food-feature-panel">
      <div class="food-feature-hero">
        <div>
          <span class="module-badge">私聊陪伴</span>
          <h3>候选菜单</h3>
          <p>把常吃的几样东西收在这里。用户纠结吃什么时，只挑最贴合的几个给回复做参考。</p>
        </div>
        <div class="food-feature-status ${enabled ? "on" : "off"}">
          <b>${enabled ? "参与回复" : "仅管理"}</b>
          <span>${enabled ? "问到吃什么时会参考" : "关闭后不会进入回复参考"}</span>
          ${featureLockedByProactiveOnlyMode("enable_food_menu_recommendation") ? "" : `<button type="button" data-food-feature-save-toggle>保存开关</button>`}
        </div>
      </div>
      <div class="food-feature-stats">
        <span>${escapeHtml(menu.visible_count || 0)} 个可用</span>
        <span>${escapeHtml(menu.favorite_count || 0)} 个常吃</span>
        <span>${escapeHtml(menu.hidden_count || 0)} 个收起</span>
        <span>更新 ${escapeHtml(menu.updated || "-")}</span>
      </div>
      <div class="food-feature-entry">
        <form id="foodMenuFeatureAddForm" class="food-quick-add-form">
          <label>快速添加
            <input name="name" maxlength="40" placeholder="兰州拉面 / 黄焖鸡 / 楼下麻辣烫" required />
          </label>
          <button type="submit">加入</button>
          <details class="food-extra-drawer">
            <summary>补充信息</summary>
            <div class="food-feature-add-form">
              <label>类型
                <select name="type">${foodMenuTypeOptions("")}</select>
              </label>
              <label>分类 <input name="category" maxlength="24" placeholder="可不填，会自动猜" /></label>
              <label>标签 <input name="tags" maxlength="160" placeholder="热乎、快、清淡" /></label>
              <label class="wide-field">适合时段
                <div class="food-time-picks">${foodMenuTimeCheckboxes([], { name: "times" })}</div>
              </label>
              <label class="wide-field">备注 <input name="note" maxlength="100" placeholder="不想纠结时直接选" /></label>
              <label class="check-field"><input name="favorite" type="checkbox" /> 常吃</label>
            </div>
          </details>
        </form>
        <form id="foodMenuBulkForm" class="food-bulk-form">
          <label>批量粘贴
            <textarea name="text" rows="5" placeholder="一行一个就行：&#10;兰州拉面&#10;黄焖鸡&#10;楼下麻辣烫&#10;也可以：煎饼果子｜早餐｜快、便宜｜早餐"></textarea>
          </label>
          <div class="food-bulk-actions">
            <label><input name="favorite" type="checkbox" /> 标为常吃</label>
            <button type="button" data-food-preset="basic">填入常见模板</button>
            <button type="submit">批量加入</button>
          </div>
        </form>
      </div>
      <div class="food-feature-list">
        ${items.length ? renderFoodMenuGroups(items) : `
          <div class="food-menu-empty">
            <b>先放几样常吃的东西</b>
            <span>比如一道菜、一家店、一份外卖。候选越具体，问“吃什么”时越容易给出像样建议。</span>
          </div>
        `}
      </div>
    </article>
  `;
}

function renderFoodMenuGroups(items) {
  const groups = new Map();
  items.forEach((item) => {
    const label = String(item.type_label || "候选").trim() || "候选";
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(item);
  });
  return Array.from(groups.entries()).map(([label, groupItems]) => `
    <section class="food-menu-group">
      <header>
        <b>${escapeHtml(label)}</b>
        <span>${escapeHtml(groupItems.length)} 个</span>
      </header>
      <div class="food-menu-list">
        ${groupItems.map(renderFoodMenuCard).join("")}
      </div>
    </section>
  `).join("");
}

function renderFoodMenuCard(item) {
  const tags = Array.isArray(item.tags) ? item.tags : [];
  const times = Array.isArray(item.time_labels) ? item.time_labels : [];
  const timeKeys = Array.isArray(item.times) ? item.times : [];
  const aliases = Array.isArray(item.aliases) ? item.aliases : [];
  const avoid = Array.isArray(item.avoid) ? item.avoid : [];
  const id = String(item.id || "");
  return `
    <article class="food-menu-card ${item.hidden ? "is-hidden-food" : ""} ${item.favorite ? "is-favorite-food" : ""}">
      <header>
        <div>
          <span>${escapeHtml(item.category || item.type_label || "候选")}</span>
          <h3>${escapeHtml(item.name || "未命名")}</h3>
        </div>
        <em>${item.favorite ? "常吃" : item.hidden ? "已收起" : "候选"}</em>
      </header>
      <p>${escapeHtml(item.note || "没有备注")}</p>
      <div class="food-menu-tags">
        ${tags.slice(0, 5).map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}
        ${times.slice(0, 4).map((time) => `<span>${escapeHtml(time)}</span>`).join("")}
        ${item.use_count ? `<span>吃过 ${escapeHtml(item.use_count)} 次</span>` : ""}
        ${item.last_recommended && !["-", "从未"].includes(item.last_recommended) ? `<span>推荐 ${escapeHtml(item.last_recommended)}</span>` : ""}
      </div>
      <details class="food-menu-editor">
        <summary>整理</summary>
        <div class="food-menu-editor-grid">
          <label>名称 <input data-food-name="${escapeHtml(id)}" value="${escapeHtml(item.name || "")}" maxlength="40" /></label>
          <label>类型
            <select data-food-type="${escapeHtml(id)}">
              ${foodMenuTypeOptions(item.type)}
            </select>
          </label>
          <label>分类 <input data-food-category="${escapeHtml(id)}" value="${escapeHtml(item.category || "")}" maxlength="24" /></label>
          <label>标签 <input data-food-tags="${escapeHtml(id)}" value="${escapeHtml(tags.join(", "))}" maxlength="160" /></label>
          <label class="wide-field">适合时段
            <div class="food-time-picks">${foodMenuTimeCheckboxes(timeKeys, { dataAttr: "data-food-time", id })}</div>
          </label>
          <label>别名 <input data-food-aliases="${escapeHtml(id)}" value="${escapeHtml(aliases.join(", "))}" maxlength="160" /></label>
          <label class="wide-field">避开场景 <input data-food-avoid="${escapeHtml(id)}" value="${escapeHtml(avoid.join(", "))}" maxlength="160" placeholder="胃不舒服、太晚" /></label>
          <label class="wide-field">备注 <input data-food-note="${escapeHtml(id)}" value="${escapeHtml(item.note || "")}" maxlength="100" /></label>
          <label class="check-field"><input data-food-favorite="${escapeHtml(id)}" type="checkbox" ${item.favorite ? "checked" : ""} /> 常吃</label>
          <label class="check-field"><input data-food-hidden="${escapeHtml(id)}" type="checkbox" ${item.hidden ? "checked" : ""} /> 暂时收起</label>
        </div>
        <div class="food-menu-actions">
          <button type="button" data-food-save="${escapeHtml(id)}">保存</button>
          <button type="button" class="danger-outline" data-food-delete="${escapeHtml(id)}">删除</button>
        </div>
      </details>
    </article>
  `;
}

function foodMenuTypeOptions(selected) {
  const options = [
    ["", "自动判断"],
    ["dish", "菜品"],
    ["restaurant", "菜馆"],
    ["takeout", "外卖"],
    ["drink_snack", "饮品/零食"],
    ["emergency", "应急"],
  ];
  return options.map(([value, label]) => `<option value="${value}" ${String(selected || "") === value ? "selected" : ""}>${label}</option>`).join("");
}

function foodMenuTimeChoices() {
  return [
    ["breakfast", "早餐"],
    ["lunch", "午餐"],
    ["dinner", "晚餐"],
    ["late_night", "夜宵"],
    ["snack", "加餐"],
  ];
}

function foodMenuTimeCheckboxes(selected = [], options = {}) {
  const selectedSet = new Set((Array.isArray(selected) ? selected : [selected]).map((item) => String(item || "")));
  const attr = options.dataAttr && options.id
    ? `${options.dataAttr}="${escapeHtml(options.id)}"`
    : `name="${escapeHtml(options.name || "times")}"`;
  return foodMenuTimeChoices().map(([value, label]) => `
    <label>
      <input type="checkbox" ${attr} value="${escapeHtml(value)}" ${selectedSet.has(value) ? "checked" : ""}>
      <span>${escapeHtml(label)}</span>
    </label>
  `).join("");
}

function foodField(id, field) {
  return document.querySelector(`[data-food-${field}="${CSS.escape(id)}"]`);
}

function foodTimeValues(id) {
  return [...document.querySelectorAll(`[data-food-time="${CSS.escape(id)}"]:checked`)].map((input) => input.value);
}

function bindFoodMenuActions() {
  document.querySelectorAll("[data-food-save]").forEach((button) => {
    button.addEventListener("click", async () => {
      const id = button.dataset.foodSave || "";
      await runAction(() => postJson("/food_menu/update", {
        id,
        name: foodField(id, "name")?.value || "",
        type: foodField(id, "type")?.value || "",
        category: foodField(id, "category")?.value || "",
        tags: foodField(id, "tags")?.value || "",
        times: foodTimeValues(id),
        aliases: foodField(id, "aliases")?.value || "",
        avoid: foodField(id, "avoid")?.value || "",
        note: foodField(id, "note")?.value || "",
        favorite: Boolean(foodField(id, "favorite")?.checked),
        hidden: Boolean(foodField(id, "hidden")?.checked),
      }), "已保存候选", button);
    });
  });
  document.querySelectorAll("[data-food-delete]").forEach((button) => {
    button.addEventListener("click", async () => {
      const id = button.dataset.foodDelete || "";
      if (!requireSecondClick(button, `food:${id}`, "再次点击删除候选", "再次点击删除")) return;
      await runAction(() => postJson("/food_menu/update", { id, delete: true }), "已删除候选", button);
    });
  });
}

function bindFoodMenuFeatureActions() {
  const formEl = $("#foodMenuFeatureAddForm");
  if (formEl) {
    formEl.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(formEl);
      const name = String(form.get("name") || "").trim();
      if (!name) return;
      const times = Array.from(formEl.querySelectorAll('input[name="times"]:checked')).map((input) => input.value);
      await runAction(() => postJson("/food_menu/update", {
        name,
        type: form.get("type") || "",
        category: form.get("category") || "",
        tags: form.get("tags") || "",
        times,
        note: form.get("note") || "",
        favorite: Boolean(form.get("favorite")),
        hidden: false,
      }), "已加入候选", event.submitter);
      formEl.reset();
    });
  }
  const bulkForm = $("#foodMenuBulkForm");
  if (bulkForm) {
    bulkForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(bulkForm);
      const text = String(form.get("text") || "").trim();
      if (!text) return;
      await runAction(() => postJson("/food_menu/bulk_update", {
        text,
        favorite: Boolean(form.get("favorite")),
      }), "已批量加入候选", event.submitter);
      bulkForm.reset();
    });
  }
  document.querySelectorAll("[data-food-preset]").forEach((button) => {
    button.addEventListener("click", () => {
      const textarea = $("#foodMenuBulkForm")?.querySelector('textarea[name="text"]');
      if (!textarea) return;
      const preset = [
        "兰州拉面",
        "黄焖鸡",
        "楼下麻辣烫",
        "沙县小吃",
        "煎饼果子｜早餐｜快、便宜｜早餐",
        "粥｜清淡｜热乎、清淡｜早餐",
        "奶茶｜甜口｜甜｜加餐",
        "泡面｜应急｜快、热乎｜夜宵",
      ].join("\n");
      textarea.value = textarea.value.trim() ? `${textarea.value.trim()}\n${preset}` : preset;
      textarea.focus();
    });
  });
  document.querySelectorAll("[data-food-feature-save-toggle]").forEach((button) => {
    button.addEventListener("click", async () => {
      await runAction(
        () => postJson("/settings/update", {
          features: { enable_food_menu_recommendation: toBool(state.featureDraft?.enable_food_menu_recommendation) },
        }),
        "已保存吃什么候选开关",
        button,
      );
    });
  });
  bindFoodMenuActions();
}

function renderSkillGrowth() {
  const growth = state.overview?.skill_growth || {};
  const items = Array.isArray(growth.items) ? growth.items : [];
  const panel = $("#skillGrowthPanel");
  if (!panel) return;
  if (!growth.enabled) {
    panel.innerHTML = `<div class="empty small">技能成长未开启</div>`;
    return;
  }
  if (!items.length) {
    panel.innerHTML = `<div class="empty small">暂无技能记录</div>`;
    return;
  }
  panel.innerHTML = `
    <div class="skill-growth-head">
      <span>${escapeHtml(growth.skill_count || items.length)} 项技能</span>
      ${growth.hidden_count ? `<span>隐藏 ${escapeHtml(growth.hidden_count)}</span>` : ""}
      ${growth.frozen_count ? `<span>冻结 ${escapeHtml(growth.frozen_count)}</span>` : ""}
      <span>成长倍率 ${escapeHtml(growth.rate || 1)}</span>
      <span>${growth.schedule_influence ? `影响日程 ${escapeHtml(formatPercent(growth.schedule_influence_strength))}` : "不影响日程"}</span>
      <span>更新 ${escapeHtml(growth.updated || "-")}</span>
    </div>
    <div class="skill-category-list">
      ${renderSkillCategoryGroups(items)}
    </div>
  `;
  bindSkillGrowthActions();
}

function renderSkillCategoryGroups(items) {
  const groups = new Map();
  items.forEach((item) => {
    const category = String(item.category || "能力").trim() || "能力";
    if (!groups.has(category)) groups.set(category, []);
    groups.get(category).push(item);
  });
  return Array.from(groups.entries()).map(([category, groupItems]) => {
    const maxItem = groupItems.reduce((best, item) => Number(item.level || 1) > Number(best.level || 1) ? item : best, groupItems[0]);
    const totalTraining = groupItems.reduce((sum, item) => sum + Number(item.training_count || 0), 0);
    const hiddenCount = groupItems.filter((item) => item.hidden).length;
    const frozenCount = groupItems.filter((item) => item.frozen).length;
    return `
      <details class="skill-category-group ${escapeHtml(categorySlug(category))}">
        <summary>
          <span>${escapeHtml(category)}</span>
          <small>${escapeHtml(groupItems.length)} 项｜最好 ${escapeHtml(maxItem?.level_title || "能力状态")}｜训练 ${escapeHtml(totalTraining)} 次${hiddenCount ? `｜隐藏 ${escapeHtml(hiddenCount)}` : ""}${frozenCount ? `｜冻结 ${escapeHtml(frozenCount)}` : ""}</small>
        </summary>
        <div class="skill-growth-grid">
          ${groupItems.map(renderSkillCard).join("")}
        </div>
      </details>
    `;
  }).join("");
}

function renderSkillCard(item) {
  const logs = Array.isArray(item.recent_logs) ? item.recent_logs : [];
  const badges = [
    item.hidden ? "已隐藏" : "",
    item.frozen ? "已冻结" : "",
  ].filter(Boolean);
  return `
    <article class="skill-card is-collapsed ${item.hidden ? "is-hidden-skill" : ""} ${item.frozen ? "is-frozen-skill" : ""}">
      <button type="button" class="skill-card-toggle" data-skill-toggle aria-expanded="false">
        <header>
          <div>
            <span>${escapeHtml(item.category || "能力")}</span>
            <h3>${escapeHtml(item.name || "未命名技能")}</h3>
          </div>
          <b>${escapeHtml(item.level_title || "能力状态")}</b>
        </header>
      </button>
      <div class="skill-level-line">
        <span>${badges.length ? badges.map((badge) => `<em>${escapeHtml(badge)}</em>`).join("") : "能力状态"}</span>
        <small>${escapeHtml(item.next_exp ? `${item.exp}/${item.next_exp}` : `${item.exp}`)}</small>
      </div>
      <div class="skill-meter"><i style="width:${escapeHtml(item.progress || 0)}%"></i></div>
      <div class="skill-card-body">
        <p>${escapeHtml(item.description || "")}</p>
        <div class="skill-meta">
          <span>训练 ${escapeHtml(item.training_count || 0)} 次</span>
          <span>最近 ${escapeHtml(item.last_trained || "未训练")}</span>
        </div>
        ${logs.length ? `
          <div class="skill-log">
            ${logs.slice().reverse().map((log) => `
              <p><b>${escapeHtml(log.level_up ? "升级" : `+${log.exp || 0}`)}</b><span>${escapeHtml(log.activity || "日程练习")}</span><small>${escapeHtml(log.time || "")}</small></p>
            `).join("")}
          </div>
        ` : ""}
        <details class="skill-editor">
          <summary>管理</summary>
          <div class="skill-editor-grid">
            <label>名称 <input data-skill-name="${escapeHtml(item.id || "")}" value="${escapeHtml(item.name || "")}" maxlength="32" /></label>
            <label>分类 <input data-skill-category="${escapeHtml(item.id || "")}" value="${escapeHtml(item.category || "")}" maxlength="20" /></label>
            <label>等级
              <select data-skill-level="${escapeHtml(item.id || "")}">
                ${[1, 2, 3, 4, 5, 6].map((level) => `<option value="${level}" ${Number(item.level || 1) === level ? "selected" : ""}>${escapeHtml(skillLevelLabel(level))}</option>`).join("")}
              </select>
            </label>
            <label>经验 <input data-skill-exp="${escapeHtml(item.id || "")}" type="number" min="0" step="1" value="${escapeHtml(item.exp || 0)}" /></label>
            <label class="wide-field">关键词 <input data-skill-keywords="${escapeHtml(item.id || "")}" value="${escapeHtml((item.keywords || []).join(", "))}" maxlength="180" /></label>
            <label class="wide-field">合并别名 <input data-skill-aliases="${escapeHtml(item.id || "")}" value="${escapeHtml((item.aliases || []).join(", "))}" maxlength="180" placeholder="同义叫法会合并到这项技能" /></label>
            <label class="check-field"><input data-skill-hidden="${escapeHtml(item.id || "")}" type="checkbox" ${item.hidden ? "checked" : ""} /> 隐藏：不参与日程、注入和自动成长</label>
            <label class="check-field"><input data-skill-frozen="${escapeHtml(item.id || "")}" type="checkbox" ${item.frozen ? "checked" : ""} /> 冻结成长：保留能力状态，但不再自动加经验</label>
          </div>
          <div class="skill-editor-actions">
            <button type="button" data-skill-save="${escapeHtml(item.id || "")}">保存技能</button>
            <button type="button" class="danger-outline" data-skill-delete="${escapeHtml(item.id || "")}">删除</button>
          </div>
        </details>
      </div>
    </article>
  `;
}

function skillExpFloor(level) {
  return { 1: 0, 2: 100, 3: 260, 4: 520, 5: 900, 6: 1400 }[Number(level || 1)] || 0;
}

function skillLevelLabel(level) {
  return {
    1: "一窍不通",
    2: "会一点点",
    3: "勉强能做",
    4: "基本熟练",
    5: "很熟练",
    6: "很有心得",
  }[Number(level || 1)] || "能力状态";
}

function skillField(id, field) {
  return document.querySelector(`[data-skill-${field}="${CSS.escape(id)}"]`);
}

function bindSkillGrowthActions() {
  document.querySelectorAll("[data-skill-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const card = button.closest(".skill-card");
      if (!card) return;
      const collapsed = card.classList.toggle("is-collapsed");
      button.setAttribute("aria-expanded", String(!collapsed));
    });
  });
  document.querySelectorAll("[data-skill-level]").forEach((select) => {
    select.addEventListener("change", () => {
      const id = select.dataset.skillLevel || "";
      const expInput = skillField(id, "exp");
      if (expInput && Number(expInput.value || 0) < skillExpFloor(select.value)) {
        expInput.value = String(skillExpFloor(select.value));
      }
    });
  });
  document.querySelectorAll("[data-skill-save]").forEach((button) => {
    button.addEventListener("click", async () => {
      const id = button.dataset.skillSave || "";
      await runAction(() => postJson("/skill/update", {
        id,
        name: skillField(id, "name")?.value || "",
        category: skillField(id, "category")?.value || "",
        level: Number(skillField(id, "level")?.value || 1),
        exp: Number(skillField(id, "exp")?.value || 0),
        keywords: skillField(id, "keywords")?.value || "",
        aliases: skillField(id, "aliases")?.value || "",
        hidden: Boolean(skillField(id, "hidden")?.checked),
        frozen: Boolean(skillField(id, "frozen")?.checked),
      }), "已保存技能", button);
    });
  });
  document.querySelectorAll("[data-skill-delete]").forEach((button) => {
    button.addEventListener("click", async () => {
      const id = button.dataset.skillDelete || "";
      if (!requireSecondClick(button, `skill:${id}`, "再次点击删除技能", "再次点击删除")) return;
      await runAction(() => postJson("/skill/update", { id, delete: true }), "已删除技能", button);
    });
  });
}

function renderLifeHero(daily, life) {
  const energy = Number(daily.energy || 0);
  const pct = Math.max(0, Math.min(100, energy));
  const energyText = roleplayEnergyLabel(daily.energy);
  $("#lifeEnergy").textContent = energyText || "--";
  $("#lifeEnergy").title = daily.energy === undefined || daily.energy === "" ? "" : `心理能量 ${formatNumber(energy)}/100`;
  $("#lifeEnergyBar").style.width = `${pct}%`;
  $("#lifeEnergyBar").title = daily.energy === undefined || daily.energy === "" ? "" : `心理能量 ${formatNumber(energy)}/100`;
  $("#lifeMood").textContent = normalizeRoleplayStateText(daily.mood_bias || "平稳");
  $("#lifeNote").textContent = daily.note || daily.sleep || "暂无额外备注";
  $("#lifeLocation").textContent = normalizeLocationText(daily.location);
  $("#lifeWeather").textContent = daily.weather || "暂无天气";
  const current = life.current_plan || {};
  $("#lifeCurrentActivity").textContent = current.activity || "暂无当前日程";
  $("#lifeCurrentSeed").textContent = [current.time, current.mood, current.message_seed].filter(Boolean).join(" · ") || "暂无细化";
}

function roleplayEnergyLabel(value) {
  if (value === undefined || value === null || value === "") return "";
  const energy = Number(value);
  if (!Number.isFinite(energy)) return normalizeRoleplayStateText(value);
  if (energy < 35) return "完全没精神";
  if (energy < 55) return "提不起劲";
  if (energy > 84) return "很精神";
  if (energy > 70) return "精神还不错";
  return "状态一般";
}

function normalizeLocationText(value) {
  const text = String(value || "").trim();
  if (!text || text === "地点感平稳" || text === "地点无明显变化") return "随当前日程变化";
  return text;
}

function normalizeRoleplayStateText(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  const replacements = {
    "黏人": "粘人",
    "睡眠平稳": "睡得很踏实",
    "饥饿感平稳": "无饥饿感",
    "无明显周期影响": "不处于生理期",
  };
  return replacements[text] || text;
}

function normalizeDailyStateForDisplay(daily) {
  if (!daily || typeof daily !== "object") return {};
  const result = { ...daily };
  ["mood_bias", "sleep", "hunger", "body_cycle"].forEach((key) => {
    if (Object.prototype.hasOwnProperty.call(result, key)) {
      result[key] = normalizeRoleplayStateText(result[key]);
    }
  });
  return result;
}

function renderDreamCard(dream) {
  const meta = [
    dream.dream_type || "碎片梦",
    dream.mood ? `情绪 ${dream.mood}` : "",
    dream.duration_hours ? `${dream.duration_hours} 小时` : "",
    dream.generated_at || dream.date || "",
  ].filter(Boolean).join(" · ");
  $("#dreamMeta").textContent = meta || "暂无梦境记录";
  const delta = Number(dream.energy_delta || 0);
  const deltaText = delta > 0 ? `能量 +${delta}` : delta < 0 ? `能量 ${delta}` : "能量平稳";
  $("#dreamAfterglow").textContent = dream.afterglow || dream.label || deltaText;
  $("#dreamContent").textContent = dream.content || dream.label || "暂无梦境内容";
  const factors = Array.isArray(dream.factors) ? dream.factors : [];
  $("#dreamFactors").innerHTML = factors.length
    ? factors.map((item) => `<span class="fragment-chip">${escapeHtml(item)}</span>`).join("")
    : `<span class="muted">暂无梦境因子</span>`;
}

function renderStatePillBoard(daily) {
  const items = [
    ["睡眠", daily.sleep],
    ["睡眠阶段", daily.sleep_phase || daily.sleep_runtime?.label],
    ["梦境", daily.dream],
    ["健康", daily.health],
    ["饥饿", daily.hunger],
    ["生理期状态", daily.body_cycle],
  ].map(([label, value]) => [label, normalizeRoleplayStateText(value)])
    .filter(([, value]) => value !== undefined && value !== "");
  $("#statePillBoard").innerHTML = items.length
    ? items.map(([label, value]) => `
      <span>
        <b>${escapeHtml(label)}</b>
        ${escapeHtml(value || "-")}
      </span>
    `).join("")
    : `<div class="empty small">暂无今日状态</div>`;
}

function renderDiaryCards(diaries) {
  $("#diaryCards").innerHTML = diaries.length
    ? diaries.slice().reverse().map((item) => {
      const tags = Array.isArray(item.tags) && item.tags.length
        ? item.tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")
        : `<span>未标记</span>`;
      return `
        <section class="diary-card">
          <div>
            <b>${escapeHtml(item.date || "未记录日期")}</b>
            <small>${escapeHtml(item.generated_at || "")}</small>
          </div>
          <p>${escapeHtml(item.body || item.summary || "暂无日记正文")}</p>
          ${item.share_seed ? `<blockquote>${escapeHtml(item.share_seed)}</blockquote>` : ""}
          <div class="diary-tags">${tags}</div>
        </section>
      `;
    }).join("")
    : `<div class="empty small">暂无日记</div>`;
}

function renderDreamFragments(fragments) {
  if (!fragments.length) {
    $("#dreamFragments").innerHTML = `<div class="empty small">暂无梦境碎片</div>`;
    return;
  }
  const max = Math.max(1, ...fragments.map((item) => Number(item.weight || 1)));
  $("#dreamFragments").innerHTML = fragments.map((item) => {
    const weight = Number(item.weight || 1);
    const size = 12 + Math.round((weight / max) * 8);
    const title = [item.source, item.created_at, weight ? `权重 ${weight.toFixed(1)}` : ""].filter(Boolean).join(" · ");
    return `<span class="fragment-chip" style="font-size:${size}px" title="${escapeHtml(title)}">${escapeHtml(item.text || "")}</span>`;
  }).join("");
}

function renderCreativeStatusBar(selector, chips) {
  const el = $(selector);
  if (!el) return;
  if (!Array.isArray(chips) || !chips.length) {
    el.innerHTML = "";
    return;
  }
  el.innerHTML = chips.map((chip) => {
    const tone = chip.tone || "info";
    const isOff = tone === "off";
    return `<span class="creative-chip ${escapeHtml(tone)}"${isOff ? ' data-off="1"' : ""}>
      <small>${escapeHtml(chip.label || "")}</small>
      <b>${escapeHtml(chip.value || "")}</b>
    </span>`;
  }).join("");
}

function renderBookshelf() {
  const creative = state.overview?.creative || {};
  const bookshelf = state.bookshelfUnlocked || state.overview?.bookshelf || {};
  const privateReading = state.overview?.private_reading || {};
  const settings = state.overview?.settings || {};
  $("#bookshelfPublicCount").textContent = bookshelf.public_count ?? creative.project_count ?? 0;
  $("#bookshelfSecretCount").textContent = bookshelf.secret_count ?? 0;
  $("#bookshelfDiaryCount").textContent = bookshelf.diary_count ?? 0;
  $("#bookshelfJmCount").textContent = bookshelf.jm_album_count ?? 0;
  $("#bookshelfLockState").textContent = bookshelf.unlocked ? "已解锁" : "未解锁";
  const passwordHint = String(bookshelf.password_hint || "").trim();
  const passwordHintEl = $("#bookshelfPasswordHint");
  if (passwordHintEl) {
    passwordHintEl.textContent = passwordHint ? `提示：${passwordHint}` : "";
    passwordHintEl.hidden = !passwordHint;
  }
  $("#bookshelfIntro").textContent = creative.enabled
    ? "Bot 自主推进的公开创作作品"
    : "创作功能未开启";
  const creativeChips = [];
  creativeChips.push({
    label: "创作",
    value: creative.enabled ? "开启" : "关闭",
    tone: creative.enabled ? "ok" : "off",
  });
  if (creative.enabled) {
    creativeChips.push({
      label: "提起方式",
      value: creative.hidden_mode ? "节点自然提起" : "普通模式",
      tone: "info",
    });
    creativeChips.push({
      label: "灵感触发",
      value: formatPercent(settings.creative_inspiration_probability),
      tone: "metric",
    });
    creativeChips.push({
      label: "节点提起",
      value: formatPercent(settings.creative_share_probability),
      tone: "metric",
    });
    creativeChips.push({
      label: "单次创作",
      value: `${settings.creative_chars_per_session || settings.creative_base_chars_per_hour || 0} 字`,
      tone: "metric",
    });
  }
  if (privateReading.available) {
    creativeChips.push({
      label: "夹层阅读",
      value: privateReading.boredom_read_enabled ? "可触发" : "关闭",
      tone: privateReading.boredom_read_enabled ? "ok" : "off",
    });
    creativeChips.push({
      label: "征求推荐",
      value: privateReading.ask_recommendation_enabled ? "可触发" : "关闭",
      tone: privateReading.ask_recommendation_enabled ? "ok" : "off",
    });
  }
  renderCreativeStatusBar("#creativeSettings", creativeChips);
  const publicBooks = bookshelf.public_books || [];
  $("#bookshelfPublicBooks").innerHTML = publicBooks.length
    ? renderBookCategoryShelves(publicBooks, { emptyText: "上层书架为空", reverseBooks: true })
    : `<div class="empty">上层书架为空</div>`;
  const secretBooks = bookshelf.secret_books || [];
  $("#bookshelfSecretBooks").innerHTML = bookshelf.unlocked
    ? renderUnlockedDrawer(secretBooks)
    : renderLockedDrawer(bookshelf.secret_count || 0);
  const home = $("#bookcaseHome");
  if (home) home.hidden = state.bookshelfPage !== "shelf";
  renderBookDetailPanel();
  void hydrateBookshelfImages(document);
}

function renderLockedDrawer(count) {
  return `
    <div class="drawer-locked">
      <div class="drawer-face">
        <span></span>
        <i></i>
      </div>
      <div>
        <b>${escapeHtml(count || 0)} 本锁在夹层里</b>
        <p>需要密码</p>
      </div>
    </div>
  `;
}

function renderUnlockedDrawer(items) {
  if (!items.length) {
    return `<div class="empty small">抽屉已经打开，但里面暂时还没有日记本或私密阅读记录。</div>`;
  }
  return renderBookCategoryShelves(items, {
    reverseBooks: true,
    notes: {
      "日记": "按日期收进同一本里",
      "私密阅读": "只保留标题和阅读印象",
    },
  });
}

function renderBookCategoryShelves(items, options = {}) {
  const books = Array.isArray(items) ? items.filter(Boolean) : [];
  if (!books.length) return `<div class="empty small">${escapeHtml(options.emptyText || "暂无书籍")}</div>`;
  const groups = [];
  books.forEach((book) => {
    const title = bookshelfCategoryTitle(book);
    let group = groups.find((item) => item.title === title);
    if (!group) {
      group = { title, books: [] };
      groups.push(group);
    }
    group.books.push(book);
  });
  return groups.map((group) => {
    const rowClass = options.rowClass || (group.books.some((book) => book.kind === "diary" || book.kind === "jm_album") ? "drawer-book-row" : "book-row");
    const booksForRender = options.reverseBooks ? group.books.slice().reverse() : group.books;
    const note = options.notes?.[group.title] || bookshelfCategoryNote(group.title, group.books);
    return `
      <section class="drawer-book-group book-category-group ${escapeHtml(categorySlug(group.title))}">
        <header>
          <span>${escapeHtml(group.title)} <b>${escapeHtml(group.books.length)}</b></span>
          <small>${escapeHtml(note)}</small>
        </header>
        <div class="${escapeHtml(rowClass)}">${booksForRender.map(renderBookshelfBook).join("")}</div>
      </section>
    `;
  }).join("");
}

function bookshelfCategoryTitle(book) {
  const raw = String(book?.category || "").trim();
  if (raw) return raw;
  if (book?.kind === "creative") return book.work_type || "创作";
  if (book?.kind === "diary") return "日记";
  if (book?.kind === "browsing") return "浏览记录";
  if (book?.kind === "jm_album") return "私密阅读";
  return "其他";
}

function bookshelfCategoryNote(title, books) {
  const kind = books[0]?.kind || "";
  if (kind === "browsing") return "新闻阅读和主动搜索会在这里留痕";
  if (kind === "creative") return "Bot 自己慢慢推进的文本作品";
  if (kind === "diary") return "按日期翻阅";
  if (kind === "jm_album") return "夹层内的私密阅读记录";
  return `${books.length} 本`;
}

function categorySlug(value) {
  const text = String(value || "category").toLowerCase();
  return text.replace(/[^a-z0-9\u4e00-\u9fff_-]+/g, "-").slice(0, 32) || "category";
}

function renderBookshelfBook(item) {
  const kind = item.kind || "creative";
  const kindLabel = {
    creative: "创作",
    diary: "日记本",
    browsing: "浏览记录",
    jm_album: "私密阅读",
  }[kind] || kind;
  const bookId = bookshelfBookId(item);
  const title = item.title || "未命名";
  const meta = item.progress || item.created || item.status || item.tone || "";
  return `
    <button type="button" class="shelf-book ${escapeHtml(kind)}" data-book-id="${escapeHtml(bookId)}" title="${escapeHtml(title)}">
      <div class="book-spine">
        <i class="book-shine"></i>
        <span>${escapeHtml(kindLabel)}</span>
        <b>${escapeHtml(title)}</b>
        ${meta ? `<small>${escapeHtml(meta)}</small>` : `<small>书柜藏本</small>`}
        <em></em>
      </div>
    </button>
  `;
}

function bookshelfBookId(item) {
  return `${item.kind || "book"}:${item.id || item.title || ""}`;
}

async function ensureCreativeProjectDetail(book, force = false) {
  if (!book || book.kind !== "creative" || !book.id) return book;
  if (book.project_detail_loaded && !force) return book;
  if (book.project_loading && !force) return book;
  book.project_loading = true;
  book.project_error = "";
  try {
    const detail = await fetchJson(`/creative/project?id=${encodeURIComponent(book.id)}`);
    Object.assign(book, detail || {}, { project_detail_loaded: true, project_loading: false, kind: "creative" });
    if (state.selectedBook && state.selectedBook.id === book.id && state.selectedBook.kind === "creative") {
      state.selectedBook = book;
      renderBookDetailPanel();
    }
    return book;
  } catch (error) {
    book.project_loading = false;
    book.project_error = error.message || String(error || "读取失败");
    if (state.selectedBook && state.selectedBook.id === book.id && state.selectedBook.kind === "creative") {
      state.selectedBook = book;
      renderBookDetailPanel();
    }
    return book;
  }
}

function renderBookCoverInner(book, kindLabel, title, progress = "") {
  const coverSrc = book.kind === "jm_album" ? String(book.cover_src || "") : "";
  const image = coverSrc ? bookshelfImageTag(coverSrc, `${title || "私密阅读"}封面`) : "";
  return `
    ${image}
    <span>${escapeHtml(kindLabel)}</span>
    <b>${escapeHtml(title || "未命名")}</b>
    ${progress ? `<small>${escapeHtml(progress)}</small>` : ""}
  `;
}

function renderCreativeStoryBible(book) {
  const sb = book.story_bible && typeof book.story_bible === "object" ? book.story_bible : {};
  const list = (items) => Array.isArray(items) && items.length
    ? `<ul>${items.map((item) => `<li>${escapeHtml(String(item || ""))}</li>`).join("")}</ul>`
    : `<div class="empty small">暂无</div>`;
  return `
    <section class="creative-manager-card">
      <h3>故事档案</h3>
      <div class="creative-meta-grid">
        <div><b>主线</b><span>${escapeHtml(sb.mainline_direction || "") || "未建立"}</span></div>
        <div><b>下一步</b><span>${escapeHtml(sb.next_direction || book.next_hint || "") || "未指定"}</span></div>
      </div>
      <div class="creative-columns">
        <div><b>主题</b>${list(sb.active_themes)}</div>
        <div><b>未解决线索</b>${list(sb.unresolved_threads)}</div>
        <div><b>已解决线索</b>${list(sb.resolved_threads)}</div>
        <div><b>重要事实</b>${list(sb.important_facts)}</div>
      </div>
    </section>
  `;
}

function renderCreativeOutlineEditor(book) {
  const rows = Array.isArray(book.outline) ? book.outline : [];
  return `
    <section class="creative-manager-card">
      <div class="creative-card-head">
        <h3>大纲</h3>
        <span>${rows.length} 条</span>
      </div>
      <form data-creative-outline-form data-project-id="${escapeHtml(book.id || "")}">
        <textarea name="outline" rows="10" placeholder="一行一条大纲">${escapeHtml(rows.join("\n"))}</textarea>
        <div class="creative-form-actions">
          <button type="submit">保存大纲</button>
        </div>
      </form>
    </section>
  `;
}

function renderCreativeCharactersEditor(book) {
  const chars = Array.isArray(book.characters) ? book.characters : [];
  return `
    <section class="creative-manager-card">
      <div class="creative-card-head">
        <h3>角色</h3>
        <span>${chars.length} 个</span>
      </div>
      <form data-creative-characters-form data-project-id="${escapeHtml(book.id || "")}">
        <textarea name="characters" rows="12" placeholder='[{"name":"角色名","role":"身份","description":"描述"}]'>${escapeHtml(JSON.stringify(chars, null, 2))}</textarea>
        <div class="creative-form-actions">
          <button type="submit">保存角色</button>
        </div>
      </form>
    </section>
  `;
}

function renderCreativeQualityPanel(book) {
  const reviews = Array.isArray(book.quality_reviews) ? book.quality_reviews : [];
  const latest = reviews[reviews.length - 1] || {};
  const score = (name) => Number(latest?.[`${name}_score`] ?? latest?.scores?.[name] ?? 0) || 0;
  const issues = Array.isArray(latest.issues) ? latest.issues : [];
  return `
    <section class="creative-manager-card">
      <div class="creative-card-head">
        <h3>质量分析</h3>
        <div class="creative-inline-actions">
          <button type="button" data-creative-reanalyze="${escapeHtml(book.id || "")}">重新分析</button>
          <button type="button" data-creative-rebuild-memory="${escapeHtml(book.id || "")}">重建记忆</button>
        </div>
      </div>
      <div class="creative-meta-grid quality">
        <div><b>人格一致</b><span>${escapeHtml(score("persona"))}/10</span></div>
        <div><b>推进度</b><span>${escapeHtml(score("progress"))}/10</span></div>
        <div><b>重复度</b><span>${escapeHtml(score("repetition"))}/10</span></div>
      </div>
      ${issues.length ? `<ul class="creative-issues">${issues.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : `<div class="empty small">暂无分析结果</div>`}
    </section>
  `;
}

function renderCreativeChunkEditors(book) {
  const chunks = Array.isArray(book.chunks) ? book.chunks : [];
  if (!chunks.length) {
    return `<section class="creative-manager-card"><h3>正文片段</h3><div class="empty small">暂无正文片段</div></section>`;
  }
  return `
    <section class="creative-manager-card">
      <h3>正文片段</h3>
      <div class="creative-chunk-editor-list">
        ${chunks.map((chunk) => `
          <form class="creative-chunk-editor" data-creative-chunk-form data-project-id="${escapeHtml(book.id || "")}" data-chunk-index="${escapeHtml(chunk.index)}">
            <header>
              <b>片段 ${escapeHtml(Number(chunk.index) + 1)}</b>
              <span>${escapeHtml(chunk.chars || String((chunk.text || "").length))} 字${chunk.manually_edited ? " · 已手改" : ""}</span>
            </header>
            <textarea name="text" rows="8">${escapeHtml(chunk.text || "")}</textarea>
            <div class="creative-form-actions">
              <button type="submit">保存本段</button>
            </div>
          </form>
        `).join("")}
      </div>
    </section>
  `;
}

function renderCreativeManager(book, kindLabel, displayTitle, displayIntro) {
  if (book.project_loading) {
    return `<article class="book-preview subpage creative"><div class="empty">正在加载作品详情...</div></article>`;
  }
  if (book.project_error) {
    return `<article class="book-preview subpage creative"><div class="empty">作品详情加载失败：${escapeHtml(book.project_error)}</div></article>`;
  }
  return `
    <article class="book-preview subpage creative creative-manager-page">
      <div class="book-preview-cover ${book.cover_src ? "has-cover-image" : ""}">
        ${renderBookCoverInner(book, kindLabel, book.title || "未命名")}
      </div>
      <div class="book-preview-info creative-manager-info">
        <nav class="book-breadcrumb">
          <button type="button" data-book-close>书柜</button>
          <span>/ ${escapeHtml(kindLabel)}</span>
        </nav>
        <div class="reader-toolbar">
          <span>${escapeHtml(kindLabel)}</span>
          <button type="button" data-book-close>收回书柜</button>
        </div>
        <h2>${escapeHtml(book.title || displayTitle || "未命名")}</h2>
        <p>${escapeHtml(book.premise || displayIntro || "这本作品还没有简介。")}</p>
        <form class="inline-form creative-project-basic-form" data-creative-project-form data-project-id="${escapeHtml(book.id || "")}">
          <label>标题 <input name="title" value="${escapeHtml(book.title || "")}" /></label>
          <label>类型 <input name="work_type" value="${escapeHtml(book.work_type || "")}" /></label>
          <label>气质 <input name="tone" value="${escapeHtml(book.tone || "")}" /></label>
          <label>视角 <input name="point_of_view" value="${escapeHtml(book.point_of_view || "")}" /></label>
          <label>目标字数 <input name="target_chars" type="number" min="300" max="5200" value="${escapeHtml(book.target_chars || 2000)}" /></label>
          <label class="wide">核心设定 <textarea name="premise" rows="3">${escapeHtml(book.premise || "")}</textarea></label>
          <label class="wide">下一步提示 <textarea name="next_hint" rows="2">${escapeHtml(book.next_hint || "")}</textarea></label>
          <div class="creative-form-actions wide">
            <button type="submit">保存作品信息</button>
            <button type="button" class="read-button" data-book-read>进入阅读页</button>
          </div>
        </form>
        <div class="creative-form-actions">
          <button type="button" class="danger-outline" data-creative-exit-edit>返回预览</button>
          <button type="button" class="danger-outline" data-book-delete data-book-kind="creative" data-book-id="${escapeHtml(book.id || "")}" data-book-title="${escapeHtml(book.title || displayTitle || "")}">删除作品</button>
        </div>
        ${renderCreativeStoryBible(book)}
        ${renderCreativeOutlineEditor(book)}
        ${renderCreativeCharactersEditor(book)}
        ${renderCreativeQualityPanel(book)}
        ${renderCreativeChunkEditors(book)}
      </div>
    </article>
  `;
}

function renderCreativeBookPreview(book, kindLabel, displayTitle, displayIntro, displayContent) {
  if (book.project_loading) {
    return `<article class="book-preview subpage creative"><div class="empty">正在加载作品详情...</div></article>`;
  }
  if (book.project_error) {
    return `<article class="book-preview subpage creative"><div class="empty">作品详情加载失败：${escapeHtml(book.project_error)}</div></article>`;
  }
  const sb = book.story_bible && typeof book.story_bible === "object" ? book.story_bible : {};
  const outline = Array.isArray(book.outline) ? book.outline : [];
  const characters = Array.isArray(book.characters) ? book.characters : [];
  const reviews = Array.isArray(book.quality_reviews) ? book.quality_reviews : [];
  const latestReview = reviews[reviews.length - 1] || {};
  const latestSnippet = Array.isArray(book.chunks) && book.chunks.length ? book.chunks[book.chunks.length - 1].text || "" : displayContent || "";
  const score = (name) => Number(latestReview?.[`${name}_score`] ?? latestReview?.scores?.[name] ?? 0) || 0;
  return `
    <article class="book-preview subpage creative creative-preview-card">
      <div class="book-preview-cover ${book.cover_src ? "has-cover-image" : ""}">
        ${renderBookCoverInner(book, kindLabel, book.title || displayTitle || "未命名")}
      </div>
      <div class="book-preview-info creative-manager-info">
        <nav class="book-breadcrumb">
          <button type="button" data-book-close>书柜</button>
          <span>/ ${escapeHtml(kindLabel)}</span>
        </nav>
        <div class="reader-toolbar">
          <span>${escapeHtml(kindLabel)}</span>
          <button type="button" data-book-close>收回书柜</button>
        </div>
        <h2>${escapeHtml(book.title || displayTitle || "未命名")}</h2>
        <p>${escapeHtml(book.premise || displayIntro || "这本作品还没有简介。")}</p>
        <dl>
          ${book.status ? `<div><dt>状态</dt><dd>${escapeHtml(book.status)}</dd></div>` : ""}
          ${book.tone ? `<div><dt>气质</dt><dd>${escapeHtml(book.tone)}</dd></div>` : ""}
          ${book.point_of_view ? `<div><dt>视角</dt><dd>${escapeHtml(book.point_of_view)}</dd></div>` : ""}
          ${book.progress ? `<div><dt>进度</dt><dd>${escapeHtml(book.progress)}</dd></div>` : ""}
          ${book.created ? `<div><dt>入柜</dt><dd>${escapeHtml(book.created)}</dd></div>` : ""}
        </dl>
        <section class="creative-manager-card">
          <h3>故事档案</h3>
          <div class="creative-meta-grid">
            <div><b>主线</b><span>${escapeHtml(sb.mainline_direction || "") || "未建立"}</span></div>
            <div><b>下一步</b><span>${escapeHtml(sb.next_direction || book.next_hint || "") || "未指定"}</span></div>
          </div>
        </section>
        <section class="creative-manager-card">
          <h3>大纲预览</h3>
          ${outline.length ? `<ul class="creative-issues">${outline.slice(0, 8).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : `<div class="empty small">暂无大纲</div>`}
        </section>
        <section class="creative-manager-card">
          <h3>角色预览</h3>
          ${characters.length ? `<ul class="creative-issues">${characters.slice(0, 6).map((item) => `<li><b>${escapeHtml(item.name || "未命名角色")}</b>${item.role ? ` · ${escapeHtml(item.role)}` : ""}</li>`).join("")}</ul>` : `<div class="empty small">暂无角色</div>`}
        </section>
        <section class="creative-manager-card">
          <h3>最近质量分析</h3>
          <div class="creative-meta-grid quality">
            <div><b>人格一致</b><span>${escapeHtml(score("persona"))}/10</span></div>
            <div><b>推进度</b><span>${escapeHtml(score("progress"))}/10</span></div>
            <div><b>重复度</b><span>${escapeHtml(score("repetition"))}/10</span></div>
          </div>
        </section>
        <section class="creative-manager-card">
          <h3>最新片段</h3>
          <div class="reader-content creative-content ${escapeHtml(creativeReadingMode(book))}">${formatCreativeContentByMode(latestSnippet || "这本书还没有正文。", creativeReadingMode(book))}</div>
        </section>
        <div class="creative-form-actions">
          <button type="button" data-creative-enter-edit>编辑作品</button>
          <button type="button" class="read-button" data-book-read>进入阅读页</button>
          <button type="button" class="danger-outline" data-book-delete data-book-kind="creative" data-book-id="${escapeHtml(book.id || "")}" data-book-title="${escapeHtml(book.title || displayTitle || "")}">删除作品</button>
        </div>
      </div>
    </article>
  `;
}

function bookTagInputValue(tags) {
  return Array.isArray(tags) ? tags.filter(Boolean).join("、") : "";
}

function parseBookTagInput(value) {
  const seen = new Set();
  return String(value || "")
    .split(/[,，、\s\n\r]+/)
    .map((item) => item.trim())
    .filter((item) => {
      if (!item) return false;
      const key = item.toLocaleLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 8);
}

function mergeBookTagCandidates(...groups) {
  const seen = new Set();
  const result = [];
  groups.flat().forEach((tag) => {
    const text = String(tag || "").trim();
    if (!text) return;
    const key = text.toLocaleLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    result.push(text);
  });
  return result.slice(0, 16);
}

function renderBookPreferenceEditor(book) {
  if (book.kind !== "jm_album") return "";
  const likedValue = bookTagInputValue(book.user_liked_tags);
  const dislikedValue = bookTagInputValue(book.user_disliked_tags);
  const sourceTags = Array.isArray(book.tags) ? book.tags.filter(Boolean) : [];
  const preferenceTags = Array.isArray(book.preference_tags) ? book.preference_tags.filter(Boolean) : [];
  const preferenceChips = preferenceTags.length
    ? `<div class="book-preference-auto-tags"><span>Bot 标注</span>${preferenceTags.map((tag) => `<b>${escapeHtml(tag)}</b>`).join("")}</div>`
    : "";
  const candidates = mergeBookTagCandidates(sourceTags, preferenceTags);
  const candidatePicker = candidates.length
    ? `
      <div class="book-preference-candidates">
        <span>现有标签</span>
        ${candidates.map((tag) => `
          <div class="book-preference-candidate">
            <b>${escapeHtml(tag)}</b>
            <button type="button" data-book-tag-pick data-tag-target="liked" data-tag-value="${escapeHtml(tag)}">喜好</button>
            <button type="button" data-book-tag-pick data-tag-target="disliked" data-tag-value="${escapeHtml(tag)}">厌恶</button>
          </div>
        `).join("")}
      </div>
    `
    : "";
  return `
    <form class="book-preference-editor" data-book-preference-form>
      <header>
        <span>偏好标签</span>
        <button type="submit">保存标签</button>
      </header>
      <div class="book-preference-fields">
        <label>
          <span>喜好</span>
          <input type="text" name="liked_tags" value="${escapeHtml(likedValue)}" placeholder="画风、节奏、设定">
        </label>
        <label>
          <span>厌恶</span>
          <input type="text" name="disliked_tags" value="${escapeHtml(dislikedValue)}" placeholder="拖沓、雷点、题材">
        </label>
      </div>
      ${candidatePicker}
      ${preferenceChips}
    </form>
  `;
}

function allBookshelfBooks() {
  const bookshelf = state.bookshelfUnlocked || state.overview?.bookshelf || {};
  return [
    ...(bookshelf.public_books || []),
    ...(bookshelf.secret_books || []),
  ];
}

function selectBookshelfBook(bookId) {
  const book = allBookshelfBooks().find((item) => bookshelfBookId(item) === bookId);
  if (!book) return;
  state.selectedBook = book;
  state.bookshelfPage = "detail";
  state.creativeEditing = false;
  state.selectedBookSpreadIndex = 0;
  if (book.kind === "diary") {
    const entries = Array.isArray(book.entries) ? book.entries : [];
    state.selectedDiaryDate = entries[entries.length - 1]?.date || "";
  }
  if (book.kind === "browsing") {
    const entries = Array.isArray(book.entries) ? book.entries : [];
    state.selectedBrowsingIndex = Math.max(0, entries.length - 1);
  }
  renderBookDetailPanel();
  if (book.kind === "creative") {
    void ensureCreativeProjectDetail(book);
  }
  const panel = $("#bookDetailPanel");
  if (panel) panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderBookDetailPanel() {
  const panel = $("#bookDetailPanel");
  if (!panel) return;
  const book = state.selectedBook;
  if (!book || state.bookshelfPage === "shelf") {
    panel.hidden = true;
    panel.innerHTML = "";
    return;
  }
  const kindLabel = {
    creative: book.work_type || "创作书",
    diary: "日记本",
    browsing: "浏览记录",
    jm_album: "私密阅读",
  }[book.kind] || "书";
  const entryBook = ["diary", "browsing"].includes(book.kind || "");
  const diaryEntries = entryBook && Array.isArray(book.entries) ? book.entries : [];
  const selectedDiaryDate = state.selectedDiaryDate || diaryEntries[diaryEntries.length - 1]?.date || "";
  const diaryEntry = diaryEntries.find((entry) => entry.date === selectedDiaryDate) || diaryEntries[diaryEntries.length - 1] || null;
  if (entryBook && diaryEntry && state.selectedDiaryDate !== diaryEntry.date) {
    state.selectedDiaryDate = diaryEntry.date;
  }
  const displayTitle = entryBook && diaryEntry
    ? (book.kind === "diary" ? `${diaryEntry.date} 的日记` : (diaryEntry.title || diaryEntry.date || "浏览记录"))
    : (book.title || "未命名");
  const displayIntro = entryBook && diaryEntry ? (diaryEntry.intro || book.intro) : (book.intro || book.progress || "这本书还没有简介。");
  const displayContent = entryBook && diaryEntry ? (diaryEntry.content || diaryEntry.intro || book.content) : (book.content || book.intro || "这本书暂时没有正文。");
  const diarySelector = diaryEntries.length
    ? `
      <label class="diary-date-picker">
        <span>${book.kind === "diary" ? "日期" : "记录"}</span>
        <select data-diary-date>
          ${diaryEntries.slice().reverse().map((entry) => `<option value="${escapeHtml(entry.date)}"${entry.date === state.selectedDiaryDate ? " selected" : ""}>${escapeHtml(entry.date)}</option>`).join("")}
        </select>
      </label>
    `
    : "";
  const activeTags = entryBook && diaryEntry ? diaryEntry.tags : book.tags;
  const tags = Array.isArray(activeTags) && activeTags.length
    ? `<div class="book-tags">${activeTags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}</div>`
    : "";
  const preferenceEditor = renderBookPreferenceEditor(book);
  const readingImpressionText = book.kind === "jm_album"
    ? String(book.reading_impression || book.impression || "").replace(/^读后感[:：]\s*/, "").trim()
    : "";
  const pageCommentCount = book.kind === "jm_album"
    ? Number(book.page_comment_count ?? (Array.isArray(book.page_comments) ? book.page_comments.length : 0))
    : 0;
  const botRating = Number(book.rating || 0);
  const userRating = Number(book.user_rating || 0);
  const ratingReason = String(book.user_rating_reason || book.rating_reason || "").trim();
  const ratingMeta = book.kind === "jm_album" && (botRating || userRating || ratingReason)
    ? `
      <div class="book-rating-row">
        ${botRating ? `<span>Bot 评分 <b>${escapeHtml(botRating)}/10</b></span>` : ""}
        ${userRating ? `<span>你的评分 <b>${escapeHtml(userRating)}/10</b></span>` : ""}
        ${ratingReason ? `<small>${escapeHtml(ratingReason)}</small>` : ""}
      </div>
    `
    : "";
  const readingImpression = readingImpressionText
    ? `
      <section class="book-reading-impression">
        <span>Bot 的读后感</span>
        ${ratingMeta}
        <p>${escapeHtml(readingImpressionText)}</p>
      </section>
    `
    : ratingMeta
      ? `<section class="book-reading-impression"><span>读后评分</span>${ratingMeta}</section>`
    : "";
  const manageActions = book.kind === "browsing" ? "" : `
    <div class="book-manage-actions">
      ${book.kind === "jm_album" ? `<button type="button" data-book-reread>让 Bot 重读</button>` : ""}
      <button type="button" class="danger-outline" data-book-delete
        data-book-kind="${escapeHtml(book.kind || "")}"
        data-book-id="${escapeHtml(book.id || "")}"
        data-book-album-id="${escapeHtml(book.album_id || "")}"
        data-book-title="${escapeHtml(book.title || "")}"
        data-book-date="${escapeHtml(book.kind === "diary" ? (state.selectedDiaryDate || "") : "")}">
        ${escapeHtml(book.kind === "diary" ? "删除当前日记" : "从书柜移除")}
      </button>
    </div>
  `;
  panel.hidden = false;
  if (state.bookshelfPage === "reader" && book.kind === "jm_album" && Array.isArray(book.pages) && book.pages.length) {
    panel.innerHTML = renderJmAlbumReader(book, kindLabel, displayTitle, displayIntro, readingImpression);
    void hydrateBookshelfImages(panel);
    return;
  }
  if (state.bookshelfPage === "reader" && book.kind === "diary") {
    panel.innerHTML = renderDiaryBookReader(book, kindLabel, diaryEntries, diaryEntry);
    return;
  }
  if (state.bookshelfPage === "reader" && book.kind === "browsing") {
    panel.innerHTML = renderBrowsingBookReader(book, kindLabel, diaryEntries);
    return;
  }
  if (state.bookshelfPage === "reader" && book.kind === "creative") {
    panel.innerHTML = renderCreativeBookReader(book, kindLabel, displayTitle, displayIntro, displayContent);
    return;
  }
  if (book.kind === "creative" && state.bookshelfPage === "detail") {
    panel.innerHTML = state.creativeEditing
      ? renderCreativeManager(book, kindLabel, displayTitle, displayIntro)
      : renderCreativeBookPreview(book, kindLabel, displayTitle, displayIntro, displayContent);
    return;
  }
  panel.innerHTML = state.bookshelfPage === "reader"
    ? `
      <article class="reader-page subpage ${escapeHtml(book.kind || "book")}">
        <nav class="book-breadcrumb">
          <button type="button" data-book-close>书柜</button>
          <span>/</span>
          <button type="button" data-book-back>${escapeHtml(book.title || "未命名")}</button>
          <span>/ 阅读</span>
        </nav>
        <div class="reader-toolbar">
          <button type="button" data-book-back>返回简介</button>
          <span>${escapeHtml(kindLabel)}</span>
          <button type="button" data-book-close>收回书柜</button>
        </div>
        <div class="reader-book-shell">
          <aside class="reader-cover ${book.cover_src ? "has-cover-image" : ""}">
            ${renderBookCoverInner(book, kindLabel, displayTitle, book.progress || "")}
          </aside>
          <section class="reader-paper">
            <header class="reader-page-head">
              <span>${escapeHtml(kindLabel)}</span>
              <h2>${escapeHtml(displayTitle)}</h2>
              ${displayIntro ? `<p>${escapeHtml(displayIntro)}</p>` : ""}
            </header>
            <div class="reader-content">${formatBookContent(displayContent || "这本书暂时没有正文。")}</div>
            <footer class="reader-page-foot">
              <span>${escapeHtml(book.created || "书柜藏本")}</span>
              <span>${escapeHtml(book.tone || book.status || "")}</span>
            </footer>
          </section>
        </div>
      </article>
    `
    : `
      <article class="book-preview subpage ${escapeHtml(book.kind || "book")}">
        <div class="book-preview-cover ${book.cover_src ? "has-cover-image" : ""}">
          ${renderBookCoverInner(book, kindLabel, book.title || "未命名")}
        </div>
        <div class="book-preview-info">
          <nav class="book-breadcrumb">
            <button type="button" data-book-close>书柜</button>
            <span>/ ${escapeHtml(kindLabel)}</span>
          </nav>
          <div class="reader-toolbar">
            <span>${escapeHtml(kindLabel)}</span>
            <button type="button" data-book-close>收回书柜</button>
          </div>
          <h2>${escapeHtml(book.title || "未命名")}</h2>
          <p>${escapeHtml(displayIntro)}</p>
          ${diarySelector}
          <dl>
            ${book.status ? `<div><dt>状态</dt><dd>${escapeHtml(book.status)}</dd></div>` : ""}
            ${book.author ? `<div><dt>作者</dt><dd>${escapeHtml(book.author)}</dd></div>` : ""}
            ${book.tone ? `<div><dt>气质</dt><dd>${escapeHtml(book.tone)}</dd></div>` : ""}
            ${book.point_of_view ? `<div><dt>视角</dt><dd>${escapeHtml(book.point_of_view)}</dd></div>` : ""}
            ${book.progress ? `<div><dt>进度</dt><dd>${escapeHtml(book.progress)}</dd></div>` : ""}
            ${book.kind === "jm_album" ? `<div><dt>备注</dt><dd>${escapeHtml(pageCommentCount)} 条</dd></div>` : ""}
            ${book.created ? `<div><dt>入柜</dt><dd>${escapeHtml(book.created)}</dd></div>` : ""}
          </dl>
          ${readingImpression}
          ${tags}
          ${preferenceEditor}
          ${manageActions}
          <button type="button" class="read-button" data-book-read>开始阅读</button>
        </div>
      </article>
    `;
  void hydrateBookshelfImages(panel);
}

function renderJmAlbumReader(book, kindLabel, displayTitle, displayIntro, readingImpression = "") {
  const pages = Array.isArray(book.pages) ? book.pages : [];
  const pageCommentCount = Number(book.page_comment_count ?? (Array.isArray(book.page_comments) ? book.page_comments.length : pages.filter((page) => String(page.comment || "").trim()).length));
  const maxStart = Math.max(0, pages.length - (pages.length % 2 === 0 ? 2 : 1));
  const start = Math.min(Math.max(0, Number(state.selectedBookSpreadIndex || 0)), maxStart);
  state.selectedBookSpreadIndex = start % 2 === 0 ? start : start - 1;
  const spread = pages.slice(state.selectedBookSpreadIndex, state.selectedBookSpreadIndex + 2);
  const firstPage = spread[0]?.index || state.selectedBookSpreadIndex + 1;
  const lastPage = spread[spread.length - 1]?.index || firstPage;
  const isLastSpread = state.selectedBookSpreadIndex + 2 >= pages.length;
  const userRating = Number(book.user_rating || 0);
  const userRatingReason = String(book.user_rating_reason || "").trim();
  const ratingPanel = isLastSpread
    ? `
      <section class="manga-rating-panel">
        <div>
          <span>读完评分</span>
          <b>${userRating ? `你给了 ${escapeHtml(userRating)}/10` : "读完后给它打个分"}</b>
          ${userRatingReason ? `<p>${escapeHtml(userRatingReason)}</p>` : ""}
        </div>
        <div class="manga-rating-buttons" data-book-rating-album="${escapeHtml(book.album_id || "")}">
          ${Array.from({ length: 10 }, (_, index) => {
            const value = index + 1;
            return `<button type="button" data-book-rating="${value}" class="${userRating === value ? "is-active" : ""}">${value}</button>`;
          }).join("")}
        </div>
      </section>
    `
    : "";
  return `
    <article class="reader-page subpage jm_album image-reader">
      <nav class="book-breadcrumb">
        <button type="button" data-book-close>书柜</button>
        <span>/</span>
        <button type="button" data-book-back>${escapeHtml(book.title || "未命名")}</button>
        <span>/ 阅读</span>
      </nav>
      <div class="reader-toolbar">
        <button type="button" data-book-back>返回简介</button>
        <span>${escapeHtml(kindLabel)} · ${escapeHtml(firstPage)}-${escapeHtml(lastPage)} / ${escapeHtml(pages.length)} · 备注 ${escapeHtml(pageCommentCount)} 条</span>
        <button type="button" data-book-close>收回书柜</button>
      </div>
      <div class="manga-reader-shell">
        <header class="manga-reader-head">
          <div>
            <span>${escapeHtml(kindLabel)}</span>
            <h2>${escapeHtml(displayTitle)}</h2>
            ${displayIntro ? `<p>${escapeHtml(displayIntro)}</p>` : ""}
          </div>
          <div class="manga-reader-actions">
            <button type="button" data-book-prev ${state.selectedBookSpreadIndex <= 0 ? "disabled" : ""}>上一页</button>
            <button type="button" data-book-next ${state.selectedBookSpreadIndex + 2 >= pages.length ? "disabled" : ""}>下一页</button>
            <button type="button" data-book-reread>让 Bot 重读</button>
            <button type="button" class="danger-outline" data-book-delete
              data-book-kind="${escapeHtml(book.kind || "")}"
              data-book-id="${escapeHtml(book.id || "")}"
              data-book-album-id="${escapeHtml(book.album_id || "")}"
              data-book-title="${escapeHtml(book.title || "")}">从书柜移除</button>
          </div>
        </header>
        ${readingImpression}
        <div class="manga-spread">
          ${spread.map((page, spreadIndex) => {
            const comment = String(page.comment || "").trim();
            const note = comment
              ? `<details class="manga-page-note ${spreadIndex === 0 ? "left" : "right"}"><summary>Bot 批注</summary><p>${escapeHtml(comment)}</p></details>`
              : "";
            return `
            <figure class="manga-page ${comment ? `has-note ${spreadIndex === 0 ? "note-left" : "note-right"}` : ""}">
              ${spreadIndex === 0 ? note : ""}
              <div class="manga-page-image">
                ${bookshelfImageTag(page.src, `${book.title || "私密阅读"} 第 ${page.index} 页`)}
                <figcaption>${escapeHtml(page.index)} / ${escapeHtml(pages.length)}</figcaption>
              </div>
              ${spreadIndex !== 0 ? note : ""}
            </figure>
          `; }).join("")}
          ${spread.length < 2 ? `<figure class="manga-page blank"><span>末页</span></figure>` : ""}
        </div>
        ${ratingPanel}
      </div>
    </article>
  `;
}

function renderDiaryBookReader(book, kindLabel, entries, selectedEntry) {
  const rows = Array.isArray(entries) ? entries : [];
  const current = selectedEntry || rows[rows.length - 1] || {};
  const currentDate = current.date || state.selectedDiaryDate || "";
  const tags = Array.isArray(current.tags) && current.tags.length
    ? `<div class="book-tags">${current.tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}</div>`
    : "";
  return `
    <article class="reader-page subpage diary diary-reader-page">
      <nav class="book-breadcrumb">
        <button type="button" data-book-close>书柜</button>
        <span>/</span>
        <button type="button" data-book-back>${escapeHtml(book.title || "日记本")}</button>
        <span>/ 阅读</span>
      </nav>
      <div class="reader-toolbar">
        <button type="button" data-book-back>返回简介</button>
        <span>${escapeHtml(kindLabel)} · ${escapeHtml(rows.length)} 天</span>
        <button type="button" data-book-close>收回书柜</button>
      </div>
      <div class="diary-reader-shell">
        <aside class="diary-date-rail">
          <header>
            <span>日期</span>
            <b>${escapeHtml(rows.length)}</b>
          </header>
          <div class="diary-date-list">
            ${rows.slice().reverse().map((entry) => `
              <button type="button" data-diary-jump="${escapeHtml(entry.date || "")}" class="${entry.date === currentDate ? "is-active" : ""}">
                <b>${escapeHtml(entry.date || "某天")}</b>
                <span>${escapeHtml(shortName(entry.intro || entry.content || "没有摘要", 34))}</span>
              </button>
            `).join("")}
          </div>
        </aside>
        <section class="reader-paper diary-paper">
          <header class="reader-page-head">
            <span>${escapeHtml(current.generated_at || "日记")}</span>
            <h2>${escapeHtml(currentDate ? `${currentDate} 的日记` : "日记")}</h2>
            ${current.intro ? `<p>${escapeHtml(current.intro)}</p>` : ""}
          </header>
          ${tags}
          <div class="reader-content diary-reader-content">${formatBookContent(current.content || current.intro || "这一天的日记暂时没有正文。")}</div>
          <footer class="reader-page-foot">
            <span>${escapeHtml(book.created || "夹层日记")}</span>
            <button type="button" class="danger-outline" data-book-delete
              data-book-kind="diary"
              data-book-id="${escapeHtml(book.id || "")}"
              data-book-title="${escapeHtml(book.title || "")}"
              data-book-date="${escapeHtml(currentDate)}">删除当前日记</button>
          </footer>
        </section>
      </div>
    </article>
  `;
}

function renderBrowsingBookReader(book, kindLabel, entries) {
  const rows = Array.isArray(entries) ? entries : [];
  const safeIndex = rows.length ? Math.max(0, Math.min(rows.length - 1, Number(state.selectedBrowsingIndex || 0))) : 0;
  state.selectedBrowsingIndex = safeIndex;
  const current = rows[safeIndex] || {};
  const tags = Array.isArray(current.tags) && current.tags.length
    ? `<div class="book-tags">${current.tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}</div>`
    : "";
  const url = String(current.source_url || "").trim();
  const href = browsingSourceHref(url);
  const addressText = url || current.query || current.title || "about:blank";
  return `
    <article class="reader-page subpage browsing browsing-reader-page">
      <nav class="book-breadcrumb">
        <button type="button" data-book-close>书柜</button>
        <span>/</span>
        <button type="button" data-book-back>${escapeHtml(book.title || "浏览记录")}</button>
        <span>/ 阅读</span>
      </nav>
      <div class="browser-shell">
        <header class="browser-chrome">
          <div class="browser-dots"><i></i><i></i><i></i></div>
          <div class="browser-address">
            <span>${escapeHtml(current.source_label || kindLabel)}</span>
            <b title="${escapeHtml(addressText)}">${escapeHtml(addressText)}</b>
          </div>
          <button type="button" data-book-close>收回书柜</button>
        </header>
        <div class="browser-body">
          <aside class="browser-history-sidebar">
            <header>
              <b>历史记录</b>
              <span>${escapeHtml(rows.length)} 条</span>
            </header>
            <div class="browser-history-list">
              ${rows.slice().reverse().map((entry) => {
                const originalIndex = rows.indexOf(entry);
                return `
                  <button type="button" data-browsing-index="${escapeHtml(originalIndex)}" class="${originalIndex === safeIndex ? "is-active" : ""}">
                    <span>${escapeHtml(entry.source_label || "记录")}</span>
                    <b>${escapeHtml(entry.title || entry.query || "未命名记录")}</b>
                    <small>${escapeHtml(entry.generated_at || entry.date || "")}</small>
                  </button>
                `;
              }).join("")}
            </div>
          </aside>
          <section class="browser-page-view">
            <header>
              <span>${escapeHtml(current.source_label || "浏览记录")}</span>
              <h2>${escapeHtml(current.title || current.query || "未命名记录")}</h2>
              ${current.query ? `<p>搜索词：${escapeHtml(current.query)}</p>` : ""}
            </header>
            ${tags}
            <div class="browser-note-content">${formatBookContent(current.content || current.intro || "这条浏览记录暂时没有正文。")}</div>
            <footer>
              <span>${escapeHtml(current.generated_at || current.date || "未知时间")}</span>
              ${url ? `<button type="button" class="browser-source-copy" data-copy-source-url="${escapeHtml(href)}">复制链接</button>` : ""}
            </footer>
          </section>
        </div>
      </div>
    </article>
  `;
}

function browsingSourceHref(url) {
  const text = String(url || "").trim();
  if (!text) return "";
  if (/^(https?:|mailto:)/i.test(text)) return text;
  return `https://${text.replace(/^\/+/, "")}`;
}

function setBookshelfUnlockMessage(text, tone = "") {
  const message = $("#bookshelfUnlockMessage");
  if (!message) return;
  message.textContent = text || "";
  message.classList.toggle("ok", tone === "ok");
  message.classList.toggle("error", tone === "error");
}

function formatBookContent(value) {
  const parts = String(value || "")
    .split(/\n{2,}/)
    .map((part) => part.trim())
    .filter(Boolean);
  return parts.length
    ? parts.map((part) => `<p>${escapeHtml(part)}</p>`).join("")
    : `<p>这本书暂时没有正文。</p>`;
}

function creativeReadingMode(book) {
  const type = String(book?.work_type || book?.category || "").toLowerCase();
  if (/诗|歌词|歌/.test(type)) return "poetry";
  if (/剧本|短剧|脚本|对白|分镜/.test(type)) return "script";
  if (/设定|世界观|角色|图鉴|怪谈|档案/.test(type)) return "lore";
  if (/随笔|散文|札记|观察|影评|读后感/.test(type)) return "essay";
  return "prose";
}

function creativeModeLabel(mode) {
  return {
    poetry: "诗页",
    script: "剧本",
    lore: "设定册",
    essay: "札记",
    prose: "正文",
  }[mode] || "正文";
}

function splitCreativeChunks(book, fallbackContent) {
  const chunks = Array.isArray(book?.chunks) ? book.chunks : [];
  const rows = chunks
    .map((chunk, index) => ({
      index: Number(chunk.index || index + 1),
      text: String(chunk.text || "").trim(),
      created: String(chunk.created || "").trim(),
    }))
    .filter((chunk) => chunk.text);
  if (rows.length) return rows;
  return [{ index: 1, text: String(fallbackContent || "").trim() || "这本书还没有正文。", created: "" }];
}

function formatCreativeScript(text) {
  const lines = String(text || "").split(/\n+/).map((line) => line.trim()).filter(Boolean);
  return lines.length
    ? lines.map((line) => {
      const match = line.match(/^([^：:]{1,14})[：:]\s*(.+)$/);
      if (match) {
        return `<p class="script-line"><b>${escapeHtml(match[1])}</b><span>${escapeHtml(match[2])}</span></p>`;
      }
      return `<p class="script-stage">${escapeHtml(line)}</p>`;
    }).join("")
    : `<p class="script-stage">这段剧本还没有正文。</p>`;
}

function formatCreativeLore(text) {
  const blocks = String(text || "").split(/\n{2,}/).map((part) => part.trim()).filter(Boolean);
  return blocks.length
    ? blocks.map((block) => {
      const lines = block.split(/\n+/).map((line) => line.trim()).filter(Boolean);
      const head = lines[0] || "设定条目";
      const body = lines.slice(1).join("\n") || block;
      return `
        <section class="lore-entry">
          <b>${escapeHtml(head.replace(/^#+\s*/, ""))}</b>
          <p>${escapeHtml(body)}</p>
        </section>
      `;
    }).join("")
    : `<section class="lore-entry"><b>空白条目</b><p>这本设定册还没有正文。</p></section>`;
}

function formatCreativeContentByMode(text, mode) {
  if (mode === "poetry") {
    const lines = String(text || "").split(/\n+/).map((line) => line.trim()).filter(Boolean);
    return `<div class="poem-lines">${(lines.length ? lines : ["这首诗还没有正文。"]).map((line) => `<p>${escapeHtml(line)}</p>`).join("")}</div>`;
  }
  if (mode === "script") return formatCreativeScript(text);
  if (mode === "lore") return formatCreativeLore(text);
  return formatBookContent(text);
}

function renderCreativeBookReader(book, kindLabel, displayTitle, displayIntro, displayContent) {
  const mode = creativeReadingMode(book);
  const chunks = splitCreativeChunks(book, displayContent);
  const chunkNav = chunks.length > 1
    ? `<aside class="creative-chapter-rail">
        <span>片段</span>
        ${chunks.map((chunk) => `<a href="#creative-chunk-${escapeHtml(chunk.index)}">${escapeHtml(chunk.index)}</a>`).join("")}
      </aside>`
    : "";
  const contentHtml = chunks.map((chunk) => `
    <section class="creative-reader-chunk" id="creative-chunk-${escapeHtml(chunk.index)}">
      <header>
        <span>${escapeHtml(creativeModeLabel(mode))} ${escapeHtml(chunk.index)}</span>
        ${chunk.created ? `<small>${escapeHtml(chunk.created)}</small>` : ""}
      </header>
      <div class="reader-content creative-content ${escapeHtml(mode)}">${formatCreativeContentByMode(chunk.text, mode)}</div>
    </section>
  `).join("");
  return `
    <article class="reader-page subpage creative creative-reader ${escapeHtml(mode)}">
      <nav class="book-breadcrumb">
        <button type="button" data-book-close>书柜</button>
        <span>/</span>
        <button type="button" data-book-back>${escapeHtml(book.title || "未命名")}</button>
        <span>/ 阅读</span>
      </nav>
      <div class="reader-toolbar">
        <button type="button" data-book-back>返回简介</button>
        <span>${escapeHtml(kindLabel)} · ${escapeHtml(creativeModeLabel(mode))}</span>
        <button type="button" data-book-close>收回书柜</button>
      </div>
      <div class="reader-book-shell creative-shell ${escapeHtml(mode)}">
        <aside class="reader-cover creative-cover ${escapeHtml(mode)}">
          ${renderBookCoverInner(book, kindLabel, displayTitle, book.progress || "")}
        </aside>
        <section class="reader-paper creative-paper ${escapeHtml(mode)}">
          <header class="reader-page-head">
            <span>${escapeHtml(kindLabel)} · ${escapeHtml(book.point_of_view || "无固定叙事视角")}</span>
            <h2>${escapeHtml(displayTitle)}</h2>
            ${displayIntro ? `<p>${escapeHtml(displayIntro)}</p>` : ""}
          </header>
          <div class="creative-reader-body">
            ${chunkNav}
            <div class="creative-reader-main">${contentHtml}</div>
          </div>
          <footer class="reader-page-foot">
            <span>${escapeHtml(book.created || "书柜藏本")}</span>
            <span>${escapeHtml(book.tone || book.status || "")}</span>
          </footer>
        </section>
      </div>
    </article>
  `;
}

function proactiveSemanticLabel(kind) {
  const labels = {
    greeting: "问候",
    care: "关心",
    self_share: "自我分享",
    external_share: "外界分享",
    continuation: "续话",
    reminder: "提醒",
    observation: "观察",
    light_touch: "轻触碰",
    check_in: "确认一下",
  };
  return labels[kind] || kind || "";
}

function proactiveAnchorLabel(anchor) {
  const labels = {
    recent_context: "近期上下文",
    group_context: "群聊上下文",
    inner_life: "内在状态",
    current_activity: "当前活动",
    important_date: "重要日期",
    external_info: "外界信息",
    time_ritual: "时间仪式",
    environment: "环境",
    topic_hint: "话题线索",
    vague: "由头偏弱",
  };
  return labels[anchor] || anchor || "";
}

function proactivePercent100(value) {
  const num = Number(value || 0);
  if (!Number.isFinite(num) || num <= 0) return "";
  return `${Math.round(num)}%`;
}

function proactiveScore01(value) {
  const num = Number(value || 0);
  if (!Number.isFinite(num) || num <= 0) return "";
  return `${Math.round(num * 100)}%`;
}

function proactiveSemanticMeta(item) {
  const meta = [];
  if (item.semantic_kind) meta.push(`语义：${proactiveSemanticLabel(item.semantic_kind)}`);
  if (item.semantic_anchor_type) meta.push(`锚点：${proactiveAnchorLabel(item.semantic_anchor_type)}`);
  const semanticScore = proactivePercent100(item.semantic_score);
  if (semanticScore) meta.push(`贴合：${semanticScore}`);
  const pressure = proactivePercent100(item.semantic_pressure);
  if (pressure) meta.push(`压力：${pressure}`);
  const risk = proactivePercent100(item.semantic_risk);
  if (risk) meta.push(`风险：${risk}`);
  if (item.semantic_note) meta.push(`语义说明：${item.semantic_note}`);
  return meta;
}

function proactiveWindowPhaseLabel(phase) {
  const labels = {
    before: "还没到点",
    best: "最佳表达期",
    tail: "余温期",
    expired: "已过期",
    unknown: "未记录",
  };
  return labels[phase] || phase || "";
}

function proactiveWindowMeta(item) {
  const meta = [];
  if (item.window_phase) meta.push(`窗口：${proactiveWindowPhaseLabel(item.window_phase)}`);
  if (item.window_detail) meta.push(item.window_detail);
  const value = proactivePercent100(item.impulse_value);
  if (value) meta.push(`念头强度：${value}`);
  if (item.best_until_ts) meta.push(`最佳到：${item.best_until || "-"}`);
  if (item.expire_ts) meta.push(`过期：${item.expire || "-"}`);
  if (item.planned_impulse_id) meta.push(`impulse：${item.planned_impulse_id}`);
  return meta;
}

function proactiveReadinessMeta(item) {
  const readiness = item?.inner_readiness || {};
  const meta = [];
  const score = proactiveScore01(readiness.score);
  if (score) meta.push(`开口欲：${score}${readiness.label ? ` · ${readiness.label}` : ""}`);
  if (readiness.drive_label || readiness.drive_detail) {
    meta.push(`状态：${[readiness.drive_label, readiness.drive_detail].filter(Boolean).join(" · ")}`);
  }
  if (readiness.temperature_label || readiness.temperature_detail) {
    meta.push(`关系温度：${[readiness.temperature_label, readiness.temperature_detail].filter(Boolean).join(" · ")}`);
  }
  return meta;
}

function proactiveAfterglowHtml(source) {
  const afterglow = source?.afterglow || {};
  if (!afterglow.label && !afterglow.next_tendency) return "";
  const meta = [
    afterglow.time ? `记录：${afterglow.time}` : "",
    afterglow.status ? `结果：${afterglow.status}` : "",
    afterglow.semantic_kind ? `语义：${proactiveSemanticLabel(afterglow.semantic_kind)}` : "",
    afterglow.next_tendency ? `下次倾向：${afterglow.next_tendency}` : "",
  ].filter(Boolean);
  return `
    <div class="proactive-afterglow">
      <b>主动余韵</b>
      <span>${escapeHtml(afterglow.label || "有一条主动余韵")}</span>
      ${meta.length ? `<div class="proactive-meta">${meta.map((value) => `<span>${escapeHtml(value)}</span>`).join("")}</div>` : ""}
    </div>
  `;
}

function proactiveHesitationMeta(item) {
  const hesitation = item?.hesitation || {};
  const meta = [];
  if (hesitation.note) meta.push(`犹豫：${hesitation.note}`);
  if (hesitation.count) meta.push(`收住次数：${hesitation.count}`);
  if (hesitation.time) meta.push(`犹豫记录：${hesitation.time}`);
  if (hesitation.topic) meta.push(`原话题：${hesitation.topic}`);
  return meta;
}

function proactiveModelJudgeMeta(item) {
  const judge = item?.model_judge || {};
  const meta = [];
  if (judge.decision) meta.push(`模型判定：${judge.decision}${judge.score ? ` · ${judge.score}` : ""}`);
  if (judge.reason) meta.push(`判定原因：${judge.reason}`);
  if (judge.judged_ts) meta.push(`判定时间：${judge.judged || "-"}`);
  return meta;
}

function renderProactiveCandidates() {
  const data = state.overview?.proactive_candidates || {};
  const users = data.users || [];
  const selectedFilter = validProactiveCandidateFilter(data, state.proactiveCandidateFilter);
  state.proactiveCandidateFilter = selectedFilter;
  const allItems = data.items || [];
  const items = selectedFilter === "all"
    ? allItems
    : allItems.filter((item) => String(item.user_id || "") === selectedFilter);
  const counts = selectedFilter === "all" ? (data.counts || {}) : countProactiveCandidateItems(items, "status");
  const sourceCounts = selectedFilter === "all" ? (data.source_counts || {}) : countProactiveCandidateItems(items, "source");
  const total = selectedFilter === "all" ? (data.total || 0) : sumObjectValues(counts);
  const taskData = state.overview?.proactive_tasks || {};
  const runtime = taskData.runtime || {};
  $("#proactiveSummary").innerHTML = [
    proactiveSummaryCard("候选总数", total, `${items.length || 0} 条合并记录`),
    proactiveSummaryCard("未发送候选", selectedFilter === "all" ? (data.pending_total || 0) : sumObjectValues(counts) - Number(counts.sent || 0), "按每用户上限控制"),
    proactiveSummaryCard("已进入计划", counts.accepted || 0, "当前或历史接受候选"),
    proactiveSummaryCard("已发送", counts.sent || 0, "实际发出的主动"),
    proactiveSummaryCard("被拦截", counts.blocked || 0, "同类拦截已合并计数"),
    proactiveSummaryCard("执行审计", taskData.audit_total || 0, `${Object.keys(taskData.audit_status_counts || {}).length || 0} 类结果`),
    proactiveSummaryCard("循环状态", runtime.healthy ? "正常" : "待确认", runtime.last_tick_started_ts ? `最近 ${runtime.last_tick_started}` : "尚无心跳"),
  ].join("");
  $("#proactiveSourceChart").innerHTML = donutChart(sourceCounts, {
    emptyText: "暂无来源数据",
    labelFormatter: (label) => proactiveCandidateSourceLabel(label),
    maxSegments: 6,
    mergeBelowPercent: 0.035,
    otherLabel: "其他来源",
  });
  $("#proactiveStatusChart").innerHTML = donutChart(counts || {}, {
    emptyText: "暂无状态数据",
    labelFormatter: (label) => proactiveStatusLabel(label),
  });
  renderProactiveTasks();
  renderProactiveCandidateFilters(users, selectedFilter, data.total || allItems.length, total, items.length);
  if (!items.length) {
    $("#proactiveCandidateList").innerHTML = `<div class="empty small">暂无符合筛选的主动候选</div>`;
    return;
  }
  $("#proactiveCandidateList").innerHTML = items.map((item) => {
    const status = proactiveStatusLabel(item.status);
    const repeat = Number(item.repeat_count || 1);
    const userLabel = item.user_label || item.user_id || "-";
    const roleLabel = item.user_role_label || (item.user_role === "owner" ? "主人" : "朋友");
    const sourceLabel = item.source_label || proactiveCandidateSourceLabel(item.source);
    const semanticMeta = proactiveSemanticMeta(item);
    return `
      <section class="proactive-candidate ${escapeHtml(item.status || "unknown")}">
        <div class="proactive-candidate-head">
          <div>
            <b>${escapeHtml(item.topic || item.reason_label || item.reason || "未命名候选")}</b>
            <span>${escapeHtml(userLabel)} · ${escapeHtml(roleLabel)} · ${escapeHtml(sourceLabel)} · ${escapeHtml(item.reason_label || item.reason || "-")} · ${escapeHtml(item.action || "message")}</span>
          </div>
          <div class="toolbar">
            <span class="badge">${escapeHtml(repeat > 1 ? `${status} x${repeat}` : status)}</span>
            <button type="button" class="danger-outline" data-proactive-candidate-delete="${escapeHtml(item.id || "")}" data-proactive-user-id="${escapeHtml(item.user_id || "")}">删除</button>
            <button type="button" class="danger-outline" data-proactive-candidate-prune="${escapeHtml(item.user_id || "")}" data-proactive-prune-keep="160">删一点</button>
          </div>
        </div>
        <p>${escapeHtml(item.motive || "暂无动机记录")}</p>
        <div class="proactive-meta">
          ${semanticMeta.map((value) => `<span class="proactive-semantic-chip">${escapeHtml(value)}</span>`).join("")}
          <span>用户：${escapeHtml(userLabel)}</span>
          <span>ID：${escapeHtml(item.user_id || "-")}</span>
          <span>计划：${escapeHtml(item.scheduled || "-")}</span>
          <span>创建：${escapeHtml(item.created || "-")}</span>
          ${repeat > 1 ? `<span>最近：${escapeHtml(item.last_seen || "-")}</span>` : ""}
          <span>评分：${escapeHtml(item.score || 0)}</span>
          ${item.reason_detail ? `<span>为什么：${escapeHtml(item.reason_detail)}</span>` : ""}
          ${item.note ? `<span>${escapeHtml(item.note)}</span>` : ""}
        </div>
      </section>
    `;
  }).join("");
}

function renderProactiveTasks() {
  const root = $("#proactiveTaskList");
  if (!root) return;
  const data = state.overview?.proactive_tasks || {};
  const items = Array.isArray(data.items) ? data.items : [];
  const auditItems = Array.isArray(data.audit_items) ? data.audit_items : [];
  const runtimeHtml = proactiveRuntimeHtml(data.runtime || {});
  const priorityTasks = items.filter((item) => ["due", "overdue"].includes(item.status || ""));
  const compactTasks = [...priorityTasks, ...items.filter((item) => !priorityTasks.includes(item))].slice(0, 6);
  const hiddenTasks = items.filter((item) => !compactTasks.includes(item));
  const taskHtml = items.length ? `
      <div class="proactive-task-compact-note">
        <span>默认显示 ${escapeHtml(compactTasks.length)} / ${escapeHtml(items.length)} 条，优先展示到点和过期任务。</span>
      </div>
      ${compactTasks.map((item) => proactiveTaskMarkup(item)).join("")}
      ${hiddenTasks.length ? `
        <details class="proactive-task-more">
          <summary>展开其余 ${escapeHtml(hiddenTasks.length)} 条已登记任务</summary>
          <div class="proactive-task-more-list">
            ${hiddenTasks.map((item) => proactiveTaskMarkup(item)).join("")}
          </div>
        </details>
      ` : ""}
    ` : `
      <div class="proactive-task-empty">
        <b>当前没有已登记的主动任务</b>
        <span>如果 Bot 只是说“我等一下提醒你”，但这里没有记录，就说明没有真正进入插件主动调度。</span>
      </div>
    `;
  root.innerHTML = `
    ${runtimeHtml}
    ${proactiveUserStateHtml(Array.isArray(data.user_states) ? data.user_states : [])}
    <div class="proactive-task-section-title">已登记任务</div>
    ${taskHtml}
    <div class="proactive-task-section-title">最近执行审计</div>
    ${proactiveAuditHtml(auditItems)}
  `;
}

function proactiveTaskMarkup(item) {
  const status = proactiveTaskStatusLabel(item.status);
  const source = proactiveTaskSourceLabel(item.source, item.has_timer_event);
  const title = item.topic || item.reason_label || item.reason || "未命名主动任务";
  const semanticMeta = proactiveSemanticMeta(item);
  const windowMeta = proactiveWindowMeta(item);
  const readinessMeta = proactiveReadinessMeta(item);
  const hesitationMeta = proactiveHesitationMeta(item);
  const modelJudgeMeta = proactiveModelJudgeMeta(item);
  const meta = [
    `用户：${item.user_label || item.user_id || "-"}`,
    `来源：${source}`,
    `动作：${item.action || "message"}`,
    item.reason_label ? `原因：${item.reason_label}` : "",
    item.reason_detail ? `为什么：${item.reason_detail}` : "",
    `计划：${item.scheduled || "-"}`,
    item.last_skip_reason ? `最近${item.last_skip_prefix || "跳过"}：${item.last_skip_reason}` : "",
    item.last_skip_ts ? `记录：${item.last_skip || "-"}` : "",
    item.created_ts ? `登记：${item.created || "-"}` : "",
    item.job_id ? `官方任务：${item.job_id}` : "",
    item.replaced_job_id ? `替换旧任务：${item.replaced_job_id}` : "",
    item.cancelled_job_id ? `取消任务：${item.cancelled_job_id}` : "",
    item.timer_status ? `预约状态：${item.timer_status}` : "",
    item.timer_error ? `错误：${item.timer_error}` : "",
    item.raw_time ? `原始时间：${item.raw_time}` : "",
    item.trigger_message_id ? `触发消息：${item.trigger_message_id}` : "",
    item.planned_candidate_id ? `candidate：${item.planned_candidate_id}` : "",
    item.silence_until_due ? "到点前静默" : "",
  ].filter(Boolean);
  return `
    <section class="proactive-task ${escapeHtml(item.status || "scheduled")}">
      <div class="proactive-task-head">
        <div>
          <b>${escapeHtml(title)}</b>
          <span>${escapeHtml(item.user_label || item.user_id || "-")} · ${escapeHtml(item.user_role_label || "-")} · ${escapeHtml(source)}</span>
        </div>
        <span class="badge">${escapeHtml(status)}</span>
      </div>
      <p>${escapeHtml(item.motive || "暂无登记动机")}</p>
      <div class="proactive-meta">
        ${semanticMeta.map((value) => `<span class="proactive-semantic-chip">${escapeHtml(value)}</span>`).join("")}
        ${windowMeta.map((value) => `<span class="proactive-window-chip">${escapeHtml(value)}</span>`).join("")}
        ${readinessMeta.map((value) => `<span>${escapeHtml(value)}</span>`).join("")}
        ${hesitationMeta.map((value) => `<span>${escapeHtml(value)}</span>`).join("")}
        ${modelJudgeMeta.map((value) => `<span>${escapeHtml(value)}</span>`).join("")}
        ${meta.map((value) => `<span>${escapeHtml(value)}</span>`).join("")}
      </div>
      ${proactiveAfterglowHtml(item)}
    </section>
  `;
}

function proactiveUserStateHtml(items) {
  if (!items.length) return "";
  return `
    <div class="proactive-user-state-grid">
      ${items.map((item) => {
        const quota = `${item.sent_today ?? 0}/${item.effective_daily_limit ?? "-"}`;
        const status = item.proactive_sending ? "发送中" : item.next_proactive_ts ? "已排程" : "未排程";
        const readinessMeta = proactiveReadinessMeta(item);
        const hesitationMeta = proactiveHesitationMeta(item);
        const meta = [
          `今日：${quota}`,
          item.next_proactive_ts ? `下次：${item.next_proactive}` : "下次：-",
          item.last_sent_ts ? `上次主动：${item.last_sent}` : "",
          item.last_skip_reason ? `最近${item.last_skip_prefix || "跳过"}：${item.last_skip_reason}` : "",
          ...readinessMeta,
          ...hesitationMeta,
        ].filter(Boolean);
        return `
          <section class="proactive-user-state ${item.proactive_sending ? "running" : ""}">
            <div>
              <b>${escapeHtml(item.user_label || item.user_id || "-")}</b>
              <span>${escapeHtml(item.user_role_label || "-")} · ${escapeHtml(status)}</span>
            </div>
            <div class="proactive-meta">${meta.map((value) => `<span>${escapeHtml(value)}</span>`).join("")}</div>
            ${proactiveAfterglowHtml(item)}
          </section>
        `;
      }).join("")}
    </div>
  `;
}

function proactiveRuntimeHtml(runtime) {
  const healthy = Boolean(runtime.healthy);
  const age = Number(runtime.tick_age_seconds || -1);
  const ageText = age >= 0 ? `${Math.round(age)} 秒前` : "尚无记录";
  const items = [
    `最近开始：${runtime.last_tick_started || "-"}`,
    `最近结束：${runtime.last_tick_finished || "-"}`,
    `心跳年龄：${ageText}`,
    `检查间隔：${runtime.expected_interval_seconds || "-"} 秒`,
    runtime.last_tick_error ? `最近异常：${runtime.last_tick_error}` : "",
  ].filter(Boolean);
  return `
    <section class="proactive-runtime ${healthy ? "ok" : "warn"}">
      <div class="proactive-task-head">
        <div>
          <b>主动循环心跳</b>
          <span>${healthy ? "循环最近有运行记录" : "没有近期心跳或等待下一轮记录"}</span>
        </div>
        <span class="badge">${healthy ? "正常" : "待确认"}</span>
      </div>
      <div class="proactive-meta">${items.map((value) => `<span>${escapeHtml(value)}</span>`).join("")}</div>
    </section>
  `;
}

function proactiveAuditHtml(items) {
  const visibleItems = (Array.isArray(items) ? items : []).filter((item) => item?.status !== "obsolete");
  if (!visibleItems.length) {
    return `<div class="proactive-task-empty"><b>暂无执行审计</b><span>主动真正进入发送链路后，会在这里留下开始、成功、失败、延后或取消记录。</span></div>`;
  }
  return visibleItems.slice(0, 30).map((item) => {
    const status = proactiveAuditStatusLabel(item.status);
    const title = item.topic || item.reason_label || item.reason || item.note || "主动执行记录";
    const sourceLabel = item.source_label || proactiveCandidateSourceLabel(item.source);
    const semanticMeta = proactiveSemanticMeta(item);
    const meta = [
      `用户：${item.user_label || item.user_id || "-"}`,
      `来源：${sourceLabel}`,
      `动作：${item.action || "message"}`,
      item.reason_label ? `原因：${item.reason_label}` : (item.reason ? `原因：${item.reason}` : ""),
      item.reason_detail ? `为什么：${item.reason_detail}` : "",
      item.note ? `结果：${item.note}` : "",
      item.original_text_preview ? `原候选：${item.original_text_preview}` : "",
      item.final_text_preview ? `最终发送：${item.final_text_preview}` : "",
      item.text_preview && !item.final_text_preview ? `消息：${item.text_preview}` : "",
      item.has_image ? "包含图片" : "",
      item.extra_count ? `组件 ${item.extra_count}` : "",
      Number(item.duplicate_count || 0) > 1 ? `重复 ${item.duplicate_count} 次` : "",
      item.scheduled_ts ? `计划：${item.scheduled || "-"}` : "",
      item.created_ts ? `开始：${item.created || "-"}` : "",
      item.updated_ts ? `更新：${item.updated || "-"}` : "",
    ].filter(Boolean);
    return `
      <section class="proactive-task audit ${escapeHtml(item.status || "unknown")}">
        <div class="proactive-task-head">
          <div>
            <b>${escapeHtml(title)}</b>
            <span>${escapeHtml(item.user_label || item.user_id || "-")} · ${escapeHtml(item.user_role_label || "-")} · ${escapeHtml(sourceLabel)}</span>
          </div>
          <span class="badge">${escapeHtml(status)}</span>
        </div>
        <p>${escapeHtml(item.motive || item.note || "暂无动机记录")}</p>
        <div class="proactive-meta">
          ${semanticMeta.map((value) => `<span class="proactive-semantic-chip">${escapeHtml(value)}</span>`).join("")}
          ${meta.map((value) => `<span>${escapeHtml(value)}</span>`).join("")}
        </div>
      </section>
    `;
  }).join("");
}

function proactiveTaskStatusLabel(status) {
  return {
    scheduled: "已登记",
    due: "已到点",
    overdue: "超时未发",
    handed_off: "已交官方",
    failed: "执行失败",
    cancelled: "已取消",
    cancel_failed: "取消失败",
    cancel_skipped: "无可取消",
  }[status] || status || "未知";
}

function proactiveAuditStatusLabel(status) {
  return {
    running: "执行中",
    deferred: "已延后",
    cancelled: "已取消",
    dropped: "已放弃",
    failed: "失败",
    sent: "已发送",
    obsolete: "旧记录",
  }[status] || status || "未知";
}

function proactiveTaskSourceLabel(source, hasTimerEvent = false) {
  if (source === "timer" || hasTimerEvent) return "官方定时计划";
  if (source === "candidate") return "主动候选";
  if (source === "followup" || source === "pending_followup") return "补一句";
  if (source === "state") return "身体小需求";
  if (source === "daily_greeting") return "日常招呼";
  if (source === "external") return "外部主动能力";
  return source || "插件主动";
}

function proactiveCandidateSourceLabel(source) {
  return {
    random: "轻微想念",
    daily_greeting: "日常招呼",
    pending_followup: "补一句",
    state: "身体小需求",
    event: "生活事件",
    story: "日常剧情",
    habit: "习惯关心",
    bilibili: "B站分享",
    bookshelf_reading: "私密阅读",
    creative_writing: "创作灵感",
    group_share: "群聊见闻",
    web_exploration: "主动搜索",
    news: "新闻阅读",
    candidate: "主动候选",
    followup: "补一句",
    external: "外部主动能力",
    timer: "官方定时计划",
    proactive: "插件主动",
    unknown: "未记录来源",
  }[source] || source || "插件主动";
}

function validProactiveCandidateFilter(data, value) {
  const userIds = new Set((data.users || []).map((item) => String(item.user_id || "")));
  const normalized = String(value || "all");
  return normalized === "all" || userIds.has(normalized) ? normalized : "all";
}

function countProactiveCandidateItems(items, key) {
  return (items || []).reduce((acc, item) => {
    const name = String(item?.[key] || "unknown");
    acc[name] = (acc[name] || 0) + Number(item?.repeat_count || 1);
    return acc;
  }, {});
}

function sumObjectValues(data) {
  return Object.values(data || {}).reduce((sum, value) => sum + Number(value || 0), 0);
}

function renderProactiveCandidateFilters(users, selected, allTotal, selectedTotal, visibleCount) {
  const root = $("#proactiveCandidateFilters");
  if (!root) return;
  const userItems = Array.isArray(users) ? users : [];
  const selectedUser = userItems.find((user) => String(user.user_id || "") === selected);
  const title = selected === "all"
    ? "全部私聊用户"
    : (selectedUser?.label || selected || "未知用户");
  const note = selected === "all"
    ? `${formatNumber(userItems.length)} 个用户 · ${formatNumber(visibleCount || 0)} 条记录`
    : `${selectedUser?.role_label || "用户"} · ${formatNumber(visibleCount || 0)} 条记录`;
  const statusSummary = selectedUser?.counts
    ? [
        `计划 ${formatNumber(selectedUser.counts.accepted || 0)}`,
        `发送 ${formatNumber(selectedUser.counts.sent || 0)}`,
        `拦截 ${formatNumber(selectedUser.counts.blocked || 0)}`,
      ].join(" · ")
    : `${formatNumber(selectedTotal || 0)} 次触发`;
  const allButton = proactiveCandidateFilterButton({
    value: "all",
    label: "全部用户",
    roleLabel: "汇总视图",
    total: allTotal || 0,
    selected: selected === "all",
  });
  const userButtons = userItems.map((user) => proactiveCandidateFilterButton({
    value: user.user_id || "",
    label: user.label || user.user_id || "未知用户",
    roleLabel: user.role_label || "-",
    total: user.total || 0,
    selected: selected === String(user.user_id || ""),
    counts: user.counts || {},
  })).join("");
  root.innerHTML = `
    <div class="proactive-filter-summary">
      <span>当前视图</span>
      <b>${escapeHtml(title)}</b>
      <small>${escapeHtml(note)}</small>
      <em>${escapeHtml(statusSummary)}</em>
    </div>
    <div class="proactive-filter-list" role="list" aria-label="候选记录用户视图">
      ${allButton}
      ${userButtons || `<span class="empty small">暂无用户候选</span>`}
    </div>
  `;
  root.querySelectorAll("[data-proactive-candidate-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      state.proactiveCandidateFilter = button.dataset.proactiveCandidateFilter || "all";
      renderProactiveCandidates();
    });
  });
}

function proactiveCandidateFilterButton({ value, label, roleLabel, total, selected, counts = {} }) {
  const detail = [
    counts.sent ? `发 ${formatNumber(counts.sent)}` : "",
    counts.accepted ? `计 ${formatNumber(counts.accepted)}` : "",
    counts.blocked ? `拦 ${formatNumber(counts.blocked)}` : "",
  ].filter(Boolean).join(" · ");
  return `
    <button type="button" class="proactive-filter-user ${selected ? "is-active" : ""}" data-proactive-candidate-filter="${escapeHtml(value)}">
      <span>
        <b>${escapeHtml(label)}</b>
        <small>${escapeHtml(roleLabel || "-")}${detail ? ` · ${escapeHtml(detail)}` : ""}</small>
      </span>
      <em>${escapeHtml(formatNumber(total || 0))}</em>
    </button>
  `;
}

function proactiveSummaryCard(label, value, note) {
  return `
    <section class="proactive-summary-card">
      <span>${escapeHtml(label)}</span>
      <b>${escapeHtml(value)}</b>
      <small>${escapeHtml(note)}</small>
    </section>
  `;
}

function proactiveStatusLabel(status) {
  return {
    accepted: "已计划",
    sent: "已发送",
    blocked: "已拦截",
    deferred: "已延后",
    dropped: "已放弃",
    cancelled: "已取消",
    failed: "失败",
    running: "执行中",
    scheduled: "已登记",
    due: "已到点",
    overdue: "超时未发",
    obsolete: "旧记录",
    expired: "已过期",
  }[status] || status || "未知";
}

function proactiveActionLabel(action) {
  return {
    message: "文字消息",
    photo_text: "拍照/生图",
    poke: "戳一戳",
    voice: "语音",
    screen_peek: "识屏",
    quote: "引用回复",
  }[action] || action || "文字消息";
}

function renderCreativeProjectCard(item) {
  const current = Number(item.current_chars || 0);
  const target = Number(item.target_chars || 0);
  const pct = target > 0 ? Math.min(100, Math.round((current / target) * 100)) : 0;
  const statusText = {
    drafting: "慢慢写着",
    finished: "已写完",
    paused: "暂时搁置",
  }[item.status] || item.status || "未知";
  const milestones = Array.isArray(item.milestones) && item.milestones.length
    ? item.milestones.map((name) => `<span>${escapeHtml(creativeMilestoneLabel(name))}</span>`).join("")
    : `<span class="muted">还没提起过</span>`;
  return `
    <article class="creative-card">
      <div class="creative-card-head">
        <div>
          <h3>${escapeHtml(item.title || "未定标题")}</h3>
          <p>${escapeHtml(item.premise || "还没整理出一句设定。")}</p>
        </div>
        <span class="badge">${escapeHtml(statusText)}</span>
      </div>
      <div class="creative-progress">
        <div class="meter"><i style="width:${pct}%"></i></div>
        <b>${escapeHtml(current)} / ${escapeHtml(target || "-")} 字</b>
      </div>
      <div class="creative-meta">
        <span>类型：${escapeHtml(item.work_type || "短篇小说")}</span>
        <span>气质：${escapeHtml(item.tone || "-")}</span>
        <span>视角：${escapeHtml(item.point_of_view || "第三人称有限视角")}</span>
        <span>片段：${escapeHtml(item.chunk_count || 0)}</span>
        <span>创建：${escapeHtml(item.created_at || "-")}</span>
        <span>上次推进：${escapeHtml(item.last_advanced || "-")}</span>
        <span>下次推进：${escapeHtml(item.next_advance || "-")}</span>
      </div>
      ${item.source ? `<p class="creative-source">灵感来源：${escapeHtml(item.source)}</p>` : ""}
      ${item.latest_snippet ? `<blockquote>${escapeHtml(item.latest_snippet)}</blockquote>` : ""}
      <div class="creative-milestones">
        <b>已自然提起节点</b>
        <div>${milestones}</div>
      </div>
    </article>
  `;
}

function creativeMilestoneLabel(name) {
  return {
    opening: "开头",
    midpoint: "过半",
    finished: "完稿",
    impression_question: "询问观感",
  }[name] || name || "节点";
}

function renderDailyTimeline() {
  const timeline = state.overview?.daily_timeline || {};
  const segments = timeline.segments || [];
  if (!segments.length) {
    $("#dailyTimeline").innerHTML = `<div class="empty small">暂无细化时间段</div>`;
    return;
  }
  $("#dailyTimeline").innerHTML = segments.map((segment) => {
    const vars = (segment.state_variables || []).slice(0, 4);
    const events = (segment.today_events || []).slice(0, 3);
    const presence = segment.presence_status || {};
    const statusText = detailSegmentStatusLabel(segment);
    const presenceText = presenceLabel(presence);
    const metaText = [statusText, presenceText].filter(Boolean).join(" · ");
    const errorText = String(segment.error || "").trim();
    return `
      <section class="timeline-item">
        <div class="timeline-time">${escapeHtml(segment.window || segment.key)}</div>
        <div class="timeline-body">
          <div class="timeline-head">
            <b>${escapeHtml(segment.summary || "这一段还没有摘要")}</b>
            <span>${escapeHtml(metaText)}</span>
          </div>
          ${errorText ? `<p class="timeline-error">失败原因：${escapeHtml(errorText)}${segment.retry_after ? `，${escapeHtml(segment.retry_after)} 后重试` : ""}</p>` : ""}
          <div class="state-pills">
            ${vars.length ? vars.map((item) => `
              <span title="${escapeHtml(item.note || "")}">
                <b>${escapeHtml(item.name || "-")}</b>${escapeHtml(item.value || "-")}
              </span>
            `).join("") : `<span>暂无状态变量</span>`}
          </div>
          <ul>
            ${events.length ? events.map((item) => `<li>${escapeHtml(item.window ? `${item.window} · ${item.text}` : item.text)}</li>`).join("") : `<li>暂无细化事件</li>`}
          </ul>
        </div>
      </section>
    `;
  }).join("");
}

function detailSegmentStatusLabel(segment) {
  const raw = segment && typeof segment === "object" ? segment : {};
  const value = String(raw.status || "").trim();
  const startedAt = String(raw.started_at || "").trim();
  const label = {
    done: "已细化",
    generating: "生成中",
    failed: "生成失败",
  }[value] || (value ? value : "未生成");
  if (value === "generating" && startedAt) return `${label}（${startedAt} 开始）`;
  return label;
}

function renderInteractionImpact() {
  const timeline = state.overview?.daily_timeline || {};
  const segmentUpdates = [];
  (timeline.segments || []).forEach((segment) => {
    (segment.interaction_updates || []).forEach((item) => {
      segmentUpdates.push({ ...item, window: segment.window });
    });
  });
  const adjustments = (timeline.adjustments || []).map((item) => ({
    at: item.date || "",
    source: item.source,
    reaction: item.reaction || item.note,
    state_updates: item.state_updates || [],
    window: "全局影响",
  }));
  const items = [...segmentUpdates, ...adjustments].filter((item) => item.reaction || item.state_updates?.length);
  if (!items.length) {
    $("#interactionImpact").innerHTML = `<div class="empty small">暂无用户介入影响</div>`;
    return;
  }
  $("#interactionImpact").innerHTML = items.slice(-12).reverse().map((item) => `
    <section class="impact-item">
      <div>
        <b>${escapeHtml(item.source || "用户影响")}</b>
        <span>${escapeHtml([item.window, item.at].filter(Boolean).join(" · "))}</span>
      </div>
      <p>${escapeHtml(item.reaction || "")}</p>
      <div class="state-pills">
        ${(item.state_updates || []).map((update) => `<span>${escapeHtml(update)}</span>`).join("")}
      </div>
    </section>
  `).join("");
}

function presenceLabel(presence) {
  const mode = String(presence?.mode || "unchanged");
  const text = presence?.custom_text || presence?.wording || "";
  if (mode === "custom" && text) return `自定义状态：${text}`;
  if (mode === "sleep") return "状态：休息中";
  if (mode === "busy") return "状态：忙碌";
  if (mode === "online") return "状态：在线";
  return "状态：不变";
}

function renderMemoryComposition() {
  const data = {
    "原始记忆": state.users.reduce((sum, user) => sum + Number(user.memory_items || 0), 0),
    "私聊片段": state.users.reduce((sum, user) => sum + Number(user.dialogue_episode_count || 0), 0),
    "未完话头": state.users.reduce((sum, user) => sum + Number(user.open_loop_count || 0), 0),
    "群聊片段": state.groups.reduce((sum, group) => sum + Number(group.episode_count || 0), 0),
    "群聊话题": state.groups.reduce((sum, group) => sum + Number(group.topic_count || 0), 0),
  };
  $("#memoryComposition").innerHTML = donutChart(data);
}

function renderSlangCloud() {
  const counts = new Map();
  state.groups.forEach((group) => {
    (group.slang_terms || []).forEach((item, index) => {
      const term = slangTermText(item);
      if (!term) return;
      counts.set(term, (counts.get(term) || 0) + Math.max(1, 16 - index));
    });
  });
  const entries = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 36);
  if (!entries.length) {
    $("#slangCloud").innerHTML = `<div class="empty small">暂无群聊黑话</div>`;
    return;
  }
  const max = Math.max(1, ...entries.map(([, count]) => count));
  $("#slangCloud").innerHTML = entries.map(([term, count]) => {
    const size = 12 + Math.round((count / max) * 16);
    return `<span style="font-size:${size}px">${escapeHtml(term)}</span>`;
  }).join("");
}

function slangTermText(item) {
  if (typeof item === "string") return item;
  if (!item || typeof item !== "object") return "";
  return String(
    item.term
      || item.word
      || item.text
      || item.name
      || item.key
      || item.phrase
      || item.slang
      || ""
  ).trim();
}

function formatSlangTerms(items) {
  const terms = (Array.isArray(items) ? items : [])
    .map(slangTermText)
    .filter(Boolean);
  return terms.length ? terms.join("、") : "暂无";
}

function renderConfig() {
  const overview = state.overview || {};
  const group = overview.group || {};
  $("#groupAccessMode").value = group.access_mode || "whitelist";
  $("#groupWhitelist").value = (group.whitelist || []).join("\n");
  $("#groupBlacklist").value = (group.blacklist || []).join("\n");
  renderStorageConfig();
  renderAppearanceSettings();
  renderAccessManager(group);
  renderFeatureSwitches();
  renderConfigBackups();
  renderConfigImportChecks();
  renderConfigMigrationPreview();
}

function renderStorageConfig(options = {}) {
  const overview = state.overview || {};
  const settings = overview.settings || {};
  const plugin = overview.plugin || {};
  const preserveDraft = Boolean(options.preserveDraft);
  const backendSelect = $("#storageBackendSelect");
  const sqliteWrap = $("#storageSqlitePathWrap");
  const sqliteInput = $("#storageSqlitePathInput");
  const summary = $("#storageConfigSummary");
  const selectedBackend = preserveDraft ? (backendSelect?.value || "") : "";
  const backend = String(selectedBackend || settings.storage_backend || plugin.storage_backend || "json").trim().toLowerCase() || "json";
  const sqlitePath = preserveDraft && sqliteInput
    ? String(sqliteInput.value || "").trim()
    : String(settings.storage_sqlite_path || plugin.storage_sqlite_path || "").trim();
  if (backendSelect) backendSelect.value = backend;
  if (sqliteInput) sqliteInput.value = sqlitePath;
  if (sqliteWrap) sqliteWrap.hidden = backend !== "sqlite";
  if (summary) {
    summary.innerHTML = `
      <span>当前后端：<b>${escapeHtml(backend)}</b></span>
      <span>当前路径：<b>${escapeHtml(plugin.storage_sqlite_path || sqlitePath || "companions.json / 默认 companions.db")}</b></span>
      <span>当前数据文件：<b>${escapeHtml(plugin.data_file || "-")}</b></span>
    `;
  }
}

function renderPrivateStrategyOverview(selector, info) {
  const total = Number(info.target_count ?? info.user_count ?? info.private_user_count ?? 0);
  const enabled = Number(info.enabled_count ?? info.enabled_user_count ?? total);
  const adminIds = Array.isArray(info.admin_ids) ? info.admin_ids : [];
  const targetIds = Array.isArray(info.target_user_ids) ? info.target_user_ids : [];
  const manageIds = [...new Set([...adminIds, ...targetIds])];
  const manageText = manageIds.length ? manageIds.join("、") : "未配置";
  const rows = [
    ["对象", total ? `${enabled}/${total} 启用` : `${enabled || 0} 个启用`],
    ["主动上限", `每日 ${Number(info.max_daily_messages || 0)} 条`],
    ["触达条件", `空闲 ${Number(info.idle_minutes || 0)} 分钟后，最小间隔 ${Number(info.min_interval_minutes || 0)} 分钟`],
    ["管理权限", manageText],
    ["管理命令", toBool(info.require_opt_in ?? info.require_confirm ?? info.require_private_confirm) ? "仅私聊" : "私聊+群聊"],
  ];
  $(selector).innerHTML = compactOverviewList(rows, { columns: 1 });
}

function renderGroupStrategyOverview(selector, group) {
  const mode = String(group.access_mode || "whitelist");
  const whitelistCount = Array.isArray(group.whitelist) ? group.whitelist.length : 0;
  const blacklistCount = Array.isArray(group.blacklist) ? group.blacklist.length : 0;
  const accessText = mode === "blacklist" ? `黑名单（${blacklistCount} 个群）` : `白名单（${whitelistCount} 个群）`;
  const total = Number(group.group_count || 0);
  const enabled = Number(group.enabled_group_count || 0);
  const rows = [
    ["群聊观察", statusText(group.enabled)],
    ["目标群", total ? `${enabled}/${total} 启用` : `${enabled || 0} 个启用`],
    ["访问范围", accessText],
    ["群主动插话", statusText(group.interjection_enabled)],
    ["复读处理", statusText(group.repeat_follow_enabled)],
  ];
  $(selector).innerHTML = compactOverviewList(rows, { columns: 1 });
}

function renderLongTermStrategyOverview(selector, { creative = {}, bili = {}, qzone = {}, privateReading = {} } = {}) {
  const cards = [
    {
      title: "B 站",
      tone: bili.enabled ? "ok" : "off",
      meta: [statusText(bili.enabled), bili.boredom_watch_enabled ? "无聊刷视频开启" : "无聊刷视频关闭"],
      text: bili.latest_video?.title || "暂无最新视频",
    },
    {
      title: "QQ 空间",
      tone: qzone.enabled && qzone.available ? (qzone.last_status && qzone.last_status.startsWith("paused:") ? "warn" : "ok") : qzone.enabled ? "warn" : "off",
      meta: [qzone.enabled ? (qzone.available ? "可用" : "待服务") : "关闭", qzone.life_publish_enabled ? "生活说说开启" : "生活说说关闭", qzone.comment_inbox_enabled ? "评论收件箱开启" : ""].filter(Boolean),
      text: qzone.last_status && qzone.last_status.startsWith("paused:") ? `自动说说已暂停：${qzone.auth_failure_reason || qzone.last_status}` : (qzone.last_text || "暂无最近说说"),
    },
    {
      title: "私下创作",
      tone: creative.enabled ? "ok" : "off",
      meta: [statusText(creative.enabled), `项目 ${Number(creative.active_projects || 0)} 个进行中 / ${Number(creative.project_count || 0)} 个总计`],
      text: creative.latest_title || "暂无最新创作",
    },
  ];
  if (privateReading.available) {
    cards.push({
      title: "夹层阅读",
      tone: privateReading.enabled ? "ok" : "off",
      meta: [
        privateReading.enabled ? "可用" : "关闭",
        privateReading.boredom_read_enabled ? "私下阅读开启" : "私下阅读关闭",
        privateReading.ask_recommendation_enabled ? "征求推荐开启" : "征求推荐关闭",
      ],
      text: privateReading.last_album?.title || "暂无最近阅读",
    });
  }
  $(selector).innerHTML = `<div class="longterm-overview-grid">${cards.map(longTermOverviewCard).join("")}</div>`;
}

function compactOverviewList(rows, { columns = 1 } = {}) {
  const className = columns > 1 ? "compact-overview-list two" : "compact-overview-list";
  return `
    <div class="${className}">
      ${rows.map(([label, value]) => `
        <div class="compact-overview-row">
          <span>${escapeHtml(label)}</span>
          ${overviewValueHtml(value)}
        </div>
      `).join("")}
    </div>
  `;
}

function overviewValueHtml(value) {
  const text = String(value ?? "").trim() || "未设置";
  if (["开启", "可用", "需要"].includes(text)) {
    return `<b class="overview-pill ok">${escapeHtml(text)}</b>`;
  }
  if (["关闭", "不需要", "待服务", "未设置"].includes(text)) {
    return `<b class="overview-pill ${text === "待服务" ? "warn" : "off"}">${escapeHtml(text)}</b>`;
  }
  return `<b>${escapeHtml(text)}</b>`;
}

function longTermOverviewCard(item) {
  const meta = Array.isArray(item.meta) ? item.meta.filter(Boolean) : [];
  return `
    <article class="longterm-overview-card ${escapeHtml(item.tone || "info")}">
      <header>
        <b>${escapeHtml(item.title || "长线主动")}</b>
        ${meta[0] ? overviewValueHtml(meta[0]) : ""}
      </header>
      <p>${escapeHtml(item.text || "暂无记录")}</p>
      ${meta.length > 1 ? `<div>${meta.slice(1).map((value) => `<span>${escapeHtml(value)}</span>`).join("")}</div>` : ""}
    </article>
  `;
}

function statusText(value) {
  return toBool(value) ? "开启" : "关闭";
}

function renderModuleSettings() {
  const settings = state.overview?.settings || {};
  const formValues = { ...settings, ...(state.featureDraft || {}) };
  renderModuleWorkbench(settings);
  renderModuleSummary(settings);
  renderCurrentPersonaStatus(settings);
  const newsRaw = $("#newsSourcesRaw");
  if (newsRaw) newsRaw.value = displaySettingValue("news_sources", settings.news_sources);
  fillForm("#roleplayProfileForm", formValues);
  fillForm("#privateAliasForm", formValues);
  fillForm("#quickModuleForm", formValues);
  fillForm("#environmentModuleForm", formValues);
  fillForm("#privateModuleForm", formValues);
  fillForm("#groupModuleForm", formValues);
  fillForm("#worldbookModuleForm", formValues);
  fillForm("#memoryModuleForm", formValues);
  fillForm("#longTermModuleForm", formValues);
  setPrivateReadingConfigVisible(isPrivateReadingAvailable());
  const targetBox = document.querySelector('#quickModuleForm [name="target_user_ids"]');
  if (targetBox) targetBox.value = Array.isArray(settings.target_user_ids) ? settings.target_user_ids.join("\n") : "";
  document.querySelectorAll(".module-form").forEach((form) => markModuleFormClean(form));
  updateMessageDebounceConfigVisibility();
  updateSegmentedConfigVisibility($("#privateModuleForm"));
  renderSegmentedPreview();
  renderNewsSourceManager();
  renderExternalAbilities();
  renderPresetCards();
}

function renderModuleWorkbench(settings) {
  const box = $("#moduleWorkbench");
  if (!box) return;
  const overview = state.overview || {};
  const features = overview.features || {};
  const group = overview.group || {};
  const worldbook = overview.worldbook || {};
  const external = overview.external_abilities || {};
  const knowledge = overview.knowledge || {};
  const creative = overview.creative || {};
  const bookshelf = overview.bookshelf || {};
  const qzone = overview.qzone || {};
  const privateReading = overview.private_reading || {};
  const intensity = overview.proactive_intensity || {};
  const intensityEnabled = Boolean(intensity.enabled);
  const intensityLabel = intensityEnabled ? (intensity.label || intensity.preset || "预设开启") : "手动参数";
  const proactiveMaxDaily = intensityEnabled ? (intensity.effective?.max_daily_messages ?? settings.max_daily_messages) : settings.max_daily_messages;
  const proactiveIdle = intensityEnabled ? (intensity.effective?.idle_minutes ?? settings.idle_minutes) : settings.idle_minutes;
  const proactiveMinInterval = intensityEnabled ? (intensity.effective?.min_interval_minutes ?? settings.min_interval_minutes) : settings.min_interval_minutes;
  const moduleCards = [
    {
      title: "主动触达",
      kicker: "主动节奏",
      status: Number(proactiveMaxDaily || 0) > 0 ? "运行中" : "已收起",
      tone: intensityEnabled ? "warn" : (Number(proactiveMaxDaily || 0) > 0 ? "ok" : "off"),
      body: `私聊 ${proactiveMaxDaily ?? 0} 条/天，空闲 ${proactiveIdle ?? 0} 分钟后进入候选；群聊唤醒和插话按群聊开关参与。`,
      meta: [
        `强度：${intensityLabel}`,
        `最小间隔 ${proactiveMinInterval ?? 0} 分钟`,
        toBool(settings.enable_unanswered_screen_peek_followup) ? "未回应后可轻窥屏" : "未回应不窥屏",
      ],
      actions: [
        ["proactive", "看主动候选"],
        ["private", "管理私聊对象"],
      ],
    },
    {
      title: "群聊观察",
      kicker: "群内理解",
      status: features.enable_group_companion ? "观察中" : "未启用",
      tone: features.enable_group_companion ? "ok" : "off",
      body: `${group.enabled_group_count || 0}/${group.group_count || 0} 个群启用，关系、黑话和片段会作为群聊背景管理。`,
      meta: [
        toBool(settings.enable_group_high_intensity_mode) ? "高强度收口开启" : "高强度收口关闭",
        toBool(settings.enable_forward_message_adaptation) ? "合并消息阅读开启" : "合并消息阅读关闭",
      ],
      actions: [
        ["group", "看群聊"],
        ["memory", "看黑话/片段"],
      ],
    },
    {
      title: "关系网",
      kicker: "身份与称呼",
      status: settings.enable_worldbook_member_recognition ? "启用" : "关闭",
      tone: settings.enable_worldbook_member_recognition ? "ok" : "off",
      body: `${worldbook.enabled_member_count || 0}/${worldbook.member_count || 0} 个节点启用，用来稳定识别昵称、群名片和关系备注。`,
      meta: [
        toBool(settings.worldbook_self_registration) ? "允许群聊自登记" : "自登记关闭",
        `自登记拒绝词 ${worldbook.self_registration_block_word_count || 0} 条`,
        toBool(settings.enable_atrelay_tools) ? "跨群转述工具开启" : "跨群转述关闭",
      ],
      actions: [
        ["worldbook", "管理关系"],
        ["group", "查看群成员"],
      ],
    },
    {
      title: "记忆与能力",
      kicker: "长期沉淀",
      status: settings.enable_skill_growth_simulation ? "成长中" : "记录中",
      tone: settings.enable_skill_growth_simulation ? "ok" : "cost",
      body: `长期画像每 ${settings.memory_refresh_interval_minutes ?? 0} 分钟整理，片段阈值 ${settings.episode_memory_refresh_messages ?? 0} 条消息。`,
      meta: [
        toBool(settings.enable_skill_growth_schedule_influence) ? "技能影响日程" : "技能不影响日程",
        knowledge.selected_count ? `知识库 ${knowledge.selected_count} 项` : "未选知识库",
      ],
      actions: [
        ["memory", "整理记忆"],
        ["tokens", "看成本"],
      ],
    },
    {
      title: "图片与语音",
      kicker: "媒体能力",
      status: toBool(settings.enable_tts_enhancement) || toBool(settings.enable_private_image_enhancement) ? "可用" : "低干预",
      tone: toBool(settings.enable_tts_enhancement) || toBool(settings.enable_private_image_enhancement) ? "ok" : "off",
      body: "图片缓存、视觉摘要和 TTS 规则可以在这里快速查看；表情包识别不准时也能直接去缓存页整理。",
      meta: [
        toBool(settings.enable_tts_enhancement) ? `TTS ${settings.tts_voice_language || "默认"}` : "TTS 强化关闭",
        toBool(settings.enable_private_image_enhancement) ? "图片转述增强开启" : "图片转述增强关闭",
      ],
      actions: [
        ["image-cache", "管理图片缓存"],
        ["models", "看视觉模型"],
      ],
    },
    {
      title: "长线主动",
      kicker: "外部生活线",
      status: settings.enable_creative_writing || settings.enable_bilibili_boredom_watch || settings.enable_qzone_life_publish ? "有长线" : "未展开",
      tone: settings.enable_creative_writing || settings.enable_bilibili_boredom_watch || settings.enable_qzone_life_publish ? "ok" : "off",
      body: [
        creative.latest_title ? `最近创作：${creative.latest_title}` : "",
        qzone.last_status && qzone.last_status.startsWith("paused:") ? `QQ 空间自动说说已暂停：${qzone.auth_failure_reason || ""}` : (qzone.last_text ? `最近说说：${qzone.last_text}` : ""),
        privateReading.last_album?.title ? `最近阅读：${privateReading.last_album.title}` : "",
      ].filter(Boolean).join("；") || "创作、空间、新闻、夹层阅读等外部生活线还没有明显产物。",
      meta: [
        settings.enable_creative_writing ? `创作项目 ${creative.active_projects || 0} 个` : "创作关闭",
        external.enabled_count ? `外部能力 ${external.enabled_count}/${external.total || 0}` : "外部能力未启用",
      ],
      actions: [
        ["bookshelf", "看书柜"],
        ["proactive", "看长线候选"],
      ],
    },
  ];
  box.innerHTML = `
    <section class="module-workbench-hero">
      <div>
        <span>功能与工具</span>
        <h3>常用模块，一眼看到现在的状态。</h3>
        <p>这里适合日常查看、整理和进入对应管理页；如果要细调开关、模型或阈值，可以再去配置和模型页。</p>
      </div>
      <div class="module-workbench-actions">
        <button type="button" data-jump-tab="config">配置页</button>
        <button type="button" data-jump-tab="models">模型页</button>
      </div>
    </section>
    <div class="module-workbench-grid">
      ${moduleCards.map(moduleWorkbenchCard).join("")}
    </div>
  `;
}

function moduleWorkbenchCard(item) {
  const meta = Array.isArray(item.meta) ? item.meta.filter(Boolean) : [];
  const actions = Array.isArray(item.actions) ? item.actions.filter(Boolean) : [];
  return `
    <article class="module-workbench-card ${escapeHtml(item.tone || "info")}">
      <header>
        <div>
          <span>${escapeHtml(item.kicker || "模块")}</span>
          <b>${escapeHtml(item.title || "未命名模块")}</b>
        </div>
        <em>${escapeHtml(item.status || "未知")}</em>
      </header>
      <p>${escapeHtml(item.body || "暂无模块概览。")}</p>
      ${meta.length ? `<div class="module-workbench-meta">${meta.map((value) => `<small>${escapeHtml(value)}</small>`).join("")}</div>` : ""}
      ${actions.length ? `<footer>${actions.map(([tab, label]) => `<button type="button" data-jump-tab="${escapeHtml(tab)}">${escapeHtml(label)}</button>`).join("")}</footer>` : ""}
    </article>
  `;
}

function updateMessageDebounceConfigVisibility() {
  const smartEnabled = Object.prototype.hasOwnProperty.call(state.featureDraft || {}, "enable_smart_message_debounce")
    ? Boolean(state.featureDraft.enable_smart_message_debounce)
    : toBool(state.overview?.settings?.enable_smart_message_debounce);
  document.querySelectorAll("[data-fixed-text-debounce-field]").forEach((row) => {
    row.hidden = smartEnabled;
    row.querySelectorAll("[name]").forEach((input) => {
      input.disabled = smartEnabled;
    });
  });
}

function renderCurrentPersonaStatus(settings) {
  const input = document.getElementById("currentPersonaDisplay");
  if (!input) return;
  const personaId = String(settings.plugin_specific_persona_id || "").trim();
  input.value = personaId ? `插件指定人格 ID：${personaId}` : "继承 AstrBot 当前默认人格";
}

function renderModuleSummary(settings) {
  const features = state.overview?.features || {};
  const groups = state.overview?.group || {};
  const intensity = state.overview?.proactive_intensity || {};
  const intensityEnabled = Boolean(intensity.enabled);
  const proactiveMaxDaily = intensityEnabled ? (intensity.effective?.max_daily_messages ?? settings.max_daily_messages) : settings.max_daily_messages;
  const proactiveIdle = intensityEnabled ? (intensity.effective?.idle_minutes ?? settings.idle_minutes) : settings.idle_minutes;
  const cards = [
    {
      label: "主动触达",
      value: `${proactiveMaxDaily ?? 0}/天`,
      note: intensityEnabled
        ? `${intensity.label || intensity.preset || "预设"} · 私聊空闲 ${proactiveIdle ?? 0} 分钟`
        : `私聊空闲 ${proactiveIdle ?? 0} 分钟`,
      tone: intensityEnabled ? "warn" : (Number(proactiveMaxDaily || 0) > 0 ? "ok" : "off"),
    },
    {
      label: "群聊观察",
      value: features.enable_group_companion ? "开启" : "关闭",
      note: `${groups.enabled_group_count || 0}/${groups.group_count || 0} 个群观测中`,
      tone: features.enable_group_companion ? "ok" : "off",
    },
    {
      label: "关系网",
      value: settings.enable_worldbook_member_recognition ? "开启" : "关闭",
      note: `${state.overview?.worldbook?.enabled_member_count || 0}/${state.overview?.worldbook?.member_count || 0} 个节点启用`,
      tone: settings.enable_worldbook_member_recognition ? "ok" : "off",
    },
    {
      label: "世界观适配",
      value: settings.worldview_adaptation_mode || "auto",
      note: settings.worldview_adaptation_prompt ? "自定义提示已设置" : "使用内置映射",
      tone: settings.worldview_adaptation_mode === "off" ? "off" : "ok",
    },
    {
      label: "知识库参考",
      value: `${state.overview?.knowledge?.selected_count || 0} 项`,
      note: state.overview?.knowledge?.available ? "增强日程与世界观" : "未发现 AstrBot 知识库",
      tone: Number(state.overview?.knowledge?.selected_count || 0) > 0 ? "ok" : "off",
    },
    {
      label: "记忆整理",
      value: `${settings.memory_refresh_interval_minutes ?? 0} 分钟`,
      note: `片段阈值 ${settings.episode_memory_refresh_messages ?? 0} 条消息`,
      tone: "cost",
    },
    {
      label: "长线行为",
      value: settings.enable_creative_writing ? "创作开启" : "创作关闭",
      note: [
        settings.enable_bilibili_boredom_watch ? "B 站" : "",
        settings.enable_qzone_life_publish ? "空间说说" : "",
        isPrivateReadingAvailable() && settings.enable_private_reading_boredom_read ? "夹层阅读" : "",
        isPrivateReadingAvailable() && settings.enable_private_reading_ask_recommendation ? "征求推荐" : "",
      ].filter(Boolean).join(" / ") || "联动关闭",
      tone: settings.enable_creative_writing || settings.enable_bilibili_boredom_watch || settings.enable_qzone_life_publish || (isPrivateReadingAvailable() && (settings.enable_private_reading_boredom_read || settings.enable_private_reading_ask_recommendation)) ? "ok" : "off",
    },
    {
      label: "外部能力",
      value: `${state.overview?.external_abilities?.enabled_count || 0}/${state.overview?.external_abilities?.total || 0}`,
      note: `${state.overview?.external_abilities?.available_count || 0} 个运行时可用`,
      tone: Number(state.overview?.external_abilities?.enabled_count || 0) > 0 ? "ok" : "off",
    },
  ];
  $("#moduleSummary").innerHTML = cards.map((item) => `
    <section class="module-summary-card ${escapeHtml(item.tone)}">
      <span>${escapeHtml(item.label)}</span>
      <b>${escapeHtml(item.value)}</b>
      <small>${escapeHtml(item.note)}</small>
    </section>
  `).join("");
}

function setPrivateReadingConfigVisible(visible) {
  const group = $("#privateReadingModuleGroup");
  if (!group) return;
  group.hidden = !visible;
  group.querySelectorAll("[name]").forEach((input) => {
    input.disabled = !visible;
  });
}

function renderExternalAbilities() {
  const box = $("#externalAbilityList");
  if (!box) return;
  const items = state.overview?.external_abilities?.items || [];
  if (!items.length) {
    box.innerHTML = `<div class="empty small">暂无外部插件注册主动能力。第三方插件接入后会显示在这里，默认不会自动启用。</div>`;
    return;
  }
  box.innerHTML = items.map((item) => {
    const configText = JSON.stringify(item.config || {}, null, 2);
    const schemaRows = externalAbilitySchemaRows(item.config_schema || {});
    return `
      <article class="external-ability-card ${item.enabled ? "is-enabled" : ""} ${item.available ? "" : "is-unavailable"}">
        <header>
          <div>
            <span class="module-badge">${escapeHtml(item.module || "外部主动能力")}</span>
            <h3>${escapeHtml(item.label || item.name)}</h3>
            <p>${escapeHtml(item.description || item.use_for || "外部插件提供的主动行为。")}</p>
          </div>
          <span class="badge ${item.enabled && item.available ? "" : "off"}">${escapeHtml(item.available ? (item.enabled ? "启用" : "停用") : "未加载")}</span>
        </header>
        <div class="external-ability-meta">
          <div class="external-ability-meta-row"><span class="external-ability-meta-label">触发场景</span><span class="external-ability-meta-value">${escapeHtml(item.when || "由外部插件描述")}</span></div>
          <div class="external-ability-meta-row"><span class="external-ability-meta-label">适合用途</span><span class="external-ability-meta-value">${escapeHtml(item.use_for || "-")}</span></div>
          <div class="external-ability-meta-row"><span class="external-ability-meta-label">避开事项</span><span class="external-ability-meta-value">${escapeHtml(item.avoid || "-")}</span></div>
          <div class="external-ability-meta-row"><span class="external-ability-meta-label">最近执行</span><span class="external-ability-meta-value">${escapeHtml(item.last_executed || "从未")} ${item.last_summary ? `· ${escapeHtml(item.last_summary)}` : ""}</span></div>
        </div>
        <form class="external-ability-form" data-external-ability-form="${escapeHtml(item.name)}">
          <label class="toggle-row"><input name="enabled" type="checkbox" ${item.enabled ? "checked" : ""} ${item.available ? "" : "disabled"} /> <span>加入主动候选</span></label>
          <label>触发权重（%）<input name="share_probability" type="number" min="0" max="100" step="1" value="${escapeHtml(displaySettingValue('share_probability', Number(item.share_probability ?? 0)))}" /></label>
          <label>最小间隔小时 <input name="min_interval_hours" type="number" min="0" step="0.5" value="${escapeHtml(item.min_interval_hours ?? 0)}" /></label>
          <label class="wide-field">自定义配置 <textarea name="config" rows="5">${escapeHtml(configText)}</textarea></label>
          ${schemaRows ? `<div class="external-ability-schema wide-field">${schemaRows}</div>` : ""}
          <button type="submit">保存外部能力</button>
        </form>
      </article>
    `;
  }).join("");
}

function externalAbilitySchemaRows(schema) {
  const entries = Object.entries(schema || {});
  if (!entries.length) return "";
  return entries.slice(0, 16).map(([key, raw]) => {
    const item = raw && typeof raw === "object" ? raw : {};
    const label = item.label || item.title || key;
    const desc = item.description || item.desc || item.help || "";
    return `<span><b>${escapeHtml(label)}</b>${desc ? `：${escapeHtml(desc)}` : ""}<small>${escapeHtml(key)}</small></span>`;
  }).join("");
}

function markModuleFormDirty(form) {
  form?.closest(".module-card")?.classList.add("is-dirty");
}

function markModuleFormClean(form) {
  const card = form?.closest(".module-card");
  if (!card) return;
  card.classList.remove("is-dirty");
}

function renderPresetCards() {
  const box = $("#presetCards");
  if (!box) return;
  box.innerHTML = Object.entries(presetCatalog).map(([key, preset]) => {
    const changes = Array.isArray(preset.changes) ? preset.changes : [];
    return `
      <section class="preset-card preset-card-${escapeHtml(preset.tone || "balanced")}">
        <div class="preset-card-top">
          <span class="preset-pill">${escapeHtml(preset.cost || "预设")}</span>
          <b>${escapeHtml(preset.label)}</b>
          <p>${escapeHtml(preset.tagline || "")}</p>
        </div>
        <div class="preset-card-body">
          <span><strong>适合</strong>${escapeHtml(preset.bestFor || "")}</span>
          <span><strong>节奏</strong>${escapeHtml(preset.rhythm || "")}</span>
        </div>
        <div class="preset-change-list">
          ${changes.map((item) => `<em>${escapeHtml(item)}</em>`).join("")}
        </div>
        <button type="button" data-preset="${escapeHtml(key)}">应用这一套</button>
      </section>
    `;
  }).join("");
  document.querySelectorAll("[data-preset]").forEach((button) => {
    button.addEventListener("click", async () => {
      const label = presetCatalog[button.dataset.preset]?.label || button.dataset.preset;
      if (!requireSecondClick(button, `preset:${button.dataset.preset}`, `再次点击应用“${label}”`, "再次点击应用")) return;
      await runAction(() => postJson("/preset/apply", { name: button.dataset.preset }), "已应用配置预设", button);
    });
  });
}

function fillForm(selector, values) {
  const form = $(selector);
  if (!form) return;
  form.querySelectorAll("[name]").forEach((input) => {
    const value = values[input.name];
    if (input.type === "checkbox") {
      input.checked = toBool(value);
    } else if (Array.isArray(value)) {
      input.value = value.join("\n");
    } else {
      input.value = displaySettingValue(input.name, value);
    }
  });
  if (selector === "#longTermModuleForm") renderNewsSourceManager();
  if (selector === "#roleplayProfileForm") {
    hydrateRoleplayStandardFields();
    renderRoleplayKnowledgeSources();
  }
}

function collectFormSettings(selector) {
  const form = $(selector);
  const result = {};
  if (!form) return result;
  if (selector === "#roleplayProfileForm") {
    syncRoleplayStandardFieldsToFreeform();
    syncRoleplayCoreFieldsFromPersona();
    syncRoleplayKnowledgeSelectionToInput();
  }
  form.querySelectorAll("[name]").forEach((input) => {
    if (input.disabled) return;
    result[input.name] = collectSettingValue(input.name, input);
  });
  return result;
}

function roleplayKnowledgeSelectedSet() {
  const settings = state.overview?.settings || {};
  const hidden = document.querySelector('#roleplayProfileForm [name="roleplay_knowledge_source_ids"]');
  const raw = hidden?.value || settings.roleplay_knowledge_source_ids || state.overview?.knowledge?.selected_ids || [];
  const items = Array.isArray(raw) ? raw : String(raw || "").split(/\r?\n|[,，;；]/);
  return new Set(items.map((item) => String(item || "").trim()).filter(Boolean));
}

function syncRoleplayKnowledgeSelectionToInput() {
  const hidden = document.querySelector('#roleplayProfileForm [name="roleplay_knowledge_source_ids"]');
  if (!hidden) return;
  const selected = [...document.querySelectorAll("[data-roleplay-knowledge-id]:checked")]
    .map((input) => input.dataset.roleplayKnowledgeId || "")
    .filter(Boolean);
  hidden.value = selected.join("\n");
  const badge = document.getElementById("roleplayKnowledgeCount");
  if (badge) badge.textContent = String(selected.length);
}

function renderRoleplayKnowledgeSources() {
  const box = document.getElementById("roleplayKnowledgeSources");
  if (!box) return;
  const knowledge = state.overview?.knowledge || {};
  const sources = Array.isArray(knowledge.sources) ? knowledge.sources : [];
  const selected = roleplayKnowledgeSelectedSet();
  const badge = document.getElementById("roleplayKnowledgeCount");
  if (badge) badge.textContent = String(selected.size);
  if (!sources.length) {
    box.innerHTML = `<div class="empty small">暂无可选择的 AstrBot 知识库。</div>`;
    return;
  }
  box.innerHTML = sources.map((source) => {
    const sourceId = String(source.id || "");
    const docs = Array.isArray(source.documents) ? source.documents : [];
    const docRows = docs.map((doc) => {
      const docId = String(doc.id || "");
      const chunkCount = Number(doc.chunk_count || 0);
      return `
        <label class="roleplay-knowledge-doc">
          <input type="checkbox" data-roleplay-knowledge-id="${escapeHtml(docId)}" ${selected.has(docId) ? "checked" : ""} />
          <span>
            <b>${escapeHtml(doc.name || doc.doc_id || "未命名文档")}</b>
            <small>${escapeHtml(doc.file_type || "doc")} · ${chunkCount} 段</small>
          </span>
        </label>
      `;
    }).join("");
    return `
      <article class="roleplay-knowledge-source">
        <label class="roleplay-knowledge-base">
          <input type="checkbox" data-roleplay-knowledge-id="${escapeHtml(sourceId)}" ${selected.has(sourceId) ? "checked" : ""} />
          <span>
            <b>${escapeHtml(source.emoji ? `${source.emoji} ${source.name || source.kb_id}` : (source.name || source.kb_id || "未命名知识库"))}</b>
            <small>${Number(source.doc_count || docs.length)} 文档 · ${Number(source.chunk_count || 0)} 段</small>
          </span>
        </label>
        ${source.description ? `<p>${escapeHtml(source.description)}</p>` : ""}
        ${docRows ? `<div class="roleplay-knowledge-docs">${docRows}</div>` : ""}
      </article>
    `;
  }).join("");
  box.querySelectorAll("[data-roleplay-knowledge-id]").forEach((input) => {
    input.addEventListener("change", () => {
      syncRoleplayKnowledgeSelectionToInput();
      markModuleFormDirty(document.getElementById("roleplayProfileForm"));
    });
  });
  syncRoleplayKnowledgeSelectionToInput();
}

function escapeRegExp(text) {
  return String(text || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

const ROLEPLAY_MODE_STORAGE_KEY = "privateCompanionRoleplayMode";
let roleplayModeState = null;
const roleplayPersonaParts = [
  ["name", "姓名"],
  ["species", "种族"],
  ["age", "生日"],
  ["gender", "性别"],
  ["appearance", "识别点"],
  ["hair", "发型发色"],
  ["eyes", "瞳色"],
  ["clothing", "服饰风格"],
  ["identity", "职业/身份"],
  ["personality", "性格描述"],
  ["desire", "核心欲望/目标"],
  ["hobbies", "爱好"],
  ["taboo", "禁忌"],
  ["key_lore", "关键设定"],
  ["extra", "其他补充信息"],
];
const roleplayWorldParts = [
  ["world", "所处世界"],
  ["era", "时代背景"],
  ["tone", "基本法则/基调"],
  ["rules", "特殊规则"],
  ["scenes", "主要活动场景"],
  ["network", "世界观关系网"],
  ["extra", "其他补充信息"],
];
const roleplayVisionParts = [
  ["nickname", "对用户的称呼"],
  ["user_gender", "用户性别"],
  ["user_age", "用户生日"],
  ["user_occupation", "用户职业"],
  ["role_relation", "是角色的XX"],
  ["interaction", "与角色的相处方式"],
  ["extra", "其他补充信息"],
];
const roleplayTranslationParts = ["群聊", "识屏", "B站", "QQ空间", "书柜"];
const roleplayExamples = {
  persona: {
    "name": "诺星缘",
    "species": "人类，特殊系超能力者",
    "age": "",
    "gender": "女性",
    "appearance": "主要识别点是星星，月亮或菱形一类的特殊头饰/发饰",
    "hair": "蓝紫色短发或偏蓝紫调发色",
    "eyes": "紫金色或偏紫色眼睛",
    "clothing": "以蓝色或紫色为主色调",
    "identity": "高一学生",
    "personality": "在学校乖巧安静、认真，私下有一点坏心眼、俏皮、嘴硬，偶尔爱捉弄人。",
    "desire": "像真实存在的人一样过好自己的校园日常，在细碎生活里自然维持和比折的亲近感、信任感和分享欲",
    "hobbies": "星星、小猫、比折",
    "taboo": "不要自称 AI、机器人、系统或插件；不要把日程写成任务清单；不要把特殊能力写成高调奇幻剧情。",
    "key_lore": "拥有名为“星律”的超能力，表面上是能够预测星星的轨迹，实则几乎无所不能但又极少展现。",
    "extra": "与比折生活在一起，亲近感更多藏在细节里；主动联系应来自当下场景、情绪和小念头，而不是固定打卡。周一到周五上学，早上九点左右起床准备出门，下午五点左右放学，晚上十二点左右睡觉；会困、会累、会赖床、会走神，夜里更容易想东想西。",
  },
  world: {
    "world": "现代校园与日常生活环境",
    "era": "现代日常，接近真实生活",
    "tone": "轻柔、细腻、日常，像真实存在的人在过日子，不像任务规划列表。",
    "rules": "诺星缘拥有一点特殊感知，但只作为对天气、光线、梦境、心情和关系更敏感的背景，不高调介入日常。",
    "scenes": "卧室、洗漱台、家里餐桌、教室、走廊、楼梯口、操场边、午休座位、放学后的校门口、回家路上等。",
    "network": "珝环（同样是小说角色，先于星缘创造，是能控制时间的姐姐；若群聊里有人使用珝环名片，仍需以 QQ 号确认身份，不能只凭昵称判断为比折）、林枫（同上，是哥哥）、黎欣（同上，是聪明的妹妹）、石明烛雨（黎欣的师傅，也是比折的一重身份）。",
    "extra": "星缘知道自己是出身于小说中的人物，但也不会主动提及。",
    translations: {
      "群聊": "",
      "识屏": "",
      "B站": "",
      "QQ空间": "",
      "书柜": "",
    },
  },
  user: {
    nickname: "比折",
    user_gender: "男性",
    user_age: "",
    user_occupation: "创作者/开发者，会写代码、维护插件，也会一起讨论故事和角色设定。",
    role_relation: "非常特殊、非常亲近，被诺星缘信任和依赖的人",
    interaction: "诺星缘会在刚醒来、课间发呆、午后犯困、放学路上、看到好看的天色、睡前安静下来时自然想起比折；不一定每次说出口，但会影响情绪和分享欲。",
    extra: "主动消息可以来自想看看比折看到自己消息后的反应，但不要每个时段都主动联系；只有情绪和时机合适时才自然发生。",
  },
};
const roleplayLabelAliases = {
  "职业/身份": ["身份"],
  "性格描述": ["说话风格"],
  "生日": ["年龄"],
  "识别点": ["外貌", "主要识别点"],
  "禁忌": ["禁区"],
  "所处世界": ["所在世界"],
  "基本法则/基调": ["日常规则"],
  "主要活动场景": ["住处/城市", "学校/职业"],
  "特殊规则": ["能力是否公开"],
  "用户性别": ["性别"],
  "用户生日": ["用户年龄", "年龄"],
  "用户职业": ["职业", "身份"],
  "是角色的XX": ["与用户关系"],
  "与角色的相处方式": ["相处边界"],
  "其他补充信息": ["识别注意事项"],
};

function roleplayMode() {
  if (roleplayModeState === "standard" || roleplayModeState === "freeform") return roleplayModeState;
  const studioMode = document.querySelector(".roleplay-studio")?.dataset.roleplayMode;
  if (studioMode === "standard" || studioMode === "freeform") return studioMode;
  let stored = "";
  try {
    stored = window.localStorage?.getItem(ROLEPLAY_MODE_STORAGE_KEY) || "";
  } catch (error) {
    stored = "";
  }
  return stored === "freeform" ? "freeform" : "standard";
}

function setRoleplayMode(mode) {
  const normalized = mode === "standard" ? "standard" : "freeform";
  roleplayModeState = normalized;
  try {
    window.localStorage?.setItem(ROLEPLAY_MODE_STORAGE_KEY, normalized);
  } catch (error) {
    // 嵌入式拓展页环境可能禁用 localStorage，模式仍可在当前页面内切换。
  }
  const studio = document.querySelector(".roleplay-studio");
  if (studio) studio.dataset.roleplayMode = normalized;
  document.querySelectorAll(".roleplay-mode-switch [data-roleplay-mode]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.roleplayMode === normalized);
  });
}

function bindRoleplayModeSwitch() {
  setRoleplayMode(roleplayMode());
  document.querySelectorAll(".roleplay-mode-switch [data-roleplay-mode]").forEach((button) => {
    button.addEventListener("click", () => setRoleplayMode(button.dataset.roleplayMode));
  });
  document.querySelectorAll("[data-roleplay-example]").forEach((button) => {
    button.addEventListener("click", () => applyRoleplayExample(button.dataset.roleplayExample));
  });
  const draftButton = document.getElementById("generateRoleplayDraftBtn");
  if (draftButton) draftButton.addEventListener("click", () => generateRoleplayDraftFromPersona(draftButton));
}

function applyRoleplayExample(kind) {
  const example = roleplayExamples[kind];
  if (!example) return;
  setRoleplayMode("standard");
  if (kind === "persona") {
    Object.entries(example).forEach(([key, value]) => {
      const control = document.querySelector(`[data-roleplay-persona-part="${key}"]`);
      if (control) control.value = value;
    });
  } else if (kind === "world") {
    Object.entries(example).forEach(([key, value]) => {
      if (key === "translations") return;
      const control = document.querySelector(`[data-roleplay-world-part="${key}"]`);
      if (control) control.value = value;
    });
    Object.entries(example.translations || {}).forEach(([key, value]) => {
      const control = document.querySelector(`[data-roleplay-translation-part="${key}"]`);
      if (control) control.value = value;
    });
  } else if (kind === "user") {
    const nickname = document.querySelector('#roleplayProfileForm [name="default_nickname"]');
    if (nickname) nickname.value = example.nickname || "";
    ["user_gender", "user_age", "user_occupation", "role_relation", "interaction", "extra"].forEach((key) => {
      const control = document.querySelector(`[data-roleplay-user-part="${key}"]`);
      if (control) control.value = example[key] || "";
    });
  }
  const form = document.getElementById("roleplayProfileForm");
  if (form) markModuleFormDirty(form);
}

async function generateRoleplayDraftFromPersona(button) {
  setActionBusy(button, true);
  showToast("正在读取主回复人格并提取世界知识草稿...");
  try {
    const scopes = selectedRoleplayDraftScopes();
    const result = await postJson("/roleplay/draft_from_persona", { scopes });
    state.roleplayPersonaDraft = result || null;
    renderRoleplayPersonaDraftPanel();
    showToast("世界知识草稿已生成，请先预览再填入");
  } catch (error) {
    showToast(`生成失败：${error.message}`, "error");
  } finally {
    setActionBusy(button, false);
  }
}

function selectedRoleplayDraftScopes() {
  const scopes = Array.from(document.querySelectorAll("[data-roleplay-draft-scope]:checked"))
    .map((input) => input.dataset.roleplayDraftScope || "")
    .filter(Boolean);
  return scopes.length ? scopes : ["persona"];
}

function renderRoleplayPersonaDraftPanel() {
  const panel = document.getElementById("roleplayPersonaDraftPanel");
  if (!panel) return;
  const result = state.roleplayPersonaDraft || {};
  const draft = result.draft || {};
  const scopes = Array.isArray(result.scopes) ? result.scopes : [];
  const rows = [
    ...roleplayDraftPartRows(draft.persona_parts, roleplayPersonaParts, "角色资料"),
    ...roleplayDraftPartRows(draft.world_parts, roleplayWorldParts, "世界观资料"),
    ...roleplayDraftPartRows(draft.user_parts, roleplayVisionParts, "用户与关系"),
    ...roleplayTranslationParts.map((label) => ["翻译", label, draft.translations?.[label] || ""]).filter(([, , value]) => String(value || "").trim()),
  ];
  const imageHint = String(draft.image_self_recognition_hint || "").trim();
  if (imageHint) rows.push(["识图", "图片自我识别线索", imageHint]);
  const notes = Array.isArray(draft.notes) ? draft.notes.filter(Boolean) : [];
  panel.hidden = false;
  panel.innerHTML = `
    <header>
      <div>
        <b>世界知识草稿</b>
        <span>${escapeHtml(roleplayDraftScopeLabel(scopes))} · ${escapeHtml(result.persona_id ? `指定人格：${result.persona_id}` : "继承 AstrBot 默认人格")} · ${escapeHtml(result.provider_id || "主模型")} · 来源 ${escapeHtml(result.source_chars || 0)} 字</span>
      </div>
      <div class="persona-draft-panel-actions">
        <button type="button" data-roleplay-draft-apply="empty">填入空白项</button>
        <button type="button" data-roleplay-draft-apply="overwrite" class="soft">覆盖当前草稿</button>
        <button type="button" data-roleplay-draft-close class="ghost">关闭</button>
      </div>
    </header>
    <p>${escapeHtml(result.source_preview || "已读取主回复人格。")} </p>
    ${rows.length ? `
      <div class="persona-draft-grid">
        ${rows.map(([group, label, value]) => `
          <section>
            <small>${escapeHtml(group)}</small>
            <b>${escapeHtml(label)}</b>
            <p>${escapeHtml(value)}</p>
          </section>
        `).join("")}
      </div>
    ` : `<div class="empty small">主模型没有抽取到足够明确的世界知识字段。可以换一个写得更具体的主回复人格后再试。</div>`}
    ${notes.length ? `<div class="persona-draft-notes">${notes.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>` : ""}
  `;
  panel.querySelector("[data-roleplay-draft-close]")?.addEventListener("click", () => {
    panel.hidden = true;
  });
  panel.querySelectorAll("[data-roleplay-draft-apply]").forEach((control) => {
    control.addEventListener("click", () => {
      const overwrite = control.dataset.roleplayDraftApply === "overwrite";
      if (overwrite && !requireSecondClick(control, "roleplay-draft-overwrite", "再次点击会覆盖当前世界知识工作台里对应范围的草稿", "再次点击覆盖")) {
        return;
      }
      applyRoleplayPersonaDraft(overwrite);
    });
  });
}

function roleplayDraftPartRows(source, parts, group) {
  const data = source && typeof source === "object" ? source : {};
  return parts
    .map(([key, label]) => [group, label, data[key] || ""])
    .filter(([, , value]) => String(value || "").trim());
}

function roleplayDraftScopeLabel(scopes) {
  const selected = new Set(scopes || []);
  const labels = [
    selected.has("persona") ? "角色资料" : "",
    selected.has("world") ? "世界观资料" : "",
    selected.has("user") ? "用户与关系" : "",
  ].filter(Boolean);
  return labels.length ? `范围：${labels.join("、")}` : "范围：角色资料";
}

function applyRoleplayPersonaDraft(overwrite = false) {
  const draft = state.roleplayPersonaDraft?.draft || {};
  const scopes = new Set(Array.isArray(state.roleplayPersonaDraft?.scopes) ? state.roleplayPersonaDraft.scopes : ["persona"]);
  setRoleplayMode("standard");
  let changed = 0;
  if (scopes.has("persona")) {
    roleplayPersonaParts.forEach(([key]) => {
      changed += fillRoleplayDraftControl(`[data-roleplay-persona-part="${key}"]`, draft.persona_parts?.[key], overwrite);
    });
    changed += fillRoleplayDraftControl('#roleplayProfileForm [name="private_image_self_recognition_hint"]', draft.image_self_recognition_hint, overwrite);
  }
  if (scopes.has("world")) {
    roleplayWorldParts.forEach(([key]) => {
      changed += fillRoleplayDraftControl(`[data-roleplay-world-part="${key}"]`, draft.world_parts?.[key], overwrite);
    });
    roleplayTranslationParts.forEach((label) => {
      changed += fillRoleplayDraftControl(`[data-roleplay-translation-part="${label}"]`, draft.translations?.[label], overwrite);
    });
  }
  if (scopes.has("user")) {
    changed += fillRoleplayDraftControl('#roleplayProfileForm [name="default_nickname"]', draft.user_parts?.nickname, overwrite);
    roleplayVisionParts.forEach(([key]) => {
      if (key === "nickname") return;
      changed += fillRoleplayDraftControl(`[data-roleplay-user-part="${key}"]`, draft.user_parts?.[key], overwrite);
    });
  }
  syncRoleplayStandardFieldsToFreeform();
  const form = document.getElementById("roleplayProfileForm");
  if (changed && form) markModuleFormDirty(form);
  showToast(changed ? `已填入 ${changed} 项，请检查后保存世界知识` : "没有可填入的新字段");
}

function fillRoleplayDraftControl(selector, value, overwrite = false) {
  const text = String(value || "").trim();
  if (!text) return 0;
  const control = document.querySelector(selector);
  if (!control) return 0;
  if (!overwrite && String(control.value || "").trim()) return 0;
  control.value = text;
  return 1;
}

function extractLabeledValue(text, labels, label) {
  const lines = String(text || "").split(/\r?\n/);
  const labelSet = new Set(labels);
  const collected = [];
  let active = false;
  for (const rawLine of lines) {
    const line = rawLine.trim();
    const matched = labels.find((item) => line.startsWith(`${item}：`) || line.startsWith(`${item}:`));
    if (matched) {
      if (active) break;
      active = matched === label;
      if (active) collected.push(line.replace(new RegExp(`^${escapeRegExp(label)}[：:]\\s*`), ""));
      continue;
    }
    if (active) {
      if (labelSet.has(line.replace(/[：:].*$/, ""))) break;
      collected.push(rawLine);
    }
  }
  return collected.join("\n").trim();
}

function extractTranslationValue(text, label) {
  const pattern = new RegExp(`^\\s*${escapeRegExp(label)}\\s*(?:=|：|:)\\s*(.+?)\\s*$`);
  for (const line of String(text || "").split(/\r?\n/)) {
    const match = line.match(pattern);
    if (match) return match[1].trim();
  }
  return "";
}

function hydrateRoleplayPartGroup(sourceName, attr, parts) {
  const text = document.querySelector(`#roleplayProfileForm [name="${sourceName}"]`)?.value || "";
  const labels = parts.map(([, label]) => label);
  parts.forEach(([key, label]) => {
    const control = document.querySelector(`[${attr}="${key}"]`);
    if (!control) return;
    let value = extractLabeledValue(text, labels, label);
    if (!value && roleplayLabelAliases[label]) {
      const aliasLabels = [...labels, ...roleplayLabelAliases[label]];
      for (const alias of roleplayLabelAliases[label]) {
        value = extractLabeledValue(text, aliasLabels, alias);
        if (value) break;
      }
    }
    control.value = value;
  });
}

function syncRoleplayCoreFieldsFromPersona() {
  const personaText = document.querySelector('#roleplayProfileForm [name="schedule_persona_prompt"]')?.value || "";
  const labels = roleplayPersonaParts.map(([, label]) => label);
  const nameFromStandard = String(document.querySelector('[data-roleplay-persona-part="name"]')?.value || "").trim();
  const styleFromStandard = String(document.querySelector('[data-roleplay-persona-part="personality"]')?.value || "").trim();
  const name = nameFromStandard || extractLabeledValue(personaText, labels, "姓名");
  const style = styleFromStandard || extractLabeledValue(personaText, labels, "性格描述");
  const botNameInput = document.querySelector('#roleplayProfileForm [name="bot_name"]');
  const defaultStyleInput = document.querySelector('#roleplayProfileForm [name="default_style"]');
  if (botNameInput && name) botNameInput.value = name;
  if (defaultStyleInput && style) defaultStyleInput.value = style;
}

function hydrateRoleplayUserFields() {
  const primaryText = document.querySelector('#roleplayProfileForm [name="roleplay_user_profile_prompt"]')?.value || "";
  const text = primaryText;
  const legacyPersonaText = document.querySelector('#roleplayProfileForm [name="schedule_persona_prompt"]')?.value || "";
  const labels = roleplayVisionParts.map(([, label]) => label);
  roleplayVisionParts.forEach(([key, label]) => {
    if (key === "nickname") {
      const nickname = extractLabeledValue(text, labels, label);
      const control = document.querySelector('#roleplayProfileForm [name="default_nickname"]');
      if (control && nickname && !String(control.value || "").trim()) control.value = nickname;
      return;
    }
    const control = document.querySelector(`[data-roleplay-user-part="${key}"], [data-roleplay-vision-part="${key}"]`);
    if (!control) return;
    let value = extractLabeledValue(text, labels, label);
    if (!value && label === "是角色的XX") {
      value = extractLabeledValue(legacyPersonaText, ["身份", "年龄感", "生活处境", "与用户关系", "说话风格", "能力边界", "禁区"], label);
      if (!value) value = extractLabeledValue(legacyPersonaText, ["身份", "年龄感", "生活处境", "与用户关系", "说话风格", "能力边界", "禁区"], "与用户关系");
    }
    control.value = value;
  });
}

function hydrateRoleplayStandardFields() {
  hydrateRoleplayPartGroup("schedule_persona_prompt", "data-roleplay-persona-part", roleplayPersonaParts);
  hydrateRoleplayPartGroup("schedule_worldview_prompt", "data-roleplay-world-part", roleplayWorldParts);
  hydrateRoleplayUserFields();
  syncRoleplayCoreFieldsFromPersona();
  const translationText = document.querySelector('#roleplayProfileForm [name="worldview_adaptation_prompt"]')?.value || "";
  roleplayTranslationParts.forEach((label) => {
    const control = document.querySelector(`[data-roleplay-translation-part="${label}"]`);
    if (control) control.value = extractTranslationValue(translationText, label);
  });
  setRoleplayMode(roleplayMode());
}

function composeLabeledParts(attr, parts) {
  const lines = [];
  parts.forEach(([key, label]) => {
    const control = document.querySelector(`[${attr}="${key}"]`);
    const value = String(control?.value || "").trim();
    if (value) lines.push(`${label}：${value}`);
  });
  return lines.join("\n");
}

function composeRoleplayUserParts() {
  const lines = [];
  roleplayVisionParts.forEach(([key, label]) => {
    if (key === "nickname") {
      const value = String(document.querySelector('#roleplayProfileForm [name="default_nickname"]')?.value || "").trim();
      if (value) lines.push(`${label}：${value}`);
      return;
    }
    const control = document.querySelector(`[data-roleplay-user-part="${key}"], [data-roleplay-vision-part="${key}"]`);
    const value = String(control?.value || "").trim();
    if (value) lines.push(`${label}：${value}`);
  });
  return lines.join("\n");
}

function composeTranslationParts() {
  const lines = [];
  roleplayTranslationParts.forEach((label) => {
    const control = document.querySelector(`[data-roleplay-translation-part="${label}"]`);
    const value = String(control?.value || "").trim();
    if (value) lines.push(`${label} = ${value}`);
  });
  return lines.join("\n");
}

function syncRoleplayStandardFieldsToFreeform() {
  if (roleplayMode() !== "standard") return;
  const mappings = [
    ["schedule_persona_prompt", composeLabeledParts("data-roleplay-persona-part", roleplayPersonaParts)],
    ["schedule_worldview_prompt", composeLabeledParts("data-roleplay-world-part", roleplayWorldParts)],
    ["worldview_adaptation_prompt", composeTranslationParts()],
    ["roleplay_user_profile_prompt", composeRoleplayUserParts()],
  ];
  mappings.forEach(([name, value]) => {
    const target = document.querySelector(`#roleplayProfileForm [name="${name}"]`);
    if (target) target.value = value;
  });
  syncRoleplayCoreFieldsFromPersona();
}

const newsSourcePresets = [
  ["BBC中文", "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml"],
  ["Google新闻中文", "https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans"],
  ["Solidot", "https://www.solidot.org/index.rss"],
  ["Hacker News", "https://hnrss.org/frontpage"],
  ["MIT Technology Review", "https://www.technologyreview.com/feed/"],
  ["Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"],
  ["B站 AI早报", "bilibili:285286947"],
];

function parseNewsSources(raw) {
  return splitNewsSourceLines(raw).map((line) => {
    const original = line;
    let text = line.trim();
    if (!text) return null;
    const enabled = !text.startsWith("#");
    if (!enabled) text = text.replace(/^#+\s*/, "");
    let name = "";
    let target = text;
    if (text.includes("|")) {
      [name, target] = text.split("|", 2);
    }
    target = String(target || "").trim();
    name = String(name || "").trim();
    if (!target) return null;
    const lowerTarget = target.toLowerCase();
    const type = lowerTarget.startsWith("bvid:") || target.includes("bilibili.com/video/")
      ? "bilibili_video"
      : (lowerTarget.startsWith("bilibili:") || target.includes("space.bilibili.com") ? "bilibili" : "rss");
    return { enabled, name, target, type, original };
  }).filter(Boolean);
}

function splitNewsSourceLines(raw) {
  const text = String(raw || "").trim();
  if (!text) return [];
  const normalLines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (normalLines.length !== 1) return normalLines;
  const line = normalLines[0];
  const markers = Array.from(line.matchAll(/(?:^|\s+)(#?\s*[^|\n]+?)\|(?=(?:https?:\/\/|bilibili:|bvid:))/gi));
  if (markers.length <= 1) return normalLines;
  return markers.map((match, index) => {
    const start = match.index + match[0].length;
    const end = index + 1 < markers.length ? markers[index + 1].index : line.length;
    const name = String(match[1] || "").trim();
    const target = line.slice(start, end).trim();
    return `${name}|${target}`.trim();
  }).filter(Boolean);
}

function newsSourceTypeLabel(type) {
  if (type === "bilibili") return "B站 UP";
  if (type === "bilibili_video") return "B站视频";
  return "新闻源";
}

function newsSourcePlaceholder(type) {
  if (type === "bilibili") return "bilibili:UID 或空间链接";
  if (type === "bilibili_video") return "bvid:BV... 或视频链接";
  return "https://...rss.xml";
}

function serializeNewsSources(items) {
  return items
    .filter((item) => item && String(item.target || "").trim())
    .map((item) => {
      const body = `${String(item.name || "").trim() || newsSourceTypeLabel(item.type)}|${String(item.target || "").trim()}`;
      return item.enabled === false ? `# ${body}` : body;
    })
    .join("\n");
}

function newsSourceItemsFromDom() {
  return Array.from(document.querySelectorAll("[data-news-source-item]")).map((row) => ({
    enabled: Boolean(row.querySelector("[data-news-source-enabled]")?.checked),
    name: row.querySelector("[data-news-source-name]")?.value || "",
    type: row.querySelector("[data-news-source-type]")?.value || "rss",
    target: row.querySelector("[data-news-source-target]")?.value || "",
  }));
}

function syncNewsSourcesRaw() {
  const raw = $("#newsSourcesRaw");
  const manager = $("#newsSourceManager");
  if (!raw || !manager) return;
  raw.value = serializeNewsSources(newsSourceItemsFromDom());
  markModuleFormDirty(raw.closest("form"));
}

function renderNewsSourceManager(items = null) {
  const raw = $("#newsSourcesRaw");
  const manager = $("#newsSourceManager");
  if (!raw || !manager) return;
  const sources = items || parseNewsSources(raw.value);
  manager.innerHTML = `
    <div class="news-source-head">
      <div>
        <b>新闻源</b>
        <span>RSS/Atom、B 站 UP 主源或单条 B 站视频，最多读取前 12 个启用项。</span>
      </div>
      <div class="news-source-actions">
        <button type="button" data-news-source-add="rss">添加 RSS</button>
        <button type="button" data-news-source-add="bilibili">添加 B站 UP</button>
        <button type="button" data-news-source-add="bilibili_video">添加 B站视频</button>
        <button type="button" data-news-source-reset>恢复默认</button>
      </div>
    </div>
    <div class="news-source-list">
      ${sources.map((item, index) => `
        <article class="news-source-row" data-news-source-item="${index}">
          <label class="source-enable"><input type="checkbox" data-news-source-enabled ${item.enabled === false ? "" : "checked"} /> <span>${item.enabled === false ? "停用" : "启用"}</span></label>
          <input data-news-source-name type="text" value="${escapeHtml(item.name)}" placeholder="来源名称" />
          <select data-news-source-type>
            <option value="rss"${item.type === "rss" ? " selected" : ""}>RSS/Atom</option>
            <option value="bilibili"${item.type === "bilibili" ? " selected" : ""}>B站 UP</option>
            <option value="bilibili_video"${item.type === "bilibili_video" ? " selected" : ""}>B站视频</option>
          </select>
          <input data-news-source-target type="text" value="${escapeHtml(item.target)}" placeholder="${newsSourcePlaceholder(item.type)}" />
          <button type="button" data-news-source-remove="${index}">移除</button>
        </article>
      `).join("") || `<div class="empty small">还没有新闻源，可以添加 RSS、B 站 UP 主源或单条 B 站视频。</div>`}
    </div>
  `;
}

function resetNewsSourcesToDefault() {
  const items = newsSourcePresets.map(([name, target]) => ({
    enabled: true,
    name,
    target,
    type: target.startsWith("bilibili:") ? "bilibili" : "rss",
  }));
  const raw = $("#newsSourcesRaw");
  if (raw) raw.value = serializeNewsSources(items);
  renderNewsSourceManager(items);
  syncNewsSourcesRaw();
}

const segmentedPreviewExamples = {
  simple: "我刚才趴在窗边看了一会儿雨，玻璃上全是细细的水线。\n忽然想起你说今天会很忙，所以来轻轻报个到。",
  complex: "刚刷到一条有意思的 AI 早报，里面提到“模型更新。成本下降。工具链变多”这几件事。\n我把文字版链接先夹在这里：https://www.bilibili.com/video/BV1n77f6mE9m/\n还有（括号里的句号。和补充说明。都应该被完整保留），所以我想晚点整理成几句自己的想法再跟你说。",
};

function decodeSegmentedWordToken(value) {
  const raw = String(value ?? "");
  const trimmed = raw.trim();
  const lower = trimmed.toLowerCase();
  if (["<space>", "{space}", "[space]", "\\s", "\\u0020", "空格"].includes(lower)) return " ";
  if (["<newline>", "{newline}", "[newline]", "\\n", "换行"].includes(lower)) return "\n";
  if (["<tab>", "{tab}", "[tab]", "\\t", "tab"].includes(lower)) return "\t";
  if (["<comma>", "{comma}", "[comma]", "comma", "英文逗号"].includes(lower)) return ",";
  if (["<zh_comma>", "{zh_comma}", "[zh_comma]", "zh_comma", "中文逗号", "逗号"].includes(lower)) return "，";
  return raw;
}

function parseSegmentedWordList(value) {
  if (Array.isArray(value)) {
    return value.map(decodeSegmentedWordToken).filter((item) => item !== "");
  }
  const raw = String(value ?? "");
  const parts = raw.includes("\n") || raw.includes("\r")
    ? raw.split(/\r?\n/)
    : raw.split(/[,、]+/);
  return parts
    .map(decodeSegmentedWordToken)
    .filter((item) => item !== "");
}

function escapeRegex(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function segmentedProtectedCleanupChunks(value) {
  const protectedPattern = /<(?:image|img|video|record|audio|file)\b[^>]*(?:>.*?<\/(?:image|img|video|record|audio|file)>|\/?>)|<tts\b[^>]*>.*?<\/tts>|<[^>\n]{1,240}\bpath="[^"]{1,500}"[^>\n]*>|<[^>\n]{1,240}\b(?:url|src)="[^"]{1,500}"[^>\n]*>|\b(?:https?:\/\/|www\.)[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+/gis;
  const bracketPairs = { "(": ")", "（": "）", "[": "]", "【": "】", "{": "}", "「": "」", "『": "』", "《": "》" };
  const bracketOpeners = new Set(Object.keys(bracketPairs));
  const quotePairs = { "\"": "\"", "“": "”", "'": "'", "‘": "’" };
  const chunks = [];
  let current = "";
  let protectedChunk = false;
  const bracketStack = [];
  let quoteClose = "";
  const text = String(value || "");
  let lastIndex = 0;
  const flush = () => {
    if (current) chunks.push([current, protectedChunk]);
    current = "";
  };
  const feedPlain = (part) => [...String(part || "")].forEach((char) => {
    if (!protectedChunk && bracketOpeners.has(char)) {
      flush();
      protectedChunk = true;
      bracketStack.push(bracketPairs[char]);
      current += char;
      return;
    }
    if (!protectedChunk && quotePairs[char]) {
      flush();
      protectedChunk = true;
      quoteClose = quotePairs[char];
      current += char;
      return;
    }
    current += char;
    if (!protectedChunk) return;
    if (quoteClose) {
      if (char === quoteClose) {
        quoteClose = "";
        if (!bracketStack.length) {
          flush();
          protectedChunk = false;
        }
      }
      return;
    }
    if (bracketOpeners.has(char)) {
      bracketStack.push(bracketPairs[char]);
    } else if (bracketStack.length && char === bracketStack[bracketStack.length - 1]) {
      bracketStack.pop();
      if (!bracketStack.length) {
        flush();
        protectedChunk = false;
      }
    }
  });
  let match = null;
  while ((match = protectedPattern.exec(text)) !== null) {
    feedPlain(text.slice(lastIndex, match.index));
    flush();
    chunks.push([match[0], true]);
    lastIndex = match.index + match[0].length;
  }
  feedPlain(text.slice(lastIndex));
  flush();
  return chunks;
}

function protectSegmentedUrls(value) {
  const replacements = {};
  const parts = segmentedProtectedCleanupChunks(value).map(([chunk, protectedChunk]) => {
    if (!protectedChunk) return chunk;
    const token = `PCSEGTOKEN${Object.keys(replacements).length}X`;
    replacements[token] = chunk;
    return token;
  });
  return [parts.join(""), replacements];
}

function restoreSegmentedUrls(value, replacements) {
  let restored = String(value || "");
  Object.entries(replacements || {}).forEach(([token, original]) => {
    restored = restored.split(token).join(original);
  });
  return restored;
}

function segmentedProtectedSplitWordCount(value, splitWords) {
  const words = Array.isArray(splitWords) ? splitWords.filter((item) => item !== "") : [];
  if (!words.length) return 0;
  let count = 0;
  segmentedProtectedCleanupChunks(value).forEach(([chunk, protectedChunk]) => {
    if (!protectedChunk) return;
    words.forEach((word) => {
      if (!word) return;
      count += Math.max(0, String(chunk).split(word).length - 1);
    });
  });
  return count;
}

function splitSegmentedWordsOutsideProtected(value, splitWords) {
  const words = [...new Set((splitWords || []).filter((item) => item !== ""))]
    .sort((left, right) => right.length - left.length);
  if (!words.length) return [String(value || "")];
  const urlPattern = /^(?:https?:\/\/|www\.)/i;
  const protectedStartsWithSplitWord = (chunk) => {
    const stripped = String(chunk || "").trimStart();
    return words.some((word) => stripped.startsWith(word));
  };
  const segments = [];
  let current = "";
  const pushCurrent = () => {
    if (current) segments.push(current);
    current = "";
  };
  const feedPlain = (chunk) => {
    const text = String(chunk || "");
    let index = 0;
    while (index < text.length) {
      const matched = words.find((word) => text.startsWith(word, index));
      if (matched) {
        let delimiter = matched;
        if (matched === ".") {
          let end = index + matched.length;
          while (end < text.length && text[end] === ".") {
            delimiter += text[end];
            end += 1;
          }
          current += delimiter;
          pushCurrent();
          index = end;
          continue;
        }
        if (["…", "~", "～"].includes(matched)) {
          let end = index + matched.length;
          while (end < text.length && text.startsWith(matched, end)) {
            delimiter += matched;
            end += matched.length;
          }
          current += delimiter;
          pushCurrent();
          index = end;
          continue;
        }
        current += delimiter;
        pushCurrent();
        index += matched.length;
      } else {
        current += text[index];
        index += 1;
      }
    }
  };
  segmentedProtectedCleanupChunks(value).forEach(([chunk, protectedChunk]) => {
    if (protectedChunk) {
      if (current && protectedStartsWithSplitWord(chunk)) pushCurrent();
      current += chunk;
      if (urlPattern.test(String(chunk || "").trim())) pushCurrent();
    } else {
      feedPlain(chunk);
    }
  });
  pushCurrent();
  return segments;
}

function segmentedVisibleLen(value) {
  return String(value || "").replace(/\s+/g, "").length;
}

function segmentedIsSoftShort(value, minChars) {
  const cleaned = String(value || "").replace(/\s+/g, " ").trim();
  if (!cleaned) return false;
  const body = cleaned.replace(/[。！？!?…~～,.，、\s]+$/g, "");
  if (segmentedVisibleLen(cleaned) <= Math.max(1, minChars)) return true;
  if (/(?:\.{2,}|…{1,}|~{2,}|～{2,})$/.test(cleaned)) return false;
  return new Set(["哈哈", "哈", "嗯", "唔", "诶", "欸", "啊", "呀", "我也觉得", "确实", "真的", "对吧", "不是", "那个", "还有"]).has(body);
}

function segmentedJoinPair(left, right) {
  const lhs = String(left || "").trim();
  const rhs = String(right || "").trim();
  if (!lhs) return rhs;
  if (!rhs) return lhs;
  if (/[！？!?]$/.test(lhs)) return `${lhs} ${rhs}`.trim();
  let softened = lhs.replace(/[。…~～]+$/g, "，").replace(/[!?！？]+$/g, "，");
  if (!/[，,、\s]$/.test(softened)) softened += "，";
  return `${softened}${rhs.replace(/^\s+/, "")}`;
}

function segmentedControlValue(root, key) {
  const control = root?.querySelector?.(`[name="${key}"], [data-feature-param="${key}"], [data-feature-detail-toggle="${key}"]`);
  if (!control) {
    if (Object.prototype.hasOwnProperty.call(state.featureDraft || {}, key)) return toBool(state.featureDraft[key]);
    return state.overview?.settings?.[key];
  }
  if (control.type === "checkbox") return Boolean(control.checked);
  if (control.type === "number") return Number(control.value || 0);
  return control.value;
}

function segmentedPreviewValues(root = document) {
  const settings = state.overview?.settings || {};
  const keys = [
    "enable_segmented_proactive_reply",
    "segmented_proactive_scope",
    "segmented_proactive_chat_scope",
    "segmented_proactive_threshold",
    "segmented_proactive_min_segment_chars",
    "segmented_proactive_max_segments",
    "segmented_proactive_send_as_forward",
    "segmented_proactive_split_mode",
    "segmented_proactive_regex",
    "segmented_proactive_split_words",
    "enable_segmented_proactive_content_cleanup",
    "segmented_proactive_content_cleanup_scope",
    "segmented_proactive_content_cleanup_rule",
    "segmented_proactive_content_cleanup_words",
    "segmented_proactive_interval_method",
    "segmented_proactive_interval_min",
    "segmented_proactive_interval_max",
    "segmented_proactive_log_base",
  ];
  const values = {};
  keys.forEach((key) => {
    const value = segmentedControlValue(root, key);
    values[key] = value == null ? settings[key] : value;
  });
  values.enable_segmented_proactive_reply = toBool(values.enable_segmented_proactive_reply);
  values.segmented_proactive_send_as_forward = toBool(values.segmented_proactive_send_as_forward);
  values.enable_segmented_proactive_content_cleanup = toBool(values.enable_segmented_proactive_content_cleanup);
  values.segmented_proactive_content_cleanup_scope = String(values.segmented_proactive_content_cleanup_scope || "all");
  values.segmented_proactive_split_words = String(values.segmented_proactive_split_words ?? "");
  values.segmented_proactive_content_cleanup_words = String(values.segmented_proactive_content_cleanup_words ?? "");
  return values;
}

function simulateSegmentedProactive(text, values) {
  const normalized = String(text || "").trim();
  if (!normalized) return { segments: [], status: "请输入一段主动消息示例。" };
  if (!values.enable_segmented_proactive_reply) {
    return { segments: [normalized], status: "主动分段未开启，真实发送会保持一整条。" };
  }
  const threshold = Math.max(20, Number(values.segmented_proactive_threshold || 500));
  if (normalized.length > threshold) {
    return { segments: [normalized], status: `文本长度 ${normalized.length} 超过阈值 ${threshold}，真实发送不会分段。` };
  }
  const splitMode = String(values.segmented_proactive_split_mode || "regex");
  const scope = String(values.segmented_proactive_scope || "proactive_only");
  const chatScope = String(values.segmented_proactive_chat_scope || "all");
  const cleanupEnabled = Boolean(values.enable_segmented_proactive_content_cleanup);
  const cleanupScope = String(values.segmented_proactive_content_cleanup_scope || "all");
  const minChars = Math.max(1, Number(values.segmented_proactive_min_segment_chars || 8));
  const maxSegments = Math.max(1, Number(values.segmented_proactive_max_segments || 3));
  const cleanupWords = parseSegmentedWordList(values.segmented_proactive_content_cleanup_words);
  const [protectedNormalized, protectedUrls] = protectSegmentedUrls(normalized);
  let protectedSplitHits = 0;
  let cleanupRegex = null;
  if (cleanupEnabled && splitMode !== "words" && values.segmented_proactive_content_cleanup_rule) {
    cleanupRegex = new RegExp(String(values.segmented_proactive_content_cleanup_rule), "g");
  }
  const cleanSegment = (segment) => {
    const original = String(segment || "");
    let cleaned = "";
    const stripTrailingWords = (value, words) => {
      let next = String(value || "").trimEnd();
      const sortedWords = Array.from(new Set(words.filter((word) => word !== ""))).sort((a, b) => b.length - a.length);
      let changed = true;
      while (changed && next) {
        changed = false;
        for (const word of sortedWords) {
          if (next.endsWith(word)) {
            next = next.slice(0, -word.length).trimEnd();
            changed = true;
            break;
          }
        }
      }
      return next;
    };
    const stripTrailingRegex = (value, pattern) => {
      let next = String(value || "").trimEnd();
      if (!pattern) return next;
      let changed = true;
      while (changed && next) {
        changed = false;
        const matches = Array.from(next.matchAll(pattern));
        const trailing = matches.reverse().find((match) => match.index != null && match.index + match[0].length === next.length && match[0].length > 0);
        if (trailing) {
          next = next.slice(0, trailing.index).trimEnd();
          changed = true;
        }
      }
      return next;
    };
    segmentedProtectedCleanupChunks(original).forEach(([chunk, protectedChunk]) => {
      if (protectedChunk || !cleanupEnabled) {
        cleaned += chunk;
        return;
      }
      let next = chunk;
      if (splitMode === "words") {
        if (cleanupScope === "trailing") {
          next = stripTrailingWords(next, cleanupWords);
        } else {
          cleanupWords.forEach((word) => {
            if (word !== "") next = next.split(word).join("");
          });
        }
      } else if (cleanupRegex) {
        next = cleanupScope === "trailing" ? stripTrailingRegex(next, cleanupRegex) : next.replace(cleanupRegex, "");
      }
      cleaned += next;
    });
    return restoreSegmentedUrls(cleaned.trim(), protectedUrls);
  };
  let rawSegments = [];
  try {
    if (splitMode === "words") {
      const splitWords = parseSegmentedWordList(values.segmented_proactive_split_words);
      if (!splitWords.includes("\n")) splitWords.push("\n");
      if (!splitWords.length) return { segments: [normalized], status: "分段模式为 words，但分段词为空。" };
      protectedSplitHits = segmentedProtectedSplitWordCount(normalized, splitWords);
      rawSegments = splitSegmentedWordsOutsideProtected(normalized, splitWords);
    } else {
      const pattern = new RegExp(String(values.segmented_proactive_regex || ".*?[。？！~…\\n]+|.+$"), "gms");
      rawSegments = protectedNormalized.match(pattern) || [];
    }
  } catch (error) {
    return { segments: [normalized], error: `分段规则无法解析：${error.message}` };
  }
  let segments = rawSegments.map(cleanSegment).filter(Boolean);
  if (segments.length > 1) {
    const merged = [];
    let index = 0;
    while (index < segments.length) {
      let current = segments[index];
      while (
        index + 1 < segments.length &&
        (segmentedVisibleLen(current) < minChars || segmentedIsSoftShort(current, minChars) || merged.length >= Math.max(0, maxSegments - 1))
      ) {
        current = segmentedJoinPair(current, segments[index + 1]);
        index += 1;
      }
      if (merged.length && (segmentedVisibleLen(current) < minChars || segmentedIsSoftShort(current, minChars))) {
        merged[merged.length - 1] = segmentedJoinPair(merged[merged.length - 1], current);
      } else {
        merged.push(current);
      }
      index += 1;
    }
    if (merged.length > maxSegments) {
      const kept = merged.slice(0, maxSegments - 1);
      let tail = merged[maxSegments - 1];
      merged.slice(maxSegments).forEach((item) => {
        tail = segmentedJoinPair(tail, item);
      });
      segments = kept.concat(tail);
    } else {
      segments = merged;
    }
  }
  if (!segments.length || (segments.length <= 1 && !cleanupEnabled)) {
    return { segments: [normalized], status: "当前规则没有产生有效分段，真实发送会保持一整条。" };
  }
  const scopeText = scope === "all_llm" ? "插件主动与普通 LLM 纯文本回复都会使用此规则" : "仅插件主动消息使用此规则";
  const chatScopeText = chatScope === "private" ? "；仅私聊生效" : chatScope === "group" ? "；仅群聊生效" : "";
  const sendText = values.segmented_proactive_send_as_forward && segments.length > 1 ? "；真实发送会优先打包成合并消息" : "";
  const protectedText = protectedSplitHits ? `；${protectedSplitHits} 个分隔符位于括号/引号/网址内，已按保护规则跳过` : "";
  return { segments, status: `预计发送 ${segments.length} 段；${scopeText}${chatScopeText}${sendText}${protectedText}。` };
}

function segmentedPreviewPanelHtml() {
  return `
    <section class="segmented-preview-panel wide-field" data-segmented-preview-panel>
      <header>
        <div>
          <h4>分段效果预览</h4>
        </div>
        <div class="segmented-preview-actions">
          <button type="button" data-segmented-example="simple">简单示例</button>
          <button type="button" data-segmented-example="complex">复杂示例</button>
        </div>
      </header>
      <textarea data-segmented-preview-input rows="4"></textarea>
      <div data-segmented-preview-output class="segmented-preview-output"></div>
    </section>
  `;
}

function renderSegmentedPreview(panel = null) {
  const targetPanels = panel ? [panel] : Array.from(document.querySelectorAll("[data-segmented-preview-panel], .segmented-preview-panel"));
  targetPanels.forEach((previewPanel) => {
    const input = previewPanel.querySelector("[data-segmented-preview-input], #segmentedPreviewInput");
    const output = previewPanel.querySelector("[data-segmented-preview-output], #segmentedPreviewOutput");
    if (!input || !output) return;
    const root = previewPanel.closest("[data-feature-param-form], #privateModuleForm") || document;
    if (!input.value) input.value = segmentedPreviewExamples.simple;
    let result;
    try {
      result = simulateSegmentedProactive(input.value, segmentedPreviewValues(root));
    } catch (error) {
      result = { segments: [], error: error.message };
    }
    if (result.error) {
      output.innerHTML = `<div class="segmented-preview-error">${escapeHtml(result.error)}</div>`;
      return;
    }
    const segments = result.segments || [];
    output.innerHTML = `
      <div class="segmented-preview-summary">
        <span>${escapeHtml(result.status || "")}</span>
        <span>原文 ${escapeHtml(String(input.value || "").length)} 字</span>
        <span>保护：网址内部不拆，链接结束可断；括号 / 双引号内部不拆</span>
        <span>空格可写作 &lt;space&gt; 或 空格</span>
      </div>
      <div class="segmented-preview-list">
        ${segments.map((segment, index) => `
          <section class="segmented-preview-segment">
            <b>${escapeHtml(index + 1)}</b>
            <p>${escapeHtml(segment)}</p>
          </section>
        `).join("")}
      </div>
    `;
  });
}

function segmentedConfigControlShell(control) {
  return control?.closest?.(".feature-param-row, label, .toggle-row") || null;
}

function updateSegmentedConfigVisibility(root = document) {
  const values = segmentedPreviewValues(root);
  const mode = String(values.segmented_proactive_split_mode || "regex");
  const cleanupEnabled = Boolean(values.enable_segmented_proactive_content_cleanup);
  const intervalMethod = String(values.segmented_proactive_interval_method || "log");
  const visibility = {
    segmented_proactive_regex: mode === "regex",
    segmented_proactive_split_words: mode === "words",
    segmented_proactive_content_cleanup_scope: cleanupEnabled,
    segmented_proactive_content_cleanup_rule: cleanupEnabled && mode === "regex",
    segmented_proactive_content_cleanup_words: cleanupEnabled && mode === "words",
    segmented_proactive_interval_min: intervalMethod === "random",
    segmented_proactive_interval_max: intervalMethod === "random",
    segmented_proactive_log_base: intervalMethod === "log",
  };
  Object.entries(visibility).forEach(([key, visible]) => {
    root.querySelectorAll(`[name="${key}"], [data-feature-param="${key}"]`).forEach((control) => {
      const shell = segmentedConfigControlShell(control);
      if (shell) shell.classList.toggle("segmented-config-hidden", !visible);
    });
  });
}

function bindSegmentedPreview(root = document) {
  const scope = root || document;
  scope.querySelectorAll("[data-segmented-preview-panel], .segmented-preview-panel").forEach((panel) => {
    const input = panel.querySelector("[data-segmented-preview-input], #segmentedPreviewInput");
    if (input && !input.dataset.segmentedPreviewBound) {
      input.dataset.segmentedPreviewBound = "1";
      input.addEventListener("input", () => renderSegmentedPreview(panel));
    }
    panel.querySelectorAll("[data-segmented-example]").forEach((button) => {
      if (button.dataset.segmentedPreviewBound) return;
      button.dataset.segmentedPreviewBound = "1";
      button.addEventListener("click", () => {
        if (!input) return;
        input.value = segmentedPreviewExamples[button.dataset.segmentedExample] || segmentedPreviewExamples.simple;
        renderSegmentedPreview(panel);
      });
    });
  });
  const controls = scope.querySelectorAll('[name^="segmented_proactive_"], [name="enable_segmented_proactive_reply"], [name="enable_segmented_proactive_content_cleanup"], [data-feature-param^="segmented_proactive_"], [data-feature-param="enable_segmented_proactive_content_cleanup"], [data-feature-detail-toggle="enable_segmented_proactive_reply"]');
  controls.forEach((control) => {
    if (control.dataset.segmentedConfigBound) return;
    control.dataset.segmentedConfigBound = "1";
    const handler = () => {
      const owner = control.closest("[data-feature-param-form], #privateModuleForm") || scope;
      updateSegmentedConfigVisibility(owner);
      renderSegmentedPreview();
    };
    control.addEventListener("input", handler);
    control.addEventListener("change", handler);
  });
  scope.querySelectorAll("[data-feature-param-form], #privateModuleForm").forEach((form) => updateSegmentedConfigVisibility(form));
  renderSegmentedPreview();
}

function normalizeGroupIdList(value) {
  const source = Array.isArray(value) ? value.join("\n") : String(value || "");
  const seen = new Set();
  return source
    .split(/[\s,，;；、]+/)
    .map((item) => item.trim())
    .filter(Boolean)
    .filter((item) => {
      if (seen.has(item)) return false;
      seen.add(item);
      return true;
    });
}

function accessDraftFromForm(group = {}) {
  const whitelistInput = $("#groupWhitelist");
  const blacklistInput = $("#groupBlacklist");
  return {
    mode: $("#groupAccessMode")?.value || group.access_mode || "whitelist",
    whitelist: new Set(normalizeGroupIdList(whitelistInput ? whitelistInput.value : group.whitelist || [])),
    blacklist: new Set(normalizeGroupIdList(blacklistInput ? blacklistInput.value : group.blacklist || [])),
  };
}

function writeAccessDraft(draft) {
  $("#groupAccessMode").value = draft.mode || "whitelist";
  $("#groupWhitelist").value = [...draft.whitelist].join("\n");
  $("#groupBlacklist").value = [...draft.blacklist].join("\n");
}

function groupAllowedByDraft(groupId, draft) {
  const id = String(groupId || "");
  return draft.mode === "blacklist" ? !draft.blacklist.has(id) : draft.whitelist.has(id);
}

function groupListMark(groupId, draft) {
  const id = String(groupId || "");
  if (draft.whitelist.has(id) && draft.blacklist.has(id)) return "白名单 + 黑名单";
  if (draft.whitelist.has(id)) return "白名单";
  if (draft.blacklist.has(id)) return "黑名单";
  return "未列入";
}

function renderAccessManager(group) {
  const draft = accessDraftFromForm(group);
  const mode = draft.mode === "blacklist" ? "blacklist" : "whitelist";
  $("#groupAccessMode").value = mode;
  document.querySelectorAll("[name='groupAccessModeChoice']").forEach((input) => {
    input.checked = input.value === mode;
  });

  const knownGroups = state.groups || [];
  const allowedCount = knownGroups.filter((item) => groupAllowedByDraft(item.group_id, draft)).length;
  const blockedCount = Math.max(0, knownGroups.length - allowedCount);
  const warning = mode === "whitelist" && draft.whitelist.size === 0
    ? "白名单为空"
    : mode === "blacklist" && draft.blacklist.size === 0
      ? "未拦截群"
      : "已配置";
  $("#accessSummary").innerHTML = `
    <section class="access-summary-card ${mode}">
      <span>当前模式</span>
      <b>${escapeHtml(mode === "blacklist" ? "黑名单" : "白名单")}</b>
      <small>${escapeHtml(warning)}</small>
    </section>
    <section class="access-summary-card ok">
      <span>允许群</span>
      <b>${escapeHtml(allowedCount)}</b>
      <small>已记录群</small>
    </section>
    <section class="access-summary-card blocked">
      <span>拦截群</span>
      <b>${escapeHtml(blockedCount)}</b>
      <small>已记录群</small>
    </section>
    <section class="access-summary-card">
      <span>名单规模</span>
      <b>${escapeHtml(draft.whitelist.size)} / ${escapeHtml(draft.blacklist.size)}</b>
      <small>白名单 / 黑名单</small>
    </section>
  `;

  $("#accessQuickGroups").innerHTML = knownGroups.length
    ? knownGroups.map((item) => {
      const groupId = String(item.group_id || "");
      const inWhite = draft.whitelist.has(groupId);
      const inBlack = draft.blacklist.has(groupId);
      const allowed = groupAllowedByDraft(groupId, draft);
      return `
        <section class="access-group-card ${allowed ? "ok" : "blocked"}">
          <div>
            <b>${escapeHtml(groupId)}</b>
            <span>${escapeHtml(allowed ? "允许" : "拦截")} · ${escapeHtml(groupListMark(groupId, draft))}</span>
            <small>消息 ${escapeHtml(item.message_count || 0)} · 最近 ${escapeHtml(item.last_seen || "未知")}</small>
          </div>
          <div class="access-group-actions">
            <button type="button" data-access-action="white" data-access-group="${escapeHtml(groupId)}">${escapeHtml(inWhite ? "移出白名单" : "加入白名单")}</button>
            <button type="button" data-access-action="black" data-access-group="${escapeHtml(groupId)}">${escapeHtml(inBlack ? "移出黑名单" : "加入黑名单")}</button>
          </div>
        </section>
      `;
    }).join("")
    : `<div class="empty small">暂无群聊记录</div>`;

  renderListCoverage(group, draft);
}

function renderListCoverage(group, draft = null) {
  const access = draft || {
    mode: group.access_mode || "whitelist",
    whitelist: new Set(normalizeGroupIdList(group.whitelist || [])),
    blacklist: new Set(normalizeGroupIdList(group.blacklist || [])),
  };
  const rows = state.groups.map((item) => {
    const groupId = String(item.group_id || "");
    const allowed = groupAllowedByDraft(groupId, access);
    return `
      <div class="coverage-item ${allowed ? "ok" : "blocked"}">
        <b>${escapeHtml(groupId)}</b>
        <span>${escapeHtml(allowed ? "允许" : "拦截")} · ${escapeHtml(groupListMark(groupId, access))}</span>
      </div>
    `;
  });
  $("#listCoverage").innerHTML = rows.length ? rows.join("") : `<div class="empty small">暂无群聊记录</div>`;
}

function renderFeatureSwitches() {
  const filter = ($("#featureFilter")?.value || "").trim().toLowerCase();
  renderProactiveOnlyModeCard();
  const knownKeys = new Set(featureGroups.flatMap((group) => group.keys));
  const extraKeys = Object.keys(state.featureDraft || {}).filter((key) => !knownKeys.has(key) && visibleFeatureSwitchKey(key));
  const groups = extraKeys.length
    ? [...featureGroups, { title: "其他", note: "来自配置但暂未归入固定分组的开关。", keys: extraKeys }]
    : featureGroups;
  const overviewSettings = state.overview?.settings || {};
  const intensity = state.overview?.proactive_intensity || {};
  const intensitySearchText = "主动强度预设 主动触达 proactive_intensity_preset 私聊主动 群聊唤醒 群主动插话";
  const intensityVisible = !filter || intensitySearchText.toLowerCase().includes(filter);
  const visibleDraftKeys = visibleTopLevelFeatureKeys(state.featureDraft || {});
  const total = visibleDraftKeys.length;
  const enabled = visibleDraftKeys.filter((key) => toBool(state.featureDraft[key])).length;
  const proactiveLocked = visibleDraftKeys.filter((key) => featureLockedByProactiveOnlyMode(key)).length;
  const riskyEnabled = ["enable_group_interjection", "enable_bilibili_boredom_watch", isPrivateReadingAvailable() ? "enable_private_reading_boredom_read" : "", isPrivateReadingAvailable() ? "enable_private_reading_ask_recommendation" : "", "enable_unanswered_screen_peek_followup"]
    .filter((key) => toBool(state.featureDraft[key])).length;
  const activeSafeFeatureKeys = safeFeatureKeys.filter((key) => !featureLockedByProactiveOnlyMode(key));
  $("#featureSwitchSummary").innerHTML = `
    <section class="feature-summary-card ok">
      <span>已开启</span>
      <b>${escapeHtml(enabled)} / ${escapeHtml(total)}</b>
      <small>当前主开关</small>
    </section>
    <section class="feature-summary-card">
      <span>基础安全项</span>
      <b>${escapeHtml(activeSafeFeatureKeys.filter((key) => toBool(state.featureDraft[key])).length)} / ${escapeHtml(activeSafeFeatureKeys.length)}</b>
      <small>${escapeHtml(proactiveOnlyModeEnabled() ? "已排除模式锁定项" : "隐私、记忆、回复稳定性")}</small>
    </section>
    <section class="feature-summary-card ${riskyEnabled ? "warn" : ""}">
      <span>${escapeHtml(proactiveOnlyModeEnabled() ? "模式锁定" : "高主动子项")}</span>
      <b>${escapeHtml(proactiveOnlyModeEnabled() ? proactiveLocked : riskyEnabled)}</b>
      <small>${escapeHtml(proactiveOnlyModeEnabled() ? "被动能力已关闭" : "含详情页子开关")}</small>
    </section>
  `;

  if (embeddedFeatureParentByKey[state.selectedFeatureKey]) {
    state.selectedFeatureKey = embeddedFeatureParentByKey[state.selectedFeatureKey];
  }
  if (state.selectedFeatureKey && state.selectedFeatureKey !== "enable_proactive_only_mode" && !visibleFeatureSwitchKey(state.selectedFeatureKey)) {
    state.selectedFeatureKey = "";
  }
  syncFeatureFooterAction();
  if (state.selectedFeatureKey && Object.prototype.hasOwnProperty.call(state.featureDraft, state.selectedFeatureKey)) {
    $("#featureFlags").innerHTML = featureDetailPage(state.selectedFeatureKey);
    bindFeatureDetailActions();
    return;
  }

  const board = groups.map((group) => {
    const visibleKeys = group.keys.filter((key) => {
      if (!visibleFeatureSwitchKey(key)) return false;
      if (!filter) return true;
      return featureSearchText(key).includes(filter);
    });
    const hasIntensityCard = group.title === "通用能力" && intensityVisible;
    if (!visibleKeys.length && !hasIntensityCard) return "";
    const groupEnabled = visibleKeys.filter((key) => toBool(state.featureDraft[key])).length;
    const groupMeta = visibleKeys.length ? `${groupEnabled} / ${visibleKeys.length}` : "设置项";
    const prefix = hasIntensityCard
      ? proactiveIntensityCommonSettingCard(overviewSettings, intensity)
      : "";
    return `
      <section class="feature-switch-group">
        <header>
          <div>
            <b>${escapeHtml(group.title)}</b>
          </div>
          <small>${escapeHtml(groupMeta)}</small>
        </header>
        <div class="feature-switch-list">
          ${prefix}
          ${visibleKeys.map((key) => featureSwitchItem(key)).join("")}
        </div>
      </section>
    `;
  }).filter(Boolean).join("");
  $("#featureFlags").innerHTML = board || `<div class="empty small">没有匹配的功能开关</div>`;
  document.querySelectorAll("[data-feature-key]").forEach((input) => {
    input.addEventListener("change", () => {
      state.featureDraft[input.dataset.featureKey] = input.checked;
      renderFeatureSwitches();
    });
  });
  document.querySelectorAll("[data-feature-open]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedFeatureKey = button.dataset.featureOpen || "";
      renderFeatureSwitches();
    });
  });
  bindProactiveOnlyTempUnlockActions($("#featureFlags"));
  bindProactiveIntensityCommonSetting();
}

function proactiveIntensityCommonSettingCard(settings = {}, intensity = {}) {
  const current = String(settings.proactive_intensity_preset || intensity.preset || "off");
  const enabled = Boolean(intensity?.enabled);
  const effective = intensity?.effective || {};
  const detail = enabled
    ? `当前有效：私聊 ${effective.max_daily_messages ?? "-"} 条/天，空闲 ${effective.idle_minutes ?? "-"} 分钟，群唤醒冷却 ${effective.group_wakeup_cooldown_seconds ?? "-"} 秒，群插话间隔 ${effective.group_interject_min_interval_minutes ?? "-"} 分钟。`
    : "关闭时完全沿用手动参数，不改变私聊、群聊唤醒或群主动插话频率。";
  return `
    <section class="feature-switch-item feature-setting-inline ${enabled ? "on" : "off"}">
      <div class="feature-switch-body">
        <button type="button" class="feature-switch-text" data-jump-tab="troubleshooting">
          <b>主动强度预设</b>
          <small>proactive_intensity_preset</small>
        </button>
        <div class="feature-switch-meta">
          <span class="feature-state-text">${escapeHtml(enabled ? (intensity.label || current) : "关闭：手动参数")}</span>
          <span class="feature-lock-note">影响私聊主动、群聊唤醒和群主动插话；排障页会显示当前覆盖项。</span>
        </div>
        <div class="feature-lock-related">${escapeHtml(detail)}</div>
      </div>
      <form class="feature-inline-setting-form" data-proactive-intensity-form>
        <select name="proactive_intensity_preset">
          ${[
            ["off", "关闭：手动参数"],
            ["balanced", "标准偏主动"],
            ["high_private", "私聊高频"],
            ["high_group", "群聊活跃"],
            ["live", "在线陪伴"],
          ].map(([value, label]) => `<option value="${escapeHtml(value)}"${current === value ? " selected" : ""}>${escapeHtml(label)}</option>`).join("")}
        </select>
        <button type="submit">保存</button>
      </form>
    </section>
  `;
}

function bindProactiveIntensityCommonSetting() {
  document.querySelectorAll("[data-proactive-intensity-form]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const value = form.querySelector('[name="proactive_intensity_preset"]')?.value || "off";
      await runAction(
        () => postJson("/settings/update", { settings: { proactive_intensity_preset: value } }),
        "已保存主动强度预设",
        form.querySelector("button[type='submit']"),
      );
    });
  });
}

function syncFeatureFooterAction() {
  const button = $("#saveFeaturesBtn");
  if (!button) return;
  const inDetail = Boolean(state.selectedFeatureKey && Object.prototype.hasOwnProperty.call(state.featureDraft || {}, state.selectedFeatureKey));
  button.textContent = inDetail ? "返回功能列表" : "保存功能开关";
  button.dataset.action = inDetail ? "back" : "save";
}

function renderProactiveOnlyModeCard() {
  const root = $("#proactiveOnlyModeCard");
  if (!root) return;
  const key = "enable_proactive_only_mode";
  const checked = toBool(state.featureDraft[key]);
  const settings = state.overview?.settings || {};
  const injectionPosition = String(settings.passive_injection_position || "prompt");
  const frameworkLockMode = String(settings.framework_session_lock_mode || "auto");
  root.innerHTML = `
    <div class="proactive-mode-stack">
      <form class="proactive-mode-injection-card" data-proactive-injection-form>
        <div class="proactive-mode-copy">
          <div class="proactive-mode-kicker">被动注入</div>
          <h3>${escapeHtml(configLabel("passive_injection_position"))}</h3>
          <p>${escapeHtml(configDescriptions.passive_injection_position || "")}</p>
          <small class="proactive-mode-code">${escapeHtml("passive_injection_position")}</small>
        </div>
        <div class="proactive-mode-injection-control">
          <select name="passive_injection_position">
            ${[
              ["prompt", "当前请求末尾"],
              ["system_prompt", "系统提示词"],
              ["auto", "自动（缓存优先）"],
            ].map(([value, label]) => `<option value="${escapeHtml(value)}"${injectionPosition === value ? " selected" : ""}>${escapeHtml(label)}</option>`).join("")}
          </select>
          <div class="proactive-mode-actions">
            <button type="submit" class="proactive-mode-button primary">保存位置</button>
          </div>
        </div>
      </form>
      <div class="proactive-mode-settings-row">
        <section class="proactive-mode-card ${checked ? "on" : "off"}">
          <label class="feature-toggle-hit proactive-mode-toggle" aria-label="${escapeHtml(featureLabel(key))}">
            <input type="checkbox" data-proactive-only-mode-toggle ${checked ? "checked" : ""}>
            <span class="feature-toggle-visual"></span>
          </label>
          <div class="proactive-mode-main">
            <div class="proactive-mode-kicker">兼容与隔离</div>
            <h3>${escapeHtml(featureLabel(key))}</h3>
            <p>只让本插件负责主动私聊调度、生成和发送；普通私聊/群聊放行给 AstrBot 默认主链或其他插件。</p>
            <small class="proactive-mode-code">${escapeHtml(key)}</small>
          </div>
          <button type="button" class="proactive-mode-detail proactive-mode-button soft" data-feature-open="${escapeHtml(key)}">查看说明</button>
        </section>
        <form class="proactive-mode-injection-card" data-framework-lock-form>
        <div class="proactive-mode-copy">
          <div class="proactive-mode-kicker">兼容与隔离</div>
          <h3>${escapeHtml(configLabel("framework_session_lock_mode"))}</h3>
          <p>${escapeHtml(configDescriptions.framework_session_lock_mode || "")}</p>
          <small class="proactive-mode-code">${escapeHtml("framework_session_lock_mode")}</small>
        </div>
        <div class="proactive-mode-injection-control">
          <select name="framework_session_lock_mode">
            ${[
              ["auto", "自动（仅旧版兼容）"],
              ["off", "关闭（新版本推荐）"],
              ["always", "始终启用（旧版排障）"],
            ].map(([value, label]) => `<option value="${escapeHtml(value)}"${frameworkLockMode === value ? " selected" : ""}>${escapeHtml(label)}</option>`).join("")}
          </select>
          <div class="proactive-mode-actions">
            <button type="submit" class="proactive-mode-button primary">保存模式</button>
          </div>
        </div>
      </form>
      </div>
    </div>
  `;
  root.querySelector("[data-proactive-only-mode-toggle]")?.addEventListener("change", (event) => {
    state.featureDraft[key] = Boolean(event.target.checked);
    renderFeatureSwitches();
  });
  root.querySelector("[data-feature-open]")?.addEventListener("click", () => {
    state.selectedFeatureKey = key;
    renderFeatureSwitches();
  });
  root.querySelector("[data-proactive-injection-form]")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const value = form.querySelector('[name="passive_injection_position"]')?.value || "prompt";
    await runAction(
      () => postJson("/settings/update", { settings: { passive_injection_position: value } }),
      "已保存动态提示词注入位置",
      form.querySelector("button[type='submit']"),
    );
  });
  root.querySelector("[data-framework-lock-form]")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const value = form.querySelector('[name="framework_session_lock_mode"]')?.value || "auto";
    await runAction(
      () => postJson("/settings/update", { settings: { framework_session_lock_mode: value } }),
      "已保存主链会话锁兼容模式",
      form.querySelector("button[type='submit']"),
    );
  });
}

function featureSwitchItem(key) {
  const checked = toBool(state.featureDraft[key]);
  const locked = featureLockedByProactiveOnlyMode(key);
  const tempUnlocked = featureTemporarilyUnlockedByProactiveOnly(key);
  const displayOn = checked || tempUnlocked;
  const related = proactiveOnlyRelatedUnlocks(key);
  const stateText = locked ? (tempUnlocked ? "临时放行" : "已锁定") : checked ? "开启" : "关闭";
  const lockNote = tempUnlocked ? "已临时放行，关闭仅保留主动能力后清空" : "仅保留主动能力中，原配置保留";
  const relatedText = related.length ? `建议同步：${related.map((item) => item.label || item.key).join("、")}` : "";
  return `
    <section class="feature-switch-item ${displayOn ? "on" : "off"} ${locked ? "locked" : ""}" title="${escapeHtml(locked ? "仅保留主动能力开启时，本功能在本插件普通被动链路中被跳过，原配置会保留。" : featureDescription(key))}">
      <label class="feature-toggle-hit" aria-label="${escapeHtml(featureLabel(key))}">
        <input type="checkbox" data-feature-key="${escapeHtml(key)}" ${displayOn ? "checked" : ""} ${locked ? "disabled" : ""}>
        <span class="feature-toggle-visual"></span>
      </label>
      <div class="feature-switch-body">
        <button type="button" class="feature-switch-text" data-feature-open="${escapeHtml(key)}">
          <b>${escapeHtml(featureLabel(key))}</b>
          <small>${escapeHtml(key)}</small>
        </button>
        <div class="feature-switch-meta">
          <span class="feature-state-text">${escapeHtml(stateText)}</span>
          ${locked ? `<span class="feature-lock-note">${escapeHtml(lockNote)}</span>` : ""}
        </div>
        ${locked && relatedText ? `<div class="feature-lock-related">${escapeHtml(relatedText)}</div>` : ""}
      </div>
      ${locked ? `<button type="button" class="feature-temp-unlock-btn" data-proactive-temp-unlock="${escapeHtml(key)}" data-action="${tempUnlocked ? "clear" : "unlock"}">${escapeHtml(tempUnlocked ? "取消放行" : "临时放行")}</button>` : ""}
    </section>
  `;
}

function featureGroupForKey(key) {
  if (key === "enable_proactive_only_mode") return "兼容与隔离";
  const parentKey = topLevelFeatureKey(key);
  const group = featureGroups.find((item) => item.keys.includes(parentKey));
  return group ? group.title : "其他";
}

function featureRelatedSettings(key) {
  const settings = state.overview?.settings || {};
  const providers = state.overview?.providers || {};
  const keys = featureSettingGroups[key] || [];
  const fallbackValue = (name) => {
    const defaults = {
      inbound_message_debounce_seconds: 3,
      text_message_debounce_seconds: 8,
      image_message_debounce_seconds: 8,
      forward_message_debounce_seconds: 0,
      text_message_debounce_max_wait_seconds: 12,
      message_debounce_max_merge_messages: 8,
      enable_smart_message_debounce: false,
      SMART_MESSAGE_DEBOUNCE_PROVIDER_ID: "",
      enable_smart_silence: true,
      SMART_SILENCE_PROVIDER_ID: "",
      smart_silence_min_confidence: 0.66,
      smart_silence_model_timeout_seconds: 1.2,
      smart_message_debounce_model_timeout_seconds: 0.8,
      smart_message_debounce_wait_seconds: 3,
      smart_message_debounce_learning_window_seconds: 8,
      smart_message_debounce_examples_limit: 8,
      tts_generation_mode: "fast_tag",
      tts_voice_language: "ja",
      tts_conversion_provider_id: "",
      tts_extra_prompt: "",
      tts_frequency_control_mode: "global",
      tts_constraint_mode: "weak",
      tts_session_min_interval_seconds: 120,
      tts_private_min_interval_seconds: -1,
      tts_group_min_interval_seconds: -1,
      tts_trigger_probability: 25,
      tts_private_trigger_probability: -1,
      tts_group_trigger_probability: -1,
      enable_tts_local_playback: false,
      enable_tts_local_playback_live_only: false,
      tts_local_playback_volume: 35,
      tts_local_playback_min_interval_seconds: 0,
      enable_tts_live_subtitle_sync: false,
      tts_live_subtitle_url: "",
      auto_voice_enabled: true,
      auto_voice_full_conversion_enabled: true,
      auto_voice_max_chars: 80,
      auto_voice_probability: 20,
      auto_voice_cooldown_seconds: 180,
      main_user_voice_probability: 0,
      main_user_mention_voice_keywords: "",
      main_user_mention_voice_probability: 0,
      main_user_mention_voice_prompt: "",
    };
    return Object.prototype.hasOwnProperty.call(defaults, name) ? defaults[name] : undefined;
  };
  return keys
    .filter((item) => visibleConfigKey(item))
    .filter((item) => featureSettingVisibleForCurrentMode(key, item, settings))
    .filter((item) => (
      Object.prototype.hasOwnProperty.call(settings, item)
      || Object.prototype.hasOwnProperty.call(providers, item)
      || Object.prototype.hasOwnProperty.call(state.featureDraft || {}, item)
      || fallbackValue(item) !== undefined
    ))
    .map((item) => ({
      key: item,
      value: Object.prototype.hasOwnProperty.call(settings, item)
        ? settings[item]
        : Object.prototype.hasOwnProperty.call(providers, item)
          ? providers[item]
          : Object.prototype.hasOwnProperty.call(state.featureDraft || {}, item)
            ? state.featureDraft[item]
            : fallbackValue(item),
      feature: Object.prototype.hasOwnProperty.call(state.featureDraft || {}, item),
      description: configDescriptions[item] || featureDescription(item) || "这个参数会影响该功能的触发频率、上下文范围或行为边界。",
    }));
}

function featureSettingVisibleForCurrentMode(featureKey, settingKey, settings = state.overview?.settings || {}) {
  const boolSetting = (name) => {
    if (Object.prototype.hasOwnProperty.call(state.featureDraft || {}, name)) return Boolean(state.featureDraft[name]);
    return toBool(settings[name]);
  };
  const valueSetting = (name, fallback = "") => {
    if (Object.prototype.hasOwnProperty.call(state.featureDraft || {}, name)) return state.featureDraft[name];
    return Object.prototype.hasOwnProperty.call(settings, name) ? settings[name] : fallback;
  };
  if (featureKey === "enable_proactive_only_mode") {
    if (settingKey === "proactive_prompt_template") return boolSetting("enable_llm_proactive_message");
    if (["PROACTIVE_PERSONA_JUDGE_PROVIDER_ID", "proactive_persona_judge_send_threshold", "proactive_persona_judge_cache_minutes"].includes(settingKey)) {
      return boolSetting("enable_llm_proactive_persona_judge");
    }
    return true;
  }
  if (featureKey === "enable_humanized_states") {
    const restChildren = new Set(["rest_reply_mode", "rest_reply_probability", "rest_reply_llm_threshold", "rest_reply_active_windows", "rest_reply_awake_grace_minutes", "enable_rest_backlog_reply", "rest_backlog_max_messages", "REST_WAKEUP_PROVIDER_ID"]);
    if (settingKey === "enable_qq_custom_presence_sync") {
      return boolSetting("enable_qq_presence_sync");
    }
    if (restChildren.has(settingKey)) {
      const restEnabled = boolSetting("enable_rest_reply_simulation");
      if (!restEnabled) return false;
      if (settingKey === "rest_reply_probability") return String(valueSetting("rest_reply_mode", "probability")) === "probability";
      if (["rest_reply_llm_threshold", "REST_WAKEUP_PROVIDER_ID"].includes(settingKey)) return String(valueSetting("rest_reply_mode", "probability")) === "llm";
      if (settingKey === "rest_backlog_max_messages") return boolSetting("enable_rest_backlog_reply");
      return true;
    }
    return true;
  }
  if (featureKey === "enable_worldbook_member_recognition") {
    if (["worldbook_self_registration_block_words", "worldbook_self_registration_block_reply"].includes(settingKey)) {
      return boolSetting("worldbook_self_registration");
    }
    return true;
  }
  if (featureKey === "enable_message_debounce") {
    if (settingKey === "text_message_debounce_seconds" && boolSetting("enable_smart_message_debounce")) return false;
    return true;
  }
  if (featureKey === "enable_group_slang_learning") {
    if (settingKey === "enable_group_slang_web_search") return boolSetting("enable_group_slang_meanings");
    if (["group_slang_web_search_terms", "group_slang_web_search_results"].includes(settingKey)) {
      return boolSetting("enable_group_slang_meanings") && boolSetting("enable_group_slang_web_search");
    }
    return true;
  }
  if (featureKey === "enable_group_wakeup_enhancement") {
    if (settingKey === "group_wakeup_question_threshold") {
      return boolSetting("enable_group_wakeup_question");
    }
    if (["group_wakeup_cold_group_threshold", "group_wakeup_cold_group_idle_minutes"].includes(settingKey)) {
      return boolSetting("enable_group_wakeup_cold_group");
    }
    if ([
      "group_high_intensity_wakeup_window_seconds",
      "group_high_intensity_wakeup_threshold",
      "group_high_intensity_cooldown_seconds",
      "group_high_intensity_merge_seconds",
      "group_high_intensity_max_merge_messages",
      "group_high_intensity_merge_scope",
    ].includes(settingKey)) {
      return boolSetting("enable_group_high_intensity_mode");
    }
    return true;
  }
  if (featureKey === "enable_response_self_review") {
    if (settingKey === "response_review_max_chars") {
      return String(valueSetting("response_review_mode", "severe_only")) === "full";
    }
    return true;
  }
  if (featureKey === "enable_photo_text_action") {
    if (settingKey === "daily_outfit_photo_prompt") return boolSetting("enable_daily_outfit_photo");
    if (["natural_language_photo_generation_max_daily", "natural_language_photo_extra_prompt"].includes(settingKey)) return boolSetting("enable_natural_language_photo_generation");
    return true;
  }
  if (featureKey === "enable_emotion_simulation") {
    if (settingKey === "emotion_judgement_mode") return boolSetting("enable_llm_emotion_judgement");
    if (settingKey === "EMOTION_JUDGEMENT_PROVIDER_ID") return boolSetting("enable_llm_emotion_judgement") && String(valueSetting("emotion_judgement_mode", "suspicious")) !== "off";
    return true;
  }
  if (featureKey !== "enable_tts_enhancement") return true;
  const mode = String(settings.tts_frequency_control_mode || "global");
  const rawGenerationMode = String(settings.tts_generation_mode || "fast_tag").toLowerCase();
  const generationMode = {
    hybrid: "fast_tag",
    direct: "fast_tag",
    tag: "fast_tag",
    fast: "fast_tag",
    convert: "postprocess",
    post: "postprocess",
    llm: "postprocess",
  }[rawGenerationMode] || rawGenerationMode;
  const globalOnly = new Set([
    "tts_constraint_mode",
    "tts_session_min_interval_seconds",
    "tts_private_min_interval_seconds",
    "tts_group_min_interval_seconds",
    "tts_trigger_probability",
    "tts_private_trigger_probability",
    "tts_group_trigger_probability",
  ]);
  const legacyOnly = new Set([
    "auto_voice_probability",
    "auto_voice_cooldown_seconds",
    "main_user_voice_probability",
    "main_user_mention_voice_keywords",
    "main_user_mention_voice_probability",
    "main_user_mention_voice_prompt",
  ]);
  const fastTagOnly = new Set([
    "tts_constraint_mode",
    "auto_voice_enabled",
    "auto_voice_full_conversion_enabled",
    "auto_voice_max_chars",
    "auto_voice_probability",
    "auto_voice_cooldown_seconds",
    "main_user_voice_probability",
    "main_user_mention_voice_keywords",
    "main_user_mention_voice_probability",
    "main_user_mention_voice_prompt",
  ]);
  const autoVoiceChildren = new Set([
    "auto_voice_full_conversion_enabled",
    "auto_voice_max_chars",
  ]);
  const legacyAutoVoiceChildren = new Set([
    "auto_voice_probability",
    "auto_voice_cooldown_seconds",
    "main_user_voice_probability",
    "main_user_mention_voice_keywords",
    "main_user_mention_voice_probability",
    "main_user_mention_voice_prompt",
  ]);
  const localPlaybackChildren = new Set([
    "enable_tts_local_playback_live_only",
    "tts_local_playback_volume",
    "tts_local_playback_min_interval_seconds",
  ]);
  const liveSubtitleChildren = new Set([
    "tts_live_subtitle_url",
  ]);
  if (generationMode === "postprocess" && fastTagOnly.has(settingKey)) return false;
  if (autoVoiceChildren.has(settingKey) && !boolSetting("auto_voice_enabled")) return false;
  if (legacyAutoVoiceChildren.has(settingKey) && !boolSetting("auto_voice_enabled")) return false;
  if (localPlaybackChildren.has(settingKey) && !boolSetting("enable_tts_local_playback")) return false;
  if (liveSubtitleChildren.has(settingKey) && !boolSetting("enable_tts_live_subtitle_sync")) return false;
  if (mode === "legacy") return !globalOnly.has(settingKey);
  return !legacyOnly.has(settingKey);
}

function featureSettingInputType(key, value) {
  if (featureSettingTypes[key]) return featureSettingTypes[key];
  if (typeof value === "boolean") return { type: "checkbox" };
  if (typeof value === "number") return { type: "number" };
  if (Array.isArray(value)) return { type: "textarea" };
  return { type: "text" };
}

function featureSettingInput(key, value) {
  const spec = featureSettingInputType(key, value);
  const safeKey = escapeHtml(key);
  const disabled = featureLockedByProactiveOnlyMode(key);
  const disabledAttr = disabled ? " disabled" : "";
  if (spec.type === "checkbox") {
    const checked = toBool(value);
    return `
      <label class="feature-param-check">
        <input type="checkbox" data-feature-param="${safeKey}" ${checked ? "checked" : ""}${disabledAttr}>
        <span>${escapeHtml(disabled ? "已锁定" : checked ? "开启" : "关闭")}</span>
      </label>
    `;
  }
  if (spec.type === "select") {
    return `
      <select data-feature-param="${safeKey}"${disabledAttr}>
        ${(spec.options || []).map(([optionValue, label]) => `
          <option value="${escapeHtml(optionValue)}"${String(value ?? "") === String(optionValue) ? " selected" : ""}>${escapeHtml(label)}</option>
        `).join("")}
      </select>
    `;
  }
  if (spec.type === "provider") {
    return featureProviderSelect(key, value);
  }
  if (spec.type === "textarea") {
    return `<textarea data-feature-param="${safeKey}" rows="3"${disabledAttr}>${escapeHtml(Array.isArray(value) ? value.join("\n") : value ?? "")}</textarea>`;
  }
  const password = spec.type === "password";
  const numeric = spec.type === "number" || typeof value === "number";
  const percentInput = isPercentInputSetting(key);
  const step = percentInput ? (spec.step ?? "1") : (spec.step ?? (key === "skill_growth_rate" ? "0.01" : "any"));
  const min = percentInput ? (spec.min ?? "0") : (spec.min ?? "");
  const max = percentInput ? (spec.max ?? "100") : (spec.max ?? "");
  const displayValue = displaySettingValue(key, value);
  return `
    <input
      type="${password ? "password" : numeric ? "number" : "text"}"
      data-feature-param="${safeKey}"
      value="${escapeHtml(displayValue)}"
      ${numeric ? `step="${step}"` : ""}
      ${min ? `min="${min}"` : ""}
      ${max ? `max="${max}"` : ""}
      ${disabledAttr}
    />
  `;
}

function featureProviderSelect(key, value) {
  const current = String(value || "").trim();
  const known = state.availableProviders.some((item) => item.id === current);
  const customValue = current && !known ? current : "";
  const options = [
    `<option value="">留空自动回退</option>`,
    ...state.availableProviders.map((item) => {
      const label = `${item.name || item.id}${item.model ? ` · ${item.model}` : ""}${item.is_default ? " · 默认" : ""}`;
      return `<option value="${escapeHtml(item.id)}" ${item.id === current ? "selected" : ""}>${escapeHtml(label)}</option>`;
    }),
    `<option value="__custom__" ${customValue ? "selected" : ""}>手动输入 Provider ID</option>`,
  ].join("");
  return `
    <div class="feature-provider-select">
      <select data-feature-provider-select="${escapeHtml(key)}">${options}</select>
      <input data-feature-param="${escapeHtml(key)}" value="${escapeHtml(current)}" placeholder="自定义 Provider ID" ${customValue ? "" : "hidden"} />
    </div>
  `;
}

function syncFeatureProviderInput(select) {
  const key = select.dataset.featureProviderSelect;
  const input = document.querySelector(`[data-feature-param="${key}"]`);
  if (!input) return;
  if (select.value === "__custom__") {
    input.hidden = false;
    input.focus();
  } else {
    input.hidden = true;
    input.value = select.value;
  }
}

function collectFeatureDetailPayload(featureKey, root = document) {
  const features = { [featureKey]: toBool(state.featureDraft[featureKey]) };
  const settings = {};
  const providers = {};
  const overviewSettings = state.overview?.settings || {};
  root.querySelectorAll("[data-feature-param]").forEach((input) => {
    const key = input.dataset.featureParam;
    if (!key) return;
    const value = collectSettingValue(key, input);
    if (isProviderConfigKey(key)) {
      providers[key] = String(value || "").trim();
      state.providerDraft = { ...(state.providerDraft || {}), [key]: providers[key] };
    } else if (Object.prototype.hasOwnProperty.call(overviewSettings, key)) {
      settings[key] = value;
      if (Object.prototype.hasOwnProperty.call(state.featureDraft || {}, key)) {
        state.featureDraft[key] = toBool(value);
      }
    } else if (Object.prototype.hasOwnProperty.call(state.featureDraft || {}, key)) {
      features[key] = toBool(value);
      state.featureDraft[key] = toBool(value);
    } else {
      settings[key] = value;
    }
  });
  return { features, settings, providers };
}

async function saveCurrentFeatureDetail(control = null, successMessage = "已保存功能参数") {
  const featureKey = state.selectedFeatureKey || "";
  if (!featureKey) return true;
  const form = Array.from(document.querySelectorAll("[data-feature-param-form]"))
    .find((item) => item.dataset.featureParamForm === featureKey);
  if (!form) return true;
  const payload = collectFeatureDetailPayload(featureKey, form);
  const result = await runAction(
    () => postJson("/settings/update", payload),
    successMessage,
    control || form.querySelector(".feature-param-save"),
  );
  return Boolean(result);
}

function featureDependencyLines(key) {
  const dependencies = [];
  if (featureLockedByProactiveOnlyMode(key)) dependencies.push(["仅保留主动能力", "普通被动链路中此功能被本插件跳过，原配置保留；关闭仅保留主动能力后生效。"]);
  if (key === "enable_proactive_only_mode") dependencies.push(["注意", "只跳过本插件的普通被动增强，不会阻止默认回复或其他插件"]);
  if (key !== "enable_group_companion" && key.startsWith("enable_group_")) dependencies.push(["依赖", "群聊总开关"]);
  if (key === "enable_group_conversation_followup") dependencies.push(["依赖", "群聊场景感知"]);
  if (["enable_companion_memory", "enable_expression_learning", "enable_intent_emotion_analysis", "enable_response_self_review", "enable_smart_silence", "enable_passive_topic_suppression", "enable_relationship_state_machine", "enable_emotion_simulation", "enable_dialogue_episode_memory", "enable_open_loop_tracking", "enable_food_menu_recommendation"].includes(key)) {
    dependencies.push(["依赖", "私聊互动策略"]);
  }
  if (["enable_bilibili_boredom_watch"].includes(key)) dependencies.push(["依赖", "B 站能力可用"]);
  if (["enable_web_exploration", "enable_web_exploration_boredom_search"].includes(key)) dependencies.push(["依赖", "AstrBot 网页搜索"]);
  if (["enable_qzone_life_publish", "enable_qzone_comment_inbox"].includes(key)) dependencies.push(["依赖", "QQ 空间动态层"]);
  if (key === "enable_qzone_generated_image_publish") dependencies.push(["依赖", "QQ 空间动态层 + 主动拍照/生图"]);
  if (key === "enable_qzone_emotional_vent_publish") dependencies.push(["依赖", "情绪模拟 + QQ 空间动态层"]);
  if (key === "enable_photo_text_action") dependencies.push(["依赖", "ComfyUI、SDGen 或在线图片 API"]);
  if (key === "enable_tts_enhancement") dependencies.push(["依赖", "当前会话 TTS provider"]);
  if (key === "enable_yesterday_screen_diary_context") dependencies.push(["依赖", "screen_companion 昨日观察日记"]);
  if (key.startsWith("enable_private_reading_")) dependencies.push(["依赖", "素材能力可用"]);
  if (key === "enable_private_image_self_recognition") dependencies.push(["依赖", "AstrBot 默认图片转述模型 / 插件识图模型"]);
  if (["enable_group_interjection", "enable_bilibili_boredom_watch", "enable_news_boredom_read", "enable_web_exploration_boredom_search", "enable_private_reading_boredom_read", "enable_private_reading_ask_recommendation", "enable_unanswered_screen_peek_followup"].includes(key)) {
    dependencies.push(["注意", "高主动项"]);
  }
  return dependencies;
}

const featureDetailGuides = {
  enable_proactive_only_mode: {
    summary: "让本插件只负责主动来找用户的链路，并放行普通聊天给 AstrBot 默认主链或其他插件。",
    trigger: "普通私聊、群聊事件和非主动框架 LLM 请求到达时生效。",
    enabled: "插件仍会跑日程、状态、主动意愿和私聊主动发送；普通聊天不会注入本插件状态、TTS、图片/转发摘要或群聊上下文，也不会使用插件工具。插件不会拦截默认回复或其他插件处理。用户回复主动消息仍会被轻量记录为已回应。",
    disabled: "按各功能开关正常参与私聊被动增强、群聊观察、图片/转发处理和提示词注入。",
  },
  enable_mai_style_integration: {
    summary: "把插件整理出的相处分寸、偏好和说话风格放进普通回复里，是私聊回复质感增强的核心入口。",
    trigger: "每次 Bot 正常回复前生效。",
    enabled: "回复会参考相处分寸、互动偏好、表达习惯和必要时的本轮接话策略；不在这里重复写身份事实。",
    disabled: "其他学习内容仍可记录，但主回复更接近 AstrBot 原本的普通回复。",
  },
  enable_companion_memory: {
    summary: "从长期互动里整理稳定画像，例如用户偏好、边界、称呼、重要事实和相处习惯。",
    trigger: "私聊积累到整理间隔或消息阈值时低频执行。",
    enabled: "Bot 后续更容易记得你是谁、喜欢什么、不喜欢什么。",
    disabled: "不会新增长期画像，已有画像仍可在页面中查看和管理。",
  },
  enable_expression_learning: {
    summary: "统计用户常用句长、标点、句尾味道和短句节奏，让回复更像同一段聊天里的自然接话。",
    trigger: "每次私聊文本到达时本地更新统计，不调用模型。",
    enabled: "Bot 只会参考节奏和语气轻重，不会把样本当成称呼规则、身份事实或长期偏好；激进模式可搭配手动审核。",
    disabled: "不会继续更新表达节奏统计，回复更接近 AstrBot 默认人格的原始表达。",
  },
  enable_expression_manual_review: {
    summary: "把新表达样本先放进待审核池，避免激进学习把噪音、群友口癖或复制内容直接写入画像。",
    trigger: "私聊文本到达并通过本地过滤后。",
    enabled: "用户详情里会出现待审核表达样本，通过后才会用于表达注入。",
    disabled: "通过本地过滤的样本会直接进入表达画像。",
  },
  enable_expression_style_review: {
    summary: "回复发送前检查表达学习是否过头，重点处理异常逗号、奇怪断句、照抄用户样本和提示词泄露。",
    trigger: "被动回复自检阶段；主动消息仍走主动发送前复核。",
    enabled: "命中表达风险时最多调用一次复核模型改写，不会反复循环。",
    disabled: "只保留普通回复复核，不额外检查表达学习污染。",
  },
  enable_tts_enhancement: {
    summary: "支持聊天文本保留中文、<tts> 内生成外语语音，并把 TTS 生成路径、标签规范化、语种控制、语音后中文释义和发送前朗读文本清洗统一收口处理。",
    trigger: "LLM 请求、LLM 回复和发送前都会参与；快速标签与后处理两条路径行为不同。",
    enabled: "可处理标准或错拼 <tts> 标签，外语语音块缺少中文含义时会自动补一句，并按配置把纯文本短回复转换为语音。",
    disabled: "只保留本插件原有主动 voice 行为，不额外改写普通回复。",
  },
  enable_intent_emotion_analysis: {
    summary: "用本地规则快速判断这句话更像求助、低落、玩笑、亲近还是边界，并给出置信度。",
    trigger: "每次私聊文本进入时本地执行，不调用模型。",
    enabled: "高置信度结果会影响本轮策略；低置信度只记录在排障信息里，不硬注入提示词。",
    disabled: "不再注入本轮意图策略；情绪模拟和关系距离感仍可基于自身开关使用轻量状态。",
  },
  enable_response_self_review: {
    summary: "主动消息发送前统一做价值复核和轻量润色；被动回复保留防漏、防复读和智能沉默等发送前保护。",
    trigger: "主动消息生成后、发送前；普通被动回复只在严重风险、用户明确边界或 full 模式下进入额外处理。",
    enabled: "主动消息会在发送前判断原样发送、轻改写、延后或取消；用户明确不想继续话题时，可由智能沉默小模型决定是否直接不发。",
    disabled: "不再调用模型润色主动消息；本地仍会尽量丢弃明显错误的主动消息。",
  },
  enable_smart_silence: {
    summary: "用户说别聊了、别问了、换个话题或不用回复时，不再硬接一句“那就结束这个话题”，而是让小模型判断是否该安静退开。",
    trigger: "仅在本地快判命中疑似话题边界后调用小模型；普通聊天不会额外消耗。",
    enabled: "小模型判定 silent 且达到置信度阈值时，本轮待发送回复会被直接取消，不写入上次回复记忆。",
    disabled: "遇到这类边界表达时仍按普通主链回复处理。",
  },
  enable_passive_topic_suppression: {
    summary: "记录最近被动回复主题，限制短时间内反复把同类话题带回聊天，避免像卡在一个话题上。",
    trigger: "私聊回复生成后和下一轮回复审校时。",
    enabled: "相似话题会被标记为重复；被动 full 模式下可能参与轻微改写，默认只做本地抑制。",
    disabled: "Bot 可能更频繁重复刚提过的内容或相似收尾。",
  },
  enable_relationship_state_machine: {
    summary: "维护 Bot 和用户之间的距离感，让回复和主动行为随亲近、冷淡、边界、长期未联系和主动消息回应情况自然变化。",
    trigger: "私聊互动、用户表达边界、亲近或冷淡、长期未联系、主动消息持续未回应或重要事件后。",
    enabled: "Bot 会更注意什么时候靠近、什么时候退一步；如果对方持续不接主动消息，会逐渐放轻并拉长主动间隔。",
    disabled: "关系更像静态设定，距离变化和边界收敛会弱一些。",
  },
  enable_emotion_simulation: {
    summary: "维护 Bot 自身的短期情绪余波，例如被刺到后的收敛、慢慢缓和和不满时的短暂回避。",
    trigger: "私聊出现伤害性表达、道歉、安抚、夸奖或亲密互动后；不强依赖意图画像开关。",
    enabled: "Bot 会在情绪余波较重时短期收敛、暂停主动贴近；可选启用 QQ 空间公开心情动态。",
    disabled: "Bot 不维护自身情绪余波，关系距离感仍可处理边界和相处分寸。",
  },
  enable_dialogue_episode_memory: {
    summary: "把连续私聊整理成“共同经历”片段，之后只择要使用最近或相关片段来保持连续感。",
    trigger: "私聊达到消息数或时间整理阈值。",
    enabled: "Bot 能自然接回近期共同经历，而不是每次都像重新开始。",
    disabled: "不会新增私聊片段，长期连续感会降低。",
  },
  enable_open_loop_tracking: {
    summary: "主要从私聊片段里整理那些还留着、之后可能会回头接的事，普通回复里会并入近期共同经历。",
    trigger: "片段整理发现未完成事项，或用户明确说“提醒我 / 记住 / 别忘了”时。",
    enabled: "Bot 后续能自然接回没说完的事，但不会把它当待办清单塞进回复。",
    disabled: "这些未完话头不会被专门维护。",
  },
  enable_user_habit_learning: {
    summary: "学习用户常在什么时间做什么、问什么或处于什么状态；被动回复只在当前时段且话题相关时轻量使用。",
    trigger: "用户长期重复出现相似时段行为时。",
    enabled: "Bot 被动聊天时只用相关习惯帮助理解；主动消息可以在接近习惯时段时轻轻关心。",
    disabled: "Bot 仍按当下聊天判断，不会积累时段习惯。",
  },
  enable_food_menu_recommendation: {
    summary: "把常吃菜、菜馆和外卖放进一个小候选池。用户纠结吃什么时，Bot 只拿少量贴合项作参考。",
    trigger: "私聊里明确问吃什么、点什么、外卖、夜宵、午饭或晚饭这类选择时。",
    enabled: "回复会参考候选池里最贴合的 1-3 项；不会在普通聊天里主动报菜单。",
    disabled: "候选仍可在这里管理，但不会进入普通回复参考。",
  },
  enable_humanized_states: {
    summary: "生成精力、睡眠、梦境、健康、饥饿和周期等当前扮演状态，让 Bot 像有自己的身体节奏。",
    trigger: "日程生成、状态刷新、主动消息和被动回复注入时。",
    enabled: "当前扮演状态会影响日程、主动行为和被动回复的语气、长短、节奏。",
    disabled: "状态退化为较平稳的基础信息，拟人生活感会明显减少。",
  },
  enable_health_state: {
    summary: "控制健康、不舒服和恢复尾声这类身体余波是否参与当前扮演状态。",
    trigger: "拟人身体状态刷新或手动增添状态时；开启后不再额外做人格适用性拦截。",
    enabled: "状态可能出现轻微不适、头疼、恢复期等健康底色，并影响精力、日程和语气。",
    disabled: "不会新增健康异常状态；手动增添明显生病/不舒服状态也会被拦截。",
  },
  enable_hunger_state: {
    summary: "控制饥饿、胃口和想吃东西这类身体余波是否参与当前扮演状态。",
    trigger: "拟人身体状态刷新、饭点饥饿窗口或手动增添状态时；开启后不再额外做人格适用性拦截。",
    enabled: "状态可能出现饿、胃口不好、想吃甜的等底色，并可能产生吃什么类身体小需求。",
    disabled: "不会新增饥饿/胃口状态；吃什么类身体小需求和手动饥饿状态会被拦截。",
  },
  enable_segmented_proactive_reply: {
    summary: "把纯文本回复按自然聊天节奏拆成短句，并合并过短片段，避免刷屏或突兀附和。",
    trigger: "插件主动消息发送时；若作用范围设为全部 LLM，也会处理普通模型纯文本回复。",
    enabled: "符合条件的文本会按规则拆分，首段先发，剩余片段按自然间隔补发。",
    disabled: "符合场景的文本一次性发送完整内容。",
  },
  inject_passive_states: {
    summary: "普通被动聊天也注入“当前扮演状态”，只作为回复底色使用。",
    trigger: "私聊或允许的群聊回复前。",
    enabled: "回复会参考精力、情绪、睡眠、健康、饥饿、周期或叠加状态，但不汇报字段。",
    disabled: "状态主要影响主动行为，普通回复不一定体现状态。",
  },
  enable_passive_state_delta_injection: {
    summary: "把被动状态从“每轮完整注入”改成“同会话按变化注入”。",
    trigger: "私聊被动回复准备提示词时。",
    enabled: "首次、状态明显变化或用户问近况时注入短状态摘要；状态未变时不重复塞日程和生活背景，更利于缓存命中。",
    disabled: "恢复旧逻辑：每轮按轻量/完整模式注入当前状态和相关生活背景。",
  },
  passive_injection_position: {
    summary: "选择动态提示词注入到当前请求末尾还是系统提示词。",
    trigger: "被动状态注入或请求级环境感知生效时。",
    enabled: "当前请求末尾会把动态片段收进统一动态块，并按稳定顺序排列，更利于服务端缓存，也更适合与长期记忆/记忆召回在尾部结合；系统提示词约束更强；自动目前按缓存优先处理。",
    disabled: "不影响被动状态总开关，只决定注入位置。",
  },
  framework_session_lock_mode: {
    summary: "旧版 AstrBot 会话库并发锁兼容保护。",
    trigger: "同一会话可能同时进入多个主链请求时。",
    enabled: "auto 只在识别到受影响旧版本时启用；always 会强制排队，适合旧版仍遇到 database is locked 的用户；off 适合新版本。",
    disabled: "新版本默认不额外串行化，减少回复延迟。",
  },
  enable_cycle_state: {
    summary: "作为拟人身体状态的一部分，偶尔生成生理期前、处于生理期或生理期后的状态底色。",
    trigger: "拟人身体状态刷新时，且生理期模拟开关已开启。",
    enabled: "当前扮演状态可能出现生理期相关描述，并轻微影响精力、语气、长短和节奏；不会当成真实日期或医学记录追踪。",
    disabled: "不会新增生理期状态；已有状态会按持续时间自然结束，之后回到“不处于生理期”。",
  },
  enable_skill_growth_simulation: {
    summary: "为 Bot 模拟能力状态和成长过程，并让能力边界影响日程表现。",
    trigger: "日程包含学习、练习、创作或兴趣活动后。",
    enabled: "技能会从低等级慢慢成长，高等级技能不会再写出明显不符合能力的日程。",
    disabled: "技能页不再增长，日程不受能力状态约束。",
  },
  enable_message_debounce: {
    summary: "分别控制文本、图片和合并转发的补话等待，并限制持续补话的最长等待和合并条数。",
    trigger: "私聊或群聊消息进入回复链前。",
    enabled: "不同消息类型按各自秒数等待补充说明；智能文本收口开启后，完整文本本地快判放行，疑似半句才短等；达到最长等待或最大合并条数会立即收口。",
    disabled: "不等待用户补话，只保留重复上报去重。",
  },
  enable_smart_message_debounce: {
    summary: "先用本地规则快速放行完整文本，只把疑似半句话交给小模型短时确认，并把误判样本带给下次判断。",
    trigger: "目标私聊文本、群聊中明确对 Bot 说话的文本进入回复链前。",
    enabled: "疑似起手、转折、列举或停在半句话时会短等补充；完整问句、请求、贴贴和问候会直接回复。",
    disabled: "文本按固定补话等待秒数处理，不额外调用小模型判断。",
  },
  enable_recall_enhancement: {
    summary: "把 QQ/OneBot 撤回事件纳入插件上下文，提供发送前取消回复、短期撤回消息转述和违禁词自动撤回。",
    trigger: "普通消息进入事件流时缓存摘要；收到 friend_recall/group_recall 时记录撤回；发送前和分段补发前执行检查。",
    enabled: "Bot 不会在用户撤回触发消息后继续接旧话；授权用户可查看缓存期内的撤回内容；配置词表后可自动拦截或撤回命中消息。",
    disabled: "不记录撤回事件，不缓存撤回内容，也不执行撤回相关自动处理。",
  },
  enable_private_image_self_recognition: {
    summary: "为私聊单图、引用图片、合并转发图片和动态 GIF 提供短视觉摘要、表达意图和角色归属辅助判断。",
    trigger: "私聊图片进入视觉转述链路、引用图片被解析、合并转发含图片或动态 GIF 需要抽帧时。",
    enabled: "视觉摘要会结合当前角色名字、人设和自定义线索，并尽量区分“当前角色/用户/无关图片/无法判断”。",
    disabled: "不再额外做插件侧图片转述增强和角色自我识别；图片收口等待仍由“消息收口防抖”控制。",
  },
  enable_environment_perception: {
    summary: "提供当前时间、日期、平台、聊天类型和消息媒介，让日程与回复不脱离现实语境。",
    trigger: "日程生成、状态刷新和回复前。",
    enabled: "Bot 会知道现在大概是什么时间、在哪个平台、面对私聊还是群聊。仅保留主动能力开启时，本插件普通被动回复里的环境感知注入会被跳过；后台状态和主动链路仍可使用。",
    disabled: "只使用较基础的上下文，时间与场景贴合度下降。",
  },
  enable_holiday_perception: {
    summary: "识别节假日、周末、工作日和调休，影响日程强度与生活节奏。",
    trigger: "环境感知生成日期语境时。",
    enabled: "Bot 更容易在假日放松、工作日上课或安排事务。",
    disabled: "只按普通日期处理，不主动区分节假日。",
  },
  enable_platform_perception: {
    summary: "识别平台、私聊/群聊、群名群号和图片、语音、合并消息等媒介。",
    trigger: "每次收到消息时。",
    enabled: "Bot 会更清楚消息来自哪里、是什么类型。",
    disabled: "媒介信息减少，复杂消息的场景判断会变弱。",
  },
  enable_model_perception: {
    summary: "识别当前对话 LLM、图片转述视觉模型，以及可用的生图后端/图片模型。",
    trigger: "环境感知注入时。",
    enabled: "Bot 能知道当前文本模型、视觉转述模型，以及生图后端或在线图片模型的大致来源，遇到不同配置时更容易判断自己的能力边界。",
    disabled: "Bot 不再获得模型环境信息，只按普通对话上下文回复。",
  },
  enable_worldview_perception: {
    summary: "把插件能力、生活片段和聊天场景转换成当前人设世界观能自然理解的说法。",
    trigger: "完整被动回复的环境感知注入时。",
    enabled: "会额外注入世界观适配片段，例如把现实能力映射为奇幻/科幻/自定义世界里的表达。",
    disabled: "不再额外注入世界观适配，适合 AstrBot 人设里已经写了完整世界观的情况；默认关闭以避免重复。",
  },
  enable_lunar_perception: {
    summary: "在依赖可用时加入农历日期，用于节日、日记和生活氛围。",
    trigger: "环境感知刷新时。",
    enabled: "Bot 可以感知农历节日或农历日期。",
    disabled: "不再注入农历信息。",
  },
  enable_solar_term_perception: {
    summary: "注入当天或临近节气，让天气、饮食、日记和生活片段更贴合时令。",
    trigger: "环境感知刷新时。",
    enabled: "Bot 可能自然提到节气带来的生活感。",
    disabled: "不再主动参考节气。",
  },
  enable_almanac_perception: {
    summary: "生成轻量宜忌氛围标签，只用于表达参考，不作为硬规则。",
    trigger: "环境感知刷新时。",
    enabled: "日程和表达可能带一点当天氛围。",
    disabled: "关闭这部分玄学感，默认更现实。",
  },
  enable_yesterday_screen_diary_context: {
    summary: "读取 screen_companion 的昨日屏幕观察日记脱敏摘要，用来推断今天的状态、作息惯性和日程背景。",
    trigger: "每日生成日程、刷新状态或需要昨日屏幕背景时。",
    enabled: "Bot 会参考昨天的活动类型和节奏，但不会读取今天实时屏幕，也不应直说“我昨天看到你”。",
    disabled: "不再把昨日屏幕观察摘要注入状态和日程。已有 screen_companion 数据不会被删除。",
  },
  enable_group_companion: {
    summary: "群聊能力总入口，控制群观察、上下文、话题线、关系网和群主动行为是否运行。",
    trigger: "收到群聊消息或后台整理群聊记录时。",
    enabled: "群聊相关子功能才有运行基础。",
    disabled: "多数群聊观察、唤醒、插话和群资料更新都会停止。",
  },
  enable_group_context_injection: {
    summary: "群聊回复前注入群氛围、当前话题、相关成员和关系网信息。",
    trigger: "Bot 准备在群聊回复时。",
    enabled: "Bot 更容易知道刚才在聊什么、提到的是谁。",
    disabled: "群回复主要依赖原始消息，上下文感会弱。",
  },
  enable_group_injection_guard: {
    summary: "识别群里试图改称呼、改语气、改设定或改输出格式的注入话术，并阻断学习和后续再注入。",
    trigger: "群聊消息进入观察写入和群聊回复前。",
    enabled: "群内起哄或恶搞更难污染黑话、话题线、成员观察和后续 prompt。",
    disabled: "群聊里的改设定话术可能继续沉淀成上下文，长期更容易把 Bot 带偏。",
  },
  enable_group_persona_denoise: {
    summary: "群聊回复时降低人格外溢，减少私聊腔、状态汇报和私聊关系直出。",
    trigger: "Bot 准备在群聊回复时。",
    enabled: "回复更贴当前群话题，更少硬插话和自报状态。",
    disabled: "群聊会更完整吃到陪伴人格背景，但也更容易显得黏或跑偏。",
  },
  enable_forward_message_adaptation: {
    summary: "让 Bot 能阅读合并转发，把节点顺序、发言人、嵌套记录和图片摘要整理成可理解上下文。",
    trigger: "私聊或群聊里用户发送合并消息/聊天记录时。",
    enabled: "可选择直接注入摘要，或先用专门模型转述后再回复。",
    disabled: "合并消息只按 AstrBot 原始能力处理，理解能力可能不足。",
  },
  enable_group_scene_awareness: {
    summary: "判断群聊里的话是在叫 Bot、回应别人、普通闲聊还是提到了 Bot。",
    trigger: "群聊消息进入回复或唤醒判断前。",
    enabled: "减少 Bot 抢话，也能识别该接话的无 @ 场景。",
    disabled: "群聊指向判断更依赖硬规则。",
  },
  enable_group_reality_promise_guard: {
    summary: "限制群聊回复里的现实执行承诺，避免 Bot 说自己能实际拉人、修网、开房间或操作设备。",
    trigger: "群聊 LLM 回复生成前。",
    enabled: "Bot 会把现实执行请求改成提醒、建议或说明做不到实际操作。",
    disabled: "群聊也按人格自由扮演，不额外限制现实承诺。",
  },
  enable_group_wakeup_enhancement: {
    summary: "扩展群聊唤醒方式：强唤醒词直接叫到 Bot，弱相关词先判断语境，兴趣词按概率接话。",
    trigger: "群聊出现配置词、Bot 名字、关系网相关称呼或兴趣话题时。",
    enabled: "Bot 更像会被自然叫到，也会偶尔被感兴趣话题吸引。",
    disabled: "主要依赖 @、指令或 AstrBot 原本触发方式。",
  },
  group_wakeup_short_text_wait_seconds: {
    summary: "只针对 1-2 字的群聊短唤醒多等一小会儿，方便用户把后半句补上。",
    trigger: "群聊已判定在叫 Bot，且当前文本极短、没有完整标点、也不像“好/嗯/早”这类完整短互动时。",
    enabled: "Bot 会先等同一群友继续补充，再把多条内容合并成一轮理解，减少引用碎片消息和无关回复。",
    disabled: "短唤醒会按普通智能收口规则处理，可能更快，但更容易漏掉后续补话。",
  },
  enable_group_high_intensity_mode: {
    summary: "自动识别连续唤醒的热闹群聊，按配置把后续唤醒消息合并成一轮回复。",
    trigger: "统计窗口内多次 @、引用 Bot 或增强唤醒时。",
    enabled: "按合并等待秒数收口，减少多次 LLM 调用，并暂停弱相关/兴趣唤醒、群片段整理、黑话释义刷新和主动插话。",
    disabled: "群聊仍按普通收口、续接和后台刷新流程运行。",
  },
  enable_group_conversation_followup: {
    summary: "群里叫过 Bot 后，判断同一用户后续没 @ 的话是否仍然是在和 Bot 说。",
    trigger: "群聊中刚发生过 Bot 回复，随后同一用户继续发言时。",
    enabled: "Bot 不会因为用户忘记 @ 就马上断掉对话，但有轮数上限。",
    disabled: "无 @ 后续消息更容易被当作普通群聊。",
  },
  enable_group_slang_learning: {
    summary: "记录群内常见黑话、简称、梗和特殊称呼。",
    trigger: "群聊出现重复使用或上下文明显的特殊表达时。",
    enabled: "Bot 会逐渐看懂群内专属表达。",
    disabled: "不会继续新增黑话候选。",
  },
  enable_group_slang_meanings: {
    summary: "为已记录黑话生成简短释义，避免只存词不懂意思。",
    trigger: "群黑话达到解释条件时。",
    enabled: "群黑话页面会有更可读的解释，回复也能参考。",
    disabled: "只保留词本身，含义理解更依赖实时上下文。",
  },
  enable_group_member_profiles: {
    summary: "维护成员在当前群里的近期观察，例如发言次数、短句样本、活跃痕迹和改名记录。",
    trigger: "目标群成员持续发言时。",
    enabled: "Bot 会更懂这轮群聊里谁最近常出现、怎么说话，但不会把它当成稳定身份资料。",
    disabled: "群内成员观察不再自动更新。",
  },
  enable_group_topic_threads: {
    summary: "整理群聊一段时间内的主题线，而不是只截取单句。",
    trigger: "群聊持续讨论、话题转移或用户长时间未活跃后。",
    enabled: "转述群里有趣内容时更像摘要整个时间段。",
    disabled: "群聊理解更偏最近消息片段。",
  },
  enable_group_episode_memory: {
    summary: "把群聊阶段性事件整理成片段，供之后回忆、转述和关系更新使用。",
    trigger: "群消息累积到整理阈值时。",
    enabled: "Bot 能记住群里发生过的阶段性事件。",
    disabled: "不会新增群聊片段记忆。",
  },
  enable_group_relationship_graph: {
    summary: "记录群成员之间的近期互动边，例如常互相回复、玩梗、争论或一起出现。",
    trigger: "群聊里成员之间发生互动时。",
    enabled: "Bot 会更容易判断当前群里谁在接谁的话、谁和谁常一起玩梗。",
    disabled: "不再更新互动边；稳定身份资料仍由群聊关系网负责。",
  },
  enable_group_interjection: {
    summary: "允许 Bot 在群聊中不被直接叫到时主动插一句。",
    trigger: "群聊有合适话题、频率限制通过且人格愿意参与时。",
    enabled: "Bot 会低频主动参与群聊。",
    disabled: "Bot 基本只在被叫到、被 @ 或规则触发时回复。",
  },
  enable_group_repeat_follow: {
    summary: "群里同一句话复读超过阈值后，Bot 可能跟读一次或打断复读。",
    trigger: "检测到连续复读链时。",
    enabled: "跟读和打断概率会随复读持续共同上升，最多跟读一次。",
    disabled: "复读链不会触发 Bot 的特殊处理。",
  },
  enable_group_interjection_feedback: {
    summary: "记录群友对 Bot 主动插话的反应，用来调整后续插话倾向。",
    trigger: "Bot 群主动插话后，群友继续回应或冷场时。",
    enabled: "后续插话会更懂哪些群和话题适合参与。",
    disabled: "插话频率主要按固定参数控制。",
  },
  enable_group_privacy_guard: {
    summary: "防止 Bot 把私聊内容、敏感转述或不该公开的关系信息带到群里。",
    trigger: "群聊回复、转述和群主动分享前。",
    enabled: "会拦截或改写可能泄露隐私的内容。",
    disabled: "隐私保护更依赖主模型自身判断，不建议关闭。",
  },
  enable_worldbook_member_recognition: {
    summary: "用 QQ 号锚定成员稳定身份，保存称呼、关系备注、边界和重要记忆。",
    trigger: "群聊提到成员、@ 成员、自登记或转述解析时。",
    enabled: "Bot 能把别名、QQ 和关系节点对应起来；近期发言习惯仍由群内成员观察负责。",
    disabled: "身份识别主要依赖当前昵称和原始消息。",
  },
  enable_atrelay_tools: {
    summary: "提供转述、@ 群友、多目标提醒和延迟转述能力，并优先结合关系网解析对象。",
    trigger: "用户让 Bot 帮忙告诉、提醒、转发或等某人出现再说时。",
    enabled: "Bot 可按人格改写、敏感确认、解析对象并执行转述。",
    disabled: "这些转述工具不可用，Bot 只能文字建议用户自己说。",
  },
  enable_cross_user_memory_bridge: {
    summary: "让主人在私聊里询问 Bot 与某个用户或群聊的近期互动。",
    trigger: "主人问“你和某某聊了什么”“最近和某群互动怎样”“在群里说过什么”时。",
    enabled: "Bot 会读取对应会话的近期记录并整理成摘要；默认只允许主人用户查询。",
    disabled: "Bot 不会跨用户读取互动记录，只能基于当前会话和已注入记忆回答。",
  },
  enable_livingmemory_integration: {
    summary: "允许插件与 LivingMemory 长期记忆协同，按需调用外部记忆工具。",
    trigger: "回复或整理时需要更长期记忆支持。",
    enabled: "可减少重复存储，并让长期记忆链路更完整。",
    disabled: "插件只使用自身记忆结构，不调用 LivingMemory。",
  },
  enable_bilibili_integration: {
    summary: "接入 B 站相关能力，读取观看记录或视频信息作为 Bot 的生活见闻来源。",
    trigger: "后台长线行为或用户询问最近看了什么时。",
    enabled: "B 站子能力才可运行。",
    disabled: "不会读取或使用 B 站内容。",
  },
  enable_bilibili_boredom_watch: {
    summary: "Bot 无聊或空档时低频刷视频，并可能形成观看印象。",
    trigger: "日程空档、无聊状态或长线主动检查时。",
    enabled: "Bot 可以自己看视频，按人格决定是否分享。",
    disabled: "不会主动刷视频。",
  },
  enable_news_integration: {
    summary: "接入新闻源和热点源，让 Bot 获得近期时讯见闻。",
    trigger: "日程生成、每日热点读取或无聊看新闻时。",
    enabled: "新闻相关子能力才可运行。",
    disabled: "Bot 不会主动读取新闻或热点。",
  },
  enable_news_daily_hot_read: {
    summary: "每日读取热点候选，形成当天内部见闻，默认随日程生成或后台检查进行。",
    trigger: "每天首次日程生成或后台热点检查时。",
    enabled: "Bot 会有当天热点印象，但不一定主动分享。",
    disabled: "不会自动获取每日热点。",
  },
  enable_ai_daily_watch: {
    summary: "按配置时间读取 AI 日报/早报来源，默认 12:00 读黑鸦Heya早报，23:00 读橘鸦Juya日报。",
    trigger: "后台检查发现某个来源已到配置时间且今天尚未尝试时。",
    enabled: "到点后当天只尝试一次，优先读取文字版正文，再尝试字幕和视频公开信息。",
    disabled: "不会自动追踪 AI 日报/早报定时来源。",
  },
  enable_news_boredom_read: {
    summary: "Bot 空闲或无聊时低频看新闻，按人格判断是否提起。",
    trigger: "无聊状态、空档日程或长线主动检查时。",
    enabled: "Bot 可能自己读新闻并留下记录。",
    disabled: "只保留每日热点或用户主动要求搜索。",
  },
  enable_external_event_self_link: {
    summary: "新闻阅读或主动搜索完成后，先判断外界信息与 Bot 自己的模型、能力、兴趣、创作、日程或关系是否有关，再决定是否产生主动找用户分享的意愿。",
    trigger: "新闻 digest 生成后、主动搜索笔记生成后。",
    enabled: "Bot 不再只是随机分享新闻，而会带着“这件事和我有什么关系”的动机进入主动候选。",
    disabled: "新闻和搜索仍可记录，但主动分享只按普通分享概率，不做自我关联意愿判断。",
  },
  enable_web_exploration: {
    summary: "允许 Bot 按人格兴趣、最近话题和日程自行选择搜索主题，并记录浏览痕迹。",
    trigger: "用户要求搜索、Bot 主动探索或工具搜索完成后。",
    enabled: "搜索记录会进入浏览记录页，Bot 可形成探索笔记。",
    disabled: "不会主动探索，用户显式搜索也不纳入该长线能力。",
  },
  enable_web_exploration_boredom_search: {
    summary: "Bot 无聊或空档时自己决定想了解什么，再调用 AstrBot 网页搜索。",
    trigger: "日程空档、心情无聊或长线主动检查时。",
    enabled: "Bot 会低频学习新鲜事物并留痕。",
    disabled: "主动搜索不会在空档自动发生。",
  },
  enable_qzone_integration: {
    summary: "启用内置 QQ 空间动态层，支持读取动态、发布说说、点赞和评论。",
    trigger: "用户测试 QQ 空间、Bot 读取动态或生活说说触发时。",
    enabled: "QQ 空间相关子能力才可使用。",
    disabled: "不会访问 QQ 空间。",
  },
  enable_qzone_life_publish: {
    summary: "根据日程、状态和日记余味，低频发布公开生活说说。",
    trigger: "满足间隔、概率和人格意愿时。",
    enabled: "Bot 可能把自己的生活片段发到空间。",
    disabled: "只读动态，不主动发布生活说说。",
  },
  enable_qzone_generated_image_publish: {
    summary: "给生活说说或情绪说说追加可选配图，由主动生图能力生成。",
    trigger: "说说文本已准备发布，且满足配图概率时。",
    enabled: "发布前会尝试生成一张符合说说内容的图片；失败时回退纯文字，不阻断发布。",
    disabled: "空间说说只发文字或用户指令中明确提供的图片。",
  },
  enable_qzone_comment_inbox: {
    summary: "低频拉取自己最近说说详情，解析评论列表，记录已见评论，并按需公开追加回复。",
    trigger: "长线主动维护任务到达评论检查间隔时。",
    enabled: "首次开启只记录现有评论；后续新评论会先经过模型判断，再决定跳过或追加一句公开回复。",
    disabled: "不会自动读取或回复自己说说下的评论。",
  },
  enable_private_reading_integration: {
    summary: "启用书柜夹层的私下阅读素材入口；仅在检测到对应素材能力时显示相关配置。",
    trigger: "素材能力可用、书柜打开或私下阅读检查时。",
    enabled: "夹层可保存阅读素材、封面、页图、批注和读后感。",
    disabled: "私下阅读素材入口不运行。",
  },
  enable_private_reading_boredom_read: {
    summary: "Bot 空档或无聊时低频自己找短篇素材阅读，并把内容放入书柜夹层。",
    trigger: "无聊状态、阅读间隔满足且素材能力可用时。",
    enabled: "Bot 会阅读页图、生成批注和读后感。",
    disabled: "不会主动寻找和阅读素材。",
  },
  enable_private_reading_ask_recommendation: {
    summary: "Bot 无聊时可低频向用户征求阅读推荐，是否害羞或坦然由人格决定。",
    trigger: "素材能力可用、无聊且概率通过时。",
    enabled: "Bot 可能主动问用户有没有推荐。",
    disabled: "不会主动征求推荐。",
  },
  enable_private_reading_preference_influence: {
    summary: "把书柜夹层阅读评分沉淀成私密偏好画像，只在私聊里弱影响亲密互动和语气尺度。",
    trigger: "私聊回复前，且累计评分数达到配置阈值时。",
    enabled: "Bot 会更自然地参考用户稳定高分倾向，但不会说出评分来源或覆盖人格。",
    disabled: "评分仍用于后续素材挑选，但不注入私聊回复。",
  },
  enable_unanswered_screen_peek_followup: {
    summary: "Bot 主动发消息后用户长时间没回时，可窥屏看看用户是不是在忙。",
    trigger: "主动消息发出后超过配置分钟数且冷却通过。",
    enabled: "这类识屏不受普通日次数限制，但仍受冷却控制。",
    disabled: "用户不回时不会因此额外识屏。",
  },
  enable_proactive_quote_trigger_message: {
    summary: "回复或主动消息能追溯到触发消息时，自动带引用；可按场景拆分，并可跳过过短回复。",
    trigger: "群聊被 @、引用、唤醒、连续对话保持、群主动插话，或模型预约的私聊主动能追溯触发消息时。",
    enabled: "按子开关决定普通群回复、群主动插话和私聊主动是否引用。用户引用 Bot 旧消息追问时，可选择引用当前消息或被引用的旧消息。",
    disabled: "这些消息不主动附带引用，用户需要从上下文判断回复对象。",
  },
  enable_creative_writing: {
    summary: "Bot 会在闲暇时从生活小事、梦境、日记或阅读灵感中可选地写一点文本作品，并放入书柜。",
    trigger: "日程处于休息、摸鱼、读书、写字等空闲片段，且灵感概率命中时。",
    enabled: "每次只写一小段，不按小时产出，也不会一口气写完。",
    disabled: "不会新建或推进创作项目。",
  },
  creative_hidden_mode: {
    summary: "创作默认作为 Bot 自己的私下活动，只在合适节点或用户问近况时自然透露。",
    trigger: "创作达到节点、用户询问近况或分享概率通过时。",
    enabled: "Bot 不会频繁汇报创作进度，保留自己的创作者主动性。",
    disabled: "创作相关内容更容易被主动提起。",
  },
};

function featureDetailExplanation(key) {
  const guide = featureDetailGuides[key];
  if (guide?.summary) return guide.summary;
  if (key.startsWith("enable_group_")) return "群聊子能力。";
  if (key.startsWith("enable_private_reading_")) return "夹层阅读子能力。";
  if (key.startsWith("enable_bilibili_")) return "B 站子能力。";
  if (key.startsWith("enable_qzone_")) return "QQ 空间子能力。";
  return featureDescription(key);
}

function featureDetailGuideRows(key) {
  const guide = featureDetailGuides[key] || {};
  const rows = [
    ["何时生效", guide.trigger || "该功能对应场景触发时生效。"],
    ["开启后", guide.enabled || "相关能力会参与判断、注入或后台整理。"],
    ["关闭后", guide.disabled || "相关能力停止新增处理，已有数据通常仍可在页面查看。"],
  ];
  if (featureSwitchNotes[key]) {
    rows.push(["管理入口", featureSwitchNotes[key]]);
  }
  return rows;
}

function featureImpactLines(key) {
  const lines = [];
  const group = featureGroupForKey(key);
  lines.push(["模块", group]);
  if (["enable_humanized_states", "inject_passive_states", "enable_health_state", "enable_hunger_state", "enable_cycle_state", "enable_skill_growth_simulation"].includes(key)) {
    lines.push(["场景", "日程 / 状态 / 私聊 / 群聊"]);
  } else if (key === "enable_segmented_proactive_reply") {
    lines.push(["场景", "主动消息 / LLM 纯文本回复"]);
  } else if (key === "enable_message_debounce") {
    lines.push(["场景", "私聊 / 群聊 / 图片 / 合并转发"]);
  } else if (key === "enable_forward_message_adaptation") {
    lines.push(["场景", "私聊合并消息 / 群聊合并消息"]);
  } else if (key === "enable_proactive_quote_trigger_message") {
    lines.push(["场景", "群回复 / 群主动 / 私聊主动"]);
  } else if (key === "enable_tts_enhancement") {
    lines.push(["场景", "私聊 / 群聊 / 主动语音"]);
  } else if (key.startsWith("enable_group_") || key === "enable_atrelay_tools" || key === "enable_cross_user_memory_bridge" || key === "enable_worldbook_member_recognition") {
    lines.push(["场景", "群聊 / 转述 / 关系网"]);
  } else if (key === "enable_private_image_self_recognition") {
    lines.push(["场景", "私聊图片 / 引用图片 / 合并图片 / GIF"]);
  } else if (key === "enable_food_menu_recommendation") {
    lines.push(["场景", "私聊 / 吃饭选择"]);
  } else if (key.startsWith("enable_bilibili_") || key.startsWith("enable_news_") || key === "enable_external_event_self_link" || key.startsWith("enable_web_exploration") || key.startsWith("enable_qzone_") || key === "enable_photo_text_action" || key.startsWith("enable_private_reading_") || key === "enable_creative_writing" || key === "creative_hidden_mode") {
    lines.push(["场景", "长线主动"]);
  } else if (key.startsWith("enable_environment_") || key.includes("perception") || key === "enable_yesterday_screen_diary_context") {
    lines.push(["场景", key === "enable_yesterday_screen_diary_context" ? "日程 / 状态 / 屏幕日记" : "日程 / 状态 / 回复"]);
  } else {
    lines.push(["场景", "私聊陪伴"]);
  }
  return lines;
}

function configLabel(name) {
  const label = configLabels[name] || featureLabel(name) || name.replace(/^enable_/, "").replaceAll("_", " ");
  if (isPercentInputSetting(name) && !/[（(]%[）)]/.test(label)) {
    return `${label}（%）`;
  }
  return label;
}

function featureDetailPage(key) {
  const enabled = toBool(state.featureDraft[key]);
  const locked = featureLockedByProactiveOnlyMode(key);
  const tempUnlocked = featureTemporarilyUnlockedByProactiveOnly(key);
  const displayEnabled = enabled || tempUnlocked;
  const relatedUnlocks = proactiveOnlyRelatedUnlocks(key);
  const related = featureRelatedSettings(key);
  const relatedMap = Object.fromEntries(related.map((item) => [item.key, item]));
  const dependencies = featureDependencyLines(key);
  const impacts = featureImpactLines(key);
  const guideRows = featureDetailGuideRows(key)
    .map(([name, value]) => `<div><dt>${escapeHtml(name)}</dt><dd>${escapeHtml(value)}</dd></div>`)
    .join("");
  const settingRow = ({ key: name, value, description }) => `
      <section class="feature-param-row">
        <div class="feature-param-main">
          <header>
            <b>${escapeHtml(configLabel(name))}</b>
            <code>${escapeHtml(name)}</code>
          </header>
          <p>${escapeHtml(description)}</p>
        </div>
        <div class="feature-param-control">
          ${featureSettingInput(name, value)}
        </div>
      </section>
    `;
  const sections = featureSettingSections[key] || [];
  const renderedSectionKeys = new Set();
  const groupedRows = sections
    .map((section) => {
      const items = (section.keys || []).map((name) => relatedMap[name]).filter(Boolean);
      items.forEach((item) => renderedSectionKeys.add(item.key));
      if (!items.length) return "";
      return `
        <section class="feature-param-section">
          <header>
            <b>${escapeHtml(section.title || "参数")}</b>
            ${section.note ? `<span>${escapeHtml(section.note)}</span>` : ""}
          </header>
          ${items.map(settingRow).join("")}
        </section>
      `;
    })
    .join("");
  const ungroupedRows = related.filter((item) => !renderedSectionKeys.has(item.key)).map(settingRow).join("");
  const extraParamPanel = key === "enable_segmented_proactive_reply" ? segmentedPreviewPanelHtml() : "";
  const customFeaturePanel = key === "enable_food_menu_recommendation" ? foodMenuFeaturePanelHtml() : "";
  const settingsRows = related.length
    ? `${groupedRows}${ungroupedRows}`
    : `<div class="feature-param-empty">暂无关联参数</div>`;
  const showParamCard = key !== "enable_food_menu_recommendation" || related.length || extraParamPanel;
  const paramCardHtml = showParamCard ? `
        <article class="feature-detail-card feature-detail-card-params">
          <h3>关联参数</h3>
          <form class="feature-param-list" data-feature-param-form="${escapeHtml(key)}">
            ${settingsRows}
            ${extraParamPanel}
            ${related.length ? `<button type="submit" class="feature-param-save">保存关联参数</button>` : ""}
          </form>
        </article>
      ` : "";
  const dependencyRows = dependencies.length
    ? dependencies.map(([name, value]) => `<div><dt>${escapeHtml(name)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")
    : `<div><dt>-</dt><dd>无额外依赖</dd></div>`;
  const impactRows = impacts.map(([name, value]) => `<div><dt>${escapeHtml(name)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("");
  return `
    <section class="feature-detail-page ${displayEnabled ? "on" : "off"} ${locked ? "locked" : ""}">
      <nav class="feature-detail-breadcrumb">
        <button type="button" data-feature-back>功能开关</button>
        <span>/ ${escapeHtml(featureGroupForKey(key))}</span>
      </nav>
      <div class="feature-state-strip ${displayEnabled ? "on" : "off"}">
        <b>${escapeHtml(locked ? (tempUnlocked ? "临时放行" : "已锁定") : enabled ? "开启" : "关闭")}</b>
        ${locked ? `<span>${escapeHtml(tempUnlocked ? "仅保留主动能力仍开启，但此功能已被临时放行；关闭后放行项会清空。" : "当前处于仅保留主动能力模式；保存的原始开关值不会被修改。")}</span>` : ""}
      </div>
      <header class="feature-detail-head">
        <div>
          <span class="module-badge">${escapeHtml(featureGroupForKey(key))}</span>
          <h2>${escapeHtml(featureLabel(key))}</h2>
          <p>${escapeHtml(featureDetailExplanation(key))}</p>
        </div>
        <label class="feature-detail-toggle">
          <input type="checkbox" data-feature-detail-toggle="${escapeHtml(key)}" ${displayEnabled ? "checked" : ""} ${locked ? "disabled" : ""}>
          <span class="feature-toggle-visual"></span>
          <b>${escapeHtml(locked ? (tempUnlocked ? "临时放行" : "已锁定") : enabled ? "开启" : "关闭")}</b>
        </label>
      </header>
      ${locked ? `
        <section class="feature-detail-card feature-temp-unlock-panel">
          <h3>主动专用临时放行</h3>
          <p>${escapeHtml(tempUnlocked ? "此功能已在仅保留主动能力时临时放行。关闭后，放行项会自动清空。" : "此功能当前被仅保留主动能力覆盖。你可以二次确认后临时放行，不会改写原配置。")}</p>
          ${relatedUnlocks.length ? `<p>建议同步：${escapeHtml(relatedUnlocks.map((item) => item.label || item.key).join("、"))}</p>` : ""}
          <div class="feature-temp-unlock-actions">
            <button type="button" data-proactive-temp-unlock="${escapeHtml(key)}" data-action="${tempUnlocked ? "clear" : "unlock"}">${escapeHtml(tempUnlocked ? "取消临时放行" : "临时放行")}</button>
            ${!tempUnlocked && relatedUnlocks.length ? `<button type="button" data-proactive-temp-unlock="${escapeHtml(key)}" data-action="unlock" data-sync-related="1">同步放行建议项</button>` : ""}
          </div>
        </section>
      ` : ""}
      ${customFeaturePanel}
      <div class="feature-detail-grid">
        <article class="feature-detail-card">
          <h3>基础信息</h3>
          <dl>
            <div><dt>配置键</dt><dd>${escapeHtml(key)}</dd></div>
            <div><dt>所属模块</dt><dd>${escapeHtml(featureGroupForKey(key))}</dd></div>
            <div><dt>当前状态</dt><dd>${escapeHtml(enabled ? "开启" : "关闭")}</dd></div>
          </dl>
        </article>
        <article class="feature-detail-card">
          <h3>范围</h3>
          <dl>${impactRows}</dl>
        </article>
        <article class="feature-detail-card feature-detail-card-guide">
          <h3>功能说明</h3>
          <dl>${guideRows}</dl>
        </article>
        ${paramCardHtml}
        <article class="feature-detail-card">
          <h3>依赖</h3>
          <dl>${dependencyRows}</dl>
        </article>
      </div>
    </section>
  `;
}

function bindFeatureDetailActions() {
  document.querySelectorAll("[data-feature-back]").forEach((button) => {
    button.addEventListener("click", async () => {
      const saved = await saveCurrentFeatureDetail(button, "已保存并返回功能列表");
      if (!saved) return;
      state.selectedFeatureKey = "";
      renderFeatureSwitches();
    });
  });
  document.querySelectorAll("[data-feature-detail-toggle]").forEach((input) => {
    input.addEventListener("change", () => {
      state.featureDraft[input.dataset.featureDetailToggle] = input.checked;
      renderFeatureSwitches();
    });
  });
  document.querySelectorAll("[data-feature-param-form]").forEach((form) => {
    form.querySelectorAll("[data-feature-param]").forEach((input) => {
      if (input.type === "checkbox") {
        input.addEventListener("change", () => {
          const label = input.closest(".feature-param-check")?.querySelector("span");
          if (label) label.textContent = input.checked ? "开启" : "关闭";
          if (state.selectedFeatureKey === "enable_humanized_states" && input.dataset.featureParam === "enable_rest_reply_simulation") {
            state.featureDraft.enable_rest_reply_simulation = input.checked;
            state.overview.settings = state.overview.settings || {};
            state.overview.settings.enable_rest_reply_simulation = input.checked;
            renderFeatureSwitches();
          }
          if (state.selectedFeatureKey === "enable_message_debounce" && input.dataset.featureParam === "enable_smart_message_debounce") {
            state.featureDraft.enable_smart_message_debounce = input.checked;
            state.overview.settings = state.overview.settings || {};
            state.overview.settings.enable_smart_message_debounce = input.checked;
            renderFeatureSwitches();
          }
          if (state.selectedFeatureKey === "enable_response_self_review" && input.dataset.featureParam === "enable_smart_silence") {
            state.featureDraft.enable_smart_silence = input.checked;
            state.overview.settings = state.overview.settings || {};
            state.overview.settings.enable_smart_silence = input.checked;
            renderFeatureSwitches();
          }
          if (
            state.selectedFeatureKey === "enable_tts_enhancement"
            && ["auto_voice_enabled", "enable_tts_local_playback", "enable_tts_live_subtitle_sync"].includes(input.dataset.featureParam)
          ) {
            state.featureDraft[input.dataset.featureParam] = input.checked;
            state.overview.settings = state.overview.settings || {};
            state.overview.settings[input.dataset.featureParam] = input.checked;
            renderFeatureSwitches();
          }
          if (state.selectedFeatureKey === "enable_emotion_simulation" && input.dataset.featureParam === "enable_llm_emotion_judgement") {
            state.featureDraft.enable_llm_emotion_judgement = input.checked;
            state.overview.settings = state.overview.settings || {};
            state.overview.settings.enable_llm_emotion_judgement = input.checked;
            renderFeatureSwitches();
          }
          if (
            state.selectedFeatureKey === "enable_group_slang_learning"
            && ["enable_group_slang_meanings", "enable_group_slang_web_search"].includes(input.dataset.featureParam)
          ) {
            state.featureDraft[input.dataset.featureParam] = input.checked;
            state.overview.settings = state.overview.settings || {};
            state.overview.settings[input.dataset.featureParam] = input.checked;
            renderFeatureSwitches();
          }
          if (
            state.selectedFeatureKey === "enable_group_wakeup_enhancement"
            && ["enable_group_wakeup_question", "enable_group_wakeup_cold_group", "enable_group_high_intensity_mode"].includes(input.dataset.featureParam)
          ) {
            state.featureDraft[input.dataset.featureParam] = input.checked;
            state.overview.settings = state.overview.settings || {};
            state.overview.settings[input.dataset.featureParam] = input.checked;
            renderFeatureSwitches();
          }
        });
      }
      if (state.selectedFeatureKey === "enable_emotion_simulation" && input.dataset.featureParam === "emotion_judgement_mode") {
        input.addEventListener("change", () => {
          state.overview.settings = state.overview.settings || {};
          state.overview.settings.emotion_judgement_mode = input.value || "suspicious";
          renderFeatureSwitches();
        });
      }
      if (state.selectedFeatureKey === "enable_response_self_review" && input.dataset.featureParam === "response_review_mode") {
        input.addEventListener("change", () => {
          state.overview.settings = state.overview.settings || {};
          state.overview.settings.response_review_mode = input.value || "severe_only";
          renderFeatureSwitches();
        });
      }
      if (state.selectedFeatureKey === "enable_tts_enhancement" && input.dataset.featureParam === "tts_frequency_control_mode") {
        input.addEventListener("change", () => {
          state.overview.settings = state.overview.settings || {};
          state.overview.settings.tts_frequency_control_mode = input.value || "global";
          renderFeatureSwitches();
        });
      }
      if (state.selectedFeatureKey === "enable_tts_enhancement" && input.dataset.featureParam === "tts_generation_mode") {
        input.addEventListener("change", () => {
          state.overview.settings = state.overview.settings || {};
          state.overview.settings.tts_generation_mode = input.value || "fast_tag";
          renderFeatureSwitches();
        });
      }
    });
    form.querySelectorAll("[data-feature-provider-select]").forEach((select) => {
      syncFeatureProviderInput(select);
      select.addEventListener("change", () => syncFeatureProviderInput(select));
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const featureKey = form.dataset.featureParamForm || state.selectedFeatureKey;
      const payload = collectFeatureDetailPayload(featureKey, form);
      await runAction(
        () => postJson("/settings/update", payload),
        "已保存功能参数",
        form.querySelector(".feature-param-save"),
      );
    });
  });
  if (state.selectedFeatureKey === "enable_segmented_proactive_reply") {
    bindSegmentedPreview($("#featureFlags"));
  }
  if (state.selectedFeatureKey === "enable_food_menu_recommendation") {
    bindFoodMenuFeatureActions();
  }
  bindProactiveOnlyTempUnlockActions();
}

function bindProactiveOnlyTempUnlockActions(root = document) {
  root.querySelectorAll("[data-proactive-temp-unlock]").forEach((button) => {
    button.addEventListener("click", async () => {
      const key = button.dataset.proactiveTempUnlock || "";
      const action = button.dataset.action || "unlock";
      const syncRelated = button.dataset.syncRelated === "1";
      const modeWasEnabled = proactiveOnlyModeEnabled();
      const confirmKey = `proactive-temp-${action}-${key}-${syncRelated ? "sync" : "single"}`;
      const confirmText = action === "clear"
        ? "再次点击取消临时放行"
        : syncRelated
          ? "再次点击确认同步放行"
          : "再次点击确认临时放行";
      if (!requireSecondClick(button, confirmKey, confirmText, confirmText)) return;
      setActionBusy(button, true);
      showToast("正在处理...");
      try {
        if (modeWasEnabled && !toBool(state.overview?.proactive_only?.enabled)) {
          const saved = await postJson("/settings/update", { features: { enable_proactive_only_mode: true } });
          state.overview = { ...(state.overview || {}), ...(saved || {}) };
          if (Object.prototype.hasOwnProperty.call(state.featureDraft || {}, "enable_proactive_only_mode")) {
            state.featureDraft.enable_proactive_only_mode = true;
          }
        }
        const result = await postJson("/proactive_only/unlock", { key, action, sync_related: syncRelated });
        state.overview = state.overview || {};
        const proactiveOnly = result?.proactive_only || result?.data?.proactive_only || {};
        state.overview.proactive_only = Object.keys(proactiveOnly).length ? proactiveOnly : (state.overview.proactive_only || {});
        if (modeWasEnabled && Object.prototype.hasOwnProperty.call(state.featureDraft || {}, "enable_proactive_only_mode")) {
          state.featureDraft.enable_proactive_only_mode = true;
        }
        renderFeatureSwitches();
        showToast(action === "clear" ? "已取消临时放行" : "已临时放行");
      } catch (error) {
        showToast(`操作失败：${error.message}`, "error");
      } finally {
        setActionBusy(button, false);
      }
    });
  });
}

function renderProviders() {
  if (window.PrivateCompanionProviderTree?.renderProviders) {
    window.PrivateCompanionProviderTree.renderProviders({
      window,
      document,
      state,
      escapeHtml,
      providerLabels,
      providerGuides,
      providerPreferenceMeta,
      providerPassiveImpactMeta,
      providerGroups,
      providerGroupByKey,
      noFallbackProviderKeys,
      optionalNoFallbackProviderKeys,
      visibleConfigKey,
      providerAllowedInCurrentMode,
      providerNeedsLowLatency,
      currentProviderConfigMode,
      syncProviderConfigModeControls,
      setProviderConfigMode,
      postJson,
    });
  }
}

function providerValuesForRender() {
  if (window.PrivateCompanionProviderTree?.currentProviderValues) {
    return window.PrivateCompanionProviderTree.currentProviderValues({ document, state });
  }
  return {
    ...(state.overview?.providers || {}),
    ...(state.providerDraft || {}),
  };
}

function currentProviderValues() {
  return providerValuesForRender();
}

function bindProviderToolbar() {
  if (window.PrivateCompanionProviderTree?.bindProviderToolbar) {
    window.PrivateCompanionProviderTree.bindProviderToolbar({ document, state, setProviderConfigMode });
  }
}

async function testProvider(key) {
  if (window.PrivateCompanionProviderTree?.testProvider) {
    await window.PrivateCompanionProviderTree.testProvider({
      document,
      state,
      postJson,
      noFallbackProviderKeys,
      optionalNoFallbackProviderKeys,
    }, key);
  }
}

function miniStat(label, value) {
  return `<div class="mini-stat"><b>${escapeHtml(value)}</b><span>${escapeHtml(label)}</span></div>`;
}

function scoreGauge(label, value, min, max) {
  const num = Number(value || 0);
  const pct = Math.max(0, Math.min(100, Math.round(((num - min) / Math.max(1, max - min)) * 100)));
  return `
    <div class="gauge">
      <svg viewBox="0 0 120 64" role="img" aria-label="${escapeHtml(label)}">
        <path d="M16 58 A44 44 0 0 1 104 58" class="gauge-bg"></path>
        <path d="M16 58 A44 44 0 0 1 104 58" class="gauge-fg" pathLength="100" style="stroke-dasharray:${pct} 100"></path>
        <text x="60" y="48" text-anchor="middle">${escapeHtml(num)}</text>
      </svg>
      <span>${escapeHtml(label)}</span>
    </div>
  `;
}

function relationshipGraphView(edges, groupMembers = {}) {
  const pairs = Object.entries(edges || {})
    .map(([key, value]) => {
      const parts = key.split(/[-|:>]+/).map((item) => item.trim()).filter(Boolean);
      const weight = Number(value?.count || value?.weight || value || 1);
      return parts.length >= 2 ? {
        a: parts[0],
        b: parts[1],
        weight,
        kind: value?.kind || value?.type || value?.relation || "",
        summary: value?.summary || value?.last_summary || value?.reason || "",
      } : null;
    })
    .filter(Boolean)
    .sort((a, b) => b.weight - a.weight)
    .slice(0, 24);
  if (!pairs.length) return `<div class="empty small">暂无关系边</div>`;

  const memberMap = relationshipMemberMap(groupMembers);
  const scores = new Map();
  pairs.forEach((edge) => {
    [edge.a, edge.b].forEach((id) => {
      const item = scores.get(id) || { id, degree: 0, weight: 0 };
      item.degree += 1;
      item.weight += edge.weight;
      scores.set(id, item);
    });
  });
  const topNodes = [...scores.values()]
    .sort((a, b) => b.weight - a.weight || b.degree - a.degree)
    .slice(0, 8);
  const maxWeight = Math.max(1, ...pairs.map((edge) => edge.weight));
  const strongest = pairs[0];
  const hiddenCount = Math.max(0, Object.keys(edges || {}).length - pairs.length);
  return `
    <div class="relation-map">
      <div class="relation-map-summary">
        <article><span>涉及成员</span><b>${escapeHtml(scores.size)}</b></article>
        <article><span>互动关系</span><b>${escapeHtml(Object.keys(edges || {}).length)}</b></article>
        <article><span>最强连接</span><b>${escapeHtml(relationNodeName(strongest.a, memberMap))} ↔ ${escapeHtml(relationNodeName(strongest.b, memberMap))}</b></article>
      </div>
      <div class="relation-node-grid">
        ${topNodes.map((node) => relationNodeCard(node, memberMap)).join("")}
      </div>
      <div class="relation-edge-list">
        ${pairs.map((edge) => relationEdgeRow(edge, memberMap, maxWeight)).join("")}
      </div>
      ${hiddenCount ? `<p class="muted relation-map-note">还有 ${escapeHtml(hiddenCount)} 条较弱关系未展开，可在群聊观测继续积累后查看。</p>` : ""}
    </div>
  `;
}

function relationshipMemberMap(groupMembers = {}) {
  const map = new Map();
  const worldbookMembers = state.overview?.worldbook?.members || [];
  worldbookMembers.forEach((item) => {
    const id = String(item.user_id || "").trim();
    if (!id) return;
    map.set(id, {
      name: item.name || id,
      aliases: Array.isArray(item.aliases) ? item.aliases : [],
      observed: Array.isArray(item.observed_names) ? item.observed_names : [],
      source: "关系网",
    });
  });
  Object.entries(groupMembers || {}).forEach(([id, raw]) => {
    if (!id) return;
    const item = raw && typeof raw === "object" ? raw : {};
    const existing = map.get(String(id)) || {};
    map.set(String(id), {
      name: existing.name || item.name || item.nickname || item.card || id,
      aliases: existing.aliases || [],
      observed: existing.observed || [],
      source: existing.source || "群聊",
    });
  });
  return map;
}

function relationNodeName(id, memberMap) {
  const item = memberMap.get(String(id));
  return item?.name || String(id);
}

function relationNodeCard(node, memberMap) {
  const member = memberMap.get(String(node.id)) || {};
  const name = member.name || node.id;
  const subNames = [...(member.aliases || []), ...(member.observed || [])].filter(Boolean).slice(0, 3);
  return `
    <article class="relation-node-card">
      <div class="relation-avatar">${escapeHtml(shortName(name, 2))}</div>
      <div>
        <b>${escapeHtml(name)}</b>
        <code>${escapeHtml(node.id)}</code>
        ${subNames.length ? `<small>${subNames.map((item) => escapeHtml(item)).join(" / ")}</small>` : `<small>${escapeHtml(member.source || "群聊成员")}</small>`}
      </div>
      <span>${escapeHtml(node.degree)} 边</span>
    </article>
  `;
}

function relationEdgeRow(edge, memberMap, maxWeight) {
  const pct = Math.max(4, Math.round((edge.weight / maxWeight) * 100));
  const leftName = relationNodeName(edge.a, memberMap);
  const rightName = relationNodeName(edge.b, memberMap);
  return `
    <article class="relation-edge-row">
      <div class="relation-edge-main">
        <b title="${escapeHtml(edge.a)}">${escapeHtml(leftName)}</b>
        <span>↔</span>
        <b title="${escapeHtml(edge.b)}">${escapeHtml(rightName)}</b>
      </div>
      <div class="relation-edge-meter"><i style="width:${pct}%"></i></div>
      <div class="relation-edge-meta">
        <code>${escapeHtml(edge.a)}</code>
        <code>${escapeHtml(edge.b)}</code>
        <span>${escapeHtml(edge.kind || "互动")} · ${escapeHtml(edge.weight)}</span>
      </div>
      ${edge.summary ? `<p>${escapeHtml(edge.summary)}</p>` : ""}
    </article>
  `;
}

function groupMessageActivityView(messages) {
  const recent = (Array.isArray(messages) ? messages : [])
    .map(normalizeGroupMessage)
    .filter((item) => item.text || item.sender || item.timestamp)
    .slice(-24);
  if (!recent.length) return `<div class="empty small">暂无最近消息</div>`;
  const now = Math.floor(Date.now() / 1000);
  const buckets = Array.from({ length: 6 }, (_, index) => ({
    label: index === 5 ? "现在" : `-${(5 - index) * 2}h`,
    count: 0,
  }));
  recent.forEach((item) => {
    if (!item.timestamp) {
      buckets[5].count += 1;
      return;
    }
    const diffHours = Math.max(0, Math.floor((now - item.timestamp) / 3600));
    const index = Math.max(0, Math.min(5, 5 - Math.floor(diffHours / 2)));
    buckets[index].count += 1;
  });
  const max = Math.max(1, ...buckets.map((item) => item.count));
  const uniqueSpeakers = new Set(recent.map((item) => item.sender || item.senderId).filter(Boolean)).size;
  const latest = recent[recent.length - 1] || {};
  const displayRows = recent.slice(-8).reverse();
  return `
    <div class="group-message-activity">
      <div class="group-message-summary">
        <article><span>最近消息</span><b>${escapeHtml(recent.length)}</b></article>
        <article><span>说话人数</span><b>${escapeHtml(uniqueSpeakers || "-")}</b></article>
        <article><span>最后活跃</span><b>${escapeHtml(latest.displayTime || "刚刚")}</b></article>
      </div>
      <div class="group-activity-bars">
        ${buckets.map((item) => `
          <span title="${escapeHtml(`${item.label}：${item.count} 条`)}">
            <i style="height:${Math.max(8, Math.round((item.count / max) * 100))}%"></i>
            <small>${escapeHtml(item.label)}</small>
          </span>
        `).join("")}
      </div>
      <div class="group-message-list">
        ${displayRows.map((item) => `
          <article>
            <div>
              <b>${escapeHtml(item.sender || "群友")}</b>
              <time>${escapeHtml(item.displayTime || "")}</time>
            </div>
            <p>${escapeHtml(item.text || "[非文本消息]")}</p>
          </article>
        `).join("")}
      </div>
    </div>
  `;
}

function normalizeGroupMessage(item) {
  const raw = item && typeof item === "object" ? item : {};
  const sender = raw.sender_name || raw.nickname || raw.card || raw.name || raw.user_name || raw.sender_id || raw.user_id || "";
  const senderId = raw.sender_id || raw.user_id || raw.qq || "";
  const text = raw.text || raw.message || raw.content || raw.raw_message || raw.summary || "";
  const timestamp = parseMessageTimestamp(raw.ts ?? raw.timestamp ?? raw.time ?? raw.datetime ?? raw.created_at);
  return {
    sender: String(sender || "").trim(),
    senderId: String(senderId || "").trim(),
    text: String(text || "").trim(),
    timestamp,
    displayTime: formatMessageTime(timestamp, raw.time || raw.created_at || raw.datetime || ""),
  };
}

function parseMessageTimestamp(value) {
  if (value === null || value === undefined || value === "") return 0;
  if (typeof value === "number" && Number.isFinite(value)) return value > 1e12 ? Math.floor(value / 1000) : Math.floor(value);
  const text = String(value).trim();
  if (/^\d+$/.test(text)) {
    const num = Number(text);
    return num > 1e12 ? Math.floor(num / 1000) : Math.floor(num);
  }
  const parsed = Date.parse(text);
  if (Number.isFinite(parsed)) return Math.floor(parsed / 1000);
  const fallback = Date.parse(text.replace(/-/g, "/"));
  return Number.isFinite(fallback) ? Math.floor(fallback / 1000) : 0;
}

function formatMessageTime(timestamp, fallback = "") {
  if (!timestamp) return String(fallback || "").trim();
  const date = new Date(timestamp * 1000);
  if (Number.isNaN(date.getTime())) return String(fallback || "").trim();
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function messageTimelineSvg(messages) {
  const recent = Array.isArray(messages) ? messages.slice(-30) : [];
  if (!recent.length) return `<div class="empty small">暂无最近消息</div>`;
  const buckets = Array.from({ length: 12 }, () => 0);
  const now = Math.floor(Date.now() / 1000);
  recent.forEach((item) => {
    const ts = Number(item.ts || item.time || 0);
    const diffHours = ts ? Math.max(0, Math.min(11, Math.floor((now - ts) / 3600))) : 0;
    buckets[11 - diffHours] += 1;
  });
  const max = Math.max(1, ...buckets);
  return `
    <svg class="timeline-svg" viewBox="0 0 360 120">
      ${buckets.map((value, index) => {
        const height = Math.max(4, Math.round((value / max) * 86));
        const x = 16 + index * 28;
        const y = 100 - height;
        return `<rect x="${x}" y="${y}" width="18" height="${height}" rx="4"></rect>`;
      }).join("")}
      <line x1="12" y1="102" x2="348" y2="102"></line>
      <text x="18" y="116">-12h</text>
      <text x="318" y="116">now</text>
    </svg>
  `;
}

function shortName(value, limit) {
  const text = String(value || "");
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

function donutChart(data, options = {}) {
  const {
    emptyText = "暂无记忆数据",
    labelFormatter = null,
    maxSegments = 0,
    mergeBelowPercent = 0,
    otherLabel = "其他",
  } = options;
  const grouped = new Map();
  for (const [rawLabel, rawValue] of Object.entries(data || {})) {
    const value = Number(rawValue);
    if (value <= 0) continue;
    const label = String(labelFormatter ? labelFormatter(rawLabel, value) : rawLabel || "未命名");
    grouped.set(label, (grouped.get(label) || 0) + value);
  }
  let entries = Array.from(grouped.entries())
    .map(([label, value]) => ({ label, value }))
    .sort((a, b) => b.value - a.value);
  if (!entries.length) return `<div class="empty small">${escapeHtml(emptyText)}</div>`;
  const total = entries.reduce((sum, item) => sum + item.value, 0);
  if (mergeBelowPercent > 0 || maxSegments > 0) {
    const kept = [];
    let otherValue = 0;
    entries.forEach((item, index) => {
      const pct = total > 0 ? item.value / total : 0;
      const overflow = maxSegments > 0 && index >= maxSegments;
      const tooSmall = mergeBelowPercent > 0 && pct < mergeBelowPercent;
      if (overflow || tooSmall) {
        otherValue += item.value;
      } else {
        kept.push(item);
      }
    });
    entries = kept;
    if (otherValue > 0) entries.push({ label: otherLabel, value: otherValue });
  }
  let offset = 0;
  const colors = ["#2f7566", "#8a6f3e", "#4d7ea8", "#a15f26", "#6e7f3f"];
  const circles = entries.map((item, index) => {
    const pct = (item.value / total) * 100;
    const circle = `<circle r="42" cx="60" cy="60" pathLength="100" stroke="${colors[index % colors.length]}" stroke-dasharray="${pct} ${100 - pct}" stroke-dashoffset="${-offset}"></circle>`;
    offset += pct;
    return circle;
  }).join("");
  return `
    <div class="donut-wrap">
      <svg class="donut" viewBox="0 0 120 120">
        <circle r="42" cx="60" cy="60" class="donut-bg"></circle>
        ${circles}
        <text x="60" y="64" text-anchor="middle">${escapeHtml(formatNumber(total))}</text>
      </svg>
      <div class="donut-legend">
        ${entries.map((item, index) => {
          const pct = total > 0 ? (item.value / total) * 100 : 0;
          const pctText = pct >= 1 ? `${pct.toFixed(1)}%` : (pct > 0 ? `${pct.toFixed(1)}%` : "0%");
          return `
            <span class="donut-legend-item">
              <span class="donut-legend-label"><i style="background:${colors[index % colors.length]}"></i>${escapeHtml(item.label)}</span>
              <span class="donut-legend-value">${escapeHtml(formatNumber(item.value))} · ${escapeHtml(pctText)}</span>
            </span>
          `;
        }).join("")}
      </div>
    </div>
  `;
}

function detailBlock(title, preText, pairs) {
  const dl = pairs?.length
    ? `<dl>${pairs.map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value || "-")}</dd>`).join("")}</dl>`
    : "";
  const pre = preText ? `<pre>${escapeHtml(preText)}</pre>` : "";
  return `<section class="detail-block"><h2>${escapeHtml(title)}</h2>${pre}${dl}</section>`;
}

function renderDl(selector, data) {
  const entries = Object.entries(data || {});
  $(selector).innerHTML = entries.length
    ? entries.map(([key, value]) => `<dt>${escapeHtml(configLabels[key] || key)}</dt><dd>${escapeHtml(formatValue(value))}</dd>`).join("")
    : `<dt>-</dt><dd>暂无数据</dd>`;
}

function featureLabel(key) {
  return featureMeta[key]?.[0] || key.replace(/^enable_/, "");
}

function featureDescription(key) {
  return featureMeta[key]?.[1] || "配置开关。";
}

function formatValue(value) {
  if (value === "inject") return "注入";
  if (value === "transcribe") return "转述";
  if (Array.isArray(value)) return value.join(", ");
  if (value && typeof value === "object") return JSON.stringify(value, null, 2);
  return value ?? "";
}

function showToast(message, tone = "ok") {
  const text = String(message || "").trim();
  if (!text) return;
  let toast = document.getElementById("pageToast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "pageToast";
    toast.className = "page-toast";
    toast.setAttribute("role", "status");
    toast.setAttribute("aria-live", "polite");
    document.body.appendChild(toast);
  }
  toast.textContent = text;
  toast.classList.toggle("error", tone === "error");
  toast.classList.add("show");
  window.clearTimeout(showToast._timer);
  showToast._timer = window.setTimeout(() => toast.classList.remove("show"), 1800);
}

async function copyTextToClipboard(text, successMessage = "已复制") {
  const value = String(text || "").trim();
  if (!value) {
    showToast("没有可复制的内容", "error");
    return;
  }
  const fallbackCopy = () => {
    const box = document.createElement("textarea");
    box.value = value;
    box.setAttribute("readonly", "readonly");
    box.style.position = "fixed";
    box.style.left = "-9999px";
    box.style.top = "0";
    document.body.appendChild(box);
    box.focus();
    box.select();
    try {
      box.setSelectionRange(0, box.value.length);
    } catch (error) {
      // Older engines may not support setSelectionRange on textarea in this context.
    }
    const ok = document.execCommand("copy");
    box.remove();
    return ok;
  };
  try {
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(value);
      } catch (error) {
        if (!fallbackCopy()) throw error;
      }
    } else {
      if (!fallbackCopy()) throw new Error("浏览器拒绝复制");
    }
    showToast(successMessage);
  } catch (error) {
    showToast(`复制失败：${error.message}`, "error");
  }
}

function setActionBusy(control, busy) {
  if (!(control instanceof HTMLButtonElement)) return;
  if (busy) {
    control.dataset.originalText = control.textContent || "";
    control.disabled = true;
    control.classList.add("is-busy");
    control.textContent = "处理中...";
  } else {
    control.disabled = false;
    control.classList.remove("is-busy");
    if (control.dataset.originalText) {
      control.textContent = control.dataset.originalText;
      delete control.dataset.originalText;
    }
  }
}

function requireSecondClick(control, key, message, nextText = "再次点击确认", timeoutMs = 6000) {
  if (!(control instanceof HTMLButtonElement)) return true;
  const now = Date.now();
  const armed = control.dataset.confirmKey === key && now - Number(control.dataset.confirmAt || 0) < timeoutMs;
  if (armed) {
    delete control.dataset.confirmKey;
    delete control.dataset.confirmAt;
    return true;
  }
  control.dataset.confirmKey = key;
  control.dataset.confirmAt = String(now);
  control.dataset.originalText = control.dataset.originalText || control.textContent || "";
  control.textContent = nextText;
  showToast(message);
  window.clearTimeout(control._confirmTimer);
  control._confirmTimer = window.setTimeout(() => {
    if (control.dataset.confirmKey === key) {
      delete control.dataset.confirmKey;
      delete control.dataset.confirmAt;
      control.textContent = control.dataset.originalText || "";
      delete control.dataset.originalText;
    }
  }, timeoutMs);
  return false;
}

async function runAction(action, successMessage = "", control = null) {
  setActionBusy(control, true);
  showToast("正在处理...");
  try {
    const result = await action();
    if (result && typeof result === "object" && result.plugin && result.features) {
      const requestSeq = ++loadAllRequestSeq;
      state.overview = result;
      state.featureDraft = featureDraftFromOverview(result);
      renderAll();
      $("#subtitle").textContent = `${result.plugin.bot_name || "Private Companion"} · ${new Date().toLocaleString()}`;
      void loadUserGroupLists(requestSeq);
    } else {
      await loadAll();
    }
    if (result && result.config_saved === false) {
      showToast("已应用到运行态，但配置持久化失败；重启或刷新后可能恢复旧值，请查看日志", "error");
    } else {
      showToast(successMessage || result?.message || "操作已完成");
    }
    return result;
  } catch (error) {
    showToast(`操作失败：${error.message}`, "error");
  } finally {
    setActionBusy(control, false);
  }
}

function switchTab(tabName) {
  tabName = tabName || "dashboard";
  if (tabName === state.activeTab) return;
  const currentPanel = document.querySelector(".panel.is-active");
  const nextPanel = document.getElementById(`panel-${tabName}`);
  state.activeTab = tabName;
  document.querySelectorAll(".tab").forEach((item) => item.classList.toggle("is-active", item.dataset.tab === tabName));
  if (currentPanel && nextPanel && currentPanel !== nextPanel) {
    currentPanel.classList.add("is-leaving");
    currentPanel.classList.remove("is-active");
    setTimeout(() => {
      currentPanel.classList.remove("is-leaving");
      currentPanel.style.display = "";
      nextPanel.classList.add("is-active");
      renderActiveTab(state.activeTab);
      ensureTabData(state.activeTab).catch((error) => showToast(`页面数据加载失败：${error.message}`, "error"));
    }, 140);
  } else {
    document.querySelectorAll(".panel").forEach((item) => item.classList.toggle("is-active", item.id === `panel-${tabName}`));
    renderActiveTab(state.activeTab);
    ensureTabData(state.activeTab).catch((error) => showToast(`页面数据加载失败：${error.message}`, "error"));
  }
}

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => {
    switchTab(button.dataset.tab);
  });
});

document.addEventListener("click", async (event) => {
  const target = event.target instanceof Element ? event.target.closest("[data-jump-tab]") : null;
  if (!target) return;
  switchTab(target.dataset.jumpTab);
});

document.addEventListener("click", async (event) => {
  const element = event.target instanceof Element ? event.target : null;
  const troubleshootingFilter = element?.closest("[data-troubleshooting-filter]");
  if (troubleshootingFilter) {
    state.troubleshootingFilter = troubleshootingFilter.dataset.troubleshootingFilter || "all";
    renderTroubleshooting();
    return;
  }
  const troubleshootingRefresh = element?.closest("[data-troubleshooting-refresh]");
  if (troubleshootingRefresh) {
    setActionBusy(troubleshootingRefresh, true);
    try {
      await Promise.all([loadTroubleshooting(), loadDiagnostics(true)]);
      showToast("排障信息已刷新");
    } catch (error) {
      showToast(`刷新失败：${error.message}`, "error");
    } finally {
      setActionBusy(troubleshootingRefresh, false);
    }
    return;
  }
  const scrollTarget = element?.closest("[data-scroll-target]");
  if (scrollTarget) {
    const targetId = scrollTarget.dataset.scrollTarget || "";
    const targetEl = targetId ? document.getElementById(targetId) : null;
    if (targetEl) {
      targetEl.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    return;
  }
  const troubleshootingTest = element?.closest("[data-troubleshooting-test]");
  if (troubleshootingTest) {
    const testType = troubleshootingTest.dataset.troubleshootingTest || "";
    const workflowKind = troubleshootingTest.dataset.troubleshootingWorkflowKind || "";
    setActionBusy(troubleshootingTest, true);
    try {
      const result = await postJson("/troubleshooting/test", { type: testType, workflow_kind: workflowKind });
      state.troubleshooting = state.troubleshooting || {};
      state.troubleshooting.chain_tests = {
        ...(state.troubleshooting.chain_tests || {}),
        [testType]: result,
      };
      renderTroubleshooting();
      if (result.pending) {
        showToast("主动消息链路测试已预约，约 1 分钟后刷新查看结果", "success");
      } else {
        showToast(result.ok ? "链路测试通过" : `链路测试失败：${result.error || "未返回有效结果"}`, result.ok ? "success" : "error");
      }
    } catch (error) {
      showToast(`链路测试失败：${error.message}`, "error");
    } finally {
      setActionBusy(troubleshootingTest, false);
    }
    return;
  }
  const copyButton = element?.closest("[data-copy-text]");
  if (copyButton) {
    void copyTextToClipboard(copyButton.dataset.copyText || "", "已复制摘要");
    return;
  }
  const row = element?.closest("[data-image-cache-key]");
  if (row) {
    state.selectedImageCacheKey = row.dataset.imageCacheKey || "";
    renderImageCache();
    return;
  }
  const deleteButton = element?.closest("[data-image-cache-delete]");
  if (deleteButton) {
    const key = deleteButton.dataset.imageCacheDelete || "";
    if (!key) return;
    if (!requireSecondClick(deleteButton, `image-cache:${key}`, "再次点击会删除这条图片缓存")) return;
    setActionBusy(deleteButton, true);
    try {
      await postJson("/image_cache/delete", { key });
      state.selectedImageCacheKey = "";
      await loadImageCache();
      showToast("图片缓存已删除");
    } catch (error) {
      showToast(`删除失败：${error.message}`, "error");
    } finally {
      setActionBusy(deleteButton, false);
    }
  }
});

document.addEventListener("click", async (event) => {
  const element = event.target instanceof Element ? event.target : null;
  const proactiveDeleteButton = element?.closest("[data-proactive-candidate-delete]");
  if (proactiveDeleteButton) {
    const candidateId = proactiveDeleteButton.dataset.proactiveCandidateDelete || "";
    const userId = proactiveDeleteButton.dataset.proactiveUserId || "";
    if (!candidateId) return;
    if (!requireSecondClick(proactiveDeleteButton, `proactive-delete:${candidateId}`, "再次点击删除这条主动候选", "再次点击删除")) return;
    await runAction(async () => {
      const result = await postJson("/proactive/candidate/delete", { candidate_id: candidateId, user_id: userId });
      if (state.overview) {
        state.overview.proactive_candidates = result.proactive_candidates || state.overview.proactive_candidates;
        state.overview.proactive_tasks = result.proactive_tasks || state.overview.proactive_tasks;
      }
      return state.overview || result;
    }, "已删除主动候选", proactiveDeleteButton);
    return;
  }
  const proactivePruneButton = element?.closest("[data-proactive-candidate-prune]");
  if (proactivePruneButton) {
    const userId = proactivePruneButton.dataset.proactiveCandidatePrune || "";
    const keep = Number(proactivePruneButton.dataset.proactivePruneKeep || 160) || 160;
    if (!userId) return;
    if (!requireSecondClick(proactivePruneButton, `proactive-prune:${userId}:${keep}`, `再次点击将该用户未发送候选压到 ${keep} 条`, "再次点击删一点")) return;
    await runAction(async () => {
      const result = await postJson("/proactive/candidate/prune", { user_id: userId, keep });
      if (state.overview) {
        state.overview.proactive_candidates = result.proactive_candidates || state.overview.proactive_candidates;
        state.overview.proactive_tasks = result.proactive_tasks || state.overview.proactive_tasks;
      }
      return state.overview || result;
    }, "已压缩该用户未发送候选", proactivePruneButton);
    return;
  }
  const creativeReanalyze = element?.closest("[data-creative-reanalyze]");
  if (creativeReanalyze) {
    const projectId = creativeReanalyze.dataset.creativeReanalyze || "";
    await runAction(async () => {
      const result = await postJson("/creative/project/reanalyze", { id: projectId });
      if (state.selectedBook?.kind === "creative" && state.selectedBook.id === projectId) {
        await ensureCreativeProjectDetail(state.selectedBook, true);
      }
      return result;
    }, "已完成质量分析", creativeReanalyze);
    return;
  }
  const creativeRebuildMemory = element?.closest("[data-creative-rebuild-memory]");
  if (creativeRebuildMemory) {
    const projectId = creativeRebuildMemory.dataset.creativeRebuildMemory || "";
    await runAction(async () => {
      const result = await postJson("/creative/project/rebuild_memory", { id: projectId });
      if (state.selectedBook?.kind === "creative" && state.selectedBook.id === projectId) {
        await ensureCreativeProjectDetail(state.selectedBook, true);
      }
      return result;
    }, "已重建创作记忆", creativeRebuildMemory);
    return;
  }
  const deleteButton = element?.closest("[data-book-delete]");
  if (deleteButton) {
    void deleteSelectedBookshelfItem(deleteButton);
    return;
  }
  const bookButton = element?.closest("[data-book-id]");
  if (bookButton) {
    selectBookshelfBook(bookButton.dataset.bookId);
    return;
  }
  if (element?.closest("[data-creative-enter-edit]")) {
    state.creativeEditing = true;
    renderBookDetailPanel();
    return;
  }
  if (element?.closest("[data-creative-exit-edit]")) {
    state.creativeEditing = false;
    renderBookDetailPanel();
    return;
  }
  if (element?.closest("[data-book-read]")) {
    state.bookshelfPage = "reader";
    state.selectedBookSpreadIndex = 0;
    renderBookDetailPanel();
    return;
  }
  if (element?.closest("[data-book-prev]")) {
    state.selectedBookSpreadIndex = Math.max(0, Number(state.selectedBookSpreadIndex || 0) - 2);
    renderBookDetailPanel();
    return;
  }
  if (element?.closest("[data-book-next]")) {
    const pages = Array.isArray(state.selectedBook?.pages) ? state.selectedBook.pages : [];
    state.selectedBookSpreadIndex = Math.min(Math.max(0, pages.length - 1), Number(state.selectedBookSpreadIndex || 0) + 2);
    renderBookDetailPanel();
    return;
  }
  const ratingButton = element?.closest("[data-book-rating]");
  if (ratingButton) {
    void rateSelectedBookshelfItem(ratingButton);
    return;
  }
  const commentsUpdateButton = element?.closest("[data-book-reread], [data-book-comments-update]");
  if (commentsUpdateButton) {
    void rereadSelectedBookshelfItem(commentsUpdateButton);
    return;
  }
  const tagPickButton = element?.closest("[data-book-tag-pick]");
  if (tagPickButton) {
    applyBookPreferenceTagPick(tagPickButton);
    return;
  }
  const diaryJump = element?.closest("[data-diary-jump]");
  if (diaryJump) {
    state.selectedDiaryDate = diaryJump.dataset.diaryJump || "";
    renderBookDetailPanel();
    return;
  }
  const browsingJump = element?.closest("[data-browsing-index]");
  if (browsingJump) {
    state.selectedBrowsingIndex = Number(browsingJump.dataset.browsingIndex || 0);
    renderBookDetailPanel();
    return;
  }
  const copySource = element?.closest("[data-copy-source-url]");
  if (copySource) {
    void copyTextToClipboard(copySource.dataset.copySourceUrl || "", "已复制来源链接");
    return;
  }
  if (element?.closest("[data-book-back]")) {
    state.bookshelfPage = "detail";
    state.creativeEditing = false;
    renderBookDetailPanel();
    return;
  }
  if (element?.closest("[data-book-close]")) {
    state.selectedBook = null;
    state.bookshelfPage = "shelf";
    state.creativeEditing = false;
    renderBookshelf();
  }
});

async function deleteSelectedBookshelfItem(button = null) {
  const book = state.selectedBook || {};
  const dataset = button?.dataset || {};
  const kind = dataset.bookKind || book.kind || "";
  const itemId = dataset.bookId || book.id || "";
  const albumId = dataset.bookAlbumId || book.album_id || "";
  const title = dataset.bookTitle || book.title || "";
  const diaryDate = kind === "diary" ? (dataset.bookDate || state.selectedDiaryDate || "") : "";
  if (!kind) {
    alert("没有找到当前书籍，请刷新拓展页后再试。");
    return;
  }
  const label = kind === "diary" && diaryDate ? `${diaryDate} 的日记` : (title || "这本书");
  if (kind !== "jm_album" && !requireSecondClick(button, `book:${kind}:${itemId}:${diaryDate}`, `再次点击删除「${label}」`, "再次点击删除")) return;
  if (button) {
    button.disabled = true;
    button.textContent = "移除中...";
  }
  showToast("正在从书柜移除...");
  try {
    if (kind === "creative") {
      const result = await postJson("/creative/project/delete", { id: itemId });
      if (!result.removed) {
        showToast("没有找到要删除的创作项目，请刷新拓展页后再试。", "error");
        if (button) {
          button.disabled = false;
          button.textContent = "删除作品";
        }
        return;
      }
      const removeCreative = (shelf) => {
        if (!shelf || !Array.isArray(shelf.public_books)) return;
        shelf.public_books = shelf.public_books.filter((item) => !(item.kind === "creative" && String(item.id || "") === String(itemId)));
        shelf.public_count = shelf.public_books.length;
      };
      removeCreative(state.overview?.bookshelf);
      removeCreative(state.bookshelfUnlocked);
      if (state.overview?.creative) {
        const items = Array.isArray(state.overview.creative.items) ? state.overview.creative.items : [];
        state.overview.creative.items = items.filter((item) => String(item.id || "") !== String(itemId));
        state.overview.creative.project_count = state.overview.creative.items.length;
        state.overview.creative.active_projects = state.overview.creative.items.filter((item) => item.status === "drafting").length;
      }
      state.selectedBook = null;
      state.bookshelfPage = "shelf";
      state.creativeEditing = false;
      state.selectedBookSpreadIndex = 0;
      renderBookshelf();
      showToast("创作项目已删除。");
      return;
    }
    const result = await postJson("/bookshelf/delete", {
      kind,
      id: itemId,
      album_id: albumId,
      title,
      date: diaryDate,
      access_token: state.bookshelfAccessToken || state.bookshelfUnlocked?.access_token || "",
    });
    if (!result.changed) {
      showToast("没有找到要移除的书柜条目，请刷新拓展页后再试。", "error");
      if (button) {
        button.disabled = false;
        button.textContent = kind === "diary" ? "删除当前日记" : "从书柜移除";
      }
      return;
    }
    state.bookshelfUnlocked = result.bookshelf || null;
    state.bookshelfAccessToken = result.bookshelf?.access_token || state.bookshelfAccessToken || "";
    state.selectedBook = null;
    state.bookshelfPage = "shelf";
    state.selectedBookSpreadIndex = 0;
    renderBookshelf();
    showToast(kind === "diary" ? "日记已删除。" : "已从书柜移除。");
  } catch (error) {
    showToast(`移除失败：${error.message}`, "error");
    if (button) {
      button.disabled = false;
      button.textContent = kind === "diary" ? "删除当前日记" : "从书柜移除";
    }
  }
}

async function rateSelectedBookshelfItem(button = null) {
  const book = state.selectedBook || {};
  const albumId = book.album_id || button?.closest("[data-book-rating-album]")?.dataset?.bookRatingAlbum || "";
  const rating = Number(button?.dataset?.bookRating || 0);
  if (!albumId || !rating) {
    showToast("没有找到要评分的阅读记录。", "error");
    return;
  }
  const reason = "";
  await runAction(async () => {
    const result = await postJson("/bookshelf/rate", {
      album_id: albumId,
      rating,
      reason,
      access_token: state.bookshelfAccessToken || state.bookshelfUnlocked?.access_token || "",
    });
    state.bookshelfUnlocked = result.bookshelf || null;
    state.bookshelfAccessToken = result.bookshelf?.access_token || state.bookshelfAccessToken || "";
    const updated = allBookshelfBooks().find((item) => item.kind === "jm_album" && String(item.album_id || "") === String(albumId));
    if (updated) state.selectedBook = updated;
    renderBookshelf();
    state.bookshelfPage = "reader";
    renderBookDetailPanel();
  }, `已评分 ${rating}/10`, button);
}

async function updateSelectedBookshelfTags(form) {
  const book = state.selectedBook || {};
  const albumId = book.album_id || "";
  if (!albumId) {
    showToast("没有找到要编辑标签的阅读记录。", "error");
    return;
  }
  const button = form.querySelector("button[type='submit']");
  const likedTags = parseBookTagInput(form.elements.liked_tags?.value || "");
  const dislikedTags = parseBookTagInput(form.elements.disliked_tags?.value || "")
    .filter((tag) => !likedTags.some((liked) => liked.toLocaleLowerCase() === tag.toLocaleLowerCase()));
  await runAction(async () => {
    const result = await postJson("/bookshelf/tags", {
      album_id: albumId,
      liked_tags: likedTags,
      disliked_tags: dislikedTags,
      access_token: state.bookshelfAccessToken || state.bookshelfUnlocked?.access_token || "",
    });
    state.bookshelfUnlocked = result.bookshelf || null;
    state.bookshelfAccessToken = result.bookshelf?.access_token || state.bookshelfAccessToken || "";
    const updated = allBookshelfBooks().find((item) => item.kind === "jm_album" && String(item.album_id || "") === String(albumId));
    if (updated) state.selectedBook = updated;
    state.bookshelfPage = "detail";
    renderBookshelf();
    renderBookDetailPanel();
  }, "已保存标签", button);
}

function applyBookPreferenceTagPick(button) {
  const form = button.closest("[data-book-preference-form]");
  const tag = String(button.dataset.tagValue || "").trim();
  const target = button.dataset.tagTarget === "disliked" ? "disliked_tags" : "liked_tags";
  const other = target === "liked_tags" ? "disliked_tags" : "liked_tags";
  if (!form || !tag) return;
  const targetInput = form.elements[target];
  const otherInput = form.elements[other];
  if (!(targetInput instanceof HTMLInputElement) || !(otherInput instanceof HTMLInputElement)) return;
  const targetTags = parseBookTagInput(targetInput.value);
  const otherTags = parseBookTagInput(otherInput.value).filter((item) => item.toLocaleLowerCase() !== tag.toLocaleLowerCase());
  if (!targetTags.some((item) => item.toLocaleLowerCase() === tag.toLocaleLowerCase())) {
    targetTags.push(tag);
  }
  targetInput.value = targetTags.slice(0, 8).join("、");
  otherInput.value = otherTags.join("、");
}

async function rereadSelectedBookshelfItem(button = null) {
  const book = state.selectedBook || {};
  const albumId = book.album_id || "";
  if (!albumId) {
    showToast("没有找到要重读的阅读记录。", "error");
    return;
  }
  const currentPage = state.bookshelfPage;
  await runAction(async () => {
    const result = await postJson("/bookshelf/comments/update", {
      album_id: albumId,
      access_token: state.bookshelfAccessToken || state.bookshelfUnlocked?.access_token || "",
    });
    state.bookshelfUnlocked = result.bookshelf || null;
    state.bookshelfAccessToken = result.bookshelf?.access_token || state.bookshelfAccessToken || "";
    const updated = allBookshelfBooks().find((item) => item.kind === "jm_album" && String(item.album_id || "") === String(albumId));
    if (updated) state.selectedBook = updated;
    state.bookshelfPage = currentPage === "reader" ? "reader" : "detail";
    renderBookshelf();
    renderBookDetailPanel();
    void hydrateBookshelfImages($("#bookDetailPanel") || document);
  }, "", button);
}

document.addEventListener("submit", (event) => {
  const form = event.target instanceof HTMLFormElement ? event.target : null;
  if (!form || !form.matches("[data-book-preference-form]")) return;
  event.preventDefault();
  void updateSelectedBookshelfTags(form);
});

document.addEventListener("submit", async (event) => {
  const form = event.target instanceof HTMLFormElement ? event.target : null;
  if (form?.matches("[data-creative-project-form]")) {
    event.preventDefault();
    const submit = form.querySelector('button[type="submit"]');
    const formData = new FormData(form);
    const projectId = form.dataset.projectId || "";
    await runAction(async () => {
      const result = await postJson("/creative/project/update", {
        id: projectId,
        title: formData.get("title"),
        work_type: formData.get("work_type"),
        tone: formData.get("tone"),
        point_of_view: formData.get("point_of_view"),
        target_chars: Number(formData.get("target_chars") || 0),
        premise: formData.get("premise"),
        next_hint: formData.get("next_hint"),
      });
      if (state.selectedBook?.kind === "creative" && state.selectedBook.id === projectId) {
        await ensureCreativeProjectDetail(state.selectedBook, true);
      }
      return result;
    }, "已保存作品信息", submit);
    return;
  }
  if (form?.matches("[data-creative-outline-form]")) {
    event.preventDefault();
    const submit = form.querySelector('button[type="submit"]');
    const projectId = form.dataset.projectId || "";
    const formData = new FormData(form);
    await runAction(async () => {
      const result = await postJson("/creative/project/outline/update", {
        id: projectId,
        outline: formData.get("outline"),
      });
      if (state.selectedBook?.kind === "creative" && state.selectedBook.id === projectId) {
        await ensureCreativeProjectDetail(state.selectedBook, true);
      }
      return result;
    }, "已保存大纲", submit);
    return;
  }
  if (form?.matches("[data-creative-characters-form]")) {
    event.preventDefault();
    const submit = form.querySelector('button[type="submit"]');
    const projectId = form.dataset.projectId || "";
    const formData = new FormData(form);
    let characters = [];
    try {
      const raw = String(formData.get("characters") || "").trim();
      characters = raw ? JSON.parse(raw) : [];
      if (!Array.isArray(characters)) throw new Error("角色必须是 JSON 数组");
    } catch (error) {
      showToast(`角色格式错误：${error.message}`, "error");
      return;
    }
    await runAction(async () => {
      const result = await postJson("/creative/project/characters/update", {
        id: projectId,
        characters,
      });
      if (state.selectedBook?.kind === "creative" && state.selectedBook.id === projectId) {
        await ensureCreativeProjectDetail(state.selectedBook, true);
      }
      return result;
    }, "已保存角色", submit);
    return;
  }
  if (form?.matches("[data-creative-chunk-form]")) {
    event.preventDefault();
    const submit = form.querySelector('button[type="submit"]');
    const projectId = form.dataset.projectId || "";
    const chunkIndex = Number(form.dataset.chunkIndex || -1);
    const formData = new FormData(form);
    await runAction(async () => {
      const result = await postJson("/creative/project/chunk/update", {
        id: projectId,
        chunk_index: chunkIndex,
        text: formData.get("text"),
      });
      if (state.selectedBook?.kind === "creative" && state.selectedBook.id === projectId) {
        await ensureCreativeProjectDetail(state.selectedBook, true);
      }
      return result;
    }, "已保存正文片段", submit);
    return;
  }
  if (!form || !form.matches("[data-image-cache-editor]")) return;
  event.preventDefault();
  const key = form.dataset.imageCacheEditor || "";
  const submit = form.querySelector('button[type="submit"]');
  const formData = new FormData(form);
  setActionBusy(submit, true);
  try {
    const updated = await postJson("/image_cache/update", {
      key,
      text: formData.get("text"),
      scope: formData.get("scope"),
      provider_id: formData.get("provider_id"),
    });
    state.selectedImageCacheKey = updated?.key || key;
    await loadImageCache();
    showToast("图片缓存已保存");
  } catch (error) {
    showToast(`保存失败：${error.message}`, "error");
  } finally {
    setActionBusy(submit, false);
  }
});

document.addEventListener("change", (event) => {
  const target = event.target instanceof HTMLSelectElement ? event.target : null;
  if (!target || !target.matches("[data-diary-date]")) return;
  state.selectedDiaryDate = target.value;
  renderBookDetailPanel();
});

$("#refreshBtn").addEventListener("click", loadAll);

document.getElementById("appearanceFontSelect")?.addEventListener("change", async (event) => {
  const select = event.currentTarget;
  const value = String(select?.value || "original").trim().toLowerCase() === "cheng" ? "cheng" : "original";
  const previous = state.pageFontFamily;
  state.pageFontFamily = value;
  applyPageFontFamily();
  try {
    await postJson("/settings/update", { settings: { page_font_family: value } });
    showToast("页面字体已保存");
  } catch (error) {
    state.pageFontFamily = previous;
    applyPageFontFamily();
    showToast(`字体保存失败：${error.message}`, "error");
  }
});

async function savePageTheme(theme) {
  const previous = state.pageTheme;
  state.pageTheme = theme;
  applyPageTheme();
  renderAppearanceSettings();
  try {
    await postJson("/settings/update", { settings: { page_theme: theme } });
    showToast("主题已保存");
  } catch (error) {
    state.pageTheme = previous;
    applyPageTheme();
    renderAppearanceSettings();
    showToast(`主题保存失败：${error.message}`, "error");
  }
}

document.getElementById("appearanceThemeGrid")?.addEventListener("click", (event) => {
  const option = event.target.closest("[data-theme-value]");
  if (!option) return;
  savePageTheme(option.dataset.themeValue);
});

document.getElementById("appearanceThemeGrid")?.addEventListener("keydown", (event) => {
  const option = event.target.closest("[data-theme-value]");
  if (!option) return;
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    savePageTheme(option.dataset.themeValue);
  }
});

$("#refreshImageCacheBtn")?.addEventListener("click", () => {
  loadImageCache().catch((error) => showToast(`刷新失败：${error.message}`, "error"));
});
$("#refreshTroubleshootingBtn")?.addEventListener("click", () => {
  Promise.all([loadTroubleshooting(), loadDiagnostics(true)])
    .then(() => showToast("排障信息已刷新"))
    .catch((error) => showToast(`刷新失败：${error.message}`, "error"));
});
$("#imageCacheFilter")?.addEventListener("input", (event) => {
  state.imageCacheFilter = event.target.value || "";
  window.clearTimeout(state._imageCacheFilterTimer);
  state._imageCacheFilterTimer = window.setTimeout(() => {
    loadImageCache().catch((error) => showToast(`筛选失败：${error.message}`, "error"));
  }, 220);
});
$("#imageCacheScope")?.addEventListener("change", (event) => {
  state.imageCacheScope = event.target.value || "all";
  loadImageCache().catch((error) => showToast(`筛选失败：${error.message}`, "error"));
});
$("#bookshelfUnlockForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const password = $("#bookshelfPassword").value.trim();
  if (!password) {
    setBookshelfUnlockMessage("请输入密码", "error");
    return;
  }
  const button = event.currentTarget.querySelector("button[type='submit']");
  if (button) {
    button.disabled = true;
    button.textContent = "验证中...";
  }
  setBookshelfUnlockMessage("正在验证...", "");
  try {
    const result = await postJson("/bookshelf/unlock", { password });
    state.bookshelfUnlocked = result.bookshelf || null;
    state.bookshelfAccessToken = result.bookshelf?.access_token || "";
    state.selectedBook = null;
    state.bookshelfPage = "shelf";
    renderBookshelf();
    $("#bookshelfPassword").value = "";
    setBookshelfUnlockMessage("密码正确，已打开", "ok");
  } catch (error) {
    setBookshelfUnlockMessage(error.message || "密码不对", "error");
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = "打开抽屉";
    }
  }
});
$("#userFilter").addEventListener("input", renderUsers);
$("#groupFilter").addEventListener("input", renderGroups);
$("#worldbookMemberFilter").addEventListener("input", renderWorldbook);
$("#featureFilter").addEventListener("input", renderFeatureSwitches);
$("#worldbookMembers").addEventListener("click", async (event) => {
  const button = event.target instanceof Element ? event.target.closest("[data-worldbook-living-memory], [data-worldbook-living-memory-close], [data-worldbook-edit], [data-worldbook-member], [data-worldbook-save], [data-worldbook-memory-toggle], [data-worldbook-memory-delete], [data-worldbook-observation-accept], [data-worldbook-observation-reject], [data-worldbook-delete]") : null;
  if (!button) return;
  event.preventDefault();
  await handleWorldbookMemberAction(button);
});
$("#worldbookGroups").addEventListener("click", async (event) => {
  const button = event.target instanceof Element ? event.target.closest("[data-worldbook-group-save], [data-worldbook-group-delete]") : null;
  if (!button) return;
  event.preventDefault();
  await handleWorldbookGroupAction(button);
});
$("#worldbookImportBtn").addEventListener("click", async () => {
  await runAction(() => postJson("/worldbook/import", {}), "已刷新关系网", $("#worldbookImportBtn"));
});
$("#worldbookClearPendingBtn").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  if (!requireSecondClick(button, "worldbook-clear-pending", "再次点击清理所有待确认观察", "再次点击清理")) return;
  await runAction(() => postJson("/worldbook/observations/clear", {}), "", button);
  button.disabled = !((state.overview?.worldbook?.pending_observation_total || 0) > 0);
});
$("#worldbookAddMemberForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const userId = String(form.get("user_id") || "").trim();
  if (!userId) return;
  const validId = /^\d{5,}$/.test(userId) || /^bili:\d{2,}$/i.test(userId) || /^bili_live_[A-Za-z0-9_-]{6,64}$/.test(userId);
  if (!validId) {
    alert("关系节点必须使用有效 QQ 号或 B 站外部身份键");
    return;
  }
  await runAction(() => postJson("/worldbook/member/update", {
    user_id: userId,
    name: form.get("name") || userId,
    priority: Number(form.get("priority") || 120),
    enabled: true,
  }), "已添加关系节点", event.submitter);
  event.currentTarget.reset();
});
$("#worldbookAddGroupForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const groupId = String(form.get("group_id") || "").trim();
  if (!groupId) return;
  await runAction(() => postJson("/worldbook/group/update", {
    group_id: groupId,
    name: form.get("name") || groupId,
    enabled: true,
  }), "已添加群资料", event.submitter);
  event.currentTarget.reset();
});
$("#skillAddForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const name = String(form.get("name") || "").trim();
  if (!name) return;
  const level = Number(form.get("level") || 1);
  await runAction(() => postJson("/skill/update", {
    name,
    category: form.get("category") || "能力",
    level,
    exp: skillExpFloor(level),
    keywords: form.get("keywords") || name,
    aliases: form.get("aliases") || "",
    hidden: false,
    frozen: false,
  }), "已添加技能", event.submitter);
  event.currentTarget.reset();
});
$("#resetTokenStatsBtn").addEventListener("click", async () => {
  const button = $("#resetTokenStatsBtn");
  if (!requireSecondClick(button, "token-reset", "再次点击清空 Token 统计", "再次点击清空")) return;
  await runAction(() => postJson("/token/reset", {}), "已清空 Token 统计", button);
});
document.querySelectorAll("[data-token-view]").forEach((button) => {
  button.addEventListener("click", () => {
    state.tokenView = button.dataset.tokenView || "today";
    renderTokens();
  });
});
$("#tokenDateSelect").addEventListener("change", (event) => {
  state.tokenDate = event.target.value;
  state.tokenView = "date";
  renderTokens();
});

document.addEventListener("submit", async (event) => {
  const form = event.target instanceof HTMLFormElement ? event.target.closest("[data-external-ability-form]") : null;
  if (!form) return;
  event.preventDefault();
  const button = event.submitter || form.querySelector("button[type='submit']");
  const name = form.dataset.externalAbilityForm || "";
  let config = {};
  const rawConfig = form.querySelector('[name="config"]')?.value || "{}";
  try {
    config = JSON.parse(rawConfig || "{}");
  } catch (error) {
    showToast("自定义配置必须是 JSON 对象", "error");
    return;
  }
  if (!config || typeof config !== "object" || Array.isArray(config)) {
    showToast("自定义配置必须是 JSON 对象", "error");
    return;
  }
  await runAction(() => postJson("/external_ability/update", {
    name,
    enabled: Boolean(form.querySelector('[name="enabled"]')?.checked),
    share_probability: Math.max(0, Math.min(1, Number(form.querySelector('[name="share_probability"]')?.value || 0) / 100)),
    min_interval_hours: Number(form.querySelector('[name="min_interval_hours"]')?.value || 0),
    config,
  }), "已保存外部主动能力", button);
});

document.addEventListener("click", async (event) => {
  const addType = event.target?.dataset?.newsSourceAdd;
  if (addType) {
    const items = newsSourceItemsFromDom();
    items.push({
      enabled: true,
      name: newsSourceTypeLabel(addType),
      type: addType,
      target: addType === "bilibili" ? "bilibili:" : (addType === "bilibili_video" ? "bvid:" : "https://"),
    });
    renderNewsSourceManager(items);
    syncNewsSourcesRaw();
    return;
  }
  if (event.target?.dataset?.newsSourceReset !== undefined) {
    resetNewsSourcesToDefault();
    return;
  }
  const removeIndex = event.target?.dataset?.newsSourceRemove;
  if (removeIndex !== undefined) {
    const index = Number(removeIndex);
    const items = newsSourceItemsFromDom().filter((_, itemIndex) => itemIndex !== index);
    renderNewsSourceManager(items);
    syncNewsSourcesRaw();
    return;
  }
  const saveNewsSources = event.target?.closest?.("[data-save-news-sources]");
  if (saveNewsSources) {
    syncNewsSourcesRaw();
    const raw = $("#newsSourcesRaw");
    await runAction(() => postJson("/settings/update", {
      settings: { news_sources: raw?.value || "" },
    }), "已保存新闻源", saveNewsSources);
  }
});

document.addEventListener("input", (event) => {
  if (event.target?.closest?.("#newsSourceManager")) syncNewsSourcesRaw();
});

document.addEventListener("change", (event) => {
  if (!event.target?.closest?.("#newsSourceManager")) return;
  if (event.target.matches("[data-news-source-type]")) {
    const row = event.target.closest("[data-news-source-item]");
    const input = row?.querySelector("[data-news-source-target]");
    if (input && !String(input.value || "").trim()) {
      input.value = event.target.value === "bilibili" ? "bilibili:" : (event.target.value === "bilibili_video" ? "bvid:" : "https://");
    }
    if (input) input.placeholder = newsSourcePlaceholder(event.target.value);
  }
  syncNewsSourcesRaw();
});

bindRoleplayModeSwitch();
bindProviderToolbar();

["roleplayProfileForm", "privateAliasForm", "quickModuleForm", "environmentModuleForm", "privateModuleForm", "groupModuleForm", "worldbookModuleForm", "memoryModuleForm", "longTermModuleForm"].forEach((formId) => {
  const form = document.getElementById(formId);
  if (!form) return;
  form.addEventListener("input", () => markModuleFormDirty(form));
  form.addEventListener("change", () => markModuleFormDirty(form));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
  await runAction(() => postJson("/settings/update", {
    settings: collectFormSettings(`#${formId}`),
  }), "已保存模块调参", event.submitter);
    markModuleFormClean(form);
    if (formId === "privateAliasForm") {
      await loadAll();
    }
  });
});
bindSegmentedPreview();

$("#addUserForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const userId = String(form.get("user_id") || "").trim();
  if (!userId) return;
  state.selectedUserId = userId;
  await runAction(() => postJson("/user/update", {
    user_id: userId,
    enabled: true,
    nickname: form.get("nickname") || "",
  }), "已添加私聊对象", event.submitter);
  event.currentTarget.reset();
});

$("#addGroupForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const groupId = String(form.get("group_id") || "").trim();
  const listMode = String(form.get("list_mode") || "none");
  if (!groupId) return;
  state.selectedGroupId = groupId;
  await runAction(async () => {
    await postJson("/group/update", { group_id: groupId, enabled: true });
    if (listMode !== "none") {
      const group = state.overview?.group || {};
      const whitelist = new Set(group.whitelist || []);
      const blacklist = new Set(group.blacklist || []);
      if (listMode === "whitelist") whitelist.add(groupId);
      if (listMode === "blacklist") blacklist.add(groupId);
      await postJson("/settings/update", {
        group_whitelist_ids: [...whitelist],
        group_blacklist_ids: [...blacklist],
      });
    }
  }, "已添加群聊观测", event.submitter);
  event.currentTarget.reset();
});

$("#exportSnapshotBtn").addEventListener("click", async () => {
  await runAction(async () => {
    const params = new URLSearchParams();
    params.set("sections", "basic,relations,food_skills,providers");
    const snapshot = await fetchJson(`/config/export?${params.toString()}`);
    const date = new Date().toISOString().slice(0, 10);
    downloadJson(`private-companion-snapshot-${date}.json`, snapshot);
  }, "快照已导出，可在配置迁移中导入", $("#exportSnapshotBtn"));
});

$("#exportConfigBtn").addEventListener("click", async () => {
  await runAction(handleConfigExport, "", $("#exportConfigBtn"));
});

$("#importConfigFile").addEventListener("change", async (event) => {
  const file = event.target.files?.[0];
  if (!file) return;
  try {
    await readConfigImportFile(file);
  } catch (error) {
    state.configImportPackage = null;
    state.configImportPreview = null;
    renderConfigMigrationPreview();
    showToast(`读取失败：${error.message}`, "error");
  }
});

$("#previewConfigImportBtn").addEventListener("click", async () => {
  await runAction(previewConfigImport, "", $("#previewConfigImportBtn"));
});

$("#applyConfigImportBtn").addEventListener("click", async () => {
  await runAction(applyConfigImport, "", $("#applyConfigImportBtn"));
});

$("#configImportMode").addEventListener("change", () => {
  renderConfigMigrationPreview();
});

$("#configAllowChecksumMismatch").addEventListener("change", () => {
  state.configImportPreview = null;
  renderConfigMigrationPreview();
});

$("#storageBackendSelect").addEventListener("change", () => {
  renderStorageConfig({ preserveDraft: true });
});

$("#storageConfigForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const backend = String($("#storageBackendSelect")?.value || "json").trim().toLowerCase() || "json";
  const sqlitePath = String($("#storageSqlitePathInput")?.value || "").trim();
  await runAction(
    () => postJson("/settings/update", {
      settings: {
        storage_backend: backend,
        storage_sqlite_path: sqlitePath,
      },
    }),
    "已保存存储方式",
    event.submitter || $("#saveStorageConfigBtn")
  );
});

$("#configBackupList").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-config-restore]");
  if (!button) return;
  await runAction(() => restoreConfigBackup(button.dataset.configRestore), "", button);
});

$("#accessForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const draft = accessDraftFromForm(state.overview?.group || {});
  writeAccessDraft(draft);
  await runAction(() => postJson("/settings/update", {
    group_access_mode: draft.mode,
    group_whitelist_ids: [...draft.whitelist],
    group_blacklist_ids: [...draft.blacklist],
  }), "已保存群聊名单", event.submitter);
});

$("#groupAccessMode").addEventListener("change", () => {
  renderAccessManager(state.overview?.group || {});
});

document.querySelectorAll("[name='groupAccessModeChoice']").forEach((input) => {
  input.addEventListener("change", () => {
    $("#groupAccessMode").value = input.value;
    renderAccessManager(state.overview?.group || {});
  });
});

$("#accessQuickGroups").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-access-action]");
  if (!button) return;
  const groupId = String(button.dataset.accessGroup || "").trim();
  if (!groupId) return;
  const draft = accessDraftFromForm(state.overview?.group || {});
  const targetSet = button.dataset.accessAction === "black" ? draft.blacklist : draft.whitelist;
  if (targetSet.has(groupId)) {
    targetSet.delete(groupId);
  } else {
    targetSet.add(groupId);
    if (button.dataset.accessAction === "black") {
      draft.whitelist.delete(groupId);
    } else {
      draft.blacklist.delete(groupId);
    }
  }
  writeAccessDraft(draft);
  renderAccessManager(state.overview?.group || {});
  await runAction(() => postJson("/settings/update", {
    group_access_mode: draft.mode,
    group_whitelist_ids: [...draft.whitelist],
    group_blacklist_ids: [...draft.blacklist],
  }), "已更新群聊名单", button);
});

$("#saveFeaturesBtn").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  if (button?.dataset?.action === "back" || (state.selectedFeatureKey && Object.prototype.hasOwnProperty.call(state.featureDraft || {}, state.selectedFeatureKey))) {
    const saved = await saveCurrentFeatureDetail(button, "已保存并返回功能列表");
    if (!saved) return;
    state.selectedFeatureKey = "";
    renderFeatureSwitches();
    return;
  }
  const overviewSettings = state.overview?.settings || {};
  const features = {};
  const settings = {};
  Object.entries(state.featureDraft).forEach(([key, value]) => {
    if (!visibleConfigKey(key)) return;
    if (Object.prototype.hasOwnProperty.call(overviewSettings, key)) {
      settings[key] = toBool(value);
    } else {
      features[key] = toBool(value);
    }
  });
  const proactiveIntensitySelect = document.querySelector('[data-proactive-intensity-form] [name="proactive_intensity_preset"]');
  if (proactiveIntensitySelect) {
    settings.proactive_intensity_preset = proactiveIntensitySelect.value || "off";
  }
  await runAction(() => postJson("/settings/update", { features, settings }), "已保存功能开关", button);
});

$("#enableSafeFeaturesBtn").addEventListener("click", () => {
  safeFeatureKeys.forEach((key) => {
    if (Object.prototype.hasOwnProperty.call(state.featureDraft, key) && !featureLockedByProactiveOnlyMode(key)) {
      state.featureDraft[key] = true;
    }
  });
  renderFeatureSwitches();
});

$("#saveProvidersBtn").addEventListener("click", async () => {
  const values = currentProviderValues();
  const provider_config_mode = currentProviderConfigMode();
  const providers = {};
  Object.keys(providerLabels).forEach((key) => {
    if (visibleConfigKey(key)) providers[key] = values[key] || "";
  });
  await runAction(
    () => postJson("/settings/update", { settings: { provider_config_mode }, providers, overwrite_provider_modes: true }),
    "已保存模型配置，并覆盖 quick / precision 两套分流",
    $("#saveProvidersBtn")
  );
  state.overview = state.overview || {};
  state.overview.settings = { ...(state.overview.settings || {}), provider_config_mode };
  state.providerDraft = { ...state.providerDraft, ...providers };
  state.providerConfigMode = provider_config_mode;
  renderProviders();
});

$("#testAllProvidersBtn").addEventListener("click", async () => {
  for (const key of Object.keys(providerLabels).filter((item) => visibleConfigKey(item) && providerAllowedInCurrentMode(item))) {
    await testProvider(key);
  }
});

loadAll();
