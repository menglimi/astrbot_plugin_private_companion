# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiUsersGroupsPart01Mixin。

由 tools/split_mixin_domain.py 从 page_api_users_groups.py 机械抽取（17 个方法 + 0 个模块级名字 + 0 个类级赋值 / 574 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiUsersGroupsMixin）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from .emotion_diagnostics import build_emotion_trace_projection, emotion_trace_summary
from .helpers import _safe_int
from .migration_backfill import legacy_pending_reference
from .page_api_shared import _page_api_host_request as request
from .page_api_users_groups_shared import logger
from copy import deepcopy
from typing import Any



class PrivateCompanionPageApiUsersGroupsPart01Mixin:
    """PrivateCompanionPageApiUsersGroupsPart01Mixin（从 PrivateCompanionPageApiUsersGroupsMixin 拆出）。"""


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
            logger.warning("更新待确认身份状态失败: %s", exc)
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
        archive_status = self._identity_archive_status()
        archive_ready = archive_status["ready"]
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
            "archive_code": archive_status["code"],
            "archive_reason": archive_status["reason"],
            "archive_recovery": archive_status["recovery"],
            "can_purge": bool(summary.get("profile_status") == "deleted"),
            "can_relink_current": bool(
                summary.get("current_identity_detached")
                and summary.get("profile_status") == "active"
            ),
        }
        return summary

    def _identity_archive_remote_available(self) -> bool:
        """Only enable the destructive archive action when scoped cleanup is bound."""
        checker = getattr(self.plugin, "_req041_scoped_archive_available", None)
        if not callable(checker):
            # Compatibility with older plugin instances that predate the
            # scoped cleanup readiness probe.
            return True
        try:
            return bool(checker())
        except Exception:
            return False

    def _identity_archive_status(self) -> dict[str, Any]:
        if self._identity_archive_remote_available():
            return {"ready": True, "code": "ready", "reason": "", "recovery": ""}
        synchronizer = getattr(self.plugin, "req041_scoped_projection_sync", None)
        if not callable(getattr(synchronizer, "archive_identity_scopes", None)):
            return {
                "ready": False,
                "code": "scoped_archive_bridge_unavailable",
                "reason": "未连接支持统一人物作用域归档的记忆桥接服务。",
                "recovery": "请启用支持 scoped archive 的 Memory Companion / Remember You 桥接并重新加载插件。仅安装 LivingMemory 不代表已具备此能力；外部记忆不会被自动删除。",
            }
        status = getattr(self.plugin, "req041_migration_status", None)
        state = str(status.get("state") or "").strip().lower() if isinstance(status, dict) else ""
        labels = {"degraded": "降级", "paused": "暂停", "stopped": "停止"}
        return {
            "ready": False,
            "code": f"scoped_archive_{state}" if state in labels else "scoped_archive_not_ready",
            "reason": f"统一身份迁移服务处于{labels[state]}状态。" if state in labels else "统一身份归档服务尚未就绪。",
            "recovery": "请在排障页检查记忆桥接和身份迁移状态，恢复服务后刷新页面。",
        }

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
                logger.warning("用户列表接口耗时较高: elapsed=%sms users=%s", elapsed_ms, len(items))
            return self._ok({"items": items[:limit], "total": len(items)})
        except Exception as exc:
            logger.error(f"获取用户列表失败: {exc}", exc_info=True)
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
            # A page read can arrive before the background retry observes a
            # late-loaded MemoryCompanion. Trigger one immediate bind attempt
            # so the identity panel reflects the current connection state.
            if getattr(self.plugin, "req041_scoped_projection_sync", None) is None:
                rebind = getattr(self.plugin, "_req041_rebind_memory_scope_if_available", None)
                if callable(rebind):
                    try:
                        await rebind()
                    except Exception as exc:
                        logger.debug("[PrivateCompanionPage] 读取用户详情时补绑定记忆作用域失败: %s", exc)
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
            logger.error(f"获取用户详情失败: {exc}", exc_info=True)
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
            logger.warning("统一身份重新关联失败: %s", exc)
            return self._error("统一身份重新关联失败")
