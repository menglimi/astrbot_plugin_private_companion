from __future__ import annotations

import hashlib
import time
from copy import deepcopy
from typing import Any

from .constants import (
    WORLDBOOK_IMPORTANT_MEMORY_CAPACITY,
    WORLDBOOK_PENDING_OBSERVATION_CAPACITY,
)
from .helpers import _safe_int, _single_line


class _RelationshipCapabilityFamily:
    """Private capability family backed only by its owning façade."""

    __slots__ = ("_owner",)

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def get_reality_touch_host_context(self, user_id: str) -> dict[str, Any]:
        """Expose bounded identity and relationship context to the device plugin."""
        plugin = self._owner._plugin
        normalized = _single_line(user_id, 120)
        binder = getattr(plugin, "_req041_reality_private_binding", None)
        binding = binder(normalized, purpose="memory_read") if callable(binder) else None
        if callable(binder):
            user = binding.get("user") if isinstance(binding, dict) and binding.get("ok") is True else {}
            identity_ready = bool(user)
        else:
            users = plugin.data.get("users") if isinstance(plugin.data, dict) else None
            user = users.get(normalized) if isinstance(users, dict) else None
            user = user if isinstance(user, dict) else {}
            identity_ready = bool(user)
        admin_checker = getattr(plugin, "_is_configured_admin_user_id", None)
        owner_getter = getattr(plugin, "_relationship_owner_user_ids", None)
        owners = set(owner_getter() if callable(owner_getter) else ())
        target_getter = getattr(plugin, "_configured_target_ids", None)
        targets = set(target_getter() if callable(target_getter) else ())
        is_primary_user = normalized in owners or normalized in targets
        quota_getter = getattr(plugin, "_proactive_quota_policy", None)
        quota = quota_getter(user) if callable(quota_getter) and user else {}
        relationship_formatter = getattr(plugin, "_format_proactive_relationship_fact", None)
        relationship = relationship_formatter(user) if callable(relationship_formatter) and user else ""
        return {
            "user_id": normalized,
            "exists": bool(user),
            "identity_ready": identity_ready,
            "reality_subject_ref": _single_line(binding.get("subject_ref"), 160)
            if isinstance(binding, dict) and binding.get("ok") is True
            else normalized,
            "is_admin": bool(callable(admin_checker) and admin_checker(normalized)),
            "is_primary_user": is_primary_user,
            "eligible": bool(
                normalized
                and (
                    is_primary_user
                    or (callable(admin_checker) and admin_checker(normalized))
                )
            ),
            "proactive_tier": _safe_int(quota.get("tier"), 1, 1, 5) if isinstance(quota, dict) else 1,
            "relationship": _single_line(relationship, 500),
            "umo": _single_line(user.get("umo"), 180),
            "display_name": _single_line(
                user.get("nickname") or user.get("last_display_name") or user.get("display_name"),
                80,
            ),
        }

    @staticmethod
    def _new_historical_member_profile(user_id: str, user_name: str) -> dict[str, Any]:
        return {
            "user_id": user_id,
            "identity_type": "qq" if user_id.isdigit() else "external",
            "name": _single_line(user_name, 80) or user_id,
            "aliases": [],
            "observed_names": [],
            "content": "",
            "identity_note": "",
            "boundary_note": "",
            "important_memories": [],
            "pending_observations": [],
            "enabled": True,
            "priority": 120,
            "source_entries": ["MemoryCompanion 历史对话导入"],
        }

    async def stage_historical_relationship_observations(
        self,
        *,
        user_id: str,
        user_name: str,
        batch_id: str,
        observations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        plugin = self._owner._plugin
        normalized_user_id = _single_line(user_id, 80)
        normalized_batch_id = _single_line(batch_id, 120)
        if not normalized_user_id or not normalized_batch_id:
            return {"staged": 0, "reason": "missing_identity_or_batch"}
        staged = 0
        async with plugin._data_lock:
            profiles = plugin.data.setdefault("worldbook_member_profiles", {})
            if not isinstance(profiles, dict):
                profiles = {}
                plugin.data["worldbook_member_profiles"] = profiles
            profile = profiles.get(normalized_user_id)
            if not isinstance(profile, dict):
                profile = self._new_historical_member_profile(normalized_user_id, user_name)
                profiles[normalized_user_id] = profile
            pending = profile.setdefault("pending_observations", [])
            if not isinstance(pending, list):
                pending = []
                profile["pending_observations"] = pending
            existing_keys = {
                (
                    _single_line(item.get("import_batch_id"), 120),
                    _single_line(item.get("content"), 500),
                )
                for item in pending
                if isinstance(item, dict)
            }
            # 调用方按置信度排好优先级；只接收容量内的候选，避免后部低优先候选
            # 因 insert(0) 反而挤掉前部高优先候选。
            for raw in observations[:WORLDBOOK_PENDING_OBSERVATION_CAPACITY]:
                if not isinstance(raw, dict):
                    continue
                content = _single_line(raw.get("content"), 500)
                if not content or (normalized_batch_id, content) in existing_keys:
                    continue
                try:
                    confidence = max(0.0, min(1.0, float(raw.get("confidence") or 0.6)))
                except Exception:
                    confidence = 0.6
                pending.insert(
                    0,
                    {
                        "id": hashlib.sha1(
                            f"{normalized_batch_id}|{content}".encode("utf-8", errors="ignore")
                        ).hexdigest()[:12],
                        "title": _single_line(raw.get("title"), 80) or "历史对话关系观察",
                        "content": content,
                        "evidence": _single_line("；".join(raw.get("source_message_ids") or raw.get("segment_ids") or []), 500),
                        "source_event_ids": [
                            _single_line(item, 120)
                            for item in (raw.get("source_message_ids") or [])
                            if _single_line(item, 120)
                        ][:16],
                        "source": "memory_companion_historical_chat",
                        "import_batch_id": normalized_batch_id,
                        "observed_at": _single_line(raw.get("observed_at"), 80),
                        "weight": max(35, min(95, int(round(confidence * 100)))),
                        "confidence": confidence,
                        "count": 1,
                        "created_at": time.time(),
                        "updated_at": time.time(),
                    },
                )
                existing_keys.add((normalized_batch_id, content))
                staged += 1
            # 历史批次只保留容量内的候选，但不能为了导入历史而删除原有的普通待确认观察。
            # 新历史观察放在前面便于审核；超过上限时只裁掉历史来源自身。
            ordinary_pending: list[dict[str, Any]] = []
            historical_pending: list[dict[str, Any]] = []
            historical_count = 0
            for item in pending:
                if not isinstance(item, dict):
                    continue
                if _single_line(item.get("source"), 80) == "memory_companion_historical_chat":
                    historical_count += 1
                    if historical_count > WORLDBOOK_PENDING_OBSERVATION_CAPACITY:
                        continue
                    historical_pending.append(item)
                    continue
                ordinary_pending.append(item)
            # 普通实时观察先展示；历史观察随后逐条审核，不会把既有候选挤出页面。
            profile["pending_observations"] = ordinary_pending + historical_pending
            if staged:
                profile["last_pending_observation_at"] = time.time()
                plugin._save_data_sync(sections={"worldbook_member_profiles"})
        return {"staged": staged, "batch_id": normalized_batch_id}

    async def rebind_historical_relationship_observations(
        self,
        *,
        batch_id: str,
        old_user_id: str,
        user_id: str,
        user_name: str = "",
    ) -> dict[str, Any]:
        """Move one imported batch of traceable pending and confirmed relationship observations."""
        plugin = self._owner._plugin
        normalized_batch_id = _single_line(batch_id, 120)
        normalized_old_user_id = _single_line(old_user_id, 80)
        normalized_user_id = _single_line(user_id, 80)
        base_result = {
            "batch_id": normalized_batch_id,
            "old_user_id": normalized_old_user_id,
            "user_id": normalized_user_id,
            "matched": 0,
            "moved": 0,
            "deduplicated": 0,
            "trimmed": 0,
            "target_batch_count": 0,
            "confirmed_matched": 0,
            "confirmed_moved": 0,
            "confirmed_deduplicated": 0,
            "confirmed_trimmed": 0,
            "target_confirmed_batch_count": 0,
            "untraceable_confirmed": 0,
        }
        if not normalized_batch_id or not normalized_old_user_id or not normalized_user_id:
            return {**base_result, "reason": "missing_identity_or_batch"}
        if normalized_old_user_id == normalized_user_id:
            return {**base_result, "reason": "same_identity"}

        def is_historical(item: Any) -> bool:
            return (
                isinstance(item, dict)
                and _single_line(item.get("source"), 80) == "memory_companion_historical_chat"
            )

        def observation_key(item: dict[str, Any]) -> tuple[str, str, str]:
            item_batch_id = _single_line(item.get("import_batch_id"), 120)
            content = _single_line(item.get("content"), 500)
            if content:
                return item_batch_id, "content", content
            return item_batch_id, "id", _single_line(item.get("id"), 120)

        def transfer_batch_items(
            source_items: list[Any],
            target_items: list[Any],
            *,
            available_slots: int,
        ) -> tuple[list[Any], list[Any], int, int, int, int]:
            """Append this batch without rewriting target data or dropping deferred source data."""
            retained_source: list[Any] = []
            updated_target = deepcopy(target_items)
            target_batch_keys = {
                observation_key(item)
                for item in target_items
                if is_historical(item)
                and _single_line(item.get("import_batch_id"), 120) == normalized_batch_id
            }
            matched = 0
            moved = 0
            deduplicated = 0
            deferred = 0
            slots = max(0, int(available_slots))

            for item in source_items:
                if not (
                    is_historical(item)
                    and _single_line(item.get("import_batch_id"), 120) == normalized_batch_id
                ):
                    retained_source.append(deepcopy(item))
                    continue

                matched += 1
                key = observation_key(item)
                if key in target_batch_keys:
                    # 目标中已有同批同内容，源端副本可以安全移除。
                    deduplicated += 1
                    continue
                if moved < slots:
                    updated_target.append(deepcopy(item))
                    target_batch_keys.add(key)
                    moved += 1
                    continue

                # `trimmed` 是既有返回字段；这里表示延期迁入，记录仍保留在源端。
                retained_source.append(deepcopy(item))
                deferred += 1

            return (
                retained_source,
                updated_target,
                matched,
                moved,
                deduplicated,
                deferred,
            )

        async with plugin._data_lock:
            profiles = plugin.data.get("worldbook_member_profiles")
            if not isinstance(profiles, dict):
                return {**base_result, "reason": "source_profile_not_found"}
            original_source = profiles.get(normalized_old_user_id)
            if not isinstance(original_source, dict):
                return {**base_result, "reason": "source_profile_not_found"}
            source_pending = original_source.get("pending_observations")
            if not isinstance(source_pending, list):
                source_pending = []
            source_important = original_source.get("important_memories")
            if not isinstance(source_important, list):
                source_important = []

            pending_match_count = sum(
                1
                for item in source_pending
                if is_historical(item)
                and _single_line(item.get("import_batch_id"), 120) == normalized_batch_id
            )
            confirmed_match_count = sum(
                1
                for item in source_important
                if is_historical(item)
                and _single_line(item.get("import_batch_id"), 120) == normalized_batch_id
            )
            untraceable_confirmed = sum(
                1
                for item in source_important
                if is_historical(item) and not _single_line(item.get("import_batch_id"), 120)
            )
            if not pending_match_count and not confirmed_match_count:
                return {
                    **base_result,
                    "untraceable_confirmed": untraceable_confirmed,
                    "reason": "batch_not_found",
                }

            source_profile = deepcopy(original_source)

            target_had_entry = normalized_user_id in profiles
            original_target = profiles.get(normalized_user_id)
            target_profile = (
                deepcopy(original_target)
                if isinstance(original_target, dict)
                else self._new_historical_member_profile(normalized_user_id, user_name)
            )
            target_pending = target_profile.get("pending_observations")
            if not isinstance(target_pending, list):
                target_pending = []

            existing_historical_count = sum(1 for item in target_pending if is_historical(item))
            (
                retained_source_pending,
                updated_target_pending,
                matched,
                moved,
                duplicate_count,
                trimmed,
            ) = transfer_batch_items(
                source_pending,
                target_pending,
                available_slots=(
                    WORLDBOOK_PENDING_OBSERVATION_CAPACITY - existing_historical_count
                ),
            )
            if pending_match_count:
                source_profile["pending_observations"] = retained_source_pending
                target_profile["pending_observations"] = updated_target_pending
                if moved:
                    target_profile["last_pending_observation_at"] = time.time()

            target_important = target_profile.get("important_memories")
            if not isinstance(target_important, list):
                target_important = []
            (
                retained_source_important,
                updated_target_important,
                confirmed_matched,
                confirmed_moved,
                confirmed_duplicate_count,
                confirmed_trimmed,
            ) = transfer_batch_items(
                source_important,
                target_important,
                available_slots=(
                    WORLDBOOK_IMPORTANT_MEMORY_CAPACITY - len(target_important)
                ),
            )
            if confirmed_match_count:
                source_profile["important_memories"] = retained_source_important
                target_profile["important_memories"] = updated_target_important

            profiles[normalized_old_user_id] = source_profile
            profiles[normalized_user_id] = target_profile
            try:
                plugin._save_data_sync(sections={"worldbook_member_profiles"})
            except Exception:
                profiles[normalized_old_user_id] = original_source
                if target_had_entry:
                    profiles[normalized_user_id] = original_target
                else:
                    profiles.pop(normalized_user_id, None)
                raise

        return {
            **base_result,
            "matched": matched,
            "moved": moved,
            "deduplicated": duplicate_count,
            "trimmed": trimmed,
            "target_batch_count": sum(
                1
                for item in updated_target_pending
                if is_historical(item)
                and _single_line(item.get("import_batch_id"), 120) == normalized_batch_id
            ),
            "confirmed_matched": confirmed_matched,
            "confirmed_moved": confirmed_moved,
            "confirmed_deduplicated": confirmed_duplicate_count,
            "confirmed_trimmed": confirmed_trimmed,
            "target_confirmed_batch_count": sum(
                1
                for item in updated_target_important
                if is_historical(item)
                and _single_line(item.get("import_batch_id"), 120) == normalized_batch_id
            ),
            "untraceable_confirmed": untraceable_confirmed,
        }

    async def rollback_historical_relationship_observations(self, batch_id: str) -> dict[str, Any]:
        plugin = self._owner._plugin
        normalized_batch_id = _single_line(batch_id, 120)
        removed = 0
        if not normalized_batch_id:
            return {"removed": 0}
        async with plugin._data_lock:
            profiles = plugin.data.get("worldbook_member_profiles")
            if not isinstance(profiles, dict):
                return {"removed": 0}
            for profile in profiles.values():
                if not isinstance(profile, dict):
                    continue
                pending = profile.get("pending_observations")
                if not isinstance(pending, list):
                    continue
                kept = [
                    item
                    for item in pending
                    if not isinstance(item, dict)
                    or _single_line(item.get("import_batch_id"), 120) != normalized_batch_id
                ]
                removed += len(pending) - len(kept)
                profile["pending_observations"] = kept
            if removed:
                plugin._save_data_sync(sections={"worldbook_member_profiles"})
        return {"removed": removed, "batch_id": normalized_batch_id}
