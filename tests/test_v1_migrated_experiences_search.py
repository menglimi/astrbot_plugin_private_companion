"""End-to-end regression tests for searching V1-migrated shared experiences (#741)."""

from __future__ import annotations

import asyncio
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from astrbot_host.management_identity import dashboard_operator
from companion_v2.execution.models import digest, encode
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.identity_policy.personas import PersonaBinding, PersonaSelector, SelectorKind
from companion_v2.legacy.data_migration import build_data_plan, next_journal_batch
from companion_v2.memory.management import MemoryManagement
from companion_v2.memory.models import AtomRef, Proposal, owner_key
from companion_v2.memory.query import MemoryQuery
from companion_v2.memory.service import MemoryService
from companion_v2.role_work.journal.models import (
    DreamRecall,
    JournalContext,
    JournalDraft,
    JournalKey,
    JournalKind,
    JournalOrigin,
)
from companion_v2.role_work.journal.service import RoleJournalService
from companion_v2.role_work.ledger import PersistentRoleLifeLedger
from companion_v2.role_work.life.context import RoleLifeContextReader
from companion_v2.role_work.models import RoleActivity, RoleActivityState as State, RoleReadingReceipt
from companion_v2.role_work.work_models import RoleWorkSpec
from tests.memory_support import MemoryFixture
from tests.test_management_access import NOW


class _FakeWebRequest:
    def __init__(self, *, username: str = "dashboard-admin", payload: dict | None = None):
        self.username = username
        self.query = {}
        self._body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")

    async def body(self) -> bytes:
        return self._body


