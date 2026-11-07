"""Read native host objects without model calls, platform sends or storage writes."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aiocqhttp.api_impl import WebSocketReverseApi
from astrbot import __version__ as ASTRBOT_VERSION
from astrbot.core import sp
from astrbot.api.event import AstrMessageEvent
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
from astrbot.core.provider.sources.gemini_source import ProviderGoogleGenAI
from astrbot.core.message.components import Image, Reply, Forward, Record, Video, File
from astrbot.core.db.po import ConversationV2
from sqlmodel import select

from companion_v2.adapters.astrbot.native import (
    NATIVE_LOOKUP_SLOW_SECONDS, HostPersona, NativePrivateBridge, account_number, bounded_native_lookup,
    configured_default_persona_id, native_client_connected, native_user_subject_id, persona_instruction,
    report_slow_native_lookup, supported_platform_name,
)
from companion_v2.adapters.astrbot.provider import looks_like_text_provider, provider_fingerprint
from companion_v2.adapters.astrbot.provider_catalog import TextProviderSettings, assemble_providers
from companion_v2.adapters.astrbot.staging import NativeAdapterTypes
from companion_v2.adapters.astrbot.staging_config import REQUIRED_CAPABILITIES, StagingConfiguration
from companion_v2.execution.models import TaskError, digest
from companion_v2.foundation.refs import ScopeKind, ScopeRef, SubjectKind, SubjectRef

from .settings import HostControlError, HostSettings, INSTALLATION_PREFIX
from .media import load_native_image
from .audio.loader import load_native_audio
from .media_components import group_components, private_components
from .video.loader import load_native_video
from .files.loader import load_native_file
from .video.gemini_provider import GeminiNativeVideoProvider
from .quotes import load_native_quote, load_native_group_quote
from .forwards import load_native_forwards, load_native_group_forwards
from .telegram_media import prepare_telegram_media, telegram_voice_path


HOST_ID = "astrbot-local-staging"
ECOSYSTEM_ID = "companion-v2"
logger = logging.getLogger(__name__)
SESSION_SETTINGS_CACHE_SECONDS = 30.0
SESSION_SETTINGS_LOOKUP_BUDGET_SECONDS = 1.0
_session_settings_cache = {}
_session_settings_reads = {}
_session_settings_versions = {}


def native_text_provider(native_types, provider):
    """Registered text class, or any provider exposing the AstrBot text surface."""
    checker = getattr(native_types, "is_text_provider", None)
    if callable(checker):
        return bool(checker(provider))
    return isinstance(provider, getattr(native_types, "provider", ()) or ()) or looks_like_text_provider(provider)


def _native_video_provider(context, bridge, native_types, native, settings):
    return GeminiNativeVideoProvider(context, bridge, native_types, native, settings=settings)


async def load_session_persona_override(umo):
    """Read the V2-owned persona choice saved for one native window."""
    try:
        services = await asyncio.wait_for(
            load_session_settings(umo), timeout=SESSION_SETTINGS_LOOKUP_BUDGET_SECONDS)
    except TimeoutError as error:
        raise TaskError("host_session_settings_timeout", "native session settings are still loading") from error
    return services.get("persona_id")


async def load_persona_snapshot_selection(context, umo, conversation_id):
    """Read only the selected persona id, never the AstrBot conversation history."""
    if conversation_id is None:
        return None
    database = getattr(context, "_db", None)
    if database is None:
        raise TaskError("host_conversation_unavailable", "AstrBot conversation database is unavailable")
    started = time.perf_counter()
    stop_loop_probe = _start_event_loop_lag_probe()
    session_ready = query_started = query_finished = result_finished = started
    try:
        async with database.get_db() as session:
            session_ready = time.perf_counter()
            query_started = session_ready
            result = await session.execute(select(ConversationV2.persona_id).where(
                ConversationV2.conversation_id == conversation_id))
            query_finished = time.perf_counter()
            row = result.one_or_none()
            result_finished = time.perf_counter()
    finally:
        loop_lag_max_ms, loop_lag_total_ms = stop_loop_probe()
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    if elapsed_ms >= NATIVE_LOOKUP_SLOW_SECONDS * 1000:
        report_slow_native_lookup("conversation", elapsed_ms, details=(
            f"database_ready={getattr(database, 'inited', None)} "
            f"db_context_ms={round((session_ready - started) * 1000)} "
            f"query_ms={round((query_finished - query_started) * 1000)} "
            f"result_ms={round((result_finished - query_finished) * 1000)} "
            f"loop_lag_max_ms={loop_lag_max_ms} loop_lag_total_ms={loop_lag_total_ms}"
        ))
    if row is None:
        raise TaskError("host_conversation_missing", "selected AstrBot conversation is unavailable")
    return row[0]


async def resolve_native_persona_selection(context, umo, conversation_persona_id):
    """Resolve AstrBot's active persona while building a window snapshot."""
    manager = getattr(context, "persona_manager", None)
    resolver = getattr(manager, "resolve_selected_persona", None)
    if not callable(resolver):
        if conversation_persona_id not in (None, ""):
            return conversation_persona_id
        return configured_default_persona_id(context, umo)

    platform_id = umo.partition(":")[0] if type(umo) is str else ""
    platform = context.get_platform_inst(platform_id) if platform_id else None
    meta = platform.meta() if platform is not None else None
    platform_name = getattr(meta, "name", None)
    if type(platform_name) is not str or not platform_name:
        raise TaskError("host_persona_platform_unavailable", "the conversation platform is unavailable")

    configuration = context.get_config(umo=umo)
    provider_settings = configuration.get("provider_settings", {}) if isinstance(configuration, dict) else {}
    if not isinstance(provider_settings, dict):
        provider_settings = {}
    result = resolver(umo=umo, conversation_persona_id=conversation_persona_id, platform_name=platform_name,
                      provider_settings=provider_settings)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, tuple) or len(result) != 4:
        raise TaskError("host_persona_invalid", "AstrBot returned an invalid selected persona")
    selected, _persona, _forced, special = result
    if special:
        return "[%None]"
    if selected in (None, ""):
        if conversation_persona_id not in (None, ""):
            return conversation_persona_id
        return configured_default_persona_id(context, umo)
    return selected


