# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiExpressionReadWriteApplyMixin。

由 tools/split_mixin_domain.py 从 page_api_expression.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 674 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiExpressionMixin）。
"""
from __future__ import annotations

from .page_api_expression_shared import logger
from .page_api_expression_shared import Any
from .page_api_expression_shared import ExpressionScopeError
from .page_api_expression_shared import datetime
from .page_api_expression_shared import deepcopy
from .page_api_expression_shared import hashlib
from .page_api_expression_shared import hmac
from .page_api_expression_shared import re
from .page_api_expression_shared import request
from .page_api_expression_shared import time



class PrivateCompanionPageApiExpressionReadWriteApplyMixin:
    """PrivateCompanionPageApiExpressionReadWriteApplyMixin（从 PrivateCompanionPageApiExpressionMixin 拆出）。"""


    async def get_expression_library(self) -> dict[str, Any]:
        try:
            async with self.plugin._data_lock:
                # Read endpoints may prepare a disposable view, but must never repair
                # or persist the live runtime state as a side effect of a GET request.
                snapshot = deepcopy(self.plugin.data)
                normalizer = getattr(self.plugin, "_normalize_group_expression_profile", None)
                pruner = getattr(self.plugin, "_prune_invalid_expression_rules", None)
                family_backfiller = getattr(self.plugin, "_backfill_expression_rule_families", None)
                for collection_key in ("users", "groups"):
                    collection = snapshot.get(collection_key)
                    if not isinstance(collection, dict):
                        continue
                    source_type = "group" if collection_key == "groups" else "private"
                    for source_id, item in collection.items():
                        profile = item.get("expression_profile") if isinstance(item, dict) else None
                        if not isinstance(profile, dict):
                            continue
                        if collection_key == "groups" and callable(normalizer):
                            normalizer(profile)
                        if callable(pruner):
                            pruner(profile)
                        if callable(family_backfiller):
                            family_backfiller(profile)
                        try:
                            managed, scope_context = self._expression_admin_scope_context(
                                source_type, self._single_line(source_id, 80), item,
                            )
                            if managed:
                                self._expression_prepare_admin_profile(item, scope_context)
                        except (ExpressionScopeError, ValueError):
                            # Pending/unresolved legacy sources remain visible but cannot be mutated.
                            pass
            return self._ok(self._expression_library_summary(snapshot))
        except Exception as exc:
            logger.error(f"获取统一表达学习库失败: {exc}", exc_info=True)
            return self._exception_error("获取统一表达学习库失败")

    async def update_expression_library(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        source_type = self._single_line(payload.get("source_type"), 16)
        source_id = self._single_line(payload.get("source_id"), 80)
        action = self._single_line(payload.get("expression_action"), 40)
        if action == "promote_rule_group":
            family_id = self._single_line(payload.get("rule_family_id"), 100)
            operation_id = self._single_line(payload.get("operation_id"), 120)
            confirmation_token = self._single_line(payload.get("confirmation_token"), 80)
            if "dry_run" in payload and type(payload.get("dry_run")) is not bool:
                return self._error("dry_run 必须是 JSON 布尔值")
            dry_run = payload.get("dry_run", True)
            if not source_id or not family_id or not operation_id:
                return self._error("缺少规则来源、规则组或操作标识")
            if not dry_run and not confirmation_token:
                return self._error("提升为全局规则前必须先生成预览")
            try:
                async with self.plugin._data_lock:
                    operations = self.plugin.data.get("_req041_expression_promotion_operations")
                    if not isinstance(operations, dict):
                        operations = {}
                    prior = operations.get(operation_id)
                    token_hash = hashlib.sha256(confirmation_token.encode("utf-8")).hexdigest()
                    if not dry_run and isinstance(prior, dict):
                        if not hmac.compare_digest(
                            self._single_line(prior.get("confirmation_token_hash"), 80), token_hash
                        ):
                            return self._error("操作标识已用于另一份全局提升请求")
                        snapshot = deepcopy(self.plugin.data)
                        result = self._expression_library_summary(snapshot)
                        result["promotion"] = {
                            "ok": True,
                            "code": "persona_global_promotion_replayed",
                            "rule_count": self._int(prior.get("rule_count")),
                        }
                        result["message"] = "该全局提升已完成，无需重复操作"
                        return self._ok(result)
                    prepared = self._expression_global_promotion_state(
                        source_type=source_type,
                        source_id=source_id,
                        family_id=family_id,
                        operation_id=operation_id,
                        payload=payload,
                    )
                    expected = prepared["confirmation_token"]
                    if dry_run:
                        return self._ok({
                            "promotion": {
                                "ok": True,
                                "code": "persona_global_promotion_preview",
                                "rule_count": len(prepared["rules"]),
                                "target_scope_revision": self._int(
                                    prepared["target_profile"].get("scope_revision")
                                ),
                                "confirmation_token": expected,
                            }
                        })
                    if not hmac.compare_digest(confirmation_token, expected):
                        return self._error("规则来源或全局规则库已变化，请重新预览")
                    global_owner = prepared["global_owner"]
                    before = deepcopy(prepared["target_profile"])
                    learned = before.get("learned_rules") if isinstance(before.get("learned_rules"), list) else []
                    existing_ids = {
                        self._single_line(item.get("id"), 100)
                        for item in learned if isinstance(item, dict)
                    }
                    inserted = [
                        {**item, "approved_at": datetime.now().strftime("%Y-%m-%d %H:%M")}
                        for item in prepared["rules"]
                        if self._single_line(item.get("id"), 100) not in existing_ids
                    ]
                    if inserted:
                        learned = inserted + learned
                        limit = max(12, int(getattr(self.plugin, "max_learned_expression_items", 60) or 60))
                        global_owner["expression_profile"] = {
                            **before,
                            "learned_rules": learned[:limit],
                            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        }
                        self._expression_finalize_admin_profile(
                            global_owner, before, prepared["persona_context"],
                        )
                        self.plugin.data["_req041_persona_expression_profile"] = global_owner["expression_profile"]
                        operations[operation_id] = {
                            "confirmation_token_hash": token_hash,
                            "rule_count": len(inserted),
                            "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        }
                        while len(operations) > 128:
                            operations.pop(next(iter(operations)))
                        self.plugin.data["_req041_expression_promotion_operations"] = operations
                        self.plugin._save_data_sync(
                            sections={
                                "_req041_persona_expression_profile",
                                "_req041_expression_promotion_operations",
                            }
                        )
                    snapshot = deepcopy(self.plugin.data)
                result = self._expression_library_summary(snapshot)
                result["promotion"] = {
                    "ok": True,
                    "code": "persona_global_promoted" if inserted else "persona_global_already_present",
                    "rule_count": len(inserted),
                }
                result["message"] = (
                    f"已将 {len(inserted)} 条规则显式提升为当前人格全局规则"
                    if inserted else "当前人格全局规则库已包含同一规则组"
                )
                return self._ok(result)
            except (ExpressionScopeError, ValueError) as exc:
                return self._error(str(exc))
            except Exception as exc:
                logger.error("提升人格全局表达规则失败: %s", exc, exc_info=True)
                return self._exception_error("提升人格全局表达规则失败")
        if action in {"batch_approve_rule_groups", "batch_reject_rule_groups"}:
            raw_items = payload.get("items")
            if not isinstance(raw_items, list) or not raw_items:
                return self._error("请至少选择一个待审核规则组")
            if len(raw_items) > 200:
                return self._error("单次最多审核 200 个规则组")
            try:
                async with self.plugin._data_lock:
                    results: list[dict[str, Any]] = []
                    seen: set[tuple[str, str, str]] = set()
                    changed = False
                    save_sections: set[str] = set()
                    for raw_item in raw_items:
                        if not isinstance(raw_item, dict):
                            continue
                        target_type = self._single_line(raw_item.get("source_type"), 16)
                        target_id = self._single_line(raw_item.get("source_id"), 80)
                        family_id = self._single_line(raw_item.get("rule_family_id"), 100)
                        target_key = (target_type, target_id, family_id)
                        if target_key in seen:
                            continue
                        seen.add(target_key)
                        if target_type not in {"private", "group"} or not target_id or not family_id:
                            results.append({"status": "skipped", "reason": "目标标识不完整"})
                            continue
                        collection_key = "groups" if target_type == "group" else "users"
                        collection = self.plugin.data.get(collection_key)
                        item = collection.get(target_id) if isinstance(collection, dict) else None
                        if not isinstance(item, dict):
                            results.append({"status": "skipped", "reason": "来源不存在", "source_id": target_id})
                            continue
                        mutation_before = deepcopy(item.get("expression_profile") or {})
                        if target_type == "group":
                            normalizer = getattr(self.plugin, "_normalize_group_expression_profile", None)
                            profile = item.get("expression_profile")
                            if callable(normalizer) and isinstance(profile, dict):
                                normalizer(profile)
                        try:
                            managed, scope_context = self._expression_admin_scope_context(
                                target_type, target_id, item,
                            )
                            if managed:
                                prepared = self._expression_prepare_admin_profile(item, scope_context)
                                self._expression_validate_admin_revision(prepared, raw_item)
                            before = deepcopy(item.get("expression_profile") or {})
                            result_message = self._apply_expression_profile_action(
                                item,
                                {
                                "expression_action": "approve_rule_group"
                                if action == "batch_approve_rule_groups" else "reject_rule_group",
                                "rule_family_id": family_id,
                                },
                            )
                            profile_changed = mutation_before != (item.get("expression_profile") or {})
                            if managed and profile_changed:
                                try:
                                    self._expression_finalize_admin_profile(item, before, scope_context)
                                except Exception:
                                    item["expression_profile"] = before
                                    raise
                        except (ExpressionScopeError, ValueError) as exc:
                            results.append({
                                "status": "skipped", "reason": str(exc),
                                "source_type": target_type, "source_id": target_id,
                                "rule_family_id": family_id,
                            })
                            continue
                        succeeded = not result_message.startswith(("没有找到", "缺少", "规则组中没有"))
                        if profile_changed:
                            save_sections.add(collection_key)
                            if succeeded:
                                changed = True
                        results.append(
                            {
                                "status": "success" if succeeded else "skipped",
                                "source_type": target_type,
                                "source_id": target_id,
                                "rule_family_id": family_id,
                                "message": result_message,
                            }
                        )
                    if changed and action == "batch_approve_rule_groups":
                        refresher = getattr(self.plugin, "_refresh_expression_voice_profile", None)
                        if callable(refresher):
                            refresher()
                        save_sections.add("expression_voice_profile")
                    if save_sections:
                        self.plugin._save_data_sync(sections=save_sections)
                    snapshot = deepcopy(self.plugin.data)
                result = self._expression_library_summary(snapshot)
                success_count = sum(1 for item in results if item.get("status") == "success")
                skipped_count = len(results) - success_count
                verb = "通过" if action == "batch_approve_rule_groups" else "拒绝"
                result["message"] = f"已{verb} {success_count} 个规则组" + (f"，跳过 {skipped_count} 个" if skipped_count else "")
                result["batch"] = {
                    "requested": len(seen),
                    "succeeded": success_count,
                    "skipped": skipped_count,
                    "results": results,
                }
                return self._ok(result)
            except Exception as exc:
                logger.error(f"批量审核表达规则失败: {exc}", exc_info=True)
                return self._error(str(exc))
        if action == "clear_all_pending":
            try:
                async with self.plugin._data_lock:
                    cleared = 0
                    save_sections: set[str] = set()
                    for collection_key in ("users", "groups"):
                        collection = self.plugin.data.get(collection_key)
                        if not isinstance(collection, dict):
                            continue
                        source_type = "group" if collection_key == "groups" else "private"
                        for source_id, item in collection.items():
                            if not isinstance(item, dict):
                                continue
                            profile = item.get("expression_profile")
                            pending = profile.get("pending_samples") if isinstance(profile, dict) else None
                            pending_rules = profile.get("pending_rules") if isinstance(profile, dict) else None
                            item_count = (len(pending) if isinstance(pending, list) else 0) + (
                                len(pending_rules) if isinstance(pending_rules, list) else 0
                            )
                            if item_count:
                                try:
                                    mutation_before = deepcopy(item.get("expression_profile") or {})
                                    managed, scope_context = self._expression_admin_scope_context(
                                        source_type, self._single_line(source_id, 80), item,
                                    )
                                    if managed:
                                        self._expression_prepare_admin_profile(item, scope_context)
                                    before = deepcopy(item.get("expression_profile") or {})
                                    self._apply_expression_profile_action(item, {"expression_action": "clear_pending"})
                                    if managed and before != (item.get("expression_profile") or {}):
                                        try:
                                            self._expression_finalize_admin_profile(item, before, scope_context)
                                        except Exception:
                                            item["expression_profile"] = before
                                            raise
                                    if mutation_before != (item.get("expression_profile") or {}):
                                        save_sections.add(collection_key)
                                    cleared += item_count
                                except (ExpressionScopeError, ValueError):
                                    continue
                    if save_sections:
                        self.plugin._save_data_sync(sections=save_sections)
                    snapshot = deepcopy(self.plugin.data)
                result = self._expression_library_summary(snapshot)
                result["message"] = f"已清空 {cleared} 条待审核表达资料"
                return self._ok(result)
            except Exception as exc:
                logger.error(f"清空统一表达待审样本失败: {exc}", exc_info=True)
                return self._exception_error("清空统一表达待审样本失败")
        if source_type not in {"private", "group", "persona"} or not source_id:
            return self._error("缺少有效的表达样本来源")
        if action not in {
            "approve", "reject", "approve_rule", "reject_rule", "delete_sample", "delete_rule",
            "approve_rule_group", "reject_rule_group", "delete_rule_group", "update_rule_group",
        }:
            return self._error("不支持的表达样本操作")
        try:
            async with self.plugin._data_lock:
                persona_target = source_type == "persona"
                collection_key = "groups" if source_type == "group" else "users"
                collection = self.plugin.data.get(collection_key)
                item = (
                    {
                        "expression_profile": deepcopy(
                            self.plugin.data.get("_req041_persona_expression_profile")
                        )
                    }
                    if persona_target
                    and source_id == "current-persona"
                    and isinstance(self.plugin.data.get("_req041_persona_expression_profile"), dict)
                    else collection.get(source_id) if isinstance(collection, dict) else None
                )
                if not isinstance(item, dict):
                    return self._error("表达样本来源不存在")
                mutation_before = deepcopy(item.get("expression_profile") or {})
                if source_type == "group":
                    normalizer = getattr(self.plugin, "_normalize_group_expression_profile", None)
                    profile = item.get("expression_profile")
                    if callable(normalizer) and isinstance(profile, dict):
                        normalizer(profile)
                managed, scope_context = self._expression_admin_scope_context(
                    source_type, source_id, item,
                )
                if managed:
                    prepared = self._expression_prepare_admin_profile(item, scope_context)
                    self._expression_validate_admin_revision(prepared, payload)
                before = deepcopy(item.get("expression_profile") or {})
                payload["source_type"] = source_type
                action_message = self._apply_expression_profile_action(item, payload)
                if managed and before != (item.get("expression_profile") or {}):
                    try:
                        self._expression_finalize_admin_profile(item, before, scope_context)
                    except Exception:
                        item["expression_profile"] = before
                        raise
                mutation_changed = mutation_before != (item.get("expression_profile") or {})
                if persona_target:
                    self.plugin.data["_req041_persona_expression_profile"] = item["expression_profile"]
                voice_changed = False
                if action in {
                    "approve", "approve_rule", "approve_rule_group", "delete_sample", "delete_rule", "delete_rule_group",
                    "update_rule_group",
                }:
                    voice_before = deepcopy(self.plugin.data.get("expression_voice_profile"))
                    voice_refresher = getattr(self.plugin, "_refresh_expression_voice_profile", None)
                    if callable(voice_refresher):
                        voice_refresher()
                        voice_changed = voice_before != self.plugin.data.get("expression_voice_profile")
                save_sections = {
                    "_req041_persona_expression_profile" if persona_target else collection_key
                } if mutation_changed else set()
                if voice_changed:
                    save_sections.add("expression_voice_profile")
                if save_sections:
                    self.plugin._save_data_sync(sections=save_sections)
                snapshot = deepcopy(self.plugin.data)
            result = self._expression_library_summary(snapshot)
            result["message"] = action_message
            return self._ok(result)
        except ValueError as exc:
            return self._error(str(exc))
        except Exception as exc:
            logger.error(f"更新统一表达学习库失败: {exc}", exc_info=True)
            return self._exception_error("更新统一表达学习库失败")

    def _apply_expression_profile_action(self, user: dict[str, Any], payload: dict[str, Any]) -> str:
        profile = user.setdefault("expression_profile", {})
        if not isinstance(profile, dict):
            profile = {}
            user["expression_profile"] = profile
        action = self._single_line(payload.get("expression_action"), 40)
        family_backfiller = getattr(self.plugin, "_backfill_expression_rule_families", None)
        if callable(family_backfiller):
            family_backfiller(profile)
        pending = profile.get("pending_samples") if isinstance(profile.get("pending_samples"), list) else []
        pending_rules = profile.get("pending_rules") if isinstance(profile.get("pending_rules"), list) else []
        samples = profile.get("samples") if isinstance(profile.get("samples"), list) else []
        sample_id = self._single_line(payload.get("sample_id"), 40)
        rule_id = self._single_line(payload.get("rule_id"), 100)
        rule_family_id = self._single_line(payload.get("rule_family_id"), 100)
        sample_index = self._int(payload.get("sample_index"))

        def archive_items(storage_key: str, items: list[Any], state: str) -> None:
            archived = profile.get(storage_key) if isinstance(profile.get(storage_key), list) else []
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            for raw in items:
                if not isinstance(raw, dict):
                    continue
                item = dict(raw)
                item["review_status"] = state
                item[f"{state}_at"] = stamp
                archived.insert(0, item)
            limit = max(24, int(getattr(self.plugin, "max_learned_expression_items", 60) or 60) * 2)
            profile[storage_key] = archived[:limit]

        def find_index(items: list[Any]) -> int:
            if sample_id:
                for idx, item in enumerate(items):
                    if isinstance(item, dict) and self._single_line(item.get("id"), 40) == sample_id:
                        return idx
            if 0 <= sample_index < len(items):
                return sample_index
            return -1

        def find_rule_index(items: list[Any]) -> int:
            if not rule_id:
                return -1
            for idx, item in enumerate(items):
                if isinstance(item, dict) and self._single_line(item.get("id"), 100) == rule_id:
                    return idx
            return -1

        if action == "clear_pending":
            archive_items("rejected_samples", pending, "rejected")
            archive_items("rejected_rules", pending_rules, "rejected")
            profile["pending_samples"] = []
            profile["pending_rules"] = []
            profile["pending_count"] = 0
            profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            return "已清空待审核表达资料"
        if action in {"approve", "reject"}:
            idx = find_index(pending)
            if idx < 0:
                return "没有找到待审核样本"
            item = pending.pop(idx)
            profile["pending_samples"] = pending
            profile["pending_count"] = len(pending)
            if action == "reject":
                archive_items("rejected_samples", [item], "rejected")
                profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                return "已删除待审核样本"
            if isinstance(item, dict):
                approved = dict(item)
                approved.pop("review_status", None)
                approved["approved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                samples.insert(0, approved)
                limit = max(12, int(getattr(self.plugin, "max_learned_expression_items", 60) or 60))
                profile["samples"] = samples[:limit]
                refresher = getattr(self.plugin, "_refresh_expression_profile_legacy_summary", None)
                if callable(refresher):
                    refresher(profile)
                profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                voice_refresher = getattr(self.plugin, "_refresh_expression_voice_profile", None)
                if callable(voice_refresher):
                    voice_refresher()
                return "已通过表达样本"
            return "待审核样本格式异常"
        if action in {"approve_rule", "reject_rule"}:
            idx = find_rule_index(pending_rules)
            if idx < 0:
                return "没有找到待审核规则"
            item = pending_rules.pop(idx)
            profile["pending_rules"] = pending_rules
            if action == "reject_rule":
                archive_items("rejected_rules", [item], "rejected")
                profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                return "已拒绝归纳规则"
            if not isinstance(item, dict):
                return "待审核规则格式异常"
            validator = getattr(self.plugin, "_expression_rule_definition_is_valid", None)
            if callable(validator) and not validator(item):
                profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                return "该规则不是可复用表达或具体语法，已从待审核区移除"
            approved = dict(item)
            approved["review_status"] = "approved"
            if self._single_line(item.get("review_status"), 24).lower() == "needs_review":
                approved["negative_feedback_before_review"] = self._int(item.get("negative_feedback"))
                approved["negative_feedback"] = 0
                approved["review_cycles"] = self._int(item.get("review_cycles")) + 1
                approved.pop("review_reason", None)
            approved["approved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            merger = getattr(self.plugin, "_merge_learned_expression_rules", None)
            if callable(merger):
                merger(
                    profile,
                    [approved],
                    batch_key=f"approve:{self._single_line(item.get('last_batch_key'), 40) or rule_id}",
                    now=time.time(),
                )
            else:
                learned_rules = profile.get("learned_rules") if isinstance(profile.get("learned_rules"), list) else []
                learned_rules.insert(0, approved)
                profile["learned_rules"] = learned_rules[: max(12, int(getattr(self.plugin, "max_learned_expression_items", 60) or 60))]
            profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            return "已通过表达规则，后续匹配情境时可以使用"
        if action == "update_rule_group":
            if not rule_family_id:
                raise ValueError("缺少规则组标识")
            rule_storage = self._single_line(payload.get("rule_storage"), 16).lower()
            if rule_storage not in {"pending", "learned"}:
                raise ValueError("缺少有效的规则组状态")
            storage_key = "pending_rules" if rule_storage == "pending" else "learned_rules"
            stored_rules = profile.get(storage_key) if isinstance(profile.get(storage_key), list) else []
            matched_indexes = [
                idx
                for idx, item in enumerate(stored_rules)
                if isinstance(item, dict) and self._single_line(item.get("family_id"), 100) == rule_family_id
            ]
            if not matched_indexes:
                raise ValueError("没有找到要编辑的表达规则组，可能已在其他页面中被处理，请刷新后重试")

            situation = self._single_line(payload.get("situation"), 100)
            label = self._single_line(payload.get("label"), 100) or situation
            avoid = self._single_line(payload.get("avoid"), 160) or "事实、工具结果、安全边界或人格发生冲突时不用"
            if not situation:
                raise ValueError("适用情境不能为空")

            raw_signals = payload.get("signals")
            if isinstance(raw_signals, str):
                raw_signals = re.split(r"[,，/、|\s]+", raw_signals)
            signals: list[str] = []
            for raw_signal in raw_signals if isinstance(raw_signals, list) else []:
                signal = self._single_line(raw_signal, 24)
                if signal and signal not in signals:
                    signals.append(signal)
                if len(signals) >= 8:
                    break

            component_payloads = {
                "style": payload.get("style_rule") if isinstance(payload.get("style_rule"), dict) else None,
                "grammar": payload.get("grammar_rule") if isinstance(payload.get("grammar_rule"), dict) else None,
            }
            validator = getattr(self.plugin, "_expression_rule_definition_is_valid", None)
            updated_rules: list[tuple[int, dict[str, Any]]] = []
            edited_at = datetime.now().strftime("%Y-%m-%d %H:%M")
            for idx in matched_indexes:
                current = stored_rules[idx]
                kind = self._single_line(current.get("kind"), 16).lower()
                component = component_payloads.get(kind)
                if kind not in {"style", "grammar"} or not isinstance(component, dict):
                    raise ValueError("规则组组件不完整，请刷新页面后重试")
                pattern = self._single_line(component.get("pattern"), 100)
                instruction = self._single_line(component.get("instruction"), 160)
                candidate = dict(current)
                candidate.update(
                    {
                        "label": label,
                        "situation": situation,
                        "pattern": pattern,
                        "instruction": instruction,
                        "keywords": list(signals),
                        "tags": list(signals),
                        "avoid": avoid,
                        "manually_edited": True,
                        "edited_at": edited_at,
                    }
                )
                if callable(validator) and not validator(candidate):
                    kind_label = "可复用表达" if kind == "style" else "句法结构"
                    raise ValueError(f"{kind_label}不符合可复用规则要求，请补全具体结构和使用指令")
                updated_rules.append((idx, candidate))

            for idx, candidate in updated_rules:
                stored_rules[idx] = candidate
            profile[storage_key] = stored_rules
            profile["updated_at"] = edited_at
            return f"已保存表达规则组，共更新 {len(updated_rules)} 条互补规则"
        if action in {"approve_rule_group", "reject_rule_group"}:
            if not rule_family_id:
                return "缺少规则组标识"
            matched = [
                item
                for item in pending_rules
                if isinstance(item, dict) and self._single_line(item.get("family_id"), 100) == rule_family_id
            ]
            if not matched:
                return "没有找到待审核规则组"
            profile["pending_rules"] = [
                item
                for item in pending_rules
                if not isinstance(item, dict) or self._single_line(item.get("family_id"), 100) != rule_family_id
            ]
            if action == "reject_rule_group":
                archive_items("rejected_rules", matched, "rejected")
                profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                return f"已拒绝规则组中的 {len(matched)} 条归纳规则"
            validator = getattr(self.plugin, "_expression_rule_definition_is_valid", None)
            approved_items: list[dict[str, Any]] = []
            approved_at = datetime.now().strftime("%Y-%m-%d %H:%M")
            for item in matched:
                if callable(validator) and not validator(item):
                    continue
                approved = dict(item)
                approved["review_status"] = "approved"
                if self._single_line(item.get("review_status"), 24).lower() == "needs_review":
                    approved["negative_feedback_before_review"] = self._int(item.get("negative_feedback"))
                    approved["negative_feedback"] = 0
                    approved["review_cycles"] = self._int(item.get("review_cycles")) + 1
                    approved.pop("review_reason", None)
                approved["approved_at"] = approved_at
                approved_items.append(approved)
            if not approved_items:
                profile["updated_at"] = approved_at
                return "规则组中没有可复用规则，已从待审核区移除"
            merger = getattr(self.plugin, "_merge_learned_expression_rules", None)
            if callable(merger):
                merger(
                    profile,
                    approved_items,
                    batch_key=f"approve-family:{rule_family_id}",
                    now=time.time(),
                )
            else:
                learned_rules = profile.get("learned_rules") if isinstance(profile.get("learned_rules"), list) else []
                learned_rules[0:0] = approved_items
                profile["learned_rules"] = learned_rules[: max(12, int(getattr(self.plugin, "max_learned_expression_items", 60) or 60))]
            profile["updated_at"] = approved_at
            return f"已通过规则组，共启用 {len(approved_items)} 条互补规则"
        if action == "delete_sample":
            idx = find_index(samples)
            if idx < 0:
                return "没有找到已入库样本"
            removed_item = samples.pop(idx)
            archive_items("revoked_samples", [removed_item], "revoked")
            profile["samples"] = samples
            refresher = getattr(self.plugin, "_refresh_expression_profile_legacy_summary", None)
            if callable(refresher):
                refresher(profile)
            profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            voice_refresher = getattr(self.plugin, "_refresh_expression_voice_profile", None)
            if callable(voice_refresher):
                voice_refresher()
            return "已删除表达样本"
        if action == "delete_rule":
            learned_rules = profile.get("learned_rules") if isinstance(profile.get("learned_rules"), list) else []
            removed_rules = [
                item for item in learned_rules
                if isinstance(item, dict) and self._single_line(item.get("id"), 100) == rule_id
            ]
            kept = [
                item
                for item in learned_rules
                if not isinstance(item, dict) or self._single_line(item.get("id"), 100) != rule_id
            ]
            if len(kept) == len(learned_rules):
                return "没有找到归纳规则"
            archive_items("revoked_rules", removed_rules, "revoked")
            profile["learned_rules"] = kept
            profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            return "已删除归纳规则"
        if action == "delete_rule_group":
            if not rule_family_id:
                return "缺少规则组标识"
            learned_rules = profile.get("learned_rules") if isinstance(profile.get("learned_rules"), list) else []
            kept = [
                item
                for item in learned_rules
                if not isinstance(item, dict) or self._single_line(item.get("family_id"), 100) != rule_family_id
            ]
            removed = len(learned_rules) - len(kept)
            if removed <= 0:
                return "没有找到归纳规则组"
            archive_items("revoked_rules", [
                item for item in learned_rules
                if isinstance(item, dict) and self._single_line(item.get("family_id"), 100) == rule_family_id
            ], "revoked")
            profile["learned_rules"] = kept
            profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            return f"已删除规则组中的 {removed} 条规则"
        return "未知表达样本操作"
