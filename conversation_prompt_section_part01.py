# -*- coding: utf-8 -*-
"""conversation_prompt_section 拆分件 part01（机械搬移，行为不变）。

函数 / 常量 / 类逐字搬自 conversation_prompt_section.py，仅调整模块级依赖的导入来源。
"""
import copy
import re
import string
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from types import MappingProxyType


class PromptRenderMode(str, Enum):
    """Wire formats supported by the canonical prompt renderer."""

    CONVERSATION_XML = "conversation_xml"
    LABELED_BLOCK = "labeled_block"
    LABELED_INLINE = "labeled_inline"
    BODY_ONLY = "body_only"
    EXACT = "exact"
    PHOTO_PROMPT = "photo_prompt"


class PromptLabelStyle(str, Enum):
    """Non-canonical labels required by existing background prompt wires."""

    SQUARE = "square"
    COLON = "colon"
    FULLWIDTH_COLON = "fullwidth_colon"


@dataclass(frozen=True, slots=True)
class PromptText:
    """Ordered text fragments joined without implicit whitespace changes."""

    parts: tuple[Any, ...]
    separator: str = ""


@dataclass(frozen=True, slots=True)
class PromptHeadingRef:
    """A typed reference to a labeled heading inside prompt body text."""

    title: str
    newline: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _validate_prompt_title(self.title))
        if not isinstance(self.newline, bool):
            raise TypeError("prompt heading reference newline must be bool")


@dataclass(frozen=True, slots=True)
class PromptGroup:
    """Ordered structured content rendered without flattening child types."""

    parts: tuple[Any, ...]
    separator: str = "\n\n"


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """A validated template whose variables remain typed until rendering."""

    template: str
    variables: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        template = str(self.template)
        variables = dict(self.variables)
        referenced: set[str] = set()
        try:
            parsed = tuple(string.Formatter().parse(template))
        except ValueError as exc:
            raise ValueError(f"invalid prompt template: {exc}") from exc
        for _literal, field_name, format_spec, conversion in parsed:
            if field_name is None:
                continue
            if not field_name or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", field_name):
                raise ValueError(f"invalid prompt template variable: {field_name!r}")
            if format_spec or conversion:
                raise ValueError("prompt template variables do not support format specs or conversions")
            referenced.add(field_name)
        supplied = set(variables)
        missing = sorted(referenced - supplied)
        unused = sorted(supplied - referenced)
        if missing:
            raise ValueError(f"missing prompt template variables: {', '.join(missing)}")
        if unused:
            raise ValueError(f"unused prompt template variables: {', '.join(unused)}")
        for name, value in variables.items():
            _validate_prompt_content(
                value,
                location=f"template variable {name!r}",
            )
        object.__setattr__(self, "template", template)
        object.__setattr__(self, "variables", MappingProxyType(variables))


@dataclass(frozen=True, slots=True)
class PromptCData:
    """Content that must remain visibly literal inside conversation XML."""

    content: Any


@dataclass(frozen=True, slots=True)
class ExactText:
    """A byte-sensitive text contract that must not be normalized or escaped."""

    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("exact prompt text must be str")


@dataclass(frozen=True, slots=True)
class PhotoPromptContent:
    """Typed positive/negative payload owned by the photo prompt domain."""

    positive: str = ""
    negative: str = ""
    domain_source: str = ""
    protected: bool = False
    sanitize_conflicts: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.positive, str):
            raise TypeError("photo prompt positive content must be str")
        if not isinstance(self.negative, str):
            raise TypeError("photo prompt negative content must be str")
        if not isinstance(self.domain_source, str) or not self.domain_source:
            raise ValueError("photo prompt domain source must be a non-empty str")
        if not isinstance(self.protected, bool):
            raise TypeError("photo prompt protected flag must be bool")
        if self.sanitize_conflicts is not None and not isinstance(
            self.sanitize_conflicts,
            bool,
        ):
            raise TypeError("photo prompt sanitize_conflicts must be bool or None")


@dataclass(frozen=True, slots=True)
class PromptLabel:
    """One typed label override for a document part."""

    style: PromptLabelStyle
    separator: ExactText = field(default_factory=lambda: ExactText("\n"))

    def __post_init__(self) -> None:
        if not isinstance(self.style, PromptLabelStyle):
            raise TypeError("prompt label style must be PromptLabelStyle")
        if not isinstance(self.separator, ExactText):
            raise TypeError("prompt label separator must be ExactText")


