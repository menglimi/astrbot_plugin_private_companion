from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from copy import deepcopy
from typing import Any, Mapping


QZONE_PLUGIN_ID = "astrbot_plugin_private_companion"
QZONE_TARGET_PLUGIN_ID = "astrbot_plugin_content_companion"
QZONE_API_FAMILY = "companion.qzone"
QZONE_API_VERSION = "companion.qzone-api.v1"
QZONE_OPERATION_VERSION = "companion.qzone-operation.v1"
QZONE_RESULT_VERSION = "companion.qzone-result.v1"
QZONE_CONFIG_SNAPSHOT_VERSION = "companion.qzone-config-snapshot.v1"
QZONE_MAX_PAYLOAD_BYTES = 8 * 1024
QZONE_MAX_SMALL_RESULT_BYTES = 16 * 1024
QZONE_MAX_FEED_RESULT_BYTES = 256 * 1024

QZONE_CAPABILITIES = (
    "qzone.auth.refresh",
    "qzone.config.snapshot",
    "qzone.contract.current-owner",
    "qzone.feed.detail",
    "qzone.feed.list",
    "qzone.post.comment",
    "qzone.post.delete",
    "qzone.post.like",
    "qzone.post.publish",
    "qzone.status.read",
)

QZONE_DESCRIPTOR_FIELDS = frozenset(
    {
        "plugin_id",
        "instance_generation",
        "api_family",
        "api_version",
        "supported_task_versions",
        "capabilities",
        "lifecycle_state",
        "degraded_reasons",
    }
)
QZONE_RESULT_FIELDS = frozenset(
    {
        "version",
        "instance_generation",
        "operation",
        "ok",
        "code",
        "message",
        "data",
    }
)
QZONE_CONFIG_SNAPSHOT_FIELDS = frozenset(
    {
        "version",
        "source_plugin_id",
        "instance_generation",
        "target_plugin_id",
        "settings",
        "credential",
        "snapshot_id",
        "snapshot_sha256",
    }
)

