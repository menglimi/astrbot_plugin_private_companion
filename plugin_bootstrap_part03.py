# -*- coding: utf-8 -*-
"""plugin_bootstrap 拆分件 part03：插件引导初始化（机械搬移，行为不变）。

函数体逐字搬自 plugin_bootstrap.py，仅调整模块级依赖的导入来源。
"""
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from astrbot.api.star import StarTools

from .body_monitor_integration import BodyMonitorIntegration
from .bot_personal_contract import capability_descriptor, contract_self_check
from .config_migration import migrate_flat_config_into_schema_groups
from .hdsi_experiment import normalize_continuity_scope, normalize_window_modes
from .constants import (
    DEFAULT_NATURAL_LANGUAGE_PHOTO_EXTRA_PROMPT,
    DEFAULT_REPLY_STYLE_PROMPT,
    PAGE_FONT_NAMES,
    PAGE_THEME_NAMES,
    PLUGIN_NAME,
)
from .helpers import (
    _flat_get,
    _normalize_timezone_setting,
    _set_into_config,
    _set_today_key_timezone,
    _single_line,
    normalize_photo_generation_scopes,
)
from .p5_attestation import P5AttestationRegistry
from .plugin_identity import PLUGIN_ID, PLUGIN_VERSION, plugin_identity_snapshot
from .photo_generation_scope import PHOTO_GENERATION_SCOPE_LIMIT_KEYS
from .photo_reference_catalog import load_catalog, validate_and_serialize
from .proactive_chat_runtime_bridge import ProactiveChatRuntimeBridge
from .relationship_ledger import normalize_relationship_positive_stage_cap_key
from .relationship_affinity_runtime import normalize_group_allowlist
from .relationship_policy import (
    normalize_relationship_stage_policy,
    normalize_relationship_stage_provider_routes,
)
from .runtime_compat import probe_runtime_capabilities
from .migration_coordinator import MigrationCoordinator
from .wardrobe import (
    WARDROBE_MAX_ITEMS,
    WARDROBE_PROMPT_MAX_ITEMS,
    normalize_wardrobe_image_prompt,
    normalize_wardrobe_items,
    normalize_wardrobe_outfits,
)
from .migration_outbox import MigrationOutbox
from .model_routing import DEFAULT_SENSITIVE_REPLACEMENT_KEYWORDS, build_rules, normalize_scope
from .segmented_message import normalize_component_order, normalize_component_strategy
from .story_authority import story_startup_sync_operation
from .unified_person_registry import UnifiedPersonRegistry
from .logging_util import get_module_logger




