"""Only a reply that was genuinely expected and then missing reaches the log.

The polarity is the contract: an event the plugin never owned is silent, the
role's own decision to keep listening is one information line, and a failure on
an expected reply is one warning line carrying the existing reason code.
"""

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import logging
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from companion_v2.adapters.astrbot.model_wire import tool_alias
from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.conversation.delivery import DeliveryReceipt
from companion_v2.conversation.flow import ConversationPolicy
from companion_v2.conversation.participation import ACTION, SKIPPED
from companion_v2.conversation.reply_settings import (
    GROUP, ReplyConfigurationOwner, configuration_fields as reply_configuration_fields,
)
from companion_v2.conversation.segmentation import SegmentationPolicy
from companion_v2.diagnostics import inbound
from companion_v2.execution.models import TaskContext, encode
from companion_v2.model_gateway.models import Amount, BudgetPolicy
from companion_v2.model_gateway.budget import BudgetLedger
from companion_v2.storage.runtime import StorageRuntime
from tests.group_host_support import GroupFixture, group_config
from tests.native_adapter_support import completion, final_text
from tests.staging_bootstrap_support import wait_for_batch
from tests.test_host_application import HostFixture
from tests.test_management_access import INSTALLATION, NOW, SCOPE
from tests.test_role_tool_loop import answered


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "_companion_v2_expected_reply_log_entry_test"
PACKAGE = ModuleType(PACKAGE_NAME)
PACKAGE.__path__ = [str(ROOT)]
sys.modules[PACKAGE_NAME] = PACKAGE
SPEC = importlib.util.spec_from_file_location(PACKAGE_NAME + ".main", ROOT / "main.py")
ENTRY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ENTRY
SPEC.loader.exec_module(ENTRY)


REPLY_LOGGER = inbound.logger.name
REPLY_LINE = "[CompanionV2][reply] "


def command(action, arguments, identifier="choice"):
    return NS(id=identifier, type="function", function=NS(name=tool_alias(action), arguments=encode(arguments)))


def rows(runtime, sql, args=()):
    return tuple(dict(row) for row in runtime.read_all(sql, args))


class ReplyLogAssertions:
    def reply_lines(self, captured):
        return [record for record in captured.records
                if isinstance(record.getMessage(), str) and record.getMessage().startswith(REPLY_LINE)]

    def log_line(self, captured, *, level=logging.WARNING):
        lines = self.reply_lines(captured)
        self.assertEqual(len(lines), 1, [record.getMessage() for record in captured.records])
        record = lines[0]
        self.assertEqual(record.levelno, level)
        parts = dict(item.split("=", 1) for item in record.getMessage().removeprefix(REPLY_LINE).split())
        self.assertEqual(parts["expected"], "true")
        return parts

    def assert_no_reply_log(self, captured):
        self.assertEqual([record.getMessage() for record in self.reply_lines(captured)], [])

    @contextmanager
    def captured(self):
        """Collect this owner's records; silence is a result, not an error."""
        records = []
        handler = logging.Handler()
        handler.emit = records.append
        logger = logging.getLogger(REPLY_LOGGER)
        previous, logger.level = logger.level, logging.DEBUG
        logger.addHandler(handler)
        try:
            yield NS(records=records)
        finally:
            logger.removeHandler(handler)
            logger.level = previous


