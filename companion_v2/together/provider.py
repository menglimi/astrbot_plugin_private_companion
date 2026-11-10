"""Plain JSON V2 native port for room lifecycle operations."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable


logger = logging.getLogger(__name__)
SCHEMA = "companion.native-provider.v1"
PLUGIN_ID = "astrbot_plugin_private_companion.together"
PROVIDER_ID = "together.native"
VERSION = "0.9.0"
_ACTIONS = {"together.room.snapshot", "together.room.create", "together.room.close",
            "together.room.members", "together.room.invite", "together.room.invitations",
            "together.room.join"}
_READ_ACTIONS = {"together.room.snapshot", "together.room.members", "together.room.invitations"}
# Bounds for an invitation's lifetime; a caller may shorten it, never extend it.
INVITE_MIN_SECONDS = 60
INVITE_MAX_SECONDS = 3600
INVITE_DEFAULT_SECONDS = 600
_MODES = {"call", "watch", "work", "novel"}
_REQUIRED = {
    "schema", "invocation_id", "parent_task_id", "tool_call_id", "action",
    "origin_key", "context", "binding", "arguments", "arguments_hash", "deadline_at",
}


class NativeTogetherError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise NativeTogetherError("invalid_native_json") from exc


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _text(value: Any, name: str, limit: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value or len(value) > limit:
        raise NativeTogetherError("invalid_" + name)
    return value.strip()


def _normalized_context(value: dict[str, Any]) -> dict[str, Any]:
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
    identity = {
        key: request[key]
        for key in ("schema", "invocation_id", "parent_task_id", "tool_call_id",
                    "action", "origin_key", "context", "binding", "arguments",
                    "arguments_hash")
    }
    identity["context"] = _normalized_context(request["context"])
    if request["action"] == "together.room.create":
        persona = request["host_persona"]
        identity["host_persona"] = {"persona_id": persona["persona_id"],
                                    "bot_id": persona["bot_id"], "account_id": persona["account_id"],
                                    "prompt_hash": hashlib.sha256(persona["prompt"].encode("utf-8")).hexdigest()}
        identity["host_execution"] = request["host_execution"]
    return _hash(identity)


class OperationStore:
    """Atomic JSON journal under Together's own plugin data directory."""

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
            raise NativeTogetherError("native_operation_store_invalid") from exc
        operations = value.get("operations") if isinstance(value, dict) else None
        if (not isinstance(value, dict) or value.get("version") != 1 or not isinstance(operations, dict)
                or any(not isinstance(bucket, dict) or any(not isinstance(item, dict)
                       for item in bucket.values()) for bucket in operations.values())):
            raise NativeTogetherError("native_operation_store_invalid")
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
                    raise NativeTogetherError("native_original_invocation_mismatch")
            bucket = self._data["operations"].setdefault(scope, {})
            for operation in bucket.values():
                if operation.get("invocation_id") == request["invocation_id"]:
                    if operation.get("identity") != identity:
                        raise NativeTogetherError("native_original_invocation_mismatch")
                    return copy.deepcopy(operation), False
            operation = {
                "id": "together-op-" + uuid.uuid4().hex,
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
                    "host_persona_hash": hashlib.sha256(_json(request.get("host_persona")).encode("utf-8")).hexdigest()
                        if request["action"] == "together.room.create" else None,
                    "host_execution_hash": _hash(request["host_execution"])
                        if request["action"] == "together.room.create" else None,
                },
                "state": "prepared", "effect": "not_committed", "result": None,
                "error": None, "created_at": time.time(), "updated_at": time.time(),
            }
            bucket[operation["id"]] = operation
            self._save()
            return copy.deepcopy(operation), True

    def get(self, scope: str, operation_id: str) -> dict[str, Any]:
        with self._lock:
            operation = self._data["operations"].get(scope, {}).get(operation_id)
            if not isinstance(operation, dict):
                raise NativeTogetherError("native_operation_not_found")
            return copy.deepcopy(operation)

    def find(self, operation_id: str) -> dict[str, Any] | None:
        """Return one operation by id without knowing its scope.

        Transport layers only see the operation id (the scope hash is internal
        to this provider), so a failed room still needs a supported way to read
        back its error code for diagnostics.
        """
        if not isinstance(operation_id, str) or not operation_id:
            return None
        with self._lock:
            for bucket in self._data["operations"].values():
                operation = bucket.get(operation_id) if isinstance(bucket, dict) else None
                if isinstance(operation, dict):
                    return copy.deepcopy(operation)
        return None

    def latest(self, scope: str, action: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            values = list(self._data["operations"].get(scope, {}).values())
        if action:
            values = [item for item in values if item.get("action") == action]
        return sorted((copy.deepcopy(item) for item in values),
                      key=lambda item: (float(item.get("created_at", 0)), item.get("id", "")),
                      reverse=True)

    def update(self, scope: str, operation_id: str, *, state: str, effect: str,
               result: dict[str, Any] | None = None, error: str | None = None) -> dict[str, Any]:
        with self._lock:
            operation = self._data["operations"].get(scope, {}).get(operation_id)
            if not isinstance(operation, dict):
                raise NativeTogetherError("native_operation_not_found")
            operation.update(state=state, effect=effect, result=copy.deepcopy(result),
                             error=error, updated_at=time.time())
            self._save()
            return copy.deepcopy(operation)


class InvitationStore:
    """Invitations for one V2 scope, recorded next to the operation journal.

    An invitation is a *record*, not a capability: holding one never grants a
    room by itself, and consuming one is a single atomic transition inside the
    scope that issued it.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "scopes": {}}
        except (OSError, ValueError, UnicodeError) as exc:
            raise NativeTogetherError("native_invitation_store_invalid") from exc
        scopes = value.get("scopes") if isinstance(value, dict) else None
        if (not isinstance(value, dict) or value.get("version") != 1 or not isinstance(scopes, dict)
                or any(not isinstance(bucket, dict) or any(not isinstance(item, dict)
                       for item in bucket.values()) for bucket in scopes.values())):
            raise NativeTogetherError("native_invitation_store_invalid")
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

    def issue(self, scope: str, *, inviter: str, room_id: str, room_mode: str,
              expires_at: float, revision: int = 1) -> dict[str, Any]:
        invitation = {
            "id": "together-invite-" + uuid.uuid4().hex,
            "scope": scope,
            "inviter": inviter,
            "room_id": room_id,
            "room_mode": room_mode,
            "revision": int(revision),
            "stage": "open",
            "created_at": time.time(),
            "expires_at": float(expires_at),
            "joined_by": [],
        }
        with self._lock:
            self._data["scopes"].setdefault(scope, {})[invitation["id"]] = invitation
            self._save()
            return copy.deepcopy(invitation)

    def get(self, scope: str, invitation_id: str) -> dict[str, Any]:
        with self._lock:
            invitation = self._data["scopes"].get(scope, {}).get(invitation_id)
            if not isinstance(invitation, dict):
                raise NativeTogetherError("native_invitation_not_found")
            return copy.deepcopy(invitation)

    def active(self, scope: str) -> list[dict[str, Any]]:
        """Open, unexpired invitations for one scope, newest first."""
        now = time.time()
        with self._lock:
            values = [copy.deepcopy(item) for item in self._data["scopes"].get(scope, {}).values()]
        live = [item for item in values
                if item.get("stage") == "open" and float(item.get("expires_at") or 0.0) > now]
        return sorted(live, key=lambda item: (float(item.get("created_at") or 0.0), item.get("id", "")),
                      reverse=True)

    def owner(self, invitation_id: str) -> str | None:
        """The scope that issued this invitation, or None if unknown.

        Invitation IDs are random and unique across the store.  A different
        person joining presents a different context and therefore a different
        scope hash, so resolution must not assume the caller's own bucket.
        """
        if not isinstance(invitation_id, str) or not invitation_id:
            return None
        with self._lock:
            for scope, bucket in self._data["scopes"].items():
                if invitation_id in bucket:
                    return scope
        return None

    def consume(self, scope: str, invitation_id: str, *, subject: str) -> dict[str, Any]:
        """Admit one subject.  Idempotent for the same subject, terminal otherwise."""
        with self._lock:
            invitation = self._data["scopes"].get(scope, {}).get(invitation_id)
            if not isinstance(invitation, dict):
                raise NativeTogetherError("native_invitation_not_found")
            joined = invitation.get("joined_by")
            joined = list(joined) if isinstance(joined, list) else []
            if subject in joined:
                # Re-presenting the same invitation by the same subject is a
                # replay, not a second member.
                return copy.deepcopy(invitation)
            if invitation.get("stage") != "open":
                raise NativeTogetherError("native_invitation_not_open")
            if float(invitation.get("expires_at") or 0.0) <= time.time():
                invitation["stage"] = "expired"
                self._save()
                raise NativeTogetherError("native_invitation_expired")
            joined.append(subject)
            invitation["joined_by"] = joined
            # The revision moves once per admitted subject, so in-flight output
            # can be re-validated against the audience it now has.
            invitation["revision"] = int(invitation.get("revision") or 1) + 1
            self._save()
            return copy.deepcopy(invitation)


async def _checked(callback: Callable) -> None:
    if not callable(callback):
        raise NativeTogetherError("host_guard_required")
    result = callback()
    if inspect.isawaitable(result):
        result = await result
    if result is False:
        raise NativeTogetherError("host_authority_revoked")


class NativeTogetherProvider:
    def __init__(self, plugin: Any, data_dir: Path, *, version: str = VERSION,
                 package_hash: str | None = None):
        self.plugin = plugin
        self.version = _text(version, "package_version", 80)
        self.artifact_hash = package_hash or _package_hash()
        self.generation = "together-" + _hash({"version": self.version, "artifact_hash": self.artifact_hash})
        self.store = OperationStore(Path(data_dir) / "native_v2" / "operations.json")
        self.invitations = InvitationStore(Path(data_dir) / "native_v2" / "invitations.json")
        self._room_lock = asyncio.Lock()
        self._closed = False

    def _live(self) -> None:
        if self._closed:
            raise NativeTogetherError("together_provider_stopped")

    def manifest(self) -> dict[str, Any]:
        self._live()
        return {
            "schema": SCHEMA, "plugin_id": PLUGIN_ID, "provider_id": PROVIDER_ID,
            "generation": self.generation,
            "package": {"extension_id": "together", "package_id": PLUGIN_ID,
                         "publisher_id": "menglimi", "package_version": self.version,
                         "artifact_hash": self.artifact_hash,
                         "capabilities": sorted(_ACTIONS), "dataset_owners": [PROVIDER_ID]},
            "tools": [
                {"action": "together.room.snapshot", "capability": "together.room.snapshot",
                 "source_owner": PROVIDER_ID, "description": "读取当前 V2 scope 下的实时共处房间摘要。",
                 "parameters": {"type": "object", "properties": {}, "required": [],
                                "additionalProperties": False}, "side_effect": "none", "timeout_seconds": 10},
                {"action": "together.room.create", "capability": "together.room.create",
                 "source_owner": PROVIDER_ID, "description": "创建带短期凭证的共处房间并返回操作回执。",
                 "parameters": {"type": "object", "properties": {
                     "mode": {"type": "string", "enum": sorted(_MODES)}},
                     "required": ["mode"], "additionalProperties": False},
                 "side_effect": "local", "timeout_seconds": 15},
                {"action": "together.room.invite", "capability": "together.room.invite",
                 "source_owner": PROVIDER_ID,
                 "description": "为当前 V2 scope 下本提供方的房间签发一份有期限的邀请。返回邀请编号与到期时间；邀请本身不授予房间，被邀请人仍需按自己的身份加入。",
                 "parameters": {"type": "object", "properties": {
                     "expires_in_seconds": {"type": "integer"}},
                     "required": [], "additionalProperties": False},
                 "side_effect": "local", "timeout_seconds": 10},
                {"action": "together.room.invitations", "capability": "together.room.invitations",
                 "source_owner": PROVIDER_ID,
                 "description": "列出当前 V2 scope 下仍然有效、尚未被使用完的邀请及其房间、邀请人与到期时间。只读。",
                 "parameters": {"type": "object", "properties": {}, "required": [],
                                "additionalProperties": False}, "side_effect": "none", "timeout_seconds": 10},
                {"action": "together.room.join", "capability": "together.room.join",
                 "source_owner": PROVIDER_ID,
                 "description": "按邀请编号让当前身份加入该邀请指向的房间。同一邀请由同一身份重复提交只算一次；过期或被关闭的邀请会被拒绝，且不会结束房间里其他人正在进行的活动。",
                 "parameters": {"type": "object", "properties": {
                     "invitation_id": {"type": "string"}},
                     "required": ["invitation_id"], "additionalProperties": False},
                 "side_effect": "local", "timeout_seconds": 15},
                {"action": "together.room.members", "capability": "together.room.members",
                 "source_owner": PROVIDER_ID,
                 "description": "读取当前 V2 scope 下本提供方房间的参与成员、各自实际身份与所持执行绑定。只读；不包含提示词原文，也不返回任何凭据。",
                 "parameters": {"type": "object", "properties": {}, "required": [],
                                "additionalProperties": False}, "side_effect": "none", "timeout_seconds": 10},
                {"action": "together.room.close", "capability": "together.room.close",
                 "source_owner": PROVIDER_ID, "description": "关闭当前 V2 scope 下本提供方创建的最近房间。",
                 "parameters": {"type": "object", "properties": {}, "required": [],
                                "additionalProperties": False}, "side_effect": "local", "timeout_seconds": 30},
            ],
        }

    def _request(self, request: dict[str, Any]) -> dict[str, Any]:
        self._live()
        if not isinstance(request, dict) or not _REQUIRED.issubset(request) or request.get("schema") != SCHEMA:
            raise NativeTogetherError("invalid_native_request")
        for key in ("invocation_id", "parent_task_id", "tool_call_id", "origin_key", "arguments_hash", "deadline_at"):
            _text(request.get(key), key, 2048)
        context = request.get("context")
        if not isinstance(context, dict) or not context:
            raise NativeTogetherError("invalid_native_context")
        _user_id(context)
        binding = request.get("binding")
        if not isinstance(binding, dict) or binding != {
            "capability": request.get("action"), "provider": PROVIDER_ID,
            "version": self.version, "generation": self.generation,
        }:
            raise NativeTogetherError("native_provider_binding_mismatch")
        action = request.get("action")
        if action not in _ACTIONS or not isinstance(request.get("arguments"), dict):
            raise NativeTogetherError("unsupported_together_operation")
        arguments = request["arguments"]
        if action in {"together.room.snapshot", "together.room.close", "together.room.members",
                      "together.room.invitations"}:
            if arguments:
                raise NativeTogetherError("invalid_together_arguments")
        elif action == "together.room.invite":
            if set(arguments) - {"expires_in_seconds"}:
                raise NativeTogetherError("invalid_together_arguments")
            if "expires_in_seconds" in arguments:
                window = arguments["expires_in_seconds"]
                if type(window) is not int or not INVITE_MIN_SECONDS <= window <= INVITE_MAX_SECONDS:
                    raise NativeTogetherError("invalid_together_invite_window")
        elif action == "together.room.join":
            if set(arguments) != {"invitation_id"}:
                raise NativeTogetherError("invalid_together_arguments")
            if not isinstance(arguments.get("invitation_id"), str) or not arguments["invitation_id"].strip():
                raise NativeTogetherError("invalid_together_arguments")
            _text(arguments.get("invitation_id"), "invitation_id", 160)
        else:
            if set(arguments) != {"mode"} or arguments.get("mode") not in _MODES:
                raise NativeTogetherError("invalid_together_arguments")
            persona = request.get("host_persona")
            scope = context.get("audience", {}).get("scope")
            if (not isinstance(persona, dict) or set(persona) != {"persona_id", "prompt", "bot_id", "account_id"}
                    or not isinstance(scope, dict) or persona.get("bot_id") != scope.get("bot_id")
                    or persona.get("account_id") != scope.get("account_id")
                    or type(persona.get("prompt")) is not str or len(persona["prompt"]) > 65536):
                raise NativeTogetherError("invalid_host_persona")
            if persona["persona_id"] is not None:
                _text(persona["persona_id"], "host_persona", 160)
            _text(persona["bot_id"], "host_bot", 160)
            _text(persona["account_id"], "host_account", 160)
            execution = request.get("host_execution")
            if (not isinstance(execution, dict) or set(execution) != {
                    "model_provider_id", "tts_provider_id", "tts_language", "tts_timeout_seconds"}
                    or type(execution.get("tts_provider_id")) is not str
                    or len(execution["tts_provider_id"]) > 160
                    or execution.get("tts_language") not in {"zh", "ja", "en"}
                    or type(execution.get("tts_timeout_seconds")) is not int
                    or not 1 <= execution["tts_timeout_seconds"] <= 120):
                raise NativeTogetherError("invalid_host_execution")
            _text(execution["model_provider_id"], "host_model", 160)
            if not isinstance(scope.get("platform"), str) or not scope["platform"]:
                raise NativeTogetherError("invalid_host_scope")
            _text(scope.get("session_id"), "host_session", 160)
            if scope.get("kind") not in {"private", "group"}:
                raise NativeTogetherError("invalid_host_scope")
        return request

    def _response(self, request: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
        succeeded = operation.get("state") == "succeeded"
        failed = operation.get("state") == "failed"
        state = "succeeded" if succeeded else "failed" if failed else "unknown"
        result = copy.deepcopy(operation.get("result")) if succeeded else None
        read_only = request["action"] in _READ_ACTIONS
        if isinstance(result, dict):
            result.pop("closed_ticket_token", None)
        response = {
            "schema": SCHEMA, "invocation_id": request["invocation_id"],
            "origin_key": request["origin_key"], "arguments_hash": request["arguments_hash"],
            "binding": request["binding"], "state": state,
            "effect": "none" if read_only else operation.get("effect") if state != "unknown" else "unknown",
            "operation_id": None if read_only else operation.get("id"),
            "receipt_ref": f"{PROVIDER_ID}.operation:{operation.get('id')}",
            "result": result, "artifacts": [],
        }
        if state != "succeeded" and not read_only:
            # 失败在 V2 里是一个正常返回的回执，外层不应该只看到 HTTP 409 而
            # 不知道是哪一步拒绝的；把原始 operation 错误留在这条日志里。
            logger.warning("[TogetherCompanion] 房间操作未完成 action=%s operation=%s state=%s error=%s",
                           request["action"], operation.get("id"), state, operation.get("error"))
        return response

    def _open_tokens(self, scope: str) -> set[str]:
        closed = {
            item["result"]["closed_ticket_token"]
            for item in self.store.latest(scope, "together.room.close")
            if item.get("state") == "succeeded" and isinstance(item.get("result"), dict)
            and item["result"].get("closed_ticket_token")
        }
        return {
            item["result"]["room"]["ticket_token"]
            for item in self.store.latest(scope, "together.room.create")
            if item.get("state") == "succeeded" and isinstance(item.get("result"), dict)
            and isinstance(item["result"].get("room"), dict)
            and item["result"]["room"].get("ticket_token") not in closed
        }

    def _room_snapshot(self, scope: str, user_id: str) -> dict[str, Any]:
        rooms: list[dict[str, Any]] = []
        tokens = self._open_tokens(scope)
        seen: set[str] = set()
        for room in list(getattr(self.plugin, "rooms", {}).values()) + list(getattr(self.plugin, "detached_rooms", {}).values()):
            room_id = str(getattr(room, "room_id", ""))
            room_scope = getattr(getattr(room, "native_v2", None), "scope_key", None)
            room_members = getattr(room, "members", {})
            member = room_members.get(user_id) if isinstance(room_members, dict) else None
            member_scope = getattr(member, "scope_key", "") if member is not None else ""
            if ((getattr(room, "user_id", "") != user_id and member_scope != scope)
                    or getattr(room, "integration_closed", False)
                    or (getattr(room, "ticket_token", "") not in tokens and room_scope != scope
                        and member_scope != scope)
                    or room_id in seen):
                continue
            seen.add(room_id)
            rooms.append({
                "room_id": room_id,
                "mode": str(getattr(room, "mode", "")),
                "created_at": float(getattr(room, "created_at", 0.0) or 0.0),
                "call_active": bool(getattr(room, "call_active", False)),
                "resumable": bool(getattr(room, "resume_token", "")),
            })
        return {"scope_key": scope, "rooms": rooms, "active_count": len(rooms)}

    def _room_members(self, scope: str, user_id: str) -> dict[str, Any]:
        """Report who is in this scope's rooms, one entry per bound identity.

        Only identity facts are exposed.  The persona prompt, tickets and any
        credential stay inside the plugin; each member's own binding is reduced
        to the execution profile that member's room actually runs with.
        """
        rows: list[dict[str, Any]] = []
        # RoomSession is an unhashable dataclass, so "is this one detached"
        # must be answered by identity over the list, never set membership.
        detached = list(getattr(self.plugin, "detached_rooms", {}).values())
        seen: set[str] = set()
        for room in list(getattr(self.plugin, "rooms", {}).values()) + detached:
            room_scope = getattr(getattr(room, "native_v2", None), "scope_key", None)
            if getattr(room, "integration_closed", False):
                continue
            room_id = str(getattr(room, "room_id", ""))
            if room_id in seen:
                continue
            seen.add(room_id)
            room_members = getattr(room, "members", {})
            room_members = room_members if isinstance(room_members, dict) else {}
            caller_member = room_members.get(user_id)
            caller_scope = getattr(caller_member, "scope_key", "") if caller_member is not None else ""
            if room_scope != scope and getattr(room, "user_id", "") != user_id and caller_scope != scope:
                continue
            same_scope = room_scope == scope
            binding = getattr(room, "native_v2", None)
            state_members = list(room_members.values()) or [None]
            for member in state_members:
                subject = str(getattr(member, "user_id", "") or getattr(room, "user_id", ""))
                if member is None and not subject:
                    continue
                rows.append({
                    "room_id": room_id,
                    "subject": subject,
                    "role": str(getattr(member, "role", "owner") or "owner"),
                    "display_name": str(getattr(member, "display_name", "") or ""),
                    "connected": (getattr(member, "websocket", None) is not None
                                  and not bool(getattr(getattr(member, "websocket", None), "closed", False))),
                    "mode": str(getattr(room, "mode", "")),
                    "detached": ((member is not None and getattr(member, "websocket", None) is None)
                                 or (member is None and any(room is item for item in detached))),
                    "persona_id": getattr(binding, "persona_id", None) if same_scope else None,
                    "scope_kind": str(getattr(binding, "scope_kind", "") or "") if same_scope else "",
                    "model_provider_id": str(getattr(binding, "model_provider_id", "") or "")
                    if same_scope else "",
                    "tts_language": str(getattr(binding, "tts_language", "") or "") if same_scope else "",
                })
        rows.sort(key=lambda item: (item["room_id"], item["subject"]))
        # The scope key is an internal context bucket; it is deliberately not
        # echoed back to the model.
        return {"members": rows, "member_count": len(rows)}

    def _room_invitations(self, scope: str) -> dict[str, Any]:
        """Open invitations for this scope, without the invitation secret."""
        live_room_ids = {
            str(getattr(room, "room_id", ""))
            for room in list(getattr(self.plugin, "rooms", {}).values())
            + list(getattr(self.plugin, "detached_rooms", {}).values())
            if not getattr(room, "integration_closed", False)
        }
        rows = [{"invitation_id": item["id"], "room_id": item["room_id"],
                 "room_mode": item["room_mode"], "revision": item["revision"],
                 "stage": item["stage"], "expires_at": item["expires_at"],
                 "inviter": item["inviter"],
                 "joined_count": len(item.get("joined_by") or [])}
                for item in self.invitations.active(scope) if item.get("room_id") in live_room_ids]
        return {"invitations": rows, "invitation_count": len(rows)}

    def _current_rooms(self, scope: str, user_id: str) -> list[Any]:
        rooms, seen = [], set()
        for room in list(getattr(self.plugin, "rooms", {}).values()) + list(
                getattr(self.plugin, "detached_rooms", {}).values()):
            room_id = str(getattr(room, "room_id", ""))
            room_scope = getattr(getattr(room, "native_v2", None), "scope_key", None)
            if (room_id in seen or getattr(room, "integration_closed", False)
                    or (room_scope != scope and getattr(room, "user_id", "") != user_id)):
                continue
            seen.add(room_id)
            rooms.append(room)
        return rooms

    async def _invite(self, value: dict[str, Any], scope: str, operation: dict[str, Any],
                      before_dispatch) -> dict[str, Any]:
        """Issue an invitation for this scope's most recent room."""
        try:
            await _checked(before_dispatch)
            user_id = _user_id(value["context"])
        except Exception as exc:
            code = exc.code if isinstance(exc, NativeTogetherError) else "together_authority_check_failed"
            operation = self.store.update(scope, operation["id"], state="failed",
                                         effect="not_committed", error=code)
            return self._response(value, operation)
        try:
            rooms = self._current_rooms(scope, user_id)
            if not rooms:
                raise NativeTogetherError("together_no_room_to_invite")
            room = max(rooms, key=lambda item: (float(getattr(item, "created_at", 0) or 0),
                                                str(getattr(item, "room_id", ""))))
            if str(getattr(room, "mode", "")) not in {"call", "watch"}:
                raise NativeTogetherError("together_room_mode_not_shareable")
            room_scope = getattr(getattr(room, "native_v2", None), "scope_key", scope)
            if room_scope != scope:
                raise NativeTogetherError("together_room_invite_authority_mismatch")
            prepare = getattr(self.plugin, "prepare_room_for_invitations", None)
            if callable(prepare):
                prepare(room)
            window = int(value["arguments"].get("expires_in_seconds") or INVITE_DEFAULT_SECONDS)
            invitation = self.invitations.issue(
                scope, inviter=user_id, room_id=str(getattr(room, "room_id", "")),
                room_mode=str(getattr(room, "mode", "")), expires_at=time.time() + window,
                revision=int(getattr(room, "member_revision", 1) or 1))
            result = {"invitation": {"invitation_id": invitation["id"],
                                     "room_id": invitation["room_id"],
                                     "room_mode": invitation["room_mode"],
                                     "revision": invitation["revision"],
                                     "expires_at": invitation["expires_at"]}}
            operation = self.store.update(scope, operation["id"], state="succeeded",
                                          effect="committed", result=result)
        except asyncio.CancelledError:
            operation = self.store.update(scope, operation["id"], state="unknown", effect="unknown",
                                          error="execution_interrupted")
            raise
        except Exception as exc:
            code = exc.code if isinstance(exc, NativeTogetherError) else "together_invite_failed"
            operation = self.store.update(scope, operation["id"], state="failed",
                                          effect="not_committed", error=code)
        return self._response(value, operation)

    async def _join(self, value: dict[str, Any], scope: str, operation: dict[str, Any],
                    before_dispatch) -> dict[str, Any]:
        """Admit the current subject to the room an invitation points at.

        Joining never closes or replaces the room: other members keep their
        activity, and a rejected or expired invitation ends only that invitation.
        """
        try:
            await _checked(before_dispatch)
            user_id = _user_id(value["context"])
        except Exception as exc:
            code = exc.code if isinstance(exc, NativeTogetherError) else "together_authority_check_failed"
            operation = self.store.update(scope, operation["id"], state="failed",
                                         effect="not_committed", error=code)
            return self._response(value, operation)
        accepted = False
        try:
            invitation_id = value["arguments"]["invitation_id"]
            # An invitation is resolved by its own identity, not by assuming it
            # lives in the caller's context bucket: a different person joining
            # brings a different context and so a different scope hash.
            owner = self.invitations.owner(invitation_id)
            if owner is None:
                raise NativeTogetherError("native_invitation_not_found")
            # Read first so an unknown or non-open invitation is refused before
            # anything is written.
            current = self.invitations.get(owner, invitation_id)
            if current.get("stage") != "open" and user_id not in (current.get("joined_by") or []):
                raise NativeTogetherError("native_invitation_not_open")
            room = next((item for item in self._current_rooms(owner, user_id)
                         if str(getattr(item, "room_id", "")) == current.get("room_id")), None)
            if room is None or getattr(room, "integration_closed", False):
                raise NativeTogetherError("together_invited_room_unavailable")
            if str(getattr(room, "mode", "")) not in {"call", "watch"}:
                raise NativeTogetherError("together_room_mode_not_shareable")
            if user_id == getattr(room, "user_id", ""):
                raise NativeTogetherError("together_room_owner_already_member")
            room_members = getattr(room, "members", {})
            room_members = room_members if isinstance(room_members, dict) else {}
            member = room_members.get(user_id)
            if member is not None and member.websocket is not None and not bool(
                    getattr(member.websocket, "closed", False)):
                result = {"join": {"invitation_id": current["id"], "room_id": current["room_id"],
                                   "room_mode": current["room_mode"], "subject": user_id,
                                   "replayed": True, "already_in_room": True,
                                   "member_revision": int(getattr(room, "member_revision", 1) or 1),
                                   "member_revision_changed": False}}
                operation = self.store.update(scope, operation["id"], state="succeeded",
                                              effect="committed", result=result)
                return self._response(value, operation)
            joined = self.invitations.consume(owner, invitation_id, subject=user_id)
            accepted = True
            replays = len(joined.get("joined_by") or []) - len(current.get("joined_by") or [])
            admit = getattr(self.plugin, "admit_room_member", None)
            if not callable(admit):
                raise NativeTogetherError("together_member_admission_unavailable")
            member_was_known = user_id in room_members
            admit(room, user_id, scope_key=scope)
            bumped = False
            if replays > 0 and not member_was_known:
                # One revision per admitted subject, so in-flight output can be
                # re-validated against the audience the room now has.
                room.member_revision = int(getattr(room, "member_revision", 1) or 1) + 1
                bumped = True
            issue_ticket = getattr(self.plugin, "issue_member_room_ticket", None)
            ticket_url = getattr(self.plugin, "_ticket_url", None)
            if not callable(issue_ticket) or not callable(ticket_url):
                raise NativeTogetherError("together_member_ticket_unavailable")
            ticket = issue_ticket(room, user_id=user_id, invitation_id=invitation_id)
            result = {"join": {"invitation_id": joined["id"], "room_id": joined["room_id"],
                               "room_mode": joined["room_mode"], "subject": user_id,
                               "replayed": replays <= 0, "already_in_room": False,
                               "invitation_revision": joined["revision"],
                               "member_revision": int(getattr(room, "member_revision", 1) or 1),
                               "member_revision_changed": bumped,
                               "join_url": ticket_url(ticket),
                               "expires_at": ticket.expires_at}}
            operation = self.store.update(scope, operation["id"], state="succeeded",
                                          effect="committed", result=result)
        except asyncio.CancelledError:
            operation = self.store.update(scope, operation["id"], state="unknown", effect="unknown",
                                          error="execution_interrupted")
            raise
        except Exception as exc:
            code = exc.code if isinstance(exc, NativeTogetherError) else "together_join_failed"
            operation = self.store.update(
                scope, operation["id"], state="unknown" if accepted else "failed",
                effect="unknown" if accepted else "not_committed", error=code)
        return self._response(value, operation)

    async def execute(self, request: dict[str, Any], *, before_dispatch) -> dict[str, Any]:
        value = self._request(request)
        await _checked(before_dispatch)
        scope = scope_key(value["context"])
        operation, fresh = self.store.begin(scope, value)
        if not fresh:
            return self._response(value, operation)
        action = value["action"]
        if action in _READ_ACTIONS:
            try:
                if action == "together.room.snapshot":
                    result = self._room_snapshot(scope, _user_id(value["context"]))
                elif action == "together.room.invitations":
                    result = self._room_invitations(scope)
                else:
                    result = self._room_members(scope, _user_id(value["context"]))
                await _checked(before_dispatch)
                operation = self.store.update(scope, operation["id"], state="succeeded", effect="none", result=result)
            except Exception as exc:
                code = exc.code if isinstance(exc, NativeTogetherError) else "together_read_failed"
                operation = self.store.update(scope, operation["id"], state="failed", effect="not_committed", error=code)
            return self._response(value, operation)
        if action == "together.room.join":
            return await self._join(value, scope, operation, before_dispatch)
        if action == "together.room.invite":
            return await self._invite(value, scope, operation, before_dispatch)
        try:
            await _checked(before_dispatch)
        except Exception as exc:
            code = exc.code if isinstance(exc, NativeTogetherError) else "together_authority_check_failed"
            operation = self.store.update(scope, operation["id"], state="failed", effect="not_committed", error=code)
            return self._response(value, operation)
        try:
            user_id = _user_id(value["context"])
            if action == "together.room.create":
                operation = await self._create_room(value, scope, operation, user_id=user_id)
            else:
                async with self._room_lock:
                    try:
                        await _checked(before_dispatch)
                    except Exception as exc:
                        code = exc.code if isinstance(exc, NativeTogetherError) else "together_authority_check_failed"
                        operation = self.store.update(scope, operation["id"], state="failed",
                                                      effect="not_committed", error=code)
                        return self._response(value, operation)
                    self.store.update(scope, operation["id"], state="dispatching", effect="unknown")
                    result = await self._close_latest(scope, user_id)
                    operation = self.store.update(scope, operation["id"], state="succeeded", effect="committed", result=result)
        except asyncio.CancelledError:
            operation = self.store.update(scope, operation["id"], state="unknown", effect="unknown", error="execution_interrupted")
            raise
        except Exception as exc:
            code = exc.code if isinstance(exc, NativeTogetherError) else "together_room_result_unconfirmed"
            dispatched = self.store.get(scope, operation["id"])["state"] == "dispatching"
            operation = self.store.update(scope, operation["id"], state="unknown" if dispatched else "failed",
                                          effect="unknown" if dispatched else "not_committed", error=code)
        return self._response(value, operation)

    async def _create_room(self, value, scope, operation, *, user_id):
        """Open one short-lived room and bind it to this V2 scope.

        旧版插件的工具路径会先准备手机公网访问（必要时自动起隧道）；V2 这里原先
        直接拼 ``_ticket_url``，没配公网地址时链接就是 ``http://127.0.0.1:6321/...``，
        手机上根本打不开。这里补回那一步，并把公网访问状态一并返回。
        """
        if not bool(getattr(getattr(self.plugin, "room_server", None), "running", False)):
            raise NativeTogetherError("together_room_server_unavailable")
        native_scope = value["context"]["audience"]["scope"]
        # 房间链接是短期凭证，群里公开出去等于把麦克风交给整个群。宿主侧没有可靠的
        # 私发通道时宁可拒绝，也不让模型在群里贴链接。
        if native_scope["kind"] == "group":
            raise NativeTogetherError("together_private_room_required")
        execution = value["host_execution"]
        model = self.plugin._get_provider_by_id(execution["model_provider_id"])
        if not callable(getattr(model, "text_chat", None)):
            raise NativeTogetherError("together_v2_model_unavailable")
        access = {"tunnel_started": False, "tunnel_ready": False, "fixed_public_url": False}
        prepare = getattr(self.plugin, "_ensure_mobile_room_access", None)
        if callable(prepare):
            from .mobile.origin import MOBILE_ROOM_CALL
            # 手机网关建房已经由网关自己准备好了房间访问（代理模式直接走
            # local_base_url）。这里再无条件准备一次会重新尝试公网隧道，在没配
            # 公网地址的机器上直接失败成 together_public_access_unavailable。
            via_mobile_gateway = bool(MOBILE_ROOM_CALL.get())
            try:
                try:
                    access = await prepare(via_mobile_gateway=via_mobile_gateway)
                except TypeError as exc:
                    # Older host APIs exposed this helper without the keyword;
                    # only retry when the call itself rejected the keyword.
                    message = str(exc).lower()
                    if "via_mobile_gateway" not in message and "unexpected keyword" not in message:
                        raise
                    access = await prepare()
            except Exception as exc:
                logger.warning(
                    "[TogetherCompanion] 建房准备房间访问失败 via_mobile_gateway=%s error_type=%s",
                    via_mobile_gateway, type(exc).__name__)
                raise NativeTogetherError("together_public_access_unavailable") from None
        self.store.update(scope, operation["id"], state="dispatching", effect="unknown")
        persona = value["host_persona"]
        ticket = self.plugin.issue_room_ticket(
            mode=value["arguments"]["mode"], user_id=user_id,
            native_v2={"scope_key": scope, "bot_id": persona["bot_id"],
                       "account_id": persona["account_id"], "persona_id": persona["persona_id"],
                       "prompt": persona["prompt"],
                       "model_provider_id": execution["model_provider_id"],
                       "tts_provider_id": execution["tts_provider_id"],
                       "tts_language": execution["tts_language"],
                       "tts_timeout_seconds": execution["tts_timeout_seconds"],
                       "platform": native_scope["platform"],
                       "session_id": native_scope["session_id"],
                       "scope_kind": native_scope["kind"]})
        result = {"room": {"mode": ticket.mode, "ticket_token": ticket.token,
                           "url": self.plugin._ticket_url(ticket), "created_at": ticket.created_at,
                           "expires_at": ticket.expires_at, "user_id": user_id,
                           "bot_id": persona["bot_id"], "persona_id": persona["persona_id"]},
                  "access": {name: bool(access.get(name)) for name in
                             ("tunnel_started", "tunnel_ready", "fixed_public_url")}}
        return self.store.update(scope, operation["id"], state="succeeded", effect="committed", result=result)

    async def _close_latest(self, scope: str, user_id: str) -> dict[str, Any]:
        bound_rooms = {
            str(getattr(room, "room_id", "")): room
            for room in list(getattr(self.plugin, "rooms", {}).values())
            + list(getattr(self.plugin, "detached_rooms", {}).values())
            if getattr(room, "user_id", "") == user_id
            and not getattr(room, "integration_closed", False)
            and getattr(getattr(room, "native_v2", None), "scope_key", None) == scope
        }
        if bound_rooms:
            room = max(bound_rooms.values(), key=lambda item: (float(getattr(item, "created_at", 0) or 0),
                                                               str(getattr(item, "room_id", ""))))
            await self.plugin.close_room(room)
            return {"closed": True, "room_id": str(room.room_id), "ticket_revoked": True,
                    "closed_ticket_token": str(room.ticket_token)}
        creates = [item for item in self.store.latest(scope, "together.room.create")
                   if item.get("state") == "succeeded" and isinstance(item.get("result"), dict)
                   and (item["result"].get("room") or {}).get("ticket_token") in self._open_tokens(scope)]
        if not creates:
            return {"closed": False, "reason": "no_provider_room"}
        room_data = creates[0]["result"].get("room") or {}
        token = str(room_data.get("ticket_token") or "")
        if not token:
            return {"closed": False, "reason": "no_provider_room"}
        for room in list(getattr(self.plugin, "rooms", {}).values()) + list(getattr(self.plugin, "detached_rooms", {}).values()):
            if getattr(room, "ticket_token", "") == token and getattr(room, "user_id", "") == user_id:
                await self.plugin.close_room(room)
                return {"closed": True, "room_id": str(getattr(room, "room_id", "")),
                        "ticket_revoked": True, "closed_ticket_token": token}
        store = getattr(self.plugin, "ticket_store", None)
        revoke = getattr(store, "revoke", None)
        if callable(revoke):
            revoke(token)
        return {"closed": False, "ticket_revoked": True, "reason": "room_not_active",
                "closed_ticket_token": token}

    def _same_request(self, request: dict[str, Any], operation: dict[str, Any]) -> None:
        if _operation_identity(request) != operation.get("identity"):
            raise NativeTogetherError("native_original_invocation_mismatch")

    async def lookup(self, request: dict[str, Any], *, before_dispatch) -> dict[str, Any]:
        value = self._request(request)
        await _checked(before_dispatch)
        scope = scope_key(value["context"])
        operation_id = value.get("operation_id")
        if isinstance(operation_id, str) and operation_id:
            operation = self.store.get(scope, operation_id)
        else:
            candidates = [item for item in self.store.latest(scope, value["action"])
                          if item.get("invocation_id") == value["invocation_id"]]
            if not candidates:
                raise NativeTogetherError("native_operation_not_found")
            operation = candidates[0]
        self._same_request(value, operation)
        return self._response(value, operation)

    async def close(self) -> None:
        self._closed = True