@dataclass(frozen=True, slots=True)
class PromptRenderSpec:
    """Typed wire-layout controls for one prompt document part."""

    mode: PromptRenderMode | None = None
    label: PromptLabel | None = None
    prefix: ExactText | None = None
    prefix_separator: ExactText = field(default_factory=lambda: ExactText("\n"))
    separator_before: ExactText = field(default_factory=lambda: ExactText("\n\n"))
    trim: bool = False

    def __post_init__(self) -> None:
        if self.mode is not None and not isinstance(self.mode, PromptRenderMode):
            raise TypeError("prompt render spec mode must be PromptRenderMode or None")
        if self.label is not None and not isinstance(self.label, PromptLabel):
            raise TypeError("prompt render spec label must be PromptLabel or None")
        if self.prefix is not None and not isinstance(self.prefix, ExactText):
            raise TypeError("prompt render spec prefix must be ExactText or None")
        if not isinstance(self.prefix_separator, ExactText):
            raise TypeError("prompt render spec prefix separator must be ExactText")
        if not isinstance(self.separator_before, ExactText):
            raise TypeError("prompt render spec separator must be ExactText")
        if not isinstance(self.trim, bool):
            raise TypeError("prompt render spec trim must be bool")
        if self.label is not None and self.mode not in {None, PromptRenderMode.BODY_ONLY}:
            raise ValueError("prompt label overrides require body-only rendering")


@dataclass(frozen=True, slots=True)
class PromptField:
    """One named structured field."""

    name: str
    value: Any

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "name",
            _validate_xml_name(self.name, kind="prompt field"),
        )


@dataclass(frozen=True, slots=True)
class PromptList:
    """An explicitly named ordered collection."""

    items: tuple[Any, ...]
    tag: str = "items"
    item_tag: str = "item"
    prefix: str = ""
    separator: str = "\n"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "tag",
            _validate_xml_name(self.tag, kind="prompt list"),
        )
        object.__setattr__(
            self,
            "item_tag",
            _validate_xml_name(self.item_tag, kind="prompt list item"),
        )


@dataclass(frozen=True, slots=True)
class XmlElement:
    """Typed XML node; callers provide data, never pre-rendered XML."""

    tag: str
    attrs: Mapping[str, Any] = field(default_factory=dict)
    text: Any = None
    children: tuple[Any, ...] = ()

    def __post_init__(self) -> None:
        tag = _validate_xml_name(self.tag, kind="XML element")
        if not isinstance(self.attrs, Mapping):
            raise TypeError("XML attributes must be a mapping")
        normalized_attrs: dict[str, Any] = {}
        for key, value in self.attrs.items():
            normalized_key = _validate_xml_name(key, kind="XML attribute")
            if isinstance(
                value,
                (
                    Mapping,
                    list,
                    tuple,
                    set,
                    XmlElement,
                    PromptGroup,
                    PromptText,
                    PromptTemplate,
                    PromptCData,
                ),
            ):
                raise TypeError(f"XML attribute {key!r} must be scalar")
            if value is not None and not isinstance(value, (str, bool, int, float)):
                raise TypeError(f"XML attribute {key!r} must be scalar")
            normalized_attrs[normalized_key] = value
        normalized_children = tuple(self.children)
        allowed_children = (
            XmlElement,
            PromptGroup,
            PromptHeadingRef,
            PromptText,
            PromptTemplate,
            PromptCData,
            ExactText,
            str,
        )
        if not all(isinstance(child, allowed_children) for child in normalized_children):
            raise TypeError("XML children must be typed prompt content")
        object.__setattr__(self, "tag", tag)
        object.__setattr__(self, "attrs", MappingProxyType(normalized_attrs))
        object.__setattr__(self, "children", normalized_children)


@dataclass(frozen=True, slots=True)
class PromptSection:
    """One strictly identified, immutable prompt-authoring unit."""

    key: str
    title: str
    source: str
    content: Any
    children: tuple["PromptSection", ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        key = _validate_prompt_identity(self.key, kind="key", limit=160)
        title = _validate_prompt_title(self.title)
        source = _validate_prompt_identity(self.source, kind="source", limit=80)
        children = tuple(self.children)
        if not all(isinstance(child, PromptSection) for child in children):
            raise TypeError("prompt section children must contain only PromptSection values")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("prompt section metadata must be a mapping")
        _validate_prompt_content(self.content, location=f"section {key!r} content")
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "children", children)
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def __copy__(self) -> "PromptSection":
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> "PromptSection":
        return PromptSection(
            key=self.key,
            title=self.title,
            source=self.source,
            content=copy.deepcopy(self.content, memo),
            children=copy.deepcopy(self.children, memo),
            metadata=copy.deepcopy(dict(self.metadata), memo),
        )


@dataclass(frozen=True, slots=True)
class PromptDocumentPart:
    """One authored section plus its sink-specific rendering contract."""

    section: PromptSection
    render_spec: PromptRenderSpec | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.section, PromptSection):
            raise TypeError("prompt document part requires PromptSection")
        if self.render_spec is not None and not isinstance(
            self.render_spec,
            PromptRenderSpec,
        ):
            raise TypeError("prompt document part render spec must be PromptRenderSpec or None")


