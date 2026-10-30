"""Conversation tools that save role-authored work through native Content."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
import json

from companion_v2.execution.models import (Lane, SideEffect, TaskDefinition, TaskError,
    TaskRegistration, TaskResult, TaskSpec, digest, encode, require)
from companion_v2.execution.runner import ExactTaskRunner
from companion_v2.execution.service import outcome
from companion_v2.foundation.refs import ScopeKind
from companion_v2.model_gateway.models import ToolSpec

from . import authoring, authoring_service
from .authoring_tasks import AUTHORING_BUDGET_SECONDS, DRAFT_KIND
from .authoring_execution import AuthoringParent, LegacyAuthoringOperations


CREATE_ACTION = "content.authoring.create"
CONTINUE_ACTION = "content.authoring.continue"
ACTIONS = frozenset({CREATE_ACTION, CONTINUE_ACTION})
INSTRUCTION = (
    "仅当用户明确要求写作并留存作品时，才使用原生创作工具；日常讨论、构思或询问不等于创作授权。新建操作会把完整作品保存为 Content 作品；续写可用完整作品标题精确定位，读取当前固定版本并保存候选稿，不会自动采用、发布或发送。若同名作品不止一部，先询问用户选哪一部，不猜选。先依据工具回执说明结果；未知结果不换调用编号重试，也不声称保存成功。"
)


def _tool(action, description, properties, required):
    return ToolSpec(action, description, encode({
        "type": "object", "properties": properties, "required": required,
        "additionalProperties": False,
    }))


CREATE_TOOL = _tool(CREATE_ACTION,
    "按用户明确要求创作并通过 Content 保存一部新作品；未提供标题和类别时由角色拟定。",
    {"title": {"type": "string"}, "work_type": {"type": "string"}, "brief": {"type": "string"}},
    ["brief"])
CONTINUE_BY_ID_TOOL = _tool(CONTINUE_ACTION,
    "按 work_id 续写当前版本并保存为候选稿；不会改动当前正文或自动采用候选。",
    {"work_id": {"type": "string"}, "brief": {"type": "string"}}, ["work_id", "brief"])
CONTINUE_BY_TITLE_TOOL = _tool(CONTINUE_ACTION,
    "按作品完整标题精确查找并续写当前版本；只提供 work_title 或 work_id 其中一项。同名时不要猜选。",
    {"work_id": {"type": "string"}, "work_title": {"type": "string"}, "brief": {"type": "string"}},
    ["brief"])


@dataclass
class _Job:
    context: object
    request: object
    settings: object
    authoring_request: dict
    materials: dict[str, str]
    work_type: str = ""
    result: authoring.ContentDraft | None = None
    revalidate: object = None


class ContentAuthoringWorkflow:
    def __init__(self, runtime):
        self.runtime = runtime
        self.actions = ACTIONS
        self.tasks = None
        self.gateway = None
        self.runner = None
        self._jobs: dict[str, _Job] = {}
        self._closed = False
        # Writing is a child of an authorized private reply, with Content mutation
        # separately authorized by the native provider capability.
        self.registrations = (TaskRegistration(
            TaskDefinition(DRAFT_KIND, "content.authoring", "conversation.reply", Lane.BACKGROUND,
                side_effect=SideEffect.NONE, timeout_seconds=AUTHORING_BUDGET_SECONDS, max_attempts=1,
                concurrency=2, per_scope_concurrency=1), self.validate),)

    def attach(self, gateway, *, require_running=None):
        if self.gateway is not None or gateway.runtime is not self.runtime:
            raise ValueError("Content authoring must attach once to its original model gateway")
        self.gateway, self.tasks = gateway, gateway.tasks
        self._require_running = require_running
        self.runner = ExactTaskRunner(self.tasks, frozenset({DRAFT_KIND}), self._generate)

    def catalog_for(self, context, native_actions):
        if (self._closed or context.scope.kind is not ScopeKind.PRIVATE
                or not authoring_service.effective_settings(self.runtime, context).enabled):
            return ()
        available = set(native_actions)
        if self.gateway is None or DRAFT_KIND not in self.gateway.router.profiles:
            return ()
        tools = []
        if "content.work.create" in available:
            tools.append(CREATE_TOOL)
        if {"content.work.describe", "content.work.read", "content.work.save_candidate"}.issubset(available):
            tools.append(CONTINUE_BY_TITLE_TOOL if "content.work.list" in available else CONTINUE_BY_ID_TOOL)
        return tuple(tools)

    def instruction_for(self, context, native_actions):
        return INSTRUCTION if self.catalog_for(context, native_actions) else ""

    @outcome
    def validate(self, spec, at):
        job = self._jobs.get(spec.task_id)
        if (job is None or spec.kind != DRAFT_KIND or spec.request_ref != spec.task_id
                or spec.context != job.context or not authoring_service.effective_settings(
                    self.runtime, spec.context).enabled):
            raise TaskError("content_authoring_request_unavailable", "the original writing request is unavailable")
        if self._closed or self.tasks is None:
            raise TaskError("content_authoring_unavailable", "the writing service has closed")
        if job.revalidate is not None:
            job.revalidate()
        return True

    async def execute(self, run, call, model_call_id, request, external):
        if external is None:
            raise TaskError("content_authoring_unavailable", "the Content operation owner is unavailable")
        parent = AuthoringParent(run.task.spec.context, run.task.task_id, run.task.spec.deadline_at, run.validate)
        operations = LegacyAuthoringOperations(external, run, call, model_call_id)
        return await self._execute(parent, call, request, operations)

    async def execute_native(self, parent, call, request, operations):
        if not isinstance(parent, AuthoringParent) or not callable(parent.revalidate):
            raise TaskError("content_authoring_request_mismatch", "native writing requires its original operation")
        return await self._execute(parent, call, request, operations)

    async def _execute(self, parent, call, request, operations):
        parent.revalidate()
        if self._closed or self.gateway is None or self.runner is None or operations is None:
            raise TaskError("content_authoring_unavailable", "the writing service is not running")
        if parent.context.scope.kind is not ScopeKind.PRIVATE:
            raise TaskError("content_scope_unavailable", "native authoring is private-scope only")
        settings = authoring_service.effective_settings(self.runtime, parent.context)
        if not settings.enabled:
            raise TaskError("content_authoring_disabled", "native authoring is turned off for this role")
        arguments = json.loads(call.arguments_json)
        native = set(operations.actions)
        if call.action == CREATE_ACTION:
            if "content.work.create" not in native:
                raise TaskError("content_provider_unavailable", "the native Content create action is unavailable")
            work_type = arguments.get("work_type") or ""
            if type(work_type) is not str:
                raise TaskError("content_authoring_work_type", "the requested work type must be text")
            authoring_request = authoring_service.prepare(action="draft", brief=arguments["brief"])
            return await self._create(parent, call, request, operations, settings,
                                      authoring_request, arguments, work_type)
        if call.action == CONTINUE_ACTION:
            required = {"content.work.describe", "content.work.read", "content.work.save_candidate"}
            if not required.issubset(native):
                raise TaskError("content_provider_unavailable", "native Content continuation actions are unavailable")
            work_id, work_title = arguments.get("work_id", ""), arguments.get("work_title", "")
            if type(work_id) is not str or type(work_title) is not str:
                raise TaskError("content_authoring_target", "continuation needs one Content work identifier or title")
            work_id, work_title = work_id.strip(), work_title.strip()
            if bool(work_id) == bool(work_title):
                raise TaskError("content_authoring_target", "choose exactly one Content work identifier or title")
            if work_title and "content.work.list" not in native:
                raise TaskError("content_work_lookup_unavailable", "Content title lookup is not authorized")
            return await self._continue(parent, call, request, operations, settings, arguments)
        raise TaskError("tool_not_available", "native authoring action is unavailable")

    async def _create(self, parent, call, request, operations, settings, authoring_request, arguments,
                      work_type):
        job = self._job(parent, call, request, settings, authoring_request, {}, work_type=work_type)
        draft = await self._draft(parent, job)
        parent.revalidate()
        self._require_enabled(parent.context)
        title = (arguments.get("title") or "").strip() or draft.title
        work_type = (arguments.get("work_type") or "").strip() or draft.work_type
        summary, material = await operations.execute("content.work.create", {"title": title, "work_type": work_type,
             "chapters": [{"chapter_id": "chapter-1", "title": title, "text": draft.body}]}, step=1)
        receipt = self._receipt(summary, material, "create")
        return {"state": "succeeded", "effect": "committed", "action": "create",
                "work_id": receipt["work_id"], "version_id": receipt["version_id"],
                "title": title}

    async def _continue(self, parent, call, request, operations, settings, arguments):
        work_id = arguments.get("work_id", "").strip()
        next_step = 1
        if not work_id:
            work_id, next_step = await self._find_work_by_title(
                operations, arguments["work_title"].strip(), start_step=1)
        summary, material = await operations.execute("content.work.describe", {"work_id": work_id}, step=next_step)
        description = self._result(summary, material)
        if description.get("state") != "active":
            raise TaskError("content_work_unavailable", "the requested Content work is unavailable")
        base_version, base_revision = description["head_version"], description["revision"]
        work_type = description.get("work_type", "")
        if type(work_type) is not str:
            raise TaskError("content_work_invalid", "Content returned an invalid work type")
        chapters, _pages, save_step = await self._read_version(operations,
            work_id, base_version, start_step=next_step + 1)
        base_text = "\n\n".join(chapter["text"] for chapter in chapters)
        authoring_request = authoring_service.prepare(action="continue", brief=arguments["brief"],
                                                      base_version=base_version)
        materials = {"base-version": base_text}
        job = self._job(parent, call, request, settings, authoring_request, materials, work_type=work_type)
        draft = await self._draft(parent, job)
        parent.revalidate()
        self._require_enabled(parent.context)
        chapter_id = "continuation-" + digest([parent.task_id, call.call_id, base_version])[:20]
        candidate_chapters = chapters + ({"chapter_id": chapter_id, "title": "续写", "text": draft.body},)
        summary, material = await operations.execute("content.work.save_candidate", {"work_id": work_id,
             "base_revision": base_revision, "base_version": base_version,
             "chapters": candidate_chapters}, step=save_step)
        receipt = self._receipt(summary, material, "save_candidate", work_id=work_id)
        return {"state": "succeeded", "effect": "committed", "action": "save_candidate",
                "work_id": receipt["work_id"], "version_id": receipt["version_id"],
                "base_version": base_version, "adopted": False,
                "title": description.get("title", "")}

    async def _find_work_by_title(self, operations, title, *, start_step):
        after_id, step, matches, seen_cursors = "", start_step, {}, set()
        while True:
            summary, material = await operations.execute("content.work.list", {"after_id": after_id, "limit": 50}, step=step)
            page = self._result(summary, material)
            items = page.get("items")
            if type(items) is not list or len(items) > 50:
                raise TaskError("content_work_lookup_invalid", "Content returned an invalid work page")
            for item in items:
                if (type(item) is not dict or type(item.get("work_id")) is not str
                        or type(item.get("title")) is not str):
                    raise TaskError("content_work_lookup_invalid", "Content returned an invalid work summary")
                if item["title"] == title:
                    matches[item["work_id"]] = item
                    if len(matches) > 1:
                        raise TaskError("content_work_ambiguous", "multiple readable Content works have this exact title")
            next_after = page.get("next_after")
            if next_after is None:
                return authoring_service.select_work_id(title, list(matches.values())), step + 1
            if (type(next_after) is not str or not next_after or next_after <= after_id
                    or next_after in seen_cursors):
                raise TaskError("content_work_lookup_invalid", "Content catalogue pagination did not advance")
            seen_cursors.add(next_after)
            after_id, step = next_after, step + 1

    async def _read_version(self, operations, work_id, version_id, *, start_step):
        offset, step, total, chapters, pages = 0, start_step, None, [], []
        parts: dict[str, str] = {}
        chapter_titles: dict[str, str] = {}
        while True:
            summary, material = await operations.execute("content.work.read", {"work_id": work_id, "version_id": version_id,
                 "offset": offset, "limit": 4096}, step=step)
            page = self._result(summary, material)
            if ((page.get("work_id"), page.get("version_id"), page.get("offset"))
                    != (work_id, version_id, offset) or type(page.get("total_chars")) is not int
                    or page["total_chars"] < 0 or total is not None and page["total_chars"] != total):
                raise TaskError("content_version_changed", "Content did not return the requested fixed version")
            total = page["total_chars"]
            pages.append(material)
            cursor = offset
            for segment in page.get("segments", ()):
                if type(segment) is not dict:
                    raise TaskError("content_page_invalid", "Content returned an invalid chapter segment")
                chapter_id = segment.get("chapter_id")
                chapter_title = segment.get("chapter_title", "")
                if (not isinstance(chapter_id, str) or not chapter_id
                        or type(chapter_title) is not str
                        or type(segment.get("text")) is not str
                        or type(segment.get("chapter_offset")) is not int
                        or segment.get("start") != cursor
                        or type(segment.get("end")) is not int
                        or segment["end"] - cursor != len(segment["text"])):
                    raise TaskError("content_page_invalid", "Content chapter ranges are discontinuous")
                if chapter_id not in parts:
                    chapters.append({"chapter_id": chapter_id, "title": chapter_title})
                    parts[chapter_id] = ""
                    chapter_titles[chapter_id] = chapter_title
                elif chapter_titles[chapter_id] != chapter_title:
                    raise TaskError("content_page_invalid", "Content chapter title changed between segments")
                current = parts[chapter_id]
                if segment["chapter_offset"] != len(current):
                    raise TaskError("content_page_invalid", "Content chapter ranges are discontinuous")
                parts[chapter_id] = current + segment["text"]
                cursor = segment["end"]
            next_offset = page.get("next_offset")
            if page.get("end_offset") != cursor:
                raise TaskError("content_page_invalid", "Content page end does not match its segments")
            if next_offset is None:
                if cursor != total:
                    raise TaskError("content_page_invalid", "Content ended before the fixed version was fully read")
                break
            if type(next_offset) is not int or next_offset != cursor or next_offset <= offset or next_offset >= total:
                raise TaskError("content_page_invalid", "Content pagination did not advance")
            offset = next_offset
            step += 1
        return (tuple({**chapter, "text": parts[chapter["chapter_id"]]} for chapter in chapters),
                tuple(pages), step + 1)

    def _job(self, parent, call, request, settings, authoring_request, materials, *, work_type=""):
        parent.revalidate()
        task_id = "content-authoring-" + digest([parent.task_id, call.call_id])
        offered = {}
        for material in request.context.materials:
            if material.source is not None:
                offered.setdefault("source-" + str(len(offered) + 1), material.payload)
        offered.update(materials)
        job = _Job(parent.context, request, settings, authoring_request, offered, work_type.strip(),
                   revalidate=parent.revalidate)
        self._jobs[task_id] = job
        return task_id

    async def _draft(self, parent, job_id):
        job = self._jobs[job_id]
        task_id = job_id
        if DRAFT_KIND not in self.gateway.router.profiles:
            raise TaskError("model_task_unregistered", "the dedicated Content writing model route is unavailable")
        now = self.tasks.now()
        deadline = min(parent.deadline_at, now + timedelta(seconds=AUTHORING_BUDGET_SECONDS))
        if deadline <= now:
            raise TaskError("content_authoring_expired", "the original conversation has expired")
        action = job.authoring_request["action"]
        request = replace(job.request,
            instruction=authoring_service.prompt(job.authoring_request, job.materials, settings=job.settings,
                                                 work_type=job.work_type),
            output_format="content_authoring", response_format="json_schema",
            response_schema_json=encode(authoring.schema_for(action)), images=(), reply_images=False,
            reply_actions=("text",),
            audios=(), videos=(), streaming=None)
        spec = TaskSpec(task_id, job.context, DRAFT_KIND, task_id, task_id, now, deadline)
        require(self.tasks.submit(spec))
        try:
            execution = await self.runner.run(job.context, task_id)
            parent.revalidate()
            self._require_enabled(parent.context)
            if not execution.is_success:
                raise TaskError(execution.failure.code, execution.failure.reason)
            settled = execution.unwrap()
            task = settled.task
            if task is None:
                raise TaskError("content_authoring_capacity", "the dedicated writing task was not admitted")
            if task.state != "succeeded" or job.result is None:
                # Report what stopped the round, not the supervisor's stop label: a
                # model-level cut carries its own code, and only a task-level cut
                # falls back to the supervised reason.
                raise TaskError(settled.failure_code or task.reason or "content_authoring_failed",
                                "the dedicated writing task did not finish")
            return job.result
        finally:
            self._jobs.pop(task_id, None)

    async def _generate(self, run):
        job = self._jobs.get(run.task.spec.task_id)
        if job is None:
            raise TaskError("content_authoring_request_unavailable", "the original writing request is unavailable")
        run.validate()
        action = job.authoring_request["action"]
        completion = await self.gateway.generate(run, replace(job.request,
            instruction=authoring_service.prompt(job.authoring_request, job.materials, settings=job.settings,
                                                 work_type=job.work_type),
            output_format="content_authoring", response_format="json_schema",
            response_schema_json=encode(authoring.schema_for(action)), images=(), reply_images=False,
            reply_actions=("text",),
            audios=(), videos=(), streaming=None))
        run.validate()
        if completion.draft is None:
            raise TaskError("content_authoring_output", "the writing model did not return a complete draft")
        job.result = authoring_service.parse_draft(completion.draft.text, action=action)
        return TaskResult("succeeded", result_ref=run.task.spec.task_id)

    def validate_result(self, row, context, result, external, at):
        """Recheck every native receipt that made a high-level authoring result."""
        if (external is None or row["action"] not in ACTIONS
                or context.scope.kind is not ScopeKind.PRIVATE
                or row["effect"] != "committed" or result.get("state") != "succeeded"
                or result.get("effect") != "committed"):
            raise TaskError("content_authoring_receipt", "the original Content authoring receipt is unavailable")
        try:
            arguments = json.loads(row["arguments_json"])
            if isinstance(row, dict) and row.get("execution_path") == "astrbot_core":
                children = self._native_children(row, context, result)
            else:
                batch = self.runtime.read_one("SELECT * FROM conversation_batches WHERE batch_id=?", (row["batch_id"],))
                plan = self.runtime.read_one("SELECT plan_json FROM model_requests WHERE task_id=?", (row["task_id"],))
                if (batch is None or plan is None or batch["origin_key"] != context.key
                        or batch["generation_task_id"] != row["task_id"]
                        or row["action"] not in json.loads(plan["plan_json"]).get("tool_actions", ())):
                    raise ValueError("authoring parent changed")
                children = self.runtime.read_all("""SELECT * FROM extension_tool_invocations
                    WHERE parent_task_id=? AND batch_id=? AND tool_call_id LIKE 'content-authoring-subcall:%'
                    ORDER BY created_at, invocation_id""", (row["task_id"], row["batch_id"]))
            fields = {"state", "effect", "action", "work_id", "version_id", "title"}
            if row["action"] == CONTINUE_ACTION:
                fields |= {"base_version", "adopted"}
            if set(result) != fields or result["action"] != (
                    "create" if row["action"] == CREATE_ACTION else "save_candidate"):
                raise ValueError("invalid authoring result shape")
            if not children:
                raise ValueError("Content has no original subcalls")
            child_results = []
            for child in children:
                try:
                    _, step_text, _ = child["tool_call_id"].split(":", 2)
                    step = int(step_text)
                except (ValueError, AttributeError) as error:
                    raise ValueError("invalid Content subcall identity") from error
                expected_id = self._subcall_id(row, step)
                if child["tool_call_id"] != expected_id:
                    continue
                allowed = ({"content.work.create"} if row["action"] == CREATE_ACTION else
                           {"content.work.list", "content.work.describe", "content.work.read",
                            "content.work.save_candidate"})
                if child["action"] not in allowed:
                    raise ValueError("authoring invoked an unrelated Content operation")
                summary, material = external.snapshot(child, context, at)
                native = self._result(summary, material)
                child_results.append((step, child, summary, material, native))
            child_results.sort(key=lambda item: item[0])
            if row["action"] == CREATE_ACTION:
                if (len(child_results) != 1 or child_results[0][0] != 1
                        or child_results[0][1]["action"] != "content.work.create"):
                    raise ValueError("new work requires exactly one original Content create")
                receipt = self._receipt(child_results[0][2], child_results[0][3], "create")
                if receipt["work_id"] != result["work_id"] or receipt["version_id"] != result["version_id"]:
                    raise ValueError("created work receipt differs from the original result")
                create_arguments = json.loads(child_results[0][1]["arguments_json"])
                requested_title = (arguments.get("title") or "").strip()
                requested_type = (arguments.get("work_type") or "").strip()
                chapters = create_arguments.get("chapters")
                if (not isinstance(create_arguments.get("title"), str) or not create_arguments["title"].strip()
                        or not isinstance(create_arguments.get("work_type"), str) or not create_arguments["work_type"].strip()
                        or not isinstance(chapters, list) or len(chapters) != 1
                        or type(chapters[0]) is not dict
                        or not isinstance(chapters[0].get("text"), str) or not chapters[0]["text"].strip()
                        or result["title"] != create_arguments["title"]
                        or requested_title and requested_title != create_arguments["title"]
                        or requested_type and requested_type != create_arguments["work_type"]):
                    raise ValueError("created Content request differs from the original authoring request")
            else:
                self._validate_continuation(row, arguments, result, child_results)
        except TaskError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise TaskError("content_authoring_receipt", "the original Content authoring receipt changed") from error
        return result, None

    def _native_children(self, row, context, result):
        from companion_v2.conversation.native_tool_results import PREFIX, operation_row

        identifier = "native-tool-" + digest([row["task_id"], row["tool_call_id"]])
        saved = self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key=?", (PREFIX + identifier,))
        original = json.loads(saved["value"]) if saved else None
        if (original is None or original["origin_key"] != context.key
                or original["turn_id"] != row["task_id"] or original["call_id"] != row["tool_call_id"]
                or original["action"] != row["action"] or original["arguments_json"] != row["arguments_json"]
                or original["state"] != "succeeded" or original["effect"] != "committed"
                or original["result"] != result):
            raise ValueError("native authoring parent changed")
        links = self.runtime.read_all("""SELECT value FROM runtime_metadata WHERE substr(key, 1, ?) = ?
            AND json_extract(value, '$.origin_key') = ? AND json_extract(value, '$.turn_id') = ?
            AND json_extract(value, '$.call_id') LIKE 'content-authoring-subcall:%'
            AND json_extract(value, '$.task_id') IS NOT NULL ORDER BY rowid""",
            (len(PREFIX), PREFIX, context.key, row["task_id"]))
        children = []
        for saved_link in links:
            link = json.loads(saved_link["value"])
            try:
                _, step_text, _ = link["call_id"].split(":", 2)
                step = int(step_text)
            except (TypeError, ValueError):
                continue
            call_id = self._subcall_id(row, step)
            if link["call_id"] != call_id:
                continue
            if step < 1:
                raise ValueError("native authoring child changed its original step")
            child_id = "native-tool-" + digest([row["task_id"], call_id])
            if link.get("invocation_id") != child_id:
                raise ValueError("native authoring child changed its original call")
            child = operation_row(self.runtime, child_id)
            if child is None or child["tool_call_id"] != call_id or child["parent_task_id"] != row["task_id"]:
                raise ValueError("native authoring child operation is unavailable")
            children.append(child)
        return tuple(children)

    @staticmethod
    def _subcall_id(row, step):
        return "content-authoring-subcall:" + str(step) + ":" + digest(
            [row["task_id"], row["tool_call_id"], step])

    def _validate_continuation(self, row, arguments, result, children):
        if (result.get("adopted") is not False or not isinstance(result.get("base_version"), str)
                or not result["base_version"]):
            raise ValueError("continuation result changed its original work or adoption state")
        requested_id = (arguments.get("work_id") or "").strip()
        requested_title = (arguments.get("work_title") or "").strip()
        if bool(requested_id) == bool(requested_title):
            raise ValueError("continuation must identify one Content work")
        index, expected_step, selected_id = 0, 1, requested_id
        if requested_title:
            matches, after_id, seen_cursors, finished_listing = {}, "", set(), False
            while index < len(children) and children[index][1]["action"] == "content.work.list":
                step, child, _summary, _material, native = children[index]
                list_args = json.loads(child["arguments_json"])
                if (step != expected_step or list_args.get("after_id", "") != after_id
                        or list_args.get("limit") != 50):
                    raise ValueError("work title lookup did not follow ordered Content pages")
                items = native.get("items")
                if type(items) is not list or len(items) > 50:
                    raise ValueError("Content title lookup page is invalid")
                for item in items:
                    if (type(item) is not dict or type(item.get("work_id")) is not str
                            or type(item.get("title")) is not str):
                        raise ValueError("Content title lookup summary is invalid")
                    if item["title"] == requested_title:
                        matches[item["work_id"]] = item
                next_after = native.get("next_after")
                index, expected_step = index + 1, expected_step + 1
                if next_after is None:
                    finished_listing = True
                    break
                if (type(next_after) is not str or not next_after or next_after <= after_id
                        or next_after in seen_cursors):
                    raise ValueError("Content title lookup pagination did not advance")
                seen_cursors.add(next_after)
                after_id = next_after
            if not finished_listing:
                raise ValueError("continuation title lookup did not finish its catalogue")
            selected_id = authoring_service.select_work_id(requested_title, list(matches.values()))
        elif index < len(children) and children[index][1]["action"] == "content.work.list":
            raise ValueError("continuation listed works without a title lookup request")
        if not selected_id or selected_id != result.get("work_id"):
            raise ValueError("continuation selected a different Content work")
        if (index >= len(children) - 1 or children[index][0] != expected_step
                or children[index][1]["action"] != "content.work.describe"):
            raise ValueError("continuation must describe and read a fixed version before saving")
        description = children[index][4]
        work_id, base_version, base_revision = (description.get("work_id"), description.get("head_version"),
                                                 description.get("revision"))
        if (description.get("state") != "active" or work_id != result["work_id"]
                or description.get("title", "") != result.get("title")
                or base_version != result["base_version"] or type(base_revision) is not int):
            raise ValueError("continuation differs from the described Content version")
        pages, parts, chapters = [], {}, []
        chapter_titles = {}
        offset, total = 0, None
        expected_step += 1
        for step, child, summary, material, native in children[index + 1:-1]:
            if (step != expected_step or child["action"] != "content.work.read"
                    or native.get("work_id") != work_id or native.get("version_id") != base_version
                    or native.get("offset") != offset):
                raise ValueError("continuation read did not pin one ordered version")
            if type(native.get("total_chars")) is not int or native["total_chars"] < 0:
                raise ValueError("Content read omitted its fixed manuscript length")
            if total is not None and native["total_chars"] != total:
                raise ValueError("Content manuscript length changed between pages")
            total = native["total_chars"]
            cursor = offset
            for segment in native.get("segments", ()):
                if type(segment) is not dict:
                    raise ValueError("Content read segment is invalid")
                chapter_id = segment.get("chapter_id")
                chapter_title = segment.get("chapter_title", "")
                if (not isinstance(chapter_id, str) or not chapter_id
                        or type(chapter_title) is not str
                        or type(segment.get("text")) is not str
                        or type(segment.get("chapter_offset")) is not int
                        or type(segment.get("start")) is not int or segment["start"] != cursor
                        or type(segment.get("end")) is not int
                        or segment["end"] - cursor != len(segment["text"])):
                    raise ValueError("Content read segments are discontinuous")
                if chapter_id not in parts:
                    chapters.append({"chapter_id": chapter_id, "title": chapter_title})
                    parts[chapter_id] = ""
                    chapter_titles[chapter_id] = chapter_title
                elif chapter_titles[chapter_id] != chapter_title:
                    raise ValueError("Content chapter title changed between segments")
                current = parts[chapter_id]
                if segment["chapter_offset"] != len(current):
                    raise ValueError("Content read segments are discontinuous")
                parts[chapter_id] = current + segment["text"]
                cursor = segment["end"]
            if native.get("end_offset") != cursor:
                raise ValueError("Content read end differs from its segments")
            next_offset = native.get("next_offset")
            if (next_offset is None and cursor != total
                    or next_offset is not None and (type(next_offset) is not int or next_offset != cursor
                                                    or next_offset <= offset or next_offset >= total)):
                raise ValueError("Content read pages are incomplete or discontinuous")
            offset = next_offset
            expected_step += 1
            pages.append(native)
        if not pages or children[-1][0] != expected_step or children[-1][1]["action"] != "content.work.save_candidate":
            raise ValueError("continuation candidate save is missing")
        save = children[-1]
        save_args = json.loads(save[1]["arguments_json"])
        if (save_args.get("work_id"), save_args.get("base_version"), save_args.get("base_revision")) != (
                work_id, base_version, base_revision):
            raise ValueError("candidate was saved against a different Content version")
        original = tuple({**chapter, "text": parts[chapter["chapter_id"]]} for chapter in chapters)
        candidate = save_args.get("chapters")
        if (type(candidate) is not list or len(candidate) != len(original) + 1
                or any(type(candidate[index]) is not dict for index in range(len(original) + 1))
                or any(candidate[index].get("chapter_id") != chapter["chapter_id"]
                       or candidate[index].get("text") != chapter["text"]
                       for index, chapter in enumerate(original))):
            raise ValueError("candidate did not preserve the fixed base version")
        continuation = candidate[-1]
        expected_chapter = "continuation-" + digest([row["task_id"], row["tool_call_id"], base_version])[:20]
        if (continuation.get("chapter_id") != expected_chapter or continuation.get("title") != "续写"
                or not isinstance(continuation.get("text"), str) or not continuation["text"].strip()):
            raise ValueError("candidate continuation chapter differs from its original authoring call")
        receipt = self._receipt(save[2], save[3], "save_candidate", work_id=work_id)
        if (receipt["version_id"] != result["version_id"] or result["base_version"] != base_version):
            raise ValueError("candidate receipt differs from the original continuation")

    @staticmethod
    def _result(summary, material):
        if summary.get("state") != "succeeded" or material is None:
            state = summary.get("state", "unknown")
            code = summary.get("code") or ("content_operation_unknown" if state == "unknown" else "content_operation_failed")
            raise TaskError(code, "the native Content operation did not return a usable result")
        try:
            value = json.loads(material.payload)
            result = value["native"]["result"]
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise TaskError("content_result_invalid", "the native Content result is invalid") from error
        if type(result) is not dict:
            raise TaskError("content_result_invalid", "the native Content result is invalid")
        return result

    @classmethod
    def _receipt(cls, summary, material, action, *, work_id=None):
        result = cls._result(summary, material)
        receipt = result.get("receipt")
        if (type(receipt) is not dict or receipt.get("action") != action
                or work_id is not None and receipt.get("work_id") != work_id
                or not isinstance(receipt.get("work_id"), str)
                or not isinstance(receipt.get("version_id"), str)):
            raise TaskError("content_receipt_mismatch", "Content did not confirm the requested saved version")
        return receipt

    async def close(self):
        if self._closed:
            return ()
        self._closed = True
        self._jobs.clear()
        return await self.runner.close() if self.runner is not None else ()

    def _require_enabled(self, context):
        if not authoring_service.effective_settings(self.runtime, context).enabled:
            raise TaskError("content_authoring_disabled", "native authoring is turned off for this role")


__all__ = ["ACTIONS", "CONTINUE_ACTION", "CREATE_ACTION", "ContentAuthoringWorkflow", "INSTRUCTION", "TOOLS"]
