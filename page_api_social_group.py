# -*- coding: utf-8 -*-
"""social_group。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（10 个方法 + 0 个模块级名字 + 0 个类级赋值 / 402 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import time
from .companion_interaction_expression import current_interaction_projection, normalize_normal_interaction_band_cap
from .relationship_ledger import (
    migrate_legacy_relationship_score,
    migrate_relationship_positive_stage_cap,
    normalize_relationship_positive_stage_cap_key,
)
from .relationship_policy import relationship_stage_for_score
from copy import deepcopy
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class PrivateCompanionPageApiSocialGroupMixin:
    """social_group（从 PrivateCompanionPageApi 拆出）。"""


    def _relationship_intimacy_projection(self, value: int) -> dict[str, Any]:
        policy = (
            getattr(self.plugin, "relationship_stage_policy", None)
            if bool(getattr(self.plugin, "enable_custom_relationship_stage_policy", False))
            else None
        )
        return relationship_stage_for_score(value, policy)

    def _group_wakeup_runtime(self, group: dict[str, Any]) -> dict[str, Any]:
        fatigue = group.get("group_wakeup_fatigue") if isinstance(group.get("group_wakeup_fatigue"), dict) else {}
        high_intensity = {}
        if hasattr(self.plugin, "_group_high_intensity_state"):
            try:
                high_intensity = self.plugin._group_high_intensity_state(group, mutate=False)
            except Exception:
                high_intensity = {}
        value = self._float(fatigue.get("value"))
        limit = self._int(fatigue.get("limit")) or int(getattr(self.plugin, "group_wakeup_fatigue_limit", 5) or 5)
        ratio = max(0.0, min(1.0, value / max(1, limit)))
        if ratio >= 1.0:
            label = "疲劳高"
            level = "high"
        elif ratio >= 0.55:
            label = "有点累"
            level = "medium"
        elif value >= 0.4:
            label = "轻微"
            level = "low"
        else:
            label = "无"
            level = "none"
        return {
            "value": round(value, 2),
            "limit": limit,
            "ratio": round(ratio, 3),
            "label": label,
            "level": level,
            "updated": self.plugin._format_timestamp_elapsed(fatigue.get("updated_ts", 0)),
            "high_intensity": {
                "active": bool(high_intensity.get("active")) if isinstance(high_intensity, dict) else False,
                "merge_active": bool(high_intensity.get("merge_active")) if isinstance(high_intensity, dict) else False,
                "reason": self._single_line(high_intensity.get("reason"), 40) if isinstance(high_intensity, dict) else "",
                "recent_wakeups": self._int(high_intensity.get("recent_wakeups")) if isinstance(high_intensity, dict) else 0,
                "threshold": self._int(high_intensity.get("threshold")) if isinstance(high_intensity, dict) else 0,
                "merge_recent_floor": self._int(high_intensity.get("merge_recent_floor")) if isinstance(high_intensity, dict) else 0,
                "remaining_seconds": self._float(high_intensity.get("remaining_seconds")) if isinstance(high_intensity, dict) else 0.0,
                "merge_seconds": self._float(getattr(self.plugin, "group_high_intensity_merge_seconds", 8)),
                "max_merge_messages": self._int(getattr(self.plugin, "group_high_intensity_max_merge_messages", 8)),
                "merge_scope": self._single_line(getattr(self.plugin, "group_high_intensity_merge_scope", "group"), 20),
            },
        }

    def _group_wakeup_logs(self, group: dict[str, Any], limit: int = 30) -> list[dict[str, Any]]:
        logs = group.get("group_wakeup_logs") if isinstance(group.get("group_wakeup_logs"), list) else []
        items: list[dict[str, Any]] = []
        for raw in reversed(logs[-limit:]):
            if not isinstance(raw, dict):
                continue
            items.append(
                {
                    "ts": self._float(raw.get("ts")),
                    "time": self.plugin._format_timestamp_elapsed(raw.get("ts", 0)),
                    "result": self._single_line(raw.get("result"), 32),
                    "type": self._single_line(raw.get("type"), 40),
                    "word": self._single_line(raw.get("word"), 60),
                    "strength": self._single_line(raw.get("strength"), 24),
                    "strength_label": self._single_line(raw.get("strength_label"), 24),
                    "probability": round(self._float(raw.get("probability")), 3),
                    "score": self._int(raw.get("score")),
                    "threshold": self._int(raw.get("threshold")),
                    "intensity": self._single_line(raw.get("intensity"), 20),
                    "help_type": self._single_line(raw.get("help_type"), 30),
                    "reason": self._single_line(raw.get("reason"), 80),
                    "reason_label": self._single_line(raw.get("reason_label"), 80),
                    "reason_detail": self._single_line(raw.get("reason_detail"), 180),
                    "topic_weight": raw.get("topic_weight") if isinstance(raw.get("topic_weight"), dict) else {},
                    "note": self._single_line(raw.get("note"), 180),
                    "sender_id": self._single_line(raw.get("sender_id"), 40),
                    "sender_name": self._single_line(raw.get("sender_name"), 40),
                    "text": self._display_message_text(raw.get("text"), 160),
                    "fatigue_value": round(self._float(raw.get("fatigue_value")), 2),
                    "fatigue_label": self._single_line(raw.get("fatigue_label"), 20),
                }
            )
        return items

    def _group_slang_items(self, group: dict[str, Any], limit: int = 120) -> list[dict[str, Any]]:
        terms = group.get("slang_terms") if isinstance(group.get("slang_terms"), list) else []
        meanings = group.get("slang_meanings") if isinstance(group.get("slang_meanings"), dict) else {}
        indexed: dict[str, dict[str, Any]] = {}
        for raw in terms:
            if isinstance(raw, dict):
                term = self._single_line(raw.get("term"), 40)
                if not term:
                    continue
                indexed[term] = {
                    "term": term,
                    "count": self._int(raw.get("count")),
                    "last_seen_ts": self._float(raw.get("last_seen")),
                    "last_seen": self.plugin._format_timestamp_elapsed(raw.get("last_seen", 0)),
                    "learned": True,
                }
            else:
                term = self._single_line(raw, 40)
                if term:
                    indexed[term] = {"term": term, "count": 0, "last_seen_ts": 0.0, "last_seen": "", "learned": True}
        for term, raw in meanings.items():
            key = self._single_line(term, 40)
            if key and key not in indexed:
                indexed[key] = {"term": key, "count": 0, "last_seen_ts": 0.0, "last_seen": "", "learned": False}

        uncertain_checker = getattr(self.plugin, "_is_uncertain_group_slang_meaning", None)
        items: list[dict[str, Any]] = []
        for term, base in indexed.items():
            raw_meaning = meanings.get(term) if isinstance(meanings.get(term), dict) else {}
            meaning = self._single_line(raw_meaning.get("meaning"), 120) if isinstance(raw_meaning, dict) else ""
            usage = self._single_line(raw_meaning.get("usage"), 120) if isinstance(raw_meaning, dict) else ""
            confidence = min(1.0, self._float(raw_meaning.get("confidence"))) if isinstance(raw_meaning, dict) else 0.0
            web_match = min(1.0, self._float(raw_meaning.get("web_match"))) if isinstance(raw_meaning, dict) else 0.0
            is_uncertain = True
            if meaning:
                if callable(uncertain_checker):
                    is_uncertain = bool(uncertain_checker(meaning, usage))
                else:
                    is_uncertain = any(marker in f"{meaning} {usage}" for marker in ("不确定", "无法判断", "语境不明", "可能是"))
            if meaning and confidence >= 0.55 and not is_uncertain:
                status = "injectable"
                status_label = "会注入"
            elif meaning and confidence < 0.55:
                status = "low_confidence"
                status_label = "低置信度"
            elif meaning and is_uncertain:
                status = "uncertain"
                status_label = "释义不足"
            else:
                status = "pending"
                status_label = "尚未释义"
            items.append(
                {
                    **base,
                    "meaning": meaning,
                    "usage": usage,
                    "type": self._single_line(raw_meaning.get("type"), 24) if isinstance(raw_meaning, dict) else "",
                    "not_owner": self._single_line(raw_meaning.get("not_owner"), 90) if isinstance(raw_meaning, dict) else "",
                    "evidence": self._single_line(raw_meaning.get("evidence"), 160) if isinstance(raw_meaning, dict) else "",
                    "source": self._single_line(raw_meaning.get("source"), 32) if isinstance(raw_meaning, dict) else "",
                    "updated_at": self._single_line(raw_meaning.get("updated_at"), 32) if isinstance(raw_meaning, dict) else "",
                    "confidence": round(confidence, 2),
                    "web_match": round(web_match, 2),
                    "web_evidence": self._single_line(raw_meaning.get("web_evidence"), 220) if isinstance(raw_meaning, dict) else "",
                    "status": status,
                    "status_label": status_label,
                }
            )
        items.sort(
            key=lambda item: (
                item.get("status") != "injectable",
                -self._int(item.get("count")),
                -self._float(item.get("last_seen_ts")),
                item.get("term") or "",
            )
        )
        return items[:limit]

    def _group_topic_thread_items(self, group: dict[str, Any], limit: int = 16) -> list[dict[str, Any]]:
        threads = group.get("topic_threads") if isinstance(group.get("topic_threads"), list) else []
        members = group.get("members") if isinstance(group.get("members"), dict) else {}
        now = time.time()
        items: list[dict[str, Any]] = []
        for index, raw in enumerate(threads[:limit]):
            if not isinstance(raw, dict):
                continue
            started_ts = self._float(raw.get("started_ts"))
            last_ts = self._float(raw.get("last_ts"))
            duration_seconds = max(0.0, (last_ts or started_ts) - started_ts) if started_ts else 0.0
            participants = raw.get("participants") if isinstance(raw.get("participants"), list) else []
            participant_items = []
            for user_id in participants[:8]:
                uid = self._single_line(user_id, 40)
                member = members.get(uid) if isinstance(members.get(uid), dict) else {}
                name = self._single_line(
                    member.get("identity_name")
                    or member.get("display_name")
                    or member.get("nickname")
                    or member.get("name")
                    or member.get("card")
                    or uid,
                    24,
                )
                if uid:
                    participant_items.append({"id": uid, "name": name or uid})
            examples = []
            for example in (raw.get("recent_examples") if isinstance(raw.get("recent_examples"), list) else [])[-4:]:
                if not isinstance(example, dict):
                    continue
                example_sender_id = self._single_line(example.get("sender_id") or example.get("user_id"), 40)
                example_member = members.get(example_sender_id) if isinstance(members.get(example_sender_id), dict) else {}
                examples.append(
                    {
                        "name": self._single_line(
                            example_member.get("identity_name") or example.get("name"),
                            24,
                        ),
                        "text": self._single_line(example.get("text"), 120),
                        "time": self.plugin._format_timestamp_elapsed(example.get("ts", 0)),
                    }
                )
            message_count = self._int(raw.get("message_count"))
            freshness = max(0.0, now - last_ts) if last_ts else 0.0
            heat = min(100, max(8, message_count * 10 + len(participant_items) * 8 - int(freshness / 600) * 5))
            status = "活跃" if freshness <= 15 * 60 else "刚冷却" if freshness <= 90 * 60 else "历史"
            title = self._single_line(raw.get("title") or raw.get("topic") or raw.get("summary"), 80)
            items.append(
                {
                    "rank": index + 1,
                    "title": title or "未命名话题",
                    "summary": self._single_line(raw.get("summary"), 180),
                    "message_count": message_count,
                    "participant_count": len(participants),
                    "participants": participant_items,
                    "recent_examples": examples,
                    "started": self.plugin._format_timestamp_elapsed(started_ts),
                    "last_seen": self.plugin._format_timestamp_elapsed(last_ts),
                    "duration": self._format_duration(duration_seconds),
                    "heat": heat,
                    "status": status,
                    "bot_joined": bool(raw.get("bot_joined")),
                }
            )
        return items

    def _relationship_profile_targets(self) -> list[tuple[str, dict[str, Any]]]:
        targets: list[tuple[str, dict[str, Any]]] = []
        seen: set[int] = set()
        seen_profile_ids: set[str] = set()

        def add(profile_id: str, profile: Any) -> None:
            if (
                isinstance(profile, dict)
                and id(profile) not in seen
                and profile_id not in seen_profile_ids
            ):
                targets.append((profile_id, profile))
                seen.add(id(profile))
                seen_profile_ids.add(profile_id)

        add("", getattr(self.plugin, "_data_default", None))
        if bool(getattr(self.plugin, "enable_multi_persona_mode", False)):
            id_getter = getattr(self.plugin, "_persona_profile_ids", None)
            ensure_profile = getattr(self.plugin, "_ensure_persona_profile", None)
            persona_ids = id_getter() if callable(id_getter) else []
            if callable(ensure_profile):
                for persona_id in persona_ids:
                    clean_id = str(persona_id or "").strip()
                    if clean_id:
                        add(clean_id, ensure_profile(clean_id))
        else:
            add("", getattr(self.plugin, "data", None))
        return targets

    def _save_relationship_profile_target(self, profile_id: str, profile: dict[str, Any]) -> None:
        snapshot = deepcopy(profile)
        if profile_id:
            saver = getattr(self.plugin, "_save_persona_profile_sync", None)
            if not callable(saver):
                raise RuntimeError(f"人格 {profile_id} 缺少保存接口")
            saver(profile_id, snapshot)
            return
        writer = getattr(self.plugin, "_write_data_snapshot_sync", None)
        if not callable(writer):
            raise RuntimeError("默认资料缺少快照保存接口")
        writer(snapshot)

    async def _apply_relationship_profile_config_batch(self, overrides: dict[str, Any]) -> None:
        flush = getattr(self.plugin, "_flush_scheduled_data_save", None)
        if callable(flush):
            await flush()
        cap_change = overrides.get("__relationship_positive_cap_change")
        interaction_change = overrides.get("__relationship_interaction_cap_change")
        now = time.time()
        async with self.plugin._data_lock:
            targets = self._relationship_profile_targets()
            snapshots = {profile_id: deepcopy(profile) for profile_id, profile in targets}
            touched: list[tuple[str, dict[str, Any]]] = []
            attempted: list[str] = []
            try:
                for profile_id, profile in targets:
                    users = profile.get("users") if isinstance(profile.get("users"), dict) else {}
                    profile_changed = False
                    for user in users.values():
                        if not isinstance(user, dict):
                            continue
                        score_result = migrate_legacy_relationship_score(user, created=False, now=now)
                        profile_changed = profile_changed or bool(score_result.get("changed"))
                        if isinstance(cap_change, tuple) and len(cap_change) == 2:
                            previous_cap = user.get("relationship_positive_stage_cap_key")
                            cap_result = migrate_relationship_positive_stage_cap(
                                user,
                                old_cap_key=cap_change[0],
                                new_cap_key=cap_change[1],
                                now=now,
                            )
                            profile_changed = profile_changed or previous_cap != cap_change[1] or bool(cap_result.get("changed"))
                        if isinstance(interaction_change, tuple) and len(interaction_change) == 2:
                            normalized = normalize_normal_interaction_band_cap(interaction_change[1])
                            before = deepcopy(user.get("current_interaction"))
                            previous_cap = user.get("normal_interaction_band_cap")
                            user["current_interaction"] = current_interaction_projection(
                                before,
                                relationship_role=user.get("relationship_role"),
                                relationship_mode=user.get("relationship_mode"),
                                relationship_score=user.get("relationship_score"),
                                normal_interaction_band_cap=normalized,
                                now=now,
                            )
                            user["normal_interaction_band_cap"] = normalized
                            profile_changed = profile_changed or previous_cap != normalized or before != user["current_interaction"]
                    if profile_changed:
                        touched.append((profile_id, profile))

                for profile_id, profile in touched:
                    attempted.append(profile_id)
                    self._save_relationship_profile_target(profile_id, profile)
            except Exception as exc:
                rollback_errors: list[str] = []
                for profile_id, profile in targets:
                    profile.clear()
                    profile.update(deepcopy(snapshots[profile_id]))
                for profile_id in reversed(attempted):
                    try:
                        self._save_relationship_profile_target(profile_id, snapshots[profile_id])
                    except Exception as rollback_exc:
                        rollback_errors.append(
                            f"{profile_id or 'default'}: {self._single_line(rollback_exc, 160)}"
                        )
                        logger.error(
                            "关系配置人格资料回滚失败: persona=%s error=%s",
                            profile_id or "default",
                            self._single_line(rollback_exc, 160),
                        )
                if rollback_errors:
                    raise RuntimeError(
                        "关系配置人格资料保存失败，且回滚未完整完成: "
                        + "; ".join(rollback_errors)
                    ) from exc
                raise
        overrides["__relationship_profile_transaction"] = {
            "targets": targets,
            "snapshots": snapshots,
            "persisted_profile_ids": [profile_id for profile_id, _profile in touched],
        }
        overrides["__relationship_data_changed"] = False

    def _restore_relationship_config_values(self, overrides: dict[str, Any]) -> None:
        cap_change = overrides.get("__relationship_positive_cap_change")
        if isinstance(cap_change, tuple) and len(cap_change) == 2:
            old_cap = normalize_relationship_positive_stage_cap_key(cap_change[0])
            self._set_config_value("relationship_positive_stage_cap_key", old_cap)
            self.plugin.relationship_positive_stage_cap_key = old_cap
        interaction_change = overrides.get("__relationship_interaction_cap_change")
        if isinstance(interaction_change, tuple) and len(interaction_change) == 2:
            old_interaction_cap = normalize_normal_interaction_band_cap(interaction_change[0])
            self._set_config_value("normal_interaction_band_cap", old_interaction_cap)
            self.plugin.normal_interaction_band_cap = old_interaction_cap

    async def _rollback_relationship_config_transaction(self, overrides: dict[str, Any]) -> None:
        transaction = overrides.pop("__relationship_profile_transaction", None)
        profile_rollback_errors: list[str] = []
        if isinstance(transaction, dict):
            targets = transaction.get("targets")
            snapshots = transaction.get("snapshots")
            persisted_profile_ids = transaction.get("persisted_profile_ids")
            if isinstance(targets, list) and isinstance(snapshots, dict):
                async with self.plugin._data_lock:
                    for profile_id, profile in targets:
                        snapshot = snapshots.get(profile_id)
                        if isinstance(profile, dict) and isinstance(snapshot, dict):
                            profile.clear()
                            profile.update(deepcopy(snapshot))
                    if isinstance(persisted_profile_ids, list):
                        for profile_id in reversed(persisted_profile_ids):
                            snapshot = snapshots.get(profile_id)
                            if not isinstance(snapshot, dict):
                                continue
                            try:
                                self._save_relationship_profile_target(profile_id, snapshot)
                            except Exception as exc:
                                profile_rollback_errors.append(
                                    f"{profile_id or 'default'}: {self._single_line(exc, 160)}"
                                )
                                logger.error(
                                    "配置保存失败后人格资料回滚失败: persona=%s error=%s",
                                    profile_id or "default",
                                    self._single_line(exc, 160),
                                )
        self._restore_relationship_config_values(overrides)
        try:
            rollback_config_saved = await self._save_config_if_possible()
        except Exception as exc:
            rollback_config_saved = False
            logger.error(
                "关系配置回滚后旧配置重新保存失败: %s",
                self._single_line(exc, 160),
            )
        if not rollback_config_saved:
            logger.warning("关系配置已恢复到运行态，但旧配置未能重新保存")
        if profile_rollback_errors:
            raise RuntimeError(
                "配置保存失败，且人格资料回滚未完整完成: "
                + "; ".join(profile_rollback_errors)
            )
