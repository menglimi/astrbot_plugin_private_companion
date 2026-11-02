"""Internal task profile. This is not the public execution wire schema."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
import math
from typing import Callable

from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import AudienceRef, ScopeRef, SubjectKind, SubjectRef, normalize_utc
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal, ResolvedIdentity, RoleBinding, _opaque


class TaskError(ValueError):
    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code


class TaskDeferred(TaskError):
    """Admission was refused before dispatch; a fresh owner task may run later."""

    def __init__(self, code: str, reason: str, *, retry_at: datetime):
        super().__init__(code, reason)
        self.retry_at = normalize_utc(retry_at, field="task deferral")


def require(result: Outcome):
    if result.failure:
        raise TaskError(result.failure.code, result.failure.reason)
    return result.unwrap()


def stamp(value: datetime) -> str:
    return normalize_utc(value, field="task timestamp").isoformat(timespec="microseconds")


def encode(value: object) -> str:
    def default(item):
        if is_dataclass(item):
            return {descriptor.name: getattr(item, descriptor.name) for descriptor in fields(item)
                    if descriptor.metadata.get("encode", True)}
        if isinstance(item, datetime):
            return stamp(item)
        raise TypeError("unsupported task record value")
    return json.dumps(value, default=default, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


class Lane(str, Enum):
    FOREGROUND = "foreground"
    BACKGROUND = "background"


class SideEffect(str, Enum):
    NONE = "none"
    LOCAL = "local"
    EXTERNAL_DEVICE = "external_device"
    EXTERNAL_NETWORK = "external_network"
    MESSAGE_DELIVERY = "message_delivery"


TERMINAL = frozenset({"succeeded", "failed", "uncertain", "cancelled", "expired", "dead_letter"})
QUEUED = ("created", "retry_scheduled")


@dataclass(frozen=True)
class TaskContext:
    actor: ResolvedIdentity
    role_binding: RoleBinding
    audience: AudienceRef

    def __post_init__(self):
        if self.role_binding.scope != self.audience.scope or not self.scope.is_resolved:
            raise ValueError("tasks require the original complete conversation scope")
        if self.actor.subject.installation_id != self.scope.installation_id:
            raise ValueError("task actor crosses installation")

    @property
    def scope(self) -> ScopeRef:
        return self.audience.scope

    @property
    def key(self) -> str:
        return digest({"principal": asdict(self.actor.principal), "subject": asdict(self.actor.subject),
                       "binding": [self.actor.binding_id, self.actor.binding_revision],
                       "role": asdict(self.role_binding), "audience": asdict(self.audience)})

    def same_window(self, other: object) -> bool:
        """Whether ``other`` denotes this exact conversation window.

        A window is the principal, its subject, the identity binding, the role
        binding and the audience — exactly ``key``. ``actor.policy_revision``
        is excluded on purpose: the identity directory advances it for every
        unrelated bind, grant or revoke in the installation, so two snapshots
        of one unchanged window routinely carry different revisions. Comparing
        whole records for equality (``==``) keeps that revision and must not be
        used to decide whether a role is still bound.

        A changed participant, conversation scope, role or audience produces a
        different ``key`` and therefore a different window.
        """
        return isinstance(other, TaskContext) and self.key == other.key

    @classmethod
    def restore(cls, value: dict) -> TaskContext:
        def subject(data):
            return SubjectRef(data["installation_id"], SubjectKind(data["kind"]), data["subject_id"])
        actor = value["actor"]
        identity = ResolvedIdentity(AuthenticatedHostPrincipal(**actor["principal"]), subject(actor["subject"]),
                                   actor["binding_id"], actor["binding_revision"], actor["policy_revision"])
        scope = ScopeRef.restore(value["role_binding"]["scope"])
        return cls(identity, RoleBinding(scope, subject(value["role_binding"]["role"]), value["role_binding"]["revision"]),
                   AudienceRef(ScopeRef.restore(value["audience"]["scope"]),
                               tuple(subject(item) for item in value["audience"]["recipients"])))


@dataclass(frozen=True)
class ProviderBinding:
    capability: str
    provider: str
    version: str
    generation: str

    def __post_init__(self):
        for name, value in asdict(self).items():
            _opaque(value, field=name)


@dataclass(frozen=True)
class TaskDefinition:
    kind: str
    owner: str
    action: str
    lane: Lane
    version: str = "1"
    side_effect: SideEffect = SideEffect.NONE
    binding: ProviderBinding | None = None
    timeout_seconds: float = 30
    max_attempts: int = 2
    backoff_seconds: float = 1
    concurrency: int = 2
    per_scope_concurrency: int = 1

    def __post_init__(self):
        for name in ("kind", "owner", "action", "version"):
            _opaque(getattr(self, name), field=name)
        if not isinstance(self.lane, Lane) or not isinstance(self.side_effect, SideEffect):
            raise ValueError("task lane and effect must be explicit")
        if self.side_effect is not SideEffect.NONE and self.binding is None:
            raise ValueError("effectful tasks require a complete capability provider binding")
        if self.binding is not None and not isinstance(self.binding, ProviderBinding):
            raise ValueError("provider binding must be a validated record")
        for name in ("timeout_seconds", "backoff_seconds"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 3600:
                raise ValueError("task time budgets must be finite and bounded")
        if self.timeout_seconds <= 0:
            raise ValueError("task timeout must be positive")
        for name, maximum in (("max_attempts", 8), ("concurrency", 32), ("per_scope_concurrency", 8)):
            if type(getattr(self, name)) is not int or not 1 <= getattr(self, name) <= maximum:
                raise ValueError("task attempts and concurrency must be positive and bounded")


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    context: TaskContext
    kind: str
    request_ref: str
    idempotency_key: str
    not_before: datetime
    deadline_at: datetime

    def __post_init__(self):
        for name in ("task_id", "kind", "request_ref", "idempotency_key"):
            _opaque(getattr(self, name), field=name)
        for name in ("not_before", "deadline_at"):
            object.__setattr__(self, name, normalize_utc(getattr(self, name), field=name))
        if self.deadline_at <= self.not_before:
            raise ValueError("task deadline must follow its earliest start")

    @classmethod
    def restore(cls, value: dict) -> TaskSpec:
        return cls(**{**value, "context": TaskContext.restore(value["context"]),
                      "not_before": datetime.fromisoformat(value["not_before"]),
                      "deadline_at": datetime.fromisoformat(value["deadline_at"])})


@dataclass(frozen=True)
class TaskRegistration:
    definition: TaskDefinition
    # Trusted, bounded owner read: validate original inputs, object access and binding.
    validate: Callable[[TaskSpec, datetime], Outcome]

    def __post_init__(self):
        if not isinstance(self.definition, TaskDefinition) or not callable(self.validate):
            raise ValueError("task registration requires a definition and owner validator")


@dataclass(frozen=True)
class SupervisorLimits:
    foreground_slots: int = 2
    background_slots: int = 2
    queued_per_lane: int = 64
    retained_tasks: int = 10000

    def __post_init__(self):
        for name, maximum in (("foreground_slots", 32), ("background_slots", 32),
                              ("queued_per_lane", 10_000), ("retained_tasks", 100_000)):
            if type(getattr(self, name)) is not int or not 1 <= getattr(self, name) <= maximum:
                raise ValueError("supervisor limits must be positive and bounded")


@dataclass(frozen=True)
class TaskClaim:
    task_id: str
    attempt_id: str
    generation: int


@dataclass(frozen=True)
class WorkerOwnership:
    process_id: int
    process_started_at: datetime
    loop_id: str

    def __post_init__(self):
        if type(self.process_id) is not int or self.process_id <= 0:
            raise ValueError("worker ownership requires a process ID")
        object.__setattr__(self, "process_started_at", normalize_utc(
            self.process_started_at, field="worker process start"))
        _opaque(self.loop_id, field="worker loop ID")

    @classmethod
    def restore(cls, value):
        return cls(value["process_id"], datetime.fromisoformat(value["process_started_at"]), value["loop_id"])


@dataclass(frozen=True)
class WorkerAttempt:
    claim: TaskClaim
    kind: str
    attempt_no: int
    started_at: datetime
    ownership: WorkerOwnership | None
    side_effect: SideEffect

    @property
    def worker_name(self):
        return f"companion:{self.kind}:{self.claim.task_id}:{self.attempt_no}"


@dataclass(frozen=True)
class WorkerStopRecovery:
    scanned: int
    last_attempt_id: str | None
    released_tasks: tuple[str, ...]


@dataclass(frozen=True)
class ClaimResult:
    """One exact-task claim attempt: a new attempt, or why the queue refused one.

    ``refused`` names a *transient* condition that left the row untouched and
    still queued, so the caller can retry or keep the reservation. A terminal
    transition is never reported here: the row's own state and reason are
    authoritative then.
    """

    claim: TaskClaim | None
    refused: str | None = None

    def __post_init__(self):
        if self.claim is not None and self.refused is not None:
            raise ValueError("an admitted claim cannot also be refused")


@dataclass(frozen=True)
class TaskResult:
    status: str
    effect: str = "none"
    result_ref: str | None = None
    receipt_ref: str | None = None
    operation_id: str | None = None
    retryable: bool = False

    def __post_init__(self):
        if self.status not in ("succeeded", "failed", "uncertain"):
            raise ValueError("unsupported atomic task result")
        if self.effect not in ("none", "not_committed", "committed", "unknown"):
            raise ValueError("unsupported task effect result")
        for name in ("result_ref", "receipt_ref", "operation_id"):
            if getattr(self, name) is not None:
                _opaque(getattr(self, name), field=name)
        if type(self.retryable) is not bool:
            raise ValueError("retryable must be a boolean")
        if self.effect in ("committed", "not_committed") and self.receipt_ref is None:
            raise ValueError("an owner receipt must support a known effect result")
        if self.effect == "committed" and (self.status != "succeeded" or not self.operation_id):
            raise ValueError("an atomic commit requires its owner operation and success receipt")
        if self.status == "uncertain" and self.retryable:
            raise ValueError("unknown results cannot be automatically retried")
        if self.effect == "unknown" and self.status != "uncertain":
            raise ValueError("unknown effects require an uncertain result")
        if self.retryable and self.status != "failed":
            raise ValueError("only a known failure may request retry")


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    revision: int
    state: str
    generation: int
    attempt_no: int
    attempt_id: str | None
    spec: TaskSpec
    operation_id: str | None
    result_ref: str | None
    effect: str
    reason: str
