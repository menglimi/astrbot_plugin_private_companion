from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "astrbot_plugin_private_companion"
if PACKAGE_NAME not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        PACKAGE_NAME,
        PLUGIN_ROOT / "__init__.py",
        submodule_search_locations=[str(PLUGIN_ROOT)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load plugin package for tests")
    package = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE_NAME] = package
    spec.loader.exec_module(package)

from astrbot_plugin_private_companion.photo_prompt_context import (
    PhotoPromptSection,
    resolve_photo_prompt_context,
)
from astrbot_plugin_private_companion import photo_prompt_context
from astrbot_plugin_private_companion.photo_wardrobe_decision import PhotoWardrobeDecision


class PhotoPromptContextTests(unittest.TestCase):
    def test_public_interface_and_section_contract_are_strict(self) -> None:
        self.assertEqual(
            photo_prompt_context.__all__,
            [
                "PhotoPromptSection",
                "ResolvedPhotoPromptContext",
                "resolve_photo_prompt_context",
            ],
        )
        with self.assertRaisesRegex(ValueError, "unsupported.*source"):
            PhotoPromptSection("invalid", "additional_prompt")
        section = PhotoPromptSection("request", "user_request", "portrait")
        self.assertFalse(hasattr(section, "__dict__"))
        with self.assertRaises((AttributeError, TypeError)):
            section.positive = "changed"  # type: ignore[misc]

    def test_locked_outfit_removes_daily_outfit_but_preserves_scene_facts(self) -> None:
        wardrobe = PhotoWardrobeDecision(
            rule_id="explicit_prompt",
            mode="explicit_prompt",
            source="explicit_prompt",
            category="school_uniform",
            lock_outfit=True,
            remove_daily_outfit_context=True,
            positive_instruction="Wear only the requested school uniform.",
        )
        sections = (
            PhotoPromptSection(
                name="request",
                source="user_request",
                positive="在教室拍照，换成校服",
            ),
            PhotoPromptSection(
                name="snapshot",
                source="scene_context",
                positive="身份：林默；当前位置：教室；今日穿搭：粉色睡衣；姿势：坐在课桌旁",
            ),
        )

        resolved = resolve_photo_prompt_context(
            wardrobe=wardrobe,
            sections=sections,
            prompt_format="traditional",
            workflow_kind="selfie",
        )

        scene = next(section for section in resolved.prompt_sections if section.name == "snapshot")
        self.assertEqual(scene.positive, "身份：林默；当前位置：教室；姿势：坐在课桌旁")
        self.assertIn("校服", resolved.final_prompt)
        self.assertIn("当前位置：教室", resolved.final_prompt)
        self.assertNotIn("睡衣", resolved.final_prompt)
        self.assertNotIn("Conflict resolution", resolved.final_prompt)
        self.assertTrue(resolved.detected_conflicts)
        self.assertTrue(resolved.removed_conflicts)
        self.assertEqual(resolved.residual_conflicts, ())

    def test_exclusion_only_removes_matching_daily_outfit(self) -> None:
        wardrobe = PhotoWardrobeDecision(
            rule_id="explicit_exclusion",
            mode="exclusion",
            source="explicit_prompt",
            excluded_categories=("sleepwear",),
            negative_instruction="Do not render sleepwear.",
        )

        resolved = resolve_photo_prompt_context(
            wardrobe=wardrobe,
            sections=(
                PhotoPromptSection(
                    name="request",
                    source="user_request",
                    positive="在卧室拍照",
                    negative="睡衣",
                ),
                PhotoPromptSection(
                    name="snapshot",
                    source="scene_context",
                    positive="当前位置：卧室；今日穿搭：蓝色睡衣；当前场景：夜晚",
                ),
            ),
            prompt_format="traditional",
            workflow_kind="selfie",
        )

        snapshot = next(section for section in resolved.prompt_sections if section.name == "snapshot")
        self.assertEqual(snapshot.positive, "当前位置：卧室；当前场景：夜晚")
        self.assertIn("Negative prompt", resolved.final_prompt)
        self.assertIn("睡衣", resolved.final_prompt)
        self.assertEqual(resolved.residual_conflicts, ())

    def test_preset_and_fixed_prompt_lose_conflicting_wardrobe_only(self) -> None:
        wardrobe = PhotoWardrobeDecision(
            rule_id="explicit_prompt",
            mode="explicit_prompt",
            source="explicit_prompt",
            category="school_uniform",
            lock_outfit=True,
        )

        resolved = resolve_photo_prompt_context(
            wardrobe=wardrobe,
            sections=(
                PhotoPromptSection("request", "user_request", "wear a school uniform"),
                PhotoPromptSection(
                    "preset",
                    "preset",
                    "Scene preset: cozy pajamas portrait, warm window light",
                ),
                PhotoPromptSection(
                    "fixed",
                    "fixed_prompt",
                    "Additional fixed prompt: formal attire; fine film grain",
                ),
            ),
            prompt_format="traditional",
            workflow_kind="portrait",
        )

        by_name = {section.name: section for section in resolved.prompt_sections}
        self.assertEqual(by_name["preset"].positive, "warm window light")
        self.assertEqual(by_name["fixed"].positive, "fine film grain")
        self.assertNotIn("pajamas", resolved.final_prompt.lower())
        self.assertNotIn("formal attire", resolved.final_prompt.lower())
        self.assertIn("warm window light", resolved.final_prompt)
        self.assertIn("fine film grain", resolved.final_prompt)

    def test_locked_category_removes_every_other_known_category(self) -> None:
        wardrobe = PhotoWardrobeDecision(
            rule_id="explicit_prompt",
            category="cosplay",
            lock_outfit=True,
        )

        resolved = resolve_photo_prompt_context(
            wardrobe=wardrobe,
            sections=(
                PhotoPromptSection("request", "user_request", "换成角色扮演服"),
                PhotoPromptSection(
                    "memory",
                    "visual_memory",
                    "校服；礼服；运动服；保留脸和发型",
                ),
            ),
            prompt_format="traditional",
            workflow_kind="selfie",
        )

        memory = next(section for section in resolved.prompt_sections if section.name == "memory")
        self.assertEqual(memory.positive, "保留脸和发型")

    def test_unknown_wardrobe_in_an_indivisible_clause_drops_the_section(self) -> None:
        wardrobe = PhotoWardrobeDecision(
            rule_id="explicit_prompt",
            mode="explicit_prompt",
            source="explicit_prompt",
            category="school_uniform",
            lock_outfit=True,
        )

        resolved = resolve_photo_prompt_context(
            wardrobe=wardrobe,
            sections=(
                PhotoPromptSection("request", "user_request", "换成校服"),
                PhotoPromptSection(
                    "memory",
                    "visual_memory",
                    "Preserve identity and copy the exact outfit and accessories from memory",
                ),
            ),
            prompt_format="natural_language",
            workflow_kind="selfie",
        )

        memory = next(section for section in resolved.prompt_sections if section.name == "memory")
        self.assertEqual(memory.positive, "")
        self.assertTrue(
            any(item["action"] == "section_dropped" for item in resolved.removed_conflicts)
        )
        self.assertNotIn("accessories from memory", resolved.final_prompt)
        self.assertEqual(resolved.residual_conflicts, ())

    def test_lower_priority_negative_cannot_negate_authoritative_outfit(self) -> None:
        wardrobe = PhotoWardrobeDecision(
            rule_id="explicit_prompt",
            mode="explicit_prompt",
            source="explicit_prompt",
            category="school_uniform",
            lock_outfit=True,
        )

        resolved = resolve_photo_prompt_context(
            wardrobe=wardrobe,
            sections=(
                PhotoPromptSection("request", "user_request", "wear a school uniform"),
                PhotoPromptSection(
                    "fixed",
                    "fixed_prompt",
                    negative="school uniform, bad anatomy",
                ),
            ),
            prompt_format="traditional",
            workflow_kind="portrait",
        )

        fixed = next(section for section in resolved.prompt_sections if section.name == "fixed")
        self.assertEqual(fixed.negative, "bad anatomy")
        negative = resolved.final_prompt.split("Negative prompt:", 1)[1]
        self.assertNotIn("school uniform", negative.lower())
        self.assertIn("bad anatomy", negative.lower())

    def test_lower_priority_negative_cannot_negate_authoritative_outfit_item(self) -> None:
        wardrobe = PhotoWardrobeDecision(
            rule_id="explicit_prompt",
            mode="explicit_prompt",
            source="explicit_prompt",
            category="custom_outfit",
            lock_outfit=True,
            requested_outfit_text="换成红色吊带长裙",
        )

        resolved = resolve_photo_prompt_context(
            wardrobe=wardrobe,
            sections=(
                PhotoPromptSection("request", "user_request", "换成红色吊带长裙"),
                PhotoPromptSection(
                    "fixed",
                    "fixed_prompt",
                    negative="长裙, bad anatomy",
                ),
            ),
            prompt_format="traditional",
            workflow_kind="portrait",
        )

        fixed = next(section for section in resolved.prompt_sections if section.name == "fixed")
        self.assertEqual(fixed.negative, "bad anatomy")
        self.assertTrue(
            any(
                item["rule"] == "authoritative_outfit_item_negated"
                for item in resolved.removed_conflicts
            )
        )

    def test_user_request_is_never_altered(self) -> None:
        wardrobe = PhotoWardrobeDecision(
            rule_id="explicit_scene_preset",
            mode="scene_preset",
            source="scene_preset",
            category="school_uniform",
            lock_outfit=True,
        )
        request = PhotoPromptSection(
            "request",
            "user_request",
            positive="wear pajamas beside the window",
            negative="school uniform",
        )

        resolved = resolve_photo_prompt_context(
            wardrobe=wardrobe,
            sections=(request,),
            prompt_format="traditional",
            workflow_kind="portrait",
        )

        self.assertEqual(resolved.prompt_sections, (request,))
        self.assertIn(request.positive, resolved.final_prompt)
        self.assertIn(request.negative, resolved.final_prompt)
        self.assertEqual(resolved.residual_conflicts, ())

    def test_recent_continuity_uses_effective_reference_roles(self) -> None:
        wardrobe = PhotoWardrobeDecision(
            rule_id="identity_only",
            mode="identity_only",
            source="selected_reference",
            reference_roles=("identity", "outfit"),
            effective_reference_roles=("identity",),
        )

        resolved = resolve_photo_prompt_context(
            wardrobe=wardrobe,
            sections=(
                PhotoPromptSection("request", "user_request", "change the seated pose"),
                PhotoPromptSection(
                    "recent",
                    "recent_continuity",
                    "Recent-photo continuity: preserve identity, face, hairstyle, exact outfit and accessories. Preserve camera tone.",
                ),
            ),
            prompt_format="natural_language",
            workflow_kind="selfie",
        )

        recent = next(section for section in resolved.prompt_sections if section.name == "recent")
        self.assertIn("preserve identity", recent.positive.lower())
        self.assertIn("preserve camera tone", recent.positive.lower())
        self.assertNotIn("exact outfit and accessories", recent.positive.lower())
        self.assertTrue(
            any(item["rule"] == "inactive_reference_outfit_role" for item in resolved.removed_conflicts)
        )

    def test_selfie_removes_incompatible_reference_using_original_roles(self) -> None:
        wardrobe = PhotoWardrobeDecision(
            rule_id="explicit_prompt",
            mode="explicit_prompt",
            source="explicit_prompt",
            category="school_uniform",
            lock_outfit=True,
            reference_roles=("identity", "outfit"),
            effective_reference_roles=("identity",),
        )
        reference = {
            "id": "library-sleep",
            "path": "C:/images/sleep.png",
            "reference_roles": ["identity", "outfit"],
            "outfit_category": "sleepwear",
        }

        resolved = resolve_photo_prompt_context(
            wardrobe=wardrobe,
            sections=(
                PhotoPromptSection("request", "user_request", "换成校服"),
                PhotoPromptSection(
                    "recent",
                    "recent_continuity",
                    "Recent-photo continuity: preserve identity and room from this reference.",
                ),
            ),
            prompt_format="traditional",
            workflow_kind="selfie",
            reference=reference,
        )

        self.assertIsNone(resolved.reference)
        self.assertIsNotNone(resolved.reference_removed)
        self.assertEqual(resolved.reference_removed["rule"], "reference_outfit_conflict")
        self.assertEqual(resolved.reference_removed["effective_reference_roles"], [])
        self.assertNotIn("Recent-photo continuity", resolved.final_prompt)
        self.assertTrue(
            any(item["rule"] == "reference_context_removed" for item in resolved.removed_conflicts)
        )

    def test_reference_isolation_handles_unknown_compatible_and_edit_references(self) -> None:
        wardrobe = PhotoWardrobeDecision(
            rule_id="explicit_prompt",
            mode="explicit_prompt",
            source="explicit_prompt",
            category="school_uniform",
            lock_outfit=True,
            reference_roles=("identity", "outfit"),
            effective_reference_roles=("identity",),
        )
        request = (PhotoPromptSection("request", "user_request", "wear a school uniform"),)
        unknown = {
            "id": "unknown-outfit",
            "reference_roles": ["identity", "outfit"],
            "outfit_category": "",
        }
        compatible = {
            "id": "school-outfit",
            "reference_roles": ["identity", "outfit"],
            "outfit_category": "school_uniform",
        }
        incompatible_edit_source = {
            "id": "edit-source",
            "reference_roles": ["source", "outfit"],
            "outfit_category": "sleepwear",
        }

        unknown_result = resolve_photo_prompt_context(
            wardrobe=wardrobe,
            sections=request,
            prompt_format="traditional",
            workflow_kind="portrait",
            reference=unknown,
        )
        compatible_result = resolve_photo_prompt_context(
            wardrobe=wardrobe,
            sections=request,
            prompt_format="traditional",
            workflow_kind="selfie",
            reference=compatible,
        )
        edit_result = resolve_photo_prompt_context(
            wardrobe=wardrobe,
            sections=request,
            prompt_format="traditional",
            workflow_kind="edit",
            reference=incompatible_edit_source,
        )

        self.assertIsNone(unknown_result.reference)
        self.assertEqual(unknown_result.reference_removed["rule"], "reference_outfit_unknown")
        self.assertIs(compatible_result.reference, compatible)
        self.assertIsNone(compatible_result.reference_removed)
        self.assertIs(edit_result.reference, incompatible_edit_source)
        self.assertIsNone(edit_result.reference_removed)

    def test_reference_removal_preview_redacts_paths_with_spaces(self) -> None:
        wardrobe = PhotoWardrobeDecision(
            rule_id="explicit_prompt",
            category="school_uniform",
            lock_outfit=True,
            reference_roles=("identity", "outfit"),
            effective_reference_roles=("identity",),
        )

        resolved = resolve_photo_prompt_context(
            wardrobe=wardrobe,
            sections=(PhotoPromptSection("request", "user_request", "换成校服"),),
            prompt_format="traditional",
            workflow_kind="selfie",
            reference={
                "source": r"C:\Users\Example User\reference image.png",
                "reference_roles": ["identity", "outfit"],
                "outfit_category": "sleepwear",
            },
        )

        self.assertEqual(resolved.reference_removed["preview"], "[path]")

    def test_exclusion_only_removes_a_specific_outfit_item(self) -> None:
        wardrobe = PhotoWardrobeDecision(
            rule_id="explicit_exclusion",
            mode="exclusion",
            source="explicit_prompt",
            excluded_outfit_text="不要白衬衫",
        )

        resolved = resolve_photo_prompt_context(
            wardrobe=wardrobe,
            sections=(
                PhotoPromptSection("request", "user_request", "在街边拍照", negative="白衬衫"),
                PhotoPromptSection(
                    "snapshot",
                    "scene_context",
                    "今日穿搭：白衬衫和牛仔裤；当前位置：街边",
                ),
            ),
            prompt_format="traditional",
            workflow_kind="selfie",
        )

        snapshot = next(section for section in resolved.prompt_sections if section.name == "snapshot")
        self.assertEqual(snapshot.positive, "当前位置：街边")
        positive = resolved.final_prompt.split("Negative prompt:", 1)[0]
        self.assertNotIn("白衬衫", positive)
        self.assertTrue(
            any(item["rule"] == "excluded_outfit_item" for item in resolved.removed_conflicts)
        )

    def test_traditional_and_natural_language_formats_share_sanitized_content(self) -> None:
        wardrobe = PhotoWardrobeDecision(rule_id="none")
        sections = (
            PhotoPromptSection(
                "request",
                "user_request",
                positive="a portrait by the window",
                negative="watermark",
            ),
            PhotoPromptSection("scene", "scene_context", "soft morning light"),
        )

        traditional = resolve_photo_prompt_context(
            wardrobe=wardrobe,
            sections=sections,
            prompt_format="traditional",
            workflow_kind="portrait",
        )
        natural = resolve_photo_prompt_context(
            wardrobe=wardrobe,
            sections=sections,
            prompt_format="natural_language",
            workflow_kind="portrait",
        )

        self.assertIn("Positive prompt:", traditional.final_prompt)
        self.assertIn("Negative prompt:", traditional.final_prompt)
        self.assertNotIn("Positive prompt:", natural.final_prompt)
        self.assertNotIn("Negative prompt:", natural.final_prompt)
        self.assertIn("Avoid watermark.", natural.final_prompt)
        self.assertIn("a portrait by the window", natural.final_prompt)
        self.assertIn("soft morning light", natural.final_prompt)
        self.assertEqual(traditional.prompt_sections, natural.prompt_sections)

    def test_nai_format_avoids_square_bracket_section_labels_and_keeps_negative_weight(self) -> None:
        resolved = resolve_photo_prompt_context(
            wardrobe=PhotoWardrobeDecision(rule_id="none"),
            sections=(
                PhotoPromptSection(
                    "request",
                    "user_request",
                    positive="{solo girl}, rainy window",
                    negative="text, watermark",
                ),
                PhotoPromptSection("scene", "scene_context", "soft morning light"),
            ),
            prompt_format="nai",
            workflow_kind="portrait",
        )

        self.assertNotIn("[User image request]", resolved.final_prompt)
        self.assertIn("User image request:", resolved.final_prompt)
        self.assertIn("{solo girl}, rainy window", resolved.final_prompt)
        self.assertIn("-1.5::text, watermark::", resolved.final_prompt)

    def test_prompt_sections_preserve_user_text_and_budget_lower_priority_groups(self) -> None:
        resolved = resolve_photo_prompt_context(
            wardrobe=PhotoWardrobeDecision(rule_id="none"),
            sections=(
                PhotoPromptSection("user", "user_request", "u" * 900, "n" * 400),
                PhotoPromptSection("wardrobe", "wardrobe_decision", "w" * 600, "d" * 180),
                PhotoPromptSection("scene", "scene_context", "s" * 500, "e" * 180),
                PhotoPromptSection("memory", "visual_memory", "m" * 500),
                PhotoPromptSection("preset", "preset", "p" * 220),
                PhotoPromptSection("fixed", "fixed_prompt", "f" * 180),
                PhotoPromptSection("edit", "edit_contract", "i" * 260),
                PhotoPromptSection("composition", "composition", "c" * 260),
                PhotoPromptSection(
                    "recent",
                    "recent_continuity",
                    "Recent-photo continuity: " + "r" * 600,
                ),
            ),
            prompt_format="traditional",
            workflow_kind="portrait",
        )
        by_name = {section.name: section for section in resolved.prompt_sections}

        self.assertEqual(by_name["user"].positive, "u" * 900)
        self.assertLessEqual(len(by_name["wardrobe"].positive), 420)
        self.assertLessEqual(
            len(by_name["scene"].positive) + len(by_name["memory"].positive),
            700,
        )
        self.assertLessEqual(len(by_name["preset"].positive), 140)
        self.assertLessEqual(len(by_name["fixed"].positive), 100)
        self.assertLessEqual(len(by_name["recent"].positive), 460)
        self.assertLessEqual(
            sum(len(by_name[name].positive) for name in ("edit", "composition", "recent")),
            680,
        )
        self.assertEqual(by_name["user"].negative, "n" * 400)
        self.assertLessEqual(
            sum(section.negative and len(section.negative) or 0 for section in resolved.prompt_sections if section.source != "user_request"),
            230,
        )

    def test_protected_sections_bypass_all_prompt_budgets(self) -> None:
        fixed_prompt = (
            "Overall Physique: preserve the complete body proportions, facial structure, "
            "natural lip color, and stable identity without simplifying any rule."
        )
        composition_negative = (
            "duplicate character, twins, multiple people, multiple outfits, outfit comparison, "
            "before and after, split screen, side-by-side panels, diptych, collage, character sheet"
        )
        user_request = "用户明确要求：" + "保留这段完整需求；" * 80
        resolved = resolve_photo_prompt_context(
            wardrobe=PhotoWardrobeDecision(rule_id="none"),
            sections=(
                PhotoPromptSection(
                    "request",
                    "user_request",
                    user_request,
                    protected=True,
                ),
                PhotoPromptSection(
                    "decision",
                    "wardrobe_decision",
                    negative="generic exclusion " * 30,
                ),
                PhotoPromptSection(
                    "global_fixed_prompt",
                    "fixed_prompt",
                    fixed_prompt,
                    protected=True,
                ),
                PhotoPromptSection(
                    "composition",
                    "composition",
                    negative=composition_negative,
                    protected=True,
                ),
            ),
            prompt_format="traditional",
            workflow_kind="selfie",
        )
        by_name = {section.name: section for section in resolved.prompt_sections}

        self.assertEqual(by_name["request"].positive, user_request)
        self.assertEqual(by_name["global_fixed_prompt"].positive, fixed_prompt)
        self.assertEqual(by_name["composition"].negative, composition_negative)

    def test_budget_compaction_keeps_complete_words(self) -> None:
        tokens = ["x" * 60, *(f"token{index:03d}" for index in range(40))]
        resolved = resolve_photo_prompt_context(
            wardrobe=PhotoWardrobeDecision(rule_id="none"),
            sections=(
                PhotoPromptSection(
                    "fixed",
                    "fixed_prompt",
                    " ".join(tokens),
                ),
            ),
            prompt_format="traditional",
            workflow_kind="selfie",
        )
        fixed = resolved.prompt_sections[0].positive
        fragments = fixed.replace("... [section compacted] ...", "").split()

        self.assertTrue(fragments)
        self.assertTrue(all(fragment in tokens for fragment in fragments))

    def test_custom_outfit_keeps_the_authoritative_decision_section(self) -> None:
        wardrobe = PhotoWardrobeDecision(
            rule_id="explicit_prompt",
            mode="explicit_prompt",
            source="explicit_prompt",
            category="custom_outfit",
            lock_outfit=True,
            positive_instruction="Wear the requested red camisole dress.",
            requested_outfit_text="换成红色吊带长裙",
        )

        resolved = resolve_photo_prompt_context(
            wardrobe=wardrobe,
            sections=(
                PhotoPromptSection("request", "user_request", "换成红色吊带长裙"),
                PhotoPromptSection(
                    "decision",
                    "wardrobe_decision",
                    "Wardrobe decision: wear the requested red camisole dress.",
                ),
                PhotoPromptSection("scene", "scene_context", "今日穿搭：蓝色睡衣"),
            ),
            prompt_format="traditional",
            workflow_kind="selfie",
        )

        decision = next(section for section in resolved.prompt_sections if section.name == "decision")
        scene = next(section for section in resolved.prompt_sections if section.name == "scene")
        self.assertEqual(
            decision.positive,
            "Wardrobe decision: wear the requested red camisole dress.",
        )
        self.assertEqual(scene.positive, "")

    def test_neutral_identity_and_single_outfit_composition_are_preserved(self) -> None:
        wardrobe = PhotoWardrobeDecision(
            rule_id="explicit_prompt",
            mode="explicit_prompt",
            source="explicit_prompt",
            category="school_uniform",
            lock_outfit=True,
        )

        resolved = resolve_photo_prompt_context(
            wardrobe=wardrobe,
            sections=(
                PhotoPromptSection("request", "user_request", "wear a school uniform"),
                PhotoPromptSection(
                    "memory",
                    "visual_memory",
                    "preserve identity, face and hairstyle; copy the exact old outfit",
                ),
                PhotoPromptSection(
                    "composition",
                    "composition",
                    "exactly one character wearing one coherent outfit in one continuous scene",
                ),
            ),
            prompt_format="natural_language",
            workflow_kind="selfie",
        )
        by_name = {section.name: section for section in resolved.prompt_sections}

        self.assertEqual(by_name["memory"].positive, "preserve identity, face and hairstyle")
        self.assertEqual(
            by_name["composition"].positive,
            "exactly one character wearing one coherent outfit in one continuous scene",
        )

    def test_sleepwear_preset_keeps_loungewear_and_moves_embedded_exclusions_negative(self) -> None:
        wardrobe = PhotoWardrobeDecision(
            rule_id="explicit_prompt",
            category="sleepwear",
            lock_outfit=True,
        )
        preset_text = (
            "sleepwear or bedtime loungewear portrait matching the explicit clothing request and selected reference, "
            "exactly one coherent sleepwear outfit, preserve the character identity, natural home or bedtime context, "
            "do not restore a daytime outfit, coat, school uniform, or commuter layers unless explicitly requested"
        )

        resolved = resolve_photo_prompt_context(
            wardrobe=wardrobe,
            sections=(
                PhotoPromptSection("request", "user_request", "wear conservative sleepwear"),
                PhotoPromptSection("sleepwear_preset", "preset", preset_text),
            ),
            prompt_format="traditional",
            workflow_kind="selfie",
        )
        preset = next(
            section
            for section in resolved.prompt_sections
            if section.name == "sleepwear_preset"
        )

        self.assertIn("sleepwear or bedtime loungewear portrait", preset.positive)
        self.assertIn("natural home or bedtime context", preset.positive)
        self.assertNotIn("do not restore", preset.positive.lower())
        self.assertIn("daytime outfit", preset.negative)
        self.assertIn("coat", preset.negative)
        self.assertIn("school uniform", preset.negative)
        self.assertEqual(resolved.residual_conflicts, ())

    def test_embedded_negative_cannot_negate_authoritative_sleepwear(self) -> None:
        wardrobe = PhotoWardrobeDecision(
            rule_id="explicit_prompt",
            category="sleepwear",
            lock_outfit=True,
        )

        resolved = resolve_photo_prompt_context(
            wardrobe=wardrobe,
            sections=(
                PhotoPromptSection("request", "user_request", "wear sleepwear"),
                PhotoPromptSection(
                    "preset",
                    "preset",
                    "soft bedroom light, avoid sleepwear",
                ),
            ),
            prompt_format="traditional",
            workflow_kind="selfie",
        )
        preset = next(section for section in resolved.prompt_sections if section.name == "preset")

        self.assertEqual(preset.positive, "soft bedroom light")
        self.assertEqual(preset.negative, "")
        self.assertTrue(
            any(
                item["rule"] == "authoritative_wardrobe_negated"
                for item in resolved.detected_conflicts
            )
        )


if __name__ == "__main__":
    unittest.main()
