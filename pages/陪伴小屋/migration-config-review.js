/* Reviewed upgrade outcomes. An audit entry is not necessarily a user task. */
"use strict";
window.CompanionMigrationConfigReview = (() => {
  const editors = {
    models: {title: "模型任务设置", location: "模型与连接 → 功能任务", mount: "models"},
    news: {title: "资讯来源设置", location: "主动联系 → 资讯", scope: "installation", kinds: ["proactive.contact"],
      views: {"proactive.contact": {sectionIds: ["news"], navigationLimit: 0}}},
    expression: {title: "表达学习设置", location: "学习与反思 → 角色策略 → 表达学习策略", kinds: ["expression.learning"],
      views: {"expression.learning": {sectionIds: ["behavior"], navigationLimit: 0}}},
  };
  const entries = {};
  const register = (sources, outcome, title, summary, editor = "") => {
    for (const source of sources) entries[source] = {outcome, title, summary, editor};
  };
  const model = (sources, title, summary) => register(sources, "settings", title, summary, "models");
  model(["model_assignment_config.enable_deepseek_peak_replacement",
    "model_assignment_config.DEEPSEEK_PEAK_REPLACEMENT_PROVIDER_ID",
    "model_assignment_config.deepseek_peak_windows", "model_assignment_config.deepseek_peak_timezone"],
    "模型时段替换", "旧时段规则未自动接续。请为需要的任务选择替代模型、时段和时区。");
  register(["model_assignment_config.deepseek_peak_match_keywords"], "mechanism", "旧模型识别关键词",
    "新版直接使用任务的时段替换规则，不再按这些关键词识别模型。旧关键词未沿用。", "models");
  model(["model_assignment_config.FAST_RESPONSE_PROVIDER_ID"], "快速回复模型",
    "旧快捷档没有唯一对应任务。请在功能任务中选择需要使用该模型的任务。");
  model(["model_assignment_config.COMPLEX_REASONING_PROVIDER_ID"], "复杂推理模型",
    "旧推理档没有唯一对应任务。请在功能任务中分别设置主模型与备用模型。");
  model(["external_memory_config.new_framework_s4_provider_id"], "后台记忆整理模型",
    "旧后台模型未自动分配。请核对记忆整理等后台任务使用的模型。");
  model(["model_token_limit_overrides"], "任务输出上限", "旧覆盖表未自动转换。请在对应任务中设置输出上限。");
  model(["model_fallback_overrides"], "任务备用模型", "旧覆盖表未自动转换。请在对应任务中设置备用模型与替代规则。");
  model(["task_prompt_overrides"], "任务提示词", "旧任务提示词未自动转换。请在对应任务中核对提示词。");
  model(["model_assignment_config.DREAM_DIARY_PROVIDER_ID", "legacy_compat_config.DIARY_PROVIDER_ID",
    "legacy_compat_config.DREAM_PROVIDER_ID"], "日记与梦境模型",
    "旧模型档与新版任务拆分不同。请分别核对日记、梦境及相关任务使用的模型。");
  model(["web_exploration_config.WEB_EXPLORATION_PROVIDER_ID"], "见闻分享模型",
    "旧浏览模型未自动分配。主动分享内容由主动联系任务生成，请核对该任务的模型。");
  register(["news_config.enable_ai_daily_watch", "news_config.news_sources"], "settings", "资讯订阅来源",
    "旧订阅未完整接续。请重新选择资讯来源；当前只支持一个来源，不能等价保留旧版多来源订阅。", "news");
  register(["memory_habit_config.expression_group_application_mode",
    "memory_habit_config.expression_group_application_ids"], "settings", "群聊表达应用范围",
    "旧群白名单未转成新版设置。可调整角色的群聊表达应用开关；该开关不能代替逐群白名单。", "expression");
  register(["forward_message_config.forward_message_max_chars"], "mechanism", "合并转发文本限制",
    "新版统一检查整轮输入容量，不再提供旧转发字数上限。旧数值未沿用，无需确认。");
  register(["forward_message_config.forward_message_image_limit"], "mechanism", "合并转发图片限制",
    "新版按整轮媒体容量处理图片，不再单独设置转发图片张数。旧数值未沿用，无需确认。");
  const unsupported = {
    "message_debounce_config.SMART_MESSAGE_DEBOUNCE_PROVIDER_ID": ["智能收口专用模型", "当前没有旧智能收口任务的等价模型配置。"],
    "forward_message_config.forward_message_image_vision_timeout_seconds": ["转发图片视觉超时", "当前没有旧转发视觉步骤的等价超时配置。"],
    "photo_action_config.enable_p5_structured_reference_assets": ["旧结构化参考素材开关", "此旧开关尚无经过核实的转换规则。"],
    "reactive_poke_config.reactive_poke_super_poke_probability": ["超级反戳概率", "当前不支持旧版超级反戳。"],
    "reactive_poke_config.reactive_poke_super_poke_times": ["连续反戳次数", "当前不支持旧版连续反戳。"],
    "reactive_poke_config.reactive_poke_interval": ["连续反戳间隔", "当前不支持旧版连续反戳。"],
    "reactive_poke_config.reactive_poke_prompts": ["被戳模型提示词", "当前被戳回复不使用旧独立模型提示词。"],
    "reactive_poke_config.reactive_poke_back_prompts": ["反戳模型提示词", "当前反戳只发送平台动作，没有等价的提示词配置。"],
    "auto_voice_config.main_user_voice_probability": ["核心用户语音概率", "此旧概率尚无经过核实的转换规则。"],
  };
  for (const [source, [title, summary]] of Object.entries(unsupported)) register([source], "unsupported", title, summary);
  for (const [key, title] of Object.entries({enable_group_high_intensity_mode: "群聊高强度收口",
    group_high_intensity_wakeup_window_seconds: "收口唤醒统计时长", group_high_intensity_wakeup_threshold: "收口唤醒阈值",
    group_high_intensity_cooldown_seconds: "收口冷却时长", group_high_intensity_merge_seconds: "收口合并时长",
    group_high_intensity_max_merge_messages: "收口合并条数", group_high_intensity_merge_scope: "收口合并范围"})) {
    register([`group_high_intensity_config.${key}`], "unsupported", title, "当前没有旧版高强度收口的等价配置。");
  }
  register(["legacy_compat_config.segmented_proactive_private_send_as_forward",
    "legacy_compat_config.segmented_proactive_group_send_as_forward", "legacy_compat_config.segmented_proactive_send_as_forward"],
    "unsupported", "主动消息合并转发", "当前主动分段逐条投递，不支持旧版合并转发形式。");

  const categories = [
    {id: "settings", title: "可手动调整", note: "点击设置，在本页调整后返回；不会自动确认或改写配置。", attention: true},
    {id: "unsupported", title: "暂不支持的旧设置", note: "这些项目没有等价配置入口，需要后续功能支持；你无需补填。"},
    {id: "mechanism", title: "改由新版机制处理", note: "相关功能使用新版机制，不代表旧值已迁入；无需逐项确认。"},
    {id: "retained", title: "未沿用的旧设置", note: "旧配置保留在来源中，以下值未写入当前设置，不会按旧值运行。"},
    {id: "inapplicable", title: "当前不适用", note: "这些旧设置不适用于当前版本，无需处理。"},
    {id: "unknown", title: "尚待核实", note: "当前无法确认这些项目的接续方式，不计为迁移成功。"},
  ];
  function describe(item) {
    const reviewed = entries[item.source_key];
    if (reviewed) return {...reviewed};
    const outcome = {manual_required: "unsupported", manual_mapping: "unknown",
      implemented_elsewhere: "mechanism", not_migrated: "retained", not_applicable: "inapplicable"}[item.disposition] || "unknown";
    return {outcome, title: item.source_key || item.field || "配置项", summary: "", editor: ""};
  }
  return {describe, categories, editor: key => Object.hasOwn(editors, key) ? editors[key] : null};
})();
