# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import functools
import io
import json
import math
import os
import time
import re
import shutil
import base64
import binascii
import hmac
import hashlib
import mimetypes
import secrets
import sqlite3
import sys
import uuid
from contextlib import asynccontextmanager
from copy import copy, deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping
from urllib.parse import quote, urlparse

from astrbot.api.event import MessageChain
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from quart import request, send_file


def _multi_persona_page_context(function):
    @functools.wraps(function)
    async def wrapper(self, *args, **kwargs):
        plugin = getattr(self, "plugin", None)
        activator = getattr(plugin, "_activate_persona_id", None)
        primary_getter = getattr(plugin, "_primary_persona_id", None)
        pid = primary_getter() if callable(primary_getter) else ""
        active_getter = getattr(plugin, "_active_persona_scope", None)
        active = str(active_getter() if callable(active_getter) else "").strip()
        token = (
            activator(pid, allow_inactive=True)
            if not active and callable(activator) and pid
            else None
        )
        try:
            return await function(self, *args, **kwargs)
        finally:
            deactivator = getattr(plugin, "_deactivate_persona_for_event", None)
            if token is not None and callable(deactivator):
                deactivator(token)
    return wrapper

from .constants import (
    DEFAULT_DAILY_PLAN_ITEMS,
    PAGE_FONT_NAMES,
    PAGE_THEME_NAMES,
    WORLDBOOK_IMPORTANT_MEMORY_CAPACITY,
    WORLDBOOK_PENDING_OBSERVATION_CAPACITY,
    _REASON_TEXT,
)
from .config_migration import _config_root_mapping, _ensure_config_parent_dir
from .conversation_prompt_section import (
    PromptRenderMode,
    prompt_document,
    prompt_heading_ref,
    prompt_section,
    render_prompt_content,
    render_prompt_document,
    render_prompt_sections,
)
from .diagnostic_envelope import DIAGNOSTIC_ENVELOPE_VERSION, diagnostic_test_id, normalize_diagnostic_result
from .helpers import _MISSING, _flat_get, _normalize_timezone_name, _normalize_timezone_setting, _path_text, _redact_outbound_secrets, _safe_int, _set_into_config, _strip_internal_message_blocks, _text_looks_garbled, _text_similarity, _today_key, normalize_bot_relationship_cards
from .persona_config import runtime_persona_setting
from .wardrobe import WARDROBE_MAX_DESCRIPTION, WARDROBE_MAX_NAME, WARDROBE_MAX_TAG
from .wardrobe_assets import asset_abs_path, asset_root, load_asset_index
from .story_authority import (
    StoryAuthorityError,
    story_authority_controller,
    story_legacy_operation,
    story_legacy_operation_if,
)
from .reference_asset_gate import ReferenceAssetGate
from .owned_reaction_asset_catalog import MAX_ASSET_BYTES, OwnedReactionAssetCatalog
from .companion_interaction_expression import current_interaction_projection, normalize_normal_interaction_band_cap
from .expression_scope_ownership import (
    ExpressionScopeError,
    bind_expression_item,
    bind_expression_profile,
    validate_expression_scope_binding,
)
from .relationship_ledger import (
    migrate_legacy_relationship_score,
    migrate_relationship_positive_stage_cap,
    normalize_relationship_mode,
    normalize_relationship_positive_stage_cap_key,
    relationship_ledger_summary,
)
from .relationship_policy import (
    normalize_relationship_stage_provider_routes,
    normalize_relationship_stage_policy,
    relationship_stage_for_score,
    relationship_stage_policy_json,
)
from .runtime_config_dispatcher import (
    TTS_RUNTIME_KEYS,
    dispatch_runtime_config_effects,
)
from .page_api_qzone import PrivateCompanionPageApiQzoneMixin
from .page_api_users_groups import PrivateCompanionPageApiUsersGroupsMixin
from .page_api_persona import PrivateCompanionPageApiPersonaMixin
from .page_api_expression import PrivateCompanionPageApiExpressionMixin
from .page_api_worldbook import PrivateCompanionPageApiWorldbookMixin
from .page_api_diagnostics import PrivateCompanionPageApiDiagnosticsMixin
from .page_api_migration import PrivateCompanionPageApiMigrationMixin
from .page_api_proactive import PrivateCompanionPageApiProactiveMixin
from .page_api_media import PrivateCompanionPageApiMediaMixin
from .page_api_settings import PageSettingNormalizerMixin
from .model_routing import build_rules, normalize_scope
from .planning import evaluate_daily_plan_quality, generate_daily_plan, generate_detail_enhancement
from .agenda_contracts import timezone_or_default
from .calendar_contracts import (
    AgendaContractError,
    calendar_lifecycle_summary,
    normalize_calendar_record,
    normalize_calendar_records,
    resolve_calendar_timeline,
    resolve_calendar_snapshot,
)
from .memo_notes import (
    apply_memo_note_action,
    memo_note_due_state,
    memo_note_sort_key,
    normalize_memo_note,
)
from .photo_reference_catalog import (
    CATALOG_VERSION,
    MAX_LIBRARY_REFERENCES,
    CatalogValidationError,
    PhotoReference,
    load_catalog,
    project_reference_candidate,
    validate_and_serialize,
)
from .photo_reference_metadata import (
    build_reference_metadata_review_prompt,
    compile_reference_metadata,
    merge_reference_questionnaire_evidence,
    normalize_reviewed_reference_intent,
)
from .photo_reference_selection import SelectionResult, run_photo_selection_trial
from .reference_assets import (
    REFERENCE_ASSET_MAX_BYTES,
    REFERENCE_ASSET_MAX_PER_OWNER,
    REFERENCE_ASSET_MAX_TOTAL,
    REFERENCE_ASSET_ROLES,
    normalize_reference_asset,
    normalize_reference_asset_scope,
    normalize_reference_owner_id,
)
from .reaction_asset_library import get_reaction_asset_library
from .logging_util import get_module_logger
from .page_api_admin_token import PrivateCompanionPageApiAdminTokenMixin
from .page_api_social_group import PrivateCompanionPageApiSocialGroupMixin
from .page_api_food_body import PrivateCompanionPageApiFoodBodyMixin
from .page_api_memory_recall import PrivateCompanionPageApiMemoryRecallMixin
from .page_api_calendar_daily import PrivateCompanionPageApiCalendarDailyMixin
from .page_api_creative import PrivateCompanionPageApiCreativeMixin
from .page_api_creative import _render_page_background_prompt, _render_page_background_prompt_pair  # noqa: F401 (兼容 page_api._render_page_background_prompt* 旧命名空间)
from .page_api_summary_panel import PrivateCompanionPageApiSummaryPanelMixin
from .page_api_bookshelf import PrivateCompanionPageApiBookshelfMixin
from .page_api_bookshelf import BOOKSHELF_ACCESS_TOKEN_MAX_PERSISTED, BOOKSHELF_ACCESS_TOKEN_TTL_SECONDS  # noqa: F401 (兼容 from page_api import BOOKSHELF_*)
from .page_api_tts import PrivateCompanionPageApiTtsMixin
from .page_api_config import PrivateCompanionPageApiConfigMixin
from .page_api_daily_review import PrivateCompanionPageApiDailyReviewMixin
from .page_api_bookshelf_remaining import PrivateCompanionPageApiBookshelfRemainingMixin
from .page_api_reality_touch import PrivateCompanionPageApiRealityTouchMixin
from .page_api_message_display import PrivateCompanionPageApiMessageDisplayMixin
from .page_api_util_small import PrivateCompanionPageApiUtilSmallMixin
from .page_api_wardrobe_page import PrivateCompanionPageApiWardrobePageMixin
from .page_api_plugin_meta import PrivateCompanionPageApiPluginMetaMixin
from .page_api_config_runtime import PrivateCompanionPageApiConfigRuntimeMixin
from .page_api_config_schema import PrivateCompanionPageApiConfigSchemaMixin
from .page_api_provider_binding import PrivateCompanionPageApiProviderBindingMixin
from .page_api_reaction_library import PrivateCompanionPageApiReactionLibraryMixin
from .page_api_task_prompt import PrivateCompanionPageApiTaskPromptMixin
from .page_api_setup_generation import PrivateCompanionPageApiSetupGenerationMixin
from .page_api_external_api_test import PrivateCompanionPageApiExternalApiTestMixin
from .page_api_chain_test import PrivateCompanionPageApiChainTestMixin
from .page_api_debug_payload import PrivateCompanionPageApiDebugPayloadMixin
from .page_api_overview_limit import PrivateCompanionPageApiOverviewLimitMixin
from .page_api_normalize_text import PrivateCompanionPageApiNormalizeTextMixin
from .page_backend import MigrationBackupService, build_route_bindings, generation_log_candidates
from .task_prompt_registry import (
    TASK_PROMPT_CONFIG_KEY,
    TASK_PROMPT_GROUPS,
    catalog_task_prompts,
    normalize_task_prompt_overrides,
    validate_task_prompt_override,
)

