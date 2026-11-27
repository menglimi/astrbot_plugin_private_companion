"""Derive the pinned V2 identity from the account and persona the operator chose.

Single-target mode used to ask the operator for four things it could work out
itself: the bot, the role, the platform and the conversation target. V1 needed
them because it let one installation hold several bots. A single-bot installation
does not, and asking anyway is what made first-run configuration read as a
chore -- the operator was told to open the plugin config and copy in values the
host already had.

So the identity is derived instead, from the two facts only the operator can
know:

* **which account** is doing the talking (the OneBot login, ``self_id``), and
* **which persona** it should speak in.

Everything here is a pure function over those two strings plus the platform id.
Keeping it pure matters: the values land in ``StagingConfiguration``, which is
fingerprinted and pinned on disk, so a derivation that could vary between two
calls would look like a profile conflict rather than a bug.

``bot_id`` and ``role_id`` are the memory-ownership keys. Deriving them from the
account and the persona -- rather than the shared ``staging-bot`` / ``staging-role``
constants -- means two installations on different accounts no longer write into
one role, and the same persona under two accounts stays two roles.
"""

from __future__ import annotations

import re

from companion_v2.execution.models import digest

from .settings import HostControlError


# Kept from the previous defaults so an already-pinned installation keeps the same
# bot/role identity it was created with. Only the derivation is new; a profile that
# never pinned anything gets the account- and persona-scoped values below.
LEGACY_BOT_ID = "staging-bot"
LEGACY_ROLE_ID = "staging-role"

_ACCOUNT = re.compile(r"[^\s\x00-\x1f\x7f:]+")
_ASCII_ACCOUNT = re.compile(r"[0-9A-Za-z._@-]+")
_SAFE = re.compile(r"[^0-9a-z]+")
_ROLE_CHANNEL_SUFFIX = re.compile(r"\s*[-_·－]\s*(?:私聊|群聊)\s*$")


def _account(value: object, field: str) -> str:
    """A native account/sender key (numeric OneBot or opaque official id)."""
    valid = (type(value) is str and len(value) <= 128 and _ACCOUNT.fullmatch(value) is not None
             and ".." not in value
             and not value.startswith(".")
             and not value.endswith(".")
             and any(char.isalnum() for char in value)
             and (_ASCII_ACCOUNT.fullmatch(value) is not None if value.isascii()
                  else all(char.isalnum() or char in "._-@" for char in value)))
    if not valid:
        raise HostControlError("host_identity_invalid",
                               f"the {field} must be a native account id, not a name or an empty value")
    return value


def derive_bot_id(account_id: str) -> str:
    """The V2 bot identity for one login.

    Prefixed so a derived id can never be mistaken for the legacy constant, and so
    a stored profile says plainly where its bot identity came from.
    """
    account = _account(account_id, "bot account")
    if account.isascii():
        return "bot-" + account
    return "bot-" + digest({"native_account": account})[:32]


def derive_core_target_id(platform_id: str, account_id: str, participant_id: str) -> str:
    """Build an ASCII target key for a native binding, including Unicode ids."""
    values = (platform_id, account_id, participant_id)
    if any(type(value) is not str or not value.strip() for value in values):
        raise HostControlError("host_identity_invalid", "the core binding identifiers are incomplete")
    return "setup-core-" + digest({"platform_id": platform_id, "account_id": account_id,
                                   "participant_id": participant_id})[:24]


def derive_role_id(account_id: str, persona_id: str | None) -> str:
    """The V2 role (memory owner) for one account and persona.

    ``persona_id`` empty means "whatever AstrBot resolves for this conversation",
    which is the single-persona case. A digest is used rather than the raw persona
    name because persona ids are free-form display names and the role id is a
    storage key: the digest is stable, bounded and says nothing about the persona.

    The persona is normalized here rather than by each caller, because a role id
    that disagreed with the pinned ``persona_id`` over the same input would be a
    silent split in memory ownership -- ``"晚星"`` and ``" 晚星 "`` are one persona.
    """
    owner = _account(account_id, "bot account")
    persona = normalize_persona_id(persona_id)
    owner_key = owner if owner.isascii() else digest({"native_account": owner})[:32]
    if not persona:
        return "role-" + owner_key
    return "role-" + owner_key + "-" + digest({"persona": persona})[:16]


def normalize_persona_id(persona_id: object) -> str:
    """A persona id, or empty for "follow the conversation's own selection"."""
    if persona_id is None:
        return ""
    if type(persona_id) is not str:
        raise HostControlError("host_identity_invalid", "the persona must be selected by name")
    text = persona_id.strip()
    if text == "[%None]":  # AstrBot's sentinel for "no persona in this conversation".
        return ""
    if any(ord(char) < 32 for char in text) or len(text) > 120:
        raise HostControlError("host_identity_invalid", "the persona name is not a single short line")
    return text


def derived_identity(*, platform_id: str, account_id: str, participant_id: str,
                     persona_id: object = None) -> dict[str, str]:
    """The five pinned identity fields, derived rather than configured.

    Returned together because they are always needed together: ``StagingConfiguration``
    refuses a partial scope, and a caller that fills one field from here and another
    from somewhere else is how the old half-configured states happened.
    """
    if type(platform_id) is not str or not platform_id.strip() or len(platform_id) > 120:
        raise HostControlError("host_identity_invalid", "the platform id is missing")
    account = _account(account_id, "bot account")
    participant = _account(participant_id, "conversation target")
    if account == participant:
        raise HostControlError("host_identity_invalid",
                               "the conversation target must be someone other than the bot itself")
    persona = normalize_persona_id(persona_id)
    return {
        "platform_id": platform_id.strip(),
        "account_id": account,
        "participant_id": participant,
        "bot_id": derive_bot_id(account),
        "role_id": derive_role_id(account, persona),
        "persona_id": persona,
    }


def persona_label(persona_id: str) -> str:
    """How a derived role is described to the operator."""
    return persona_id or "随会话当前人格"


def role_display_name(role_id: object, persona_ids: object = ()) -> str:
    """Return the human role name without exposing generated storage keys.

    Older mappings often used persona names such as ``星缘-私聊`` and
    ``星缘-群聊`` as the role key.  The role is the shared character, so those
    transport suffixes must not leak into the cottage title.  Explicit human
    role names still win; generated ``role-*`` keys fall back to the loaded
    persona labels.
    """
    role = str(role_id or "").strip()
    if role and not role.startswith(("role-", "staging-", "dashboard:", "onebot-user:")):
        role = _ROLE_CHANNEL_SUFFIX.sub("", role).strip()
        return role or "当前角色"
    values = persona_ids if isinstance(persona_ids, (list, tuple, set)) else ()
    labels = []
    for value in values:
        text = "默认人格" if value in (None, "", "[%None]") else str(value).strip()
        text = _ROLE_CHANNEL_SUFFIX.sub("", text).strip()
        if text and text not in labels:
            labels.append(text)
    return "、".join(labels) or (role if role else "当前角色")