_PAYLOAD_FIELDS = {
    "status": frozenset(),
    "feed": frozenset({"scope", "target_uin", "page"}),
    "detail": frozenset({"post_ref"}),
    "refresh": frozenset(),
    "publish": frozenset({"content", "auto_generate_image"}),
    "like": frozenset({"post_ref"}),
    "comment": frozenset({"post_ref", "content"}),
    "delete": frozenset({"post_ref"}),
}
_POST_REF_RE = re.compile(r"^qzref_[A-Za-z0-9_-]{32}$")
_GENERATION_RE = re.compile(r"^[0-9a-f]{32}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class QzoneContractError(ValueError):
    """Stable, body-free refusal at the formal QZone boundary."""

    def __init__(self, code: str) -> None:
        self.code = str(code or "qzone_contract_invalid")[:64]
        super().__init__(self.code)


def _reject(code: str) -> None:
    raise QzoneContractError(code)


def _exact_dict(
    value: Any,
    fields: frozenset[str],
    *,
    code: str = "qzone_payload_invalid",
) -> Mapping[str, Any]:
    if (
        type(value) is not dict
        or any(type(key) is not str for key in value)
        or set(value) != fields
    ):
        _reject(code)
    return value


def _text(value: Any, *, maximum: int, required: bool = False) -> str:
    if type(value) is not str or len(value) > maximum:
        _reject("qzone_payload_invalid")
    if required and not value.strip():
        _reject("qzone_payload_invalid")
    if any(
        unicodedata.category(character) in {"Cc", "Cs"}
        for character in value
    ):
        _reject("qzone_payload_invalid")
    return value


def validate_instance_generation(value: Any) -> str:
    if type(value) is not str or _GENERATION_RE.fullmatch(value) is None:
        _reject("qzone_generation_invalid")
    return value


def validate_post_ref(value: Any) -> str:
    if type(value) is not str or _POST_REF_RE.fullmatch(value) is None:
        _reject("qzone_post_ref_invalid")
    return value


def validate_qzone_operation_payload(
    operation: Any,
    payload: Any,
) -> tuple[str, dict[str, Any]]:
    if type(operation) is not str:
        _reject("qzone_operation_unsupported")
    normalized_operation = operation.strip().lower()
    fields = _PAYLOAD_FIELDS.get(normalized_operation)
    if fields is None:
        _reject("qzone_operation_unsupported")
    source = _exact_dict(payload, fields)
    normalized: dict[str, Any]
    if normalized_operation == "feed":
        scope = _text(source["scope"], maximum=16, required=True)
        target_uin = _text(source["target_uin"], maximum=20)
        page = source["page"]
        if (
            scope not in {"self", "friends", "profile"}
            or type(page) is not int
            or not 1 <= page <= 10
            or (
                scope == "profile"
                and (not target_uin.isdigit() or not 5 <= len(target_uin) <= 20)
            )
            or (scope != "profile" and target_uin != "")
        ):
            _reject("qzone_payload_invalid")
        normalized = {
            "scope": scope,
            "target_uin": target_uin,
            "page": page,
        }
    elif normalized_operation in {"detail", "like", "delete"}:
        normalized = {"post_ref": validate_post_ref(source["post_ref"])}
    elif normalized_operation == "comment":
        normalized = {
            "post_ref": validate_post_ref(source["post_ref"]),
            "content": _text(source["content"], maximum=120, required=True),
        }
    elif normalized_operation == "publish":
        if type(source["auto_generate_image"]) is not bool:
            _reject("qzone_payload_invalid")
        normalized = {
            "content": _text(source["content"], maximum=300, required=True),
            "auto_generate_image": source["auto_generate_image"],
        }
    else:
        normalized = {}
    try:
        encoded = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        _reject("qzone_payload_invalid")
    if len(encoded) > QZONE_MAX_PAYLOAD_BYTES:
        _reject("qzone_payload_too_large")
    return normalized_operation, normalized


def build_qzone_descriptor(
    *,
    instance_generation: Any,
    lifecycle_state: Any,
    degraded_reasons: Any,
) -> dict[str, Any]:
    generation = validate_instance_generation(instance_generation)
    if lifecycle_state not in {"created", "ready", "superseded", "closed"}:
        _reject("qzone_lifecycle_invalid")
    if (
        type(degraded_reasons) is not list
        or any(type(item) is not str or not item or len(item) > 80 for item in degraded_reasons)
        or len(degraded_reasons) != len(set(degraded_reasons))
    ):
        _reject("qzone_descriptor_invalid")
    return {
        "plugin_id": QZONE_PLUGIN_ID,
        "instance_generation": generation,
        "api_family": QZONE_API_FAMILY,
        "api_version": QZONE_API_VERSION,
        "supported_task_versions": [QZONE_OPERATION_VERSION],
        "capabilities": list(QZONE_CAPABILITIES),
        "lifecycle_state": lifecycle_state,
        "degraded_reasons": list(degraded_reasons),
    }


def build_qzone_result(
    *,
    instance_generation: Any,
    operation: Any,
    ok: bool,
    code: Any,
    message: Any,
    data: Any,
) -> dict[str, Any]:
    generation = validate_instance_generation(instance_generation)
    if type(operation) is not str or operation not in _PAYLOAD_FIELDS:
        _reject("qzone_operation_unsupported")
    if type(ok) is not bool or type(code) is not str or len(code) > 64:
        _reject("qzone_result_invalid")
    if type(message) is not str or len(message) > 200:
        _reject("qzone_result_invalid")
    if type(data) is not dict:
        _reject("qzone_result_invalid")
    result = {
        "version": QZONE_RESULT_VERSION,
        "instance_generation": generation,
        "operation": operation,
        "ok": ok,
        "code": code,
        "message": message,
        "data": deepcopy(data),
    }
    try:
        encoded = json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        _reject("qzone_result_invalid")
    maximum = (
        QZONE_MAX_FEED_RESULT_BYTES
        if operation in {"feed", "detail"}
        else QZONE_MAX_SMALL_RESULT_BYTES
    )
    if len(encoded) > maximum:
        _reject("qzone_result_too_large")
    return result


def build_qzone_config_snapshot(
    *,
    instance_generation: Any,
    target_plugin_id: Any,
    settings: Any,
    credential: Any,
) -> dict[str, Any]:
    generation = validate_instance_generation(instance_generation)
    if target_plugin_id != QZONE_TARGET_PLUGIN_ID:
        _reject("qzone_snapshot_target_unsupported")
    settings_value = _exact_dict(
        settings,
        frozenset(
            {
                "enabled",
                "life_publish_enabled",
                "comment_inbox_enabled",
                "generated_image_enabled",
            }
        ),
        code="qzone_snapshot_invalid",
    )
    if any(type(value) is not bool for value in settings_value.values()):
        _reject("qzone_snapshot_invalid")
    credential_value = _exact_dict(
        credential,
        frozenset({"configured", "source", "state"}),
        code="qzone_snapshot_invalid",
    )
    if (
        type(credential_value["configured"]) is not bool
        or credential_value["source"] not in {"manual", "runtime", "none"}
        or credential_value["state"]
        not in {"ready", "missing", "invalid", "blocked", "unknown"}
    ):
        _reject("qzone_snapshot_invalid")
    payload = {
        "version": QZONE_CONFIG_SNAPSHOT_VERSION,
        "source_plugin_id": QZONE_PLUGIN_ID,
        "instance_generation": generation,
        "target_plugin_id": QZONE_TARGET_PLUGIN_ID,
        "settings": deepcopy(dict(settings_value)),
        "credential": deepcopy(dict(credential_value)),
    }
    try:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        _reject("qzone_snapshot_invalid")
    digest = hashlib.sha256(canonical).hexdigest()
    snapshot = {
        **payload,
        "snapshot_id": f"qzonesnap_{digest}",
        "snapshot_sha256": digest,
    }
    if set(snapshot) != QZONE_CONFIG_SNAPSHOT_FIELDS:
        _reject("qzone_snapshot_invalid")
    return snapshot


def validate_qzone_config_snapshot(value: Any) -> dict[str, Any]:
    snapshot = _exact_dict(
        value,
        QZONE_CONFIG_SNAPSHOT_FIELDS,
        code="qzone_snapshot_invalid",
    )
    rebuilt = build_qzone_config_snapshot(
        instance_generation=snapshot["instance_generation"],
        target_plugin_id=snapshot["target_plugin_id"],
        settings=snapshot["settings"],
        credential=snapshot["credential"],
    )
    if (
        type(snapshot["snapshot_id"]) is not str
        or type(snapshot["snapshot_sha256"]) is not str
        or _DIGEST_RE.fullmatch(snapshot["snapshot_sha256"]) is None
        or snapshot["snapshot_id"] != f"qzonesnap_{snapshot['snapshot_sha256']}"
        or rebuilt != snapshot
    ):
        _reject("qzone_snapshot_invalid")
    return deepcopy(rebuilt)


def bounded_number(value: Any, minimum: int, maximum: int) -> int:
    if type(value) is bool:
        return minimum
    try:
        numeric = int(value)
    except (TypeError, ValueError, OverflowError):
        return minimum
    return max(minimum, min(maximum, numeric))


__all__ = [
    "QZONE_API_FAMILY",
    "QZONE_API_VERSION",
    "QZONE_CAPABILITIES",
    "QZONE_CONFIG_SNAPSHOT_FIELDS",
    "QZONE_CONFIG_SNAPSHOT_VERSION",
    "QZONE_DESCRIPTOR_FIELDS",
    "QZONE_OPERATION_VERSION",
    "QZONE_PLUGIN_ID",
    "QZONE_RESULT_FIELDS",
    "QZONE_RESULT_VERSION",
    "QZONE_TARGET_PLUGIN_ID",
    "QzoneContractError",
    "bounded_number",
    "build_qzone_config_snapshot",
    "build_qzone_descriptor",
    "build_qzone_result",
    "validate_instance_generation",
    "validate_post_ref",
    "validate_qzone_config_snapshot",
    "validate_qzone_operation_payload",
]
