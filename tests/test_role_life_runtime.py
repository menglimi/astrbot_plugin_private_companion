"""Use the real owner, task, source, model and journal layers in an isolated store."""

import asyncio
from dataclasses import replace
from datetime import timedelta
import json
import unittest

from companion_v2.configuration.registry import FieldRegistry
from companion_v2.execution.models import TaskContext, digest
from companion_v2.foundation.refs import AudienceRef
from companion_v2.model_gateway.routing import configuration_fields
from companion_v2.model_gateway.models import ProviderResponse
from companion_v2.role_work.reader import reading_profile
from companion_v2.role_work.life.management import RoleLifeManagement
from companion_v2.role_work.models import RoleActivityState as State
from companion_v2.role_work.journal.writing import writing_profiles
from tests.test_host_application import HostFixture
from tests.test_management_access import SCOPE
from tests.test_role_tool_loop import answered


class LifeFixture(HostFixture):
    def setUp(self):
        super().setUp()
        self.profiles += writing_profiles("primary", max_output_tokens=100) + (reading_profile("primary", max_output_tokens=100),)
        self.registry = FieldRegistry(version="life.test.v1", fields=configuration_fields(self.profiles))
        self.sequence = 0

    async def setup_life(self, *, enable=True):
        self.host_instance = await self.start()
        self.app = self.host_instance.application
        binding = self.app.tasks.identity.resolve_role(SCOPE, observed_at=self.clock.now()).unwrap()
        self.task_context = TaskContext(self.important, binding, AudienceRef(SCOPE, (self.important.subject,)))
        self.app.life_runtime.bind(self.task_context, "原人格")
        self.manager = RoleLifeManagement(self.app, self.task_context)
        self.model_behavior = self.model
        if enable:
            await self.configure(enabled=True, diary_enabled=False)

    async def model(self, envelope):
        fmt = envelope.original.output_format
        if fmt == "role_reading":
            return answered(json.dumps({"reflection": "这篇资料让我想到一个还未想明白的问题。"}, ensure_ascii=False))
        if fmt == "journal_derivatives":
            return answered(json.dumps({"summary": "读到一篇资料，记下一点想法。", "dream_fragments": [], "continuity": ""}, ensure_ascii=False))
        if fmt == "journal_selection":
            data = json.loads(next(m.payload for m in envelope.original.context.materials if m.purpose == "material_candidates"))
            return answered(json.dumps({"material_ids": [m["id"] for m in data[-8:]]}))
        kind = json.loads(next(m.payload for m in envelope.original.context.materials if m.purpose == "writing_date"))["kind"]
        return answered(json.dumps({"body": "我记得一个小小的念头。", "form": "随记",
            "recall": "fragments" if kind == "dream" else None, "afterglow": ""}, ensure_ascii=False))

    def status(self):
        return self.manager.status(self.core)

    async def command(self, action, item=None, **args):
        self.sequence += 1
        status = self.status()
        body = dict(action=action, operation_key="command-" + str(self.sequence), view_token=status["view_token"])
        if item:
            body.update(view_token=item["view_token"], expected_revision=item["revision"])
            if "activity_id" in item:
                body["activity_id"] = item["activity_id"]
            else:
                body.update(kind=item["kind"], local_date=item["local_date"])
        body.update(args)
        return await self.manager.command(self.core, body)

    async def configure(self, **changes):
        result = await self.command("configure", changes=changes, edit_token=self.status()["edit_token"])
        self.assertTrue(result.is_success, result)
        self.manager.life.refresh()
        return result

    async def add_text(self, text="这是实际提供给角色阅读的外部资料。"):
        result = await self.command("add_text", text=text, title="一篇阅读资料")
        self.assertTrue(result.is_success, result)
        return self.status()["activities"]

    async def settle(self):
        await self.until(lambda: not self.app.life_runtime.active_handles)


