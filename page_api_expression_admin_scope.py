# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiExpressionAdminScopeMixin。

由 tools/split_mixin_domain.py 从 page_api_expression.py 机械抽取（7 个方法 + 0 个模块级名字 + 0 个类级赋值 / 254 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiExpressionMixin）。
"""
from __future__ import annotations
from .page_api_expression_shared import Any
from .page_api_expression_shared import ExpressionScopeError
from .page_api_expression_shared import bind_expression_item
from .page_api_expression_shared import bind_expression_profile
from .page_api_expression_shared import deepcopy
from .page_api_expression_shared import hashlib
from .page_api_expression_shared import json
from .page_api_expression_shared import validate_expression_scope_binding



class PrivateCompanionPageApiExpressionAdminScopeMixin:
    """PrivateCompanionPageApiExpressionAdminScopeMixin（从 PrivateCompanionPageApiExpressionMixin 拆出）。"""


    def _expression_admin_scope_context(
        self,
        source_type: str,
        source_id: str,
        owner: dict[str, Any],
    ) -> tuple[bool, Any | None]:
        managed = getattr(self.plugin, "req041_scoped_projection_sync", None) is not None
        if not managed:
            return False, None
        if source_type == "persona":
            if source_id != "current-persona":
                raise ValueError("人格全局规则来源标识无效")
            resolver = getattr(self.plugin, "_req041_persona_global_context", None)
            context = resolver(purpose="rule_write") if callable(resolver) else None
        elif source_type == "private":
            resolver = getattr(self.plugin, "_req041_scoped_context_for_user", None)
            context = resolver(owner, kind="private", purpose="rule_write") if callable(resolver) else None
        elif source_type == "group":
            normalize = getattr(self.plugin, "_normalize_group_identity_id", None)
            normalized_source = self._single_line(normalize(source_id) if callable(normalize) else source_id, 160)
            raw_owner_group = owner.get("group_id") or source_id
            normalized_owner = self._single_line(
                normalize(raw_owner_group) if callable(normalize) else raw_owner_group, 160,
            )
            if not normalized_source or normalized_source != normalized_owner:
                raise ValueError("表达群来源标识与正式作用域不一致")
            resolver = getattr(self.plugin, "_req041_scoped_group_context", None)
            context = resolver(normalized_owner, purpose="rule_write") if callable(resolver) else None
        else:
            raise ValueError("表达来源类型无效")
        if context is None:
            raise ValueError("表达来源没有可写的正式身份作用域")
        return True, context

    def _expression_prepare_admin_profile(
        self,
        owner: dict[str, Any],
        context: Any,
    ) -> dict[str, Any]:
        profile = owner.get("expression_profile") if isinstance(owner.get("expression_profile"), dict) else {}
        binder = getattr(self.plugin, "_expression_bind_profile_scope", None)
        try:
            bound = binder(profile, context, bump_revision=False) if callable(binder) else bind_expression_profile(
                profile, context, bump_revision=False,
            )
        except (ExpressionScopeError, TypeError, ValueError) as exc:
            raise ValueError(f"表达来源作用域校验失败：{exc}") from exc
        owner["expression_profile"] = bound
        return bound

    def _expression_validate_admin_revision(
        self,
        profile: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        raw_expected = payload.get("expected_scope_revision")
        if raw_expected in (None, ""):
            raise ValueError("缺少表达资料版本，请刷新页面后重试")
        expected = self._int(raw_expected)
        current = max(1, self._int(profile.get("scope_revision")))
        if expected != current:
            raise ValueError("表达资料已被其他操作更新，请刷新页面后重试")
        raw_items = payload.get("expected_item_revisions")
        if not isinstance(raw_items, dict):
            raise ValueError("缺少表达项版本，请刷新页面后重试")
        requested = {self._single_line(key, 100): self._int(value) for key, value in raw_items.items() if self._single_line(key, 100)}
        if not requested:
            raise ValueError("缺少表达项版本，请刷新页面后重试")
        found: dict[str, int] = {}
        target_ids: set[str] = set()
        target_rule = self._single_line(payload.get("rule_id"), 100)
        target_family = self._single_line(payload.get("rule_family_id"), 100)
        target_sample = self._single_line(payload.get("sample_id"), 100)
        for storage_key in ("samples", "pending_samples", "learned_rules", "pending_rules"):
            items = profile.get(storage_key) if isinstance(profile.get(storage_key), list) else []
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                item_id = self._single_line(item.get("id"), 100) or f"{storage_key}:{index}"
                binding = item.get("scope_binding") if isinstance(item.get("scope_binding"), dict) else {}
                if (
                    (target_rule and item_id == target_rule)
                    or (target_family and self._single_line(item.get("family_id"), 100) == target_family)
                    or (target_sample and item_id == target_sample)
                ):
                    target_ids.add(item_id)
                if item_id in requested:
                    found[item_id] = self._int(binding.get("revision"))
        if found != requested or (target_ids and set(requested) != target_ids):
            raise ValueError("表达项已被其他操作更新，请刷新页面后重试")

    @staticmethod
    def _expression_item_content(item: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(item)
        result.pop("scope_binding", None)
        return result

    def _expression_finalize_admin_profile(
        self,
        owner: dict[str, Any],
        before: dict[str, Any],
        context: Any,
    ) -> None:
        profile = owner.get("expression_profile") if isinstance(owner.get("expression_profile"), dict) else {}
        previous: dict[str, tuple[str, dict[str, Any]]] = {}
        for storage_key in (
            "samples", "pending_samples", "learned_rules", "pending_rules",
            "rejected_samples", "revoked_samples", "rejected_rules", "revoked_rules",
        ):
            for index, item in enumerate(before.get(storage_key) if isinstance(before.get(storage_key), list) else []):
                if isinstance(item, dict):
                    item_id = self._single_line(item.get("id"), 100) or f"{storage_key}:{index}"
                    previous[item_id] = (storage_key, item)
        states = {
            "samples": ("approved", "administrator"),
            "pending_samples": ("pending", ""),
            "learned_rules": ("approved", "administrator"),
            "pending_rules": ("pending", ""),
            "rejected_samples": ("rejected", "administrator"),
            "revoked_samples": ("revoked", "administrator"),
            "rejected_rules": ("rejected", "administrator"),
            "revoked_rules": ("revoked", "administrator"),
        }
        for storage_key, (approval_state, actor) in states.items():
            items = profile.get(storage_key) if isinstance(profile.get(storage_key), list) else []
            rebound: list[dict[str, Any]] = []
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                item_id = self._single_line(item.get("id"), 100) or f"{storage_key}:{index}"
                old_storage, old = previous.get(item_id, ("", {}))
                changed = bool(
                    not old
                    or old_storage != storage_key
                    or self._expression_item_content(old) != self._expression_item_content(item)
                )
                existing = item.get("scope_binding") if isinstance(item.get("scope_binding"), dict) else {}
                old_binding = old.get("scope_binding") if isinstance(old.get("scope_binding"), dict) else {}
                already_advanced = bool(
                    old and self._int(existing.get("revision")) > self._int(old_binding.get("revision"))
                )
                state_changed = bool(
                    old_binding and self._single_line(old_binding.get("approval_state"), 24) != approval_state
                )
                approved_by = actor if changed and approval_state == "approved" else self._single_line(
                    existing.get("approved_by"), 80,
                )
                if approval_state == "approved" and not approved_by:
                    approved_by = "legacy_migration"
                rebound.append(bind_expression_item(
                    item, context, approval_state=approval_state,
                    approved_by=approved_by,
                    bump_revision=state_changed or (changed and not already_advanced),
                ))
            profile[storage_key] = rebound
        owner["expression_profile"] = bind_expression_profile(profile, context, bump_revision=True)

    @staticmethod
    def _expression_promotion_confirmation(
        *, operation_id: str, source_profile: dict[str, Any], target_profile: dict[str, Any],
        family_id: str, rules: list[dict[str, Any]],
    ) -> str:
        material = {
            "action": "promote_rule_group",
            "operation_id": operation_id,
            "family_id": family_id,
            "source_scope": source_profile.get("scope_ownership"),
            "source_revision": int(source_profile.get("scope_revision") or 0),
            "target_scope": target_profile.get("scope_ownership"),
            "target_revision": int(target_profile.get("scope_revision") or 0),
            "rules": rules,
        }
        return hashlib.sha256(
            json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def _expression_global_promotion_state(
        self,
        *,
        source_type: str,
        source_id: str,
        family_id: str,
        operation_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        collection = self.plugin.data.get("groups" if source_type == "group" else "users")
        source = collection.get(source_id) if isinstance(collection, dict) else None
        if source_type not in {"private", "group"} or not isinstance(source, dict):
            raise ValueError("表达规则来源不存在")
        managed, source_context = self._expression_admin_scope_context(source_type, source_id, source)
        if not managed or source_context is None:
            raise ValueError("表达来源没有可写的正式身份作用域")
        source_owner = {
            "expression_profile": deepcopy(source.get("expression_profile"))
            if isinstance(source.get("expression_profile"), dict) else {}
        }
        source_profile = self._expression_prepare_admin_profile(source_owner, source_context)
        self._expression_validate_admin_revision(source_profile, payload)
        matched = [
            item for item in source_profile.get("learned_rules", [])
            if isinstance(item, dict)
            and self._single_line(item.get("family_id"), 100) == family_id
        ]
        if not matched:
            raise ValueError("没有找到要提升的已审核规则组")
        sanitized: list[dict[str, Any]] = []
        for item in matched:
            validate_expression_scope_binding(
                item.get("scope_binding"), source_context, approval_state="approved",
            )
            clean, reason = self._expression_share_rule(item)
            if clean is None:
                raise ValueError(reason or "规则不能安全提升")
            sanitized.append(clean)
        persona_context_getter = getattr(self.plugin, "_req041_persona_global_context", None)
        persona_context = persona_context_getter(purpose="rule_write") if callable(persona_context_getter) else None
        if persona_context is None:
            raise ValueError("当前人格全局规则作用域不可用")
        raw_global = self.plugin.data.get("_req041_persona_expression_profile")
        global_owner = {
            "expression_profile": deepcopy(raw_global) if isinstance(raw_global, dict) else {}
        }
        target_profile = self._expression_prepare_admin_profile(global_owner, persona_context)
        family_fingerprint = hashlib.sha256(
            json.dumps(sanitized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        promoted: list[dict[str, Any]] = []
        for index, clean in enumerate(sanitized):
            rule_fingerprint = hashlib.sha256(
                json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            candidate = {
                **clean,
                "id": f"persona-{rule_fingerprint[:20]}",
                "family_id": f"persona-{family_fingerprint[:20]}",
                "family_key": f"persona_{family_fingerprint[:20]}",
                "evidence_count": 1,
                "review_status": "approved",
                "explicit_global_promotion": True,
                "component_index": index,
            }
            promoted.append(bind_expression_item(
                candidate, persona_context, approval_state="approved",
                approved_by="administrator",
            ))
        confirmation = self._expression_promotion_confirmation(
            operation_id=operation_id,
            source_profile=source_profile,
            target_profile=target_profile,
            family_id=family_id,
            rules=promoted,
        )
        return {
            "source_profile": source_profile,
            "persona_context": persona_context,
            "global_owner": global_owner,
            "target_profile": target_profile,
            "rules": promoted,
            "confirmation_token": confirmation,
        }
