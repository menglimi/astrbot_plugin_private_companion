# -*- coding: utf-8 -*-
"""MemoryCompanionAdapterMatchPresenceOutboxArchiveMixin。

由 tools/split_mixin_domain.py 从 memory_companion_adapter.py 机械抽取（14 个方法 + 0 个模块级名字 + 0 个类级赋值 / 441 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 MemoryCompanionAdapterMixin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import types
from .bot_personal_contract import BOT_PERSONAL_CANONICAL_SCHEMA_VERSION, window_for_minutes
from .bot_personal_outbox import BotPersonalOutbox
from .helpers import _path_text, _single_line
from .memory_companion_adapter_shared import logger
from pathlib import Path
from typing import Any



class MemoryCompanionAdapterMatchPresenceOutboxArchiveMixin:
    """MemoryCompanionAdapterMatchPresenceOutboxArchiveMixin（从 MemoryCompanionAdapterMixin 拆出）。"""


    @classmethod
    def _memory_companion_identity_matches(cls, value: Any) -> bool:
        if value is None:
            return False
        try:
            text = str(value).strip().lower()
        except Exception:
            # AstrBot may expose optional-model proxies (for example torch
            # namespaces) as metadata values. Their string conversion can
            # import a missing dependency; an invalid identity is simply not
            # a MemoryCompanion module.
            return False
        if not text:
            return False
        if text in cls._MEMORY_COMPANION_PLUGIN_ALIASES:
            return True
        normalized = re.sub(r"[\s\-]+", "_", text)
        if normalized in cls._MEMORY_COMPANION_PLUGIN_ALIASES:
            return True
        return any(part in cls._MEMORY_COMPANION_PLUGIN_ALIASES for part in text.split("."))

    @classmethod
    def _memory_companion_module_matches(cls, module: Any | None) -> bool:
        # AstrBot's plugin registry can expose proxy objects from optional
        # libraries (notably ``torch.classes``) as a module field. Those
        # proxies resolve arbitrary attributes as dynamic classes, so reading
        # ``__file__`` from them raises instead of returning a missing value.
        if module is None or not isinstance(module, types.ModuleType):
            return False
        module_vars = getattr(module, "__dict__", {})
        if isinstance(module_vars, dict) and cls._memory_companion_identity_matches(module_vars.get("PLUGIN_NAME")):
            return True
        if cls._memory_companion_identity_matches(getattr(module, "__name__", "")):
            return True
        module_file = _path_text(getattr(module, "__file__", ""))
        if module_file:
            path_parts = re.split(r"[\\/]", module_file.lower())
            return any(part in cls._MEMORY_COMPANION_PLUGIN_ALIASES for part in path_parts)
        return False

    @classmethod
    def _memory_companion_star_matches(cls, metadata: Any | None) -> bool:
        if metadata is None:
            return False
        try:
            values = (
                getattr(metadata, "name", ""),
                getattr(metadata, "display_name", ""),
                getattr(metadata, "root_dir_name", ""),
                getattr(metadata, "module_path", ""),
            )
        except Exception:
            return False
        if any(cls._memory_companion_identity_matches(value) for value in values):
            return True
        try:
            module = getattr(metadata, "module", None)
        except Exception:
            module = None
        return cls._memory_companion_module_matches(module)

    def _memory_companion_bridge_from_star(self, metadata: Any | None) -> Any | None:
        if metadata is None or not bool(getattr(metadata, "activated", True)):
            return None
        instance = getattr(metadata, "star_cls", None)
        bridge = self._memory_companion_bridge_from_object(instance)
        if bridge is not None:
            return bridge
        return self._memory_companion_bridge_from_module(getattr(metadata, "module", None))

    def _memory_companion_presence(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "detected": False,
            "installed": False,
            "loaded": False,
            "activated": False,
            "display_name": "我会牢牢记住你",
            "version": "",
            "plugin_dir": "",
            "reason": _single_line(self._bridge_last_status.get("reason"), 80),
        }
        context = getattr(self, "context", None)
        get_all_stars = getattr(context, "get_all_stars", None)
        if callable(get_all_stars):
            try:
                stars = list(get_all_stars() or [])
            except Exception:
                stars = []
            for metadata in stars:
                if not self._memory_companion_star_matches(metadata):
                    continue
                result.update(
                    {
                        "detected": True,
                        "installed": True,
                        "loaded": getattr(metadata, "star_cls", None) is not None,
                        "activated": bool(getattr(metadata, "activated", True)),
                        "display_name": _single_line(getattr(metadata, "display_name", ""), 80)
                        or "我会牢牢记住你",
                        "version": _single_line(getattr(metadata, "version", ""), 40),
                    }
                )
                root_dir_name = _single_line(getattr(metadata, "root_dir_name", ""), 120)
                if root_dir_name:
                    result["plugin_dir"] = str(Path(__file__).resolve().parent.parent / root_dir_name)
                return result

        plugin_root = Path(__file__).resolve().parent.parent
        for directory_name in ("astrbot_plugin_memory_companion", "astrbot_plugin_remember_you"):
            candidate = plugin_root / directory_name
            if not (candidate / "main.py").exists():
                continue
            result.update(
                {
                    "detected": True,
                    "installed": True,
                    "plugin_dir": str(candidate),
                }
            )
            metadata_path = candidate / "metadata.yaml"
            if metadata_path.exists():
                try:
                    metadata_text = metadata_path.read_text(encoding="utf-8")
                    version_match = re.search(r"(?m)^version:\s*[\"']?([^\n\"']+)", metadata_text)
                    display_match = re.search(r"(?m)^display_name:\s*[\"']?([^\n\"']+)", metadata_text)
                    if version_match:
                        result["version"] = _single_line(version_match.group(1), 40)
                    if display_match:
                        result["display_name"] = _single_line(display_match.group(1), 80)
                except Exception:
                    pass
            break
        return result

    def _memory_companion_outbox(self) -> BotPersonalOutbox | None:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return None
        persona_id = self._memory_companion_archive_persona_id()
        cache = getattr(self, "_bot_personal_outboxes", None)
        if not isinstance(cache, dict):
            cache = {}
            try:
                setattr(self, "_bot_personal_outboxes", cache)
            except Exception:
                pass
        current = cache.get(persona_id)
        if isinstance(current, BotPersonalOutbox) and current.data is data:
            return current

        default_data = getattr(self, "_data_default", None)
        is_default_backing = isinstance(default_data, dict) and data is default_data
        persona_data_getter = getattr(self, "_persona_data_for_save", None)
        try:
            is_exact_persona_backing = bool(
                callable(persona_data_getter)
                and persona_id
                and persona_data_getter(persona_id) is data
            )
        except Exception:
            is_exact_persona_backing = False
        secondary = bool(not is_default_backing and is_exact_persona_backing)

        def save_bound_outbox() -> Any:
            sections = {
                "bot_personal_outbox",
                "bot_personal_archive_revisions",
            }
            if secondary:
                scheduler = getattr(self, "_schedule_persona_data_save", None)
                if callable(scheduler):
                    return scheduler(persona_id, sections=sections, delay=0.5)
                return None
            if not is_default_backing and not is_exact_persona_backing:
                # Never guess a save target for an unrecognised backing dict.
                return None
            scheduler = getattr(self, "_schedule_default_data_save", None)
            if callable(scheduler):
                return scheduler(sections=sections, delay=0.5)
            fallback = getattr(self, "_schedule_data_save", None)
            if callable(fallback):
                return fallback(sections=sections, delay=0.5)
            return None

        lifecycle_task = getattr(self, "_create_lifecycle_background_task", None)
        try:
            current = BotPersonalOutbox(
                data,
                save=save_bound_outbox,
                background_task=(
                    lambda operation, label: lifecycle_task(operation, label=label)
                )
                if callable(lifecycle_task)
                else None,
            )
        except Exception as exc:
            logger.debug("Bot Personal outbox 初始化失败: %s", _single_line(exc, 120))
            return None
        try:
            cache[persona_id] = current
            setattr(self, "_bot_personal_outbox", current)
        except Exception:
            pass
        return current

    @staticmethod
    def _memory_companion_archive_business_value(value: Any) -> Any:
        ignored = {
            "archive_result",
            "archived_at",
            "created_at",
            "expires_at",
            "generated_at",
            "memory_archive",
            "memory_archive_result",
            "occurred_at",
            "sent_at",
            "updated_at",
            "version",
            "window",
        }
        if isinstance(value, dict):
            return {
                str(key): MemoryCompanionAdapterMatchPresenceOutboxArchiveMixin._memory_companion_archive_business_value(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
                if str(key) not in ignored
            }
        if isinstance(value, (list, tuple)):
            return [
                MemoryCompanionAdapterMatchPresenceOutboxArchiveMixin._memory_companion_archive_business_value(item)
                for item in value
            ]
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        return str(value)

    def _memory_companion_archive_revision(
        self,
        *,
        memory_type: str,
        local_date: str,
        business_payload: dict[str, Any],
    ) -> int:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return 1
        registry = data.setdefault("bot_personal_archive_revisions", {})
        if not isinstance(registry, dict):
            registry = {}
            data["bot_personal_archive_revisions"] = registry
        record_key = f"{str(memory_type or '').strip()}:{str(local_date or '').strip()}"
        canonical = self._memory_companion_archive_business_value(business_payload)
        encoded = json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        previous = registry.get(record_key)
        if isinstance(previous, dict) and previous.get("fingerprint") == fingerprint:
            try:
                return max(1, int(previous.get("revision") or 1))
            except (TypeError, ValueError, OverflowError):
                return 1
        try:
            revision = max(0, int(previous.get("revision") or 0)) + 1 if isinstance(previous, dict) else 1
        except (TypeError, ValueError, OverflowError):
            revision = 1
        registry[record_key] = {
            "revision": revision,
            "fingerprint": fingerprint,
        }
        return revision

    def _memory_companion_bot_personal_sender(self) -> Any | None:
        bridge = self._memory_companion_bridge()
        recorder = getattr(bridge, "record_bot_personal_archive", None) if bridge is not None else None
        capability = self._memory_companion_emotion_producer_capability(bridge)
        if not callable(recorder) or capability is None:
            return None

        async def _send(envelope: dict[str, Any]) -> dict[str, Any]:
            result = recorder(envelope, producer_capability=capability)
            if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                result = await result
            return result if isinstance(result, dict) else {"ok": False, "state": "retry", "error_code": "invalid_bridge_response"}

        return _send

    async def _memory_companion_record_bot_personal(
        self,
        *,
        memory_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
        occurred_at: str = "",
        version: int = 1,
        source_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        outbox = self._memory_companion_outbox()
        if outbox is None:
            return {
                "ok": False,
                "state": "local_only",
                "record_id": "",
                "deduplicated": False,
                "version": int(version or 1),
                "error_code": "outbox_unavailable",
            }
        # Probe before constructing the envelope so a v3 bridge gets the
        # namespace-aware format while a known v2 bridge receives a legacy
        # envelope it can still validate.  Local-only operation remains
        # available when no bridge is installed.
        try:
            self._memory_companion_bridge()
        except Exception:
            pass
        negotiated_schema = int(
            getattr(self, "_bridge_last_status", {}).get(
                "negotiated_canonical_schema_version", 2
            ) or 2
        )
        if negotiated_schema >= BOT_PERSONAL_CANONICAL_SCHEMA_VERSION:
            owner_bot_id = self._memory_companion_bridge_bot_id()
            persona_id = self._memory_companion_archive_persona_id()
            if not owner_bot_id or not persona_id:
                negotiated_schema = 2
                owner_bot_id = ""
                persona_id = ""
        else:
            owner_bot_id = ""
            persona_id = ""
        try:
            result = await outbox.enqueue(
                memory_type=memory_type,
                payload=payload,
                idempotency_key=idempotency_key,
                occurred_at=occurred_at or self._memory_companion_now_iso(),
                version=max(1, int(version or 1)),
                source_refs=source_refs,
                owner_bot_id=owner_bot_id,
                persona_id=persona_id,
                canonical_schema_version=negotiated_schema,
                sender=self._memory_companion_bot_personal_sender(),
            )
            self._bridge_last_status = {
                **getattr(self, "_bridge_last_status", {}),
                "bot_personal_outbox": outbox.status(),
            }
            return result
        except Exception as exc:
            logger.debug("Bot Personal 本地归档失败: %s", _single_line(exc, 160))
            return {
                "ok": False,
                "state": "local_only",
                "record_id": "",
                "deduplicated": False,
                "version": int(version or 1),
                "error_code": "outbox_enqueue_failed",
            }

    async def _memory_companion_flush_bot_personal_outbox(self, *, limit: int = 16) -> list[dict[str, Any]]:
        outbox = self._memory_companion_outbox()
        sender = self._memory_companion_bot_personal_sender()
        if outbox is None or sender is None:
            return []
        try:
            results = await outbox.drain(sender, limit=max(1, int(limit or 16)))
            self._bridge_last_status = {
                **getattr(self, "_bridge_last_status", {}),
                "bot_personal_outbox": outbox.status(),
            }
            return results
        except Exception as exc:
            logger.debug("Bot Personal outbox 补投失败: %s", _single_line(exc, 160))
            return []

    async def _memory_companion_record_observed_activity(self, activity: dict[str, Any]) -> dict[str, Any]:
        """Archive only private observed activity; group observations stay local/group-scoped."""
        if not isinstance(activity, dict) or _single_line(activity.get("visibility"), 32) != "private":
            return {"ok": False, "state": "local_only", "error_code": "non_private_activity"}
        activity_id = _single_line(activity.get("activity_id"), 160)
        title = _single_line(activity.get("title") or activity.get("summary"), 180)
        if not activity_id or not title:
            return {"ok": False, "state": "invalid", "error_code": "invalid_activity"}
        payload = {
            "date": _single_line(activity.get("start_at"), 10),
            "window": window_for_minutes(0),
            "summary": title,
            "activity_id": activity_id,
            "kind": _single_line(activity.get("kind"), 48),
            "participants": [_single_line(item, 80) for item in (activity.get("participants") or []) if _single_line(item, 80)][:8],
            "message_count": int(activity.get("message_count") or len(activity.get("source_refs") or []) or 1),
        }
        try:
            occurred_at = _single_line(activity.get("start_at"), 80) or self._memory_companion_now_iso()
            if "+" in occurred_at or occurred_at.endswith("Z"):
                parsed = occurred_at.replace("Z", "+00:00")
                from datetime import datetime

                moment = datetime.fromisoformat(parsed)
                payload["window"] = window_for_minutes(moment.hour * 60 + moment.minute)
        except Exception:
            occurred_at = self._memory_companion_now_iso()
        source_refs = [_single_line(item, 160) for item in (activity.get("source_refs") or []) if _single_line(item, 160)]
        return await self._memory_companion_record_bot_personal(
            memory_type="bot_observed_activity",
            payload=payload,
            idempotency_key=f"observed:{activity_id}",
            occurred_at=occurred_at,
            version=int(activity.get("version") or 1),
            source_refs=source_refs or [f"companion:observed:{activity_id}"],
        )

    def _memory_companion_bridge_from_module(self, module: Any | None) -> Any | None:
        module_vars = getattr(module, "__dict__", {}) if module is not None else {}
        if not isinstance(module_vars, dict):
            return None
        for getter_name in ("get_active_bridge", "get_memory_companion_bridge"):
            getter = module_vars.get(getter_name)
            if not callable(getter):
                continue
            try:
                bridge = getter()
            except Exception as exc:
                self._memory_companion_optional_dependency_failed(exc, where=getter_name)
                continue
            if bridge is not None:
                return bridge
        return self._memory_companion_bridge_from_object(module)

    @staticmethod
    def _memory_companion_bridge_from_object(candidate: Any | None) -> Any | None:
        if candidate is None:
            return None
        for getter_name in ("get_active_bridge", "get_memory_companion_bridge"):
            getter = getattr(candidate, getter_name, None)
            if not callable(getter):
                continue
            try:
                bridge = getter()
            except Exception:
                continue
            if bridge is not None:
                return bridge
        for attr in ("memory_companion", "memory_companion_bridge", "bridge", "_ACTIVE_BRIDGE"):
            try:
                bridge = getattr(candidate, attr, None)
            except Exception:
                continue
            if bridge is not None:
                return bridge
        return None
