# -*- coding: utf-8 -*-
"""plugin_bootstrap 拆分件 part01：插件引导初始化（机械搬移，行为不变）。

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
from .plugin_bootstrap_shared import (
    _LEGACY_MULTI_PERSONA_PRIMARY_KEY,
    _LEGACY_PHOTO_SCENE_PRESET_NAMES,
    logger,
)



def _config_root_mapping(config: Any) -> dict[str, Any] | None:
    if isinstance(config, dict):
        return config
    for attr in ("data", "config"):
        value = getattr(config, attr, None)
        if isinstance(value, dict):
            return value
    return None


def _remove_legacy_multi_persona_primary_from_runtime_config(config: Any) -> bool:
    """Remove the retired key only when existing persistence can save the change."""
    if not any(callable(getattr(config, name, None)) for name in ("save_config", "save", "save_conf")):
        return False
    root = _config_root_mapping(config)
    if root is None:
        return False
    removed = False

    def remove_from(mapping: dict[str, Any]) -> None:
        nonlocal removed
        for value in tuple(mapping.values()):
            if isinstance(value, dict):
                remove_from(value)
        if _LEGACY_MULTI_PERSONA_PRIMARY_KEY in mapping:
            mapping.pop(_LEGACY_MULTI_PERSONA_PRIMARY_KEY, None)
            removed = True

    remove_from(root)
    return removed


def _initialize_primary_persona_config(self: Any, config: Any) -> bool:
    """Resolve the sole primary persona and retain the retired value as diagnostics."""
    authoritative = self._sanitize_persona_id(
        self._cfg_str(config, "plugin_specific_persona_id", "", "")
    )
    legacy_candidate = self._sanitize_persona_id(
        self._cfg_str(config, _LEGACY_MULTI_PERSONA_PRIMARY_KEY, "", "")
    )
    self.plugin_specific_persona_id = authoritative
    self._legacy_multi_persona_primary_id_candidate = legacy_candidate
    self._multi_persona_primary_id_mismatch = (
        {
            "authoritative": authoritative,
            "legacy_candidate": legacy_candidate,
        }
        if authoritative and legacy_candidate and authoritative != legacy_candidate
        else {}
    )
    self._multi_persona_primary_requires_configuration = bool(
        getattr(self, "enable_multi_persona_mode", False) and not authoritative
    )

    if self._multi_persona_primary_id_mismatch:
        logger.warning(
            "检测到旧主人格配置不一致，继续以 plugin_specific_persona_id 为权威: "
            "authoritative=%s legacy_candidate=%s",
            authoritative,
            legacy_candidate,
        )
    elif self._multi_persona_primary_requires_configuration and legacy_candidate:
        logger.warning(
            "多人格模式缺少 plugin_specific_persona_id；旧 multi_persona_primary_id "
            "仅保留为待确认候选，不会静默启用: legacy_candidate=%s",
            legacy_candidate,
        )

    cleanup_pending = bool(authoritative) and _remove_legacy_multi_persona_primary_from_runtime_config(
        config
    )
    self._legacy_multi_persona_primary_id_cleanup_pending = cleanup_pending
    return cleanup_pending


def _validate_primary_persona_runtime(self: Any) -> bool:
    """Keep multi-persona disabled when AstrBot no longer has its primary."""
    primary = self._sanitize_persona_id(
        getattr(self, "plugin_specific_persona_id", "")
    )
    invalid = False
    if bool(getattr(self, "_multi_persona_enable_requested", False)) and primary:
        checker = getattr(self, "_astrbot_persona_exists", None)
        invalid = bool(callable(checker) and not checker(primary))
    self._multi_persona_primary_invalid = invalid
    if invalid:
        self.enable_multi_persona_mode = False
        logger.warning(
            "多人格主人格已不在 AstrBot 人格列表中，运行态保持关闭等待修复: persona=%s",
            primary,
        )
    return invalid


def _legacy_photo_scene_preset_names(raw: Any) -> set[str]:
    """Read preset names only for validating legacy catalog migration."""
    names = set(_LEGACY_PHOTO_SCENE_PRESET_NAMES)
    if isinstance(raw, dict):
        values = raw.keys()
    elif isinstance(raw, list):
        values = [
            item.get("name") or item.get("key") or item.get("title")
            if isinstance(item, dict)
            else str(item or "").split("：", 1)[0].split(":", 1)[0]
            for item in raw
        ]
    else:
        values = [
            line.split("：", 1)[0].split(":", 1)[0]
            for line in str(raw or "").replace("\r", "\n").split("\n")
        ]
    names.update(_single_line(value, 40) for value in values if _single_line(value, 40))
    return names


def initialize_plugin_entrypoint_state(
    self: Any,
    context: Any,
    config: Any,
    *,
    extension_api_factory: Any,
) -> None:
    self.extension_api = extension_api_factory(self)
    self._persistence_owner_token = str(
        getattr(self.extension_api, "_story_migration_generation", "") or f"instance-{id(self)}"
    )
    self._external_proactive_abilities: dict[str, dict[str, Any]] = {}
    self._external_realtime_activities: dict[str, dict[str, Any]] = {}
    # Short-lived continuity from realtime extensions. This is deliberately
    # separate from long-term memory: it preserves the immediate thread while
    # allowing stale call details to disappear automatically.
    self._external_realtime_continuity: dict[str, dict[str, Any]] = {}
    self.config = config
    self.plugin_identity = plugin_identity_snapshot()
    self.runtime_capabilities = probe_runtime_capabilities(
        context=context,
        plugin_name=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
    )
    contract_issues = tuple(contract_self_check())
    self.bot_personal_capabilities = capability_descriptor(available=not contract_issues, read_only=False)
    self.bot_personal_capabilities.update(
        {
            "state": "ready" if not contract_issues else "degraded",
            "degraded": bool(contract_issues),
            "warnings": list(contract_issues),
        }
    )
    if contract_issues:
        logger.warning("Bot Personal contract self-check degraded: %s", ";".join(contract_issues))

def _initialize_core_and_relationship_config(self: Any, c: Any) -> None:
    self.data_dir = StarTools.get_data_dir(PLUGIN_NAME)
    os.makedirs(self.data_dir, exist_ok=True)
    self.data_file = os.path.join(self.data_dir, "companions.json")
    # HDSI is opt-in; legacy remains the default and unlisted scopes never
    # enter the compatibility prompt path.
    self.hdsi_experiment_mode = self._cfg_str(c, "hdsi_experiment_mode", "legacy", "legacy").strip().lower()
    if self.hdsi_experiment_mode not in {"legacy", "hdsi_shadow", "hdsi_active"}:
        self.hdsi_experiment_mode = "legacy"
    self.hdsi_experiment_continuity_scope = normalize_continuity_scope(
        self._cfg_str(c, "hdsi_experiment_continuity_scope", "global", "global")
    )
    def _hdsi_ids(value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            values = value.replace("\r", "\n").replace(",", "\n").replace("，", "\n").split("\n")
        elif isinstance(value, (list, tuple, set, frozenset)):
            values = value
        else:
            values = ()
        return tuple(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))
    self.hdsi_experiment_user_ids = _hdsi_ids(self._cfg_raw(c, "hdsi_experiment_user_ids", []))
    self.hdsi_experiment_group_ids = _hdsi_ids(self._cfg_raw(c, "hdsi_experiment_group_ids", []))
    self.hdsi_experiment_binding_revision = self._cfg_str(c, "hdsi_experiment_binding_revision", "1", "1").strip() or "1"
    self.hdsi_experiment_window_modes = normalize_window_modes(
        self._cfg_raw(c, "hdsi_experiment_window_modes", "{}")
    )
    self.enable_multi_persona_mode = self._cfg_bool(c, "enable_multi_persona_mode", False)
    self._multi_persona_enable_requested = self.enable_multi_persona_mode
    legacy_primary_cleanup_pending = _initialize_primary_persona_config(self, c)
    if self._multi_persona_primary_requires_configuration:
        # Keep runtime in single-persona mode until the WebUI transaction
        # installs and validates an authoritative primary persona.
        self.enable_multi_persona_mode = False
    _validate_primary_persona_runtime(self)
    self.multi_persona_ids = self._configured_multi_persona_ids()
    self._persona_profiles_dir = os.path.join(self.data_dir, "persona_profiles")
    self._persona_data_profiles: dict[str, dict[str, Any]] = {}
    self._persona_profile_errors: dict[str, str] = {}
    self._page_current_persona_id = self.plugin_specific_persona_id
    self.storage_backend = self._cfg_str(c, "storage_backend", "json", "json").strip().lower() or "json"
    if self.storage_backend not in {"json", "sqlite"}:
        self.storage_backend = "json"
    self.storage_sqlite_path = self._cfg_str(c, "storage_sqlite_path", "", "")
    self.enable_standalone_webui = self._cfg_bool(c, "enable_standalone_webui", False)
    self.standalone_webui_host = self._cfg_str(
        c, "standalone_webui_host", "127.0.0.1", "127.0.0.1"
    )
    self.standalone_webui_port = self._cfg_int(c, "standalone_webui_port", 6190, 1, 65535)
    self.standalone_webui_access_token = self._cfg_str(
        c, "standalone_webui_access_token", "", ""
    )
    self.standalone_webui_session_ttl_hours = self._cfg_int(
        c, "standalone_webui_session_ttl_hours", 24, 1, 168
    )
    self.enable_store_control_tag_sanitization = self._cfg_bool(
        c, "enable_store_control_tag_sanitization", True
    )
    self.enable_outbound_secret_redaction = self._cfg_bool(
        c, "enable_outbound_secret_redaction", True
    )
    trusted_domains = self._cfg_raw(c, "outbound_secret_redaction_trusted_domains", [])
    self.outbound_secret_redaction_trusted_domains = [
        str(item).strip().lower().rstrip(".")
        for item in (trusted_domains if isinstance(trusted_domains, (list, tuple, set)) else [])
        if str(item).strip()
    ][:100]
    self._rebuild_store_manager()
    config_migration_started = time.perf_counter()
    self._startup_config_migration_changes = migrate_flat_config_into_schema_groups(
        c,
        schema_path=Path(__file__).with_name("_conf_schema.json"),
        logger=logger,
        save=False,
    )
    if legacy_primary_cleanup_pending:
        self._startup_config_migration_changes += 1
    config_migration_elapsed_ms = int((time.perf_counter() - config_migration_started) * 1000)
    if config_migration_elapsed_ms > 1200:
        logger.warning(
            "启动配置迁移耗时较高: elapsed=%sms changes=%s",
            config_migration_elapsed_ms,
            self._startup_config_migration_changes,
        )

    legacy_enabled_value = self._cfg_raw(c, "enabled", None)
    if isinstance(legacy_enabled_value, str):
        self._legacy_enabled_config_disabled = legacy_enabled_value.strip().lower() in {
            "false", "0", "no", "n", "off", "disable", "disabled", "停用", "关闭", "关", "否", "",
        }
    else:
        self._legacy_enabled_config_disabled = legacy_enabled_value is False
    # 插件启停只交给 AstrBot 官方插件开关；旧版配置里的 enabled 已废弃，避免残留 false 误关整套链路。
    self.enabled = True
    self.enable_proactive_only_mode = self._cfg_bool(c, "enable_proactive_only_mode", False)
    self.proactive_intensity_preset = self._normalize_proactive_intensity_preset(
        self._cfg_str(c, "proactive_intensity_preset", "off", "off")
    )
    self.proactive_preempt_queue_enabled = self._cfg_bool(c, "proactive_preempt_queue_enabled", False)
    self.proactive_preempt_queue_expire_hours = self._cfg_int(c, "proactive_preempt_queue_expire_hours", 2, 1, 24)
    self.proactive_closing_grace_minutes = self._cfg_int(c, "proactive_closing_grace_minutes", 45, 0, 240)
    self.proactive_share_priority = self._cfg_int(c, "proactive_share_priority", 48, 0, 100)
    self.enable_experimental_motivation_model = self._cfg_bool(c, "enable_experimental_motivation_model", False)
    self.check_interval_seconds = self._cfg_int(c, "check_interval_seconds", 60, 30)
    self.idle_minutes = self._cfg_int(c, "idle_minutes", 60, 5)
    self.min_interval_minutes = self._cfg_int(c, "min_interval_minutes", 120, 10)
    self.enable_proactive_burst = self._cfg_bool(c, "enable_proactive_burst", False)
    self.proactive_burst_max_messages = self._cfg_int(c, "proactive_burst_max_messages", 2, 2, 3)
    self.proactive_burst_gap_min_seconds = self._cfg_int(c, "proactive_burst_gap_min_seconds", 45, 10, 600)
    self.proactive_burst_gap_max_seconds = self._cfg_int(c, "proactive_burst_gap_max_seconds", 180, 20, 900)
    if self.proactive_burst_gap_max_seconds < self.proactive_burst_gap_min_seconds:
        self.proactive_burst_gap_max_seconds = self.proactive_burst_gap_min_seconds
    self.proactive_hour_activity_curve = self._cfg_str(
        c,
        "proactive_hour_activity_curve",
        "0.22,0.16,0.12,0.10,0.10,0.14,0.28,0.50,0.66,0.72,0.78,0.92,1.0,0.94,0.82,0.74,0.78,0.92,1.0,0.98,0.88,0.70,0.48,0.32",
    )
    self.proactive_unanswered_slowdown_start = self._cfg_int(c, "proactive_unanswered_slowdown_start", 1, 1, 10)
    self.proactive_unanswered_pause_after = self._cfg_int(
        c,
        "proactive_unanswered_pause_after",
        0,
        0,
        50,
    )
    self.proactive_unanswered_max_interval_multiplier = min(
        8.0,
        max(1.0, self._cfg_float(c, "proactive_unanswered_max_interval_multiplier", 2.2, 1.0)),
    )
    self.friend_unanswered_max_cooldown_hours = min(
        168.0,
        max(1.0, self._cfg_float(c, "friend_unanswered_max_cooldown_hours", 60.0, 1.0)),
    )
    self.timer_pre_silence_minutes = self._cfg_int(c, "timer_pre_silence_minutes", 20, 0, 240)
    self.max_daily_messages = self._cfg_int(c, "max_daily_messages", 8, 0, 25)
    self.enable_reply_interception_forward = self._cfg_bool(c, "enable_reply_interception_forward", False)
    self.reply_interception_forward_target_umo = self._cfg_str(c, "reply_interception_forward_target_umo", "")
    self.reply_interception_forward_plugin_blocks = self._cfg_bool(c, "reply_interception_forward_plugin_blocks", True)
    self.reply_interception_forward_rewrites = self._cfg_bool(c, "reply_interception_forward_rewrites", True)
    self.reply_interception_forward_proactive_blocks = self._cfg_bool(c, "reply_interception_forward_proactive_blocks", True)
    self.enable_balance_awareness = self._cfg_bool(c, "enable_balance_awareness", False)
    self.balance_api_url = self._cfg_str(c, "balance_api_url", "")
    self.balance_api_key = self._cfg_str(c, "balance_api_key", "")
    self.balance_api_auth_header = self._cfg_str(c, "balance_api_auth_header", "Authorization", "Authorization")
    self.balance_api_auth_scheme = str(self._cfg_raw(c, "balance_api_auth_scheme", "Bearer") or "").strip()
    self.balance_api_custom_headers = self._cfg_str(c, "balance_api_custom_headers", "")
    self.balance_json_path = self._cfg_str(c, "balance_json_path", "")
    self.balance_total_json_path = self._cfg_str(c, "balance_total_json_path", "")
    self.balance_used_json_path = self._cfg_str(c, "balance_used_json_path", "")
    self.balance_value_divisor = self._cfg_float(c, "balance_value_divisor", 1.0, 0.000000000001)
    self.balance_currency_label = self._cfg_str(c, "balance_currency_label", "元", "元")
    self.balance_check_interval_minutes = self._cfg_float(c, "balance_check_interval_minutes", 60.0, 5.0)
    self.balance_request_timeout_seconds = self._cfg_float(c, "balance_request_timeout_seconds", 10.0, 2.0)
    self.balance_low_threshold = self._cfg_float(c, "balance_low_threshold", 10.0, 0.0)
    self.balance_critical_threshold = min(
        self.balance_low_threshold,
        self._cfg_float(c, "balance_critical_threshold", 3.0, 0.0),
    )
    self.balance_low_percent_threshold = self._cfg_float(c, "balance_low_percent_threshold", 15.0, 0.0)
    self.balance_critical_percent_threshold = min(
        self.balance_low_percent_threshold,
        self._cfg_float(c, "balance_critical_percent_threshold", 5.0, 0.0),
    )
    self.balance_message_cooldown_hours = self._cfg_float(c, "balance_message_cooldown_hours", 24.0, 1.0)
    self.balance_include_amount_in_message = self._cfg_bool(c, "balance_include_amount_in_message", True)
    self.inbound_message_debounce_seconds = self._cfg_float(c, "inbound_message_debounce_seconds", 3.0, 0.0)
    self.enable_recall_enhancement = self._cfg_bool(c, "enable_recall_enhancement", True)
    self.enable_recall_cancel_reply = self._cfg_bool(c, "enable_recall_cancel_reply", self.enable_recall_enhancement)
    self.enable_recall_message_cache = self._cfg_bool(c, "enable_recall_message_cache", True)
    self.enable_recall_transcribe_command = self._cfg_bool(c, "enable_recall_transcribe_command", True)
    self.recall_message_cache_ttl_seconds = self._cfg_float(c, "recall_message_cache_ttl_seconds", 600.0, 60.0)
    self.recall_message_cache_max_items = self._cfg_int(c, "recall_message_cache_max_items", 300, 0, 3000)
    self.recall_message_image_cache_max_mb = self._cfg_float(c, "recall_message_image_cache_max_mb", 256.0, 0.0)
    self.recall_message_cache_text_chars = self._cfg_int(c, "recall_message_cache_text_chars", 500, 80, 2000)
    self.recall_cancel_reply_ttl_seconds = self.recall_message_cache_ttl_seconds
    self.enable_forbidden_word_recall = self._cfg_bool(c, "enable_forbidden_word_recall", False)
    self.recall_forbidden_words = self._parse_text_list_config(self._cfg_raw(c, "recall_forbidden_words", []), limit=300)
    self.recall_forbidden_word_case_sensitive = self._cfg_bool(c, "recall_forbidden_word_case_sensitive", False)
    self.recall_forbidden_scope = self._cfg_str(c, "recall_forbidden_scope", "bot_and_group", "bot_and_group").lower()
    if self.recall_forbidden_scope not in {"bot_only", "group_only", "bot_and_group"}:
        self.recall_forbidden_scope = "bot_and_group"
    self._recalled_message_ids: dict[str, dict[str, Any]] = {}
    self._recall_message_cache: dict[str, dict[str, Any]] = {}
    self._recent_outbound_text_guard: dict[str, dict[str, Any]] = {}
    self.enable_message_debounce = self._cfg_bool(
        c,
        "enable_message_debounce",
        self._cfg_bool(c, "enable_semantic_message_debounce", True),
    )
    self.enable_semantic_message_debounce = self.enable_message_debounce
    self.enable_smart_message_debounce = self._cfg_bool(c, "enable_smart_message_debounce", False)
    self.smart_message_debounce_provider_id = self._cfg_str(c, "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID", "")
    self.smart_message_debounce_wait_seconds = self._cfg_float(c, "smart_message_debounce_wait_seconds", 3.0, 0.0)
    self.smart_message_debounce_model_timeout_seconds = self._cfg_float(c, "smart_message_debounce_model_timeout_seconds", 0.8, 0.2)
    self.smart_message_debounce_learning_window_seconds = self._cfg_float(c, "smart_message_debounce_learning_window_seconds", 8.0, 1.0)
    self.smart_message_debounce_examples_limit = self._cfg_int(c, "smart_message_debounce_examples_limit", 8, 0, 30)
    legacy_semantic_debounce_seconds = self._cfg_float(c, "semantic_message_debounce_seconds", 8.0, 0.0)
    text_debounce_raw = self._cfg_raw(c, "text_message_debounce_seconds", None)
    text_debounce_default = legacy_semantic_debounce_seconds if text_debounce_raw in (None, "") else 0.0
    self.text_message_debounce_seconds = self._cfg_float(c, "text_message_debounce_seconds", text_debounce_default, 0.0)
    self.image_message_debounce_seconds = self._cfg_float(c, "image_message_debounce_seconds", 8.0, 0.0)
    self.forward_message_debounce_seconds = self._cfg_float(c, "forward_message_debounce_seconds", 0.0, 0.0)
    self.text_message_debounce_max_wait_seconds = self._cfg_float(c, "text_message_debounce_max_wait_seconds", 12.0, 0.0)
    self.message_debounce_max_merge_messages = self._cfg_int(c, "message_debounce_max_merge_messages", 8, 0, 30)
    self.semantic_message_debounce_seconds = self.text_message_debounce_seconds
    self.private_image_vision_wait_seconds = self._cfg_float(c, "private_image_vision_wait_seconds", 30.0, 0.0, 600.0)
    self.private_image_provider_timeout_seconds = self._cfg_float(c, "private_image_provider_timeout_seconds", 12.0, 0.0, 600.0)
    self.private_image_provider_failure_cooldown_seconds = self._cfg_float(
        c,
        "private_image_provider_failure_cooldown_seconds",
        0.0,
        0.0,
        3600.0,
    )
    self.private_image_vision_provider_priority = self._normalize_private_image_vision_provider_priority(
        self._cfg_str(c, "private_image_vision_provider_priority", "astrbot_first")
    )
    self.private_image_vision_custom_prompt = self._cfg_str(c, "private_image_vision_custom_prompt", "")[:12000]
    self.private_image_vision_max_chars = self._cfg_int(c, "private_image_vision_max_chars", 2400, 300, 12000)
    self.enable_private_image_self_recognition = self._cfg_bool(c, "enable_private_image_self_recognition", True)
    self.enable_private_image_vision_cache = self._cfg_bool(c, "enable_private_image_vision_cache", True)
    self.private_image_vision_cache_max_items = self._cfg_int(c, "private_image_vision_cache_max_items", 300, 0, 3000)
    self.enable_group_image_understanding = self._cfg_bool(c, "enable_group_image_understanding", False)
    self.enable_group_image_wakeup = self._cfg_bool(c, "enable_group_image_wakeup", False)
    self.group_image_vision_wait_seconds = self._cfg_float(c, "group_image_vision_wait_seconds", 8.0, 0.0, 60.0)
    self.group_image_max_images = self._cfg_int(c, "group_image_max_images", 4, 0, 12)
    self.enable_context_image_captioning = self._cfg_bool(c, "enable_context_image_captioning", True)
    self.context_image_caption_max_items = self._cfg_int(c, "context_image_caption_max_items", 12, 0, 50)
    self.context_image_caption_timeout_seconds = self._cfg_float(c, "context_image_caption_timeout_seconds", 8.0, 0.0, 600.0)
    self.enable_private_image_gif_enhancement = self._cfg_bool(c, "enable_private_image_gif_enhancement", True)
    self.private_image_gif_max_frames = self._cfg_int(c, "private_image_gif_max_frames", 4, 1, 8)
    self.enable_group_conversation_followup = self._cfg_bool(c, "enable_group_conversation_followup", True)
    self.group_conversation_followup_seconds = self._cfg_int(c, "group_conversation_followup_seconds", 120, 0, 600)
    self.group_conversation_followup_max_turns = self._cfg_int(c, "group_conversation_followup_max_turns", 1, 0, 10)
    self.enable_group_air_reply_guard = self._cfg_bool(c, "enable_group_air_reply_guard", True)
    self.group_air_guard_window_seconds = self._cfg_int(c, "group_air_guard_window_seconds", 180, 30, 1800)
    self.group_air_guard_max_bot_replies = self._cfg_int(c, "group_air_guard_max_bot_replies", 3, 1, 20)
    self.group_air_guard_polite_loop_limit = self._cfg_int(c, "group_air_guard_polite_loop_limit", 2, 1, 10)
    self.quiet_hours = self._cfg_str(c, "quiet_hours", "23:00-08:30")
    self.default_style = self._cfg_str(c, "default_style", "温柔", "温柔")
    reply_style_raw = _flat_get(c, "reply_style_prompt", None)
    self.reply_style_prompt = DEFAULT_REPLY_STYLE_PROMPT if reply_style_raw is None else str(reply_style_raw).strip()
    self.enable_persona_voice_channels = self._cfg_bool(c, "enable_persona_voice_channels", True)
    self.persona_conversation_voice_prompt = self._cfg_str(c, "persona_conversation_voice_prompt", "")
    self.persona_creative_voice_prompt = self._cfg_str(c, "persona_creative_voice_prompt", "")
    self.persona_planning_voice_prompt = self._cfg_str(c, "persona_planning_voice_prompt", "")
    self.persona_inner_voice_prompt = self._cfg_str(c, "persona_inner_voice_prompt", "")
    self.persona_proactive_voice_prompt = self._cfg_str(c, "persona_proactive_voice_prompt", "")
    self.worldview_adaptation_mode = self._cfg_str(c, "worldview_adaptation_mode", "auto", "auto")
    if self.worldview_adaptation_mode not in {"auto", "modern", "fantasy", "sci_fi", "custom", "off"}:
        self.worldview_adaptation_mode = "auto"
    self.worldview_adaptation_prompt = self._cfg_str(c, "worldview_adaptation_prompt", "")
    self.default_nickname = self._cfg_str(c, "default_nickname", "你", "你")
    self.enable_auto_user_profile_creation = self._cfg_bool(c, "enable_auto_user_profile_creation", True)
    self.auto_profile_platforms = self._cfg_raw(
        c,
        "auto_profile_platforms",
        ["onebot", "qq_official", "telegram", "webchat", "generic"],
    )
    self.default_nickname_strategy = self._cfg_str(
        c,
        "default_nickname_strategy",
        "platform_display_name",
    )
    if self.default_nickname_strategy not in {"platform_display_name", "fixed", "user_id"}:
        self.default_nickname_strategy = "platform_display_name"
    self.default_proactive_enabled = self._cfg_bool(c, "default_proactive_enabled", False)
    self.default_proactive_daily_limit = self._cfg_int(c, "default_proactive_daily_limit", 0, 0, 30)
    self.portrait_global_mode = self._cfg_str(c, "portrait_global_mode", "learn_and_use", "learn_and_use")
    if self.portrait_global_mode not in {"disabled", "use_existing", "learn_and_use"}:
        self.portrait_global_mode = "learn_and_use"
    self.require_private_opt_in = self._cfg_bool(c, "require_private_opt_in", True)
    self.target_user_ids = self._cfg_raw(c, "target_user_ids", [])
    self.private_user_aliases = self._parse_private_user_aliases(self._cfg_raw(c, "private_user_aliases", ""))
    self.private_user_delivery_aliases = self._parse_private_user_aliases(self._cfg_raw(c, "private_user_delivery_aliases", ""))
    self._load_tts_enhancement_config(c)
    # Reality Companion owns all device runtime settings. Historical values
    # stay in ``self.config`` only so its migration API can import them.
    self.target_platform = self._cfg_str(c, "target_platform", "aiocqhttp", "aiocqhttp")
    self.default_enable_configured_targets = self._cfg_bool(c, "default_enable_configured_targets", True)
    self.default_interaction_band = self._cfg_str(c, "default_interaction_band", "relaxed")
    if self.default_interaction_band not in {"avoidant", "hurt", "relaxed", "lively", "warm"}:
        self.default_interaction_band = "relaxed"
    self.enable_custom_relationship_stage_policy = self._cfg_bool(c, "enable_custom_relationship_stage_policy", True)
    self.relationship_stage_policy = normalize_relationship_stage_policy(
        self._cfg_raw(c, "relationship_stage_policy", [])
    )
    self.enable_relationship_stage_provider_routing = self._cfg_bool(
        c,
        "enable_relationship_stage_provider_routing",
        False,
    )
    self.relationship_stage_provider_routes = normalize_relationship_stage_provider_routes(
        self._cfg_raw(c, "relationship_stage_provider_routes", {})
    )
    self.relationship_positive_stage_cap_key = normalize_relationship_positive_stage_cap_key(
        self._cfg_raw(c, "relationship_positive_stage_cap_key", "close")
    )
    self.normal_interaction_band_cap = self._cfg_str(c, "normal_interaction_band_cap", "warm")
    if self.normal_interaction_band_cap not in {"relaxed", "lively", "warm"}:
        self.normal_interaction_band_cap = "warm"
    self.owner_group_relationship_projection = self._cfg_bool(c, "owner_group_relationship_projection", True)
    self.owner_group_interaction_projection = self._cfg_bool(c, "owner_group_interaction_projection", True)
    self.enable_relationship_content_tiers = self._cfg_bool(c, "enable_relationship_content_tiers", False)
    self.enable_flirt_content_tier = self._cfg_bool(c, "enable_flirt_content_tier", True)
    self.owner_exclusive_label = self._cfg_str(c, "owner_exclusive_label", "专属联结", "专属联结")
    self.owner_exclusive_tone = self._cfg_str(c, "owner_exclusive_tone", "温暖、亲近、稳定", "温暖、亲近、稳定")
    self.owner_exclusive_address_style = self._cfg_str(
        c,
        "owner_exclusive_address_style",
        "优先使用已确认的专属称呼",
        "优先使用已确认的专属称呼",
    )
    self.owner_exclusive_proactive_limit = self._cfg_int(c, "owner_exclusive_proactive_limit", 6, 0, 30)
    self.relationship_event_window_minutes = self._cfg_int(c, "relationship_event_window_minutes", 30, 1, 1440)
    self.relationship_positive_event_cap = self._cfg_int(c, "relationship_positive_event_cap", 4, 1, 30)
    self.relationship_negative_event_cap = self._cfg_int(c, "relationship_negative_event_cap", 12, 1, 60)
    self.enable_group_relationship_affinity = self._cfg_bool(
        c, "enable_group_relationship_affinity", False
    )
    self.group_relationship_affinity_allowlist = tuple(sorted(normalize_group_allowlist(
        self._cfg_raw(c, "group_relationship_affinity_allowlist", [])
    )))
    self.group_relationship_daily_net_cap = self._cfg_int(
        c, "group_relationship_daily_net_cap", 2, 0, 20
    )
    self.group_relationship_window_minutes = self._cfg_int(
        c, "group_relationship_window_minutes", 30, 1, 1440
    )
    self.group_relationship_window_absolute_cap = self._cfg_int(
        c, "group_relationship_window_absolute_cap", 1, 0, 20
    )
    self.group_relationship_person_daily_absolute_cap = self._cfg_int(
        c, "group_relationship_person_daily_absolute_cap", 4, 0, 120
    )
    self.group_relationship_scope_daily_absolute_cap = self._cfg_int(
        c, "group_relationship_scope_daily_absolute_cap", 20, 0, 1000
    )
