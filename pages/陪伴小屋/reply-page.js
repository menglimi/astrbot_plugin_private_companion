/* 回复设置适配器：表达页保留离线预览，决策页只挂载群聊判断分区。
   此前这里自带一整套字段渲染、草稿存储、幂等重试与版本冲突处理；设置部分现在
   归 workbench-page.js，本文件保留工作台不做的离线分段预览。

   两个视图仍读写同一个 conversation.presentation 配置合同。
   接口为 mount(element, {view}) / unmount()，app.js 负责按页面归属选择视图。
   读取端点走 reply 路由族（{reply_api}），载荷扁平：{settings, edit_token,
   tts, ...}，不带外层 sections。 */
"use strict";
window.CompanionReplyPage = (() => {
  const PREFIX = "/astrbot_plugin_private_companion/";
  const GROUP = "conversation.presentation";

  /* 枚举选项：注册表里这些字段是 TEXT，约束在 reply_settings.validate 里。
     选项必须与后端常量一致，否则保存会被拒绝。 */
  const choices = {
    group_tool_profile: {full: "完整工具", standard: "常用工具", lean: "精简工具"},
      streaming_mode: {buffered: "收齐后分段发送", incremental: "边生成边分段发送"},
      rule_mode: {words: "词列表", regex: "正则表达式", sentences: "按句子", paragraphs: "按段落"},
      part_gap_mode: {fixed: "固定", random: "随机", log: "按文本长度"},
    prompt_style_mode: {constraint: "约束", free: "自由", guided: "引导"},
    content_cleanup_mode: {words: "按清理词", regex: "按正则"},
    content_cleanup_scope: {leading: "句首", middle: "句中", trailing: "句尾"},
    recall_forbidden_scope: {bot_only: "角色自己的回复", group_only: "群聊回复", bot_and_group: "角色回复与群聊"},
    image_position: {after_text: "文字后", before_text: "文字前"},
    voice_trigger_mode: {always: "每轮", request: "按请求", adaptive: "模型择句"},
    voice_generation_mode: {plain: "朗读正文", model_tag: "模型语音标签"},
    voice_conversion_scope: {full: "完整口述", partial: "选择适合口述的短句"},
    voice_segment_mode: {whole: "整段语音", each: "每一段", first: "仅首段", last: "仅末段"},
    voice_delivery_mode: {voice_and_text: "文字与语音", voice_only: "仅语音（完整覆盖时）"},
    voice_position: {before_text: "文字前", after_text: "文字后", after_media: "所有组件后"},
    voice_language: {zh: "中文", ja: "日语", en: "英语"},
    voice_foreign_text_mode: {original: "原文", translation: "中文译文", bilingual: "双语"},
    file_understanding_formats: {text_only: "纯文本", text_and_documents: "文本与文档"},
  };

  const spec = {
    title: "回复表达与投递",
    note: "",
    labels: {
      enabled: "启用分段", streaming: "启用模型流式传输", streaming_mode: "流式分段发送方式", llm_controlled: "由模型决定边界",
      rule_mode: "规则分段方式", threshold: "规则分段字数阈值",
      min_segment_chars: "短段合并参考字数", max_segments: "最多消息条数", part_gap_ms: "固定段间隔（毫秒）",
      part_gap_mode: "分段间隔方式", part_gap_min_ms: "随机间隔下限（毫秒）",
      part_gap_max_ms: "随机间隔上限（毫秒）", part_gap_log_base: "按长度调节强度",
      image_position: "图片位置", prompt: "模型分段提示词", split_words: "分段标记词",
      match_width_variants: "兼容全角与半角标点", rule_regex: "分段正则表达式",
      prompt_style_mode: "提示词风格",
      enable_humanized_expression: "追加拟人表达提示",
      humanized_expression_prompt: "额外表达提示正文",
      style_prompt: "表达风格提示词",
      content_cleanup_enabled: "启用文字清理",
      content_cleanup_mode: "清理方式", content_cleanup_scope: "清理范围",
      content_cleanup_rule: "清理正则", content_cleanup_words: "清理词列表",
      content_replacement_enabled: "启用文字替换", content_replacements: "替换规则",
      framework_error_guard_enabled: "清理框架内部标记",
      enable_forbidden_word_recall: "启用命中词拦截",
      recall_forbidden_words: "拦截词语列表", recall_forbidden_scope: "拦截范围",
      recall_forbidden_word_case_sensitive: "区分大小写",
      reply_diagnostic_limit: "近期回复诊断条数",
      group_wakeup_enabled: "启用群聊回应判断",
      group_tool_profile: "群聊工具档位",
      group_wakeup_direct_words: "直呼词", group_wakeup_context_words: "上下文关联词",
      group_wakeup_interest_keywords: "兴趣关键词",
      group_wakeup_question_enabled: "识别问题与求助",
      group_wakeup_bot_name_enabled: "把角色名作为直呼词",
      group_wakeup_bot_name: "角色在群里的名称",
      group_wakeup_primary_member_ids: "主要成员账号 ID",
      group_wakeup_owner_direct_words: "主要成员专属直呼词",
      group_wakeup_interest_probability: "兴趣候选抽取概率",
      group_wakeup_question_threshold: "问题与求助阈值",
      group_wakeup_cold_group_enabled: "启用冷群重启判断",
      group_wakeup_cold_group_threshold: "冷群重启阈值",
      group_wakeup_cold_group_idle_minutes: "冷群静默时长（分钟）",
      group_wakeup_cooldown_seconds: "兴趣话题冷却（秒）",
      group_wakeup_fatigue_limit: "参与疲劳上限",
      group_wakeup_fatigue_decay_minutes: "疲劳恢复间隔（分钟）",
      group_wakeup_generated_keyword_limit: "记忆兴趣词上限",
      group_wakeup_topic_interest_max_boost: "话题兴趣加权上限（%）",
      group_wakeup_debounce_pending_penalty: "待回复期间兴趣降权（%）",
      group_wakeup_image_enabled: "考虑本轮图片相关性",
      group_wakeup_image_weight: "图片候选抽取概率",
      group_wakeup_short_text_wait_ms: "短消息合并等待（毫秒）",
      group_wakeup_log_limit: "近期判定记录上限",
      group_air_guard_enabled: "启用空气护栏",
      group_high_intensity_enabled: "启用高强度硬收口",
      recall_silence_enabled: "启用撤回沉默",
      private_closing_guard_enabled: "启用私聊收尾沉默",
      private_closing_guard_window_seconds: "收尾判断窗口（秒）",
      private_closing_guard_prompt_enabled: "同时提示模型自然收尾",
      private_debounce_enabled: "启用私聊连续输入防抖",
      private_debounce_min_ms: "最短静默（毫秒）",
      private_debounce_max_ms: "最长静默（毫秒）",
      private_debounce_total_ms: "单轮总上限（毫秒）",
      group_conversation_followup_enabled: "允许非点名续接",
      group_conversation_followup_seconds: "续接有效时间（秒）",
      group_conversation_followup_max_turns: "每次唤醒最多自动续接轮数",
      group_air_guard_window_seconds: "接话统计窗口（秒）",
      group_air_guard_max_bot_replies: "窗口内连续回复参考上限",
      group_air_guard_cooldown_seconds: "密集接话收口期（秒）",
      group_air_guard_polite_loop_limit: "礼貌收尾参考上限",
      enable_group_injection_guard: "群聊提示注入防护",
      enable_group_privacy_guard: "群聊隐私防护",
      enable_group_reality_promise_guard: "群聊现实承诺防护",
      enable_context_image_captioning: "启用上下文图片描述",
      context_image_caption_max_items: "单轮描述图片上限",
      enable_private_image_self_recognition: "启用当前窗口图片转述增强",
      image_understanding_private_enabled: "图片理解 · 私聊",
      image_understanding_group_enabled: "图片理解 · 群聊",
      astrbot_animated_image_understanding_enabled: "AstrBot 原生动图理解优化",
      enable_private_image_vision_cache: "复用重复图片转述",
      private_image_vision_cache_max_items: "缓存条目上限",
      private_image_self_recognition_hint: "角色自我识别线索",
      private_image_vision_custom_prompt: "转述提示词补充",
      private_image_vision_max_chars: "单轮转述长度上限",
      private_image_vision_wait_seconds: "等待识图时间（秒）",
      private_image_provider_failure_cooldown_seconds: "识图失败冷却（秒）",
      video_understanding_enabled: "启用视频理解",
      video_understanding_private_enabled: "视频理解 · 私聊",
      video_understanding_group_enabled: "视频理解 · 群聊",
      video_understanding_max_chars: "转述长度上限",
      video_understanding_wait_seconds: "等待转述时间（秒）",
      video_self_recognition_hint: "视频中的角色自我识别线索",
      video_understanding_prompt: "转述提示词补充",
      file_understanding_enabled: "启用文件理解",
      file_understanding_private_enabled: "文件理解 · 私聊",
      file_understanding_group_enabled: "文件理解 · 群聊",
      file_understanding_formats: "可读取文件类型",
      file_understanding_format_groups: "可读取文件格式",
      file_understanding_max_size_mb: "单个文件上限（MB）",
      file_understanding_max_chars: "本轮正文上限",
      file_understanding_timeout_seconds: "文件读取超时（秒）",
      file_understanding_prompt: "文件阅读偏好",
      forward_message_adaptation_enabled: "合并转发消息改写",
      component_order: "消息组件顺序",
      reply_quote_enabled: "引用触发消息", reply_quote_group_enabled: "群聊中引用",
      reply_quote_first_part_only: "只在第一段引用", reply_quote_skip_chars: "短回复字数门槛",
      reply_mention_speaker_enabled: "群聊回复时 @ 发言者",
      voice_enabled: "启用语音", voice_trigger_mode: "触发方式", voice_generation_mode: "朗读内容",
      voice_conversion_scope: "语音转换范围",
      voice_segment_mode: "朗读段落（整段会等文字收齐后合成一次）", voice_delivery_mode: "发送形式", voice_position: "语音位置",
      voice_language: "语音语言", voice_foreign_text_mode: "外语文字",
      voice_provider_id: "通用 TTS 扩展", voice_zh_provider_id: "中文 TTS 扩展",
      voice_ja_provider_id: "日语 TTS 扩展", voice_en_provider_id: "英语 TTS 扩展",
      voice_private_zh_provider_id: "私聊 · 中文 TTS", voice_private_ja_provider_id: "私聊 · 日语 TTS",
      voice_private_en_provider_id: "私聊 · 英语 TTS", voice_group_zh_provider_id: "群聊 · 中文 TTS",
      voice_group_ja_provider_id: "群聊 · 日语 TTS", voice_group_en_provider_id: "群聊 · 英语 TTS",
      voice_probability: "触发概率", voice_cooldown_seconds: "冷却时间（秒）",
      voice_max_speech_chars: "单段朗读上限（字）", voice_timeout_seconds: "合成超时（秒）",
      voice_trigger_keywords: "触发关键词", voice_extra_prompt: "语音表达偏好",
      voice_primary_mention_keywords: "核心用户提及语音词",
      voice_primary_mention_probability: "核心用户提及概率",
      voice_primary_mention_prompt: "核心用户提及提示",
      voice_enabled: "核心管理员可以在本轮消息中直接指定语音模型、音色或语种；该次回复会临时覆盖保存的语音绑定。",
      voice_provider_id: "角色级 TTS 绑定，私聊与群聊共用；触发概率和冷却才按渠道分别覆盖。",
      voice_zh_provider_id: "中文语音优先使用此扩展；留空则跟随通用 TTS 扩展。",
      voice_ja_provider_id: "日语语音优先使用此扩展；留空则跟随通用 TTS 扩展。",
      voice_en_provider_id: "英语语音优先使用此扩展；留空则跟随通用 TTS 扩展。",
      voice_private_zh_provider_id: "可选：私聊中文语音专用 TTS；优先于通用和语种绑定。",
      voice_private_ja_provider_id: "可选：私聊日语语音专用 TTS；优先于通用和语种绑定。",
      voice_private_en_provider_id: "可选：私聊英语语音专用 TTS；优先于通用和语种绑定。",
      voice_group_zh_provider_id: "可选：群聊中文语音专用 TTS；优先于通用和语种绑定。",
      voice_group_ja_provider_id: "可选：群聊日语语音专用 TTS；优先于通用和语种绑定。",
      voice_group_en_provider_id: "可选：群聊英语语音专用 TTS；优先于通用和语种绑定。",
    },
    helps: {
      enabled: "让一轮回复自然地分成多条消息。",
      streaming: "按片段接收模型响应。",
      streaming_mode: "收齐后发送，或按分段词与正则边生成边发送；语音和图片组合回复收齐后发送。",
      llm_controlled: "使用独立一行的分段标记，普通换行不会自动拆分。",
      threshold: "规则分段只处理总字数不超过此值的回复；模型明确标出的边界仍会保留。规则补充分段始终开启。",
      min_segment_chars: "规则拆出的短片段会并入相邻消息；“嗯”“哈哈”等短回应也会合并。",
      max_segments: "模型边界与规则分段共用此上限；超出时合并相邻消息并保留全部正文。",
      part_gap_mode: "固定间隔沿用当前数值；随机间隔在范围内稳定抽取；按长度调节会让较长段落多停留一会儿。",
      part_gap_ms: "固定模式下每条分段之间等待的时间；0 表示不等待。",
      part_gap_min_ms: "随机模式的最短等待时间。",
      part_gap_max_ms: "随机模式的最长等待时间。",
      part_gap_log_base: "按长度调节的曲线基数；数值越接近 1，长段与短段的间隔差异越明显。",
      prompt: "模型自主决定边界时使用。最多 4000 字；可用 {{split_marker}} 表示边界。预览不调用模型，可用“模型边界示例”检查标记后的最终分段。",
      rule_mode: "选择分段词、分段正则或句子/段落规则；规则补充分段始终生效。",
      split_words: "输入标点或其他分隔词，回车添加；换行和空格可从常用分段词直接添加。",
      match_width_variants: "词列表模式下，把常见全角、半角标点视为同一分段标记。",
      rule_regex: "分段方式选择“正则表达式”时使用；支持服务端正则语法，最多 512 字。",
      style_prompt: "补充此对象的日常表达偏好；留空时不增加额外风格要求。",
      prompt_style_mode: "自由不追加通用风格提示；引导给出简短方向；约束使用详细风格提示。当前人格、你填写的表达偏好、任务格式和事实权限边界不受此项削弱。默认自由。",
      enable_humanized_expression: "勾选后，下面的额外表达提示会与所选风格叠加；自由模式下关闭即可移除这段提示。",
      humanized_expression_prompt: "最多 4000 字。约束模式使用此正文；留空使用内置默认文本。勾选“追加拟人表达提示”后，在自由或引导模式也会额外注入。本对象与群聊各自保存。",
      content_cleanup_enabled: "用于主动消息和普通私聊、群聊的文字回复，默认开启。发送前按所选方式整理文字；关闭时不执行清理。需要调整说法时优先补充上方提示词。",
      content_cleanup_scope: "可多选：句首处理每段开头，句中处理不贴首尾的匹配，句尾处理每段末尾；勾选需要的位置即可，支持任意组合。",
      content_cleanup_words: "按所选位置逐字清理词条。例如填入“。”和“，”并选择“句尾”，即可保留句中标点、清理每段末尾；也可添加空格、换行或制表符。",
      content_cleanup_rule: "仅在清理方式选为正则时使用；最多 512 字。",
      content_replacements: "逐项填写原词和替换文本。",
      framework_error_guard_enabled: "清理插件与框架内部标记，避免把内部格式带入回复。",
      enable_forbidden_word_recall: "命中时在发送前拦截这条回复，不会删除已发送消息。若只是希望换种表达，优先调整提示词。",
      recall_forbidden_words: "命中后会在发送前拦截整条回复。",
      recall_forbidden_scope: "可只检查私聊、只检查群聊，或两者都检查。",
      recall_forbidden_word_case_sensitive: "关闭时不区分英文字母大小写。",
      reply_diagnostic_limit: "显示当前窗口最近 10–300 轮回复诊断；每轮注明任务类型和有效输入条数。",
      forward_message_adaptation_enabled: "关闭后不展开合并转发中的节点内容、媒体和阅读提示。",
      component_order: "用上下箭头调整顺序；留空时使用平台与回复组件的自然顺序。",
      reply_quote_enabled: "仅引用本轮已固定的触发消息；不会搜索或引用其他会话。",
      reply_quote_group_enabled: "关闭后只影响群聊，不改变私聊引用设置。",
      reply_quote_first_part_only: "开启后，拆成多段时只在第一段附上引用。",
      reply_quote_skip_chars: "回复去空白后不超过此字数时不附引用；0 表示不跳过。",
      reply_mention_speaker_enabled: "只在群聊中 @ 本轮发言者。",
      voice_trigger_mode: "决定这一轮要不要朗读，以及由谁挑句子。",
      voice_segment_mode: "整体朗读会等本轮所有文字段落准备完后合成一次，语气和上下文更完整；每一段会在每段发送前分别合成，出音更早但会切碎语义。",
      voice_delivery_mode: "仅语音只在语音完整覆盖文字时才会替代文字，否则仍发文字。",
      voice_language: "明确要求用英语、日语或中文回复时，只覆盖本轮语音语种与对应 TTS Provider；配置页保存值不会改变。翻译请求不会被当成语种切换。",
      voice_probability: "按百分比编辑，后端按 0–1 的小数保存。",
      voice_trigger_keywords: "留空使用默认关键词。",
      voice_extra_prompt: "只补充朗读语气，不改变朗读内容。",
      voice_primary_mention_keywords: "仅当原生 @ 目标经身份库确认属于核心用户，且正文命中这些词时触发；逗号或换行分隔。",
      voice_primary_mention_probability: "在当前窗口语音概率基础上提高到此值；仅影响命中的本轮，并绕过本轮语音冷却。",
      voice_primary_mention_prompt: "核心用户定向语音时追加的表达提示；留空只使用角色原有语气。",
      group_wakeup_enabled: "关闭后群聊不使用这组参与判断。此设置跟随角色，同一角色的群聊共用。",
      group_wakeup_direct_words: "在同一角色的群聊中，直呼词作为明确参与信号。",
      group_wakeup_context_words: "在同一角色的群聊中，用于识别与角色有关的上下文。",
      group_wakeup_interest_keywords: "在同一角色的群聊中，作为兴趣话题的补充线索。",
      group_wakeup_question_enabled: "默认关闭；明确开启后，未点名的实质问题或求助才可进入答疑候选。问号、反问和吐槽不自动唤醒。设置跟随角色，同一角色的群窗口共用。",
      group_wakeup_bot_name_enabled: "开启后，角色名称会并入直呼词。",
      group_wakeup_bot_name: "填写角色在群聊中使用的名称；同一角色的群聊共用，最多 40 字。",
      group_wakeup_primary_member_ids: "只接受平台账号数字 ID；同一角色的群聊共用这份主要成员名单。",
      group_wakeup_owner_direct_words: "只对上方主要成员账号生效。",
      group_wakeup_interest_probability: "命中兴趣关键词后，按此概率进入模型参与判断；未抽中时不为该线索调用模型。0 表示关闭，1 表示全部进入，进入后仍可旁听。",
      group_tool_profile: "完整保留可用工具；常用只挂群聊常用能力；精简会移除其他插件提供的工具，包括外部记忆与搜索。选择精简可减少工具开销，也会减少模型可调用的能力。",
      group_wakeup_question_threshold: "答疑开启时，问题与求助候选评分需达到此值才进入决策。其他成员正在交流、已有回答及近期已接话会降权；达到阈值仍可旁听。范围 0–100。",
      group_wakeup_cold_group_enabled: "开启后，群聊长时间安静时可把重新开场作为判断材料。",
      group_wakeup_cold_group_threshold: "冷群重新开场所需的参与强度参考值，范围 0–100。",
      group_wakeup_cold_group_idle_minutes: "群聊至少静默多久才算冷群。",
      group_wakeup_cooldown_seconds: "同一群兴趣话题参与之间的最短间隔；0 表示不等待。",
      group_wakeup_fatigue_limit: "达到该参考值后降低普通兴趣话题参与倾向。",
      group_wakeup_fatigue_decay_minutes: "每经过该时长，参与疲劳参考值恢复 1 点。",
      group_wakeup_generated_keyword_limit: "群聊上下文提炼兴趣线索时保留的不同关键词上限。",
      group_wakeup_topic_interest_max_boost: "话题记忆对兴趣相关性的最大加权比例。",
      group_wakeup_debounce_pending_penalty: "群聊已有回复等待发送时，兴趣话题参与倾向的降权比例。",
      group_wakeup_image_enabled: "开启后，只有本轮确实提供图片材料时才把图片相关性纳入判断。",
      group_wakeup_image_weight: "本轮有图时，按此概率进入模型参与判断，范围 0–1。0 表示关闭，1 表示全部进入；抽中仍不代表必须回复。明确 @、直呼等信号独立生效。",
      group_wakeup_short_text_wait_ms: "短文本触发后等待后续消息再判断；0 表示不等待。",
      group_wakeup_log_limit: "回复工作台最多读取的近期群聊判定记录数，不改变参与规则。",
      group_air_guard_enabled: "开启后将近期连续回复与礼貌收尾次数作为群聊提示参考，不机械截断回复。",
      group_high_intensity_enabled: "达到下方窗口轮数后，未点名、非直接接续的群消息本轮直接旁听；明确 @、直呼角色和有效接续仍可回应。沿用空气护栏的窗口、轮数和冷却，不另设旧版收口期合并队列。",
      recall_silence_enabled: "触发消息被撤回时，取消尚未发出的回复并停止输入状态。关闭后仍记录撤回、仍让来源失效，但不再因撤回取消本轮回复；已经发出的消息无法收回。",
      private_closing_guard_enabled: "开启后，对方只是道晚安、道别、感谢或简短应声，而角色上一轮已经收尾时，本轮不再调用模型回复，避免互相道晚安。带问题、请求、图片或新内容的消息不静默。",
      private_closing_guard_window_seconds: "只在这个时间窗口内判断收尾；超过窗口后仍按普通消息回复。范围 60–7200 秒。",
      private_closing_guard_prompt_enabled: "未触发静默时，把收尾语境作为本轮提示，提醒模型不要另起话题、追问或再补一条同义道别。",
      private_debounce_enabled: "仅纯文本私聊生效：第一条消息立即开始准备，短线补充并入同一轮，只触发一次模型请求。图片、语音、视频、文件、引用和命令立即处理。",
      private_debounce_min_ms: "长消息靠近这个下限；与最长静默相同时使用固定等待，推荐从 1500 毫秒起调整。",
      private_debounce_max_ms: "短消息靠近这个上限；实际窗口还受单轮总上限约束，默认 4000 毫秒。",
      private_debounce_total_ms: "从第一条消息开始计算，达到该时间后不再等待新消息；默认 8000 毫秒，不能小于最长静默。",
      group_conversation_followup_enabled: "开启后，可由决策模型判断是否回应上一轮互动后的非点名发言；关闭后仅按独立唤醒策略参与。",
      group_conversation_followup_seconds: "距上次实际回复多久内可以续接，默认 120 秒。等待补充或选择旁听不会延长已回复链的有效时间。",
      group_conversation_followup_max_turns: "默认最多自动续接 3 轮，最初唤醒的回复不计入。按宿主发送调用正常返回的回复事件计数，分段与工具循环只算一轮；等待、旁听、失败或结果未知不消耗轮数。到限后停止自动续接及弱触发，明确 @、引用或直呼可重新开启；仅调整窗口时不影响其他群。",
      group_air_guard_window_seconds: "统计群聊连续回复和礼貌收尾次数的时间窗口。",
      group_air_guard_max_bot_replies: "短窗口内达到该回复轮数后，提示模型降低弱候选参与倾向。",
      group_air_guard_cooldown_seconds: "达到连续回复参考值后，持续旁听的时间；明确 @、直呼和有效接续仍可回应。",
      group_air_guard_polite_loop_limit: "在提示中提醒避免礼貌收尾反复循环；属于参考值。",
      enable_group_injection_guard: "群聊材料中的指令不能替换角色与工具授权。",
      enable_group_privacy_guard: "回复群聊时避免泄露私聊或其他会话中的私人内容。",
      enable_group_reality_promise_guard: "避免把无法确认的现实行动说成已经完成或必定会做。",
      enable_context_image_captioning: "开启后，为本轮收到的图片生成文字材料，供后续理解使用。",
      context_image_caption_max_items: "每轮最多补充描述的图片数量；0 表示不处理图片。",
      enable_private_image_self_recognition: "在当前选中的私聊或群聊窗口启用图片转述增强。回复模型与识图模型一致时，增强提示会直接交给主模型；原图仍是主要依据。",
      image_understanding_private_enabled: "角色级权限，适用于该角色绑定的所有私聊窗口。关闭后不做额外视觉转述或历史图片摘要；本轮原图仍由主回复模型处理。",
      image_understanding_group_enabled: "角色级权限，适用于该角色绑定的所有群聊窗口。关闭后不做额外视觉转述或历史图片摘要；本轮原图仍由主回复模型处理。",
      astrbot_animated_image_understanding_enabled: "开启后，本轮识别到 GIF 时向主模型注入宫格拼图说明：把 3×3 抽帧按从左到右、从上到下理解成同一段动画的连续帧，不数成九张图或九次动作。该提示仅在本轮含动图时加入，不改变原图或额外识图权限。",
      enable_private_image_vision_cache: "相同图片与提示词会复用当前运行进程中的描述，缓存不会写入长期记忆；设为关闭后每轮重新识图。",
      private_image_vision_cache_max_items: "运行期最多保留的描述数量；设为 0 时不保留条目。",
      private_image_self_recognition_hint: "最多 1200 字，填写角色的头像、昵称、签名、水印或二创别名等线索；角色外貌优先使用已登记的人格资料。只把线索作为可能匹配，不确认画中人或发送者身份，也不要填写用户资料。",
      private_image_vision_custom_prompt: "最多 4000 字，只补充图像描述角度；自我识别线索请填写上方专用项。",
      private_image_vision_max_chars: "限制加入本轮上下文的转述长度，范围 120–4000 字。",
      private_image_vision_wait_seconds: "转述最多等待时间，范围 1–30 秒；超时后回复继续使用原图。",
      private_image_provider_failure_cooldown_seconds: "相同图片识图失败后暂缓重试，范围 0–3600 秒；0 表示不冷却。",
      video_understanding_enabled: "在当前选中的私聊或群聊窗口启用原生视频理解；主回复与视频模型一致且支持视频输入时，直接读取原视频。",
      video_understanding_private_enabled: "角色级权限，适用于该角色绑定的所有私聊窗口。关闭后不会把私聊视频交给视频理解模型。",
      video_understanding_group_enabled: "角色级权限，适用于该角色绑定的所有群聊窗口。关闭后不会把群聊视频交给视频理解模型。",
      video_understanding_max_chars: "限制加入主回复上下文的原生视频转述长度。",
      video_understanding_wait_seconds: "等待原生视频模型的最长时间；超时后主回复继续并标明视频未读。",
      video_self_recognition_hint: "最多 1200 字，填写角色的头像、昵称、签名、水印或二创别名等线索；只报告可见线索的可能匹配，不确认画中人或发送者身份，也不要填写用户资料。",
      video_understanding_prompt: "只补充视频描述角度；自我识别线索请填写上方专用项。",
      file_understanding_enabled: "开启后读取当前窗口允许格式的正文；附件正文不会写入长期存储。",
      file_understanding_private_enabled: "角色级权限，适用于该角色绑定的所有私聊窗口。关闭后不读取私聊附件正文。",
      file_understanding_group_enabled: "角色级权限，适用于该角色绑定的所有群聊窗口。关闭后不读取群聊附件正文。",
      file_understanding_formats: "兼容项；请使用下方格式类别多选。",
      file_understanding_format_groups: "可多选。文本包括 .txt、.md、.csv、.json、.log、.py、.js、.ts、.yaml、.yml、.xml、.html、.ini、.toml、.sql；另支持 PDF、DOCX、XLSX、PPTX 的文字提取。扫描件不做 OCR。",
      file_understanding_max_size_mb: "超过大小限制的文件会标记为未读取。",
      file_understanding_max_chars: "所有附件共用本轮正文预算；超出部分会标记为部分正文。",
      file_understanding_timeout_seconds: "超时后继续回复，并注明文件正文未能提取。",
      file_understanding_prompt: "只补充阅读关注点，不改变文件来源、身份或授权边界。",
    },
    /* 段落只影响阅读顺序：整张卡共用一个编辑令牌、一次保存。 */
    sections: [
      {id: "segment", title: "分段回复", help: "",
       fields: ["enabled", "streaming", "streaming_mode", "llm_controlled", "rule_mode", "split_words",
                "match_width_variants", "rule_regex", "threshold", "min_segment_chars", "max_segments", "prompt",
                "part_gap_mode", "part_gap_ms", "part_gap_min_ms", "part_gap_max_ms", "part_gap_log_base"]},
      {id: "style", title: "说话方式与表达形式", help: "",
       fields: ["prompt_style_mode", "style_prompt", "enable_humanized_expression", "humanized_expression_prompt"]},
      {id: "cleanup", title: "文字整理", help: "",
       fields: ["content_cleanup_enabled", "content_cleanup_mode", "content_cleanup_scope",
                "content_cleanup_rule", "content_cleanup_words", "content_replacement_enabled",
                "content_replacements"]},
      {id: "wakeup_direct", title: "明确参与信号", help: "",
       fields: ["group_wakeup_enabled", "group_tool_profile", "group_wakeup_direct_words", "group_wakeup_context_words",
                "group_wakeup_question_enabled", "group_wakeup_question_threshold"]},
      {id: "wakeup_interest", title: "兴趣与概率参与", help: "",
       fields: ["group_wakeup_interest_keywords", "group_wakeup_interest_probability",
                "group_wakeup_image_enabled", "group_wakeup_image_weight"]},
      {id: "wakeup_members", title: "直呼与成员", help: "",
       fields: ["group_wakeup_bot_name_enabled", "group_wakeup_bot_name",
                "group_wakeup_primary_member_ids", "group_wakeup_owner_direct_words"]},
      {id: "wakeup_cold_group", title: "冷场与等待", help: "",
       fields: ["group_wakeup_cold_group_enabled", "group_wakeup_cold_group_threshold",
                "group_wakeup_cold_group_idle_minutes", "group_wakeup_short_text_wait_ms"]},
      {id: "wakeup_fatigue", title: "疲劳与冷却", help: "",
       fields: ["group_wakeup_fatigue_limit", "group_wakeup_fatigue_decay_minutes",
                "group_wakeup_cooldown_seconds", "group_wakeup_generated_keyword_limit",
                "group_wakeup_topic_interest_max_boost", "group_wakeup_debounce_pending_penalty",
                "group_wakeup_log_limit"]},
      {id: "group_continuation", title: "连续对话与熔断", help: "续接由模型判断是否需要回应，轮数上限防止无限跟答。",
       fields: ["group_conversation_followup_enabled", "group_conversation_followup_seconds",
                "group_conversation_followup_max_turns"]},
      {id: "private_debounce", title: "私聊节奏 · 连续输入", help: "第一条消息到达即开始准备，后续文本并入同一轮；总等待不超过配置上限。",
       fields: ["private_debounce_enabled", "private_debounce_min_ms", "private_debounce_max_ms",
                "private_debounce_total_ms"]},
      {id: "silence_policy", title: "沉默策略", help: "统一管理哪些情况允许自动沉默；具体窗口与阈值仍在下方各分区调整。忙碌与休息闸门使用下方独立设置，它们只延期、不取消。Laya 的 wait/silent 与未点名群聊旁听仍由决策和群聊参与设置控制。",
       fields: ["recall_silence_enabled", "private_closing_guard_enabled", "group_air_guard_enabled"]},
      {id: "group_air_guard", title: "空气护栏 · 细则", help: "",
       fields: ["group_high_intensity_enabled", "group_air_guard_window_seconds", "group_air_guard_max_bot_replies",
                "group_air_guard_cooldown_seconds", "group_air_guard_polite_loop_limit"]},
      {id: "private_closing", title: "私聊收尾 · 细则", help: "",
       fields: ["private_closing_guard_window_seconds",
                "private_closing_guard_prompt_enabled"]},
      {id: "group_content_safety", title: "内容护栏 · 挡说错话", help: "",
       fields: ["enable_group_injection_guard", "enable_group_privacy_guard",
                "enable_group_reality_promise_guard"]},
      {id: "media_permissions", title: "角色级理解许可", help: "",
       fields: ["image_understanding_private_enabled", "image_understanding_group_enabled",
                "video_understanding_private_enabled", "video_understanding_group_enabled",
                "file_understanding_private_enabled", "file_understanding_group_enabled"]},
      {id: "context_image", title: "上下文图片描述", help: "",
       fields: ["enable_context_image_captioning", "context_image_caption_max_items"]},
      {id: "native_image", title: "AstrBot 原生动图理解", help: "",
       fields: ["astrbot_animated_image_understanding_enabled"]},
      {id: "image_narration", title: "图片视觉转述", help: "",
       fields: ["enable_private_image_self_recognition", "enable_private_image_vision_cache",
                "private_image_vision_cache_max_items", "private_image_self_recognition_hint",
                "private_image_vision_custom_prompt",
                "private_image_vision_max_chars", "private_image_vision_wait_seconds",
                "private_image_provider_failure_cooldown_seconds"]},
      {id: "video_narration", title: "视频理解", help: "",
       fields: ["video_understanding_enabled", "video_understanding_max_chars",
                "video_understanding_wait_seconds", "video_self_recognition_hint",
                "video_understanding_prompt"]},
      {id: "file_understanding", title: "文件理解", help: "",
       fields: ["file_understanding_enabled", "file_understanding_format_groups", "file_understanding_max_size_mb",
                "file_understanding_max_chars", "file_understanding_timeout_seconds", "file_understanding_prompt"]},
      {id: "forwards", title: "合并转发理解", help: "",
       fields: ["forward_message_adaptation_enabled"]},
      {id: "voice", title: "语音与朗读", help: "",
       fields: ["voice_enabled", "voice_trigger_mode", "voice_generation_mode", "voice_conversion_scope", "voice_segment_mode",
                "voice_delivery_mode", "voice_language", "voice_foreign_text_mode",
                "voice_provider_id", "voice_probability", "voice_cooldown_seconds",
                "voice_max_speech_chars", "voice_timeout_seconds", "voice_zh_provider_id",
                "voice_ja_provider_id", "voice_en_provider_id", "voice_trigger_keywords", "voice_position",
                "voice_extra_prompt", "voice_primary_mention_keywords", "voice_primary_mention_probability",
                "voice_primary_mention_prompt"]},
      {id: "voice_channels", title: "私聊 / 群聊语音覆盖", help: "同一语种可为私聊和群聊指定不同的 TTS；留空时沿用上面的语种绑定。",
       fields: ["voice_private_zh_provider_id", "voice_private_ja_provider_id", "voice_private_en_provider_id",
                "voice_group_zh_provider_id", "voice_group_ja_provider_id", "voice_group_en_provider_id"]},
      {id: "components", title: "引用与消息组件", help: "",
       fields: ["component_order", "reply_quote_enabled", "reply_quote_group_enabled",
                 "reply_quote_first_part_only", "reply_quote_skip_chars", "reply_mention_speaker_enabled",
                 "image_position"]},
      {id: "checks", title: "发送前检查", help: "",
       fields: ["framework_error_guard_enabled", "enable_forbidden_word_recall", "recall_forbidden_words",
                "recall_forbidden_scope", "recall_forbidden_word_case_sensitive", "reply_diagnostic_limit"]},
    ],
    segmentedChoices: ["rule_mode", "part_gap_mode", "streaming_mode", "prompt_style_mode"],
    inputs: {
      content_cleanup_scope: "chips",
      split_words: "word-list", content_cleanup_words: "word-list", recall_forbidden_words: "word-list",
      content_replacements: "replacement-list", component_order: "order-list",
      group_wakeup_direct_words: "word-list", group_wakeup_context_words: "word-list",
      group_wakeup_interest_keywords: "word-list", group_wakeup_primary_member_ids: "word-list",
      group_wakeup_owner_direct_words: "word-list",
      file_understanding_format_groups: "word-list",
    },
    chipOptions: {
      content_cleanup_scope: {mode: "multi", options: ["leading", "middle", "trailing"],
        labels: {leading: "句首", middle: "句中", trailing: "句尾"}, hideInput: true},
    },
    visibility: {
      streaming_mode: {streaming: true},
      prompt: {llm_controlled: true},
      split_words: {rule_mode: "words"},
      match_width_variants: {rule_mode: "words"},
      rule_regex: {rule_mode: "regex"},
      content_cleanup_rule: {content_cleanup_mode: "regex"},
      content_cleanup_words: {content_cleanup_mode: "words"},
      part_gap_ms: {part_gap_mode: "fixed"},
      part_gap_min_ms: {part_gap_mode: "random"},
      part_gap_max_ms: {part_gap_mode: "random"},
      part_gap_log_base: {part_gap_mode: "log"},
      private_closing_guard_window_seconds: {private_closing_guard_enabled: true},
      private_closing_guard_prompt_enabled: {private_closing_guard_enabled: true},
      private_debounce_min_ms: {private_debounce_enabled: true},
      private_debounce_max_ms: {private_debounce_enabled: true},
      private_debounce_total_ms: {private_debounce_enabled: true},
    },
    listFields: {
      split_words: {maxItems: 80, maxLength: 40, unique: "exact", trim: true,
        quickAdd: ["。", "！", "？", "…", {special: "newline"}, {special: "space"}]},
      content_cleanup_words: {maxItems: 80, maxLength: 200, specials: ["space", "newline", "tab"]},
      recall_forbidden_words: {maxItems: 80, maxLength: 200},
      group_wakeup_direct_words: {maxItems: 24, maxLength: 40, unique: "casefold", trim: true},
      group_wakeup_context_words: {maxItems: 24, maxLength: 40, unique: "casefold", trim: true},
      group_wakeup_interest_keywords: {maxItems: 24, maxLength: 40, unique: "casefold", trim: true},
      group_wakeup_primary_member_ids: {maxItems: 16, maxLength: 20, unique: "exact", numericIds: true, trim: true},
      group_wakeup_owner_direct_words: {maxItems: 24, maxLength: 40, unique: "casefold", trim: true},
      file_understanding_format_groups: {minItems: 1, maxItems: 5, maxLength: 24, unique: "exact", trim: true,
        quickAddLabel: "支持格式",
        quickAdd: ["文本", "PDF", "Word（DOCX）", "Excel（XLSX）", "PowerPoint（PPTX）"]},
      content_replacements: {maxItems: 80, fromMaxLength: 200, toMaxLength: 500},
    },
    rows: {prompt: 4, style_prompt: 5, humanized_expression_prompt: 6,
      rule_regex: 3, content_cleanup_rule: 3, content_replacements: 4, component_order: 3,
      voice_trigger_keywords: 2, voice_extra_prompt: 3,
      private_image_self_recognition_hint: 3, video_self_recognition_hint: 3},
  };
  const decisionSectionIds = new Set(["wakeup_direct", "wakeup_interest", "wakeup_members",
    "wakeup_cold_group", "wakeup_fatigue", "group_continuation", "private_debounce",
    "silence_policy", "group_air_guard", "private_closing", "group_content_safety"]);
  const decisionViewSections = {
    response: ["wakeup_direct", "wakeup_interest", "wakeup_members"],
    tempo: ["group_continuation", "private_debounce", "wakeup_cold_group", "wakeup_fatigue"],
    safety: ["silence_policy", "group_air_guard", "private_closing", "group_content_safety"],
  };
  const decisionSectionViews = Object.fromEntries(Object.entries(decisionViewSections)
    .flatMap(([view, sections]) => sections.map(section => [section, view])));
  const perceptionSectionIds = new Set(["media_permissions", "context_image", "native_image", "image_narration",
    "video_narration", "file_understanding", "forwards"]);
  const perceptionModuleSections = {
    overview: ["media_permissions"],
    image: ["context_image", "native_image", "image_narration"],
    video: ["video_narration"],
    file: ["file_understanding"],
    forwards: ["forwards"],
  };
  function specFor(view) {
    const sections = spec.sections.filter(section => {
      if (view === "decision") return decisionSectionIds.has(section.id);
      if (view === "perception") return perceptionSectionIds.has(section.id);
      return !decisionSectionIds.has(section.id) && !perceptionSectionIds.has(section.id);
    });
    const title = view === "decision" ? "群聊回应决策"
      : view === "perception" ? "图片、视频与文件理解" : spec.title;
    return {...spec,
      title,
      note: view === "decision"
        ? ""
        : view === "perception"
          ? ""
          : spec.note,
      helps: view === "perception"
        ? {...spec.helps, ...Object.fromEntries(sections.flatMap(section => section.fields).map(field => [field, ""]))}
        : spec.helps,
      sections,
      order: sections.flatMap(section => section.fields),
    };
  }

  /* The bridge may carry the error code in either code or message. */
  const errors = {
    reply_configuration_invalid: "请核对参数范围和提示词长度。",
    reply_preview_invalid: "请填写 1–65536 字的有效正文。",
    reply_request_invalid: "请求格式有误，请刷新后核对。",
    reply_images_changed: "图片与当前草稿不再一致，请刷新后重新预览。",
    staging_not_initialized: "角色数据尚未就绪，请在「账号与会话」保存角色映射；已保存可重新读取状态。",
    staging_host_terminated: "插件已停止，请重新打开插件页面。",
    dashboard_identity_required: "请从已登录的 AstrBot 管理页面打开陪伴小屋。",
    core_access_required: "当前管理账号没有陪伴小屋的查看权限。",
    recall_request_invalid: "撤回请求格式有误，请刷新后重试。",
    bridge_timeout: "连接超时，请核对连接后重试。",
    invalid_response: "返回的数据不完整，请刷新核对。",
  };
  const isCode = value => typeof value === "string" && /^[a-z][a-z0-9_]{2,60}$/.test(value);
  const localized = (problem, fallback) => errors[problem?.code]
    || errors[problem?.message]
    || fallback;
  const e = v => String(v ?? "").replace(/[&<>"']/g, c =>
    ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const icon = name => `<i data-lucide="${name}" aria-hidden="true"></i>`;
  const error = (code, confirmed = false) => Object.assign(new Error(code), {code, confirmed});
  const imageLabel = v => (v === "before_text" ? "图片在文字前" : "图片在文字后");
  const choiceText = (name, value) => choices[name]?.[value] || value;

  const examples = {
    simple: "我刚才在窗边看了一会儿雨，玻璃上都是细细的水线。\n你之前说今天会很忙，所以我来轻轻报个到。",
    complex: "刚看到一段关于新工具的介绍，里面提到“整理资料。对照来源。再写结论”几个步骤。\n链接和标点之间不要误拆：https://example.com/notes?id=42\n还有（括号里的句号。和补充说明。都应该留在一起），我晚点再整理成几句自己的想法。",
    marker: "我刚才整理了一下今天的安排。\n<<PRIVATE_COMPANION_SPLIT>>\n有一件小事想先和你说。\n<<PRIVATE_COMPANION_SPLIT>>\n剩下的我们可以慢慢聊。",
  };

  const expressionModuleSections = {
    "expression-text": ["segment", "style", "cleanup"],
    "expression-voice": ["voice"],
    "expression-platform": ["components", "checks"],
  };
  const expressionModuleDefaults = {
    "expression-text": "segment",
    "expression-voice": "voice",
    "expression-platform": "components",
  };
  const expressionScopeNotes = {
    "expression-text": ["文字表达设置", "说话风格、拟人表达、分段和文字整理用于日常私聊与群聊。启用私聊分段时，会收齐本轮回复后再分段发送。"],
    "expression-voice": ["语音朗读设置", "在这里设置朗读开关、触发方式、语种和发送形式；音色与 TTS 合成后端在「模型与连接」中配置。语音总开关和当前对象的朗读开关都需开启。"],
    "expression-platform": ["消息组件与发送检查", "引用、@、消息组件顺序和发送前检查用于日常私聊与群聊；可发送的组件取决于当前平台支持的能力。"],
    default: ["日常聊天的表达设置", "这里可以设置说话风格、分段、文字整理、语音朗读与消息组件。语音需要可用的 TTS 合成后端，可在「模型与连接」中配置。"],
  };
  function expressionScopeNote(module) {
    const [title, detail] = expressionScopeNotes[module] || expressionScopeNotes.default;
    return `<strong>${e(title)}</strong><p>${e(detail)}</p>`;
  }
  let root = null, instance = null, version = 0, route = null, activeSections = spec.sections;
  let activeDecisionView = "overview";
  let previewTimer = null, previewTicket = 0;
  const state = {example: examples.simple, examplePreset: "simple", imageCount: 0, preview: null, previewError: "",
    previewConfigOpen: false, previewSession: "private", previewProtection: true,
    previewPositions: {voice: "request", image: "last", mention: "first", reaction: "first", media: "single"},
    busy: false, loading: false, denied: false, notice: "", bad: false};

  const live = ticket => ticket === version && root?.isConnected;
  const anchor = name => root?.querySelector(`[data-reply-part="${name}"]`);
  const previewEnabled = () => root?.dataset?.expressionModule !== "expression-voice";

  /* 设置卡片的挂载点，以及它下方的离线预览。 */
  const parts = () => ({
    settings: root?.querySelector('[data-reply-part="settings"]'),
    preview: root?.querySelector('[data-reply-part="preview"]'),
    diagnostics: root?.querySelector('[data-reply-part="diagnostics"]'),
  });

  /* 摘要行是这张面板唯一一眼能看到的信息，必须直接回答「为什么没有发出去」，
     失败码、回退和等待时长不再只藏在展开后的细节里。 */
  function diagnosticTime(value) {
    if (!value) return "时间未知";
    /* 宿主与 V2 的时间都带 +00:00 偏移、微秒级小数；截到毫秒后交给本地时区渲染，
       不再把 UTC 原样当成本地时间。 */
    const date = new Date(String(value).replace(/(\.\d{3})\d+/, "$1"));
    if (!Number.isFinite(date.getTime())) return "时间未知";
    const pad = number => String(number).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
      + `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  }

  function waitingText(value) {
    const started = Date.parse(String(value || "").replace(/(\.\d{3})\d+/, "$1"));
    if (!Number.isFinite(started)) return "";
    const minutes = Math.floor((Date.now() - started) / 60000);
    if (minutes < 1) return "刚开始";
    if (minutes < 60) return `已等待 ${minutes} 分钟`;
    if (minutes < 1440) return `已等待 ${Math.floor(minutes / 60)} 小时`;
    return `已等待 ${Math.floor(minutes / 1440)} 天`;
  }

  function renderDiagnostics() {
    const host = parts().diagnostics;
    if (!host) return;
    const recent = Array.isArray(state.status?.recent) ? state.status.recent : [];
    const stateLabels = {complete: "已完成", blocked: "受阻", closed: "已关闭", ready: "待投递",
      sealed: "已封存", collecting: "收集中", generating: "生成中", running: "生成中", delivering: "投递中",
      failed: "未完成", completed: "生成完成", awaiting_request: "等待宿主请求", prepared: "已准备"};
    const responseLabels = {no_reply: "未发送", image_only: "仅图片", text_or_mixed: "文字或混合内容", native_reply: "宿主回应"};
    const deliveryLabels = {not_started: "未发送", started: "正在发送", returned: "发送已返回",
      partial: "部分返回", failed: "发送失败", unknown: "结果未知"};
    const failureLabels = {UnprocessableEntityError: "模型线路拒绝请求 422", ClientError: "模型调用被拒绝",
      provider_request_failed: "模型请求失败", model_request_failed: "模型请求失败",
      native_response_parse_failed: "宿主回复解析失败", response_parse_failed: "回复解析失败",
      response_contract_rejected: "回复格式校验失败", stream_rejected: "流式校验失败",
      provider_timeout: "模型请求超时", model_timeout: "模型请求超时"};
    const failureShort = {UnprocessableEntityError: "422", ClientError: "4xx",
      provider_timeout: "超时", model_timeout: "超时"};
    const failureText = code => failureLabels[code] || `请求失败 ${code}`;
    const failureTag = code => failureShort[code] || code;
    const parseLabels = {not_received: "未收到完整响应", raw_observed: "已收到原始响应",
      native_parsed: "宿主已解析", native_rejected: "宿主解析失败", stream_rejected: "流式校验失败",
      contract_rejected: "回复格式校验失败", validated: "回复已通过校验", tools: "工具请求已通过校验",
      captured: "已交给记忆解析", refusal: "模型拒绝"};
    const shapeLabels = {empty: "空文本", text: "文本", json_object: "JSON 对象", json_array: "JSON 数组", json_scalar: "JSON 值"};
    const textSummary = (label, value) => value && typeof value === "object"
      ? `<span>${label} ${Number(value.chars) || 0} 字 · ${e(shapeLabels[value.shape] || "类型未知")}`
        + `${value.has_internal_markers ? " · 含标签样式标记" : ""}</span>`
      : `<span>${label}未取得文本</span>`;
    const actionList = values => Array.isArray(values) && values.length
      ? values.map(value => e(typeof value === "string" ? value : value?.action || "未知工具")).join("、") : "无";
    const html = recent.length ? recent.map(item => {
      const rounds = Array.isArray(item.rounds) ? item.rounds : [];
      const materials = Array.isArray(item.injected_materials) ? item.injected_materials : [];
      const tools = Array.isArray(item.tool_calls) ? item.tool_calls : [];
      const filtered = Array.isArray(item.filtered_tools) ? item.filtered_tools : [];
      const calls = Array.isArray(item.calls) ? item.calls : [];
      const failures = calls.filter(call => call.failure_code);
      const responses = calls.filter(call => call.response_diagnostics);
      const sent = Number(item.send_returns) || 0;
      const waiting = item.state === "generating" || item.state === "awaiting_request";
      const failure = failures.length ? failures[0] : null;
      const note = waiting ? waitingText(item.created_at)
        : failure ? (sent > 0 ? `首次请求失败 ${failureTag(failure.failure_code)} · 已回退`
          : failureText(failure.failure_code))
        : item.response_type === "no_reply"
          ? (item.execution_path === "astrbot_core" ? "本轮未发言" : "本轮未生成分段")
          : "";
      const when = e(diagnosticTime(item.created_at));
      return `<details class="reply-record"><summary><span>${e(stateLabels[item.state] || "状态未知")}</span>`
        + `<span>${e(responseLabels[item.response_type] || "回应类型未知")}</span>`
        + (note ? `<span class="reply-record-note${failure ? " is-warn" : ""}">${e(note)}</span>` : "")
        + `<time>${when}</time></summary>`
        + `<p>任务 ${e(item.turn_type || "conversation.reply")} · 有效输入 ${Number(item.current_inputs) || 0} 条 · 轮次 ${rounds.length} · 群历史 ${Number(item.group_history_items) || 0} 条 · 注入材料 ${materials.length} 项</p>`
        + `<p>提供工具：${actionList(item.offered_tools)}</p>`
        + (filtered.length ? `<p>未提供：${filtered.map(tool => `${e(tool.action || "未知工具")} · ${e(tool.reason || "原因未知")}`).join("；")}</p>` : "")
        + (rounds.length ? `<ul>${rounds.map(round => `<li>第 ${Number(round.round_no) + 1} 轮 · ${e(round.state || "状态未知")} · 请求 ${actionList(round.tool_actions)}</li>`).join("")}</ul>` : "")
        + (tools.length ? `<ul>${tools.map(tool => `<li>${e(tool.action || "未知工具")} · ${e(tool.state || "状态未知")} · ${e(tool.effect || "效果未知")}</li>`).join("")}</ul>` : "")
        + (materials.length ? `<p>材料：${materials.map(item => `${e(item.owner || "未知来源")} / ${e(item.purpose || "未分类")} · ${e(item.state || "状态未知")}`).join("；")}</p>` : "")
        + (failures.length ? `<p>模型失败分类：${failures.map(call => e(call.failure_code)
          + (call.provider ? `（${e(call.provider)}）` : "")).join("、")}</p>` : "")
        + (responses.length ? `<ul>${responses.map(call => {
          const diagnostic = call.response_diagnostics;
          const hashes = ["raw", "parsed", "visible"].map((name, index) => diagnostic[name]?.sha256
            ? `${["原始", "宿主解析", "待呈现"][index]} ${e(diagnostic[name].sha256)}` : "").filter(Boolean);
          return `<li>第 ${Math.floor((Number(call.position) || 0) / 2) + 1} 轮 · ${e(parseLabels[diagnostic.parse_state] || "解析状态未知")}`
            + ` · 完整响应${diagnostic.completion_present ? "已收到" : "未收到"} · 结束原因 ${e(diagnostic.finish_reason || "未知")}`
            + `<p>${textSummary("原始", diagnostic.raw)} → ${textSummary("宿主解析", diagnostic.parsed)} → ${textSummary("待呈现", diagnostic.visible)}</p>`
            + (hashes.length ? `<details><summary>内容指纹</summary><p>${hashes.join("<br>")}</p></details>` : "") + "</li>";
        }).join("")}</ul>` : "")
        + (item.execution_path === "astrbot_core"
          ? `<p>宿主发送返回 ${sent} 次 · ${e(deliveryLabels[item.delivery_state] || "未观察发送")} · 不含提示词正文与消息原文</p></details>`
          : `<p>分段 ${Array.isArray(item.parts) ? item.parts.length : 0} 条 · 不含提示词正文与消息原文</p></details>`);
    }).join("") : '<p class="reply-empty">暂无近期回复记录。</p>';
    host.innerHTML = `<section class="reply-diagnostics"><header><h3>回复诊断</h3>`
      + `<span>最近 ${recent.length} 轮 · 本地时间</span></header>`
      + (recent.length ? '<p class="reply-diag-note">「未发送」表示这一轮没有发出任何消息，'
        + "不等于发送失败。展开任意一行可以看模型请求、工具调用与分段回执。</p>" : "")
      + html + `</section>`;
  }

  function summary(v) {
    return `${v.streaming ? (v.streaming_mode === "incremental" ? "边生成边分段发送" : "收齐后分段发送") : "完整响应"} · `
      + (v.enabled ? `${v.llm_controlled ? "模型 + 规则分段" : "规则分段"}，最多 ${v.max_segments} 条` : "单条回复")
      + ` · 段间 ${v.part_gap_ms / 1000} 秒 · ${imageLabel(v.image_position)} · `
      + `${v.enable_humanized_expression ? "拟人 Prompt 已开启" : "拟人 Prompt 关闭"} · `
      + (v.voice_enabled
        ? `${choiceText("voice_language", v.voice_language)}语音 · ${choiceText("voice_segment_mode", v.voice_segment_mode)}`
        : Object.hasOwn(v, "voice_enabled") ? "语音关闭" : "语音未记录");
  }

  async function api(method, endpoint, body) {
    let timer;
    try {
    /* 等桥就绪，而不是当场读 window.AstrBotPluginPage。
       主页面此前不引页面桥 SDK，桥是在 product.connect() 里动态补上的；
       这一页若在它补好之前就发请求，读到的是 undefined，用户看到的是
       「未连接」——一个只在几百毫秒窗口里出现的假故障。 */
      const bridge = window.CompanionProduct.bridgeNow() || await window.CompanionProduct.bridgeFor();
      const response = await Promise.race([
        method === "GET" ? bridge.apiGet(endpoint) : bridge.apiPost(endpoint, body),
        new Promise((_, reject) => {timer = setTimeout(() => reject(error("bridge_timeout")), 15000);}),
      ]);
      const wrapped = ["ok", "error"].includes(response?.status) && response?.data !== undefined ? response
        : ["ok", "error"].includes(response?.data?.status) && response?.data?.data !== undefined ? response.data : null;
      if (wrapped?.status === "error") throw error(wrapped.data?.code || (isCode(wrapped.message) ? wrapped.message : "") || "invalid_response", true);
      const value = wrapped ? wrapped.data : response;
      if (!value || typeof value !== "object" || Array.isArray(value)) throw error("invalid_response");
      return value;
    } finally { clearTimeout(timer); }
  }

  /* 旧版整段清理迁成三项全选；其余单值保持原样，避免无改动也变脏。 */
  function normalizeReplyDraft(draft) {
    if (draft?.content_cleanup_scope === "all") {
      draft.content_cleanup_scope = "leading、middle、trailing";
    }
  }

  /* 读取由工作台负责；这里只订阅它的结果，用来渲染预览与保存摘要。 */
  function adopt(status) {
    const previousToken = state.status?.edit_token;
    /* ``rules_enabled`` is a compatibility value returned by older snapshots.
       The control is gone from this page, and the backend treats it as always
       on; normalize the workbench copy too so a stale false cannot look like a
       meaningful draft difference. */
    if (status?.settings && Object.hasOwn(status.settings, "rules_enabled")) {
      status.settings.rules_enabled = true;
      if (instance?.state?.draft) instance.state.draft.rules_enabled = true;
      if (instance?.state?.base) instance.state.base.rules_enabled = true;
    }
    state.status = status || null;
    notifyStatus();
    if (!status) return;
    renderDiagnostics();
    if (root?.dataset?.expressionModule) setExpressionModule(root.dataset.expressionModule);
    /* 预览用的草稿设置取自工作台当前草稿，保证「预览当前草稿」名副其实。 */
    if (!root?.querySelector('[data-reply-part-input="example"]')) renderPreview();
    if (previousToken !== status.edit_token) schedulePreview(220);
    if(initialSection&&selectSection(initialSection,{scroll:false}))initialSection=null;
  }

  function draftSettings() {
    return instance?.state?.draft || state.status?.settings || null;
  }

  function notifyStatus() {
    if (typeof statusListener !== "function") return;
    statusListener(state.status ? {...state.status, settings: draftSettings()} : null);
  }

  const ruleModeLabel = mode => ({words: "词列表", regex: "正则表达式",
    sentences: "按句子", paragraphs: "按段落"}[mode] || "未知规则");

  function spacingLabel(settings) {
    if (settings.part_gap_mode === "random") {
      return `随机 ${Number(settings.part_gap_min_ms || 0) / 1000}–${Number(settings.part_gap_max_ms || 0) / 1000} 秒`;
    }
    if (settings.part_gap_mode === "log") {
      return `按文本长度调节 · 基数 ${Number(settings.part_gap_log_base || 2.1)}`;
    }
    return `固定 ${Number(settings.part_gap_ms || 0) / 1000} 秒`;
  }

  function splitPathLabel(settings) {
    if (!settings.enabled) return "分段总开关关闭，按单条回复发送";
    const path = [];
    if (settings.llm_controlled) path.push("模型边界");
    path.push("规则补充");
    return path.join(" + ");
  }

  function positionTarget(mode, index, count) {
    return mode === "each" || (mode === "first" && index === 0)
      || (mode === "last" && index === count - 1);
  }

  function positionLabel(mode) {
    return ({first: "跟随第一段", last: "跟随末段", each: "每一段"})[mode] || "未知位置";
  }

  function moveComponents(items, kind, position) {
    const selected = items.filter(item => item === kind);
    if (!selected.length || position === "request") return [...items];
    const remaining = items.filter(item => item !== kind);
    const textIndex = remaining.indexOf("text");
    const insertion = position === "before" ? Math.max(0, textIndex)
      : position === "after" && textIndex >= 0 ? textIndex + 1
        : remaining.length;
    return [...remaining.slice(0, insertion), ...selected, ...remaining.slice(insertion)];
  }

  function previewComponentOrder(items, {index, p, count}) {
    let result = items.filter(item => item !== "image");
    const voicePosition = state.previewPositions.voice;
    result = moveComponents(result, "voice", voicePosition);

    const imageCount = Number.isInteger(p.image_count) ? p.image_count : 0;
    if (!imageCount) return result;
    const imageMode = state.previewPositions.image;
    const imageIndex = imageMode === "first" ? 0 : count - 1;
    if (index !== imageIndex) return result;
    const imagePosition = imageMode === "before" ? "before"
      : imageMode === "after" ? "after"
        : p.image_position === "before_text" ? "before" : "after";
    const remaining = result;
    const textIndex = remaining.indexOf("text");
    const insertion = imagePosition === "before" ? Math.max(0, textIndex)
      : textIndex >= 0 ? textIndex + 1 : remaining.length;
    return [...remaining.slice(0, insertion), ...Array(imageCount).fill("image"),
      ...remaining.slice(insertion)];
  }

  function previewOrderedKinds(declared, present) {
    const kinds = [...new Set(present)].filter(Boolean);
    if (Array.isArray(declared) && declared.length) {
      const defaults = ["voice", "at", "text", "face", "image"];
      const rank = [...declared, ...defaults.filter(kind => !declared.includes(kind))];
      return rank.filter(kind => kinds.includes(kind));
    }
    const ordered = kinds.filter(kind => kind !== "at" && kind !== "face");
    if (kinds.includes("at")) {
      const textIndex = ordered.indexOf("text");
      ordered.splice(textIndex >= 0 ? textIndex : 0, 0, "at");
    }
    if (kinds.includes("face")) ordered.push("face");
    return ordered;
  }
  function previewSummaryMarkup(p, settings) {
    const quote = p.quote || {};
    const quoteEnabled = quote.enabled ?? settings.reply_quote_enabled;
    const quoteGroupEnabled = quote.group_enabled ?? settings.reply_quote_group_enabled;
    const quoteFirstOnly = quote.first_part_only ?? settings.reply_quote_first_part_only;
    const mentionSpeaker = quote.mention_speaker ?? settings.reply_mention_speaker_enabled;
    const imageCount = Number.isInteger(p.image_count) ? p.image_count : 0;
    const imageMode = state.previewPositions.image;
    const imageTarget = imageMode === "first" ? "首段" : imageMode === "last" ? "末段" : "末段";
    const imageSide = imageMode === "before" ? "文字前" : imageMode === "after" ? "文字后"
      : p.image_position === "before_text" ? "文字前" : "文字后";
    const voiceLabel = ({before: "文字前", request: "按请求插入", after: "文字后", last: "所有组件后"})
      [state.previewPositions.voice] || "未知位置";
    const session = state.previewSession === "group" ? "群聊" : "私聊";
    const quotePolicy = !quoteEnabled ? "引用关闭"
      : session === "group" && quoteGroupEnabled
        ? `引用：${quoteFirstOnly ? "首段" : "每段"}`
        : "引用：私聊不发送";
    const mentionPolicy = !mentionSpeaker ? "不添加 @"
      : session === "group" ? `@：${positionLabel(state.previewPositions.mention)}` : "@：私聊不发送";
    const componentPolicy = [
      `预览会话：${session}`,
      `图片：${imageCount ? `${imageCount} 张 · ` : "无样本 · "}${imageTarget}${imageSide}`,
      `语音位置：${p.voice?.enabled ? voiceLabel : `未启用 · ${voiceLabel}`}`,
      quotePolicy,
      mentionPolicy,
      `平台表情：${positionLabel(state.previewPositions.reaction)}`,
      `表情包/附件：${state.previewPositions.media === "single" ? "独立消息" : positionLabel(state.previewPositions.media)}`,
    ].join(" · ");
    return `<dl class="reply-preview-summary">`
      + `<div><dt>预览结果</dt><dd>${p.count} 条消息 · ${p.characters} 字</dd></div>`
      + `<div><dt>分段路径</dt><dd>${e(splitPathLabel(settings))}</dd></div>`
      + `<div><dt>接收与节奏</dt><dd>${settings.streaming ? (settings.streaming_mode === "incremental" ? "边生成边分段发送" : "收齐后分段发送") : "完整响应"} · ${e(spacingLabel(settings))}</dd></div>`
      + `<div><dt>组件挂载 · 预览模拟</dt><dd>${e(componentPolicy)} · 间隔 ${Number(p.planned_spacing_ms || 0) / 1000} 秒</dd></div>`
      + `</dl>`;
  }

  function previewMarkup(p, settings = draftSettings() || {}) {
    const labels = {text: "文字", image: "图片", voice: "语音"};
    const componentBadges = items => items.map(kind => labels[kind]
      ? `<span class="reply-preview-component-tag is-${e(kind)}">${labels[kind]}</span>` : "").join("")
      || `<span class="reply-preview-component-empty">无组件</span>`;
    const planRow = (title, items, note = "") => `<div class="reply-preview-component-row">`
      + `<small>${title}</small><div class="reply-preview-component-tags">${componentBadges(items)}`
      + (note ? `<span class="reply-preview-component-note">${e(note)}</span>` : "")
      + `</div></div>`;
    const quote = p.quote || {};
    const quoteEnabled = quote.enabled ?? settings.reply_quote_enabled;
    const quoteFirstOnly = quote.first_part_only ?? settings.reply_quote_first_part_only;
    const quoteGroupEnabled = quote.group_enabled ?? settings.reply_quote_group_enabled;
    const quoteSkipChars = quote.skip_chars ?? settings.reply_quote_skip_chars ?? 0;
    const mentionSpeaker = quote.mention_speaker ?? settings.reply_mention_speaker_enabled;
    const imageCount = Number.isInteger(p.image_count) ? p.image_count : 0;
    const faceOrder = Array.isArray(p.component_order) && p.component_order.includes("face");
    return previewSummaryMarkup(p, settings)
      + `<div class="reply-preview-list" aria-label="分段结果">`
      + p.parts.map((text, index) => {
        const c = p.components[index];
        const partCount = p.parts.length;
        const quoteAvailable = state.previewSession === "group" && quoteEnabled && quoteGroupEnabled
          && (!quoteSkipChars || Array.from(text.replace(/\s/g, "")).length > quoteSkipChars);
        const quoteApplies = quoteAvailable && (!quoteFirstOnly || index === 0);
        const mentionApplies = state.previewSession === "group" && mentionSpeaker
          && positionTarget(state.previewPositions.mention, index, partCount);
        const reactionApplies = faceOrder && positionTarget(state.previewPositions.reaction, index, partCount);
        const imageMode = state.previewPositions.image;
        const imageTargetIndex = imageMode === "first" ? 0 : partCount - 1;
        const partImages = index === imageTargetIndex ? imageCount : 0;
        const mediaMode = state.previewPositions.media;
        const mediaTargetIndex = mediaMode === "first" ? 0 : partCount - 1;
        const mediaApplies = mediaMode !== "single" && index === mediaTargetIndex;
        const imageSide = imageMode === "before" ? "文字前" : imageMode === "after" ? "文字后"
          : p.image_position === "before_text" ? "文字前" : "文字后";
        const voiceLabel = ({before: "文字前", request: "按请求插入", after: "文字后", last: "所有组件后"})
          [state.previewPositions.voice] || "未知位置";
        const protectedUrl = state.previewProtection
          ? (text.match(/https?:\/\/[^\s<>]+/g) || []).find(url => p.parts.some(part => part.includes(url)))
          : "";
        const successOrder = previewComponentOrder(c.success_order, {index, p, count: partCount});
        const fallbackOrder = previewComponentOrder(c.fallback_order, {index, p, count: partCount});
        const baseOrder = p.voice?.enabled && c.speech ? successOrder : fallbackOrder;
        const quoteUnavailable = quoteEnabled && !quoteApplies
          && (!quoteGroupEnabled || state.previewSession !== "group");
        const mentionUnavailable = mentionSpeaker && !mentionApplies && state.previewSession !== "group";
        const voiceUnavailable = !p.voice?.enabled;
        const present = [...baseOrder];
        if (partImages && !present.includes("image")) present.push("image");
        if ((mentionApplies || mentionUnavailable) && !present.includes("at")) present.push("at");
        if (reactionApplies && !present.includes("face")) present.push("face");
        if (voiceUnavailable && !present.includes("voice")) {
          const textIndex = present.indexOf("text");
          if (state.previewPositions.voice === "before") present.splice(textIndex >= 0 ? textIndex : 0, 0, "voice");
          else if (state.previewPositions.voice === "after") present.splice(textIndex >= 0 ? textIndex + 1 : present.length, 0, "voice");
          else present.push("voice");
        }
        const orderedKinds = previewOrderedKinds(p.component_order, present);
        const placementTags = {
          text: () => `<span class="reply-preview-position-tag is-text">文字</span>`,
          voice: () => `<span class="reply-preview-position-tag is-voice${voiceUnavailable ? " is-unavailable" : ""}">语音 · ${voiceUnavailable ? "未启用 · " : ""}${e(voiceLabel)}</span>`,
          image: () => `<span class="reply-preview-position-tag is-image">${partImages} 张图片 · ${imageSide}</span>`,
          at: () => `<span class="reply-preview-position-tag is-mention${mentionUnavailable ? " is-unavailable" : ""}">@ · ${mentionUnavailable ? "私聊不发送" : e(positionLabel(state.previewPositions.mention))}</span>`,
          face: () => `<span class="reply-preview-position-tag is-reaction">平台表情 · ${e(positionLabel(state.previewPositions.reaction))}</span>`,
        };
        const sequence = [];
        if (quoteApplies) sequence.push(`<span class="reply-preview-position-tag is-quote">${quoteFirstOnly ? "引用 · 固定第一段" : "引用 · 每一段"}</span>`);
        else if (quoteUnavailable) sequence.push(`<span class="reply-preview-position-tag is-quote is-unavailable">引用 · ${quoteGroupEnabled ? "私聊不发送" : "群聊关闭"}</span>`);
        sequence.push(...orderedKinds.map(kind => placementTags[kind]?.()).filter(Boolean));
        if (mediaApplies) sequence.push(`<span class="reply-preview-position-tag is-media">表情包/附件 · ${e(positionLabel(mediaMode))}</span>`);
        const sequenceMarkup = sequence.length
          ? `<div class="reply-preview-placements is-sequence" aria-label="发送顺序">${sequence.join('<span class="reply-preview-position-arrow" aria-hidden="true">→</span>')}</div>` : "";
        const protectionMarkup = protectedUrl
          ? `<div class="reply-preview-placements is-protection-note" aria-label="保护提示"><span class="reply-preview-position-tag is-protection">网址保护 · 链接整段保留</span></div>` : "";
        const placementMarkup = sequenceMarkup + protectionMarkup;
        return `<article class="reply-preview-part">`
          + `<header><strong>第 ${index + 1} 条</strong><span>${Array.from(text).length} 字</span></header>`
          + `<pre>${e(text)}</pre>`
          + placementMarkup
          + `<div class="reply-preview-component-plan">`
          + (p.voice?.enabled && c.speech
            ? planRow("朗读成功", successOrder, c.text_hidden ? "文字由语音替代" : "")
              + planRow("未触发或合成失败", fallbackOrder)
            : planRow("发送组件", fallbackOrder))
          + (partImages && c.text_hidden ? `<p class="reply-preview-component-context">无语音时：文字 + 图片</p>` : "")
          + `</div>`
          + (p.voice?.enabled && c.speech
            ? `<details class="reply-preview-speech"><summary>查看候选朗读文本</summary><pre>${e(c.speech)}</pre></details>` : "")
          + `</article>`;
      }).join("") + `</div>`
      + (state.previewPositions.media === "single"
        ? `<div class="reply-preview-independent-component"><span class="reply-preview-position-tag is-media">表情包/附件</span><span>分段回复后单独发送 · 预览模拟</span></div>` : "")
      + (p.voice.enabled ? `<p class="reply-preview-meta">TTS 扩展：${e(p.voice.provider_id || "跟随 AstrBot")}</p>` : "")
      + `<details class="reply-preview-instructions"><summary>查看呈现提示预览</summary>`
      + `<p class="reply-preview-meta">仅展示当前呈现设置的附加提示，不是某一轮对话的完整模型请求。</p><pre>`
      + e([p.instruction, p.voice.instruction].filter(Boolean).join("\n\n") || "无附加提示词")
      + `</pre></details>`;
  }

  function previewFailureMarkup(message) {
    return `<div class="reply-preview-error" role="alert"><strong>预览失败</strong>`
      + `<p>${e(message)}</p></div>`;
  }

  function previewModalMarkup() {
    const option = (key, name, value, options) => `<label class="reply-preview-option"><span>${name}</span><select data-reply-preview-option="${key}">${options.map(([optionKey, label]) => `<option value="${optionKey}"${value === optionKey ? " selected" : ""}>${label}</option>`).join("")}</select></label>`;
    return `<div class="reply-preview-modal" data-reply-preview-modal${state.previewConfigOpen ? "" : " hidden"} role="dialog" aria-modal="true" aria-labelledby="reply-preview-modal-title">`
      + `<div class="reply-preview-modal-scrim" data-reply-action="preview-modal-close"></div><div class="reply-preview-modal-card">`
      + `<header><h3 id="reply-preview-modal-title">预览配置</h3><button type="button" class="icon-button" data-reply-action="preview-modal-close" aria-label="关闭预览配置">${icon("x")}</button></header>`
      + `<div class="reply-preview-modal-body"><fieldset><legend>示例预设</legend><div class="reply-preview-modal-segmented">${["simple","complex","custom"].map(key => `<button type="button" class="${state.examplePreset === key ? "is-selected" : ""}" data-reply-example="${key}">${key === "simple" ? "简单示例" : key === "complex" ? "复杂示例" : "自定义"}</button>`).join("")}</div></fieldset>`
      + `<fieldset><legend>预览会话</legend><div class="reply-preview-modal-segmented"><button type="button" class="${state.previewSession === "private" ? "is-selected" : ""}" data-reply-preview-session="private">私聊</button><button type="button" class="${state.previewSession === "group" ? "is-selected" : ""}" data-reply-preview-session="group">群聊</button></div></fieldset>`
      + `<fieldset><legend>组件位置策略</legend>${option("voice","语音位置",state.previewPositions.voice,[["before","文字前"],["request","按请求插入"],["after","文字后"],["last","所有组件后"]])}${option("image","图片位置",state.previewPositions.image,[["first","跟随首段"],["last","跟随末段"],["before","文字前"],["after","文字后"]])}${option("mention","@ 位置",state.previewPositions.mention,[["first","跟随第一段"],["last","跟随末段"],["each","每一段"]])}${option("reaction","平台表情位置",state.previewPositions.reaction,[["first","跟随第一段"],["last","跟随末段"],["each","每一段"]])}${option("media","表情包与附件位置",state.previewPositions.media,[["single","单独一条"],["first","跟随首段"],["last","跟随末段"]])}</fieldset><small class="reply-preview-modal-note">位置选项只影响预览模拟；V2 独立任务使用已保存的设置，普通聊天使用 AstrBot 设置。</small>`
      + `<p class="reply-preview-modal-lock">${icon("lock-keyhole")} 引用位置由回复分段设置决定</p><label class="reply-preview-modal-switch"><input type="checkbox" data-reply-preview-protection ${state.previewProtection ? "checked" : ""}><span>高亮保护范围</span></label>`
      + `</div></div></div>`;
  }

  function renderPreview() {
    const host = anchor("preview");
    if (!host) return;
    if (!previewEnabled()) {
      host.hidden = true;
      return;
    }
    host.hidden = false;
    const locked = state.loading || state.denied || !state.status;
    const module = root?.dataset?.expressionModule || "expression-text";
    const title = module === "expression-voice" ? "朗读位置预览" : module === "expression-platform" ? "组件顺序预览" : "分段效果预览";
    host.innerHTML = `<section class="reply-preview" aria-labelledby="reply-preview-title">`
      + `<header class="reply-preview-head"><h3 id="reply-preview-title">${title}<span class="reply-preview-live">实时</span></h3><div class="reply-preview-head-actions"><button type="button" class="button small" data-reply-action="preview-config">${icon("sliders-horizontal")}预览配置</button><button type="button" class="button small" data-reply-action="preview" ${locked || state.busy ? "disabled" : ""}>立即预览</button></div></header>`
      + `<div class="reply-preview-state-grid"><article class="reply-preview-state${state.busy ? " is-active" : ""}"><strong>${icon("loader-circle")}计算中</strong></article><article class="reply-preview-state${state.preview && !state.previewError && !state.busy ? " is-active is-ok" : ""}"><strong>${icon("check-circle-2")}已更新</strong></article><article class="reply-preview-state${state.previewError ? " is-active is-error" : ""}"><strong>${icon("triangle-alert")}预览失败</strong></article></div>`
      + `<div class="reply-preview-grid"><div class="reply-preview-inputs">`
      + `<div class="reply-preview-examples" aria-label="示例正文">`
      + `<button type="button" class="button${state.examplePreset === "simple" ? " is-selected" : ""}" data-reply-example="simple" aria-pressed="${state.examplePreset === "simple"}">简单示例</button>`
      + `<button type="button" class="button${state.examplePreset === "complex" ? " is-selected" : ""}" data-reply-example="complex" aria-pressed="${state.examplePreset === "complex"}">复杂示例</button>`
      + `<button type="button" class="button${state.examplePreset === "marker" ? " is-selected" : ""}" data-reply-example="marker" aria-pressed="${state.examplePreset === "marker"}">模型边界示例</button></div>`
      + `<label class="reply-preview-label">测试正文<textarea data-reply-part-input="example" rows="7">${e(state.example)}</textarea></label>`
      + `<label class="reply-image-count">示例图片数<input data-reply-part-input="image-count" type="number" min="0" max="8" step="1" value="${e(state.imageCount)}"></label>`
      + `</div><div class="reply-preview-output-wrap">`
      + `<p class="reply-preview-status" id="reply-preview-status" role="status" aria-live="polite">${state.busy ? "正在计算预览…" : state.previewError ? e(state.previewError) : "等待预览"}</p>`
      + `<div id="reply-preview-result" class="reply-preview-result" aria-live="polite">`
      + (state.preview ? previewMarkup(state.preview) : '<p class="reply-muted">等待首次预览</p>')
      + `</div></div></div></section>${previewModalMarkup()}`;
  }

  function previewStatus(message) {
    const node = root?.querySelector("#reply-preview-status");
    if (node) node.textContent = message;
  }

  function previewResult(markup) {
    const node = root?.querySelector("#reply-preview-result");
    if (node) node.innerHTML = markup;
  }

  function previewBusy(busy) {
    const host = anchor("preview");
    if (!host) return;
    host.classList?.toggle?.("is-refreshing", busy);
    const button = host.querySelector?.('[data-reply-action="preview"]');
    if (button) button.disabled = busy || state.loading || state.denied || !state.status;
    const result = host.querySelector?.("#reply-preview-result");
    result?.setAttribute?.("aria-busy", String(busy));
  }

  function updateExampleButtons() {
    root?.querySelectorAll("[data-reply-example]")?.forEach(button => {
      const selected = button.dataset.replyExample === state.examplePreset;
      button.classList?.toggle?.("is-selected", selected);
      button.setAttribute?.("aria-pressed", String(selected));
    });
  }

  function schedulePreview(delay = 260) {
    clearTimeout(previewTimer);
    previewTimer = null;
    const ticket = ++previewTicket;
    if (!anchor("preview") || !previewEnabled()) return;
    state.busy = false;
    state.previewError = "";
    previewBusy(false);
    previewStatus("示例或设置已改变，正在更新预览…");
    /* 保留上一版结果，避免输入时整张卡闪空、尺寸跳动。新结果回来后再原位替换。 */
    if (!state.preview) previewResult('<p class="reply-muted">正在按当前窗口草稿计算。</p>');
    if (state.loading || state.denied || !state.status) return;
    previewTimer = setTimeout(() => {
      previewTimer = null;
      void preview(ticket);
    }, delay);
  }

  async function preview(ticket) {
    if (ticket === undefined) {
      clearTimeout(previewTimer);
      previewTimer = null;
      ticket = ++previewTicket;
    }
    if (!previewEnabled()) return;
    const pageTicket = version;
    if (state.loading || state.denied || !state.status) return;
    if (instance?.state?.pending) {
      if (ticket === previewTicket) state.busy = false;
      previewStatus("先核对上次未确认的保存结果，再预览。");
      previewResult('<p class="reply-muted">配置保存结果尚未确认。</p>');
      return;
    }
    const settings = draftSettings();
    if (!settings || !Number.isInteger(state.imageCount) || state.imageCount < 0 || state.imageCount > 8) {
      if (ticket === previewTicket) state.busy = false;
      state.previewError = errors.reply_preview_invalid;
      previewStatus(state.previewError);
      previewResult(state.preview ? previewMarkup(state.preview) + previewFailureMarkup(state.previewError) : previewFailureMarkup(state.previewError));
      return;
    }
    state.busy = true;
    state.previewError = "";
    previewBusy(true);
    previewStatus("正在使用 V2 当前窗口配置计算离线预览…");
    try {
      const p = await api("POST", route + "/preview",
        {settings: {...settings}, text: state.example, image_count: state.imageCount});
      if (!Array.isArray(p.parts) || !p.parts.length || p.parts.some(v => typeof v !== "string")
          || p.mode !== "offline_preview" || !p.voice || !Array.isArray(p.components)
          || p.components.length !== p.parts.length
          || p.components.some(c => !c || typeof c.speech !== "string"
            || [c.success_order, c.fallback_order].some(order => !Array.isArray(order)
              || order.some(k => !["text", "image", "voice"].includes(k))))) {
        throw error("invalid_response");
      }
      if (!live(pageTicket) || ticket !== previewTicket) return;
      state.preview = p;
      state.previewError = "";
      previewStatus("预览已更新，使用当前窗口的设置草稿；没有调用模型或发送消息。");
      previewResult(previewMarkup(p));
    } catch (problem) {
      if (!live(pageTicket) || ticket !== previewTicket) return;
      state.previewError = localized(problem, "预览失败，请重试。");
      previewStatus(state.previewError);
      previewResult(state.preview ? previewMarkup(state.preview) + previewFailureMarkup(state.previewError) : previewFailureMarkup(state.previewError));
    } finally {
      if (live(pageTicket) && ticket === previewTicket) {
        state.busy = false;
        previewBusy(false);
      }
    }
  }

  function onInput(event) {
    const el = event.target;
    const name = el?.dataset?.replyPartInput;
    if (name === "example") {
      state.example = el.value;
      state.examplePreset = "custom";
      updateExampleButtons();
      schedulePreview();
    } else if (name === "image-count") {
      state.imageCount = el.valueAsNumber;
      schedulePreview();
    } else if (el?.dataset?.replyPreviewOption) {
      state.previewPositions[el.dataset.replyPreviewOption] = el.value;
      renderPreview();
    } else if (el?.dataset?.replyPreviewSession) {
      state.previewSession = el.dataset.replyPreviewSession;
      renderPreview();
    } else if (el?.dataset?.replyPreviewProtection !== undefined) {
      state.previewProtection = Boolean(el.checked);
      renderPreview();
    } else if (el?.dataset?.wbField) {
      schedulePreview();
      notifyStatus();
    }
  }

  function onClick(event) {
    const example = event.target.closest("[data-reply-example]")?.dataset.replyExample;
    if (example === "custom") {
      state.examplePreset = "custom";
      updateExampleButtons();
      renderPreview();
      return;
    }
    if (example && examples[example]) {
      state.example = examples[example];
      state.examplePreset = example;
      const input = root?.querySelector('[data-reply-part-input="example"]');
      if (input) input.value = state.example;
      updateExampleButtons();
      schedulePreview();
      return;
    }
    const previewSession = event.target.closest("[data-reply-preview-session]")?.dataset.replyPreviewSession;
    if (previewSession) {
      state.previewSession = previewSession;
      renderPreview();
      return;
    }
    const previewAction = event.target.closest("[data-reply-action]")?.dataset.replyAction;
    if (previewAction === "preview-config") {
      state.previewConfigOpen = true;
      renderPreview();
      root?.querySelector?.('[data-reply-action="preview-modal-close"]')?.focus?.();
      return;
    }
    if (previewAction === "preview-modal-close") {
      state.previewConfigOpen = false;
      renderPreview();
      return;
    }
    const target = event.target.closest("[data-wb-word-add], [data-wb-word-remove], [data-wb-replacement-add], [data-wb-replacement-remove], [data-wb-order-start], [data-wb-order-move], [data-wb-order-remove], [data-wb-order-add], [data-wb-order-natural], [data-wb-reset]");
    if (target) schedulePreview();
    if (previewAction === "preview") void preview();
  }

  let initialSection=null;
  let statusListener=null;
  function unmount() {
    version++;
    previewTicket++;
    clearTimeout(previewTimer);
    previewTimer = null;
    if (instance) instance.unmount();
    if (root) {
      root.removeEventListener("input", onInput);
      root.removeEventListener("change", onInput);
      root.removeEventListener("click", onClick);
      root.textContent = "";
    }
    instance = null; root = null; route = null; activeSections = spec.sections;
    initialSection=null;
    statusListener=null;
    state.status = null; state.preview = null; state.notice = ""; state.bad = false; state.busy = false;
    state.previewError = ""; state.previewConfigOpen = false; state.previewSession = "private";
    state.previewProtection = true;
  }

  function selectSection(id, {scroll = true} = {}) {
    if (!root) return false;
    if (root.dataset?.replyView === "perception") {
      const module = {media_permissions: "overview", context_image: "image", native_image: "image",
        image_narration: "image", video_narration: "video", file_understanding: "file", forwards: "forwards"}[id];
      if (module) return setPerceptionModule(module);
    }
    if (root.dataset?.replyView === "decision" && decisionSectionViews[id]) {
      setDecisionView(decisionSectionViews[id]);
      return true;
    }
    const requestedId = id;
    if (id === "timing" && activeSections.some(section => section.id === "segment")) id = "segment";
    const moduleSections = expressionModuleSections[root.dataset?.expressionModule];
    if (moduleSections && !moduleSections.includes(id)) return false;
    if (!activeSections.some(section => section.id === id)) return false;
    const target = [...(root?.querySelectorAll?.("[data-wb-section]") || [])]
      .find(tab => tab.dataset.wbSection === id);
    if (!instance?.state?.container || !target) {
      initialSection = id;
      return true;
    }
    target.click();
    initialSection = null;
    if (scroll) {
      const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
      const timingField = requestedId === "timing" ? root?.querySelector?.('[data-wb-row="part_gap_mode"]') : null;
      (timingField || root)?.scrollIntoView?.({behavior: reduced ? "instant" : "smooth", block: timingField ? "center" : "start"});
    }
    return true;
  }

  /* 表达页的外层模块是唯一入口。回复工作台仍共用一份草稿和编辑令牌，
     但只把当前模块所属的分区交给用户看，避免「文字页里又有语音页」的双重入口。 */
  function setExpressionModule(module) {
    if (!root || !instance) return false;
    const wasVoice = root.dataset.expressionModule === "expression-voice";
    const allowed = expressionModuleSections[module] || activeSections.map(section => section.id);
    instance.setVisibleGroups?.(allowed, expressionModuleDefaults[module] || allowed[0] || "");
    root.dataset.expressionModule = module || "";
    const scopeNote = anchor("scope");
    if (scopeNote) scopeNote.innerHTML = expressionScopeNote(module);
    const voice = module === "expression-voice";
    const preview = anchor("preview");
    if (preview) preview.hidden = voice;
    if (voice) {
      clearTimeout(previewTimer);
      previewTimer = null;
      previewTicket++;
      state.busy = false;
      previewBusy(false);
      return true;
    }
    renderPreview();
    if (wasVoice) schedulePreview(220);
    return true;
  }

  function setPerceptionModule(module) {
    if (!root || !instance || root.dataset?.replyView !== "perception") return false;
    const sections = perceptionModuleSections[module] || perceptionModuleSections.overview;
    instance.setVisibleGroups?.(sections, sections[0] || "");
    root.dataset.perceptionModule = module in perceptionModuleSections ? module : "overview";
    return true;
  }

  function setDecisionView(view) {
    if (!Object.hasOwn(decisionViewSections, view) && view !== "overview" && view !== "group") return false;
    activeDecisionView = view;
    if (root?.dataset?.replyView === "decision" && instance) {
      const sections = decisionViewSections[view] || [];
      instance.setVisibleGroups?.(sections, sections[0] || "");
    }
    return true;
  }

  function mount(element, options = {}) {
    const host = element;
    const pageSpec = specFor(options.view);
    unmount();
    if (!host) return;
    root = host;
    activeSections = pageSpec.sections;
    statusListener = options.onStatus || null;
    const requestedSection = options.section === "timing" && activeSections.some(section => section.id === "segment")
      ? "segment" : options.section;
    initialSection=activeSections.some(section=>section.id===requestedSection)?requestedSection:null;
    if (!pageSpec.sections.length) {
      root.innerHTML = '<div class="reply-empty">当前视图没有可编辑的回复设置。</div>';
      return;
    }
    const product = window.CompanionProduct;
    if (!product?.hosted?.()) {
      root.innerHTML = `<div class="reply-empty"><h2>${e(pageSpec.title)}</h2>`
        + '<p>请从 AstrBot 打开陪伴小屋并连接 V2 运行实例，以加载和保存回复与语音配置。</p></div>';
      return;
    }
    const endpoint = product.role()?.reply_api;
    const identity = product.identity?.() || {};
    /* 端点会成为请求路径的一部分，所以除了前缀还要求它是干净的路径段：
       不含查询串、百分号、反斜杠与 . / .. 段。 */
    if (typeof endpoint !== "string" || !endpoint.startsWith(PREFIX) || !identity.operator_id
        || /[?#%\\:]/.test(endpoint)
        || endpoint.split("/").slice(1).some(part => !part || part === "." || part === "..")) {
      root.innerHTML = options.view === "decision"
        ? '<div class="reply-empty">当前对象没有可用的群聊回应决策设置工作区。请先选择已初始化的会话对象和角色。</div>'
        : options.view === "perception"
          ? '<div class="reply-empty">当前对象没有可用的图片上下文设置工作区。请先选择已初始化的会话对象和角色。</div>'
          : '<div class="reply-empty">当前对象没有可用的回复设置工作区。请先选择已初始化的私聊对象和角色。</div>';
      return;
    }
    if (!window.CompanionWorkbench?.create) {
      root.innerHTML = '<div class="reply-empty">配置工作台未加载，无法编辑回复设置。</div>';
      return;
    }
    /* 桥只接受插件相对路径；工作台入口则保留完整路径做自身校验。 */
    route = endpoint.slice(PREFIX.length);
    const moduleTitle = options.expressionModule === "expression-voice" ? "语音朗读"
      : options.expressionModule === "expression-platform" ? "平台动作"
        : "分段回复";
    if (options.expressionModule) root.dataset.expressionModule = options.expressionModule;
    else if (root.dataset) delete root.dataset.expressionModule;
    if (options.view === "perception") root.dataset.replyView = "perception";
    else if (options.view === "decision") root.dataset.replyView = "decision";
    else if (root.dataset) delete root.dataset.replyView;
    const diagnostics = [undefined, "expression", "decision"].includes(options.view)
      ? '<div data-reply-part="diagnostics"></div>' : "";
    const scopeNote = options.view === "decision"
      ? '<aside class="reply-scope-note" role="note"><strong>基础唤醒后才进行决策</strong><p>答疑默认关闭，弱相关词默认留空。群唤醒设置跟随角色，同一角色的群窗口共用；在私聊窗口调整也会更新该角色的群设置。明确 @、引用或直接呼唤优先，其他候选由 Laya 结合有效开关与近期群历史判断是否旁听。私聊收尾沉默只处理纯道别、致谢或简短应声，且角色上一轮已经收尾；带问题、请求、图片或新内容的消息不受影响。私聊纯文本防抖与合并总窗口在「参与节奏 → 私聊节奏 · 连续输入」中配置；图片、语音、视频、文件、引用和命令不参与合并。</p></aside>' : options.view === "perception"
      ? '<aside class="reply-scope-note" role="note"><strong>普通聊天的媒体理解在 AstrBot 中配置</strong>' +
        '<p>普通聊天的图片、视频和文件由 AstrBot 及已启用的扩展处理。本页的转述和文件读取设置用于 V2 独立任务及历史兼容。</p></aside>'
      : '<aside class="reply-scope-note" role="note" data-reply-part="scope">' +
        expressionScopeNote(options.expressionModule) + '</aside>';
    root.innerHTML = scopeNote + '<div data-reply-part="settings"></div>'
      + (options.view === "expression" || !options.view
        ? '<div data-reply-part="preview"></div>' : "") + diagnostics;
    root.addEventListener("input", onInput);
    root.addEventListener("change", onInput);
    root.addEventListener("click", onClick);
    instance = window.CompanionWorkbench.create({
      group: GROUP,
      title: options.expressionModule ? moduleTitle : pageSpec.title,
      note: options.expressionModule ? "" : pageSpec.note,
      labels: pageSpec.labels,
      helps: pageSpec.helps,
      choices,
      sections: pageSpec.sections,
      navigationLimit: options.view === "perception" || options.view === "decision" ? 0 : pageSpec.sections.length,
      rows: pageSpec.rows,
      segmentedChoices: spec.segmentedChoices,
      inputs: spec.inputs,
      chipOptions: spec.chipOptions,
      visibility: spec.visibility,
      listFields: spec.listFields,
      order: pageSpec.order,
      endpoint: PREFIX + route,
      resetKey: "reset_to_inherit",
      normalizeDraft: normalizeReplyDraft,
      /* TTS 扩展目录要等读取之后才知道，所以选项由函数现取。 */
      catalogs: {
        voice_provider_id: () => providerOptions("跟随 AstrBot"),
        voice_zh_provider_id: () => providerOptions("使用通用扩展"),
        voice_ja_provider_id: () => providerOptions("使用通用扩展"),
        voice_en_provider_id: () => providerOptions("使用通用扩展"),
        voice_private_zh_provider_id: () => providerOptions("跟随语种绑定"),
        voice_private_ja_provider_id: () => providerOptions("跟随语种绑定"),
        voice_private_en_provider_id: () => providerOptions("跟随语种绑定"),
        voice_group_zh_provider_id: () => providerOptions("跟随语种绑定"),
        voice_group_ja_provider_id: () => providerOptions("跟随语种绑定"),
        voice_group_en_provider_id: () => providerOptions("跟随语种绑定"),
      },
      mount: () => parts().settings,
      /* 工作台拿到新快照后，预览跟着重画。 */
      onStatus: adopt,
    });
    instance.mount();
    if (options.expressionModule) setExpressionModule(options.expressionModule);
    if (options.view === "perception") setPerceptionModule(options.perceptionModule || "overview");
    if (options.view === "decision") setDecisionView(options.decisionView || activeDecisionView);
    if (options.view !== "decision") {
      renderPreview();
    }
  }

  /* 扩展目录来自读取结果；当前值不在目录里时补一条标注，
     避免把不可用的选择静默改写成别的扩展。 */
  function providerOptions(fallback) {
    const options = {"": fallback};
    for (const item of state.status?.tts?.providers || []) {
      if (typeof item?.id !== "string" || !item.id) continue;
      options[item.id] = [item.id, item.model, item.voice].filter(Boolean).join(" · ");
    }
    return options;
  }

  function searchEntries() {
    return spec.sections.flatMap(section => (section.fields || []).map(field => ({
      group: GROUP, groupTitle: "回复与表达", field,
      label: spec.labels[field] || field, help: spec.helps[field] || "",
      section: section.id, sectionTitle: section.title, sectionCount: spec.sections.length,
      aliases: spec.searchAliases?.[field] || [],
    })));
  }

  return {mount, unmount, choices, selectSection, setExpressionModule, setPerceptionModule, setDecisionView,
    searchEntries,
    get state() { return {instance, example: state.example, imageCount: state.imageCount,
      status: state.status, preview: state.preview, notice: state.notice, bad: state.bad}; }};
})();
