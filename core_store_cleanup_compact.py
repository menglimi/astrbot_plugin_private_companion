# -*- coding: utf-8 -*-
"""CoreStoreCleanupCompactMixin。

由 tools/split_mixin_domain.py 从 core_store.py 机械抽取（15 个方法 + 0 个模块级名字 + 0 个类级赋值 / 507 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 CoreStoreMixin）。
"""
from __future__ import annotations

from .core_store_shared import logger
from .core_store_shared import Any
from .core_store_shared import Path
from .core_store_shared import _safe_float
from .core_store_shared import _safe_int
from .core_store_shared import _single_line
from .core_store_shared import _strip_persisted_chat_control_tags
from .core_store_shared import datetime
from .core_store_shared import deepcopy
from .core_store_shared import json
from .core_store_shared import runtime_persona_setting
from .core_store_shared import time



class CoreStoreCleanupCompactMixin:
    """CoreStoreCleanupCompactMixin（从 CoreStoreMixin 拆出）。"""


    @staticmethod
    def _store_path_is_raw_user_text(path: tuple[Any, ...]) -> bool:
        """Raw observations are evidence and must not be rewritten during persistence."""
        if "recent_phrases" in path:
            return True
        if len(path) >= 3 and path[0] == "memo_notes" and path[-1] in {"title", "content"}:
            return True
        return bool(
            len(path) >= 5
            and path[0] == "groups"
            and path[2] == "recent_messages"
            and path[-1] == "text"
        )

    def _sanitize_store_control_tags_inplace(self, value: Any, _path: tuple[Any, ...] = ()) -> int:
        """Remove leaked pseudo-control tags from persisted companion data."""
        if not bool(getattr(self, "enable_store_control_tag_sanitization", True)):
            return 0
        changed = 0
        if isinstance(value, dict):
            for key, item in list(value.items()):
                item_path = (*_path, key)
                if isinstance(item, str):
                    if self._store_path_is_raw_user_text(item_path):
                        continue
                    cleaned = _strip_persisted_chat_control_tags(
                        item, tts_enabled=bool(runtime_persona_setting(self, "enable_tts_enhancement", False))
                    )
                    if cleaned != item:
                        value[key] = cleaned
                        changed += 1
                elif isinstance(item, (dict, list)):
                    changed += self._sanitize_store_control_tags_inplace(item, item_path)
            return changed
        if isinstance(value, list):
            for idx, item in enumerate(list(value)):
                item_path = (*_path, idx)
                if isinstance(item, str):
                    if self._store_path_is_raw_user_text(item_path):
                        continue
                    cleaned = _strip_persisted_chat_control_tags(
                        item, tts_enabled=bool(runtime_persona_setting(self, "enable_tts_enhancement", False))
                    )
                    if cleaned != item:
                        value[idx] = cleaned
                        changed += 1
                elif isinstance(item, (dict, list)):
                    changed += self._sanitize_store_control_tags_inplace(item, item_path)
            return changed
        return 0

    def _log_store_control_cleanup(self, stage: str, changed: int, *, cooldown_seconds: float = 600.0) -> bool:
        if changed <= 0:
            return False
        now = time.monotonic()
        states = getattr(self, "_store_control_cleanup_log_states", None)
        if not isinstance(states, dict):
            states = {}
            self._store_control_cleanup_log_states = states
        key = _single_line(stage, 40) or "unknown"
        state = states.setdefault(key, {"last_at": 0.0, "suppressed_events": 0, "suppressed_fields": 0})
        last_at = _safe_float(state.get("last_at"), 0.0, 0.0)
        if last_at and now - last_at < max(1.0, float(cooldown_seconds)):
            state["suppressed_events"] = _safe_int(state.get("suppressed_events"), 0, 0) + 1
            state["suppressed_fields"] = _safe_int(state.get("suppressed_fields"), 0, 0) + changed
            return False
        suppressed_events = _safe_int(state.get("suppressed_events"), 0, 0)
        suppressed_fields = _safe_int(state.get("suppressed_fields"), 0, 0)
        state.update({"last_at": now, "suppressed_events": 0, "suppressed_fields": 0})
        suffix = (
            f" suppressed_events={suppressed_events} suppressed_fields={suppressed_fields}"
            if suppressed_events
            else ""
        )
        logger.info(
            "Store safety cleanup: stage=%s fields=%s%s",
            key,
            changed,
            suffix,
        )
        return True

    @staticmethod
    def _proactive_candidate_repeat_limit_for_status(status: Any) -> int:
        normalized = str(status or "").strip().lower()
        if normalized in {"accepted", "deferred", "queued", "pending", "unknown", ""}:
            return 12
        if normalized == "sent":
            return 8
        return 6

    def _sanitize_proactive_candidate_repeat_counts_inplace(self, data: Any) -> int:
        if not isinstance(data, dict):
            return 0
        pool = data.get("proactive_candidate_pool")
        if not isinstance(pool, list):
            return 0
        changed = 0
        for item in pool:
            if not isinstance(item, dict):
                continue
            limit = self._proactive_candidate_repeat_limit_for_status(item.get("status"))
            current = _safe_int(item.get("repeat_count"), 1, 1)
            normalized = max(1, min(limit, current))
            if current != normalized:
                item["repeat_count"] = normalized
                item["repeat_count_capped"] = True
                changed += 1
        if changed:
            data["proactive_candidate_repeat_sanitized_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return changed

    @staticmethod
    def _store_history_item_timestamp(item: dict[str, Any]) -> float:
        return max(
            _safe_float(item.get("updated_ts"), 0),
            _safe_float(item.get("created_ts"), 0),
            _safe_float(item.get("scheduled_ts"), 0),
            _safe_float(item.get("last_seen_ts"), 0),
            _safe_float(item.get("ts"), 0),
        )

    @staticmethod
    def _compact_external_history_items(items: Any, *, limit: int) -> list[dict[str, Any]]:
        if not isinstance(items, list):
            return []
        text_limits = {
            "key": 120,
            "id": 120,
            "title": 300,
            "headline": 300,
            "topic": 200,
            "source": 100,
            "source_title": 300,
            "summary": 1200,
            "note": 1200,
            "impression": 1200,
            "reason": 400,
            "link": 800,
            "url": 800,
            "source_url": 800,
            "published_at": 80,
            "published": 80,
            "date": 80,
        }
        compacted: list[dict[str, Any]] = []
        for raw in items[: max(0, limit)]:
            if not isinstance(raw, dict):
                continue
            item: dict[str, Any] = {}
            for key, max_len in text_limits.items():
                value = raw.get(key)
                if value is not None and str(value).strip():
                    item[key] = _single_line(value, max_len)
            for key in ("created_ts", "published_ts", "score", "rank", "text_readable", "video_subtitle_readable"):
                value = raw.get(key)
                if isinstance(value, (int, float, bool)):
                    item[key] = value
            if item:
                compacted.append(item)
        return compacted

    def _compact_store_history_inplace(self, data: Any) -> dict[str, int]:
        if not isinstance(data, dict):
            return {}
        changed: dict[str, int] = {}

        pool = data.get("proactive_candidate_pool")
        if isinstance(pool, list) and len(pool) > 600:
            candidates = [item for item in pool if isinstance(item, dict)]
            users = data.get("users") if isinstance(data.get("users"), dict) else {}
            planned_ids = {
                _single_line(user.get("planned_candidate_id"), 40)
                for user in users.values()
                if isinstance(user, dict) and _single_line(user.get("planned_candidate_id"), 40)
            }
            active_statuses = {"", "accepted", "deferred", "queued", "pending", "unknown"}
            protected = [item for item in candidates if _single_line(item.get("id"), 40) in planned_ids]
            protected_ids = {_single_line(item.get("id"), 40) for item in protected}
            active = [
                item
                for item in candidates
                if _single_line(item.get("id"), 40) not in protected_ids
                and _single_line(item.get("status"), 24).lower() in active_statuses
            ]
            completed = [
                item
                for item in candidates
                if _single_line(item.get("id"), 40) not in protected_ids
                and _single_line(item.get("status"), 24).lower() not in active_statuses
            ]
            active.sort(key=self._store_history_item_timestamp, reverse=True)
            completed.sort(key=self._store_history_item_timestamp, reverse=True)
            remaining = max(0, 600 - len(protected))
            kept_active = active[:remaining]
            kept_completed = completed[: max(0, remaining - len(kept_active))]
            kept = protected + kept_active + kept_completed
            kept.sort(key=self._store_history_item_timestamp)
            data["proactive_candidate_pool"] = kept[-600:]
            changed["proactive_candidate_pool"] = len(pool) - len(data["proactive_candidate_pool"])

        news = data.get("news_integration")
        if isinstance(news, dict):
            digests = news.get("digests")
            if isinstance(digests, list):
                compacted_digests: list[dict[str, Any]] = []
                removed_payloads = 0
                for raw in digests[-32:]:
                    if not isinstance(raw, dict):
                        continue
                    digest = dict(raw)
                    for key in ("items", "results", "raw_items", "articles"):
                        if key in digest:
                            digest.pop(key, None)
                            removed_payloads += 1
                    compacted_digests.append(digest)
                if len(compacted_digests) != len(digests) or removed_payloads:
                    news["digests"] = compacted_digests
                    changed["news_digests"] = max(0, len(digests) - len(compacted_digests)) + removed_payloads
            latest_items = news.get("latest_items")
            if isinstance(latest_items, list):
                compacted_latest = self._compact_external_history_items(latest_items, limit=12)
                if compacted_latest != latest_items:
                    news["latest_items"] = compacted_latest
                    changed["news_latest_items"] = len(latest_items)
            last_digest = news.get("last_digest")
            if isinstance(last_digest, dict) and isinstance(last_digest.get("items"), list):
                compacted_items = self._compact_external_history_items(last_digest.get("items"), limit=8)
                if compacted_items != last_digest.get("items"):
                    last_digest["items"] = compacted_items
                    changed["news_last_digest_items"] = len(compacted_items)

        web = data.get("web_exploration")
        if isinstance(web, dict):
            notes = web.get("notes")
            if isinstance(notes, list):
                compacted_notes: list[dict[str, Any]] = []
                removed_payloads = 0
                for raw in notes[-40:]:
                    if not isinstance(raw, dict):
                        continue
                    note = dict(raw)
                    for key in ("results", "raw_results", "pages"):
                        if key in note:
                            note.pop(key, None)
                            removed_payloads += 1
                    compacted_notes.append(note)
                if len(compacted_notes) != len(notes) or removed_payloads:
                    web["notes"] = compacted_notes
                    changed["web_notes"] = max(0, len(notes) - len(compacted_notes)) + removed_payloads
            latest_results = web.get("latest_results")
            if isinstance(latest_results, list):
                compacted_results = self._compact_external_history_items(latest_results, limit=8)
                if compacted_results != latest_results:
                    web["latest_results"] = compacted_results
                    changed["web_latest_results"] = len(latest_results)
            last_digest = web.get("last_digest")
            if isinstance(last_digest, dict) and isinstance(last_digest.get("results"), list):
                compacted_results = self._compact_external_history_items(last_digest.get("results"), limit=6)
                if compacted_results != last_digest.get("results"):
                    last_digest["results"] = compacted_results
                    changed["web_last_digest_results"] = len(compacted_results)
        return changed

    @staticmethod
    def _strip_ephemeral_group_transcripts_inplace(data: Any) -> dict[str, int]:
        """Project group chat to a bounded restart-safe context window for snapshots."""
        if not isinstance(data, dict):
            return {}
        groups = data.get("groups")
        if not isinstance(groups, dict):
            return {}
        removed_messages = 0
        removed_bot_replies = 0
        removed_phrases = 0
        for group in groups.values():
            if not isinstance(group, dict):
                continue
            recent = group.get("recent_messages")
            if isinstance(recent, list) and recent:
                # Keep only a small restart-safe tail. The live store still
                # retains its configured window for in-process reasoning.
                keep = [item for item in recent[-12:] if isinstance(item, dict)]
                for item in keep:
                    item["text"] = _single_line(item.get("text"), 180)
                    item.pop("image_vision", None)
                removed_messages += max(0, len(recent) - len(keep))
                group["recent_messages"] = keep
            recent_bot = group.get("recent_bot_replies")
            if isinstance(recent_bot, list) and recent_bot:
                keep_bot = [item for item in recent_bot[-12:] if isinstance(item, dict)]
                for item in keep_bot:
                    item["text"] = _single_line(item.get("text"), 500)
                removed_bot_replies += max(0, len(recent_bot) - len(keep_bot))
                group["recent_bot_replies"] = keep_bot
            members = group.get("members")
            if not isinstance(members, dict):
                continue
            for member in members.values():
                if not isinstance(member, dict):
                    continue
                phrases = member.get("recent_phrases")
                if isinstance(phrases, list) and phrases:
                    keep_phrases = [_single_line(item, 80) for item in phrases[:4] if _single_line(item, 80)]
                    removed_phrases += max(0, len(phrases) - len(keep_phrases))
                    member["recent_phrases"] = keep_phrases
        changed: dict[str, int] = {}
        if removed_messages:
            changed["group_recent_messages"] = removed_messages
        if removed_bot_replies:
            changed["group_recent_bot_replies"] = removed_bot_replies
        if removed_phrases:
            changed["group_member_recent_phrases"] = removed_phrases
        return changed

    def _mark_bookshelf_store_changed(self, data: dict[str, Any] | None = None) -> int:
        target = data if isinstance(data, dict) else self.data
        try:
            current = max(0, int(target.get("bookshelf_store_revision") or 0))
        except (TypeError, ValueError, OverflowError):
            current = 0
        revision = max(current + 1, int(time.time() * 1000))
        target["bookshelf_store_revision"] = revision
        return revision

    def _recover_bookshelf_after_load(self, data: dict[str, Any]) -> int:
        recoverer = getattr(self, "_recover_bookshelf_items_from_local_pages_inplace", None)
        if not callable(recoverer):
            return 0
        try:
            recovered = max(0, int(recoverer(data) or 0))
        except Exception as exc:
            logger.warning(
                "启动恢复夹层本地书页失败，已保留现有存储: %s",
                _single_line(exc, 160),
            )
            return 0
        if recovered:
            logger.warning("已根据本地书页和删除记录校准夹层书库: changed=%s", recovered)
        return recovered

    def _persist_startup_maintenance_sync(
        self,
        manager: Any,
        before: dict[str, Any],
        data: dict[str, Any],
        persisted_tombstones: dict[str, int] | None = None,
    ) -> None:
        tombstones = {
            str(section): int(revision)
            for section, revision in (persisted_tombstones or {}).items()
        }
        for section in tombstones:
            data.pop(section, None)
        changed_sections = {
            str(section)
            for section, value in data.items()
            if section not in before or before[section] != value
        }
        deleted_sections = {str(section) for section in before if section not in data}
        bookshelf_sections = {
            "bookshelf_items",
            "bookshelf_secret",
            "bookshelf_store_revision",
            "reading_archive_integration",
        }
        bookshelf_tombstones = bookshelf_sections & set(tombstones)
        if bookshelf_sections & (changed_sections | deleted_sections):
            changed_sections.difference_update(bookshelf_tombstones)
            deleted_sections.update(bookshelf_tombstones)
        if not changed_sections and not deleted_sections:
            return

        incremental = bool(
            str(getattr(manager, "backend_name", "") or "").lower() == "sqlite"
            and callable(getattr(manager, "save_sections", None))
            and callable(getattr(manager, "next_revision", None))
        )
        if not incremental:
            manager.save_store(data)
            self._refresh_data_save_revision_from_manager()
            return

        self._expand_bookshelf_save_sections(
            data,
            changed_sections,
            deleted_sections,
            tombstones,
        )
        revision = manager.next_revision()
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise RuntimeError("SQLite startup maintenance returned an invalid revision")
        confirmed = manager.save_sections(
            {
                section: (revision, deepcopy(data[section]))
                for section in changed_sections
            },
            {section: revision for section in deleted_sections},
        )
        expected = changed_sections | deleted_sections
        unconfirmed = sorted(
            section
            for section in expected
            if int(confirmed.get(section, -1)) < revision
        )
        if unconfirmed:
            raise RuntimeError(
                "SQLite startup maintenance did not confirm sections: "
                + ", ".join(unconfirmed)
            )
        self._refresh_data_save_revision_from_manager()

    def _primary_store_owner_id(self) -> str:
        getter = getattr(self, "_primary_persona_id", None)
        if callable(getter):
            try:
                return str(getter() or "").strip()
            except Exception:
                pass
        return str(getattr(self, "plugin_specific_persona_id", "") or "").strip()

    def _ensure_primary_store_ownership(self, data: dict[str, Any]) -> bool:
        """Annotate the legacy primary store without changing its contents."""
        if not isinstance(data, dict):
            return False
        before = deepcopy(data.get("primary_store_ownership"))
        self._ensure_store_defaults(data)
        ownership = data.get("primary_store_ownership")
        if not isinstance(ownership, dict):
            ownership = {
                "schema_version": 1,
                "owner_persona_id": "",
                "active_persona_id": "",
                "status": "unattributed",
                "history": [],
            }
            data["primary_store_ownership"] = ownership
        current = self._primary_store_owner_id()
        owner = str(ownership.get("owner_persona_id") or "").strip()
        if not owner and current:
            ownership["owner_persona_id"] = current
            ownership["status"] = "bound"
        ownership["active_persona_id"] = current
        if owner and current and owner != current:
            ownership["status"] = "pending_review"
            warning = {
                "code": "primary_store_owner_mismatch",
                "owner_persona_id": owner,
                "active_persona_id": current,
                "message": "主存储仍绑定旧主人格；请在确认备份后决定保留、迁移或合并历史。",
            }
            self._primary_store_ownership_warning = warning
            logger.warning(
                "检测到主存储人格归属变化: owner=%s active=%s",
                owner,
                current,
            )
        else:
            self._primary_store_ownership_warning = {}
        return before != ownership

    def _record_primary_persona_change(self, old_persona_id: Any, new_persona_id: Any) -> dict[str, Any]:
        """Record a primary-ID change and preserve a recoverable local snapshot."""
        old_id = str(old_persona_id or "").strip()
        new_id = str(new_persona_id or "").strip()
        if old_id == new_id:
            return {}
        data = getattr(self, "_data_default", None)
        if not isinstance(data, dict):
            return {"code": "primary_store_snapshot_unavailable", "message": "主存储尚未加载，未执行归属记录"}
        self._ensure_store_defaults(data)
        ownership = data.get("primary_store_ownership")
        if not isinstance(ownership, dict):
            ownership = {
                "schema_version": 1,
                "owner_persona_id": "",
                "active_persona_id": "",
                "status": "unattributed",
                "history": [],
            }
            data["primary_store_ownership"] = ownership
        owner = str(ownership.get("owner_persona_id") or old_id).strip()
        backup_path = ""
        try:
            root = Path(str(getattr(self, "data_dir", "") or Path(str(getattr(self, "data_file", "companions.json"))).parent))
            root.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            backup = root / f"companions.json.primary-switch-{stamp}.bak"
            backup.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            backup_path = str(backup)
        except Exception as exc:
            logger.warning("主人格切换快照写入失败: %s", _single_line(exc, 180))
        history = ownership.setdefault("history", [])
        if not isinstance(history, list):
            history = []
            ownership["history"] = history
        history.append({
            "at": datetime.now().isoformat(timespec="seconds"),
            "from_persona_id": owner,
            "to_persona_id": new_id,
            "backup_path": backup_path,
            "status": "pending_review",
        })
        del history[:-20]
        ownership["owner_persona_id"] = owner
        ownership["active_persona_id"] = new_id
        ownership["status"] = "pending_review"
        warning = {
            "code": "primary_store_owner_mismatch",
            "owner_persona_id": owner,
            "active_persona_id": new_id,
            "backup_path": backup_path,
            "message": "主人格已变更，companions.json 的历史归属未自动迁移；请依据备份进行保留、迁移或合并。",
        }
        self._primary_store_ownership_warning = warning
        try:
            self._write_data_snapshot_sync(deepcopy(data))
        except Exception as exc:
            logger.warning("主人格归属审计写入失败: %s", _single_line(exc, 180))
            warning["persist_error"] = _single_line(exc, 180)
        return warning
