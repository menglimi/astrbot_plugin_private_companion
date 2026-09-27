# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiProactivePart03Mixin。

由 tools/split_mixin_domain.py 从 page_api_proactive.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 408 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiProactiveMixin）。
"""
from __future__ import annotations

from .page_api_proactive_shared import logger
from .page_api_proactive_shared import Any
from .page_api_proactive_shared import asyncio
from .page_api_proactive_shared import datetime
from .page_api_proactive_shared import deepcopy
from .page_api_proactive_shared import time
from .page_api_proactive_shared import timedelta



class PrivateCompanionPageApiProactivePart03Mixin:
    """PrivateCompanionPageApiProactivePart03Mixin（从 PrivateCompanionPageApiProactiveMixin 拆出）。"""


    def _proactive_candidate_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        raw = data.get("proactive_candidate_pool") if isinstance(data.get("proactive_candidate_pool"), list) else []
        users = data.get("users") if isinstance(data.get("users"), dict) else {}
        now = time.time()
        converter = getattr(self.plugin, "_environment_fromtimestamp", None)
        try:
            current_dt = converter(now) if callable(converter) else datetime.fromtimestamp(now)
        except Exception:
            current_dt = datetime.fromtimestamp(now)
        today_start = current_dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        tomorrow_start = (current_dt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp()
        today_key = current_dt.strftime("%Y-%m-%d")
        buckets: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}
        user_counts: dict[str, dict[str, Any]] = {}
        total_attempts = 0
        pending_total = 0
        pool_record_total = len([item for item in raw if isinstance(item, dict)])
        today_record_total = 0
        today_merge_trigger_count = 0
        today_blocked_record_total = 0

        def pending_status(status: Any) -> bool:
            normalized = self._single_line(status, 24).lower()
            return normalized in {"accepted", "deferred", "queued", "pending", "unknown", ""}

        def repeat_limit(status: Any) -> int:
            normalized = self._single_line(status, 24).lower()
            if normalized in {"accepted", "deferred", "queued", "pending", "unknown", ""}:
                return 12
            if normalized == "sent":
                return 8
            return 6

        def normalized_repeat(item: dict[str, Any], status: str) -> int:
            count = max(1, self._int(item.get("repeat_count")))
            limit = repeat_limit(status)
            value = max(1, min(limit, count))
            if count != value:
                item["repeat_count"] = value
                item["repeat_count_capped"] = True
            return value

        def candidate_user_meta(user_id: str, user: Any) -> dict[str, str]:
            if not isinstance(user, dict):
                return {"label": user_id or "未知用户", "role": "unknown", "role_label": "未知"}
            role = self.plugin._private_user_role(user, user_id) if hasattr(self.plugin, "_private_user_role") else ""
            role_labeler = getattr(self.plugin, "_private_user_role_label", None)
            role_label = role_labeler(role) if callable(role_labeler) else ("主要用户" if role == "owner" else "次要用户")
            nickname = self._single_line(user.get("nickname"), 40)
            generic_names = {"用户", "主人", "主要用户", "默认用户"}
            if str(user_id).isdigit():
                label = nickname if nickname and nickname not in generic_names else user_id
            else:
                umo = self._single_line(
                    user.get("umo") or user.get("last_umo") or user.get("last_unified_msg_origin"),
                    180,
                )
                profile_getter = getattr(self.plugin, "_platform_profile", None)
                try:
                    platform_profile = profile_getter(umo=umo) if callable(profile_getter) else {}
                except Exception:
                    platform_profile = {}
                platform_kind = self._single_line((platform_profile or {}).get("kind"), 40)
                if platform_kind == "qq_official":
                    label = nickname if nickname and nickname not in generic_names else f"QQ 官方 · {str(user_id)[:8]}"
                else:
                    label = nickname if nickname and nickname not in generic_names else f"临时会话 · {str(user_id)[:8]}"
            return {"label": label or user_id or "未知用户", "role": role or "friend", "role_label": role_label}

        for item in raw:
            if not isinstance(item, dict):
                continue
            status = self._single_line(item.get("status"), 24) or "unknown"
            repeat_count = normalized_repeat(item, status)
            note = self._single_line(item.get("note"), 160)
            created_ts = self._float(item.get("created_ts"))
            status_ts = self._float(item.get("updated_ts")) or created_ts
            created_today = today_start <= created_ts < tomorrow_start
            if created_today:
                today_record_total += 1
            merged_by_day = item.get("merged_by_day") if isinstance(item.get("merged_by_day"), dict) else {}
            if today_key in merged_by_day:
                today_merge_trigger_count += self._int(merged_by_day.get(today_key))
            elif created_today:
                # 兼容升级前只有 repeat_count、没有逐日合并计数的候选。
                today_merge_trigger_count += max(0, repeat_count - 1)
            if status == "blocked" and today_start <= status_ts < tomorrow_start:
                today_blocked_record_total += 1
            if status == "blocked" and note in {"朋友关系不接收敏感主动", "次要用户关系不接收敏感主动"}:
                continue
            user_id = self._single_line(item.get("user_id"), 128)
            user = users.get(user_id) if isinstance(users, dict) else None
            reason_raw = self._single_line(item.get("reason"), 40)
            action_raw = self._single_line(item.get("action"), 40)
            if (
                isinstance(user, dict)
                and hasattr(self.plugin, "_friend_can_receive_proactive_reason")
                and not self.plugin._friend_can_receive_proactive_reason(user, reason_raw, action_raw)
            ):
                continue
            if status != "sent" and not bool(
                isinstance(user, dict)
                and getattr(self.plugin, "_user_enabled_for_proactive", lambda uid, profile: bool(profile and profile.get("enabled", True)))(
                    user_id,
                    user,
                )
            ):
                continue
            total_attempts += repeat_count
            if pending_status(status):
                pending_total += repeat_count
            user_meta = candidate_user_meta(user_id, user)
            user_bucket = user_counts.setdefault(
                user_id or "unknown",
                {
                    "user_id": user_id,
                    "label": user_meta["label"],
                    "role": user_meta["role"],
                    "role_label": user_meta["role_label"],
                    "total": 0,
                    "pending_total": 0,
                    "counts": {},
                },
            )
            user_bucket["total"] = self._int(user_bucket.get("total")) + repeat_count
            if pending_status(status):
                user_bucket["pending_total"] = self._int(user_bucket.get("pending_total")) + repeat_count
            bucket_counts = user_bucket.get("counts")
            if not isinstance(bucket_counts, dict):
                bucket_counts = {}
                user_bucket["counts"] = bucket_counts
            bucket_counts[status] = self._int(bucket_counts.get(status)) + repeat_count
            source = self._single_line(item.get("source"), 40) or "unknown"
            display_source = "bookshelf_reading" if source == "reading_archive" else source
            counts[status] = counts.get(status, 0) + repeat_count
            source_counts[display_source] = source_counts.get(display_source, 0) + repeat_count
            scheduled = self._float(item.get("scheduled_ts"))
            created = created_ts
            last_seen = self._float(item.get("last_seen_ts")) or created
            reason = reason_raw
            action = action_raw
            if reason == "reading_archive_share":
                reason = "bookshelf_reading_share"
            if reason == "reading_archive_recommendation_request":
                reason = "bookshelf_recommendation_request"
            if action == "reading_archive_read":
                action = "bookshelf_reading"
            signature = self._single_line(item.get("signature"), 120)
            topic = self._single_line(item.get("topic"), 100)
            motive = self._single_line(item.get("motive"), 180)
            semantic_kind = self._single_line(item.get("semantic_kind"), 40)
            semantic_anchor_type = self._single_line(item.get("semantic_anchor_type"), 40)
            semantic_score = self._int(item.get("semantic_score"))
            semantic_pressure = self._int(item.get("semantic_pressure"))
            semantic_risk = self._int(item.get("semantic_risk"))
            semantic_note = self._single_line(item.get("semantic_note"), 180)
            need_layer = self._single_line(item.get("semantic_need_layer") or item.get("need_layer"), 40)
            need_drive = self._single_line(item.get("semantic_need_drive") or item.get("need_drive"), 80)
            need_note = self._single_line(item.get("semantic_need_note") or item.get("need_note"), 120)
            need_score_bias = item.get("semantic_need_score_bias", item.get("need_score_bias"))
            need_pressure_bias = item.get("semantic_need_pressure_bias", item.get("need_pressure_bias"))
            sanitizer = getattr(self.plugin, "_sanitize_friend_proactive_plan_fields", None)
            if isinstance(user, dict) and callable(sanitizer):
                sanitized = sanitizer(
                    user,
                    reason=reason,
                    action=action,
                    topic=topic,
                    motive=motive,
                )
                reason = self._single_line(sanitized.get("reason"), 40) or reason
                action = self._single_line(sanitized.get("action"), 40) or action
                topic = self._single_line(sanitized.get("topic"), 100)
                motive = self._single_line(sanitized.get("motive"), 180)
            merged = None
            for existing in reversed(buckets):
                if existing.get("status") != status:
                    continue
                if existing.get("user_id") != user_id:
                    continue
                old_signature = str(existing.get("_signature") or "")
                if signature and old_signature:
                    similar = bool(getattr(self.plugin, "_topic_signature_similar", lambda a, b: a == b)(signature, old_signature))
                else:
                    similar = (topic or motive) == (existing.get("topic") or existing.get("motive"))
                if not similar:
                    continue
                if max(last_seen, scheduled, created) - self._float(existing.get("_first_ts")) > 36 * 3600:
                    continue
                merged = existing
                break
            if merged is None:
                buckets.append(
                    {
                        "id": self._single_line(item.get("id"), 20),
                        "user_id": user_id,
                        "user_label": user_meta["label"],
                        "user_role": user_meta["role"],
                        "user_role_label": user_meta["role_label"],
                        "source": display_source,
                        "source_label": self._proactive_source_label(display_source),
                        "source_note": self._proactive_source_note(display_source),
                        "reason": reason,
                        "reason_label": self._proactive_reason_label(reason, target_name=user_meta["label"]),
                        "reason_detail": self._proactive_reason_detail(
                            reason=reason,
                            source=display_source,
                            topic=topic,
                            motive=motive,
                            note=note,
                            target_name=user_meta["label"],
                        ),
                        "action": action,
                        "topic": topic,
                        "motive": motive,
                        "score": self._int(item.get("score")),
                        "semantic_kind": semantic_kind,
                        "semantic_anchor_type": semantic_anchor_type,
                        "semantic_score": semantic_score,
                        "semantic_pressure": semantic_pressure,
                        "semantic_risk": semantic_risk,
                        "semantic_note": semantic_note,
                        "need_layer": need_layer,
                        "need_level": need_layer,
                        "need_drive": need_drive,
                        "need_note": need_note,
                        "need_score_bias": need_score_bias,
                        "need_pressure_bias": need_pressure_bias,
                        "status": status,
                        "note": note,
                        "repeat_count": repeat_count,
                        "created_ts": created,
                        "last_seen_ts": last_seen,
                        "scheduled_ts": scheduled,
                        "is_due": bool(scheduled and scheduled <= now),
                        "_signature": signature,
                        "_first_ts": max(created, scheduled, last_seen),
                    }
                )
                continue
            previous_repeat = self._int(merged.get("repeat_count"))
            merged_repeat_limit = repeat_limit(status)
            merged["repeat_count"] = min(merged_repeat_limit, previous_repeat + repeat_count)
            if previous_repeat + repeat_count > merged_repeat_limit:
                merged["repeat_count_capped"] = True
            merged["last_seen_ts"] = max(self._float(merged.get("last_seen_ts")), last_seen, created)
            merged["scheduled_ts"] = max(self._float(merged.get("scheduled_ts")), scheduled)
            merged["score"] = max(self._int(merged.get("score")), self._int(item.get("score")))
            if semantic_score >= self._int(merged.get("semantic_score")):
                merged["semantic_kind"] = semantic_kind
                merged["semantic_anchor_type"] = semantic_anchor_type
                merged["semantic_score"] = semantic_score
                merged["semantic_pressure"] = semantic_pressure
                merged["semantic_risk"] = semantic_risk
                merged["semantic_note"] = semantic_note
                merged["need_layer"] = need_layer
                merged["need_level"] = need_layer
                merged["need_drive"] = need_drive
                merged["need_note"] = need_note
                merged["need_score_bias"] = need_score_bias
                merged["need_pressure_bias"] = need_pressure_bias
            if topic:
                merged["topic"] = topic
            if motive:
                merged["motive"] = motive
            if note and note != merged.get("note"):
                merged["note"] = "多来源合并"
            merged["is_due"] = bool(merged.get("scheduled_ts") and self._float(merged.get("scheduled_ts")) <= now)
        items: list[dict[str, Any]] = []
        for item in buckets:
            created = self._float(item.get("created_ts"))
            last_seen = self._float(item.get("last_seen_ts")) or created
            scheduled = self._float(item.get("scheduled_ts"))
            item.pop("_signature", None)
            item.pop("_first_ts", None)
            item["created"] = self.plugin._format_timestamp_elapsed(created)
            item["last_seen"] = self.plugin._format_timestamp_elapsed(last_seen)
            item["scheduled"] = self.plugin._format_timestamp_elapsed(scheduled)
            items.append(item)
        items.sort(key=lambda item: item.get("last_seen_ts") or item.get("scheduled_ts") or 0, reverse=True)
        display_limit = 60
        displayed_items = items[:display_limit]
        return {
            "total": total_attempts,
            "pending_total": pending_total,
            "record_total": pool_record_total,
            "pool_record_total": pool_record_total,
            "today_record_total": today_record_total,
            "today_merge_trigger_count": today_merge_trigger_count,
            "today_blocked_record_total": today_blocked_record_total,
            "visible_total": len(items),
            "list_total": len(items),
            "list_displayed_total": len(displayed_items),
            "list_limit": display_limit,
            "list_truncated": len(items) > len(displayed_items),
            "counts": counts,
            "source_counts": source_counts,
            "source_labels": {key: self._proactive_source_label(key) for key in source_counts},
            "users": sorted(
                user_counts.values(),
                key=lambda item: (self._int(item.get("total")), self._single_line(item.get("label"), 40)),
                reverse=True,
            ),
            "items": displayed_items,
        }

    def _proactive_motivation_runtime_summary(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}

        def part(raw: Any) -> dict[str, Any]:
            if not isinstance(raw, dict):
                return {}
            result: dict[str, Any] = {
                "score": round(self._float(raw.get("score")), 3),
                "label": self._single_line(raw.get("label"), 60),
                "detail": self._single_line(raw.get("detail"), 180),
            }
            if "level" in raw:
                result["level"] = round(self._float(raw.get("level")), 3)
            return result

        return {
            "score": round(self._float(value.get("score")), 3),
            "label": self._single_line(value.get("label"), 60),
            "detail": self._single_line(value.get("detail"), 220),
            "drive": part(value.get("drive")),
            "temperature": part(value.get("temperature")),
            "incentive": part(value.get("incentive")),
            "arousal": part(value.get("arousal")),
        }

    async def _proactive_task_summary_async(
        self,
        data: dict[str, Any],
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Build the SQLite-backed summary off-loop with one shared flight."""

        now = time.monotonic()
        if force_refresh:
            self._proactive_task_summary_generation += 1
        if (
            not force_refresh
            and self._proactive_task_summary_cache_ready
            and now - self._proactive_task_summary_cache_at
            < max(
                1.0,
                float(self.TROUBLESHOOTING_PROACTIVE_SUMMARY_CACHE_SECONDS),
            )
        ):
            return deepcopy(self._proactive_task_summary_cache)

        task = None if force_refresh else self._proactive_task_summary_task
        if task is None or task.done():
            snapshot = deepcopy(data)
            generation = self._proactive_task_summary_generation

            async def compute() -> dict[str, Any]:
                try:
                    result = await asyncio.to_thread(
                        self._build_troubleshooting_proactive_summary,
                        snapshot,
                    )
                    if not isinstance(result, dict):
                        raise TypeError("proactive task summary returned a non-object")
                except Exception as exc:
                    logger.warning(
                        "[PrivateCompanionPage] 主动任务摘要已降级: error_type=%s",
                        type(exc).__name__,
                        exc_info=True,
                    )
                    fallback = (
                        deepcopy(self._proactive_task_summary_cache)
                        if self._proactive_task_summary_cache_ready
                        else {
                            "total": 0,
                            "pending_total": 0,
                            "items": [],
                            "users": [],
                        }
                    )
                    fallback["degraded"] = True
                    fallback["diagnostic"] = {
                        "code": "proactive_task_summary_unavailable",
                        "error_type": type(exc).__name__,
                        "using_last_good": self._proactive_task_summary_cache_ready,
                    }
                    return fallback
                if generation == self._proactive_task_summary_generation:
                    self._proactive_task_summary_cache = deepcopy(result)
                    self._proactive_task_summary_cache_at = time.monotonic()
                    self._proactive_task_summary_cache_ready = True
                return result

            task = asyncio.create_task(
                compute(),
                name="private-companion-proactive-task-summary",
            )
            self._proactive_task_summary_task = task

            def clear_finished(completed: asyncio.Task[dict[str, Any]]) -> None:
                if self._proactive_task_summary_task is completed:
                    self._proactive_task_summary_task = None

            task.add_done_callback(clear_finished)
        return deepcopy(await asyncio.shield(task))
