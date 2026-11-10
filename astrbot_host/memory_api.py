"""Authenticated memory administration for the explicitly selected host profile."""

import json
from uuid import uuid4

from astrbot.api.web import request
from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome
from companion_v2.memory.archive import MAX_BUNDLE_BYTES, MemoryArchive
from companion_v2.memory.management import MemoryManagement
from companion_v2.memory.models import AtomRef
from companion_v2.memory.query import MemoryQuery
from companion_v2.memory import codec
from companion_v2.memory.reflection_management import ReflectionManagement
from .management_identity import dashboard_operator
from .memory_linkage import read_external_memory_summary


PREFIX = "/astrbot_plugin_private_companion/memory"


class MemoryWebAPI:
    def __init__(self, controller):
        self.controller = controller

    def routes(self):
        return [(PREFIX, self.list, ["GET"], "Read V2 memory facts"),
                (PREFIX + "/add", self.add, ["POST"], "Add one manually authored memory fact"),
                (PREFIX + "/command", self.command, ["POST"], "Correct, pin or forget V2 memory facts"),
                (PREFIX + "/revisions", self.revisions, ["GET"], "Review authorized memory fact revisions"),
                (PREFIX + "/clear/preview", self.preview_clear, ["GET"], "Preview clearing this private relationship's facts"),
                (PREFIX + "/clear", self.clear, ["POST"], "Clear the previewed private fact archive"),
                (PREFIX + "/reflections", self.reflections, ["GET"], "Inspect pending memory extraction and repair status"),
                (PREFIX + "/reflections/retry", self.retry_reflection, ["POST"], "Repair a known failed memory extraction"),
                (PREFIX + "/reflections/queue", self.queue_reflection, ["POST"], "Continue one requested memory reflection in the background"),
                (PREFIX + "/overview", self.overview, ["GET"], "Read authorized memory and linkage counts"),
                (PREFIX + "/settings", self.settings, ["GET"], "Read unified V2 memory configuration"),
                (PREFIX + "/settings/command", self.settings_command, ["POST"], "Update unified V2 memory configuration"),
                (PREFIX + "/detail", self.detail, ["POST"], "Read one authorized fact with governed evidence handles"),
                (PREFIX + "/query", self.query, ["POST"], "Search authorized private facts, sources and experiences"),
                (PREFIX + "/source", self.read_source, ["POST"], "Read a bounded source span from an authorized memory query"),
                (PREFIX + "/archive/preview", self.archive_preview, ["GET"], "Preview cold-archiving long-idle durable facts"),
                (PREFIX + "/archive", self.archive_commit, ["POST"], "Confirm the previewed cold archive"),
                (PREFIX + "/archive/restore", self.archive_restore, ["POST"], "Return archived facts to the working set"),
                (PREFIX + "/export", self.memory_export, ["GET"], "Export this relationship's portable memory bundle"),
                (PREFIX + "/import/preview", self.import_preview, ["POST"], "Validate a portable bundle and show what would land"),
                (PREFIX + "/import", self.import_commit, ["POST"], "Import the previewed portable bundle")]

    def _reflection_manager(self):
        controller = self.controller
        if controller._terminated:
            raise TaskError("staging_host_terminated", "plugin is stopped")
        username = request.username
        if type(username) is not str or not username.strip():
            raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
        session = controller.session or controller.last_session
        if session is None or controller.runtime is None:
            raise TaskError("staging_not_initialized", "load the explicit V2 profile first")
        operator = dashboard_operator(controller.runtime, username, at=controller.now())
        return ReflectionManagement(session.application, operator), session.profile.target

    async def reflections(self):
        from .web_api import outcome_response
        try:
            query = request.query
            if (set(query.keys()) - {"limit", "batch_id", "after_id", "history"} or ("batch_id" in query and "after_id" in query)
                    or any(len(query.getlist(k)) != 1 for k in query.keys())):
                raise ValueError()
            history_value = query.get("history", "0")
            if history_value not in ("0", "1"):
                raise ValueError()
            async with self.controller._control_lock:
                manager, context = self._reflection_manager()
                return outcome_response(manager.list(context, batch_id=query.get("batch_id"), limit=int(query.get("limit", "20")),
                                                     after_id=query.get("after_id", ""), history=history_value == "1"))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError):
            return outcome_response(Outcome.failed("memory_request_invalid", "invalid reflection query"))

    async def overview(self):
        from .web_api import outcome_response
        try:
            async with self.controller._control_lock:
                manager, context = self._reflection_manager()
                local = manager.manager.overview(context)
            if not local.is_success:
                return outcome_response(local)
            linked = await read_external_memory_summary(self.controller, context)
            return outcome_response(Outcome.success({"local": local.unwrap(), "linkage": linked}))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))

    async def retry_reflection(self):
        return await self._submit_reflection(background=False)

    async def queue_reflection(self):
        return await self._submit_reflection(background=True)

    async def _submit_reflection(self, *, background):
        from .web_api import outcome_response
        try:
            raw = await request.body()
            if len(raw) > 4096 or request.query:
                raise ValueError()
            def unique(pairs):
                values = {}
                for key, value in pairs:
                    if key in values:
                        raise ValueError()
                    values[key] = value
                return values
            arguments = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
            if type(arguments) is not dict or set(arguments) != {"batch_id", "operation_key"}:
                raise ValueError()
            async with self.controller._control_lock:
                manager, context = self._reflection_manager()
                if background:
                    return outcome_response(manager.enqueue(context, **arguments))
                prepared = manager.prepare_retry(context, **arguments)
                if not prepared.is_success:
                    return outcome_response(prepared)
            # Stop, permission changes and source withdrawal remain responsive.
            return outcome_response(await manager.run(context, prepared.unwrap()["batch_id"]))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("memory_request_invalid", "invalid reflection repair command"))

    async def _run(self, arguments, mutation=False, method=None):
        from .web_api import outcome_response
        controller = self.controller
        async with controller._control_lock:
            try:
                if controller._terminated:
                    raise TaskError("staging_host_terminated", "plugin is stopped")
                username = request.username
                if type(username) is not str or not username.strip():
                    raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
                session = controller.session or controller.last_session
                if session is None or controller.runtime is None:
                    raise TaskError("staging_not_initialized", "load the explicit V2 profile before managing memory")
                operator = dashboard_operator(controller.runtime, username, at=controller.now())
                manager = MemoryManagement(session.application.memory, operator)
                selected = getattr(manager, method) if method else manager.command if mutation else manager.list
                result = selected(session.profile.target, **arguments)
                if mutation and result.is_success:
                    session.application.notify()
                return outcome_response(result)
            except TaskError as error:
                return outcome_response(Outcome.failed(error.code, str(error)))
            except (TypeError, ValueError, KeyError):
                return outcome_response(Outcome.failed("memory_request_invalid", "invalid memory management arguments"))

    async def _run_archive(self, arguments, method, *, mutation=False):
        """Archive/portability share _run's identity rules but take a context."""
        from .web_api import outcome_response
        controller = self.controller
        async with controller._control_lock:
            try:
                if controller._terminated:
                    raise TaskError("staging_host_terminated", "plugin is stopped")
                username = request.username
                if type(username) is not str or not username.strip():
                    raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
                session = controller.session or controller.last_session
                if session is None or controller.runtime is None:
                    raise TaskError("staging_not_initialized", "load the explicit V2 profile before managing memory")
                operator = dashboard_operator(controller.runtime, username, at=controller.now())
                archive = MemoryArchive(session.application.memory, operator)
                result = getattr(archive, method)(context=session.profile.target, **arguments)
                if mutation and result.is_success:
                    session.application.notify()
                return outcome_response(result)
            except TaskError as error:
                return outcome_response(Outcome.failed(error.code, str(error)))
            except (TypeError, ValueError, KeyError):
                return outcome_response(Outcome.failed("memory_request_invalid", "invalid memory archive arguments"))

    @staticmethod
    def _bounded_body(raw, allowed):
        def unique(pairs):
            values = {}
            for key, value in pairs:
                if key in values:
                    raise ValueError()
                values[key] = value
            return values
        arguments = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
        if type(arguments) is not dict or set(arguments) != allowed:
            raise ValueError()
        return arguments

    async def archive_preview(self):
        from .web_api import outcome_response
        keys = set(request.query.keys())
        if keys - {"idle_days"} or any(len(request.query.getlist(key)) != 1 for key in keys):
            return outcome_response(Outcome.failed("memory_request_invalid", "invalid archive preview query"))
        try:
            arguments = {"idle_days": int(request.query.get("idle_days", "180"))}
        except ValueError:
            return outcome_response(Outcome.failed("memory_request_invalid", "invalid idle window"))
        return await self._run_archive(arguments, "preview")

    async def archive_commit(self):
        from .web_api import outcome_response
        try:
            raw = await request.body()
            if len(raw) > 4096 or request.query:
                raise ValueError()
            arguments = self._bounded_body(raw, {"expected_archive_token", "operation_key", "idle_days"})
            arguments["idle_days"] = int(arguments["idle_days"])
        except (ValueError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("memory_request_invalid", "invalid archive command"))
        return await self._run_archive(arguments, "commit", mutation=True)

    async def archive_restore(self):
        from .web_api import outcome_response
        try:
            raw = await request.body()
            if len(raw) > 65536 or request.query:
                raise ValueError()
            arguments = self._bounded_body(raw, {"atom_ids", "operation_key"})
            if type(arguments["atom_ids"]) is not list:
                raise ValueError()
            arguments["atom_ids"] = tuple(arguments["atom_ids"])
        except (ValueError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("memory_request_invalid", "invalid archive restore command"))
        return await self._run_archive(arguments, "restore", mutation=True)

    async def memory_export(self):
        from .web_api import outcome_response
        keys = set(request.query.keys())
        if keys - {"include_archived"} or any(len(request.query.getlist(key)) != 1 for key in keys):
            return outcome_response(Outcome.failed("memory_request_invalid", "invalid export query"))
        include = request.query.get("include_archived", "true")
        if include not in ("true", "false"):
            return outcome_response(Outcome.failed("memory_request_invalid", "include_archived must be true or false"))
        return await self._run_archive({"include_archived": include == "true"}, "export")

    async def import_preview(self):
        from .web_api import outcome_response
        try:
            raw = await request.body()
            if len(raw) > MAX_BUNDLE_BYTES + 4096 or request.query:
                raise ValueError()
            arguments = self._bounded_body(raw, {"bundle"})
        except (ValueError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("memory_request_invalid", "invalid import preview request"))
        return await self._run_archive(arguments, "preview_import")

    async def import_commit(self):
        from .web_api import outcome_response
        try:
            raw = await request.body()
            if len(raw) > MAX_BUNDLE_BYTES + 8192 or request.query:
                raise ValueError()
            arguments = self._bounded_body(raw,
                                           {"bundle", "expected_import_token", "operation_key"})
        except (ValueError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("memory_request_invalid", "invalid import command"))
        return await self._run_archive(arguments, "commit_import", mutation=True)

    async def list(self):
        from .web_api import outcome_response
        if set(request.query.keys()) - {"after_id", "limit"} or any(len(request.query.getlist(key)) != 1 for key in request.query.keys()):
            return outcome_response(Outcome.failed("memory_request_invalid", "invalid memory query"))
        try:
            limit = int(request.query.get("limit", "20"))
        except ValueError:
            return outcome_response(Outcome.failed("memory_request_invalid", "invalid memory page size"))
        return await self._run({"after_id": request.query.get("after_id", ""), "limit": limit})

    async def add(self):
        from .web_api import outcome_response
        try:
            raw = await request.body()
            if len(raw) > 16384 or request.query:
                raise ValueError()
            def unique(pairs):
                values = {}
                for key, value in pairs:
                    if key in values:
                        raise ValueError()
                    values[key] = value
                return values
            arguments = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
            required = {"operation_key", "subject", "predicate", "object"}
            allowed = required | {"representation"}
            if (type(arguments) is not dict or not required.issubset(arguments)
                    or set(arguments) - allowed):
                raise ValueError()
            arguments.setdefault("representation", "")
        except (ValueError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("memory_request_invalid", "invalid memory add command"))
        return await self._run(arguments, mutation=True, method="add")

    async def command(self):
        from .web_api import outcome_response
        try:
            raw = await request.body()
            if len(raw) > 65536:
                raise ValueError()
            def unique(pairs):
                values = {}
                for key, value in pairs:
                    if key in values:
                        raise ValueError()
                    values[key] = value
                return values
            arguments = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
            required = {"atom_id", "expected_revision", "view_token", "operation_key", "action"}
            if type(arguments) is not dict or not required.issubset(arguments) or set(arguments) - required - {"value"}:
                raise ValueError()
        except (ValueError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("memory_request_invalid", "invalid memory command"))
        return await self._run(arguments, mutation=True)

    async def revisions(self):
        from .web_api import outcome_response
        keys = set(request.query.keys())
        if (not {"atom_id"}.issubset(keys) or keys - {"atom_id", "after_revision", "limit"}
                or any(len(request.query.getlist(key)) != 1 for key in keys)):
            return outcome_response(Outcome.failed("memory_request_invalid", "invalid revision query"))
        try:
            arguments = {
                "atom_id": request.query["atom_id"],
                "after_revision": int(request.query.get("after_revision", "0")),
                "limit": int(request.query.get("limit", "32")),
            }
        except ValueError:
            return outcome_response(Outcome.failed("memory_request_invalid", "invalid revision page"))
        return await self._run(arguments, method="revisions")

    async def preview_clear(self):
        from .web_api import outcome_response
        if request.query:
            return outcome_response(Outcome.failed("memory_request_invalid", "clear target is supplied by the host"))
        return await self._run({}, method="preview_clear")

    async def clear(self):
        from .web_api import outcome_response
        try:
            raw = await request.body()
            if len(raw) > 4096 or request.query:
                raise ValueError()
            def unique(pairs):
                values = {}
                for key, value in pairs:
                    if key in values:
                        raise ValueError()
                    values[key] = value
                return values
            arguments = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
            if type(arguments) is not dict or set(arguments) != {"operation_key", "expected_revision_token"}:
                raise ValueError()
        except (ValueError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("memory_request_invalid", "invalid memory clear command"))
        return await self._run(arguments, mutation=True, method="clear")

    async def settings(self):
        from .web_api import outcome_response
        query = request.query
        if (set(query.keys()) - {"layer"}
                or "layer" in query and len(query.getlist("layer")) != 1):
            return outcome_response(Outcome.failed("memory_request_invalid", "settings target is supplied by the host"))
        layer = query.get("layer", "role")
        if layer not in ("role", "window"):
            return outcome_response(Outcome.failed("memory_request_invalid", "invalid memory settings layer"))
        return await self._run({"layer": layer}, method="settings")

    async def settings_command(self):
        from .web_api import outcome_response
        try:
            raw = await request.body()
            if len(raw) > 16384:
                raise ValueError()
            def unique(pairs):
                value = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError()
                    value[key] = item
                return value
            arguments = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
            required = {"operation_key", "expected_edit_token", "changes"}
            allowed = required | {"reset_to_inherit", "layer"}
            if (type(arguments) is not dict or not required.issubset(arguments) or set(arguments) - allowed
                    or arguments.get("layer", "role") not in ("role", "window")):
                raise ValueError()
        except (ValueError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("memory_request_invalid", "invalid memory configuration patch"))
        return await self._run(arguments, mutation=True, method="patch_settings")

    async def query(self):
        """Search one authenticated private target; the browser cannot choose its subject or scope."""
        from .web_api import outcome_response
        try:
            raw = await request.body()
            if len(raw) > 8192 or request.query:
                raise ValueError()
            def unique(pairs):
                values = {}
                for key, value in pairs:
                    if key in values:
                        raise ValueError()
                    values[key] = value
                return values
            arguments = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
            required = {"query", "kind"}
            if (type(arguments) is not dict or not required.issubset(arguments)
                    or set(arguments) - required - {"session_id", "cursor"}):
                raise ValueError()
            query_text, kind = arguments["query"], arguments["kind"]
            if type(query_text) is not str or len(query_text) > 2000 or not query_text.strip():
                raise ValueError()
            if kind not in ("facts", "sources", "experiences", "all"):
                raise ValueError()
            session_id = arguments.get("session_id")
            cursor = arguments.get("cursor")
            if session_id is not None and (type(session_id) is not str or not session_id.startswith("memory-page-query-") or len(session_id) > 128):
                raise ValueError()
            if cursor is not None and (type(cursor) is not str or not cursor.startswith("memory-page-") or len(cursor) > 128):
                raise ValueError()
            if cursor is not None and session_id is None:
                raise ValueError()
            async with self.controller._control_lock:
                reflection_manager, context = self._reflection_manager()
                session = self.controller.session or self.controller.last_session
                memory = session.application.memory
                engine = MemoryQuery(reflection_manager.manager.writer)
                engine.access.check(context, "memory.read")
                session_id = session_id or "memory-page-query-" + uuid4().hex
                if cursor is None:
                    engine.begin(context, session_id)
                result = engine.query(context, session_id, query_intent=query_text, kind=kind, cursor=cursor)
                if result.failure:
                    return outcome_response(result)
                return outcome_response(Outcome.success({"session_id": session_id, **result.unwrap()}))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("memory_request_invalid", "invalid private memory query"))

    async def detail(self):
        """Return one current fact and opaque source handles, never caller-chosen evidence."""
        from .web_api import outcome_response
        try:
            raw = await request.body()
            if len(raw) > 4096 or request.query:
                raise ValueError()
            def unique(pairs):
                values = {}
                for key, value in pairs:
                    if key in values:
                        raise ValueError()
                    values[key] = value
                return values
            arguments = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
            if type(arguments) is not dict or set(arguments) != {"atom_id", "revision"}:
                raise ValueError()
            atom_id, revision = arguments["atom_id"], arguments["revision"]
            if type(atom_id) is not str or not atom_id.strip() or len(atom_id) > 512 or type(revision) is not int or revision < 1:
                raise ValueError()
            async with self.controller._control_lock:
                reflection_manager, context = self._reflection_manager()
                session = self.controller.session or self.controller.last_session
                management = reflection_manager.manager
                item = management.writer.read(context, AtomRef(atom_id, revision))
                if item.failure:
                    return outcome_response(item)
                value = item.unwrap()
                engine = MemoryQuery(management.writer)
                session_id = "memory-page-query-" + uuid4().hex
                engine.begin(context, session_id)
                sources = []
                for raw_evidence in value.pop("evidence", []):
                    evidence = codec.evidence(raw_evidence)
                    management.writer.access.evidence(context, evidence)
                    sources.append({"handle": engine.offer(context, session_id, evidence),
                                    "source_kind": evidence.source_kind, "speaker_id": evidence.speaker_id,
                                    "start": evidence.start, "end": evidence.end})
                value.pop("source", None)
                return outcome_response(Outcome.success({"item": value, "sources": sources, "session_id": session_id}))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("memory_request_invalid", "invalid fact detail request"))

    async def read_source(self):
        """Read source text only through a live query handle tied to the current private context."""
        from .web_api import outcome_response
        try:
            raw = await request.body()
            if len(raw) > 4096 or request.query:
                raise ValueError()
            def unique(pairs):
                values = {}
                for key, value in pairs:
                    if key in values:
                        raise ValueError()
                    values[key] = value
                return values
            arguments = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
            required = {"session_id", "handle"}
            if (type(arguments) is not dict or not required.issubset(arguments)
                    or set(arguments) - required - {"start", "max_chars"}):
                raise ValueError()
            session_id, handle = arguments["session_id"], arguments["handle"]
            if (type(session_id) is not str or not session_id.startswith("memory-page-query-") or len(session_id) > 128
                    or type(handle) is not str or len(handle) > 512):
                raise ValueError()
            start, maximum = arguments.get("start"), arguments.get("max_chars")
            if start is not None and (type(start) is not int or start < 0):
                raise ValueError()
            if maximum is not None and (type(maximum) is not int or not 1 <= maximum <= 24000):
                raise ValueError()
            async with self.controller._control_lock:
                reflection_manager, context = self._reflection_manager()
                session = self.controller.session or self.controller.last_session
                engine = MemoryQuery(reflection_manager.manager.writer)
                engine.access.check(context, "memory.read")
                result = engine.source(context, session_id, handle=handle, start=start, max_chars=maximum)
                return outcome_response(result)
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("memory_request_invalid", "invalid authorized source request"))
