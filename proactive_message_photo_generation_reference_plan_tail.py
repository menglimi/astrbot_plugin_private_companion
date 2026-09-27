# -*- coding: utf-8 -*-
"""参考图计划构建与绑定收尾域。

由 tools/split_mixin_domain.py 从 proactive_message_photo_generation.py 机械抽取（4 个方法 + 0 个模块级名字 + 0 个类级赋值 / 411 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessagePhotoGenerationMixin）。
"""
from __future__ import annotations

import os
import re
from .helpers import _path_text, _safe_int
from .photo_reference_intent import (
    REFERENCE_ROLES,
    ReferenceIntent,
    analyze_indexed_reference_roles,
    explicitly_excludes_reference_outfit,
)
from .photo_reference_plan import PhotoReferencePlan, build_photo_reference_plan
from .photo_wardrobe_decision import PhotoWardrobeIntent
from dataclasses import replace
from typing import Any



class ProactiveMessagePhotoGenerationReferencePlanTailMixin:
    """参考图计划构建与绑定收尾域（从 ProactiveMessagePhotoGenerationMixin 拆出）。"""


    async def _photo_reference_candidate_for_path_async(
        self,
        reference_image_path: str,
        *,
        workflow_kind: str,
        allow_daily_outfit: bool = True,
        continuity_key: str = "",
    ) -> dict[str, Any]:
        """Resolve transient reference metadata without an Image implementation mixin."""
        path = _path_text(reference_image_path, 1000)
        if not path:
            return {}
        normalized_kind = str(workflow_kind or "").strip().lower()
        if normalized_kind in {"edit", "改图", "修图", "重绘", "p图"}:
            return self._normalize_photo_reference_candidate_metadata(
                {
                    "id": "explicit_reference",
                    "path": path,
                    "source": path,
                    "kind": "source",
                    "note": "用户本轮明确提供或引用的改图原图",
                    "reference_roles": ["source"],
                    "outfit_lock_default": False,
                    "metadata_source": "runtime",
                }
            )
        candidates = await self._photo_reference_candidates_async(
            allow_daily_outfit=allow_daily_outfit,
        )
        recent = self._recent_sent_photo_continuity_candidate(continuity_key)
        if recent:
            candidates.insert(0, recent)
        for candidate in candidates:
            if self._photo_reference_paths_equal(path, candidate.get("path", "")):
                return self._normalize_photo_reference_candidate_metadata(candidate)
        return self._normalize_photo_reference_candidate_metadata(
            {
                "id": "explicit_reference",
                "path": path,
                "source": path,
                "kind": "explicit",
                "note": "用户本轮明确提供或引用的参考图",
                "reference_roles": ["identity"],
                "outfit_lock_default": False,
                "metadata_source": "runtime",
            }
        )

    async def _select_photo_reference_plan_async(
        self,
        workflow_kind: str,
        *,
        reference_intent: ReferenceIntent,
        wardrobe_intent: PhotoWardrobeIntent | None = None,
        requested_outfit_category: str | None = None,
        allow_daily_outfit: bool = True,
        requester_user_id: str = "",
        session_key: str = "",
        request_text: str = "",
        ambient_context: str = "",
        schedule_history_context: str = "",
        suggested_scene_preset: str = "",
        continuity_key: str = "",
        explicit_reference_paths: Any = (),
        require_existing_paths: bool = False,
        trace_id: str = "",
    ) -> PhotoReferencePlan:
        if reference_intent.continuity_mode == "new_topic":
            return build_photo_reference_plan(reference_intent, ())

        paths: list[str] = []
        raw_paths = explicit_reference_paths
        if isinstance(raw_paths, str):
            raw_paths = (raw_paths,)
        for raw_path in raw_paths or ():
            path = _path_text(raw_path, 1000)
            if path and path not in paths:
                paths.append(path)

        candidates: list[dict[str, Any]] = []
        indexed_roles = analyze_indexed_reference_roles(
            request_text,
            image_count=len(paths),
        )
        has_indexed_roles = any(indexed_roles)
        indexed_edit_has_source = any(
            "source" in roles for roles in indexed_roles
        )
        for index, path in enumerate(paths):
            if require_existing_paths and not os.path.isfile(path):
                continue
            candidate = await self._photo_reference_candidate_for_path_async(
                path,
                workflow_kind=workflow_kind,
                allow_daily_outfit=allow_daily_outfit,
                continuity_key=continuity_key,
            )
            if candidate:
                candidate = dict(candidate)
                candidate["available_reference_roles"] = list(
                    candidate.get("reference_roles") or ()
                )
                candidate["id"] = f"explicit_reference_{index + 1}" if len(paths) > 1 else "explicit_reference"
                if reference_intent.continuity_mode == "edit":
                    assigned_roles = list(indexed_roles[index]) if has_indexed_roles else ["source"]
                    if has_indexed_roles and index == 0 and not indexed_edit_has_source:
                        assigned_roles.insert(0, "source")
                    candidate["kind"] = "source" if "source" in assigned_roles else "explicit"
                    candidate["reference_roles"] = list(dict.fromkeys(assigned_roles))
                else:
                    candidate["kind"] = "explicit"
                    if has_indexed_roles:
                        candidate["reference_roles"] = list(indexed_roles[index])
                    else:
                        candidate["reference_roles"] = [
                            role
                            for role in reference_intent.requested_roles
                            if role not in {"continuity", "source"}
                        ]
                candidates.append(candidate)

        if (
            not candidates
            and not paths
            and "source" not in reference_intent.requested_roles
        ):
            selected = await self._select_photo_reference_candidate_async(
                workflow_kind,
                allow_daily_outfit=allow_daily_outfit,
                requester_user_id=requester_user_id,
                request_text=request_text,
                ambient_context=ambient_context,
                schedule_history_context=schedule_history_context,
                suggested_scene_preset=suggested_scene_preset,
                wardrobe_intent=wardrobe_intent,
                trace_id=trace_id,
            )
            if selected:
                selected_candidates: list[dict[str, Any]] = []
                resolved_role_candidates = self._photo_reference_role_asset_candidates(
                    request_text=request_text,
                )
                role_candidates = [
                    item
                    for item in resolved_role_candidates
                    if item.get("role_explicit_mention")
                    and item.get("group_photo_requested")
                ]
                if (
                    not role_candidates
                    and selected.get("kind") == "relation_role"
                    and selected.get("group_photo_requested")
                ):
                    role_candidates = [selected]
                if role_candidates:
                    unique_role_candidates: list[dict[str, Any]] = []
                    seen_role_owners: set[str] = set()
                    for role_candidate in sorted(
                        role_candidates,
                        key=lambda item: -_safe_int(item.get("priority"), 0, -1000),
                    ):
                        owner_id = str(role_candidate.get("owner_id") or "")
                        if not owner_id or owner_id in seen_role_owners:
                            continue
                        seen_role_owners.add(owner_id)
                        unique_role_candidates.append(role_candidate)
                    role_candidates = unique_role_candidates
                group_role_requested = bool(role_candidates)
                if group_role_requested:
                    # A named relationship-role group shot should retain both
                    # Bot's identity and the named role when the backend can
                    # accept multiple references.  The projection layer still
                    # handles one-image backends and emits a textual fallback.
                    persona_candidate = next(
                        (
                            item
                            for item in await self._photo_reference_candidates_async(
                                request_text=request_text,
                                requester_user_id=requester_user_id,
                                ambient_context=ambient_context,
                                allow_daily_outfit=allow_daily_outfit,
                            )
                            if item.get("kind") == "persona"
                        ),
                        None,
                    )
                    if persona_candidate:
                        persona_candidate = dict(persona_candidate)
                        persona_candidate["priority"] = max(
                            760,
                            _safe_int(persona_candidate.get("priority"), 0, -1000),
                        )
                        selected_candidates.append(persona_candidate)
                    elif selected.get("kind") in {
                        "persona",
                        "library",
                        "daily_outfit",
                        "recent_sent_photo",
                    }:
                        bot_candidate = dict(selected)
                        bot_candidate["priority"] = max(
                            760,
                            _safe_int(bot_candidate.get("priority"), 0, -1000),
                        )
                        selected_candidates.append(bot_candidate)
                    selected_candidates.extend(role_candidates[:4])
                if not selected_candidates:
                    selected_candidates = [selected]
                for candidate in selected_candidates:
                    if (
                        candidate.get("kind") == "knowledge_reference"
                        and reference_intent.source == "workflow_default"
                        and "identity" not in set(candidate.get("reference_roles") or ())
                    ):
                        candidate = dict(candidate)
                        candidate["reference_roles"] = [
                            "identity",
                            *(role for role in (candidate.get("reference_roles") or ()) if role != "identity"),
                        ]
                        candidate["available_reference_roles"] = list(candidate["reference_roles"])
                    if all(
                        str(candidate.get("id") or "") != str(existing.get("id") or "")
                        for existing in candidates
                    ):
                        candidates.append(candidate)
        plan_intent = reference_intent
        if not plan_intent.requested_roles and candidates:
            scoped_roles: set[str] = set()
            for candidate in candidates:
                if candidate.get("kind") in {"relation_user", "relation_role"}:
                    scoped_roles.update(candidate.get("reference_roles") or ("identity",))
                elif candidate.get("kind") == "knowledge_reference":
                    scoped_roles.update(candidate.get("reference_roles") or ("scene", "style"))
            scoped_roles.intersection_update(REFERENCE_ROLES)
            if scoped_roles:
                plan_intent = ReferenceIntent(
                    tuple(role for role in REFERENCE_ROLES if role in scoped_roles),
                    reference_intent.excluded_roles,
                    reference_intent.continuity_mode,
                    reference_intent.confidence,
                    "scoped_context",
                )
        if reference_intent.continuity_mode == "edit" and has_indexed_roles:
            requested_roles = set(reference_intent.requested_roles)
            for candidate in candidates:
                requested_roles.update(candidate.get("reference_roles") or ())
            plan_intent = ReferenceIntent(
                tuple(role for role in REFERENCE_ROLES if role in requested_roles),
                reference_intent.excluded_roles,
                reference_intent.continuity_mode,
                reference_intent.confidence,
                reference_intent.source,
            )
        if requested_outfit_category is None:
            requested_outfit_category = (
                str(getattr(wardrobe_intent, "target_category", "") or "")
                .strip()
                .lower()
            )
        else:
            requested_outfit_category = str(requested_outfit_category).strip().lower()
        reference_outfit_excluded = explicitly_excludes_reference_outfit(request_text)
        if reference_intent.source == "workflow_default" and candidates and not paths:
            requested_roles = set(reference_intent.requested_roles)
            for candidate in candidates:
                candidate_roles = {
                    str(role or "").strip().lower()
                    for role in (candidate.get("reference_roles") or ())
                }
                candidate_category = (
                    str(candidate.get("outfit_category") or "").strip().lower()
                )
                if (
                    bool(candidate.get("outfit_lock_default"))
                    and "outfit" in candidate_roles
                    and "outfit" not in reference_intent.excluded_roles
                    and not reference_outfit_excluded
                    and (
                        not requested_outfit_category
                        or (
                            requested_outfit_category != "custom_outfit"
                            and candidate_category == requested_outfit_category
                        )
                    )
                ):
                    requested_roles.add("outfit")
            if requested_roles != set(reference_intent.requested_roles):
                plan_intent = ReferenceIntent(
                    tuple(role for role in REFERENCE_ROLES if role in requested_roles),
                    reference_intent.excluded_roles,
                    reference_intent.continuity_mode,
                    reference_intent.confidence,
                    reference_intent.source,
                )
        matching_outfit_candidates: list[tuple[dict[str, Any], bool]] = []
        for candidate in candidates:
            candidate_roles = {
                str(role or "").strip().lower()
                for role in (candidate.get("reference_roles") or ())
            }
            uses_available_outfit_role = (
                "outfit" not in candidate_roles
                and candidate.get("kind") == "explicit"
                and not has_indexed_roles
                and reference_intent.continuity_mode != "edit"
                and "outfit"
                in {
                    str(role or "").strip().lower()
                    for role in (candidate.get("available_reference_roles") or ())
                }
            )
            declared_roles = candidate_roles | (
                {"outfit"} if uses_available_outfit_role else set()
            )
            if (
                "outfit" in declared_roles
                and str(candidate.get("outfit_category") or "").strip().lower()
                == requested_outfit_category
            ):
                matching_outfit_candidates.append(
                    (candidate, uses_available_outfit_role)
                )
        matching_outfit_reference = bool(matching_outfit_candidates)
        if (
            requested_outfit_category
            and requested_outfit_category != "custom_outfit"
            and matching_outfit_reference
            and not reference_outfit_excluded
        ):
            for candidate, uses_available_outfit_role in matching_outfit_candidates:
                if uses_available_outfit_role:
                    candidate_roles = {
                        str(role or "").strip().lower()
                        for role in (candidate.get("reference_roles") or ())
                    }
                    candidate_roles.add("outfit")
                    candidate["reference_roles"] = [
                        role for role in REFERENCE_ROLES if role in candidate_roles
                    ]
            requested_roles = set(plan_intent.requested_roles)
            excluded_roles = set(plan_intent.excluded_roles)
            requested_roles.add("outfit")
            excluded_roles.discard("outfit")
            plan_intent = replace(
                plan_intent,
                requested_roles=tuple(
                    role for role in REFERENCE_ROLES if role in requested_roles
                ),
                excluded_roles=tuple(
                    role for role in REFERENCE_ROLES if role in excluded_roles
                ),
            )
        plan = build_photo_reference_plan(plan_intent, candidates)
        if (
            not plan.bindings
            and self._photo_persona_fallback_allowed(
                workflow_kind,
                reference_intent,
            )
        ):
            persona_path = await self._photo_persona_reference_image_path_async()
            if persona_path:
                fallback_plan = build_photo_reference_plan(
                    reference_intent,
                    (
                        {
                            "id": "persona",
                            "kind": "persona",
                            "path": persona_path,
                            "reference_roles": ["identity"],
                        },
                    ),
                )
                if fallback_plan.bindings:
                    plan = fallback_plan
        return plan

    async def _photo_reference_candidate_from_plan_binding_async(
        self,
        binding: Any,
        *,
        workflow_kind: str,
        allow_daily_outfit: bool,
        continuity_key: str,
    ) -> dict[str, Any]:
        path = _path_text(getattr(binding, "path", ""), 1000)
        if not path:
            return {}
        snapshot = getattr(binding, "candidate", None)
        candidate = dict(snapshot) if isinstance(snapshot, dict) else {}
        if not candidate:
            candidate = await self._photo_reference_candidate_for_path_async(
                path,
                workflow_kind=workflow_kind,
                allow_daily_outfit=allow_daily_outfit,
                continuity_key=continuity_key,
            )
        if not candidate:
            return {}
        normalized = dict(candidate)
        normalized["reference_roles"] = list(getattr(binding, "roles", ()) or ())
        normalized["ignored_reference_roles"] = list(getattr(binding, "ignore", ()) or ())
        normalized["outfit_lock_default"] = bool(
            normalized.get("outfit_lock_default") and "outfit" in normalized["reference_roles"]
        )
        return self._normalize_photo_reference_candidate_metadata(normalized)

    def _extract_action_image_path(self, action_context: str) -> str:
        text = str(action_context or "")
        match = re.search(r"(?:图片路径|真实图片文件)[:：]\s*(.+)", text)
        if not match:
            return ""
        path = match.group(1).strip().splitlines()[0].strip()
        return path if path and os.path.exists(path) else ""
