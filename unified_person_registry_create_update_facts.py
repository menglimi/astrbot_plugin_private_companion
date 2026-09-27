# -*- coding: utf-8 -*-
"""UnifiedPersonRegistryCreateUpdateFactsMixin。

由 tools/split_mixin_domain.py 从 unified_person_registry.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 259 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UnifiedPersonRegistry）。
"""
from __future__ import annotations

try:  # package import
    from .unified_person_registry_shared import (
        _CONTROL_CHARACTER_RE,
        _LOCK,
        _PROFILE_FACT_FIELDS,
        _fingerprint,
        _identity,
        _now,
        _operation_id,
        _root,
        _safe,
        _safe_affinity_score,
        _text,
    )
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import (
        _CONTROL_CHARACTER_RE,
        _LOCK,
        _PROFILE_FACT_FIELDS,
        _fingerprint,
        _identity,
        _now,
        _operation_id,
        _root,
        _safe,
        _safe_affinity_score,
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
    from .unified_person_registry_shared import person_id_for_identity
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import person_id_for_identity
try:  # package import
    from .unified_person_registry_shared import validate_projection
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import validate_projection



class UnifiedPersonRegistryCreateUpdateFactsMixin:
    """UnifiedPersonRegistryCreateUpdateFactsMixin（从 UnifiedPersonRegistry 拆出）。"""


    def create_or_link(
        self, identity: dict[str, Any], profile: dict[str, Any] | None = None,
        operation_id: str = "", actor_id: str = "companion", **_: Any,
    ) -> dict[str, Any]:
        """Create a person for an explicit operation, or return the existing link."""
        try:
            normalized = _identity(identity)
            op = _operation_id(operation_id)
            actor = _text(actor_id, "actor_id", 120)
        except ValueError:
            return {"ok": False, "state": "invalid", "code": "invalid_request", "person_id": ""}
        if not op:
            return {"ok": False, "state": "pending", "code": "explicit_operation_required", "person_id": "", "identity_key": build_identity_key(normalized)}
        safe_profile = _safe(profile or {})
        if not isinstance(safe_profile, dict):
            safe_profile = {}
        display_name = safe_profile.get("display_name")
        if not isinstance(display_name, str) or not display_name:
            display_name = "unknown_person"
        aliases = safe_profile.get("aliases")
        aliases = [item for item in aliases if isinstance(item, str) and item] if isinstance(aliases, list) else []
        relation_policy_id = safe_profile.get("relation_policy_id")
        if not isinstance(relation_policy_id, str) or not relation_policy_id:
            relation_policy_id = "default_friend"
        owner_mode = safe_profile.get("owner_mode")
        if owner_mode not in {"owner", "not_owner"}:
            owner_mode = "not_owner"
        key = build_identity_key(normalized)
        person_id = person_id_for_identity(normalized)
        with _LOCK:
            root = _root(self._store)
            identity_tombstone = root["identity_tombstones"].get(key)
            if isinstance(identity_tombstone, dict):
                return {
                    "ok": False, "state": "deleted", "code": "identity_archived",
                    "person_id": str(identity_tombstone.get("person_id") or ""),
                    "identity_key": key, "changed": False,
                }
            detached = root["detached_identity_links"].get(key)
            if isinstance(detached, dict):
                return {
                    "ok": False, "state": "detached", "code": "identity_relink_required",
                    "person_id": str(detached.get("person_id") or ""),
                    "identity_key": key, "changed": False,
                }
            existing = root["identity_links"].get(key)
            if isinstance(existing, dict) and existing.get("person_id"):
                existing_id = str(existing["person_id"])
                projection = build_person_projection(self._store, existing_id)
                state = "resolved" if projection and not validate_projection(projection) else "invalid"
                return {"ok": state == "resolved", "state": state, "code": "already_linked", "person_id": existing_id, "identity_key": key, "projection": projection, "changed": False}
            if person_id in root["profiles"]:
                return {
                    "ok": False,
                    "state": "invalid",
                    "code": "person_record_conflict",
                    "person_id": person_id,
                    "identity_key": key,
                    "changed": False,
                }
            now = _now()
            stored = {
                "person_id": person_id,
                "resolved_identity_key": key,
                "identity_keys": [key],
                "identity_assurance": "observed",
                "profile_status": "active",
                # The contract requires a non-empty display name.  Keep the
                # fallback generic; never derive it from message content.
                "display_name": display_name,
                "preferred_address": (
                    safe_profile.get("preferred_address")
                    if isinstance(safe_profile.get("preferred_address"), str) else ""
                ),
                "style": safe_profile.get("style") if isinstance(safe_profile.get("style"), str) else "",
                "profile_origin": (
                    safe_profile.get("profile_origin")
                    if isinstance(safe_profile.get("profile_origin"), str) else ""
                ),
                "auto_profile_created": bool(safe_profile.get("auto_profile_created", False)),
                "profile_fact_revision": 1,
                "aliases": aliases,
                "relation_policy_id": relation_policy_id,
                "owner_mode": owner_mode,
                "affinity_score": _safe_affinity_score(safe_profile.get("affinity_score")),
                "group_overlay_ref": "",
                "projection_revision": 1,
                "updated_at": now,
            }
            root["profiles"][person_id] = stored
            root["identity_links"][key] = {
                "identity_key": key, "identity": normalized, "person_id": person_id,
                "identity_assurance": "observed", "status": "active",
                "created_at": now, "updated_at": now, "last_operation_id": op,
            }
            projection = build_person_projection(self._store, person_id)
            if projection is None or validate_projection(projection):
                root["identity_links"].pop(key, None)
                root["profiles"].pop(person_id, None)
                return {
                    "ok": False,
                    "state": "invalid",
                    "code": "projection_invalid",
                    "person_id": person_id,
                    "identity_key": key,
                    "changed": False,
                }
            root["binding_checkpoints"][f"{person_id}:{key}"] = {
                "person_id": person_id,
                "identity_key": key,
                "origin_identity_key": key,
                "relationship_score": stored["affinity_score"],
                "created_at": now,
                "operation_id": op,
                "source_event_count": 0,
            }
            root["audit_events"].append({"event_id": op, "action": "create_or_link", "actor_id": actor, "person_id": person_id, "at": now})
            return {"ok": True, "state": "resolved", "code": "created", "person_id": person_id, "identity_key": key, "projection": projection, "changed": True}

    def identity_profile_facts(self, person_id: str) -> dict[str, Any]:
        """Read the bounded person-wide facts that may cross chat namespaces."""
        try:
            clean_person = _text(person_id, "person_id")
        except ValueError:
            return {"ok": False, "code": "identity_reference_invalid", "facts": {}}
        with _LOCK:
            root = _root(self._store)
            profile = root["profiles"].get(clean_person)
            if not isinstance(profile, dict):
                return {"ok": False, "code": "person_not_found", "facts": {}}
            if profile.get("profile_status", "active") != "active":
                return {"ok": False, "code": "person_not_active", "facts": {}}
            facts = {
                key: deepcopy(profile.get(key))
                for key in _PROFILE_FACT_FIELDS
                if key in profile
            }
            return {
                "ok": True,
                "code": "identity_profile_facts",
                "person_id": clean_person,
                "profile_fact_revision": max(1, int(profile.get("profile_fact_revision") or 1)),
                "facts": facts,
            }

    def update_identity_profile_facts(
        self,
        person_id: str,
        changes: dict[str, Any],
        *,
        operation_id: str,
        actor_id: str = "companion",
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Update person-wide facts without touching relationship or channel capabilities."""
        try:
            clean_person = _text(person_id, "person_id")
            operation = _operation_id(operation_id)
            actor = _text(actor_id, "actor_id", 120)
        except ValueError:
            return {"ok": False, "state": "invalid", "code": "invalid_request", "changed": False}
        if not operation or not isinstance(changes, dict) or not changes:
            return {"ok": False, "state": "invalid", "code": "profile_fact_update_invalid", "changed": False}
        if set(changes) - _PROFILE_FACT_FIELDS:
            return {"ok": False, "state": "invalid", "code": "profile_fact_fields_invalid", "changed": False}
        normalized: dict[str, Any] = {}
        for key, value in changes.items():
            if key == "auto_profile_created":
                if type(value) is not bool:
                    return {"ok": False, "state": "invalid", "code": "profile_fact_value_invalid", "changed": False}
                normalized[key] = value
                continue
            if not isinstance(value, str) or _CONTROL_CHARACTER_RE.search(value):
                return {"ok": False, "state": "invalid", "code": "profile_fact_value_invalid", "changed": False}
            limit = 80 if key == "display_name" else 40 if key in {"preferred_address", "style"} else 60
            cleaned = " ".join(value.split())[:limit]
            if key == "display_name" and not cleaned:
                return {"ok": False, "state": "invalid", "code": "profile_fact_value_invalid", "changed": False}
            normalized[key] = cleaned
        if expected_revision is not None and (
            isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1
        ):
            return {"ok": False, "state": "invalid", "code": "profile_fact_revision_invalid", "changed": False}
        operation_key = f"req041.profile_fact:{operation}"
        request_fingerprint = _fingerprint({
            "person_id": clean_person,
            "changes": normalized,
            "actor_id": actor,
            "expected_revision": expected_revision,
        })
        with _LOCK:
            root = _root(self._store)
            prior = root["operations"].get(operation_key)
            if isinstance(prior, dict):
                if prior.get("request_fingerprint") != request_fingerprint:
                    return {
                        "ok": False, "state": "invalid", "code": "operation_id_conflict",
                        "person_id": clean_person, "changed": False,
                    }
                cached = prior.get("result")
                return deepcopy(cached) if isinstance(cached, dict) else {
                    "ok": False, "state": "invalid", "code": "operation_record_corrupt",
                    "person_id": clean_person, "changed": False,
                }
            if operation_key in root["operations"]:
                return {
                    "ok": False, "state": "invalid", "code": "operation_record_corrupt",
                    "person_id": clean_person, "changed": False,
                }
            profile = root["profiles"].get(clean_person)
            projection = build_person_projection(self._store, clean_person)
            if (
                not isinstance(profile, dict)
                or profile.get("profile_status", "active") != "active"
                or projection is None
                or validate_projection(projection)
            ):
                return {
                    "ok": False, "state": "invalid", "code": "person_record_invalid",
                    "person_id": clean_person, "changed": False,
                }
            revision = max(1, int(profile.get("profile_fact_revision") or 1))
            if expected_revision is not None and revision != expected_revision:
                return {
                    "ok": False, "state": "conflict", "code": "profile_fact_revision_conflict",
                    "person_id": clean_person, "profile_fact_revision": revision, "changed": False,
                }
            changed = any(profile.get(key) != value for key, value in normalized.items())
            if changed:
                profile.update(deepcopy(normalized))
                revision += 1
                profile["profile_fact_revision"] = revision
                profile["updated_at"] = _now()
                root["audit_events"].append({
                    "event_id": operation,
                    "action": "update_identity_profile_facts",
                    "actor_id": actor,
                    "person_id": clean_person,
                    "at": profile["updated_at"],
                    "changed_fields": sorted(normalized),
                })
            facts = {
                key: deepcopy(profile.get(key))
                for key in _PROFILE_FACT_FIELDS
                if key in profile
            }
            result = {
                "ok": True,
                "state": "resolved",
                "code": "profile_facts_updated" if changed else "profile_facts_unchanged",
                "person_id": clean_person,
                "identity_key": str(profile.get("resolved_identity_key") or ""),
                "profile_fact_revision": revision,
                "facts": facts,
                "changed": changed,
            }
            root["operations"][operation_key] = {
                "request_fingerprint": request_fingerprint,
                "result": deepcopy(result),
            }
            return result