def _start_event_loop_lag_probe(interval=1.0):
    loop = asyncio.get_running_loop()
    next_expected = loop.time() + interval
    maximum_lag = 0.0
    total_lag = 0.0
    handle = None

    def sample():
        nonlocal next_expected, maximum_lag, total_lag, handle
        now = loop.time()
        lag = max(0.0, now - next_expected)
        maximum_lag = max(maximum_lag, lag)
        total_lag += lag
        next_expected = now + interval
        handle = loop.call_later(interval, sample)

    handle = loop.call_later(interval, sample)

    def stop():
        nonlocal maximum_lag, total_lag
        if handle is not None:
            handle.cancel()
        lag = max(0.0, loop.time() - next_expected)
        maximum_lag = max(maximum_lag, lag)
        total_lag += lag
        return round(maximum_lag * 1000), round(total_lag * 1000)

    return stop


async def _read_session_settings(umo, version):
    task = asyncio.current_task()
    try:
        services = await sp.get_async(scope="umo", scope_id=umo, key="session_service_config", default={})
        if services is None:
            services = {}
        if not isinstance(services, dict):
            raise ValueError("native session settings are not an object")
        services = dict(services)
        if version != _session_settings_versions.get(umo, 0):
            cached = _session_settings_cache.get(umo)
            return dict(cached[1]) if cached is not None else services
        _session_settings_cache[umo] = (time.monotonic() + SESSION_SETTINGS_CACHE_SECONDS, services)
        return services
    finally:
        if _session_settings_reads.get(umo) is task:
            _session_settings_reads.pop(umo, None)


async def load_session_settings(umo):
    """Share the native settings read across admission, capture and send checks."""
    if type(umo) is not str or not umo:
        raise ValueError("native session settings need a unified message origin")
    cached = _session_settings_cache.get(umo)
    now = time.monotonic()
    if cached is not None:
        if cached[0] > now:
            return dict(cached[1])
        task = _session_settings_reads.get(umo)
        if task is None or task.done():
            version = _session_settings_versions.get(umo, 0)
            task = asyncio.create_task(_read_session_settings(umo, version),
                                       name="companion-v2-session-settings-refresh")
            _session_settings_reads[umo] = task
            task.add_done_callback(lambda completed: _report_session_settings_refresh(umo, completed))
        return dict(cached[1])
    task = _session_settings_reads.get(umo)
    if task is None or task.done():
        version = _session_settings_versions.get(umo, 0)
        task = asyncio.create_task(_read_session_settings(umo, version),
                                   name="companion-v2-session-settings")
        _session_settings_reads[umo] = task
    return dict(await asyncio.shield(task))


