"""Role-level capability enablement shared by assembly and access routes.

A capability package is assembled once for the installation, but each Bot role
still has to opt into it.  The Dashboard already scopes its capability panel to
one role, so assembly records that role in the same transaction that reads the
successful installation.  Consent stays a separate, per-window decision.
"""

from __future__ import annotations

from companion_v2.capabilities import CapabilityManagementService, CapabilityRepository
from companion_v2.configuration.models import fingerprint
from companion_v2.execution.models import TaskError, require
from companion_v2.foundation.refs import OperationRef, SubjectKind, SubjectRef, VersionRef


# Only the privacy-sensitive native modules need this bridge: they are the
# capabilities whose core consent is implicit, so a missing role row otherwise
# looks permanently disabled after a successful assembly.
AUTO_ROLE_EXTENSIONS = frozenset(("reality", "companion.screen.native"))

ROLE_SELECTION_FIELDS = frozenset(("operation_key", "expected_revision", "enabled"))


def validate_role_selection_command(command, *, error_code: str):
    """Accept exactly the bounded role-enable command used by the page."""

    if type(command) is not dict or set(command) != ROLE_SELECTION_FIELDS:
        raise TaskError(error_code, "a role selection command is required")
    if type(command["enabled"]) is not bool:
        raise TaskError(error_code, "enabled must be boolean")
    revision = command["expected_revision"]
    if type(revision) is not int or isinstance(revision, bool) or revision < 0:
        raise TaskError(error_code, "expected_revision must be a non-negative integer")
    key = command["operation_key"]
    if (type(key) is not str or not 1 <= len(key) <= 128
            or any(not (char.isascii() and (char.isalnum() or char in "._:-")) for char in key)):
        raise TaskError(error_code, "operation_key must be a bounded stable identifier")
    return command


def set_role_selection(controller, operator, extension_id: str, context, command, at):
    """Apply one idempotent role selection to the loaded role context."""

    role = context.role_binding.role
    bot_id = context.scope.bot_id
    management = CapabilityManagementService(controller.runtime)
    scope = management.role_scope(extension_id, role, bot_id)
    operation = OperationRef(
        "capability-role-ui-" + fingerprint(
            [extension_id, role.subject_id, bot_id or "", command["operation_key"]]),
        management.OWNER,
        scope,
        VersionRef("capability-role", 1),
        command["operation_key"],
    )
    return require(management.set_role_enabled(
        operator, extension_id=extension_id, role=role, bot_id=bot_id,
        enabled=command["enabled"], expected_revision=command["expected_revision"],
        operation=operation, observed_at=at,
    ))


def enable_role(runtime, extension_id: str, role, bot_id, at):
    """Idempotently enable an assembled capability for exactly one Bot role."""

    repository = CapabilityRepository(runtime)
    current = repository.role_selection(extension_id=extension_id, role=role, bot_id=bot_id or None)
    if current.failure is not None:
        if current.failure.code != "capability_role_not_enabled":
            raise TaskError(current.failure.code, current.failure.reason)
        selection = None
    else:
        selection = current.unwrap()
        if selection.enabled:
            return selection
    return require(repository.set_role_enabled(
        extension_id=extension_id, role=role, bot_id=bot_id or None, enabled=True,
        expected_revision=selection.revision if selection is not None else 0,
        observed_at=at,
    ))


def lifecycle_role_identity(controller, *, role_id: str | None = None, target_id: str | None = None):
    """Return ``(bot role, bot id)`` for an assembly, if one can be identified.

    A resolved context is preferred because it carries the canonical role id.
    When the installation has not seen a message yet, the selected Dashboard
    role and target are still enough to write the role row directly.
    """

    context = lifecycle_role_context(controller, role_id=role_id, target_id=target_id)
    if context is not None:
        return context.role_binding.role, context.scope.bot_id
    runtime = getattr(controller, "runtime", None)
    settings = getattr(controller, "settings", None)
    if runtime is None or settings is None:
        return None
    personas = tuple(getattr(settings, "personas", ()) or ())
    configured = {(getattr(item, "bot_id", None), getattr(item, "role_id", None)) for item in personas}
    configured = {item for item in configured if None not in item}
    if role_id is None:
        if len(configured) != 1:
            return None
        bot_id, role_id = next(iter(configured))
    else:
        bot_id = _target_bot_id(settings, target_id)
        if bot_id is None:
            matches = {item[0] for item in configured if item[1] == role_id}
            if len(matches) != 1:
                return None
            bot_id = next(iter(matches))
    return SubjectRef(runtime.installation_id, SubjectKind.BOT, role_id), bot_id


def _target_bot_id(settings, target_id):
    if not target_id:
        return None
    lookup = getattr(settings, "target", None)
    if callable(lookup):
        try:
            return getattr(lookup(target_id), "bot_id", None)
        except (TaskError, KeyError, ValueError, AttributeError):
            return None
    if target_id == getattr(settings, "profile_id", None):
        return getattr(settings, "bot_id", None)
    return None


def lifecycle_role_context(controller, *, role_id: str | None = None, target_id: str | None = None):
    """Resolve the role that initiated an assembly, never a guessed default.

    The Dashboard sends the currently selected role explicitly.  Older callers
    and the single-profile host may omit it; those cases fall back to the loaded
    private context, or to the one role configured for the installation.
    """

    if role_id is not None:
        context = _authority_context(controller, target_id, role_id)
        if context is not None:
            return context
    session = getattr(controller, "session", None) or getattr(controller, "last_session", None)
    target = getattr(getattr(session, "profile", None), "target", None)
    if target is not None:
        role = getattr(getattr(target, "role_binding", None), "role", None)
        if role_id is None or (role is not None and role.subject_id == role_id):
            return target
    if role_id is not None:
        return None
    return _unique_role_context(controller, session)


def _authority_context(controller, target_id, role_id):
    if not target_id:
        return None
    resolve = getattr(getattr(controller, "authority", None), "context", None)
    if not callable(resolve):
        return None
    try:
        return resolve(target_id, role_id)
    except (TaskError, KeyError, AttributeError, ValueError):
        return None


def _unique_role_context(controller, session):
    settings = getattr(controller, "settings", None)
    personas = tuple(getattr(settings, "personas", ()) or ())
    roles = {(getattr(item, "bot_id", None), getattr(item, "role_id", None)) for item in personas}
    roles = {item for item in roles if None not in item}
    if len(roles) != 1:
        return None
    bot_id, role_id = next(iter(roles))
    target = getattr(getattr(session, "profile", None), "target", None)
    if target is not None:
        role = getattr(getattr(target, "role_binding", None), "role", None)
        if role is not None and role.subject_id == role_id:
            return target
    targets = tuple(getattr(settings, "all_targets", ()) or getattr(settings, "targets", ()) or ())
    for candidate in targets:
        if (getattr(candidate, "bot_id", None) != bot_id
                or getattr(candidate, "group_id", None) is not None):
            continue
        context = _authority_context(controller, getattr(candidate, "target_id", None), role_id)
        if context is not None:
            return context
    return None


__all__ = [
    "AUTO_ROLE_EXTENSIONS",
    "ROLE_SELECTION_FIELDS",
    "enable_role",
    "lifecycle_role_context",
    "lifecycle_role_identity",
    "set_role_selection",
    "validate_role_selection_command",
]
