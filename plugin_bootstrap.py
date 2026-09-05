# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Any

from astrbot.api.star import StarTools

from .body_monitor_integration import BodyMonitorIntegration
from .bot_personal_contract import capability_descriptor, contract_self_check
from .config_migration import migrate_flat_config_into_schema_groups
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
from .migration_outbox import MigrationOutbox
from .model_routing import DEFAULT_SENSITIVE_REPLACEMENT_KEYWORDS, build_rules, normalize_scope
from .segmented_message import normalize_component_order, normalize_component_strategy
from .story_authority import story_startup_sync_operation
from .unified_person_registry import UnifiedPersonRegistry
from .logging_util import get_module_logger

logger = get_module_logger(__name__)

DEFAULT_AI_DAILY_MORNING_UID = "3706929260006322"
DEFAULT_AI_DAILY_JUYA_UID = "285286947"
DEFAULT_AI_DAILY_SOURCES = "\n".join(
    [
        f"AI日报|橘鸦Juya|{DEFAULT_AI_DAILY_JUYA_UID}|日报 早报|23:00",
        f"AI早报|黑鸦Heya|{DEFAULT_AI_DAILY_MORNING_UID}|早报 日报|12:00",
    ]
)

DEFAULT_NEWS_SOURCES = "\n".join(
    [
        "BBC中文|https://feeds.bbci.co.uk/zhongwen/simp/rss.xml",
        "Google新闻中文|https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "Solidot|https://www.solidot.org/index.rss",
        "Hacker News|https://hnrss.org/frontpage",
        "MIT Technology Review|https://www.technologyreview.com/feed/",
        "Ars Technica|https://feeds.arstechnica.com/arstechnica/index",
    ]
)

def _normalize_photo_generation_scopes(value: Any) -> list[str]:
    """Keep explicit empty selections while defaulting missing legacy config."""
    return normalize_photo_generation_scopes(value, default_if_missing=True)

LEGACY_DEFAULT_NEWS_SOURCES = "\n".join(
    [
        "BBC中文|https://feeds.bbci.co.uk/zhongwen/simp/rss.xml",
        "Google新闻中文|https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "Solidot|https://www.solidot.org/index.rss",
    ]
)

_LEGACY_PHOTO_SCENE_PRESET_NAMES = {
    "角色自拍",
    "COS自拍",
    "日常穿搭",
    "居家睡衣",
    "居家服",
    "校服人像",
    "礼服人像",
    "泳装人像",
    "运动服人像",
    "镜前穿搭",
    "头像特写",
    "房间日常",
    "可拍画面",
    "表情包场景",
}

_LEGACY_MULTI_PERSONA_PRIMARY_KEY = "multi_persona_primary_id"


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

PREVIOUS_TECH_DEFAULT_NEWS_SOURCES = "\n".join(
    [
        "BBC中文|https://feeds.bbci.co.uk/zhongwen/simp/rss.xml",
        "Google新闻中文|https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "Solidot|https://www.solidot.org/index.rss",
        "Hacker News|https://hnrss.org/frontpage",
        "MIT Technology Review|https://www.technologyreview.com/feed/",
        "Ars Technica|https://feeds.arstechnica.com/arstechnica/index",
    ]
)

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

def _initialize_core_and_relationship_config(self: Any, c: Any) -> None:
    self.data_dir = StarTools.get_data_dir(PLUGIN_NAME)
    os.makedirs(self.data_dir, exist_ok=True)
    self.data_file = os.path.join(self.data_dir, "companions.json")
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

