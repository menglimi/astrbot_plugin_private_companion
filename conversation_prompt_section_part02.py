# -*- coding: utf-8 -*-
"""conversation_prompt_section 拆分件 part02（机械搬移，行为不变）。

函数 / 常量 / 类逐字搬自 conversation_prompt_section.py，仅调整模块级依赖的导入来源。
"""
import hashlib
import json
import string
from collections.abc import Iterable, Mapping, Sequence
from typing import Any
from xml.sax.saxutils import escape

try:  # package import
    from .conversation_prompt_section_part01 import (
        ExactText,
        PhotoPromptContent,
        PromptCData,
        PromptDocument,
        PromptDocumentPart,
        PromptField,
        PromptGroup,
        PromptHeadingRef,
        PromptList,
        PromptRenderMode,
        PromptRenderSpec,
        PromptSection,
        PromptTemplate,
        PromptText,
        XmlElement,
        _MISSING,
        _validate_xml_name,
    )
except ImportError:  # direct test/import from the plugin directory
    from conversation_prompt_section_part01 import (
        ExactText,
        PhotoPromptContent,
        PromptCData,
        PromptDocument,
        PromptDocumentPart,
        PromptField,
        PromptGroup,
        PromptHeadingRef,
        PromptList,
        PromptRenderMode,
        PromptRenderSpec,
        PromptSection,
        PromptTemplate,
        PromptText,
        XmlElement,
        _MISSING,
        _validate_xml_name,
    )


def prompt_section(
    *,
    key: str,
    title: str,
    source: str,
    content: Any = _MISSING,
    template: str | None = None,
    variables: Mapping[str, Any] | None = None,
    children: Iterable[PromptSection] = (),
    metadata: Mapping[str, Any] | None = None,
) -> PromptSection:
    """Create one strictly identified canonical prompt section."""

    if template is not None and content is not _MISSING:
        raise TypeError("prompt_section accepts either content or template, not both")
    if template is not None:
        if not isinstance(template, str):
            raise TypeError("prompt section template must be str")
        content = PromptTemplate(template=template, variables=dict(variables or {}))
    elif variables:
        raise TypeError("prompt_section variables require template")
    elif content is _MISSING:
        raise TypeError("prompt_section requires content or template")
    return PromptSection(
        key=key,
        title=title,
        source=source,
        content=content,
        children=tuple(children),
        metadata=dict(metadata or {}),
    )


def prompt_document(
    *,
    system: Iterable[PromptSection | PromptDocumentPart] = (),
    user: Iterable[PromptSection | PromptDocumentPart] = (),
    system_render: PromptRenderSpec | None = None,
    user_render: PromptRenderSpec | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> PromptDocument:
    def normalize(
        values: Iterable[PromptSection | PromptDocumentPart],
    ) -> tuple[tuple[PromptSection, ...], tuple[PromptDocumentPart, ...]]:
        parts: list[PromptDocumentPart] = []
        for value in values:
            if isinstance(value, PromptSection):
                parts.append(PromptDocumentPart(section=value))
            elif isinstance(value, PromptDocumentPart):
                parts.append(value)
            else:
                raise TypeError(
                    "prompt document channels require PromptSection or PromptDocumentPart values"
                )
        return tuple(part.section for part in parts), tuple(parts)

    system_sections, system_parts = normalize(system)
    user_sections, user_parts = normalize(user)
    return PromptDocument(
        system=system_sections,
        user=user_sections,
        system_parts=system_parts,
        user_parts=user_parts,
        system_render=system_render,
        user_render=user_render,
        metadata=dict(metadata or {}),
    )


def prompt_document_part(
    section: PromptSection,
    *,
    render_spec: PromptRenderSpec | None = None,
) -> PromptDocumentPart:
    return PromptDocumentPart(section=section, render_spec=render_spec)


def xml_element(
    tag: str,
    *,
    attrs: Mapping[str, Any] | None = None,
    text: Any = None,
    children: Iterable[Any] = (),
) -> XmlElement:
    return XmlElement(
        tag=tag,
        attrs=dict(attrs or {}),
        text=text,
        children=tuple(children),
    )


def _has_content(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, PromptCData):
        return _has_content(value.content)
    if isinstance(value, ExactText):
        return bool(value.text)
    if isinstance(value, PhotoPromptContent):
        return bool(value.positive or value.negative)
    if isinstance(value, PromptTemplate):
        return bool(value.template)
    if isinstance(value, PromptGroup):
        return any(_has_content(item) for item in value.parts)
    if isinstance(value, PromptText):
        return any(_has_content(item) for item in value.parts)
    if isinstance(value, PromptField):
        return _has_content(value.value)
    if isinstance(value, PromptList):
        return bool(value.items)
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple)):
        return bool(value)
    return True


