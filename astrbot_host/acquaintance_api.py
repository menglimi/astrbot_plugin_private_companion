"""Dashboard directory and editors for trusted private-member routes."""

from __future__ import annotations

import json
import asyncio
import logging
from datetime import datetime, timezone
from time import perf_counter

from astrbot.api.web import request

from companion_v2.execution.models import TaskContext, TaskError, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import AudienceRef, ScopeKind, ScopeRef
from companion_v2.identity_policy.management import (
    PAGE_MANAGED_CAPABILITIES,
    PERSON_ROUTE_KEYS,
    PersonAuthorizationManagement,
)
from companion_v2.identity_policy.repository import PersistentIdentityDirectory
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.identity_policy.personas import ConversationRoute
from companion_v2.memory.person.admin_names import PersonAcquaintanceNameManagement
from companion_v2.memory.person.management import PersonAcquaintanceDirectory
from companion_v2.memory.person.models import empty_document
from companion_v2.knowledge.catalog import KnowledgeCatalog
from companion_v2.relationships.group_network import (
    PREFIX as GROUP_NETWORK_PREFIX,
    VERSION as GROUP_NETWORK_VERSION,
    GroupRelationshipStore,
    attach_member_clues,
)
from companion_v2.relationships.private_management import PrivateMemberSettings
from companion_v2.agenda.models import AgendaTarget, MemoCursor
from companion_v2.agenda.service import AgendaService
from companion_v2.foundation.refs import OperationRef, VersionRef

from .management_identity import dashboard_operator
from .display_names import display_names
from .settings import HostControlError


PREFIX = "/astrbot_plugin_private_companion/acquaintance"
AUTHORIZATION_PATH = PREFIX + "/authorization"
ADMIN_NAME_PATH = PREFIX + "/admin-name"
IDENTITY_PATH = PREFIX + "/identity"
SETTINGS_PATH = PREFIX + "/settings"
OPEN_LOOPS_PATH = PREFIX + "/open-loops"
STRANGERS_PATH = PREFIX + "/strangers"
MAX_AUTHORIZATION_BODY = 8192
MAX_STRANGER_ROWS = 100
logger = logging.getLogger(__name__)


def _group_route_key(group):
    return (group.get("platform_id") or group.get("platform") or "", group.get("bot_id") or "",
            group.get("account_id") or "", str(group.get("group_id") or ""))


def _persona_metadata(settings, bot_id, role_id):
    """Label a stored role for display without changing its ownership or grants."""
    if hasattr(settings, "all_targets"):
        ids = [item.persona_id for item in settings.personas
               if item.bot_id == bot_id and item.role_id == role_id]
    else:
        ids = [settings.persona_id] if (settings.bot_id, settings.role_id) == (bot_id, role_id) else []
    if not ids:
        return {}
    ids = sorted({"" if item is None or item in ("", "[%None]") else item for item in ids})
    from .identity_derivation import role_display_name
    return {"persona_ids": ids, "role_name": role_display_name(role_id, ids)}


def _include_native_groups(groups, sessions):
    """Expose native group sessions as discoveries, without importing their authority."""
    known = {_group_route_key(group): group for group in groups}
    for session in sessions:
        if not isinstance(session, dict) or session.get("native") is not True or session.get("scope_kind") != "group":
            continue
        group_id = str(session.get("group_id") or "")
        platform_id = session.get("platform_id") or ""
        if not group_id or not platform_id:
            continue
        group = {"group_id": group_id, "platform_id": platform_id,
                 "bot_id": session.get("bot_id") or "", "account_id": session.get("account_id") or "",
                 "configured": session.get("managed") is True, "discovered": True,
                 "roles": list(session.get("roles") or []),
                 "member_count": 0, "msg_count": 0}
        key = _group_route_key(group)
        existing = known.get(key)
        if existing is not None:
            existing["discovered"] = True
            existing["configured"] = bool(existing.get("configured") or group["configured"])
            existing["roles"] = list({role["role_id"]: role
                for role in [*existing.get("roles", []), *group["roles"]]
                if isinstance(role, dict) and role.get("role_id")}.values())
            continue
        known[key] = group
        groups.append(group)


