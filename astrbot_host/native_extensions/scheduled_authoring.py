"""Run existing native authoring against an authorized calendar occurrence."""

import asyncio
from datetime import datetime
import sqlite3
from types import SimpleNamespace

from companion_v2.content.models import (
    Call,
    Chapter,
    ContentError,
    JsonObject,
    Origin,
    WorkOwner,
)
from companion_v2.content.service import ContentService
from companion_v2.conversation.content.access import document, owner_for
from companion_v2.conversation.content.authoring_service import effective_settings
from companion_v2.conversation.content.authoring_tasks import DRAFT_KIND
from companion_v2.conversation.content.library import (
    catalogue,
    check_audience,
    manuscript,
)
from companion_v2.conversation.foreground import (
    BackgroundPromptTrigger,
    PromptRequest,
    SealedForegroundContext,
)
from companion_v2.execution.models import TaskError, digest, encode, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.role_work.life.calendar_work import (
    require_window,
    reserve_routine_slot,
)


class ScheduledAuthoring:
    def __init__(self, consumer):
        self.consumer = consumer
        self.app = consumer.application
        self.runtime = self.app.runtime

    def port(self):
        port = self.consumer.registry.active_service("content")
        if port is None or not isinstance(
            getattr(port, "service", None), ContentService
        ):
            raise TaskError(
                "content_provider_unavailable",
                "the native Content service is not running",
            )
        return port

    def admit(self, context, action):
        self.consumer.live()
        registry = self.consumer.registry
        declaration = self.consumer.declarations.get("content.work." + action)
        if (
            declaration is None
            or registry.current_declaration(declaration.host_tool_id) != declaration
        ):
            raise TaskError(
                "content_provider_unavailable",
                "the native Content service is not running",
            )
        port = self.port()
        at = self.app.now()
        require(
            self.app.tasks.check_context_access(
                context, action="conversation.reply", observed_at=at
            )
        )
        self.app.external.admit(declaration.descriptor, context, at)
        return port

    def ready(self, context, window=None):
        if (
            DRAFT_KIND not in self.app.gateway.router.profiles
            or not effective_settings(self.runtime, context).enabled
            or self.app.gateway.ledger.background_deferral(
                context, DRAFT_KIND, at=self.app.now()
            )
        ):
            return False
        try:
            for action in ("read", "list"):
                self.admit(context, action)
        except TaskError:
            return False
        return (
            window is None
            or self.runtime.read_one(
                "SELECT 1 FROM runtime_metadata WHERE key=?",
                (self.key(context, window),),
            )
            is None
        )

    def plan_continuation(self, context, now, settings, page, *, sleep_plan):
        if (
            not settings["planning_enabled"]
            or page["scan_truncated"]
            or page["occurrences_truncated"]
            or not self.ready(context)
        ):
            return
        try:
            self.admit(context, "save_candidate")
            port = self.admit(context, "list")
            works = catalogue(
                port.service.repository.path, self.consumer.access, context
            )
            if any(work["needs_writing"] for work in works):
                return reserve_routine_slot(
                    self.app.calendar,
                    context,
                    routine="writing",
                    timezone=settings["planning_timezone"],
                    now=now,
                    items=page["items"],
                    sleep_plan=sleep_plan,
                )
        except (TaskError, ContentError, sqlite3.Error):
            return

    def key(self, context, window):
        return "content.calendar:" + digest(
            [
                self.app.calendar.records.role_key(context),
                window["id"],
                window["sequence"],
            ]
        )

    def operation(
        self,
        context,
        action,
        key,
        parameters,
        guard,
        *,
        administrator=False,
        **arguments,
    ):
        if (administrator and action != "adopt") or (
            not administrator and action not in ("create", "save_candidate")
        ):
            raise TaskError(
                "content_access_action", "the host path cannot use this Content action"
            )
        port = self.port() if administrator else self.admit(context, action)
        owner = WorkOwner(**owner_for(context))
        audience = document(context.audience)
        contributor = JsonObject.capture(
            {
                "kind": "role_output",
                "role": document(context.role_binding.role),
                "requested_by": document(context.actor.subject),
            }
        )
        origin = Origin(
            key,
            JsonObject.capture(
                {"schema": "companion.calendar-authoring.v1", **parameters}
            ),
            JsonObject.capture(document(context.actor)),
            JsonObject.capture(document(context.role_binding)),
            JsonObject.capture(audience),
        )

        def check():
            guard()
            current = self.port() if administrator else self.admit(context, action)
            if current is not port:
                raise TaskError(
                    "content_provider_changed", "the Content service changed"
                )

        def access(request):
            check()
            if (
                request.owner != owner
                or request.origin != origin
                or request.action != action
                or request.contributor is not None
                and request.contributor != contributor
            ):
                raise ContentError("content_host_target_mismatch")
            if request.stored_audience is not None:
                check_audience(request.stored_audience.value(), audience)
            for source in request.sources:
                self.consumer.access.reference(source.value(), context)

        # The live provider owns the original repository and version CAS.
        # This access port binds a single trusted host action instead of a chat call.
        service = ContentService(port.service.repository, SimpleNamespace(check=access))
        if action in ("create", "save_candidate"):
            arguments["contributor"] = contributor
        return getattr(service, action)(
            Call(origin, check), owner, operation_key=key, **arguments
        )

    async def run(self, life, window):
        await life.preflight()
        context = life.context
        at = self.app.now()
        key = self.key(context, window)
        if not self.ready(context, window):
            return None

        def validate():
            life.authorize()
            if (
                not life.settings["enabled"]
                or not effective_settings(self.runtime, context).enabled
            ):
                raise TaskError(
                    "content_authoring_disabled",
                    "the scheduled authoring switch changed",
                )
            if self.app.role_state.body_clock.pending_sleep(context):
                raise TaskError("life_sleeping", "the role is sleeping")
            return require_window(self.app.calendar, context, window, self.app.now())

        validate()
        port = self.admit(context, "list")
        path = port.service.repository.path
        works = await asyncio.to_thread(catalogue, path, self.consumer.access, context)
        current = [item for item in works if item["needs_writing"]]
        title = window["title"]
        intended = [
            item for item in current if item["title"] and item["title"] in title
        ]
        current.sort(
            key=lambda item: item["candidate_at"] or item["created_at"], reverse=True
        )
        work = (intended or current or [None])[0]
        if (
            any(token in title for token in ("新作", "新篇", "新故事", "新小说"))
            and not intended
        ):
            work = None
        if work is None and "续写" in title:
            return None
        action = "save_candidate" if work else "create"
        self.admit(context, action)
        base, head, chapters = None, None, ()
        if work:
            head = await asyncio.to_thread(
                manuscript,
                path,
                self.consumer.access,
                context,
                work["work_id"],
                work["head_version"],
            )
            chapters = tuple(Chapter(**item) for item in head["chapters"])
            base = head
            if work["candidate_version"]:
                draft = await asyncio.to_thread(
                    manuscript,
                    path,
                    self.consumer.access,
                    context,
                    work["work_id"],
                    work["candidate_version"],
                )
                proposed = tuple(Chapter(**item) for item in draft["chapters"])
                if proposed[: len(chapters)] == chapters:
                    chapters, base = proposed, draft
                elif not {chapter.chapter_id for chapter in proposed}.intersection(
                    chapter.chapter_id for chapter in chapters
                ):
                    # Older chat tools saved only the appended paragraphs.
                    chapters, base = chapters + proposed, draft
                else:
                    raise TaskError(
                        "content_candidate_conflict",
                        "the latest candidate needs manual review",
                    )
        brief = (
            "按当前角色的日程进行一次私人创作。写具体的作品正文。\n"
            + title
            + "\n"
            + window["detail"]
        )
        if work:
            brief += "\n继续《" + work["title"] + "》，从现有稿末尾推进一个连贯的小节。"
            if type(work["target_chars"]) is int and work["target_chars"] > 0:
                brief += f"原计划约 {work['target_chars']} 字，已存约 {sum(len(c.text) for c in chapters)} 字；接近目标时安排自然收尾。"
        else:
            brief += "\n完成一篇可独立阅读的短作。"
        binding = BackgroundPromptTrigger(
            key, context.actor.principal, context.scope, at
        )
        request = PromptRequest(
            binding,
            life.instruction,
            SealedForegroundContext((), (), ()),
            "",
            context.role_binding,
        )
        # The durable claim precedes any model call. A restart may leave a missed
        # slot, but it cannot write the same calendar occurrence twice.
        with self.runtime.transaction() as connection:
            if not connection.execute(
                "INSERT OR IGNORE INTO runtime_metadata(key,value) VALUES (?,?)",
                (key, encode({"state": "running", "at": at})),
            ).rowcount:
                return None
        try:
            version = (
                (
                    work["candidate_version"]
                    if base is not head
                    else work["head_version"]
                )
                if work
                else ""
            )

            def validate_write():
                self.admit(context, action)
                return validate()

            output = await self.app.authoring.draft_scheduled(
                context,
                request,
                key=key,
                deadline=datetime.fromisoformat(window["end"]),
                guard=validate_write,
                brief=brief,
                previous_text="\n\n".join(chapter.text for chapter in chapters),
                version=version,
                work_type=work["work_type"] if work else "",
            )
            await life.preflight()
            record = validate()
            if record["revision"] != window["revision"]:
                raise TaskError(
                    "life_schedule_changed",
                    "the original plan changed while the model was writing",
                )
            if work:
                latest = next(
                    (
                        item
                        for item in catalogue(path, self.consumer.access, context)
                        if item["work_id"] == work["work_id"]
                    ),
                    None,
                )
                if latest is None or (
                    latest["head_version"],
                    latest["candidate_version"],
                ) != (work["head_version"], work["candidate_version"]):
                    raise TaskError(
                        "content_candidate_conflict",
                        "another revision arrived during writing",
                    )
            finished = output.finished
            if work and type(work["target_chars"]) is int:
                finished = (
                    finished
                    and sum(len(c.text) for c in chapters) + len(output.body)
                    >= work["target_chars"]
                )
            parameters = {
                "window": window["id"],
                "sequence": window["sequence"],
                "finished": finished,
                "scheduled_serial": not finished,
            }
            source_docs = []
            for item in record["sources"]:
                reference = self.consumer.access.reference(item, context)
                source_docs.append(JsonObject.capture(document(reference)))
            if work and base is not head:
                for item in base["sources"]:
                    source_docs.append(JsonObject.capture(item))
            if work:
                arguments = {
                    "work_id": work["work_id"],
                    "base_revision": work["revision"],
                    "base_version": work["head_version"],
                    "chapters": chapters
                    + (
                        Chapter(
                            "continuation-" + digest(key)[:24], output.body, "续写"
                        ),
                    ),
                }
            else:
                title = output.title[:200]
                arguments = {
                    "title": title,
                    "work_type": output.work_type[:80],
                    "chapters": (Chapter("chapter-1", output.body, title),),
                }
            receipt = self.operation(
                context,
                action,
                key,
                parameters,
                validate_write,
                sources=tuple(source_docs),
                **arguments,
            )
            result = {
                "state": "written",
                "work_id": receipt.work_id,
                "version_id": receipt.version_id,
                "operation_id": receipt.operation_id,
            }
        except (TaskError, ContentError, sqlite3.Error) as error:
            result = {
                "state": "failed",
                "code": getattr(error, "code", "content_storage_unavailable"),
            }
        with self.runtime.transaction() as connection:
            connection.execute(
                "UPDATE runtime_metadata SET value=? WHERE key=?", (encode(result), key)
            )
        if result["state"] == "failed":
            return Outcome.failed(
                result["code"], "the calendar writing slot did not save a version"
            )
        return Outcome.success(result)
