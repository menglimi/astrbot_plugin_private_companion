"""Host-local registrations for third-party executable role activities."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
import math
from uuid import uuid4

from companion_v2.execution.models import TaskError


REGISTRY_ATTRIBUTE = "_private_companion_activity_providers"
ACTIVITY_API_VERSION = "companion.external-activity.v1"


def activity_provider_registry(context):
    return getattr(context, REGISTRY_ATTRIBUTE, None)


@dataclass(frozen=True)
class ActivityProviderRegistration:
    provider_id: str
    registration_id: str
    plugin: object
    display_name: str
    purpose: str
    min_minutes: int
    max_minutes: int
    timeout_seconds: float
    execute: object
    is_active: object = None


class ExternalActivityProviderRegistry:
    """Callbacks are leased to one live Star instance and one host lifetime."""

    def __init__(self, context, *, host_active=lambda: True):
        self.context = context
        self._host_active = host_active
        self._entries = {}
        self._closed = False
        setattr(context, REGISTRY_ATTRIBUTE, self)

    def register(self, provider_id, *, plugin, execute, display_name="", purpose="life",
                 min_minutes=1, max_minutes=15, timeout_seconds=120.0, is_active=None):
        if self._closed or not self._host_active() or activity_provider_registry(self.context) is not self:
            raise ValueError("activity provider registry is closed")
        if not isinstance(provider_id, str) or not provider_id.strip() or provider_id != provider_id.strip():
            raise ValueError("activity provider requires a non-blank provider_id")
        if len(provider_id) > 120:
            raise ValueError("activity provider_id is too long")
        if plugin is None or not callable(execute):
            raise ValueError("activity provider requires its owning plugin and execute callback")
        if not isinstance(display_name, str) or len(display_name) > 120:
            raise ValueError("invalid activity display name")
        if not isinstance(purpose, str) or not purpose.strip() or len(purpose) > 32:
            raise ValueError("activity provider requires a bounded purpose")
        if (type(min_minutes) is not int or type(max_minutes) is not int
                or not 1 <= min_minutes <= max_minutes <= 120):
            raise ValueError("activity duration must be an integer range from 1 to 120 minutes")
        if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or not 1 <= timeout_seconds <= 900):
            raise ValueError("activity timeout must be from 1 to 900 seconds")
        if is_active is not None and not callable(is_active):
            raise ValueError("activity is_active must be callable")
        previous = self._entries.get(provider_id)
        if previous is not None and previous.plugin is not plugin and self._plugin_active(previous):
            raise ValueError("activity provider_id belongs to another active plugin")
        entry = ActivityProviderRegistration(provider_id, uuid4().hex, plugin,
            display_name.strip() or provider_id, purpose.strip(), min_minutes, max_minutes,
            float(timeout_seconds), execute, is_active)
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
        if (self._closed or not self._host_active() or activity_provider_registry(self.context) is not self
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
        return sorted(tuple(self._entries.values()), key=lambda entry: (-entry.max_minutes, entry.provider_id))

    def select(self, purpose=None, *, provider_id=None):
        for entry in self._ordered():
            if provider_id is not None and entry.provider_id != provider_id:
                continue
            if purpose is not None and entry.purpose != purpose:
                continue
            if self.active(entry):
                return entry
        return None

    def snapshot(self):
        return [{"provider_id": entry.provider_id, "display_name": entry.display_name,
                 "purpose": entry.purpose, "min_minutes": entry.min_minutes,
                 "max_minutes": entry.max_minutes, "active": self.active(entry)}
                for entry in self._ordered()]

    async def invoke(self, entry, context, request, *, seconds=None):
        if not self.active(entry):
            raise TaskError("activity_provider_unavailable", "the activity provider is no longer active")
        budget = entry.timeout_seconds if seconds is None else min(entry.timeout_seconds, float(seconds))
        if budget <= 0:
            raise TaskError("activity_timeout_invalid", "activity time budget is invalid")
        try:
            value = entry.execute(context, request)
            if inspect.isawaitable(value):
                value = await asyncio.wait_for(value, timeout=budget)
            return value
        except asyncio.TimeoutError as error:
            raise TaskError("activity_timeout", "the activity provider did not finish in time") from error
        except TaskError:
            raise
        except Exception as error:
            raise TaskError("activity_provider_failed", "the activity provider failed") from error

    def close(self):
        self._closed = True
        self._entries.clear()
        if activity_provider_registry(self.context) is self:
            try:
                delattr(self.context, REGISTRY_ATTRIBUTE)
            except AttributeError:
                pass
