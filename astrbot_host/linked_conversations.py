"""Explicit, revisioned links for native model history (no shared V2 state)."""
import asyncio
from contextlib import AsyncExitStack
from dataclasses import asdict, replace
import json
import logging

from astrbot.core import sp
from astrbot.core.star.star_handler import EventType, star_handlers_registry
from astrbot.core.utils.active_event_registry import active_event_registry
from astrbot.core.utils.session_lock import session_lock_manager

from companion_v2.adapters.astrbot.native import NativeServedConversation
from companion_v2.conversation.linked_ownership import link_records_changed, preferred_target_id
from companion_v2.execution.models import TaskError, digest
from companion_v2.foundation.refs import ScopeKind, ScopeRef, SubjectRef, SubjectKind
from .linked_turn_gate import LinkedTurnGate

logger = logging.getLogger(__name__)
KEY = "astrbot.private-conversation-links.v1"
EXTRA = "companion_linked_turn_v1"
# AstrBot 4.29.4's own commands that replace the conversation, switch the
# provider or write session variables (handler names in its builtin_commands).
SESSION_COMMANDS = frozenset({"reset", "new_conv", "provider", "set_variable", "unset_variable"})
# The two that end the current context: like AstrBot on its own origin, they
# first stop what is in flight, here on every member.
CONTEXT_COMMANDS = frozenset({"reset", "new_conv"})


def origin(target):
    return f"{target.platform_id}:FriendMessage:{target.participant_id}"


def linked_origins(records):
    return {row["origin"] for link in records.values() for row in link["members"] + link.get("detached", [])}


