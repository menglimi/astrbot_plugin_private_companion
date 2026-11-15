"""External ownership declarations for role state and schedule.

The public extension facade already lets third-party plugins register memory
providers.  State and schedule are different: one owner must be able to stop
V2's own background maintenance/planning before it writes through this API.
This module keeps only that ownership ledger; the host facade performs the
authorized reads and writes against the ordinary V2 services.
"""

from __future__ import annotations

import threading
import time
from uuid import uuid4

STATE_API_VERSION = "companion.external-state.v1"
SCHEDULE_API_VERSION = "companion.external-schedule.v1"
KINDS = ("state", "schedule")


class ExternalOwnershipError(ValueError):
    pass


class ExternalOwnershipRegistry:
    """One active external owner per kind for one installation."""

    def __init__(self, installation_id: str):
        self.installation_id = installation_id
        self._lock = threading.RLock()
        self._entries: dict[str, dict] = {}

    def _active_entries(self, kind: str) -> list[dict]:
        entries = []
        for token, entry in tuple(self._entries.items()):
            if entry["kind"] != kind:
                continue
            try:
                active = entry["is_active"]()
            except Exception:
                active = False
            if active is not True:
                continue
            entries.append({**entry, "token": token})
        entries.sort(key=lambda item: (-item["priority"], item["registered_at"]))
        return entries

    def active(self, kind: str) -> dict | None:
        if kind not in KINDS:
            raise ExternalOwnershipError("unknown external ownership kind")
        with self._lock:
            entries = self._active_entries(kind)
            return entries[0] if entries else None

    def register(self, kind: str, provider_id: str, *, plugin, display_name="", priority=0,
                 is_active=None) -> str:
        if kind not in KINDS:
            raise ExternalOwnershipError("unknown external ownership kind")
        if type(provider_id) is not str or not provider_id.strip() or len(provider_id) > 128:
            raise ExternalOwnershipError("provider id must be a bounded non-empty string")
        if plugin is None:
            raise ExternalOwnershipError("a live plugin instance is required")
        if is_active is not None and not callable(is_active):
            raise ExternalOwnershipError("is_active must be callable")
        if type(priority) is not int:
            raise ExternalOwnershipError("priority must be an integer")
        with self._lock:
            active = self._active_entries(kind)
            if active:
                raise ExternalOwnershipError(
                    f"{kind} is already owned by {active[0]['provider_id']}")
            token = kind + ":" + uuid4().hex
            self._entries[token] = {
                "kind": kind, "provider_id": provider_id.strip(), "plugin": plugin,
                "display_name": str(display_name or provider_id)[:128], "priority": priority,
                "is_active": is_active or (lambda: True), "registered_at": time.time(),
            }
            return token

    def unregister(self, token: str) -> bool:
        with self._lock:
            return self._entries.pop(token, None) is not None

    def authorize(self, token: str, kind: str) -> dict:
        with self._lock:
            entry = self._entries.get(token)
            if entry is None or entry["kind"] != kind:
                raise ExternalOwnershipError("this provider token is not registered for that domain")
            try:
                active = entry["is_active"]()
            except Exception as error:
                raise ExternalOwnershipError("provider activity check failed") from error
            if active is not True:
                raise ExternalOwnershipError("provider is not active")
            active_entry = self.active(kind)
            if active_entry is None or active_entry["token"] != token:
                raise ExternalOwnershipError("provider is no longer the active owner")
            return {key: value for key, value in entry.items() if key != "plugin"}

    def snapshot(self) -> dict:
        with self._lock:
            result = {}
            for kind in KINDS:
                active = self.active(kind)
                result[kind] = None if active is None else {
                    "provider_id": active["provider_id"], "display_name": active["display_name"],
                    "priority": active["priority"], "registered_at": active["registered_at"],
                }
            return result


_REGISTRIES: dict[str, ExternalOwnershipRegistry] = {}
_REGISTRIES_LOCK = threading.RLock()


def registry_for(installation_id: str) -> ExternalOwnershipRegistry:
    with _REGISTRIES_LOCK:
        registry = _REGISTRIES.get(installation_id)
        if registry is None:
            registry = _REGISTRIES[installation_id] = ExternalOwnershipRegistry(installation_id)
        return registry


__all__ = ["ExternalOwnershipError", "ExternalOwnershipRegistry", "KINDS",
           "SCHEDULE_API_VERSION", "STATE_API_VERSION", "registry_for"]
