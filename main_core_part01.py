# -*- coding: utf-8 -*-
"""PrivateCompanionExtensionAPIPart01Mixin。

由 tools/split_mixin_domain.py 从 main.py 机械抽取（39 个方法 + 0 个模块级名字 + 3 个类级赋值 / 504 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionExtensionAPI）。
"""
from __future__ import annotations

import hashlib
import re
from .companion.injection import (
    ContextContribution,
    ExtensionManifest,
    ExtensionStatus,
    PROTOCOL_VERSION,
    RuntimeScope,
    Scope,
)
from .conversation_prompt_section import prompt_section
from .helpers import _single_line
from .main_shared import _private_companion_runtime
from .plugin_identity import PLUGIN_ID
from .story_authority import story_authority_controller
from datetime import datetime
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class PrivateCompanionExtensionAPIPart01Mixin:
    """PrivateCompanionExtensionAPIPart01Mixin（从 PrivateCompanionExtensionAPI 拆出）。"""


    @staticmethod
    def _protocol_version(version: Any) -> str:
        """Reduce a host plugin version to the protocol's major.minor form."""
        match = re.search(r"(\d+)\.(\d+)", str(version or ""))
        return f"{match.group(1)}.{match.group(2)}" if match else "0.1"

    def _set_core_extension_status(self, state: str, *, reason: str = "") -> None:
        """Keep the control-plane status aligned with the published API generation."""
        try:
            current = self._extension_registry.status(PLUGIN_ID)
            task_counter = getattr(self._plugin, "_extension_task_count", None)
            task_count = task_counter() if callable(task_counter) else 0
            now = datetime.now().astimezone().isoformat()
            self._extension_registry.set_status(
                ExtensionStatus(
                    id=PLUGIN_ID,
                    state=state,
                    reason=reason,
                    task_count=task_count,
                    started_at=(current.started_at if current and current.started_at else now),
                    updated_at=now,
                )
            )
        except Exception as exc:
            logger.warning("更新扩展控制面状态失败: %s", _single_line(exc, 160))

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

    def register_extension(
        self,
        manifest: ExtensionManifest | dict[str, Any],
    ) -> dict[str, Any]:
        """Register public extension metadata without importing provider objects."""
        item = manifest if isinstance(manifest, ExtensionManifest) else ExtensionManifest.from_dict(manifest)
        self._extension_registry.register(item)
        return {
            "ok": True,
            "extension_id": item.id,
            "version": item.version,
            "protocol_version": PROTOCOL_VERSION,
        }

    def set_extension_status(
        self,
        status: ExtensionStatus | dict[str, Any],
    ) -> dict[str, Any]:
        item = status if isinstance(status, ExtensionStatus) else ExtensionStatus.from_dict(status)
        self._extension_registry.set_status(item)
        return {"ok": True, "extension_id": item.id, "state": item.state}

    def unregister_extension(self, extension_id: str) -> dict[str, Any]:
        normalized_id = str(extension_id or "").strip().lower()
        if normalized_id == PLUGIN_ID:
            return {"ok": False, "extension_id": normalized_id, "reason": "core_extension_protected"}
        removed = self._extension_registry.unregister(normalized_id)
        return {"ok": removed, "extension_id": normalized_id}

    def extension_control_plane_status(self) -> dict[str, Any]:
        """Return bounded metadata used by the panel and self-check command."""
        core_status = self._extension_registry.status(PLUGIN_ID)
        task_counter = getattr(self._plugin, "_extension_task_count", None)
        task_count = task_counter() if callable(task_counter) else 0
        if core_status is not None and core_status.task_count != task_count:
            self._extension_registry.set_status(
                ExtensionStatus.from_dict(
                    {
                        **core_status.to_dict(),
                        "task_count": task_count,
                    }
                )
            )
        return {
            "protocol_version": PROTOCOL_VERSION,
            "instance_generation": self._story_migration_generation,
            "lifecycle_state": self._story_migration_state,
            "extensions": list(self._extension_registry.snapshot()),
            "issues": list(self._extension_registry.self_check()),
        }

    def runtime_scope_for_event(self, event: Any) -> RuntimeScope | None:
        """Resolve one complete, stable scope at the host boundary.

        Extension code should use this result instead of extracting a user ID
        or platform-specific origin on its own. Missing adapter fields get
        explicit ``unknown`` values so a DTO is never silently re-scoped to a
        different Bot or persona.
        """
        if event is None:
            return None
        plugin = self._plugin

        def _call(name: str, default: Any = "") -> Any:
            getter = getattr(plugin, name, None)
            if not callable(getter):
                return default
            try:
                return getter(event)
            except Exception:
                return default

        raw: dict[str, Any] = {}
        raw_reader = getattr(plugin, "_event_raw_payload", None)
        if callable(raw_reader):
            try:
                candidate = raw_reader(event)
                if isinstance(candidate, dict):
                    raw = candidate
            except Exception:
                raw = {}

        origin = _single_line(getattr(event, "unified_msg_origin", ""), 240)
        platform = _single_line(_call("_platform_kind_for_event"), 80)
        if not platform:
            platform_getter = getattr(event, "get_platform_name", None)
            if callable(platform_getter):
                try:
                    platform = _single_line(platform_getter(), 80)
                except Exception:
                    platform = ""
        if not platform and origin:
            platform = _single_line(origin.split(":", 1)[0], 80)
        platform = platform or _single_line(getattr(plugin, "target_platform", ""), 80) or "unknown"

        sender_id = _single_line(_call("_safe_event_sender_id"), 160)
        if not sender_id:
            sender_id = _single_line(raw.get("user_id") or raw.get("sender_id"), 160)
        self_id = _single_line(_call("_event_self_id"), 160)
        if not self_id:
            self_id = _single_line(raw.get("self_id") or raw.get("bot_id"), 160)
        bot_id = _single_line(
            raw.get("bot_id")
            or getattr(event, "bot_id", "")
            or (f"{platform}:{self_id}" if self_id else ""),
            160,
        ) or f"{platform}:unknown"
        account_id = _single_line(
            raw.get("account_id")
            or getattr(event, "account_id", "")
            or (f"{platform}:{self_id}" if self_id else bot_id),
            160,
        ) or f"{platform}:{bot_id}"

        group_id = _single_line(_call("_extract_group_id_from_event"), 160)
        if not group_id:
            group_id = _single_line(raw.get("group_id"), 160)
        conversation_id = origin or _single_line(
            raw.get("conversation_id") or getattr(event, "conversation_id", ""),
            240,
        )
        if not conversation_id:
            conversation_id = f"{platform}:{group_id or sender_id or 'global'}"
        session_id = _single_line(
            getattr(event, "session_id", "") or raw.get("session_id") or conversation_id,
            240,
        ) or conversation_id

        persona_getter = getattr(plugin, "_effective_plugin_persona_id", None)
        try:
            persona_id = _single_line(persona_getter() if callable(persona_getter) else "", 96)
        except Exception:
            persona_id = ""
        if not persona_id:
            primary_getter = getattr(plugin, "_primary_persona_id", None)
            try:
                persona_id = _single_line(primary_getter() if callable(primary_getter) else "", 96)
            except Exception:
                persona_id = ""
        persona_id = persona_id or "default"

        installation_source = _single_line(
            getattr(plugin, "data_dir", "")
            or getattr(plugin, "data_file", "")
            or PLUGIN_ID,
            512,
        )
        installation_id = "installation:" + hashlib.sha256(
            installation_source.encode("utf-8", "ignore")
        ).hexdigest()[:24]
        try:
            binding_revision = int(
                getattr(event, "persona_binding_revision", 0)
                or raw.get("persona_binding_revision", 0)
                or 0
            )
        except (TypeError, ValueError, OverflowError):
            binding_revision = 0

        try:
            return RuntimeScope(
                installation_id=installation_id,
                bot_id=bot_id,
                platform=platform,
                account_id=account_id,
                persona_id=persona_id,
                conversation_id=conversation_id,
                session_id=session_id,
                user_id=sender_id,
                group_id=group_id,
                persona_binding_revision=max(0, binding_revision),
            )
        except Exception as exc:
            logger.debug("解析扩展运行作用域失败: %s", _single_line(exc, 160))
            return None

    @staticmethod
    def _scopes_compatible(expected: Scope | RuntimeScope, supplied: Scope | RuntimeScope) -> bool:
        """Compare only fields present on both sides of a legacy scope."""
        for name in (
            "installation_id",
            "bot_id",
            "platform",
            "account_id",
            "conversation_id",
            "session_id",
            "user_id",
            "group_id",
            "persona_id",
        ):
            left = _single_line(getattr(expected, name, ""), 256)
            right = _single_line(getattr(supplied, name, ""), 256)
            if left and right and left != right:
                return False
        expected_revision = int(getattr(expected, "persona_binding_revision", 0) or 0)
        supplied_revision = int(getattr(supplied, "persona_binding_revision", 0) or 0)
        if expected_revision and supplied_revision and expected_revision != supplied_revision:
            return False
        return True

    @staticmethod
    def _complete_scope(scope: Scope | RuntimeScope | None) -> RuntimeScope | None:
        """Convert a legacy Scope only when all host identity fields exist."""
        if isinstance(scope, RuntimeScope):
            return scope
        if not isinstance(scope, Scope):
            return None
        required = ("installation_id", "bot_id", "platform", "account_id", "persona_id")
        if any(not _single_line(getattr(scope, name, ""), 256) for name in required):
            return None
        try:
            return RuntimeScope(**{
                name: getattr(scope, name)
                for name in (
                    "installation_id", "bot_id", "platform", "account_id", "persona_id",
                    "conversation_id", "session_id", "user_id", "group_id", "persona_binding_revision",
                )
            })
        except Exception:
            return None

    def add_context_contribution(
        self,
        req: Any,
        contribution: ContextContribution | dict[str, Any],
        *,
        event: Any | None = None,
        source_id: str = "",
        priority: int | None = None,
    ) -> bool:
        """Publish one bounded typed context section for the current request.

        The method is intentionally synchronous so an extension can call it
        from ``on_llm_request`` without creating another task. It only stages a
        request-local section; the host still controls final prompt placement,
        delivery and audit metadata.
        """
        if req is None:
            return False
        try:
            item = (
                contribution
                if isinstance(contribution, ContextContribution)
                else ContextContribution.from_dict(contribution)
            )
            source = _single_line(source_id or item.source or "external", 128).lower()
            if not source or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{1,127}", source):
                return False
            if source != item.source:
                payload = item.to_dict()
                payload["source"] = source
                item = ContextContribution.from_dict(payload)
            resolved_scope = self.runtime_scope_for_event(event) if event is not None else self._complete_scope(item.scope)
            if resolved_scope is None:
                return False
            if item.scope is not None and not self._scopes_compatible(resolved_scope, item.scope):
                logger.warning(
                    "拒绝跨作用域扩展上下文: source=%s key=%s",
                    source,
                    item.key,
                )
                return False
            section = prompt_section(
                key=f"extension.{source}.{item.key}",
                title=f"扩展上下文：{source}",
                source="extension_api",
                content=item.content,
                metadata={
                    "source_id": source,
                    "lane": item.lane,
                    "evidence": item.evidence,
                    "source_refs": list(item.source_refs),
                    "revision": item.revision,
                    "trace_id": item.trace_id,
                    "visibility": item.visibility,
                    "max_age_seconds": item.max_age_seconds,
                    "scope_key": resolved_scope.scope_key,
                },
            )
            normalized_priority = item.priority if priority is None else priority
            normalized_priority = max(-100000, min(100000, int(normalized_priority)))
            marker = f"extension_context:{source}:{item.key}"
            append = getattr(self._plugin, "_append_turn_prompt_fragment_by_position", None)
            if not callable(append):
                return False
            return bool(
                append(
                    req,
                    marker,
                    section,
                    priority=normalized_priority,
                    force_dynamic=True,
                )
            )
        except Exception as exc:
            logger.debug("扩展上下文注入失败: %s", _single_line(exc, 160))
            return False

    publish_context_contribution = add_context_contribution

    submit_context_contribution = add_context_contribution

    get_runtime_scope = runtime_scope_for_event

    def _activate_story_migration_api(self) -> bool:
        if self._story_migration_state != "created":
            return False
        story_authority_controller().activate_generation(
            self._story_migration_generation
        )
        self._story_migration_state = "ready"
        self._set_core_extension_status("ready")
        return True

    def _supersede_story_migration_api(self) -> None:
        if self._story_migration_state in {"created", "ready"}:
            self._story_migration_state = "superseded"
            self._set_core_extension_status("stopped", reason="superseded")
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
            self._set_core_extension_status("stopped", reason="closed")
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
