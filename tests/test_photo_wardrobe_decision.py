from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
plugin_package = types.ModuleType("astrbot_plugin_private_companion")
plugin_package.__path__ = [str(PLUGIN_ROOT)]
plugin_package.__package__ = "astrbot_plugin_private_companion"
sys.modules.setdefault("astrbot_plugin_private_companion", plugin_package)

from astrbot_plugin_private_companion.photo_wardrobe_decision import (
    PhotoWardrobeDecision,
    analyze_photo_wardrobe,
    resolve_photo_wardrobe_decision,
)


class PhotoWardrobeDecisionTests(unittest.TestCase):
    def test_legacy_preset_name_is_projected_to_the_single_selected_preset(self) -> None:
        decision = PhotoWardrobeDecision(
            rule_id="legacy_single_preset",
            preset_name="居家睡衣",
        )

        self.assertEqual(decision.preset_name, "居家睡衣")
        self.assertEqual(decision.selected_presets, ("居家睡衣",))

    def test_selected_preset_is_projected_back_to_the_legacy_name(self) -> None:
        decision = PhotoWardrobeDecision(
            rule_id="canonical_single_preset",
            selected_presets=("校服人像",),
        )

        self.assertEqual(decision.preset_name, "校服人像")
        self.assertEqual(decision.selected_presets, ("校服人像",))

    def test_decision_rejects_more_than_one_selected_preset(self) -> None:
        with self.assertRaisesRegex(ValueError, "at most one"):
            PhotoWardrobeDecision(
                rule_id="invalid_multiple_presets",
                selected_presets=("角色自拍", "房间日常"),
            )

    def test_decision_requires_legacy_preset_name_to_match_final_preset(self) -> None:
        with self.assertRaisesRegex(ValueError, "preset_name must match"):
            PhotoWardrobeDecision(
                rule_id="invalid_preset_projection",
                preset_name="角色自拍",
                selected_presets=("头像特写",),
            )

    def test_user_request_overrides_reference_and_suggested_preset(self) -> None:
        intent = analyze_photo_wardrobe("换成校服，不要睡衣")

        decision = resolve_photo_wardrobe_decision(
            workflow_kind="selfie",
            prompt_text="换成校服，不要睡衣",
            intent=intent,
            reference={
                "id": "library-formal",
                "kind": "library",
                "path": "C:/images/formal.png",
                "reference_roles": ["identity", "outfit", "style"],
                "outfit_category": "formalwear",
                "outfit_lock_default": True,
            },
            scene_context="当前位置：卧室；今日穿搭：白衬衫和长裙；当前场景：居家",
            base_prompt=(
                "Positive prompt: user request: 换成校服, visual continuity reference: "
                "今日穿搭：白衬衫和长裙, keep today's outfit and character appearance "
                "consistent with the reference image. Negative prompt: 睡衣."
            ),
            suggested_scene_preset="居家睡衣",
            available_presets={"居家睡衣", "校服人像", "日常穿搭"},
        )

        self.assertEqual(decision.rule_id, "explicit_prompt")
        self.assertEqual(decision.category, "school_uniform")
        self.assertTrue(decision.lock_outfit)
        self.assertEqual(decision.suggested_preset, "居家睡衣")
        self.assertEqual(decision.preset_source, "wardrobe_category")
        self.assertEqual(decision.suggestion_status, "rejected_user_conflict")
        self.assertEqual(decision.selected_presets, ("校服人像",))
        self.assertEqual(decision.effective_reference_roles, ("identity", "style"))
        self.assertNotIn("今日穿搭", decision.scene_context)
        self.assertNotIn("today's outfit and character", decision.base_prompt.lower())
        self.assertIn("reference_outfit_role_removed", decision.adjustments)
        self.assertIn("daily_outfit_context_removed", decision.adjustments)
        self.assertIn("generated_daily_outfit_continuity_removed", decision.adjustments)

    def test_explicit_prompt_parses_traditional_format_and_overrides_locked_reference(self) -> None:
        prompt = (
            "Positive prompt: user request: change into a school uniform, classroom selfie. "
            "Negative prompt: sleepwear, formal attire."
        )
        intent = analyze_photo_wardrobe(prompt)

        decision = resolve_photo_wardrobe_decision(
            workflow_kind="portrait",
            prompt_text=prompt,
            intent=intent,
            reference={
                "id": "daily_outfit",
                "kind": "daily_outfit",
                "path": "C:/images/today.png",
                "reference_roles": ["identity", "outfit"],
                "outfit_category": "daily_outfit",
                "outfit_lock_default": True,
            },
            scene_context="当前日程：上课；今日穿搭：针织衫和长裙；当前场景：教室",
            base_prompt=prompt,
            available_presets={"校服人像", "日常穿搭"},
        )

        self.assertEqual(intent.target_category, "school_uniform")
        self.assertEqual(intent.excluded_categories, ("sleepwear", "formalwear"))
        self.assertTrue(intent.change_requested)
        self.assertEqual(decision.rule_id, "explicit_prompt")
        self.assertEqual(decision.mode, "explicit_prompt")
        self.assertEqual(decision.category, "school_uniform")
        self.assertEqual(decision.preset_name, "校服人像")
        self.assertEqual(decision.selected_presets, ("校服人像",))
        self.assertEqual(decision.effective_reference_roles, ("identity",))
        self.assertTrue(decision.remove_daily_outfit_context)
        self.assertNotIn("今日穿搭", decision.scene_context)

    def test_loungewear_is_classified_as_sleepwear(self) -> None:
        prompt = "wear loungewear for a bedtime portrait"
        intent = analyze_photo_wardrobe(prompt)

        decision = resolve_photo_wardrobe_decision(
            workflow_kind="selfie",
            prompt_text=prompt,
            intent=intent,
            reference=None,
            available_presets={"居家睡衣", "居家服", "角色自拍"},
        )

        self.assertEqual(intent.target_category, "sleepwear")
        self.assertEqual(decision.category, "sleepwear")
        self.assertEqual(decision.selected_presets, ("居家睡衣",))

    def test_custom_outfit_is_recognized_without_forcing_a_known_category(self) -> None:
        intent = analyze_photo_wardrobe("换成红色吊带长裙，别穿校服")

        self.assertEqual(intent.target_category, "custom_outfit")
        self.assertTrue(intent.custom_outfit)
        self.assertEqual(intent.excluded_categories, ("school_uniform",))

    def test_contextual_jk_is_treated_as_a_school_uniform_without_matching_a_name(self) -> None:
        self.assertEqual("school_uniform", analyze_photo_wardrobe("穿着JK继续拍一张").target_category)
        self.assertEqual("school_uniform", analyze_photo_wardrobe("换一套JK出门拍照").target_category)
        self.assertEqual("", analyze_photo_wardrobe("JK 说再拍一张").target_category)

    def test_explicit_wear_phrase_overrides_a_locked_reference_outfit(self) -> None:
        for prompt in (
            "穿铠甲拍照",
            "穿白色风衣拍照",
            "wear armor for the photo",
            "wear a trench coat for the photo",
        ):
            with self.subTest(prompt=prompt):
                intent = analyze_photo_wardrobe(prompt)
                decision = resolve_photo_wardrobe_decision(
                    workflow_kind="selfie",
                    prompt_text=prompt,
                    intent=intent,
                    reference={
                        "id": "sleepwear-reference",
                        "path": "C:/images/sleepwear.png",
                        "kind": "library",
                        "reference_roles": ["identity", "outfit"],
                        "outfit_category": "sleepwear",
                        "outfit_lock_default": True,
                        "preferred_preset": "居家睡衣",
                    },
                    available_presets={"角色自拍", "居家睡衣"},
                )

                self.assertEqual(intent.target_category, "custom_outfit")
                self.assertEqual(decision.rule_id, "explicit_prompt")
                self.assertEqual(decision.category, "custom_outfit")
                self.assertTrue(decision.lock_outfit)
                self.assertEqual(decision.effective_reference_roles, ("identity",))

        self.assertEqual(analyze_photo_wardrobe("穿过树林拍照").target_category, "")

    def test_explicit_exclusion_removes_only_the_reference_outfit_role(self) -> None:
        prompt = "在卧室拍一张照片，不要睡衣"
        intent = analyze_photo_wardrobe(prompt)

        decision = resolve_photo_wardrobe_decision(
            workflow_kind="selfie",
            prompt_text=prompt,
            intent=intent,
            reference={
                "id": "library-sleep",
                "kind": "library",
                "path": "C:/images/sleep.png",
                "reference_roles": ["identity", "outfit", "pose"],
                "outfit_category": "sleepwear",
                "outfit_lock_default": True,
            },
            scene_context="当前位置：卧室；今日穿搭：睡衣；当前场景：睡前",
            base_prompt=prompt,
            available_presets={"角色自拍", "居家睡衣"},
        )

        self.assertEqual(intent.target_category, "")
        self.assertEqual(intent.excluded_categories, ("sleepwear",))
        self.assertEqual(decision.rule_id, "explicit_exclusion")
        self.assertEqual(decision.category, "")
        self.assertFalse(decision.lock_outfit)
        self.assertEqual(decision.effective_reference_roles, ("identity", "pose"))
        self.assertIn("reference_outfit_role_removed", decision.adjustments)

    def test_explicit_exclusion_rejects_matching_tool_suggestion(self) -> None:
        decision = resolve_photo_wardrobe_decision(
            workflow_kind="selfie",
            prompt_text="在卧室拍一张照片，不要睡衣",
            reference={
                "id": "library-sleep",
                "kind": "library",
                "path": "C:/images/sleep.png",
                "reference_roles": ["identity", "outfit", "pose"],
                "outfit_category": "sleepwear",
                "outfit_lock_default": True,
            },
            suggested_scene_preset="居家睡衣",
            available_presets={"角色自拍", "居家睡衣"},
        )

        self.assertEqual(decision.rule_id, "explicit_exclusion")
        self.assertEqual(decision.selected_presets, ("角色自拍",))
        self.assertEqual(decision.preset_source, "workflow_default")
        self.assertEqual(decision.suggestion_status, "rejected_user_conflict")
        self.assertNotIn("outfit", decision.effective_reference_roles)

    def test_daily_outfit_reference_is_the_authoritative_fallback(self) -> None:
        intent = analyze_photo_wardrobe("在街边拍一张自然自拍")

        decision = resolve_photo_wardrobe_decision(
            workflow_kind="selfie",
            prompt_text="在街边拍一张自然自拍",
            intent=intent,
            reference={
                "id": "daily_outfit",
                "kind": "daily_outfit",
                "path": "C:/images/today.png",
                "reference_roles": ["identity", "outfit"],
                "outfit_category": "daily_outfit",
                "outfit_lock_default": True,
            },
            scene_context="当前位置：街边；今日穿搭：针织衫和长裙",
            base_prompt="在街边拍一张自然自拍",
            available_presets={"角色自拍", "日常穿搭"},
        )

        self.assertEqual(decision.rule_id, "daily_outfit_reference")
        self.assertEqual(decision.mode, "daily_outfit")
        self.assertEqual(decision.source, "selected_reference")
        self.assertEqual(decision.category, "daily_outfit")
        self.assertTrue(decision.lock_outfit)
        self.assertFalse(decision.remove_daily_outfit_context)
        self.assertEqual(decision.preset_name, "日常穿搭")
        self.assertEqual(decision.selected_presets, ("日常穿搭",))
        self.assertEqual(decision.preset_source, "wardrobe_category")
        self.assertEqual(decision.suggestion_status, "not_provided")
        self.assertEqual(decision.effective_reference_roles, ("identity", "outfit"))
        self.assertEqual(decision.adjustments, ())

    def test_user_composition_overrides_daily_outfit_reference_preset(self) -> None:
        prompt = "拍一张头像特写"

        decision = resolve_photo_wardrobe_decision(
            workflow_kind="selfie",
            prompt_text=prompt,
            intent=analyze_photo_wardrobe(prompt),
            reference={
                "id": "daily_outfit",
                "kind": "daily_outfit",
                "path": "C:/images/today.png",
                "reference_roles": ["identity", "outfit"],
                "outfit_category": "daily_outfit",
                "outfit_lock_default": True,
                "preferred_preset": "日常穿搭",
            },
            scene_context="今日穿搭：针织衫和长裙",
            available_presets={"日常穿搭", "头像特写"},
        )

        self.assertEqual(decision.category, "daily_outfit")
        self.assertTrue(decision.lock_outfit)
        self.assertEqual(decision.selected_presets, ("头像特写",))
        self.assertEqual(decision.preset_source, "user_prompt")

    def test_recent_sent_photo_locks_outfit_and_cleans_advanced_schedule_context(self) -> None:
        prompt = "保持上一张的样子，换个坐姿"
        intent = analyze_photo_wardrobe(prompt)

        decision = resolve_photo_wardrobe_decision(
            workflow_kind="selfie",
            prompt_text=prompt,
            intent=intent,
            reference={
                "id": "recent_sent_photo",
                "kind": "recent_sent_photo",
                "path": "C:/images/recent.png",
                "reference_roles": ["identity", "outfit", "scene", "continuity"],
                "outfit_category": "",
                "outfit_lock_default": True,
                "preferred_preset": "居家服",
            },
            scene_context="当前位置：书房；今日穿搭：通勤西装；当前场景：阅读",
            base_prompt=(
                "Positive prompt: user request: 保持上一张的样子，换个坐姿, "
                "keep today's outfit and character appearance consistent with available visual continuity."
            ),
            available_presets={"角色自拍", "居家服"},
        )

        self.assertEqual(decision.rule_id, "recent_photo_continuity")
        self.assertEqual(decision.mode, "continuity")
        self.assertEqual(decision.category, "reference_outfit")
        self.assertTrue(decision.lock_outfit)
        self.assertTrue(decision.remove_daily_outfit_context)
        self.assertEqual(decision.preset_name, "居家服")
        self.assertEqual(decision.selected_presets, ("居家服",))
        self.assertEqual(decision.preset_source, "reference_preferred")
        self.assertNotIn("今日穿搭", decision.scene_context)
        self.assertNotIn("today's outfit and character", decision.base_prompt.lower())
        self.assertIn("daily_outfit_context_removed", decision.adjustments)
        self.assertIn("generated_daily_outfit_continuity_removed", decision.adjustments)

    def test_locked_library_reference_controls_the_complete_outfit(self) -> None:
        intent = analyze_photo_wardrobe("在卧室拍一张坐在床边的自拍")

        decision = resolve_photo_wardrobe_decision(
            workflow_kind="selfie",
            prompt_text="在卧室拍一张坐在床边的自拍",
            intent=intent,
            reference={
                "id": "library-sleep",
                "kind": "library",
                "path": "C:/images/sleep.png",
                "reference_roles": ["identity", "outfit", "style"],
                "outfit_category": "sleepwear",
                "outfit_lock_default": True,
            },
            scene_context="当前位置：卧室；今日穿搭：校服和外套；当前场景：睡前",
            base_prompt="在卧室拍一张坐在床边的自拍",
            available_presets={"角色自拍", "居家睡衣"},
        )

        self.assertEqual(decision.rule_id, "locked_reference_outfit")
        self.assertEqual(decision.mode, "reference_outfit")
        self.assertEqual(decision.category, "sleepwear")
        self.assertTrue(decision.lock_outfit)
        self.assertEqual(decision.preset_name, "居家睡衣")
        self.assertEqual(decision.selected_presets, ("居家睡衣",))
        self.assertEqual(decision.effective_reference_roles, ("identity", "outfit", "style"))
        self.assertNotIn("今日穿搭", decision.scene_context)
        self.assertIn("daily_outfit_context_removed", decision.adjustments)

    def test_locked_reference_preferred_preset_rejects_conflicting_suggestion(self) -> None:
        decision = resolve_photo_wardrobe_decision(
            workflow_kind="selfie",
            prompt_text="在卧室拍一张坐在床边的自拍",
            intent=analyze_photo_wardrobe("在卧室拍一张坐在床边的自拍"),
            reference={
                "id": "library-sleep",
                "kind": "library",
                "path": "C:/images/sleep.png",
                "reference_roles": ["identity", "outfit", "style"],
                "outfit_category": "sleepwear",
                "outfit_lock_default": True,
                "preferred_preset": "居家睡衣",
            },
            suggested_scene_preset="校服人像",
            available_presets={"居家睡衣", "校服人像", "角色自拍"},
        )

        self.assertEqual(decision.rule_id, "locked_reference_outfit")
        self.assertEqual(decision.category, "sleepwear")
        self.assertEqual(decision.selected_presets, ("居家睡衣",))
        self.assertEqual(decision.preset_source, "reference_preferred")
        self.assertEqual(decision.suggested_preset, "校服人像")
        self.assertEqual(decision.suggestion_status, "rejected_reference_conflict")

    def test_reference_preferred_preset_shadows_compatible_suggestion(self) -> None:
        decision = resolve_photo_wardrobe_decision(
            workflow_kind="selfie",
            prompt_text="拍一张自然自拍",
            reference={
                "id": "library-sleep",
                "kind": "library",
                "path": "C:/images/sleep.png",
                "reference_roles": ["identity", "outfit"],
                "outfit_category": "sleepwear",
                "outfit_lock_default": True,
                "preferred_preset": "居家睡衣",
            },
            suggested_scene_preset="头像特写",
            available_presets={"居家睡衣", "头像特写", "角色自拍"},
        )

        self.assertEqual(decision.selected_presets, ("居家睡衣",))
        self.assertEqual(decision.preset_source, "reference_preferred")
        self.assertEqual(decision.suggestion_status, "shadowed_by_reference")

    def test_compatible_suggestion_is_used_when_reference_has_no_preferred_preset(self) -> None:
        decision = resolve_photo_wardrobe_decision(
            workflow_kind="selfie",
            prompt_text="拍一张自然自拍",
            reference={
                "id": "library-sleep",
                "kind": "library",
                "path": "C:/images/sleep.png",
                "reference_roles": ["identity", "outfit"],
                "outfit_category": "sleepwear",
                "outfit_lock_default": True,
                "preferred_preset": "",
            },
            suggested_scene_preset="头像特写",
            available_presets={"居家睡衣", "头像特写", "角色自拍"},
        )

        self.assertEqual(decision.category, "sleepwear")
        self.assertTrue(decision.lock_outfit)
        self.assertEqual(decision.selected_presets, ("头像特写",))
        self.assertEqual(decision.preset_source, "tool_suggestion")
        self.assertEqual(decision.suggestion_status, "accepted")

    def test_explicit_user_composition_preset_overrides_reference_preferred_preset(self) -> None:
        decision = resolve_photo_wardrobe_decision(
            workflow_kind="selfie",
            prompt_text="给我拍一张头像特写",
            reference={
                "id": "library-sleep",
                "kind": "library",
                "path": "C:/images/sleep.png",
                "reference_roles": ["identity", "outfit"],
                "outfit_category": "sleepwear",
                "outfit_lock_default": True,
                "preferred_preset": "居家睡衣",
            },
            suggested_scene_preset="镜前穿搭",
            available_presets={"居家睡衣", "头像特写", "镜前穿搭", "角色自拍"},
        )

        self.assertEqual(decision.category, "sleepwear")
        self.assertTrue(decision.lock_outfit)
        self.assertEqual(decision.selected_presets, ("头像特写",))
        self.assertEqual(decision.preset_source, "user_prompt")
        self.assertEqual(decision.suggestion_status, "shadowed_by_user")

    def test_explicit_user_composition_also_wins_with_explicit_outfit(self) -> None:
        prompt = "穿校服拍一张头像特写"

        decision = resolve_photo_wardrobe_decision(
            workflow_kind="selfie",
            prompt_text=prompt,
            intent=analyze_photo_wardrobe(prompt),
            reference=None,
            available_presets={"校服人像", "头像特写"},
        )

        self.assertEqual(decision.category, "school_uniform")
        self.assertTrue(decision.lock_outfit)
        self.assertEqual(decision.selected_presets, ("头像特写",))
        self.assertEqual(decision.preset_source, "user_prompt")

    def test_explicit_outfit_removes_unknown_locked_reference_outfit_role(self) -> None:
        prompt = "换成校服拍一张照片"

        decision = resolve_photo_wardrobe_decision(
            workflow_kind="selfie",
            prompt_text=prompt,
            intent=analyze_photo_wardrobe(prompt),
            reference={
                "id": "explicit_reference",
                "kind": "explicit",
                "path": "C:/images/user.png",
                "reference_roles": ["identity", "outfit"],
                "outfit_category": "",
                "outfit_lock_default": True,
            },
            available_presets={"校服人像"},
        )

        self.assertEqual(decision.effective_reference_roles, ("identity",))
        self.assertIn("reference_outfit_role_removed", decision.adjustments)

    def test_explicit_outfit_exclusion_removes_conflicting_daily_context(self) -> None:
        prompt = "拍一张自然自拍，不要校服"

        decision = resolve_photo_wardrobe_decision(
            workflow_kind="selfie",
            prompt_text=prompt,
            intent=analyze_photo_wardrobe(prompt),
            reference={
                "id": "persona",
                "kind": "persona",
                "path": "C:/images/persona.png",
                "reference_roles": ["identity"],
                "outfit_category": "",
                "outfit_lock_default": False,
            },
            scene_context="今日穿搭：校服；当前位置：教室",
            available_presets={"角色自拍", "日常穿搭", "校服人像"},
        )

        self.assertTrue(decision.remove_daily_outfit_context)
        self.assertNotIn("今日穿搭", decision.scene_context)
        self.assertEqual(decision.selected_presets, ("角色自拍",))
        self.assertIn("daily_outfit_context_removed", decision.adjustments)

    def test_exclusion_removes_unknown_locked_reference_outfit_role(self) -> None:
        prompt = "拍一张自然自拍，不要睡衣"

        decision = resolve_photo_wardrobe_decision(
            workflow_kind="selfie",
            prompt_text=prompt,
            reference={
                "id": "explicit_reference",
                "kind": "explicit",
                "path": "C:/images/user.png",
                "reference_roles": ["identity", "outfit"],
                "outfit_category": "",
                "outfit_lock_default": True,
            },
            available_presets={"角色自拍", "居家睡衣"},
        )

        self.assertFalse(decision.lock_outfit)
        self.assertEqual(decision.effective_reference_roles, ("identity",))
        self.assertIn("reference_outfit_role_removed", decision.adjustments)

    def test_excluded_reference_outfit_allows_compatible_suggestion(self) -> None:
        prompt = "拍一张自然自拍，不要睡衣"

        decision = resolve_photo_wardrobe_decision(
            workflow_kind="selfie",
            prompt_text=prompt,
            reference={
                "id": "sleepwear",
                "kind": "library",
                "path": "C:/images/sleepwear.png",
                "reference_roles": ["identity", "outfit"],
                "outfit_category": "sleepwear",
                "outfit_lock_default": True,
                "preferred_preset": "居家睡衣",
            },
            suggested_scene_preset="校服人像",
            available_presets={"角色自拍", "居家睡衣", "校服人像"},
        )

        self.assertEqual(decision.category, "school_uniform")
        self.assertTrue(decision.lock_outfit)
        self.assertEqual(decision.selected_presets, ("校服人像",))
        self.assertEqual(decision.preset_source, "tool_suggestion")
        self.assertEqual(decision.suggestion_status, "accepted")
        self.assertEqual(decision.effective_reference_roles, ("identity",))

    def test_explicit_user_location_removes_ambient_location_fields(self) -> None:
        prompt = "在卧室窗边拍一张自然自拍"

        decision = resolve_photo_wardrobe_decision(
            workflow_kind="selfie",
            prompt_text=prompt,
            intent=analyze_photo_wardrobe(prompt),
            reference=None,
            scene_context="当前日程：在学校上课；当前位置：教室；当前场景：校园；今日穿搭：针织衫；天气：晴",
            available_presets={"角色自拍", "日常穿搭"},
        )

        self.assertNotIn("学校", decision.scene_context)
        self.assertNotIn("教室", decision.scene_context)
        self.assertNotIn("校园", decision.scene_context)
        self.assertIn("今日穿搭：针织衫", decision.scene_context)
        self.assertIn("天气：晴", decision.scene_context)
        self.assertIn("ambient_location_context_removed", decision.adjustments)

    def test_non_selfie_rejects_suggested_preset_excluded_by_user(self) -> None:
        prompt = "画一张房间日常，不要睡衣"

        decision = resolve_photo_wardrobe_decision(
            workflow_kind="text2img",
            prompt_text=prompt,
            intent=analyze_photo_wardrobe(prompt),
            reference=None,
            suggested_scene_preset="居家睡衣",
            available_presets={"可拍画面", "房间日常", "居家睡衣"},
        )

        self.assertNotEqual(decision.selected_presets, ("居家睡衣",))
        self.assertEqual(decision.suggestion_status, "rejected_user_conflict")

    def test_reference_preferred_preset_conflicting_with_its_outfit_is_rejected(self) -> None:
        decision = resolve_photo_wardrobe_decision(
            workflow_kind="selfie",
            prompt_text="拍一张自然自拍",
            reference={
                "id": "library-sleep",
                "kind": "library",
                "path": "C:/images/sleep.png",
                "reference_roles": ["identity", "outfit"],
                "outfit_category": "sleepwear",
                "outfit_lock_default": True,
                "preferred_preset": "校服人像",
            },
            available_presets={"居家睡衣", "校服人像", "角色自拍"},
        )

        self.assertEqual(decision.selected_presets, ("居家睡衣",))
        self.assertEqual(decision.preset_source, "wardrobe_category")
        self.assertIn("reference_preferred_preset_conflict", decision.adjustments)

    def test_daily_outfit_context_is_a_soft_fallback_for_identity_reference(self) -> None:
        intent = analyze_photo_wardrobe("在公园拍一张自然自拍")

        decision = resolve_photo_wardrobe_decision(
            workflow_kind="selfie",
            prompt_text="在公园拍一张自然自拍",
            intent=intent,
            reference={
                "id": "persona",
                "kind": "persona",
                "path": "C:/images/persona.png",
                "reference_roles": ["identity", "style"],
                "outfit_category": "",
                "outfit_lock_default": False,
            },
            scene_context="当前位置：公园；今日穿搭：针织衫和长裙；当前场景：散步",
            base_prompt="在公园拍一张自然自拍",
            available_presets={"角色自拍", "日常穿搭"},
        )

        self.assertEqual(decision.rule_id, "daily_outfit_context")
        self.assertEqual(decision.mode, "daily_outfit_context")
        self.assertEqual(decision.source, "daily_outfit")
        self.assertEqual(decision.category, "daily_outfit")
        self.assertFalse(decision.lock_outfit)
        self.assertEqual(decision.preset_name, "日常穿搭")
        self.assertEqual(decision.selected_presets, ("日常穿搭",))
        self.assertEqual(decision.preset_source, "wardrobe_category")
        self.assertIn("今日穿搭", decision.scene_context)
        self.assertEqual(decision.effective_reference_roles, ("identity", "style"))

    def test_identity_reference_does_not_lock_incidental_clothing(self) -> None:
        intent = analyze_photo_wardrobe("拍一张头像特写")

        decision = resolve_photo_wardrobe_decision(
            workflow_kind="selfie",
            prompt_text="拍一张头像特写",
            intent=intent,
            reference={
                "id": "persona",
                "kind": "persona",
                "path": "C:/images/persona.png",
                "reference_roles": ["identity", "style"],
                "outfit_category": "",
                "outfit_lock_default": False,
            },
            scene_context="当前位置：房间；当前场景：休息",
            base_prompt="拍一张头像特写",
            available_presets={"角色自拍", "头像特写"},
        )

        self.assertEqual(decision.rule_id, "identity_only")
        self.assertEqual(decision.mode, "identity_only")
        self.assertFalse(decision.lock_outfit)
        self.assertEqual(decision.category, "")
        self.assertEqual(decision.selected_presets, ("头像特写",))
        self.assertEqual(decision.effective_reference_roles, ("identity", "style"))

    def test_identity_reference_preferred_preset_respects_user_exclusion(self) -> None:
        prompt = "拍一张自然自拍，不要睡衣"

        decision = resolve_photo_wardrobe_decision(
            workflow_kind="selfie",
            prompt_text=prompt,
            intent=analyze_photo_wardrobe(prompt),
            reference={
                "id": "persona",
                "kind": "persona",
                "path": "C:/images/persona.png",
                "reference_roles": ["identity"],
                "preferred_preset": "居家睡衣",
            },
            available_presets={"角色自拍", "居家睡衣"},
        )

        self.assertEqual(decision.selected_presets, ("角色自拍",))
        self.assertIn("reference_preferred_preset_user_conflict", decision.adjustments)

    def test_deleted_reference_preferred_preset_uses_workflow_default(self) -> None:
        decision = resolve_photo_wardrobe_decision(
            workflow_kind="selfie",
            prompt_text="拍一张自然自拍",
            reference={
                "id": "persona",
                "kind": "persona",
                "path": "C:/images/persona.png",
                "reference_roles": ["identity"],
                "preferred_preset": "已删除预设",
            },
            available_presets={"角色自拍"},
        )

        self.assertEqual(decision.selected_presets, ("角色自拍",))
        self.assertEqual(decision.preset_source, "workflow_default")
        self.assertIn("reference_preferred_preset_unknown", decision.adjustments)

    def test_identity_reference_accepts_wardrobe_suggestion_when_user_is_silent(self) -> None:
        decision = resolve_photo_wardrobe_decision(
            workflow_kind="selfie",
            prompt_text="拍一张自然自拍",
            reference={
                "id": "persona",
                "kind": "persona",
                "path": "C:/images/persona.png",
                "reference_roles": ["identity", "style"],
                "outfit_category": "",
                "outfit_lock_default": False,
                "preferred_preset": "",
            },
            suggested_scene_preset="校服人像",
            scene_context="今日穿搭：针织衫和长裙",
            available_presets={"校服人像", "日常穿搭", "角色自拍"},
        )

        self.assertEqual(decision.category, "school_uniform")
        self.assertTrue(decision.lock_outfit)
        self.assertEqual(decision.selected_presets, ("校服人像",))
        self.assertEqual(decision.preset_source, "tool_suggestion")
        self.assertEqual(decision.suggestion_status, "accepted")
        self.assertNotIn("今日穿搭", decision.scene_context)

    def test_unknown_suggestion_is_rejected_and_workflow_default_is_used(self) -> None:
        decision = resolve_photo_wardrobe_decision(
            workflow_kind="selfie",
            prompt_text="拍一张自然自拍",
            reference={
                "id": "persona",
                "kind": "persona",
                "path": "C:/images/persona.png",
                "reference_roles": ["identity"],
            },
            suggested_scene_preset="已删除预设",
            available_presets={"角色自拍", "头像特写"},
        )

        self.assertEqual(decision.selected_presets, ("角色自拍",))
        self.assertEqual(decision.preset_source, "workflow_default")
        self.assertEqual(decision.suggested_preset, "已删除预设")
        self.assertEqual(decision.suggestion_status, "rejected_unknown")

    def test_image_edit_keeps_the_source_contract_without_wardrobe_presets(self) -> None:
        prompt = "把外套改成校服"
        intent = analyze_photo_wardrobe(prompt)

        decision = resolve_photo_wardrobe_decision(
            workflow_kind="edit",
            prompt_text=prompt,
            intent=intent,
            reference={
                "id": "explicit_reference",
                "kind": "source",
                "path": "C:/images/source.png",
                "reference_roles": ["source"],
            },
            scene_context="今日穿搭：针织衫",
            base_prompt=prompt,
            suggested_scene_preset="校服人像",
            available_presets={"居家睡衣", "校服人像"},
        )

        self.assertEqual(decision.rule_id, "non_selfie_source_edit")
        self.assertEqual(decision.mode, "source_edit")
        self.assertEqual(decision.source, "explicit_reference")
        self.assertEqual(decision.category, "")
        self.assertFalse(decision.lock_outfit)
        self.assertEqual(decision.selected_presets, ())
        self.assertEqual(decision.suggested_preset, "校服人像")
        self.assertEqual(decision.preset_source, "none")
        self.assertEqual(decision.suggestion_status, "rejected_workflow")
        self.assertEqual(decision.effective_reference_roles, ("source",))

    def test_sticker_workflow_defaults_to_sticker_preset(self) -> None:
        decision = resolve_photo_wardrobe_decision(
            workflow_kind="sticker",
            prompt_text="开心地挥挥手",
            reference=None,
            available_presets={"表情包场景", "可拍画面"},
        )

        self.assertEqual(decision.selected_presets, ("表情包场景",))
        self.assertEqual(decision.preset_source, "workflow_default")
        self.assertEqual(decision.suggestion_status, "not_provided")

    def test_selfie_workflow_can_receive_a_separate_sticker_default(self) -> None:
        decision = resolve_photo_wardrobe_decision(
            workflow_kind="selfie",
            prompt_text="开心地挥挥手",
            reference=None,
            workflow_default_scene_preset="表情包场景",
            available_presets={"表情包场景", "角色自拍"},
        )

        self.assertEqual(decision.selected_presets, ("表情包场景",))
        self.assertEqual(decision.suggested_preset, "")
        self.assertEqual(decision.preset_source, "workflow_default")
        self.assertEqual(decision.suggestion_status, "not_provided")

    def test_no_reference_returns_an_auditable_unlocked_decision(self) -> None:
        intent = analyze_photo_wardrobe("拍一张自然自拍")

        decision = resolve_photo_wardrobe_decision(
            workflow_kind="selfie",
            prompt_text="拍一张自然自拍",
            intent=intent,
            reference=None,
            scene_context="当前位置：公园；当前场景：散步",
            base_prompt="拍一张自然自拍",
            available_presets={"角色自拍"},
        )

        self.assertEqual(decision.rule_id, "no_wardrobe_source")
        self.assertEqual(decision.mode, "none")
        self.assertEqual(decision.source, "none")
        self.assertEqual(decision.category, "")
        self.assertFalse(decision.lock_outfit)
        self.assertEqual(decision.selected_presets, ("角色自拍",))
        self.assertEqual(decision.reference_roles, ())

    def test_locked_decision_requires_a_wardrobe_category(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires a category"):
            PhotoWardrobeDecision(rule_id="invalid_lock", lock_outfit=True)

    def test_effective_reference_roles_must_be_a_subset(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be a subset"):
            PhotoWardrobeDecision(
                rule_id="invalid_roles",
                reference_roles=("identity",),
                effective_reference_roles=("identity", "outfit"),
            )

    def test_removed_daily_outfit_context_cannot_remain_in_decision(self) -> None:
        with self.assertRaisesRegex(ValueError, "was not removed"):
            PhotoWardrobeDecision(
                rule_id="invalid_context",
                remove_daily_outfit_context=True,
                scene_context="今日穿搭：校服",
            )

    def test_as_dict_keeps_legacy_log_keys_and_adds_audit_fields(self) -> None:
        decision = resolve_photo_wardrobe_decision(
            workflow_kind="selfie",
            prompt_text="换成校服",
            intent=analyze_photo_wardrobe("换成校服"),
            reference=None,
            available_presets={"校服人像"},
        )

        payload = decision.as_dict()

        for key in (
            "mode",
            "source",
            "category",
            "lock_outfit",
            "remove_daily_outfit_context",
            "preset_name",
            "reference_image_path",
            "reference_id",
            "reference_kind",
            "reference_roles",
            "effective_reference_roles",
            "positive_instruction",
            "negative_instruction",
            "reason",
            "excluded_categories",
            "requested_outfit_text",
        ):
            self.assertIn(key, payload)
        self.assertEqual(payload["decision_version"], 1)
        self.assertEqual(payload["rule_id"], "explicit_prompt")
        self.assertEqual(payload["selected_presets"], ["校服人像"])
        self.assertIn("suggested_preset", payload)
        self.assertIn("preset_source", payload)
        self.assertIn("suggestion_status", payload)
        self.assertNotIn("authoritative_preset", payload)
        self.assertIn("adjustments", payload)

    def test_non_wardrobe_scene_preset_suggestion_is_accepted_without_locking_outfit(self) -> None:
        prompt = "拍一张头像特写"
        intent = analyze_photo_wardrobe(prompt)

        decision = resolve_photo_wardrobe_decision(
            workflow_kind="selfie",
            prompt_text=prompt,
            intent=intent,
            suggested_scene_preset="头像特写",
            reference=None,
            available_presets={"角色自拍", "头像特写"},
        )

        self.assertEqual(decision.suggested_preset, "头像特写")
        self.assertEqual(decision.preset_source, "user_prompt")
        self.assertEqual(decision.suggestion_status, "accepted")
        self.assertEqual(decision.selected_presets, ("头像特写",))
        self.assertEqual(decision.category, "")
        self.assertFalse(decision.lock_outfit)


if __name__ == "__main__":
    unittest.main()