@dataclass(frozen=True, slots=True)
class PromptDocument:
    """A channel-aware collection; all authored content remains sections."""

    system: tuple[PromptSection, ...] = ()
    user: tuple[PromptSection, ...] = ()
    system_parts: tuple[PromptDocumentPart, ...] = ()
    user_parts: tuple[PromptDocumentPart, ...] = ()
    system_render: PromptRenderSpec | None = None
    user_render: PromptRenderSpec | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        system_sections = tuple(self.system)
        user_sections = tuple(self.user)
        if not all(isinstance(item, PromptSection) for item in (*system_sections, *user_sections)):
            raise TypeError("prompt document channels must contain PromptSection instances")
        system_parts = tuple(self.system_parts) or tuple(
            PromptDocumentPart(section=section) for section in system_sections
        )
        user_parts = tuple(self.user_parts) or tuple(
            PromptDocumentPart(section=section) for section in user_sections
        )
        if not all(
            isinstance(item, PromptDocumentPart)
            for item in (*system_parts, *user_parts)
        ):
            raise TypeError("prompt document parts must contain PromptDocumentPart instances")
        if tuple(part.section for part in system_parts) != system_sections:
            raise ValueError("prompt document system parts do not match system sections")
        if tuple(part.section for part in user_parts) != user_sections:
            raise ValueError("prompt document user parts do not match user sections")
        if self.system_render is not None and not isinstance(
            self.system_render,
            PromptRenderSpec,
        ):
            raise TypeError("prompt document system render must be PromptRenderSpec or None")
        if self.user_render is not None and not isinstance(
            self.user_render,
            PromptRenderSpec,
        ):
            raise TypeError("prompt document user render must be PromptRenderSpec or None")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("prompt document metadata must be a mapping")
        object.__setattr__(self, "system", system_sections)
        object.__setattr__(self, "user", user_sections)
        object.__setattr__(self, "system_parts", system_parts)
        object.__setattr__(self, "user_parts", user_parts)
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


def _validate_xml_name(value: Any, *, kind: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{kind} must be str")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", value):
        raise ValueError(f"invalid {kind} name: {value!r}")
    return value


def _validate_prompt_identity(value: Any, *, kind: str, limit: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"prompt {kind} must be str")
    if not value or len(value) > limit or not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
        raise ValueError(f"invalid prompt {kind}: {value!r}")
    return value


def _validate_prompt_title(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("prompt title must be str")
    if (
        not value
        or len(value) > 80
        or value != value.strip()
        or any(char in value for char in "\r\n\t")
    ):
        raise ValueError(f"invalid prompt title: {value!r}")
    return value


def _validate_prompt_content(value: Any, *, location: str) -> None:
    if isinstance(value, PromptSection):
        raise TypeError(f"{location} cannot contain PromptSection; use children")
    if isinstance(value, Mapping):
        raise TypeError(f"{location} cannot contain raw Mapping content")
    if isinstance(value, (PromptText, PromptGroup)):
        for index, item in enumerate(value.parts):
            _validate_prompt_content(item, location=f"{location} part {index}")
        return
    if isinstance(value, PromptHeadingRef):
        return
    if isinstance(value, PhotoPromptContent):
        return
    if isinstance(value, PromptTemplate):
        for name, item in value.variables.items():
            _validate_prompt_content(
                item,
                location=f"{location} variable {name!r}",
            )
        return
    if isinstance(value, PromptCData):
        _validate_prompt_content(value.content, location=f"{location} CDATA")
        return
    if isinstance(value, PromptField):
        _validate_prompt_content(value.value, location=f"{location} field {value.name!r}")
        return
    if isinstance(value, PromptList):
        for index, item in enumerate(value.items):
            _validate_prompt_content(item, location=f"{location} item {index}")
        return
    if isinstance(value, XmlElement):
        if value.text is not None:
            _validate_prompt_content(value.text, location=f"{location} XML text")
        for index, child in enumerate(value.children):
            _validate_prompt_content(child, location=f"{location} XML child {index}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_prompt_content(item, location=f"{location} item {index}")
        return
    if value is None or isinstance(value, (str, bool, int, float, ExactText)):
        return
    raise TypeError(f"{location} has unsupported type {type(value).__name__}")


def prompt_text(*parts: Any, separator: str = "") -> PromptText:
    return PromptText(parts=tuple(parts), separator=str(separator))


def prompt_heading_ref(title: str, *, newline: bool = False) -> PromptHeadingRef:
    return PromptHeadingRef(title=title, newline=newline)


def prompt_group(*parts: Any, separator: str = "\n\n") -> PromptGroup:
    return PromptGroup(parts=tuple(parts), separator=str(separator))


def prompt_cdata(content: Any) -> PromptCData:
    return PromptCData(content=content)


def exact_text(text: str) -> ExactText:
    return ExactText(text=text)


def prompt_field(name: str, value: Any) -> PromptField:
    return PromptField(name=name, value=value)


def prompt_list(
    items: Iterable[Any],
    *,
    tag: str = "items",
    item_tag: str = "item",
    prefix: str = "",
    separator: str = "\n",
) -> PromptList:
    return PromptList(
        items=tuple(items),
        tag=tag,
        item_tag=item_tag,
        prefix=str(prefix),
        separator=str(separator),
    )


_MISSING = object()
