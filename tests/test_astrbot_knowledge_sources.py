from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

from astrbot_host.knowledge_sources import AstrBotKnowledgeSources


class FakeHelper:
    def __init__(self, docs):
        self.docs = docs
        self.init_error = None

    async def count_documents(self):
        return len(self.docs)

    async def list_documents(self, *, offset, limit):
        return self.docs[offset:offset + limit]


class FakeManager:
    def __init__(self, database, kb, helper):
        self.kb_db = NS(db_path=str(database))
        self.kb = kb
        self.helper = helper

    async def list_kbs(self):
        return [self.kb]

    async def get_kb(self, kb_id):
        return self.helper if kb_id == self.kb.kb_id else None


class AstrBotKnowledgeSourcesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="companion-official-kb-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "data"
        kb_root = self.root / "knowledge_base"
        kb_root.mkdir(parents=True)
        database = kb_root / "kb.db"
        connection = sqlite3.connect(database)
        connection.executescript(
            "CREATE TABLE knowledge_bases (kb_id TEXT, kb_name TEXT);"
            "CREATE TABLE kb_documents (kb_id TEXT, doc_id TEXT, doc_name TEXT);"
        )
        connection.executemany("INSERT INTO knowledge_bases VALUES (?, ?)", [("kb-1", "星河设定")])
        connection.executemany("INSERT INTO kb_documents VALUES (?, ?, ?)", [
            ("kb-1", "doc-1", "角色资料.md"), ("kb-1", "doc-2", "城市资料.md")])
        connection.commit()
        connection.close()

        doc_root = kb_root / "kb-1"
        doc_root.mkdir()
        chunks = [
            ("米拉喜欢雨天", {"kb_id": "kb-1", "kb_doc_id": "doc-1", "chunk_index": 0}),
            ("她会沿着河边散步", {"kb_id": "kb-1", "kb_doc_id": "doc-1", "chunk_index": 1}),
            ("旧城有一座钟楼", {"kb_id": "kb-1", "kb_doc_id": "doc-2", "chunk_index": 0}),
        ]
        connection = sqlite3.connect(doc_root / "doc.db")
        connection.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, text TEXT, metadata TEXT)")
        connection.executemany("INSERT INTO documents (text, metadata) VALUES (?, ?)",
                               [(text, json.dumps(meta, ensure_ascii=False)) for text, meta in chunks])
        connection.commit()
        connection.close()

        self.docs = [NS(doc_id="doc-1", doc_name="角色资料.md", file_type="md", chunk_count=2),
                     NS(doc_id="doc-2", doc_name="城市资料.md", file_type="md", chunk_count=1)]
        kb = NS(kb_id="kb-1", kb_name="星河设定", description="角色与城市背景", emoji="📚",
                doc_count=2, chunk_count=3)
        manager = FakeManager(database, kb, FakeHelper(self.docs))
        self.sources = AstrBotKnowledgeSources(NS(kb_manager=manager))

    async def test_lists_official_kbs_and_documents_with_v1_ids(self):
        sources = await self.sources.list_sources()

        self.assertEqual(sources[0]["id"], "kb:kb-1")
        self.assertEqual(sources[0]["name"], "星河设定")
        self.assertEqual([item["id"] for item in sources[0]["documents"]],
                         ["doc:kb-1:doc-1", "doc:kb-1:doc-2"])

    async def test_candidates_respect_document_selection_and_character_budget(self):
        candidates = self.sources.candidates(
            ["doc:kb-1:doc-2"], source_limit=4, entry_limit=8, entry_char_limit=100,
            char_budget=100)

        self.assertEqual([item.document_id for item in candidates], ["doc-2"])
        self.assertEqual(candidates[0].document_name, "城市资料.md")
        self.assertEqual(candidates[0].text, "旧城有一座钟楼")

        bounded = self.sources.candidates(
            ["kb:kb-1"], source_limit=4, entry_limit=1, entry_char_limit=5, char_budget=3)
        self.assertEqual(len(bounded), 1)
        self.assertEqual(bounded[0].text, "米拉喜")

    async def test_documents_remain_selectable_when_the_embedding_provider_failed(self):
        self.sources.manager.helper.init_error = "embedding provider unavailable"
        self.sources.manager.helper.list_documents = AsyncMock(side_effect=RuntimeError("helper unavailable"))
        database = self.sources.manager.kb_db
        database.count_documents_by_kb = AsyncMock(return_value=2)
        database.list_documents_by_kb = AsyncMock(return_value=self.docs)

        sources = await self.sources.list_sources()

        self.assertEqual([item["doc_id"] for item in sources[0]["documents"]], ["doc-1", "doc-2"])
        self.sources.manager.helper.list_documents.assert_not_awaited()

    async def test_documents_use_host_metadata_when_the_helper_is_missing(self):
        self.sources.manager.helper = None
        database = self.sources.manager.kb_db
        database.count_documents_by_kb = AsyncMock(return_value=2)
        database.list_documents_by_kb = AsyncMock(return_value=self.docs)

        sources = await self.sources.list_sources()

        self.assertEqual([item["doc_id"] for item in sources[0]["documents"]], ["doc-1", "doc-2"])
        database.list_documents_by_kb.assert_awaited_once_with("kb-1", offset=0, limit=2)

    async def test_a_removed_chunk_database_does_not_hide_other_sources(self):
        connection = sqlite3.connect(self.root / "knowledge_base" / "kb.db")
        connection.execute("INSERT INTO knowledge_bases VALUES (?, ?)", ("kb-2", "已移除的资料"))
        connection.commit()
        connection.close()

        candidates = self.sources.candidates(["kb:kb-2", "kb:kb-1"])

        self.assertEqual(len(candidates), 3)
        self.assertTrue(all(item.knowledge_base_id == "kb-1" for item in candidates))

    async def test_damaged_metadata_does_not_hide_valid_chunks(self):
        connection = sqlite3.connect(self.root / "knowledge_base" / "kb-1" / "doc.db")
        connection.execute("UPDATE documents SET metadata = ? WHERE id = 1", ("{",))
        connection.commit()
        connection.close()

        candidates = self.sources.candidates(["kb:kb-1"])
        selected_document = self.sources.candidates(["doc:kb-1:doc-1"])

        self.assertEqual([item.text for item in candidates], ["她会沿着河边散步", "旧城有一座钟楼"])
        self.assertEqual([item.text for item in selected_document], ["她会沿着河边散步"])

    def add_library(self, kb_id, kb_name, documents):
        """写入一份测试知识库：documents 按文档顺序给出 doc_id -> 段文本列表。"""
        connection = sqlite3.connect(self.root / "knowledge_base" / "kb.db")
        connection.execute("INSERT INTO knowledge_bases VALUES (?, ?)", (kb_id, kb_name))
        connection.executemany("INSERT INTO kb_documents VALUES (?, ?, ?)",
                               [(kb_id, doc_id, doc_id + ".md") for doc_id in documents])
        connection.commit()
        connection.close()
        doc_root = self.root / "knowledge_base" / kb_id
        doc_root.mkdir()
        connection = sqlite3.connect(doc_root / "doc.db")
        connection.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, text TEXT, metadata TEXT)")
        chunks = []
        for doc_id, texts in documents.items():
            for index, text in enumerate(texts):
                chunks.append((text, json.dumps({"kb_id": kb_id, "kb_doc_id": doc_id,
                                                 "chunk_index": index}, ensure_ascii=False)))
        connection.executemany("INSERT INTO documents (text, metadata) VALUES (?, ?)", chunks)
        connection.commit()
        connection.close()

    def test_a_whole_base_budget_smaller_than_the_library_still_covers_every_document(self):
        documents = {f"doc-{index:02d}": [f"doc-{index:02d}-{chunk}" for chunk in range(5)]
                     for index in range(1, 7)}
        self.add_library("kb-many", "多文档资料", documents)

        candidates = self.sources.candidates(["kb:kb-many"], entry_limit=6)

        self.assertEqual(len(candidates), 6)
        self.assertEqual([(item.document_id, item.chunk_index) for item in candidates],
                         [(doc_id, 0) for doc_id in documents])

    def test_a_whole_base_budget_covering_the_library_reads_every_chunk_in_order(self):
        self.add_library("kb-small", "小资料", {
            "doc-a": ["a0", "a1"], "doc-b": ["b0", "b1", "b2"], "doc-c": ["c0"]})

        first = self.sources.candidates(["kb:kb-small"], entry_limit=6)
        second = self.sources.candidates(["kb:kb-small"], entry_limit=6)

        # 先每篇第 1 段，再每篇第 2 段……层内按文档顺序，文档内按 chunk id 升序。
        self.assertEqual([item.text for item in first], ["a0", "b0", "c0", "a1", "b1", "b2"])
        self.assertEqual([item.text for item in first], [item.text for item in second])

    def test_a_whole_base_budget_is_not_eaten_by_one_dominant_document(self):
        documents = {"doc-01": [f"大文档第{index}段" for index in range(100)]}
        documents.update({f"doc-{index:02d}": [f"小文档第{index}段"] for index in range(2, 11)})
        self.add_library("kb-skewed", "长短不一", documents)

        candidates = self.sources.candidates(["kb:kb-skewed"], entry_limit=20)

        counts = {}
        for item in candidates:
            counts[item.document_id] = counts.get(item.document_id, 0) + 1
        expected = {"doc-01": 11}
        expected.update({f"doc-{index:02d}": 1 for index in range(2, 11)})
        self.assertEqual(len(candidates), 20)
        self.assertEqual(counts, expected)

    def test_a_single_document_selection_still_reads_only_that_document(self):
        self.add_library("kb-deep", "单篇资料", {"doc-01": ["单篇第0段", "单篇第1段", "单篇第2段", "单篇第3段"]})

        candidates = self.sources.candidates(["doc:kb-deep:doc-01"], entry_limit=3)

        self.assertEqual([item.text for item in candidates], ["单篇第0段", "单篇第1段", "单篇第2段"])
        self.assertTrue(all(item.document_id == "doc-01" for item in candidates))

    def test_mixed_whole_base_and_document_sources_still_share_the_budgets(self):
        self.add_library("kb-tiny", "微型资料", {"doc-a": ["a0", "a1"]})

        candidates = self.sources.candidates(["kb:kb-tiny", "doc:kb-1:doc-2"],
                                             entry_limit=3, char_budget=10)

        self.assertEqual(len(candidates), 3)
        self.assertEqual(sum(len(item.text) for item in candidates), 10)
        self.assertEqual([item.text for item in candidates], ["a0", "a1", "旧城有一座钟"])
        self.assertEqual(candidates[-1].knowledge_base_id, "kb-1")
        self.assertEqual(candidates[-1].document_id, "doc-2")


if __name__ == "__main__":
    unittest.main()
