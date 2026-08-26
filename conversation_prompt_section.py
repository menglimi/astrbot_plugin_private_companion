"""Explicit XML sections for plugin context sent to conversation LLMs."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from xml.sax.saxutils import escape


@dataclass(frozen=True)
class PromptSection:
    title: str
    content: Any


@dataclass(frozen=True)
class XmlElement:
    """Typed XML node; callers provide data, never pre-rendered XML."""

    tag: str
    attrs: Mapping[str, Any] = field(default_factory=dict)
    text: Any = None
    children: tuple["XmlElement", ...] = ()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", self.tag):
            raise ValueError(f"invalid XML element tag: {self.tag!r}")
        if not isinstance(self.attrs, Mapping):
            raise TypeError("XML attributes must be a mapping")
        for key, value in self.attrs.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", str(key or "")):
                raise ValueError(f"invalid XML attribute name: {key!r}")
            if isinstance(value, (Mapping, list, tuple, set, XmlElement)):
                raise TypeError(f"XML attribute {key!r} must be scalar")
        if not isinstance(self.children, tuple) or not all(
            isinstance(child, XmlElement) for child in self.children
        ):
            raise TypeError("XML children must be a tuple of XmlElement instances")


def xml_element(
    tag: str,
    *,
    attrs: Mapping[str, Any] | None = None,
    text: Any = None,
    children: Iterable[XmlElement] = (),
) -> XmlElement:
    normalized_tag = str(tag or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", normalized_tag):
        raise ValueError(f"invalid XML element tag: {tag!r}")
    normalized_attrs: dict[str, Any] = {}
    for raw_key, value in (attrs or {}).items():
        key = str(raw_key or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", key):
            raise ValueError(f"invalid XML attribute name: {raw_key!r}")
        if isinstance(value, (Mapping, list, tuple, set, XmlElement)):
            raise TypeError(f"XML attribute {key!r} must be scalar")
        normalized_attrs[key] = value
    normalized_children = tuple(children)
    if not all(isinstance(child, XmlElement) for child in normalized_children):
        raise TypeError("XML children must be XmlElement instances")
    return XmlElement(
        tag=normalized_tag,
        attrs=normalized_attrs,
        text=text,
        children=normalized_children,
    )


def _normalize_title(value: Any) -> str:
    normalized = " ".join(str(value or "").split()).strip()[:80]
    return normalized or "提示词片段"


def prompt_section(title: Any, content: Any) -> dict[str, Any]:
    """Create the portable mapping form accepted by all prompt surfaces."""

    return {"title": _normalize_title(title), "content": content}


def coerce_prompt_section(value: Any) -> PromptSection | None:
    """Normalize a PromptSection or a ``{title, content}`` mapping."""

    if isinstance(value, PromptSection):
        return PromptSection(_normalize_title(value.title), value.content)
    if isinstance(value, Mapping) and "title" in value and "content" in value:
        return PromptSection(_normalize_title(value.get("title")), value.get("content"))
    return None


def title_for_prompt_key(key: Any, explicit_title: Any = "") -> str:
    title = " ".join(str(explicit_title or "").split()).strip()
    return _normalize_title(title)


def _has_content(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (Mapping, list, tuple)):
        return bool(value)
    return True


def _xml_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    # XML 1.0 rejects control characters, noncharacters and isolated surrogates.
    text = "".join(
        char
        for char in str(value)
        if (
            char in "\t\n\r"
            or 0x20 <= ord(char) <= 0xD7FF
            or 0xE000 <= ord(char) <= 0xFFFD
            or 0x10000 <= ord(char) <= 0x10FFFF
        )
    )
    return text


def _xml_text(value: Any) -> str:
    return escape(_xml_string(value))


def _xml_attribute(value: Any) -> str:
    return escape(_xml_string(value), {'"': "&quot;", "'": "&apos;"})


def _xml_tag(value: Any, fallback: str = "item") -> str:
    text = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(value or "")).strip("_.-")
    if not text or not re.match(r"^[A-Za-z_]", text):
        return fallback
    return text


def _list_item_tag(parent: str) -> str:
    return {
        "history": "message",
        "constraints": "constraint",
        "items": "item",
        "evidence": "item",
    }.get(parent, "item")


def _render_xml_value(tag: str, value: Any) -> str:
    safe_tag = _xml_tag(tag)
    if isinstance(value, XmlElement):
        return f"<{safe_tag}>{_render_xml_element(value)}</{safe_tag}>"
    if isinstance(value, Mapping):
        body = "".join(_render_xml_value(str(key), item) for key, item in value.items())
        return f"<{safe_tag}>{body}</{safe_tag}>"
    if isinstance(value, (list, tuple)):
        item_tag = _list_item_tag(safe_tag)
        body = "".join(_render_xml_value(item_tag, item) for item in value)
        return f"<{safe_tag}>{body}</{safe_tag}>"
    return f"<{safe_tag}>{_xml_text(value)}</{safe_tag}>"


def _render_xml_element(element: XmlElement) -> str:
    attrs = "".join(
        f' {key}="{_xml_attribute(value)}"'
        for key, value in element.attrs.items()
        if value is not None
    )
    body = _xml_text(element.text) if element.text is not None else ""
    body += "".join(_render_xml_element(child) for child in element.children)
    if not body:
        return f"<{element.tag}{attrs}/>"
    return f"<{element.tag}{attrs}>{body}</{element.tag}>"


def _render_section(section: PromptSection) -> str:
    title = _xml_attribute(section.title)
    if isinstance(section.content, XmlElement):
        content = _render_xml_element(section.content)
    elif isinstance(section.content, Mapping):
        content = "".join(
            _render_xml_value(str(key), value)
            for key, value in section.content.items()
        )
    elif isinstance(section.content, (list, tuple)):
        content = "".join(_render_xml_value("item", value) for value in section.content)
    else:
        # The scalar is escaped but otherwise left intact, including newlines and
        # repeated spaces inside the business-owned body.
        content = _xml_text(section.content)
    return f'<section title="{title}">{content}</section>'


def render_prompt_section(title_or_section: Any, content: Any = None) -> str:
    section = coerce_prompt_section(title_or_section)
    if section is None:
        section = PromptSection(_normalize_title(title_or_section), content)
    return render_prompt_sections([section])


def render_prompt_sections(
    sections: Iterable[PromptSection | Mapping[str, Any]],
) -> str:
    """Render one compact root while preserving all business-content spacing."""

    payload: list[PromptSection] = []
    for raw in sections:
        section = coerce_prompt_section(raw)
        if section is not None and _has_content(section.content):
            payload.append(section)
    if not payload:
        return ""
    body = "".join(_render_section(section) for section in payload)
    return f"<private_companion_context>{body}</private_companion_context>"


__all__ = [
    "PromptSection",
    "XmlElement",
    "coerce_prompt_section",
    "xml_element",
    "prompt_section",
    "render_prompt_section",
    "render_prompt_sections",
    "title_for_prompt_key",
]
