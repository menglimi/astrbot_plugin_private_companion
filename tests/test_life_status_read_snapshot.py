"""Regression tests for read-only life sleep schedule inspection inside read snapshots."""

import unittest
from datetime import time
from unittest.mock import patch

from astrbot_host.management_identity import dashboard_operator
from companion_v2.role_work.life.configuration import GROUP as LIFE_GROUP, target_for
from companion_v2.role_work.life.management import RoleLifeManagement
from companion_v2.role_work.life.scheduling import next_bedtime
from companion_v2.role_work.state.management import StateManagement
from companion_v2.sources.dependencies import validation_batch
from tests.multi_host_support import multi_config
from tests.multi_life_support import MultiLifeFixture


class LifeStatusReadSnapshotTests(MultiLifeFixture, unittest.IsolatedAsyncioTestCase):
    def shared_role_config(self):
        config = multi_config()
        config["multi_host"]["targets"][1]["bot_id"] = "bot-a"
        config["multi_host"]["personas"] = [
            mapping for mapping in config["multi_host"]["personas"] if mapping["bot_id"] == "bot-a"
        ]
        return config

    async def _setup_stale_revision(self, *, diary_enabled=True, sleep_time="01:15", sleep_minutes=90):
        app = await self.initialize(self.shared_role_config())
        await self.command(
            "configure",
            "first",
            "role-a",
            key="enable-first",
            changes={
                "enabled": True,
                "diary_enabled": diary_enabled,
                "sleep_enabled": True,
                "sleep_time": "00:30",
                "sleep_timezone": "UTC",
                "sleep_variation_minutes": 0,
                "sleep_minutes": 60,
            },
        )
        await self.command(
            "configure",
            "second",
            "role-a",
            key="enable-second",
            changes={"enabled": True, "diary_enabled": diary_enabled},
        )
        await self.start()
        await self.command(
            "configure",
            "first",
            "role-a",
            key="update-sleep",
            changes={"sleep_time": sleep_time, "sleep_minutes": sleep_minutes},
        )
        first_routine = self.routine("first", "role-a")
        second_routine = self.routine("second", "role-a")
        operator = dashboard_operator(app.runtime, "admin", at=self.clock.now())
        latest_first = app.configuration.read(
            operator,
            target=target_for(first_routine.context),
            group_id=LIFE_GROUP,
            observed_at=self.clock.now(),
        ).unwrap()
        latest_second = app.configuration.read(
            operator,
            target=target_for(second_routine.context),
            group_id=LIFE_GROUP,
            observed_at=self.clock.now(),
        ).unwrap()
        self.assertNotEqual(first_routine._revision, latest_first.resolution_token)
        self.assertNotEqual(second_routine._revision, latest_second.resolution_token)
        return app, operator, first_routine, second_routine, latest_first, latest_second

    async def test_life_status_inside_read_snapshot_avoids_refresh_transaction_when_revision_stale(self):
        app, operator, first_routine, second_routine, latest_first, latest_second = await self._setup_stale_revision(
            diary_enabled=True, sleep_time="01:15", sleep_minutes=90
        )
        before_first_revision = first_routine._revision
        before_second_revision = second_routine._revision
        manager = RoleLifeManagement(app, self.context("first", "role-a"))

        with (
            patch.object(app.runtime, "transaction", side_effect=AssertionError("unexpected runtime write")),
            patch.object(app.configuration.store, "transaction", side_effect=AssertionError("unexpected config write")),
            patch.object(app.role_journal.sources, "publish", side_effect=AssertionError("unexpected source publish")),
        ):
            with app.runtime.read_snapshot(), validation_batch(app.runtime, app.now()):
                status = manager.status(operator)

        expected_next = next_bedtime(self.clock.now(), first_routine.owner, "UTC", time.fromisoformat("01:15"), 0)
        self.assertEqual(status["sleep_schedule"]["executor_state"], "available")
        self.assertEqual(status["sleep_schedule"]["next_sleep_at"], expected_next.isoformat())
        self.assertEqual(status["sleep_schedule"]["duration_minutes"], 90)
        self.assertEqual(first_routine._revision, before_first_revision)
        self.assertEqual(second_routine._revision, before_second_revision)

        # Outside read_snapshot, advance() still performs full refresh, publication, and attestation.
        app.life_runtime.advance()
        self.assertEqual(first_routine._revision, latest_first.resolution_token)
        self.assertEqual(second_routine._revision, latest_second.resolution_token)
        self.assertEqual(first_routine._next_sleep, expected_next)

    async def test_life_status_inside_read_snapshot_handles_freshly_bound_none_revision(self):
        app, operator, first_routine, second_routine, latest_first, latest_second = await self._setup_stale_revision(
            diary_enabled=True, sleep_time="01:45", sleep_minutes=50
        )
        first_routine._revision = None
        second_routine._revision = None
        first_routine._next_sleep = None
        second_routine._next_sleep = None
        manager = RoleLifeManagement(app, self.context("first", "role-a"))

        with (
            patch.object(app.runtime, "transaction", side_effect=AssertionError("unexpected runtime write")),
            patch.object(app.configuration.store, "transaction", side_effect=AssertionError("unexpected config write")),
            patch.object(app.role_journal.sources, "publish", side_effect=AssertionError("unexpected source publish")),
        ):
            with app.runtime.read_snapshot(), validation_batch(app.runtime, app.now()):
                status = manager.status(operator)

        expected_next = next_bedtime(self.clock.now(), first_routine.owner, "UTC", time.fromisoformat("01:45"), 0)
        self.assertEqual(status["sleep_schedule"]["executor_state"], "available")
        self.assertEqual(status["sleep_schedule"]["next_sleep_at"], expected_next.isoformat())
        self.assertEqual(status["sleep_schedule"]["duration_minutes"], 50)
        self.assertIsNone(first_routine._revision)
        self.assertIsNone(second_routine._revision)

        app.life_runtime.advance()
        self.assertEqual(first_routine._revision, latest_first.resolution_token)
        self.assertEqual(second_routine._revision, latest_second.resolution_token)

    async def test_state_management_status_inside_read_snapshot_handles_stale_diary_and_sleep_config(self):
        app, operator, first_routine, _, _, _ = await self._setup_stale_revision(
            diary_enabled=True, sleep_time="02:20", sleep_minutes=75
        )
        state_manager = StateManagement(app, self.context("first", "role-a"))
        outcome = state_manager.status(operator)
        self.assertTrue(outcome.is_success, outcome.failure)
        data = outcome.unwrap()
        expected_next = next_bedtime(self.clock.now(), first_routine.owner, "UTC", time.fromisoformat("02:20"), 0)
        self.assertEqual(data["sleep_schedule"]["executor_state"], "available")
        self.assertEqual(data["sleep_schedule"]["next_sleep_at"], expected_next.isoformat())
        self.assertEqual(data["sleep_schedule"]["duration_minutes"], 75)

    async def test_sleep_executor_respects_read_snapshot_and_keeps_advance_refresh_semantics(self):
        app, operator, first_routine, second_routine, _, _ = await self._setup_stale_revision(
            diary_enabled=True, sleep_time="03:10", sleep_minutes=45
        )
        expected_executor = min((first_routine, second_routine), key=lambda item: item.context.key)
        prior_first_revision = first_routine._revision

        with app.runtime.read_snapshot():
            selected = app.life_runtime.sleep_executor(first_routine)
        self.assertIs(selected, expected_executor)
        self.assertEqual(first_routine._revision, prior_first_revision)

        # Disable the current minimum window and verify read-only inspection immediately excludes it.
        target_name = "first" if expected_executor is first_routine else "second"
        other_name = "second" if target_name == "first" else "first"
        await self.command("configure", target_name, "role-a", key="disable-winning", changes={"enabled": False})
        other_manager = RoleLifeManagement(app, self.context(other_name, "role-a"))
        with app.runtime.read_snapshot(), validation_batch(app.runtime, app.now()):
            other_status = other_manager.status(operator)
        self.assertTrue(other_status["sleep_schedule"]["executor_is_current"])
        self.assertEqual(other_status["sleep_schedule"]["executor_state"], "available")

        # Outside read_snapshot, owns_sleep_schedule/sleep_executor still refreshes candidates.
        other_routine = self.routine(other_name, "role-a")
        self.assertTrue(app.life_runtime.owns_sleep_schedule(other_routine))
        latest_other = app.configuration.read(
            operator,
            target=target_for(other_routine.context),
            group_id=LIFE_GROUP,
            observed_at=self.clock.now(),
        ).unwrap()
        self.assertEqual(other_routine._revision, latest_other.resolution_token)
        self.assertIn(other_routine.schedule_id, app.journal_schedule._schedules)

    async def test_stopped_host_status_with_stale_revision_never_mutates_or_writes(self):
        app, operator, first_routine, _, _, _ = await self._setup_stale_revision(diary_enabled=True)
        (await self.controller.stop()).unwrap()
        await self.command(
            "configure",
            "first",
            "role-a",
            key="bump-while-stopped",
            changes={"sleep_time": "04:00"},
        )
        revision_before = first_routine._revision
        latest = app.configuration.read(
            operator,
            target=target_for(first_routine.context),
            group_id=LIFE_GROUP,
            observed_at=self.clock.now(),
        ).unwrap()
        self.assertNotEqual(revision_before, latest.resolution_token)
        manager = RoleLifeManagement(app, self.context("first", "role-a"))
        with (
            patch.object(app.runtime, "transaction", side_effect=AssertionError("unexpected runtime write")),
            patch.object(app.configuration.store, "transaction", side_effect=AssertionError("unexpected config write")),
            patch.object(app.role_journal.sources, "publish", side_effect=AssertionError("unexpected source publish")),
        ):
            with app.runtime.read_snapshot(), validation_batch(app.runtime, app.now()):
                status = manager.status(operator)
        self.assertEqual(status["host_state"], "stopped")
        self.assertEqual(status["sleep_schedule"]["executor_state"], "no_authorized_window")
        self.assertEqual(first_routine._revision, revision_before)


if __name__ == "__main__":
    unittest.main()
