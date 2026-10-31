"""Composition of shared semantic ports with memory, vocabulary and reactions."""

import asyncio
from dataclasses import replace
import json
import logging
import time

from companion_v2.execution.models import TaskContext, TaskError, digest, encode, require
from companion_v2.foundation.refs import RoleOwnerRef, ScopeKind
from companion_v2.model_gateway.embedding import EmbeddingGateway
from companion_v2.model_gateway.semantic_search import CatalogSearch
from companion_v2.model_gateway.models import ToolSpec
from companion_v2.storage.semantic import SemanticStore
from companion_v2.memory.semantic import MemorySemanticRecall
from companion_v2.memory.group.slang import GroupSlang, INSTRUCTION as SLANG_INSTRUCTION
from companion_v2.proactive.news_context import (INSTRUCTION as NEWS_INSTRUCTION,
                                                 UNAVAILABLE_INSTRUCTION,
                                                 ConversationNewsContext)
from .reaction_configuration import read_settings as read_reaction_settings
from .reaction_configuration import read_snapshot as read_reaction_configuration
from .reactions import ReactionLibrary, INSTRUCTION
from .reaction_preferences import ReactionPreferences
logger = logging.getLogger(__name__)


FIELDS = {"reaction.search": (("intent",), ()), "slang.search": (("query",), ()),
          "slang.learn": (("term", "meaning", "input_handle"), ("usage", "correction"))}
# Reaction vector backfill shares the one background registry with memory, under
# a namespaced key so ``prepare_memory`` still finds the memory job by owner.
REACTION_BACKFILL_KEY = "reaction-backfill:"
REACTION_CAPTURE_KEY = "reaction-capture:"