def _initialize_world_and_model_config(self: Any, c: Any) -> None:
    self.relationship_positive_daily_cap = self._cfg_int(c, "relationship_positive_daily_cap", 12, 0, 120)
    self.relationship_decay_grace_days = self._cfg_int(c, "relationship_decay_grace_days", 3, 0, 30)
    self.relationship_decay_early_per_day = self._cfg_int(c, "relationship_decay_early_per_day", 2, 0, 30)
    self.relationship_decay_middle_per_day = self._cfg_int(c, "relationship_decay_middle_per_day", 5, 0, 30)
    self.relationship_decay_late_per_day = self._cfg_int(c, "relationship_decay_late_per_day", 8, 0, 30)
    self.enable_environment_perception = self._cfg_bool(c, "enable_environment_perception", True)
    configured_timezone = _flat_get(c, "environment_perception_timezone", None)
    if configured_timezone in (None, ""):
        configured_timezone = _flat_get(c, "timezone", None) or "global"
    self.environment_perception_timezone_setting = _normalize_timezone_setting(configured_timezone)
    self.environment_perception_timezone = self._resolve_environment_perception_timezone(
        self.environment_perception_timezone_setting
    )
    _set_today_key_timezone(self.environment_perception_timezone)
    self.enable_holiday_perception = self._cfg_bool(c, "enable_holiday_perception", True)
    self.holiday_country = self._cfg_str(c, "holiday_country", "CN", "CN").upper()
    self.enable_platform_perception = self._cfg_bool(c, "enable_platform_perception", True)
    self.enable_model_perception = self._cfg_bool(c, "enable_model_perception", True)
    self.enable_worldview_perception = self._cfg_bool(c, "enable_worldview_perception", False)
    self.enable_lunar_perception = self._cfg_bool(c, "enable_lunar_perception", True)
    self.enable_solar_term_perception = self._cfg_bool(c, "enable_solar_term_perception", True)
    self.enable_almanac_perception = self._cfg_bool(c, "enable_almanac_perception", False)
    self.provider_config_mode = self._normalize_provider_config_mode(
        self._cfg_raw(c, "provider_config_mode", None),
        c,
    )
    self.model_timeout_overrides = self._normalize_model_timeout_overrides(
        self._cfg_raw(c, "model_timeout_overrides", {})
    )
    self.model_token_limit_overrides = self._normalize_model_token_limit_overrides(
        self._cfg_raw(c, "model_token_limit_overrides", {})
    )
    self.model_fallback_overrides = self._normalize_model_fallback_overrides(
        self._cfg_raw(c, "model_fallback_overrides", {})
    )
    self.enable_llm_streaming = self._cfg_bool(c, "enable_llm_streaming", False)
    self.enable_deepseek_peak_replacement = self._cfg_bool(c, "enable_deepseek_peak_replacement", False)
    self.model_replacement_scope = normalize_scope(
        self._cfg_raw(c, "model_replacement_scope", "plugin"),
    )
    raw_model_replacement_rules = self._cfg_raw(c, "model_replacement_rules", [])
    self.model_replacement_rules, model_replacement_warnings = build_rules(raw_model_replacement_rules)
    if not self.model_replacement_rules:
        legacy_metadata = None
        legacy_getter = getattr(getattr(self, "context", None), "get_registered_star", None)
        if callable(legacy_getter):
            try:
                legacy_metadata = legacy_getter("astrbot_plugin_keyword_model_router")
            except Exception:
                legacy_metadata = None
        legacy_config = getattr(getattr(legacy_metadata, "star_cls", None), "config", None)
        if isinstance(legacy_config, dict):
            self.model_replacement_rules, legacy_warnings = build_rules(legacy_config.get("route_rules", []))
            model_replacement_warnings.extend(f"兼容旧插件：{warning}" for warning in legacy_warnings)
    for warning in model_replacement_warnings:
        logger.warning("模型替换规则：%s", warning)
    self.enable_sensitive_model_replacement = self._cfg_bool(
        c,
        "enable_sensitive_model_replacement",
        False,
    )
    self.sensitive_replacement_provider_id = self._cfg_str(
        c,
        "SENSITIVE_REPLACEMENT_PROVIDER_ID",
        "",
    )
    self.sensitive_replacement_keywords = self._cfg_str(
        c,
        "sensitive_replacement_keywords",
        "；".join(DEFAULT_SENSITIVE_REPLACEMENT_KEYWORDS),
    )
    self.deepseek_peak_replacement_provider_id = self._cfg_str(c, "DEEPSEEK_PEAK_REPLACEMENT_PROVIDER_ID", "")
    self.deepseek_peak_windows = self._cfg_str(c, "deepseek_peak_windows", "09:00-12:00\n14:00-18:00")
    self.deepseek_peak_timezone = self._cfg_str(c, "deepseek_peak_timezone", "Asia/Shanghai", "Asia/Shanghai")
    self.deepseek_peak_match_keywords = self._cfg_str(c, "deepseek_peak_match_keywords", "deepseek,深度求索")
    self._deepseek_peak_last_log_key = ""
    _page_font = str(self._cfg_raw(c, "page_font_family", "original") or "original").strip().lower()
    self.page_font_family = _page_font if _page_font in PAGE_FONT_NAMES else "original"
    _page_theme = str(self._cfg_raw(c, "page_theme", "classic") or "classic").strip().lower()
    self.page_theme = _page_theme if _page_theme in PAGE_THEME_NAMES else "classic"
    self.fast_response_provider_id = self._cfg_str(c, "FAST_RESPONSE_PROVIDER_ID", "")
    self.complex_reasoning_provider_id = self._cfg_str(c, "COMPLEX_REASONING_PROVIDER_ID", "")
    self.creative_model_provider_id = self._cfg_str(c, "CREATIVE_MODEL_PROVIDER_ID", "")
    self.llm_provider_id = self._cfg_str(c, "LLM_PROVIDER_ID", "")
    self.daily_token_limit = self._cfg_int(c, "daily_token_limit", 1_000_000, 0)
    legacy_soft_enabled = self._cfg_bool(c, "enable_maintenance_token_saver", True)
    legacy_soft_limit = self._cfg_int(c, "maintenance_token_soft_limit", 800_000, 0)
    self.enable_daily_token_soft_limit = self._cfg_bool(c, "enable_daily_token_soft_limit", legacy_soft_enabled)
    self.daily_token_soft_limit = self._cfg_int(c, "daily_token_soft_limit", legacy_soft_limit, 0)
    self.enable_maintenance_token_saver = self.enable_daily_token_soft_limit
    self.maintenance_token_soft_limit = self.daily_token_soft_limit
    self.daily_plan_provider_id = self._cfg_str(c, "DAILY_PLAN_PROVIDER_ID", "")
    self.enable_daily_plan = self._cfg_bool(c, "enable_daily_plan", True)
    self.daily_plan_time = self._cfg_str(c, "daily_plan_time", "07:30")
    self.bot_name = self._cfg_str(c, "bot_name", "小星", "小星")
    self.bot_scope_mode = self._cfg_str(c, "bot_scope_mode", "all", "all").strip().lower()
    if self.bot_scope_mode not in {"all", "allowlist", "denylist"}:
        self.bot_scope_mode = "all"
    self.bot_scope_ids = self._cfg_raw(c, "bot_scope_ids", [])
    self.include_schedule_in_messages = self._cfg_bool(c, "include_schedule_in_messages", True)
    self.daily_plan_prompt = self._cfg_str(c, "daily_plan_prompt", "")
    self.schedule_persona_prompt = self._cfg_str(c, "schedule_persona_prompt", "")
    self.schedule_worldview_prompt = self._cfg_str(c, "schedule_worldview_prompt", "")
    self.roleplay_user_profile_prompt = self._cfg_str(c, "roleplay_user_profile_prompt", "")
    self.roleplay_knowledge_source_ids = self._normalize_roleplay_knowledge_source_ids(
        self._cfg_raw(c, "roleplay_knowledge_source_ids", [])
    )
    self.private_image_self_recognition_hint = self._cfg_str(c, "private_image_self_recognition_hint", "")
    self.daily_plan_item_count = self._cfg_int(c, "daily_plan_item_count", 10, 5, 24)
    self.enable_humanized_states = self._cfg_bool(c, "enable_humanized_states", True)
    self.enable_health_state = self._cfg_bool(c, "enable_health_state", True)
    self.enable_hunger_state = self._cfg_bool(c, "enable_hunger_state", True)
    self.enable_cycle_state = self._cfg_bool(c, "enable_cycle_state", True)
    self.enable_group_cycle_awareness = self._cfg_bool(c, "enable_group_cycle_awareness", False)
    self.humanized_state_intensity = self._cfg_int(c, "humanized_state_intensity", 50, 0, 100)
    self.enable_advanced_cycle_strategy = self._cfg_bool(c, "enable_advanced_cycle_strategy", False)
    self.advanced_cycle_link_intensity = self._cfg_bool(c, "advanced_cycle_link_intensity", False)
    self.advanced_cycle_start_offset = self._cfg_int(c, "advanced_cycle_start_offset", 0, 0, 180)
    self.advanced_cycle_menstrual_days = self._cfg_int(c, "advanced_cycle_menstrual_days", 5, 1, 30)
    self.advanced_cycle_menstrual_prompt = self._cfg_str(
        c, "advanced_cycle_menstrual_prompt", "处于月经期，身体更容易疲倦，情绪感受稍敏锐"
    )
    self.advanced_cycle_menstrual_mood = self._cfg_str(c, "advanced_cycle_menstrual_mood", "疲惫")
    self.advanced_cycle_menstrual_energy = self._cfg_int(c, "advanced_cycle_menstrual_energy", -12, -50, 30)
    self.advanced_cycle_follicular_days = self._cfg_int(c, "advanced_cycle_follicular_days", 5, 1, 30)
    self.advanced_cycle_follicular_prompt = self._cfg_str(
        c, "advanced_cycle_follicular_prompt", "处于卵泡期，精力平稳回升，心情逐渐轻快"
    )
    self.advanced_cycle_follicular_mood = self._cfg_str(c, "advanced_cycle_follicular_mood", "轻快")
    self.advanced_cycle_follicular_energy = self._cfg_int(c, "advanced_cycle_follicular_energy", 0, -50, 30)
    self.advanced_cycle_pre_ovulation_days = self._cfg_int(c, "advanced_cycle_pre_ovulation_days", 3, 1, 30)
    self.advanced_cycle_pre_ovulation_prompt = self._cfg_str(
        c, "advanced_cycle_pre_ovulation_prompt", "处于排卵前期，身体逐渐轻盈，精力有所上升"
    )
    self.advanced_cycle_pre_ovulation_mood = self._cfg_str(c, "advanced_cycle_pre_ovulation_mood", "期待")
    self.advanced_cycle_pre_ovulation_energy = self._cfg_int(c, "advanced_cycle_pre_ovulation_energy", 8, -50, 30)
    self.advanced_cycle_ovulation_days = self._cfg_int(c, "advanced_cycle_ovulation_days", 1, 1, 30)
    self.advanced_cycle_ovulation_prompt = self._cfg_str(
        c, "advanced_cycle_ovulation_prompt", "处于排卵期，精力较充足，社交意愿稍有增强"
    )
    self.advanced_cycle_ovulation_mood = self._cfg_str(c, "advanced_cycle_ovulation_mood", "明朗")
    self.advanced_cycle_ovulation_energy = self._cfg_int(c, "advanced_cycle_ovulation_energy", 9, -50, 30)
    self.advanced_cycle_luteal_days = self._cfg_int(c, "advanced_cycle_luteal_days", 8, 1, 30)
    self.advanced_cycle_luteal_prompt = self._cfg_str(
        c, "advanced_cycle_luteal_prompt", "处于黄体期，精力尚可，情绪整体平稳"
    )
    self.advanced_cycle_luteal_mood = self._cfg_str(c, "advanced_cycle_luteal_mood", "平稳")
    self.advanced_cycle_luteal_energy = self._cfg_int(c, "advanced_cycle_luteal_energy", 5, -50, 30)
    self.advanced_cycle_pms_days = self._cfg_int(c, "advanced_cycle_pms_days", 6, 1, 30)
    self.advanced_cycle_pms_prompt = self._cfg_str(
        c, "advanced_cycle_pms_prompt", "处于 PMS 期，精力有所下降，情绪波动稍明显"
    )
    self.advanced_cycle_pms_mood = self._cfg_str(c, "advanced_cycle_pms_mood", "敏感")
    self.advanced_cycle_pms_energy = self._cfg_int(c, "advanced_cycle_pms_energy", -8, -50, 30)
    self.advanced_cycle_discomfort_simulation = self._cfg_bool(
        c, "advanced_cycle_discomfort_simulation", False
    )
    self.advanced_cycle_discomfort_chance = self._cfg_int(
        c, "advanced_cycle_discomfort_chance", 55, 0, 100
    )
    self.advanced_cycle_discomfort_types = self._cfg_str(
        c, "advanced_cycle_discomfort_types", "痛经,头痛,腰酸,乏力"
    )
    self.enable_rest_reply_simulation = self._cfg_bool(c, "enable_rest_reply_simulation", False)
    self.rest_reply_mode = self._cfg_str(c, "rest_reply_mode", "probability", "probability").strip().lower()
    if self.rest_reply_mode in {"model", "模型", "llm_judge", "llm-judge"}:
        self.rest_reply_mode = "llm"
    if self.rest_reply_mode not in {"probability", "llm"}:
        self.rest_reply_mode = "probability"
    self.rest_reply_probability = self._cfg_int(c, "rest_reply_probability", 18, 0, 100) / 100.0
    self.rest_reply_llm_threshold = self._cfg_int(c, "rest_reply_llm_threshold", 65, 0, 100)
    self.rest_reply_active_windows = self._cfg_str(c, "rest_reply_active_windows", "23:00-08:30,12:20-13:40")
    self.rest_reply_awake_grace_minutes = self._cfg_int(c, "rest_reply_awake_grace_minutes", 30, 0, 240)
    self.enable_rest_backlog_reply = self._cfg_bool(c, "enable_rest_backlog_reply", True)
    self.rest_backlog_max_messages = self._cfg_int(c, "rest_backlog_max_messages", 4, 1, 12)
    self.rest_wakeup_provider_id = self._cfg_str(c, "REST_WAKEUP_PROVIDER_ID", "")
    self.enable_busy_reply_gate = self._cfg_bool(c, "enable_busy_reply_gate", False)
    self.busy_reply_min_delay_seconds = self._cfg_int(c, "busy_reply_min_delay_seconds", 60, 0, 900)
    self.busy_reply_max_delay_seconds = self._cfg_int(c, "busy_reply_max_delay_seconds", 300, 0, 900)
    if self.busy_reply_max_delay_seconds < self.busy_reply_min_delay_seconds:
        self.busy_reply_min_delay_seconds, self.busy_reply_max_delay_seconds = (
            self.busy_reply_max_delay_seconds,
            self.busy_reply_min_delay_seconds,
        )
    self.busy_reply_proactive_resume_buffer_minutes = self._cfg_int(
        c,
        "busy_reply_proactive_resume_buffer_minutes",
        10,
        0,
        120,
    )
    self.enable_enhanced_dreams = self._cfg_bool(c, "enable_enhanced_dreams", False)
    self.dream_diary_provider_id = self._cfg_str(
        c,
        "DREAM_DIARY_PROVIDER_ID",
        self._cfg_str(c, "DREAM_PROVIDER_ID", self._cfg_str(c, "DIARY_PROVIDER_ID", "")),
    )
    self.dream_provider_id = self.dream_diary_provider_id
    self.diary_provider_id = self.dream_diary_provider_id
    self.dream_afterglow_mode = self._cfg_str(c, "dream_afterglow_mode", "auto", "auto")
    if self.dream_afterglow_mode not in {"auto", "轻", "标准", "明显"}:
        self.dream_afterglow_mode = "auto"
    self.enable_mixed_dream_themes = self._cfg_bool(c, "enable_mixed_dream_themes", True)
    self.enable_intimate_dream_theme = self._cfg_bool(c, "enable_intimate_dream_theme", False)
    self.dream_theme_candidates = self._cfg_str(
        c,
        "dream_theme_candidates",
        "温柔日常,奇幻,恐怖,追逐,悬疑,荒诞,怀旧,暧昧春梦",
    )
    self.inject_passive_states = self._cfg_bool(c, "inject_passive_states", True)
    self.enable_passive_state_delta_injection = self._cfg_bool(c, "enable_passive_state_delta_injection", True)
    self.enable_passive_state_continuity_anchor = self._cfg_bool(
        c,
        "enable_passive_state_continuity_anchor",
        False,
    )
    self.passive_injection_position = self._normalize_passive_injection_position(
        self._cfg_str(c, "passive_injection_position", "prompt")
    )
    self.proactive_share_probability = self._cfg_int(c, "proactive_share_probability", 45, 0, 100) / 100
    self.enable_daily_greetings = self._cfg_bool(c, "enable_daily_greetings", True)
    self.greeting_idle_minutes = self._cfg_int(c, "greeting_idle_minutes", 30, 0, 240)
    self.allow_insomnia_night_message = self._cfg_bool(c, "allow_insomnia_night_message", True)
    self.proactive_reply_context_hours = self._cfg_int(c, "proactive_reply_context_hours", 12, 1, 72)
    self.enable_creative_writing = self._cfg_bool(c, "enable_creative_writing", False)
    self.enable_creative_work_read_guard = self._cfg_bool(
        c, "enable_creative_work_read_guard", True
    )
    self.creative_inspiration_probability = self._cfg_unit_interval(c, "creative_inspiration_probability", 0.20, 0.0)
    self.creative_share_probability = self._cfg_unit_interval(c, "creative_share_probability", 0.28, 0.0)
    self.creative_chars_per_session = self._cfg_int(
        c,
        "creative_chars_per_session",
        self._cfg_int(c, "creative_base_chars_per_hour", 220, 60, 1200),
        60,
        1200,
    )
    self.creative_base_chars_per_hour = self.creative_chars_per_session
    self.creative_max_active_projects = self._cfg_int(c, "creative_max_active_projects", 2, 1, 5)
    self.creative_hidden_mode = self._cfg_bool(c, "creative_hidden_mode", True)
    self.creative_direction_prompt = self._cfg_str(c, "creative_direction_prompt", "")[:2000]
    self.creative_provider_id = self._cfg_str(c, "CREATIVE_PROVIDER_ID", "")
    self.creative_outline_provider_id = self._cfg_str(c, "CREATIVE_OUTLINE_PROVIDER_ID", "")
    self.creative_review_provider_id = self._cfg_str(c, "CREATIVE_REVIEW_PROVIDER_ID", "")
    self.voice_prompt_provider_id = self._cfg_str(c, "VOICE_PROMPT_PROVIDER_ID", "")
    self.history_summary_provider_id = self._cfg_str(c, "HISTORY_SUMMARY_PROVIDER_ID", "")
    self.enable_llm_proactive_message = self._cfg_bool(c, "enable_llm_proactive_message", True)
    self.proactive_generation_history_limit = self._cfg_int(
        c,
        "proactive_generation_history_limit",
        20,
        1,
        200,
    )
    self.proactive_history_context_mode = self._cfg_str(
        c,
        "proactive_history_context_mode",
        "compact",
        "compact",
    ).lower()
    if self.proactive_history_context_mode not in {"recent_only", "compact", "expanded"}:
        self.proactive_history_context_mode = "compact"
    self.proactive_history_recent_raw_count = self._cfg_int(
        c,
        "proactive_history_recent_raw_count",
        8,
        1,
        50,
    )

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
    if self.photo_generation_backend not in {"auto", "comfyui", "sdgen", "external", "tool_call", "nai"}:
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

