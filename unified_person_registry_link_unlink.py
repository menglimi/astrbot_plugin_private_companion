# -*- coding: utf-8 -*-
"""UnifiedPersonRegistryLinkUnlinkMixin。

由 tools/split_mixin_domain.py 从 unified_person_registry.py 机械抽取（2 个方法 + 0 个模块级名字 + 0 个类级赋值 / 266 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UnifiedPersonRegistry）。
"""
from __future__ import annotations

try:  # package import
    from .unified_person_registry_shared import (
        _LOCK,
        _fingerprint,
        _identity,
        _now,
        _operation_id,
        _person_identity_assurance,
        _root,
        _safe_affinity_score,
        _text,
    )
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import (
        _LOCK,
        _fingerprint,
        _identity,
        _now,
        _operation_id,
        _person_identity_assurance,
        _root,
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
    from .unified_person_registry_shared import validate_projection
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import validate_projection



class UnifiedPersonRegistryLinkUnlinkMixin:
    """UnifiedPersonRegistryLinkUnlinkMixin（从 UnifiedPersonRegistry 拆出）。"""


    def link_identity(self, person_id: str, identity: dict[str, Any], operation_id: str = "", actor_id: str = "companion", **_: Any) -> dict[str, Any]:
        try:
            person_id = _text(person_id, "person_id")
            normalized = _identity(identity)
            op = _operation_id(operation_id)
            actor = _text(actor_id, "actor_id", 120)
        except ValueError:
            return {"ok": False, "state": "invalid", "code": "invalid_request", "person_id": ""}
        if not op:
            return {"ok": False, "state": "pending", "code": "explicit_operation_required", "person_id": person_id}
        key = build_identity_key(normalized)
        operation_key = f"req036.link:{op}"
        request_fingerprint = _fingerprint({"person_id": person_id, "identity_key": key, "actor_id": actor})
        with _LOCK:
            root = _root(self._store)
            prior_operation = root["operations"].get(operation_key)
            if isinstance(prior_operation, dict):
                if prior_operation.get("request_fingerprint") != request_fingerprint:
                    return {
                        "ok": False, "state": "invalid", "code": "operation_id_conflict",
                        "operation_id": op, "person_id": person_id, "identity_key": key, "changed": False,
                    }
                cached = prior_operation.get("result")
                return deepcopy(cached) if isinstance(cached, dict) else {
                    "ok": False, "state": "invalid", "code": "operation_record_corrupt",
                    "operation_id": op, "person_id": person_id, "identity_key": key, "changed": False,
                }
            if operation_key in root["operations"]:
                return {
                    "ok": False, "state": "invalid", "code": "operation_record_corrupt",
                    "operation_id": op, "person_id": person_id, "identity_key": key, "changed": False,
                }
            identity_tombstone = root["identity_tombstones"].get(key)
            if isinstance(identity_tombstone, dict):
                return {
                    "ok": False, "state": "deleted", "code": "identity_archived",
                    "person_id": person_id, "identity_key": key, "changed": False,
                }
            profile = root["profiles"].get(person_id)
            if not isinstance(profile, dict):
                return {"ok": False, "state": "pending", "code": "person_not_found", "person_id": person_id}
            if profile.get("profile_status", "active") != "active":
                return {
                    "ok": False, "state": "pending", "code": "person_not_active",
                    "person_id": person_id, "identity_key": key, "changed": False,
                }
            try:
                current_projection = build_person_projection(self._store, person_id)
            except (TypeError, ValueError, OverflowError):
                current_projection = None
            identity_keys = profile.get("identity_keys")
            if (
                current_projection is None
                or validate_projection(current_projection)
                or not isinstance(identity_keys, list)
                or any(not isinstance(item, str) for item in identity_keys)
            ):
                return {
                    "ok": False,
                    "state": "invalid",
                    "code": "person_record_invalid",
                    "person_id": person_id,
                    "identity_key": key,
                    "changed": False,
                }
            prior = root["identity_links"].get(key)
            if isinstance(prior, dict) and prior.get("person_id") != person_id:
                return {"ok": False, "state": "invalid", "code": "identity_conflict", "person_id": person_id}
            if isinstance(prior, dict) and prior.get("person_id") == person_id and prior.get("status") == "active":
                projection = build_person_projection(self._store, person_id)
                result = {
                    "ok": bool(projection and not validate_projection(projection)),
                    "state": "resolved" if projection and not validate_projection(projection) else "invalid",
                    "code": "already_linked",
                    "person_id": person_id,
                    "identity_key": key,
                    "projection": projection,
                    "changed": False,
                }
                root["operations"][operation_key] = {
                    "request_fingerprint": request_fingerprint, "result": deepcopy(result),
                }
                return result
            detached = root["detached_identity_links"].get(key)
            relinking = isinstance(detached, dict)
            if relinking:
                try:
                    detached_identity = _identity(detached.get("identity"))
                    detached_valid = (
                        detached.get("person_id") == person_id
                        and detached.get("identity_key") == key
                        and detached.get("status") == "detached"
                        and build_identity_key(detached_identity) == key
                    )
                except (TypeError, ValueError):
                    detached_valid = False
                if not detached_valid:
                    return {
                        "ok": False, "state": "invalid", "code": "detached_identity_conflict",
                        "person_id": person_id, "identity_key": key, "changed": False,
                    }
            now = _now()
            root["identity_links"][key] = {
                "identity_key": key, "identity": normalized, "person_id": person_id,
                "identity_assurance": "explicit_linked", "status": "active",
                "created_at": str(detached.get("created_at") or now) if relinking else now,
                "updated_at": now, "last_operation_id": op,
            }
            if relinking:
                root["detached_identity_links"].pop(key, None)
            if key not in identity_keys:
                identity_keys.append(key)
            profile["identity_assurance"] = "explicit_linked"
            profile["projection_revision"] = int(profile.get("projection_revision") or 1) + 1
            profile["updated_at"] = now
            root["binding_checkpoints"][f"{person_id}:{key}"] = {
                "person_id": person_id,
                "identity_key": key,
                "origin_identity_key": str(profile.get("resolved_identity_key") or key),
                "relationship_score": _safe_affinity_score(profile.get("affinity_score")),
                "created_at": now,
                "operation_id": op,
                "source_event_count": 0,
            }
            action = "relink_identity" if relinking else "link_identity"
            root["audit_events"].append({"event_id": op, "action": action, "actor_id": actor, "person_id": person_id, "at": now})
            projection = build_person_projection(self._store, person_id)
            result = {
                "ok": bool(projection and not validate_projection(projection)),
                "state": "resolved" if projection and not validate_projection(projection) else "invalid",
                "code": "identity_relinked" if relinking else "identity_linked",
                "person_id": person_id, "identity_key": key, "projection": projection, "changed": True,
            }
            root["operations"][operation_key] = {
                "request_fingerprint": request_fingerprint, "result": deepcopy(result),
            }
            return result

    def unlink_identity(
        self,
        person_id: str,
        identity: dict[str, Any],
        operation_id: str = "",
        actor_id: str = "companion",
        *,
        dry_run: bool = True,
        **_: Any,
    ) -> dict[str, Any]:
        """Detach one explicit identity without inventing a profile split.

        Relationship totals, portrait facts, and suppression markers are never
        copied here.  The checkpoint tells an administrator whether a later
        source-event replay can make the split deterministic.
        """
        try:
            person_id = _text(person_id, "person_id")
            normalized = _identity(identity)
            op = _operation_id(operation_id)
            actor = _text(actor_id, "actor_id", 120)
        except ValueError:
            return {"ok": False, "state": "invalid", "code": "invalid_request", "person_id": ""}
        if not op:
            return {"ok": False, "state": "pending", "code": "explicit_operation_required", "person_id": person_id}
        key = build_identity_key(normalized)
        operation_key = f"req036.unlink:{op}"
        request_fingerprint = _fingerprint({
            "person_id": person_id,
            "identity_key": key,
            "actor_id": actor,
        })
        with _LOCK:
            root = _root(self._store)
            prior_operation = root["operations"].get(operation_key)
            if isinstance(prior_operation, dict):
                if "request_fingerprint" in prior_operation or "result" in prior_operation:
                    if prior_operation.get("request_fingerprint") != request_fingerprint:
                        return {
                            "ok": False,
                            "state": "invalid",
                            "code": "operation_id_conflict",
                            "operation_id": op,
                            "person_id": person_id,
                            "identity_key": key,
                            "changed": False,
                        }
                    cached_result = prior_operation.get("result")
                    if not isinstance(cached_result, dict):
                        return {
                            "ok": False,
                            "state": "invalid",
                            "code": "operation_record_corrupt",
                            "operation_id": op,
                            "person_id": person_id,
                            "identity_key": key,
                            "changed": False,
                        }
                    return deepcopy(cached_result)
                # Compatibility with records written before request-bound
                # operation envelopes were introduced.
                if prior_operation.get("person_id") != person_id or prior_operation.get("identity_key") != key:
                    return {
                        "ok": False,
                        "state": "invalid",
                        "code": "operation_id_conflict",
                        "operation_id": op,
                        "person_id": person_id,
                        "identity_key": key,
                        "changed": False,
                    }
                return deepcopy(prior_operation)
            if operation_key in root["operations"]:
                return {
                    "ok": False,
                    "state": "invalid",
                    "code": "operation_record_corrupt",
                    "operation_id": op,
                    "person_id": person_id,
                    "identity_key": key,
                    "changed": False,
                }
            profile = root["profiles"].get(person_id)
            link = root["identity_links"].get(key)
            if not isinstance(profile, dict) or not isinstance(link, dict) or link.get("person_id") != person_id:
                return {"ok": False, "state": "pending", "code": "identity_not_linked", "person_id": person_id, "identity_key": key}
            identity_keys = [item for item in profile.get("identity_keys", []) if isinstance(item, str)]
            checkpoint = root["binding_checkpoints"].get(f"{person_id}:{key}")
            checkpoint = deepcopy(checkpoint) if isinstance(checkpoint, dict) else {}
            replay_count = int(checkpoint.get("source_event_count") or 0)
            ambiguity_count = 0
            if key == profile.get("resolved_identity_key") or len(identity_keys) <= 1:
                ambiguity_count = 1
            result = {
                "ok": ambiguity_count == 0,
                "state": "resolved" if ambiguity_count == 0 else "pending",
                "code": "migration_dry_run" if dry_run and ambiguity_count == 0 else (
                    "split_manual_review_required" if ambiguity_count else "identity_unlinked"
                ),
                "person_id": person_id,
                "identity_key": key,
                "source_event_count": replay_count,
                "replayable_event_count": replay_count,
                "ambiguity_count": ambiguity_count,
                "checkpoint": checkpoint,
                "changed": False,
            }
            if dry_run or ambiguity_count:
                return result
            now = _now()
            root["detached_identity_links"][key] = {
                **deepcopy(link),
                "status": "detached",
                "detached_at": now,
                "detached_by": actor,
                "detach_operation_id": op,
            }
            root["identity_links"].pop(key, None)
            profile["identity_keys"] = [item for item in identity_keys if item != key]
            profile["identity_assurance"] = _person_identity_assurance(root, person_id)
            profile["projection_revision"] = int(profile.get("projection_revision") or 1) + 1
            profile["updated_at"] = now
            root["audit_events"].append({"event_id": op, "action": "unlink_identity", "actor_id": actor, "person_id": person_id, "at": now})
            result.update({"ok": True, "state": "resolved", "code": "identity_unlinked", "changed": True})
            root["operations"][operation_key] = {
                "request_fingerprint": request_fingerprint,
                "result": deepcopy(result),
            }
            return result