logger = get_module_logger(__name__)



PLUGIN_NAME = "astrbot_plugin_private_companion"
PAGE_API_PREFIX = f"/{PLUGIN_NAME}/page"
_MIGRATION_UNKNOWN_CONFIG_KEY = "_migration_unknown_config_fields_v1"
_MIGRATION_UNKNOWN_NAMESPACES = ("settings", "features", "providers")
_MIGRATION_UNKNOWN_MAX_BYTES = 256 * 1024
_MIGRATION_UNKNOWN_MAX_FIELDS = 128
_MIGRATION_UNKNOWN_SENSITIVE_NAME = re.compile(
    r"(?:access[_-]?token|password|secret|cookie|api[_-]?key|storage[_-])",
    flags=re.I,
)
EXTENSION_MIGRATION_NOTICE_VERSION = "6.2.2"
IMAGE_CACHE_THUMBNAIL_MAX_EDGE = 160
IMAGE_CACHE_THUMBNAIL_QUALITY = 78
PHOTO_REFERENCE_PREVIEW_MAX_BYTES = 20 * 1024 * 1024
# The guided editor must never leave a WebUI request waiting forever when the
# configured main model or its upstream connection stops responding.
PHOTO_REFERENCE_METADATA_REVIEW_TIMEOUT_SECONDS = 60.0
# Reference assets are an independent store for member/role/knowledge images. Keep
# the limits generous enough for a small visual knowledge base while preventing
# an accidental page upload from exhausting the plugin data directory.
PHOTO_REFERENCE_ASSET_MAX_BYTES = 12 * 1024 * 1024
PHOTO_REFERENCE_ASSET_MAX_COUNT = 256
PHOTO_REFERENCE_ASSET_MAX_PER_OWNER = 32
# WebUI catalog uploads are content-addressed so repeated submissions do not
# create another copy of the same image. These limits cover abandoned uploads
# that are no longer referenced by the saved catalog.
PHOTO_REFERENCE_UPLOAD_MAX_COUNT = 256
PHOTO_REFERENCE_UPLOAD_MAX_TOTAL_BYTES = 1024 * 1024 * 1024
# A 12 MiB image expands to roughly 16 MiB when Base64 encoded. Leave room for
# the data URL and JSON envelope, while rejecting oversized bodies before
# Quart parses them into memory.
PHOTO_REFERENCE_UPLOAD_MAX_REQUEST_BYTES = 20 * 1024 * 1024
PHOTO_REFERENCE_ASSET_SCOPES = {"relation_user", "group", "knowledge"}
PHOTO_REFERENCE_ASSET_MIMES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

