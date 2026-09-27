# -*- coding: utf-8 -*-
"""UnifiedPersonRegistryPurgeMixin。

由 tools/split_mixin_domain.py 从 unified_person_registry.py 机械抽取（5 个方法 + 0 个模块级名字 + 0 个类级赋值 / 267 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UnifiedPersonRegistry）。
"""
from __future__ import annotations

try:  # package import
    from .unified_person_registry_shared import (
        PERSON_PURGE_RETENTION_SECONDS,
        _LOCK,
        _contains_exact_value,
        _fingerprint,
        _identity,
        _now,
        _operation_id,
        _root,
        _text,
        _timestamp,
    )
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import (
        PERSON_PURGE_RETENTION_SECONDS,
        _LOCK,
        _contains_exact_value,
        _fingerprint,
        _identity,
        _now,
        _operation_id,
        _root,
        _text,
        _timestamp,
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
    from .unified_person_registry_shared import datetime
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import datetime
try:  # package import
    from .unified_person_registry_shared import deepcopy
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import deepcopy
try:  # package import
    from .unified_person_registry_shared import timezone
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import timezone



class UnifiedPersonRegistryPurgeMixin:
    """UnifiedPersonRegistryPurgeMixin（从 UnifiedPersonRegistry 拆出）。"""


    def prepare_person_purge(
        self,
        person_id: str,
        operation_id: str = "",
        *,
        actor_id: str = "companion",
        reason_code: str = "person_delete",
        retention_seconds: int = PERSON_PURGE_RETENTION_SECONDS,
        now_ts: float | None = None,
    ) -> dict[str, Any]:
        """Prepare legacy physical removal after the fixed archive retention."""
        try:
            clean_person = _text(person_id, "person_id")
            operation = _operation_id(operation_id)
            actor = _text(actor_id, "actor_id", 120)
            reason = _text(reason_code, "reason_code", 80)
            retention = int(retention_seconds)
            current = float(now_ts) if now_ts is not None else datetime.now(timezone.utc).timestamp()
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "state": "invalid", "code": "invalid_request", "person_id": ""}
        if not operation or retention < 0 or retention > 365 * 24 * 60 * 60:
            return {"ok": False, "state": "invalid", "code": "invalid_request", "person_id": clean_person}
        operation_key = f"req041.purge:{operation}"
        request_fingerprint = _fingerprint({
            "person_id": clean_person, "actor_id": actor, "reason_code": reason,
            "retention_seconds": retention,
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
                if prior.get("stage") == "completed" and isinstance(prior.get("result"), dict):
                    return deepcopy(prior["result"])
                preview = prior.get("preview")
                return deepcopy(preview) if isinstance(preview, dict) else {
                    "ok": False, "state": "invalid", "code": "operation_record_corrupt",
                    "person_id": clean_person, "operation_id": operation,
                }
            profile = root["profiles"].get(clean_person)
            tombstone = root["person_tombstones"].get(clean_person)
            if (
                not isinstance(profile, dict) or profile.get("profile_status") != "deleted"
                or not isinstance(tombstone, dict)
            ):
                return {"ok": False, "state": "pending", "code": "person_not_archived", "person_id": clean_person}
            archived_at = _timestamp(tombstone.get("created_at"))
            if archived_at < 0:
                return {"ok": False, "state": "invalid", "code": "archive_tombstone_corrupt", "person_id": clean_person}
            eligible_at = archived_at + retention
            if current < eligible_at:
                return {
                    "ok": False, "state": "retention", "code": "archive_retention_active",
                    "person_id": clean_person, "eligible_at": datetime.fromtimestamp(
                        eligible_at, timezone.utc
                    ).isoformat(timespec="seconds"), "changed": False,
                }
            detached_keys = [
                str(key) for key, link in root["detached_identity_links"].items()
                if isinstance(link, dict) and link.get("person_id") == clean_person
            ]
            if any(
                isinstance(link, dict) and link.get("person_id") == clean_person
                for link in root["identity_links"].values()
            ):
                return {"ok": False, "state": "invalid", "code": "archived_person_has_active_link", "person_id": clean_person}
            token = _fingerprint({
                "request_fingerprint": request_fingerprint,
                "archive_operation_id": tombstone.get("archive_operation_id"),
                "identity_tombstone_count": len(detached_keys),
            })
            preview = {
                "ok": True, "state": "prepared", "code": "person_purge_prepared",
                "person_id": clean_person, "operation_id": operation,
                "confirmation_token": token,
                "detached_identity_count": len(detached_keys),
                "binding_checkpoint_count": sum(
                    1 for checkpoint in root["binding_checkpoints"].values()
                    if isinstance(checkpoint, dict) and checkpoint.get("person_id") == clean_person
                ),
                "changed": False,
            }
            root["operations"][operation_key] = {
                "request_fingerprint": request_fingerprint, "stage": "prepared",
                "person_id": clean_person, "actor_id": actor, "reason_code": reason,
                "retention_seconds": retention, "confirmation_token": token,
                "prepared_at": _now(), "preview": deepcopy(preview),
            }
            return preview

    def confirm_person_purge(
        self,
        person_id: str,
        operation_id: str,
        confirmation_token: str,
        *,
        actor_id: str = "companion",
        reason_code: str = "person_delete",
        retention_seconds: int = PERSON_PURGE_RETENTION_SECONDS,
    ) -> dict[str, Any]:
        try:
            clean_person = _text(person_id, "person_id")
            operation = _operation_id(operation_id)
            token = _text(confirmation_token, "confirmation_token", 80)
            actor = _text(actor_id, "actor_id", 120)
            reason = _text(reason_code, "reason_code", 80)
            retention = int(retention_seconds)
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "state": "invalid", "code": "invalid_request", "person_id": ""}
        request_fingerprint = _fingerprint({
            "person_id": clean_person, "actor_id": actor, "reason_code": reason,
            "retention_seconds": retention,
        })
        operation_key = f"req041.purge:{operation}"
        with _LOCK:
            root = _root(self._store)
            saga = root["operations"].get(operation_key)
            if not isinstance(saga, dict) or saga.get("request_fingerprint") != request_fingerprint:
                return {"ok": False, "state": "invalid", "code": "purge_operation_missing", "person_id": clean_person}
            if saga.get("confirmation_token") != token:
                return {"ok": False, "state": str(saga.get("stage") or "prepared"), "code": "purge_confirmation_mismatch", "person_id": clean_person}
            if saga.get("stage") == "completed" and isinstance(saga.get("result"), dict):
                return deepcopy(saga["result"])
            if saga.get("stage") not in {"prepared", "confirmed"}:
                return {"ok": False, "state": "invalid", "code": "purge_operation_state_invalid", "person_id": clean_person}
            changed = saga.get("stage") != "confirmed"
            saga["stage"] = "confirmed"
            if changed:
                saga["confirmed_at"] = _now()
            return {
                "ok": True, "state": "confirmed", "code": "person_purge_confirmed",
                "person_id": clean_person, "operation_id": operation,
                "confirmation_token": token, "changed": changed,
            }

    def confirmed_person_purges(self, *, limit: int = 32) -> list[dict[str, str]]:
        safe_limit = max(1, min(128, int(limit)))
        with _LOCK:
            root = _root(self._store)
            result: list[dict[str, str]] = []
            for key, saga in sorted(root["operations"].items()):
                if not key.startswith("req041.purge:") or not isinstance(saga, dict) or saga.get("stage") != "confirmed":
                    continue
                values = {
                    "person_id": str(saga.get("person_id") or ""),
                    "operation_id": key.split(":", 2)[-1],
                    "confirmation_token": str(saga.get("confirmation_token") or ""),
                    "actor_id": str(saga.get("actor_id") or ""),
                    "reason_code": str(saga.get("reason_code") or ""),
                }
                if all(values.values()):
                    result.append(values)
                if len(result) >= safe_limit:
                    break
            return result

    def archived_identity_subjects(self, person_id: str) -> list[str]:
        """Internal purge helper; raw subjects must never be returned by page APIs."""
        try:
            clean_person = _text(person_id, "person_id")
        except ValueError:
            return []
        with _LOCK:
            root = _root(self._store)
            profile = root["profiles"].get(clean_person)
            if not isinstance(profile, dict) or profile.get("profile_status") != "deleted":
                return []
            subjects: list[str] = []
            for key, link in root["detached_identity_links"].items():
                if not isinstance(link, dict) or link.get("person_id") != clean_person:
                    continue
                try:
                    identity = _identity(link.get("identity"))
                    if build_identity_key(identity) != key or key not in root["identity_tombstones"]:
                        return []
                except (TypeError, ValueError):
                    return []
                subject = identity["platform_subject_id"]
                if subject not in subjects:
                    subjects.append(subject)
            return subjects

    def finalize_person_purge(
        self,
        person_id: str,
        operation_id: str,
        confirmation_token: str,
        outbox_receipt: dict[str, Any],
        *,
        actor_id: str = "companion",
        reason_code: str = "person_delete",
        retention_seconds: int = PERSON_PURGE_RETENTION_SECONDS,
    ) -> dict[str, Any]:
        try:
            clean_person = _text(person_id, "person_id")
            operation = _operation_id(operation_id)
            token = _text(confirmation_token, "confirmation_token", 80)
            actor = _text(actor_id, "actor_id", 120)
            reason = _text(reason_code, "reason_code", 80)
            retention = int(retention_seconds)
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "state": "invalid", "code": "invalid_request", "person_id": ""}
        if not isinstance(outbox_receipt, dict) or outbox_receipt.get("code") != "outbox_retired_streams_purged":
            return {"ok": False, "state": "confirmed", "code": "purge_outbox_receipt_invalid", "person_id": clean_person}
        request_fingerprint = _fingerprint({
            "person_id": clean_person, "actor_id": actor, "reason_code": reason,
            "retention_seconds": retention,
        })
        operation_key = f"req041.purge:{operation}"
        with _LOCK:
            root = _root(self._store)
            saga = root["operations"].get(operation_key)
            if not isinstance(saga, dict) or saga.get("request_fingerprint") != request_fingerprint:
                return {"ok": False, "state": "invalid", "code": "purge_operation_missing", "person_id": clean_person}
            if saga.get("confirmation_token") != token:
                return {"ok": False, "state": "invalid", "code": "purge_confirmation_mismatch", "person_id": clean_person}
            if saga.get("stage") == "completed" and isinstance(saga.get("result"), dict):
                return deepcopy(saga["result"])
            if saga.get("stage") != "confirmed":
                return {"ok": False, "state": "invalid", "code": "purge_operation_state_invalid", "person_id": clean_person}
            profile = root["profiles"].get(clean_person)
            if not isinstance(profile, dict) or profile.get("profile_status") != "deleted":
                return {"ok": False, "state": "invalid", "code": "person_not_archived", "person_id": clean_person}
            tombstone = root["person_tombstones"].get(clean_person)
            if not isinstance(tombstone, dict):
                return {"ok": False, "state": "invalid", "code": "person_tombstone_missing", "person_id": clean_person}
            detached_keys = [
                str(key) for key, link in root["detached_identity_links"].items()
                if isinstance(link, dict) and link.get("person_id") == clean_person
            ]
            checkpoint_keys = [
                key for key, checkpoint in root["binding_checkpoints"].items()
                if isinstance(checkpoint, dict) and checkpoint.get("person_id") == clean_person
            ]
            root["profiles"].pop(clean_person, None)
            for key in detached_keys:
                root["detached_identity_links"].pop(key, None)
            for key in checkpoint_keys:
                root["binding_checkpoints"].pop(key, None)
            for container_name in ("p4_effect", "p4_live"):
                container = root.get(container_name)
                if isinstance(container, dict) and isinstance(container.get("people"), dict):
                    container["people"].pop(clean_person, None)
            root["audit_events"] = [
                event for event in root["audit_events"]
                if not (isinstance(event, dict) and event.get("person_id") == clean_person)
            ]
            preserved_saga = deepcopy(saga)
            for key, value in list(root["operations"].items()):
                if key != operation_key and _contains_exact_value(value, clean_person):
                    root["operations"].pop(key, None)
            now = _now()
            tombstone.update({"purged_at": now, "purge_operation_id": operation})
            result = {
                "ok": True, "state": "completed", "code": "person_purged",
                "person_id": clean_person, "operation_id": operation,
                "removed_detached_identity_count": len(detached_keys),
                "removed_binding_checkpoint_count": len(checkpoint_keys),
                "changed": True,
            }
            preserved_saga.update({"stage": "completed", "completed_at": now, "result": deepcopy(result)})
            root["operations"][operation_key] = preserved_saga
            root["audit_events"].append({
                "event_id": operation, "action": "purge_person", "actor_id": actor,
                "person_id": clean_person, "reason_code": reason, "at": now,
            })
            return result
