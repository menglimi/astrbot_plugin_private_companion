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
from typing import Any, Iterable
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse, urlunparse
from xml.etree import ElementTree as ET

from astrbot.api import AstrBotConfig
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
from .main_shared import (
    _ACTIVE_PERSONA_ID,
    _PERSONA_PROFILE_FORBIDDEN_FILENAME_CHARS,
    _PERSONA_SETTING_MANIFEST,
    _PROACTIVE_ONLY_TEMP_UNLOCK_ALIASES,
    _PROACTIVE_ONLY_TEMP_UNLOCK_RELATED,
    _PROACTIVE_ONLY_TEMP_UNLOCK_LABELS,
    _PROACTIVE_ONLY_TEMP_UNLOCK_GROUPS,
    _WINDOWS_RESERVED_FILENAME_STEMS,
    _multi_persona_event_context,
    _plugin_instance_can_dispatch,
    _plugin_instance_root,
    _private_companion_runtime,
    _strip_chain_plain_thinking,
    bookshelf_password_reset_actions,
    companion_manual_query_actions,
    daily_outfit_generate_actions,
    daily_schedule_cancel_actions,
    daily_schedule_regenerate_actions,
    image_api_swap_actions,
    photo_command_actions,
    qweather_location_actions,
    wakeup_alarm_actions,
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
from .domains.affect.reply_temperature import (
    compose_reply_temperature,
    reply_temperature_prompt_section,
)
from .plugin_identity import (
    PLUGIN_ID,
    PLUGIN_VERSION,
    is_module_path_for_package,
)
from .lab_fixture_adapter import register_companion_lab_fixture_adapter
from .companion_interaction_expression import (
    build_expression_decision,
    content_intent_from_text,
    expression_decision_prompt_section,
)
from .photo_reference_catalog import CATALOG_VERSION, load_catalog, validate_and_serialize
from .photo_nai_params import extract_user_photo_nai_params
from .relationship_ledger import normalize_relationship_positive_stage_cap_key
from .relationship_policy import normalize_relationship_stage_policy
from .runtime_config_dispatcher import dispatch_runtime_config_effects
from .companion.injection import (
    PROTOCOL_VERSION,
    ContextContribution,
    ExtensionManifest,
    ExtensionRegistry,
    ExtensionStatus,
    RuntimeScope,
    Scope,
)


from .main_shared import _PHOTO_TOOL_PROMPT_FORMAT_MARKER  # noqa: F401
from .main_core_part02 import PrivateCompanionExtensionAPIPart02Mixin
from .main_core_part01 import PrivateCompanionExtensionAPIPart01Mixin

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
from .wake_message_context import capture_wake_message_context, restore_wake_message_request
from .tool_history_sanitizer import sanitize_history_image_blocks, sanitize_openai_tool_history
from .forward_message import ForwardMessageMixin
from .private_image import PrivateImageMixin
from .conversation_injection_plan import (
    DELIVERY_GROUP_MARKER_METADATA_KEY,
    PLACEMENT_DYNAMIC_SYSTEM,
    PLACEMENT_STABLE_SYSTEM,
    PLACEMENT_TOOL_CONTRACT,
    PLACEMENT_TURN_TAIL,
    get_conversation_injection_plan,
)
from .conversation_prompt_section import (
    ExactText,
    PromptRenderMode,
    PromptSection,
    exact_text,
    prompt_cdata,
    prompt_heading_ref,
    prompt_section,
    render_prompt_content,
    render_prompt_sections,
)
from .hdsi_experiment import (
    apply_hdsi_prompt,
    finalize_trial_response,
    hdsi_window_command,
    mark_hdsi_route,
    record_hdsi_inbound_event,
    record_hdsi_outbound_event,
    record_hdsi_proactive_event,
    record_trial_failure,
)
from .prompt_surface import CollectedPromptContext, PromptSurface
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
from .external_bridge_resolver import invalidate_external_bridge_cache
from .proactive import ProactiveMixin
from .group_wakeup import GroupWakeupMixin
from .group_observation import GroupObservationMixin
from .group_cycle_boundary import (
    build_group_cycle_boundary,
    group_cycle_boundary_prompt_section,
)
from .logging_util import get_module_logger

# ``logger`` must be bound before the optional-module fallbacks below: each
# degradation path reports the missing file through the logger, so leaving the
# binding until later in the module turned a fail-open fallback into a
# NameError that aborted the whole plugin import.
logger = get_module_logger(__name__)
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
        "发布包缺少 group_member_safety.py，群成员风控已停用；插件其余功能继续加载。"
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

    logger.warning("self_timeline.py 缺失，已跳过 Bot 自身时间线注入能力。请重新安装完整版本。")
from .core_store import CoreStoreMixin
from .storage.path_generation import activate_persistence_owner
from .platform_compat import PlatformCompatibilityMixin
from .integration_status import IntegrationStatusMixin
from .astrbot_knowledge import AstrBotKnowledgeMixin
from .atrelay import AtRelayMixin
from .main_tts_response import PrivateCompanionPluginTtsResponseMixin
from .main_companion_command import PrivateCompanionPluginCompanionCommandMixin
from .main_provider_config import PrivateCompanionPluginProviderConfigMixin
from .main_llm_request import PrivateCompanionPluginLlmRequestMixin
from .main_req036_unified_person import PrivateCompanionPluginReq036UnifiedPersonMixin
from .main_persona_profile import PrivateCompanionPluginPersonaProfileMixin
from .main_segmented_reply import PrivateCompanionPluginSegmentedReplyMixin
from .main_private_passive_prompt import PrivateCompanionPluginPrivatePassivePromptMixin
from .main_misc_unassigned import PrivateCompanionPluginMiscUnassignedMixin
from .main_persona_routing_part04 import PrivateCompanionPluginPersonaRoutingPart04Mixin
from .main_util_small import PrivateCompanionPluginUtilSmallMixin
from .main_proactive_only_unlock import PrivateCompanionPluginProactiveOnlyUnlockMixin
from .main_lifecycle import PrivateCompanionPluginLifecycleMixin
from .main_reaction_expression import PrivateCompanionPluginReactionExpressionMixin
from .main_outbound_persistence import PrivateCompanionPluginOutboundPersistenceMixin
from .main_p5_attestation import PrivateCompanionPluginP5AttestationMixin
from .main_pc_llm_tools import PrivateCompanionPluginPcLlmToolsMixin
from .main_sqlite_group_reset import PrivateCompanionPluginSqliteGroupResetMixin
from .main_rest_reply import PrivateCompanionPluginRestReplyMixin
from .main_prompt_formatting import PrivateCompanionPluginPromptFormattingMixin
from .main_atrelay_relay import PrivateCompanionPluginAtrelayRelayMixin
from .main_group_inbound_capture import PrivateCompanionPluginGroupInboundCaptureMixin
from .main_persona_routing import PrivateCompanionPluginPersonaRoutingMixin
from .main_outbound_guard import PrivateCompanionPluginOutboundGuardMixin
from .main_prompt import PrivateCompanionPluginPromptMixin
from .main_req041 import PrivateCompanionPluginReq041Mixin
from .main_external_image_api import PrivateCompanionPluginExternalImageApiMixin
from .main_photo_tool import PrivateCompanionPluginPhotoToolMixin
from .main_debug_version import PrivateCompanionPluginDebugVersionMixin
from .main_token_usage import PrivateCompanionPluginTokenUsageMixin
from .main_body_monitor import PrivateCompanionPluginBodyMonitorMixin
from .main_private_preflight import PrivateCompanionPluginPrivatePreflightMixin
from .main_sensitive_reply import PrivateCompanionPluginSensitiveReplyMixin
from .main_scope_guard import PrivateCompanionPluginScopeGuardMixin
from .proactive_engine import ProactiveEngineMixin
from .proactive_message import ProactiveMessageMixin
from .image_companion_bridge import ImageCompanionBridgeMixin
from .nai_image_bridge import NAIImageBridgeMixin
from .proactive_chat_runtime_bridge import ProactiveChatRuntimeBridge
from .plugin_lifecycle import (
    assemble_plugin_dependencies,
    cancel_registered_host_tasks,
    close_early_resources,
    task_manager,
)
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
from .wardrobe_runtime import WardrobeMixin
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



_private_companion_plugin: Any | None = _private_companion_runtime.active_plugin



def _is_primary_plugin_instance(instance: Any) -> bool:
    return _plugin_instance_root(instance) == PLUGIN_NAME






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


class PrivateCompanionExtensionAPI(PrivateCompanionExtensionAPIPart01Mixin, PrivateCompanionExtensionAPIPart02Mixin):
    """Lightweight integration API for external AstrBot plugins."""

    def __init__(self, plugin: "PrivateCompanionPlugin") -> None:
        self._plugin = plugin
        self._story_migration_generation = uuid.uuid4().hex
        self._story_migration_state = "created"
        self._extension_registry = ExtensionRegistry()
        self._extension_registry.register(
            ExtensionManifest(
                id=PLUGIN_ID,
                version=self._protocol_version(PLUGIN_VERSION),
                sdk_version=PROTOCOL_VERSION,
                display_name="Private Companion",
            )
        )
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



async def _mark_hdsi_inbound(plugin: Any, event: Any) -> None:
    """Record an inbound HDSI route for both private and group entry points.

    The two message handlers need identical bookkeeping, so keep it in one
    place and fail open: a broken HDSI sidecar must never block the normal
    companion reply path.
    """
    mark_hdsi_route(plugin, event)
    try:
        await record_hdsi_inbound_event(plugin, event)
    except Exception:
        return


# 本插件的部分 @filter.* hook 定义在子模块（atrelay / main_outbound_guard / main_prompt 等）里。
# AstrBot 的 get_handlers_by_event_type(only_activated=True) 按 handler_module_path 去
# star_map 反查插件元数据，而 star_map 只登记插件主模块路径，故这些 handler 会被静默跳过。
# 这里统一把它们重绑到主模块路径，使其能通过 only_activated 反查。
from .handler_binding import bind_submodule_handlers as _bind_submodule_handlers  # noqa: E402

_PACKAGE_NAME = __package__ or "astrbot_plugin_private_companion"
for _pkg in {_PACKAGE_NAME, _PACKAGE_NAME.rsplit(".", 1)[0]}:
    try:
        _bind_submodule_handlers(_pkg, f"{_pkg}.main")
    except Exception:
        # 绑定失败不应阻断插件加载。
        pass





private_delivery_bind_actions = {"绑定主动消息", "绑定主动会话", "绑定会话"}

companion_manual_confirm_actions = {"答疑确认", "排障确认", "诊断确认", "应用答疑建议", "应用建议"}

companion_manual_cancel_actions = {"答疑取消", "排障取消", "诊断取消", "取消答疑建议", "取消建议"}

companion_manual_setting_actions = {"答疑设置", "排障设置", "诊断设置", "答疑修改", "排障修改", "诊断修改"}

daily_outfit_view_actions = {"今日穿搭图", "今日穿搭", "查看穿搭图", "查看穿搭", "穿搭图", "每日穿搭图", "每日穿搭", "当前穿搭图", "当前穿搭", "展示穿搭图"}

wardrobe_command_actions = {"衣柜", "衣橱", "wardrobe", "角色衣柜", "服装库"}

image_api_status_actions = {"查看生图API", "查看生图api", "生图API状态", "生图api状态", "在线生图API", "在线生图api", "生图接口"}

private_delivery_view_actions = {"查看主动路由", "查看主动绑定", "主动路由", "主动绑定"}

private_delivery_unbind_actions = {"解绑主动消息", "解绑主动会话", "解绑会话"}

private_delivery_actions = {
    *private_delivery_bind_actions,
    *private_delivery_view_actions,
    *private_delivery_unbind_actions,
}

tts_language_actions = {"TTS语种", "tts语种", "语音语种", "TTS", "tts"}

bookshelf_password_output_actions = {
    "输出夹层密码", "强制输出夹层密码", "查看夹层密码", "显示夹层密码",
    "输出资料柜密码", "强制输出资料柜密码", "查看资料柜密码", "显示资料柜密码",
    "输出抽屉密码", "查看抽屉密码", "显示抽屉密码",
}

bookshelf_password_value_actions = {"强制输出", "输出", "查看密码", "查看", "显示"}

bookshelf_password_value_targets = {"夹层密码", "资料柜密码", "抽屉密码", "资料柜暗格", "夹层", "资料柜"}

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

public_safe_actions = {
    *companion_manual_query_actions,
    *companion_manual_confirm_actions,
    *companion_manual_cancel_actions,
    *companion_manual_setting_actions,
    *daily_outfit_view_actions,
    *tts_language_actions,
    *wakeup_alarm_actions,
}

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
    WardrobeMixin,
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
    PrivateCompanionPluginTtsResponseMixin,
    PrivateCompanionPluginCompanionCommandMixin,
    PrivateCompanionPluginProviderConfigMixin,
    PrivateCompanionPluginLlmRequestMixin,
    PrivateCompanionPluginReq036UnifiedPersonMixin,
    PrivateCompanionPluginPersonaProfileMixin,
    PrivateCompanionPluginPersonaRoutingPart04Mixin,
    PrivateCompanionPluginSegmentedReplyMixin,
    PrivateCompanionPluginPrivatePassivePromptMixin,
    PrivateCompanionPluginMiscUnassignedMixin,
    PrivateCompanionPluginUtilSmallMixin,
    PrivateCompanionPluginProactiveOnlyUnlockMixin,
    PrivateCompanionPluginLifecycleMixin,
    PrivateCompanionPluginReactionExpressionMixin,
    PrivateCompanionPluginOutboundPersistenceMixin,
    PrivateCompanionPluginP5AttestationMixin,
    PrivateCompanionPluginPcLlmToolsMixin,
    PrivateCompanionPluginSqliteGroupResetMixin,
    PrivateCompanionPluginRestReplyMixin,
    PrivateCompanionPluginPromptFormattingMixin,
    PrivateCompanionPluginAtrelayRelayMixin,
    PrivateCompanionPluginGroupInboundCaptureMixin,
    PrivateCompanionPluginPersonaRoutingMixin,
    PrivateCompanionPluginOutboundGuardMixin,
    PrivateCompanionPluginPromptMixin,
    PrivateCompanionPluginReq041Mixin,
    PrivateCompanionPluginExternalImageApiMixin,
    PrivateCompanionPluginPhotoToolMixin,
    PrivateCompanionPluginDebugVersionMixin,
    PrivateCompanionPluginTokenUsageMixin,
    PrivateCompanionPluginBodyMonitorMixin,
    PrivateCompanionPluginPrivatePreflightMixin,
    PrivateCompanionPluginSensitiveReplyMixin,
    PrivateCompanionPluginScopeGuardMixin,
    Star,
):
    # AstrBot registers handlers from their exact defining module.  Keep the
    # implementations in EventDispatchMixin, but expose the decorated entry
    # points here so waiting/request/response form one complete pipeline.
    @filter.command("HDSI", alias={"hdsi"})
    @_multi_persona_event_context
    async def hdsi_experiment_command(self, event: AstrMessageEvent, action: str = "状态"):
        event.stop_event()
        await self._reply(event, await hdsi_window_command(self, event, action))

    @filter.on_llm_request(priority=109000)
    @_multi_persona_event_context
    async def inject_hdsi_experiment_prompt(
        self, event: AstrMessageEvent, req: ProviderRequest, *args: Any, **kwargs: Any,
    ) -> None:
        try:
            await apply_hdsi_prompt(self, event, req)
        except Exception as exc:
            await record_trial_failure(self, event, type(exc).__name__)
            logger.warning("HDSI 表达试验处理失败: error_type=%s", type(exc).__name__)

    @filter.on_llm_response(priority=-99950)
    @_multi_persona_event_context
    async def finalize_hdsi_trial_response(self, event: AstrMessageEvent, resp: LLMResponse, *args: Any, **kwargs: Any) -> None:
        await finalize_trial_response(self, event, resp)
        try:
            await record_hdsi_outbound_event(self, event, resp)
        except Exception:
            # The event bridge is observational and must never alter delivery.
            pass

    # 与 astrbot_plugin_reality_companion 的 capability 契约一致；仅用于识别
    # 拆分前版本遗留在本插件用户数据中的摄像头待授权记录。
    _REALITY_TOUCH_CAMERA_CAPABILITY = "camera_single_frame"

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

    # ------------------------------------------------------------------
    # 手机端陪伴形象：Bot 称呼的唯一数据源在这里，终端只是远程入口
    # ------------------------------------------------------------------

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


    def __init__(self, context: Context, config: AstrBotConfig):
        self._private_companion_instance_guard_enabled = True
        self._private_companion_duplicate_instance = False
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
        assemble_plugin_dependencies(
            self,
            observability_factory=Req041Observability,
        )


    @filter.on_llm_request()
    @_multi_persona_event_context
    async def inject_humanized_state(self, event: AstrMessageEvent, req: ProviderRequest, *args, **kwargs):
        return await run_humanized_state_injection(self, event, req, *args, **kwargs)

    @filter.on_llm_response()
    @_multi_persona_event_context
    async def normalize_tts_enhancement_response(self, event: AstrMessageEvent, resp: LLMResponse, *args, **kwargs):
        """恢复降级为正文的生图调用，并规范化 TTS 标签。"""
        if self is None or not self.enabled:
            return
        original_text, recovered_text = await self._tts_recover_visible_text(event, resp)
        if await self._tts_drop_photo_tool_trailing_text(event, resp, recovered_text):
            return
        recovered_text = await self._tts_apply_reaction_expression_pass(event, resp, recovered_text)
        original_text, recovered_text = self._tts_apply_photo_sentinel_guards(
            event, resp, original_text, recovered_text
        )
        pending_tool_text = str(
            getattr(event, "_private_companion_same_session_tool_text", "") or ""
        ).strip()
        tool_names = getattr(resp, "tools_call_name", None)
        has_tool_call = bool(tool_names) if isinstance(tool_names, (list, tuple, set, str)) else False
        same_session_tool = getattr(self, "_prepare_same_session_send_tool_response", None)
        same_session_tool_call = False
        if callable(same_session_tool):
            try:
                same_session_tool_call, _ = same_session_tool(event, resp)
            except Exception:
                same_session_tool_call = False
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
                "已将同会话工具文本恢复为唯一最终回复: session=%s text=%s",
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
        is_private_delivery_bootstrap = bootstrap_action in private_delivery_bind_actions
        await self._companion_command_bootstrap_private_identity(event, is_private)
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
        action, value = self._companion_manual_inline_action(action, value, companion_manual_query_actions)
        bookshelf_password_output_requested = (
            action in bookshelf_password_output_actions
            or (
                action in bookshelf_password_value_actions
                and _single_line(value, 24) in bookshelf_password_value_targets
            )
        )
        response_image_path = ""
        response_extra_components: list[Any] = []

        is_private = bool(getattr(event, "is_private_chat", lambda: False)())
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

        if (action in management_actions or bookshelf_password_output_requested) and not self._can_manage_private_companion(event):
            await self._reply(event, self._management_denied_text())
            event.stop_event()
            return

        raw_user_id, user_id = self._companion_command_resolve_user_id(event)
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
                response = self._format_companion_status_response(user, user_id)
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
            elif action in wardrobe_command_actions:
                response, response_image_path = await self._wardrobe_command_payload(event, user_id, value)
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
                        fields=("open_loops",),
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
        if await self._companion_command_dispatch_actions(
            event, user, user_id, action, value, wakeup_test_requested
        ):
            return
        if await self._companion_command_qzone_actions(event, response, action, value):
            return
        await self._companion_command_reset_actions(event, response, action, value)
        if await self._companion_command_generate_actions(event, user, action, value):
            return
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

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @_multi_persona_event_context
    @event_data_save_boundary(flush=True)
    async def on_private_message(self, event: AstrMessageEvent, *args, **kwargs):
        await _mark_hdsi_inbound(self, event)
        if await self._handle_private_message_preflight(event):
            return
        return await handle_private_message(self, event, *args, **kwargs)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @_multi_persona_event_context
    @event_data_save_boundary(flush=True)
    async def on_group_message(self, event: AstrMessageEvent, *args, **kwargs):
        await _mark_hdsi_inbound(self, event)
        return await handle_group_message(self, event, *args, **kwargs)
