"""Host-local registrations for third-party memory readers and writers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
import json
import math
from typing import Callable
from uuid import uuid4

from companion_v2.execution.models import TaskError, encode


REGISTRY_ATTRIBUTE = "_private_companion_memory_providers"
PROVIDER_API_VERSION = "companion.external-memory.v1"


def memory_provider_registry(context):
    return getattr(context, REGISTRY_ATTRIBUTE, None)


def memory_context_document(context):
    return json.loads(encode({"scope": context.scope.document(),
        "role": context.role_binding.role, "role_binding_revision": context.role_binding.revision,
        "user": context.actor.subject, "principal": context.actor.principal,
        "audience": context.audience}))


@dataclass(frozen=True)
class MemoryProviderRegistration:
    provider_id: str
    registration_id: str
    plugin: object
    display_name: str
    priority: int
    handles_model_memory: bool
    timeout_seconds: float
    read: Callable | None = None
    write: Callable | None = None
    summary: Callable | None = None
    is_active: Callable | None = None


class ExternalMemoryProviderRegistry:
    """Callbacks are leased to one live Star instance and one host lifetime."""

    def __init__(self, context, *, host_active=lambda: True):
        self.context = context
        self._host_active = host_active
        self._entries = {}
        self._closed = False
        setattr(context, REGISTRY_ATTRIBUTE, self)

    def register(self, provider_id, *, plugin, read=None, write=None, summary=None,
                 display_name="", priority=0, handles_model_memory=False,
                 is_active=None, timeout_seconds=5.0):
        if self._closed or not self._host_active() or memory_provider_registry(self.context) is not self:
            raise ValueError("memory provider registry is closed")
        if not isinstance(provider_id, str) or not provider_id.strip() or provider_id != provider_id.strip():
            raise ValueError("memory provider requires a non-blank provider_id")
        if plugin is None:
            raise ValueError("memory provider requires its owning plugin instance")
        if not any(callable(value) for value in (read, write, summary)):
            raise ValueError("memory provider requires a read, write or summary callback")
        if any(value is not None and not callable(value) for value in (read, write, summary, is_active)):
            raise ValueError("memory provider callbacks must be callable")
        if (not isinstance(display_name, str) or type(priority) is not int
                or type(handles_model_memory) is not bool):
            raise ValueError("invalid memory provider metadata")
        if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or timeout_seconds <= 0):
            raise ValueError("memory provider timeout must be positive and finite")
        previous = self._entries.get(provider_id)
        if previous is not None and previous.plugin is not plugin and self._plugin_active(previous):
            raise ValueError("memory provider_id belongs to another active plugin")
        entry = MemoryProviderRegistration(provider_id, uuid4().hex, plugin,
            display_name.strip() or provider_id, priority, handles_model_memory,
            float(timeout_seconds), read, write, summary, is_active)
        self._entries[provider_id] = entry
        return entry.registration_id

    def unregister(self, registration_id):
        for provider_id, entry in tuple(self._entries.items()):
            if entry.registration_id == registration_id:
                del self._entries[provider_id]
                return True
        return False

    def _plugin_active(self, entry):
        try:
            return any(getattr(star, "star_cls", None) is entry.plugin
                       and bool(getattr(star, "activated", False))
                       for star in self.context.get_all_stars())
        except Exception:
            return False

    def active(self, entry):
        if (self._closed or not self._host_active() or memory_provider_registry(self.context) is not self
                or self._entries.get(entry.provider_id) is not entry or not self._plugin_active(entry)):
            return False
        if entry.is_active is None:
            return True
        try:
            value = entry.is_active()
            if inspect.isawaitable(value):
                if inspect.iscoroutine(value):
                    value.close()
                return False
            return value is True
        except Exception:
            return False

    def _ordered(self):
        return sorted(tuple(self._entries.values()), key=lambda entry: (-entry.priority, entry.provider_id))

    def select(self, capability=None, *, handles_model_memory=None):
        for entry in self._ordered():
            if capability is not None and getattr(entry, capability, None) is None:
                continue
            if handles_model_memory is not None and entry.handles_model_memory is not handles_model_memory:
                continue
            if self.active(entry):
                return entry
        return None

    def snapshot(self):
        return [{"provider_id": entry.provider_id, "display_name": entry.display_name,
                 "priority": entry.priority, "active": self.active(entry),
                 "handles_model_memory": entry.handles_model_memory,
                 "capabilities": [name for name in ("read", "write", "summary")
                                  if getattr(entry, name) is not None]}
                for entry in self._ordered()]

    async def invoke(self, entry, capability, request):
        if capability not in ("read", "write", "summary") or not self.active(entry):
            raise TaskError("external_memory_provider_unavailable", "memory provider is no longer active")
        callback = getattr(entry, capability)
        if callback is None:
            raise TaskError("external_memory_capability_unavailable", "memory provider lacks this callback")

        async def call():
            if not self.active(entry):
                raise TaskError("external_memory_registration_stale", "memory provider changed before dispatch")
            # Synchronous SDKs must not block AstrBot's event loop. A timed-out
            # write may still finish in its worker, so the host never retries it.
            result = (callback(request) if inspect.iscoroutinefunction(callback)
                      else await asyncio.to_thread(callback, request))
            return await result if inspect.isawaitable(result) else result

        try:
            result = await asyncio.wait_for(call(), timeout=entry.timeout_seconds)
        except asyncio.TimeoutError:
            raise TaskError("external_memory_timeout", "memory provider timed out") from None
        except Exception:
            raise TaskError("external_memory_callback_failed", "memory provider callback failed") from None
        if not self.active(entry):
            raise TaskError("external_memory_registration_stale", "memory provider changed during the call")
        return result

    def close(self):
        self._closed = True
        self._entries.clear()
        if memory_provider_registry(self.context) is self:
            delattr(self.context, REGISTRY_ATTRIBUTE)


__all__ = ["ExternalMemoryProviderRegistry", "MemoryProviderRegistration",
           "PROVIDER_API_VERSION", "memory_context_document", "memory_provider_registry"]
