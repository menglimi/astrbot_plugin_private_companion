"""Read opt-in, consented mobile material for one private core reply.

``authorized_mobile_snapshot`` owns the shared private-window gate; health and
the phone foreground summary project the same snapshot differently.
"""

from __future__ import annotations

import logging

from companion_v2.capabilities.repositories import CapabilityRepository
from companion_v2.conversation.health_context import project_health_context
from companion_v2.execution.models import require
from companion_v2.foundation.refs import ScopeKind
from companion_v2.storage.codec import scope_values


logger = logging.getLogger(__name__)
REALITY_EXTENSION = "reality"
HEALTH_CAPABILITY = "reality.snapshot.read"


def _consent_active(runtime, context):
    """Check the same per-participant capability consent the Reality tool uses."""
    subject = getattr(getattr(context, "actor", None), "subject", None)
    if subject is None or getattr(getattr(context, "scope", None), "kind", None) != ScopeKind.PRIVATE:
        return False
    row = runtime.read_one(
        """SELECT 1 FROM capability_authorizations
           WHERE extension_id = ? AND capability_id = ? AND subject_kind = ? AND subject_id = ?
             AND installation_id = ? AND platform = ? AND scope_kind = ? AND native_scope_id = ?
             AND bot_id = ? AND account_id = ? AND conversation_id = ? AND session_id = ?
             AND state = 'active' LIMIT 1""",
        (REALITY_EXTENSION, HEALTH_CAPABILITY, subject.kind.value, subject.subject_id,
         *scope_values(context.scope)),
    )
    return row is not None


def _reality_registry(application):
    """Find the native registry even when optional tool ports are composed."""
    pending = [getattr(application, "native_tool_port", None)]
    seen = set()
    while pending:
        port = pending.pop()
        if port is None or id(port) in seen:
            continue
        seen.add(id(port))
        if callable(getattr(port, "active_service", None)):
            return port
        pending.extend(getattr(port, "ports", ()) or ())
    return None


def _role_enabled(runtime, context):
    projection = CapabilityRepository(runtime).projection(
        extension_id=REALITY_EXTENSION, role=context.role_binding.role, scope=context.scope)
    selection = require(projection).role_selection
    return bool(selection and selection.enabled)


def authorized_mobile_snapshot(application, context):
    """Return the mobile snapshot this private window is allowed to read.

    The gateway cache is already scoped to the current private user.  Role
    enablement and per-window consent are checked here once, so no consumer
    can turn a disabled Reality capability into a model-visible reading.
    Health and the phone foreground summary share this gate; each projects the
    snapshot differently and owns its own failure handling.
    """
    if getattr(getattr(context, "scope", None), "kind", None) != ScopeKind.PRIVATE:
        return None
    runtime = getattr(application, "runtime", None)
    registry = _reality_registry(application)
    if runtime is None or registry is None:
        return None
    service = registry.active_service(REALITY_EXTENSION)
    if service is None or not callable(getattr(service, "mobile_context", None)):
        return None
    try:
        if not _role_enabled(runtime, context) or not _consent_active(runtime, context):
            return None
        principal = getattr(getattr(context, "actor", None), "principal", None)
        user_id = getattr(principal, "native_account_id", "") if principal is not None else ""
        if not isinstance(user_id, str) or not user_id.strip():
            return None
        return service.mobile_context(user_id.strip())
    except Exception as error:
        logger.warning("[CompanionV2] 核心回复手机感知降级 error_type=%s", type(error).__name__)
        return None


def health_perception_snapshot(application, context, at):
    """Return a bounded health projection only for an authorized private window."""
    snapshot = authorized_mobile_snapshot(application, context)
    if snapshot is None:
        return None
    try:
        return project_health_context(snapshot, observed_at=at)
    except Exception as error:
        logger.warning("[CompanionV2] 核心回复健康感知降级 error_type=%s", type(error).__name__)
        return None


__all__ = ["authorized_mobile_snapshot", "health_perception_snapshot"]
