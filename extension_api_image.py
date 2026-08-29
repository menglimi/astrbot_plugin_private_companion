from __future__ import annotations

from typing import Any


class _ImageCapabilityFamily:
    """Private capability family backed only by its owning façade."""

    __slots__ = ("_owner",)

    def __init__(self, owner: Any) -> None:
        self._owner = owner
