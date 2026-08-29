from __future__ import annotations

import re
from typing import Any

from .helpers import _single_line


class _IdentityCapabilityFamily:
    """Private capability family backed only by its owning façade."""

    __slots__ = ("_owner",)

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def get_reality_touch_authorized_user_ids(self) -> list[str]:
        """Return host administrators and primary users eligible for device consent."""
        plugin = self._owner._plugin
        owner_getter = getattr(plugin, "_relationship_owner_user_ids", None)
        owners = set(owner_getter() if callable(owner_getter) else ())
        target_getter = getattr(plugin, "_configured_target_ids", None)
        targets = set(target_getter() if callable(target_getter) else ())
        admins = {
            _single_line(item, 120)
            for item in getattr(plugin, "admin_user_ids", ())
            if _single_line(item, 120)
        }
        return sorted(
            {
                _single_line(item, 120)
                for item in owners | targets | admins
                if _single_line(item, 120)
            }
        )

    def get_bot_identity(self) -> dict[str, Any]:
        """Return a stable Bot identity without guessing between multiple accounts."""
        plugin = self._owner._plugin
        self_ids = sorted(
            {
                _single_line(item, 80)
                for item in getattr(plugin, "_known_bot_self_ids", lambda: set())()
                if _single_line(item, 80)
            }
        )
        qq_ids = [item for item in self_ids if re.fullmatch(r"[1-9]\d{4,14}", item)]
        selected_id = self_ids[0] if len(self_ids) == 1 else ""
        qq_id = qq_ids[0] if len(qq_ids) == 1 else ""
        bot_name = _single_line(getattr(plugin, "bot_name", ""), 80)
        return {
            "available": True,
            "name": bot_name,
            "aliases": [bot_name] if bot_name else [],
            "platform": _single_line(getattr(plugin, "target_platform", ""), 80),
            "self_ids": self_ids,
            "selected_id": selected_id,
            "qq_id": qq_id,
            "ambiguous": len(self_ids) > 1 or len(qq_ids) > 1,
            "avatar": {
                "kind": "qq" if qq_id else "fallback",
                "qq_id": qq_id,
                "remote_url": f"https://q1.qlogo.cn/g?b=qq&nk={qq_id}&s=640" if qq_id else "",
            },
        }

    def get_unified_person_contract(self) -> dict[str, Any]:
        return self._owner._plugin.unified_person_contract_status()

    def resolve_unified_person(self, identity: dict[str, Any]) -> dict[str, Any]:
        return self._owner._plugin.resolve_unified_person_identity(identity)

    def create_unified_person(
        self,
        identity: dict[str, Any],
        *,
        profile: dict[str, Any] | None = None,
        operation_id: str = "",
    ) -> dict[str, Any]:
        return self._owner._plugin.create_unified_person(identity, profile=profile, operation_id=operation_id)

    def get_unified_person_projection(self, person_id: str) -> dict[str, Any] | None:
        return self._owner._plugin.get_unified_person_projection(person_id)

    def get_unified_person_context(self, event: Any | None = None) -> dict[str, Any]:
        return self._owner._plugin.build_unified_person_context(event)

    def resolve_historical_chat_identities(self, speakers: list[str]) -> dict[str, Any]:
        plugin = self._owner._plugin
        labels = [_single_line(item, 80) for item in speakers if _single_line(item, 80)]
        matches: dict[str, list[dict[str, Any]]] = {}
        resolver = getattr(plugin, "_resolve_worldbook_member_by_name", None)
        for label in labels:
            candidates = resolver(label) if callable(resolver) else []
            matches[label] = [
                {
                    "user_id": _single_line(item.get("user_id"), 80),
                    "name": _single_line(item.get("name"), 80),
                    "aliases": [
                        _single_line(alias, 40)
                        for alias in (item.get("aliases") or [])
                        if _single_line(alias, 40)
                    ][:12],
                    "observed_names": [
                        _single_line(alias, 40)
                        for alias in (item.get("observed_names") or [])
                        if _single_line(alias, 40)
                    ][:12],
                    "identity_note": _single_line(item.get("identity_note"), 240),
                }
                for item in (candidates or [])
                if isinstance(item, dict)
            ][:8]
        users = plugin.data.get("users") if isinstance(plugin.data.get("users"), dict) else {}
        configured_targets = getattr(plugin, "_configured_target_ids", None)
        target_ids = set(str(item) for item in (configured_targets() if callable(configured_targets) else []) or [])
        target_users: list[dict[str, Any]] = []
        for user_id, raw in users.items():
            if not isinstance(raw, dict):
                continue
            if target_ids and str(user_id) not in target_ids:
                continue
            target_users.append(
                {
                    "user_id": _single_line(user_id, 80),
                    "name": _single_line(raw.get("nickname") or raw.get("display_name") or user_id, 80),
                }
            )
        bot_identity = self._owner.get_bot_identity()
        return {
            "available": True,
            "matches": matches,
            "bot": {
                "name": bot_identity.get("name", ""),
                "aliases": bot_identity.get("aliases", []),
                "self_ids": bot_identity.get("self_ids", []),
                "selected_id": bot_identity.get("selected_id", ""),
                "qq_id": bot_identity.get("qq_id", ""),
            },
            "target_users": target_users[:30],
        }