def _initialize_photo_and_expression_config(self: Any, c: Any) -> None:
    self._external_image_download_session = None
    self._external_image_download_session_lock = None
    self._external_image_download_session_trust_env = None
    self.enable_backup_external_image_api = self._cfg_bool(c, "enable_backup_external_image_api", False)
    self.backup_external_image_api_platform = self._cfg_str(
        c, "backup_external_image_api_platform", "auto", "auto"
    ).strip().lower()
    self.backup_external_image_api_base_url = self._cfg_str(c, "BACKUP_EXTERNAL_IMAGE_API_BASE_URL", "")
    self.backup_external_image_api_key = self._cfg_str(c, "BACKUP_EXTERNAL_IMAGE_API_KEY", "")
    self.backup_external_image_api_model = self._cfg_str(c, "BACKUP_EXTERNAL_IMAGE_API_MODEL", "")
    self.backup_external_image_api_size = self._cfg_str(c, "backup_external_image_api_size", "1024x1024", "1024x1024")
    self.backup_external_image_api_timeout_seconds = self._cfg_int(c, "backup_external_image_api_timeout_seconds", 180, 20, 600)
    self.backup_external_image_api_custom_headers = self._cfg_str(c, "backup_external_image_api_custom_headers", "")
    raw_external_image_endpoints = self._cfg_raw(c, "external_image_api_endpoints", [])
    self.external_image_api_endpoints = (
        [dict(item) for item in raw_external_image_endpoints if isinstance(item, dict)]
        if isinstance(raw_external_image_endpoints, list)
        else []
    )
    self.photo_generation_prompt_format = self._normalize_photo_generation_prompt_format(
        self._cfg_str(c, "photo_generation_prompt_format", "traditional", "traditional")
    )
    self.photo_generation_style = self._cfg_str(c, "photo_generation_style", "真实", "真实")
    self.photo_generation_style_custom_prompt = self._cfg_str(c, "photo_generation_style_custom_prompt", "")
    self.photo_generation_negative_prompt_mode = self._normalize_photo_generation_negative_prompt_mode(
        self._cfg_str(c, "photo_generation_negative_prompt_mode", "safe_default", "safe_default")
    )
    self.photo_generation_negative_prompt = self._cfg_str(
        c, "photo_generation_negative_prompt", ""
    )
    self.photo_generation_text2img_negative_prompt = self._cfg_str(
        c, "photo_generation_text2img_negative_prompt", ""
    )
    self.photo_generation_selfie_negative_prompt = self._cfg_str(
        c, "photo_generation_selfie_negative_prompt", ""
    )
    self.photo_generation_edit_negative_prompt = self._cfg_str(
        c, "photo_generation_edit_negative_prompt", ""
    )
    self.photo_generation_fixed_prompt = self._cfg_str(c, "photo_generation_fixed_prompt", "")
    self.photo_generation_text2img_fixed_prompt = self._cfg_str(
        c, "photo_generation_text2img_fixed_prompt", ""
    )
    self.photo_generation_selfie_fixed_prompt = self._cfg_str(
        c, "photo_generation_selfie_fixed_prompt", ""
    )
    self.photo_generation_edit_fixed_prompt = self._cfg_str(
        c, "photo_generation_edit_fixed_prompt", ""
    )
    self.photo_generation_scene_presets = self._cfg_raw(c, "photo_generation_scene_presets", "")
    self.enable_bot_relationship_network = self._cfg_bool(c, "enable_bot_relationship_network", False)
    self.bot_relationship_cards = self._normalize_bot_relationship_cards(
        self._cfg_raw(c, "bot_relationship_cards", [])
    )
    raw_reference_catalog = self._cfg_raw(c, "photo_reference_catalog", [])
    raw_reference_catalog_version = self._cfg_raw(c, "photo_reference_catalog_version", 0)
    raw_reference_catalog_user_cleared = self._cfg_bool(
        c,
        "photo_reference_catalog_user_cleared",
        False,
    )
    # This is a compatibility projection only. Scene-preset interpretation and
    # ongoing catalog ownership now live in Image Companion.
    legacy_preset_names = _legacy_photo_scene_preset_names(self.photo_generation_scene_presets)
    loaded_reference_catalog = load_catalog(
        raw_reference_catalog,
        catalog_version=raw_reference_catalog_version,
        legacy_persona=self.photo_persona_reference_image_path,
        legacy_library=self.photo_reference_library,
        user_cleared=raw_reference_catalog_user_cleared,
        preset_names=legacy_preset_names,
    )
    self.photo_reference_catalog = loaded_reference_catalog.references
    self.photo_reference_catalog_read_only = loaded_reference_catalog.read_only
    try:
        self.photo_reference_catalog_version = int(raw_reference_catalog_version or 0)
    except (TypeError, ValueError):
        self.photo_reference_catalog_version = 0
    self.photo_reference_catalog_user_cleared = raw_reference_catalog_user_cleared
    self._startup_photo_reference_catalog_migration_pending = False
    for warning in loaded_reference_catalog.warnings:
        logger.warning("参考图目录加载警告: %s", warning)
    if loaded_reference_catalog.needs_persist:
        self.photo_reference_catalog_read_only = True
        try:
            serialized_reference_catalog = validate_and_serialize(
                loaded_reference_catalog.references,
                preset_names=legacy_preset_names,
            )
            if _set_into_config(c, "photo_reference_catalog", serialized_reference_catalog):
                _set_into_config(c, "photo_reference_catalog_user_cleared", False)
                self.photo_reference_catalog_user_cleared = False
                self._startup_photo_reference_catalog_migration_pending = True
                self._startup_config_migration_changes += 1
            else:
                logger.error("参考图目录迁移无法写入配置，启动期间继续使用旧配置的只读内存投影")
        except Exception as exc:
            logger.error(
                "参考图目录迁移失败，启动期间继续使用旧配置的只读内存投影: %s",
                _single_line(exc, 180),
                exc_info=True,
            )
    self.enable_daily_outfit_photo = self._cfg_bool(c, "enable_daily_outfit_photo", False)
    self.enable_creative_cover_generation = self._cfg_bool(c, "enable_creative_cover_generation", False)
    self.daily_outfit_photo_prompt = self._cfg_str(c, "daily_outfit_photo_prompt", "")
    self.daily_outfit_rotation_days = self._cfg_int(c, "daily_outfit_rotation_days", 10, 1, 30)
    self.enable_natural_language_photo_generation = self._cfg_bool(c, "enable_natural_language_photo_generation", False)
    self.natural_language_photo_generation_mode = self._cfg_str(
        c,
        "natural_language_photo_generation_mode",
        "tool_first",
        "tool_first",
    ).strip().lower()
    if self.natural_language_photo_generation_mode not in {"tool_first", "rule_fast", "off"}:
        self.natural_language_photo_generation_mode = "tool_first"
    self.enable_user_requested_photo_generation = self._cfg_bool(
        c,
        "enable_user_requested_photo_generation",
        True,
    )
    self.command_photo_generation_max_daily = self._cfg_int(c, "command_photo_generation_max_daily", -1, -1, 100)
    self.photo_generation_trace_max_size_kb = self._cfg_int(
        c,
        "photo_generation_trace_max_size_kb",
        0,
        0,
        102400,
    )
    self.photo_generation_trace_backup_count = self._cfg_int(
        c,
        "photo_generation_trace_backup_count",
        5,
        0,
        20,
    )
    self.natural_language_photo_generation_max_daily = self._cfg_int(c, "natural_language_photo_generation_max_daily", 2, 0, 100)
    raw_natural_photo_extra = _flat_get(c, "natural_language_photo_extra_prompt", None)
    self.natural_language_photo_extra_prompt = (
        DEFAULT_NATURAL_LANGUAGE_PHOTO_EXTRA_PROMPT
        if raw_natural_photo_extra is None
        else str(raw_natural_photo_extra).strip()
    )
    self.enable_weather_context = self._cfg_bool(c, "enable_weather_context", True)
    self.weather_source = self._cfg_str(c, "weather_source", "qweather").lower()
    if self.weather_source not in {"qweather", "openweathermap", "amap", "openmeteo"}:
        self.weather_source = "qweather"
    self.weather_api_key = self._cfg_str(c, "weather_api_key", "")
    self.weather_city = self._cfg_str(c, "weather_city", "")
    self.weather_amap_api_key = self._cfg_str(c, "weather_amap_api_key", "")
    self.weather_amap_city = self._cfg_str(c, "weather_amap_city", "")
    raw_weather_location = _flat_get(c, "weather_location", "")
    self.weather_location = str(raw_weather_location or "").strip()
    self.weather_lat = self._cfg_float(c, "weather_lat", 0.0, -90.0)
    self.weather_lon = self._cfg_float(c, "weather_lon", 0.0, -180.0)
    self.weather_refresh_minutes = self._cfg_int(c, "weather_refresh_minutes", 90, 10, 720)
    self.enable_weather_alerts = self._cfg_bool(c, "enable_weather_alerts", False)
    configured_weather_host = self._cfg_str(c, "weather_api_host", "").rstrip("/")
    configured_weather_token = self._cfg_str(c, "weather_token", "")
    legacy_weather_alert_host = self._cfg_str(c, "weather_alert_api_host", "").rstrip("/")
    legacy_weather_alert_token = self._cfg_str(c, "weather_alert_api_key", "")
    configured_alert_token = self._cfg_str(c, "weather_alert_token", "")
    # The generic QWeather fields are shared by ordinary weather and
    # alerts.  Keep the old alert names as read-time fallbacks so an
    # existing installation does not need to be reconfigured at once.
    self.weather_api_host = configured_weather_host or legacy_weather_alert_host
    self.weather_token = configured_weather_token or configured_alert_token or legacy_weather_alert_token
    self.weather_alert_api_host = legacy_weather_alert_host or self.weather_api_host
    self.weather_alert_token = configured_alert_token or configured_weather_token or legacy_weather_alert_token
    # Expose the old attribute as a runtime alias for integrations written
    # against the early api_key draft; the persisted field remains token.
    self.weather_alert_api_key = (
        legacy_weather_alert_token or self.weather_alert_token
    )
    self.weather_alert_refresh_minutes = self._cfg_int(c, "weather_alert_refresh_minutes", 10, 5, 60)
    self.weather_alert_min_severity = self._normalize_weather_alert_min_severity(
        self._cfg_str(c, "weather_alert_min_severity", "blue", "blue")
    )
    self.enable_environment_change_proactive = self._cfg_bool(c, "enable_environment_change_proactive", True)
    self.environment_change_check_minutes = self._cfg_int(c, "environment_change_check_minutes", 10, 5, 60)
    self.environment_change_cooldown_minutes = self._cfg_int(c, "environment_change_cooldown_minutes", 90, 20, 360)
    self.enable_yesterday_screen_diary_context = self._cfg_bool(c, "enable_yesterday_screen_diary_context", True)
    self.screen_diary_context_max_chars = self._cfg_int(c, "screen_diary_context_max_chars", 700, 200, 1600)
    self.detail_enhancement_lead_minutes = self._cfg_int(c, "detail_enhancement_lead_minutes", 3, 0, 180)
    self.enable_daily_diary = self._cfg_bool(c, "enable_daily_diary", True)
    self.daily_diary_time = self._cfg_str(c, "daily_diary_time", "23:10")
    self.daily_diary_form = self._cfg_str(c, "daily_diary_form", "auto")
    self.daily_diary_length = self._cfg_str(c, "daily_diary_length", "standard")
    self.daily_diary_creativity = self._cfg_str(c, "daily_diary_creativity", "balanced")
    self.daily_diary_custom_direction = self._cfg_str(c, "daily_diary_custom_direction", "")
    self.daily_diary_generate_share_seed = self._cfg_bool(c, "daily_diary_generate_share_seed", True)
    self.max_diary_entries = self._cfg_int(c, "max_diary_entries", 14, 1, 60)
    self.enable_daily_review = self._cfg_bool(c, "enable_daily_review", True)
    self.daily_review_time = self._cfg_str(c, "daily_review_time", "04:00")
    self.daily_review_retention_days = self._cfg_int(c, "daily_review_retention_days", 30, 3, 180)
    self.daily_review_auto_apply_guidance = self._cfg_bool(c, "daily_review_auto_apply_guidance", True)
    self.enable_daily_case_review_experiment = self._cfg_bool(
        c, "enable_daily_case_review_experiment", False
    )
    self.important_date_lookahead_days = self._cfg_int(c, "important_date_lookahead_days", 7, 0, 60)
    legacy_actions = self._parse_action_list(self._cfg_raw(c, "enabled_proactive_actions", None))
    legacy_photo_enabled = "photo_text" in legacy_actions if legacy_actions else True
    legacy_screen_enabled = "screen_peek" in legacy_actions if legacy_actions else False
    legacy_poke_enabled = "poke" in legacy_actions if legacy_actions else False
    legacy_voice_enabled = "voice" in legacy_actions if legacy_actions else False
    self.enable_photo_text_action = self._cfg_bool(
        c, "enable_photo_text_action", bool(self._cfg_raw(c, "allow_photo_text_action", legacy_photo_enabled))
    )
    self.photo_generation_allowed_scopes = {}
    for scope, key in PHOTO_GENERATION_SCOPE_LIMIT_KEYS.items():
        limit = self._cfg_int(c, key, -1, -1, 100)
        setattr(self, key, limit)
        self.photo_generation_allowed_scopes[scope] = limit
    self.enable_photo_reference_image = self._cfg_bool(c, "enable_photo_reference_image", False)
    self.enable_group_nsfw_private_fallback = self._cfg_bool(c, "enable_group_nsfw_private_fallback", False)
    review_mode = self._cfg_str(c, "group_nsfw_image_review_mode", "single").strip().lower()
    self.group_nsfw_image_review_mode = review_mode if review_mode in {"single", "dual"} else "single"
    review_sensitivity = self._cfg_str(c, "group_nsfw_image_review_sensitivity", "balanced").strip().lower()
    self.group_nsfw_image_review_sensitivity = (
        review_sensitivity if review_sensitivity in {"relaxed", "balanced", "strict"} else "balanced"
    )
    self.group_nsfw_image_review_min_confidence = min(
        1.0,
        self._cfg_float(c, "group_nsfw_image_review_min_confidence", 0.7, 0.0),
    )
    self.group_nsfw_image_review_timeout_seconds = min(
        30.0,
        self._cfg_float(c, "group_nsfw_image_review_timeout_seconds", 8.0, 3.0),
    )
    self.group_nsfw_image_review_max_dimension = self._cfg_int(
        c, "group_nsfw_image_review_max_dimension", 1280, 0, 4096
    )
    review_failure_action = self._cfg_str(c, "group_nsfw_image_review_failure_action", "private").strip().lower()
    self.group_nsfw_image_review_failure_action = (
        review_failure_action if review_failure_action in {"private", "block"} else "private"
    )
    self.group_nsfw_image_review_custom_prompt = self._cfg_str(
        c, "group_nsfw_image_review_custom_prompt", ""
    )[:1200]
    self.enable_screen_glance_action = self._cfg_bool(
        c, "enable_screen_glance_action", bool(self._cfg_raw(c, "allow_screen_peek_action", legacy_screen_enabled))
    )
    self.enable_poke_action = self._cfg_bool(
        c, "enable_poke_action", bool(self._cfg_raw(c, "allow_poke_action", legacy_poke_enabled))
    )
    self.enable_voice_action = self._cfg_bool(
        c, "enable_voice_action", bool(self._cfg_raw(c, "allow_voice_action", legacy_voice_enabled))
    )
    self.enable_qq_presence_sync = self._cfg_bool(c, "enable_qq_presence_sync", True)
    self.enable_qq_custom_presence_sync = self._cfg_bool(c, "enable_qq_custom_presence_sync", False)
    self.poke_action_max_times = self._cfg_int(c, "poke_action_max_times", 1, 1, 3)
    self.poke_action_cooldown_minutes = self._cfg_int(c, "poke_action_cooldown_minutes", 30, 0, 1440)
    self.voice_action_max_chars = self._cfg_int(c, "voice_action_max_chars", 30, 6, 80)
    self.photo_action_max_daily = self._cfg_int(c, "photo_action_max_daily", 1, 0, 5)
    self.proactive_photo_text_probability = self._cfg_int(c, "proactive_photo_text_probability", 18, 0, 100) / 100
    self.screen_peek_max_daily = self._cfg_int(c, "screen_peek_max_daily", 1, 0, 5)
    self.screen_peek_cooldown_minutes = self._cfg_int(c, "screen_peek_cooldown_minutes", 240, 0, 1440)
    self.enable_goodnight_screen_check = self._cfg_bool(c, "enable_goodnight_screen_check", False)
    self.goodnight_screen_check_delay_minutes = self._cfg_int(
        c, "goodnight_screen_check_delay_minutes", 45, 1, 180
    )
    self.enable_unanswered_screen_peek_followup = self._cfg_bool(c, "enable_unanswered_screen_peek_followup", True)
    self.unanswered_screen_peek_after_minutes = self._cfg_int(c, "unanswered_screen_peek_after_minutes", 45, 10, 240)
    self.unanswered_screen_peek_cooldown_minutes = self._cfg_int(c, "unanswered_screen_peek_cooldown_minutes", 180, 30, 1440)
    self.enable_mai_style_integration = self._cfg_bool(c, "enable_mai_style_integration", True)
    self.enable_companion_memory = self._cfg_bool(c, "enable_companion_memory", True)
    self.enable_expression_learning = self._cfg_bool(c, "enable_expression_learning", True)
    self.expression_learning_mode = self._cfg_str(c, "expression_learning_mode", "balanced", "balanced").lower()
    if self.expression_learning_mode not in {"light", "balanced", "aggressive"}:
        self.expression_learning_mode = "balanced"
    self.expression_private_learning_source_mode = self._cfg_str(
        c, "expression_private_learning_source_mode", "owner", "owner"
    ).lower()
    if self.expression_private_learning_source_mode not in {"owner", "selected", "all"}:
        self.expression_private_learning_source_mode = "owner"
    self.expression_private_learning_source_ids = self._cfg_raw(c, "expression_private_learning_source_ids", [])
    self.expression_group_learning_source_mode = self._cfg_str(
        c, "expression_group_learning_source_mode", "disabled", "disabled"
    ).lower()
    if self.expression_group_learning_source_mode not in {"disabled", "selected", "all"}:
        self.expression_group_learning_source_mode = "disabled"
    self.expression_group_learning_source_ids = self._cfg_raw(c, "expression_group_learning_source_ids", [])

