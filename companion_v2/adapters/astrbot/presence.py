"""Pinned QQ extension actions. No API-name guessing, account fallback or toggle loop."""

from companion_v2.execution.models import require
from companion_v2.foundation.outcome import Outcome
from .native import route_id


class OneBotPresencePort:
    def __init__(self, bridge, *, reverse_api_type, authorize, now):
        self.bridge, self.reverse_api_type, self.authorize, self.now = bridge, reverse_api_type, authorize, now

    async def _call(self, context, capability, action, *, before_dispatch=None, **parameters):
        try:
            scope, principal = context.scope, context.actor.principal
            if (context.audience.recipients != (context.actor.subject,)
                    or principal.host_id != self.bridge.host_id or principal.account_id != scope.account_id
                    or principal.bot_id != scope.bot_id or principal.platform != scope.platform
                    or route_id(principal.native_account_id) != scope.native_scope_id):
                return {"state": "not_started", "code": "presence_scope_mismatch"}
            check = self.authorize()
            if isinstance(check, Outcome):
                require(check)
            require(self.bridge.identities.authorize(context.actor, scope=scope, capability=capability, observed_at=self.now()))
            await self.bridge.verify_role(scope, context.role_binding)
            client = self.bridge.client(scope)
            api = client._api._wsr_api
            if (type(api) is not self.reverse_api_type or api._api_clients is not client._wsr_api_clients
                    or not client._wsr_api_clients.get(scope.account_id)):
                return {"state": "not_started", "code": "presence_transport_unavailable"}
            check = self.authorize()
            if isinstance(check, Outcome):
                require(check)
            require(self.bridge.identities.authorize(context.actor, scope=scope, capability=capability, observed_at=self.now()))
            if before_dispatch is not None:
                before_dispatch()
        except Exception:
            return {"state": "not_started", "code": "presence_authorization_changed"}
        try:
            value = await api.call_action(action, self_id=scope.account_id, **parameters)
            if value is None or isinstance(value, dict) and value.get("retcode", 0) == 0 and value.get("status", "ok") in ("ok", "async"):
                return {"state": "accepted"}
            # NapCat's OneBot implementation may acknowledge status actions with
            # a plain success string instead of an empty/dict payload.
            status_ack = {"set_diy_online_status": "set status success",
                          "set_online_status": "set online status success",
                          "set_input_status": "set input status success"}
            if isinstance(value, str) and value.strip().lower() == status_ack.get(action):
                return {"state": "accepted"}
            return {"state": "failed", "code": "presence_action_rejected"}
        except Exception:
            return {"state": "unknown", "code": "presence_ack_unknown"}

    async def typing(self, context, *, before_dispatch=None):
        return await self._call(context, "platform.typing", "set_input_status",
                                user_id=int(context.scope.native_scope_id), event_type=1, before_dispatch=before_dispatch)

    async def custom_status(self, context, wording, *, before_dispatch=None):
        if type(wording) is not str or not wording.strip() or len(wording) > 8:
            return {"state": "not_started", "code": "presence_wording_invalid"}
        return await self._call(context, "platform.presence", "set_diy_online_status",
                                face_id=21, face_type=1, wording=wording, before_dispatch=before_dispatch)

    async def online_status(self, context, mode, *, before_dispatch=None):
        status = {"online": 10, "away": 30, "busy": 50, "invisible": 40}.get(mode) if type(mode) is str else None
        if status is None:
            return {"state": "not_started", "code": "presence_mode_invalid"}
        return await self._call(context, "platform.presence", "set_online_status",
                                status=status, ext_status=0, battery_status=0, before_dispatch=before_dispatch)


class TelegramPresencePort:
    """Telegram chat actions (typing) for the optional presence lease.

    Telegram has no OneBot ``set_input_status`` equivalent and no custom online
    status; only the typing action is mapped.  Everything else reports
    ``not_started`` so the caller keeps its normal no-presence behaviour.
    """

    def __init__(self, bridge, *, authorize, now):
        self.bridge, self.authorize, self.now = bridge, authorize, now

    def _not_started(self, code):
        return {"state": "not_started", "code": code}

    async def _prepare(self, context, capability, *, before_dispatch=None):
        from companion_v2.execution.models import require
        from companion_v2.foundation.outcome import Outcome
        from .native import route_id

        try:
            scope, principal = context.scope, context.actor.principal
            if (context.audience.recipients != (context.actor.subject,)
                    or principal.host_id != self.bridge.host_id or principal.account_id != scope.account_id
                    or principal.bot_id != scope.bot_id or principal.platform != scope.platform
                    or route_id(principal.native_account_id) != scope.native_scope_id):
                return None, self._not_started("presence_scope_mismatch")
            check = self.authorize()
            if isinstance(check, Outcome):
                require(check)
            require(self.bridge.identities.authorize(context.actor, scope=scope,
                                                     capability=capability, observed_at=self.now()))
            await self.bridge.verify_role(scope, context.role_binding)
            client = self.bridge.client(scope)
            check = self.authorize()
            if isinstance(check, Outcome):
                require(check)
            require(self.bridge.identities.authorize(context.actor, scope=scope,
                                                     capability=capability, observed_at=self.now()))
            if before_dispatch is not None:
                before_dispatch()
        except Exception:
            return None, self._not_started("presence_authorization_changed")
        return (scope, client), None

    async def typing(self, context, *, before_dispatch=None):
        prepared, refusal = await self._prepare(context, "platform.typing",
                                                before_dispatch=before_dispatch)
        if prepared is None:
            return refusal
        scope, client = prepared
        try:
            chat_id, _, thread = str(scope.native_scope_id).partition("#")
            payload = {"chat_id": int(chat_id) if chat_id.lstrip("-").isdigit() else chat_id,
                       "action": "typing"}
            if thread:
                payload["message_thread_id"] = int(thread)
            await client.send_chat_action(**payload)
            return {"state": "accepted"}
        except Exception:
            return {"state": "unknown", "code": "presence_ack_unknown"}

    async def custom_status(self, context, wording, *, before_dispatch=None):
        return self._not_started("presence_unsupported")

    async def online_status(self, context, mode, *, before_dispatch=None):
        return self._not_started("presence_unsupported")


class NullPresencePort:
    """Explicit no-presence port for adapters without a presence transport."""

    def _not_started(self, code="presence_unsupported"):
        return {"state": "not_started", "code": code}

    async def typing(self, context, *, before_dispatch=None):
        return self._not_started()

    async def custom_status(self, context, wording, *, before_dispatch=None):
        return self._not_started()

    async def online_status(self, context, mode, *, before_dispatch=None):
        return self._not_started()


def presence_port_for(bridge, platform_name, *, reverse_api_type=None, authorize, now):
    """Select the presence transport for one native platform family."""
    if platform_name == "telegram":
        return TelegramPresencePort(bridge, authorize=authorize, now=now)
    if platform_name == "aiocqhttp":
        return OneBotPresencePort(bridge, reverse_api_type=reverse_api_type,
                                  authorize=authorize, now=now)
    return NullPresencePort()
