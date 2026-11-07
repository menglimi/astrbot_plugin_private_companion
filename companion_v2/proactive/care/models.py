"""Data shapes for care concerns and the impulses they produce."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class Concern:
    """A standing attachment, not a schedule entry."""

    id: str
    label: str
    note: str
    weight: str = "medium"
    supported: bool = True
    cue: str = ""


@dataclass(frozen=True)
class DeviceObservation:
    """One device as the current moment sees it."""

    alias: str
    category: str = ""
    online: bool | None = None
    state: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_light(self) -> bool:
        return "灯" in self.category or "light" in self.category.lower() or \
            "灯" in self.alias

    @property
    def is_air_conditioner(self) -> bool:
        return "空调" in self.category or "ac" in self.category.lower() or \
            "空调" in self.alias

    def is_on(self) -> bool:
        return self.state.get("on") is True


@dataclass(frozen=True)
class Impulse:
    """One moment of noticing, with the reason it stayed an impulse."""

    concern_id: str
    alias: str
    title: str
    reason: str
    verdict: str
    action: str
    at: str
    marker: str

    def as_dict(self) -> dict[str, Any]:
        return {"concern_id": self.concern_id, "alias": self.alias, "title": self.title,
                "reason": self.reason, "verdict": self.verdict, "action": self.action,
                "at": self.at, "marker": self.marker}
