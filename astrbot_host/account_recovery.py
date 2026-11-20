"""Bind available accounts independently without restarting the shared host."""

import asyncio
import json
import logging
from threading import RLock

from companion_v2.execution.models import SideEffect, TaskError, digest


logger = logging.getLogger(__name__)


class AccountExecutionGuard:
    """Fence outbound work while keeping local memory and profile access usable."""

    def __init__(self, connections, runtime=None):
        self.connections = connections
        self.runtime = runtime
        self._logical_leases = {}
        self._lock = RLock()

    def _logical_key(self, spec):
        if spec.kind in ("proactive.plan", "proactive.compose"):
            return "contact-" + digest(spec.idempotency_key)
        if spec.kind == "proactive.judge":
            return spec.idempotency_key
        if spec.kind == "delivery.proactive.contact" and self.runtime is not None:
            row = self.runtime.read_one("SELECT request_json FROM background_deliveries WHERE task_id = ?",
                                        (spec.task_id,))
            if row is not None:
                return json.loads(row["request_json"])["source_ref"]
        if spec.kind == "conversation.reply":
            return "reply:" + spec.request_ref
        if spec.kind == "conversation.deliver_part" and self.runtime is not None:
            row = self.runtime.read_one("SELECT batch_id FROM conversation_parts WHERE part_id = ?",
                                        (spec.request_ref,))
            if row is not None:
                return "reply:" + row["batch_id"]
        return None

    def available(self, context):
        try:
            self.connections.acquire((context.scope.platform, context.scope.account_id))
            return True
        except TaskError:
            return False

    def capture(self, spec, definition, *, submitting=False):
        if (definition.side_effect is SideEffect.MESSAGE_DELIVERY or spec.kind in (
                "conversation.reply", "proactive.plan", "proactive.compose", "proactive.judge")):
            now = self.connections.now()
            logical_key = self._logical_key(spec)
            with self._lock:
                self._logical_leases = {key: value for key, value in self._logical_leases.items() if value[1] > now}
                if logical_key in self._logical_leases:
                    lease = self._logical_leases[logical_key][0]
                    self.connections.assert_current(lease)
                else:
                    lease = self.connections.acquire((spec.context.scope.platform, spec.context.scope.account_id))
                    if logical_key is not None:
                        self._logical_leases[logical_key] = lease, spec.deadline_at
            # A queued reminder may recover within its original deadline. Once
            # execution begins, its connection lease is fixed like any other send.
            return None if submitting and spec.kind == "delivery.agenda.reminder" else lease
        return None

    def assert_current(self, spec, lease):
        self.connections.assert_current(lease)

    def finished(self, spec, definition, state):
        logical_key = self._logical_key(spec)
        if logical_key is None or self.runtime is None or state != "succeeded":
            return
        completed = False
        if spec.kind == "delivery.proactive.contact":
            row = self.runtime.read_one("SELECT state, request_json FROM background_deliveries WHERE task_id = ?",
                                        (spec.task_id,))
            if row is not None:
                request = json.loads(row["request_json"])
                if row["state"] == "delivered" and request.get("sequence", 0) + 1 == request.get("part_count", 1):
                    completed = True
        elif spec.kind in ("proactive.compose", "proactive.judge"):
            row = self.runtime.read_one("SELECT record_json FROM proactive_contacts WHERE contact_id = ?", (logical_key,))
            if row is not None and json.loads(row["record_json"]).get("decision") == "skip":
                completed = True
        elif spec.kind in ("conversation.reply", "conversation.deliver_part"):
            row = self.runtime.read_one("""SELECT 1 FROM conversation_batches b
                JOIN execution_tasks t ON t.task_id = b.generation_task_id
                WHERE b.batch_id = ? AND t.state = 'succeeded'
                AND NOT EXISTS (SELECT 1 FROM conversation_parts p WHERE p.batch_id = b.batch_id
                                AND p.state != 'delivered')""", (logical_key.removeprefix("reply:"),))
            completed = row is not None
        if completed:
            with self._lock:
                self._logical_leases.pop(logical_key, None)


class AccountRecovery:
    INTERVAL_SECONDS = 15.0

    def __init__(self, session):
        self.session = session
        self.connections = session.bridge.connections
        self.completed = {}
        self.failures = {}
        self.workers = {}
        self.driver = None
        self.closed = False

    async def refresh(self, *, wait=False):
        if self.closed:
            return
        rows = self.connections.refresh()
        sync = getattr(self.session, "sync_native_bridge_pairs", None)
        if callable(sync):
            sync()
        for row in rows:
            key = row["platform_id"], row["account_id"]
            worker = self.workers.get(key)
            if row["state"] != "online" or self.completed.get(key) == row["generation"]:
                continue
            if worker is None or worker.done():
                self.workers[key] = asyncio.create_task(
                    self._bind(key), name="companion:account-recovery:" + row["platform_id"])
        if wait and self.workers:
            await asyncio.gather(*tuple(self.workers.values()))

    async def _bind(self, key):
        session = self.session
        try:
            lease = self.connections.acquire(key)
            targets = [target for target in session.settings.primary_targets if target.participant_id != "*"]
            targets.extend(session._prepared_wildcard_private_targets())
            seen = set()
            for target in targets:
                if (target.platform_id, target.account_id) != key or target.target_id in seen:
                    continue
                seen.add(target.target_id)
                links = getattr(session, "linked_conversations", None)
                if links is not None and links.removed(target):
                    # Closed until it is admitted on its own: no routine runs under it.
                    continue
                try:
                    context, instruction = await session.synchronize(target)
                except TaskError as error:
                    if error.code in ("host_persona_unmapped", "host_persona_window_unbound",
                                      "host_native_session_unavailable"):
                        continue
                    raise
                self.connections.assert_current(lease)
                app = session.application
                app.life_runtime.bind(context, instruction, guard=session.bridge.verify_role)
                app.state_maintenance.bind(context, instruction, guard=session.bridge.verify_role)
                app.contact_schedule.bind_context(context)
                if app.presence is not None:
                    app.presence.bind(context)
            self.connections.assert_current(lease)
            self.completed[key] = lease.generation
            self.failures.pop(key, None)
        except TaskError as error:
            self._failed(key, error.code)
        except Exception as error:
            self._failed(key, "host_account_recovery_failed", type(error).__name__)

    def _failed(self, key, code, error_type=""):
        if self.failures.get(key) != code:
            logger.warning("[CompanionV2] 账号接入等待 platform=%s account=%s code=%s error_type=%s",
                           key[0], key[1], code, error_type)
        self.failures[key] = code

    def statuses(self):
        rows = self.connections.statuses()
        for row in rows:
            key = row["platform_id"], row["account_id"]
            if row["state"] != "online":
                continue
            worker = self.workers.get(key)
            if worker is not None and not worker.done():
                row.update(state="recovering", reason_code="host_account_recovering")
            elif key in self.failures:
                row.update(state="error", reason_code=self.failures[key])
        return rows

    def start(self):
        if self.driver is None:
            self.driver = asyncio.create_task(self._drive(), name="companion:account-connections")

    async def _drive(self):
        while not self.closed:
            await self.refresh()
            await asyncio.sleep(self.INTERVAL_SECONDS)

    async def close(self):
        self.closed = True
        tasks = [task for task in (self.driver, *self.workers.values()) if task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.workers.clear()