class OutcomeContractTests(ReplyLogAssertions, unittest.TestCase):
    """The emitter's own contract, on a clock that can be measured."""

    def setUp(self):
        inbound.reset_reply_log()
        self.temporary = TemporaryDirectory(prefix="companion-v2-reply-outcome-")
        self.addCleanup(self.temporary.cleanup)
        self.runtime = StorageRuntime.open(data_root=Path(self.temporary.name),
                                           installation_id=INSTALLATION)
        self.addCleanup(self.runtime.close)
        self.ledger = inbound.InboundNoReplyLedger(self.runtime)

    def test_a_duration_is_measured_from_the_clock_that_owns_it(self):
        started = datetime.now(timezone.utc) - timedelta(seconds=2)

        with self.captured() as captured:
            self.ledger.emit(expectation=inbound.UNMET, stage="flow", turn="reply-1",
                             reason_code="deadline_elapsed", observed_at=datetime.now(timezone.utc),
                             origin_key="role", started_at=started)

        self.assertGreaterEqual(int(self.log_line(captured)["ms"]), 1000)

    def test_a_collapsed_reason_carries_the_count_it_collapsed(self):
        at = datetime.now(timezone.utc)
        with self.captured() as first:
            for index in range(3):
                self.ledger.emit(expectation=inbound.UNMET, stage="flow", turn="reply-" + str(index),
                                 reason_code="deadline_elapsed", observed_at=at)

        self.log_line(first)
        self.assertEqual(len(rows(self.runtime, "SELECT event_ref FROM inbound_no_replies")), 3)

    def test_an_unknown_polarity_is_refused_rather_than_guessed(self):
        with self.assertRaises(ValueError):
            self.ledger.emit(expectation="maybe", stage="flow", turn="reply-1",
                             reason_code="deadline_elapsed", observed_at=NOW)