def _group_relationship_networks(runtime, settings, persons, groups):
    """Join group graph records with acquaintance cards and weak member clues.

    This is one management projection.  The three inputs keep their provenance
    in separate fields so a display name or imported account can never become an
    identity assertion or a permission decision.
    """
    if runtime is None:
        return ()
    if hasattr(settings, "all_targets"):
        # A wildcard group target is the automatic admission rule. It is not a
        # literal group and must never appear as a fake member of the directory.
        targets = [target for target in settings.all_targets
                   if getattr(target, "group_id", None)
                   and getattr(target, "group_id", None) != "*"]
        role_ids = lambda target: tuple(sorted({p.role_id for p in settings.personas if p.bot_id == target.bot_id}))
        mapped_roles = lambda bot_id: tuple(sorted({p.role_id for p in settings.personas if p.bot_id == bot_id}))
    else:
        targets = []
        role_ids = lambda target: (settings.role_id,)
        mapped_roles = lambda bot_id: (settings.role_id,) if bot_id == settings.bot_id else ()
    def group_is_configured(platform_id, account_id, group_id, bot_id, role_id):
        resolver = getattr(settings, "group_target", None)
        if callable(resolver):
            target = resolver(platform_id, account_id, group_id)
            return target is not None and target.bot_id == bot_id and role_id in mapped_roles(bot_id)
        return False

    routes = []
    for target in targets:
        for role_id in role_ids(target):
            routes.append((target.platform_id, target.bot_id, target.account_id, target.group_id, role_id,
                           group_is_configured(target.platform_id, target.account_id,
                                               target.group_id, target.bot_id, role_id)))
    # Native discoveries are supplied by the session topology.  They do not
    # carry a role of their own, so project one route per role mapped to the
    # account.  A wildcard group target makes the route managed automatically.
    for group in groups:
        group_id = str(group.get("group_id") or "")
        if not group_id or group_id == "*":
            continue
        if group.get("discovered") and not group.get("configured"):
            continue
        assigned = mapped_roles(group.get("bot_id", ""))
        native_roles = {role.get("role_id") for role in group.get("roles", []) if isinstance(role, dict)}
        roles = (tuple(sorted(native_roles.intersection(assigned))) if native_roles else
                 tuple(sorted({group.get("role_id")} - {None, ""})) or assigned)
        for role_id in roles:
            key = (group.get("platform_id") or group.get("platform") or "", group.get("bot_id", ""),
                   group.get("account_id", ""), group_id, role_id)
            if all(key) and key not in {(a, b, c, d, e) for a, b, c, d, e, _ in routes}:
                routes.append((*key, group_is_configured(key[0], key[2], key[3], key[1], key[4])))
    # Historical groups are still useful in the directory after an operator
    # removes a configured target, but their graph is never confused with a
    # currently managed target.
    known = {(a, b, c, d, e) for a, b, c, d, e, _ in routes}
    for person in persons:
        for group_id in person.get("groups", {}):
            key = (person.get("platform"), person.get("bot_id"), person.get("account_id"), group_id,
                   person.get("role_id"))
            if key[0] and key[1] and key[2] and key[4] and key not in known:
                known.add(key)
                routes.append((*key, group_is_configured(key[0], key[2], key[3], key[1], key[4])))
    prefix_length = len(GROUP_NETWORK_PREFIX)
    for row in runtime.read_all("SELECT value FROM runtime_metadata WHERE substr(key, 1, ?) = ?",
                                 (prefix_length, GROUP_NETWORK_PREFIX)):
        try:
            document = json.loads(row["value"])
        except (TypeError, ValueError):
            continue
        group = document.get("group") if isinstance(document, dict) else None
        if (not isinstance(document, dict) or document.get("version") != GROUP_NETWORK_VERSION
                or not isinstance(group, dict)
                or group.get("installation_id") != runtime.installation_id):
            continue
        key = (group.get("platform_id"), group.get("bot_id"), group.get("account_id"),
               group.get("group_id"), group.get("role_id"))
        if key[3] == "*":
            continue
        if all(key) and key not in known:
            known.add(key)
            routes.append((*key, group_is_configured(key[0], key[2], key[3], key[1], key[4])))
    store = GroupRelationshipStore(runtime)
    catalog = KnowledgeCatalog(runtime)
    result = []
    for platform_id, bot_id, account_id, group_id, role_id, configured in routes:
        matching = [person for person in persons
                    if person.get("platform") == platform_id and person.get("bot_id") == bot_id
                    and person.get("account_id") == account_id and person.get("role_id") == role_id
                    and group_id in person.get("groups", {})]
        network = store.projection_for_route(runtime.installation_id, platform_id, bot_id, account_id,
                                             group_id, role_id, people=matching)
        scope = ScopeRef(runtime.installation_id, platform_id, ScopeKind.GROUP, group_id,
                         bot_id, account_id, group_id, group_id)
        try:
            clues = catalog.member_entries(installation_id=runtime.installation_id, source_limit=64,
                                           role_id=role_id, scope_ids=(group_id,), scope_kind=ScopeKind.GROUP,
                                           scope=scope)
        except Exception:
            clues = ()
        attach_member_clues(network, clues)
        network.update({"platform_id": platform_id, "bot_id": bot_id, "account_id": account_id,
                        "role_id": role_id,
                        "role_name": next((person.get("role_name") for person in matching
                                           if person.get("role_name")), None),
                        "configured": configured,
                        **_persona_metadata(settings, bot_id, role_id)})
        result.append(network)
    result.sort(key=lambda item: (not item["configured"], str(item.get("group_id") or ""), item["role_id"]))
    return tuple(result)


