"""Run existing native authoring against an authorized calendar occurrence."""

import asyncio
from datetime import datetime, timedelta
import json
import logging
import sqlite3
import time
from types import SimpleNamespace

from astrbot_host.content_access import ensure_content_access
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
from companion_v2.execution.models import TaskError, digest, encode, require, stamp
from companion_v2.foundation.outcome import Outcome
from companion_v2.role_work.life.calendar_work import (
    require_window,
    reserve_routine_slot,
)


logger = logging.getLogger(__name__)


class ScheduledAuthoring:
    BLOCKED_LOG_INTERVAL_SECONDS = 300.0
    BLOCKED_LOG_MAX_KEYS = 256
    MAX_SLOT_ATTEMPTS = 3
    RETRY_COOLDOWN_SECONDS = 300
    RETRYABLE_FAILURE_CODES = frozenset({
        "content_authoring_output",
        "content_authoring_failed",
        "content_authoring_capacity",
        "content_storage_unavailable",
        "task_lane_full",
        "model_usage_unknown",
        "provider_timeout",
        "provider_unavailable",
        "model_timeout",
    })

    @classmethod
    def is_retryable_failure(cls, code: str | None) -> bool:
        return isinstance(code, str) and code in cls.RETRYABLE_FAILURE_CODES

    @staticmethod
    def _parse_metadata(raw) -> dict:
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _can_retry_metadata(self, meta: dict, now: datetime) -> bool:
        if meta.get("state") != "failed":
            return False
        if not self.is_retryable_failure(meta.get("code")):
            return False
        attempts = int(meta.get("attempts") or 1)
        if attempts >= self.MAX_SLOT_ATTEMPTS:
            return False
        failed_at = meta.get("at")
        if isinstance(failed_at, str) and failed_at:
            try:
                parsed = datetime.fromisoformat(failed_at)
                if now < parsed + timedelta(seconds=self.RETRY_COOLDOWN_SECONDS):
                    return False
            except ValueError:
                pass
        return True

    def status_key(self, context) -> str:
        return "content.calendar.last:" + self.app.calendar.records.role_key(context)

    def last_run_status(self, context) -> dict | None:
        row = self.runtime.read_one(
            "SELECT value FROM runtime_metadata WHERE key=?",
            (self.status_key(context),),
        )
        if row is None:
            return None
        meta = self._parse_metadata(row["value"])
        return meta or None

    def __init__(self, consumer):
        self.consumer = consumer
        self.app = consumer.application
        self.runtime = self.app.runtime
        self._blocked_log_at: dict[tuple[str, str, str], float] = {}
        self._log_clock = time.monotonic

    def _warn_blocked(self, context, stage: str, code: str, *, window=None) -> None:
        now = self._log_clock()
        key = (getattr(context, "key", ""), stage, code)
        last = self._blocked_log_at.get(key)
        if last is not None and now - last < self.BLOCKED_LOG_INTERVAL_SECONDS:
            return
        if len(self._blocked_log_at) >= self.BLOCKED_LOG_MAX_KEYS and key not in self._blocked_log_at:
            oldest = min(self._blocked_log_at, key=self._blocked_log_at.__getitem__)
            self._blocked_log_at.pop(oldest, None)
        self._blocked_log_at[key] = now
        role = getattr(getattr(context, "role_binding", None), "role", None)
        role_id = getattr(role, "subject_id", "")
        window_id = f"{window.get('id', '')}:{window.get('sequence', '')}" if isinstance(window, dict) else "-"
        logger.warning(
            "[CompanionV2] 定时创作被阻止 stage=%s code=%s role=%s window=%s",
            stage,
            code,
            role_id,
            window_id,
        )

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
        if window is not None:
            row = self.runtime.read_one(
                "SELECT value FROM runtime_metadata WHERE key=?",
                (self.key(context, window),),
            )
            if row is not None:
                meta = self._parse_metadata(row["value"])
                if not self._can_retry_metadata(meta, self.app.now()):
                    return False
        if DRAFT_KIND not in self.app.gateway.router.profiles:
            if window is not None:
                self._warn_blocked(context, "ready", "model_task_unregistered", window=window)
            return False
        if not effective_settings(self.runtime, context).enabled:
            if window is not None:
                self._warn_blocked(context, "ready", "content_authoring_disabled", window=window)
            return False
        if self.app.gateway.ledger.background_deferral(
            context, DRAFT_KIND, at=self.app.now()
        ):
            return False
        ensure_content_access(self.runtime, context, observed_at=self.app.now())
        try:
            for action in ("read", "list"):
                self.admit(context, action)
        except TaskError as error:
            if window is not None:
                self._warn_blocked(context, "ready", error.code, window=window)
            return False
        return True

    def plan_continuation(self, context, now, settings, page, *, sleep_plan):
        if (
            not settings["planning_enabled"]
            or page["scan_truncated"]
            or page["occurrences_truncated"]
            or not self.ready(context)
        ):
            return
        try:
            port = self.admit(context, "list")
            works = catalogue(
                port.service.repository.path, self.consumer.access, context
            )
            if works and not any(work["needs_writing"] for work in works):
                return
            self.admit(context, "save_candidate" if works else "create")
            return reserve_routine_slot(
                self.app.calendar,
                context,
                routine="writing",
                timezone=settings["planning_timezone"],
                now=now,
                items=page["items"],
                sleep_plan=sleep_plan,
                title=None if works else "文学创作",
                detail=None if works else "写一篇短作或新作品开篇。",
            )
        except (TaskError, ContentError, sqlite3.Error) as error:
            code = getattr(error, "code", type(error).__name__)
            self._warn_blocked(context, "plan_continuation", code)
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
        if work is None and "续写" in title and works:
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
        # slot, but it cannot write the same calendar occurrence twice; bounded
        # retries only reopen explicitly retryable transient failures.
        with self.runtime.transaction() as connection:
            existing = connection.execute(
                "SELECT value FROM runtime_metadata WHERE key=?",
                (key,),
            ).fetchone()
            if existing is None:
                attempts = 1
                connection.execute(
                    "INSERT INTO runtime_metadata(key,value) VALUES (?,?)",
                    (key, encode({"state": "running", "at": stamp(at), "attempts": attempts})),
                )
            else:
                meta = self._parse_metadata(existing["value"])
                if not self._can_retry_metadata(meta, at):
                    return None
                attempts = int(meta.get("attempts") or 1) + 1
                connection.execute(
                    "UPDATE runtime_metadata SET value=? WHERE key=?",
                    (encode({"state": "running", "at": stamp(at), "attempts": attempts}), key),
                )
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
            finished_at = stamp(self.app.now())
            result = {
                "state": "written",
                "work_id": receipt.work_id,
                "version_id": receipt.version_id,
                "operation_id": receipt.operation_id,
                "attempts": attempts,
                "at": finished_at,
            }
        except (TaskError, ContentError, sqlite3.Error) as error:
            finished_at = stamp(self.app.now())
            result = {
                "state": "failed",
                "code": getattr(error, "code", "content_storage_unavailable"),
                "attempts": attempts,
                "at": finished_at,
            }
            self._warn_blocked(context, "run", result["code"], window=window)
        summary = {
            "state": result["state"],
            "code": result.get("code"),
            "work_id": result.get("work_id"),
            "window_title": str(window.get("title") or "")[:120],
            "attempts": attempts,
            "retryable": result["state"] == "failed" and self.is_retryable_failure(result.get("code")) and attempts < self.MAX_SLOT_ATTEMPTS,
            "at": result["at"],
        }
        with self.runtime.transaction() as connection:
            connection.execute(
                "UPDATE runtime_metadata SET value=? WHERE key=?", (encode(result), key)
            )
            connection.execute(
                "INSERT INTO runtime_metadata(key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (self.status_key(context), encode(summary)),
            )
        if result["state"] == "failed":
            return Outcome.failed(
                result["code"], "the calendar writing slot did not save a version"
            )
        return Outcome.success(result)
