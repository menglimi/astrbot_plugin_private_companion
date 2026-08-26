"""Request-scoped assembly plan for main-conversation prompt injections."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from astrbot.core.agent.message import TextPart

from .conversation_prompt_section import (
    coerce_prompt_section,
    render_prompt_section,
    title_for_prompt_key,
)

PLAN_ATTR = "_private_companion_conversation_injection_plan"
LEGACY_TURN_FRAGMENTS_ATTR = "_private_companion_turn_prompt_fragments"
TURN_PLACEMENT_ATTR = "_private_companion_turn_prompt_placement"
TURN_PART_ATTR = "_private_companion_turn_fragments"
TURN_TEXT_ATTR = "_private_companion_conversation_plan_turn_text"
TURN_START_MARKER = "<!-- private_companion_turn_fragments_start -->"
TURN_END_MARKER = "<!-- private_companion_turn_fragments_end -->"

PLACEMENT_STABLE_SYSTEM = "stable_system"
PLACEMENT_DYNAMIC_SYSTEM = "dynamic_system"
PLACEMENT_TURN_TAIL = "turn_tail"
PLACEMENT_TOOL_CONTRACT = "tool_contract"
_PLACEMENTS = {
    PLACEMENT_STABLE_SYSTEM,
    PLACEMENT_DYNAMIC_SYSTEM,
    PLACEMENT_TURN_TAIL,
    PLACEMENT_TOOL_CONTRACT,
}
_MERGE_POLICIES = {"first", "replace", "append"}


def _clean_key(value: Any, fallback: str = "fragment") -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:160] or fallback


def _key_from_marker(marker: Any) -> str:
    text = _clean_key(marker, "")
    match = re.fullmatch(r"<!--\s*private_companion_([a-zA-Z0-9_]+)_v\d+\s*-->", text)
    if match:
        return f"marker.{match.group(1)}"
    return f"marker.{text}" if text else "fragment"


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)


def _content_bytes(value: Any) -> bytes:
    return _content_text(value).encode("utf-8", "ignore")


@dataclass
class ConversationInjectionBlock:
    key: str
    marker: str
    content: Any
    title: str = ""
    priority: int = 50
    source: str = ""
    placement: str = PLACEMENT_TURN_TAIL
    temporary: bool = True
    merge_policy: str = "first"
    materialized: bool = False
    opaque: bool = False
    structured: bool = False
    index: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    children: list[dict[str, Any]] = field(default_factory=list)

    @staticmethod
    def _manifest_children(
        children: Iterable[dict[str, Any]],
        *,
        include_content: bool,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for raw in children:
            if not isinstance(raw, dict):
                continue
            item = copy.deepcopy(raw)
            content = str(item.pop("content", "") or "")
            if content:
                item["chars"] = len(content)
                item["sha256"] = hashlib.sha256(
                    content.encode("utf-8", "ignore")
                ).hexdigest()
                if include_content:
                    item["content"] = content
            result.append(item)
        return result

    def manifest_item(self, *, include_content: bool = False) -> dict[str, Any]:
        item = {
            "key": self.key,
            "marker": self.marker,
            "source": self.source,
            "title": self.title,
            "priority": self.priority,
            "placement": self.placement,
            "temporary": self.temporary,
            "merge_policy": self.merge_policy,
            "materialized": self.materialized,
            "opaque": self.opaque,
            "structured": self.structured,
            "index": self.index,
            "chars": len(_content_text(self.content)),
            "sha256": hashlib.sha256(_content_bytes(self.content)).hexdigest(),
            "metadata": copy.deepcopy(self.metadata),
            "children": self._manifest_children(
                self.children,
                include_content=include_content,
            ),
        }
        if include_content:
            item["content"] = copy.deepcopy(self.content)
        return item


class ConversationInjectionPlan:
    """Own prompt blocks for one ProviderRequest and render them deterministically."""

    def __init__(self) -> None:
        self._blocks: list[ConversationInjectionBlock] = []
        self._by_key: dict[str, ConversationInjectionBlock] = {}
        self._next_index = 0
        self._frozen = False
        self._prefer_extra_user_content = True

    @property
    def frozen(self) -> bool:
        return self._frozen

    def freeze(self) -> None:
        self._frozen = True

    def add(
        self,
        *,
        key: str = "",
        marker: str = "",
        content: Any,
        priority: int = 50,
        source: str = "",
        title: str = "",
        placement: str = PLACEMENT_TURN_TAIL,
        temporary: bool = True,
        merge_policy: str = "first",
        materialized: bool = False,
        opaque: bool = False,
        structured: bool = False,
        metadata: dict[str, Any] | None = None,
        children: Iterable[dict[str, Any]] | None = None,
    ) -> ConversationInjectionBlock | None:
        if self._frozen:
            raise RuntimeError("conversation injection plan is frozen")
        normalized_marker = _clean_key(marker, "")
        normalized_key = _clean_key(key, "") or _key_from_marker(normalized_marker)
        normalized_placement = str(placement or "").strip().lower()
        if normalized_placement not in _PLACEMENTS:
            raise ValueError(f"unsupported conversation injection placement: {placement}")
        normalized_merge = str(merge_policy or "first").strip().lower()
        if normalized_merge not in _MERGE_POLICIES:
            raise ValueError(f"unsupported conversation injection merge policy: {merge_policy}")

        section = None if opaque or structured or normalized_placement == PLACEMENT_TOOL_CONTRACT else coerce_prompt_section(content)
        body = section.content if section is not None else content
        if body is None or (isinstance(body, str) and not body.strip()):
            return None
        if opaque or normalized_placement == PLACEMENT_TOOL_CONTRACT:
            # These payloads are contracts owned by their producer. Do not strip,
            # normalize, parse or reserialize even a single byte.
            body = str(body)
        resolved_title = title_for_prompt_key(
            normalized_key,
            title or (section.title if section is not None else ""),
        )

        existing = self._by_key.get(normalized_key)
        if existing is None and normalized_marker:
            existing = next(
                (
                    block
                    for block in self._blocks
                    if block.marker == normalized_marker
                ),
                None,
            )
            if existing is not None:
                self._by_key[normalized_key] = existing
        child_items = [copy.deepcopy(item) for item in (children or []) if isinstance(item, dict)]
        if existing is not None:
            if normalized_merge == "first":
                return existing
            if normalized_merge == "append":
                existing_text = _content_text(existing.content)
                new_text = _content_text(body)
                if new_text != existing_text and new_text not in existing_text.split("\n\n"):
                    existing.content = f"{existing_text}\n\n{new_text}"
                existing.children.extend(child_items)
                if metadata:
                    existing.metadata.update(copy.deepcopy(metadata))
                return existing
            existing.marker = normalized_marker
            existing.content = body
            existing.title = resolved_title
            existing.priority = int(priority)
            existing.source = _clean_key(source, "")[:80]
            existing.placement = normalized_placement
            existing.temporary = bool(temporary)
            existing.merge_policy = normalized_merge
            existing.materialized = bool(materialized)
            existing.opaque = bool(opaque)
            existing.structured = bool(structured)
            existing.metadata = copy.deepcopy(metadata or {})
            existing.children = child_items
            return existing

        block = ConversationInjectionBlock(
            key=normalized_key,
            marker=normalized_marker,
            content=body,
            title=resolved_title,
            priority=int(priority),
            source=_clean_key(source, "")[:80],
            placement=normalized_placement,
            temporary=bool(temporary),
            merge_policy=normalized_merge,
            materialized=bool(materialized),
            opaque=bool(opaque),
            structured=bool(structured),
            index=self._next_index,
            metadata=copy.deepcopy(metadata or {}),
            children=child_items,
        )
        self._next_index += 1
        self._blocks.append(block)
        self._by_key[normalized_key] = block
        return block

    def annotate_marker(
        self,
        marker: str,
        *,
        metadata: dict[str, Any] | None = None,
        children: Iterable[dict[str, Any]] | None = None,
    ) -> bool:
        marker_text = _clean_key(marker, "")
        target = next((block for block in self._blocks if block.marker == marker_text), None)
        if target is None:
            return False
        if metadata:
            target.metadata.update(copy.deepcopy(metadata))
        if children is not None:
            target.children = [copy.deepcopy(item) for item in children if isinstance(item, dict)]
        return True

    def materialize_system_block(
        self,
        req: Any,
        *,
        key: str,
        marker: str,
        content: Any,
        priority: int = 50,
        source: str = "",
        title: str = "",
        placement: str = PLACEMENT_DYNAMIC_SYSTEM,
        metadata: dict[str, Any] | None = None,
        structured: bool = False,
    ) -> bool:
        """Append one system block in legacy order while registering its provenance."""

        normalized_key = _clean_key(key, "") or _key_from_marker(marker)
        if self.contains_key(normalized_key) or self.contains_marker(marker):
            return False
        block = self.add(
            key=normalized_key,
            marker=_clean_key(marker, ""),
            content=content,
            priority=priority,
            source=source,
            title=title,
            placement=placement,
            temporary=False,
            materialized=True,
            structured=structured,
            metadata={"materialized_by_plan": True, **copy.deepcopy(metadata or {})},
        )
        if block is None:
            return False
        self._render_system(req)
        return True

    def contains_key(self, key: str) -> bool:
        return _clean_key(key, "") in self._by_key

    def contains_marker(self, marker: str) -> bool:
        marker_text = _clean_key(marker, "")
        return bool(marker_text) and any(block.marker == marker_text for block in self._blocks)

    def blocks(self, *, placement: str | None = None, include_materialized: bool = True) -> list[ConversationInjectionBlock]:
        selected = self._blocks
        if placement is not None:
            selected = [block for block in selected if block.placement == placement]
        if not include_materialized:
            selected = [block for block in selected if not block.materialized]
        return sorted(selected, key=lambda block: (block.priority, block.index))

    def legacy_turn_fragments(self) -> list[dict[str, Any]]:
        fragments: list[dict[str, Any]] = []
        seen_markers: set[str] = set()
        seen_content: set[str] = set()
        for block in self.blocks(placement=PLACEMENT_TURN_TAIL, include_materialized=False):
            content_text = _content_text(block.content)
            if not block.marker or not content_text:
                continue
            if block.marker in seen_markers or content_text in seen_content:
                continue
            seen_markers.add(block.marker)
            seen_content.add(content_text)
            fragments.append(
                {
                    "marker": block.marker,
                    "content": self._visible_content(block),
                    "priority": block.priority,
                    "source": block.source,
                    "index": block.index,
                }
            )
        return fragments

    def manifest(self, *, include_content: bool = False) -> list[dict[str, Any]]:
        return [
            block.manifest_item(include_content=include_content)
            for block in self.blocks()
        ]

    @staticmethod
    def _visible_content(block: ConversationInjectionBlock) -> str:
        if block.opaque or block.structured or block.placement == PLACEMENT_TOOL_CONTRACT:
            return _content_text(block.content)
        return render_prompt_section(block.title, block.content)

    @staticmethod
    def _remove_once(text: str, candidate: str) -> str:
        if not candidate or candidate not in text:
            return text
        cleaned = text.replace(candidate, "", 1)
        return re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    def _strip_registered_materialized_blocks(self, current: str) -> str:
        """Remove legacy/raw copies before rendering owned system placements."""

        result = current
        for block in self.blocks(include_materialized=True):
            if (
                not block.materialized
                or block.opaque
                or block.placement == PLACEMENT_TOOL_CONTRACT
            ):
                continue
            content = _content_text(block.content)
            raw = f"{block.marker}\n{content}".strip() if block.marker else content
            visible = self._visible_content(block)
            rendered = f"{block.marker}\n{visible}".strip() if block.marker else visible
            for candidate in (rendered, raw, visible):
                before = result
                result = self._remove_once(result, candidate)
                if result != before:
                    break
        return result

    @staticmethod
    def _render_block_group(blocks: Iterable[ConversationInjectionBlock]) -> str:
        """Render ordinary blocks under one root, flushing only raw exceptions."""

        parts: list[str] = []
        xml_children: list[str] = []

        def unwrap_root(rendered: str) -> str | None:
            start = "<private_companion_context>"
            end = "</private_companion_context>"
            if rendered.startswith(start) and rendered.endswith(end):
                return rendered[len(start) : -len(end)]
            return None

        def flush_xml() -> None:
            if not xml_children:
                return
            parts.append(
                "<private_companion_context>"
                + "".join(xml_children)
                + "</private_companion_context>"
            )
            xml_children.clear()

        for block in blocks:
            if block.placement == PLACEMENT_TOOL_CONTRACT:
                continue
            if block.opaque:
                flush_xml()
                parts.append(_content_text(block.content))
                continue
            if block.structured:
                raw = _content_text(block.content)
                inner = unwrap_root(raw)
                if inner is not None:
                    if inner.strip():
                        xml_children.append(inner)
                    continue
                flush_xml()
                parts.append(raw)
                continue
            rendered = render_prompt_section(block.title, block.content)
            inner = unwrap_root(rendered)
            if inner:
                xml_children.append(inner)
        flush_xml()
        return "\n\n".join(part for part in parts if part)

    @staticmethod
    def _remove_owned_turn_part(req: Any) -> None:
        parts = getattr(req, "extra_user_content_parts", None)
        if not isinstance(parts, list):
            return
        kept: list[Any] = []
        for part in parts:
            if bool(getattr(part, TURN_PART_ATTR, False)):
                continue
            raw = str(
                part.get("text") or part.get("content") or ""
                if isinstance(part, dict)
                else getattr(part, "text", "") or getattr(part, "content", "") or ""
            )
            if TURN_START_MARKER in raw and TURN_END_MARKER in raw:
                continue
            kept.append(part)
        req.extra_user_content_parts = kept

    def _base_prompt(self, req: Any) -> str:
        current = str(getattr(req, "prompt", "") or "")
        previous = str(getattr(req, TURN_TEXT_ATTR, "") or "")
        if previous:
            if current == previous:
                current = ""
            elif current.endswith(f"\n\n{previous}"):
                current = current[: -(len(previous) + 2)].rstrip()
            elif previous in current:
                current = self._remove_once(current, previous)
        # Compatibility cleanup for requests rendered by older plugin versions.
        return re.sub(
            rf"\n*\s*{re.escape(TURN_START_MARKER)}.*?{re.escape(TURN_END_MARKER)}\s*",
            "\n\n",
            current,
            flags=re.DOTALL,
        ).strip()

    def _render_system(self, req: Any) -> None:
        current = str(getattr(req, "system_prompt", "") or "")
        previous = str(getattr(req, "_private_companion_conversation_plan_system_text", "") or "")
        if previous:
            if current == previous:
                current = ""
            elif current.endswith(f"\n\n{previous}"):
                current = current[: -(len(previous) + 2)].rstrip()
            elif previous in current:
                current = self._remove_once(current, previous)
        current = self._strip_registered_materialized_blocks(current)
        rendered: list[str] = []
        for placement in (PLACEMENT_STABLE_SYSTEM, PLACEMENT_DYNAMIC_SYSTEM):
            placement_text = self._render_block_group(self.blocks(placement=placement))
            if placement_text:
                rendered.append(placement_text)
        owned = "\n\n".join(part for part in rendered if part).strip()
        setattr(req, "_private_companion_conversation_plan_system_text", owned)
        if owned:
            req.system_prompt = f"{current}\n\n{owned}".strip() if current else owned
        else:
            req.system_prompt = current

    def render_into(self, req: Any, *, prefer_extra_user_content: bool | None = None) -> str:
        if prefer_extra_user_content is not None:
            self._prefer_extra_user_content = bool(prefer_extra_user_content)
        self._render_system(req)
        base = self._base_prompt(req)
        fragments = self.legacy_turn_fragments()
        setattr(req, LEGACY_TURN_FRAGMENTS_ATTR, copy.deepcopy(fragments))
        if not fragments:
            req.prompt = base
            self._remove_owned_turn_part(req)
            setattr(req, TURN_TEXT_ATTR, "")
            setattr(req, TURN_PLACEMENT_ATTR, "none")
            return "none"

        turn_text = self._render_block_group(
            self.blocks(placement=PLACEMENT_TURN_TAIL, include_materialized=False)
        )
        managed = turn_text
        setattr(req, TURN_TEXT_ATTR, managed)
        if self._prefer_extra_user_content:
            req.prompt = base
            self._remove_owned_turn_part(req)
            try:
                if not isinstance(getattr(req, "extra_user_content_parts", None), list):
                    req.extra_user_content_parts = []
                part = TextPart(text=managed)
                marker = getattr(part, "mark_as_temp", None)
                if callable(marker):
                    part = marker()
                try:
                    setattr(part, TURN_PART_ATTR, True)
                except Exception:
                    pass
                req.extra_user_content_parts.append(part)
                setattr(req, TURN_PLACEMENT_ATTR, "extra_user_content_parts")
                return "extra_user_content_parts"
            except Exception:
                self._prefer_extra_user_content = False

        req.prompt = f"{base}\n\n{managed}".strip() if base else managed
        self._remove_owned_turn_part(req)
        setattr(req, TURN_PLACEMENT_ATTR, "prompt")
        return "prompt"


def get_conversation_injection_plan(req: Any, *, create: bool = True) -> ConversationInjectionPlan | None:
    plan = getattr(req, PLAN_ATTR, None)
    if isinstance(plan, ConversationInjectionPlan):
        return plan
    if not create or req is None:
        return None
    plan = ConversationInjectionPlan()
    setattr(req, PLAN_ATTR, plan)
    return plan


__all__ = [
    "ConversationInjectionBlock",
    "ConversationInjectionPlan",
    "PLACEMENT_DYNAMIC_SYSTEM",
    "PLACEMENT_STABLE_SYSTEM",
    "PLACEMENT_TOOL_CONTRACT",
    "PLACEMENT_TURN_TAIL",
    "PLAN_ATTR",
    "get_conversation_injection_plan",
]