def _configured_windows(settings):
    """The private targets this profile manages, keyed by their routing tuple.

    A group target carries a ``group_id`` and is deliberately absent: this page
    manages what one person may do, and a group is not a person.
    """
    if not hasattr(settings, "all_targets"):
        return [{"key": (settings.platform_id, settings.bot_id, settings.account_id, settings.participant_id)}]
    return [{"key": (target.platform_id, target.bot_id, target.account_id, target.participant_id)}
            for target in settings.all_targets if not getattr(target, "group_id", None)]


def _authorization_participants(runtime, settings, *, observed_at=None):
    """One entry per configured private person: their subject and their window.

    Both come from the registered conversation route rather than from settings.
    The settings name a participant as a bare platform account ("200"); the whole
    installation addresses them as a subject ("onebot-user:200"), and a grant's
    scope carries the conversation and session the host opened. Deriving either
    one here would be a second, silently divergent naming of the same person --
    the grant written against it would not be the one the reply path checks.
    """
    wanted = {entry["key"] for entry in _configured_windows(settings)}
    participants = []
    identity = PersistentIdentityDirectory(runtime)
    at = observed_at or datetime.now(timezone.utc)
    for row in runtime.read_all("SELECT record_json FROM conversation_routes", ()):
        route = ConversationRoute.restore(json.loads(row["record_json"]))
        if route.scene_kind is not ScopeKind.PRIVATE or route.user is None or not route.scope.is_resolved:
            continue
        scope = route.scope
        key = scope.platform, scope.bot_id, scope.account_id, scope.native_scope_id
        if not scope.native_scope_id or scope.native_scope_id == "*" or not (
                key in wanted or (*key[:3], "*") in wanted):
            continue
        binding = identity.resolve_role(scope, observed_at=at)
        if not binding.is_success:
            continue
        role_id = binding.unwrap().role.subject_id
        mapped = ({item.role_id for item in settings.personas if item.bot_id == scope.bot_id}
                  if hasattr(settings, "all_targets") else {settings.role_id})
        if role_id not in mapped:
            continue
        participants.append({"target": None, "subject_id": route.user.subject_id,
                             "subject": route.user, "scope": scope,
                             "role_id": role_id})
    return participants


def _directory_routes(settings):
    routes, groups = [], []
    if hasattr(settings, "all_targets"):
        for target in settings.all_targets:
            roles = [persona.role_id for persona in settings.personas if persona.bot_id == target.bot_id]
            routes.extend({"platform": target.platform_id, "bot_id": target.bot_id,
                           "account_id": target.account_id, "role_id": role_id} for role_id in roles)
            if getattr(target, "group_id", None) and target.group_id != "*":
                groups.append({"target_id": target.target_id, "group_id": target.group_id, "bot_id": target.bot_id,
                               "platform_id": target.platform_id, "account_id": target.account_id,
                               "configured": True, "member_count": 0, "msg_count": 0})
    else:
        routes.append({"platform": settings.platform_id, "bot_id": settings.bot_id,
                       "account_id": settings.account_id, "role_id": settings.role_id})
    return routes, groups


def _group_target_access(controller, settings):
    """Describe the exact preconditions enforced by AccountSessionWebAPI."""
    session = getattr(controller, "session", None)
    application = getattr(session, "application", None)
    session_state = str(getattr(application, "state", "unloaded"))
    running = bool(getattr(controller, "_accepting", False)) or bool(
        session is not None and session_state not in ("new", "stopped"))
    multi = bool(getattr(controller, "_multi_host", False))
    targets = tuple(getattr(settings, "all_targets", ())) if multi else ()
    accounts = sorted({(item.platform_id, item.account_id, item.bot_id) for item in targets})
    persona_bots = sorted({item.bot_id for item in getattr(settings, "personas", ())}) if multi else []
    return {
        "editable": multi and not running and not bool(getattr(controller, "_terminated", False)),
        "running": running,
        "session_state": session_state,
        "accounts": [{"platform_id": platform, "account_id": account, "bot_id": bot}
                     for platform, account, bot in accounts],
        "persona_bots": persona_bots,
    }


