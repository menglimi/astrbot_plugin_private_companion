"""One sealed outbound plan: what a delivered message carries beyond its text.

Two different kinds of fact live here, and they are deliberately kept apart:

``OutboundPlan``
    Content that changes what the recipient reads: the native message being
    quoted, the members a reply mentions, the platform faces it carries and an
    explicit component order. It is part of the sealed payload hash, so a
    receipt proves that the reviewed plan -- not a later edit -- is what left.

``VoicePlan`` / ``PokePlan``
    Best-effort presentation side effects rendered at dispatch time, exactly
    like the conversation voice: a failed synthesis or poke lowers the
    experience, never the sealed text. They are re-read from their owner's
    sealed record on every validation, but they stay out of the payload hash so
    that a provider failure cannot invalidate an already-authorized message.

``DeliveryContent``
    What a background delivery owner returns: the visible text, its ordered
    images and the optional plan/side effects.
"""

from dataclasses import dataclass
import json

from companion_v2.execution.models import encode


CONTENT_KINDS = ("voice", "at", "text", "face", "image")
VOICE_POSITIONS = ("after_media", "after_text", "before_text")
VOICE_DELIVERY_MODES = ("voice_and_text", "voice_only")
MAX_MENTIONS = 8
MAX_FACES = 8
MAX_POKE_COUNT = 3
MAX_SPEECH_CHARS = 2000


def _number(value, *, field_name):
    """A platform message number or account id, in its canonical decimal form.

    OneBot v11 identifiers are signed; SnowLuma and some other implementations
    return negative message ids, so the sign is part of the identifier.
    """
    if type(value) is not str or not value.isascii():
        raise ValueError(f"{field_name} requires the platform's decimal identifier")
    digits = value[1:] if value.startswith("-") else value
    if not digits.isdecimal():
        raise ValueError(f"{field_name} requires the platform's decimal identifier")
    if not 1 <= len(digits) <= 20 or digits.startswith("0"):
        raise ValueError(f"{field_name} is outside the supported identifier range")
    return value


def _message_id(value):
    """OneBot message ids are signed; account ids are not."""
    if type(value) is not str or not value.isascii():
        raise ValueError("quoted message requires the platform's decimal identifier")
    digits = value[1:] if value.startswith("-") else value
    if not digits.isdecimal() or not 1 <= len(digits) <= 19 or digits.startswith("0"):
        raise ValueError("quoted message is outside the supported identifier range")
    return value


@dataclass(frozen=True)
class OutboundPlan:
    """Sealed content additions that participate in the payload hash."""

    quote: str = ""
    mentions: tuple[str, ...] = ()
    faces: tuple[int, ...] = ()
    order: tuple[str, ...] = ()

    def __post_init__(self):
        if self.quote:
            _message_id(self.quote)
        for value in (self.mentions, self.faces, self.order):
            if type(value) is not tuple:
                raise ValueError("outbound plan collections must be tuples")
        if len(self.mentions) > MAX_MENTIONS or len(self.faces) > MAX_FACES:
            raise ValueError("outbound plan carries too many mentions or faces")
        for mention in self.mentions:
            _number(mention, field_name="mentioned account")
        if len(set(self.mentions)) != len(self.mentions):
            raise ValueError("mentioned accounts must be unique")
        if any(type(face) is not int or not 1 <= face <= 100000 for face in self.faces):
            raise ValueError("platform face identifiers must be small positive integers")
        if len(set(self.faces)) != len(self.faces):
            raise ValueError("platform faces must be unique")
        if (len(set(self.order)) != len(self.order) or not set(self.order) <= set(CONTENT_KINDS)
                or len(self.order) > len(CONTENT_KINDS)):
            raise ValueError("component order must name each supported kind at most once")

    @property
    def empty(self):
        return not (self.quote or self.mentions or self.faces or self.order)

    def document(self):
        """The canonical JSON document folded into the payload hash."""
        if self.empty:
            return None
        document = {}
        if self.quote:
            document["quote"] = self.quote
        if self.mentions:
            document["mentions"] = list(self.mentions)
        if self.faces:
            document["faces"] = list(self.faces)
        if self.order:
            document["order"] = list(self.order)
        return document

    @classmethod
    def parse(cls, value):
        if value is None:
            return None
        if isinstance(value, cls):
            return None if value.empty else value
        if type(value) is not dict or set(value) - {"quote", "mentions", "faces", "order"}:
            raise ValueError("outbound plan contains unknown fields")
        quote = value.get("quote", "")
        if type(quote) is not str:
            raise ValueError("quoted message must be a platform identifier")
        mentions = value.get("mentions", [])
        faces = value.get("faces", [])
        order = value.get("order", [])
        for name, items in (("mentions", mentions), ("faces", faces), ("order", order)):
            if type(items) is not list:
                raise ValueError(f"outbound plan {name} must be a list")
        plan = cls(quote, tuple(mentions), tuple(faces), tuple(order))
        return None if plan.empty else plan


