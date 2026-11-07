"""One-time binding of the conversation target, driven from the chat itself.

Single-target mode used to require the operator to open the plugin config and type
in the platform, the bot account, the participant and the persona before the role
could run at all. For one bot and one persona that is backwards: the only two facts
the host cannot work out on its own are *who is being spoken to* and *in what
voice*, and both of those are already present in the first private message.

So binding is a command. Somebody sends it to the bot in a private chat; the sender
becomes the participant, the bot that received it becomes the account, and the
persona is the one named in the command (or the conversation's own, if unnamed).
``identity_derivation`` turns those into the pinned ``bot_id`` and ``role_id``.

Three rules hold this to the same boundary the configuration form had:

* **It happens once.** A pinned profile refuses to rebind. Changing the target stays
  a deliberate act with existing memory attached to it, exactly as it was when the
  values lived in a config file.
* **The confirmation is explicit.** Without it, the first stranger to find the bot's
  number would silently become the person it talks to. A one-word reply confirms.
* **Nothing here sends or generates.** Binding writes the pin; it does not start the
  session, and the review step still has to be seen before anything is delivered.

The decisions are pure functions over plain values so they can be tested without an
AstrBot installation; the event-shaped wrapper at the bottom only reads identity off
an event and delegates.
"""

from __future__ import annotations

from dataclasses import dataclass

from companion_v2.execution.models import TaskError

from .identity_derivation import derived_identity, normalize_persona_id, persona_label
from .settings import HostControlError


BIND_COMMAND = "我会永远陪着你"
# 旧口令保留一年，避免已养成习惯的用户突然绑不上。
LEGACY_BIND_COMMANDS = ("companion_bind",)
CONFIRM_COMMAND = "companion_confirm"
UNBIND_COMMAND = "companion_unbind"

# A persona chosen inline with the bind command, e.g. "我会永远陪着你 晚星".
CONFIRM_WORD = "确认"


@dataclass(frozen=True)
class BindRequest:
    """What an inbound private message is asking for, decided without side effects."""

    kind: str  # "bind" | "confirm" | "unbind" | "ignore"
    persona_id: str = ""
    reason: str = ""


def parse_bind_command(text: object) -> BindRequest:
    """Classify a private message against the binding vocabulary.

    Deliberately strict and literal: this runs before ``claims()`` on every private
    message the bot receives, so it must never mistake ordinary conversation for a
    command. Only an exact leading keyword counts, and an unrecognized trailing
    word is refused rather than ignored -- silently dropping a persona name the
    operator typed would pin the wrong voice.
    """
    if type(text) is not str:
        return BindRequest("ignore")
    stripped = text.strip()
    if not stripped:
        return BindRequest("ignore")

    head, _, tail = stripped.partition(" ")
    head = head.strip().lstrip("/")
    tail = tail.strip()

    if head == BIND_COMMAND or head in LEGACY_BIND_COMMANDS:
        if not tail:
            # No persona named: follow whatever AstrBot resolves for this chat. That
            # is the single-persona case, and the common one.
            return BindRequest("bind", "")
        try:
            return BindRequest("bind", normalize_persona_id(tail))
        except HostControlError:
            return BindRequest("bind", "", reason="host_identity_invalid")
    if head == CONFIRM_COMMAND:
        if tail and tail != CONFIRM_WORD:
            return BindRequest("ignore")
        return BindRequest("confirm")
    if head == UNBIND_COMMAND:
        return BindRequest("unbind")
    return BindRequest("ignore")


def is_bind_command(text: object) -> bool:
    """Whether this message is part of the binding vocabulary at all."""
    return parse_bind_command(text).kind != "ignore"


def binding_identity(*, platform_id: str, account_id: str, participant_id: str,
                     persona_id: str = "") -> dict[str, str]:
    """The identity block to pin, derived from the message that carried the command.

    A thin, named wrapper over :func:`derived_identity` so the setup route and the
    command handler cannot disagree about what binding produces.
    """
    return derived_identity(platform_id=platform_id, account_id=account_id,
                            participant_id=participant_id, persona_id=persona_id)


def describe_pin(identity: dict[str, str]) -> str:
    """The operator-facing summary of what was bound, in the chat's own language."""
    return (f"会话对象：{identity['participant_id']}\n"
            f"Bot 账号：{identity['account_id']}\n"
            f"人格：{persona_label(identity['persona_id'])}\n"
            f"记忆归属：{identity['role_id']}\n"
            "请回到「开始使用」，由核心管理员确认这个窗口。")


