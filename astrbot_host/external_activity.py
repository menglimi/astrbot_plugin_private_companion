"""Run one explicitly registered external role activity and seal its receipt."""

from __future__ import annotations

import json

from companion_v2.adapters.astrbot.activity_providers import (
    ACTIVITY_API_VERSION, activity_provider_registry,
)
from companion_v2.execution.models import TaskError, digest, encode
from companion_v2.foundation.refs import RoleOwnerRef, ScopeKind
from companion_v2.role_work.models import RoleActivity, RoleActivityState
from companion_v2.role_work.work_models import RoleWorkSpec


MAX_INPUT_CHARS = 8192
MAX_SUMMARY_CHARS = 4000
MAX_ITEMS = 20


def activity_context_document(context):
    """Give an activity provider only the role/scope it acts for, never the user."""
    return json.loads(encode({
        "scope": context.scope,
        "role": context.role_binding.role,
        "role_binding_revision": context.role_binding.revision,
    }))


def _bounded_inputs(value):
    if value is None:
        return {}
    if type(value) is not dict:
        raise TaskError("external_activity_input_invalid", "activity inputs must be an object")
    try:
        encoded = encode(value)
    except (TypeError, ValueError) as error:
        raise TaskError("external_activity_input_invalid", "activity inputs must be JSON values") from error
    if len(encoded) > MAX_INPUT_CHARS:
        raise TaskError("external_activity_input_too_large", "activity inputs exceed the bounded payload")
    return json.loads(encoded)


def _bounded_result(value):
    if type(value) is not dict or value.get("state") not in ("completed", "failed", "cancelled"):
        raise TaskError("external_activity_result_invalid", "activity result must declare a bounded state")
    summary = value.get("summary")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > MAX_SUMMARY_CHARS:
        raise TaskError("external_activity_result_invalid", "activity result needs a bounded summary")
    items = value.get("items", ())
    if not isinstance(items, (list, tuple)) or len(items) > MAX_ITEMS:
        raise TaskError("external_activity_result_invalid", "activity result items are not bounded")
    clean = []
    for item in items:
        if not isinstance(item, str) or not item.strip() or len(item) > 500:
            continue
        clean.append(item.strip())
    return value["state"], summary.strip(), tuple(clean)


class ExternalActivityHost:
    """Trusted host entry point used by the proactive scheduler later.

    It creates a real role activity before execution, so a provider cannot claim
    an activity that the role never started. Completion and cancellation both
    become ordinary role-life receipts, which the existing activity ingestion
    and proactive material pool already know how to read.
    """

    def __init__(self, controller):
        self.controller = controller

    def _application(self):
        session = self.controller.session or self.controller.last_session
        if session is None:
            raise TaskError("external_activity_profile_unavailable", "load the V2 profile first")
        return session.application

    def available_provider(self, purpose="life"):
        registry = activity_provider_registry(self.controller.context)
        entry = registry.select(purpose=purpose) if registry is not None else None
        if entry is None:
            return None
        return {"provider_id": entry.provider_id, "display_name": entry.display_name,
                "purpose": entry.purpose, "min_minutes": entry.min_minutes,
                "max_minutes": entry.max_minutes, "timeout_seconds": entry.timeout_seconds}

    async def run(self, context, provider_id, *, request_id, inputs=None):
        if context.scope.kind is not ScopeKind.PRIVATE:
            raise TaskError("external_activity_private_only", "external activities require a private role window")
        if not isinstance(provider_id, str) or not provider_id.strip() or len(provider_id) > 120:
            raise TaskError("external_activity_provider_invalid", "select a registered activity provider")
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 256:
            raise TaskError("external_activity_request_invalid", "provide a stable activity request key")
        registry = activity_provider_registry(self.controller.context)
        entry = registry.select(provider_id=provider_id) if registry is not None else None
        if entry is None:
            raise TaskError("external_activity_provider_unavailable", "the activity provider is not registered")
        application = self._application()
        application.require_running()
        owner = RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                             context.role_binding.role.subject_id)
        activity_id = "external-activity-" + digest([context.key, provider_id, request_id])
        try:
            existing = application.role_life.activity(activity_id, owner=owner)
            return {"state": existing.state.value, "activity_id": activity_id,
                    "summary": existing.checkpoint, "items": (), "replayed": True}
        except KeyError:
            pass
        payload = _bounded_inputs(inputs)
        started_at = application.now()
        activity = RoleActivity(activity_id, context.role_binding.role.subject_id, context.scope,
            started_at, RoleActivityState.ACTIVE, description=entry.display_name,
            work=RoleWorkSpec("external", context.key, request_id))
        application.role_life.add_activity(activity)
        request = {"schema": ACTIVITY_API_VERSION, "provider_id": provider_id,
                   "activity_id": activity_id, "context": activity_context_document(context),
                   "inputs": payload, "max_seconds": entry.timeout_seconds}
        try:
            raw = await registry.invoke(entry, context, request)
            state, summary, items = _bounded_result(raw)
        except TaskError as error:
            state, summary, items = "cancelled", "活动执行端没有确认完成。", ()
            failure_code = getattr(error, "code", "external_activity_failed")
        else:
            failure_code = None
        checkpoint = summary
        if items:
            checkpoint = summary + "\n" + "\n".join(items)
        target_state = RoleActivityState.COMPLETED if state == "completed" else RoleActivityState.CANCELLED
        updated = application.role_life.transition_activity(activity_id, owner=activity.owner,
            expected_revision=1, state=target_state,
            operation_id=activity_id + ":" + ("completed" if state == "completed" else "cancelled"),
            checkpoint=checkpoint[:16384], observed_at=application.now())
        application.notify()
        return {"state": state, "activity_id": activity_id, "summary": summary,
                "items": items, "activity": updated, "failure_code": failure_code,
                "replayed": False}