# These values configure the page/API trust boundary itself. They must remain
# available to AstrBot's native config editor, but must never be projected back
# through the companion panel or accepted by its migration importer.


# Keep the cycle editor contract explicit.  These settings live inside the
# humanized-state schema group, but the page API must remain usable when an
# older AstrBot process has not rebuilt its schema index yet.
try:
    from PIL import Image as PILImage
    from PIL import ImageOps as PILImageOps
except Exception:  # pragma: no cover - Pillow 缺失时回退到原图预览
    PILImage = None
    PILImageOps = None


class PrivateCompanionPageApi(
    PageSettingNormalizerMixin,
    PrivateCompanionPageApiQzoneMixin,
    PrivateCompanionPageApiUsersGroupsMixin,
    PrivateCompanionPageApiMediaMixin,
    PrivateCompanionPageApiPersonaMixin,
    PrivateCompanionPageApiExpressionMixin,
    PrivateCompanionPageApiWorldbookMixin,
    PrivateCompanionPageApiDiagnosticsMixin,
    PrivateCompanionPageApiMigrationMixin,
    PrivateCompanionPageApiProactiveMixin,
    PrivateCompanionPageApiConfigMixin,
    PrivateCompanionPageApiTtsMixin,
    PrivateCompanionPageApiBookshelfMixin,
    PrivateCompanionPageApiSummaryPanelMixin,
    PrivateCompanionPageApiCreativeMixin,
    PrivateCompanionPageApiCalendarDailyMixin,
    PrivateCompanionPageApiMemoryRecallMixin,
    PrivateCompanionPageApiFoodBodyMixin,
    PrivateCompanionPageApiSocialGroupMixin,
    PrivateCompanionPageApiAdminTokenMixin,
    PrivateCompanionPageApiDailyReviewMixin,
    PrivateCompanionPageApiBookshelfRemainingMixin,
    PrivateCompanionPageApiRealityTouchMixin,
    PrivateCompanionPageApiMessageDisplayMixin,
    PrivateCompanionPageApiUtilSmallMixin,
    PrivateCompanionPageApiWardrobePageMixin,
    PrivateCompanionPageApiPluginMetaMixin,
    PrivateCompanionPageApiConfigRuntimeMixin,
    PrivateCompanionPageApiConfigSchemaMixin,
    PrivateCompanionPageApiProviderBindingMixin,
    PrivateCompanionPageApiReactionLibraryMixin,
    PrivateCompanionPageApiTaskPromptMixin,
    PrivateCompanionPageApiSetupGenerationMixin,
    PrivateCompanionPageApiExternalApiTestMixin,
    PrivateCompanionPageApiChainTestMixin,
    PrivateCompanionPageApiDebugPayloadMixin,
    PrivateCompanionPageApiOverviewLimitMixin,
    PrivateCompanionPageApiNormalizeTextMixin,
):
    """AstrBot 官方插件拓展页面 API。"""

    @staticmethod
    def _save_plugin_sections(plugin: Any, sections: set[str]) -> None:
        saver = getattr(plugin, "_save_data_sync", None)
        if not callable(saver):
            return
        saver(sections=sections)

    IMAGE_API_RUNTIME_SETTING_KEYS = {
        "external_image_api_platform",
        "EXTERNAL_IMAGE_API_BASE_URL",
        "EXTERNAL_IMAGE_API_KEY",
        "EXTERNAL_IMAGE_API_MODEL",
        "external_image_api_size",
        "external_image_api_timeout_seconds",
        "external_image_api_custom_headers",
        "external_image_download_proxy",
        "external_image_download_use_environment_proxy",
        "external_image_api_endpoints",
        "enable_backup_external_image_api",
        "backup_external_image_api_platform",
        "BACKUP_EXTERNAL_IMAGE_API_BASE_URL",
        "BACKUP_EXTERNAL_IMAGE_API_KEY",
        "BACKUP_EXTERNAL_IMAGE_API_MODEL",
        "backup_external_image_api_size",
        "backup_external_image_api_timeout_seconds",
        "backup_external_image_api_custom_headers",
    }

    PERCENT_PROBABILITY_KEYS = {
        "group_repeat_follow_probability",
        "group_repeat_interrupt_probability",
        "group_repeat_interrupt_probability_step",
        "group_wakeup_interest_probability",
        "group_wakeup_topic_interest_max_boost",
        "group_wakeup_debounce_pending_penalty",
        "tts_trigger_probability",
        "auto_voice_probability",
        "main_user_mention_voice_probability",
        "rest_reply_probability",
        "proactive_photo_text_probability",
        "proactive_share_probability",
    }
    INHERIT_PERCENT_PROBABILITY_KEYS = {
        "tts_private_trigger_probability",
        "tts_group_trigger_probability",
        "main_user_voice_probability",
    }
    FRACTIONAL_PERCENT_SETTING_KEYS = {
        "reaction_expression_trigger_probability",
        "reaction_expression_embedding_score_threshold",
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
        "smart_silence_min_confidence",
        "reading_archive_share_probability",
        "reading_archive_ask_probability",
        "creative_inspiration_probability",
        "creative_share_probability",
        "skill_growth_schedule_influence_strength",
    }
    PERSONALITY_AUTO_TUNE_KEYS = {
        "proactive_intensity_preset",
        "max_daily_messages",
        "idle_minutes",
        "min_interval_minutes",
        "proactive_persona_judge_send_threshold",
        "proactive_review_strength",
    }
    PERSONALITY_AUTO_TUNE_RECOVERY_STREAK = 3
    PERSONALITY_AUTO_TUNE_RECOVERY_MIN_SECONDS = 5 * 60
    TROUBLESHOOTING_PROACTIVE_SUMMARY_CACHE_SECONDS = 20.0

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self._schema_key_index_cache: dict[str, Any] | None = None
        self._proactive_task_summary_task: asyncio.Task[dict[str, Any]] | None = None
        self._proactive_task_summary_cache: dict[str, Any] = {}
        self._proactive_task_summary_cache_at = 0.0
        self._proactive_task_summary_cache_ready = False
        self._proactive_task_summary_generation = 0


    @staticmethod
    def _p4_page_status_projection() -> dict[str, Any]:
        """Expose only fixed P4 boundaries; never resolve a user or ledger."""
        return {
            "schema_version": "chat.p4.page_status.v1",
            "scope": "chat_event_only",
            "reply_gate": "host_verified_event_only",
            "warmth": "host_verified_event_only",
            "confinement": "not_exposed_to_page",
            "manual_review": "not_migrated",
            "action_available": False,
        }




    def _create_page_background_task(self, operation: Any, *, label: str) -> asyncio.Task | None:
        creator = getattr(self.plugin, "_create_lifecycle_background_task", None)
        if callable(creator):
            task = creator(operation, label=label)
            if task is None:
                close = getattr(operation, "close", None)
                if callable(close):
                    close()
            return task
        try:
            task = asyncio.create_task(operation, name=f"private-companion-page-{label}")
        except RuntimeError:
            close = getattr(operation, "close", None)
            if callable(close):
                close()
            return None

        def consume(done_task: asyncio.Task) -> None:
            try:
                done_task.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning(
                    "background task failed: label=%s error=%s",
                    label,
                    self._single_line(exc, 160),
                )

        task.add_done_callback(consume)
        return task






    def _http_status_route_handler(self, handler):
        """Apply transport status codes without changing direct-call results."""

        @functools.wraps(handler)
        async def wrapper(*args, **kwargs):
            return self._as_http_response(await handler(*args, **kwargs))

        return wrapper


    # Calendar records are durable constraints and are intentionally exposed
    # separately from the generated ``daily_plan``.  These small helpers keep
    # range parsing and the response contract consistent across the page and
    # the standalone API transport.
    def register_routes(self) -> None:
        register = self.plugin.context.register_web_api
        for path, handler, methods, desc in self.route_bindings():
            register(f"{PAGE_API_PREFIX}{path}", handler, methods, desc)









































    # 缩略图只服务衣柜素材目录里的位图；单张上限是为了不把 32MB 的原图
    # base64 成 43MB 再塞回浏览器 —— 队列里那一小格图不值得这个带宽。
    WARDROBE_ASSET_IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
    WARDROBE_ASSET_IMAGE_MAX_BYTES = 8 * 1024 * 1024






























    # ------------------------------------------------------------------
    # Independent visual reference assets
    # ------------------------------------------------------------------


































































































    def _passive_no_reply_item_is_obsolete_fixed_error(self, item: dict[str, Any]) -> bool:
        checker = getattr(self.plugin, "_proactive_audit_note_is_obsolete_fixed_error", None)
        texts = [
            item.get("reason"),
            item.get("last_detail"),
            item.get("last_action"),
            item.get("last_reply_preview"),
        ]
        samples = item.get("samples") if isinstance(item.get("samples"), list) else []
        for sample in samples[:5]:
            if not isinstance(sample, dict):
                continue
            texts.extend([sample.get("detail"), sample.get("reply_preview")])
        joined = "\n".join(str(value or "") for value in texts)
        if callable(checker) and checker(joined):
            return True
        return "NameError" in joined and any(
            token in joined
            for token in (
                "name 'topic' is not defined",
                "name 'name' is not defined",
            )
        )



























































































































    def _deep_merge_dict(self, target: dict[str, Any], incoming: dict[str, Any], *, conflict: str = "use_backup") -> None:
        for key, value in incoming.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                self._deep_merge_dict(target[key], value, conflict=conflict)
            elif key in target and not self._should_apply_migration_value(target.get(key), value, conflict):
                continue
            else:
                target[key] = deepcopy(value)


























    def _screen_companion_available(self) -> bool:
        getter = getattr(self.plugin, "_get_screen_companion_plugin", None)
        if callable(getter):
            try:
                return getter() is not None
            except Exception:
                return False
        return False












    # ============================================================
    # Creative Project Management Endpoints
    # ============================================================

    def _balance_status_payload(self, state: Any = None) -> dict[str, Any]:
        raw = state if isinstance(state, dict) else {}

        def optional_number(value: Any) -> float | None:
            try:
                number = float(value)
            except (TypeError, ValueError):
                return None
            return number if math.isfinite(number) else None

        amount = optional_number(raw.get("amount"))
        total = optional_number(raw.get("total"))
        remaining_percent = optional_number(raw.get("remaining_percent"))
        tier = self._single_line(raw.get("tier"), 20) or "unknown"
        if tier not in {"normal", "low", "critical", "unknown"}:
            tier = "unknown"
        enabled = bool(getattr(self.plugin, "enable_balance_awareness", False))
        manual_configured = bool(str(getattr(self.plugin, "balance_api_url", "") or "").strip())
        auto_available_getter = getattr(self.plugin, "_balance_auto_discovery_available", None)
        try:
            auto_discovery_available = bool(auto_available_getter()) if callable(auto_available_getter) else False
        except Exception:
            auto_discovery_available = False
        configured = manual_configured or auto_discovery_available
        query_mode = self._single_line(raw.get("query_mode"), 20)
        if query_mode not in {"manual", "auto"}:
            query_mode = "manual" if manual_configured else "auto"
        return {
            "enabled": enabled,
            "configured": configured,
            "manual_configured": manual_configured,
            "auto_discovery_available": auto_discovery_available,
            "query_mode": query_mode,
            "source_label": self._single_line(raw.get("auto_source_id"), 80),
            "available": bool(enabled and configured and amount is not None and self._float(raw.get("last_success_at")) > 0),
            "amount": amount,
            "total": total,
            "remaining_percent": remaining_percent,
            "currency_label": self._single_line(
                raw.get("currency_label") or getattr(self.plugin, "balance_currency_label", "元"),
                20,
            ) or "元",
            "tier": tier,
            "last_check_at": self._float(raw.get("last_check_at")),
            "last_success_at": self._float(raw.get("last_success_at")),
            "next_check_at": self._float(raw.get("next_check_at")),
            "last_prompted_at": self._float(raw.get("last_prompted_at")),
            "consecutive_failures": self._int(raw.get("consecutive_failures")),
            "last_error": self._single_line(raw.get("last_error"), 180),
        }
