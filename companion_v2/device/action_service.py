"""Run one supervised phone step at a time for the original private requester."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
import base64
import hashlib
import json
import logging
from pathlib import Path

from companion_v2.conversation.foreground import (BackgroundPromptTrigger, PromptRequest,
                                                   SealedForegroundContext)
from companion_v2.conversation.media.models import ImageDescriptor
from companion_v2.delivery.media import payload_hash
from companion_v2.delivery.plan import DeliveryContent
from companion_v2.delivery.service import DeliveryRequest
from companion_v2.execution.models import (Lane, ProviderBinding, SideEffect, TaskContext, TaskDefinition,
    TaskError, TaskRegistration, TaskResult, TaskSpec, digest, encode, require, stamp)
from companion_v2.execution.runner import CAPACITY_REFUSALS, ExactTaskRunner
from companion_v2.execution.service import outcome
from companion_v2.foundation.refs import ScopeKind, SourceRef, VersionRef
from companion_v2.model_gateway.models import ModelTaskProfile
from companion_v2.sources.privacy import redact_text

from .adb import AdbClient, AdbError
from .annotate import annotate_screen, image_mime_type, image_size
from .configuration import PhoneSettings, PhoneSettingsReader, load_settings
from .checkout import (CANCEL_COMMAND, MEITUAN_CUTLERY_SUBMIT, MEITUAN_EXPRESS_PAYMENT, QUICK_APPROVAL,
                       CheckoutDecision, checkout_boundary, checkout_frame, checkout_screenshot,
                       checkout_visible, meituan_cutlery_action, meituan_express_at, meituan_payment_result,
                       tap_position)
from .decider import (CHECKOUT_INSTRUCTION, INSTRUCTION, SCREEN_READ_INSTRUCTION,
                      DecisionError, decision_prompt, parse_decision)
from .prompt_image import PhonePromptImage
from .session import LAUNCH_SETTLE_SECONDS, PhoneAction, PhoneObservation, PhoneSession, PhoneStep, parse_action
from .skills import PhoneSkillMemory, SkillScreen, takeout_guidance

KIND = "phone.act"
OWNER = "device.phone"
NOTICE_OWNER = "phone.notice"
logger = logging.getLogger(__name__)
QUEUE_PREFIX = "phone.task.v1:"
MAX_TASKS = 32
MAX_GOAL_CHARS = 500
STEP_TIMEOUT_SECONDS = 120
MAX_STEPS = 20
STEP_DELAY_SECONDS = 6.0
REPLAY_SETTLE_SECONDS = 1.5
TASK_LIFETIME_SECONDS = 7200
NOTICE_LIFETIME = timedelta(minutes=5)
ROLE_INSTRUCTION = "保持原人格。这是你自己在用手机完成一件事，不是回复用户，也不对用户说话。"
SCREEN_CAPTURE_ERRORS = {
    "adb_screen_off": "手机处于息屏状态。请点亮后再查看。",
    "adb_unlock_required": "手机处于锁屏状态。请解锁后再查看。",
    "adb_no_device": "没有已授权的 ADB 手机。请在「手机使用」里检查连接。",
    "adb_multiple_devices": "连接了多台手机。请在「手机使用」里选择要读取的设备。",
    "adb_timeout": "手机未及时响应。请在「手机使用」里检查 ADB 连接。",
}


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
    shopping: bool = False
    checkout: dict | None = None
    notice: str = ""
    resume_checkout: bool = False
    screen_read: bool = False
    notice_at: str = ""

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
        if self.shopping:
            value["shopping"] = True
        if self.checkout is not None:
            value["checkout"] = self.checkout
        if self.notice:
            value["notice"] = self.notice
        if self.notice_at:
            value["notice_at"] = self.notice_at
        if self.resume_checkout:
            value["resume_checkout"] = True
        if self.screen_read:
            value["screen_read"] = True
        return value

    @classmethod
    def parse(cls, value: dict) -> "PhoneTaskRecord":
        required = {"version", "task_id", "context", "goal", "state", "step", "max_steps",
                    "history", "created_at", "next_at", "expires_at"}
        optional = {"inflight_at", "outcome", "shopping", "checkout", "notice", "notice_at",
                    "resume_checkout", "screen_read"}
        if type(value) is not dict or set(value) - (required | optional) or not required <= set(value):
            raise ValueError("stored phone task has an unexpected shape")
        if value["version"] != 1 or value["state"] not in ("active", "awaiting_confirmation", "done", "failed", "cancelled"):
            raise ValueError("stored phone task has an unknown version or state")
        return cls(str(value["task_id"]), json.dumps(value["context"], ensure_ascii=False, sort_keys=True),
                   str(value["goal"]), str(value["state"]), int(value["step"]), int(value["max_steps"]),
                   tuple(value["history"]), str(value["created_at"]), str(value["next_at"]),
                   str(value["expires_at"]), str(value.get("inflight_at") or ""),
                   str(value.get("outcome") or ""), value.get("shopping") is True,
                   value.get("checkout"), str(value.get("notice") or ""),
                   value.get("resume_checkout") is True, value.get("screen_read") is True,
                   str(value.get("notice_at") or ""))


class PhoneActionService:
    def __init__(self, runtime, *, notify=None, settings_reader=None,
                 client_factory=None) -> None:
        self.runtime = runtime
        self.notify = notify or (lambda: None)
        # A caller-supplied reader is for tests; production attaches the
        # configuration service and reads the role group through the task context.
        self._settings_reader = settings_reader
        self._client_factory = client_factory
        self.skills = PhoneSkillMemory(runtime)
        self.pending: dict[str, PhoneTaskRecord] = {}
        self._workers: dict[str, asyncio.Task] = {}
        self._settled_steps = set()
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
            """SELECT value FROM runtime_metadata WHERE key >= ? AND key < ?
               ORDER BY CASE WHEN json_extract(value, '$.state') IN ('active', 'awaiting_confirmation')
                        THEN 0 ELSE 1 END, json_extract(value, '$.created_at') DESC LIMIT ?""",
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

    def create_screen_read(self, context: TaskContext, question: str, *, request_id: str) -> dict:
        if not request_id:
            raise TaskError("phone_screen_request_invalid", "a screen read needs its original message ID")
        return self.create(context, question, max_steps=1, screen_request_id=request_id)

    def create(self, context: TaskContext, goal: str, *, delay_minutes: int = 0,
               max_steps: int | None = None, shopping: bool = False, resume_checkout: bool = False,
               screen_request_id: str = "") -> dict:
        if context.scope.kind is not ScopeKind.PRIVATE or context.audience.recipients != (context.actor.subject,):
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
        screen_read = bool(screen_request_id)
        identity = [context.key, "screen-read", screen_request_id] if screen_read else [context.key, goal, stamp(now)]
        identifier = "phone-" + digest(identity)[:32]
        record = PhoneTaskRecord(
            identifier, json.dumps(json.loads(encode(context)), ensure_ascii=False, sort_keys=True),
            goal, "active", 0, steps, (), stamp(now),
            stamp(now + timedelta(minutes=int(delay_minutes))),
            stamp(now + timedelta(minutes=int(delay_minutes), seconds=TASK_LIFETIME_SECONDS)),
            shopping=shopping or resume_checkout, resume_checkout=resume_checkout, screen_read=screen_read)
        with self.runtime.transaction():
            prior = self._read(identifier) if screen_read else None
            if prior is not None:
                record = prior
            else:
                records = tuple(item for item in self._records() if datetime.fromisoformat(item.expires_at) > now)
                previous = None
                if resume_checkout:
                    previous = next((item for item in records if item.shopping
                                     and context.same_window(TaskContext.restore(json.loads(item.context_json)))), None)
                if any(item.state == "active" or (item.state == "awaiting_confirmation"
                       and item is not previous and not screen_read) for item in records):
                    raise TaskError("phone_busy", "the phone already has an active task or checkout")
                if resume_checkout:
                    if previous is None or previous.state not in ("awaiting_confirmation", "failed"):
                        raise TaskError("phone_checkout_unavailable", "没有可从当前结账页接续的手机订单。")
                    if previous.state == "awaiting_confirmation":
                        self._save(replace(previous, state="cancelled", checkout=None, notice="",
                                           outcome="phone_checkout_revised"))
                self._save(record)
        self.notify()
        return {"task_id": identifier, "state": record.state, "goal": record.goal, "max_steps": record.max_steps,
                "starts_at": record.next_at}

    def cancel(self, task_id: str) -> bool:
        record = self._read(task_id)
        if record is None or record.state not in ("active", "awaiting_confirmation"):
            return False
        self._save(replace(record, state="cancelled", inflight_at="", checkout=None, notice=""))
        return True

    def status(self, context, task_id):
        record = self._read(task_id) if isinstance(task_id, str) and len(task_id) <= 64 else None
        if record is None or TaskContext.restore(json.loads(record.context_json)).key != context.key:
            raise TaskError("phone_task_unavailable", "no phone task belongs to this private route")
        return {"task_id": record.task_id, "state": record.state, "step": record.step,
                "max_steps": record.max_steps, "code": record.outcome, "notice": record.notice,
                "reused_steps": sum(bool(item.get("skill_reused") and item.get("skill_verified"))
                                    for item in record.history)}

    def answer_checkout(self, context, token, *, approve, received_at=None):
        now = self.tasks.now()
        require(self.tasks.check_context_access(context, action=KIND, observed_at=now))
        with self.runtime.transaction():
            matching = []
            for record in self._records():
                checkout = record.checkout
                if record.state != "awaiting_confirmation" or not checkout:
                    continue
                if token is not None and checkout["token"] != token:
                    continue
                original = TaskContext.restore(json.loads(record.context_json))
                if (not context.same_window(original)
                        or datetime.fromisoformat(checkout["expires_at"]) <= now):
                    continue
                if token is None:
                    receipt = self.runtime.read_one("""SELECT delivered_at FROM background_deliveries
                        WHERE owner = ? AND origin_key = ? AND state = 'delivered'
                        AND json_extract(request_json, '$.logical_key') = ?""",
                        (NOTICE_OWNER, context.key, self._notice_key(record)))
                    if (receipt is None or not receipt["delivered_at"] or received_at is None
                            or datetime.fromisoformat(receipt["delivered_at"]) > received_at):
                        continue
                matching.append(record)
            if len(matching) == 1:
                record = matching[0]
                if approve and not self.available(context):
                    raise TaskError("phone_disabled", "the phone channel is disabled")
                self._save(replace(record, state="active" if approve else "cancelled",
                                   next_at=stamp(now), notice="",
                                   checkout={**record.checkout, "approved": stamp(now)} if approve else None))
                self.notify()
                return
        raise TaskError("phone_checkout_unavailable", "there is no matching unexpired checkout")

    @outcome
    def delivery_content(self, request, at):
        record = self._read(request.source_ref)
        if (record is None or not record.notice or not self.available(request.context)
                or request.logical_key != self._notice_key(record)
                or TaskContext.restore(json.loads(record.context_json)) != request.context):
            raise TaskError("phone_notice_unavailable", "the original private phone notice is unavailable")
        if record.state == "awaiting_confirmation" and datetime.fromisoformat(record.checkout["expires_at"]) <= at:
            raise TaskError("phone_notice_expired", "the original checkout expired")
        return self._notice_content(record, request.context)

    @staticmethod
    def _checkout_picture(task_id, step, descriptor):
        identifier = "phone-checkout-" + digest([task_id, step, descriptor.content_hash])[:32]
        suffix = ".jpg" if descriptor.mime_type == "image/jpeg" else ".png"
        return identifier, identifier + suffix

    def _notice_content(self, record, context):
        picture = record.checkout.get("screenshot") if record.state == "awaiting_confirmation" else None
        if picture is None:
            return DeliveryContent(record.notice)
        try:
            descriptor = ImageDescriptor(**picture["descriptor"])
            identifier, filename = self._checkout_picture(record.task_id, record.step, descriptor)
            source = SourceRef(identifier, OWNER, context.scope, VersionRef(identifier, 1),
                               datetime.fromisoformat(picture["issued_at"]))
            with (Path(self.runtime.data_root) / "phone" / filename).open("rb") as stream:
                data = stream.read(descriptor.byte_count + 1)
            image = PhonePromptImage(source, source, descriptor, base64.b64encode(data).decode("ascii"))
        except (OSError, TypeError, KeyError, ValueError, TaskError) as error:
            raise TaskError("phone_checkout_screenshot_unavailable", "the original checkout screenshot is unavailable") from error
        return DeliveryContent(record.notice, (image,))

    def _queue_notice(self, record, at):
        if self.app is None or not record.notice or not record.notice_at:
            return
        issued = datetime.fromisoformat(record.notice_at)
        deadline = issued + NOTICE_LIFETIME
        if at >= deadline:
            return
        key = self._notice_key(record)
        if self.app.outbound.contains("outbound-" + digest([NOTICE_OWNER, key])):
            return
        context = TaskContext.restore(json.loads(record.context_json))
        try:
            content = self._notice_content(record, context)
        except TaskError as error:
            logger.warning("Phone notice will be sent as text: %s", error.code)
            checkout = {key: value for key, value in record.checkout.items() if key != "screenshot"}
            record = replace(record, checkout=checkout)
            self._save(record)
            content = DeliveryContent(record.notice)
        request = DeliveryRequest(NOTICE_OWNER, key,
                                  context, record.task_id, payload_hash(content.text, content.images),
                                  issued, deadline)
        result = self.app.outbound.queue(request)
        if result.failure:
            logger.warning("Phone notice could not be queued: %s", result.failure.code)
        self.notify()

    @staticmethod
    def _notice_key(record):
        return digest([record.task_id, record.step, record.notice])

    # -- scheduling ----------------------------------------------------

    @outcome
    def advance(self):
        """Submit at most one step for each due record; never observes the phone here."""
        if self._closed or self.runner is None or self.tasks is None or self._workers:
            return 0
        return self.launch_prepared_step(self.prepare_due_step())

    def prepare_due_step(self):
        now = self.tasks.now()
        for record in self._records():
            if record.state == "awaiting_confirmation":
                if datetime.fromisoformat(record.checkout["expires_at"]) <= now:
                    self._save(replace(record, state="cancelled", checkout=None, notice="",
                                       outcome="phone_checkout_expired"))
                else:
                    self._queue_notice(record, now)
                continue
            if record.state != "active":
                self._queue_notice(record, now)
                continue
            try:
                due = datetime.fromisoformat(record.next_at)
                expires = datetime.fromisoformat(record.expires_at)
            except ValueError:
                continue
            if now < due:
                continue
            if now >= expires or (record.step >= record.max_steps and not record.checkout):
                self._finish(record, state="failed", code="phone_task_expired",
                             notice="手机任务已超时，请核对手机上的实际进度。")
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
                self._finish(record, state="failed", code="phone_step_stalled",
                             notice="上一步手机操作的结果未确认，请查看手机上的实际进度。")
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
            return spec
        return None

    def launch_prepared_step(self, spec):
        if spec is None:
            return 0
        identifier = spec.task_id
        if self._closed:
            self.pending.pop(identifier, None)
            return 0
        if identifier in self._workers:
            return 0
        worker = asyncio.create_task(self.runner.run(spec.context, identifier), name="companion:phone:" + identifier)
        self._workers[identifier] = worker
        worker.add_done_callback(lambda done: self._step_done(identifier, done))
        self.notify()
        return 1

    def _step_done(self, identifier, worker) -> None:
        self._workers.pop(identifier, None)
        record = self.pending.pop(identifier, None)
        self._settled_steps.discard(identifier)
        if worker.cancelled():
            return
        try:
            error = worker.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            logger.warning("[CompanionV2][phone] step task failed step=%s error_type=%s",
                           identifier, type(error).__name__)
        if record is not None:
            stored = self._read(record.task_id)
            if stored and stored.state == "active" and stored.step == record.step and stored.inflight_at:
                self._finish(stored, state="failed", code="phone_step_stalled",
                             notice="手机操作中断了。请先查看手机上的实际进度。")
        self.notify()

    @property
    def active_handles(self):
        return (self.runner.active_handles if self.runner else ()) + tuple(self._workers)

    def cancel_running(self):
        self._closed = True
        for worker in tuple(self._workers.values()):
            worker.cancel()

    async def close(self):
        self.cancel_running()
        if self._workers:
            await asyncio.gather(*tuple(self._workers.values()), return_exceptions=True)
        if self.runner:
            await self.runner.close()

    # -- one step ------------------------------------------------------

    @outcome
    def validate(self, spec, at):
        record = self.pending.get(spec.task_id)
        if record is None:
            raise TaskError("phone_task_unavailable", "the prepared phone step is no longer held")
        stored = self._read(record.task_id)
        if (stored is None or stored.state == "cancelled"
                or (spec.task_id not in self._settled_steps
                    and (stored.state != "active" or stored.step != record.step))):
            raise TaskError("phone_task_changed", "the phone task changed before its step ran")
        settings = self.settings(spec.context)
        if not settings.enabled:
            raise TaskError("phone_disabled", "the phone channel was disabled before this step")
        if not settings.permits(spec.context):
            raise TaskError("phone_subject_denied", "this window is no longer in the phone allowlist")
        require(self.tasks.check_context_access(spec.context, action=KIND, observed_at=at))
        return True

    async def generate(self, run):
        record = self.pending.get(run.task.task_id)
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
            client = await asyncio.to_thread(self._client_factory, settings) if self._client_factory else await asyncio.to_thread(settings.client)
        except (AdbError, ValueError) as error:
            self._finish(stored, state="failed", code=getattr(error, "code", "phone_device_unavailable"),
                         notice="手机连接失败，请检查手机和 ADB 连接。")
            raise TaskError("phone_device_unavailable", str(error)) from error
        session = PhoneSession(client, goal=stored.goal, max_steps=stored.max_steps,
                               max_seconds=STEP_TIMEOUT_SECONDS - 10, allowed=settings.allowed,
                               ime=settings.unicode_input,
                               ime_restore_seconds=settings.ime_restore_seconds)
        at = self.tasks.now()
        try:
            if stored.screen_read:
                observation = await asyncio.to_thread(session.observe, stored.step, read_only=True)
            else:
                observation = await asyncio.to_thread(session.observe, stored.step)
        except AdbError as error:
            if stored.screen_read:
                notice = SCREEN_CAPTURE_ERRORS.get(error.code, "这次没能取得手机当前画面。")
            elif error.code == "adb_screenshot_secure":
                notice = "当前手机页面开启了隐私防截屏保护，无法读取画面。"
            else:
                notice = ("手机已亮屏，请在手机上解锁后重新发起任务。" if error.code == "adb_unlock_required"
                          else "读取手机画面失败，请检查手机。")
            self._finish(stored, state="failed", code=error.code, notice=notice)
            raise TaskError(error.code, str(error)) from error
        if stored.screen_read:
            return await self._describe_screen(run, stored, observation, at)
        if stored.checkout:
            return await self._approved_checkout(run, stored, observation, session, settings)
        context = run.task.spec.context
        screen = SkillScreen.capture(observation)
        observed = self._observe_previous_tap(stored, screen, observation)
        if observed is not stored:
            self.skills.observe_tap(context, observed.history[-1], at)
            stored = observed
        avoided = self.skills.ineffective_targets(context, screen, at)
        if screen is not None and "tap" in settings.allowed:
            reused = self.skills.recall(context, stored.goal, stored.shopping, screen, observation, stored.history,
                                       avoided=avoided)
            if reused is not None:
                return await self._execute(run, stored, observation, session, reused,
                                           skill_tap=screen.tap(observation, reused), reused=True)
        history = tuple(_history_step(item) for item in stored.history)
        image = await asyncio.to_thread(self._prompt_image, record, observation, run, at)
        ordering = ""
        allowed = settings.allowed
        if stored.shopping and "tap" in allowed:
            ordering = CHECKOUT_INSTRUCTION + "\n" + takeout_guidance(stored.goal)
            allowed += ("confirm_checkout",)
        if stored.resume_checkout:
            ordering += ("\n本单从手机当前结账页接续。先核对现有订单是否与目标相符，调整本次要求的配送、红包等选项，"
                         "完成后用 confirm_checkout 发新的确认单。当前没有这笔订单时，用 fail 请用户打开对应的结账页。")
        if avoided:
            positions = "、".join(f"({x}, {y})" for x, y in (screen.targets[target] for target in avoided[-4:]))
            ordering += (f"\n操作经验：相同页面上点击 {positions} 曾没有产生可观察的变化。"
                         "优先根据当前画面寻找其他入口；当前状态表明点击会生效时可以重新判断。")
        request = self._prompt_request(run, at, image,
            INSTRUCTION + ordering +
            "\n本轮允许的动作：" + ", ".join(sorted(allowed)) +
            "\n\n" + decision_prompt(stored.goal, observation, history, screenshot_attached=image is not None))
        try:
            completion = await self.gateway.generate(run, request)
        except TaskError as error:
            if error.code == "phone_model_rate_limited":
                self._finish(stored, state="failed", code=error.code,
                             notice="手机操作的视觉模型被限流，本次任务已停止。请在模型与连接中切换手机任务的模型后重试。")
            else:
                self._finish(stored, state="failed", code="phone_model_failed",
                             notice="手机视觉决策失败，本次操作已暂停。")
            raise
        text = completion.draft.text if completion.draft is not None else ""
        try:
            action = parse_decision(text, checkout=stored.shopping)
        except DecisionError as error:
            self._finish(stored, state="failed", code="phone_decision_invalid", notice="手机操作决策格式出错，当前页面已保留。")
            raise TaskError("phone_decision_invalid", str(error)) from error
        if isinstance(action, CheckoutDecision):
            return await self._offer_checkout(run, stored, observation, action, settings)
        if action.kind not in settings.allowed:
            self._finish(stored, state="failed", code="phone_action_forbidden",
                         notice="当前手机配置没有开放这一步操作。")
            raise TaskError("phone_action_forbidden", "the configured phone channel forbids this action")
        if checkout_boundary(observation, action):
            step = PhoneStep(stored.step + 1, action, False,
                             "付款操作未执行：需要用户先确认商品、规格、金额和收货地址",
                             "phone_payment_confirmation_required",
                             observation.screenshot_sha256, started_at=stamp(self.tasks.now()))
            return self._commit(run, self._with_step(stored, step, state="failed",
                outcome="phone_payment_confirmation_required", notice="手机已停在结账环节。还需要核对这笔订单的明细。"))
        started = stamp(self.tasks.now())
        if action.kind == "done":
            step = PhoneStep(stored.step + 1, action, True, action.reason or "完成",
                             observation_sha256=observation.screenshot_sha256, started_at=started)
            notice = redact_text(step.detail) if stored.shopping else f"手机任务已完成。{redact_text(step.detail)}"
            return self._commit(run, self._with_step(stored, step, state="done", notice=notice),
                                learn=not stored.shopping)
        if action.kind == "fail":
            step = PhoneStep(stored.step + 1, action, False, action.reason or "放弃",
                             code="phone_task_failed", observation_sha256=observation.screenshot_sha256,
                             started_at=started)
            notice = redact_text(step.detail) if stored.shopping else f"手机任务未完成。{redact_text(step.detail)}"
            return self._commit(run, self._with_step(stored, step, state="failed",
                outcome=step.code, notice=notice))
        return await self._execute(run, stored, observation, session, action,
                                   skill_tap=screen.tap(observation, action) if screen else None)

    @staticmethod
    def _prompt_request(run, at, image, instruction):
        context = run.task.spec.context
        trigger = BackgroundPromptTrigger(digest([run.task.task_id, "phone-step"]),
                                          context.actor.principal, context.scope, at)
        return PromptRequest(trigger, ROLE_INSTRUCTION, SealedForegroundContext((), (), ()),
                             instruction, context.role_binding, output_format="phone_action",
                             response_format="json_object", images=(image,) if image is not None else ())

    async def _describe_screen(self, run, record, observation, at):
        image = await asyncio.to_thread(self._prompt_image, record, observation, run, at)
        if image is None:
            self._finish(record, state="failed", code="phone_screen_image_unavailable", notice="本次未取得可读取的手机截图。")
            raise TaskError("phone_screen_image_unavailable", "the current screen image is unavailable")
        request = self._prompt_request(run, at, image, SCREEN_READ_INSTRUCTION +
            f"\ngoal: {record.goal}\n\nobservation:\n{observation.prompt_text(annotated=False)}")
        try:
            completion = await self.gateway.generate(run, request)
        except TaskError as error:
            if error.code in ("model_capability_mismatch", "model_provider_unavailable", "model_task_unregistered"):
                notice = "已取得手机画面。请在「模型与连接」为手机操作配置支持图片输入的模型。"
            elif error.code == "phone_model_rate_limited":
                notice = "已取得手机画面，视觉模型暂时被限流。"
            else:
                notice = "已取得手机画面，视觉模型这次未能返回解读。"
            self._finish(record, state="failed", code=error.code, notice=notice)
            raise
        try:
            result = parse_decision(completion.draft.text if completion.draft is not None else "")
            if result.kind not in ("done", "fail") or not result.reason:
                raise DecisionError("phone_screen_result_invalid", "a screen read needs an answer or a reason")
        except DecisionError as error:
            self._finish(record, state="failed", code=error.code, notice="视觉模型未返回可用的手机画面解读。")
            raise TaskError(error.code, str(error)) from error
        answer = redact_text(result.reason)
        ok = result.kind == "done"
        step = PhoneStep(1, PhoneAction(result.kind), ok, answer, "" if ok else "phone_screen_unclear",
                         observation.screenshot_sha256, started_at=stamp(at))
        return self._commit(run, self._with_step(record, step, state="done" if ok else "failed",
                                                outcome=step.code, notice=f"手机当前画面\n{answer}"))

    @staticmethod
    def _observe_previous_tap(record, screen, observation):
        if screen is None or not record.history:
            return record
        previous = record.history[-1]
        tap = previous.get("skill_tap")
        if not tap or not previous.get("ok"):
            return record
        if tap["screen"] != screen.key:
            previous = {**previous, "skill_verified": True}
        elif not screen.checkout and previous.get("observation") == observation.screenshot_sha256:
            previous = {**previous, "ok": False, "code": "phone_action_ineffective",
                        "detail": "点击后页面没有变化，请按当前画面重新判断"}
        else:
            return record
        return replace(record, history=(*record.history[:-1], previous))

    async def _execute(self, run, stored, observation, session, action, *, skill_tap=None, reused=False,
                       next_checkout=None):
        # Mark the action before touching the device.  If this process dies
        # between the tap and the receipt, the step stays inflight and the
        # scheduler retires the whole task as an unknown action instead of
        # repeating a side effect nobody observed.
        run.begin_effect()
        started = stamp(self.tasks.now())
        pending_step = PhoneStep(stored.step + 1, action, False, "动作已开始，结果未记录",
                                 "phone_action_started", observation.screenshot_sha256,
                                 started_at=started)
        self._save(replace(stored, history=(*stored.history, pending_step.document())))
        try:
            if (action.kind == "tap_text" and checkout_visible(observation)
                    and tap_position(observation, action) is None):
                raise AdbError("adb_text_not_found", "当前结账画面无此文字节点，请根据截图重新定位")
            detail = await asyncio.to_thread(session.execute, action)
            ok, code = True, ""
        except AdbError as error:
            detail, ok, code = str(error), False, error.code
        step = PhoneStep(stored.step + 1, action, ok, detail, code,
                         observation.screenshot_sha256, started_at=started)
        completed = self._with_step(stored, step, skill_tap=skill_tap, reused=reused)
        consecutive = bool(stored.history and not stored.history[-1]["ok"])
        if stored.checkout and not ok:
            self._finish(completed, state="failed", code="phone_checkout_outcome_unknown",
                         notice="结账返回异常，请在手机上核对订单状态。")
            return TaskResult("uncertain", "unknown", result_ref=stored.task_id)
        if not ok and consecutive:
            return self._commit(run, replace(completed, state="failed",
                outcome="phone_repeated_failure", notice="连续两步操作失败，请查看当前手机页面。"), began=True)
        if stored.step + 1 >= stored.max_steps and next_checkout is None:
            return self._commit(run, replace(completed, state="failed",
                outcome="phone_exhausted", notice="手机操作步数已用完，请查看当前页面进度。"), began=True)
        if action.kind == "launch":
            delay = LAUNCH_SETTLE_SECONDS
        else:
            delay = REPLAY_SETTLE_SECONDS if reused or next_checkout is not None else STEP_DELAY_SECONDS
        return self._commit(run, replace(completed, checkout=next_checkout,
            next_at=stamp(self.tasks.now() + timedelta(seconds=delay))), began=True)

    @staticmethod
    def _with_step(record, step, *, skill_tap=None, reused=False, **changes):
        item = step.document()
        if skill_tap is not None:
            item["skill_tap"] = skill_tap
        if reused:
            item["skill_reused"] = True
        return replace(record, step=step.step, history=(*record.history, item),
                       inflight_at="", **changes)

    def _commit(self, run, record, *, began=False, learn=False):
        at = self.tasks.now()
        if record.notice:
            record = replace(record, notice_at=stamp(at))
        with self.runtime.transaction():
            if not began:
                run.begin_effect()
            run.record_operation(run.task.task_id)
            self._save(record)
            if learn:
                self.skills.remember(run.task.spec.context, record.goal, record.shopping, record.history, at)
            self._settled_steps.add(run.task.task_id)
        self._queue_notice(record, at)
        return TaskResult("succeeded", "committed", result_ref=record.task_id,
                          operation_id=run.task.task_id, receipt_ref=run.task.task_id)

    async def _offer_checkout(self, run, record, observation, decision, settings):
        position = tap_position(observation, decision.button)
        if (position is None or not observation.screenshot or decision.button.kind not in settings.allowed
                or "tap" not in settings.allowed):
            raise TaskError("phone_checkout_unverifiable", "the payment target is not available on this screen")
        frame = checkout_frame(observation, form=True)
        token = digest([record.task_id, record.step, frame, decision.document()])[:10]
        now = self.tasks.now()
        expires = min(datetime.fromisoformat(record.expires_at), now + timedelta(minutes=10))
        checkout = {"token": token, "position": list(position), "frame": frame, "form_frame": True,
                    "serial": settings.serial or settings.connect, "expires_at": stamp(expires)}
        if self.app.outbound.sender.supports_images:
            try:
                screenshot = await asyncio.to_thread(checkout_screenshot, observation.screenshot)
                _, filename = self._checkout_picture(record.task_id, record.step + 1, screenshot.descriptor)
                if await asyncio.to_thread(self._store_screenshot, filename, screenshot.content):
                    checkout["screenshot"] = {"descriptor": asdict(screenshot.descriptor), "issued_at": stamp(now)}
            except (OSError, ValueError, TaskError) as error:
                logger.warning("Checkout screenshot could not be prepared: %s", getattr(error, "code", type(error).__name__))
        notice = (f"手机上的订单已准备好，请核对。\n"
                  f"{decision.merchant} · {decision.item}\n{decision.options}\n"
                  f"实付 ¥{decision.amount} · {decision.address_hint}\n"
                  f"10 分钟内回复「{QUICK_APPROVAL}」，我会核对订单后点击「{decision.button.text}」。"
                  f"取消请回复「{CANCEL_COMMAND}」。")
        step = PhoneStep(record.step + 1, PhoneAction("wait_text", text=decision.button.text),
                         True, "等待用户确认这一笔订单", "phone_checkout_pending",
                         observation.screenshot_sha256, started_at=stamp(now))
        first_checkout = not any(item.get("code") == "phone_checkout_pending" for item in record.history)
        return self._commit(run, self._with_step(record, step, state="awaiting_confirmation",
                                                checkout=checkout, notice=notice), learn=first_checkout)

    async def _approved_checkout(self, run, record, observation, session, settings):
        checkout = record.checkout
        if (not checkout.get("approved") or datetime.fromisoformat(checkout["expires_at"]) <= self.tasks.now()
                or checkout["serial"] != (settings.serial or settings.connect) or "tap" not in settings.allowed
                or not observation.screenshot):
            notice = ("付款步骤已发出，当前无法核对订单结果，请查看美团订单。"
                      if checkout.get("meituan_stage") else "订单画面或设备已变化。请重新发起任务并核对订单。")
            return self._commit(run, replace(record, state="failed", checkout=None, inflight_at="",
                outcome="phone_checkout_changed", notice=notice))
        if checkout.get("meituan_stage"):
            return await self._continue_meituan_checkout(run, record, observation, session)
        x, y = checkout["position"]
        if (checkout["frame"] != checkout_frame(observation, form=checkout.get("form_frame") is True)
                or not (0 <= x < observation.width and 0 <= y < observation.height)):
            return self._commit(run, replace(record, state="failed", checkout=None, inflight_at="",
                outcome="phone_checkout_changed", notice="订单画面或设备已变化。请重新发起任务并核对订单。"))
        next_checkout = {**checkout, "meituan_stage": "express_tapped"} if meituan_express_at(observation, x, y) else None
        return await self._execute(run, record, observation, session,
                                   PhoneAction("tap", x=x, y=y, reason="用户已确认这笔订单"),
                                   next_checkout=next_checkout)

    async def _continue_meituan_checkout(self, run, record, observation, session):
        checkout = record.checkout
        stage = checkout["meituan_stage"]
        if stage in {"express_tapped", "cutlery_selected"}:
            needs_cutlery = "需要餐具" in record.goal and "不需要餐具" not in record.goal
            action = meituan_cutlery_action(observation, choose=stage == "express_tapped", needs_cutlery=needs_cutlery)
            if action is not None and tap_position(observation, action) is not None:
                next_stage = "submitted" if action.text == MEITUAN_CUTLERY_SUBMIT else "cutlery_selected"
                return await self._execute(run, record, observation, session, action,
                                           next_checkout={**checkout, "meituan_stage": next_stage})

        # Once a submit tap may have reached the app, this path only reads the
        # next screen. Even an unchanged checkout must not trigger a second tap.
        paid = meituan_payment_result(observation)
        if paid:
            state, code, notice = "done", "phone_order_paid", f"美团显示「{paid}」。"
        else:
            button = MEITUAN_CUTLERY_SUBMIT if stage == "submitted" else MEITUAN_EXPRESS_PAYMENT
            state, code = "failed", "phone_checkout_outcome_unknown"
            notice = f"已点击「{button}」。当前画面无法确认付款结果，请先查看美团订单状态。"
        return self._commit(run, replace(record, state=state, outcome=code, notice=notice,
                                         checkout=None, inflight_at=""))

    def _prompt_image(self, record: PhoneTaskRecord, observation: PhoneObservation, run, at):
        if not observation.screenshot:
            return None
        try:
            if record.screen_read:
                captured = checkout_screenshot(observation.screenshot)
                content, descriptor = captured.content, captured.descriptor
            else:
                content = annotate_screen(observation.screenshot, observation.elements, boxes=observation.stable)
                width, height = image_size(content)
                descriptor = ImageDescriptor(0, image_mime_type(content), width, height, len(content),
                                             hashlib.sha256(content).hexdigest())
            identifier = "phone-shot-" + digest([record.task_id, observation.step])[:32]
            source = SourceRef(identifier, "device.phone", run.task.spec.context.scope,
                               VersionRef(identifier, 1), at)
            image = PhonePromptImage(source, source, descriptor, base64.b64encode(content).decode("ascii"))
        except (ImportError, OSError, ValueError, TypeError, TaskError):
            return None
        if not record.screen_read:
            suffix = ".jpg" if descriptor.mime_type == "image/jpeg" else ".png"
            self._store_screenshot(f"{record.task_id}-{observation.step}{suffix}", content)
        return image

    def _store_screenshot(self, filename: str, data: bytes) -> bool:
        folder = Path(self.runtime.data_root) / "phone"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / filename).write_bytes(data)
        except OSError:
            return False
        try:
            files = sorted((*folder.glob("*.png"), *folder.glob("*.jpg")),
                           key=lambda path: path.stat().st_mtime, reverse=True)
            for old in files[40:]:
                old.unlink(missing_ok=True)
        except OSError:
            pass
        return True

    def _finish(self, record, *, state, code, notice=""):
        at = self.tasks.now()
        finished = replace(record, state=state, inflight_at="", checkout=None, outcome=code,
                           notice=notice, notice_at=stamp(at) if notice else "")
        self._save(finished)
        self._queue_notice(finished, at)


def _history_step(item: dict) -> PhoneStep:
    """Restore the prompt-facing part of a step; unknown documents are skipped."""
    try:
        parsed = parse_action(item.get("action") or {})
    except ValueError:
        parsed = PhoneAction("fail", reason="unreadable history")
    return PhoneStep(int(item.get("step") or 0), parsed, bool(item.get("ok")),
                     str(item.get("detail") or ""), str(item.get("code") or ""),
                     str(item.get("observation") or ""), str(item.get("at") or ""))
