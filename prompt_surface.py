# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .conversation_prompt_section import (
    PromptSection,
    coerce_prompt_section,
    render_prompt_sections,
    title_for_prompt_key,
)
from .helpers import _single_line


@dataclass
class PromptFragment:
    key: str
    content: Any
    title: str = ""
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
        content: Any,
        *,
        priority: int = 100,
        source: str = "",
        title: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        section = coerce_prompt_section(content)
        body = section.content if section is not None else content
        if body is None or (isinstance(body, str) and not body.strip()):
            return
        resolved_title = title_for_prompt_key(
            key,
            title or (section.title if section is not None else ""),
        )
        self._fragments.append(
            PromptFragment(
                key=key,
                content=body,
                title=resolved_title,
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
                    title=fragment.title,
                    metadata=fragment.metadata,
                )

    def _rendered_fragments(self) -> list[PromptFragment]:
        seen_keys: set[str] = set()
        seen_content: set[str] = set()
        rendered: list[PromptFragment] = []
        for fragment in sorted(self._fragments, key=lambda item: (item.priority, item.index)):
            key = fragment.normalized_key()
            content = fragment.content
            content_sig = repr(content)
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
            content = fragment.content
            item = {
                "key": fragment.normalized_key(),
                "title": fragment.title,
                "source": _single_line(fragment.source, 80),
                "priority": int(fragment.priority),
                "content": content,
                "chars": len(str(content)),
            }
            if fragment.metadata:
                item["metadata"] = dict(fragment.metadata)
            result.append(item)
        return result

    def render(self) -> str:
        return self._render_sections(self._rendered_fragments())

    @staticmethod
    def _render_sections(fragments: Iterable[PromptFragment]) -> str:
        return render_prompt_sections(
            PromptSection(fragment.title, fragment.content)
            for fragment in fragments
        )

    def render_partition(self, predicate: Callable[[PromptFragment], bool]) -> tuple[str, str]:
        matched, rest, _matched_fragments, _rest_fragments = self.render_partition_with_fragments(predicate)
        return matched, rest

    def render_partition_with_fragments(
        self,
        predicate: Callable[[PromptFragment], bool],
    ) -> tuple[str, str, list[dict[str, object]], list[dict[str, object]]]:
        """Render a partition and expose the exact child manifest for plan bridges."""
        matched: list[PromptFragment] = []
        rest: list[PromptFragment] = []
        matched_fragments: list[dict[str, object]] = []
        rest_fragments: list[dict[str, object]] = []
        for fragment in self._rendered_fragments():
            content = fragment.content
            if content is None or (isinstance(content, str) and not content.strip()):
                continue
            item: dict[str, object] = {
                "key": fragment.normalized_key(),
                "title": fragment.title,
                "source": _single_line(fragment.source, 80),
                "priority": int(fragment.priority),
                "content": content,
                "chars": len(str(content)),
            }
            if fragment.metadata:
                item["metadata"] = dict(fragment.metadata)
            if predicate(fragment):
                matched.append(fragment)
                matched_fragments.append(item)
            else:
                rest.append(fragment)
                rest_fragments.append(item)
        return (
            self._render_sections(matched),
            self._render_sections(rest),
            matched_fragments,
            rest_fragments,
        )

    def __len__(self) -> int:
        return len(self._fragments)
