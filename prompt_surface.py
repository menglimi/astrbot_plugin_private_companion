# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .helpers import _single_line


@dataclass
class PromptFragment:
    key: str
    content: str
    priority: int = 100
    source: str = ""
    index: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def normalized_key(self) -> str:
        return _single_line(self.key or self.source or "fragment", 80)


class PromptSurface:
    """Collects prompt fragments before rendering them into one injection block."""

    def __init__(self) -> None:
        self._fragments: list[PromptFragment] = []
        self._next_index = 0

    def add(
        self,
        key: str,
        content: str,
        *,
        priority: int = 100,
        source: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        text = str(content or "").strip()
        if not text:
            return
        self._fragments.append(
            PromptFragment(
                key=key,
                content=text,
                priority=priority,
                source=source,
                index=self._next_index,
                metadata=dict(metadata or {}),
            )
        )
        self._next_index += 1

    def extend(self, fragments: Iterable[PromptFragment]) -> None:
        for fragment in fragments:
            if isinstance(fragment, PromptFragment):
                self.add(
                    fragment.key,
                    fragment.content,
                    priority=fragment.priority,
                    source=fragment.source,
                    metadata=fragment.metadata,
                )

    def _rendered_fragments(self) -> list[PromptFragment]:
        seen_keys: set[str] = set()
        seen_content: set[str] = set()
        rendered: list[PromptFragment] = []
        for fragment in sorted(self._fragments, key=lambda item: (item.priority, item.index)):
            key = fragment.normalized_key()
            content = str(fragment.content or "").strip()
            content_sig = content
            if key and key in seen_keys:
                continue
            if content_sig and content_sig in seen_content:
                continue
            if key:
                seen_keys.add(key)
            if content_sig:
                seen_content.add(content_sig)
            rendered.append(fragment)
        return rendered

    def rendered_fragments(self) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for fragment in self._rendered_fragments():
            content = str(fragment.content or "").strip()
            item = {
                "key": fragment.normalized_key(),
                "source": _single_line(fragment.source, 80),
                "priority": int(fragment.priority),
                "content": content,
                "chars": len(content),
            }
            if fragment.metadata:
                item["metadata"] = dict(fragment.metadata)
            result.append(item)
        return result

    def render(self) -> str:
        parts: list[str] = []
        for fragment in self._rendered_fragments():
            parts.append(str(fragment.content or "").strip())
        return "\n\n".join(parts)

    def render_partition(self, predicate: Callable[[PromptFragment], bool]) -> tuple[str, str]:
        matched, rest, _matched_fragments, _rest_fragments = self.render_partition_with_fragments(predicate)
        return matched, rest

    def render_partition_with_fragments(
        self,
        predicate: Callable[[PromptFragment], bool],
    ) -> tuple[str, str, list[dict[str, object]], list[dict[str, object]]]:
        """Render a partition and expose the exact child manifest for plan bridges."""
        matched: list[str] = []
        rest: list[str] = []
        matched_fragments: list[dict[str, object]] = []
        rest_fragments: list[dict[str, object]] = []
        for fragment in self._rendered_fragments():
            content = str(fragment.content or "").strip()
            if not content:
                continue
            item: dict[str, object] = {
                "key": fragment.normalized_key(),
                "source": _single_line(fragment.source, 80),
                "priority": int(fragment.priority),
                "content": content,
                "chars": len(content),
            }
            if fragment.metadata:
                item["metadata"] = dict(fragment.metadata)
            if predicate(fragment):
                matched.append(content)
                matched_fragments.append(item)
            else:
                rest.append(content)
                rest_fragments.append(item)
        return (
            "\n\n".join(matched),
            "\n\n".join(rest),
            matched_fragments,
            rest_fragments,
        )

    def __len__(self) -> int:
        return len(self._fragments)
