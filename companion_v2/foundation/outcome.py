"""Explicit success and failure envelopes for V2 contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class Failure:
    """A stable, non-sensitive failure result that callers must account for."""

    code: str
    reason: str
    details: dict | None = None


@dataclass(frozen=True)
class Outcome(Generic[T]):
    """Exactly one of a value or failure, avoiding empty-value fallbacks."""

    value: T | None = None
    failure: Failure | None = None

    def __post_init__(self) -> None:
        if (self.value is None) == (self.failure is None):
            raise ValueError("Outcome requires exactly one of value or failure")

    @classmethod
    def success(cls, value: T) -> "Outcome[T]":
        return cls(value=value)

    @classmethod
    def failed(cls, code: str, reason: str, *, details: dict | None = None) -> "Outcome[T]":
        return cls(failure=Failure(code=code, reason=reason, details=dict(details) if details else None))

    @property
    def is_success(self) -> bool:
        return self.failure is None

    def unwrap(self) -> T:
        if self.failure is not None:
            raise RuntimeError(f"{self.failure.code}: {self.failure.reason}")
        assert self.value is not None
        return self.value
