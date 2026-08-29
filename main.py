from __future__ import annotations

import asyncio
import base64
from collections.abc import Collection
from contextlib import asynccontextmanager
import contextvars
import functools
import gc
import hashlib
import html
import importlib
import inspect
import json
import math
import os
import random
import re
import shutil
import sqlite3
import stat
import sys
import threading
import time
import unicodedata
import uuid
import zoneinfo
from copy import copy, deepcopy
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse, urlunparse
from xml.etree import ElementTree as ET

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
try:
    from astrbot.api.message_components import (
        At,
        BaseMessageComponent,
        ComponentType,
        Image,
        Plain,
        Record,
        Reply,
    )
except ImportError:
    from astrbot.api.message_components import At, Image, Plain
    from astrbot.core.message.components import BaseMessageComponent, ComponentType, Record
    try:
        from astrbot.api.message_components import Reply
    except ImportError:
        try:
            from astrbot.core.message.components import Reply
        except ImportError:
            Reply = None
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools
from astrbot.core import file_token_service
from astrbot.core.astr_main_agent import MainAgentBuildConfig, build_main_agent
from astrbot.core.agent.message import AssistantMessageSegment, TextPart, UserMessageSegment
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform import PlatformStatus
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.star.star_handler import EventType, star_handlers_registry
from astrbot.core.provider.entities import LLMResponse

from .private_scope_isolation import (
    GROUP_SCOPE_MARKERS,
    sanitize_private_request_group_artifacts,
)

try:
    import chinese_calendar as calendar_cn
except Exception:
    calendar_cn = None

try:
    from lunarcalendar import Converter, Solar
except Exception:
    Converter = None
    Solar = None

from .constants import (
    DEFAULT_DAILY_PLAN_ITEMS,
    DEFAULT_HUMANIZED_STATE,
    DEFAULT_NATURAL_LANGUAGE_PHOTO_EXTRA_PROMPT,
    DEFAULT_REPLY_STYLE_PROMPT,
    PAGE_FONT_NAMES,
    PAGE_THEME_NAMES,
    PLUGIN_NAME,
    DATA_VERSION,
    PROACTIVE_ABILITY_REGISTRY,
    VOICE_FALLBACK_TEMPLATES,
    TIMER_TAG_PATTERN,
    SUPPORTED_TIMER_FORMATS,
    _ACTION_TEXT,
    _DATA_STORE_KEYS,
    _DEFAULT_GROUP_TEMPLATE,
    _DEFAULT_USER_TEMPLATE,
    _REASON_TEXT,
    _SIMULATION_FALLBACK_EVENTS,
)
from .dreaming import (
    build_dream_memory_fragments,
    dream_fragment_effective_weight,
    dream_theme_specs,
    extract_weighted_dream_fragments,
    fallback_diary_payload,
    fallback_dream_fragments_for_diary,
    generate_daily_diary,
    generate_enhanced_dream_pick,
    merge_dream_fragment_pool,
    normalize_dream_fragment_item,
    normalize_dream_fragment_pool,
    recent_diary_context,
    recent_diary_tags,
    weighted_unique_fragment_sample,
)
from .helpers import (
    _date_key,
    _flat_get,
    _group_link_message_context,
    _missing_optional_model_dependency,
    _normalize_outbound_punctuation_flow,
    _now_ts,
    _normalize_timezone_name,
    _normalize_timezone_setting,
    _path_text,
    _redact_outbound_secrets,
    _safe_float,
    _safe_int,
    _set_today_key_timezone,
    _set_into_config,
    _single_line,
    _strip_internal_message_blocks,
    _strip_outbound_control_blocks,
    _today_key,
    _resolve_timezone_setting,
)
from .config_migration import migrate_flat_config_into_schema_groups
from .group_context_interception import (
    intercept_astrbot_group_context,
    restore_astrbot_group_history,
)
from .persona_config import (
    PERSONA_SETTINGS_KEY,
    PERSONA_SETTINGS_REVISION_KEY,
    PERSONA_SETTINGS_SCHEMA_VERSION,
    PERSONA_SETTINGS_VERSION_KEY,
    PersonaConfigError,
    PersonaSettingsTypeError,
    create_persona_settings,
    copy_from_primary_config,
    detach_persona_settings,
    load_scope_manifest,
    migrate_persona_profile,
    normalize_persona_settings,
    normalize_setting_value,
    resolve_effective_settings,
    resolve_persona_setting,
    runtime_persona_setting,
)
from .persona_sqlite_store import (
    PersonaSqliteStoreError,
    PersonaSqliteStoreRegistry,
    load_persona_sqlite_store,
    read_persona_store_snapshot_read_only,
)
from .model_routing import contains_sensitive_refusal, scope_allows
from .person_context_contract import (
    CONTRACT_NAME as PERSON_CONTRACT_NAME,
    CONTRACT_VERSION as PERSON_CONTRACT_VERSION,
    P3_CONTRACT_NAME,
    P3_CONTRACT_VERSION,
    build_identity_key,
    contract_self_check as person_contract_self_check,
)
from .unified_person_registry import UnifiedPersonRegistry
from .migration_backfill import MigrationBackfill, legacy_pending_reference
from .migration_dual_write import MigrationDualWriteProducer
from .migration_replay import MigrationReplayWorker
from .migration_read_router import MigrationRelationshipReadRouter
from .migration_stability import advance_migration_stability
from .migration_source_inspector import inspect_migration_sources
from .relationship_account_store import RelationshipAccountStore
from .req041_observability import Req041Observability
from .relationship_affinity_runtime import (
    admit_confirmed_group_affinity,
    normalize_group_allowlist,
    prepare_group_affinity_candidate,
)
from .identity_namespace import AssurancePolicy, NamespaceContext
from .migration_scoped_projection import (
    ScopedProjectionSynchronizer,
    scoped_group_ref,
    scoped_persona_ref,
)
from .scoped_runtime_view import overlay_group_runtime_view, overlay_private_runtime_view
from .unified_profile_contract import (
    build_person_ref as req036_build_person_ref,
    build_profile_dto as req036_build_profile_dto,
    build_portrait_request as req036_build_portrait_request,
    validate_profile_dto as req036_validate_profile_dto,
)
from .unified_profile_service import (
    DEFAULT_UNAUTHORIZED_PRIVATE_REPLY,
    capability_summary as req036_capability_summary,
    ensure_new_profile_capabilities as req036_ensure_new_profile_capabilities,
    private_companion_gate as req036_private_companion_gate,
    proactive_private_gate as req036_proactive_private_gate,
    update_capabilities as req036_update_capabilities,
)
from .context_orchestration import build_context, project_context
from .p4_shadow import build_p4_shadow
from .p4_affinity_confinement import apply_legacy_relationship_delta
from .p4_live_runtime import decide_live_request
from .p4_runtime_gate import SAFE_CONFINEMENT_REPLY
from .extension_api_content import _ContentCapabilityFamily
from .extension_api_diagnostics import _DiagnosticsCapabilityFamily
from .extension_api_identity import _IdentityCapabilityFamily
from .extension_api_image import _ImageCapabilityFamily
from .extension_api_memory import _MemoryCapabilityFamily
from .extension_api_qzone import _QzoneCapabilityFamily
from .extension_api_relationship import _RelationshipCapabilityFamily
from .extension_api_scheduler import _SchedulerCapabilityFamily
from .memory_page_snapshot import MemoryPageSnapshotService
from .story_authority import (
    StoryAuthorityError,
    story_authority_controller,
    story_legacy_operation,
    story_legacy_sync_operation,
)
from .story_handoff import resume_story_handoff
from .domains.affect.reply_temperature import compose_reply_temperature
from .plugin_identity import (
    PLUGIN_ID,
    is_module_path_for_package,
)
from .lab_fixture_adapter import register_companion_lab_fixture_adapter
from .companion_interaction_expression import build_expression_decision, content_intent_from_text, expression_decision_prompt
from .photo_reference_catalog import CATALOG_VERSION, load_catalog, validate_and_serialize
from .relationship_ledger import normalize_relationship_positive_stage_cap_key
from .relationship_policy import normalize_relationship_stage_policy
from .runtime_config_dispatcher import dispatch_runtime_config_effects


_ACTIVE_PERSONA_ID = contextvars.ContextVar("private_companion_active_persona_id", default="")
_PERSONA_SETTING_MANIFEST = load_scope_manifest()
_PHOTO_TOOL_PROMPT_FORMAT_MARKER = "<!-- private_companion_prompt_format_req_v1 -->"
_PERSONA_PROFILE_FORBIDDEN_FILENAME_CHARS = frozenset('<>:"/\\|?*%')
_WINDOWS_RESERVED_FILENAME_STEMS = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
)


def _multi_persona_event_context(function):
    """Bind one event task to one persona profile for the complete event lifetime."""
    if inspect.isasyncgenfunction(function):
        @functools.wraps(function)
        async def asyncgen_wrapper(self, event, *args, **kwargs):
            scope_checker = getattr(self, "_bot_scope_allows_event", None)
            if callable(scope_checker) and not scope_checker(event):
                return
            activator = getattr(self, "_activate_persona_for_event_context", None)
            if not callable(activator):
                activator = getattr(self, "_activate_persona_for_event", None)
            activation = activator(event) if callable(activator) else (None, "")
            if inspect.isawaitable(activation):
                activation = await activation
            token, _ = activation
            try:
                async for item in function(self, event, *args, **kwargs):
                    yield item
            finally:
                deactivator = getattr(self, "_deactivate_persona_for_event", None)
                if callable(deactivator):
                    deactivator(token)
        return asyncgen_wrapper

    @functools.wraps(function)
    async def async_wrapper(self, event, *args, **kwargs):
        scope_checker = getattr(self, "_bot_scope_allows_event", None)
        if callable(scope_checker) and not scope_checker(event):
            return None
        activator = getattr(self, "_activate_persona_for_event_context", None)
        if not callable(activator):
            activator = getattr(self, "_activate_persona_for_event", None)
        activation = activator(event) if callable(activator) else (None, "")
        if inspect.isawaitable(activation):
            activation = await activation
        token, _ = activation
        try:
            return await function(self, event, *args, **kwargs)
        finally:
            deactivator = getattr(self, "_deactivate_persona_for_event", None)
            if callable(deactivator):
                deactivator(token)
    return async_wrapper
from .busy_reply_gate import BusyReplyGateMixin
from .chronotype import ChronotypeMixin
from .memory_companion_adapter import MemoryCompanionAdapterMixin
from .p5_attestation import P5AttestationError, REASON_CODES as P5_ATTESTATION_REASON_CODES
from .p5_source_observer import evaluate_source
from .message_pipeline import (
    event_data_save_boundary,
    handle_group_message,
    handle_private_message,
)
from .tool_history_sanitizer import sanitize_history_image_blocks, sanitize_openai_tool_history
from .forward_message import ForwardMessageMixin
from .private_image import PrivateImageMixin
from .conversation_injection_plan import (
    PLACEMENT_DYNAMIC_SYSTEM,
    PLACEMENT_STABLE_SYSTEM,
    PLACEMENT_TOOL_CONTRACT,
    PLACEMENT_TURN_TAIL,
    get_conversation_injection_plan,
)
from .conversation_prompt_section import (
    coerce_prompt_section,
    prompt_section,
    render_prompt_sections,
)
from .prompt_surface import PromptSurface
from .passive_state_pipeline import inject_humanized_state as run_humanized_state_injection
from .qzone_integration import QzoneMixin
from .segmented_message import (
    bind_reply_components_to_first_text,
    component_kind,
    component_order_from_owner,
    component_strategies_from_owner,
    flatten_component_chunks,
    has_fenced_llm_segment_marker,
    LLM_SEGMENT_MARKER,
    normalize_component_strategy,
    parse_llm_segment_control,
    plan_component_chunks,
    sanitize_llm_segment_control_tokens,
    split_llm_controlled_text,
    strip_llm_segment_marker_lines,
)
from .token_budget import TokenBudgetMixin
from .balance_awareness import BalanceAwarenessMixin
from .body_monitor_integration import BodyMonitorIntegration
from .worldbook import WorldbookMixin
from .user_memory import UserMemoryMixin
from .creative import CreativeMixin
from .content_companion_bridge import ContentCompanionBridgeMixin
from .proactive import ProactiveMixin
from .group_wakeup import GroupWakeupMixin
from .group_observation import GroupObservationMixin
from .group_cycle_boundary import build_group_cycle_boundary
try:
    from .group_member_safety import GroupMemberSafetyMixin
except ModuleNotFoundError as exc:
    if str(getattr(exc, "name", "") or "").split(".")[-1] != "group_member_safety":
        raise

    class GroupMemberSafetyMixin:
        """Fail-open fallback for an incomplete release package."""

        @staticmethod
        def _extract_group_member_safety_hidden_markers(text: Any) -> tuple[str, list[dict[str, Any]]]:
            return str(text or ""), []

        @staticmethod
        def _group_member_safety_hidden_marker_mode() -> str:
            return "disabled"

        @staticmethod
        def _group_member_safety_member(*args: Any, **kwargs: Any) -> None:
            return None

        @staticmethod
        def _group_member_safety_is_exempt_event(*args: Any, **kwargs: Any) -> bool:
            return True

        @staticmethod
        def _group_member_safety_active(*args: Any, **kwargs: Any) -> bool:
            return False

        async def _append_group_member_safety_hidden_marker_to_request(
            self,
            *args: Any,
            **kwargs: Any,
        ) -> None:
            return None

        async def _record_group_member_safety_decision(
            self,
            *args: Any,
            **kwargs: Any,
        ) -> dict[str, Any]:
            return {"reviewed": False, "counted": False, "blocked": False, "reason": "module_missing"}

        async def _review_group_member_safety_message(
            self,
            *args: Any,
            **kwargs: Any,
        ) -> dict[str, Any]:
            return {"reviewed": False, "counted": False, "blocked": False, "reason": "module_missing"}

    logger.error(
        "[PrivateCompanion] 发布包缺少 group_member_safety.py，群成员风控已停用；插件其余功能继续加载。"
        "请重新安装包含该文件的完整版本。"
    )
from .event_dispatch import EventDispatchMixin, _ON_WAITING_LLM_REQUEST
from .reading_archive import ReadingArchiveMixin
from .news_exploration import NewsExplorationMixin
try:
    from .self_timeline import SelfTimelineMixin
except ModuleNotFoundError as exc:
    if str(getattr(exc, "name", "") or "").split(".")[-1] != "self_timeline":
        raise

    class SelfTimelineMixin:
        """Fallback used when an old release package missed self_timeline.py."""

        def _format_self_timeline_context_for_reply(self, *args: Any, **kwargs: Any) -> str:
            return ""

    logger.warning("[PrivateCompanion] self_timeline.py 缺失，已跳过 Bot 自身时间线注入能力。请重新安装完整版本。")
from .core_store import CoreStoreMixin
from .storage.path_generation import activate_persistence_owner
from .platform_compat import PlatformCompatibilityMixin
from .integration_status import IntegrationStatusMixin
from .astrbot_knowledge import AstrBotKnowledgeMixin
from .atrelay import AtRelayMixin
from .proactive_engine import ProactiveEngineMixin
from .proactive_message import ProactiveMessageMixin
from .image_companion_bridge import ImageCompanionBridgeMixin
from .nai_image_bridge import NAIImageBridgeMixin
from .proactive_chat_runtime_bridge import ProactiveChatRuntimeBridge
from .plugin_bootstrap import (
    DEFAULT_AI_DAILY_JUYA_UID,
    DEFAULT_AI_DAILY_MORNING_UID,
    DEFAULT_AI_DAILY_SOURCES,
    DEFAULT_NEWS_SOURCES,
    LEGACY_DEFAULT_NEWS_SOURCES,
    PREVIOUS_TECH_DEFAULT_NEWS_SOURCES,
    initialize_plugin_entrypoint_state,
    initialize_plugin_config,
    initialize_plugin_post_runtime_state,
    initialize_plugin_runtime,
)
from .daily_state import DailyStateMixin
from .agenda_runtime import AgendaRuntimeMixin
from .daily_review import DailyReviewMixin
from .scene_context import SceneContextMixin
from .place_cognitive_map import PlaceCognitiveMapMixin
from .game_integration import GameIntegrationMixin
from .state_views import StateViewsMixin
from .interaction_utils import InteractionUtilsMixin
from .llm_tool_actions import LlmToolActionsMixin, PHOTO_TOOL_SILENT_SENTINEL
from .command_handlers import CommandHandlersMixin
from .tts_enhancement import TtsEnhancementMixin
from .tts_tool_sanitizer import TtsToolSanitizerMixin
from .reality_companion_bridge import RealityCompanionBridgeMixin
from .planning import (
    build_daily_plan_prompt,
    build_detail_enhancement_prompt,
    format_plan_for_diary,
    generate_daily_plan,
    generate_detail_enhancement,
    get_schedule_planning_prompt,
    normalize_long_term_events,
    normalize_story_items,
    normalize_story_plan,
    pick_detail_segment,
)

_PRIVATE_COMPANION_RUNTIME_KEY = "_astrbot_private_companion_runtime_v1"


def _new_private_companion_runtime() -> ModuleType:
    runtime = ModuleType(_PRIVATE_COMPANION_RUNTIME_KEY)
    runtime.lock = threading.RLock()
    runtime.active_plugin = None
    return runtime


_private_companion_runtime = sys.modules.setdefault(
    _PRIVATE_COMPANION_RUNTIME_KEY,
    _new_private_companion_runtime(),
)
_private_companion_plugin: Any | None = _private_companion_runtime.active_plugin


class _OneBotReactionImage(BaseMessageComponent):
    """OneBot image segment that preserves QQ's emoji-image subtype flag."""

    type: ComponentType = ComponentType.Image
    file: str
    path: str
    url: str = ""
    sub_type: int = 1
    payload_file: str
    _private_companion_reaction_expression = True

    def __init__(self, path: str) -> None:
        resolved = str(Path(path).resolve(strict=True))
        payload = base64.b64encode(Path(resolved).read_bytes()).decode("ascii")
        super().__init__(
            file=resolved,
            path=resolved,
            url="",
            sub_type=1,
            payload_file=f"base64://{payload}",
        )

    def toDict(self) -> dict[str, Any]:
        return {
            "type": "image",
            "data": {"file": self.payload_file, "sub_type": self.sub_type},
        }

    async def to_dict(self) -> dict[str, Any]:
        return self.toDict()

    def __repr__(self) -> str:
        return f"_OneBotReactionImage(path={self.path!r}, sub_type={self.sub_type})"



def get_private_companion_api() -> Any | None:
    with _private_companion_runtime.lock:
        plugin = _private_companion_runtime.active_plugin
        api = getattr(plugin, "extension_api", None) if plugin is not None else None
        lifecycle = getattr(api, "bridge_lifecycle_status", None)
        if not callable(lifecycle):
            return None
        try:
            status = lifecycle()
        except Exception:
            return None
        if not isinstance(status, dict) or status.get("active") is not True:
            return None
        return api


class PrivateCompanionExtensionAPI:
    """Lightweight integration API for external AstrBot plugins."""

    def __init__(self, plugin: "PrivateCompanionPlugin") -> None:
        self._plugin = plugin
        self._story_migration_generation = uuid.uuid4().hex
        self._story_migration_state = "created"
        self._qzone_reference_lock = threading.RLock()
        self._qzone_references: dict[str, tuple[float, Any]] = {}
        story_authority_controller().stage_generation(
            self._story_migration_generation
        )
        self._memory_page_service = MemoryPageSnapshotService(self)
        self._identity_family = _IdentityCapabilityFamily(self)
        self._relationship_family = _RelationshipCapabilityFamily(self)
        self._scheduler_family = _SchedulerCapabilityFamily(self)
        self._memory_family = _MemoryCapabilityFamily(self)
        self._content_family = _ContentCapabilityFamily(self)
        self._diagnostics_family = _DiagnosticsCapabilityFamily(self)
        self._image_family = _ImageCapabilityFamily(self)
        self._qzone_family = _QzoneCapabilityFamily(self)

    def _story_migration_instance_generation(self) -> str:
        return self._story_migration_generation

    def _story_migration_lifecycle_state(self) -> str:
        return self._story_migration_state

    def _extension_instance_generation(self) -> str:
        return self._story_migration_generation

    def _extension_lifecycle_state(self) -> str:
        return self._story_migration_state

    def bridge_lifecycle_status(self) -> dict[str, Any]:
        """Expose whether this published cross-plugin generation is callable."""
        state = self._story_migration_state
        published = _private_companion_runtime.active_plugin is self._plugin
        return {
            "active": state == "ready" and published,
            "state": state,
            "instance_generation": self._story_migration_generation,
        }

    def _activate_story_migration_api(self) -> bool:
        if self._story_migration_state != "created":
            return False
        story_authority_controller().activate_generation(
            self._story_migration_generation
        )
        self._story_migration_state = "ready"
        return True

    def _supersede_story_migration_api(self) -> None:
        if self._story_migration_state in {"created", "ready"}:
            self._story_migration_state = "superseded"
            self._memory_page_service.clear_references()
            with self._qzone_reference_lock:
                self._qzone_references.clear()
            story_authority_controller().supersede_generation(
                self._story_migration_generation
            )

    def _close_story_migration_api(self) -> None:
        self._memory_page_service.clear_references()
        with self._qzone_reference_lock:
            self._qzone_references.clear()
        if self._story_migration_state != "superseded":
            self._story_migration_state = "closed"
            story_authority_controller().close_generation(
                self._story_migration_generation
            )

    def register_proactive_ability(self, spec: dict[str, Any]) -> bool:
        return self._scheduler_family.register_proactive_ability(
            spec,
        )

    def unregister_proactive_ability(self, name: str) -> bool:
        return self._scheduler_family.unregister_proactive_ability(
            name,
        )

    def list_proactive_abilities(self) -> list[dict[str, Any]]:
        return self._scheduler_family.list_proactive_abilities()

    async def record_game_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply one idempotent, per-user game event to companion afterglow."""
        return await self._memory_family.record_game_event(
            payload,
        )

    def memory_page_capabilities(self) -> dict[str, Any]:
        """Describe the versioned, read-only Memory Page producer API."""
        return self._memory_family.memory_page_capabilities()

    async def export_memory_page_snapshot(
        self,
        *,
        target_plugin_id: str,
        selected_date: str = "",
    ) -> dict[str, Any]:
        """Export a bounded, path-free Memory Page snapshot."""
        return await self._memory_family.export_memory_page_snapshot(
            target_plugin_id=target_plugin_id,
            selected_date=selected_date,
        )

    async def read_memory_page_photo(
        self,
        *,
        target_plugin_id: str,
        photo_ref: str,
    ) -> dict[str, Any]:
        """Read one generation-bound photo reference after strict revalidation."""
        return await self._memory_family.read_memory_page_photo(
            target_plugin_id=target_plugin_id,
            photo_ref=photo_ref,
        )

    def get_realtime_voice_config(self) -> dict[str, Any]:
        """Expose the active companion voice language to realtime plugins."""
        return self._content_family.get_realtime_voice_config()

    def story_migration_capabilities(self) -> dict[str, Any]:
        """Describe the versioned, read-only Story migration snapshot API."""
        return self._content_family.story_migration_capabilities()

    async def export_story_migration_snapshot(
        self,
        *,
        lease_token: str = "",
    ) -> dict[str, Any]:
        """Export a detached Story snapshot without exposing local paths."""
        return await self._content_family.export_story_migration_snapshot(
            lease_token=lease_token,
        )

    async def prepare_story_handoff(
        self,
        *,
        target_plugin_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        """Drain legacy Story writers and prepare an ephemeral handoff lease."""
        return await self._content_family.prepare_story_handoff(
            target_plugin_id=target_plugin_id,
            owner_id=owner_id,
        )

    async def abort_story_handoff(
        self,
        *,
        lease_token: str,
    ) -> dict[str, Any]:
        """Abort the exact active Story handoff lease."""
        return await self._content_family.abort_story_handoff(
            lease_token=lease_token,
        )

    async def commit_story_handoff(
        self,
        *,
        lease_token: str = "",
    ) -> dict[str, Any]:
        """Commit a live lease or replay an already durable handoff marker."""

        return await self._content_family.commit_story_handoff(
            lease_token=lease_token,
        )

    def qzone_capabilities(self) -> dict[str, Any]:
        """Describe the generation-bound Companion-owned QZone contract."""

        return self._qzone_family.qzone_capabilities()

    def qzone_status_snapshot(self) -> dict[str, Any]:
        """Return a path-free, credential-free QZone status snapshot."""

        return self._qzone_family.qzone_status_snapshot()

    def export_qzone_config_snapshot(
        self,
        *,
        target_plugin_id: str,
    ) -> dict[str, Any]:
        """Export only non-secret owner settings plus credential state."""

        return self._qzone_family.export_qzone_config_snapshot(
            target_plugin_id=target_plugin_id,
        )

    async def execute_qzone_operation(
        self,
        operation: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute one exact QZone operation without exposing the host/Page API."""

        return await self._qzone_family.execute_qzone_operation(operation, payload)

    async def synthesize_realtime_voice(
        self,
        text: str,
        *,
        tts_provider: Any = None,
        provider_settings: dict[str, Any] | None = None,
        source: str = "external_realtime",
        play_local: bool = True,
    ) -> dict[str, Any]:
        """Synthesize external realtime speech through companion TTS rules."""
        return await self._plugin._synthesize_realtime_voice(
            text,
            tts_provider=tts_provider,
            provider_settings=provider_settings,
            source=source,
            play_local=play_local,
        )

    def get_reality_touch_authorized_user_ids(self) -> list[str]:
        """Return host administrators and primary users eligible for device consent."""
        return self._identity_family.get_reality_touch_authorized_user_ids()

    async def notify_mobile_location_update(self, user_id: str) -> dict[str, Any]:
        """Let the mobile gateway wake location-aware proactive planning promptly."""
        return await self._scheduler_family.notify_mobile_location_update(
            user_id,
        )

    def get_reality_touch_host_context(self, user_id: str) -> dict[str, Any]:
        """Expose bounded identity and relationship context to the device plugin."""
        return self._relationship_family.get_reality_touch_host_context(
            user_id,
        )

    def export_reality_touch_legacy_state(self) -> dict[str, Any]:
        """Return a detached one-time migration payload for Reality Companion."""
        plugin = self._plugin
        source_config = getattr(plugin, "config", {})

        def legacy_bool(key: str, default: bool = False) -> bool:
            value = _flat_get(source_config, key, default)
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"true", "1", "yes", "y", "on", "enable", "enabled", "启用", "开启", "开", "是"}:
                    return True
                if normalized in {"false", "0", "no", "n", "off", "disable", "disabled", "停用", "关闭", "关", "否", ""}:
                    return False
            return bool(value)

        def legacy_int(key: str, default: int, minimum: int, maximum: int) -> int:
            return _safe_int(_flat_get(source_config, key, default), default, minimum, maximum)

        source_users = plugin.data.get("users") if isinstance(plugin.data, dict) else None
        allowed_keys = {
            "user_id",
            "umo",
            "nickname",
            "last_display_name",
            "display_name",
            "reality_touch_consent",
            "reality_touch_pending_consent",
            "reality_touch_policy",
            "reality_touch_camera_consent",
            "reality_touch_camera_policy",
            "wakeup_alarm",
            "reality_touch_reminders",
        }
        users: dict[str, dict[str, Any]] = {}
        if isinstance(source_users, dict):
            for user_id, user in source_users.items():
                if not isinstance(user, dict):
                    continue
                selected = {
                    key: deepcopy(value)
                    for key, value in user.items()
                    if key in allowed_keys
                }
                if any(key.startswith("reality_touch") or key == "wakeup_alarm" for key in selected):
                    selected.setdefault("user_id", _single_line(user_id, 120))
                    users[_single_line(user_id, 120)] = selected
        store = plugin.data.get("reality_touch") if isinstance(plugin.data, dict) else None
        config = {
            "enabled": legacy_bool("enable_experimental_bluetooth_wakeup"),
            "camera_enabled": legacy_bool("enable_reality_touch_camera"),
            "camera_index": legacy_int("reality_touch_camera_index", 0, 0, 100000),
            "camera_min_interval_seconds": legacy_int("reality_touch_camera_min_interval_seconds", 60, 10, 3600),
            "camera_capture_timeout_seconds": legacy_int("reality_touch_camera_capture_timeout_seconds", 5, 2, 20),
            "camera_analysis_timeout_seconds": legacy_int("reality_touch_camera_analysis_timeout_seconds", 25, 5, 90),
            "camera_proactive_curiosity_enabled": legacy_bool("enable_reality_touch_camera_proactive_curiosity"),
            "camera_proactive_min_tier": legacy_int("reality_touch_camera_proactive_min_tier", 4, 1, 5),
            "camera_proactive_max_daily": legacy_int("reality_touch_camera_proactive_max_daily", 1, 0, 10),
            "camera_proactive_cooldown_minutes": legacy_int("reality_touch_camera_proactive_cooldown_minutes", 240, 10, 1440),
            "audio_default_playback_volume": legacy_int("tts_local_playback_volume", 35, 0, 100),
        }
        return {
            "version": 1,
            "users": users,
            "reality_touch": deepcopy(store) if isinstance(store, dict) else {},
            "config": config,
        }

    async def generate_reality_touch_text(self, prompt: str, **kwargs: Any) -> str:
        """Generate bounded device-facing wording through the host model stack."""
        caller = getattr(self._plugin, "_llm_call", None)
        if not callable(caller):
            return ""
        return str(await caller(prompt, **kwargs) or "")

    async def send_reality_touch_chat(self, umo: str, text: str) -> bool:
        return await self._content_family.send_reality_touch_chat(
            umo,
            text,
        )

    async def record_reality_touch_output(
        self,
        user_id: str,
        text: str,
        *,
        source: str = "reality_touch_audio",
        delivered_at: float | None = None,
    ) -> dict[str, Any]:
        """Record speech delivered outside chat so the next reply can continue it."""
        return await self._content_family.record_reality_touch_output(
            user_id,
            text,
            source=source,
            delivered_at=delivered_at,
        )

    def get_reality_touch_cron_manager(self) -> Any | None:
        return self._scheduler_family.get_reality_touch_cron_manager()

    async def delete_reality_touch_cron_job(self, job_id: str) -> tuple[bool, str]:
        return await self._scheduler_family.delete_reality_touch_cron_job(
            job_id,
        )

    def get_bot_identity(self) -> dict[str, Any]:
        """Return a stable Bot identity without guessing between multiple accounts."""
        return self._identity_family.get_bot_identity()

    def get_unified_person_contract(self) -> dict[str, Any]:
        return self._identity_family.get_unified_person_contract()

    def resolve_unified_person(self, identity: dict[str, Any]) -> dict[str, Any]:
        return self._identity_family.resolve_unified_person(
            identity,
        )

    def create_unified_person(
        self,
        identity: dict[str, Any],
        *,
        profile: dict[str, Any] | None = None,
        operation_id: str = "",
    ) -> dict[str, Any]:
        return self._identity_family.create_unified_person(
            identity,
            profile=profile,
            operation_id=operation_id,
        )

    def get_unified_person_projection(self, person_id: str) -> dict[str, Any] | None:
        return self._identity_family.get_unified_person_projection(
            person_id,
        )

    def get_p6_readonly_status(self) -> dict[str, Any]:
        """Expose bounded Unified Person counts without an authority surface."""
        return self._diagnostics_family.get_p6_readonly_status()

    def get_unified_person_context(self, event: Any | None = None) -> dict[str, Any]:
        return self._identity_family.get_unified_person_context(
            event,
        )

    def get_scene_context(self, user_id: str = "") -> dict[str, Any]:
        """Return the current structured Bot-life context for plugin integrations."""
        return self._diagnostics_family.get_scene_context(
            user_id,
        )

    def get_realtime_context(self, user_id: str = "", purpose: str = "together") -> dict[str, Any]:
        """Return the full structured scene and its canonical prompt representation."""
        return self._diagnostics_family.get_realtime_context(
            user_id,
            purpose,
        )

    def record_external_realtime_continuity(
        self,
        user_id: str,
        *,
        summary: str,
        public_summary: str = "",
        facts: list[str] | None = None,
        ttl_seconds: int = 21600,
        activity_id: str = "",
    ) -> dict[str, Any]:
        """Store bounded post-call continuity without writing long-term memory."""
        return self._memory_family.record_external_realtime_continuity(
            user_id,
            summary=summary,
            public_summary=public_summary,
            facts=facts,
            ttl_seconds=ttl_seconds,
            activity_id=activity_id,
        )

    def get_external_realtime_continuity(self, *, user_id: str = "", public: bool = False) -> dict[str, Any]:
        return self._memory_family.get_external_realtime_continuity(
            user_id=user_id,
            public=public,
        )

    def notify_external_activity_started(
        self,
        activity_id: str,
        *,
        user_id: str = "",
        kind: str = "external",
        label: str = "",
        source_plugin: str = "external",
        ttl_seconds: int = 240,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._scheduler_family.notify_external_activity_started(
            activity_id,
            user_id=user_id,
            kind=kind,
            label=label,
            source_plugin=source_plugin,
            ttl_seconds=ttl_seconds,
            metadata=metadata,
        )

    def notify_external_activity_updated(
        self,
        activity_id: str,
        *,
        user_id: str = "",
        kind: str = "",
        label: str = "",
        source_plugin: str = "",
        ttl_seconds: int = 240,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._scheduler_family.notify_external_activity_updated(
            activity_id,
            user_id=user_id,
            kind=kind,
            label=label,
            source_plugin=source_plugin,
            ttl_seconds=ttl_seconds,
            metadata=metadata,
        )

    def notify_external_activity_ended(self, activity_id: str) -> bool:
        return self._scheduler_family.notify_external_activity_ended(
            activity_id,
        )

    def get_external_activity(self, *, user_id: str = "", activity_id: str = "") -> dict[str, Any]:
        return self._scheduler_family.get_external_activity(
            user_id=user_id,
            activity_id=activity_id,
        )

    async def prepare_proactive_chat(
        self,
        session_id: str,
        *,
        unanswered_count: int = 0,
    ) -> dict[str, Any]:
        return await self._plugin._prepare_proactive_chat_bridge(
            session_id,
            unanswered_count=unanswered_count,
        )

    async def review_proactive_chat_message(
        self,
        session_id: str,
        text: str,
        *,
        token: str = "",
    ) -> dict[str, Any]:
        return await self._plugin._review_proactive_chat_bridge_message(
            session_id,
            text,
            token=token,
        )

    async def notify_proactive_chat_sent(
        self,
        session_id: str,
        text: str,
        *,
        token: str = "",
    ) -> dict[str, Any]:
        return await self._plugin._record_proactive_chat_bridge_sent(
            session_id,
            text,
            token=token,
        )

    async def cancel_proactive_chat(
        self,
        session_id: str,
        *,
        token: str = "",
    ) -> bool:
        return await self._plugin._cancel_proactive_chat_bridge(
            session_id,
            token=token,
        )

    def resolve_historical_chat_identities(self, speakers: list[str]) -> dict[str, Any]:
        return self._identity_family.resolve_historical_chat_identities(
            speakers,
        )

    async def stage_historical_relationship_observations(
        self,
        *,
        user_id: str,
        user_name: str,
        batch_id: str,
        observations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return await self._relationship_family.stage_historical_relationship_observations(
            user_id=user_id,
            user_name=user_name,
            batch_id=batch_id,
            observations=observations,
        )

    async def rebind_historical_relationship_observations(
        self,
        *,
        batch_id: str,
        old_user_id: str,
        user_id: str,
        user_name: str = "",
    ) -> dict[str, Any]:
        """Move one imported batch of traceable pending and confirmed relationship observations."""
        return await self._relationship_family.rebind_historical_relationship_observations(
            batch_id=batch_id,
            old_user_id=old_user_id,
            user_id=user_id,
            user_name=user_name,
        )

    async def rollback_historical_relationship_observations(self, batch_id: str) -> dict[str, Any]:
        return await self._relationship_family.rollback_historical_relationship_observations(
            batch_id,
        )

_LUNAR_MONTH_NAMES = [
    "正月",
    "二月",
    "三月",
    "四月",
    "五月",
    "六月",
    "七月",
    "八月",
    "九月",
    "十月",
    "冬月",
    "腊月",
]
_LUNAR_DAY_NAMES = [
    "初一",
    "初二",
    "初三",
    "初四",
    "初五",
    "初六",
    "初七",
    "初八",
    "初九",
    "初十",
    "十一",
    "十二",
    "十三",
    "十四",
    "十五",
    "十六",
    "十七",
    "十八",
    "十九",
    "二十",
    "廿一",
    "廿二",
    "廿三",
    "廿四",
    "廿五",
    "廿六",
    "廿七",
    "廿八",
    "廿九",
    "三十",
]
_SOLAR_TERM_DATES = {
    (1, 5): "小寒",
    (1, 20): "大寒",
    (2, 4): "立春",
    (2, 19): "雨水",
    (3, 5): "惊蛰",
    (3, 20): "春分",
    (4, 4): "清明",
    (4, 20): "谷雨",
    (5, 5): "立夏",
    (5, 21): "小满",
    (6, 5): "芒种",
    (6, 21): "夏至",
    (7, 7): "小暑",
    (7, 22): "大暑",
    (8, 7): "立秋",
    (8, 23): "处暑",
    (9, 7): "白露",
    (9, 23): "秋分",
    (10, 8): "寒露",
    (10, 23): "霜降",
    (11, 7): "立冬",
    (11, 22): "小雪",
    (12, 7): "大雪",
    (12, 22): "冬至",
}
_ALMANAC_YI = ["整理房间", "写字", "散步", "读书", "听歌", "轻度创作", "复盘", "安静休息"]
_ALMANAC_JI = ["熬夜", "冲动发言", "硬撑", "反复纠结", "过度解释", "临时加压", "情绪化决定"]
_PLATFORM_DISPLAY_NAMES = {
    "aiocqhttp": "QQ",
    "qq": "QQ",
    "onebot": "QQ",
    "telegram": "Telegram",
    "wechat": "微信",
    "discord": "Discord",
}

_PROACTIVE_ONLY_TEMP_UNLOCK_ALIASES = {
    "全部": "all",
    "all": "all",
    "被动": "all",
    "被动链路": "all",
    "状态": "inject_passive_states",
    "状态注入": "inject_passive_states",
    "被动状态": "inject_passive_states",
    "图片": "enable_private_image_self_recognition",
    "识图": "enable_private_image_self_recognition",
    "私聊图片": "enable_private_image_self_recognition",
    "合并消息": "enable_forward_message_adaptation",
    "转发": "enable_forward_message_adaptation",
    "转发消息": "enable_forward_message_adaptation",
    "防抖": "enable_message_debounce",
    "智能防抖": "enable_message_debounce",
    "撤回": "enable_recall_enhancement",
    "撤回增强": "enable_recall_enhancement",
    "tts": "enable_tts_enhancement",
    "TTS": "enable_tts_enhancement",
    "语音": "enable_tts_enhancement",
    "分段": "enable_segmented_proactive_reply",
    "回复分段": "enable_segmented_proactive_reply",
    "群聊": "enable_group_companion",
    "群聊观察": "enable_group_companion",
    "技能": "enable_skill_growth_passive_injection",
    "技能注入": "enable_skill_growth_passive_injection",
    "吃什么": "enable_food_menu_recommendation",
    "吃什么候选": "enable_food_menu_recommendation",
    "候选菜单": "enable_food_menu_recommendation",
    "饭点关心": "enable_meal_care_proactive",
    "吃饭关心": "enable_meal_care_proactive",
    "关系网": "enable_worldbook_member_recognition",
    "跨用户记忆": "enable_cross_user_memory_bridge",
    "跨用户记忆互通": "enable_cross_user_memory_bridge",
    "互动查询": "enable_cross_user_memory_bridge",
    "跨群转述": "enable_atrelay_tools",
    "转述工具": "enable_atrelay_tools",
    "livingmemory": "enable_livingmemory_integration",
    "lmem": "enable_livingmemory_integration",
    "记忆插件": "enable_livingmemory_integration",
    "记忆协同": "enable_livingmemory_integration",
}
_PROACTIVE_ONLY_TEMP_UNLOCK_LABELS = {
    "all": "全部被动链路",
    "inject_passive_states": "被动状态注入",
    "enable_intent_emotion_analysis": "意图/情绪分析",
    "enable_llm_timer_scheduling": "预约类主动捕获",
    "enable_passive_topic_suppression": "重复话题抑制",
    "enable_environment_perception": "环境感知",
    "enable_message_debounce": "防抖",
    "enable_recall_enhancement": "撤回增强",
    "enable_private_image_self_recognition": "私聊图片识别",
    "enable_forward_message_adaptation": "合并/转发消息阅读",
    "enable_group_companion": "群聊观察",
    "enable_skill_growth_passive_injection": "技能被动注入",
    "enable_food_menu_recommendation": "吃什么候选",
    "enable_meal_care_proactive": "饭点主动关心",
    "enable_worldbook_member_recognition": "关系网成员识别",
    "enable_cross_user_memory_bridge": "跨用户记忆互通",
    "enable_atrelay_tools": "跨群转述工具",
    "enable_livingmemory_integration": "记忆插件被动引导",
    "enable_tts_enhancement": "TTS 后处理",
    "enable_segmented_proactive_reply": "普通 LLM 分段",
}
_PROACTIVE_ONLY_TEMP_UNLOCK_GROUPS = {
    "private_event_pipeline": {
        "enable_message_debounce",
        "enable_private_image_self_recognition",
        "enable_forward_message_adaptation",
    },
    "group_event_pipeline": {
        "enable_group_companion",
        "enable_message_debounce",
        "enable_forward_message_adaptation",
    },
    "llm_request": {
        "inject_passive_states",
        "enable_intent_emotion_analysis",
        "enable_llm_timer_scheduling",
        "enable_passive_topic_suppression",
        "enable_environment_perception",
        "enable_tts_enhancement",
        "enable_private_image_self_recognition",
        "enable_forward_message_adaptation",
        "enable_group_companion",
        "enable_skill_growth_passive_injection",
        "enable_food_menu_recommendation",
        "enable_worldbook_member_recognition",
        "enable_cross_user_memory_bridge",
        "enable_livingmemory_integration",
    },
    "pc_tools": {
        "enable_atrelay_tools",
        "enable_worldbook_member_recognition",
        "enable_cross_user_memory_bridge",
        "enable_qzone_integration",
    },
}
_PROACTIVE_ONLY_TEMP_UNLOCK_RELATED = {
    "enable_atrelay_tools": ["enable_worldbook_member_recognition"],
    "enable_cross_user_memory_bridge": ["enable_worldbook_member_recognition"],
    "enable_group_companion": ["enable_worldbook_member_recognition"],
    "enable_forward_message_adaptation": ["enable_private_image_self_recognition"],
}


def _strip_chain_plain_thinking(owner: Any, chain: list[Any]) -> None:
    """Strip reasoning/thinking chain from ALL Plain text joined together.

    The thinking chain (e.g. ``  thinking\n...\n  /response```) can be
    split across multiple Plain components in the chain.  Per-component
    cleaning would fail since none of the fragments contain the complete
    opening+content+closing pattern.  This helper joins all Plain text,
    cleans it, and puts the result back into the first Plain component.
    """
    plain_components = [(i, comp) for i, comp in enumerate(chain) if isinstance(comp, Plain)]
    if not plain_components:
        return
    all_text = "".join(str(getattr(comp, "text", "") or "") for _, comp in plain_components)
    cleaned = _strip_internal_message_blocks(all_text)
    if cleaned == all_text:
        return
    for idx, (_, comp) in enumerate(plain_components):
        try:
            comp.text = cleaned if idx == 0 else ""
        except Exception:
            pass


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
    # AstrBot registers handlers from their exact defining module.  Keep the
    # implementations in EventDispatchMixin, but expose the decorated entry
    # points here so waiting/request/response form one complete pipeline.
    @_ON_WAITING_LLM_REQUEST(priority=110000)
    @_multi_persona_event_context
    async def route_model_replacement_before_agent_hook(
        self,
        event: AstrMessageEvent,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        await EventDispatchMixin.route_model_replacement_before_agent(
            self,
            event,
            *args,
            **kwargs,
        )

    @filter.on_llm_request(priority=110000)
    @_multi_persona_event_context
    async def enforce_model_replacement_request_hook(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        await EventDispatchMixin.enforce_model_replacement_request(
            self,
            event,
            req,
            *args,
            **kwargs,
        )

    @filter.on_llm_response(priority=-100000)
    @_multi_persona_event_context
    async def clear_model_replacement_context_hook(
        self,
        event: AstrMessageEvent,
        resp: LLMResponse,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        await EventDispatchMixin.clear_model_replacement_context(
            self,
            event,
            resp,
            *args,
            **kwargs,
        )

    @_ON_WAITING_LLM_REQUEST(priority=100000)
    @_multi_persona_event_context
    async def guard_pending_message_debounce_hook(
        self,
        event: AstrMessageEvent,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        await EventDispatchMixin.guard_pending_message_debounce(
            self,
            event,
            *args,
            **kwargs,
        )

    @filter.on_llm_response(priority=100000)
    @_multi_persona_event_context
    async def settle_pending_message_debounce_hook(
        self,
        event: AstrMessageEvent,
        resp: LLMResponse,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        await EventDispatchMixin.settle_pending_message_debounce(
            self,
            event,
            resp,
            *args,
            **kwargs,
        )

    # 与 astrbot_plugin_reality_companion 的 capability 契约一致；仅用于识别
    # 拆分前版本遗留在本插件用户数据中的摄像头待授权记录。
    _REALITY_TOUCH_CAMERA_CAPABILITY = "camera_single_frame"

    @staticmethod
    def _schema_runtime_default(
        key: str,
        fallback: Any,
        *,
        schema_types: frozenset[str] | None = None,
    ) -> Any:
        entry = _PERSONA_SETTING_MANIFEST.get(key)
        if (
            isinstance(entry, dict)
            and "default" in entry
            and (
                schema_types is None
                or str(entry.get("type") or "") in schema_types
            )
        ):
            return deepcopy(entry["default"])
        return deepcopy(fallback)

    @staticmethod
    def _cfg_raw(config: AstrBotConfig, key: str, default: Any = None) -> Any:
        # ``None`` is retained as the explicit missing-value probe used by
        # compatibility migrations. All ordinary public defaults come from
        # the schema manifest, not from call-site literals.
        effective = (
            default
            if default is None
            else PrivateCompanionPlugin._schema_runtime_default(key, default)
        )
        return _flat_get(config, key, effective)

    @staticmethod
    def _cfg_bool(config: AstrBotConfig, key: str, default: bool = True) -> bool:
        effective = PrivateCompanionPlugin._schema_runtime_default(
            key,
            default,
            schema_types=frozenset({"bool"}),
        )
        value = _flat_get(config, key, effective)
        if isinstance(value, str):
            text = value.strip().lower()
            parsed: bool | None = None
            if text in {"true", "1", "yes", "y", "on", "enable", "enabled", "启用", "开启", "开", "是"}:
                parsed = True
            elif text in {"false", "0", "no", "n", "off", "disable", "disabled", "停用", "关闭", "关", "否", ""}:
                parsed = False
            if parsed is not None:
                _set_into_config(config, key, parsed)
                return parsed
        return bool(value)

    @staticmethod
    def _cfg_str(config: AstrBotConfig, key: str, default: str = "", fallback: str = "") -> str:
        effective = PrivateCompanionPlugin._schema_runtime_default(
            key,
            default,
            schema_types=frozenset({"string", "text"}),
        )
        return str(_flat_get(config, key, effective)).strip() or fallback

    @staticmethod
    def _cfg_int(config: AstrBotConfig, key: str, default: int, minimum: int = 0, maximum: int | None = None) -> int:
        effective = PrivateCompanionPlugin._schema_runtime_default(
            key,
            default,
            schema_types=frozenset({"int"}),
        )
        return _safe_int(
            _flat_get(config, key, effective),
            effective,
            minimum,
            maximum,
        )

    @staticmethod
    def _cfg_float(
        config: AstrBotConfig,
        key: str,
        default: float,
        minimum: float = 0.0,
        maximum: float | None = None,
    ) -> float:
        effective = PrivateCompanionPlugin._schema_runtime_default(
            key,
            default,
            schema_types=frozenset({"float", "int"}),
        )
        return _safe_float(
            _flat_get(config, key, effective),
            effective,
            minimum,
            maximum,
        )

    @staticmethod
    def _cfg_unit_interval(config: AstrBotConfig, key: str, default: float, minimum: float = 0.0) -> float:
        effective = PrivateCompanionPlugin._schema_runtime_default(
            key,
            default,
            schema_types=frozenset({"float", "int"}),
        )
        original = _safe_float(
            _flat_get(config, key, effective),
            effective,
            minimum,
        )
        value = original / 100.0 if original > 1.0 else original
        value = max(minimum, min(1.0, value))
        if value != original:
            _set_into_config(config, key, value)
        return value

    @staticmethod
    def _p5_hash(value: Any) -> str:
        return hashlib.sha256(str(value).encode("utf-8", errors="ignore")).hexdigest()

    @staticmethod
    def _p5_event_reference(event: Any | None) -> str:
        if event is None:
            return "private_companion:background"
        for value in (
            getattr(event, "private_companion_p5_event_ref", ""),
            getattr(event, "message_id", ""),
            getattr(event, "unified_msg_origin", ""),
        ):
            text = _single_line(value, 120)
            if text:
                return text
        return f"private_companion:event:{id(event)}"

    @staticmethod
    def _p5_detect_source_kind(event: Any | None, explicit: str = "") -> str:
        candidate = _single_line(explicit, 60)
        if not candidate and event is not None:
            for attr in ("private_companion_p5_source_kind", "p5_source_kind"):
                candidate = _single_line(getattr(event, attr, ""), 60)
                if candidate:
                    break
        if not candidate and event is not None:
            components: list[Any] = []
            for attr in ("message_obj", "message_chain", "message", "message_components"):
                value = getattr(event, attr, None)
                if isinstance(value, (list, tuple)):
                    components.extend(value[:24])
                elif value is not None:
                    components.append(value)
            component_names = {type(item).__name__.lower() for item in components}
            if any("forward" in name for name in component_names):
                candidate = "forwarded_text"
            elif any("reply" in name or "quote" in name for name in component_names):
                candidate = "quoted_text"
            elif any("image" in name or "visual" in name for name in component_names):
                candidate = "vision_summary"
        allowed = {
            "policy_config", "verified_authorization", "current_user_intent", "forwarded_text",
            "quoted_text", "vision_summary", "tool_output", "web_extract", "memory_recall",
            "derived_summary", "legacy_memory", "unknown",
        }
        if candidate in allowed:
            return candidate
        return "current_user_intent" if event is not None else "policy_config"

    def _p5_issue_attestation_for_event(
        self,
        *,
        event: Any | None,
        request: Any | None,
        sink: str,
        source_kind: str = "",
    ) -> tuple[Any, Any] | None:
        """Mint a one-shot handle and bound consumer for a local Bridge call."""
        if not bool(getattr(self, "enable_p5_source_observer", False)):
            return None

        event_anchor = event if event is not None else object()
        request_anchor = request if request is not None else event_anchor
        p3_state = getattr(event_anchor, "private_companion_p5_p3_state", None)
        if p3_state is None:
            p3_state = object()
            try:
                setattr(event_anchor, "private_companion_p5_p3_state", p3_state)
            except Exception:
                pass
        source_kind = self._p5_detect_source_kind(event_anchor, source_kind)
        event_ref = self._p5_event_reference(event_anchor)
        observation = evaluate_source(
            {
                "source_kind": source_kind,
                "trust": {
                    "policy_config": "T0",
                    "verified_authorization": "T1",
                    "current_user_intent": "T2",
                    "forwarded_text": "T3",
                    "quoted_text": "T3",
                    "vision_summary": "T3",
                    "tool_output": "T3",
                    "web_extract": "T3",
                    "memory_recall": "T4",
                    "derived_summary": "T4",
                    "legacy_memory": "T4",
                    "unknown": "T4",
                }[source_kind],
                "sink": sink,
                "event_id": event_ref,
                "security_state": "allowed",
            }
        )
        trust = str(observation.get("trust") or "T4")
        source_event_ref_hash = _single_line(observation.get("safe_ref_hash"), 80) or self._p5_hash(event_ref)
        reasons = [
            code for code in observation.get("reason_codes", [])
            if code in P5_ATTESTATION_REASON_CODES
        ]
        if not reasons:
            reasons = ["invalid_segment"]
        firewall_status = str(observation.get("security_state") or "unknown")
        if firewall_status == "not_supplied":
            firewall_status = "unknown"
        try:
            handle = self.p5_attestation_registry.mint(
                request_anchor,
                event_anchor,
                p3_state,
                request_hash=self._p5_hash(f"request:{id(request_anchor)}"),
                session_hash=self._p5_hash(getattr(event_anchor, "unified_msg_origin", "background")),
                source_kind=source_kind,
                source_trust=trust,
                firewall_status=firewall_status,
                disposition=str(observation.get("disposition") or "shadow_quarantine"),
                reason_codes=reasons,
                source_event_ref_hash=source_event_ref_hash,
                sinks=(sink,),
            )
        except (P5AttestationError, KeyError, TypeError, ValueError):
            return None
        if handle is None:
            return None

        def consume(candidate: Any, requested_sink: str = sink) -> Any:
            return self.p5_attestation_registry.consume(
                candidate,
                request_anchor,
                event_anchor,
                p3_state,
                requested_sink,
            )

        return handle, consume

    def p5_source_observer_status(self) -> dict[str, Any]:
        return {
            "schema_version": "ops.p5.source_observer.v1",
            "enabled": bool(getattr(self, "enable_p5_source_observer", False)),
            "attestation": "available" if bool(getattr(self, "enable_p5_source_observer", False)) else "disabled",
            "bridge_gate": bool(getattr(self, "enable_p5_b1_bridge_gate", False)),
            "recall_gate": bool(getattr(self, "enable_p5_b1_recall_gate", False)),
            "execution_authority": "none",
        }

    @staticmethod
    def _normalize_weather_alert_min_severity(value: Any) -> str:
        """Normalize weather alert color thresholds while accepting common aliases."""
        normalized = str(value or "blue").strip().lower()
        aliases = {
            "蓝": "blue",
            "蓝色": "blue",
            "黄色": "yellow",
            "黄": "yellow",
            "橙": "orange",
            "橙色": "orange",
            "红": "red",
            "红色": "red",
            "全部": "all",
            "全部级别": "all",
            "全部等级": "all",
            "所有": "all",
            "any": "all",
        }
        normalized = aliases.get(normalized, normalized)
        return normalized if normalized in {"blue", "yellow", "orange", "red", "all"} else "blue"

    def _resolve_environment_perception_timezone(self, configured_timezone: Any) -> str:
        global_timezone = ""
        try:
            global_config = self.context.get_config()
            if callable(getattr(global_config, "get", None)):
                global_timezone = str(global_config.get("timezone") or "").strip()
        except Exception:
            global_timezone = ""
        local_tzinfo = datetime.now().astimezone().tzinfo
        system_timezone = str(getattr(local_tzinfo, "key", "") or local_tzinfo or "").strip()
        return _resolve_timezone_setting(
            configured_timezone,
            global_timezone=global_timezone,
            system_timezone=system_timezone,
        )

    @property
    def data(self) -> dict[str, Any]:
        """Return the profile store bound to the current event task."""
        active = _ACTIVE_PERSONA_ID.get()
        if active and bool(getattr(self, "enable_multi_persona_mode", False)):
            if self._sanitize_persona_id(active) == self._primary_persona_id():
                return getattr(self, "_data_default", {})
            profiles = getattr(self, "_persona_data_profiles", {})
            profile = profiles.get(active) if isinstance(profiles, dict) else None
            if isinstance(profile, dict):
                return profile
            ensure_profile = getattr(self, "_ensure_persona_profile", None)
            if callable(ensure_profile):
                profile = ensure_profile(active)
                if isinstance(profile, dict):
                    return profile
            factory = getattr(self, "_new_store", None)
            profile = factory() if callable(factory) else {}
            if not isinstance(profiles, dict):
                profiles = {}
                self._persona_data_profiles = profiles
            profiles[active] = profile
            return profile
        return getattr(self, "_data_default", {})

    def _persona_scope_manifest(self) -> dict[str, dict[str, Any]]:
        return _PERSONA_SETTING_MANIFEST

    def _primary_persona_config(self) -> Any:
        return getattr(self, "config", {})

    def _primary_persona_id(self) -> str:
        """Return the plugin's only authoritative primary persona ID."""
        return self._sanitize_persona_id(
            object.__getattribute__(self, "plugin_specific_persona_id")
            if hasattr(self, "plugin_specific_persona_id")
            else ""
        )

    def _persona_settings_for_id(self, persona_id: Any = "") -> dict[str, Any]:
        pid = self._sanitize_persona_id(persona_id or _ACTIVE_PERSONA_ID.get())
        if not pid:
            return {}
        primary = self._primary_persona_id()
        if pid == primary:
            return {}
        profile = self._ensure_persona_profile(pid)
        settings = profile.get(PERSONA_SETTINGS_KEY)
        return dict(settings) if isinstance(settings, dict) else {}

    def get_persona_setting(self, key: str, persona_id: Any = "", default: Any = None) -> Any:
        """Return one effective setting for the active or requested persona."""
        key = str(key or "").strip()
        if not key:
            return default
        manifest = self._persona_scope_manifest()
        # Runtime attributes use snake_case while AstrBot's provider schema
        # keeps legacy provider IDs in upper case. Treat those spellings as
        # aliases at the resolver boundary so sparse persona profiles written
        # by either version continue to work.
        manifest_key = key
        if manifest_key not in manifest:
            folded = key.casefold()
            manifest_key = next(
                (candidate for candidate in manifest if str(candidate).casefold() == folded),
                key,
            )
        runtime_key = (
            manifest_key.lower()
            if manifest_key.isupper() and manifest_key.endswith("_PROVIDER_ID")
            else key
        )
        active = self._sanitize_persona_id(persona_id or _ACTIVE_PERSONA_ID.get())
        enabled = bool(object.__getattribute__(self, "enable_multi_persona_mode")) if hasattr(self, "enable_multi_persona_mode") else False
        if not enabled or not active:
            try:
                return object.__getattribute__(self, runtime_key)
            except AttributeError:
                return default
        if manifest_key == "plugin_specific_persona_id":
            return active
        primary = self._primary_persona_id()
        if active == primary:
            try:
                return object.__getattribute__(self, runtime_key)
            except AttributeError:
                pass
        settings = self._persona_settings_for_id(active)
        if manifest_key not in settings and key in settings and manifest_key != key:
            # Accept the lowercase runtime spelling in hand-edited/early
            # profile files while keeping the schema's canonical key in the
            # resolver path.
            settings[manifest_key] = deepcopy(settings[key])
        try:
            primary_value = object.__getattribute__(self, runtime_key)
            primary_source: Any = {manifest_key: deepcopy(primary_value)}
        except AttributeError:
            primary_source = self._primary_persona_config()
        value = resolve_persona_setting(
            manifest_key,
            settings,
            primary_source,
            manifest=manifest,
            default=default,
        )
        # The resolver intentionally returns a copy. This keeps mutable list/
        # object settings from being modified through a runtime read.
        return value

    def persona_setting(self, key: str, default: Any = None, persona_id: Any = "") -> Any:
        """Short explicit accessor for persona-aware runtime code."""
        return self.get_persona_setting(key, persona_id=persona_id, default=default)

    def effective_persona_settings(self, persona_id: Any = "", *, include_common: bool = False) -> dict[str, Any]:
        active = self._sanitize_persona_id(persona_id or _ACTIVE_PERSONA_ID.get())
        if not active:
            active = self._primary_persona_id()
        settings = self._persona_settings_for_id(active)
        primary = self._primary_persona_id()
        if active == primary:
            result: dict[str, Any] = {}
            for key, entry in self._persona_scope_manifest().items():
                if not include_common and entry.get("scope") == "common":
                    continue
                try:
                    result[key] = deepcopy(object.__getattribute__(self, key))
                except AttributeError:
                    result[key] = resolve_persona_setting(
                        key,
                        {},
                        self._primary_persona_config(),
                        manifest=self._persona_scope_manifest(),
                    )
            return result
        return resolve_effective_settings(
            settings,
            self._primary_persona_config(),
            manifest=self._persona_scope_manifest(),
            include_common=include_common,
            include_identity=True,
        )

    @story_legacy_sync_operation("persona.profiles.startup-migrate")
    def _migrate_persona_profiles_sync(self) -> dict[str, Any]:
        """Migrate legacy persona JSON into SQLite and upgrade sparse settings."""
        result = {"ok": True, "migrated": [], "degraded": [], "skipped": []}
        profiles_dir = Path(str(getattr(self, "_persona_profiles_dir", "") or ""))
        if not profiles_dir.exists():
            return result
        primary = self._primary_persona_id()
        errors = getattr(self, "_persona_profile_errors", None)
        if not isinstance(errors, dict):
            errors = {}
            self._persona_profile_errors = errors
        candidates: dict[str, list[Path]] = {}
        for pattern in ("*.json", "*.db"):
            for path in sorted(profiles_dir.glob(pattern)):
                pid = self._persona_id_from_profile_path(path)
                if pid:
                    candidates.setdefault(pid, []).append(path)
        profiles = getattr(self, "_persona_data_profiles", None)
        if not isinstance(profiles, dict):
            profiles = {}
            self._persona_data_profiles = profiles
        for pid, paths in sorted(candidates.items()):
            if not pid or pid == primary:
                result["skipped"].append(pid or paths[0].name)
                continue
            legacy_path = self._persona_profile_path(pid)
            legacy_present = legacy_path.is_file()
            try:
                handle = self._load_secondary_persona_store_sync(pid)
                raw = handle.data
                settings = raw.get(PERSONA_SETTINGS_KEY)
                if settings is not None and not isinstance(settings, dict):
                    raise PersonaSettingsTypeError("persona_settings must be an object")
                # Legacy JSON is transformed before the SQLite write. For an
                # existing DB, keep the upgrade path below so a failed schema
                # migration leaves the already-authoritative DB untouched.
                migrated = raw if legacy_present else migrate_persona_profile(
                    raw,
                    manifest=self._persona_scope_manifest(),
                    target_version=PERSONA_SETTINGS_SCHEMA_VERSION,
                    persona_id=pid,
                    legacy_bot_name=(
                        self._persona_display_name_for_id(pid)
                        if not isinstance(settings, dict)
                        or not str(settings.get("bot_name") or "").strip()
                        else ""
                    ),
                )
                changed = migrated != raw
                if changed:
                    handle.manager.save_snapshot(deepcopy(migrated))
                profiles[pid] = migrated
                errors.pop(pid, None)
                if changed or legacy_present:
                    result["migrated"].append(pid)
            except Exception as exc:
                backup = (
                    self._backup_corrupt_persona_profile_sync(legacy_path, reason=str(exc))
                    if legacy_path.is_file()
                    else None
                )
                errors[pid] = str(exc)
                result["degraded"].append(pid)
                result["ok"] = False
                if backup is not None:
                    result.setdefault("backups", {})[pid] = str(backup)
                logger.warning(
                    "[PrivateCompanion] 人格配置迁移降级: persona=%s error=%s",
                    pid,
                    _single_line(exc, 180),
                )
        return result

    @data.setter
    def data(self, value: dict[str, Any]) -> None:
        active = _ACTIVE_PERSONA_ID.get()
        if active and bool(getattr(self, "enable_multi_persona_mode", False)):
            if self._sanitize_persona_id(active) == self._primary_persona_id():
                self._data_default = value if isinstance(value, dict) else {}
                return
            profiles = getattr(self, "_persona_data_profiles", None)
            if profiles is None:
                profiles = {}
                self._persona_data_profiles = profiles
            profiles[active] = value if isinstance(value, dict) else {}
            return
        self._data_default = value if isinstance(value, dict) else {}

    def _effective_plugin_persona_id(self) -> str:
        active = _ACTIVE_PERSONA_ID.get()
        if bool(getattr(self, "enable_multi_persona_mode", False)) and active:
            return active
        return str(runtime_persona_setting(self, 'plugin_specific_persona_id', "") or "").strip()

    def _active_persona_scope(self) -> str:
        return _ACTIVE_PERSONA_ID.get() if bool(getattr(self, "enable_multi_persona_mode", False)) else ""

    @staticmethod
    def _sanitize_persona_id(value: Any) -> str:
        text = unicodedata.normalize("NFC", str(value or ""))
        text = "".join(
            character
            for character in text
            if unicodedata.category(character) not in {"Cc", "Cs"}
        ).strip()
        return text[:96]

    def _persona_profile_stem(self, persona_id: Any) -> str:
        """Return a reversible, cross-platform-safe filename stem."""
        pid = self._sanitize_persona_id(persona_id)
        encoded_parts: list[str] = []
        for character in pid:
            if (
                character in _PERSONA_PROFILE_FORBIDDEN_FILENAME_CHARS
                or unicodedata.category(character).startswith("C")
            ):
                encoded_parts.extend(
                    f"%{byte:02X}" for byte in character.encode("utf-8")
                )
            else:
                encoded_parts.append(character)
        stem = "".join(encoded_parts)
        if stem.partition(".")[0].upper() in _WINDOWS_RESERVED_FILENAME_STEMS and stem:
            stem = f"%{ord(stem[0]):02X}{stem[1:]}"
        return stem

    def _persona_profile_filename(self, persona_id: Any) -> str:
        """Return the legacy JSON filename for one logical persona ID."""
        return f"{self._persona_profile_stem(persona_id)}.json"

    def _persona_profile_db_filename(self, persona_id: Any) -> str:
        """Return the authoritative secondary-persona SQLite filename."""
        return f"{self._persona_profile_stem(persona_id)}.db"

    def _persona_id_from_profile_path(self, path: Path) -> str:
        filename = path.name
        suffix = path.suffix.lower()
        if suffix not in {".json", ".db"}:
            return ""
        try:
            decoded = unquote(filename[: -len(suffix)], encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, ValueError):
            return ""
        return self._sanitize_persona_id(decoded)

    def _configured_multi_persona_ids(self, *, strict: bool = False) -> list[str]:
        raw = self._cfg_raw(getattr(self, "config", {}), "multi_persona_ids", [])
        if isinstance(raw, str):
            raw = re.split(r"[\s,，、]+", raw)
        elif strict and not isinstance(raw, list):
            raise PersonaConfigError(
                "multi_persona_ids has an unverifiable container"
            )
        if not isinstance(raw, (list, tuple, set)):
            raw = []
        result: list[str] = []
        for value in raw:
            if strict and not isinstance(value, str):
                raise PersonaConfigError(
                    "multi_persona_ids contains a non-text persona id"
                )
            pid = self._sanitize_persona_id(value)
            if strict and str(value or "").strip() not in {"", pid}:
                raise PersonaConfigError(
                    "multi_persona_ids contains a non-canonical persona id"
                )
            if pid and pid not in result:
                result.append(pid)
        primary = self._primary_persona_id()
        if strict and not primary:
            raise PersonaConfigError(
                "multi-persona primary id cannot be verified"
            )
        if primary:
            result = [primary, *(pid for pid in result if pid != primary)]
        return result

    def _req041_update_unified_profile_facts(
        self,
        user: dict[str, Any],
        changes: dict[str, Any],
        *,
        operation_id: str = "",
        actor_id: str = "companion",
        schedule_save: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(user, dict) or not isinstance(changes, dict) or not changes:
            return {"ok": False, "state": "skipped", "code": "profile_fact_update_skipped"}
        person_id = _single_line(user.get("unified_person_id"), 80)
        if not person_id:
            return {"ok": False, "state": "skipped", "code": "profile_identity_pending"}
        result = self._active_unified_person_registry().update_identity_profile_facts(
            person_id,
            changes,
            operation_id=(
                _single_line(operation_id, 120)
                or f"req041-profile-{uuid.uuid4().hex}"
            ),
            actor_id=actor_id,
        )
        if result.get("ok") and result.get("changed") and schedule_save:
            self._schedule_data_save(sections={"unified_person"})
        return result

    def _persona_profile_path(self, persona_id: str) -> Path:
        return Path(self._persona_profiles_dir) / self._persona_profile_filename(persona_id)

    def _persona_profile_db_path(self, persona_id: str) -> Path:
        return Path(self._persona_profiles_dir) / self._persona_profile_db_filename(persona_id)

    def _persona_profile_store_paths(self, persona_id: Any) -> tuple[Path, Path]:
        pid = self._sanitize_persona_id(persona_id)
        return self._persona_profile_path(pid), self._persona_profile_db_path(pid)

    def _persona_sqlite_registry(self) -> PersonaSqliteStoreRegistry:
        registry = getattr(self, "_persona_sqlite_store_registry", None)
        if not isinstance(registry, PersonaSqliteStoreRegistry):
            registry = PersonaSqliteStoreRegistry()
            self._persona_sqlite_store_registry = registry
        return registry

    @story_legacy_sync_operation("persona.store.load")
    def _load_secondary_persona_store_sync(
        self,
        persona_id: Any,
        *,
        prepare_payload: Any = None,
    ):
        pid = self._sanitize_persona_id(persona_id)
        if not pid or pid == self._primary_persona_id():
            raise PersonaConfigError("secondary persona SQLite requires a non-primary persona")
        legacy_path, database_path = self._persona_profile_store_paths(pid)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        if not callable(prepare_payload):
            prepare_payload = lambda payload: self._prepare_legacy_persona_payload(
                pid, payload
            )
        return load_persona_sqlite_store(
            persona_id=pid,
            legacy_json_path=legacy_path,
            sqlite_path=database_path,
            ensure_defaults=self._ensure_store_defaults,
            new_store=self._new_store,
            registry=self._persona_sqlite_registry(),
            prepare_payload=prepare_payload,
        )

    def _prepare_legacy_persona_payload(
        self,
        persona_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate and migrate a legacy JSON profile before SQLite commit."""
        settings = payload.get(PERSONA_SETTINGS_KEY)
        if settings is not None and not isinstance(settings, dict):
            raise PersonaSettingsTypeError("persona_settings must be an object")
        return migrate_persona_profile(
            payload,
            manifest=self._persona_scope_manifest(),
            target_version=PERSONA_SETTINGS_SCHEMA_VERSION,
            persona_id=persona_id,
            legacy_bot_name=(
                self._persona_display_name_for_id(persona_id)
                if not isinstance(settings, dict)
                or not str(settings.get("bot_name") or "").strip()
                else ""
            ),
        )

    def _secondary_persona_store_exists(self, persona_id: Any) -> bool:
        pid = self._sanitize_persona_id(persona_id)
        if not pid or pid == self._primary_persona_id():
            return False
        legacy_path, database_path = self._persona_profile_store_paths(pid)
        return legacy_path.is_file() or database_path.is_file()

    def _backup_corrupt_persona_profile_sync(self, path: Path, *, reason: str) -> Path | None:
        if not path.exists():
            return None
        backup = path.with_name(
            f"{path.name}.corrupt-{int(time.time())}-{uuid.uuid4().hex[:8]}.bak"
        )
        try:
            shutil.copy2(path, backup)
            logger.error(
                "[PrivateCompanion] 已隔离损坏人格 profile: source=%s backup=%s reason=%s",
                path,
                backup,
                _single_line(reason, 160),
            )
            return backup
        except Exception as exc:
            logger.error(
                "[PrivateCompanion] 人格 profile 备份失败: source=%s error=%s",
                path,
                _single_line(exc, 160),
            )
            return None

    def _persona_display_name_for_id(self, persona_id: Any) -> str:
        """Return an AstrBot persona label without requiring async I/O."""
        pid = self._sanitize_persona_id(persona_id)
        manager = getattr(getattr(self, "context", None), "persona_manager", None)
        for item in list(getattr(manager, "personas", None) or []):
            if isinstance(item, dict):
                item_id = item.get("persona_id") or item.get("id") or item.get("name")
                label = item.get("name") or item.get("label") or item_id
            else:
                item_id = getattr(item, "persona_id", None) or getattr(item, "id", None) or getattr(item, "name", None)
                label = getattr(item, "name", None) or getattr(item, "label", None) or item_id
            if self._sanitize_persona_id(item_id) == pid:
                return _single_line(label, 80) or pid
        return pid

    def _ensure_persona_profile(self, persona_id: str) -> dict[str, Any]:
        pid = self._sanitize_persona_id(persona_id) or self._primary_persona_id()
        if not pid:
            return self._data_default
        if pid == self._primary_persona_id():
            return self._data_default
        profile_errors = getattr(self, "_persona_profile_errors", None)
        if not isinstance(profile_errors, dict):
            profile_errors = {}
            self._persona_profile_errors = profile_errors
        profiles = getattr(self, "_persona_data_profiles", None)
        if profiles is None:
            profiles = {}
            self._persona_data_profiles = profiles
        existing = profiles.get(pid)
        if isinstance(existing, dict):
            return existing
        legacy_path, database_path = self._persona_profile_store_paths(pid)
        store_existed = legacy_path.is_file() or database_path.is_file()
        loaded: dict[str, Any] | None = None
        try:
            handle = self._load_secondary_persona_store_sync(pid)
            loaded = handle.data
            profile_errors.pop(pid, None)
        except Exception as exc:
            if legacy_path.is_file():
                self._backup_corrupt_persona_profile_sync(legacy_path, reason=str(exc))
            profile_errors[pid] = str(exc)
            logger.warning("[PrivateCompanion] 人格资料读取失败 persona=%s error=%s", pid, _single_line(exc, 160))
        if loaded is not None:
            profile = loaded
        else:
            factory = getattr(self, "_new_store", None)
            profile = factory() if callable(factory) else {}
        ensure_defaults = getattr(self, "_ensure_store_defaults", None)
        if callable(ensure_defaults):
            profile = ensure_defaults(profile)
        if PERSONA_SETTINGS_KEY not in profile:
            profile["persona_settings"] = {}
        elif not isinstance(profile.get(PERSONA_SETTINGS_KEY), dict):
            reason = "persona_settings must be an object"
            self._backup_corrupt_persona_profile_sync(legacy_path, reason=reason)
            profile_errors[pid] = reason
            profile[PERSONA_SETTINGS_KEY] = {}
        if store_existed and loaded is not None and not str(profile[PERSONA_SETTINGS_KEY].get("bot_name") or "").strip():
            # Pre-settings secondary profiles need an identity to be editable;
            # ordinary missing keys remain sparse and continue following the
            # primary configuration.
            profile[PERSONA_SETTINGS_KEY]["bot_name"] = self._persona_display_name_for_id(pid)
            profile[PERSONA_SETTINGS_VERSION_KEY] = PERSONA_SETTINGS_SCHEMA_VERSION
            profile.setdefault(PERSONA_SETTINGS_REVISION_KEY, 0)
            try:
                self._save_persona_profile_sync(pid, profile)
            except Exception as exc:
                logger.warning(
                    "[PrivateCompanion] 旧人格身份配置初始化落盘失败: persona=%s error=%s",
                    pid,
                    _single_line(exc, 160),
                )
        profiles[pid] = profile
        return profile

    def _save_persona_profile_sync(self, persona_id: str, data: dict[str, Any] | None = None) -> None:
        pid = self._sanitize_persona_id(persona_id)
        if not pid:
            return
        if pid == self._primary_persona_id():
            payload = data if isinstance(data, dict) else self._data_default
            self._write_data_snapshot_sync(deepcopy(payload))
            return
        payload = data if isinstance(data, dict) else self._ensure_persona_profile(pid)
        handle = self._load_secondary_persona_store_sync(pid)
        handle.manager.save_snapshot(deepcopy(payload))

    def _write_persona_reset_backup_sync(
        self,
        persona_id: str,
        snapshot: dict[str, Any],
    ) -> Path:
        pid = self._sanitize_persona_id(persona_id)
        profile_stem = (
            Path(self._persona_profile_filename(pid)).stem
            if pid
            else "single-profile"
        )
        data_root = Path(
            str(getattr(self, "data_dir", "") or "").strip()
            or Path(self._persona_profiles_dir).parent
        )
        backup_dir = data_root / "persona_backups" / profile_stem
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = backup_dir / f"{timestamp}-{uuid.uuid4().hex[:8]}.json"
        temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        payload = {
            "backup_version": 1,
            "persona_id": pid,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "data": snapshot,
        }
        try:
            temp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temp, path)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except Exception:
                pass
        return path

    @story_legacy_operation("persona.store.reset")
    async def _reset_current_persona_store(
        self,
        persona_id: Any = "",
        *,
        rebuild_today: bool = True,
        operation_id: str = "",
        _force_default_store: bool = False,
    ) -> dict[str, Any]:
        multi_enabled = bool(getattr(self, "enable_multi_persona_mode", False)) and not _force_default_store
        requested = self._sanitize_persona_id(persona_id)
        active = self._sanitize_persona_id(self._active_persona_scope())
        pid = ""
        if multi_enabled:
            configured = self._configured_multi_persona_ids()
            pid = (
                requested
                or active
                or self._sanitize_persona_id(getattr(self, "_page_current_persona_id", ""))
                or self._primary_persona_id()
                or (configured[0] if configured else "")
            )
            if not pid or pid not in set(configured):
                return {"ok": False, "message": "当前人格不在已启用的多人格列表中"}

        token = None
        if multi_enabled and active != pid:
            token = self._activate_persona_id(pid)
            if token is None:
                return {"ok": False, "message": "无法激活要重置的人格"}

        backup_path: Path | None = None
        generation = 1
        try:
            await self._flush_scheduled_data_save()
            scoped_reset: dict[str, Any] = {
                "ok": True, "state": "not_required", "code": "scoped_persona_erase_not_required",
            }
            synchronizer = getattr(self, "req041_scoped_projection_sync", None)
            migration_status = getattr(self, "req041_migration_status", None)
            if synchronizer is None and isinstance(migration_status, dict) and (
                migration_status.get("required") or migration_status.get("scoped_required")
            ):
                return {"ok": False, "message": "人格分域清理暂不可用", "code": "scoped_persona_erase_unavailable"}
            if synchronizer is not None:
                persona_ref = scoped_persona_ref(pid)
                async with self._data_lock:
                    group_sagas = self.data.get("_req041_group_reset_sagas")
                    if isinstance(group_sagas, dict) and group_sagas:
                        return {
                            "ok": False, "message": "存在未完成的群删除事务，请等待恢复完成后再重置人格",
                            "code": "group_reset_in_progress",
                        }
                    marker = self.data.get("_req041_persona_reset_saga")
                    if marker is not None and not isinstance(marker, dict):
                        return {"ok": False, "message": "人格重置恢复记录损坏", "code": "persona_reset_saga_invalid"}
                    clean_operation = _single_line(operation_id, 120)
                    if isinstance(marker, dict):
                        marker_operation = _single_line(marker.get("operation_id"), 120)
                        if (
                            marker.get("state") != "confirmed"
                            or _single_line(marker.get("persona_id"), 80) != persona_ref
                            or (clean_operation and clean_operation != marker_operation)
                            or not marker_operation
                        ):
                            return {"ok": False, "message": "人格重置恢复记录冲突", "code": "persona_reset_saga_conflict"}
                        clean_operation = marker_operation
                    else:
                        clean_operation = clean_operation or "req041-persona-reset-" + uuid.uuid4().hex
                        self.data["_req041_persona_reset_saga"] = {
                            "operation_id": clean_operation,
                            "persona_id": persona_ref,
                            "source_persona_id": pid,
                            "state": "confirmed",
                            "created_at": _now_ts(),
                        }
                        self._req041_persist_archive_saga_locked(
                            sections={"_req041_persona_reset_saga"},
                        )
                scoped_reset = self._req041_erase_scoped_persona_data(
                    pid, operation_id=clean_operation,
                )
                if not scoped_reset.get("ok"):
                    return {
                        "ok": False,
                        "message": "人格分域清理失败，已保留本地资料并将在启动时重试",
                        "code": str(scoped_reset.get("code") or "scoped_persona_erase_failed")[:120],
                        "operation_id": clean_operation,
                    }
            async with self._data_lock:
                previous = deepcopy(self.data)
                backup_snapshot = deepcopy(previous)
                backup_snapshot.pop("_req041_persona_reset_saga", None)
                lifecycle = previous.get("persona_lifecycle")
                if not isinstance(lifecycle, dict):
                    lifecycle = {}
                try:
                    previous_generation = max(
                        1,
                        int(lifecycle.get("generation", 1) or 1),
                    )
                except (TypeError, ValueError):
                    previous_generation = 1
                generation = previous_generation + 1
                backup_path = self._write_persona_reset_backup_sync(pid, backup_snapshot)

                replacement = self._new_store()
                ensure_defaults = getattr(self, "_ensure_store_defaults", None)
                if callable(ensure_defaults):
                    replacement = ensure_defaults(replacement)
                # Reset life data without resetting the persona's independent
                # configuration or its optimistic-concurrency metadata.
                for settings_key in (
                    "persona_settings",
                    "persona_settings_schema_version",
                    "persona_settings_revision",
                ):
                    if settings_key in previous:
                        replacement[settings_key] = deepcopy(previous[settings_key])
                replacement["persona_lifecycle"] = {
                    "generation": generation,
                    "reset_at": _now_ts(),
                    "previous_backup": str(backup_path),
                }
                self.data = replacement
                if bool(runtime_persona_setting(self, "default_enable_configured_targets", False)):
                    sync_targets = getattr(self, "_sync_configured_targets", None)
                    if callable(sync_targets):
                        sync_targets()
                try:
                    if multi_enabled:
                        self._write_persona_data_snapshot_sync(
                            pid,
                            deepcopy(self.data),
                        )
                        clear_dirty = getattr(self, "_clear_scheduled_data_save_dirty", None)
                        if callable(clear_dirty):
                            clear_dirty(persona_id=pid)
                        else:
                            dirty = getattr(self, "_persona_data_save_dirty", None)
                            if isinstance(dirty, set):
                                dirty.discard(pid)
                    else:
                        self._write_data_snapshot_sync(deepcopy(self.data))
                        clear_dirty = getattr(self, "_clear_scheduled_data_save_dirty", None)
                        if callable(clear_dirty):
                            clear_dirty()
                        else:
                            self._data_save_dirty = False
                except Exception:
                    self.data = previous
                    if multi_enabled:
                        self._write_persona_data_snapshot_sync(pid, previous)
                    else:
                        self._write_data_snapshot_sync(previous)
                    raise

            self._reset_persona_prompt_caches(pid)
            bookshelf_tokens = getattr(self, "_bookshelf_access_tokens", None)
            if isinstance(bookshelf_tokens, dict):
                bookshelf_tokens.clear()

            state: dict[str, Any] = {}
            plan: dict[str, Any] = {}
            rebuild_error = ""
            if rebuild_today:
                try:
                    state, plan, _ = await self._rebuild_today_after_reset()
                except Exception as exc:
                    rebuild_error = _single_line(exc, 180)
                    logger.warning(
                        "[PrivateCompanion] 当前人格资料已重置，但今日数据重建失败: persona=%s error=%s",
                        pid or "single",
                        rebuild_error,
                        exc_info=True,
                    )
            return {
                "ok": True,
                "persona_id": pid,
                "generation": generation,
                "backup_path": str(backup_path or ""),
                "state": state,
                "plan": plan,
                "rebuild_error": rebuild_error,
                "external_memory_preserved": synchronizer is None,
                "non_req041_external_memory_preserved": True,
                "scoped_memory_reset": bool(synchronizer is not None and scoped_reset.get("ok")),
                "scoped_cleanup": scoped_reset,
            }
        finally:
            if token is not None:
                self._deactivate_persona_for_event(token)

    def _persona_profile_ids(self, *, strict: bool = False) -> list[str]:
        ids = self._configured_multi_persona_ids(strict=strict)
        profiles = getattr(self, "_persona_data_profiles", {})
        if strict and not isinstance(profiles, dict):
            raise PersonaConfigError(
                "persona profile cache cannot be enumerated"
            )
        if isinstance(profiles, dict):
            for pid in profiles:
                if strict and not isinstance(pid, str):
                    raise PersonaConfigError(
                        "persona profile cache contains a non-text id"
                    )
                clean = self._sanitize_persona_id(pid)
                if strict and pid.strip() not in {"", clean}:
                    raise PersonaConfigError(
                        "persona profile cache contains a non-canonical id"
                    )
                if clean and clean not in ids:
                    ids.append(clean)
                elif strict and not clean:
                    raise PersonaConfigError(
                        "persona profile enumeration contains an invalid id"
                    )
        try:
            profiles_dir = Path(self._persona_profiles_dir)
            if strict:
                try:
                    root_stat = profiles_dir.lstat()
                except FileNotFoundError:
                    paths = []
                else:
                    if not stat.S_ISDIR(root_stat.st_mode):
                        raise PersonaConfigError(
                            "persona profile root is not a regular directory"
                        )
                    paths = sorted(
                        profiles_dir.iterdir(),
                        key=lambda item: item.name,
                    )
                candidates = [
                    path
                    for path in paths
                    if path.suffix.lower() in {".db", ".json"}
                ]
            else:
                candidates = [
                    path
                    for pattern in ("*.db", "*.json")
                    for path in profiles_dir.glob(pattern)
                ]
            for path in candidates:
                if strict:
                    entry_stat = path.lstat()
                    if not stat.S_ISREG(entry_stat.st_mode):
                        raise PersonaConfigError(
                            "persona profile entry is not a regular file"
                        )
                clean = self._persona_id_from_profile_path(path)
                if strict and not clean:
                    raise PersonaConfigError(
                        "persona profile entry cannot be identified"
                    )
                if strict:
                    expected_name = (
                        self._persona_profile_db_filename(clean)
                        if path.suffix == ".db"
                        else self._persona_profile_filename(clean)
                    )
                    if path.name != expected_name:
                        raise PersonaConfigError(
                            "persona profile entry is not canonical"
                        )
                if clean and clean not in ids:
                    ids.append(clean)
        except Exception:
            if strict:
                raise PersonaConfigError(
                    "persona profile enumeration is unavailable"
                ) from None
        return ids

    def _persona_profile_snapshot_if_exists(self, persona_id: Any) -> dict[str, Any] | None:
        """Read an existing profile without creating one as a side effect."""
        pid = self._sanitize_persona_id(persona_id)
        if not pid:
            return None
        profiles = getattr(self, "_persona_data_profiles", {})
        if isinstance(profiles, dict) and isinstance(profiles.get(pid), dict):
            return profiles[pid]
        if not self._secondary_persona_store_exists(pid):
            return None
        try:
            payload = self._load_secondary_persona_store_sync(pid).data
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def _persona_profile_snapshot_read_only(
        self,
        persona_id: Any,
    ) -> dict[str, Any] | None:
        """Inspect persisted profile state without migration or initialization."""
        pid = self._sanitize_persona_id(persona_id)
        if not pid:
            raise PersonaConfigError(
                "read-only persisted profile requires a persona id"
            )
        legacy_path, database_path = self._persona_profile_store_paths(pid)
        return read_persona_store_snapshot_read_only(
            persona_id=pid,
            legacy_json_path=legacy_path,
            sqlite_path=database_path,
        )

    def _persona_config_exists(self, persona_id: Any) -> bool:
        pid = self._sanitize_persona_id(persona_id)
        if not pid or pid == self._primary_persona_id():
            return bool(pid)
        errors = getattr(self, "_persona_profile_errors", {})
        if isinstance(errors, dict) and pid in errors:
            return False
        profile = self._persona_profile_snapshot_if_exists(pid)
        if not isinstance(profile, dict):
            return False
        settings = profile.get(PERSONA_SETTINGS_KEY)
        if not isinstance(settings, dict) or not _single_line(settings.get("bot_name"), 80):
            return False
        try:
            revision = int(profile.get(PERSONA_SETTINGS_REVISION_KEY) or 0)
        except (TypeError, ValueError):
            revision = 0
        if revision > 0 or any(key != "bot_name" for key in settings):
            return True
        factory = getattr(self, "_new_store", None)
        if not callable(factory):
            return False
        try:
            baseline = factory()
            ensure_defaults = getattr(self, "_ensure_store_defaults", None)
            if callable(ensure_defaults):
                baseline = ensure_defaults(baseline)
            candidate = deepcopy(profile)
            for key in (
                PERSONA_SETTINGS_KEY,
                PERSONA_SETTINGS_VERSION_KEY,
                PERSONA_SETTINGS_REVISION_KEY,
            ):
                candidate.pop(key, None)
                baseline.pop(key, None)
            for payload in (candidate, baseline):
                for key in tuple(payload):
                    if payload.get(key) in (None, "", [], {}) and key not in (
                        baseline if payload is candidate else candidate
                    ):
                        payload.pop(key, None)
            return candidate != baseline
        except Exception:
            return False

    def _persona_config_profile_ids(self) -> list[str]:
        primary = self._primary_persona_id()
        candidates: list[str] = []
        profiles = getattr(self, "_persona_data_profiles", {})
        if isinstance(profiles, dict):
            candidates.extend(map(str, profiles))
        try:
            profiles_dir = Path(self._persona_profiles_dir)
            for pattern in ("*.db", "*.json"):
                candidates.extend(
                    self._persona_id_from_profile_path(path)
                    for path in profiles_dir.glob(pattern)
                )
        except Exception:
            pass
        result: list[str] = []
        for candidate in candidates:
            pid = self._sanitize_persona_id(candidate)
            if (
                pid
                and pid != primary
                and pid not in result
                and self._persona_config_exists(pid)
            ):
                result.append(pid)
        return result

    def _persona_window_bindings(self) -> dict[str, str]:
        # Compatibility surface only. AstrBot is the sole routing authority.
        return {}

    def _persona_window_bindings_store_path(self) -> Path:
        configured = str(getattr(self, "_persona_window_bindings_file", "") or "").strip()
        if configured:
            return Path(configured)
        data_dir = str(getattr(self, "data_dir", "") or "").strip()
        if data_dir:
            return Path(data_dir) / "persona_window_bindings.json"
        profiles_dir = Path(str(getattr(self, "_persona_profiles_dir", "persona_profiles")))
        return profiles_dir.parent / "persona_window_bindings.json"

    def _retire_legacy_persona_routing_sync(self) -> dict[str, Any]:
        """Back up legacy plugin-owned routes without using them at runtime."""
        config_bindings = self._cfg_raw(
            getattr(self, "config", {}), "multi_persona_window_bindings", {}
        )
        config_bindings = dict(config_bindings) if isinstance(config_bindings, dict) else {}
        path = self._persona_window_bindings_store_path()
        file_bindings: dict[str, Any] = {}
        file_error = ""
        if path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict) and isinstance(payload.get("bindings"), dict):
                    file_bindings = dict(payload["bindings"])
                elif isinstance(payload, dict):
                    file_bindings = dict(payload)
                else:
                    file_error = "legacy_binding_root_invalid"
            except Exception as exc:
                file_error = _single_line(exc, 160) or "legacy_binding_read_failed"
        merged = {
            _single_line(window, 240): self._sanitize_persona_id(persona_id)
            for window, persona_id in {**config_bindings, **file_bindings}.items()
            if _single_line(window, 240) and self._sanitize_persona_id(persona_id)
        }
        backup_path = path.with_name("persona_window_bindings.retired.json")
        if (merged or file_error) and not backup_path.exists():
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = backup_path.with_name(
                f".{backup_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            )
            try:
                temporary.write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "retired_at": time.time(),
                            "reason": "astrbot_is_persona_routing_authority",
                            "config_bindings": config_bindings,
                            "file_bindings": file_bindings,
                            "source_file": str(path),
                            "source_file_error": file_error,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                os.replace(temporary, backup_path)
            finally:
                temporary.unlink(missing_ok=True)
        status = {
            "ignored": bool(merged or file_error),
            "count": len(merged),
            "backup_path": str(backup_path) if backup_path.exists() else "",
            "warning_code": "legacy_persona_binding_ignored" if merged or file_error else "",
            "source_file_preserved": path.is_file(),
            "source_file_error": file_error,
        }
        self._legacy_persona_window_bindings = merged
        self._legacy_persona_routing_status = status
        if status["ignored"]:
            logger.warning(
                "[PrivateCompanion] 已停用插件窗口人格路由，AstrBot 为唯一权威: count=%s backup=%s error=%s",
                len(merged),
                status["backup_path"] or "-",
                file_error or "-",
            )
        return status


    def _persona_id_for_event(self, event: Any) -> tuple[str, str]:
        """Return a cached plugin scope without consulting legacy bindings."""
        umo = str(getattr(event, "unified_msg_origin", "") or "").strip()
        cached = getattr(event, "_private_companion_persona_route_decision", None)
        pid = self._sanitize_persona_id(
            cached.get("plugin_persona_id") if isinstance(cached, dict) else ""
        )
        enabled = set(self._configured_multi_persona_ids())
        if pid not in enabled:
            pid = self._primary_persona_id()
        return pid, umo

    @staticmethod
    async def _await_if_needed(value: Any) -> Any:
        return await value if inspect.isawaitable(value) else value

    def _astrbot_persona_exists(self, persona_id: Any) -> bool:
        pid = self._sanitize_persona_id(persona_id)
        if not pid:
            return False
        manager = getattr(getattr(self, "context", None), "persona_manager", None)
        getter = getattr(manager, "get_persona_v3_by_id", None)
        if callable(getter):
            try:
                return getter(pid) is not None
            except Exception:
                pass
        for item in list(getattr(manager, "personas_v3", None) or []) + list(
            getattr(manager, "personas", None) or []
        ):
            if isinstance(item, dict):
                item_id = item.get("name") or item.get("persona_id") or item.get("id")
            else:
                item_id = (
                    getattr(item, "persona_id", None)
                    or getattr(item, "name", None)
                    or getattr(item, "id", None)
                )
            if self._sanitize_persona_id(item_id) == pid:
                return True
        return False

    async def _astrbot_effective_persona_for_event(self, event: Any) -> dict[str, Any]:
        """Resolve AstrBot's request persona using AstrBot's own precedence."""
        umo = str(getattr(event, "unified_msg_origin", "") or "").strip()
        result = {
            "persona_id": "",
            "source": "unresolved",
            "exists": False,
            "explicit_none": False,
            "umo": umo,
            "error": "",
        }
        if not umo:
            result["error"] = "umo_missing"
            return result
        context = getattr(self, "context", None)
        conversation_persona = None
        try:
            conversation_manager = getattr(context, "conversation_manager", None)
            if conversation_manager is not None:
                cid = await self._await_if_needed(
                    conversation_manager.get_curr_conversation_id(umo)
                )
                if cid:
                    conversation = await self._await_if_needed(
                        conversation_manager.get_conversation(umo, cid)
                    )
                    conversation_persona = getattr(conversation, "persona_id", None)

            config_getter = getattr(context, "get_config", None)
            try:
                astrbot_config = config_getter(umo=umo) if callable(config_getter) else {}
            except TypeError:
                astrbot_config = config_getter() if callable(config_getter) else {}
            provider_settings = (
                astrbot_config.get("provider_settings", {})
                if isinstance(astrbot_config, dict)
                else {}
            )
            platform_getter = getattr(event, "get_platform_name", None)
            platform_name = (
                str(platform_getter() or "") if callable(platform_getter) else ""
            )
            persona_manager = getattr(context, "persona_manager", None)
            resolver = getattr(persona_manager, "resolve_selected_persona", None)
            if callable(resolver):
                resolved = await self._await_if_needed(
                    resolver(
                        umo=umo,
                        conversation_persona_id=conversation_persona,
                        platform_name=platform_name,
                        provider_settings=provider_settings,
                    )
                )
                selected = resolved[0] if isinstance(resolved, (list, tuple)) and resolved else ""
                persona = resolved[1] if isinstance(resolved, (list, tuple)) and len(resolved) > 1 else None
                forced = resolved[2] if isinstance(resolved, (list, tuple)) and len(resolved) > 2 else ""
                pid = self._sanitize_persona_id(selected)
                result.update(
                    {
                        "persona_id": pid,
                        "source": (
                            "session_rule"
                            if forced
                            else "explicit_none"
                            if conversation_persona == "[%None]"
                            else "conversation"
                            if conversation_persona
                            else "provider_default"
                        ),
                        "exists": persona is not None,
                        "explicit_none": selected == "[%None]" or conversation_persona == "[%None]",
                    }
                )
                return result

            selected = conversation_persona
            source = "conversation"
            if selected is None:
                selected = provider_settings.get("default_personality")
                source = "provider_default"
            result.update(
                {
                    "persona_id": self._sanitize_persona_id(selected),
                    "source": "explicit_none" if selected == "[%None]" else source,
                    "exists": self._astrbot_persona_exists(selected),
                    "explicit_none": selected == "[%None]",
                    "error": "astrbot_effective_persona_resolver_unavailable",
                }
            )
        except Exception as exc:
            result["error"] = _single_line(exc, 160) or "persona_resolution_failed"
        return result

    async def _conversation_persona_id_for_event(self, event: Any) -> str:
        """Compatibility wrapper returning AstrBot's final effective persona."""
        resolved = await self._astrbot_effective_persona_for_event(event)
        return self._sanitize_persona_id(resolved.get("persona_id"))

    async def _validate_proactive_persona_delivery(
        self,
        target_umo: Any,
        scheduled_persona_id: Any,
    ) -> dict[str, Any]:
        """Validate the last-mile target without changing AstrBot's routing."""
        umo = _single_line(target_umo, 240)
        multi = bool(getattr(self, "enable_multi_persona_mode", False))
        primary = self._primary_persona_id()
        scheduled = self._sanitize_persona_id(
            scheduled_persona_id
            or _ACTIVE_PERSONA_ID.get()
            or ("" if multi else primary)
        )
        # In single-persona mode an empty plugin_specific_persona_id means
        # “use AstrBot's current/default persona”, not a persona mismatch.
        # Keep delivery allowed and emit one actionable configuration warning.
        if not multi and not primary and umo:
            await self._record_persona_routing_warning(
                code="persona.route.plugin_persona_unspecified",
                channel="proactive",
                disposition="sent_with_warning",
                reason_code="plugin_persona_unspecified",
                window_key=umo,
            )
            return {
                "ok": True,
                "action": "sent_with_warning",
                "astrbot_persona_id": "",
                "scheduled_persona_id": "",
                "reason_code": "plugin_persona_unspecified",
            }
        event = SimpleNamespace(
            unified_msg_origin=umo,
            get_platform_name=lambda: umo.partition(":")[0],
        )
        resolved = await self._astrbot_effective_persona_for_event(event)
        astrbot_persona = self._sanitize_persona_id(resolved.get("persona_id"))
        reason = ""
        if not umo:
            reason = "target_umo_missing"
        elif not astrbot_persona:
            reason = "astrbot_persona_unresolved"
        elif not resolved.get("exists"):
            reason = "astrbot_persona_missing"
        elif not scheduled:
            reason = "scheduled_persona_missing"
        elif astrbot_persona != scheduled:
            reason = "target_persona_mismatch"
        elif multi:
            ready, readiness_reason = self._persona_profile_route_status(scheduled)
            if not ready:
                reason = readiness_reason

        if not reason:
            return {
                "ok": True,
                "action": "matched",
                "astrbot_persona_id": astrbot_persona,
                "scheduled_persona_id": scheduled,
                "reason_code": "",
            }
        if not multi:
            await self._record_persona_routing_warning(
                code="persona.route.proactive_single_mismatch_allowed",
                channel="proactive",
                disposition="sent_with_warning",
                reason_code=reason,
                window_key=umo,
                requested_persona_id=astrbot_persona,
                resolved_persona_id=scheduled,
                active_persona_id=scheduled,
            )
            return {
                "ok": True,
                "action": "sent_with_warning",
                "astrbot_persona_id": astrbot_persona,
                "scheduled_persona_id": scheduled,
                "reason_code": reason,
            }
        await self._record_persona_routing_warning(
            code="persona.route.proactive_multi_mismatch_blocked",
            channel="proactive",
            disposition="blocked",
            reason_code=reason,
            window_key=umo,
            requested_persona_id=astrbot_persona,
            resolved_persona_id=scheduled,
            active_persona_id=scheduled,
        )
        return {
            "ok": False,
            "action": "blocked",
            "astrbot_persona_id": astrbot_persona,
            "scheduled_persona_id": scheduled,
            "reason_code": reason,
        }

    def _persona_profile_route_status(self, persona_id: Any) -> tuple[bool, str]:
        pid = self._sanitize_persona_id(persona_id)
        primary = self._primary_persona_id()
        if not pid:
            return False, "persona_id_missing"
        if pid not in set(self._configured_multi_persona_ids()):
            return False, "persona_not_enabled"
        if pid == primary:
            return True, "primary"
        errors = getattr(self, "_persona_profile_errors", {})
        if isinstance(errors, dict) and pid in errors:
            return False, "persona_profile_degraded"
        if not self._persona_config_exists(pid):
            return False, "persona_config_missing"
        return True, "configured"

    async def _record_persona_routing_warning(
        self,
        *,
        code: str,
        channel: str,
        disposition: str,
        reason_code: str,
        window_key: Any = "",
        requested_persona_id: Any = "",
        resolved_persona_id: Any = "",
        active_persona_id: Any = "",
    ) -> None:
        """Persist one global, content-free persona routing diagnostic."""
        now = time.time()
        window = _single_line(window_key, 180)
        requested = self._sanitize_persona_id(requested_persona_id)
        resolved = self._sanitize_persona_id(resolved_persona_id)
        active = self._sanitize_persona_id(active_persona_id)
        reason = _single_line(reason_code, 80) or "unknown"
        signature = "|".join((code, reason, window, requested, resolved, active))
        record_id = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:20]
        should_schedule = False
        lock = getattr(self, "_data_lock", None)

        async def update() -> None:
            nonlocal should_schedule
            store = getattr(self, "_data_default", None)
            if not isinstance(store, dict):
                return
            root = store.get("persona_routing_warnings")
            if not isinstance(root, dict):
                root = {"schema_version": 1, "items": []}
                store["persona_routing_warnings"] = root
            items = root.get("items")
            if not isinstance(items, list):
                items = []
                root["items"] = items
            item = next(
                (candidate for candidate in items if isinstance(candidate, dict) and candidate.get("id") == record_id),
                None,
            )
            if item is None:
                item = {"id": record_id, "first_ts": now, "count": 0}
                items.append(item)
                should_schedule = True
            item.update(
                {
                    "schema_version": 1,
                    "code": _single_line(code, 100),
                    "level": "error" if disposition == "blocked" else "warn",
                    "channel": _single_line(channel, 24),
                    "disposition": _single_line(disposition, 24),
                    "reason_code": reason,
                    "source": "persona_router",
                    "window_key": window,
                    "requested_persona_id": requested,
                    "resolved_persona_id": resolved,
                    "active_persona_id": active,
                    "primary_persona_id": self._primary_persona_id(),
                    "multi_persona": bool(getattr(self, "enable_multi_persona_mode", False)),
                    "last_ts": now,
                    "count": int(item.get("count") or 0) + 1,
                }
            )
            items.sort(key=lambda candidate: float((candidate or {}).get("last_ts") or 0), reverse=True)
            del items[120:]
            save_marks = getattr(self, "_persona_routing_warning_save_marks", None)
            if not isinstance(save_marks, dict):
                save_marks = {}
                self._persona_routing_warning_save_marks = save_marks
            previous_save = float(save_marks.get(record_id) or 0)
            if now - previous_save >= 60:
                save_marks[record_id] = now
                should_schedule = True

        if isinstance(lock, asyncio.Lock):
            async with lock:
                await update()
        else:
            await update()
        if should_schedule:
            scheduler = getattr(self, "_schedule_default_data_save", None)
            if callable(scheduler):
                clear_token = _ACTIVE_PERSONA_ID.set("")
                try:
                    scheduler(sections={"persona_routing_warnings"}, delay=0.2)
                finally:
                    _ACTIVE_PERSONA_ID.reset(clear_token)
        log_marks = getattr(self, "_persona_routing_warning_log_marks", None)
        if not isinstance(log_marks, dict):
            log_marks = {}
            self._persona_routing_warning_log_marks = log_marks
        if now - float(log_marks.get(record_id) or 0) >= 300:
            log_marks[record_id] = now
            logger.warning(
                "[PrivateCompanion] 人格路由告警 code=%s reason=%s umo=%s astrbot=%s plugin=%s action=%s",
                code,
                reason,
                window or "-",
                requested or "-",
                active or "-",
                disposition,
            )

    async def _activate_persona_for_event_context(self, event: Any) -> tuple[Any, str]:
        if not bool(getattr(self, "enable_multi_persona_mode", False)):
            if not self._primary_persona_id():
                await self._record_persona_routing_warning(
                    code="persona.route.plugin_persona_unspecified",
                    channel="passive",
                    disposition="sent_with_warning",
                    reason_code="plugin_persona_unspecified",
                    window_key=getattr(event, "unified_msg_origin", ""),
                )
            return None, ""
        active = _ACTIVE_PERSONA_ID.get()
        if active:
            return None, active
        cached = getattr(event, "_private_companion_persona_route_decision", None)
        if isinstance(cached, dict):
            pid = self._sanitize_persona_id(cached.get("plugin_persona_id"))
            if pid:
                return _ACTIVE_PERSONA_ID.set(pid), pid

        primary = self._primary_persona_id()
        resolved = await self._astrbot_effective_persona_for_event(event)
        astrbot_persona = self._sanitize_persona_id(resolved.get("persona_id"))
        legacy_bindings = getattr(self, "_legacy_persona_window_bindings", {})
        legacy_persona = self._sanitize_persona_id(
            legacy_bindings.get(str(resolved.get("umo") or ""), "")
            if isinstance(legacy_bindings, dict)
            else ""
        )
        if legacy_persona and legacy_persona != astrbot_persona:
            await self._record_persona_routing_warning(
                code="persona.route.legacy_binding_ignored",
                channel="passive",
                disposition="ignored",
                reason_code="astrbot_is_routing_authority",
                window_key=resolved.get("umo"),
                requested_persona_id=legacy_persona,
                resolved_persona_id=astrbot_persona,
                active_persona_id=astrbot_persona,
            )
        ready, route_reason = self._persona_profile_route_status(astrbot_persona)
        if resolved.get("explicit_none"):
            ready, route_reason = False, "astrbot_persona_explicit_none"
        elif not astrbot_persona:
            ready, route_reason = False, "astrbot_persona_unresolved"
        elif not resolved.get("exists"):
            ready, route_reason = False, "astrbot_persona_missing"
        pid = astrbot_persona if ready else primary
        if not pid:
            await self._record_persona_routing_warning(
                code="persona.route.passive_primary_fallback",
                channel="passive",
                disposition="fallback_unavailable",
                reason_code="primary_persona_invalid",
                window_key=resolved.get("umo"),
                requested_persona_id=astrbot_persona,
            )
            return None, ""
        primary_ready, primary_reason = self._persona_profile_route_status(primary)
        if not ready and not primary_ready:
            await self._record_persona_routing_warning(
                code="persona.route.passive_primary_fallback",
                channel="passive",
                disposition="fallback_unavailable",
                reason_code=f"{route_reason}:{primary_reason}",
                window_key=resolved.get("umo"),
                requested_persona_id=astrbot_persona,
                resolved_persona_id=primary,
            )
            return None, ""

        decision = {
            "astrbot_persona_id": astrbot_persona,
            "plugin_persona_id": pid,
            "source": resolved.get("source"),
            "reason_code": route_reason,
            "fallback": not ready,
            "umo": resolved.get("umo"),
        }
        try:
            setattr(event, "_private_companion_persona_route_decision", decision)
            setattr(event, "private_companion_astrbot_persona_id", astrbot_persona)
            setattr(event, "private_companion_persona_id", pid)
            setattr(event, "private_companion_persona_window", "")
            setattr(event, "private_companion_persona_conflict", {})
        except Exception:
            pass
        if not ready:
            await self._record_persona_routing_warning(
                code="persona.route.passive_primary_fallback",
                channel="passive",
                disposition="fallback",
                reason_code=route_reason,
                window_key=resolved.get("umo"),
                requested_persona_id=astrbot_persona,
                resolved_persona_id=primary,
                active_persona_id=pid,
            )
        self._ensure_persona_profile(pid)
        return _ACTIVE_PERSONA_ID.set(pid), pid

    def _activate_persona_for_event(self, event: Any) -> tuple[Any, str]:
        """Legacy synchronous activation uses only a prior async decision."""
        if not bool(getattr(self, "enable_multi_persona_mode", False)):
            return None, ""
        pid, _ = self._persona_id_for_event(event)
        if not pid:
            return None, ""
        self._ensure_persona_profile(pid)
        return _ACTIVE_PERSONA_ID.set(pid), pid

    def _activate_persona_id(self, persona_id: Any, *, allow_inactive: bool = False) -> Any:
        pid = self._sanitize_persona_id(persona_id)
        if not pid or not bool(getattr(self, "enable_multi_persona_mode", False)):
            return None
        profile_errors = getattr(self, "_persona_profile_errors", {})
        if isinstance(profile_errors, dict) and pid in profile_errors and not allow_inactive:
            return None
        if not allow_inactive and pid not in set(self._configured_multi_persona_ids()):
            return None
        if pid != self._primary_persona_id() and not self._persona_config_exists(pid):
            return None
        self._ensure_persona_profile(pid)
        return _ACTIVE_PERSONA_ID.set(pid)

    def _deactivate_persona_for_event(self, token: Any) -> None:
        if token is not None:
            _ACTIVE_PERSONA_ID.reset(token)

    def _clear_persona_runtime_cache(self, profile: dict[str, Any]) -> None:
        if not isinstance(profile, dict):
            return
        for key in tuple(profile.keys()):
            lowered = str(key).lower()
            if "cache" in lowered or lowered in {"conversation_history", "recent_context", "pending_context"}:
                profile.pop(key, None)

    def _reset_persona_prompt_caches(self, *persona_ids: Any) -> None:
        for attr, value in (
            ("_default_persona_prompt_cache", ""),
            ("_default_persona_prompt_cache_at", 0.0),
            ("_default_persona_prompt_cache_umo", ""),
            ("_default_persona_prompt_cache_persona_id", ""),
            ("_default_persona_prompt_cache_by_scope", {}),
        ):
            try:
                setattr(self, attr, deepcopy(value))
            except Exception:
                pass

        ids = {
            pid
            for pid in (self._sanitize_persona_id(value) for value in persona_ids)
            if pid
        }
        cache = getattr(self, "_passive_light_injection_cache", None)
        if not isinstance(cache, dict) or "text" in cache or not ids:
            self._passive_light_injection_cache = {}
            return
        next_cache = dict(cache)
        for pid in ids:
            next_cache.pop(pid, None)
        self._passive_light_injection_cache = next_cache

    @story_legacy_sync_operation("persona.profile.migrate")
    def _migrate_persona_profile(self, source_persona_id: Any, target_persona_id: Any, keys: list[Any]) -> dict[str, Any]:
        source = self._sanitize_persona_id(source_persona_id)
        target = self._sanitize_persona_id(target_persona_id)
        if not source or not target or source == target:
            return {"ok": False, "message": "源人格和目标人格必须不同"}
        if not bool(getattr(self, "enable_multi_persona_mode", False)):
            return {
                "ok": False,
                "code": "persona_migration_multi_persona_disabled",
                "message": "请先开启多人格模式后再迁移人格资料",
            }
        enabled_ids = set(self._configured_multi_persona_ids())
        if source not in enabled_ids:
            return {
                "ok": False,
                "code": "persona_migration_source_not_enabled",
                "message": "来源人格未在已保存的人格拓扑中启用",
            }
        if target not in enabled_ids:
            return {
                "ok": False,
                "code": "persona_migration_target_not_enabled",
                "message": "目标人格未在已保存的人格拓扑中启用",
            }
        primary = self._primary_persona_id()

        def eligible(persona_id: str) -> bool:
            # The primary intentionally has no separate persona JSON; its
            # authoritative data is the single-persona store.
            return persona_id == primary or self._persona_config_exists(persona_id)

        if not eligible(source):
            return {
                "ok": False,
                "code": "persona_migration_source_config_missing",
                "message": "来源人格尚未建立有效的人格配置文件",
            }
        if not eligible(target):
            return {
                "ok": False,
                "code": "persona_migration_target_config_missing",
                "message": "目标人格尚未建立有效的人格配置文件",
            }
        source_data = self._ensure_persona_profile(source)
        target_data = self._ensure_persona_profile(target)
        source_before = deepcopy(source_data)
        target_before = deepcopy(target_data)
        source_next = deepcopy(source_data)
        target_next = deepcopy(target_data)
        selected = [str(key).strip() for key in keys if str(key).strip()]
        if not selected:
            selected = ["daily_plan", "daily_state", "bot_diaries", "users", "groups", "memo_notes", "token_usage"]
        migration_keys = list(selected)
        if "bot_diaries" in migration_keys:
            for companion_key in (
                "diary_generated_day",
                "daily_diary_deleted_days",
                "daily_diary_delete_revision",
            ):
                if companion_key not in migration_keys:
                    migration_keys.append(companion_key)
        for key in migration_keys:
            source_settings = source_next.get("persona_settings") if isinstance(source_next.get("persona_settings"), dict) else {}
            target_settings = target_next.setdefault("persona_settings", {})
            if key in source_settings:
                target_settings[key] = deepcopy(source_settings[key])
            elif key in source_next:
                target_next[key] = deepcopy(source_next[key])
        if "bot_diaries" in migration_keys:
            diaries = source_next.get("bot_diaries")
            diary_days: list[str] = []
            if isinstance(diaries, list):
                diary_days = [
                    _single_line(item.get("date"), 16)
                    for item in diaries
                    if isinstance(item, dict)
                ]
            elif isinstance(diaries, dict):
                diary_days = [
                    _single_line(
                        (item.get("date") if isinstance(item, dict) else "") or stored_date,
                        16,
                    )
                    for stored_date, item in diaries.items()
                ]
            valid_days = [day for day in diary_days if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day)]
            source_marker = _single_line(source_next.get("diary_generated_day"), 16)
            target_next["diary_generated_day"] = (
                source_marker
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", source_marker)
                else max(valid_days, default="")
            )
            source_deleted_days = source_next.get("daily_diary_deleted_days")
            target_next["daily_diary_deleted_days"] = deepcopy(
                source_deleted_days if isinstance(source_deleted_days, list) else []
            )
            try:
                target_next["daily_diary_delete_revision"] = max(
                    0,
                    int(source_next.get("daily_diary_delete_revision") or 0),
                )
            except (TypeError, ValueError, OverflowError):
                target_next["daily_diary_delete_revision"] = 0
        self._clear_persona_runtime_cache(source_next)
        self._clear_persona_runtime_cache(target_next)
        try:
            self._save_persona_profile_sync(source, source_next)
            self._save_persona_profile_sync(target, target_next)
        except Exception as exc:
            for persona_id, previous in ((source, source_before), (target, target_before)):
                try:
                    self._save_persona_profile_sync(persona_id, previous)
                except Exception:
                    pass
            return {
                "ok": False,
                "message": f"人格资料迁移落盘失败: {_single_line(exc, 120)}",
            }
        source_data.clear()
        source_data.update(source_next)
        target_data.clear()
        target_data.update(target_next)
        self._reset_persona_prompt_caches(source, target)
        return {"ok": True, "source_persona_id": source, "target_persona_id": target, "keys": migration_keys, "cache_cleared": True}

    @story_legacy_operation("persona.profile.migrate-transaction")
    async def _migrate_persona_profile_async(
        self,
        source_persona_id: Any,
        target_persona_id: Any,
        keys: list[Any],
    ) -> dict[str, Any]:
        await self._flush_scheduled_data_save()
        async with self._data_lock:
            return self._migrate_persona_profile(
                source_persona_id,
                target_persona_id,
                keys,
            )

    def _switch_persona_for_window(
        self,
        persona_id: Any,
        *,
        window_key: str = "",
        source_persona_id: str = "",
        migrate_keys: list[Any] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "status_code": 410,
            "code": "plugin_persona_routing_removed",
            "message": "窗口人格由 AstrBot 管理，插件不再提供窗口绑定",
            "routing_authority": "astrbot",
        }

    async def _switch_persona_for_window_async(
        self,
        *args,
        persist: bool = False,
        **kwargs,
    ) -> dict[str, Any]:
        return self._switch_persona_for_window(
            args[0] if args else kwargs.get("persona_id", "")
        )

    def _multi_persona_status(self) -> dict[str, Any]:
        enabled = bool(getattr(self, "enable_multi_persona_mode", False))
        primary = self._primary_persona_id()
        configured_profiles = self._persona_config_profile_ids()
        profile_labels: dict[str, str] = {}
        if primary:
            profile_labels[primary] = _single_line(
                getattr(self, "bot_name", "") or primary,
                80,
            )
        for pid in configured_profiles:
            profile = self._persona_profile_snapshot_if_exists(pid)
            settings = profile.get(PERSONA_SETTINGS_KEY) if isinstance(profile, dict) else {}
            profile_labels[pid] = _single_line(
                settings.get("bot_name") if isinstance(settings, dict) else "",
                80,
            ) or pid
        return {
            "enabled": enabled,
            "primary": primary,
            "enabled_ids": self._configured_multi_persona_ids(),
            "configured_profiles": configured_profiles,
            "profiles": [primary, *configured_profiles] if primary else configured_profiles,
            "profile_labels": profile_labels,
            "window_bindings": {},
            "window_conflicts": {},
            "window_bindings_revision": 0,
            "routing_authority": "astrbot",
            "legacy_routing": deepcopy(getattr(self, "_legacy_persona_routing_status", {})),
            "primary_setup": {
                "required": bool(
                    getattr(self, "_multi_persona_primary_requires_configuration", False)
                    or getattr(self, "_multi_persona_primary_invalid", False)
                    or (getattr(self, "_multi_persona_enable_requested", False) and not primary)
                ),
                "invalid": bool(getattr(self, "_multi_persona_primary_invalid", False)),
                "legacy_candidate": self._sanitize_persona_id(
                    getattr(self, "_legacy_multi_persona_primary_id_candidate", "")
                ),
                "legacy_mismatch": deepcopy(
                    getattr(self, "_multi_persona_primary_id_mismatch", {})
                ),
            },
            "profile_errors": deepcopy(getattr(self, "_persona_profile_errors", {})),
            "settings_migration": deepcopy(getattr(self, "_persona_settings_migration_status", {})),
        }

    def _persona_config_state(self, persona_id: Any) -> dict[str, Any]:
        pid = self._sanitize_persona_id(persona_id)
        primary = self._primary_persona_id()
        if not pid:
            pid = primary
        if not pid:
            raise PersonaConfigError("人格 ID 不能为空")
        is_primary = pid == primary
        if not is_primary and not self._persona_config_exists(pid):
            raise PersonaConfigError("该人格尚未创建独立配置")
        profile = self._ensure_persona_profile(pid)
        raw = {} if is_primary else deepcopy(profile.get(PERSONA_SETTINGS_KEY) or {})
        effective = self.effective_persona_settings(pid, include_common=False)
        manifest = self._persona_scope_manifest()
        sources = {
            key: (
                "primary"
                if is_primary
                else "persona"
                if key in raw
                else "primary"
            )
            for key, entry in manifest.items()
            if entry.get("scope") == "persona"
        }
        sensitive = {key for key, entry in manifest.items() if entry.get("sensitive")}
        redacted_effective = {
            key: ("***" if key in sensitive and value not in (None, "", [], {}) else value)
            for key, value in effective.items()
        }
        redacted_raw = {
            key: ("***" if key in sensitive and value not in (None, "", [], {}) else value)
            for key, value in raw.items()
            if key in manifest and manifest[key].get("scope") == "persona"
        }
        return {
            "persona_id": pid,
            "is_primary": is_primary,
            "enabled": pid in set(self._configured_multi_persona_ids()),
            "degraded": pid in set(getattr(self, "_persona_profile_errors", {})),
            "degraded_reason": str(getattr(self, "_persona_profile_errors", {}).get(pid, "")),
            "schema_version": int(profile.get(PERSONA_SETTINGS_VERSION_KEY) or PERSONA_SETTINGS_SCHEMA_VERSION),
            "revision": int(profile.get(PERSONA_SETTINGS_REVISION_KEY) or 0),
            "settings": redacted_effective,
            "raw_settings": redacted_raw,
            "sources": sources,
        }

    @story_legacy_sync_operation("persona.mode.transition")
    def _prepare_multi_persona_transition(self, enabled: bool) -> None:
        """Keep the canonical single store authoritative across mode changes."""
        current = bool(getattr(self, "enable_multi_persona_mode", False))
        if current == bool(enabled):
            return
        if enabled:
            primary = self._primary_persona_id()
            if not primary:
                raise PersonaConfigError("开启多人格前必须先补充插件指定人格 ID")
            if not self._astrbot_persona_exists(primary):
                raise PersonaConfigError("插件指定人格在 AstrBot 中不存在，请先重新选择")
            ids = self._configured_multi_persona_ids()
            if primary not in ids:
                ids.insert(0, primary)
            self.multi_persona_ids = ids
        else:
            self._write_data_snapshot_sync(deepcopy(self._data_default))

    async def _create_persona_config_async(
        self,
        persona_id: Any,
        *,
        bot_name: Any,
        mode: Any,
        source_persona_id: Any = "",
        recovery: bool = False,
    ) -> dict[str, Any]:
        pid = self._sanitize_persona_id(persona_id)
        name = _single_line(bot_name, 80)
        primary = self._primary_persona_id()
        if not pid or pid == primary:
            return {"ok": False, "code": "persona_config_target_invalid", "message": "请选择非主人格作为新配置目标"}
        if pid not in set(self._configured_multi_persona_ids()):
            return {"ok": False, "code": "persona_config_target_not_enabled", "message": "请先保存人格拓扑，再为该人格创建配置"}
        if not name:
            return {"ok": False, "code": "persona_bot_name_required", "message": "Bot 名字不能为空"}
        create_mode = str(mode or "follow_primary").strip().lower()
        source_id = self._sanitize_persona_id(source_persona_id)
        if create_mode == "copy":
            if not source_id or source_id == pid:
                return {"ok": False, "code": "persona_config_invalid", "message": "请选择不同的来源人格"}
            if source_id != primary and not self._persona_config_exists(source_id):
                return {"ok": False, "code": "persona_config_invalid", "message": "复制来源尚未创建独立人格配置"}
        await self._flush_scheduled_data_save()
        async with self._data_lock:
            existed = self._secondary_persona_store_exists(pid) or pid in getattr(
                self, "_persona_data_profiles", {}
            )
            profile = deepcopy(self._ensure_persona_profile(pid))
            current_settings = profile.get(PERSONA_SETTINGS_KEY)
            profile_degraded = pid in set(getattr(self, "_persona_profile_errors", {}))
            if existed and isinstance(current_settings, dict) and current_settings and not (recovery and profile_degraded):
                return {"ok": False, "code": "persona_config_exists", "message": "该人格配置已存在，请直接编辑或恢复使用"}
            try:
                if create_mode == "copy":
                    if source_id == primary:
                        settings = copy_from_primary_config(
                            self._primary_persona_config(),
                            bot_name=name,
                            manifest=self._persona_scope_manifest(),
                        )
                    else:
                        source_profile = self._ensure_persona_profile(source_id)
                        settings = create_persona_settings(
                            "copy",
                            bot_name=name,
                            source_settings=source_profile.get(PERSONA_SETTINGS_KEY) or {},
                            manifest=self._persona_scope_manifest(),
                        )
                else:
                    settings = create_persona_settings(
                        create_mode,
                        bot_name=name,
                        primary_config=self._primary_persona_config(),
                        manifest=self._persona_scope_manifest(),
                        normalizer=normalize_setting_value,
                    )
            except PersonaConfigError as exc:
                return {"ok": False, "code": "persona_config_invalid", "message": str(exc)}
            before_config = deepcopy(self._cfg_raw(self.config, "multi_persona_ids", []))
            before_profile = deepcopy(profile)
            next_profile = deepcopy(profile)
            database_path = self._persona_profile_db_path(pid)
            next_profile[PERSONA_SETTINGS_KEY] = settings
            next_profile[PERSONA_SETTINGS_VERSION_KEY] = PERSONA_SETTINGS_SCHEMA_VERSION
            next_profile[PERSONA_SETTINGS_REVISION_KEY] = max(1, int(profile.get(PERSONA_SETTINGS_REVISION_KEY) or 0) + 1)
            ids = self._configured_multi_persona_ids()
            if pid not in ids:
                ids.append(pid)
            try:
                self._save_persona_profile_sync(pid, next_profile)
                _set_into_config(self.config, "multi_persona_ids", ids)
                config_saved = bool(await self._save_config_if_possible())
                if not config_saved:
                    raise RuntimeError("AstrBot 配置未能持久化")
            except Exception as exc:
                _set_into_config(self.config, "multi_persona_ids", before_config)
                try:
                    if existed:
                        self._save_persona_profile_sync(pid, before_profile)
                    else:
                        registry = getattr(self, "_persona_sqlite_store_registry", None)
                        discard = getattr(registry, "discard", None)
                        if callable(discard):
                            discard(database_path)
                        database_path.unlink(missing_ok=True)
                        database_path.with_name(database_path.name + "-wal").unlink(missing_ok=True)
                        database_path.with_name(database_path.name + "-shm").unlink(missing_ok=True)
                except Exception:
                    pass
                return {"ok": False, "code": "persona_config_persistence_failed", "message": f"人格配置保存失败，已回滚: {_single_line(exc, 120)}"}
            live = self._ensure_persona_profile(pid)
            live.clear()
            live.update(next_profile)
            self.multi_persona_ids = ids
            self._reset_persona_prompt_caches(pid)
            return {"ok": True, "created": True, **self._persona_config_state(pid)}

    async def _update_persona_settings_async(
        self,
        persona_id: Any,
        *,
        changes: Any,
        follow_primary_keys: Any,
        expected_revision: Any,
    ) -> dict[str, Any]:
        pid = self._sanitize_persona_id(persona_id)
        primary = self._primary_persona_id()
        if not pid or pid == primary:
            return {"ok": False, "code": "persona_settings_target_invalid", "message": "主人格请使用现有通用配置保存接口"}
        if not self._persona_config_exists(pid):
            return {"ok": False, "code": "persona_config_missing", "message": "该人格尚未创建独立配置"}
        if not isinstance(changes, dict) or not isinstance(follow_primary_keys, list):
            return {"ok": False, "code": "persona_settings_payload_invalid", "message": "人格配置更新格式无效"}
        manifest = self._persona_scope_manifest()
        overlap = set(changes) & {str(key) for key in follow_primary_keys}
        if overlap:
            return {"ok": False, "code": "persona_settings_overlap", "message": "同一配置项不能同时覆盖和恢复跟随"}
        await self._flush_scheduled_data_save()
        changed_keys = sorted(set(changes) | set(map(str, follow_primary_keys)))
        async with self._data_lock:
            profile = self._ensure_persona_profile(pid)
            revision = int(profile.get(PERSONA_SETTINGS_REVISION_KEY) or 0)
            try:
                expected = int(expected_revision)
            except (TypeError, ValueError):
                expected = revision
            if expected != revision:
                return {"ok": False, "status_code": 409, "code": "persona_settings_revision_conflict", "message": "人格配置已被其他页面修改", "revision": revision}
            next_profile = deepcopy(profile)
            raw = deepcopy(next_profile.get(PERSONA_SETTINGS_KEY) or {})
            for key, value in changes.items():
                entry = manifest.get(str(key))
                if not entry or entry.get("scope") != "persona":
                    return {"ok": False, "code": "persona_setting_not_allowed", "message": f"配置项不允许按人格覆盖: {key}"}
                raw[str(key)] = normalize_setting_value(str(key), value, entry)
            for raw_key in follow_primary_keys:
                key = str(raw_key)
                entry = manifest.get(key)
                if not entry or entry.get("scope") != "persona":
                    return {"ok": False, "code": "persona_setting_not_allowed", "message": f"配置项不允许按人格跟随: {key}"}
                if entry.get("identity"):
                    return {"ok": False, "code": "persona_identity_cannot_follow", "message": f"身份配置不能跟随主人格: {key}"}
                raw.pop(key, None)
            if not str(raw.get("bot_name") or "").strip():
                return {"ok": False, "code": "persona_bot_name_required", "message": "Bot 名字不能为空"}
            next_profile[PERSONA_SETTINGS_KEY] = normalize_persona_settings(raw, manifest=manifest, preserve_unknown=True)
            next_profile[PERSONA_SETTINGS_VERSION_KEY] = PERSONA_SETTINGS_SCHEMA_VERSION
            next_profile[PERSONA_SETTINGS_REVISION_KEY] = revision + 1
            if any(
                self._persona_setting_invalidates_runtime_cache(key, manifest)
                for key in changed_keys
            ):
                self._clear_persona_runtime_cache(next_profile)
            try:
                self._save_persona_profile_sync(pid, next_profile)
            except Exception as exc:
                return {"ok": False, "code": "persona_settings_persistence_failed", "message": f"人格配置保存失败: {_single_line(exc, 120)}"}
            profile.clear()
            profile.update(next_profile)
            result = {
                "ok": True,
                "changed": changed_keys,
                **self._persona_config_state(pid),
            }
        self._apply_persona_setting_hot_effects(pid, changed_keys)
        return result

    @staticmethod
    def _persona_setting_invalidates_runtime_cache(
        key: str,
        manifest: dict[str, dict[str, Any]],
    ) -> bool:
        entry = manifest.get(str(key)) or {}
        lowered = str(key).lower()
        return bool(entry.get("identity")) or any(
            marker in lowered
            for marker in (
                "provider",
                "prompt",
                "reference",
                "worldbook",
                "knowledge_source",
                "voice",
                "tts_",
            )
        )

    def _apply_persona_setting_hot_effects(
        self,
        persona_id: str,
        changed_keys: list[str],
    ) -> None:
        """Compatibility adapter to the sole runtime side-effect dispatcher."""
        dispatch_runtime_config_effects(
            self,
            {str(key): None for key in changed_keys},
            scope="persona",
            persona_id=persona_id,
            source="persona",
        )

    def _persona_detach_preview(self, persona_id: Any) -> dict[str, Any]:
        pid = self._sanitize_persona_id(persona_id)
        primary = self._primary_persona_id()
        if not pid or pid == primary:
            return {"ok": False, "code": "persona_detach_target_invalid", "message": "主人格不需要脱离跟随"}
        if not self._persona_config_exists(pid):
            return {"ok": False, "code": "persona_config_missing", "message": "该人格尚未创建独立配置，不能执行脱离"}
        profile = self._ensure_persona_profile(pid)
        revision = int(profile.get(PERSONA_SETTINGS_REVISION_KEY) or 0)
        settings = profile.get(PERSONA_SETTINGS_KEY) or {}
        detached = detach_persona_settings(settings, self._primary_persona_config(), manifest=self._persona_scope_manifest())
        existing = sorted(set(detached) & set(settings))
        missing = sorted(set(detached) - set(settings))
        digest = hashlib.sha256(
            json.dumps({"persona_id": pid, "revision": revision, "settings": detached}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return {
            "ok": True,
            "persona_id": pid,
            "revision": revision,
            "existing_override_keys": existing,
            "existing_override_count": len(existing),
            "follow_primary_keys": missing,
            "follow_primary_count": len(missing),
            "final_settings_count": len(detached),
            # Kept for compatibility with clients built before split statistics.
            "missing_keys": missing,
            "materialized_count": len(detached),
            "preview_hash": digest,
        }

    async def _detach_persona_settings_async(self, persona_id: Any, *, expected_revision: Any, preview_hash: Any) -> dict[str, Any]:
        preview = self._persona_detach_preview(persona_id)
        if not preview.get("ok"):
            return preview
        if str(preview_hash or "") != preview["preview_hash"]:
            return {"ok": False, "status_code": 409, "code": "persona_detach_preview_stale", "message": "脱离预览已过期，请重新预览"}
        return await self._update_persona_settings_async(
            preview["persona_id"],
            changes=detach_persona_settings(
                self._ensure_persona_profile(preview["persona_id"]).get(PERSONA_SETTINGS_KEY) or {},
                self._primary_persona_config(),
                manifest=self._persona_scope_manifest(),
            ),
            follow_primary_keys=[],
            expected_revision=expected_revision,
        )

    async def _mutate_persona_window_binding_async(
        self,
        *,
        action: str,
        window_key: Any,
        persona_id: Any = "",
        previous_window_key: Any = "",
        expected_revision: Any = None,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "status_code": 410,
            "code": "plugin_persona_routing_removed",
            "message": "窗口人格由 AstrBot 管理，旧插件绑定只读保留",
            "routing_authority": "astrbot",
        }

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        initialize_plugin_entrypoint_state(
            self,
            context,
            config,
            extension_api_factory=PrivateCompanionExtensionAPI,
        )
        initialize_plugin_config(self, config)
        initialize_plugin_runtime(self)
        initialize_plugin_post_runtime_state(self, config)
        getattr(self, "_initialize_lab_fixture_adapter", lambda: None)()
        self.req041_observability = Req041Observability()
        self._req041_runtime_boot_ref = f"boot-{id(self)}"

    def _initialize_lab_fixture_adapter(self) -> None:
        try:
            self._lab_fixture_adapter = register_companion_lab_fixture_adapter()
        except Exception as exc:
            self._lab_fixture_adapter = None
            logger.warning(
                "[PrivateCompanion] LAB fixture 门控注册失败，已保持生产路径关闭: %s",
                type(exc).__name__,
            )

    def _lab_fixture_relationship_view(self, event: Any, user: Any) -> Any:
        adapter = getattr(self, "_lab_fixture_adapter", None)
        overlay = getattr(adapter, "overlay_relationship_view", None)
        if not callable(overlay):
            return user
        try:
            return overlay(event, user)
        except Exception as exc:
            logger.warning(
                "[PrivateCompanion] LAB fixture 关系投影失败，已放行原始生产视图: %s",
                type(exc).__name__,
            )
            return user

    async def _pull_body_monitor_candidates(self) -> dict[str, Any]:
        integration = getattr(self, "_body_monitor_integration", None)
        if integration is None:
            return {}
        return await integration.poll()

    def _body_monitor_integration_status_view(self) -> dict[str, Any]:
        integration = getattr(self, "_body_monitor_integration", None)
        if integration is None:
            return {
                "enabled": bool(getattr(self, "enable_body_monitor_integration", False)),
                "state": "initializing",
                "status": "initializing",
            }
        return integration.status_view()

    def _format_body_monitor_health_prompt(self, user: dict[str, Any], *, reason: str = "") -> str:
        integration = getattr(self, "_body_monitor_integration", None)
        if integration is None:
            return ""
        return integration.format_health_prompt(user, reason=reason)

    def plugin_identity_status(self) -> dict[str, Any]:
        return dict(self.plugin_identity)

    def runtime_compatibility_status(self) -> dict[str, Any]:
        return self.runtime_capabilities.to_dict()

    def bot_personal_capability_status(self) -> dict[str, Any]:
        return dict(self.bot_personal_capabilities)

    def _active_unified_person_registry(self) -> UnifiedPersonRegistry:
        """Bind identity operations to the store selected by the current persona context."""
        store = self.data
        registry = getattr(self, "unified_person_registry", None)
        if isinstance(registry, UnifiedPersonRegistry) and registry.is_bound_to(store):
            return registry
        return UnifiedPersonRegistry(store)

    def _unified_persona_domain(self) -> str:
        """Return a stable, opaque identity domain for the active persona."""
        if not bool(getattr(self, "enable_multi_persona_mode", False)):
            return ""
        persona_id = self._sanitize_persona_id(self._effective_plugin_persona_id())
        if not persona_id:
            return ""
        digest = hashlib.sha256(persona_id.encode("utf-8")).hexdigest()[:16]
        return f"persona:{digest}"

    def _unified_persona_scoped_value(self, value: Any, *, limit: int = 120) -> str:
        maximum = max(40, min(160, int(limit or 120)))
        base = _single_line(value, maximum)
        persona_domain = self._unified_persona_domain()
        if not base or not persona_domain:
            return base
        available = maximum - len(persona_domain) - 1
        if len(base) > available:
            base_hash = hashlib.sha256(base.encode("utf-8")).hexdigest()[:12]
            prefix_length = max(1, available - len(base_hash) - 1)
            base = f"{base[:prefix_length]}:{base_hash}"
        return f"{base}:{persona_domain}"

    @staticmethod
    def _unified_wire_group_scope(platform: Any, group_id: Any) -> str:
        """Match Memory's persona-neutral group scope wire contract."""
        platform_name = _single_line(platform, 40).lower()
        group_key = _single_line(group_id, 120)
        if not platform_name or not group_key:
            return ""
        return _single_line(f"group:{platform_name}:{group_key}", 80)

    def _req036_source_event_anchor(self, event: Any) -> str:
        """Build an event-local, content-free anchor when an adapter omits message IDs."""
        cached = _single_line(
            getattr(event, "_private_companion_req036_source_event_anchor", ""),
            80,
        )
        if cached:
            return cached

        raw_reader = getattr(self, "_event_raw_payload", None)
        try:
            raw = raw_reader(event) if callable(raw_reader) else {}
        except Exception:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        message_obj = getattr(event, "message_obj", None)
        metadata: dict[str, str] = {
            "origin": _single_line(getattr(event, "unified_msg_origin", ""), 200),
            "event_type": _single_line(type(event).__qualname__, 120),
            "runtime_event_ref": f"{id(event):x}",
        }
        for key in (
            "post_type",
            "message_type",
            "notice_type",
            "sub_type",
            "time",
            "timestamp",
            "user_id",
            "group_id",
            "self_id",
        ):
            value = _single_line(raw.get(key), 120)
            if value:
                metadata[f"raw_{key}"] = value
        for attr in ("time", "timestamp"):
            value = _single_line(getattr(message_obj, attr, ""), 120)
            if value:
                metadata[f"message_{attr}"] = value
            event_value = _single_line(getattr(event, attr, ""), 120)
            if event_value:
                metadata[f"event_{attr}"] = event_value

        inbound_ts = _single_line(
            getattr(event, "_private_companion_inbound_ts", ""),
            80,
        )
        if not inbound_ts:
            inbound_reader = getattr(self, "_event_inbound_activity_ts", None)
            try:
                inbound_ts = _single_line(
                    inbound_reader(event) if callable(inbound_reader) else _now_ts(),
                    80,
                )
            except Exception:
                inbound_ts = _single_line(_now_ts(), 80)
        metadata["observed_at"] = inbound_ts

        anchor = hashlib.sha256(
            json.dumps(
                metadata,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        try:
            setattr(event, "_private_companion_req036_source_event_anchor", anchor)
        except Exception:
            pass
        return anchor

    def unified_person_contract_status(self) -> dict[str, Any]:
        issues = list(person_contract_self_check())
        return {
            "available": not issues,
            "state": "ready" if not issues else "degraded",
            "degraded": bool(issues),
            "contract_name": PERSON_CONTRACT_NAME,
            "contract_version": PERSON_CONTRACT_VERSION,
            "p3_contract_name": P3_CONTRACT_NAME,
            "p3_contract_version": P3_CONTRACT_VERSION,
            "warnings": issues,
            "registry": self._active_unified_person_registry().status(),
        }

    def _unified_person_registry_status(self) -> dict[str, Any]:
        return self._active_unified_person_registry().status()

    def _unified_person_event_identity(
        self,
        event: Any | None = None,
        *,
        subject_id: str = "",
        subject_namespace: str = "",
    ) -> dict[str, str]:
        sender_id = _single_line(subject_id, 160)
        if not sender_id and event is not None:
            sender_getter = getattr(self, "_event_sender_id", None)
            if callable(sender_getter):
                try:
                    sender_id = _single_line(sender_getter(event), 160)
                except Exception:
                    sender_id = ""
            if not sender_id:
                try:
                    sender_id = _single_line(event.get_sender_id(), 160)
                except Exception:
                    sender_id = ""
        platform = ""
        if event is not None:
            try:
                platform = _single_line(event.get_platform_name(), 80)
            except Exception:
                platform = ""
            if not platform:
                platform = _single_line(str(getattr(event, "unified_msg_origin", "") or "").split(":", 1)[0], 80)
        platform = platform or _single_line(getattr(self, "target_platform", ""), 80) or "unknown"
        self_id = ""
        if event is not None:
            self_getter = getattr(self, "_event_self_id", None)
            if callable(self_getter):
                try:
                    self_id = _single_line(self_getter(event), 160)
                except Exception:
                    self_id = ""
        if not self_id:
            ids = sorted(_single_line(item, 160) for item in self._known_bot_self_ids() if _single_line(item, 160))
            if len(ids) == 1:
                self_id = ids[0]
        if not sender_id or not self_id:
            return {}
        namespace = _single_line(subject_namespace, 160).lower()
        if not namespace:
            namespace = f"{platform}:bot" if sender_id == self_id else f"{platform}:user"
        adapter_instance = _single_line(
            getattr(event, "adapter_instance_id", "") if event is not None else "",
            160,
        ) or f"{platform}:{_single_line(getattr(self, 'target_platform', ''), 80) or platform}"
        return {
            "companion_instance_id": self._unified_persona_scoped_value(PLUGIN_ID),
            "bot_account_id": f"{platform}:{self_id}",
            "adapter_instance_id": adapter_instance,
            "subject_namespace": namespace,
            "platform_subject_id": sender_id,
        }

    def resolve_unified_person_identity(self, identity: dict[str, Any]) -> dict[str, Any]:
        return self._active_unified_person_registry().resolve(identity)

    def create_unified_person(
        self,
        identity: dict[str, Any],
        *,
        profile: dict[str, Any] | None = None,
        operation_id: str = "",
    ) -> dict[str, Any]:
        registry = self._active_unified_person_registry()
        result = registry.create_or_link(
            identity,
            profile=profile,
            operation_id=operation_id,
            actor_id="companion",
        )
        self._req041_emit_identity_dual_write(
            result,
            action="create",
            operation_id=operation_id,
            registry=registry,
        )
        return result

    def _req041_emit_identity_dual_write(
        self,
        result: dict[str, Any],
        *,
        action: str,
        operation_id: str,
        registry: UnifiedPersonRegistry | None = None,
    ) -> dict[str, Any]:
        producer = getattr(self, "req041_dual_write_producer", None)
        if producer is None:
            return {"status": "skipped", "code": "dual_write_not_active"}
        active_registry = registry if isinstance(registry, UnifiedPersonRegistry) else self._active_unified_person_registry()
        try:
            return producer.emit_identity_change(
                registry=active_registry,
                result=result,
                action=action,
                operation_id=operation_id,
            )
        except Exception as exc:
            producer.fail_closed("identity_dual_write_failed")
            migration_status = getattr(self, "req041_migration_status", None)
            if isinstance(migration_status, dict):
                migration_status.update({
                    "state": "paused",
                    "code": "identity_dual_write_failed",
                    "dual_write": "failed",
                })
            logger.warning(
                "[PrivateCompanion] REQ-041 身份双写失败，已暂停新读切换并保留 legacy 写入: %s",
                _single_line(exc, 160),
            )
            return {"status": "failed", "code": "identity_dual_write_failed"}

    def _req041_emit_relationship_snapshot(
        self,
        user: dict[str, Any],
        *,
        reason_code: str,
    ) -> dict[str, Any]:
        producer = getattr(self, "req041_dual_write_producer", None)
        if producer is None:
            return {"status": "skipped", "code": "dual_write_not_active"}
        try:
            try:
                source_revision = max(0, int(user.get("req041_relationship_source_revision") or 0)) + 1
            except (TypeError, ValueError, OverflowError):
                source_revision = 1
            scope = self._unified_persona_domain()
            emitted = producer.emit_relationship_snapshot(
                registry=self._active_unified_person_registry(),
                user=user,
                reason_code=reason_code,
                source_scope=scope or "default",
                source_revision=source_revision,
            )
            if int(emitted.get("source_revision") or 0) > 0:
                user["req041_relationship_source_revision"] = int(emitted["source_revision"])
            return emitted
        except Exception as exc:
            producer.fail_closed("relationship_snapshot_dual_write_failed")
            migration_status = getattr(self, "req041_migration_status", None)
            if isinstance(migration_status, dict):
                migration_status.update({
                    "state": "paused",
                    "code": "relationship_snapshot_dual_write_failed",
                    "dual_write": "failed",
                })
            logger.warning(
                "[PrivateCompanion] REQ-041 关系快照双写失败，已暂停新读切换并保留 legacy 写入: %s",
                _single_line(exc, 160),
            )
            return {"status": "failed", "code": "relationship_snapshot_dual_write_failed"}

    def get_unified_person_projection(self, person_id: str) -> dict[str, Any] | None:
        return self._active_unified_person_registry().read_projection(person_id)

    def _req036_private_gate_for_user(self, user: Any) -> dict[str, Any]:
        return req036_private_companion_gate(user)

    def _req036_migrate_configured_target_capability(self, user_id: Any, user: Any) -> bool:
        """Repair migration-only false gates for configured targets and legacy owners."""
        if not isinstance(user, dict):
            return False
        if bool(user.get("manual_disabled")):
            return False
        canonicalizer = getattr(self, "_canonical_private_user_id", None)
        identity_normalizer = getattr(self, "_normalize_private_identity_id", None)

        def normalized_identity(value: Any) -> str:
            candidate = _single_line(value, 160)
            if callable(identity_normalizer):
                try:
                    candidate = _single_line(identity_normalizer(candidate), 160) or candidate
                except Exception:
                    return ""
            if callable(canonicalizer):
                try:
                    candidate = _single_line(canonicalizer(candidate), 160)
                except Exception:
                    return ""
            return candidate

        configured_targets = getattr(self, "_configured_target_ids", None)
        target_ids: set[str] = set()
        try:
            if callable(configured_targets):
                target_ids = {
                    target_id
                    for target_id in (normalized_identity(target) for target in configured_targets())
                    if target_id
                }
        except Exception:
            return False

        identity_candidates = {
            candidate
            for candidate in (
                normalized_identity(user_id),
                normalized_identity(user.get("user_id")),
                normalized_identity(user.get("identity_subject_id")),
            )
            if candidate
        }
        aliases = user.get("alias_user_ids")
        if isinstance(aliases, list):
            identity_candidates.update(
                candidate
                for candidate in (normalized_identity(alias) for alias in aliases[:32])
                if candidate
            )
        configured_match = bool(target_ids.intersection(identity_candidates))

        # A bare numeric/openid target must not grant the same identifier on a
        # different platform. Adapter instance changes inside the configured
        # platform remain compatible and are handled by the scoped profile.
        if configured_match:
            platform_normalizer = getattr(self, "_normalize_platform_kind", None)
            configured_platform_raw = _single_line(getattr(self, "target_platform", ""), 80).lower()
            observed_platform = _single_line(user.get("identity_platform_kind"), 40).lower()
            configured_platform = ""
            if callable(platform_normalizer) and configured_platform_raw:
                try:
                    configured_platform = _single_line(platform_normalizer(configured_platform_raw), 40).lower()
                except Exception:
                    configured_platform = ""
            if (
                configured_platform
                and configured_platform != "generic"
                and observed_platform
                and observed_platform != "generic"
                and configured_platform != observed_platform
            ):
                configured_match = False
            elif configured_platform == "generic" and configured_platform_raw:
                observed_adapter = _single_line(user.get("identity_adapter_instance_id"), 120).lower()
                if observed_adapter and configured_platform_raw not in {observed_adapter, observed_adapter.split(":", 1)[0]}:
                    configured_match = False

        owner_match = False
        if not configured_match:
            return False

        capabilities = user.get("unified_profile_capabilities")
        if isinstance(capabilities, dict) and capabilities.get("private_companion_enabled") is True:
            return False
        grant_source = (
            _single_line(capabilities.get("grant_source"), 80).lower()
            if isinstance(capabilities, dict)
            else ""
        )
        explicit_sources = {
            "admin",
            "administrator",
            "manual",
            "page_administrator",
            "page_administrator_update",
        }
        if grant_source in explicit_sources or "administrator" in grant_source:
            return False

        # Audit provenance is authoritative even when an older writer failed
        # to keep grant_source synchronized.
        audit = user.get("unified_profile_capability_audit")
        if isinstance(audit, list):
            for entry in reversed(audit[-64:]):
                if not isinstance(entry, dict):
                    continue
                changed = entry.get("changed")
                private_change = changed.get("private_companion_enabled") if isinstance(changed, dict) else None
                if not isinstance(private_change, dict) or "to" not in private_change:
                    continue
                if private_change.get("to") is True:
                    break
                actor = _single_line(entry.get("actor_id"), 80).lower()
                reason = _single_line(entry.get("reason_code"), 80).lower()
                compatibility_change = any(
                    token in f"{actor} {reason}"
                    for token in ("migration", "compatibility", "reconciliation", "startup")
                )
                if private_change.get("to") is False and not compatibility_change:
                    return False
                break

        repairable_sources = {
            "",
            "default_closed",
            "group_observation",
            "legacy_effective_migration",
            "legacy_configured_target_migration",
            "owner_default_enabled",
        }
        if (
            isinstance(capabilities, dict)
            and grant_source not in repairable_sources
            and not bool(user.get("manual_enabled"))
        ):
            return False

        proactive_enabled = bool(
            owner_match
            or (isinstance(capabilities, dict) and capabilities.get("proactive_private_enabled") is True)
            or user.get("proactive_private_enabled") is True
            or _safe_int(user.get("proactive_daily_limit"), 0, 0) > 0
        )
        source = (
            "owner_capability_reconciliation"
            if owner_match and not configured_match
            else "configured_target_capability_reconciliation"
            if isinstance(capabilities, dict)
            else "legacy_configured_target_migration"
        )
        result = req036_update_capabilities(
            user,
            {
                "private_companion_enabled": True,
                "proactive_private_enabled": proactive_enabled,
            },
            actor_authorized=True,
            grant_source=source,
            actor_id="compatibility_migration",
            target_identity=normalized_identity(user.get("identity_subject_id")) or normalized_identity(user_id),
            reason_code=source,
        )
        return bool(result.get("ok"))

    def _req036_capability_summary_for_user(self, user: Any) -> dict[str, Any]:
        bridge = self._memory_companion_bridge()
        portrait_backend_available = callable(getattr(bridge, "read_unified_profile_portrait", None))
        return req036_capability_summary(
            user,
            global_portrait_mode=runtime_persona_setting(self, 'portrait_global_mode', "disabled"),
            portrait_backend_available=portrait_backend_available,
        )

    def _req036_proactive_private_allowed(self, user: Any) -> bool:
        return bool(req036_proactive_private_gate(user).get("allowed"))

    def _req036_update_capabilities(
        self,
        user: dict[str, Any],
        changes: dict[str, Any],
        *,
        actor_id: str = "page_administrator",
        target_identity: str = "",
        reason_code: str = "administrator_update",
    ) -> dict[str, Any]:
        requested_mode = _single_line(changes.get("portrait_mode"), 40).lower() if isinstance(changes, dict) else ""
        if requested_mode and requested_mode not in {"disabled", "off", "follow_global"}:
            bridge = self._memory_companion_bridge()
            if not callable(getattr(bridge, "read_unified_profile_portrait", None)):
                return {
                    "ok": False,
                    "code": "memory_companion_required",
                    "message": "需要安装并启用 MemoryCompanion",
                    "capabilities": self._req036_capability_summary_for_user(user),
                }
        return req036_update_capabilities(
            user,
            changes,
            actor_authorized=True,
            grant_source="administrator",
            actor_id=actor_id,
            target_identity=target_identity,
            reason_code=reason_code,
        )

    def _req039_group_observation_projection(
        self,
        event: Any,
        *,
        sender_id: str,
        sender_name: str = "",
    ) -> dict[str, Any] | None:
        """Build a transient group-speaker projection without creating a DM user."""
        raw_sender_id = _single_line(sender_id, 160)
        normalizer = getattr(self, "_normalize_private_identity_id", None)
        normalized_sender_id = normalizer(raw_sender_id) if callable(normalizer) else raw_sender_id
        normalized_sender_id = normalized_sender_id or raw_sender_id
        self_getter = getattr(self, "_event_self_id", None)
        try:
            self_id = _single_line(self_getter(event), 160) if callable(self_getter) else ""
        except Exception:
            self_id = ""
        if not normalized_sender_id or normalized_sender_id == self_id or raw_sender_id == self_id:
            return None
        canonical = _single_line(self._canonical_private_user_id(normalized_sender_id), 160)
        bot_checker = getattr(self, "_is_bot_self_user_id", None)
        if not canonical or (callable(bot_checker) and bot_checker(canonical)):
            return None
        display_name = _single_line(sender_name, 80) or canonical
        profiles = self.data.get("worldbook_member_profiles") if isinstance(getattr(self, "data", None), dict) else {}
        observation = profiles.get(normalized_sender_id) if isinstance(profiles, dict) else None
        if isinstance(observation, dict) and bool(observation.get("observation_only")):
            display_name = _single_line(observation.get("name"), 80) or display_name
        projection: dict[str, Any] = {
            "user_id": canonical,
            "nickname": display_name,
            "enabled": False,
            "manual_enabled": False,
            "manual_disabled": False,
            "relationship_role": "friend",
            "relationship_mode": "normal",
            "relationship_score": 0,
            "current_interaction": {},
            "profile_origin": "group_observation",
            "projection_kind": "group_observation",
            "observation_only": True,
            "private_companion_enabled": False,
            "proactive_private_enabled": False,
        }
        identity_context_getter = getattr(self, "_private_event_identity_context", None)
        if callable(identity_context_getter):
            try:
                identity_context = identity_context_getter(event, normalized_sender_id)
            except Exception:
                identity_context = {}
            if isinstance(identity_context, dict):
                projection["identity_subject_id"] = _single_line(identity_context.get("subject"), 128)
                projection["identity_platform_kind"] = _single_line(identity_context.get("platform"), 40)
                projection["identity_adapter_instance_id"] = _single_line(identity_context.get("adapter"), 120)
                projection["identity_bot_id"] = _single_line(identity_context.get("bot_id"), 120)
        req036_ensure_new_profile_capabilities(projection)
        return projection

    def _req036_attach_unified_profile_context(
        self,
        event: Any,
        *,
        user: dict[str, Any] | None = None,
        group_id: str = "",
        source: str = "observation",
    ) -> dict[str, Any]:
        """Attach the smallest exact-person context for Memory's read-only use."""
        identity = self._unified_person_event_identity(event)
        if not identity:
            return {"state": "identity_pending", "code": "identity_pending"}
        resolution = self.resolve_unified_person_identity(identity)
        if resolution.get("state") != "resolved":
            profile = user if isinstance(user, dict) else {}
            created = self.create_unified_person_for_event(
                event,
                operation_id=f"req036.{source}:{str(resolution.get('identity_key') or '')[-24:]}",
                profile={
                    "display_name": (
                        _single_line(profile.get("nickname"), 80) if not group_id else ""
                    ),
                    "preferred_address": (
                        _single_line(profile.get("nickname"), 40) if not group_id else ""
                    ),
                    "style": _single_line(profile.get("style"), 40) if not group_id else "",
                    "profile_origin": _single_line(profile.get("profile_origin"), 60),
                    "auto_profile_created": bool(profile.get("auto_profile_created", False)),
                    "affinity_score": _safe_int(profile.get("relationship_score"), 0, -1200, 1200),
                    "owner_mode": "owner" if _single_line(profile.get("relationship_role"), 40) == "owner" else "not_owner",
                    "relation_policy_id": _single_line(profile.get("relationship_mode"), 40) or "default_friend",
                },
            )
            if created.get("state") != "resolved":
                return {"state": "identity_pending", "code": str(created.get("code") or "identity_pending")}
            resolution = self.resolve_unified_person_identity(identity)
        projection = resolution.get("projection") if isinstance(resolution.get("projection"), dict) else None
        if not isinstance(projection, dict):
            return {"state": "identity_pending", "code": "projection_missing"}
        person_id = _single_line(projection.get("person_id"), 80)
        if isinstance(user, dict) and person_id:
            user["unified_person_id"] = person_id
            user["unified_profile_projection_revision"] = int(projection.get("projection_revision") or 1)
            if not group_id:
                private_facts = {
                    "style": _single_line(user.get("style"), 40),
                    "profile_origin": _single_line(user.get("profile_origin"), 60),
                    "auto_profile_created": bool(user.get("auto_profile_created", False)),
                }
                private_name = _single_line(user.get("nickname"), 80)
                if private_name:
                    private_facts["display_name"] = private_name
                    private_facts["preferred_address"] = private_name[:40]
                fact_signature = hashlib.sha256(
                    json.dumps(private_facts, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest()[:32]
                self._req041_update_unified_profile_facts(
                    user,
                    private_facts,
                    operation_id=f"req041-private-profile-observation-{person_id[-16:]}-{fact_signature}",
                    actor_id="private_observation",
                    schedule_save=True,
                )
        group_scope = ""
        if group_id and person_id:
            platform = _single_line(identity.get("subject_namespace"), 80).split(":", 1)[0]
            group_scope = self._unified_wire_group_scope(platform, group_id)
            if group_scope:
                self._active_unified_person_registry().upsert_group_overlay(
                    person_id,
                    group_scope,
                    {
                        "alias": _single_line((user or {}).get("nickname"), 80),
                        "source": "group_observation",
                        "public": True,
                    },
                    operation_id=f"req036.overlay:{person_id[-12:]}:{_single_line(group_id, 40)}",
                    actor_id="companion",
                )
        if person_id:
            event_id = _single_line(self._event_message_id(event), 120)
            event_anchor = event_id or self._req036_source_event_anchor(event)
            source_fingerprint = hashlib.sha256(
                f"req036:{source}:{group_scope or 'private'}:{event_anchor}".encode("utf-8", errors="ignore")
            ).hexdigest()
            self._active_unified_person_registry().record_identity_source_event(
                person_id,
                _single_line(projection.get("resolved_identity_key"), 160),
                group_scope or "private",
                source_fingerprint,
                operation_id=f"req036.source:{source}:{source_fingerprint[:24]}",
            )
        portrait_namespace_getter = getattr(self, "_req041_scoped_context_for_user", None)
        if callable(portrait_namespace_getter) and isinstance(user, dict):
            try:
                portrait_namespace = portrait_namespace_getter(
                    user,
                    kind="group_member" if group_id else "private",
                    group_id=group_id,
                    purpose="profile_read",
                )
            except Exception:
                portrait_namespace = None
            if isinstance(portrait_namespace, NamespaceContext) and not portrait_namespace.errors():
                try:
                    setattr(
                        event,
                        "private_companion_namespace_context",
                        portrait_namespace.to_dict(),
                    )
                except Exception:
                    pass
        dto = req036_build_profile_dto(
            person_ref=req036_build_person_ref(projection),
            identity_summary={"display_name": _single_line((user or {}).get("nickname"), 80)},
            expression_summary={
                "relationship_score": _safe_int((user or {}).get("relationship_score"), 0, -1200, 1200),
                "relationship_role": _single_line((user or {}).get("relationship_role"), 40) or "friend",
            },
            capability_summary=self._req036_capability_summary_for_user(user),
            context_overlays={"group_scope": group_scope} if group_scope else {},
            bridge_status={"state": "ready", "source": "companion"},
        )
        errors = req036_validate_profile_dto(dto)
        if errors:
            return {"state": "degraded", "code": "bridge_contract_mismatch", "errors": errors}
        try:
            setattr(event, "private_companion_unified_profile_context", dto)
        except Exception:
            return {"state": "degraded", "code": "bridge_unavailable"}
        return {"state": "profile_exact", "code": "profile_exact", "dto": dto, "person_id": person_id}

    async def _req036_reject_unauthorized_private_event(self, event: Any, gate: dict[str, Any]) -> None:
        """Reply before any LLM, bridge, tool, portrait, or relationship path."""
        inbound_checker = getattr(self, "_event_is_inbound_chat_message", None)
        if callable(inbound_checker) and not inbound_checker(event):
            return
        if bool(getattr(event, "private_companion_req036_denied", False)):
            try:
                event.stop_event()
            except Exception:
                pass
            return
        try:
            setattr(event, "private_companion_req036_denied", True)
            setattr(event, "private_companion_req036_denial_code", str(gate.get("code") or "private_companion_disabled"))
        except Exception:
            pass
        try:
            event.stop_event()
        except Exception:
            pass

        # Adapter redelivery can reconstruct the same genuine message as a
        # different event object.  Deduplicate only by its stable platform
        # message identity; this is not a time-based user rate limit.
        message_id = ""
        message_id_getter = getattr(self, "_event_message_id", None)
        if callable(message_id_getter):
            try:
                message_id = _single_line(message_id_getter(event), 120)
            except Exception:
                message_id = ""
        denial_cache_key = ""
        denial_cache: dict[str, float] | None = None
        denial_cache_stamp = time.monotonic()
        if message_id:
            try:
                sender_id = _single_line(event.get_sender_id(), 120)
            except Exception:
                sender_id = ""
            platform_getter = getattr(self, "_platform_kind_for_event", None)
            try:
                platform = _single_line(platform_getter(event), 80) if callable(platform_getter) else ""
            except Exception:
                platform = ""
            scope_getter = getattr(self, "_event_req036_scope", None)
            try:
                denial_scope = _single_line(scope_getter(event), 480) if callable(scope_getter) else ""
            except Exception:
                denial_scope = ""
            denial_scope = denial_scope or f"{platform or 'unknown'}:{sender_id or 'unknown'}"
            denial_cache_key = f"{denial_scope}:{message_id}"
            denial_cache = getattr(self, "_req036_recent_denial_message_ids", None)
            if not isinstance(denial_cache, dict):
                denial_cache = {}
                self._req036_recent_denial_message_ids = denial_cache
            for cache_key, cached_at in list(denial_cache.items()):
                age = denial_cache_stamp - _safe_float(cached_at, 0.0)
                if age < 0 or age > 180.0:
                    denial_cache.pop(cache_key, None)
            if denial_cache_key in denial_cache:
                logger.debug(
                    "[PrivateCompanion] 已忽略重复的未授权私聊拒绝: sender=%s message_id=%s",
                    sender_id or "-",
                    message_id,
                )
                return
            denial_cache[denial_cache_key] = denial_cache_stamp
        reply_text = str(gate.get("reply") or DEFAULT_UNAUTHORIZED_PRIVATE_REPLY)
        echo_entry = None
        echo_remember = getattr(self, "_remember_req036_denial_echo", None)
        if callable(echo_remember):
            try:
                echo_entry = echo_remember(event, reply_text)
            except Exception:
                echo_entry = None

        def release_reply_reservations() -> None:
            if denial_cache is not None and denial_cache_key and denial_cache.get(denial_cache_key) == denial_cache_stamp:
                denial_cache.pop(denial_cache_key, None)
            echo_forget = getattr(self, "_forget_req036_denial_echo", None)
            if callable(echo_forget) and echo_entry is not None:
                try:
                    echo_forget(echo_entry)
                except Exception:
                    pass

        try:
            reply_result = await self._reply(event, reply_text)
        except Exception:
            release_reply_reservations()
            raise
        if reply_result is False:
            release_reply_reservations()
            logger.debug("[PrivateCompanion] 未授权私聊拒绝未发送，已释放回流与消息去重占位")
            return
        echo_confirm = getattr(self, "_confirm_req036_denial_echo", None)
        if callable(echo_confirm) and echo_entry is not None:
            try:
                echo_confirm(echo_entry)
            except Exception:
                pass

    @staticmethod
    def _req036_group_portrait_query_kind(text: Any) -> str:
        value = _single_line(text, 240)
        # A preference phrase followed by advice or a conclusion is ordinary
        # group chatter, not a request to summarize anyone's profile.
        ordinary_statement_patterns = (
            r"(?:^|[\s，,：:@])(?:自己|按自己|个人|各自)\s*(?:喜欢|爱)(?:吃|喝|玩|看|听)?什么\s*(?:就|便|吧|呀|喵|都|随便)",
            r"(?:喜欢|爱)(?:吃|喝|玩|看|听)?什么\s*(?:就|便|吧|呀|喵|都|随便)",
        )
        if any(re.search(pattern, value) for pattern in ordinary_statement_patterns):
            return ""
        probe_patterns = (
            r"喜欢(?:吃|喝|玩|看|听)?什么",
            r"爱(?:吃|喝|玩|看|听)什么",
            r"(?:爱好|兴趣|偏好|习惯|口味|画像)(?:是|有|包括)?(?:什么|啥|哪些|怎么样)",
            r"(?:什么|啥|哪些|有啥|有哪些).{0,8}(?:爱好|兴趣|偏好|习惯|口味)",
            r"(?:说说|看看|查查|总结|整理).{0,12}(?:爱好|兴趣|偏好|习惯|口味|画像)",
        )
        if not any(re.search(pattern, value) for pattern in probe_patterns):
            return ""
        self_subject = r"(?:我自己|我的|我|本人自己|本人的|本人|俺自己|俺的|俺|咱自己|咱的|咱)"
        self_predicate = (
            r"(?:平时|一般|通常|到底|最)?(?:"
            r"喜欢(?:吃|喝|玩|看|听)?什么|爱(?:吃|喝|玩|看|听)什么|"
            r"(?:有|有什么|有啥|有哪些).{0,8}(?:爱好|兴趣|偏好|习惯)|"
            r"(?:的)?(?:爱好|兴趣|偏好|习惯|口味|画像)(?:是|有|包括)?(?:什么|啥|哪些|怎么样)"
            r")"
        )
        direct_self_query = rf"{self_subject}\s*{self_predicate}"
        reflective_self_query = (
            rf"(?:^|[\s，,：:@])我\s*(?:想知道|想问|想看看|想了解)\s*"
            rf"(?:一下)?\s*(?:自己|我自己|我的)\s*{self_predicate}"
        )
        if re.search(direct_self_query, value) or re.search(reflective_self_query, value):
            return "self"
        bot_subject = r"(?:你自己|你的|你)"
        direct_bot_query = rf"{bot_subject}\s*{self_predicate}"
        reflective_bot_query = (
            rf"(?:^|[\s，,：:@])我\s*(?:想知道|想问|想看看|想了解)\s*"
            rf"(?:一下)?\s*(?:你自己|你的|你)\s*{self_predicate}"
        )
        if re.search(direct_bot_query, value) or re.search(reflective_bot_query, value):
            return "bot_self"
        # An omitted subject is ambiguous in natural group speech. Let the
        # normal reply chain decide whether the user means the Bot, instead of
        # treating a prompt such as "喜欢什么发型" as a third-party probe.
        subjectless_query = (
            r"^(?:@[^\s]+\s*)?(?:(?:你觉得|你认为|请问|我想(?:知道|问|看看|了解))(?:一下)?\s*)?"
            r"(?:喜欢|爱)(?:吃|喝|玩|看|听)?什么"
            r"|^(?:@[^\s]+\s*)?(?:(?:你觉得|你认为|请问|我想(?:知道|问|看看|了解))(?:一下)?\s*)?"
            r"(?:什么|啥|哪些).{0,8}(?:爱好|兴趣|偏好|习惯|口味|画像)"
        )
        if re.search(subjectless_query, value):
            return ""
        return "third_party"

    def _req036_group_portrait_query_is_directed(self, event: Any) -> bool:
        """Use adapter addressing metadata so ordinary group chatter never triggers this guard."""
        if bool(getattr(event, "is_at_or_wake_command", False)) or bool(getattr(event, "is_wake", False)):
            return True
        try:
            signals = self._event_scene_signals(event)
        except Exception:
            signals = {}
        if not isinstance(signals, dict):
            return False
        if any(
            isinstance(item, dict) and bool(item.get("is_bot"))
            for item in (signals.get("at_targets") or [])
        ):
            return True
        self_id = _single_line(signals.get("self_id"), 80)
        return bool(self_id and _single_line(signals.get("reply_to_id"), 80) == self_id)

    async def _req036_read_group_self_portrait(self, event: Any) -> str:
        dto = getattr(event, "private_companion_unified_profile_context", None)
        if not isinstance(dto, dict):
            return "这部分画像暂时不可用。"
        capabilities = dto.get("capability_summary")
        if not isinstance(capabilities, dict) or capabilities.get("portrait_usage_enabled") is not True:
            return "智能画像当前未开启。"
        person_ref = dto.get("person_ref") if isinstance(dto.get("person_ref"), dict) else {}
        person_id = _single_line(person_ref.get("person_id"), 80)
        overlays = dto.get("context_overlays") if isinstance(dto.get("context_overlays"), dict) else {}
        scope = _single_line(overlays.get("group_scope"), 80)
        if not scope.startswith("group:"):
            return "这部分画像暂时不可用。"
        request = req036_build_portrait_request(
            person_ref=person_ref,
            requester_person_id=person_id,
            target_person_id=person_id,
            scope=scope,
            purpose="summarize_to_subject",
        )
        namespace_context = getattr(event, "private_companion_namespace_context", None)
        if isinstance(namespace_context, dict):
            request["namespace_context"] = dict(namespace_context)
        bridge = self._memory_companion_bridge()
        reader = getattr(bridge, "read_unified_profile_portrait", None) if bridge is not None else None
        if not callable(reader):
            return "这部分画像暂时不可用。"
        try:
            result = reader(request, limit=5)
            if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                result = await result
        except Exception:
            return "这部分画像暂时不可用。"
        if not isinstance(result, dict) or not result.get("ok"):
            return "这部分画像暂时不可用。"
        summaries = [
            _single_line(item.get("summary"), 80)
            for item in result.get("items", [])
            if isinstance(item, dict) and _single_line(item.get("summary"), 80)
        ]
        return "我目前只记得这些公开的低敏偏好：" + "；".join(summaries[:5]) if summaries else "我还没有整理出可公开的低敏画像。"

    async def _req036_portrait_bridge_status_for_user(self, user: Any) -> dict[str, Any]:
        """Read synchronization state only; facts stay in Memory's admin UI."""
        source = user if isinstance(user, dict) else {}
        person_id = _single_line(source.get("unified_person_id"), 80)
        if not person_id:
            return {"available": False, "code": "identity_pending", "last_synced_at": "", "portrait_revision": 0}
        bridge = self._memory_companion_bridge()
        reader = getattr(bridge, "unified_profile_portrait_status", None) if bridge is not None else None
        if not callable(reader):
            return {"available": False, "code": "bridge_unavailable", "last_synced_at": "", "portrait_revision": 0}
        try:
            result = reader(person_id)
            if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                result = await result
        except Exception:
            return {"available": False, "code": "bridge_degraded", "last_synced_at": "", "portrait_revision": 0}
        if not isinstance(result, dict):
            return {"available": False, "code": "bridge_degraded", "last_synced_at": "", "portrait_revision": 0}
        response = {
            "available": bool(result.get("ok")),
            "code": _single_line(result.get("code"), 80) or "bridge_degraded",
            "last_synced_at": _single_line(result.get("last_synced_at"), 80),
            "portrait_revision": _safe_int(result.get("portrait_revision"), 0, 0),
        }
        projection = self.get_unified_person_projection(person_id)
        portrait_reader = getattr(bridge, "read_unified_profile_portrait", None) if bridge is not None else None
        if not response["available"] or not isinstance(projection, dict) or not callable(portrait_reader):
            return response
        try:
            request = req036_build_portrait_request(
                person_ref=req036_build_person_ref(projection),
                requester_person_id=person_id,
                target_person_id=person_id,
                scope="private",
                purpose="summarize_to_subject",
            )
            namespace_getter = getattr(self, "_req041_scoped_context_for_user", None)
            if callable(namespace_getter):
                namespace_context = namespace_getter(
                    source, kind="private", purpose="profile_read"
                )
                if isinstance(namespace_context, NamespaceContext) and not namespace_context.errors():
                    request["namespace_context"] = namespace_context.to_dict()
            portrait = portrait_reader(request, limit=3)
            if asyncio.iscoroutine(portrait) or hasattr(portrait, "__await__"):
                portrait = await portrait
            response["summaries"] = [
                _single_line(item.get("summary"), 80)
                for item in (portrait.get("items", []) if isinstance(portrait, dict) else [])
                if isinstance(item, dict) and _single_line(item.get("summary"), 80)
            ][:3]
        except Exception:
            response["summaries"] = []
        return response

    async def _req036_preferred_address_from_portrait(self, user: Any) -> str:
        """Resolve this exact private subject's latest explicit address hint."""
        source = user if isinstance(user, dict) else {}
        capabilities = self._req036_capability_summary_for_user(source)
        if capabilities.get("portrait_usage_enabled") is not True:
            return ""
        person_id = _single_line(source.get("unified_person_id"), 80)
        if not person_id:
            return ""
        projection = self.get_unified_person_projection(person_id)
        if not isinstance(projection, dict):
            return ""
        bridge = self._memory_companion_bridge()
        reader = (
            getattr(bridge, "read_unified_profile_portrait", None)
            if bridge is not None
            else None
        )
        if not callable(reader):
            return ""
        request = req036_build_portrait_request(
            person_ref=req036_build_person_ref(projection),
            requester_person_id=person_id,
            target_person_id=person_id,
            scope="private",
            purpose="summarize_to_subject",
        )
        namespace_getter = getattr(self, "_req041_scoped_context_for_user", None)
        if callable(namespace_getter):
            namespace_context = namespace_getter(
                source, kind="private", purpose="profile_read"
            )
            if (
                isinstance(namespace_context, NamespaceContext)
                and not namespace_context.errors()
            ):
                request["namespace_context"] = namespace_context.to_dict()
        result = reader(request, limit=8)
        if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
            result = await result
        if not isinstance(result, dict) or not result.get("ok"):
            return ""
        for item in result.get("items", []):
            if not isinstance(item, dict):
                continue
            if _single_line(item.get("dimension"), 80) != "preferred_address":
                continue
            summary = _single_line(item.get("summary"), 180)
            match = re.fullmatch(r"希望被称为\s+(.+)", summary)
            preferred = _single_line(match.group(1) if match else "", 24)
            if preferred:
                return preferred
        return ""

    def read_p4_effect_state(self, person_id: str) -> dict[str, Any]:
        return self._active_unified_person_registry().read_p4_effect_state(person_id)

    def read_p4_live_state(self, person_id: str) -> dict[str, Any]:
        return self._active_unified_person_registry().read_p4_live_state(person_id)

    def _p4_b_apply_legacy_relationship_delta(
        self,
        user: dict[str, Any],
        delta: int,
        *,
        reason_code: str = "",
    ) -> bool:
        del reason_code
        return apply_legacy_relationship_delta(
            user,
            delta,
            isolate=bool(getattr(self, "enable_p4_b_legacy_score_isolation", False)),
        )

    def _p4_live_state_for_event(self, event: Any) -> dict[str, Any] | None:
        try:
            if not bool(event.is_private_chat()):
                return None
        except Exception:
            return None
        resolution = self.resolve_unified_person_for_event(event)
        if resolution.get("state") != "resolved":
            return None
        person_id = _single_line(resolution.get("person_id"), 160)
        if not person_id:
            return None
        result = self.read_p4_live_state(person_id)
        if result.get("ok") is not True:
            return {"_p4_live_invalid": True}
        return result.get("state")

    def _bounded_p4_reply_temperature_signals(self, event: Any) -> dict[str, Any]:
        """Return transient, bounded advisory inputs for the P4 reply projection."""
        data = getattr(self, "data", None)
        daily_state = data.get("daily_state") if isinstance(data, dict) else None
        energy = daily_state.get("energy") if isinstance(daily_state, dict) else None
        if (
            isinstance(energy, bool)
            or not isinstance(energy, (int, float))
            or not math.isfinite(float(energy))
        ):
            energy = None
        else:
            energy = max(0, min(100, energy))
        mood = ""
        if isinstance(daily_state, dict):
            mood = _single_line(daily_state.get("mood_bias") or daily_state.get("mood"), 64)

        schedule_parts: list[str] = []
        segment_getter = getattr(self, "_current_detail_segment_for_update", None)
        if callable(segment_getter):
            try:
                segment = segment_getter()
            except Exception:
                segment = None
            if isinstance(segment, dict):
                schedule_parts.extend(
                    _single_line(segment.get(key), 80)
                    for key in ("title", "name", "summary", "activity", "location")
                    if _single_line(segment.get(key), 80)
                )
        if not schedule_parts and isinstance(data, dict):
            try:
                current_item = self._get_current_plan_item(data.get("daily_plan", {}))
            except Exception:
                current_item = None
            if isinstance(current_item, dict):
                schedule_parts.extend(
                    _single_line(current_item.get(key), 80)
                    for key in ("title", "name", "summary", "activity", "location")
                    if _single_line(current_item.get(key), 80)
                )

        return {
            "energy": energy,
            "mood": mood or None,
            "schedule": " ".join(schedule_parts)[:240] or None,
            "context": _single_line(getattr(event, "message_str", ""), 280) or None,
        }

    def record_p4_effect_event(
        self,
        person_id: str,
        event: dict[str, Any],
        *,
        operation_id: str,
        actor_id: str = "system",
    ) -> dict[str, Any]:
        result = self._active_unified_person_registry().record_p4_effect_event(
            person_id,
            event,
            operation_id=operation_id,
            actor_id=actor_id,
        )
        if result.get("ok") and result.get("changed"):
            saver = getattr(self, "_schedule_data_save", None)
            if callable(saver):
                saver(sections={"unified_person"})
        return result

    def resolve_unified_person_for_event(self, event: Any | None = None) -> dict[str, Any]:
        identity = self._unified_person_event_identity(event)
        if not identity:
            return {"state": "pending", "identity_key": "", "person_id": "", "errors": ["event_identity_missing"]}
        return self.resolve_unified_person_identity(identity)

    def create_unified_person_for_event(
        self,
        event: Any | None = None,
        *,
        operation_id: str = "",
        profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        identity = self._unified_person_event_identity(event)
        if not identity:
            return {"ok": False, "state": "pending", "code": "event_identity_missing", "person_id": ""}
        try:
            identity_key = build_identity_key(identity)
        except (TypeError, ValueError):
            return {"ok": False, "state": "invalid", "code": "identity_invalid", "person_id": ""}
        user_profile = dict(profile) if isinstance(profile, dict) else {}
        if event is not None and not user_profile.get("display_name"):
            name_getter = getattr(self, "_sender_display_name", None)
            if callable(name_getter):
                try:
                    user_profile["display_name"] = _single_line(name_getter(event), 80)
                except Exception:
                    pass
        return self.create_unified_person(
            identity,
            profile=user_profile,
            operation_id=operation_id or f"companion.person.create:{identity_key[-24:]}",
        )

    def build_unified_person_context(self, event: Any | None = None) -> dict[str, Any]:
        identity = self._unified_person_event_identity(event)
        resolution = self.resolve_unified_person_identity(identity) if identity else {
            "state": "pending", "identity_key": "", "person_id": "", "errors": ["event_identity_missing"],
        }
        state = str(resolution.get("state") or "pending")
        projection = resolution.get("projection") if isinstance(resolution.get("projection"), dict) else None
        scope = "unknown"
        if event is not None:
            try:
                scope = "private" if bool(event.is_private_chat()) else "group"
            except Exception:
                scope = "unknown"
        platform = str(identity.get("subject_namespace") or "").split(":", 1)[0] if identity else ""
        group_id = ""
        if scope == "group":
            group_getter = getattr(self, "_extract_group_id_from_event", None)
            if callable(group_getter):
                try:
                    group_id = _single_line(group_getter(event), 160)
                except Exception:
                    group_id = ""
        group_scope = self._unified_wire_group_scope(platform, group_id)
        group_overlay = None
        if state == "resolved" and group_scope and resolution.get("person_id"):
            group_overlay = self._active_unified_person_registry().read_group_overlay(
                str(resolution.get("person_id") or ""), group_scope
            )
        person_payload = {
            key: projection.get(key)
            for key in (
                "person_id", "identity_assurance", "profile_status", "relation_policy_id",
                "relation_label", "owner_mode", "affinity_band", "projection_revision",
                "group_overlay_ref",
            )
            if projection is not None and projection.get(key) not in (None, "", [], {})
        }
        p3 = build_context(
            persona={"companion_instance_id": self._unified_persona_scoped_value(PLUGIN_ID)},
            runtime={"platform": platform, "scope": scope, "adapter_instance_id": identity.get("adapter_instance_id", "")},
            person=person_payload,
            scene={
                "scope": scope,
                "group_scope": group_scope,
                "group_id_present": bool(group_id),
                "group_overlay_revision": group_overlay.get("revision") if isinstance(group_overlay, dict) else 0,
            },
            bridge_available=True,
        )
        if state != "resolved":
            p3["state"] = state if state in {"pending", "invalid", "degraded", "legacy_local"} else "degraded"
            p3["warnings"] = list(p3.get("warnings") or []) + list(resolution.get("errors") or [f"person_{state}"])[:8]
            person_slot = p3.get("slots", {}).get("person")
            if isinstance(person_slot, dict):
                person_slot["state"] = p3["state"]
        p3 = project_context(p3)
        p4 = build_p4_shadow(
            source_kind="companion",
            target_kind="memory_bridge",
            authority="companion",
            reason_code="projection_ready" if state == "resolved" else f"person_{state}",
            safe_reference=str(resolution.get("person_id") or ""),
            operation_id=f"person.context:{str(resolution.get('identity_key') or 'pending')[-24:]}",
            status="shadow" if state == "resolved" else "degraded",
        )
        return {
            "contract_name": PERSON_CONTRACT_NAME,
            "contract_version": PERSON_CONTRACT_VERSION,
            "p3_contract_name": P3_CONTRACT_NAME,
            "p3_contract_version": P3_CONTRACT_VERSION,
            "state": state,
            "identity": {"identity_key": str(resolution.get("identity_key") or ""), "person_id": str(resolution.get("person_id") or "")},
            "projection": projection,
            "p3": p3,
            "p4_shadow": p4,
            "scope": scope,
            "group_scope": group_scope,
        }

    def _companion_owned_sqlite_path(self, db_path: Any) -> Path | None:
        """Resolve an existing SQLite file only when Companion owns its path."""
        data_dir = str(getattr(self, "data_dir", "") or "").strip()
        if not data_dir or db_path in (None, ""):
            return None
        try:
            data_root = Path(data_dir).resolve(strict=True)
            resolved = Path(str(db_path)).resolve(strict=True)
            resolved.relative_to(data_root)
        except (OSError, RuntimeError, ValueError):
            return None
        if not data_root.is_dir() or not resolved.is_file():
            return None
        return resolved

    def _sqlite_wal_candidate_paths(self) -> list[Path]:
        # AstrBot 4.27.x configures its own database and knowledge-base engines.
        # Only explicitly registered files below this plugin's data directory
        # may be tuned; shared AstrBot or sibling-plugin databases stay untouched.
        candidates: list[Any] = []
        effective_store_text = str(
            getattr(self, "storage_sqlite_effective_path", "") or ""
        ).strip()
        if effective_store_text:
            candidates.append(effective_store_text)
        if str(getattr(self, "storage_backend", "json") or "json").lower() == "sqlite":
            store_manager = getattr(self, "store_manager", None)
            candidates.append(
                getattr(getattr(store_manager, "backend", None), "db_path", None)
            )
        profiles_dir_text = str(getattr(self, "_persona_profiles_dir", "") or "").strip()
        profiles_dir = Path(profiles_dir_text) if profiles_dir_text else None
        if profiles_dir is not None and profiles_dir.is_dir():
            candidates.extend(sorted(profiles_dir.glob("*.db")))
        for owner_name in (
            "req041_migration_coordinator",
            "req041_migration_outbox",
            "req041_relationship_store",
        ):
            candidates.append(getattr(getattr(self, owner_name, None), "path", None))

        seen: set[str] = set()
        paths: list[Path] = []
        for candidate in candidates:
            path = self._companion_owned_sqlite_path(candidate)
            if path is None:
                continue
            canonical = str(path)
            if canonical in seen:
                continue
            seen.add(canonical)
            paths.append(path)
        return paths

    def _apply_sqlite_wal_to_file(self, db_path: Path) -> str:
        owned_path = self._companion_owned_sqlite_path(db_path)
        if owned_path is None:
            raise ValueError("sqlite_path_not_companion_owned")
        conn = sqlite3.connect(str(owned_path), timeout=15.0)
        try:
            conn.execute("PRAGMA busy_timeout=15000")
            mode_row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA wal_autocheckpoint=1000")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.commit()
            return str(mode_row[0] if mode_row else "")
        finally:
            conn.close()

    async def _apply_sqlite_wal_optimizations(self) -> None:
        applied: list[str] = []
        failed: list[str] = []
        for path in self._sqlite_wal_candidate_paths():
            try:
                mode = await asyncio.to_thread(self._apply_sqlite_wal_to_file, path)
                applied.append(f"{path.name}:{mode or 'unknown'}")
            except Exception as exc:
                failed.append(f"{path.name}:{_single_line(exc, 80)}")
        if applied:
            logger.info(
                "[PrivateCompanion] 插件自有 SQLite WAL 优化已应用: files=%s",
                "，".join(applied),
            )
        if failed:
            logger.warning("[PrivateCompanion] SQLite WAL 并发优化部分失败: %s", "；".join(failed))

    def _repair_private_companion_handler_bindings(self) -> None:
        """热更新后强制把残留 handler 重新绑定到当前插件实例。"""
        try:
            module_path = str(getattr(type(self), "__module__", "") or "")
            package_prefix = module_path.rsplit(".", 1)[0] if "." in module_path else module_path
            if not package_prefix:
                return
            repaired = 0
            for handler in list(star_handlers_registry):
                handler_module_path = str(getattr(handler, "handler_module_path", "") or "")
                if not is_module_path_for_package(handler_module_path, package_prefix):
                    continue
                handler_name = str(getattr(handler, "handler_name", "") or "")
                if not handler_name:
                    continue
                current_func = getattr(type(self), handler_name, None)
                if not callable(current_func):
                    continue
                handler.handler = functools.partial(current_func, self)
                repaired += 1
            if repaired:
                logger.info("[PrivateCompanion] 已修复热更新残留回调绑定: handlers=%s", repaired)
        except Exception as exc:
            logger.warning("[PrivateCompanion] 修复热更新残留回调绑定失败: %s", _single_line(exc, 160))

    def _req041_migration_source_files(self) -> list[Path]:
        # Once a migration has a verified backup, its manifest is the authority
        # for the legacy source set. Persona profiles created later are new
        # runtime stores and must not change the resume contract on restart.
        coordinator = getattr(self, "req041_migration_coordinator", None)
        status_getter = getattr(coordinator, "status", None)
        if callable(status_getter):
            try:
                status = status_getter()
            except Exception:
                status = {}
            manifest_name = _single_line(
                status.get("backup_manifest") if isinstance(status, dict) else "",
                300,
            )
            if manifest_name:
                data_root = Path(str(getattr(self, "data_dir", "") or "")).resolve()
                try:
                    manifest_path = (data_root / manifest_name).resolve(strict=True)
                    manifest_path.relative_to(data_root)
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    entries = manifest.get("files") if isinstance(manifest, dict) else None
                    frozen: list[Path] = []
                    if isinstance(entries, list):
                        for entry in entries:
                            name = entry.get("name") if isinstance(entry, dict) else ""
                            if not isinstance(name, str) or not name.strip():
                                frozen = []
                                break
                            candidate = (data_root / name).resolve(strict=False)
                            candidate.relative_to(data_root)
                            if candidate.is_symlink():
                                frozen = []
                                break
                            frozen.append(candidate)
                    if frozen:
                        return frozen
                except (OSError, ValueError, json.JSONDecodeError):
                    # Fall through to live discovery. The coordinator will then
                    # retain its existing fail-closed validation and error code.
                    pass
        candidates: list[Path] = []
        if str(getattr(self, "storage_backend", "json") or "json").lower() == "sqlite":
            candidates.append(Path(str(getattr(self, "storage_sqlite_effective_path", "") or "")))
        else:
            candidates.append(Path(str(getattr(self, "data_file", "") or "")))
        profiles = Path(str(getattr(self, "_persona_profiles_dir", "") or ""))
        if profiles.is_dir():
            candidates.extend(sorted(profiles.glob("*.db")))
            candidates.extend(sorted(profiles.glob("*.json")))
        result: list[Path] = []
        for candidate in candidates:
            try:
                if candidate and candidate.is_file() and not candidate.is_symlink():
                    result.append(candidate)
            except OSError:
                continue
        return result

    def _req041_compatibility_snapshot(self) -> dict[str, Any]:
        return {
            "auto_profile_creation": bool(runtime_persona_setting(self, 'enable_auto_user_profile_creation', False)),
            "private_access_policy": {
                "passive_private_default": "legacy_effective",
                "configured_targets_default": bool(runtime_persona_setting(self, 'default_enable_configured_targets', False)),
            },
            "proactive_policy": {
                "proactive_only": bool(runtime_persona_setting(self, 'enable_proactive_only_mode', False)),
                "intensity": _single_line(runtime_persona_setting(self, 'proactive_intensity_preset', "off"), 40) or "off",
            },
            "tool_policy": {
                "photo": bool(runtime_persona_setting(self, 'enable_photo_text_action', False)),
                "screen": bool(runtime_persona_setting(self, 'enable_screen_glance_action', False)),
                "poke": bool(runtime_persona_setting(self, 'enable_poke_action', False)),
                "voice": bool(runtime_persona_setting(self, 'enable_voice_action', False)),
            },
            "content_policy": {
                "relationship_tiers": bool(runtime_persona_setting(self, 'enable_relationship_content_tiers', False)),
            },
            "owner_policy": {
                "configured_target": _single_line(getattr(self, "target_user_id", ""), 80) != "",
                "normal_cap_exempt": True,
                "exclusive_mode_frozen": True,
            },
            "relationship_policy": {
                "enabled": bool(runtime_persona_setting(self, 'enable_custom_relationship_stage_policy', False)),
                "positive_cap": _single_line(
                    runtime_persona_setting(self, 'relationship_positive_stage_cap_key', "deeply_bonded"), 40
                ) or "deeply_bonded",
                "group_ordinary_delta": 0,
            },
        }

    def _req041_registry_for_person(self, person_id: str) -> UnifiedPersonRegistry | None:
        """Locate exactly one persona-scoped registry for a stable person id."""
        stores: list[dict[str, Any]] = []
        default_data = getattr(self, "_data_default", None)
        if not isinstance(default_data, dict):
            default_data = self.data if isinstance(getattr(self, "data", None), dict) else None
        if isinstance(default_data, dict):
            stores.append(default_data)
        profiles = getattr(self, "_persona_data_profiles", {})
        if isinstance(profiles, dict):
            for profile_data in profiles.values():
                if isinstance(profile_data, dict) and all(profile_data is not item for item in stores):
                    stores.append(profile_data)
        matches = [
            UnifiedPersonRegistry(store)
            for store in stores
            if UnifiedPersonRegistry(store).read_projection(person_id) is not None
        ]
        return matches[0] if len(matches) == 1 else None

    def _req041_legacy_relationship_state(self, person_id: str) -> dict[str, Any] | None:
        """Read exactly one live legacy authority row for S5 reconciliation."""
        stores: list[dict[str, Any]] = []
        default_data = getattr(self, "_data_default", None)
        if not isinstance(default_data, dict):
            default_data = self.data if isinstance(getattr(self, "data", None), dict) else None
        if isinstance(default_data, dict):
            stores.append(default_data)
        profiles = getattr(self, "_persona_data_profiles", {})
        if isinstance(profiles, dict):
            for profile_data in profiles.values():
                if isinstance(profile_data, dict) and all(profile_data is not item for item in stores):
                    stores.append(profile_data)
        matches: list[dict[str, Any]] = []
        for store in stores:
            registry = UnifiedPersonRegistry(store)
            users = store.get("users") if isinstance(store.get("users"), dict) else {}
            for legacy_key, user in users.items():
                if not isinstance(user, dict) or user.get("unified_person_id") != person_id:
                    continue
                subject = _single_line(
                    user.get("identity_subject_id") or user.get("user_id") or legacy_key, 160
                )
                if not subject or not registry.matches_person_subject(person_id, subject):
                    continue
                try:
                    score = int(user.get("relationship_score", 0))
                    totals = user.get("relationship_daily_totals")
                    totals = totals if isinstance(totals, dict) else {}
                    positive = int(totals.get("positive", 0))
                    negative = int(totals.get("negative", 0))
                    effective = float(user.get("relationship_last_effective_at") or 0.0)
                except (TypeError, ValueError, OverflowError):
                    return None
                if (
                    any(isinstance(value, bool) for value in (user.get("relationship_score"), totals.get("positive"), totals.get("negative")))
                    or not -1200 <= score <= 1200 or not 0 <= positive <= 120
                    or not -180 <= negative <= 0 or not math.isfinite(effective) or effective < 0
                ):
                    return None
                role = "owner" if str(user.get("relationship_role") or "").strip().lower() == "owner" else "friend"
                mode = (
                    "owner_exclusive"
                    if role == "owner" and str(user.get("relationship_mode") or "").strip().lower() == "owner_exclusive"
                    else "normal"
                )
                matches.append({
                    "relationship_role": role,
                    "relationship_mode": mode,
                    "relationship_score": score,
                    "positive_stage_cap_key": normalize_relationship_positive_stage_cap_key(
                        user.get("relationship_positive_stage_cap_key")
                    ),
                    "daily_totals": {
                        "day": _single_line(totals.get("day"), 16),
                        "positive": positive,
                        "negative": negative,
                    },
                    "last_effective_at": effective,
                })
        return matches[0] if len(matches) == 1 else None

    def _req041_resolve_legacy_pending_for_person(self, person_id: str) -> int:
        """Resolve one S4 opaque pending row only after S5 proves exact parity."""
        coordinator = getattr(self, "req041_migration_coordinator", None)
        status = coordinator.status() if coordinator is not None else {}
        epoch = _single_line(status.get("migration_epoch"), 128) if isinstance(status, dict) else ""
        if not epoch:
            return 0
        scoped_stores: list[tuple[str, dict[str, Any]]] = []
        default_data = getattr(self, "_data_default", None)
        if not isinstance(default_data, dict):
            default_data = self.data if isinstance(getattr(self, "data", None), dict) else None
        if isinstance(default_data, dict):
            scoped_stores.append(("default", default_data))
        profiles = getattr(self, "_persona_data_profiles", {})
        if isinstance(profiles, dict):
            for persona_id, profile_data in profiles.items():
                if not isinstance(profile_data, dict):
                    continue
                scope_hash = hashlib.sha256(str(persona_id).encode("utf-8")).hexdigest()[:24]
                scoped_stores.append((f"persona:{scope_hash}", profile_data))
        matches: list[tuple[str, str]] = []
        for source_scope, store in scoped_stores:
            registry = UnifiedPersonRegistry(store)
            users = store.get("users") if isinstance(store.get("users"), dict) else {}
            for legacy_key, user in users.items():
                if not isinstance(user, dict) or user.get("unified_person_id") != person_id:
                    continue
                subject = _single_line(
                    user.get("identity_subject_id") or user.get("user_id") or legacy_key, 160
                )
                if subject and registry.matches_person_subject(person_id, subject):
                    matches.append((source_scope, str(legacy_key)))
        if len(matches) != 1:
            return 0
        source_scope, legacy_key = matches[0]
        reference = legacy_pending_reference(epoch, source_scope, legacy_key)
        return int(bool(coordinator.resolve_pending(reference)))

    def _req041_schedule_replay(self) -> None:
        worker = getattr(self, "req041_migration_replay", None)
        if worker is None:
            return
        self._req041_replay_requested = True
        task = getattr(self, "_req041_replay_task", None)
        if task is not None and not task.done():
            return
        try:
            self._req041_replay_task = asyncio.get_running_loop().create_task(
                self._req041_run_replay_batch(), name="req041-shadow-replay"
            )
            self._req041_replay_task.add_done_callback(self._req041_replay_finished)
        except RuntimeError:
            raise RuntimeError("migration_replay_loop_unavailable")

    def _req041_legacy_snapshots_locked(self) -> list[tuple[str, dict[str, Any]]]:
        snapshots: list[tuple[str, dict[str, Any]]] = []
        default_data = getattr(self, "_data_default", None)
        if not isinstance(default_data, dict):
            default_data = self.data if isinstance(getattr(self, "data", None), dict) else {}
        snapshots.append(("default", deepcopy(default_data)))
        profiles = getattr(self, "_persona_data_profiles", {})
        if isinstance(profiles, dict):
            for persona_id, profile_data in profiles.items():
                if not isinstance(profile_data, dict):
                    continue
                scope_hash = hashlib.sha256(str(persona_id).encode("utf-8")).hexdigest()[:24]
                snapshots.append((f"persona:{scope_hash}", deepcopy(profile_data)))
        return snapshots

    async def _req041_sync_scoped_now(self) -> dict[str, Any]:
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if synchronizer is None:
            return {"ok": False, "code": "scoped_projection_not_initialized", "scopes": []}
        synchronizer.mark_dirty()
        async with self._data_lock:
            snapshots = self._req041_legacy_snapshots_locked()
        results: list[dict[str, Any]] = []
        for source_scope, snapshot in snapshots:
            results.append(await asyncio.to_thread(
                synchronizer.sync_snapshot, snapshot, source_scope=source_scope
            ))
        ok = all(item.get("ok") is True for item in results)
        summary = {
            "ok": ok,
            "code": "scoped_projection_synced" if ok else "scoped_projection_degraded",
            "scopes": results,
            "records": sum(int(item.get("records") or 0) for item in results),
            "errors": sum(int(item.get("errors") or 0) for item in results),
        }
        self.req041_scoped_projection_status = summary
        return summary

    async def _req041_run_scoped_sync(self) -> None:
        while bool(getattr(self, "_req041_scoped_sync_requested", False)):
            self._req041_scoped_sync_requested = False
            result = await self._req041_sync_scoped_now()
            if result.get("ok") is not True:
                status = getattr(self, "req041_migration_status", None)
                if isinstance(status, dict):
                    status.update({"state": "degraded", "code": "scoped_projection_degraded", "scoped": result})
                return

    def _req041_scoped_sync_finished(self, task: Any) -> None:
        self._req041_scoped_sync_task = None
        if not task.cancelled():
            try:
                error = task.exception()
            except Exception:
                error = None
            if error is not None:
                self.req041_scoped_projection_status = {
                    "ok": False, "code": _single_line(error, 120) or "scoped_projection_exception"
                }
        if bool(getattr(self, "_req041_scoped_sync_requested", False)):
            self._req041_schedule_scoped_sync()

    def _req041_schedule_scoped_sync(self) -> None:
        if getattr(self, "req041_scoped_projection_sync", None) is None:
            return
        self.req041_scoped_projection_sync.mark_dirty()
        stop_event = getattr(self, "_stop_event", None)
        if stop_event is not None and callable(getattr(stop_event, "is_set", None)) and stop_event.is_set():
            return
        self._req041_scoped_sync_requested = True
        task = getattr(self, "_req041_scoped_sync_task", None)
        if isinstance(task, asyncio.Task) and not task.done():
            return
        try:
            task = asyncio.get_running_loop().create_task(
                self._req041_run_scoped_sync(), name="req041-scoped-projection-sync"
            )
        except RuntimeError:
            return
        self._req041_scoped_sync_task = task
        task.add_done_callback(self._req041_scoped_sync_finished)

    def _req041_scoped_context_for_user(
        self,
        user: dict[str, Any],
        *,
        kind: str = "private",
        group_id: str = "",
        purpose: str = "memory_read",
    ) -> NamespaceContext | None:
        if not isinstance(user, dict):
            return None
        person_id = str(user.get("unified_person_id") or "").strip()
        subject = str(user.get("identity_subject_id") or user.get("user_id") or "").strip()
        if not person_id or not subject:
            return None
        registry = self._active_unified_person_registry()
        if not registry.matches_person_subject(person_id, subject):
            return None
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if synchronizer is None:
            return None
        active_persona = self._active_persona_scope()
        persona_id = scoped_persona_ref(active_persona)
        safe_group = scoped_group_ref(persona_id, group_id) if kind == "group_member" else ""
        resolution = registry.formal_namespace_for_person(
            person_id, kind=kind, group_id=safe_group,
            policy_version=synchronizer.policy_version,
            migration_epoch=synchronizer.migration_epoch,
            purpose=purpose,
        )
        raw = resolution.get("context") if isinstance(resolution, dict) else None
        if not resolution.get("ok") or not isinstance(raw, dict):
            return None
        context = NamespaceContext(
            kind=kind, persona_id=persona_id, identity_id=person_id, group_id=safe_group,
            assurance=str(raw.get("assurance") or "verified"),
            profile_status=str(raw.get("profile_status") or "active"),
            policy_version=synchronizer.policy_version,
            migration_epoch=synchronizer.migration_epoch,
        )
        return context if not context.errors() else None

    def _req041_scoped_private_context_for_person(
        self,
        person_id: str,
        *,
        purpose: str = "memory_write",
    ) -> NamespaceContext | None:
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if synchronizer is None:
            return None
        clean_person = _single_line(person_id, 80)
        if not clean_person:
            return None
        registry = self._active_unified_person_registry()
        resolution = registry.formal_namespace_for_person(
            clean_person, kind="private",
            policy_version=synchronizer.policy_version,
            migration_epoch=synchronizer.migration_epoch,
            purpose=purpose,
        )
        raw = resolution.get("context") if isinstance(resolution, dict) else None
        if not resolution.get("ok") or not isinstance(raw, dict):
            return None
        context = NamespaceContext(
            kind="private", persona_id=scoped_persona_ref(self._active_persona_scope()),
            identity_id=clean_person, group_id="",
            assurance=str(raw.get("assurance") or "verified"), profile_status="active",
            policy_version=synchronizer.policy_version,
            migration_epoch=synchronizer.migration_epoch,
        )
        return context if not context.errors() else None

    @staticmethod
    def _req041_person_private_aux_key(person_id: str) -> str:
        """Return a stable opaque key for persona-local person-private helpers."""
        clean_person = str(person_id or "").strip()
        if not clean_person:
            return ""
        digest = hashlib.sha256(f"req041-person-private-aux:{clean_person}".encode("utf-8")).hexdigest()
        return f"person:{digest}"

    def _req041_reality_private_binding(
        self,
        user_id: Any,
        *,
        purpose: str = "memory_read",
    ) -> dict[str, Any]:
        """Resolve one mobile/reality operation to a reconciled private person scope."""
        normalized = _single_line(user_id, 120)
        users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else None
        user = users.get(normalized) if normalized and isinstance(users, dict) else None
        if not isinstance(user, dict):
            return {"ok": False, "code": "private_user_not_managed"}
        context = self._req041_scoped_context_for_user(user, kind="private", purpose=purpose)
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if context is None or synchronizer is None:
            return {"ok": False, "code": "formal_private_identity_required"}
        projection = synchronizer.read_projection(context)
        if not isinstance(projection, dict) or projection.get("ok") is not True:
            return {
                "ok": False,
                "code": str(projection.get("code") or "scoped_projection_not_reconciled")[:120]
                if isinstance(projection, dict) else "scoped_projection_not_reconciled",
            }
        person_id = _single_line(getattr(context, "identity_id", ""), 80)
        store_key = self._req041_person_private_aux_key(person_id)
        if not person_id or not store_key:
            return {"ok": False, "code": "formal_private_identity_required"}
        snapshot = self._req041_relationship_snapshot_view(
            user, source=f"reality_{_single_line(purpose, 40) or 'memory'}",
        )
        return {
            "ok": True,
            "code": "formal_private_identity_bound",
            "context": context,
            "person_id": person_id,
            "store_key": store_key,
            "subject_ref": store_key,
            "user": snapshot if isinstance(snapshot, dict) else user,
        }

    def _req041_erase_person_private_auxiliary_locked(
        self,
        person_id: str,
        subjects: list[str] | tuple[str, ...] = (),
    ) -> dict[str, int]:
        """Erase canonical and exact legacy auxiliary nodes for one person."""
        canonical = self._req041_person_private_aux_key(person_id)
        keys = {canonical} if canonical else set()
        keys.update(_single_line(item, 160) for item in subjects if _single_line(item, 160))
        counts = {"place_cognitive_maps": 0, "reality_touch_outputs": 0}
        for root_name in tuple(counts):
            root = self.data.get(root_name) if isinstance(self.data, dict) else None
            if not isinstance(root, dict):
                continue
            for key in keys:
                if key in root:
                    root.pop(key, None)
                    counts[root_name] += 1
        return counts

    def _req041_scoped_group_context(
        self,
        group_id: str,
        *,
        purpose: str = "rule_write",
    ) -> NamespaceContext | None:
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        raw_group = _single_line(group_id, 160)
        if synchronizer is None or not raw_group:
            return None
        persona_id = scoped_persona_ref(self._active_persona_scope())
        context = NamespaceContext(
            kind="group_shared", persona_id=persona_id, identity_id="",
            group_id=scoped_group_ref(persona_id, raw_group), assurance="verified",
            profile_status="active", policy_version=synchronizer.policy_version,
            migration_epoch=synchronizer.migration_epoch,
        )
        policy = AssurancePolicy()
        decision = policy.authorize(context, purpose)
        return context if decision.allowed and not context.errors() else None

    def _req041_persona_global_context(
        self, *, purpose: str = "rule_read"
    ) -> NamespaceContext | None:
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if synchronizer is None:
            return None
        context = NamespaceContext(
            kind="persona_global", persona_id=scoped_persona_ref(self._active_persona_scope()),
            identity_id="", group_id="", assurance="verified", profile_status="active",
            policy_version=synchronizer.policy_version,
            migration_epoch=synchronizer.migration_epoch,
        )
        decision = AssurancePolicy().authorize(context, purpose)
        return context if decision.allowed and not context.errors() else None

    def _req041_erase_scoped_group_data(
        self,
        group_id: str,
        *,
        operation_id: str = "",
        persona_id: str = "",
    ) -> dict[str, Any]:
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if synchronizer is None:
            if self._req041_group_remote_cleanup_required():
                return {"ok": False, "state": "degraded", "code": "scoped_group_erase_unavailable"}
            return {"ok": True, "state": "not_required", "code": "scoped_group_erase_not_required", "count": 0}
        raw_group = _single_line(group_id, 160)
        safe_persona = _single_line(persona_id, 80) or scoped_persona_ref(self._active_persona_scope())
        group_ref = scoped_group_ref(safe_persona, raw_group)
        context = NamespaceContext(
            kind="group_shared", persona_id=safe_persona, identity_id="", group_id=group_ref,
            assurance="verified", profile_status="active",
            policy_version=synchronizer.policy_version,
            migration_epoch=synchronizer.migration_epoch,
        )
        if not raw_group or context.errors():
            return {"ok": False, "state": "rejected", "code": "scoped_group_erase_context_invalid"}
        clean_operation = _single_line(operation_id, 120) or "req041-group-reset-" + uuid.uuid4().hex
        return synchronizer.erase_group_scopes(
            context, operation_id=clean_operation, reason_code="group_reset",
        )

    def _req041_memory_scope_was_bound(self) -> bool:
        """Return whether this persona has ever had a remote scoped bridge bound."""
        data = getattr(self, "data", None)
        state = data.get("_req041_memory_scope_state") if isinstance(data, dict) else None
        return isinstance(state, dict) and bool(state.get("ever_bound"))

    def _req041_mark_memory_scope_bound(self) -> None:
        """Persist remote-binding history so a later outage remains fail-closed."""
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return
        state = data.get("_req041_memory_scope_state")
        if not isinstance(state, dict):
            state = {}
            data["_req041_memory_scope_state"] = state
        if state.get("ever_bound"):
            return
        state["ever_bound"] = True
        state["bound_at"] = _now_ts()
        scheduler = getattr(self, "_schedule_data_save", None)
        if callable(scheduler):
            try:
                scheduler(delay=0.35)
            except Exception:
                pass

    def _req041_group_remote_cleanup_required(self) -> bool:
        """Decide whether deleting a group must wait for remote scope erasure.

        A fresh runtime with no remote bridge has never published scoped records,
        so local group cleanup is safe. Once a bridge was bound, an outage must
        continue to fail closed because the remote side may retain group data.
        Legacy migrations also remain fail-closed until their remote cleanup is
        available.
        """
        status = getattr(self, "req041_migration_status", None)
        if not isinstance(status, dict):
            return False
        if status.get("required"):
            if (
                not status.get("memory_bound")
                and not self._req041_memory_scope_was_bound()
            ):
                coordinator = getattr(self, "req041_migration_coordinator", None)
                status_getter = getattr(coordinator, "status", None)
                if callable(status_getter):
                    try:
                        control = status_getter()
                    except Exception:
                        control = {}
                    if isinstance(control, dict) and control.get("memory_version") == "not-detected":
                        return False
            return True
        if not status.get("scoped_required"):
            return False
        if status.get("memory_bound") or self._req041_memory_scope_was_bound():
            return True
        coordinator = getattr(self, "req041_migration_coordinator", None)
        status_getter = getattr(coordinator, "status", None)
        if callable(status_getter):
            try:
                control = status_getter()
            except Exception:
                control = {}
            if isinstance(control, dict) and control.get("source_schema_version") == "req041-fresh-v1":
                return False
        return True

    def _req041_erase_scoped_persona_data(
        self,
        persona_id: str,
        *,
        operation_id: str,
    ) -> dict[str, Any]:
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if synchronizer is None:
            status = getattr(self, "req041_migration_status", None)
            if isinstance(status, dict) and (status.get("required") or status.get("scoped_required")):
                return {"ok": False, "state": "degraded", "code": "scoped_persona_erase_unavailable"}
            return {"ok": True, "state": "not_required", "code": "scoped_persona_erase_not_required"}
        persona_ref = scoped_persona_ref(persona_id)
        context = NamespaceContext(
            kind="persona_global", persona_id=persona_ref, identity_id="", group_id="",
            assurance="verified", profile_status="active",
            policy_version=synchronizer.policy_version,
            migration_epoch=synchronizer.migration_epoch,
        )
        clean_operation = _single_line(operation_id, 120)
        if not clean_operation or context.errors():
            return {"ok": False, "state": "rejected", "code": "scoped_persona_erase_context_invalid"}
        return synchronizer.erase_persona_scopes(
            context, operation_id=clean_operation, reason_code="persona_reset",
        )

    def _req041_group_reset_sagas_locked(self) -> dict[str, dict[str, Any]]:
        sagas = self.data.get("_req041_group_reset_sagas")
        if not isinstance(sagas, dict):
            sagas = {}
            self.data["_req041_group_reset_sagas"] = sagas
        return sagas

    def _req041_finalize_group_reset_locked(self, group_id: str) -> dict[str, Any]:
        normalize = getattr(self, "_normalize_group_identity_id", None)

        def normalized(value: Any) -> str:
            if callable(normalize):
                return _single_line(normalize(value), 160)
            return _single_line(value, 160)

        clean_group = normalized(group_id)
        groups = self.data.get("groups")
        if not isinstance(groups, dict):
            groups = {}
            self.data["groups"] = groups
        matching_keys = [key for key in groups if normalized(key) == clean_group]
        for key in matching_keys:
            groups.pop(key, None)

        changed: dict[str, list[str]] = {}
        for key in (
            "group_whitelist_ids", "group_blacklist_ids",
            "expression_group_learning_source_ids", "expression_group_application_ids",
        ):
            old_values = list(getattr(self, key, []) or [])
            new_values = [
                str(item).strip() for item in old_values
                if str(item).strip() and normalized(item) != clean_group
            ]
            setattr(self, key, new_values)
            _set_into_config(self.config, key, new_values)
            if new_values != old_values:
                changed[key] = new_values
        refresher = getattr(self, "_refresh_expression_voice_profile", None)
        if callable(refresher):
            refresher()
        return {
            "removed_group": bool(matching_keys),
            "removed_whitelist": "group_whitelist_ids" in changed,
            "removed_blacklist": "group_blacklist_ids" in changed,
            "removed_expression_scope": bool(
                {"expression_group_learning_source_ids", "expression_group_application_ids"} & changed.keys()
            ),
        }

    async def reset_group_scoped_data(
        self,
        group_id: str,
        *,
        operation_id: str = "",
    ) -> dict[str, Any]:
        """Durably reset a group remotely before removing its legacy/config sources."""
        normalize = getattr(self, "_normalize_group_identity_id", None)
        clean_group = _single_line(normalize(group_id) if callable(normalize) else group_id, 160)
        if not clean_group:
            return {"ok": False, "state": "invalid", "code": "scoped_group_erase_context_invalid"}
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if synchronizer is None:
            # Startup migration runs in the background. A panel action can arrive
            # before it binds the scoped eraser, so wait for that task once instead
            # of reporting an unavailable capability during the normal race window.
            startup_tasks = getattr(self, "_startup_background_tasks", {})
            migration_task = (
                startup_tasks.get("req041_automatic_migration")
                if isinstance(startup_tasks, dict) else None
            )
            if isinstance(migration_task, asyncio.Task) and not migration_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(migration_task), timeout=20.0)
                except asyncio.TimeoutError:
                    return {
                        "ok": False, "state": "degraded",
                        "code": "scoped_group_erase_initializing",
                    }
                except Exception:
                    pass
            synchronizer = getattr(self, "req041_scoped_projection_sync", None)
            status = getattr(self, "req041_migration_status", None)
            if synchronizer is None and isinstance(status, dict) and status.get("state") == "uninitialized":
                initializer = getattr(self, "_req041_initialize_automatic_migration", None)
                if callable(initializer):
                    try:
                        await asyncio.wait_for(initializer(), timeout=20.0)
                    except asyncio.TimeoutError:
                        return {
                            "ok": False, "state": "degraded",
                            "code": "scoped_group_erase_initializing",
                        }
                    except Exception:
                        pass
                    synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if synchronizer is None:
            status = getattr(self, "req041_migration_status", None)
            if self._req041_group_remote_cleanup_required():
                logger.warning(
                    "[PrivateCompanion] 群聊删除因远端分域不可用而保留: group=%s state=%s code=%s memory_bound=%s scoped_required=%s",
                    clean_group,
                    _single_line((status or {}).get("state"), 32),
                    _single_line((status or {}).get("code"), 96),
                    bool((status or {}).get("memory_bound")),
                    bool((status or {}).get("scoped_required")),
                )
                return {"ok": False, "state": "degraded", "code": "scoped_group_erase_unavailable"}
            logger.info(
                "[PrivateCompanion] MemoryCompanion 从未绑定，群聊删除仅清理本地分域: group=%s",
                clean_group,
            )
            return {"ok": True, "state": "not_required", "code": "scoped_group_erase_not_required"}

        async with self._data_lock:
            sagas = self._req041_group_reset_sagas_locked()
            persona_reset = self.data.get("_req041_persona_reset_saga")
            if isinstance(persona_reset, dict) and persona_reset.get("state") == "confirmed":
                return {"ok": False, "state": "rejected", "code": "persona_reset_in_progress"}
            clean_operation = _single_line(operation_id, 120)
            saga = sagas.get(clean_operation) if clean_operation else None
            if saga is not None and not isinstance(saga, dict):
                return {"ok": False, "state": "rejected", "code": "group_reset_saga_invalid"}
            current_persona = scoped_persona_ref(self._active_persona_scope())
            if saga is None:
                matches = [
                    item for item in sagas.values()
                    if isinstance(item, dict)
                    and item.get("state") in {"confirmed", "config_pending"}
                    and _single_line(item.get("group_id"), 160) == clean_group
                    and _single_line(item.get("persona_id"), 80) == current_persona
                ]
                if len(matches) > 1:
                    return {"ok": False, "state": "rejected", "code": "group_reset_saga_conflict"}
                saga = matches[0] if matches else None
            if saga is None:
                clean_operation = clean_operation or "req041-group-reset-" + uuid.uuid4().hex
                saga = {
                    "operation_id": clean_operation,
                    "group_id": clean_group,
                    "persona_id": current_persona,
                    "state": "confirmed",
                    "created_at": _now_ts(),
                }
                sagas[clean_operation] = saga
                self._req041_persist_archive_saga_locked(
                    sections={"_req041_group_reset_sagas"},
                )
            else:
                clean_operation = _single_line(saga.get("operation_id"), 120)
                if (
                    not clean_operation or sagas.get(clean_operation) is not saga
                    or _single_line(saga.get("group_id"), 160) != clean_group
                    or saga.get("state") not in {"confirmed", "config_pending"}
                ):
                    return {"ok": False, "state": "rejected", "code": "group_reset_saga_invalid"}
            safe_persona = _single_line(saga.get("persona_id"), 80)

        remote = self._req041_erase_scoped_group_data(
            clean_group, operation_id=clean_operation, persona_id=safe_persona,
        )
        if not remote.get("ok"):
            return {
                "ok": False, "state": "confirmed",
                "code": str(remote.get("code") or "scoped_group_erase_failed")[:120],
                "operation_id": clean_operation,
            }

        async with self._data_lock:
            saga = self._req041_group_reset_sagas_locked().get(clean_operation)
            if not isinstance(saga, dict):
                return {"ok": False, "state": "rejected", "code": "group_reset_saga_missing"}
            local = self._req041_finalize_group_reset_locked(clean_group)
            saga["state"] = "config_pending"
            saga["remote_receipt"] = {
                "code": str(remote.get("code") or "")[:120],
                "count": int(remote.get("count") or 0),
                "namespace_count": int(remote.get("namespace_count") or 0),
            }
            saga["local_result"] = deepcopy(local)
            self._req041_persist_archive_saga_locked(
                sections={
                    "groups",
                    "expression_voice_profile",
                    "_req041_group_reset_sagas",
                },
            )

        config_saved = await self._save_config_if_possible()
        if not config_saved:
            return {
                "ok": False, "state": "config_pending", "code": "group_reset_config_save_failed",
                "operation_id": clean_operation, **local, "scoped_cleanup": remote,
            }
        async with self._data_lock:
            self._req041_group_reset_sagas_locked().pop(clean_operation, None)
            deleted_sections: set[str] = set()
            if not self.data.get("_req041_group_reset_sagas"):
                self.data.pop("_req041_group_reset_sagas", None)
                deleted_sections.add("_req041_group_reset_sagas")
            self._req041_persist_archive_saga_locked(
                sections={"_req041_group_reset_sagas"},
                deleted_sections=deleted_sections,
            )
        return {
            "ok": True, "state": "completed", "code": "group_reset_completed",
            "operation_id": clean_operation, "config_saved": True,
            **local, "scoped_cleanup": remote,
        }

    async def _req041_resume_confirmed_group_resets(self) -> dict[str, Any]:
        async with self._data_lock:
            pending = [
                deepcopy(saga) for saga in self._req041_group_reset_sagas_locked().values()
                if isinstance(saga, dict) and saga.get("state") in {"confirmed", "config_pending"}
            ][:32]
        completed = 0
        errors: list[str] = []
        for saga in pending:
            result = await self.reset_group_scoped_data(
                str(saga.get("group_id") or ""),
                operation_id=str(saga.get("operation_id") or ""),
            )
            if result.get("ok") and result.get("state") == "completed":
                completed += 1
            else:
                errors.append(str(result.get("code") or "group_reset_resume_failed")[:120])
        return {
            "ok": not errors,
            "code": "group_reset_resume_complete" if not errors else "group_reset_resume_degraded",
            "pending": len(pending), "completed": completed,
            "error_codes": sorted(set(errors))[:16],
        }

    async def _req041_resume_confirmed_persona_resets(self) -> dict[str, Any]:
        pending: list[dict[str, str]] = []
        async with self._data_lock:
            default_data = getattr(self, "_data_default", None)
            default_marker = (
                default_data.get("_req041_persona_reset_saga")
                if isinstance(default_data, dict) else None
            )
            if isinstance(default_marker, dict) and default_marker.get("state") == "confirmed":
                pending.append({
                    "persona_id": "",
                    "operation_id": str(default_marker.get("operation_id") or ""),
                    "force_default": "1",
                })
            profiles = getattr(self, "_persona_data_profiles", None)
            if isinstance(profiles, dict):
                for raw_persona, profile in profiles.items():
                    marker = profile.get("_req041_persona_reset_saga") if isinstance(profile, dict) else None
                    if isinstance(marker, dict) and marker.get("state") == "confirmed":
                        pending.append({
                            "persona_id": str(raw_persona or ""),
                            "operation_id": str(marker.get("operation_id") or ""),
                            "force_default": "0",
                        })
        completed = 0
        errors: list[str] = []
        for saga in pending[:32]:
            result = await self._reset_current_persona_store(
                saga["persona_id"], rebuild_today=False,
                operation_id=saga["operation_id"],
                _force_default_store=saga["force_default"] == "1",
            )
            if result.get("ok"):
                completed += 1
            else:
                errors.append(str(result.get("code") or "persona_reset_resume_failed")[:120])
        return {
            "ok": not errors,
            "code": "persona_reset_resume_complete" if not errors else "persona_reset_resume_degraded",
            "pending": min(len(pending), 32), "completed": completed,
            "error_codes": sorted(set(errors))[:16],
        }

    def _req041_persist_archive_saga_locked(
        self,
        *,
        sections: Collection[str] | None = None,
        deleted_sections: Collection[str] = (),
        full_scope: str | None = None,
    ) -> None:
        """Durably persist a destructive saga before any cross-store write."""
        normalized_scope = str(full_scope or "").strip() or None
        normalized_deleted = {
            str(section).strip()
            for section in deleted_sections
            if str(section).strip()
        }
        normalized_sections = {
            str(section).strip()
            for section in (sections or ())
            if str(section).strip()
        }
        if normalized_scope is None and sections is None:
            raise ValueError(
                "archive saga sections must be explicit unless full_scope is provided"
            )
        if normalized_scope is not None:
            if normalized_scope != "admin_import_export":
                raise ValueError(
                    "archive saga full_scope must be admin_import_export"
                )
            if sections is not None or normalized_deleted:
                raise ValueError(
                    "archive saga full_scope cannot be combined with sections"
                )
        else:
            # The live mapping is authoritative when an internal caller names a
            # section in both sets. Present values are upserts; absent values are
            # tombstones. Never pass an overlapping request to the writer.
            for section in normalized_sections & normalized_deleted:
                if section in self.data:
                    normalized_deleted.discard(section)
                else:
                    normalized_sections.discard(section)
        if not normalized_sections and not normalized_deleted:
            if normalized_scope is None:
                return
        validator = getattr(self, "_validate_save_request", None)
        if callable(validator):
            validator(
                None if normalized_scope is not None else normalized_sections,
                normalized_deleted,
                normalized_scope,
            )
        active_persona = str(self._active_persona_scope() or "")
        if bool(getattr(self, "enable_multi_persona_mode", False)) and active_persona:
            # Destructive sagas must be durable before touching another store;
            # the persona profile backend exposes only an immediate snapshot API.
            self._write_persona_data_snapshot_sync(active_persona, deepcopy(self.data))
            return
        if normalized_scope is not None:
            self._save_data_now_sync(full_scope="admin_import_export")
        else:
            self._save_data_now_sync(
                sections=normalized_sections,
                deleted_sections=normalized_deleted,
            )

    def _req041_scoped_archive_available(self) -> bool:
        """Return whether a confirmed identity archive can reach Memory safely."""
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if synchronizer is None or not callable(
            getattr(synchronizer, "archive_identity_scopes", None)
        ):
            return False
        status = getattr(self, "req041_migration_status", None)
        if not isinstance(status, dict):
            return False
        if str(status.get("state") or "").strip().lower() in {"degraded", "paused", "stopped"}:
            return False
        return True

    async def archive_unified_person(
        self,
        person_id: str,
        *,
        operation_id: str,
        confirmation_token: str = "",
        dry_run: bool = True,
        actor_id: str = "page_administrator",
        reason_code: str = "person_archive",
    ) -> dict[str, Any]:
        """Run the request-bound, resumable person archive saga."""
        clean_person = _single_line(person_id, 80)
        clean_operation = _single_line(operation_id, 120)
        if not clean_person or not clean_operation or type(dry_run) is not bool:
            return {"ok": False, "state": "invalid", "code": "invalid_request"}
        async with self._data_lock:
            registry = self._active_unified_person_registry()
            prepared = registry.prepare_person_archive(
                clean_person, operation_id=clean_operation,
                actor_id=actor_id, reason_code=reason_code,
            )
            if not prepared.get("ok"):
                return prepared
            self._req041_persist_archive_saga_locked(
                sections={"unified_person"},
            )
            if prepared.get("code") == "person_archived":
                subjects = registry.archived_identity_subjects(clean_person)
                removed = self._req041_erase_person_private_auxiliary_locked(clean_person, subjects)
                if sum(removed.values()) > 0:
                    self._req041_persist_archive_saga_locked(
                        sections={
                            name
                            for name, count in removed.items()
                            if int(count or 0) > 0
                        },
                    )
                return prepared
            if dry_run:
                return prepared
            if not confirmation_token or confirmation_token != prepared.get("confirmation_token"):
                return {
                    "ok": False, "state": "prepared", "code": "archive_confirmation_mismatch",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            archive_available = getattr(self, "_req041_scoped_archive_available", None)
            if callable(archive_available) and not archive_available():
                return {
                    "ok": False, "state": "prepared", "code": "scoped_identity_archive_unavailable",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            confirmed = registry.confirm_person_archive(
                clean_person, clean_operation, confirmation_token,
                actor_id=actor_id, reason_code=reason_code,
            )
            if not confirmed.get("ok"):
                return confirmed
            self._req041_persist_archive_saga_locked(
                sections={"unified_person"},
            )
            context = self._req041_scoped_private_context_for_person(clean_person)
            synchronizer = getattr(self, "req041_scoped_projection_sync", None)
            relationship_store = getattr(self, "req041_relationship_store", None)
            outbox = getattr(self, "req041_migration_outbox", None)
            if context is None or synchronizer is None:
                return {
                    "ok": False, "state": "prepared", "code": "scoped_identity_archive_unavailable",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            if relationship_store is None or not callable(getattr(relationship_store, "tombstone_account", None)):
                synchronizer.mark_dirty()
                return {
                    "ok": False, "state": "prepared", "code": "relationship_archive_unavailable",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            if outbox is None or not callable(getattr(outbox, "retire_streams", None)):
                synchronizer.mark_dirty()
                return {
                    "ok": False, "state": "prepared", "code": "archive_outbox_unavailable",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            try:
                stream_receipt = outbox.retire_streams(
                    [f"identity:{clean_person}", f"relationship:{clean_person}"],
                    synchronizer.migration_epoch,
                    operation_id=f"req041-streams-{clean_operation}", reason_code=reason_code,
                )
            except Exception as exc:
                synchronizer.mark_dirty()
                return {
                    "ok": False, "state": "prepared",
                    "code": _single_line(exc, 120) or "archive_stream_retirement_failed",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            scoped_receipt = synchronizer.archive_identity_scopes(
                context, operation_id=f"req041-scoped-{clean_operation}", reason_code=reason_code,
            )
            if not scoped_receipt.get("ok"):
                return {
                    "ok": False, "state": "prepared",
                    "code": str(scoped_receipt.get("code") or "scoped_identity_archive_failed")[:120],
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            try:
                relationship_receipt = relationship_store.tombstone_account(
                    context, operation_id=f"req041-relationship-{clean_operation}",
                    reason_code=reason_code, actor="administrator",
                )
            except Exception as exc:
                return {
                    "ok": False, "state": "prepared",
                    "code": _single_line(exc, 120) or "relationship_archive_failed",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            legacy_subjects = [
                _single_line(item.get("identity_subject_id") or item.get("user_id"), 160)
                for item in (self.data.get("users") or {}).values()
                if isinstance(item, dict)
                and _single_line(item.get("unified_person_id"), 80) == clean_person
            ] if isinstance(self.data.get("users"), dict) else []
            auxiliary_counts = self._req041_erase_person_private_auxiliary_locked(
                clean_person, legacy_subjects,
            )
            result = registry.finalize_person_archive(
                clean_person, clean_operation, confirmation_token,
                scoped_receipt, relationship_receipt, stream_receipt,
                actor_id=actor_id, reason_code=reason_code,
            )
            if result.get("changed"):
                result["auxiliary_removed_record_count"] = sum(auxiliary_counts.values())
                coordinator = getattr(self, "req041_migration_coordinator", None)
                rollback = getattr(coordinator, "rollback_identity", None)
                if callable(rollback):
                    rollback(clean_person, reason_code="person_archived")
                self._req041_persist_archive_saga_locked(
                    sections={
                        "unified_person",
                        *(
                            name
                            for name, count in auxiliary_counts.items()
                            if int(count or 0) > 0
                        ),
                    },
                )
            return result

    async def _req041_resume_confirmed_person_archives(self) -> dict[str, Any]:
        registry = self._active_unified_person_registry()
        pending = registry.confirmed_person_archives(limit=32)
        completed = 0
        errors: list[str] = []
        for saga in pending:
            result = await self.archive_unified_person(
                saga["person_id"], operation_id=saga["operation_id"],
                confirmation_token=saga["confirmation_token"], dry_run=False,
                actor_id=saga["actor_id"], reason_code=saga["reason_code"],
            )
            if result.get("ok") and result.get("code") == "person_archived":
                completed += 1
            else:
                errors.append(str(result.get("code") or "person_archive_resume_failed")[:120])
        return {
            "ok": not errors,
            "code": "person_archive_resume_complete" if not errors else "person_archive_resume_degraded",
            "pending": len(pending), "completed": completed,
            "error_codes": sorted(set(errors))[:16],
        }

    def _req041_purge_legacy_person_locked(
        self,
        person_id: str,
        subjects: list[str],
        *,
        changed_sections: set[str] | None = None,
    ) -> dict[str, int]:
        """Remove only exact identity-owned legacy nodes; never fuzzy-search text."""
        clean_person = _single_line(person_id, 80)
        subject_set = {_single_line(item, 160) for item in subjects if _single_line(item, 160)}
        counts = {"mapping_entries": 0, "list_entries": 0, "records": 0}
        identity_fields = {
            "user_id", "identity_subject_id", "platform_subject_id", "sender_id", "member_id",
            "linked_qq_user_id", "target_user_id", "qq_user_id",
        }

        def owned(value: Any) -> bool:
            if not isinstance(value, dict):
                return False
            if str(value.get("unified_person_id") or "").strip() == clean_person:
                return True
            return any(
                str(value.get(field) or "").strip() in subject_set
                for field in identity_fields
                if value.get(field) not in (None, "")
            )

        def scrub(value: Any, *, depth: int = 0) -> Any:
            if depth > 10:
                return value
            if isinstance(value, dict):
                for key in list(value):
                    item = value[key]
                    if str(key) == clean_person or str(key) in subject_set or owned(item):
                        value.pop(key, None)
                        counts["mapping_entries"] += 1
                        counts["records"] += 1
                        continue
                    value[key] = scrub(item, depth=depth + 1)
                return value
            if isinstance(value, list):
                kept: list[Any] = []
                for item in value:
                    if owned(item):
                        counts["list_entries"] += 1
                        counts["records"] += 1
                    else:
                        kept.append(scrub(item, depth=depth + 1))
                value[:] = kept
            return value

        for key in list(self.data):
            if key == "unified_person":
                continue
            before = deepcopy(self.data[key])
            scrubbed = scrub(self.data[key])
            self.data[key] = scrubbed
            if changed_sections is not None and before != scrubbed:
                changed_sections.add(str(key))
        return counts

    async def purge_unified_person(
        self,
        person_id: str,
        *,
        operation_id: str,
        confirmation_token: str = "",
        dry_run: bool = True,
        actor_id: str = "page_administrator",
        reason_code: str = "person_delete",
    ) -> dict[str, Any]:
        clean_person = _single_line(person_id, 80)
        clean_operation = _single_line(operation_id, 120)
        if not clean_person or not clean_operation or type(dry_run) is not bool:
            return {"ok": False, "state": "invalid", "code": "invalid_request"}
        async with self._data_lock:
            registry = self._active_unified_person_registry()
            prepared = registry.prepare_person_purge(
                clean_person, operation_id=clean_operation,
                actor_id=actor_id, reason_code=reason_code,
            )
            if not prepared.get("ok"):
                return prepared
            self._req041_persist_archive_saga_locked(
                sections={"unified_person"},
            )
            if prepared.get("code") == "person_purged":
                return prepared
            if dry_run:
                return prepared
            if not confirmation_token or confirmation_token != prepared.get("confirmation_token"):
                return {
                    "ok": False, "state": "prepared", "code": "purge_confirmation_mismatch",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            confirmed = registry.confirm_person_purge(
                clean_person, clean_operation, confirmation_token,
                actor_id=actor_id, reason_code=reason_code,
            )
            if not confirmed.get("ok"):
                return confirmed
            self._req041_persist_archive_saga_locked(
                sections={"unified_person"},
            )
            subjects = registry.archived_identity_subjects(clean_person)
            if int(prepared.get("detached_identity_count") or 0) > 0 and not subjects:
                return {
                    "ok": False, "state": "confirmed", "code": "purge_identity_subjects_invalid",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            outbox = getattr(self, "req041_migration_outbox", None)
            synchronizer = getattr(self, "req041_scoped_projection_sync", None)
            if outbox is None or synchronizer is None or not callable(getattr(outbox, "purge_retired_streams", None)):
                return {
                    "ok": False, "state": "confirmed", "code": "purge_outbox_unavailable",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            try:
                outbox_receipt = outbox.purge_retired_streams(
                    [f"identity:{clean_person}", f"relationship:{clean_person}"],
                    synchronizer.migration_epoch,
                    operation_id=f"req041-purge-streams-{clean_operation}", reason_code=reason_code,
                )
            except Exception as exc:
                return {
                    "ok": False, "state": "confirmed",
                    "code": _single_line(exc, 120) or "purge_outbox_failed",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            auxiliary_counts = self._req041_erase_person_private_auxiliary_locked(clean_person, subjects)
            legacy_changed_sections: set[str] = set()
            legacy_counts = self._req041_purge_legacy_person_locked(
                clean_person,
                subjects,
                changed_sections=legacy_changed_sections,
            )
            result = registry.finalize_person_purge(
                clean_person, clean_operation, confirmation_token, outbox_receipt,
                actor_id=actor_id, reason_code=reason_code,
            )
            if result.get("changed"):
                result["legacy_removed_record_count"] = int(legacy_counts.get("records") or 0)
                result["auxiliary_removed_record_count"] = sum(auxiliary_counts.values())
                if legacy_changed_sections:
                    # Legacy identity records may live under unregistered roots.
                    # This administrator-confirmed purge is therefore an explicit
                    # full-store repair boundary rather than an implicit fallback.
                    self._req041_persist_archive_saga_locked(
                        full_scope="admin_import_export",
                    )
                else:
                    self._req041_persist_archive_saga_locked(
                        sections={"unified_person"}
                        | {
                            name
                            for name, count in auxiliary_counts.items()
                            if int(count or 0) > 0
                        },
                    )
            return result

    async def _req041_resume_confirmed_person_purges(self) -> dict[str, Any]:
        registry = self._active_unified_person_registry()
        pending = registry.confirmed_person_purges(limit=32)
        completed = 0
        errors: list[str] = []
        for saga in pending:
            result = await self.purge_unified_person(
                saga["person_id"], operation_id=saga["operation_id"],
                confirmation_token=saga["confirmation_token"], dry_run=False,
                actor_id=saga["actor_id"], reason_code=saga["reason_code"],
            )
            if result.get("ok") and result.get("code") == "person_purged":
                completed += 1
            else:
                errors.append(str(result.get("code") or "person_purge_resume_failed")[:120])
        return {
            "ok": not errors,
            "code": "person_purge_resume_complete" if not errors else "person_purge_resume_degraded",
            "pending": len(pending), "completed": completed,
            "error_codes": sorted(set(errors))[:16],
        }

    def _req041_scoped_private_read_view(self, event: Any, user: dict[str, Any]) -> dict[str, Any]:
        existing = getattr(event, "req041_scoped_private_read_view", None)
        if isinstance(existing, dict):
            return existing
        view = dict(user) if isinstance(user, dict) else user
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        context = self._req041_scoped_context_for_user(user, kind="private")
        if synchronizer is None or context is None:
            return view
        projection = synchronizer.read_projection(context)
        if not isinstance(projection, dict) or projection.get("ok") is not True:
            if isinstance(view, dict):
                view["req041_scoped_read_generation"] = "new_unavailable"
                try:
                    setattr(event, "req041_scoped_private_read_view", view)
                except Exception:
                    pass
            return view
        persona_context = self._req041_persona_global_context(purpose="rule_read")
        persona_projection = (
            synchronizer.read_projection(persona_context) if persona_context is not None else None
        )
        view = overlay_private_runtime_view(view, projection, persona_projection)
        if not isinstance(view, dict) or view.get("req041_scoped_read_generation") != "new":
            return view
        try:
            setattr(event, "req041_scoped_private_read_view", view)
        except Exception:
            pass
        return view

    def _req041_scoped_group_read_view(
        self,
        event: Any,
        *,
        group_id: str,
        group: dict[str, Any],
        sender_id: str,
        relationship_user: dict[str, Any] | None,
    ) -> dict[str, Any]:
        existing = getattr(event, "req041_scoped_group_read_view", None)
        if isinstance(existing, dict):
            return existing
        view = deepcopy(group) if isinstance(group, dict) else group
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if synchronizer is None or not isinstance(view, dict):
            return view
        persona_id = scoped_persona_ref(self._active_persona_scope())
        safe_group = scoped_group_ref(persona_id, group_id)
        shared = NamespaceContext(
            kind="group_shared", persona_id=persona_id, identity_id="", group_id=safe_group,
            assurance="verified", profile_status="active", policy_version=synchronizer.policy_version,
            migration_epoch=synchronizer.migration_epoch,
        )
        shared_projection = synchronizer.read_projection(shared)
        persona_context = self._req041_persona_global_context(purpose="rule_read")
        persona_projection = (
            synchronizer.read_projection(persona_context) if persona_context is not None else None
        )
        member_projection = None
        member_context = self._req041_scoped_context_for_user(
            relationship_user or {}, kind="group_member", group_id=group_id, purpose="profile_read"
        )
        if member_context is not None:
            member_projection = synchronizer.read_projection(member_context)
        if not isinstance(shared_projection, dict) or shared_projection.get("ok") is not True:
            view["req041_scoped_read_generation"] = "new_unavailable"
            try:
                setattr(event, "req041_scoped_group_read_view", view)
            except Exception:
                pass
            return view
        view = overlay_group_runtime_view(
            view, shared_projection, sender_id=sender_id, member_projection=member_projection,
            persona_projection=persona_projection,
        )
        if not isinstance(view, dict) or view.get("req041_scoped_read_generation") != "new":
            return view
        view["req041_scoped_read_generation"] = "new"
        try:
            setattr(event, "req041_scoped_group_read_view", view)
        except Exception:
            pass
        return view

    def _req041_replay_finished(self, _task: Any) -> None:
        self._req041_replay_task = None
        if bool(getattr(self, "_req041_replay_requested", False)):
            try:
                self._req041_schedule_replay()
            except RuntimeError:
                status = getattr(self, "req041_migration_status", None)
                if isinstance(status, dict):
                    status.update({"state": "paused", "code": "migration_replay_loop_unavailable"})

    def _req041_relationship_read_view(
        self,
        event: Any,
        user: dict[str, Any],
        *,
        kind: str = "private",
        group_id: str = "",
    ) -> dict[str, Any]:
        existing = getattr(event, "req041_relationship_read_view", None)
        if isinstance(existing, dict):
            return existing
        router = getattr(self, "req041_relationship_read_router", None)
        if not isinstance(user, dict):
            return user
        result: dict[str, Any] = {}
        view = user
        if router is not None:
            event_ref = self._event_message_id(event)
            if not event_ref:
                event_ref = f"{getattr(event, 'unified_msg_origin', '')}:{uuid.uuid4().hex}"
            result = router.begin(user, event_ref=event_ref, kind=kind, group_id=group_id)
            view = result.get("user") if isinstance(result.get("user"), dict) else user
        production_view = view
        view = self._lab_fixture_relationship_view(event, view)
        if router is None and view is production_view:
            return user
        try:
            setattr(event, "req041_relationship_read_view", view)
            if router is not None:
                setattr(event, "req041_read_chain_id", str(result.get("chain_id") or ""))
                setattr(event, "req041_read_generation", str(result.get("generation") or "legacy"))
                setattr(event, "req041_read_identity_id", str(result.get("identity_id") or ""))
        except Exception:
            pass
        return view

    def _req041_relationship_snapshot_view(
        self,
        user: dict[str, Any],
        *,
        source: str,
    ) -> dict[str, Any]:
        """Take one short-lived private relationship view for background decisions."""
        if not isinstance(user, dict):
            return user
        if user.get("_req041_relationship_snapshot_resolved") is True:
            return user
        relationship_view = user
        router = getattr(self, "req041_relationship_read_router", None)
        if router is not None and user.get("req041_read_generation") != "new":
            result = router.begin(
                user,
                event_ref=f"snapshot:{_single_line(source, 60) or 'relationship'}:{uuid.uuid4().hex}",
                kind="private",
            )
            chain_id = str(result.get("chain_id") or "")
            try:
                relationship_view = (
                    result.get("user") if isinstance(result.get("user"), dict) else user
                )
            finally:
                if chain_id:
                    try:
                        router.finish(chain_id)
                    except Exception:
                        pass
        scoped_getter = getattr(self, "_req041_scoped_private_read_view", None)
        return (
            scoped_getter(None, relationship_view)
            if callable(scoped_getter) else relationship_view
        )

    def _req041_group_sender_is_human(self, event: AstrMessageEvent) -> bool:
        if not self._event_is_inbound_chat_message(event):
            return False
        sender_id = self._event_sender_id(event)
        self_id = self._event_self_id(event)
        if not sender_id or (self_id and sender_id == self_id):
            return False
        raw = self._event_raw_payload(event)
        sender = raw.get("sender") if isinstance(raw.get("sender"), dict) else {}
        message_obj = getattr(event, "message_obj", None)
        message_sender = getattr(message_obj, "sender", None) if message_obj is not None else None

        def field(owner: Any, name: str) -> Any:
            if isinstance(owner, dict):
                return owner.get(name)
            try:
                return getattr(owner, name, None)
            except Exception:
                return None

        for owner in (raw, sender, message_sender):
            if owner is None:
                continue
            for key in ("is_bot", "bot", "is_system", "system"):
                value = field(owner, key)
                if value is True or str(value or "").strip().lower() in {"1", "true", "yes", "bot", "system"}:
                    return False
            role = str(field(owner, "role") or field(owner, "sender_type") or "").strip().lower()
            if role in {"assistant", "bot", "system", "service"}:
                return False
        return True

    def _req041_prepare_group_affinity_candidate(
        self,
        event: AstrMessageEvent,
        *,
        group_id: str,
        relationship_user: dict[str, Any] | None,
        scene_trigger: str,
        forwarded: bool,
    ) -> dict[str, Any] | None:
        if (
            not isinstance(relationship_user, dict)
            or str(getattr(event, "req041_read_generation", "") or "") != "new"
            or getattr(self, "req041_dual_write_producer", None) is None
            or getattr(self, "req041_migration_replay", None) is None
            or getattr(self, "req041_relationship_store", None) is None
            or not bool(runtime_persona_setting(self, 'enable_custom_relationship_stage_policy', False))
        ):
            return None
        direction = "at_bot" if scene_trigger == "at_bot" else "reply_bot" if scene_trigger == "reply_bot" else ""
        context = self._req041_scoped_context_for_user(
            relationship_user,
            kind="group_member",
            group_id=group_id,
            purpose="relationship_write",
        )
        if context is None:
            return None
        candidate = prepare_group_affinity_candidate(
            context,
            raw_group_id=group_id,
            allowlist=getattr(self, "group_relationship_affinity_allowlist", ()),
            enabled=bool(getattr(self, "enable_group_relationship_affinity", False)),
            inbound_event_id=self._event_message_id(event),
            directed_by=direction,
            legacy_user_key=str(relationship_user.get("user_id") or ""),
            inbound=self._event_is_inbound_chat_message(event),
            human_sender=self._req041_group_sender_is_human(event),
            forwarded=bool(forwarded),
            echo=False,
            historical=False,
        )
        if isinstance(candidate, dict):
            setattr(event, "req041_group_affinity_candidate", candidate)
        return candidate

    async def _req041_settle_confirmed_group_affinity(self, event: AstrMessageEvent) -> None:
        candidate = getattr(event, "req041_group_affinity_candidate", None)
        if not isinstance(candidate, dict) or bool(candidate.get("settled")):
            return
        if not self._reaction_expression_primary_reply_confirmed(
            event, require_segmented_complete=True,
        ):
            return
        live_allowlist = normalize_group_allowlist(
            getattr(self, "group_relationship_affinity_allowlist", ())
        )
        if (
            not bool(runtime_persona_setting(self, 'enable_custom_relationship_stage_policy', False))
            or not bool(getattr(self, "enable_group_relationship_affinity", False))
            or str(candidate.get("raw_group_id") or "") not in live_allowlist
        ):
            candidate["settled"] = True
            candidate["result_code"] = "group_affinity_config_revoked"
            return
        store = getattr(self, "req041_relationship_store", None)
        if not isinstance(store, RelationshipAccountStore):
            return
        admission = await asyncio.to_thread(
            admit_confirmed_group_affinity,
            candidate,
            store,
            reply_succeeded=True,
            requested_delta=4,
            group_daily_net_cap=int(getattr(self, "group_relationship_daily_net_cap", 2)),
            group_window_seconds=int(getattr(self, "group_relationship_window_minutes", 30)) * 60,
            group_window_absolute_cap=int(getattr(self, "group_relationship_window_absolute_cap", 1)),
            group_person_daily_absolute_cap=int(
                getattr(self, "group_relationship_person_daily_absolute_cap", 4)
            ),
            group_scope_daily_absolute_cap=int(
                getattr(self, "group_relationship_scope_daily_absolute_cap", 20)
            ),
            group_event_cap=4,
        )
        if admission is None:
            return
        candidate["settled"] = True
        candidate["result_code"] = admission.code
        if admission.admitted_delta == 0:
            return
        context = NamespaceContext(**candidate.get("context", {}))
        user_key = str(candidate.get("legacy_user_key") or "")
        async with self._data_lock:
            users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
            user = users.get(user_key) if isinstance(users, dict) else None
            if (
                not isinstance(user, dict)
                or str(user.get("unified_person_id") or "") != context.identity_id
            ):
                candidate["settled"] = False
                raise RuntimeError("group_affinity_legacy_subject_mismatch")
            result = self._apply_relationship_event(
                user,
                admission.admitted_delta,
                reason_code="direct_group_interaction",
                event_id=admission.event_id,
                now=_now_ts(),
                req041_group_admission_event_id=admission.event_id,
            )
            candidate["legacy_result_code"] = str(result.get("code") or "")
            if result.get("changed"):
                self._schedule_data_save(sections={"users"})

    @filter.after_message_sent(priority=-105000)
    @_multi_persona_event_context
    async def settle_req041_group_affinity_after_send(
        self, event: AstrMessageEvent, *args, **kwargs
    ) -> None:
        if self is None or not self.enabled:
            return
        try:
            await self._req041_settle_confirmed_group_affinity(event)
        except Exception as exc:
            status = getattr(self, "req041_migration_status", None)
            if isinstance(status, dict):
                status.update({"state": "degraded", "code": "group_affinity_settlement_failed"})
            logger.warning(
                "[PrivateCompanion] REQ-041 群好感度结算失败，已保持事件可重放: %s",
                _single_line(exc, 160),
            )

    @filter.after_message_sent(priority=-110000)
    @_multi_persona_event_context
    async def finish_req041_read_chain(self, event: AstrMessageEvent, *args, **kwargs) -> None:
        router = getattr(self, "req041_relationship_read_router", None)
        chain_id = str(getattr(event, "req041_read_chain_id", "") or "")
        if router is not None and chain_id:
            try:
                await asyncio.to_thread(router.finish, chain_id)
            except Exception as exc:
                logger.debug("[PrivateCompanion] REQ-041 读链清理失败: %s", _single_line(exc, 120))
            try:
                setattr(event, "req041_read_chain_id", "")
            except Exception:
                pass
            status = getattr(self, "req041_migration_status", None)
            metrics = getattr(self, "req041_observability", None)
            phase = str((status or {}).get("phase") or "") if isinstance(status, dict) else ""
            now = _now_ts()
            next_check = float(getattr(self, "_req041_stability_next_check_at", 0.0) or 0.0)
            if phase in {"S6", "S7", "S8"} and metrics is not None and now >= next_check:
                local_samples = sum(
                    int((item.get("local") or {}).get("samples") or 0)
                    for item in (metrics.snapshot().get("stages") or {}).values()
                    if isinstance(item, dict)
                )
                if local_samples >= 20:
                    self._req041_stability_next_check_at = now + 60.0
                    self._req041_schedule_replay()

    async def _req041_run_replay_batch(self) -> None:
        worker = getattr(self, "req041_migration_replay", None)
        if worker is None:
            return
        while bool(getattr(self, "_req041_replay_requested", False)):
            self._req041_replay_requested = False
            result = await asyncio.to_thread(worker.run_batch)
            if result.get("status") == "paused":
                status = getattr(self, "req041_migration_status", None)
                if isinstance(status, dict):
                    status.update({
                        "state": "paused",
                        "code": str(result.get("error_code") or "migration_replay_failed")[:120],
                        "s5": result,
                    })
                return
            runtime = getattr(self, "req041_migration_status", None)
            coordinator = getattr(self, "req041_migration_coordinator", None)
            outbox = getattr(self, "req041_migration_outbox", None)
            if isinstance(runtime, dict) and coordinator is not None and outbox is not None:
                try:
                    stability_fn = advance_migration_stability
                except NameError:
                    from migration_stability import advance_migration_stability as stability_fn
                control = coordinator.status()
                scoped = runtime.get("scoped") if isinstance(runtime.get("scoped"), dict) else {}
                stability = await asyncio.to_thread(
                    stability_fn,
                    coordinator=coordinator, outbox=outbox,
                    migration_epoch=str(control.get("migration_epoch") or ""),
                    replay_ok=True, scoped_ok=bool(scoped.get("ok")),
                    memory_bound=bool(runtime.get("memory_bound")),
                    observability=self.req041_observability,
                    boot_ref=str(getattr(self, "_req041_runtime_boot_ref", f"boot-{id(self)}")),
                )
                control = coordinator.status()
                runtime.update({"phase": control.get("phase", runtime.get("phase")),
                                "checkpoint": control.get("checkpoint", runtime.get("checkpoint")),
                                "stability": stability})
            if int(result.get("count") or 0) > 0 or int(result.get("recovered") or 0) > 0:
                self._req041_replay_requested = True

    async def _req041_initialize_automatic_migration(self) -> None:
        try:
            metrics_type = Req041Observability
        except NameError:  # Standalone migration harnesses load selected methods only.
            from req041_observability import Req041Observability as metrics_type
        if not isinstance(getattr(self, "req041_observability", None), metrics_type):
            self.req041_observability = metrics_type()
        if not str(getattr(self, "_req041_runtime_boot_ref", "") or ""):
            self._req041_runtime_boot_ref = f"boot-{id(self)}"
        coordinator = getattr(self, "req041_migration_coordinator", None)
        outbox = getattr(self, "req041_migration_outbox", None)
        if coordinator is None or outbox is None:
            self.req041_migration_status = {
                "required": False, "state": "degraded", "code": "migration_runtime_unavailable"
            }
            return
        sources = self._req041_migration_source_files()
        presence_getter = getattr(self, "_memory_companion_presence", None)
        try:
            presence = presence_getter() if callable(presence_getter) else {}
        except Exception:
            presence = {}
        memory_version = _single_line((presence or {}).get("version"), 32) or "not-detected"
        companion_version = _single_line((getattr(self, "plugin_identity", {}) or {}).get("version"), 32) or "unknown"
        try:
            current_status = coordinator.status()
            is_fresh_runtime = current_status.get("source_schema_version") == "req041-fresh-v1"
            if not sources and not current_status:
                current_status = await asyncio.to_thread(
                    coordinator.initialize_fresh_runtime,
                    policy_version="req041-v1",
                    target_schema_version="req041-v1",
                    companion_version=companion_version,
                    memory_version=memory_version,
                )
                is_fresh_runtime = True
            if is_fresh_runtime:
                await self._req041_initialize_fresh_scoped_runtime(current_status)
                return
            async with self._data_lock:
                source_inventory = await asyncio.to_thread(
                    inspect_migration_sources,
                    self.data_dir,
                    sources,
                )
                status = await asyncio.to_thread(
                    coordinator.start_or_resume,
                    source_files=sources,
                    policy_version="req041-v1",
                    source_schema_version=source_inventory["source_schema_version"],
                    target_schema_version="req041-v1",
                    companion_version=companion_version,
                    memory_version=memory_version,
                    source_inventory=source_inventory,
                )
            if status.get("phase") == "S1" and status.get("state") != "paused":
                await asyncio.to_thread(coordinator.capture_compatibility, self._req041_compatibility_snapshot())
                status = coordinator.status()
            epoch = str(status.get("migration_epoch") or "")
            policy = str(status.get("policy_version") or "")
            await asyncio.to_thread(outbox.begin_epoch, epoch, policy_version=policy)
            self.req041_dual_write_producer = MigrationDualWriteProducer(
                outbox=outbox,
                coordinator=coordinator,
                migration_epoch=epoch,
                policy_version=policy,
                on_enqueued=self._req041_schedule_replay,
            )
            if status.get("state") == "paused":
                self.req041_migration_status = {
                    "required": True, "state": "paused", "code": status.get("error_code") or "migration_paused",
                    "phase": status.get("phase", "S0"), "dual_write": "capturing_while_paused",
                }
                return
            if status.get("phase") == "S2":
                status = await asyncio.to_thread(coordinator.transition, "S3", checkpoint="durable_outbox_active")

            backfill_result: dict[str, Any] = {"ok": True, "code": "s4_not_required"}
            if status.get("phase") in {"S3", "S4"}:
                try:
                    async with self._data_lock:
                        legacy_snapshots = self._req041_legacy_snapshots_locked()
                    backfiller = await asyncio.to_thread(
                        MigrationBackfill,
                        coordinator=coordinator,
                        relationship_path=Path(self.data_dir) / "req041_relationship.db",
                        migration_epoch=epoch,
                        policy_version=policy,
                        outbox=outbox,
                    )
                    backfill_counts: dict[str, Any] = {
                        "phase": status.get("phase", "S3"), "migrated": 0, "idempotent": 0,
                        "pending": 0, "conflicts": 0, "formal_identities": 0, "legacy_users": 0,
                        "identity_baselines": 0,
                        "source_scopes": len(legacy_snapshots),
                    }
                    for source_scope, legacy_snapshot in legacy_snapshots:
                        scoped_counts = await asyncio.to_thread(
                            backfiller.run,
                            legacy_snapshot,
                            source_scope=source_scope,
                        )
                        backfill_counts["phase"] = scoped_counts["phase"]
                        for count_key in (
                            "migrated", "idempotent", "pending", "conflicts",
                            "formal_identities", "legacy_users",
                            "identity_baselines",
                        ):
                            backfill_counts[count_key] += int(scoped_counts[count_key])
                    self.req041_migration_backfill = backfiller
                    self.req041_relationship_store = backfiller.relationships
                    backfill_result = {"ok": True, "code": "s4_shadow_backfilled", **backfill_counts}
                    status = coordinator.status()
                except Exception as backfill_exc:
                    backfill_result = {
                        "ok": False,
                        "code": _single_line(backfill_exc, 120) or "s4_backfill_failed",
                    }
                    logger.warning(
                        "[PrivateCompanion] REQ-041 S4 Shadow 回填失败，继续使用 legacy 路径: %s",
                        _single_line(backfill_exc, 160),
                    )

            relationship_store = getattr(self, "req041_relationship_store", None)
            if relationship_store is None and status.get("phase") in {"S4", "S5", "S6", "S7", "S8", "S9"}:
                backfiller = await asyncio.to_thread(
                    MigrationBackfill,
                    coordinator=coordinator,
                    relationship_path=Path(self.data_dir) / "req041_relationship.db",
                    migration_epoch=epoch,
                    policy_version=policy,
                    outbox=outbox,
                )
                self.req041_migration_backfill = backfiller
                self.req041_relationship_store = backfiller.relationships
                relationship_store = backfiller.relationships
                relationship_store.set_observability(self.req041_observability)

            replay_result: dict[str, Any] = {"status": "skipped", "code": "s5_not_ready"}
            if relationship_store is not None and backfill_result.get("ok"):
                if status.get("phase") == "S4":
                    status = await asyncio.to_thread(
                        coordinator.transition, "S5", checkpoint="ordered_shadow_replay_active"
                    )
                if status.get("phase") in {"S5", "S6", "S7", "S8", "S9"}:
                    active_registry = self._active_unified_person_registry()
                    replay_worker = MigrationReplayWorker(
                        outbox=outbox,
                        coordinator=coordinator,
                        relationship_store=relationship_store,
                        registry=active_registry,
                        registry_resolver=self._req041_registry_for_person,
                        legacy_relationship_resolver=self._req041_legacy_relationship_state,
                        legacy_pending_resolver=self._req041_resolve_legacy_pending_for_person,
                        enable_gap_recovery=True,
                        migration_epoch=epoch,
                        policy_version=policy,
                        observability=self.req041_observability,
                    )
                    self.req041_migration_replay = replay_worker
                    await asyncio.to_thread(
                        outbox.set_epoch_state, epoch, "replaying", checkpoint="s5_replay_batch"
                    )
                    replay_result = await asyncio.to_thread(replay_worker.run_batch)
                    if replay_result.get("status") == "ok" and status.get("phase") == "S5":
                        status = await asyncio.to_thread(
                            coordinator.transition, "S6", checkpoint="per_identity_relationship_cutover_enabled"
                        )
                        replay_result = await asyncio.to_thread(replay_worker.run_batch)
                    if replay_result.get("status") == "ok":
                        await asyncio.to_thread(
                            outbox.set_epoch_state, epoch, "active", checkpoint="s5_reconciled"
                        )
                    else:
                        status = coordinator.status()
                    if replay_result.get("status") == "ok":
                        self.req041_relationship_read_router = MigrationRelationshipReadRouter(
                            coordinator=coordinator,
                            relationship_store=relationship_store,
                            registry_resolver=self._req041_registry_for_person,
                            migration_epoch=epoch,
                            policy_version=policy,
                            observability=self.req041_observability,
                        )
                        await asyncio.to_thread(
                            coordinator.prune_read_chains, older_than=_now_ts() - 3600
                        )

            remote = {"ok": False, "state": "degraded", "code": "memory_bridge_unavailable"}
            bridge_getter = getattr(self, "_memory_companion_bridge", None)
            bridge = bridge_getter() if callable(bridge_getter) else None
            binder = getattr(self, "_memory_companion_bind_namespace_epoch", None)
            if bridge is not None and callable(binder):
                remote = binder(
                    bridge,
                    operation_id=f"req041-bind-{epoch}",
                    migration_epoch=epoch,
                    policy_version=policy,
                )
            scoped_result: dict[str, Any] = {
                "ok": False, "code": "namespace_scoped_api_not_bound", "scopes": []
            }
            archive_resume: dict[str, Any] = {
                "ok": True, "code": "person_archive_resume_not_required",
                "pending": 0, "completed": 0, "error_codes": [],
            }
            purge_resume: dict[str, Any] = {
                "ok": True, "code": "person_purge_resume_not_required",
                "pending": 0, "completed": 0, "error_codes": [],
            }
            group_reset_resume: dict[str, Any] = {
                "ok": True, "code": "group_reset_resume_not_required",
                "pending": 0, "completed": 0, "error_codes": [],
            }
            persona_reset_resume: dict[str, Any] = {
                "ok": True, "code": "persona_reset_resume_not_required",
                "pending": 0, "completed": 0, "error_codes": [],
            }
            if remote.get("ok") and bridge is not None:
                self._req041_mark_memory_scope_bound()
                self.req041_scoped_projection_sync = ScopedProjectionSynchronizer(
                    read=lambda namespace, **kwargs: self._memory_companion_read_scoped_record(
                        bridge, namespace, **kwargs
                    ),
                    list_records=lambda namespace, **kwargs: self._memory_companion_list_scoped_records(
                        bridge, namespace, **kwargs
                    ),
                    upsert=lambda namespace, **kwargs: self._memory_companion_upsert_scoped_record(
                        bridge, namespace, **kwargs
                    ),
                    tombstone=lambda namespace, **kwargs: self._memory_companion_tombstone_scoped_record(
                        bridge, namespace, **kwargs
                    ),
                    tombstone_identity_scopes=lambda namespace, **kwargs: self._memory_companion_tombstone_scoped_identity_scopes(
                        bridge, namespace, **kwargs
                    ),
                    erase_group_scopes=lambda namespace, **kwargs: self._memory_companion_erase_scoped_group_scopes(
                        bridge, namespace, **kwargs
                    ),
                    erase_persona_scopes=lambda namespace, **kwargs: self._memory_companion_erase_scoped_persona_scopes(
                        bridge, namespace, **kwargs
                    ),
                    migration_epoch=epoch,
                    policy_version=policy,
                    observability=self.req041_observability,
                )
                resumer = getattr(self, "_req041_resume_confirmed_person_archives", None)
                if callable(resumer):
                    archive_resume = await resumer()
                purge_resumer = getattr(self, "_req041_resume_confirmed_person_purges", None)
                if archive_resume.get("ok") and callable(purge_resumer):
                    purge_resume = await purge_resumer()
                group_resumer = getattr(self, "_req041_resume_confirmed_group_resets", None)
                if archive_resume.get("ok") and purge_resume.get("ok") and callable(group_resumer):
                    group_reset_resume = await group_resumer()
                persona_resumer = getattr(self, "_req041_resume_confirmed_persona_resets", None)
                if (
                    archive_resume.get("ok") and purge_resume.get("ok")
                    and group_reset_resume.get("ok") and callable(persona_resumer)
                ):
                    persona_reset_resume = await persona_resumer()
                if archive_resume.get("ok") and purge_resume.get("ok") and group_reset_resume.get("ok") and persona_reset_resume.get("ok"):
                    scoped_result = await self._req041_sync_scoped_now()
                else:
                    scoped_result = {
                        "ok": False, "code": "lifecycle_resume_degraded", "scopes": [],
                    }
            self.req041_migration_status = {
                "required": True,
                "state": "active" if remote.get("ok") and archive_resume.get("ok") and purge_resume.get("ok") and group_reset_resume.get("ok") and persona_reset_resume.get("ok") and scoped_result.get("ok") and backfill_result.get("ok") and replay_result.get("status") == "ok" else (
                    "paused" if status.get("state") == "paused" else "degraded"
                ),
                "code": (
                    "migration_shadow_active"
                    if remote.get("ok") and archive_resume.get("ok") and purge_resume.get("ok") and group_reset_resume.get("ok") and persona_reset_resume.get("ok") and scoped_result.get("ok") and backfill_result.get("ok") and replay_result.get("status") == "ok"
                    else str(
                        backfill_result.get("code") if not backfill_result.get("ok")
                        else replay_result.get("error_code") if replay_result.get("status") == "paused"
                        else archive_resume.get("code") if not archive_resume.get("ok")
                        else purge_resume.get("code") if not purge_resume.get("ok")
                        else group_reset_resume.get("code") if not group_reset_resume.get("ok")
                        else persona_reset_resume.get("code") if not persona_reset_resume.get("ok")
                        else scoped_result.get("code") if remote.get("ok") and not scoped_result.get("ok")
                        else remote.get("code") or "migration_degraded"
                    )[:120]
                ),
                "phase": status.get("phase", "S5"),
                "memory_bound": bool(remote.get("ok")),
                "checkpoint": status.get("checkpoint", ""),
                "s4": backfill_result,
                "s5": replay_result,
                "dual_write": "capturing",
                "scoped": scoped_result,
                "archive_resume": archive_resume,
                "purge_resume": purge_resume,
                "group_reset_resume": group_reset_resume,
                "persona_reset_resume": persona_reset_resume,
            }
            try:
                stability_fn = advance_migration_stability
            except NameError:
                from migration_stability import advance_migration_stability as stability_fn
            stability = await asyncio.to_thread(
                stability_fn,
                coordinator=coordinator, outbox=outbox, migration_epoch=epoch,
                replay_ok=replay_result.get("status") == "ok",
                scoped_ok=bool(scoped_result.get("ok")), memory_bound=bool(remote.get("ok")),
                observability=self.req041_observability,
                boot_ref=self._req041_runtime_boot_ref,
            )
            status = coordinator.status()
            self.req041_migration_status.update({
                "phase": status.get("phase", self.req041_migration_status.get("phase")),
                "checkpoint": status.get("checkpoint", self.req041_migration_status.get("checkpoint")),
                "stability": stability,
            })
        except Exception as exc:
            status = coordinator.status()
            self.req041_migration_status = {
                "required": bool(status),
                "state": "paused" if status.get("state") == "paused" else "degraded",
                "code": _single_line(exc, 120) or "migration_startup_failed",
                "phase": status.get("phase", "S0") if status else "S0",
            }
            logger.warning(
                "[PrivateCompanion] REQ-041 自动迁移启动失败，继续使用官方 legacy 路径: %s",
                _single_line(exc, 160),
            )

    async def _req041_initialize_fresh_scoped_runtime(
        self,
        status: dict[str, Any],
    ) -> None:
        """Bring a source-free install directly into the normal scoped runtime."""
        coordinator = self.req041_migration_coordinator
        outbox = self.req041_migration_outbox
        epoch = _single_line(status.get("migration_epoch"), 128)
        policy = _single_line(status.get("policy_version"), 64)
        if not epoch or not policy:
            raise RuntimeError("fresh_runtime_contract_invalid")
        await asyncio.to_thread(outbox.begin_epoch, epoch, policy_version=policy)
        relationship_store = RelationshipAccountStore(
            Path(self.data_dir) / "req041_relationship.db",
            active_migration_epoch=epoch,
            observability=self.req041_observability,
        )
        self.req041_relationship_store = relationship_store
        self.req041_dual_write_producer = MigrationDualWriteProducer(
            outbox=outbox,
            coordinator=coordinator,
            migration_epoch=epoch,
            policy_version=policy,
            on_enqueued=self._req041_schedule_replay,
        )
        replay_worker = MigrationReplayWorker(
            outbox=outbox,
            coordinator=coordinator,
            relationship_store=relationship_store,
            registry=self._active_unified_person_registry(),
            registry_resolver=self._req041_registry_for_person,
            legacy_relationship_resolver=self._req041_legacy_relationship_state,
            legacy_pending_resolver=self._req041_resolve_legacy_pending_for_person,
            enable_gap_recovery=True,
            migration_epoch=epoch,
            policy_version=policy,
            observability=self.req041_observability,
        )
        self.req041_migration_replay = replay_worker
        await asyncio.to_thread(outbox.set_epoch_state, epoch, "active", checkpoint="fresh_runtime_active")
        replay_result = await asyncio.to_thread(replay_worker.run_batch)
        self.req041_relationship_read_router = MigrationRelationshipReadRouter(
            coordinator=coordinator,
            relationship_store=relationship_store,
            registry_resolver=self._req041_registry_for_person,
            migration_epoch=epoch,
            policy_version=policy,
            observability=self.req041_observability,
        )

        remote = {"ok": False, "state": "degraded", "code": "memory_bridge_unavailable"}
        bridge_getter = getattr(self, "_memory_companion_bridge", None)
        bridge = bridge_getter() if callable(bridge_getter) else None
        binder = getattr(self, "_memory_companion_bind_namespace_epoch", None)
        if bridge is not None and callable(binder):
            remote = binder(
                bridge,
                operation_id=f"req041-bind-{epoch}",
                migration_epoch=epoch,
                policy_version=policy,
            )
        scoped_result: dict[str, Any] = {
            "ok": False, "code": "namespace_scoped_api_not_bound", "scopes": []
        }
        if remote.get("ok") and bridge is not None:
            self._req041_mark_memory_scope_bound()
            self.req041_scoped_projection_sync = ScopedProjectionSynchronizer(
                read=lambda namespace, **kwargs: self._memory_companion_read_scoped_record(
                    bridge, namespace, **kwargs
                ),
                list_records=lambda namespace, **kwargs: self._memory_companion_list_scoped_records(
                    bridge, namespace, **kwargs
                ),
                upsert=lambda namespace, **kwargs: self._memory_companion_upsert_scoped_record(
                    bridge, namespace, **kwargs
                ),
                tombstone=lambda namespace, **kwargs: self._memory_companion_tombstone_scoped_record(
                    bridge, namespace, **kwargs
                ),
                tombstone_identity_scopes=lambda namespace, **kwargs: self._memory_companion_tombstone_scoped_identity_scopes(
                    bridge, namespace, **kwargs
                ),
                erase_group_scopes=lambda namespace, **kwargs: self._memory_companion_erase_scoped_group_scopes(
                    bridge, namespace, **kwargs
                ),
                erase_persona_scopes=lambda namespace, **kwargs: self._memory_companion_erase_scoped_persona_scopes(
                    bridge, namespace, **kwargs
                ),
                migration_epoch=epoch,
                policy_version=policy,
                observability=self.req041_observability,
            )
            scoped_result = await self._req041_sync_scoped_now()
        ready = bool(
            remote.get("ok")
            and scoped_result.get("ok")
            and replay_result.get("status") == "ok"
        )
        self.req041_migration_status = {
            "required": False,
            "scoped_required": True,
            "state": "active" if ready else "degraded",
            "code": "fresh_scoped_runtime_active" if ready else str(
                replay_result.get("error_code")
                if replay_result.get("status") != "ok"
                else scoped_result.get("code") if remote.get("ok")
                else remote.get("code") or "fresh_scoped_runtime_degraded"
            )[:120],
            "phase": "S9",
            "memory_bound": bool(remote.get("ok")),
            "checkpoint": status.get("checkpoint", "fresh_runtime_initialized"),
            "dual_write": "capturing",
            "s5": replay_result,
            "scoped": scoped_result,
        }

    async def initialize(self):
        global _private_companion_plugin
        await self._initialize_before_publication()
        try:
            await resume_story_handoff(self)
        except asyncio.CancelledError:
            raise
        except StoryAuthorityError as exc:
            # A durable marker is irreversible, but Content may legitimately
            # load later. Keep the Companion core available while Story stays
            # fenced and a later startup/API call replays the same marker.
            logger.warning(
                "[PrivateCompanion] Story handoff replay pending: code=%s",
                exc.code,
            )
        except Exception:
            logger.warning(
                "[PrivateCompanion] Story handoff replay pending: "
                "code=story_handoff_replay_failed"
            )
        # Keep the prior ready instance visible until every startup step succeeds.
        activate = getattr(self.extension_api, "_activate_story_migration_api", None)
        if not callable(activate) or not activate():
            return
        # No await may split activation, supersession, and global publication.
        with _private_companion_runtime.lock:
            store_manager = getattr(self, "store_manager", None)
            activate_persistence = getattr(
                store_manager, "activate_persistence_generation", None
            )
            if store_manager is not None and not callable(activate_persistence):
                raise RuntimeError("persistence generation activation is unavailable")
            if callable(activate_persistence):
                activate_persistence()
            else:
                owner_token = str(
                    getattr(self, "_persistence_owner_token", "") or ""
                ).strip()
                data_file = getattr(self, "data_file", None)
                if not owner_token or data_file is None or not str(data_file).strip():
                    raise RuntimeError(
                        "direct persistence generation activation is unavailable"
                    )
                activate_persistence_owner(owner_token, [data_file])
            previous = _private_companion_runtime.active_plugin
            if previous is not None and previous is not self:
                previous_api = getattr(previous, "extension_api", None)
                supersede = getattr(previous_api, "_supersede_story_migration_api", None)
                if callable(supersede):
                    supersede()
            _private_companion_runtime.active_plugin = self
        _private_companion_plugin = self

    async def _initialize_before_publication(self):
        self._repair_private_companion_handler_bindings()
        if getattr(self, "_legacy_enabled_config_disabled", False):
            logger.warning(
                "[PrivateCompanion] 检测到旧版配置 enabled=false；该字段已废弃并被忽略。"
                "如需停用插件，请在 AstrBot 官方插件管理页关闭本插件。"
            )
        self._log_registered_command_handlers()
        self._install_send_message_to_user_tool_sanitizer()
        boundary_ability_registrar = getattr(self, "_register_relationship_boundary_proactive_ability", None)
        if callable(boundary_ability_registrar) and bool(runtime_persona_setting(self, 'enable_relationship_boundary_feedback', True)):
            boundary_ability_registrar()
        self._schedule_default_persona_prompt_refresh()
        await self._body_monitor_integration.set_enabled(self.enable_body_monitor_integration)
        needs_startup_save = False
        agenda_before = bool(getattr(self, "_agenda_migration_dirty", False))
        self._agenda_prepare_store()
        agenda_migration_changed = bool(
            getattr(self, "_agenda_migration_dirty", False) and not agenda_before
        )
        if agenda_migration_changed:
            needs_startup_save = True
        async with self._data_lock:
            changed = False
            raw_users = self.data.get("users") if isinstance(self.data, dict) else None
            if isinstance(raw_users, dict):
                cleaned_habit_users = 0
                for habit_user in raw_users.values():
                    if isinstance(habit_user, dict) and self._sanitize_user_behavior_habit_patterns(habit_user):
                        cleaned_habit_users += 1
                if cleaned_habit_users:
                    changed = True
                    logger.info(
                        "[PrivateCompanion] 已清理旧版低质量用户习惯记录: users=%s",
                        cleaned_habit_users,
                    )
            if runtime_persona_setting(self, 'default_enable_configured_targets', True):
                self._sync_configured_targets()
                changed = True
            recovered_troubleshooting = self._recover_stale_troubleshooting_proactive_plans()
            if recovered_troubleshooting:
                logger.info("[PrivateCompanion] 已恢复未完成的排障临时主动任务: %s", recovered_troubleshooting)
            if self._prime_enabled_user_schedules():
                changed = True
            if recovered_troubleshooting:
                changed = True
            if changed:
                needs_startup_save = True
        if needs_startup_save:
            # Initialization may combine an agenda schema migration with legacy
            # user repair and recovery across several durable roots. Keep this
            # one-time boundary explicit and distinguish migration from upkeep.
            if agenda_migration_changed:
                self._schedule_data_save(
                    full_scope="startup_migration",
                    delay=0.5,
                )
            else:
                self._schedule_data_save(
                    full_scope="startup_maintenance",
                    delay=0.5,
                )
        self._create_startup_background_task(
            "req041_automatic_migration",
            self._req041_initialize_automatic_migration,
        )
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._scheduler_loop())
            logger.info("[PrivateCompanion] 主动消息循环已启动")
        self._create_startup_background_task(
            "mobile_location_watch",
            self._mobile_location_watch_loop,
        )
        if self._startup_maintenance_task is None or self._startup_maintenance_task.done():
            self._startup_maintenance_task = asyncio.create_task(self._run_startup_background_maintenance())
        self._create_startup_background_task(
            "reset_stale_qq_presence",
            self._reset_stale_qq_presence_if_needed,
        )
        self._create_startup_background_task("prepare_today", self._startup_prepare_today)
        # Keep one orchestrator alive in multi-persona mode so each persona's
        # own enable/time/provider settings are evaluated inside its ContextVar.
        if runtime_persona_setting(self, 'enable_daily_review', True) or bool(getattr(self, "enable_multi_persona_mode", False)):
            self._create_startup_background_task("daily_review", self._daily_review_loop)
        if self.enable_balance_awareness:
            self._create_startup_background_task(
                "refresh_balance_awareness",
                self._maybe_refresh_balance_awareness,
            )
        self._create_startup_background_task(
            "refresh_passive_injection_cache",
            self._refresh_passive_injection_cache,
        )
        await self._proactive_chat_runtime_bridge.start()
        standalone_webui = getattr(self, "standalone_webui", None)
        if standalone_webui is not None:
            try:
                await standalone_webui.start()
            except Exception as exc:
                logger.warning(
                    "[PrivateCompanion] 独立陪伴 WebUI 启动失败: %s",
                    _single_line(exc, 160),
                    exc_info=True,
                )

    def _create_startup_background_task(self, label: str, operation: Any) -> asyncio.Task:
        previous = self._startup_background_tasks.get(label)
        if isinstance(previous, asyncio.Task) and not previous.done():
            return previous
        task = asyncio.create_task(operation())
        self._startup_background_tasks[label] = task

        def discard_finished_task(finished: asyncio.Task) -> None:
            if self._startup_background_tasks.get(label) is finished:
                self._startup_background_tasks.pop(label, None)
            if finished.cancelled():
                return
            try:
                error = finished.exception()
            except asyncio.CancelledError:
                return
            if error is not None:
                logger.warning(
                    "[PrivateCompanion] startup background task failed: task=%s error=%s",
                    _single_line(label, 100) or "startup",
                    _single_line(error, 180),
                    exc_info=(type(error), error, error.__traceback__),
                )

        task.add_done_callback(discard_finished_task)
        return task

    @asynccontextmanager
    async def _temporarily_release_data_lock(self):
        """Release the data lock for an external await, then reacquire it safely."""
        lock = getattr(self, "_data_lock", None)
        if lock is None or not lock.locked():
            yield
            return
        lock.release()
        reacquire_cancelled = False
        try:
            yield
        finally:
            while True:
                try:
                    await lock.acquire()
                    break
                except asyncio.CancelledError:
                    reacquire_cancelled = True
            if reacquire_cancelled:
                raise asyncio.CancelledError

    def _create_lifecycle_background_task(
        self,
        operation: Any,
        *,
        label: str,
    ) -> asyncio.Task | None:
        """Track delayed sends and short-lived jobs so plugin reload can cancel them."""
        stop_event = getattr(self, "_stop_event", None)
        if isinstance(stop_event, asyncio.Event) and stop_event.is_set():
            closer = getattr(operation, "close", None)
            if callable(closer):
                closer()
            logger.debug(
                "[PrivateCompanion] 插件已进入终止流程，跳过创建后台任务: task=%s",
                _single_line(label, 100) or "background",
            )
            return None
        try:
            task = asyncio.create_task(operation)
        except RuntimeError:
            closer = getattr(operation, "close", None)
            if callable(closer):
                closer()
            logger.warning(
                "[PrivateCompanion] 后台任务无法启动：当前没有运行中的事件循环 task=%s",
                _single_line(label, 100) or "background",
            )
            return None
        tasks = getattr(self, "_lifecycle_background_tasks", None)
        if not isinstance(tasks, dict):
            tasks = {}
            self._lifecycle_background_tasks = tasks
        tasks[task] = _single_line(label, 100) or "background"

        def discard_finished_task(finished: asyncio.Task) -> None:
            registry = getattr(self, "_lifecycle_background_tasks", None)
            task_label = label
            if isinstance(registry, dict):
                task_label = registry.pop(finished, task_label)
            if finished.cancelled():
                return
            try:
                error = finished.exception()
            except asyncio.CancelledError:
                return
            if error is not None:
                logger.warning(
                    "[PrivateCompanion] 后台任务异常结束: task=%s error=%s",
                    _single_line(task_label, 100) or "background",
                    _single_line(error, 180),
                    exc_info=(type(error), error, error.__traceback__),
                )

        task.add_done_callback(discard_finished_task)
        tracker = getattr(self, "_track_final_response_background_task", None)
        if callable(tracker):
            tracker(task, label)
        return task

    async def _cancel_lifecycle_background_tasks(self, timeout: float = 3.0) -> None:
        registry = getattr(self, "_lifecycle_background_tasks", None)
        if not isinstance(registry, dict) or not registry:
            return
        current = asyncio.current_task()
        pending_tasks = {
            task
            for task in list(registry)
            if isinstance(task, asyncio.Task) and task is not current and not task.done()
        }
        for task in pending_tasks:
            task.cancel()
        if pending_tasks:
            done, pending = await asyncio.wait(pending_tasks, timeout=max(0.0, float(timeout)))
            for task in done:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    # The task completion callback logs the original exception.
                    pass
            if pending:
                labels = sorted(
                    {
                        _single_line(registry.get(task), 100) or "background"
                        for task in pending
                    }
                )
                logger.warning(
                    "[PrivateCompanion] 终止后台任务超时,继续卸载: tasks=%s",
                    "，".join(labels),
                )
        registry.clear()

    def _log_registered_command_handlers(self) -> None:
        expected = {
            "companion_command": "/陪伴(alias: /私聊陪伴, /主动陪伴)",
            "group_companion_command": "/陪伴群(alias: /群陪伴, /群聊陪伴)",
        }
        found: set[str] = set()
        try:
            for handler in star_handlers_registry:
                callback = getattr(handler, "handler", None) or getattr(handler, "func", None)
                handler_name = (
                    getattr(handler, "handler_name", "")
                    or getattr(handler, "name", "")
                    or getattr(callback, "__name__", "")
                )
                if handler_name in expected:
                    found.add(handler_name)
        except Exception as exc:
            logger.debug("[PrivateCompanion] 指令注册诊断失败: %s", _single_line(exc, 120))
            return
        registered = [expected[name] for name in expected if name in found]
        missing = [expected[name] for name in expected if name not in found]
        if registered:
            logger.info("[PrivateCompanion] AstrBot 指令已注册: %s", "；".join(registered))
        if missing:
            logger.warning("[PrivateCompanion] AstrBot 指令注册诊断未找到: %s", "；".join(missing))

    @story_legacy_sync_operation("startup.story-maintenance")
    def _run_startup_data_maintenance_locked(self) -> bool:
        changed = False

        def run_step(label: str, func: Any) -> None:
            nonlocal changed
            started = time.perf_counter()
            try:
                if callable(func) and func():
                    changed = True
            except Exception as exc:
                logger.warning("[PrivateCompanion] 启动后台维护步骤失败: %s error=%s", label, _single_line(exc, 160))
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if elapsed_ms > 1200:
                logger.warning("[PrivateCompanion] 启动后台维护步骤耗时较高: step=%s elapsed=%sms", label, elapsed_ms)

        run_step("legacy_prompt_trace_cleanup", self._cleanup_legacy_proactive_prompt_traces)
        run_step("framework_meta_leak_cleanup", self._cleanup_framework_meta_leak_records)
        run_step("creative_fallback_cleanup", self._cleanup_legacy_creative_fallback_chunks)
        run_step("runtime_social_fact_sanitize", self._sanitize_runtime_social_facts_inplace)
        run_step("false_sleep_interaction_cleanup", self._cleanup_false_sleep_interaction_updates)
        run_step("private_user_alias_merge", self._merge_private_user_alias_records)
        run_step("reaction_expression_orphan_user_cleanup", self._cleanup_orphan_reaction_expression_users)
        run_step("group_slang_cleanup", self._cleanup_all_group_slang_terms)
        run_step("recall_image_cache_cleanup", lambda: self._cleanup_recall_message_image_cache(force=True))

        def cleanup_groups() -> bool:
            groups = self.data.get("groups") if isinstance(self.data.get("groups"), dict) else {}
            if not isinstance(groups, dict):
                return False
            group_changed = False
            cleaner = getattr(self, "_cleanup_group_members", None)
            edge_cleaner = getattr(self, "_cleanup_group_relationship_edges", None)
            for raw_group in groups.values():
                if not isinstance(raw_group, dict):
                    continue
                if callable(cleaner) and cleaner(raw_group):
                    group_changed = True
                if callable(edge_cleaner) and edge_cleaner(raw_group):
                    group_changed = True
            return group_changed

        run_step("group_record_cleanup", cleanup_groups)

        if runtime_persona_setting(self, 'worldbook_auto_import', True):
            run_step("worldbook_auto_import", self._import_worldbook_entries_from_sources)
        return changed

    async def _run_startup_background_maintenance(self) -> None:
        await asyncio.sleep(0)
        started = time.perf_counter()
        try:
            if bool(getattr(self, "_startup_photo_reference_catalog_migration_pending", False)):
                config_started = time.perf_counter()
                catalog_saved = await self._save_config_if_possible()
                if catalog_saved and _set_into_config(self.config, "photo_reference_catalog_version", CATALOG_VERSION):
                    self.photo_reference_catalog_version = CATALOG_VERSION
                    marker_saved = await self._save_config_if_possible()
                    if marker_saved:
                        self._startup_photo_reference_catalog_migration_pending = False
                        self.photo_reference_catalog_read_only = False
                        logger.info(
                            "[PrivateCompanion] 参考图目录迁移完成: version=%s references=%s",
                            CATALOG_VERSION,
                            len(runtime_persona_setting(self, 'photo_reference_catalog', ()) or ()),
                        )
                    else:
                        logger.error("[PrivateCompanion] 参考图目录已保存，但迁移版本号保存失败；下次启动会安全重试")
                elif not catalog_saved:
                    logger.error("[PrivateCompanion] 参考图目录迁移保存失败，当前进程继续使用只读内存投影")
                else:
                    logger.error("[PrivateCompanion] 参考图目录已保存，但迁移版本号无法写入；当前进程继续使用只读内存投影")
                elapsed_ms = int((time.perf_counter() - config_started) * 1000)
                if elapsed_ms > 1200:
                    logger.warning("[PrivateCompanion] 启动后台配置保存耗时较高: elapsed=%sms", elapsed_ms)
            elif _safe_int(getattr(self, "_startup_config_migration_changes", 0), 0, 0) > 0:
                config_started = time.perf_counter()
                await self._save_config_if_possible()
                elapsed_ms = int((time.perf_counter() - config_started) * 1000)
                if elapsed_ms > 1200:
                    logger.warning("[PrivateCompanion] 启动后台配置保存耗时较高: elapsed=%sms", elapsed_ms)
            try:
                await asyncio.wait_for(self._apply_sqlite_wal_optimizations(), timeout=20)
            except asyncio.TimeoutError:
                logger.warning("[PrivateCompanion] SQLite WAL 后台优化超时,已跳过本轮启动优化")
            await self._image_companion_maintenance()
            if self._nai_image_selected():
                await self._nai_image_maintenance()
            async with self._data_lock:
                if self._run_startup_data_maintenance_locked():
                    # This one-time pass can scrub legacy records across several
                    # roots, so it intentionally uses the startup maintenance scope.
                    self._save_data_sync(full_scope="startup_maintenance")
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if elapsed_ms > 1200:
                logger.info("[PrivateCompanion] 启动后台维护完成: elapsed=%sms", elapsed_ms)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[PrivateCompanion] 启动后台维护失败: %s", _single_line(exc, 160), exc_info=True)

    async def terminate(self):
        global _private_companion_plugin
        lab_fixture_adapter = getattr(self, "_lab_fixture_adapter", None)
        close_lab_fixture = getattr(lab_fixture_adapter, "close", None)
        if callable(close_lab_fixture):
            try:
                close_lab_fixture()
            except Exception as exc:
                logger.warning(
                    "[PrivateCompanion] LAB fixture 门控清理失败，继续关闭插件: %s",
                    type(exc).__name__,
                )
        self._lab_fixture_adapter = None
        close_extension = getattr(
            getattr(self, "extension_api", None),
            "_close_story_migration_api",
            None,
        )
        if callable(close_extension):
            close_extension()
        self._stop_event.set()
        standalone_webui = getattr(self, "standalone_webui", None)
        if standalone_webui is not None:
            try:
                await standalone_webui.stop()
            except Exception as exc:
                logger.warning(
                    "[PrivateCompanion] 独立陪伴 WebUI 停止失败: %s",
                    _single_line(exc, 160),
                )
        cleanup_delivery_caches = getattr(self, "_cleanup_framework_delivery_caches", None)
        if callable(cleanup_delivery_caches):
            cleanup_delivery_caches(force=True)
        await self._cancel_lifecycle_background_tasks()
        invalidate_bridge = getattr(self, "_memory_companion_invalidate_bridge_cache", None)
        if callable(invalidate_bridge):
            invalidate_bridge()
        scoped_sync = getattr(self, "req041_scoped_projection_sync", None)
        if scoped_sync is not None:
            mark_dirty = getattr(scoped_sync, "mark_dirty", None)
            if callable(mark_dirty):
                mark_dirty()
        self.req041_scoped_projection_sync = None

        runtime_bridge = getattr(self, "_proactive_chat_runtime_bridge", None)
        if runtime_bridge is not None:
            try:
                await runtime_bridge.stop()
            except Exception as exc:
                logger.warning(
                    "[PrivateCompanion] 终止 Proactive Chat 深度联动失败: %s",
                    _single_line(exc, 160),
                )

        async def cancel_task(task: Any, label: str, timeout: float = 3.0) -> None:
            if not isinstance(task, asyncio.Task) or task.done():
                return
            task.cancel()
            done, pending = await asyncio.wait({task}, timeout=timeout)
            if pending:
                logger.warning("[PrivateCompanion] 终止后台任务超时,继续卸载: task=%s", label)
                return
            for finished in done:
                try:
                    await finished
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.debug("[PrivateCompanion] 终止后台任务时收到异常: task=%s error=%s", label, _single_line(exc, 160))

        if self._task:
            await cancel_task(self._task, "proactive_scheduler")
        for task in list(self._passive_input_status_tasks.values()):
            if isinstance(task, asyncio.Task) and not task.done():
                task.cancel()
        self._passive_input_status_tasks.clear()
        startup_task = getattr(self, "_startup_maintenance_task", None)
        await cancel_task(startup_task, "startup_maintenance")
        replay_task = getattr(self, "_req041_replay_task", None)
        self._req041_replay_requested = False
        await cancel_task(replay_task, "req041_shadow_replay")
        scoped_task = getattr(self, "_req041_scoped_sync_task", None)
        self._req041_scoped_sync_requested = False
        await cancel_task(scoped_task, "req041_scoped_projection_sync")
        startup_background_tasks = list(getattr(self, "_startup_background_tasks", {}).items())
        for label, task in startup_background_tasks:
            await cancel_task(task, f"startup_{label}")
        self._startup_background_tasks.clear()
        group_image_tasks = list(getattr(self, "_group_image_understanding_tasks", {}).items())
        for task_key, entry in group_image_tasks:
            task = entry.get("task") if isinstance(entry, dict) else None
            await cancel_task(task, f"group_image_{_single_line(task_key, 80)}")
        self._group_image_understanding_tasks.clear()
        troubleshooting_wakeup_tasks = list(getattr(self, "_troubleshooting_proactive_wakeup_tasks", {}).items())
        for user_id, task in troubleshooting_wakeup_tasks:
            await cancel_task(task, f"troubleshooting_proactive_{_single_line(user_id, 40)}")
        self._troubleshooting_proactive_wakeup_tasks = {}
        try:
            await asyncio.wait_for(self._flush_scheduled_data_save(), timeout=3.0)
        except asyncio.TimeoutError:
            logger.warning(
                "[PrivateCompanion] Scheduled persistence did not drain before "
                "shutdown; final persistence will continue in the background"
            )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.debug(
                "[PrivateCompanion] Waiting for scheduled persistence during "
                "shutdown failed: %s",
                _single_line(exc, 160),
            )
        self._termination_flush_already_attempted = True
        close_image_download_session = getattr(self, "_close_external_image_download_session", None)
        if callable(close_image_download_session):
            try:
                await asyncio.wait_for(close_image_download_session(), timeout=3.0)
            except asyncio.TimeoutError:
                logger.warning("[PrivateCompanion] 终止时关闭在线图片下载会话超时")
            except Exception as exc:
                logger.debug("[PrivateCompanion] 终止时关闭在线图片下载会话失败: %s", _single_line(exc, 160))
        final_save_task = asyncio.create_task(self._save_data_on_terminate())
        self._termination_save_task = final_save_task
        self._termination_save_status = {
            "state": "running",
            "started_at": asyncio.get_running_loop().time(),
            "task_id": id(final_save_task),
        }

        def observe_final_save(task: asyncio.Task) -> None:
            if task.cancelled():
                self._termination_save_status = {
                    **getattr(self, "_termination_save_status", {}),
                    "state": "cancelled",
                    "completed_at": asyncio.get_running_loop().time(),
                }
                if getattr(self, "_termination_save_task", None) is task:
                    self._termination_save_task = None
                return
            try:
                error = task.exception()
            except (asyncio.CancelledError, asyncio.InvalidStateError):
                return
            if error is not None:
                self._termination_save_status = {
                    **getattr(self, "_termination_save_status", {}),
                    "state": "failed",
                    "completed_at": asyncio.get_running_loop().time(),
                    "error": _single_line(error, 160),
                }
                logger.warning(
                    "[PrivateCompanion] Final shutdown persistence failed: %s",
                    _single_line(error, 160),
                )
            else:
                persistence = dict(
                    getattr(self, "_last_persistence_write_status", {}) or {}
                )
                self._termination_save_status = {
                    **getattr(self, "_termination_save_status", {}),
                    "state": (
                        "superseded"
                        if persistence.get("accepted") is False
                        else "completed"
                    ),
                    "completed_at": asyncio.get_running_loop().time(),
                    "persistence": persistence,
                }
            if getattr(self, "_termination_save_task", None) is task:
                self._termination_save_task = None

        final_save_task.add_done_callback(observe_final_save)
        try:
            await asyncio.wait_for(asyncio.shield(final_save_task), timeout=3.0)
        except asyncio.TimeoutError:
            self._termination_save_status = {
                **getattr(self, "_termination_save_status", {}),
                "state": "timed_out_background",
                "timed_out_at": asyncio.get_running_loop().time(),
            }
            logger.warning(
                "[PrivateCompanion] Final shutdown persistence timed out; the "
                "shielded task will continue in the background"
            )
        with _private_companion_runtime.lock:
            if _private_companion_runtime.active_plugin is self:
                _private_companion_runtime.active_plugin = None
        if _private_companion_plugin is self:
            _private_companion_plugin = None

    async def _save_data_on_terminate(self) -> None:
        if not bool(getattr(self, "_termination_flush_already_attempted", False)):
            await self._flush_scheduled_data_save()

        manager = getattr(self, "store_manager", None)
        manager_backend = str(getattr(manager, "backend_name", "") or "").lower()
        sqlite_incremental = bool(
            manager_backend == "sqlite"
            and callable(getattr(manager, "save_sections", None))
        )
        if sqlite_incremental:
            await self._flush_default_data_save_on_terminate()
        else:
            task = getattr(self, "_data_save_task", None)
            if (
                isinstance(task, asyncio.Task)
                and not task.done()
                and task is not asyncio.current_task()
            ):
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.debug(
                        "[PrivateCompanion] Waiting for the default JSON writer "
                        "during shutdown failed: %s",
                        _single_line(exc, 160),
                    )
            async with self._data_lock:
                snapshot = deepcopy(getattr(self, "_data_default", self.data))
            await asyncio.to_thread(self._write_data_snapshot_sync, snapshot)

        primary = self._primary_persona_id()
        if bool(getattr(self, "enable_multi_persona_mode", False)):
            profiles = getattr(self, "_persona_data_profiles", {}) or {}
            persona_ids = (
                [
                    str(persona_id)
                    for persona_id, profile in profiles.items()
                    if isinstance(profile, dict) and str(persona_id) != primary
                ]
                if isinstance(profiles, dict)
                else []
            )
            for persona_id in persona_ids:
                task = getattr(self, "_persona_data_save_tasks", {}).get(persona_id)
                if isinstance(task, asyncio.Task) and not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        pass
                    except Exception as exc:
                        logger.debug(
                            "[PrivateCompanion] Waiting for a persona writer during "
                            "shutdown failed: persona=%s error=%s",
                            persona_id,
                            _single_line(exc, 160),
                        )
                async with self._data_lock:
                    profile = getattr(self, "_persona_data_profiles", {}).get(persona_id)
                    if not isinstance(profile, dict):
                        continue
                    snapshot = deepcopy(profile)
                await asyncio.to_thread(
                    self._write_persona_data_snapshot_sync,
                    persona_id,
                    snapshot,
                )

    @filter.event_message_type(filter.EventMessageType.ALL, priority=11000)
    @_multi_persona_event_context
    async def prepare_tts_streaming_boundary(self, event: AstrMessageEvent, *args, **kwargs):
        """在 AstrBot 读取流式配置前，为可能进入插件 TTS 的回合预留完整回复。"""
        if self is None or not self.enabled:
            return
        preflight = getattr(self, "_tts_turn_requires_complete_reply", None)
        disable = getattr(self, "_disable_streaming_for_tts_turn", None)
        if not callable(preflight) or not callable(disable):
            return
        try:
            if preflight(event):
                disable(event)
        except Exception as exc:
            logger.debug(
                "[PrivateCompanion] TTS 流式预判失败，保留默认流式行为: session=%s error=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                _single_line(exc, 160),
            )

    @filter.event_message_type(filter.EventMessageType.ALL, priority=10000)
    @_multi_persona_event_context
    async def observe_recall_enhancement_events(self, event: AstrMessageEvent, *args, **kwargs):
        """记录普通消息和 QQ/OneBot 撤回事件，用于撤回增强。"""
        if self is None:
            return
        if self._is_onebot_poke_notice_event(event):
            # OneBot 把戳一戳同时映射为消息事件。它由专用插件处理，不能参与
            # 陪伴的活动、繁忙闸门或撤回缓存链路。
            logger.debug("[PrivateCompanion] 放行 OneBot 戳一戳 notice 给专用插件")
            return
        self._qzone_note_event_bot(event)
        if not self.enabled:
            return
        self._note_inbound_activity_for_scope(event)
        self._busy_reply_note_inbound_event(event)
        if not runtime_persona_setting(self, 'enable_recall_enhancement', True):
            return
        raw = self._event_raw_payload(event)
        if raw.get("post_type") == "notice":
            notice_type = str(raw.get("notice_type") or "").strip()
            if notice_type not in {"friend_recall", "group_recall"}:
                return
            message_id = _single_line(raw.get("message_id") or raw.get("msg_id"), 120)
            if not message_id:
                return
            scope = _single_line(
                (f"group:{raw.get('group_id')}" if raw.get("group_id") else "")
                or (f"private:{raw.get('user_id')}" if raw.get("user_id") else "")
                or getattr(event, "unified_msg_origin", ""),
                160,
            )
            self._record_recalled_message_id(
                message_id,
                scope=scope,
                notice_type=notice_type,
                sender_id=_single_line(raw.get("user_id"), 80),
            )
            if notice_type == "friend_recall":
                recall_user_id = _single_line(raw.get("user_id"), 80)
                if recall_user_id:
                    self._stop_passive_input_status_loop(recall_user_id)
                    logger.info(
                        "[PrivateCompanion] 用户撤回消息，已停止私聊输入状态: user=%s message_id=%s",
                        recall_user_id,
                        message_id,
                    )
            logger.info(
                "[PrivateCompanion] 已记录消息撤回: notice=%s scope=%s message_id=%s",
                notice_type,
                scope or "-",
                message_id,
            )
            return

        await self._cache_message_for_recall(event)
        if not runtime_persona_setting(self, 'enable_forbidden_word_recall', False) or not self._forbidden_recall_words():
            return
        message_id = self._event_message_id(event)
        if not message_id:
            return
        is_group = bool(self._extract_group_id_from_event(event))
        is_self = self._event_sender_id(event) and self._event_sender_id(event) == self._event_self_id(event)
        scope = runtime_persona_setting(self, 'recall_forbidden_scope', 'bot_and_group')
        if scope == "bot_only" and not is_self:
            return
        if scope == "group_only" and not is_group:
            return
        if scope == "bot_and_group" and not (is_self or is_group):
            return
        text = self._event_text_for_recall_cache(event, limit=2000)
        hit = self._forbidden_recall_hit(text)
        if not hit:
            return
        ok = await self._try_delete_message(event, message_id, reason=f"forbidden:{hit}")
        logger.info(
            "[PrivateCompanion] 违禁词撤回检查命中: scope=%s self=%s group=%s ok=%s word=%s message_id=%s",
            scope,
            is_self,
            is_group,
            ok,
            _single_line(hit, 40),
            message_id,
        )

    @filter.on_decorating_result(priority=10000)
    @_multi_persona_event_context
    async def bridge_proactive_chat_outbound(self, event: AstrMessageEvent, *args, **kwargs):
        """识别 Proactive Chat 的装饰发送链，接入状态、边界和 TTS 统一出口。"""
        if self is None or not self.enabled or not runtime_persona_setting(self, 'enable_proactive_chat_integration', True):
            return
        bridge_context = self._proactive_chat_decorating_context()
        if not bridge_context.get("detected"):
            return
        try:
            if not bool(event.is_private_chat()):
                return
        except Exception:
            return
        session_id = str(getattr(event, "unified_msg_origin", "") or "")
        user_id, user = self._proactive_chat_bridge_user(session_id)
        if not user_id or not isinstance(user, dict) or not self._user_enabled_for_proactive(user_id, user):
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            return
        chain_text = "".join(
            str(getattr(component, "text", "") or "")
            for component in chain
            if isinstance(component, Plain)
        ).strip()
        source_text = str(bridge_context.get("full_text") or chain_text).strip()
        if not source_text:
            return
        attempt_id = _single_line(bridge_context.get("attempt_id"), 100)
        replaced_attempts = getattr(self, "_proactive_chat_bridge_replaced_record_attempts", None)
        if not isinstance(replaced_attempts, dict):
            replaced_attempts = {}
            self._proactive_chat_bridge_replaced_record_attempts = replaced_attempts
        now = _now_ts()
        for stale_attempt, created_at in list(replaced_attempts.items()):
            if now - _safe_float(created_at, 0) > 10 * 60:
                replaced_attempts.pop(stale_attempt, None)
        if attempt_id and attempt_id in replaced_attempts and chain_text:
            event.set_result(self._build_result_from_chain([]))
            event.stop_event()
            logger.info(
                "[PrivateCompanion] 已跳过 Proactive Chat 改写后的重复文本分支: session=%s attempt=%s",
                _single_line(session_id, 120),
                attempt_id,
            )
            return
        token = _single_line(bridge_context.get("token"), 80)
        if _single_line(user.get("proactive_chat_bridge_session"), 180) == _single_line(session_id, 180):
            token = token or _single_line(user.get("proactive_chat_bridge_token"), 80)
        segment_count = max(1, _safe_int(bridge_context.get("segment_count"), 1, 1))
        segment_index = max(0, min(_safe_int(bridge_context.get("segment_index"), 0, 0), segment_count - 1))
        setattr(event, "private_companion_proactive_framework", True)
        setattr(event, "_private_companion_external_proactive_source", "proactive_chat")
        setattr(event, "_private_companion_proactive_chat_attempt_id", attempt_id)
        setattr(event, "_private_companion_proactive_chat_token", token)
        setattr(event, "_private_companion_proactive_full_text", source_text)
        setattr(event, "_private_companion_proactive_segment_index", segment_index)
        setattr(event, "_private_companion_proactive_segment_count", segment_count)
        if segment_count > 1:
            setattr(event, "_private_companion_external_presegmented", True)
        review = await self._review_proactive_chat_bridge_message(
            session_id,
            source_text,
            token=token,
            attempt_id=attempt_id,
        )
        if not review.get("ok") or not review.get("text"):
            event.set_result(self._build_result_from_chain([]))
            event.stop_event()
            if token:
                await self._cancel_proactive_chat_bridge(session_id, token=token)
            logger.info(
                "[PrivateCompanion] 已拦截 Proactive Chat 主动候选: session=%s decision=%s reason=%s",
                _single_line(session_id, 120),
                _single_line(review.get("decision"), 24) or "drop",
                _single_line(review.get("reason"), 160),
            )
            return
        replacement = str(review["text"])
        should_replace_full_attempt = replacement != source_text and (
            any(isinstance(component, Record) for component in chain)
            or segment_count > 1
        )
        if should_replace_full_attempt:
            event.set_result(self._build_result_from_chain([Plain(replacement)]))
            source_text = replacement
            setattr(event, "_private_companion_proactive_full_text", replacement)
            if attempt_id:
                replaced_attempts[attempt_id] = now
        elif replacement != source_text:
            rebuilt: list[Any] = []
            replaced = False
            for component in chain:
                if isinstance(component, Plain):
                    if not replaced:
                        rebuilt.append(Plain(replacement))
                        replaced = True
                    continue
                rebuilt.append(component)
            if replaced:
                event.set_result(self._build_result_from_chain(rebuilt))
                source_text = replacement
                setattr(event, "_private_companion_proactive_full_text", replacement)
        if bool(bridge_context.get("tts_sent")) and not should_replace_full_attempt:
            setattr(event, "_private_companion_skip_tts_enhancement", "proactive_chat_prebuilt_tts")
        logger.info(
            "[PrivateCompanion] 已接入 Proactive Chat 发送前链路: session=%s segment=%s/%s upstream_tts=%s text=%s",
            _single_line(session_id, 120),
            segment_index + 1,
            segment_count,
            bool(bridge_context.get("tts_sent")),
            _single_line(source_text, 160),
        )

    @filter.on_decorating_result(priority=20000)
    @_multi_persona_event_context
    async def consume_group_member_safety_hidden_marker(self, event: AstrMessageEvent, *args, **kwargs):
        """Consume the reply model's internal member-risk decision before any outbound transform."""
        if self is None:
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        plain_components = [component for component in chain if isinstance(component, Plain)]
        if not plain_components:
            return

        combined_original = "".join(str(getattr(component, "text", "") or "") for component in plain_components)
        combined_cleaned, combined_decisions = self._extract_group_member_safety_hidden_markers(combined_original)
        rebuilt: list[Any] = []
        per_component_cleaned: list[str] = []
        per_component_decisions: list[dict[str, Any]] = []
        changed = False
        for component in plain_components:
            original = str(getattr(component, "text", "") or "")
            cleaned, decisions = self._extract_group_member_safety_hidden_markers(original)
            per_component_cleaned.append(cleaned)
            per_component_decisions.extend(decisions)
            changed = changed or cleaned != original

        cross_component_marker = "".join(per_component_cleaned) != combined_cleaned
        first_plain_written = False
        plain_index = 0
        for component in chain:
            if not isinstance(component, Plain):
                rebuilt.append(component)
                continue
            if cross_component_marker:
                if not first_plain_written and combined_cleaned:
                    rebuilt.append(Plain(combined_cleaned))
                    first_plain_written = True
                changed = True
            else:
                cleaned = per_component_cleaned[plain_index]
                if cleaned:
                    rebuilt.append(Plain(cleaned) if cleaned != str(getattr(component, "text", "") or "") else component)
                plain_index += 1
        if changed:
            try:
                result.chain = rebuilt
            except Exception:
                event.set_result(self._build_result_from_chain(rebuilt))

        decisions = combined_decisions if combined_decisions else per_component_decisions
        if not decisions:
            return
        if (
            self._group_member_safety_hidden_marker_mode() == "disabled"
            or not bool(getattr(event, "_private_companion_member_safety_hidden_marker_expected", False))
        ):
            logger.warning(
                "[PrivateCompanion] 已清理未授权的群成员风控隐性标签，未计数: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
            return
        if bool(getattr(event, "_private_companion_member_safety_hidden_marker_consumed", False)):
            return
        setattr(event, "_private_companion_member_safety_hidden_marker_consumed", True)
        decision = max(
            decisions,
            key=lambda item: (_safe_float(item.get("confidence"), 0.0), _safe_int(item.get("severity"), 1, 1, 3)),
        )
        group_id = _single_line(getattr(event, "_private_companion_member_safety_group_id", ""), 128)
        sender_id = _single_line(getattr(event, "_private_companion_member_safety_sender_id", ""), 128)
        sender_name = _single_line(getattr(event, "_private_companion_member_safety_sender_name", ""), 60)
        source_text = str(getattr(event, "_private_companion_member_safety_message_text", "") or "")
        if not group_id or not sender_id:
            return
        recorded = await self._record_group_member_safety_decision(
            event,
            group_id=group_id,
            sender_id=sender_id,
            sender_name=sender_name,
            text=source_text,
            decision=decision,
            source="reply_hidden_marker",
        )
        logger.info(
            "[PrivateCompanion] 已消费群成员风控隐性标签: group=%s sender=%s counted=%s blocked=%s reason=%s",
            group_id,
            sender_id,
            bool(recorded.get("counted")),
            bool(recorded.get("blocked")),
            _single_line(recorded.get("reason"), 80),
        )

    @filter.on_decorating_result(priority=-18000)
    @_multi_persona_event_context
    async def attach_reaction_expression_image_before_send(
        self, event: AstrMessageEvent, *args, **kwargs
    ):
        """Prepare a local reaction image without weakening the text reply."""
        if self is None or not self.enabled:
            return
        if bool(getattr(event, "_private_companion_skip_reaction_expression", False)):
            for attr in (
                "_private_companion_reaction_expression_intent",
                "_private_companion_deferred_reaction_tts",
            ):
                try:
                    delattr(event, attr)
                except (AttributeError, TypeError):
                    pass
            logger.debug(
                "[PrivateCompanion] 本轮已有真实生图，跳过追加表情附件: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
            return
        intent = getattr(
            event, "_private_companion_reaction_expression_intent", None
        )
        if not isinstance(intent, dict) or not intent:
            return
        tracker_installer = getattr(
            self,
            "_install_reaction_expression_delivery_tracker",
            None,
        )
        if callable(tracker_installer):
            # TTS may already be deferred even when lookup later misses. Track the
            # primary reply before any attachment-only early return.
            tracker_installer(event, {})
        if bool(
            getattr(
                event,
                "_private_companion_reaction_expression_attachment_attempted",
                False,
            )
        ):
            return
        setattr(
            event,
            "_private_companion_reaction_expression_attachment_attempted",
            True,
        )

        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        visible_text = "".join(
            str(getattr(component, "text", "") or "")
            for component in chain
            if isinstance(component, Plain)
        ).strip()
        if not self._reaction_expression_has_visible_text(visible_text):
            self._note_reaction_expression_runtime(
                skipped=1, last_reason="missing_visible_text"
            )
            self._log_reaction_expression_event(
                event,
                stage="attachment",
                decision="skip",
                reason="missing_visible_text",
                scope=self._reaction_expression_scope(event),
                found=False,
                sent=False,
            )
            return
        if any(isinstance(component, Image) for component in chain):
            self._note_reaction_expression_runtime(
                skipped=1, last_reason="existing_image"
            )
            self._log_reaction_expression_event(
                event,
                stage="attachment",
                decision="skip",
                reason="existing_image",
                scope=self._reaction_expression_scope(event),
                found=False,
                sent=False,
            )
            return

        context_text = _single_line(intent.get("context"), 1000) or _single_line(
            visible_text, 700
        )
        raw_prepared = await self._pc_reaction_expression_impl(
            event,
            query=_single_line(intent.get("provider_query"), 500),
            context=context_text,
            meme_only=True,
            send=True,
            purpose=_single_line(intent.get("purpose"), 120),
            emotion=_single_line(intent.get("emotion"), 80),
            intensity=_safe_int(intent.get("intensity"), 0, 0, 5),
            candidate_queries=intent.get("candidate_queries", []),
            attach_only=True,
        )
        try:
            prepared = json.loads(raw_prepared)
        except (TypeError, ValueError, json.JSONDecodeError):
            prepared = {}
        if not isinstance(prepared, dict) or prepared.get("decision") != "attach":
            return

        pending = getattr(
            event,
            "_private_companion_reaction_expression_pending_attachment",
            None,
        )
        image_path = _path_text(prepared.get("path"), 1000)
        if not isinstance(pending, dict) or not image_path or not os.path.isfile(
            image_path
        ):
            if isinstance(pending, dict):
                await self._settle_reaction_expression_attachment_data(
                    pending,
                    sent=False,
                    reason="attachment_file_missing",
                )
            return
        try:
            builder = getattr(self, "_build_reaction_image_component", None)
            if callable(builder):
                image_component = builder(event, image_path)
            else:
                try:
                    image_component = Image.fromFileSystem(image_path)
                except AttributeError:
                    image_component = Image.from_file_system(image_path)
                try:
                    object.__setattr__(
                        image_component,
                        "_private_companion_reaction_expression",
                        True,
                    )
                except Exception:
                    pass
        except Exception as exc:
            await self._settle_reaction_expression_attachment_data(
                pending,
                sent=False,
                reason="attachment_component_failed",
            )
            logger.warning(
                "[PrivateCompanion] 表情图片附件构建失败: error_type=%s",
                type(exc).__name__,
            )
            return
        delivery_mode = self._reaction_expression_delivery_mode()
        pending["delivery_mode"] = delivery_mode
        pending["component"] = image_component
        pending["delivery_started"] = False
        self._install_reaction_expression_delivery_tracker(event, pending)
        if delivery_mode == "same_message":
            chain.append(image_component)
            try:
                result.chain = chain
            except Exception:
                event.set_result(self._build_result_from_chain(chain))
            pending["attached"] = True
        elif delivery_mode == "separate_before":
            pending["delivery_started"] = True
            sent = await self._send_reaction_expression_component_separately(
                event,
                image_component,
            )
            await self._settle_reaction_expression_attachment_data(
                pending,
                sent=sent,
                reason="delivered" if sent else "delivery_failed",
            )
        self._log_reaction_expression_event(
            event,
            stage="attachment",
            decision="accepted",
            reason=(
                "attachment_appended"
                if delivery_mode == "same_message"
                else "delivered_before_primary"
                if delivery_mode == "separate_before" and pending.get("sent")
                else "delivery_failed"
                if delivery_mode == "separate_before"
                else "attachment_prepared"
            ),
            scope=self._reaction_expression_scope(event),
            found=True,
            sent=bool(pending.get("sent")),
            image_id=prepared.get("image_id"),
            confidence=prepared.get("confidence"),
            cache_hit=prepared.get("cache_hit"),
            latency_ms=prepared.get("lookup_latency_ms"),
            match_basis=pending.get("match_basis"),
        )

    def _reaction_expression_delivery_mode(self) -> str:
        raw_mode = _single_line(
            runtime_persona_setting(self, 'reaction_expression_delivery_mode', "separate_after"),
            32,
        )
        normalizer = getattr(
            self,
            "_normalize_reaction_expression_delivery_mode",
            None,
        )
        if callable(normalizer):
            try:
                return normalizer(raw_mode)
            except Exception:
                pass
        mode = raw_mode.lower()
        if mode not in {"separate_after", "same_message", "separate_before"}:
            return "separate_after"
        return mode

    def _reaction_expression_image_format(self) -> str:
        image_format = _single_line(
            runtime_persona_setting(self, 'reaction_expression_image_format', "image"),
            24,
        ).lower()
        return image_format if image_format in {"image", "qq_emoji"} else "image"

    @staticmethod
    def _is_reaction_image_component(component: Any) -> bool:
        return isinstance(component, Image) or bool(
            getattr(component, "_private_companion_reaction_expression", False)
            and callable(getattr(component, "toDict", None))
        )

    def _build_reaction_image_component(
        self,
        event: AstrMessageEvent | None,
        image_path: str,
    ) -> Any:
        format_getter = getattr(self, "_reaction_expression_image_format", None)
        image_format = (
            format_getter()
            if callable(format_getter)
            else _single_line(
                runtime_persona_setting(self, 'reaction_expression_image_format', "image"),
                24,
            ).lower()
        )
        platform_getter = getattr(self, "_platform_kind_for_event", None)
        platform_kind = (
            platform_getter(event)
            if image_format == "qq_emoji" and callable(platform_getter)
            else "generic"
        )
        if image_format == "qq_emoji" and platform_kind == "onebot":
            try:
                return _OneBotReactionImage(image_path)
            except Exception as exc:
                logger.warning(
                    "[PrivateCompanion] QQ表情格式组件构建失败,回退普通图片: error_type=%s",
                    type(exc).__name__,
                )
        try:
            component = Image.fromFileSystem(image_path)
        except AttributeError:
            component = Image.from_file_system(image_path)
        try:
            object.__setattr__(
                component,
                "_private_companion_reaction_expression",
                True,
            )
        except Exception:
            pass
        return component

    async def _send_reaction_expression_component_separately(
        self,
        event: AstrMessageEvent,
        component: Any,
    ) -> bool:
        sender = getattr(event, "send", None)
        result_builder = getattr(event, "chain_result", None)
        if not callable(sender) or not callable(result_builder) or component is None:
            return False
        try:
            send_result = await sender(result_builder([component]))
            return send_result is not False
        except Exception as exc:
            logger.warning(
                "[PrivateCompanion] 表情图片单独投递失败: mode=%s error_type=%s",
                self._reaction_expression_delivery_mode(),
                type(exc).__name__,
            )
            return False

    @staticmethod
    def _reaction_expression_flatten_delivery_components(
        components: Any,
    ) -> list[Any]:
        flattened: list[Any] = []

        def visit(component: Any) -> None:
            if component is None:
                return
            class_name = component.__class__.__name__.strip().lower()
            nested = getattr(component, "content", None)
            if class_name == "node" and isinstance(nested, (list, tuple)):
                for item in nested:
                    visit(item)
                return
            nodes = getattr(component, "nodes", None)
            if class_name == "nodes" and isinstance(nodes, (list, tuple)):
                for item in nodes:
                    visit(item)
                return
            flattened.append(component)

        raw_components = getattr(components, "chain", components)
        if isinstance(raw_components, (list, tuple)):
            for item in raw_components:
                visit(item)
        return flattened

    @staticmethod
    def _reaction_expression_delivery_signature(component: Any) -> tuple[str, ...] | None:
        if isinstance(component, Plain):
            text = str(getattr(component, "text", "") or "").strip()
            return ("plain", text) if text else None
        if isinstance(component, Record):
            reference = _single_line(
                getattr(component, "file", "")
                or getattr(component, "url", ""),
                1000,
            )
            return (
                "record",
                reference,
                _single_line(getattr(component, "text", ""), 1000),
            )
        if PrivateCompanionPlugin._is_reaction_image_component(component):
            reference = _single_line(
                getattr(component, "file", "")
                or getattr(component, "url", "")
                or getattr(component, "path", ""),
                1000,
            )
            if reference and not reference.startswith(("http://", "https://")):
                reference = os.path.normcase(os.path.normpath(reference))
            return ("image", reference) if reference else None
        return None

    def _install_reaction_expression_delivery_tracker(
        self,
        event: AstrMessageEvent,
        pending: dict[str, Any],
    ) -> None:
        existing = getattr(
            event,
            "_private_companion_reaction_expression_delivery_tracker",
            None,
        )
        if isinstance(existing, dict):
            if not existing.get("restored"):
                pending["delivery_tracker"] = existing
                return
            try:
                delattr(
                    event,
                    "_private_companion_reaction_expression_delivery_tracker",
                )
            except Exception:
                pass
        original_send = getattr(event, "send", None)
        if not callable(original_send):
            return
        tracker: dict[str, Any] = {
            "original_send": original_send,
            "successful_signatures": [],
            "restored": False,
        }

        async def tracked_send(message: Any) -> Any:
            result = await original_send(message)
            if result is False:
                return result
            signatures = tracker.get("successful_signatures")
            if isinstance(signatures, list):
                for item in self._reaction_expression_flatten_delivery_components(
                    message
                ):
                    signature = self._reaction_expression_delivery_signature(item)
                    if signature is not None:
                        signatures.append(signature)
            return result

        tracker["tracked_send"] = tracked_send
        try:
            setattr(event, "send", tracked_send)
            setattr(
                event,
                "_private_companion_reaction_expression_delivery_tracker",
                tracker,
            )
            pending["delivery_tracker"] = tracker
        except Exception:
            return

    def _reaction_expression_primary_reply_confirmed(
        self,
        event: AstrMessageEvent,
        pending: dict[str, Any] | None = None,
        *,
        require_segmented_complete: bool = False,
    ) -> bool:
        tracker = (
            pending.get("delivery_tracker")
            if isinstance(pending, dict)
            else None
        )
        if not isinstance(tracker, dict):
            tracker = getattr(
                event,
                "_private_companion_reaction_expression_delivery_tracker",
                None,
            )
        if not isinstance(tracker, dict):
            return bool(getattr(event, "_has_send_oper", False))
        successful = list(tracker.get("successful_signatures") or [])
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        expected_sources: list[Any] = [chain]
        if require_segmented_complete:
            segmented_chunks = getattr(
                event,
                "_private_companion_reaction_expression_expected_primary_chunks",
                None,
            )
            if isinstance(segmented_chunks, list) and segmented_chunks:
                expected_sources = segmented_chunks
        expected: list[tuple[str, ...]] = []
        for source in expected_sources:
            for item in self._reaction_expression_flatten_delivery_components(source):
                signature = self._reaction_expression_delivery_signature(item)
                if signature is None or signature[0] == "image":
                    continue
                expected.append(signature)
        if not expected:
            return False
        for signature in expected:
            try:
                successful.remove(signature)
            except ValueError:
                return False
        return True

    def _reaction_expression_image_delivery_confirmed(
        self,
        event: AstrMessageEvent,
        pending: dict[str, Any],
    ) -> bool:
        tracker = pending.get("delivery_tracker")
        component = pending.get("component")
        signature = self._reaction_expression_delivery_signature(component)
        if not isinstance(tracker, dict) or signature is None:
            return False
        return signature in list(tracker.get("successful_signatures") or [])

    @staticmethod
    def _restore_reaction_expression_delivery_tracker(event: AstrMessageEvent) -> None:
        tracker = getattr(
            event,
            "_private_companion_reaction_expression_delivery_tracker",
            None,
        )
        if not isinstance(tracker, dict) or tracker.get("restored"):
            return
        tracker["restored"] = True
        original_send = tracker.get("original_send")
        if callable(original_send):
            try:
                setattr(event, "send", original_send)
            except Exception:
                pass
        try:
            delattr(
                event,
                "_private_companion_reaction_expression_delivery_tracker",
            )
        except Exception:
            try:
                setattr(
                    event,
                    "_private_companion_reaction_expression_delivery_tracker",
                    None,
                )
            except Exception:
                pass

    @staticmethod
    def _reaction_expression_attachment_present(
        chain: list[Any],
        component: Any,
        pending: dict[str, Any],
    ) -> bool:
        if component is None:
            return False
        flattened = PrivateCompanionPlugin._reaction_expression_flatten_delivery_components(
            chain
        )
        if any(item is component for item in flattened):
            return True
        expected_path = os.path.normcase(
            os.path.normpath(_path_text(pending.get("image_path"), 1000))
        )
        if not expected_path:
            return False
        for item in flattened:
            if not PrivateCompanionPlugin._is_reaction_image_component(item):
                continue
            for attr in ("file", "path", "url"):
                raw_value = _path_text(getattr(item, attr, ""), 1000)
                if not raw_value or raw_value.startswith(("http://", "https://")):
                    continue
                if os.path.normcase(os.path.normpath(raw_value)) == expected_path:
                    return True
        return False

    @filter.on_decorating_result(priority=-20000)
    @_multi_persona_event_context
    async def finalize_proactive_chat_outbound_bridge(self, event: AstrMessageEvent, *args, **kwargs):
        """在所有装饰器结束后，仅为仍有实际发送内容的 Proactive Chat 链同步状态。"""
        if self is None or not self.enabled or not runtime_persona_setting(self, 'enable_proactive_chat_integration', True):
            return
        if str(getattr(event, "_private_companion_external_proactive_source", "") or "") != "proactive_chat":
            return
        session_id = str(getattr(event, "unified_msg_origin", "") or "")
        token = _single_line(getattr(event, "_private_companion_proactive_chat_token", ""), 80)
        attempt_id = _single_line(getattr(event, "_private_companion_proactive_chat_attempt_id", ""), 100)
        runtime_bridge = getattr(self, "_proactive_chat_runtime_bridge", None)
        if runtime_bridge is not None and runtime_bridge.owns_outbound(session_id, attempt_id):
            # 深度桥接会在平台 send_by_session/context.send_message 无异常返回后统一结算。
            # 此处仍处于发送前装饰阶段，不能把非空消息链提前当成已送达。
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            if token:
                await self._cancel_proactive_chat_bridge(session_id, token=token)
            return
        source_text = str(getattr(event, "_private_companion_proactive_full_text", "") or "").strip()
        if not source_text:
            return
        recorded = await self._record_proactive_chat_bridge_sent(
            session_id,
            source_text,
            token=token,
            attempt_id=attempt_id,
        )
        if recorded.get("recorded"):
            logger.info(
                "[PrivateCompanion] 已同步 Proactive Chat 最终发送状态: session=%s text=%s",
                _single_line(session_id, 120),
                _single_line(source_text, 160),
            )

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def stop_passive_input_status_before_private_send(self, event: AstrMessageEvent, *args, **kwargs):
        """LLM 回复进入发送前阶段时停止私聊持续输入状态。"""
        if self is None or not self.enabled:
            return
        if bool(getattr(event, "is_private_chat", lambda: False)()):
            self._stop_passive_input_status_loop(event)

    @filter.on_decorating_result(priority=-10000)
    @_multi_persona_event_context
    async def suppress_recent_duplicate_outbound_text(self, event: AstrMessageEvent, *args, **kwargs):
        """Last-mile idempotency guard for adapter echoes and concurrent reply chains."""
        if self is None or not self.enabled:
            return
        candidate = self._outbound_text_duplicate_candidate(event)
        if not candidate:
            return
        duplicate_state = self._reserve_outbound_text_candidate(candidate)
        if not duplicate_state:
            setattr(event, "_private_companion_outbound_text_candidate", candidate)
            return
        logger.warning(
            "[PrivateCompanion] 发送前拦截短时间重复正文: scope=%s sender=%s previous=%s text=%s",
            candidate.get("scope") or "unknown",
            candidate.get("sender_id") or "-",
            duplicate_state,
            _single_line(candidate.get("text"), 120),
        )
        empty_result = self._build_result_from_chain([])
        try:
            empty_result.stop_event()
        except Exception:
            pass
        event.set_result(empty_result)
        event.stop_event()

    @filter.after_message_sent(priority=8500)
    @_multi_persona_event_context
    async def remember_confirmed_outbound_text(self, event: AstrMessageEvent, *args, **kwargs):
        """Confirm only candidates for which the platform send operation ran."""
        if self is None or not self.enabled:
            return
        if not self._reaction_expression_primary_reply_confirmed(
            event,
            require_segmented_complete=True,
        ):
            return
        candidate = getattr(event, "_private_companion_outbound_text_candidate", None)
        if isinstance(candidate, dict):
            self._confirm_outbound_text_candidate(candidate)

    @filter.after_message_sent(priority=9000)
    @_multi_persona_event_context
    async def settle_reaction_expression_attachment_after_send(
        self, event: AstrMessageEvent, *args, **kwargs
    ):
        """Deliver or settle a reaction only after the primary reply is confirmed."""
        if self is None or not self.enabled:
            return
        pending = getattr(
            event,
            "_private_companion_reaction_expression_pending_attachment",
            None,
        )
        if not isinstance(pending, dict) or pending.get("settled"):
            return
        delivery_mode = _single_line(
            pending.get("delivery_mode"),
            32,
        ).lower() or self._reaction_expression_delivery_mode()
        if delivery_mode not in {"separate_after", "same_message", "separate_before"}:
            delivery_mode = "separate_after"
        if delivery_mode == "separate_before":
            await self._settle_reaction_expression_attachment_data(
                pending,
                sent=False,
                reason="delivery_not_started",
            )
            return

        component = pending.get("component")
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        primary_sent = self._reaction_expression_primary_reply_confirmed(
            event,
            pending,
            require_segmented_complete=True,
        )
        if delivery_mode == "separate_after":
            if pending.get("delivery_started"):
                return
            pending["delivery_started"] = True
            if not primary_sent:
                await self._settle_reaction_expression_attachment_data(
                    pending,
                    sent=False,
                    reason="primary_not_delivered",
                )
                return
            sent = await self._send_reaction_expression_component_separately(
                event,
                component,
            )
            await self._settle_reaction_expression_attachment_data(
                pending,
                sent=sent,
                reason="delivered" if sent else "delivery_failed",
            )
            return

        attachment_present = self._reaction_expression_attachment_present(
            chain,
            component,
            pending,
        )
        tracker = pending.get("delivery_tracker")
        if isinstance(tracker, dict):
            sent = self._reaction_expression_image_delivery_confirmed(
                event,
                pending,
            )
        else:
            sent = primary_sent and attachment_present
        reason = (
            "delivered"
            if sent
            else "delivery_failed"
            if primary_sent and attachment_present
            else "attachment_removed"
            if not attachment_present
            else "platform_not_sent"
        )
        await self._settle_reaction_expression_attachment_data(
            pending,
            sent=sent,
            reason=reason,
        )

    @filter.after_message_sent(priority=9500)
    @_multi_persona_event_context
    async def release_reaction_expression_segmented_remainder_after_send(
        self, event: AstrMessageEvent, *args, **kwargs
    ):
        """Finish all text bubbles before a separate-after reaction is released."""
        if self is None or not self.enabled:
            return
        pending = getattr(
            event,
            "_private_companion_reaction_expression_segmented_remainder",
            None,
        )
        if not isinstance(pending, dict) or pending.get("started"):
            return
        chunks = pending.get("chunks")
        if not isinstance(chunks, list) or not chunks:
            return
        if not self._reaction_expression_primary_reply_confirmed(event):
            return
        pending["started"] = True
        try:
            await self._send_segmented_llm_chain_remainder(
                event,
                chunks,
                previous_segment=_single_line(pending.get("previous_segment"), 500),
                source="reaction_expression",
                started_at=_safe_float(pending.get("started_at"), time.time(), 0.0),
            )
            pending["completed"] = self._reaction_expression_primary_reply_confirmed(
                event,
                require_segmented_complete=True,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            pending["completed"] = False
            logger.warning(
                "[PrivateCompanion] 表情正文分段补发失败: session=%s error=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120)
                or "unknown",
                _single_line(exc, 160),
            )

    @filter.after_message_sent(priority=8000)
    @_multi_persona_event_context
    async def release_tts_reply_remainder_after_send(
        self, event: AstrMessageEvent, *args, **kwargs
    ):
        """Start delayed TTS chunks only after the platform accepted the first chunk."""
        if self is None or not self.enabled:
            return
        pending = getattr(event, "_private_companion_tts_reply_remainder", None)
        if not isinstance(pending, dict):
            return
        try:
            delattr(event, "_private_companion_tts_reply_remainder")
        except Exception:
            setattr(event, "_private_companion_tts_reply_remainder", None)
        if not self._reaction_expression_primary_reply_confirmed(event):
            return
        chunks = pending.get("chunks")
        if not isinstance(chunks, list) or not chunks:
            return
        operation = self._send_tts_chain_chunks_after_first(
            event,
            chunks,
            started_at=_safe_float(pending.get("started_at"), time.time(), 0.0),
            primary_delivery_confirmed=True,
        )
        self._create_lifecycle_background_task(
            operation,
            label="tts_reply_remainder",
        )

    @filter.after_message_sent(priority=7000)
    @_multi_persona_event_context
    async def release_deferred_reaction_tts_after_send(
        self, event: AstrMessageEvent, *args, **kwargs
    ):
        """Generate optional reaction voice only after text and image delivery settle."""
        if self is None or not self.enabled:
            return
        pending = getattr(
            event,
            "_private_companion_deferred_reaction_tts",
            None,
        )
        if not isinstance(pending, dict):
            return
        try:
            delattr(event, "_private_companion_deferred_reaction_tts")
        except Exception:
            setattr(event, "_private_companion_deferred_reaction_tts", None)
        if not self._reaction_expression_primary_reply_confirmed(
            event,
            require_segmented_complete=True,
        ):
            return
        operation = self._send_deferred_reaction_tts(event, pending)
        self._create_lifecycle_background_task(
            operation,
            label="reaction_tts_after_delivery",
        )

    @filter.after_message_sent(priority=6000)
    @_multi_persona_event_context
    async def cleanup_reaction_expression_delivery_tracker_after_send(
        self, event: AstrMessageEvent, *args, **kwargs
    ):
        """Restore the adapter send method after all ordered follow-ups are released."""
        if self is None:
            return
        self._restore_reaction_expression_delivery_tracker(event)
        for attr_name in (
            "_private_companion_reaction_expression_segmented_remainder",
            "_private_companion_reaction_expression_expected_primary_chunks",
        ):
            try:
                delattr(event, attr_name)
            except Exception:
                pass

    @filter.on_agent_begin(priority=100000)
    @_multi_persona_event_context
    async def begin_final_response_persistence(
        self,
        event: AstrMessageEvent,
        run_context: Any,
        *args,
        **kwargs,
    ):
        """Defer optional memory sinks until the platform confirms delivery."""
        if self is None or not self.enabled or event is None:
            return
        self._begin_final_response_persistence(event)

    @filter.on_agent_done(priority=-100000)
    @_multi_persona_event_context
    async def prepare_final_response_persistence(
        self,
        event: AstrMessageEvent,
        run_context: Any,
        response: Any,
        *args,
        **kwargs,
    ):
        if self is None or event is None:
            return
        await self._prepare_final_response_after_agent(event, run_context, response)

    @filter.on_agent_done(priority=1000000)
    @_multi_persona_event_context
    async def restore_intercepted_astrbot_group_history(
        self,
        event: AstrMessageEvent,
        run_context: Any,
        response: Any,
        *args,
        **kwargs,
    ):
        """Restore provider-hidden AstrBot history before the core persists it."""
        if self is None or event is None:
            return
        result = restore_astrbot_group_history(event, run_context)
        if result.get("failed"):
            logger.error(
                "[PrivateCompanion] AstrBot 群聊历史恢复失败，已阻止本轮覆盖旧会话: session=%s reason=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 140) or "unknown",
                _single_line(result.get("reason"), 80) or "unknown",
            )
        elif result.get("restored"):
            logger.debug(
                "[PrivateCompanion] 已在核心保存前恢复 AstrBot 群聊历史: session=%s messages=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 140) or "unknown",
                result.get("history_messages", 0),
            )

    @filter.on_decorating_result(priority=-30000)
    @_multi_persona_event_context
    async def capture_final_outbound_chain_for_persistence(
        self,
        event: AstrMessageEvent,
        *args,
        **kwargs,
    ):
        if self is None or event is None:
            return
        self._capture_final_outbound_delivery(event)

    @filter.after_message_sent(priority=-100000)
    @_multi_persona_event_context
    async def persist_confirmed_passive_reply(
        self,
        event: AstrMessageEvent,
        *args,
        **kwargs,
    ):
        if self is None or event is None:
            return
        await self._persist_final_outbound_delivery(event)

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def suppress_group_llm_reply_block_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """群级 LLM 熔断的发送前兜底。"""
        if self is None or not self.enabled:
            return
        self._stop_group_llm_reply_if_blocked(event, source="decorating_result")

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def strip_outbound_control_blocks_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """发送前兜底清理内部控制块，避免 timer/TTSBLOCK 泄漏到聊天。"""
        if self is None or not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "llm_request"):
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            return
        changed = False
        protected_tts_tokens = getattr(event, "_private_companion_tts_block_tokens", None)
        preserve_private_tts_tokens = (
            bool(runtime_persona_setting(self, 'enable_tts_enhancement', False))
            and isinstance(protected_tts_tokens, dict)
            and bool(protected_tts_tokens)
        )
        for comp in chain:
            if not isinstance(comp, Plain):
                continue
            original = str(getattr(comp, "text", "") or "")
            cleaned = _strip_outbound_control_blocks(
                original,
                preserve_private_tts_tokens=preserve_private_tts_tokens,
                allowed_private_tts_tokens=set(protected_tts_tokens.keys()) if isinstance(protected_tts_tokens, dict) else None,
            )
            if not bool(runtime_persona_setting(self, 'enable_tts_enhancement', False)):
                cleaned = re.sub(r"</?t{2,}s\b[^>]*>", "", cleaned, flags=re.IGNORECASE).strip()
            cleaned = sanitize_llm_segment_control_tokens(cleaned)
            if cleaned != original:
                changed = True
                try:
                    comp.text = cleaned
                except Exception:
                    pass
        if changed:
            logger.warning(
                "[PrivateCompanion] 发送前已清理内部控制标签: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )

    @filter.on_decorating_result(priority=-29999)
    @_multi_persona_event_context
    async def final_strip_outbound_control_blocks_before_send(
        self, event: AstrMessageEvent, *args, **kwargs
    ):
        """最后一环清理，防止后续 TTS/分段钩子重新带出内部标签。"""
        if self is None or not bool(getattr(self, "enabled", False)):
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            return
        protected_tts_tokens = getattr(event, "_private_companion_tts_block_tokens", None)
        preserve_private_tts_tokens = (
            bool(runtime_persona_setting(self, 'enable_tts_enhancement', False))
            and isinstance(protected_tts_tokens, dict)
            and bool(protected_tts_tokens)
        )
        cleaned_chain: list[Any] = []
        changed = False
        for component in chain:
            if not isinstance(component, Plain):
                cleaned_chain.append(component)
                continue
            original = str(getattr(component, "text", "") or "")
            cleaned = _strip_outbound_control_blocks(
                original,
                preserve_private_tts_tokens=preserve_private_tts_tokens,
                allowed_private_tts_tokens=(
                    set(protected_tts_tokens.keys())
                    if isinstance(protected_tts_tokens, dict)
                    else None
                ),
            )
            if not bool(runtime_persona_setting(self, 'enable_tts_enhancement', False)):
                cleaned = re.sub(r"</?t{2,}s\b[^>]*>", "", cleaned, flags=re.IGNORECASE).strip()
            cleaned = sanitize_llm_segment_control_tokens(cleaned)
            if cleaned:
                cleaned_chain.append(Plain(cleaned) if cleaned != original else component)
            if cleaned != original:
                changed = True
        if not changed:
            return
        try:
            result.chain = cleaned_chain
        except Exception:
            event.set_result(self._build_result_from_chain(cleaned_chain))
        logger.warning(
            "[PrivateCompanion] 最终发送前已清理内部控制标签: session=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
        )

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def strip_plaintext_tool_calls_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """阻止兼容模型把工具调用 JSON 当普通聊天正文发送。"""
        if self is None or not self.enabled:
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            return
        changed = False
        leaked_names: list[str] = []
        cleaned_chain: list[Any] = []
        for comp in chain:
            if not isinstance(comp, Plain):
                cleaned_chain.append(comp)
                continue
            original = str(getattr(comp, "text", "") or "")
            cleaned, calls = self._strip_plaintext_tool_call_envelopes(original)
            if not calls:
                cleaned_chain.append(comp)
                continue
            changed = True
            leaked_names.extend(str(item.get("name") or "") for item in calls)
            if cleaned:
                try:
                    comp.text = cleaned
                    cleaned_chain.append(comp)
                except Exception:
                    cleaned_chain.append(Plain(cleaned))
        if not changed:
            return
        try:
            result.chain = cleaned_chain
        except Exception:
            event.set_result(self._build_result_from_chain(cleaned_chain))
        logger.warning(
            "[PrivateCompanion] 发送前终检已移除明文工具调用: session=%s tools=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            ",".join(leaked_names),
        )

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def cancel_reply_if_trigger_recalled_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """若触发/唤醒消息在回复发出前被撤回，则静默取消本次回复。"""
        if self is None or not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "enable_recall_enhancement"):
            return
        recalled_message_id = await self._should_cancel_reply_for_missing_or_recalled_trigger(event)
        if not recalled_message_id:
            return
        logger.info(
            "[PrivateCompanion] 触发消息已撤回或发送前不可见，取消本次发送: session=%s message_id=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            recalled_message_id,
        )
        self._record_passive_no_reply(
            event,
            source="撤回取消",
            reason="触发消息已撤回或发送前不可见",
            detail=str(recalled_message_id),
            level="info",
        )
        empty_result = self._build_result_from_chain([])
        try:
            empty_result.stop_event()
        except Exception:
            pass
        event.set_result(empty_result)
        event.stop_event()

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def suppress_forbidden_outbound_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """自己的待发送消息命中违禁词时，优先在发送前拦截。"""
        if self is None or not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "enable_recall_enhancement"):
            return
        if not runtime_persona_setting(self, 'enable_recall_enhancement', True) or not runtime_persona_setting(self, 'enable_forbidden_word_recall', False):
            return
        if not self._forbidden_recall_words():
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            return
        text = self._chain_text_for_forbidden_recall(chain)
        hit = self._forbidden_recall_hit(text)
        if not hit:
            return
        logger.warning(
            "[PrivateCompanion] 待发送消息命中违禁词，已拦截发送: word=%s session=%s",
            _single_line(hit, 40),
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
        )
        self._record_passive_no_reply(
            event,
            source="发送前拦截",
            reason="待发送消息命中屏蔽词",
            detail=hit,
            reply_preview=text,
            level="warn",
        )
        empty_result = self._build_result_from_chain([])
        try:
            empty_result.stop_event()
        except Exception:
            pass
        event.set_result(empty_result)
        event.stop_event()

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def suppress_framework_error_leak_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """避免 AstrBot/Core 的技术错误和工具循环摘要直接发进聊天。"""
        if self is None or not self.enabled:
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain or any(not isinstance(comp, Plain) for comp in chain):
            return
        if self._restore_response_review_meta_leak_before_send(event, chain):
            return
        text = "\n".join(str(getattr(comp, "text", "") or "") for comp in chain).strip()
        compact = text.lower()
        if re.fullmatch(
            r"(?:\[\s*astrbot_plugin_private_companion(?:\.[A-Za-z_][\w]*)+:\d+\s*\]\s*)+",
            text,
            flags=re.IGNORECASE,
        ):
            logger.warning(
                "[PrivateCompanion] 已拦截插件日志来源位置外发: session=%s text=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                _single_line(text, 180),
            )
            self._record_passive_no_reply(
                event,
                source="发送前拦截",
                reason="插件日志来源位置泄漏",
                reply_preview=text,
                level="warn",
            )
            empty_result = self._build_result_from_chain([])
            try:
                empty_result.stop_event()
            except Exception:
                pass
            event.set_result(empty_result)
            event.stop_event()
            return
        receipt_compact = re.sub(r"[\s。.!！?？,，；;:：]+", "", text)
        status_receipt_like = (
            len(receipt_compact) <= 28
            and any(token in receipt_compact for token in ("发送给用户", "发给用户", "发送给对方", "发给对方", "发出去了"))
            and any(token in receipt_compact for token in ("已", "已经", "完成", "成功"))
        )
        if receipt_compact in {
            "已发送",
            "发送成功",
            "发送完成",
            "发送完毕",
            "已成功发送",
            "消息已发送",
            "消息发送成功",
            "消息已发送会等对方回复",
            "messagesent",
            "sent",
        } or status_receipt_like or self._is_proactive_delivery_receipt_text(text) or re.fullmatch(r"(?i)message\s+sent\s+to\s+session\s+\S+", text):
            atrelay_result = getattr(event, "private_companion_atrelay_tool_result", None)
            if isinstance(atrelay_result, dict) and _single_line(atrelay_result.get("status"), 24) in {"success", "scheduled"}:
                final_reply = _single_line(atrelay_result.get("final_reply"), 80) or "说过啦。"
                reference = _single_line(atrelay_result.get("final_reply_reference"), 260)
                rewriter = getattr(self, "_rewrite_reference_reply_with_persona", None)
                if reference and callable(rewriter):
                    sender_id = ""
                    try:
                        resolver = getattr(self, "_private_user_id_for_event", None)
                        sender_id = (
                            resolver(event)
                            if callable(resolver)
                            else self._canonical_private_user_id(str(event.get_sender_id()))
                        )
                    except Exception:
                        try:
                            sender_id = str(event.get_sender_id())
                        except Exception:
                            sender_id = ""
                    users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else {}
                    user = users.get(sender_id) if sender_id and isinstance(users, dict) and isinstance(users.get(sender_id), dict) else {}
                    rewritten = await rewriter(
                        reference,
                        scene="拦截工具发送状态后改成自然聊天回执",
                        user=user,
                        event=event,
                        fallback_text=final_reply,
                        task="atrelay_receipt_rewrite",
                        max_chars=70,
                        allow_fallback=True,
                        preserve_status=True,
                    )
                    if rewritten:
                        final_reply = rewritten
                logger.info(
                    "[PrivateCompanion] 工具发送回执已改为自然短句: before=%s after=%s",
                    _single_line(text, 120),
                    final_reply,
                )
                event.set_result(self._build_result_from_chain([Plain(final_reply)]))
                return
            companion_receipt = bool(
                getattr(event, "private_companion_proactive_framework", False)
            )
            if not companion_receipt:
                logger.debug(
                    "[PrivateCompanion] 放行非陪伴插件工具回执: session=%s text=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                    _single_line(text, 120),
                )
                return
            logger.warning(
                "[PrivateCompanion] 已拦截孤立工具发送回执外发: session=%s text=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                _single_line(text, 120),
            )
            self._record_passive_no_reply(
                event,
                source="发送前拦截",
                reason="孤立工具发送回执被拦截",
                reply_preview=text,
                level="warn",
            )
            empty_result = self._build_result_from_chain([])
            try:
                empty_result.stop_event()
            except Exception:
                pass
            event.set_result(empty_result)
            event.stop_event()
            return
        if not bool(runtime_persona_setting(self, 'enable_framework_error_leak_guard', True)):
            return
        tool_loop_markers = (
            "trying to send messages",
            "sent 20",
            "no response yet",
            "shared parts",
            "asked for her thoughts",
            "message captured",
            "executed the same tool",
            "repetition is now very high",
            "agent reached max steps",
            "forcing a final response",
            "tool `send_message_to_user`",
            "send_message_to_user",
            "一直试着给",
            "发了差不多20条",
            "还没收到回复",
        )
        error_kind_checker = getattr(self, "_framework_error_leak_kind", None)
        marker_kind = error_kind_checker(text) if callable(error_kind_checker) else ""
        if not marker_kind and any(marker in compact for marker in tool_loop_markers):
            marker_kind = "tool_loop"
        if not marker_kind:
            return
        logger.warning(
            "[PrivateCompanion] 已拦截框架异常文本外发: kind=%s session=%s",
            marker_kind,
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
        )
        self._record_passive_no_reply(
            event,
            source="发送前拦截",
            reason=f"框架异常文本外发被拦截:{marker_kind}",
            reply_preview="",
            level="warn",
        )
        empty_result = self._build_result_from_chain([])
        try:
            empty_result.stop_event()
        except Exception:
            pass
        event.set_result(empty_result)
        event.stop_event()

    def _restore_response_review_meta_leak_before_send(self, event: AstrMessageEvent, chain: list[Any]) -> bool:
        if not chain or any(not isinstance(comp, Plain) for comp in chain):
            return False
        outbound = "\n".join(str(getattr(comp, "text", "") or "") for comp in chain).strip()
        cleaned, reason = self._strip_response_review_meta_leak(outbound)
        if not reason:
            return False
        fallback = str(getattr(event, "_private_companion_response_review_fallback_text", "") or "").strip()
        replacement = cleaned or fallback
        if replacement and self._response_review_meta_leak_reason(replacement):
            replacement = ""
        setattr(event, "_private_companion_response_review_guard_active", False)
        logger.error(
            "[PrivateCompanion] 发送前拦截到回复复核内部判断: session=%s reason=%s before=%s after=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            reason,
            _single_line(outbound, 180),
            _single_line(replacement, 180),
        )
        if replacement:
            try:
                current_result = event.get_result()
                current_result.chain = [Plain(replacement)]
            except Exception:
                event.set_result(self._build_result_from_chain([Plain(replacement)]))
            self._schedule_reply_interception_forward(
                "rewrite",
                source="回复复核发送前保护",
                reason=f"复核模型返回内部判断，已回退可发送正文：{reason}",
                source_session=_single_line(getattr(event, "unified_msg_origin", ""), 180),
                before=outbound,
                after=replacement,
            )
            return True
        self._record_passive_no_reply(
            event,
            source="发送前拦截",
            reason=f"回复复核内部判断泄漏：{reason}",
            reply_preview=outbound,
            level="warn",
        )
        empty_result = self._build_result_from_chain([])
        try:
            empty_result.stop_event()
        except Exception:
            pass
        event.set_result(empty_result)
        event.stop_event()
        return True

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def suppress_group_question_wakeup_collision_reply(self, event: AstrMessageEvent, *args, **kwargs):
        """答疑唤醒的群聊回复发送前复核，避免 Bot 碰瓷式插话。"""
        if self is None or not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "enable_group_companion"):
            return
        if not self._feature_enabled_or_temp_unlocked("enable_group_companion"):
            return
        if not self._passive_response_review_enabled():
            return
        if self._effective_passive_review_mode() == "local_only":
            return
        if bool(getattr(event, "_private_companion_group_question_review_done", False)):
            return
        setattr(event, "_private_companion_group_question_review_done", True)
        group_id = self._extract_group_id_from_event(event)
        if not group_id:
            return
        scene = getattr(event, "private_companion_group_scene", None)
        if not isinstance(scene, dict) or str(scene.get("trigger") or "") != "group_wakeup_question":
            return
        result = event.get_result()
        if result is None:
            return
        try:
            if hasattr(result, "is_llm_result") and not result.is_llm_result():
                return
        except Exception:
            pass
        chain = list(getattr(result, "chain", []) or [])
        if not chain:
            return
        reply_text = self._chain_text_for_forbidden_recall(chain, limit=600)
        if not reply_text:
            return
        try:
            review = await self._review_group_question_wakeup_reply_before_send(event, reply_text=reply_text)
        except Exception as exc:
            logger.warning(
                "[PrivateCompanion] 群聊答疑回复发送前复核失败,默认放行: %s",
                _single_line(exc, 160),
            )
            return
        if str(review.get("decision") or "") != "drop":
            return
        logger.info(
            "[PrivateCompanion] 已拦截群聊答疑碰瓷回复: group=%s reason=%s text=%s",
            group_id,
            _single_line(review.get("reason"), 120),
            _single_line(reply_text, 160),
        )
        self._record_passive_no_reply(
            event,
            source="群聊答疑复核",
            reason=_single_line(review.get("reason"), 120) or "群聊答疑碰瓷回复被拦截",
            reply_preview=reply_text,
            level="info",
        )
        empty_result = self._build_result_from_chain([])
        try:
            empty_result.stop_event()
        except Exception:
            pass
        event.set_result(empty_result)
        event.stop_event()

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def suppress_smart_silence_reply_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """用户明确想停下当前话题时，用小模型决定是否静默取消待发送回复。"""
        if self is None or not self.enabled:
            return
        if (
            self._passive_response_review_enabled()
            and bool(getattr(event, "_private_companion_response_review_drop", False))
        ):
            logger.info("[PrivateCompanion] 回复复核去重发送前兜底拦截")
            self._record_passive_no_reply(
                event,
                source="回复复核去重",
                reason="最终回复与上一条 Bot 消息重复",
                level="info",
            )
            empty_result = self._build_result_from_chain([])
            try:
                empty_result.stop_event()
            except Exception:
                pass
            event.set_result(empty_result)
            event.stop_event()
            return
        if bool(getattr(event, "_private_companion_smart_silence_drop", False)):
            logger.info(
                "[PrivateCompanion] 智能沉默发送前兜底拦截: reason=%s",
                _single_line(getattr(event, "_private_companion_smart_silence_reason", ""), 120),
            )
            self._record_passive_no_reply(
                event,
                source="智能沉默",
                reason=_single_line(getattr(event, "_private_companion_smart_silence_reason", ""), 120) or "用户边界语义触发静默",
                level="info",
            )
            empty_result = self._build_result_from_chain([])
            try:
                empty_result.stop_event()
            except Exception:
                pass
            event.set_result(empty_result)
            event.stop_event()
            return
        if not bool(runtime_persona_setting(self, 'enable_smart_silence', True)):
            return
        try:
            if bool(getattr(event, "is_private_chat", lambda: False)()):
                return
        except Exception:
            pass
        result = event.get_result()
        if result is None:
            return
        try:
            if hasattr(result, "is_llm_result") and not result.is_llm_result():
                return
        except Exception:
            pass
        chain = list(getattr(result, "chain", []) or [])
        if not chain or any(not isinstance(comp, Plain) for comp in chain):
            return
        reply_text = self._chain_text_for_forbidden_recall(chain, limit=600)
        if not reply_text:
            return
        inbound_text = _single_line(
            getattr(event, "private_companion_group_text", "") or getattr(event, "message_str", ""),
            260,
        )
        trigger_checker = getattr(self, "_smart_silence_contextual_trigger_reason", None)
        trigger_reason = (
            trigger_checker(inbound_text, reply_text, session_kind="group")
            if callable(trigger_checker)
            else self._smart_silence_trigger_reason(inbound_text)
        )
        if not trigger_reason:
            return
        recent_context: list[str] = []
        group_id = self._extract_group_id_from_event(event)
        if group_id:
            group = self._get_group(group_id)
            sender_id = ""
            try:
                sender_id = str(event.get_sender_id())
            except Exception:
                sender_id = ""
            flow_formatter = getattr(self, "_format_group_recent_flow_for_review", None)
            recent_flow = (
                flow_formatter(group, sender_id=sender_id, text=inbound_text, max_lines=8, max_chars=1000)
                if callable(flow_formatter)
                else ""
            )
            for line in recent_flow.splitlines():
                line = _single_line(line, 140)
                if line.startswith("- "):
                    line = line[2:].strip()
                if line:
                    recent_context.append(line)
        try:
            decision = await self._decide_smart_silence(
                inbound_text=inbound_text,
                response_text=reply_text,
                user=None,
                session_kind="group" if group_id else "chat",
                recent_context=recent_context,
            )
        except Exception as exc:
            logger.info("[PrivateCompanion] 智能沉默发送前判定失败,默认放行: %s", _single_line(exc, 120))
            return
        if str(decision.get("decision") or "") != "silent":
            return
        logger.info(
            "[PrivateCompanion] 智能沉默已取消本轮群聊回复: group=%s reason=%s inbound=%s reply=%s",
            group_id or "-",
            _single_line(decision.get("reason"), 120),
            _single_line(inbound_text, 120),
            _single_line(reply_text, 140),
        )
        self._record_passive_no_reply(
            event,
            source="智能沉默",
            reason=_single_line(decision.get("reason"), 120) or "群聊边界语义触发静默",
            reply_preview=reply_text,
            level="info",
        )
        empty_result = self._build_result_from_chain([])
        try:
            empty_result.stop_event()
        except Exception:
            pass
        event.set_result(empty_result)
        event.stop_event()

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def record_empty_passive_result_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """发送前兜底记录空结果，避免被动不回复却没有排障原因。"""
        if self is None or not self.enabled:
            return
        if bool(getattr(event, "_private_companion_passive_no_reply_recorded", False)):
            return
        if bool(getattr(event, "private_companion_proactive_framework", False)):
            return
        result = event.get_result()
        if result is None:
            return
        chain = list(getattr(result, "chain", []) or [])
        if chain:
            return
        try:
            is_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            is_private = False
        is_group = bool(self._extract_group_id_from_event(event))
        if not is_private and not is_group:
            return
        self._record_passive_no_reply(
            event,
            source="发送前检查",
            reason="发送前结果为空",
            level="info",
        )

    @staticmethod
    def _photo_tool_followup_chain_has_visible_content(chain: list[Any]) -> bool:
        for component in chain if isinstance(chain, list) else []:
            if not isinstance(component, Plain):
                return True
            text = str(getattr(component, "text", "") or "")
            visible = "".join(
                char
                for char in text
                if not char.isspace() and not unicodedata.category(char).startswith("C")
            )
            if visible:
                return True
        return False

    @filter.on_decorating_result(priority=-19000)
    @_multi_persona_event_context
    async def suppress_empty_photo_tool_followup_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """Stop any adapter-visible followup after a tool already sent the photo."""
        if self is None or not self.enabled:
            return
        if not bool(getattr(event, "_private_companion_photo_tool_sent", False)):
            return
        result = event.get_result()
        if result is None:
            return
        chain = list(getattr(result, "chain", []) or [])
        had_visible_content = self._photo_tool_followup_chain_has_visible_content(chain)
        empty_result = self._build_result_from_chain([])
        try:
            empty_result.stop_event()
        except Exception:
            pass
        event.set_result(empty_result)
        event.stop_event()
        logger.info(
            "[PrivateCompanion] 已阻止图片工具成功发送后的尾随消息: session=%s components=%s visible=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            len(chain),
            had_visible_content,
        )

    @filter.on_decorating_result(priority=300)
    @_multi_persona_event_context
    async def apply_tts_enhancement_before_send_hook(self, event: AstrMessageEvent, *args, **kwargs):
        """发送前处理 TTS强化标签和自动语音转换。"""
        if self is None or not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "enable_tts_enhancement"):
            return
        await self.apply_tts_enhancement_before_send(event)

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def strip_group_internal_identity_anchors(self, event: AstrMessageEvent, *args, **kwargs):
        """发送前清理群聊内部身份锚点，避免调试标记泄露到回复。"""
        if self is None or not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "enable_group_companion"):
            return
        if not self._feature_enabled_or_temp_unlocked("enable_group_companion"):
            return
        if not self._extract_group_id_from_event(event):
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            return
        for comp in chain:
            if not isinstance(comp, Plain):
                continue
            original = str(getattr(comp, "text", "") or "")
            cleaned = self._strip_internal_identity_anchors(original)
            if cleaned != original:
                try:
                    comp.text = cleaned
                except Exception:
                    pass

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def suppress_group_silent_control_reply(self, event: AstrMessageEvent, *args, **kwargs):
        """模型输出“不回复”控制语时静默吞掉，避免把内部判断发到群里。"""
        if self is None or not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "enable_group_companion"):
            return
        if not self._feature_enabled_or_temp_unlocked("enable_group_companion"):
            return
        if not self._extract_group_id_from_event(event):
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain or any(not isinstance(comp, Plain) for comp in chain):
            return
        text = "".join(str(getattr(comp, "text", "") or "") for comp in chain).strip()
        if not self._is_silent_control_reply_text(text):
            return
        logger.info("[PrivateCompanion] 已静默吞掉群聊不回复控制语: %s", _single_line(text, 120))
        self._record_passive_no_reply(
            event,
            source="群聊静默",
            reason="模型输出不回复控制语",
            reply_preview=text,
            level="info",
        )
        empty_result = self._build_result_from_chain([])
        try:
            empty_result.stop_event()
        except Exception:
            pass
        event.set_result(empty_result)
        event.stop_event()

    async def _review_group_question_wakeup_reply_before_send(
        self,
        event: AstrMessageEvent,
        *,
        reply_text: str,
    ) -> dict[str, str]:
        provider_id = self._task_provider(
            runtime_persona_setting(self, "response_review_provider_id", ""),
            runtime_persona_setting(self, "group_followup_judge_provider_id", ""),
            runtime_persona_setting(self, "mai_style_provider_id", ""),
        )
        if not provider_id:
            return {"decision": "send", "reason": "未配置复核模型"}
        scene = getattr(event, "private_companion_group_scene", None)
        if not isinstance(scene, dict):
            scene = {}
        group_id = self._extract_group_id_from_event(event)
        group = self._get_group(group_id) if group_id else {}
        inbound_text = _single_line(
            getattr(event, "private_companion_group_text", "") or getattr(event, "message_str", "") or "",
            220,
        )
        sender_id = ""
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        flow_formatter = getattr(self, "_format_group_recent_flow_for_review", None)
        recent_flow = (
            flow_formatter(group, sender_id=sender_id, text=inbound_text, max_lines=12, max_chars=1400)
            if callable(flow_formatter)
            else ""
        )
        wakeup = group.get("last_group_wakeup") if isinstance(group.get("last_group_wakeup"), dict) else {}
        prompt = f"""
判断这条群聊回复是否应该在发送前拦截。

只输出 JSON 对象，不要解释。

可选 decision：
- send：确实是在自然回答群里的公共求助/开放问题，可以发送。
- drop：像 Bot 碰瓷插话，或问题明显是在接群友的话、问别人、吐槽/反问，不该发送。

判断标准：
- 没有明确 @ Bot 或引用 Bot 时，要更保守。
- 如果触发句只是“为什么/啥情况/怎么回事/不会吧？”这类接话、吐槽、反问，通常 drop。
- 如果是“有没有人懂/谁会/求问/报错/怎么解决/帮忙”这类公共求助，通常 send。
- 如果待发送内容虽然正确，但当前群聊并不需要 Bot 插入，也应 drop。

【本轮群唤醒】
trigger={_single_line(scene.get('trigger'), 40)} reason={_single_line(scene.get('reason'), 60)}
wakeup_type={_single_line(wakeup.get('type'), 40)} score={_single_line(wakeup.get('score'), 20)}/{_single_line(wakeup.get('threshold'), 20)} detail={_single_line(wakeup.get('reason_detail'), 160)}

【真实最近群聊】
{recent_flow or "（无）"}

【触发消息】
{inbound_text}

【待发送回复】
{_single_line(reply_text, 360)}

请输出：
{{"decision":"send|drop","reason":"一句很短的原因"}}
""".strip()
        started = time.perf_counter()
        raw = await self._llm_call(
            prompt,
            max_tokens=120,
            provider_id=provider_id,
            task="group_question_wakeup_reply_review",
        )
        payload = self._parse_json_object(raw)
        decision = str((payload or {}).get("decision") or "").strip().lower()
        reason = _single_line((payload or {}).get("reason"), 120)
        if decision not in {"send", "drop"}:
            decision = "send"
            reason = reason or "复核输出不可解析，默认放行"
        logger.info(
            "[PrivateCompanion] 群聊答疑回复发送前复核: decision=%s elapsed=%dms reason=%s trigger=%s text=%s",
            decision,
            int((time.perf_counter() - started) * 1000),
            reason,
            _single_line(scene.get("trigger"), 40),
            _single_line(reply_text, 140),
        )
        if recent_flow:
            logger.info(
                "[PrivateCompanion] 群聊答疑复核已附带真实群聊上下文: group=%s lines=%s chars=%s",
                group_id or "-",
                len([line for line in recent_flow.splitlines() if line.strip()]),
                len(recent_flow),
            )
        return {"decision": decision, "reason": reason}

    @filter.on_decorating_result(priority=-1000)
    @_multi_persona_event_context
    async def strip_unexpected_private_passive_reply(self, event: AstrMessageEvent, *args, **kwargs):
        """私聊被动主链不沿用框架误带的引用，避免 QQ 显示跨会话引用。"""
        if self is None or not self.enabled:
            return
        if bool(getattr(event, "private_companion_proactive_framework", False)):
            return
        try:
            if not bool(event.is_private_chat()):
                return
        except Exception:
            return
        try:
            result = event.get_result()
        except Exception:
            return
        if result is None:
            return
        try:
            is_llm_result = bool(result.is_llm_result())
        except Exception:
            return
        if not is_llm_result:
            return
        chain = list(getattr(result, "chain", []) or [])
        if not chain:
            return
        current_message_ids = set(self._event_message_id_candidates(event))
        cleaned_chain: list[Any] = []
        removed_reply_ids: list[str] = []
        for component in chain:
            if not self._is_reply_component(component):
                cleaned_chain.append(component)
                continue
            reply_id = _single_line(self._extract_reply_message_id(component), 120)
            if reply_id and reply_id in current_message_ids:
                cleaned_chain.append(component)
                continue
            removed_reply_ids.append(reply_id or "unknown")
        if len(cleaned_chain) == len(chain):
            return
        try:
            result.chain = cleaned_chain
        except Exception:
            event.set_result(self._build_result_from_chain(cleaned_chain))
        logger.info(
            "[PrivateCompanion] 已移除私聊被动主链中的跨目标引用组件: session=%s current=%s removed=%s targets=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "-",
            ",".join(sorted(current_message_ids)) or "-",
            len(chain) - len(cleaned_chain),
            ",".join(removed_reply_ids) or "-",
        )

    @filter.on_decorating_result(priority=100)
    @_multi_persona_event_context
    async def apply_segmented_llm_reply_scope(self, event: AstrMessageEvent, *args, **kwargs):
        """按回复范围与分段策略整理 LLM 输出，减少长回复和误引用。"""
        if self is None or not self.enabled:
            return
        external_proactive = (
            str(getattr(event, "_private_companion_external_proactive_source", "") or "")
            == "proactive_chat"
        )
        # Strip reasoning/thinking chain and internal control markers (e.g.
        # [[PC_PHOTO_SENT_NO_FOLLOWUP]]) from ALL Plain text joined together,
        # BEFORE any segmented-scope early return below, because:
        #   1. The thinking chain can be split across multiple Plain components
        #      and per-component regex matching would fail.
        #   2. Passive replies in proactive_only mode (e.g. "来张表情包")
        #      would otherwise skip the strip via the segmented_scope early
        #      return, leaking thinking/control content outbound.
        #   3. `_strip_internal_message_blocks` already handles
        #      [[PC_PHOTO_SENT_NO_FOLLOWUP]] and other markers, but only
        #      `_strip_chain_plain_thinking` joins all Plain components first,
        #      which is necessary for reliable matching.
        early_result = event.get_result()
        if early_result is not None:
            early_chain = list(getattr(early_result, "chain", []) or [])
            if early_chain:
                _strip_chain_plain_thinking(self, early_chain)
        if self._proactive_only_blocks_passive_event(event, "enable_segmented_proactive_reply"):
            return
        if not self._feature_enabled_or_temp_unlocked("enable_segmented_proactive_reply"):
            return
        segmented_scope = str(
            self._segmented_setting("scope", event=event, default="proactive_only")
            or "proactive_only"
        )
        if segmented_scope != "all_llm" and not external_proactive:
            return
        if external_proactive and bool(getattr(event, "_private_companion_external_presegmented", False)):
            return
        if not self._segmented_scope_allows_event(event):
            return
        result = event.get_result()
        if result is None or not result.chain:
            return
        is_llm_result = False
        try:
            is_llm_result = bool(result.is_llm_result())
        except Exception:
            is_llm_result = False
        chain = list(result.chain or [])
        if self._restore_response_review_meta_leak_before_send(event, chain):
            result = event.get_result()
            chain = list(getattr(result, "chain", []) or []) if result is not None else []
            if not chain:
                return
            try:
                is_llm_result = bool(result.is_llm_result())
            except Exception:
                is_llm_result = False
        if (
            bool(runtime_persona_setting(self, 'enable_framework_error_leak_guard', True))
            and chain
            and all(isinstance(comp, Plain) for comp in chain)
        ):
            provider_error_checker = getattr(self, "_looks_like_internal_provider_error_text", None)
            outbound_text = "\n".join(str(getattr(comp, "text", "") or "") for comp in chain).strip()
            if callable(provider_error_checker):
                try:
                    provider_error = bool(outbound_text and provider_error_checker(outbound_text))
                except Exception:
                    provider_error = False
                if provider_error:
                    logger.warning(
                        "[PrivateCompanion] 分段前丢弃 Provider 错误正文: session=%s preview=%s",
                        _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                        _single_line(outbound_text, 180),
                    )
                    self._record_passive_no_reply(
                        event,
                        source="分段前拦截",
                        reason="Provider 错误正文未进入分段发送",
                        reply_preview=outbound_text,
                        level="warn",
                    )
                    empty_result = self._build_result_from_chain([])
                    try:
                        empty_result.stop_event()
                    except Exception:
                        pass
                    event.set_result(empty_result)
                    event.stop_event()
                    return
        reaction_intent = getattr(
            event,
            "_private_companion_reaction_expression_intent",
            None,
        )
        has_reaction_intent = isinstance(reaction_intent, dict) and bool(
            reaction_intent
        )
        deferred_reaction_tts = getattr(
            event,
            "_private_companion_deferred_reaction_tts",
            None,
        )
        plugin_owned_reaction_text = (
            has_reaction_intent
            and isinstance(deferred_reaction_tts, dict)
            and bool(deferred_reaction_tts)
        )
        plugin_tts_plain_fallback = (
            bool(getattr(event, "_private_companion_tts_request_applied", False))
            and bool(self._plain_result_body_text(chain))
        )
        if is_llm_result and await self._should_defer_segmenting_to_astrbot_tts(event, result, chain):
            logger.debug(
                "[PrivateCompanion] 当前 LLM 结果交由 AstrBot 官方 TTS 与原生分段处理: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
            return
        if (
            not is_llm_result
            and not external_proactive
            and not plugin_owned_reaction_text
            and not plugin_tts_plain_fallback
            and not self._private_plain_result_allows_segmenting(event, chain)
        ):
            return
        if getattr(result, "use_t2i_", None) or getattr(result, "use_markdown_", None):
            return
        if not self._segmented_platform_allows(event=event):
            return
        if not chain:
            return
        chunks, changed, text = self._segment_llm_reply_chain(event, chain)
        if not chunks or not text:
            return
        chunks = self._limit_private_routine_check_segments(
            str(getattr(event, "message_str", "") or ""),
            chunks,
        )
        if len(chunks) <= 1:
            if changed:
                event.set_result(self._build_result_from_chain(chunks[0]))
            return
        llm_segment_count = max(0, _safe_int(getattr(event, "_private_companion_llm_segment_count", 0), 0, 0))
        logger.debug(
            "[PrivateCompanion] 按分段计划整理 LLM 回复: chars=%s segments=%s llm_segments=%s",
            len(text),
            len(chunks),
            llm_segment_count,
        )
        logger.info(
            "[PrivateCompanion] 已按分段计划发送 LLM 回复: segments=%s llm_segments=%s first=%s full=%s",
            len(chunks),
            llm_segment_count,
            _single_line(self._segmented_chunk_log_text(chunks[0]), 120),
            _single_line(text, 420),
        )
        plain_segments = self._plain_text_segments_from_chunks(chunks)
        if (
            not has_reaction_intent
            and plain_segments
            and len(plain_segments) == len(chunks)
            and await self._send_segmented_event_forward_message(
                event,
                plain_segments,
                source="decorating_result",
            )
        ):
            empty_result = self._build_result_from_chain([])
            try:
                empty_result.stop_event()
            except Exception:
                pass
            event.set_result(empty_result)
            event.stop_event()
            return
        event.set_result(self._build_result_from_chain(chunks[0]))
        if runtime_persona_setting(self, 'enable_daily_case_review_experiment', False):
            self._record_daily_review_outbound_case(event, chunks[0])
        activity_baseline = time.time()
        if len(chunks) > 1:
            previous_segment = self._segmented_chunk_log_text(chunks[0])
            if has_reaction_intent:
                setattr(
                    event,
                    "_private_companion_reaction_expression_expected_primary_chunks",
                    chunks,
                )
                setattr(
                    event,
                    "_private_companion_reaction_expression_segmented_remainder",
                    {
                        "chunks": chunks[1:],
                        "previous_segment": previous_segment,
                        "started_at": activity_baseline,
                        "started": False,
                        "completed": False,
                    },
                )
                logger.info(
                    "[PrivateCompanion] 表情正文启用有序分段: session=%s segments=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120)
                    or "unknown",
                    len(chunks),
                )
            else:
                self._create_lifecycle_background_task(
                    self._send_segmented_llm_chain_remainder(
                        event,
                        chunks[1:],
                        previous_segment=previous_segment,
                        source="decorating_result",
                        started_at=activity_baseline,
                    ),
                    label="segmented_llm_remainder",
                )

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def remember_group_bot_reply_context_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """记录群聊 Bot 实际候选回复，供下一轮连续对话判断使用。"""
        # Group continuity is committed only by the confirmed-delivery
        # finalizer. This decorating hook must remain a pure read/transform
        # stage because the adapter can reject the outgoing result afterwards.
        return

    def _plain_result_body_text(self, chain: list[Any]) -> str:
        """Return text when a result contains only an optional quote and plain body."""
        body = [comp for comp in list(chain or []) if not self._is_reply_component(comp)]
        if not body or any(not isinstance(comp, Plain) for comp in body):
            return ""
        return "".join(str(getattr(comp, "text", "") or "") for comp in body).strip()

    def _private_plain_result_allows_segmenting(
        self,
        event: AstrMessageEvent,
        chain: list[Any],
    ) -> bool:
        """Allow plugin text replies while leaving functional command output intact."""
        if not self._plain_result_body_text(chain):
            return False
        command_reason = getattr(self, "_tts_functional_command_reason", None)
        if callable(command_reason):
            try:
                if command_reason(event):
                    return False
            except Exception:
                pass
        return True

    @filter.on_decorating_result(priority=-9000)
    @_multi_persona_event_context
    async def final_tts_markup_guard_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """发送前终检 TTS 标签，避免 <tts> 原样泄漏到聊天。"""
        if self is None or not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "enable_tts_enhancement"):
            return
        guard = getattr(self, "finalize_outbound_tts_markup_guard", None)
        if callable(guard):
            await guard(event)

    def _is_reply_component(self, component: Any) -> bool:
        try:
            if Reply is not None and isinstance(component, Reply):
                return True
        except Exception:
            pass
        return component.__class__.__name__.lower() == "reply"

    def _segmented_chunk_log_text(self, chunk: list[Any]) -> str:
        parts: list[str] = []
        for comp in chunk or []:
            if isinstance(comp, Plain):
                text = str(getattr(comp, "text", "") or "").strip()
                if text:
                    parts.append(text)
                continue
            if self._is_reply_component(comp):
                parts.append("[引用]")
            else:
                parts.append(f"[{comp.__class__.__name__}]")
        return " ".join(parts).strip()

    def _plain_text_segments_from_chunks(self, chunks: list[list[Any]]) -> list[str]:
        segments: list[str] = []
        for chunk in chunks or []:
            if not chunk or any(not isinstance(comp, Plain) for comp in chunk):
                return []
            text = "".join(str(getattr(comp, "text", "") or "") for comp in chunk).strip()
            text = self._strip_leading_sentence_boundary_artifacts(text)
            if not text:
                return []
            segments.append(text)
        return segments

    def _segmented_context_chars(self, text: str) -> set[str]:
        text = re.sub(r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>", "", str(text or ""), flags=re.IGNORECASE)
        stop_chars = set(
            "的一是不了在有和人就都而及与着或个上也很到说要去会这那我你他她它们"
            "吧呢呀啊吗么啦喔哦噢嘛哈嘿诶哎被把给让才还再又没别刚边里外"
        )
        chars = {ch for ch in text if "\u4e00" <= ch <= "\u9fff" and ch not in stop_chars}
        chars.update(re.findall(r"[a-zA-Z][a-zA-Z0-9_]{1,}", text.lower()))
        return chars

    def _segmented_context_overlap_ratio(self, left: str, right: str) -> float:
        left_chars = self._segmented_context_chars(left)
        right_chars = self._segmented_context_chars(right)
        if not left_chars or not right_chars:
            return 1.0
        return len(left_chars & right_chars) / max(1, min(len(left_chars), len(right_chars)))

    def _segmented_remainder_context_drift_reason(
        self,
        event: AstrMessageEvent,
        *,
        previous_text: str,
        next_text: str,
        source: str = "",
    ) -> str:
        """Stop delayed passive chunks when they look like a different reply turn."""
        segmented_scope = self._segmented_setting(
            "scope",
            event=event,
            default="proactive_only",
        )
        if source != "decorating_result" or segmented_scope != "all_llm":
            return ""
        prev = _single_line(previous_text, 260)
        nxt = _single_line(next_text, 260)
        if not prev or not nxt:
            return ""
        inbound = ""
        getter = getattr(event, "get_message_str", None)
        if callable(getter):
            try:
                inbound = str(getter() or "")
            except Exception:
                inbound = ""
        if not inbound:
            inbound = str(getattr(event, "message_str", "") or "")
        if any(marker in inbound for marker in ("在干嘛", "干什么", "忙什么", "忙啥", "进度", "代码", "项目", "修到", "跑通", "测试", "校验")):
            return ""

        context = f"{inbound}\n{prev}"
        context_chars = self._segmented_context_chars(context)
        next_chars = self._segmented_context_chars(nxt)
        if len(context_chars) < 8 or len(next_chars) < 6:
            return ""
        overlap = self._segmented_context_overlap_ratio(context, nxt)
        if overlap >= 0.08:
            return ""

        food_markers = ("西瓜", "水果", "吃", "甜", "买", "拎", "饭", "餐", "晚饭", "午饭", "口", "手勒", "奖励")
        work_markers = ("逻辑", "校验", "进度", "跑通", "顺手", "焦躁", "代码", "编译", "测试", "调试", "需求", "项目")
        checkin_markers = ("忙完没", "忙完了吗", "忙完了没", "你那边忙", "歇会", "休息一下", "停下来")
        fresh_turn_pattern = r"^\s*(在呢|我在|我这边|这边|刚把|刚刚把|刚刚|我刚|你那边|你这边)"

        context_has_food = any(marker in context for marker in food_markers)
        next_has_work = any(marker in nxt for marker in work_markers)
        next_is_checkin = any(marker in nxt for marker in checkin_markers)
        if context_has_food and (next_has_work or next_is_checkin):
            return "food_topic_to_work_or_checkin"
        if re.search(fresh_turn_pattern, nxt) and (next_has_work or next_is_checkin):
            return "fresh_turn_without_topic_overlap"
        if re.search(r"^\s*(你那边|你这边)", nxt) and next_is_checkin:
            return "new_checkin_without_topic_overlap"
        if "昨晚" in nxt and "昨晚" not in context and re.search(fresh_turn_pattern, nxt):
            return "unexpected_time_anchor"
        return ""

    def _sanitize_segmented_plain_text(self, event: AstrMessageEvent, text: Any) -> str:
        protected_tts_tokens = getattr(event, "_private_companion_tts_block_tokens", None)
        preserve_private_tts_tokens = (
            bool(runtime_persona_setting(self, 'enable_tts_enhancement', False))
            and isinstance(protected_tts_tokens, dict)
            and bool(protected_tts_tokens)
        )
        cleaned = _strip_outbound_control_blocks(
            text,
            preserve_private_tts_tokens=preserve_private_tts_tokens,
            allowed_private_tts_tokens=set(protected_tts_tokens.keys())
            if isinstance(protected_tts_tokens, dict) else None,
        )
        if not bool(runtime_persona_setting(self, 'enable_tts_enhancement', False)):
            cleaned = re.sub(r"</?t{2,}s\b[^>]*>", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = sanitize_llm_segment_control_tokens(cleaned)
        return cleaned

    @staticmethod
    def _strip_llm_segment_marker_lines(text: Any) -> str:
        return strip_llm_segment_marker_lines(text)

    def _clean_segmented_reply_chunks(
        self,
        event: AstrMessageEvent,
        chunks: list[list[Any]],
    ) -> list[list[Any]]:
        cleaned_chunks: list[list[Any]] = []
        removed_internal_control = False
        for chunk in chunks or []:
            cleaned_chunk: list[Any] = []
            for comp in chunk or []:
                if isinstance(comp, Plain):
                    original = str(getattr(comp, "text", "") or "")
                    text = self._sanitize_segmented_plain_text(event, original)
                    removed_internal_control = removed_internal_control or text != original.strip()
                    text = self._strip_leading_sentence_boundary_artifacts(text)
                    if text:
                        cleaned_chunk.append(Plain(text))
                    continue
                cleaned_chunk.append(comp)
            if cleaned_chunk:
                cleaned_chunks.append(cleaned_chunk)
        if removed_internal_control:
            logger.warning(
                "[PrivateCompanion] 分段前已移除内部控制标记: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
        return cleaned_chunks

    def _llm_controlled_segmenting_allowed(self, event: AstrMessageEvent | None = None) -> bool:
        """Return whether the current conversation may use LLM boundaries."""
        if not bool(runtime_persona_setting(self, "enable_segmented_proactive_reply", False)):
            return False
        if not bool(runtime_persona_setting(self, "enable_llm_controlled_segmenting", False)):
            return False
        scope = self._segmented_setting(
            "scope",
            event=event,
            default="proactive_only",
        )
        external_proactive = bool(
            event is not None
            and (
                bool(getattr(event, "private_companion_proactive_framework", False))
                or bool(getattr(event, "_private_companion_external_proactive_source", ""))
            )
        )
        if str(scope or "proactive_only").strip().lower() != "all_llm" and not external_proactive:
            return False
        try:
            if event is not None and not bool(self._segmented_scope_allows_event(event)):
                return False
            if event is not None and not bool(self._segmented_platform_allows(event=event)):
                return False
            return True
        except Exception:
            return False

    @staticmethod
    def _default_llm_controlled_segmenting_prompt() -> str:
        return (
            "你可以自行决定是否把你的本轮回复作为多条消息发送；"
            "日常对话、聊天 等需要短句输出的情景建议使用拆分；"
            "长文、教程、代码 等连续性较高的表述尽量少用分段。"
            "\n使用方法：每个消息边界都必须使用下面的完整标记： {{split_marker}}，并让它单独占一行，不能添加引号，也不能把它放进代码块。"
            "\n示例：第一段内容\n{{split_marker}}\n第二段内容。"
            "\n只有上述方法可以实现发送多条消息，换行和连续换行都不会作为多条消息发送。（仅在确有必要时使用）。"
        )

    def _llm_controlled_segmenting_prompt(self) -> str:
        """Resolve the active persona's user-facing segmentation instruction."""
        custom = str(
            runtime_persona_setting(self, "llm_controlled_segmenting_prompt", "")
            or ""
        ).strip()[:4000]
        template = custom or PrivateCompanionPlugin._default_llm_controlled_segmenting_prompt()
        return re.sub(
            r"\{\{\s*split_marker\s*\}\}",
            lambda _match: LLM_SEGMENT_MARKER,
            template,
            flags=re.IGNORECASE,
        )

    @filter.on_llm_request(priority=-253000)
    @_multi_persona_event_context
    async def inject_llm_controlled_segmenting_instruction(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ) -> None:
        """Tell only the main conversation model about the outbound marker."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        if not self._llm_controlled_segmenting_allowed(event):
            return
        marker = "<!-- private_companion_reply_segmentation_v1 -->"
        if self._request_has_managed_prompt_marker(req, marker):
            return
        # CDATA keeps the fixed transport marker byte-for-byte visible while
        # remaining valid XML. A normal XML text node would serialize '<' as
        # entities, making exact-copy behavior less reliable for some models.
        content = (
            '<private_companion_context><section title="回复分段控制"><![CDATA['
            + self._llm_controlled_segmenting_prompt().replace("]]>", "]]]]><![CDATA[>")
            + "]]></section></private_companion_context>"
        )
        placement = "prompt" if self._append_turn_prompt_fragment_by_position(
            req,
            marker,
            content,
            title="回复分段控制",
            priority=90,
            source="segmented_reply",
            structured=True,
        ) else "system_prompt"
        if placement == "system_prompt":
            self._materialize_conversation_system_block(
                req,
                key="reply.segmentation",
                marker=marker,
                content=content,
                title="回复分段控制",
                priority=90,
                source="segmented_reply",
                placement=PLACEMENT_DYNAMIC_SYSTEM,
                structured=True,
            )

    def _split_llm_controlled_text_for_event(
        self,
        event: AstrMessageEvent | None,
        text: str,
        *,
        umo: str = "",
    ) -> list[str]:
        """Apply explicit LLM boundaries and then spend the remaining rule budget."""
        planned = PrivateCompanionPlugin._split_llm_controlled_text_buffers_for_event(
            self,
            event,
            [text],
            umo=umo,
        )
        return planned[0] if planned else []

    def _split_llm_controlled_text_buffers_for_event(
        self,
        event: AstrMessageEvent | None,
        texts: list[str],
        *,
        umo: str = "",
    ) -> list[list[str]]:
        """Plan LLM and plugin boundaries across every text buffer in a chain."""
        raw_buffers = [str(text or "").strip() for text in texts]
        if not raw_buffers:
            return []
        if not bool(runtime_persona_setting(self, "enable_segmented_proactive_reply", False)):
            return [
                [cleaned] if (cleaned := sanitize_llm_segment_control_tokens(text)) else []
                for text in raw_buffers
            ]
        if not bool(runtime_persona_setting(self, "enable_llm_controlled_segmenting", False)):
            return [
                self._split_proactive_text(cleaned, event=event, umo=umo) if cleaned else []
                for text in raw_buffers
                for cleaned in [sanitize_llm_segment_control_tokens(text)]
            ]
        plugin_rules_enabled = bool(
            runtime_persona_setting(self, "enable_segmented_plugin_rules", True)
        )

        def apply_common_transforms(segment: str) -> str:
            candidate = self._split_proactive_text(
                segment,
                event=event,
                umo=umo,
                force_common_transforms=True,
                common_transforms_only=True,
            )
            return str(candidate[0] if candidate else "").strip()

        parsed_buffers: list[list[str]] = []
        controlled_buffers: list[bool] = []
        parse_results = []
        for raw_text in raw_buffers:
            parsed = parse_llm_segment_control(raw_text)
            parse_results.append(parsed)
            parsed_buffers.append(
                list(parsed.segments)
                if parsed.controlled
                else ([parsed.sanitized_text] if parsed.sanitized_text else [])
            )
            controlled_buffers.append(parsed.controlled)

        if event is not None:
            setattr(
                event,
                "_private_companion_llm_segment_diagnostics",
                {
                    "exact": sum(item.exact_boundary_count for item in parse_results),
                    "recovered": sum(item.recovered_boundary_count for item in parse_results),
                    "cleaned_only": sum(item.cleaned_only_count for item in parse_results),
                },
            )
            for attr_name in (
                "_private_companion_llm_history_segments",
                "_private_companion_llm_planned_chunk_texts",
                "_private_companion_llm_planned_segment_ids",
            ):
                try:
                    delattr(event, attr_name)
                except AttributeError:
                    pass
            if len(parse_results) == 1 and parse_results[0].controlled:
                history_segments = [
                    cleaned
                    for segment in parse_results[0].segments
                    if (cleaned := apply_common_transforms(segment))
                ]
                if len(history_segments) >= 2:
                    setattr(
                        event,
                        "_private_companion_llm_history_segments",
                        tuple(history_segments),
                    )

        def split_uncontrolled_buffer(text: str, *, suppress_plugin_rules: bool = False) -> list[str]:
            if not text:
                return []
            if suppress_plugin_rules:
                transformed = apply_common_transforms(text)
                return [transformed] if transformed else []
            return self._split_proactive_text(text, event=event, umo=umo)

        if not any(controlled_buffers):
            if plugin_rules_enabled:
                return [
                    split_uncontrolled_buffer(
                        parsed.sanitized_text,
                        suppress_plugin_rules=parsed.suppress_plugin_rule_split,
                    )
                    for parsed in parse_results
                ]
            return [
                [transformed]
                if (transformed := apply_common_transforms(parsed.sanitized_text))
                else []
                for parsed in parse_results
            ]

        llm_count = sum(len(segments) for segments in parsed_buffers)
        if event is not None:
            setattr(event, "_private_companion_llm_segment_count", llm_count)
        if not plugin_rules_enabled or any(
            item.suppress_plugin_rule_split for item in parse_results
        ):
            planned = [
                [
                    cleaned
                    for segment in segments
                    if (cleaned := apply_common_transforms(segment))
                ]
                for segments in parsed_buffers
            ]
            if event is not None and len(planned) == 1 and len(planned[0]) >= 2:
                setattr(
                    event,
                    "_private_companion_llm_planned_segment_ids",
                    tuple(range(len(planned[0]))),
                )
            return planned

        max_segments = max(
            1,
            _safe_int(
                self._segmented_setting("max_segments", event=event, umo=umo, default=3),
                3,
                1,
                8,
            ),
        )
        if llm_count >= max_segments:
            planned = [
                [
                    cleaned
                    for segment in segments
                    if (cleaned := apply_common_transforms(str(segment or "")))
                ]
                for segments in parsed_buffers
            ]
            if event is not None and len(planned) == 1 and len(planned[0]) >= 2:
                setattr(
                    event,
                    "_private_companion_llm_planned_segment_ids",
                    tuple(range(len(planned[0]))),
                )
            return planned

        result: list[list[list[str] | str]] = [list(segments) for segments in parsed_buffers]
        rule_processed: set[tuple[int, int]] = set()
        remaining = max_segments - llm_count
        candidate_indices = sorted(
            (
                (buffer_index, segment_index)
                for buffer_index, segments in enumerate(parsed_buffers)
                for segment_index in range(len(segments))
                if not has_fenced_llm_segment_marker(segments[segment_index])
            ),
            key=lambda position: (
                -len(str(parsed_buffers[position[0]][position[1]])),
                position[0],
                position[1],
            ),
        )
        for buffer_index, segment_index in candidate_indices:
            if remaining <= 0:
                break
            candidate = self._split_proactive_text(
                str(parsed_buffers[buffer_index][segment_index]),
                event=event,
                umo=umo,
                max_segments_override=remaining + 1,
            )
            additions = max(0, len(candidate) - 1)
            if additions <= 0 or additions > remaining:
                continue
            result[buffer_index][segment_index] = candidate
            rule_processed.add((buffer_index, segment_index))
            remaining -= additions

        flattened_buffers: list[list[str]] = []
        flattened_ids_by_buffer: list[list[int]] = []
        for buffer_index, segments in enumerate(result):
            flattened: list[str] = []
            flattened_ids: list[int] = []
            for segment_index, item in enumerate(segments):
                if isinstance(item, list):
                    for part in item:
                        clean_part = str(part or "").strip()
                        if clean_part:
                            flattened.append(clean_part)
                            flattened_ids.append(segment_index)
                    continue
                transformed = (
                    str(item or "").strip()
                    if (buffer_index, segment_index) in rule_processed
                    else apply_common_transforms(str(item or ""))
                )
                if transformed:
                    flattened.append(transformed)
                    flattened_ids.append(segment_index)
            flattened_buffers.append(flattened)
            flattened_ids_by_buffer.append(flattened_ids)
        if (
            event is not None
            and len(flattened_buffers) == 1
            and len(set(flattened_ids_by_buffer[0])) >= 2
        ):
            setattr(
                event,
                "_private_companion_llm_planned_segment_ids",
                tuple(flattened_ids_by_buffer[0]),
            )
        return flattened_buffers

    def _segment_llm_reply_chain(self, event: AstrMessageEvent, chain: list[Any]) -> tuple[list[list[Any]], bool, str]:
        working_chain = list(chain or [])
        # Strip reasoning/thinking chain from ALL Plain text joined together,
        # because the thinking chain can be split across multiple Plain
        # components (e.g. "  thinking" in one component, reasoning content
        # in another, "  /response" in a third).  Per-component cleaning
        # would fail since none of the fragments contain the complete
        # opening+content+closing pattern.
        _strip_chain_plain_thinking(self, working_chain)
        reply_prefix = [comp for comp in working_chain if self._is_reply_component(comp)]
        content_chain = [comp for comp in working_chain if not self._is_reply_component(comp)]
        if (
            bool(runtime_persona_setting(self, 'enable_proactive_quote_trigger_message', False))
            and bool(runtime_persona_setting(self, 'enable_quote_group_reply', True))
            and not reply_prefix
            and not self._chain_has_reply_component(working_chain)
        ):
            quote_message_id = self._group_current_reply_quote_message_id(
                event,
                text_or_chain=content_chain,
            )
            reply = self._make_reply_component(quote_message_id, event=event)
            if reply is not None:
                working_chain = [reply, *working_chain]

        llm_controlled = bool(
            runtime_persona_setting(self, "enable_llm_controlled_segmenting", False)
        )
        prepared_buffers: list[list[str]] = []
        if llm_controlled:
            raw_buffers: list[str] = []
            plain_buffer: list[str] = []

            def flush_plain_buffer() -> None:
                if not plain_buffer:
                    return
                raw_text = "".join(plain_buffer).strip()
                plain_buffer.clear()
                if raw_text:
                    raw_buffers.append(raw_text)

            for component in working_chain:
                if self._is_reply_component(component):
                    continue
                if isinstance(component, Plain):
                    plain_buffer.append(str(getattr(component, "text", "") or ""))
                    continue
                flush_plain_buffer()
            flush_plain_buffer()
            prepared_buffers = self._split_llm_controlled_text_buffers_for_event(
                event,
                raw_buffers,
            )
        prepared_iter = iter(prepared_buffers)

        def split_text_buffer(text: str) -> list[str]:
            if llm_controlled:
                try:
                    return next(prepared_iter)
                except StopIteration:
                    return [str(text or "").strip()]
            return self._split_proactive_text(text, event=event)

        chunks, changed, _split_changed, full_text = plan_component_chunks(
            working_chain,
            plain_type=Plain,
            split_text=split_text_buffer,
            strategies=component_strategies_from_owner(self),
            component_order=component_order_from_owner(self),
            classify=component_kind,
        )
        if not full_text:
            return [], False, ""
        full_text = sanitize_llm_segment_control_tokens(full_text)
        final_chunks = self._clean_segmented_reply_chunks(event, chunks) if changed else [chain]
        history_segments = getattr(
            event,
            "_private_companion_llm_history_segments",
            (),
        )
        if isinstance(history_segments, tuple) and len(history_segments) >= 2:
            planned_texts = [
                self._plain_result_body_text(chunk)
                for chunk in final_chunks
            ]
            planned_ids = getattr(
                event,
                "_private_companion_llm_planned_segment_ids",
                (),
            )
            if (
                planned_texts
                and all(planned_texts)
                and isinstance(planned_ids, tuple)
                and len(planned_ids) == len(planned_texts)
            ):
                setattr(
                    event,
                    "_private_companion_llm_planned_chunk_texts",
                    tuple(planned_texts),
                )
            else:
                for attr_name in (
                    "_private_companion_llm_history_segments",
                    "_private_companion_llm_planned_segment_ids",
                ):
                    try:
                        delattr(event, attr_name)
                    except AttributeError:
                        pass
        return final_chunks, changed, full_text

    async def _send_segmented_remainder_chain(
        self,
        event: AstrMessageEvent,
        chain: list[Any],
    ) -> str:
        """Send delayed chunks through a live platform route when the source event is proactive."""
        external_proactive = (
            str(getattr(event, "_private_companion_external_proactive_source", "") or "")
            == "proactive_chat"
        )
        proactive_delivery_umo = _single_line(
            getattr(event, "_private_companion_proactive_delivery_umo", ""),
            240,
        )
        if external_proactive or proactive_delivery_umo:
            umo = proactive_delivery_umo or _single_line(
                getattr(event, "unified_msg_origin", ""),
                240,
            )
            sender = getattr(self, "_send_chain_components", None)
            if not umo or not callable(sender):
                raise RuntimeError("主动分段补发缺少可用的平台发送入口")
            accepted = await sender(
                umo,
                list(chain),
                apply_decorating_hooks=False,
            )
            if not accepted:
                raise RuntimeError("主动分段补发未被平台接受")
            return "platform"
        try:
            await event.send(event.chain_result(chain))
        except Exception:
            await event.send(self._build_result_from_chain(chain))
        return "event"

    async def _send_segmented_llm_chain_remainder(
        self,
        event: AstrMessageEvent,
        chunks: list[list[Any]],
        *,
        previous_segment: str = "",
        source: str = "",
        started_at: float | None = None,
    ) -> None:
        """后台补发被动分段的剩余组件片段；只拆文本，媒体组件保持原子发送。"""
        prev = previous_segment
        total = len([item for item in chunks if item])
        sent_index = 0
        case_id = _single_line(getattr(event, "_private_companion_daily_review_case_id", ""), 20)
        proactive_delivery_umo = _single_line(
            getattr(event, "_private_companion_proactive_delivery_umo", ""),
            240,
        )
        scope = self._event_scope_key(event)
        async with self._segmented_remainder_lock(scope):
            for chunk in chunks:
                if not chunk:
                    continue
                sent_index += 1
                try:
                    preview = self._segmented_chunk_log_text(chunk)
                    outbound_chunk = chunk
                    drift_reason = self._segmented_remainder_context_drift_reason(
                        event,
                        previous_text=prev,
                        next_text=preview,
                        source=source,
                    )
                    if drift_reason:
                        if case_id:
                            self._update_daily_review_case(
                                case_id,
                                outcome="incomplete",
                                signals={"stop_reason": drift_reason, "segments_expected": total + 1, "segments_sent": sent_index},
                            )
                        logger.info(
                            "[PrivateCompanion] 分段剩余组件疑似上下文割裂，停止发送: source=%s reason=%s sent=%s/%s prev=%s next=%s",
                            source or "unknown",
                            drift_reason,
                            max(0, sent_index - 1),
                            total,
                            _single_line(prev, 120),
                            _single_line(preview, 120),
                        )
                        return
                    wait_for = prev or preview
                    delay = await self._calc_segmented_proactive_interval(wait_for, event=event)
                    if delay > 0:
                        await asyncio.sleep(delay)
                    recalled_message_id = await self._should_cancel_reply_for_missing_or_recalled_trigger(event)
                    if recalled_message_id:
                        if case_id:
                            self._update_daily_review_case(
                                case_id,
                                outcome="incomplete",
                                signals={"stop_reason": "trigger_recalled", "segments_expected": total + 1, "segments_sent": sent_index},
                            )
                        logger.info(
                            "[PrivateCompanion] 触发消息已撤回或发送前不可见，停止发送分段剩余组件: source=%s message_id=%s sent=%s/%s",
                            source or "unknown",
                            recalled_message_id,
                            max(0, sent_index - 1),
                            total,
                        )
                        return
                    if chunk and all(isinstance(comp, Plain) for comp in chunk):
                        normalized_segment = "".join(str(getattr(comp, "text", "") or "") for comp in chunk).strip()
                        provider_error_checker = getattr(self, "_looks_like_internal_provider_error_text", None)
                        if (
                            bool(runtime_persona_setting(self, 'enable_framework_error_leak_guard', True))
                            and callable(provider_error_checker)
                        ):
                            try:
                                if normalized_segment and provider_error_checker(normalized_segment):
                                    logger.warning(
                                        "[PrivateCompanion] 分段剩余组件命中 Provider 错误正文，停止补发: source=%s preview=%s",
                                        source or "unknown",
                                        _single_line(normalized_segment, 180),
                                    )
                                    return
                            except Exception:
                                pass
                        normalizer = getattr(self, "_normalize_tts_tags", None)
                        if callable(normalizer) and re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", normalized_segment, flags=re.IGNORECASE):
                            try:
                                normalized_segment = str(normalizer(normalized_segment) or normalized_segment).strip()
                            except Exception:
                                pass
                        if (
                            bool(runtime_persona_setting(self, 'enable_tts_enhancement', False))
                            and re.search(r"<tts\b[^>]*>.*?</tts>", normalized_segment, flags=re.IGNORECASE | re.DOTALL)
                        ):
                            processor = getattr(self, "_process_tts_tags", None)
                            if callable(processor):
                                fallback_plain = re.sub(r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>", "", normalized_segment, flags=re.IGNORECASE).strip()
                                processed_chunk = await processor(normalized_segment, event, fallback_plain=fallback_plain)
                                if processed_chunk:
                                    outbound_chunk = processed_chunk
                        elif re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", normalized_segment, flags=re.IGNORECASE):
                            cleaned = re.sub(r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>", "", normalized_segment, flags=re.IGNORECASE).strip()
                            outbound_chunk = [Plain(cleaned)] if cleaned else []
                    if not outbound_chunk:
                        continue
                    sanitized_chunk: list[Any] = []
                    leaked_tools: list[str] = []
                    for component in outbound_chunk:
                        if not isinstance(component, Plain):
                            sanitized_chunk.append(component)
                            continue
                        original_text = str(getattr(component, "text", "") or "")
                        visible_text = self._sanitize_segmented_plain_text(event, original_text)
                        cleaned_text, calls = self._strip_plaintext_tool_call_envelopes(
                            visible_text
                        )
                        leaked_tools.extend(str(item.get("name") or "") for item in calls)
                        if cleaned_text:
                            sanitized_chunk.append(
                                Plain(cleaned_text)
                                if calls or cleaned_text != original_text else component
                            )
                    if leaked_tools:
                        logger.warning(
                            "[PrivateCompanion] 分段组件发送前已移除明文工具调用: tools=%s",
                            ",".join(leaked_tools),
                        )
                    outbound_chunk = sanitized_chunk
                    if not outbound_chunk:
                        continue
                    hit = self._forbidden_recall_hit(self._chain_text_for_forbidden_recall(outbound_chunk))
                    if hit:
                        if case_id:
                            self._update_daily_review_case(
                                case_id,
                                outcome="incomplete",
                                signals={"stop_reason": "forbidden_recall", "segments_expected": total + 1, "segments_sent": sent_index},
                            )
                        logger.warning("[PrivateCompanion] 分段剩余组件命中违禁词，停止发送: word=%s", _single_line(hit, 40))
                        return
                    delivery_path = await self._send_segmented_remainder_chain(
                        event,
                        outbound_chunk,
                    )
                    if case_id:
                        self._update_daily_review_case(
                            case_id,
                            append_output=self._segmented_chunk_log_text(outbound_chunk),
                            outcome="delivered" if sent_index >= total else "delivery_pending",
                            signals={"segments_expected": total + 1, "segments_sent": sent_index + 1},
                        )
                    logger.info(
                        "[PrivateCompanion] 分段 LLM 剩余组件已发送: source=%s delivery=%s index=%s/%s preview=%s",
                        source or "unknown",
                        delivery_path,
                        sent_index,
                        total,
                        _single_line(preview, 120),
                    )
                    prev = preview
                except asyncio.CancelledError:
                    if case_id:
                        self._update_daily_review_case(
                            case_id,
                            outcome="incomplete",
                            signals={"stop_reason": "task_cancelled", "segments_expected": total + 1, "segments_sent": sent_index},
                        )
                    raise
                except Exception as exc:
                    if (
                        str(getattr(event, "_private_companion_external_proactive_source", "") or "")
                        == "proactive_chat"
                        or proactive_delivery_umo
                    ):
                        if case_id:
                            self._update_daily_review_case(
                                case_id,
                                outcome="delivery_failed",
                                signals={"segments_expected": total + 1, "segments_sent": sent_index},
                            )
                        logger.warning(
                            "[PrivateCompanion] 主动分段 LLM 剩余组件发送失败: source=%s error=%s",
                            source or "unknown",
                            _single_line(exc, 160),
                            exc_info=True,
                        )
                        return
                    try:
                        await event.send(self._build_result_from_chain(outbound_chunk))
                        if case_id:
                            self._update_daily_review_case(
                                case_id,
                                append_output=self._segmented_chunk_log_text(outbound_chunk),
                                outcome="delivered" if sent_index >= total else "delivery_pending",
                                signals={"segments_expected": total + 1, "segments_sent": sent_index + 1},
                            )
                        logger.info(
                            "[PrivateCompanion] 分段 LLM 剩余组件已发送: source=%s index=%s/%s preview=%s",
                            source or "unknown",
                            sent_index,
                            total,
                            _single_line(self._segmented_chunk_log_text(chunk), 120),
                        )
                        prev = self._segmented_chunk_log_text(chunk)
                    except Exception:
                        if case_id:
                            self._update_daily_review_case(
                                case_id,
                                outcome="delivery_failed",
                                signals={"segments_expected": total + 1, "segments_sent": sent_index},
                            )
                        logger.warning(
                            "[PrivateCompanion] 分段 LLM 剩余组件发送失败: source=%s error=%s",
                            source or "unknown",
                            _single_line(exc, 160),
                            exc_info=True,
                        )
                        return

    async def _send_segmented_llm_reply_remainder(
        self,
        event: AstrMessageEvent,
        segments: list[str],
        *,
        previous_segment: str = "",
        source: str = "",
        started_at: float | None = None,
    ) -> None:
        """后台补发被动分段的剩余片段，避免阻塞主链首包。"""
        prev = previous_segment
        total = len([item for item in segments if str(item or "").strip()])
        sent_index = 0
        for segment in segments:
            segment = str(segment or "").strip()
            if not segment:
                continue
            segment, leaked_calls = self._strip_plaintext_tool_call_envelopes(segment)
            if leaked_calls:
                logger.warning(
                    "[PrivateCompanion] 分段文本发送前已移除明文工具调用: tools=%s",
                    ",".join(str(item.get("name") or "") for item in leaked_calls),
                )
            if not segment:
                continue
            sent_index += 1
            try:
                drift_reason = self._segmented_remainder_context_drift_reason(
                    event,
                    previous_text=prev,
                    next_text=segment,
                    source=source,
                )
                if drift_reason:
                    logger.info(
                        "[PrivateCompanion] 分段剩余片段疑似上下文割裂，停止发送: source=%s reason=%s sent=%s/%s prev=%s next=%s",
                        source or "unknown",
                        drift_reason,
                        max(0, sent_index - 1),
                        total,
                        _single_line(prev, 120),
                        _single_line(segment, 120),
                    )
                    return
                wait_for = prev or segment
                delay = await self._calc_segmented_proactive_interval(wait_for, event=event)
                if delay > 0:
                    await asyncio.sleep(delay)
                recalled_message_id = await self._should_cancel_reply_for_missing_or_recalled_trigger(event)
                if recalled_message_id:
                    logger.info(
                        "[PrivateCompanion] 触发消息已撤回或发送前不可见，停止发送分段剩余片段: source=%s message_id=%s sent=%s/%s",
                        source or "unknown",
                        recalled_message_id,
                        max(0, sent_index - 1),
                        total,
                    )
                    return
                sent_tts_chain = False
                normalized_segment = segment
                normalizer = getattr(self, "_normalize_tts_tags", None)
                if callable(normalizer) and re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", normalized_segment, flags=re.IGNORECASE):
                    try:
                        normalized_segment = str(normalizer(normalized_segment) or normalized_segment).strip()
                    except Exception:
                        pass
                if (
                    bool(runtime_persona_setting(self, 'enable_tts_enhancement', False))
                    and re.search(r"<tts\b[^>]*>.*?</tts>", normalized_segment, flags=re.IGNORECASE | re.DOTALL)
                ):
                        processor = getattr(self, "_process_tts_tags", None)
                        if callable(processor):
                            fallback_plain = re.sub(r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>", "", normalized_segment, flags=re.IGNORECASE).strip()
                            chain = await processor(normalized_segment, event, fallback_plain=fallback_plain)
                            if chain:
                                hit = self._forbidden_recall_hit(self._chain_text_for_forbidden_recall(chain))
                                if hit:
                                    logger.warning("[PrivateCompanion] 分段 TTS 剩余片段命中违禁词，停止发送: word=%s", _single_line(hit, 40))
                                    return
                                try:
                                    await event.send(event.chain_result(chain))
                                except Exception:
                                    await event.send(self._build_result_from_chain(chain))
                                sent_tts_chain = True
                if not sent_tts_chain:
                    outbound = re.sub(r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>", "", normalized_segment, flags=re.IGNORECASE).strip() or segment
                    hit = self._forbidden_recall_hit(outbound)
                    if hit:
                        logger.warning("[PrivateCompanion] 分段剩余片段命中违禁词，停止发送: word=%s", _single_line(hit, 40))
                        return
                    await event.send(event.plain_result(outbound))
                logger.info(
                    "[PrivateCompanion] 分段 LLM 剩余片段已发送: source=%s index=%s/%s preview=%s",
                    source or "unknown",
                    sent_index,
                    total,
                    _single_line(segment, 120),
                )
                prev = segment
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "[PrivateCompanion] 分段 LLM 剩余片段发送失败: source=%s error=%s",
                    source or "unknown",
                    _single_line(exc, 160),
                    exc_info=True,
                )
                return

    @filter.on_decorating_result(priority=200)
    @_multi_persona_event_context
    async def attach_group_reply_quote(self, event: AstrMessageEvent, *args, **kwargs):
        """Bind reply quotes to text instead of a leading voice or image chunk."""
        result = None
        chain: list[Any] = []
        if self is None or not self.enabled:
            return
        try:
            result = event.get_result()
        except Exception as exc:
            logger.debug("[PrivateCompanion] 群聊补引用读取结果失败: %s", _single_line(exc, 120))
            return
        if result is None:
            return
        try:
            if hasattr(result, "is_llm_result") and not result.is_llm_result():
                return
        except Exception:
            pass
        try:
            chain = list(getattr(result, "chain", []) or [])
        except Exception as exc:
            logger.debug("[PrivateCompanion] 群聊补引用读取消息链失败: %s", _single_line(exc, 120))
            return
        if not chain:
            return

        delivery_chunks: list[list[Any]] = [chain]
        for attr_name in (
            "_private_companion_tts_reply_remainder",
            "_private_companion_reaction_expression_segmented_remainder",
        ):
            pending = getattr(event, attr_name, None)
            pending_chunks = pending.get("chunks") if isinstance(pending, dict) else None
            if not isinstance(pending_chunks, list):
                continue
            delivery_chunks.extend(
                chunk for chunk in pending_chunks if isinstance(chunk, list)
            )

        has_voice = any(
            isinstance(component, Record)
            for chunk in delivery_chunks
            for component in chunk
        )
        has_visible_text = any(
            isinstance(component, Plain)
            and bool(str(getattr(component, "text", "") or "").strip())
            for chunk in delivery_chunks
            for component in chunk
        )
        # Keep quotes whenever a visible text companion exists. A voice
        # component must not erase a quote needed by image/forward/vision
        # consumers or by a delayed text chunk from this reply.
        suppress_reply_reason = "voice_component" if has_voice and not has_visible_text else ""
        if not has_voice:
            official_tts_checker = getattr(
                self,
                "_should_defer_segmenting_to_astrbot_tts",
                None,
            )
            if callable(official_tts_checker):
                try:
                    if await official_tts_checker(event, result, chain):
                        suppress_reply_reason = "framework_tts"
                except Exception as exc:
                    logger.debug(
                        "[PrivateCompanion] 官方 TTS 引用预判失败: session=%s error=%s",
                        _single_line(getattr(event, "unified_msg_origin", ""), 120)
                        or "unknown",
                        _single_line(exc, 120),
                    )
        if suppress_reply_reason:
            cleaned_chunks = [
                self._without_reply_components(chunk) for chunk in delivery_chunks
            ]
            removed_count = sum(len(chunk) for chunk in delivery_chunks) - sum(
                len(chunk) for chunk in cleaned_chunks
            )
            primary_chunk = cleaned_chunks[0]
            pending_index = 1
            for attr_name in (
                "_private_companion_tts_reply_remainder",
                "_private_companion_reaction_expression_segmented_remainder",
            ):
                pending = getattr(event, attr_name, None)
                pending_chunks = (
                    pending.get("chunks") if isinstance(pending, dict) else None
                )
                if not isinstance(pending_chunks, list):
                    continue
                replacement_count = len(
                    [chunk for chunk in pending_chunks if isinstance(chunk, list)]
                )
                pending["chunks"] = cleaned_chunks[
                    pending_index : pending_index + replacement_count
                ]
                pending_index += replacement_count
            try:
                result.chain = primary_chunk
            except Exception:
                event.set_result(self._build_result_from_chain(primary_chunk))
            logger.info(
                "[PrivateCompanion] 语音回复已移除孤立消息引用: session=%s reason=%s removed=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120)
                or "unknown",
                suppress_reply_reason,
                removed_count,
            )
            return

        existing_replies = [
            component
            for chunk in delivery_chunks
            for component in chunk
            if self._is_reply_component(component)
        ]
        if not existing_replies:
            if not bool(runtime_persona_setting(self, 'enable_proactive_quote_trigger_message', False)):
                return
            if not bool(runtime_persona_setting(self, 'enable_quote_group_reply', True)):
                return
            if self._proactive_only_blocks_passive_event(event, "enable_group_companion"):
                return
            try:
                quote_message_id = self._group_current_reply_quote_message_id(
                    event,
                    text_or_chain=flatten_component_chunks(delivery_chunks),
                )
            except Exception as exc:
                logger.debug("[PrivateCompanion] 群聊补引用计算引用目标失败: %s", _single_line(exc, 120))
                return
            if not quote_message_id:
                return
            try:
                reply = self._make_reply_component(quote_message_id, event=event)
            except Exception as exc:
                logger.debug("[PrivateCompanion] 群聊补引用构建消息链失败: %s", _single_line(exc, 120))
                return
            if reply is None:
                return
            existing_replies = [reply]

        bound_chunks, _changed = bind_reply_components_to_first_text(
            delivery_chunks,
            plain_type=Plain,
            classify=component_kind,
            reply_components=existing_replies,
        )
        if not bound_chunks:
            return
        primary_chunk = bound_chunks[0]
        pending_index = 1
        has_pending_delivery = False
        for attr_name in (
            "_private_companion_tts_reply_remainder",
            "_private_companion_reaction_expression_segmented_remainder",
        ):
            pending = getattr(event, attr_name, None)
            pending_chunks = pending.get("chunks") if isinstance(pending, dict) else None
            if not isinstance(pending_chunks, list):
                continue
            has_pending_delivery = True
            replacement_count = len(
                [chunk for chunk in pending_chunks if isinstance(chunk, list)]
            )
            pending["chunks"] = bound_chunks[pending_index : pending_index + replacement_count]
            pending_index += replacement_count
        if not has_pending_delivery and len(bound_chunks) > 1:
            # This result is still delivered as one message. Do not discard
            # text merely because quote binding split the in-memory chain.
            primary_chunk = flatten_component_chunks(bound_chunks)
        try:
            result.chain = primary_chunk
        except Exception:
            event.set_result(self._build_result_from_chain(primary_chunk))


    @filter.llm_tool(name="pc_qzone_view_feed")
    @_multi_persona_event_context
    async def pc_qzone_view_feed(
        self,
        event: AstrMessageEvent,
        user_id: str = "",
        target_scope: str = "",
        target_uin: str = "",
        pos: int = 0,
        like: bool = False,
        reply: bool = False,
        selector: str = "",
        fid: str = "",
        time_hint: str = "",
        **kwargs,
    ) -> str:
        """查看指定归属的 QQ 空间说说,可按需点赞或评论。

        Args:
            user_id(string): 兼容旧调用的明确 QQ 号；不能再作为省略目标时的默认值。
            target_scope(string): 可选归属：bot_self（兼容 self）、current_user 或 explicit_uin；用户原话已有明确“你/我”归属时工具也会语义校正，确实含糊时返回 needs_target。
            target_uin(string): target_scope=explicit_uin 时要查看的 QQ 号；可用 user_id 兼容旧调用。
            pos(number): 可选,说说位置,0 表示最新一条。
            like(boolean): 可选,是否给该条说说点赞。
            reply(boolean): 可选,是否按工具内部规则尝试评论。
            selector(string): 可选,自然语言选择器,如“最新”“第2条”“最后”；也可以填 fid。
            fid(string): 可选,明确指定说说 fid。
            time_hint(string): 可选,发布时间提示,如“今天下午6点多”或“18:20”。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_qzone_view_feed_impl(
            event,
            user_id=user_id,
            target_scope=target_scope,
            target_uin=target_uin,
            pos=pos,
            like=like,
            reply=reply,
            selector=selector,
            fid=fid,
            time_hint=time_hint,
            **kwargs,
        )

    @filter.llm_tool(name="pc_qzone_publish_feed")
    @_multi_persona_event_context
    async def pc_qzone_publish_feed(
        self,
        event: AstrMessageEvent,
        text: str = "",
        images: list[str] | None = None,
        image: str = "",
        image_path: str = "",
        image_url: str = "",
        use_latest_draft: bool = False,
        **kwargs,
    ) -> str:
        """发布一条 QQ 空间说说。必须通过 text 参数传入最终正文；如需带图,通过 images 或 image 传入图片。

        Args:
            text(string): 要发布到 QQ 空间的说说正文。
            images(list[string]): 可选,要随说说发布的本地图片路径或图片 URL 列表。
            image(string): 可选,单张图片的本地路径或图片 URL。
            image_path(string): 可选,单张本地图片路径。
            image_url(string): 可选,单张图片 URL。
            use_latest_draft(boolean): 可选,是否使用最近生成的生活说说草稿。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        if images:
            kwargs["images"] = images
        if image:
            kwargs["image"] = image
        if image_path:
            kwargs["image_path"] = image_path
        if image_url:
            kwargs["image_url"] = image_url
        if use_latest_draft:
            kwargs["use_latest_draft"] = use_latest_draft
        return await self._pc_qzone_publish_feed_impl(event, text, **kwargs)

    @filter.llm_tool(name="pc_qzone_reply_my_comment")
    @_multi_persona_event_context
    async def pc_qzone_reply_my_comment(
        self,
        event: AstrMessageEvent,
        comment_hint: str = "",
        selector: str = "latest",
        reply_hint: str = "",
    ) -> str:
        """检查并回复用户刚在 Bot 自己 QQ 空间动态下留下的评论。

        Args:
            comment_hint(string): 用户记得的评论全文、关键词或大致说法；无法精确映射 QQ 身份时用于唯一匹配。
            selector(string): 可选，优先检查“最新”动态。
            reply_hint(string): 可选，用户希望 Bot 使用的公开回复语气或内容提示。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"当前模式不可使用 QQ 空间工具。"}'
        try:
            result = await self._qzone_reply_my_comment(
                event,
                comment_hint=comment_hint,
                selector=selector,
                reply_hint=reply_hint,
            )
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"status": "error", "message": _single_line(exc, 160)}, ensure_ascii=False)

    @filter.llm_tool(name="pc_generate_photo")
    @_multi_persona_event_context
    async def pc_generate_photo(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        kind: str = "text2img",
        reference_image_path: str = "",
        image_size: str = "",
        send: bool = True,
        caption: str = "",
        scene_preset: str = "",
        **kwargs,
    ) -> str:
        """调用 Private Companion 生图/自拍/改图能力。

        Args:
            prompt(string): 画面描述、自拍要求或改图要求。若用户在前几轮文字剧情中已经明确换装，而本轮只说继续/再拍一张，必须把仍生效的具体服装展开写进 prompt（例如“角色当前仍穿 JK 校服，继续拍摄”），不能只写“继续”让下游猜测。
            kind(string): text2img/selfie/sticker/edit。角色本人以自拍、背影、侧脸或环境人像等任何形式出镜时用 selfie；角色表情包/贴纸用 sticker；不含角色本人的普通场景、物件、风景用 text2img；改图用 edit。
            reference_image_path(string): 可选，本地图片路径或图片 URL；edit 必填，selfie 可留空自动使用人设参考图/当天基础穿搭图。本轮引用图片会由工具自动解析，无需猜测路径；没有引用图片时不会自动复用上一张成图。合影需要本轮用户消息/引用消息中的其他人物参考图，或在 prompt 中明确点名已绑定可用参考图的关系网角色；后者由工具自动选图，单独填写或猜测此参数不能授权合影。当天基础穿搭只是默认基线，近期对话已发生的换装优先。
            image_size(string): 可选，在线图片 API 尺寸，如 1024x1024。
            send(boolean): 是否生成后直接发送到当前会话，默认 true。
            caption(string): 发送图片时附带的短文字。
            scene_preset(string): 可选场景预设建议，如 角色自拍/COS自拍/日常穿搭/镜前穿搭/头像特写/房间日常/可拍画面/表情包场景；不会覆盖用户原话或参考图强约束。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_generate_photo"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        inbound_text = str(getattr(event, "message_str", "") or "")
        reaction_kind = _single_line(kind, 24).casefold() in {
            "sticker",
            "emoji",
            "meme",
            "表情包",
            "贴纸",
        } or bool(re.search(r"(?:表情包|反应图|贴纸|梗图|斗图)", str(prompt or ""), flags=re.I))
        if (
            reaction_kind
            and self._reaction_expression_opt_out_requested(inbound_text)
            and not self._photo_generation_instruction_matches(inbound_text)
        ):
            return json.dumps(
                {
                    "status": "skipped",
                    "success": True,
                    "generated": False,
                    "sent": False,
                    "skip_reason": "explicit_opt_out",
                    "message": "用户本轮明确要求不发表情包",
                    "must_not_claim_sent": True,
                    "final_response_instruction": "尊重用户边界，只继续自然文字回复。",
                },
                ensure_ascii=False,
            )
        reaction_authorization = self._reaction_expression_authorization(event)
        if reaction_authorization and not self._photo_generation_instruction_matches(
            getattr(event, "message_str", "")
        ):
            return json.dumps(
                {
                    "status": "skipped",
                    "success": True,
                    "generated": False,
                    "sent": False,
                    "message": "本轮没有显式生图请求，继续自然文字回复即可。",
                    "must_not_claim_sent": True,
                    "final_response_instruction": "不要解释内部工具边界，按原语境继续自然文字回复。",
                },
                ensure_ascii=False,
            )
        return await self._pc_generate_photo_impl(
            event,
            prompt=prompt,
            kind=kind,
            reference_image_path=reference_image_path,
            image_size=image_size,
            send=send,
            caption=caption,
            scene_preset=scene_preset,
            **kwargs,
        )

    @filter.llm_tool(name="pc_send_current_media")
    @_multi_persona_event_context
    async def pc_send_current_media(
        self,
        event: AstrMessageEvent,
        media_path: str = "",
        caption: str = "",
        destination: str = "current",
        **kwargs,
    ) -> str:
        """发送本轮或紧邻上一轮其他工具刚生成、但尚未投递的本地图片。

        Args:
            media_path(string): 最近一次工具明确返回的本地图片路径；仅支持 AstrBot 临时目录或本插件成图目录，不得猜测路径。
            caption(string): 可选，随图片发送的一句自然短文；不要填写发送状态回执。
            destination(string): current（默认，发到当前会话）或 requester_private（仅在当前请求者明确说“私聊发我”等要求时，私聊发给请求者本人）；不能指定第三方。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_generate_photo"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用当前媒体投递工具。"}'
        return await self._pc_send_current_media_impl(
            event,
            media_path=media_path,
            caption=caption,
            destination=destination,
            **kwargs,
        )

    @filter.llm_tool(name="pc_find_reaction_image")
    @_multi_persona_event_context
    async def pc_find_reaction_image(
        self,
        event: AstrMessageEvent,
        query: str = "",
        search_context: str = "",
        meme_only: bool = True,
        send: bool = True,
        caption: str = "",
        purpose: str = "",
        emotion: str = "",
        intensity: int = 0,
        spontaneous: bool = False,
        candidate_queries: str = "",
        **kwargs: Any,
    ) -> str:
        """从 Private Companion 自有表情包素材库检索并发送一张已有图片。

        Args:
            query(string): 表情或图片需求，例如“震惊又无语的反应图”。
            search_context(string): 可选，当前对话语境或希望表达的情绪。
            meme_only(boolean): 是否只检索标记为表情包的图片，默认 true。
            send(boolean): 是否直接发送到当前会话，默认 true。
            caption(string): send=true 时必填；与图片一起发送的完整可见正文，图片不能替代正文。
            purpose(string): 自发表情实验的沟通用途，例如安慰、轻吐槽或分享开心；显式找图时可留空。
            emotion(string): 自发表情实验希望传达的情绪。
            intensity(int): 自发表情实验的表达强度，0-5。
            spontaneous(boolean): 是否为模型在普通闲聊中自主选择的表情表达，默认 false；用户明确要求找图时不要开启。
            candidate_queries(string): 自发表情实验的少量候选检索说法，可用分号分隔或传 JSON 字符串数组。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            if self is not None:
                self._log_reaction_expression_event(
                    event,
                    stage="decision",
                    decision="skip",
                    reason="proactive_only",
                    scope=self._reaction_expression_scope(event),
                    status="disabled",
                    found=False,
                    sent=False,
                )
            return json.dumps(
                {
                    "status": "disabled",
                    "success": False,
                    "found": False,
                    "sent": False,
                    "message": "主动消息专用模式下不可使用图库表情工具。",
                },
                ensure_ascii=False,
            )
        # AstrBot may inject a host-owned ``context`` keyword. Keep the public
        # schema on ``search_context`` so host context objects never become
        # model-controlled search text; only a direct legacy string can fill an
        # absent search_context value.
        legacy_context = kwargs.get("context")
        if not search_context and isinstance(legacy_context, str):
            search_context = legacy_context
        inbound_text = str(getattr(event, "message_str", "") or "")
        if (
            self._reaction_expression_opt_out_requested(inbound_text)
            and not self._reaction_expression_explicit_request_matches(inbound_text)
        ):
            return json.dumps(
                {
                    "status": "skipped",
                    "success": True,
                    "found": False,
                    "sent": False,
                    "decision": "skip",
                    "skip_reason": "explicit_opt_out",
                    "message": "用户本轮明确要求不发表情包",
                    "must_not_claim_sent": True,
                    "final_response_instruction": "尊重用户边界，只继续自然文字回复。",
                },
                ensure_ascii=False,
            )
        reaction_authorization = self._reaction_expression_authorization(event)
        if reaction_authorization and not reaction_authorization.get("authorized"):
            return json.dumps(
                self._reaction_expression_skip_result(
                    _single_line(reaction_authorization.get("reason"), 80)
                    or "not_authorized",
                    event=event,
                ),
                ensure_ascii=False,
            )
        if reaction_authorization and reaction_authorization.get("consumed"):
            return json.dumps(
                self._reaction_expression_skip_result(
                    "authorization_consumed",
                    event=event,
                ),
                ensure_ascii=False,
            )
        send_requested = self._reaction_expression_bool_arg(send, True)
        visible_caption = self._sanitize_photo_tool_caption(caption, limit=500)
        if send_requested and not visible_caption:
            return json.dumps(
                self._reaction_expression_skip_result(
                    "missing_visible_caption",
                    event=event,
                    message="发送表情包前需要同时提供一条完整的可见正文",
                ),
                ensure_ascii=False,
            )
        if send_requested:
            caption = visible_caption
        spontaneous_call = self._reaction_expression_bool_arg(
            spontaneous, False
        ) or bool(reaction_authorization.get("authorized"))
        if spontaneous_call:
            return await self._pc_reaction_expression_impl(
                event,
                query=query,
                context=search_context,
                meme_only=meme_only,
                send=send,
                caption=visible_caption,
                purpose=purpose,
                emotion=emotion,
                intensity=intensity,
                candidate_queries=candidate_queries,
            )
        return await self._pc_find_reaction_image_impl(
            event,
            query=query,
            search_context=search_context,
            meme_only=meme_only,
            send=send,
            caption=caption,
        )

    @filter.llm_tool(name="pc_manage_memo")
    @_multi_persona_event_context
    async def pc_manage_memo(
        self,
        event: AstrMessageEvent,
        action: str = "list",
        title: str = "",
        content: str = "",
        selector: str = "",
        due_at: str = "",
        repeat: str = "",
        color: str = "",
        remind_enabled: bool | None = None,
        include_completed: bool = False,
        status: str = "",
        query: str = "",
        clear_due: bool = False,
        clear_content: bool = False,
        confirmation_token: str = "",
    ) -> str:
        """在主要用户私聊中新增、查看、修改、完成、恢复、置顶或删除备忘便签。

        Args:
            action(string): list/get/create/update/complete/reopen/delete/cancel_delete/pin/unpin。
            title(string): 新增时的标题，或修改后的标题。
            content(string): 新增时的正文，或修改后的正文。
            selector(string): 要操作的便签标题、列表编号或便签 id。
            due_at(string): 可选提醒时间，可传绝对日期或“明早9点”“两小时后”等常见表达。
            repeat(string): 可选，none/daily/weekly/monthly/yearly。
            color(string): 可选，yellow/blue/green/rose/gray。
            remind_enabled(boolean): 可选，是否在到期时提醒。
            include_completed(boolean): list 时是否包含已完成便签。
            status(string): 可选，active/completed/all；用于筛选列表或限定编号所在视图。
            query(string): list 时可选，按标题或正文关键词筛选。
            clear_due(boolean): update 时是否清除到期时间和重复设置。
            clear_content(boolean): update 时是否清空正文。
            confirmation_token(string): 删除确认时原样传回首次 delete 返回的令牌。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","saved":false,"message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_manage_memo_impl(
            event,
            action=action,
            title=title,
            content=content,
            selector=selector,
            due_at=due_at,
            repeat=repeat,
            color=color,
            remind_enabled=remind_enabled,
            include_completed=include_completed,
            status=status,
            query=query,
            clear_due=clear_due,
            clear_content=clear_content,
            confirmation_token=confirmation_token,
        )

    @filter.llm_tool(name="pc_manage_schedule")
    @_multi_persona_event_context
    async def pc_manage_schedule(
        self,
        event: AstrMessageEvent,
        action: str = "list",
        selector: str = "",
    ) -> str:
        """按时间、序号或活动名查看、重新细化或取消主要用户的今日日程段。

        Args:
            action(string): list/regenerate/cancel。用户说删除、删掉、移除时使用 cancel。
            selector(string): 用户指定的时间、序号或活动关键词，例如“下午三点”“第二段”“整理房间”。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return json.dumps(
                {"status": "disabled", "saved": False, "message": "主动消息专用模式下不可管理日程。"},
                ensure_ascii=False,
            )
        try:
            is_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            is_private = ":FriendMessage:" in str(getattr(event, "unified_msg_origin", "") or "")
        if not is_private or not self._can_manage_private_companion(event):
            return json.dumps(
                {"status": "forbidden", "saved": False, "message": "只有主要用户可以在私聊中管理今日日程。"},
                ensure_ascii=False,
            )
        normalized_action = _single_line(action, 24).lower()
        normalized_action = {
            "delete": "cancel",
            "remove": "cancel",
            "删除": "cancel",
            "取消": "cancel",
            "移除": "cancel",
            "reset": "regenerate",
            "redo": "regenerate",
            "重置": "regenerate",
            "重做": "regenerate",
            "重新细化": "regenerate",
        }.get(normalized_action, normalized_action or "list")
        if normalized_action == "list":
            async with self._data_lock:
                plan = self.data.get("daily_plan", {})
                segments = self._collect_detail_segments(
                    plan if isinstance(plan, dict) else {},
                    {},
                    include_cancelled=True,
                )
                labels = [self._schedule_segment_label(segment) for segment in segments]
            return json.dumps(
                {
                    "status": "success",
                    "saved": False,
                    "action": "list",
                    "segments": labels,
                    "message": "\n".join(labels) if labels else "今天还没有可操作的日程。",
                },
                ensure_ascii=False,
            )
        if normalized_action == "cancel":
            ok, message = await self._cancel_daily_plan_segment_by_selector(selector)
            return json.dumps(
                {"status": "success" if ok else "error", "saved": ok, "action": "cancel", "message": message},
                ensure_ascii=False,
            )
        if normalized_action == "regenerate":
            ok, message, detail = await self._regenerate_daily_plan_segment_by_selector(
                selector,
                generate_detail_enhancement,
            )
            return json.dumps(
                {
                    "status": "success" if ok else "error",
                    "saved": ok,
                    "action": "regenerate",
                    "message": message,
                    "summary": _single_line(detail.get("summary"), 140) if isinstance(detail, dict) else "",
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {"status": "error", "saved": False, "message": "不支持的日程操作，请使用 list/regenerate/cancel。"},
            ensure_ascii=False,
        )

    @filter.llm_tool(name="pc_view_creative_work")
    @_multi_persona_event_context
    async def pc_view_creative_work(
        self,
        event: AstrMessageEvent,
        action: str = "get",
        selector: str = "",
        part: int = 0,
        max_chars: int = 6000,
    ) -> str:
        """只读查看 Bot 自己资料柜的真实库存、创作项目与正文。

        Args:
            action(string): list/get。list 列出资料柜库存与作品；get 读取指定作品正文。
            selector(string): 作品准确标题、项目 id 或列表编号；留空时 get 默认读取最近一篇。
            part(number): 可选，明确读取第几部分，按 1 开始；0 表示从第一部分起按预算读取。
            max_chars(number): 可选，本次最多返回正文字符数，默认 6000。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_view_creative_work_impl(
            event,
            action=action,
            selector=selector,
            part=part,
            max_chars=max_chars,
        )

    @filter.llm_tool(name="pc_get_group_id_by_name")
    @_multi_persona_event_context
    async def pc_get_group_id_by_name(self, event: AstrMessageEvent, **kwargs) -> str:
        """按群名关键词查询机器人已加入的群号。

        Args:
            group_name(string): 群名关键词或群号。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_get_group_id_by_name_impl(event, **kwargs)

    @filter.llm_tool(name="pc_get_user_id_by_name")
    @_multi_persona_event_context
    async def pc_get_user_id_by_name(self, event: AstrMessageEvent, **kwargs) -> str:
        """按关系网名称、别名、群名片或昵称解析群友 QQ。

        Args:
            group_id(string): 目标群号；私聊中可填写要查询的群号。
            nickname(string): 关系网名称、别名、群名片、昵称或 QQ。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_get_user_id_by_name_impl(event, **kwargs)

    @filter.llm_tool(name="pc_query_relation_person")
    @_multi_persona_event_context
    async def pc_query_relation_person(self, event: AstrMessageEvent, **kwargs) -> str:
        """查询关系网里是否认识某个 QQ、昵称或别名。

        Args:
            keyword(string): QQ 号、昵称、别名，或用户原话里最像名字的部分。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_query_relation_person_impl(event, **kwargs)

    @filter.llm_tool(name="pc_get_specified_group_members")
    @_multi_persona_event_context
    async def pc_get_specified_group_members(self, event: AstrMessageEvent, **kwargs) -> str:
        """查询指定群成员,并标记是否已在关系网中登记。

        Args:
            group_id(string): 目标群号。
            keyword(string): 可选筛选关键词、昵称、群名片或 QQ。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_get_specified_group_members_impl(event, **kwargs)

    @filter.llm_tool(name="pc_query_interaction")
    @_multi_persona_event_context
    async def pc_query_interaction(self, event: AstrMessageEvent, **kwargs) -> str:
        """查询 Bot 与某个私聊对象或群聊的近期互动摘要。

        Args:
            scope(string): private/group/auto。
            user_hint(string): 私聊对象/群成员 QQ、关系网名称、别名或显示名。
            group_hint(string): 群号或群名；和 user_hint 同时提供时查询这个人在该群的近期发言。
            hint(string): 不确定目标类型时的原始称呼。
            hours(number): 查询最近多少小时，默认 72。
            limit(number): 返回多少条候选互动线索，默认 36。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_query_interaction_impl(event, **kwargs)

    @filter.llm_tool(name="pc_relay_message")
    @_multi_persona_event_context
    async def pc_relay_message(self, event: AstrMessageEvent, **kwargs) -> str:
        """统一转述入口：把用户明确要求转发/转述/提醒的话发送到群聊或私聊。

        Args:
            destination(string): group/private/auto。发群填 group, 私聊填 private, 不确定填 auto。
            group_hint(string): 群号或群名。群聊转私聊时可用于按群成员名解析 QQ。
            recipient_hint(string): 收件人 QQ、关系网名称、别名、群名片或昵称。
            message(string): 最终要发送的内容。
            at_recipient(boolean): 发到群时是否 @ recipient_hint。
            relay_mode(string): persona/soft/original。默认 persona。
            sensitive_confirmed(boolean): 敏感内容是否已获得用户确认。
            delay_until_recipient_seen(boolean): 是否等目标群友在群里出现后再转述。
            need_receipt(boolean): 私聊询问时是否等待对方回复并带回结果。
            confirm_before_report(boolean): 带回私聊回复前是否先向对方确认。
            expire_hours(number): 延迟转述有效小时数。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_relay_message_impl(event, **kwargs)

    @filter.llm_tool(name="pc_send_to_group")
    @_multi_persona_event_context
    async def pc_send_to_group(self, event: AstrMessageEvent, **kwargs) -> str:
        """向指定群聊发送消息,可按 QQ/关系网名称/别名/群名片 @ 群友。

        Args:
            group_id(string): 目标群号。
            message(string): 最终要发送的转述文本。
            at_user(string): 可选,要 @ 的 QQ、关系网名称、别名、群名片或昵称。
            relay_mode(string): persona/soft/original。
            sensitive_confirmed(boolean): 敏感内容是否已获得用户确认。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        result = await self._pc_send_to_group_impl(event, **kwargs)
        if str(result or "").startswith("消息已发送"):
            setattr(
                event,
                "private_companion_atrelay_tool_result",
                {
                    "status": "success",
                    "destination": "group",
                    "final_reply": "带到了。",
                    "final_reply_reference": "参考意图：转述已经成功发到目标群；只给用户一个很短的成功回执，不要复述转述正文，也不要写工具执行状态。",
                    "sent_text": _single_line(kwargs.get("message") or kwargs.get("text") or kwargs.get("content") or kwargs.get("msg"), 800),
                    "recipient": _single_line(kwargs.get("at_user") or kwargs.get("at") or kwargs.get("target_user") or kwargs.get("user_id"), 80),
                    "group_id": _single_line(kwargs.get("group_id") or kwargs.get("group") or kwargs.get("target_group"), 40),
                },
            )
        return result

    @filter.llm_tool(name="pc_send_to_private_user")
    @_multi_persona_event_context
    async def pc_send_to_private_user(self, event: AstrMessageEvent, **kwargs) -> str:
        """向指定平台用户 ID 发送私聊消息。

        Args:
            user_id(string): 目标用户 ID；OneBot 通常是 QQ 号，QQ 官方机器人通常是 openid/平台用户 ID。
            message(string): 最终要发送的转述文本。
            relay_mode(string): persona/soft/original。
            sensitive_confirmed(boolean): 敏感内容是否已获得用户确认。
            need_receipt(boolean): 是否等待对方回复并带回结果。
            confirm_before_report(boolean): 带回私聊回复前是否先向对方确认。
            receipt_expire_hours(number): 等待回执的有效小时数。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        result = await self._pc_send_to_private_user_impl(event, **kwargs)
        if str(result or "").startswith("已向") and "发送私聊消息" in str(result or ""):
            need_receipt = self._atrelay_bool_flag(
                kwargs.get("need_receipt", kwargs.get("wait_for_reply", kwargs.get("receipt", kwargs.get("report_back", False))))
            )
            setattr(
                event,
                "private_companion_atrelay_tool_result",
                {
                    "status": "success",
                    "destination": "private",
                    "final_reply": "带到了，有回复我再告诉你。" if need_receipt else "带到了。",
                    "final_reply_reference": (
                        "参考意图：转述已经成功发给目标私聊用户，并且如果对方回复会再告诉当前用户；只给一个很短的成功回执。"
                        if need_receipt
                        else "参考意图：转述已经成功发给目标私聊用户；只给用户一个很短的成功回执，不要复述转述正文，也不要写工具执行状态。"
                    ),
                    "sent_text": _single_line(kwargs.get("message") or kwargs.get("text") or kwargs.get("content") or kwargs.get("msg"), 800),
                    "recipient": _single_line(kwargs.get("user_id") or kwargs.get("qq") or kwargs.get("target_user") or kwargs.get("target"), 128),
                },
            )
        return result

    @filter.llm_tool(name="pc_send_to_groups")
    @_multi_persona_event_context
    async def pc_send_to_groups(self, event: AstrMessageEvent, **kwargs) -> str:
        """向多个群发送同一条通知。

        Args:
            group_ids(string): 目标群号,可用逗号、空格或换行分隔。
            message(string): 最终要发送的转述文本。
            at_user(string): 可选,要 @ 的 QQ、关系网名称、别名、群名片或昵称。
            relay_mode(string): persona/soft/original。
            sensitive_confirmed(boolean): 敏感内容是否已获得用户确认。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_send_to_groups_impl(event, **kwargs)

    @filter.llm_tool(name="pc_send_to_private_users")
    @_multi_persona_event_context
    async def pc_send_to_private_users(self, event: AstrMessageEvent, **kwargs) -> str:
        """向多个平台用户 ID 发送同一条私聊转述。

        Args:
            user_ids(string): 目标用户 ID,可用逗号、空格或换行分隔；OneBot 通常是 QQ 号，QQ 官方机器人通常是 openid/平台用户 ID。
            message(string): 最终要发送的转述文本。
            relay_mode(string): persona/soft/original。
            sensitive_confirmed(boolean): 敏感内容是否已获得用户确认。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_send_to_private_users_impl(event, **kwargs)

    @filter.llm_tool(name="pc_schedule_group_relay")
    @_multi_persona_event_context
    async def pc_schedule_group_relay(self, event: AstrMessageEvent, **kwargs) -> str:
        """挂起一条群聊转述,等目标用户在群里发言后自动 @ 并转述。

        Args:
            group_id(string): 目标群号。
            at_user(string): 目标 QQ、关系网名称、别名、群名片或昵称。
            message(string): 最终要发送的转述文本。
            relay_mode(string): persona/soft/original。
            sensitive_confirmed(boolean): 敏感内容是否已获得用户确认。
            expire_hours(number): 挂起有效小时数。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_schedule_group_relay_impl(event, **kwargs)

    async def _append_environment_perception_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        marker = "<!-- private_companion_environment_v1 -->"
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        if marker in current_prompt or marker in current_turn_prompt:
            return
        environment_section = await self._format_environment_perception_prompt_section(event)
        environment_injection = str(environment_section.get("content") or "")
        if environment_injection:
            placement = "prompt" if self._append_turn_prompt_fragment_by_position(
                req,
                marker,
                environment_injection,
                title=str(environment_section.get("title") or "环境感知"),
                priority=30,
                source="environment",
            ) else "system_prompt"
            if placement == "system_prompt":
                req.system_prompt = f"{current_prompt}\n\n{marker}\n{environment_injection}".strip()
            await self._record_request_prompt_fragment(
                event,
                title="请求级环境感知注入",
                key="environment.request",
                text=environment_injection,
                source="environment",
                metadata={"注入位置": placement},
            )

    @staticmethod
    def _normalize_passive_injection_position(value: Any) -> str:
        text = str(value or "").strip().lower()
        aliases = {
            "auto": "auto",
            "自动": "auto",
            "cache": "auto",
            "cache_friendly": "auto",
            "缓存友好": "auto",
            "prompt": "prompt",
            "request": "prompt",
            "turn": "prompt",
            "tail": "prompt",
            "user_prompt": "prompt",
            "current_prompt": "prompt",
            "当前请求": "prompt",
            "当前请求末尾": "prompt",
            "请求末尾": "prompt",
            "用户消息末尾": "prompt",
            "system": "system_prompt",
            "system_prompt": "system_prompt",
            "系统提示": "system_prompt",
            "系统提示词": "system_prompt",
            "强约束": "system_prompt",
        }
        return aliases.get(text, text if text in {"auto", "prompt", "system_prompt"} else "prompt")

    def _normalize_persona_voice_text(self, value: Any, *, max_chars: int = 1200) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = re.sub(r"\r\n?", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text[:max_chars].strip()

    def _format_persona_voice_channel_prompt(self, channel: str, *, include_heading: bool = True) -> str:
        if not bool(runtime_persona_setting(self, 'enable_persona_voice_channels', True)):
            return ""
        channel = str(channel or "").strip().lower()
        specs = {
            "conversation": (
                "对话风格",
                "persona_conversation_voice_prompt",
                "只用于私聊/群聊里真正说出口的聊天回复。不要把创作腔、日程计划或内心分析写进外发消息；用户要求详细说明时可优先保证信息完整。",
            ),
            "creative": (
                "创作风格",
                "persona_creative_voice_prompt",
                "只用于日记、QQ 空间、私下创作、文案和公开动态。允许比聊天更完整,但仍应像角色本人写的,避免模型作文、升华总结和营销文案腔。",
            ),
            "planning": (
                "计划风格",
                "persona_planning_voice_prompt",
                "只影响日程、计划、候选排序和行动倾向。这里描述角色会怎样安排自己、被什么驱动、什么时候收住,不是最终聊天台词。",
            ),
            "inner": (
                "内心活动风格",
                "persona_inner_voice_prompt",
                "只用于内部动机、念头、犹豫和状态余波。它默认不可直接外发,不能泄露系统、插件、模型或自我分析过程。",
            ),
            "proactive": (
                "主动开口风格",
                "persona_proactive_voice_prompt",
                "只用于把主动动机改写成最终私聊/群聊开口。优先具体由头、低压力、短句和可接话落点；不要写成回复空气、任务汇报或询问是否继续。",
            ),
        }
        label, attr, note = specs.get(channel, ("表达风格", f"persona_{channel}_voice_prompt", "只在对应链路使用。"))
        text = self._normalize_persona_voice_text(
            runtime_persona_setting(self, attr, ""),
            max_chars=1400,
        )
        if not text:
            return ""
        body = f"{text}\n使用边界：{note}"
        return f"【人格标准化：{label}】\n{body}" if include_heading else body

    def _format_proactive_voice_prompt(self) -> str:
        parts: list[str] = []
        base = self._normalize_persona_voice_text(runtime_persona_setting(self, 'reply_style_prompt', ""), max_chars=900)
        if base:
            parts.append(
                "【主动消息基础表达约束】\n"
                f"{base}\n"
                "这里只保留句数、口语化和简洁度等通用约束；不要把普通被动接话方式直接当成主动开口。"
            )
        proactive = self._format_persona_voice_channel_prompt("proactive")
        if proactive:
            parts.append(proactive)
        conversation = self._format_persona_voice_channel_prompt("conversation")
        if conversation and not proactive:
            parts.append(
                conversation
                + "\n补充边界：当前没有单独配置主动开口风格,因此只把对话风格作为轻量回退；仍必须围绕主动由头自然开口。"
            )
        return "\n\n".join(part for part in parts if part).strip()

    def _format_reply_style_prompt(self, *, include_heading: bool = True) -> str:
        text = str(runtime_persona_setting(self, 'reply_style_prompt', "") or "").strip()
        persona_voice = self._format_persona_voice_channel_prompt(
            "conversation",
            include_heading=include_heading,
        )
        if not text and not persona_voice:
            return ""
        text = self._normalize_persona_voice_text(text)
        parts: list[str] = []
        if text:
            parts.append(text)
        if persona_voice:
            parts.append(persona_voice)
        return (
            ("【回复风格约束】\n" if include_heading else "")
            + "\n\n".join(parts)
            + "\n这些规则用于普通聊天的表达节奏；如果当前问题确实需要排障、教程、代码说明、复杂解释或用户明确要求详细说明，可以优先保证信息完整。"
            + "\n无论工具或模型返回什么内容，外发正文都不要照抄英文报错、内容策略提示、政策链接或内部诊断；遇到这类结果时，用当前人格的一句简短中文说明，再自然收住或邀请用户换一种说法。"
        )

    def _format_reply_style_prompt_section(self) -> dict[str, Any]:
        return prompt_section(
            "回复风格约束",
            self._format_reply_style_prompt(include_heading=False),
        )

    @staticmethod
    def _format_technical_reasoning_prompt(
        event: AstrMessageEvent | None,
        req: ProviderRequest | None = None,
        *,
        include_heading: bool = True,
    ) -> str:
        text = "\n".join(
            part
            for part in (
                str(getattr(event, "message_str", "") or "").strip(),
                str(getattr(req, "prompt", "") or "").strip(),
            )
            if part
        )
        compact = re.sub(r"\s+", "", text).lower()
        if not compact:
            return ""
        technical_markers = (
            "代码", "源码", "脚本", "python", "sleep(", "报错", "日志", "执行结果",
            "计算", "公式", "换算", "单位", "耗时", "延迟", "超时", "秒", "分钟", "小时",
        )
        if not any(marker in compact for marker in technical_markers):
            return ""
        return (
            ("【技术解释准确性】\n" if include_heading else "")
            +
            "解释代码、公式、日志耗时或单位换算时，先逐项读取用户给出的原表达式和原始数值，写清每个量的单位；"
            "先统一换算到同一种基本单位，再换算成用户需要的展示单位，并用一次反向换算复核。"
            "严格区分配置/代码要求的时长、程序实际运行耗时、日志记录值和界面格式化后的显示值，不要把它们当成同一个量。\n"
            "不得引入源码、日志或用户材料中没有出现的运算、常数、倍率、对数或所谓解释器规则来凑结果；"
            "尤其不能凭空加入 ln、log、指数或除法。如果结果与原表达式不一致，明确指出缺少哪段真实代码或日志，不要虚构原因。\n"
            "例如 `time.sleep(10 * 60)` 的参数是 600 秒，也就是 10 分钟；除非真实代码另有运算，不能解释成 4.35 分钟。"
        )

    async def _append_reply_style_to_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *,
        mode: str = "passive",
        priority: int = 12,
    ) -> None:
        sections = [self._format_reply_style_prompt_section()]
        technical_prompt = self._format_technical_reasoning_prompt(
            event,
            req,
            include_heading=False,
        )
        if technical_prompt:
            sections.append(prompt_section("技术解释准确性", technical_prompt))
        combined_prompt = render_prompt_sections(sections)
        if not combined_prompt:
            return
        marker = "<!-- private_companion_reply_style_v1 -->"
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        if marker in current_prompt or marker in current_turn_prompt:
            return
        placement = "prompt" if self._append_turn_prompt_fragment_by_position(
            req,
            marker,
            combined_prompt,
            title="回复风格约束",
            priority=priority,
            source="reply_style",
            structured=True,
        ) else "system_prompt"
        if placement == "system_prompt":
            req.system_prompt = f"{current_prompt}\n\n{marker}\n{combined_prompt}".strip()
        await self._record_request_prompt_fragment(
            event,
            title="回复风格约束",
            key="reply.style",
            text=combined_prompt,
            source="reply_style",
            mode=mode,
            metadata={"注入位置": placement},
        )

    @staticmethod
    def _compact_high_intensity_prompt_lines(text: Any, *, max_chars: int = 900, max_lines: int = 12) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        lines: list[str] = []
        for line in re.split(r"[\r\n]+", raw):
            cleaned = _single_line(line, 180).strip()
            if cleaned:
                lines.append(cleaned)
        if not lines:
            return _single_line(raw, max_chars)
        if len(lines) > max_lines:
            lines = lines[: max(1, max_lines - 1)] + [f"...已省略 {len(lines) - max_lines + 1} 行高强度背景"]
        compact = "\n".join(lines).strip()
        if len(compact) > max_chars:
            compact = compact[:max_chars].rstrip() + "\n...已截断高强度背景"
        return compact

    def _format_group_high_intensity_reply_guard(
        self,
        event: AstrMessageEvent | None = None,
        *,
        include_heading: bool = True,
    ) -> str:
        high_intensity = getattr(event, "private_companion_group_high_intensity", None) if event is not None else None
        if not isinstance(high_intensity, dict) or not high_intensity.get("active"):
            return ""
        lines = [
                "当前群聊处于高强度/合并收口状态，本轮只抓一个重点短句接住即可。",
                "必须优先服从 AstrBot 人格、系统提示和回复风格配置里的字数、句数、口吻、语言和表达节奏要求；用户在这些配置里写了什么，就按对应要求回复。",
                "如果配置要求很短，就不要因为高强度背景而扩写；如果配置要求口语、简体中文、少句数或特定风格，也要继续保持。",
                "不要因为关系网、状态、记忆或合并消息而扩写、复述背景、逐条总结；一般 1 句，能少字就少字。",
        ]
        if include_heading:
            lines.insert(0, "【群聊高强度短回复护栏】")
        return "\n".join(lines)

    async def _append_group_high_intensity_reply_guard_to_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
    ) -> None:
        guard_text = self._format_group_high_intensity_reply_guard(
            event,
            include_heading=False,
        )
        if not guard_text:
            return
        marker = "<!-- private_companion_group_high_intensity_reply_guard_v1 -->"
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        if marker in current_prompt or marker in current_turn_prompt:
            return
        placement = "prompt" if self._append_turn_prompt_fragment_by_position(
            req,
            marker,
            guard_text,
            title="群聊高强度短回复护栏",
            priority=11,
            source="group_high_intensity",
        ) else "system_prompt"
        if placement == "system_prompt":
            req.system_prompt = f"{current_prompt}\n\n{marker}\n{guard_text}".strip()
        await self._record_request_prompt_fragment(
            event,
            title="群聊高强度短回复护栏",
            key="group.high_intensity.reply_guard",
            text=guard_text,
            source="group_high_intensity",
            mode="group",
            priority=11,
            metadata={"注入位置": placement},
        )

    @staticmethod
    def _normalize_provider_config_mode(value: Any, config: Any = None) -> str:
        text = str(value or "").strip().lower()
        aliases = {
            "quick": "quick",
            "fast": "quick",
            "simple": "quick",
            "快速": "quick",
            "快速配置": "quick",
            "precision": "precision",
            "precise": "precision",
            "advanced": "precision",
            "detail": "precision",
            "detailed": "precision",
            "精准": "precision",
            "精准配置": "precision",
            "分流": "precision",
            "分流模型": "precision",
        }
        if text in aliases:
            return aliases[text]
        if text in {"quick", "precision"}:
            return text

        precision_keys = (
            "MAI_STYLE_PROVIDER_ID",
            "DAILY_PLAN_PROVIDER_ID",
            "DETAIL_ENHANCEMENT_PROVIDER_ID",
            "DREAM_DIARY_PROVIDER_ID",
            "CREATIVE_PROVIDER_ID",
            "CREATIVE_OUTLINE_PROVIDER_ID",
            "CREATIVE_REVIEW_PROVIDER_ID",
            "VOICE_PROMPT_PROVIDER_ID",
            "tts_conversion_provider_id",
            "PHOTO_PROMPT_PROVIDER_ID",
            "NARRATION_PROVIDER_ID",
            "HISTORY_SUMMARY_PROVIDER_ID",
            "RESPONSE_REVIEW_PROVIDER_ID",
            "SMART_SILENCE_PROVIDER_ID",
            "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID",
            "TROUBLESHOOTING_PROVIDER_ID",
            "DAILY_REVIEW_PROVIDER_ID",
            "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID",
            "REST_WAKEUP_PROVIDER_ID",
            "RELATIONSHIP_ANALYSIS_PROVIDER_ID",
            "EMOTION_JUDGEMENT_PROVIDER_ID",
            "COMPANION_MEMORY_PROVIDER_ID",
            "DIALOGUE_EPISODE_PROVIDER_ID",
            "GROUP_INTERJECT_PROVIDER_ID",
            "GROUP_EPISODE_PROVIDER_ID",
            "GROUP_SLANG_PROVIDER_ID",
            "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID",
            "FORWARD_MESSAGE_PROVIDER_ID",
            "NEWS_PROVIDER_ID",
            "WEB_EXPLORATION_PROVIDER_ID",
        )
        if any(str(_flat_get(config, key, "") or "").strip() for key in precision_keys):
            return "precision"
        return "quick"

    @staticmethod
    def _normalize_external_image_api_platform(value: Any) -> str:
        text = str(value or "").strip().lower()
        aliases = {
            "auto": "auto",
            "自动": "auto",
            "openai": "openai",
            "openai-compatible": "openai",
            "openai_compatible": "openai",
            "openai兼容": "openai",
            "兼容": "openai",
            "兼容模式": "openai",
            "external": "openai",
            "openrouter": "openrouter",
            "open-router": "openrouter",
            "open_router": "openrouter",
            "openrouter.ai": "openrouter",
            "agnes": "agnes",
            "agnes-ai": "agnes",
            "agnes_ai": "agnes",
            "agnes image": "agnes",
            "agnes-image": "agnes",
            "sapiens": "agnes",
            "sapiens ai": "agnes",
            "bailian": "bailian",
            "dashscope": "bailian",
            "aliyun": "bailian",
            "alibaba": "bailian",
            "modelstudio": "bailian",
            "model_studio": "bailian",
            "百炼": "bailian",
            "阿里云百炼": "bailian",
            "通义万相": "bailian",
            "modelscope": "modelscope",
            "model_scope": "modelscope",
            "魔搭": "modelscope",
            "魔搭社区": "modelscope",
            "api-inference": "modelscope",
            "doubao": "doubao",
            "豆包": "doubao",
            "火山": "doubao",
            "火山引擎": "doubao",
            "volcengine": "doubao",
            "volces": "doubao",
            "ark": "doubao",
            "seedream": "doubao",
            "seed": "doubao",
            "gemini": "gemini",
            "google": "gemini",
            "google-ai": "gemini",
            "google_ai": "gemini",
            "generativelanguage": "gemini",
            "nano-banana": "gemini",
            "sensenova": "sensenova",
            "sense-nova": "sensenova",
            "日日新": "sensenova",
            "商汤日日新": "sensenova",
            "minimax": "minimax",
            "minimaxi": "minimax",
            "minimax-ai": "minimax",
            "minimax_ai": "minimax",
            "海螺": "minimax",
            "海螺ai": "minimax",
        }
        return aliases.get(text, text if text in {"auto", "openai", "openrouter", "agnes", "bailian", "modelscope", "doubao", "gemini", "sensenova", "minimax"} else "auto")

    @staticmethod
    def _normalize_external_image_endpoint_enabled(value: Any, default: bool = True) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        text = str(value).strip().lower()
        if text in {"true", "1", "yes", "y", "on", "enable", "enabled", "启用", "开启", "开", "是"}:
            return True
        if text in {"false", "0", "no", "n", "off", "disable", "disabled", "停用", "关闭", "关", "否", ""}:
            return False
        return default

    def _normalize_external_image_api_endpoint(self, item: Any, *, index: int = 0) -> dict[str, Any]:
        if not isinstance(item, dict):
            return {}

        def pick(*keys: str, default: Any = "") -> Any:
            for key in keys:
                if key in item and item.get(key) not in (None, ""):
                    return item.get(key)
            return default

        endpoint = {
            "name": _single_line(pick("name", "label", "title", default=f"在线 API {index + 1}"), 80) or f"在线 API {index + 1}",
            "enabled": self._normalize_external_image_endpoint_enabled(pick("enabled", "enable", "active", default=True), True),
            "platform": self._normalize_external_image_api_platform(
                pick("platform", "external_image_api_platform", "image_api_platform", default="auto")
            ),
            "base_url": str(
                pick(
                    "base_url",
                    "api_base",
                    "api_base_url",
                    "url",
                    "endpoint",
                    "EXTERNAL_IMAGE_API_BASE_URL",
                    "BACKUP_EXTERNAL_IMAGE_API_BASE_URL",
                    default="",
                )
                or ""
            ).strip(),
            "api_key": str(
                pick(
                    "api_key",
                    "key",
                    "token",
                    "EXTERNAL_IMAGE_API_KEY",
                    "BACKUP_EXTERNAL_IMAGE_API_KEY",
                    default="",
                )
                or ""
            ).strip(),
            "model": str(
                pick(
                    "model",
                    "model_name",
                    "EXTERNAL_IMAGE_API_MODEL",
                    "BACKUP_EXTERNAL_IMAGE_API_MODEL",
                    default="",
                )
                or ""
            ).strip(),
            "size": str(
                pick("size", "image_size", "external_image_api_size", "backup_external_image_api_size", default="1024x1024")
                or "1024x1024"
            ).strip()
            or "1024x1024",
            "ratio": _single_line(pick("ratio", "aspect_ratio", "image_ratio", default=""), 20),
            "timeout_seconds": _safe_int(
                pick(
                    "timeout_seconds",
                    "timeout",
                    "external_image_api_timeout_seconds",
                    "backup_external_image_api_timeout_seconds",
                    default=180,
                ),
                180,
                20,
                600,
            ),
            "custom_headers": str(
                pick(
                    "custom_headers",
                    "headers",
                    "external_image_api_custom_headers",
                    "backup_external_image_api_custom_headers",
                    default="",
                )
                or ""
            ).strip(),
        }
        base_lower = str(endpoint.get("base_url") or "").lower()
        model_lower = str(endpoint.get("model") or "").lower()
        parsed_base = urlparse(base_lower if "://" in base_lower else f"https://{base_lower}")
        base_host = str(parsed_base.hostname or "").strip().lower()
        if endpoint["platform"] in {"auto", "openai", "openrouter"} and (
            base_host == "openrouter.ai" or base_host.endswith(".openrouter.ai")
        ):
            endpoint["platform"] = "openrouter"
        if endpoint["platform"] in {"auto", "openai"} and (
            "apihub.agnes-ai.com" in base_lower or model_lower.startswith("agnes-image-")
        ):
            endpoint["platform"] = "agnes"
        minimax_official_host = any(
            host in base_lower
            for host in ("api.minimaxi.com", "api.minimax.io", "minimaxi.com", "minimax.io")
        )
        if (
            endpoint["platform"] in {"auto", "openai"} and minimax_official_host
        ) or (
            endpoint["platform"] == "auto" and model_lower in {"image-01", "image-01-live"}
        ):
            endpoint["platform"] = "minimax"
        if endpoint["platform"] == "minimax" and re.search(
            r"/v1/(?:image_generation|image/generation|images/generations|images/edits)/?(?:[?#].*)?$",
            str(endpoint.get("base_url") or ""),
            flags=re.I,
        ):
            base_normalizer = getattr(self, "_normalized_external_image_api_base_url", None)
            if callable(base_normalizer):
                normalized_root = base_normalizer(endpoint["base_url"], platform="minimax")
                if normalized_root:
                    endpoint["base_url"] = f"{normalized_root.rstrip('/')}/image_generation"
        if endpoint["platform"] == "auto" and ("token.sensenova.cn" in base_lower or model_lower in {"senova-u1-fast", "sensenova-u1-fast"}):
            endpoint["platform"] = "sensenova"
        if endpoint["platform"] == "sensenova" and model_lower == "senova-u1-fast":
            endpoint["model"] = "sensenova-u1-fast"
        return endpoint

    def _normalize_external_image_api_endpoints(self, value: Any) -> list[dict[str, Any]]:
        raw = value
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                raw = []
            else:
                try:
                    parsed = json.loads(text)
                    raw = parsed
                except Exception:
                    lines = [line.strip() for line in text.splitlines() if line.strip()]
                    raw = [{"base_url": line} for line in lines]
        if isinstance(raw, dict):
            raw = raw.get("items") or raw.get("endpoints") or raw.get("apis") or []
        if not isinstance(raw, list):
            return []
        normalized: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for index, item in enumerate(raw[:12]):
            endpoint = self._normalize_external_image_api_endpoint(item, index=index)
            if not endpoint:
                continue
            if not any(str(endpoint.get(key) or "").strip() for key in ("base_url", "api_key", "model", "custom_headers")):
                continue
            signature = (
                str(endpoint.get("platform") or "auto").lower(),
                str(endpoint.get("base_url") or "").rstrip("/"),
                str(endpoint.get("model") or ""),
                str(endpoint.get("api_key") or "")[:12],
            )
            if signature in seen:
                continue
            seen.add(signature)
            normalized.append(endpoint)
        return normalized

    def _apply_quick_provider_defaults(self) -> None:
        fast = str(getattr(self, "fast_response_provider_id", "") or "").strip()
        complex_model = str(getattr(self, "complex_reasoning_provider_id", "") or "").strip()
        creative = str(getattr(self, "creative_model_provider_id", "") or "").strip()
        plugin_vision = str(getattr(self, "plugin_vision_provider_id", "") or "").strip()
        config = getattr(self, "config", None)

        def configured_provider(config_key: str, fallback: str = "") -> str:
            # Preserve an explicit empty value while tolerating older configs
            # that do not yet contain the independent vision key.
            raw = self._cfg_raw(config, config_key, None)
            return fallback if raw is None else str(raw or "").strip()

        # These routes are independent of quick/precision text assignment and
        # must be refreshed in either mode when the page saves a new value.
        for attr, config_key in (
            ("embedding_provider_id", "EMBEDDING_PROVIDER_ID"),
            ("group_member_safety_provider_id", "GROUP_MEMBER_SAFETY_PROVIDER_ID"),
            ("reaction_expression_embedding_provider_id", "REACTION_EXPRESSION_EMBEDDING_PROVIDER_ID"),
            ("deepseek_peak_replacement_provider_id", "DEEPSEEK_PEAK_REPLACEMENT_PROVIDER_ID"),
            ("sensitive_replacement_provider_id", "SENSITIVE_REPLACEMENT_PROVIDER_ID"),
        ):
            setattr(self, attr, self._cfg_str(config, config_key, ""))

        attr_config_keys = {
            "llm_provider_id": "LLM_PROVIDER_ID",
            "mai_style_provider_id": "MAI_STYLE_PROVIDER_ID",
            "daily_plan_provider_id": "DAILY_PLAN_PROVIDER_ID",
            "detail_enhancement_provider_id": "DETAIL_ENHANCEMENT_PROVIDER_ID",
            "history_summary_provider_id": "HISTORY_SUMMARY_PROVIDER_ID",
            "relationship_analysis_provider_id": "RELATIONSHIP_ANALYSIS_PROVIDER_ID",
            "companion_memory_provider_id": "COMPANION_MEMORY_PROVIDER_ID",
            "dialogue_episode_provider_id": "DIALOGUE_EPISODE_PROVIDER_ID",
            "group_episode_provider_id": "GROUP_EPISODE_PROVIDER_ID",
            "forward_message_provider_id": "FORWARD_MESSAGE_PROVIDER_ID",
            "proactive_persona_judge_provider_id": "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID",
            "response_review_provider_id": "RESPONSE_REVIEW_PROVIDER_ID",
            "smart_silence_provider_id": "SMART_SILENCE_PROVIDER_ID",
            "troubleshooting_provider_id": "TROUBLESHOOTING_PROVIDER_ID",
            "daily_review_provider_id": "DAILY_REVIEW_PROVIDER_ID",
            "emotion_judgement_provider_id": "EMOTION_JUDGEMENT_PROVIDER_ID",
            "smart_message_debounce_provider_id": "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID",
            "rest_wakeup_provider_id": "REST_WAKEUP_PROVIDER_ID",
            "group_followup_judge_provider_id": "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID",
            "group_interject_provider_id": "GROUP_INTERJECT_PROVIDER_ID",
            "group_slang_provider_id": "GROUP_SLANG_PROVIDER_ID",
            "voice_prompt_provider_id": "VOICE_PROMPT_PROVIDER_ID",
            "tts_conversion_provider_id": "tts_conversion_provider_id",
            "narration_provider_id": "NARRATION_PROVIDER_ID",
            "news_provider_id": "NEWS_PROVIDER_ID",
            "web_exploration_provider_id": "WEB_EXPLORATION_PROVIDER_ID",
            "creative_provider_id": "CREATIVE_PROVIDER_ID",
            "creative_outline_provider_id": "CREATIVE_OUTLINE_PROVIDER_ID",
            "creative_review_provider_id": "CREATIVE_REVIEW_PROVIDER_ID",
            "dream_diary_provider_id": "DREAM_DIARY_PROVIDER_ID",
            "dream_provider_id": "DREAM_DIARY_PROVIDER_ID",
            "diary_provider_id": "DREAM_DIARY_PROVIDER_ID",
            "photo_prompt_provider_id": "PHOTO_PROMPT_PROVIDER_ID",
            "embedding_provider_id": "EMBEDDING_PROVIDER_ID",
            "group_member_safety_provider_id": "GROUP_MEMBER_SAFETY_PROVIDER_ID",
            "reaction_expression_embedding_provider_id": "REACTION_EXPRESSION_EMBEDDING_PROVIDER_ID",
            "deepseek_peak_replacement_provider_id": "DEEPSEEK_PEAK_REPLACEMENT_PROVIDER_ID",
            "sensitive_replacement_provider_id": "SENSITIVE_REPLACEMENT_PROVIDER_ID",
        }

        if str(getattr(self, "provider_config_mode", "quick") or "quick").strip().lower() != "quick":
            for attr, config_key in attr_config_keys.items():
                setattr(self, attr, self._cfg_str(config, config_key, ""))
            self.plugin_vision_provider_id = configured_provider("PLUGIN_VISION_PROVIDER_ID", plugin_vision)
            return

        def fill(attr: str, provider_id: str) -> None:
            setattr(self, attr, provider_id)

        fill("llm_provider_id", complex_model)
        fill("mai_style_provider_id", fast or complex_model)

        for attr in (
            "daily_plan_provider_id",
            "detail_enhancement_provider_id",
            "history_summary_provider_id",
            "relationship_analysis_provider_id",
            "companion_memory_provider_id",
            "dialogue_episode_provider_id",
            "group_episode_provider_id",
            "forward_message_provider_id",
            "proactive_persona_judge_provider_id",
            "troubleshooting_provider_id",
            "daily_review_provider_id",
        ):
            fill(attr, complex_model)

        for attr in (
            "response_review_provider_id",
            "smart_silence_provider_id",
            "emotion_judgement_provider_id",
            "smart_message_debounce_provider_id",
            "rest_wakeup_provider_id",
            "group_interject_provider_id",
            "group_slang_provider_id",
            "voice_prompt_provider_id",
            "tts_conversion_provider_id",
            "narration_provider_id",
            "news_provider_id",
            "web_exploration_provider_id",
        ):
            fill(attr, fast or complex_model)
        fill("group_followup_judge_provider_id", fast)

        for attr in (
            "creative_provider_id",
            "creative_outline_provider_id",
            "creative_review_provider_id",
            "dream_diary_provider_id",
            "dream_provider_id",
            "diary_provider_id",
            "photo_prompt_provider_id",
        ):
            fill(attr, creative or complex_model)
        self.plugin_vision_provider_id = configured_provider("PLUGIN_VISION_PROVIDER_ID", plugin_vision)

    def _detect_astrbot_version(self) -> str:
        candidates: list[Any] = []
        for obj in (
            getattr(self, "context", None),
            getattr(getattr(self, "context", None), "core_lifecycle", None),
            getattr(getattr(self, "context", None), "metadata", None),
        ):
            if obj is None:
                continue
            for attr in ("version", "astrbot_version", "__version__", "VERSION"):
                try:
                    candidates.append(getattr(obj, attr, ""))
                except Exception:
                    pass
        for module_name in ("astrbot", "astrbot.core", "astrbot.api"):
            try:
                module = importlib.import_module(module_name)
            except Exception:
                continue
            for attr in ("__version__", "VERSION", "version"):
                try:
                    candidates.append(getattr(module, attr, ""))
                except Exception:
                    pass
        for candidate in candidates:
            text = _single_line(candidate, 40)
            if re.search(r"\d+\.\d+(?:\.\d+)?", text):
                return text
        return ""

    @staticmethod
    def _parse_version_tuple(value: Any) -> tuple[int, int, int] | None:
        match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", str(value or ""))
        if not match:
            return None
        return (
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3) or 0),
        )

    def _append_turn_prompt_fragment_by_position(
        self,
        req: ProviderRequest,
        marker: str,
        text: str,
        *,
        priority: int = 50,
        source: str = "",
        title: str = "",
        force_dynamic: bool = False,
        opaque: bool = False,
        structured: bool = False,
    ) -> bool:
        position = self._normalize_passive_injection_position(runtime_persona_setting(self, 'passive_injection_position', "prompt"))
        content = str(text or "").strip()
        if not content:
            return False
        try:
            marker = _single_line(marker, 120) or "<!-- private_companion_turn_fragment -->"
            if self._request_has_managed_prompt_marker(req, marker):
                return True
            plan = get_conversation_injection_plan(req)
            if plan is None:
                return False
            use_system_prompt = position == "system_prompt" and not force_dynamic
            plan.add(
                marker=marker,
                content=content,
                priority=int(priority),
                source=_single_line(source, 80),
                title=_single_line(title, 80),
                placement=(
                    PLACEMENT_TOOL_CONTRACT
                    if opaque and use_system_prompt
                    else PLACEMENT_DYNAMIC_SYSTEM
                    if use_system_prompt
                    else PLACEMENT_TURN_TAIL
                ),
                temporary=not use_system_prompt,
                materialized=use_system_prompt,
                opaque=opaque,
                structured=structured,
            )
            setattr(req, "_private_companion_turn_prompt_fragments", plan.legacy_turn_fragments())
            if use_system_prompt:
                return False
            plan.render_into(req, prefer_extra_user_content=True)
            return True
        except Exception as exc:
            logger.debug("[PrivateCompanion] 指定位置 prompt 注入失败,回退 system_prompt: %s", _single_line(exc, 120))
            return False

    def _materialize_conversation_system_block(
        self,
        req: ProviderRequest,
        *,
        key: str,
        marker: str,
        content: Any,
        priority: int = 50,
        source: str = "",
        title: str = "",
        placement: str = PLACEMENT_DYNAMIC_SYSTEM,
        opaque: bool = False,
        structured: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Register and append a legacy system block without changing its wire shape."""
        plan = get_conversation_injection_plan(req)
        if plan is not None:
            if opaque:
                rendered = f"{marker}\n{str(content or '').strip()}".strip()
                current = str(getattr(req, "system_prompt", "") or "")
                req.system_prompt = f"{current}\n\n{rendered}".strip() if current else rendered
                return plan.add(
                    key=key,
                    marker=marker,
                    content=content,
                    priority=priority,
                    source=source,
                    title=title,
                    placement=PLACEMENT_TOOL_CONTRACT,
                    temporary=False,
                    materialized=True,
                    opaque=True,
                    metadata=metadata,
                ) is not None
            return plan.materialize_system_block(
                req,
                key=key,
                marker=marker,
                content=content,
                priority=priority,
                source=source,
                title=title,
                placement=placement,
                structured=structured,
                metadata=metadata,
            )
        rendered = f"{marker}\n{str(content or '').strip()}".strip()
        current = str(getattr(req, "system_prompt", "") or "")
        req.system_prompt = f"{current}\n\n{rendered}".strip() if current else rendered
        return True

    @staticmethod
    def _request_has_managed_prompt_marker(req: ProviderRequest, marker: str) -> bool:
        """Only trust markers placed by the plugin, never raw user prompt text."""
        marker_text = _single_line(marker, 120)
        if not marker_text:
            return False
        plan = get_conversation_injection_plan(req, create=False)
        if plan is not None and plan.contains_marker(marker_text):
            return True
        if marker_text in str(getattr(req, "system_prompt", "") or ""):
            return True
        fragments = getattr(req, "_private_companion_turn_prompt_fragments", None)
        if isinstance(fragments, list) and any(
            isinstance(item, dict) and item.get("marker") == marker_text
            for item in fragments
        ):
            return True
        extra_parts = getattr(req, "extra_user_content_parts", None)
        if not isinstance(extra_parts, list):
            return False
        for part in extra_parts:
            if not bool(getattr(part, "_private_companion_turn_fragments", False)):
                continue
            text = str(getattr(part, "text", "") or getattr(part, "content", "") or "")
            if marker_text in text:
                return True
        return False

    def _request_prompt_context_surface(self, req: ProviderRequest) -> str:
        parts = [str(getattr(req, "prompt", "") or ""), str(getattr(req, "system_prompt", "") or "")]
        extra_parts = getattr(req, "extra_user_content_parts", None)
        if isinstance(extra_parts, list):
            for part in extra_parts:
                if isinstance(part, dict):
                    parts.append(str(part.get("text") or part.get("content") or ""))
                else:
                    parts.append(str(getattr(part, "text", "") or getattr(part, "content", "") or ""))
        return "\n".join(item for item in parts if item)

    @staticmethod
    def _strip_private_companion_prompt_artifacts(text: Any) -> str:
        cleaned = sanitize_llm_segment_control_tokens(text)
        if not cleaned or "private_companion_" not in cleaned:
            return cleaned
        cleaned = re.sub(
            r"\n*\s*<!--\s*private_companion_turn_fragments_start\s*-->.*?<!--\s*private_companion_turn_fragments_end\s*-->\s*",
            "\n",
            cleaned,
            flags=re.DOTALL,
        )
        block_markers = (
            "state",
            "static",
            "reply_style",
            "environment",
            "reply_image_anchor",
            "atrelay_tools",
            "relation_lookup",
            "qzone_tools",
            "photo_generation_tool",
            "cross_user_memory",
            "group_persona_denoise",
            "group_high_intensity_reply_guard",
            "group_context",
            "recall_query",
            "self_timeline",
            "rest_backlog",
            "atrelay_target_summary",
            "worldbook_mentions",
            "non_target_private_guard",
            "capability_boundary",
            "forward_message",
            "group_injection_guard",
            "reply_chain",
            "reply_segmentation",
            "media_delivery_truth",
            "tool_protocol",
            "period_boundary",
        )
        marker_pattern = "|".join(re.escape(f"private_companion_{name}_v1") for name in block_markers)
        cleaned = re.sub(
            rf"\n*\s*<!--\s*(?:{marker_pattern})\s*-->.*?(?=\n\s*<!--\s*private_companion_[a-z0-9_]+_v1\s*-->|\Z)",
            "\n",
            cleaned,
            flags=re.DOTALL,
        )
        return re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    def _sanitize_private_companion_prompt_artifacts_in_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        contexts = getattr(req, "contexts", None)
        changed = sanitize_private_request_group_artifacts(event, req)

        def clean_content(value: Any) -> tuple[Any, bool]:
            if isinstance(value, str):
                cleaned = self._strip_private_companion_prompt_artifacts(value)
                return cleaned, cleaned != value
            if isinstance(value, dict):
                updated = dict(value)
                dirty = False
                for key in ("text", "content", "value"):
                    if key in updated and isinstance(updated.get(key), str):
                        cleaned = self._strip_private_companion_prompt_artifacts(updated.get(key))
                        if cleaned != updated.get(key):
                            updated[key] = cleaned
                            dirty = True
                return updated, dirty
            if isinstance(value, list):
                new_items = []
                dirty = False
                for item in value:
                    cleaned_item, item_dirty = clean_content(item)
                    new_items.append(cleaned_item)
                    dirty = dirty or item_dirty
                return new_items, dirty
            return value, False

        if isinstance(contexts, list) and contexts:
            sanitized: list[Any] = []
            for item in contexts:
                if isinstance(item, dict):
                    updated = dict(item)
                    cleaned_content, dirty = clean_content(updated.get("content"))
                    if dirty:
                        updated["content"] = cleaned_content
                        changed += 1
                    sanitized.append(updated)
                else:
                    cleaned_item, dirty = clean_content(item)
                    if dirty:
                        changed += 1
                    sanitized.append(cleaned_item)
            try:
                req.contexts = sanitized
            except Exception:
                return
        if changed <= 0:
            return
        logger.info(
            "[PrivateCompanion] 已清理请求中的跨轮/跨作用域动态注入残留: session=%s surfaces_changed=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            changed,
        )

    def _neutralize_stale_reaction_feedback_in_history(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
    ) -> None:
        """Remove leaked internal reaction tags from provider history.

        Reaction-expression tags are transport metadata, not conversation text.
        A failed/older response may leave them in ``req.contexts``; stripping
        only those tags keeps the surrounding user feedback intact and avoids
        teaching the model to emit the internal protocol on a later turn.
        """
        contexts = getattr(req, "contexts", None)
        if not isinstance(contexts, list) or not contexts:
            return
        tag_pattern = re.compile(
            r"(?:<|&lt;|\\<)\s*/?\s*pc[_-]?reaction[_-]?expression\b[^>]*?(?:>|&gt;|\\>)"
            r".*?"
            r"(?:<|&lt;|\\<)\s*/\s*pc[_-]?reaction[_-]?expression\s*(?:>|&gt;|\\>)",
            flags=re.IGNORECASE | re.DOTALL,
        )
        changed = 0

        def clean(value: Any) -> tuple[Any, bool]:
            if isinstance(value, str):
                updated = tag_pattern.sub("", value)
                updated = re.sub(r"\n{3,}", "\n\n", updated).strip()
                return updated, updated != value
            if isinstance(value, dict):
                updated = dict(value)
                dirty = False
                for key in ("content", "text", "value"):
                    if key not in updated:
                        continue
                    cleaned, item_dirty = clean(updated.get(key))
                    if item_dirty:
                        updated[key] = cleaned
                        dirty = True
                return updated, dirty
            if isinstance(value, list):
                items: list[Any] = []
                dirty = False
                for item in value:
                    cleaned, item_dirty = clean(item)
                    items.append(cleaned)
                    dirty = dirty or item_dirty
                return items, dirty
            return value, False

        sanitized: list[Any] = []
        for item in contexts:
            cleaned, item_changed = clean(item)
            sanitized.append(cleaned)
            if item_changed:
                changed += 1
        if changed <= 0:
            return
        try:
            req.contexts = sanitized
        except Exception:
            return
        logger.info(
            "[PrivateCompanion] 已清理请求历史里的残留反应协议标签: session=%s contexts_changed=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            changed,
        )

    @staticmethod
    def _request_context_role(item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("role") or "").strip().lower()
        return str(getattr(item, "role", "") or "").strip().lower()

    @staticmethod
    def _request_context_tool_calls(item: Any) -> list[Any]:
        raw = item.get("tool_calls") if isinstance(item, dict) else getattr(item, "tool_calls", None)
        return list(raw) if isinstance(raw, (list, tuple)) else []

    @staticmethod
    def _request_context_tool_call_id(item: Any) -> str:
        value = item.get("tool_call_id") if isinstance(item, dict) else getattr(item, "tool_call_id", None)
        return str(value or "").strip()

    @staticmethod
    def _request_context_declared_tool_call_id(item: Any) -> str:
        value = item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
        return str(value or "").strip()

    def _repair_incomplete_tool_context_groups(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        """Drop broken tool-call groups atomically before strict providers see them."""
        contexts = getattr(req, "contexts", None)
        if not isinstance(contexts, list) or not contexts:
            return

        repaired: list[Any] = []
        removed_groups = 0
        removed_messages = 0
        index = 0
        while index < len(contexts):
            item = contexts[index]
            role = self._request_context_role(item)
            declared_calls = self._request_context_tool_calls(item) if role == "assistant" else []
            if declared_calls:
                declared_ids = [
                    self._request_context_declared_tool_call_id(call)
                    for call in declared_calls
                ]
                next_index = index + 1
                tool_messages: list[Any] = []
                while (
                    next_index < len(contexts)
                    and self._request_context_role(contexts[next_index]) == "tool"
                ):
                    tool_messages.append(contexts[next_index])
                    next_index += 1

                expected_ids = set(declared_ids)
                result_ids = {
                    self._request_context_tool_call_id(tool_message)
                    for tool_message in tool_messages
                    if self._request_context_tool_call_id(tool_message)
                }
                complete = (
                    bool(expected_ids)
                    and len(expected_ids) == len(declared_ids)
                    and expected_ids.issubset(result_ids)
                )
                if complete:
                    repaired.append(item)
                    kept_ids: set[str] = set()
                    for tool_message in tool_messages:
                        tool_call_id = self._request_context_tool_call_id(tool_message)
                        if tool_call_id in expected_ids and tool_call_id not in kept_ids:
                            repaired.append(tool_message)
                            kept_ids.add(tool_call_id)
                        else:
                            removed_messages += 1
                else:
                    removed_groups += 1
                    removed_messages += 1 + len(tool_messages)
                index = next_index
                continue

            if role == "tool":
                removed_messages += 1
            else:
                repaired.append(item)
            index += 1

        if removed_messages <= 0:
            return
        try:
            req.contexts = repaired
        except Exception:
            return
        logger.warning(
            "[PrivateCompanion] 已修复不完整工具调用历史: session=%s groups=%s messages=%s contexts=%s->%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            removed_groups,
            removed_messages,
            len(contexts),
            len(repaired),
        )

    def _remove_managed_turn_prompt_extra_part(self, req: ProviderRequest) -> None:
        extra_parts = getattr(req, "extra_user_content_parts", None)
        if not isinstance(extra_parts, list):
            return
        start_marker = "<!-- private_companion_turn_fragments_start -->"
        end_marker = "<!-- private_companion_turn_fragments_end -->"
        kept = []
        for part in extra_parts:
            text = ""
            if isinstance(part, dict):
                text = str(part.get("text") or part.get("content") or "")
            else:
                text = str(getattr(part, "text", "") or getattr(part, "content", "") or "")
            if getattr(part, "_private_companion_turn_fragments", False):
                continue
            if start_marker in text and end_marker in text:
                continue
            kept.append(part)
        req.extra_user_content_parts = kept

    def _append_managed_turn_prompt_extra_part(self, req: ProviderRequest, text: str) -> bool:
        content = str(text or "").strip()
        if not content or TextPart is None:
            return False
        try:
            extra_parts = getattr(req, "extra_user_content_parts", None)
            if not isinstance(extra_parts, list):
                req.extra_user_content_parts = []
            part = TextPart(text=content)
            mark_as_temp = getattr(part, "mark_as_temp", None)
            if callable(mark_as_temp):
                part = mark_as_temp()
            try:
                setattr(part, "_private_companion_turn_fragments", True)
            except Exception:
                pass
            req.extra_user_content_parts.append(part)
            setattr(req, "_private_companion_turn_prompt_placement", "extra_user_content_parts")
            return True
        except Exception as exc:
            logger.debug("[PrivateCompanion] extra_user_content_parts 注入失败,回退 prompt: %s", _single_line(exc, 120))
            return False

    def _render_turn_prompt_fragments(self, req: ProviderRequest, *, prefer_extra_user_content: bool = False) -> bool:
        plan = get_conversation_injection_plan(req, create=False)
        if plan is not None:
            plan.render_into(req, prefer_extra_user_content=prefer_extra_user_content)
            return True
        start_marker = "<!-- private_companion_turn_fragments_start -->"
        end_marker = "<!-- private_companion_turn_fragments_end -->"
        current = str(getattr(req, "prompt", "") or "")
        base = re.sub(
            rf"\n*\s*{re.escape(start_marker)}.*?{re.escape(end_marker)}\s*",
            "\n\n",
            current,
            flags=re.DOTALL,
        ).strip()
        fragments = getattr(req, "_private_companion_turn_prompt_fragments", None)
        if not isinstance(fragments, list) or not fragments:
            setattr(req, "prompt", base)
            self._remove_managed_turn_prompt_extra_part(req)
            return True
        seen_markers: set[str] = set()
        seen_content: set[str] = set()
        rendered_parts: list[str] = []
        for item in sorted(
            (frag for frag in fragments if isinstance(frag, dict)),
            key=lambda frag: (_safe_int(frag.get("priority"), 50), _safe_int(frag.get("index"), 0)),
        ):
            marker = _single_line(item.get("marker"), 120)
            content = str(item.get("content") or "").strip()
            if not marker or not content:
                continue
            if marker in seen_markers or content in seen_content:
                continue
            seen_markers.add(marker)
            seen_content.add(content)
            rendered_parts.append(f"{marker}\n{content}")
        if not rendered_parts:
            setattr(req, "prompt", base)
            self._remove_managed_turn_prompt_extra_part(req)
            return True
        managed = f"{start_marker}\n" + "\n\n".join(rendered_parts) + f"\n{end_marker}"
        if prefer_extra_user_content:
            setattr(req, "prompt", base)
            self._remove_managed_turn_prompt_extra_part(req)
            if self._append_managed_turn_prompt_extra_part(req, managed):
                return True
        setattr(req, "prompt", f"{base}\n\n{managed}".strip() if base else managed)
        self._remove_managed_turn_prompt_extra_part(req)
        setattr(req, "_private_companion_turn_prompt_placement", "prompt")
        return True

    async def _record_request_prompt_fragment(
        self,
        event: AstrMessageEvent,
        *,
        title: str,
        key: str,
        text: str,
        source: str = "",
        mode: str = "",
        priority: int = 50,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        recorder = getattr(self, "_record_prompt_injection_snapshot", None)
        content = str(text or "").strip()
        if not callable(recorder) or not content:
            return
        await recorder(
            kind="request",
            session=_single_line(getattr(event, "unified_msg_origin", ""), 160) or self._event_scope_key(event),
            title=title,
            text=content,
            mode=mode,
            trace_id=self._prompt_injection_trace_id_for_event(event),
            message_preview=self._prompt_injection_message_preview_for_event(event),
            sender_label=self._prompt_injection_sender_label_for_event(event),
            modules=[
                {
                    "key": key,
                    "source": source,
                    "priority": priority,
                    "content": content,
                    "chars": len(content),
                }
            ],
            metadata={
                **(metadata or {}),
                "会话": _single_line(getattr(event, "unified_msg_origin", ""), 160) or "unknown",
                "发送者": _single_line(self._event_sender_id(event), 80),
            },
        )

    async def _resolve_prompt_context_collector(self, spec: dict[str, Any]) -> dict[str, Any]:
        key = _single_line(spec.get("key"), 80)
        title = _single_line(spec.get("title"), 80)
        source = _single_line(spec.get("source"), 80)
        priority = _safe_int(spec.get("priority"), 100, 0)
        timeout = max(0.05, _safe_float(spec.get("timeout"), 0.8, 0.05))
        started = time.time()
        metadata = dict(spec.get("metadata") if isinstance(spec.get("metadata"), dict) else {})
        metadata.setdefault("来源", source or key)
        metadata.setdefault("超时秒数", round(timeout, 2))
        try:
            func = spec.get("func")
            if not callable(func):
                raise TypeError("collector is not callable")
            result = func()
            if asyncio.iscoroutine(result):
                result = await asyncio.wait_for(result, timeout=timeout)
            section = coerce_prompt_section(result)
            if section is not None:
                title = section.title
                content = str(section.content or "").strip()
            else:
                content = str(result or "").strip()
            elapsed_ms = int((time.time() - started) * 1000)
            metadata.update(
                {
                    "耗时ms": elapsed_ms,
                    "状态": "命中" if content else "空",
                    "字符数": len(content),
                }
            )
            return {
                "key": key,
                "title": title,
                "source": source,
                "priority": priority,
                "content": content,
                "metadata": metadata,
                "status": "hit" if content else "empty",
            }
        except asyncio.TimeoutError:
            elapsed_ms = int((time.time() - started) * 1000)
            metadata.update({"耗时ms": elapsed_ms, "状态": "超时"})
            logger.info(
                "[PrivateCompanion] 请求上下文收集超时: key=%s source=%s timeout=%.2fs",
                key or "-",
                source or "-",
                timeout,
            )
            return {
                "key": key,
                "title": title,
                "source": source,
                "priority": priority,
                "content": "",
                "metadata": metadata,
                "status": "timeout",
            }
        except Exception as exc:
            elapsed_ms = int((time.time() - started) * 1000)
            metadata.update({"耗时ms": elapsed_ms, "状态": "失败", "错误": _single_line(exc, 120)})
            logger.debug(
                "[PrivateCompanion] 请求上下文收集失败: key=%s source=%s error=%s",
                key or "-",
                source or "-",
                _single_line(exc, 120),
            )
            return {
                "key": key,
                "title": title,
                "source": source,
                "priority": priority,
                "content": "",
                "metadata": metadata,
                "status": "error",
            }

    async def _collect_prompt_contexts_parallel(self, specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        tasks = [self._resolve_prompt_context_collector(spec) for spec in specs if isinstance(spec, dict)]
        if not tasks:
            return []
        results = await asyncio.gather(*tasks, return_exceptions=True)
        collected: list[dict[str, Any]] = []
        for result in results:
            if isinstance(result, dict):
                collected.append(result)
            elif isinstance(result, Exception):
                logger.debug("[PrivateCompanion] 请求上下文并行收集出现未捕获异常: %s", _single_line(result, 120))
        return collected

    def _add_collected_prompt_contexts(self, prompt_surface: PromptSurface, collected: list[dict[str, Any]]) -> None:
        for item in collected:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            prompt_surface.add(
                _single_line(item.get("key"), 80),
                content,
                title=_single_line(item.get("title"), 80),
                priority=_safe_int(item.get("priority"), 100, 0),
                source=_single_line(item.get("source"), 80),
                metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
            )

    def _expression_profile_prompt_metadata(
        self,
        user: dict[str, Any],
        rule_details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        profile = user.get("expression_profile") if isinstance(user.get("expression_profile"), dict) else {}
        samples = profile.get("samples") if isinstance(profile.get("samples"), list) else []
        pending = profile.get("pending_samples") if isinstance(profile.get("pending_samples"), list) else []
        scene_profiles = profile.get("scene_profiles") if isinstance(profile.get("scene_profiles"), dict) else {}
        stable_scene_count = sum(
            1
            for item in scene_profiles.values()
            if isinstance(item, dict) and _safe_int(item.get("count"), 0, 0) >= 2
        )
        return {
            "来源": "表达学习样本",
            "置信度": min(1.0, round(len(samples) / 8, 2)) if samples else 0,
            "样本数": len(samples),
            "待审核": len(pending),
            "已学场景": stable_scene_count,
            "本轮命中": _single_line((rule_details or {}).get("label"), 32) or "无稳定规则",
            "规则证据": _safe_int((rule_details or {}).get("evidence_count"), 0, 0),
            "启用": bool(runtime_persona_setting(self, "enable_expression_learning", False)),
            "模式": _single_line(runtime_persona_setting(self, "expression_learning_mode", "balanced"), 20),
        }

    async def _collect_private_passive_prompt_contexts(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *,
        inbound_text: str,
        current_user: dict[str, Any],
        is_private_chat: bool,
    ) -> list[dict[str, Any]]:
        specs: list[dict[str, Any]] = []

        def section_call(func: Any, *args: Any, **kwargs: Any) -> Any:
            """Ask source formatters for a section, with legacy test/plugin fallback."""
            try:
                return func(*args, as_section=True, **kwargs)
            except TypeError as exc:
                if "as_section" not in str(exc):
                    raise
                return func(*args, **kwargs)

        def add_spec(
            key: str,
            title: str,
            source: str,
            priority: int,
            func: Any,
            *,
            timeout: float = 0.8,
            metadata: dict[str, Any] | None = None,
        ) -> None:
            specs.append(
                {
                    "key": key,
                    "title": title,
                    "source": source,
                    "priority": priority,
                    "func": func,
                    "timeout": timeout,
                    "metadata": metadata or {},
                }
            )

        current_user_id = ""
        if is_private_chat:
            try:
                current_user_id = _single_line(current_user.get("user_id") or event.get_sender_id(), 80)
            except Exception:
                current_user_id = _single_line(current_user.get("user_id"), 80)
        prompt_user = current_user
        current_umo = _single_line(getattr(event, "unified_msg_origin", ""), 220)
        if current_umo:
            prompt_user = dict(current_user)
            prompt_user["_game_current_umo"] = current_umo

        current_state_memory_needed = bool(
            self._user_asks_bot_current_state_or_activity(inbound_text)
            or re.search(
                r"(你|星缘|bot|机器人).{0,8}(在干嘛|在做什么|做什么|穿什么|穿的?什么|衣服|衣服颜色|什么颜色|吃了什么|吃的?什么|几点吃|什么时候吃|吃饭|进食|在哪里|在哪儿|当前位置|今天状态|现在状态)",
                inbound_text,
            )
            or re.search(
                r"(穿搭|自拍|衣服.{0,8}(颜色|什么色)|穿.{0,6}什么|今天.*衣服|今天.*颜色|刚才.*做|几点.*做了什么)",
                inbound_text,
            )
        )

        async def current_state_memory_context() -> str:
            composer = getattr(self, "_memory_companion_compose_feature_context", None)
            if not callable(composer):
                return ""
            current_state_memory = await composer(
                kind="current_state_reply",
                query=(
                    f"当前状态问答：{inbound_text}；"
                    "今日穿搭、衣服颜色、当前日程、当前位置、刚才做了什么、进食时间、吃了什么、最近自拍、用户常问状态习惯"
                ),
                user=current_user,
                user_id=current_user_id,
                event=event,
                top_k=6,
                max_chars=950,
                timeout_seconds=1.6,
            )
            current_state_memory = str(current_state_memory or "").strip()
            if not current_state_memory:
                return ""
            return (
                f"{current_state_memory}\n"
                "使用方式：只把它当作回答当前状态、穿搭、吃饭、日程连续性的辅助证据；"
                "优先服从本轮状态注入和当前会话中明确发生的时间线。尤其是近期明确换装、换地点或动作变化，"
                "高于每日穿搭、旧日程和旧记忆，不得被它们覆盖。不要说“我查到/记忆里”。"
            )

        if is_private_chat and current_state_memory_needed:
            add_spec(
                "memory.current_state",
                "我会牢牢记住你 当前状态参考",
                "memory_companion",
                54,
                current_state_memory_context,
                timeout=1.65,
                metadata={"范围": "当前私聊会话", "触发": "当前状态问答"},
            )

        add_spec(
            "creative.hidden",
            "私下创作近况",
            "creative",
            60,
            lambda: self._format_hidden_creative_context_for_reply(
                inbound_text,
                current_user,
                as_section=True,
            ),
        )
        add_spec("photo.recent_share", "最近一次真实图片分享", "photo", 61, lambda: section_call(self._format_recent_photo_share_snapshot_for_reply, current_user, inbound_text))
        add_spec(
            "bookshelf.secret",
            "资料柜夹层",
            "bookshelf",
            61,
            lambda: self._format_bookshelf_secret_for_prompt(inbound_text, current_user),
            timeout=1.2,
        )
        add_spec("bookshelf.reading", "资料柜阅读连续性", "bookshelf", 62, lambda: self._format_bookshelf_reading_context_for_reply(inbound_text, current_user))
        add_spec("news.recent", "新闻阅读上下文", "news", 64, lambda: section_call(self._format_recent_news_context_for_reply, inbound_text))
        add_spec("web_exploration.recent", "主动搜索上下文", "web_exploration", 65, lambda: section_call(self._format_recent_web_exploration_context_for_reply, inbound_text))
        if is_private_chat:
            add_spec(
                "reality_touch.continuity",
                "现实触达连续性",
                "reality_touch",
                69,
                lambda: section_call(self._format_reality_touch_continuity_context, current_user),
            )
            add_spec(
                "reality_touch.mobile_location",
                "用户手机位置感知",
                "reality_touch",
                68,
                lambda: section_call(self._format_mobile_user_location_context, current_user),
                metadata={"范围": "当前私聊会话", "来源": "用户主动授权的手机前台定位"},
            )
        if self._feature_enabled_or_temp_unlocked("enable_skill_growth_passive_injection"):
            add_spec("skill.growth", "能力熟悉度", "skill", 66, lambda: section_call(self._format_skill_growth_for_prompt))
        else:
            add_spec("skill.growth.match", "本轮相关技能", "skill", 66, lambda: section_call(self._format_skill_growth_for_user_text, inbound_text))
        if not self._memory_companion_should_defer_prompt_section("self_timeline", event, req):
            add_spec("self.timeline", "自我时间线检索", "self_timeline", 67, lambda: self._format_self_timeline_context_for_reply(inbound_text, current_user, limit=8, include_heading=False))
        if is_private_chat:
            add_spec(
                "relationship.owner_exclusive",
                "当前用户专属关系背景",
                "relationship",
                18,
                lambda: self._format_owner_exclusive_relationship_prompt(
                    current_user,
                    stable_user_id=current_user_id,
                    channel_scope="private",
                    include_heading=False,
                ),
                metadata={"范围": "当前人格与精确私聊用户", "模式": "owner_exclusive"},
            )
        private_context_deferred = self._memory_companion_should_defer_prompt_section("private_context", event, req)
        if not private_context_deferred:
            add_spec("private.context", "相处线索", "companion", 70, lambda: self._format_private_chat_context_injection(current_user, include_heading=False))
        if is_private_chat and not private_context_deferred:
            add_spec(
                "memory.private_recall",
                "当前私聊长期记忆补充",
                "memory_companion",
                73,
                lambda: section_call(
                    self._memory_companion_compose_private_recall,
                    event=event,
                    user=current_user,
                    user_id=current_user_id,
                    text=inbound_text,
                ),
                timeout=min(1.4, max(0.3, _safe_float(getattr(self, "memory_companion_context_timeout_seconds", 1.2), 1.2, 0.2))),
                metadata={"范围": "当前私聊会话", "触发": "记忆线索"},
            )
        add_spec("companion.planner", "私聊互动补充", "companion", 80, lambda: self._format_companion_planner_injection(prompt_user, include_heading=False))
        if not self._memory_companion_should_defer_prompt_section("livingmemory_guidance", event, req):
            add_spec("livingmemory.guidance", "长期记忆检索", "livingmemory", 90, lambda: self._format_livingmemory_guidance(scope="private" if is_private_chat else "group", include_heading=False))
        add_spec("detail.injection", "Bot 模拟当前片段", "daily_detail", 40, lambda: section_call(self._format_detail_injection))

        if is_private_chat:
            expression_user_id = self._expression_private_scope_id(current_user_id)
            expression_voice_selection = self._expression_voice_selection(
                scope="private",
                target_id=expression_user_id,
                inbound_text=inbound_text,
                context_owner=current_user,
                include_heading=False,
            )
            expression_voice = str(expression_voice_selection.get("prompt") or "")
            semantic_expression_rules = expression_voice_selection.get("rules")
            if isinstance(semantic_expression_rules, list) and semantic_expression_rules:
                try:
                    setattr(event, "private_companion_semantic_expression_rules", semantic_expression_rules)
                    setattr(
                        event,
                        "private_companion_semantic_expression_context",
                        dict(expression_voice_selection.get("context") or {}),
                    )
                except Exception:
                    pass
            if expression_voice:
                add_spec(
                    "expression.voice",
                    "已审核的表达学习规则",
                    "expression",
                    68,
                    lambda: expression_voice,
                    metadata={"范围": "全局抽象表达底色", "目标": expression_user_id},
                )

        async def timer_context() -> str:
            if not (self.enable_llm_timer_scheduling and is_private_chat):
                return ""
            try:
                target_user_id = str(event.get_sender_id())
            except Exception:
                target_user_id = ""
            resolver = getattr(self, "_private_user_id_for_event", None)
            if callable(resolver) and target_user_id:
                target_user_id = resolver(event, target_user_id)
            if not target_user_id:
                return ""
            async with self._data_lock:
                timer_user = dict(self._get_user(target_user_id))
                enabled = bool(timer_user.get("enabled"))
            return section_call(self._format_timer_scheduling_instruction, timer_user) if enabled else ""

        add_spec("timer.scheduling", "临时预约与动作回访", "timer", 95, timer_context, timeout=0.5)
        return await self._collect_prompt_contexts_parallel(specs)

    async def _format_passive_environment_fragment(
        self,
        event: AstrMessageEvent,
        *,
        lightweight: bool = False,
        include_heading: bool = True,
    ) -> str:
        if not lightweight:
            return await self._format_environment_perception(
                event,
                include_heading=include_heading,
            )
        if not self._feature_enabled_or_temp_unlocked("enable_environment_perception"):
            return ""
        current = self._environment_now()
        lines = [
            "这是当前消息的轻量背景边界，主要影响时间感、平台语境和回复节奏；如果用户刚好在问时间、平台或环境感受，可以按需要自然带出，没问到时就只当背景参考。",
            f"时间：{current.strftime('%Y-%m-%d %H:%M')}",
            "时间锚点必须以这一行真实时间为准；不要把未来日程、睡眠段、旧记忆或上次对话里的时间说成当前时间。",
        ]
        current_minutes = current.hour * 60 + current.minute
        if not (22 * 60 <= current_minutes or current_minutes <= 90):
            lines.append(
                "当前没有进入深夜时段；即使人格、作息或旧上下文提到“可能很晚”“晚上睡觉”，也不能主动说快十一点、困不困、该睡了或晚安。"
            )
        platform = await self._format_platform_perception(event)
        if platform:
            lines.append(f"会话：{platform}")
        body = "\n".join(lines)
        return f"【轻量环境感知】\n{body}" if include_heading else body

    async def _format_passive_environment_prompt_section(
        self,
        event: AstrMessageEvent,
        *,
        lightweight: bool = False,
    ) -> dict[str, Any]:
        return prompt_section(
            "轻量环境感知" if lightweight else "环境感知",
            await self._format_passive_environment_fragment(
                event,
                lightweight=lightweight,
                include_heading=False,
            ),
        )

    async def _append_capability_boundary_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        marker = "<!-- private_companion_capability_boundary_v1 -->"
        current_prompt = req.system_prompt or ""
        if marker in current_prompt:
            return
        boundary = (
            "你不能假装自己能影响现实、网络、游戏房间、他人设备或用户身体动作。"
            "没有可用工具且没有实际执行结果时,不要承诺“我这就拉你/我帮你操作/我已经处理/我去修/我给你弄好”。"
            "遇到拉人、开房间、修网、重启、登录、下载、现实代办等请求,只能自然说明自己做不到实际操作,可以提醒、陪用户确认、建议对方找能操作的人,或在确有工具时调用工具后再描述结果。"
        )
        platform_boundary_getter = getattr(self, "_platform_capability_prompt", None)
        if callable(platform_boundary_getter):
            platform_boundary = platform_boundary_getter(event)
            if platform_boundary:
                boundary = f"{boundary}\n\n{platform_boundary}"
        self._materialize_conversation_system_block(
            req,
            key="guard.capability_boundary",
            marker=marker,
            content=boundary,
            title="能力边界",
            priority=30,
            source="guard",
            placement=PLACEMENT_DYNAMIC_SYSTEM,
        )
        await self._record_request_prompt_fragment(
            event,
            title="能力边界注入",
            key="capability.boundary",
            text=boundary,
            source="guard",
            mode="group",
        )

    async def _append_media_delivery_truth_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        sections = self._media_delivery_truth_prompt_sections()
        media_truth_marker = "<!-- private_companion_media_delivery_truth_v1 -->"
        if not sections or self._request_has_managed_prompt_marker(req, media_truth_marker):
            return
        plan = get_conversation_injection_plan(req)
        for index, section in enumerate(sections):
            plan.materialize_system_block(
                req,
                key=f"tools.media_delivery_truth.{index}",
                marker=media_truth_marker if index == 0 else "",
                content=section,
                priority=30 + index,
                source="tools",
                placement=PLACEMENT_STABLE_SYSTEM,
            )
        media_truth_instruction = self._media_delivery_truth_instruction()
        await self._record_request_prompt_fragment(
            event,
            title="媒体发送真实性约束",
            key="tools.media_delivery_truth",
            text=media_truth_instruction,
            source="tools",
            mode="always",
            metadata={"注入位置": "system_prompt"},
        )

    async def _append_conditional_tool_instructions_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        message_text = str(getattr(event, "message_str", "") or "")
        current_prompt = req.system_prompt or ""
        atrelay_instruction = self._atrelay_tool_instruction(include_heading=False)
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        atrelay_marker = "<!-- private_companion_atrelay_tools_v1 -->"
        if atrelay_instruction and atrelay_marker not in current_prompt and atrelay_marker not in current_turn_prompt:
            if self._message_looks_like_atrelay_request(message_text):
                await self._append_atrelay_target_summary_to_request(event, req)
                current_prompt = req.system_prompt or ""
                current_turn_prompt = str(getattr(req, "prompt", "") or "")
                placement = "prompt" if self._append_turn_prompt_fragment_by_position(
                    req,
                    atrelay_marker,
                    atrelay_instruction,
                    title="跨会话转述与 @ 群友工具",
                    priority=88,
                    source="tools",
                ) else "system_prompt"
                if placement == "system_prompt":
                    current_prompt = f"{current_prompt}\n\n{atrelay_marker}\n{atrelay_instruction}".strip()
                    req.system_prompt = current_prompt
                await self._record_request_prompt_fragment(
                    event,
                    title="跨群转述工具注入",
                    key="tools.atrelay",
                    text=atrelay_instruction,
                    source="tools",
                    mode="conditional",
                    metadata={"注入位置": placement},
                )
        relation_instruction = self._relation_lookup_instruction(include_heading=False)
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        relation_marker = "<!-- private_companion_relation_lookup_v1 -->"
        try:
            relation_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            relation_private = ":FriendMessage:" in str(getattr(event, "unified_msg_origin", "") or "")
        relation_query = any(token in message_text for token in ("查关系网", "关系网查", "查一下关系", "查查关系"))
        relation_query = relation_query or (
            any(token in message_text for token in ("查一下", "查查", "帮我查", "查一查"))
            and (
                bool(re.search(r"\d{5,12}", message_text))
                or any(token in message_text for token in ("这个人", "这人", "那个人", "那人", "是谁", "认识"))
            )
        )
        livingmemory_relation_context = (
            relation_private
            and bool(getattr(self, "enable_livingmemory_integration", False))
            and bool(getattr(self, "_livingmemory_available", lambda: False)())
        )
        if relation_private and relation_instruction and relation_marker not in current_prompt and relation_marker not in current_turn_prompt and (relation_query or livingmemory_relation_context):
            placement = "prompt" if self._append_turn_prompt_fragment_by_position(
                req,
                relation_marker,
                relation_instruction,
                title="关系网查询",
                priority=87,
                source="tools",
            ) else "system_prompt"
            if placement == "system_prompt":
                current_prompt = f"{current_prompt}\n\n{relation_marker}\n{relation_instruction}".strip()
                req.system_prompt = current_prompt
            await self._record_request_prompt_fragment(
                event,
                title="关系网查询工具注入",
                key="tools.relation_lookup",
                text=relation_instruction,
                source="tools",
                mode="conditional",
                metadata={"注入位置": placement, "触发原因": "livingmemory" if livingmemory_relation_context and not relation_query else "query"},
            )
        qzone_instruction = self._qzone_tool_instruction(event, include_heading=False)
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        qzone_marker = "<!-- private_companion_qzone_tools_v1 -->"
        if qzone_instruction and qzone_marker not in current_prompt and qzone_marker not in current_turn_prompt:
            if any(token in message_text for token in ("说说", "空间", "QQ空间", "动态", "点赞", "评论")):
                placement = "prompt" if self._append_turn_prompt_fragment_by_position(
                    req,
                    qzone_marker,
                    qzone_instruction,
                    title="QQ 空间动态工具",
                    priority=88,
                    source="tools",
                ) else "system_prompt"
                if placement == "system_prompt":
                    current_prompt = f"{current_prompt}\n\n{qzone_marker}\n{qzone_instruction}".strip()
                    req.system_prompt = current_prompt
                await self._record_request_prompt_fragment(
                    event,
                    title="QQ 空间工具注入",
                    key="tools.qzone",
                    text=qzone_instruction,
                    source="tools",
                    mode="conditional",
                    metadata={"注入位置": placement},
                )
        schedule_management_instruction = self._schedule_management_tool_instruction(
            include_heading=False,
        )
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        schedule_management_marker = "<!-- private_companion_schedule_management_v1 -->"
        try:
            schedule_management_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            schedule_management_private = ":FriendMessage:" in str(getattr(event, "unified_msg_origin", "") or "")
        if (
            schedule_management_private
            and self._can_manage_private_companion(event)
            and self._schedule_management_instruction_matches(message_text)
            and schedule_management_instruction
            and schedule_management_marker not in current_prompt
            and schedule_management_marker not in current_turn_prompt
        ):
            placement = "prompt" if self._append_turn_prompt_fragment_by_position(
                req,
                schedule_management_marker,
                schedule_management_instruction,
                title="指定日程管理工具",
                priority=88,
                source="tools",
            ) else "system_prompt"
            if placement == "system_prompt":
                current_prompt = f"{current_prompt}\n\n{schedule_management_marker}\n{schedule_management_instruction}".strip()
                req.system_prompt = current_prompt
            await self._record_request_prompt_fragment(
                event,
                title="指定日程管理工具注入",
                key="tools.schedule_management",
                text=schedule_management_instruction,
                source="tools",
                mode="conditional",
                metadata={"注入位置": placement},
            )
        memo_instruction = self._memo_management_tool_instruction(include_heading=False)
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        memo_marker = "<!-- private_companion_memo_management_v1 -->"
        try:
            memo_private = bool(getattr(event, "is_private_chat", lambda: False)())
            identity_for_event = getattr(self, "_event_permission_identity_id", None)
            memo_requester = (
                identity_for_event(event)
                if callable(identity_for_event)
                else self._permission_identity_id(event.get_sender_id())
            )
        except Exception:
            memo_private = ":FriendMessage:" in str(getattr(event, "unified_msg_origin", "") or "")
            memo_requester = ""
        memo_owner = bool(memo_requester and self._is_private_companion_owner_user_id(memo_requester))
        memo_request = bool(
            memo_private
            and memo_owner
            and self._memo_management_instruction_matches(message_text)
        )
        if memo_request:
            self._mark_memo_request_tool_boundary(event, req)
            if self._remove_future_task_for_memo_request(req, message_text):
                logger.debug(
                    "[PrivateCompanion] 明确便签请求已从初始工具集移除 future_task: session=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                )
        if (
            memo_request
            and memo_instruction
            and memo_marker not in current_prompt
            and memo_marker not in current_turn_prompt
        ):
            placement = "prompt" if self._append_turn_prompt_fragment_by_position(
                req,
                memo_marker,
                memo_instruction,
                title="备忘便签工具",
                priority=88,
                source="tools",
            ) else "system_prompt"
            if placement == "system_prompt":
                current_prompt = f"{current_prompt}\n\n{memo_marker}\n{memo_instruction}".strip()
                req.system_prompt = current_prompt
            await self._record_request_prompt_fragment(
                event,
                title="备忘便签工具注入",
                key="tools.memo_management",
                text=memo_instruction,
                source="tools",
                mode="conditional",
                metadata={"注入位置": placement},
            )

        creative_work_instruction = self._creative_work_tool_instruction(
            include_heading=False,
        )
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        creative_work_marker = "<!-- private_companion_creative_work_tool_v1 -->"
        try:
            creative_work_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            creative_work_private = ":FriendMessage:" in str(getattr(event, "unified_msg_origin", "") or "")
        if (
            creative_work_private
            and creative_work_instruction
            and self._creative_work_query_instruction_matches(message_text)
            and creative_work_marker not in current_prompt
            and creative_work_marker not in current_turn_prompt
        ):
            try:
                setattr(event, "private_companion_creative_work_tool_required", True)
            except Exception:
                pass
            placement = "prompt" if self._append_turn_prompt_fragment_by_position(
                req,
                creative_work_marker,
                creative_work_instruction,
                title="资料柜与自己的创作读取工具",
                priority=89,
                source="tools",
            ) else "system_prompt"
            if placement == "system_prompt":
                current_prompt = f"{current_prompt}\n\n{creative_work_marker}\n{creative_work_instruction}".strip()
                req.system_prompt = current_prompt
            await self._record_request_prompt_fragment(
                event,
                title="创作正文读取工具注入",
                key="tools.creative_work",
                text=creative_work_instruction,
                source="tools",
                mode="conditional",
                metadata={"注入位置": placement},
            )

        await self._append_media_delivery_truth_to_request(event, req)
        explicit_photo_request = self._photo_generation_instruction_matches(message_text)
        explicit_media_delivery_request = self._current_media_delivery_instruction_matches(message_text)
        referenced_media_edit_request = False
        if (
            not explicit_photo_request
            and self._referenced_media_edit_instruction_matches(message_text)
        ):
            finder = getattr(self, "_find_reply_image_sources_for_event", None)
            if callable(finder):
                try:
                    referenced_media_edit_request = bool(await finder(event))
                except Exception as exc:
                    logger.debug(
                        "[PrivateCompanion] 引用图片编辑意图确认失败: session=%s error=%s",
                        _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                        _single_line(exc, 160),
                    )
        explicit_media_request = bool(
            explicit_photo_request
            or explicit_media_delivery_request
            or referenced_media_edit_request
        )
        reaction_expression_authorized = False
        if (
            not explicit_media_request
            and bool(runtime_persona_setting(self, 'enable_reaction_expression_experiment', False))
        ):
            reaction_expression_authorized = await self._preauthorize_reaction_expression_prompt(event)
        reaction_expression_evaluated = bool(
            self._reaction_expression_authorization(event)
        )
        removed_reaction_tools = self._scope_reaction_media_tools_for_request(
            req,
            explicit_media_request=explicit_media_request,
            reaction_authorized=reaction_expression_authorized,
            reaction_evaluated=reaction_expression_evaluated,
        )
        if removed_reaction_tools:
            self._log_reaction_expression_event(
                event,
                stage="authorization",
                decision="scoped",
                reason="media_tools_scoped",
                scope=self._reaction_expression_scope(event),
            )
        photo_instruction = self._photo_generation_tool_instruction(
            include_spontaneous=reaction_expression_authorized,
            spontaneous_only=reaction_expression_authorized and not explicit_media_request,
            include_heading=False,
        )
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        photo_marker = "<!-- private_companion_photo_generation_tool_v1 -->"
        if photo_instruction and photo_marker not in current_prompt and photo_marker not in current_turn_prompt:
            if explicit_media_request or reaction_expression_authorized:
                placement = "prompt" if self._append_turn_prompt_fragment_by_position(
                    req,
                    photo_marker,
                    photo_instruction,
                    title=(
                        "实验性表情表达"
                        if reaction_expression_authorized and not explicit_media_request
                        else "图库表情与生图工具"
                    ),
                    priority=88,
                    source="tools",
                ) else "system_prompt"
                if placement == "system_prompt":
                    current_prompt = f"{current_prompt}\n\n{photo_marker}\n{photo_instruction}".strip()
                    req.system_prompt = current_prompt
                await self._record_request_prompt_fragment(
                    event,
                    title=(
                        "实验性表情表达工具注入"
                        if reaction_expression_authorized and not explicit_media_request
                        else "生图工具注入"
                    ),
                    key=(
                        "tools.reaction_expression"
                        if reaction_expression_authorized and not explicit_media_request
                        else "tools.photo_generation"
                    ),
                    text=photo_instruction,
                    source="tools",
                    mode="conditional",
                    metadata={
                        "注入位置": placement,
                        "预授权": bool(reaction_expression_authorized),
                    },
                )
        cross_user_instruction = self._cross_user_memory_query_instruction(
            include_heading=False,
        )
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        cross_user_marker = "<!-- private_companion_cross_user_memory_v1 -->"
        if cross_user_instruction and cross_user_marker not in current_prompt and cross_user_marker not in current_turn_prompt:
            if any(token in message_text for token in (
                "聊了什么", "说了什么", "发了什么", "讲了什么", "互动", "和谁聊", "跟谁聊", "最近跟", "最近和",
                "你和", "你跟", "在群里", "那个群", "这个群", "私聊过", "聊过",
            )):
                placement = "prompt" if self._append_turn_prompt_fragment_by_position(
                    req,
                    cross_user_marker,
                    cross_user_instruction,
                    title="跨用户记忆互通",
                    priority=88,
                    source="tools",
                ) else "system_prompt"
                if placement == "system_prompt":
                    current_prompt = f"{current_prompt}\n\n{cross_user_marker}\n{cross_user_instruction}".strip()
                    req.system_prompt = current_prompt
                await self._record_request_prompt_fragment(
                    event,
                    title="跨用户记忆互通工具注入",
                    key="tools.cross_user_memory",
                    text=cross_user_instruction,
                    source="tools",
                    mode="conditional",
                    metadata={"注入位置": placement},
                )

    @filter.on_agent_begin()
    @_multi_persona_event_context
    async def enforce_memo_reminder_tool_boundary(self, event: AstrMessageEvent, run_context: Any, *args, **kwargs):
        """AstrBot 会在请求钩子之后补内置工具，因此在 Agent 启动时做最终互斥。"""
        if self is None or event is None:
            return
        await self._acknowledge_official_llm_timer_trigger(event)
        self._finalize_passive_reply_tool_boundary(event)
        if self._finalize_memo_request_tool_boundary(event):
            logger.info(
                "[PrivateCompanion] 明确便签请求已从最终工具集移除 future_task,避免重复提醒: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )

    @filter.on_llm_tool_respond()
    @_multi_persona_event_context
    async def capture_future_task_result(
        self,
        event: AstrMessageEvent,
        tool: Any,
        tool_args: dict[str, Any] | None,
        tool_result: Any,
        *args,
        **kwargs,
    ):
        """记录官方定时与创作读取工具的真实结果，供响应阶段可靠校验。"""
        if self is None or event is None:
            return
        await self._record_official_llm_timer_tool_result(event, tool, tool_result)
        if self._record_future_task_result(event, tool, tool_args, tool_result):
            logger.info(
                "[PrivateCompanion] 已记录本轮 future_task 成功: action=%s session=%s",
                _single_line((tool_args or {}).get("action"), 20) or "unknown",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
        if self._record_creative_work_tool_result(event, tool, tool_args, tool_result):
            logger.info(
                "[PrivateCompanion] 已记录本轮创作读取工具结果: action=%s status=%s inventory_complete=%s session=%s",
                _single_line((tool_args or {}).get("action") if isinstance(tool_args, dict) else "", 20) or "get",
                _single_line(getattr(event, "private_companion_creative_work_tool_status", ""), 24) or "unknown",
                bool(getattr(event, "private_companion_bookshelf_inventory_complete", False)),
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )

    @filter.on_agent_done()
    @_multi_persona_event_context
    async def complete_official_llm_timer_lifecycle(
        self,
        event: AstrMessageEvent,
        run_context: Any,
        response: Any,
        *args,
        **kwargs,
    ):
        """Only finalize timer state when the cron event matches this plugin's timer id/job id."""
        if self is None or event is None:
            return
        await self._complete_official_llm_timer_event(event)

    def _is_lightweight_private_passive_inbound(self, text: str) -> bool:
        cleaned = _single_line(text, 80)
        if not cleaned:
            return False
        if len(cleaned) > 18:
            return False
        weather_query_detector = getattr(self, "_user_asks_current_weather", None)
        if callable(weather_query_detector) and weather_query_detector(cleaned):
            return False
        outfit_change_detector = getattr(self, "_detect_dialogue_outfit_change", None)
        if callable(outfit_change_detector):
            try:
                if outfit_change_detector(cleaned):
                    return False
            except Exception:
                pass
        heavy_tokens = (
            "图片", "看图", "照片", "语音", "引用", "转发", "聊天记录",
            "帮我", "怎么", "为什么", "是什么", "怎么办", "分析", "解释", "总结",
            "日程", "状态", "近况", "在干嘛", "做什么", "忙什么",
            "资料柜", "夹层", "抽屉", "阅读", "读过", "看过", "素材", "资料", "漫画", "藏本",
            "创作", "作品", "写作", "写书", "写过书", "小说", "随笔", "散文", "剧本", "手稿", "草稿", "出版",
            "新闻", "说说", "空间", "发给", "转告", "@",
        )
        if any(token in cleaned for token in heavy_tokens):
            return False
        bookshelf_checker = getattr(self, "_user_asks_bookshelf_reading_memory", None)
        if callable(bookshelf_checker) and bookshelf_checker(cleaned):
            return False
        creative_checker = getattr(self, "_user_asks_recent_creative_activity", None)
        if callable(creative_checker) and creative_checker(cleaned):
            return False
        return True

    @staticmethod
    def _is_private_routine_check_invocation(text: str) -> bool:
        cleaned = _single_line(text, 80)
        if not cleaned or len(cleaned) > 28:
            return False
        compact = re.sub(r"[\s，。！？!?,.、~～…]+", "", cleaned)
        markers = ("例行检查", "日常检查", "每日检查", "晚间检查", "夜间检查")
        prefixes = (
            "开始", "来", "继续", "进行", "该",
            "那", "那么", "那就", "嗯", "嗯那", "嗯那就", "好", "好吧", "好那就",
        )
        suffixes = ("啦", "咯", "了", "开始", "时间", "时间到", "一下")
        variants = set(markers)
        for marker in markers:
            variants.update(f"{prefix}{marker}" for prefix in prefixes)
            variants.update(f"{marker}{suffix}" for suffix in suffixes)
            variants.update(f"{prefix}{marker}{suffix}" for prefix in prefixes for suffix in suffixes)
        return compact in variants

    def _format_private_routine_check_boundary(
        self,
        text: str,
        *,
        include_heading: bool = True,
    ) -> str:
        if not self._is_private_routine_check_invocation(text):
            return ""
        return (
            ("【轻量例行检查边界】\n" if include_heading else "")
            +
            "用户正在发起一次例行检查，但这不等于要求你自动展开固定健康清单。\n"
            "优先承接当前原始对话或可靠记忆中已经明确的双方约定；整次回复最多两个短句、最多提出一个问题。\n"
            "开头若有语气词和称呼，要和后面的承接正文自然写在同一句里，不要把“嗯，某某”“唔，某某大人”单独拆成一条消息。\n"
            "只询问当前消息、最近原始对话、明确提醒/便签或可靠记忆实际支持的项目。没有依据时，不要假定用户正在服药、生病、没吃饭或遗漏了某项现实任务。\n"
            "如果没有明确检查项目，就自然问今天想先检查哪一项；不要一口气连续追问晚饭、吃药和睡觉。"
        )

    def _format_private_routine_check_boundary_section(
        self,
        text: str,
    ) -> dict[str, Any]:
        return prompt_section(
            "轻量例行检查边界",
            self._format_private_routine_check_boundary(
                text,
                include_heading=False,
            ),
        )

    def _limit_private_routine_check_segments(self, text: str, chunks: list[list[Any]]) -> list[list[Any]]:
        if not self._is_private_routine_check_invocation(text):
            return chunks
        limited = list(chunks or [])
        if len(limited) >= 2 and all(
            part and all(isinstance(component, Plain) for component in part)
            for part in limited[:2]
        ):
            lead = "".join(str(getattr(component, "text", "") or "") for component in limited[0]).strip()
            following = "".join(str(getattr(component, "text", "") or "") for component in limited[1]).strip()
            match = re.fullmatch(
                r"(唔|嗯|哦|啊|诶|欸|哎|唉)([\s，,、…~～]+)([\u4e00-\u9fffA-Za-z0-9·]{1,10})[\s，。！？!?,.、…~～]*",
                lead,
            )
            address = match.group(3) if match else ""
            address_titles = ("大人", "主人", "老师", "先生", "小姐", "同学", "哥哥", "姐姐", "前辈", "殿下")
            non_address_phrases = ("知道", "明白", "收到", "可以", "没事", "不用", "不要", "好了", "好吧")
            looks_like_address = bool(
                address
                and (
                    address.endswith(address_titles)
                    or (len(address) <= 4 and not any(token in address for token in non_address_phrases))
                )
            )
            if looks_like_address and following:
                separator = "" if re.search(r"[，,。！？!?、…~～]$", lead) else "，"
                limited = [[Plain(f"{lead}{separator}{following}")], *limited[2:]]
        if len(limited) <= 2:
            return limited
        return [limited[0], flatten_component_chunks(limited[1:])]

    def _private_passive_state_fingerprint(self, state: dict[str, Any], current_user: dict[str, Any] | None = None) -> dict[str, Any]:
        now = self._environment_now()
        time_label, _ = self._current_time_period_label(now)
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        activity = _single_line(current_item.get("activity"), 50) if isinstance(current_item, dict) else ""
        detail = self._current_detail_segment_for_update()
        detail_key = _single_line(detail.get("key"), 80) if isinstance(detail, dict) else ""
        detail_summary = _single_line(detail.get("summary"), 80) if isinstance(detail, dict) else ""
        friend_user = self._private_user_role(current_user or {}) == "friend"
        weather = "" if friend_user else _single_line(state.get("weather"), 60)
        conditions: list[str] = []
        raw_conditions = state.get("conditions")
        if isinstance(raw_conditions, list):
            for cond in raw_conditions[:3]:
                if not isinstance(cond, dict) or not self._should_show_condition(cond):
                    continue
                label = _single_line(cond.get("label") or cond.get("title") or cond.get("kind"), 18)
                if label and label not in conditions:
                    conditions.append(label)
        cycle_profile = self._active_body_cycle_profile(state)
        return {
            "date": _today_key(),
            "time_label": time_label,
            "energy_bracket": (energy // 10) * 10,
            "mood": _single_line(state.get("mood_bias"), 18) or "平稳",
            "activity": self._sanitize_schedule_context_for_private_user(activity, current_user or {}) if activity else "",
            "detail": detail_key or detail_summary,
            "weather": weather if weather and weather != "暂无天气信息" else "",
            "conditions": conditions[:2],
            "body_cycle": _single_line(state.get("body_cycle"), 120) if cycle_profile else "",
            "body_cycle_phase": _single_line(cycle_profile.get("phase"), 24),
        }

    def _format_private_passive_state_snapshot(
        self,
        state: dict[str, Any],
        current_user: dict[str, Any] | None,
        *,
        direct: bool = False,
        include_heading: bool = True,
    ) -> str:
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        mood = _single_line(state.get("mood_bias"), 18) or "平稳"
        now = self._environment_now()
        time_label, _ = self._current_time_period_label(now)
        pieces = [f"时间节奏：{time_label}", f"精神约 {energy}/100", f"情绪底色偏{mood}"]
        realtime_formatter = getattr(self, "_format_external_realtime_context_for_prompt", None)
        realtime_context = realtime_formatter(current_user, public=False) if callable(realtime_formatter) else ""
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        schedule = self._sanitize_schedule_context_for_private_user(
            self._format_plan_item_for_prompt(current_item),
            current_user or {},
        )
        if schedule and not realtime_context:
            pieces.append(f"拟人化日程素材：{schedule}")
        elif schedule:
            pieces.append(f"原定日程素材（已被实时共同活动覆盖）：{schedule}")
        detail = self._current_detail_segment_for_update()
        if isinstance(detail, dict):
            summary = _single_line(detail.get("summary"), 90)
            if summary:
                pieces.append(f"模拟氛围：{summary}")
        weather = _single_line(state.get("weather"), 60)
        if self._private_user_role(current_user or {}) == "friend":
            weather = ""
        if weather and weather != "暂无天气信息":
            pieces.append(f"天气素材：{weather}")
        conditions: list[str] = []
        raw_conditions = state.get("conditions")
        if isinstance(raw_conditions, list):
            for cond in raw_conditions[:3]:
                if not isinstance(cond, dict) or not self._should_show_condition(cond):
                    continue
                label = _single_line(cond.get("label") or cond.get("title") or cond.get("kind"), 18)
                if label and label not in conditions:
                    conditions.append(label)
        if conditions:
            pieces.append("身体感素材：" + "、".join(conditions[:2]))
        cycle_profile = self._active_body_cycle_profile(state)
        if cycle_profile:
            pieces.append(f"周期状态：Bot 当前{cycle_profile['stage']}")
        usage = (
            "本轮状态素材触发原因：用户直接询问 Bot 近况或状态。"
            if direct
            else "本轮状态素材触发原因：Bot 自身模拟状态发生变化。"
        )
        guidance = (
            "用户正在直接问 Bot 此刻在做什么或当前状态：先回答实时共同活动（若有），它高于固定日程、旧对话、旧记忆和临场发挥。"
            "固定日程只是原计划，若与实时共同活动冲突，必须说原计划被打断/覆盖，禁止继续声称仍在旧地点或旧动作中。"
            "若没有实时共同活动，先正面回答拟人化日程素材中的当前活动。"
            "不得另编素材未提供的动作、地点、饮食或娱乐活动。"
            "如果素材本身较笼统，就按原有粒度自然转述，例如只说正在专心处理手头的事；不要为了显得具体而补造细节。"
            if direct
            else "只用于语气、长短、节奏和轻微接话；不要把它改写成用户做过的事或现实已经发生的事件。"
        )
        blocks = [
            "以下只描述 Bot 的拟人化内部状态/场景素材，不是用户事实、不是现实证据，也不要写入长期记忆。",
        ]
        if include_heading:
            blocks.insert(0, "【Bot 自身模拟状态更新】")
        if realtime_context:
            blocks.append(realtime_context)
        blocks.extend([
            guidance,
            usage + " " + "；".join(pieces) + "。",
        ])
        return "\n".join(
            blocks
        )

    def _format_external_realtime_context_for_prompt(
        self,
        current_user: dict[str, Any] | None = None,
        *,
        public: bool = False,
        include_heading: bool = True,
    ) -> str:
        """Format extension state for ordinary private/group prompts.

        Active shared activity is authoritative over the schedule. Continuity is
        deliberately bounded and public views never include call transcript.
        """
        now = _now_ts()
        user = current_user if isinstance(current_user, dict) else {}
        user_id = _single_line(user.get("user_id"), 80)
        role = _single_line(user.get("relationship_role"), 24)
        role_getter = getattr(self, "_private_user_role", None)
        if callable(role_getter):
            try:
                role = _single_line(role_getter(user, user_id), 24)
            except TypeError:
                role = _single_line(role_getter(user), 24)
            except Exception:
                pass
        activity: dict[str, Any] = {}
        registry = getattr(self, "_external_realtime_activities", None)
        if isinstance(registry, dict):
            for key, item in list(registry.items()):
                if not isinstance(item, dict) or _safe_float(item.get("expires_at"), 0.0) <= now:
                    registry.pop(key, None)
                    continue
                item_user = _single_line(item.get("user_id"), 80)
                if item_user and item_user == user_id:
                    activity = dict(item)
                    break
                if not activity and (public or role != "owner"):
                    activity = dict(item)
        continuity: dict[str, Any] = {}
        continuity_registry = getattr(self, "_external_realtime_continuity", None)
        if isinstance(continuity_registry, dict) and user_id:
            item = continuity_registry.get(user_id)
            if isinstance(item, dict) and _safe_float(item.get("expires_at"), 0.0) > now:
                continuity = dict(item)
        if not activity and not continuity:
            return ""
        label = _single_line(activity.get("label"), 140) or {
            "shared_call": "正在和主要用户通话",
            "shared_watch": "正在和主要用户一起看视频",
            "shared_work": "正在和主要用户进行工作协同",
        }.get(_single_line(activity.get("kind"), 40), "正在进行共同活动")
        if public:
            return (
                ("【实时共同活动公开状态】\n" if include_heading else "")
                +
                f"{label}。这是当前优先级最高的实时事实，固定日程只是原计划。"
                "群聊只可概括说明正在与主要用户共同活动，不得透露电话内容、具体约定、逐字转写或私密地点。"
            )
        lines = [
            "实时共同活动是正在发生的高优先级事实；固定日程只是原计划，冲突时必须以实时活动为准。",
        ]
        if include_heading:
            lines.insert(0, "【实时共同活动与短期连续性】")
        if activity:
            lines.append(f"当前活动：{label}")
        summary = _single_line(continuity.get("summary"), 1800)
        if summary:
            lines.append(
                "最近通话/共同活动的短期连续性（带说话人归属，仅作自然接续，不是长期记忆）："
                + summary
            )
        return "\n".join(lines)

    def _format_external_realtime_prompt_section(
        self,
        current_user: dict[str, Any] | None = None,
        *,
        public: bool = False,
    ) -> dict[str, Any]:
        return prompt_section(
            "实时共同活动公开状态" if public else "实时共同活动与短期连续性",
            self._format_external_realtime_context_for_prompt(
                current_user,
                public=public,
                include_heading=False,
            ),
        )

    def _private_passive_state_reply_policy_prompt(
        self,
        *,
        include_heading: bool = True,
    ) -> str:
        lines = [
                "先自然回应用户当前表达；主动提供一处与 Bot 自身有关的具体细节；不要逐项汇报状态，也不要把内部素材描述成已经证实的现实事件。",
                "不要把回复写成连续盘问；整次回复最多提出一个问题；没有必要时可以不提问。",
                "当前用户最后一条消息是本轮唯一的主线：先接住其中的具体词、问题或情绪，再决定是否补充背景。旧话题、未完成话头和状态素材只有在与当前内容有明确语义连接时才轻轻带过；不贴合就留在背景里，不要为了连续性硬拽回来。",
                "话题确实转向时，用当前消息里的连接点自然过渡，不要凭空写“刚刚/刚才/前面”作为转场。相对时间词只在用户明确提到时间、或有可靠事实表明确实发生在那个时间段时使用；内部提示中的时间标签不得原样出现在回复里。",
        ]
        if include_heading:
            lines.insert(0, "【私聊被动回复策略】")
        return "\n".join(lines)

    def _format_private_passive_state_continuity_anchor(
        self,
        state: dict[str, Any],
        current_user: dict[str, Any] | None,
        *,
        include_heading: bool = True,
    ) -> str:
        now = self._environment_now()
        time_label, _ = self._current_time_period_label(now)
        pieces = [f"时段={time_label}"]

        raw_energy = state.get("energy") if isinstance(state, dict) else None
        if isinstance(raw_energy, (int, float)) and not isinstance(raw_energy, bool):
            energy = _safe_int(raw_energy, 70, 0, 100)
            energy_floor = min(90, (energy // 10) * 10)
            energy_ceiling = 100 if energy_floor == 90 else energy_floor + 9
            pieces.append(f"精力={energy_floor}-{energy_ceiling}/100")

        mood = (
            _single_line(state.get("mood_bias"), 18) if isinstance(state, dict) else ""
        )
        if mood:
            pieces.append(f"情绪底色={mood}")

        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        activity = ""
        scene_text = ""
        if isinstance(current_item, dict):
            scene_text = self._sanitize_schedule_model_artifacts(
                current_item.get("activity"), limit=72
            )
            future_marker = re.search(
                r"准备\s*(?:(?:先|再|马上|即将|随后|然后|接着|待会儿?|等会儿?|晚点|稍后)\s*)?"
                r"(?:去|到|回|前往|出发|开始|继续|做|处理|整理|收拾|上课|自习|洗漱|洗澡|睡觉|"
                r"出门|吃饭|用餐|跑步|散步|运动|锻炼|看书|读书|写作|买东西|买菜)|"
                r"正要|马上|即将|稍后|之后|随后|然后|接着|待会儿?|等会儿?|过(?:一)?会儿|一会儿后|"
                r"晚点|晚些时候|接下来|下一段|再(?:去|到|回|前往|开始|继续|做|处理|整理|收拾)|"
                r"(?:做|整理|收拾|写|看|读|处理)?完(?:后)?(?:再)?(?:去|到|回|前往)",
                scene_text,
            )
            if future_marker:
                scene_text = scene_text[: future_marker.start()].rstrip(" ，,；;。")
            if scene_text and self._daily_plan_clause_has_unsafe_social_fact(
                scene_text
            ):
                scene_text = ""
            if scene_text and re.search(
                r"用户|主要用户|当前用户|主人|对方|给你|和你|跟你|你在|你的|明天|后天|下周|未来|日程|计划|打算|将要",
                scene_text,
            ):
                scene_text = ""
            scene_text = self._sanitize_schedule_context_for_private_user(
                scene_text, current_user or {}
            )
            if scene_text and re.search(
                r"(?:^|[，,；;。])(?:准备|正要|要去|想去|去往|前往|出发|赶往|回到?)",
                scene_text,
            ):
                scene_text = ""
            action_match = re.search(
                r"(?:整理|收拾|看书|阅读|读书|写作|写字|写笔记|听歌|听音乐|休息|发呆|学习|"
                r"上课|自习|工作|处理|做饭|吃饭|用餐|洗漱|洗澡|睡觉|散步|运动|锻炼|画画|"
                r"练习|聊天|看电影|看视频|玩游戏|刷手机|喝咖啡|喝茶|做手工|晒太阳|通勤|买东西|买菜)"
                r"[^，,；;。]{0,52}",
                scene_text,
            )
            if action_match:
                activity = action_match.group(0).strip()
                if re.search(
                    r"(?:在|到|去|回|靠近|路过|位于|身处)[^，,；;。]{1,24}|"
                    r"[^，,；;。]{2,24}(?:省|市|区|县|镇|村|路|街|巷|号|小区|校区|商场|广场|"
                    r"大厦|园区|车站|机场|酒店|咖啡店|餐厅|公园|图书馆)",
                    activity,
                ):
                    activity = ""
        if activity:
            pieces.append(f"当前活动={_single_line(activity, 56)}")
        if scene_text:
            inferred_location = self._coarse_roleplay_location_text(
                self._infer_location_from_text(scene_text)
            )
            safe_location = self._sanitize_schedule_context_for_private_user(
                f"当前位置：{inferred_location}" if inferred_location else "",
                current_user or {},
            )
            if safe_location:
                pieces.append(f"粗略位置={inferred_location}")

        lines = [
                "这是 Bot 的拟人化模拟状态，不是用户事实、现实证据或长期记忆。",
                "当下素材（仅供隐性承接）：" + "；".join(pieces) + "。",
        ]
        if include_heading:
            lines.insert(0, "【Bot 当下连续性】")
        text = "\n".join(lines)
        return text[:300]

    def _private_passive_state_update_for_prompt(
        self,
        *,
        session: str,
        state: dict[str, Any],
        current_user: dict[str, Any] | None,
        inbound_text: str,
        lightweight: bool,
        as_sections: bool = False,
    ) -> tuple[Any, bool, str]:
        session_key = _single_line(session, 160) or "unknown"
        cache = getattr(self, "_passive_state_session_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._passive_state_session_cache = cache
        fingerprint = self._private_passive_state_fingerprint(state, current_user)
        previous = cache.get(session_key) if isinstance(cache.get(session_key), dict) else {}
        changed = previous.get("fingerprint") != fingerprint
        direct_state_request = self._user_asks_bot_current_state_or_activity(inbound_text) or self._user_asks_recent_bot_activity(inbound_text) or bool(
            re.search(r"(状态|日程|精力|心情|情绪|在干嘛|做什么|忙什么|近况)", str(inbound_text or ""))
        )
        now_ts = _now_ts()
        cache[session_key] = {
            "fingerprint": fingerprint,
            "ts": now_ts,
            "last_changed_ts": now_ts if changed else _safe_float(previous.get("last_changed_ts"), now_ts),
        }
        if len(cache) > 240:
            stale = sorted(
                ((key, _safe_float(value.get("ts"), 0)) for key, value in cache.items() if isinstance(value, dict)),
                key=lambda item: item[1],
            )
            for key, _ in stale[: max(0, len(cache) - 200)]:
                cache.pop(key, None)
        if direct_state_request:
            state_text = self._format_private_passive_state_snapshot(
                state,
                current_user,
                direct=True,
                include_heading=not as_sections,
            )
            state_changed = changed
            reason = "direct"
        elif changed:
            state_text = self._format_private_passive_state_snapshot(
                state,
                current_user,
                direct=False,
                include_heading=not as_sections,
            )
            state_changed = True
            reason = "changed"
        elif bool(runtime_persona_setting(self, 'enable_passive_state_continuity_anchor', False)):
            state_text = self._format_private_passive_state_continuity_anchor(
                state,
                current_user,
                include_heading=not as_sections,
            )
            state_changed = False
            reason = "continuity_anchor"
        else:
            return "", False, "unchanged_light" if lightweight else "unchanged"

        if reason == "continuity_anchor":
            reply_policy = (
                ("【私聊被动回复策略】\n" if not as_sections else "")
                + "先自然回应用户当前表达；主动提供一处与 Bot 自身有关的具体细节；不要逐项汇报状态；不要把回复写成连续盘问；整次回复最多提出一个问题；没有必要时可以不提问。"
            )
            state_text = state_text[: max(0, 300 - len(reply_policy) - 1)].rstrip()
        else:
            reply_policy = self._private_passive_state_reply_policy_prompt(
                include_heading=not as_sections,
            )
        if as_sections:
            state_title = (
                "Bot 当下连续性"
                if reason == "continuity_anchor"
                else "Bot 自身模拟状态更新"
            )
            return (
                [
                    prompt_section(state_title, state_text),
                    prompt_section("私聊被动回复策略", reply_policy),
                ],
                state_changed,
                reason,
            )
        return f"{state_text}\n{reply_policy}", state_changed, reason

    async def _append_group_active_period_boundary_to_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        group_id: str,
    ) -> str:
        if not group_id:
            return ""
        try:
            state = await self._ensure_daily_state(
                skip_conversation_summary=True,
                passive_fast=True,
            )
            boundary = self._format_active_period_boundary_for_prompt(
                state,
                public=True,
                include_heading=False,
            )
        except Exception as exc:
            logger.debug(
                "[PrivateCompanion] 群聊读取经期互动边界失败，已跳过: group=%s error=%s",
                _single_line(group_id, 40) or "-",
                _single_line(exc, 120),
            )
            return ""
        if not boundary:
            return ""

        marker = "<!-- private_companion_period_boundary_v1 -->"
        current_prompt = str(getattr(req, "system_prompt", "") or "")
        if self._request_has_managed_prompt_marker(req, marker):
            return boundary
        placement = "prompt" if self._append_turn_prompt_fragment_by_position(
            req,
            marker,
            boundary,
            title="Bot 当前经期与互动边界",
            priority=89,
            source="daily_state",
        ) else "system_prompt"
        if placement == "system_prompt":
            req.system_prompt = f"{current_prompt}\n\n{marker}\n{boundary}".strip()
        await self._record_request_prompt_fragment(
            event,
            title="群聊经期互动边界",
            key="state.period_boundary",
            text=boundary,
            source="daily_state",
            mode="group",
            priority=89,
            metadata={"注入位置": placement, "群号": _single_line(group_id, 40)},
        )
        return boundary

    async def _append_private_active_period_boundary_to_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        state: dict[str, Any],
    ) -> str:
        boundary = self._format_active_period_boundary_for_prompt(
            state,
            public=False,
            include_heading=False,
        )
        if not boundary:
            return ""
        marker = "<!-- private_companion_period_boundary_v1 -->"
        if self._request_has_managed_prompt_marker(req, marker):
            return boundary
        placement = "prompt" if self._append_turn_prompt_fragment_by_position(
            req,
            marker,
            boundary,
            title="Bot 当前经期与互动边界",
            priority=89,
            source="daily_state",
        ) else "system_prompt"
        if placement == "system_prompt":
            current_prompt = str(getattr(req, "system_prompt", "") or "")
            req.system_prompt = f"{current_prompt}\n\n{marker}\n{boundary}".strip()
        await self._record_request_prompt_fragment(
            event,
            title="私聊经期互动边界",
            key="state.period_boundary",
            text=boundary,
            source="daily_state",
            mode="private",
            priority=89,
            metadata={"注入位置": placement},
        )
        return boundary

    def _add_private_active_period_boundary_to_surface(
        self,
        prompt_surface: PromptSurface,
        state: dict[str, Any],
    ) -> str:
        boundary = self._format_active_period_boundary_for_prompt(
            state,
            public=False,
            include_heading=False,
        )
        if boundary:
            prompt_surface.add(
                "state.period_boundary",
                boundary,
                title="Bot 当前经期与互动边界",
                priority=89,
                source="daily_state",
            )
        return boundary

    def _format_group_persona_denoise_prompt(
        self,
        event: AstrMessageEvent | None = None,
        *,
        include_heading: bool = True,
    ) -> str:
        if not bool(runtime_persona_setting(self, 'enable_group_persona_denoise', True)):
            return ""
        scene = getattr(event, "private_companion_group_scene", None) if event is not None else None
        trigger = _single_line(scene.get("trigger"), 40) if isinstance(scene, dict) else ""
        high_intensity = getattr(event, "private_companion_group_high_intensity", None) if event is not None else None
        high_active = isinstance(high_intensity, dict) and bool(high_intensity.get("active"))
        sender_id = ""
        sender_display_name = ""
        sender_is_target = False
        sender_name_conflicts_with_address = False
        if event is not None:
            try:
                sender_id = _single_line(str(event.get_sender_id()), 40)
            except Exception:
                sender_id = ""
            try:
                sender_display_name = _single_line(self._sender_display_name(event), 40)
            except Exception:
                sender_display_name = ""
            if sender_id:
                users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
                resolver = getattr(self, "_private_user_id_for_event", None)
                scoped_sender_id = (
                    resolver(event, sender_id)
                    if callable(resolver)
                    else self._canonical_private_user_id(sender_id)
                )
                current_user = users.get(scoped_sender_id) if isinstance(users, dict) else None
                sender_is_target = self._is_target_private_user(
                    scoped_sender_id,
                    current_user if isinstance(current_user, dict) else None,
                )
                conflict_checker = getattr(
                    self,
                    "_group_display_name_address_conflict",
                    None,
                )
                if callable(conflict_checker):
                    sender_name_conflicts_with_address = bool(
                        conflict_checker(scoped_sender_id, sender_display_name)
                    )
        lines = [
            "这是群聊场景，更适合先接住当前被问到的事或眼前话题，语气也尽量比私聊更轻一点。",
            "群聊里的身份优先按平台稳定 ID 理解；昵称、群名片、角色名、别名和“通常是谁”这类设定，更适合作为称呼线索，不直接当成身份结论。",
            "提到群聊旧消息、群梗、记忆召回或最近群聊时，尽量保留具体成员名或 QQ 标签，例如“A[QQ:...] 说过/起哄过”；只有确实缺少成员线索时，再概括成“群里有人”。除非当前消息或引用明确就是这位发言者，尽量不要顺手改写成“你说过”“主要用户说过”这类直接归到当前对象身上的表达。",
            "群成员画像只用于自然理解当前对话：当前发言者明确询问自己时，最多概括可公开的低敏偏好；不要替任何人整理、推断或披露第三方画像。普通群聊提到某人的爱好、习惯或偏好只是聊天内容，不要把它误当成对你的查询。",
            "【群聊玩笑边界】群聊里“记住了/记下某人是XX”这类话（尤其把某人当对象、或带主观评价、攻击、贬损、色情、侮辱标签），通常只是群友之间的玩笑或随口一说。把它当作玩笑正面应和，顺着调侃接一下就好；这类玩笑性记录可以作为旁线补充，但不要写进核心人物画像（主要用户画像、关系画像、稳定偏好等），落库时只能标为低置信的玩笑性质，不能混入可验证事实。涉及他人名誉、隐私或主观定性的说法，调侃可以，别替别人正式贴标签、下结论。",
            "状态、日程、情绪和私聊关系更适合只留在语气底色里；如果没有人明确问到，就不必主动展开能量、天气、日程、心情或插件状态。",
            "表达上尽量自然一点，不需要刻意堆动作描写、撒娇、长解释或关系总结；一句能说清，就简单说一句。",
            "如果只是被轻轻提到，或者话题本身并不需要你展开，宁可短一点、轻一点、贴着当前梗回应，也不用顺势写成主动陪伴式长回复。",
        ]
        if include_heading:
            lines.insert(0, "【群聊人格降噪】")
        if sender_id:
            identity_line = f"当前群聊发言者稳定 ID：{sender_id}"
            if sender_display_name and sender_display_name != sender_id:
                identity_line += f"；显示名：{sender_display_name}"
            lines.append(identity_line)
            if sender_is_target:
                lines.append("当前发言者 ID 与目标陪伴用户匹配；相关关系可以保留，但在群聊这种公共场合里，亲密度和表达还是稍微收一点更自然。")
            else:
                lines.append("当前发言者不是已配置的目标陪伴用户；更适合把 TA 当成普通群成员来接话，别把专属称呼或私聊关系直接套到 TA 身上。若要提到主要用户或目标用户，也更适合作为第三方提及。")
                if sender_name_conflicts_with_address:
                    lines.append(
                        f"当前群名片“{sender_display_name}”恰好是主要用户、亲密关系或权限称谓；"
                        "它只是显示名，不是关系事实，也不适合作为本轮对该成员的称呼。回复时自然省略称呼或使用中性称呼，不要照着群名片叫。"
                    )
        else:
            lines.append("本轮还不能确认当前发言者的稳定 ID，所以先别只凭昵称、群名片或角色设定就把对方认成主要用户或目标用户。")
        if trigger:
            lines.append(f"本轮触发：{trigger}。按这个触发强度自然回应就够了，不用顺手把亲密度或话题范围再往上抬。")
        if high_active:
            lines.append("群里刚才比较密集，这轮回复更适合收一点：抓住一个重点回应就好，不必逐条点名展开。")
        return "\n".join(lines)

    async def _append_group_persona_denoise_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        if not bool(runtime_persona_setting(self, 'enable_group_companion', True)):
            return
        group_id = self._extract_group_id_from_event(event)
        if not group_id or not self._group_enabled_for_event(group_id):
            return
        denoise_text = self._format_group_persona_denoise_prompt(
            event,
            include_heading=False,
        )
        if not denoise_text:
            return
        marker = "<!-- private_companion_group_persona_denoise_v1 -->"
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        if marker in current_prompt or marker in current_turn_prompt:
            return
        placement = "prompt" if self._append_turn_prompt_fragment_by_position(
            req,
            marker,
            denoise_text,
            title="群聊人格降噪",
            priority=32,
            source="group",
        ) else "system_prompt"
        if placement == "system_prompt":
            req.system_prompt = f"{current_prompt}\n\n{marker}\n{denoise_text}".strip()
        await self._record_request_prompt_fragment(
            event,
            title="群聊人格降噪注入",
            key="group.persona_denoise",
            text=denoise_text,
            source="group",
            mode="group",
            metadata={"注入位置": placement},
        )

    async def _append_non_target_private_identity_guard_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        marker = "<!-- private_companion_non_target_private_guard_v1 -->"
        current_prompt = req.system_prompt or ""
        if marker in current_prompt:
            return
        try:
            user_id = str(event.get_sender_id())
        except Exception:
            user_id = ""
        user_id = _single_line(user_id, 40)
        resolver = getattr(self, "_private_user_id_for_event", None)
        canonical_user_id = (
            resolver(event, user_id)
            if callable(resolver) and user_id
            else self._canonical_private_user_id(user_id)
            if user_id
            else ""
        )
        if canonical_user_id:
            user_id = canonical_user_id
        if not user_id or self._is_bot_self_user_id(user_id):
            return
        raw_users = self.data.get("users", {})
        current_user = raw_users.get(user_id) if isinstance(raw_users, dict) else None
        if (
            isinstance(current_user, dict)
            and self._private_passive_profile_available(user_id, current_user)
        ):
            return
        display_name = ""
        try:
            display_name = _single_line(self._sender_display_name(event), 40)
        except Exception:
            display_name = ""
        lines = [
            f"当前私聊对象稳定 ID：{user_id}",
            "这个用户不是插件当前启用的目标陪伴用户/主用户。",
            "如果基础人格里包含“主要用户/主人”“恋人”“专属称呼”或只属于主要用户的关系设定,不要套用到当前私聊对象身上。",
            "可以保留人格的通用说话风格,但关系身份、亲密度、记忆和承诺必须按当前用户重新判断。",
            "除非当前用户明确提出角色扮演或临时设定,否则不要把对方当成主要用户、恋人或目标陪伴对象。",
        ]
        if display_name and display_name != user_id:
            lines.append(f"平台当前显示名：{display_name}。显示名只作称呼线索,不能覆盖稳定 ID。")
        profile = None
        try:
            profile = self._worldbook_profile_by_user_id(user_id)
        except Exception:
            profile = None
        profile_lines: list[str] = []
        if isinstance(profile, dict) and profile.get("enabled", True):
            name = _single_line(profile.get("name"), 40)
            gender = _single_line(profile.get("gender"), 40)
            identity = _single_line(profile.get("identity_note") or profile.get("note") or profile.get("content"), 220)
            boundary = _single_line(profile.get("boundary_note"), 140)
            aliases = []
            for item in profile.get("aliases") if isinstance(profile.get("aliases"), list) else []:
                alias = _single_line(item, 24)
                if alias and alias != user_id and alias not in aliases:
                    aliases.append(alias)
            profile_lines.append("以下资料来自当前私聊 QQ 号的精确匹配,只用于识别当前用户,不能外推到主用户。")
            if name and name != user_id:
                profile_lines.append(f"登记名：{name}")
            if gender:
                profile_lines.append(f"性别：{gender}")
            if aliases:
                profile_lines.append(f"可用称呼线索：{'、'.join(aliases[:6])}")
            if identity:
                profile_lines.append(f"身份备注：{identity}")
            if boundary:
                profile_lines.append(f"互动边界：{boundary}")
            profile_lines.append("即使此用户资料中有亲昵称呼,也必须服从上面的防串规则：不要把目标陪伴用户的专属关系套给 TA。")
        guard_sections = [prompt_section("私聊身份防串", chr(10).join(lines))]
        if profile_lines:
            guard_sections.append(prompt_section("当前用户关系网资料", chr(10).join(profile_lines)))
        guard_text = render_prompt_sections(guard_sections)
        self._materialize_conversation_system_block(
            req,
            key="identity.non_target_private",
            marker=marker,
            content=guard_text,
            title="私聊身份防串",
            priority=10,
            source="identity",
            placement=PLACEMENT_DYNAMIC_SYSTEM,
            structured=True,
        )
        await self._record_request_prompt_fragment(
            event,
            title="非目标私聊防串注入",
            key="identity.non_target",
            text=guard_text,
            source="identity",
            mode="private",
        )

    def _message_looks_like_atrelay_request(self, text: str) -> bool:
        text = str(text or "")
        return any(token in text for token in (
            "发到", "发给", "告诉", "转告", "转达", "带话", "捎话", "通知", "私聊",
            "帮我", "替我", "你去", "跟他说", "和他说", "跟她说", "和她说", "说一声",
            "@", "艾特", "群友", "群里", "群聊", "出现", "冒泡", "上线",
        ))

    def _format_atrelay_target_summary_for_prompt(
        self,
        text: str,
        *,
        include_heading: bool = True,
    ) -> str:
        if not (self.enabled and self.enable_atrelay_tools):
            return ""
        text = str(text or "")
        if not self._message_looks_like_atrelay_request(text):
            return ""
        lines = ["【本轮转述目标摘要】"] if include_heading else []
        has_signal = False
        group_expected = any(token in text for token in ("群里", "群聊", "发到", "发群", "群"))
        member_expected = any(token in text for token in ("找", "告诉", "转告", "转达", "跟", "和", "给", "@", "艾特", "私聊", "说一句", "说一声"))

        group_matches = self._atrelay_cached_group_matches(text)
        if group_matches:
            has_signal = True
            if len(group_matches) == 1:
                group = group_matches[0]
                lines.append(
                    "目标群候选：确定｜"
                    f"{_single_line(group.get('group_name'), 60) or group.get('group_id')}（群号:{_single_line(group.get('group_id'), 40)}）"
                    f"｜来源:{_single_line(group.get('source'), 30) or 'local'}"
                )
            else:
                parts = [
                    f"{_single_line(item.get('group_name'), 40) or item.get('group_id')}（{_single_line(item.get('group_id'), 40)}）"
                    for item in group_matches[:5]
                ]
                lines.append("目标群候选：多个｜" + "；".join(parts))
        elif group_expected:
            has_signal = True
            lines.append("目标群候选：未命中｜用户可能还需要补充群名或群号。")

        member_profiles = self._select_worldbook_member_profiles_for_private_text(text, limit=5)
        if member_profiles:
            has_signal = True
            if len(member_profiles) == 1:
                profile = member_profiles[0]
                uid = _single_line(profile.get("user_id"), 40)
                name = _single_line(profile.get("name"), 40) or uid
                identity = _single_line(profile.get("identity_note") or profile.get("note") or profile.get("content"), 100)
                parts = [f"{name}（QQ:{uid or '-'}）"]
                if identity:
                    parts.append(f"身份:{identity}")
                lines.append("目标成员候选：确定｜" + "｜".join(parts))
            else:
                parts = [
                    f"{_single_line(profile.get('name'), 32) or _single_line(profile.get('user_id'), 40)}"
                    f"（{_single_line(profile.get('user_id'), 40) or '-'}）"
                    for profile in member_profiles[:5]
                ]
                lines.append("目标成员候选：多个｜" + "；".join(parts))
        elif member_expected:
            has_signal = True
            lines.append("目标成员候选：未命中｜没有从关系网里确定收话人。")

        if not has_signal:
            return ""
        lines.append("这些只是本轮目标解析线索；真正发送仍以用户明确要求和工具执行结果为准。")
        return "\n".join(lines)

    def _parse_direct_atrelay_request(self, text: str) -> dict[str, Any]:
        cleaned = _single_line(text, 260)
        if not cleaned or not self._message_looks_like_atrelay_request(cleaned):
            return {}
        destination = ""
        if "私聊" in cleaned or "私信" in cleaned:
            destination = "private"
        elif any(token in cleaned for token in ("群里", "群聊", "发到群", "发群", "到群里", "去群里")):
            destination = "group"
        if not destination:
            return {}

        profiles = self._select_worldbook_member_profiles_for_private_text(cleaned, limit=3)
        if len(profiles) != 1:
            return {}
        profile = profiles[0]
        recipient_id = _single_line(profile.get("user_id"), 40)
        recipient_name = _single_line(profile.get("name"), 40) or recipient_id
        tokens = sorted(
            [token for token in self._worldbook_profile_tokens(profile) if token and token in cleaned],
            key=len,
            reverse=True,
        )
        target_token = tokens[0] if tokens else recipient_name
        if not target_token or target_token not in cleaned:
            return {}

        _, after = cleaned.split(target_token, 1)
        after = re.sub(r"^(?:说一句|说一声|说下|说|告诉|转告|带话|发|：|:|，|,|\s)+", "", after).strip()
        if not after:
            # “告诉 A B”这类没有“说一句”的短命令，目标后面的内容就是正文。
            after = cleaned[cleaned.find(target_token) + len(target_token):].strip()
        message = _single_line(after, 300).strip(" ：:，,。")
        if not message:
            return {}

        group_hint = ""
        if destination == "group":
            group_matches = self._atrelay_cached_group_matches(cleaned)
            if len(group_matches) == 1:
                group_hint = _single_line(group_matches[0].get("group_id") or group_matches[0].get("group_name"), 80)
        return {
            "destination": destination,
            "recipient_hint": recipient_id or recipient_name or target_token,
            "group_hint": group_hint,
            "message": message,
            "target_token": target_token,
        }

    def _pending_atrelay_requests(self) -> dict[str, Any]:
        pending = self.data.setdefault("pending_atrelay_requests", {})
        if not isinstance(pending, dict):
            pending = {}
            self.data["pending_atrelay_requests"] = pending
        now = _now_ts()
        expired = [
            key for key, item in pending.items()
            if not isinstance(item, dict) or now - _safe_float(item.get("ts"), 0) > 10 * 60
        ]
        for key in expired:
            pending.pop(key, None)
        return pending

    def _store_pending_atrelay_request(self, user_id: str, payload: dict[str, Any], reason: str = "") -> None:
        uid = _single_line(user_id, 40)
        if not uid or not isinstance(payload, dict):
            return
        pending = self._pending_atrelay_requests()
        pending[uid] = {
            "ts": _now_ts(),
            "payload": {
                "destination": _single_line(payload.get("destination"), 20),
                "recipient_hint": _single_line(payload.get("recipient_hint"), 80),
                "group_hint": _single_line(payload.get("group_hint"), 80),
                "message": _single_line(payload.get("message"), 300),
                "target_token": _single_line(payload.get("target_token"), 80),
            },
            "reason": _single_line(reason, 120),
        }
        self._save_data_sync(sections={"pending_atrelay_requests"})
        logger.info(
            "[PrivateCompanion] 转述请求等待补群: user=%s target=%s text=%s reason=%s",
            uid,
            _single_line(payload.get("recipient_hint"), 80),
            _single_line(payload.get("message"), 80),
            _single_line(reason, 120),
        )

    async def _format_direct_atrelay_final_reply(
        self,
        event: AstrMessageEvent,
        payload: dict[str, Any],
        result: dict[str, Any],
    ) -> str:
        status = _single_line(result.get("status"), 40)
        fallback = _single_line(result.get("final_reply") or result.get("message"), 240)
        sender_id = ""
        try:
            sender_id = self._canonical_private_user_id(str(event.get_sender_id()))
        except Exception:
            try:
                sender_id = str(event.get_sender_id())
            except Exception:
                sender_id = ""
        users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else {}
        resolver = getattr(self, "_private_user_id_for_event", None)
        scoped_sender_id = (
            resolver(event, sender_id)
            if callable(resolver) and sender_id
            else self._canonical_private_user_id(sender_id)
        )
        user = users.get(scoped_sender_id) if scoped_sender_id and isinstance(users, dict) and isinstance(users.get(scoped_sender_id), dict) else {}
        rewriter = getattr(self, "_rewrite_reference_reply_with_persona", None)
        if status not in {"success", "scheduled"}:
            if callable(rewriter):
                rewritten = await rewriter(
                    f"参考意图：转述没有成功；原因是「{fallback or '未知'}」。用当前人格简短告诉用户失败，不要说成已经发出。",
                    scene="跨群/私聊转述失败回执",
                    user=user,
                    event=event,
                    fallback_text=fallback or "转述没成功。",
                    task="atrelay_receipt_rewrite",
                    max_chars=80,
                    allow_fallback=True,
                    preserve_status=True,
                )
                if rewritten:
                    return rewritten
            return fallback or "转述没成功。"
        recipient = _single_line(payload.get("target_token") or payload.get("recipient_hint"), 60) or "对方"
        if status == "scheduled":
            reference = f"参考意图：转述已挂起，等{recipient}下次在群里出现或冒泡时再转达；简短告诉用户会稍后带到。"
            fallback_ok = f"等{recipient}出现我再说。"
        else:
            reference = (
                f"参考意图：转述已经成功发给{recipient}；只给用户一个很短的成功回执，"
                "不要复述转述正文，也不要写工具执行状态。"
            )
            fallback_ok = f"给{recipient}带到了。" if recipient and recipient not in {"对方", "群里"} else "带到了。"
        if callable(rewriter):
            rewritten = await rewriter(
                reference,
                scene="跨群/私聊转述成功回执",
                user=user,
                event=event,
                fallback_text=fallback_ok,
                task="atrelay_receipt_rewrite",
                max_chars=70,
                allow_fallback=True,
                preserve_status=True,
            )
            if rewritten:
                return rewritten
        return fallback_ok

    async def _send_direct_atrelay_result_reply(
        self,
        event: AstrMessageEvent,
        payload: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        reply = await self._format_direct_atrelay_final_reply(event, payload, result)
        await event.send(event.plain_result(reply))

    async def _maybe_resume_pending_atrelay_request(self, event: AstrMessageEvent, user_id: str, text: str) -> bool:
        uid = _single_line(user_id, 40)
        pending = self._pending_atrelay_requests()
        item = pending.get(uid)
        if not isinstance(item, dict):
            return False
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if not payload or _single_line(payload.get("destination"), 20) != "group":
            pending.pop(uid, None)
            return False
        hint = _single_line(text, 100)
        if not hint:
            return False
        group_result = await self._resolve_atrelay_target_group(event, hint)
        if group_result.get("status") != "success":
            return False
        payload = dict(payload)
        payload["group_hint"] = _single_line(group_result.get("group_id") or hint, 80)
        result_raw = await self._pc_relay_message_impl(event, **payload)
        try:
            result = json.loads(result_raw)
        except Exception:
            result = {"status": "error", "message": _single_line(result_raw, 240)}
        pending.pop(uid, None)
        self._save_data_sync(sections={"pending_atrelay_requests"})
        logger.info(
            "[PrivateCompanion] 已用补充群名续发转述: user=%s group=%s status=%s target=%s",
            uid,
            _single_line(group_result.get("group_id"), 40),
            _single_line(result.get("status"), 40),
            _single_line(payload.get("recipient_hint"), 80),
        )
        await self._send_direct_atrelay_result_reply(event, payload, result)
        event.stop_event()
        return True

    async def _maybe_handle_direct_atrelay_request(self, event: AstrMessageEvent, text: str) -> bool:
        payload = self._parse_direct_atrelay_request(text)
        if not payload:
            return False
        result_raw = await self._pc_relay_message_impl(event, **payload)
        try:
            result = json.loads(result_raw)
        except Exception:
            result = {"status": "error", "message": _single_line(result_raw, 240)}
        status = _single_line(result.get("status"), 40)
        if status in {"need_group", "not_found"} and _single_line(payload.get("destination"), 20) == "group":
            resolver = getattr(self, "_private_user_id_for_event", None)
            pending_user_id = (
                resolver(event)
                if callable(resolver)
                else str(event.get_sender_id())
            )
            self._store_pending_atrelay_request(pending_user_id, payload, _single_line(result.get("message"), 120))
        logger.info(
            "[PrivateCompanion] 明确转述请求已本地直通: status=%s destination=%s target=%s text=%s",
            status or "-",
            _single_line(payload.get("destination"), 20),
            _single_line(payload.get("recipient_hint"), 40),
            _single_line(payload.get("message"), 80),
        )
        await self._send_direct_atrelay_result_reply(event, payload, result)
        event.stop_event()
        return True

    def _text_looks_like_relation_lookup_question(self, text: str) -> bool:
        cleaned = _single_line(text, 180)
        if not cleaned:
            return False
        compact = re.sub(r"\s+", "", cleaned)
        has_query_word = any(
            token in compact
            for token in (
                "认识吗",
                "认得吗",
                "知道吗",
                "是谁",
                "哪位",
                "什么人",
                "这个人",
                "这人",
                "那个人",
                "那人",
                "qq号",
                "QQ号",
                "QQ",
                "qq",
            )
        )
        if re.search(r"\d{5,12}", compact):
            return has_query_word
        if has_query_word:
            try:
                return bool(self._select_worldbook_member_profiles_for_private_text(compact, limit=1))
            except Exception:
                return True
        return False

    async def _private_reply_only_relation_lookup_text(self, event: AstrMessageEvent) -> str:
        try:
            message_id, raw_message = await self._reply_raw_message_for_event(event)
        except Exception as exc:
            logger.info("[PrivateCompanion] 私聊引用关系网问题预读取失败: %s", _single_line(exc, 120))
            return ""
        if raw_message is None:
            return ""
        try:
            info = self._extract_reply_rich_card_info(raw_message)
        except Exception as exc:
            logger.info("[PrivateCompanion] 私聊引用关系网问题解析失败: message_id=%s error=%s", message_id or "-", _single_line(exc, 120))
            return ""
        texts = [_single_line(item, 120) for item in info.get("texts", []) if _single_line(item, 120)]
        if not texts:
            return ""
        quoted_text = _single_line("；".join(texts[:3]), 180)
        if not self._text_looks_like_relation_lookup_question(quoted_text):
            return ""
        logger.info(
            "[PrivateCompanion] 私聊纯引用关系网问题已补触发文本: message_id=%s text=%s",
            message_id or "-",
            _single_line(quoted_text, 120),
        )
        return quoted_text

    async def _append_atrelay_target_summary_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> bool:
        text = str(
            getattr(event, "private_companion_group_text", "")
            or getattr(event, "message_str", "")
            or ""
        )
        summary = self._format_atrelay_target_summary_for_prompt(
            text,
            include_heading=False,
        )
        if not summary:
            return False
        marker = "<!-- private_companion_atrelay_target_summary_v1 -->"
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        if marker in current_prompt or marker in current_turn_prompt:
            return True
        placement = "prompt" if self._append_turn_prompt_fragment_by_position(
            req,
            marker,
            summary,
            title="本轮转述目标摘要",
            priority=86,
            source="tools",
        ) else "system_prompt"
        if placement == "system_prompt":
            req.system_prompt = f"{current_prompt}\n\n{marker}\n{summary}".strip()
        await self._record_request_prompt_fragment(
            event,
            title="本轮转述目标摘要",
            key="tools.atrelay.targets",
            text=summary,
            source="tools",
            mode="conditional",
            metadata={"注入位置": placement},
        )
        return True

    async def _append_worldbook_mentions_to_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *,
        mode: str = "conditional",
    ) -> None:
        if not bool(runtime_persona_setting(self, 'enable_worldbook_member_recognition', False)):
            return
        text = str(
            getattr(event, "private_companion_group_text", "")
            or getattr(event, "message_str", "")
            or ""
        )
        if self._format_atrelay_target_summary_for_prompt(text):
            return
        mention_text = self._format_worldbook_private_mentions_for_prompt(
            text,
            limit=4,
            include_heading=False,
        )
        if not mention_text:
            return
        marker = "<!-- private_companion_worldbook_mentions_v1 -->"
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        if marker in current_prompt or marker in current_turn_prompt:
            return
        placement = "prompt" if self._append_turn_prompt_fragment_by_position(
            req,
            marker,
            mention_text,
            title="本轮提到的关系网对象",
            priority=58,
            source="worldbook",
        ) else "system_prompt"
        if placement == "system_prompt":
            req.system_prompt = f"{current_prompt}\n\n{marker}\n{mention_text}".strip()
        await self._record_request_prompt_fragment(
            event,
            title="本轮关系网提及注入",
            key="worldbook.mentions",
            text=mention_text,
            source="worldbook",
            mode=mode,
            metadata={"注入位置": placement},
        )

    def _request_context_text_size(self, value: Any, *, depth: int = 0) -> int:
        if depth > 8 or value is None:
            return 0
        if isinstance(value, str):
            return len(value)
        if isinstance(value, (int, float, bool)):
            return len(str(value))
        if isinstance(value, dict):
            total = 0
            for key, item in value.items():
                if str(key) in {"tool_calls", "extra_content", "metadata"}:
                    continue
                total += self._request_context_text_size(item, depth=depth + 1)
            return total
        if isinstance(value, (list, tuple)):
            return sum(self._request_context_text_size(item, depth=depth + 1) for item in value)
        return len(str(value))

    def _plain_context_content_for_fast_reply(self, content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    if item.strip():
                        parts.append(item.strip())
                    continue
                if not isinstance(item, dict):
                    text = str(item or "").strip()
                    if text:
                        parts.append(text)
                    continue
                item_type = str(item.get("type") or "").lower()
                if item_type in {"text", "input_text"}:
                    text = str(item.get("text") or "").strip()
                    if text:
                        parts.append(text)
                elif "image" in item_type:
                    parts.append("[图片]")
                elif "audio" in item_type or "voice" in item_type:
                    parts.append("[语音]")
            return "\n".join(parts).strip()
        if isinstance(content, dict):
            for key in ("text", "content", "value"):
                if key in content:
                    return self._plain_context_content_for_fast_reply(content.get(key))
        return str(content or "").strip()

    def _trim_passive_request_context_if_needed(self, event: AstrMessageEvent, req: ProviderRequest, *, is_private_chat: bool) -> None:
        if not is_private_chat:
            return
        contexts = getattr(req, "contexts", None)
        if not isinstance(contexts, list) or len(contexts) <= 24:
            return
        approx_tokens = max(0, self._request_context_text_size(contexts) // 4)
        if approx_tokens < 50000 and len(contexts) < 120:
            return
        trimmed: list[Any] = []
        for item in contexts[-36:]:
            if not isinstance(item, dict):
                text = self._plain_context_content_for_fast_reply(item)
                if text:
                    trimmed.append({"role": "user", "content": _single_line(text, 1200)})
                continue
            role = str(item.get("role") or "").strip().lower()
            if role not in {"system", "user", "assistant"}:
                continue
            text = self._plain_context_content_for_fast_reply(item.get("content"))
            if not text:
                continue
            trimmed.append({"role": role, "content": _single_line(text, 1200)})
        trimmed = trimmed[-24:]
        if not trimmed:
            return
        try:
            req.contexts = trimmed
        except Exception:
            return
        logger.info(
            "[PrivateCompanion] 私聊超长上下文已启用轻量护栏: session=%s contexts=%s->%s approx_tokens=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            len(contexts),
            len(trimmed),
            approx_tokens,
        )

    def _context_text_is_new_conversation_boundary(self, text: Any) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        compact = re.sub(r"\s+", "", raw).lower()
        if compact in {"/new", "／new"}:
            return True
        if "switchedtonewconversation" in compact:
            return True
        if re.search(r"(已|成功)?(切换|开启|创建|新建).{0,8}(新)?会话", raw, flags=re.IGNORECASE):
            return True
        return False

    def _sanitize_request_context_new_conversation_boundary(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        contexts = getattr(req, "contexts", None)
        if not isinstance(contexts, list) or not contexts:
            return
        boundary_index = -1
        for index, item in enumerate(contexts):
            text = self._plain_context_content_for_fast_reply(item.get("content") if isinstance(item, dict) else item)
            if self._context_text_is_new_conversation_boundary(text):
                boundary_index = index
        if boundary_index < 0:
            return
        trimmed: list[Any] = []
        for item in contexts[boundary_index + 1:]:
            text = self._plain_context_content_for_fast_reply(item.get("content") if isinstance(item, dict) else item)
            if self._context_text_is_new_conversation_boundary(text):
                continue
            trimmed.append(item)
        if len(trimmed) == len(contexts):
            return
        try:
            req.contexts = trimmed
        except Exception:
            return
        logger.info(
            "[PrivateCompanion] 已按新会话边界裁剪 AstrBot 上下文: session=%s contexts=%s->%s boundary_index=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            len(contexts),
            len(trimmed),
            boundary_index,
        )

    def _rest_reply_window_active(self) -> bool:
        raw = str(runtime_persona_setting(self, 'rest_reply_active_windows', "") or "").strip()
        if not raw:
            return True
        raw = re.sub(r"\s+", "", raw)
        now_minutes = self._environment_now_minutes()
        for part in re.split(r"[,;；，、]+", raw):
            window = part.strip()
            if not window:
                continue
            start, end = self._parse_window_minutes(window)
            if start is None or end is None:
                continue
            for candidate in (now_minutes, now_minutes + 24 * 60):
                if start <= candidate < end:
                    return True
        return False

    def _rest_reply_sleep_context(self) -> tuple[bool, dict[str, Any], dict[str, Any] | None, str]:
        try:
            current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
            runtime = self._refresh_sleep_runtime_state(current_item)
        except Exception:
            current_item = None
            runtime = self._sleep_runtime_state()
        phase = str((runtime or {}).get("phase") or "")
        window_active = self._rest_reply_window_active()
        sleep_delay_active = False
        try:
            sleep_delay_active = bool(self._sleep_delay_override_state(runtime if isinstance(runtime, dict) else None))
        except Exception:
            sleep_delay_active = False
        sleepy_item = window_active and not sleep_delay_active and self._is_sleepy_plan_item(current_item) if isinstance(current_item, dict) else False
        sleeping = window_active and (phase in {"falling_asleep", "light_sleep", "sleeping_again"} or sleepy_item)
        if phase == "woken":
            sleeping = False
        if phase == "staying_up" or sleep_delay_active:
            sleeping = False
        if phase in {"natural_wake", "awake"} and not sleepy_item:
            sleeping = False
        schedule_text = self._format_plan_item_for_prompt(current_item) if isinstance(current_item, dict) else ""
        return sleeping, runtime if isinstance(runtime, dict) else {}, current_item, _single_line(schedule_text, 220)

    @staticmethod
    def _rest_reply_boundary_score(text: str) -> tuple[int, str]:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return 0, "empty"
        no_reply_boundary = r"(?:了|啦|吧|我|这(?:个|条|句|段)(?:消息|话|话题|内容|问题)?|这(?:条)?消息|本条消息|消息|哈|噢|哦|$|[，。！？,.!?])"
        if re.search(
            r"(?:不用|不必|无需|别|不要|先别|暂时别|今晚别|今天别)(?:再)?(?:回(?:复)?|理我|搭理我|接话|说话|出声)"
            + no_reply_boundary,
            compact,
        ):
            return -100, "user_asks_no_reply"
        proactive_only_quiet = bool(
            re.search(r"(?:别|不要|先别|暂时别|今晚别|今天别).{0,10}主动.{0,8}(?:打扰|吵|发消息|找我|回(?:复)?|理我|搭理我|接话|说话)", compact)
        )
        if re.search(r"你.{0,6}(没睡|没在睡|不是在睡|不是睡|不在睡|还没睡|醒着|清醒|没休息|没在休息|不是在休息|不是休息|不在休息)|别装睡|别装休息|装睡|装睡觉|明明醒着|明明没睡|明明没休息|又没睡|又没休息", compact):
            return 100, "user_corrects_not_resting"
        if re.search(r"快醒|醒醒|醒一醒|醒来|醒过来|别睡了?|别睡啦|别睡嘛|先别睡|别睡别睡|起床|起来|快起|回我一下|快回|马上回", compact):
            return 100, "explicit_wakeup_request"
        quiet_pattern = (
            r"(?:不用|不必|无需|别|不要|先别|暂时别|今晚别|今天别)(?:再)?(?:回(?:复)?|理我|搭理我|接话|说话|出声)"
            + no_reply_boundary
            + r"|(?:别|不要|先别|暂时别|今晚别|今天别).{0,10}(?:打扰|吵我|叫我|主动|发消息|找我)"
            r"|(?:安静点|闭嘴|别说话|不要说话|别醒|继续睡)"
        )
        if not proactive_only_quiet and re.search(quiet_pattern, compact):
            return -100, "user_asks_quiet"
        if re.search(r"(?:晚安|好梦|早点睡|早点休息|睡个好觉)", compact):
            return 100, "goodnight_ack"
        if proactive_only_quiet:
            return 0, "user_asks_no_proactive"
        if re.search(r"救命|出事|急|紧急|重要|不舒服|难受|害怕|崩溃|报警|医院|摔|痛", compact):
            return 100, "urgent_or_explicit_wakeup"
        if re.search(r"醒了吗|睡了吗|在吗|能不能回|可以回吗|想你|陪我|听我说|还睡吗|还在睡吗", compact):
            return 72, "soft_wakeup_request"
        return 0, "normal"

    async def _rest_reply_llm_score(
        self,
        *,
        text: str,
        schedule_text: str,
        runtime: dict[str, Any],
        is_private_chat: bool,
    ) -> tuple[int, str]:
        prompt = f"""
你是一个睡眠/休息中是否需要醒来回复的判定器。请只输出 JSON。

背景：
- Bot 当前日程处于睡眠、午休或休息段。
- 当前睡眠阶段：{_single_line(runtime.get("label") or runtime.get("phase"), 40) or "未知"}。
- 当前日程：{schedule_text or "未知"}。
- 会话类型：{"私聊" if is_private_chat else "群聊"}。

判断原则：
- 只有用户明显需要回应、明确叫醒、情绪/安全/紧急需要支持，或继续不回复会显得很不合适时，才建议醒来。
- 普通闲聊、表情、无明确对象的群聊、轻微玩笑、可等到醒来再说的内容，应保持睡眠不回复。
- 如果用户明确说不要打扰、别回、继续睡，必须不回复。

用户消息：
{_single_line(text, 800)}

只输出 JSON：
{{"score": 0-100, "should_reply": true/false, "reason": "一句话原因"}}
""".strip()
        raw = await self._llm_call(
            prompt,
            max_tokens=180,
            provider_id=self._task_provider(
                runtime_persona_setting(self, "rest_wakeup_provider_id", ""),
                runtime_persona_setting(self, "response_review_provider_id", ""),
                runtime_persona_setting(self, "llm_provider_id", ""),
            ),
            task="rest_wakeup_judge",
        )
        payload = self._extract_json_payload(raw or "")
        if not isinstance(payload, dict):
            return 0, "llm_invalid"
        try:
            score = max(0, min(100, int(float(payload.get("score", 0)))))
        except (TypeError, ValueError):
            score = 0
        should_reply = bool(payload.get("should_reply"))
        reason = _single_line(payload.get("reason"), 80) or "llm"
        if should_reply and score < runtime_persona_setting(self, 'rest_reply_llm_threshold', 65):
            score = runtime_persona_setting(self, 'rest_reply_llm_threshold', 65)
        return score, reason

    async def _should_reply_during_rest(self, event: AstrMessageEvent, *, is_private_chat: bool) -> tuple[bool, str]:
        if not runtime_persona_setting(self, 'enable_rest_reply_simulation', False):
            return True, "disabled"
        sleeping, runtime, _current_item, schedule_text = self._rest_reply_sleep_context()
        if not sleeping:
            return True, "not_sleeping"
        text = _single_line(getattr(event, "message_str", ""), 800)
        boundary_score, boundary_reason = self._rest_reply_boundary_score(text)
        if boundary_score < 0:
            return False, boundary_reason
        if boundary_score >= max(1, runtime_persona_setting(self, 'rest_reply_llm_threshold', 65)):
            try:
                self._mark_sleep_woken_by_user(text)
            except Exception:
                pass
            return True, boundary_reason
        mode = runtime_persona_setting(self, 'rest_reply_mode', "probability")
        if mode == "llm":
            score, reason = await self._rest_reply_llm_score(
                text=text,
                schedule_text=schedule_text,
                runtime=runtime,
                is_private_chat=is_private_chat,
            )
            allowed = score >= runtime_persona_setting(self, 'rest_reply_llm_threshold', 65)
            if allowed:
                try:
                    self._mark_sleep_woken_by_user(text)
                except Exception:
                    pass
            return allowed, f"llm:{score}/{runtime_persona_setting(self, 'rest_reply_llm_threshold', 65)}:{reason}"
        probability = max(0.0, min(1.0, float(runtime_persona_setting(self, 'rest_reply_probability', 0.0) or 0.0)))
        hit = random.random() <= probability
        if hit:
            try:
                self._mark_sleep_woken_by_user(text)
            except Exception:
                pass
        return hit, f"probability:{probability:.2f}"

    def _rest_backlog_user_for_event(self, event: AstrMessageEvent) -> tuple[str, dict[str, Any] | None]:
        try:
            if not bool(getattr(event, "is_private_chat", lambda: False)()):
                return "", None
        except Exception:
            return "", None
        try:
            resolver = getattr(self, "_private_user_id_for_event", None)
            user_id = (
                resolver(event)
                if callable(resolver)
                else self._canonical_private_user_id(str(event.get_sender_id()))
            )
        except Exception:
            return "", None
        users = self.data.get("users", {})
        user = users.get(user_id) if isinstance(users, dict) else None
        if not isinstance(user, dict):
            return user_id, None
        if not self._private_passive_profile_available(user_id, user):
            return user_id, None
        return user_id, user

    def _record_rest_reply_backlog(self, event: AstrMessageEvent, reason: str) -> None:
        if not bool(runtime_persona_setting(self, 'enable_rest_backlog_reply', True)):
            return
        user_id, user = self._rest_backlog_user_for_event(event)
        if not isinstance(user, dict):
            return
        text = _single_line(getattr(event, "message_str", ""), 240)
        if not text:
            text = "发来了一条非文本消息"
        backlog = user.get("rest_reply_backlog")
        if not isinstance(backlog, list):
            backlog = []
        now = time.time()
        backlog.append(
            {
                "ts": now,
                "text": text,
                "reason": _single_line(reason, 80),
            }
        )
        max_items = max(1, _safe_int(runtime_persona_setting(self, 'rest_backlog_max_messages', 4), 4, 1))
        user["rest_reply_backlog"] = backlog[-max_items:]
        user["rest_reply_backlog_updated_at"] = now
        self._schedule_data_save(sections={"users"})
        logger.info(
            "[PrivateCompanion] 已记录休息中未回复私聊: user=%s count=%s reason=%s text=%s",
            user_id,
            len(user["rest_reply_backlog"]),
            _single_line(reason, 80),
            _single_line(text, 80),
        )

    def _take_rest_reply_backlog_prompt(self, user: dict[str, Any]) -> str:
        if not bool(runtime_persona_setting(self, 'enable_rest_backlog_reply', True)):
            return ""
        backlog = user.get("rest_reply_backlog")
        if not isinstance(backlog, list) or not backlog:
            return ""
        max_items = max(1, _safe_int(runtime_persona_setting(self, 'rest_backlog_max_messages', 4), 4, 1))
        items = [item for item in backlog[-max_items:] if isinstance(item, dict)]
        if not items:
            user["rest_reply_backlog"] = []
            user["rest_reply_backlog_updated_at"] = 0
            self._schedule_data_save(sections={"users"})
            return ""
        lines: list[str] = []
        for idx, item in enumerate(items, 1):
            ts = _safe_float(item.get("ts"), 0)
            if ts > 0:
                try:
                    when = self._environment_fromtimestamp(ts).strftime("%H:%M")
                except Exception:
                    when = datetime.fromtimestamp(ts).strftime("%H:%M")
            else:
                when = "刚才"
            text = _single_line(item.get("text"), 180) or "发来了一条消息"
            lines.append(f"{idx}. {when}｜{text}")
        user["rest_reply_backlog"] = []
        user["rest_reply_backlog_updated_at"] = 0
        self._schedule_data_save(sections={"users"})
        if not lines:
            return ""
        return "休息时有几条私聊没来得及回，醒来后补看到：\n" + "\n".join(lines)

    async def _append_rest_reply_backlog_to_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        user: dict[str, Any],
    ) -> str:
        backlog_prompt = self._take_rest_reply_backlog_prompt(user)
        if not backlog_prompt:
            return ""
        marker = "<!-- private_companion_rest_backlog_v1 -->"
        placement = "prompt" if self._append_turn_prompt_fragment_by_position(
            req,
            marker,
            backlog_prompt,
            title="醒后补看私聊",
            priority=25,
            source="daily_state",
        ) else "system_prompt"
        if placement == "system_prompt":
            req.system_prompt = f"{req.system_prompt or ''}\n\n{marker}\n{backlog_prompt}".strip()
        await self._record_request_prompt_fragment(
            event,
            title="醒后补看私聊",
            key="rest.backlog",
            text=backlog_prompt,
            source="daily_state",
            mode="private",
            metadata={"注入位置": placement},
        )
        return backlog_prompt

    def _stop_reply_for_rest_gate(self, event: AstrMessageEvent, reason: str) -> None:
        self._record_rest_reply_backlog(event, reason)
        logger.info(
            "[PrivateCompanion] 睡眠/休息回复闸门拦截本轮被动回复: session=%s reason=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            _single_line(reason, 120),
        )
        self._record_passive_no_reply(
            event,
            source="休息闸门",
            reason=reason or "睡眠/休息回复闸门拦截",
            level="info",
        )
        empty_result = self._build_result_from_chain([])
        try:
            empty_result.stop_event()
        except Exception:
            pass
        event.set_result(empty_result)
        event.stop_event()

    def _stop_private_reply_after_user_rest_signal(self, event: AstrMessageEvent, user_id: str, text: str) -> None:
        logger.info(
            "[PrivateCompanion] 用户明确勿扰/不用回复,已前置拦截本轮私聊回复: user=%s text=%s",
            _single_line(user_id, 80),
            _single_line(text, 120),
        )
        self._record_passive_no_reply(
            event,
            source="休息静默",
            reason="用户明确要求勿扰或不用回复",
            detail=text,
            level="info",
        )
        empty_result = self._build_result_from_chain([])
        try:
            empty_result.stop_event()
        except Exception:
            pass
        event.set_result(empty_result)
        event.stop_event()

    def _is_private_companion_command_event(self, event: AstrMessageEvent) -> bool:
        text = _single_line(getattr(event, "message_str", ""), 160)
        if not text:
            return False
        stripped = text.lstrip()
        if stripped.startswith(("/", "／", "!", "！", "#", "＃", ".", "。")):
            return True
        prefixes = (
            "陪伴群", "/陪伴群", "群陪伴", "群聊陪伴",
            "陪伴", "/陪伴", "私聊陪伴", "主动陪伴",
        )
        return any(text == prefix or re.match(rf"^{re.escape(prefix)}\s+", text) for prefix in prefixes)

    def _group_llm_reply_block_for_event(self, event: AstrMessageEvent) -> dict[str, Any]:
        if bool(getattr(event, "is_private_chat", lambda: False)()):
            return {}
        group_id = self._extract_group_id_from_event(event)
        if not group_id:
            return {}
        item = self._group_llm_reply_block_item(group_id)
        if not bool(item.get("enabled")):
            return {}
        return item

    def _stop_group_llm_reply_if_blocked(self, event: AstrMessageEvent, *, source: str) -> bool:
        if self._is_private_companion_command_event(event):
            return False
        if source == "decorating_result" and not bool(getattr(event, "_private_companion_group_llm_reply_request_blocked", False)):
            return False
        item = self._group_llm_reply_block_for_event(event)
        if not item:
            return False
        if bool(getattr(event, "_private_companion_group_llm_reply_blocked", False)):
            return True
        group_id = _single_line(item.get("group_id"), 80) or self._extract_group_id_from_event(event)
        logger.info(
            "[PrivateCompanion] 本群 LLM 回复已被单独关闭,拦截本轮回复: group=%s source=%s",
            group_id or "-",
            _single_line(source, 40),
        )
        self._record_passive_no_reply(
            event,
            source="群聊 LLM 熔断",
            reason="本群所有 LLM 回复已关闭",
            detail=f"group={group_id or '-'} source={_single_line(source, 40)}",
            level="warn",
        )
        empty_result = self._build_result_from_chain([])
        try:
            empty_result.stop_event()
        except Exception:
            pass
        try:
            setattr(event, "_private_companion_group_llm_reply_blocked", True)
            if source.startswith("llm_request"):
                setattr(event, "_private_companion_group_llm_reply_request_blocked", True)
        except Exception:
            pass
        event.set_result(empty_result)
        event.stop_event()
        return True

    def _passive_no_reply_event_text(self, event: AstrMessageEvent | None, *, limit: int = 180) -> str:
        if event is None:
            return ""
        candidates = [
            getattr(event, "private_companion_group_text", ""),
            getattr(event, "message_str", ""),
        ]
        message_obj = getattr(event, "message_obj", None)
        if message_obj is not None:
            candidates.append(getattr(message_obj, "message_str", ""))
        for value in candidates:
            text = _single_line(value, limit)
            if text:
                return text
        component_types: list[str] = []
        try:
            for item in self._event_components(event):
                name = _single_line(self._component_type_name(item), 32)
                if name and name not in component_types:
                    component_types.append(name)
        except Exception:
            component_types = []
        return ",".join(component_types[:6])

    def _record_passive_no_reply(
        self,
        event: AstrMessageEvent | None,
        *,
        source: str,
        reason: str,
        detail: str = "",
        level: str = "info",
        action: str = "",
        reply_preview: str = "",
    ) -> None:
        if bool(getattr(event, "_private_companion_passive_no_reply_recorded", False)):
            return
        if bool(getattr(event, "private_companion_proactive_framework", False)):
            return
        source_text = _single_line(source, 40) or "被动未回复"
        reason_text = _single_line(reason, 120) or "未说明原因"
        level_text = _single_line(level, 12)
        if level_text not in {"error", "warn", "info"}:
            level_text = "info"
        now = _now_ts()
        session = _single_line(getattr(event, "unified_msg_origin", ""), 160) if event is not None else ""
        try:
            sender_id = _single_line(event.get_sender_id(), 80) if event is not None else ""
        except Exception:
            sender_id = ""
        inbound = self._passive_no_reply_event_text(event)
        detail_text = _single_line(detail, 220)
        reply_text = _single_line(reply_preview, 180)
        key = hashlib.sha1(f"{source_text}|{reason_text}".encode("utf-8", errors="ignore")).hexdigest()[:16]
        root = self.data.setdefault("passive_no_reply_records", {})
        if not isinstance(root, dict):
            root = {}
            self.data["passive_no_reply_records"] = root
        items = root.setdefault("items", [])
        if not isinstance(items, list):
            items = []
            root["items"] = items
        target: dict[str, Any] | None = None
        for item in items:
            if isinstance(item, dict) and item.get("key") == key:
                target = item
                break
        if target is None:
            target = {
                "key": key,
                "source": source_text,
                "reason": reason_text,
                "level": level_text,
                "count": 0,
                "first_ts": now,
                "last_ts": 0,
                "samples": [],
            }
            items.append(target)
        target["source"] = source_text
        target["reason"] = reason_text
        target["level"] = level_text
        target["count"] = _safe_int(target.get("count"), 0, 0) + 1
        target["last_ts"] = now
        target["last_session"] = session
        target["last_sender_id"] = sender_id
        target["last_inbound"] = inbound
        target["last_detail"] = detail_text
        target["last_action"] = _single_line(action, 120)
        target["last_reply_preview"] = reply_text
        sample = {
            "ts": now,
            "time": self._format_timestamp_elapsed(now),
            "session": session,
            "sender_id": sender_id,
            "inbound": inbound,
            "detail": detail_text,
            "reply_preview": reply_text,
        }
        samples = target.setdefault("samples", [])
        if not isinstance(samples, list):
            samples = []
            target["samples"] = samples
        samples.insert(0, sample)
        del samples[5:]
        root["total"] = _safe_int(root.get("total"), 0, 0) + 1
        root["last_ts"] = now
        items.sort(key=lambda item: _safe_float(item.get("last_ts"), 0) if isinstance(item, dict) else 0, reverse=True)
        del items[80:]
        if event is not None:
            try:
                setattr(event, "_private_companion_passive_no_reply_recorded", True)
            except Exception:
                pass
        logger.info(
            "[PrivateCompanion] 已记录被动未回复: source=%s reason=%s count=%s session=%s inbound=%s",
            source_text,
            reason_text,
            target.get("count"),
            session or "-",
            _single_line(inbound, 120),
        )
        try:
            self._schedule_data_save(sections={"passive_no_reply_records"})
        except Exception:
            pass
        self._schedule_reply_interception_forward(
            "plugin_block",
            source=source_text,
            reason=reason_text,
            source_session=session,
            inbound=inbound,
            after=reply_text,
            detail=detail_text,
        )

    def _schedule_reply_interception_forward(
        self,
        category: str,
        *,
        source: str = "",
        reason: str = "",
        source_session: str = "",
        inbound: str = "",
        before: str = "",
        after: str = "",
        detail: str = "",
    ) -> None:
        if not bool(getattr(self, "enable_reply_interception_forward", False)):
            return
        enabled = {
            "plugin_block": bool(getattr(self, "reply_interception_forward_plugin_blocks", False)),
            "rewrite": bool(getattr(self, "reply_interception_forward_rewrites", False)),
            "proactive_block": bool(getattr(self, "reply_interception_forward_proactive_blocks", False)),
        }.get(str(category or ""), False)
        target = _single_line(getattr(self, "reply_interception_forward_target_umo", ""), 180)
        if not enabled or not target:
            return
        labels = {
            "plugin_block": "插件阻断消息",
            "rewrite": "回复已改写",
            "proactive_block": "主动消息被拦截",
        }
        fields = [
            f"【回复拦截转发】{labels.get(category, category)}",
            f"时间：{self._environment_now().strftime('%Y-%m-%d %H:%M:%S')}",
        ]
        for label, value, limit in (
            ("来源", source, 80),
            ("原会话", source_session, 180),
            ("原因", reason, 300),
            ("用户消息", inbound, 300),
            ("原消息", before, 500),
            ("处理后", after, 500),
            ("补充", detail, 300),
        ):
            clean = _single_line(value, limit)
            if clean:
                fields.append(f"{label}：{clean}")
        text = "\n".join(fields)
        now = _now_ts()
        signature = hashlib.sha1(f"{target}|{category}|{source_session}|{reason}|{before}|{after}".encode("utf-8", errors="ignore")).hexdigest()[:20]
        recent = getattr(self, "_reply_interception_forward_recent", None)
        if not isinstance(recent, dict):
            recent = {}
            self._reply_interception_forward_recent = recent
        recent = {key: ts for key, ts in recent.items() if now - _safe_float(ts, 0) <= 30}
        self._reply_interception_forward_recent = recent
        if now - _safe_float(recent.get(signature), 0) <= 5:
            return
        recent[signature] = now
        try:
            self._create_lifecycle_background_task(
                self._send_reply_interception_forward(target, text),
                label="reply_interception_forward",
            )
        except Exception as exc:
            logger.warning(
                "[PrivateCompanion] 回复拦截转发无法启动: %s",
                _single_line(exc, 160),
            )

    async def _send_reply_interception_forward(self, target_umo: str, text: str) -> None:
        try:
            safe_text = _redact_outbound_secrets(text, self)
            await self.context.send_message(target_umo, MessageChain([Plain(safe_text)]))
            logger.info("[PrivateCompanion] 已转发回复拦截情况: target=%s", _single_line(target_umo, 120))
        except Exception as exc:
            logger.warning(
                "[PrivateCompanion] 回复拦截转发失败: target=%s error=%s",
                _single_line(target_umo, 120),
                _single_line(exc, 180),
            )

    def _redact_outbound_chain_secrets(self, chain: list[Any]) -> tuple[list[Any], bool]:
        changed = False
        for comp in list(chain or []):
            if not isinstance(comp, Plain):
                continue
            original = str(getattr(comp, "text", "") or "")
            cleaned = _redact_outbound_secrets(original, self)
            if cleaned == original:
                continue
            changed = True
            try:
                comp.text = cleaned
            except Exception:
                pass
        return chain, changed

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def redact_outbound_secrets_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """Final passive-reply guard against API keys, tokens and passwords."""
        if self is None or not self.enabled or not bool(getattr(self, "enable_outbound_secret_redaction", True)):
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            return
        _, changed = self._redact_outbound_chain_secrets(chain)
        if changed:
            logger.error(
                "[PrivateCompanion] 发送前检测到敏感凭据并已脱敏: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )

    @filter.on_decorating_result(priority=-21000)
    @_multi_persona_event_context
    async def record_daily_review_outbound_case_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """Experimental final-stage sampling for the next daily case review."""
        if self is None or not self.enabled or not runtime_persona_setting(self, 'enable_daily_case_review_experiment', False):
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if chain:
            self._record_daily_review_outbound_case(event, chain)

    def _proactive_only_unlock_store(self) -> set[str]:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return set()
        raw = data.get("proactive_only_temp_unlocks", [])
        if isinstance(raw, dict):
            items = raw.keys()
        elif isinstance(raw, (list, tuple, set)):
            items = raw
        else:
            items = []
        return {str(item).strip() for item in items if str(item or "").strip()}

    def _set_proactive_only_unlock_store(self, keys: set[str]) -> None:
        self.data["proactive_only_temp_unlocks"] = sorted(keys)

    def _normalize_proactive_only_unlock_key(self, value: Any) -> str:
        text = _single_line(value, 80).strip()
        if not text:
            return ""
        return _PROACTIVE_ONLY_TEMP_UNLOCK_ALIASES.get(text, text)

    def _proactive_only_unlock_label(self, key: str) -> str:
        return _PROACTIVE_ONLY_TEMP_UNLOCK_LABELS.get(key, key)

    def _proactive_only_temp_unlock_allows(self, feature: str = "") -> bool:
        unlocks = self._proactive_only_unlock_store()
        if not unlocks:
            return False
        if "all" in unlocks:
            return True
        feature = str(feature or "").strip()
        if not feature:
            return False
        if feature in unlocks:
            return True
        group = _PROACTIVE_ONLY_TEMP_UNLOCK_GROUPS.get(feature, set())
        return bool(group and (group & unlocks))

    def _feature_enabled_or_temp_unlocked(self, feature: str, default: bool = False) -> bool:
        if bool(runtime_persona_setting(self, feature, default)):
            return True
        return bool(
            runtime_persona_setting(self, 'enable_proactive_only_mode', False)
            and self._proactive_only_temp_unlock_allows(feature)
        )

    def _proactive_only_limited_passive_event(self, event: AstrMessageEvent | None) -> bool:
        return bool(
            runtime_persona_setting(self, 'enable_proactive_only_mode', False)
            and not bool(getattr(event, "private_companion_proactive_framework", False))
        )

    def _proactive_only_llm_request_needs_full_path(self) -> bool:
        unlocks = self._proactive_only_unlock_store()
        if "all" in unlocks or "llm_request" in unlocks:
            return True
        full_path_keys = {
            "inject_passive_states",
            "enable_intent_emotion_analysis",
            "enable_llm_timer_scheduling",
            "enable_passive_topic_suppression",
            "enable_private_image_self_recognition",
            "enable_group_companion",
            "enable_skill_growth_passive_injection",
            "enable_worldbook_member_recognition",
            "enable_livingmemory_integration",
        }
        return bool(full_path_keys & unlocks)

    async def _append_proactive_only_unlocked_llm_request_fragments(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        if self._proactive_only_temp_unlock_allows("enable_tts_enhancement"):
            await self.apply_tts_enhancement_request(event, req)
        if self._proactive_only_temp_unlock_allows("enable_forward_message_adaptation"):
            await self._append_forward_message_context_to_request(event, req)
        if self._proactive_only_temp_unlock_allows("enable_environment_perception"):
            await self._append_environment_perception_to_request(event, req)

    def _clear_proactive_only_temp_unlocks_if_mode_off(self) -> None:
        if runtime_persona_setting(self, 'enable_proactive_only_mode', False):
            return
        if not self._proactive_only_unlock_store():
            return
        self.data["proactive_only_temp_unlocks"] = []
        self._schedule_data_save(sections={"proactive_only_temp_unlocks"})

    def _format_proactive_only_temp_unlocks(self) -> str:
        unlocks = self._proactive_only_unlock_store()
        if not unlocks:
            return "当前没有临时放行项。"
        labels = [self._proactive_only_unlock_label(key) for key in sorted(unlocks)]
        return "当前主动专用模式临时放行：\n" + "\n".join(f"- {label}" for label in labels)

    def _related_proactive_only_unlock_keys(self, key: str) -> list[str]:
        related = list(_PROACTIVE_ONLY_TEMP_UNLOCK_RELATED.get(key, []) or [])
        return [item for item in related if item and item != key]

    def _apply_proactive_only_temp_unlock(self, key: str, *, sync_related: bool = False, clear: bool = False) -> str:
        normalized = self._normalize_proactive_only_unlock_key(key)
        if not normalized:
            return "没有识别到要临时放行的功能。"
        keys = self._proactive_only_unlock_store()
        target_keys = {normalized}
        if sync_related:
            target_keys.update(self._related_proactive_only_unlock_keys(normalized))
        if clear:
            removed = keys & target_keys
            keys.difference_update(target_keys)
            self._set_proactive_only_unlock_store(keys)
            self._save_data_sync(sections={"proactive_only_temp_unlocks"})
            if not removed:
                return "对应临时放行项本来就没有开启。"
            return "已取消临时放行：\n" + "\n".join(f"- {self._proactive_only_unlock_label(item)}" for item in sorted(removed))
        keys.update(target_keys)
        self._set_proactive_only_unlock_store(keys)
        self._save_data_sync(sections={"proactive_only_temp_unlocks"})
        return "已临时放行：\n" + "\n".join(f"- {self._proactive_only_unlock_label(item)}" for item in sorted(target_keys))

    def _proactive_only_blocks_passive_event(self, event: AstrMessageEvent | None, feature: str = "") -> bool:
        proactive_framework = bool(getattr(event, "private_companion_proactive_framework", False))
        allow_proactive_photo = feature == "pc_generate_photo"
        effective_feature = "pc_tools" if allow_proactive_photo else feature
        if effective_feature == "pc_tools" and proactive_framework and not allow_proactive_photo:
            return True
        if not bool(runtime_persona_setting(self, 'enable_proactive_only_mode', False)):
            self._clear_proactive_only_temp_unlocks_if_mode_off()
            return False
        if proactive_framework:
            return False
        return not self._proactive_only_temp_unlock_allows(effective_feature)

    async def _record_proactive_only_private_feedback(
        self,
        event: AstrMessageEvent,
        *,
        user_id: str,
        sender_display_name: str,
        text: str,
        received_ts: float,
    ) -> None:
        """主动专用模式下只记录用户回应,不接管被动回复链路。"""
        async with self._data_lock:
            users = self.data.get("users", {})
            canonical_user_id = self._canonical_private_user_id(user_id)
            user = users.get(canonical_user_id) if isinstance(users, dict) else None
            if not isinstance(user, dict):
                return
            user_id = canonical_user_id
            if not self._private_passive_profile_available(user_id, user):
                return
            if self._is_recent_poke_echo(user, text):
                logger.info("[PrivateCompanion] 主动专用模式忽略 poke 回流事件: user=%s", user_id)
                return
            if self._is_duplicate_inbound_message(event, scope=f"private:{user_id}", sender_id=user_id, text=text):
                self._schedule_data_save(sections={"inbound_debounce_stats"})
                return
            self._note_private_user_umo(user_id, user, event.unified_msg_origin)
            self._note_private_display_name_observation(user, user_id, sender_display_name, now=received_ts)
            user["last_seen"] = received_ts
            user["last_activity_at"] = received_ts
            self._note_private_inbound_activity(user, received_ts, text=text)
            self._mark_greetings_satisfied_by_recent_activity(user, activity_ts=received_ts)
            self._note_morning_greeting_reply(user, now=received_ts)
            if self._cancel_inbound_conflicting_greeting(
                user,
                now=received_ts,
                user_id=user_id,
                trigger_umo=str(getattr(event, "unified_msg_origin", "") or ""),
            ):
                logger.info("[PrivateCompanion] 用户已在当前问候时段自然来聊,已请求取消冲突问候候选: %s", user_id)
                if not self._simulation_active(user) and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                    self._schedule_next_proactive(user, now=received_ts)
            if text:
                safe_text = self._sanitize_orphan_tts_placeholders(text)
                user["last_user_message"] = safe_text or text
                user["last_user_message_at"] = received_ts
                if self._clear_state_share_proactive_after_user_status_question(user, user_id=user_id, text=safe_text or text, now=received_ts):
                    if not self._simulation_active(user) and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                        self._schedule_next_proactive(user, now=received_ts)
                user["inbound_count"] = _safe_int(user.get("inbound_count"), 0) + 1
                user["episode_message_count"] = _safe_int(user.get("episode_message_count"), 0, 0) + 1
                self._apply_user_rest_silence_from_message(user, safe_text or text, now=received_ts)
            if _safe_float(user.get("awaiting_reply_since"), 0) > 0:
                audit_outcome_recorder = getattr(self, "_mark_proactive_audit_reply_outcome", None)
                if callable(audit_outcome_recorder):
                    audit_outcome_recorder(
                        user,
                        received_at=received_ts,
                        message_id=self._event_message_id(event),
                    )
                user["reply_count"] = _safe_int(user.get("reply_count"), 0) + 1
                self._note_action_reply_feedback(
                    user,
                    str(user.get("last_proactive_action") or "message"),
                    text,
                )
                self._apply_relationship_event(
                    user,
                    2,
                    reason_code="proactive_reply",
                    event_id=self._event_message_id(event),
                    now=received_ts,
                )
                user["awaiting_reply_since"] = 0
                user["last_reply_at"] = received_ts
                user["last_private_reply_at"] = received_ts
                user["pending_followup_event"] = {}
                user["planned_proactive_quota_exempt"] = False
            user["ignored_streak"] = 0
            user["friend_unanswered_silenced_since"] = 0
            user["friend_unanswered_silence_note"] = ""
            meal_care_result: dict[str, Any] = {}
            if self._private_user_role(user, user_id) == "owner" and text:
                meal_care_result = self._handle_meal_care_inbound(user, text, now=received_ts)
            save_sections = {"users"}
            if meal_care_result.get("foods"):
                save_sections.add("food_menu")
            self._schedule_data_save(sections=save_sections)
        logger.info(
            "[PrivateCompanion] 主动消息专用模式已跳过私聊被动增强: user=%s text=%s",
            user_id,
            _single_line(text, 80) or "非文本消息",
        )

    @filter.on_llm_request()
    @_multi_persona_event_context
    async def inject_tts_enhancement_request_fallback(self, event: AstrMessageEvent, req: ProviderRequest, *args, **kwargs):
        """TTS 请求规则独立兜底，避免被状态注入链路早退顺手跳过。"""
        if self is None or not self.enabled:
            return
        if self._stop_group_llm_reply_if_blocked(event, source="llm_request_tts_fallback"):
            return
        if self._proactive_only_blocks_passive_event(event, "enable_tts_enhancement"):
            return
        await self.apply_tts_enhancement_request(event, req)

    def _llm_request_provider_settings_for_event(self, event: AstrMessageEvent | None) -> dict[str, Any]:
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        resolver = getattr(self, "_astrbot_provider_settings_for_umo", None)
        if callable(resolver):
            try:
                return dict(resolver(umo) or {})
            except Exception:
                pass
        try:
            cfg = self.context.get_config(umo=umo) if umo else self.context.get_config()
        except TypeError:
            try:
                cfg = self.context.get_config(umo) if umo else self.context.get_config()
            except Exception:
                cfg = {}
        except Exception:
            cfg = {}
        settings = cfg.get("provider_settings", {}) if isinstance(cfg, dict) else {}
        return dict(settings or {}) if isinstance(settings, dict) else {}

    def _llm_request_provider_identity_parts(self, event: AstrMessageEvent | None, req: ProviderRequest | None) -> list[str]:
        parts: list[str] = []

        def add(value: Any) -> None:
            text = _single_line(value, 200)
            if text and text not in parts:
                parts.append(text)

        if req is not None:
            for key in ("provider_id", "llm_provider_id", "chat_provider_id", "model"):
                add(getattr(req, key, ""))
        settings = self._llm_request_provider_settings_for_event(event)
        for key in (
            "default_provider_id",
            "default_llm_provider_id",
            "provider_id",
            "model",
            "api_base",
            "base_url",
        ):
            add(settings.get(key))
        context = getattr(self, "context", None)
        get_using = getattr(context, "get_using_provider", None)
        if callable(get_using):
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            provider = None
            try:
                provider = get_using(umo=umo) if umo else get_using()
            except TypeError:
                try:
                    provider = get_using(umo) if umo else get_using(None)
                except Exception:
                    provider = None
            except Exception:
                provider = None
            if provider is not None:
                try:
                    meta = provider.meta()
                    if isinstance(meta, dict):
                        for key in ("id", "model", "type"):
                            add(meta.get(key))
                    else:
                        for key in ("id", "model", "type"):
                            add(getattr(meta, key, ""))
                except Exception:
                    pass
                config = getattr(provider, "provider_config", None) or getattr(provider, "config", None) or {}
                if isinstance(config, dict):
                    for key in ("id", "provider_id", "provider", "model", "api_base", "base_url"):
                        add(config.get(key))
        return parts

    def _llm_request_uses_gemini_family_provider(self, event: AstrMessageEvent | None, req: ProviderRequest | None) -> bool:
        identity = " ".join(self._llm_request_provider_identity_parts(event, req)).lower()
        return any(
            marker in identity
            for marker in (
                "gemini",
                "generativelanguage.googleapis.com",
                "googleapis.com/v1beta/openai",
            )
        )

    def _llm_request_uses_deepseek_family_provider(self, event: AstrMessageEvent | None, req: ProviderRequest | None) -> bool:
        identity = " ".join(self._llm_request_provider_identity_parts(event, req)).lower()
        return "deepseek" in identity

    def _llm_request_uses_deepseek_openai_compatible_provider(
        self,
        event: AstrMessageEvent | None,
        req: ProviderRequest | None,
    ) -> bool:
        """Limit history cleanup to DeepSeek's OpenAI-compatible endpoint."""
        identity = " ".join(self._llm_request_provider_identity_parts(event, req)).lower()
        return "deepseek" in identity

    def _append_deepseek_tool_protocol_guard(self, event: AstrMessageEvent, req: ProviderRequest) -> bool:
        if getattr(req, "func_tool", None) is None:
            return False
        if not self._llm_request_uses_deepseek_family_provider(event, req):
            return False
        marker = "<!-- private_companion_tool_protocol_v1 -->"
        current_prompt = str(getattr(req, "system_prompt", "") or "")
        if marker in current_prompt:
            return False
        instruction = (
            "当前模型兼容接口会严格核对每个 tool_call_id 与工具结果。"
            "需要使用多个工具时，请按顺序逐个调用：每条 assistant 消息只发起一个工具调用，"
            "拿到该工具结果后再决定是否调用下一个；不要并行或批量发起 tool_calls。"
            "回复当前会话的普通文字时，直接输出最终回复，不要调用 send_message_to_user；"
            "确需使用该工具发送媒体或主动消息时，plain 文本不得为空，调用同一轮不要额外输出可见正文。"
        )
        self._materialize_conversation_system_block(
            req,
            key="tools.deepseek_protocol",
            marker=marker,
            content=instruction,
            title="工具调用协议",
            priority=10,
            source="tools",
            placement=PLACEMENT_DYNAMIC_SYSTEM,
        )
        return True

    def _append_passive_reply_tool_boundary(self, event: AstrMessageEvent, req: ProviderRequest) -> list[str]:
        """Guide ordinary replies without removing AstrBot's media sender.

        Plain text stays on the final assistant-response path so it cannot be
        delivered twice. The official sender remains available for real files,
        images, records and videos that the current turn needs to deliver.
        """
        if req is None:
            return []
        if callable(getattr(self, "_event_requires_direct_same_session_tool_delivery", None)):
            if self._event_requires_direct_same_session_tool_delivery(event):
                return []
        if str(getattr(event, "_private_companion_external_proactive_source", "") or ""):
            return []
        umo = _single_line(getattr(event, "unified_msg_origin", ""), 240)
        if not umo or not any(marker in umo for marker in (":GroupMessage:", ":FriendMessage:")):
            return []

        try:
            setattr(event, "_private_companion_passive_reply_tool_boundary", True)
            setattr(event, "_private_companion_passive_reply_request", req)
        except Exception:
            pass
        marker = "<!-- private_companion_passive_reply_tool_boundary_v1 -->"
        prompt = str(getattr(req, "system_prompt", "") or "")
        instruction = (
            "这是普通私聊或群聊的被动回复。请直接输出一次最终正文；"
            "普通文字不要调用 `send_message_to_user`，同一正文也不要在工具调用后再次输出。"
            "只有本轮确实需要投递已经存在、且带有真实 path 或 url 的图片、音频、视频或文件时，"
            "才可使用该官方工具；messages 至少包含一个非 plain 媒体组件。"
            "媒体消息中的 plain 只写必要附言，工具调用后不要重复输出附言或发送结果。"
            "不要猜测文件路径，也不要把该工具当成生成、搜索或读取文件的能力。"
            "需要跨会话主动发送时，使用 PrivateCompanion 专用发送工具；官方 Cron 任务不受此边界影响。"
        )
        if marker not in prompt and hasattr(req, "system_prompt"):
            materializer = getattr(self, "_materialize_conversation_system_block", None)
            if callable(materializer):
                materializer(
                    req,
                    key="tools.passive_reply_boundary",
                    marker=marker,
                    content=instruction,
                    title="当前会话回复边界",
                    priority=10,
                    source="tools",
                    placement=PLACEMENT_DYNAMIC_SYSTEM,
                )
            else:
                plan = get_conversation_injection_plan(req)
                if plan is not None:
                    plan.materialize_system_block(
                        req,
                        key="tools.passive_reply_boundary",
                        marker=marker,
                        content=instruction,
                        title="当前会话回复边界",
                        priority=10,
                        source="tools",
                        placement=PLACEMENT_DYNAMIC_SYSTEM,
                    )
            if self._tool_set_has_named_tool(getattr(req, "func_tool", None), "send_message_to_user"):
                logger.info(
                    "[PrivateCompanion] 已约束被动回复的 send_message_to_user 仅用于媒体投递: session=%s",
                    umo,
                )
        return []

    def _finalize_passive_reply_tool_boundary(self, event: AstrMessageEvent) -> list[str]:
        if not bool(getattr(event, "_private_companion_passive_reply_tool_boundary", False)):
            return []
        req = getattr(event, "_private_companion_passive_reply_request", None)
        getter = getattr(event, "get_extra", None)
        if callable(getter):
            try:
                req = getter("provider_request") or req
            except Exception:
                pass
        return self._append_passive_reply_tool_boundary(event, req) if req is not None else []

    @staticmethod
    def _tool_set_has_named_tool(tool_set: Any, tool_name: str) -> bool:
        get_tool = getattr(tool_set, "get_tool", None)
        if callable(get_tool):
            try:
                return get_tool(tool_name) is not None
            except Exception:
                pass
        tools = getattr(tool_set, "tools", None)
        if isinstance(tools, list):
            return any(_single_line(getattr(tool, "name", ""), 120) == tool_name for tool in tools)
        return False

    @staticmethod
    def _tool_set_tool_names(tool_set: Any) -> list[str]:
        names: list[str] = []
        tools = getattr(tool_set, "tools", None)
        if isinstance(tools, list):
            for tool in tools:
                name = _single_line(getattr(tool, "name", ""), 120)
                if name and name not in names:
                    names.append(name)
        return names

    @staticmethod
    def _safe_event_sender_id(event: AstrMessageEvent | None) -> str:
        if event is None:
            return ""
        getter = getattr(event, "get_sender_id", None)
        if callable(getter):
            try:
                return _single_line(getter(), 80)
            except Exception:
                pass
        return _single_line(getattr(event, "sender_id", "") or getattr(event, "user_id", ""), 80)

    @staticmethod
    def _safe_event_is_private(event: AstrMessageEvent | None) -> bool:
        if event is None:
            return False
        unified_msg_origin = str(getattr(event, "unified_msg_origin", "") or "")
        try:
            if bool(getattr(event, "is_private_chat", lambda: False)()):
                return True
        except Exception:
            pass
        return ":FriendMessage:" in unified_msg_origin

    def _is_owner_private_event(self, event: AstrMessageEvent | None) -> bool:
        if event is None:
            return False
        if not self._safe_event_is_private(event):
            return False
        try:
            resolver = getattr(self, "_private_user_id_for_event", None)
            requester_id = (
                resolver(event)
                if callable(resolver)
                else self._canonical_private_user_id(self._safe_event_sender_id(event))
            )
        except Exception:
            requester_id = ""
        if not requester_id:
            return False
        requester_profile = None
        try:
            requester_profile = self._get_user(requester_id)
        except Exception:
            users = self.data.get("users") if isinstance(getattr(self, "data", {}), dict) and isinstance(self.data.get("users"), dict) else {}
            requester_profile = users.get(requester_id) if isinstance(users, dict) else None
        try:
            return (
                bool(requester_id and self._is_target_private_user(requester_id, requester_profile if isinstance(requester_profile, dict) else None))
                and isinstance(requester_profile, dict)
                and bool(requester_profile.get("enabled", True))
                and self._private_user_role(requester_profile, requester_id) == "owner"
            )
        except Exception:
            return False

    @filter.on_llm_request(priority=220000)
    @_multi_persona_event_context
    async def guard_req036_private_capability_before_llm(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Compatibility hook retained after removing passive private-chat gating."""
        return

    @filter.on_llm_request(priority=-30000)
    @_multi_persona_event_context
    async def enforce_p4_live_confinement_before_enrichment(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Block only an already-resolved private person with an invalid or active P4 state."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        state = self._p4_live_state_for_event(event)
        decision = decide_live_request(state)
        if decision.get("decision") == "skip":
            return
        if decision.get("decision") == "block":
            # This runs before P5/Memory and the enrichment collectors. Leave
            # no request route to original prompt, tool, bridge, or context data.
            for attribute, value in (
                ("system_prompt", SAFE_CONFINEMENT_REPLY),
                ("prompt", ""),
                ("contexts", []),
                ("extra_user_content_parts", []),
                ("func_tool", None),
                ("tools", []),
                ("images", []),
                ("image_urls", []),
            ):
                try:
                    setattr(req, attribute, value)
                except Exception:
                    pass
            try:
                setattr(event, "private_companion_p4_blocked", True)
                setattr(event, "private_companion_p4_block_code", decision.get("code", "p4_state_invalid"))
            except Exception:
                pass
            await self._reply(event, SAFE_CONFINEMENT_REPLY)
            event.stop_event()
            return
        temperature = compose_reply_temperature(
            decision.get("warmth_projection", {}).get("tier"),
            **self._bounded_p4_reply_temperature_signals(event),
        )
        try:
            setattr(req, "_private_companion_reply_temperature", temperature)
        except Exception:
            pass
        instruction = _single_line(temperature.get("instruction"), 240)
        if instruction and hasattr(req, "system_prompt"):
            self._materialize_conversation_system_block(
                req,
                key="relationship.reply_temperature",
                marker="[Reply boundary]",
                content=instruction,
                title="Reply boundary",
                priority=5,
                source="relationship",
                placement=PLACEMENT_DYNAMIC_SYSTEM,
            )

    @filter.on_llm_request(priority=-30000)
    @_multi_persona_event_context
    async def inject_unified_relationship_expression(self, event: AstrMessageEvent, req: ProviderRequest, *args, **kwargs):
        """Inject one fail-closed relationship expression decision before Memory enrichment."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        if not bool(runtime_persona_setting(self, 'enable_custom_relationship_stage_policy', False)):
            return
        is_private = self._safe_event_is_private(event)
        group_id = "" if is_private else self._extract_group_id_from_event(event)
        if not is_private and not group_id:
            return
        raw_sender_id = self._safe_event_sender_id(event)
        current_user = None
        if is_private:
            try:
                resolver = getattr(self, "_private_user_id_for_event", None)
                sender_id = (
                    resolver(event)
                    if callable(resolver)
                    else self._canonical_private_user_id(raw_sender_id)
                )
            except Exception:
                sender_id = ""
            users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
            current_user = users.get(sender_id) if sender_id and isinstance(users, dict) else None
        else:
            projection_getter = getattr(self, "_req039_group_observation_projection", None)
            if callable(projection_getter):
                current_user = projection_getter(
                    event,
                    sender_id=raw_sender_id,
                    sender_name=self._sender_display_name(event),
                )
        if not isinstance(current_user, dict):
            return
        fixture_user = self._lab_fixture_relationship_view(event, current_user)
        fixture_relationship_applied = fixture_user is not current_user
        current_user = fixture_user
        expression_builder = getattr(self, "_build_expression_decision_for_user", None)
        if not callable(expression_builder):
            return
        try:
            expression_args = {
                "passive_reengagement": True,
                "bot_state": {
                    "energy": current_user.get("bot_energy", 70),
                    "mood": current_user.get("bot_mood", ""),
                },
                "message_intent": content_intent_from_text(getattr(event, "message_str", "")),
                "content_policy": {
                    "enabled": bool(runtime_persona_setting(self, 'enable_relationship_content_tiers', False)),
                    "flirt_enabled": bool(runtime_persona_setting(self, 'enable_flirt_content_tier', True)),
                    "private_chat": is_private,
                },
                "channel_scope": "private" if is_private else "group",
            }
            if fixture_relationship_applied:
                expression_args["_authoritative_relationship_view"] = True
            expression = expression_builder(current_user, **expression_args)
            projection = expression.to_dict() if hasattr(expression, "to_dict") else dict(expression or {})
            if is_private:
                violation_hint_getter = getattr(self, "_relationship_violation_prompt_hint", None)
                if callable(violation_hint_getter):
                    hint = violation_hint_getter(current_user, now=_now_ts())
                    if hint:
                        projection["relationship_violation_hint"] = hint
        except Exception as exc:
            logger.debug("[PrivateCompanion] 统一表达决策生成失败，使用日常保守默认值: %s", _single_line(exc, 120))
            projection = build_expression_decision({}).to_dict()
        try:
            setattr(req, "_private_companion_expression_decision", projection)
            setattr(event, "_private_companion_expression_decision", projection)
        except Exception:
            pass
        instruction = expression_decision_prompt(projection)
        if instruction:
            self._append_turn_prompt_fragment_by_position(
                req,
                "<!-- private_companion_expression_decision_v2 -->",
                instruction,
                title="Companion expression",
                priority=5,
                source="expression_decision",
                force_dynamic=True,
            )

    def _remove_sensitive_screen_tools_from_request(self, event: AstrMessageEvent, req: ProviderRequest) -> list[str]:
        tool_set = getattr(req, "func_tool", None)
        if tool_set is None:
            return []
        allow_owner_private = self._is_owner_private_event(event)
        if allow_owner_private:
            return []
        sensitive_tools = {"screen_peek", "screen_usage_context"}
        names = self._tool_set_tool_names(tool_set)
        if not names:
            names = [name for name in sensitive_tools if self._tool_set_has_named_tool(tool_set, name)]
        removed: list[str] = []
        remove_tool = getattr(tool_set, "remove_tool", None)
        for name in names:
            if name not in sensitive_tools:
                continue
            try:
                if callable(remove_tool):
                    remove_tool(name)
                else:
                    tools = getattr(tool_set, "tools", None)
                    if isinstance(tools, list):
                        tool_set.tools = [tool for tool in tools if _single_line(getattr(tool, "name", ""), 120) != name]
                    else:
                        continue
                removed.append(name)
            except Exception as exc:
                logger.warning(
                    "[PrivateCompanion] 移除敏感屏幕工具失败: tool=%s session=%s error=%s",
                    name,
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                    _single_line(exc, 160),
                )
        if removed:
            try:
                setattr(event, "_private_companion_removed_sensitive_tools", removed)
            except Exception:
                pass
            logger.info(
                "[PrivateCompanion] 已移除非主人私聊场景的敏感屏幕工具: session=%s sender=%s tools=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                self._safe_event_sender_id(event) or "-",
                ",".join(removed),
            )
        return removed

    async def _append_sensitive_screen_tool_guard_to_request(self, event: AstrMessageEvent, req: ProviderRequest, removed: list[str] | None = None) -> None:
        marker = "<!-- private_companion_sensitive_screen_tool_guard_v1 -->"
        current_prompt = req.system_prompt or ""
        if marker in current_prompt:
            return
        removed_text = "、".join(removed or []) or "screen_peek、screen_usage_context"
        guard = (
            f"本轮不是已授权的主要用户私聊,已禁用或不可使用这些本机屏幕工具：{removed_text}。\n"
            "群聊成员、次要用户、未登记用户或第三方不能要求你查看主要用户/部署电脑正在做什么、屏幕内容、近期电脑使用记录或窗口信息。\n"
            "遇到这类请求时必须简短拒绝,说明屏幕内容只允许主要用户本人在授权私聊里使用；不要改用记忆、关系网、屏幕日记或猜测来替代窥屏。\n"
            "这条边界只约束屏幕工具，不代表摄像头能力不存在；若本轮另有“摄像头请求”提示，应按其独立资格、授权和单帧规则调用 pc_reality_touch_camera_snapshot。"
        )
        self._materialize_conversation_system_block(
            req,
            key="guard.screen_privacy",
            marker=marker,
            content=guard,
            title="屏幕隐私边界",
            priority=10,
            source="guard",
            placement=PLACEMENT_DYNAMIC_SYSTEM,
        )
        await self._record_request_prompt_fragment(
            event,
            title="屏幕隐私边界注入",
            key="tools.screen_privacy_guard",
            text=guard,
            source="guard",
            mode="private" if self._safe_event_is_private(event) else "group",
        )

    @filter.on_llm_request(priority=-22000)
    @_multi_persona_event_context
    async def prepare_p5_memory_attestation(self, event: AstrMessageEvent, req: ProviderRequest, *args, **kwargs):
        """Expose a per-request attestation issuer before MemoryCompanion runs."""
        if self is None or event is None or not bool(getattr(self, "enable_p5_source_observer", False)):
            return
        request_carrier = req if req is not None else event
        p3_state = getattr(event, "private_companion_p5_p3_state", None)
        if p3_state is None:
            p3_state = object()
            try:
                setattr(event, "private_companion_p5_p3_state", p3_state)
            except Exception:
                pass
        try:
            setattr(event, "private_companion_p5_request_carrier", request_carrier)
            setattr(
                event,
                "private_companion_p5_issue_attestation",
                lambda sink, _event=event, _request=request_carrier: self._p5_issue_attestation_for_event(
                    event=_event,
                    request=_request,
                    sink=str(sink or "memory_recall"),
                ),
            )
            setattr(event, "private_companion_p5_status", self.p5_source_observer_status())
        except Exception:
            logger.debug("[PrivateCompanion] P5 request carrier attach failed")

    @filter.on_llm_request(priority=-21000)
    @_multi_persona_event_context
    async def sanitize_sensitive_screen_tools(self, event: AstrMessageEvent, req: ProviderRequest, *args, **kwargs):
        """屏幕工具只能保留给已启用的主要用户私聊，群聊和第三方场景一律裁掉。"""
        if self is None or req is None:
            return
        removed = self._remove_sensitive_screen_tools_from_request(event, req)
        if removed:
            await self._append_sensitive_screen_tool_guard_to_request(event, req, removed)

    @filter.on_llm_request(priority=-20500)
    @_multi_persona_event_context
    async def sanitize_deepseek_tool_call_history(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Drop only malformed tool-call groups before a DeepSeek provider request."""
        if self is None or req is None:
            return
        if not self._llm_request_uses_deepseek_openai_compatible_provider(event, req):
            return
        contexts = getattr(req, "contexts", None)
        cleaned, stats = sanitize_openai_tool_history(contexts)
        if not stats.get("changed"):
            return
        try:
            req.contexts = cleaned
        except Exception:
            return
        logger.info(
            "[PrivateCompanion] Cleaned malformed DeepSeek tool history: groups=%s assistants=%s tool_results=%s orphans=%s",
            stats.get("removed_groups", 0),
            stats.get("removed_assistants", 0),
            stats.get("removed_tool_results", 0),
            stats.get("removed_orphans", 0),
        )

    @filter.on_llm_request(priority=-20400)
    @_multi_persona_event_context
    async def append_group_cycle_privacy_boundary(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Add a default-off, request-only Bot cycle privacy boundary for allowed groups."""
        if self is None or req is None or not bool(runtime_persona_setting(self, 'enable_group_cycle_awareness', False)):
            return
        try:
            if bool(getattr(event, "is_private_chat", lambda: False)()):
                return
        except Exception:
            return
        group_id = self._extract_group_id_from_event(event)
        if not group_id or not self._group_enabled_for_event(group_id):
            return
        daily_state = self.data.get("daily_state") if isinstance(getattr(self, "data", None), dict) else {}
        if not isinstance(daily_state, dict):
            return
        inbound_text = getattr(event, "private_companion_group_text", "") or getattr(event, "message_str", "") or ""
        boundary = build_group_cycle_boundary(
            enabled=True,
            group_allowed=True,
            cycle_label=daily_state.get("body_cycle"),
            inbound_text=inbound_text,
        )
        boundary_text = str(boundary.get("prompt") or "")
        if not boundary.get("active") or not boundary_text:
            return
        marker = "<!-- private_companion_group_cycle_boundary_v1 -->"
        current_prompt = str(getattr(req, "system_prompt", "") or "")
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        if marker in current_prompt or marker in current_turn_prompt:
            return
        placement = "prompt" if self._append_turn_prompt_fragment_by_position(
            req,
            marker,
            boundary_text,
            title="Group cycle privacy boundary",
            priority=59,
            source="safety",
        ) else "system_prompt"
        if placement == "system_prompt" and hasattr(req, "system_prompt"):
            req.system_prompt = f"{current_prompt}\n\n{marker}\n{boundary_text}".strip()

    @filter.on_llm_request(priority=-20000)
    @_multi_persona_event_context
    async def sanitize_incompatible_web_search_tools(self, event: AstrMessageEvent, req: ProviderRequest, *args, **kwargs):
        """移除 Gemini/OpenAI 兼容层会拒绝的 Baidu AI Search MCP 工具声明。"""
        if self is None or req is None:
            return
        tool_set = getattr(req, "func_tool", None)
        if tool_set is None:
            return
        if not self._tool_set_has_named_tool(tool_set, "AIsearch"):
            return
        if not self._llm_request_uses_gemini_family_provider(event, req):
            return
        remove_tool = getattr(tool_set, "remove_tool", None)
        if not callable(remove_tool):
            return
        try:
            remove_tool("AIsearch")
        except Exception as exc:
            logger.debug("[PrivateCompanion] 移除不兼容 AIsearch 工具失败: %s", _single_line(exc, 160))
            return
        settings = self._llm_request_provider_settings_for_event(event)
        provider_label = " / ".join(self._llm_request_provider_identity_parts(event, req)[:3]) or "unknown"
        umo = _single_line(getattr(event, "unified_msg_origin", ""), 120)
        log_key = f"{umo}:{provider_label}:AIsearch"
        logged = getattr(self, "_incompatible_web_search_tool_logged_keys", None)
        if not isinstance(logged, set):
            logged = set()
            setattr(self, "_incompatible_web_search_tool_logged_keys", logged)
        if log_key not in logged:
            logged.add(log_key)
            logger.warning(
                "[PrivateCompanion] 已移除本轮 Gemini 不兼容的 AIsearch 搜索工具，避免请求 400: provider=%s websearch_provider=%s session=%s",
                _single_line(provider_label, 200),
                _single_line(settings.get("websearch_provider"), 80) or "unknown",
                umo or "unknown",
            )

    @filter.on_llm_request(priority=-240000)
    @_multi_persona_event_context
    async def flush_conversation_injection_plan(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Render all registered plugin-owned conversation blocks once before provider cleanup."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        plan = get_conversation_injection_plan(req, create=False)
        if plan is None:
            return
        try:
            plan.render_into(req)
        except Exception as exc:
            logger.error(
                "[PrivateCompanion] 主对话注入计划最终渲染失败: session=%s error=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                _single_line(exc, 180),
            )

    @filter.on_llm_request(priority=-249000)
    @_multi_persona_event_context
    async def sanitize_historical_image_blocks_before_provider(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Keep legacy multimodal history compatible with text-only chat endpoints."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        cleaned, stats = sanitize_history_image_blocks(getattr(req, "contexts", None))
        if not stats.get("changed"):
            return
        try:
            req.contexts = cleaned
        except Exception:
            return
        logger.info(
            "[PrivateCompanion] 已兼容化历史图片消息: session=%s messages=%s image_blocks=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            stats.get("messages_changed", 0),
            stats.get("image_blocks_replaced", 0),
        )

    @filter.on_llm_request(priority=-250000)
    @_multi_persona_event_context
    async def sanitize_gif_inputs_before_provider(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Keep provider adapters from receiving unsupported raw GIF inputs."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        replaced, dropped = self._sanitize_provider_request_gif_inputs(req)
        if replaced or dropped:
            logger.info(
                "[PrivateCompanion] Provider 请求中的 GIF 已兼容化: converted=%s dropped=%s session=%s",
                replaced,
                dropped,
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )

    def _scope_photo_generation_tool_for_request(self, req: ProviderRequest) -> bool:
        """Remove the optional Image tool from this request when it is not ready."""
        if req is None or self._photo_generation_runtime_available():
            return False
        tool_set = getattr(req, "func_tool", None)
        if tool_set is None:
            return False
        tools = getattr(tool_set, "tools", None)
        if isinstance(tools, list):
            filtered = [
                tool
                for tool in tools
                if _single_line(getattr(tool, "name", ""), 120) != "pc_generate_photo"
            ]
            if len(filtered) == len(tools):
                return False
            try:
                request_tool_set = copy(tool_set)
                request_tool_set.tools = filtered
                req.func_tool = request_tool_set
                return True
            except Exception as exc:
                logger.debug(
                    "[PrivateCompanion] 请求级移除未就绪生图工具失败: %s",
                    _single_line(exc, 120),
                )
                return False

        try:
            request_tool_set = copy(tool_set)
            remove_tool = getattr(request_tool_set, "remove_tool", None)
            if not callable(remove_tool):
                return False
            remove_tool("pc_generate_photo")
            req.func_tool = request_tool_set
            return True
        except Exception as exc:
            logger.debug(
                "[PrivateCompanion] 兼容请求级移除未就绪生图工具失败: %s",
                _single_line(exc, 120),
            )
            return False

    def _annotate_photo_tool_prompt_format_for_request(self, req: ProviderRequest) -> bool:
        """Attach the selected prompt syntax to this request's photo tool schema."""
        if not self._photo_generation_runtime_available():
            return False
        tool_set = getattr(req, "func_tool", None) if req is not None else None
        get_tool = getattr(tool_set, "get_tool", None) if tool_set is not None else None
        if not callable(get_tool):
            return False
        try:
            tool = get_tool("pc_generate_photo")
        except Exception:
            return False
        if tool is None or not bool(getattr(tool, "active", True)):
            return False

        instruction_getter = getattr(self, "_photo_tool_prompt_format_instruction", None)
        if not callable(instruction_getter):
            return False
        instruction = re.sub(
            r"\s+",
            " ",
            str(instruction_getter() or ""),
        ).strip()
        if not instruction:
            return False
        marker = _PHOTO_TOOL_PROMPT_FORMAT_MARKER
        description = re.sub(
            rf"\n*\s*{re.escape(marker)}.*?{re.escape(marker)}\s*",
            "",
            str(getattr(tool, "description", "") or ""),
            flags=re.DOTALL,
        ).strip()
        annotated = (
            f"{description}\n\n{marker}\n"
            f"【提示词表达方式】prompt 参数必须按下述格式书写：{instruction}\n"
            f"{marker}"
        ).strip()

        def record_contract() -> None:
            plan = get_conversation_injection_plan(req)
            if plan is None:
                return
            plan.add(
                key="tool.photo.prompt_format",
                marker=marker,
                content=annotated,
                title="提示词表达方式",
                priority=10,
                source="photo_tool",
                placement=PLACEMENT_TOOL_CONTRACT,
                temporary=True,
                materialized=True,
                opaque=True,
                merge_policy="replace",
            )

        tools = getattr(tool_set, "tools", None)
        if isinstance(tools, list):
            try:
                for index, existing in enumerate(tools):
                    if existing is tool:
                        request_tool = copy(tool)
                        request_tool.description = annotated
                        request_tools = list(tools)
                        request_tools[index] = request_tool
                        request_tool_set = copy(tool_set)
                        request_tool_set.tools = request_tools
                        req.func_tool = request_tool_set
                        record_contract()
                        return True
            except Exception as exc:
                logger.debug(
                    "[PrivateCompanion] pc_generate_photo 请求工具描述标注失败: %s",
                    _single_line(exc, 120),
                )
                return False

        # Older request-local wrappers may expose get_tool() without a tools list.
        if getattr(tool, "handler", None) is None:
            try:
                tool.description = annotated
                record_contract()
                return True
            except Exception as exc:
                logger.debug(
                    "[PrivateCompanion] pc_generate_photo 兼容工具描述标注失败: %s",
                    _single_line(exc, 120),
                )
        return False

    @filter.on_llm_request(priority=-251000)
    @_multi_persona_event_context
    async def annotate_photo_tool_prompt_format(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Expose prompt-format guidance whenever the photo tool is available."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        try:
            if self._scope_photo_generation_tool_for_request(req):
                return
            self._annotate_photo_tool_prompt_format_for_request(req)
        except Exception as exc:
            logger.debug(
                "[PrivateCompanion] pc_generate_photo 工具提示词格式标注失败: %s",
                _single_line(exc, 120),
            )

    @filter.on_llm_request(priority=-252000)
    @_multi_persona_event_context
    async def intercept_native_astrbot_group_context(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Prefer the plugin group context while preserving AstrBot records."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        if bool(getattr(event, "is_private_chat", lambda: False)()):
            return
        if not bool(runtime_persona_setting(self, "intercept_astrbot_group_context", True)):
            return
        if not bool(runtime_persona_setting(self, "enable_group_history_injection", True)):
            return
        marker = "<!-- private_companion_group_context_v1 -->"
        if not self._request_has_managed_prompt_marker(req, marker):
            return
        result = intercept_astrbot_group_context(event, req)
        logger.info(
            "[PrivateCompanion] 已拦截 AstrBot 群聊对话注入: session=%s history=%s icl=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 140) or "unknown",
            result.get("history_messages", 0),
            result.get("group_icl_removed", 0),
        )

    @filter.on_llm_request(priority=-259000)
    @_multi_persona_event_context
    async def enforce_private_request_scope_isolation(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Prune group-only plan blocks and residues before private dispatch."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        try:
            if not bool(getattr(event, "is_private_chat", lambda: False)()):
                return
        except Exception:
            if ":FriendMessage:" not in str(
                getattr(event, "unified_msg_origin", "") or ""
            ):
                return

        plan = get_conversation_injection_plan(req, create=False)
        if plan is not None:
            try:
                if plan.remove_markers(
                    f"<!-- {marker} -->" for marker in GROUP_SCOPE_MARKERS
                ):
                    plan.render_into(req)
            except Exception as exc:
                logger.warning(
                    "[PrivateCompanion] 私聊请求的群作用域注入计划裁剪失败: session=%s error=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120)
                    or "unknown",
                    _single_line(exc, 160),
                )
        self._sanitize_private_companion_prompt_artifacts_in_request(event, req)

    @filter.on_llm_request(priority=-260000)
    @_multi_persona_event_context
    async def finalize_conversation_injection_plan(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Freeze a privacy-safe manifest after all plugin-owned request changes."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        plan = get_conversation_injection_plan(req, create=False)
        if plan is None:
            return
        try:
            plan.render_into(req)
            setattr(
                req,
                "_private_companion_conversation_injection_manifest",
                plan.manifest(),
            )
            plan.freeze()
        except Exception as exc:
            logger.error(
                "[PrivateCompanion] 主对话注入计划冻结失败: session=%s error=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120)
                or "unknown",
                _single_line(exc, 180),
            )

    @filter.on_llm_request()
    @_multi_persona_event_context
    async def inject_humanized_state(self, event: AstrMessageEvent, req: ProviderRequest, *args, **kwargs):
        return await run_humanized_state_injection(self, event, req, *args, **kwargs)

    @filter.on_llm_response(priority=120000)
    @_multi_persona_event_context
    async def replace_sensitive_conversation_response(
        self,
        event: AstrMessageEvent,
        resp: LLMResponse,
        *args,
        **kwargs,
    ):
        """Retry common model refusals on the configured conversation Provider."""
        if self is None or not bool(getattr(self, "enabled", False)):
            return
        if not bool(getattr(self, "enable_sensitive_model_replacement", False)):
            return
        if not scope_allows(getattr(self, "model_replacement_scope", "plugin"), "conversation"):
            return
        if bool(getattr(event, "private_companion_sensitive_model_retry", False)):
            return
        text = str(getattr(resp, "completion_text", "") or "").strip()
        keyword = contains_sensitive_refusal(text, getattr(self, "sensitive_replacement_keywords", ""))
        if not keyword:
            return
        target_provider = _single_line(getattr(self, "sensitive_replacement_provider_id", ""), 160)
        if not target_provider:
            return
        current_provider = self._provider_id_from_llm_response(resp)
        if target_provider == current_provider:
            return
        getter = getattr(getattr(self, "context", None), "get_provider_by_id", None)
        if not callable(getter):
            return
        try:
            if getter(target_provider) is None:
                logger.warning("[PrivateCompanion] 敏感拒答替换模型不存在：%s", target_provider)
                return
        except Exception:
            return
        request = self._model_replacement_event_extra(event, "provider_request", None)
        if request is None:
            request = getattr(event, "private_companion_model_replacement_request", None)
        try:
            setattr(event, "private_companion_sensitive_model_retry", True)
        except Exception:
            pass
        try:
            retry_kwargs: dict[str, Any] = {
                "chat_provider_id": target_provider,
                "prompt": getattr(request, "prompt", None) if request is not None else getattr(event, "message_str", ""),
                "contexts": getattr(request, "contexts", None) if request is not None else None,
                "system_prompt": getattr(request, "system_prompt", None) if request is not None else None,
                "image_urls": list(getattr(request, "image_urls", None) or []) if request is not None else None,
                "audio_urls": list(getattr(request, "audio_urls", None) or []) if request is not None else None,
            }
            fallback = await self.context.llm_generate(**retry_kwargs)
            fallback_text = str(getattr(fallback, "completion_text", "") or "").strip()
            fallback_keyword = contains_sensitive_refusal(
                fallback_text,
                getattr(self, "sensitive_replacement_keywords", ""),
            )
            if not fallback_text or fallback_keyword:
                try:
                    resp.result_chain = None
                    resp.completion_text = ""
                except Exception:
                    pass
                logger.warning(
                    "[PrivateCompanion] 敏感拒答替换模型仍拒答，已阻断原回复: original=%s target=%s keyword=%s",
                    _single_line(current_provider, 120) or "unknown",
                    target_provider,
                    _single_line(fallback_keyword or keyword, 80),
                )
                return
            resp.completion_text = fallback_text
            resp.result_chain = getattr(fallback, "result_chain", None)
            try:
                resp.role = getattr(fallback, "role", None) or resp.role
            except Exception:
                pass
            logger.info(
                "[PrivateCompanion] 检测到模型敏感拒答，已改用指定对话模型: original=%s target=%s keyword=%s",
                _single_line(current_provider, 120) or "unknown",
                target_provider,
                _single_line(keyword, 80),
            )
        except Exception as exc:
            logger.warning(
                "[PrivateCompanion] 敏感拒答替换模型调用失败，已阻断原回复: target=%s error=%s",
                target_provider,
                _single_line(exc, 180),
            )
            try:
                resp.result_chain = None
                resp.completion_text = ""
            except Exception:
                pass

    @filter.on_llm_response()
    @_multi_persona_event_context
    async def normalize_tts_enhancement_response(self, event: AstrMessageEvent, resp: LLMResponse, *args, **kwargs):
        """恢复降级为正文的生图调用，并规范化 TTS 标签。"""
        if self is None or not self.enabled:
            return
        original_text = str(getattr(resp, "completion_text", "") or "")
        same_session_tool = getattr(self, "_prepare_same_session_send_tool_response", None)
        same_session_tool_call = False
        if callable(same_session_tool):
            try:
                same_session_tool_call, _ = same_session_tool(event, resp)
            except Exception as exc:
                logger.debug(
                    "[PrivateCompanion] 同会话工具回复去重准备失败: %s",
                    _single_line(exc, 120),
                )
        if same_session_tool_call:
            # AstrBot yields completion_text even when the same response also has
            # a tool call. The tool/final-response path is authoritative here.
            try:
                resp.result_chain = None
            except Exception:
                pass
            resp.completion_text = ""
            original_text = ""
        tool_names = getattr(resp, "tools_call_name", None)
        if isinstance(tool_names, str):
            normalized_tool_names = {tool_names.strip()}
        elif isinstance(tool_names, (list, tuple, set)):
            normalized_tool_names = {
                str(item or "").strip() for item in tool_names if str(item or "").strip()
            }
        else:
            normalized_tool_names = set()
        media_delivery_tool_call = bool(
            normalized_tool_names
            & {"pc_find_reaction_image", "pc_generate_photo", "pc_send_current_media"}
        )
        if media_delivery_tool_call:
            # These tools own their visible caption/media delivery. AstrBot also
            # yields assistant content attached to a tool call as an llm_result;
            # exposing that intermediate text produces a duplicate before the
            # tool result is known and can falsely claim that an image was sent.
            try:
                resp.result_chain = None
            except Exception:
                pass
            resp.completion_text = ""
            original_text = ""
            logger.info(
                "[PrivateCompanion] 已隐藏媒体工具调用前的中间正文: tools=%s session=%s",
                ",".join(sorted(normalized_tool_names)),
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
        recovered_text, _ = await self._recover_plaintext_photo_tool_call(event, resp, original_text)
        if recovered_text != original_text:
            resp.completion_text = recovered_text
        if bool(getattr(event, "_private_companion_photo_tool_sent", False)):
            # pc_generate_photo 已经把 caption 与图片作为唯一可见回复发出。
            # 不论模型是否输出静默标记，都丢弃同一轮尾随正文，避免再次分段、TTS 或触发表情附件。
            try:
                resp.result_chain = None
            except Exception:
                pass
            resp.completion_text = ""
            for attr in (
                "_private_companion_reaction_expression_intent",
                "_private_companion_deferred_reaction_tts",
                "_private_companion_reaction_expression_expected_primary_chunks",
                "_private_companion_reaction_expression_segmented_remainder",
            ):
                try:
                    delattr(event, attr)
                except (AttributeError, TypeError):
                    pass
            logger.info(
                "[PrivateCompanion] 图片已发送，已丢弃同轮尾随模型正文: session=%s chars=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                len(recovered_text or ""),
            )
            return
        reaction_extractor = getattr(
            self, "_extract_reaction_expression_hidden_intent", None
        )
        if callable(reaction_extractor):
            cleaned_reaction_text, reaction_intent = reaction_extractor(
                recovered_text
            )
        else:
            cleaned_reaction_text, reaction_intent = recovered_text, {}
        response_has_tool_call = bool(getattr(resp, "tools_call_name", None))
        if response_has_tool_call:
            try:
                delattr(event, "_private_companion_reaction_expression_intent")
            except (AttributeError, TypeError):
                pass
        reaction_authorization_getter = getattr(
            self, "_reaction_expression_authorization", None
        )
        authorization = (
            reaction_authorization_getter(event)
            if callable(reaction_authorization_getter)
            else {}
        )
        reaction_visible_checker = getattr(
            self, "_reaction_expression_has_visible_text", None
        )
        reaction_visible_text = (
            reaction_visible_checker(cleaned_reaction_text)
            if callable(reaction_visible_checker)
            else bool(str(cleaned_reaction_text or "").strip())
        )
        reaction_runtime_logger = getattr(
            self, "_log_reaction_expression_event", None
        )
        reaction_scope_getter = getattr(self, "_reaction_expression_scope", None)
        reaction_scope = (
            reaction_scope_getter(event)
            if callable(reaction_scope_getter)
            else "unknown"
        )
        if cleaned_reaction_text != recovered_text:
            resp.completion_text = cleaned_reaction_text
            recovered_text = cleaned_reaction_text
            if (
                reaction_intent
                and authorization.get("authorized")
                and not authorization.get("consumed")
                and reaction_visible_text
                and not response_has_tool_call
            ):
                try:
                    setattr(
                        event,
                        "_private_companion_reaction_expression_intent",
                        reaction_intent,
                    )
                except Exception:
                    pass
                if callable(reaction_runtime_logger):
                    reaction_runtime_logger(
                        event,
                        stage="intent",
                        decision="accepted",
                        reason="intent_extracted",
                        scope=reaction_scope,
                    )
            elif reaction_intent and callable(reaction_runtime_logger):
                reaction_runtime_logger(
                    event,
                    stage="intent",
                    decision="discarded",
                        reason=(
                            "tool_call_intermediate"
                            if response_has_tool_call
                            else "intent_discarded"
                        ),
                        scope=reaction_scope,
                    )
        existing_reaction_intent = getattr(
            event, "_private_companion_reaction_expression_intent", None
        )
        if (
            authorization.get("authorized")
            and not authorization.get("consumed")
            and reaction_visible_text
            and not reaction_intent
            and not (
                isinstance(existing_reaction_intent, dict)
                and bool(existing_reaction_intent)
            )
            and not response_has_tool_call
            and not authorization.get("model_omission_recorded")
        ):
            authorization["model_omission_recorded"] = True
            authorization_setter = getattr(
                self, "_set_reaction_expression_authorization", None
            )
            if callable(authorization_setter):
                authorization_setter(event, authorization)
            runtime_notifier = getattr(self, "_note_reaction_expression_runtime", None)
            if callable(runtime_notifier):
                runtime_notifier(
                    model_omissions=1,
                    last_reason="model_omitted_intent",
                )
            if callable(reaction_runtime_logger):
                reaction_runtime_logger(
                    event,
                    stage="intent",
                    decision="omit",
                    reason="model_omitted_intent",
                    scope=authorization.get("scope") or reaction_scope,
                )
            fallback_builder = getattr(self, "_reaction_expression_local_fallback_intent", None)
            fallback_intent = (
                fallback_builder(event, cleaned_reaction_text, authorization)
                if callable(fallback_builder)
                else {}
            )
            if fallback_intent:
                try:
                    setattr(
                        event,
                        "_private_companion_reaction_expression_intent",
                        fallback_intent,
                    )
                except Exception:
                    pass
                if callable(runtime_notifier):
                    runtime_notifier(
                        local_fallbacks=1,
                        last_reason="local_fallback_intent",
                    )
                if callable(reaction_runtime_logger):
                    reaction_runtime_logger(
                        event,
                        stage="intent",
                        decision="accepted",
                        reason="local_fallback_intent",
                        scope=authorization.get("scope") or reaction_scope,
                    )
        sent_photo_caption = str(
            getattr(event, "_private_companion_photo_tool_sent_caption", "") or ""
        ).strip()
        if (
            bool(getattr(event, "_private_companion_photo_tool_sent", False))
            and PHOTO_TOOL_SILENT_SENTINEL in recovered_text
        ):
            try:
                resp.result_chain = None
            except Exception:
                pass
            resp.completion_text = ""
            original_text = ""
            recovered_text = ""
            logger.info(
                "[PrivateCompanion] 已清除图片工具成功发送后的内部静默标记: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
        if (
            bool(getattr(event, "_private_companion_photo_tool_sent", False))
            and self._photo_tool_followup_is_redundant(sent_photo_caption, recovered_text)
        ):
            try:
                resp.result_chain = None
            except Exception:
                pass
            resp.completion_text = ""
            original_text = ""
            recovered_text = ""
            logger.info(
                "[PrivateCompanion] 已移除生图工具成功发送后的重复承接正文: session=%s caption=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                _single_line(sent_photo_caption, 120),
            )
        pending_tool_text = str(
            getattr(event, "_private_companion_same_session_tool_text", "") or ""
        ).strip()
        tool_names = getattr(resp, "tools_call_name", None)
        has_tool_call = bool(tool_names) if isinstance(tool_names, (list, tuple, set, str)) else False
        if (
            not same_session_tool_call
            and pending_tool_text
            and not has_tool_call
            and not bool(getattr(event, "_private_companion_same_session_tool_finalized", False))
        ):
            # A same-session tool call already contains the intended visible
            # message. Use it once as the final assistant response instead of
            # sending the tool payload and then repeating it here.
            try:
                resp.result_chain = None
            except Exception:
                pass
            resp.completion_text = pending_tool_text
            recovered_text = pending_tool_text
            try:
                setattr(event, "_private_companion_same_session_tool_finalized", True)
            except Exception:
                pass
            logger.info(
                "[PrivateCompanion] 已将同会话工具文本恢复为唯一最终回复: session=%s text=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                _single_line(pending_tool_text, 160),
            )
        called_names = getattr(resp, "tools_call_name", None)
        creative_tool_called = bool(
            (isinstance(called_names, str) and called_names.strip() == "pc_view_creative_work")
            or (
                isinstance(called_names, (list, tuple, set))
                and "pc_view_creative_work" in {str(item) for item in called_names}
            )
        )
        if creative_tool_called:
            try:
                setattr(event, "private_companion_creative_work_tool_attempted", True)
            except Exception:
                pass
        guarded_text = self._guard_unread_creative_work_response(event, recovered_text)
        if guarded_text != recovered_text:
            resp.completion_text = guarded_text
        original_text = guarded_text
        if self._proactive_only_blocks_passive_event(event, "enable_tts_enhancement"):
            return
        normalized_text = _normalize_outbound_punctuation_flow(original_text)
        if normalized_text and normalized_text != original_text:
            resp.completion_text = normalized_text
        await self.protect_tts_enhancement_response_blocks(event, resp)

    @filter.on_llm_response()
    @_multi_persona_event_context
    async def record_external_llm_token_usage(self, event: AstrMessageEvent, resp: LLMResponse, *args, **kwargs):
        """统计非插件内部调用的 AstrBot 主回复 Token，单独展示且不计入插件限额。"""
        if self is None or not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "llm_request"):
            return
        if bool(getattr(event, "private_companion_skip_external_token_stats", False)):
            return
        prompt = str(getattr(event, "private_companion_external_token_prompt", "") or "")
        started = _safe_float(getattr(event, "private_companion_external_token_start", 0), 0)
        completion = self._completion_text_for_token_stats(resp)
        response_tool_names = getattr(resp, "tools_call_name", None) if resp is not None else None
        if isinstance(response_tool_names, str):
            has_tool_call = bool(response_tool_names.strip())
        elif isinstance(response_tool_names, (list, tuple, set)):
            has_tool_call = any(str(item or "").strip() for item in response_tool_names)
        else:
            has_tool_call = False
        if not prompt and not completion and resp is None:
            return
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        resp_id = _single_line(getattr(resp, "id", ""), 120) if resp is not None else ""
        usage = getattr(resp, "usage", None) if resp is not None else None
        usage_total = _safe_int(getattr(usage, "total", 0), 0)
        trigger_message_id = self._event_message_id(event)
        completion_sig = hashlib.sha1(
            completion[:4000].encode("utf-8", errors="ignore")
        ).hexdigest()[:16] if completion else ""
        record_key = "|".join(
            (
                resp_id,
                umo,
                sender_id,
                trigger_message_id,
                str(usage_total),
                str(len(prompt)),
                str(len(completion)),
                completion_sig,
            )
        )
        try:
            recorded_keys = getattr(event, "private_companion_external_token_recorded_keys", None)
            if not isinstance(recorded_keys, set):
                recorded_keys = set()
                setattr(event, "private_companion_external_token_recorded_keys", recorded_keys)
            if record_key in recorded_keys:
                return
            recorded_keys.add(record_key)
        except Exception:
            pass
        try:
            is_private_chat = bool(getattr(event, "is_private_chat", lambda: False)())
            task = "astrbot_private_reply" if is_private_chat else "astrbot_group_reply"
        except Exception:
            is_private_chat = False
            task = "astrbot_reply"
        provider_id = self._provider_id_from_llm_response(resp) or self._default_chat_provider_id(umo)
        self._record_external_llm_usage(
            provider_id=provider_id,
            task=task,
            prompt=prompt,
            completion=completion,
            elapsed_ms=int(max(0.0, time.time() - started) * 1000) if started > 0 else 0,
            success=bool(completion or has_tool_call),
            error="" if completion or has_tool_call else "empty_response",
            resp=resp,
            session_id=umo,
            sender_id=sender_id,
            message_type="private" if is_private_chat else "group",
        )

    @filter.on_llm_response()
    @_multi_persona_event_context
    async def record_group_expression_rule_usage(self, event: AstrMessageEvent, resp: LLMResponse, *args, **kwargs):
        """记录群聊中实际进入主回复链的已审核语义表达规则。"""
        if self is None or not self.enabled or bool(getattr(event, "is_private_chat", lambda: False)()):
            return
        semantic_rules = getattr(event, "private_companion_semantic_expression_rules", None)
        if not isinstance(semantic_rules, list) or not semantic_rules:
            return
        if bool(getattr(event, "private_companion_group_semantic_usage_recorded", False)):
            return
        completion = _single_line(getattr(resp, "completion_text", ""), 500)
        if not completion:
            return
        # Rule usage is committed by the confirmed-delivery finalizer. Keep this
        # hook read-only so an LLM response that is later dropped cannot mutate
        # durable expression-learning state.
        try:
            setattr(event, "private_companion_group_semantic_usage_pending", True)
        except Exception:
            pass

    @filter.on_llm_response()
    @_multi_persona_event_context
    async def capture_llm_timer_directive(self, event: AstrMessageEvent, resp: LLMResponse, *args, **kwargs):
        """LLM 回复后捕获定时/状态指令，并做私聊回复审校。"""
        release_now = False
        try:
            if self is None or not self.enabled:
                release_now = True
                return
            if bool(getattr(event, "private_companion_proactive_framework", False)):
                return
            if self._proactive_only_blocks_passive_event(event, "enable_llm_timer_scheduling"):
                release_now = True
                return
            if not bool(getattr(event, "is_private_chat", lambda: False)()):
                return
            original_text = str(resp.completion_text or "").strip()
            if not original_text:
                if bool(getattr(event, "_private_companion_plaintext_photo_sent", False)):
                    self._stop_passive_input_status_loop(event)
                    release_now = True
                    return
                self._stop_passive_input_status_loop(event)
                self._record_passive_no_reply(
                    event,
                    source="主链回复",
                    reason="LLM 返回空回复",
                    level="warn",
                )
                release_now = True
                return
            try:
                user_id = str(event.get_sender_id())
            except Exception:
                self._stop_passive_input_status_loop(event)
                release_now = True
                return
            resolver = getattr(self, "_private_user_id_for_event", None)
            if callable(resolver):
                user_id = resolver(event, user_id)
            raw_users = self.data.get("users", {})
            current_user = raw_users.get(user_id) if isinstance(raw_users, dict) else None
            if not isinstance(current_user, dict):
                self._stop_passive_input_status_loop(event)
                release_now = True
                return
            working_text = original_text
            reply_image_count = _safe_int(getattr(event, "private_companion_reply_image_count", 1), 1, 1, 5)
            reply_image_vision = _single_line(
                getattr(event, "private_companion_reply_image_vision_text", ""),
                self._private_image_vision_text_limit(reply_image_count),
            )
            reply_image_user_text = _single_line(
                getattr(event, "private_companion_reply_image_user_text", "") or current_user.get("last_user_message"),
                260,
            )
            if (
                reply_image_vision
                and bool(getattr(event, "private_companion_reply_image_content_question", False))
                and self._private_image_reply_misses_content_question(working_text)
            ):
                corrected = self._private_image_content_answer_from_vision(
                    reply_image_vision,
                    user_text=reply_image_user_text,
                )
                if corrected:
                    logger.info(
                        "[PrivateCompanion] 私聊引用图片回复疑似被历史话题污染,已按视觉摘要纠偏: user=%s before=%s after=%s",
                        user_id,
                        _single_line(working_text, 120),
                        _single_line(corrected, 160),
                    )
                    working_text = corrected
                    resp.completion_text = corrected
            if self.enable_llm_timer_scheduling and "<timer" in original_text.lower():
                cleaned_text, payloads = self._extract_timer_directives(original_text)
                if cleaned_text != original_text:
                    working_text = cleaned_text
                    resp.completion_text = working_text
                if payloads:
                    timer_source_text = _single_line(current_user.get("last_user_message"), 260) or working_text
                    await self._schedule_llm_timer_after_response_dedup(
                        event,
                        resp,
                        user_id,
                        payloads[-1],
                        source_text=timer_source_text,
                        visible_text=working_text,
                        trigger_message_id=self._event_message_id(event),
                        trigger_umo=str(getattr(event, "unified_msg_origin", "") or ""),
                    )

            inbound_text = _single_line(current_user.get("last_user_message"), 260)
            sanitized_elapsed_text = self._sanitize_unverified_repeat_elapsed_claim(
                inbound_text,
                working_text,
                current_user,
            )
            sanitized_elapsed_text = self._sanitize_robotic_topic_choice_after_repeat_correction(
                inbound_text,
                sanitized_elapsed_text,
            )
            if sanitized_elapsed_text != working_text:
                logger.info(
                    "[PrivateCompanion] 已清理重复纠正后的生硬回复: user=%s before=%s after=%s",
                    user_id,
                    _single_line(working_text, 120),
                    _single_line(sanitized_elapsed_text, 120),
                )
                working_text = sanitized_elapsed_text
                resp.completion_text = working_text
            music_album_context = getattr(event, "private_companion_reply_music_album_context", None)
            silence_decision = await self._decide_smart_silence(
                inbound_text=inbound_text,
                response_text=working_text,
                user=current_user,
                session_kind="private",
            )
            if str(silence_decision.get("decision") or "") == "silent":
                setattr(event, "_private_companion_smart_silence_drop", True)
                setattr(event, "_private_companion_smart_silence_reason", _single_line(silence_decision.get("reason"), 120))
                resp.completion_text = ""
                async with self._data_lock:
                    current = self._get_user(user_id)
                    stats = current.setdefault("postprocess_stats", {})
                    if not isinstance(stats, dict):
                        stats = {}
                        current["postprocess_stats"] = stats
                    stats["smart_silence"] = _safe_int(stats.get("smart_silence"), 0, 0) + 1
                    stats["last_smart_silence_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M")
                    self._save_data_sync(sections={"users"})
                logger.info(
                    "[PrivateCompanion] 智能沉默已取消本轮私聊回复: user=%s reason=%s inbound=%s reply=%s",
                    user_id,
                    _single_line(silence_decision.get("reason"), 120),
                    _single_line(inbound_text, 120),
                    _single_line(working_text, 140),
                )
                self._record_passive_no_reply(
                    event,
                    source="智能沉默",
                    reason=_single_line(silence_decision.get("reason"), 120) or "用户边界语义触发静默",
                    detail=inbound_text,
                    reply_preview=working_text,
                    level="info",
                )
                release_now = True
                return
            reviewed_text = await self._review_and_rewrite_response(
                current_user,
                inbound_text,
                working_text,
                music_album_context=music_album_context if isinstance(music_album_context, dict) else None,
                creative_context=str(getattr(event, "private_companion_creative_reply_context", "") or ""),
                review_event=event,
            )
            if self._passive_response_review_enabled() and self._is_response_review_drop_marker(reviewed_text):
                setattr(event, "_private_companion_response_review_drop", True)
                resp.completion_text = ""
                async with self._data_lock:
                    current = self._get_user(user_id)
                    stats = current.setdefault("postprocess_stats", {})
                    if not isinstance(stats, dict):
                        stats = {}
                        current["postprocess_stats"] = stats
                    stats["duplicate_dropped"] = _safe_int(stats.get("duplicate_dropped"), 0, 0) + 1
                    stats["last_duplicate_dropped_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M")
                    self._save_data_sync(sections={"users"})
                logger.info(
                    "[PrivateCompanion] 回复复核已取消重复私聊回复: user=%s inbound=%s reply=%s",
                    user_id,
                    _single_line(inbound_text, 120),
                    _single_line(working_text, 160),
                )
                self._record_passive_no_reply(
                    event,
                    source="回复复核去重",
                    reason="最终回复与上一条 Bot 消息重复",
                    detail=inbound_text,
                    reply_preview=working_text,
                    level="info",
                )
                release_now = True
                return
            if reviewed_text != working_text:
                resp.completion_text = reviewed_text
                working_text = reviewed_text
                async with self._data_lock:
                    current = self._get_user(user_id)
                    stats = current.setdefault("postprocess_stats", {})
                    if not isinstance(stats, dict):
                        stats = {}
                        current["postprocess_stats"] = stats
                    stats["rewritten"] = _safe_int(stats.get("rewritten"), 0, 0) + 1
                    stats["last_rewritten_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M")
                    self._save_data_sync(sections={"users"})

            async with self._data_lock:
                live_user_for_duplicate = self._get_user(user_id)
            if self._passive_response_review_enabled() and self._effective_passive_review_strength() != "lenient":
                should_drop_duplicate, duplicate_reason = self._should_drop_duplicate_reply_text(live_user_for_duplicate, inbound_text, working_text)
            else:
                should_drop_duplicate, duplicate_reason = False, ""
            if should_drop_duplicate:
                setattr(event, "_private_companion_response_review_drop", True)
                resp.completion_text = ""
                async with self._data_lock:
                    current = self._get_user(user_id)
                    stats = current.setdefault("postprocess_stats", {})
                    if not isinstance(stats, dict):
                        stats = {}
                        current["postprocess_stats"] = stats
                    stats["duplicate_dropped"] = _safe_int(stats.get("duplicate_dropped"), 0, 0) + 1
                    stats["last_duplicate_dropped_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M")
                    self._save_data_sync(sections={"users"})
                logger.info(
                    "[PrivateCompanion] 发送前去重已取消重复私聊回复: user=%s reason=%s inbound=%s reply=%s",
                    user_id,
                    _single_line(duplicate_reason, 120),
                    _single_line(inbound_text, 120),
                    _single_line(working_text, 160),
                )
                self._record_passive_no_reply(
                    event,
                    source="回复复核去重",
                    reason=duplicate_reason or "最终回复与上一条 Bot 消息重复",
                    detail=inbound_text,
                    reply_preview=working_text,
                    level="info",
                )
                release_now = True
                return

            if working_text != original_text:
                self._schedule_reply_interception_forward(
                    "rewrite",
                    source="私聊回复处理",
                    reason="回复在发送前经过纠偏、清理或复核改写",
                    source_session=_single_line(getattr(event, "unified_msg_origin", ""), 180),
                    inbound=inbound_text,
                    before=original_text,
                    after=working_text,
                )
        except Exception:
            release_now = True
            raise
        finally:
            pass

    def _sanitize_unverified_repeat_elapsed_claim(
        self,
        inbound_text: str,
        response_text: str,
        user: dict[str, Any],
    ) -> str:
        text = str(response_text or "").strip()
        if not text:
            return ""
        inbound = str(inbound_text or "").strip()
        if not re.search(r"(说过|讲过|提过|聊过|发过|说了|讲了|提了).{0,4}(啦|了|呀|啊)?$", inbound):
            return text
        if not re.search(r"\d+\s*(?:个)?\s*(?:小时|分钟|天)前.{0,8}(?:说过|讲过|提过|聊过|发过)", text):
            return text

        last_at = 0.0
        if isinstance(user, dict):
            last_at = _safe_float(user.get("last_companion_message_at"), 0) or _safe_float(user.get("last_reply_at"), 0)
        elapsed = _now_ts() - last_at if last_at > 0 else 0.0
        if 0 < elapsed <= 90 * 60:
            replacement = "刚才说过了"
        elif 0 < elapsed <= 6 * 3600:
            replacement = "前面说过了"
        else:
            replacement = "之前说过了"
        cleaned = re.sub(
            r"\d+\s*(?:个)?\s*(?:小时|分钟|天)前.{0,4}(?:已经|就)?(?:说过|讲过|提过|聊过|发过)(?:了)?",
            replacement,
            text,
        )
        cleaned = re.sub(r"(刚才说过了|前面说过了|之前说过了)(?:了)+", r"\1", cleaned)
        return cleaned.strip()

    def _sanitize_robotic_topic_choice_after_repeat_correction(
        self,
        inbound_text: str,
        response_text: str,
    ) -> str:
        text = str(response_text or "").strip()
        if not text:
            return ""
        inbound = str(inbound_text or "").strip()
        if not re.search(r"(说过|讲过|提过|聊过|发过|说了|讲了|提了).{0,4}(啦|了|呀|啊)?$", inbound):
            return text
        original = text
        text = re.sub(r"刚醒(?=脑子|反应|没转|有点懵)", "刚才", text)
        text = re.sub(
            r"(?:（|\()\s*(?:看来|可能|大概)?\s*刚(?:才)?脑子([^）)]{0,24}?没(?:转|反应)[^）)]*?)\s*(?:）|\))",
            r"刚才脑子\1。",
            text,
        )
        text = re.sub(
            r"(?:那)?\s*(?:你)?(?:希望|想让|要不要|要我|我是不是该)?[^。！？!?]{0,36}(?:换个话题|换话题)[^。！？!?]{0,36}(?:继续聊|接着聊|聊下去)[^。！？!?]*[？?。！!]*",
            "",
            text,
        )
        text = re.sub(
            r"(?:那)?\s*(?:你)?(?:希望|想让|要不要|要我|我是不是该)?[^。！？!?]{0,36}(?:继续聊|接着聊|聊下去)[^。！？!?]{0,36}(?:换个话题|换话题)[^。！？!?]*[？?。！!]*",
            "",
            text,
        )
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"([。！？!?])\s+", r"\1", text)
        text = re.sub(r"[，,、；;]\s*$", "。", text).strip()
        if text != original and not re.search(r"(不绕|先收|换个轻点|我记住|脑子|对哦|说过)", text):
            text = f"{text.rstrip('。！？!?')}，我先不绕这个了。"
        if text != original and re.fullmatch(r"(啊[，,。…]*)?(对哦[，,。…]*)?", text):
            text = "啊，对哦，刚才脑子没转过来，我先不绕这个了。"
        return text or original

    async def _debug_prompt_text(self, kind: str, user: dict[str, Any], event: AstrMessageEvent | None = None) -> str:
        normalized = str(kind or "").strip().lower()
        await self._ensure_weather_context()
        if normalized in {"日程", "plan", "daily_plan"}:
            memory_companion_context = ""
            memory_companion_context_getter = getattr(self, "_memory_companion_compose_schedule_context", None)
            if callable(memory_companion_context_getter):
                memory_companion_context = await memory_companion_context_getter(kind="daily_plan", max_chars=1300)
            return self._build_daily_plan_prompt(
                self._environment_now().strftime("%Y-%m-%d %H:%M"),
                memory_companion_context=memory_companion_context,
            )
        if normalized in {"细化", "detail", "enhancement"}:
            plan = dict(self.data.get("daily_plan", {}))
            state = dict(self.data.get("daily_state", {}))
            enhanced = self.data.get("detail_enhanced_segments", {})
            if not isinstance(enhanced, dict):
                enhanced = {}
            segment = self._current_detail_segment_for_update() or self._pick_detail_segment(plan, enhanced)
            if not segment:
                current_item = self._get_current_plan_item(plan)
                if not isinstance(current_item, dict):
                    return "当前没有可用于细化的日程段。先生成日程,并等到某个时间段临近,或让当天有当前日程项。"
                start = self._parse_hhmm_to_minutes(current_item.get("time")) or self._environment_now_minutes()
                segment = {
                    "start": start,
                    "end": min(24 * 60, start + 120),
                    "item": current_item,
                }
            memory_companion_context = ""
            memory_companion_context_getter = getattr(self, "_memory_companion_compose_schedule_context", None)
            if callable(memory_companion_context_getter):
                memory_companion_context = await memory_companion_context_getter(
                    kind="detail",
                    segment=segment,
                    plan=plan,
                    state=state,
                    max_chars=1100,
                )
            return self._build_detail_enhancement_prompt(
                segment,
                plan,
                state,
                memory_companion_context=memory_companion_context,
            )
        if normalized in {"主动", "proactive"}:
            name = str(user.get("nickname") or runtime_persona_setting(self, 'default_nickname', '你'))
            planned_reason = str(user.get("planned_proactive_reason") or "")
            planned_action = str(user.get("planned_proactive_action") or "message")
            planned_motive = _single_line(user.get("planned_proactive_motive"), 140)
            reason = planned_reason if planned_reason and self._is_reason_allowed_now(planned_reason, user) else ""
            if not reason:
                reason, _ = self._choose_proactive_message(user, name, planned_reason)
                planned_motive = self._choose_proactive_motive(reason, user, action=planned_action)
            planned_topic = _single_line(user.get("planned_proactive_topic"), 48)
            framework_prompt = await self._build_framework_proactive_prompt(
                user=user,
                name=name,
                reason=reason,
                action=planned_action,
                action_context="（调试预览：这里会放工具结果或观察结果）",
                motive=planned_motive,
            )
            return (
                "【说明】\n"
                "当前主动消息已改为走 AstrBot 框架唤醒链。\n"
                "人格、历史对话和会话上下文不再在这里手工重复拼接,而是由框架根据当前 conversation 自动注入。\n\n"
                + (f"【内部话题钩子】\n{planned_topic}\n\n" if planned_topic else "")
                + "【送入框架的任务提示】\n"
                f"{framework_prompt}"
            )
        if normalized in {"回复注入", "reply", "injection"}:
            await self._refresh_default_persona_prompt(getattr(event, "unified_msg_origin", "") if event is not None else "")
            state = await self._ensure_daily_state()
            parts = [self._format_state_injection(state)]
            life_context = self._format_life_context_injection()
            if life_context:
                parts.append(life_context)
            important_dates = self._format_important_dates_injection()
            if important_dates:
                parts.append(important_dates)
            memo_notes = self._format_memo_notes_injection()
            if memo_notes:
                parts.append(memo_notes)
            detail_injection = self._format_detail_injection()
            if detail_injection:
                parts.append(detail_injection)
            return "\n\n".join(parts)
        return "可查看的提示词类型：日程 / 细化 / 主动 / 回复注入"

    def _should_skip_recent_outfit_command_send(
        self,
        event: AstrMessageEvent,
        *,
        text: str,
        image_path: str,
        ttl_seconds: float = 30.0,
    ) -> bool:
        cache = getattr(self, "_recent_outfit_command_sends", None)
        if not isinstance(cache, dict):
            cache = {}
            self._recent_outfit_command_sends = cache
        now = _now_ts()
        ttl = max(1.0, float(ttl_seconds or 30.0))
        for key, ts in list(cache.items()):
            if now - _safe_float(ts, 0.0) > ttl:
                cache.pop(key, None)
        try:
            scope = self._event_scope_key(event)
        except Exception:
            scope = _single_line(getattr(event, "unified_msg_origin", ""), 160) or "unknown"
        signature = hashlib.sha1(
            f"{scope}|daily_outfit_photo|{text}|{image_path}".encode("utf-8", errors="ignore")
        ).hexdigest()[:20]
        last_at = _safe_float(cache.get(signature), 0.0)
        if last_at and now - last_at <= ttl:
            logger.info(
                "[PrivateCompanion] 已跳过重复的每日穿搭命令发图: scope=%s image=%s age=%.1fs",
                _single_line(scope, 120),
                _single_line(image_path, 160),
                now - last_at,
            )
            return True
        cache[signature] = now
        return False

    @filter.command("陪伴", alias={"私聊陪伴", "主动陪伴"})
    @_multi_persona_event_context
    async def companion_command(self, event: AstrMessageEvent):
        """管理私聊陪伴状态、日程、记忆、风格、重要日期和可选外部动作。"""
        if self is None:
            return
        try:
            is_private = bool(event.is_private_chat())
        except Exception:
            is_private = False
        raw_command_text = str(getattr(event, "message_str", "") or "")
        # Some adapters (notably QQ official) preserve the slash while others
        # strip the registered command token before invoking the handler. Keep
        # both forms equivalent so bootstrap commands do not fall back to help.
        command_text = raw_command_text.replace("\u3000", " ").replace("／", "/").strip()
        if command_text.startswith("/"):
            command_text = command_text[1:].lstrip()
        bootstrap_args = command_text.split(maxsplit=2)
        bootstrap_action = bootstrap_args[1].strip() if len(bootstrap_args) >= 2 else ""
        bootstrap_value = bootstrap_args[2].strip() if len(bootstrap_args) >= 3 else ""
        if len(bootstrap_args) == 1 and bootstrap_args[0] in {
            "绑定主动消息", "绑定主动会话", "绑定会话",
            "查看主动路由", "查看主动绑定", "主动路由", "主动绑定",
            "解绑主动消息", "解绑主动会话", "解绑会话",
        }:
            bootstrap_action = bootstrap_args[0]
        bootstrap_normalizer = getattr(self, "_normalize_companion_command_action", None)
        if callable(bootstrap_normalizer):
            bootstrap_action, _ = bootstrap_normalizer(
                bootstrap_action,
                bootstrap_value,
            )
        private_delivery_bind_actions = {"绑定主动消息", "绑定主动会话", "绑定会话"}
        is_private_delivery_bootstrap = bootstrap_action in private_delivery_bind_actions
        if is_private:
            raw_user_id = str(event.get_sender_id() or "").strip()
            identity_normalizer = getattr(self, "_normalize_private_identity_id", None)
            user_id = identity_normalizer(raw_user_id) if callable(identity_normalizer) else raw_user_id
            user_id = user_id or raw_user_id
            sender_name_reader = getattr(self, "_sender_display_name", None)
            if callable(sender_name_reader):
                sender_display_name = _single_line(sender_name_reader(event), 40)
            else:
                sender_display_name = _single_line(user_id, 40)
            async with self._data_lock:
                private_user, _ = self._ensure_auto_private_user_profile(
                    event,
                    user_id=user_id,
                    sender_display_name=sender_display_name,
                    now=_now_ts(),
                )
                if isinstance(private_user, dict):
                    user_id = _single_line(private_user.get("user_id"), 160) or user_id
                migrator = getattr(self, "_req036_migrate_configured_target_capability", None)
                if callable(migrator):
                    migrator(user_id, private_user)
                self._req036_attach_unified_profile_context(
                    event,
                    user=private_user if isinstance(private_user, dict) else None,
                    source="private_command",
                )
                self._schedule_data_save(sections={"users", "unified_person"})
        self._qzone_note_event_bot(event)
        raw_text = str(event.message_str or "")
        normalized_text = raw_text.replace("\u3000", " ").replace("／", "/").strip()
        if normalized_text.startswith("/"):
            normalized_text = normalized_text[1:].lstrip()
        args = normalized_text.split(maxsplit=2)
        action = args[1].strip() if len(args) >= 2 else "帮助"
        value = args[2].strip() if len(args) >= 3 else ""
        if len(args) == 1 and args[0] in {
            "绑定主动消息", "绑定主动会话", "绑定会话",
            "查看主动路由", "查看主动绑定", "主动路由", "主动绑定",
            "解绑主动消息", "解绑主动会话", "解绑会话",
        }:
            action, value = args[0], ""
        action, value = self._normalize_companion_command_action(action, value)
        companion_manual_query_actions = {"答疑", "排障", "诊断", "说明"}
        companion_manual_confirm_actions = {"答疑确认", "排障确认", "诊断确认", "应用答疑建议", "应用建议"}
        companion_manual_cancel_actions = {"答疑取消", "排障取消", "诊断取消", "取消答疑建议", "取消建议"}
        companion_manual_setting_actions = {"答疑设置", "排障设置", "诊断设置", "答疑修改", "排障修改", "诊断修改"}
        daily_outfit_view_actions = {"今日穿搭图", "今日穿搭", "查看穿搭图", "查看穿搭", "穿搭图", "每日穿搭图", "每日穿搭", "当前穿搭图", "当前穿搭", "展示穿搭图"}
        daily_outfit_generate_actions = {
            "生成穿搭", "刷新穿搭", "重置穿搭",
            "生成穿搭图", "刷新穿搭图", "重置穿搭图",
            "重新生成穿搭", "重新生成穿搭图", "重生穿搭", "重生穿搭图",
            "生成今日穿搭", "生成今日穿搭图", "生成每日穿搭", "生成每日穿搭图",
        }
        photo_command_actions = {"生图", "画图", "绘图", "生成图片", "出图", "自拍", "拍照", "拍一张", "改图", "修图", "重绘", "P图", "p图"}
        daily_schedule_regenerate_actions = {"重置日程", "生成日程", "刷新日程", "重新生成日程"}
        daily_schedule_cancel_actions = {"删除日程", "取消日程", "移除日程"}
        image_api_status_actions = {"查看生图API", "查看生图api", "生图API状态", "生图api状态", "在线生图API", "在线生图api", "生图接口"}
        image_api_swap_actions = {
            "切换生图API", "切换生图api", "交换生图API", "交换生图api",
            "切换在线生图API", "切换在线生图api", "交换在线生图API", "交换在线生图api",
            "切换图片API", "切换图片api", "交换图片API", "交换图片api",
            "切换备用生图", "启用备用生图", "使用备用生图", "切到备用生图",
            "切换备选生图", "启用备选生图", "使用备选生图", "切到备选生图",
        }
        qweather_location_bind_actions = {"绑定城市", "设置城市"}
        qweather_location_view_actions = {"查看城市", "当前城市", "天气城市"}
        qweather_location_unbind_actions = {"解绑城市", "清除城市"}
        qweather_location_actions = {
            *qweather_location_bind_actions,
            *qweather_location_view_actions,
            *qweather_location_unbind_actions,
        }
        wakeup_alarm_actions = {"现实触及", "现实触及闹钟", "现实触及起床", "起床闹钟", "起床提醒", "蓝牙起床", "蓝牙闹钟"}
        private_delivery_view_actions = {"查看主动路由", "查看主动绑定", "主动路由", "主动绑定"}
        private_delivery_unbind_actions = {"解绑主动消息", "解绑主动会话", "解绑会话"}
        private_delivery_actions = {
            *private_delivery_bind_actions,
            *private_delivery_view_actions,
            *private_delivery_unbind_actions,
        }
        tts_language_actions = {"TTS语种", "tts语种", "语音语种", "TTS", "tts"}
        if action in companion_manual_query_actions:
            inline_value = value.strip()
            if inline_value in {"确认", "应用", "执行", "确认执行", "应用建议"}:
                action = "答疑确认"
                value = ""
            elif inline_value in {"取消", "取消建议", "放弃"}:
                action = "答疑取消"
                value = ""
            else:
                inline_parts = inline_value.split(maxsplit=1)
                if len(inline_parts) >= 2 and inline_parts[0] in {"设置", "修改", "set", "Set", "SET"}:
                    action = "答疑设置"
                    value = inline_parts[1].strip()
                elif re.search(r"^[A-Za-z_][A-Za-z0-9_]*\s*(?:=|:|：|设为|设置为|改成|调到)\s*\S+", inline_value):
                    action = "答疑设置"
                    value = inline_value
                else:
                    maybe_key, maybe_value = self._companion_manual_parse_setting_text(inline_value)
                    if maybe_key and maybe_value:
                        ok, _, _ = self._companion_manual_normalize_config_value(maybe_key, maybe_value)
                        if ok:
                            action = "答疑设置"
                            value = inline_value
        bookshelf_password_reset_actions = {
            "重置夹层密码", "重设夹层密码", "重新生成夹层密码", "刷新夹层密码", "生成夹层密码",
            "重置资料柜密码", "重设资料柜密码", "重新生成资料柜密码", "刷新资料柜密码", "生成资料柜密码",
        }
        bookshelf_password_output_actions = {
            "输出夹层密码", "强制输出夹层密码", "查看夹层密码", "显示夹层密码",
            "输出资料柜密码", "强制输出资料柜密码", "查看资料柜密码", "显示资料柜密码",
            "输出抽屉密码", "查看抽屉密码", "显示抽屉密码",
        }
        bookshelf_password_value_actions = {"强制输出", "输出", "查看密码", "查看", "显示"}
        bookshelf_password_value_targets = {"夹层密码", "资料柜密码", "抽屉密码", "资料柜暗格", "夹层", "资料柜"}
        bookshelf_password_output_requested = (
            action in bookshelf_password_output_actions
            or (
                action in bookshelf_password_value_actions
                and _single_line(value, 24) in bookshelf_password_value_targets
            )
        )
        response_image_path = ""
        response_extra_components: list[Any] = []
        deferred_actions = {
            "重置当前人格", "当前人格重置", "重置人格",
            "重置插件", "全部重置",
            "查看提示词", "提示词", "prompt",
            "重置细化",
            *daily_schedule_regenerate_actions,
            *daily_schedule_cancel_actions,
            *daily_outfit_generate_actions,
            "生成状态", "刷新状态", "重生状态",
            "增添状态", "添加状态",
            "生成日记", "刷新日记",
            "梦境", "做了什么梦", "今日梦境",
            *bookshelf_password_reset_actions,
            "发说说", "发QQ空间", "发布说说", "空间发布", "发布空间",
            "测试说说链路", "测试空间发布", "测试QQ空间发布", "测试qzone发布",
            "测试说说配图", "测试空间配图", "测试QQ空间配图", "测试qzone配图",
            "新闻", "今日新闻", "AI新闻", "ai新闻", "AI日报", "ai日报", "日报", "AI早报", "ai早报", "早报",
            *companion_manual_query_actions,
            *photo_command_actions,
            *image_api_swap_actions,
            *qweather_location_actions,
        }

        is_private = bool(getattr(event, "is_private_chat", lambda: False)())
        public_safe_actions = {
            *companion_manual_query_actions,
            *companion_manual_confirm_actions,
            *companion_manual_cancel_actions,
            *companion_manual_setting_actions,
            *daily_outfit_view_actions,
            *tts_language_actions,
            *wakeup_alarm_actions,
        }
        if action in private_delivery_actions and not is_private:
            await self._reply(event, "请在需要接收主动消息的私聊窗口执行这个指令。")
            event.stop_event()
            return
        if action in wakeup_alarm_actions and not is_private:
            await self._reply(event, "现实触及只在私聊窗口设置，避免群聊误触发本机播放。")
            event.stop_event()
            return
        if action in qweather_location_actions and not self._can_manage_sensitive_location(event):
            await self._reply(event, self._sensitive_location_denied_text())
            event.stop_event()
            return
        if runtime_persona_setting(self, 'require_private_opt_in', True) and not is_private and action not in public_safe_actions:
            await self._reply(event, self._private_only_text())
            event.stop_event()
            return

        management_actions = {
            "重置当前人格", "当前人格重置", "重置人格",
            "重置插件", "全部重置",
            "查看提示词", "提示词", "prompt",
            "重置细化", *daily_schedule_regenerate_actions, *daily_schedule_cancel_actions,
            *daily_outfit_generate_actions,
            "生成状态", "刷新状态", "重生状态",
            "增添状态", "添加状态",
            "生成日记", "刷新日记",
            *bookshelf_password_reset_actions,
            *bookshelf_password_output_actions,
            "发说说", "发QQ空间", "发布说说", "空间发布", "发布空间",
            "测试说说链路", "测试空间发布", "测试QQ空间发布", "测试qzone发布",
            "测试说说配图", "测试空间配图", "测试QQ空间配图", "测试qzone配图",
            "新闻", "今日新闻", "AI新闻", "ai新闻", "AI日报", "ai日报", "日报", "AI早报", "ai早报", "早报",
            *tts_language_actions,
            "撤回消息", "防撤回", "转述撤回", "撤回转述",
            "日期添加", "添加日期", "重要日期添加",
            "日期删除", "删除日期", "重要日期删除",
            "话头删除", "删除话头", "未完话头删除", "删除未完话头",
            "清空记忆", "忘记我",
            "参考图", "人设参考图", "自拍参考图", "参考图库",
            *image_api_status_actions,
            *image_api_swap_actions,
            *qweather_location_actions,
        }
        if (action in management_actions or bookshelf_password_output_requested) and not self._can_manage_private_companion(event):
            await self._reply(event, self._management_denied_text())
            event.stop_event()
            return

        raw_user_id = str(event.get_sender_id() or "").strip()
        resolver = getattr(self, "_private_user_id_for_event", None)
        canonicalizer = getattr(self, "_canonical_private_user_id", None)
        identity_normalizer = getattr(self, "_normalize_private_identity_id", None)
        fallback_user_id = (
            identity_normalizer(raw_user_id)
            if callable(identity_normalizer)
            else raw_user_id
        ) or raw_user_id
        user_id = (
            resolver(event, raw_user_id)
            if callable(resolver)
            else canonicalizer(fallback_user_id) if callable(canonicalizer) else fallback_user_id
        )
        user_id = _single_line(user_id, 160) or raw_user_id
        wakeup_test_requested: Any = False
        async with self._data_lock:
            user = self._get_user(user_id)
            stamper = getattr(self, "_stamp_private_event_identity", None)
            if is_private and callable(stamper):
                stamper(user, event, raw_user_id)
            self._note_private_user_umo(user_id, user, event.unified_msg_origin)

            if action in private_delivery_bind_actions:
                changed, response = self._bind_private_delivery_umo(user_id, user, event.unified_msg_origin)
                if changed:
                    self._save_data_sync(sections={"users"})
            elif action in private_delivery_view_actions:
                response = self._format_private_delivery_binding_status(user_id, user)
            elif action in private_delivery_unbind_actions:
                changed, response = self._unbind_private_delivery_umo(user)
                if changed:
                    self._save_data_sync(sections={"users"})
            elif action in wakeup_alarm_actions:
                camera_command_requested = bool(
                    re.sub(r"\s+", "", str(value or "")).lower().startswith(
                        ("摄像头", "确认摄像头", "读取摄像头", "测试摄像头", "撤销摄像头", "取消摄像头")
                    )
                )
                if camera_command_requested and not self._reality_touch_camera_user_eligible(user_id):
                    response = "主机摄像头只允许 AstrBot 管理员或主要用户本人授权和使用。"
                    wakeup_test_requested = False
                else:
                    response, wakeup_test_requested = self._wakeup_alarm_command(user, value)
                enabled_getter = getattr(self, "_reality_companion_enabled", None)
                feature_enabled = bool(callable(enabled_getter) and enabled_getter())
                if not feature_enabled:
                    wakeup_test_requested = False
                    response += "\n现实触及联动插件未启用，请在“我会来到你身边”配置中开启总开关。"
            elif action in {"状态", "status"}:
                self._reset_daily_counter_if_needed(user)
                last_seen = self._format_timestamp_elapsed(self._latest_user_activity_ts(user))
                last_sent = self._format_timestamp_elapsed(user.get("last_sent"))
                plan = self.data.get("daily_plan", {})
                plan_text = self._format_plan_status_summary(plan if isinstance(plan, dict) else {})
                state = self.data.get("daily_state", {})
                state_text = (
                    f"{state.get('date')}｜能量 {state.get('energy', 70)}/100｜情绪偏{state.get('mood_bias', '平稳')}"
                    if state else "未生成"
                )
                simulation_text = self._format_simulation_summary(user)
                response = "".join(
                    [
                        "运行模式：默认开启\n",
                        f"称呼：{user.get('nickname') or runtime_persona_setting(self, 'default_nickname', '你')}\n",
                        f"语气：{user.get('style') or runtime_persona_setting(self, 'default_style', '温柔')}\n",
                        f"日程：{plan_text}\n",
                        f"拟人状态：{state_text}\n",
                        f"关系角色：{self._private_user_role_label(self._private_user_role(user, user_id))}\n",
                        f"今日主动消息：{user.get('sent_today', 0)}/{self._effective_user_daily_limit(user)}\n",
                        f"今日软目标：约 {self._soft_daily_target(user):.1f} 条\n",
                        f"免打扰：{runtime_persona_setting(self, 'quiet_hours', '23:00-08:30')}\n",
                        f"上次活跃：{last_seen}\n",
                        f"上次主动：{last_sent}\n",
                        f"下次候选：{self._format_next_proactive(user)}\n",
                        f"{simulation_text}\n" if simulation_text else "",
                        f"{self._format_suspended_summary(user)}\n",
                        f"主动方式承接：{self._format_action_affinity_summary(user)}\n",
                        f"关系：{self._format_relationship_summary(user)}",
                    ]
                )
            elif action in {"撤回消息", "防撤回", "转述撤回", "撤回转述"}:
                if not runtime_persona_setting(self, 'enable_recall_enhancement', True) or not runtime_persona_setting(self, 'enable_recall_transcribe_command', True):
                    response = "撤回消息转述没有开启。"
                else:
                    response = self._format_recalled_messages_for_event(event, limit=5)
                    response_extra_components = self._recalled_message_media_components_for_event(event, limit=5)
            elif action in tts_language_actions:
                tts_value = value
                if action in {"TTS", "tts"}:
                    tts_parts = value.split(maxsplit=1)
                    if tts_parts and tts_parts[0].strip().lower() in {"语种", "语言", "language", "lang"}:
                        tts_value = tts_parts[1].strip() if len(tts_parts) >= 2 else ""
                response = self._set_tts_voice_language_from_command(tts_value)
            elif action in companion_manual_confirm_actions:
                response = await self._companion_manual_apply_pending_config(event)
            elif action in companion_manual_cancel_actions:
                response = self._companion_manual_cancel_pending_config(event)
            elif action in companion_manual_setting_actions:
                response = await self._companion_manual_apply_setting_command(event, value)
            elif action in companion_manual_query_actions:
                response = "正在结合说明书和当前运行状态做诊断。"
            elif action in {"参考图", "人设参考图", "自拍参考图"}:
                response, response_image_path = await self._photo_reference_command_payload(event, user_id, value)
            elif action == "参考图库":
                response, response_image_path = await self._photo_reference_library_command_payload(event, user_id, value)
            elif action in daily_outfit_view_actions:
                response, response_image_path = self._daily_outfit_command_payload()
            elif action in image_api_status_actions:
                response = self._image_api_command_status_text()
            elif action in image_api_swap_actions:
                response = "正在交换在线生图 API 优先级。"
            elif action in qweather_location_actions:
                response = "正在处理天气城市设置。"
            elif action in photo_command_actions:
                response = "正在准备图片。"
            elif action in {"查看主动判定", "主动判定", "判定"}:
                response = self._explain_proactive_decision(user)
            elif action in {"能力列表", "主动能力", "工具列表"}:
                response = self._format_proactive_ability_list_for_user(user)
            elif action in {"重置当前人格", "当前人格重置", "重置人格"}:
                response = "正在备份并重置当前人格资料，插件基础配置和窗口绑定会保留。"
            elif action in {"重置插件", "全部重置"}:
                response = "正在清空插件状态,并重新生成今天的状态和日程。"
            elif action == "重置":
                response = "请明确要重置的对象，例如“陪伴 重置 日程”“陪伴 重置 细化”或“陪伴 重置 插件”。"
            elif action in {"查看提示词", "提示词", "prompt"}:
                response = "正在整理当前这层提示词。"
            elif action in {"重置细化"}:
                response = "正在生成当前时间段细化。"
            elif action in {"增添状态", "添加状态"}:
                response = "正在把这个状态加进去。"
            elif action in {"当前细化", "查看当前细化"}:
                response = self._format_current_detail_view()
            elif action in {"查看今日日程", "查看日程", "今日日程", "日程"}:
                plan = self.data.get("daily_plan", {})
                response = self._format_daily_plan(plan)
            elif action in daily_schedule_regenerate_actions:
                response = (
                    "正在重新细化指定的日程段。"
                    if value
                    else "正在生成今天的日程,我先把今天怎么过想清楚。"
                )
            elif action in daily_schedule_cancel_actions:
                response = "正在取消指定的日程段。"
            elif action in daily_outfit_generate_actions:
                response = "正在按今日日程生成每日穿搭照片。"
            elif action in {"生成状态", "刷新状态", "重生状态"}:
                response = "正在刷新今天的拟人状态。"
            elif action in {"梦境", "做了什么梦", "今日梦境"}:
                state = self.data.get("daily_state", {})
                response = self._format_dream_view(state if isinstance(state, dict) else {})
            elif action in {"梦境碎片", "梦碎片", "碎片梦境"}:
                response = self._format_dream_fragment_pool_view()
            elif action in {"画像", "关系", "回复率"}:
                response = self._format_user_profile(user)
            elif action in {"记忆", "陪伴记忆"}:
                response = "当前本地陪伴画像：\n" + self._format_companion_memory_for_prompt(user)
            elif action in {"表达学习", "说话风格", "口癖"}:
                response = "当前表达节奏学习：\n" + self._format_expression_profile_for_prompt(user)
            elif action in {"气氛", "意图", "关系状态"}:
                response = "当前气氛判断：\n" + (self._format_intent_relationship_injection(user) or "暂无样本。")
            elif action in {"片段", "对话片段", "共同经历", "未完成"}:
                episode_text = self._format_dialogue_episodes_for_prompt(user) or "暂无对话片段记忆。"
                loop_text = self._format_open_loops_for_prompt(user) or "暂无未完成约定。"
                response = f"当前对话片段：\n{episode_text}\n\n未完话头：\n{loop_text}"
            elif action in {"话头删除", "删除话头", "未完话头删除", "删除未完话头"}:
                memory_managed = self._req041_private_memory_managed()
                memory_revision = (
                    self._req041_prepare_authoritative_private_memory(user)
                    if memory_managed else None
                )
                if memory_managed and memory_revision is None:
                    response = "权威私聊记忆暂不可写，请稍后重试。"
                else:
                    response = self._remove_open_loop_entry(user, value)
                    committed = not memory_managed or self._req041_commit_authoritative_private_memory(
                        user,
                        expected_revision=memory_revision,
                        operation_id="req041-command-open-loop:" + uuid.uuid4().hex,
                    )
                    if committed:
                        save_sections = {"users"}
                        if memory_managed:
                            save_sections.add("_req041_private_memory")
                        self._save_data_sync(sections=save_sections)
                    else:
                        response = "记忆已发生并发变更，请重试。"
            elif action in {"长期记忆", "livingmemory", "lmem", "向量记忆"}:
                response = self._format_livingmemory_status()
            elif action in {"日记", "bot日记", "小记"}:
                response = self._format_diaries()
            elif action in {"资料柜密码", "夹层密码", "抽屉密码", "资料柜暗格"}:
                response = "这个要直接问我本人。她会不会说、怎么说,要看当时的人格和心情。"
            elif bookshelf_password_output_requested:
                password = await self._ensure_bookshelf_password_async()
                password_reason = await self._ensure_bookshelf_password_reason_async(password)
                secret = self.data.get("bookshelf_secret", {}) if isinstance(self.data.get("bookshelf_secret"), dict) else {}
                response = (
                    "当前资料柜夹层密码：\n"
                    f"{password}\n"
                    f"生成方式：{_single_line(secret.get('basis'), 40) or '未知'}\n"
                    f"理由：{password_reason or '这是一枚资料柜夹层里的私密暗号。'}"
                )
            elif action in bookshelf_password_reset_actions:
                secret = self.data.setdefault("bookshelf_secret", {})
                if not isinstance(secret, dict):
                    secret = {}
                    self.data["bookshelf_secret"] = secret
                secret.pop("password", None)
                # Changing the secret also revokes any browser session issued for
                # the previous password; a fresh unlock should be required.
                secret.pop("web_access", None)
                runtime_access = getattr(self, "_bookshelf_access_tokens", None)
                if isinstance(runtime_access, dict):
                    runtime_access.clear()
                secret["reset_at"] = _now_ts()
                await self._ensure_bookshelf_password_async()
                self._save_data_sync(sections={"bookshelf_secret"})
                response = "已重新设置资料柜夹层密码。需要查看真实密码可用：陪伴 输出夹层密码"
            elif action in {"发说说", "发QQ空间", "发布说说", "空间发布", "发布空间"}:
                response = "正在发布 QQ 空间说说。"
            elif action in {"测试说说链路", "测试空间发布", "测试QQ空间发布", "测试qzone发布"}:
                response = "正在模拟 QQ 空间发布链路。"
            elif action in {"测试说说配图", "测试空间配图", "测试QQ空间配图", "测试qzone配图"}:
                response = "正在测试 QQ 空间配图生成链路。"
            elif action in {"AI日报", "ai日报", "日报", "AI早报", "ai早报", "早报"}:
                response = "我先看看最近的 AI 日报记录。"
            elif action in {"新闻", "今日新闻", "AI新闻", "ai新闻"}:
                response = "正在读今天的新闻源。"
            elif action in {"生成日记", "刷新日记"}:
                response = "正在写今天的日记。"
            elif action in {"日期列表", "重要日期", "日期"}:
                response = self._format_important_dates()
            elif action in {"日期添加", "添加日期", "重要日期添加"}:
                ok, response = self._add_important_date_entry(value)
                if ok:
                    self._save_data_sync(sections={"important_dates"})
            elif action in {"日期删除", "删除日期", "重要日期删除"}:
                response = self._remove_important_date_entry(value)
                self._save_data_sync(sections={"important_dates"})
            elif action in {"可做事项", "能做什么"}:
                items = self.data.get("can_do", [])
                if items:
                    response = "我现在可以安排进日程的事：\n" + "\n".join(f"- {_single_line(item, 80)}" for item in items)
                else:
                    response = "还没有可做事项。"
            elif action in {"昵称", "称呼"}:
                if not value:
                    response = "请这样设置：陪伴 昵称 <你喜欢的称呼>"
                else:
                    user["nickname"] = _single_line(value, 24)
                    self._save_data_sync(sections={"users"})
                    response = f"记住了,以后我会叫你：{user['nickname']}"
            elif action in {"语气", "风格"}:
                style_value = _single_line(value, 24)
                if not style_value:
                    response = "请这样设置：陪伴 语气 <简短语气描述>"
                else:
                    user["style"] = style_value
                    self._save_data_sync(sections={"users"})
                    response = f"语气偏好已记录：{style_value}"
            elif action in {"清空记忆", "忘记我"}:
                self.data.setdefault("users", {}).pop(user_id, None)
                self._save_data_sync(sections={"users"})
                response = "已清空你的陪伴设置和轻量记忆。"
            else:
                response = self._help_text()

        if action not in deferred_actions:
            await self._reply_with_optional_media(
                event,
                response,
                response_image_path,
                extra_components=response_extra_components,
            )
        if (
            action in wakeup_alarm_actions
            and isinstance(wakeup_test_requested, dict)
            and wakeup_test_requested.get("camera_snapshot")
        ):
            camera_snapshotter = getattr(self, "_reality_touch_camera_snapshot_for_user", None)
            try:
                result = (
                    await camera_snapshotter(user_id, wakeup_test_requested.get("purpose"))
                    if callable(camera_snapshotter)
                    else {"status": "unavailable", "message": "当前插件实例没有摄像头单帧能力"}
                )
            except Exception as exc:
                logger.warning(
                    "[PrivateCompanion] 摄像头单帧读取异常: %s",
                    _single_line(exc, 160),
                )
                result = {"status": "error", "message": "摄像头单帧读取失败，请稍后再试或检查设备连接。"}
            observation = result.get("observation") if isinstance(result.get("observation"), dict) else {}
            detail = _single_line(observation.get("summary"), 180)
            await self._reply(
                event,
                ("单帧读取完成：" + detail) if result.get("status") == "success" and detail else _single_line(result.get("message"), 200),
            )
            event.stop_event()
            return
        if action in wakeup_alarm_actions and wakeup_test_requested:
            self._create_lifecycle_background_task(
                self._test_wakeup_alarm(user),
                label="wakeup_alarm_test",
            )
            event.stop_event()
            return
        if action in companion_manual_query_actions:
            await self._reply(event, await self._companion_manual_answer(event, value))
            event.stop_event()
            return
        if action in qweather_location_actions:
            await self._reply(event, await self._qweather_location_command_text(action, value))
            event.stop_event()
            return
        if action in photo_command_actions:
            await self._handle_companion_photo_command(event, user_id, action, value)
            return
        if action in image_api_swap_actions:
            force_swap = bool(re.search(r"(?:强制|force|确认|直接)", value, flags=re.I))
            await self._reply(event, await self._swap_external_image_api_command_text(force=force_swap))
            event.stop_event()
            return
        if action in {"发说说", "发QQ空间", "发布说说", "空间发布", "发布空间"}:
            image_sources = await self._qzone_image_sources_from_event(event)
            image_sources, image_select_message = self._qzone_select_image_sources(value, image_sources)
            if image_select_message:
                await self._reply(event, image_select_message)
                event.stop_event()
                return
            publish_text = self._qzone_clean_publish_text(value)
            if image_sources and publish_text in {"[图片]", "【图片】", "图片"}:
                publish_text = ""
            if not publish_text and not image_sources:
                await self._reply(event, "请这样使用：陪伴 发说说 <正文>，也可以随消息附带图片。\n这是公开发布动作，正文或图片不能为空。")
                event.stop_event()
                return
            await self._reply(event, response)
            result = await self._publish_qzone_text(publish_text, event, images=image_sources, auto_generate_image=True)
            if result.get("success"):
                await self._reply(
                    event,
                    "QQ 空间说说已发布。\n"
                    f"QQ：{result.get('uin') or '未知'}\n"
                    f"tid：{result.get('tid') or '未知'}\n"
                    f"正文：{_single_line(result.get('text'), 160) or '无'}\n"
                    f"图片：{len(result.get('images') or [])} 张\n"
                    f"校验：{_single_line(result.get('verify_message'), 120) or ('通过' if result.get('verified') else '未校验')}",
                )
            else:
                await self._reply(event, f"发布失败：{_single_line(result.get('message'), 180)}")
            event.stop_event()
            return
        if action in {"测试说说链路", "测试空间发布", "测试QQ空间发布", "测试qzone发布"}:
            await self._reply(event, response)
            await self._reply(event, await self._test_qzone_publish_tool_chain(event))
            event.stop_event()
            return
        if action in {"测试说说配图", "测试空间配图", "测试QQ空间配图", "测试qzone配图"}:
            await self._reply(event, response)
            await self._reply(event, await self._test_qzone_publish_image_chain(event))
            event.stop_event()
            return
        if action in {"AI日报", "ai日报", "日报", "AI早报", "ai早报", "早报"}:
            await self._reply(event, response)
            await self._maybe_track_ai_daily(force=True)
            await self._reply(event, self._format_ai_daily_digest_for_command())
            event.stop_event()
            return
        if action in {"新闻", "今日新闻", "AI新闻", "ai新闻"}:
            await self._reply(event, response)
            await self._perform_news_reading(reason="user_query", allow_share=False, force=True)
            await self._reply(event, self._format_news_digest_for_command())
            event.stop_event()
            return
        if action in bookshelf_password_reset_actions:
            await self._reply(event, response)
        if action in {"重置当前人格", "当前人格重置", "重置人格"}:
            result = await self._reset_current_persona_store(rebuild_today=True)
            if not result.get("ok"):
                await self._reply(event, result.get("message") or "当前人格重置失败。")
            else:
                persona_label = result.get("persona_id") or "当前单人格资料"
                generation = result.get("generation") or 1
                rebuild_error = _single_line(result.get("rebuild_error"), 180)
                message = (
                    f"当前人格已重置：{persona_label}\n"
                    f"人格资料代次：第 {generation} 代\n"
                    "插件基础配置、多人格列表和窗口绑定均已保留。\n"
                    "重置前资料已保存到 persona_backups。AstrBot 会话历史和外部长期记忆不在本次重置范围内。"
                )
                if rebuild_error:
                    message += f"\n今日状态与日程自动重建失败：{rebuild_error}"
                else:
                    state = result.get("state") if isinstance(result.get("state"), dict) else {}
                    plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
                    if state:
                        message += "\n\n" + self._format_state_detail(state)
                    if plan:
                        message += "\n\n" + self._format_daily_plan(plan)
                await self._reply(event, message)
        if action in {"重置插件", "全部重置"}:
            await self._reset_plugin_store()
            state, plan, _ = await self._rebuild_today_after_reset()
            await self._reply(
                event,
                "插件状态已清空并重建。\n"
                + self._format_state_detail(state)
                + "\n\n"
                + self._format_daily_plan(plan or {}),
            )
        if action in daily_schedule_regenerate_actions:
            if value:
                ok, message, detail = await self._regenerate_daily_plan_segment_by_selector(
                    value,
                    generate_detail_enhancement,
                )
                if ok and isinstance(detail, dict):
                    summary = _single_line(detail.get("summary"), 140)
                    if summary:
                        message = f"{message}\n{summary}"
                await self._reply(event, message)
            else:
                plan = await self._ensure_daily_plan(force=True)
                async with self._data_lock:
                    self.data["detail_enhanced_day"] = str((plan or {}).get("date") or _today_key())
                    self.data["detail_enhanced_segments"] = {}
                    self.data["daily_story_plan"] = {}
                    self._save_data_sync(
                        sections={
                            "daily_plan",
                            "daily_story_plan",
                            "detail_enhanced_day",
                            "detail_enhanced_segments",
                        }
                    )
                await self._reply(event, self._format_daily_plan(plan or {}))
        if action in daily_schedule_cancel_actions:
            _, message = await self._cancel_daily_plan_segment_by_selector(value)
            await self._reply(event, message)
        if action in daily_outfit_generate_actions:
            outfit_generator = getattr(self, "_ensure_daily_outfit_photo", None)
            outfit_lock = getattr(self, "_daily_outfit_photo_generation_lock", None)
            wait_existing = bool(outfit_lock is not None and outfit_lock.locked())
            if wait_existing:
                await self._reply(event, "穿搭图已经在生成中了，我等这轮结果出来直接发给你。")
                outfit = await outfit_generator(force=False) if callable(outfit_generator) else None
            else:
                await self._reply(event, "等我换身衣服哦")
                plan = await self._ensure_daily_plan(force=False)
                if not plan:
                    plan = await self._ensure_daily_plan(force=True)
                outfit = await outfit_generator(force=True) if callable(outfit_generator) else None
            if isinstance(outfit, dict) and outfit.get("path"):
                image_path = _path_text(outfit.get("path"), 1000)
                if not os.path.exists(image_path):
                    await self._reply(event, f"每日穿搭照片未生成：图片文件不存在 {image_path}")
                    event.stop_event()
                    return
                caption = "换好啦，你看"
                if not self._should_skip_recent_outfit_command_send(event, text=caption, image_path=image_path):
                    try:
                        await self._reply_with_optional_media(event, caption, image_path)
                    except Exception as exc:
                        logger.warning(
                            "[PrivateCompanion] 每日穿搭命令发图异常,为避免重复发送已不再兜底补发: image=%s err=%s",
                            _single_line(image_path, 160),
                            _single_line(exc, 180),
                        )
            else:
                error = _single_line((outfit or {}).get("error") if isinstance(outfit, dict) else "", 180)
                note = _single_line((outfit or {}).get("note") if isinstance(outfit, dict) else "", 180)
                await self._reply(event, f"每日穿搭照片未生成：{error or note or '没有可用结果'}")
        if action in {"生成状态", "刷新状态", "重生状态"}:
            state = await self._ensure_daily_state(force=True)
            async with self._data_lock:
                self.data["daily_plan"] = {}
                self._save_data_sync(sections={"daily_plan"})
            await self._reply(
                event,
                self._format_state_detail(state)
                + "\n今天的日程已清空,下次生成日程会按这个状态重新安排。",
            )
        if action in {"增添状态", "添加状态"}:
            ok, message = await self._add_manual_state(value)
            if ok:
                async with self._data_lock:
                    self.data["daily_plan"] = {}
                    state = dict(self.data.get("daily_state", {}))
                    self._save_data_sync(sections={"daily_plan"})
                await self._reply(
                    event,
                    message
                    + "\n"
                    + self._format_state_detail(state)
                    + "\n今天的日程已清空,下次生成日程会按这个状态重新安排。",
                )
            else:
                await self._reply(event, message)
        if action in {"查看提示词", "提示词", "prompt"}:
            prompt_text = await self._debug_prompt_text(value or "主动", user, event)
            await self._reply(event, prompt_text)
        if action in {"重置细化"}:
            plan = await self._ensure_daily_plan(force=False)
            if not plan:
                plan = await self._ensure_daily_plan(force=True)
            ok, message, detail = await self._regenerate_daily_plan_segment_by_selector(
                "当前",
                generate_detail_enhancement,
                reason="用户通过聊天命令重置当前日程细化",
            )
            if ok:
                detail_text = self._format_current_detail_view()
                if detail_text:
                    message = f"{message}\n{detail_text}"
            await self._reply(event, message)
        if action in {"生成日记", "刷新日记"}:
            diary = await self._ensure_daily_diary(force=True)
            await self._reply(event, self._format_single_diary(diary or {}))
        if action in {"梦境", "做了什么梦", "今日梦境"}:
            state = await self._ensure_daily_state(force=False)
            if not state:
                state = await self._ensure_daily_state(force=True)
            await self._reply(event, self._format_dream_view(state or {}))
        event.stop_event()

    @filter.command("陪伴群", alias={"群陪伴", "群聊陪伴"})
    @_multi_persona_event_context
    async def group_companion_command(self, event: AstrMessageEvent):
        """管理群聊陪伴状态、群友画像、群内常见词、话题线程和关系网。"""
        if self is None:
            return
        self._qzone_note_event_bot(event)
        async for result in self._group_companion_command_impl(event):
            yield result

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=220000)
    @_multi_persona_event_context
    @event_data_save_boundary
    async def guard_req036_private_capability_early(self, event: AstrMessageEvent, *args, **kwargs):
        """Reject an unauthorized private event before any normal message plugin runs."""
        if self is None or bool(getattr(event, "private_companion_req036_denied", False)):
            return
        inbound_checker = getattr(self, "_event_is_inbound_chat_message", None)
        if callable(inbound_checker) and not inbound_checker(event):
            logger.debug("[PrivateCompanion] 非入站聊天事件跳过私聊档案预建")
            return
        try:
            user_id = str(event.get_sender_id())
        except Exception:
            user_id = ""
        self_id = self._event_self_id(event)
        if user_id and self_id and user_id == self_id:
            return
        sender_display_name = _single_line(self._sender_display_name(event), 40)
        async with self._data_lock:
            private_user, auto_profile_created = self._ensure_auto_private_user_profile(
                event,
                user_id=user_id,
                sender_display_name=sender_display_name,
                now=_now_ts(),
            )
            if isinstance(private_user, dict):
                user_id = _single_line(private_user.get("user_id"), 160) or user_id
            migrator = getattr(self, "_req036_migrate_configured_target_capability", None)
            migrated = bool(migrator(user_id, private_user)) if callable(migrator) else False
            if migrated:
                self._schedule_data_save(sections={"users"})
        if auto_profile_created:
            logger.info(
                "[PrivateCompanion] 已建立最小用户档案: user=%s platform=%s",
                _single_line(self._canonical_private_user_id(user_id), 80),
                _single_line(self._platform_kind_for_event(event), 40),
            )

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @_multi_persona_event_context
    @event_data_save_boundary(flush=True)
    async def on_private_message(self, event: AstrMessageEvent, *args, **kwargs):
        if await self._handle_private_message_preflight(event):
            return
        return await handle_private_message(self, event, *args, **kwargs)

    async def _handle_private_message_preflight(self, event: AstrMessageEvent) -> bool:
        feedback_handler = getattr(self, "_maybe_handle_wakeup_feedback", None)
        feedback_text = str(getattr(event, "message_str", "") or "")
        is_companion_command = feedback_text.lstrip().startswith(("陪伴", "/陪伴", "私聊陪伴", "主动陪伴"))
        pending_confirmation_handler = getattr(self, "_reality_touch_apply_pending_confirmation", None)
        if callable(pending_confirmation_handler) and not is_companion_command:
            resolver = getattr(self, "_private_user_id_for_event", None)
            user_id = resolver(event) if callable(resolver) else str(event.get_sender_id() or "").strip()
            confirmation_reply = None
            camera_pending = False
            # 锁内只读数据并判定是否为遗留的摄像头待授权；外部插件调用一律
            # 放到锁外，避免拉长全局数据锁的持有时间。
            async with self._data_lock:
                users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
                user = users.get(user_id) if isinstance(users, dict) else None
                if isinstance(user, dict):
                    pending = user.get("reality_touch_pending_consent")
                    camera_pending = isinstance(pending, dict) and pending.get("capability") == self._REALITY_TOUCH_CAMERA_CAPABILITY
            if camera_pending:
                if self._reality_touch_camera_user_eligible(user_id):
                    try:
                        confirmation_reply = pending_confirmation_handler(user, feedback_text)
                    except Exception as exc:
                        logger.warning(
                            "[PrivateCompanion] 现实触及待授权确认处理失败: %s",
                            _single_line(exc, 160),
                        )
                else:
                    async with self._data_lock:
                        users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
                        user = users.get(user_id) if isinstance(users, dict) else None
                        if isinstance(user, dict):
                            user.pop("reality_touch_pending_consent", None)
                            self._save_data_sync(sections={"users"})
                    confirmation_reply = "主机摄像头只允许 AstrBot 管理员或主要用户本人授权和使用。"
            elif isinstance(user, dict) and isinstance(
                user.get("reality_touch_pending_consent"), dict
            ):
                try:
                    confirmation_reply = pending_confirmation_handler(user, feedback_text)
                except Exception as exc:
                    logger.warning(
                        "[PrivateCompanion] 现实触及待授权确认处理失败: %s",
                        _single_line(exc, 160),
                    )
            if confirmation_reply:
                await self._reply(event, confirmation_reply)
                event.stop_event()
                return True
        if callable(feedback_handler) and not is_companion_command:
            raw_user_id = str(event.get_sender_id() or "").strip()
            normalizer = getattr(self, "_canonical_private_user_id", None)
            user_id = normalizer(raw_user_id) if callable(normalizer) else raw_user_id
            users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
            user = users.get(user_id) if isinstance(users, dict) else None
            if isinstance(user, dict) and await feedback_handler(
                event,
                user_id,
                user,
                feedback_text,
            ):
                return True
        return False

    def _record_c3_inbound_activity(
        self,
        event: AstrMessageEvent,
        *,
        text: str,
        received_ts: float,
        user_id: str = "",
        group_id: str = "",
        sender_id: str = "",
        sender_name: str = "",
    ) -> dict[str, Any] | None:
        """Feed the local C3 activity aggregator without changing the reply path."""
        if not _single_line(text, 400):
            return None
        capture = getattr(self, "_agenda_capture_inbound_message", None)
        if not callable(capture):
            return None
        scope = "group" if _single_line(group_id, 80) else "private"
        subject_id = _single_line(group_id or user_id, 120)
        conversation_id = f"{scope}:{subject_id}" if subject_id else scope
        source_ref = _single_line(self._event_message_id(event), 160)
        if not source_ref:
            source_ref = _single_line(getattr(event, "unified_msg_origin", ""), 180)
        if not source_ref:
            source_ref = f"{conversation_id}:{int(received_ts)}"
        try:
            event_time = self._environment_fromtimestamp(received_ts)
        except Exception:
            event_time = datetime.fromtimestamp(received_ts).astimezone()
        try:
            result = capture(
                text=_single_line(text, 400),
                event_time=event_time,
                source_ref=source_ref,
                conversation_id=conversation_id,
                participant=_single_line(sender_name or sender_id or "user", 120),
                message_count=1,
                visibility="group" if scope == "group" else "private",
            )
            if isinstance(result, dict):
                result["scope"] = scope
            if result is not None and scope == "private":
                recorder = getattr(self, "_memory_companion_record_observed_activity", None)
                if callable(recorder):
                    self._create_lifecycle_background_task(
                        recorder(result),
                        label="record_observed_activity",
                    )
            return result
        except Exception as exc:
            logger.debug(
                "[PrivateCompanion] C3 activity capture skipped: scope=%s id=%s error=%s",
                scope,
                subject_id or "-",
                _single_line(exc, 160),
            )
            return None

    async def _capture_group_observation_event(
        self,
        event: AstrMessageEvent,
        *,
        group_id: str,
        sender_id: str,
        sender_name: str,
        text: str,
        scene: dict[str, Any] | None = None,
    ) -> bool:
        async with self._data_lock:
            group = self._get_group(group_id)
            group["umo"] = _single_line(getattr(event, "unified_msg_origin", ""), 160)
            effective_scene = scene or self._infer_group_scene(
                event,
                group,
                sender_id=sender_id,
                sender_name=sender_name,
                text=text,
            )
            captured = self._capture_group_observation_once(
                group,
                sender_id=sender_id,
                sender_name=sender_name,
                text=text,
                group_id=group_id,
                scene=effective_scene,
                message_id=self._event_message_id(event),
                event=event,
            )
            if captured:
                    self._schedule_data_save(sections={"groups"})
        activity_recorder = getattr(self, "_record_c3_inbound_activity", None)
        if callable(activity_recorder):
            activity_recorder(
                event,
                text=text,
                received_ts=_now_ts(),
                group_id=group_id,
                sender_id=sender_id,
                sender_name=sender_name,
            )
        if (
            self._group_role_context_requested(text)
            and not bool(getattr(event, "_private_companion_group_role_refreshed", False))
        ):
            await self._refresh_group_role_snapshot(event, group_id, force=True)
            setattr(event, "_private_companion_group_role_refreshed", True)
        return captured

    def _stop_group_member_safety_event(self, event: AstrMessageEvent) -> None:
        """清空可能已生成的结果，并停止已静默成员的当前群消息。"""
        try:
            event.set_result(self._build_result_from_chain([]))
        except Exception:
            pass
        try:
            event.stop_event()
        except Exception:
            pass
        setattr(event, "_private_companion_member_safety_blocked", True)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=210000)
    @_multi_persona_event_context
    @event_data_save_boundary
    async def guard_blocked_group_member_early(self, event: AstrMessageEvent, *args, **kwargs):
        """在群聊观察和回复插件之前丢弃已静默成员的消息。"""
        if self is None or self._is_onebot_poke_notice_event(event):
            return
        if not self._feature_enabled_or_temp_unlocked("enable_group_companion"):
            return
        if not bool(runtime_persona_setting(self, 'enable_group_member_safety', True)):
            return
        group_id = self._extract_group_id_from_event(event)
        if not group_id or not self._group_enabled_for_event(group_id):
            return
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        if not sender_id or sender_id == self._event_self_id(event):
            return
        async with self._data_lock:
            group = self._get_group(group_id)
            member = self._group_member_safety_member(group, sender_id, create=False)
            if not isinstance(member, dict):
                return
            if not bool(member.get("manual_blocked")) and self._group_member_safety_is_exempt_event(event, sender_id):
                return
            was_blocked = bool(member and (_safe_float(member.get("blocked_at"), 0) > 0 or member.get("manual_blocked")))
            blocked = self._group_member_safety_active(member, expire=True)
            if was_blocked and not blocked:
                self._save_data_sync(sections={"groups"})
        if blocked:
            logger.info(
                "[PrivateCompanion] 已静默群成员消息: group=%s sender=%s",
                group_id,
                sender_id,
            )
            self._stop_group_member_safety_event(event)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=200000)
    @_multi_persona_event_context
    @event_data_save_boundary
    async def capture_group_observation_early(self, event: AstrMessageEvent, *args, **kwargs):
        """Record allowed group messages before reply plugins can stop propagation."""
        if self is None or self._is_onebot_poke_notice_event(event):
            return
        if not self._feature_enabled_or_temp_unlocked("enable_group_companion"):
            return
        group_id = self._extract_group_id_from_event(event)
        if not group_id or not self._group_enabled_for_event(group_id):
            return
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        self_id = self._event_self_id(event)
        if sender_id and self_id and sender_id == self_id:
            return
        text = self._group_observation_event_text(event)
        if not text or text.startswith(("陪伴群", "/陪伴群", "群陪伴", "群聊陪伴")):
            return
        if self._message_debounce_command_text(event, text):
            return
        sender_name = self._sender_display_name(event)
        await self._capture_group_observation_event(
            event,
            group_id=group_id,
            sender_id=sender_id,
            sender_name=sender_name,
            text=text,
        )
        repeat_group_snapshot: dict[str, Any] = {}
        repeat_scene: dict[str, Any] = {}
        async with self._data_lock:
            repeat_group = self._get_group(group_id)
            repeat_group_snapshot = deepcopy(repeat_group)
            repeat_scene = self._infer_group_scene(
                event,
                repeat_group,
                sender_id=sender_id,
                sender_name=sender_name,
                text=text,
            )
            projection_getter = getattr(self, "_req039_group_observation_projection", None)
            observed_user = (
                projection_getter(event, sender_id=sender_id, sender_name=sender_name)
                if callable(projection_getter)
                else None
            )
            self._req036_attach_unified_profile_context(
                event,
                user=observed_user if isinstance(observed_user, dict) else None,
                group_id=group_id,
                source="group_observation",
            )
            self._schedule_data_save(sections={"unified_person"})
        if not self._proactive_only_blocks_passive_event(event, "group_repeat_early"):
            original_repeat_state = deepcopy(repeat_group_snapshot.get("repeat_follow_state", {}))
            original_interject_at = _safe_float(repeat_group_snapshot.get("last_interject_at"), 0)
            await self._maybe_group_interject(
                event,
                repeat_group_snapshot,
                text,
                allow_interjection=False,
                repeat_scene=repeat_scene,
            )
            repeat_state_changed = repeat_group_snapshot.get("repeat_follow_state") != original_repeat_state
            repeat_acted = _safe_float(repeat_group_snapshot.get("last_interject_at"), 0) > original_interject_at
            if repeat_state_changed or repeat_acted:
                async with self._data_lock:
                    current = self._get_group(group_id)
                    current["repeat_follow_state"] = repeat_group_snapshot.get("repeat_follow_state", {})
                    if repeat_acted:
                        current["last_interject_at"] = repeat_group_snapshot.get("last_interject_at", 0)
                        current["interject_day"] = repeat_group_snapshot.get("interject_day", "")
                        current["interject_today"] = repeat_group_snapshot.get("interject_today", 0)
                        current["last_bot_interjection"] = repeat_group_snapshot.get("last_bot_interjection", {})
                        current["recent_bot_replies"] = deepcopy(
                            repeat_group_snapshot.get("recent_bot_replies", current.get("recent_bot_replies", []))
                        )
                    self._save_data_sync(sections={"groups"})
        self._start_group_image_understanding(
            event,
            group_id=group_id,
            sender_id=sender_id,
            text=text,
        )

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=190000)
    @_multi_persona_event_context
    @event_data_save_boundary
    async def review_group_member_safety_early(self, event: AstrMessageEvent, *args, **kwargs):
        """在回复链路前保守审核当前消息，达到阈值时立即静默。"""
        if self is None or self._is_onebot_poke_notice_event(event):
            return
        if bool(getattr(event, "_private_companion_member_safety_blocked", False)):
            return
        if not self._feature_enabled_or_temp_unlocked("enable_group_companion"):
            return
        if not bool(runtime_persona_setting(self, 'enable_group_member_safety', True)):
            return
        if self._group_member_safety_hidden_marker_mode() == "reply_only":
            return
        group_id = self._extract_group_id_from_event(event)
        if not group_id or not self._group_enabled_for_event(group_id):
            return
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        if not sender_id or sender_id == self._event_self_id(event):
            return
        text = self._group_observation_event_text(event)
        if not text or text.startswith(("陪伴群", "/陪伴群", "群陪伴", "群聊陪伴")):
            return
        if self._message_debounce_command_text(event, text):
            return
        result = await self._review_group_member_safety_message(
            event,
            group_id=group_id,
            sender_id=sender_id,
            sender_name=self._sender_display_name(event),
            text=text,
        )
        if result.get("blocked"):
            logger.warning(
                "[PrivateCompanion] 群成员风险次数达到阈值，已静默当前消息: group=%s sender=%s",
                group_id,
                sender_id,
            )
            self._stop_group_member_safety_event(event)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=180000)
    @_multi_persona_event_context
    @event_data_save_boundary
    async def guard_req036_group_portrait_queries(self, event: AstrMessageEvent, *args, **kwargs):
        """Reject third-party portrait probing before any retrieval or LLM hook."""
        if self is None or bool(getattr(event, "_private_companion_member_safety_blocked", False)):
            return
        if not bool(runtime_persona_setting(self, 'enable_group_third_party_portrait_guard', True)):
            return
        group_id = self._extract_group_id_from_event(event)
        if not group_id:
            return
        if not self._req036_group_portrait_query_is_directed(event):
            return
        text = self._group_observation_event_text(event)
        kind = self._req036_group_portrait_query_kind(text)
        if not kind:
            return
        if kind == "bot_self":
            return
        if kind == "third_party":
            logger.info(
                "[PrivateCompanion] 群聊第三方画像查询已拦截: reason=explicit_third_party_query group_hash=%s text_hash=%s text_len=%s",
                hashlib.sha256(str(group_id).encode("utf-8", errors="ignore")).hexdigest()[:12],
                hashlib.sha256(str(text).encode("utf-8", errors="ignore")).hexdigest()[:12],
                len(str(text)),
            )
            event.stop_event()
            await self._reply(event, "这个我不方便替别人整理啦。")
            return
        # An observation-disabled group must not become a wording bypass.  It
        # still does not receive normal group capture; this narrow explicit
        # self-query only prepares a minimal identity/scene reference for the
        # low-sensitivity, same-person Memory request below.
        if not isinstance(getattr(event, "private_companion_unified_profile_context", None), dict):
            try:
                raw_sender_id = str(event.get_sender_id())
                resolver = getattr(self, "_event_private_user_storage_id", None)
                sender_id = (
                    resolver(event, raw_sender_id)
                    if callable(resolver)
                    else self._canonical_private_user_id(raw_sender_id)
                )
            except Exception:
                sender_id = ""
            async with self._data_lock:
                users = self.data.get("users", {}) if isinstance(self.data, dict) else {}
                user = users.get(sender_id) if sender_id and isinstance(users, dict) else None
                self._req036_attach_unified_profile_context(
                    event,
                    user=user if isinstance(user, dict) else None,
                    group_id=group_id,
                    source="group_portrait_query",
                )
                self._schedule_data_save(sections={"users", "unified_person"})
        event.stop_event()
        await self._reply(event, await self._req036_read_group_self_portrait(event))

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @_multi_persona_event_context
    @event_data_save_boundary(flush=True)
    async def on_group_message(self, event: AstrMessageEvent, *args, **kwargs):
        return await handle_group_message(self, event, *args, **kwargs)

    def _format_timestamp_elapsed(self, timestamp: Any) -> str:
        ts = _safe_float(timestamp, 0)
        if ts <= 0:
            return "从未"
        delta = _now_ts() - ts
        if delta < -5:
            seconds = abs(delta)
            if seconds < 60:
                return f"{max(1, int(seconds))} 秒后"
            if seconds < 3600:
                return f"{max(1, int(seconds // 60))} 分钟后"
            if seconds < 86400:
                return f"{max(1, int(seconds // 3600))} 小时后"
            return f"{max(1, int(seconds // 86400))} 天后"
        seconds = max(0, delta)
        return self._format_elapsed(seconds)

    def _format_elapsed(self, seconds: float) -> str:
        if seconds < 5:
            return "刚刚"
        if seconds < 60:
            return f"{int(seconds)} 秒前"
        if seconds < 3600:
            return f"{int(seconds // 60)} 分钟前"
        if seconds < 86400:
            return f"{int(seconds // 3600)} 小时前"
        return f"{int(seconds // 86400)} 天前"
