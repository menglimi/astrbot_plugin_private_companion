"""Dashboard model routing overrides for one configured group and role."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from astrbot.api.web import request
from companion_v2.configuration.models import ConfigurationError
from companion_v2.decision import LayaDecisionService, LayaSettingsStore
from companion_v2.decision.client import LayaClientError
from companion_v2.decision.service import safe_laya_client_error_reason
from companion_v2.decision.store import LayaSettingsError
from companion_v2.execution.models import TaskError, digest, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind, ScopeRef
from companion_v2.model_gateway.management import ModelManagement

from .group_settings import GroupTarget
from .laya_client import LayaDecisionClient
from .management_identity import dashboard_operator
from .model_api import PREFIX as MODEL_PREFIX, _document, _document_optional
from .web_api import outcome_response


class GroupModelWebAPI:
    def __init__(self, controller, target_id, role_id):
        self.controller, self.target_id, self.role_id = controller, target_id, role_id
        self._laya_lock = asyncio.Lock()

    def routes(self, prefix):
        return [(prefix, self.status, ["GET"], "Read this group's model routing"),
                (prefix + "/configure", self.configure, ["POST"], "Save this group's model routing"),
                (prefix + "/laya", self.laya_status, ["GET"], "Read global Laya configuration from this group"),
                (prefix + "/laya/save", self.laya_save, ["POST"], "Save global Laya configuration from this group"),
                (prefix + "/laya/logs", self.laya_logs, ["GET"], "Report group Laya log availability"),
                (prefix + "/laya/test", self.laya_test, ["POST"], "Test Laya from this group"),
                (prefix + "/laya/decide", self.laya_decide, ["POST"], "Preview a Laya decision from this group")]

    async def _laya_authorized(self):
        if request.query:
            raise TaskError("model_request_invalid", "model routes do not accept target overrides")
        operator, manager, _ = await self._prepare("role")
        require(manager.authorize(operator))
        return operator, manager

    def _laya_service(self, manager=None):
        application = getattr(manager, "application", None)
        context = getattr(manager, "context", None)
        session = getattr(self.controller, "session", None) or getattr(self.controller, "last_session", None)
        runtime = (getattr(application, "runtime", None)
                   or getattr(getattr(session, "application", None), "runtime", None)
                   or getattr(self.controller, "runtime", None))
        scope = getattr(context, "scope", None)
        raw_scope_key = getattr(scope, "key", None) if scope is not None else None
        scope_key = raw_scope_key() if callable(raw_scope_key) else raw_scope_key
        origin_key = getattr(context, "key", None) or scope_key
        return LayaDecisionService(LayaSettingsStore(Path(self.controller.data_root)),
                                   runtime=runtime, origin_key=origin_key, scope=scope,
                                   client_factory=LayaDecisionClient)

    @staticmethod
    def _laya_failure(error):
        return outcome_response(Outcome.failed(getattr(error, "code", "laya_unavailable"),
                                               str(getattr(error, "reason", error))))

    @staticmethod
    def _laya_client_failure(error, service=None):
        """Build an ok-enveloped failure payload so the host bridge keeps safe reasons."""
        api_key = ""
        if service is not None:
            try:
                api_key = str(service.store.raw().get("api_key") or "")
            except Exception:
                api_key = ""
        return {
            "status": "failed",
            "code": getattr(error, "code", "laya_unavailable"),
            "reason": safe_laya_client_error_reason(error, api_key=api_key),
        }

    async def laya_status(self):
        try:
            async with self.controller._control_lock:
                _operator, manager = await self._laya_authorized()
                return outcome_response(Outcome.success(self._laya_service(manager).status()))
        except (TaskError, LayaSettingsError, OSError, ValueError) as error:
            return self._laya_failure(error)

    async def laya_save(self):
        try:
            data = _document(await request.body(), {"revision", "changes"}, limit=16000)
            if type(data["changes"]) is not dict:
                raise ValueError("invalid Laya changes")
            async with self.controller._control_lock:
                _operator, manager = await self._laya_authorized()
                service = self._laya_service(manager)
                service.save(data["revision"], data["changes"])
                return outcome_response(Outcome.success(service.status()))
        except (TaskError, LayaSettingsError, ValueError, TypeError, KeyError, UnicodeError,
                RecursionError, OverflowError) as error:
            return self._laya_failure(error)

    async def laya_logs(self):
        try:
            async with self.controller._control_lock:
                _operator, manager = await self._laya_authorized()
                service = self._laya_service(manager)
                scope = getattr(getattr(manager, "context", None), "scope", None)
                raw_key = getattr(scope, "key", None) if scope is not None else None
                scope_key = raw_key() if callable(raw_key) else raw_key
                if service.runtime is None or not scope_key:
                    return outcome_response(Outcome.success({"events": [], "available": False}))
                events = service.recent(scope_key=scope_key)["events"]
                return outcome_response(Outcome.success({"events": events, "available": True, "scope_kind": "group"}))
        except (TaskError, LayaSettingsError, OSError, ValueError) as error:
            return self._laya_failure(error)

    async def laya_test(self):
        service = None
        try:
            _document(await request.body(), set(), limit=1024)
            async with self.controller._control_lock:
                _operator, manager = await self._laya_authorized()
                service = self._laya_service(manager)
                service.set_trace(source="probe")
            async with self._laya_lock:
                return outcome_response(Outcome.success(await service.test()))
        except LayaClientError as error:
            return outcome_response(Outcome.success(self._laya_client_failure(error, service)))
        except (TaskError, LayaSettingsError, OSError, ValueError, TypeError,
                KeyError, UnicodeError, RecursionError, OverflowError) as error:
            return self._laya_failure(error)

    async def laya_decide(self):
        service = None
        try:
            data = _document(await request.body(), {"facts"}, limit=32000)
            if type(data["facts"]) is not dict:
                raise ValueError("facts must be an object")
            async with self.controller._control_lock:
                _operator, manager = await self._laya_authorized()
                service = self._laya_service(manager)
                service.set_trace(source="dashboard_preview")
            async with self._laya_lock:
                return outcome_response(Outcome.success(await service.decide(data["facts"])))
        except LayaClientError as error:
            return outcome_response(Outcome.success(self._laya_client_failure(error, service)))
        except (TaskError, LayaSettingsError, OSError, ValueError, TypeError,
                KeyError, UnicodeError, RecursionError, OverflowError) as error:
            return self._laya_failure(error)

    async def _prepare(self, layer):
        controller = self.controller
        if controller._terminated:
            raise TaskError("staging_host_terminated", "plugin is stopped")
        username = request.username
        if type(username) is not str or not username.strip() or username.startswith("api_key:"):
            raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
        session = controller.session or controller.last_session
        if session is None or controller.runtime is None:
            raise TaskError("staging_not_initialized", "load the V2 profile first")
        target = controller.settings.target(self.target_id)
        if not isinstance(target, GroupTarget) or not any(
                item.bot_id == target.bot_id and item.role_id == self.role_id
                for item in controller.settings.personas):
            raise TaskError("group_model_target_required", "the configured group and role are unavailable")
        bridge = session.bridge.groups
        account = bridge.accounts.get((target.platform_id, target.account_id))
        if account is None:
            raise TaskError("group_model_target_required", "the configured group account is unavailable")
        conversation_id, role, _instruction = await bridge._role(account, target.group_id)
        if role.subject_id != self.role_id or role.installation_id != controller.runtime.installation_id:
            raise TaskError("group_model_role_changed", "the configured group now selects another role")
        scope = ScopeRef(controller.runtime.installation_id, target.platform_id, ScopeKind.GROUP,
                         target.group_id, target.bot_id, target.account_id, conversation_id, target.group_id)
        if bridge.target_for_scope(scope) != target:
            raise TaskError("group_model_target_required", "the configured group route changed")
        resolved = session.application.tasks.identity.resolve_role(scope, observed_at=controller.now())
        if resolved.failure is not None:
            if resolved.failure.code not in ("role_binding_missing", "scope_unresolved"):
                require(resolved)
            # Admin model settings may be opened before this group has produced
            # its first authenticated turn. The native persona was verified
            # above; this management-only context does not create a runtime
            # identity binding for a group without an authenticated speaker.
            binding = SimpleNamespace(role=role, revision=0)
        else:
            binding = resolved.unwrap()
        if binding.role != role:
            raise TaskError("group_model_role_changed", "the registered group role changed")
        context = SimpleNamespace(scope=scope, role_binding=binding,
            key=digest(["group-model-context.v1", scope.document(), binding.revision]))
        operator = dashboard_operator(controller.runtime, username, at=controller.now())
        manager = ModelManagement(session.application, context)
        return operator, manager, layer

    @staticmethod
    def _layer_query():
        query = request.query
        if set(query.keys()) - {"layer"} or any(len(query.getlist(key)) != 1 for key in query.keys()):
            raise TaskError("model_request_invalid", "model routes do not accept target overrides")
        layer = query.get("layer", "role")
        if layer not in ("role", "window"):
            raise TaskError("model_request_invalid", "select role defaults or this conversation window")
        return layer

    async def status(self):
        try:
            layer = self._layer_query()
            async with self.controller._control_lock:
                operator, manager, _ = await self._prepare(layer)
                result = require(manager.status(operator, layer=layer))
                result["scope_kind"] = "group"
                return outcome_response(Outcome.success(result))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except ConfigurationError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, AttributeError):
            return outcome_response(Outcome.failed("group_model_unavailable", "group model settings are unavailable"))

    async def configure(self):
        try:
            data = _document_optional(await request.body(),
                {"kind", "operation_key", "edit_token", "changes", "reset"},
                optional=("section", "layer"))
            layer = data.get("layer", "role")
            if layer not in ("role", "window"):
                raise TaskError("model_request_invalid", "select role defaults or this conversation window")
            if (data.get("kind") == "conversation.reply" and not data.get("section")
                    and ({*(data.get("changes") or {}), *(data.get("reset") or [])}
                         & {"provider", "fallback", "max_output_tokens", "timeout_seconds"})):
                raise TaskError("model_configuration_host_managed",
                                "AstrBot manages the active group reply model; only V2 task routes are editable here")
            async with self.controller._control_lock:
                operator, manager, _ = await self._prepare(layer)
                result = require(manager.configure(operator, data))
                result["task"]["scope_kind"] = "group"
                return outcome_response(Outcome.success(result))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except ConfigurationError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, AttributeError):
            return outcome_response(Outcome.failed("group_model_request_invalid", "invalid group model settings request"))
