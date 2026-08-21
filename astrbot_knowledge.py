# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .helpers import _single_line
from .persona_config import runtime_persona_setting
from .reference_assets import normalize_reference_asset


class AstrBotKnowledgeMixin:
    """Read AstrBot knowledge-base metadata and selected chunks for roleplay context."""

    @staticmethod
    def _normalize_roleplay_knowledge_source_ids(value: Any, *, limit: int = 80) -> list[str]:
        if isinstance(value, list):
            raw_items = value
        elif isinstance(value, tuple):
            raw_items = list(value)
        else:
            raw = str(value or "")
            raw_items = re.split(r"[\r\n,，;；]+", raw)
        ids: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            text = str(item or "").strip()
            if not text or len(text) > 260:
                continue
            if not (text.startswith("kb:") or text.startswith("doc:")):
                continue
            if text in seen:
                continue
            seen.add(text)
            ids.append(text)
            if len(ids) >= limit:
                break
        return ids

    def _astrbot_knowledge_root(self) -> Path:
        try:
            return Path(get_astrbot_data_path()) / "knowledge_base"
        except Exception:
            data_dir = Path(str(getattr(self, "data_dir", "") or ".")).resolve()
            return data_dir.parent.parent / "knowledge_base"

    def _astrbot_knowledge_sources(self) -> list[dict[str, Any]]:
        root = self._astrbot_knowledge_root()
        db_path = root / "kb.db"
        if not db_path.exists():
            return []
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            bases = conn.execute(
                "select kb_id, kb_name, description, emoji, doc_count, chunk_count, updated_at "
                "from knowledge_bases order by updated_at desc, kb_name asc"
            ).fetchall()
            docs = conn.execute(
                "select kb_id, doc_id, doc_name, file_type, chunk_count, updated_at "
                "from kb_documents order by updated_at desc, doc_name asc"
            ).fetchall()
            conn.close()
        except Exception as exc:
            logger.warning(f"[PrivateCompanion] 读取 AstrBot 知识库列表失败: {exc}")
            return []

        docs_by_kb: dict[str, list[dict[str, Any]]] = {}
        for row in docs:
            kb_id = str(row["kb_id"] or "").strip()
            doc_id = str(row["doc_id"] or "").strip()
            if not kb_id or not doc_id:
                continue
            docs_by_kb.setdefault(kb_id, []).append(
                {
                    "id": f"doc:{kb_id}:{doc_id}",
                    "type": "document",
                    "kb_id": kb_id,
                    "doc_id": doc_id,
                    "name": str(row["doc_name"] or doc_id),
                    "file_type": str(row["file_type"] or ""),
                    "chunk_count": int(row["chunk_count"] or 0),
                    "updated_at": str(row["updated_at"] or ""),
                }
            )

        items: list[dict[str, Any]] = []
        for row in bases:
            kb_id = str(row["kb_id"] or "").strip()
            if not kb_id:
                continue
            documents = docs_by_kb.get(kb_id, [])
            items.append(
                {
                    "id": f"kb:{kb_id}",
                    "type": "knowledge_base",
                    "kb_id": kb_id,
                    "name": str(row["kb_name"] or kb_id),
                    "description": str(row["description"] or ""),
                    "emoji": str(row["emoji"] or ""),
                    "doc_count": int(row["doc_count"] or len(documents)),
                    "chunk_count": int(row["chunk_count"] or sum(int(doc.get("chunk_count") or 0) for doc in documents)),
                    "updated_at": str(row["updated_at"] or ""),
                    "documents": documents,
                }
            )
        return items

    def _roleplay_knowledge_summary(self) -> dict[str, Any]:
        selected = self._normalize_roleplay_knowledge_source_ids(
            runtime_persona_setting(self, "roleplay_knowledge_source_ids", [])
        )
        sources = self._astrbot_knowledge_sources()
        selected_set = set(selected)
        available_ids = {str(item.get("id") or "") for item in sources}
        for item in sources:
            for doc in item.get("documents") or []:
                available_ids.add(str(doc.get("id") or ""))
        assets = [
            normalized
            for raw in (self.data.get("photo_reference_assets") if isinstance(getattr(self, "data", None), dict) and isinstance(self.data.get("photo_reference_assets"), list) else [])
            if (normalized := normalize_reference_asset(raw)) is not None and normalized.get("scope") == "knowledge"
        ]
        asset_view = [
            {
                "id": item.get("id"),
                "owner_id": item.get("owner_id"),
                "title": _single_line(item.get("title"), 120),
                "note": _single_line(item.get("note"), 240),
                "tags": [str(tag) for tag in (item.get("tags") or [])[:12]],
                "reference_roles": list(item.get("reference_roles") or []),
                "enabled": bool(item.get("enabled", True)),
                "priority": int(item.get("priority") or 0),
                "preview_endpoint": f"/reference_asset/image_data?id={item.get('id')}" if item.get("id") else "",
            }
            for item in assets
        ]
        assets_by_owner: dict[str, int] = {}
        for asset in assets:
            owner = str(asset.get("owner_id") or "")
            assets_by_owner[owner] = assets_by_owner.get(owner, 0) + 1
        for source in sources:
            source["reference_asset_count"] = assets_by_owner.get(str(source.get("id") or ""), 0)
            for doc in source.get("documents") or []:
                doc["reference_asset_count"] = assets_by_owner.get(str(doc.get("id") or ""), 0)
        return {
            "available": bool(sources),
            "root": str(self._astrbot_knowledge_root()),
            "sources": sources,
            "selected_ids": selected,
            "selected_count": len(selected),
            "missing_ids": [item for item in selected if item not in available_ids],
            "reference_assets": asset_view,
            "reference_asset_count": len(asset_view),
        }

    def _format_roleplay_knowledge_context(
        self,
        *,
        query: str = "",
        purpose: str = "roleplay",
        max_chars: int = 3200,
        max_chunks: int = 18,
    ) -> str:
        selected = self._normalize_roleplay_knowledge_source_ids(
            runtime_persona_setting(self, "roleplay_knowledge_source_ids", [])
        )
        if not selected:
            return ""
        sources = self._astrbot_knowledge_sources()
        if not sources:
            return ""

        kb_names = {str(item.get("kb_id") or ""): str(item.get("name") or "") for item in sources}
        doc_names: dict[tuple[str, str], str] = {}
        selected_kbs: set[str] = set()
        selected_docs: dict[str, set[str]] = {}
        for source in sources:
            kb_id = str(source.get("kb_id") or "")
            if f"kb:{kb_id}" in selected:
                selected_kbs.add(kb_id)
            for doc in source.get("documents") or []:
                doc_id = str(doc.get("doc_id") or "")
                doc_names[(kb_id, doc_id)] = str(doc.get("name") or doc_id)
                if str(doc.get("id") or "") in selected:
                    selected_docs.setdefault(kb_id, set()).add(doc_id)

        if not selected_kbs and not selected_docs:
            return ""

        chunk_budget = max(1, int(max_chunks or 18))
        char_budget = max(600, int(max_chars or 3200))
        candidates: list[dict[str, Any]] = []
        for kb_id in sorted(selected_kbs | set(selected_docs)):
            doc_filter = None if kb_id in selected_kbs else selected_docs.get(kb_id, set())
            chunks = self._read_roleplay_knowledge_chunks(kb_id, doc_filter, scan_limit=max(200, chunk_budget * 80))
            for chunk in chunks:
                doc_id = str(chunk.get("kb_doc_id") or "")
                chunk["kb_id"] = kb_id
                chunk["kb_name"] = kb_names.get(kb_id, kb_id)
                chunk["doc_name"] = doc_names.get((kb_id, doc_id), doc_id or "资料")
                candidates.append(chunk)
        if not candidates:
            return ""

        effective_query = query or self._build_roleplay_knowledge_query(purpose=purpose)
        selected_chunks = self._select_roleplay_knowledge_chunks(
            candidates,
            query=effective_query,
            max_chunks=chunk_budget,
        )
        used_search = bool(selected_chunks)
        if not selected_chunks:
            selected_chunks = self._select_representative_roleplay_chunks(candidates, max_chunks=chunk_budget)

        snippets: list[str] = []
        for chunk in selected_chunks:
            text = _single_line(chunk.get("text", ""), 700)
            if not text:
                continue
            source = f"{chunk.get('kb_name') or chunk.get('kb_id') or '知识库'} / {chunk.get('doc_name') or '资料'}"
            snippets.append(f"- {source}: {text}")
        if not snippets:
            return ""
        body = "\n".join(snippets)
        if len(body) > char_budget:
            body = body[:char_budget].rstrip() + "..."
        mode_text = "按当前用途检索匹配片段" if used_search else "未命中检索词，回退为文档代表性取样"
        return (
            "【AstrBot 知识库世界观参考】\n"
            "以下内容来自拓展页角色设定中勾选的 AstrBot 知识库/文档，只作为日程模拟、生活背景和世界观适配参考；"
            "不要复述“知识库/文档/片段”等后台来源，不要把小说叙事逐字当成当前现实发言。\n"
            f"选取方式：{mode_text}；用途：{_single_line(purpose, 40) or 'roleplay'}。\n"
            f"{body}"
        )

    def _build_roleplay_knowledge_query(self, *, purpose: str = "roleplay") -> str:
        anchors = {
            "schedule": "日程 作息 生活 学校 工作 休息 住所 城市 关系 习惯 能力 身份 当前活动",
            "worldview": "世界观 时代 背景 法则 规则 能力 种族 组织 地点 关系网 术语 现代功能转译",
        }
        purpose_key = str(purpose or "").lower()
        anchor = anchors["worldview" if "world" in purpose_key or "适配" in purpose_key else "schedule"]
        parts = [
            str(purpose or ""),
            anchor,
            runtime_persona_setting(self, "bot_name", "小星"),
            runtime_persona_setting(self, "schedule_persona_prompt", ""),
            runtime_persona_setting(self, "schedule_worldview_prompt", ""),
            runtime_persona_setting(self, "roleplay_user_profile_prompt", ""),
            runtime_persona_setting(self, "worldview_adaptation_prompt", ""),
        ]
        getter = getattr(self, "_get_default_persona_prompt", None)
        if callable(getter):
            try:
                parts.append(getter())
            except Exception:
                pass
        return "\n".join(str(part or "") for part in parts if str(part or "").strip())

    def _select_roleplay_knowledge_chunks(
        self,
        chunks: list[dict[str, Any]],
        *,
        query: str,
        max_chunks: int,
    ) -> list[dict[str, Any]]:
        terms = self._roleplay_knowledge_query_terms(query)
        if not terms:
            return []

        scored: list[tuple[float, int, dict[str, Any]]] = []
        for order, chunk in enumerate(chunks):
            text = self._knowledge_norm(chunk.get("text", ""))
            title = self._knowledge_norm(f"{chunk.get('kb_name', '')} {chunk.get('doc_name', '')}")
            if not text:
                continue
            score = 0.0
            matched = 0
            for term, weight in terms:
                text_count = text.count(term)
                title_count = title.count(term)
                if text_count or title_count:
                    matched += 1
                    score += min(text_count, 4) * weight
                    score += min(title_count, 2) * weight * 1.6
            if score <= 0:
                continue
            try:
                chunk_index = int(chunk.get("chunk_index") or 0)
            except (TypeError, ValueError):
                chunk_index = 0
            score += min(matched, 6) * 0.6
            score += max(0.0, 0.35 - min(chunk_index, 12) * 0.02)
            scored.append((score, order, chunk))
        if not scored:
            return []

        scored.sort(key=lambda item: (-item[0], item[1]))
        selected: list[dict[str, Any]] = []
        doc_counts: dict[tuple[str, str], int] = {}
        doc_indices: dict[tuple[str, str], set[int]] = {}
        text_fingerprints: set[str] = set()
        per_doc_limit = max(2, (max_chunks + 1) // 2)
        deferred: list[dict[str, Any]] = []

        def add_chunk(chunk: dict[str, Any], *, allow_adjacent: bool) -> bool:
            doc_key = (str(chunk.get("kb_id") or ""), str(chunk.get("kb_doc_id") or ""))
            if doc_counts.get(doc_key, 0) >= per_doc_limit:
                return False
            try:
                chunk_index = int(chunk.get("chunk_index") or 0)
            except (TypeError, ValueError):
                chunk_index = 0
            if not allow_adjacent and any(abs(chunk_index - used) <= 1 for used in doc_indices.get(doc_key, set())):
                return False
            text = _single_line(chunk.get("text", ""), 220)
            fingerprint = self._knowledge_norm(text)[:160]
            if fingerprint and fingerprint in text_fingerprints:
                return False
            if fingerprint:
                text_fingerprints.add(fingerprint)
            selected.append(chunk)
            doc_counts[doc_key] = doc_counts.get(doc_key, 0) + 1
            doc_indices.setdefault(doc_key, set()).add(chunk_index)
            return True

        for _score, _order, chunk in scored:
            if not add_chunk(chunk, allow_adjacent=False):
                deferred.append(chunk)
            if len(selected) >= max_chunks:
                break
        for chunk in deferred:
            if len(selected) >= max_chunks:
                break
            add_chunk(chunk, allow_adjacent=True)
        return selected

    def _select_representative_roleplay_chunks(
        self,
        chunks: list[dict[str, Any]],
        *,
        max_chunks: int,
    ) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for chunk in chunks:
            grouped.setdefault(
                (str(chunk.get("kb_id") or ""), str(chunk.get("kb_doc_id") or "")),
                [],
            ).append(chunk)
        for items in grouped.values():
            items.sort(key=lambda item: int(item.get("chunk_index") or 0))
        selected: list[dict[str, Any]] = []
        max_len = max((len(items) for items in grouped.values()), default=0)
        for index in range(max_len):
            for key in sorted(grouped):
                items = grouped[key]
                if index < len(items):
                    selected.append(items[index])
                    if len(selected) >= max_chunks:
                        return selected
        return selected

    def _roleplay_knowledge_query_terms(self, query: str) -> list[tuple[str, float]]:
        text = self._knowledge_norm(query)
        if not text:
            return []
        raw_terms = re.findall(r"[a-z][a-z0-9_+-]{2,}|[\u4e00-\u9fff]{2,12}", text)
        stop_terms = {
            "角色设定", "世界观", "生活背景", "其他补充", "用户补充", "当前使用",
            "日程专用", "适配模式", "自定义", "默认人格", "插件", "不要", "可以",
            "例如", "使用", "当前", "补充信息", "主要", "基本", "关系",
        }
        weighted: dict[str, float] = {}
        for raw in raw_terms:
            term = raw.strip()
            if not term or term in stop_terms:
                continue
            weight = 1.0 + min(len(term), 8) / 8.0
            weighted[term] = max(weighted.get(term, 0.0), weight)
            if re.fullmatch(r"[\u4e00-\u9fff]{5,12}", term):
                for size in (2, 3, 4):
                    for index in range(0, len(term) - size + 1):
                        sub = term[index:index + size]
                        if sub in stop_terms:
                            continue
                        weighted[sub] = max(weighted.get(sub, 0.0), 0.65 + size * 0.15)
        return sorted(weighted.items(), key=lambda item: (-item[1], item[0]))[:90]

    @staticmethod
    def _knowledge_norm(value: Any) -> str:
        text = unicodedata.normalize("NFKC", str(value or "")).lower()
        return re.sub(r"\s+", "", text)

    def _read_roleplay_knowledge_chunks(
        self,
        kb_id: str,
        doc_filter: set[str] | None,
        *,
        scan_limit: int = 1600,
    ) -> list[dict[str, Any]]:
        db_path = self._astrbot_knowledge_root() / kb_id / "doc.db"
        if not db_path.exists():
            return []
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute("select text, metadata from documents order by id asc").fetchall()
            conn.close()
        except Exception as exc:
            logger.warning(f"[PrivateCompanion] 读取 AstrBot 知识库片段失败 {kb_id}: {exc}")
            return []

        chunks: list[dict[str, Any]] = []
        for row in rows:
            try:
                metadata = json.loads(row["metadata"] or "{}")
            except Exception:
                metadata = {}
            kb_doc_id = str(metadata.get("kb_doc_id") or "")
            if doc_filter is not None and kb_doc_id not in doc_filter:
                continue
            try:
                chunk_index = int(metadata.get("chunk_index") or 0)
            except (TypeError, ValueError):
                chunk_index = 0
            chunks.append(
                {
                    "text": str(row["text"] or ""),
                    "kb_doc_id": kb_doc_id,
                    "chunk_index": chunk_index,
                }
            )
            if len(chunks) >= max(1, int(scan_limit or 1600)):
                break
        chunks.sort(key=lambda item: (str(item.get("kb_doc_id") or ""), int(item.get("chunk_index") or 0)))
        return chunks