class V1MigratedExperiencesSearchTests(MemoryFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.service = MemoryService(self.tasks)
        self.journal = RoleJournalService(self.runtime, now=self.clock.now)
        self.life = PersistentRoleLifeLedger(self.runtime, now=self.clock.now)
        self.reader = RoleLifeContextReader(self.life, self.journal, self.tasks)
        self.producer = self.service.attach_life(self.reader)
        self.addCleanup(self.producer.close)
        self.role_owner = self.reader.owner(self.context)
        self.operator = dashboard_operator(self.runtime, "dashboard-admin", at=NOW)

    def _import_v1_journals(self, context, *, diary_count: int = 13, include_dream: bool = True) -> int:
        """Build a V1 data plan from synthetic legacy JSON and import journals as legacy_api does."""
        diaries = [
            {
                "date": f"2026-08-{day:02d}",
                "body": f"今天在海边散步并记录了第 {day} 篇日记。",
                "summary": f"海边散步第 {day} 天",
            }
            for day in range(1, diary_count + 1)
        ]
        payload = {"bot_diaries": diaries}
        if include_dream:
            payload["daily_dream"] = {
                "date": "2026-08-20",
                "content": "梦见一片安静的星空。",
                "label": "星空梦境",
                "afterglow": "醒来时还有一点余韵。",
            }
        with tempfile.TemporaryDirectory(prefix="companion-v1-legacy-") as directory:
            root = Path(directory)
            (root / "companions.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            plan = build_data_plan(root)
        batch = next_journal_batch(plan, {})
        for index, item in enumerate(batch, 1):
            kind = JournalKind.DIARY if item.kind == "diary" else JournalKind.DREAM
            key = JournalKey(self.role_owner, kind, item.local_date)
            journal_context = JournalContext(key, context.scope, "Asia/Shanghai")
            if kind is JournalKind.DIARY:
                draft = JournalDraft(
                    journal_context,
                    body=item.body,
                    summary=item.summary,
                    form=item.form,
                    origin=JournalOrigin.MANUAL,
                )
            else:
                recall = DreamRecall.FULL if item.body.strip() else DreamRecall.FRAGMENTS
                draft = JournalDraft(
                    journal_context,
                    body=item.body,
                    summary=item.summary,
                    afterglow=item.afterglow,
                    form=item.form,
                    recall=recall,
                    origin=JournalOrigin.MANUAL,
                )
            self.journal.save(
                draft,
                expected_revision=0,
                operation_id=f"legacy-v1-journal-{index}-{item.kind}-{item.local_date}",
                observed_at=NOW,
            )
        return len(batch)

    def _rotate_conversation_and_bump_old_scope(self, *, new_conversation_id: str = "conv-astrbot-430"):
        """Simulate AstrBot 4.30 first live chat rotating conversation_id and MultiAuthority.select()."""
        scope1 = self.context.scope
        scope2 = replace(scope1, conversation_id=new_conversation_id)
        resolved1 = self.personas.resolve(scope1, observed_at=NOW).unwrap()
        old_binding = self.personas.binding(resolved1.selected.binding_id)
        # MultiAuthority.select() bumps the departed scope's binding revision while keeping the same role.
        self.personas.set_binding(
            replace(old_binding, revision=old_binding.revision + 1),
            expected_revision=old_binding.revision,
            observed_at=NOW,
        )
        selector2 = PersonaSelector(
            scope2.installation_id,
            SelectorKind.CONVERSATION,
            bot_id=scope2.bot_id,
            platform=scope2.platform,
            account_id=scope2.account_id,
            conversation_kind=scope2.kind,
            conversation_id=scope2.conversation_id,
        )
        self.personas.set_binding(
            PersonaBinding(
                "rotated-conv-" + digest(new_conversation_id)[:12],
                selector2,
                old_binding.role,
                20,
                NOW,
                "test",
                "simulate first live chat conversation rotation",
            ),
            expected_revision=0,
            observed_at=NOW,
        )
        # Also advance actor.policy_revision by issuing a fresh grant on scope2.
        self.grant(self.important, scope2, "memory.read")
        self.grant(self.important, scope2, "memory.propose")
        self.grant(self.important, scope2, "role.life.read")
        fresh_actor = self.identity.resolve_authenticated(
            self.important.principal, observed_at=NOW
        ).unwrap()
        return self.context_for(fresh_actor, scope2)

    def _dashboard_query(self, context, intent: str, *, kind: str = "experiences"):
        management = MemoryManagement(self.service, self.operator)
        engine = MemoryQuery(management.writer)
        session_id = "memory-page-query-" + digest([context.key, intent, kind])[:16]
        engine.begin(context, session_id)
        return engine, session_id, engine.query(
            context, session_id, query_intent=intent, kind=kind
        ).unwrap()

    async def test_v1_migrated_journals_searchable_via_memory_web_api_after_conversation_rotation(self):
        imported = self._import_v1_journals(self.context, diary_count=13, include_dream=True)
        self.assertEqual(imported, 14)
        self.assertEqual(self.producer.advance(self.context).unwrap()["saved"], 14)

        # Simulate "核对 -> 导入 -> 私聊一次" rotating conversation_id and bumping old scope revision.
        rotated = self._rotate_conversation_and_bump_old_scope()

        management = MemoryManagement(self.service, self.operator)
        overview = management.overview(rotated).unwrap()
        self.assertEqual(overview["experiences"]["active"], 14)
        self.assertEqual(overview["experiences"]["total"], 14)

        # Verify through the real MemoryWebAPI.query route with lightweight web stubs.
        fake_req = _FakeWebRequest(payload={"query": "的", "kind": "experiences"})
        astrbot_mod = ModuleType("astrbot")
        astrbot_api_mod = ModuleType("astrbot.api")
        astrbot_web_mod = ModuleType("astrbot.api.web")
        astrbot_web_mod.request = fake_req
        astrbot_mod.api = astrbot_api_mod
        astrbot_api_mod.web = astrbot_web_mod

        fake_web_api_mod = ModuleType("astrbot_host.web_api")

        def _outcome_response(result):
            if result.failure is not None:
                return {
                    "status": "error",
                    "message": result.failure.code,
                    "reason": result.failure.reason,
                    "data": {"code": result.failure.code},
                }
            return {"status": "ok", "message": "", "data": result.unwrap()}

        fake_web_api_mod.outcome_response = _outcome_response

        fake_app = SimpleNamespace(
            memory=self.service,
            memory_reflection=SimpleNamespace(runtime=self.runtime),
            memory_schedule=SimpleNamespace(continuations=SimpleNamespace()),
        )
        fake_session = SimpleNamespace(
            application=fake_app,
            profile=SimpleNamespace(target=rotated),
        )
        fake_controller = SimpleNamespace(
            _terminated=False,
            _control_lock=asyncio.Lock(),
            session=fake_session,
            last_session=fake_session,
            runtime=self.runtime,
            now=self.clock.now,
        )

        with patch.dict(
            sys.modules,
            {
                "astrbot": astrbot_mod,
                "astrbot.api": astrbot_api_mod,
                "astrbot.api.web": astrbot_web_mod,
                "astrbot_host.web_api": fake_web_api_mod,
            },
        ):
            import astrbot_host.memory_api as memory_api_module

            with patch.object(memory_api_module, "request", fake_req):
                api = memory_api_module.MemoryWebAPI(fake_controller)
                response = await api.query()

        self.assertEqual(response["status"], "ok")
        data = response["data"]
        self.assertEqual(data["index_state"], "ready")
        self.assertTrue(data["complete"])
        self.assertFalse(data["unavailable_sources"])
        self.assertEqual(data["candidates_examined"], 14)
        self.assertEqual(len(data["items"]), 14)

        # Specific Chinese keywords and exact day tokens hit accurately.
        _, _, diary_hits = self._dashboard_query(rotated, "私人日记")
        self.assertEqual(len(diary_hits["items"]), 14)
        self.assertTrue(diary_hits["complete"])

        _, _, dream_hits = self._dashboard_query(rotated, "梦境")
        self.assertEqual(len(dream_hits["items"]), 1)
        self.assertEqual(dream_hits["items"][0]["title"], "梦境记录：2026-08-20")

        engine, session_id, day_hits = self._dashboard_query(rotated, "05")
        self.assertEqual(len(day_hits["items"]), 1)
        self.assertEqual(day_hits["items"][0]["title"], "私人日记：2026-08-05")
        handle = day_hits["items"][0]["statements"][0]["sources"][0]
        source_text = engine.source(rotated, session_id, handle=handle).unwrap()["text"]
        self.assertIn("2026-08-05", source_text)
        self.assertIn("私人日记", source_text)

        # Re-running advance() on the rotated context is idempotent with zero blocks or duplicates.
        second_advance = self.producer.advance(rotated).unwrap()
        self.assertEqual(second_advance["blocked"], [])
        self.assertEqual(management.overview(rotated).unwrap()["experiences"]["active"], 14)

    def test_rotation_before_ingestion_and_legacy_v1_skipped_cursor_still_ingest_and_search(self):
        imported = self._import_v1_journals(self.context, diary_count=13, include_dream=True)
        self.assertEqual(imported, 14)

        # First live chat rotates conversation_id BEFORE the 30s background ingestion tick runs.
        rotated = self._rotate_conversation_and_bump_old_scope(new_conversation_id="conv-before-tick")

        # Simulate an already-migrated v7.2.9 DB whose old v1 cursor skipped all 14 rows.
        old_v1_key = "memory.activity-ingestion.v1:" + digest([owner_key(rotated), rotated.scope])
        with self.runtime.transaction() as connection:
            connection.execute(
                "INSERT INTO runtime_metadata(key, value) VALUES (?, ?)",
                (old_v1_key, encode({"activity": 0, "journal": 14})),
            )

        report = self.producer.advance(rotated).unwrap()
        self.assertEqual(report["saved"], 14)
        self.assertEqual(report["blocked"], [])

        management = MemoryManagement(self.service, self.operator)
        self.assertEqual(management.overview(rotated).unwrap()["experiences"]["active"], 14)

        _, _, result = self._dashboard_query(rotated, "的")
        self.assertEqual(result["index_state"], "ready")
        self.assertTrue(result["complete"])
        self.assertFalse(result["unavailable_sources"])
        self.assertEqual(len(result["items"]), 14)

        # Follow-up tick on the same rotated window examines nothing new.
        self.assertEqual(self.producer.advance(rotated).unwrap()["examined"], 0)

    def test_missing_recall_changes_and_stale_v2_cache_self_heal_with_zero_hot_reads(self):
        self._import_v1_journals(self.context, diary_count=13, include_dream=True)
        self.assertEqual(self.producer.advance(self.context).unwrap()["saved"], 14)

        # Case A: active memory_views exist but memory_recall_changes has no experience notices.
        with self.runtime.transaction() as connection:
            connection.execute("DELETE FROM memory_recall_changes WHERE kind = 'experience'")

        _, _, healed = self._dashboard_query(self.context, "的")
        self.assertEqual(healed["index_state"], "ready")
        self.assertTrue(healed["complete"])
        self.assertEqual(healed["candidates_examined"], 14)
        self.assertEqual(len(healed["items"]), 14)

        # Subsequent hot query must not rescan memory_views or memory_recall_changes.
        management = MemoryManagement(self.service, self.operator)
        engine = MemoryQuery(management.writer)
        engine.begin(self.context, "memory-page-query-hot")
        original_read_all = self.runtime.read_all
        scanned = []

        def tracked_read_all(sql, args):
            if "memory_views" in sql or "memory_recall_changes" in sql:
                scanned.append(sql)
            return original_read_all(sql, args)

        with patch.object(self.runtime, "read_all", side_effect=tracked_read_all):
            hot = engine.query(
                self.context, "memory-page-query-hot", query_intent="的", kind="experiences"
            ).unwrap()
        self.assertEqual(len(hot["items"]), 14)
        self.assertEqual(scanned, [])

        # Case B: stale projection_version=2 sync row parked at current anchor with empty recall_documents.
        partition = digest([owner_key(self.context), "personal", "experiences", "incremental-recall.v1"])
        with closing(engine.index._open()) as db, db:
            db.execute("DELETE FROM recall_lexical")
            db.execute("DELETE FROM recall_documents WHERE owner = ?", (partition,))
            db.execute(
                "INSERT OR REPLACE INTO recall_sync VALUES (?, ?)",
                (
                    partition,
                    encode(
                        {
                            "generation": "stale-v2",
                            "after": 0,
                            "pending": 0,
                            "anchor": {"sequence": 0, "token": "empty"},
                            "unavailable": False,
                            "projection_version": 2,
                        }
                    ),
                ),
            )

        rebuilt = engine.query(
            self.context, "memory-page-query-hot", query_intent="梦境", kind="experiences"
        ).unwrap()
        self.assertEqual(rebuilt["index_state"], "ready")
        self.assertTrue(rebuilt["complete"])
        self.assertEqual(len(rebuilt["items"]), 1)
        self.assertEqual(rebuilt["items"][0]["title"], "梦境记录：2026-08-20")

    def test_security_boundaries_remain_enforced_across_revalidation_and_bootstrap(self):
        self._import_v1_journals(self.context, diary_count=2, include_dream=False)
        self.assertEqual(self.producer.advance(self.context).unwrap()["saved"], 2)

        # 1. Reassigning the historical scope to a different role denies previous reads.
        scope1 = self.context.scope
        resolved1 = self.personas.resolve(scope1, observed_at=NOW).unwrap()
        old_binding = self.personas.binding(resolved1.selected.binding_id)
        other_role = replace(old_binding.role, subject_id="different-role")
        self.personas.set_binding(
            replace(old_binding, role=other_role, revision=old_binding.revision + 1),
            expected_revision=old_binding.revision,
            observed_at=NOW,
        )
        views = self.runtime.read_all(
            "SELECT view_id FROM memory_views WHERE owner_key = ? AND kind = 'experience'",
            (owner_key(self.context),),
        )
        self.assertEqual(len(views), 2)
        rotated_ctx = self._rotate_conversation_and_bump_old_scope(new_conversation_id="conv-other-role")
        # Restore scope2 to the original role while scope1 remains bound to other_role.
        res2 = self.personas.resolve(rotated_ctx.scope, observed_at=NOW).unwrap()
        b2 = self.personas.binding(res2.selected.binding_id)
        self.personas.set_binding(
            replace(b2, role=old_binding.role, revision=b2.revision + 1),
            expected_revision=b2.revision,
            observed_at=NOW,
        )
        orig_role_ctx2 = self.context_for(
            self.identity.resolve_authenticated(self.important.principal, observed_at=NOW).unwrap(),
            rotated_ctx.scope,
        )
        self.assertFalse(
            self.service.experiences.read(orig_role_ctx2, views[0]["view_id"]).is_success
        )

        # Restore scope1 back to original role; verify permission revocation still denies reads.
        current_b1 = self.personas.binding(old_binding.binding_id)
        self.personas.set_binding(
            replace(current_b1, role=old_binding.role, revision=current_b1.revision + 1),
            expected_revision=current_b1.revision,
            observed_at=NOW,
        )
        self.assertTrue(
            self.service.experiences.read(orig_role_ctx2, views[0]["view_id"]).is_success
        )
        self.policy.set_tier(
            self.important.subject,
            AuthorizationTier.STANDARD,
            expected_revision=self.policy.revision,
            observed_at=NOW,
        )
        self.assertFalse(
            self.service.experiences.read(orig_role_ctx2, views[0]["view_id"]).is_success
        )

    def test_rebinding_account_to_another_human_denies_previous_experience_read(self):
        self._import_v1_journals(self.context, diary_count=1, include_dream=False)
        self.assertEqual(self.producer.advance(self.context).unwrap()["saved"], 1)
        view_id = self.runtime.read_one(
            "SELECT view_id FROM memory_views WHERE owner_key = ? AND kind = 'experience'",
            (owner_key(self.context),),
        )["view_id"]
        self.assertTrue(self.service.experiences.read(self.context, view_id).is_success)
        # Rebind the same principal to a different human subject.
        self.identity.bind(
            binding_id="rebound-to-core",
            principal=self.important.principal,
            subject=self.core.subject,
            valid_from=NOW,
            expected_revision=self.identity.revision,
        )
        # Stored receipt must fail validation because current_actor.subject != stored_context.actor.subject.
        self.assertFalse(self.service.experiences.read(self.context, view_id).is_success)

    def test_experience_bootstrap_resumes_in_bounded_pages_and_supports_live_private_recall(self):
        self._import_v1_journals(self.context, diary_count=13, include_dream=True)
        self.assertEqual(self.producer.advance(self.context).unwrap()["saved"], 14)
        rotated = self._rotate_conversation_and_bump_old_scope(new_conversation_id="conv-paged-live")

        with self.runtime.transaction() as connection:
            connection.execute("DELETE FROM memory_recall_changes WHERE kind = 'experience'")

        # Use the non-operator private chat MemoryQuery path with a small sync_items budget.
        self.service.query.recall_index.sync_items = 4
        self.service.query.begin(rotated, "live-paged-recall")
        page = self.service.query.query(
            rotated, "live-paged-recall", query_intent="的", kind="experiences"
        ).unwrap()
        self.assertEqual(page["index_state"], "catching_up")
        self.assertEqual(page["items"], [])
        self.assertFalse(page["complete"])

        pages = 1
        while page["next_cursor"]:
            page = self.service.query.query(
                rotated,
                "live-paged-recall",
                query_intent="的",
                kind="experiences",
                cursor=page["next_cursor"],
            ).unwrap()
            pages += 1
        self.assertGreaterEqual(pages, 4)
        self.assertEqual(page["index_state"], "ready")
        self.assertTrue(page["complete"])
        self.assertFalse(page["unavailable_sources"])
        self.assertEqual(len(page["items"]), 14)

    def test_forgotten_reading_experience_is_not_rehydrated_by_experience_bootstrap(self):
        event = self.event("夜航船")
        self.accept(event)
        record = self.service.writer.access.source(self.context, event.source)
        activity = self.life.add_activity(
            RoleActivity(
                "book-1",
                self.context.role_binding.role.subject_id,
                self.context.scope,
                NOW,
                State.ACTIVE,
                description="阅读夜航船",
                work=RoleWorkSpec(
                    "reading",
                    self.context.key,
                    "read-book-1",
                    source=event.source,
                    content_hash=record.content_hash,
                    start_offset=0,
                    end_offset=3,
                ),
            )
        )
        self.life.transition_activity(
            activity.activity_id,
            owner=activity.owner,
            expected_revision=activity.revision,
            state=State.COMPLETED,
            operation_id="complete-book-1",
            observed_at=NOW,
            reading=RoleReadingReceipt(
                event.source, record.content_hash, len(record.payload), 0, 3, NOW, "晚风很轻。"
            ),
        )
        evidence = self.service.writer.access.capture(self.context, event.source)
        fact = self.service.writer.submit(
            self.context,
            Proposal(
                "add",
                predicate="阅读材料",
                object="夜航船",
                representation="提到了夜航船",
                evidence=(evidence,),
            ),
            operation_key="remember-book-1",
        ).unwrap()
        self.producer.advance(self.context).unwrap()
        self.service.writer.forget(
            self.context,
            AtomRef(fact["atom_id"], fact["revision"]),
            evidence=(self.evidence("忘记那本书"),),
            operation_key="forget-book-1",
        ).unwrap()

        # Even if memory_recall_changes is empty and bootstrap scans memory_views, forgotten terms stay absent.
        with self.runtime.transaction() as connection:
            connection.execute("DELETE FROM memory_recall_changes WHERE kind = 'experience'")
        self.service.query.begin(self.context, "forget-check")
        result = self.service.query.query(
            self.context, "forget-check", query_intent="夜航船", kind="experiences"
        ).unwrap()
        self.assertEqual(result["items"], [])
        with closing(self.service.query.index._open()) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM recall_documents WHERE producer = 'experience'").fetchone()[0],
                0,
            )


if __name__ == "__main__":
    unittest.main()
