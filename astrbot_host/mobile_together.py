"""Authenticated mobile entry to the native Together room provider."""

from __future__ import annotations

from datetime import timedelta
import asyncio
import inspect
import json
import re

from companion_v2.conversation.external_tools import ExternalToolInvocation
from companion_v2.execution.models import TaskError, digest, encode, require
from companion_v2.foundation.refs import ScopeRef
from companion_v2.sources.models import GovernanceState
from companion_v2.together.mobile.requests import MobileRoomRequests


REALITY_PLUGIN_ID = "astrbot_plugin_private_companion.reality"
TOGETHER_PLUGIN_ID = "astrbot_plugin_private_companion.together"
TOGETHER_ACTIONS = {
    "create": "together.room.create",
    "invite": "together.room.invite",
    "join": "together.room.join",
}
REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


class MobileTogetherBridge:
    """Resolve a paired phone to a configured V2 private context for each call."""

    def __init__(self, controller):
        self.controller = controller

    def _extension_api(self, plugin_id):
        registry = getattr(self.controller, "native_extensions", None)
        return registry.api(plugin_id.rsplit(".", 1)[-1]) if registry is not None else None

    def _mobile_user(self, session_token):
        if type(session_token) is not str or not 24 <= len(session_token) <= 512:
            raise TaskError("mobile_session_invalid", "the paired mobile session is unavailable")
        api = self._extension_api(REALITY_PLUGIN_ID)
        validator = getattr(api, "companion_v2_mobile_session_user", None) if api is not None else None
        if not callable(validator) or inspect.iscoroutinefunction(validator):
            raise TaskError("mobile_session_bridge_unavailable", "Reality cannot attest the mobile session")
        result = validator(session_token)
        if (type(result) is not dict or result.get("schema") != "companion.mobile-session.v1"
                or type(result.get("user_id")) is not str or not result["user_id"]):
            raise TaskError("mobile_session_invalid", "the paired mobile session is no longer valid")
        return result["user_id"]

    def mobile_session_user(self, session_token):
        user_id = self._mobile_user(session_token)
        return {"schema": "companion.mobile-session.v1", "user_id": user_id}

    def pairing_candidates(self):
        """List only people with an exact, current V2 private conversation.

        This is a pairing convenience, not a capability grant. Every later room
        operation still checks the phone session and current per-action policy.
        """
        controller = self.controller
        try:
            self._running_session()
        except TaskError:
            return ()
        candidates = set()
        if getattr(controller, "_multi_host", False):
            settings = getattr(controller, "settings", None)
            for target in getattr(settings, "targets", ()):
                user_id = getattr(target, "participant_id", "")
                if user_id and user_id != "*":
                    candidates.add(user_id)
            runtime = getattr(controller, "runtime", None)
            if runtime is not None:
                for row in runtime.read_all("SELECT value FROM runtime_metadata WHERE key LIKE ?",
                                            ("host.multi.active:%",)):
                    try:
                        scope = ScopeRef.restore(json.loads(row["value"]))
                    except (ValueError, TypeError, KeyError):
                        continue
                    if scope.kind.value == "private":
                        candidates.add(scope.native_scope_id)
        else:
            target = getattr(getattr(controller.session, "profile", None), "target", None)
            user_id = getattr(getattr(getattr(target, "actor", None), "principal", None),
                              "native_account_id", "")
            if user_id:
                candidates.add(user_id)
        current = []
        for user_id in sorted(candidates):
            try:
                if self._target_context(user_id) is not None:
                    current.append(user_id)
            except TaskError:
                continue
        return tuple(current)

    def core_pairing_candidates(self):
        """Return exact private targets whose V2 identity is a core administrator."""
        runtime = getattr(self.controller, "runtime", None)
        if runtime is None:
            return ()
        from companion_v2.identity_policy.models import AuthorizationTier
        try:
            rows = runtime.read_all(
                "SELECT subject_id FROM subject_access_tiers "
                "WHERE subject_kind = 'human' AND tier = ?",
                (AuthorizationTier.CORE.value,),
            )
        except Exception:
            return ()
        core_subjects = {str(row["subject_id"]) for row in rows}
        if not core_subjects:
            return ()
        matches = []
        for user_id in self.pairing_candidates():
            try:
                resolved = self._target_context(user_id)
            except TaskError:
                continue
            context = resolved[2] if isinstance(resolved, tuple) and len(resolved) == 3 else None
            subject = getattr(getattr(context, "actor", None), "subject", None)
            kind = getattr(getattr(subject, "kind", None), "value", "")
            subject_id = getattr(subject, "subject_id", "")
            if kind == "human" and subject_id in core_subjects:
                matches.append(user_id)
        return tuple(sorted(set(matches)))

    def _running_session(self):
        controller = self.controller
        session = getattr(controller, "session", None)
        application = getattr(session, "application", None) if session is not None else None
        if (getattr(controller, "_terminated", False) or session is None or application is None
                or getattr(application, "state", None) != "running"):
            raise TaskError("mobile_v2_host_not_running", "the V2 companion host is not running")
        return session, application

    def _target_context(self, user_id):
        controller = self.controller
        if getattr(controller, "_multi_host", False):
            settings, authority = getattr(controller, "settings", None), getattr(controller, "authority", None)
            if settings is None:
                raise TaskError("mobile_v2_host_not_ready", "the V2 private target is not ready")
            # A wildcard private target is the persisted account mapping. Resolve
            # the current person through the same route resolver used by inbound
            # messages instead of requiring one stored row per person.
            # An explicit participant mapping wins over a wildcard account
            # template.  Wildcard targets exist so a test account can accept
            # arbitrary private users without every user becoming ambiguous
            # with the normal account's explicitly configured primary target.
            configured_target_ids = {getattr(target, "target_id", None)
                                     for target in getattr(settings, "targets", ())}
            exact_matches, wildcard_matches = [], []
            for account in settings.accounts:
                target = settings.private_target(account.platform_id, account.self_id, user_id)
                if target is None:
                    continue
                target_id = getattr(target, "target_id", None)
                if target_id is not None and configured_target_ids and target_id not in configured_target_ids:
                    wildcard_matches.append(target)
                else:
                    exact_matches.append(target)
            matches = exact_matches or wildcard_matches
            if not matches:
                return None
            if len(matches) != 1:
                raise TaskError("mobile_v2_target_ambiguous", "the paired user matches more than one V2 private target")
            if authority is None:
                raise TaskError("mobile_v2_host_not_ready", "the V2 private target is not ready")
            session, application = self._running_session()
            target = matches[0]
            active = authority.read("host.multi.active:" + target.target_id)
            if not isinstance(active, dict):
                raise TaskError("mobile_v2_context_unavailable", "the V2 target has no active conversation persona")
            scope = ScopeRef.restore(active)
            resolution = require(authority.personas.resolve(scope, observed_at=controller.now()))
            role_id = resolution.binding.role.subject_id
            context = authority.context(target.target_id, role_id)
            if context.scope != scope:
                raise TaskError("mobile_v2_context_changed", "the active V2 conversation changed")
        else:
            session = getattr(controller, "session", None)
            target = getattr(getattr(session, "profile", None), "target", None) if session is not None else None
            if target is None:
                try:
                    settings = controller.resolved_settings()
                except Exception as error:
                    raise TaskError("mobile_v2_host_not_ready", "the V2 private target cannot be verified") from error
                if settings.has_target and settings.participant_id == user_id:
                    raise TaskError("mobile_v2_host_not_running", "the V2 companion host is not running")
                if not settings.has_target:
                    return None
                return None
            if target.actor.principal.native_account_id != user_id:
                return None
            session, application = self._running_session()
            context = target
        principal = context.actor.principal
        if (context.scope.kind.value != "private" or principal.native_account_id != user_id
                or context.scope.native_scope_id != user_id):
            raise TaskError("mobile_v2_target_mismatch", "the paired phone differs from its configured V2 private target")
        return session, application, context

    def _admitted(self, registry, application, context, action):
        declaration = next((item for item in registry.declarations()
                            if item.descriptor.tool.action == action
                            and registry.current_declaration(item.host_tool_id) == item), None)
        if declaration is None or application.external is None:
            return None
        try:
            application.external.admit(declaration.descriptor, context, self.controller.now())
        except TaskError:
            return None
        return declaration

    def _together_status(self):
        api = self._extension_api(TOGETHER_PLUGIN_ID)
        getter = getattr(api, "companion_v2_status", None) if api is not None else None
        if not callable(getter):
            return {"enabled": False, "running": False}
        try:
            status = getter()
        except Exception:
            return {"enabled": False, "running": False}
        if (type(status) is not dict or status.get("schema") != "companion.provider-status.v1"
                or status.get("plugin_id") != TOGETHER_PLUGIN_ID or type(status.get("enabled")) is not bool):
            return {"enabled": False, "running": False}
        return {"enabled": status["enabled"], "running": status.get("runtime_ready") is True}

    def status(self, session_token):
        user_id = self._mobile_user(session_token)
        resolved = self._target_context(user_id)
        if resolved is None:
            return {"matched": False}
        session, application, context = resolved
        registry = getattr(self.controller, "native_extensions", None)
        if registry is None or registry.application is not application:
            return {"matched": True, "available": False, "reason": "mobile_v2_provider_unavailable"}
        status = self._together_status()
        modes = []
        actions = {}
        for name, action in TOGETHER_ACTIONS.items():
            declaration = self._admitted(registry, application, context, action)
            actions[name] = declaration is not None
            if name == "create" and declaration is not None:
                schema = json.loads(declaration.descriptor.tool.parameters_json)
                modes = schema.get("properties", {}).get("mode", {}).get("enum", [])
        lifecycle_reader = getattr(registry, "status", None)
        lifecycle = lifecycle_reader("together") if callable(lifecycle_reader) else {}
        failed = lifecycle.get("state") == "failed"
        ready = bool(status["enabled"] and actions["create"] and not failed)
        reason = ("mobile_together_disabled" if not status["enabled"] else
                  "mobile_v2_together_start_failed" if failed else
                  "mobile_together_not_authorized" if not actions["create"] else None)
        return {"matched": True, "available": status["enabled"], "ready": ready,
                "enabled": status["enabled"], "running": status["running"], "capabilities": actions,
                "modes": [item for item in modes if type(item) is str],
                "reason": reason}

    async def invoke(self, session_token, *, request_id, operation, arguments):
        legacy_create = operation == "create" and (request_id is None or request_id == "")
        if not legacy_create and (type(request_id) is not str or not REQUEST_ID.fullmatch(request_id)):
            raise TaskError("mobile_request_id_required", "a stable request_id is required for this room operation")
        if operation not in TOGETHER_ACTIONS or type(arguments) is not dict:
            raise TaskError("mobile_together_request_invalid", "the room operation is invalid")
        user_id = self._mobile_user(session_token)
        resolved = self._target_context(user_id)
        if resolved is None:
            return {"matched": False}
        session, application, context = resolved
        registry = getattr(self.controller, "native_extensions", None)
        if registry is None or registry.application is not application:
            raise TaskError("mobile_v2_provider_unavailable", "the V2 Together provider is unavailable")
        action = TOGETHER_ACTIONS[operation]
        declaration = self._admitted(registry, application, context, action)
        if declaration is None:
            raise TaskError("mobile_together_not_authorized", "the current V2 target is not authorized for this room operation")
        if not self._together_status()["enabled"]:
            raise TaskError("mobile_together_disabled", "the Together room service is disabled")
        requests = None
        if legacy_create:
            modes = json.loads(declaration.descriptor.tool.parameters_json).get("properties", {}).get("mode", {}).get("enum", [])
            if arguments.get("mode") not in modes or set(arguments) != {"mode"}:
                raise TaskError("mobile_together_request_invalid", "the room mode is invalid")
            requests = MobileRoomRequests(application.runtime)
            from .native_extensions.together.mobile_requests import room_request_available
            getter = getattr(registry, "active_service", None)
            service = getter("together") if callable(getter) else None
            request_id = requests.begin(user_id, context.key, arguments.get("mode"),
                now=self.controller.now().timestamp(),
                room_available=lambda original: room_request_available(service, context, original))
        try:
            response = await self._invoke_native(session_token, user_id, session, application, context,
                                                  registry, declaration, request_id, arguments)
        except TaskError as error:
            error.request_id = request_id
            raise
        if requests is not None:
            requests.finish(user_id, arguments.get("mode"), request_id, response)
        return {**response, "request_id": request_id}

    async def _invoke_native(self, session_token, user_id, session, application, context,
                             registry, declaration, request_id, arguments):
        action = declaration.descriptor.tool.action
        stable = digest(["mobile-together.v1", action, request_id, context.key])
        invocation = ExternalToolInvocation(
            "mobile-" + stable, "mobile-parent-" + stable, "mobile-call-" + stable,
            context, declaration.descriptor, encode(arguments),
            self.controller.now() + timedelta(seconds=declaration.descriptor.timeout_seconds + 2),
            before_dispatch=lambda: self._recheck(session_token, user_id, context, application,
                                                   registry, declaration),
        )
        try:
            response = await asyncio.wait_for(
                registry.invoke(declaration.host_tool_id, invocation),
                timeout=declaration.descriptor.timeout_seconds + 2,
            )
        except asyncio.TimeoutError as error:
            raise TaskError("mobile_together_result_unknown", "the room operation result is not confirmed; do not submit a new request") from error
        except Exception as error:
            # The provider persists the original invocation identity. Reusing
            # a request_id with different arguments is a known client error,
            # not an unknown room outcome and must never be retried as a new
            # operation.
            if getattr(error, "code", None) == "native_original_invocation_mismatch":
                raise TaskError(
                    "mobile_together_request_conflict",
                    "the request_id was already used with different room arguments",
                ) from error
            raise
        if response.state != "succeeded":
            return {"matched": True, "state": response.state, "effect": response.effect,
                    "operation_id": response.operation_id, "receipt_ref": response.receipt_ref}
        if response.source is None or response.source.state is not GovernanceState.ACTIVE:
            raise TaskError("mobile_together_result_unavailable", "the operation completed but its result is no longer authorized")
        metadata = json.loads(response.source.payload).get("native", {})
        result = metadata.get("result")
        if type(result) is not dict:
            raise TaskError("mobile_together_result_invalid", "the native room provider returned no usable result")
        self._recheck(session_token, user_id, context, application, registry, declaration)
        return {"matched": True, "state": "succeeded", "effect": response.effect,
                "operation_id": response.operation_id, "receipt_ref": response.receipt_ref,
                "result": result}

    @staticmethod
    def mobile_result(operation, response):
        if not response.get("matched") or response.get("state") != "succeeded":
            return response
        result = response.get("result") or {}
        if operation == "create":
            room = result.get("room") if type(result.get("room")) is dict else {}
            projected = {"room": {key: room[key] for key in ("url", "mode", "expires_at") if key in room}}
        elif operation == "invite":
            invitation = result.get("invitation") if type(result.get("invitation")) is dict else {}
            projected = {"invitation": {key: invitation[key] for key in
                ("invitation_id", "room_id", "room_mode", "expires_at") if key in invitation}}
        else:
            joined = result.get("join") if type(result.get("join")) is dict else {}
            projected = {"join": {key: joined[key] for key in
                ("invitation_id", "room_id", "room_mode", "join_url", "expires_at", "replayed")
                if key in joined}}
        return {**response, "result": projected}

    def _recheck(self, session_token, user_id, context, application, registry, declaration):
        if self._mobile_user(session_token) != user_id:
            raise TaskError("mobile_session_changed", "the paired mobile session changed")
        resolved = self._target_context(user_id)
        if resolved is None or resolved[1] is not application or resolved[2] != context:
            raise TaskError("mobile_v2_context_changed", "the authorized V2 conversation changed")
        if registry.current_declaration(declaration.host_tool_id) != declaration:
            raise TaskError("mobile_v2_provider_changed", "the native Together provider changed")
        application.external.admit(declaration.descriptor, context, self.controller.now())