@dataclass(frozen=True)
class VoicePlan:
    """The spoken span a trusted presentation policy prepared for one dispatch."""

    speech: str
    provider_id: str = ""
    timeout_seconds: int = 30
    position: str = "after_text"
    delivery_mode: str = "voice_and_text"

    def __post_init__(self):
        if (type(self.speech) is not str or not self.speech.strip()
                or len(self.speech) > MAX_SPEECH_CHARS):
            raise ValueError("prepared speech must be short nonempty text")
        if type(self.provider_id) is not str or len(self.provider_id) > 128:
            raise ValueError("voice provider identifier is invalid")
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 120:
            raise ValueError("voice synthesis timeout is outside its range")
        if self.position not in VOICE_POSITIONS:
            raise ValueError("voice position is unsupported")
        if self.delivery_mode not in VOICE_DELIVERY_MODES:
            raise ValueError("voice delivery mode is unsupported")

    def document(self):
        return {"speech": self.speech, "provider_id": self.provider_id,
                "timeout_seconds": self.timeout_seconds, "position": self.position,
                "delivery_mode": self.delivery_mode}

    @classmethod
    def parse(cls, value):
        if value is None:
            return None
        if isinstance(value, cls):
            return value
        if type(value) is not dict or set(value) != {"speech", "provider_id", "timeout_seconds",
                                                     "position", "delivery_mode"}:
            raise ValueError("voice plan contains unknown fields")
        return cls(**value)


@dataclass(frozen=True)
class PokePlan:
    """The native poke that opens one proactive message."""

    count: int = 1

    def __post_init__(self):
        if type(self.count) is not int or not 1 <= self.count <= MAX_POKE_COUNT:
            raise ValueError("poke count is outside its range")

    def document(self):
        return {"count": self.count}

    @classmethod
    def parse(cls, value):
        if value is None:
            return None
        if isinstance(value, cls):
            return value
        if type(value) is not dict or set(value) != {"count"}:
            raise ValueError("poke plan contains unknown fields")
        return cls(**value)


@dataclass(frozen=True)
class DeliveryContent:
    """What one background delivery owner returns for its sealed record."""

    text: str
    images: tuple = ()
    plan: OutboundPlan | None = None
    voice: VoicePlan | None = None
    poke: PokePlan | None = None
    allow_cross_scope_images: bool = False

    def __post_init__(self):
        if type(self.text) is not str or type(self.images) is not tuple:
            raise ValueError("delivery content requires text and an ordered image tuple")
        if type(self.allow_cross_scope_images) is not bool:
            raise ValueError("delivery image scope authorization must be explicit")
        for name in ("plan", "voice", "poke"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, (OutboundPlan, VoicePlan, PokePlan)):
                raise ValueError("delivery content carries an unvalidated plan")

    @property
    def plan_document(self):
        return self.plan.document() if self.plan is not None else None


def content_digest(document):
    """The stable text one sealed plan contributes to an operation's parameter hash."""
    return encode(document or {})


def plan_from_document(document):
    """Read a stored plan document back into its validated form."""
    if document is None:
        return None
    if type(document) is not dict:
        raise ValueError("stored outbound plan must be an object")
    return OutboundPlan.parse(document)


def parse_json_document(value, *, field_name):
    """Parse one optional JSON plan column without inventing a default."""
    if value in (None, "", b""):
        return None
    if type(value) is bytes:
        value = value.decode("utf-8")
    if type(value) is not str:
        raise ValueError(f"{field_name} must be stored as JSON text")
    document = json.loads(value)
    if type(document) is not dict:
        raise ValueError(f"{field_name} must be a JSON object")
    return document


__all__ = ["CONTENT_KINDS", "DeliveryContent", "OutboundPlan", "PokePlan", "VoicePlan",
           "content_digest", "parse_json_document", "plan_from_document"]
