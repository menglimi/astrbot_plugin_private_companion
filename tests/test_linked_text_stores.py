"""Shared text records through the real QQ/Telegram reply and tool path."""
from tests import astrbot_data_root  # noqa: F401

import unittest

from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA
from companion_v2.agenda.models import AgendaTarget
from tests.linked_reply_support import LinkedTelegramFixture
from tests import test_telegram_text_tools as text_tools


class LinkedTextStoreFixture(LinkedTelegramFixture):
    def make_controller(self, config=None):
        from tests.multi_host_support import multi_config
        from companion_v2.adapters.astrbot.staging_config import REQUIRED_CAPABILITIES
        from companion_v2.conversation.agenda_tools import TOOL_FIELDS
        config = config or multi_config(capabilities=sorted(REQUIRED_CAPABILITIES | set(TOOL_FIELDS) | {
            "memory.read", "memory.propose", "memory.reflect", "memory.subscribe", "memory.forget",
            "calendar.read", "calendar.write", "role.growth.read", "role.growth.propose", "role.life.read",
            "relationship.memory.read", "relationship.memory.propose", "reminder.deliver", "memo.reminder.set",
            "configuration.write"}))
        return super().make_controller(config)
    turn = text_tools.TelegramTextToolTests.turn
    run_tools = text_tools.TelegramTextToolTests.run_tools
    prior = staticmethod(text_tools.TelegramTextToolTests.prior)

    @staticmethod
    def context(event):
        return event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["prepared"]["context"]

    @staticmethod
    def target(context):
        return AgendaTarget(context.scope, context.role_binding.role, context.actor.subject)

    def revoke(self, context, capability):
        directory = self.controller.authority.identities
        grants = [grant for grant in directory.grants_for_subject(context.actor.subject)
                  if grant.scope == context.scope and grant.capability == capability]
        self.assertTrue(grants, capability)
        for grant in grants:
            directory.revoke_grant(grant.grant_id, revoked_at=self.clock.now(), expected_revision=directory.revision)

    @staticmethod
    def calendar_data(kind, title):
        return {"kind": kind, "title": title, "detail": "synthetic plan", "duration_minutes": 30, "presence": "",
                "schedule": {"anchor": "2026-10-11T10:00:00+00:00", "timezone_name": "UTC"}}

    def calendar_command(self, action, identifier, revision, data=None):
        return lambda args: {"action": action, "id": identifier, "expected_revision": revision,
            "evidence_handles": self.prior(args, 0)["evidence_handles"], **({"data": data} if data else {})}