def _initialize_proactive_and_reaction_config(self: Any, c: Any) -> None:
    self.proactive_history_max_chars = self._cfg_int(
        c,
        "proactive_history_max_chars",
        6000,
        500,
        20000,
    )
    self.enable_proactive_chat_integration = self._cfg_bool(c, "enable_proactive_chat_integration", True)
    self.enable_body_monitor_integration = self._cfg_bool(c, "enable_body_monitor_integration", False)
    self.proactive_chat_bridge_review_mode = self._cfg_str(
        c,
        "proactive_chat_bridge_review_mode",
        "local",
        "local",
    ).lower()
    if self.proactive_chat_bridge_review_mode not in {"local", "follow_proactive_review"}:
        self.proactive_chat_bridge_review_mode = "local"
    self.proactive_chat_bridge_collision_window_seconds = self._cfg_int(
        c,
        "proactive_chat_bridge_collision_window_seconds",
        90,
        10,
        600,
    )
    self.enable_llm_proactive_persona_judge = self._cfg_bool(c, "enable_llm_proactive_persona_judge", True)
    self.proactive_persona_judge_provider_id = self._cfg_str(c, "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID", "")
    self.proactive_persona_judge_send_threshold = self._cfg_int(c, "proactive_persona_judge_send_threshold", 62, 0, 100)
    self.proactive_persona_judge_cache_minutes = self._cfg_int(c, "proactive_persona_judge_cache_minutes", 180, 5, 720)
    self.proactive_persona_judge_max_daily = self._cfg_int(c, "proactive_persona_judge_max_daily", 12, 0, 100)
    self.enable_reaction_expression_experiment = self._cfg_bool(
        c, "enable_reaction_expression_experiment", False
    )
    self.reaction_expression_private_enabled = self._cfg_bool(
        c, "reaction_expression_private_enabled", True
    )
    self.reaction_expression_proactive_enabled = self._cfg_bool(
        c, "reaction_expression_proactive_enabled", True
    )
    self.reaction_expression_group_enabled = self._cfg_bool(
        c, "reaction_expression_group_enabled", False
    )
    self.reaction_expression_trigger_probability = self._cfg_unit_interval(
        c, "reaction_expression_trigger_probability", 0.2, 0.0
    )
    self.reaction_expression_cooldown_seconds = self._cfg_int(
        c, "reaction_expression_cooldown_seconds", 180, 0, 3600
    )
    self.reaction_expression_low_latency_mode = self._cfg_bool(
        c, "reaction_expression_low_latency_mode", True
    )
    self.reaction_expression_candidate_limit = self._cfg_int(
        c, "reaction_expression_candidate_limit", 6, 1, 16
    )
    self.reaction_expression_embedding_enabled = self._cfg_bool(
        c, "reaction_expression_embedding_enabled", False
    )
    self.embedding_provider_id = self._cfg_str(c, "EMBEDDING_PROVIDER_ID", "")
    self.reaction_expression_embedding_provider_id = self._cfg_str(
        c, "REACTION_EXPRESSION_EMBEDDING_PROVIDER_ID", ""
    )
    self.reaction_expression_embedding_timeout_ms = self._cfg_int(
        c, "reaction_expression_embedding_timeout_ms", 5000, 0, 30000
    )
    self.reaction_expression_embedding_candidate_limit = self._cfg_int(
        c, "reaction_expression_embedding_candidate_limit", 1200, 20, 5000
    )
    self.reaction_expression_embedding_score_threshold = self._cfg_unit_interval(
        c, "reaction_expression_embedding_score_threshold", 0.42, 0.0
    )
    self.reaction_expression_embedding_weight = self._cfg_float(
        c, "reaction_expression_embedding_weight", 0.55, 0.0
    )
    self.reaction_expression_embedding_backfill_enabled = self._cfg_bool(
        c, "reaction_expression_embedding_backfill_enabled", True
    )
    self.reaction_expression_embedding_backfill_batch_size = self._cfg_int(
        c, "reaction_expression_embedding_backfill_batch_size", 24, 1, 100
    )
    self.reaction_expression_embedding_backfill_interval_seconds = self._cfg_int(
        c, "reaction_expression_embedding_backfill_interval_seconds", 300, 0, 86400
    )
    self.reaction_expression_semantic_trigger_enabled = self._cfg_bool(
        c, "reaction_expression_semantic_trigger_enabled", True
    )
    self.reaction_expression_delivery_mode = self._cfg_str(
        c,
        "reaction_expression_delivery_mode",
        "separate_after",
        "separate_after",
    ).lower()
    if self.reaction_expression_delivery_mode not in {
        "separate_after",
        "same_message",
        "separate_before",
    }:
        self.reaction_expression_delivery_mode = "separate_after"
    self.reaction_expression_image_format = self._cfg_str(
        c,
        "reaction_expression_image_format",
        "image",
        "image",
    ).lower()
    if self.reaction_expression_image_format not in {"image", "qq_emoji"}:
        self.reaction_expression_image_format = "image"
    self.enable_maslow_motivation_experiment = self._cfg_bool(c, "enable_maslow_motivation_experiment", False)
    self.enable_maslow_schedule_influence = self._cfg_bool(c, "enable_maslow_schedule_influence", False)
    self.maslow_motivation_strength = self._cfg_int(c, "maslow_motivation_strength", 35, 0, 100)
    self.enable_personality_iteration_experiment = self._cfg_bool(c, "enable_personality_iteration_experiment", False)
    self.enable_personality_iteration_auto_tune = self._cfg_bool(c, "enable_personality_iteration_auto_tune", False)
    # 临时预约与动作查岗属于内建主动类别，不再由第二套功能开关控制。
    # 保留属性名供既有调度、转写和旧配置迁移代码兼容。
    self.enable_llm_timer_scheduling = True
    self.enable_proactive_decorating_hooks = self._cfg_bool(c, "enable_proactive_decorating_hooks", True)
    self.enable_precise_platform_send = self._cfg_bool(c, "enable_precise_platform_send", True)
    self.enable_proactive_quote_trigger_message = self._cfg_bool(c, "enable_proactive_quote_trigger_message", False)
    self.enable_quote_group_reply = self._cfg_bool(c, "enable_quote_group_reply", True)
    self.quote_group_reply_once_per_target = self._cfg_bool(c, "quote_group_reply_once_per_target", True)
    self.enable_quote_group_interjection = self._cfg_bool(c, "enable_quote_group_interjection", True)
    self.enable_quote_private_proactive = self._cfg_bool(c, "enable_quote_private_proactive", True)
    self.quote_skip_short_reply_chars = self._cfg_int(c, "quote_skip_short_reply_chars", 0, 0, 120)
    self.quote_target_strategy = self._cfg_str(c, "quote_target_strategy", "current", "current").lower()
    if self.quote_target_strategy not in {"current", "quoted", "auto"}:
        self.quote_target_strategy = "current"
    self._reply_component_style_cache: dict[str, tuple[str, str]] = {}
    self._group_reply_quote_target_cache: dict[str, dict[str, Any]] = {}
    self.enable_segmented_proactive_reply = self._cfg_bool(c, "enable_segmented_proactive_reply", False)
    # 被动戳一戳配置
    self.enable_reactive_poke = self._cfg_bool(c, "enable_reactive_poke", False)
    self.reactive_poke_trigger_probability = self._cfg_unit_interval(
        c, "reactive_poke_trigger_probability", 1.0
    )
    self.reactive_poke_normal_reply_probability = self._cfg_unit_interval(
        c, "reactive_poke_normal_reply_probability", 0.3
    )
    self.reactive_poke_back_probability = self._cfg_unit_interval(
        c, "reactive_poke_back_probability", 0.1
    )
    self.reactive_poke_super_poke_probability = self._cfg_unit_interval(
        c, "reactive_poke_super_poke_probability", 0.01
    )
    self.reactive_poke_back_times = self._cfg_int(c, "reactive_poke_back_times", 1, 1, 10)
    self.reactive_poke_super_poke_times = self._cfg_int(c, "reactive_poke_super_poke_times", 5, 1, 20)
    self.reactive_poke_interval = self._cfg_float(c, "reactive_poke_interval", 1.0, 0.1, 5.0)
    self.reactive_poke_normal_replies = self._cfg_raw(c, "reactive_poke_normal_replies", None)
    self.reactive_poke_prompts = self._cfg_raw(c, "reactive_poke_prompts", None)
    self.reactive_poke_back_prompts = self._cfg_raw(c, "reactive_poke_back_prompts", None)
    # The legacy switch remains the module master switch.  Rule-based
    # segmentation defaults on so existing configurations keep their behavior;
    # the LLM-controlled protocol is opt-in.
    self.enable_llm_controlled_segmenting = self._cfg_bool(
        c,
        "enable_llm_controlled_segmenting",
        False,
    )
    self.llm_controlled_segmenting_prompt = self._cfg_str(
        c,
        "llm_controlled_segmenting_prompt",
        "",
        "",
    )[:4000]
    self.enable_segmented_plugin_rules = self._cfg_bool(
        c,
        "enable_segmented_plugin_rules",
        True,
    )
    self.segmented_proactive_scope = self._cfg_str(c, "segmented_proactive_scope", "proactive_only", "proactive_only")
    if self.segmented_proactive_scope not in {"proactive_only", "all_llm"}:
        self.segmented_proactive_scope = "proactive_only"
    self.segmented_proactive_chat_scope = self._cfg_str(c, "segmented_proactive_chat_scope", "all", "all").lower()
    if self.segmented_proactive_chat_scope not in {"all", "private", "group"}:
        self.segmented_proactive_chat_scope = "all"
    self.segmented_proactive_threshold = self._cfg_int(c, "segmented_proactive_threshold", 500, 20, 1024)
    self.segmented_proactive_min_segment_chars = self._cfg_int(c, "segmented_proactive_min_segment_chars", 8, 1, 40)
    self.segmented_proactive_max_segments = self._cfg_int(c, "segmented_proactive_max_segments", 3, 1, 8)
    self.segmented_proactive_split_mode = self._cfg_str(c, "segmented_proactive_split_mode", "regex", "regex")
    if self.segmented_proactive_split_mode not in {"regex", "words"}:
        self.segmented_proactive_split_mode = "regex"
    self.enable_qq_official_segmented_reply = self._cfg_bool(
        c,
        "enable_qq_official_segmented_reply",
        False,
    )
    self.segmented_proactive_match_width_variants = self._cfg_bool(
        c,
        "segmented_proactive_match_width_variants",
        True,
    )
    self.segmented_proactive_regex = str(self._cfg_raw(c, "segmented_proactive_regex", r".*?[。？！~…\n]+|.+$"))
    split_words = self._cfg_raw(c, "segmented_proactive_split_words", ["。", "？", "！", "~", "…", "“"])
    self.segmented_proactive_split_words = [str(item) for item in split_words] if isinstance(split_words, list) else ["。", "？", "！", "~", "…", "“"]
    if "……" in self.segmented_proactive_split_words and "…" not in self.segmented_proactive_split_words:
        self.segmented_proactive_split_words.append("…")
    self.enable_segmented_proactive_content_cleanup = self._cfg_bool(c, "enable_segmented_proactive_content_cleanup", False)
    self.segmented_proactive_content_cleanup_scope = self._cfg_str(c, "segmented_proactive_content_cleanup_scope", "all", "all")
    if self.segmented_proactive_content_cleanup_scope not in {"all", "trailing"}:
        self.segmented_proactive_content_cleanup_scope = "all"
    self.segmented_proactive_content_cleanup_rule = str(self._cfg_raw(c, "segmented_proactive_content_cleanup_rule", r"[\n]"))
    cleanup_words = self._cfg_raw(c, "segmented_proactive_content_cleanup_words", ["\n"])
    self.segmented_proactive_content_cleanup_words = (
        [str(item) for item in cleanup_words if str(item) != ""]
        if isinstance(cleanup_words, list)
        else ["\n"]
    )
    self.enable_segmented_proactive_content_replacement = self._cfg_bool(
        c,
        "enable_segmented_proactive_content_replacement",
        False,
    )
    replacement_rules = self._cfg_raw(c, "segmented_proactive_content_replacements", [])
    if isinstance(replacement_rules, list):
        self.segmented_proactive_content_replacements = replacement_rules[:80]
    elif isinstance(replacement_rules, str):
        self.segmented_proactive_content_replacements = [
            line.strip()
            for line in replacement_rules.splitlines()
            if line.strip()
        ][:80]
    else:
        self.segmented_proactive_content_replacements = []
    self.segmented_proactive_interval_method = self._cfg_str(c, "segmented_proactive_interval_method", "log", "log")
    if self.segmented_proactive_interval_method not in {"random", "log"}:
        self.segmented_proactive_interval_method = "log"
    self.segmented_proactive_interval_min = self._cfg_float(c, "segmented_proactive_interval_min", 1.5, 0.1)
    self.segmented_proactive_interval_max = self._cfg_float(c, "segmented_proactive_interval_max", 3.5, 0.1)
    self.segmented_proactive_log_base = self._cfg_float(c, "segmented_proactive_log_base", 1.8, 1.1)
    self.segmented_proactive_send_as_forward = self._cfg_bool(c, "segmented_proactive_send_as_forward", False)
    self.enable_segmented_proactive_chat_profiles = self._cfg_bool(
        c,
        "enable_segmented_proactive_chat_profiles",
        False,
    )
    for chat_type in ("private", "group"):
        prefix = f"segmented_proactive_{chat_type}_"
        setattr(self, f"{prefix}enabled", self._cfg_bool(c, f"{prefix}enabled", True))
        profile_scope = self._cfg_str(c, f"{prefix}scope", self.segmented_proactive_scope, self.segmented_proactive_scope)
        if profile_scope not in {"proactive_only", "all_llm"}:
            profile_scope = self.segmented_proactive_scope
        setattr(self, f"{prefix}scope", profile_scope)
        setattr(
            self,
            f"{prefix}threshold",
            self._cfg_int(c, f"{prefix}threshold", self.segmented_proactive_threshold, 20, 1024),
        )
        setattr(
            self,
            f"{prefix}min_segment_chars",
            self._cfg_int(c, f"{prefix}min_segment_chars", self.segmented_proactive_min_segment_chars, 1, 40),
        )
        setattr(
            self,
            f"{prefix}max_segments",
            self._cfg_int(c, f"{prefix}max_segments", self.segmented_proactive_max_segments, 1, 8),
        )
        setattr(
            self,
            f"{prefix}send_as_forward",
            self._cfg_bool(c, f"{prefix}send_as_forward", self.segmented_proactive_send_as_forward),
        )
        interval_method = self._cfg_str(
            c,
            f"{prefix}interval_method",
            self.segmented_proactive_interval_method,
            self.segmented_proactive_interval_method,
        )
        if interval_method not in {"random", "log"}:
            interval_method = self.segmented_proactive_interval_method
        setattr(self, f"{prefix}interval_method", interval_method)
        interval_min = self._cfg_float(
            c,
            f"{prefix}interval_min",
            self.segmented_proactive_interval_min,
            0.1,
        )
        interval_max = self._cfg_float(
            c,
            f"{prefix}interval_max",
            self.segmented_proactive_interval_max,
            0.1,
        )
        setattr(self, f"{prefix}interval_min", interval_min)
        setattr(self, f"{prefix}interval_max", max(interval_min, interval_max))
        setattr(
            self,
            f"{prefix}log_base",
            self._cfg_float(c, f"{prefix}log_base", self.segmented_proactive_log_base, 1.1),
        )
    self.segmented_proactive_voice_strategy = normalize_component_strategy(
        self._cfg_str(c, "segmented_proactive_voice_strategy", "separate", "separate"),
        "separate",
    )
    self.segmented_proactive_image_strategy = normalize_component_strategy(
        self._cfg_str(c, "segmented_proactive_image_strategy", "separate", "separate"),
        "separate",
    )
    self.segmented_proactive_at_strategy = normalize_component_strategy(
        self._cfg_str(c, "segmented_proactive_at_strategy", "inline", "inline"),
        "inline",
    )
    self.segmented_proactive_face_strategy = normalize_component_strategy(
        self._cfg_str(c, "segmented_proactive_face_strategy", "inline", "inline"),
        "inline",
    )
    self.segmented_proactive_other_strategy = normalize_component_strategy(
        self._cfg_str(c, "segmented_proactive_other_strategy", "separate", "separate"),
        "separate",
    )
    self.segmented_proactive_component_order = normalize_component_order(
        self._cfg_raw(c, "segmented_proactive_component_order", [])
    )
    if self.segmented_proactive_interval_max < self.segmented_proactive_interval_min:
        self.segmented_proactive_interval_max = self.segmented_proactive_interval_min
    self.proactive_prompt_template = self._cfg_str(c, "proactive_prompt_template", "")
    self.max_proactive_plan_lag_minutes = self._cfg_int(c, "max_proactive_plan_lag_minutes", 180, 5, 1440)
    self._recent_inbound_message_debounce: dict[str, float] = {}
    self._semantic_message_buffers: dict[str, dict[str, Any]] = {}
    self._private_image_vision_handoffs: dict[Any, dict[str, Any]] = {}
    self.enable_detail_enhancement = self._cfg_bool(c, "enable_detail_enhancement", False)
    self.detail_enhancement_provider_id = self._cfg_str(c, "DETAIL_ENHANCEMENT_PROVIDER_ID", "")
    self.narration_provider_id = self._cfg_str(c, "NARRATION_PROVIDER_ID", "")
    self.photo_prompt_provider_id = self._cfg_str(c, "PHOTO_PROMPT_PROVIDER_ID", "")
    self.comfyui_photo_workflow_name = self._cfg_str(c, "COMFYUI_PHOTO_WORKFLOW_NAME", "")
    self.comfyui_text2img_workflow_name = self._cfg_str(c, "COMFYUI_TEXT2IMG_WORKFLOW_NAME", self.comfyui_photo_workflow_name)
    self.comfyui_selfie_workflow_name = self._cfg_str(c, "COMFYUI_SELFIE_WORKFLOW_NAME", self.comfyui_photo_workflow_name)
    self.photo_persona_reference_image_path = self._cfg_str(c, "photo_persona_reference_image_path", "")
    raw_reference_library = self._cfg_raw(c, "photo_reference_library", [])
    if isinstance(raw_reference_library, list):
        self.photo_reference_library = [
            (dict(item) if isinstance(item, dict) else str(item).strip())
            for item in raw_reference_library
            if (isinstance(item, dict) and bool(item)) or str(item or "").strip()
        ][:24]
    else:
        self.photo_reference_library = [
            line.strip() for line in str(raw_reference_library or "").splitlines() if line.strip()
        ][:24]
    self.enable_p5_structured_reference_assets = self._cfg_bool(
        c,
        "enable_p5_structured_reference_assets",
        False,
    )
    raw_structured_assets = self._cfg_raw(c, "photo_structured_reference_assets", [])
    self.photo_structured_reference_assets = (
        [dict(item) for item in raw_structured_assets if isinstance(item, dict)][:16]
        if isinstance(raw_structured_assets, list)
        else []
    )
    self.enable_owned_reaction_asset_workbench = self._cfg_bool(
        c,
        "enable_owned_reaction_asset_workbench",
        False,
    )
    raw_owned_reaction_assets = self._cfg_raw(c, "owned_reaction_assets", [])
    self.owned_reaction_assets = (
        [dict(item) for item in raw_owned_reaction_assets if isinstance(item, dict)][:96]
        if isinstance(raw_owned_reaction_assets, list)
        else []
    )
    self.comfyui_photo_wait_seconds = self._cfg_int(c, "comfyui_photo_wait_seconds", 90, 5, 600)
    self.photo_generation_backend = self._cfg_str(c, "photo_generation_backend", "auto", "auto").strip().lower()
    if self.photo_generation_backend not in {"auto", "comfyui", "sdgen", "external", "tool_call", "nai", "anima_master"}:
        self.photo_generation_backend = "auto"
    self.enable_generated_photo_cleanup = self._cfg_bool(c, "enable_generated_photo_cleanup", True)
    self.generated_photo_retention_days = self._cfg_int(c, "generated_photo_retention_days", 30, 0, 3650)
    self.generated_photo_max_mb = self._cfg_int(c, "generated_photo_max_mb", 512, 0, 10240)
    self._last_generated_photo_cleanup_ts = 0.0
    self.custom_photo_tool_name = self._cfg_str(c, "custom_photo_tool_name", "", "").strip()
    self.custom_photo_tool_prompt_param = self._cfg_str(c, "custom_photo_tool_prompt_param", "prompt", "prompt").strip() or "prompt"
    self.custom_photo_tool_kind_param = self._cfg_str(c, "custom_photo_tool_kind_param", "", "").strip()
    self.custom_photo_tool_reference_param = self._cfg_str(c, "custom_photo_tool_reference_param", "", "").strip()
    self.custom_photo_tool_extra_params = self._cfg_str(c, "custom_photo_tool_extra_params", "", "").strip()
    self.enable_local_photo_load_guard = self._cfg_bool(c, "enable_local_photo_load_guard", True)
    self.local_photo_cpu_busy_percent = self._cfg_int(c, "local_photo_cpu_busy_percent", 85, 1, 100)
    self.local_photo_memory_busy_percent = self._cfg_int(c, "local_photo_memory_busy_percent", 88, 1, 100)
    self.local_photo_defer_minutes = self._cfg_int(c, "local_photo_defer_minutes", 30, 1, 240)
    self._local_photo_load_cache: dict[str, Any] = {}
    # Raw legacy values are exposed only for Image Companion migration and old
    # diagnostics. The host no longer normalizes or executes image backends.
    self.external_image_api_platform = self._cfg_str(
        c, "external_image_api_platform", "auto", "auto"
    ).strip().lower()
    self.external_image_api_base_url = self._cfg_str(c, "EXTERNAL_IMAGE_API_BASE_URL", "")
    self.external_image_api_key = self._cfg_str(c, "EXTERNAL_IMAGE_API_KEY", "")
    self.external_image_api_model = self._cfg_str(c, "EXTERNAL_IMAGE_API_MODEL", "")
    self.external_image_api_size = self._cfg_str(c, "external_image_api_size", "1024x1024", "1024x1024")
    self.external_image_api_timeout_seconds = self._cfg_int(c, "external_image_api_timeout_seconds", 180, 20, 600)
    self.external_image_api_custom_headers = self._cfg_str(c, "external_image_api_custom_headers", "")
    self.external_image_download_proxy = self._cfg_str(c, "external_image_download_proxy", "").strip()
    self.external_image_download_use_environment_proxy = self._cfg_bool(
        c,
        "external_image_download_use_environment_proxy",
        False,
    )
    self.proactive_dedup_enabled = self._cfg_bool(c, "proactive_dedup_enabled", True)
    self.proactive_dedup_policies = self._cfg_str(
        c,
        "proactive_dedup_policies",
        "semantic,content_fingerprint,life_event",
        "semantic,content_fingerprint,life_event",
    )
    self.proactive_dedup_sent_window_minutes = self._cfg_int(
        c, "proactive_dedup_sent_window_minutes", 240, 0, 1440
    )
    self.proactive_dedup_last_message_window_minutes = self._cfg_int(
        c, "proactive_dedup_last_message_window_minutes", 240, 0, 1440
    )
    self.proactive_dedup_last_message_enabled = self._cfg_bool(
        c, "proactive_dedup_last_message_enabled", True
    )
    self.proactive_dedup_weather_window_minutes = self._cfg_int(
        c, "proactive_dedup_weather_window_minutes", 1080, 0, 2880
    )
    self.proactive_dedup_min_shared_tokens = self._cfg_int(
        c, "proactive_dedup_min_shared_tokens", 1, 1, 4
    )
    self.proactive_dedup_min_overlap_ratio = min(
        1.0,
        max(0.0, self._cfg_float(c, "proactive_dedup_min_overlap_ratio", 0.0, 0.0)),
    )
