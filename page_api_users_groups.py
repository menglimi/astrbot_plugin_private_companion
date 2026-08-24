# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import hashlib
import hmac
import json
import time
import uuid
from copy import deepcopy
from datetime import datetime
from typing import Any

from astrbot.api import logger
from quart import request

from .helpers import _safe_int
from .companion_interaction_expression import allowed_expression_bands, current_interaction_projection
from .emotion_diagnostics import build_emotion_trace_projection, emotion_trace_summary
from .relationship_ledger import (
    normalize_relationship_mode,
    record_manual_relationship_change,
    relationship_positive_score_cap,
)
from .migration_backfill import legacy_pending_reference


class PrivateCompanionPageApiUsersGroupsMixin:
    def _page_unified_person_registry(self) -> Any:
        getter = getattr(self.plugin, "_active_unified_person_registry", None)
        if callable(getter):
            return getter()
        return self.plugin.unified_person_registry

    def _normalize_page_group_id(self, value: Any) -> str:
        normalizer = getattr(self.plugin, "_normalize_group_identity_id", None)
        if callable(normalizer):
            return normalizer(value)
        return self._single_line(value, 160)

    @staticmethod
    def _identity_unlink_confirmation(
        *, person_id: str, operation_id: str, identity: dict[str, Any], checkpoint: dict[str, Any]
    ) -> str:
        """Bind an unlink preview to the exact identity projection revision."""
        payload = {
            "person_id": person_id,
            "operation_id": operation_id,
            "identity": identity,
            "projection_revision": int(checkpoint.get("projection_revision") or 0),
            "checkpoint_hash": str(checkpoint.get("checkpoint_hash") or ""),
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _identity_link_confirmation(
        *, person_id: str, operation_id: str, identity: dict[str, Any], checkpoint: dict[str, Any]
    ) -> str:
        payload = {
            "action": "relink",
            "person_id": person_id,
            "operation_id": operation_id,
            "identity": identity,
            "projection_revision": int(checkpoint.get("projection_revision") or 0),
            "checkpoint_hash": str(checkpoint.get("checkpoint_hash") or ""),
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _safe_identity_unlink_result(result: dict[str, Any]) -> dict[str, Any]:
        """Strip raw identity keys and migration checkpoints from page output."""
        return {
            "ok": bool(result.get("ok")),
            "state": str(result.get("state") or "pending")[:32],
            "code": str(result.get("code") or "identity_unlink_failed")[:80],
            "changed": bool(result.get("changed")),
            "source_event_count": max(0, _safe_int(result.get("source_event_count"), 0)),
            "replayable_event_count": max(0, _safe_int(result.get("replayable_event_count"), 0)),
            "ambiguity_count": max(0, _safe_int(result.get("ambiguity_count"), 0)),
        }

    @staticmethod
    def _safe_person_lifecycle_result(result: dict[str, Any], action: str) -> dict[str, Any]:
        """Expose lifecycle impact without leaking identity or storage keys."""
        safe_action = action if action in {"archive", "purge"} else "archive"
        safe = {
            "ok": bool(result.get("ok")),
            "state": str(result.get("state") or "pending")[:32],
            "code": str(result.get("code") or f"person_{safe_action}_failed")[:80],
            "changed": bool(result.get("changed")),
            "active_identity_count": max(0, _safe_int(result.get("active_identity_count"), 0)),
            "detached_identity_count": max(0, _safe_int(result.get("detached_identity_count"), 0)),
            "group_overlay_count": max(0, _safe_int(result.get("group_overlay_count"), 0)),
            "binding_checkpoint_count": max(0, _safe_int(result.get("binding_checkpoint_count"), 0)),
        }
        token = str(result.get("confirmation_token") or "")
        if len(token) == 64 and re.fullmatch(r"[0-9a-f]{64}", token):
            safe["confirmation_token"] = token
        eligible_at = str(result.get("eligible_at") or "")[:40]
        if eligible_at:
            safe["eligible_at"] = eligible_at
        if safe_action == "archive":
            safe["impact"] = {
                "identity_links": "detach_and_tombstone",
                "scoped_private_and_group_member": "tombstone",
                "relationship_account": "tombstone",
                "group_overlays": "remove",
                "migration_stream_count": 2,
                "automatic_restore_available": False,
                "purge_retention_days": 7,
            }
        else:
            safe["impact"] = {
                "detached_identity_links": "remove",
                "binding_checkpoints": "remove",
                "legacy_exact_records": "remove",
                "retired_migration_streams": "remove",
                "automatic_restore_available": False,
            }
        return safe

    def _identity_domain_summary(
        self, person_id: str, snapshot: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        """Count projected domains without returning group identifiers or data."""
        synchronizer = getattr(self.plugin, "req041_scoped_projection_sync", None)
        builder = getattr(synchronizer, "build_records", None)
        if not callable(builder) or not isinstance(snapshot, dict):
            return {
                kind: {"status": "unavailable", "scope_count": 0, "record_count": 0, "ready_scope_count": 0}
                for kind in ("private", "group_member", "group_shared")
            }
        active_scope = ""
        scope_getter = getattr(self.plugin, "_active_persona_scope", None)
        try:
            active_scope = str(scope_getter() or "") if callable(scope_getter) else ""
        except Exception:
            active_scope = ""
        source_scope = (
            "default" if not active_scope
            else "persona:" + hashlib.sha256(active_scope.encode("utf-8")).hexdigest()[:24]
        )
        try:
            records, contexts = builder(snapshot, source_scope=source_scope)
        except Exception:
            return {
                kind: {"status": "degraded", "scope_count": 0, "record_count": 0, "ready_scope_count": 0}
                for kind in ("private", "group_member", "group_shared")
            }
        member_groups = {
            str(context.group_id)
            for context in contexts
            if getattr(context, "kind", "") == "group_member"
            and getattr(context, "identity_id", "") == person_id
            and str(getattr(context, "group_id", "") or "")
        }
        selected_contexts = {
            "private": [
                context for context in contexts
                if getattr(context, "kind", "") == "private"
                and getattr(context, "identity_id", "") == person_id
            ],
            "group_member": [
                context for context in contexts
                if getattr(context, "kind", "") == "group_member"
                and getattr(context, "identity_id", "") == person_id
            ],
            "group_shared": [
                context for context in contexts
                if getattr(context, "kind", "") == "group_shared"
                and str(getattr(context, "group_id", "") or "") in member_groups
            ],
        }
        result: dict[str, dict[str, Any]] = {}
        for kind, selected in selected_contexts.items():
            scope_keys = {context.cache_scope() for context in selected}
            record_count = sum(
                1 for record in records
                if record.context.cache_scope() in scope_keys
                and (
                    kind == "group_shared"
                    or getattr(record.context, "identity_id", "") == person_id
                )
            )
            ready_count = sum(
                1 for context in selected
                if callable(getattr(synchronizer, "is_ready", None))
                and synchronizer.is_ready(context)
            )
            scope_count = len(scope_keys)
            result[kind] = {
                "status": "ready" if scope_count and ready_count == scope_count else (
                    "reconciling" if scope_count else "empty"
                ),
                "scope_count": scope_count,
                "record_count": record_count,
                "ready_scope_count": ready_count,
            }
        return result

    def _identity_pending_reference(self, user_id: str) -> str:
        coordinator = getattr(self.plugin, "req041_migration_coordinator", None)
        status_reader = getattr(coordinator, "status", None)
        if not callable(status_reader):
            return ""
        try:
            status = status_reader()
        except Exception:
            return ""
        epoch = str(status.get("migration_epoch") or "") if isinstance(status, dict) else ""
        if not epoch:
            return ""
        active_scope = ""
        scope_getter = getattr(self.plugin, "_active_persona_scope", None)
        try:
            active_scope = str(scope_getter() or "") if callable(scope_getter) else ""
        except Exception:
            active_scope = ""
        source_scope = (
            "default" if not active_scope
            else "persona:" + hashlib.sha256(active_scope.encode("utf-8")).hexdigest()[:24]
        )
        return legacy_pending_reference(epoch, source_scope, user_id)

    def _identity_pending_summary(self, user_id: str) -> dict[str, Any]:
        coordinator = getattr(self.plugin, "req041_migration_coordinator", None)
        pending_reader = getattr(coordinator, "pending_status", None)
        if not callable(pending_reader):
            return {"found": False, "state": "unavailable", "reason_code": "migration_unavailable"}
        reference = self._identity_pending_reference(user_id)
        if not reference:
            return {"found": False, "state": "degraded", "reason_code": "pending_lookup_failed"}
        try:
            result = pending_reader(reference)
        except Exception:
            return {"found": False, "state": "degraded", "reason_code": "pending_lookup_failed"}
        return result if isinstance(result, dict) else {
            "found": False, "state": "degraded", "reason_code": "pending_lookup_failed"
        }

    async def update_pending_identity_review(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True)
        if not isinstance(payload, dict):
            return self._error("请求体必须是 JSON 对象")
        user_id = self._single_line(payload.get("user_id"), 160)
        action = self._single_line(payload.get("action"), 24)
        if not user_id or action not in {"dismiss", "restore"}:
            return self._error("user_id 与有效 action 均为必填项")
        async with self.plugin._data_lock:
            users = self.plugin.data.get("users") if isinstance(self.plugin.data, dict) else {}
            user = users.get(user_id) if isinstance(users, dict) else None
            if not isinstance(user, dict):
                return self._error("用户不存在")
            if self._single_line(user.get("unified_person_id"), 80):
                return self._error("该用户已绑定统一人物，不能修改待确认状态")
        coordinator = getattr(self.plugin, "req041_migration_coordinator", None)
        reference = self._identity_pending_reference(user_id)
        status_reader = getattr(coordinator, "pending_status", None)
        transition = getattr(
            coordinator, "dismiss_pending" if action == "dismiss" else "restore_pending", None
        )
        if not reference or not callable(status_reader) or not callable(transition):
            return self._error("待确认身份服务不可用")
        try:
            before = status_reader(reference)
            if not isinstance(before, dict) or not before.get("found"):
                return self._error("没有找到该用户的待确认记录")
            expected = "pending" if action == "dismiss" else "dismissed"
            target = "dismissed" if action == "dismiss" else "pending"
            current = str(before.get("state") or "")
            changed = False
            if current == expected:
                changed = bool(transition(reference))
            elif current != target:
                return self._error("待确认记录状态已变化，请刷新后重试")
            after = status_reader(reference)
            safe = after if isinstance(after, dict) else {}
            return self._ok({
                "result": {
                    "ok": str(safe.get("state") or "") == target,
                    "state": str(safe.get("state") or "")[:24],
                    "reason_code": str(safe.get("reason_code") or "")[:80],
                    "changed": changed,
                }
            })
        except Exception as exc:
            logger.warning("[PrivateCompanionPage] 更新待确认身份状态失败: %s", exc)
            return self._error("更新待确认身份状态失败")

    def _identity_admin_summary(
        self, user: dict[str, Any], *, user_id: str = "", snapshot: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Build the official user-page identity view from allowlisted state."""
        if not isinstance(user, dict):
            return {"linked": False, "code": "identity_pending"}
        person_id = self._single_line(user.get("unified_person_id"), 80)
        subject = self._single_line(
            user.get("identity_subject_id") or user.get("user_id"), 160
        )
        if not person_id:
            return {
                "linked": False,
                "code": "identity_pending",
                "profile_status": "pending",
                "identity_assurance": "unverified",
                "migration": {"state": "pending", "read_generation": "legacy"},
                "pending": self._identity_pending_summary(
                    user_id or self._single_line(user.get("user_id"), 160)
                ),
                "domains": {
                    kind: {"status": "pending", "scope_count": 0, "record_count": 0, "ready_scope_count": 0}
                    for kind in ("private", "group_member", "group_shared")
                },
            }
        registry = self._page_unified_person_registry()
        reader = getattr(registry, "safe_admin_person_summary", None)
        summary = reader(person_id, subject) if callable(reader) else {
            "linked": False, "code": "identity_summary_unavailable"
        }
        if not isinstance(summary, dict):
            summary = {"linked": False, "code": "identity_summary_unavailable"}
        summary = dict(summary)
        summary["person_id"] = person_id

        coordinator = getattr(self.plugin, "req041_migration_coordinator", None)
        migration_reader = getattr(coordinator, "identity_status", None)
        migration = migration_reader(person_id) if callable(migration_reader) else {}
        if not isinstance(migration, dict):
            migration = {}
        summary["migration"] = {
            "state": self._single_line(migration.get("state"), 32) or "pending",
            "read_generation": self._single_line(
                migration.get("read_generation"), 16
            ) or "legacy",
            "backlog": max(0, _safe_int(migration.get("backlog"), 0)),
            "stable_cycles": max(0, _safe_int(migration.get("stable_cycles"), 0)),
        }

        summary["domains"] = self._identity_domain_summary(person_id, snapshot or {})
        archive_ready_checker = getattr(self, "_identity_archive_remote_available", None)
        archive_ready = (
            bool(archive_ready_checker())
            if callable(archive_ready_checker)
            else True
        )
        summary["lifecycle"] = {
            "can_unlink_current": bool(
                summary.get("current_identity_linked")
                and int(summary.get("active_identity_count") or 0) > 1
                and summary.get("profile_status") == "active"
            ),
            "can_archive": bool(
                summary.get("linked")
                and summary.get("profile_status") == "active"
                and archive_ready
            ),
            "archive_ready": archive_ready,
            "can_purge": bool(summary.get("profile_status") == "deleted"),
            "can_relink_current": bool(
                summary.get("current_identity_detached")
                and summary.get("profile_status") == "active"
            ),
        }
        return summary

    def _identity_archive_remote_available(self) -> bool:
        """Only expose the destructive archive action when scoped cleanup is bound."""
        checker = getattr(self.plugin, "_req041_scoped_archive_available", None)
        if not callable(checker):
            # Compatibility with older plugin instances that predate the
            # scoped cleanup readiness probe.
            return True
        try:
            return bool(checker())
        except Exception:
            return False

    @staticmethod
    def _relationship_score_input(value: Any) -> int:
        """Parse the bounded manual companion-intimacy compatibility field."""
        if isinstance(value, bool):
            raise ValueError("companion_intimacy must be an integer")
        if isinstance(value, int):
            score = value
        elif isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
            score = int(value.strip())
        else:
            raise ValueError("companion_intimacy must be an integer")
        if not -1200 <= score <= 1200:
            raise ValueError("companion_intimacy must be between -1200 and 1200")
        return score

    async def list_users(self) -> dict[str, Any]:
        start = time.perf_counter()
        try:
            limit = self._query_int("limit", 80, 1, 300)
            async with self.plugin._data_lock:
                cleaner = getattr(self.plugin, "_cleanup_orphan_reaction_expression_users", None)
                if callable(cleaner) and cleaner():
                    self.plugin._save_data_sync(sections={"users"})
                users = self.plugin.data.get("users", {})
                if not isinstance(users, dict):
                    users = {}
                user_items = [(user_id, dict(user)) for user_id, user in users.items() if isinstance(user, dict)]
            # Keep source identities visible until capability and deletion
            # semantics are fully person-scoped. Hiding them here could leave
            # a contradictory permission record that an administrator cannot see.
            items = [self._user_summary(user_id, user) for user_id, user in user_items]
            items.sort(key=lambda item: item.get("last_seen_ts") or 0, reverse=True)
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            if elapsed_ms > 1200:
                logger.warning("[PrivateCompanionPage] 用户列表接口耗时较高: elapsed=%sms users=%s", elapsed_ms, len(items))
            return self._ok({"items": items[:limit], "total": len(items)})
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 获取用户列表失败: {exc}", exc_info=True)
            return self._error(str(exc))
    async def get_user(self) -> dict[str, Any]:
        user_id = str(request.args.get("user_id", "")).strip()
        if not user_id:
            return self._error("缺少 user_id")
        try:
            async with self.plugin._data_lock:
                user = deepcopy((self.plugin.data.get("users") or {}).get(user_id))
                daily_state = deepcopy(self.plugin.data.get("daily_state"))
                state_conditions = deepcopy(self.plugin.data.get("state_conditions"))
                identity_snapshot = {
                    key: deepcopy(self.plugin.data.get(key))
                    for key in (
                        "unified_person", "users", "groups", "_req041_private_memory",
                        "_req041_persona_reset_saga", "_req041_group_reset_sagas",
                    )
                    if key in self.plugin.data
                }
            if not isinstance(user, dict):
                return self._error("用户不存在")
            relationship_view_getter = getattr(
                self.plugin, "_req041_relationship_snapshot_view", None
            )
            if callable(relationship_view_getter):
                user = relationship_view_getter(user, source="admin_user_detail")
            detail = self._user_summary(user_id, user)
            relationship_panel = self._relationship_panel(
                user_id,
                user,
                relationship_stage=str(detail.get("relationship_stage") or ""),
            )
            detail["relationship_panel"] = relationship_panel
            detail["current_interaction"] = relationship_panel["current_interaction"]
            detail["expression_decision"] = relationship_panel["expression_decision"]
            detail["p4_runtime"] = self._p4_page_status_projection()
            detail["emotion_trace_summary"] = emotion_trace_summary(user, limit=20)
            trace_id = self._single_line(request.args.get("trace_id", ""), 96)
            if trace_id:
                detail["emotion_trace"] = build_emotion_trace_projection(
                    user,
                    trace_id,
                    daily_state=daily_state,
                    state_conditions=state_conditions,
                    expression_decision=detail.get("expression_decision"),
                )
            route_status_getter = getattr(self.plugin, "_private_delivery_route_status", None)
            delivery_route = route_status_getter(user_id, user) if callable(route_status_getter) else {}
            detail.update(
                {
                    "memory": user.get("companion_memory") if isinstance(user.get("companion_memory"), dict) else {},
                    "expression_profile": self._expression_profile_summary(user),
                    "intent_profile": user.get("intent_profile") if isinstance(user.get("intent_profile"), dict) else {},
                    "behavior_habits": self._behavior_habit_summary(user),
                    "dialogue_episodes": self._limited_list(user.get("dialogue_episodes"), 12),
                    "open_loops": self._limited_list(user.get("open_loops"), 12),
                    "recent_reply_topics": self._limited_list(user.get("recent_reply_topics"), 16),
                    "last_user_message": self._display_message_text(user.get("last_user_message"), 500),
                    "last_companion_message": self._display_message_text(user.get("last_companion_message"), 500),
                    "delivery_route": delivery_route if isinstance(delivery_route, dict) else {},
                    "formatted": {
                        "action_affinity": self.plugin._format_action_affinity_summary(user),
                        "next_proactive": self.plugin._format_next_proactive(user),
                    },
                }
            )
            portrait_status_reader = getattr(self.plugin, "_req036_portrait_bridge_status_for_user", None)
            detail["portrait_bridge"] = (
                await portrait_status_reader(user)
                if callable(portrait_status_reader)
                else {"available": False, "code": "bridge_unavailable", "last_synced_at": "", "portrait_revision": 0}
            )
            detail["identity_admin"] = self._identity_admin_summary(
                user, user_id=user_id, snapshot=identity_snapshot
            )
            return self._ok(detail)
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 获取用户详情失败: {exc}", exc_info=True)
            return self._error(str(exc))
    async def link_unified_identity(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True)
        if not isinstance(payload, dict):
            return self._error("请求体必须是 JSON 对象")
        person_id = self._single_line(payload.get("person_id"), 80)
        user_id = self._single_line(payload.get("user_id"), 160)
        operation_id = self._single_line(payload.get("operation_id"), 120)
        confirmation_token = self._single_line(payload.get("confirmation_token"), 80)
        if "dry_run" in payload and type(payload.get("dry_run")) is not bool:
            return self._error("dry_run 必须是 JSON 布尔值")
        dry_run = payload.get("dry_run", True)
        if not person_id or not user_id or not operation_id:
            return self._error("person_id、user_id 和 operation_id 均为必填项")
        if not dry_run and not confirmation_token:
            return self._error("执行重新关联必须提交预览返回的 confirmation_token")
        try:
            async with self.plugin._data_lock:
                registry = self._page_unified_person_registry()
                users = self.plugin.data.get("users")
                user = users.get(user_id) if isinstance(users, dict) else None
                if not isinstance(user, dict):
                    return self._error("用户不存在")
                if self._single_line(user.get("unified_person_id"), 80) != person_id:
                    return self._error("用户与统一人物不匹配")
                subject = self._single_line(
                    user.get("identity_subject_id") or user.get("user_id") or user_id,
                    160,
                )
                resolver = getattr(registry, "detached_identity_for_person_subject", None)
                identity = resolver(person_id, subject) if callable(resolver) else None
                if not isinstance(identity, dict):
                    return self._error("当前账号没有可安全恢复的已解绑身份")
                checkpoint_reader = getattr(registry, "identity_projection_checkpoint", None)
                checkpoint = checkpoint_reader(person_id) if callable(checkpoint_reader) else {}
                if not isinstance(checkpoint, dict) or checkpoint.get("ok") is not True:
                    return self._error("统一身份投影暂不可安全变更")
                expected_confirmation = self._identity_link_confirmation(
                    person_id=person_id,
                    operation_id=operation_id,
                    identity=identity,
                    checkpoint=checkpoint,
                )
                if not dry_run and not hmac.compare_digest(
                    confirmation_token, expected_confirmation
                ):
                    return self._error("身份状态已变化，请刷新后重新预览")
                if dry_run:
                    summary_reader = getattr(registry, "safe_admin_person_summary", None)
                    summary = summary_reader(person_id, subject) if callable(summary_reader) else {}
                    result = {
                        "ok": True,
                        "state": "pending",
                        "code": "identity_relink_preview",
                        "changed": False,
                        "active_identity_count": max(0, _safe_int(summary.get("active_identity_count"), 0)),
                        "detached_identity_count": max(0, _safe_int(summary.get("detached_identity_count"), 0)),
                        "confirmation_token": expected_confirmation,
                    }
                else:
                    raw_result = registry.link_identity(
                        person_id,
                        identity,
                        operation_id=operation_id,
                        actor_id="page_administrator",
                    )
                    result = {
                        "ok": bool(raw_result.get("ok")),
                        "state": self._single_line(raw_result.get("state"), 32) or "pending",
                        "code": self._single_line(raw_result.get("code"), 80) or "identity_relink_failed",
                        "changed": bool(raw_result.get("changed")),
                    }
                if result.get("changed"):
                    emitter = getattr(self.plugin, "_req041_emit_identity_dual_write", None)
                    if callable(emitter):
                        emitter(
                            raw_result,
                            action="link",
                            operation_id=operation_id,
                            registry=registry,
                        )
                    self.plugin._schedule_data_save(sections={"unified_person"})
            if not result.get("ok"):
                return self._error(str(result.get("code") or "统一身份重新关联失败"))
            return self._ok({"result": result})
        except Exception as exc:
            logger.warning("[PrivateCompanionPage] 统一身份重新关联失败: %s", exc)
            return self._error("统一身份重新关联失败")

    async def unlink_unified_identity(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True)
        if not isinstance(payload, dict):
            return self._error("请求体必须是 JSON 对象")
        person_id = self._single_line(payload.get("person_id"), 80)
        user_id = self._single_line(payload.get("user_id"), 160)
        operation_id = self._single_line(payload.get("operation_id"), 120)
        confirmation_token = self._single_line(payload.get("confirmation_token"), 80)
        if "dry_run" in payload and type(payload.get("dry_run")) is not bool:
            return self._error("dry_run 必须是 JSON 布尔值")
        dry_run = payload.get("dry_run", True)
        if not person_id or not user_id or not operation_id:
            return self._error("person_id、user_id 和 operation_id 均为必填项")
        if not dry_run and not confirmation_token:
            return self._error("执行解绑必须提交预览返回的 confirmation_token")
        try:
            async with self.plugin._data_lock:
                registry = self._page_unified_person_registry()
                users = self.plugin.data.get("users")
                user = users.get(user_id) if isinstance(users, dict) else None
                if not isinstance(user, dict):
                    return self._error("用户不存在")
                if self._single_line(user.get("unified_person_id"), 80) != person_id:
                    return self._error("用户与统一人物不匹配")
                subject = self._single_line(
                    user.get("identity_subject_id") or user.get("user_id") or user_id,
                    160,
                )
                resolver = getattr(registry, "identity_for_person_subject", None)
                identity = resolver(person_id, subject) if callable(resolver) else None
                if not isinstance(identity, dict):
                    return self._error("当前用户没有唯一的正式身份链接")
                checkpoint_reader = getattr(registry, "identity_projection_checkpoint", None)
                checkpoint = checkpoint_reader(person_id) if callable(checkpoint_reader) else {}
                if not isinstance(checkpoint, dict) or checkpoint.get("ok") is not True:
                    return self._error("统一身份投影暂不可安全变更")
                expected_confirmation = self._identity_unlink_confirmation(
                    person_id=person_id,
                    operation_id=operation_id,
                    identity=identity,
                    checkpoint=checkpoint,
                )
                if not dry_run and not hmac.compare_digest(
                    confirmation_token, expected_confirmation
                ):
                    return self._error("身份状态已变化，请刷新后重新预览")
                result = registry.unlink_identity(
                    person_id,
                    identity,
                    operation_id=operation_id,
                    actor_id="page_administrator",
                    dry_run=dry_run,
                )
                if result.get("changed"):
                    emitter = getattr(self.plugin, "_req041_emit_identity_dual_write", None)
                    if callable(emitter):
                        emitter(
                            result,
                            action="unlink",
                            operation_id=operation_id,
                            registry=registry,
                        )
                    self.plugin._schedule_data_save(sections={"unified_person"})
            if not result.get("ok") and result.get("code") != "split_manual_review_required":
                return self._error(str(result.get("code") or "统一身份解绑失败"))
            safe_result = self._safe_identity_unlink_result(result)
            if dry_run and result.get("ok"):
                safe_result["confirmation_token"] = expected_confirmation
            return self._ok({"result": safe_result})
        except Exception as exc:
            logger.warning("[PrivateCompanionPage] 统一身份解绑失败: %s", exc)
            return self._error("统一身份解绑失败")

    async def archive_unified_person(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True)
        if not isinstance(payload, dict):
            return self._error("请求体必须是 JSON 对象")
        person_id = self._single_line(payload.get("person_id"), 80)
        operation_id = self._single_line(payload.get("operation_id"), 120)
        confirmation_token = self._single_line(payload.get("confirmation_token"), 80)
        if "dry_run" in payload and type(payload.get("dry_run")) is not bool:
            return self._error("dry_run 必须是 JSON 布尔值")
        dry_run = payload.get("dry_run", True)
        if not person_id or not operation_id:
            return self._error("person_id 和 operation_id 均为必填项")
        if not dry_run and not confirmation_token:
            return self._error("执行归档必须提交预览返回的 confirmation_token")
        archive = getattr(self.plugin, "archive_unified_person", None)
        if not callable(archive):
            return self._error("人物归档服务不可用")
        try:
            result = await archive(
                person_id, operation_id=operation_id,
                confirmation_token=confirmation_token, dry_run=dry_run,
                actor_id="page_administrator", reason_code="person_archive",
            )
            if not result.get("ok"):
                code = str(result.get("code") or "人物归档失败")
                if code == "scoped_identity_archive_unavailable":
                    return self._error("记忆插件的作用域归档服务尚未就绪，请先启动或更新记忆插件后刷新页面")
                return self._error(code)
            return self._ok({"result": self._safe_person_lifecycle_result(result, "archive")})
        except Exception as exc:
            logger.warning("[PrivateCompanionPage] 人物归档失败: %s", exc)
            return self._error("人物归档失败")

    async def delete_unified_person(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True)
        if not isinstance(payload, dict):
            return self._error("请求体必须是 JSON 对象")
        person_id = self._single_line(payload.get("person_id"), 80)
        operation_id = self._single_line(payload.get("operation_id"), 120)
        confirmation_token = self._single_line(payload.get("confirmation_token"), 80)
        if "dry_run" in payload and type(payload.get("dry_run")) is not bool:
            return self._error("dry_run 必须是 JSON 布尔值")
        dry_run = payload.get("dry_run", True)
        if not person_id or not operation_id:
            return self._error("person_id 和 operation_id 均为必填项")
        if not dry_run and not confirmation_token:
            return self._error("执行删除必须提交预览返回的 confirmation_token")
        purge = getattr(self.plugin, "purge_unified_person", None)
        if not callable(purge):
            return self._error("人物删除服务不可用")
        try:
            result = await purge(
                person_id, operation_id=operation_id,
                confirmation_token=confirmation_token, dry_run=dry_run,
                actor_id="page_administrator", reason_code="person_delete",
            )
            safe_result = self._safe_person_lifecycle_result(result, "purge")
            if not result.get("ok") and result.get("code") != "archive_retention_active":
                return self._error(str(result.get("code") or "人物删除失败"))
            return self._ok({"result": safe_result})
        except Exception as exc:
            logger.warning("[PrivateCompanionPage] 人物删除失败: %s", exc)
            return self._error("人物删除失败")

    async def preview_unified_identity_merge(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True)
        if not isinstance(payload, dict):
            return self._error("请求体必须是 JSON 对象")
        source_person_id = self._single_line(payload.get("source_person_id"), 80)
        target_person_id = self._single_line(payload.get("target_person_id"), 80)
        operation_id = self._single_line(payload.get("operation_id"), 120)
        if not source_person_id or not target_person_id or not operation_id:
            return self._error("source_person_id、target_person_id 和 operation_id 均为必填项")
        try:
            async with self.plugin._data_lock:
                result = self._page_unified_person_registry().preview_person_merge(
                    source_person_id,
                    target_person_id,
                    operation_id=operation_id,
                )
            if not result.get("ok") and result.get("code") != "merge_manual_review_required":
                return self._error(str(result.get("code") or "统一人物合并预览失败"))
            return self._ok({"result": result})
        except Exception as exc:
            logger.warning("[PrivateCompanionPage] 统一人物合并预览失败: %s", exc)
            return self._error("统一人物合并预览失败")

    async def update_user(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True)
        if not isinstance(payload, dict):
            return self._error("请求体必须是 JSON 对象")
        user_id = str(payload.get("user_id", "")).strip()
        if not user_id:
            return self._error("缺少 user_id")
        if "enabled" in payload or "private_companion_enabled" in payload:
            return self._error("私聊权限已移除，普通私聊始终可用")
        if "proactive_private_enabled" in payload and type(payload.get("proactive_private_enabled")) is not bool:
            return self._error("proactive_private_enabled 必须是 JSON 布尔值")
        if "portrait_mode" in payload:
            portrait_mode = str(payload.get("portrait_mode") or "").strip().lower()
            if portrait_mode not in {"follow_global", "disabled", "use_existing", "learn_and_use"}:
                return self._error("portrait_mode 无效")
            payload["portrait_mode"] = portrait_mode
        relationship_score = None
        intimacy_keys = [
            key
            for key in ("companion_intimacy", "relationship_score")
            if key in payload
        ]
        if len(intimacy_keys) > 1:
            return self._error("陪伴亲密度字段不能重复提交")
        if intimacy_keys:
            try:
                relationship_score = self._relationship_score_input(
                    payload.get(intimacy_keys[0])
                )
            except ValueError as exc:
                return self._error(str(exc))
        requested_mode = str(payload.get("relationship_mode") or "").strip().lower() if "relationship_mode" in payload else None
        if requested_mode is not None and requested_mode not in {"normal", "owner_exclusive"}:
            return self._error("relationship_mode must be normal or owner_exclusive")
        relationship_prompt_requested = "owner_exclusive_relationship_prompt" in payload
        relationship_prompt_value = payload.get("owner_exclusive_relationship_prompt")
        if relationship_prompt_requested and relationship_prompt_value is not None and not isinstance(relationship_prompt_value, str):
            return self._error("owner_exclusive_relationship_prompt 必须是文本")
        requested_interaction_band = (
            str(payload.get("current_interaction_band") or "").strip().lower()
            if "current_interaction_band" in payload
            else None
        )
        interaction_expires_at = 0.0
        if "current_interaction_expires_at" in payload:
            raw_expiry = payload.get("current_interaction_expires_at")
            if isinstance(raw_expiry, bool) or not isinstance(raw_expiry, (int, float)):
                return self._error("current_interaction_expires_at must be a timestamp")
            interaction_expires_at = float(raw_expiry)
            if interaction_expires_at < 0 or interaction_expires_at > time.time() + 366 * 86400:
                return self._error("current_interaction_expires_at is outside the allowed range")
        try:
            action_message = ""
            async with self.plugin._data_lock:
                save_sections = {"users"}
                user = self.plugin._get_user(user_id)
                private_memory_mutation = any(
                    bool(payload.get(key))
                    for key in (
                        "clear_emotion_state",
                        "clear_behavior_habits",
                        "clear_learning",
                        "clear_open_loops",
                    )
                ) or bool(self._single_line(payload.get("remove_open_loop_text"), 120))
                private_memory_revision = None
                memory_managed_getter = getattr(
                    self.plugin, "_req041_private_memory_managed", None
                )
                private_memory_managed = bool(
                    memory_managed_getter() if callable(memory_managed_getter) else False
                )
                if private_memory_mutation and private_memory_managed:
                    preparer = getattr(
                        self.plugin, "_req041_prepare_authoritative_private_memory", None
                    )
                    private_memory_revision = preparer(user) if callable(preparer) else None
                    if private_memory_revision is None:
                        return self._error("权威私聊记忆暂不可写，请稍后重试")
                expression_voice_needs_refresh = False
                previous_role = self.plugin._private_user_role(user, user_id)
                previous_mode = normalize_relationship_mode(user.get("relationship_mode"), previous_role)
                role = previous_role
                if "relationship_role" in payload:
                    normalized_role = self.plugin._normalize_private_user_role(payload.get("relationship_role"))
                    if not normalized_role:
                        return self._error("relationship_role must be owner or friend")
                    role = normalized_role
                next_mode = normalize_relationship_mode(requested_mode if requested_mode is not None else previous_mode, role)
                if relationship_prompt_requested:
                    stable_user_id = self._single_line(user.get("user_id"), 160)
                    if stable_user_id != user_id:
                        return self._error("稳定用户身份不匹配，请刷新用户详情后重试")
                    prompt_normalizer = getattr(
                        self.plugin,
                        "_normalize_owner_exclusive_relationship_prompt",
                        None,
                    )
                    normalized_relationship_prompt = (
                        prompt_normalizer(relationship_prompt_value)
                        if callable(prompt_normalizer)
                        else str(relationship_prompt_value or "").strip()
                    )
                    if normalized_relationship_prompt and role != "owner":
                        return self._error("专属关系文本只允许绑定主要用户")
                if requested_mode == "owner_exclusive" and role != "owner":
                    return self._error("owner_exclusive relationship requires an owner user")
                if previous_mode == "owner_exclusive" and role != "owner" and requested_mode != "normal":
                    return self._error("switch the relationship to a normal stage before changing the owner role")
                if previous_mode == "owner_exclusive" and requested_mode == "normal" and relationship_score is None:
                    return self._error("leaving owner_exclusive requires selecting a normal relationship score")
                if previous_mode == "owner_exclusive" and relationship_score is not None and requested_mode != "normal":
                    return self._error("owner_exclusive relationship score is frozen; select normal mode first")
                if next_mode == "owner_exclusive" and relationship_score is not None:
                    return self._error("owner_exclusive relationship does not accept an exact score")
                if relationship_score is not None and relationship_score > 0 and role != "owner":
                    positive_cap = relationship_positive_score_cap(
                        getattr(self.plugin, "relationship_positive_stage_cap_key", "close")
                    )
                    if relationship_score > positive_cap:
                        return self._error(
                            f"普通用户亲密度上限为 {positive_cap}（当前配置的阶段上限），"
                            "请先调整「普通用户正向亲密度阶段上限」或将该用户设为主要用户"
                        )
                if requested_interaction_band is not None:
                    if not requested_interaction_band:
                        return self._error("current_interaction_band is required")
                    if requested_interaction_band not in allowed_expression_bands(role, next_mode):
                        return self._error("current interaction band is not allowed for this relationship")
                    requested_projection = current_interaction_projection(
                        {"expression_band": requested_interaction_band},
                        relationship_role=role,
                        relationship_mode=next_mode,
                        relationship_score=user.get("relationship_score"),
                        normal_interaction_band_cap=getattr(self.plugin, "normal_interaction_band_cap", "warm"),
                    )
                    if requested_projection.get("expression_band") != requested_interaction_band:
                        return self._error("current interaction band exceeds the configured user cap")
                capability_changes = {}
                for capability_key in (
                    "proactive_private_enabled",
                    "portrait_mode",
                ):
                    if capability_key in payload:
                        capability_changes[capability_key] = payload.get(capability_key)
                if capability_changes:
                    updater = getattr(self.plugin, "_req036_update_capabilities", None)
                    if not callable(updater):
                        return self._error("统一用户权限服务不可用")
                    capability_result = updater(
                        user,
                        capability_changes,
                        actor_id="page_administrator",
                        target_identity=user_id,
                        reason_code="page_administrator_update",
                    )
                    if not bool(capability_result.get("ok")):
                        return self._error(str(capability_result.get("code") or "权限更新失败"))
                    if "proactive_private_enabled" in capability_changes:
                        proactive_enabled = bool(
                            capability_result["capabilities"].get("proactive_private_enabled")
                        )
                        if proactive_enabled:
                            self.plugin._ensure_private_user_umo(user_id, user)
                        else:
                            self.plugin._clear_pending_proactive_plan(user)
                legacy_profile_before = {
                    key: (key in user, user.get(key))
                    for key in ("nickname", "style")
                    if key in payload
                }
                if "nickname" in payload:
                    user["nickname"] = self._single_line(payload.get("nickname"), 24)
                if "style" in payload:
                    user["style"] = self._single_line(payload.get("style"), 24)
                profile_fact_changes = {}
                if "nickname" in payload:
                    profile_fact_changes["preferred_address"] = user["nickname"]
                    if user["nickname"]:
                        profile_fact_changes["display_name"] = user["nickname"]
                if "style" in payload:
                    profile_fact_changes["style"] = user["style"]
                if profile_fact_changes:
                    profile_updater = getattr(
                        self.plugin, "_req041_update_unified_profile_facts", None
                    )
                    if callable(profile_updater):
                        profile_result = profile_updater(
                            user,
                            profile_fact_changes,
                            actor_id="page_administrator",
                            schedule_save=False,
                        )
                        if (
                            profile_result.get("state") != "skipped"
                            and profile_result.get("ok") is not True
                        ):
                            for key, (was_present, previous_value) in legacy_profile_before.items():
                                if was_present:
                                    user[key] = previous_value
                                else:
                                    user.pop(key, None)
                            return self._error(
                                str(profile_result.get("code") or "统一身份档案更新失败")
                            )
                        if profile_result.get("ok") and profile_result.get("changed"):
                            save_sections.add("unified_person")
                if "relationship_role" in payload:
                    user["relationship_role"] = role
                    expression_voice_needs_refresh = role != previous_role
                if requested_mode is not None:
                    user["relationship_mode"] = next_mode
                    expression_voice_needs_refresh = expression_voice_needs_refresh or next_mode != previous_mode
                if relationship_prompt_requested:
                    prompt_setter = getattr(
                        self.plugin,
                        "_set_owner_exclusive_relationship_prompt",
                        None,
                    )
                    if not callable(prompt_setter):
                        return self._error("当前版本不支持按人格保存专属关系文本")
                    prompt_result = prompt_setter(
                        user,
                        stable_user_id=user_id,
                        text=normalized_relationship_prompt,
                    )
                    if not prompt_result.get("ok"):
                        return self._error(prompt_result.get("message") or "专属关系文本保存失败")
                if relationship_score is not None:
                    previous_score = _safe_int(user.get("relationship_score"), 0, -1200, 1200)
                    effective_score = relationship_score
                    user["relationship_score"] = effective_score
                    record_manual_relationship_change(
                        user,
                        previous_score,
                        effective_score,
                        now=time.time(),
                        reason_code="administrator_manual_relationship_adjustment",
                    )
                if requested_interaction_band is not None:
                    changed_at = time.time()
                    user["current_interaction"] = current_interaction_projection(
                        {
                            "expression_band": requested_interaction_band,
                            "source": "manual",
                            "operator": "page_administrator",
                            "reason": self._single_line(payload.get("current_interaction_reason"), 120) or "administrator_manual_override",
                            "updated_at": changed_at,
                            "expires_at": interaction_expires_at,
                            "manual_override": True,
                        },
                        relationship_role=role,
                        relationship_mode=next_mode,
                        relationship_score=user.get("relationship_score"),
                        normal_interaction_band_cap=getattr(self.plugin, "normal_interaction_band_cap", "warm"),
                        now=changed_at,
                    )
                    contact = user.get("contact_preference")
                    contact_active = bool(
                        (
                            isinstance(contact, dict)
                            and (
                                contact.get("active")
                                or contact.get("no_contact")
                                or contact.get("backoff")
                            )
                        )
                        or str(contact or "").strip().lower()
                        in {"no_contact", "backoff", "avoid", "stop"}
                    )
                    if requested_interaction_band != "avoidant" and contact_active:
                        user["contact_preference"] = {
                            "mode": "normal",
                            "active": False,
                            "no_contact": False,
                            "backoff": False,
                            "source": "manual",
                            "operator": "page_administrator",
                            "reason_code": "administrator_manual_interaction_correction",
                            "updated_at": changed_at,
                        }
                    expression_voice_needs_refresh = True
                elif role != previous_role or next_mode != previous_mode:
                    user["current_interaction"] = current_interaction_projection(
                        user.get("current_interaction"),
                        relationship_role=role,
                        relationship_mode=next_mode,
                        relationship_score=user.get("relationship_score"),
                        normal_interaction_band_cap=getattr(self.plugin, "normal_interaction_band_cap", "warm"),
                        now=time.time(),
                    )
                if "proactive_daily_limit" in payload:
                    user["proactive_daily_limit"] = _safe_int(payload.get("proactive_daily_limit"), -1, -1, 30)
                for key in (
                    "proactive_idle_minutes",
                    "proactive_min_interval_minutes",
                    "photo_daily_limit",
                    "screen_peek_daily_limit",
                    "poke_daily_limit",
                ):
                    if key in payload:
                        user[key] = _safe_int(payload.get(key), -1, -1)
                if self.plugin._private_user_role(user, user_id) == "friend":
                    user["photo_daily_limit"] = -1
                    user["photo_sent_today"] = 0
                    user["photo_sent_day"] = ""
                    user["photo_generated_today"] = 0
                    user["photo_generated_day"] = ""
                    user["last_generated_photo_path"] = ""
                    user["last_generated_photo_at"] = 0
                    user["screen_peek_daily_limit"] = -1
                    user["screen_peek_today"] = 0
                    user["screen_peek_day"] = ""
                    user["screen_peek_last_at"] = 0
                if "proactive_boundary_note" in payload:
                    user["proactive_boundary_note"] = self._single_line(payload.get("proactive_boundary_note"), 180)
                if payload.get("reset_daily"):
                    user["sent_today"] = 0
                    user["sent_day"] = ""
                    user["ignored_streak"] = 0
                    user["photo_sent_today"] = 0
                    user["photo_sent_day"] = ""
                    user["photo_generated_today"] = 0
                    user["photo_generated_day"] = ""
                    user["screen_peek_today"] = 0
                if payload.get("clear_schedule"):
                    self.plugin._clear_pending_proactive_plan(user)
                if payload.get("clear_emotion_state"):
                    user["intent_profile"] = {}
                    user.pop("relationship_state", None)
                    user["current_interaction"] = current_interaction_projection(
                        None,
                        relationship_role=user.get("relationship_role"),
                        relationship_mode=user.get("relationship_mode"),
                        relationship_score=user.get("relationship_score"),
                        normal_interaction_band_cap=getattr(self.plugin, "normal_interaction_band_cap", "warm"),
                    )
                if payload.get("clear_behavior_habits"):
                    user["behavior_habits"] = {}
                if payload.get("clear_learning"):
                    for key, empty in (
                        ("companion_memory", {}),
                        ("expression_profile", {}),
                        ("intent_profile", {}),
                        ("recent_reply_topics", []),
                        ("dialogue_episodes", []),
                        ("open_loops", []),
                        ("action_preferences", {}),
                    ):
                        user[key] = empty
                    user["episode_message_count"] = 0
                    user["last_episode_refresh_at"] = 0
                    user["last_memory_refresh_at"] = 0
                    expression_voice_needs_refresh = True
                if payload.get("clear_open_loops"):
                    action_message = self.plugin._remove_open_loop_entry(user, "全部")
                remove_open_loop_text = self._single_line(payload.get("remove_open_loop_text"), 120)
                if remove_open_loop_text:
                    action_message = self.plugin._remove_open_loop_entry(user, remove_open_loop_text)
                expression_action = self._single_line(payload.get("expression_action"), 40)
                if expression_action:
                    action_message = self._apply_expression_profile_action(user, payload)
                    if expression_action in {"approve", "delete_sample"}:
                        expression_voice_needs_refresh = True
                if expression_voice_needs_refresh:
                    voice_refresher = getattr(self.plugin, "_refresh_expression_voice_profile", None)
                    if callable(voice_refresher):
                        voice_refresher()
                        save_sections.add("expression_voice_profile")
                if any(
                    key in payload
                    for key in ("relationship_role", "relationship_mode", "relationship_score", "companion_intimacy")
                ):
                    snapshot_emitter = getattr(self.plugin, "_req041_emit_relationship_snapshot", None)
                    if callable(snapshot_emitter):
                        snapshot_emitter(
                            user,
                            reason_code="administrator_relationship_update",
                        )
                if private_memory_mutation and private_memory_managed:
                    committer = getattr(
                        self.plugin, "_req041_commit_authoritative_private_memory", None
                    )
                    if not callable(committer) or not committer(
                        user,
                        expected_revision=private_memory_revision,
                        operation_id=f"req041-page-memory:{user_id}:{uuid.uuid4().hex}",
                    ):
                        return self._error("权威私聊记忆已发生并发变更，请刷新后重试")
                    save_sections.add("_req041_private_memory")
                self.plugin._save_data_sync(sections=save_sections)
                snapshot = deepcopy(user)
            result = self._user_summary(user_id, snapshot)
            result.update(
                {
                    "expression_profile": self._expression_profile_summary(snapshot),
                }
            )
            if action_message:
                result["message"] = action_message
            return self._ok(result)
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 更新用户失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def delete_user(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        user_id = str(payload.get("user_id", "")).strip()
        if not user_id:
            return self._error("缺少 user_id")
        try:
            async with self.plugin._data_lock:
                users = self.plugin.data.get("users")
                if not isinstance(users, dict):
                    users = {}
                    self.plugin.data["users"] = users
                canonical_user_id = self.plugin._canonical_private_user_id(user_id)
                stored_user_id = canonical_user_id if canonical_user_id in users else user_id
                existing_user = users.get(stored_user_id)
                if (
                    isinstance(existing_user, dict)
                    and self._single_line(existing_user.get("unified_person_id"), 80)
                ):
                    return self._error(
                        "该用户已属于统一人物，请在“身份与隔离”中预览并归档，不能绕过统一数据链直接删除"
                    )
                removed_ids = {user_id, canonical_user_id, stored_user_id}
                removed_user = users.pop(stored_user_id, None)
                if isinstance(removed_user, dict):
                    for alias_id in removed_user.get("alias_user_ids") if isinstance(removed_user.get("alias_user_ids"), list) else []:
                        alias_text = str(alias_id or "").strip()
                        if alias_text:
                            removed_ids.add(alias_text)
                removed_ids = {item for item in removed_ids if item}
                merge_backups = self.plugin.data.get("private_user_alias_merge_backups")
                merge_backups_changed = False
                if isinstance(merge_backups, dict):
                    for removed_id in removed_ids:
                        if removed_id in merge_backups:
                            merge_backups.pop(removed_id, None)
                            merge_backups_changed = True

                def keep_expression_scope_ids(raw_values: Any) -> list[str]:
                    kept: list[str] = []
                    for item in self._normalize_id_list(raw_values or []):
                        canonical_item_getter = getattr(self.plugin, "_expression_private_scope_id", None)
                        canonical_item = canonical_item_getter(item) if callable(canonical_item_getter) else item
                        if item in removed_ids or canonical_item in removed_ids:
                            continue
                        if item not in kept:
                            kept.append(item)
                    return kept

                old_expression_learning_ids = self._normalize_id_list(
                    getattr(self.plugin, "expression_private_learning_source_ids", []) or []
                )
                old_expression_application_ids = self._normalize_id_list(
                    getattr(self.plugin, "expression_private_application_user_ids", []) or []
                )
                expression_learning_ids = keep_expression_scope_ids(old_expression_learning_ids)
                expression_application_ids = keep_expression_scope_ids(old_expression_application_ids)
                removed_expression_scope = (
                    expression_learning_ids != old_expression_learning_ids
                    or expression_application_ids != old_expression_application_ids
                )

                old_target_user_ids = self._normalize_id_list(getattr(self.plugin, "target_user_ids", []) or [])
                target_user_ids = [item for item in old_target_user_ids if item not in removed_ids]
                removed_target = len(target_user_ids) != len(old_target_user_ids)

                private_aliases = {
                    str(alias).strip(): str(target).strip()
                    for alias, target in (getattr(self.plugin, "private_user_aliases", {}) or {}).items()
                    if str(alias).strip()
                    and str(target).strip()
                    and str(alias).strip() not in removed_ids
                    and str(target).strip() not in removed_ids
                }
                delivery_aliases = {
                    str(alias).strip(): str(target).strip()
                    for alias, target in (getattr(self.plugin, "private_user_delivery_aliases", {}) or {}).items()
                    if str(alias).strip()
                    and str(target).strip()
                    and str(alias).strip() not in removed_ids
                    and str(target).strip() not in removed_ids
                }
                removed_private_aliases = len(private_aliases) != len(getattr(self.plugin, "private_user_aliases", {}) or {})
                removed_delivery_aliases = len(delivery_aliases) != len(getattr(self.plugin, "private_user_delivery_aliases", {}) or {})

                alias_text = self._format_private_alias_mapping(private_aliases)
                delivery_alias_text = self._format_private_alias_mapping(delivery_aliases)
                overrides = {
                    "target_user_ids": target_user_ids,
                    "private_user_aliases": alias_text,
                    "private_user_delivery_aliases": delivery_alias_text,
                    "expression_private_learning_source_ids": expression_learning_ids,
                    "expression_private_application_user_ids": expression_application_ids,
                }
                self._apply_config_value("target_user_ids", target_user_ids, overrides)
                self._apply_config_value("private_user_aliases", alias_text, overrides)
                self._apply_config_value("private_user_delivery_aliases", delivery_alias_text, overrides)
                self._apply_config_value("expression_private_learning_source_ids", expression_learning_ids, overrides)
                self._apply_config_value("expression_private_application_user_ids", expression_application_ids, overrides)
                save_sections: set[str] = set()
                if removed_user is not None:
                    save_sections.add("users")
                if merge_backups_changed:
                    save_sections.add("private_user_alias_merge_backups")
                if removed_user is not None or removed_expression_scope:
                    voice_refresher = getattr(self.plugin, "_refresh_expression_voice_profile", None)
                    if callable(voice_refresher):
                        voice_refresher()
                        save_sections.add("expression_voice_profile")
                if save_sections:
                    self.plugin._save_data_sync(sections=save_sections)

            config_saved = await self._save_config_if_possible()
            message_parts = []
            if removed_user is not None:
                message_parts.append("已删除私聊用户记录")
            if removed_target:
                message_parts.append("已移出主动目标名单")
            if removed_private_aliases:
                message_parts.append("已清理身份归并映射")
            if removed_delivery_aliases:
                message_parts.append("已清理主动发送映射")
            if removed_expression_scope:
                message_parts.append("已清理表达学习范围")
            message = "，".join(message_parts) if message_parts else "没有找到可删除的私聊用户记录"
            return self._ok(
                {
                    "user_id": user_id,
                    "canonical_user_id": canonical_user_id,
                    "removed_ids": sorted(removed_ids),
                    "removed_user": removed_user is not None,
                    "removed_target": removed_target,
                    "removed_private_aliases": removed_private_aliases,
                    "removed_delivery_aliases": removed_delivery_aliases,
                    "removed_expression_scope": removed_expression_scope,
                    "config_saved": config_saved,
                    "message": message,
                }
            )
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 删除用户失败: {exc}", exc_info=True)
            return self._error(str(exc))

    @staticmethod
    def _format_private_alias_mapping(mapping: dict[str, str]) -> str:
        return "\n".join(
            f"{alias}={target}"
            for alias, target in sorted(
                (
                    (str(alias or "").strip(), str(target or "").strip())
                    for alias, target in (mapping or {}).items()
                ),
                key=lambda item: (item[1], item[0]),
            )
            if alias and target and alias != target
        )

    async def list_groups(self) -> dict[str, Any]:
        start = time.perf_counter()
        try:
            limit = self._query_int("limit", 80, 1, 300)
            async with self.plugin._data_lock:
                groups = self.plugin.data.get("groups", {})
                if not isinstance(groups, dict):
                    groups = {}
                visible_groups = [
                    (group_id, dict(group))
                    for group_id, group in groups.items()
                    if isinstance(group, dict) and not self._looks_like_member_shadow_group(str(group_id), group)
                ]
                shadow_count = len(groups) - len(visible_groups)
            await self._refresh_group_names_from_platform(visible_groups)
            items = [
                self._group_summary(group_id, self._refresh_group_atmosphere_for_page(group))
                for group_id, group in visible_groups
            ]
            items.sort(key=lambda item: item.get("last_seen_ts") or 0, reverse=True)
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            if elapsed_ms > 1200:
                logger.warning("[PrivateCompanionPage] 群列表接口耗时较高: elapsed=%sms groups=%s", elapsed_ms, len(items))
            return self._ok({"items": items[:limit], "total": len(items), "shadow_total": shadow_count})
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 获取群列表失败: {exc}", exc_info=True)
            return self._error(str(exc))

    def _refresh_group_atmosphere_for_page(self, group: dict[str, Any]) -> dict[str, Any]:
        updater = getattr(self.plugin, "_update_group_atmosphere", None)
        if callable(updater):
            try:
                updater(group)
            except Exception as exc:
                logger.info(
                    "[PrivateCompanionPage] 群气氛读取时重算失败: %s",
                    self._single_line(exc, 120),
                )
        return group

    def _group_page_identity_names(self, group: dict[str, Any]) -> dict[str, str]:
        """Project relationship-network names onto group members for page display only."""
        members = group.get("members") if isinstance(group.get("members"), dict) else {}
        resolver = getattr(self.plugin, "_group_member_identity_name", None)
        profile_getter = getattr(self.plugin, "_worldbook_profile_by_user_id", None)
        if not callable(resolver) or not callable(profile_getter):
            return {}
        names: dict[str, str] = {}
        for user_id, raw_member in members.items():
            uid = self._single_line(user_id, 40)
            if not uid or not isinstance(raw_member, dict):
                continue
            fallback = self._single_line(
                raw_member.get("display_name")
                or raw_member.get("nickname")
                or raw_member.get("name")
                or raw_member.get("card")
                or uid,
                40,
            )
            try:
                profile = profile_getter(uid, include_observation=True)
                if not isinstance(profile, dict):
                    continue
                identity_name = self._single_line(resolver(uid, fallback, limit=40), 40)
            except Exception:
                identity_name = ""
            if identity_name and identity_name != uid:
                raw_member["identity_name"] = identity_name
                names[uid] = identity_name
        return names

    def _group_page_recent_messages(
        self,
        group: dict[str, Any],
        identity_names: dict[str, str],
    ) -> list[dict[str, Any]]:
        items = self._limited_list(group.get("recent_messages"), 30)
        projected: list[dict[str, Any]] = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            sender_id = self._single_line(item.get("sender_id") or item.get("user_id") or item.get("qq"), 40)
            if sender_id in identity_names:
                item["identity_name"] = identity_names[sender_id]
            projected.append(item)
        return projected

    def _group_page_recent_bot_replies(self, group: dict[str, Any]) -> list[dict[str, Any]]:
        items = self._limited_list(group.get("recent_bot_replies"), 30)
        projected: list[dict[str, Any]] = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            item: dict[str, Any] = {
                "ts": raw.get("ts", 0),
                "text": self._display_message_text(raw.get("text"), 500),
                "reply_to_id": self._single_line(
                    raw.get("reply_to_id") or raw.get("sender_id"),
                    80,
                ),
                "kind": self._single_line(raw.get("kind"), 40) or "bot_reply",
                "talking_to_bot": bool(raw.get("talking_to_bot")),
            }
            for key in ("message_id", "delivery_id"):
                value = self._single_line(raw.get(key), 160)
                if value:
                    item[key] = value
            projected.append(item)
        return projected

    def _looks_like_member_shadow_group(self, group_id: str, group: dict[str, Any]) -> bool:
        """Hide historical records created when a sender id was mistaken for a group id."""
        gid = str(group_id or group.get("group_id") or "").strip()
        if not gid or not gid.isdigit():
            return False
        configured = set(self.plugin._configured_group_ids()) | set(self.plugin._configured_group_blacklist_ids())
        if gid in configured:
            return False
        recent = group.get("recent_messages") if isinstance(group.get("recent_messages"), list) else []
        sender_ids = [
            str(item.get("sender_id") or "").strip()
            for item in recent
            if isinstance(item, dict) and str(item.get("sender_id") or "").strip()
        ]
        if not sender_ids:
            return False
        members = group.get("members") if isinstance(group.get("members"), dict) else {}
        same_sender_hits = sum(1 for sender_id in sender_ids if sender_id == gid)
        unique_senders = {sender_id for sender_id in sender_ids if sender_id}
        if gid in members and same_sender_hits >= max(1, int(len(sender_ids) * 0.8)) and len(unique_senders) <= 2:
            return True
        if not self._single_line(group.get("name") or group.get("group_name"), 80) and same_sender_hits == len(sender_ids) and len(members) <= 2:
            return True
        return False

    def _group_display_name_missing(self, group_id: str, group: dict[str, Any]) -> bool:
        manual_name = self._single_line(group.get("manual_group_name"), 80)
        if manual_name:
            return False
        name = self._single_line(group.get("name") or group.get("group_name") or group.get("display_name"), 80)
        gid = str(group_id or group.get("group_id") or "").strip()
        return not name or name == gid or name == f"群 {gid}" or name.isdigit()

    def _clean_manual_group_display_name(self, value: Any, group_id: str = "") -> str:
        text = self._single_line(value, 80)
        gid = str(group_id or "").strip()
        if not text or text == gid or text == f"群 {gid}":
            return ""
        return text

    def _clean_group_display_name(self, value: Any, group_id: str = "") -> str:
        text = self._single_line(value, 80)
        gid = str(group_id or "").strip()
        if not text or text == gid or text == f"群 {gid}" or text.isdigit():
            return ""
        return text

    def _extract_onebot_list(self, result: Any) -> list[dict[str, Any]]:
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        if isinstance(result, dict):
            data = result.get("data")
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
            groups = result.get("groups") or result.get("items") or result.get("result")
            if isinstance(groups, list):
                return [item for item in groups if isinstance(item, dict)]
        return []

    def _extract_onebot_object(self, result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            return {}
        data = result.get("data")
        if isinstance(data, dict):
            return data
        result_obj = result.get("result")
        if isinstance(result_obj, dict):
            return result_obj
        return result

    def _name_from_group_payload(self, item: dict[str, Any], group_id: str) -> str:
        return self._clean_group_display_name(
            item.get("group_name")
            or item.get("group_remark")
            or item.get("group_display_name")
            or item.get("name")
            or item.get("display_name")
            or item.get("title"),
            group_id,
        )

    def _group_names_from_loaded_history(self, target_ids: set[str]) -> dict[str, str]:
        found: dict[str, str] = {}
        if not target_ids:
            return found
        patterns = {
            group_id: re.compile(rf"群号\s*{re.escape(group_id)}\(([^)\r\n]{{1,80}})\)")
            for group_id in target_ids
        }
        stack: list[Any] = [getattr(self.plugin, "data", {})]
        scanned_strings = 0
        while stack and len(found) < len(target_ids) and scanned_strings < 20000:
            value = stack.pop()
            if isinstance(value, dict):
                stack.extend(value.values())
                continue
            if isinstance(value, list):
                stack.extend(value)
                continue
            if not isinstance(value, str) or "群号" not in value:
                continue
            scanned_strings += 1
            for group_id, pattern in patterns.items():
                if group_id in found:
                    continue
                match = pattern.search(value)
                if not match:
                    continue
                name = self._clean_group_display_name(match.group(1), group_id)
                if name:
                    found[group_id] = name
        return found

    @staticmethod
    def _lookup_float(value: Any) -> float:
        try:
            return float(value or 0)
        except Exception:
            return 0.0

    def _page_onebot_call_actions(self) -> list[Any]:
        candidates: list[Any] = []
        finder = getattr(self.plugin, "_qzone_find_runtime_bot", None)
        if callable(finder):
            try:
                bot = finder()
                if bot is not None:
                    candidates.append(bot)
            except Exception:
                pass
        context = getattr(self.plugin, "context", None)
        if context is not None:
            try:
                platform = context.get_platform("aiocqhttp")
            except Exception:
                platform = None
            if platform is not None:
                candidates.append(platform)
                for attr in ("bot", "client", "adapter", "connection", "api"):
                    try:
                        value = getattr(platform, attr, None)
                    except Exception:
                        value = None
                    if value is not None:
                        candidates.append(value)
        platform_manager = getattr(context, "platform_manager", None) if context is not None else None
        for attr in ("platform_insts", "platform_instances", "instances", "platforms"):
            try:
                value = getattr(platform_manager, attr, None)
            except Exception:
                value = None
            if not value:
                continue
            try:
                iterable = value.values() if isinstance(value, dict) else value
                candidates.extend(list(iterable or []))
            except Exception:
                pass
        calls: list[Any] = []
        seen: set[int] = set()
        for candidate in candidates:
            if candidate is None or id(candidate) in seen:
                continue
            seen.add(id(candidate))
            api = getattr(candidate, "api", None)
            call_action = getattr(api, "call_action", None)
            if not callable(call_action):
                call_action = getattr(candidate, "call_action", None)
            if callable(call_action):
                calls.append(call_action)
        return calls

    async def _page_call_onebot_action(self, action: str, **kwargs: Any) -> Any:
        last_error: Exception | None = None
        for call_action in self._page_onebot_call_actions():
            try:
                result = call_action(action, **kwargs)
                return await result if hasattr(result, "__await__") else result
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError("没有可用的 OneBot call_action")

    async def _refresh_group_names_from_platform(self, visible_groups: list[tuple[str, dict[str, Any]]], *, force: bool = False) -> None:
        now = time.time()
        display_missing = [
            (str(group_id), group)
            for group_id, group in visible_groups
            if self._group_display_name_missing(str(group_id), group)
        ]
        if not display_missing:
            return
        target_ids = {group_id for group_id, _ in display_missing if group_id}
        found: dict[str, str] = self._group_names_from_loaded_history(target_ids)
        missing = [
            (group_id, group)
            for group_id, group in display_missing
            if group_id not in found
            and (force or now - self._lookup_float(group.get("last_group_name_lookup_at")) > 5 * 60)
        ]
        # QQ Official group_openid values are opaque and cannot be queried through
        # OneBot's get_group_list/get_group_info API. Their names stay event/manual driven.
        platform_target_ids = {group_id for group_id, _ in missing if group_id.isdigit()}
        try:
            if platform_target_ids:
                raw_groups = await self._page_call_onebot_action("get_group_list")
                for item in self._extract_onebot_list(raw_groups):
                    group_id = str(item.get("group_id") or item.get("group_uin") or item.get("group_no") or "").strip()
                    if group_id not in platform_target_ids:
                        continue
                    name = self._name_from_group_payload(item, group_id)
                    if name:
                        found[group_id] = name
        except Exception as exc:
            logger.info("[PrivateCompanionPage] 群列表名称刷新失败: %s", self._single_line(exc, 120))
        if len(found) < len(target_ids) and platform_target_ids:
            for group_id, _ in [item for item in missing if item[0] in platform_target_ids][:30]:
                if group_id in found:
                    continue
                try:
                    raw_item = await self._page_call_onebot_action("get_group_info", group_id=int(group_id) if group_id.isdigit() else group_id)
                except Exception:
                    continue
                item = self._extract_onebot_object(raw_item)
                if not isinstance(item, dict):
                    continue
                name = self._name_from_group_payload(item, group_id)
                if name:
                    found[group_id] = name
        if not found and not missing:
            return
        changed = False
        async with self.plugin._data_lock:
            groups = self.plugin.data.get("groups")
            if not isinstance(groups, dict):
                return
            for group_id, snapshot in display_missing:
                group = groups.get(group_id)
                if not isinstance(group, dict):
                    continue
                if self._single_line(group.get("manual_group_name"), 80):
                    continue
                if group_id in platform_target_ids:
                    group["last_group_name_lookup_at"] = now
                name = found.get(group_id, "")
                if name:
                    group["name"] = name
                    group["group_name"] = name
                    group["last_group_name_seen_at"] = now
                    snapshot["name"] = name
                    snapshot["group_name"] = name
                    snapshot["last_group_name_seen_at"] = now
                    changed = True
                if group_id in platform_target_ids:
                    snapshot["last_group_name_lookup_at"] = now
            if changed:
                self.plugin._save_data_sync(sections={"groups"})
    async def get_group(self) -> dict[str, Any]:
        group_id = self._normalize_page_group_id(request.args.get("group_id", ""))
        if not group_id:
            return self._error("缺少 group_id")
        try:
            async with self.plugin._data_lock:
                group = deepcopy((self.plugin.data.get("groups") or {}).get(group_id))
            if not isinstance(group, dict):
                return self._error("群不存在")
            await self._refresh_group_names_from_platform([(group_id, group)], force=True)
            self._refresh_group_atmosphere_for_page(group)
            identity_names = self._group_page_identity_names(group)
            detail = self._group_summary(group_id, group)
            safety_getter = getattr(self.plugin, "_group_member_safety_compact_summary", None)
            member_safety = safety_getter(group) if callable(safety_getter) else {}
            detail.update(
                {
                    "members": group.get("members") if isinstance(group.get("members"), dict) else {},
                    "recent_messages": self._group_page_recent_messages(group, identity_names),
                    "recent_bot_replies": self._group_page_recent_bot_replies(group),
                    "topic_threads": self._group_topic_thread_items(group),
                    "group_episodes": self._limited_list(group.get("group_episodes"), 12),
                    "relationship_edges": group.get("relationship_edges") if isinstance(group.get("relationship_edges"), dict) else {},
                    "interjection_feedback": group.get("interjection_feedback") if isinstance(group.get("interjection_feedback"), dict) else {},
                    "last_bot_interjection": self._sanitize_last_bot_interjection(group.get("last_bot_interjection")),
                    "group_wakeup_logs": self._group_wakeup_logs(group),
                    "slang_items": self._group_slang_items(group),
                    "member_safety": member_safety,
                    "formatted": {
                        "status": self.plugin._format_group_status(group),
                        "feedback": self.plugin._format_group_interjection_feedback(group),
                        "relationship_graph": self.plugin._format_group_relationship_graph_for_prompt(group),
                    },
                }
            )
            return self._ok(detail)
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 获取群详情失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def get_group_member_safety(self) -> dict[str, Any]:
        group_id = self._normalize_page_group_id(request.args.get("group_id", ""))
        if not group_id:
            return self._error("缺少 group_id")
        try:
            async with self.plugin._data_lock:
                group = deepcopy((self.plugin.data.get("groups") or {}).get(group_id))
            if not isinstance(group, dict):
                return self._error("群不存在")
            getter = getattr(self.plugin, "_group_member_safety_summary", None)
            if not callable(getter):
                return self._error("当前插件版本不支持成员风控")
            summary = getter(group)
            summary["group_id"] = group_id
            summary["group_name"] = self._single_line(
                group.get("manual_group_name") or group.get("name") or group.get("group_name") or group.get("display_name"),
                80,
            )
            return self._ok(summary)
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 获取成员风控失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def update_group_member_safety(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        group_id = self._normalize_page_group_id(payload.get("group_id", ""))
        user_id = str(payload.get("user_id", "")).strip()
        action = str(payload.get("action", "")).strip().lower()
        if not group_id:
            return self._error("缺少 group_id")
        if not user_id:
            return self._error("缺少成员 ID")
        if action not in {"manual_block", "unblock", "clear_strikes", "exempt", "unexempt"}:
            return self._error("不支持的成员风控操作")
        try:
            async with self.plugin._data_lock:
                group = self.plugin._get_group(group_id)
                profiles = group.get("members") if isinstance(group.get("members"), dict) else {}
                profile = profiles.get(user_id) if isinstance(profiles.get(user_id), dict) else {}
                name = self._single_line(payload.get("name") or profile.get("name") or profile.get("identity_name"), 60)
                updater = getattr(self.plugin, "_apply_group_member_safety_action", None)
                getter = getattr(self.plugin, "_group_member_safety_summary", None)
                if not callable(updater) or not callable(getter):
                    return self._error("当前插件版本不支持成员风控")
                item = updater(group, user_id=user_id, action=action, name=name)
                self.plugin._save_data_sync(sections={"groups"})
                summary = getter(group)
            return self._ok({"item": item, "summary": summary})
        except ValueError as exc:
            return self._error(str(exc))
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 更新成员风控失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def update_group(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        group_id = self._normalize_page_group_id(payload.get("group_id", ""))
        if not group_id:
            return self._error("缺少 group_id")
        try:
            async with self.plugin._data_lock:
                group = self.plugin._get_group(group_id)
                if "group_name" in payload or "name" in payload:
                    previous_manual_name = self._single_line(group.get("manual_group_name"), 80)
                    manual_name = self._clean_manual_group_display_name(
                        payload.get("group_name") if "group_name" in payload else payload.get("name"),
                        group_id,
                    )
                    if manual_name:
                        group["manual_group_name"] = manual_name
                        group["name"] = manual_name
                        group["group_name"] = manual_name
                        group["group_name_source"] = "manual"
                        group["manual_group_name_updated_at"] = time.time()
                    else:
                        group["manual_group_name"] = ""
                        group.pop("group_name_source", None)
                        group.pop("manual_group_name_updated_at", None)
                        if previous_manual_name and self._single_line(group.get("name"), 80) == previous_manual_name:
                            group.pop("name", None)
                        if previous_manual_name and self._single_line(group.get("group_name"), 80) == previous_manual_name:
                            group.pop("group_name", None)
                if "enabled" in payload:
                    group["enabled"] = bool(payload.get("enabled"))
                if payload.get("reset_interjection"):
                    group["last_interject_at"] = 0
                    group["interject_day"] = ""
                    group["interject_today"] = 0
                    group["last_bot_interjection"] = {}
                    group["interjection_feedback"] = {}
                if payload.get("reset_atmosphere"):
                    current_atmosphere = group.get("atmosphere") if isinstance(group.get("atmosphere"), dict) else {}
                    group["atmosphere"] = {**current_atmosphere, "reset_at": time.time()}
                    updater = getattr(self.plugin, "_update_group_atmosphere", None)
                    if callable(updater):
                        updater(group)
                    else:
                        group["atmosphere"].update(
                            {
                                "pace": "安静",
                                "mood": "平稳",
                                "active_speakers": 0,
                                "recent_count": 0,
                                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                            }
                        )
                if payload.get("clear_observation"):
                    enabled = bool(group.get("enabled", True))
                    manual_group_name = self._single_line(group.get("manual_group_name"), 80)
                    group.clear()
                    group.update(
                        {
                            "enabled": enabled,
                            "group_id": group_id,
                            "manual_group_name": manual_group_name,
                            "name": manual_group_name,
                            "group_name": manual_group_name,
                            "group_name_source": "manual" if manual_group_name else "",
                            "message_count": 0,
                            "last_seen": 0,
                            "last_interject_at": 0,
                            "interject_day": "",
                            "interject_today": 0,
                            "recent_messages": [],
                            "recent_bot_replies": [],
                            "members": {},
                            "member_safety": {},
                            "slang_terms": [],
                            "slang_meanings": {},
                            "topic_signatures": [],
                            "topic_threads": [],
                            "group_episodes": [],
                            "relationship_edges": {},
                            "interjection_feedback": {},
                            "last_bot_interjection": {},
                            "last_speaker": {},
                            "active_bot_conversation": {},
                            "atmosphere": {},
                            "last_summary_at": 0,
                            "last_episode_refresh_at": 0,
                            "last_slang_summary_at": 0,
                        }
                    )
                    voice_refresher = getattr(self.plugin, "_refresh_expression_voice_profile", None)
                    if callable(voice_refresher):
                        voice_refresher()
                save_sections = {"groups"}
                if payload.get("clear_observation") and callable(
                    getattr(self.plugin, "_refresh_expression_voice_profile", None)
                ):
                    save_sections.add("expression_voice_profile")
                self.plugin._save_data_sync(sections=save_sections)
                snapshot = deepcopy(group)
            return self._ok(self._group_summary(group_id, snapshot))
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 更新群失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def delete_group(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        group_id = self._normalize_page_group_id(payload.get("group_id", ""))
        if not group_id:
            return self._error("缺少 group_id")
        try:
            resetter = getattr(self.plugin, "reset_group_scoped_data", None)
            if callable(resetter):
                reset_result = await resetter(group_id)
                if not reset_result.get("ok"):
                    return self._error(str(reset_result.get("code") or "群聊分域清理失败"))
                if reset_result.get("state") != "not_required":
                    message_parts = []
                    if reset_result.get("removed_group"):
                        message_parts.append("已删除群聊观测")
                    if reset_result.get("removed_whitelist") or reset_result.get("removed_blacklist"):
                        message_parts.append("已移出群聊名单")
                    if reset_result.get("removed_expression_scope"):
                        message_parts.append("已清理表达学习范围")
                    return self._ok({
                        "group_id": group_id,
                        "removed_group": bool(reset_result.get("removed_group")),
                        "removed_whitelist": bool(reset_result.get("removed_whitelist")),
                        "removed_blacklist": bool(reset_result.get("removed_blacklist")),
                        "removed_expression_scope": bool(reset_result.get("removed_expression_scope")),
                        "config_saved": bool(reset_result.get("config_saved")),
                        "scoped_cleanup": reset_result.get("scoped_cleanup") or {},
                        "operation_id": str(reset_result.get("operation_id") or ""),
                        "message": "，".join(message_parts) if message_parts else "没有找到可删除的群聊记录",
                    })
            async with self.plugin._data_lock:
                groups = self.plugin.data.get("groups")
                groups_repaired = not isinstance(groups, dict)
                if not isinstance(groups, dict):
                    groups = {}
                    self.plugin.data["groups"] = groups
                removed_group = groups.pop(group_id, None) is not None

                old_expression_learning_ids = self._normalize_id_list(
                    getattr(self.plugin, "expression_group_learning_source_ids", []) or []
                )
                old_expression_application_ids = self._normalize_id_list(
                    getattr(self.plugin, "expression_group_application_ids", []) or []
                )
                expression_learning_ids = [
                    item
                    for item in old_expression_learning_ids
                    if self._normalize_page_group_id(item) != group_id
                ]
                expression_application_ids = [
                    item
                    for item in old_expression_application_ids
                    if self._normalize_page_group_id(item) != group_id
                ]
                removed_expression_scope = (
                    expression_learning_ids != old_expression_learning_ids
                    or expression_application_ids != old_expression_application_ids
                )

                whitelist = [
                    str(item).strip()
                    for item in (getattr(self.plugin, "group_whitelist_ids", []) or [])
                    if str(item).strip() and self._normalize_page_group_id(item) != group_id
                ]
                blacklist = [
                    str(item).strip()
                    for item in (getattr(self.plugin, "group_blacklist_ids", []) or [])
                    if str(item).strip() and self._normalize_page_group_id(item) != group_id
                ]
                removed_whitelist = len(whitelist) != len(getattr(self.plugin, "group_whitelist_ids", []) or [])
                removed_blacklist = len(blacklist) != len(getattr(self.plugin, "group_blacklist_ids", []) or [])
                self._apply_config_value("group_whitelist_ids", whitelist, {"group_whitelist_ids": whitelist, "group_blacklist_ids": blacklist})
                self._apply_config_value("group_blacklist_ids", blacklist, {"group_whitelist_ids": whitelist, "group_blacklist_ids": blacklist})
                expression_overrides = {
                    "expression_group_learning_source_ids": expression_learning_ids,
                    "expression_group_application_ids": expression_application_ids,
                }
                self._apply_config_value("expression_group_learning_source_ids", expression_learning_ids, expression_overrides)
                self._apply_config_value("expression_group_application_ids", expression_application_ids, expression_overrides)
                save_sections: set[str] = set()
                if removed_group or groups_repaired:
                    save_sections.add("groups")
                if removed_group or removed_expression_scope:
                    voice_refresher = getattr(self.plugin, "_refresh_expression_voice_profile", None)
                    if callable(voice_refresher):
                        voice_refresher()
                        save_sections.add("expression_voice_profile")
                if save_sections:
                    self.plugin._save_data_sync(sections=save_sections)

            config_saved = await self._save_config_if_possible()
            message_parts = []
            if removed_group:
                message_parts.append("已删除群聊观测")
            if removed_whitelist or removed_blacklist:
                message_parts.append("已移出群聊名单")
            if removed_expression_scope:
                message_parts.append("已清理表达学习范围")
            message = "，".join(message_parts) if message_parts else "没有找到可删除的群聊记录"
            return self._ok(
                {
                    "group_id": group_id,
                    "removed_group": removed_group,
                    "removed_whitelist": removed_whitelist,
                    "removed_blacklist": removed_blacklist,
                    "removed_expression_scope": removed_expression_scope,
                    "config_saved": config_saved,
                    "scoped_cleanup": {
                        "ok": True, "code": "scoped_group_erase_not_required", "count": 0,
                    },
                    "message": message,
                }
            )
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 删除群失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def update_group_slang(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        group_id = self._normalize_page_group_id(payload.get("group_id", ""))
        term = self._single_line(payload.get("term"), 40)
        if not group_id:
            return self._error("缺少 group_id")
        if not term:
            return self._error("缺少黑话词")
        try:
            async with self.plugin._data_lock:
                group = self.plugin._get_group(group_id)
                terms = group.setdefault("slang_terms", [])
                if not isinstance(terms, list):
                    terms = []
                    group["slang_terms"] = terms
                meanings = group.setdefault("slang_meanings", {})
                if not isinstance(meanings, dict):
                    meanings = {}
                    group["slang_meanings"] = meanings

                if payload.get("delete"):
                    group["slang_terms"] = [
                        item
                        for item in terms
                        if self._single_line(item.get("term") if isinstance(item, dict) else item, 40) != term
                    ]
                    meanings.pop(term, None)
                else:
                    existing_term = None
                    for item in terms:
                        if isinstance(item, dict) and self._single_line(item.get("term"), 40) == term:
                            existing_term = item
                            break
                    if existing_term is None:
                        existing_term = {"term": term, "count": 0, "last_seen": 0}
                        terms.append(existing_term)
                    previous = meanings.get(term) if isinstance(meanings.get(term), dict) else {}
                    confidence_raw = payload.get("confidence") if "confidence" in payload else previous.get("confidence", 0.85)
                    web_match_raw = payload.get("web_match") if "web_match" in payload else previous.get("web_match", 0.0)
                    confidence = max(0.0, min(1.0, self._float(confidence_raw)))
                    web_match = max(0.0, min(1.0, self._float(web_match_raw)))
                    meanings[term] = {
                        "meaning": self._single_line(payload.get("meaning"), 120),
                        "usage": self._single_line(payload.get("usage"), 120),
                        "type": self._single_line(payload.get("type"), 24),
                        "not_owner": self._single_line(payload.get("not_owner"), 90),
                        "evidence": self._single_line(payload.get("evidence"), 160),
                        "web_evidence": self._single_line(payload.get("web_evidence"), 220),
                        "confidence": f"{confidence:.2f}",
                        "web_match": f"{web_match:.2f}" if web_match > 0 else "",
                        "source": "manual",
                        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    }
                    terms.sort(key=lambda item: (_safe_int(item.get("count"), 0) if isinstance(item, dict) else 0), reverse=True)
                self.plugin._save_data_sync(sections={"groups"})
                snapshot = deepcopy(group)
            detail = self._group_summary(group_id, snapshot)
            detail["slang_items"] = self._group_slang_items(snapshot)
            return self._ok(detail)
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 更新群黑话失败: {exc}", exc_info=True)
            return self._error(str(exc))
