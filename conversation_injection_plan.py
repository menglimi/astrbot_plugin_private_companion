"""Request-scoped assembly plan for main-conversation prompt injections."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from astrbot.core.agent.message import TextPart


PLAN_ATTR = "_private_companion_conversation_injection_plan"
LEGACY_TURN_FRAGMENTS_ATTR = "_private_companion_turn_prompt_fragments"
TURN_PLACEMENT_ATTR = "_private_companion_turn_prompt_placement"
TURN_PART_ATTR = "_private_companion_turn_fragments"
TURN_START_MARKER = "<!-- private_companion_turn_fragments_start -->"
TURN_END_MARKER = "<!-- private_companion_turn_fragments_end -->"

PLACEMENT_STABLE_SYSTEM = "stable_system"
PLACEMENT_DYNAMIC_SYSTEM = "dynamic_system"
PLACEMENT_TURN_TAIL = "turn_tail"
_PLACEMENTS = {
    PLACEMENT_STABLE_SYSTEM,
    PLACEMENT_DYNAMIC_SYSTEM,
    PLACEMENT_TURN_TAIL,
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


@dataclass
class ConversationInjectionBlock:
    key: str
    marker: str
    content: str
    priority: int = 50
    source: str = ""
    placement: str = PLACEMENT_TURN_TAIL
    temporary: bool = True
    merge_policy: str = "first"
    materialized: bool = False
    index: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    children: list[dict[str, Any]] = field(default_factory=list)

    def manifest_item(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "marker": self.marker,
            "source": self.source,
            "priority": self.priority,
            "placement": self.placement,
            "temporary": self.temporary,
            "merge_policy": self.merge_policy,
            "materialized": self.materialized,
            "index": self.index,
            "chars": len(self.content),
            "content": self.content,
            "metadata": copy.deepcopy(self.metadata),
            "children": copy.deepcopy(self.children),
        }


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
        content: str,
        priority: int = 50,
        source: str = "",
        placement: str = PLACEMENT_TURN_TAIL,
        temporary: bool = True,
        merge_policy: str = "first",
        materialized: bool = False,
        metadata: dict[str, Any] | None = None,
        children: Iterable[dict[str, Any]] | None = None,
    ) -> ConversationInjectionBlock | None:
        text = str(content or "").strip()
        if not text:
            return None
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

        existing = self._by_key.get(normalized_key)
        child_items = [copy.deepcopy(item) for item in (children or []) if isinstance(item, dict)]
        if existing is not None:
            if normalized_merge == "first":
                return existing
            if normalized_merge == "append":
                if text != existing.content and text not in existing.content.split("\n\n"):
                    existing.content = f"{existing.content}\n\n{text}".strip()
                existing.children.extend(child_items)
                if metadata:
                    existing.metadata.update(copy.deepcopy(metadata))
                return existing
            existing.marker = normalized_marker
            existing.content = text
            existing.priority = int(priority)
            existing.source = _clean_key(source, "")[:80]
            existing.placement = normalized_placement
            existing.temporary = bool(temporary)
            existing.merge_policy = normalized_merge
            existing.materialized = bool(materialized)
            existing.metadata = copy.deepcopy(metadata or {})
            existing.children = child_items
            return existing

        block = ConversationInjectionBlock(
            key=normalized_key,
            marker=normalized_marker,
            content=text,
            priority=int(priority),
            source=_clean_key(source, "")[:80],
            placement=normalized_placement,
            temporary=bool(temporary),
            merge_policy=normalized_merge,
            materialized=bool(materialized),
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
            if not block.marker or not block.content:
                continue
            if block.marker in seen_markers or block.content in seen_content:
                continue
            seen_markers.add(block.marker)
            seen_content.add(block.content)
            fragments.append(
                {
                    "marker": block.marker,
                    "content": block.content,
                    "priority": block.priority,
                    "source": block.source,
                    "index": block.index,
                }
            )
        return fragments

    def manifest(self) -> list[dict[str, Any]]:
        return [block.manifest_item() for block in self.blocks()]

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

    @staticmethod
    def _base_prompt(req: Any) -> str:
        current = str(getattr(req, "prompt", "") or "")
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
        rendered: list[str] = []
        for placement in (PLACEMENT_STABLE_SYSTEM, PLACEMENT_DYNAMIC_SYSTEM):
            for block in self.blocks(placement=placement, include_materialized=False):
                rendered.append(f"{block.marker}\n{block.content}".strip() if block.marker else block.content)
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
            setattr(req, TURN_PLACEMENT_ATTR, "none")
            return "none"

        rendered = [f"{item['marker']}\n{item['content']}" for item in fragments]
        managed = f"{TURN_START_MARKER}\n" + "\n\n".join(rendered) + f"\n{TURN_END_MARKER}"
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
    "PLACEMENT_TURN_TAIL",
    "PLAN_ATTR",
    "get_conversation_injection_plan",
]
