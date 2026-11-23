# -*- coding: utf-8 -*-
"""AstrBot entry for private conversation and explicitly authorized groups."""

from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import Mapping
from dataclasses import replace as replace_dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .astrbot_host.upgrade_source import preserve_host_configuration

# Keep recent plugin log records in memory for the governance page. This is
# process-local and dashboard-only; it never creates or rotates a log file.
from .astrbot_host.log_buffer import install_log_buffer
install_log_buffer()

# AstrBot can prune keys unknown to the new schema before constructing the Star.
preserve_host_configuration()

from .astrbot_host.import_lifecycle import activate_plugin_imports, retire_plugin_imports

# AstrBot imports plugins as ``data.plugins.<directory>.main`` without adding
# the plugin directory itself to sys.path.  The isolated core intentionally
# keeps its release package name ``companion_v2``; this host entry owns that
# one import path and releases it after a clean plugin shutdown.
_PLUGIN_ROOT = str(Path(__file__).resolve().parent)
_CORE_PATH_INSERTED = activate_plugin_imports(_PLUGIN_ROOT, __name__)

from .astrbot_host.multi_controller import AstrBotMultiController
from .astrbot_host.conversation_reset import install_native_reset_marker
from .astrbot_host.core_reply import (
    CORE_REPLY_CONTEXT_EXTRA,
    SILENT_SEND_DENIAL_CODES,
    activate_core_reply_delivery_guard,
    adopt_core_reply_event,
    finish_core_reply_presence,
    inject_core_reply_prompt,
    prepare_core_reply_event,
    repair_tool_call_history,
    restore_core_reply_event,
    restore_core_reply_prepared,
)
from .astrbot_host.core_reply_tools import inject_core_reply_tools, inject_core_turn_notes
from .astrbot_host.core_reply_preparation import measure_core_preparation
from .astrbot_host.core_reply_decision import prepare_core_reply_decision
from .astrbot_host.core_reply_closing import prepare_private_closing_decision
from .astrbot_host.native import warm_session_settings
from .astrbot_host.core_reply_tool_dispatch import observe_core_tool_dispatch_begin, restore_core_tool_dispatch
from .astrbot_host.core_reply_turn_gate import close_core_reply_turn, install_core_reply_turn
from .astrbot_host.core_reply_tool_guard import (
    close_core_reply_tool_guard, install_core_reply_tool_guard, mark_managed_turn,
)
from .astrbot_host.core_reply_mentions import prepare_bare_mention_request
from .astrbot_host.core_reply_forwards import (
    apply_debounced_private_merge,
    await_event_forwards_parsed,
    claim_core_reply_forward_injection,
    coalesce_debounced_forwards,
    combine_text_and_forward_summary,
    ensure_event_forwards_parsed,
    event_has_media_or_reply,
    event_has_top_level_forwards,
    event_will_enter_astrbot_llm,
    format_forwards_for_observation,
    forward_burst_debounce_policy,
    get_parsed_forwards,
    inject_core_reply_forwards,
    merge_followup_forwards,
    parse_event_forwards,
    prepare_forward_only_request,
    private_debounce_input,
    resolve_forward_intake_policy,
)
from .astrbot_host.core_reply_native_send import inject_core_reply_send_tool
from .astrbot_host.core_reply_perception import (
    inject_core_reply_perception_context, inject_core_reply_perception_policy,
)
from .astrbot_host.native_extensions.host_events import (
    prepare_native_host_request, observe_native_host_response, observe_native_host_result,
)
from .astrbot_host.core_reply_segmentation import (
    activate_core_reply_segmentation, prepare_core_reply_segmentation,
    prepare_core_reply_streaming_boundary, prepare_core_result_decoration,
)
from .astrbot_host.core_reply_tracking import (
    activate_core_reply_tracking, adopt_core_reply_tracking, finish_core_reply_tracking,
    mark_core_reply_inflight, observe_core_group_participation, prepare_core_reply_tracking,
)
from .astrbot_host.core_reply_telemetry import (
    finish_core_reply_telemetry, observe_core_agent_begin, observe_core_agent_done,
    observe_core_llm_response, observe_core_reply_delivery, observe_core_tool_finished,
    observe_core_tool_started, observe_core_waiting_llm_request, prepare_core_reply_telemetry,
    observe_core_reply_suppressed,
)
from .astrbot_host.calendar_command import parse_calendar_command
from .astrbot_host.functional_commands import classify_functional_command, inject_functional_command_instruction
from .astrbot_host.group_command import receive_group_llm_command
from .astrbot_host.reset_command import receive_reset_command
from .astrbot_host.phone_command import receive_phone_confirmation
from companion_v2.conversation.group_llm_block import GroupLLMBlock
from companion_v2.conversation.group_timing import SELF_MENTION
from companion_v2.conversation import debounce_log
from .astrbot_host.private_debounce import PrivateDebounce, PrivateDeliveryDedup
from .astrbot_host.group_debounce import GroupDebounce, group_plain_text, replace_group_plain_text
from companion_v2.adapters.astrbot.group import group_event_route
from companion_v2.adapters.astrbot.voice_stt import has_external_stt_extra, read_stt_extra
from .astrbot_host.private_reply_followup import PrivateReplyFollowUps
from .astrbot_host.core_reply_followup_guard import CoreReplyFollowUpGuard
from .astrbot_host.event_routing import (
    COMPANION_INTAKE_PRIORITY, competing_command_handler, suppress_default_reply,
)
from .astrbot_host.shared_host_migration import migrate_single_target_config
from .astrbot_host.storage_location import prepare_storage_root, resolve_data_root
from companion_v2.product import PRODUCT_VERSION
from companion_v2.diagnostics.inbound import InboundNoReplyLedger, SUPPRESSED, UNMET
from companion_v2.conversation.private_reply_context import merged_input_sequence
from .astrbot_host.integration_registry import UnifiedCompanionExtensionAPI
from .astrbot_host.recalls import is_notice_event
from .astrbot_host.stranger_notice import refuse_stranger


PLUGIN_NAME = "astrbot_plugin_private_companion"
PLUGIN_VERSION = PRODUCT_VERSION
_ACTIVE_EXTENSION_API = None


def get_private_companion_api():
    """Return this loaded plugin generation's public API while it is active."""
    api = _ACTIVE_EXTENSION_API
    controller = getattr(api, "controller", None)
    if api is None or controller is None or getattr(controller, "_terminated", True):
        return None
    return api


# 婉拒陌生号里，只有"该发却发不出去"才算预期未回。开关关闭、冷却、当日额度、
# 权限或文案不可用，都是设计内的安静，连排障台账都不该记。
STRANGER_NOTICE_FAILURES = frozenset({
    "stranger_notice_session_unavailable",
    "stranger_notice_settings_unavailable",
    "stranger_notice_send_failed",
    "stranger_notice_processing_failed",
})
GROUP_INPUT_PASSTHROUGH_FAILURES = frozenset({
    "host_group_media_unsupported",
    "host_media_unsupported",
})
CORE_REPLY_IDENTITY_FAILURE_PREFIXES = (
    "host_persona_", "host_conversation_", "host_native_lookup_timeout", "host_core_reply_capture_failed",
)
RETRYABLE_HOST_STARTUP_CODES = frozenset({
    "host_platform_unavailable", "host_platform_changed", "host_account_unavailable",
    "host_account_changed", "host_persona_unavailable",
    "host_persona_window_unbound", "host_conversation_changed", "host_startup_failed",
    "host_provider_required", "host_provider_unavailable",
    "staging_preflight_failed",
})
# A denied final send is the one refusal the user feels as silence, and it is
# never replayed. Say which gate refused the reply and whether it can clear by
# itself, so an operator can tell a dropped account from a removed target.
CORE_REPLY_SEND_DENIAL_HINTS = {
    "host_client_offline": "该账号的原生连接在生成期间断开，本条回复作废（不会补发）",
    "host_not_claimed": "该事件已不在当前配置的目标里，本条回复作废",
    "host_not_accepting": "共享宿主已停止接收入站消息，本条回复作废",
    "host_not_running": "共享宿主未在运行，本条回复作废",
    "host_terminated": "共享宿主正在停止，本条回复作废",
    "host_target_mismatch": "本轮回复的目标在生成期间改变，本条回复作废",
    "core_reply_target_changed": "本轮回复的目标在准入之后改变，本条回复作废",
    "core_reply_event_changed": "原生事件在准入之后改变，本条回复作废",
    "core_reply_context_changed": "身份、路由或角色在准入之后改变，本条回复作废",
    "core_reply_instruction_changed": "人格提示词在准入之后改变，本条回复作废",
    "host_persona_changed": "当前人格与准入时不同，本条回复作废",
    "message_withdrawn": "触发消息已被撤回，本条回复按撤回静默",
    "group_llm_blocked": "该群已开启模型封锁，本条回复作废",
    "private_access_dialogue_denied": "该私聊用户已失去对话权限，本条回复作废",
}


def _can_continue_astrbot_core_reply(code):
    if isinstance(code, str) and code.startswith(CORE_REPLY_IDENTITY_FAILURE_PREFIXES):
        return False
    if code in {
        "host_reply_settings_unavailable", "host_core_reply_prepare_failed",
        "host_not_claimed", "application_not_running", "host_core_reply_conversation_unavailable",
        "host_session_settings_timeout",
    }:
        return True
    return False


def _chain_has_populated_content(chain: Any) -> bool:
    """Return True when a message result chain contains non-empty text or a non-Plain component."""
    if not chain:
        return False
    if not isinstance(chain, (list, tuple)):
        return True
    for part in chain:
        kind = getattr(getattr(part, "type", None), "value", getattr(part, "type", None))
        if kind == "Plain" or type(part).__name__ == "Plain":
            if str(getattr(part, "text", "") or "").strip():
                return True
            continue
        return True
    return False


def _has_populated_event_result(event: Any) -> bool:
    """Return True when an upstream handler already attached a concrete reply result on ``event``."""
    getter = getattr(event, "get_result", None)
    result = getter() if callable(getter) else None
    if result is None:
        return False
    if isinstance(result, str):
        return bool(result.strip())
    is_stopped = getattr(result, "is_stopped", None)
    if callable(is_stopped) and is_stopped():
        return True
    if _chain_has_populated_content(getattr(result, "chain", None)) or getattr(result, "async_stream", None) is not None:
        return True
    content_type = getattr(getattr(result, "result_content_type", None), "name", None)
    return content_type in {"LLM_RESULT", "STREAMING_RESULT", "STREAMING_FINISH", "AGENT_RUNNER_ERROR"}


def _is_external_stt_cache_only(event: Any) -> bool:
    """Return True when upstream ``astrbot_plugin_hear_voice``（兼容 ``_gemini_stt_*``）suppressed a group voice turn to cache-only."""
    return read_stt_extra(event, "cache_only") is True and read_stt_extra(event, "should_reply") is False


def _is_explicit_non_hijacking_external_stt(event: Any) -> bool:
    """Return True when upstream ``astrbot_plugin_hear_voice``（兼容 ``_gemini_stt_*``）explicitly declared a non-hijacking turn."""
    hijacked = read_stt_extra(event, "hijacked")
    non_hijacking = read_stt_extra(event, "non_hijacking")
    if hijacked is True or non_hijacking is False:
        return False
    if hijacked is False or non_hijacking is True:
        return True
    return read_stt_extra(event, "notify_only_sent") is True


def _should_skip_hijacked_external_stt(event: Any) -> bool:
    """Return True ONLY when an external STT plugin genuinely hijacked or replied to this turn.

    Non-hijacking ``astrbot_plugin_hear_voice``（兼容 ``_gemini_stt_*``）sets
    ``_hear_voice_should_reply=True`` and ``_hear_voice_cache_only=False`` alongside
    ``_hear_voice_non_hijacking=True`` and ``_hear_voice_hijacked=False`` (both on success and
    under ``on_stt_fail="pass"`` / ``"notify_pass"``; ``notify_pass`` may also set
    ``_hear_voice_notify_only_sent=True`` and fall back to ``event.send()`` which sets
    ``_has_send_oper=True``). Only legacy un-flagged STT plugins treat ``_has_send_oper`` as a
    hijack takeover clue. Events without any external STT extra are never treated as an
    external STT hijack.
    """
    if not has_external_stt_extra(event):
        return False
    getter = getattr(event, "get_extra", None)
    if not callable(getter):
        return False
    hijacked = read_stt_extra(event, "hijacked")
    non_hijacking = read_stt_extra(event, "non_hijacking")
    notify_only_sent = read_stt_extra(event, "notify_only_sent")
    if hijacked is True or non_hijacking is False:
        return True
    if hijacked is False or non_hijacking is True or notify_only_sent is True:
        is_stopped = getattr(event, "is_stopped", None)
        if callable(is_stopped) and is_stopped():
            return True
        if getter("provider_request") is not None:
            return True
        return False
    if getattr(event, "_has_send_oper", False):
        return True
    is_stopped = getattr(event, "is_stopped", None)
    if callable(is_stopped) and is_stopped():
        return True
    if getter("provider_request") is not None:
        return True
    return _has_populated_event_result(event)


