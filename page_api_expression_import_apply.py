# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiExpressionImportApplyMixin。

由 tools/split_mixin_domain.py 从 page_api_expression.py 机械抽取（6 个方法 + 0 个模块级名字 + 0 个类级赋值 / 313 行）。
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
from .page_api_expression_shared import json
from .page_api_expression_shared import request
from .page_api_expression_shared import time
from .page_api_expression_shared import validate_expression_scope_binding



class PrivateCompanionPageApiExpressionImportApplyMixin:
    """PrivateCompanionPageApiExpressionImportApplyMixin（从 PrivateCompanionPageApiExpressionMixin 拆出）。"""


    def _expression_import_candidates(self, normalized_pack: dict[str, Any]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        imported_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        for group in normalized_pack.get("rule_groups", []):
            if not isinstance(group, dict):
                continue
            family_key = f"shared_{self._single_line(group.get('signature'), 24)}"
            for raw_rule in group.get("rules", []):
                if not isinstance(raw_rule, dict):
                    continue
                fingerprint = hashlib.sha256(
                    json.dumps(raw_rule, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest()
                rule = dict(raw_rule)
                rule.update(
                    {
                        "id": f"shared-{fingerprint[:20]}",
                        "family_key": family_key,
                        "evidence_count": 1,
                        "review_status": "pending",
                        "shared_import": True,
                        "imported_at": imported_at,
                    }
                )
                candidates.append(rule)
        assigner = getattr(self.plugin, "_assign_expression_rule_families", None)
        if callable(assigner):
            assigner(candidates)
        return candidates

    def _expression_import_preview(
        self,
        normalized_pack: dict[str, Any],
        target: dict[str, Any],
    ) -> dict[str, Any]:
        profile = target.get("expression_profile") if isinstance(target.get("expression_profile"), dict) else {}
        existing = [
            item
            for storage in ("learned_rules", "pending_rules")
            for item in (profile.get(storage) if isinstance(profile.get(storage), list) else [])
            if isinstance(item, dict)
        ]
        duplicate_analyzer = getattr(self.plugin, "_expression_rule_duplicate_analysis", None)
        signature_getter = getattr(self.plugin, "_expression_rule_signature", None)
        candidates = self._expression_import_candidates(normalized_pack)
        duplicate_ids: set[str] = set()
        for candidate in candidates:
            candidate_signature = signature_getter(candidate) if callable(signature_getter) else ""
            for current in existing:
                current_signature = signature_getter(current) if callable(signature_getter) else ""
                if candidate_signature and candidate_signature == current_signature:
                    duplicate_ids.add(str(candidate.get("id") or ""))
                    break
                analysis = duplicate_analyzer(current, candidate) if callable(duplicate_analyzer) else {}
                if isinstance(analysis, dict) and (
                    analysis.get("auto_merge") or self._float(analysis.get("confidence")) >= 0.9
                ):
                    duplicate_ids.add(str(candidate.get("id") or ""))
                    break
        importable = [item for item in candidates if str(item.get("id") or "") not in duplicate_ids]
        family_ids = {
            self._single_line(item.get("family_id"), 100)
            for item in importable
            if self._single_line(item.get("family_id"), 100)
        }
        return {
            "title": normalized_pack.get("title") or "表达分享包",
            "group_count": len(normalized_pack.get("rule_groups", [])),
            "rule_count": len(candidates),
            "importable_group_count": len(family_ids),
            "importable_rule_count": len(importable),
            "duplicate_rule_count": len(duplicate_ids),
            "rejected_count": len(normalized_pack.get("rejected", [])),
            "rejected": normalized_pack.get("rejected", [])[:20],
            "candidates": importable,
        }

    def _expression_import_preview_signature(
        self,
        normalized_pack: dict[str, Any],
        *,
        source_type: str,
        source_id: str,
        scope_revision: int,
        preview: dict[str, Any],
    ) -> str:
        material = {
            "pack": normalized_pack,
            "source_type": source_type,
            "source_id": source_id,
            "scope_revision": int(scope_revision),
            "candidate_ids": sorted(
                self._single_line(item.get("id"), 100)
                for item in preview.get("candidates", []) if isinstance(item, dict)
            ),
        }
        return hashlib.sha256(
            json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    async def share_expression_library(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        raw_items = payload.get("items")
        if raw_items is not None and not isinstance(raw_items, list):
            return self._error("分享范围格式无效")
        if isinstance(raw_items, list) and not raw_items:
            return self._error("当前范围没有可分享的已启用表达")
        if isinstance(raw_items, list) and len(raw_items) > 200:
            return self._error("单次最多分享 200 个表达规则组")
        try:
            async with self.plugin._data_lock:
                snapshot = deepcopy(self.plugin.data)
            selected = {
                (
                    self._single_line(item.get("source_type"), 16),
                    self._single_line(item.get("source_id"), 80),
                    self._single_line(item.get("rule_family_id"), 100),
                )
                for item in raw_items or []
                if isinstance(item, dict)
            }
            groups: list[dict[str, Any]] = []
            seen: set[str] = set()
            for source_type, collection_key in (("private", "users"), ("group", "groups")):
                collection = snapshot.get(collection_key)
                if not isinstance(collection, dict):
                    continue
                for source_id, source in collection.items():
                    profile = source.get("expression_profile") if isinstance(source, dict) else None
                    learned = profile.get("learned_rules") if isinstance(profile, dict) and isinstance(profile.get("learned_rules"), list) else []
                    try:
                        managed, scope_context = self._expression_admin_scope_context(
                            source_type, self._single_line(source_id, 80), source,
                        )
                        if managed:
                            bound = self._expression_prepare_admin_profile(source, scope_context)
                            learned = [
                                item for item in bound.get("learned_rules", [])
                                if isinstance(item, dict)
                                and validate_expression_scope_binding(
                                    item.get("scope_binding"), scope_context, approval_state="approved",
                                )
                            ]
                    except (ExpressionScopeError, ValueError):
                        continue
                    raw_groups = self.plugin._expression_rule_groups(learned) if callable(getattr(self.plugin, "_expression_rule_groups", None)) else [[item] for item in learned]
                    for raw_group in raw_groups:
                        family_id = self._single_line((raw_group[0] if raw_group else {}).get("family_id"), 100)
                        if selected and (source_type, self._single_line(source_id, 80), family_id) not in selected:
                            continue
                        rules: list[dict[str, Any]] = []
                        for raw_rule in raw_group:
                            rule, _ = self._expression_share_rule(raw_rule)
                            if rule is not None:
                                rules.append(rule)
                        if not rules:
                            continue
                        signature = hashlib.sha256(
                            json.dumps(rules, ensure_ascii=False, sort_keys=True).encode("utf-8")
                        ).hexdigest()
                        if signature in seen:
                            continue
                        seen.add(signature)
                        groups.append(
                            {
                                "id": f"group-{len(groups) + 1:03d}",
                                "label": rules[0].get("label") or rules[0].get("situation"),
                                "rules": rules,
                            }
                        )
            if not groups:
                return self._error("当前范围没有可分享的已启用表达")
            pack = {
                "schema": "private-companion-expression-pack",
                "version": 1,
                "title": self._single_line(payload.get("title"), 80) or "我的表达分享",
                "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "privacy": "仅包含抽象表达规则，不包含用户、群聊、原始消息、证据、反馈或使用记录。",
                "rule_groups": groups,
            }
            return self._ok({"package": pack, "group_count": len(groups), "rule_count": sum(len(item["rules"]) for item in groups)})
        except Exception as exc:
            logger.error(f"生成表达分享包失败: {exc}", exc_info=True)
            return self._exception_error("生成表达分享包失败")

    async def preview_expression_library_import(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        try:
            normalized = self._normalize_expression_share_pack(payload.get("package"))
            async with self.plugin._data_lock:
                source_type = self._single_line(payload.get("target_source_type"), 16).lower()
                source_id = self._single_line(payload.get("target_source_id"), 80)
                target = self._expression_share_target(source_type, source_id)
                if target is None:
                    return self._error("请选择有效的导入目标")
                managed, scope_context = self._expression_admin_scope_context(source_type, source_id, target)
                scope_changed = False
                if managed:
                    before_scope = deepcopy(target.get("expression_profile") or {})
                    self._expression_prepare_admin_profile(target, scope_context)
                    scope_changed = before_scope != (target.get("expression_profile") or {})
                preview = self._expression_import_preview(normalized, target)
                scope_revision = self._int((target.get("expression_profile") or {}).get("scope_revision"))
                preview["target_scope_revision"] = scope_revision
                preview["preview_signature"] = self._expression_import_preview_signature(
                    normalized, source_type=source_type, source_id=source_id,
                    scope_revision=scope_revision, preview=preview,
                )
                if scope_changed:
                    self.plugin._save_data_sync(
                        sections={"groups" if source_type == "group" else "users"}
                    )
            return self._ok(preview)
        except ValueError as exc:
            return self._error(str(exc))
        except Exception as exc:
            logger.error(f"预览表达导入失败: {exc}", exc_info=True)
            return self._exception_error("预览表达导入失败")

    async def apply_expression_library_import(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        destination = self._single_line(payload.get("destination"), 16).lower() or "pending"
        if destination not in {"pending", "learned"}:
            return self._error("导入方式无效")
        try:
            normalized = self._normalize_expression_share_pack(payload.get("package"))
            async with self.plugin._data_lock:
                source_type = self._single_line(payload.get("target_source_type"), 16).lower()
                source_id = self._single_line(payload.get("target_source_id"), 80)
                target = self._expression_share_target(source_type, source_id)
                if target is None:
                    return self._error("请选择有效的导入目标")
                managed, scope_context = self._expression_admin_scope_context(source_type, source_id, target)
                if managed:
                    prepared = self._expression_prepare_admin_profile(target, scope_context)
                    expected_revision = self._int(payload.get("expected_scope_revision"))
                    if expected_revision != self._int(prepared.get("scope_revision")):
                        raise ValueError("导入目标已被其他操作更新，请重新预览")
                preview = self._expression_import_preview(normalized, target)
                if managed:
                    expected_signature = self._expression_import_preview_signature(
                        normalized, source_type=source_type, source_id=source_id,
                        scope_revision=self._int(prepared.get("scope_revision")), preview=preview,
                    )
                    if not hmac.compare_digest(
                        self._single_line(payload.get("preview_signature"), 80), expected_signature,
                    ):
                        raise ValueError("导入预览已失效，请重新预览")
                candidates = [dict(item) for item in preview.get("candidates", []) if isinstance(item, dict)]
                if not candidates:
                    result = self._expression_library_summary(deepcopy(self.plugin.data))
                    result["message"] = "没有需要导入的新表达，目标中已存在相同规则"
                    result["import"] = preview
                    return self._ok(result)
                profile = target.setdefault("expression_profile", {})
                if not isinstance(profile, dict):
                    profile = {}
                    target["expression_profile"] = profile
                before = deepcopy(profile)
                if destination == "learned":
                    for item in candidates:
                        item["review_status"] = "approved"
                        item["approved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                    merger = getattr(self.plugin, "_merge_learned_expression_rules", None)
                    if callable(merger):
                        merger(
                            profile,
                            candidates,
                            batch_key=f"share-import:{hashlib.sha1(json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()[:20]}",
                            now=time.time(),
                        )
                    else:
                        learned = profile.get("learned_rules") if isinstance(profile.get("learned_rules"), list) else []
                        profile["learned_rules"] = candidates + learned
                    refresher = getattr(self.plugin, "_refresh_expression_voice_profile", None)
                    if callable(refresher):
                        refresher()
                else:
                    pending = profile.get("pending_rules") if isinstance(profile.get("pending_rules"), list) else []
                    profile["pending_rules"] = candidates + pending
                    backfiller = getattr(self.plugin, "_backfill_expression_rule_families", None)
                    if callable(backfiller):
                        backfiller(profile)
                    deduper = getattr(self.plugin, "_deduplicate_expression_rule_families", None)
                    if callable(deduper):
                        deduper(profile["pending_rules"])
                limit = max(24, self._int(getattr(self.plugin, "max_learned_expression_items", 60)) * 2)
                storage_key = "learned_rules" if destination == "learned" else "pending_rules"
                stored = profile.get(storage_key) if isinstance(profile.get(storage_key), list) else []
                profile[storage_key] = stored[:limit]
                profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                if managed:
                    try:
                        self._expression_finalize_admin_profile(target, before, scope_context)
                    except Exception:
                        target["expression_profile"] = before
                        raise
                save_sections = {
                    "groups" if source_type == "group" else "users"
                }
                if destination == "learned":
                    save_sections.add("expression_voice_profile")
                self.plugin._save_data_sync(sections=save_sections)
                snapshot = deepcopy(self.plugin.data)
            result = self._expression_library_summary(snapshot)
            imported_groups = self._int(preview.get("importable_group_count"))
            result["message"] = (
                f"已导入 {imported_groups} 个表达规则组并直接启用"
                if destination == "learned"
                else f"已将 {imported_groups} 个表达规则组加入审核队列"
            )
            result["import"] = {**preview, "destination": destination}
            return self._ok(result)
        except ValueError as exc:
            return self._error(str(exc))
        except Exception as exc:
            logger.error(f"导入表达分享包失败: {exc}", exc_info=True)
            return self._exception_error("导入表达分享包失败")