class SemanticService:
    actions = frozenset(FIELDS)

    def __init__(self, app, port, *, reaction_role_count=0):
        if type(reaction_role_count) is not int or not 0 <= reaction_role_count <= 10000:
            raise ValueError("reaction role count must be a bounded non-negative integer")
        self.app = app
        self.reaction_role_count = reaction_role_count
        self.store = SemanticStore(app.runtime.data_root)
        self.gateway = EmbeddingGateway(port, timeout=self.store.settings()["timeout_seconds"])
        self.memory = MemorySemanticRecall(app.memory, self.gateway, self.store.settings)
        self.slang = GroupSlang(self)
        self.reactions = ReactionLibrary(self)
        self.reaction_preferences = ReactionPreferences(self)
        self.news_context = ConversationNewsContext(app)
        self.search = CatalogSearch(self.store, self.gateway, self.validate_item)
        self.locks, self.background, self.errors = {}, {}, {}
        self.backfill_runs = {}
        self.reaction_capture_runs = {}
        self._reaction_catalog_ready = set()
        self.prepared = set()
        self.closed = False
        descriptions = {"reaction.search": "按角色想怎样回应检索表情原图，可提供安慰、吐槽等用途及否定语境；找不到可不发。",
                        "slang.search": "按当前原句查询本群梗与词义，结果仅作有证据的语义参考。",
                        "slang.learn": "根据本轮原始群发言学习词义；明确纠正时设 correction=true。"}
        self.catalog = tuple(ToolSpec(action, descriptions[action], encode({"type": "object",
            "properties": {k: {"type": "boolean" if k == "correction" else "string"} for k in (*required, *optional)},
            "required": list(required), "additionalProperties": False})) for action,(required,optional) in FIELDS.items())

    @staticmethod
    def owner(context):
        s = context.scope
        return digest(["semantic-owner.v1", s.installation_id, s.platform, s.bot_id, s.account_id,
                       s.kind, s.native_scope_id, s.session_id if s.kind is ScopeKind.GROUP else "", context.role_binding.role])

    @staticmethod
    def reaction_owner(context):
        scope = context.scope
        owner = RoleOwnerRef(scope.installation_id, scope.bot_id,
                             context.role_binding.role.subject_id)
        return digest(["semantic-reaction-role.v1", owner])

    def ensure_reaction_catalog(self, context):
        """Promote old window catalogs for this role, preserving every source row."""
        target_owner = self.reaction_owner(context)
        if target_owner in self._reaction_catalog_ready:
            return target_owner
        scope, role_id = context.scope, context.role_binding.role.subject_id
        rows = self.app.runtime.read_all("""SELECT DISTINCT context_json FROM conversation_batches
            WHERE json_extract(context_json,'$.role_binding.scope.installation_id')=?
            AND json_extract(context_json,'$.role_binding.scope.bot_id')=?
            AND json_extract(context_json,'$.role_binding.role.subject_id')=?""",
            (scope.installation_id, scope.bot_id, role_id))
        for row in rows:
            try:
                candidate = TaskContext.restore(json.loads(row["context_json"]))
            except (KeyError, TypeError, ValueError):
                continue
            candidate_scope = candidate.scope
            if (candidate_scope.installation_id != scope.installation_id
                    or candidate_scope.bot_id != scope.bot_id
                    or candidate.role_binding.role.subject_id != role_id):
                continue
            self.store.promote_reaction_owner(self.owner(candidate), target_owner)
        self.store.promote_reaction_owner(self.owner(context), target_owner)
        if self.reaction_role_count == 1:
            for source_owner in self.store.unclaimed_legacy_reaction_owners(target_owner):
                self.store.promote_reaction_owner(source_owner, target_owner)
        self._reaction_catalog_ready.add(target_owner)
        return target_owner

    def check(self, context, *, action="conversation.reply"):
        if self.closed:
            raise TaskError("semantic_stopped", "semantic service is stopped")
        require(self.app.tasks.authorize_context(context, action=action, observed_at=self.app.now()))

    @staticmethod
    def reaction_enabled(context, cfg):
        scope_setting = "reactions_group_enabled" if context.scope.kind is ScopeKind.GROUP else "reactions_private_enabled"
        return cfg["reactions_enabled"] and cfg[scope_setting]

    def reaction_capable(self, context, cfg=None):
        """Return whether this role may ever use the reaction library.

        Mount-time capability only. Per-turn eligibility stays in
        ``reaction_available``; keeping the per-turn roll out of the tool
        catalog stops the model-facing tool list from flipping between turns
        and invalidating the provider prefix cache.
        """
        cfg = cfg or self.store.settings()
        return (cfg["enabled"] and not self.closed and self.reaction_enabled(context, cfg)
                and self.app.messages.reply_images.enabled)

    def reaction_available(self, context, batch_id, cfg=None):
        cfg = cfg or self.store.settings()
        return (self.reaction_capable(context, cfg)
                and self.reaction_preferences.available(context, batch_id, cfg))

    def proactive_reaction_available(self, context, contact_id, *, diagnostic=False):
        cfg = self.store.settings()
        if diagnostic:
            if (not cfg["enabled"] or not self.reaction_enabled(context, cfg)
                    or not self.app.messages.reply_images.enabled):
                return False
            self.check(context, action="proactive.contact")
            return self.reaction_preferences.allowed(context, contact_id)
        if not cfg["reactions_proactive_enabled"] or not self.reaction_available(context, contact_id, cfg):
            return False
        self.check(context, action="proactive.contact")
        return True

    def validate_item(self, item, context, *, action="conversation.reply"):
        cfg = self.store.settings()
        if not cfg["enabled"] or not cfg["slang_enabled" if item["kind"] == "slang" else "reactions_enabled"]:
            return False
        if context is not None:
            self.check(context, action=action)
            expected_owner = (self.ensure_reaction_catalog(context) if item["kind"] == "reaction"
                              else self.owner(context))
            if item["owner"] != expected_owner:
                return False
            if item["kind"] == "reaction" and not self.reaction_enabled(context, cfg):
                return False
        current = self.store.get(item["owner"], item["kind"], item["id"])
        if not current["enabled"] or current["revision"] != item["revision"]:
            return False
        return self.slang.validate(item, context) if item["kind"] == "slang" else True

    def catalog_for(self, context, batch_id=None, *, mount_stable=False):
        cfg = self.store.settings()
        if not cfg["enabled"] or self.closed:
            return ()
        self.check(context)
        available = []
        for tool in self.catalog:
            if tool.action == "reaction.search":
                # The native core reply mounts a stable catalog and lets the
                # per-turn check deny the call, so the tool list does not churn.
                permitted = (self.reaction_capable(context, cfg) if mount_stable
                             else self.reaction_available(context, batch_id, cfg))
                if permitted:
                    available.append(tool)
            elif cfg["slang_enabled"] and context.scope.kind is ScopeKind.GROUP:
                try:
                    self.slang.memory.access.check(context, "group.memory.write" if tool.action == "slang.learn" else "group.memory.read")
                    available.append(tool)
                except TaskError:
                    pass
        return tuple(available)

    def catalog_filter_reason(self, context, action):
        """Return a content-free reason code for one omitted semantic action."""
        cfg = self.store.settings()
        if self.closed:
            return "semantic_stopped"
        if not cfg["enabled"]:
            return "semantic_disabled"
        if action == "reaction.search":
            if not cfg["reactions_enabled"]:
                return "reactions_disabled"
            if not self.reaction_enabled(context, cfg):
                return "reactions_disabled_for_scope"
            if not self.app.messages.reply_images.enabled:
                return "reply_images_unavailable"
            return "reaction_preferences_or_media_unavailable"
        if not cfg["slang_enabled"]:
            return "slang_disabled"
        try:
            self.slang.memory.access.check(context,
                "group.memory.write" if action == "slang.learn" else "group.memory.read")
        except TaskError as error:
            return error.code
        return "semantic_action_unavailable"

    def validate_arguments(self, action, args):
        required, optional = FIELDS[action]
        if type(args) is not dict or set(required) - set(args) or set(args) - set(required) - set(optional):
            raise ValueError("invalid semantic tool arguments")
        if any(type(v) is not (bool if k == "correction" else str) or isinstance(v, str) and len(v) > 2000 for k,v in args.items()):
            raise ValueError("invalid semantic argument value")

    async def execute(self, run, call):
        context, batch_id = run.task.spec.context, run.task.spec.request_ref
        if call.action not in {t.action for t in self.catalog_for(context, batch_id)}:
            raise TaskError("semantic_action_unavailable", "semantic action is disabled or unauthorized")
        args = json.loads(call.arguments_json)
        self.validate_arguments(call.action, args)
        if call.action == "reaction.search":
            payload = await self.reactions.search(context, batch_id, call.call_id, args["intent"])
        elif call.action == "slang.search":
            _, historical = self._slang_context_texts(context, batch_id)
            payload = {"kind": "slang", **await self.slang.search(context, args["query"], recent_texts=historical)}
            self.store.offer(batch_id, call.call_id, self.owner(context), payload)
        else:
            item = self.slang.learn(context, batch_id, **args)
            payload = {"kind": "slang", "items": [{k: item[k] for k in ("id", "revision", "payload")}]}
            self.store.offer(batch_id, call.call_id, self.owner(context), payload)
        run.validate()
        return {"state": "succeeded", "effect": "committed" if call.action == "slang.learn" else "none", "payload": payload}

    async def execute_native(self, context, turn_id, call_id, action, args):
        if action not in {tool.action for tool in self.catalog_for(context, turn_id, mount_stable=True)}:
            raise TaskError("semantic_action_unavailable", "semantic action is disabled or unauthorized")
        self.validate_arguments(action, args)
        if action == "reaction.search":
            payload = await self.reactions.search_native(context, turn_id, call_id, args["intent"])
        elif action == "slang.search":
            payload = {"kind": "slang", **await self.slang.search(context, args["query"])}
            self.store.offer(turn_id, call_id, self.owner(context), payload)
        else:
            item = self.slang.learn(context, turn_id, **args)
            payload = {"kind": "slang", "items": [{key: item[key] for key in ("id", "revision", "payload")}]}
            self.store.offer(turn_id, call_id, self.owner(context), payload)
        self.check(context)
        return {"state": "succeeded", "effect": "committed" if action == "slang.learn" else "none", "payload": payload}

    def validate_native(self, context, turn_id, call_id, action, result):
        validated, _ = self.validate({"state": result["state"], "action": action,
            "batch_id": turn_id, "tool_call_id": call_id}, context, result)
        return validated

    def validate(self, row, context, result):
        self.check(context)
        if row["state"] != "succeeded":
            return result, None
        offer_owner = (self.ensure_reaction_catalog(context) if row["action"] == "reaction.search"
                       else self.owner(context))
        saved = dict(self.store.offers(row["batch_id"], offer_owner)).get(row["tool_call_id"])
        if saved != result.get("payload"):
            raise TaskError("semantic_result_changed", "original semantic result is unavailable")
        kind = saved["kind"]
        changed = self.changed_slang(row["batch_id"])
        for offered in saved.get("items", []):
            item = self.store.get(offer_owner, kind, offered["id"])
            if kind == "slang" and item["revision"] != offered["revision"] and offered["id"] in changed:
                if not self.validate_item(item, context):
                    raise TaskError("semantic_result_changed", "updated vocabulary evidence is unavailable")
                return {"state": "superseded", "effect": result["effect"], "reason": "changed_by_this_turn"}, None
            if item["revision"] != offered["revision"] or not self.validate_item(item, context):
                raise TaskError("semantic_result_changed", "original semantic evidence changed")
        return result, None

    def changed_slang(self, batch_id):
        from companion_v2.conversation.native_tool_sources import native_turn
        if native_turn(batch_id):
            rows = self.app.runtime.read_all("""SELECT value FROM runtime_metadata WHERE key LIKE ?
                AND json_extract(value,'$.turn_id')=? AND json_extract(value,'$.action')='slang.learn'
                AND json_extract(value,'$.state')='succeeded'""", ("astrbot.core-tool.%", batch_id))
            return {item["id"] for row in rows
                    for item in json.loads(row["value"])["result"]["payload"]["items"]}
        rows = self.app.runtime.read_all("SELECT result_json FROM conversation_tool_calls WHERE batch_id=? AND action='slang.learn' AND state='succeeded'", (batch_id,))
        return {item["id"] for row in rows for item in json.loads(row["result_json"])["payload"]["items"]}

    def _slang_context_texts(self, context, batch_id):
        from companion_v2.conversation.messages import source_from_document
        rows = self.app.runtime.read_all("SELECT event_json FROM conversation_inputs WHERE batch_id=? AND withdrawn=0 ORDER BY position",
                                        (batch_id,))
        texts = [self.app.messages._read_source(source_from_document(json.loads(row["event_json"])["source"]),
                    context, "text", self.app.now()).payload for row in rows]
        historical = []
        try:
            batch = self.app.messages.batch(batch_id)
            snapshot = json.loads(batch["contributions_json"]).get("group_context")
            if snapshot is not None:
                items = self.app.messages.group_context.read(snapshot, context, batch_id, self.app.now())
                historical = [item["text"] for item, _ in items if item.get("type") == "group_member_message"
                              and type(item.get("text")) is str and item["text"].strip()][-4:]
        except Exception as error:
            logger.debug("V2 group slang history unavailable: %s", type(error).__name__)
        return [text for text in texts if type(text) is str], historical

    async def prepare_turn(self, run):
        cfg = self.store.settings()
        if run.task.task_id in self.prepared:
            return
        self.prepared.add(run.task.task_id)
        context = run.task.spec.context
        self.check(context)
        try:
            if await self.news_context.prepare_turn(run, self.app.now()):
                self.errors.pop("news_recall", None)
        except Exception as exc:
            self.errors["news_recall"] = getattr(exc, "code", type(exc).__name__)
        if not cfg["enabled"]:
            if len(self.prepared) > 1024:
                self.prepared = {run.task.task_id}
            return
        self.gateway.timeout = cfg["timeout_seconds"]
        self.reaction_preferences.prepare(context, run.task.spec.request_ref)
        # Learning runs before opening the model session, so its material is stable.
        if context.scope.kind is ScopeKind.GROUP and cfg["slang_enabled"]:
            try:
                async with self.locks.setdefault(self.owner(context), asyncio.Lock()):
                    try:
                        await self.slang.observe(context, run.task.spec.request_ref)
                        self.errors.pop("slang_learning", None)
                    except Exception as exc:
                        self.errors["slang_learning"] = getattr(exc, "code", type(exc).__name__)
                    texts, historical = self._slang_context_texts(context, run.task.spec.request_ref)
                    result = await self.slang.search(context, "\n".join(texts), recent_texts=historical, limit=2)
                    self.store.offer(run.task.spec.request_ref, "__slang_context", self.owner(context), {"kind": "slang", **result})
            except Exception as exc:
                self.errors["slang_recall"] = getattr(exc, "code", type(exc).__name__)
        else:
            # Only the current query belongs on the reply path. Both memory
            # kinds use the same vector; document indexing uses the background
            # owner lock, so a previous fill cannot hold up this turn.
            if cfg["memory_enabled"]:
                try:
                    rows = self.app.runtime.read_all(
                        "SELECT event_json FROM conversation_inputs "
                        "WHERE batch_id=? AND withdrawn=0 ORDER BY position",
                        (run.task.spec.request_ref,),
                    )
                    from companion_v2.conversation.messages import source_from_document
                    texts = []
                    for row in rows:
                        event = json.loads(row["event_json"])
                        source = source_from_document(event["source"])
                        try:
                            record = self.app.messages._read_source(source, context, "text", self.app.now())
                        except Exception as error:
                            logger.warning("V2 semantic batch source read failed: %s", type(error).__name__)
                            continue
                        if isinstance(record.payload, str) and record.payload.strip():
                            texts.append(record.payload)
                    intent = "\n".join(texts)[-2000:]
                    if intent:
                        prepared = await self.memory.prepare_query(context, {"query_intent": intent})
                        if prepared.get("vector") and prepared.get("model"):
                            self.app.memory.context.enrich_semantic(
                                context, run.task.spec.request_ref, intent,
                                prepared["vector"], prepared["model"],
                            )
                            self.errors.pop("memory_recall", None)
                except Exception as exc:
                    # Source expiry, indexing, and provider failures are
                    # optional enrichment; the sealed lexical context remains
                    # authoritative for this turn.
                    self.errors["memory_recall"] = getattr(exc, "code", type(exc).__name__)
            self.schedule_memory(context)
        if len(self.prepared) > 1024:
            self.prepared = {run.task.task_id}

    def schedule_memory(self, context):
        cfg = self.store.settings()
        if self.closed or not cfg["enabled"] or not cfg["memory_enabled"] or context.scope.kind is ScopeKind.GROUP:
            return
        key = self.owner(context)
        if key in self.background:
            return
        async def fill():
            try:
                async with self.locks.setdefault(key, asyncio.Lock()):
                    for kind in ("facts", "sources", "experiences", "all"):
                        await self.memory.backfill(context, kind)
            except Exception as exc:
                self.errors["memory_backfill"] = getattr(exc, "code", type(exc).__name__)
            finally:
                self.background.pop(key, None)
        self.background[key] = asyncio.create_task(fill(), name="v2-semantic-backfill")

    def schedule_native_input(self, context, turn_id):
        """Learn/index core input in the background, outside its native model turn."""
        cfg = self.store.settings()
        if self.closed or not cfg["enabled"]:
            return
        if context.scope.kind is ScopeKind.PRIVATE:
            if cfg["memory_enabled"]:
                self.schedule_memory(context)
            return
        if context.scope.kind is not ScopeKind.GROUP or not cfg["slang_enabled"] or not cfg["slang_learning"]:
            return
        key = "native-slang:" + self.owner(context)
        if key in self.background:
            return

        async def learn():
            try:
                async with self.locks.setdefault(self.owner(context), asyncio.Lock()):
                    self.check(context)
                    await self.slang.observe(context, turn_id)
                    self.errors.pop("slang_learning", None)
            except Exception as error:
                if not self.closed:
                    self.errors["slang_learning"] = getattr(error, "code", type(error).__name__)
            finally:
                self.background.pop(key, None)

        self.background[key] = asyncio.create_task(learn(), name="v2-native-slang-learning")

    def reaction_settings(self, context):
        """Installation-wide reaction bounds and their revision; never blocks a turn."""
        return read_reaction_configuration(self.app.runtime, context)

    def schedule_reaction_backfill(self, context, settings=None):
        """Fill a bounded batch of reaction vectors for one owner, off the reply path.

        V1 did this from its lifecycle background tasks with an enable switch, a
        batch size, a per-provider minimum interval and an in-flight set
        (``llm_tool_actions.py:5789-5837``).  The same shape is reused here with
        the one background registry memory already uses: one task per owner, so two
        runs cannot write the same vectors at once, cancelled on reconfigure and
        on shutdown.  The retrieval path only pays a dictionary lookup when the
        interval has not elapsed.
        """
        cfg = self.store.settings()
        if settings is None:
            settings = read_reaction_settings(self.app.runtime, context)
        if self.closed or not cfg["enabled"] or not settings.embedding_backfill_enabled:
            return
        owner = self.ensure_reaction_catalog(context)
        key = REACTION_BACKFILL_KEY + owner
        if key in self.background:
            return
        now = time.monotonic()
        interval = settings.embedding_backfill_interval_seconds
        if interval and now - self.backfill_runs.get(key, 0.0) < interval:
            return
        self.backfill_runs[key] = now
        limit = settings.embedding_backfill_batch_size

        async def fill():
            try:
                async with self.locks.setdefault(owner, asyncio.Lock()):
                    await self.search.backfill(owner, "reaction", limit=limit,
                                               validate=lambda item: self.validate_item(item, None))
                self.errors.pop("reaction_backfill", None)
            except Exception as exc:
                self.errors["reaction_backfill"] = getattr(exc, "code", type(exc).__name__)
            finally:
                self.background.pop(key, None)

        self.background[key] = asyncio.create_task(fill(), name="v2-reaction-backfill")

    async def prepare_memory(self, context, args):
        pending = self.background.get(self.owner(context))
        if pending:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        async with self.locks.setdefault(self.owner(context), asyncio.Lock()):
            return await self.memory.prepare(context, args)

    def augment(self, batch, context, request):
        news_material = self.news_context.material(batch["generation_task_id"], context, self.app.now())
        if news_material is not None:
            request = replace(request, context=replace(request.context,
                materials=request.context.materials + (news_material,)),
                instruction=request.instruction + "\n" + NEWS_INSTRUCTION)
        elif self.news_context.unavailable(batch["generation_task_id"]):
            request = replace(request, instruction=request.instruction + "\n" + UNAVAILABLE_INSTRUCTION)
        self.schedule_reaction_collection(batch["batch_id"], context, request)
        request = self.reactions.augment(batch, context, request)
        if context.scope.kind is ScopeKind.GROUP:
            from companion_v2.conversation.foreground import PromptMaterial
            payload = dict(self.store.offers(batch["batch_id"], self.owner(context))).get("__slang_context")
            if payload and payload["items"]:
                changed = self.changed_slang(batch["batch_id"])
                current_items = []
                for item in payload["items"]:
                    if item["id"] in changed:
                        continue
                    current = self.store.get(self.owner(context), "slang", item["id"])
                    if current["revision"] != item["revision"] or not self.validate_item(current, context):
                        raise TaskError("slang_context_changed", "group vocabulary evidence changed during this reply")
                    current_items.append(item)
                if current_items:
                    request = replace(request, context=replace(request.context, materials=request.context.materials + (
                        PromptMaterial("group.slang", "contextual_word_meanings", encode({**payload, "items": current_items})),)))
            request = replace(request, instruction=request.instruction + "\n" + SLANG_INSTRUCTION)
        return request

    @staticmethod
    def _reaction_collection_fields(context):
        if context.scope.kind is ScopeKind.PRIVATE:
            return "reactions_collection_private_enabled", "reactions_collection_private_probability", "private_capture"
        if context.scope.kind is ScopeKind.GROUP:
            return "reactions_collection_group_enabled", "reactions_collection_group_probability", "group_capture"
        return None

    def reaction_collection_allowed(self, context, turn_id):
        fields = self._reaction_collection_fields(context)
        settings = self.store.settings()
        if fields is None or self.closed or not settings["enabled"] or not settings[fields[0]]:
            return False
        sample = int(digest(["reaction-capture-sample.v1", self.reaction_owner(context), turn_id])[:16], 16) / 2**64
        return sample < settings[fields[1]]

    def schedule_native_reaction_collection(self, context, turn_id, event):
        """Read captured originals on a worker before the existing collection flow."""
        if not self.reaction_collection_allowed(context, turn_id):
            return
        key = "native-reaction-prepare:" + digest(turn_id)
        if key in self.background:
            return

        async def prepare():
            try:
                def read():
                    self.app.messages.recalls.check(context.scope, event.event_id, event.principal.native_account_id)
                    return self.app.messages.images.read(context, (event,), self.app.now())
                images = await asyncio.to_thread(read)
                if not self.closed:
                    self.schedule_reaction_collection(turn_id, context, native_images=images, native_event=event)
            except Exception as error:
                if not self.closed:
                    self.errors["reaction_collection"] = getattr(error, "code", type(error).__name__)
            finally:
                self.background.pop(key, None)

        self.background[key] = asyncio.create_task(prepare(), name="v2-native-reaction-prepare")

    def schedule_reaction_collection(self, batch_id, context, request=None, *, native_images=None, native_event=None):
        fields = self._reaction_collection_fields(context)
        if fields is None:
            return
        enabled_field, probability_field, source = fields
        if not self.reaction_collection_allowed(context, batch_id):
            return
        direct_sources = ({image.source for image in native_images} if native_images is not None else
            {material.source for material in request.context.materials
             if material.purpose.startswith("input_image:") and material.source is not None})
        original_event = native_event if native_event is not None else request.event
        if not direct_sources or not getattr(original_event, "images", ()):
            return
        owner = self.ensure_reaction_catalog(context)
        run_key = digest(["reaction-capture.v1", owner, batch_id])
        key = REACTION_CAPTURE_KEY + run_key
        now = time.monotonic()
        self.reaction_capture_runs = {key: at for key, at in self.reaction_capture_runs.items()
                                      if now - at < 600}
        if run_key in self.reaction_capture_runs or key in self.background:
            return
        try:
            images = (native_images if native_images is not None else
                      self.app.messages.images.read(context, (original_event,), self.app.now()))
        except Exception as error:
            self.errors["reaction_collection"] = getattr(error, "code", type(error).__name__)
            self._record_reaction_collection(owner, run_key, source, "failed",
                                             code=getattr(error, "code", type(error).__name__))
            return
        images = tuple(image for image in images if image.source in direct_sources)
        if not images:
            return
        self.reaction_capture_runs[run_key] = now
        if len(self.reaction_capture_runs) > 512:
            oldest = min(self.reaction_capture_runs, key=self.reaction_capture_runs.get)
            self.reaction_capture_runs.pop(oldest, None)
        index = int(digest(["reaction-capture-image.v1", owner, batch_id])[:16], 16) % len(images)
        try:
            candidate = self.reactions.capture_prompt_image(images[index])
        except Exception as error:
            self.errors["reaction_collection"] = getattr(error, "code", type(error).__name__)
            self._record_reaction_collection(owner, run_key, source, "failed",
                                             code=getattr(error, "code", type(error).__name__))
            return
        self._record_reaction_collection(owner, run_key, source, "checking")
        def validate_capture():
            self.check(context)
            current = self.store.settings()
            if self.closed or not current["enabled"] or not current[enabled_field]:
                raise TaskError("reaction_capture_disabled", "automatic reaction collection was disabled")
            if native_event is not None:
                from companion_v2.conversation.native_tool_sources import native_input_images
                native_input_images(self.app.messages, context, batch_id)

        async def collect():
            try:
                status, item = await self.reactions.collect_candidate(owner, candidate, source, validate_capture)
                self._record_reaction_collection(owner, run_key, source, status,
                                                 item_id=item["id"] if item else None)
                self.errors.pop("reaction_collection", None)
            except TaskError as error:
                if error.code == "reaction_capture_disabled" or self.closed:
                    self._record_reaction_collection(owner, run_key, source, "cancelled", code=error.code)
                else:
                    self.errors["reaction_collection"] = error.code
                    self._record_reaction_collection(owner, run_key, source, "failed", code=error.code)
            except Exception as error:
                if not self.closed:
                    code = getattr(error, "code", type(error).__name__)
                    self.errors["reaction_collection"] = code
                    self._record_reaction_collection(owner, run_key, source, "failed", code=code)
            finally:
                self.background.pop(key, None)

        self.background[key] = asyncio.create_task(collect(), name="v2-reaction-collection")

    def _record_reaction_collection(self, owner, run_key, scope, status, *, code=None, item_id=None):
        try:
            self.store.record_reaction_collection(owner, run_key, scope, status,
                at=self.app.now().timestamp(), code=code, item_id=item_id)
        except Exception as error:
            self.errors["reaction_collection_log"] = getattr(error, "code", type(error).__name__)

    @property
    def active_handles(self):
        return tuple("semantic:" + k for k,t in self.background.items() if not t.done())

    async def close(self):
        self.closed = True
        tasks = tuple(self.background.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.reactions.clear_lookups()
        self.news_context.close()
        self.gateway.clear()

    async def configure(self, changes, revision):
        result = self.store.configure(changes, revision)
        tasks = tuple(self.background.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.gateway.clear()
        self.gateway.timeout = result["timeout_seconds"]
        self.memory.continuations.clear()
        # A settings change changes the lookup identity; drop both the cached
        # results and the backfill throttle so the new interval applies at once.
        self.reactions.clear_lookups()
        self.backfill_runs.clear()
        return result

    def status(self):
        cfg = self.store.settings()
        try:
            binding = self.gateway.port.binding(cfg["embedding_provider"], "embedding")
            state = "ready" if cfg["enabled"] else "disabled"
        except Exception:
            binding, state = None, "lexical_fallback" if cfg["enabled"] else "disabled"
        return {"state": state, "settings": cfg, "model_binding": binding,
                "metrics": dict(self.gateway.metrics), "errors": dict(self.errors),
                "background_jobs": len(self.active_handles), "providers": self.gateway.port.inventory()}
