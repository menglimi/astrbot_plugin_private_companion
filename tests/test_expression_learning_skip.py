"""An already-attempted batch must say which earlier attempt owns it.

``role.expression.learn`` spends one model call per material batch: the request
key covers ``material_hash``, so the same samples are not tried twice. When the
earlier attempt never produced a receipt, the batch stays locked until new
samples arrive, and from the outside that is indistinguishable from "already
learned". The skip line now carries the previous state, its timestamp and the
material hash, so the two cases can be told apart.
"""

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from companion_v2.execution.models import digest
from companion_v2.expression.style_service import ExpressionStyleService, KIND, OWNER
from companion_v2.foundation.refs import OperationRef, ScopeKind, ScopeRef, VersionRef
from companion_v2.storage.models import OperationState
from companion_v2.storage.repositories import OperationRepository
from companion_v2.storage.runtime import StorageRuntime

NOW = datetime(2026, 10, 30, 9, 0, tzinfo=timezone.utc)
SCOPE = ScopeRef("installation-a", "onebot", ScopeKind.PRIVATE, "native-a", "bot-a", "login-a",
                 "conversation-a", "session-a")
LOGGER = "companion_v2.expression.style_service"


async def _noop(*_args):
    return None


class SkipNoteFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="expression-skip-")
        self.addCleanup(self.temporary.cleanup)
        self.runtime = StorageRuntime.open(data_root=Path(self.temporary.name),
                                           installation_id="installation-a")
        self.addCleanup(self.runtime.close)
        self.operations = OperationRepository(self.runtime)

    def service(self):
        service = ExpressionStyleService(self.runtime)
        service.gateway = SimpleNamespace(router=SimpleNamespace(profiles={KIND: object()}))
        service.tasks = SimpleNamespace(now=lambda: NOW)
        service.guard = _noop
        return service

    def job(self, material="0123456789abcdef0123", mode="whole"):
        return {"material_hash": material, "policy": SimpleNamespace(learning_mode=mode), "at": NOW}

    def receipt(self, operation_id, state=OperationState.ACCEPTED):
        reference = OperationRef(operation_id, OWNER, SCOPE, VersionRef(operation_id, 1), operation_id)
        self.operations.reserve(reference, parameter_hash=digest(["parameters"]), observed_at=NOW).unwrap()
        if state is OperationState.COMPLETED:
            self.operations.transition(operation_id, expected_state=OperationState.ACCEPTED,
                                       next_state=state, observed_at=NOW).unwrap()
        return self.operations.get(operation_id).unwrap()


class SkipNoteTests(SkipNoteFixture):
    def test_a_finished_attempt_reports_already_learned(self):
        service, receipt = self.service(), self.receipt("expression-learn:finished", OperationState.COMPLETED)
        with self.assertLogs(LOGGER, level="INFO") as logs:
            service._note_skipped("expression-learn:finished", self.job(), receipt)

        line = "\n".join(logs.output)
        self.assertIn("expression-learn:finished", line)
        self.assertIn("previous=completed", line)
        self.assertIn("material=0123456789ab", line)
        self.assertIn("mode=whole", line)
        self.assertEqual(logs.records[0].levelno, 20)

    def test_an_unfinished_attempt_is_reported_as_a_lock(self):
        service, receipt = self.service(), self.receipt("expression-learn:locked")
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            service._note_skipped("expression-learn:locked", self.job(), receipt)

        self.assertIn("previous=accepted", "\n".join(logs.output))
        self.assertEqual(logs.records[0].levelno, 30)

    def test_an_unknown_operation_state_is_still_readable(self):
        service = self.service()
        receipt = SimpleNamespace(state=None, updated_at=None)
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            service._note_skipped("expression-learn:odd", self.job(mode=""), receipt)

        self.assertIn("previous=missing", "\n".join(logs.output))


class SkipWiringTests(SkipNoteFixture, unittest.IsolatedAsyncioTestCase):
    async def test_run_notes_the_previous_attempt_instead_of_skipping_silently(self):
        service = self.service()
        self.receipt("expression-learn:task-b")
        service.identifier = lambda context, request_key: "expression-learn:task-b"
        service.prepare = lambda context, instruction, at: self.job()
        context = SimpleNamespace(key="private:1", scope=SimpleNamespace(kind=ScopeKind.PRIVATE))

        with self.assertLogs(LOGGER, level="WARNING") as logs:
            report = await service.run(context, "学习这一批")

        self.assertEqual(report, {"state": "already_attempted", "task_id": "expression-learn:task-b"})
        self.assertIn("previous=accepted", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
