"""Authenticated V2 semantic settings and owned reaction/vocabulary catalog."""

import asyncio
import base64
import binascii
from collections import OrderedDict
import io
import json
import logging
import sqlite3
import threading
from PIL import Image
from astrbot.api.web import request
from companion_v2.conversation.media.models import MAX_IMAGE_BYTES
from companion_v2.execution.models import TaskContext, TaskError, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind, ScopeRef
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from .group_settings import GroupTarget
from .management_identity import dashboard_operator
from .media import inspect_image

PREFIX = "/astrbot_plugin_private_companion/semantic"
FIELDS = ("name", "description", "visible_text", "tags", "emotions", "intents")
logger = logging.getLogger(__name__)


class SemanticWebAPI:
    def __init__(self, controller):
        self.controller = controller
        self._preview_cache = OrderedDict()
        self._preview_cache_lock = threading.Lock()

    def routes(self):
        return [(PREFIX, self.get, ["GET"], "Read semantic catalog and actual model status"),
                (PREFIX, self.post, ["POST"], "Manage semantic models, reactions and group vocabulary")]

    def group_sessions(self, target, role_id, service):
        rows = self.controller.runtime.read_all("""SELECT context_json FROM conversation_batches
            WHERE json_extract(context_json,'$.role_binding.scope.native_scope_id')=?
            AND json_extract(context_json,'$.role_binding.scope.platform')=?
            AND json_extract(context_json,'$.role_binding.scope.bot_id')=?
            AND json_extract(context_json,'$.role_binding.scope.account_id')=?
            AND json_extract(context_json,'$.role_binding.role.subject_id')=?
            ORDER BY rowid DESC LIMIT 1000""",
            (target.group_id, target.platform_id, target.bot_id, target.account_id, role_id))
        sessions = {}
        for row in rows:
            candidate = TaskContext.restore(json.loads(row["context_json"]))
            if candidate.scope.kind is not ScopeKind.GROUP:
                continue
            session_id = candidate.scope.session_id
            if session_id in sessions:
                continue
            try:
                service.slang.memory.access.check(candidate)
                service.check(candidate)
            except TaskError:
                continue
            sessions[session_id] = candidate
            if len(sessions) == 50:
                break
        return sessions

    def group_context(self, target, role_id, session_id, service):
        rows = self.controller.runtime.read_all("""SELECT context_json FROM conversation_batches
            WHERE json_extract(context_json,'$.role_binding.scope.native_scope_id')=?
            AND json_extract(context_json,'$.role_binding.scope.platform')=?
            AND json_extract(context_json,'$.role_binding.scope.bot_id')=?
            AND json_extract(context_json,'$.role_binding.scope.account_id')=?
            AND json_extract(context_json,'$.role_binding.role.subject_id')=?
            AND json_extract(context_json,'$.role_binding.scope.session_id')=?
            ORDER BY rowid DESC LIMIT 100""",
            (target.group_id, target.platform_id, target.bot_id, target.account_id, role_id, session_id))
        for row in rows:
            candidate = TaskContext.restore(json.loads(row["context_json"]))
            if candidate.scope.kind is not ScopeKind.GROUP:
                continue
            try:
                service.slang.memory.access.check(candidate)
                service.check(candidate)
            except TaskError:
                continue
            return candidate
        return None

    def resolve(self, selection, *, username=None):
        c = self.controller
        if c._terminated:
            raise TaskError("staging_host_terminated", "plugin is stopped")
        resolved_username = request.username if username is None else username
        if type(resolved_username) is not str or not resolved_username.strip() or resolved_username.startswith("api_key:"):
            raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
        session = c.session or c.last_session
        if session is None or c.runtime is None or c.settings is None:
            raise TaskError("staging_not_initialized", "load the V2 profile first")
        operator = dashboard_operator(c.runtime, resolved_username, at=c.now())
        require(ObjectAccessPolicy(c.runtime).feature_access(operator,
            scope=ScopeRef(c.runtime.installation_id, "companion", ScopeKind.SYSTEM, "page.management"),
            action="memory.manage", observed_at=c.now()))
        service = session.application.semantics
        if service is None or service.closed:
            raise TaskError("semantic_stopped", "start V2 to manage semantic resources")
        global_value = selection.get("global")
        is_global = global_value is True or (type(global_value) is str and global_value.lower() in {"1", "true"})
        if is_global:
            if set(selection) != {"global"}:
                raise ValueError("global semantic settings cannot include a chat selection")
            return service, None
        target_id, role_id = selection.get("target_id"), selection.get("role_id")
        if not all(type(v) is str and 0 < len(v) <= 200 for v in (target_id, role_id)):
            raise ValueError("select a configured target and role")
        context = None
        if hasattr(c.settings, "all_targets"):
            target = c.settings.target(target_id)
            if not any(p.bot_id == target.bot_id and p.role_id == role_id for p in c.settings.personas):
                raise TaskError("semantic_target_unavailable", "configured role is unavailable")
            if isinstance(target, GroupTarget):
                session_id = selection.get("session_id") or target.group_id
                if type(session_id) is not str or not 0 < len(session_id) <= 200:
                    raise ValueError("invalid group session")
                context = self.group_context(target, role_id, session_id, service)
            else:
                if selection.get("session_id"):
                    raise ValueError("private selection has no group session")
                context = c.authority.context(target_id, role_id)
        else:
            if (target_id,role_id)!=(c.settings.profile_id,c.settings.role_id) or selection.get("session_id"):
                raise TaskError("semantic_target_unavailable", "select the configured private target")
            context = session.profile.target
        if context:
            service.check(context)
        return service, context

    def role_reaction_owner(self, selection, service, context):
        """Return the role-owned reaction catalog key for a validated chat selection."""
        if context is not None:
            return service.ensure_reaction_catalog(context)
        target_id, role_id = selection.get("target_id"), selection.get("role_id")
        if not target_id or not role_id:
            return None
        settings = self.controller.settings
        if not hasattr(settings, "all_targets"):
            return None
        target = settings.target(target_id)
        if not isinstance(target, GroupTarget):
            return None
        if not any(p.bot_id == target.bot_id and p.role_id == role_id for p in settings.personas):
            return None
        return service.ensure_reaction_catalog_for_role(
            self.controller.runtime.installation_id, target.bot_id, role_id)

    def recheck(self, selection, service, context, *, reaction_owner=None, username=None):
        current, target = self.resolve(selection, username=username)
        if current is not service:
            raise TaskError("semantic_scope_changed", "target changed during this operation")
        if reaction_owner is not None:
            if self.role_reaction_owner(selection, current, target) != reaction_owner:
                raise TaskError("semantic_scope_changed", "target changed during this operation")
            if target:
                service.check(target)
            return
        if (service.owner(target) if target else None)!=(service.owner(context) if context else None):
            raise TaskError("semantic_scope_changed", "target changed during this operation")
        if context:
            service.check(context)

    @staticmethod
    def public(item):
        fields = ("id", "kind", "revision", "enabled", "payload")
        result = {key: item[key] for key in fields}
        if "vector_ready" in item:
            result["vector_ready"] = item["vector_ready"]
        return result

    def reaction_thumbnail(self, service, item):
        key = (item["owner"], item["id"])
        with self._preview_cache_lock:
            cached = self._preview_cache.get(key)
            if cached is not None:
                self._preview_cache.move_to_end(key)
                return cached
        try:
            captured = service.reactions.captured(item)
            frame = captured.frames[0] if getattr(captured, "frames", ()) else captured
            with Image.open(io.BytesIO(frame.content)) as source:
                source.seek(0)
                has_alpha = "A" in source.getbands() or "transparency" in source.info
                preview = source.convert("RGBA" if has_alpha else "RGB")
                preview.thumbnail((160, 160), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                if has_alpha:
                    preview.save(output, format="PNG", optimize=True)
                    mime_type = "image/png"
                else:
                    preview.save(output, format="JPEG", quality=78, optimize=True)
                    mime_type = "image/jpeg"
            value = "data:" + mime_type + ";base64," + base64.b64encode(output.getvalue()).decode("ascii")
        except Exception:
            return ""
        with self._preview_cache_lock:
            self._preview_cache[key] = value
            self._preview_cache.move_to_end(key)
            if len(self._preview_cache) > 96:
                self._preview_cache.popitem(last=False)
        return value

    def status(self, service, context, *, reaction_owner=None):
        result = service.status()
        effective_reaction_owner = service.ensure_reaction_catalog(context) if context else reaction_owner
        scope_kind = (context.scope.kind.value if context
                      else "group" if effective_reaction_owner is not None else "global")
        result.update(selection_ready=context is not None,
                      reaction_ready=effective_reaction_owner is not None,
                      scope_kind=scope_kind,
                      settings_scope="installation", catalog={})
        if effective_reaction_owner is not None or context is not None:
            slang_owner = service.owner(context) if context is not None else None
            with service.store.db() as db:
                for kind, owner in (("reaction", effective_reaction_owner), ("slang", slang_owner)):
                    if owner is None:
                        continue
                    counts = {str(r[0]):r[1] for r in db.execute("SELECT enabled,COUNT(*) FROM items WHERE owner=? AND kind=? GROUP BY enabled",(owner,kind))}
                    embedded = db.execute("""SELECT COUNT(*) FROM vectors v JOIN items i ON i.owner=v.owner AND i.kind=v.kind AND i.id=v.id
                        WHERE v.owner=? AND v.kind=? AND v.model=? AND v.revision=i.revision AND i.enabled=1""",(owner,kind,result["model_binding"])).fetchone()[0]
                    result["catalog"][kind] = {"enabled":counts.get("1",0),"disabled":counts.get("0",0),
                                               "total":sum(counts.values()),"embedded":embedded}
                    if kind == "reaction":
                        # Compute live pending/recognized/awaiting-confirmation counts across
                        # the whole catalog so progress never drifts from lifetime totals.
                        stats = db.execute(
                            """SELECT
                                 COALESCE(SUM(CASE WHEN json_extract(payload, '$.analysis')='pending' THEN 1 ELSE 0 END), 0),
                                 COALESCE(SUM(CASE WHEN json_extract(payload, '$.analysis') IN ('analyzed','auto_reviewed','manual','legacy_imported') THEN 1 ELSE 0 END), 0),
                                 COALESCE(SUM(CASE WHEN json_extract(payload, '$.collection_state')='awaiting_confirmation' AND enabled=0 THEN 1 ELSE 0 END), 0)
                               FROM items WHERE owner=? AND kind='reaction'""",
                            (owner,),
                        ).fetchone()
                        result["catalog"][kind]["pending"] = stats[0]
                        result["catalog"][kind]["recognized"] = stats[1]
                        result["catalog"][kind]["awaiting_confirmation"] = stats[2]
            result["reaction_collection"] = (service.store.reaction_collection_summary(effective_reaction_owner)
                                             if effective_reaction_owner is not None else {"totals": {}, "recent": []})
        return result

    def _get_sync(self, query_map, username):
        selection = {key: query_map[key] for key in ("target_id", "role_id", "session_id", "global") if key in query_map}
        service, context = self.resolve(selection, username=username)
        reaction_owner = self.role_reaction_owner(selection, service, context)
        result = self.status(service, context, reaction_owner=reaction_owner)
        if context is not None and hasattr(self.controller.settings, "all_targets"):
            # Global settings requests intentionally carry no target/role.
            # Resolve target-specific group sessions only for a chat scope.
            target = self.controller.settings.target(query_map["target_id"])
            if isinstance(target, GroupTarget):
                result["sessions"] = list(self.group_sessions(target, query_map["role_id"], service))
                result["session_id"] = query_map.get("session_id") or target.group_id
        kind = query_map.get("kind", "reaction")
        if kind not in ("reaction", "slang"):
            raise ValueError("unknown catalog")
        result.update(items=[], next_after=None)
        owner = reaction_owner if kind == "reaction" else (service.owner(context) if context else None)
        if owner is not None:
            if query_map.get("id"):
                item = service.store.get(owner, kind, query_map["id"])
                result["item"] = self.public(item)
                if kind == "reaction":
                    image = service.reactions.captured(item)
                    result["image_url"] = "data:" + image.descriptor.mime_type + ";base64," + base64.b64encode(image.content).decode("ascii")
            else:
                page, next_after = service.store.list_recent_page(owner, kind, after=query_map.get("after", ""), limit=24)
                with service.store.db() as db:
                    items = []
                    for item in page:
                        vector = db.execute("""SELECT 1 FROM vectors WHERE owner=? AND kind=? AND id=?
                            AND model=? AND revision=?""",
                            (owner, kind, item["id"], result["model_binding"], item["revision"])
                            if result["model_binding"] else (owner, kind, item["id"], "", item["revision"])).fetchone()
                        public_item = self.public({**item, "vector_ready": vector is not None})
                        if kind == "reaction":
                            public_item["thumbnail_url"] = self.reaction_thumbnail(service, item)
                        items.append({**public_item,
                            "source_valid": service.slang.validate(item, context) if kind == "slang" else True})
                result["items"] = items
                result["next_after"] = next_after
        return result

    async def get(self):
        try:
            username = request.username
            query = request.query
            if set(query.keys()) - {"target_id", "role_id", "session_id", "kind", "after", "id", "global"} or any(len(query.getlist(k)) != 1 for k in query.keys()):
                raise ValueError("unexpected or duplicate fields")
            query_map = {key: query[key] for key in query.keys()}
            result = await asyncio.to_thread(self._get_sync, query_map, username)
            return self.response(result)
        except (TaskError, ValueError, TypeError, KeyError) as error:
            return self.error(error)
        except sqlite3.Error as error:
            # The catalog is one SQLite file with a short busy timeout; a
            # background backfill can hold it past that. Report the real
            # condition instead of blaming the embedding model.
            logger.warning("[CompanionV2] 语义素材读取失败 error_type=%s", type(error).__name__)
            return self.error(TaskError("semantic_store_busy", "semantic catalog is busy"))
        except Exception as error:
            logger.warning("[CompanionV2] 语义素材读取异常 error_type=%s", type(error).__name__)
            return self.error(TaskError("semantic_operation_failed", "semantic catalog read did not complete"))

    def _prepare_post_sync(self, raw, username, action_box):
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate field")
                value[key] = item
            return value
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
        if type(data) is not dict:
            raise ValueError("JSON object required")
        action = data.get("action")
        action_box[0] = action
        fields = {"configure": ("changes", "revision"), "import": ("content", "metadata"), "analyze": ("id",),
                  "edit": ("id", "kind", "revision", "values", "enabled"), "delete": ("id", "kind", "revision"),
                  "search": ("kind", "query"), "reindex": ()}
        if action not in fields or set(data) - {"target_id", "role_id", "session_id", "global", "action", *fields[action]}:
            raise ValueError("unsupported action or fields")
        selection = {k: data[k] for k in ("target_id", "role_id", "session_id", "global") if k in data}
        service, context = self.resolve(selection, username=username)
        reaction_owner = self.role_reaction_owner(selection, service, context)
        return action, data, selection, service, context, reaction_owner

    def _import_sync(self, service, owner, data):
        metadata = data.get("metadata", {})
        if type(metadata) is not dict:
            raise ValueError("image and metadata required")
        if ("source_filename" in metadata and metadata["source_filename"] is not None
                and type(metadata["source_filename"]) is not str):
            raise ValueError("invalid source filename")
        raw_content = data.get("content")
        if raw_content is None:
            raise TaskError("host_image_invalid", "image content is empty")
        if type(raw_content) is not str:
            raise ValueError("image and metadata required")
        encoded = raw_content.strip()
        if not encoded:
            raise TaskError("host_image_invalid", "image content is empty")
        if len(encoded) > (MAX_IMAGE_BYTES + 2) // 3 * 4:
            raise TaskError("host_image_limit", "image exceeds the intake byte budget")
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise TaskError("host_image_invalid", "image base64 content is invalid") from None
        if not decoded:
            raise TaskError("host_image_invalid", "image content is empty")
        image = inspect_image(decoded, 0)
        item, created = service.reactions.import_image_with_status(owner, image, metadata)
        return {"item": self.public(item), "created": created}

    def _edit_sync(self, service, owner, context, data):
        kind, values = data["kind"], data["values"]
        allowed = FIELDS if kind == "reaction" else ("term", "meaning", "usage") if kind == "slang" else ()
        if (not allowed or type(values) is not dict or set(values) - set(allowed) or type(data["enabled"]) is not bool
                or any(type(v) is not str or len(v) > 1500 for v in values.values()) or type(data["revision"]) is not int):
            raise ValueError("invalid metadata fields")
        item = service.store.get(owner, kind, data["id"])
        payload = {**item["payload"], **values}
        if kind == "slang":
            if context.scope.kind is not ScopeKind.GROUP or not service.slang.validate(item, context):
                raise TaskError("slang_evidence_unavailable", "original group evidence is unavailable")
            if payload["term"] != item["payload"]["term"] or not payload["meaning"].strip() or len(payload["meaning"]) > 500 or len(payload["usage"]) > 500:
                raise ValueError("revise the bounded meaning of this exact term")
            payload["source"] = "manual"
        else:
            # Saving an editor with untouched empty fields must not mark
            # those fields as human-authored. Otherwise a user who saves
            # before pressing "complete blank fields" permanently pins
            # every empty field and the vision model can never fill it.
            changed_fields = {name for name, value in values.items()
                              if value != item["payload"].get(name, "")}
            if changed_fields and (payload.get("description", "").strip()
                                   or payload.get("analysis") != "pending"):
                payload["analysis"] = "manual"
            payload["manual_fields"] = sorted(set(payload.get("manual_fields", ())) | changed_fields)
            if data["enabled"] and payload.get("collection_state") == "awaiting_confirmation":
                payload["collection_state"] = "confirmed"
        payload["text"] = "；".join(payload.get(k, "") for k in allowed)
        return {"item": self.public(service.store.put(owner, kind, item["id"], payload, expected=data["revision"], enabled=data["enabled"]))}

    @staticmethod
    def _clear_reindex_sync(service, context):
        reaction_owner = service.ensure_reaction_catalog(context)
        slang_owner = service.owner(context)
        with service.store.db() as db:
            db.execute("DELETE FROM vectors WHERE owner=? AND kind='reaction'", (reaction_owner,))
            db.execute("DELETE FROM vectors WHERE owner=? AND kind='slang'", (slang_owner,))
        service.gateway.clear()

    async def post(self):
        action_box = [""]
        try:
            username = request.username
            raw = await request.body()
            if request.query or len(raw) > 7 * 1024 * 1024:
                raise ValueError("bounded JSON body required")
            action, data, selection, service, context, reaction_owner = await asyncio.to_thread(
                self._prepare_post_sync, raw, username, action_box
            )
            if action == "configure":
                result = await service.configure(data["changes"], data["revision"])
                await asyncio.to_thread(self.recheck, selection, service, context, username=username)
                return self.response({"settings": result})
            is_reaction_action = (action in ("import", "analyze")
                                  or (action in ("edit", "delete", "search") and data.get("kind") == "reaction"))
            if context is None and not (is_reaction_action and reaction_owner is not None):
                settings = self.controller.settings
                target_id = selection.get("target_id")
                if (target_id and hasattr(settings, "all_targets")
                        and isinstance(settings.target(target_id), GroupTarget)):
                    raise TaskError("semantic_group_input_required",
                                    "the selected group session has no verified member input")
                raise TaskError("semantic_target_unavailable", "select a chat scope for semantic catalog data")
            owner = reaction_owner if is_reaction_action else service.owner(context)
            validate = lambda: self.recheck(selection, service, context,
                                            reaction_owner=reaction_owner if is_reaction_action else None,
                                            username=username)
            if action == "import":
                result = await asyncio.to_thread(self._import_sync, service, owner, data)
            elif action == "analyze":
                result = {"item": self.public(await service.reactions.analyze(owner, data["id"], validate))}
            elif action == "edit":
                result = await asyncio.to_thread(self._edit_sync, service, owner, context, data)
            elif action == "search":
                kind, text = data["kind"], data["query"]
                if kind not in ("reaction", "slang") or type(text) is not str or not 0 < len(text.strip()) <= 2000:
                    raise ValueError("bounded search query required")
                if kind == "slang" and context.scope.kind is not ScopeKind.GROUP:
                    raise ValueError("group vocabulary requires a group")
                result = await service.search.search(owner, kind, text, context=context)
                await asyncio.to_thread(validate)
            elif action == "delete":
                if (data["kind"] != "reaction" or type(data["id"]) is not str or not data["id"]
                        or type(data["revision"]) is not int):
                    raise ValueError("reaction ID and revision required")
                def _delete_sync():
                    validate()
                    return service.store.delete(owner, "reaction", data["id"], expected=data["revision"])
                result = await asyncio.to_thread(_delete_sync)
            else:
                await asyncio.to_thread(self._clear_reindex_sync, service, context)
                service.schedule_memory(context)
                result = {"state": "scheduled", "note": "catalog rebuilds on search; private memory fills in background"}
            return self.response(result)
        except (TaskError, ValueError, TypeError, KeyError, UnicodeError, RecursionError) as error:
            return self.error(error)
        except sqlite3.Error as error:
            # Same short-timeout catalog as the read path. "语义模型调用失败"
            # made operators check the embedding provider even when the file was
            # merely locked by the vector backfill.
            logger.warning("[CompanionV2] 语义素材操作失败 action=%s error_type=%s",
                           action_box[0], type(error).__name__)
            return self.error(TaskError("semantic_store_busy", "semantic catalog is busy"))
        except Exception as error:
            logger.warning("[CompanionV2] 语义素材操作异常 action=%s error_type=%s",
                           action_box[0], type(error).__name__)
            return self.error(TaskError("semantic_operation_failed", "semantic catalog operation did not complete"))

    @staticmethod
    def response(value):
        from .web_api import outcome_response
        return outcome_response(Outcome.success(value))

    @staticmethod
    def error(error):
        from .web_api import outcome_response
        return outcome_response(Outcome.failed(getattr(error,"code","semantic_request_invalid"),
            str(error) if isinstance(error,TaskError) else "invalid semantic request"))