class LinkedTextStoreTests(LinkedTextStoreFixture, unittest.IsolatedAsyncioTestCase):
    async def test_notes_share_existing_and_new_records_without_moving_sources_or_permissions(self):
        independent, _ = await self.run_tools(1, [("memo.create", {"title": "unrelated", "body": "old Telegram"})])
        old_event, saved = await self.run_tools(0, [("memo.create", {"title": "tea", "body": "old QQ"})], identifier=2)
        memo_id = saved[0]["payload"]["object_id"]
        self.assertEqual((await self.link())["status"], "ok")
        event, results = await self.run_tools(1, [("memo.list", {}), ("memo.read", {"memo_id": memo_id}),
            ("memo.edit", {"memo_id": memo_id, "expected_revision": 1, "changes": {"body": "shared tea"}}),
            ("operation.lookup", {"operation_id": saved[0]["operation_id"]}),
            ("memo.create", {"title": "coffee", "body": "new Telegram"})], identifier=3)
        self.assertEqual([item["memo_id"] for item in results[0]["payload"]], [memo_id])
        self.assertEqual(results[1]["payload"]["target"]["scope"]["platform"], "qq-main")
        peer_id = results[4]["payload"]["object_id"]
        _, back = await self.run_tools(0, [("memo.read", {"memo_id": memo_id}), ("memo.read", {"memo_id": peer_id}),
            ("memo.complete", {"memo_id": peer_id, "expected_revision": 1}),
            ("memo.reopen", {"memo_id": peer_id, "expected_revision": 2}),
            ("memo.pin", {"memo_id": peer_id, "expected_revision": 3}),
            ("memo.unpin", {"memo_id": peer_id, "expected_revision": 4}),
            ("memo.delete", {"memo_id": peer_id, "expected_revision": 5})], identifier=4)
        self.assertEqual(back[0]["payload"]["body"], "shared tea")
        self.assertEqual(back[1]["payload"]["target"]["scope"]["platform"], "tg-main")
        self.assertEqual(back[-1]["payload"]["state"], "deleted")
        stranger = self.context(independent)
        denied = self.app.agenda.read_memo(stranger.actor, target=self.target(stranger), memo_id=memo_id,
                                          observed_at=self.clock.now())
        self.assertFalse(denied.is_success)
        reader = self.context(event)
        self.revoke(reader, "memo.edit")
        from companion_v2.foundation.refs import OperationRef, VersionRef
        operation = OperationRef("refused-note-edit", "agenda", reader.scope, VersionRef("refused-note-edit", 1), "refused-note-edit")
        denied = self.app.agenda.change_memo(reader.actor, target=self.target(reader), memo_id=memo_id,
            action="edit", changes={"body": "forbidden"}, expected_revision=2,
            expected_role_revision=reader.role_binding.revision, operation=operation, observed_at=self.clock.now())
        self.assertFalse(denied.is_success)
        self.assertEqual(denied.failure.code, "authorization_revoked")
        self.assertEqual(self.app.agenda.repository.memo(memo_id).target.scope, self.context(old_event).scope)

    async def test_reminders_share_records_and_edits_keep_the_original_destination(self):
        schedule = {"anchor": "2026-10-11T10:00:00+00:00", "timezone_name": "UTC"}
        outside, _ = await self.run_tools(1, [("reminder.list", {})])
        origin, saved = await self.run_tools(0, [("memo.create", {"title": "tea", "body": "brew tea", "schedule": schedule}),
            ("reminder.create", lambda args: {"memo_id": self.prior(args)["payload"]["object_id"], "expected_memo_revision": 1}),
            ("reminder.create", {"message": "standalone", "schedule": schedule})], identifier=2)
        linked_id, standalone_id = [item["payload"]["object_id"] for item in saved[1:]]
        self.assertEqual((await self.link())["status"], "ok")
        event, results = await self.run_tools(1, [("reminder.list", {}), ("reminder.read", {"reminder_id": linked_id}),
            ("reminder.reschedule", {"reminder_id": linked_id, "expected_revision": 1, "expected_memo_revision": 1,
                "schedule": {**schedule, "anchor": "2026-10-12T10:00:00+00:00"}}),
            ("reminder.cancel", {"reminder_id": standalone_id, "expected_revision": 1}),
            ("reminder.create", {"message": "Telegram reminder", "schedule": schedule})], identifier=3)
        self.assertEqual({item["reminder_id"] for item in results[0]["payload"]}, {linked_id, standalone_id})
        self.assertEqual(results[1]["payload"]["target"]["scope"]["platform"], "qq-main")
        peer_id = results[-1]["payload"]["object_id"]
        _, back = await self.run_tools(0, [("reminder.read", {"reminder_id": linked_id}),
            ("reminder.read", {"reminder_id": standalone_id}), ("reminder.read", {"reminder_id": peer_id}),
            ("reminder.cancel", {"reminder_id": peer_id, "expected_revision": 1})], identifier=4)
        from datetime import datetime
        self.assertEqual(datetime.fromisoformat(back[0]["payload"]["schedule"]["anchor"]),
                         datetime.fromisoformat("2026-10-12T10:00:00+00:00"))
        self.assertEqual(back[0]["payload"]["target"]["scope"]["platform"], "qq-main")
        self.assertEqual(back[1]["payload"]["state"], "cancelled")
        self.assertEqual(back[2]["payload"]["target"]["scope"]["platform"], "tg-main")
        reader = self.context(event)
        self.revoke(reader, "reminder.read")
        for context in (reader, self.context(outside)):
            denied = self.app.agenda.read_reminder(context.actor, target=self.target(context), reminder_id=linked_id,
                                                  observed_at=self.clock.now())
            self.assertFalse(denied.is_success)
        original = self.app.agenda.repository.reminder(linked_id)
        self.assertEqual(original.target.scope, self.context(origin).scope)
        self.assertEqual(original.requester.principal.platform, "tg-main")

    async def test_non_role_calendar_records_are_shared_with_original_evidence(self):
        outside = self.context(await self.turn(1, "independent", 1))
        origin, _ = await self.run_tools(0, [("turn.evidence", {}),
            ("calendar.command", self.calendar_command("create", "reported-tea", 0, self.calendar_data("user_report", "tea"))),
            ("calendar.command", self.calendar_command("create", "promised-tea", 0, self.calendar_data("commitment", "tea together")))], identifier=2)
        self.assertEqual((await self.link())["status"], "ok")
        event, results = await self.run_tools(1, [("turn.evidence", {}), ("calendar.query", {}),
            ("calendar.command", self.calendar_command("revise", "reported-tea", 1, self.calendar_data("user_report", "shared tea"))),
            ("calendar.command", self.calendar_command("cancel", "promised-tea", 1)),
            ("calendar.command", self.calendar_command("create", "telegram-plan", 0, self.calendar_data("user_report", "Telegram tea")))], identifier=3)
        self.assertEqual({item["id"] for item in results[1]["payload"]["items"]}, {"reported-tea", "promised-tea"})
        self.assertEqual({source["scope"]["platform"] for item in results[1]["payload"]["items"] for source in item["sources"]}, {"qq-main"})
        _, back = await self.run_tools(0, [("calendar.query", {})], identifier=4)
        records = {item["id"]: item for item in back[0]["payload"]["items"]}
        self.assertEqual(records["reported-tea"]["title"], "shared tea")
        self.assertEqual(records["promised-tea"]["state"], "cancelled")
        self.assertEqual(records["telegram-plan"]["sources"][0]["scope"]["platform"], "tg-main")
        calendar = self.app.tools.calendar.calendar
        self.assertEqual(calendar.records.version(self.context(event), "reported-tea", 1)["sources"][0]["scope"],
                         self.context(origin).scope.document())
        from companion_v2.execution.models import TaskError
        with self.assertRaises(TaskError):
            calendar.read(outside, "reported-tea")
        reader = self.context(event)
        self.revoke(reader, "conversation.reply")
        with self.assertRaises(TaskError) as denied:
            calendar.read(reader, "reported-tea")
        self.assertEqual(denied.exception.code, "authorization_revoked")

    async def test_active_calendar_pages_include_linked_scopes_and_filter_before_limiting(self):
        from datetime import datetime
        from companion_v2.execution.models import encode

        self.assertEqual((await self.link())["status"], "ok")
        context = self.context(await self.turn(1, "calendar page", 1))
        records = self.app.calendar.records
        keys = (records.role_key(context), *records.conversation_keys(context))
        self.assertGreaterEqual(len(set(keys)), 3)
        with self.app.runtime.transaction() as connection:
            for index, key in enumerate(keys):
                # More expired rows than a page may never hide an active peer record.
                for number in range(4):
                    identifier = f"a-expired-{index}-{number}"
                    record = {"id": identifier, "kind": "user_report", "state": "accepted",
                        "schedule": {"frequency": "once", "anchor": "2026-10-01T10:00:00+00:00"},
                        "exceptions": {}}
                    connection.execute("INSERT INTO calendar_records VALUES (?, ?, 1, ?, ?)",
                        (key, identifier, "2026-10-01T00:00:00+00:00", encode(record)))
                for label, frequency, anchor, exceptions, state in (
                    ("future", "once", "2026-10-12T10:00:00+00:00", {}, "accepted"),
                    ("recurring", "daily", "2026-10-01T10:00:00+00:00", {}, "accepted"),
                    ("moved", "once", "2026-10-01T10:00:00+00:00",
                     {"0": "2026-10-12T10:00:00+00:00"}, "accepted"),
                    ("cancelled", "daily", "2026-10-12T10:00:00+00:00", {}, "cancelled")):
                    identifier = f"z-{label}-{index}"
                    record = {"id": identifier, "kind": "user_report", "state": state,
                        "schedule": {"frequency": frequency, "anchor": anchor}, "exceptions": exceptions}
                    connection.execute("INSERT INTO calendar_records VALUES (?, ?, 1, ?, ?)",
                        (key, identifier, "2026-10-01T00:00:00+00:00", encode(record)))
        seen, cursor = [], ""
        while True:
            page = records.list(context, after_id=cursor, limit=2, kinds=("user_report",),
                active_since=datetime.fromisoformat("2026-10-11T00:00:00+00:00"))
            if not page:
                break
            seen.extend(item["id"] for item in page)
            cursor = page[-1]["id"]
        self.assertEqual(seen, sorted(f"z-{label}-{index}" for index in range(len(keys))
            for label in ("future", "recurring", "moved")))

    async def test_growth_list_and_revision_share_the_record_but_keep_each_evidence_source(self):
        outside = self.context(await self.turn(1, "independent", 1))
        origin, saved = await self.run_tools(0, [("turn.evidence", {}),
            ("role.growth.propose", lambda args: {"kind": "goal", "title": "paint together",
                "evidence_handles": self.prior(args, 0)["evidence_handles"]})], identifier=2)
        growth_id = saved[1]["payload"]["growth_id"]
        self.assertEqual((await self.link())["status"], "ok")
        event, results = await self.run_tools(1, [("turn.evidence", {}), ("role.growth.list", {"limit": 1}),
            ("role.growth.revise", lambda args: {"growth_id": growth_id, "expected_revision": 1,
                "title": "paint a landscape", "description": "shared practice", "progress_note": "starting",
                "evidence_handles": self.prior(args, 0)["evidence_handles"]}),
            ("role.growth.propose", lambda args: {"kind": "skill", "title": "brushwork",
                "evidence_handles": self.prior(args, 0)["evidence_handles"]})], identifier=3)
        self.assertEqual([item["growth_id"] for item in results[1]["payload"]["items"]], [growth_id])
        _, back = await self.run_tools(0, [("role.growth.list", {"limit": 1}),
            ("role.growth.list", lambda args: {"limit": 1, "after_id": self.prior(args)["payload"]["next_cursor"]})], identifier=4)
        self.assertEqual({item["title"] for result in back for item in result["payload"]["items"]},
                         {"paint a landscape", "brushwork"})
        ledger = self.app.tools.growth.ledger
        owner = self.app.tools.growth.reader.owner(self.context(event))
        self.assertEqual(ledger.revision(growth_id, 1, owner=owner).sources[0].scope, self.context(origin).scope)
        self.assertEqual(ledger.record(growth_id, owner=owner).sources[0].scope, self.context(event).scope)
        for context in (outside, self.context(event)):
            if context is not outside:
                self.revoke(context, "role.growth.read")
            self.assertEqual(self.app.tools.growth.catalog_for(context), ())

    async def test_calendar_reminder_can_be_cancelled_from_the_linked_peer(self):
        origin, _ = await self.run_tools(0, [("turn.evidence", {}),
            ("calendar.command", self.calendar_command("create", "reminded-plan", 0, self.calendar_data("role_plan", "tea"))),
            ("calendar.command", self.calendar_command("remind", "reminded-plan", 1))])
        calendar = self.app.tools.calendar.calendar
        reminder_id = calendar.read(self.context(origin), "reminded-plan")["reminder_id"]
        self.assertEqual((await self.link())["status"], "ok")
        _, results = await self.run_tools(1, [("turn.evidence", {}),
            ("calendar.command", self.calendar_command("cancel", "reminded-plan", 2))], identifier=2)
        self.assertEqual(results[1]["payload"]["state"], "cancelled")
        reminder = self.app.agenda.repository.reminder(reminder_id)
        self.assertEqual(reminder.state, "cancelled")
        self.assertEqual(reminder.target.scope, self.context(origin).scope)

    async def test_confirmed_growth_context_uses_the_same_records_as_the_linked_tools(self):
        origin, saved = await self.run_tools(0, [("turn.evidence", {}),
            ("role.growth.propose", lambda args: {"kind": "skill", "title": "shared brushwork",
                "evidence_handles": self.prior(args, 0)["evidence_handles"]})])
        source = self.context(origin)
        reader = self.app.tools.growth.reader
        # Synthetic authenticated human confirmation at the existing ledger port.
        reader.ledger.confirm(saved[1]["payload"]["growth_id"], owner=reader.owner(source), scope=source.scope,
            operation_id="confirm-shared-brushwork", expected_revision=1, actor=source.actor.subject,
            observed_at=self.clock.now())
        self.assertEqual((await self.link())["status"], "ok")
        event, _ = await self.run_tools(1, [("role.growth.list", {})], identifier=2)
        context = self.context(event)
        candidates = reader.candidates(context, self.clock.now())
        self.assertEqual(len(candidates.items), 1)
        self.assertIn("shared brushwork", candidates.items[0].payload)
        self.assertEqual(reader.read([candidates.items[0].reference], context, self.clock.now()), candidates.items)
        self.revoke(context, "role.growth.read")
        from companion_v2.execution.models import TaskError
        with self.assertRaises(TaskError):
            reader.read([candidates.items[0].reference], context, self.clock.now())

    async def test_legacy_memory_query_and_verification_use_the_existing_companion_mapping(self):
        import json
        import sqlite3
        from tests.test_legacy_memory_materials import MEMORY_DDL, TIMELINE_DDL
        from companion_v2.memory.legacy_materials import LegacyMemoryBinding, LegacyOwnerSelector
        origin = self.context(await self.turn(0, "existing companion", 1))
        snapshot = self.root / "synthetic-legacy.db"
        selector = LegacyOwnerSelector(origin.scope.bot_id, origin.scope.platform,
            origin.role_binding.role.subject_id, "private", "synthetic-session", "synthetic-user")
        with sqlite3.connect(snapshot) as connection:
            connection.executescript(MEMORY_DDL + ";" + TIMELINE_DDL)
            connection.execute("""INSERT INTO memories(id,memory_type,subject_kind,subject_id,object_kind,object_id,
                scope,session_id,platform,visibility,sayability,reality_level,lifecycle,content,owner_bot_id,
                validity_status,sensitivity,review_status,metadata,occurred_at,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("synthetic-legacy", "stable_fact", "user", selector.actor_id, "bot", selector.bot_id,
                 "private", selector.session_id, selector.platform, "private_pair", "direct", "real_user_fact",
                 "stable_memory", "用户喜欢蓝风铃", selector.bot_id, "active", "private", "auto",
                 json.dumps({"persona_id": selector.persona_id}), "2026-01-02T03:04:05+00:00", "2026-01-02T03:04:05+00:00"))
        binding = LegacyMemoryBinding(selector, origin.scope.installation_id, origin.scope.bot_id,
            origin.role_binding.role.subject_id, origin.actor.subject.subject_id, origin.scope.platform, origin.scope.account_id)
        legacy = self.app.tools.memory.memory.query.legacy
        self.assertEqual(legacy.import_snapshot(snapshot, source_fingerprint="a" * 64, bindings=(binding,))["imported"]["memories"], 1)
        _, original = await self.run_tools(0, [("memory.legacy.query", {"query_intent": "蓝风铃"})], identifier=2)
        self.assertEqual((await self.link())["status"], "ok")
        event, shared = await self.run_tools(1, [("memory.legacy.query", {"query_intent": "蓝风铃"})], identifier=3)
        items = shared[0]["payload"]["items"]
        self.assertEqual(items, original[0]["payload"]["items"])
        self.assertEqual(items[0]["text"], "用户喜欢蓝风铃")
        self.assertTrue(legacy.verify(self.context(event), items))
        self.revoke(self.context(event), "memory.read")
        self.assertFalse(legacy.query(self.context(event), query_intent="蓝风铃").is_success)

    async def test_diagnostic_proposal_read_shares_the_original_review_with_current_endpoint_authority(self):
        outside = self.context(await self.turn(1, "independent", 1))
        self.assertEqual((await self.link())["status"], "ok")
        origin, saved = await self.run_tools(0, [
            ("diagnostics.inspect", {"check": "configuration", "group_id": "conversation.presentation",
                "configuration_target": "current_window"}),
            ("diagnostics.propose", lambda args: {"report_id": self.prior(args)["report"]["report_id"],
                "changes": {"conversation.presentation.private_debounce_enabled": True}, "rationale": "shared review"})], identifier=2)
        proposal = saved[1]["proposal"]
        event, results = await self.run_tools(1, [("diagnostics.proposal.read", {"proposal_id": proposal["proposal_id"]})], identifier=3)
        self.assertEqual(results[0]["proposal"]["proposal_id"], proposal["proposal_id"])
        self.assertEqual(results[0]["proposal"]["review"], proposal["review"])
        # Reviews seal the originating actor's configuration edit token. A peer
        # may inspect the same review, but must obtain its own review to confirm.
        self.assertEqual(results[0]["proposal"]["state"], "stale")
        _, cancelled = await self.run_tools(1, [("diagnostics.proposal.cancel", {"proposal_id": proposal["proposal_id"]})],
            text="陪伴 答疑取消 " + proposal["proposal_id"], identifier=4)
        self.assertEqual(cancelled[0]["proposal"]["state"], "cancelled")
        from companion_v2.diagnostics.models import DiagnosticContext
        from companion_v2.diagnostics.repairs import RepairProposalService
        repairs = RepairProposalService(self.app.tools.diagnostics)
        context = DiagnosticContext(outside.actor, outside.role_binding, outside.audience)
        self.assertFalse(repairs.read(context, proposal_id=proposal["proposal_id"]).is_success)
        reader = self.context(event)
        self.revoke(reader, "configuration.read")
        context = DiagnosticContext(reader.actor, reader.role_binding, reader.audience)
        denied = repairs.read(context, proposal_id=proposal["proposal_id"])
        self.assertFalse(denied.is_success)
        self.assertEqual(denied.failure.code, "authorization_revoked")
        self.assertEqual(self.context(origin).scope.platform, "qq-main")

    async def test_shared_text_records_survive_restart_without_the_original_endpoint(self):
        origin, saved = await self.run_tools(0, [("turn.evidence", {}),
            ("memo.create", {"title": "persistent note", "body": "keep this"}),
            ("reminder.create", {"message": "persistent reminder", "schedule": self.calendar_data("role_plan", "")["schedule"]}),
            ("calendar.command", self.calendar_command("create", "persistent-calendar", 0, self.calendar_data("user_report", "persistent plan"))),
            ("role.growth.propose", lambda args: {"kind": "goal", "title": "persistent growth",
                "evidence_handles": self.prior(args, 0)["evidence_handles"]})])
        memo_id, reminder_id = [item["payload"]["object_id"] for item in saved[1:3]]
        self.assertEqual((await self.link())["status"], "ok")
        self.assertEqual((await self.link(revision=1, members=["second"]))["status"], "ok")
        await self.controller.terminate()
        self.native.platforms.pop("qq-main")
        self.controller = self.make_controller()
        self.plugin.staging = self.controller
        result = await self.controller.start()
        self.assertTrue(result.is_success, result.failure)
        self.app = self.controller.session.application
        _, results = await self.run_tools(1, [("memo.read", {"memo_id": memo_id}),
            ("memo.edit", {"memo_id": memo_id, "expected_revision": 1, "changes": {"body": "still shared"}}),
            ("reminder.read", {"reminder_id": reminder_id}),
            ("reminder.cancel", {"reminder_id": reminder_id, "expected_revision": 1}),
            ("calendar.query", {}), ("role.growth.list", {})], identifier=2)
        self.assertEqual(results[0]["payload"]["target"]["scope"], self.context(origin).scope.document())
        self.assertEqual(results[2]["payload"]["target"]["scope"], self.context(origin).scope.document())
        self.assertEqual(results[4]["payload"]["items"][0]["id"], "persistent-calendar")
        self.assertEqual(results[5]["payload"]["items"][0]["title"], "persistent growth")


class RemovedMemberRecordTests(LinkedTextStoreFixture, unittest.IsolatedAsyncioTestCase):
    """What a member recorded stays the conversation's when that member is removed afterwards."""

    async def records_of_a_member_removed_afterwards(self, made_on):
        remaining = 1 - made_on
        schedule = self.calendar_data("role_plan", "")["schedule"]
        self.assertEqual((await self.link())["status"], "ok")
        origin, saved = await self.run_tools(made_on, [("turn.evidence", {}),
            ("memo.create", {"title": "kept note", "body": "made by the member that leaves"}),
            ("reminder.create", {"message": "moved later", "schedule": schedule}),
            ("reminder.create", {"message": "cancelled later", "schedule": schedule}),
            ("calendar.command", self.calendar_command("create", "kept-plan", 0,
                                                       self.calendar_data("user_report", "kept plan"))),
            ("role.growth.propose", lambda args: {"kind": "goal", "title": "kept growth",
                "evidence_handles": self.prior(args, 0)["evidence_handles"]}),
            ("diagnostics.inspect", {"check": "configuration", "group_id": "conversation.presentation",
                "configuration_target": "current_window"}),
            ("diagnostics.propose", lambda args: {"report_id": self.prior(args)["report"]["report_id"],
                "changes": {"conversation.presentation.private_debounce_enabled": True}, "rationale": "kept review"})])
        removed = self.context(origin)
        memo_id, moved_id, cancelled_id = [item["payload"]["object_id"] for item in saved[1:4]]
        growth_id, proposal_id = saved[5]["payload"]["growth_id"], saved[7]["proposal"]["proposal_id"]
        self.assertEqual((await self.link(revision=1, members=[("first", "second")[remaining]]))["status"], "ok")
        event, results = await self.run_tools(remaining, [("turn.evidence", {}),
            ("memo.list", {}), ("memo.read", {"memo_id": memo_id}),
            ("memo.edit", {"memo_id": memo_id, "expected_revision": 1, "changes": {"body": "edited by who remains"}}),
            ("reminder.list", {}), ("reminder.read", {"reminder_id": moved_id}),
            ("reminder.reschedule", {"reminder_id": moved_id, "expected_revision": 1,
                "schedule": {**schedule, "anchor": "2026-10-12T10:00:00+00:00"}}),
            ("reminder.cancel", {"reminder_id": cancelled_id, "expected_revision": 1}),
            ("calendar.query", {}),
            ("calendar.command", self.calendar_command("revise", "kept-plan", 1,
                                                       self.calendar_data("user_report", "revised plan"))),
            ("role.growth.list", {}),
            ("role.growth.revise", lambda args: {"growth_id": growth_id, "expected_revision": 1,
                "title": "revised growth", "description": "kept", "progress_note": "going on",
                "evidence_handles": self.prior(args, 0)["evidence_handles"]}),
            ("diagnostics.proposal.read", {"proposal_id": proposal_id})], identifier=2)
        self.assertEqual([item["memo_id"] for item in results[1]["payload"]], [memo_id])
        self.assertEqual(results[2]["payload"]["target"]["scope"], removed.scope.document())
        self.assertEqual(results[3]["payload"]["revision"], 2)
        self.assertEqual({item["reminder_id"] for item in results[4]["payload"]}, {moved_id, cancelled_id})
        self.assertEqual(results[5]["payload"]["target"]["scope"], removed.scope.document())
        self.assertEqual((results[6]["payload"]["state"], results[7]["payload"]["state"]), ("active", "cancelled"))
        self.assertEqual([item["id"] for item in results[8]["payload"]["items"]], ["kept-plan"])
        self.assertEqual(results[9]["payload"]["revision"], 2)
        self.assertEqual([item["growth_id"] for item in results[10]["payload"]["items"]], [growth_id])
        growth = self.app.tools.growth
        self.assertEqual(growth.ledger.record(growth_id, owner=growth.reader.owner(self.context(event))).title,
                         "revised growth")
        self.assertEqual(results[12]["proposal"]["proposal_id"], proposal_id)
        _, cancelled = await self.run_tools(remaining, [("diagnostics.proposal.cancel", {"proposal_id": proposal_id})],
            text="陪伴 答疑取消 " + proposal_id, identifier=3)
        self.assertEqual(cancelled[0]["proposal"]["state"], "cancelled")
        # The reminder that was set again is the remaining member's work; its record keeps its window.
        moved = self.app.agenda.repository.reminder(moved_id)
        self.assertEqual((moved.target.scope, moved.requester.principal.platform),
                         (removed.scope, self.context(event).scope.platform))
        # The endpoint that left reads none of it, although it made every record.
        refused = self.app.agenda.read_memo(removed.actor, target=self.target(removed), memo_id=memo_id,
                                            observed_at=self.clock.now())
        self.assertEqual(refused.failure and refused.failure.code, "scope_not_granted")
        from companion_v2.execution.models import TaskError
        with self.assertRaises(TaskError):
            self.app.tools.calendar.calendar.read(removed, "kept-plan")
        self.assertEqual(self.app.tools.growth.catalog_for(removed), ())
        return memo_id, growth_id

    async def test_records_made_by_the_joined_endpoint_stay_usable_after_it_is_removed(self):
        memo_id, growth_id = await self.records_of_a_member_removed_afterwards(made_on=1)
        # Also once the endpoint that made them has been admitted to a conversation of its own.
        self.assertEqual((await self.link(revision=2, members=["first"],
                                          independent_target_ids=["second"]))["status"], "ok")
        event, results = await self.run_tools(0, [("memo.read", {"memo_id": memo_id}),
            ("memo.edit", {"memo_id": memo_id, "expected_revision": 2, "changes": {"body": "edited once more"}}),
            ("reminder.list", {}), ("calendar.query", {}), ("role.growth.list", {})], identifier=4)
        self.assertEqual(results[1]["payload"]["revision"], 3)
        self.assertEqual(len(results[2]["payload"]), 2)
        self.assertEqual([item["id"] for item in results[3]["payload"]["items"]], ["kept-plan"])
        self.assertEqual([item["growth_id"] for item in results[4]["payload"]["items"]], [growth_id])

    async def test_records_made_by_the_endpoint_the_conversation_started_on_stay_usable_after_it_is_removed(self):
        await self.records_of_a_member_removed_afterwards(made_on=0)


class LinkedTextStoreOutsideTests(LinkedTextStoreFixture, unittest.IsolatedAsyncioTestCase):
    extra_targets = ({"target_id": "outside", "platform_id": "qq-main", "account_id": "100",
                      "bot_id": "bot-a", "participant_id": "999"},)

    async def test_an_authorized_endpoint_outside_the_link_cannot_read_or_edit_shared_records(self):
        _, saved = await self.run_tools(0, [("turn.evidence", {}),
            ("memo.create", {"title": "shared secret", "body": "only the linked conversation"}),
            ("reminder.create", {"message": "shared reminder", "schedule": self.calendar_data("role_plan", "")["schedule"]}),
            ("calendar.command", self.calendar_command("create", "shared-calendar", 0, self.calendar_data("user_report", "shared plan"))),
            ("role.growth.propose", lambda args: {"kind": "goal", "title": "shared growth",
                "evidence_handles": self.prior(args, 0)["evidence_handles"]})])
        self.assertEqual((await self.link())["status"], "ok")
        outside_origin = "qq-main:FriendMessage:999"
        await self.db.create_conversation(outside_origin, "qq-main", cid="outside-conversation", persona_id="original", content=[])
        await self.manager.switch_conversation(outside_origin, "outside-conversation")
        original_origins = self.origins
        event = self.event_for(0, "show my records", 2, sender="999")
        self.origins = (outside_origin, original_origins[1])
        try:
            event, results = await self.run_tools(0, [("memo.list", {}), ("reminder.list", {}),
                ("calendar.query", {}), ("role.growth.list", {}), ("role.timeline.query", {"kinds": ["growth"]})], event=event)
        finally:
            self.origins = original_origins
        self.assertEqual(results[0]["payload"], [])
        self.assertEqual(results[1]["payload"], [])
        for result in results[2:]:
            self.assertEqual(result["payload"]["items"], [])
        context = self.context(event)
        self.assertEqual(context.actor.subject.subject_id, "onebot-user:999")
        memo_id, reminder_id = [item["payload"]["object_id"] for item in saved[1:3]]
        for reader, parameters in ((self.app.agenda.read_memo, {"memo_id": memo_id}),
                                   (self.app.agenda.read_reminder, {"reminder_id": reminder_id})):
            self.assertFalse(reader(context.actor, target=self.target(context), observed_at=self.clock.now(), **parameters).is_success)
        from companion_v2.foundation.refs import OperationRef, VersionRef
        operation = OperationRef("outside-edit", "agenda", context.scope, VersionRef("outside-edit", 1), "outside-edit")
        denied = self.app.agenda.change_memo(context.actor, target=self.target(context), memo_id=memo_id, action="edit",
            changes={"body": "forbidden"}, expected_revision=1, expected_role_revision=context.role_binding.revision,
            operation=operation, observed_at=self.clock.now())
        self.assertFalse(denied.is_success)
        self.assertEqual(denied.failure.code, "agenda_object_not_found")
