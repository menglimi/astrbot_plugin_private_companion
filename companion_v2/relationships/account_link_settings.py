"""Reversible shared configuration, in the SAME transaction as sparse writes.

Association catalog and shared values live in the configuration store's existing
runtime_metadata table. Main identity/history data is never rewritten. All owners,
including direct runtime readers, see the overlay; disabling exposes untouched locals.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import json

from companion_v2.configuration.models import ConfigLayer, ConfigurationError, EffectiveValue
from companion_v2.configuration.owners import OwnerSnapshot, SparseConfigurationOwner
from companion_v2.execution.models import TaskError, digest
from .account_links import KEY, PrivateAccountLinks


_LOCAL = ContextVar("account_link_original_settings", default=False)


@contextmanager
def unshared():
    token = _LOCAL.set(True)
    try:
        yield
    finally:
        _LOCAL.reset(token)


def definitions():
    from .profile import configuration_fields as profiles
    from .private_management import PRIVATE_REPLY_FIELDS
    from companion_v2.conversation.reply_settings import configuration_fields as replies, GROUP as REPLY
    from companion_v2.proactive.configuration import configuration_fields as contacts
    from companion_v2.conversation.segmentation import SegmentationPolicy
    return {"profile": profiles(), "reply": tuple(f for f in replies(SegmentationPolicy(), False, 0)
        if f.field_id.removeprefix(REPLY + ".") in PRIVATE_REPLY_FIELDS), "contact": contacts()}


def catalog():
    return {category: [field.field_id for field in fields] for category, fields in definitions().items()}


def validate_sharing(sharing, values):
    allowed = catalog()
    if (type(sharing) is not dict or set(sharing) != set(allowed)
            or any(type(keys) is not list or len(set(keys)) != len(keys)
                   or any(type(key) is not str or key not in allowed[category] for key in keys)
                   for category, keys in sharing.items())):
        raise TaskError("account_link_fields_invalid", "select registered private member fields only")
    active = {key for keys in sharing.values() for key in keys}
    if not active <= set(values):
        raise TaskError("account_link_seed_required", "initial shared values are incomplete")
    for fields in definitions().values():
        for field in fields:
            if field.field_id in active:
                field.validate(values[field.field_id])


def target_matches(target, member, subject_id=None):
    if target.layer not in (ConfigLayer.RELATIONSHIP, ConfigLayer.WINDOW) or target.role is None:
        return False
    subject_id = target.subject.subject_id if target.subject is not None else subject_id
    if (target.role.subject_id, subject_id, target.bot_id) != (
            member["role_id"], member["subject_id"], member["bot_id"]):
        return False
    scope = target.scope
    return scope is None or (scope.kind.value == "private" and
        (scope.platform, scope.account_id, scope.native_scope_id) ==
        (member["platform"], member["account_id"], member["native_account_id"]))


def group_for_target(runtime, target):
    if target.layer not in (ConfigLayer.RELATIONSHIP, ConfigLayer.WINDOW):
        return None
    check = getattr(runtime, "private_account_link_persona", None)
    if check is None:
        return None
    subject_id = target.subject.subject_id if target.subject is not None else None
    if subject_id is None:
        # Reply settings intentionally use the original subject-less WINDOW key.
        # Resolve ownership only for matching; NEVER change that address/key or
        # read/write a newly fabricated subject-bearing copy of local settings.
        if target.scope is None or target.scope.kind.value != "private":
            return None
        from companion_v2.identity_policy.personas import PersonaDirectory
        route = PersonaDirectory(runtime).route(target.scope)
        if route is None or route.user is None:
            return None
        subject_id = route.user.subject_id
    matches = [group for group in PrivateAccountLinks(runtime).read()["groups"]
        if any(target_matches(target, member, subject_id) and check(member) == group["persona_id"]
               for member in group["members"])]
    return matches[0] if len(matches) == 1 else None


def overlay_snapshot(runtime, target, fields, local):
    if _LOCAL.get():
        return local
    group = group_for_target(runtime, target)
    if not group:
        return local
    values = dict(local.fields)
    selected = {key for keys in group.get("sharing", {}).values() for key in keys}
    shared = group.get("shared_values", {})
    for field in fields:
        key = field.field_id
        if key in selected and key in shared:
            value = field.validate(shared[key])
            values[key] = EffectiveValue(value, target, True, group.get("shared_revision", 0))
    token = digest([group["id"], group.get("shared_revision", 0),
                    group.get("sharing", {}), group["members"], group["persona_id"]])
    return OwnerSnapshot(local.revision, local.dependencies, values, token)


def split_write(runtime, target, fields, *, changes, reset, observed_at):
    """Called inside the owner's configuration-store transaction; no dual write."""
    group = group_for_target(runtime, target)
    if not group:
        return changes, reset, ()
    selected = {key for keys in group.get("sharing", {}).values() for key in keys}
    shared_keys = (set(changes) | set(reset)) & selected
    if not shared_keys:
        return changes, reset, ()
    links = PrivateAccountLinks(runtime)
    state = links.read()
    current = next(g for g in state["groups"] if g["id"] == group["id"])
    # Reset shared overrides to the acting account's inherited original value;
    # it still edits the ONE shared value and never destroys that account's local.
    inherited = None
    changed = []
    for key in sorted(shared_keys):
        if key in changes:
            value = changes[key]
        else:
            if inherited is None:
                parent = target.chain()[-2]
                inherited = SparseConfigurationOwner(runtime).read_local(parent, fields)
            value = inherited.fields[key].value
        if current["shared_values"].get(key) != value:
            current["shared_values"][key] = value
            changed.append(key)
    if changed:
        current["shared_revision"] = current.get("shared_revision", 0) + 1
        state["revision"] += 1
        with runtime.configuration_store.transaction() as connection:
            connection.execute("INSERT OR REPLACE INTO runtime_metadata VALUES (?, ?)",
                (KEY, json.dumps(state, ensure_ascii=False, separators=(",", ":"))))
    return {k: v for k, v in changes.items() if k not in shared_keys}, tuple(k for k in reset if k not in shared_keys), tuple(changed)


def seed_values(application, context):
    from .profile import target_for as profile_target
    from companion_v2.proactive.configuration import target_for as window_target
    from companion_v2.conversation.reply_settings import target_for as reply_target
    result = {}
    with unshared():
        for category, fields in definitions().items():
            target = {"profile": profile_target, "reply": reply_target, "contact": window_target}[category](context)
            registered = application.configuration.registry.group(fields[0].group_id)
            owner = application.configuration._owners[registered[0].owner_id]
            result.update({key: item.value for key, item in owner.read(target, registered).fields.items()})
    return result


def change_keys(runtime, target, group_id):
    keys = [layer.key for layer in target.chain()]
    group = group_for_target(runtime, target)
    if group and any(key.startswith(group_id + ".") for values in group.get("sharing", {}).values() for key in values):
        keys.extend(group.get("configuration_targets", {}).get(group_id, ()))
    return tuple(dict.fromkeys(keys))