@register(
    PLUGIN_NAME,
    "menglimi",
    "我会永远陪着你 V2.0 统一陪伴宿主，整合核心生活能力与公开拓展 API。",
    PLUGIN_VERSION,
)
class PrivateCompanionV2StagingPlugin(Star):
    PLATFORM_STARTUP_COALESCE_SECONDS = 1.5
    PLATFORM_BATCH_COALESCE_SECONDS = 0.05

    def __init__(self, context: Context, config: dict[str, Any] | None = None):
        self._imports_retired = False
        try:
            super().__init__(context, config)
            self._host_start_task = None
            self._core_reply_owner = object()
            self._startup_failure_key = None
            self._startup_failure_at = 0.0
            self._session_settings_warm_task = None
            self._qzone_life_publisher = None
            self.config = config if config is not None else {}
            default_data_root = resolve_data_root(Path(StarTools.get_data_dir(PLUGIN_NAME)).resolve())
            data_root = prepare_storage_root(default_data_root, self.config)
            data_root.mkdir(parents=True, exist_ok=True)
            migrated, changed = migrate_single_target_config(self.config)
            if changed:
                self.config.clear()
                self.config.update(migrated)
            self.staging = AstrBotMultiController(context, self.config, data_root)
            self._retire_context_reset_marker = install_native_reset_marker(context)
            self.staging.default_data_root = default_data_root
            config_object = config if hasattr(config, "save_config") else None
            if config_object is not None:
                self.staging.save_plugin_config = self._save_plugin_config
                if changed:
                    try:
                        config_object.save_config()
                    except Exception:
                        logger.warning("[CompanionV2] 旧单目标设置已在内存迁移，但持久化失败", exc_info=True)
            try:
                repaired = self.staging.repair_legacy_config()
            except Exception:
                repaired = {}
                logger.warning("[CompanionV2] 旧配置自愈检查失败", exc_info=True)
            if repaired:
                logger.warning("[CompanionV2] 启动时已自动修复旧策略字段：%s", repaired)
            # One public facade exposes the body and its seven internal modules.
            # Independent third-party integrations use their documented APIs.
            self.extension_api = UnifiedCompanionExtensionAPI(self.staging, claim=True)
            global _ACTIVE_EXTENSION_API
            _ACTIVE_EXTENSION_API = self.extension_api
            try:
                self.staging.register_routes()
            except Exception:
                logger.warning("[CompanionV2] 共享宿主管理 API 注册失败", exc_info=True)
        except BaseException:
            self._retire_failed_initialization()
            raise

    def _retire_failed_initialization(self) -> None:
        if self._imports_retired:
            return
        global _ACTIVE_EXTENSION_API
        if _ACTIVE_EXTENSION_API is getattr(self, "extension_api", None):
            _ACTIVE_EXTENSION_API = None
        retire_reset_marker = getattr(self, "_retire_context_reset_marker", None)
        if callable(retire_reset_marker):
            try:
                retire_reset_marker()
            except Exception:
                logger.warning("[CompanionV2] 初始化失败后清理 reset 标记失败", exc_info=True)
        staging = getattr(self, "staging", None)
        unregister_routes = getattr(staging, "unregister_routes", None)
        if callable(unregister_routes):
            try:
                unregister_routes()
            except Exception:
                logger.warning("[CompanionV2] 初始化失败后注销管理路由失败", exc_info=True)
        try:
            retire_plugin_imports(_PLUGIN_ROOT, __name__, path_inserted=_CORE_PATH_INSERTED,
                                  entry_globals=globals())
        except Exception:
            logger.warning("[CompanionV2] 初始化失败后清理导入模块失败", exc_info=True)
        finally:
            self._imports_retired = True

    def _save_plugin_config(self, candidate: dict[str, Any]) -> None:
        previous = dict(self.config)
        self.config.clear()
        self.config.update(candidate)
        try:
            self.config.save_config()
        except Exception:
            self.config.clear()
            self.config.update(previous)
            raise

    def _record_no_reply(self, event, reason_code, *, origin_key=None, channel=None, event_ref=None,
                         stage="intake", turn=None, expected=True, code=None):
        """Resolve one inbound that produced no reply.

        ``expected`` is the polarity, and it is the whole contract: only a reply
        that was genuinely expected and then not delivered reaches the platform
        log. Anything outside this plugin's targets, any disabled feature and
        any deliberate silence is recorded at most in the bounded ledger.
        """
        if channel is None:
            kind = getattr(getattr(getattr(event, "message_obj", None), "type", None), "value", None)
            channel = "private" if kind == "FriendMessage" else "group" if kind == "GroupMessage" else "unknown"
        event_ref = event_ref or uuid4().hex
        runtime = getattr(self.staging, "runtime", None)
        if runtime is not None:
            try:
                InboundNoReplyLedger(runtime).emit(
                    expectation=UNMET if expected else SUPPRESSED, stage=stage,
                    turn=turn or event_ref, reason_code=reason_code, observed_at=self.staging.now(),
                    channel=channel, origin_key=origin_key, event_ref=event_ref, code=code)
            except Exception as error:
                logger.error("[CompanionV2] 未回复排障记录写入失败 event_ref=%s error_type=%s",
                             event_ref, type(error).__name__)
        return event_ref

    def _harassment_guard(self):
        guard = getattr(self, "_harassment", None)
        if guard is None:
            from .astrbot_host.harassment import HarassmentGuard
            guard = HarassmentGuard()
            self._harassment = guard
        return guard

    @staticmethod
    def _harassment_features(event):
        message = getattr(getattr(event, "message_obj", None), "message", None)
        mentions = 0
        media = False
        if isinstance(message, (list, tuple)):
            for part in message:
                kind = getattr(part, "type", None)
                name = getattr(kind, "value", kind)
                if name == "At":
                    mentions += 1
                elif name in ("Image", "Record", "Video", "File"):
                    media = True
        return mentions, media

    @staticmethod
    def _harassment_scope(event):
        origin = getattr(event, "unified_msg_origin", "")
        return str(origin or getattr(event, "session_id", "") or "")

    @staticmethod
    def _harassment_user(event):
        sender = getattr(getattr(event, "message_obj", None), "sender", None)
        user_id = getattr(sender, "user_id", "")
        if not user_id:
            getter = getattr(event, "get_sender_id", None)
            user_id = getter() if callable(getter) else ""
        return str(user_id or "")

    def _private_debounce_manager(self):
        manager = getattr(self, "_private_debounce", None)
        if manager is None:
            manager = PrivateDebounce()
            self._private_debounce = manager
        return manager

    def _group_debounce_manager(self):
        manager = getattr(self, "_group_debounce", None)
        if manager is None:
            manager = GroupDebounce()
            self._group_debounce = manager
        return manager

    def _private_delivery_dedup(self):
        dedup = getattr(self, "_private_delivery_seen", None)
        if dedup is None:
            dedup = PrivateDeliveryDedup()
            self._private_delivery_seen = dedup
        return dedup

    def _private_delivery_duplicate(self, event, target):
        """Whether the platform delivered this exact native message twice."""
        message_id = getattr(getattr(event, "message_obj", None), "message_id", None)
        if message_id is None or message_id == "":
            return False
        key = (str(getattr(target, "target_id", "") or ""), str(message_id))
        return self._private_delivery_dedup().duplicate(key)

    def _private_reply_followups(self):
        manager = getattr(self, "_private_followup", None)
        if manager is None:
            manager = PrivateReplyFollowUps()
            self._private_followup = manager
        return manager

    def _core_reply_followup_guard(self):
        guard = getattr(self, "_core_followup_guard", None)
        if guard is None:
            guard = CoreReplyFollowUpGuard()
            self._core_followup_guard = guard
        return guard

    def _cleanup_captured_core_followup(self, event, prepared, *, event_ref="", channel="",
                                        reason="follow_up_captured"):
        """Release plugin turn state when an admitted event never runs its own core turn."""
        followups = getattr(self, "_private_followup", None)
        if followups is not None:
            followups.finish_event(event)
        finish_core_reply_tracking(event, log_completion=False)
        if isinstance(prepared, dict):
            phase = "completed" if reason == "follow_up_captured" else "failed"
            self._observe_core_phase(prepared, phase)
        restore_core_reply_event(event)
        finish_core_reply_presence(event, force=True)
        if reason == "follow_up_captured":
            getter = getattr(event, "get_extra", None)
            captured = getter("_follow_up_captured") if callable(getter) else None
            target_run_id = captured.get("target_run_id") if isinstance(captured, Mapping) else None
            logger.info(
                "[CompanionV2] 核心追发消息已并入在途轮次，清理本条跟踪状态 "
                "event_ref=%s channel=%s target_run_id=%s",
                event_ref or "unknown", channel or "unknown", target_run_id or "-")

    async def _enter_core_reply_followup_guard(self, event, *, event_ref="", channel="", prepared=None):
        """Serialize media follow-ups and attach cleanup for host follow-up absorption."""
        def on_turn_done(done_event, done_task):
            getter = getattr(done_event, "get_extra", None)
            captured = getter("_follow_up_captured") if callable(getter) else None
            if captured:
                self._cleanup_captured_core_followup(
                    done_event, prepared, event_ref=event_ref, channel=channel,
                    reason="follow_up_captured")
                return
            if done_task.cancelled():
                self._cleanup_captured_core_followup(
                    done_event, prepared, event_ref=event_ref, channel=channel,
                    reason="task_cancelled")
                return
            try:
                failed = done_task.exception() is not None
            except Exception:
                failed = False
            if failed:
                self._cleanup_captured_core_followup(
                    done_event, prepared, event_ref=event_ref, channel=channel,
                    reason="task_failed")

        try:
            return await self._core_reply_followup_guard().enter(
                event, event_ref=event_ref, channel=channel, on_turn_done=on_turn_done)
        except asyncio.CancelledError:
            self._cleanup_captured_core_followup(
                event, prepared, event_ref=event_ref, channel=channel, reason="wait_cancelled")
            raise

    async def _guard_passthrough_core_reply(self, event, *, event_ref="", channel=""):
        """Protect degraded AstrBot passthrough turns when they will enter core LLM."""
        if (not getattr(event, "is_at_or_wake_command", False)
                or getattr(event, "call_llm", False)
                or getattr(event, "_has_send_oper", False)):
            return
        outcome = await self._enter_core_reply_followup_guard(event, event_ref=event_ref, channel=channel)
        if outcome.get("reason") == "closed":
            suppress_default_reply(event)

    def _fold_private_follow_up(self, event, target, event_ref, text, *, allow_empty=False):
        """Join a post-debounce message to the first model request when possible."""
        target_key = getattr(target, "target_id", None)
        if not isinstance(target_key, str) or not target_key:
            return False
        active_window = self._private_reply_followups().active_for(target_key)
        if active_window is None:
            return False
        guard = getattr(self, "_core_followup_guard", None)
        if guard is not None and guard.has_pending_media_barrier(
                event, owner_event=active_window.event):
            return False
        active = self._private_reply_followups().fold(
            target_key, event, text, allow_empty=allow_empty)
        if active is None:
            return False
        self._suppress_merged_private_event(
            event, target, event_ref, reason="private_reply_follow_up_merged")
        logger.info(
            "[CompanionV2] 私聊后续消息并入待发送回复 event_ref=%s owner_event_ref=%s target=%s message=%s",
            event_ref, active.event_ref, target_key, str(text)[:120])
        return True

    def _apply_private_follow_ups_to_request(self, event, request):
        """Append messages that arrived after debounce but before provider dispatch."""
        prepared_state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        resolved = self._private_reply_followups().for_request(event)
        if resolved is None:
            return
        pending, entries = resolved
        if not pending:
            return
        existing_forwards = get_parsed_forwards(event)
        merged_forwards = merge_followup_forwards(
            event, pending, policy=resolve_forward_intake_policy(self.staging))
        forwards_updated = (merged_forwards is not None and merged_forwards is not existing_forwards)
        extra_text = "\n".join(str(item[1]).strip() for item in pending if str(item[1]).strip())
        if not extra_text and not forwards_updated:
            return
        if extra_text:
            prompt = getattr(request, "prompt", None)
            if isinstance(prompt, str) and prompt.strip():
                request.prompt = prompt.rstrip() + "\n" + extra_text
            else:
                request.prompt = extra_text
            if hasattr(request, "retrieval_query"):
                current_query = getattr(request, "retrieval_query", None)
                if isinstance(current_query, str) and current_query.strip():
                    request.retrieval_query = current_query.rstrip() + "\n" + extra_text
                else:
                    request.retrieval_query = request.prompt
        prepared = prepared_state.get("prepared") if isinstance(prepared_state, dict) else None
        if isinstance(prepared, dict):
            base = str(prepared.get("text") or "").rstrip()
            if forwards_updated:
                old_summary = format_forwards_for_observation(existing_forwards)
                if old_summary and base.endswith(old_summary):
                    base = base[: -len(old_summary)].rstrip()
                elif old_summary and base == old_summary:
                    base = ""
                if extra_text:
                    base = (base + "\n" + extra_text).strip() if base else extra_text
                adaptation_enabled = prepared.get("forward_adaptation_enabled", True) is not False
                prepared["text"] = combine_text_and_forward_summary(
                    base, merged_forwards, adaptation_enabled=adaptation_enabled)
            elif extra_text:
                prepared["text"] = (base + "\n" + extra_text).strip() if base else extra_text
            prepared["debounced_inputs"] = merged_input_sequence(entries)
            prepared["debounced_message_count"] = len(entries)
        logger.info(
            "[CompanionV2] 首轮模型请求前合并私聊后续消息 event_ref=%s count=%d message=%s",
            (prepared or {}).get("event_ref") if isinstance(prepared, dict) else None,
            len(pending), (extra_text or "[合并转发聊天记录]")[:120])

    def _suppress_merged_private_event(self, event, target, event_ref, *, reason="private_debounce_merged"):
        suppress_default_reply(event)
        self._record_no_reply(event, reason, origin_key=getattr(target, "key", None),
                              channel="private", event_ref=event_ref, stage="intake",
                              turn=event_ref, expected=False)

    def _suppress_debounced_private_event(self, event, target, event_ref, manager, reservation,
                                          *, reason="private_debounce_merged"):
        if manager.superseded_by_same_event(reservation, event):
            return False
        self._suppress_merged_private_event(event, target, event_ref, reason=reason)
        return True

    @staticmethod
    def _core_reply_plan_with_text(plan, text):
        captured = getattr(plan, "captured", None)
        if captured is None:
            return plan
        try:
            updated = replace_dataclass(captured, text=text)
        except (TypeError, ValueError):
            try:
                captured.text = text
            except (AttributeError, TypeError):
                return plan
            return plan
        try:
            return replace_dataclass(plan, captured=updated)
        except (TypeError, ValueError):
            try:
                plan.captured = updated
            except (AttributeError, TypeError):
                return plan
            return plan

    def _origin_key_for_event(self, event):
        target_for_event = getattr(self.staging, "target_for_event", None)
        target = target_for_event(event) if callable(target_for_event) else None
        if target is None:
            session = getattr(self.staging, "session", None)
            target = getattr(getattr(session, "profile", None), "target", None)
        key = getattr(target, "key", None)
        return key if isinstance(key, str) and key else None

    def _claims_target(self, event) -> bool:
        """Whether this exact event resolves to one of the configured targets.

        A target that resolves but is not being accepted is still the plugin's
        own message; the difference matters because only the plugin's own
        inbounds may ever produce a reply outcome at all.
        """
        target_for_event = getattr(self.staging, "target_for_event", None)
        try:
            return bool(callable(target_for_event) and target_for_event(event) is not None)
        except Exception:
            return False

    def staging_enabled(self) -> bool:
        """Whether this installation ever asked the shared host to answer.

        With the host switched off, or its settings unreadable, nothing was ever
        promised to anybody: that is a configuration state, not a missed reply.
        """
        settings = getattr(self.staging, "settings", None)
        return bool(getattr(settings, "enabled", False))

    def _admission_reason(self) -> str:
        """Why the shared host could not take a message it owns."""
        session = getattr(self.staging, "session", None)
        app_state = getattr(getattr(session, "application", None), "state", None)
        return ("staging_terminated" if getattr(self.staging, "_terminated", False) else
                "staging_not_accepting" if not getattr(self.staging, "_accepting", False) else
                "staging_session_unavailable" if session is None else
                "staging_application_not_running" if app_state != "running" else
                "staging_target_not_claimed")

    def _platform_loaded_signal(self) -> asyncio.Event:
        signal = getattr(self, "_platform_loaded_event", None)
        if signal is None:
            signal = asyncio.Event()
            self._platform_loaded_event = signal
        return signal

    def _platform_loaded_lock(self) -> asyncio.Lock:
        lock = getattr(self, "_platform_loaded_hook_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._platform_loaded_hook_lock = lock
        return lock

    async def initialize(self) -> None:
        if self._session_settings_warm_task is None or self._session_settings_warm_task.done():
            self._session_settings_warm_task = asyncio.create_task(
                warm_session_settings(), name="companion-v2-session-settings-warmup")
        # Start both drivers before host startup; each checks its own readiness.
        if getattr(self, "_automatic_backup", None) is None:
            from .astrbot_host.automatic_backup import AutomaticBackup
            self._automatic_backup = AutomaticBackup(self.staging).start()
        if self._qzone_life_publisher is None:
            from .astrbot_host.qzone_life_publish import QzoneLifePublisher
            self._qzone_life_publisher = QzoneLifePublisher(self.staging).start()
        if self.staging.settings is None or not self.staging.settings.enabled:
            logger.info("[CompanionV2] 共享宿主已加载，等待账号与人格配置")
            return
        should_wait = getattr(self.staging, "should_wait_for_initial_platform", None)
        if callable(should_wait) and should_wait():
            logger.info("[CompanionV2] 等待平台适配器加载完成后再装配共享宿主")
            self._schedule_host_start(wait_for_platform_seconds=self.PLATFORM_STARTUP_COALESCE_SECONDS)
            return
        if not await self._try_start_host():
            self._schedule_host_start()

    async def _try_start_host(self) -> bool:
        if getattr(self.staging, "_stopped_by_user", False):
            return True
        try:
            readiness = self.staging.startup_readiness()
        except Exception as error:
            code = getattr(error, "code", None) or "startup_readiness_failed"
            if code == "host_terminated":
                return True
            retry = self._retry_startup_failure(code)
            self._note_startup_failure("readiness", code, type(error).__name__, str(error), retrying=retry)
            return not retry
        if readiness in ("host_platform_unavailable", "host_account_unavailable"):
            return False
        if readiness is not None:
            retry = self._retry_startup_failure(readiness)
            self._note_startup_failure("readiness", readiness, retrying=retry)
            return not retry

        initialized_profile = False
        if self.staging.session is None:
            loaded = await self.staging.load_existing()
            if loaded.failure is not None:
                if loaded.failure.code == "host_terminated":
                    return True
                retry = self._retry_startup_failure(loaded.failure.code)
                self._note_startup_failure("load_existing", loaded.failure.code,
                                           detail=loaded.failure.reason, retrying=retry)
                return not retry
            if not loaded.unwrap()["loaded"]:
                # Account/persona mappings are sufficient to create the shared
                # installation. The old core-window binding was only a first-run bootstrap.
                initialized = await self.staging.initialize_profile(automatic=True)
                if initialized.failure is not None:
                    if initialized.failure.code == "host_terminated":
                        return True
                    retry = self._retry_startup_failure(initialized.failure.code)
                    self._note_startup_failure("initialize", initialized.failure.code,
                                               detail=initialized.failure.reason, retrying=retry)
                    return not retry
                initialized_profile = True

        if getattr(self.staging, "_stopped_by_user", False):
            return True
        started = await self.staging.start(automatic=True)
        if started.failure is not None:
            if started.failure.code in ("host_terminated", "host_manually_stopped"):
                return True
            retry = self._retry_startup_failure(started.failure.code)
            self._note_startup_failure("start", started.failure.code,
                                       detail=started.failure.reason, retrying=retry)
            return not retry
        self._startup_failure_key = None
        logger.info("[CompanionV2] staging profile %s并开始接收入站消息",
                    "已自动初始化" if initialized_profile else "已加载")
        return True

    @staticmethod
    def _retry_startup_failure(code: str) -> bool:
        return (isinstance(code, str)
                and (code in RETRYABLE_HOST_STARTUP_CODES
                     or code.startswith("host_native_lookup_timeout")))

    def _note_startup_failure(self, stage: str, code: str, error_type: str | None = None,
                              detail: str | None = None, *, retrying: bool = False) -> None:
        key = (stage, code, error_type)
        now = asyncio.get_running_loop().time()
        previous_key = getattr(self, "_startup_failure_key", None)
        previous_at = getattr(self, "_startup_failure_at", 0.0)
        if previous_key == key and now - previous_at < 60.0:
            return
        self._startup_failure_key = key
        self._startup_failure_at = now
        suffix = "，将自动重试" if retrying else "，请修正配置或重新加载插件"
        if code == "host_provider_required":
            logger.warning(
                "[CompanionV2] 共享启动模型（model.provider_id）留空且 AstrBot 当前暂无可用的文本 Provider%s "
                "stage=%s code=%s detail=%s（请确认 AstrBot 已启用文本模型，或在插件配置中指定共享启动模型）",
                suffix, stage, code, detail or "unavailable")
        elif stage == "load_existing":
            logger.warning("[CompanionV2] 已固定 profile 加载失败%s code=%s detail=%s",
                           suffix, code, detail or "unavailable")
        elif stage == "initialize":
            logger.warning("[CompanionV2] staging profile 自动初始化失败%s code=%s detail=%s",
                           suffix, code, detail or "unavailable")
        elif stage == "start":
            logger.warning("[CompanionV2] staging profile 自动启动失败%s code=%s detail=%s",
                           suffix, code, detail or "unavailable")
        else:
            logger.warning("[CompanionV2] 保存的运行档暂不能启动%s code=%s error_type=%s detail=%s",
                           suffix, code, error_type or "unknown", detail or "unavailable")

    def _schedule_host_start(self, *, wait_for_platform_seconds: float = 0.0) -> None:
        if self.staging._terminated or getattr(self.staging, "_stopped_by_user", False):
            return
        session = self.staging.session
        if (self.staging._accepting and session is not None
                and session.application.state == "running"):
            try:
                bridges_current = self.staging.native_bridges_current()
            except Exception as error:
                logger.warning("[CompanionV2] 平台桥接状态检查失败 error_type=%s", type(error).__name__)
                bridges_current = False
            if bridges_current:
                return
            self.staging._accepting = False
        task = getattr(self, "_host_start_task", None)
        if task is None or task.done():
            self._host_start_task = asyncio.create_task(
                self._wait_for_host_start(wait_for_platform_seconds=wait_for_platform_seconds),
                name="companion:host-startup")

    async def _wait_for_host_start(self, *, wait_for_platform_seconds: float = 0.0) -> None:
        loop = asyncio.get_running_loop()
        if (wait_for_platform_seconds > 0
                and not self.staging._terminated
                and not getattr(self.staging, "_stopped_by_user", False)):
            should_wait = getattr(self.staging, "should_wait_for_initial_platform", None)
            all_loaded = getattr(self.staging, "all_configured_platforms_loaded", None)
            if callable(should_wait) and should_wait():
                signal = self._platform_loaded_signal()
                signal.clear()
                try:
                    await asyncio.wait_for(signal.wait(), timeout=wait_for_platform_seconds)
                except asyncio.TimeoutError:
                    pass
                batch_window = getattr(self, "PLATFORM_BATCH_COALESCE_SECONDS", 0.05)
                if (not self.staging._terminated
                        and not getattr(self.staging, "_stopped_by_user", False)
                        and batch_window > 0
                        and callable(all_loaded)
                        and not all_loaded()):
                    deadline = loop.time() + batch_window
                    while loop.time() < deadline and not all_loaded():
                        signal.clear()
                        remaining = deadline - loop.time()
                        if remaining <= 0:
                            break
                        try:
                            await asyncio.wait_for(signal.wait(), timeout=remaining)
                        except asyncio.TimeoutError:
                            break
        started_at = loop.time()
        delay = 0.5
        warned = False
        while not self.staging._terminated:
            generation_before = getattr(self, "_platform_loaded_generation", 0)
            try:
                finished = await self._try_start_host()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("[CompanionV2] 启动恢复失败 code=%s error_type=%s",
                               getattr(error, "code", "startup_failed"), type(error).__name__)
                return
            if finished:
                if (getattr(self, "_platform_loaded_generation", 0) != generation_before
                        and not self.staging._terminated
                        and not getattr(self.staging, "_stopped_by_user", False)
                        and self.staging.session is not None):
                    recover = getattr(self.staging, "recover_accounts", None)
                    if callable(recover):
                        await recover()
                    checker = getattr(self.staging, "native_bridges_current", None)
                    if callable(checker) and not checker():
                        self.staging._accepting = False
                        continue
                return
            if not warned and loop.time() - started_at >= 30:
                logger.warning("[CompanionV2] 运行档尚未进入接收状态，恢复任务仍在运行并会自动重试")
                warned = True
            await asyncio.sleep(delay)
            delay = min(delay * 1.8, 30.0)

    @filter.on_platform_loaded()
    async def on_companion_platform_loaded(self) -> None:
        self._platform_loaded_generation = getattr(self, "_platform_loaded_generation", 0) + 1
        signal = getattr(self, "_platform_loaded_event", None)
        if signal is not None:
            signal.set()
        if self.staging.settings is None or not self.staging.settings.enabled:
            return
        if self.staging._terminated or getattr(self.staging, "_stopped_by_user", False):
            return
        task = getattr(self, "_host_start_task", None)
        if task is not None and not task.done():
            return
        async with self._platform_loaded_lock():
            task = getattr(self, "_host_start_task", None)
            if task is not None and not task.done():
                return
            await self.staging.recover_accounts()
            self._schedule_host_start()

    @filter.event_message_type(filter.EventMessageType.ALL, priority=240000)
    async def observe_companion_recall(self, event: AstrMessageEvent):
        result = await self.staging.receive_notice(event)
        if result is not None and result.failure is not None:
            logger.warning("[CompanionV2] 原生通知未受理: %s", result.failure.code)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=235000)
    async def observe_companion_group_early(self, event: AstrMessageEvent):
        """Prepare the authenticated group persona before reply admission.

        V1 captured group observations at a higher priority than its reply
        handlers.  Keep that ordering in V2 so a transient reply admission
        failure cannot prevent the group route from being established for the
        same event; this hook never sends or invokes a model.
        """
        if is_notice_event(event):
            return
        # AstrBot has already resolved command filters before invoking adapter
        # handlers.  Let another plugin's explicit group command continue down
        # the normal dispatch chain; observing it here would add work and make
        # the companion look like it owns a command it does not handle.
        if competing_command_handler(event, own_module=__name__) is not None:
            return
        result = await self.staging.observe_group_early(event)
        if result is not None and result.failure is None:
            try:
                target = self.staging.target_for_event(event)
                processor = getattr(self.staging, "process_relay_group_presence", None)
                if callable(processor):
                    await processor(event, target, result.unwrap())
            except Exception:
                logger.warning("[CompanionV2] 延迟跨群转述触发失败", exc_info=True)
        if result is not None and result.failure is not None:
            # Unconfigured routes and attachment-only messages are normal
            # fall-through cases.  Unexpected failures are useful for the
            # operator, but must not stop AstrBot's remaining handlers.
            if result.failure.code not in {"host_target_unavailable", "host_text_invalid", "host_group_media_unsupported",
                                           "host_not_claimed"}:
                logger.warning("[CompanionV2] 群消息早期预处理未受理: %s", result.failure.code)

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=235000)
    async def receive_companion_binding(self, event: AstrMessageEvent):
        """First-run binding, handled before the staging target can claim anything.

        Runs after the notice listener and before ordinary plugin handlers:
        binding has to be seen while nothing is pinned -- ``claims``
        refuses every message until a target exists, so a bind command read at the
        intake priority would never be reached. Only the literal command is taken;
        ordinary private messages fall through to normal handling untouched.

        The reply is logged, never sent. Binding happens before any session exists,
        and the delivery boundary does not permit private sends without one, so the
        operator reads the outcome on the「开始使用」page instead. That ordering is
        what keeps this role's first real message the draft they reviewed.
        """
        if not self.staging.handles_binding(event):
            return
        event.stop_event()
        result = await self.staging.receive_binding(event)
        if result.get("handled"):
            # 绑定回复不发给用户（此时还没有运行会话），但至少让日志带上
            # 面板/日志里能直接照做的下一步，例如先发 companion_unbind。
            logger.info("[CompanionV2] 首次绑定: %s%s", result.get("reason") or "ok",
                        (" reply=" + str(result.get("reply"))) if result.get("reply") else "")
        else:
            logger.warning("[CompanionV2] 绑定命令未被受理")

    def _core_reply_owner_token(self):
        token = getattr(self, "_core_reply_owner", None)
        if token is None:
            token = object()
            self._core_reply_owner = token
        return token

    def _core_reply_delivery_callbacks(self, event, prepared, *, target):
        event_ref = prepared.get("event_ref")
        origin_key = prepared.get("origin_key")
        channel = prepared.get("channel")
        session = getattr(self.staging, "session", None)
        connection_callbacks = {}
        if prepared.get("connection_lease") is not None:
            connection_callbacks = {
                "assert_connection_current": lambda: session.assert_connection_current(prepared),
                "wait_connection_changed": lambda: session.wait_connection_changed(prepared),
            }

        async def authorize_core_send(current):
            return await self.staging.authorize_core_reply(event, target, current)

        def note_core_send_denied(current, error):
            reason = getattr(error, "code", type(error).__name__)
            self._record_no_reply(event, "core_reply_send_denied", origin_key=origin_key,
                                  channel=channel, event_ref=event_ref, stage="delivery",
                                  turn=event_ref, code=reason)
            hint = CORE_REPLY_SEND_DENIAL_HINTS.get(
                reason, "发送前复核未通过，这条已生成的回复没有发出")
            if reason in SILENT_SEND_DENIAL_CODES:
                logger.info("[CompanionV2] 核心回复按撤回静默跳过发送 event_ref=%s code=%s 说明=%s",
                            event_ref, reason, hint)
            else:
                logger.warning("[CompanionV2] 核心回复发送前复核失败，这条已生成的回复没有发出 "
                               "event_ref=%s code=%s 说明=%s", event_ref, reason, hint)

        def observe_core_send(current, text):
            observer = getattr(getattr(self.staging, "session", None), "core_reply_observations", None)
            if observer is not None:
                observer.output(current, text, state="send_returned")

        def observe_core_delivery(stage):
            if stage == "finished":
                finish_core_reply_telemetry(event)
            else:
                observe_core_reply_delivery(event, stage)

        def finish_core_reply_end():
            from .astrbot_host.core_reply_presence import observe_native_reply_presence
            application = getattr(getattr(self.staging, "session", None), "application", None)
            observe_native_reply_presence(application, prepared, "finished")

        async def prepare_core_voice(current, message, authorize, segment_index, segment_count):
            from .astrbot_host.core_reply_voice import prepare_core_reply_voice_message
            from .astrbot_host.core_reply_reaction_delivery import prepare_core_reaction_message
            from .astrbot_host.core_reply_outbound import prepare_core_reply_outbound_message
            session = getattr(self.staging, "session", None)
            message = await prepare_core_reaction_message(
                event, message, authorize=authorize,
                segment_index=segment_index, segment_count=segment_count)
            message = await prepare_core_reply_voice_message(
                event, current, message,
                renderer=getattr(session, "voice", None),
                delivery=getattr(session, "voice_delivery", None),
                authorize=authorize,
                segment_index=segment_index,
                segment_count=segment_count,
            )
            return prepare_core_reply_outbound_message(
                event, current, message, segment_index=segment_index)

        observer = getattr(getattr(self.staging, "session", None), "core_reply_observations", None)
        return {
            **connection_callbacks,
            "authorize": authorize_core_send,
            "on_denied": note_core_send_denied,
            "on_sent": observe_core_send,
            "on_delivery": observe_core_delivery,
            "on_reply_end": finish_core_reply_end,
            "prepare_send": prepare_core_voice,
            "media_submit": getattr(observer, "submit", None),
        }

    def _adopt_core_reply_delivery(self, event) -> bool:
        """Rebind an in-flight reply after AstrBot replaces this plugin instance."""
        state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        if not isinstance(state, dict):
            return False
        prepared = state.get("prepared")
        if not isinstance(prepared, Mapping):
            return False
        try:
            target = self.staging.target_for_event(event)
        except Exception:
            target = None
        owner = self._core_reply_owner_token()
        changed_owner = state.get("owner") is not owner
        if changed_owner:
            prepared = restore_core_reply_prepared(prepared)
        callbacks = self._core_reply_delivery_callbacks(event, prepared, target=target)
        if not adopt_core_reply_event(event, owner=owner, prepared=prepared, **callbacks):
            return False
        if prepared.get("channel") == "group":
            try:
                application = getattr(getattr(self.staging, "session", None), "application", None)
                participation = getattr(getattr(application, "tools", None), "group_participation", None)
                admission = getattr(participation, "admission", None)
                observer = getattr(getattr(self.staging, "session", None), "core_reply_observations", None)
                if admission is not None:
                    adopt_core_reply_tracking(
                        event, admission=admission, now=self.staging.now,
                        submit=getattr(observer, "submit", None), prepared=prepared,
                        reopen=changed_owner)
            except Exception:
                logger.warning("[CompanionV2] 核心群回复跟踪接管失败", exc_info=True)
        return True

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE | filter.EventMessageType.GROUP_MESSAGE,
                               priority=COMPANION_INTAKE_PRIORITY)
    async def receive_companion_message(self, event: AstrMessageEvent):
        if is_notice_event(event):
            return
        competing = competing_command_handler(event, own_module=__name__)
        if competing is not None:
            logger.debug("[CompanionV2] 放行其他插件指令 handler=%s",
                         getattr(competing, "handler_full_name", "unknown"))
            return
        # Ordinary listeners (e.g. prefix-free sign-in) run before intake.
        # A plugin may answer without stopping propagation; respect that reply,
        # unless upstream astrbot_plugin_hear_voice（兼容 _gemini_stt_*）explicitly
        # declared a non-hijacking turn (e.g. notify_pass).
        if getattr(event, "_has_send_oper", False) and not _is_explicit_non_hijacking_external_stt(event):
            return
        if _is_external_stt_cache_only(event):
            suppress_default_reply(event)
            logger.debug("[CompanionV2] 外部 hear_voice/gemini_stt 群语音概率门控设为仅缓存，跳过本轮回复")
            return
        if _should_skip_hijacked_external_stt(event):
            logger.debug("[CompanionV2] 外部 hear_voice/gemini_stt 已接管本轮语音回复，跳过重复受理")
            return
        if not self.staging.claims(event):
            # 不是本插件的目标：这条消息从来没有进入它的预期范围，回复与否都不是
            # 它的失败，既不写台账也不出日志——平台日志不该被别人的群消息刷屏。
            if self._claims_target(event):
                if self.staging_enabled():
                    # 目标确实是本插件的，只是这一刻没能受理（停机中、会话不在运行、
                    # 启动失败）。接不了这条本该由它回的消息，是预期未回。
                    self._record_no_reply(event, self._admission_reason(), stage="admission",
                                          origin_key=self._origin_key_for_event(event))
                return
            # 不属于任何目标（未绑定、也没配进来）的私聊：默认什么都不做，
            # 消息留给 AstrBot 自己的管线。只有配置里明确打开"婉拒陌生号"时，
            # 才由插件回一句固定文案，并关闭这一轮的默认模型回复。
            # 其他插件仍能继续检测这条消息。
            def observe_stranger_outcome(outcome):
                reason = outcome.get("reason_code", "stranger_notice_not_sent")
                if outcome.get("state") == "no_reply" and reason in STRANGER_NOTICE_FAILURES:
                    # 打开了婉拒、也确实该发：发不出去才是预期未回。
                    self._record_no_reply(event, reason, channel="private", stage="stranger_notice")
                elif outcome.get("state") == "replied":
                    logger.info("[CompanionV2] 陌生私聊婉拒已投递 event_ref=%s",
                                uuid4().hex)

            receipt = await refuse_stranger(self.staging, event, on_outcome=observe_stranger_outcome)
            if receipt is not None:
                suppress_default_reply(event)
            return
        try:
            target = self.staging.target_for_event(event)
        except Exception as error:
            logger.warning("[CompanionV2] V2 原生路由暂不可用，继续 AstrBot 核心回复 error_type=%s",
                           type(error).__name__)
            return
        if target is None:
            return
        # AstrBot owns the persona and reply; V2 contributes optional background.
        event_ref = uuid4().hex
        origin_key = self._origin_key_for_event(event)
        kind = getattr(getattr(getattr(event, "message_obj", None), "type", None), "value", None)
        channel = "private" if kind == "FriendMessage" else "group" if kind == "GroupMessage" else "unknown"
        if channel != "unknown":
            # This turn belongs to a conversation the plugin serves, even when V2
            # later declines it and AstrBot's own pipeline writes the reply. The
            # request-history and tool guards need that answer before any decision
            # is made, because a declined turn is exactly where the model echoes a
            # companion tool name our own history taught it.
            mark_managed_turn(event, channel=channel, event_ref=event_ref)
        if channel == "private" and self._private_delivery_duplicate(event, target):
            # AstrBot can surface one platform message twice (adapter reconnect,
            # duplicate file acknowledgement).  The first delivery already owns
            # the reply; the duplicate must not start a second model turn.
            suppress_default_reply(event)
            logger.info("[CompanionV2] 重复投递已忽略 event_ref=%s message_id=%s", event_ref,
                        getattr(getattr(event, "message_obj", None), "message_id", ""))
            self._record_no_reply(event, "duplicate_private_delivery", origin_key=origin_key,
                                  channel=channel, event_ref=event_ref, stage="intake", expected=False)
            return
        if channel == "private":
            now = self.staging.now()
            mentions, media = self._harassment_features(event)
            verdict = self._harassment_guard().check(
                self.staging.runtime, scope=self._harassment_scope(event),
                user_id=self._harassment_user(event),
                text=str(getattr(event, "message_str", "") or ""),
                mentions=mentions, media=media,
                now=now.timestamp() if hasattr(now, "timestamp") else None)
            if verdict.get("blocked"):
                notice = verdict.get("notice") or ""
                if notice:
                    try:
                        from astrbot.core.message.components import Plain
                        from astrbot.core.message.message_event_result import MessageChain
                        await event.send(MessageChain([Plain(notice)]))
                    except Exception:
                        pass
                # 名单在决策页管理；命中后本条不再进入模型或记忆。
                suppress_default_reply(event)
                return
        if channel == "private" and getattr(target, "participant_id", None):
            processor = getattr(self.staging, "process_private_relay_receipts", None)
            if callable(processor):
                await processor(event, target)
        if channel == "group":
            # 群 LLM 开关必须先于候选准入处理：封锁期间普通消息不再请求模型，
            # 但「开启 LLM」这条命令本身仍要能执行并回执。
            try:
                group_command = await receive_group_llm_command(self.staging, event, target=target)
            except Exception as error:
                group_command = None
                logger.warning("[CompanionV2] 群 LLM 指令处理异常 event_ref=%s error_type=%s",
                               event_ref, type(error).__name__)
            if group_command is not None:
                manager = getattr(self, "_group_debounce", None)
                if manager is not None:
                    manager.observe(str(getattr(target, "target_id", "") or ""), "", eligible=False)
                suppress_default_reply(event)
                if group_command.failure is not None:
                    self._record_no_reply(event, group_command.failure.code, origin_key=origin_key,
                                          channel=channel, event_ref=event_ref, stage="command")
                else:
                    logger.info("[CompanionV2] 群 LLM 指令由专用流程处理 event_ref=%s", event_ref)
                return
            block = (GroupLLMBlock(self.staging.runtime, now=self.staging.now)
                     if getattr(self.staging, "runtime", None) is not None else None)
            if block is not None and block.status_for_target(target) is not None:
                manager = getattr(self, "_group_debounce", None)
                if manager is not None:
                    manager.observe(str(getattr(target, "target_id", "") or ""), "", eligible=False)
                suppress_default_reply(event)
                self._record_no_reply(event, "group_llm_blocked", origin_key=origin_key,
                                      channel=channel, event_ref=event_ref, stage="admission")
                return
        if channel == "private":
            phone_command = await receive_phone_confirmation(self.staging, event, target=target)
            if phone_command is not None:
                suppress_default_reply(event)
                if phone_command.failure is not None:
                    self._record_no_reply(event, phone_command.failure.code, origin_key=origin_key,
                                          channel=channel, event_ref=event_ref, stage="command")
                return
            # 全部重置必须在建批次之前执行并直发回执：窗口重置会取消在途批次。
            try:
                reset_command = await receive_reset_command(self.staging, event, target=target)
            except Exception as error:
                reset_command = None
                logger.warning("[CompanionV2] 全部重置指令处理异常 event_ref=%s error_type=%s",
                               event_ref, type(error).__name__)
            if reset_command is not None:
                suppress_default_reply(event)
                if reset_command.failure is not None:
                    self._record_no_reply(event, reset_command.failure.code, origin_key=origin_key,
                                          channel=channel, event_ref=event_ref, stage="command")
                else:
                    logger.info("[CompanionV2] 全部重置由专用流程处理 event_ref=%s", event_ref)
                return
        is_calendar_command = (channel == "private"
                               and parse_calendar_command(str(getattr(event, "message_str", "") or "")))
        debounce_input = (private_debounce_input(event)
                          if channel == "private" and not is_calendar_command else None)
        plain_text = debounce_input[0] if debounce_input is not None else None
        event_has_forward = (debounce_input[1] if debounce_input is not None
                             else event_has_top_level_forwards(event))
        forward_policy = (resolve_forward_intake_policy(self.staging)
                          if event_has_forward else None)
        target_key = getattr(target, "target_id", None) if channel == "private" else None
        if (plain_text is not None and isinstance(target_key, str) and target_key
                and self._private_reply_followups().active_for(target_key) is not None):
            if event_has_forward:
                await parse_event_forwards(
                    event, policy=forward_policy,
                    native_context=getattr(self.staging, "context", None),
                )
            if self._fold_private_follow_up(
                    event, target, event_ref, plain_text, allow_empty=event_has_forward):
                return
        debounce_manager = None
        debounce_pending = None
        if plain_text is not None and isinstance(target_key, str) and target_key:
            debounce_manager = self._private_debounce_manager()
            debounce_pending = debounce_manager.reserve(target_key, event, plain_text)
        forward_parse_task = None
        if event_has_forward:
            if debounce_pending is not None:
                forward_parse_task = ensure_event_forwards_parsed(
                    event, policy=forward_policy,
                    native_context=getattr(self.staging, "context", None),
                )
            else:
                await parse_event_forwards(
                    event, policy=forward_policy,
                    native_context=getattr(self.staging, "context", None),
                )
        group_debounce_manager = None
        group_pending = None
        group_debounced_entries = None
        if channel == "group":
            group_target = str(getattr(target, "target_id", "") or "")
            group_text = group_plain_text(event)
            try:
                group_sender = group_event_route(event).sender_id if group_text is not None else ""
            except Exception:
                group_sender = ""
                group_text = None
            group_debounce_manager = self._group_debounce_manager()
            group_debounce_manager.observe(group_target, group_sender,
                                           eligible=group_text is not None and bool(group_sender))
            if group_text is not None:
                group_pending = group_debounce_manager.reserve_group(
                    group_target, group_sender, event, group_text)
            if group_pending is not None:
                try:
                    status = await group_debounce_manager.wait_for_quiet(group_pending, event)
                    if status == "ready":
                        group_debounced_entries = await group_debounce_manager.finish(group_pending, event)
                        if group_debounced_entries is None:
                            status = "superseded"
                except asyncio.CancelledError:
                    await group_debounce_manager.abort(group_pending, event=event)
                    raise
                if status == "superseded":
                    if not group_debounce_manager.superseded_by_same_event(group_pending, event):
                        suppress_default_reply(event)
                    return
                if status in {"closed", "aborted", "finished"}:
                    suppress_default_reply(event)
                    return
                if group_debounced_entries and len(group_debounced_entries) > 1:
                    merged_text = group_debounce_manager.combined(group_pending, group_debounced_entries)
                    if not replace_group_plain_text(event, merged_text):
                        suppress_default_reply(event)
                        logger.warning("[CompanionV2] 群聊连续输入合并失败 target=%s", group_target)
                        return
                    logger.info("[CompanionV2] 群聊连续输入合并 target=%s count=%d",
                                group_target, len(group_debounced_entries))
        group_admission = None
        if channel == "group":
            try:
                candidate_kwargs = {}
                if group_debounced_entries is not None and len(group_debounced_entries) > 1:
                    candidate_kwargs["merged_event_ids"] = tuple(
                        message_id for item in group_debounced_entries[:-1]
                        if (message_id := str(getattr(getattr(item[0], "message_obj", None),
                                                     "message_id", "") or "")))
                    candidate_kwargs["latest_native_text"] = group_debounced_entries[-1][1]
                candidate = await self.staging.group_reply_candidate(event, **candidate_kwargs)
                if candidate.failure is not None:
                    if (candidate.failure.code in GROUP_INPUT_PASSTHROUGH_FAILURES
                            or _can_continue_astrbot_core_reply(candidate.failure.code)
                            or candidate.failure.code == "host_group_preflight_failed"):
                        logger.info("[CompanionV2] V2 群回复材料暂不可用，继续 AstrBot 核心处理 event_ref=%s code=%s",
                                    event_ref, candidate.failure.code)
                        await self._guard_passthrough_core_reply(event, event_ref=event_ref, channel=channel)
                        return
                    suppress_default_reply(event)
                    if candidate.failure.code == "host_text_invalid":
                        logger.debug("[CompanionV2] 群回复候选跳过无正文消息 event_ref=%s code=%s",
                                     event_ref, candidate.failure.code)
                    else:
                        logger.warning("[CompanionV2] 群回复候选检查失败 event_ref=%s code=%s",
                                       event_ref, candidate.failure.code)
                    return
                group_admission = candidate.unwrap()
            except Exception as error:
                logger.warning("[CompanionV2] 群回复候选检查异常，继续 AstrBot 核心处理 event_ref=%s error_type=%s",
                               event_ref, type(error).__name__)
                await self._guard_passthrough_core_reply(event, event_ref=event_ref, channel=channel)
                return
            if not group_admission["admitted"]:
                suppress_default_reply(event)
                # 群聊「故意不说话」不写台账（那是 SUPPRESSED 的语义），但也不能
                # 什么都不留：用户反馈「机器人不理我」时，这行是唯一能区分
                # 「判了不该说」和「根本没走到这里」的线索。只写码值，不带正文。
                logger.info("[CompanionV2] 群聊本轮保持沉默 event_ref=%s reason=%s mentioned=%s",
                            event_ref, group_admission.get("reason") or "role_choice",
                            bool(group_admission.get("mentioned")))
                return
            if group_debounced_entries is not None and len(group_debounced_entries) > 1:
                group_admission = dict(group_admission)
                group_admission["debounced_event_ids"] = candidate_kwargs["merged_event_ids"]
        logger.info("[CompanionV2] 核心回复准入检查 event_ref=%s channel=%s", event_ref, channel)
        try:
            reply_enabled = await self.staging.session_topology.reply_enabled(event)
        except Exception as error:
            reply_enabled = True
            logger.warning("[CompanionV2] 会话回复开关暂不可读，继续核心授权检查 event_ref=%s error_type=%s",
                           event_ref, type(error).__name__)
        except BaseException:
            if debounce_pending is not None:
                await debounce_manager.abort(debounce_pending, event=event)
            raise
        if not reply_enabled:
            aborted = True
            if debounce_pending is not None:
                aborted = await debounce_manager.abort(debounce_pending, event=event)
                if debounce_manager.superseded_by_same_event(debounce_pending, event):
                    return
            if aborted and forward_parse_task is not None and not forward_parse_task.done():
                forward_parse_task.cancel()
            suppress_default_reply(event)
            self._record_no_reply(event, "session_reply_disabled", origin_key=origin_key,
                                  channel=channel, event_ref=event_ref, turn=event_ref)
            return

        if channel == "group" and group_admission is not None and not group_admission.get("mentioned"):
            policy = group_admission.get("participation_policy") or {}
            reference = group_admission.get("question_reference") or {}
            logger.info("[CompanionV2] 非@群核心回复候选 event_ref=%s trigger=%s candidate_source=policy "
                        "question_enabled=%s interjection_enabled=%s question_score=%s question_threshold=%s",
                        event_ref, group_admission.get("reason") or "role_choice", policy.get("question_enabled"),
                        policy.get("interjection_enabled"), reference.get("score"), policy.get("question_threshold"))

        if is_calendar_command:
            suppress_default_reply(event)
            try:
                result = await self.staging.receive_core_command(event)
            except Exception as error:
                result = None
                logger.warning("[CompanionV2] 日程命令处理异常 event_ref=%s error_type=%s",
                               event_ref, type(error).__name__)
            if result is None or result.failure is not None:
                reason = result.failure.code if result is not None and result.failure is not None else "calendar_command_failed"
                self._record_no_reply(event, reason, origin_key=origin_key, channel=channel,
                                      event_ref=event_ref, stage="command", turn=event_ref)
            else:
                logger.info("[CompanionV2] 日程命令由专用流程处理 event_ref=%s", event_ref)
            return

        # Repeat candidates stay literal even if optional preparation fails.
        # The optional Laya decision may keep listening, but the main reply
        # model must never receive a repeat candidate as ordinary conversation.
        if channel == "group" and group_admission is not None and group_admission.get("reason") == "repeat":
            suppress_default_reply(event)
        core_reply_plan = None
        debounced_entries = None
        if debounce_pending is not None:
            try:
                prepare_context = getattr(self.staging, "prepare_core_reply_context", None)
                context_result = None
                if callable(prepare_context):
                    try:
                        context_result = await prepare_context(event, target, group_admission=None)
                    except Exception as error:
                        logger.warning("[CompanionV2] 私聊防抖上下文读取失败，保留原核心回复 event_ref=%s error_type=%s",
                                       event_ref, type(error).__name__)
                if context_result is not None and context_result.failure is None:
                    core_reply_plan = context_result.unwrap()
                    policy = getattr(core_reply_plan, "debounce", None)
                else:
                    policy = None
                burst_has_forwards = event_has_forward or any(
                    event_has_top_level_forwards(item[0])
                    for item in debounce_pending.pending.entries
                )
                effective_policy = forward_burst_debounce_policy(
                    policy, has_forwards=burst_has_forwards)
                status = await debounce_manager.configure(debounce_pending, event, effective_policy)
                if status == "configuring":
                    status = await debounce_manager.wait_for_resolution(debounce_pending, event)
                if status == "active":
                    status = await debounce_manager.wait_for_quiet(debounce_pending, event)
                    if status == "ready":
                        debounced_entries = await debounce_manager.finish(debounce_pending, event)
                        if debounced_entries is None:
                            status = "superseded"
                    if status == "ready" and debounced_entries:
                        merged_text = debounce_manager.combined(debounce_pending, debounced_entries)
                        aggregated_forwards = await coalesce_debounced_forwards(
                            event, debounced_entries,
                            policy=forward_policy or resolve_forward_intake_policy(self.staging),
                            native_context=getattr(self.staging, "context", None),
                        )
                        if not apply_debounced_private_merge(
                                event, merged_text, has_forwards=aggregated_forwards is not None):
                            suppress_default_reply(event)
                            self._record_no_reply(event, "private_debounce_merge_failed", origin_key=origin_key,
                                                  channel="private", event_ref=event_ref, stage="intake",
                                                  turn=event_ref)
                            logger.warning("[CompanionV2] 私聊防抖合并失败 event_ref=%s", event_ref)
                            return
                        if core_reply_plan is not None:
                            plan_text = combine_text_and_forward_summary(merged_text, aggregated_forwards)
                            core_reply_plan = self._core_reply_plan_with_text(core_reply_plan, plan_text)
                if status == "bypass" and forward_parse_task is not None:
                    parsed = await await_event_forwards_parsed(
                        event, policy=forward_policy,
                        native_context=getattr(self.staging, "context", None),
                    )
                    if core_reply_plan is not None and parsed is not None:
                        plan_text = combine_text_and_forward_summary(plain_text or "", parsed)
                        core_reply_plan = self._core_reply_plan_with_text(core_reply_plan, plan_text)
                if status == "superseded":
                    if forward_parse_task is not None:
                        await await_event_forwards_parsed(
                            event, policy=forward_policy,
                            native_context=getattr(self.staging, "context", None),
                        )
                    self._suppress_debounced_private_event(
                        event, target, event_ref, debounce_manager, debounce_pending)
                    return
                if status == "finished":
                    if forward_parse_task is not None:
                        await await_event_forwards_parsed(
                            event, policy=forward_policy,
                            native_context=getattr(self.staging, "context", None),
                        )
                    self._suppress_debounced_private_event(
                        event, target, event_ref, debounce_manager, debounce_pending,
                        reason="private_debounce_cancelled")
                    return
                if status in {"closed", "aborted"}:
                    if forward_parse_task is not None and not forward_parse_task.done():
                        forward_parse_task.cancel()
                    self._suppress_debounced_private_event(
                        event, target, event_ref, debounce_manager, debounce_pending,
                        reason="private_debounce_cancelled")
                    return
            except BaseException:
                aborted = await debounce_manager.abort(debounce_pending, event=event)
                if aborted and forward_parse_task is not None and not forward_parse_task.done():
                    forward_parse_task.cancel()
                raise
        if (channel == "group" and group_admission is not None
                and group_admission.get("reason") != "repeat"):
            debounce_log.wake(
                "group", group_admission.get("reason") or "role_choice",
                getattr(event, "message_str", ""),
                target=getattr(target, "target_id", ""),
            )
        try:
            prepare_kwargs = {"group_admission": group_admission}
            if core_reply_plan is not None:
                prepare_kwargs["plan"] = core_reply_plan
            admission = await self.staging.prepare_core_reply(event, target, **prepare_kwargs)
        except Exception as error:
            logger.warning("[CompanionV2] V2 回复材料准备失败，继续 AstrBot 核心回复 event_ref=%s error_type=%s",
                         event_ref, type(error).__name__)
            await self._guard_passthrough_core_reply(event, event_ref=event_ref, channel=channel)
            return
        if admission.failure is not None:
            failure = admission.failure
            if _can_continue_astrbot_core_reply(failure.code):
                logger.warning("[CompanionV2] V2 回复材料暂不可用，继续 AstrBot 核心回复 event_ref=%s code=%s",
                               event_ref, failure.code)
                await self._guard_passthrough_core_reply(event, event_ref=event_ref, channel=channel)
                return
            suppress_default_reply(event)
            if failure.code == "private_access_dialogue_denied":
                # Only this failed preparation owns the refused context; never
                # let a concurrent event's notice or cooldown stand in for it.
                await self.staging.refuse_core_reply(event, getattr(admission, "refusal", None))
            self._record_no_reply(event, failure.code, origin_key=origin_key,
                                  channel=channel, event_ref=event_ref, stage="admission",
                                  turn=event_ref, code=failure.code)
            logger.warning("[CompanionV2] 核心回复未获准 event_ref=%s code=%s",
                           event_ref, failure.code)
            return

        prepared = dict(admission.unwrap())
        prepared["event_ref"] = event_ref
        prepared["origin_key"] = origin_key
        prepared["channel"] = channel
        if debounced_entries is not None and len(debounced_entries) > 1:
            prepared["debounced_inputs"] = merged_input_sequence(debounced_entries)
            prepared["debounced_message_count"] = len(debounced_entries)
        if group_debounced_entries is not None and len(group_debounced_entries) > 1:
            prepared["debounced_inputs"] = merged_input_sequence(group_debounced_entries)
            prepared["debounced_message_count"] = len(group_debounced_entries)
        if group_admission is not None:
            prepared["group_admission"] = dict(group_admission)
        application = getattr(getattr(self.staging, "session", None), "application", None)
        decision = await prepare_core_reply_decision(event, prepared, application=application, now=self.staging.now)
        closing = {"suppress_reply": False, "action": "reply", "reason": "not_private"}
        if channel == "private":
            try:
                closing = prepare_private_closing_decision(
                    event, prepared, application=application, now=self.staging.now)
            except Exception as error:
                logger.warning("[CompanionV2] 私聊收尾判断暂不可用，继续核心回复 event_ref=%s error_type=%s",
                               event_ref, type(error).__name__)
        suppress_reply = bool(decision.get("suppress_reply") or closing.get("suppress_reply"))

        if (channel == "group" and group_admission is not None and group_admission.get("reason") == "repeat"
                and not suppress_reply):
            suppress_default_reply(event)
            self._observe_session(event)
            try:
                result = await self.staging.receive_group_repeat(event, group_admission, prepared=prepared)
                if result.failure is not None:
                    self._record_no_reply(event, result.failure.code, origin_key=origin_key,
                                          channel=channel, event_ref=event_ref, stage="repeat", turn=event_ref)
                else:
                    outcome = result.unwrap()
                    logger.debug("[CompanionV2] 群复读判定 event_ref=%s action=%s state=%s reason=%s",
                                 event_ref, outcome.get("action"), outcome.get("state"), outcome.get("reason"))
            except Exception as error:
                logger.warning("[CompanionV2] 群复读处理异常 event_ref=%s error_type=%s",
                               event_ref, type(error).__name__)
                self._record_no_reply(event, "group_repeat_failed", origin_key=origin_key,
                                      channel=channel, event_ref=event_ref, stage="repeat", turn=event_ref)
            finally:
                self._observe_core_phase(prepared, "completed")
            return

        prepare_core_reply_event(
            event, prepared, owner=self._core_reply_owner_token(),
            **self._core_reply_delivery_callbacks(event, prepared, target=target))
        observer = getattr(getattr(self.staging, "session", None), "core_reply_observations", None)
        media = getattr(observer, "media", None)
        if callable(media):
            try:
                media(event, prepared)
            except Exception:
                pass
        if channel == "group" and group_admission is not None:
            application = getattr(getattr(self.staging, "session", None), "application", None)
            participation = getattr(getattr(application, "tools", None), "group_participation", None)
            observer = getattr(getattr(self.staging, "session", None), "core_reply_observations", None)
            if participation is not None:
                prepare_core_reply_tracking(
                    event, prepared, admission=participation.admission, now=self.staging.now,
                    reason=group_admission.get("reason", ""), mentioned=group_admission.get("mentioned", False),
                    submit=getattr(observer, "submit", None))
        prepare_core_reply_segmentation(event)
        self._observe_session(event)
        if suppress_reply:
            suppress_default_reply(event)
            if decision.get("suppress_reply"):
                action = decision["applied_decision"]["action"]
                observe_core_group_participation(event, {
                    "mode": "skip", "reason": "Laya 判断本轮行动为 " + action,
                    "act": "", "signature": "", "source": "laya", "decision_action": action})
                reason_code, stage = "laya_" + action, "decision"
            else:
                action = str(closing.get("action") or "silent")
                reason_code = "private_closing_" + str(closing.get("reason") or "closing")
                stage = "closing"
            finish_core_reply_tracking(event)
            observe_core_reply_suppressed(event, prepared, application=application,
                submit=getattr(observer, "submit", None), now=self.staging.now)
            self._observe_core_phase(prepared, "completed")
            self._record_no_reply(event, reason_code, origin_key=origin_key,
                channel=channel, event_ref=event_ref, stage=stage, turn=prepared.get("turn_id"), expected=False)
            restore_core_reply_event(event)
            if decision.get("suppress_reply"):
                logger.info("[CompanionV2] Laya 决策跳过主模型 event_ref=%s turn_id=%s action=%s",
                            event_ref, prepared.get("turn_id"), action)
            else:
                logger.info("[CompanionV2] 私聊收尾判断跳过主模型 event_ref=%s turn_id=%s action=%s reason=%s",
                            event_ref, prepared.get("turn_id"), action, closing.get("reason"))
            return
        prepare_core_reply_streaming_boundary(event)
        if (channel == "group"
                and (prepared.get("group_trigger") or {}).get("bare_mention")
                and not str(getattr(event, "message_str", "") or "").strip()):
            # AstrBot's aiocqhttp adapter strips the leading self-@ from
            # message_str; keep event.message_str clean via an explicit
            # conversation-bound request so _apply_kb() never embeds
            # "<提及自己>", falling back only if no session conversation can be bound.
            if not await prepare_bare_mention_request(
                    event, native_context=getattr(self.staging, "context", None)):
                event.message_str = SELF_MENTION
                if getattr(event, "message_obj", None) is not None:
                    event.message_obj.message_str = SELF_MENTION
        if get_parsed_forwards(event) is not None:
            if (not str(getattr(event, "message_str", "") or "").strip()
                    and not event_has_media_or_reply(event)):
                if not await prepare_forward_only_request(
                        event, native_context=getattr(self.staging, "context", None)):
                    event.message_str = "[合并转发聊天记录]"
                    if getattr(event, "message_obj", None) is not None:
                        event.message_obj.message_str = "[合并转发聊天记录]"
            claim_core_reply_forward_injection(event)
        guard_outcome = await self._enter_core_reply_followup_guard(
            event, event_ref=event_ref, channel=channel, prepared=prepared)
        if guard_outcome.get("reason") in {"stopped", "closed"}:
            if guard_outcome.get("reason") == "closed":
                suppress_default_reply(event)
            cleanup_reason = (
                "wait_stopped" if guard_outcome.get("reason") == "stopped" else "wait_closed"
            )
            self._cleanup_captured_core_followup(
                event, prepared, event_ref=event_ref, channel=channel, reason=cleanup_reason)
            return
        event.is_wake = True
        event.is_at_or_wake_command = True
        if (channel == "private" and not guard_outcome.get("had_active_turn")
                and event_will_enter_astrbot_llm(event)
                and (plain_text is not None or bool(str(prepared.get("text") or "").strip()))):
            entries = (debounced_entries if debounced_entries is not None else
                       ((event, str(prepared.get("text") or event.message_str or ""),
                         getattr(event, "created_at", None)),))
            if not any(len(entry) >= 2 and str(entry[1]).strip() for entry in entries):
                entries = ((event, str(prepared.get("text") or event.message_str or "[合并转发聊天记录]"),
                            getattr(event, "created_at", None)),)
            self._private_reply_followups().start(
                target_key or getattr(target, "target_id", ""), event, event_ref, entries)
        logger.info("[CompanionV2] 交由 AstrBot 核心回复管线 event_ref=%s channel=%s",
                    event_ref, channel)

    def _video_fusion(self):
        fusion = getattr(self, "_video_fusion_service", None)
        if fusion is None:
            from .astrbot_host.video.fusion import VideoFusionService

            fusion = VideoFusionService()
            self._video_fusion_service = fusion
        return fusion

    async def _route_core_video_to_image_provider(self, event):
        from .astrbot_host.core_reply_perception import event_has_video
        from .astrbot_host.core_reply_video_sources import key_from_context, peek
        from companion_v2.conversation.media.understanding import media_scope_enabled

        state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        prepared = state.get("prepared") if isinstance(state, dict) else None
        context = prepared.get("context") if isinstance(prepared, Mapping) else None
        scope = getattr(context, "scope", None)
        if getattr(getattr(scope, "kind", None), "value", None) not in ("private", "group"):
            return
        application = getattr(getattr(self.staging, "session", None), "application", None)
        settings = self._video_understanding_settings(application, context)
        if settings.get("video_understanding_enabled") is not True or not media_scope_enabled(
                settings, "video_understanding", scope):
            return
        include_quoted = bool(settings.get("video_understanding_quoted_enabled", True))
        if not event_has_video(event, include_quoted=include_quoted) and not peek(key_from_context(context)):
            return
        native = getattr(self.staging, "context", None)
        if native is None:
            return
        getter = getattr(native, "get_provider_by_id", None)
        all_providers = getattr(native, "get_all_providers", None)
        config_getter = getattr(native, "get_config", None)
        if not all(callable(method) for method in (getter, all_providers, config_getter)):
            return
        try:
            from astrbot.core.astr_main_agent import _provider_supports_modality
        except ModuleNotFoundError as error:
            if error.name != "astrbot.core.astr_main_agent":
                raise

            def _provider_supports_modality(provider, modality):
                config = getattr(provider, "provider_config", None)
                modalities = config.get("modalities") if isinstance(config, Mapping) else None
                return not modalities or isinstance(modalities, list) and modality in modalities

        selected = event.get_extra("selected_provider")
        current = getter(selected) if isinstance(selected, str) and selected else None
        if current is None:
            resolver = getattr(native, "get_using_provider_async", None)
            if callable(resolver):
                current = await resolver(umo=getattr(event, "unified_msg_origin", None))
        if current is not None and _provider_supports_modality(current, "image"):
            return
        host_config = config_getter(umo=getattr(event, "unified_msg_origin", None))
        provider_settings = host_config.get("provider_settings", {})
        agent_settings = host_config.get("agent_runner", {}).get("config", {})
        model_settings = agent_settings.get("model", {})
        preferred = provider_settings.get("default_image_caption_provider_id", "")
        fallbacks = model_settings.get("fallback_provider_ids", ())
        candidate_ids = (preferred, *(fallbacks if isinstance(fallbacks, (list, tuple)) else ()))
        loaded = all_providers()
        for provider_id in candidate_ids:
            candidate = getter(provider_id) if isinstance(provider_id, str) and provider_id else None
            if candidate is not None and candidate in loaded and _provider_supports_modality(candidate, "image"):
                event.set_extra("selected_provider", provider_id)
                logger.info("[CompanionV2] 本轮视频切换到视觉模型 event_ref=%s provider=%s",
                            prepared.get("event_ref"), provider_id)
                return
        logger.warning("[CompanionV2] 本轮视频没有可用的图片输入模型 event_ref=%s",
                       prepared.get("event_ref"))

    async def _route_core_peak_provider(self, event):
        """Apply a saved time-window replacement to this core reply only.

        The core reads ``selected_provider`` from the event before resolving the
        provider, so a peak rule can switch this turn's model without rewriting
        the session's saved model (or whatever the dashboard selected).
        """
        if event.get_extra("selected_provider"):
            # Video routing already chose a vision provider for this turn.
            return
        state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        prepared = state.get("prepared") if isinstance(state, dict) else None
        if not isinstance(prepared, Mapping):
            return
        context = prepared.get("context")
        application = getattr(getattr(self.staging, "session", None), "application", None)
        if context is None or application is None:
            return
        if not hasattr(context, "scope") or not hasattr(context, "actor"):
            return
        from companion_v2.configuration.models import ConfigLayer, ConfigTarget
        from companion_v2.model_gateway.replacement_rules import checked_rules, match_route
        at = application.tasks.now()
        target = ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW,
                              context.role_binding.role, context.scope)
        snapshot = application.configuration.read(context.actor, target=target,
            group_id="model.conversation.reply", observed_at=at)
        if not snapshot.is_success:
            return
        snapshot = snapshot.unwrap()
        values = {name.rsplit(".", 1)[1]: item.value for name, item in snapshot.fields.items()}
        if values.get("replacement_scope") == "plugin":
            # The saved scope says these rules belong to plugin tasks only.
            return
        try:
            rules = checked_rules(values.get("replacement_rules", "[]"))
        except ValueError:
            return
        match = match_route(rules, str(prepared.get("text") or ""), at)
        if match is None or not match.provider_id:
            return
        native = getattr(self.staging, "context", None)
        getter = getattr(native, "get_provider_by_id", None)
        provider = getter(match.provider_id) if callable(getter) else None
        if provider is None:
            logger.warning("[CompanionV2] 时段替换的 Provider 不可用 event_ref=%s provider=%s",
                           prepared.get("event_ref"), match.provider_id)
            return
        event.set_extra("selected_provider", match.provider_id)
        logger.info("[CompanionV2] 时段替换对话模型 event_ref=%s provider=%s rule=%s reason=%s",
                    prepared.get("event_ref"), match.provider_id, match.rule_id, match.reason)

    @filter.on_waiting_llm_request(priority=-1000000)
    async def observe_companion_core_waiting_llm_request(self, event: AstrMessageEvent):
        observe_core_waiting_llm_request(event, now=self.staging.now)
        try:
            await self._route_core_video_to_image_provider(event)
        except Exception as error:
            logger.warning("[CompanionV2] 视频视觉路由暂不可用 error_type=%s", type(error).__name__)
        try:
            await self._route_core_peak_provider(event)
        except Exception as error:
            logger.warning("[CompanionV2] 时段替换暂不可用 error_type=%s", type(error).__name__, exc_info=True)

    @filter.on_llm_request(priority=250000)
    async def prepare_companion_core_request(self, event: AstrMessageEvent, req):
        # Repair before the state check: an unconfigured plugin still runs this
        # hook, and leaving an orphan tool-call batch untouched makes the whole
        # AstrBot request fail with a 400 even though V2 takes no other action.
        try:
            removed = repair_tool_call_history(getattr(req, "contexts", None))
        except Exception:
            removed = 0
        if removed:
            logger.warning("[CompanionV2] 修复缺少工具结果的历史轮次 batches=%s", removed)
        state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        if not isinstance(state, dict):
            prepare_native_host_request(self.staging, event, req)
            return
        self._adopt_core_reply_delivery(event)
        self._apply_private_follow_ups_to_request(event, req)
        # Repair an unpaired tool-call batch before the provider sees it: a
        # missing tool result makes the whole request fail with a 400 and the
        # provider fallback then answers without tools at all.
        try:
            removed = repair_tool_call_history(getattr(req, "contexts", None))
        except Exception:
            removed = 0
        if removed:
            logger.warning("[CompanionV2] 修复缺少工具结果的历史轮次 batches=%s", removed)
        prepared = state.get("prepared")
        try:
            with measure_core_preparation(event, "request_authorization", now=self.staging.now):
                target = self.staging.target_for_event(event)
                result = await self.staging.authorize_core_reply(event, target, prepared)
        except Exception as error:
            result = None
            code = type(error).__name__
        else:
            code = result.failure.code if result.failure is not None else ""
        if result is None or result.failure is not None:
            event_ref = prepared.get("event_ref") if isinstance(prepared, dict) else None
            origin_key = prepared.get("origin_key") if isinstance(prepared, dict) else None
            channel = prepared.get("channel") if isinstance(prepared, dict) else None
            self._record_no_reply(event, "core_reply_authorization_changed", origin_key=origin_key,
                                  channel=channel, event_ref=event_ref, stage="model_request",
                                  turn=event_ref, code=code or "authorization_denied")
            event.stop_event()
            return
        with measure_core_preparation(event, "prompt_injection", now=self.staging.now):
            inject_core_reply_perception_policy(event, req)
            inject_core_reply_prompt(event, req)
            inject_core_reply_forwards(event, req, prepared=prepared)
        mark_core_reply_inflight(event)
        application = getattr(getattr(self.staging, "session", None), "application", None)
        observer = getattr(getattr(self.staging, "session", None), "core_reply_observations", None)
        self._observe_core_phase(prepared, "running")
        classify_functional_command(event, channel=str(prepared.get("channel") or ""), own_module=__name__)
        if application is not None:
            try:
                with measure_core_preparation(event, "tool_injection", now=self.staging.now):
                    inject_core_reply_tools(event, req, application=application)
            except Exception as error:
                logger.warning("[CompanionV2] V2 工具暂不可用，保留 AstrBot 核心工具 error_type=%s",
                               type(error).__name__)
        prepare_native_host_request(self.staging, event, req)
        inject_core_turn_notes(event, req)
        inject_functional_command_instruction(
            event, req, channel=str(prepared.get("channel") or ""), own_module=__name__,
            application=application,
            context=prepared.get("context") if isinstance(prepared, dict) else None)
        with measure_core_preparation(event, "send_tool_injection", now=self.staging.now):
            inject_core_reply_send_tool(event, req, submit=getattr(observer, "submit", None))
        try:
            with measure_core_preparation(event, "perception_injection", now=self.staging.now):
                await inject_core_reply_perception_context(
                    event, req, native_context=getattr(self.staging, "context", None),
                    application=application)
        except Exception as error:
            logger.warning("[CompanionV2] 核心回复感知信息暂不可用，继续回复 error_type=%s",
                           type(error).__name__)
        if application is not None:
            try:
                with measure_core_preparation(event, "telemetry_capture", now=self.staging.now):
                    prepare_core_reply_telemetry(event, req, application=application,
                        submit=getattr(observer, "submit", None), now=self.staging.now)
            except Exception as error:
                logger.warning("[CompanionV2] 核心回复统计暂不可用，继续回复 error_type=%s", type(error).__name__)
        from .astrbot_host.core_reply_presentation import capture_core_reply_tool_names
        capture_core_reply_tool_names(event, req)

    @staticmethod
    def _append_core_reply_video_frames(parts, videos, *, start_number=1):
        from astrbot.core.agent.message import ImageURLPart, TextPart

        for number, video in enumerate(videos, start_number):
            parts.append(TextPart(text=(
                f"本轮视频 {number} 已按时间顺序抽取 {len(video.frames)} 帧画面。请根据这些画面回应；"
                "抽帧不等于看完整段视频，音轨未读取，不猜测帧间动作或声音。"
            )).mark_as_temp())
            for frame, descriptor in zip(video.frames, video.descriptor.frames):
                parts.append(TextPart(text=f"视频 {number}，抽样画面帧 {descriptor.index + 1}/{video.descriptor.frame_count}：")
                             .mark_as_temp())
                url = "data:image/png;base64," + base64.b64encode(frame.content).decode("ascii")
                parts.append(ImageURLPart(image_url=ImageURLPart.ImageURL(
                    url=url, id=f"companion-video-frame:{video.descriptor.position}:{descriptor.index}"
                )).mark_as_temp())

    @staticmethod
    async def _call_video_loader_with_budget(loader, target, *, maximum_bytes: int):
        """Pass ``maximum_bytes`` on the first call; fall back only for 1-arg test stubs."""
        try:
            return await loader(target, maximum_bytes=maximum_bytes)
        except TypeError as error:
            if "maximum_bytes" not in str(error) and "unexpected keyword" not in str(error):
                raise
            return await loader(target)

    async def _attach_core_reply_video_material(self, event, prepared, request, originals,
                                                application, context, settings, *,
                                                question="", fetch_errors=()):
        """Try visual video understanding (native Gemini / OpenAI relay + compression), then sample frames on failure."""
        from pathlib import Path
        from astrbot.core.agent.message import TextPart
        from companion_v2.conversation.media.models import MAX_TURN_BYTES, MAX_TURN_FRAMES
        from companion_v2.conversation.media.video.models import MAX_TURN_VIDEO_MS, MAX_VIDEOS, MAX_VIDEO_BYTES
        from .astrbot_host.core_reply_perception import neutralize_video_placeholder
        from .astrbot_host.video.container import inspect_container
        from .astrbot_host.video.core_understanding import (
            select_native_video_routes, summarize_original_video)
        from .astrbot_host.video.loader import decode_video

        parts = getattr(request, "extra_user_content_parts", None)
        if not isinstance(parts, list) or (not originals and not fetch_errors) or len(originals) > MAX_VIDEOS:
            return

        routes = ()
        if event is not None:
            try:
                routes = select_native_video_routes(
                    application, getattr(self.staging, "context", None), context,
                    event, self.staging.now())
            except Exception as error:
                logger.warning("[CompanionV2] 原生视频路由暂不可用 error_type=%s", type(error).__name__)

        fusion = self._video_fusion()
        staged = []
        pending_frame_videos = []
        fallback_source_bytes = 0
        native_count = 0

        for number, entry in enumerate(originals, 1):
            if len(entry) == 2:
                position, content = entry
                suffix, placeholder_obj = ".mp4", None
            else:
                position, content, suffix, placeholder_obj = entry
            ext = (Path(f"clip{suffix}").suffix or ".mp4").lower()
            if ext == ".ts":
                continue

            outcome = await fusion.analyze_video(
                content,
                suffix=ext,
                routes=routes,
                settings=settings,
                question=question,
                summarize_fn=summarize_original_video,
            )
            if outcome.status == "summary":
                staged.append(TextPart(text=(
                    f"<companion_video_native_summary>视频 {number} 的原视频理解转述"
                    "（模型直接读取整段视频，转述可能有误差；据此回应，不自行补造细节）：\n"
                    f"{outcome.text}\n</companion_video_native_summary>"
                )).mark_as_temp())
                if placeholder_obj is not None:
                    label = placeholder_obj.name or f"视频{number}"
                    neutralize_video_placeholder(
                        placeholder_obj.part,
                        f"[视频内容已由陪伴理解服务完成转述（文件名 {label}），请直接参考下方转述，无需再次读取原文件。]",
                    )
                native_count += 1
                continue

            if outcome.status == "rejected":
                staged.append(TextPart(text=(
                    f"<companion_video_notice>视频 {number} 未处理：{outcome.error_reason}</companion_video_notice>"
                )).mark_as_temp())
                if placeholder_obj is not None:
                    label = placeholder_obj.name or f"视频{number}"
                    neutralize_video_placeholder(
                        placeholder_obj.part,
                        f"[视频 {label} 超出配置限制未发送：{outcome.error_reason}]",
                    )
                continue

            decoded_video = None
            candidate = outcome.prepared_bytes or content
            if 0 < len(candidate) <= MAX_VIDEO_BYTES:
                try:
                    movie = await asyncio.to_thread(inspect_container, candidate)
                    if getattr(movie, "duration_ms", 0) <= MAX_TURN_VIDEO_MS:
                        decoded_video = await decode_video(candidate, position)
                        fallback_source_bytes += len(candidate)
                except Exception as error:
                    logger.warning("[CompanionV2] 视频抽帧失败 event_ref=%s position=%s code=%s",
                                   prepared.get("event_ref"), position,
                                   getattr(error, "code", type(error).__name__))
            if decoded_video is not None:
                # Defer appending frame parts and neutralizing placeholders until
                # after turn frame/byte budget enforcement so evicted frames never
                # leave misleading "已提取关键帧" placeholders or orphan captions.
                pending_frame_videos.append((number, decoded_video, placeholder_obj))
                continue

            reason = outcome.error_reason or "视频无法读取或抽帧。"
            staged.append(TextPart(text=(
                f"<companion_video_notice>视频 {number} 理解降级：{reason}</companion_video_notice>"
            )).mark_as_temp())
            if placeholder_obj is not None:
                label = placeholder_obj.name or f"视频{number}"
                neutralize_video_placeholder(
                    placeholder_obj.part,
                    f"[视频 {label} 理解失败（{reason}），切勿猜测或编造视频内容。]",
                )

        for index, placeholder_obj, error_reason in fetch_errors:
            label = (getattr(placeholder_obj, "name", "") if placeholder_obj is not None else "") or f"视频{index}"
            staged.append(TextPart(text=(
                f"<companion_video_notice>视频 {label} 获取失败：{error_reason}</companion_video_notice>"
            )).mark_as_temp())
            if placeholder_obj is not None:
                neutralize_video_placeholder(
                    placeholder_obj.part,
                    f"[视频 {label} 暂无法读取（{error_reason}），请如实告知对方。]",
                )

        total_frame_count = sum(len(video.frames) for _num, video, _ph in pending_frame_videos)
        total_frame_bytes = fallback_source_bytes + sum(
            len(frame.content)
            for _num, video, _ph in pending_frame_videos
            for frame in video.frames
        )
        attached_frame_count = 0
        if pending_frame_videos and (
                total_frame_bytes > MAX_TURN_BYTES or total_frame_count > MAX_TURN_FRAMES):
            logger.warning("[CompanionV2] 视频抽帧超过本轮预算 event_ref=%s", prepared.get("event_ref"))
            for number, _video, placeholder_obj in pending_frame_videos:
                staged.append(TextPart(text=(
                    f"<companion_video_notice>视频 {number} 理解降级：视频抽帧超出本轮上下文预算，未附带画面帧。</companion_video_notice>"
                )).mark_as_temp())
                if placeholder_obj is not None:
                    label = placeholder_obj.name or f"视频{number}"
                    neutralize_video_placeholder(
                        placeholder_obj.part,
                        f"[视频 {label} 抽帧超出本轮预算未能提供画面，切勿猜测或编造视频内容。]",
                    )
        else:
            for number, video, placeholder_obj in pending_frame_videos:
                self._append_core_reply_video_frames(staged, (video,), start_number=number)
                if placeholder_obj is not None:
                    label = placeholder_obj.name or f"视频{number}"
                    neutralize_video_placeholder(
                        placeholder_obj.part,
                        f"[视频 {label} 已由陪伴提取关键帧（共 {len(video.frames)} 帧），请直接参考画面帧。]",
                    )
            attached_frame_count = total_frame_count

        if staged:
            parts.extend(staged)
            logger.info("[CompanionV2] 视频已送入核心回复 event_ref=%s native=%s sampled_frames=%s",
                        prepared.get("event_ref"), native_count, attached_frame_count)

    async def _attach_core_reply_videos(self, prepared, request, *, event=None):
        """把上一条群消息里被文本化丢弃的视频补挂进这一轮请求。"""
        from .astrbot_host.core_reply_video_sources import key_from_context, take
        state = prepared if isinstance(prepared, Mapping) else {}
        context = state.get("context")
        scope = getattr(context, "scope", None)
        if scope is None or getattr(getattr(scope, "kind", None), "value", None) != "group":
            return
        locators = take(key_from_context(context))
        if not locators:
            return
        from .astrbot_host.core_reply_perception import extract_video_question, video_request_state
        if video_request_state(request) in ("native_summary", "sampled_frames", "native_and_sampled", "external_summary"):
            return
        application = getattr(getattr(self.staging, "session", None), "application", None)
        settings = self._video_understanding_settings(application, context)
        if settings.get("video_understanding_enabled") is not True:
            return
        from companion_v2.conversation.media.understanding import media_scope_enabled
        if not media_scope_enabled(settings, "video_understanding", scope):
            return
        from companion_v2.conversation.media.video.models import MAX_VIDEOS
        from .astrbot_host.video.fusion import MAX_CORE_VIDEO_DOWNLOAD_BYTES
        from .astrbot_host.video.loader import fetch_native_video_bytes
        from .astrbot_host.video.openai_compat_video import sanitize_video_error
        originals = []
        fetch_errors = []
        for idx, locator in enumerate(locators[:MAX_VIDEOS], 1):
            try:
                raw = await self._call_video_loader_with_budget(
                    fetch_native_video_bytes,
                    locator,
                    maximum_bytes=MAX_CORE_VIDEO_DOWNLOAD_BYTES,
                )
                originals.append((locator.position, raw, ".mp4", None))
            except Exception as error:
                logger.warning("[CompanionV2] 群聊视频补挂跳过 position=%s code=%s",
                               getattr(locator, "position", None),
                               getattr(error, "code", type(error).__name__))
                fetch_errors.append((idx, None, sanitize_video_error(error)))
        question = extract_video_question(event, request, state)
        await self._attach_core_reply_video_material(event, state, request, originals,
                                                     application, context, settings,
                                                     question=question, fetch_errors=fetch_errors)

    async def _attach_core_reply_current_videos(self, event, request):
        """Supply current or quoted host-resolved / remote ref videos to the core chat request."""
        state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        prepared = state.get("prepared") if isinstance(state, dict) else None
        context = prepared.get("context") if isinstance(prepared, Mapping) else None
        scope = getattr(context, "scope", None)
        if getattr(getattr(scope, "kind", None), "value", None) not in ("private", "group"):
            return
        from .astrbot_host.core_reply_perception import (
            extract_video_question, neutralize_video_placeholder,
            video_placeholders, video_request_state)

        if video_request_state(request) in ("native_summary", "sampled_frames", "native_and_sampled", "external_summary"):
            return
        application = getattr(getattr(self.staging, "session", None), "application", None)
        settings = self._video_understanding_settings(application, context)
        if settings.get("video_understanding_enabled") is not True:
            return
        from companion_v2.conversation.media.understanding import media_scope_enabled

        if not media_scope_enabled(settings, "video_understanding", scope):
            return
        include_quoted = bool(settings.get("video_understanding_quoted_enabled", True))
        placeholders = video_placeholders(request, include_quoted=include_quoted)
        if not placeholders:
            return
        from pathlib import Path
        from urllib.parse import urlparse
        from companion_v2.conversation.media.video.models import MAX_VIDEOS
        from .astrbot_host.video.fusion import MAX_CORE_VIDEO_DOWNLOAD_BYTES
        from .astrbot_host.video.loader import (
            fetch_placeholder_ref_video_bytes, read_host_resolved_video_bytes)
        from .astrbot_host.video.openai_compat_video import sanitize_video_error

        originals = []
        fetch_errors = []
        for position, item in enumerate(placeholders[:MAX_VIDEOS]):
            parsed_name = item.name or (urlparse(item.value).path if "://" in item.value else item.value)
            suffix = (Path(parsed_name).suffix or ".mp4").lower()
            try:
                if item.location == "path":
                    raw = await self._call_video_loader_with_budget(
                        read_host_resolved_video_bytes,
                        item.value,
                        maximum_bytes=MAX_CORE_VIDEO_DOWNLOAD_BYTES,
                    )
                else:
                    raw = await fetch_placeholder_ref_video_bytes(
                        item.value, maximum_bytes=MAX_CORE_VIDEO_DOWNLOAD_BYTES)
                originals.append((position, raw, suffix, item))
            except Exception as error:
                logger.warning("[CompanionV2] 当前视频读取失败 event_ref=%s position=%s code=%s",
                               prepared.get("event_ref"), position,
                               getattr(error, "code", type(error).__name__))
                fetch_errors.append((position + 1, item, sanitize_video_error(error)))
        for index, placeholder_obj in enumerate(placeholders[MAX_VIDEOS:], MAX_VIDEOS + 1):
            label = placeholder_obj.name or f"视频{index}"
            neutralize_video_placeholder(
                placeholder_obj.part,
                f"[视频 {label} 超出单轮理解数量上限（最多 {MAX_VIDEOS} 个），已跳过读取，请勿自行抓取外部链接。]",
            )
        question = extract_video_question(event, request, prepared)
        await self._attach_core_reply_video_material(event, prepared, request, originals,
                                                     application, context, settings,
                                                     question=question, fetch_errors=fetch_errors)

    def _video_understanding_settings(self, application, context):
        """读这个窗口的媒体理解设置；读不到就返回 {}（等于本轮不挂）。

        必须走 ``reply_settings.capture``：媒体理解字段属于
        ``conversation.presentation``（``companion_v2/conversation/reply_settings.py:24-26``
        导入的 ``UNDERSTANDING_DEFAULTS``），``capture`` 会补上文档化的默认值并跑
        一遍 ``understanding.settings`` 校验。读不到时必须等于不挂——这些开关的默认
        值是 True，把异常吞成默认开就成了绕过用户的显式关闭。
        """
        reader = getattr(application, "reply_settings", None) if application is not None else None
        if reader is None:
            return {}
        try:
            return dict(reader.capture(context, self.staging.now())["settings"])
        except Exception as error:
            logger.warning("[CompanionV2] 视频理解设置暂不可读，本轮不补挂 error_type=%s",
                           type(error).__name__)
            return {}

    @filter.on_agent_begin(priority=-1000000)
    async def observe_companion_core_agent_begin(self, event: AstrMessageEvent, run_context):
        from .astrbot_host.core_reply_media_prompt import finalize_core_reply_media_prompt
        finalize_core_reply_media_prompt(event, run_context)
        from .astrbot_host.core_reply_voice_authoring import prepare_core_voice_authoring
        prepare_core_voice_authoring(event, run_context)
        # One reply per turn: hold a tool round's prose until the turn says which
        # text is the answer. The dispatch observer installs first because it owns
        # the point where withheld text goes back before the tool runs. Neither
        # install raises; a host that cannot host the gate keeps today's delivery,
        # and the send-side net still applies.
        observe_core_tool_dispatch_begin(event, run_context)
        install_core_reply_turn(event, run_context)
        # Outside the turn gate on purpose: a turn V2 declined has no gate, and it
        # is the turn where the model can still ask for a tool this request never
        # declared. Installed last so it sees a round before the dispatch observer.
        install_core_reply_tool_guard(event, run_context)
        observe_core_agent_begin(event, run_context)
        followup_guard = getattr(self, "_core_followup_guard", None)
        if followup_guard is not None:
            followup_guard.mark_agent_begin(event)

    @filter.on_agent_done()
    async def observe_companion_core_agent_done(self, event: AstrMessageEvent, run_context, response):
        from .astrbot_host.core_reply_voice_authoring import restore_core_voice_authoring
        self._private_reply_followups().finish(event)
        restore_core_voice_authoring(event)
        await close_core_reply_turn(event, response, release=True)
        close_core_reply_tool_guard(event, run_context)
        restore_core_tool_dispatch(event, run_context)
        observe_core_agent_done(event, run_context, response)
        state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        prepared = state.get("prepared") if isinstance(state, dict) else None
        self._observe_core_phase(prepared, "completed" if getattr(response, "role", None) == "assistant" else "failed")
        # Empty/error results can end the runner without a final send hook.
        # A subsequent native delivery reopens the observation phase itself.
        finish_core_reply_tracking(event, log_completion=False)
        followup_guard = getattr(self, "_core_followup_guard", None)
        if followup_guard is not None:
            followup_guard.finish_turn(event)

    @filter.on_using_llm_tool()
    async def observe_companion_core_tool_begin(self, event: AstrMessageEvent, tool, tool_args):
        observe_core_tool_started(event, tool)

    @filter.on_llm_tool_respond()
    async def observe_companion_core_tool_done(self, event: AstrMessageEvent, tool, tool_args, tool_result):
        observe_core_tool_finished(event, tool)

    # The agent boundary is the last chance to make the history legal: the SDK
    # converts req.contexts into run_context.messages right after the request
    # hooks, and one unanswered assistant tool call fails the turn on every
    # provider of the fallback chain at once. Lowest priority runs last, so this
    # sees every other plugin's rewrite of the same request.
    @filter.on_llm_request(priority=-1000000)
    async def repair_companion_core_request_history(self, event: AstrMessageEvent, req):
        from .astrbot_host.core_reply_history_guard import repair_request_history
        repair_request_history(event, req)

    # 外部视频插件晚于感知注入，当前视频和跨轮视频也在这里补上。优先本轮视频，
    # 避免上一条群视频的缓存占用请求，使本轮视频只剩路径占位符。最后就地改写感知
    # JSON 的视频状态；同一份请求重复触发不会累加抽帧内容块。
    # 改写失败只记一行告警：一句感知标注不值得让整轮回复失败。
    @filter.on_llm_request(priority=-1000000)
    async def restate_companion_core_video_state(self, event: AstrMessageEvent, req):
        from .astrbot_host.core_reply_perception import rewrite_core_reply_video_state
        state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        prepared = state.get("prepared") if isinstance(state, dict) else None
        try:
            await self._attach_core_reply_current_videos(event, req)
        except Exception as error:
            logger.warning("[CompanionV2] 当前视频补挂跳过 error_type=%s",
                           type(error).__name__)
        try:
            await self._attach_core_reply_videos(prepared, req, event=event)
        except Exception as error:
            logger.warning("[CompanionV2] 跨轮视频补挂跳过 error_type=%s", type(error).__name__)
        try:
            rewrite_core_reply_video_state(req)
        except Exception as error:
            logger.warning("[CompanionV2] 视频输入状态重写失败 error_type=%s",
                           type(error).__name__)

    @filter.on_agent_begin(priority=-1000000)
    async def repair_companion_core_runner_history(self, event: AstrMessageEvent, run_context):
        from .astrbot_host.core_reply_history_guard import repair_run_context_history
        repair_run_context_history(event, run_context)

    @filter.after_message_sent(priority=1000000)
    async def observe_companion_core_reply_sent(self, event: AstrMessageEvent):
        from .astrbot_host.core_reply_voice_authoring import restore_core_voice_authoring
        followup_guard = getattr(self, "_core_followup_guard", None)
        if followup_guard is not None:
            followup_guard.finish_turn(event)
        self._private_reply_followups().finish(event)
        restore_core_voice_authoring(event)
        await close_core_reply_turn(event)
        state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        if isinstance(state, dict):
            if not state.get("delivery_guard_active"):
                getter = getattr(event, "get_result", None)
                result = getter() if callable(getter) else None
                if getattr(getattr(result, "result_content_type", None), "name", None) == "AGENT_RUNNER_ERROR":
                    self._observe_core_phase(state.get("prepared"), "failed")
                    restore_core_tool_dispatch(event)
                    finish_core_reply_tracking(event)
                    finish_core_reply_telemetry(event, delivery=state)
                    restore_core_reply_event(event)
                return
            prepared = state.get("prepared")
            event_ref = prepared.get("event_ref") if isinstance(prepared, dict) else "unknown"
            tracked = finish_core_reply_tracking(event)
            send_attempted = (tracked["send_attempts"] if tracked is not None else state.get("send_attempts", 0)) > 0
            send_unknown = (tracked["unknown_sends"] if tracked is not None else state.get("send_unknown", 0)) > 0
            send_returned = (tracked["send_returns"] if tracked is not None else state.get("send_returns", 0)) > 0
            send_state = ("denied" if state.get("denial_notified") else "unknown" if send_unknown
                          else "returned" if send_returned else "failed" if state.get("send_failed") else "not_started")
            finish_core_reply_telemetry(event, delivery=state)
            restore_core_reply_event(event)
            logger.info("[CompanionV2] AstrBot 核心回复阶段结束 event_ref=%s send_attempted=%s send_state=%s",
                        event_ref, send_attempted, send_state)
            if (send_state in {"unknown", "not_started"} and isinstance(prepared, dict)
                    and not state.get("_text_presentation_removed")
                    and state.get("group_participation", {}).get("mode") != "skip"):
                self._record_no_reply(
                    event, "core_reply_delivery_unknown" if send_unknown else "core_reply_no_delivery",
                    origin_key=prepared.get("origin_key"),
                    channel=prepared.get("channel"), event_ref=event_ref,
                    stage="delivery", turn=event_ref)

    @filter.on_decorating_result(priority=-1000000)
    async def guard_companion_core_delivery(self, event: AstrMessageEvent):
        # Final boundary safety: a model may reach the core pipeline through a
        # path that does not run the V2 response observer (for example an empty
        # wake-prefix turn). Strip the reaction protocol before AstrBot sends it.
        try:
            getter = getattr(event, "get_result", None)
            result = getter() if callable(getter) else None
            from companion_v2.conversation.text_processing import strip_internal_expression_protocol
            # Both the sendable chain and the provider chain can carry the raw
            # model text; strip every plain part of each so a result assembled
            # after the response observer still cannot leak the protocol.
            stripped = 0
            for owner in (result, getattr(result, "result_chain", None)):
                for part in getattr(owner, "chain", None) or ():
                    text = getattr(part, "text", None)
                    if not isinstance(text, str):
                        continue
                    try:
                        cleaned = strip_internal_expression_protocol(text)
                        if cleaned != text:
                            part.text = cleaned
                            stripped += len(text) - len(cleaned)
                    except Exception:  # noqa: BLE001 - one odd part must not skip the rest
                        continue
            if stripped:
                logger.info("[CompanionV2] 外发边界清理内部标签 chars=%d", stripped)
        except Exception:
            pass
        self._adopt_core_reply_delivery(event)
        activate_core_reply_delivery_guard(event)
        activate_core_reply_tracking(event)
        activate_core_reply_segmentation(event)
        prepare_core_result_decoration(event)

    @filter.on_decorating_result(priority=-1000001)
    async def suppress_companion_native_tool_repeat(self, event: AstrMessageEvent):
        """Check the complete final result before AstrBot splits or speaks it."""
        state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        result = event.get_result()
        if (not isinstance(state, dict) or not state.get("delivery_guard_active")
                or getattr(getattr(result, "result_content_type", None), "name", None) != "LLM_RESULT"
                or not getattr(result, "chain", None)):
            return
        get_config = getattr(getattr(self, "context", None), "get_config", None)
        if not callable(get_config):
            return
        config = get_config(event.unified_msg_origin)
        settings = config.get("platform_settings") if isinstance(config, Mapping) else None
        if not isinstance(settings, Mapping):
            return
        if (config.get("t2i") or getattr(result, "use_t2i_", False)
                or settings.get("reply_prefix") or settings.get("reply_with_quote")
                or settings.get("reply_with_mention")):
            return
        if event.get_platform_name() == "aiocqhttp":
            try:
                if len(result.get_plain_text()) > int(settings["forward_threshold"]):
                    return
            except (KeyError, TypeError, ValueError):
                return
        from astrbot.core.message.message_event_result import MessageChain
        from .astrbot_host.core_reply_voice import capture_core_voice_message
        from .astrbot_host.core_reply_idempotency import should_drop_native_tool_response

        visible = capture_core_voice_message(event, MessageChain(chain=list(result.chain)))
        if should_drop_native_tool_response(event, visible):
            result.chain.clear()

    def _observe_session(self, event: AstrMessageEvent) -> None:
        try:
            self.staging.session_topology.schedule_observe(event)
        except Exception:
            logger.warning("[CompanionV2] 会话目录更新失败", exc_info=True)

    def _observe_core_phase(self, prepared, state):
        observer = getattr(getattr(self.staging, "session", None), "core_reply_observations", None)
        phase = getattr(observer, "phase", None)
        if isinstance(prepared, dict) and callable(phase):
            try:
                # phase() only queues background observation, like output().
                phase(prepared, state)
            except Exception:
                pass

    @filter.on_llm_response()
    async def observe_companion_model_session(self, event: AstrMessageEvent, response):
        from .astrbot_host.core_reply_reaction_delivery import observe_core_reaction_response
        observe_core_reaction_response(event, response)
        from .astrbot_host.core_reply_cleanup import clean_reply_text
        if (response is not None and getattr(response, "role", None) == "assistant"
                and not getattr(response, "is_chunk", False)):
            text = getattr(response, "completion_text", "") or ""
            cleaned, removed = clean_reply_text(text)
            if removed:
                logger.info("[CompanionV2] 回复自重复清理 removed=%s", removed)
                response.completion_text = cleaned
                for part in getattr(getattr(response, "result_chain", None), "chain", ()) or ():
                    if type(getattr(part, "text", None)) is str:
                        part.text, _ = clean_reply_text(part.text)
        from .astrbot_host.core_reply_voice import observe_core_voice_response
        observe_core_voice_response(event, response)
        observe_native_host_response(self.staging, event, response)
        observe_core_llm_response(event, response)
        if response is not None and getattr(response, "role", None) == "assistant":
            state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
            prepared = state.get("prepared") if isinstance(state, dict) else None
            observer = getattr(getattr(self.staging, "session", None), "core_reply_observations", None)
            if observer is not None and isinstance(prepared, dict):
                observer.output(prepared, getattr(response, "completion_text", "") or "")
            self._observe_session(event)

    @filter.on_decorating_result(priority=-999999)
    async def observe_companion_native_presentation(self, event: AstrMessageEvent):
        observe_native_host_result(self.staging, event)

    @filter.on_decorating_result(priority=100000)
    async def observe_companion_context_reset(self, event: AstrMessageEvent):
        result = await self.staging.observe_context_reset(event)
        if result.failure is not None:
            logger.warning("[CompanionV2] 窗口临时上下文清理失败 code=%s", result.failure.code)
        elif result.unwrap().get("handled") and not result.unwrap().get("replayed"):
            logger.info("[CompanionV2] 窗口临时上下文已清理")

    async def terminate(self) -> None:
        extension_api = getattr(self, "extension_api", None)
        close_memory = getattr(extension_api, "close_memory_providers", None)
        if callable(close_memory):
            close_memory()
        debounce_manager = getattr(self, "_private_debounce", None)
        if debounce_manager is not None:
            try:
                await debounce_manager.close()
            except Exception as error:
                logger.warning("[CompanionV2] 私聊防抖停机清理失败 error_type=%s", type(error).__name__)
        group_debounce_manager = getattr(self, "_group_debounce", None)
        if group_debounce_manager is not None:
            try:
                await group_debounce_manager.close()
            except Exception as error:
                logger.warning("[CompanionV2] 群聊防抖停机清理失败 error_type=%s", type(error).__name__)
        followup_guard = getattr(self, "_core_followup_guard", None)
        if followup_guard is not None:
            try:
                followup_guard.close()
            except Exception as error:
                logger.warning("[CompanionV2] 核心追发媒体保护停机清理失败 error_type=%s", type(error).__name__)
        settings_task = getattr(self, "_session_settings_warm_task", None)
        self._session_settings_warm_task = None
        if settings_task is not None and not settings_task.done():
            settings_task.cancel()
            try:
                await settings_task
            except asyncio.CancelledError:
                pass
        qzone_publisher = getattr(self, "_qzone_life_publisher", None)
        self._qzone_life_publisher = None
        if qzone_publisher is not None:
            try:
                await qzone_publisher.close()
            except Exception as error:
                logger.warning("[CompanionV2] 生活说说停机清理失败 error_type=%s", type(error).__name__)
        video_fusion = getattr(self, "_video_fusion_service", None)
        self._video_fusion_service = None
        if video_fusion is not None:
            try:
                video_fusion.close()
            except Exception as error:
                logger.warning("[CompanionV2] 视频通道停机清理失败 error_type=%s", type(error).__name__)
        startup_task = getattr(self, "_host_start_task", None)
        self._host_start_task = None
        if startup_task is not None and not startup_task.done():
            startup_task.cancel()
            try:
                await startup_task
            except asyncio.CancelledError:
                pass
        automatic_backup = getattr(self, "_automatic_backup", None)
        self._automatic_backup = None
        if automatic_backup is not None:
            try:
                await automatic_backup.close()
            except Exception as error:
                logger.warning("[CompanionV2] 自动滚动备份停机清理失败 error_type=%s", type(error).__name__)
        retire_reset_marker = getattr(self, "_retire_context_reset_marker", None)
        if retire_reset_marker is not None:
            retire_reset_marker()
            self._retire_context_reset_marker = None
        setup_api = (getattr(self.staging, "_setup_api", None)
                     or getattr(getattr(self.staging, "web_api", None), "setup", None))
        if setup_api is not None and callable(getattr(setup_api, "aclose", None)):
            try:
                await setup_api.aclose()
            except Exception as error:
                logger.warning("[CompanionV2] 设置测试任务停机清理失败 error_type=%s", type(error).__name__)
        try:
            try:
                await self.staging.terminate()
                logger.info("[CompanionV2] 共享宿主已停止" if self.staging.runtime is None
                            else "[CompanionV2] 共享宿主正在收尾")
            finally:
                try:
                    from .astrbot_host.huawei_health.service import close_all_huawei_health_services
                    await close_all_huawei_health_services()
                except Exception as error:
                    logger.warning("[CompanionV2] 华为健康服务停机清理失败 error_type=%s", type(error).__name__)
                try:
                    from .astrbot_host.laya_client import shutdown_laya_resources
                    await shutdown_laya_resources()
                except Exception as error:
                    logger.warning("[CompanionV2] Laya 资源停机清理失败 error_type=%s", type(error).__name__)
                try:
                    from .astrbot_host.service_balance_script import shutdown_balance_script_workers
                    shutdown_balance_script_workers()
                except Exception as error:
                    logger.warning("[CompanionV2] 余额脚本线程池停机清理失败 error_type=%s", type(error).__name__)
        finally:
            global _ACTIVE_EXTENSION_API
            if _ACTIVE_EXTENSION_API is getattr(self, "extension_api", None):
                _ACTIVE_EXTENSION_API = None
            # Preserve the module graph during a deferred drain. Retire this
            # instance's path ownership once, even if an old stop is retried.
            if not getattr(self, "_imports_retired", False):
                retire_plugin_imports(_PLUGIN_ROOT, __name__, path_inserted=_CORE_PATH_INSERTED,
                                      entry_globals=globals(),
                                      preserve_modules=getattr(self.staging, "runtime", None) is not None)
                self._imports_retired = True
