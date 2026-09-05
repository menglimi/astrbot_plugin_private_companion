from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

Route = tuple[str, Any, list[str], str]


def build_route_bindings(
    routes: Iterable[Route],
    *,
    persona_control_routes: set[str],
    persona_wrapper: Callable[[Any], Any],
    http_wrapper: Callable[[Any], Any],
) -> list[Route]:
    """Apply transport-neutral route wrappers without changing route order."""
    bindings: list[Route] = []
    for path, handler, methods, description in routes:
        scoped = handler if path in persona_control_routes else persona_wrapper(handler)
        bindings.append((path, http_wrapper(scoped), methods, description))
    return bindings
