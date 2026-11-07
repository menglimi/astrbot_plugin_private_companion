"""V2 native provider for the Reality Companion extension.

The module deliberately speaks only the plain JSON provider contract.  V2
resolves the actor, role and audience; this provider derives the current user
from that context and never accepts a target identity from model arguments.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import math
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable


SCHEMA = "companion.native-provider.v1"
PLUGIN_ID = "astrbot_plugin_private_companion.reality"
PROVIDER_ID = "reality.native"
VERSION = "0.4.2"
# Device verbs the provider will forward.  "power" is the original single
# switch; the parameterised verbs are only ever allowed on properties the
# device profile itself marks as writable (see mihome_web_api.py).
DEVICE_ACTIONS = ("power", "set_mode", "set_brightness", "set_color_temperature")
_ACTIONS = {
    "reality.snapshot.read", "reality.camera.observe", "reality.device.list", "reality.device.status",
    "reality.device.inspect", "reality.device.control",
    "reality.device.action", "reality.operation.lookup", "reality.operation.cancel",
}
_READ_ACTIONS = {
    "reality.snapshot.read", "reality.device.list", "reality.device.status",
    "reality.device.inspect", "reality.operation.lookup",
}
# Actions that answer purely from this provider's own journal.
_REQUIRED = {
    "schema", "invocation_id", "parent_task_id", "tool_call_id", "action",
    "origin_key", "context", "binding", "arguments", "arguments_hash", "deadline_at",
}


class NativeRealityError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise NativeRealityError("invalid_native_json") from exc


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _text(value: Any, name: str, limit: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value or len(value) > limit:
        raise NativeRealityError("invalid_" + name)
    return value.strip()


def _normalized_context(value: dict[str, Any]) -> dict[str, Any]:
    """Drop only the host's rotating policy marker from durable identity."""
    copied = copy.deepcopy(value)

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            item.pop("policy_revision", None)
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(copied)
    return copied


def scope_key(context: dict[str, Any]) -> str:
    return _hash(_normalized_context(context))


def _user_id(context: dict[str, Any]) -> str:
    actor = context.get("actor") if isinstance(context, dict) else None
    subject = actor.get("subject") if isinstance(actor, dict) else None
    principal = actor.get("principal") if isinstance(actor, dict) else None
    _text(subject.get("subject_id") if isinstance(subject, dict) else None, "context_subject", 160)
    value = principal.get("native_account_id") if isinstance(principal, dict) else None
    return _text(value, "context_user", 80)


def _package_hash() -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def _operation_identity(request: dict[str, Any]) -> str:
    return _hash({
        key: request[key]
        for key in ("schema", "invocation_id", "parent_task_id", "tool_call_id",
                    "action", "origin_key", "context", "binding", "arguments",
                    "arguments_hash")
    } | {"context": _normalized_context(request["context"])})