def _initialize_review_and_group_config(self: Any, c: Any) -> None:
    self.expression_group_learning_daily_batch_limit = self._cfg_int(
        c,
        "expression_group_learning_daily_batch_limit",
        6,
        1,
        50,
    )
    self.expression_group_learning_min_new_messages = self._cfg_int(
        c,
        "expression_group_learning_min_new_messages",
        20,
        5,
        80,
    )
    self.expression_private_application_mode = self._cfg_str(
        c, "expression_private_application_mode", "all", "all"
    ).lower()
    if self.expression_private_application_mode not in {"all", "selected"}:
        self.expression_private_application_mode = "all"
    self.expression_private_application_user_ids = self._cfg_raw(c, "expression_private_application_user_ids", [])
    self.expression_group_application_mode = self._cfg_str(
        c, "expression_group_application_mode", "all", "all"
    ).lower()
    if self.expression_group_application_mode not in {"disabled", "all", "selected"}:
        self.expression_group_application_mode = "all"
    self.expression_group_application_ids = self._cfg_raw(c, "expression_group_application_ids", [])
    self.enable_expression_manual_review = self._cfg_bool(c, "enable_expression_manual_review", False)
    self.enable_expression_style_review = self._cfg_bool(c, "enable_expression_style_review", True)
    self.enable_intent_emotion_analysis = self._cfg_bool(c, "enable_intent_emotion_analysis", True)
    legacy_response_review_enabled = self._cfg_bool(c, "enable_response_self_review", True)
    self.enable_passive_response_review = self._cfg_bool(
        c,
        "enable_passive_response_review",
        legacy_response_review_enabled,
    )
    self.enable_framework_error_leak_guard = self._cfg_bool(
        c,
        "enable_framework_error_leak_guard",
        True,
    )
    # Runtime alias for older integrations. It no longer gates proactive review.
    self.enable_response_self_review = self.enable_passive_response_review
    self.enable_proactive_message_review = self._cfg_bool(
        c,
        "enable_proactive_message_review",
        legacy_response_review_enabled,
    )
    self.external_share_require_source_link = self._cfg_bool(c, "external_share_require_source_link", True)
    self.proactive_review_history_limit = self._cfg_int(
        c,
        "proactive_review_history_limit",
        30,
        1,
        200,
    )
    legacy_response_review_mode = self._cfg_str(c, "response_review_mode", "severe_only", "severe_only").lower()
    self.passive_review_mode = self._cfg_str(
        c,
        "passive_review_mode",
        legacy_response_review_mode,
        legacy_response_review_mode,
    ).lower()
    if self.passive_review_mode not in {"local_only", "severe_only", "full"}:
        self.passive_review_mode = "severe_only"
    self.response_review_mode = self.passive_review_mode
    self.passive_review_strength = self._cfg_str(c, "passive_review_strength", "lenient", "lenient").lower()
    if self.passive_review_strength not in {"lenient", "balanced", "strict"}:
        self.passive_review_strength = "lenient"
    self.proactive_review_mode = self._cfg_str(c, "proactive_review_mode", "full", "full").lower()
    if self.proactive_review_mode not in {"local_only", "severe_only", "full"}:
        self.proactive_review_mode = "full"
    self.enable_smart_silence = self._cfg_bool(c, "enable_smart_silence", True)
    self.smart_silence_judge_mode = self._cfg_str(c, "smart_silence_judge_mode", "boundary_only", "boundary_only").strip().lower()
    if self.smart_silence_judge_mode not in {"boundary_only", "contextual"}:
        self.smart_silence_judge_mode = "boundary_only"
    self.smart_silence_provider_id = self._cfg_str(c, "SMART_SILENCE_PROVIDER_ID", "")
    self.smart_silence_min_confidence = self._cfg_unit_interval(c, "smart_silence_min_confidence", 0.66, 0.0)
    self.smart_silence_model_timeout_seconds = self._cfg_float(c, "smart_silence_model_timeout_seconds", 1.2, 0.2)
    self._smart_silence_cache: dict[str, dict[str, Any]] = {}
    self.proactive_review_strength = self._cfg_str(c, "proactive_review_strength", "lenient", "lenient").lower()
    if self.proactive_review_strength not in {"lenient", "balanced", "strict"}:
        self.proactive_review_strength = "lenient"
    self.proactive_review_hard_risk_threshold = self._cfg_unit_interval(c, "proactive_review_hard_risk_threshold", 0.70, 0.0)
    self.proactive_review_low_score_threshold = self._cfg_unit_interval(c, "proactive_review_low_score_threshold", 0.34, 0.0)
    self.proactive_review_pressure_threshold = self._cfg_unit_interval(c, "proactive_review_pressure_threshold", 0.55, 0.0)
    self.enable_passive_topic_suppression = self._cfg_bool(c, "enable_passive_topic_suppression", True)
    # The unified ledger owns runtime updates.  Keep the legacy switches
    # readable for diagnostics and old config pages, but do not let their
    # persisted values disable the unified path.
    self.enable_relationship_analysis = self._cfg_bool(c, "enable_relationship_analysis", True)
    self.enable_relationship_state_machine = self._cfg_bool(c, "enable_relationship_state_machine", True)
    self.enable_emotion_simulation = self._cfg_bool(c, "enable_emotion_simulation", True)
    self.enable_relationship_violation_penalties = self._cfg_bool(c, "enable_relationship_violation_penalties", True)
    self.enable_relationship_boundary_feedback = self._cfg_bool(c, "enable_relationship_boundary_feedback", True)
    self.enable_relationship_boundary_stage = self._cfg_bool(c, "enable_relationship_boundary_stage", True)
    self.enable_relationship_boundary_apology = self._cfg_bool(c, "enable_relationship_boundary_apology", True)
    self.enable_relationship_boundary_bottom_line = self._cfg_bool(c, "enable_relationship_boundary_bottom_line", True)
    self.relationship_boundary_tier_adaptive = self._cfg_bool(c, "relationship_boundary_tier_adaptive", True)
    self.relationship_boundary_penalty_light = self._cfg_int(c, "relationship_boundary_penalty_light", 4, 1, 60)
    self.relationship_boundary_penalty_mid = self._cfg_int(c, "relationship_boundary_penalty_mid", 7, 1, 60)
    self.relationship_boundary_penalty_severe = self._cfg_int(c, "relationship_boundary_penalty_severe", 12, 1, 60)
    self.relationship_boundary_penalty_bottom_line = self._cfg_int(c, "relationship_boundary_penalty_bottom_line", 14, 1, 60)
    self.relationship_boundary_stage_avoid_points = self._cfg_int(c, "relationship_boundary_stage_avoid_points", 6, 1, 120)
    self.relationship_boundary_stage_forbid_points = self._cfg_int(c, "relationship_boundary_stage_forbid_points", 12, 1, 120)
    self.relationship_boundary_stage_reflect_points = self._cfg_int(c, "relationship_boundary_stage_reflect_points", 20, 1, 120)
    if self.relationship_boundary_stage_forbid_points < self.relationship_boundary_stage_avoid_points:
        self.relationship_boundary_stage_forbid_points = self.relationship_boundary_stage_avoid_points
    if self.relationship_boundary_stage_reflect_points < self.relationship_boundary_stage_forbid_points:
        self.relationship_boundary_stage_reflect_points = self.relationship_boundary_stage_forbid_points
    self.relationship_boundary_cold_minutes = self._cfg_int(c, "relationship_boundary_cold_minutes", 180, 10, 1440)
    self.relationship_boundary_apology_restore_ratio = self._cfg_unit_interval(c, "relationship_boundary_apology_restore_ratio", 0.6, 0.0)
    self.relationship_boundary_apology_duplicate_limit = self._cfg_int(c, "relationship_boundary_apology_duplicate_limit", 3, 1, 20)
    self.relationship_boundary_apology_speedup_multiplier = self._cfg_float(c, "relationship_boundary_apology_speedup_multiplier", 3.0, 1.0, 10.0)
    self.relationship_boundary_recover_ratio_light = self._cfg_unit_interval(c, "relationship_boundary_recover_ratio_light", 0.5, 0.0)
    self.relationship_boundary_recover_ratio_mid = self._cfg_unit_interval(c, "relationship_boundary_recover_ratio_mid", 0.33, 0.0)
    self.relationship_boundary_recover_ratio_severe = self._cfg_unit_interval(c, "relationship_boundary_recover_ratio_severe", 0.25, 0.0)
    self.enable_relationship_boundary_vent = self._cfg_bool(c, "enable_relationship_boundary_vent", True)
    self.enable_relationship_boundary_owner_report = self._cfg_bool(c, "enable_relationship_boundary_owner_report", True)
    self.relationship_boundary_vent_targets = self._cfg_raw(c, "relationship_boundary_vent_targets", [])
    self.relationship_boundary_vent_scene_template = self._cfg_str(c, "relationship_boundary_vent_scene_template", "")
    self.relationship_boundary_bottom_line_baseline = self._cfg_str(c, "relationship_boundary_bottom_line_baseline", "")
    self.relationship_boundary_tone_confession = self._cfg_str(
        c,
        "relationship_boundary_tone_confession",
        "把这次表达当作心意，不当作冒犯；结合当前关系自然害羞、迟疑或温和说明节奏，不必机械拒绝。",
    )
    self.relationship_boundary_tone_light = self._cfg_str(
        c,
        "relationship_boundary_tone_light",
        "轻微降低亲密度，带一点迟疑或回避并自然说明节奏；不要把普通互动渲染成严重冒犯。",
    )
    self.relationship_boundary_tone_mid = self._cfg_str(
        c,
        "relationship_boundary_tone_mid",
        "平静而明确地划清界限，减少主动贴近和暧昧回应；可以说明原因，但不要反复说教。",
    )
    self.relationship_boundary_tone_severe = self._cfg_str(
        c,
        "relationship_boundary_tone_severe",
        "明显收住亲密表达，直接说明不舒服并拒绝继续；保持角色口吻，不使用系统式警告。",
    )
    self.relationship_boundary_tone_bottom_line = self._cfg_str(
        c,
        "relationship_boundary_tone_bottom_line",
        "明确表达这触碰了重要底线，受伤和距离感可以真实存在；不要功能化播报惩罚，也不要立即恢复亲密。",
    )
    self.relationship_boundary_tone_silent = self._cfg_str(
        c,
        "relationship_boundary_tone_silent",
        "关系尚浅时不必长篇袒露脆弱，可以安静收住互动并记住这次不舒服。",
    )
    self.relationship_boundary_tone_communicate = self._cfg_str(
        c,
        "relationship_boundary_tone_communicate",
        "关系很深时可以因为信任而说清为什么难过或生气，但亲密关系不等于放弃边界。",
    )
    for _boundary_level, _boundary_default in {
        "light": 0.15,
        "mid": 0.35,
        "severe": 0.6,
        "bottom_line": 0.9,
    }.items():
        setattr(self, f"relationship_boundary_vent_probability_{_boundary_level}", self._cfg_unit_interval(c, f"relationship_boundary_vent_probability_{_boundary_level}", _boundary_default, 0.0))
    for _boundary_level, _boundary_default in {
        "light": 0.12,
        "mid": 0.3,
        "severe": 0.55,
        "bottom_line": 0.85,
    }.items():
        setattr(self, f"relationship_boundary_owner_report_probability_{_boundary_level}", self._cfg_unit_interval(c, f"relationship_boundary_owner_report_probability_{_boundary_level}", _boundary_default, 0.0))
    self.relationship_violation_recovery_minutes_per_point = self._cfg_int(
        c,
        "relationship_violation_recovery_minutes_per_point",
        180,
        15,
        10080,
    )
    self.enable_llm_emotion_judgement = self._cfg_bool(c, "enable_llm_emotion_judgement", False)
    self.emotion_judgement_mode = self._cfg_str(c, "emotion_judgement_mode", "suspicious", "suspicious").lower()
    if self.emotion_judgement_mode not in {"suspicious", "always", "off"}:
        self.emotion_judgement_mode = "suspicious"
    self.emotional_gate_hurt_threshold = self._cfg_int(c, "emotional_gate_hurt_threshold", 70, 10, 100)
    if self.emotional_gate_hurt_threshold == 55:
        self.emotional_gate_hurt_threshold = 70
        _set_into_config(c, "emotional_gate_hurt_threshold", self.emotional_gate_hurt_threshold)
    self.emotional_gate_refuse_threshold = self._cfg_int(c, "emotional_gate_refuse_threshold", 90, 20, 100)
    if self.emotional_gate_refuse_threshold == 80:
        self.emotional_gate_refuse_threshold = 90
        _set_into_config(c, "emotional_gate_refuse_threshold", self.emotional_gate_refuse_threshold)
    if self.emotional_gate_refuse_threshold <= self.emotional_gate_hurt_threshold:
        self.emotional_gate_refuse_threshold = min(100, self.emotional_gate_hurt_threshold + 5)
    self.emotional_gate_recovery_per_hour = self._cfg_int(c, "emotional_gate_recovery_per_hour", 24, 1, 60)
    if self.emotional_gate_recovery_per_hour == 12:
        self.emotional_gate_recovery_per_hour = 24
        _set_into_config(c, "emotional_gate_recovery_per_hour", self.emotional_gate_recovery_per_hour)
    self.emotional_gate_max_hurt_minutes = self._cfg_int(c, "emotional_gate_max_hurt_minutes", 90, 10, 720)
    if self.emotional_gate_max_hurt_minutes == 180:
        self.emotional_gate_max_hurt_minutes = 90
        _set_into_config(c, "emotional_gate_max_hurt_minutes", self.emotional_gate_max_hurt_minutes)
    self.enable_dialogue_episode_memory = self._cfg_bool(c, "enable_dialogue_episode_memory", True)
    self.enable_open_loop_tracking = self._cfg_bool(c, "enable_open_loop_tracking", True)
    self.enable_user_habit_learning = self._cfg_bool(c, "enable_user_habit_learning", True)
    self.enable_food_menu_recommendation = self._cfg_bool(c, "enable_food_menu_recommendation", True)
    self.enable_meal_care_proactive = self._cfg_bool(c, "enable_meal_care_proactive", True)
    self.meal_care_max_daily = self._cfg_int(c, "meal_care_max_daily", 1, 0, 3)
    self.meal_care_min_interval_hours = self._cfg_int(c, "meal_care_min_interval_hours", 48, 0, 168)
    self.meal_care_followup_minutes = self._cfg_int(c, "meal_care_followup_minutes", 45, 15, 180)
    self.user_habit_min_count = self._cfg_int(c, "user_habit_min_count", 3, 2, 20)
    self.user_habit_max_items = self._cfg_int(c, "user_habit_max_items", 24, 8, 80)
    self.enable_skill_growth_simulation = self._cfg_bool(c, "enable_skill_growth_simulation", True)
    self.skill_growth_rate = self._cfg_float(c, "skill_growth_rate", 1.0, 0.1)
    self.skill_growth_custom_skills = self._cfg_str(c, "skill_growth_custom_skills", "")
    self.enable_skill_growth_passive_injection = self._cfg_bool(c, "enable_skill_growth_passive_injection", False)
    self.enable_skill_growth_schedule_influence = self._cfg_bool(c, "enable_skill_growth_schedule_influence", True)
    self.skill_growth_schedule_influence_strength = self._cfg_unit_interval(c, "skill_growth_schedule_influence_strength", 0.35, 0.0)
    self.enable_personal_goals = self._cfg_bool(c, "enable_personal_goals", True)
    self.enable_personal_goal_auto_progress = self._cfg_bool(c, "enable_personal_goal_auto_progress", True)
    self.personal_goal_share_cooldown_hours = self._cfg_float(c, "personal_goal_share_cooldown_hours", 12.0, 1.0, 168.0)
    self.personal_goal_stall_days = self._cfg_int(c, "personal_goal_stall_days", 3, 1, 30)
    self.memory_refresh_interval_minutes = self._cfg_int(c, "memory_refresh_interval_minutes", 360, 30, 4320)
    self.max_companion_memory_items = self._cfg_int(c, "max_companion_memory_items", 36, 8, 120)
    self.max_learned_expression_items = self._cfg_int(c, "max_learned_expression_items", 60, 12, 240)
    self.mai_style_provider_id = self._cfg_str(c, "MAI_STYLE_PROVIDER_ID", "")
    self.companion_memory_provider_id = self._cfg_str(c, "COMPANION_MEMORY_PROVIDER_ID", "")
    self.dialogue_episode_provider_id = self._cfg_str(c, "DIALOGUE_EPISODE_PROVIDER_ID", "")
    self.relationship_analysis_provider_id = self._cfg_str(c, "RELATIONSHIP_ANALYSIS_PROVIDER_ID", "")
    self.response_review_provider_id = self._cfg_str(c, "RESPONSE_REVIEW_PROVIDER_ID", "")
    self.troubleshooting_provider_id = self._cfg_str(c, "TROUBLESHOOTING_PROVIDER_ID", "")
    self.daily_review_provider_id = self._cfg_str(c, "DAILY_REVIEW_PROVIDER_ID", "")
    self.emotion_judgement_provider_id = self._cfg_str(c, "EMOTION_JUDGEMENT_PROVIDER_ID", "")
    self.response_review_max_chars = self._cfg_int(c, "response_review_max_chars", 260, 80, 900)
    self.passive_topic_memory_hours = self._cfg_int(c, "passive_topic_memory_hours", 8, 1, 72)
    self.episode_memory_refresh_messages = self._cfg_int(c, "episode_memory_refresh_messages", 8, 3, 40)
    self.episode_memory_refresh_minutes = self._cfg_int(c, "episode_memory_refresh_minutes", 90, 15, 1440)
    self.max_dialogue_episodes = self._cfg_int(c, "max_dialogue_episodes", 12, 3, 40)
    self.enable_group_companion = self._cfg_bool(c, "enable_group_companion", True)
    self.group_access_mode = self._cfg_str(c, "group_access_mode", "whitelist", "whitelist").lower()
    if self.group_access_mode not in {"whitelist", "blacklist"}:
        self.group_access_mode = "whitelist"
    self.target_group_ids = self._cfg_raw(c, "target_group_ids", [])
    self.group_whitelist_ids = self._cfg_raw(c, "group_whitelist_ids", self.target_group_ids)
    self.group_blacklist_ids = self._cfg_raw(c, "group_blacklist_ids", [])
    self.require_target_group = self._cfg_bool(c, "require_target_group", True)
    self.enable_group_slang_learning = self._cfg_bool(c, "enable_group_slang_learning", True)
    # Group observation no longer writes the retired Worldbook member profile,
    # but keep this compatibility switch readable for existing installations.
    self.enable_group_member_profiles = self._cfg_bool(c, "enable_group_member_profiles", True)
    self.enable_group_member_safety = self._cfg_bool(c, "enable_group_member_safety", True)
    self.group_member_safety_review_mode = self._cfg_str(
        c, "group_member_safety_review_mode", "directed", "directed"
    ).lower()
    if self.group_member_safety_review_mode not in {"directed", "suspicious", "all"}:
        self.group_member_safety_review_mode = "directed"
    self.group_member_safety_hidden_marker_mode = self._cfg_str(
        c, "group_member_safety_hidden_marker_mode", "reply_only", "reply_only"
    ).lower()
    if self.group_member_safety_hidden_marker_mode not in {"supplement", "reply_only", "disabled"}:
        self.group_member_safety_hidden_marker_mode = "reply_only"
    self.group_member_safety_strike_threshold = self._cfg_int(
        c, "group_member_safety_strike_threshold", 3, 1, 20
    )
    self.group_member_safety_strike_window_days = self._cfg_int(
        c, "group_member_safety_strike_window_days", 30, 1, 365
    )
    self.group_member_safety_block_hours = self._cfg_int(
        c, "group_member_safety_block_hours", 168, 0, 8760
    )
    self.group_member_safety_min_confidence = self._cfg_float(
        c, "group_member_safety_min_confidence", 0.86, 0.5, 1.0
    )
    self.group_member_safety_exempt_managers = self._cfg_bool(
        c, "group_member_safety_exempt_managers", True
    )
    self.group_member_safety_audit_limit = self._cfg_int(
        c, "group_member_safety_audit_limit", 40, 10, 200
    )
    self.enable_group_context_injection = self._cfg_bool(c, "enable_group_context_injection", True)
    self.enable_group_history_injection = self._cfg_bool(c, "enable_group_history_injection", True)
    self.intercept_astrbot_group_context = self._cfg_bool(
        c, "intercept_astrbot_group_context", True
    )
    self.enable_group_injection_guard = self._cfg_bool(c, "enable_group_injection_guard", True)
    self.enable_group_persona_denoise = self._cfg_bool(c, "enable_group_persona_denoise", True)
    self.enable_forward_message_adaptation = self._cfg_bool(c, "enable_forward_message_adaptation", True)
    self.forward_message_mode = self._cfg_str(c, "forward_message_mode", "inject", "inject").lower()
    if self.forward_message_mode in {"注入", "injection"}:
        self.forward_message_mode = "inject"
    elif self.forward_message_mode in {"转述", "summary", "summarize", "narrate", "relay"}:
        self.forward_message_mode = "transcribe"
    elif self.forward_message_mode not in {"inject", "transcribe"}:
        self.forward_message_mode = "inject"
    self.forward_message_provider_id = self._cfg_str(c, "FORWARD_MESSAGE_PROVIDER_ID", "")
    self.forward_message_max_messages = self._cfg_int(c, "forward_message_max_messages", 80, 5, 300)
    self.forward_message_max_chars = self._cfg_int(c, "forward_message_max_chars", 5000, 800, 20000)
    self.forward_message_parse_nested = self._cfg_bool(c, "forward_message_parse_nested", True)
    self.forward_message_image_vision = self._cfg_bool(c, "forward_message_image_vision", True)
    self.forward_message_image_limit = self._cfg_int(c, "forward_message_image_limit", 4, 0, 12)
    self.forward_message_image_vision_timeout_seconds = self._cfg_float(
        c,
        "forward_message_image_vision_timeout_seconds",
        60.0,
        0.0,
        600.0,
    )
    self.enable_group_scene_awareness = self._cfg_bool(c, "enable_group_scene_awareness", True)
    self.group_scene_recent_limit = self._cfg_int(c, "group_scene_recent_limit", 20, 2, 100)
    self.group_scene_recent_max_chars = self._cfg_int(c, "group_scene_recent_max_chars", 4000, 500, 20000)
    self.enable_group_reality_promise_guard = self._cfg_bool(c, "enable_group_reality_promise_guard", True)
    self.enable_group_wakeup_enhancement = self._cfg_bool(c, "enable_group_wakeup_enhancement", True)
    self.group_wakeup_direct_words = self._parse_text_list_config(self._cfg_raw(c, "group_wakeup_direct_words", []))
    self.enable_group_bot_name_wakeup = self._cfg_bool(c, "enable_group_bot_name_wakeup", True)
    self.group_wakeup_owner_direct_words = self._parse_text_list_config(
        self._cfg_raw(c, "group_wakeup_owner_direct_words", [])
    )
    self.group_wakeup_context_words = self._parse_text_list_config(
        self._cfg_raw(c, "group_wakeup_context_words", ["机器人", "bot"])
    )

