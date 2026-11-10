"""Host-observed group standing (群主 / 管理员) for the group prompt.

The plugin never asks the model to infer standing from names or tone: the
OneBot sender payload already carries ``role`` (owner / admin / member), so the
host records it per group owner and the group prompt can then say "this speaker
is the group owner" instead of treating 群主 as an unnamed third person.

Records live on the shared ``runtime_metadata`` ledger, one bounded document per
group owner, exactly like the other group ledgers: no table, no migration.
"""

import json

from companion_v2.execution.models import digest, stamp
from companion_v2.memory.group.access import owner_key

VERSION = "conversation.group_roles.v1"
PREFIX = "conversation.group_roles"
MAX_MEMBERS = 64
ROLE_LABELS = {"owner": "群主", "admin": "管理员", "member": "成员"}


class GroupRoleStore:
    """One owner-scoped document: ``{"owner_id": str, "members": {account: role}}``."""

    def __init__(self, runtime):
        self.runtime = runtime

    @staticmethod
    def key(owner):
        return PREFIX + ":" + digest([VERSION, owner])

    def read(self, owner):
        row = self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?",
                                    (self.key(owner),))
        if row is None:
            return None
        try:
            document = json.loads(row["value"])
        except ValueError:
            return None
        if type(document) is not dict or document.get("version") != VERSION:
            return None
        members = {}
        raw_members = document.get("members")
        for account, record in (raw_members.items() if type(raw_members) is dict else ()):
            if not isinstance(account, str) or not isinstance(record, dict):
                continue
            role = record.get("role")
            if role not in ROLE_LABELS:
                continue
            members[account] = {"role": role, "observed_at": str(record.get("observed_at") or "")}
        owner_id = document.get("owner_id")
        return {"owner_id": owner_id if isinstance(owner_id, str) else "", "members": members}

    def remember(self, connection, context, *, account_id, role, at):
        """Persist one host-observed standing; the caller owns the transaction.

        The read goes through the caller's connection so a write never nests a
        second transaction inside the caller's.
        """
        if role not in ROLE_LABELS or type(account_id) is not str or not account_id:
            return False
        owner = owner_key(context)
        key = self.key(owner)
        row = connection.execute("SELECT value FROM runtime_metadata WHERE key = ?", (key,)).fetchone()
        document = None
        if row is not None:
            raw = row["value"] if hasattr(row, "keys") else row[0]
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                parsed = None
            if type(parsed) is dict and parsed.get("version") == VERSION:
                document = {"owner_id": parsed.get("owner_id") if isinstance(parsed.get("owner_id"), str) else "",
                            "members": {account: {"role": record.get("role"),
                                                  "observed_at": str(record.get("observed_at") or "")}
                                        for account, record in (parsed.get("members") or {}).items()
                                        if isinstance(record, dict) and record.get("role") in ROLE_LABELS}}
        document = document or {"owner_id": "", "members": {}}
        members = dict(document["members"])
        previous = members.get(account_id, {}).get("role", "")
        members[account_id] = {"role": role, "observed_at": stamp(at)}
        owner_id = document["owner_id"]
        if role == "owner":
            # One group has one owner. A later owner replaces the previous one,
            # so an old cached standing cannot keep answering as 群主.
            owner_id = account_id
            for other, record in list(members.items()):
                if other != account_id and record["role"] == "owner":
                    members[other] = {"role": "member", "observed_at": stamp(at)}
        if len(members) > MAX_MEMBERS:
            kept = sorted(members.items(), key=lambda item: item[1]["observed_at"], reverse=True)
            members = dict(kept[:MAX_MEMBERS])
        value = json.dumps({"version": VERSION, "owner_id": owner_id, "members": members},
                           ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        connection.execute("""INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value""", (self.key(owner), value))
        return previous != role

    def role_of(self, context, account_id):
        """``(role, label)`` for one account in this group, or ``("", "")``."""
        if type(account_id) is not str or not account_id:
            return "", ""
        document = self.read(owner_key(context)) or {"owner_id": "", "members": {}}
        record = document["members"].get(account_id) or {}
        role = record.get("role", "")
        if role:
            return role, ROLE_LABELS[role]
        if document["owner_id"] and document["owner_id"] == account_id:
            return "owner", ROLE_LABELS["owner"]
        return "", ""

    def describe(self, context):
        """Prompt fields for the current speaker: standing plus the group owner id."""
        account = context.actor.principal.native_account_id
        role, label = self.role_of(context, account)
        document = self.read(owner_key(context)) or {"owner_id": "", "members": {}}
        owner_id = document["owner_id"] or next(
            (member for member, record in document["members"].items() if record["role"] == "owner"), "")
        described = {"group_owner_id": owner_id}
        if role:
            described.update(speaker_role=role, speaker_role_label=label,
                             speaker_is_group_owner=bool(owner_id and owner_id == account) or role == "owner")
        return described