class IntakeReplyLogTests(ReplyLogAssertions, unittest.IsolatedAsyncioTestCase):
    """The handler's own polarity, driven without the AstrBot import machinery."""

    def setUp(self):
        inbound.reset_reply_log()
        self.temporary = TemporaryDirectory(prefix="companion-v2-expected-reply-log-")
        self.addCleanup(self.temporary.cleanup)
        self.runtime = StorageRuntime.open(data_root=Path(self.temporary.name),
                                           installation_id=INSTALLATION)
        self.addCleanup(self.runtime.close)
        self.plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        self.target = NS(key="configured-role")
        self.staging = NS(
            runtime=self.runtime,
            now=lambda: NOW,
            claims=lambda _event: False,
            target_for_event=lambda _event: None,
            session=None,
            settings=NS(enabled=False),
            _accepting=False,
            _terminated=False,
            session_topology=NS(reply_enabled=AsyncMock(return_value=True), observe=AsyncMock()),
            persona_mapping_for_event=AsyncMock(return_value=NS(role_id="registered-role")),
            receive_claimed=AsyncMock(return_value=NS(failure=NS(code="staging_target_mismatch",
                                                               reason="outside the target"))),
            terminate=AsyncMock(),
        )
        self.plugin.staging = self.staging

    async def asyncTearDown(self):
        await self.plugin.terminate()

    def event(self, kind):
        event = NS(platform_meta=NS(name="aiocqhttp"), unified_msg_origin="qq-main:%s:300" % kind,
                   message_obj=NS(type=NS(value=kind), raw_message={"post_type": "message"}), stopped=False)
        event.stop_event = lambda: setattr(event, "stopped", True)
        return event

    async def test_a_non_target_group_message_produces_nothing_at_all(self):
        # Every message in a busy group lands here. It is not this plugin's
        # message, so it may produce neither a log line nor a ledger row.
        with self.captured() as captured:
            with patch.object(ENTRY, "refuse_stranger", new=AsyncMock(return_value=None)):
                await self.plugin.receive_companion_message(self.event("GroupMessage"))

        self.assert_no_reply_log(captured)
        self.assertEqual(rows(self.runtime, "SELECT * FROM inbound_no_replies"), ())

    async def test_a_non_target_private_message_produces_nothing_at_all(self):
        with self.captured() as captured:
            with patch.object(ENTRY, "refuse_stranger", new=AsyncMock(return_value=None)):
                await self.plugin.receive_companion_message(self.event("FriendMessage"))

        self.assert_no_reply_log(captured)
        self.assertEqual(rows(self.runtime, "SELECT * FROM inbound_no_replies"), ())

    async def test_d_an_owned_message_with_the_session_switch_off_warns_once(self):
        # The target is ours and only the host switch stops the reply: the plugin
        # had the responsibility, so the operator hears about it.
        self.staging.settings = NS(enabled=True)
        self.staging.claims = lambda _event: True
        self.staging.target_for_event = lambda _event: self.target
        self.staging.session_topology = NS(reply_enabled=AsyncMock(return_value=False), observe=AsyncMock())

        with self.captured() as captured:
            await self.plugin.receive_companion_message(self.event("FriendMessage"))

        parts = self.log_line(captured)
        self.assertEqual(parts["reason"], "session_reply_disabled")
        self.assertEqual(parts["stage"], "intake")
        self.assertEqual(rows(self.runtime, "SELECT reason_code, origin_key FROM inbound_no_replies"),
                         ({"reason_code": "session_reply_disabled", "origin_key": self.target.key},))

    async def test_an_owned_message_refused_by_the_host_warns_with_the_host_code(self):
        self.staging.settings = NS(enabled=True)
        self.staging.claims = lambda _event: True
        self.staging.target_for_event = lambda _event: self.target

        with self.captured() as captured:
            await self.plugin.receive_companion_message(self.event("FriendMessage"))

        parts = self.log_line(captured)
        self.assertEqual(parts["reason"], "staging_target_mismatch")
        self.assertEqual(parts["code"], "staging_target_mismatch")

    async def test_an_owned_target_that_cannot_be_admitted_warns_at_admission(self):
        # Configured, switched on, and still not accepting: that is a reply this
        # turn owed and could not take.
        self.staging.settings = NS(enabled=True)
        self.staging.target_for_event = lambda _event: self.target

        with self.captured() as captured:
            await self.plugin.receive_companion_message(self.event("FriendMessage"))

        parts = self.log_line(captured)
        self.assertEqual(parts["stage"], "admission")
        self.assertEqual(parts["reason"], "staging_not_accepting")
        self.assertEqual(rows(self.runtime, "SELECT reason_code, origin_key FROM inbound_no_replies"),
                         ({"reason_code": "staging_not_accepting", "origin_key": self.target.key},))

    async def test_a_disabled_host_never_promises_a_reply(self):
        # Switched off is a configuration state, not a missed reply.
        self.staging.settings = NS(enabled=False)
        self.staging.target_for_event = lambda _event: self.target

        with self.captured() as captured:
            await self.plugin.receive_companion_message(self.event("FriendMessage"))

        self.assert_no_reply_log(captured)
        self.assertEqual(rows(self.runtime, "SELECT * FROM inbound_no_replies"), ())

    async def test_a_stranger_notice_that_may_not_be_sent_is_not_an_expected_reply(self):
        outcomes = {"state": "no_reply", "reason_code": "stranger_notice_disabled"}

        async def refuse(_staging, _event, *, on_outcome=None):
            on_outcome(outcomes)
            return None
        self.staging.target_for_event = lambda _event: None

        with self.captured() as captured:
            with patch.object(ENTRY, "refuse_stranger", new=refuse):
                await self.plugin.receive_companion_message(self.event("FriendMessage"))

        self.assert_no_reply_log(captured)
        self.assertEqual(rows(self.runtime, "SELECT * FROM inbound_no_replies"), ())