def _xml_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
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


def _plain_content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, ExactText):
        return value.text
    if isinstance(value, PromptCData):
        return _plain_content(value.content)
    if isinstance(value, PromptTemplate):
        parts: list[str] = []
        for literal, field_name, _format_spec, _conversion in string.Formatter().parse(value.template):
            parts.append(literal)
            if field_name is not None:
                parts.append(_plain_content(value.variables[field_name]))
        return "".join(parts)
    if isinstance(value, PromptHeadingRef):
        return f"【{value.title}】" + ("\n" if value.newline else "")
    if isinstance(value, PhotoPromptContent):
        raise TypeError("PhotoPromptContent requires PHOTO_PROMPT rendering")
    if isinstance(value, PromptGroup):
        return value.separator.join(_plain_content(part) for part in value.parts)
    if isinstance(value, PromptText):
        return value.separator.join(_plain_content(part) for part in value.parts)
    if isinstance(value, PromptField):
        return f"{value.name}: {_plain_content(value.value)}"
    if isinstance(value, PromptList):
        return value.separator.join(f"{value.prefix}{_plain_content(item)}" for item in value.items)
    if isinstance(value, XmlElement):
        return _render_xml_element(value)
    if isinstance(value, Mapping):
        raise TypeError("raw Mapping is not prompt content; serialize it explicitly")
    if isinstance(value, (list, tuple)):
        return "\n".join(_plain_content(item) for item in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _cdata_text(value: Any) -> str:
    return _xml_string(_plain_content(value)).replace("]]>", "]]]]><![CDATA[>")


def _list_item_tag(parent: str) -> str:
    return {
        "history": "message",
        "constraints": "constraint",
        "items": "item",
        "evidence": "item",
    }.get(parent, "item")


def _render_xml_value(tag: str, value: Any) -> str:
    safe_tag = _validate_xml_name(tag, kind="XML value")
    if isinstance(value, PromptField):
        return _render_xml_value(value.name, value.value)
    if isinstance(value, PromptList):
        body = "".join(_render_xml_value(value.item_tag, item) for item in value.items)
        return f"<{value.tag}>{body}</{value.tag}>"
    if isinstance(value, XmlElement):
        return f"<{safe_tag}>{_render_xml_element(value)}</{safe_tag}>"
    if isinstance(value, Mapping):
        raise TypeError("raw Mapping is not XML prompt content; use typed XML nodes")
    if isinstance(value, (list, tuple)):
        item_tag = _list_item_tag(safe_tag)
        body = "".join(_render_xml_value(item_tag, item) for item in value)
        return f"<{safe_tag}>{body}</{safe_tag}>"
    if isinstance(value, PromptCData):
        return f"<{safe_tag}><![CDATA[{_cdata_text(value.content)}]]></{safe_tag}>"
    if isinstance(value, ExactText):
        raise ValueError("ExactText cannot be embedded in conversation XML")
    return f"<{safe_tag}>{_xml_text(_plain_content(value))}</{safe_tag}>"


def _render_xml_child(value: Any) -> str:
    if isinstance(value, PromptSection):
        return _render_xml_section(value)
    if isinstance(value, XmlElement):
        return _render_xml_element(value)
    if isinstance(value, PromptCData):
        return f"<![CDATA[{_cdata_text(value.content)}]]>"
    if isinstance(value, ExactText):
        raise ValueError("ExactText cannot be embedded in conversation XML")
    return _xml_text(_plain_content(value))


def _render_xml_element(element: XmlElement) -> str:
    attrs = "".join(
        f' {key}="{_xml_attribute(value)}"'
        for key, value in element.attrs.items()
        if value is not None
    )
    body = _render_xml_child(element.text) if element.text is not None else ""
    body += "".join(_render_xml_child(child) for child in element.children)
    if not body:
        return f"<{element.tag}{attrs}/>"
    return f"<{element.tag}{attrs}>{body}</{element.tag}>"


def _render_xml_content(value: Any) -> str:
    if isinstance(value, PromptCData):
        return f"<![CDATA[{_cdata_text(value.content)}]]>"
    if isinstance(value, ExactText):
        raise ValueError("ExactText requires the exact render mode")
    if isinstance(value, PromptGroup):
        separator = _xml_text(value.separator)
        return separator.join(_render_xml_content(item) for item in value.parts)
    if isinstance(value, PromptSection):
        raise TypeError("nested PromptSection must be declared through children")
    if isinstance(value, XmlElement):
        return _render_xml_element(value)
    if isinstance(value, PromptField):
        return _render_xml_value(value.name, value.value)
    if isinstance(value, PromptList):
        return _render_xml_value(value.tag, value)
    if isinstance(value, Mapping):
        raise TypeError("raw Mapping is not XML prompt content; use typed XML nodes")
    if isinstance(value, (list, tuple)):
        return "".join(_render_xml_value("item", item) for item in value)
    return _xml_text(_plain_content(value))


def _coerce_render_mode(value: PromptRenderMode | str) -> PromptRenderMode:
    if isinstance(value, PromptRenderMode):
        return value
    normalized = str(value or "").strip().lower()
    try:
        return PromptRenderMode(normalized)
    except ValueError as exc:
        raise ValueError(f"unsupported prompt render mode: {value!r}") from exc


def _render_conversation_xml(sections: Sequence[PromptSection]) -> str:
    visible = tuple(section for section in sections if _section_has_content(section))
    if not visible:
        return ""
    body = "".join(_render_xml_section(section) for section in visible)
    return f"<private_companion_context>{body}</private_companion_context>"


def _section_has_content(section: PromptSection) -> bool:
    return _has_content(section.content) or any(
        _section_has_content(child) for child in section.children
    )


def _render_xml_section(section: PromptSection) -> str:
    body = _render_xml_content(section.content)
    body += "".join(
        _render_xml_section(child)
        for child in section.children
        if _section_has_content(child)
    )
    return f'<section title="{_xml_attribute(section.title)}">{body}</section>'


def _render_labeled_section(section: PromptSection, *, inline: bool) -> str:
    if not _section_has_content(section):
        return ""
    separator = "" if inline else "\n"
    body = _plain_content(section.content)
    child_blocks = [
        _render_labeled_section(child, inline=False)
        for child in section.children
        if _section_has_content(child)
    ]
    content = "\n\n".join(part for part in (body, *child_blocks) if part)
    return f"【{section.title}】{separator}{content}"


def _render_labeled(sections: Sequence[PromptSection], *, inline: bool) -> str:
    return "\n\n".join(
        _render_labeled_section(section, inline=inline)
        for section in sections
    )


def _render_body_section(section: PromptSection) -> str:
    if not _section_has_content(section):
        return ""
    body = _plain_content(section.content)
    child_blocks = [
        _render_labeled_section(child, inline=False)
        for child in section.children
        if _section_has_content(child)
    ]
    return "\n\n".join(part for part in (body, *child_blocks) if part)


def _render_body_only(sections: Sequence[PromptSection]) -> str:
    return "\n\n".join(
        content
        for section in sections
        if (content := _render_body_section(section))
    )


def _render_exact(sections: Sequence[PromptSection]) -> str:
    bodies: list[str] = []
    for section in sections:
        if section.children:
            raise TypeError("exact render mode does not support child sections")
        if not isinstance(section.content, ExactText):
            raise TypeError("exact render mode requires ExactText content")
        bodies.append(section.content.text)
    return "".join(bodies)


def _render_photo_prompt(sections: Sequence[PromptSection]) -> str:
    """Render an already-authoritative photo payload without XML semantics.

    The photo pipeline owns positive/negative ordering and NAI weights. During
    Complete assembled wires may use ExactText, while authored photo sections
    carry PhotoPromptContent without changing this public mode.
    """

    if any(section.children for section in sections):
        raise TypeError("photo prompt render mode does not support child sections")
    bodies: list[str] = []
    for section in sections:
        if isinstance(section.content, ExactText):
            bodies.append(section.content.text)
        elif isinstance(section.content, PhotoPromptContent):
            bodies.append(section.content.positive)
        else:
            bodies.append(_render_body_section(section))
    return "\n\n".join(body for body in bodies if body)


def _fingerprint_scalar(value: Any) -> list[Any]:
    if value is None:
        return ["none"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, float):
        return ["float", value.hex()]
    if isinstance(value, str):
        return ["str", value]
    raise TypeError(f"unsupported fingerprint scalar: {type(value).__name__}")


def _fingerprint_content(value: Any) -> Any:
    if isinstance(value, PromptText):
        return [
            "text",
            value.separator,
            [_fingerprint_content(item) for item in value.parts],
        ]
    if isinstance(value, PromptGroup):
        return [
            "group",
            value.separator,
            [_fingerprint_content(item) for item in value.parts],
        ]
    if isinstance(value, PromptTemplate):
        return [
            "template",
            value.template,
            [
                [name, _fingerprint_content(item)]
                for name, item in sorted(value.variables.items())
            ],
        ]
    if isinstance(value, PromptHeadingRef):
        return ["heading_ref", value.title, value.newline]
    if isinstance(value, PhotoPromptContent):
        return [
            "photo_prompt",
            value.positive,
            value.negative,
            value.domain_source,
            value.protected,
            value.sanitize_conflicts,
        ]
    if isinstance(value, PromptCData):
        return ["cdata", _fingerprint_content(value.content)]
    if isinstance(value, ExactText):
        return ["exact", value.text]
    if isinstance(value, PromptField):
        return ["field", value.name, _fingerprint_content(value.value)]
    if isinstance(value, PromptList):
        return [
            "list_node",
            value.tag,
            value.item_tag,
            value.prefix,
            value.separator,
            [_fingerprint_content(item) for item in value.items],
        ]
    if isinstance(value, XmlElement):
        return [
            "xml",
            value.tag,
            [
                [name, _fingerprint_scalar(item)]
                for name, item in sorted(value.attrs.items())
            ],
            _fingerprint_content(value.text) if value.text is not None else None,
            [_fingerprint_content(item) for item in value.children],
        ]
    if isinstance(value, list):
        return ["list", [_fingerprint_content(item) for item in value]]
    if isinstance(value, tuple):
        return ["tuple", [_fingerprint_content(item) for item in value]]
    if isinstance(value, Mapping):
        raise TypeError("raw Mapping is not prompt content")
    return _fingerprint_scalar(value)


def _fingerprint_section_payload(section: PromptSection) -> list[Any]:
    return [
        "section",
        section.key,
        section.title,
        section.source,
        _fingerprint_content(section.content),
        [_fingerprint_section_payload(child) for child in section.children],
    ]


def prompt_section_fingerprint(section: PromptSection) -> str:
    """Return a stable metadata-independent fingerprint for one section tree."""

    if not isinstance(section, PromptSection):
        raise TypeError("prompt_section_fingerprint requires PromptSection")
    payload = json.dumps(
        _fingerprint_section_payload(section),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
