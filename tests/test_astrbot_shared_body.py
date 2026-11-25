"""Same-role physical continuity through the real multi-target management routes."""

import asyncio
from datetime import timedelta
import json
import unittest

from companion_v2.conversation.state_context import ConversationStateContext
from companion_v2.execution.models import TaskError
from companion_v2.role_work.state.management import StateManagement
from astrbot_host.management_identity import dashboard_operator
from tests.multi_host_support import multi_config
from tests.multi_life_support import MultiLifeFixture
from tests.test_role_state import state_data
from tests.test_role_cycle import assertion


def shared_config():
    config = multi_config()
    config["multi_host"]["targets"].append(dict(target_id="same", platform_id="qq-main",
        account_id="100", bot_id="bot-a", participant_id="201"))
    return config


class SharedBodyHostTests(MultiLifeFixture, unittest.IsolatedAsyncioTestCase):
    async def ready(self, config=None):
        self.app = await self.initialize(config or shared_config())
        for target in ("first", "same", "second"):
            await self.enable(target)
        await self.start()

    def enable_state(self, target):
        context = self.context(target)
        manager = StateManagement(self.app, context)
        operator = dashboard_operator(self.controller.runtime, "admin", at=self.clock.now())
        status = manager.status(operator).unwrap()
        manager.command(operator, dict(action="configure", operation_key="state-enabled",
            edit_token=status["edit_token"], changes={"enabled": True, "mood_enabled": True})).unwrap()
        with self.app.runtime.transaction() as connection:
            connection.execute("UPDATE role_state_schedules SET next_at = ?", ((self.clock.now() + timedelta(days=5)).isoformat(),))
        return context

    async def test_state_body_and_preferences_follow_the_persona_across_windows(self):
        await self.ready()
        first = self.enable_state("first")
        same = self.context("same")
        operator = dashboard_operator(self.controller.runtime, "admin", at=self.clock.now())

        self.assertTrue(self.app.role_state.settings(same)[0]["enabled"])
        self.app.role_state.write(first, data=state_data(energy=54, hunger="有点饿", health="平稳"),
            expected_revision=0, operation_key="persona-body", operator=operator, reason="human_edit")
        shared = self.app.role_state.read(same, operator=operator)

        self.assertEqual(shared["values"]["energy"], 54)
        self.assertEqual(shared["values"]["hunger"], "有点饿")
        self.assertEqual(self.app.role_state.records.storage(same), "role")

    async def test_sleep_is_shared_without_private_activity_prose_and_other_bot_is_independent(self):
        await self.ready()
        book = await self.book("same")
        await self.command("sleep", minutes=30)
        first, same = await self.status(), await self.status("same")
        self.assertEqual(first["body_sleep"], same["body_sleep"])
        self.assertEqual(same["body_sleep"]["state"], "sleeping")
        self.assertFalse(any(a["kind"] == "sleep" for a in same["activities"]))
        for action, fields, code in (("sleep", {"minutes": 20}, "life_already_sleeping"),
                ("read", {"item": book}, "life_sleeping")):
            response = await self.request("same", data=await self.body(action, "same", **fields))
            self.assertEqual(response["data"]["code"], code)
        await self.command("sleep", "second", minutes=20)
        self.assertNotEqual((await self.status("second"))["body_sleep"]["planned_wake_at"], same["body_sleep"]["planned_wake_at"])
        self.assertNotIn("activity_id", same["body_sleep"])
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_simultaneous_sleep_requests_get_one_physical_receipt(self):
        await self.ready()
        a = await self.body("sleep", minutes=30)
        b = await self.body("sleep", "same", minutes=40)
        results = await asyncio.gather(self.request(data=a), self.request("same", data=b))
        self.assertEqual(sum(r["status"] == "ok" for r in results), 1)
        self.assertEqual(len(self.app.role_state.body_clock.active(self.context(), "sleep")), 1)
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_other_window_wakes_due_timer_after_restart_without_writing_a_foreign_dream(self):
        await self.ready()
        await self.command("sleep", minutes=1)
        await self.command("configure", key="disable-first", changes={"enabled": False})
        (await self.controller.stop()).unwrap()
        self.clock.advance(timedelta(minutes=2))
        self.controller = self.make_controller(shared_config())
        self.register()
        (await self.controller.load_existing()).unwrap()
        await self.start()
        self.app = self.controller.session.application
        await self.until(lambda: self.app.role_state.body_clock.read(self.context("same"))["state"] == "awake")
        for _ in range(3):
            self.app.life_runtime.advance()
        first, same = await self.status(), await self.status("same")
        self.assertEqual(first["activities"][0]["state"], "completed")
        self.assertEqual(same["body_sleep"]["state"], "awake")
        self.assertEqual(same["activities"], [])
        self.assertEqual(same["journals"], [])
        self.assertEqual(self.native.provider.client.state.calls, [])
        count = self.app.runtime.read_one("SELECT COUNT(*) n FROM operations WHERE operation_id LIKE 'body-wake-%'", ())["n"]
        self.assertEqual(count, 1)

    async def test_foreign_sleep_snapshot_is_frozen_and_wake_invalidates_only_unsent_view(self):
        await self.ready()
        context = self.enable_state("same")
        await self.command("sleep", minutes=20)
        consumer = ConversationStateContext(self.app.role_state)
        snapshot = consumer.capture(context, self.clock.now())
        material = consumer.contributions(snapshot, context, self.clock.now())
        state = json.loads(material[0].payload)["simulation"]
        self.assertEqual(state["sleep_execution"]["state"], "sleeping")
        self.assertEqual(material[0].source.scope, context.scope)
        self.clock.advance(timedelta(minutes=1))
        sleep = (await self.status())["activities"][0]
        await self.command("wake", item=sleep)
        with self.assertRaises(TaskError):
            consumer.contributions(snapshot, context, self.clock.now())
        self.assertEqual(consumer.contributions(snapshot, context, self.clock.now(), historical=True), material)

    async def test_cancel_releases_shared_sleep_but_not_another_windows_management_authority(self):
        await self.ready()
        await self.command("sleep", minutes=10)
        sleep = (await self.status())["activities"][0]
        foreign = await self.request("same", data=await self.body("cancel", "same", item=sleep))
        self.assertEqual(foreign["data"]["code"], "life_view_stale")
        await self.command("cancel", item=sleep)
        self.assertEqual((await self.status("same"))["body_sleep"]["state"], "interrupted")
        await self.command("sleep", "same", minutes=15)
        self.assertEqual((await self.status())["body_sleep"]["state"], "sleeping")

    async def test_two_daily_schedules_do_not_put_the_same_role_back_to_sleep_after_waking(self):
        from zoneinfo import ZoneInfo
        await self.ready()
        due = (self.clock.now() + timedelta(minutes=1)).astimezone(ZoneInfo("Asia/Shanghai")).strftime("%H:%M")
        for target in ("first", "same"):
            await self.command("configure", target, key="daily", changes={"sleep_enabled": True,
                "sleep_time": due, "sleep_minutes": 1, "sleep_variation_minutes": 0})
        self.app.life_runtime.advance()
        self.clock.advance(timedelta(minutes=1))
        self.app.life_runtime.advance()
        self.assertEqual(len(self.app.role_state.body_clock.active(self.context(), "sleep")), 1)
        self.clock.advance(timedelta(minutes=1))
        for _ in range(3):
            self.app.life_runtime.advance()
        await self.until(lambda: not self.app.role_state.body_clock.active(self.context(), "sleep"))
        self.assertEqual(self.app.role_state.body_clock.active(self.context(), "sleep"), ())
        self.assertEqual((await self.status())["body_sleep"]["state"], "awake")

    async def test_shared_body_enters_the_original_persona_reply_and_survives_reload(self):
        await self.ready()
        first, same = self.enable_state("first"), self.enable_state("same")
        self.app.role_state.write(first, data=state_data(energy=41, hunger="有点饿", health="平稳",
            mood="心情平和", location="在自己的房间", busy="整理自己的想法"),
            expected_revision=0, operation_key="body")
        await self.accepted("same", "body-prompt", "现在感觉如何")
        payload = self.native.provider.client.state.calls[-1][0]
        materials = [m for message in payload["messages"] if message["role"] == "user"
                     for m in json.loads(message["content"]).get("materials", []) if m["purpose"] == "role_state"]
        self.assertEqual(len(materials), 1)
        view = json.loads(materials[0]["text"])["simulation"]
        self.assertEqual(view["values"]["hunger"], "有点饿")
        self.assertEqual(view["values"]["mood"], "心情平和")
        self.assertEqual(view["values"]["location"], "在自己的房间")
        self.assertEqual(view["values"]["busy"], "整理自己的想法")
        (await self.controller.stop()).unwrap()
        self.controller = self.make_controller(shared_config())
        self.register()
        (await self.controller.load_existing()).unwrap()
        restored = self.controller.session.application.role_state.read(self.context("same"))
        self.assertEqual(restored["values"]["energy"], 41)
        self.assertEqual(len(self.native.provider.client.state.calls), 1)

    async def test_native_persona_change_cannot_complete_an_old_roles_sleep_timer(self):
        self.window_personas["qq-main:FriendMessage:201"] = "original"
        await self.ready()
        await self.command("sleep", minutes=1)
        self.window_personas["qq-main:FriendMessage:200"] = "second"
        self.window_personas["qq-main:FriendMessage:201"] = "second"
        self.clock.advance(timedelta(minutes=2))
        self.app.life_runtime.advance()
        await self.until(lambda: not any(not worker.done() for routine in self.app.life_runtime.routines.values()
                                        for worker in routine._workers.values()))
        self.assertEqual(self.app.role_state.body_clock.read(self.context())["state"], "sleeping")
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_two_native_accounts_of_the_same_logical_bot_share_only_the_same_role_body(self):
        config = shared_config()
        config["multi_host"]["targets"][1]["bot_id"] = "bot-a"
        config["multi_host"]["personas"] = [p for p in config["multi_host"]["personas"] if p["bot_id"] == "bot-a"]
        await self.ready(config)
        first, second = self.enable_state("first"), self.enable_state("second")
        self.app.role_state.write(first, data=state_data(energy=33, hunger="有点饿"), expected_revision=0, operation_key="shared")
        self.assertEqual(self.app.role_state.read(second)["values"]["energy"], 33)
        self.assertIsNone(self.app.role_state.records.get(self.context("second", "role-b"), "current"))
        await self.command("sleep", minutes=10)
        self.assertEqual((await self.status("second"))["body_sleep"], (await self.status())["body_sleep"])


if __name__ == "__main__":
    unittest.main()