def _initialize_group_and_provider_config(self: Any, c: Any) -> None:
    if self.group_wakeup_context_words == ["有人叫你", "提到你", "说到你", "机器人", "AI"]:
        self.group_wakeup_context_words = ["机器人", "bot"]
    self.group_wakeup_interest_keywords = self._parse_text_list_config(self._cfg_raw(c, "group_wakeup_interest_keywords", []))
    self.group_wakeup_interest_probability = self._cfg_int(c, "group_wakeup_interest_probability", 18, 0, 100) / 100
    self.enable_group_wakeup_question = self._cfg_bool(c, "enable_group_wakeup_question", True)
    self.group_wakeup_question_threshold = self._cfg_int(c, "group_wakeup_question_threshold", 65, 0, 100)
    self.enable_group_wakeup_cold_group = self._cfg_bool(c, "enable_group_wakeup_cold_group", False)
    self.group_wakeup_cold_group_threshold = self._cfg_int(c, "group_wakeup_cold_group_threshold", 65, 0, 100)
    self.group_wakeup_cold_group_idle_minutes = self._cfg_int(c, "group_wakeup_cold_group_idle_minutes", 25, 3, 720)
    self.group_wakeup_cooldown_seconds = self._cfg_int(c, "group_wakeup_cooldown_seconds", 90, 0, 3600)
    self.group_wakeup_generated_keyword_limit = self._cfg_int(c, "group_wakeup_generated_keyword_limit", 24, 4, 80)
    self.group_wakeup_topic_interest_max_boost = self._cfg_int(c, "group_wakeup_topic_interest_max_boost", 45, 0, 150) / 100
    self.group_wakeup_debounce_pending_penalty = self._cfg_int(c, "group_wakeup_debounce_pending_penalty", 65, 0, 100) / 100
    self.group_wakeup_fatigue_limit = self._cfg_int(c, "group_wakeup_fatigue_limit", 5, 1, 20)
    self.group_wakeup_fatigue_decay_minutes = self._cfg_int(c, "group_wakeup_fatigue_decay_minutes", 90, 5, 720)
    self.group_wakeup_log_limit = self._cfg_int(c, "group_wakeup_log_limit", 80, 10, 300)
    self.group_wakeup_short_text_wait_seconds = self._cfg_float(c, "group_wakeup_short_text_wait_seconds", 15.0, 0.0)
    self.enable_group_high_intensity_mode = self._cfg_bool(c, "enable_group_high_intensity_mode", True)
    self.group_high_intensity_wakeup_window_seconds = self._cfg_int(c, "group_high_intensity_wakeup_window_seconds", 60, 15, 600)
    self.group_high_intensity_wakeup_threshold = self._cfg_int(c, "group_high_intensity_wakeup_threshold", 3, 2, 20)
    self.group_high_intensity_cooldown_seconds = self._cfg_int(c, "group_high_intensity_cooldown_seconds", 150, 30, 1800)
    self.group_high_intensity_merge_seconds = self._cfg_int(c, "group_high_intensity_merge_seconds", 8, 1, 30)
    self.group_high_intensity_max_merge_messages = self._cfg_int(c, "group_high_intensity_max_merge_messages", 8, 0, 50)
    self.group_high_intensity_merge_scope = self._cfg_str(c, "group_high_intensity_merge_scope", "group", "group").lower()
    if self.group_high_intensity_merge_scope in {"sender", "same_sender", "same_user", "user"}:
        self.group_high_intensity_merge_scope = "same_user"
    elif self.group_high_intensity_merge_scope not in {"group", "same_user"}:
        self.group_high_intensity_merge_scope = "group"
    self.enable_group_interjection = self._cfg_bool(c, "enable_group_interjection", False)
    self.enable_group_repeat_follow = self._cfg_bool(c, "enable_group_repeat_follow", True)
    self.group_repeat_trigger_threshold = self._cfg_int(c, "group_repeat_trigger_threshold", 4, 3, 20)
    self.group_repeat_count_distinct_users_only = self._cfg_bool(c, "group_repeat_count_distinct_users_only", False)
    self.group_repeat_follow_probability = self._cfg_int(c, "group_repeat_follow_probability", 18, 0, 100) / 100
    self.group_repeat_interrupt_probability = self._cfg_int(c, "group_repeat_interrupt_probability", 10, 0, 100) / 100
    self.group_repeat_interrupt_probability_step = self._cfg_int(c, "group_repeat_interrupt_probability_step", 12, 0, 100) / 100
    self.group_repeat_interrupt_text = self._cfg_str(c, "group_repeat_interrupt_text", "禁止复读", "禁止复读")
    self.group_repeat_interrupt_image_path = self._cfg_str(c, "group_repeat_interrupt_image_path", "")
    self.group_interject_min_interval_minutes = self._cfg_int(c, "group_interject_min_interval_minutes", 180, 10, 1440)
    self.group_interject_max_daily = self._cfg_int(c, "group_interject_max_daily", 2, 0, 12)
    self.max_group_recent_messages = self._cfg_int(c, "max_group_recent_messages", 80, 20, 300)
    self.max_group_slang_terms = self._cfg_int(c, "max_group_slang_terms", 40, 8, 160)
    self.enable_group_topic_threads = self._cfg_bool(c, "enable_group_topic_threads", True)
    self.enable_group_episode_memory = self._cfg_bool(c, "enable_group_episode_memory", True)
    self.enable_group_interjection_feedback = self._cfg_bool(c, "enable_group_interjection_feedback", True)
    self.enable_group_slang_meanings = self._cfg_bool(c, "enable_group_slang_meanings", True)
    self.enable_group_slang_web_search = self._cfg_bool(c, "enable_group_slang_web_search", False)
    self.group_slang_web_search_terms = self._cfg_int(c, "group_slang_web_search_terms", 4, 1, 12)
    self.group_slang_web_search_results = self._cfg_int(c, "group_slang_web_search_results", 2, 1, 5)
    self.enable_group_relationship_graph = self._cfg_bool(c, "enable_group_relationship_graph", True)
    self.enable_group_privacy_guard = self._cfg_bool(c, "enable_group_privacy_guard", True)
    self.enable_group_third_party_portrait_guard = self._cfg_bool(
        c, "enable_group_third_party_portrait_guard", True
    )
    self.enable_worldbook_member_recognition = self._cfg_bool(c, "enable_worldbook_member_recognition", True)
    self.enable_atrelay_tools = self._cfg_bool(c, "enable_atrelay_tools", True)
    self.enable_cross_user_memory_bridge = self._cfg_bool(c, "enable_cross_user_memory_bridge", False)
    self.cross_user_memory_owner_only = self._cfg_bool(c, "cross_user_memory_owner_only", True)
    self.atrelay_require_worldbook_first = self._cfg_bool(c, "atrelay_require_worldbook_first", True)
    self.atrelay_member_cache_minutes = self._cfg_int(c, "atrelay_member_cache_minutes", 60, 1, 1440)
    self.atrelay_sensitive_confirm = self._cfg_bool(c, "atrelay_sensitive_confirm", True)
    self.enable_atrelay_llm_rewrite = self._cfg_bool(c, "enable_atrelay_llm_rewrite", True)
    self.atrelay_default_relay_style = self._cfg_str(c, "atrelay_default_relay_style", "persona", "persona")
    self.atrelay_multi_target_limit = self._cfg_int(c, "atrelay_multi_target_limit", 5, 1, 20)
    self.worldbook_auto_import = self._cfg_bool(c, "worldbook_auto_import", True)
    self.worldbook_member_match_aliases = self._cfg_bool(c, "worldbook_member_match_aliases", True)
    self.worldbook_self_registration = self._cfg_bool(c, "worldbook_self_registration", True)
    self.worldbook_self_registration_block_words = self._parse_text_list_config(
        self._cfg_raw(c, "worldbook_self_registration_block_words", []),
        limit=120,
    )
    self.worldbook_self_registration_block_reply = self._cfg_str(
        c,
        "worldbook_self_registration_block_reply",
        "这个称呼我不记。",
    )
    if self.worldbook_self_registration_block_reply in {"这个称呼我先不记。", "你是小猪"}:
        self.worldbook_self_registration_block_reply = "这个称呼我不记。"
        _set_into_config(c, "worldbook_self_registration_block_reply", self.worldbook_self_registration_block_reply)
    self.worldbook_auto_pending_observations = self._cfg_bool(c, "worldbook_auto_pending_observations", True)
    self.worldbook_member_inject_limit = self._cfg_int(c, "worldbook_member_inject_limit", 6, 1, 20)
    self.worldbook_config_paths = self._cfg_str(c, "worldbook_config_paths", "")
    self.group_interject_provider_id = self._cfg_str(c, "GROUP_INTERJECT_PROVIDER_ID", "")
    self.group_episode_provider_id = self._cfg_str(c, "GROUP_EPISODE_PROVIDER_ID", "")
    self.group_slang_provider_id = self._cfg_str(c, "GROUP_SLANG_PROVIDER_ID", "")
    self.group_followup_judge_provider_id = self._cfg_str(c, "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID", "")
    self.group_member_safety_provider_id = self._cfg_str(c, "GROUP_MEMBER_SAFETY_PROVIDER_ID", "")
    self.enable_livingmemory_integration = self._cfg_bool(c, "enable_livingmemory_integration", True)
    self.livingmemory_tool_name = self._cfg_str(c, "livingmemory_tool_name", "recall_long_term_memory", "recall_long_term_memory")
    self.memory_companion_context_timeout_seconds = self._cfg_float(c, "memory_companion_context_timeout_seconds", 1.2, 0.2)
    self.enable_memory_companion_emotional_drift = self._cfg_bool(c, "enable_memory_companion_emotional_drift", True)
    self.enable_memory_companion_cross_window_emotion = self._cfg_bool(c, "enable_memory_companion_cross_window_emotion", True)
    self.enable_memory_companion_dream_fragment = self._cfg_bool(c, "enable_memory_companion_dream_fragment", True)
    self.enable_memory_companion_open_loop_search = self._cfg_bool(c, "enable_memory_companion_open_loop_search", True)
    self.enable_memory_companion_feature_context = self._cfg_bool(c, "enable_memory_companion_feature_context", True)
    self.enable_memory_companion_private_recall = self._cfg_bool(c, "enable_memory_companion_private_recall", True)
    self.memory_companion_context_top_k = self._cfg_int(c, "memory_companion_context_top_k", 5, 1, 10)
    self.memory_companion_context_max_chars = self._cfg_int(c, "memory_companion_context_max_chars", 900, 240, 1800)
    self.enable_memory_companion_local_fallback = self._cfg_bool(c, "enable_memory_companion_local_fallback", True)
    self.memory_companion_local_fallback_max_chars = self._cfg_int(c, "memory_companion_local_fallback_max_chars", 600, 240, 1200)
    self.enable_bilibili_integration = self._cfg_bool(c, "enable_bilibili_integration", True)
    self.enable_bilibili_boredom_watch = self._cfg_bool(c, "enable_bilibili_boredom_watch", True)
    self.bilibili_boredom_min_interval_hours = self._cfg_int(c, "bilibili_boredom_min_interval_hours", 8, 2, 72)
    self.bilibili_share_probability = self._cfg_unit_interval(c, "bilibili_share_probability", 0.35, 0.0)
    self.bilibili_share_min_score = self._cfg_int(c, "bilibili_share_min_score", 7, 0, 10)
    self.enable_news_integration = self._cfg_bool(c, "enable_news_integration", False)
    self.enable_news_boredom_read = self._cfg_bool(c, "enable_news_boredom_read", True)
    self.enable_news_daily_hot_read = self._cfg_bool(c, "enable_news_daily_hot_read", self._cfg_bool(c, "enable_hot_trend_sources", True))
    self.news_min_interval_hours = self._cfg_int(c, "news_min_interval_hours", 6, 1, 72)
    self.news_share_probability = self._cfg_unit_interval(c, "news_share_probability", 0.22, 0.0)
    self.enable_external_event_self_link = self._cfg_bool(c, "enable_external_event_self_link", True)
    self.external_event_self_link_probability = self._cfg_unit_interval(c, "external_event_self_link_probability", 0.62, 0.0)
    self.external_event_self_link_cooldown_hours = self._cfg_int(c, "external_event_self_link_cooldown_hours", 12, 1, 168)
    self.external_link_share_cooldown_hours = self._cfg_int(c, "external_link_share_cooldown_hours", 72, 0, 168)
    self.news_max_items_per_source = self._cfg_int(c, "news_max_items_per_source", 5, 1, 20)
    self.external_event_idle_minutes = self._cfg_int(c, "external_event_idle_minutes", 90, 5, 1440)
    self.external_event_idle_strong_minutes = self._cfg_int(c, "external_event_idle_strong_minutes", 20, 1, 1440)
    self.news_share_cooldown_hours = self._cfg_int(c, "news_share_cooldown_hours", 8, 0, 168)
    self.bilibili_share_cooldown_hours = self._cfg_int(c, "bilibili_share_cooldown_hours", 10, 0, 168)
    self.web_exploration_share_cooldown_hours = self._cfg_int(c, "web_exploration_share_cooldown_hours", 10, 0, 168)
    self.external_event_self_link_override_min_relevance = self._cfg_int(c, "external_event_self_link_override_min_relevance", 0, 0, 10)
    self.external_event_self_link_override_min_desire = self._cfg_int(c, "external_event_self_link_override_min_desire", 0, 0, 10)
    self.external_event_self_link_override_probability = self._cfg_unit_interval(c, "external_event_self_link_override_probability", 0.6, 0.0)
    self.external_event_share_min_total = self._cfg_int(c, "external_event_share_min_total", 18, 0, 100)
    self.news_hot_sources = self._cfg_str(c, "news_hot_sources", self._cfg_str(c, "hot_trend_sources", "weibo,hackernews"))
    self.news_hot_max_items = self._cfg_int(c, "news_hot_max_items", self._cfg_int(c, "hot_trend_max_items", 12, 3, 30), 3, 30)
    self.enable_ai_daily_watch = self._cfg_bool(c, "enable_ai_daily_watch", True)
    self.ai_daily_sources = self._cfg_str(c, "ai_daily_sources", DEFAULT_AI_DAILY_SOURCES)
    self.ai_daily_source_uid = re.sub(r"\D+", "", self._cfg_str(c, "ai_daily_source_uid", "285286947")) or "285286947"
    self.ai_daily_prefer_text_version = self._cfg_bool(c, "ai_daily_prefer_text_version", True)
    self.news_sources = self._cfg_str(
        c,
        "news_sources",
        DEFAULT_NEWS_SOURCES,
    )
    if str(self.news_sources or "").strip() in {LEGACY_DEFAULT_NEWS_SOURCES, PREVIOUS_TECH_DEFAULT_NEWS_SOURCES}:
        self.news_sources = DEFAULT_NEWS_SOURCES
    self.news_provider_id = self._cfg_str(c, "NEWS_PROVIDER_ID", "")
    self.enable_web_exploration = self._cfg_bool(c, "enable_web_exploration", False)
    self.enable_web_exploration_boredom_search = self._cfg_bool(c, "enable_web_exploration_boredom_search", True)
    self.web_exploration_min_interval_hours = self._cfg_int(c, "web_exploration_min_interval_hours", 8, 1, 168)
    self.web_exploration_share_probability = self._cfg_unit_interval(c, "web_exploration_share_probability", 0.18, 0.0)
    self.web_exploration_max_results = self._cfg_int(c, "web_exploration_max_results", 6, 3, 20)
    self.web_exploration_interests = self._cfg_str(
        c,
        "web_exploration_interests",
        "按 Bot 人格自行决定；可偏向最近聊天、日程、人设兴趣、作品、技术、生活小知识、流行梗、时讯、新鲜事物。",
    )
    self.web_exploration_provider_id = self._cfg_str(c, "WEB_EXPLORATION_PROVIDER_ID", "")
    self.web_exploration_api_base_url = self._cfg_str(c, "WEB_EXPLORATION_API_BASE_URL", "")
    self.web_exploration_api_key = self._cfg_str(c, "WEB_EXPLORATION_API_KEY", "")
    self.web_exploration_api_model = self._cfg_str(c, "WEB_EXPLORATION_API_MODEL", "")
    self.enable_qzone_integration = self._cfg_bool(c, "enable_qzone_integration", True)
    self.qzone_cookie = self._cfg_str(c, "QZONE_COOKIE", "")
    self.enable_qzone_life_publish = self._cfg_bool(c, "enable_qzone_life_publish", False)
    self.qzone_life_publish_min_interval_hours = self._cfg_int(c, "qzone_life_publish_min_interval_hours", 24, 4, 168)
    self.qzone_life_publish_probability = self._cfg_unit_interval(c, "qzone_life_publish_probability", 0.18, 0.0)
    self.qzone_life_publish_max_daily = self._cfg_int(c, "qzone_life_publish_max_daily", 1, 1)
    self.qzone_life_publish_window_mode = self._cfg_str(c, "qzone_life_publish_window_mode", "template_double")
    self.qzone_life_publish_windows = self._cfg_str(c, "qzone_life_publish_windows", "")
    self.qzone_life_publish_allow_insomnia_night = self._cfg_bool(
        c,
        "qzone_life_publish_allow_insomnia_night",
        False,
    )
    self.qzone_life_publish_intra_day_gap_minutes = self._cfg_int(
        c,
        "qzone_life_publish_intra_day_gap_minutes",
        45,
        0,
        1440,
    )
    self.qzone_life_publish_double_windows = self._cfg_str(
        c,
        "qzone_life_publish_double_windows",
        "07:00-10:00\n18:00-22:00",
    )
    self.qzone_life_publish_custom_windows = self._cfg_str(c, "qzone_life_publish_custom_windows", "")
    self.qzone_life_publish_similarity_threshold = self._cfg_int(c, "qzone_life_publish_similarity_threshold", 2, 1, 20)
    self.qzone_publish_style_prompt = self._cfg_str(c, "qzone_publish_style_prompt", "")
    self.enable_qzone_generated_image_publish = self._cfg_bool(c, "enable_qzone_generated_image_publish", True)
    self.qzone_generated_image_probability = self._cfg_unit_interval(c, "qzone_generated_image_probability", 0.25, 0.0)
    self.qzone_publish_image_style_prompt = self._cfg_str(c, "qzone_publish_image_style_prompt", "")
    self.enable_qzone_comment_inbox = self._cfg_bool(c, "enable_qzone_comment_inbox", False)
    self.qzone_comment_inbox_interval_minutes = self._cfg_int(c, "qzone_comment_inbox_interval_minutes", 60, 5, 1440)
    self.qzone_comment_inbox_recent_posts = self._cfg_int(c, "qzone_comment_inbox_recent_posts", 5, 1, 20)
    self.qzone_comment_inbox_max_replies_per_tick = self._cfg_int(c, "qzone_comment_inbox_max_replies_per_tick", 1, 1, 5)
    self.enable_qzone_emotional_vent_publish = self._cfg_bool(c, "enable_qzone_emotional_vent_publish", False)
    self.qzone_emotional_vent_threshold = self._cfg_int(c, "qzone_emotional_vent_threshold", 90, 40, 100)
    self.qzone_emotional_vent_cooldown_hours = self._cfg_int(c, "qzone_emotional_vent_cooldown_hours", 72, 4, 336)
    self.qzone_emotional_vent_probability = self._cfg_unit_interval(c, "qzone_emotional_vent_probability", 0.35, 0.0)
    self.plugin_vision_provider_id = self._cfg_str(c, "PLUGIN_VISION_PROVIDER_ID", "")
    self._apply_quick_provider_defaults()
    self.group_episode_refresh_minutes = self._cfg_int(c, "group_episode_refresh_minutes", 180, 30, 1440)
    self.group_slang_summary_minutes = self._cfg_int(c, "group_slang_summary_minutes", 360, 60, 2880)
    self.max_group_topic_threads = self._cfg_int(c, "max_group_topic_threads", 12, 3, 40)
    self.max_group_episodes = self._cfg_int(c, "max_group_episodes", 10, 3, 40)
    self.max_group_relationship_edges = self._cfg_int(c, "max_group_relationship_edges", 80, 10, 300)
    # Backward-compatible aliases for stored daily plans and older code paths.
    self.allow_photo_text_action = self.enable_photo_text_action
    self.allow_screen_peek_action = self.enable_screen_glance_action
    self.allow_poke_action = self.enable_poke_action
    self.allow_voice_action = self.enable_voice_action