def _report_session_settings_refresh(umo, task):
    if task.cancelled():
        return
    try:
        task.result()
    except Exception as error:
        logger.warning("[CompanionV2] 会话设置后台刷新失败 error_type=%s", type(error).__name__)


async def warm_session_settings():
    """Load saved native-window settings while the host is starting."""
    versions = dict(_session_settings_versions)
    try:
        rows = await sp.range_get_async(scope="umo", scope_id=None, key="session_service_config")
    except asyncio.CancelledError:
        raise
    except Exception as error:
        logger.warning("[CompanionV2] 会话设置预热失败 error_type=%s", type(error).__name__)
        return 0

    expires_at = time.monotonic() + SESSION_SETTINGS_CACHE_SECONDS
    loaded = 0
    for row in rows or ():
        umo = getattr(row, "scope_id", None)
        raw = getattr(row, "value", None)
        services = raw.get("val") if isinstance(raw, dict) else None
        if type(umo) is not str or not umo or not isinstance(services, dict):
            continue
        if _session_settings_versions.get(umo, 0) != versions.get(umo, 0):
            continue
        _session_settings_cache[umo] = (expires_at, dict(services))
        loaded += 1
    logger.info("[CompanionV2] 会话设置预热完成 windows=%s", loaded)
    return loaded


def remember_session_settings(umo, services):
    """Publish a successful dashboard write before the next native event."""
    if type(umo) is not str or not umo or not isinstance(services, dict):
        raise ValueError("native session settings cache needs a window and object")
    _session_settings_versions[umo] = _session_settings_versions.get(umo, 0) + 1
    _session_settings_cache[umo] = (time.monotonic() + SESSION_SETTINGS_CACHE_SECONDS, dict(services))


def installed_native_types() -> NativeAdapterTypes:
    return NativeAdapterTypes(
        ASTRBOT_VERSION,
        AstrMessageEvent,
        ProviderOpenAIOfficial,
        FunctionTool,
        ToolSet,
        WebSocketReverseApi,
        Image,
        load_native_image,
        Reply,
        load_native_quote,
        Forward,
        load_native_forwards,
        Record,
        load_native_audio,
        private_components,
        group_quote_loader=load_native_group_quote,
        group_forward_loader=load_native_group_forwards,
        video=Video,
        video_loader=load_native_video,
        group_component_reader=group_components,
        file=File,
        file_loader=load_native_file,
        video_provider_type=ProviderGoogleGenAI,
        video_provider_factory=_native_video_provider,
        # AstrBot's native Google GenAI provider also speaks text; the V2 text
        # adapter translates its OpenAI-shaped payload onto generate_content.
        text_provider_types=(ProviderGoogleGenAI,),
        session_persona_override_lookup=load_session_persona_override,
        persona_snapshot_lookup=load_persona_snapshot_selection,
        persona_selection_lookup=resolve_native_persona_selection,
        media_preparer=prepare_telegram_media,
        voice_converter=telegram_voice_path,
    )


@dataclass(frozen=True)
class NativeProbe:
    configuration: StagingConfiguration
    platform_kind: str
    account_connected: bool
    conversation_open: bool
    persona_selected: str | None
    provider_type: str
    streaming_requested: bool
    streaming_transport_available: bool

    @property
    def streaming_report(self) -> dict[str, Any]:
        available = self.streaming_transport_available
        return {
            "requested": self.streaming_requested,
            "available": available,
            "ready": not self.streaming_requested or available,
            "transport": "astrbot_native_query_stream" if available else None,
            "reason": None if not self.streaming_requested or available else "streaming_transport_unavailable",
        }

    def report(self, *, pinned: bool) -> dict[str, Any]:
        config = self.configuration
        return {
            "profile_id": config.scope.installation_id.removeprefix(INSTALLATION_PREFIX),
            "delivery_mode": config.delivery_mode,
            "platform_id": config.scope.platform,
            "platform_kind": self.platform_kind,
            "account_id": config.scope.account_id,
            "account_connected": self.account_connected,
            "participant_id": config.scope.native_scope_id,
            "conversation_id": config.scope.conversation_id,
            "conversation_open": self.conversation_open,
            "persona_id": self.persona_selected,
            "role_id": config.persona.role.subject_id,
            "persona_instruction_hash": config.persona.instruction_hash,
            "provider_id": config.provider_id,
            "provider_type": self.provider_type,
            "provider_fingerprint": config.provider_fingerprint,
            "configuration_fingerprint": config.fingerprint,
            "streaming": self.streaming_report,
            "pinned": pinned,
            "model_request_sent": False,
            "platform_message_sent": False,
        }