class LinkedConversations:
    def __init__(self, session):
        self.session = session
        self.gate = LinkedTurnGate.shared()
        if not hasattr(self.gate, "editing_origins"):
            self.gate.editing_origins = set()
        if not hasattr(self.gate, "linked_cids"):
            self.gate.linked_cids = set()
        records = self.records()
        for link in records.values():
            self.gate.linked_cids.add(link["conversation_id"])
        # Every other origin is answered from memory, without reading the record.
        self.gate.linked_origins = linked_origins(records)
        self.retiring = False
        self.management_tasks = set()

    def records(self):
        records = self.session.authority.read(KEY)
        if records is None:
            return {}
        if type(records) is not dict or any(type(link) is not dict or link.get("schema_version") != 1
                                           for link in records.values()):
            raise TaskError("conversation_link_invalid", "the stored conversation link version is unavailable")
        return records

    def get(self, link_id):
        return self.records().get(link_id)

    def view(self):
        """The stored links, each saying whether its reminders and proactive text have a destination now."""
        return {link_id: {**link, "background_destination": self.destination_state(link)}
                for link_id, link in self.records().items()}

    def destination_state(self, link):
        """``selected``, or why text that comes due now would be left unsent.

        ``missing``: the named target is no member, or is no longer configured.
        ``unavailable``: a configured member whose account is not connected at the moment.
        """
        member = next((row for row in link["members"] if row["target_id"] == preferred_target_id(link)), None)
        if member is None or self.session.settings.private_target(
                member["platform_id"], member["account_id"], member["participant_id"]) is None:
            return "missing"
        bridge = self.session.bridge
        account = bridge.accounts.get((member["platform_id"], member["account_id"]))
        return "selected" if account is not None and bridge.account_connected(account) else "unavailable"

    def for_origin(self, umo):
        if umo not in self.gate.linked_origins:
            return None
        return next((link for link in self.records().values()
                     if any(row["origin"] == umo for row in link["members"] + link.get("detached", []))), None)

    def removed(self, target):
        """Taken out of a link and not admitted on its own since."""
        link = self.for_origin(origin(target))
        return link is not None and all(row["origin"] != origin(target) for row in link["members"])

    def for_event(self, event):
        try:
            return self.for_origin(event.unified_msg_origin)
        except BaseException:
            # Hook dispatch swallows even cancellation: a failed record read must
            # stop propagation before the core can load or overwrite history.
            event.stop_event()
            raise

    def served(self, target):
        link = self.for_origin(origin(target))
        if link is None:
            return target
        anchor = link["anchor"]
        return NativeServedConversation(anchor["target_id"], anchor["origin"],
            ScopeRef.restore(anchor["scope"]), SubjectRef(anchor["role"]["installation_id"], SubjectKind(anchor["role"]["kind"]),
                       anchor["role"]["subject_id"]))

    def destination(self, link, endpoint):
        """A member's own current context, for the conversation's reminders and proactive text.

        The member is addressed as itself: its configured target, its registered
        and connected account, its identity and its role binding on the linked
        scope, which it gets here if it has not spoken since it joined.
        """
        session = self.session
        target = session.settings.private_target(*endpoint)
        if target is None:
            raise TaskError("delivery_destination_missing", "the preferred endpoint is no longer configured")
        scope = link.scope_at(*endpoint)
        session.bridge.client(scope)
        try:
            context = session.authority.context(target.target_id, link.role_id)
        except TaskError:
            context = None
        if context is None or context.scope != scope:
            context = session.authority.select(target, scope,
                SubjectRef(scope.installation_id, SubjectKind.BOT, link.role_id))
        return context

    async def settings(self, umo, platform_name, cid):
        context = self.session.bridge.context
        cfg = context.get_config(umo=umo)
        if cfg.get("agent_runner", {}).get("runner_type") != "local":
            raise TaskError("linked_runner_unsupported", "linked dialogue requires AstrBot's local runner")
        plugins = cfg.get("plugin_set", ["*"])
        hooks = star_handlers_registry.get_handlers_by_event_type(EventType.OnWaitingLLMRequestEvent,
            plugins_name=None if plugins == ["*"] else plugins)
        if not any(handler.handler_name == "admit_linked_companion_turn" for handler in hooks):
            raise TaskError("linked_plugin_disabled", "enable the companion for every linked endpoint")
        provider = await context.get_using_provider_async(umo=umo)
        if provider is None:
            raise TaskError("linked_provider_unavailable", "select a native chat provider")
        conversation = await context.conversation_manager.get_conversation(umo, cid, create_if_not_exists=False)
        if conversation is None:
            raise TaskError("linked_anchor_missing", "the linked conversation no longer exists")
        selected = await context.persona_manager.resolve_selected_persona(umo=umo, platform_name=platform_name,
            conversation_persona_id=conversation.persona_id)
        services = await sp.get_async(scope="umo", scope_id=umo, key="session_service_config", default={}) or {}
        variables = await sp.session_get(umo, "session_variables", {})
        # Only what AstrBot resolves per origin and that shapes the turn, including
        # the runner's history compression and fallback models; the companion's
        # enablement is required above. Digests only: never provider credentials
        # or session-variable values.
        return digest({"provider_settings": cfg.get("provider_settings"), "agent_runner": cfg.get("agent_runner"),
            "provider": provider.provider_config.get("id"), "model": provider.get_model(),
            "persona": selected[:3], "services": services, "variables": variables})

    async def manage(self, body):
        required = {"schema_version", "link_id", "expected_revision", "conversation_id",
                    "source_target_id", "member_target_ids"}
        # Optional: removed endpoints the administrator admits to independent use, and
        # the member that receives the conversation's reminders and proactive text.
        admitted = body.get("independent_target_ids", [])
        preferred = body.get("preferred_target_id")
        if (set(body) - {"independent_target_ids", "preferred_target_id"} != required or type(admitted) is not list
                or any(type(value) is not str for value in admitted)
                or type(body["schema_version"]) is not int or body["schema_version"] != 1
                or type(body["expected_revision"]) is not int or body["expected_revision"] < 0
                or any(type(body[key]) is not str or not body[key].strip() or len(body[key]) > 128
                       for key in ("link_id", "conversation_id", "source_target_id"))
                or type(body["member_target_ids"]) is not list or len(body["member_target_ids"]) > 32
                or any(type(value) is not str for value in body["member_target_ids"])
                or len(set(body["member_target_ids"])) != len(body["member_target_ids"])
                or ("preferred_target_id" in body and preferred not in body["member_target_ids"])):
            raise TaskError("conversation_link_invalid", "supply a versioned conversation link and exact private members")
        if self.retiring:
            raise TaskError("linked_busy", "the companion is stopping")
        session, cid = self.session, body["conversation_id"]
        old = self.get(body["link_id"])
        if not body["member_target_ids"]:
            if old is None:
                raise TaskError("conversation_link_invalid", "supply a versioned conversation link and exact private members")
            # The conversation belongs to the companion and is reached through its
            # endpoints: without one it would stay stored and silently unreachable.
            raise TaskError("conversation_link_last_endpoint", "a linked conversation keeps at least one endpoint: "
                            "link another private endpoint before removing the last one")
        if (old["revision"] if old else 0) != body["expected_revision"]:
            raise TaskError("conversation_link_revision_conflict", "reload the current conversation link before editing")
        if old and old["conversation_id"] != cid:
            raise TaskError("conversation_link_invalid", "changing the conversation anchor requires lifecycle controls")
        targets = [session.settings.target(identifier) for identifier in body["member_target_ids"]]
        source = session.settings.target(body["source_target_id"]) if old is None else None
        for target in [*targets, *([source] if source is not None else [])]:
            if target not in session.settings.targets or target.participant_id == "*":
                raise TaskError("conversation_link_invalid", "only exact private targets can be linked")
            if (session.settings.window_link_for_target(target.target_id) is not None
                    or session.settings.window_links_for_primary(target.target_id)):
                raise TaskError("conversation_link_invalid", "remove the legacy window link first")
        known = {row["origin"]: row["platform_name"]
                 for row in (old or {}).get("members", []) + (old or {}).get("detached", [])}

        def platform_name(target):
            # An endpoint of the link keeps the channel kind stored with it: an edit
            # must not need the channel of a member that is down to be loaded.
            platform = session.bridge.context.get_platform_inst(target.platform_id)
            if platform is not None:
                return platform.meta().name
            if origin(target) not in known:
                raise TaskError("host_platform_unavailable", "an endpoint's channel must be loaded when it is linked")
            return known[origin(target)]
        members = [{**asdict(target), "origin": origin(target), "platform_name": platform_name(target)}
                   for target in targets]
        # A link addresses each endpoint by origin(), built from the participant id.
        # WeChat Native routes a private chat by a separate session id: its events would
        # never match the stored origin and would run outside the conversation permit.
        anchor_platform = session.bridge.context.get_platform_inst(source.platform_id) if source is not None else None
        if (any(row["platform_name"] == "wechat_native" for row in members)
                or (anchor_platform is not None and anchor_platform.meta().name == "wechat_native")):
            raise TaskError("conversation_link_invalid", "WeChat Native private chats cannot be linked")
        for row in members:
            collision = self.for_origin(row["origin"])
            if collision is not None and collision["link_id"] != body["link_id"]:
                raise TaskError("conversation_link_invalid", "an endpoint already belongs to another conversation")
        origins = {row["origin"] for row in members + (old["members"] if old else [])}
        if source is not None:
            origins.add(origin(source))
        origins.update(origin(session.settings.target(identifier)) for identifier in admitted)
        if origins & self.gate.editing_origins:
            raise TaskError("linked_busy", "another link operation is in progress")
        if any(active_event_registry._events.get(umo) for umo in origins):
            raise TaskError("linked_busy", "finish active turns before changing a conversation link")
        if cid in self.gate.editing_cids or cid in self.gate.history_writers.values():
            raise TaskError("linked_busy", "finish the companion's background history writer before linking")
        management_task = asyncio.current_task()
        self.management_tasks.add(management_task)
        self.gate.editing_cids.add(cid)
        self.gate.editing_origins.update(origins)
        try:
            async with AsyncExitStack() as stack:
                # Also coordinate with the companion's out-of-band history writers.
                await stack.enter_async_context(self.gate.locks.setdefault(cid, asyncio.Lock()))
                for umo in sorted(origins):
                    await stack.enter_async_context(session_lock_manager.acquire_lock(umo))
                # The pinned registry registers at scheduler entry, before any plugin
                # await. With editing_origins set, new linked intake fails closed.
                if any(active_event_registry._events.get(umo) for umo in origins):
                    raise TaskError("linked_busy", "finish active turns before changing a conversation link")
                current = self.get(body["link_id"])
                if (current["revision"] if current else 0) != body["expected_revision"]:
                    raise TaskError("conversation_link_revision_conflict", "the conversation link changed")
                manager = session.bridge.context.conversation_manager
                if source is not None:
                    if await manager.get_curr_conversation_id(origin(source)) != cid:
                        raise TaskError("linked_anchor_missing", "select the existing companion conversation first")
                    account = session.bridge.accounts[(source.platform_id, source.account_id)]
                    conversation_key, role, _ = await session.bridge._role(account, source.participant_id)
                    scope = ScopeRef(session.runtime.installation_id, source.platform_id, ScopeKind.PRIVATE,
                        source.participant_id, source.bot_id, source.account_id, conversation_key, source.participant_id)
                    anchor = {"target_id": source.target_id, "origin": origin(source),
                              "scope": scope.document(), "role": asdict(role),
                              "subject": asdict(session.authority.actor(source).subject)}
                else:
                    anchor = old["anchor"]
                signatures = [await self.settings(row["origin"], row["platform_name"], cid) for row in members]
                if source is not None:
                    source_platform = session.bridge.context.get_platform_inst(source.platform_id)
                    source_settings = await self.settings(origin(source), source_platform.meta().name, cid)
                    signatures.append(source_settings)
                if len(set(signatures)) != 1:
                    raise TaskError("linked_settings_mismatch", "align native model, persona and session settings before linking")
                detached = {row["origin"]: row for row in (old or {}).get("detached", []) + (old or {}).get("members", [])}
                if source is not None and origin(source) not in {row["origin"] for row in members}:
                    detached[origin(source)] = {**asdict(source), "origin": origin(source),
                                               "platform_name": source_platform.meta().name}
                for row in members:
                    detached.pop(row["origin"], None)
                released = [detached.pop(origin(session.settings.target(identifier)), None) for identifier in admitted]
                if None in released:
                    raise TaskError("conversation_link_invalid", "only a removed endpoint can be admitted independently")
                if any(row["origin"] == anchor["origin"] for row in released):
                    # Its native bot, role and human keys are the shared owner's: opened
                    # beside the link it would read what the conversation learns later.
                    raise TaskError("linked_anchor_endpoint_closed",
                                    "the endpoint the conversation started on cannot be admitted independently")
                record = {"schema_version": 1, "link_id": body["link_id"],
                    "revision": body["expected_revision"] + 1, "conversation_id": cid,
                    "anchor": anchor, "members": members, "detached": list(detached.values())}
                # An unnamed preference stays what it was, also when that member has
                # just left: the conversation then has no destination until one is chosen.
                record["preferred_target_id"] = preferred or preferred_target_id(old or record)
                # What an admitted endpoint recorded as a member stays the conversation's.
                former = {row["origin"]: row for row in (old or {}).get("former", []) + released}
                if former:
                    record["former"] = list(former.values())
                # Persist before selection: an interrupted operation remains closed,
                # rather than allowing an endpoint to continue in a silent fork.
                with session.runtime.transaction():
                    # Checked with no await before the write: an account link saved
                    # while this edit waited must not end up sharing an endpoint.
                    from companion_v2.relationships.account_links import PrivateAccountLinks
                    associated = {(m["platform"], m["account_id"], m["native_account_id"]) for group
                                  in PrivateAccountLinks(session.runtime).read()["groups"] for m in group["members"]}
                    if any((target.platform_id, target.account_id, target.participant_id) in associated
                           for target in [*targets, *([source] if source is not None else [])]):
                        raise TaskError("conversation_link_account_link_conflict",
                                        "an endpoint associated with other accounts cannot join a linked conversation")
                    closed = {row["origin"] for row in (old or {}).get("detached", [])}
                    for row in record["detached"] + released:
                        if row["origin"] not in closed:
                            # It leaves the conversation with this edit: what it captured stays behind.
                            session.authority.fence_removed_endpoint(replace(ScopeRef.restore(anchor["scope"]),
                                platform=row["platform_id"], account_id=row["account_id"],
                                native_scope_id=row["participant_id"], session_id=row["participant_id"]))
                    records = self.records()
                    records[body["link_id"]] = record
                    session.authority.write(KEY, records)
                    for target in targets:
                        principal = session.authority.principal(target)
                        subject = session.authority.subject(target)
                        directory = session.authority.identities
                        existing = directory.resolve_authenticated(principal, observed_at=session.now())
                        if existing.failure is not None and existing.failure.code != "unbound_identity":
                            from companion_v2.execution.models import require
                            require(existing)
                        if existing.failure is not None or existing.unwrap().subject != subject:
                            directory.bind(binding_id="linked-person:" + digest([record["link_id"], principal,
                                record["revision"]]), principal=principal, subject=subject,
                                valid_from=session.now(), expected_revision=directory.revision)
                self.gate.linked_cids.add(cid)
                self.gate.linked_origins = linked_origins(records)
                for row in members:
                    await manager.switch_conversation(row["origin"], cid)
                # A removed endpoint keeps no reference to the shared conversation. It
                # stays refused; what it selects instead is new, empty and its own.
                for row in record["detached"] + released:
                    if await manager.get_curr_conversation_id(row["origin"]) in (cid, None):
                        await manager.new_conversation(row["origin"], row["platform_id"])
                return record
        finally:
            # The committed record, never a rolled-back attempt, is what owners resolve from.
            link_records_changed(session.runtime)
            self.gate.editing_origins.difference_update(origins)
            self.gate.editing_cids.discard(cid)
            self.management_tasks.discard(management_task)

    async def admit(self, controller, event, *, control=False):
        """Admit a turn, or with ``control`` one of AstrBot's own session commands.

        The command then runs unchanged on its own origin, under the same
        conversation permit as a turn, and ``settle`` makes the members agree.
        """
        umo = event.unified_msg_origin
        link = self.for_event(event)
        if link is None and umo not in self.gate.editing_origins:
            return True
        try:
            if self.retiring or umo in self.gate.editing_origins:
                raise TaskError("linked_busy", "the conversation is busy with a management operation")
            target = controller.target_for_event(event)
            member = next((row for row in link["members"] if row["origin"] == umo), None)
            if target is None or member is None or any(asdict(target).get(key) != member.get(key)
                    for key in asdict(target)):
                raise TaskError("linked_endpoint_revoked", "this private endpoint no longer belongs to the conversation")
            # /stop is per physical origin and must be able to reach a running turn.
            if self.stop_only(event):
                return True
            if control and self.native_command(event, CONTEXT_COMMANDS):
                for row in link["members"]:
                    active_event_registry.stop_all(row["origin"], exclude=event)
            stamp = event.get_extra(EXTRA)
            if stamp is None:
                stamp = {"link_id": link["link_id"], "revision": link["revision"],
                         "conversation_id": link["conversation_id"], "control": control}
                event.set_extra(EXTRA, stamp)
            await self.gate.acquire(link["conversation_id"], event)
            if event.is_stopped():
                raise TaskError("linked_turn_stopped", "a conversation control stopped this queued turn")
            await self.validate(event, pipeline=True, parity=not control)
            if not stamp.get("admitted"):
                from .private_debounce import PrivateDeliveryDedup
                if not hasattr(self.gate, "deliveries"):
                    self.gate.deliveries = PrivateDeliveryDedup()
                message_id = str(event.message_obj.message_id)
                if not message_id or self.gate.deliveries.duplicate((umo, message_id)):
                    raise TaskError("linked_duplicate_delivery", "this native input has already been admitted")
                stamp["admitted"] = True
            return True
        except BaseException as error:
            event.stop_event()
            event.set_extra("companion_linked_failure", getattr(error, "code", "linked_busy"))
            if isinstance(error, Exception):
                from astrbot.core.message.message_event_result import MessageChain
                if getattr(error, "code", "") not in ("linked_duplicate_delivery", "linked_turn_stopped"):
                    await event.send(MessageChain().message("共享对话暂不可用，请等待当前对话结束或由管理员检查连接。"))
                return False
            raise

    @staticmethod
    def native_command(event, names):
        handlers = event.get_extra("activated_handlers", ()) or ()
        return any(handler.handler_module_path == "astrbot.builtin_stars.builtin_commands.main"
                   and handler.handler_name in names for handler in handlers)

    @staticmethod
    def stop_only(event):
        return LinkedConversations.native_command(event, ("stop",))

    def stop_members(self, event):
        """AstrBot's /stop reaches its own origin; a member's also asks the others' turns to stop."""
        link = self.for_event(event)
        if (link is not None and self.stop_only(event)
                and any(row["origin"] == event.unified_msg_origin for row in link["members"])):
            for row in link["members"]:
                if row["origin"] != event.unified_msg_origin:
                    active_event_registry.request_agent_stop_all(row["origin"])

    def require_permit(self, event):
        link = self.for_event(event)
        if link is None:
            return
        stamp = event.get_extra(EXTRA)
        if (not isinstance(stamp, dict) or stamp.get("link_id") != link["link_id"]
                or stamp.get("revision") != link["revision"]
                or stamp.get("conversation_id") != link["conversation_id"]
                or not any(row["origin"] == event.unified_msg_origin for row in link["members"])
                or not self.gate.protects(link["conversation_id"], event)):
            event.stop_event()
            raise TaskError("linked_permit_required", "linked preparation requires its live pipeline permit")

    async def validate(self, event, *, pipeline=False, parity=True):
        link = self.for_event(event)
        stamp = event.get_extra(EXTRA)
        if link is None:
            if stamp is not None:
                raise TaskError("linked_revision_changed", "the link was removed")
            return
        if (not isinstance(stamp, dict) or stamp.get("revision") != link["revision"]
                or stamp.get("link_id") != link["link_id"]
                or stamp.get("conversation_id") != link["conversation_id"]
                or not (self.gate.holds if pipeline else self.gate.protects)(link["conversation_id"], event)):
            raise TaskError("linked_revision_changed", "this turn no longer holds the current conversation permit")
        if not any(row["origin"] == event.unified_msg_origin for row in link["members"]):
            raise TaskError("linked_endpoint_revoked", "the endpoint is no longer a member")
        manager = self.session.bridge.context.conversation_manager
        # All members keep their own transport. An offline former anchor is
        # irrelevant: settings and the CID are stored independently of that client.
        # Members are compared with each other on every turn, never with a value
        # frozen at link time: a setting changed for all of them is followed.
        # A control is checked on its own origin only: it is what makes the others agree.
        signatures = set()
        for row in link["members"] if parity else ():
            if await manager.get_curr_conversation_id(row["origin"]) != link["conversation_id"]:
                raise TaskError("linked_selection_changed", "a linked endpoint selected another conversation")
            signatures.add(await self.settings(row["origin"], row["platform_name"], link["conversation_id"]))
        if not parity:
            if await manager.get_curr_conversation_id(event.unified_msg_origin) != link["conversation_id"]:
                raise TaskError("linked_selection_changed", "this endpoint selected another conversation")
            return
        if len(signatures) != 1:
            raise TaskError("linked_settings_mismatch", "align native model, persona and session settings between the linked endpoints")
        selected_provider, selected_model = event.get_extra("selected_provider"), event.get_extra("selected_model")
        if selected_provider or selected_model:
            provider = await self.session.bridge.context.get_using_provider_async(umo=event.unified_msg_origin)
            if ((selected_provider and selected_provider != provider.provider_config.get("id"))
                    or (selected_model and selected_model != provider.get_model())):
                raise TaskError("linked_settings_changed", "the event tried to override the linked model selection")
        request = event.get_extra("provider_request")
        if request is not None:
            conversation = getattr(request, "conversation", None)
            if conversation is None or conversation.cid != link["conversation_id"]:
                raise TaskError("linked_selection_changed", "a handler requested a different conversation")
        if event.get_extra("action_type") == "live":
            raise TaskError("linked_live_unsupported", "Live Mode writes history twice and is unavailable on linked endpoints")

    async def settle(self, event):
        """After AstrBot's own command ran on one member: every member agrees with it.

        Still inside the command's conversation permit. A new conversation becomes
        the link's, with a new revision; session variables and the chat provider
        are applied to the others through AstrBot's own setters.
        """
        stamp = event.get_extra(EXTRA)
        if not isinstance(stamp, dict) or not stamp.get("control"):
            return
        try:
            link, umo = self.for_event(event), event.unified_msg_origin
            if (link is None or link["revision"] != stamp["revision"]
                    or not self.gate.holds(link["conversation_id"], event)):
                raise TaskError("linked_revision_changed", "the link changed while the command ran")
            context = self.session.bridge.context
            manager = context.conversation_manager
            cid = await manager.get_curr_conversation_id(umo)
            if cid != link["conversation_id"]:
                if not cid or await manager.get_conversation(umo, cid) is None:
                    raise TaskError("linked_anchor_missing", "the command left no conversation selected")
                with self.session.runtime.transaction():
                    records = self.records()
                    records[link["link_id"]] = {**link, "conversation_id": cid, "revision": link["revision"] + 1}
                    self.session.authority.write(KEY, records)
                link_records_changed(self.session.runtime)
                self.gate.follow(link["conversation_id"], cid)
                self.gate.linked_cids.add(cid)
                stamp.update(conversation_id=cid, revision=link["revision"] + 1)
            variables = await sp.session_get(umo, "session_variables", {})
            provider = await context.get_using_provider_async(umo=umo)
            for other in (row["origin"] for row in link["members"] if row["origin"] != umo):
                if await manager.get_curr_conversation_id(other) != cid:
                    await manager.switch_conversation(other, cid)
                if await sp.session_get(other, "session_variables", {}) != variables:
                    await sp.session_put(other, "session_variables", variables)
                selected = await context.get_using_provider_async(umo=other)
                if provider is not None and (selected is None
                        or selected.provider_config.get("id") != provider.provider_config.get("id")):
                    from astrbot.core.provider.entities import ProviderType
                    await context.provider_manager.set_provider(provider.provider_config["id"],
                        ProviderType.CHAT_COMPLETION, umo=other)
        except BaseException:
            event.stop_event()
            raise

    async def tripwire(self, event, req):
        if self.for_event(event) is None:
            return
        try:
            await self.validate(event, pipeline=True)
            if req.conversation is None or req.conversation.cid != event.get_extra(EXTRA)["conversation_id"]:
                raise TaskError("linked_selection_changed", "native request selected another conversation")
            if req.session_id and req.session_id != event.unified_msg_origin:
                raise TaskError("linked_selection_changed", "a handler changed the physical provider session")
            if req.model:
                provider = await self.session.bridge.context.get_using_provider_async(umo=event.unified_msg_origin)
                if req.model != provider.get_model():
                    raise TaskError("linked_settings_changed", "a handler changed the linked model")
            # Background/cron tools persist history outside the native turn lock.
            tools = getattr(req, "func_tool", None)
            if tools is not None:
                tools.tools = [tool for tool in tools.tools if not getattr(tool, "is_background_task", False)
                               and ".cron" not in type(tool).__module__]
        except BaseException:
            event.stop_event()
            raise

    async def close(self):
        self.retiring = True
        await asyncio.gather(*(asyncio.shield(task) for task in tuple(self.management_tasks)),
                             return_exceptions=True)
        await self.gate.drain({link["conversation_id"] for link in self.records().values()})


