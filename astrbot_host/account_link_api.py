"""Dashboard-only account linking on already authenticated private routes."""
from companion_v2.execution.models import TaskError, require, digest
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind
from companion_v2.configuration.models import ConfigurationError
from companion_v2.relationships.account_link_settings import catalog, seed_values
from companion_v2.identity_policy.management import PERSON_ROUTE_KEYS
from companion_v2.relationships.account_links import PrivateAccountLinks, member_for, member_key
from companion_v2.relationships.account_link_context import has_legacy_takeover, persona_for

PREFIX = "/astrbot_plugin_private_companion/acquaintance/account-links"


class AccountLinkWebAPI:
    def __init__(self, acquaintance):
        self.api = acquaintance
        self.controller = acquaintance.controller

    def routes(self):
        return [(PREFIX, self.get, ["GET"], "Read one persona's explicit private account association"),
                (PREFIX, self.save, ["POST"], "Associate accounts without changing identity or reply routes"),
                (PREFIX + "/unlink", self.unlink, ["POST"], "Remove the entire selected account association"),
                (PREFIX + "/history", self.history, ["GET"], "Read original records across associated private accounts")]

    def _settings(self):
        if not self.controller._multi_host:
            raise TaskError("account_link_shared_host_required", "account linking requires the shared host")
        return self.api._settings()

    def _persona(self, settings, context):
        if has_legacy_takeover(settings, member_for(context)):
            raise TaskError("account_link_legacy_route_conflict", "unlink the old primary-window takeover before associating accounts")
        persona = persona_for(settings, member_for(context))
        if not persona:
            raise TaskError("account_link_persona_ambiguous", "the current route must resolve one native persona")
        return persona

    def _candidates(self, settings, context):
        persona = self._persona(settings, context)
        candidates = {}
        for entry in self.api._participants():
            scope = entry.get("scope")
            if scope is None or scope.kind is not ScopeKind.PRIVATE or not entry.get("role_id"):
                continue
            request = {"subject_id": entry["subject"].subject_id,
                "route": {"platform": scope.platform, "bot_id": scope.bot_id,
                          "account_id": scope.account_id, "role_id": entry["role_id"]}}
            try:
                candidate = self.api._context(request["subject_id"], request["route"])
                if (candidate.role_binding.role != context.role_binding.role
                        or self._persona(settings, candidate) != persona):
                    continue
                member = member_for(candidate)
                candidates[tuple(member.values())] = {**request, "native_account_id": scope.native_scope_id,
                    "label": f"{scope.platform} · {scope.native_scope_id}（Bot {scope.account_id}）"}
            except TaskError:
                continue
        return list(candidates.values())

    async def get(self):
        from .web_api import outcome_response
        try:
            async with self.controller._control_lock:
                await self.api._operator()
                settings = self._settings()
                query = self.api._query(("subject_id", *PERSON_ROUTE_KEYS), required=("subject_id", *PERSON_ROUTE_KEYS))
                context = self.api._context(query["subject_id"], self.api._route_query(query))
                return outcome_response(Outcome.success({**PrivateAccountLinks(self.controller.runtime).status(context),
                    "candidates": self._candidates(settings, context), "current": member_for(context),
                    "fields": catalog(),
                    "supported": {"context": True, "settings": True, "identity": True, "proactive": True, "memory": True}}))
        except (TaskError, ConfigurationError) as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("account_link_command_invalid", "invalid private account association"))

    def _application(self):
        session = self.controller.session or self.controller.last_session
        if session is None:
            raise TaskError("staging_not_initialized", "load the private session first")
        return session.application

    async def history(self):
        from .web_api import outcome_response
        from companion_v2.relationships.account_link_history import history
        try:
            async with self.controller._control_lock:
                await self.api._operator()
                settings = self._settings()
                query = self.api._query(("subject_id", *PERSON_ROUTE_KEYS, "cursor"), required=("subject_id", *PERSON_ROUTE_KEYS))
                context = self.api._context(query["subject_id"], self.api._route_query(query))
                self._persona(settings, context)
                return outcome_response(Outcome.success(history(self._application(), context,
                    at=self.controller.now(), cursor=query.get("cursor"))))
        except (TaskError, ConfigurationError) as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError):
            return outcome_response(Outcome.failed("account_link_cursor_invalid", "invalid private history page"))

    async def save(self):
        return await self._write(unlink=False)

    async def unlink(self):
        return await self._write(unlink=True)

    async def _write(self, *, unlink):
        from .web_api import outcome_response
        try:
            async with self.controller._control_lock:
                _, operator = await self.api._operator()
                settings = self._settings()
                fields = {"subject_id", "route", "operation_key", "expected_revision"}
                if not unlink:
                    fields |= {"members", "share_context"}
                body = await self.api._command(expected=fields, optional=() if unlink else ("sharing", "seed", "identity", "proactive", "memory"))
                context = self.api._context(body["subject_id"], body["route"])
                links = PrivateAccountLinks(self.controller.runtime)
                arguments = {key: body[key] for key in ("expected_revision", "operation_key")}
                arguments.update(operator_id=operator.subject.subject_id, at=self.controller.now())
                if unlink:
                    result = links.unlink(context, **arguments)
                else:
                    members = body["members"]
                    if type(members) is not list or not 2 <= len(members) <= 16:
                        raise TaskError("account_link_command_invalid", "select two to sixteen private accounts")
                    contexts = []
                    persona = self._persona(settings, context)
                    for member in members:
                        if type(member) is not dict or set(member) != {"subject_id", "route"}:
                            raise TaskError("account_link_command_invalid", "select complete private account routes")
                        selected = self.api._context(member["subject_id"], member["route"])
                        if self._persona(settings, selected) != persona:
                            raise TaskError("account_link_persona_mismatch", "link accounts of the same persona only")
                        contexts.append(selected)
                    extras = {}
                    if "identity" in body and type(body["identity"]) is not dict:
                        raise TaskError("account_link_identity_invalid", "provide a separate identity sharing switch")
                    seed_context = None
                    if "sharing" in body or body.get("identity", {}).get("enabled") is True:
                        seed = body.get("seed")
                        if type(seed) is not dict or set(seed) != {"subject_id", "route"}:
                            raise TaskError("account_link_seed_required", "choose the initial source account")
                        seed_context = self.api._context(seed["subject_id"], seed["route"])
                        if member_for(seed_context) not in [member_for(item) for item in contexts]:
                            raise TaskError("account_link_seed_required", "seed must be an associated account")
                        session = self.controller.session or self.controller.last_session
                        if session is None:
                            raise TaskError("staging_not_initialized", "load the private session first")
                        if "sharing" in body:
                            extras.update(sharing=body["sharing"], seed=member_for(seed_context),
                                          initial_values=seed_values(session.application, seed_context))
                    previous = links.group_for(context)
                    if (previous and previous.get("identity", {}).get("enabled") is True
                            and "identity" not in body
                            and any(member_for(item) not in previous["members"] for item in contexts)):
                        raise TaskError("account_link_identity_confirmation_required", "explicitly confirm identity sharing when adding another account")
                    if "identity" in body:
                        from companion_v2.relationships.account_link_identity import CONFIRMATION, validate_identity
                        value = body["identity"]
                        if (type(value) is not dict or not {"enabled"} <= set(value)
                                or set(value) - {"enabled", "confirmation"} or type(value["enabled"]) is not bool):
                            raise TaskError("account_link_identity_invalid", "provide a separate identity sharing switch")
                        old = (previous or {}).get("identity", {})
                        retained = old.get("enabled") is True and previous["members"] == sorted(
                            [member_for(item) for item in contexts], key=member_key)
                        if value["enabled"] and not retained:
                            if value.get("confirmation") != CONFIRMATION:
                                raise TaskError("account_link_identity_confirmation_required", "explicitly confirm scope-bound identity and permission sharing")
                            from companion_v2.identity_policy.management import PAGE_MANAGED_CAPABILITIES
                            seed_status = self.api._management()._status(seed_context.actor.subject,
                                self.controller.now(), scope=seed_context.scope)
                            old = {"tier": seed_status.get("local_identity_tier", seed_status["identity_tier"]),
                                "permissions": {key: any(row["capability"] == key and row.get("local_state", row["effective_state"]) == "active"
                                    for row in seed_status["items"]) for key in PAGE_MANAGED_CAPABILITIES}}
                        extras["identity"] = {**old, "enabled": value["enabled"]}
                        if value["enabled"]:
                            validate_identity(extras["identity"])
                    if "memory" in body:
                        extras["memory"] = body["memory"]
                    if "proactive" in body:
                        extras["proactive"] = body["proactive"]
                    with links.store.transaction():
                        result = links.configure(context, contexts, persona_id=persona,
                            share_context=body["share_context"], command_hash=digest(body), **arguments, **extras)
                        if "sharing" in extras:
                            from companion_v2.relationships.account_link_settings import definitions
                            from companion_v2.relationships.profile import target_for as profile_target
                            from companion_v2.proactive.configuration import target_for as window_target
                            from companion_v2.conversation.reply_settings import target_for as reply_target
                            for selected in contexts:
                                for category, fields in definitions().items():
                                    registered = self._application().configuration.registry.group(fields[0].group_id)
                                    owner = self._application().configuration._owners[registered[0].owner_id]
                                    target = {"profile": profile_target, "reply": reply_target, "contact": window_target}[category](selected)
                                    owner.preview_set(target, registered, changes={})
                return outcome_response(Outcome.success(result))
        except (TaskError, ConfigurationError) as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("account_link_command_invalid", "invalid private account association"))
