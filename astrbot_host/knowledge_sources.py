"""Host-side origin readers for the V2 knowledge import contract.

The core package may not touch a filesystem (the V2 isolation scan rejects
unapproved file and SQLite access inside ``companion_v2``), so the two real
origins live here:

* an AstrBot worldbook export, a JSON file with an ``entry_storage`` list, the
  same shape V1 read from ``config/plugin_upload_astrbot_plugin_worldbook_config.json``;
* an AstrBot knowledge base under ``<data root>/knowledge_base`` (``kb.db`` for the
  catalogue and ``<kb_id>/doc.db`` for the chunk rows).

Only explicitly supplied or configured origins are read.  No V1 companion data
root is touched and the AstrBot data root must be passed in by the caller; there
is no implicit global lookup in this module.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import json
import logging
from pathlib import Path
import re
import sqlite3

from companion_v2.storage.sqlite_path import readonly_uri

from companion_v2.knowledge.catalog import KnowledgeCatalog, SourceBinding
from companion_v2.knowledge.configuration import read_installation_settings
from companion_v2.knowledge.importer import (
    ImportPlan,
    ImportResult,
    ImportStatus,
    KnowledgeFormat,
    KnowledgeImporter,
    KnowledgeSourceSpec,
)
from companion_v2.knowledge.parsing import parse_knowledge_rows
from companion_v2.knowledge.retrieval import KnowledgeCandidate


logger = logging.getLogger(__name__)

WORLDBOOK_DEFAULTS = (
    "config/plugin_upload_astrbot_plugin_worldbook_config.json",
    "config/astrbot_plugin_worldbook_config.json",
)
KNOWLEDGE_BASE_DIR = "knowledge_base"
SOURCE_PREFIXES = ("kb:", "doc:")
_CONTEXT_BLOCK_RE = re.compile(r"<companion_v2_context>.*?</companion_v2_context>", re.DOTALL)
_PLACEHOLDER_TOKEN_RE = re.compile(
    r"(?:<提及自己>|\[提及机器人\]|<attachment>|\[图片\]|\[语音\]|\[音频\]|\[视频\]|\[文件\]"
    r"|\[Image unavailable\]|\[Voice unavailable\])"
)


def clean_retrieval_query(text, *, max_chars: int = 800) -> str:
    """Extract clean user query text; reject JSON material packs and bare placeholders."""
    if not isinstance(text, str):
        return ""
    stripped = _CONTEXT_BLOCK_RE.sub(" ", text).strip()
    if not stripped:
        return ""
    if stripped[0] in "{[":
        try:
            decoded = json.loads(stripped)
        except ValueError:
            decoded = None
        if isinstance(decoded, (dict, list)):
            return ""
    cleaned = " ".join(_PLACEHOLDER_TOKEN_RE.sub(" ", stripped).split())
    if not cleaned:
        return ""
    return cleaned[:max(1, int(max_chars))]


def _parse_source_ids(source_ids, source_limit: int):
    identifiers = []
    for value in source_ids or ():
        identifier = str(value or "").strip()
        if identifier.startswith(SOURCE_PREFIXES) and identifier not in identifiers:
            identifiers.append(identifier)
        if len(identifiers) >= max(1, int(source_limit)):
            break
    selected_kbs = {item[3:] for item in identifiers if item.startswith("kb:")}
    selected_docs = {}
    for item in identifiers:
        if item.startswith("doc:"):
            parts = item.split(":", 2)
            if len(parts) == 3 and parts[1] not in selected_kbs:
                selected_docs.setdefault(parts[1], set()).add(parts[2])
    ordered_kbs = list(dict.fromkeys(
        [item[3:] for item in identifiers if item.startswith("kb:")]
        + [item.split(":", 2)[1] for item in identifiers if item.startswith("doc:") and len(item.split(":", 2)) == 3]
    ))
    return identifiers, selected_kbs, selected_docs, ordered_kbs


def worldbook_paths(data_root, settings):
    """Configured worldbook files, then the two conventional AstrBot locations."""
    data_root = Path(data_root)
    resolved = []
    configured = settings.paths()
    for part in configured:
        path = Path(part)
        candidate = path if path.is_absolute() else data_root / path
        if candidate not in resolved:
            resolved.append(candidate)
    if not configured:
        for relative in WORLDBOOK_DEFAULTS:
            candidate = data_root / relative
            if candidate not in resolved:
                resolved.append(candidate)
    return tuple(resolved)


def knowledge_base_ids(data_root, settings):
    """Configured ``kb:``/``doc:`` ids that still exist in this installation."""
    available = available_source_ids(data_root)
    return tuple(item for item in settings.knowledge_source_ids()
                 if item.startswith(SOURCE_PREFIXES) and item in available)


def available_source_ids(data_root) -> frozenset[str]:
    """Every knowledge base and document id the local AstrBot installation has."""
    root = Path(data_root) / KNOWLEDGE_BASE_DIR
    database = root / "kb.db"
    if not database.is_file():
        return frozenset()
    identifiers = set()
    connection = _connect(database)
    try:
        for row in connection.execute("select kb_id from knowledge_bases"):
            kb_id = str(row["kb_id"] or "").strip()
            if kb_id:
                identifiers.add("kb:" + kb_id)
        for row in connection.execute("select kb_id, doc_id from kb_documents"):
            kb_id, doc_id = str(row["kb_id"] or "").strip(), str(row["doc_id"] or "").strip()
            if kb_id and doc_id:
                identifiers.add(f"doc:{kb_id}:{doc_id}")
    finally:
        connection.close()
    return frozenset(identifiers)


def import_plan(data_root, binding: SourceBinding, settings, *, include_knowledge_rows=True) -> ImportPlan | None:
    """The configured import plan, or None when importing is switched off.

    ``include_knowledge_rows`` is off for the start-up refresh: an AstrBot
    knowledge base selected in ``knowledge.source_ids`` already reaches a turn
    through :class:`AstrBotKnowledgeSources` (and, on the native reply chain,
    through AstrBot itself), so storing the same chunks in the catalogue would
    hand one document to the same prompt twice.
    """
    if not settings.import_enabled:
        return None
    specs = []
    for path in worldbook_paths(data_root, settings):
        specs.append(KnowledgeSourceSpec(f"worldbook:{path}", KnowledgeFormat.WORLD_BOOK,
                                         str(path), binding, enabled=True))
    if include_knowledge_rows:
        for identifier in knowledge_base_ids(data_root, settings):
            specs.append(KnowledgeSourceSpec(identifier, KnowledgeFormat.KNOWLEDGE_ROWS, identifier,
                                             binding, enabled=True))
    return ImportPlan(tuple(specs), KnowledgeSourceReader(data_root))


class KnowledgeSourceReader:
    """Read one configured origin; None means the origin is gone."""

    def __init__(self, data_root) -> None:
        self.data_root = Path(data_root)

    def __call__(self, spec: KnowledgeSourceSpec):
        if spec.format is KnowledgeFormat.WORLD_BOOK:
            return self._worldbook(Path(spec.locator))
        return self._knowledge_rows(spec.locator)

    @staticmethod
    def _worldbook(path: Path):
        if not path.is_file():
            return None
        # A decoded failure is reported to the importer, which keeps the
        # previous good revision instead of emptying the catalogue.
        return json.loads(path.read_text(encoding="utf-8-sig"))

    def _knowledge_rows(self, identifier: str, *, limit=None):
        parts = identifier.split(":")
        kb_id = parts[1] if len(parts) > 1 else ""
        doc_id = parts[2] if len(parts) > 2 else None
        database = self.data_root / KNOWLEDGE_BASE_DIR / kb_id / "doc.db"
        if not kb_id or not database.is_file():
            return None
        if doc_id is None and limit is not None:
            return self._knowledge_rows_by_document(kb_id, database, limit)
        rows = []
        connection = _connect(database)
        try:
            for row in connection.execute("select text, metadata from documents order by id asc"):
                metadata = row["metadata"] or "{}"
                if doc_id is not None:
                    try:
                        decoded = json.loads(metadata) if isinstance(metadata, str) else metadata
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(decoded, dict) or str(decoded.get("kb_doc_id") or "") != doc_id:
                        continue
                rows.append({"text": row["text"], "metadata": metadata})
                if limit is not None and len(rows) >= limit:
                    break
        finally:
            connection.close()
        return rows

    def _knowledge_rows_by_document(self, kb_id: str, database: Path, limit: int):
        """整库引用：在预算内按文档公平分配，而不是只读库头的前 ``limit`` 段。

        先只扫一遍 ``(id, metadata)`` 记下每篇文档的段数并排出文档顺序，再用
        该段数模拟轮次配额（每轮翻倍）算出每篇实际该取几段，最后第二遍扫描
        只保留配额内的行。整库 chunk 不会全部读进内存，也不会每轮重扫一次库。

        返回按“第几段”分层交错排列：先取每篇第 1 段，再取每篇第 2 段，依此类
        推，层内按文档顺序、文档内按 chunk id 升序。这样调用方按字符预算截断
        时，库里的每篇文档都能先拿到代表段，而不是被前几篇大文档占满。
        """
        counts = {}
        first_seen = []
        connection = _connect(database)
        try:
            for row in connection.execute("select id, metadata from documents order by id asc"):
                key = _document_key(row["metadata"])
                if key is None:
                    continue
                if key not in counts:
                    counts[key] = 0
                    first_seen.append(key)
                counts[key] += 1
        finally:
            connection.close()
        order, known = [], set()
        for item in self._document_order(kb_id):
            if item in counts and item not in known:
                known.add(item)
                order.append(item)
        for item in first_seen:
            if item not in known:
                known.add(item)
                order.append(item)
        if not order:
            return []
        quotas = _fair_quotas(counts, order, limit)
        rows = {key: [] for key in order}
        connection = _connect(database)
        try:
            for row in connection.execute("select text, metadata from documents order by id asc"):
                key = _document_key(row["metadata"])
                if key is None or quotas.get(key, 0) <= 0:
                    continue
                quotas[key] -= 1
                rows[key].append({"text": row["text"], "metadata": row["metadata"]})
        finally:
            connection.close()
        depth = max(len(item) for item in rows.values())
        return [rows[key][level] for level in range(depth) for key in order if level < len(rows[key])]

    def _document_order(self, kb_id: str):
        """kb.db 里该库的文档顺序；读不到时返回空，由调用方回退为首次出现顺序。"""
        database = self.data_root / KNOWLEDGE_BASE_DIR / "kb.db"
        if not database.is_file():
            return ()
        order, seen = [], set()
        try:
            connection = _connect(database)
        except (OSError, sqlite3.Error):
            return ()
        try:
            for row in connection.execute(
                    "select doc_id from kb_documents where kb_id = ? order by rowid", (kb_id,)):
                doc_id = str(row["doc_id"] or "").strip()
                if doc_id and doc_id not in seen:
                    seen.add(doc_id)
                    order.append(doc_id)
        except (OSError, sqlite3.Error):
            return ()
        finally:
            connection.close()
        return tuple(order)


class AstrBotKnowledgeSources:
    """Read and retrieve official AstrBot knowledge bases selected in Companion V2."""

    def __init__(self, host_context, *, loop=None) -> None:
        self.manager = getattr(host_context, "kb_manager", None)
        database = getattr(getattr(self.manager, "kb_db", None), "db_path", None)
        self.data_root = Path(database).parent.parent if database else None
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
        self._loop = loop

    async def _resolve_kb_names(self, ordered_kbs):
        id_to_name = {}
        unavailable_ids = set()
        get_kb = getattr(self.manager, "get_kb", None)
        if callable(get_kb):
            for kb_id in ordered_kbs:
                try:
                    helper = await get_kb(kb_id)
                except Exception:
                    unavailable_ids.add(kb_id)
                    continue
                if helper is None:
                    continue
                if getattr(helper, "init_error", None):
                    unavailable_ids.add(kb_id)
                    continue
                kb_obj = getattr(helper, "kb", None)
                kb_name = str(getattr(kb_obj, "kb_name", "") or "").strip()
                if kb_name:
                    id_to_name[kb_id] = kb_name
        missing = [kb_id for kb_id in ordered_kbs if kb_id not in id_to_name and kb_id not in unavailable_ids]
        list_kbs = getattr(self.manager, "list_kbs", None)
        if missing and callable(list_kbs):
            try:
                listed = await list_kbs()
            except Exception:
                listed = ()
            for kb in listed or ():
                kb_id = str(getattr(kb, "kb_id", "") or "").strip()
                kb_name = str(getattr(kb, "kb_name", "") or "").strip()
                if kb_id in missing and kb_name:
                    id_to_name[kb_id] = kb_name
        missing = [kb_id for kb_id in ordered_kbs if kb_id not in id_to_name and kb_id not in unavailable_ids]
        if missing and self.data_root is not None:
            try:
                kb_names, _ = self._names(missing)
                for kb_id, kb_name in kb_names.items():
                    if kb_name:
                        id_to_name[kb_id] = str(kb_name).strip()
            except (OSError, sqlite3.Error):
                pass
        name_to_id = {name: kb_id for kb_id, name in id_to_name.items()}
        return id_to_name, name_to_id, len(unavailable_ids)

    async def retrieve(self, query, source_ids, *, source_limit=16, max_hits=8,
                       entry_char_limit=1200, char_budget=6000, fusion_top_k=None):
        """Delegate semantic+BM25+RRF+Rerank lookup to AstrBot's ``kb_manager``.

        Returns ``None`` when ``kb_manager`` is unavailable or fails, signaling
        the caller to fall back to the bounded SQLite candidate reader.
        Returns ``()`` when the query is empty/placeholder/JSON or yields no
        hits without falling back to lexical sampling.
        """
        retriever = getattr(self.manager, "retrieve", None)
        if self.manager is None or not callable(retriever):
            return None
        identifiers, selected_kbs, selected_docs, ordered_kbs = _parse_source_ids(source_ids, source_limit)
        if not identifiers or max_hits < 1 or entry_char_limit < 1 or char_budget < 1:
            return ()
        cleaned_query = clean_retrieval_query(query)
        if not cleaned_query:
            return ()
        id_to_name, name_to_id, unavailable = await self._resolve_kb_names(ordered_kbs)
        kb_names = [id_to_name[kb_id] for kb_id in ordered_kbs if kb_id in id_to_name]
        if not kb_names:
            return None if unavailable else ()
        has_doc_filter = bool(selected_docs)
        fetch_final = min(max(int(max_hits) * 3, 20), 60) if has_doc_filter else max(1, int(max_hits))
        top_k_fusion = max(fetch_final, int(fusion_top_k) if fusion_top_k else max(20, fetch_final * 3))
        try:
            payload = await retriever(
                query=cleaned_query,
                kb_names=kb_names,
                top_k_fusion=top_k_fusion,
                top_m_final=fetch_final,
            )
        except Exception as error:
            logger.warning("[CompanionV2] 官方知识库语义检索降级 error_type=%s", type(error).__name__)
            return None
        if not isinstance(payload, Mapping):
            return ()
        items = payload.get("results")
        if not isinstance(items, (list, tuple)) or not items:
            return ()
        candidates = []
        seen_text = set()
        used_chars = 0
        for index, item in enumerate(items):
            if len(candidates) >= int(max_hits):
                break
            if not isinstance(item, Mapping):
                continue
            kb_id = str(item.get("kb_id") or "").strip()
            kb_name = str(item.get("kb_name") or "").strip()
            if not kb_id and kb_name:
                kb_id = name_to_id.get(kb_name, "")
            if not kb_id:
                continue
            doc_id = str(item.get("doc_id") or "").strip()
            if kb_id not in selected_kbs:
                allowed_docs = selected_docs.get(kb_id)
                if not allowed_docs or doc_id not in allowed_docs:
                    continue
            content = str(item.get("content") or "").strip()[:int(entry_char_limit)]
            if not content:
                continue
            remaining = int(char_budget) - used_chars
            if remaining <= 0:
                break
            content = content[:remaining].strip()
            if not content:
                break
            normalized = " ".join(content.casefold().split())
            if normalized in seen_text:
                continue
            chunk_index = item.get("chunk_index", index)
            try:
                chunk_index = max(0, int(chunk_index))
            except (TypeError, ValueError):
                chunk_index = index
            doc_name = str(item.get("doc_name") or "").strip() or doc_id or "资料"
            candidates.append(KnowledgeCandidate(
                f"astrbot:{kb_id}:{doc_id}:{chunk_index}",
                content,
                knowledge_base_id=kb_id,
                document_id=doc_id,
                document_name=doc_name,
                chunk_index=chunk_index,
                name=kb_name or id_to_name.get(kb_id, kb_id),
            ))
            seen_text.add(normalized)
            used_chars += len(content)
        return tuple(candidates)

    def retrieve_sync(self, query, source_ids, *, timeout: float = 10.0, **kwargs):
        """Bridge worker-thread domain calls onto the host asyncio loop."""
        if self.manager is None or not callable(getattr(self.manager, "retrieve", None)):
            return None
        loop = self._loop
        if loop is None or not loop.is_running():
            return None
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            return None
        future = asyncio.run_coroutine_threadsafe(self.retrieve(query, source_ids, **kwargs), loop)
        try:
            return future.result(timeout=timeout)
        except Exception as error:
            future.cancel()
            logger.warning("[CompanionV2] 官方知识库同步桥接检索降级 error_type=%s", type(error).__name__)
            return None

    async def list_sources(self):
        """List official knowledge bases and documents through the host manager."""
        if self.manager is None or not callable(getattr(self.manager, "list_kbs", None)):
            return []
        result = []
        for kb in await self.manager.list_kbs():
            kb_id = str(getattr(kb, "kb_id", "") or "").strip()
            if not kb_id:
                continue
            documents = []
            try:
                helper = await self.manager.get_kb(kb_id)
                if helper is not None and not getattr(helper, "init_error", None):
                    count = await helper.count_documents()
                    docs = await helper.list_documents(offset=0, limit=min(max(count, 1), 5000))
                else:
                    database = self.manager.kb_db
                    count = await database.count_documents_by_kb(kb_id)
                    docs = await database.list_documents_by_kb(kb_id, offset=0, limit=min(max(count, 1), 5000))
                documents = [
                    {"id": f"doc:{kb_id}:{doc.doc_id}", "doc_id": str(doc.doc_id),
                     "name": str(doc.doc_name or doc.doc_id), "file_type": str(doc.file_type or ""),
                     "chunk_count": int(doc.chunk_count or 0)}
                    for doc in docs if str(getattr(doc, "doc_id", "") or "").strip()
                ]
            except Exception:
                documents = []
            result.append({
                "id": f"kb:{kb_id}", "type": "knowledge_base", "kb_id": kb_id,
                "name": str(getattr(kb, "kb_name", "") or kb_id),
                "description": str(getattr(kb, "description", "") or "")[:500],
                "emoji": str(getattr(kb, "emoji", "") or "")[:8],
                "doc_count": int(getattr(kb, "doc_count", 0) or len(documents)),
                "chunk_count": int(getattr(kb, "chunk_count", 0) or 0),
                "documents": documents,
            })
        return result

    def candidates(self, source_ids, *, source_limit=16, entry_limit=500,
                   entry_char_limit=1200, char_budget=60000):
        """Return bounded chunks for the selected official sources, without importing them."""
        if self.data_root is None:
            return ()
        identifiers, selected_kbs, selected_docs, ordered_kbs = _parse_source_ids(source_ids, source_limit)
        if not identifiers or entry_limit < 1 or entry_char_limit < 1 or char_budget < 1:
            return ()

        try:
            available = available_source_ids(self.data_root)
            names, document_names = self._names(ordered_kbs)
        except (OSError, sqlite3.Error):
            return ()
        reader = KnowledgeSourceReader(self.data_root)
        candidates = []
        used_chars = 0
        remaining_entries = int(entry_limit)
        for kb_id in ordered_kbs:
            if not kb_id or f"kb:{kb_id}" not in available:
                continue
            doc_filter = None if kb_id in selected_kbs else selected_docs.get(kb_id, set())
            if doc_filter is not None and not doc_filter:
                continue
            source_ids_for_kb = [f"kb:{kb_id}"] if doc_filter is None else [
                f"doc:{kb_id}:{doc_id}" for doc_id in sorted(doc_filter)
                if f"doc:{kb_id}:{doc_id}" in available
            ]
            for source_id in source_ids_for_kb:
                try:
                    rows = reader._knowledge_rows(source_id, limit=remaining_entries)
                except (OSError, sqlite3.Error):
                    continue
                for chunk in parse_knowledge_rows(rows or (), knowledge_base_id=kb_id).items:
                    content = chunk.text[:entry_char_limit]
                    if not content:
                        continue
                    remaining = char_budget - used_chars
                    if remaining <= 0:
                        return tuple(candidates)
                    content = content[:remaining]
                    if not content:
                        return tuple(candidates)
                    document_name = chunk.document_name or document_names.get(
                        (kb_id, chunk.document_id), chunk.document_id or "资料")
                    candidates.append(KnowledgeCandidate(
                        f"astrbot:{kb_id}:{chunk.document_id}:{chunk.chunk_index}", content,
                        knowledge_base_id=kb_id, document_id=chunk.document_id,
                        document_name=document_name, chunk_index=chunk.chunk_index,
                        name=names.get(kb_id, kb_id),
                    ))
                    used_chars += len(content)
                    remaining_entries -= 1
                    if remaining_entries <= 0:
                        return tuple(candidates)
        return tuple(candidates)

    def _names(self, kb_ids):
        if not kb_ids:
            return {}, {}
        database = self.data_root / KNOWLEDGE_BASE_DIR / "kb.db"
        connection = _connect(database)
        try:
            placeholders = ",".join("?" for _ in kb_ids)
            bases = connection.execute(
                f"select kb_id, kb_name from knowledge_bases where kb_id in ({placeholders})", kb_ids).fetchall()
            docs = connection.execute(
                f"select kb_id, doc_id, doc_name from kb_documents where kb_id in ({placeholders})", kb_ids).fetchall()
            kb_names = {row["kb_id"]: row["kb_name"] for row in bases}
            doc_names = {(row["kb_id"], row["doc_id"]): row["doc_name"] for row in docs}
            return kb_names, doc_names
        finally:
            connection.close()


def attach_official_knowledge(application, host_context):
    """Inject the host-side reader and semantic retriever into the knowledge collaborator."""
    sources = AstrBotKnowledgeSources(host_context)
    messages = getattr(application, "messages", None)
    knowledge = getattr(messages, "knowledge", None)
    if knowledge is not None and sources.manager is not None:
        knowledge.official_candidate_reader = sources.candidates
        knowledge.official_semantic_retriever = sources.retrieve_sync
        knowledge.official_sources = sources
    return sources


async def prepare_official_knowledge_hits(application, context, text, *, include_official=None):
    """Pre-fetch official KB hits asynchronously on the event loop before sync prompt building."""
    messages = getattr(application, "messages", None)
    owner = getattr(messages, "knowledge", None)
    if owner is None:
        return None
    try:
        settings = owner.settings(context)
    except Exception:
        return None
    if not settings.inject_enabled or not settings.source_ids:
        return None
    if include_official is None:
        include_official = settings.official_in_core
    if not include_official:
        return None
    sources = getattr(owner, "official_sources", None)
    retriever = getattr(sources, "retrieve", None)
    if not callable(retriever):
        return None
    try:
        return await retriever(
            text,
            settings.knowledge_source_ids(),
            source_limit=settings.source_limit,
            max_hits=settings.inject_max_entries,
            entry_char_limit=settings.entry_char_limit,
            char_budget=settings.inject_max_chars,
        )
    except Exception as error:
        logger.warning("[CompanionV2] 官方知识库异步预检索降级 error_type=%s", type(error).__name__)
        return None


def import_configured_sources(runtime, *, data_root, binding: SourceBinding, at, settings=None,
                              include_knowledge_rows=True):
    """One start-up or on-demand refresh; returns per-source results, never raises.

    The host calls this from its own lifecycle or a management command; nothing
    here runs inside a conversation turn.
    """
    if at is None:
        raise ValueError("import requires an explicit observation time")
    settings = settings if settings is not None else read_installation_settings(
        runtime, binding.installation_id)
    plan = import_plan(data_root, binding, settings, include_knowledge_rows=include_knowledge_rows)
    if plan is None:
        return ()
    importer = KnowledgeImporter(KnowledgeCatalog(runtime), entry_limit=settings.entry_limit,
                                 content_chars=settings.entry_char_limit)
    try:
        return importer.import_all(plan, at=at)
    except Exception:
        # Import is additive background work: a storage problem must not take the
        # host lifecycle down, and the previous revisions stay readable.
        return tuple(ImportResult(spec.source_id, ImportStatus.INVALID, code="import_unavailable")
                     for spec in plan.specs)


def _astrbot_data_root(host_context=None):
    """The AstrBot data root that ``knowledge.import_paths`` resolve against."""
    root = AstrBotKnowledgeSources(host_context).data_root
    if root is not None:
        return Path(root)
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path
        return Path(get_astrbot_data_path())
    except Exception:
        return None


def _log_import_results(results):
    """One count line plus one line per skipped source; never any content."""
    if not results:
        logger.info("[CompanionV2] 启动知识导入没有可读来源（未配置或路径全部缺失）")
        return
    counts = {status: 0 for status in ImportStatus}
    entries = members = 0
    for item in results:
        counts[item.status] = counts[item.status] + 1
        if item.status is ImportStatus.IMPORTED:
            entries, members = entries + item.entries, members + item.member_entries
    logger.info(
        "[CompanionV2] 启动知识导入完成 来源=%d 导入=%d 未变=%d 缺失=%d 无效=%d 条目=%d 成员=%d",
        len(results), counts[ImportStatus.IMPORTED], counts[ImportStatus.UNCHANGED],
        counts[ImportStatus.MISSING], counts[ImportStatus.INVALID], entries, members)
    for item in results:
        if item.ok:
            continue
        reason = item.code or item.status.value
        if item.status is ImportStatus.INVALID:
            logger.warning("[CompanionV2] 启动知识导入跳过 source=%s reason=%s", item.source_id, reason)
        else:
            logger.info("[CompanionV2] 启动知识导入跳过 source=%s reason=%s", item.source_id, reason)


def import_configured_sources_at_startup(runtime, host_context=None, *, data_root=None, at):
    """Refresh the configured world-book material once while the host starts.

    Imports are additive background work, so nothing here raises or delays a
    start: a missing file, an unparsable one or a storage problem only reaches
    the log, and the previous revisions stay readable.  Repeating it is harmless
    because an unchanged source keeps its stored revision.

    Only world-book origins are refreshed; the AstrBot knowledge bases selected
    in ``knowledge.source_ids`` are read live by :class:`AstrBotKnowledgeSources`
    and must not also be copied into the catalogue.
    """
    try:
        settings = read_installation_settings(runtime, runtime.installation_id)
    except Exception as error:
        logger.warning("[CompanionV2] 启动知识导入读取设置失败 error_type=%s", type(error).__name__)
        return ()
    if not settings.import_enabled:
        logger.info("[CompanionV2] 启动知识导入已关闭 knowledge.import_enabled=false")
        return ()
    if at is None:
        logger.warning("[CompanionV2] 启动知识导入缺少观测时间，已跳过")
        return ()
    root = Path(data_root) if data_root is not None else _astrbot_data_root(host_context)
    if root is None:
        logger.warning("[CompanionV2] 启动知识导入找不到 AstrBot 数据根目录，已跳过")
        return ()
    try:
        results = import_configured_sources(
            runtime, data_root=root, binding=SourceBinding(runtime.installation_id), at=at,
            settings=settings, include_knowledge_rows=False)
    except Exception as error:
        logger.warning("[CompanionV2] 启动知识导入失败 error_type=%s", type(error).__name__)
        return ()
    _log_import_results(results)
    return results


def _document_key(metadata):
    """chunk 所属文档 id；损坏到无法归属的行返回 None（下游解析同样会丢弃它）。"""
    if metadata is None or metadata == "":
        return ""
    try:
        decoded = json.loads(metadata) if isinstance(metadata, str) else metadata
    except (ValueError, TypeError):
        return None
    if not isinstance(decoded, dict):
        return None
    value = decoded.get("kb_doc_id")
    if value is None:
        return ""
    return value if isinstance(value, str) else None


def _fair_quotas(counts, order, limit):
    """整库引用的配额：每轮配额翻倍，先保证每篇至少一段，再把预算分给还有余量的文档。"""
    remaining = max(0, int(limit))
    quota = max(1, remaining // len(order))
    taken = {key: 0 for key in order}
    while remaining > 0:
        progressed = False
        for key in order:
            if remaining <= 0:
                break
            spare = counts[key] - taken[key]
            if spare <= 0:
                continue
            given = min(quota, spare, remaining)
            taken[key] += given
            remaining -= given
            progressed = True
        if not progressed:
            break
        quota *= 2
    return taken


def _connect(path: Path):
    connection = sqlite3.connect(readonly_uri(path), uri=True)
    connection.row_factory = sqlite3.Row
    return connection


__all__ = [
    "AstrBotKnowledgeSources", "KNOWLEDGE_BASE_DIR", "KnowledgeSourceReader", "SOURCE_PREFIXES", "WORLDBOOK_DEFAULTS",
    "attach_official_knowledge",
    "available_source_ids", "clean_retrieval_query", "import_configured_sources",
    "import_configured_sources_at_startup",
    "import_plan", "knowledge_base_ids", "prepare_official_knowledge_hits",
    "worldbook_paths",
]
