# -*- coding: utf-8 -*-
"""photo_wardrobe_decision 拆分件 part03（机械搬移，行为不变）。

函数 / 常量 / 类逐字搬自 photo_wardrobe_decision.py，仅调整模块级依赖的导入来源。
"""
from collections.abc import Collection, Mapping
from typing import Any

try:  # package import
    from .photo_wardrobe_decision_part01 import (
        PhotoWardrobeDecision,
        PhotoWardrobeIntent,
        _CATEGORY_PRESETS,
        _DAILY_OUTFIT_PATTERN,
        _EDIT_WORKFLOWS,
        _SELFIE_WORKFLOWS,
        _ambient_location_categories,
        _clean_text,
        _daily_outfit_categories,
        _location_categories,
        _preset_category,
        _scene_without_daily_outfit_details,
        analyze_photo_wardrobe,
    )
except ImportError:  # direct test/import from the plugin directory
    from photo_wardrobe_decision_part01 import (
        PhotoWardrobeDecision,
        PhotoWardrobeIntent,
        _CATEGORY_PRESETS,
        _DAILY_OUTFIT_PATTERN,
        _EDIT_WORKFLOWS,
        _SELFIE_WORKFLOWS,
        _ambient_location_categories,
        _clean_text,
        _daily_outfit_categories,
        _location_categories,
        _preset_category,
        _scene_without_daily_outfit_details,
        analyze_photo_wardrobe,
    )
try:  # package import
    from .photo_wardrobe_decision_part02 import (
        _automatic_presets,
        _explicit_prompt_preset,
        _location_categories_conflict,
        _outfit_label,
        _prompt_without_generated_daily_outfit_continuity,
        _scene_without_ambient_location_fields,
        _selected_presets,
        _validated_reference_preferred_preset,
    )
except ImportError:  # direct test/import from the plugin directory
    from photo_wardrobe_decision_part02 import (
        _automatic_presets,
        _explicit_prompt_preset,
        _location_categories_conflict,
        _outfit_label,
        _prompt_without_generated_daily_outfit_continuity,
        _scene_without_ambient_location_fields,
        _selected_presets,
        _validated_reference_preferred_preset,
    )


