"""Private life management, provenance and concurrent persona routing."""

import asyncio
from datetime import timedelta
import unittest
from unittest.mock import patch

from tests.multi_life_support import MultiLifeFixture
from tests.group_host_support import group_config
from astrbot_host.management_identity import dashboard_operator
from companion_v2.execution.models import digest
from companion_v2.role_work.life.management import RoleLifeManagement
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.identity_policy.object_access import ObjectAccessPolicy


class MultiLifeTests(MultiLifeFixture, unittest.IsolatedAsyncioTestCase):
    async def test_inventory_private_routes_and_two_accounts_two_personas_keep_own_books(self):
        app = await self.initialize(group_config())
        ids, receipts = set(), set()
        for target, role in self.paths:
            path = self.paths[(target, role)]
            if target.startswith("group-"):
                self.assertIsNone(path)
                continue
            self.assertIn(path, self.routes)
            self.assertIn(path + "/command", self.routes)
            receipts.add((await self.enable(target, role))["operation_id"])
            book = await self.book(target, role)
            ids.add(book["activity_id"])
        self.assertEqual((len(ids), len(receipts)), (4, 4))
        for target in ("first", "second"):
            for role in ("role-a", "role-b"):
                status = await self.status(target, role)
                self.assertEqual((status["target_id"], status["role_id"], status["profile_id"]), (target, role, "shared-test"))
                self.assertEqual(len(status["activities"]), 1)
                self.assertTrue(status["activities"][0]["title"].startswith(target + role))
                self.assertEqual((status["running"], status["receipts"], status["journals"]), ([], [], []))
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertEqual(self.native.selections, {"qq-main": "original", "qq-second": "original"})
        self.assertTrue(all(r.instruction is None for r in app.life_runtime.routines.values()))

    async def test_request_identity_tokens_and_foreign_activity_are_rejected(self):
        app = await self.initialize()
        for target in ("first", "second"):
            await self.enable(target)
        book = await self.book()
        command = await self.body("withdraw", item=book)
        for username in (None, "", "api_key:123"):
            self.assertEqual((await self.request(data=command, username=username))["data"]["code"], "dashboard_identity_required")
        for extra in ({"target_id": "second"}, {"role_id": "role-b"}, {"context": {}}, {"operator": "admin"}):
            self.assertEqual((await self.request(data=command | extra))["data"]["code"], "life_command_invalid")
        for query in ("target_id=second", "role_id=role-b", "before=2026-09-21&before=2026-09-20"):
            self.assertEqual((await self.request(query=query))["data"]["code"], "life_query_invalid")
            self.assertEqual((await self.request(data=command, query=query))["data"]["code"], "life_command_invalid")
        self.assertEqual((await self.request("second", data=command))["data"]["code"], "life_view_stale")
        manager = RoleLifeManagement(app, self.context("second"))
        operator = dashboard_operator(app.runtime, "admin", at=self.clock.now())
        token = manager.token(operator, manager.authorize(operator), book["activity_id"], book["revision"])
        self.assertEqual((await self.request("second", data=command | {"view_token": token}))["data"]["code"], "life_activity_unavailable")
        policy = ObjectAccessPolicy(app.runtime)
        policy.set_tier(operator.subject, AuthorizationTier.STANDARD, expected_revision=policy.revision, observed_at=self.clock.now())
        self.assertEqual((await self.request(data=command))["data"]["code"], "authorization_denied")

    async def test_legacy_receipts_replay_only_in_original_scope(self):
        app = await self.initialize()
        config = await self.body("configure", changes={"enabled": True, "diary_enabled": False})
        add = await self.body("add_text", key="book", title="旧版本原文", text="旧版本保存的完整原文。")
        def legacy(manager, prefix, operator, key):
            return prefix + digest([operator.subject, key])
        with patch.object(RoleLifeManagement, "operation_id", legacy):
            old_config = self.ok(await self.request(data=config))
            # Configuration changed the authority token; refresh the book command.
            add = await self.body("add_text", key="book", title="旧版本原文", text="旧版本保存的完整原文。")
            old_book = self.ok(await self.request(data=add))
        self.assertTrue(self.ok(await self.request(data=config))["replayed"])
        self.assertTrue(self.ok(await self.request(data=add))["replayed"])
        other_config = await self.enable("second")
        other_book = await self.book("second")
        self.assertNotEqual(old_config["operation_id"], other_config["operation_id"])
        self.assertNotIn(other_book["activity_id"], old_book["activity_ids"])
        self.assertEqual(len((await self.status())["activities"]), 1)
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_awaiting_request_body_keeps_its_own_target(self):
        await self.initialize()
        body = await self.body("add_text", title="第一个目标", text="只属于第一个目标的资料。")
        entered, release = asyncio.Event(), asyncio.Event()
        slow = asyncio.create_task(self.request(data=body, gate=(entered, release)))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await self.book("second", "role-b")
            self.assertEqual((await self.status())["activities"], [])
        finally:
            release.set()
            self.ok(await slow)
        self.assertTrue((await self.status())["activities"][0]["title"].startswith("第一个目标"))
        self.assertTrue((await self.status("second", "role-b"))["activities"][0]["title"].startswith("secondrole-b"))

    async def test_original_personas_read_write_sleep_and_dream_independently(self):
        self.native.selections["qq-second"] = "second"
        self.window_personas["qq-second:FriendMessage:200"] = "second"
        app = await self.initialize()
        pairs = (("first", "role-a", "原人格"), ("second", "role-b", "第二人格"))
        for target, role, _ in pairs:
            await self.enable(target, role)
            await self.book(target, role)
        await self.start()
        for target, role, persona in pairs:
            book = (await self.status(target, role))["activities"][0]
            await self.command("read", target, role, item=book)
            await self.settled()
            status = await self.status(target, role)
            self.assertEqual(status["activities"][0]["state"], "completed", status)
            self.assertIn(persona, status["activities"][0]["reflection"])
            written = await self.command("diary", target, role)
            self.assertEqual(written["state"], "written", written)
            self.assertIn(persona, (await self.status(target, role))["journals"][0]["body"])
        first_receipts = {r["key"] for r in (await self.status())["receipts"]}
        second_receipts = {r["key"] for r in (await self.status("second", "role-b"))["receipts"]}
        self.assertTrue(first_receipts and second_receipts and first_receipts.isdisjoint(second_receipts))
        await self.command("sleep", minutes=1)
        await self.command("sleep", "second", "role-b", minutes=2)
        self.clock.advance(timedelta(seconds=61))
        app.notify()
        await self.until(lambda: any(a.work.kind == "sleep" and a.state.value == "completed" for a in self.routine().activities()))
        await self.settled()
        self.assertTrue(any(a.work.kind == "sleep" and a.state.value == "active" for a in self.routine("second", "role-b").activities()))
        self.clock.advance(timedelta(seconds=60))
        app.notify()
        await self.until(lambda: any(a.work.kind == "sleep" and a.state.value == "completed" for a in self.routine("second", "role-b").activities()))
        await self.settled()
        for target, role, persona in pairs:
            status = await self.status(target, role)
            dreams = [j for j in status["journals"] if j["kind"] == "dream"]
            self.assertEqual(len(dreams), 1, status)
            self.assertIn(persona, dreams[0]["body"])
            self.assertIsNone((await self.status(target, role))["failure_code"])
        generations = app.configuration.store.read_all(
            "SELECT generation FROM configuration_applications WHERE group_id = 'role.life'", ())
        self.assertEqual({r["generation"] for r in generations}, {1})
        self.assertEqual(len(generations), 2)
        self.assertTrue(all(not p.client._api._wsr_api.calls for p in self.native.platforms.values()))

    async def test_daily_schedules_keep_own_context_time_and_configuration_generation(self):
        self.native.selections["qq-second"] = "second"
        self.window_personas["qq-second:FriendMessage:200"] = "second"
        app = await self.initialize()
        for target, role, at in (("first", "role-a", "08:01"), ("second", "role-b", "08:02")):
            await self.enable(target, role, diary_enabled=True, diary_time=at)
            await self.book(target, role)
        await self.start()
        for target, role in (("first", "role-a"), ("second", "role-b")):
            await self.command("read", target, role, item=(await self.status(target, role))["activities"][0])
            await self.settled()
        first, second = await self.status(), await self.status("second", "role-b")
        self.assertNotEqual(first["next_diary_at"], second["next_diary_at"])
        self.assertEqual(len(app.journal_schedule._schedules), 2)
        self.clock.advance(timedelta(seconds=61))
        app.notify()
        await self.until(lambda: self.routine().schedule_id in app.journal_schedule.results)
        await self.settled()
        self.assertEqual(len((await self.status())["journals"]), 1)
        self.assertEqual((await self.status("second", "role-b"))["journals"], [])
        await self.command("configure", key="disable-first", changes={"enabled": False})
        app.life_runtime.advance()
        self.assertEqual(set(app.journal_schedule._schedules), {self.routine("second", "role-b").schedule_id})
        self.clock.advance(timedelta(seconds=60))
        app.notify()
        await self.until(lambda: self.routine("second", "role-b").schedule_id in app.journal_schedule.results)
        await self.settled()
        self.assertIn("第二人格", (await self.status("second", "role-b"))["journals"][0]["body"])
        self.assertIsNone((await self.status("second", "role-b"))["failure_code"])


if __name__ == "__main__":
    unittest.main()
