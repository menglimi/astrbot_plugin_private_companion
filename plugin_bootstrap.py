# -*- coding: utf-8 -*-
"""插件启动引导：把配置落盘/校验与运行时初始化拆到 part 模块，本文件只做聚合与入口。

拆分说明：
- plugin_bootstrap_shared.py：常量、依赖导入、模块 logger（被所有 part 依赖，无反向依赖）
- plugin_bootstrap_part01.py：配置根映射 / 主人格 / 入口态 / core+relationship 初始化
- plugin_bootstrap_part02.py：world+model 初始化
- plugin_bootstrap_part03.py：proactive+reaction 初始化
- plugin_bootstrap_part04.py：photo+expression 初始化
- plugin_bootstrap_part05.py：review+group 初始化
- plugin_bootstrap_part06.py：group+provider、runtime、post-runtime 初始化

对外导出保持不变，``from .plugin_bootstrap import X`` 仍然可用。
"""
from __future__ import annotations

from typing import Any

from .plugin_bootstrap_part01 import (
    _config_root_mapping,
    _initialize_core_and_relationship_config,
    _initialize_primary_persona_config,
    _legacy_photo_scene_preset_names,
    _remove_legacy_multi_persona_primary_from_runtime_config,
    _validate_primary_persona_runtime,
    initialize_plugin_entrypoint_state,
)
from .plugin_bootstrap_part02 import _initialize_world_and_model_config
from .plugin_bootstrap_part03 import _initialize_proactive_and_reaction_config
from .plugin_bootstrap_part04 import _initialize_photo_and_expression_config
from .plugin_bootstrap_part05 import _initialize_review_and_group_config
from .plugin_bootstrap_part06 import (
    _initialize_group_and_provider_config,
    initialize_plugin_post_runtime_state,
    initialize_plugin_runtime,
)
from .plugin_bootstrap_shared import (
    BodyMonitorIntegration,
    DEFAULT_AI_DAILY_JUYA_UID,
    DEFAULT_AI_DAILY_MORNING_UID,
    DEFAULT_AI_DAILY_SOURCES,
    DEFAULT_NATURAL_LANGUAGE_PHOTO_EXTRA_PROMPT,
    DEFAULT_NEWS_SOURCES,
    DEFAULT_REPLY_STYLE_PROMPT,
    DEFAULT_SENSITIVE_REPLACEMENT_KEYWORDS,
    LEGACY_DEFAULT_NEWS_SOURCES,
    MigrationCoordinator,
    MigrationOutbox,
    P5AttestationRegistry,
    PAGE_FONT_NAMES,
    PAGE_THEME_NAMES,
    PHOTO_GENERATION_SCOPE_LIMIT_KEYS,
    PLUGIN_ID,
    PLUGIN_NAME,
    PLUGIN_VERSION,
    PREVIOUS_TECH_DEFAULT_NEWS_SOURCES,
    ProactiveChatRuntimeBridge,
    UnifiedPersonRegistry,
    WARDROBE_MAX_ITEMS,
    WARDROBE_PROMPT_MAX_ITEMS,
    _LEGACY_MULTI_PERSONA_PRIMARY_KEY,
    _LEGACY_PHOTO_SCENE_PRESET_NAMES,
    _flat_get,
    _normalize_photo_generation_scopes,
    _normalize_timezone_setting,
    _set_into_config,
    _set_today_key_timezone,
    _single_line,
    build_rules,
    capability_descriptor,
    contract_self_check,
    load_catalog,
    logger,
    migrate_flat_config_into_schema_groups,
    normalize_component_order,
    normalize_component_strategy,
    normalize_continuity_scope,
    normalize_group_allowlist,
    normalize_photo_generation_scopes,
    normalize_relationship_positive_stage_cap_key,
    normalize_relationship_stage_policy,
    normalize_relationship_stage_provider_routes,
    normalize_scope,
    normalize_wardrobe_image_prompt,
    normalize_wardrobe_items,
    normalize_wardrobe_outfits,
    normalize_window_modes,
    plugin_identity_snapshot,
    probe_runtime_capabilities,
    story_startup_sync_operation,
    validate_and_serialize,
)

__all__ = [
    "BodyMonitorIntegration",
    "DEFAULT_AI_DAILY_JUYA_UID",
    "DEFAULT_AI_DAILY_MORNING_UID",
    "DEFAULT_AI_DAILY_SOURCES",
    "DEFAULT_NATURAL_LANGUAGE_PHOTO_EXTRA_PROMPT",
    "DEFAULT_NEWS_SOURCES",
    "DEFAULT_REPLY_STYLE_PROMPT",
    "DEFAULT_SENSITIVE_REPLACEMENT_KEYWORDS",
    "LEGACY_DEFAULT_NEWS_SOURCES",
    "MigrationCoordinator",
    "MigrationOutbox",
    "P5AttestationRegistry",
    "PAGE_FONT_NAMES",
    "PAGE_THEME_NAMES",
    "PHOTO_GENERATION_SCOPE_LIMIT_KEYS",
    "PLUGIN_ID",
    "PLUGIN_NAME",
    "PLUGIN_VERSION",
    "PREVIOUS_TECH_DEFAULT_NEWS_SOURCES",
    "ProactiveChatRuntimeBridge",
    "UnifiedPersonRegistry",
    "WARDROBE_MAX_ITEMS",
    "WARDROBE_PROMPT_MAX_ITEMS",
    "build_rules",
    "capability_descriptor",
    "contract_self_check",
    "initialize_plugin_config",
    "initialize_plugin_entrypoint_state",
    "initialize_plugin_post_runtime_state",
    "initialize_plugin_runtime",
    "load_catalog",
    "logger",
    "migrate_flat_config_into_schema_groups",
    "normalize_component_order",
    "normalize_component_strategy",
    "normalize_continuity_scope",
    "normalize_group_allowlist",
    "normalize_photo_generation_scopes",
    "normalize_relationship_positive_stage_cap_key",
    "normalize_relationship_stage_policy",
    "normalize_relationship_stage_provider_routes",
    "normalize_scope",
    "normalize_wardrobe_image_prompt",
    "normalize_wardrobe_items",
    "normalize_wardrobe_outfits",
    "normalize_window_modes",
    "plugin_identity_snapshot",
    "probe_runtime_capabilities",
    "story_startup_sync_operation",
    "validate_and_serialize",
]


def initialize_plugin_config(self: Any, config: Any) -> None:
    c = config
    _initialize_core_and_relationship_config(self, c)
    _initialize_world_and_model_config(self, c)
    _initialize_proactive_and_reaction_config(self, c)
    _initialize_photo_and_expression_config(self, c)
    _initialize_review_and_group_config(self, c)
    _initialize_group_and_provider_config(self, c)
    self.enable_p4_b_legacy_score_isolation = self._cfg_bool(
        c,
        "enable_p4_b_legacy_score_isolation",
        False,
    )