@story_startup_sync_operation("startup.store-persona-load")
def initialize_plugin_runtime(self: Any) -> None:
    # These references are process-local capabilities. Never inherit them from
    # mixin class attributes or a previous hot-reloaded plugin instance.
    self._bridge_cache = None
    self._bridge_cache_ts = 0.0
    self._bridge_last_status = {}
    self._bridge_dependency_failure_until = 0.0
    self._bridge_dependency_failure_module = ""
    self._memory_companion_emotion_capability_bridge = None
    self._memory_companion_emotion_producer_capability_cache = None
    self._patch_livingmemory_processor_compat()
    self._report_integrated_feature_conflicts()
    self._data_lock = asyncio.Lock()
    self._daily_state_generation_lock = asyncio.Lock()
    self._daily_diary_generation_lock = asyncio.Lock()
    self._daily_review_generation_lock = asyncio.Lock()
    self._conversation_db_lock = asyncio.Lock()
    self._framework_agent_lock = asyncio.Lock()
    self._stop_event = asyncio.Event()
    self._task: asyncio.Task | None = None
    self._default_persona_prompt_cache = ""
    self._default_persona_prompt_cache_at = 0.0
    self._default_persona_prompt_cache_umo = ""
    self._default_persona_prompt_cache_persona_id = ""
    self._default_persona_prompt_refresh_task: asyncio.Task | None = None
    self._default_persona_prompt_cache_by_scope: dict[str, dict[str, Any]] = {}
    self._default_persona_prompt_refresh_tasks: dict[str, asyncio.Task] = {}
    self._passive_light_injection_cache: dict[str, Any] = {}
    self._passive_state_session_cache: dict[str, dict[str, Any]] = {}
    self._data_save_task: asyncio.Task | None = None
    self._data_save_dirty: dict[str, int] = {}
    self._data_save_deleted: dict[str, int] = {}
    self._data_save_dirty_since: dict[str, float] = {}
    self._data_save_section_revisions: dict[str, int] = {}
    self._data_save_full_revision = 0
    self._data_save_full_since = 0.0
    self._data_save_revision = 0
    self._data_save_max_delay_seconds = 2.0
    self._data_save_retry_base_seconds = 3.0
    self._data_save_retry_max_seconds = 30.0
    self._persona_data_save_tasks: dict[str, asyncio.Task] = {}
    self._persona_data_save_dirty: dict[str, dict[str, int]] = {}
    self._persona_data_save_deleted: dict[str, dict[str, int]] = {}
    self._persona_data_save_dirty_since: dict[str, dict[str, float]] = {}
    self._persona_data_save_section_revisions: dict[str, dict[str, int]] = {}
    self._persona_data_save_full_revision: dict[str, int] = {}
    self._persona_data_save_revision: dict[str, int] = {}
    self._maintenance_failure_cooldowns: dict[str, dict[str, Any]] = {}
    self._framework_captured_send_cache: dict[str, list[Any]] = {}
    self._framework_captured_send_cache_at: dict[str, float] = {}
    self._framework_deferred_photo_cache: dict[str, dict[str, Any]] = {}
    self._framework_deferred_photo_cache_at: dict[str, float] = {}
    self._segmented_reply_remainder_locks: dict[str, asyncio.Lock] = {}
    self._last_input_status_at: dict[str, float] = {}
    self._passive_input_status_tasks: dict[str, asyncio.Task] = {}
    self._recent_inbound_activity_by_scope: dict[str, dict[str, Any]] = {}
    # Monotonic per-conversation turn marker used to stop delayed TTS/remainder
    # tasks from delivering content belonging to an older inbound message.
    self._reply_turn_generation_by_scope: dict[str, int] = {}
    self._recent_outfit_command_sends: dict[str, float] = {}
    self._startup_maintenance_task: asyncio.Task | None = None
    self._startup_background_tasks: dict[str, asyncio.Task] = {}
    self._lifecycle_background_tasks: dict[asyncio.Task, str] = {}
    self._group_image_understanding_tasks: dict[str, dict[str, Any]] = {}
    self._qzone_last_bot = None
    startup_load_started = time.perf_counter()
    self.data = self._load_data_sync()
    timezone_reconciler = getattr(self, "_invalidate_timezone_derived_state", None)
    if callable(timezone_reconciler):
        runtime_state = (
            self.data.get("proactive_runtime")
            if isinstance(self.data.get("proactive_runtime"), dict)
            else {}
        )
        timezone_result = timezone_reconciler(
            str(runtime_state.get("window_timezone") or ""),
            str(getattr(self, "environment_perception_timezone", "") or ""),
            schedule_save=False,
        )
        timezone_sections = set(timezone_result.get("sections") or [])
        if timezone_result.get("changed") and timezone_sections:
            self._save_data_sync(sections=timezone_sections)
    manager = getattr(self, "store_manager", None)
    next_revision = getattr(manager, "next_revision", None)
    if callable(next_revision):
        try:
            self._data_save_revision = max(
                int(self._data_save_revision or 0),
                max(0, int(next_revision()) - 1),
            )
        except Exception:
            pass
    retire_legacy_routing = getattr(self, "_retire_legacy_persona_routing_sync", None)
    if callable(retire_legacy_routing):
        try:
            retire_legacy_routing()
        except Exception as exc:
            logger.warning(
                "旧人格路由停用失败，保留原数据等待下次启动重试: %s",
                _single_line(exc, 180),
            )
    migrate_profiles = getattr(self, "_migrate_persona_profiles_sync", None)
    if callable(migrate_profiles):
        try:
            self._persona_settings_migration_status = migrate_profiles()
        except Exception as exc:
            self._persona_settings_migration_status = {
                "ok": False,
                "migrated": [],
                "degraded": ["startup"],
                "skipped": [],
                "error": _single_line(exc, 180),
            }
            logger.warning(
                "启动人格配置迁移失败: %s",
                _single_line(exc, 180),
            )
    self._body_monitor_integration = BodyMonitorIntegration(self)
    self._apply_tts_runtime_overrides()
    load_elapsed_ms = int((time.perf_counter() - startup_load_started) * 1000)
    if load_elapsed_ms > 1200:
        logger.warning("启动读取数据耗时较高: elapsed=%sms", load_elapsed_ms)
    self._proactive_chat_runtime_bridge = ProactiveChatRuntimeBridge(self)
    self.page_api = None
    self.standalone_webui = None
    self._patch_astrbot_plugin_page_asset_token_compat()
    self._register_page_api_if_available()


