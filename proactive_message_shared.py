# -*- coding: utf-8 -*-
"""proactive_message 域的跨域共享件。

由 tools/promote_to_shared.py 从 proactive_message.py 机械提升，
代码逐字节原样（方法体零改动）。

为什么需要这个模块：下面这几个名字被 **8 个 / 6 个** 域的方法共同引用。
任何一个域独占搬走都会让其余域断链；留在宿主则新拆出的域模块无法 import
（域模块 ← 宿主 会与 宿主 ← 域模块 形成循环 import）。

因此提升为独立的「叶子模块」：它不 import 任何域模块，任何人都可以安全 import 它。
"""
from __future__ import annotations

from typing import Any

from .conversation_prompt_section import (
    PromptDocumentPart,
    PromptLabel,
    PromptLabelStyle,
    PromptRenderMode,
    PromptRenderSpec,
    PromptSection,
    exact_text,
    prompt_document_part,
)
from .persona_config import runtime_persona_setting


_PROACTIVE_DOCUMENT_RENDER = PromptRenderSpec(
    mode=PromptRenderMode.LABELED_BLOCK,
    trim=True,
)


def _proactive_prompt_part(
    section: PromptSection,
    *,
    mode: PromptRenderMode | None = None,
    label_style: PromptLabelStyle | None = None,
    prefix: str = "",
    separator_before: str = "\n\n",
) -> PromptDocumentPart:
    label = (
        PromptLabel(style=label_style, separator=exact_text("\n"))
        if label_style is not None
        else None
    )
    return prompt_document_part(
        section,
        render_spec=PromptRenderSpec(
            mode=PromptRenderMode.BODY_ONLY if label is not None else mode,
            label=label,
            prefix=exact_text(prefix) if prefix else None,
            separator_before=exact_text(separator_before),
            trim=True,
        ),
    )


def _persona_provider_id(owner: Any, canonical_key: str, legacy_attr: str, quick_role: str) -> str:
    """Resolve canonical persona provider settings while preserving test harnesses."""
    fallback = str(getattr(owner, legacy_attr, "") or "").strip()
    if not callable(getattr(owner, "persona_setting", None)):
        return fallback
    mode = str(getattr(owner, "provider_config_mode", "quick") or "quick").strip().lower()
    if mode != "quick":
        return str(runtime_persona_setting(owner, canonical_key, fallback) or "").strip()
    complex_id = str(runtime_persona_setting(owner, "COMPLEX_REASONING_PROVIDER_ID", "") or "").strip()
    if quick_role == "complex":
        return complex_id or fallback
    if quick_role == "creative":
        creative_id = str(runtime_persona_setting(owner, "CREATIVE_MODEL_PROVIDER_ID", "") or "").strip()
        return creative_id or complex_id or fallback
    fast_id = str(runtime_persona_setting(owner, "FAST_RESPONSE_PROVIDER_ID", "") or "").strip()
    return fast_id or complex_id or fallback
