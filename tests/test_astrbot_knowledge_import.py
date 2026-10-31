"""Start-up knowledge import: configured world-book material reaches the catalogue."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from astrbot_host.knowledge_sources import (
    import_configured_sources_at_startup,
    import_plan,
)
from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.knowledge.catalog import KnowledgeCatalog, SourceBinding
from companion_v2.knowledge.configuration import (
    KnowledgeConfigurationOwner,
    configuration_fields,
    read_installation_settings,
)
from companion_v2.knowledge.importer import ImportStatus
from companion_v2.storage.runtime import StorageRuntime
from tests.multi_host_support import MultiFixture
from tests.test_management_access import INSTALLATION, NOW


def worldbook(*entries):
    return {"entry_storage": list(entries)}


def entry(entry_id, content, **extra):
    return {"id": entry_id, "name": entry_id, "content": content, **extra}


class StartupKnowledgeImportTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="companion-startup-import-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        # Two roots on purpose: the catalogue lives in the plugin data root while
        # knowledge.import_paths resolve against the AstrBot data root.
        self.data_root = root / "astrbot-data"
        self.data_root.mkdir(parents=True)
        self.runtime = StorageRuntime.open(data_root=root / "plugin-data",
                                           installation_id=INSTALLATION)
        self.addCleanup(self.runtime.close)
        self.catalog = KnowledgeCatalog(self.runtime)

    # -- helpers -------------------------------------------------------------

    def configure(self, changes):
        owner = KnowledgeConfigurationOwner(self.runtime)
        target = ConfigTarget(INSTALLATION, ConfigLayer.INSTALLATION)
        fields = configuration_fields()
        owner.write(target, fields, changes=changes, reset=(),
                    expected_revision=owner.read(target, fields).revision, observed_at=NOW)

    def worldbook_file(self, *entries, relative="config/worldbook.json"):
        path = self.data_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(worldbook(*entries), ensure_ascii=False), encoding="utf-8")
        return path

    def knowledge_base(self):
        root = self.data_root / "knowledge_base"
        root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(root / "kb.db")
        connection.executescript(
            "CREATE TABLE knowledge_bases (kb_id TEXT, kb_name TEXT);"
            "CREATE TABLE kb_documents (kb_id TEXT, doc_id TEXT, doc_name TEXT);")
        connection.executemany("INSERT INTO knowledge_bases VALUES (?, ?)", [("kb-1", "角色资料")])
        connection.executemany("INSERT INTO kb_documents VALUES (?, ?, ?)",
                               [("kb-1", "doc-1", "设定.md")])
        connection.commit()
        connection.close()
        documents = root / "kb-1"
        documents.mkdir()
        connection = sqlite3.connect(documents / "doc.db")
        connection.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, text TEXT, metadata TEXT)")
        connection.execute("INSERT INTO documents (text, metadata) VALUES (?, ?)",
                           ("米拉喜欢雨天", json.dumps({"kb_doc_id": "doc-1", "chunk_index": 0},
                                                      ensure_ascii=False)))
        connection.commit()
        connection.close()

    def import_at_startup(self):
        return import_configured_sources_at_startup(self.runtime, None, data_root=self.data_root,
                                                    at=NOW)

    def versions(self):
        return self.catalog.list(INSTALLATION, limit=16)

    def candidates(self):
        return self.catalog.background_candidates(installation_id=INSTALLATION, source_limit=16,
                                                  char_budget=60000)

    # -- configured material -------------------------------------------------

    def test_configured_worldbook_reaches_the_catalogue_and_member_clues(self):
        self.worldbook_file(entry("rain", "雨天适合沿河散步", keywords=["雨天"]),
                            entry("member-200", "阿岚偏好安静的聊天", template="user", scope=["200"]))
        self.configure({"knowledge.import_enabled": True,
                        "knowledge.import_paths": "config/worldbook.json"})

        results = self.import_at_startup()

        self.assertEqual([item.source_id for item in results],
                         ["worldbook:" + str(self.data_root / "config" / "worldbook.json")])
        self.assertEqual((results[0].status, results[0].entries, results[0].member_entries),
                         (ImportStatus.IMPORTED, 1, 1))
        self.assertEqual([item.candidate_id for item in self.candidates()], ["rain"])
        self.assertEqual([item.entry_id for item in self.catalog.member_entries(
            installation_id=INSTALLATION, source_limit=16)], ["member-200"])

    def test_repeated_start_import_keeps_one_revision(self):
        self.worldbook_file(entry("rain", "雨天适合沿河散步"))
        self.configure({"knowledge.import_enabled": True,
                        "knowledge.import_paths": "config/worldbook.json"})
        self.assertEqual(self.import_at_startup()[0].status, ImportStatus.IMPORTED)
        revision = self.versions()[0].revision

        repeated = self.import_at_startup()

        self.assertEqual(repeated[0].status, ImportStatus.UNCHANGED)
        self.assertEqual([version.revision for version in self.versions()], [revision])

    # -- missing or invalid origins -----------------------------------------

    def test_absent_default_worldbooks_start_without_material(self):
        self.configure({"knowledge.import_enabled": True})

        with self.assertLogs("astrbot_host.knowledge_sources", level="INFO") as captured:
            results = self.import_at_startup()

        self.assertEqual([item.status for item in results], [ImportStatus.MISSING] * 2)
        self.assertEqual(self.versions(), ())
        self.assertIn("启动知识导入完成", "\n".join(captured.output))
        self.assertIn("reason=source_unknown", "\n".join(captured.output))

    def test_configured_paths_that_are_not_files_are_skipped(self):
        directory = self.data_root / "config" / "not-a-file"
        directory.mkdir(parents=True)
        self.configure({"knowledge.import_enabled": True,
                        "knowledge.import_paths": f"{directory}\n{self.data_root / 'absent.json'}"})

        results = self.import_at_startup()

        self.assertEqual([item.status for item in results], [ImportStatus.MISSING] * 2)
        self.assertEqual(self.versions(), ())

    def test_broken_worldbook_keeps_the_previous_revision(self):
        path = self.worldbook_file(entry("rain", "雨天适合沿河散步"))
        self.configure({"knowledge.import_enabled": True,
                        "knowledge.import_paths": "config/worldbook.json"})
        self.assertEqual(self.import_at_startup()[0].status, ImportStatus.IMPORTED)

        path.write_text("{", encoding="utf-8")
        unreadable = self.import_at_startup()[0]
        path.write_text(json.dumps({"nope": []}), encoding="utf-8")
        unparsable = self.import_at_startup()[0]

        self.assertEqual((unreadable.status, unreadable.code),
                         (ImportStatus.INVALID, "source_unreadable"))
        self.assertEqual((unparsable.status, unparsable.code),
                         (ImportStatus.INVALID, "source_unparsable"))
        self.assertEqual([item.candidate_id for item in self.candidates()], ["rain"])
        self.assertEqual([version.revision for version in self.versions()], [1])

    # -- switches and failures -----------------------------------------------

    def test_switched_off_import_reads_nothing(self):
        self.worldbook_file(entry("rain", "雨天适合沿河散步"))

        with self.assertLogs("astrbot_host.knowledge_sources", level="INFO") as captured:
            results = self.import_at_startup()

        self.assertEqual(results, ())
        self.assertEqual(self.versions(), ())
        self.assertIn("已关闭", "\n".join(captured.output))

    def test_selected_official_knowledge_is_read_live_instead_of_copied(self):
        self.knowledge_base()
        self.configure({"knowledge.import_enabled": True, "knowledge.source_ids": "kb:kb-1"})

        results = self.import_at_startup()

        self.assertEqual([item.status for item in results], [ImportStatus.MISSING] * 2)
        self.assertEqual(self.versions(), ())
        settings = read_installation_settings(self.runtime, INSTALLATION)
        full = import_plan(self.data_root, SourceBinding(INSTALLATION), settings)
        self.assertIn("kb:kb-1", [spec.source_id for spec in full.specs])

    def test_a_storage_failure_does_not_break_the_start(self):
        self.configure({"knowledge.import_enabled": True})

        with patch("astrbot_host.knowledge_sources.import_configured_sources",
                   side_effect=RuntimeError("storage down")):
            with self.assertLogs("astrbot_host.knowledge_sources", level="WARNING") as captured:
                results = self.import_at_startup()

        self.assertEqual(results, ())
        self.assertIn("启动知识导入失败", "\n".join(captured.output))


class StartupImportWiringTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def test_host_start_refreshes_configured_knowledge_once(self):
        with patch("astrbot_host.knowledge_sources"
                   ".import_configured_sources_at_startup") as refresh:
            application = await self.start_host()

        refresh.assert_called_once()
        runtime, host_context = refresh.call_args.args
        self.assertIs(runtime, application.runtime)
        self.assertIs(host_context, self.controller.context)
        self.assertIn("at", refresh.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