def event_identity(event: object) -> dict[str, str] | None:
    """Read platform, bot account and sender off a native private message event.

    Returns ``None`` when the event does not carry the identity this needs, so the
    caller can ignore it rather than guess. These are the same three fields
    ``AstrBotStagingController.claims`` already reads for its exact match; nothing
    new is being trusted, it is just being read one step earlier.
    """
    meta = getattr(event, "platform_meta", None)
    message = getattr(event, "message_obj", None)
    platform_id = getattr(meta, "id", None)
    platform_name = str(getattr(meta, "name", "") or "").casefold()
    account_id = _native_id(getattr(message, "self_id", None))
    # Non-OneBot adapters use the configured platform instance as the stable
    # account key. Some expose a bot open id instead; using it would not match
    # the account picker or the host bridge.
    if platform_name != "aiocqhttp":
        account_id = _native_id(platform_id)
    # QQ Official exposes a synthetic self id on C2C/direct events. The platform
    # instance id is the stable account key used by the setup account list.
    official = platform_name in {"qq_official", "qqofficial"} or str(platform_id or "").casefold().startswith(
        ("qq_official", "qqofficial"))
    if account_id in {"unknown_selfid", "qq_official"} or (not account_id and official):
        account_id = _native_id(platform_id)
    sender = getattr(message, "sender", None)
    participant_id = _native_id(getattr(sender, "user_id", None))
    if not platform_id or not account_id or not participant_id:
        return None
    if account_id == participant_id:
        return None  # The bot talking to itself is never a binding.
    return {"platform_id": str(platform_id), "account_id": account_id,
            "participant_id": participant_id}


def message_text(event: object) -> str:
    """The plain text of a message event, tolerating the shapes AstrBot ships.

    Read from several places on purpose rather than one attribute. Only ``event.text``
    and ``event.message_obj`` are used elsewhere in this plugin, and ``message_str``
    is what the contract cases attach to ``message_obj`` -- so the command parser
    cannot assume any single one of them exists. The last fallback reassembles the
    ``Plain`` components the same way the native adapter does.

    Returns "" when there is no text at all, which the parser treats as "not a
    command" rather than as an error: a sticker or an image in a private chat is
    ordinary conversation, not a malformed command.
    """
    value = getattr(event, "text", None)
    if isinstance(value, str) and value.strip():
        return value
    message = getattr(event, "message_obj", None)
    for attribute in ("message_str", "message_str_"):
        value = getattr(message, attribute, None)
        if isinstance(value, str) and value.strip():
            return value
    parts = getattr(message, "message", None)
    if isinstance(parts, (list, tuple)):
        pieces = []
        for part in parts:
            kind = getattr(part, "type", None)
            kind = getattr(kind, "value", kind)
            if kind == "Plain" and isinstance(getattr(part, "text", None), str):
                pieces.append(part.text)
        return "".join(pieces)
    return ""


def _native_id(value: object) -> str:
    """Read native ids without assuming OneBot's numeric shape.

    OneBot uses decimal QQ numbers, but QQ Official / Satori / other adapters use
    opaque ids such as ``607097777B7E7FE3...``.  Rejecting those before the
    binding parser sees them made the first-run command fail with
    ``host_identity_unreadable`` on those platforms.
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text or len(text) > 128:
        return ""
    if any(char.isspace() or ord(char) < 32 for char in text):
        return ""
    if not all(char.isalnum() or char in "._:-@" for char in text):
        return ""
    return text


def is_private_message(event: object) -> bool:
    """Whether this event is a friend/private message rather than a group one.

    Binding a group id as the private participant would produce a target that can
    never match, so the shape is checked before the command is honoured.
    """
    origin = getattr(event, "unified_msg_origin", "")
    if isinstance(origin, str) and origin:
        return ":FriendMessage:" in origin
    return getattr(getattr(getattr(event, "message_obj", None), "type", None), "value", None) == "FriendMessage"


class BindRefusal(TaskError):
    """A binding that was understood and deliberately declined."""


def refuse_already_bound(identity: dict[str, str]) -> BindRefusal:
    """A pinned profile does not silently rebind.

    Matches how ``staging_profile_conflict`` behaved when these values came from a
    config file: the operator is told what is currently bound and that changing it
    is a deliberate act, not a side effect of a stray command.
    """
    return BindRefusal(
        "host_binding_exists",
        "this installation is already bound to " + identity.get("participant_id", "")
        + "; unbind first to point it somewhere else",
    )