class AcquaintanceWebAPI:
    def __init__(self, controller):
        self.controller = controller

    def routes(self):
        return [(PREFIX, self.directory, ["GET"], "Read the Companion V2 acquaintance directory"),
                (AUTHORIZATION_PATH, self.authorization, ["GET"],
                 "Read one person's capability authorization"),
                (AUTHORIZATION_PATH, self.authorize, ["POST"],
                 "Switch one page-managed capability for one person"),
                (ADMIN_NAME_PATH, self.set_admin_name, ["POST"],
                 "Set a directory person's administrator-assigned display name"),
                (IDENTITY_PATH, self.set_identity, ["POST"], "Set one trusted person's management tier"),
                (SETTINGS_PATH, self.member_settings, ["GET"], "Read one private member's settings"),
                (SETTINGS_PATH, self.configure_member, ["POST"], "Save one private member's settings"),
                (OPEN_LOOPS_PATH, self.open_loops, ["GET"], "Read one person's open conversation threads"),
                (OPEN_LOOPS_PATH, self.change_open_loop, ["POST"], "Manage one person's open conversation thread"),
                (STRANGERS_PATH, self.strangers, ["GET"],
                 "Read private accounts refused by the stranger boundary")]

    def _settings(self):
        settings = self.controller.settings
        return settings if hasattr(settings, "all_targets") else self.controller.resolved_settings()

    def _participants(self):
        controller = self.controller
        participants = _authorization_participants(controller.runtime, self._settings(), observed_at=controller.now())
        # Old conversation routes stay in the history. Select the host's current
        # registered window when it has one, so opening a fresh conversation
        # does not make one person's stable settings route look ambiguous.
        current_scopes = set()
        if getattr(controller, "_multi_host", False):
            prefix = "host.multi.context:"
            for row in controller.runtime.read_all(
                    "SELECT value FROM runtime_metadata WHERE substr(key, 1, ?) = ?", (len(prefix), prefix)):
                try:
                    current_scopes.add(ScopeRef.restore(json.loads(row["value"])))
                except (TypeError, ValueError, KeyError):
                    continue
        else:
            session = getattr(controller, "session", None) or getattr(controller, "last_session", None)
            scope = getattr(getattr(getattr(session, "profile", None), "target", None), "scope", None)
            if scope is not None:
                current_scopes.add(scope)
        family = lambda scope: (scope.platform, scope.bot_id, scope.account_id, scope.native_scope_id)
        current_families = {family(scope) for scope in current_scopes}
        return [entry for entry in participants if family(entry["scope"]) not in current_families
                or entry["scope"] in current_scopes]

    def _members(self, result, settings):
        """Configured people stay selectable before an acquaintance card exists."""
        runtime = self.controller.runtime
        identity = PersistentIdentityDirectory(runtime)
        bindings = identity.snapshot().bindings
        registered = self._participants()
        persons = {tuple(person[key] for key in ("subject_id", *PERSON_ROUTE_KEYS)): person
                   for person in result["persons"]}
        if hasattr(settings, "all_targets"):
            targets = [target for target in settings.all_targets if not getattr(target, "group_id", None)]
        else:
            targets = [settings]
        entries = []
        for entry in registered:
            scope = entry["scope"]
            entries.append((entry["subject"], scope.platform, scope.bot_id, scope.account_id,
                            scope.native_scope_id, entry["role_id"]))
        for target in targets:
            if target.participant_id == "*":
                continue
            matched = [item for item in bindings if
                (item.principal.platform, item.principal.bot_id, item.principal.account_id,
                 item.principal.native_account_id) ==
                (target.platform_id, target.bot_id, target.account_id, target.participant_id)]
            if not matched:
                continue
            subject = matched[0].subject
            roles = [(p.role_id, p.persona_id) for p in settings.personas if p.bot_id == target.bot_id] \
                if hasattr(settings, "all_targets") else [(settings.role_id, settings.persona_id)]
            entries.extend((subject, target.platform_id, target.bot_id, target.account_id,
                            target.participant_id, role_id) for role_id in dict(roles))
        for subject, platform_id, bot_id, account_id, participant_id, role_id in entries:
            key = (subject.subject_id, platform_id, bot_id, account_id, role_id)
            if key not in persons:
                route = {"subject_id": subject.subject_id, "platform": platform_id,
                         "bot_id": bot_id, "account_id": account_id, "role_id": role_id}
                document = {**empty_document(), "native_account_id": participant_id}
                persons[key] = PersonAcquaintanceDirectory(runtime)._person(route, document, None, 0)
            persons[key].update(private_target=True,
                settings_available=any(entry["subject"] == subject
                    and (entry["scope"].platform, entry["scope"].bot_id, entry["scope"].account_id,
                         entry["role_id"]) == key[1:] for entry in registered))
        for person in persons.values():
            person.update(_persona_metadata(settings, person["bot_id"], person["role_id"]))
            row = runtime.read_one("SELECT tier FROM subject_access_tiers WHERE subject_kind = 'human' AND subject_id = ?",
                                   (person["subject_id"],))
            person["identity_tier"] = row["tier"] if row else "standard"
        result["persons"] = list(persons.values())
        result["counts"] = PersonAcquaintanceDirectory._counts(result["persons"])
        return result

    async def _operator(self):
        """The signed-in-operator, loaded-profile gate both routes must pass.

        Identity comes first on purpose: who is asking is knowable before what
        they asked for, and an unauthenticated caller must not get a different
        answer by varying the request shape.
        """
        controller = self.controller
        username = request.username
        if type(username) is not str or not username.strip() or username.startswith("api_key:"):
            raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
        if controller._terminated:
            raise TaskError("staging_host_terminated", "the plugin has stopped")
        if controller.runtime is None or controller.settings is None:
            raise TaskError("staging_not_initialized", "load the V2 profile before reading the directory")
        operator = dashboard_operator(controller.runtime, username, at=controller.now())
        require(ObjectAccessPolicy(controller.runtime).feature_access(operator,
            scope=ScopeRef(controller.runtime.installation_id, "companion", ScopeKind.SYSTEM,
                           "page.management"),
            action="memory.manage", observed_at=controller.now()))
        return username, operator

    def _management(self):
        controller = self.controller
        return PersonAuthorizationManagement(controller.runtime,
            participants=self._participants())

    def _context(self, person, route):
        subject, scope = self._management()._person(person, route)
        identity = PersistentIdentityDirectory(self.controller.runtime)
        for binding in identity.snapshot().bindings:
            principal = binding.principal
            if (binding.subject != subject or (principal.platform, principal.bot_id, principal.account_id,
                    principal.native_account_id) != (scope.platform, scope.bot_id, scope.account_id, scope.native_scope_id)):
                continue
            actor = identity.resolve_authenticated(principal, observed_at=self.controller.now())
            if actor.is_success and actor.unwrap().subject == subject:
                role = require(identity.resolve_role(scope, observed_at=self.controller.now()))
                if role.role.subject_id != route["role_id"]:
                    break
                return TaskContext(actor.unwrap(), role, AudienceRef(scope, (subject,)))
        raise TaskError("acquaintance_route_unknown", "the selected private route is no longer current")

    @staticmethod
    def _route_query(query):
        # AstrBot's PluginMultiDict exposes ``keys()`` but intentionally does
        # not implement positional iteration; iterating it directly can ask
        # for key ``0`` and turn a valid request into HTTP 500.
        supplied = set(query.keys()).intersection(PERSON_ROUTE_KEYS)
        if supplied and supplied != set(PERSON_ROUTE_KEYS):
            raise TaskError("acquaintance_route_required", "provide one complete private member route")
        return {key: query[key] for key in PERSON_ROUTE_KEYS} if supplied else None

    @staticmethod
    def _query(allowed, required=()):
        query = request.query
        if (set(query.keys()) - set(allowed) or set(required) - set(query.keys())
                or any(len(query.getlist(key)) != 1 for key in query.keys())):
            raise TaskError("acquaintance_request_invalid", "the directory does not accept these query fields")
        return {key: query.get(key) for key in allowed if key in query}

    async def directory(self):
        from .web_api import outcome_response
        controller = self.controller
        try:
            query = self._query(("read_mode",))
            stored_only = query.get("read_mode") == "stored"
            if query and not stored_only:
                raise TaskError("acquaintance_request_invalid", "the directory does not accept query overrides")
            async with controller._control_lock:
                username, _ = await self._operator()
                runtime, settings_snapshot = controller.runtime, controller.settings
                settings = self._settings()
                group_access = _group_target_access(controller, settings)
                routes, groups = _directory_routes(settings)
                result = self._members(PersonAcquaintanceDirectory(controller.runtime).directory(routes), settings)
            topology = getattr(controller, "session_topology", None)
            if topology is not None and not stored_only:
                try:
                    _include_native_groups(groups, await topology.inventory(scope_kind="group"))
                except Exception as error:
                    logger.exception("AstrBot native group directory read failed")
                    detail = " ".join(str(error).split())
                    reason = "AstrBot 原生群聊目录读取失败"
                    reason += (f"（{type(error).__name__}: {detail[:512]}）" if detail
                               else f"（{type(error).__name__}）")
                    raise TaskError("acquaintance_native_directory_unavailable", reason) from error
            async with controller._control_lock:
                await self._operator()
                if controller.runtime is not runtime or controller.settings is not settings_snapshot:
                    raise TaskError("staging_not_initialized", "the profile changed while reading native sessions")
                members = {}
                known_groups = {_group_route_key(group) for group in groups}
                for person in result["persons"]:
                    for group_id, entry in person["groups"].items():
                        key = _group_route_key({"platform_id": person["platform"], "bot_id": person["bot_id"],
                                                "account_id": person["account_id"], "group_id": group_id})
                        members.setdefault(key, []).append(person)
                        if key not in known_groups:
                            known_groups.add(key)
                            groups.append({"group_id": group_id, "bot_id": person["bot_id"],
                                "platform_id": person["platform"], "account_id": person["account_id"],
                                "configured": False, "member_count": 0, "msg_count": 0})
                for group in groups:
                    for person in members.get(_group_route_key(group), ()):
                        group["member_count"] += 1
                        group["msg_count"] += person["groups"][group["group_id"]]["msg_count"]
                group_networks = _group_relationship_networks(runtime, settings, result["persons"], groups)
                known_groups = {_group_route_key(group) for group in groups}
                for network in group_networks:
                    key = _group_route_key(network)
                    if key not in known_groups:
                        known_groups.add(key)
                        groups.append({"group_id": network["group_id"], "bot_id": network["bot_id"],
                                       "platform_id": network["platform_id"], "account_id": network["account_id"],
                                       "configured": network["configured"],
                                       "member_count": network["member_count"],
                                       "msg_count": sum(member["group_observation"]["message_count"]
                                                         for member in network["members"])})
            names = display_names(controller)
            name_options = {"cached_only": True} if stored_only else {}
            persons, groups = await asyncio.gather(names.persons(result["persons"], **name_options),
                                                  names.targets(groups, **name_options))
            async with controller._control_lock:
                await self._operator()
                if controller.runtime is not runtime or controller.settings is not settings_snapshot:
                    raise TaskError("staging_not_initialized", "the profile changed while reading display names")
                return outcome_response(Outcome.success({
                    "installation_id": controller.runtime.installation_id,
                    "profile_id": getattr(controller, "target_id", None) or controller.settings.profile_id,
                    "operator_name": username, "persons": persons,
                    "groups": groups, "counts": result["counts"],
                    "group_access": group_access,
                    "group_networks": list(group_networks),
                    "directory_refresh_pending": stored_only,
                    # The page must not keep its own copy of which capabilities this
                    # page owns: a second list would drift and start offering a
                    # control for a capability that cannot be changed here.
                    "page_managed_capabilities": list(PAGE_MANAGED_CAPABILITIES)}))
        except (TaskError, HostControlError) as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except Exception as error:
            logger.exception("V2 acquaintance directory read failed")
            detail = " ".join(str(error).split())
            reason = f"{type(error).__name__}: {detail[:512]}" if detail else type(error).__name__
            return outcome_response(Outcome.failed("acquaintance_directory_read_failed", reason))

    async def strangers(self):
        """Private accounts the stranger boundary has seen, newest first.

        Read-only and bounded: the ledger holds the account and time, never a
        subject binding, conversation or capability grant.
        """
        from .stranger_notice import CONTACT_KIND
        from .web_api import outcome_response
        controller = self.controller
        try:
            async with controller._control_lock:
                await self._operator()
                rows = controller.runtime.read_all(
                    "SELECT document_json FROM memory_views WHERE kind = ? "
                    "ORDER BY updated_at DESC LIMIT ?", (CONTACT_KIND, MAX_STRANGER_ROWS))
                items = []
                for row in rows:
                    try:
                        document = json.loads(row["document_json"])
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(document, dict):
                        continue
                    items.append({key: document.get(key) for key in (
                        "platform_id", "login", "peer", "first_seen_at", "last_seen_at",
                        "attempts", "last_outcome")})
                return outcome_response(Outcome.success({"items": items}))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))

    async def authorization(self):
        """Read the real state of every capability one person holds."""
        from .web_api import outcome_response
        controller = self.controller
        try:
            async with controller._control_lock:
                _, operator = await self._operator()
                query = self._query(("subject_id", *PERSON_ROUTE_KEYS), required=("subject_id",))
                manager = self._management()
                return outcome_response(manager.status(operator, query["subject_id"],
                    route=self._route_query(query), observed_at=controller.now()))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))

    async def authorize(self):
        """Switch one page-managed capability, as one ledgered operation."""
        from .web_api import outcome_response
        controller = self.controller
        try:
            raw = await request.body()
            if len(raw) > MAX_AUTHORIZATION_BODY or request.query:
                raise TaskError("acquaintance_request_invalid", "the authorization command requires one bounded JSON body")

            def unique(pairs):
                values = {}
                for key, value in pairs:
                    if key in values:
                        raise TaskError("acquaintance_request_invalid", "the authorization command repeats a field")
                    values[key] = value
                return values

            command = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
            if type(command) is not dict:
                raise TaskError("authorization_command_invalid", "an explicit authorization command is required")
            async with controller._control_lock:
                _, operator = await self._operator()
                manager = self._management()
                result = manager.configure(operator, command, observed_at=controller.now())
                if result.is_success and not result.unwrap()["replayed"]:
                    # Every later request re-reads the current rows, so no in-memory
                    # cache has to be invalidated here; the session only needs to know
                    # that authority moved so an already-assembled turn rechecks it.
                    session = (None if controller._terminated
                               else controller.session or controller.last_session)
                    if session is not None:
                        session.application.notify()
                return outcome_response(result)
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("authorization_command_invalid",
                                                   "the authorization command is not valid JSON"))

    async def set_admin_name(self):
        """Set or clear a person's preferred name within one authorized role route."""
        from .web_api import outcome_response
        controller = self.controller
        try:
            raw = await request.body()
            if len(raw) > MAX_AUTHORIZATION_BODY or request.query:
                raise TaskError("acquaintance_request_invalid", "the name command requires one bounded JSON body")

            def unique(pairs):
                values = {}
                for key, value in pairs:
                    if key in values:
                        raise TaskError("acquaintance_request_invalid", "the name command repeats a field")
                    values[key] = value
                return values

            command = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
            required = {"operation_key", "subject_id", "platform", "bot_id", "account_id", "role_id",
                        "native_account_id", "admin_nickname", "expected_revision"}
            if (type(command) is not dict or set(command) != required
                    or type(command.get("expected_revision")) is not int
                    or type(command.get("admin_nickname")) is not str):
                raise TaskError("person_nickname_command_invalid", "the name command does not match its contract")
            async with controller._control_lock:
                _, operator = await self._operator()
                routes, _ = _directory_routes(self._settings())
                manager = PersonAcquaintanceNameManagement(controller.runtime)
                return outcome_response(manager.set_nickname(operator, routes=routes, observed_at=controller.now(),
                    subject_id=command["subject_id"], platform=command["platform"], bot_id=command["bot_id"],
                    account_id=command["account_id"], role_id=command["role_id"],
                    native_account_id=command["native_account_id"], nickname=command["admin_nickname"],
                    expected_revision=command["expected_revision"], operation_key=command["operation_key"]))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("person_nickname_command_invalid",
                                                   "the name command is not valid JSON"))

    async def _command(self, *, expected):
        raw = await request.body()
        if len(raw) > 32768 or request.query:
            raise TaskError("acquaintance_request_invalid", "provide one bounded JSON body")
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate field")
                result[key] = value
            return result
        command = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
        if type(command) is not dict or set(command) != expected:
            raise TaskError("private_member_command_invalid", "the member command does not match its contract")
        return command

    async def set_identity(self):
        from .web_api import outcome_response
        try:
            async with self.controller._control_lock:
                _, operator = await self._operator()
                command = await self._command(expected={"operation_key", "subject_id", "route", "tier", "expected_revision"})
                result = self._management().set_identity(operator, command, observed_at=self.controller.now())
                session = self.controller.session or self.controller.last_session
                if result.is_success and session:
                    session.application.notify()
                return outcome_response(result)
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("identity_command_invalid", "invalid identity command"))

    async def _member_settings(self, *, write=False):
        from .web_api import outcome_response
        started_at = perf_counter()
        lock_acquired_at = None
        try:
            async with self.controller._control_lock:
                lock_acquired_at = perf_counter()
                _, operator = await self._operator()
                if write:
                    command = await self._command(expected={"operation_key", "subject_id", "route", "sections"})
                    subject_id, route = command["subject_id"], command["route"]
                else:
                    query = self._query(("subject_id", *PERSON_ROUTE_KEYS), required=("subject_id", *PERSON_ROUTE_KEYS))
                    subject_id, route = query["subject_id"], self._route_query(query)
                context = self._context(subject_id, route)
                session = self.controller.session or self.controller.last_session
                if session is None:
                    raise TaskError("staging_not_initialized", "load the private session first")
                editor = PrivateMemberSettings(session.application, context)
                result = (editor.configure(operator, {key: command[key] for key in ("operation_key", "sections")})
                          if write else editor.status(operator))
                if not write and result.is_success and isinstance(result.value, dict):
                    from .voice import tts_provider_catalog
                    result.value["tts"] = tts_provider_catalog(self.controller.context)
                return outcome_response(result)
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("private_member_command_invalid", "invalid private member settings"))
        finally:
            elapsed_ms = round((perf_counter() - started_at) * 1000)
            if elapsed_ms >= 1000:
                lock_wait_ms = round(((lock_acquired_at or perf_counter()) - started_at) * 1000)
                logger.warning("[CompanionV2] 私聊成员配置请求耗时 operation=%s elapsed_ms=%d lock_wait_ms=%d",
                               "write" if write else "read", elapsed_ms, lock_wait_ms)

    async def member_settings(self):
        return await self._member_settings()

    async def configure_member(self):
        return await self._member_settings(write=True)

    async def open_loops(self):
        from .web_api import outcome_response
        try:
            query = self._query(("subject_id", *PERSON_ROUTE_KEYS, "state", "after"),
                                required=("subject_id", *PERSON_ROUTE_KEYS))
            state = query.get("state", "all")
            if state not in ("all", "active", "completed"):
                raise TaskError("acquaintance_request_invalid", "unsupported open-loop state")
            cursor = MemoCursor(**json.loads(query["after"])) if query.get("after") else None
            async with self.controller._control_lock:
                _, operator = await self._operator()
                context = self._context(query["subject_id"], {key: query[key] for key in PERSON_ROUTE_KEYS})
                target = AgendaTarget(context.scope, context.role_binding.role, context.actor.subject)
                result = AgendaService(self.controller.runtime).list_memos(
                    operator, target=target, observed_at=self.controller.now(), limit=100,
                    after=cursor, state=state, purpose="open_loop", include_expired=True)
                if result.failure is not None:
                    return outcome_response(result)
                memos = result.unwrap()
                active, expired, completed = [], [], []
                for memo in memos:
                    item = {"memo_id": memo.memo_id, "revision": memo.revision, "state": memo.state,
                            "title": memo.title, "body": memo.body, "created_at": memo.created_at,
                            "expires_at": memo.expires_at, "pinned": memo.pinned,
                            "expired": memo.expired(self.controller.now()),
                            "last_followup_at": memo.last_followup_at,
                            "context_count": memo.context_count,
                            "followup_count": memo.followup_count}
                    (completed if memo.state == "completed" else expired if item["expired"] else active).append(item)
                page = {"active": active, "expired": expired, "completed": completed,
                        "next_cursor": None if len(memos) < 100 else {"pinned": memos[-1].pinned,
                                                                       "memo_id": memos[-1].memo_id}}
                return outcome_response(Outcome.success(page))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("open_loop_request_invalid", "invalid open-loop request"))

    async def change_open_loop(self):
        from .web_api import outcome_response
        try:
            command = await self._command(expected={"subject_id", "route", "memo_id", "expected_revision",
                                                     "action", "operation_key"})
            if command["action"] not in ("complete", "reopen", "delete"):
                raise TaskError("open_loop_action_invalid", "unsupported open-loop operation")
            route = command["route"]
            if type(route) is not dict or set(route) != set(PERSON_ROUTE_KEYS):
                raise TaskError("acquaintance_route_invalid", "select one complete Bot, account and role route")
            if (type(command["expected_revision"]) is not int or type(command["memo_id"]) is not str
                    or type(command["operation_key"]) is not str or not command["operation_key"]):
                raise TaskError("open_loop_request_invalid", "open-loop operation fields are invalid")
            async with self.controller._control_lock:
                _, operator = await self._operator()
                context = self._context(command["subject_id"], route)
                target = AgendaTarget(context.scope, context.role_binding.role, context.actor.subject)
                service = AgendaService(self.controller.runtime)
                current = service.read_memo(operator, target=target, memo_id=command["memo_id"],
                                            observed_at=self.controller.now(), include_expired=True)
                if current.failure is not None:
                    return outcome_response(current)
                if current.unwrap().purpose != "open_loop":
                    raise TaskError("open_loop_not_found", "selected item is not an open conversation thread")
                operation = OperationRef(command["operation_key"], "agenda", context.scope,
                                         VersionRef(command["operation_key"], 1), command["operation_key"])
                result = service.change_memo(operator, target=target, memo_id=command["memo_id"],
                    action=command["action"], expected_revision=command["expected_revision"],
                    expected_role_revision=context.role_binding.revision, operation=operation,
                    observed_at=self.controller.now())
                return outcome_response(result)
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("open_loop_request_invalid", "invalid open-loop operation"))
