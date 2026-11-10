"""Group standing read from the authenticated OneBot sender payload.

``sender.role`` is the platform's own answer to "who is this in this group":
``owner`` / ``admin`` / ``member``. QQ Official (webhook / openid) payloads carry
no such field, so this stays optional and never blocks intake.
"""

from collections.abc import Mapping

ROLES = ("owner", "admin", "member")


def group_sender_standing(event):
    """Return ``{"account_id", "role", "group_id"}`` for the sender, or ``None``."""
    message = getattr(event, "message_obj", None)
    raw = getattr(message, "raw_message", None)
    if not isinstance(raw, Mapping):
        return None
    sender = raw.get("sender")
    if not isinstance(sender, Mapping):
        return None
    role = sender.get("role")
    if not isinstance(role, str):
        return None
    role = role.strip().lower()
    if role not in ROLES:
        return None
    account = sender.get("user_id")
    if type(account) is int:
        account = str(account)
    if not isinstance(account, str):
        return None
    account = account.strip()
    if not (account.isascii() and account.isdigit()):
        return None
    group = raw.get("group_id")
    if type(group) is int:
        group = str(group)
    return {"account_id": account, "role": role,
            "group_id": group.strip() if isinstance(group, str) else ""}
