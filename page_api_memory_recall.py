# -*- coding: utf-8 -*-
"""记忆召回域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（10 个方法 + 0 个模块级名字 + 0 个类级赋值 / 237 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import sqlite3
import time
from .constants import WORLDBOOK_IMPORTANT_MEMORY_CAPACITY
from .memo_notes import apply_memo_note_action, memo_note_due_state, memo_note_sort_key, normalize_memo_note
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from .page_api_shared import _page_api_host, _page_api_host_request as request
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class PrivateCompanionPageApiMemoryRecallMixin:
    """记忆召回域（从 PrivateCompanionPageApi 拆出）。"""


    def _memo_notes_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        raw_notes = data.get("memo_notes") if isinstance(data.get("memo_notes"), list) else []
        notes = [note for note in (normalize_memo_note(item, now=now) for item in raw_notes) if note]
        notes.sort(key=lambda item: memo_note_sort_key(item, now=now))
        rows: list[dict[str, Any]] = []
        for note in notes[:200]:
            due_at = self._float(note.get("due_at"))
            due_text = ""
            due_input = ""
            if due_at > 0:
                try:
                    due_dt = self.plugin._environment_fromtimestamp(due_at)
                    due_text = due_dt.strftime("%Y-%m-%d %H:%M")
                    due_input = due_dt.strftime("%Y-%m-%dT%H:%M")
                except Exception:
                    due_text = datetime.fromtimestamp(due_at).strftime("%Y-%m-%d %H:%M")
                    due_input = datetime.fromtimestamp(due_at).strftime("%Y-%m-%dT%H:%M")
            rows.append({
                **note,
                "due_state": memo_note_due_state(note, now=now),
                "due_text": due_text,
                "due_input": due_input,
                "created_text": self.plugin._format_timestamp_elapsed(note.get("created_at", 0)),
                "updated_text": self.plugin._format_timestamp_elapsed(note.get("updated_at", 0)),
            })
        active = [item for item in rows if item.get("status") == "active"]
        return {
            "items": rows,
            "total": len(rows),
            "active": len(active),
            "completed": sum(1 for item in rows if item.get("status") == "completed"),
            "overdue": sum(1 for item in active if item.get("due_state") == "overdue"),
            "due_soon": sum(1 for item in active if item.get("due_state") in {"due", "today"}),
        }

    async def list_memo_notes(self) -> dict[str, Any]:
        try:
            async with self.plugin._data_lock:
                data = deepcopy(self.plugin.data)
            return self._ok({"memo_notes": self._memo_notes_payload(data)})
        except Exception as exc:
            logger.error(f"获取备忘便签失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    async def update_memo_note(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        now = time.time()
        try:
            async with self.plugin._data_lock:
                notes, _ = apply_memo_note_action(
                    self.plugin.data.get("memo_notes"),
                    payload,
                    now=now,
                    fromtimestamp=self.plugin._environment_fromtimestamp,
                )
                self.plugin.data["memo_notes"] = notes[-200:]
                self.plugin._save_data_sync(sections={"memo_notes"})
                result = self._memo_notes_payload(self.plugin.data)
            return self._ok({"memo_notes": result})
        except ValueError as exc:
            return self._exception_error(str(exc))
        except Exception as exc:
            logger.error(f"更新备忘便签失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    def _normalize_important_memories(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        memories: list[dict[str, Any]] = []
        for raw in value[:12]:
            if not isinstance(raw, dict):
                continue
            content = str(raw.get("content") or "").strip()[:500]
            if not content:
                continue
            privacy = self._single_line(raw.get("privacy"), 20).lower()
            if privacy not in {"public", "private", "internal"}:
                privacy = "internal"
            memory = {
                "title": self._single_line(raw.get("title"), 60),
                "content": content,
                "weight": self._clamp_int(raw.get("weight"), 50, 0, 100),
                "privacy": privacy,
                "source": self._single_line(raw.get("source"), 40),
                "enabled": bool(raw.get("enabled", True)),
                "updated_at": float(raw.get("updated_at") or time.time()),
            }
            import_batch_id = self._single_line(raw.get("import_batch_id"), 120)
            source_observation_id = self._single_line(raw.get("source_observation_id"), 120)
            if import_batch_id:
                memory["import_batch_id"] = import_batch_id
            if source_observation_id:
                memory["source_observation_id"] = source_observation_id
            memories.append(memory)
        memories.sort(key=lambda item: (item.get("enabled", True), item.get("weight", 50), item.get("updated_at", 0)), reverse=True)
        return memories[:WORLDBOOK_IMPORTANT_MEMORY_CAPACITY]

    @staticmethod
    def _memory_item_count(memory: Any) -> int:
        if not isinstance(memory, dict):
            return 0
        count = 0
        for value in memory.values():
            if isinstance(value, list):
                count += len(value)
            elif value:
                count += 1
        return count

    def _livingmemory_db_path(self) -> Path | None:
        candidates: list[Path] = []
        data_dir = Path(str(getattr(self.plugin, "data_dir", "") or "")).resolve()
        if data_dir:
            candidates.append(data_dir.parent / "astrbot_plugin_livingmemory" / "livingmemory.db")
            candidates.append(data_dir.parent / "astrbot_plugin_livingmemory" / "livingmemory_graph_documents.db")
        candidates.append(Path.home() / ".astrbot" / "data" / "plugin_data" / "astrbot_plugin_livingmemory" / "livingmemory.db")
        for path in candidates:
            try:
                if path.exists() and path.is_file():
                    return path
            except OSError:
                continue
        return None

    def _livingmemory_match_info(self, content: str, metadata_text: str, token_bundle: dict[str, list[str]]) -> dict[str, Any]:
        haystack = f"{content}\n{metadata_text}".lower()
        score = 0.0
        primary_tokens = token_bundle.get("primary_tokens", [])
        support_tokens = token_bundle.get("support_tokens", [])
        matched_primary: list[str] = []
        matched_support: list[str] = []
        for token in primary_tokens:
            text = token.lower()
            if not text or text not in haystack:
                continue
            count = max(1, haystack.count(text))
            if token.isdigit():
                score += 9.0 + min(count, 4)
            else:
                score += min(8.0, 3.5 + len(token) * 0.65) + min(count - 1, 3) * 0.7
            matched_primary.append(token)
        for token in support_tokens:
            text = token.lower()
            if not text or text not in haystack:
                continue
            count = max(1, haystack.count(text))
            score += min(3.0, 0.8 + len(token) * 0.25) + min(count - 1, 2) * 0.25
            matched_support.append(token)
        accepted = bool(matched_primary) or (not primary_tokens and len(matched_support) >= 2)
        return {
            "accepted": accepted,
            "score": round(score, 3),
            "matched_tokens": [*matched_primary, *matched_support][:8],
            "primary_hits": len(matched_primary),
            "support_hits": len(matched_support),
        }

    def _livingmemory_item_from_document(self, row: sqlite3.Row, token_bundle: dict[str, list[str]]) -> dict[str, Any] | None:
        metadata = self._json_dict(row["metadata"])
        content = str(row["text"] or "").strip()
        match = self._livingmemory_match_info(content, str(row["metadata"] or ""), token_bundle)
        if not match.get("accepted"):
            return None
        create_time = self._coerce_float(metadata.get("create_time"))
        last_access = self._coerce_float(metadata.get("last_access_time"))
        topics = metadata.get("topics") if isinstance(metadata.get("topics"), list) else []
        key_facts = metadata.get("key_facts") if isinstance(metadata.get("key_facts"), list) else []
        return {
            "source": "documents",
            "source_label": "长期记忆文档",
            "id": row["doc_id"] or row["id"],
            "score": match.get("score"),
            "matched_tokens": match.get("matched_tokens", []),
            "primary_hits": match.get("primary_hits", 0),
            "support_hits": match.get("support_hits", 0),
            "session_id": self._single_line(metadata.get("session_id"), 80),
            "persona_id": self._single_line(metadata.get("persona_id"), 80),
            "importance": metadata.get("importance"),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
            "create_time": create_time,
            "last_access_time": last_access,
            "topics": [self._single_line(item, 40) for item in topics[:8] if self._single_line(item, 40)],
            "key_facts": [self._single_line(item, 120) for item in key_facts[:8] if self._single_line(item, 120)],
            "preview": self._single_line(metadata.get("canonical_summary") or content, 260),
            "content": content[:1800],
        }

    def _livingmemory_item_from_atom(self, row: sqlite3.Row, token_bundle: dict[str, list[str]]) -> dict[str, Any] | None:
        content = str(row["content"] or "").strip()
        entities = self._json_list(row["entities"])
        metadata = self._json_dict(row["metadata"])
        metadata_text = " ".join([str(row["entities"] or ""), str(row["metadata"] or "")])
        match = self._livingmemory_match_info(content, metadata_text, token_bundle)
        if not match.get("accepted"):
            return None
        return {
            "source": "atoms",
            "source_label": "原子记忆",
            "id": row["id"],
            "parent_memory_id": row["parent_memory_id"],
            "score": match.get("score"),
            "matched_tokens": match.get("matched_tokens", []),
            "primary_hits": match.get("primary_hits", 0),
            "support_hits": match.get("support_hits", 0),
            "session_id": self._single_line(row["session_id"], 80),
            "persona_id": self._single_line(row["persona_id"], 80),
            "importance": row["importance"],
            "confidence": row["confidence"],
            "created_at": str(row["created_at"] or ""),
            "create_time": self._coerce_float(row["created_at"]),
            "last_access_time": self._coerce_float(row["last_accessed_at"]),
            "topics": [self._single_line(item, 40) for item in entities[:8] if self._single_line(item, 40)],
            "key_facts": [self._single_line(item, 120) for item in metadata.get("key_facts", [])[:6]] if isinstance(metadata.get("key_facts"), list) else [],
            "preview": self._single_line(content, 260),
            "content": content[:1200],
        }

    def _livingmemory_item_from_graph_entry(self, row: sqlite3.Row, token_bundle: dict[str, list[str]]) -> dict[str, Any] | None:
        metadata = self._json_dict(row["metadata"])
        content = str(row["content"] or "").strip()
        match = self._livingmemory_match_info(content, str(row["metadata"] or ""), token_bundle)
        if not match.get("accepted"):
            return None
        return {
            "source": "graph",
            "source_label": "关系图谱",
            "id": row["entry_key"] or row["id"],
            "parent_memory_id": row["source_memory_id"],
            "score": match.get("score"),
            "matched_tokens": match.get("matched_tokens", []),
            "primary_hits": match.get("primary_hits", 0),
            "support_hits": match.get("support_hits", 0),
            "session_id": self._single_line(row["session_id"] or metadata.get("session_id"), 80),
            "persona_id": self._single_line(row["persona_id"] or metadata.get("persona_id"), 80),
            "importance": metadata.get("importance"),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
            "create_time": self._coerce_float(metadata.get("create_time")),
            "last_access_time": self._coerce_float(metadata.get("last_access_time")),
            "topics": [],
            "key_facts": [],
            "preview": self._single_line(content, 260),
            "content": content[:1600],
        }
