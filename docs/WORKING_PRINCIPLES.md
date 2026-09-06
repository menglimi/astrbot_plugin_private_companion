# 「我会永远陪着你」完整工作原理

> 适用版本：`astrbot_plugin_private_companion` v6.4.5b（2026-09-03）
> 目标：AstrBot `>= 4.22.0`，官方声明平台 `aiocqhttp` 与 `qq_official`。
> 文档定位：**源码层**工作原理——不是产品使用手册，也不是 API 索引，而是把"插件内部在做什么、按什么顺序、由谁负责"完整铺开。

---

## 目录

0. [插件身份与定位](#0-插件身份与定位)
1. [文件清单与规模](#1-文件清单与规模)
2. [主类 Mixin 体系（38 + 1 = PrivateCompanionPlugin）](#2-主类-mixin-体系)
3. [生命周期：构造 → initialize → 事件循环 → terminate](#3-生命周期)
4. [启动四阶段（plugin_bootstrap.py）](#4-启动四阶段)
5. [配置系统：30+ 章节、6 个初始化函数](#5-配置系统)
6. [存储层：JSON 与 SQLite 双后端](#6-存储层)
7. [AstrBot 钩子全景：优先级与执行顺序](#7-钩子全景)
8. [私聊消息链 on_private_message](#8-私聊消息链)
9. [群聊消息链 on_group_message](#9-群聊消息链)
10. [主动消息系统：候选生命周期 9 阶段](#10-主动消息系统)
11. [状态 / 日程 / 细化引擎 daily_state.py](#11-状态引擎)
12. [关系系统：阶段 / 互动档位 / 关系网 / 边界反馈](#12-关系系统)
13. [情感系统：domains/affect](#13-情感系统)
14. [社交领域：domains/social](#14-社交领域)
15. [群聊能力：观察 / 唤醒 / 续接 / 插话](#15-群聊能力)
16. [命令系统：陪伴 / 陪伴群 + 60+ 子命令](#16-命令系统)
17. [LLM 工具：20+ 个 `pc_*` 工具](#17-llm-工具)
18. [扩展 API：8 个 CapabilityFamily](#18-扩展-api)
19. [联动桥接：ProactiveChat / Memory / Image / Content](#19-联动桥接)
20. [能力子系统：creative / dreaming / TTS / private_image / news / qzone / memo](#20-能力子系统)
21. [陪伴面板：page_api.py 60+ 路由](#21-陪伴面板)
22. [独立 WebUI：端口 6190 + Token 登录](#22-独立-webui)
23. [迁移体系：5 条迁移子模块](#23-迁移体系)
24. [关键设计哲学](#24-关键设计哲学)
25. [附录：核心文件 → 行数索引](#25-附录核心文件索引)

---

## 0. 插件身份与定位

| 项 | 值 |
|---|---|
| 包名 | `astrbot_plugin_private_companion` |
| 显示名 | **我会永远陪着你** |
| 版本 | `6.4.5b` |
| AstrBot 依赖 | `>= 4.22.0` |
| 官方平台 | `aiocqhttp`、`qq_official` |
| 管理入口 | AstrBot 插件扩展页"陪伴面板"，可选独立 WebUI（默认关闭） |
| 数据存储 | JSON（默认兼容）或 SQLite |
| GitHub | `https://github.com/menglimi/astrbot_plugin_private_companion` |
| 交流群 | QQ `1097283005` |

身份常量定义在 [`plugin_identity.py`](./plugin_identity.py)：

```python
PLUGIN_ID = "astrbot_plugin_private_companion"
PLUGIN_DISPLAY_NAME = "我会永远陪着你"
PLUGIN_VERSION = "6.4.5b"
PLUGIN_DATA_DIRECTORY_KEY = PLUGIN_ID
```

并对外提供 `is_exact_plugin_id()` / `is_module_path_for_package()` 等身份匹配工具（不会被前缀误中）。

### 0.1 插件是什么 / 不是什么

- **是**：面向 AstrBot 的**持续型 AI 陪伴核心**。把"角色状态、日程、关系、生活事件"做成统一上下文，给私聊、群聊、主动消息、外部能力提供**连续、可配置、可审计**的运行基础。
- **不是**：单一"早安晚安定时器"，也不是聊天 bot 模板。**不替代 AstrBot 主回复人格**——AstrBot 的人格负责"我是谁、我说什么"，本插件负责"我现在状态如何、我和他关系怎样、什么时候主动找他说话"。

### 0.2 设计目标（来自 README）

1. 状态、日程、位置、天气、梦境、日记、技能、个人目标保持**连续性**。
2. 主动消息**来源可解释**（来自日程？事件？记忆回响？新闻？），与随机问候、重复触达、错误时段问候区分开。
3. 私聊、群聊、多种消息媒介之间维持**一致的身份、关系、权限边界**。
4. 主动候选、拦截原因、改写结果、发送状态、Token 消耗**全程记录**用于诊断。
5. 高成本 / 设备权限 / 外部服务能力**默认关闭、按需启用**，缺失时**降级而非崩溃**。

### 0.3 与 AstrBot 的对接面

| 入口 | 装饰器 | 用途 |
|---|---|---|
| 插件生命周期 | `@filter.on_plugin_loaded / @filter.on_plugin_unloaded` | 跨插件桥接缓存失效 |
| LLM 请求钩子 | `@filter.on_llm_request`、`@_ON_WAITING_LLM_REQUEST` | 模型路由替换（关键词 / DeepSeek 峰时 / 敏感拒答） |
| LLM 响应钩子 | `@filter.on_llm_response` | 去抖收敛 / 替换上下文清理 |
| 事件消息 | `@filter.event_message_type(...)` | 私聊、群聊、ALL 共 11 个钩子 |
| 命令 | `@filter.command("陪伴"/"陪伴群", alias=...)` | 用户命令入口 |
| LLM 工具 | `@filter.llm_tool(name=...)` | 主模型可调用 20+ 个 `pc_*` 工具 |
| 路由注册 | `context.register_web_api(prefix, handler, methods, desc)` | 陪伴面板 HTTP API |
| 扩展 API | `get_private_companion_api()` | 其他 AstrBot 插件调用本插件 |

---

## 1. 文件清单与规模

### 1.1 总体规模

| 维度 | 数量 |
|---|---|
| 仓库总体 | **530 MB** |
| 根目录 `.py` 文件 | **182** 个 |
| 根目录 `.py` 总行数 | **266 164** 行 |
| 子目录 | `android/`、`astrbot_plugin_nene_boundary/`、`astrbot_plugin_temp_emotion/`、`companion/`、`data/`、`dist/`、`docs/`、`domains/`、`pages/`、`scripts/`、`storage/`、`tests/` |
| 单文件最大 | `page_api.py` **31 111 行**（陪伴面板 HTTP API） |
| 单文件次大 | `daily_state.py` **18 627 行**（状态 / 日程 / 细化 / 天气 / 日记 / 技能 / 目标） |
| 单文件第三 | `proactive_message.py` **18 038 行**（主动消息生成 / 发送） |
| 单文件第四 | `main.py` **21 112 行**（插件主类 / 事件钩子 / 命令 / LLM 工具） |

### 1.2 顶级 `.py` 文件分组速查（按职责）

| 类别 | 代表文件 | 关键概念 |
|---|---|---|
| **入口** | `main.py`、`plugin_bootstrap.py`、`plugin_identity.py` | `PrivateCompanionPlugin(Star)`、启动 4 阶段、身份常量 |
| **存储** | `core_store.py`、`storage/*` | JSON / SQLite 双后端、`StoreManager`、路径票据 |
| **状态** | `daily_state.py`、`daily_state_tick.py`、`agenda_runtime.py`、`agenda_contracts.py`、`unified_agenda.py`、`schedule_authority.py`、`schedule_reconciler.py` | 每日状态 / 日程 / 细化 / Tick / 排程权限 |
| **主动消息** | `proactive.py`、`proactive_engine.py`、`proactive_message.py`、`proactive_routes.py`、`proactive_chat_runtime_bridge.py` | 候选 9 阶段、调度器、路由注册表、桥接 |
| **关系** | `relationship_ledger.py`、`relationship_policy.py`、`relationship_affinity_runtime.py`、`relationship_event_policy.py`、`companion_interaction_expression.py` | 阶段 / 互动档位 / 关系网 / 表达决策 |
| **群聊** | `group_observation.py`、`group_wakeup.py`、`group_member_safety.py`、`group_cycle_boundary.py`、`group_prompt_context.py`、`group_context_interception.py` | 观察 / 唤醒 / 续接 / 插话 / 成员安全 |
| **图片** | `private_image.py`、`image_companion_bridge.py`、`nai_image_bridge.py`、`photo_reference_*.py`、`photo_wardrobe_decision.py`、`photo_prompt_context.py`、`photo_generation_scope.py` | 视觉理解 / 生图 / 参考图 / 服装意图 |
| **TTS** | `tts_enhancement.py`、`tts_tool_sanitizer.py` | 文本转换 / 链式分段 / Fish Audio / 本机播放 |
| **梦境 / 日记 / 创作** | `dreaming.py`、`daily_review.py`、`creative.py` | 梦境池 / 日记 / 创作项目生命周期 |
| **记忆 / 用户 / 世界书** | `user_memory.py`、`worldbook.py`、`memo_notes.py`、`authoritative_private_memory.py`、`memory_page_snapshot.py`、`memory_context_policy.py` | 长期记忆 / 知识库 / 备忘 |
| **命令 / 事件分发** | `command_handlers.py`、`event_dispatch.py`、`message_pipeline.py`、`passive_state_pipeline.py`、`busy_reply_gate.py`、`silent_reply_gate.py`、`user_rest_gate.py`、`runtime_scene_resolver.py` | 命令实现 / 事件链 / 消息流水线 / 静默闸门 |
| **新闻 / B站 / 搜索** | `news_exploration.py`、`creative.py` 中的探索块 | 新闻订阅 / AI日报 / B站 / Web 探索 |
| **QQ 空间** | `qzone_*.py`（6 个文件） | 说说 / 评论 / 点赞 / 发布 |
| **LLM 调用** | `model_routing.py`、`token_budget.py`、`llm_tool_actions.py` | 模型路由 / Token 限额 / 工具调用 |
| **面板 / WebUI** | `page_api.py`、`page_api_*.py`、`standalone_webui.py` | 60+ 路由 / port 6190 / Token 登录 |
| **扩展 API** | `extension_api_*.py` | 8 个 Capability Family |
| **配置 / 迁移** | `config_migration.py`、`migration_*.py`、`constants.py` | 30+ 配置章节 / 5 大迁移模块 |
| **情绪 / 社交领域** | `domains/affect/*`、`domains/social/*` | 情感事件 / 群聊名场面 / 接梗边界 |
| **其他能力** | `body_monitor_integration.py`、`balance_awareness.py`、`forward_message.py`、`atrelay.py`、`debug_runtime.py`、`logging_util.py`、`runtime_compat.py` 等 | 设备联动 / 余额感知 / 合并转发 / @ 中继 |

### 1.3 子目录速查

| 子目录 | 内容 |
|---|---|
| `android/` | 移动端 APK、配置文件、配套脚本（用于 `我会来到你身边` 联动插件） |
| `astrbot_plugin_nene_boundary/` | 边界判断子插件（nene） |
| `astrbot_plugin_temp_emotion/` | 临时情绪子插件 |
| `companion/` | 桥接实现和协议合同的归档子包 |
| `data/` | `cmd_config.json`、插件数据、`t2i_templates`、临时缓存 |
| `dist/` | `build_plugin_package.py` 输出的发布 ZIP |
| `docs/` | 设计文档（**本文档也归入此处**） |
| `domains/` | 领域拆分（`affect` / `social`） |
| `pages/companion-panel/` 与 `pages/陪伴面板/` | 陪伴面板前端资源（HTML/JS/CSS） |
| `scripts/` | `build_plugin_package.py`（打包）、`ci_static_checks.py`（CI） |
| `storage/` | `backend_base.py`、`factory.py`、`json_backend.py`、`sqlite_backend.py`、`store_manager.py`、`path_generation.py`、`migration.py` |
| `tests/` | 回归测试 |

---

## 2. 主类 Mixin 体系

[`main.py:1330`](./main.py) 的 `PrivateCompanionPlugin` 不是普通类，而是一个**38 个 Mixin 叠加 + 基类 Star** 的"巨型继承图"：

```python
class PrivateCompanionPlugin(
    CoreStoreMixin,
    PlatformCompatibilityMixin,
    AstrBotKnowledgeMixin,
    IntegrationStatusMixin,
    BusyReplyGateMixin,
    ChronotypeMixin,
    MemoryCompanionAdapterMixin,
    PrivateImageMixin,
    ForwardMessageMixin,
    QzoneMixin,
    TokenBudgetMixin,
    BalanceAwarenessMixin,
    WorldbookMixin,
    UserMemoryMixin,
    ContentCompanionBridgeMixin,
    CreativeMixin,
    ProactiveMixin,
    ProactiveEngineMixin,
    GameIntegrationMixin,
    PlaceCognitiveMapMixin,
    SceneContextMixin,
    ProactiveMessageMixin,
    ImageCompanionBridgeMixin,
    NAIImageBridgeMixin,
    DailyStateMixin,
    AgendaRuntimeMixin,
    DailyReviewMixin,
    StateViewsMixin,
    InteractionUtilsMixin,
    LlmToolActionsMixin,
    CommandHandlersMixin,
    TtsEnhancementMixin,
    TtsToolSanitizerMixin,
    RealityCompanionBridgeMixin,
    GroupWakeupMixin,
    GroupObservationMixin,
    GroupMemberSafetyMixin,
    EventDispatchMixin,
    ReadingArchiveMixin,
    NewsExplorationMixin,
    SelfTimelineMixin,
    AtRelayMixin,
    Star,
):
    ...
```

> **每个 Mixin 都是一个独立子系统**。这种"用 Mixin 而非组合"的设计让单文件代码聚拢到具体概念，但也带来 21 112 行的主文件。

### 2.1 Mixin 子系统对照表

| Mixin | 文件 | 主要职责（关键方法名） |
|---|---|---|
| `CoreStoreMixin` | `core_store.py` | `_load_data_sync` / `_save_data_sync` / `_save_data_now_sync` / `_atomic_write_data_file_sync` |
| `PlatformCompatibilityMixin` | `platform_compat.py` | 平台能力探测（QQ 官方 vs OneBot 能力差异） |
| `AstrBotKnowledgeMixin` | `astrbot_knowledge.py` | 引用 AstrBot 知识库、roleplay 资料整合 |
| `IntegrationStatusMixin` | `integration_status.py` | 集成状态汇总（联动插件、扩展、桥接） |
| `BusyReplyGateMixin` | `busy_reply_gate.py` | 繁忙时段延迟普通回复、保留临时提醒 |
| `ChronotypeMixin` | `chronotype.py` | 时型 / 生理节律推断 |
| `MemoryCompanionAdapterMixin` | `memory_companion_adapter.py` | Memory 桥接（旧 + 新契约） |
| `PrivateImageMixin` | `private_image.py` | 私聊图片理解 / 缓存 / 自识别 |
| `ForwardMessageMixin` | `forward_message.py` | 合并转发解析 / 转写 |
| `QzoneMixin` | qzone 总合（`qzone_runtime.py` 等） | QQ 空间 CRUD + 桥接 |
| `TokenBudgetMixin` | `token_budget.py` | Token 限额 / 软限额 / 单卡预算 |
| `BalanceAwarenessMixin` | `balance_awareness.py` | API 余额感知（阈值 / 百分比 / 冷却） |
| `WorldbookMixin` | `worldbook.py` | 世界书条目、关联引用 |
| `UserMemoryMixin` | `user_memory.py` | 用户画像 / 偏好 / 习惯 / 关系状态 |
| `ContentCompanionBridgeMixin` | `content_companion_bridge.py` | 内容创作桥接 |
| `CreativeMixin` | `creative.py` | 旧版创作兼容；由桥接层委托 |
| `ProactiveMixin` | `proactive.py` | 私聊主动排程 / 调度器入口 |
| `ProactiveEngineMixin` | `proactive_engine.py` | 候选派生 / 评分 / 时间窗 / 复核 |
| `GameIntegrationMixin` | `game_integration.py` | 游戏事件余韵（来自扩展 API） |
| `PlaceCognitiveMapMixin` | `place_cognitive_map.py` | 地点认知地图 |
| `SceneContextMixin` | `scene_context.py` | 结构化场景快照（温度/体感/睡眠阶段） |
| `ProactiveMessageMixin` | `proactive_message.py` | 主动消息生成 / 图片 / 发送编排 |
| `ImageCompanionBridgeMixin` | `image_companion_bridge.py` | Image 扩展桥接 |
| `NAIImageBridgeMixin` | `nai_image_bridge.py` | NovelAI 生图桥接 |
| `DailyStateMixin` | `daily_state.py` | 每日状态 / 日程 / 细化 / 天气 / 日记 |
| `AgendaRuntimeMixin` | `agenda_runtime.py` | 日程运行时 |
| `DailyReviewMixin` | `daily_review.py` | 日回顾 / 复盘 |
| `StateViewsMixin` | `state_views.py` | 状态视图（供面板 API） |
| `InteractionUtilsMixin` | `interaction_utils.py` | 互动档位 / 表达决策工具 |
| `LlmToolActionsMixin` | `llm_tool_actions.py` | LLM 工具的辅助动作 |
| `CommandHandlersMixin` | `command_handlers.py` | 命令实现体 |
| `TtsEnhancementMixin` | `tts_enhancement.py` | TTS 强化 / 链式分段 |
| `TtsToolSanitizerMixin` | `tts_tool_sanitizer.py` | TTS 工具历史脱敏 |
| `RealityCompanionBridgeMixin` | `body_monitor_integration.py`（含现实触及桥接） | 现实设备联动桥 |
| `GroupWakeupMixin` | `group_wakeup.py` | 群聊唤醒 / 续接 |
| `GroupObservationMixin` | `group_observation.py` | 群聊观察 / 群友 / 黑话 / 话题 |
| `GroupMemberSafetyMixin` | `group_member_safety.py` | 群成员安全审核 |
| `EventDispatchMixin` | `event_dispatch.py` | 事件分发 / 去抖 / 模型替换 |
| `ReadingArchiveMixin` | `reading_archive.py` | 阅读档案（资料柜的阅读清单） |
| `NewsExplorationMixin` | `news_exploration.py` | 新闻 / B站 / Web 探索 |
| `SelfTimelineMixin` | `self_timeline.py` | Bot 自我时间线（用于叙事） |
| `AtRelayMixin` | `atrelay.py` | @ 中继 / 跨群转述 |

### 2.2 关键全局字段（`self.xxx` 集合）

构造函数是"四段委托"，本身只填两个字段：

```python
def __init__(self, context, config):
    super().__init__(context)
    initialize_plugin_entrypoint_state(self, context, config,
                                        extension_api_factory=PrivateCompanionExtensionAPI)
    initialize_plugin_config(self, config)
    initialize_plugin_runtime(self)
    initialize_plugin_post_runtime_state(self, config)
    getattr(self, "_initialize_lab_fixture_adapter", lambda: None)()
    self.req041_observability = Req041Observability()
    self._req041_runtime_boot_ref = f"boot-{id(self)}"
```

**真正字段在四阶段里写入**，下面按"用途"分类摘要（前缀代表来源阶段）：

**入口阶段（`initialize_plugin_entrypoint_state`）**

| 字段 | 类型 | 用途 |
|---|---|---|
| `self.extension_api` | `PrivateCompanionExtensionAPI` | 对外扩展 API |
| `self._persistence_owner_token` | `str` | 持久化代际令牌 |
| `self._external_proactive_abilities` | `dict[str, dict]` | 外部主动能力注册表 |
| `self._external_realtime_activities` | `dict[str, dict]` | 外部实时活动 |
| `self._external_realtime_continuity` | `dict[str, dict]` | 短期通话连续性 |
| `self.config` | `AstrBotConfig` | 原始配置 |
| `self.plugin_identity` | `dict` | `{plugin_id, display_name, version, ...}` |
| `self.runtime_capabilities` | `dict` | 运行时能力探测结果 |
| `self.bot_personal_capabilities` | `dict` | Bot 契约自检 |

**核心与关系配置阶段（`_initialize_core_and_relationship_config`）**

`data_dir` / `data_file` / `storage_backend` / `storage_sqlite_path` / `enable_multi_persona_mode` / `multi_persona_ids` / `plugin_specific_persona_id` / `_persona_profiles_dir` / `_persona_data_profiles` / `enable_standalone_webui` / `standalone_webui_host` / `standalone_webui_port` / `standalone_webui_access_token` / `standalone_webui_session_ttl_hours` / `enable_store_control_tag_sanitization` / `enable_outbound_secret_redaction` / `enable_proactive_only_mode` / `proactive_intensity_preset` / `proactive_preempt_queue_*` / `check_interval_seconds` / `idle_minutes` / `min_interval_minutes` / `enable_proactive_burst` / `proactive_burst_*` / `proactive_hour_activity_curve` / `proactive_unanswered_*` / `friend_unanswered_max_cooldown_hours` / `timer_pre_silence_minutes` / `max_daily_messages` / `enable_reply_interception_forward` / `reply_interception_forward_*` / `enable_balance_awareness` / `balance_*` / `inbound_message_debounce_seconds` / `enable_recall_enhancement` / `enable_forbidden_word_recall` / `recall_*` / `_recalled_message_ids` / `_recall_message_cache` / `_recent_outbound_text_guard` / `enable_message_debounce` / `enable_smart_message_debounce` / `text_message_debounce_seconds` / `image_message_debounce_seconds` / `forward_message_debounce_seconds` / `smart_message_debounce_*` / `enable_private_image_self_recognition` / `private_image_vision_*` / `enable_group_image_understanding` / `enable_group_image_wakeup` / `group_image_vision_*` / `enable_context_image_captioning` / `enable_private_image_gif_enhancement` / `enable_group_conversation_followup` / `enable_group_air_reply_guard` / `group_air_guard_*` / `quiet_hours` / `default_style` / `reply_style_prompt` / `enable_persona_voice_channels` / `persona_{conversation,creative,planning,inner,proactive}_voice_prompt` / `worldview_adaptation_mode` / `worldview_adaptation_prompt` / `default_nickname` / `enable_auto_user_profile_creation` / `auto_profile_platforms` / `default_nickname_strategy` / `default_proactive_enabled` / `default_proactive_daily_limit` / `portrait_global_mode` / `require_private_opt_in` / `target_user_ids` / `private_user_aliases` / `private_user_delivery_aliases` / `target_platform` / `default_interaction_band` / `enable_custom_relationship_stage_policy` / `relationship_stage_policy` / `relationship_stage_provider_routes` / `relationship_positive_stage_cap_key` / `normal_interaction_band_cap` / `owner_group_*_projection` / `content_tiers` / `flirt_content_tier` / `owner_exclusive_*` / `relationship_event_window_minutes` / `positive_event_cap` / `negative_event_cap` / `enable_group_relationship_affinity` / `group_relationship_affinity_*`

**世界与模型阶段（`_initialize_world_and_model_config`）**

`relationship_positive_daily_cap` / `relationship_decay_*` / `environment_perception`（开关、时区、节假日平台/模型、世界观、农历/节气/黄历） / `provider_config_mode` / `model_token_*` / `model_timeout_*` / `model_fallback_overrides` / `enable_llm_streaming` / `deepseek_peak_*` / `page_font_family` / `page_theme` / `*_PROVIDER_ID`（FAST_RESPONSE、COMPLEX_REASONING、CREATIVE、LLM、DAILY_PLAN、VOICE_PROMPT、HISTORY_SUMMARY_*） / `daily_token_limit` / `daily_token_soft_limit` / `humanized_states` / `advanced_cycle_strategy`（六阶段参数） / `rest_reply_simulation` / `rest_wakeup_provider_id` / `busy_reply_gate_*` / `enhanced_dreams` / `passive_states_injection_*` / `proactive_share_probability` / `daily_greetings_*` / `creative_writing_*` / `photo_prompt_provider_id`

**主动与反应阶段（`_initialize_proactive_and_reaction_config`）**

`history_max_chars` / `enable_proactive_chat_integration` / `body_monitor_integration_*` / `bridge_review_mode` / `collision_window_seconds` / `llm_proactive_persona_judge_*` / `reaction_expression_*`（私聊/主动/群聊三组开关、概率、冷却、low_latency、候选数、embedding_provider_id、超时、score_threshold、回填、semantic_trigger_enabled、delivery_mode、image_format）/ `maslow_motivation_*` / `personality_iteration_experiment` / `enable_llm_timer_scheduling` / `proactive_decorating_hooks` / `precise_platform_send_*` / `quote_group_reply_*` / `quote_once_per_target` / `quote_interjection` / `quote_private_proactive` / `quote_skip_short_reply_chars` / `quote_target_strategy` / `segmented_proactive_*`（regex/words、mode、scope、threshold、min/max_segment_chars、content_cleanup、content_replacement、interval_method、send_as_forward、voice/image/at/face/other 策略、component_order）/ `proactive_prompt_template` / `max_proactive_plan_lag_minutes` / `proactive_dedup_*` / `detail_enhancement_*` / `narration_provider_id` / `photo_reference_library` / `structured_reference_assets` / `owned_reaction_assets` / `comfyui_workflow_names` / `photo_persona_reference_image_path` / `photo_generation_backend` / `generated_photo_cleanup_*` / `custom_photo_tool_*` / `local_photo_load_guard` / `external_image_api_*` / `proactive_dedup_enabled` / `proactive_dedup_policies`

**图片与表达阶段（`_initialize_photo_and_expression_config`）**

`backup_external_image_api_*` / `external_image_api_endpoints` / `photo_generation_prompt_format` / `photo_generation_style` / `photo_generation_negative_prompt` / `photo_generation_mode` / `text2img_*` / `selfie_*` / `edit_negative_prompt` / `fixed_prompt` / `photo_generation_scene_presets` / `enable_bot_relationship_network` / `bot_relationship_network_cards` / `photo_reference_catalog` / `_startup_photo_reference_catalog_migration_pending` / `enable_daily_outfit_photo` / `creative_cover_generation_*` / `rotation_days` / `natural_language_photo_generation_*`（mode、max_daily、backup_count、extra_prompt） / `weather_*`（source、api_key、city、amap_*、location、lat、lon、refresh、alerts_min_severity、api_host、token、alert_api_host、alert_token、alert_refresh_minutes） / `environment_change_proactive_*` / `yesterday_screen_diary_context_*` / `detail_enhancement_lead_minutes` / `enable_daily_diary` / `daily_diary_*` / `enable_daily_review` / `daily_review_*` / `daily_case_review_experiment` / `important_date_lookahead_days` / `enable_photo_text_action` / `photo_generation_allowed_scopes` / `enable_photo_reference_image` / `enable_group_nsfw_private_fallback` / `group_nsfw_review_*` / `enable_screen_glance_action` / `enable_poke_action` / `enable_voice_action` / `poke_max_times` / `poke_cooldown` / `voice_max_chars` / `photo_max_daily` / `proactive_photo_text_probability` / `screen_peek_max_daily` / `screen_peek_cooldown` / `goodnight_screen_check` / `unanswered_screen_peek_followup` / `qq_presence_sync_custom` / `enable_mai_style_integration` / `companion_memory` / `expression_learning` / `expression_*_mode` / `private_group_source_mode_ids`

**复核与群聊阶段（`_initialize_review_and_group_config`）**

`expression_group_learning_*` / `expression_manual_review` / `expression_style_review` / `intent_emotion_analysis` / `enable_passive_response_review` / `enable_framework_error_leak_guard` / `enable_proactive_message_review` / `smart_silence_*` / `passive_review_mode` / `passive_review_strength` / `proactive_review_mode` / `proactive_review_strength` / `proactive_review_*_threshold` / `passive_topic_suppression` / `enable_relationship_analysis` / `enable_relationship_statemachine` / `enable_emotion_simulation` / `violation_penalties` / `enable_boundary_*` / `boundary_*_penalty_*` / `boundary_*_stage_*_points` / `boundary_cold_minutes` / `boundary_apology_*` / `boundary_recover_ratio_*` / `enable_boundary_vent` / `vent_targets` / `vent_scene_template` / `vent_baseline` / `vent_tone_*` / `_vent_probability_*` / `enable_boundary_owner_report` / `owner_report_targets` / `owner_report_scene_template` / `owner_report_baseline` / `owner_report_tone_*` / `_owner_report_probability_*` / `violation_recovery_minutes_per_point` / `enable_llm_emotion_judgement` / `emotion_judgement_mode` / `emotional_gate_hurt` / `emotional_gate_refuse` / `emotional_gate_recovery_per_hour` / `emotional_gate_max_hurt_minutes` / `enable_dialogue_episode_memory` / `enable_open_loop_tracking` / `enable_user_habit_learning` / `enable_food_menu_recommendation` / `enable_meal_care_proactive` / `meal_care_max_daily` / `meal_care_interval` / `meal_care_followup` / `user_habit_min_count` / `user_habit_max_items` / `enable_skill_growth_simulation` / `skill_growth_rate` / `skill_growth_custom_skills` / `skill_growth_passive_inject` / `skill_growth_schedule_influence` / `skill_growth_strength` / `enable_personal_goals` / `personal_goals_auto_progress` / `personal_goals_cooldown` / `personal_goals_stall_days` / `enable_memory_refresh` / `memory_refresh_interval_minutes` / `max_companion_memory_items` / `max_learned_expression_items` / 各 `*_PROVIDER_ID`（mai_style、companion_memory、dialogue_episode、relationship_analysis、response_review、troubleshooting、daily_review、emotion_judgement）/ `passive_topic_memory_hours` / `episode_refresh_messages` / `episode_refresh_minutes` / `max_dialogue_episodes` / `enable_group_companion` / `group_access_mode` / `target_group_ids` / `group_whitelist_ids` / `group_blacklist_ids` / `require_target_group` / `enable_group_slang_learning` / `group_member_profiles_legacy_*` / `enable_group_member_safety` / `group_member_safety_review_mode` / `group_member_safety_hidden_marker_mode` / `group_member_safety_strike_threshold` / `group_member_safety_window_days` / `group_member_safety_block_hours` / `group_member_safety_min_confidence` / `group_member_safety_exempt_managers` / `group_member_safety_audit_limit` / `enable_group_context_injection` / `enable_group_history_injection` / `intercept_astrbot_group_context` / `enable_injection_guard` / `enable_persona_denoise` / `forward_message_adaptation_mode` / `forward_message_*` / `enable_group_scene_awareness` / `group_scene_awareness_*` / `enable_group_reality_promise_guard` / `enable_group_wakeup_enhancement` / `group_wakeup_direct_words` / `group_wakeup_owner_direct_words`

**群聊与 Provider 阶段（`_initialize_group_and_provider_config`）**

校正后的 `group_wakeup_context_words = ["机器人","bot"]` / `group_wakeup_interest_keywords` / `group_wakeup_probability` / `group_wakeup_question_threshold` / `group_wakeup_cold_group_*` / `group_wakeup_idle_*` / `group_wakeup_cooldown` / `group_wakeup_log_limit` / `group_wakeup_short_text_wait_seconds` / `enable_group_high_intensity_mode` / `group_high_intensity_window` / `group_high_intensity_threshold` / `group_high_intensity_cooldown` / `group_high_intensity_merge_seconds` / `group_high_intensity_max_merge_messages` / `group_high_intensity_merge_scope` / `enable_group_interjection` / `enable_group_repeat_follow` / `group_repeat_follow_threshold` / `group_repeat_follow_distinct_users_only` / `group_repeat_follow_probability` / `group_repeat_follow_interrupt_*` / `group_interruption_text` / `group_interruption_image_path` / `group_interjection_min_interval` / `group_interjection_max_daily` / `max_group_recent_messages` / `max_group_slang_terms` / `enable_group_topic_threads` / `enable_group_episode_memory` / `enable_group_interjection_feedback` / `enable_group_slang_meanings` / `enable_group_slang_web_search` / `group_slang_terms` / `group_slang_results` / `enable_group_relationship_graph` / `enable_group_privacy_guard` / `enable_third_party_portrait_guard` / `enable_worldbook_member_recognition` / `enable_atrelay_tools` / `enable_cross_user_memory_bridge` / `enable_owner_only` / `worldbook_first` / `cached_minutes` / `enable_sensitive_confirm` / `enable_llm_rewrite` / `default_relay_style` / `multi_target_limit` / `enable_worldbook_auto_import` / `enable_worldbook_match_aliases` / `enable_worldbook_self_registration` / `worldbook_block_words` / `worldbook_block_reply`（默认 "这个称呼我不记。"） / `enable_auto_pending_observations` / `worldbook_member_inject_limit` / `worldbook_config_paths` / 各 `*_PROVIDER_ID`（GROUP_INTERJECT、EPISODE、SLANG、FOLLOWUP_JUDGE、MEMBER_SAFETY）/ `enable_livingmemory_integration` / `livingmemory_tool_name`（默认 `recall_long_term_memory`） / `livingmemory_context_timeout_seconds` / `livingmemory_emotional_drift` / `livingmemory_cross_window_emotion` / `livingmemory_dream_fragment` / `livingmemory_open_loop_search` / `livingmemory_feature_context` / `livingmemory_private_recall` / `livingmemory_top_k` / `livingmemory_max_chars` / `enable_bilibili_integration` / `bilibili_*` / `enable_news_integration` / `news_*` / `ai_daily_watch_*` / `enable_web_exploration` / `web_exploration_*` / `qzone_*`（cookie、life_publish、emotional_vent、comment_inbox）/ `plugin_vision_provider_id` / `group_episode_refresh_minutes` / `group_slang_summary_minutes` / `max_topic_threads` / `max_eps` / `max_relationship_edges` / 向后兼容别名 `allow_photo_text_action` / `allow_screen_peek_action` / `allow_poke_action` / `allow_voice_action` 等于对应 `enable_*`

**Runtime 阶段（`initialize_plugin_runtime`）**

`asyncio.Lock` × 6：`_data_lock` / `_daily_state_generation_lock` / `_daily_diary_generation_lock` / `_daily_review_generation_lock` / `_conversation_db_lock` / `_framework_agent_lock`
`_stop_event` / `_task`（主动调度器）/ `_startup_maintenance_task` / `_startup_background_tasks` / `_lifecycle_background_tasks` / `_group_image_understanding_tasks` / `_data_save_task` / `_dirty` / `_deleted` / `_dirty_since` / `_section_revisions` / `_full_revision` / `_full_since` / `_revision` / `_max_delay_seconds` / `_retry_base_seconds` / `_retry_max_seconds` / `_persona_data_save_tasks` / `_maintenance_failure_cooldowns` / `framework_captured_send_cache` / `deferred_photo_cache` / `_segmented_reply_remainder_locks` / `_input_status_timestamps` / `_input_status_tasks` / `_inbound_activity_scopes` / `_reply_turn_generation_by_scope` / `_bot_personal_decorating_recent_send` / `self.data`（`PersistedData`）/ `_qzone_last_bot` / `_bot_personal_outboxes` / `_bot_personal_outbox` / `self._body_monitor_integration` / `self._proactive_chat_runtime_bridge` / `self.page_api` / `self.standalone_webui`

**Post-Runtime 阶段（`initialize_plugin_post_runtime_state`）**

`self.enable_p5_source_observer` / `self.enable_p5_b1_recall_gate` / `self.enable_p5_b1_bridge_gate` / `self.p5_attestation_registry = P5AttestationRegistry()` / `self.unified_person_registry = UnifiedPersonRegistry(self.data)` / `self.req041_migration_coordinator = MigrationCoordinator(self.data_dir)` / `self.req041_migration_outbox = MigrationOutbox(Path(self.data_dir) / "req041_migration_outbox.db")` / `self.req041_migration_status` / `self.req041_scoped_projection_*`

---

## 3. 生命周期

### 3.1 `__init__` — 几乎不做任何事

四段委托 → `_initialize_lab_fixture_adapter` → 设两个 req041 字段。所有真正字段在 4 个 bootstrap 函数里写入（见 §4）。

### 3.2 `initialize(self)` — 启动主流程（`main.py:7970`）

按顺序：

1. `await self._initialize_before_publication()`
2. `try: await resume_story_handoff(self)` → 捕获 `StoryAuthorityError` 记 warning 等待下次重放
3. 检查 `extension_api._activate_story_migration_api` 是否激活，未激活 → `return`
4. **在 `_private_companion_runtime.lock` 内**（关键代际）：
   - `store_manager.activate_persistence_generation()`；缺则 `activate_persistence_owner(owner_token, [data_file])`
   - 若上一个 `active_plugin` 是另一个 instance → `previous_api._supersede_story_migration_api()`
   - `_private_companion_runtime.active_plugin = self`
5. `_private_companion_plugin = self`（模块级全局发布）

#### 3.2.1 `_initialize_before_publication` 步骤（`main.py:8023`）

1. `self._repair_private_companion_handler_bindings()`
2. 检测并警告旧版 `enabled=false` 残留（已废弃）
3. `self._log_registered_command_handlers()`
4. `self._install_send_message_to_user_tool_sanitizer()`
5. 若 `runtime_persona_setting(...,'enable_relationship_boundary_feedback',True)` → `_register_relationship_boundary_proactive_ability()`
6. `self._schedule_default_persona_prompt_refresh()`
7. `await self._body_monitor_integration.set_enabled(self.enable_body_monitor_integration)`
8. `self._agenda_prepare_store()`（日程 schema 迁移）
9. **在 `async with self._data_lock:` 内**：
   - 对 `self.data["users"]` 调用 `_sanitize_user_behavior_habit_patterns` 清旧习惯
   - 若 `default_enable_configured_targets=True` → `_sync_configured_targets()` 并置 `changed=True`
   - `recovered_troubleshooting = self._recover_stale_troubleshooting_proactive_proactive_plans()`
   - `self._prime_enabled_user_schedules()`
   - 任一改动 → `needs_startup_save=True`
10. 任一改动：`self._schedule_data_save(full_scope="startup_migration"|"startup_maintenance", delay=0.5)`
11. **启动 6 个后台任务**（`_create_startup_background_task`）：
    - `req041_automatic_migration` → `_req041_initialize_automatic_migration`
    - `req041_memory_scope_rebind` → `_req041_run_memory_scope_rebind`
    - `reset_stale_qq_presence` → `_reset_stale_qq_presence_if_needed`
    - `prepare_today` → `_startup_prepare_today`
    - `daily_review` → `_daily_review_loop`（仅当 `enable_daily_review=True` 或多人格模式）
    - `refresh_balance_awareness` → `_maybe_refresh_balance_awareness`（仅 `enable_balance_awareness=True` 时）
    - `refresh_passive_injection_cache` → `_refresh_passive_injection_cache`
12. **`self._task = asyncio.create_task(self._scheduler_loop())`**（主动消息调度循环）
13. `self._startup_maintenance_task = asyncio.create_task(self._run_startup_background_maintenance())`
14. `await self._proactive_chat_runtime_bridge.start()`
15. 若 `standalone_webui is not None` → `await standalone_webui.start()`

### 3.3 事件循环 — 持续运行

`self._scheduler_loop()` 每 60s 触发一次（详见 §10）。其他钩子按 AstrBot 调度器执行（详见 §7）。

### 3.4 `terminate(self)` — 关闭清理（`main.py:8404`）

按顺序：

1. `_lab_fixture_adapter.close()`，置 `None`
2. `self.extension_api._close_story_migration_api()`
3. `self._stop_event.set()`
4. `await standalone_webui.stop()`（若非 None）
5. `self._cleanup_framework_delivery_caches(force=True)`
6. `await self._cancel_lifecycle_background_tasks()`（3.0s 超时）
7. `self._memory_companion_invalidate_bridge_cache()`
8. `self.req041_scoped_projection_sync.mark_dirty()`，置 `None`
9. `await self._proactive_chat_runtime_bridge.stop()`
10. 内嵌 `async def cancel_task(task, label, timeout=3.0)` 逐个取消并 `await`：
    - `self._task`（proactive scheduler）
    - `self._passive_input_status_tasks` 字典
    - `_startup_maintenance_task`
    - `_req041_replay_task`（置 `requested=False`）
    - `_req041_scoped_sync_task`（置 `requested=False`）
    - 所有 `_startup_background_tasks` 项
    - 所有 `_group_image_understanding_tasks` 项
    - 所有 `_troubleshooting_proactive_wakeup_tasks` 项
11. `await asyncio.wait_for(self._flush_scheduled_data_save(), timeout=3.0)`；`TimeoutError` 时记 warning
12. `self._termination_flush_already_attempted = True`
13. `await asyncio.wait_for(_close_external_image_download_session(), timeout=3.0)`
14. `final_save_task = asyncio.create_task(self._save_data_on_terminate())`；附 `observe_final_save` 回调（更新 `_termination_save_status`）
15. `await asyncio.wait_for(asyncio.shield(final_save_task), timeout=3.0)`；超时分支记 warning
16. 在 `_private_companion_runtime.lock` 内：若 `active_plugin is self → None`
17. 若 `_private_companion_plugin is self → None`

`_save_data_on_terminate(self)`（`main.py:8595`）按 `store_manager.backend_name` 分叉：`sqlite` 走 `_flush_default_data_save_on_terminate()`；`json` 走 `_data_save_task.shield` + 同步 `to_thread(self._write_data_snapshot_sync, snapshot)`；多 persona 模式遍历 `_persona_data_profiles` 中非 primary 的，对每个人格也 `shield + to_thread` 落盘。

---

## 4. 启动四阶段

[`plugin_bootstrap.py`](./plugin_bootstrap.py) 把插件初始化拆成 4 个阶段，每个阶段都是一组函数，按确定顺序调用。

### 4.1 阶段 1：`initialize_plugin_entrypoint_state`（`L230`）

1. `self.extension_api = extension_api_factory(self)` — 安装 `PrivateCompanionExtensionAPI`
2. `self._persistence_owner_token = str(extension_api._story_migration_generation or f"instance-{id(self)}")` — 持久化代际令牌
3. `self._external_proactive_abilities: dict[str, dict] = {}` — 外部主动能力注册表
4. `self._external_realtime_activities: dict[str, dict] = {}` — 外部实时活动
5. `self._external_realtime_continuity: dict[str, dict] = {}` — 短期通话连续性
6. `self.config = config`
7. `self.plugin_identity = plugin_identity_snapshot()`
8. `self.runtime_capabilities = probe_runtime_capabilities(context=context, plugin_name=PLUGIN_ID, plugin_version=PLUGIN_VERSION)`
9. `contract_issues = tuple(contract_self_check())`
10. `self.bot_personal_capabilities = capability_descriptor(available=not contract_issues, read_only=False).update({state, degraded, warnings})`
11. 若 `contract_issues` 非空 → warning

### 4.2 阶段 2：`initialize_plugin_config`（`L267`）

调用顺序伪代码：

```python
def initialize_plugin_config(self, c):
    _initialize_core_and_relationship_config(self, c)         # basic_config + persona + relationship_owner + group_relationship_affinity
    _initialize_world_and_model_config(self, c)              # world_knowledge + model + token + humanized + rest + dream + diary + creative
    _initialize_proactive_and_reaction_config(self, c)        # proactive + photo + segmented + decoration + dedup
    _initialize_photo_and_expression_config(self, c)         # photo 后端 + weather + diary + outfit + nsfw + poke + goodnight + worldbook 前置
    _initialize_review_and_group_config(self, c)              # review + boundary + emotion_gate + memory_learning + group_companion + group_observation
    _initialize_group_and_provider_config(self, c)           # group_wakeup + group_high_intensity + group_topics + worldbook/atrelay + livingmemory + bilibili/news/web + qzone
    self.enable_p4_b_legacy_score_isolation = self._cfg_bool(c, "enable_p4_b_legacy_score_isolation", False)
```

### 4.3 阶段 3：`initialize_plugin_runtime`（`L2079`）

被装饰器 `@story_startup_sync_operation("startup.store-persona_load")` 包裹。步骤：

1. 清空过程级缓存（bridge cache / emotion bridge fields）
2. `_patch_livingmemory_processor_compat()` + `_report_integrated_feature_conflicts()`
3. **创建 6 个 `asyncio.Lock`**：`_data_lock`、`_daily_state_generation_lock`、`_daily_diary_generation_lock`、`_daily_review_generation_lock`、`_conversation_db_lock`、`_framework_agent_lock`
4. `_stop_event = asyncio.Event()`
5. `_task: asyncio.Task | None = None`（主动 scheduler 占位）
6. 默认 persona prompt cache 一组（5 字段）
7. 数据保存簿记（见 §6）
8. `_persona_data_save_tasks` / `_dirty` / `_deleted` / `_dirty_since` / `_section_revisions` / `_full_revision` / `_revision`
9. 维护账本：`_maintenance_failure_cooldowns`
10. framework captured send cache / deferred photo cache + 时间戳
11. 分段余量锁 `_segmented_reply_remainder_locks` / input status 时间戳 + 任务表 / inbound 活动 scope 表 / 回复回合代际 `_reply_turn_generation_by_scope` / 装扮命令最近发送时间表
12. 启动后台维护容器：`_startup_maintenance_task` / `_startup_background_tasks` / `_lifecycle_background_tasks` / `_group_image_understanding_tasks`
13. `_qzone_last_bot = None`
14. **`self.data = self._load_data_sync()`**（测时，>1200ms 警告）
15. 若可调用 `_invalidate_timezone_derived_state` → 同步 `proactive_runtime.window_timezone` 到 `environment_perception_timezone`，若有变化 `_save_data_sync(sections=...)`
16. 与 `store_manager.next_revision()` 对齐 `_data_save_revision`
17. 尝试 `_retire_legacy_persona_routing_sync()`（失败仅警告）
18. `_migrate_persona_profiles_sync()` 写入 `_persona_settings_migration_status`
19. `self._body_monitor_integration = BodyMonitorIntegration(self)`
20. `_apply_tts_runtime_overrides()`
21. `self._proactive_chat_runtime_bridge = ProactiveChatRuntimeBridge(self)`
22. `self.page_api = None`、`self.standalone_webui = None`
23. `_patch_astrbot_plugin_page_asset_token_compat()` + `_register_page_api_if_available()`

### 4.4 阶段 4：`initialize_plugin_post_runtime_state`（`L2208`）

1. `self.enable_p5_source_observer / enable_p5_b1_recall_gate / enable_p5_b1_bridge_gate`（按 config 读）
2. `self.p5_attestation_registry = P5AttestationRegistry()`
3. `self._bot_personal_outboxes = {}`、`self._bot_personal_outbox = None`
4. 若存在 `_memory_companion_outbox()`：`_bot_personal_outbox = outbox_getter()`
5. `self.unified_person_registry = UnifiedPersonRegistry(self.data)`
6. `self.req041_migration_coordinator = MigrationCoordinator(self.data_dir)`
7. `self.req041_migration_outbox = MigrationOutbox(Path(self.data_dir) / "req041_migration_outbox.db")`
8. `self.req041_migration_status = {"required": False, "state": "uninitialized", "code": "migration_not_started"}`
9. `self.req041_migration_backfill / relationship_store / dual_write_producer / scoped_projection_sync = None`
10. `self.req041_scoped_projection_status = {"ok": False, "code": "scoped_projection_not_initialized", "scopes": []}`
11. `self._req041_scoped_sync_task = None`、`self._req041_scoped_sync_requested = False`

---

## 5. 配置系统

### 5.1 配置 Schema（`_conf_schema.json`）

AstroBot 原生 schema（约 1000+ 字段）。每个字段都有 `description` + `type` + `default` + `hint`（中文 UX 文案），部分有 `condition`（依赖其他字段）、`_special`（如 `select_persona` / `select_provider`）、`obvious_hint: true`（强调显示）、`password: true`（密码遮罩）。

### 5.2 30+ 配置章节一览

| 章节 | 所在 `_initialize_*_config` | 核心字段（节选） |
|---|---|---|
| `basic_config`（数据/插件开关/存储/WebUI/启停） | core_and_relationship | data_dir/data_file、storage_backend/sqlite、standalone_webui_*、store_control_tag_sanitization、outbound_secret_redaction、enabled（旧版迁移）、reply_interception_forward_*、proactive_only_mode/intensity_preset/preempt_queue/.../unanswered_*/friend_unanswered_*/timer_pre_silence/max_daily_messages |
| `persona_routing`（多人格/主人格） | core_and_relationship | enable_multi_persona_mode、plugin_specific_persona_id、_multi_persona_primary_*、multi_persona_ids、_persona_profiles_dir |
| `relationship_owner`（关系阶段+专属+阶段策略） | core_and_relationship | default_interaction_band、enable_custom_relationship_stage_policy、relationship_stage_policy、relationship_stage_provider_routes、relationship_positive_stage_cap_key、normal_interaction_band_cap、owner_group_*_projection、content_tiers/flirt、owner_exclusive_*、relationship_event_window_*、event_cap_*/cap_key/owner_proactive_limit |
| `group_relationship_affinity` | core_and_relationship | enable、allowlist、daily_net_cap、window_minutes/absolute_cap/person/scope_daily |
| `world_knowledge`（环境/模型/Token/人化/休息/创作） | world_and_model | relationship_decay_*、environment_perception 开关与时区、holiday/platform/model/worldview/lunar/solar_term/almanac、provider_config_mode、model_*_overrides、deepseek_peak、page_font/theme、各 *PROVIDER_ID、daily_token_limit/soft_limit、humanized_states/advanced_cycle/rest_reply/busy_reply_gate/enhanced_dreams/passive_states/proactive_share_probability/daily_greetings/creative_writing |
| `proactive`（主动行为） | proactive_and_reaction | history 限制、proactive_chat_integration/body_monitor_integration、bridge_review_mode/collision_window、persona_judge、reaction_expression 全部配置、maslow_motivation、personality_iteration、proactive_decorating_hooks、precise_platform_send、quote_*、segmented_proactive_* 全套、proactive_prompt_template、max_proactive_plan_lag_minutes、proactive_dedup_* |
| `photo`（图像生成/参考/后端） | proactive_and_reaction + photo_and_expression | comfyui/sdgen/external/tool_call/nai、custom_photo_tool_*、local_photo_load_guard、external_image_api_*、backup_external_image_api_*、photo_reference_catalog、daily_outfit/creative_cover、natlang_photo_generation、weather、photo_generation_prompt_format/style/negative_prompt/fixed/scene_presets、bot_relationship_network/cards、group_nsfw_review_*、qq_presence_sync |
| `expression`（学习/应用） | photo_and_expression + review_and_group 头部 | expression_*_learning (mode/source_mode/ids)、expression_*_application (mode/ids)、expression_manual_review/style_review、intent_emotion_analysis |
| `review`（被动/主动审查） | review_and_group | enable_passive_response_review（兼容旧）、enable_framework_error_leak_guard、enable_proactive_message_review、smart_silence、passive_review_mode/strength、proactive_review_mode/strength + 阈值、passive_topic_suppression |
| `relationship_analysis`（关系分析/边界） | review_and_group | enable_relationship_*、boundary_*_penalty、stage_points、cold_minutes、apology_*、recover_ratio_*、vent/owner_report_targets/scene_template/baseline/tone_*、violation_recovery_minutes_per_point |
| `emotion_gate` | review_and_group | llm_emotion_judgement、emotion_judgement_mode、emotional_gate_hurt/refuse/recovery_per_hour/max_hurt_minutes（带旧值迁移） |
| `memory_learning`（对话/情节/循环/技能/目标） | review_and_group | dialogue_episode_memory、open_loop_tracking、user_habit_learning、food_menu_recommendation、meal_care_proactive、user_habit_*、skill_growth_*、personal_goals、memory_refresh/max_*、多个 *_PROVIDER_ID、passive_topic_memory_hours、episode_*、max_dialogue_episodes |
| `daily_diary_review` | photo_and_expression | enable_daily_diary（time/form/length/creativity/custom_direction/share_seed/max_entries）、enable_daily_review（time/retention/auto_apply）、daily_case_review_experiment、important_date_lookahead_days |
| `proactive_actions`（动作开关与限制） | photo_and_expression | legacy `enabled_proactive_actions` → 4 个开关；photo_generation_allowed_scopes (limit_keys)；screen_glance/poke/voice 行为 + cooldown；photo_text_action；goodnight_screen_check；unanswered_screen_peek_followup；mai_style_integration |
| `group_companion` | review_and_group | enable、access_mode、target_group_ids/whitelist/blacklist、require_target_group、slang_learning、member_profiles 兼容、member_safety 全部 |
| `group_observation` | review_and_group | context_injection、history_injection、intercept_astrbot_group_context、injection_guard、persona_denoise、forward_message_adaptation（inject/transcribe）、forward_message_*、scene_awareness/reality_promise_guard/wakeup_enhancement |
| `group_wakeup` | group_and_provider | context_words 校正、interest_*、question_threshold、cold_group、cooldown、generated_keyword_limit、topic_interest_boost、debounce_pending_penalty、fatigue_limit/decay_minutes、log_limit、short_text_wait_seconds |
| `group_high_intensity` | group_and_provider | enable、window/threshold/cooldown/merge_seconds/max_merge_messages、merge_scope=group\|same_user |
| `group_interjection_repeat` | group_and_provider | group_interjection、group_repeat_follow/follow_probability/interrupt_*/trigger_threshold/distinct_users_only、interrupt_text/image_path、min_interval/max_daily |
| `group_topics_episodes` | group_and_provider | max_group_recent_messages/slang_terms、topic_threads/episode_memory/interjection_feedback/slang_meanings/slang_web_search + terms/results |
| `group_graph_privacy_worldbook_atrelay` | group_and_provider | relationship_graph、privacy_guard、third_party_portrait_guard、worldbook_member_recognition、atrelay_tools、cross_user_memory_bridge、worldbook_first、member_cache_minutes、sensitive_confirm、llm_rewrite、relay_style、multi_target_limit；worldbook_auto_import/match_aliases/self_registration/block_words/block_reply（值迁移）、auto_pending_observations/member_inject_limit/config_paths |
| `group_livingmemory_memory_companion` | group_and_provider | livingmemory_integration、tool_name、context_timeout_seconds、emotional_drift/cross_window_emotion/dream_fragment/open_loop_search/feature_context/private_recall、top_k/max_chars |
| `external_event_bilibili_news_web` | group_and_provider | bilibili_*/news_*/web_exploration_*/external_event_self_link_q_*/ai_daily_watch + sources/uid/prefer_text、news_sources + 旧值迁移、news_hot_sources/max_items（兼容旧名）、web_exploration api |
| `qzone` | group_and_provider | qzone cookie、life_publish 全部参数（mode/window/probability/cool/insomnia/intra_day_gap/double_windows/similarity/style/generated_image/comment_inbox 轮询/emotional_vent 阈值） |
| `vision_provider` | group_and_provider | plugin_vision_provider_id；调用 `_apply_quick_provider_defaults()` |
| `group_refresh_limits_aliases` | group_and_provider | episode_refresh_minutes/slang_summary_minutes/max_topic_threads/eps/relationship_edges；向后兼容 `allow_photo_text_action/allow_screen_peek_action/allow_poke_action/allow_voice_action = enable_*` |

### 5.3 配置迁移

`config_migration.py` 中的关键函数：

- `LEGACY_KEY_ALIASES`：旧字段名 → 新字段名
- `migrate_flat_config_into_schema_groups(c)`：扁平配置 → schema 分组
- `_migrate_relationship_switch_semantics`：关系开关语义迁移
- `_migrate_command_photo_quota_semantics`：生图额度语义迁移
- `_migrate_photo_scope_quota_semantics`：生图范围额度语义迁移
- `_migrate_qweather_config`：和风天气配置迁移

迁移以**幂等**为前提，可在 `storage_backend` / `storage_sqlite_path` / `enable_standalone_webui` 等关键字段上设置 `_X_quota_semantics_version` 隐形标记，检测版本号确保一次性升级。

---

## 6. 存储层

### 6.1 物理分层

| 层 | 文件 | 行数 | 角色 |
|---|---|---|---|
| Mixin 门面 | `core_store.py` | 5798 | `CoreStoreMixin`（line 467）+ 全插件 `CompactionDataPersistence` 内部直接复用 |
| Backend 抽象 | `storage/backend_base.py` | 61 | `StoreBackendBase`（line 14）抽象 12 个方法 |
| Backend 工厂 | `storage/factory.py` | 22 | `build_store_backend(backend_name, data_file, sqlite_path, …)` |
| JSON 实现 | `storage/json_backend.py` | 139 | `JsonStoreBackend`，`os.replace` + ticket 防护 |
| SQLite 实现 | `storage/sqlite_backend.py` | 1153 | WAL + `PRAGMA busy_timeout=15000`，section 表 + SHA-256 |
| 运行时票据 | `storage/path_generation.py` | 154 | `sys.modules`-anchored `_runtime` + `owner/generation/sequence` |
| 编排 | `storage/store_manager.py` | 553 | `StoreManager`，`reconcile_bookshelf_payload` |
| 首次装载迁移 | `storage/migration.py` | 66 | `migrate_json_to_backend_if_needed` |

### 6.2 `core_store.py` 五个核心入口签名

```text
_load_data_sync(self, *, reload=False, include_migration=True) -> PersistedData   # L1999
_save_data_sync(self, *, force=False, reason="unspecified", coalesce=True)         # L2182
_save_data_now_sync(self, *, reason="unspecified") -> None                         # L2279
_atomic_write_data_file_sync(self, snapshot, owner_token=None) -> None             # L2402
_rebuild_store_manager(self, …)                                                    # L813
_new_store(self) -> PersistedData                                                  # L1096
_ensure_store_defaults(self, store) -> None                                        # L1259
_save_config_if_possible(self, …)                                                  # L998
```

### 6.3 JSON 与 SQLite 关键差异

| 维度 | JSON | SQLite |
|---|---|---|
| 并发 | `path_generation._RUNTIME_KEY` 单进程态 `shared_prepare_lock` + `replace_if_ticket_current` ticket 校验 | WAL + `BEGIN IMMEDIATE` 事务 + per-section `revision` 自增 + SHA-256 `checksum` |
| 写入原子 | `pid+thread+uuid` 临时文件名 + `os.replace` | `_replace_store()` 在 `BEGIN IMMEDIATE` 内清表→批量 INSERT |
| 查询能力 | 全文件 `json.loads` | `sections(section_name, payload_json, …)` 单段按需查询 |
| 迁移 | 见 §6.4 | `_SCHEMA_VERSION=2`，`schema_version` 字段随 section 记录 |
| 共享锁 | `_shared_schema_lock` 绑定到 `resolved_store_path` | 同上（在 sqlite_backend 内同一 dict） |
| 健康检查 | `health_check()` 返回 dict 包含 `lock_holder/owner_token/path_generation` | 返回 dict 包含 `wal_mode/sections_table_count/sqlite_version` |

### 6.4 迁移路径

1. **配置选择**：`config["storage_backend"]` ∈ {`json`, `sqlite`}（默认 `sqlite`）。
2. **首次启动**：`storage/migration.py:migrate_json_to_backend_if_needed(backend, json_backend, default_data)` 三分支：
   - 目标后端存在 → 不动
   - 目标不存在 + JSON 存在 → 复制
   - 都为空 → 落默认
3. **schema 升级**：`sqlite_backend.py` 在每次打开时执行 `PRAGMA user_version` 比对 `_SCHEMA_VERSION`，v1→v2 通过新增 `revision/is_deleted/checksum` 列完成。
4. **运行时路径生成**：`storage/path_generation.py` 用 `sys.modules.setdefault("astrbot_plugin_private_companion.storage.path_generation._runtime")` 把 `_runtime` 字典锚定在模块对象上；`capture_write_ticket()` 返回 `(owner, generation, sequence)` 三元组，被 `_atomic_write_data_file_sync` 写入前 + `replace_if_ticket_current` 比对通过后才允许 `os.replace`。

### 6.5 StoreManager 关键方法

```text
StoreManager.load_initial_store(...)            # 启动期
StoreManager.load_sections(names)               # 按需段读取
StoreManager.save_store(snapshot, reason)       # 全量
StoreManager.save_snapshot(snapshot)            # 协程
StoreManager.save_sections(payload_dict)        # 增量
StoreManager.next_revision(section_name)        # 计算下一 revision
StoreManager.deleted_section_revisions()        # 列出软删段
StoreManager.export_current_to_json()           # 5 分钟间隔导出备份
```

`reconcile_bookshelf_payload(primary_store, secondary_store)` 处理 `bookshelf_items` / `reading_archive_integration` / `bookshelf_secret` 三段在两个后端间的合并；`_apply_bookshelf_recovery` 双向恢复。

### 6.6 持久化票据（owner / generation / sequence）

每个插件实例启动时生成 `_persistence_owner_token = str(extension_api._story_migration_generation or f"instance-{id(self)}")`。

写文件时：

1. `capture_write_ticket()` 拿到当前 `(owner, generation, sequence)`
2. 临时文件名 = `data_file.tmp.{pid}.{thread_id}.{uuid}`
3. 把 ticket 元数据写入快照头部
4. `replace_if_ticket_current(expected_ticket)` 比对通过 → `os.replace`

启动时：

1. `_load_data_sync()` 读快照头部 ticket
2. 比对 `_persistence_owner_token`，**不一致则拒绝读旧文件**（防止旧实例脏数据）
3. 失败则 `_new_store()` 创建空存储

### 6.7 多 persona 隔离存储

- `_persona_profiles_dir`：每个人格的资料目录
- `_persona_data_profiles`：人格 → profile 映射
- `_persona_data_save_tasks`：每个人格独立的保存任务簿记
- 关闭插件时遍历非 primary 人格，对每个人格分别 `shield + to_thread` 落盘

---

## 7. 钩子全景

`PrivateCompanionPlugin` 在 AstrBot 上注册的钩子，按优先级从大到小排列（值越大越早执行）：

| 行号 | 装饰器 | 优先级 | 监听 | 方法 | 一句话职责 |
|---|---|---|---|---|---|
| 1375 | `@filter.on_plugin_loaded()` | — | 任意插件加载 | `_on_external_plugin_loaded` | 让跨插件桥接缓存失效 |
| 1382 | `@filter.on_plugin_unloaded()` | — | 任意插件卸载 | `_on_external_plugin_uninstalled` | 卸载后清空跨插件 extension_api 引用 |
| 1391 | `@_ON_WAITING_LLM_REQUEST(priority=110000)` | 110000 | LLM 等待请求 | `route_model_replacement_before_agent_hook` | agent 触发前重写模型路由 |
| 1406 | `@filter.on_llm_request(priority=110000)` | 110000 | LLM 请求 | `enforce_model_replacement_request_hook` | 强制把已选替换应用到 req |
| 1423 | `@filter.on_llm_response(priority=-100000)` | -100000 | LLM 响应 | `clear_model_replacement_context_hook` | 响应结束时清掉替换上下文 |
| 1440 | `@_ON_WAITING_LLM_REQUEST(priority=100000)` | 100000 | LLM 等待请求 | `guard_pending_message_debounce_hook` | 入站消息合并去抖守卫 |
| 1455 | `@filter.on_llm_response(priority=100000)` | 100000 | LLM 响应 | `settle_pending_message_debounce_hook` | LLM 收敛后提交去抖结果 |
| 8665 | `@filter.event_message_type(ALL, priority=11000)` | 11000 | ALL | `prepare_tts_streaming_boundary` | TTS 回合预判并强制完整回复 |
| 8685 | `@filter.event_message_type(ALL, priority=9500)` | 9500 | ALL | `handle_reactive_poke` | OneBot 戳一戳→文字/LLM 回复，可反戳 |
| 8818 | `@filter.event_message_type(ALL, priority=10000)` | 10000 | ALL | `observe_recall_enhancement_events` | 撤回增强：缓存 / 撤回 / 取消 |
| 19841 | `@filter.command("陪伴", alias={...})` | — | 命令"陪伴" | `companion_command` | 陪伴主面板入口 |
| 20609 | `@filter.command("陪伴群", alias={...})` | — | 命令"陪伴群" | `group_companion_command` | 群陪伴主面板入口 |
| 20619 | `@filter.event_message_type(PRIVATE_MESSAGE, priority=220000)` | 220000 | 私聊最早 | `guard_req036_private_capability_early` | REQ-036 私聊授权最早期门控 |
| 20658 | `@filter.event_message_type(PRIVATE_MESSAGE)` | 默认 | 私聊 | `on_private_message` | 私聊主入口与 preflight |
| 20850 | `@filter.event_message_type(GROUP_MESSAGE, priority=210000)` | 210000 | 群最早 | `guard_blocked_group_member_early` | 黑名单/被封成员早拒绝 |
| 20889 | `@filter.event_message_type(GROUP_MESSAGE, priority=200000)` | 200000 | 群早 | `capture_group_observation_early` | 群观察快照 |
| 20978 | `@filter.event_message_type(GROUP_MESSAGE, priority=190000)` | 190000 | 群早 | `review_group_member_safety_early` | 群成员安全审核 |
| 21022 | `@filter.event_message_type(GROUP_MESSAGE, priority=180000)` | 180000 | 群早 | `guard_req036_group_portrait_queries` | REQ-036 群画像查询守卫 |
| 21080 | `@filter.event_message_type(GROUP_MESSAGE)` | 默认 | 群 | `on_group_message` | 群聊主入口 |

### 7.1 优先级机制

- **正值**（220000 → 10000）= 在标准管线**前/外层**运行（`on_private_message`、`on_group_message`、`on_llm_request`、`on_agent_begin`）
- **零附近**（≥0）= `on_decorating_result` 修饰阶段
- **负值**（≤-1000）= `on_decorating_result` / `after_message_sent` 在 LLM 调用后/输出后清理

举例：

```
on_decorating_result 优先级: 10000, 20000, -10000, -18000, -20000,
                       -30000, -29999, -21000, 300, -1000, 100, -9000
after_message_sent   优先级: 8500, 9000, 9500, 8000, 7000, 6000,
                       -105000, -110000, -100000
on_agent_begin: 15408 (default), 9946 (priority 100000)
on_agent_done:  15452 (default), 9960 (-100000), 9974 (1000000)
```

---

## 8. 私聊消息链

### 8.1 入口

```
@filter.event_message_type(EventMessageType.PRIVATE_MESSAGE, priority=220000)   # main.py L20622
async def guard_req036_private_capability_early(self, event):
    ...

@filter.event_message_type(EventMessageType.PRIVATE_MESSAGE)                     # main.py L20661
async def on_private_message(self, event):
    await self.handle_private_message(event)
```

实际处理体在 [`message_pipeline.py:107`](./message_pipeline.py) 的 `MessagePipeline.handle_private_message`。

### 8.2 完整链路（按调用顺序）

```
on_private_message (priority 220000, main.py L20661)
   └─ handle_private_message(message_pipeline.py L107)
        ├─ _is_duplicate_inbound_message(...)              # event_dispatch.py L3150 去重
        ├─ _note_semantic_message_buffer(...)              # event_dispatch.py L3270 缓冲
        ├─ _smart_message_debounce_wait_seconds_for_event  # event_dispatch.py L4283
        ├─ on_llm_request: route_model_replacement_before_agent  # L3690
        │     └─ enforce_model_replacement_request         # L3829
        ├─ on_agent_begin (priority 100000 / default)       # main.py L15408 / L9946
        ├─ on_agent_done (清理阶段)                         # main.py L15452 / L9960 / L9974
        ├─ on_decorating_result hooks (10+ 条)
        ├─ guard_pending_message_debounce                   # event_dispatch.py L3896
        └─ settle_pending_message_debounce                  # event_dispatch.py L3916
              └─ after_message_sent (clean-up hooks, 7+ 条)
```

### 8.3 关键子函数

| 函数 | 文件:行 | 用途 |
|---|---|---|
| `_is_duplicate_inbound_message(event)` | event_dispatch.py L3150 | 入站消息去重（基于内容指纹 + 时间窗） |
| `_note_semantic_message_buffer(event)` | event_dispatch.py L3270 | 语义合并缓冲 |
| `_smart_message_debounce_wait_seconds_for_event(event)` | event_dispatch.py L4283 | 计算去抖等待时长（基于 provider 模型判定） |
| `route_model_replacement_before_agent(event)` | event_dispatch.py L3690 | agent 触发前应用模型替换 |
| `enforce_model_replacement_request(event, req)` | event_dispatch.py L3829 | 强制把已选替换写入 req |
| `clear_model_replacement_context(event, resp)` | event_dispatch.py L3754 | 响应结束时清理上下文 |
| `guard_pending_message_debounce(...)` | event_dispatch.py L3896 | 入站消息合并守卫 |
| `settle_pending_message_debounce(...)` | event_dispatch.py L3916 | 收敛后提交合并结果 |

---

## 9. 群聊消息链

### 9.1 入口

```
@filter.event_message_type(GROUP_MESSAGE, priority=210000)   # main.py L20850
async def guard_blocked_group_member_early(self, event):      # 黑名单/被封成员早拒绝

@filter.event_message_type(GROUP_MESSAGE, priority=200000)   # main.py L20889
async def capture_group_observation_early(self, event):       # 群观察快照

@filter.event_message_type(GROUP_MESSAGE, priority=190000)   # main.py L20978
async def review_group_member_safety_early(self, event):      # 群成员安全审核

@filter.event_message_type(GROUP_MESSAGE, priority=180000)   # main.py L21022
async def guard_req036_group_portrait_queries(self, event):   # REQ-036 群画像查询守卫

@filter.event_message_type(GROUP_MESSAGE)                     # main.py L21083
async def on_group_message(self, event):
    await self.handle_group_message(event)
```

实际处理体在 [`message_pipeline.py:1248`](./message_pipeline.py) 的 `MessagePipeline.handle_group_message`。

### 9.2 完整链路

```
on_group_message × 4 优先级 (210000/200000/190000/180000)
        ↓
handle_group_message (message_pipeline.py L1248)
   ├─ _group_active_conversation(group)            # event_dispatch.py L4623
   ├─ _group_followup_llm_judge(...)               # event_dispatch.py L4740
   ├─ _group_message_is_bot_continuation(...)      # event_dispatch.py L4796
   ├─ _resolve_quote_message_id(...)               # event_dispatch.py L2958
   ├─ _group_current_reply_quote_message_id(...)   # event_dispatch.py L3038
   ├─ _group_resting_mention_notice(...)           # event_dispatch.py L5225
   ├─ _format_recalled_messages_for_event(...)     # event_dispatch.py L2532
   ├─ _recent_recalled_messages_for_scope(...)     # event_dispatch.py L2503
   ├─ _event_raw_payload(event)                    # event_dispatch.py L482
   ├─ _event_is_inbound_chat_message(event)        # event_dispatch.py L867
   └─ _event_priority(event)                       # event_dispatch.py L5284 → 排序后处理
```

### 9.3 文本分段（send-time segmentation）

```
def _split_proactive_text(...) -> list[str]: ...                  # event_dispatch.py L5597
async def _calc_segmented_proactive_interval(...) -> float: ...   # event_dispatch.py L6223
```

---

## 10. 主动消息系统

### 10.1 候选生命周期（9 阶段）

| # | 阶段 | 关键函数 | 文件:行 | 输入 → 输出 |
|---|------|----------|---------|--------------|
| 1 | **候选派生** | 各 `_pick_*_event` | `proactive_engine.py` L6641–L8071 | 业务事件 → 候选字典 `{key, score, reason, source, payload, ...}` |
| 2 | **入池** | `_queue_proactive_impulse(...)` | `proactive_engine.py` L1295 | 候选 → `self._proactive_candidate_pool[user_id]` |
| 3 | **池维护** | `_cleanup_proactive_candidate_pool(...)` / `_apply_per_user_pending_candidate_cap(...)` | `proactive_engine.py` L508, L420 | 池条目 → 清理/截断后的池 |
| 4 | **时间窗判定** | `_schedule_next_proactive(...)` / `_next_proactive_window_open_seconds(...)` | `proactive.py` L3570 | 池 + `now` → `next_proactive_at`、`planned_proactive_window_start_at`、`planned_proactive_best_until_at` |
| 5 | **边界检查** | `_should_send(self, user) -> tuple[bool, str]` | `proactive_engine.py` L4302 | `user → `(pass, reason)` |
| 6 | **人格判断打分** | `_score_proactive_impulse(...)` / `_proactive_persona_alignment(...)` / `_proactive_candidate_semantics(...)` | `proactive_engine.py` L1552, L1683, L1814 | 候选 → `score`、`alignment`、`semantics` |
| 7 | **生成**（plan / diary / detail / render） | `_ensure_daily_plan(...)` / `_ensure_daily_diary(...)` / `_ensure_detail_enhancement(...)` / `_render_message(...)` | `daily_state.py` L532, L666, L685, L883；`proactive_engine.py` L10448 | LLM 调用 → 文本/分段 |
| 8 | **预发送复核**（人设裁决） | `_review_planned_proactive_with_model(...)` / `_local_proactive_persona_judgement(...)` / `_apply_proactive_model_rewrite(...)` | `proactive_engine.py` L2639, L2296, L2773 | `(user, candidate)` → 决策 `{"send", "rewrite", "defer", "drop"}` + 可选改写文本 |
| 9 | **发送 + 审计** | `_send_proactive_message(...)` / `_append_proactive_audit(...)` / `_proactive_audit_log(...)` | `proactive_message.py`；`proactive_engine.py` L5740, L5546 | 消息 → 渠道 + 审计日志 |

### 10.2 调度器入口（`proactive.py`）

```python
async def _scheduler_loop(self) -> None: ...                                  # L4097
async def _run_scheduler_cycle(self, *, immediate: bool) -> None: ...        # L4060
async def _kick_proactive_loop_once(self) -> None: ...                        # L4416
def _next_scheduler_timeout(self) -> float: ...                               # L4422（默认 60s）
def _next_scheduler_timeout_for_active_persona(self) -> float: ...           # L4449
```

启动位置：`main.py:8094`：`self._task = asyncio.create_task(self._scheduler_loop())`

**循环结构（伪代码）**：

1. 取下一个到期 persona / 候选（`_next_scheduler_timeout_for_active_persona`）
2. 遍历 `self._scheduler_persona_ids()`（多 persona 模式）
3. 调 `_run_scheduler_cycle(immediate=False)`，其中进入 `_tick_user`（`daily_state_tick.py:132`）

### 10.3 候选源（candidate sources）

候选来自两类入口 + 多个 `_pick_*_event` 业务钩子：

**A. 事件驱动（queue_event_driven）**：`proactive.py:3177`

```python
def _queue_event_driven_proactive_impulses(self, *, event, user_id, ...):
    # 触发：inbound message、mention、game_invite、photo_recall、absence_miss 等
```

**B. 随机触发（queue_random）**：`proactive.py:3310`

```python
def _queue_random_proactive_impulse(self, *, user_id, ...):
    # 用于"无外部事件"时的随机主动
```

**C. `_pick_*_event`（route 内调用的显式业务候选派生）**

| 函数 | 文件:行 | 含义 |
|------|---------|------|
| `_pick_best_planned_event` | proactive_engine.py L6641 | 当日最佳计划事件 |
| `_pick_mobile_location_arrival_event` | proactive_engine.py L6707 | 抵达常去地点 |
| `_pick_mood_checkin_event` | proactive_engine.py L6794 | 心情 check-in |
| `_pick_corrected_memory_echo_event` | proactive_engine.py L6873 | 校正后的记忆回响 |
| `_pick_memory_echo_event` | proactive_engine.py L6936 | 记忆回响 |
| `_pick_absence_miss_event` | proactive_engine.py L7034 | 缺席 miss |
| `_pick_game_invite_event` | proactive_engine.py L7097 | 游戏邀请 |
| `_pick_state_need_event` | proactive_engine.py L7182 | 状态需求 |
| `_pick_meal_care_event` | proactive_engine.py L7355 | 用餐关心 |
| `_pick_open_loop_followup_event` | proactive_engine.py L7429 | 未关闭话题跟进 |
| `_pick_pending_followup_event` | proactive_engine.py L7506 | 待处理跟进 |
| `_pick_daily_greeting_event` | proactive_engine.py L7804 | 日问候 |
| `_pick_birthday_celebration_event` | proactive_engine.py L7917 | 生日 |
| `_pick_insomnia_night_event` | proactive_engine.py L8071 | 失眠夜 |

**D. 路由注册表（`proactive_routes.py`）**——7 个 `ProactiveRoute` 子类把 `_pick_*` 整理成可订阅的来源：

```
class ProactiveRoute                  # 基类
class TransactionalRoute              # 事务性问候/祝福
class SafetyEventRoute                # 安全事件 (位置/失眠)
class ContinuationRoute               # 延续 (话题/记忆回响)
class RitualRoute                     # 仪式 (生日/每日问候)
class ContentShareRoute               # 内容分享 (心情/记忆)
class SelfLifeRoute                   # 自生活
class RelationalRoute                 # 关系性 (缺席/跟进)
class ProactiveRouteRegistry          # 单例 PROACTIVE_ROUTE_REGISTRY
```

每条路由提供：

```
key / label / source_names / reason_names / active_window_seconds /
grace_window_seconds / review_profile / disable_segmenting /
cancel_if_new_inbound / recent_chat_policy / duplicate_policy
```

**E. 候选状态字段**（写入 `user` 字典）：

```
planned_proactive_source            # 来源 key
planned_proactive_reason            # 原因 key
planned_proactive_kind              # 业务类型
next_proactive_at                   # 下一次窗口起点
planned_proactive_window_start_at   # 窗口起始
planned_proactive_best_until_at     # 最佳发送截止
planned_proactive_expire_at         # 过期时间
planned_proactive_origin_at         # 起源时间戳
planned_proactive_origin_key        # 起源事件 key
planned_proactive_freshness         # 新鲜度
planned_proactive_delivery_state    # pending/sent/deferred/dropped
```

### 10.4 边界检查器

边界由 `_should_send(user) -> tuple[bool, str]`（`proactive_engine.py:4302`）按顺序求值：

| # | 检查函数 | 文件:行 | 用途 |
|---|----------|---------|------|
| 1 | `_effective_user_daily_limit(user)` | proactive.py L1555 | 单用户当天主动上限 |
| 2 | `_quiet_hours_end_timestamp(self, at_ts)` | proactive.py L2181 | 安静时段结束时间 |
| 3 | `_is_quiet_time(self)` | proactive.py L2207 | 当前是否安静时段（DND） |
| 4 | `_effective_proactive_persona_judge_send_threshold(self)` | proactive.py L937 | 人格判断阈值 |
| 5 | `UserRestGateMixin._user_in_rest_window(...)` | user_rest_gate.py | 用户休息信号 |
| 6 | 关系阶段（warmup/steady/intimate）限制 | engine 内部 | 阶段未到则降频 |
| 7 | Token budget / segment | engine 内部 | 防止超长 |
| 8 | `recent_chat_policy` | proactive_routes.py | 刚聊过则推迟 |

最终 `_should_send` 返回 `(bool, reason)`，`reason` 进入 `planned_proactive_defer_reason` 或 `drop_reason`。

### 10.5 发送前复核

`_review_planned_proactive_with_model(...)`（`proactive_engine.py:2639`）：

- **输入**：`user`、`candidate`、`rendered_text`
- **缓存层**：`_cache_proactive_model_judgement(...)`（L2736）按 `(user_id, candidate_key, plan_version)` 索引
- **本地兜底**：`_local_proactive_persona_judgement(...)`（L2296）正则在模型不可用时直接给决策
- **改写**：`_apply_proactive_model_rewrite(...)`（L2773）使用模型提供的新文本
- **输出**：`{"decision": "send"|"rewrite"|"defer"|"drop", "text": ..., "reason": ...}`

判定在 `daily_state_tick.py:_tick_user`（L132，约 L234 附近）被处理：

- `decision == "rewrite"` → 用 `text` 替换本次内容再走发送
- `decision == "defer"` → 推迟 `next_proactive_at`
- `decision == "drop"` → 写 `drop_reason`，不发送
- `decision == "send"` → 继续

### 10.6 `_tick_user` 状态机

`async def _tick_user(self, user_id, user)`（`daily_state_tick.py:132`）伪流程：

1. `now = time.time()`
2. `guard_reason = _route_recent_chat_guard_reason(user, ...)` （L69）
3. 若 guard：`_defer_route_for_recent_chat(...)` （L94）→ return
4. `_should_send(user)` → 不通过则 return
5. `_ensure_daily_plan / _ensure_daily_diary / _ensure_detail_enhancement`（按需）
6. `_review_planned_proactive_with_model(...)` → 取判定
7. 应用判定（rewrite/defer/drop/send）
8. 若发送：`_settle_proactive_route_state(user, route_key=..., settlement="sent", ...)`（L108）
9. 审计日志：`_append_proactive_audit(...)`

### 10.7 主动消息发送链 `proactive_message.py`

`ProactiveMessageMixin`（18 038 行）负责"已经通过复核"的候选如何"形成消息链并送达"，关键流程：

1. 文本渲染（`render_message`）→ 多段（regex/words/scope）
2. 图片决策（`photo_wardrobe_decision`）→ 是否附带今日穿搭 / 自拍 / 主动生图
3. TTS 决策（`tts_enhancement.apply_tts_enhancement_request`）→ 文本转语音（单独发送或并入）
4. 表情包预判（reaction expression experiment）
5. 发送编排（`send_proactive_message` / `split_chain_for_ordered_send`）
6. 发送前脱敏（API Key、Token、Bearer、密码）
7. 出站日志（`append_proactive_audit`）

### 10.8 主动消息"门控"叠加层（按生效顺序）

1. 模式开关：`enable_proactive_only_mode` / `proactive_intensity_preset` / `enable_llm_streaming`
2. 时段：`quiet_hours` / `default_interaction_band` / `busy_reply_gate_*` / `rest_reply_simulation`
3. 强度：`max_daily_messages` / `proactive_hour_activity_curve` / `preempt_queue_*` / `burst_*`
4. 未回应：`unanswered_slowdown_*` / `friend_unanswered_max_cooldown_hours` / `timer_pre_silence_minutes`
5. 关系：`relationship_stage` / `relationship_positive_stage_cap_key` / `owner_proactive_limit` / `group_relationship_affinity_*`
6. Token：`daily_token_limit` / `daily_token_soft_limit` / `token_budget`
7. 路由层：`active_window_seconds` / `grace_window_seconds` / `recent_chat_policy` / `duplicate_policy`
8. 复核层：`smart_silence_*` / `proactive_review_*_threshold` / `bridge_review_mode` / `local_proactive_persona_judgement`
9. 余额感知：`balance_low_threshold` / `balance_critical_threshold` / `message_cooldown_hours`
10. 设备：`screen_glance_*` / `poke_*` / `voice_*` / `photo_max_daily`

> **每层都 fail-closed**：任意一层拒绝，候选从 `pending` → `deferred` 或 `dropped`，记录 `defer_reason` / `drop_reason`。

---

## 11. 状态 / 日程 / 细化引擎

### 11.1 类继承

```
DailyStateTickMixin                       # daily_state_tick.py L27
        ↑
DailyStateMixin(DailyStateTickMixin)      # daily_state.py L407
```

### 11.2 关键数据模型（`self.data` 内的段）

```
self.data["daily_plan"]                       # 当日日程 dict
self.data["daily_state"]                      # 日内标记位 (fired, last_tick_at, ...)
self.data["detail_enhanced_segments"]         # set[str] 已细化 segment 列表
self.data["detail_enhanced_day"]              # str YYYY-MM-DD
self.data["daily_story_plan"]                 # 配套故事计划
self.data["can_do"]                           # 可做事项（"能做什么"展示）
self.data["important_dates"]                  # 重要日期列表
self.data["diaries"]                          # 日记合集
self.data["bookshelf_secret"]                 # 书架
self.data["agenda"]                           # 新日程段（runtime）
self.data["unified_agenda"]                   # 统一日程（schema 升级后）
self.data["schedule_history"]                 # 日程历史
```

### 11.3 生成入口

| 函数 | 行 | 行为 |
|---|---|---|
| `async def _ensure_daily_plan(self, force)` | daily_state.py L532 | LLM 生成 `daily_plan`；按 `_daily_generation_lock` 串行 |
| `async def _ensure_daily_diary(self, force)` | daily_state.py L666 | 日记文本 |
| `async def _ensure_daily_diary_once(self, force)` | daily_state.py L685 | 同上的单次保护版 |
| `async def _ensure_detail_enhancement(self, force)` | daily_state.py L883 | 段落级细化，按 `detail_enhanced_day` 切换 |

**锁 / 作用域**：

```
def _daily_generation_lock(self, attribute: str) -> asyncio.Lock: ...   # daily_state.py L421
def _daily_generation_scope(self) -> str: ...                           # daily_state.py L440
def _daily_force_result_cache(self, attribute: str) -> dict: ...        # daily_state.py L444
```

**下次到期**：

```
def _next_detail_due_in_seconds(self, now) -> int: ...                  # daily_state.py L503
```

### 11.4 每小时 Tick 状态事件链

主调度 `_scheduler_loop`（proactive.py L4097）每 60s 轮询 → `_run_scheduler_cycle` → 对每个 persona 调用 `_tick_user`（daily_state_tick.py L132）。`_tick_user` 中间会调用 `self._ensure_*` 三个懒生成钩子，确保当日 plan/diary/detail 都已生成后再走 §10.6 流程。

节流点：

- `daily_plan` 仅在 `daily_plan_day` ≠ 今天时重生成（强制标志除外）
- `detail_enhancement` 按 `detail_enhanced_day` + `detail_enhanced_segments` 控制频率
- diary 同理有 `_ensure_daily_diary_once`

### 11.5 天气、地点、节日

- `weather_*` 30+ 字段：来源（qweather/openweathermap/amap/openmeteo）、API Key、城市、经纬度、刷新间隔、预警阈值
- `place_cognitive_map.py`：地点认知地图（带置信度、依据），地点切换时写移动过程
- `environment_perception`：holiday_country/platform/model/worldview、lunar/solar_term/almanac 开关
- 节假日、农历节气计算：`chinese_calendar` / `lunarcalendar`（可选依赖，缺失则降级）
- 气象预警感知：`enable_weather_alert` + `alert_min_severity` + `alert_refresh_minutes`，复用和风配置
- 地点事件天气联动：`environment_change_proactive`（敏感度 安静/平衡/敏感）

### 11.6 日记

- `enable_daily_diary` 开关：`time` / `form` / `length` / `creativity` / `custom_direction` / `share_seed` / `max_entries`
- `enable_daily_review` 开关：`time` / `retention_days` / `auto_apply_guidance`
- 入口：`dreaming.generate_daily_diary(user, ctx) → str`
- 来源：当日状态、日程、梦境、交互、见闻、创作进展
- 联动 Memory 时可承接共同经历、关系变化、情绪余波、未完成心事
- 体裁/长度/创作度由 LLM 按 `daily_diary_*` 决定

### 11.7 技能成长与个人目标

- `enable_skill_growth_simulation` + `skill_growth_rate` + `skill_growth_custom_skills` + `skill_growth_strength`
- 影响：日程安排（`schedule_influence`）+ 被动注入（`passive_inject`）
- `enable_personal_goals` + `auto_progress` + `cooldown` + `stall_days`
- 个人目标**只按真实完成的日程推进**，不凭空宣告完成

---

## 12. 关系系统

### 12.1 三层结构

```
┌──────────────────────────────────────────────────────┐
│ Layer 1: 长期好感度（relationship stage）             │
│   八个普通阶段 + 主要用户专属"专属联结"               │
│   自动增减 → 限长账本 + 阶段迟滞 + 单次/每日上限      │
│   只向 0 回落的自然降温（relationship_decay_*）       │
├──────────────────────────────────────────────────────┤
│ Layer 2: 当前互动档位（ExpressionBand, 7 档）         │
│   avoidant / relaxed / lively / warm / close /         │
│   affectionate / hurt                                 │
│   close/affectionate 只对主要用户开放                │
├──────────────────────────────────────────────────────┤
│ Layer 3: 关系网（relationship graph）                  │
│   稳定身份 + 别名 + 群资料 + 重要记忆 + 边界备注       │
│   + 待确认观察（不会自动写入长期记忆）                │
└──────────────────────────────────────────────────────┘
```

### 12.2 关键模块

| 文件 | 行数 | 核心职责 |
|---|---|---|
| `relationship_ledger.py` | 713 | `apply_relationship_event` / `apply_natural_relationship_decay` / `relationship_ledger_summary` / `record_manual_relationship_change` / `migrate_relationship_score_schema` / `migrate_relationship_positive_stage_cap` |
| `relationship_policy.py` | 340 | `default_relationship_stage_policy` / `normalize_relationship_stage_policy` / `relationship_stage_for_score` / `relationship_projection_for_bridge` |
| `relationship_affinity_runtime.py` | 147 | `normalize_group_allowlist` / `prepare_group_affinity_candidate` / `admit_confirmed_group_affinity` |
| `relationship_event_policy.py` | 中 | 事件策略（事件类型 → 加减分） |
| `companion_interaction_expression.py` | 867 | `ExpressionBand`（7 档）/ `ExpressionDecision` / `ExpressionInput` / `build_expression_decision(L394)` / `current_interaction_projection(L671)` / `allowed_expression_bands(relationship_role, relationship_mode)` |

### 12.3 表达决策（`build_expression_decision`）

输入：`ExpressionInput(user, message, candidate, recent_chat, boundary_signal, relationship_role, relationship_mode, ...)`
输出：`ExpressionDecision(band, allowed_styles, content_tier, safety_block, contact_boundary)`

关键路径：

1. 计算 7 档互动档位（基于最近互动、关系阶段、当前事件）
2. 应用 `content_tiers` / `flirt_content_tier`（按关系阶段限制内容尺度）
3. 应用 safety / p4-block / contact-boundary 三类硬阻断
4. 应用 `allowed_expression_bands`（关系档位 → 可用 band 过滤）

### 12.4 边界反馈

- `enable_boundary_*` 开关 → `_penalty_*` / `_stage_*_points` / `_cold_minutes` / `_apology_*` / `_recover_ratio_*`
- 明确越界按档位差分为轻度、中度、严重
- 恶意贬低角色、角色珍视之物或在意的人视为踩底线
- 结果统一写入：关系账本 + 情绪余波 + 互动状态
- 自然恢复只返还配置允许恢复的部分；真诚道歉可有限加速修复
- 同类再犯追回上一次道歉恢复的关系分
- 连续踩底线逐级进入：明确拒绝 → 冷静反思 → 关系降档
- 可选把严重事件低频写入当天生活叙事（向设定中的亲近对象倾诉）
- 内置"边界转达"主动能力（自然告诉主要用户）

### 12.5 专属联结（主要用户限定）

- 主要用户处于"专属联结"时，可在"用户 → 关系"中为当前人格单独填写自由文本关系背景
- 文本同时绑定稳定用户 ID + 人格 ID
- 只进入该组合的被动私聊和主动消息
- 切换人格后可维护另一份关系，群聊与其他用户不会继承
- **关系背景不开放工具、隐私、设备、平台管理、现实操作或内容权限**，也不能覆盖当轮明确边界和更高优先级规则

---

## 13. 情感系统：`domains/affect`

### 13.1 文件清单

| 文件 | 行数 | 内容 |
|---|---|---|
| `affect_modulation.py` | 47 | `compose_affect_modulation(conditions, now)` — 综合调制 |
| `emotion_event_contract.py` | 162 | `EMOTION_EVENT_SCHEMA_VERSION="companion_emotion_event.v1"`，**15 event_types**，**3 origins**，**5 statuses** |
| `emotion_event_ledger.py` | 62 | `record_recent_emotion_event` 用 **64-256 环形缓冲**（per-user） |
| `emotion_targeting.py` | 46 | `classify_emotion_target` 正则归属 actor/target |
| `interaction_dynamics.py` | 125 | `project_interaction_dynamics` / `settle_interaction_dynamics`，7 band 半衰期衰减 |
| `reply_temperature.py` | 179 | `compose_reply_temperature`，4 tier（guarded/neutral/warm/close） |

### 13.2 情绪事件 schema

```
EMOTION_EVENT_SCHEMA_VERSION = "companion_emotion_event.v1"
# 15 event_types: hurt, comfort, praise, neglect, misunderstanding, apology,
#                 boundary, intimacy, longing, play, neutral, joy, sadness,
#                 anger, anticipation
# 3 origins: user_message, bot_action, external_event
# 5 statuses: open, acknowledged, settled, escalated, expired
```

### 13.3 互动档位半衰期

每个 band（relaxed/lively/warm/close/affectionate/avoidant/hurt）有独立的半衰期。在 `project_interaction_dynamics(records, now)` 中：

- 时间衰减（half-life × bands）
- 互相转化（warm → relaxed → neutral → hurt）
- 上下文事件注入

### 13.4 回复温度（`reply_temperature.py`）

4 tier 决定语气：

| Tier | 触发 | 文案特征 |
|---|---|---|
| `guarded` | hurt/avoidant 高分 | 简短、克制、不主动 |
| `neutral` | 默认/不熟 | 普通对话 |
| `warm` | warm/lively 中等 | 关心+表情 |
| `close` | close/affectionate | 主动+亲密 |

---

## 14. 社交领域：`domains/social`

### 14.1 文件清单

| 文件 | 行数 | 内容 |
|---|---|---|
| `group_moments.py` | 287 | 确定性**名场面**抽取（hash → snapshot id） |
| `group_mood.py` | 262 | 6 label（tease/banter/serious/tension/confession/dead_silence）+ 各自 half-life |
| `joke_boundary.py` | 269 | `_MIN_SENSITIVITY=0`、`_MAX_SENSITIVITY=100`、`_BLOCK_THRESHOLD=60.0`，per-member 敏感度 |
| `roleplay_strength.py` | 128 | `project_roleplay_strength(mood, expression_band, now)` |

### 14.2 群聊名场面（`group_moments`）

- 输入：群聊摘要 + 触发条件（情绪烈度、互动密度、关键短语）
- 输出：snapshot id（确定性 hash）
- 持久化到 `group_moments_snapshot`
- 用于：群聊叙事回顾、关系网引用、表情包场景候选

### 14.3 接梗边界（`joke_boundary`）

- 每个群成员有 0-100 的敏感度评分
- `_BLOCK_THRESHOLD=60.0`：超过则禁用接梗
- 由群成员安全审核（`group_member_safety`）累加计算
- 例外豁免：`group_member_safety_exempt_managers`

### 14.4 扮演强度（`roleplay_strength`）

- 综合 mood（6 label）+ expression_band（7 band）→ 0.0-1.0 强度值
- 用于：决定是否进入沉浸扮演、何时回退到真实

---

## 15. 群聊能力

群聊由"观察 / 唤醒 / 续接 / 插话"四层组成。

### 15.1 观察（`group_observation.py`，4926 行）

- `GroupObservationMixin`（L447）
- 记录允许群内的：文本 + 图片 + 语音 + 视频 + 分享卡片 + 文件的可读占位
- 可选开启"群聊图片理解"，在后台生成带内容缓存的视觉摘要
- 维护：群气氛、成员观察、黑话、话题线、群聊片段、互动关系
- 群气氛：tease/banter/serious/tension/confession/dead_silence 6 label

### 15.2 唤醒（`group_wakeup.py`，1661 行）

`GroupWakeupMixin`（L257），idle wake、poke 反应

**唤醒信号分类**：

| 类型 | 触发词 / 模式 | 备注 |
|---|---|---|
| 直接 @ | `@bot` | 仅在 bot 是被 @ 方时 |
| Bot 名称 | `group_wakeup_direct_words` | 默认 `["机器人","bot"]` |
| 强唤醒词 | `group_wakeup_owner_direct_words` | 主要用户专属 |
| 问题信号 | `?` / `？` / 关键词 | 群 W 形 |
| 兴趣词 | `group_wakeup_interest_keywords` | 提前定义 |
| 冷群信号 | 群长时间沉默 | 主动破冰 |

**图片唤醒**（`enable_group_image_wakeup`）：

- 只复用当前图片已经完成或正在生成的视觉摘要，**不额外发起第二次识图**
- 摘要命中 Bot 名称 / 强唤醒词 / 主要用户专属强唤醒词 → 接入现有群聊唤醒链
- 弱相关词不会仅凭图片直接触发
- 主要用户专属词仍会校验真实发送者

### 15.3 续接

- 用户叫过 Bot 后，判断后续未继续 @ 的消息是否仍在对 Bot 说话
- 限制连续轮数（`group_conversation_followup_seconds` / `max_turns`）

### 15.4 插话（`group_interjection`）

- 按概率（`group_interjection_min_interval` / `group_interjection_max_daily`）、冷却、每日上限、场景、关系边界
- 高强度模式：`enable_group_high_intensity_mode` + `merge_seconds` + `max_merge_messages` + `merge_scope=group|same_user`
- 重复续接：`enable_group_repeat_follow` + `group_repeat_follow_*`

### 15.5 群成员安全（`group_member_safety.py`）

- `enable_group_member_safety` + `review_mode=directed|suspicious|all`
- `hidden_marker_mode=supplement|reply_only|disabled`
- `strike_threshold` + `window_days` + `block_hours` + `min_confidence`
- `exempt_managers` + `audit_limit`
- 触发：`review_group_member_safety_early`（priority 190000）提前审核

### 15.6 群聊上下文注入

- `enable_group_context_injection` / `enable_group_history_injection`
- `intercept_astrbot_group_context`：拦截 AstrBot 默认群上下文
- `enable_injection_guard`：注入守卫（防止 prompt 过长）
- `enable_persona_denoise`：人格去噪（去掉模型误生成的控制标签）
- `forward_message_adaptation_mode=inject|transcribe`：合并转发处理

### 15.7 群聊世界书 / 关系网

- `enable_worldbook_member_recognition`：成员识别
- `enable_worldbook_auto_import`：自动导入
- `enable_worldbook_match_aliases`：别名匹配
- `enable_worldbook_self_registration`：自注册
- `worldbook_block_words` / `worldbook_block_reply`（默认 "这个称呼我不记。"）
- `enable_group_relationship_graph` / `enable_group_privacy_guard` / `enable_third_party_portrait_guard`
- `worldbook_member_inject_limit` 注入数量上限
- `worldbook_config_paths` 多文件路径

### 15.8 群聊 LLM 开关（与观察分离）

- `陪伴群 关闭LLM` / `陪伴群 开启LLM` / `陪伴群 开启` / `陪伴群 关闭`
- **关闭 LLM 后允许范围内的观察数据仍可继续记录**

### 15.9 白名单 / 黑名单

- `group_access_mode = whitelist|blacklist`
- 白名单为空 = 观察范围为零
- 群 ID 支持：OneBot 数字群号、QQ 官方 `group_openid`、完整 `GroupMessage` UMO
- `require_target_group`：强制只在目标群工作

---

## 16. 命令系统

### 16.1 入口

```python
@filter.command("陪伴", alias={"私聊陪伴", "主动陪伴"})        # main.py L19841
async def companion_command(self, event): ...                  # main.py L19843

@filter.command("陪伴群", alias={"群陪伴", "群聊陪伴"})         # main.py L20609
async def group_companion_command(self, event): ...            # main.py L20611
    └─ self._group_companion_command_impl(event)               # command_handlers.py L5809
```

### 16.2 派发方式：`frozenset` 集合 + if/elif 链

**不是 dict map**。`action` 由 `_normalize_companion_command_action(action, value)`（`main.py:19917`）标准化（小写、去空格）后进入以下集合查表：

```
companion_manual_query_actions      = {"答疑", "排障", "诊断", "说明"}                              # main.py L19918
companion_manual_confirm_actions    = {"答疑确认", "排障确认", "诊断确认", "应用答疑建议", "应用建议"}   # L19919
companion_manual_cancel_actions     = {"答疑取消", "排障取消", "诊断取消", "取消答疑建议", "取消建议"}   # L19920
companion_manual_setting_actions    = {"答疑设置", "排障设置", "诊断设置", "答疑修改", "排障修改", "诊断修改"} # L19921
daily_outfit_view_actions                                                                # L19922
daily_outfit_generate_actions                                                             # L19923
photo_command_actions = {"生图","画图","绘图","生成图片","出图","自拍","拍照","拍一张","改图","修图","重绘","P图","p图"}  # L19929
daily_schedule_regenerate_actions = {"重置日程","生成日程","刷新日程","重新生成日程"}        # L19930
daily_schedule_cancel_actions    = {"删除日程","取消日程","移除日程"}                       # L19931
image_api_status_actions                                                               # L19932
image_api_swap_actions                                                                  # L19933
qweather_location_actions                                                                # L19943
wakeup_alarm_actions    = {"现实触及", "现实触及闹钟", ...}                                # L19948
private_delivery_actions                                                                # L19951
tts_language_actions   = {"TTS语种", ...}                                                # L19956
deferred_actions                                                                 # L20000
management_actions                                                            # L20050（受 _can_manage_private_companion 守护）
```

### 16.3 `companion_command` 内 action 分支

```
"状态","status"                             -> L20130
"生成状态","刷新状态","重生状态"            -> L20228
"增添状态","添加状态"                       -> L20211
"梦境","做了什么梦","今日梦境"              -> L20230
"梦境碎片","梦碎片","碎片梦境"              -> L20233
"画像","关系","回复率"                      -> L20235
"记忆","陪伴记忆"                            -> L20237
"表达学习","说话风格","口癖"               -> L20239
"气氛","意图","关系状态"                    -> L20241
"片段","对话片段","共同经历","未完成"      -> L20243
"话头删除","删除话头",...                   -> L20247
"长期记忆","livingmemory","lmem","向量记忆" -> L20269
"日记","bot日记","小记"                    -> L20271
"AI日报","ai日报","日报",...                -> L20307
"新闻","今日新闻","AI新闻","ai新闻"        -> L20309
"日期列表","重要日期","日期"                -> L20313
"日期添加","添加日期","重要日期添加"        -> L20315
"日期删除","删除日期","重要日期删除"        -> L20319
"可做事项","能做什么"                       -> L20322
"昵称","称呼"                               -> L20328
"语气","风格"                               -> L20335
"清空记忆","忘记我"                          -> L20343
+ 上文列出的 daily_schedule_* / photo_command_* / image_api_* /
  qweather_location_* / wakeup_alarm_* / tts_language_* / private_delivery_*
+ companion_manual_{query,confirm,cancel,setting} 集合
```

### 16.4 实现体位置（handler bodies）

类：`CommandHandlersMixin`（`command_handlers.py:40`）

| 函数 | 行 | 说明 |
|------|----|------|
| `async def _qweather_location_command_text(self, action, value)` | L43 | 天气地点命令 |
| `async def _persist_qweather_location_setting(...)` | L109 | 持久化 |
| `def _image_api_format_runtime_pair(self, *, backup)` | L161 | 图像 API 运行时配对 |
| `async def _swap_external_image_api_command_text(self, *, force)` | L281 | 切外部 API |
| `def _companion_manual_config_specs(self) -> list[...]` | L657 | 所有陪伴 manual config key 定义 |
| `def _companion_manual_aliases(self)` | L1073 | alias → key 映射 |
| `def _companion_manual_normalize_config_value(self, key, value)` | L1357 | value 标准化 |
| `async def _companion_manual_apply_pending_config(self, event)` | L2576 | 应用待生效配置 |
| `async def _companion_manual_apply_setting_command(self, event, text)` | L2643 | 应用设置 |
| `async def _companion_manual_answer(self, event, question)` | L3567 | 答疑回答 |
| `def _daily_outfit_command_payload(self)` | L3597 | 每日换装 |
| `async def _photo_reference_command_payload(self, event, user_id, value)` | L4413 | 图片参考指令 |
| `def _natural_language_photo_intent(self, ...)` | L4542 | 自然语言图片意图识别 |
| `async def _maybe_handle_natural_language_photo_request(...)` | L5156 | 自然语言触发 |
| `async def _handle_companion_photo_command(self, ...)` | L5488 | 处理生图命令 |
| `async def _group_companion_command_impl(self, event)` | L5809 | "陪伴群" 实现体 |

### 16.5 管理权守卫

```
def _can_manage_private_companion(event) -> bool: ...     # main.py L20050 附近
```

任何 `action in management_actions` 的分支先调用 `_can_manage_private_companion(event)` 决定是否回复。

---

## 17. LLM 工具（20+ 个 `pc_*`）

主模型可调用的工具，按出现顺序：

| 行号 | 工具名 | 方法行号 | 一句话用途 |
|---|---|---|---|
| 12460 | `pc_qzone_view_feed` | 12462 | 查看 QQ 空间动态 |
| 12505 | `pc_qzone_publish_feed` | 12507 | 发布 QQ 空间动态 |
| 12542 | `pc_qzone_reply_my_comment` | 12544 | 回复自己的 QQ 空间评论 |
| 12571 | `pc_generate_photo` | 12573 | 生图（自然语言） |
| 12652 | `pc_send_current_media` | 12654 | 发送当前媒体（图片/语音） |
| 12679 | `pc_find_reaction_image` | 12681 | 检索表情包素材库 |
| 12814 | `pc_manage_memo` | 12816 | 备忘管理 |
| 12872 | `pc_manage_schedule` | 12874 | 日程管理 |
| 12958 | `pc_view_creative_work` | 12960 | 查看创作作品 |
| 12986 | `pc_get_group_id_by_name` | 12988 | 按群名查 ID |
| 12998 | `pc_get_user_id_by_name` | 13000 | 按用户名查 ID |
| 13011 | `pc_query_relation_person` | 13013 | 查询关系人 |
| 13023 | `pc_get_specified_group_members` | 13025 | 查询指定群成员 |
| 13036 | `pc_query_interaction` | 13038 | 查询互动状态 |
| 13053 | `pc_relay_message` | 13055 | 中继消息 |
| 13075 | `pc_send_to_group` | 13077 | 主动发群 |
| 13106 | `pc_send_to_private_user` | 13108 | 主动发私聊 |
| 13145 | `pc_send_to_groups` | 13147 | 批量发群 |
| 13161 | `pc_send_to_private_users` | 13163 | 批量发私聊 |
| 13176 | `pc_schedule_group_relay` | 13178 | 定时群转述 |

> 工具按当前用户角色、会话类型、平台能力、扩展状态和功能开关**动态加入**，不会在所有会话中无条件暴露。生图工具由 `astrbot_plugin_image_companion` 检测并注册，创作工具由 `astrbot_plugin_content_companion` 检测并注册。

---

## 18. 扩展 API（8 个 CapabilityFamily）

`PrivateCompanionExtensionAPI`（`main.py:558`）是插件的对外门面，由 8 个 `_XxxCapabilityFamily` 组成：

| Family | 文件 | 行数 | 主要方法 |
|---|---|---|---|
| `_ContentCapabilityFamily` | `extension_api_content.py` | 279 | `get_realtime_voice_config`, `story_migration_capabilities`, `export_story_migration_snapshot`, `prepare_story_handoff`, `abort_story_handoff`, `commit_story_handoff`, `send_reality_touch_chat`, `record_reality_touch_output` |
| `_QzoneCapabilityFamily` | `extension_api_qzone.py` | 764 | `qzone_capabilities`, `qzone_status_snapshot`, `export_qzone_config_snapshot`, `execute_qzone_operation` |
| `_RelationshipCapabilityFamily` | `extension_api_relationship.py` | 442 | `get_reality_touch_host_context`, `stage_historical_relationship_observations` |
| `_MemoryCapabilityFamily` | `extension_api_memory.py` | 103 | `record_game_event`, `memory_page_capabilities`, `export_memory_page_snapshot`, `read_memory_page_photo`, `record_external_realtime_continuity`, `get_external_realtime_continuity` |
| `_SchedulerCapabilityFamily` | `extension_api_scheduler.py` | 153 | `register/unregister/list_proactive_ability`, `notify_mobile_location_update`, `get_reality_touch_cron_manager`, `delete_reality_touch_cron_job`, `notify_external_activity_started/updated/ended`, `get_external_activity` |
| `_IdentityCapabilityFamily` | `extension_api_identity.py` | 141 | `get_reality_touch_authorized_user_ids`, `get_bot_identity`, `get_unified_person_contract`, `resolve_unified_person`, `create_unified_person`, `get_unified_person_projection`, `get_unified_person_context`, `resolve_historical_chat_identities` |
| `_ImageCapabilityFamily` | `extension_api_image.py` | 12 | (空占位) |
| `_DiagnosticsCapabilityFamily` | `extension_api_diagnostics.py` | 64 | `get_p6_readonly_status`, `get_scene_context`, `get_realtime_context` |

### 18.1 公共方法签名（按行号）

```
bridge_lifecycle_status (592)                          # 暴露 plugin generation 可调用状态
_activate_story_migration_api (602)                    # generation 状态机推进
_supersede_story_migration_api (611)                   # 上一个 instance 让位
_close_story_migration_api (621)                       # 关闭

register_proactive_ability(spec) → bool (631)           # 注册外部主动能力
unregister_proactive_ability(name) → bool (636)        # 取消
list_proactive_abilities() → list (641)                 # 列出

async record_game_event(payload) (644)                  # 幂等游戏事件 → afterglow

memory_page_capabilities() (650)                        # Memory Page 只读 API 元信息
async export_memory_page_snapshot(*, target_plugin_id, selected_date) (654)
async read_memory_page_photo(*, target_plugin_id, photo_ref) (666)

get_realtime_voice_config() (678)                       # 暴露角色语音配置

story_migration_capabilities() (682)                    # Story 迁移能力
async export_story_migration_snapshot(*, lease_token) (686)
async prepare_story_handoff(*, target_plugin_id, owner_id) (696)
async abort_story_handoff(*, lease_token) (708)
async commit_story_handoff(*, lease_token) (718)

qzone_capabilities() (729)                              # QZone 元信息
qzone_status_snapshot() (734)
export_qzone_config_snapshot(*, target_plugin_id) (739)
async execute_qzone_operation(operation, payload) (750) # 执行单一 QZone 操作

async synthesize_realtime_voice(text, *, tts_provider, provider_settings, source, play_local) (759)

get_reality_touch_authorized_user_ids() → list (777)    # 可授权设备用户
async notify_mobile_location_update(user_id) (781)      # 通知移动 gateway 触发位置规划
get_reality_touch_host_context(user_id) (787)           # 暴露身份/关系上下文
export_reality_touch_legacy_state() (793)               # 一次性迁移载荷
async generate_reality_touch_text(prompt, **kwargs) (860)
async send_reality_touch_chat(umo, text) (867)
async record_reality_touch_output(...) (873)
get_reality_touch_cron_manager() (889)
async delete_reality_touch_cron_job(job_id) (892)

get_bot_identity() (897)                                # Bot 身份
get_unified_person_contract() (901)                     # 统一人格契约
resolve_unified_person(identity) (904)                  # 解析
create_unified_person(identity, *, profile, operation_id) (909)
get_unified_person_projection(person_id) (922)

get_p6_readonly_status() (927)                          # 只读 P6 计数
get_unified_person_context(event=None) (931)
get_scene_context(user_id="") (936)                     # Bot 生活场景上下文
get_realtime_context(user_id="", purpose="together") (942)

record_external_realtime_continuity(...) (949)
get_external_realtime_continuity(...) (969)

notify_external_activity_started(...) (975)
notify_external_activity_updated(...) (996)
notify_external_activity_ended(activity_id) (1017)
get_external_activity(*, user_id, activity_id) (1022)
```

### 18.2 外部主动能力注册示例

```python
async def my_executor(ctx):
    return {
        "ok": True,
        "context": "外部插件完成了一次适合分享的动作。",
        "summary": "外部动作",
        "memory": "这次动作留下的内部印象。",
    }

if api:
    api.register_proactive_ability({
        "name": "example_ability",
        "module": "示例插件",
        "label": "示例主动能力",
        "description": "在合适时机执行一项外部动作。",
        "when": "Bot 空闲且当前日程适合时",
        "use_for": "形成生活素材或内部印象",
        "avoid": "不要向用户暴露插件名和执行过程",
        "share_probability": 0.12,
        "min_interval_hours": 12,
        "default_enabled": False,
        "default_config": {"keyword": ""},
        "config_schema": {
            "keyword": {
                "label": "默认关键词",
                "description": "执行器可读取的自定义关键词",
            }
        },
        "executor": my_executor,
    })
```

`config_schema` 支持控件元数据：`"type": "select"` / `"text"` / `"bool"` / `"number"`。未声明时保留 JSON 文本编辑。

外部主动能力还可以提供同步 `availability(ctx)` 回调；返回 false 时不进候选。

### 18.3 游戏事件

```python
result = await api.record_game_event({
    "event_id": "room-1:gomoku:3:10001",
    "event_type": "round_finished",
    "user_id": "10001",
    "game": "gomoku",
    "game_label": "五子棋",
    "bot_result": "bot_loss",
    "scope": "group",
    "room_id": "123456789",
    "session_id": "default:GroupMessage:123456789",
    "match_id": "room-1:gomoku:match-7",
    "round_number": 3,
    "source_plugin": "astrbot_plugin_game_companion",
})
```

- `event_id` 唯一；重复上报不会再次结算
- `round_number` + `match_id` 同 `match_id` 内判断回合乱序
- 按人格、私聊/群聊、会话、游戏分别保存
- 余韵独立于用户伤害、拒绝和关系分数
- 过期状态会停止注入，长期未使用的 scope 会自动清理

---

## 19. 联动桥接

### 19.1 ProactiveChat 桥（`proactive_chat_runtime_bridge.py`，670 行）

`ProactiveChatRuntimeBridge` 类。

**关键常量**：

```python
REQUIRED_METHODS = ("check_and_chat", "_prepare_llm_request",
                    "_generate_llm_response", "_send_proactive_message",
                    "_send_chain_with_hooks", "_finalize_and_reschedule")
```

**发现机制**：

```python
_discover_instance(context):
    # 遍历 context.get_all_stars()
    # 过滤 astrbot_plugin_proactive_chat 子串
    # 用 setattr(target, name, MethodType(wrapper, target)) 注入包装
```

watch 循环 12s 刷新，发现变更后注入/回收。

**职责**：

- 在生成前共享关系与状态
- 防撞（`bridge_review_mode`）
- 生成后统一复核 + 发送结算
- 不修改对方源码，不创建第二套定时任务
- 版本不兼容或缺方法时 fail-closed，面板显示"深度联动已降级"

### 19.2 Memory 桥（`memory_companion_adapter.py`，3602 行）

`MemoryCompanionAdapterMixin`

**关键常量**：

```python
_MEMORY_COMPANION_PLUGIN_ALIASES = frozenset({
    "astrbot_plugin_memory_companion",
    # + 6 个旧别名
})
_BRIDGE_CACHE_TTL = 30.0
_BRIDGE_MISSING_CACHE_TTL = 2.0
_LEGACY_V2_CONTRACT = ...
```

**模式**：`resolve_external_bridge()` / `invalidate_external_bridge_cache()`

提供：

- 情绪漂移（`emotional_drift`）
- 跨窗口情绪（`cross_window_emotion`）
- 梦境碎片（`dream_fragment`）
- 未完成话题搜索（`open_loop_search`）
- 特征上下文（`feature_context`）
- 私聊召回（`private_recall`）

### 19.3 Image 桥（`image_companion_bridge.py`）

兼容 facade，从 `companion.integrations.image_companion_bridge` re-export。

能力：

- 上传参考图（最多 24 张）
- 服装 / 姿势 / 场景 / 画风职责标注
- 生图任务提交
- 在线 API 切换
- 排障自拍

### 19.4 Content 桥（`content_companion_bridge.py`，1004 行）

**关键常量**：

```python
_CONTENT_PLUGIN_ID = "astrbot_plugin_content_companion"
_CONTENT_STORY_OWNER_ID = "astrbot_plugin_private_companion"
_CONTENT_REQUIRED_CAPABILITIES = frozenset(...)
_ContentStoryModelBudget.max = 8
```

能力：

- 创作项目（大纲、章节、审校）
- 书柜
- 创作封面
- 故事迁移（`story_migration_*`）
- `pc_view_creative_work` 工具
- `story_handoff` 三件套（prepare/abort/commit）

### 19.5 Bridge 通用：ctx 字段语义

- `ctx.user_id` / `ctx.session_id` — 触发请求归属
- `ctx.scene` — `private` / `group`
- `ctx.unified_persona_id` — 多 persona 桥接
- `ctx.owner_token` — 用于 backend 写入票据校验
- `ctx.ext_metadata.proactive_chat_hint` — 主动聊天触发

### 19.6 联动解析层（6.2.5+ 起）

- 6.2.5 起：生图、现实触及、内容创作、NAI 等可选扩展**统一经过联动解析层发现**
- 当前 AstrBot 上以 active registry 的精确插件身份为准
- 调用前校验：生命周期、API 版本、能力
- 扩展刚启动 / 重载 / 卸载时会重新协商
- registry 明确缺失 / 对象歧义 / 方法不完整 / 未知高版本 / 已撤权代 → `unavailable`，不会从残留模块或 GC 对象恢复能力

---

## 20. 能力子系统

### 20.1 Creative（创意项目）— `creative.py` 2306 行

`CreativeMixin`（L174）：

| 方法 | 用途 |
|---|---|
| `_creative_projects()` | 持久化读取 |
| `_creative_chars_per_session` | 单次字符上限 |
| `_creative_advance_gap_minutes` | 推进间隔 |
| `_generate_creative_project / _generate_outline_for_chunk / _review_creative_chunk / _generate_creative_chunk` | 单步流水线 |
| `_maybe_start_creative_project / _maybe_advance_creative_projects / _maybe_schedule_creative_share` | 调度三件套 |
| `_maybe_generate_creative_cover` | 生封面 |

含 memory pool + story bible 字段。

### 20.2 Dreaming（梦境 & 日记）— `dreaming.py` 1229 行

80+ helper：

- 片段池：`normalize_dream_fragment_pool` / `build_dream_memory_fragments` / `dream_theme_specs`
- 日记：`recent_diary_context` / `recent_diary_tags` / `fallback_diary_payload` / `generate_daily_diary`
- 权重采样：`dream_fragment_effective_weight`
- `extract_weighted_dream_fragments` / `merge_dream_fragment_pool` / `weighted_unique_fragment_sample`

每次 nightly tick 抽 3-5 片段 → 生成 `dream_chunk`。

### 20.3 Companion Interaction Expression — `companion_interaction_expression.py` 867 行

- `ExpressionBand` enum（L42，7 档：relaxed/lively/warm/close/affectionate/avoidant/hurt）
- `ExpressionDecision` dataclass（L87）
- `ExpressionInput`（L124）
- `build_expression_decision(L394)` 主决策
- `current_interaction_projection(L671)` 投影
- `allowed_expression_bands(relationship_role, relationship_mode)` 关系档位→可用 band 过滤
- safety / p4-block / contact-boundary 三类硬阻断

### 20.4 TTS Enhancement — `tts_enhancement.py` 5994 行

`TtsEnhancementMixin`（L353）；`_MimoVoiceCloneTtsAdapter`（L216）；`apply_tts_enhancement_request`（L2636）主入口；`_process_tts_tags`（L5313）。

能力：

- Fish Audio S2.1/S2/S1 模型与情绪控制策略
- 语言检测与转换（→ 保持原语言/转中文开关）
- 链式分段：`split_chain_for_ordered_send`
- `_MimoVoiceCloneTtsAdapter` 单独管理 Mimo 克隆语料的预热与缓存
- 本机播放与直播打字机字幕同步

### 20.5 Private Image — `private_image.py` 6223 行

`PrivateImageMixin`（L68）：

- vision cache TTL
- provider 选择带 cooldown
- image-only 事件缓冲（用户连发时合并一次生图）
- GIF 帧采样（按帧间隔 + 最大帧数）
- 图片归属线索（避免把 Bot 主动发送的图片误认成用户作品）

### 20.6 News Exploration — `news_exploration.py` 4901 行

`NewsExplorationMixin`（L428）：

- 按主题订阅 → LLM 摘要 → 写入最近资讯列表
- 每条 source 限频
- 附加 `news_card_template` 富文本渲染
- 支持：B 站见闻、AI 日报（橘鸦 Juya、黑鸦 Heya）、主动搜索

### 20.7 QZone — 6 个文件

| 文件 | 行数 | 内容 |
|---|---|---|
| `qzone_contract.py` | 16 | `QZONE_TARGET_PLUGIN_ID` 常量 + capability 描述符 + `degraded_reasons` |
| `qzone_runtime.py` | 848 | `QzoneRuntimeMixin` |
| `qzone_publish.py` | 1223 | `QzonePublishMixin` |
| `qzone_comments.py` | 867 | `QzoneCommentsMixin` |
| `qzone_feed.py` | 585 | `QzoneFeedMixin` |

操作白名单：`feed / detail / refresh / publish / like / comment / delete`，reference TTL=10min。

**主动发布**：life_publish 整套配置：min_interval / probability / max_daily / window_mode=template_double|wins / cus_insomnia / intra_day_gap / double / custom_windows / similarity_threshold / style_prompt / generated_image_publish / probability / publish_image_style_prompt / comment_inbox interval / posts / replies / emotional_vent 三件 `enable/threshold/cooldown_hours/probability`。

### 20.8 Relationship — 3 文件（详见 §12.2）

### 20.9 Group — 3 文件（详见 §15）

### 20.10 Affect / Social 域（详见 §13、§14）

### 20.11 User Memory & Worldbook & Memo

- `user_memory.py`（10 752 行）`UserMemoryMixin`（L263）— 长期记忆、知识检索、scope 隔离
- `worldbook.py`（1768）`WorldbookMixin`（L242）— 世界书条目、关联引用
- `memo_notes.py`（239）— 纯函数集：`clean_memo_note_content` / `normalize_memo_note` / `memo_note_due_state` / `advance_recurring_memo_due` / `memo_note_sort_key` / `apply_memo_note_action`

### 20.12 现实触及（已拆分）

- `astrbot_plugin_reality_companion`（我会来到你身边）已拆分独立插件
- 本插件保留桥接：`RealityCompanionBridgeMixin` + `body_monitor_integration.py`
- 摄像头、音频设备、授权、现实提醒、设备主动语音策略由联动插件管理
- 联动插件首次启动会迁移本插件旧版本中的授权、策略、闹钟和提醒记录

### 20.13 余额感知 — `balance_awareness.py` 32 053 字节

`BalanceAwarenessMixin`：

- API 余额感知（threshold、percent_threshold、cooldown_hours、include_amount_in_message）
- 自定义请求头 + 鉴权 scheme
- JSON path 支持（`balance_json_path/total_json_path/used_json_path`）
- `balance_value_divisor` / `balance_currency_label` / `check_interval_minutes` / `request_timeout_seconds`
- 低阈值 / 严重阈值 → 主动消息暂缓
- 联动：`_maybe_refresh_balance_awareness` 后台任务

### 20.14 @ 中继 — `atrelay.py` 67 050 字节

`AtRelayMixin`：

- 跨群转述
- @ 群友工具
- `multi_target_limit` 限制多目标
- 与外部 `astrbot_plugin_atrelay` 共存时的兼容

### 20.15 模型路由 — `model_routing.py`

- 关键词换模：contains / exact / regex / 优先级
- DeepSeek 峰时替换：在高价时区窗口内临时路由到峰时替换模型
- 敏感拒答替换：识别常见拒绝短语，阻断原文本并让指定 Provider 重试
- 替换模型仍拒答时不会把原拒答发给用户
- 所有替换只改运行时选模，不改写原 Provider 配置

### 20.16 Token 预算 — `token_budget.py`

- 硬限额 + 软限额
- 单卡预算（模型页"单次 Token 上限（预估）"）
- 估算输入提示词、视觉请求和最大输出预算
- 超限优先调用备用模型，未配置时不阻断请求
- 多人格模式按人格汇总

---

## 21. 陪伴面板（`page_api.py`，31 111 行）

### 21.1 总入口注册

```python
PAGE_API_PREFIX = "/astrbot_plugin_private_companion/page"

class PrivateCompanionPageApi:                                # line 314
    def route_bindings(self) -> list[tuple[str, callable, list[str], str]]:
        return ~200+ 条 (path, handler, methods, desc)         # line 1172

    def register_routes(self):
        for path, handler, methods, desc in self.route_bindings():
            self.plugin.context.register_web_api(
                f"{PAGE_API_PREFIX}{path}", handler, methods, desc
            )                                                  # line 1385
```

### 21.2 路径前缀 → 模块分布

| 前缀 | 所在 mixin 文件 | 职责 |
|---|---|---|
| `/overview` `GET` | `page_api.py` | 仪表盘概览 |
| `/calendar/*` | `page_api.py` | 日历/事件 |
| `/expression-library/*` | `page_api.py` | 表达档位库 |
| `/users` `/user/*` | `page_api_users_groups.py`（2070 行）`PrivateCompanionPageApiUsersGroupsMixin`（line 30） | 用户列表/详情/统一身份 link |
| `/groups` `/group/*` `/group/slang/update` | 同上 | 群列表/详情/黑话更新 |
| `/settings/update` `/config/*` | `page_api_settings.py`（1382 行）`PageSettingNormalizerMixin`（line 34） | 配置归一化写入 |
| `/reality-touch/*` | `page_api.py` | 现实联动调度 |
| `/extensions/image/status` `/image/debug` `/image_api/*` `/image_cache/*` | 跨文件 | 图像子系统 |
| `/proactive_only/unlock` `/proactive/candidate/*` | `page_api.py` | 主动候选解锁 |
| `/diagnostics` `/troubleshooting` | `extension_api_diagnostics.py`（混入） | 诊断 |
| `/daily-review/*` | `page_api.py` | 日回顾 |
| `/token/*` | `page_api.py` | Token 限额统计 |
| `/reaction_library/*` `/reaction_assets/*` | `page_api.py` | 表达库素材 |
| `/photo_reference/*` `/reference_asset/*` | `page_api.py` | 参考图谱 |
| `/worldbook/member/reference/*` `/knowledge/reference/*` `/worldbook/*` | `page_api.py` | 知识库 |
| `/relationship/role/reference/*` | `page_api.py` | 关系角色参考 |
| `/daily_outfit/*` | `page_api.py` | 每日穿搭 |
| `/bookshelf/*` | `page_api.py` | 书架 |
| `/memo/*` | `page_api.py` | 备忘 |
| `/qzone/*` | `page_api_qzone.py`（532 行, line 17） | QQ 空间操作 |
| `/creative/project/*` | `page_api.py` | 创作项目 |
| `/skill/update` `/personal_goal/update` | `page_api.py` | 技能/个人目标 |
| `/food_menu/*` | `page_api.py` | 菜谱 |
| `/external_ability/update` | `page_api.py` | 外部能力开关 |
| `/setup/*` | `page_api.py` | 向导式初始化 |
| `/roleplay/*` `/persona/*` `/preset/apply` | `page_api.py` | 人设 |
| `/providers/*` `/provider/test` | `page_api_settings.py` | 提供方测试 |
| `/tts/*` | `page_api.py` | TTS 配置 |

### 21.3 角色装饰器

`_multi_persona_page_context`（line 35）— 校验 multi-persona 上下文。

### 21.4 页面资源

前端在 `pages/companion-panel/` 和 `pages/陪伴面板/` 两个目录（双份以兼容 AstrBot 旧资源加载路径）。

---

## 22. 独立 WebUI（`standalone_webui.py`，986 行）

### 22.1 类与配置

```python
class StandaloneWebUIServer:       # line 169
PORT = 6190                         # line 202
API_PREFIX = "/api/v1"
BRIDGE_API_PREFIX = "/astrbot_plugin_private_companion/page"
SESSION_COOKIE_NAME = "private_companion_session"
# Cookie 属性：HttpOnly + SameSite=Strict
# Token 摘要：SHA-256
```

### 22.2 路由收集与桥接

```python
class _ContextRouteCollector:        # line 145 — 收集非 mutating 路由

def _register_page_api_routes(self):  # line 558
    # 遍历 plugin.page_api.route_bindings()
    # 挂到 _standalone_route_suffix 路径后缀
```

### 22.3 登录

```python
async def _login(self):                # line 623
    # 校验密码
    # 设置 HttpOnly + SameSite cookie
    # 返回 token digest
```

- 限流：login 5 次 / 300 秒
- CSRF 通过校验 Origin
- 至少 16 字符 token
- TTL 范围 1-168 小时

### 22.4 与 AstrBot 面板的关系

- **共存**：默认独立 WebUI 关闭；AstrBot 插件扩展页继续可用
- **业务复用**：独立入口复用原陪伴面板的业务处理函数和数据锁，**不会复制数据或启动额外轮询**
- **关闭时没有运行时开销**；开启后空闲状态只有一个本地监听任务
- **不要把未配置 HTTPS 的独立端口直接暴露到公网**

---

## 23. 迁移体系

### 23.1 五条迁移子模块

| 模块 | 用途 |
|---|---|
| `migration_backfill.py` | 历史数据回填（如关系账本 schema 升级） |
| `migration_coordinator.py` | 协调多源迁移（`MigrationCoordinator`） |
| `migration_outbox.py` | 迁移 outbox（`MigrationOutbox`）保证幂等 |
| `migration_dual_write.py` | 双写期保证两个后端一致 |
| `migration_read_router.py` | 读路由（按代际选主备） |
| `migration_replay.py` | 重放已发送迁移 |
| `migration_scoped_projection.py` | 范围投影迁移 |
| `migration_source_inspector.py` | 数据源检查 |
| `migration_stability.py` | 迁移稳定性监控 |

### 23.2 配置迁移（`config_migration.py`）

- `LEGACY_KEY_ALIASES`：旧字段名 → 新字段名
- `migrate_flat_config_into_schema_groups(c)`：扁平 → schema 分组
- `_migrate_relationship_switch_semantics`：关系开关语义迁移
- `_migrate_command_photo_quota_semantics`：生图额度语义迁移
- `_migrate_photo_scope_quota_semantics`：生图范围额度语义迁移
- `_migrate_qweather_config`：和风天气配置迁移

### 23.3 数据迁移（`storage/migration.py`）

- `migrate_json_to_backend_if_needed(backend, json_backend, default_data)` 三分支
- 启动时一次性执行

### 23.4 故事迁移（`story_authority.py` + `story_handoff.py` + `story_migration_contract.py`）

- `resume_story_handoff(self)`：恢复故事接管
- `_activate_story_migration_api` / `_supersede_story_migration_api` / `_close_story_migration_api`：代际状态机
- `prepare_story_handoff(*, target_plugin_id, owner_id)` / `abort_story_handoff(*, lease_token)` / `commit_story_handoff(*, lease_token)`
- 租约机制保证不被多个 instance 同时迁移

### 23.5 req041 迁移（`req041_*`）

- `self.req041_migration_coordinator = MigrationCoordinator(self.data_dir)`
- `self.req041_migration_outbox = MigrationOutbox(Path(self.data_dir) / "req041_migration_outbox.db")`
- `req041_automatic_migration` / `req041_memory_scope_rebind` 后台任务
- `_req041_scoped_projection_status` 监控状态

### 23.6 Persona 资料迁移

- `_retire_legacy_persona_routing_sync()`：旧路由退役
- `_migrate_persona_profiles_sync()`：写入 `_persona_settings_migration_status`

### 23.7 拆分前版本升级流程（来自 README）

1. 从陪伴面板导出配置，并备份主插件数据目录。
2. 停止 AstrBot。
3. 先安装原使用功能对应的独立扩展，但**不要中途启动或重载插件**。
4. 再原地更新 `astrbot_plugin_private_companion`，不要删除旧数据和旧配置。
5. 全部完成后统一启动 AstrBot，在"总览"和"拓展"确认扩展已安装、启用且可用。

> 内容扩展会在主插件尚未就绪时保持迁移待定，待主插件加载后自动导入旧作品、创作参数和 QQ 空间设置。**迁移完成前不会用新插件默认值覆盖旧 QQ 空间状态**。生图按需兼容读取旧后端、密钥和图库，现实触及会延迟重试旧授权与设备配置迁移。**迁移过程只复制或读取旧数据，不会删除旧文件**。

---

## 24. 关键设计哲学

### 24.1 "Bot 应当像人一样活着"

> 这是 README 中的开发者自述：
>
> 「你好，我要一个有记忆、有生活、有自己的小秘密和想法、有喜怒哀乐和健康的 bot。」
> 「这得装不少插件，先生。」
> 「我知道，再让它们之间能够互相影响。」
> 「怎么让 bot 更像人？在此之前已经有很多优秀的插件给出了自己的答卷……」
> 「但我觉得 bot 拟人只需要做好一件事，像人一样活着。……我的一天中，我会做什么，bot 也应该会做什么。」

落到代码层面：

- 状态 / 日程 / 细化三件套让 Bot 有"正在做什么"
- 主动消息系统让 Bot 有"想和你说话"
- 梦境 / 日记 / 表达学习让 Bot 有"自己的秘密和表达"
- 关系账本 / 互动档位 / 情绪事件让 Bot 有"喜怒哀乐"
- 健康 / 饥饿 / 周期 / 休息让 Bot 有"生理节律"
- 技能成长 / 个人目标让 Bot 有"成长"

### 24.2 持续型上下文，不是单次工具

- 多层提示词：稳定层 + 当前层 + 关系层 + 内容层 + 发送前层
- 稳定内容保持在前部，动态内容后置 → 减少对 Provider prompt cache 的破坏

### 24.3 可观察、可审计、可降级

- **可观察**：主动候选保留来源、时间窗、状态、拦截原因、发送诊断
- **可审计**：每个主动消息都有审计日志（`_append_proactive_audit`）
- **可降级**：扩展缺失时只关闭对应能力，不阻塞核心；mode=auto 时在线 API 失败回退 ComfyUI，再回退 SDGen

### 24.4 不替代 AstrBot 主回复人格

- AstrBot 人格负责"我是谁、我说什么"
- 本插件负责"我现在状态如何、我和他关系怎样、什么时候主动找他说话"
- 插件不替代主回复人格，但可以通过：
  - 提示词注入（5 层）
  - LLM 工具暴露（20+ 个）
  - 模型路由替换（关键词 / DeepSeek 峰时 / 敏感拒答）
  - TTS 强化、表情包预判
  影响主回复

### 24.5 边界判断与修复

- **明确越界**按档位差分为轻度、中度、严重
- 恶意贬低角色、角色珍视之物视为踩底线
- 自然恢复只返还配置允许恢复的部分
- 真诚道歉可有限加速修复；同类再犯追回上一次道歉恢复的关系分
- 连续踩底线逐级进入明确拒绝、冷静反思、关系降档

### 24.6 关系表达 ≠ 权限扩展

- 关系背景文本只属于关系资料
- **不开放工具、隐私、设备、平台管理、现实操作或内容权限**
- 不能覆盖当轮明确边界和更高优先级规则

### 24.7 fail-closed 默认

- 未知高版本 → unavailable
- descriptor 缺字段 → unavailable
- 能力不完整 → unavailable
- 实例 generation 改变 → unavailable
- 生命周期不是 ready/active → unavailable
- 需要写入、设备、平台或付费后端的权限不会因插件联动自动扩大

### 24.8 多人格隔离

- 每个人格分别维护资料、日程、状态、日记、用户、群聊关系、便笺、Token 记录
- 主人格负责未绑定窗口回退
- 绑定错误可在对应窗口行点击"解除"
- 关闭多人格模式后继续使用原来的单资料行为

### 24.9 不抓取第三方敏感内容

- 资料柜不会抓取或管理第三方专辑、漫画或成人内容
- 公开 QQ 空间内容不会使用私聊隐私、关系网内部备注或原始状态数值

---

## 25. 附录：核心文件索引

```text
# 入口与生命周期
main.py                                          21 112
plugin_bootstrap.py                               2 236
plugin_identity.py                                   70
constants.py                                        748

# 存储
core_store.py                                     5 798
storage/__init__.py                                  16
storage/backend_base.py                              61
storage/factory.py                                   22
storage/json_backend.py                             139
storage/sqlite_backend.py                        1 153
storage/store_manager.py                           553
storage/path_generation.py                         154
storage/migration.py                                 66

# 状态 / 日程 / 细化
daily_state.py                                   18 627
daily_state_tick.py                               2 293
agenda_runtime.py                                  (~)
agenda_contracts.py                              (~)
unified_agenda.py                                 (~)
schedule_authority.py                                (~)
schedule_reconciler.py                             (~)

# 主动消息
proactive.py                                      4 503
proactive_engine.py                              10 689
proactive_message.py                             18 038
proactive_routes.py                                 593
proactive_chat_runtime_bridge.py                   670

# 关系
relationship_ledger.py                             713
relationship_policy.py                             340
relationship_affinity_runtime.py                   147
relationship_event_policy.py                      (~)
companion_interaction_expression.py                867

# 群聊
group_observation.py                             4 926
group_wakeup.py                                  1 661
group_member_safety.py                          (~)
group_cycle_boundary.py                            102
group_prompt_context.py                         (~)
group_context_interception.py                    (~)

# 图片
private_image.py                                 6 223
image_companion_bridge.py                           18
nai_image_bridge.py                              (~)
photo_reference_*.py                             (~5)
photo_wardrobe_decision.py                       (~)
photo_prompt_context.py                          (~)
photo_generation_scope.py                        (~)

# TTS
tts_enhancement.py                               5 994
tts_tool_sanitizer.py                            (~)

# 梦境 / 日记 / 创作
dreaming.py                                      1 229
daily_review.py                                 88 873 bytes
creative.py                                      2 306

# 记忆 / 用户 / 世界书
user_memory.py                                  10 752
worldbook.py                                     1 768
memo_notes.py                                      239
authoritative_private_memory.py                    (~)
memory_page_snapshot.py                          (~)
memory_context_policy.py                         (~)

# 命令 / 事件分发 / 消息流
command_handlers.py                              6 022
event_dispatch.py                                6 353
message_pipeline.py                            (~1 300)
passive_state_pipeline.py                       (~)
busy_reply_gate.py                              (~)
silent_reply_gate.py                            (~)
user_rest_gate.py                               (~)
runtime_scene_resolver.py                       (~)

# 新闻 / 搜索 / B站
news_exploration.py                              4 901

# QQ 空间
qzone_contract.py                                   16
qzone_runtime.py                                   848
qzone_publish.py                                 1 223
qzone_comments.py                                  867
qzone_feed.py                                      585

# LLM 调用
model_routing.py                                (~)
token_budget.py                                 (~)
llm_tool_actions.py                             (~)

# 面板 / WebUI
page_api.py                                    31 111
page_api_settings.py                            1 382
page_api_users_groups.py                        2 070
page_api_qzone.py                                 532
standalone_webui.py                               986

# 扩展 API
extension_api_content.py                          279
extension_api_diagnostics.py                        64
extension_api_identity.py                          141
extension_api_image.py                              12
extension_api_memory.py                            103
extension_api_qzone.py                             764
extension_api_relationship.py                      442
extension_api_scheduler.py                         153

# 联动桥接
content_companion_bridge.py                     1 004
memory_companion_adapter.py                     3 602
proactive_chat_runtime_bridge.py                   670
image_companion_bridge.py                           18

# 配置 / 迁移
config_migration.py                             1 402
constants.py                                       748
migration_backfill.py                           (~)
migration_coordinator.py                        (~)
migration_dual_write.py                         (~)
migration_outbox.py                             (~)
migration_read_router.py                        (~)
migration_replay.py                             (~)
migration_scoped_projection.py                  (~)
migration_source_inspector.py                   (~)
migration_stability.py                          (~)
story_migration_contract.py                     (~)
story_authority.py                              (~)
story_handoff.py                                (~)

# 情绪 / 社交领域
domains/__init__.py                                  0
domains/affect/__init__.py                           1
domains/affect/affect_modulation.py                  47
domains/affect/emotion_event_contract.py            162
domains/affect/emotion_event_ledger.py               62
domains/affect/emotion_targeting.py                  46
domains/affect/interaction_dynamics.py              125
domains/affect/reply_temperature.py                 179
domains/social/__init__.py                            0
domains/social/group_moments.py                      287
domains/social/group_mood.py                         262
domains/social/joke_boundary.py                      269
domains/social/roleplay_strength.py                  128

# 其他能力
body_monitor_integration.py                      (~)
balance_awareness.py                            32 053 bytes
forward_message.py                              (~)
atrelay.py                                      67 050 bytes
debug_runtime.py                                (~)
logging_util.py                                 (~)
runtime_compat.py                               (~)
```

> 说明：`(~)` 行未在本次扫描中读取精确行数；`bytes` 后缀表示该文件因权限无法读取行数但确认存在。

---

## 收尾：阅读建议

按角色挑章节：

- **想理解"插件为什么这样设计"** → §0、§24
- **想理解启动顺序** → §3、§4
- **想理解消息怎么流过插件** → §7、§8、§9
- **想理解"主动消息是怎么生成的"** → §10
- **想理解 Bot 的"状态/日程/关系"** → §11、§12、§13、§14
- **想理解群聊特殊能力** → §15
- **想给插件加命令 / 工具** → §16、§17
- **想集成其他 AstrBot 插件** → §18、§19
- **想开面板 / WebUI** → §21、§22
- **遇到升级 / 数据迁移问题** → §23
- **想找某个文件 / 概念在哪** → §25

### 内部标签清理开关

`enable_framework_error_leak_guard` 保留原配置键，界面名称为“内部标签清理”，默认开启，旧配置的关闭值继续保留。默认开启。清理待发正文中的插件内部标签、AstrBot 内部标签和成对推理标签，保留 Markdown 代码示例。关闭后跳过普通回复、分段回复、主动消息和工具直发的内部标签清理，助手历史同步保留正文。此开关只控制标签清理，不启用已删除的框架报错或工具循环关键词拦截。回复复核、业务静默、语音合成、分段解析和独立的存储清洗各按自身配置执行。

此键不再表示框架异常关键词拦截。该拦截逻辑已删除，不会因开启标签清理而恢复。`enable_store_control_tag_sanitization` 控制的记忆/日记存储清洗是另一项功能。