async def refuse_unavailable_link(controller, event):
    """Persisted links remain closed before startup and while the host is stopped."""
    umo = event.unified_msg_origin
    if ":FriendMessage:" not in umo or LinkedConversations.stop_only(event):
        return False
    session = getattr(controller, "session", None)
    links = getattr(session, "linked_conversations", None)
    if (links is not None and not links.retiring
            and getattr(getattr(session, "application", None), "state", None) == "running"):
        return False
    gate = LinkedTurnGate.shared()
    linked = getattr(gate, "linked_origins", None)
    if linked is None:
        # A new process whose host has not assembled yet: read the one
        # management record once, with no migrations, creation or startup.
        root = getattr(controller, "profile_root", None)
        if root is None:
            return False
        from contextlib import closing
        import sqlite3
        from companion_v2.storage.runtime import StorageRuntime
        path = root / "runtime" / StorageRuntime.DATABASE_FILENAME
        if not path.is_file():
            return False
        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
                row = connection.execute("SELECT value FROM runtime_metadata WHERE key = ?", (KEY,)).fetchone()
            linked = gate.linked_origins = linked_origins(json.loads(row[0]) if row else {})
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError) as error:
            # Only an exact configured private target can be a link member: without
            # the record those stay closed, and every other chat keeps AstrBot's reply.
            logger.warning("[CompanionV2] 共享对话记录暂不可读 error_type=%s", type(error).__name__)
            linked = {origin(target) for target in controller.settings.targets if target.participant_id != "*"}
    if umo not in linked:
        return False
    event.stop_event()
    event.set_extra("companion_linked_failure", "linked_host_unavailable")
    from astrbot.core.message.message_event_result import MessageChain
    await event.send(MessageChain().message("共享对话尚未就绪，请等待陪伴宿主启动。"))
    return True