class NativeProbeService:
    """Resolve the configured host target without owning the staging lifecycle."""

    def __init__(self, context, native_types, now):
        self.context = context
        self.native_types = native_types
        self.now = now

    def _streaming_report(self, settings: HostSettings, provider) -> dict[str, Any]:
        available = native_text_provider(self.native_types, provider) and callable(
            getattr(provider, "_query_stream", None)
        )
        return {
            "requested": settings.streaming,
            "available": available,
            "ready": not settings.streaming or available,
            "transport": "astrbot_native_query_stream" if available else None,
            "reason": None if not settings.streaming or available else "streaming_transport_unavailable",
        }

    def discover(self, settings: HostSettings) -> dict[str, Any]:
        platform = self.context.get_platform_inst(settings.platform_id)
        if platform is None:
            raise HostControlError("staging_platform_unavailable", "the configured native platform is unavailable")
        metadata = platform.meta()
        if not supported_platform_name(metadata.name) or metadata.id != settings.platform_id:
            raise HostControlError("staging_platform_unsupported", "the configured platform is not a supported selected instance")
        client = platform.get_client()
        if metadata.name == "aiocqhttp":
            clients = getattr(client, "_wsr_api_clients", {})
            if not isinstance(clients, dict):
                clients = {}
            connected_accounts = []
            for value in clients:
                try:
                    connected_accounts.append(account_number(value))
                except (TypeError, ValueError):
                    continue
            connected_accounts = sorted(set(connected_accounts), key=int)
        else:
            connected_accounts = ([settings.account_id]
                                  if native_client_connected(platform, settings.account_id, client) else [])

        provider = self.context.get_provider_by_id(settings.provider_id) if settings.provider_id else None
        if provider is None and not settings.provider_id:
            provider = self.context.get_using_provider()
        provider_id = ""
        provider_hash = None
        if provider is not None:
            native_config = getattr(provider, "provider_config", {})
            candidate = native_config.get("id") if isinstance(native_config, dict) else None
            provider_id = candidate if isinstance(candidate, str) else ""
            if native_text_provider(self.native_types, provider):
                provider_hash = provider_fingerprint(provider)
        return {
            "platform_id": metadata.id,
            "platform_kind": metadata.name,
            "connected_account_ids": connected_accounts,
            "configured_account_id": settings.account_id or None,
            "configured_participant_id": settings.participant_id or None,
            "configured_provider_id": settings.provider_id or None,
            "resolved_provider_id": provider_id or None,
            "provider_type": type(provider).__module__ + "." + type(provider).__qualname__ if provider else None,
            "provider_fingerprint": provider_hash,
            "provider_supported": native_text_provider(self.native_types, provider),
            "streaming": self._streaming_report(settings, provider),
            "target_ready": bool(
                settings.account_id and settings.participant_id and settings.provider_id
            ),
            "enabled": settings.enabled,
            "model_request_sent": False,
            "platform_message_sent": False,
        }

    async def probe(
        self, settings: HostSettings, *, valid_from: datetime | None = None, require_enabled: bool = True
    ) -> NativeProbe:
        settings.require_ready(require_enabled=require_enabled)
        self.discover(settings)
        platform = self.context.get_platform_inst(settings.platform_id)
        assert platform is not None
        metadata = platform.meta()
        client = platform.get_client()
        if not native_client_connected(platform, settings.account_id, client):
            raise HostControlError("staging_account_unavailable", "the configured native account is not connected")

        umo = f"{settings.platform_id}:FriendMessage:{settings.participant_id}"
        manager = self.context.conversation_manager
        conversation_id = await bounded_native_lookup(manager.get_curr_conversation_id(umo), operation="conversation_id")
        override = None
        if self.native_types.session_persona_override_lookup is not None:
            override = await bounded_native_lookup(
                self.native_types.session_persona_override_lookup(umo), operation="session_settings")
        selected = override if override is not None else settings.persona_id
        if selected is None or selected == "":
            raise HostControlError("staging_persona_unbound", "bind a persona to this window in Companion settings")
        if (type(selected) is not str or not selected.strip() or len(selected) > 120
                or any(ord(char) < 32 for char in selected)):
            raise HostControlError("staging_persona_invalid", "the V2 window persona binding is invalid")
        if selected == "[%None]":
            persona = None
        else:
            resolver = getattr(self.context.persona_manager, "get_persona_v3_by_id", None)
            persona = resolver(selected) if callable(resolver) else next((item for item in
                getattr(self.context.persona_manager, "personas_v3", ())
                if isinstance(item, dict) and item.get("name") == selected), None)
            if persona is None or persona.get("name") != selected:
                raise HostControlError("staging_persona_unavailable", "the V2 window persona is unavailable")
        special = False
        if await bounded_native_lookup(manager.get_curr_conversation_id(umo), operation="conversation_verify") != conversation_id:
            raise HostControlError("staging_conversation_changed", "the AstrBot conversation changed during the probe")
        if special or persona is None and selected not in (None, "[%None]"):
            raise HostControlError("staging_persona_unsupported", "the selected AstrBot persona cannot be pinned")
        if persona is not None and persona.get("name") != selected:
            raise HostControlError("staging_persona_invalid", "AstrBot returned an inconsistent persona")
        if settings.persona_id and settings.persona_id != (selected or ""):
            raise HostControlError("staging_persona_mismatch", "the configured persona does not match the target conversation")
        instruction = persona_instruction(persona)

        provider = self.context.get_provider_by_id(settings.provider_id)
        if not native_text_provider(self.native_types, provider):
            raise HostControlError("staging_provider_unsupported", "the configured provider does not use the verified AstrBot adapter")
        assemble_providers(self.context, bridge=None, native_types=self.native_types,
                           primary_id=settings.provider_id, additional=settings.additional_providers,
                           defaults=TextProviderSettings(settings.provider_id, settings.tariff,
                               settings.max_output_tokens, settings.maximum_input_estimate,
                               settings.output_parameter, settings.streaming))

        installation_id = settings.installation_id
        scope = ScopeRef(
            installation_id,
            settings.platform_id,
            ScopeKind.PRIVATE,
            settings.participant_id,
            settings.bot_id,
            settings.account_id,
            NativePrivateBridge._conversation_key(umo, conversation_id),
            settings.participant_id,
        )
        role = SubjectRef(installation_id, SubjectKind.BOT, settings.role_id)
        configuration = StagingConfiguration(
            scope=scope,
            host_id=HOST_ID,
            ecosystem_id=ECOSYSTEM_ID,
            subject=SubjectRef(installation_id, SubjectKind.HUMAN,
                               native_user_subject_id(metadata.name, settings.participant_id)),
            persona=HostPersona(settings.bot_id, selected, role, digest(instruction)),
            authority_ref="astrbot-dashboard:" + settings.profile_id,
            valid_from=valid_from or self.now(),
            expires_at=None,
            capabilities=tuple(sorted(REQUIRED_CAPABILITIES)),
            provider_id=settings.provider_id,
            provider_fingerprint=provider_fingerprint(provider),
            tariff=settings.tariff,
            budget=settings.budget,
            budget_soft=settings.budget_soft,
            task_prompt=settings.task_prompt,
            delivery_mode=settings.delivery_mode,
            max_output_tokens=settings.max_output_tokens,
            maximum_input_estimate=settings.maximum_input_estimate,
            output_parameter=settings.output_parameter,
            streaming=settings.streaming,
            segmentation=settings.segmentation,
            forward_intake=settings.forward_intake,
            policy=settings.policy,
            limits=settings.limits,
        )
        return NativeProbe(
            configuration,
            metadata.name,
            True,
            conversation_id is not None,
            selected,
            type(provider).__module__ + "." + type(provider).__qualname__,
            settings.streaming,
            callable(getattr(provider, "_query_stream", None)),
        )
