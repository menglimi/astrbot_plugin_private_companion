"""One self-directed phone step per supervised task.

A phone task is a small durable record: a goal, the private window it belongs
to, a step budget and the steps already taken.  Each maintenance tick submits at
most one step for a due record; that step observes the screen, asks the model
for exactly one action, executes it and writes the outcome back.  One model call
per task is the gateway contract, so the step chain lives in the record rather
than in a single long model run.

This service owns no permission of its own.  The target window must be private,
the operator must have created ``phone.json`` in the V2 data root with
``"enabled": true``, and the approval path that creates a task is the existing
private reply turn.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import base64
import hashlib
import json
from pathlib import Path
import time

from companion_v2.conversation.foreground import (BackgroundPromptTrigger, PromptRequest,
                                                   SealedForegroundContext)
from companion_v2.conversation.media.models import ImageDescriptor
from companion_v2.execution.models import (Lane, ProviderBinding, SideEffect, TaskContext, TaskDefinition,
    TaskError, TaskRegistration, TaskResult, TaskSpec, digest, encode, require, stamp)
from companion_v2.execution.runner import CAPACITY_REFUSALS, ExactTaskRunner
from companion_v2.execution.service import outcome
from companion_v2.foundation.refs import ScopeKind, SourceRef, VersionRef
from companion_v2.model_gateway.models import ModelTaskProfile

from .adb import AdbClient, AdbError
from .annotate import annotate_screen, image_size
from .configuration import PhoneSettings, PhoneSettingsReader, load_settings
from .decider import INSTRUCTION, DecisionError, decision_prompt, parse_decision
from .prompt_image import PhonePromptImage
from .session import (ACTION_KINDS, DEFAULT_ALLOWED, PhoneAction, PhoneObservation, PhoneSession,
                      PhoneStep)

KIND = "phone.act"
OWNER = "device.phone"
QUEUE_PREFIX = "phone.task.v1:"
MAX_TASKS = 32
MAX_GOAL_CHARS = 500
STEP_TIMEOUT_SECONDS = 120
MAX_STEPS = 4
STEP_DELAY_SECONDS = 6.0
TASK_LIFETIME_SECONDS = 7200
ROLE_INSTRUCTION = "保持原人格。这是你自己在用手机完成一件事，不是回复用户，也不对用户说话。"


def phone_profile(provider, *, max_output_tokens=1024):
    return ModelTaskProfile(KIND, Lane.BACKGROUND, provider,
        "按当前屏幕做一步手机操作决定；只输出一个 JSON 动作。",
        max_output_tokens=max_output_tokens, timeout_seconds=STEP_TIMEOUT_SECONDS,
        capabilities=("text", "image"))


@dataclass(frozen=True)
class PhoneTaskRecord:
    task_id: str
    context_json: str
    goal: str
    state: str
    step: int
    max_steps: int
    history: tuple[dict, ...]
    created_at: str
    next_at: str
    expires_at: str
    inflight_at: str = ""
    outcome: str = ""

    def document(self) -> dict:
        value = {"version": 1, "task_id": self.task_id, "context": json.loads(self.context_json),
                 "goal": self.goal, "state": self.state, "step": self.step,
                 "max_steps": self.max_steps, "history": list(self.history),
                 "created_at": self.created_at, "next_at": self.next_at,
                 "expires_at": self.expires_at}
        if self.inflight_at:
            value["inflight_at"] = self.inflight_at
        if self.outcome:
            value["outcome"] = self.outcome
        return value

    @classmethod
    def parse(cls, value: dict) -> "PhoneTaskRecord":
        required = {"version", "task_id", "context", "goal", "state", "step", "max_steps",
                    "history", "created_at", "next_at", "expires_at"}
        if type(value) is not dict or set(value) - (required | {"inflight_at", "outcome"}) or not required <= set(value):
            raise ValueError("stored phone task has an unexpected shape")
        if value["version"] != 1 or value["state"] not in ("active", "done", "failed", "cancelled"):
            raise ValueError("stored phone task has an unknown version or state")
        return cls(str(value["task_id"]), json.dumps(value["context"], ensure_ascii=False, sort_keys=True),
                   str(value["goal"]), str(value["state"]), int(value["step"]), int(value["max_steps"]),
                   tuple(value["history"]), str(value["created_at"]), str(value["next_at"]),
                   str(value["expires_at"]), str(value.get("inflight_at") or ""),
                   str(value.get("outcome") or ""))


class PhoneActionService:
    def __init__(self, runtime, *, notify=None, settings_reader=None,
                 client_factory=None) -> None:
        self.runtime = runtime
        self.notify = notify or (lambda: None)
        # A caller-supplied reader is for tests; production attaches the
        # configuration service and reads the role group through the task context.
        self._settings_reader = settings_reader
        self._client_factory = client_factory
        self.pending: dict[str, PhoneTaskRecord] = {}
        self.runner = None
        self.tasks = self.gateway = self.app = None
        self._closed = False
        self.registrations = (TaskRegistration(TaskDefinition(
            KIND, OWNER, KIND, Lane.BACKGROUND, side_effect=SideEffect.LOCAL,
            binding=ProviderBinding(OWNER, "local", "1", "phone-1"),
            timeout_seconds=STEP_TIMEOUT_SECONDS, max_attempts=1, concurrency=1), self.validate),)

    # -- lifecycle -----------------------------------------------------

    def attach(self, app):
        self.app, self.tasks, self.gateway = app, app.tasks, app.gateway
        if self._settings_reader is None:
            self._settings_reader = PhoneSettingsReader(app.configuration,
                                                        fallback_root=self.runtime.data_root)
        self.runner = ExactTaskRunner(self.tasks, frozenset({KIND}), self.generate,
                                      waiting=app.background_waiting)
        return self

    def close(self):
        self._closed = True

    # -- settings and records ------------------------------------------

    def settings(self, context) -> PhoneSettings:
        if self._settings_reader is None or self.tasks is None:
            return load_settings(self.runtime.data_root)
        return self._settings_reader.read(context, self.tasks.now())

    def available(self, context) -> bool:
        """The reply tool is offered only where a task could actually run."""
        if context is None or context.scope.kind is not ScopeKind.PRIVATE:
            return False
        try:
            settings = self.settings(context)
        except (ValueError, TaskError):
            return False
        return settings.enabled and settings.permits(context)

    def _key(self, task_id: str) -> str:
        return QUEUE_PREFIX + task_id

    def _read(self, task_id: str) -> PhoneTaskRecord | None:
        row = self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?", (self._key(task_id),))
        if row is None:
            return None
        try:
            return PhoneTaskRecord.parse(json.loads(row["value"]))
        except (TypeError, ValueError):
            return None

    def _records(self) -> tuple[PhoneTaskRecord, ...]:
        prefix = QUEUE_PREFIX
        rows = self.runtime.read_all(
            "SELECT value FROM runtime_metadata WHERE key >= ? AND key < ? ORDER BY key LIMIT ?",
            (prefix, prefix + "\uffff", MAX_TASKS))
        records = []
        for row in rows:
            try:
                records.append(PhoneTaskRecord.parse(json.loads(row["value"])))
            except (TypeError, ValueError):
                continue
        return tuple(records)

    def _save(self, record: PhoneTaskRecord) -> None:
        value = json.dumps(record.document(), ensure_ascii=False, sort_keys=True)
        with self.runtime.transaction() as connection:
            connection.execute("""INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value""", (self._key(record.task_id), value))

    # -- creation ------------------------------------------------------

    def create(self, context: TaskContext, goal: str, *, delay_minutes: int = 0,
               max_steps: int | None = None) -> dict:
        if context.scope.kind is not ScopeKind.PRIVATE:
            raise TaskError("phone_private_required", "a phone task belongs to one private window")
        goal = (goal or "").strip()
        if not goal or len(goal) > MAX_GOAL_CHARS:
            raise TaskError("phone_goal_invalid", "a phone goal needs 1 to 500 characters")
        settings = self.settings(context)
        if not settings.enabled:
            raise TaskError("phone_disabled", "the phone channel is not enabled for this role")
        if not settings.permits(context):
            raise TaskError("phone_subject_denied", "this window is not in the phone allowlist")
        steps = settings.max_steps if max_steps is None else int(max_steps)
        if not 1 <= steps <= settings.max_steps:
            raise TaskError("phone_steps_invalid", "requested steps exceed the configured budget")
        if not 0 <= int(delay_minutes) <= 1440:
            raise TaskError("phone_delay_invalid", "phone task delay must be within one day")
        now = self.tasks.now() if self.tasks is not None else datetime.now().astimezone()
        if self.tasks is not None:
            # Refuse at registration instead of retrying a task whose steps can
            # never be authorized; the step still rechecks this boundary.
            require(self.tasks.check_context_access(context, action=KIND, observed_at=now))
        identifier = "phone-" + digest([context.key, goal, stamp(now)])[:32]
        record = PhoneTaskRecord(
            identifier, json.dumps(json.loads(encode(context)), ensure_ascii=False, sort_keys=True),
            goal, "active", 0, steps, (), stamp(now),
            stamp(now + timedelta(minutes=int(delay_minutes))),
            stamp(now + timedelta(minutes=int(delay_minutes), seconds=TASK_LIFETIME_SECONDS)))
        self._save(record)
        self.notify()
        return {"task_id": identifier, "state": "active", "goal": goal, "max_steps": steps,
                "starts_at": record.next_at}

    def cancel(self, task_id: str) -> bool:
        record = self._read(task_id)
        if record is None or record.state != "active":
            return False
        self._save(replace(record, state="cancelled", inflight_at=""))
        return True

    # -- scheduling ----------------------------------------------------

    @outcome
    def advance(self):
        """Submit at most one step for each due record; never observes the phone here."""
        if self._closed or self.runner is None or self.tasks is None:
            return 0
        now = self.tasks.now()
        started = 0
        for record in self._records():
            if record.state != "active":
                continue
            try:
                due = datetime.fromisoformat(record.next_at)
                expires = datetime.fromisoformat(record.expires_at)
            except ValueError:
                continue
            if now < due:
                continue
            if now >= expires or record.step >= record.max_steps:
                self._save(replace(record, state="failed", inflight_at="", outcome="phone_task_expired"))
                continue
            try:
                context = TaskContext.restore(json.loads(record.context_json))
                settings = self.settings(context)
            except (ValueError, KeyError, TypeError, TaskError):
                continue
            if not settings.enabled or not settings.permits(context):
                continue
            if record.inflight_at:
                try:
                    if now - datetime.fromisoformat(record.inflight_at) < timedelta(minutes=3):
                        continue
                except ValueError:
                    pass
                # A step that stayed inflight well past its own deadline never
                # produced a receipt.  Treat it as an unknown action rather than
                # retrying a tap whose effect nobody observed.
                self._save(replace(record, state="failed", inflight_at="", outcome="phone_step_stalled"))
                continue
            inflight = replace(record, inflight_at=stamp(now))
            self._save(inflight)
            identifier = "phone-step-" + digest([record.task_id, record.step])[:32]
            spec = TaskSpec(identifier, TaskContext.restore(json.loads(record.context_json)), KIND,
                            identifier, identifier, now, now + timedelta(seconds=STEP_TIMEOUT_SECONDS))
            self.pending[identifier] = record
            try:
                require(self.tasks.submit(spec))
            except TaskError as error:
                self.pending.pop(identifier, None)
                self._save(replace(record, inflight_at="", outcome=error.code,
                                   next_at=stamp(now + timedelta(seconds=60))))
                if error.code in CAPACITY_REFUSALS or error.code == "task_storage_full":
                    break
                continue
            started += 1
            if started >= 1:
                break
        if started:
            self.notify()
        return started

    # -- one step ------------------------------------------------------

    @outcome
    def validate(self, spec, at):
        record = self.pending.get(spec.task_id)
        if record is None:
            raise TaskError("phone_task_unavailable", "the prepared phone step is no longer held")
        stored = self._read(record.task_id)
        if stored is None or stored.state != "active" or stored.step != record.step:
            raise TaskError("phone_task_changed", "the phone task changed before its step ran")
        settings = self.settings(spec.context)
        if not settings.enabled:
            raise TaskError("phone_disabled", "the phone channel was disabled before this step")
        if not settings.permits(spec.context):
            raise TaskError("phone_subject_denied", "this window is no longer in the phone allowlist")
        require(self.tasks.check_context_access(spec.context, action=KIND, observed_at=at))
        return True

    async def generate(self, run):
        record = self.pending.pop(run.task.task_id, None)
        if record is None:
            raise TaskError("phone_task_unavailable", "the prepared phone step is no longer held")
        stored = self._read(record.task_id)
        if stored is None or stored.state != "active" or stored.step != record.step:
            raise TaskError("phone_task_changed", "the phone task changed before its step ran")
        settings = self.settings(run.task.spec.context)
        if not settings.enabled:
            raise TaskError("phone_disabled", "the phone channel was disabled before this step")
        if not settings.permits(run.task.spec.context):
            raise TaskError("phone_subject_denied", "this window is no longer in the phone allowlist")
        try:
            client = (self._client_factory(settings) if self._client_factory is not None
                      else settings.client())
        except (AdbError, ValueError) as error:
            self._finish(record, state="failed", code=getattr(error, "code", "phone_device_unavailable"))
            raise TaskError("phone_device_unavailable", str(error)) from error
        session = PhoneSession(client, goal=stored.goal, max_steps=stored.max_steps,
                               max_seconds=STEP_TIMEOUT_SECONDS - 10, allowed=settings.allowed,
                               ime=settings.unicode_input,
                               ime_restore_seconds=settings.ime_restore_seconds)
        at = self.tasks.now()
        try:
            observation = session.observe(stored.step)
        except AdbError as error:
            self._finish(record, state="failed", code=error.code)
            raise TaskError(error.code, str(error)) from error
        history = tuple(_history_step(item) for item in stored.history)
        image = self._annotate(record, observation, run, at)
        request = PromptRequest(
            BackgroundPromptTrigger(digest([run.task.task_id, "phone-step"]), run.task.spec.context.actor.principal,
                                    run.task.spec.context.scope, at),
            ROLE_INSTRUCTION, SealedForegroundContext((), (), ()),
            INSTRUCTION + "\n本轮允许的动作：" + ", ".join(sorted(settings.allowed)) +
            "\n\n" + decision_prompt(stored.goal, observation, history),
            run.task.spec.context.role_binding,
            output_format="phone_action", response_format="json_object",
            images=(image,) if image is not None else ())
        try:
            completion = await self.gateway.generate(run, request)
        except TaskError:
            self._finish(record, state="failed", code="phone_model_failed")
            raise
        text = completion.draft.text if completion.draft is not None else ""
        try:
            action = parse_decision(text)
        except DecisionError as error:
            self._finish(record, state="failed", code="phone_decision_invalid")
            raise TaskError("phone_decision_invalid", str(error)) from error
        if action.kind not in settings.allowed:
            self._finish(record, state="failed", code="phone_action_forbidden")
            raise TaskError("phone_action_forbidden", "the configured phone channel forbids this action")
        run.begin_effect()
        started = stamp(self.tasks.now())
        if action.kind == "done":
            step = PhoneStep(stored.step + 1, action, True, action.reason or "完成",
                             observation_sha256=observation.screenshot_sha256, started_at=started)
            self._finish(record, state="done", step=step)
            return TaskResult("succeeded", "committed", result_ref=record.task_id)
        if action.kind == "fail":
            step = PhoneStep(stored.step + 1, action, False, action.reason or "放弃",
                             code="phone_task_failed", observation_sha256=observation.screenshot_sha256,
                             started_at=started)
            self._finish(record, state="failed", step=step)
            return TaskResult("failed", "committed", result_ref=record.task_id)
        # Mark the action before touching the device.  If this process dies
        # between the tap and the receipt, the step stays inflight and the
        # scheduler retires the whole task as an unknown action instead of
        # repeating a side effect nobody observed.
        pending_step = PhoneStep(stored.step + 1, action, False, "动作已开始，结果未记录",
                                 "phone_action_started", observation.screenshot_sha256,
                                 started_at=started)
        self._save(replace(stored, history=(*stored.history, pending_step.document()), inflight_at=""))
        try:
            detail = session.execute(action)
            ok, code = True, ""
        except AdbError as error:
            detail, ok, code = str(error), False, error.code
        step = PhoneStep(stored.step + 1, action, ok, detail, code,
                         observation.screenshot_sha256, started_at=started)
        history = (*stored.history, step.document())
        consecutive = len(history) >= 2 and not history[-1]["ok"] and not history[-2]["ok"]
        if not ok and consecutive:
            self._finish(record, state="failed", history=history, code="phone_repeated_failure")
            return TaskResult("failed", "committed", result_ref=record.task_id)
        if stored.step + 1 >= stored.max_steps:
            self._finish(record, state="failed", history=history, code="phone_exhausted")
            return TaskResult("failed", "committed", result_ref=record.task_id)
        self._save(PhoneTaskRecord(
            task_id=stored.task_id, context_json=stored.context_json, goal=stored.goal, state="active",
            step=stored.step + 1, max_steps=stored.max_steps, history=history,
            created_at=stored.created_at,
            next_at=stamp(self.tasks.now() + timedelta(seconds=STEP_DELAY_SECONDS)),
            expires_at=stored.expires_at, inflight_at=""))
        self.notify()
        return TaskResult("succeeded", "committed", result_ref=record.task_id)

    def _annotate(self, record: PhoneTaskRecord, observation: PhoneObservation, run, at):
        """Draw grid and node boxes; never fail the step because of the annotation."""
        if not observation.screenshot:
            return None
        try:
            annotated = annotate_screen(observation.screenshot, observation.elements,
                                        boxes=observation.stable)
            width, height = image_size(annotated)
            identifier = "phone-shot-" + digest([record.task_id, observation.step])[:32]
            source = SourceRef(identifier, "device.phone", run.task.spec.context.scope,
                               VersionRef(identifier, 1), at)
            descriptor = ImageDescriptor(0, "image/png", width, height, len(annotated),
                                         hashlib.sha256(annotated).hexdigest())
            image = PhonePromptImage(source, source, descriptor,
                                     base64.b64encode(annotated).decode("ascii"))
        except (ValueError, TypeError, TaskError):
            return None
        self._store_screenshot(record, observation.step, annotated)
        return image

    def _store_screenshot(self, record: PhoneTaskRecord, step: int, data: bytes) -> None:
        """Keep a bounded local trail for diagnosis; the model gets the bytes."""
        try:
            folder = Path(self.runtime.data_root) / "phone"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{record.task_id}-{step}.png").write_bytes(data)
            files = sorted(folder.glob("*.png"), key=lambda path: path.stat().st_mtime, reverse=True)
            for old in files[40:]:
                old.unlink(missing_ok=True)
        except OSError:
            pass

    def _finish(self, record: PhoneTaskRecord, *, state: str, step: PhoneStep | None = None,
                history: tuple[dict, ...] | None = None, code: str = "") -> None:
        steps = history if history is not None else ((*record.history, step.document()) if step else record.history)
        self._save(replace(
            record, state=state, step=(step.step if step is not None else record.step),
            history=steps, inflight_at="", outcome=code))


def _history_step(item: dict) -> PhoneStep:
    """Restore the prompt-facing part of a step; unknown documents are skipped."""
    action = item.get("action") or {}
    try:
        parsed = PhoneAction(kind=str(action.get("kind") or "fail"),
                             text=str(action.get("text") or ""),
                             x=action.get("x") if type(action.get("x")) is int else None,
                             y=action.get("y") if type(action.get("y")) is int else None,
                             package=str(action.get("package") or ""),
                             key=str(action.get("key") or ""),
                             seconds=float(action.get("seconds") or 0.0),
                             reason=str(action.get("reason") or ""))
    except ValueError:
        parsed = PhoneAction("fail", reason="unreadable history")
    return PhoneStep(int(item.get("step") or 0), parsed, bool(item.get("ok")),
                     str(item.get("detail") or ""), str(item.get("code") or ""),
                     str(item.get("observation") or ""), str(item.get("at") or ""))
