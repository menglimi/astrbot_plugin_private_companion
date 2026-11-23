"""Scope-bound identity/permission projection in the existing access policy.

It never substitutes a principal, issues a grant or opens another owner's data.
Sharing CORE applies only within that explicit persona's private route, NOT to
Dashboard/system administrator membership. Original explicit bans remain vetoes.
"""
import json
from companion_v2.execution.models import TaskError, digest
from companion_v2.foundation.refs import ScopeKind
from companion_v2.identity_policy.repository import PersistentIdentityDirectory
from .account_links import KEY, PrivateAccountLinks

REVISION_BASE = 1_000_000_000
CONFIRMATION = "share-private-identity-and-permissions"


def group_for_scope(runtime, subject, scope, at):
    if scope is None or scope.kind is not ScopeKind.PRIVATE:
        return None
    check = getattr(runtime, "private_account_link_persona", None)
    if check is None:
        return None
    resolved = PersistentIdentityDirectory(runtime).resolve_role(scope, observed_at=at)
    if not resolved.is_success:
        return None
    role = resolved.unwrap().role.subject_id
    return next((group for group in PrivateAccountLinks(runtime).read()["groups"]
        if group.get("identity", {}).get("enabled") is True and any(
            (m["subject_id"], m["role_id"], m["platform"], m["bot_id"], m["account_id"], m["native_account_id"]) ==
            (subject.subject_id, role, scope.platform, scope.bot_id, scope.account_id, scope.native_scope_id)
            and check(m) == group["persona_id"] for m in group["members"])), None)


def validate_identity(value):
    from companion_v2.identity_policy.management import PAGE_MANAGED_CAPABILITIES
    if (type(value) is not dict or set(value) != {"enabled", "tier", "permissions"}
            or type(value["enabled"]) is not bool or value["tier"] not in ("standard", "important", "core")
            or type(value["permissions"]) is not dict or set(value["permissions"]) != set(PAGE_MANAGED_CAPABILITIES)
            or any(type(flag) is not bool for flag in value["permissions"].values())):
        raise TaskError("account_link_identity_invalid", "select a private identity tier and explicit managed permissions")


def effective(runtime, subject, scope, at):
    group = group_for_scope(runtime, subject, scope, at)
    return group.get("identity") if group else None


def project_status(runtime, subject, scope, at, status):
    group = group_for_scope(runtime, subject, scope, at)
    if not group:
        return status
    from companion_v2.identity_policy.grants import PERSON_PERMISSION_GRANT_PREFIX
    from companion_v2.identity_policy.models import GrantState
    grants = PersistentIdentityDirectory(runtime).grants_for_subject(subject)
    identity = group["identity"]
    revision = REVISION_BASE + PrivateAccountLinks(runtime).read()["revision"]
    result = {**status, "local_identity_tier": status["identity_tier"], "identity_tier": identity["tier"],
        "identity_revision": revision, "policy_revision": revision,
        "shared_identity": {"group_id": group["id"], "scope": "private-persona", "system_administrator": False}}
    for item in result["items"]:
        action = item["capability"]
        if action in identity["permissions"]:
            explicit = [g for g in grants if g.scope == scope and g.capability == action and
                        g.grant_id.startswith(PERSON_PERMISSION_GRANT_PREFIX)]
            veto = bool(explicit) and not any(g.state is GrantState.ACTIVE and g.valid_from <= at
                        and (g.expires_at is None or at < g.expires_at) for g in explicit)
            item["local_state"] = item["effective_state"]
            item["effective_state"] = "active" if identity["permissions"][action] and not veto else "off"
            item["shared"] = True
            item["managed_by"] = "page"
            item["revocable"] = True
            item["local_veto"] = veto
            item["policy_revision"] = revision
    result["companion"] = any(item["capability"] == "conversation.reply" and item["effective_state"] == "active"
                                for item in result["items"])
    # Global administrator membership is intentionally left at its original value.
    return result


def edit(runtime, operator, subject, scope, command, *, at, identity_tier=False):
    """Return None for local edits. Shared edits and receipts are ONE side-store tx."""
    links = PrivateAccountLinks(runtime)
    with links.store.transaction() as connection:
        state = links.read()
        group = group_for_scope(runtime, subject, scope, at)
        if not group:
            if command["expected_revision"] >= REVISION_BASE:
                raise TaskError("account_link_revision_conflict", "sharing changed; reread original permissions")
            return None
        field = "tier" if identity_tier else command["capability"]
        value = command["tier"] if identity_tier else command["enabled"]
        if not identity_tier and field not in group["identity"]["permissions"]:
            raise TaskError("account_link_identity_invalid", "only registered managed permissions can be shared")
        op_id = "shared-identity:" + digest([operator.subject, subject, scope.document(), field, command["operation_key"]])
        hashed = digest(command)
        receipt = state["receipts"].get(op_id)
        if receipt is not None:
            if receipt["hash"] != hashed:
                raise TaskError("account_link_operation_conflict", "operation already has different parameters")
            return {"operation_id": op_id, "replayed": True}
        if command["expected_revision"] != REVISION_BASE + state["revision"]:
            raise TaskError("account_link_revision_conflict", "shared identity changed; reread before saving")
        current = next(g for g in state["groups"] if g["id"] == group["id"])
        if identity_tier:
            current["identity"]["tier"] = value
        else:
            current["identity"]["permissions"][field] = value
        state["revision"] += 1
        state["receipts"][op_id] = {"hash": hashed}
        while len(state["receipts"]) > 128:
            del state["receipts"][next(iter(state["receipts"]))]
        connection.execute("INSERT OR REPLACE INTO runtime_metadata VALUES (?, ?)", (KEY, json.dumps(state)))
        return {"operation_id": op_id, "replayed": False}
