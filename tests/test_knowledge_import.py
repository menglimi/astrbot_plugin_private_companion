"""T27: knowledge import lifecycle, scope isolation, bounded injection, member clues."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from companion_v2.configuration.models import ConfigurationError
from companion_v2.foundation.refs import AudienceRef, ScopeKind, ScopeRef, SubjectKind, SubjectRef
from companion_v2.knowledge.catalog import CatalogKnowledgeProvider, EntryDocument, KnowledgeCatalog, MemberEntry, SourceBinding
from companion_v2.knowledge.configuration import KnowledgeSettings, read_settings
from companion_v2.knowledge.context import (
    MEMBER_CLUE_INSTRUCTION,
    MEMBER_CLUE_PURPOSE,
    KnowledgeContext,
    knowledge_context,
)
from companion_v2.knowledge.importer import (
    ImportPlan,
    ImportStatus,
    KnowledgeFormat,
    KnowledgeImporter,
    KnowledgeSourceSpec,
)
from companion_v2.knowledge.members import DECLARED_ACCOUNT, MENTIONED_NAME, select_member_clues
from companion_v2.knowledge.retrieval import KnowledgeCandidate, KnowledgeRetriever, query_for_context
from companion_v2.sources.repositories import SourceRepository
from companion_v2.storage.runtime import StorageRuntime
from tests.test_conversation_messages import MessageFixture
from tests.test_management_access import INSTALLATION, NOW, SCOPE


WINDOW = NOW + timedelta(minutes=1)
ROLE_ID = "persona-a"
GROUP_A = ScopeRef(INSTALLATION, "onebot", ScopeKind.GROUP, "111", "bot-a", "login-a", "c1", "s1")
GROUP_B = ScopeRef(INSTALLATION, "onebot", ScopeKind.GROUP, "222", "bot-a", "login-a", "c2", "s2")


def worldbook(*entries):
    return {"entry_storage": list(entries)}


def entry(entry_id, content, **extra):
    return {"id": entry_id, "name": entry_id, "content": content, **extra}


class Value:
    """Duck-typed stand-in for the pieces of a task context these paths read."""

    def __init__(self, **fields):
        self.__dict__.update(fields)


def task_context(scope, *, account="important", role_id=ROLE_ID):
    subject = SubjectRef(INSTALLATION, SubjectKind.HUMAN, account)
    return Value(
        scope=scope,
        actor=Value(subject=subject, principal=Value(native_account_id=account)),
        role_binding=Value(role=SubjectRef(INSTALLATION, SubjectKind.BOT, role_id)),
        audience=AudienceRef(scope, (subject,)),
    )


class Fixture(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="companion-v2-knowledge-")
        self.addCleanup(temporary.cleanup)
        self.runtime = StorageRuntime.open(data_root=Path(temporary.name) / "data",
                                           installation_id=INSTALLATION)
        self.addCleanup(self.runtime.close)
        self.catalog = KnowledgeCatalog(self.runtime)
        self.sources = SourceRepository(self.runtime)
        self.binding = SourceBinding(INSTALLATION)

    def importer(self, *, entry_limit=50, content_chars=400):
        return KnowledgeImporter(self.catalog, entry_limit=entry_limit, content_chars=content_chars)

    def spec(self, source_id="worldbook:test", binding=None):
        return KnowledgeSourceSpec(source_id, KnowledgeFormat.WORLD_BOOK, source_id,
                                   binding or self.binding)

    def provider(self, *, source_limit=8, char_budget=10000):
        return CatalogKnowledgeProvider(self.catalog, installation_id=INSTALLATION,
                                        source_limit=source_limit, char_budget=char_budget)


class KnowledgeImportTests(Fixture):
    def test_import_updates_and_invalidation_are_versioned_and_idempotent(self):
        importer, spec = self.importer(), self.spec()
        original = worldbook(entry("rain", "米拉喜欢在雨天散步", keywords=["雨天"]))

        first = importer.import_source(spec, lambda item: original, at=NOW)
        again = importer.import_source(spec, lambda item: original, at=WINDOW)
        changed = importer.import_source(
            spec, lambda item: worldbook(entry("rain", "米拉现在更喜欢晴天", keywords=["雨天"])),
            at=WINDOW)

        self.assertEqual((first.status, first.revision, first.entries), (ImportStatus.IMPORTED, 1, 1))
        self.assertEqual((again.status, again.revision), (ImportStatus.UNCHANGED, 1))
        self.assertEqual((changed.status, changed.revision), (ImportStatus.IMPORTED, 2))
        self.assertEqual([item.content for item in self.catalog.get(self.binding, spec.source_id).entries],
                         ["米拉现在更喜欢晴天"])

        removed = importer.import_source(spec, lambda item: None, at=WINDOW)

        self.assertEqual(removed.status, ImportStatus.MISSING)
        self.assertFalse(self.catalog.get(self.binding, spec.source_id).active)
        self.assertEqual(self.provider().snapshot(), ())

    def test_bad_source_keeps_the_last_good_revision_and_never_raises(self):
        importer, spec = self.importer(), self.spec()
        importer.import_source(spec, lambda item: worldbook(entry("rain", "雨天散步", keywords=["雨天"])),
                               at=NOW)

        def unreadable_reader(item):
            raise OSError("file vanished mid-read")

        def mixed_reader(item):
            if item.source_id == "worldbook:good":
                return worldbook(entry("sun", "晴天散步", keywords=["晴天"]))
            raise OSError("broken")

        unreadable = importer.import_source(spec, unreadable_reader, at=WINDOW)
        unparsable = importer.import_source(spec, lambda item: {"nope": []}, at=WINDOW)
        empty = importer.import_source(spec, lambda item: worldbook(), at=WINDOW)

        self.assertEqual([item.status for item in (unreadable, unparsable, empty)],
                         [ImportStatus.INVALID] * 3)
        self.assertEqual([item.revision for item in (unreadable, unparsable, empty)], [1, 1, 1])
        self.assertEqual([item.candidate_id for item in self.provider().snapshot()], ["rain"])

        good = self.spec("worldbook:good")
        results = importer.import_all(ImportPlan((spec, good), mixed_reader), at=WINDOW)

        self.assertEqual([(item.source_id, item.status) for item in results],
                         [("worldbook:test", ImportStatus.INVALID),
                          ("worldbook:good", ImportStatus.IMPORTED)])

    def test_damaged_catalogue_row_is_skipped_instead_of_breaking_the_turn(self):
        self.importer().import_source(self.spec(), lambda item: worldbook(entry("rain", "雨天散步")),
                                      at=NOW)
        owner = self.catalog.list(INSTALLATION, limit=1)[0].owner_key
        with self.runtime.transaction() as connection:
            connection.execute(
                "INSERT INTO memory_views VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("knowledge-source-damaged", owner, "knowledge.source.v1", 1, "active", "{not json",
                 "{}", "2026-09-21T00:00:00+00:00"))

        self.assertEqual([item.candidate_id for item in self.provider().snapshot()], ["rain"])

    def test_knowledge_base_rows_import_as_attributed_chunks(self):
        spec = KnowledgeSourceSpec("doc:kb-1:doc-1", KnowledgeFormat.KNOWLEDGE_ROWS, "doc:kb-1:doc-1",
                                   self.binding)
        rows = [
            {"text": "世界观设定一", "metadata": {"kb_doc_id": "doc-1", "doc_name": "设定",
                                                 "chunk_index": 0}},
            {"text": "世界观设定二", "metadata": {"kb_doc_id": "doc-1", "doc_name": "设定",
                                                 "chunk_index": 1}},
            {"text": ""},
        ]

        result = self.importer().import_source(spec, lambda item: rows, at=NOW)

        self.assertEqual((result.status, result.entries, len(result.issues)),
                         (ImportStatus.IMPORTED, 2, 1))
        self.assertEqual([(item.knowledge_base_id, item.document_id, item.document_name,
                           item.chunk_index) for item in self.provider().snapshot()],
                         [("kb-1", "doc-1", "设定", 0), ("kb-1", "doc-1", "设定", 1)])

    def test_selected_official_sources_are_published_into_the_conversation(self):
        observed = []

        def official_reader(source_ids, **bounds):
            observed.append((tuple(source_ids), bounds))
            return (KnowledgeCandidate("astrbot:kb-1:doc-1:0", "米拉喜欢雨天散步",
                knowledge_base_id="kb-1", document_id="doc-1", document_name="角色资料.md",
                chunk_index=0),)

        settings = KnowledgeSettings.from_values({"source_ids": "kb:kb-1"})
        context = KnowledgeContext(KnowledgeRetriever(()), runtime=self.runtime,
            settings_reader=lambda _: settings, official_candidate_reader=official_reader)
        task = task_context(SCOPE)
        audience = task.audience

        contributions = context.contributions(task, text="米拉的雨天习惯", at=NOW, batch_id="official-kb",
                                              sources=self.sources, audience=audience)

        self.assertEqual(observed[0][0], ("kb:kb-1",))
        self.assertEqual(observed[0][1]["entry_char_limit"], settings.entry_char_limit)
        self.assertEqual(len(contributions), 1)
        self.assertEqual(contributions[0].payload, "米拉喜欢雨天散步")
        self.assertIsNotNone(contributions[0].source)
        self.assertTrue(self.sources.validate_reference(contributions[0].source, observed_at=NOW).is_success)

    def test_core_reply_retrieves_official_kb_only_when_the_opt_in_is_on(self):
        observed = []

        def official_reader(source_ids, **bounds):
            observed.append(tuple(source_ids))
            return (KnowledgeCandidate("astrbot:kb-1:doc-1:0", "米拉喜欢雨天散步",
                knowledge_base_id="kb-1", document_id="doc-1", document_name="角色资料.md",
                chunk_index=0),)

        task = task_context(SCOPE)
        audience = task.audience

        def build(values):
            settings = KnowledgeSettings.from_values(values)
            return KnowledgeContext(KnowledgeRetriever(()), runtime=self.runtime,
                settings_reader=lambda _: settings, official_candidate_reader=official_reader)

        off = build({"source_ids": "kb:kb-1"}).core_background_contributions(
            task, text="米拉的雨天习惯", at=NOW, batch_id="core-off",
            sources=self.sources, audience=audience)
        self.assertEqual(off, ())
        self.assertEqual(observed, [])

        on = build({"source_ids": "kb:kb-1", "official_in_core": True}).core_background_contributions(
            task, text="米拉的雨天习惯", at=NOW, batch_id="core-on",
            sources=self.sources, audience=audience)
        self.assertEqual(observed, [("kb:kb-1",)])
        self.assertEqual([item.payload for item in on], ["米拉喜欢雨天散步"])

    def test_semantic_official_retriever_preserves_order_and_falls_back_when_unavailable(self):
        scan_called = []
        semantic_calls = []

        def official_reader(source_ids, **bounds):
            scan_called.append(tuple(source_ids))
            return (KnowledgeCandidate("astrbot:kb-1:doc-1:0", "降级字面命中内容",
                knowledge_base_id="kb-1", document_id="doc-1", document_name="角色资料.md",
                chunk_index=0),)

        def semantic_retriever(query_text, source_ids, **bounds):
            semantic_calls.append((query_text, tuple(source_ids)))
            # Zero literal character overlap with query_text; must still be kept in rank order.
            return (
                KnowledgeCandidate("astrbot:kb-1:doc-9:2", "维也纳雪夜音乐会终章",
                    knowledge_base_id="kb-1", document_id="doc-9", document_name="终章.md",
                    chunk_index=2),
                KnowledgeCandidate("astrbot:kb-1:doc-3:1", "空港离别的约定",
                    knowledge_base_id="kb-1", document_id="doc-3", document_name="序章.md",
                    chunk_index=1),
            )

        task = task_context(SCOPE)
        settings = KnowledgeSettings.from_values({"source_ids": "kb:kb-1", "official_in_core": True})
        ctx = KnowledgeContext(
            KnowledgeRetriever(()), runtime=self.runtime,
            settings_reader=lambda _: settings,
            official_candidate_reader=official_reader,
            official_semantic_retriever=semantic_retriever,
        )
        hits = ctx.core_background_contributions(
            task, text="钢琴家后来去了哪", at=NOW, batch_id="semantic-ok",
            sources=self.sources, audience=task.audience)
        self.assertEqual([item.payload for item in hits], ["维也纳雪夜音乐会终章", "空港离别的约定"])
        self.assertEqual(semantic_calls, [("钢琴家后来去了哪", ("kb:kb-1",))])
        self.assertEqual(scan_called, [])

        # When semantic retriever returns None (kb_manager unavailable), fall back to official_reader.
        fallback_ctx = KnowledgeContext(
            KnowledgeRetriever(()), runtime=self.runtime,
            settings_reader=lambda _: settings,
            official_candidate_reader=official_reader,
            official_semantic_retriever=lambda *_a, **_kw: None,
        )
        fallback_hits = fallback_ctx.core_background_contributions(
            task, text="降级字面", at=NOW, batch_id="semantic-fallback",
            sources=self.sources, audience=task.audience)
        self.assertEqual([item.payload for item in fallback_hits], ["降级字面命中内容"])
        self.assertEqual(scan_called, [("kb:kb-1",)])

    def test_role_and_group_boundaries_isolate_imported_entries(self):
        importer = self.importer()
        importer.import_source(self.spec("worldbook:shared"), lambda item: worldbook(
            entry("plain", "安装级背景资料", keywords=["资料"]),
            entry("member", "只作称呼线索", template="user", scope=["12345"]),
            entry("group-a", "一群资料", template="group", scope=["111"], keywords=["资料"]),
            entry("group-b", "二群资料", template="group", scope=["222"], keywords=["资料"]),
        ), at=NOW)
        importer.import_source(self.spec("worldbook:role", binding=SourceBinding(
            INSTALLATION, role_id=ROLE_ID)), lambda item: worldbook(
            entry("role", "米拉的专属资料", keywords=["资料"])), at=NOW)
        retriever = KnowledgeRetriever(self.provider())

        def hits(scope, role_id=ROLE_ID):
            return sorted(hit.candidate.candidate_id for hit in retriever.select(
                query_for_context("资料", scope=scope, role_id=role_id, persona_id=role_id,
                                  max_hits=8, max_chars=6000)).hits)

        self.assertEqual(hits(GROUP_A), ["group-a", "plain", "role"])
        self.assertEqual(hits(GROUP_B), ["group-b", "plain", "role"])
        self.assertEqual(hits(SCOPE), ["plain", "role"])
        self.assertEqual(hits(GROUP_A, role_id="other-role"), ["group-a", "plain"])
        self.assertNotIn("member", hits(GROUP_A))
        self.assertEqual([item.entry_id for item in self.catalog.member_entries(
            installation_id=INSTALLATION, source_limit=8)], ["member"])

    def test_removing_a_configured_source_retires_only_its_own_binding(self):
        importer = self.importer()
        first = SourceBinding(INSTALLATION, role_id="mira")
        second = SourceBinding(INSTALLATION, role_id="alex")
        keep = KnowledgeSourceSpec("worldbook:keep", KnowledgeFormat.WORLD_BOOK, "keep", first)
        drop = KnowledgeSourceSpec("worldbook:drop", KnowledgeFormat.WORLD_BOOK, "drop", second)
        replacement = KnowledgeSourceSpec("worldbook:new", KnowledgeFormat.WORLD_BOOK, "new", second)
        reader = lambda item: worldbook(entry("only", f"{item.source_id} 资料"))

        importer.import_all(ImportPlan((keep, drop), reader), at=NOW)
        results = importer.import_all(ImportPlan((keep, replacement), reader), at=WINDOW)

        self.assertIn(("worldbook:drop", ImportStatus.MISSING),
                      [(item.source_id, item.status) for item in results])
        self.assertTrue(self.catalog.get(first, "worldbook:keep").active)
        self.assertFalse(self.catalog.get(second, "worldbook:drop").active)
        self.assertTrue(self.catalog.get(second, "worldbook:new").active)

    def test_source_cleanup_preserves_explicitly_imported_v1_worldbook(self):
        source_id = "legacy-v1-worldbook-entries-primary"
        self.catalog.save(self.binding, source_id=source_id, locator="v1-persona:primary",
            format="legacy_worldbook", content_digest="v1-worldbook-digest",
            entries=(EntryDocument("legacy-entry", "从 V1 导入的资料"),), members=(),
            issues=(), at=NOW, code="legacy_v1_imported")
        importer = self.importer()

        results = importer.import_all(ImportPlan((self.spec(),),
            lambda item: worldbook(entry("current", "当前资料"))), at=WINDOW)

        self.assertNotIn(source_id, {item.source_id for item in results})
        self.assertTrue(self.catalog.get(self.binding, source_id).active)


class MemberClueTests(Fixture):
    def import_members(self, *entries):
        self.importer().import_source(self.spec(), lambda item: worldbook(*entries), at=NOW)
        return self.catalog.member_entries(installation_id=INSTALLATION, source_limit=8)

    def test_member_scope_kind_and_individual_group_boundary_are_enforced(self):
        group_binding = SourceBinding(INSTALLATION, scope_kind=ScopeKind.GROUP)
        group_member = MemberEntry("all-groups", content="只能在群里参考的描述")
        local_member = MemberEntry("group-a", content="一群的成员描述", group_ids=("111",))
        for binding, member in ((group_binding, group_member), (self.binding, local_member)):
            self.catalog.save(binding, source_id=member.entry_id, locator="synthetic", format="worldbook",
                              content_digest=member.entry_id, entries=(), members=(member,), issues=(), at=NOW)

        def members(scope):
            return {entry.entry_id for entry in self.catalog.member_entries(
                installation_id=INSTALLATION, source_limit=8, role_id=ROLE_ID,
                scope_ids=(scope.native_scope_id,), scope_kind=scope.kind, scope=scope)}

        self.assertEqual(members(GROUP_A), {"all-groups", "group-a"})
        self.assertEqual(members(GROUP_B), {"all-groups"})
        self.assertEqual(members(SCOPE), set())

    def test_declared_account_matches_only_the_authenticated_speaker(self):
        entries = self.import_members(
            entry("alan", "爱喝冰美式，说话慢", name="阿岚", template="user", scope=["12345"],
                  aliases=["岚岚"], gender="男"))

        authenticated = select_member_clues(entries, text="晚上好", native_account_id="12345", limit=4)
        stranger = select_member_clues(entries, text="晚上好", native_account_id="99999", limit=4)
        prompt = authenticated.prompt(char_limit=1200)

        self.assertEqual([clue.basis for clue in authenticated.clues], [DECLARED_ACCOUNT])
        self.assertTrue(authenticated.clues[0].authenticated_account)
        self.assertIn("由运行时认证", prompt)
        self.assertIn("条目内容来自导入资料", prompt)
        self.assertTrue(stranger.empty)

    def test_unresolved_label_stays_undecided_and_writes_no_profile(self):
        entries = self.import_members(
            entry("alan", "爱喝冰美式", name="阿岚", template="user", scope=["12345"], aliases=["岚岚"]),
            entry("other", "另一个人", name="小岚", template="user", scope=["99999"], aliases=["岚岚"]))
        before = self.runtime.read_all("SELECT view_id, revision FROM memory_views", ())

        mentioned = select_member_clues(entries, text="岚岚今天在吗", limit=4)
        unique = select_member_clues(entries, text="阿岚今天在吗", limit=4)

        self.assertEqual(mentioned.clues, ())
        self.assertIn("岚岚", mentioned.ambiguous)
        self.assertIn("称呼无法唯一确定", mentioned.prompt(char_limit=1200))
        self.assertEqual([clue.basis for clue in unique.clues], [MENTIONED_NAME])
        self.assertIn("未认证", unique.prompt(char_limit=1200))
        self.assertEqual(self.runtime.read_all("SELECT view_id, revision FROM memory_views", ()), before)
        self.assertEqual(self.runtime.read_all(
            "SELECT view_id FROM memory_views WHERE kind LIKE 'person%'", ()), ())
        self.assertIn("不是身份认证", MEMBER_CLUE_INSTRUCTION)

    def test_ambiguous_account_and_clue_budget_stay_bounded(self):
        entries = self.import_members(
            entry("one", "第一份资料", name="甲", template="user", scope=["12345"]),
            entry("two", "第二份资料", name="乙", template="user", scope=["12345"]),
            entry("long", "很长的资料" * 40, name="丙", template="user", scope=["777"]))

        duplicated = select_member_clues(entries, text="", native_account_id="12345", limit=4)
        bounded = select_member_clues(entries, text="", native_account_id="777", limit=4)

        self.assertEqual(duplicated.clues, ())
        self.assertEqual(len(bounded.clues), 1)
        self.assertLessEqual(len(bounded.prompt(char_limit=400)), 400)
        self.assertLessEqual(len(bounded.prompt(char_limit=120)), 120)


class KnowledgeInjectionTests(Fixture):
    def knowledge(self, settings=None):
        reader = (lambda context: settings) if settings is not None else None
        return KnowledgeContext(KnowledgeRetriever(self.provider()), catalog=self.catalog,
                                runtime=self.runtime, settings_reader=reader)

    def test_injected_material_is_bounded_and_carries_a_governed_source(self):
        entries = [entry(f"e{index}", f"雨天散步资料第{index}段" + "长" * 300, keywords=["雨天"])
                   for index in range(6)]
        entries.append(entry("member", "只作称呼线索", name="阿岚", template="user",
                             scope=["important"], aliases=["岚岚"]))
        imported = self.importer().import_source(self.spec(), lambda item: worldbook(*entries), at=NOW)
        self.assertEqual((imported.status, imported.entries, imported.member_entries),
                         (ImportStatus.IMPORTED, 6, 1))
        settings = KnowledgeSettings.from_values({
            "entry_char_limit": 400, "inject_max_entries": 4, "inject_max_chars": 500,
            "member_clue_chars": 400, "member_recognition_enabled": True})
        context = task_context(SCOPE)

        materials = self.knowledge(settings).contributions(
            context, text="雨天 资料", at=NOW, batch_id="batch-1", sources=self.sources,
            audience=context.audience)

        background = [item for item in materials if item.purpose.startswith("knowledge_reference:")]
        clues = [item for item in materials if item.purpose == MEMBER_CLUE_PURPOSE]
        # One full entry fits the five-hundred character budget; a second does not.
        self.assertEqual(len(background), 1)
        self.assertLessEqual(sum(len(item.payload) for item in background), 500)
        self.assertTrue(all(item.owner == "knowledge.v2" and item.source is not None
                            for item in materials))
        self.assertTrue(all(self.sources.validate_reference(item.source, observed_at=NOW).is_success
                            for item in materials))
        self.assertEqual(len(clues), 1)
        self.assertLessEqual(len(clues[0].payload), 400)
        self.assertIn("条目内容来自导入资料", clues[0].payload)

    def test_injection_can_be_switched_off_without_touching_the_catalogue(self):
        self.importer().import_source(self.spec(), lambda item: worldbook(
            entry("rain", "雨天散步资料", keywords=["雨天"])), at=NOW)
        context = task_context(SCOPE)

        off = self.knowledge(KnowledgeSettings.from_values({"inject_enabled": False}))
        on = self.knowledge()

        self.assertEqual(off.contributions(context, text="雨天", at=NOW, batch_id="b1",
                                           sources=self.sources, audience=context.audience), ())
        self.assertEqual(len(on.contributions(context, text="雨天", at=NOW, batch_id="b2",
                                              sources=self.sources, audience=context.audience)), 1)
        self.assertTrue(self.catalog.get(self.binding, "worldbook:test").active)

    def test_registered_defaults_read_back_and_cross_field_rules_are_enforced(self):
        settings = read_settings(self.runtime, task_context(SCOPE))

        self.assertFalse(settings.import_enabled)
        self.assertFalse(settings.member_recognition_enabled)
        self.assertTrue(settings.inject_enabled)
        self.assertEqual((settings.inject_max_entries, settings.inject_max_chars), (8, 6000))
        self.assertEqual(settings.paths(), ())
        self.assertEqual(KnowledgeSettings.from_values({"import_paths": "a.json,b.json\nc.json"}).paths(),
                         ("a.json", "b.json", "c.json"))
        with self.assertRaises(ConfigurationError):
            KnowledgeSettings.from_values({"entry_char_limit": 1200, "inject_max_chars": 500})


class KnowledgeMessageInjectionTests(MessageFixture, unittest.IsolatedAsyncioTestCase):
    async def test_sealed_turn_injects_imported_material_and_member_clues_with_sources(self):
        catalog = KnowledgeCatalog(self.runtime)
        KnowledgeImporter(catalog, entry_limit=20, content_chars=400).import_source(
            KnowledgeSourceSpec("worldbook:live", KnowledgeFormat.WORLD_BOOK, "live",
                                SourceBinding(INSTALLATION)),
            lambda item: worldbook(
                entry("rain", "米拉喜欢在雨天散步", keywords=["private"]),
                entry("speaker", "爱喝冰美式", name="重要的人", template="user",
                      scope=["important"], aliases=["小重"])),
            at=NOW)
        self.messages.knowledge = knowledge_context(
            self.runtime,
            settings_reader=lambda context: KnowledgeSettings.from_values(
                {"member_recognition_enabled": True}))

        await self.generate(batch=self.accept())
        request = self.called[0][1].original

        knowledge = [item for item in request.context.materials if item.owner == "knowledge.v2"]
        self.assertEqual(sorted(item.purpose for item in knowledge),
                         ["knowledge_member_clue", "knowledge_reference:rain"])
        self.assertTrue(all(item.source is not None for item in knowledge))
        self.assertIn("米拉喜欢在雨天散步",
                      "\n".join(item.payload for item in request.context.materials))
        self.assertIn("角色知识资料只是受控的背景参考", request.instruction)
        self.assertIn("不是身份认证", request.instruction)
        self.assertTrue(catalog.get(SourceBinding(INSTALLATION), "worldbook:live").active)


if __name__ == "__main__":
    unittest.main()