class OperationStore:
    """Small atomic JSON journal owned by this plugin, never V1 user data."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "operations": {}}
        except (OSError, ValueError, UnicodeError) as exc:
            raise NativeRealityError("native_operation_store_invalid") from exc
        operations = value.get("operations") if isinstance(value, dict) else None
        if (not isinstance(value, dict) or value.get("version") != 1 or not isinstance(operations, dict)
                or any(not isinstance(bucket, dict) or any(not isinstance(item, dict)
                       for item in bucket.values()) for bucket in operations.values())):
            raise NativeRealityError("native_operation_store_invalid")
        # Journals written before dispatched_at existed are upgraded on load:
        # a row already past prepared has provably been dispatched, so its last
        # update is the best available stamp.  "prepared" stays un-dispatched.
        for bucket in operations.values():
            for item in bucket.values():
                if "dispatched_at" not in item:
                    item["dispatched_at"] = (
                        float(item.get("updated_at") or item.get("created_at") or 0.0)
                        if item.get("state") != "prepared" else None
                    )
        return value

    def _save(self) -> None:
        payload = _json(self._data)
        temp = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with open(temp, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass

    def begin(self, scope: str, request: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        identity = _operation_identity(request)
        with self._lock:
            for existing_scope, operations in self._data["operations"].items():
                if existing_scope != scope and any(
                    item.get("invocation_id") == request["invocation_id"] for item in operations.values()
                ):
                    raise NativeRealityError("native_original_invocation_mismatch")
            bucket = self._data["operations"].setdefault(scope, {})
            for operation in bucket.values():
                if operation.get("invocation_id") == request["invocation_id"]:
                    if operation.get("identity") != identity:
                        raise NativeRealityError("native_original_invocation_mismatch")
                    return copy.deepcopy(operation), False
            operation = {
                "id": "reality-op-" + uuid.uuid4().hex,
                "scope": scope,
                "identity": identity,
                "invocation_id": request["invocation_id"],
                "action": request["action"],
                "request": {
                    "schema": request["schema"], "invocation_id": request["invocation_id"],
                    "parent_task_id": request["parent_task_id"], "tool_call_id": request["tool_call_id"],
                    "action": request["action"], "origin_key": request["origin_key"],
                    "context": _normalized_context(request["context"]),
                    "binding": dict(request["binding"]), "arguments": copy.deepcopy(request["arguments"]),
                    "arguments_hash": request["arguments_hash"],
                },
                "state": "prepared", "effect": "not_committed", "result": None,
                "error": None, "created_at": time.time(), "updated_at": time.time(),
                "dispatched_at": None,
            }
            bucket[operation["id"]] = operation
            self._save()
            return copy.deepcopy(operation), True

    def get(self, scope: str, operation_id: str) -> dict[str, Any]:
        with self._lock:
            operation = self._data["operations"].get(scope, {}).get(operation_id)
            if not isinstance(operation, dict):
                raise NativeRealityError("native_operation_not_found")
            return copy.deepcopy(operation)

    def latest(self, scope: str, action: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            values = list(self._data["operations"].get(scope, {}).values())
        if action:
            values = [item for item in values if item.get("action") == action]
        return sorted((copy.deepcopy(item) for item in values),
                      key=lambda item: (float(item.get("created_at", 0)), item.get("id", "")),
                      reverse=True)

    def dispatched(self, scope: str, operation_id: str) -> bool:
        """True once the operation has crossed the dispatch boundary.

        Reads the explicit ``dispatched_at`` stamp rather than the ``state``
        column: between the dispatching write and the result write the state is
        an intermediate value that must not be mistaken for "never dispatched".
        """
        operation = self.get(scope, operation_id)
        return operation.get("dispatched_at") is not None

    def cancel(self, scope: str, operation_id: str) -> dict[str, Any]:
        """Refuse to cancel anything that has already crossed the dispatch boundary.

        A dispatched operation may already have taken effect; the honest answer
        is to keep it and let the caller query the original operation.
        """
        operation = self.get(scope, operation_id)
        if operation.get("state") != "prepared" or operation.get("dispatched_at") is not None:
            raise NativeRealityError("native_operation_not_cancellable")
        return self.update(scope, operation_id, state="cancelled", effect="not_committed",
                           error="cancelled_before_dispatch")

    def update(self, scope: str, operation_id: str, *, state: str, effect: str,
               result: dict[str, Any] | None = None, error: str | None = None,
               dispatched_at: float | None = None) -> dict[str, Any]:
        with self._lock:
            operation = self._data["operations"].get(scope, {}).get(operation_id)
            if not isinstance(operation, dict):
                raise NativeRealityError("native_operation_not_found")
            operation.update(state=state, effect=effect, result=copy.deepcopy(result),
                             error=error, updated_at=time.time())
            if dispatched_at is not None:
                operation["dispatched_at"] = dispatched_at
            self._save()
            return copy.deepcopy(operation)


async def _checked(callback: Callable) -> None:
    if not callable(callback):
        raise NativeRealityError("host_guard_required")
    result = callback()
    if inspect.isawaitable(result):
        result = await result
    if result is False:
        raise NativeRealityError("host_authority_revoked")


def _actor_subject(context: Any) -> dict[str, Any] | None:
    """The exact human subject this turn belongs to, forwarded for authority checks."""
    actor = context.get("actor") if isinstance(context, dict) else None
    subject = actor.get("subject") if isinstance(actor, dict) else None
    return subject if isinstance(subject, dict) else None


def _bind_power(handler: Callable, alias: str, is_on: bool, actor: dict[str, Any] | None):
    """Freeze the legacy two-argument power call (extension_control_power)."""

    async def dispatch() -> dict[str, Any]:
        return await handler(alias, is_on, native=True, actor=actor)

    return dispatch


def _bind_device_action(handler: Callable, alias: str, verb: str, prop: str | None, amount: Any,
                        binding_revision: str, actor: dict[str, Any] | None):
    """Freeze the parameterised device call (extension_device_action).

    The verb key -- not the property name -- is what the API switches on, and
    the binding revision is carried through so a rebound alias is refused.
    """

    async def dispatch() -> dict[str, Any]:
        return await handler(alias, verb, amount, native=True, actor=actor,
                             binding_revision=binding_revision)

    return dispatch


def _binding_revision(target: Callable, alias: str, actor: dict[str, Any] | None) -> str:
    """Ask the provider for the current binding revision of one exact alias."""
    value = target(alias, native=True, actor=actor)
    if isinstance(value, dict):
        return str(value.get("binding_revision") or "")
    return str(value or "")


def _parse_verb(arguments: dict[str, Any]) -> tuple[str, str | None, Any]:
    """Turn one declared device verb into (verb, property, value).

    Values arrive as strings because the host's closed parameter schema subset
    rejects union types; every conversion is re-validated here.
    """
    x = arguments["action"]
    raw = arguments.get("value")
    if x == "power":
        if not isinstance(raw, str) or raw.strip().lower() not in {"on", "off"}:
            raise NativeRealityError("invalid_reality_device_value")
        return "power", None, raw.strip().lower() == "on"
    prop = {"set_mode": "mode", "set_brightness": "brightness",
            "set_color_temperature": "color_temperature"}.get(x)
    if prop is None:
        # Profile-driven name: the property/action lookup happens in the device
        # profile, so the provider only bounds the string it forwards.
        if raw is None:
            return x, None, ""
        if not isinstance(raw, str) or not raw.strip():
            raise NativeRealityError("invalid_reality_device_value")
        return x, None, raw.strip()[:256]
    if not isinstance(raw, str) or not raw.strip():
        raise NativeRealityError("invalid_reality_device_value")
    text = raw.strip()
    if x == "set_mode":
        return x, prop, text[:60]
    try:
        number = int(text)
    except ValueError as exc:
        raise NativeRealityError("invalid_reality_device_value") from exc
    if not 1 <= number <= 100:
        raise NativeRealityError("invalid_reality_device_value")
    return x, prop, number


def _camera_observation(value: dict[str, Any]) -> dict[str, Any]:
    """Keep only bounded visual facts; camera bytes and provider metadata never leave Reality."""
    fields = {
        "scene_description": 500, "purpose_answer": 240, "activity_detail": 160,
        "uncertainty": 180, "summary": 500, "presence": 32, "activity": 32,
        "interruptibility": 32, "brightness": 32, "answer_status": 32,
        "food_visibility": 32, "visible_food": 80,
    }
    result: dict[str, Any] = {}
    for key, limit in fields.items():
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            result[key] = item.strip()[:limit]
    evidence = value.get("visible_evidence")
    if isinstance(evidence, list):
        result["visible_evidence"] = [item.strip()[:100] for item in evidence[:6]
                                      if isinstance(item, str) and item.strip()]
    confidence = value.get("confidence")
    if type(confidence) in (int, float) and math.isfinite(confidence):
        result["confidence"] = round(max(0.0, min(1.0, confidence)), 2)
    return result


class NativeRealityProvider:
    def __init__(self, plugin: Any, data_dir: Path, *, version: str = VERSION,
                 package_hash: str | None = None):
        self.plugin = plugin
        self.version = _text(version, "package_version", 80)
        self.artifact_hash = package_hash or _package_hash()
        self.generation = "reality-" + _hash({"version": self.version, "artifact_hash": self.artifact_hash})
        self.store = OperationStore(Path(data_dir) / "native_v2" / "operations.json")
        self._closed = False

    def _live(self) -> None:
        if self._closed:
            raise NativeRealityError("reality_provider_stopped")

    def manifest(self) -> dict[str, Any]:
        self._live()
        return {
            "schema": SCHEMA, "plugin_id": PLUGIN_ID, "provider_id": PROVIDER_ID,
            "generation": self.generation,
            "package": {"extension_id": "reality", "package_id": PLUGIN_ID,
                         "publisher_id": "menglimi", "package_version": self.version,
                         "artifact_hash": self.artifact_hash,
                         "capabilities": sorted(_ACTIONS), "dataset_owners": [PROVIDER_ID]},
            "tools": [
                {"action": "reality.snapshot.read", "capability": "reality.snapshot.read",
                 "source_owner": PROVIDER_ID, "description": "读取当前 V2 身份对应的现实触及摘要。",
                 "parameters": {"type": "object", "properties": {}, "required": [],
                                "additionalProperties": False}, "side_effect": "none", "timeout_seconds": 10},
                {"action": "reality.camera.observe", "capability": "reality.camera.observe",
                 "source_owner": PROVIDER_ID,
                 "description": "只在当前私聊用户明确要求查看摄像头时读取一帧，并调用现实触及已配置的视觉模型分析；需要独立摄像头确认与 V2 私聊授权，不自动采集，不保留原图。",
                 "parameters": {"type": "object", "properties": {"purpose": {"type": "string"}},
                                "required": ["purpose"], "additionalProperties": False},
                 "side_effect": "external_network", "timeout_seconds": 60},
                {"action": "reality.device.list", "capability": "reality.device.list",
                 "source_owner": PROVIDER_ID, "description": "列出已映射的米家设备别名、类别和缓存在线状态；控制白名单仅供参考，不代表当前用户可控制。",
                 "parameters": {"type": "object", "properties": {}, "required": [],
                                "additionalProperties": False}, "side_effect": "none", "timeout_seconds": 10},
                {"action": "reality.device.status", "capability": "reality.device.status",
                 "source_owner": PROVIDER_ID, "description": "按设备目录中的精确别名读取米家设备状态；只读查询，不控制设备。",
                 "parameters": {"type": "object", "properties": {"alias": {"type": "string"}},
                                "required": ["alias"], "additionalProperties": False},
                 "side_effect": "none", "timeout_seconds": 30},
                {"action": "reality.device.inspect", "capability": "reality.device.inspect",
                 "source_owner": PROVIDER_ID,
                 "description": "按精确别名读取米家设备的静态能力：可写属性（中文名、示例值、说明）、可读属性与已声明动作。控制前先查它，只按这里出现的名称下发。",
                 "parameters": {"type": "object", "properties": {"alias": {"type": "string"}},
                                "required": ["alias"], "additionalProperties": False},
                 "side_effect": "none", "timeout_seconds": 10},
                {"action": "reality.device.control", "capability": "reality.device.control",
                 "source_owner": PROVIDER_ID, "description": "仅对米家设置中启用且列入控制白名单的设备按精确别名控制电源开关，并返回确认回执。",
                 "parameters": {"type": "object", "properties": {
                     "alias": {"type": "string"}, "is_on": {"type": "boolean"}},
                     "required": ["alias", "is_on"], "additionalProperties": False},
                 "side_effect": "external_network", "timeout_seconds": 30},
                {"action": "reality.device.action", "capability": "reality.device.action",
                 "source_owner": PROVIDER_ID,
                 "description": "按精确别名对已授权设备执行一个具名动作。power 需要用 value=\"on\" 或 \"off\"；set_brightness / set_color_temperature / set_mode 只在该设备档案自身声明该属性可写时才允许，value 传字符串形式的取值。也可以直接传 reality.device.inspect 返回的中文属性名（如“温度”“模式”）或中文动作名（如“开始清扫”），由设备画像换算成底层属性与取值。可选 expect_revision 用来固定本次动作针对的设备绑定修订：别名在此期间被换绑时该次动作会被拒绝，而不会静默作用到新设备上。是否成功以设备云端确认层为准。",
                 "parameters": {"type": "object", "properties": {
                     "alias": {"type": "string"},
                     "action": {"type": "string"},
                     "value": {"type": "string"},
                     "expect_revision": {"type": "string"}},
                     "required": ["alias", "action"], "additionalProperties": False},
                 "side_effect": "external_device", "timeout_seconds": 30},
                {"action": "reality.operation.lookup", "capability": "reality.operation.lookup",
                 "source_owner": PROVIDER_ID,
                 "description": "按操作编号或原调用编号读回本提供方已保存的原操作与最新结果。只读账本，不会重新执行设备动作、不会触发相机采集或付费分析。",
                 "parameters": {"type": "object", "properties": {
                     "operation_id": {"type": "string"}},
                     "required": [], "additionalProperties": False},
                 "side_effect": "none", "timeout_seconds": 10},
                {"action": "reality.operation.cancel", "capability": "reality.operation.cancel",
                 "source_owner": PROVIDER_ID,
                 "description": "取消一个尚未派发的设备操作。已经越过派发边界的操作无法取消，结果保持 unknown，请改用查询核对。",
                 "parameters": {"type": "object", "properties": {
                     "operation_id": {"type": "string"}},
                     "required": ["operation_id"], "additionalProperties": False},
                 "side_effect": "local", "timeout_seconds": 10},
            ],
        }

    def _request(self, request: dict[str, Any]) -> dict[str, Any]:
        self._live()
        if not isinstance(request, dict) or not _REQUIRED.issubset(request) or request.get("schema") != SCHEMA:
            raise NativeRealityError("invalid_native_request")
        for key in ("invocation_id", "parent_task_id", "tool_call_id", "origin_key", "arguments_hash", "deadline_at"):
            _text(request.get(key), key, 2048)
        context = request.get("context")
        if not isinstance(context, dict) or not context:
            raise NativeRealityError("invalid_native_context")
        _user_id(context)
        binding = request.get("binding")
        if not isinstance(binding, dict) or binding != {
            "capability": request.get("action"), "provider": PROVIDER_ID,
            "version": self.version, "generation": self.generation,
        }:
            raise NativeRealityError("native_provider_binding_mismatch")
        action = request.get("action")
        if action not in _ACTIONS or not isinstance(request.get("arguments"), dict):
            raise NativeRealityError("unsupported_reality_operation")
        arguments = request["arguments"]
        if action == "reality.operation.cancel":
            if set(arguments) != {"operation_id"}:
                raise NativeRealityError("invalid_reality_arguments")
            _text(arguments.get("operation_id"), "operation_id", 160)
        elif action == "reality.operation.lookup":
            if set(arguments) - {"operation_id"}:
                raise NativeRealityError("invalid_reality_arguments")
            if "operation_id" in arguments:
                _text(arguments.get("operation_id"), "operation_id", 160)
        elif action in {"reality.snapshot.read", "reality.device.list"}:
            if arguments:
                raise NativeRealityError("invalid_reality_arguments")
        elif action == "reality.camera.observe":
            scope = context.get("audience", {}).get("scope", {})
            if scope.get("kind") != "private":
                raise NativeRealityError("reality_camera_private_required")
            if set(arguments) != {"purpose"}:
                raise NativeRealityError("invalid_reality_arguments")
            _text(arguments.get("purpose"), "camera_purpose", 120)
        elif action == "reality.device.status":
            if set(arguments) != {"alias"}:
                raise NativeRealityError("invalid_reality_arguments")
            _text(arguments.get("alias"), "device_alias", 120)
        elif action == "reality.device.inspect":
            if set(arguments) != {"alias"}:
                raise NativeRealityError("invalid_reality_arguments")
            _text(arguments.get("alias"), "device_alias", 120)
        elif action == "reality.device.action":
            if set(arguments) - {"alias", "action", "value", "expect_revision"} \
                    or not {"alias", "action"} <= set(arguments):
                raise NativeRealityError("invalid_reality_arguments")
            _text(arguments.get("alias"), "device_alias", 120)
            name = arguments.get("action")
            if not isinstance(name, str) or not name.strip() or len(name) > 64 or "\x00" in name:
                raise NativeRealityError("invalid_reality_device_action")
            if "value" in arguments and (not isinstance(arguments["value"], str)
                                         or len(arguments["value"]) > 256 or "\x00" in arguments["value"]):
                raise NativeRealityError("invalid_reality_device_value")
            if "expect_revision" in arguments and (
                    not isinstance(arguments["expect_revision"], str)
                    or not arguments["expect_revision"].strip()
                    or len(arguments["expect_revision"]) > 64):
                raise NativeRealityError("invalid_reality_expected_revision")
        else:
            if set(arguments) != {"alias", "is_on"} or type(arguments.get("is_on")) is not bool:
                raise NativeRealityError("invalid_reality_arguments")
            _text(arguments.get("alias"), "device_alias", 120)
        return request

    def _response(self, request: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
        succeeded = operation.get("state") == "succeeded"
        failed = operation.get("state") == "failed"
        state = "succeeded" if succeeded else "failed" if failed else "unknown"
        result = operation.get("result") if succeeded else None
        read_only = request["action"] in _READ_ACTIONS
        return {
            "schema": SCHEMA, "invocation_id": request["invocation_id"],
            "origin_key": request["origin_key"], "arguments_hash": request["arguments_hash"],
            "binding": request["binding"], "state": state,
            "effect": "none" if read_only else operation.get("effect") if state != "unknown" else "unknown",
            "operation_id": None if read_only else operation.get("id"),
            "receipt_ref": f"{PROVIDER_ID}.operation:{operation.get('id')}",
            "result": result if isinstance(result, dict) else None, "artifacts": [],
        }

    async def execute(self, request: dict[str, Any], *, before_dispatch) -> dict[str, Any]:
        value = self._request(request)
        await _checked(before_dispatch)
        scope = scope_key(value["context"])
        operation, fresh = self.store.begin(scope, value)
        if not fresh:
            return self._response(value, operation)
        if value["action"] in _READ_ACTIONS:
            try:
                if value["action"] == "reality.snapshot.read":
                    user_id = _user_id(value["context"])
                    snapshot = self.plugin.mobile_context(user_id)
                    if not isinstance(snapshot, dict):
                        raise NativeRealityError("invalid_reality_snapshot")
                    result = {"snapshot": snapshot}
                elif value["action"] == "reality.operation.lookup":
                    # Read the journal only.  Nothing here may turn a query into
                    # a device retry, a camera capture or a paid analysis.
                    return self._response(value, self._resolve_operation(scope, value))
                else:
                    web_api = getattr(self.plugin, "mihome_web_api", None)
                    method_name = {"reality.device.list": "extension_device_directory",
                                   "reality.device.inspect": "extension_device_profile",
                                   }.get(value["action"], "extension_read_device_status")
                    handler = getattr(web_api, method_name, None) if web_api is not None else None
                    if not callable(handler):
                        raise NativeRealityError("reality_device_read_unavailable")
                    result = handler(**value["arguments"])
                    if inspect.isawaitable(result):
                        result = await result
                    if not isinstance(result, dict):
                        raise NativeRealityError("invalid_reality_device_result")
                await _checked(before_dispatch)
                operation = self.store.update(scope, operation["id"], state="succeeded", effect="none",
                                              result=result)
            except Exception as exc:
                code = exc.code if isinstance(exc, NativeRealityError) else "reality_read_failed"
                operation = self.store.update(scope, operation["id"], state="failed", effect="not_committed",
                                              error=code)
            return self._response(value, operation)
        if value["action"] == "reality.camera.observe":
            try:
                await _checked(before_dispatch)
                self.store.update(scope, operation["id"], state="dispatching", effect="unknown",
                                  dispatched_at=time.time())
            except Exception as exc:
                code = exc.code if isinstance(exc, NativeRealityError) else "host_authority_revoked"
                operation = self.store.update(scope, operation["id"], state="failed",
                                              effect="not_committed", error=code)
                return self._response(value, operation)
            try:
                observer = getattr(self.plugin, "camera_snapshot", None)
                if not callable(observer):
                    raise NativeRealityError("reality_camera_unavailable")
                raw = await observer(_user_id(value["context"]), value["arguments"]["purpose"],
                                     source="assistant_tool_private")
                if type(raw) is not dict:
                    raise NativeRealityError("invalid_reality_camera_result")
                status = raw.get("status")
                if status in {"disabled", "forbidden", "cooldown"}:
                    operation = self.store.update(scope, operation["id"], state="failed",
                                                  effect="not_committed", error="reality_camera_" + status)
                    return self._response(value, operation)
                if status not in {"success", "observation_uncertain"} or raw.get("captured") is not True:
                    operation = self.store.update(scope, operation["id"], state="unknown", effect="unknown",
                                                  error="reality_camera_result_uncertain")
                    return self._response(value, operation)
                observation = raw.get("observation")
                if type(observation) is not dict:
                    operation = self.store.update(scope, operation["id"], state="unknown", effect="unknown",
                                                  error="invalid_reality_camera_result")
                    return self._response(value, operation)
                projected = _camera_observation(observation)
                answer_available = raw.get("answer_available") is True and status == "success"
                result = {
                    "status": "success" if answer_available else "observation_uncertain",
                    "captured": True,
                    "answer_available": answer_available,
                    "purpose": value["arguments"]["purpose"],
                    "observation": projected,
                    "message": "已完成一次单帧观察" if answer_available else "已读取单帧，但没有可靠判断结果",
                    "final_response_instruction": (
                        "只陈述 observation 中明确出现的事实，不从姿势、时间、房间物品或气氛推断情绪、身份、意图或其他状态。"
                        if answer_available else
                        "本次未能从单帧中可靠判断。只简短说明无法判断，不猜测画面或原因，不在本轮重试。"
                    ),
                }
                operation = self.store.update(scope, operation["id"], state="succeeded", effect="committed",
                                              result=result)
            except asyncio.CancelledError:
                operation = self.store.update(scope, operation["id"], state="unknown", effect="unknown",
                                              error="execution_interrupted")
                raise
            except Exception as exc:
                code = exc.code if isinstance(exc, NativeRealityError) else "reality_camera_result_uncertain"
                operation = self.store.update(scope, operation["id"], state="unknown", effect="unknown",
                                              error=code)
            return self._response(value, operation)
        # ---- journal-only branches: never touch a device -------------------
        if value["action"] == "reality.operation.cancel":
            try:
                await _checked(before_dispatch)
                operation = self.store.cancel(scope, value["arguments"]["operation_id"])
            except Exception as exc:
                code = exc.code if isinstance(exc, NativeRealityError) else "reality_operation_cancel_failed"
                operation = self.store.update(scope, operation["id"], state="failed",
                                              effect="not_committed", error=code)
            return self._response(value, operation)
        # ---- device write branches ----------------------------------------
        try:
            await _checked(before_dispatch)
            web_api = getattr(self.plugin, "mihome_web_api", None)
            alias = value["arguments"]["alias"]
            actor = _actor_subject(value["context"])
            # Argument decoding happens before dispatch: a malformed value must
            # produce a not_committed receipt, never an unknown one.
            if value["action"] == "reality.device.action":
                verb, prop, amount = _parse_verb(value["arguments"])
            else:
                verb, prop, amount = "power", None, value["arguments"]["is_on"]
            if value["action"] == "reality.device.action":
                handler = getattr(web_api, "extension_device_action", None) if web_api is not None else None
                target = getattr(web_api, "extension_device_target", None) if web_api is not None else None
            else:
                handler = getattr(web_api, "extension_control_power", None) if web_api is not None else None
                target = getattr(web_api, "extension_control_preflight", None) if web_api is not None else None
            if not callable(handler):
                raise NativeRealityError("reality_device_control_unavailable")
            # The preflight runs before the external call and proves the exact
            # alias is still mapped and still allow-listed.  A caller that pinned
            # a binding revision is checked against that pin, so a rebind between
            # preparing and dispatching is refused instead of silently following
            # the new device.
            current_revision = _binding_revision(target, alias, actor) if callable(target) else ""
            pinned = str(value["arguments"].get("expect_revision") or "").strip() \
                if value["action"] == "reality.device.action" else ""
            if pinned and pinned != current_revision:
                raise NativeRealityError("reality_device_binding_rebound")
            if value["action"] == "reality.device.action":
                handler = _bind_device_action(handler, alias, verb, prop, amount,
                                              current_revision, actor)
            else:
                handler = _bind_power(handler, alias, amount, actor)
        except Exception as exc:
            code = exc.code if isinstance(exc, NativeRealityError) else "reality_device_control_unavailable"
            operation = self.store.update(scope, operation["id"], state="failed", effect="not_committed", error=code)
            return self._response(value, operation)
        # The external call begins only after the last authority check. Once
        # dispatched, a transport failure is unknown and is never replayed.
        # A journal failure here must not escape without a receipt, and the
        # explicit dispatched_at stamp is what cancel/lookup read afterwards.
        try:
            self.store.update(scope, operation["id"], state="dispatching", effect="unknown",
                              dispatched_at=time.time())
        except Exception as exc:
            code = exc.code if isinstance(exc, NativeRealityError) else "reality_operation_store_write_failed"
            operation = self.store.update(scope, operation["id"], state="failed", effect="not_committed", error=code)
            return self._response(value, operation)
        try:
            result = await handler()
            if not isinstance(result, dict):
                raise NativeRealityError("invalid_reality_receipt")
            if result.get("confirmed") is not True:
                raise NativeRealityError("reality_device_result_unconfirmed")
            operation = self.store.update(scope, operation["id"], state="succeeded", effect="committed",
                                          result={"control": result})
        except asyncio.CancelledError:
            operation = self.store.update(scope, operation["id"], state="unknown", effect="unknown",
                                          error="execution_interrupted")
            raise
        except Exception as exc:
            code = exc.code if isinstance(exc, NativeRealityError) else "reality_device_result_unconfirmed"
            operation = self.store.update(scope, operation["id"], state="unknown", effect="unknown", error=code)
        return self._response(value, operation)

    def _same_request(self, request: dict[str, Any], operation: dict[str, Any]) -> None:
        if _operation_identity(request) != operation.get("identity"):
            raise NativeRealityError("native_original_invocation_mismatch")

    def _resolve_operation(self, scope: str, value: dict[str, Any]) -> dict[str, Any]:
        """Locate the original operation by explicit ID, else by its invocation.

        An explicit operation ID names an existing operation, so the request
        that carries it is a *new* invocation and cannot share that operation's
        identity.  Only the same-invocation fallback must prove it is asking
        about its own original request.
        """
        operation_id = value["arguments"].get("operation_id") if value["action"] == "reality.operation.lookup" \
            else value.get("operation_id")
        if isinstance(operation_id, str) and operation_id:
            # store.get is scoped by normalized context, so a caller can only
            # ever name an operation inside its own scope.
            return self.store.get(scope, operation_id)
        candidates = [item for item in self.store.latest(scope, value["action"])
                      if item.get("invocation_id") == value["invocation_id"]]
        if not candidates:
            raise NativeRealityError("native_operation_not_found")
        operation = candidates[0]
        self._same_request(value, operation)
        return operation

    async def lookup(self, request: dict[str, Any], *, before_dispatch) -> dict[str, Any]:
        value = self._request(request)
        await _checked(before_dispatch)
        scope = scope_key(value["context"])
        return self._response(value, self._resolve_operation(scope, value))

    async def close(self) -> None:
        self._closed = True