class LifeRuntimeTests(LifeFixture, unittest.IsolatedAsyncioTestCase):
    async def test_unknown_read_result_is_not_automatically_or_explicitly_replayed(self):
        await self.setup_life()
        book = (await self.add_text())[0]
        async def unknown(envelope):
            return ProviderResponse("unknown")
        self.model_behavior = unknown
        (await self.command("read", book)).unwrap()
        await self.settle()
        item = self.status()["activities"][0]
        self.assertEqual(item["task"]["state"], "unknown")
        self.assertEqual(item["state"], "paused")
        retry = await self.command("read", item)
        self.assertEqual(retry.failure.code, "life_reading_unresolved")
        self.assertEqual(len(self.called), 1)

    async def test_withdrawal_cancels_all_unread_spans_of_the_same_original(self):
        await self.setup_life()
        items = await self.add_text("甲" * 12001)
        (await self.command("withdraw", items[0])).unwrap()
        self.assertEqual({a["state"] for a in self.status()["activities"]}, {"cancelled"})
        await self.configure(reading_enabled=True)
        self.app.life_runtime.advance()
        self.assertEqual(self.called, [])

    async def test_journal_archive_cursor_does_not_skip_the_other_kind_on_a_boundary_date(self):
        from companion_v2.role_work.journal.models import JournalDraft, JournalKind, DreamRecall
        await self.setup_life()
        first = self.clock.now()
        for day in range(55):
            for kind in (JournalKind.DIARY, JournalKind.DREAM) if day % 2 else (JournalKind.DIARY,):
                target = self.app.journal_triggers.target(self.task_context, kind,
                    (first - timedelta(days=day)).date().isoformat(), "Asia/Shanghai")
                self.app.role_journal.save(JournalDraft(target, body="私人文字。",
                    recall=DreamRecall.FRAGMENTS if kind is JournalKind.DREAM else None), expected_revision=0,
                    operation_id=f"archive-{day}-{kind.value}")
        first_page = self.status()
        second = self.manager.status(self.core, before=first_page["next_date"])
        all_keys = {(j["kind"], j["local_date"]) for j in first_page["journals"] + second["journals"]}
        self.assertEqual(len(all_keys), 82)

    async def test_read_sleep_dream_diary_derivatives_and_archive_edits(self):
        await self.setup_life()
        book = (await self.add_text())[0]
        self.assertEqual((book["state"], len(self.called)), ("planned", 0))
        self.assertTrue((await self.command("read", book)).is_success)
        await self.settle()
        book = self.status()["activities"][0]
        self.assertEqual(book["state"], "completed", book)
        self.assertEqual(book["task"]["state"], "succeeded", book)
        self.assertIn("问题", book["reflection"])
        self.assertEqual(self.called[0].original.output_format, "role_reading")
        diary = (await self.command("diary")).unwrap()
        self.assertEqual(diary.state, "written")
        journal = self.status()["journals"][0]
        self.assertTrue(journal["summary"])
        self.assertTrue((await self.command("edit", journal, body="我重新写下这一点想法。")).is_success)
        self.assertFalse((await self.command("edit", journal, body="旧页面的修改")).is_success)
        current = self.status()["journals"][0]
        self.assertEqual(current["body"], "我重新写下这一点想法。")
        self.assertIsNone(current["summary"])
        self.assertTrue((await self.command("delete", current)).is_success)
        deleted = self.status()["journals"][0]
        self.assertEqual(deleted["state"], "deleted")
        self.assertTrue((await self.command("restore", deleted, body="主动恢复的正文")).is_success)
        sleep = (await self.command("sleep", minutes=1)).unwrap()["activity_id"]
        self.advance(self.app, 61)
        await self.until(lambda: any(j["kind"] == "dream" for j in self.status()["journals"]))
        await self.settle()
        dreams = [j for j in self.status()["journals"] if j["kind"] == "dream"]
        self.assertEqual(len(dreams), 1)
        self.assertTrue(dreams[0]["summary"])
        self.assertEqual(self.manager.life.activity(sleep).state, State.COMPLETED)
        self.assertEqual(self.sent, [])

    async def test_automatic_queue_covers_all_original_characters(self):
        await self.setup_life()
        original = "甲" * 6000 + "乙" * 111
        await self.add_text(original)
        await self.configure(reading_enabled=True)
        await self.until(lambda: all(a["state"] == "completed" for a in self.status()["activities"]))
        inputs = [json.loads(e.original.context.materials[0].payload) for e in self.called]
        self.assertEqual(sorted((x["start"], x["end"]) for x in inputs), [(0, 6000), (6000, 6111)])
        self.assertEqual("".join(x["text"] for x in sorted(inputs, key=lambda x: x["start"])), original)

    async def test_configuration_and_sleep_recover_without_replaying_finished_model(self):
        await self.setup_life()
        await self.configure(diary_enabled=True, diary_time="23:45")
        await self.command("sleep", minutes=1)
        await self.app.close()
        self.runtime.close()
        self.clock.advance(timedelta(minutes=2))
        self.runtime = self.open_runtime()
        self.host_instance = self.host(runtime=self.runtime)
        self.app = self.host_instance.application
        self.app.life_runtime.bind(self.task_context, "原人格")
        self.manager = RoleLifeManagement(self.app, self.task_context)
        await self.start(self.host_instance, task_generation=1, model_generation=1)
        await self.until(lambda: any(j["kind"] == "dream" for j in self.status()["journals"]))
        await self.settle()
        self.assertEqual(self.manager.life.settings["diary_time"], "23:45")
        self.assertIn(self.manager.life.schedule_id, self.app.journal_schedule._schedules)
        self.assertEqual(self.manager.life.activities()[0].updated_at, self.clock.now())
        count = len(self.called)
        for _ in range(3):
            self.app.life_runtime.advance()
        self.assertEqual(len(self.called), count)

    async def test_disabled_configuration_has_no_grants_or_automatic_work(self):
        await self.setup_life(enable=False)
        await self.add_text()
        self.app.life_runtime.advance()
        self.assertEqual(self.called, [])
        self.assertFalse((await self.command("sleep", minutes=1)).is_success)
        await self.configure(enabled=True, diary_enabled=False)
        await self.configure(enabled=False)
        self.assertFalse(self.app.tasks.identity.authorize(self.important, scope=SCOPE, capability="role.life.run", observed_at=self.clock.now()).is_success)
        self.assertTrue(self.app.tasks.identity.authorize(self.important, scope=SCOPE, capability="text", observed_at=self.clock.now()).is_success)
        self.assertEqual(self.app.journal_schedule._schedules, {})

    async def test_withdrawing_original_hides_reflection_and_invalidates_dependent_diary(self):
        await self.setup_life()
        book = (await self.add_text())[0]
        (await self.command("read", book)).unwrap()
        await self.settle()
        (await self.command("diary")).unwrap()
        book = self.status()["activities"][0]
        (await self.command("withdraw", book)).unwrap()
        status = self.status()
        self.assertIsNone(status["activities"][0]["reflection"])
        self.assertIsNone(status["journals"][0]["body"])
        self.assertEqual(status["journals"][0]["unavailable_reason"], "source_revoked")

    async def test_completed_reading_test_record_can_be_removed_from_the_shelf(self):
        await self.setup_life()
        book = (await self.add_text())[0]
        (await self.command("read", book)).unwrap()
        await self.settle()
        activity = self.status()["activities"][0]
        self.assertEqual(activity["state"], "completed")
        removed = await self.command("remove", activity)
        self.assertTrue(removed.is_success, removed.failure)
        self.assertEqual(self.status()["activities"], [])

    async def test_unfinished_reading_record_cannot_be_removed(self):
        await self.setup_life()
        book = (await self.add_text())[0]
        result = await self.command("remove", book)
        self.assertFalse(result.is_success)
        self.assertEqual(result.failure.code, "life_activity_not_removable")
        self.assertEqual(len(self.status()["activities"]), 1)

    async def test_read_failure_does_not_complete_and_requires_explicit_retry(self):
        await self.setup_life()
        book = (await self.add_text())[0]
        async def bad(envelope):
            return answered("invalid json")
        self.model_behavior = bad
        await self.configure(reading_enabled=True)
        await self.until(lambda: self.status()["activities"][0]["state"] == "paused")
        await self.settle()
        self.assertEqual(len(self.called), 1)
        self.model_behavior = self.model
        book = self.status()["activities"][0]
        (await self.command("read", book)).unwrap()
        await self.settle()
        self.assertEqual(self.status()["activities"][0]["state"], "completed")
        self.assertEqual(len(self.called), 2)

    async def test_admin_and_target_identity_cannot_be_replaced(self):
        await self.setup_life()
        data = self.status()
        request = dict(action="add_text", operation_key="unauthorized", view_token=data["view_token"], text="原文", title="书")
        denied = await self.manager.command(self.important, request)
        self.assertFalse(denied.is_success)
        wrong = replace(self.task_context, role_binding=replace(self.task_context.role_binding, revision=999))
        with self.assertRaises(Exception):
            RoleLifeManagement(self.app, wrong).status(self.core)
        self.assertEqual(self.status()["activities"], [])

    async def test_shutdown_cancels_reader_without_inventing_completion(self):
        await self.setup_life()
        book = (await self.add_text())[0]
        entered = asyncio.Event()
        async def held(envelope):
            entered.set()
            await asyncio.Event().wait()
        self.model_behavior = held
        (await self.command("read", book)).unwrap()
        await asyncio.wait_for(entered.wait(), 2)
        report = await self.app.close()
        self.assertEqual(report.state, "stopped")
        self.assertEqual(report.pending_handles, ())
        a = self.manager._activity(book["activity_id"])
        self.assertEqual(a.state, State.PAUSED)
        self.assertIsNone(a.reading)
