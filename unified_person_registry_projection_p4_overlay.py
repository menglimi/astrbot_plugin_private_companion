# -*- coding: utf-8 -*-
"""UnifiedPersonRegistryProjectionP4OverlayMixin。

由 tools/split_mixin_domain.py 从 unified_person_registry.py 机械抽取（11 个方法 + 0 个模块级名字 + 0 个类级赋值 / 422 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UnifiedPersonRegistry）。
"""
from __future__ import annotations

try:  # package import
    from .unified_person_registry_shared import (
        _LOCK,
        _fingerprint,
        _identity,
        _normalize_p4_effect_event,
        _now,
        _operation_id,
        _p4_effect_container,
        _p4_effect_fingerprint,
        _p4_effect_state,
        _p4_effect_summary,
        _replay_p4_effect_entry,
        _root,
        _safe,
        _text,
    )
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import (
        _LOCK,
        _fingerprint,
        _identity,
        _normalize_p4_effect_event,
        _now,
        _operation_id,
        _p4_effect_container,
        _p4_effect_fingerprint,
        _p4_effect_state,
        _p4_effect_summary,
        _replay_p4_effect_entry,
        _root,
        _safe,
        _text,
    )
try:  # package import
    from .unified_person_registry_shared import Any
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import Any
try:  # package import
    from .unified_person_registry_shared import build_identity_key
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import build_identity_key
try:  # package import
    from .unified_person_registry_shared import build_person_projection
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import build_person_projection
try:  # package import
    from .unified_person_registry_shared import deepcopy
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import deepcopy
try:  # package import
    from .unified_person_registry_shared import hashlib
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import hashlib
try:  # package import
    from .unified_person_registry_shared import json
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import json
try:  # package import
    from .unified_person_registry_shared import validate_projection
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import validate_projection
try:  # package import
    from .unified_person_registry_shared import validate_runtime_state
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import validate_runtime_state