class GroupReplyLogTests(ReplyLogAssertions, GroupFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        inbound.reset_reply_log()
        self.sequence = 0

    @property
    def app(self):
        return self.controller.session.application

    async def setup_delivery(self):
        self.controller = self.make_controller(group_config(group_delivery_mode="onebot_group"))
        await self.start_host()

    async def settle(self, batch):
        await wait_for_batch(self.controller.session, batch)

        async def wait():
            while any(work.batch_id == batch for work, _ in self.app._workers.values()):
                await asyncio.sleep(0.005)
        await asyncio.wait_for(wait(), 3)

    async def receive(self, text="两位成员还在继续讨论", *, commands=(), sender="200", target="group-a"):
        async def respond(payload, tools):
            done = len([message for message in payload["messages"] if message["role"] == "tool"])
            if done < len(commands):
                action, arguments = commands[done]
                return completion(tools=[command(action, arguments, "command-" + str(done))])
            return completion(final_text("我也说一句。"))
        self.native.provider.client.state.behavior = respond
        self.sequence += 1
        event = self.group_event(target=target, identifier="reply-log-" + str(self.sequence), sender=sender, text=text)
        batch = (await self.controller.receive_claimed(event)).unwrap()
        await self.settle(batch)
        return TaskContext.restore(json.loads(self.app.messages.batch(batch)["context_json"])), batch

    def configure(self, context, changes):
        """Write this group window's sealed reply settings through their owner."""
        target = ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW, context.role_binding.role,
                              context.scope)
        row = self.app.runtime.read_one("SELECT revision FROM configuration_targets WHERE target_key = ?",
                                        (target.key,))
        ReplyConfigurationOwner(self.app.runtime).write(
            target, reply_configuration_fields(SegmentationPolicy(), True, 0.0),
            changes={GROUP + "." + name: value for name, value in changes.items()}, reset=(),
            expected_revision=row["revision"] if row else 0, observed_at=self.clock.now())

    async def test_b_role_choice_to_keep_listening_is_one_information_line(self):
        await self.setup_delivery()

        with self.captured() as captured:
            _, batch = await self.receive("周末想去河边走走", commands=((ACTION, {"mode": "skip"}),))

        parts = self.log_line(captured, level=logging.INFO)
        self.assertEqual(parts["reason"], "silence")
        self.assertEqual(parts["turn"], batch)
        self.assertEqual(parts["code"], "basis_role_choice")
        self.assertEqual(int(parts["ms"]), 0)
        self.assertEqual(self.app.flow.row(batch)["reason"], SKIPPED)
        self.assertEqual(rows(self.app.runtime, "SELECT reason_code FROM inbound_no_replies"),
                         ({"reason_code": "silence"},))

    async def test_b_a_replied_turn_logs_no_reply_outcome(self):
        await self.setup_delivery()

        with self.captured() as captured:
            _, _batch = await self.receive("这条消息会正常回复")

        self.assert_no_reply_log(captured)
        self.assertEqual(rows(self.app.runtime, "SELECT * FROM inbound_no_replies"), ())

    async def test_a_repeated_turn_is_logged_once_however_often_it_is_resolved(self):
        await self.setup_delivery()
        _context, batch = await self.receive("重复处理的同一轮")

        with self.captured() as captured:
            self.app.flow.halt(batch, "deadline_elapsed", expected=True)
            self.app.flow.halt(batch, "deadline_elapsed", expected=True)

        parts = self.log_line(captured)
        self.assertEqual(parts["reason"], "deadline_elapsed")
        self.assertEqual(parts["turn"], batch)
        self.assertEqual(len(rows(self.app.runtime,
                                  "SELECT event_ref FROM inbound_no_replies WHERE reason_code = 'deadline_elapsed'")), 1)
        self.assertEqual(self.app.flow.row(batch)["state"], "blocked")


