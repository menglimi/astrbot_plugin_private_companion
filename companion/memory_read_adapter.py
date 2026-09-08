"""Platform-neutral, read-only adapter for ``memory.query.v1``.

This module is deliberately independent from AstrBot, Quart, ORM sessions and
the legacy memory service.  A host resolves authorization and storage details
through :class:`ScopeResolver`; a reader only receives an immutable plan.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping, Protocol


class MemoryQueryError(ValueError):
    """The canonical query is malformed or cannot be authorized."""


class MemoryReadError(RuntimeError):
    """The read port could not produce a result."""


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(ord(char) < 32 for char in value):
        raise MemoryQueryError(f"{name} must be a non-empty token")
    return value


def _optional_token(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, name)


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MemoryQueryError(f"{name} must be an object")
    return value


def _exact_keys(value: Mapping[str, Any], allowed: set[str], name: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise MemoryQueryError(f"{name} contains unknown fields: {', '.join(sorted(map(str, unknown)))}")


def _validate_time(value: Any, name: str) -> str:
    text = _required_text(value, name)
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MemoryQueryError(f"{name} must be an ISO-8601 timestamp") from exc
    return text


@dataclass(frozen=True, slots=True)
class ReadScopeBinding:
    """Resolved owner, partition and policy information supplied by the host."""

    owner_ref: str
    namespace_context: Mapping[str, Any]
    policy_revision: str
    memory_revision: int
    authorized_purposes: frozenset[str]
    partition_ref: str
    provider_generation: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_ref", _required_text(self.owner_ref, "owner_ref"))
        object.__setattr__(self, "policy_revision", _required_text(self.policy_revision, "policy_revision"))
        object.__setattr__(self, "partition_ref", _required_text(self.partition_ref, "partition_ref"))
        if isinstance(self.memory_revision, bool) or not isinstance(self.memory_revision, int) or self.memory_revision < 0:
            raise MemoryQueryError("memory_revision must be a non-negative integer")
        object.__setattr__(self, "namespace_context", _freeze(_mapping(self.namespace_context, "namespace_context")))
        object.__setattr__(self, "authorized_purposes", frozenset(_required_text(item, "authorized_purpose") for item in self.authorized_purposes))
        if self.provider_generation is not None:
            object.__setattr__(self, "provider_generation", _required_text(self.provider_generation, "provider_generation"))


class ScopeResolver(Protocol):
    def resolve(self, query: Mapping[str, Any]) -> ReadScopeBinding:
        """Resolve authorization and storage partition for one canonical query."""


class MemoryReadPort(Protocol):
    async def query(self, plan: "ReadOnlyQueryPlan") -> Mapping[str, Any]:
        """Read without mutating memory, ledgers, outboxes or caches."""


@dataclass(frozen=True, slots=True)
class ReadOnlyQueryPlan:
    request_id: str
    trace_id: str
    provider_id: str
    provider_generation: str
    scope: Mapping[str, Any]
    payload: Mapping[str, Any]
    owner_ref: str
    partition_ref: str
    namespace_context: Mapping[str, Any]
    policy_revision: str
    memory_revision: int

    def __post_init__(self) -> None:
        for name in ("request_id", "trace_id", "provider_id", "provider_generation", "owner_ref", "partition_ref", "policy_revision"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        object.__setattr__(self, "scope", _freeze(_mapping(self.scope, "scope")))
        object.__setattr__(self, "payload", _freeze(_mapping(self.payload, "payload")))
        object.__setattr__(self, "namespace_context", _freeze(_mapping(self.namespace_context, "namespace_context")))


_TOP_LEVEL = {
    "schema_version", "capability_id", "capability_version", "provider_id", "provider_generation",
    "request_id", "trace_id", "deadline_at", "scope", "required_features", "budget", "payload",
}
_SCOPE_FIELDS = {
    "ecosystem_id", "installation_id", "runtime_instance_id", "host_kind", "host_id", "bot_id",
    "platform", "account_id", "conversation_ref", "platform_conversation_id", "conversation_id",
    "session_id", "user_id", "group_id", "persona_id", "persona_binding_revision",
}
_PAYLOAD_FIELDS = {
    "namespace", "visibility", "purpose", "query_intent", "subjects", "types", "time_range", "at_time",
    "allowed_retention", "include_pending", "conflict_policy", "confidence_floor", "evidence_budget",
    "cursor", "extensions",
}


def _validate_scope(value: Any) -> Mapping[str, Any]:
    scope = _mapping(value, "scope")
    _exact_keys(scope, _SCOPE_FIELDS, "scope")
    required = {"ecosystem_id", "installation_id", "runtime_instance_id", "host_kind", "bot_id", "platform", "account_id", "conversation_ref", "platform_conversation_id", "session_id", "user_id", "group_id", "persona_id", "persona_binding_revision"}
    missing = required - set(scope)
    if missing:
        raise MemoryQueryError(f"scope missing fields: {', '.join(sorted(missing))}")
    for name in ("ecosystem_id", "installation_id", "runtime_instance_id", "host_kind", "bot_id", "persona_id"):
        _required_text(scope[name], f"scope.{name}")
    if isinstance(scope["persona_binding_revision"], bool) or not isinstance(scope["persona_binding_revision"], int) or scope["persona_binding_revision"] < 0:
        raise MemoryQueryError("scope.persona_binding_revision must be a non-negative integer")
    for name in ("host_id", "platform", "account_id", "conversation_ref", "platform_conversation_id", "conversation_id", "session_id", "user_id", "group_id"):
        _optional_token(scope[name], f"scope.{name}")
    platform = scope["platform"]
    if platform is None:
        for name in ("account_id", "platform_conversation_id", "conversation_id", "user_id", "group_id"):
            if scope[name] is not None:
                raise MemoryQueryError(f"scope.{name} must be null for an internal scope")
    elif scope["account_id"] is None:
        raise MemoryQueryError("scope.account_id is required when platform is present")
    if scope["platform_conversation_id"] is not None and scope["conversation_ref"] is None:
        raise MemoryQueryError("scope.conversation_ref is required with platform_conversation_id")
    if scope["group_id"] is not None and (scope["conversation_ref"] is None or scope["platform_conversation_id"] is None):
        raise MemoryQueryError("group scope requires conversation_ref and platform_conversation_id")
    return scope


def _validate_payload(value: Any) -> Mapping[str, Any]:
    payload = _mapping(value, "payload")
    _exact_keys(payload, _PAYLOAD_FIELDS, "payload")
    for name in ("namespace", "visibility", "purpose", "query_intent"):
        _required_text(payload.get(name), f"payload.{name}")
    if not isinstance(payload.get("subjects"), list) or not isinstance(payload.get("types"), list):
        raise MemoryQueryError("payload.subjects and payload.types must be arrays")
    if not isinstance(payload.get("include_pending"), bool):
        raise MemoryQueryError("payload.include_pending must be boolean")
    if payload.get("conflict_policy") not in {"current_only", "include_history"}:
        raise MemoryQueryError("payload.conflict_policy is invalid")
    budget = _mapping(payload.get("evidence_budget"), "payload.evidence_budget")
    _exact_keys(budget, {"max_items", "max_chars", "max_tokens"}, "payload.evidence_budget")
    for name in ("max_items", "max_chars", "max_tokens"):
        if isinstance(budget.get(name), bool) or not isinstance(budget.get(name), int) or budget[name] < 0:
            raise MemoryQueryError(f"payload.evidence_budget.{name} must be non-negative integer")
    return payload


class MemoryReadAdapter:
    """Validate, authorize and execute a canonical memory query."""

    def __init__(self, resolver: ScopeResolver, reader: MemoryReadPort) -> None:
        self._resolver = resolver
        self._reader = reader

    def build_plan(self, query: Mapping[str, Any]) -> ReadOnlyQueryPlan:
        root = _mapping(query, "query")
        _exact_keys(root, _TOP_LEVEL, "query")
        if root.get("schema_version") != "memory.query.v1":
            raise MemoryQueryError("schema_version must be memory.query.v1")
        if root.get("capability_id") != "memory.query" or root.get("capability_version") != "1.0":
            raise MemoryQueryError("query capability must be memory.query 1.0")
        for name in ("provider_id", "provider_generation", "request_id", "trace_id"):
            _required_text(root.get(name), name)
        _validate_time(root.get("deadline_at"), "deadline_at")
        required_features = root.get("required_features", [])
        if not isinstance(required_features, list) or any(not isinstance(item, str) or not item.strip() for item in required_features):
            raise MemoryQueryError("required_features must be an array of non-empty strings")
        budget = _mapping(root.get("budget"), "budget")
        _exact_keys(budget, {"max_input_bytes", "max_model_calls", "max_output_bytes", "max_tokens", "max_candidates", "max_concurrency"}, "budget")
        for name in ("max_input_bytes", "max_model_calls"):
            if isinstance(budget.get(name), bool) or not isinstance(budget.get(name), int) or budget[name] < (1 if name == "max_input_bytes" else 0):
                raise MemoryQueryError(f"budget.{name} is invalid")
        scope = _validate_scope(root.get("scope"))
        payload = _validate_payload(root.get("payload"))
        if payload["include_pending"] and scope["session_id"] is None:
            raise MemoryQueryError("include_pending requires scope.session_id")
        binding = self._resolver.resolve(root)
        if not isinstance(binding, ReadScopeBinding):
            raise MemoryQueryError("scope resolver must return ReadScopeBinding")
        if payload["purpose"] == "admin_read_all":
            raise MemoryQueryError("admin_read_all is not a portable memory purpose")
        if payload["purpose"] not in binding.authorized_purposes:
            raise MemoryQueryError("purpose is not authorized for this scope")
        if binding.provider_generation is not None and binding.provider_generation != root["provider_generation"]:
            raise MemoryQueryError("provider generation is stale")
        return ReadOnlyQueryPlan(
            request_id=root["request_id"], trace_id=root["trace_id"], provider_id=root["provider_id"],
            provider_generation=root["provider_generation"], scope=scope, payload=payload,
            owner_ref=binding.owner_ref, partition_ref=binding.partition_ref,
            namespace_context=binding.namespace_context, policy_revision=binding.policy_revision,
            memory_revision=binding.memory_revision,
        )

    async def execute(self, query: Mapping[str, Any]) -> dict[str, Any]:
        try:
            plan = self.build_plan(query)
        except MemoryQueryError as exc:
            return self._error_result(query, "rejected", "query_invalid", str(exc))
        try:
            output = await self._reader.query(plan)
            if not isinstance(output, Mapping):
                raise MemoryReadError("reader must return a mapping")
            return self._result(plan, status="succeeded", output=output)
        except Exception as exc:  # reader failures never mutate state or leak internals
            return self._result(plan, status="failed", output=None, reason_code="reader_failed", retryable=True, warning=str(exc))

    def _result(self, plan: ReadOnlyQueryPlan, *, status: str, output: Mapping[str, Any] | None, reason_code: str | None = None, retryable: bool = False, warning: str | None = None) -> dict[str, Any]:
        result = {
            "schema_version": "memory.result.v1", "capability_id": "memory.query", "capability_version": "1.0",
            "provider_id": plan.provider_id, "provider_generation": plan.provider_generation,
            "request_id": plan.request_id, "trace_id": plan.trace_id, "status": status,
            "reason_code": reason_code, "retryable": retryable, "degraded": status == "partial",
            "warnings": [warning] if warning else [], "output": output,
        }
        return result

    @staticmethod
    def _error_result(query: Mapping[str, Any], status: str, reason: str, warning: str, retryable: bool = False) -> dict[str, Any]:
        return {
            "schema_version": "memory.result.v1", "capability_id": "memory.query", "capability_version": "1.0",
            "provider_id": str(query.get("provider_id") or "unknown"), "provider_generation": str(query.get("provider_generation") or "unknown"),
            "request_id": str(query.get("request_id") or "unknown"), "trace_id": str(query.get("trace_id") or "unknown"),
            "status": status, "reason_code": reason, "retryable": retryable, "degraded": False,
            "warnings": [warning], "output": None,
        }


__all__ = ["MemoryQueryError", "MemoryReadError", "MemoryReadPort", "MemoryReadAdapter", "ReadOnlyQueryPlan", "ReadScopeBinding", "ScopeResolver"]