def initialize_plugin_post_runtime_state(self: Any, config: Any) -> None:
    self.enable_p5_source_observer = self._cfg_bool(config, "enable_p5_source_observer", False)
    self.enable_p5_b1_recall_gate = self._cfg_bool(config, "enable_p5_b1_recall_gate", False)
    self.enable_p5_b1_bridge_gate = self._cfg_bool(config, "enable_p5_b1_bridge_gate", False)
    self.p5_attestation_registry = P5AttestationRegistry()
    self._bot_personal_outboxes = {}
    self._bot_personal_outbox = None
    outbox_getter = getattr(self, "_memory_companion_outbox", None)
    if callable(outbox_getter):
        self._bot_personal_outbox = outbox_getter()
    self.unified_person_registry = UnifiedPersonRegistry(self.data)
    self.req041_migration_coordinator = MigrationCoordinator(self.data_dir)
    self.req041_migration_outbox = MigrationOutbox(
        Path(self.data_dir) / "req041_migration_outbox.db"
    )
    self.req041_migration_status = {
        "required": False,
        "state": "uninitialized",
        "code": "migration_not_started",
    }
    self.req041_migration_backfill = None
    self.req041_relationship_store = None
    self.req041_dual_write_producer = None
    self.req041_scoped_projection_sync = None
    self.req041_scoped_projection_status = {
        "ok": False, "code": "scoped_projection_not_initialized", "scopes": []
    }
    self._req041_scoped_sync_task = None
    self._req041_scoped_sync_requested = False