class UnifiedPersonRegistryProjectionP4OverlayMixin:
    """UnifiedPersonRegistryProjectionP4OverlayMixin（从 UnifiedPersonRegistry 拆出）。"""


    def read_projection(self, person_id: str) -> dict[str, Any] | None:
        with _LOCK:
            projection = build_person_projection(self._store, str(person_id or ""))
            return deepcopy(projection) if projection and not validate_projection(projection) else None

    def identity_link_state(self, person_id: str, identity_key: str) -> dict[str, Any]:
        """Verify one redacted link reference without exposing its raw identity."""
        try:
            clean_person = _text(person_id, "person_id")
            clean_key = _text(identity_key, "identity_key", 160)
        except ValueError:
            return {"ok": False, "code": "identity_reference_invalid"}
        with _LOCK:
            root = _root(self._store)
            profile = root["profiles"].get(clean_person)
            projection_revision = (
                int(profile.get("projection_revision") or 0) if isinstance(profile, dict) else 0
            )
            for state, container_name in (
                ("active", "identity_links"), ("detached", "detached_identity_links")
            ):
                candidate = root[container_name].get(clean_key)
                if not isinstance(candidate, dict) or candidate.get("person_id") != clean_person:
                    continue
                try:
                    normalized = _identity(candidate.get("identity"))
                    exact = (
                        candidate.get("identity_key") == clean_key
                        and build_identity_key(normalized) == clean_key
                        and candidate.get("status") == state
                    )
                except (TypeError, ValueError):
                    exact = False
                if not exact:
                    return {"ok": False, "code": "identity_link_corrupt"}
                return {
                    "ok": True,
                    "code": "identity_link_verified",
                    "state": state,
                    "identity_assurance": str(candidate.get("identity_assurance") or "observed"),
                    "profile_status": str(profile.get("profile_status") or "active") if isinstance(profile, dict) else "deleted",
                    "projection_revision": projection_revision,
                }
        return {"ok": False, "code": "identity_link_missing"}

    def identity_projection_checkpoint(self, person_id: str) -> dict[str, Any]:
        """Return a hash-only checkpoint for detecting uncaptured identity writes."""
        try:
            clean_person = _text(person_id, "person_id")
        except ValueError:
            return {"ok": False, "code": "identity_reference_invalid"}
        with _LOCK:
            root = _root(self._store)
            profile = root["profiles"].get(clean_person)
            projection = build_person_projection(self._store, clean_person)
            if not isinstance(profile, dict) or projection is None or validate_projection(projection):
                return {"ok": False, "code": "identity_projection_invalid"}
            profile_keys = profile.get("identity_keys")
            if not isinstance(profile_keys, list) or any(not isinstance(item, str) for item in profile_keys):
                return {"ok": False, "code": "identity_projection_invalid"}
            active_keys = sorted(
                str(key)
                for key, link in root["identity_links"].items()
                if isinstance(link, dict)
                and link.get("person_id") == clean_person
                and link.get("status") == "active"
            )
            if sorted(profile_keys) != active_keys:
                return {"ok": False, "code": "identity_projection_invalid"}
            state = {
                "person_id": clean_person,
                "resolved_identity_key": projection["resolved_identity_key"],
                "identity_assurance": projection["identity_assurance"],
                "profile_status": projection["profile_status"],
                "projection_revision": projection["projection_revision"],
                "active_identity_keys_hash": hashlib.sha256(
                    json.dumps(active_keys, separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
            }
            return {
                "ok": True,
                "code": "identity_projection_checkpoint",
                "projection_revision": state["projection_revision"],
                "checkpoint_hash": hashlib.sha256(
                    json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
            }

    def identity_recovery_state(self, person_id: str) -> dict[str, Any]:
        """Return hash-only active/detached refs for durable gap recovery."""
        checkpoint = self.identity_projection_checkpoint(person_id)
        if not checkpoint.get("ok"):
            return checkpoint
        with _LOCK:
            root = _root(self._store)
            active_keys = sorted(
                str(key)
                for key, link in root["identity_links"].items()
                if isinstance(link, dict)
                and link.get("person_id") == person_id
                and link.get("status") == "active"
            )
            detached_keys: list[str] = []
            for key, link in root["detached_identity_links"].items():
                if not isinstance(link, dict) or link.get("person_id") != person_id:
                    continue
                try:
                    normalized = _identity(link.get("identity"))
                    valid = (
                        link.get("status") == "detached"
                        and link.get("identity_key") == key
                        and build_identity_key(normalized) == key
                    )
                except (TypeError, ValueError):
                    valid = False
                if not valid:
                    return {"ok": False, "code": "identity_detached_link_corrupt"}
                detached_keys.append(str(key))
            return {
                **checkpoint,
                "resolved_identity_key": active_keys[0] if active_keys else "",
                "active_identity_keys": active_keys,
                "detached_identity_keys": sorted(detached_keys),
            }

    def read_p4_effect_state(self, person_id: str) -> dict[str, Any]:
        """Read preparation state without creating a ledger or a person."""
        try:
            person_id = _text(person_id, "person_id")
        except ValueError:
            return {"ok": False, "code": "invalid_request", "person_id": ""}
        with _LOCK:
            root = _root(self._store)
            profile = root["profiles"].get(person_id)
            if not isinstance(profile, dict):
                return {"ok": False, "code": "person_not_found", "person_id": person_id}
            if profile.get("profile_status") != "active":
                return {"ok": False, "code": "person_not_active", "person_id": person_id}
            container = root.get("p4_effect")
            if container is None:
                state = _p4_effect_state([])
                return {
                    "ok": True,
                    "code": "p4_effect_empty",
                    "person_id": person_id,
                    "p4_effect_exists": False,
                    "p4_effect_state": state,
                    "p4_effect_summary": _p4_effect_summary(state),
                    "event_count": 0,
                }
            if _p4_effect_container(root) is None:
                return {"ok": False, "code": "p4_effect_corrupt", "person_id": person_id}
            entry = container["people"].get(person_id)
            if entry is None:
                state = _p4_effect_state([])
                return {
                    "ok": True,
                    "code": "p4_effect_empty",
                    "person_id": person_id,
                    "p4_effect_exists": False,
                    "p4_effect_state": state,
                    "p4_effect_summary": _p4_effect_summary(state),
                    "event_count": 0,
                }
            state, event_index, error = _replay_p4_effect_entry(entry, person_id)
            if error or state is None or event_index is None:
                return {"ok": False, "code": error or "p4_effect_corrupt", "person_id": person_id}
            return {
                "ok": True,
                "code": "p4_effect_read",
                "person_id": person_id,
                "p4_effect_exists": True,
                "p4_effect_state": state,
                "p4_effect_summary": _p4_effect_summary(state),
                "event_count": len(event_index),
            }

    def read_p4_live_state(self, person_id: str) -> dict[str, Any]:
        """Read a separately-owned live state without creating or repairing it."""
        try:
            person_id = _text(person_id, "person_id")
        except ValueError:
            return {"ok": False, "code": "invalid_request", "person_id": ""}
        with _LOCK:
            root = _root(self._store)
            profile = root["profiles"].get(person_id)
            if not isinstance(profile, dict) or profile.get("profile_status") != "active":
                return {"ok": False, "code": "person_not_active", "person_id": person_id}
            container = root.get("p4_live")
            if container is None:
                return {"ok": True, "code": "p4_live_state_absent", "person_id": person_id, "state": None}
            if type(container) is not dict or container.get("version") != 1 or type(container.get("people")) is not dict:
                return {"ok": False, "code": "p4_live_state_corrupt", "person_id": person_id}
            state = container["people"].get(person_id)
            if state is None:
                return {"ok": True, "code": "p4_live_state_absent", "person_id": person_id, "state": None}
            if type(state) is not dict:
                return {"ok": False, "code": "p4_live_state_corrupt", "person_id": person_id}
            return {"ok": True, "code": "p4_live_state_read", "person_id": person_id, "state": deepcopy(state)}

    def record_p4_live_state(
        self,
        person_id: str,
        state: dict[str, Any],
        *,
        operation_id: str,
        actor_id: str = "companion",
    ) -> dict[str, Any]:
        """Persist only an exact Companion-owned runtime state with replay safety."""
        try:
            person_id = _text(person_id, "person_id")
            operation_id = _text(operation_id, "operation_id", 120)
            actor_id = _text(actor_id, "actor_id", 120)
        except ValueError:
            return {"ok": False, "code": "invalid_request"}
        if actor_id != "companion" or validate_runtime_state(state) == "invalid":
            return {"ok": False, "code": "p4_live_state_rejected", "person_id": person_id, "operation_id": operation_id}
        copied_state = deepcopy(state)
        request_fingerprint = _fingerprint({"person_id": person_id, "state": copied_state, "actor_id": actor_id})
        with _LOCK:
            root = _root(self._store)
            profile = root["profiles"].get(person_id)
            if not isinstance(profile, dict) or profile.get("profile_status") != "active":
                return {"ok": False, "code": "person_not_active", "person_id": person_id, "operation_id": operation_id}
            container = root.get("p4_live")
            if container is None:
                container = {"version": 1, "people": {}, "operations": {}}
                root["p4_live"] = container
            if (
                type(container) is not dict
                or container.get("version") != 1
                or type(container.get("people")) is not dict
                or type(container.get("operations")) is not dict
            ):
                return {"ok": False, "code": "p4_live_state_corrupt", "person_id": person_id, "operation_id": operation_id}
            prior = container["operations"].get(operation_id)
            if isinstance(prior, dict):
                if prior.get("request_fingerprint") != request_fingerprint:
                    return {"ok": False, "code": "operation_id_conflict", "person_id": person_id, "operation_id": operation_id}
                result = deepcopy(prior.get("result"))
                if isinstance(result, dict):
                    result["idempotent"] = True
                    return result
                return {"ok": False, "code": "p4_live_state_corrupt", "person_id": person_id, "operation_id": operation_id}
            container["people"][person_id] = copied_state
            result = {"ok": True, "code": "p4_live_state_recorded", "person_id": person_id, "operation_id": operation_id, "changed": True}
            container["operations"][operation_id] = {"request_fingerprint": request_fingerprint, "result": deepcopy(result)}
            root["audit_events"].append({"event_id": operation_id, "action": "p4_live_state_recorded", "actor_id": actor_id, "person_id": person_id, "at": _now()})
            root["audit_events"] = root["audit_events"][-1000:]
            return result

    def record_p4_effect_event(
        self,
        person_id: str,
        event: dict[str, Any],
        *,
        operation_id: str,
        actor_id: str = "system",
    ) -> dict[str, Any]:
        """Append a replayable preparation event without enabling a live effect."""
        try:
            person_id = _text(person_id, "person_id")
            operation_id = _text(operation_id, "operation_id", 120)
            actor_id = _text(actor_id, "actor_id", 120)
        except ValueError:
            return {"ok": False, "code": "invalid_request"}
        normalized_event, error = _normalize_p4_effect_event(event)
        if normalized_event is None:
            return {"ok": False, "code": error or "invalid_p4_effect_event", "person_id": person_id, "operation_id": operation_id}
        payload_fingerprint = _fingerprint({"person_id": person_id, "event": normalized_event, "actor_id": actor_id})
        with _LOCK:
            root = _root(self._store)
            profile = root["profiles"].get(person_id)
            if not isinstance(profile, dict):
                return {"ok": False, "code": "person_not_found", "person_id": person_id, "operation_id": operation_id}
            if profile.get("profile_status") != "active":
                return {"ok": False, "code": "person_not_active", "person_id": person_id, "operation_id": operation_id}
            container = _p4_effect_container(root)
            if container is None:
                return {"ok": False, "code": "p4_effect_corrupt", "person_id": person_id, "operation_id": operation_id}
            previous_operation = container["operations"].get(operation_id)
            if isinstance(previous_operation, dict):
                if previous_operation.get("request_fingerprint") != payload_fingerprint:
                    return {"ok": False, "code": "operation_id_conflict", "person_id": person_id, "operation_id": operation_id}
                result = deepcopy(previous_operation.get("result") or {})
                if isinstance(result, dict):
                    result["idempotent"] = True
                    return result
                return {"ok": False, "code": "p4_effect_corrupt", "person_id": person_id, "operation_id": operation_id}

            people = container["people"]
            entry = people.get(person_id)
            if entry is None:
                now = _now()
                entry = {
                    "person_id": person_id,
                    "state": _p4_effect_state([]),
                    "events": [],
                    "created_at": now,
                    "updated_at": now,
                    "last_operation_id": "",
                }
            state, event_index, replay_error = _replay_p4_effect_entry(entry, person_id) if entry else (_p4_effect_state([]), {}, "")
            if replay_error or state is None or event_index is None:
                return {"ok": False, "code": replay_error or "p4_effect_corrupt", "person_id": person_id, "operation_id": operation_id}
            event_id = normalized_event["event_id"]
            fingerprint = _p4_effect_fingerprint(person_id, normalized_event)
            known = event_index.get(event_id)
            if known is not None:
                if known.get("event_fingerprint") != fingerprint:
                    return {"ok": False, "code": "p4_effect_event_id_conflict", "person_id": person_id, "event_id": event_id, "operation_id": operation_id}
                result = {
                    "ok": True,
                    "code": "p4_effect_event_duplicate",
                    "person_id": person_id,
                    "event_id": event_id,
                    "operation_id": operation_id,
                    "event_duplicate": True,
                    "p4_effect_summary": _p4_effect_summary(state),
                    "live_effect_permitted": False,
                }
                container["operations"][operation_id] = {"request_fingerprint": payload_fingerprint, "result": deepcopy(result)}
                return result
            now = _now()
            entry["events"].append({
                "event_id": event_id,
                "person_id": person_id,
                "origin_person_id": person_id,
                "event": deepcopy(normalized_event),
                "event_fingerprint": fingerprint,
                "recorded_at": now,
                "operation_id": operation_id,
            })
            replay_state = _p4_effect_state([item["event"] for item in entry["events"]])
            entry["state"] = replay_state
            entry["updated_at"] = now
            entry["last_operation_id"] = operation_id
            people[person_id] = entry
            root["audit_events"].append({
                "event_id": operation_id,
                "action": "p4_effect_event_recorded",
                "actor_id": actor_id,
                "person_id": person_id,
                "at": now,
                "kind": normalized_event["kind"],
            })
            root["audit_events"] = root["audit_events"][-1000:]
            result = {
                "ok": True,
                "code": "p4_effect_event_recorded",
                "person_id": person_id,
                "event_id": event_id,
                "operation_id": operation_id,
                "changed": True,
                "affected_person_ids": [person_id],
                "p4_effect_summary": _p4_effect_summary(replay_state),
                "live_effect_permitted": False,
            }
            container["operations"][operation_id] = {"request_fingerprint": payload_fingerprint, "result": deepcopy(result)}
            return result

    def guard_p4_effect_person_transition(
        self,
        action: str,
        source_person_id: str,
        target_person_id: str,
        *,
        operation_id: str,
    ) -> dict[str, Any]:
        """Reject unsupported identity merge/split before it can touch the P4 ledger.

        Chat-side Unified Person deliberately has no person-lifecycle merge or
        split operation.  A future caller must implement an explicit replay
        migration rather than silently reassigning preparation entries.
        """
        try:
            action = _text(action, "action", 40)
            source_person_id = _text(source_person_id, "source_person_id")
            target_person_id = _text(target_person_id, "target_person_id")
            operation_id = _text(operation_id, "operation_id", 120)
        except ValueError:
            return {"ok": False, "code": "invalid_request"}
        if action not in {"merge", "split"} or source_person_id == target_person_id:
            return {"ok": False, "code": "p4_effect_transition_rejected", "operation_id": operation_id}
        with _LOCK:
            # Do not call the P4 container helper here: creating or repairing
            # a ledger would itself violate the no-transition guarantee.
            root = _root(self._store)
            source = root["profiles"].get(source_person_id)
            target = root["profiles"].get(target_person_id)
            if not isinstance(source, dict) or not isinstance(target, dict):
                return {"ok": False, "code": "p4_effect_transition_person_not_found", "operation_id": operation_id}
            return {
                "ok": False,
                "code": "p4_effect_transition_unsupported",
                "operation_id": operation_id,
                "action": action,
            }

    def upsert_group_overlay(self, person_id: str, group_scope: str, overlay: dict[str, Any], operation_id: str = "", actor_id: str = "companion", **_: Any) -> dict[str, Any]:
        try:
            person_id, group_scope, op = _text(person_id, "person_id"), _text(group_scope, "group_scope", 240), _operation_id(operation_id)
            _text(actor_id, "actor_id", 120)
        except ValueError:
            return {"ok": False, "state": "invalid", "code": "invalid_request", "person_id": person_id if isinstance(person_id, str) else ""}
        if not op:
            return {"ok": False, "state": "pending", "code": "explicit_operation_required", "person_id": person_id, "group_scope": group_scope}
        safe = _safe(overlay)
        if not isinstance(safe, dict):
            return {"ok": False, "state": "invalid", "code": "overlay_invalid", "person_id": person_id, "group_scope": group_scope}
        with _LOCK:
            root = _root(self._store)
            if not isinstance(root["profiles"].get(person_id), dict):
                return {"ok": False, "state": "pending", "code": "person_not_found", "person_id": person_id, "group_scope": group_scope}
            key = f"{person_id}:{_fingerprint(group_scope)[:32]}"
            previous = root["group_overlays"].get(key)
            revision = int(previous.get("revision") or 0) + 1 if isinstance(previous, dict) else 1
            root["group_overlays"][key] = {"person_id": person_id, "group_scope": group_scope, "overlay": safe, "revision": revision, "updated_at": _now(), "operation_id": op}
            return {"ok": True, "state": "resolved", "code": "group_overlay_upserted", "person_id": person_id, "group_scope": group_scope, "revision": revision, "changed": previous != root["group_overlays"][key]}

    def read_group_overlay(self, person_id: str, group_scope: str) -> dict[str, Any] | None:
        try:
            person_id, group_scope = _text(person_id, "person_id"), _text(group_scope, "group_scope", 240)
        except ValueError:
            return None
        with _LOCK:
            root = _root(self._store)
            key = f"{person_id}:{_fingerprint(group_scope)[:32]}"
            record = root["group_overlays"].get(key)
            if not isinstance(record, dict) or record.get("person_id") != person_id or record.get("group_scope") != group_scope:
                return None
            return deepcopy(record)
