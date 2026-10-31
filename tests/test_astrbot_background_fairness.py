"""Competing private routines share capacity while retaining their own inputs."""

import asyncio
from dataclasses import replace
from datetime import timedelta
import unittest

from tests.multi_life_support import MultiLifeFixture
from tests.native_adapter_support import completion
from tests.test_role_state import state_data
from astrbot_host.management_identity import dashboard_operator
from companion_v2.execution.models import encode, stamp
from companion_v2.role_work.state.management import StateManagement


class BackgroundFairnessTests(MultiLifeFixture, unittest.IsolatedAsyncioTestCase):
    def state_manager(self, target="second"):
        app = self.controller.session.application
        return StateManagement(app, self.context(target))

    def enable_state(self, target="second"):
        app = self.controller.session.application
        operator = dashboard_operator(app.runtime, "admin", at=self.clock.now())
        manager = self.state_manager(target)
        before = manager.status(operator).unwrap()
        manager.command(operator, dict(action="configure", operation_key="enable-state",
            edit_token=before["edit_token"], role_edit_token=before["role_edit_token"],
            changes={"enabled": True})).unwrap()
        return self.context(target)

    def queued(self, app, kind):
        return app.runtime.read_one("SELECT task_id FROM execution_tasks WHERE kind = ? AND state = 'created'", (kind,))

    async def test_both_automatic_readers_wait_and_keep_their_original_personas(self):
        self.native.selections["qq-second"] = "second"
        app = await self.initialize()
        for target, role in (("first", "role-a"), ("second", "role-b")):
            await self.enable(target, role, reading_enabled=True)
            await self.book(target, role)
        entered, release = asyncio.Event(), asyncio.Event()

        async def held(payload, tools):
            if "original_reading_span" in str(payload) and "你是第二个人格。" not in str(payload):
                entered.set()
                await release.wait()
            return await self.model(payload, tools)

        self.native.provider.client.state.behavior = held
        await self.start()
        await asyncio.wait_for(entered.wait(), 2)
        await self.until(lambda: bool(self.queued(app, "role.reading.consume")))
        status = await self.status("second", "role-b")
        self.assertEqual(status["activities"][0]["state"], "active")
        self.assertEqual(status["activities"][0]["task"]["state"], "created")
        self.assertEqual(len(self.native.provider.client.state.calls), 1)
        release.set()
        await self.settled()
        for target, role, persona in (("first", "role-a", "原人格"), ("second", "role-b", "第二人格")):
            item = (await self.status(target, role))["activities"][0]
            self.assertEqual(item["state"], "completed", item)
            self.assertIn(persona, item["reflection"])
        self.assertEqual(len(self.native.provider.client.state.calls), 2)

    async def test_queued_diary_and_state_run_before_next_reading_span_and_foreground_still_progresses(self):
        app = await self.initialize()
        await self.enable(reading_enabled=True)
        await self.enable("second", diary_enabled=True, diary_time="08:01")
        await self.command("add_text", title="两段原文", text="甲" * 6100)
        order = []
        entered, release = asyncio.Event(), asyncio.Event()

        async def model(payload, tools):
            text = str(payload)
            if "original_reading_span" in text:
                order.append("read")
                if order == ["read"]:
                    entered.set()
                    await release.wait()
            elif "maintenance_basis" in text:
                order.append("state")
                return completion(encode(state_data(hunger="还不饿")))
            elif "writing_date" in text:
                order.append("diary")
            return await self.model(payload, tools)

        self.native.provider.client.state.behavior = model
        await self.start()
        await asyncio.wait_for(entered.wait(), 2)
        self.clock.advance(timedelta(seconds=61))
        app.notify()
        await self.until(lambda: bool(self.queued(app, "role.journal.diary")))
        state_context = self.enable_state()
        app.notify()
        await self.until(lambda: bool(self.queued(app, "role.state.maintain")))
        await self.accepted(identifier="while-background-queued")
        self.assertEqual(order, ["read"])
        release.set()
        await self.until(lambda: len(order) == 4 and not app.state_maintenance.workers)
        await self.settled()
        self.assertEqual(order, ["read", "diary", "state", "read"])
        self.assertEqual({a["state"] for a in (await self.status())["activities"]}, {"completed"})
        self.assertEqual(len((await self.status("second"))["journals"]), 1)
        self.assertEqual(app.role_state.read(state_context)["values"]["hunger"], "还不饿")
        self.assertEqual(app.runtime.read_one("SELECT COUNT(*) n FROM execution_tasks WHERE json_extract(record_json, '$.reason') = 'owner_unavailable'", ())["n"], 0)

    async def test_withdraw_waiting_reading_removes_it_without_model_dispatch(self):
        app = await self.initialize()
        for target in ("first", "second"):
            await self.enable(target, reading_enabled=True)
            await self.book(target)
        entered, release = asyncio.Event(), asyncio.Event()

        async def held(payload, tools):
            entered.set()
            await release.wait()
            return await self.model(payload, tools)

        self.native.provider.client.state.behavior = held
        await self.start()
        await asyncio.wait_for(entered.wait(), 2)
        await self.until(lambda: bool(self.queued(app, "role.reading.consume")))
        item = (await self.status("second"))["activities"][0]
        await self.command("withdraw", "second", item=item)
        release.set()
        await self.settled()
        item = (await self.status("second"))["activities"][0]
        self.assertEqual((item["state"], item["task"]["state"], item["reflection"]), ("cancelled", "cancelled", None))
        self.assertEqual(len(self.native.provider.client.state.calls), 1)
        self.assertEqual(app.background_waiting._pending, {})

    async def test_stop_cleans_unstarted_waiters_and_reload_does_not_replay_unknown_reader(self):
        app = await self.initialize()
        for target in ("first", "second"):
            await self.enable(target, reading_enabled=True)
            await self.book(target)
        entered = asyncio.Event()

        async def held(payload, tools):
            entered.set()
            await asyncio.Event().wait()

        self.native.provider.client.state.behavior = held
        await self.start()
        await asyncio.wait_for(entered.wait(), 2)
        await self.until(lambda: bool(self.queued(app, "role.reading.consume")))
        queued_id = self.queued(app, "role.reading.consume")["task_id"]
        stopped = (await self.controller.stop()).unwrap()
        self.assertEqual((stopped.state, stopped.pending_handles), ("stopped", ()))
        self.assertEqual(app.background_waiting._pending, {})
        row = app.runtime.read_one("SELECT state, attempt_no FROM execution_tasks WHERE task_id = ?", (queued_id,))
        self.assertEqual((row["state"], row["attempt_no"]), ("cancelled", 0))
        self.assertEqual((await self.status())["activities"][0]["task"]["state"], "unknown")
        await self.controller.terminate()
        self.controller = self.make_controller()
        self.register()
        (await self.controller.load_existing()).unwrap()
        self.native.provider.client.state.behavior = self.model
        await self.start()
        await self.settled()
        self.assertEqual(len(self.native.provider.client.state.calls), 1)
        second = (await self.status("second"))["activities"][0]
        await self.command("read", "second", item=second, key="explicit-never-started")
        await self.settled()
        self.assertEqual((await self.status("second"))["activities"][0]["state"], "completed")
        first = (await self.status())["activities"][0]
        denied = await self.request(data=await self.body("read", item=first, key="unknown-retry"))
        self.assertEqual(denied["data"]["code"], "life_reading_unresolved")
        self.assertEqual(len(self.native.provider.client.state.calls), 2)

    async def test_switching_waiting_persona_cancels_original_without_dispatch(self):
        app = await self.initialize()
        for target in ("first", "second"):
            await self.enable(target, reading_enabled=True)
            await self.book(target)
        entered, release = asyncio.Event(), asyncio.Event()

        async def held(payload, tools):
            if "original_reading_span" in str(payload):
                entered.set()
                await release.wait()
            return await self.model(payload, tools)

        self.native.provider.client.state.behavior = held
        await self.start()
        await asyncio.wait_for(entered.wait(), 2)
        await self.until(lambda: bool(self.queued(app, "role.reading.consume")))
        original = self.routine("second")
        self.native.selections["qq-second"] = "second"
        await self.accepted("second", identifier="switch-while-waiting")
        await self.until(lambda: not original.active_handles)
        old = (await self.status("second"))["activities"][0]
        self.assertEqual((old["state"], old["task"]["state"], old["current_context"]), ("paused", "cancelled", False))
        self.assertEqual((await self.status("second", "role-b"))["activities"], [])
        self.assertTrue(original._retired)
        release.set()
        await self.settled()
        read_calls = [p for p, _ in self.native.provider.client.state.calls if "original_reading_span" in str(p)]
        self.assertEqual(len(read_calls), 1)
        self.assertEqual((await self.status())["activities"][0]["state"], "completed")

    async def test_stale_state_schedule_pages_cannot_starve_the_current_target(self):
        app = await self.initialize()
        context = self.enable_state()
        # Historical per-window rows are consolidated on the role's next binding.
        for index in range(40):
            old = replace(context, role_binding=replace(context.role_binding, revision=100 + index))
            with app.runtime.transaction() as connection:
                connection.execute("INSERT INTO role_state_schedules VALUES (?, ?, ?, ?, ?, ?, NULL)",
                    (old.key, encode(old), encode({"instruction": "旧绑定", "token": "old", "values": {}}),
                     stamp(self.clock.now() - timedelta(days=1)), 1, index % 2))
        calls = []

        async def state_model(payload, tools):
            calls.append(payload["messages"][0]["content"])
            return completion(encode(state_data(energy=77)))

        self.native.provider.client.state.behavior = state_model
        await self.start()
        await self.until(lambda: app.role_state.records.get(context, "current") is not None)
        await self.until(lambda: not app.state_maintenance.workers)
        self.assertEqual(app.role_state.read(context)["values"]["energy"], 77)
        self.assertEqual(len(calls), 1)
        self.assertEqual(app.runtime.read_one("SELECT COUNT(*) n FROM role_state_schedules", ())["n"], 1)
        self.assertEqual(app.runtime.read_one("SELECT COUNT(*) n FROM execution_tasks WHERE kind = 'role.state.maintain'", ())["n"], 1)


if __name__ == "__main__":
    unittest.main()
