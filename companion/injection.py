"""Small, dependency-free injection protocol for companion extensions.

The host owns lifecycle, scope and prompt placement. Feature plugins own their
domain data and submit immutable DTOs through the public extension facade.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime
import hashlib
import json
import re
import threading
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Protocol

PROTOCOL_VERSION = "0.1"
CAPABILITY_KINDS = frozenset({"observe", "enrich", "event", "remember", "propose", "execute"})
EXTENSION_STATES = frozenset({"discovered", "validated", "bound", "ready", "degraded", "stopped", "failed"})
CAPABILITY_STATES = EXTENSION_STATES | frozenset({"unavailable", "permission_denied"})
ACTION_STATUSES = frozenset({"succeeded", "rejected", "permission_denied", "unavailable", "timeout", "failed", "cancelled"})
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,127}$")
_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class InjectionProtocolError(ValueError):
    """Malformed DTO or invalid registry operation."""


class CapabilityConflictError(InjectionProtocolError):
    """A capability key is already owned by another provider."""


def _text(value: Any, *, name: str, required: bool = False, limit: int = 256) -> str:
    result = str(value).strip() if value is not None else ""
    if required and not result:
        raise InjectionProtocolError(f"{name} is required")
    if len(result) > limit:
        raise InjectionProtocolError(f"{name} exceeds {limit} characters")
    return result


def _id(value: Any, *, name: str) -> str:
    result = _text(value, name=name, required=True, limit=128).lower()
    if not _ID_RE.fullmatch(result):
        raise InjectionProtocolError(f"{name} contains invalid characters")
    return result


def _version(value: Any, *, name: str = "version") -> str:
    result = _text(value, name=name, required=True, limit=32)
    if not _VERSION_RE.fullmatch(result):
        raise InjectionProtocolError(f"{name} must use major.minor format")
    return result


def _mapping(value: Any, *, name: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise InjectionProtocolError(f"{name} must be a mapping")
    return value


def _nonnegative(value: Any, *, name: str) -> int:
    if isinstance(value, bool):
        raise InjectionProtocolError(f"{name} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise InjectionProtocolError(f"{name} must be an integer") from exc
    if result < 0:
        raise InjectionProtocolError(f"{name} cannot be negative")
    return result


def _texts(values: Iterable[Any] | None, *, name: str, limit: int = 64) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        values = (values,)
    if not isinstance(values, Iterable):
        raise InjectionProtocolError(f"{name} must be iterable")
    result: list[str] = []
    for value in values:
        item = _text(value, name=name, limit=256)
        if item and item not in result:
            result.append(item)
        if len(result) >= limit:
            break
    return tuple(result)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(_freeze(item) for item in value)
    return value


def _json(value: Any) -> Any:
    if is_dataclass(value):
        return {item.name: _json(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _construct(cls: Any, payload: Mapping[str, Any], *, name: str) -> Any:
    try:
        return cls(**dict(payload))
    except InjectionProtocolError:
        raise
    except (TypeError, ValueError, OverflowError) as exc:
        raise InjectionProtocolError(f"invalid {name}") from exc


@dataclass(frozen=True, slots=True)
class Scope:
    platform: str = ""
    account_id: str = ""
    conversation_id: str = ""
    user_id: str = ""
    group_id: str = ""
    persona_id: str = ""
    installation_id: str = ""
    bot_id: str = ""
    session_id: str = ""
    persona_binding_revision: int = 0

    def __post_init__(self) -> None:
        for name in ("platform", "account_id", "conversation_id", "user_id", "group_id", "persona_id", "installation_id", "bot_id", "session_id"):
            object.__setattr__(self, name, _text(getattr(self, name), name=name, limit=256))
        if not any((self.account_id, self.conversation_id, self.user_id, self.group_id, self.persona_id)):
            raise InjectionProtocolError("scope needs at least one identity field")
        object.__setattr__(self, "persona_binding_revision", _nonnegative(self.persona_binding_revision, name="persona_binding_revision"))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Scope":
        payload = _mapping(value, name="scope")
        names = {item.name for item in fields(cls)}
        return _construct(cls, {name: payload[name] for name in names if name in payload}, name="scope")

    @property
    def scope_key(self) -> str:
        encoded = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "scope:v1:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]

    def to_dict(self) -> dict[str, Any]:
        return _json(self)


@dataclass(frozen=True, slots=True)
class RuntimeScope:
    installation_id: str
    bot_id: str
    platform: str
    account_id: str
    persona_id: str
    conversation_id: str = ""
    session_id: str = ""
    user_id: str = ""
    group_id: str = ""
    persona_binding_revision: int = 0

    def __post_init__(self) -> None:
        for name in ("installation_id", "bot_id", "platform", "account_id", "persona_id", "conversation_id", "session_id", "user_id", "group_id"):
            object.__setattr__(self, name, _text(getattr(self, name), name=name, required=name in {"installation_id", "bot_id", "platform", "account_id", "persona_id"}, limit=256))
        object.__setattr__(self, "persona_binding_revision", _nonnegative(self.persona_binding_revision, name="persona_binding_revision"))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RuntimeScope":
        payload = _mapping(value, name="runtime_scope")
        missing = [name for name in ("installation_id", "bot_id", "platform", "account_id", "persona_id") if not str(payload.get(name) or "").strip()]
        if missing:
            raise InjectionProtocolError("runtime_scope missing: " + ",".join(missing))
        names = {item.name for item in fields(cls)}
        return _construct(cls, {name: payload[name] for name in names if name in payload}, name="runtime_scope")

    @property
    def scope_key(self) -> str:
        return self.to_scope().scope_key

    def to_scope(self) -> Scope:
        return Scope(**self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return _json(self)


@dataclass(frozen=True, slots=True)
class Evidence:
    source: str
    evidence_kind: str
    observed_at: str = ""
    expires_at: str = ""
    confidence: float = 1.0
    sensitivity: str = "private"

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", _text(self.source, name="source", required=True))
        object.__setattr__(self, "evidence_kind", _id(self.evidence_kind, name="evidence_kind"))
        for name in ("observed_at", "expires_at"):
            object.__setattr__(self, name, _text(getattr(self, name), name=name, limit=64))
        try:
            confidence = float(self.confidence)
        except (TypeError, ValueError, OverflowError) as exc:
            raise InjectionProtocolError("confidence must be a number") from exc
        if not 0 <= confidence <= 1:
            raise InjectionProtocolError("confidence must be between 0 and 1")
        object.__setattr__(self, "confidence", confidence)
        sensitivity = _id(self.sensitivity, name="sensitivity")
        if sensitivity not in {"public", "private", "sensitive", "restricted"}:
            raise InjectionProtocolError("invalid sensitivity")
        object.__setattr__(self, "sensitivity", sensitivity)

    def to_dict(self) -> dict[str, Any]:
        return _json(self)


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    id: str
    kind: str
    version: str
    provider: str
    scopes: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    input_schema: str = ""
    output_schema: str = ""
    requires: tuple[str, ...] = ()
    side_effect: str = "none"
    confirmation: str = "policy_decides"
    lifecycle: str = "on_demand"
    resource_budget: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _id(self.id, name="id"))
        kind = _id(self.kind, name="kind")
        if kind not in CAPABILITY_KINDS:
            raise InjectionProtocolError(f"unsupported capability kind: {kind}")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "version", _version(self.version))
        object.__setattr__(self, "provider", _id(self.provider, name="provider"))
        object.__setattr__(self, "scopes", _texts(self.scopes, name="scopes"))
        object.__setattr__(self, "permissions", _texts(self.permissions, name="permissions"))
        object.__setattr__(self, "requires", _texts(self.requires, name="requires"))
        object.__setattr__(self, "input_schema", _text(self.input_schema, name="input_schema", limit=256))
        object.__setattr__(self, "output_schema", _text(self.output_schema, name="output_schema", limit=256))
        if self.side_effect not in {"none", "local", "external_device", "external_network", "message_delivery"}:
            raise InjectionProtocolError("invalid side_effect")
        if self.confirmation not in {"never", "policy_decides", "user_required"}:
            raise InjectionProtocolError("invalid confirmation")
        if self.lifecycle not in {"always_on", "on_demand", "session_bound"}:
            raise InjectionProtocolError("invalid lifecycle")
        object.__setattr__(self, "resource_budget", _freeze(dict(_mapping(self.resource_budget, name="resource_budget"))))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CapabilityDescriptor":
        payload = _mapping(value, name="capability")
        names = {item.name for item in fields(cls)}
        return _construct(cls, {name: payload[name] for name in names if name in payload}, name="capability")

    @property
    def key(self) -> str:
        return f"{self.id}@{self.version}"

    def to_dict(self) -> dict[str, Any]:
        return _json(self)


@dataclass(frozen=True, slots=True)
class ExtensionManifest:
    id: str
    version: str
    sdk_version: str
    display_name: str = ""
    capabilities: tuple[CapabilityDescriptor, ...] = ()
    requires: tuple[str, ...] = ()
    optional_requires: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    ui_modules: tuple[str, ...] = ()
    supported_platforms: tuple[str, ...] = ()
    resource_budget: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _id(self.id, name="id"))
        object.__setattr__(self, "version", _version(self.version))
        object.__setattr__(self, "sdk_version", _version(self.sdk_version, name="sdk_version"))
        object.__setattr__(self, "display_name", _text(self.display_name, name="display_name", limit=256))
        capabilities = tuple(self.capabilities or ())
        if any(not isinstance(item, CapabilityDescriptor) for item in capabilities):
            raise InjectionProtocolError("capabilities must contain CapabilityDescriptor values")
        object.__setattr__(self, "capabilities", capabilities)
        for name in ("requires", "optional_requires", "permissions", "ui_modules", "supported_platforms"):
            object.__setattr__(self, name, _texts(getattr(self, name), name=name))
        object.__setattr__(self, "resource_budget", _freeze(dict(_mapping(self.resource_budget, name="resource_budget"))))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExtensionManifest":
        payload = _mapping(value, name="manifest")
        raw = payload.get("capabilities") or ()
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Iterable):
            raise InjectionProtocolError("manifest capabilities must be iterable")
        capabilities = tuple(item if isinstance(item, CapabilityDescriptor) else CapabilityDescriptor.from_dict(item) for item in raw)
        names = {item.name for item in fields(cls)} - {"capabilities"}
        values = {name: payload[name] for name in names if name in payload}
        values["capabilities"] = capabilities
        return _construct(cls, values, name="manifest")

    def to_dict(self) -> dict[str, Any]:
        return _json(self)


@dataclass(frozen=True, slots=True)
class ExtensionStatus:
    id: str
    state: str = "discovered"
    reason: str = ""
    missing_requirements: tuple[str, ...] = ()
    capability_states: Mapping[str, str] = field(default_factory=dict)
    task_count: int = 0
    last_error: str = ""
    started_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _id(self.id, name="id"))
        if self.state not in EXTENSION_STATES:
            raise InjectionProtocolError(f"unsupported extension state: {self.state}")
        object.__setattr__(self, "reason", _text(self.reason, name="reason", limit=1000))
        object.__setattr__(self, "missing_requirements", _texts(self.missing_requirements, name="missing_requirements"))
        states: dict[str, str] = {}
        for key, value in _mapping(self.capability_states, name="capability_states").items():
            normalized = _id(key, name="capability state id")
            if value not in CAPABILITY_STATES:
                raise InjectionProtocolError(f"unsupported capability state: {value}")
            states[normalized] = value
        object.__setattr__(self, "capability_states", _freeze(states))
        object.__setattr__(self, "task_count", _nonnegative(self.task_count, name="task_count"))
        object.__setattr__(self, "last_error", _text(self.last_error, name="last_error", limit=2000))
        object.__setattr__(self, "started_at", _text(self.started_at, name="started_at", limit=64))
        object.__setattr__(self, "updated_at", _text(self.updated_at, name="updated_at", limit=64))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExtensionStatus":
        payload = _mapping(value, name="extension_status")
        names = {item.name for item in fields(cls)}
        return _construct(cls, {name: payload[name] for name in names if name in payload}, name="extension_status")

    def to_dict(self) -> dict[str, Any]:
        return _json(self)


@dataclass(frozen=True, slots=True)
class Observation:
    type: str
    scope: Scope
    value: Any
    evidence: Evidence
    revision: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "type", _id(self.type, name="type"))
        if not isinstance(self.scope, (Scope, RuntimeScope)) or not isinstance(self.evidence, Evidence):
            raise InjectionProtocolError("observation scope/evidence is invalid")
        object.__setattr__(self, "value", _freeze(self.value))
        object.__setattr__(self, "revision", _text(self.revision, name="revision", limit=128))

    def to_dict(self) -> dict[str, Any]:
        return _json(self)


@dataclass(frozen=True, slots=True)
class CompanionEvent:
    id: str
    type: str
    scope: Scope
    occurred_at: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    dedupe_key: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, name="id", required=True, limit=128))
        object.__setattr__(self, "type", _id(self.type, name="type"))
        if not isinstance(self.scope, (Scope, RuntimeScope)):
            raise InjectionProtocolError("event scope is invalid")
        object.__setattr__(self, "occurred_at", _text(self.occurred_at, name="occurred_at", required=True, limit=64))
        object.__setattr__(self, "payload", _freeze(dict(_mapping(self.payload, name="payload"))))
        object.__setattr__(self, "dedupe_key", _text(self.dedupe_key, name="dedupe_key", limit=256))

    def to_dict(self) -> dict[str, Any]:
        return _json(self)


@dataclass(frozen=True, slots=True)
class ContextContribution:
    lane: str
    key: str
    content: str
    evidence: str
    priority: int = 0
    max_age_seconds: int = 1800
    visibility: str = "private"
    scope: Scope | RuntimeScope | None = None
    source: str = ""
    source_refs: tuple[str, ...] = ()
    revision: str = ""
    trace_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "lane", _id(self.lane, name="lane"))
        object.__setattr__(self, "key", _id(self.key, name="key"))
        object.__setattr__(self, "content", _text(self.content, name="content", required=True, limit=4000))
        object.__setattr__(self, "evidence", _id(self.evidence, name="evidence"))
        object.__setattr__(self, "priority", int(self.priority))
        object.__setattr__(self, "max_age_seconds", _nonnegative(self.max_age_seconds, name="max_age_seconds"))
        if self.visibility not in {"public", "private", "restricted"}:
            raise InjectionProtocolError("invalid visibility")
        if self.scope is not None and not isinstance(self.scope, (Scope, RuntimeScope)):
            raise InjectionProtocolError("contribution scope is invalid")
        source = _text(self.source, name="source", limit=128)
        object.__setattr__(self, "source", _id(source, name="source") if source else "")
        object.__setattr__(self, "source_refs", _texts(self.source_refs, name="source_refs", limit=32))
        object.__setattr__(self, "revision", _text(self.revision, name="revision", limit=128))
        object.__setattr__(self, "trace_id", _text(self.trace_id, name="trace_id", limit=128))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ContextContribution":
        payload = _mapping(value, name="context_contribution")
        scope = payload.get("scope")
        if isinstance(scope, Mapping):
            required = {"installation_id", "bot_id", "platform", "account_id", "persona_id"}
            scope = RuntimeScope.from_dict(scope) if required.issubset(scope) else Scope.from_dict(scope)
        names = {item.name for item in fields(cls)}
        values = {name: payload[name] for name in names if name in payload}
        if scope is not None:
            values["scope"] = scope
        return _construct(cls, values, name="context_contribution")

    def to_dict(self) -> dict[str, Any]:
        return _json(self)


@dataclass(frozen=True, slots=True)
class ProactiveCandidate:
    id: str
    trigger: str
    scope: Scope
    intent: str
    context_keys: tuple[str, ...] = ()
    expires_at: str = ""
    cooldown_key: str = ""
    requires_confirmation: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, name="id", required=True, limit=128))
        object.__setattr__(self, "trigger", _id(self.trigger, name="trigger"))
        if not isinstance(self.scope, (Scope, RuntimeScope)):
            raise InjectionProtocolError("candidate scope is invalid")
        object.__setattr__(self, "intent", _text(self.intent, name="intent", required=True, limit=1000))
        object.__setattr__(self, "context_keys", _texts(self.context_keys, name="context_keys"))
        object.__setattr__(self, "expires_at", _text(self.expires_at, name="expires_at", limit=64))
        object.__setattr__(self, "cooldown_key", _text(self.cooldown_key, name="cooldown_key", limit=256))

    def to_dict(self) -> dict[str, Any]:
        return _json(self)


@dataclass(frozen=True, slots=True)
class ActionRequest:
    action: str
    scope: Scope
    arguments: Mapping[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""
    requested_by: str = "system"
    trace_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", _id(self.action, name="action"))
        if not isinstance(self.scope, (Scope, RuntimeScope)):
            raise InjectionProtocolError("action scope is invalid")
        object.__setattr__(self, "arguments", _freeze(dict(_mapping(self.arguments, name="arguments"))))
        object.__setattr__(self, "idempotency_key", _text(self.idempotency_key, name="idempotency_key", limit=256))
        object.__setattr__(self, "requested_by", _text(self.requested_by, name="requested_by", required=True, limit=128))
        object.__setattr__(self, "trace_id", _text(self.trace_id, name="trace_id", limit=128))

    def to_dict(self) -> dict[str, Any]:
        return _json(self)


@dataclass(frozen=True, slots=True)
class ActionResult:
    status: str
    action: str
    receipt: Mapping[str, Any] = field(default_factory=dict)
    error_code: str = ""
    message: str = ""
    completed_at: str = ""
    trace_id: str = ""

    def __post_init__(self) -> None:
        if self.status not in ACTION_STATUSES:
            raise InjectionProtocolError(f"unsupported action status: {self.status}")
        object.__setattr__(self, "action", _id(self.action, name="action"))
        object.__setattr__(self, "receipt", _freeze(dict(_mapping(self.receipt, name="receipt"))))
        for name, limit in (("error_code", 128), ("message", 1000), ("completed_at", 64), ("trace_id", 128)):
            object.__setattr__(self, name, _text(getattr(self, name), name=name, limit=limit))

    def to_dict(self) -> dict[str, Any]:
        return _json(self)


class CapabilityProvider(Protocol):
    def descriptor(self) -> CapabilityDescriptor: ...


class CapabilityRegistry:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], CapabilityDescriptor] = {}
        self._lock = threading.RLock()

    def register(self, descriptor: CapabilityDescriptor | CapabilityProvider) -> CapabilityDescriptor:
        item = descriptor.descriptor() if hasattr(descriptor, "descriptor") else descriptor
        if not isinstance(item, CapabilityDescriptor):
            raise InjectionProtocolError("register expects CapabilityDescriptor")
        key = (item.id, item.version)
        with self._lock:
            current = self._items.get(key)
            if current is not None and current.provider != item.provider:
                raise CapabilityConflictError(f"capability already registered: {item.key}")
            self._items[key] = item
        return item

    def unregister(self, capability_id: str, version: str | None = None) -> int:
        cid = _id(capability_id, name="capability_id")
        with self._lock:
            keys = [key for key in self._items if key[0] == cid and (version is None or key[1] == _version(version))]
            for key in keys:
                del self._items[key]
        return len(keys)

    def get(self, capability_id: str, version: str) -> CapabilityDescriptor | None:
        with self._lock:
            return self._items.get((_id(capability_id, name="capability_id"), _version(version)))

    def list(self, *, kind: str | None = None) -> tuple[CapabilityDescriptor, ...]:
        with self._lock:
            values = tuple(self._items.values())
        if kind:
            values = tuple(item for item in values if item.kind == kind)
        return tuple(sorted(values, key=lambda item: (item.id, item.version, item.provider)))

    def resolve(self, requirement: str) -> CapabilityDescriptor | None:
        raw = _text(requirement, name="requirement", required=True)
        if "@" in raw:
            cid, requested = raw.rsplit("@", 1)
            cid = _id(cid, name="requirement id")
            parts = requested.split(".")
            if not parts or not all(part.isdigit() for part in parts) or len(parts) > 2:
                raise InjectionProtocolError(f"invalid capability requirement: {requirement}")
            major, minor = int(parts[0]), int(parts[1]) if len(parts) == 2 else None
        else:
            cid, major, minor = _id(raw, name="requirement id"), None, None
        with self._lock:
            candidates = []
            for item in self._items.values():
                if item.id != cid:
                    continue
                item_major, item_minor = map(int, item.version.split("."))
                if (major is None or item_major == major) and (minor is None or item_minor >= minor):
                    candidates.append(item)
        return max(candidates, key=lambda item: tuple(map(int, item.version.split("."))), default=None)

    def missing(self, requirements: Iterable[str]) -> tuple[str, ...]:
        return tuple(item for item in requirements if self.resolve(item) is None)


class ExtensionRegistry:
    """Thread-safe control-plane registry; no provider objects cross the boundary."""

    def __init__(self) -> None:
        self._manifests: dict[str, ExtensionManifest] = {}
        self._statuses: dict[str, ExtensionStatus] = {}
        self._manifest_keys: dict[str, set[tuple[str, str]]] = {}
        self.capabilities = CapabilityRegistry()
        self._lock = threading.RLock()

    def register(self, manifest: ExtensionManifest) -> ExtensionManifest:
        if not isinstance(manifest, ExtensionManifest):
            raise InjectionProtocolError("register expects ExtensionManifest")
        with self._lock:
            existing = self._manifests.get(manifest.id)
            if existing is not None and existing.version != manifest.version:
                raise CapabilityConflictError(f"extension already registered at another version: {manifest.id}")
            incoming: set[tuple[str, str]] = set()
            for descriptor in manifest.capabilities:
                if descriptor.provider != manifest.id:
                    raise InjectionProtocolError(f"capability provider mismatch: {descriptor.key}")
                key = (descriptor.id, descriptor.version)
                if key in incoming:
                    raise InjectionProtocolError(f"duplicate capability: {descriptor.key}")
                current = self.capabilities.get(*key)
                if current is not None and current.provider != manifest.id:
                    raise CapabilityConflictError(f"capability already registered: {descriptor.key}")
                incoming.add(key)
            items = self.capabilities._items_snapshot()
            for key in self._manifest_keys.get(manifest.id, set()) - incoming:
                items.pop(key, None)
            items.update({(item.id, item.version): item for item in manifest.capabilities})
            self.capabilities._replace_items(items)
            self._manifests[manifest.id] = manifest
            self._manifest_keys[manifest.id] = incoming
            self._statuses.setdefault(manifest.id, ExtensionStatus(id=manifest.id))
        return manifest

    def set_status(self, status: ExtensionStatus) -> ExtensionStatus:
        with self._lock:
            if status.id not in self._manifests:
                raise InjectionProtocolError(f"unknown extension: {status.id}")
            self._statuses[status.id] = status
        return status

    def status(self, extension_id: str) -> ExtensionStatus | None:
        with self._lock:
            return self._statuses.get(_id(extension_id, name="extension_id"))

    def snapshot(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple({"manifest": self._manifests[key].to_dict(), "status": self._statuses[key].to_dict()} for key in sorted(self._manifests))

    def self_check(self) -> tuple[str, ...]:
        with self._lock:
            issues: list[str] = []
            known = set(self._manifests)
            for extension_id, manifest in self._manifests.items():
                if extension_id not in self._statuses:
                    issues.append(f"{extension_id}:missing_status")
                for descriptor in manifest.capabilities:
                    if self.capabilities.get(descriptor.id, descriptor.version) is None:
                        issues.append(f"{extension_id}:{descriptor.key}:missing_registration")
                issues.extend(f"{extension_id}:missing_requirement:{item}" for item in self.capabilities.missing(manifest.requires))
            for descriptor in self.capabilities.list():
                if descriptor.provider not in known:
                    issues.append(f"{descriptor.key}:orphan_provider:{descriptor.provider}")
            return tuple(sorted(set(issues)))

    def unregister(self, extension_id: str) -> bool:
        extension_id = _id(extension_id, name="extension_id")
        with self._lock:
            if extension_id not in self._manifests:
                return False
            self._manifests.pop(extension_id)
            self._statuses.pop(extension_id, None)
            items = self.capabilities._items_snapshot()
            for key in self._manifest_keys.pop(extension_id, set()):
                items.pop(key, None)
            self.capabilities._replace_items(items)
            return True


def _registry_snapshot(registry: CapabilityRegistry) -> dict[tuple[str, str], CapabilityDescriptor]:
    with registry._lock:
        return dict(registry._items)


CapabilityRegistry._items_snapshot = _registry_snapshot  # type: ignore[attr-defined]
CapabilityRegistry._replace_items = lambda self, items: _replace_registry_items(self, items)  # type: ignore[attr-defined]


def _replace_registry_items(registry: CapabilityRegistry, items: Mapping[tuple[str, str], CapabilityDescriptor]) -> None:
    with registry._lock:
        registry._items = dict(items)


__all__ = [
    "ACTION_STATUSES", "CAPABILITY_KINDS", "CAPABILITY_STATES", "PROTOCOL_VERSION",
    "ActionRequest", "ActionResult", "CapabilityConflictError", "CapabilityDescriptor",
    "CapabilityProvider", "CapabilityRegistry", "CompanionEvent", "ContextContribution",
    "Evidence", "EXTENSION_STATES", "ExtensionManifest", "ExtensionRegistry",
    "ExtensionStatus", "InjectionProtocolError", "Observation", "ProactiveCandidate", "RuntimeScope", "Scope",
]