class HostReplyLogTests(ReplyLogAssertions, HostFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        inbound.reset_reply_log()

    async def failed_turn(self, *, budget=None):
        # The debounce policy is collapsed so the deterministic clock can settle
        # the turn instead of measuring wall time.
        settings = {"policy": ConversationPolicy(0, 0, 180, 0)}
        if budget is not None:
            settings["budget"] = budget
        host = await self.start(self.host(**settings))
        app = host.application
        batch = host.receive(self.event()).unwrap()
        row = await self.done(app, batch)
        return app, batch, row

    async def test_c_a_generation_failure_is_one_warning_with_its_failure_code(self):
        async def broken(envelope):
            raise RuntimeError("synthetic model transport failure")
        self.model_behavior = broken

        with self.captured() as captured:
            app, batch, row = await self.failed_turn()

        self.assertEqual(row["state"], "blocked")
        parts = self.log_line(captured)
        # The reason keeps the flow's own name for the stopped turn; the code is
        # what the reply actually died of.
        self.assertEqual(parts["reason"], "conversation_task_failed")
        self.assertEqual(parts["code"], "model_transport_failed")
        self.assertEqual(parts["stage"], "flow")
        outcome = app.runtime.read_one("SELECT code FROM inbound_no_replies WHERE reason_code = ? ORDER BY observed_at DESC LIMIT 1",
                                       ("conversation_task_failed",))
        self.assertEqual(outcome["code"], "model_transport_failed")
        self.assertEqual(self.sent, [])

    async def test_budget_exhaustion_is_recorded_before_any_model_call(self):
        budget = BudgetPolicy(*(Amount(1, 100000) for _ in range(5)))

        # Foreground replies are exempt from the optional hard quota. Exercise
        # the reservation refusal contract explicitly for this diagnostic test.
        with patch.object(BudgetLedger, "_is_main_chain", return_value=False), self.captured() as captured:
            app, batch, row = await self.failed_turn(budget=budget)

        self.assertEqual(row["reason"], "conversation_task_failed")
        parts = self.log_line(captured)
        self.assertEqual(parts["code"], "model_budget_exhausted")
        self.assertEqual(self.called, [])
        self.assertEqual(app.runtime.read_one("SELECT COUNT(*) AS count FROM model_calls", ())["count"], 0)
        outcome = app.runtime.read_one("SELECT code FROM inbound_no_replies WHERE reason_code = ? ORDER BY observed_at DESC LIMIT 1",
                                       ("conversation_task_failed",))
        self.assertEqual(outcome["code"], "model_budget_exhausted")

    async def test_d_a_gate_that_blocks_the_reply_reports_the_gate_by_name(self):
        async def blocked(envelope):
            return answered("这次回复里有禁用词。")
        self.model_behavior = blocked
        self.grant(self.important, SCOPE, "configuration.write")
        self.write_window({"enable_forbidden_word_recall": True, "recall_forbidden_words": '["禁用词"]',
                           "recall_forbidden_scope": "bot_and_group"})

        with self.captured() as captured:
            app, batch, row = await self.failed_turn()

        self.assertEqual(row["state"], "blocked")
        parts = self.log_line(captured)
        self.assertEqual(parts["reason"], "conversation_task_failed")
        self.assertEqual(parts["code"], "forbidden_word_blocked")
        self.assertEqual(parts["turn"], batch)
        self.assertEqual(self.sent, [])

    async def test_e_an_uncertain_delivery_still_warns_exactly_once(self):
        async def accepted(envelope):
            return DeliveryReceipt(envelope.part_id, envelope.operation_id, envelope.context.key,
                                   envelope.payload_hash, envelope.sender, "accepted",
                                   "accepted:" + envelope.part_id, "platform:" + envelope.part_id)
        self.send_behavior = accepted

        with self.captured() as captured:
            app, batch, row = await self.failed_turn()

        self.assertEqual(row["reason"], "message_delivery_unresolved")
        self.assertEqual(len(self.sent), 1)
        parts = self.log_line(captured)
        self.assertEqual(parts["reason"], "message_delivery_unresolved")
        self.assertEqual(parts["turn"], batch)
        self.assertTrue(parts["part"].startswith("part-"))

    def write_window(self, changes):
        """Seal the reply settings on this private window through their owner."""
        row = self.runtime.read_one("SELECT revision FROM configuration_targets WHERE target_key = ?",
                                    (self.window.key,))
        ReplyConfigurationOwner(self.runtime).write(
            self.window, reply_configuration_fields(SegmentationPolicy(), True, 0.0),
            changes={GROUP + "." + name: value for name, value in changes.items()}, reset=(),
            expected_revision=row["revision"] if row else 0, observed_at=self.clock.now())


if __name__ == "__main__":
    unittest.main()
