# -*- coding: utf-8 -*-
"""UnifiedPersonRegistryArchiveMixin。

由 tools/split_mixin_domain.py 从 unified_person_registry.py 机械抽取（6 个方法 + 0 个模块级名字 + 0 个类级赋值 / 438 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UnifiedPersonRegistry）。
"""
from __future__ import annotations

try:  # package import
    from .unified_person_registry_shared import _LOCK, _fingerprint, _identity, _now, _operation_id, _root, _text
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import _LOCK, _fingerprint, _identity, _now, _operation_id, _root, _text
try:  # package import
    from .unified_person_registry_shared import Any
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import Any
try:  # package import
    from .unified_person_registry_shared import build_identity_key
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import build_identity_key
try:  # package import
    from .unified_person_registry_shared import deepcopy
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import deepcopy
try:  # package import
    from .unified_person_registry_shared import re
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import re



class UnifiedPersonRegistryArchiveMixin:
    """UnifiedPersonRegistryArchiveMixin（从 UnifiedPersonRegistry 拆出）。"""


    def record_identity_source_event(
        self,
        person_id: str,
        identity_key: str,
        source_scope: str,
        event_fingerprint: str,
        *,
        operation_id: str = "",
    ) -> dict[str, Any]:
        """Record a hash-only source event for a later deterministic split.

        The registry never receives message text.  A bounded fingerprint list
        gives unlink dry-runs a replay count without turning identity storage
        into a second chat archive.
        """
        try:
            person_id = _text(person_id, "person_id")
            identity_key = _text(identity_key, "identity_key", 160)
            source_scope = _text(source_scope or "private", "source_scope", 120)
            event_fingerprint = _text(event_fingerprint, "event_fingerprint", 80)
        except ValueError:
            return {"ok": False, "code": "invalid_request"}
        if re.fullmatch(r"[0-9a-f]{64}", event_fingerprint) is None:
            return {"ok": False, "code": "invalid_request"}
        with _LOCK:
            root = _root(self._store)
            link = root["identity_links"].get(identity_key)
            checkpoint_key = f"{person_id}:{identity_key}"
            checkpoint = root["binding_checkpoints"].get(checkpoint_key)
            if not isinstance(link, dict) or link.get("person_id") != person_id or not isinstance(checkpoint, dict):
                return {"ok": False, "code": "identity_not_linked"}
            seen = checkpoint.get("source_event_fingerprints")
            if not isinstance(seen, list):
                seen = []
                checkpoint["source_event_fingerprints"] = seen
            if event_fingerprint in seen:
                return {
                    "ok": True,
                    "code": "source_event_idempotent_replay",
                    "source_event_count": int(checkpoint.get("source_event_count") or len(seen)),
                }
            seen.append(event_fingerprint)
            del seen[:-512]
            checkpoint["source_event_count"] = len(seen)
            checkpoint["last_source_scope"] = source_scope
            checkpoint["last_source_event_at"] = _now()
            if operation_id:
                checkpoint["last_source_operation_id"] = _text(operation_id, "operation_id", 120)
            return {"ok": True, "code": "source_event_recorded", "source_event_count": len(seen)}

    def preview_person_merge(self, source_person_id: str, target_person_id: str, operation_id: str = "", **_: Any) -> dict[str, Any]:
        """Expose conflicts without merging two existing people automatically."""
        try:
            source_person_id = _text(source_person_id, "source_person_id")
            target_person_id = _text(target_person_id, "target_person_id")
            operation_id = _operation_id(operation_id)
        except ValueError:
            return {"ok": False, "code": "invalid_request"}
        with _LOCK:
            root = _root(self._store)
            source = root["profiles"].get(source_person_id)
            target = root["profiles"].get(target_person_id)
            if not isinstance(source, dict) or not isinstance(target, dict) or source_person_id == target_person_id:
                return {"ok": False, "code": "invalid_request"}
            conflicts = [
                field for field in ("affinity_score", "owner_mode", "relation_policy_id")
                if source.get(field) != target.get(field)
            ]
            return {
                "ok": False,
                "code": "merge_manual_review_required",
                "operation_id": operation_id,
                "source_person_id": source_person_id,
                "target_person_id": target_person_id,
                "conflicts": conflicts,
                "write_count": 0,
            }

    def prepare_person_archive(
        self,
        person_id: str,
        operation_id: str = "",
        actor_id: str = "companion",
        reason_code: str = "person_archive",
    ) -> dict[str, Any]:
        """Persist a request-bound archive saga without changing identity state."""
        try:
            clean_person = _text(person_id, "person_id")
            operation = _operation_id(operation_id)
            actor = _text(actor_id, "actor_id", 120)
            reason = _text(reason_code, "reason_code", 80)
        except ValueError:
            return {"ok": False, "state": "invalid", "code": "invalid_request", "person_id": ""}
        if not operation:
            return {
                "ok": False, "state": "pending", "code": "explicit_operation_required",
                "person_id": clean_person,
            }
        operation_key = f"req041.archive:{operation}"
        request_fingerprint = _fingerprint({
            "person_id": clean_person, "actor_id": actor, "reason_code": reason,
        })
        with _LOCK:
            root = _root(self._store)
            prior = root["operations"].get(operation_key)
            if isinstance(prior, dict):
                if prior.get("request_fingerprint") != request_fingerprint:
                    return {
                        "ok": False, "state": "invalid", "code": "operation_id_conflict",
                        "person_id": clean_person, "operation_id": operation,
                    }
                result = prior.get("result")
                if prior.get("stage") == "completed" and isinstance(result, dict):
                    return deepcopy(result)
                preview = prior.get("preview")
                return deepcopy(preview) if isinstance(preview, dict) else {
                    "ok": False, "state": "invalid", "code": "operation_record_corrupt",
                    "person_id": clean_person, "operation_id": operation,
                }
            if operation_key in root["operations"]:
                return {
                    "ok": False, "state": "invalid", "code": "operation_record_corrupt",
                    "person_id": clean_person, "operation_id": operation,
                }
            profile = root["profiles"].get(clean_person)
            if not isinstance(profile, dict):
                return {"ok": False, "state": "pending", "code": "person_not_found", "person_id": clean_person}
            if profile.get("profile_status", "active") != "active":
                return {"ok": False, "state": "pending", "code": "person_not_active", "person_id": clean_person}
            identity_keys = profile.get("identity_keys")
            if not isinstance(identity_keys, list) or any(not isinstance(item, str) for item in identity_keys):
                return {"ok": False, "state": "invalid", "code": "person_record_invalid", "person_id": clean_person}
            active_keys = sorted(
                str(key) for key, link in root["identity_links"].items()
                if isinstance(link, dict) and link.get("person_id") == clean_person and link.get("status") == "active"
            )
            if sorted(identity_keys) != active_keys or not active_keys:
                return {"ok": False, "state": "invalid", "code": "identity_exact_link_invalid", "person_id": clean_person}
            for key in active_keys:
                link = root["identity_links"].get(key)
                try:
                    valid = (
                        isinstance(link, dict)
                        and link.get("identity_key") == key
                        and build_identity_key(_identity(link.get("identity"))) == key
                    )
                except (TypeError, ValueError):
                    valid = False
                if not valid:
                    return {"ok": False, "state": "invalid", "code": "identity_exact_link_invalid", "person_id": clean_person}
            projection_revision = max(1, int(profile.get("projection_revision") or 1))
            token = _fingerprint({
                "request_fingerprint": request_fingerprint,
                "projection_revision": projection_revision,
                "active_identity_keys_hash": _fingerprint(active_keys),
            })
            preview = {
                "ok": True, "state": "prepared", "code": "person_archive_prepared",
                "person_id": clean_person, "operation_id": operation,
                "confirmation_token": token,
                "active_identity_count": len(active_keys),
                "detached_identity_count": sum(
                    1 for link in root["detached_identity_links"].values()
                    if isinstance(link, dict) and link.get("person_id") == clean_person
                ),
                "group_overlay_count": sum(
                    1 for overlay in root["group_overlays"].values()
                    if isinstance(overlay, dict) and overlay.get("person_id") == clean_person
                ),
                "projection_revision": projection_revision,
                "changed": False,
            }
            root["operations"][operation_key] = {
                "request_fingerprint": request_fingerprint,
                "stage": "prepared",
                "person_id": clean_person,
                "actor_id": actor,
                "reason_code": reason,
                "confirmation_token": token,
                "prepared_at": _now(),
                "preview": deepcopy(preview),
            }
            return preview

    def finalize_person_archive(
        self,
        person_id: str,
        operation_id: str,
        confirmation_token: str,
        remote_receipt: dict[str, Any],
        relationship_receipt: dict[str, Any],
        stream_receipt: dict[str, Any],
        *,
        actor_id: str = "companion",
        reason_code: str = "person_archive",
    ) -> dict[str, Any]:
        """Finalize identity state only after an atomic Memory receipt exists."""
        try:
            clean_person = _text(person_id, "person_id")
            operation = _operation_id(operation_id)
            token = _text(confirmation_token, "confirmation_token", 80)
            actor = _text(actor_id, "actor_id", 120)
            reason = _text(reason_code, "reason_code", 80)
        except ValueError:
            return {"ok": False, "state": "invalid", "code": "invalid_request", "person_id": ""}
        operation_key = f"req041.archive:{operation}"
        request_fingerprint = _fingerprint({
            "person_id": clean_person, "actor_id": actor, "reason_code": reason,
        })
        if not isinstance(remote_receipt, dict) or not remote_receipt.get("ok"):
            return {
                "ok": False, "state": "prepared", "code": "archive_remote_not_confirmed",
                "person_id": clean_person, "operation_id": operation, "changed": False,
            }
        remote_code = str(remote_receipt.get("code") or "")
        if remote_code not in {"identity_scopes_tombstoned", "identity_scopes_already_empty"}:
            return {
                "ok": False, "state": "prepared", "code": "archive_remote_receipt_invalid",
                "person_id": clean_person, "operation_id": operation, "changed": False,
            }
        try:
            remote_count = int(remote_receipt.get("count") or 0)
            namespace_count = int(remote_receipt.get("namespace_count") or 0)
        except (TypeError, ValueError, OverflowError):
            return {
                "ok": False, "state": "prepared", "code": "archive_remote_receipt_invalid",
                "person_id": clean_person, "operation_id": operation, "changed": False,
            }
        if remote_count < 0 or namespace_count < 0:
            return {
                "ok": False, "state": "prepared", "code": "archive_remote_receipt_invalid",
                "person_id": clean_person, "operation_id": operation, "changed": False,
            }
        if not isinstance(relationship_receipt, dict):
            return {
                "ok": False, "state": "prepared", "code": "archive_relationship_receipt_invalid",
                "person_id": clean_person, "operation_id": operation, "changed": False,
            }
        relationship_code = str(relationship_receipt.get("code") or "")
        if relationship_code not in {
            "relationship_account_tombstoned", "relationship_account_already_empty",
        }:
            return {
                "ok": False, "state": "prepared", "code": "archive_relationship_receipt_invalid",
                "person_id": clean_person, "operation_id": operation, "changed": False,
            }
        if (
            not isinstance(stream_receipt, dict)
            or stream_receipt.get("code") != "outbox_streams_retired"
            or int(stream_receipt.get("stream_count") or 0) != 2
        ):
            return {
                "ok": False, "state": "prepared", "code": "archive_stream_receipt_invalid",
                "person_id": clean_person, "operation_id": operation, "changed": False,
            }
        with _LOCK:
            root = _root(self._store)
            saga = root["operations"].get(operation_key)
            if not isinstance(saga, dict) or saga.get("request_fingerprint") != request_fingerprint:
                return {
                    "ok": False, "state": "invalid", "code": "archive_operation_missing",
                    "person_id": clean_person, "operation_id": operation, "changed": False,
                }
            if saga.get("confirmation_token") != token:
                return {
                    "ok": False, "state": "invalid", "code": "archive_confirmation_mismatch",
                    "person_id": clean_person, "operation_id": operation, "changed": False,
                }
            if saga.get("stage") == "completed" and isinstance(saga.get("result"), dict):
                return deepcopy(saga["result"])
            if saga.get("stage") != "confirmed":
                return {
                    "ok": False, "state": "invalid", "code": "archive_operation_state_invalid",
                    "person_id": clean_person, "operation_id": operation, "changed": False,
                }
            profile = root["profiles"].get(clean_person)
            if not isinstance(profile, dict) or profile.get("profile_status", "active") != "active":
                return {
                    "ok": False, "state": "invalid", "code": "person_not_active",
                    "person_id": clean_person, "operation_id": operation, "changed": False,
                }
            now = _now()
            active_keys = [
                str(key) for key, link in root["identity_links"].items()
                if isinstance(link, dict) and link.get("person_id") == clean_person and link.get("status") == "active"
            ]
            for key in active_keys:
                link = root["identity_links"].get(key)
                try:
                    valid = (
                        isinstance(link, dict)
                        and link.get("identity_key") == key
                        and build_identity_key(_identity(link.get("identity"))) == key
                    )
                except (TypeError, ValueError):
                    valid = False
                if not valid:
                    return {
                        "ok": False, "state": "invalid", "code": "identity_exact_link_invalid",
                        "person_id": clean_person, "operation_id": operation, "changed": False,
                    }
            detached_keys = [
                str(key) for key, link in root["detached_identity_links"].items()
                if isinstance(link, dict) and link.get("person_id") == clean_person
            ]
            all_identity_keys = sorted(set(active_keys + detached_keys))
            for key in all_identity_keys:
                prior_tombstone = root["identity_tombstones"].get(key)
                if isinstance(prior_tombstone, dict) and prior_tombstone.get("person_id") != clean_person:
                    return {
                        "ok": False, "state": "invalid", "code": "identity_tombstone_conflict",
                        "person_id": clean_person, "operation_id": operation, "changed": False,
                    }
            for key in active_keys:
                link = deepcopy(root["identity_links"].pop(key))
                root["detached_identity_links"][key] = {
                    **link, "status": "detached", "detached_at": now,
                    "detached_by": actor, "archive_operation_id": operation,
                }
            for key in all_identity_keys:
                root["identity_tombstones"][key] = {
                    "person_id": clean_person, "reason_code": reason,
                    "archive_operation_id": operation, "created_at": now,
                }
            root["person_tombstones"][clean_person] = {
                "reason_code": reason, "archive_operation_id": operation, "created_at": now,
                "identity_key_count": len(all_identity_keys),
            }
            overlays = [
                key for key, overlay in root["group_overlays"].items()
                if isinstance(overlay, dict) and overlay.get("person_id") == clean_person
            ]
            for key in overlays:
                root["group_overlays"].pop(key, None)
            profile["identity_keys"] = []
            profile["identity_assurance"] = "unverified"
            profile["profile_status"] = "deleted"
            profile["projection_revision"] = int(profile.get("projection_revision") or 1) + 1
            profile["updated_at"] = now
            root["audit_events"].append({
                "event_id": operation, "action": "archive_person", "actor_id": actor,
                "person_id": clean_person, "reason_code": reason, "at": now,
            })
            result = {
                "ok": True, "state": "completed", "code": "person_archived",
                "person_id": clean_person, "operation_id": operation,
                "detached_identity_count": len(active_keys),
                "removed_group_overlay_count": len(overlays),
                "scoped_record_count": remote_count,
                "scoped_namespace_count": namespace_count,
                "identity_tombstone_count": len(all_identity_keys),
                "changed": True,
            }
            saga.update({
                "stage": "completed", "completed_at": now,
                "remote_receipt_hash": _fingerprint({
                    "code": remote_code, "count": remote_count, "namespace_count": namespace_count,
                    "relationship_code": relationship_code,
                    "relationship_last_revision": int(relationship_receipt.get("last_revision") or 0),
                    "stream_code": "outbox_streams_retired",
                    "stream_revisions": stream_receipt.get("revisions") or {},
                }),
                "result": deepcopy(result),
            })
            return result

    def confirm_person_archive(
        self,
        person_id: str,
        operation_id: str,
        confirmation_token: str,
        *,
        actor_id: str = "companion",
        reason_code: str = "person_archive",
    ) -> dict[str, Any]:
        """Persist explicit destructive intent so confirmed work can resume."""
        try:
            clean_person = _text(person_id, "person_id")
            operation = _operation_id(operation_id)
            token = _text(confirmation_token, "confirmation_token", 80)
            actor = _text(actor_id, "actor_id", 120)
            reason = _text(reason_code, "reason_code", 80)
        except ValueError:
            return {"ok": False, "state": "invalid", "code": "invalid_request", "person_id": ""}
        request_fingerprint = _fingerprint({
            "person_id": clean_person, "actor_id": actor, "reason_code": reason,
        })
        operation_key = f"req041.archive:{operation}"
        with _LOCK:
            root = _root(self._store)
            saga = root["operations"].get(operation_key)
            if not isinstance(saga, dict) or saga.get("request_fingerprint") != request_fingerprint:
                return {
                    "ok": False, "state": "invalid", "code": "archive_operation_missing",
                    "person_id": clean_person, "operation_id": operation, "changed": False,
                }
            if saga.get("confirmation_token") != token:
                return {
                    "ok": False, "state": str(saga.get("stage") or "prepared"),
                    "code": "archive_confirmation_mismatch", "person_id": clean_person,
                    "operation_id": operation, "changed": False,
                }
            if saga.get("stage") == "completed" and isinstance(saga.get("result"), dict):
                return deepcopy(saga["result"])
            if saga.get("stage") not in {"prepared", "confirmed"}:
                return {
                    "ok": False, "state": "invalid", "code": "archive_operation_state_invalid",
                    "person_id": clean_person, "operation_id": operation, "changed": False,
                }
            changed = saga.get("stage") != "confirmed"
            saga["stage"] = "confirmed"
            if changed:
                saga["confirmed_at"] = _now()
            return {
                "ok": True, "state": "confirmed", "code": "person_archive_confirmed",
                "person_id": clean_person, "operation_id": operation,
                "confirmation_token": token, "changed": changed,
            }

    def confirmed_person_archives(self, *, limit: int = 32) -> list[dict[str, str]]:
        """Return only resumable confirmed sagas, never preview-only requests."""
        safe_limit = max(1, min(128, int(limit)))
        with _LOCK:
            root = _root(self._store)
            result: list[dict[str, str]] = []
            for key, saga in sorted(root["operations"].items()):
                if not key.startswith("req041.archive:") or not isinstance(saga, dict):
                    continue
                if saga.get("stage") != "confirmed":
                    continue
                operation = key.split(":", 2)[-1]
                values = {
                    "person_id": str(saga.get("person_id") or ""),
                    "operation_id": operation,
                    "confirmation_token": str(saga.get("confirmation_token") or ""),
                    "actor_id": str(saga.get("actor_id") or ""),
                    "reason_code": str(saga.get("reason_code") or ""),
                }
                if all(values.values()):
                    result.append(values)
                if len(result) >= safe_limit:
                    break
            return result
