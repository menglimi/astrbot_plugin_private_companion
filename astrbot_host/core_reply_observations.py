"""Asynchronous local observation jobs, outside AstrBot generation and send."""

import asyncio
import logging
from datetime import timedelta

from companion_v2.execution.models import digest, stamp
from companion_v2.foundation.refs import ScopeKind
from companion_v2.identity_policy.interventions import (CALENDAR_INTERVENTION,
    REST_WAKE_INTERVENTION, STATE_INTERVENTION, authorized_capabilities)
from companion_v2.conversation.native_observations import NativeConversationObservations


logger = logging.getLogger(__name__)
INTERVENTION_DEBOUNCE_SECONDS = 6
INTERVENTION_INPUT_WINDOW = timedelta(minutes=3)


class CoreReplyObservations:
    def __init__(self, session):
        self.session = session
        self.owner = NativeConversationObservations(session.application.messages)
        from .core_reply_media import bind_native_quoted_image_sources
        self._quoted_image_guard = bind_native_quoted_image_sources(session.application)
        self._pending = set()
        self._tails = {}
        self._intervention_latest = {}
        self._intervention_locks = {}
        self._intervention_tasks = set()
        self._closed = False

    def _schedule(self, work, *, context_key=None, before=None):
        if self._closed:
            return None

        preceding = self._tails.get(context_key) if context_key is not None else None

        async def record():
            try:
                for prior in (preceding, before):
                    if prior is not None:
                        await asyncio.shield(prior)
                return await asyncio.to_thread(work)
            except Exception as error:
                logger.warning("[CompanionV2] 核心聊天后台观察失败 error_type=%s code=%s",
                               type(error).__name__, getattr(error, "code", "unavailable"))
                return None

        task = asyncio.create_task(record(), name="companion-v2-native-observation")
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
        if context_key is not None:
            self._tails[context_key] = task
            def release(done):
                if self._tails.get(context_key) is done:
                    self._tails.pop(context_key, None)
            task.add_done_callback(release)
        return task

    def submit(self, work):
        """Schedule local telemetry work with the same shutdown drain as sources."""
        return self._schedule(work)

    def input(self, context, native_input, *, turn_id, expression_signal_ids=()):
        intervention_ticket = None
        if context.scope.kind is ScopeKind.PRIVATE and native_input.text.strip():
            intervention_ticket = object()
            self._intervention_latest[context.key] = intervention_ticket
            note_input = getattr(self.session.application.state_maintenance, "note_input", None)
            if callable(note_input):
                note_input(context, native_input.event_id)

        def record():
            self.session.authorize(context)
            reference = self.owner.capture_input(context, native_input, turn_id=turn_id)
            if expression_signal_ids:
                from companion_v2.execution.models import digest
                from companion_v2.expression.feedback import stage_injection
                expression = self.session.application.messages.expression_style_context
                try:
                    with self.session.runtime.transaction() as connection:
                        stage_key = "astrbot.core-expression-stage." + digest(turn_id)
                        if connection.execute("SELECT 1 FROM runtime_metadata WHERE key = ?", (stage_key,)).fetchone() is None:
                            stage_injection(self.session.runtime, batch_id=turn_id,
                                owner_key=digest(expression.catalog.role_owner(context)), scope_key=digest(context.scope),
                                signal_ids=expression_signal_ids, at=native_input.at)
                            connection.execute("INSERT INTO runtime_metadata VALUES (?, ?)", (stage_key, "true"))
                except Exception as error:
                    logger.warning("[CompanionV2] 核心表达注入观察降级 error_type=%s", type(error).__name__)
            return reference

        pending = self._schedule(record, context_key=context.key)
        if pending is not None:
            def observed(done):
                if done.cancelled() or done.exception() is not None or done.result() is None:
                    return
                self.session.application.notify()
                semantic = getattr(self.session.application.tools, "semantic", None)
                schedule = getattr(semantic, "schedule_native_input", None)
                if callable(schedule):
                    try:
                        schedule(context, turn_id)
                    except Exception as error:
                        logger.warning("[CompanionV2] 核心聊天语义观察调度降级 error_type=%s", type(error).__name__)
                if (intervention_ticket is not None
                        and self._intervention_latest.get(context.key) is intervention_ticket):
                    self._queue_intervention(context, intervention_ticket)
            pending.add_done_callback(observed)
        return pending

    def _queue_intervention(self, context, ticket, *, delay=INTERVENTION_DEBOUNCE_SECONDS):
        if self._closed or context.scope.kind is not ScopeKind.PRIVATE:
            return None

        async def observe():
            try:
                await asyncio.sleep(delay)
                lock = self._intervention_locks.setdefault(context.key, asyncio.Lock())
                async with lock:
                    if self._closed or self._intervention_latest.get(context.key) is not ticket:
                        return
                    await self._classify_intervention(context, ticket)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("[CompanionV2] 私聊干预复核降级 error_type=%s", type(error).__name__)

        task = asyncio.create_task(observe(), name="companion-v2-role-intervention")
        self._intervention_tasks.add(task)
        self._pending.add(task)
        task.add_done_callback(self._intervention_tasks.discard)
        task.add_done_callback(self._pending.discard)
        return task

    def _intervention_inputs(self, context, at):
        from companion_v2.conversation.native_history import dialogue

        messages = dialogue(self.session.application.messages, context, at,
                            start=stamp(at - INTERVENTION_INPUT_WINDOW), end=stamp(at),
                            limit=16, max_chars=8000)
        text = "\n".join(f"{item['speaker']}: {item['text']}" for item in messages)
        return messages, text

    def _intervention_material(self, context, capabilities, at):
        if CALENDAR_INTERVENTION not in capabilities:
            return {}
        try:
            inventory = self.session.application.calendar.inventory(context, limit=30)
        except Exception:
            return {"calendar_unavailable": True}
        plans = []
        for item in inventory.get("items", ()):
            if item.get("kind") != "role_plan" or item.get("state") != "accepted":
                continue
            plans.append({name: item.get(name) for name in (
                "id", "revision", "title", "detail", "schedule", "duration_minutes", "exceptions")})
            if len(plans) >= 12:
                break
        return {"role_plans": plans, "partial": bool(inventory.get("next_cursor") or inventory.get("skipped"))}

    def _resting(self, context, at):
        from companion_v2.role_work.life import rest_gate
        settings = rest_gate.read_settings(self.session.runtime, context)
        return rest_gate.sleep_context(self.session.runtime, context, now=at, settings=settings)["resting"]

    async def _classify_intervention(self, context, ticket):
        app = self.session.application
        binding = app.state_maintenance.bindings.get(context.key)
        if binding is None or binding[0] != context:
            return
        await asyncio.to_thread(self.session.authorize, context)
        if binding[2] is not None:
            await binding[2](context.scope, context.role_binding, binding[1])
        at = self.session.now()
        capabilities = await asyncio.to_thread(authorized_capabilities, app.tasks, context, at=at)
        domains = {name for capability, name in ((STATE_INTERVENTION, "state"),
            (CALENDAR_INTERVENTION, "calendar")) if capability in capabilities}
        state_settings = app.role_state.settings(context)[0]
        if not state_settings.get("enabled"):
            domains.discard("state")
        if not state_settings.get("calendar_enabled"):
            domains.discard("calendar")
        if not domains:
            return
        resting = await asyncio.to_thread(self._resting, context, at)
        if resting and REST_WAKE_INTERVENTION not in capabilities:
            return

        for _ in range(60):
            if self._closed or self._intervention_latest.get(context.key) is not ticket:
                return
            if not await asyncio.to_thread(app.state_maintenance.foreground_busy, context):
                break
            await asyncio.sleep(0.5)
        else:
            return

        try:
            messages, user_text = await asyncio.to_thread(self._intervention_inputs, context, self.session.now())
        except Exception:
            return
        if not messages or not user_text:
            return
        if self._closed or self._intervention_latest.get(context.key) is not ticket:
            return
        input_epoch = app.state_maintenance._input_epochs.get(context.key, 0)
        request_id = "role-intervention-" + digest(
            [(item["observation_id"], item["content_hash"]) for item in messages])
        intervention_material = await asyncio.to_thread(
            self._intervention_material, context, capabilities, self.session.now())
        if (self._closed or self._intervention_latest.get(context.key) is not ticket
                or app.state_maintenance._input_epochs.get(context.key, 0) != input_epoch):
            return
        facts = {"scope_kind": "private", "user_text": user_text,
            "conversation": messages, "assistant_reply_returned": any(
                item["speaker"] == "assistant" for item in messages),
            "allowed_domains": sorted(domains),
            "resting": resting, "rest_wake_allowed": REST_WAKE_INTERVENTION in capabilities,
            "intervention_material": intervention_material}
        resolver = getattr(app, "laya_decision_resolver", None)
        service = resolver(context) if callable(resolver) else None
        if service is None or not callable(getattr(service, "classify_intervention", None)):
            return
        set_trace = getattr(service, "set_trace", None)
        if callable(set_trace):
            set_trace(event_ref=request_id, source="role_intervention")
        try:
            result = await service.classify_intervention(facts)
        except Exception as error:
            logger.info("[CompanionV2] 私聊干预分类未采用 reason=%s",
                        getattr(error, "code", type(error).__name__))
            return
        if (self._closed or self._intervention_latest.get(context.key) is not ticket
                or not isinstance(result, dict) or result.get("status") != "ok"):
            return

        current_at = self.session.now()
        current_capabilities = await asyncio.to_thread(
            authorized_capabilities, app.tasks, context, at=current_at)
        current_domains = {name for capability, name in ((STATE_INTERVENTION, "state"),
            (CALENDAR_INTERVENTION, "calendar")) if capability in current_capabilities}
        current_domains.intersection_update(domains)
        if not current_domains:
            return
        if await asyncio.to_thread(self._resting, context, current_at):
            if REST_WAKE_INTERVENTION not in current_capabilities:
                return
        if await asyncio.to_thread(app.state_maintenance.foreground_busy, context):
            return
        binding = app.state_maintenance.bindings.get(context.key)
        if binding is None or binding[0] != context:
            return
        await asyncio.to_thread(self.session.authorize, context)
        if binding[2] is not None:
            await binding[2](context.scope, context.role_binding, binding[1])
        current_settings = app.role_state.settings(context)[0]
        if not current_settings.get("enabled"):
            current_domains.discard("state")
        if not current_settings.get("calendar_enabled"):
            current_domains.discard("calendar")
        if (self._closed or self._intervention_latest.get(context.key) is not ticket
                or app.state_maintenance._input_epochs.get(context.key, 0) != input_epoch):
            return
        choice = result.get("intervention_need")
        requested = ({"state", "calendar"} if choice == "both" else
                     {choice} if choice in {"state", "calendar"} else set())
        selected = requested & current_domains
        if not selected:
            return

        jobs = []
        if "state" in selected:
            jobs.append(app.state_maintenance.run(context, binding[1], request_id + ":state",
                intervention=True, expected_input_epoch=input_epoch))
        if "calendar" in selected:
            jobs.append(app.calendar_planning.run_intervention(context, binding[1], request_id + ":calendar",
                expected_input_epoch=input_epoch))
        outcomes = await asyncio.gather(*jobs, return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                logger.info("[CompanionV2] 获授权的角色内容更新未完成 error_type=%s",
                            type(outcome).__name__)
        app.notify()

    def output(self, prepared, text, *, state="output_observed"):
        context, at = prepared["context"], self.session.now()

        def record():
            self.session.authorize(context)
            return self.owner.capture_output(context, turn_id=prepared["turn_id"], event_id=prepared["event_id"],
                                            text=text, at=at, state=state)

        pending = self._schedule(record, context_key=context.key, before=prepared.get("native_source_future"))
        if pending is not None:
            def observed(done):
                if done.cancelled() or done.exception() is not None:
                    return
                self.session.application.notify()
                if state == "send_returned" and context.scope.kind is ScopeKind.PRIVATE:
                    if self._intervention_latest.get(context.key) is not None:
                        ticket = object()
                        self._intervention_latest[context.key] = ticket
                        self._queue_intervention(context, ticket, delay=0.2)
            pending.add_done_callback(observed)
        return pending

    def phase(self, prepared, state):
        context, at = prepared["context"], self.session.now()

        def record():
            self.session.authorize(context)
            from companion_v2.conversation.native_turns import NativeConversationTurns
            NativeConversationTurns(self.session.runtime).mark(prepared, state, at=at)

        pending = self._schedule(record, context_key=context.key)
        if pending is not None:
            pending.add_done_callback(lambda done: self.session.application.notify()
                if not done.cancelled() and done.exception() is None else None)
        return pending

    def media(self, event, prepared, *, collection_only=False):
        """Capture direct native images after admission; generation never awaits it."""
        if self._closed:
            return None
        context = prepared["context"]
        if collection_only:
            semantic = getattr(self.session.application.tools, "semantic", None)
            if semantic is None or not semantic.reaction_collection_allowed(context, prepared["turn_id"]):
                return None
        from .core_reply_media import capture_native_input_images, frozen_image_locators
        locators = frozen_image_locators(event)
        if not locators:
            return None

        async def capture():
            try:
                image_event = await capture_native_input_images(self.session.application, prepared, locators,
                    authorize=lambda: self.session.authorize(context))
                if image_event is not None:
                    semantic = getattr(self.session.application.tools, "semantic", None)
                    schedule = getattr(semantic, "schedule_native_reaction_collection", None)
                    if callable(schedule):
                        schedule(context, prepared["turn_id"], image_event)
                    self.session.application.notify()
                return image_event
            except Exception as error:
                logger.warning("[CompanionV2] 核心媒体后台观察降级 error_type=%s code=%s",
                               type(error).__name__, getattr(error, "code", "unavailable"))
                return None

        task = asyncio.create_task(capture(), name="companion-v2-native-media-observation")
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
        return task

    async def close(self):
        self._closed = True
        for task in tuple(self._intervention_tasks):
            task.cancel()
        # Thread-backed SQLite writes must finish before the application's data
        # root closes. Cancelling an asyncio wrapper would leave its thread live.
        if self._pending:
            await asyncio.gather(*tuple(self._pending), return_exceptions=True)
        from companion_v2.conversation.native_turns import NativeConversationTurns
        await asyncio.to_thread(lambda: NativeConversationTurns(self.session.runtime).interrupt_open(at=self.session.now()))
        self._quoted_image_guard.close()
