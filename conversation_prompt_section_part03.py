# -*- coding: utf-8 -*-
"""conversation_prompt_section 拆分件 part03（机械搬移，行为不变）。

函数 / 常量 / 类逐字搬自 conversation_prompt_section.py，仅调整模块级依赖的导入来源。
"""
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

try:  # package import
    from .conversation_prompt_section_part01 import (
        ExactText,
        PhotoPromptContent,
        PromptDocument,
        PromptDocumentPart,
        PromptLabel,
        PromptLabelStyle,
        PromptRenderMode,
        PromptRenderSpec,
        PromptSection,
        _validate_prompt_content,
    )
except ImportError:  # direct test/import from the plugin directory
    from conversation_prompt_section_part01 import (
        ExactText,
        PhotoPromptContent,
        PromptDocument,
        PromptDocumentPart,
        PromptLabel,
        PromptLabelStyle,
        PromptRenderMode,
        PromptRenderSpec,
        PromptSection,
        _validate_prompt_content,
    )
try:  # package import
    from .conversation_prompt_section_part02 import (
        _coerce_render_mode,
        _plain_content,
        _render_body_only,
        _render_conversation_xml,
        _render_exact,
        _render_labeled,
        _render_photo_prompt,
        _render_xml_content,
    )
except ImportError:  # direct test/import from the plugin directory
    from conversation_prompt_section_part02 import (
        _coerce_render_mode,
        _plain_content,
        _render_body_only,
        _render_conversation_xml,
        _render_exact,
        _render_labeled,
        _render_photo_prompt,
        _render_xml_content,
    )


def render_prompt_content(
    content: Any,
    *,
    mode: PromptRenderMode | str = PromptRenderMode.BODY_ONLY,
) -> str:
    """Render one typed content node without inventing a section identity."""

    _validate_prompt_content(content, location="prompt content")
    render_mode = _coerce_render_mode(mode)
    if isinstance(content, PhotoPromptContent) and render_mode is not PromptRenderMode.PHOTO_PROMPT:
        raise TypeError("PhotoPromptContent requires PHOTO_PROMPT rendering")
    if render_mode is PromptRenderMode.BODY_ONLY:
        return _plain_content(content)
    if render_mode is PromptRenderMode.CONVERSATION_XML:
        return _render_xml_content(content)
    if render_mode is PromptRenderMode.EXACT:
        if not isinstance(content, ExactText):
            raise TypeError("exact render mode requires ExactText content")
        return content.text
    if render_mode is PromptRenderMode.PHOTO_PROMPT:
        if isinstance(content, ExactText):
            return content.text
        if isinstance(content, PhotoPromptContent):
            return content.positive
        return _plain_content(content)
    raise ValueError("labeled prompt content requires a section title")


def render_prompt_sections(
    sections: Iterable[PromptSection],
    *,
    mode: PromptRenderMode | str = PromptRenderMode.CONVERSATION_XML,
) -> str:
    """Render authored sections without changing business-content spacing."""

    payload = tuple(sections)
    if not all(isinstance(section, PromptSection) for section in payload):
        raise TypeError("render_prompt_sections requires PromptSection values")
    render_mode = _coerce_render_mode(mode)
    if render_mode is PromptRenderMode.CONVERSATION_XML:
        return _render_conversation_xml(payload)
    if render_mode is PromptRenderMode.LABELED_BLOCK:
        return _render_labeled(payload, inline=False)
    if render_mode is PromptRenderMode.LABELED_INLINE:
        return _render_labeled(payload, inline=True)
    if render_mode is PromptRenderMode.BODY_ONLY:
        return _render_body_only(payload)
    if render_mode is PromptRenderMode.EXACT:
        return _render_exact(payload)
    if render_mode is PromptRenderMode.PHOTO_PROMPT:
        return _render_photo_prompt(payload)
    raise AssertionError(f"unhandled prompt render mode: {render_mode}")


def _render_prompt_label(section: PromptSection, label: PromptLabel) -> str:
    if label.style is PromptLabelStyle.SQUARE:
        return f"[{section.title}]"
    if label.style is PromptLabelStyle.COLON:
        return f"{section.title}:"
    if label.style is PromptLabelStyle.FULLWIDTH_COLON:
        return f"{section.title}："
    raise AssertionError(f"unhandled prompt label style: {label.style}")


def _render_prompt_document_channel(
    parts: Sequence[PromptDocumentPart],
    *,
    default_mode: PromptRenderMode | str,
    default_spec: PromptRenderSpec | None,
) -> str:
    if not parts:
        return ""
    if default_spec is None and all(part.render_spec is None for part in parts):
        return render_prompt_sections(
            (part.section for part in parts),
            mode=default_mode,
        )

    inherited_mode = _coerce_render_mode(default_mode)
    rendered_document = ""
    for part in parts:
        spec = part.render_spec or default_spec or PromptRenderSpec()
        mode = spec.mode or inherited_mode
        if spec.label is not None:
            body = render_prompt_sections(
                (part.section,),
                mode=PromptRenderMode.BODY_ONLY,
            )
            label = _render_prompt_label(part.section, spec.label)
            rendered = (
                f"{label}{spec.label.separator.text}{body}"
                if body
                else label
            )
        else:
            rendered = render_prompt_sections((part.section,), mode=mode)
        if spec.trim:
            rendered = rendered.strip()
        if spec.prefix is not None and rendered:
            rendered = (
                f"{spec.prefix.text}{spec.prefix_separator.text}{rendered}"
            )
        if not rendered:
            continue
        rendered_document = (
            f"{rendered_document}{spec.separator_before.text}{rendered}"
            if rendered_document
            else rendered
        )
    return rendered_document


def render_prompt_document(
    document: PromptDocument,
    *,
    mode: PromptRenderMode | str | None = None,
    system_mode: PromptRenderMode | str = PromptRenderMode.LABELED_BLOCK,
    user_mode: PromptRenderMode | str = PromptRenderMode.LABELED_BLOCK,
) -> dict[str, str]:
    if not isinstance(document, PromptDocument):
        raise TypeError("render_prompt_document requires PromptDocument")
    if mode is not None:
        system_mode = mode
        user_mode = mode
    return {
        "system": _render_prompt_document_channel(
            document.system_parts,
            default_mode=system_mode,
            default_spec=document.system_render,
        ),
        "user": _render_prompt_document_channel(
            document.user_parts,
            default_mode=user_mode,
            default_spec=document.user_render,
        ),
    }