def resolve_photo_wardrobe_decision(
    *,
    workflow_kind: str,
    prompt_text: str,
    reference: Mapping[str, Any] | None,
    scene_context: str = "",
    suggested_scene_preset: str = "",
    workflow_default_scene_preset: str = "",
    intent: PhotoWardrobeIntent | None = None,
    base_prompt: str = "",
    available_presets: Collection[str] = (),
) -> PhotoWardrobeDecision:
    resolved_intent = intent or analyze_photo_wardrobe(prompt_text)

    normalized_kind = _clean_text(workflow_kind, 40).lower()
    reference_data = dict(reference or {})
    reference_path = _clean_text(reference_data.get("path"), 1000)
    reference_id = _clean_text(reference_data.get("id"), 60)
    reference_kind = _clean_text(reference_data.get("kind"), 40)
    roles = tuple(str(role) for role in (reference_data.get("reference_roles") or ()))
    effective_roles = roles
    adjustments: list[str] = []
    reference_category = _clean_text(reference_data.get("outfit_category"), 40).lower()
    reference_locks = bool(reference_data.get("outfit_lock_default")) and "outfit" in roles
    suggested_preset = _clean_text(suggested_scene_preset, 80)
    suggested_category = _preset_category(suggested_preset)
    available = {
        _clean_text(name, 80)
        for name in available_presets
        if _clean_text(name, 80)
    }
    excluded_categories = set(resolved_intent.excluded_categories)
    workflow_default_preset = _clean_text(workflow_default_scene_preset, 80)
    if (
        workflow_default_preset not in available
        or _preset_category(workflow_default_preset) in excluded_categories
    ):
        workflow_default_preset = ""
    requested_locations = _location_categories(resolved_intent.positive_text)
    ambient_locations = _ambient_location_categories(scene_context)
    if normalized_kind in _SELFIE_WORKFLOWS and _location_categories_conflict(
        requested_locations,
        ambient_locations,
    ):
        cleaned_scene = _scene_without_ambient_location_fields(scene_context)
        if cleaned_scene != _clean_text(scene_context, 2400):
            scene_context = cleaned_scene
            adjustments.append("ambient_location_context_removed")

    if normalized_kind not in _SELFIE_WORKFLOWS:
        explicit_prompt_preset = _explicit_prompt_preset(workflow_kind, resolved_intent)
        suggestion_conflicts_with_user = bool(
            suggested_category
            and suggested_category in excluded_categories
        )
        if normalized_kind in _EDIT_WORKFLOWS:
            selected = ()
            preset_source = "none"
            suggestion_status = "rejected_workflow" if suggested_preset else "not_provided"
        elif explicit_prompt_preset and explicit_prompt_preset in available:
            selected = (explicit_prompt_preset,)
            preset_source = "user_prompt"
            suggestion_status = (
                "not_provided"
                if not suggested_preset
                else (
                    "rejected_user_conflict"
                    if suggestion_conflicts_with_user
                    else ("accepted" if suggested_preset in selected else "shadowed_by_user")
                )
            )
        elif suggested_preset and suggested_preset in available and not suggestion_conflicts_with_user:
            selected = (suggested_preset,)
            preset_source = "tool_suggestion"
            suggestion_status = "accepted"
        elif workflow_default_preset:
            selected = (workflow_default_preset,)
            preset_source = "workflow_default"
            suggestion_status = (
                "rejected_user_conflict"
                if suggestion_conflicts_with_user
                else ("rejected_unknown" if suggested_preset else "not_provided")
            )
        elif normalized_kind == "sticker" and "表情包场景" in available:
            selected = ("表情包场景",)
            preset_source = "workflow_default"
            suggestion_status = (
                "rejected_user_conflict"
                if suggestion_conflicts_with_user
                else ("rejected_unknown" if suggested_preset else "not_provided")
            )
        else:
            selected = tuple(
                name
                for name in _automatic_presets(
                    workflow_kind,
                    resolved_intent,
                    resolved_intent.excluded_categories,
                )
                if name in available
            )[:1]
            preset_source = "workflow_default" if selected else "none"
            suggestion_status = (
                "rejected_user_conflict"
                if suggestion_conflicts_with_user
                else ("rejected_unknown" if suggested_preset else "not_provided")
            )
        preset_name = selected[0] if selected else ""
        return PhotoWardrobeDecision(
            rule_id="non_selfie_source_edit" if normalized_kind in _EDIT_WORKFLOWS and reference_path else "non_selfie",
            mode="source_edit" if normalized_kind in _EDIT_WORKFLOWS and reference_path else "none",
            source="explicit_reference" if reference_path else "none",
            preset_name=preset_name,
            selected_presets=selected,
            suggested_preset=suggested_preset,
            preset_source=preset_source,
            suggestion_status=suggestion_status,
            reference_image_path=reference_path,
            reference_id=reference_id,
            reference_kind=reference_kind,
            reference_roles=roles,
            effective_reference_roles=roles,
            reason="non-selfie workflow keeps its own image-edit contract",
            excluded_categories=resolved_intent.excluded_categories,
            excluded_outfit_text=resolved_intent.exclusion_text,
            base_prompt=str(base_prompt or prompt_text or "").strip(),
            scene_context=_clean_text(scene_context, 2400),
            adjustments=tuple(adjustments),
        )

    explicit_category = resolved_intent.target_category
    if explicit_category:
        if (
            explicit_category == "custom_outfit"
            or not reference_category
            or (reference_category and reference_category != explicit_category)
        ) and "outfit" in effective_roles:
            effective_roles = tuple(role for role in effective_roles if role != "outfit")
            adjustments.append("reference_outfit_role_removed")
        remove_daily = explicit_category != "daily_outfit"
        cleaned_scene = _clean_text(scene_context, 2400)
        cleaned_prompt = str(base_prompt or prompt_text or "").strip()
        if remove_daily and _DAILY_OUTFIT_PATTERN.search(cleaned_scene):
            updated_scene = _scene_without_daily_outfit_details(cleaned_scene)
            if updated_scene != cleaned_scene:
                cleaned_scene = updated_scene
                adjustments.append("daily_outfit_context_removed")
        if remove_daily:
            updated_prompt = _prompt_without_generated_daily_outfit_continuity(cleaned_prompt)
            if updated_prompt != cleaned_prompt:
                cleaned_prompt = updated_prompt
                adjustments.append("generated_daily_outfit_continuity_removed")
        explicit_prompt_preset = _explicit_prompt_preset(workflow_kind, resolved_intent)
        preset_name = (
            explicit_prompt_preset
            if explicit_prompt_preset in available
            else _CATEGORY_PRESETS.get(explicit_category, "")
        )
        selected = _selected_presets(
            workflow_kind=workflow_kind,
            intent=resolved_intent,
            preset_name=preset_name,
            available_presets=available_presets,
            excluded_categories=resolved_intent.excluded_categories,
        )
        preset_name = selected[0] if selected else ""
        exclusion_instruction = (
            f"Respect the current request's explicit wardrobe exclusions: {resolved_intent.exclusion_text}."
            if resolved_intent.exclusion_text
            else ""
        )
        return PhotoWardrobeDecision(
            rule_id="explicit_prompt",
            mode="explicit_prompt",
            source="user_prompt",
            category=explicit_category,
            lock_outfit=True,
            remove_daily_outfit_context=remove_daily,
            preset_name=preset_name,
            selected_presets=selected,
            suggested_preset=suggested_preset,
            preset_source=(
                "user_prompt"
                if explicit_prompt_preset in selected
                else ("wardrobe_category" if selected else "none")
            ),
            suggestion_status=(
                "not_provided"
                if not suggested_preset
                else (
                    "rejected_unknown"
                    if suggested_preset not in available
                    else (
                        "accepted"
                        if suggested_preset in selected
                        else (
                            "rejected_user_conflict"
                            if suggested_category and suggested_category != explicit_category
                            else "shadowed_by_user"
                        )
                    )
                )
            ),
            reference_image_path=reference_path,
            reference_id=reference_id,
            reference_kind=reference_kind,
            reference_roles=roles,
            effective_reference_roles=effective_roles,
            positive_instruction=(
                "An explicit clothing request in this prompt has highest priority. "
                f"Render one coherent {_outfit_label(explicit_category)} outfit exactly as requested; "
                "use any incompatible selected reference only for identity and compatible visual details."
            ),
            negative_instruction=" ".join(
                part
                for part in (
                    (
                        "Do not restore clothing from today's outfit, schedule context, an older photo, or an incompatible reference."
                        if remove_daily
                        else "Do not replace today's requested outfit with an unrelated costume or wardrobe."
                    ),
                    exclusion_instruction,
                )
                if part
            ),
            reason=(
                "explicit custom or generic clothing change in the current image prompt"
                if explicit_category == "custom_outfit"
                else "explicit clothing request in the current image prompt"
            ),
            excluded_categories=resolved_intent.excluded_categories,
            excluded_outfit_text=resolved_intent.exclusion_text,
            requested_outfit_text=resolved_intent.target_text,
            base_prompt=cleaned_prompt,
            scene_context=cleaned_scene,
            adjustments=tuple(adjustments),
        )

    excluded_daily_context_removed = bool(
        excluded_categories & _daily_outfit_categories(scene_context)
    )
    if excluded_daily_context_removed:
        cleaned_scene = _scene_without_daily_outfit_details(scene_context)
        if cleaned_scene != _clean_text(scene_context, 2400):
            scene_context = cleaned_scene
            adjustments.append("daily_outfit_context_removed")
        cleaned_prompt = _prompt_without_generated_daily_outfit_continuity(
            str(base_prompt or prompt_text or "").strip()
        )
        if cleaned_prompt != str(base_prompt or prompt_text or "").strip():
            adjustments.append("generated_daily_outfit_continuity_removed")
        base_prompt = cleaned_prompt
        if reference_kind == "daily_outfit" and "outfit" in effective_roles:
            effective_roles = tuple(role for role in effective_roles if role != "outfit")
            adjustments.append("reference_outfit_role_removed")
            reference_locks = False

    if excluded_categories and reference_locks and not reference_category:
        effective_roles = tuple(role for role in effective_roles if role != "outfit")
        adjustments.append("reference_outfit_role_removed")
        reference_locks = False

    if reference_category and reference_category in excluded_categories:
        if "outfit" in effective_roles:
            effective_roles = tuple(role for role in effective_roles if role != "outfit")
            adjustments.append("reference_outfit_role_removed")
        explicit_prompt_preset = _explicit_prompt_preset(workflow_kind, resolved_intent)
        preferred_preset = _validated_reference_preferred_preset(
            reference_data.get("preferred_preset"),
            available_presets=available,
            excluded_categories=excluded_categories,
            outfit_category=reference_category,
            adjustments=adjustments,
        )
        suggestion_compatible = bool(
            suggested_preset
            and suggested_preset in available
            and suggested_category not in excluded_categories
        )
        if explicit_prompt_preset and explicit_prompt_preset in available:
            preset_name = explicit_prompt_preset
            preset_source = "user_prompt"
        elif preferred_preset:
            preset_name = preferred_preset
            preset_source = "reference_preferred"
        elif suggestion_compatible:
            preset_name = suggested_preset
            preset_source = "tool_suggestion"
        elif workflow_default_preset:
            preset_name = workflow_default_preset
            preset_source = "workflow_default"
        else:
            preset_name = ""
            preset_source = "none"
        selected = _selected_presets(
            workflow_kind=workflow_kind,
            intent=resolved_intent,
            preset_name=preset_name,
            available_presets=available_presets,
            excluded_categories=resolved_intent.excluded_categories,
        )
        preset_name = selected[0] if selected else ""
        if preset_name and preset_source == "none":
            preset_source = "workflow_default"
        selected_category = _preset_category(preset_name)
        remove_daily = bool(
            reference_category == "daily_outfit"
            or excluded_daily_context_removed
            or (selected_category and selected_category != "daily_outfit")
        )
        cleaned_scene = _clean_text(scene_context, 2400)
        cleaned_prompt = str(base_prompt or prompt_text or "").strip()
        if remove_daily and _DAILY_OUTFIT_PATTERN.search(cleaned_scene):
            updated_scene = _scene_without_daily_outfit_details(cleaned_scene)
            if updated_scene != cleaned_scene:
                cleaned_scene = updated_scene
                adjustments.append("daily_outfit_context_removed")
        if remove_daily:
            updated_prompt = _prompt_without_generated_daily_outfit_continuity(cleaned_prompt)
            if updated_prompt != cleaned_prompt:
                cleaned_prompt = updated_prompt
                adjustments.append("generated_daily_outfit_continuity_removed")
        exclusion_instruction = (
            f"Respect the current request's explicit wardrobe exclusions: {resolved_intent.exclusion_text}."
            if resolved_intent.exclusion_text
            else ""
        )
        return PhotoWardrobeDecision(
            rule_id="explicit_exclusion",
            mode="explicit_exclusion",
            source="user_prompt",
            category=selected_category,
            lock_outfit=bool(selected_category),
            remove_daily_outfit_context=remove_daily,
            preset_name=preset_name,
            selected_presets=selected,
            suggested_preset=suggested_preset,
            preset_source=preset_source,
            suggestion_status=(
                "rejected_user_conflict"
                if suggested_preset and suggested_category in excluded_categories
                else (
                    "rejected_unknown"
                    if suggested_preset and suggested_preset not in available
                    else (
                        "accepted"
                        if suggested_preset and suggested_preset in selected
                        else (
                            "shadowed_by_user"
                            if suggested_preset and explicit_prompt_preset in selected
                            else (
                                "shadowed_by_reference"
                                if suggested_preset and preferred_preset in selected
                                else ("rejected_user_conflict" if suggested_preset else "not_provided")
                            )
                        )
                    )
                )
            ),
            reference_image_path=reference_path,
            reference_id=reference_id,
            reference_kind=reference_kind,
            reference_roles=roles,
            effective_reference_roles=effective_roles,
            positive_instruction=(
                "Use the selected reference for identity and other compatible responsibilities only; "
                "its outfit is explicitly excluded by the current request."
                + (
                    f" Render one coherent {_outfit_label(selected_category)} outfit from the selected preset."
                    if selected_category
                    else ""
                )
            ),
            negative_instruction=exclusion_instruction,
            reason="selected reference outfit is explicitly excluded by the current request",
            excluded_categories=resolved_intent.excluded_categories,
            excluded_outfit_text=resolved_intent.exclusion_text,
            base_prompt=cleaned_prompt,
            scene_context=cleaned_scene,
            adjustments=tuple(adjustments),
        )

    if reference_kind == "daily_outfit" and not excluded_daily_context_removed:
        explicit_prompt_preset = _explicit_prompt_preset(workflow_kind, resolved_intent)
        preferred_preset = _validated_reference_preferred_preset(
            reference_data.get("preferred_preset"),
            available_presets=available,
            excluded_categories=excluded_categories,
            outfit_category="daily_outfit",
            adjustments=adjustments,
        )
        suggestion_compatible = bool(
            suggested_preset
            and suggested_preset in available
            and suggested_category not in excluded_categories
            and (not suggested_category or suggested_category == "daily_outfit")
        )
        if explicit_prompt_preset and explicit_prompt_preset in available:
            preset_name = explicit_prompt_preset
        elif preferred_preset:
            preset_name = preferred_preset
        elif suggestion_compatible:
            preset_name = suggested_preset
        else:
            preset_name = _CATEGORY_PRESETS["daily_outfit"]
        selected = _selected_presets(
            workflow_kind=workflow_kind,
            intent=resolved_intent,
            preset_name=preset_name,
            available_presets=available_presets,
            excluded_categories=resolved_intent.excluded_categories,
        )
        preset_name = selected[0] if selected else ""
        exclusion_instruction = (
            f"Respect the current request's explicit wardrobe exclusions: {resolved_intent.exclusion_text}."
            if resolved_intent.exclusion_text
            else ""
        )
        return PhotoWardrobeDecision(
            rule_id="daily_outfit_reference",
            mode="daily_outfit",
            source="selected_reference",
            category="daily_outfit",
            lock_outfit=True,
            preset_name=preset_name,
            selected_presets=selected,
            suggested_preset=suggested_preset,
            preset_source=(
                "user_prompt"
                if explicit_prompt_preset in selected
                else (
                    "reference_preferred"
                    if preferred_preset in selected
                    else (
                        "tool_suggestion"
                        if suggested_preset in selected
                        else ("wardrobe_category" if selected else "none")
                    )
                )
            ),
            suggestion_status=(
                "accepted"
                if suggested_preset and suggested_preset in selected
                else (
                    "rejected_unknown"
                    if suggested_preset and suggested_preset not in available
                    else (
                        "shadowed_by_user"
                        if suggested_preset and explicit_prompt_preset in selected
                        else (
                            "rejected_reference_conflict"
                            if suggested_preset and suggested_category and suggested_category != "daily_outfit"
                            else ("shadowed_by_reference" if suggested_preset else "not_provided")
                        )
                    )
                )
            ),
            reference_image_path=reference_path,
            reference_id=reference_id,
            reference_kind=reference_kind,
            reference_roles=roles,
            effective_reference_roles=roles,
            positive_instruction=(
                "Use the selected reference as the authoritative source for today's complete outfit and identity continuity. "
                "Preserve its coherent clothing layers, accessories, silhouette, and main color palette."
            ),
            negative_instruction=" ".join(
                part
                for part in (
                    "Do not invent an alternative outfit or mix several wardrobe variants.",
                    exclusion_instruction,
                )
                if part
            ),
            reason="selected reference is today's outfit reference",
            excluded_categories=resolved_intent.excluded_categories,
            excluded_outfit_text=resolved_intent.exclusion_text,
            base_prompt=str(base_prompt or prompt_text or "").strip(),
            scene_context=_clean_text(scene_context, 2400),
            adjustments=tuple(adjustments),
        )

    if reference_kind == "recent_sent_photo" and reference_locks:
        category = reference_category or "reference_outfit"
        remove_daily = category != "daily_outfit" or excluded_daily_context_removed
        cleaned_scene = _clean_text(scene_context, 2400)
        cleaned_prompt = str(base_prompt or prompt_text or "").strip()
        if remove_daily and _DAILY_OUTFIT_PATTERN.search(cleaned_scene):
            updated_scene = _scene_without_daily_outfit_details(cleaned_scene)
            if updated_scene != cleaned_scene:
                cleaned_scene = updated_scene
                adjustments.append("daily_outfit_context_removed")
        if remove_daily:
            updated_prompt = _prompt_without_generated_daily_outfit_continuity(cleaned_prompt)
            if updated_prompt != cleaned_prompt:
                cleaned_prompt = updated_prompt
                adjustments.append("generated_daily_outfit_continuity_removed")
        available = {
            _clean_text(name, 80)
            for name in available_presets
            if _clean_text(name, 80)
        }
        explicit_prompt_preset = _explicit_prompt_preset(workflow_kind, resolved_intent)
        preferred_preset = _validated_reference_preferred_preset(
            reference_data.get("preferred_preset"),
            available_presets=available,
            excluded_categories=excluded_categories,
            outfit_category=category,
            adjustments=adjustments,
        )
        suggestion_compatible = bool(
            suggested_preset
            and suggested_preset in available
            and suggested_category not in set(resolved_intent.excluded_categories)
            and (
                not suggested_category
                or (category != "reference_outfit" and suggested_category == category)
            )
        )
        if explicit_prompt_preset and explicit_prompt_preset in available:
            preset_name = explicit_prompt_preset
        elif preferred_preset and preferred_preset in available:
            preset_name = preferred_preset
        elif suggestion_compatible:
            preset_name = suggested_preset
        else:
            preset_name = _CATEGORY_PRESETS.get(category, "")
        selected = _selected_presets(
            workflow_kind=workflow_kind,
            intent=resolved_intent,
            preset_name=preset_name,
            available_presets=available_presets,
            excluded_categories=resolved_intent.excluded_categories,
        )
        preset_name = selected[0] if selected else ""
        exclusion_instruction = (
            f"Respect the current request's explicit wardrobe exclusions: {resolved_intent.exclusion_text}."
            if resolved_intent.exclusion_text
            else ""
        )
        return PhotoWardrobeDecision(
            rule_id="recent_photo_continuity",
            mode="continuity",
            source="selected_reference",
            category=category,
            lock_outfit=True,
            remove_daily_outfit_context=remove_daily,
            preset_name=preset_name,
            selected_presets=selected,
            suggested_preset=suggested_preset,
            preset_source=(
                "user_prompt"
                if explicit_prompt_preset in selected
                else (
                    "reference_preferred"
                    if preferred_preset in selected
                    else (
                        "tool_suggestion"
                        if suggested_preset in selected
                        else ("wardrobe_category" if selected else "none")
                    )
                )
            ),
            suggestion_status=(
                "accepted"
                if suggested_preset and suggested_preset in selected
                else (
                    "rejected_unknown"
                    if suggested_preset and suggested_preset not in available
                    else (
                        "rejected_reference_conflict"
                        if (
                            suggested_preset
                            and suggested_category
                            and (category == "reference_outfit" or suggested_category != category)
                        )
                        else (
                            "shadowed_by_user"
                            if suggested_preset and explicit_prompt_preset in selected
                            else ("shadowed_by_reference" if suggested_preset else "not_provided")
                        )
                    )
                )
            ),
            reference_image_path=reference_path,
            reference_id=reference_id,
            reference_kind=reference_kind,
            reference_roles=roles,
            effective_reference_roles=roles,
            positive_instruction=(
                "The last image sent in this conversation is authoritative for identity, complete outfit, room or location, "
                "lighting, and time unless the current request changes them. Use the schedule only for missing, non-conflicting details."
            ),
            negative_instruction=" ".join(
                part
                for part in (
                    "Do not relocate the scene, redesign the outfit, or replace continuity details merely because the schedule has advanced.",
                    exclusion_instruction,
                )
                if part
            ),
            reason="selected reference is the last image sent in this conversation",
            excluded_categories=resolved_intent.excluded_categories,
            excluded_outfit_text=resolved_intent.exclusion_text,
            base_prompt=cleaned_prompt,
            scene_context=cleaned_scene,
            adjustments=tuple(adjustments),
        )

    if reference_locks:
        category = reference_category or "reference_outfit"
        remove_daily = category != "daily_outfit" or excluded_daily_context_removed
        cleaned_scene = _clean_text(scene_context, 2400)
        cleaned_prompt = str(base_prompt or prompt_text or "").strip()
        if remove_daily and _DAILY_OUTFIT_PATTERN.search(cleaned_scene):
            updated_scene = _scene_without_daily_outfit_details(cleaned_scene)
            if updated_scene != cleaned_scene:
                cleaned_scene = updated_scene
                adjustments.append("daily_outfit_context_removed")
        if remove_daily:
            updated_prompt = _prompt_without_generated_daily_outfit_continuity(cleaned_prompt)
            if updated_prompt != cleaned_prompt:
                cleaned_prompt = updated_prompt
                adjustments.append("generated_daily_outfit_continuity_removed")
        available = {
            _clean_text(name, 80)
            for name in available_presets
            if _clean_text(name, 80)
        }
        explicit_prompt_preset = _explicit_prompt_preset(workflow_kind, resolved_intent)
        preferred_preset = _validated_reference_preferred_preset(
            reference_data.get("preferred_preset"),
            available_presets=available,
            excluded_categories=excluded_categories,
            outfit_category=category,
            adjustments=adjustments,
        )
        suggestion_compatible = bool(
            suggested_preset
            and suggested_preset in available
            and suggested_category not in set(resolved_intent.excluded_categories)
            and (
                not suggested_category
                or (category != "reference_outfit" and suggested_category == category)
            )
        )
        if explicit_prompt_preset and explicit_prompt_preset in available:
            preset_name = explicit_prompt_preset
        elif preferred_preset and preferred_preset in available:
            preset_name = preferred_preset
        elif suggestion_compatible:
            preset_name = suggested_preset
        else:
            preset_name = _CATEGORY_PRESETS.get(category, "")
        selected = _selected_presets(
            workflow_kind=workflow_kind,
            intent=resolved_intent,
            preset_name=preset_name,
            available_presets=available_presets,
            excluded_categories=resolved_intent.excluded_categories,
        )
        preset_name = selected[0] if selected else ""
        exclusion_instruction = (
            f"Respect the current request's explicit wardrobe exclusions: {resolved_intent.exclusion_text}."
            if resolved_intent.exclusion_text
            else ""
        )
        return PhotoWardrobeDecision(
            rule_id="locked_reference_outfit",
            mode="reference_outfit",
            source="selected_reference",
            category=category,
            lock_outfit=True,
            remove_daily_outfit_context=remove_daily,
            preset_name=preset_name,
            selected_presets=selected,
            suggested_preset=suggested_preset,
            preset_source=(
                "user_prompt"
                if explicit_prompt_preset in selected
                else (
                    "reference_preferred"
                    if preferred_preset in selected
                    else (
                        "tool_suggestion"
                        if suggested_preset in selected
                        else ("wardrobe_category" if selected else "none")
                    )
                )
            ),
            suggestion_status=(
                "accepted"
                if suggested_preset and suggested_preset in selected
                else (
                    "rejected_unknown"
                    if suggested_preset and suggested_preset not in available
                    else (
                        "rejected_reference_conflict"
                        if (
                            suggested_preset
                            and suggested_category
                            and (category == "reference_outfit" or suggested_category != category)
                        )
                        else (
                            "shadowed_by_user"
                            if suggested_preset and explicit_prompt_preset in selected
                            else ("shadowed_by_reference" if suggested_preset else "not_provided")
                        )
                    )
                )
            ),
            reference_image_path=reference_path,
            reference_id=reference_id,
            reference_kind=reference_kind,
            reference_roles=roles,
            effective_reference_roles=roles,
            positive_instruction=(
                "Use the selected reference image as the authoritative source for identity and the complete visible outfit. "
                f"Preserve {_outfit_label(category)}, including its garment category, layers, silhouette, material impression, "
                "trim details, accessories, and main color palette. The schedule context controls only location, activity, mood, lighting, and time."
            ),
            negative_instruction=" ".join(
                part
                for part in (
                    (
                        "Do not replace the selected-reference outfit with today's daytime outfit, school or commuter layers, a coat, blazer, shirt, vest, tie, or another wardrobe unless the user explicitly requests it."
                        if category in {"sleepwear", "homewear"}
                        else "Do not restore a different outfit from schedule context or today's outfit."
                    ),
                    exclusion_instruction,
                )
                if part
            ),
            reason="selected reference is an outfit-bearing reference with outfit_lock_default=true",
            excluded_categories=resolved_intent.excluded_categories,
            excluded_outfit_text=resolved_intent.exclusion_text,
            base_prompt=cleaned_prompt,
            scene_context=cleaned_scene,
            adjustments=tuple(adjustments),
        )

    identity_reference_preferred = _validated_reference_preferred_preset(
        reference_data.get("preferred_preset"),
        available_presets=available,
        excluded_categories=excluded_categories,
        adjustments=adjustments,
    )
    if (
        suggested_preset
        and suggested_preset in available
        and suggested_category
        and suggested_category not in set(resolved_intent.excluded_categories)
        and not (identity_reference_preferred and identity_reference_preferred in available)
    ):
        remove_daily = suggested_category != "daily_outfit" or excluded_daily_context_removed
        cleaned_scene = _clean_text(scene_context, 2400)
        cleaned_prompt = str(base_prompt or prompt_text or "").strip()
        if remove_daily and _DAILY_OUTFIT_PATTERN.search(cleaned_scene):
            updated_scene = _scene_without_daily_outfit_details(cleaned_scene)
            if updated_scene != cleaned_scene:
                cleaned_scene = updated_scene
                adjustments.append("daily_outfit_context_removed")
        if remove_daily:
            updated_prompt = _prompt_without_generated_daily_outfit_continuity(cleaned_prompt)
            if updated_prompt != cleaned_prompt:
                cleaned_prompt = updated_prompt
                adjustments.append("generated_daily_outfit_continuity_removed")
        return PhotoWardrobeDecision(
            rule_id="suggested_scene_preset",
            mode="suggested_preset",
            source="tool_suggestion",
            category=suggested_category,
            lock_outfit=True,
            remove_daily_outfit_context=remove_daily,
            preset_name=suggested_preset,
            selected_presets=(suggested_preset,),
            suggested_preset=suggested_preset,
            preset_source="tool_suggestion",
            suggestion_status="accepted",
            reference_image_path=reference_path,
            reference_id=reference_id,
            reference_kind=reference_kind,
            reference_roles=roles,
            effective_reference_roles=effective_roles,
            positive_instruction=(
                f"Use the compatible suggested scene preset '{suggested_preset}' as the wardrobe source because the user "
                "and selected reference do not provide a stronger outfit requirement. Render one coherent outfit."
            ),
            negative_instruction="Do not restore a conflicting outfit from schedule context or today's outfit.",
            reason="compatible tool suggestion fills an otherwise unspecified wardrobe",
            excluded_categories=resolved_intent.excluded_categories,
            excluded_outfit_text=resolved_intent.exclusion_text,
            base_prompt=cleaned_prompt,
            scene_context=cleaned_scene,
            adjustments=tuple(adjustments),
        )

    if _DAILY_OUTFIT_PATTERN.search(str(scene_context or "")):
        explicit_prompt_preset = _explicit_prompt_preset(workflow_kind, resolved_intent)
        suggestion_allowed = bool(
            suggested_preset
            and suggested_preset in available
            and not suggested_category
        )
        if explicit_prompt_preset and explicit_prompt_preset in available:
            selected = (explicit_prompt_preset,)
            preset_source = "user_prompt"
        elif identity_reference_preferred and identity_reference_preferred in available:
            selected = (identity_reference_preferred,)
            preset_source = "reference_preferred"
        elif suggestion_allowed:
            selected = (suggested_preset,)
            preset_source = "tool_suggestion"
        elif "日常穿搭" in available:
            selected = ("日常穿搭",)
            preset_source = "wardrobe_category"
        else:
            selected = ()
            preset_source = "none"
        if not suggested_preset:
            suggestion_status = "not_provided"
        elif suggested_preset not in available:
            suggestion_status = "rejected_unknown"
        elif suggested_preset in selected:
            suggestion_status = "accepted"
        elif explicit_prompt_preset in selected:
            suggestion_status = "shadowed_by_user"
        elif identity_reference_preferred in selected:
            suggestion_status = "shadowed_by_reference"
        else:
            suggestion_status = "rejected_user_conflict"
        preset_name = selected[0] if selected else ""
        return PhotoWardrobeDecision(
            rule_id="daily_outfit_context",
            mode="daily_outfit_context",
            source="daily_outfit",
            category="daily_outfit",
            lock_outfit=False,
            preset_name=preset_name,
            selected_presets=selected,
            suggested_preset=suggested_preset,
            preset_source=preset_source,
            suggestion_status=suggestion_status,
            reference_image_path=reference_path,
            reference_id=reference_id,
            reference_kind=reference_kind,
            reference_roles=roles,
            effective_reference_roles=effective_roles,
            positive_instruction=(
                "The selected reference, if present, controls identity only. Since the user did not request a clothing change, "
                "today's outfit context may provide wardrobe continuity."
            ),
            negative_instruction="Do not copy incidental clothing from an identity-only reference over today's outfit.",
            reason="identity-only reference with available daily outfit context",
            excluded_categories=resolved_intent.excluded_categories,
            excluded_outfit_text=resolved_intent.exclusion_text,
            base_prompt=str(base_prompt or prompt_text or "").strip(),
            scene_context=_clean_text(scene_context, 2400),
            adjustments=tuple(adjustments),
        )

    explicit_prompt_preset = _explicit_prompt_preset(workflow_kind, resolved_intent)
    suggestion_allowed = bool(
        suggested_preset
        and suggested_preset in available
        and suggested_category not in set(resolved_intent.excluded_categories)
    )
    if explicit_prompt_preset and explicit_prompt_preset in available:
        selected = (explicit_prompt_preset,)
        preset_source = "user_prompt"
    elif identity_reference_preferred and identity_reference_preferred in available:
        selected = (identity_reference_preferred,)
        preset_source = "reference_preferred"
    elif suggestion_allowed:
        selected = (suggested_preset,)
        preset_source = "tool_suggestion"
    else:
        selected = (
            (workflow_default_preset,)
            if workflow_default_preset
            else tuple(
                name
                for name in _automatic_presets(
                    workflow_kind,
                    resolved_intent,
                    resolved_intent.excluded_categories,
                )
                if name in available
            )[:1]
        )
        preset_source = (
            "user_prompt"
            if selected
            and not workflow_default_preset
            and selected[0] not in {"角色自拍", "可拍画面"}
            else ("workflow_default" if selected else "none")
        )
    preset_name = selected[0] if selected else ""
    if not suggested_preset:
        suggestion_status = "not_provided"
    elif suggested_preset not in available:
        suggestion_status = "rejected_unknown"
    elif suggested_category in set(resolved_intent.excluded_categories):
        suggestion_status = "rejected_user_conflict"
    elif suggested_preset in selected:
        suggestion_status = "accepted"
    elif explicit_prompt_preset in selected:
        suggestion_status = "shadowed_by_user"
    elif identity_reference_preferred in selected:
        suggestion_status = "shadowed_by_reference"
    else:
        suggestion_status = "rejected_user_conflict"
    return PhotoWardrobeDecision(
        rule_id="identity_only" if reference_path else "no_wardrobe_source",
        mode="identity_only" if reference_path else "none",
        source="selected_reference" if reference_path else "none",
        remove_daily_outfit_context=excluded_daily_context_removed,
        preset_name=preset_name,
        selected_presets=selected,
        suggested_preset=suggested_preset,
        preset_source=preset_source,
        suggestion_status=suggestion_status,
        reference_image_path=reference_path,
        reference_id=reference_id,
        reference_kind=reference_kind,
        reference_roles=roles,
        effective_reference_roles=effective_roles,
        positive_instruction=(
            "Use the selected reference only for character identity and appearance traits; its incidental clothing is not an outfit lock."
            if reference_path
            else ""
        ),
        reason="selected reference is identity-only" if reference_path else "no wardrobe source selected",
        excluded_categories=resolved_intent.excluded_categories,
        excluded_outfit_text=resolved_intent.exclusion_text,
        base_prompt=str(base_prompt or prompt_text or "").strip(),
        scene_context=_clean_text(scene_context, 2400),
        adjustments=tuple(adjustments),
    )
