"""Ordered components shared by outbound adapters.

The conversation layer owns text, original images, the optional rendered voice,
the members a reply addresses and the platform faces it carries. Adapters only
translate these components into their platform payload; they do not decide which
component is hidden or where it belongs.

``plan.order`` is the V2 form of V1's ``segmented_proactive_component_order``:
when it is present it fixes the kind sequence of one message; when it is absent
the long-standing ``image_position``/``position`` behaviour is unchanged, so an
installation that never touches the new field keeps its current delivery.
"""

from dataclasses import dataclass

from companion_v2.conversation.text_processing import strip_internal_expression_protocol
from companion_v2.conversation.voice_reply import speech_covers_visible
from .plan import CONTENT_KINDS


_KINDS = frozenset(("text", "image", "voice", "at", "face"))
_DEFAULT_ORDER = ("voice", "at", "text", "face", "image")


@dataclass(frozen=True)
class OutboundComponent:
    """One already-selected component, before platform serialization."""

    kind: str
    value: object

    def __post_init__(self):
        if self.kind not in _KINDS:
            raise ValueError("outbound component kind is unsupported")


@dataclass(frozen=True)
class OutboundComponentPlan:
    """Stable component order and the voice decision used by the sender."""

    components: tuple[OutboundComponent, ...]
    voice_included: bool = False
    text_hidden: bool = False

    def __post_init__(self):
        if (not isinstance(self.components, tuple)
                or any(not isinstance(component, OutboundComponent) for component in self.components)
                or self.voice_included != any(component.kind == "voice" for component in self.components)
                or self.text_hidden != (self.voice_included and not any(component.kind == "text"
                                                                         for component in self.components))):
            raise ValueError("outbound component plan is inconsistent")

    @property
    def kinds(self):
        return tuple(component.kind for component in self.components)


def _voice_component_is_valid(voice):
    """Accept only a complete OneBot record shape from the trusted renderer."""
    if not isinstance(voice, dict) or voice.get("type") != "record":
        return False
    data = voice.get("data")
    return isinstance(data, dict) and isinstance(data.get("file"), str) and bool(data["file"].strip())


def _mention_component_is_valid(mention):
    return type(mention) is str and mention.isascii() and mention.isdecimal() and not mention.startswith("0")


def _face_component_is_valid(face):
    return type(face) is int and 1 <= face <= 100000


def order_rank(order):
    """The rank table one explicit component order implies, or None without one."""
    if not order:
        return None
    declared = tuple(kind for kind in order if kind in _KINDS)
    remaining = tuple(kind for kind in _DEFAULT_ORDER if kind not in declared)
    return {kind: index for index, kind in enumerate(declared + remaining)}


def build_component_plan(text, images=(), voice=None, *, position="after_media", image_position="after_text",
                         voice_only=False, speech=None, order=(), mentions=(), faces=()):
    """Build the ordered text/image/voice/at/face components for one ACK.

    A malformed or empty voice is ignored, preserving the remaining text and
    image order. ``voice_only`` hides text only when the rendered speech is
    the complete visible text for this part. Mentions and faces are dropped
    when they are not platform identifiers; an installation that requests them
    still receives its text.
    """
    try:
        images = tuple(images)
    except TypeError as error:
        raise ValueError("outbound images must be iterable") from error
    if any(image is None for image in images):
        raise ValueError("outbound image is missing")
    if isinstance(text, str):
        # Last-mile defense for proactive and session-send paths.  The core
        # presentation pipeline already cleans ordinary replies; this catches
        # components that were rebuilt or forwarded after that stage.
        text = strip_internal_expression_protocol(text)
    if not isinstance(text, str) or (not text.strip() and (text != "" or not images)):
        raise ValueError("outbound text must be nonempty unless sending images alone")
    if image_position not in ("after_text", "before_text"):
        raise ValueError("outbound image position is unsupported")
    if any(kind not in CONTENT_KINDS for kind in order):
        raise ValueError("component order names an unsupported kind")

    voice_included = _voice_component_is_valid(voice)
    hide_text = (voice_included and voice_only is True and isinstance(speech, str)
                 and speech_covers_visible(text, speech))
    text_component = () if hide_text or not text else (OutboundComponent("text", text),)
    image_components = tuple(OutboundComponent("image", image) for image in images)
    voice_components = (OutboundComponent("voice", voice),) if voice_included else ()
    mention_components = tuple(OutboundComponent("at", mention) for mention in mentions
                               if _mention_component_is_valid(mention))
    face_components = tuple(OutboundComponent("face", face) for face in faces if _face_component_is_valid(face))

    rank = order_rank(order)
    if rank is not None:
        grouped = {"text": text_component, "image": image_components, "voice": voice_components,
                   "at": mention_components, "face": face_components}
        ordered = tuple(component for kind in sorted(grouped, key=lambda name: rank[name])
                        for component in grouped[kind])
        return OutboundComponentPlan(ordered, voice_included, hide_text)

    if position == "before_text":
        text_group = voice_components + text_component
    elif position == "after_text":
        text_group = text_component + voice_components
    else:
        text_group = text_component
    ordered = image_components + text_group if image_position == "before_text" else text_group + image_components
    if position not in ("before_text", "after_text"):
        # Unknown persisted voice positions retain the default trailing placement.
        ordered += voice_components
    # Without an explicit order the default placement stays predictable: the
    # addressed members lead the sentence they belong to, and platform faces
    # close it.
    text_index = next((index for index, component in enumerate(ordered) if component.kind == "text"), None)
    if text_index is None:
        ordered = mention_components + ordered
    else:
        ordered = ordered[:text_index] + mention_components + ordered[text_index:]
    ordered = ordered + face_components
    return OutboundComponentPlan(ordered, voice_included, hide_text)


def ordered_components(text, images=(), voice=None, *, position="after_media", image_position="after_text",
                       voice_only=False, speech=None, order=(), mentions=(), faces=()):
    """Return only the ordered component tuple for small adapter-facing uses."""
    return build_component_plan(text, images, voice, position=position, image_position=image_position,
                                voice_only=voice_only, speech=speech, order=order,
                                mentions=mentions, faces=faces).components
