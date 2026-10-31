# -*- coding: utf-8 -*-
"""AstrBot entry for private conversation and explicitly authorized groups."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace as replace_dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .astrbot_host.upgrade_source import preserve_host_configuration

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
    activate_core_reply_delivery_guard,
    adopt_core_reply_event,
    inject_core_reply_prompt,
    prepare_core_reply_event,
    restore_core_reply_event,
    restore_core_reply_prepared,
)
from .astrbot_host.core_reply_tools import inject_core_reply_tools, inject_core_turn_notes
from .astrbot_host.core_reply_preparation import measure_core_preparation
from .astrbot_host.core_reply_decision import prepare_core_reply_decision
from .astrbot_host.core_reply_closing import prepare_private_closing_decision
from .astrbot_host.native import warm_session_settings
from .astrbot_host.core_reply_tool_dispatch import observe_core_tool_dispatch_begin, restore_core_tool_dispatch
from .astrbot_host.core_reply_native_send import inject_core_reply_send_tool
from .astrbot_host.core_reply_perception import (
    inject_core_reply_perception_context, inject_core_reply_perception_policy,
)
from .astrbot_host.native_extensions.host_events import (
    prepare_native_host_request, observe_native_host_response, observe_native_host_result,
)
from .astrbot_host.core_reply_segmentation import (
    activate_core_reply_segmentation, prepare_core_reply_segmentation,
    prepare_core_reply_streaming_boundary,
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
from companion_v2.conversation.group_llm_block import GroupLLMBlock
from companion_v2.conversation import debounce_log
from .astrbot_host.private_debounce import PrivateDebounce
from .astrbot_host.private_reply_followup import PrivateReplyFollowUps
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
    "host_platform_unavailable", "host_account_unavailable", "host_persona_unavailable",
    "host_persona_window_unbound", "host_conversation_changed", "host_startup_failed",
    "staging_preflight_failed",
})


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


@register(
    PLUGIN_NAME,
    "menglimi",
    "我会永远陪着你 V2.0 统一陪伴宿主，整合核心生活能力与公开拓展 API。",
    PLUGIN_VERSION,
)
class PrivateCompanionV2StagingPlugin(Star):
    def __init__(self, context: Context, config: dict[str, Any] | None = None):
        self._imports_retired = False
        try:
            super().__init__(context, config)
            self._host_start_task = None
            self._core_reply_owner = object()
            self._startup_failure_key = None
            self._startup_failure_at = 0.0
            self._session_settings_warm_task = None
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
            # One public facade exposes the body and its seven internal modules.
            # Independent third-party integrations use their documented APIs.
            self.extension_api = UnifiedCompanionExtensionAPI(self.staging)
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

    def _private_debounce_manager(self):
        manager = getattr(self, "_private_debounce", None)
        if manager is None:
            manager = PrivateDebounce()
            self._private_debounce = manager
        return manager

    def _private_reply_followups(self):
        manager = getattr(self, "_private_followup", None)
        if manager is None:
            manager = PrivateReplyFollowUps()
            self._private_followup = manager
        return manager

    def _fold_private_follow_up(self, event, target, event_ref, text):
        """Join a post-debounce message to the first model request when possible."""
        target_key = getattr(target, "target_id", None)
        if not isinstance(target_key, str) or not target_key:
            return False
        active = self._private_reply_followups().fold(target_key, event, text)
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
        extra_text = "\n".join(str(item[1]).strip() for item in pending if str(item[1]).strip())
        if not extra_text:
            return
        prompt = getattr(request, "prompt", None)
        if isinstance(prompt, str) and prompt.strip():
            request.prompt = prompt.rstrip() + "\n" + extra_text
        else:
            request.prompt = extra_text
        prepared = prepared_state.get("prepared") if isinstance(prepared_state, dict) else None
        if isinstance(prepared, dict):
            base = str(prepared.get("text") or "").rstrip()
            prepared["text"] = (base + "\n" + extra_text).strip() if base else extra_text
            prepared["debounced_inputs"] = merged_input_sequence(entries)
            prepared["debounced_message_count"] = len(entries)
        logger.info(
            "[CompanionV2] 首轮模型请求前合并私聊后续消息 event_ref=%s count=%d message=%s",
            (prepared or {}).get("event_ref") if isinstance(prepared, dict) else None,
            len(pending), extra_text[:120])

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

    async def initialize(self) -> None:
        if self._session_settings_warm_task is None or self._session_settings_warm_task.done():
            self._session_settings_warm_task = asyncio.create_task(
                warm_session_settings(), name="companion-v2-session-settings-warmup")
        if self.staging.settings is None or not self.staging.settings.enabled:
            logger.info("[CompanionV2] 共享宿主已加载，等待账号与人格配置")
            return
        if not await self._try_start_host():
            self._schedule_host_start()

    async def _try_start_host(self) -> bool:
        try:
            readiness = self.staging.startup_readiness()
        except Exception as error:
            self._note_startup_failure("readiness", getattr(error, "code", "startup_readiness_failed"),
                                       type(error).__name__, str(error))
            return True
        if readiness in ("host_platform_unavailable", "host_account_unavailable"):
            return False
        if readiness is not None:
            self._note_startup_failure("readiness", readiness)
            return True

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
                initialized = await self.staging.initialize_profile()
                if initialized.failure is not None:
                    if initialized.failure.code == "host_terminated":
                        return True
                    retry = self._retry_startup_failure(initialized.failure.code)
                    self._note_startup_failure("initialize", initialized.failure.code,
                                               detail=initialized.failure.reason, retrying=retry)
                    return not retry
                initialized_profile = True

        started = await self.staging.start()
        if started.failure is not None:
            if started.failure.code == "host_terminated":
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
        return code in RETRYABLE_HOST_STARTUP_CODES or code.startswith("host_native_lookup_timeout")

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
        if stage == "load_existing":
            logger.warning("[CompanionV2] 已固定 profile 加载失败%s code=%s detail=%s",
                           suffix, code, detail or "unavailable")
        elif stage == "initialize":
            logger.warning("[CompanionV2] staging profile 自动初始化失败%s code=%s detail=%s",
                           suffix, code, detail or "unavailable")
        elif stage == "start":
            logger.warning("[CompanionV2] staging profile 自动启动失败%s code=%s detail=%s",
                           suffix, code, detail or "unavailable")
        else:
            logger.warning("[CompanionV2] 保存的运行档暂不能启动 code=%s error_type=%s detail=%s",
                           code, error_type or "unknown", detail or "unavailable")

    def _schedule_host_start(self) -> None:
        if self.staging._terminated:
            return
        session = self.staging.session
        if (self.staging._accepting and session is not None
                and session.application.state == "running"):
            return
        task = self._host_start_task
        if task is None or task.done():
            self._host_start_task = asyncio.create_task(
                self._wait_for_host_start(), name="companion:host-startup")

    async def _wait_for_host_start(self) -> None:
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        delay = 0.5
        warned = False
        while not self.staging._terminated:
            try:
                finished = await self._try_start_host()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("[CompanionV2] 启动恢复失败 code=%s error_type=%s",
                               getattr(error, "code", "startup_failed"), type(error).__name__)
                return
            if finished:
                return
            if not warned and loop.time() - started_at >= 30:
                logger.warning("[CompanionV2] 运行档尚未进入接收状态，恢复任务仍在运行并会自动重试")
                warned = True
            await asyncio.sleep(delay)
            delay = min(delay * 1.8, 30.0)

    @filter.on_platform_loaded()
    async def on_companion_platform_loaded(self) -> None:
        if self.staging.settings is not None and self.staging.settings.enabled:
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
            logger.info("[CompanionV2] 首次绑定: %s", result.get("reason") or "ok")
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

        async def authorize_core_send(current):
            return await self.staging.authorize_core_reply(event, target, current)

        def note_core_send_denied(current, error):
            reason = getattr(error, "code", type(error).__name__)
            self._record_no_reply(event, "core_reply_send_denied", origin_key=origin_key,
                                  channel=channel, event_ref=event_ref, stage="delivery",
                                  turn=event_ref, code=reason)
            logger.warning("[CompanionV2] 核心回复发送前复核失败 event_ref=%s code=%s",
                           event_ref, reason)

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
        # A plugin may answer without stopping propagation; respect that reply.
        if getattr(event, "_has_send_oper", False):
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
                suppress_default_reply(event)
                self._record_no_reply(event, "group_llm_blocked", origin_key=origin_key,
                                      channel=channel, event_ref=event_ref, stage="admission")
                return
        if channel == "private":
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
        plain_text = (PrivateDebounce.private_plain_text(event)
                      if channel == "private" and not is_calendar_command else None)
        target_key = getattr(target, "target_id", None) if channel == "private" else None
        if (plain_text is not None and isinstance(target_key, str) and target_key
                and self._fold_private_follow_up(event, target, event_ref, plain_text)):
            return
        debounce_manager = None
        debounce_pending = None
        if plain_text is not None and isinstance(target_key, str) and target_key:
            debounce_manager = self._private_debounce_manager()
            debounce_pending = debounce_manager.reserve(target_key, event, plain_text)
        group_admission = None
        if channel == "group":
            try:
                candidate = await self.staging.group_reply_candidate(event)
                if candidate.failure is not None:
                    if (candidate.failure.code in GROUP_INPUT_PASSTHROUGH_FAILURES
                            or _can_continue_astrbot_core_reply(candidate.failure.code)
                            or candidate.failure.code == "host_group_preflight_failed"):
                        logger.info("[CompanionV2] V2 群回复材料暂不可用，继续 AstrBot 核心处理 event_ref=%s code=%s",
                                    event_ref, candidate.failure.code)
                        return
                    suppress_default_reply(event)
                    logger.warning("[CompanionV2] 群回复候选检查失败 event_ref=%s code=%s",
                                   event_ref, candidate.failure.code)
                    return
                group_admission = candidate.unwrap()
            except Exception as error:
                logger.warning("[CompanionV2] 群回复候选检查异常，继续 AstrBot 核心处理 event_ref=%s error_type=%s",
                               event_ref, type(error).__name__)
                return
            if not group_admission["admitted"]:
                suppress_default_reply(event)
                return
        logger.info("[CompanionV2] 核心回复准入检查 event_ref=%s channel=%s", event_ref, channel)
        try:
            reply_enabled = await self.staging.session_topology.reply_enabled(event)
        except Exception as error:
            reply_enabled = True
            logger.warning("[CompanionV2] 会话回复开关暂不可读，继续核心授权检查 event_ref=%s error_type=%s",
                           event_ref, type(error).__name__)
        if not reply_enabled:
            if debounce_pending is not None:
                await debounce_manager.abort(debounce_pending, event=event)
                if debounce_manager.superseded_by_same_event(debounce_pending, event):
                    return
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
            status = await debounce_manager.configure(debounce_pending, event, policy)
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
                    if not PrivateDebounce.replace_private_plain_text(event, merged_text):
                        suppress_default_reply(event)
                        self._record_no_reply(event, "private_debounce_merge_failed", origin_key=origin_key,
                                              channel="private", event_ref=event_ref, stage="intake",
                                              turn=event_ref)
                        logger.warning("[CompanionV2] 私聊防抖合并失败 event_ref=%s", event_ref)
                        return
                    if core_reply_plan is not None:
                        core_reply_plan = self._core_reply_plan_with_text(core_reply_plan, merged_text)
            if status == "superseded":
                self._suppress_debounced_private_event(
                    event, target, event_ref, debounce_manager, debounce_pending)
                return
            if status in {"closed", "aborted", "finished"}:
                self._suppress_debounced_private_event(
                    event, target, event_ref, debounce_manager, debounce_pending,
                    reason="private_debounce_cancelled")
                return
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
            return
        if admission.failure is not None:
            failure = admission.failure
            if _can_continue_astrbot_core_reply(failure.code):
                logger.warning("[CompanionV2] V2 回复材料暂不可用，继续 AstrBot 核心回复 event_ref=%s code=%s",
                               event_ref, failure.code)
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
        event.is_wake = True
        event.is_at_or_wake_command = True
        if channel == "private":
            entries = (debounced_entries if debounced_entries is not None else
                       ((event, str(prepared.get("text") or event.message_str or ""),
                         getattr(event, "created_at", None)),))
            self._private_reply_followups().start(
                target_key or getattr(target, "target_id", ""), event, event_ref, entries)
        logger.info("[CompanionV2] 交由 AstrBot 核心回复管线 event_ref=%s channel=%s",
                    event_ref, channel)

    @filter.on_waiting_llm_request()
    async def observe_companion_core_waiting_llm_request(self, event: AstrMessageEvent):
        observe_core_waiting_llm_request(event, now=self.staging.now)

    @filter.on_llm_request(priority=250000)
    async def prepare_companion_core_request(self, event: AstrMessageEvent, req):
        state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        if not isinstance(state, dict):
            prepare_native_host_request(self.staging, event, req)
            return
        self._adopt_core_reply_delivery(event)
        self._apply_private_follow_ups_to_request(event, req)
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

    @filter.on_agent_begin(priority=-1000000)
    async def observe_companion_core_agent_begin(self, event: AstrMessageEvent, run_context):
        from .astrbot_host.core_reply_media_prompt import finalize_core_reply_media_prompt
        finalize_core_reply_media_prompt(event, run_context)
        from .astrbot_host.core_reply_voice_authoring import prepare_core_voice_authoring
        prepare_core_voice_authoring(event, run_context)
        observe_core_tool_dispatch_begin(event, run_context)
        observe_core_agent_begin(event, run_context)

    @filter.on_agent_done()
    async def observe_companion_core_agent_done(self, event: AstrMessageEvent, run_context, response):
        from .astrbot_host.core_reply_voice_authoring import restore_core_voice_authoring
        self._private_reply_followups().finish(event)
        restore_core_voice_authoring(event)
        restore_core_tool_dispatch(event, run_context)
        observe_core_agent_done(event, run_context, response)
        state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        prepared = state.get("prepared") if isinstance(state, dict) else None
        self._observe_core_phase(prepared, "completed" if getattr(response, "role", None) == "assistant" else "failed")
        # Empty/error results can end the runner without a final send hook.
        # A subsequent native delivery reopens the observation phase itself.
        finish_core_reply_tracking(event, log_completion=False)

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

    @filter.on_agent_begin(priority=-1000000)
    async def repair_companion_core_runner_history(self, event: AstrMessageEvent, run_context):
        from .astrbot_host.core_reply_history_guard import repair_run_context_history
        repair_run_context_history(event, run_context)

    @filter.after_message_sent(priority=1000000)
    async def observe_companion_core_reply_sent(self, event: AstrMessageEvent):
        from .astrbot_host.core_reply_voice_authoring import restore_core_voice_authoring
        self._private_reply_followups().finish(event)
        restore_core_voice_authoring(event)
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
            chain = getattr(result, "chain", None) if result is not None else None
            from companion_v2.conversation.text_processing import strip_internal_expression_protocol
            for part in chain or ():
                text = getattr(part, "text", None)
                if isinstance(text, str):
                    cleaned = strip_internal_expression_protocol(text)
                    if cleaned != text:
                        part.text = cleaned
        except Exception:
            pass
        self._adopt_core_reply_delivery(event)
        activate_core_reply_delivery_guard(event)
        activate_core_reply_tracking(event)
        activate_core_reply_segmentation(event)

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
        debounce_manager = getattr(self, "_private_debounce", None)
        if debounce_manager is not None:
            try:
                await debounce_manager.close()
            except Exception as error:
                logger.warning("[CompanionV2] 私聊防抖停机清理失败 error_type=%s", type(error).__name__)
        settings_task = getattr(self, "_session_settings_warm_task", None)
        self._session_settings_warm_task = None
        if settings_task is not None and not settings_task.done():
            settings_task.cancel()
            try:
                await settings_task
            except asyncio.CancelledError:
                pass
        startup_task = getattr(self, "_host_start_task", None)
        self._host_start_task = None
        if startup_task is not None and not startup_task.done():
            startup_task.cancel()
            try:
                await startup_task
            except asyncio.CancelledError:
                pass
        retire_reset_marker = getattr(self, "_retire_context_reset_marker", None)
        if retire_reset_marker is not None:
            retire_reset_marker()
            self._retire_context_reset_marker = None
        try:
            await self.staging.terminate()
            from .astrbot_host.laya_client import cancel_laya_warmup
            await cancel_laya_warmup()
            logger.info("[CompanionV2] 共享宿主已停止" if self.staging.runtime is None
                        else "[CompanionV2] 共享宿主正在收尾")
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
