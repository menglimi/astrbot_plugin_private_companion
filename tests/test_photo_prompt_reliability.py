# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import AsyncMock

from astrbot_plugin_private_companion.command_handlers import CommandHandlersMixin

if find_spec("astrbot_plugin_image_companion") is not None:
    from astrbot_plugin_image_companion.image_runtime import (
        PhotoGenerationResult,
        PhotoWardrobeDecision,
        ProactiveMessageMixin,
    )
    from astrbot_plugin_image_companion.photo_prompt_context import (
        PhotoPromptSection,
        resolve_photo_prompt_context,
    )
    from astrbot_plugin_image_companion.photo_wardrobe_decision import (
        analyze_photo_wardrobe,
        resolve_photo_wardrobe_decision,
    )
else:
    from astrbot_plugin_private_companion.proactive_message import (
        PhotoGenerationResult,
        PhotoWardrobeDecision,
        ProactiveMessageMixin,
    )
    from astrbot_plugin_private_companion.photo_prompt_context import (
        PhotoPromptSection,
        resolve_photo_prompt_context,
    )
    from astrbot_plugin_private_companion.photo_wardrobe_decision import (
        analyze_photo_wardrobe,
        resolve_photo_wardrobe_decision,
    )


class _PhotoReliabilityHarness(CommandHandlersMixin, ProactiveMessageMixin):
    def __init__(self, root: Path, reference: dict | None = None) -> None:
        self.data_dir = str(root)
        self.data: dict = {}
        self.config: dict = {}
        self.photo_generation_backend = "external"
        self.photo_generation_prompt_format = "traditional"
        self.photo_generation_scene_presets: list = []
        self.photo_generation_fixed_prompt = ""
        self.photo_generation_trace_max_size_kb = 2048
        self.natural_language_photo_extra_prompt = ""
        self.reference = deepcopy(reference or {})
        self.generated_path = root / "generated.png"
        self.generated_path.write_bytes(b"generated image")
        self.external_calls: list[dict] = []

    def _save_data_sync(self, **_kwargs) -> None:
        return None

    def _photo_generation_selfie_schedule_scene_hint(self) -> str:
        return (
            "当前日程：在卧室休息；当前位置：家里；当前场景：居家室内；"
            "今日穿搭：white shirt, navy vest, blazer, burgundy ribbon, trousers, watch；"
            "天气背景：夜间微凉"
        )

    def _get_photo_style_instruction(self) -> tuple[str, str]:
        return "默认", "natural image style"

    async def _select_photo_reference_candidate_async(self, *_args, **_kwargs) -> dict:
        return deepcopy(self.reference)

    def _photo_generation_backend_config_summary(self) -> str:
        return "test-backend"

    def _external_photo_available(self) -> bool:
        return True

    async def _run_external_photo_generation(self, prompt_text: str, **kwargs):
        self.external_calls.append({"prompt_text": prompt_text, **kwargs})
        # Deliberately omit any textual "used reference" marker. Usage must be structural.
        return str(self.generated_path), "backend completed"

    def _resolve_photo_wardrobe_decision(
        self,
        *,
        workflow_kind: str,
        prompt_text: str,
        reference=None,
        scene_snapshot: str = "",
    ) -> PhotoWardrobeDecision:
        return resolve_photo_wardrobe_decision(
            workflow_kind=workflow_kind,
            prompt_text=prompt_text,
            intent=analyze_photo_wardrobe(prompt_text),
            reference=reference,
            scene_context=scene_snapshot,
            base_prompt=self._apply_photo_generation_prompt_format(prompt_text),
            available_presets=self._photo_generation_scene_presets().keys(),
        )

    @staticmethod
    def _photo_generation_preset_names_for_decision(
        _workflow_kind: str,
        _prompt_text: str,
        decision: PhotoWardrobeDecision,
    ) -> list[str]:
        return list(decision.selected_presets)

    def _build_final_photo_prompt(
        self,
        *,
        base_prompt: str,
        workflow_kind: str,
        scene_hint: str,
        reference,
        wardrobe: PhotoWardrobeDecision,
        preset_section: str = "",
        continuity_section: str = "",
    ) -> tuple[str, dict[str, str]]:
        user_positive, user_negative = self._photo_prompt_split_formatted(base_prompt)
        wardrobe_positive, wardrobe_negative = self._photo_generation_reference_wardrobe_section(
            reference or {},
            wardrobe,
        )
        composition_positive, composition_negative = self._photo_generation_composition_sections(
            workflow_kind,
            user_positive,
        )
        sections = (
            PhotoPromptSection("user_request", "user_request", user_positive, user_negative, True),
            PhotoPromptSection("wardrobe_decision", "wardrobe_decision", wardrobe_positive, wardrobe_negative),
            PhotoPromptSection("scene_context", "scene_context", scene_hint),
            PhotoPromptSection("scene_preset", "preset", preset_section),
            PhotoPromptSection("composition", "composition", composition_positive, composition_negative),
            PhotoPromptSection("recent_continuity", "recent_continuity", continuity_section),
        )
        resolved = resolve_photo_prompt_context(
            wardrobe=wardrobe,
            sections=sections,
            prompt_format=self._photo_generation_prompt_format_mode(),
            workflow_kind=workflow_kind,
            reference=reference,
        )
        by_name = {section.name: section for section in resolved.prompt_sections}

        def joined(*names: str, negative: bool = False) -> str:
            return "\n".join(
                getattr(by_name[name], "negative" if negative else "positive")
                for name in names
                if name in by_name and getattr(by_name[name], "negative" if negative else "positive")
            )

        return resolved.final_prompt, {
            "user_request": joined("user_request"),
            "reference_wardrobe": joined("wardrobe_decision"),
            "scene_style_preset": joined("scene_context", "scene_preset"),
            "composition_continuity": joined("composition", "recent_continuity"),
            "negative": joined(*by_name.keys(), negative=True),
        }


class _StructuredPresetSelectionHarness(_PhotoReliabilityHarness):
    def __init__(self, root: Path, candidates: list[dict]) -> None:
        super().__init__(root)
        self.enable_photo_reference_image = True
        self.candidates = deepcopy(candidates)

    async def _photo_reference_candidates_async(self, **_kwargs) -> list[dict]:
        return deepcopy(self.candidates)

    async def _select_photo_reference_candidate_async(self, *args, **kwargs) -> dict:
        return await ProactiveMessageMixin._select_photo_reference_candidate_async(self, *args, **kwargs)


class PhotoPromptReliabilityTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _write_reference(root: Path, name: str = "sleepwear.png") -> Path:
        path = root / name
        path.write_bytes(b"reference image")
        return path

    def test_subject_count_contract_blocks_unreferenced_group_photos(self) -> None:
        positive, negative = ProactiveMessageMixin._photo_generation_subject_count_contract(
            "text2img",
            "我和朋友拍一张合影",
            explicit_reference_supplied=False,
        )
        self.assertIn("at most one recognizable human character", positive)
        self.assertIn("group photo", negative)

        allowed_positive, allowed_negative = ProactiveMessageMixin._photo_generation_subject_count_contract(
            "selfie",
            "把这张双人照改成咖啡店合影",
            explicit_reference_supplied=True,
        )
        self.assertIn("explicit source reference", allowed_positive)
        self.assertIn("unreferenced extra people", allowed_negative)

        scene_positive, scene_negative = ProactiveMessageMixin._photo_generation_subject_count_contract(
            "text2img",
            "节日街道上的自然人群",
            explicit_reference_supplied=False,
        )
        self.assertEqual((scene_positive, scene_negative), ("", ""))

    async def test_home_sleepwear_reference_controls_wardrobe_removes_daily_outfit_and_preset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference_path = self._write_reference(root)
            reference = {
                "id": "library_sleepwear",
                "path": str(reference_path),
                "source": str(reference_path),
                "kind": "library",
                "note": "奶油色睡衣，卧室、睡前和居家休息时使用",
                "reference_roles": ["identity", "outfit"],
                "outfit_category": "sleepwear",
                "outfit_lock_default": True,
                "scene_categories": ["home", "bedroom"],
                "preferred_preset": "居家睡衣",
            }
            harness = _PhotoReliabilityHarness(root, reference)

            backend, image_path, note = await harness._generate_photo_image(
                workflow_kind="selfie",
                prompt_text="坐在床边拍一张自然自拍",
                session_key="default:FriendMessage:10001",
            )

            self.assertEqual((backend, image_path, note), ("在线图片 API", str(harness.generated_path), "backend completed"))
            self.assertEqual(len(harness.external_calls), 1)
            call = harness.external_calls[0]
            self.assertEqual(call["reference_image_path"], str(reference_path))
            final_prompt = call["prompt_text"]
            self.assertIn("outfit category=sleepwear", final_prompt)
            self.assertIn("authoritative source for identity and the complete visible outfit", final_prompt)
            self.assertNotIn("今日穿搭：", final_prompt)
            self.assertNotIn("navy vest", final_prompt)
            self.assertNotIn("burgundy ribbon", final_prompt)

            record = harness.data["recent_photo_generations"][0]
            self.assertEqual(record["wardrobe_mode"], "reference_outfit")
            self.assertEqual(record["wardrobe_category"], "sleepwear")
            self.assertTrue(record["outfit_locked"])
            self.assertTrue(record["daily_outfit_removed"])
            self.assertEqual(record["presets"], ["居家睡衣"])
            self.assertIn("daily_outfit_context_removed", record["removed_conflicts"])

            debug_payload = json.loads(Path(record["prompt_path"]).read_text(encoding="utf-8"))
            self.assertNotIn("今日穿搭：", debug_payload["scene_context_after"])
            self.assertEqual(debug_payload["wardrobe_decision"]["mode"], "reference_outfit")
            self.assertEqual(debug_payload["presets"], ["居家睡衣"])

    async def test_structured_sleepwear_preset_stays_positive_and_selects_matching_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sleepwear_path = self._write_reference(root, "sleepwear.png")
            daily_path = self._write_reference(root, "daily.png")
            persona_path = self._write_reference(root, "persona.png")
            harness = _StructuredPresetSelectionHarness(
                root,
                [
                    {
                        "id": "library_sleepwear",
                        "path": str(sleepwear_path),
                        "kind": "library",
                        "note": "奶油色睡衣，适用于卧室、睡前和居家休息",
                        "reference_roles": ["identity", "outfit"],
                        "outfit_category": "sleepwear",
                        "outfit_lock_default": True,
                        "scene_categories": ["home", "bedroom"],
                        "preferred_preset": "居家睡衣",
                    },
                    {
                        "id": "daily_outfit",
                        "path": str(daily_path),
                        "kind": "daily_outfit",
                        "note": "今天的外出穿搭",
                        "reference_roles": ["identity", "outfit"],
                        "outfit_category": "daily_outfit",
                        "outfit_lock_default": True,
                        "scene_categories": ["outdoor"],
                        "preferred_preset": "日常穿搭",
                    },
                    {
                        "id": "persona_default",
                        "path": str(persona_path),
                        "kind": "persona",
                        "note": "基础人物身份和外貌参考",
                        "reference_roles": ["identity"],
                        "outfit_lock_default": False,
                    },
                ],
            )
            harness._llm_call = AsyncMock(return_value="1")
            source_prompt = (
                "Positive prompt: at a dorm desk after evening skincare, calm portrait. "
                "Negative prompt: text, watermark, cropped head."
            )

            await harness._generate_photo_image(
                workflow_kind="selfie",
                prompt_text=source_prompt,
                session_key="default:FriendMessage:10001",
                requested_scene_preset="居家睡衣",
            )

            call = harness.external_calls[0]
            self.assertEqual(call["reference_image_path"], str(sleepwear_path))
            final_prompt = call["prompt_text"]
            positive, negative = harness._photo_prompt_split_formatted(final_prompt)
            self.assertIn("outfit category=sleepwear", positive)
            self.assertNotIn("outfit category=sleepwear", negative)
            self.assertNotIn("white shirt", final_prompt)
            self.assertNotIn("navy vest", final_prompt)

            record = harness.data["recent_photo_generations"][0]
            self.assertEqual(record["requested_scene_preset"], "居家睡衣")
            self.assertEqual(record["wardrobe_mode"], "reference_outfit")
            self.assertEqual(record["wardrobe_category"], "sleepwear")
            self.assertTrue(record["outfit_locked"])
            self.assertTrue(record["daily_outfit_removed"])
            self.assertEqual(record["presets"], ["居家睡衣"])
            debug_payload = json.loads(Path(record["prompt_path"]).read_text(encoding="utf-8"))
            self.assertEqual(debug_payload["requested_scene_preset"], "居家睡衣")
            self.assertEqual(debug_payload["wardrobe_decision"]["category"], "sleepwear")

    async def test_internal_today_outfit_boilerplate_does_not_override_selected_sleepwear(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference_path = self._write_reference(root)
            reference = {
                "id": "library_sleepwear",
                "path": str(reference_path),
                "source": str(reference_path),
                "kind": "library",
                "note": "奶油色睡衣，卧室、睡前和居家休息时使用",
                "reference_roles": ["identity", "outfit"],
                "outfit_category": "sleepwear",
                "outfit_lock_default": True,
                "preferred_preset": "居家睡衣",
            }
            for has_reference in (False, True):
                with self.subTest(has_reference=has_reference):
                    harness = _PhotoReliabilityHarness(root, reference)
                    constructed_prompt = harness._build_natural_language_photo_prompt(
                        prompt="来张自拍",
                        kind="selfie",
                        has_reference=has_reference,
                        memory_context="今日穿搭：白衬衫和西装外套；地点：卧室",
                    )
                    self.assertIn("user request: 来张自拍", constructed_prompt)
                    self.assertIn("preserve character identity and stable appearance", constructed_prompt)
                    self.assertNotIn("keep today's outfit and character appearance", constructed_prompt)
                    self.assertIn("visual continuity reference: 今日穿搭：", constructed_prompt)

                    await harness._generate_photo_image(
                        workflow_kind="selfie",
                        prompt_text=constructed_prompt,
                        session_key="default:FriendMessage:10001",
                    )

                    record = harness.data["recent_photo_generations"][0]
                    self.assertEqual(record["wardrobe_mode"], "reference_outfit")
                    self.assertEqual(record["wardrobe_category"], "sleepwear")
                    self.assertTrue(record["outfit_locked"])
                    self.assertTrue(record["daily_outfit_removed"])
                    self.assertEqual(record["presets"], ["居家睡衣"])
                    final_prompt = harness.external_calls[0]["prompt_text"]
                    self.assertNotIn("今日穿搭：", final_prompt)
                    self.assertNotIn("白衬衫", final_prompt)
                    self.assertNotIn("西装外套", final_prompt)
                    self.assertNotIn("navy vest", final_prompt)
                    self.assertNotIn("keep today's outfit and character appearance", final_prompt)
                    self.assertIn("visual continuity reference: 地点：卧室", final_prompt)
                    self.assertIn("outfit category=sleepwear", final_prompt)
                    self.assertIn("generated_daily_outfit_continuity_removed", record["removed_conflicts"])
                    self.assertNotIn("conflicting_wardrobe:daily_outfit", record["conflicts"])

    def test_explicit_outfit_categories_win_and_choose_matching_final_preset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference_path = self._write_reference(root)
            harness = _PhotoReliabilityHarness(root)
            sleepwear_reference = {
                "id": "sleepwear-reference",
                "path": str(reference_path),
                "kind": "library",
                "reference_roles": ["identity", "outfit"],
                "outfit_category": "sleepwear",
                "outfit_lock_default": True,
                "preferred_preset": "居家睡衣",
            }
            cases = (
                ("穿校服在校门口拍照", "school_uniform", "校服人像"),
                ("换成魔法少女 COS 服自拍", "cosplay", "COS自拍"),
                ("穿红色晚礼服去宴会厅拍照", "formalwear", "礼服人像"),
                ("在海边穿泳装自拍", "swimwear", "泳装人像"),
                ("健身结束后穿运动服拍一张", "sportswear", "运动服人像"),
                ("在家换成宽松居家服自拍", "homewear", "居家服"),
            )

            for request, category, preset in cases:
                with self.subTest(request=request):
                    decision = harness._resolve_photo_wardrobe_decision(
                        workflow_kind="selfie",
                        prompt_text=request,
                        reference=sleepwear_reference,
                        scene_snapshot="当前位置：家里；今日穿搭：白衬衫和西装外套",
                    )
                    self.assertEqual(decision.mode, "explicit_prompt")
                    self.assertEqual(decision.source, "user_prompt")
                    self.assertEqual(decision.category, category)
                    self.assertEqual(decision.preset_name, preset)
                    self.assertTrue(decision.lock_outfit)
                    self.assertTrue(decision.remove_daily_outfit_context)
                    self.assertIn("identity", decision.effective_reference_roles)
                    self.assertNotIn("outfit", decision.effective_reference_roles)
                    self.assertEqual(
                        harness._photo_generation_preset_names_for_decision("selfie", request, decision),
                        [preset],
                    )

    def test_identity_only_reference_never_locks_incidental_clothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference_path = self._write_reference(root, "persona.png")
            harness = _PhotoReliabilityHarness(root)
            reference = {
                "id": "persona-default",
                "path": str(reference_path),
                "kind": "persona",
                "note": "只用于人物身份、脸和发型",
                "reference_roles": ["identity"],
                "outfit_category": "",
                "outfit_lock_default": False,
            }

            with_daily_outfit = harness._resolve_photo_wardrobe_decision(
                workflow_kind="selfie",
                prompt_text="在窗边比个心",
                reference=reference,
                scene_snapshot="当前位置：家里；今日穿搭：蓝色连衣裙",
            )
            identity_only = harness._resolve_photo_wardrobe_decision(
                workflow_kind="selfie",
                prompt_text="在窗边比个心",
                reference=reference,
                scene_snapshot="当前位置：家里",
            )

            self.assertEqual(with_daily_outfit.mode, "daily_outfit_context")
            self.assertEqual(with_daily_outfit.category, "daily_outfit")
            self.assertFalse(with_daily_outfit.lock_outfit)
            self.assertFalse(with_daily_outfit.remove_daily_outfit_context)
            self.assertEqual(with_daily_outfit.effective_reference_roles, ("identity",))
            self.assertEqual(identity_only.mode, "identity_only")
            self.assertFalse(identity_only.lock_outfit)
            self.assertEqual(identity_only.effective_reference_roles, ("identity",))

    def test_recent_generation_metadata_matches_long_output_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = _PhotoReliabilityHarness(Path(directory))
            output_path = "C:/" + ("nested  folder/" * 48) + "generated  image.png"
            prompt_path = "C:/debug/" + ("prompt  folder/" * 36) + "trace  file.json"
            harness._record_recent_photo_generation(
                trace_id="long-path",
                session_key="default:FriendMessage:10001",
                workflow_kind="selfie",
                backend="external",
                ok=True,
                prompt_text="a selfie",
                image_path=output_path,
                reference_image_path="C:/references/sleepwear.png",
                reference_used=True,
                reference_candidate={"id": "sleepwear", "kind": "library"},
                prompt_path=prompt_path,
            )

            metadata = harness._photo_generation_result_metadata(
                image_path=output_path,
                session_key="default:FriendMessage:10001",
            )

            self.assertEqual(metadata["path"], output_path)
            self.assertEqual(metadata["prompt_path"], prompt_path)
            self.assertTrue(metadata["reference_used"])

    def test_daily_outfit_aliases_are_removed_from_conflicting_scene_context(self) -> None:
        for label in ("今日穿搭", "当天穿搭", "日常穿搭", "today's outfit", "daily outfit"):
            with self.subTest(label=label):
                prompt = "在卧室穿睡衣拍一张自拍"
                decision = resolve_photo_wardrobe_decision(
                    workflow_kind="selfie",
                    prompt_text=prompt,
                    intent=analyze_photo_wardrobe(prompt),
                    reference=None,
                    scene_context=f"{label}：白衬衫和西装外套；地点：卧室；天气：微凉",
                    base_prompt=prompt,
                    available_presets=("居家睡衣",),
                )
                self.assertNotIn("白衬衫", decision.scene_context)
                self.assertNotIn("西装外套", decision.scene_context)
                self.assertIn("地点：卧室", decision.scene_context)
                self.assertIn("daily_outfit_context_removed", decision.adjustments)

    def test_partitioned_prompt_budget_preserves_tail_wardrobe_and_negative_constraints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = _PhotoReliabilityHarness(Path(directory))
            wardrobe = PhotoWardrobeDecision(
                mode="explicit_prompt",
                source="user_prompt",
                category="formalwear",
                lock_outfit=True,
                remove_daily_outfit_context=True,
                preset_name="礼服人像",
                reference_roles=("identity",),
                effective_reference_roles=("identity",),
                positive_instruction=(
                    "WARDROBE_LOCK_SENTINEL: render exactly one coherent red formal outfit and preserve its silhouette. "
                    + "material detail " * 45
                ),
                negative_instruction="never restore the daytime blazer",
            )
            base_prompt = (
                "Positive prompt: "
                + "rich subject detail " * 110
                + " USER_TAIL_SENTINEL. Negative prompt: "
                + "artifact detail, " * 80
                + "NEGATIVE_TAIL_SENTINEL"
            )

            final_prompt, sections = harness._build_final_photo_prompt(
                base_prompt=base_prompt,
                workflow_kind="selfie",
                scene_hint="宴会厅灯光和环境细节 " * 90,
                reference={"id": "persona", "reference_roles": ["identity"]},
                wardrobe=wardrobe,
                preset_section="Scene preset: formalwear portrait; " + "formal style detail " * 80,
                continuity_section="continuity detail " * 90,
            )

            unbudgeted_length = sum(
                len(value)
                for value in (
                    base_prompt,
                    wardrobe.positive_instruction,
                    wardrobe.negative_instruction,
                    "宴会厅灯光和环境细节 " * 90,
                    "Scene preset: formalwear portrait; " + "formal style detail " * 80,
                    "continuity detail " * 90,
                )
            )
            self.assertGreater(len(final_prompt), 1800)
            self.assertLess(len(final_prompt), unbudgeted_length)
            self.assertIn("USER_TAIL_SENTINEL", final_prompt)
            self.assertIn("WARDROBE_LOCK_SENTINEL", final_prompt)
            self.assertIn("Negative prompt:", final_prompt)
            self.assertIn("NEGATIVE_TAIL_SENTINEL", final_prompt)
            self.assertIn("WARDROBE_LOCK_SENTINEL", sections["reference_wardrobe"])
            self.assertIn("NEGATIVE_TAIL_SENTINEL", sections["negative"])

    def test_conflict_detector_reports_stale_daily_outfit_and_wrong_preset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = _PhotoReliabilityHarness(Path(directory))
            wardrobe = PhotoWardrobeDecision(
                mode="reference_outfit",
                source="selected_reference",
                category="sleepwear",
                lock_outfit=True,
                remove_daily_outfit_context=True,
                preset_name="居家睡衣",
            )
            conflicting_prompt = (
                "Positive prompt:\n"
                "今日穿搭：white shirt and navy blazer. "
                "Scene preset: daily outfit portrait: polished commuter layers.\n\n"
                "Negative prompt:\nwatermark"
            )

            resolved = resolve_photo_prompt_context(
                wardrobe=wardrobe,
                sections=(
                    PhotoPromptSection(
                        "scene_context",
                        "scene_context",
                        "今日穿搭：white shirt and navy blazer.",
                    ),
                    PhotoPromptSection(
                        "scene_preset",
                        "preset",
                        "Scene preset: daily outfit portrait: polished commuter layers.",
                    ),
                    PhotoPromptSection("negative", "user_request", negative="watermark", protected=True),
                ),
                prompt_format="traditional",
                workflow_kind="selfie",
            )
            rules = {item["rule"] for item in resolved.detected_conflicts}

            self.assertIn("daily_outfit_conflict", rules)
            self.assertEqual(rules, {"daily_outfit_conflict"})
            self.assertNotIn("white shirt", resolved.final_prompt)
            self.assertNotIn("daily outfit portrait", resolved.final_prompt)
            self.assertIn("watermark", resolved.final_prompt)
            self.assertEqual(resolved.residual_conflicts, ())

    def test_bedtime_loungewear_wording_remains_compatible_with_sleepwear(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = _PhotoReliabilityHarness(Path(directory))
            wardrobe = PhotoWardrobeDecision(
                mode="reference_outfit",
                source="selected_reference",
                category="sleepwear",
                lock_outfit=True,
                preset_name="居家睡衣",
            )
            prompt = (
                "Positive prompt:\n"
                "Scene preset: home sleepwear portrait with soft bedtime loungewear.\n\n"
                "Negative prompt:\nwatermark"
            )

            resolved = resolve_photo_prompt_context(
                wardrobe=wardrobe,
                sections=(
                    PhotoPromptSection(
                        "scene_preset",
                        "preset",
                        "Scene preset: home sleepwear portrait with soft bedtime loungewear.",
                    ),
                    PhotoPromptSection("negative", "user_request", negative="watermark", protected=True),
                ),
                prompt_format="traditional",
                workflow_kind="selfie",
            )

            self.assertIn("watermark", resolved.final_prompt)
            self.assertIn("soft bedtime loungewear", resolved.final_prompt)
            self.assertEqual(resolved.detected_conflicts, ())
            self.assertEqual(resolved.removed_conflicts, ())
            self.assertEqual(resolved.residual_conflicts, ())

    def test_negative_outfit_request_is_separated_and_cannot_choose_forbidden_preset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = _PhotoReliabilityHarness(Path(directory))

            formatted = harness._apply_photo_generation_prompt_format("不要睡衣，改穿校服自拍")
            decision = harness._resolve_photo_wardrobe_decision(
                workflow_kind="selfie",
                prompt_text="不要睡衣，改穿校服自拍",
                reference=None,
                scene_snapshot="",
            )

            self.assertIn("Positive prompt: 改穿校服自拍", formatted)
            self.assertIn("Negative prompt: 睡衣", formatted)
            self.assertEqual(decision.category, "school_uniform")
            self.assertEqual(decision.excluded_categories, ("sleepwear",))
            self.assertEqual(
                harness._photo_generation_preset_names_for_decision("selfie", formatted, decision),
                ["校服人像"],
            )

            for prompt in (
                "不要睡衣，普通自拍",
                "Positive prompt: casual selfie. Negative prompt: sleepwear, pajamas.",
                "Create a casual selfie. Avoid sleepwear and pajamas.",
            ):
                with self.subTest(prompt=prompt):
                    exclusion = harness._resolve_photo_wardrobe_decision(
                        workflow_kind="selfie",
                        prompt_text=prompt,
                        reference=None,
                        scene_snapshot="",
                    )
                    normalized = harness._apply_photo_generation_prompt_format(prompt)
                    self.assertIn("sleepwear", exclusion.excluded_categories)
                    self.assertEqual(
                        harness._photo_generation_preset_names_for_decision("selfie", normalized, exclusion),
                        ["角色自拍"],
                    )

    def test_natural_language_exclusion_stays_out_of_positive_reference_section(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = _PhotoReliabilityHarness(Path(directory))
            harness.photo_generation_prompt_format = "natural_language"
            reference = {
                "id": "sleepwear_identity",
                "kind": "library",
                "reference_roles": ["identity", "outfit"],
                "outfit_category": "sleepwear",
                "outfit_lock_default": True,
            }
            base_prompt = harness._apply_photo_generation_prompt_format(
                "Create a casual selfie. Avoid sleepwear and pajamas."
            )
            wardrobe = harness._resolve_photo_wardrobe_decision(
                workflow_kind="selfie",
                prompt_text="Create a casual selfie. Avoid sleepwear and pajamas.",
                reference=reference,
                scene_snapshot="",
            )

            final_prompt, sections = harness._build_final_photo_prompt(
                base_prompt=base_prompt,
                workflow_kind="selfie",
                scene_hint="",
                reference=reference,
                wardrobe=wardrobe,
                preset_section="Scene preset: casual character selfie",
            )

            positive_prompt = final_prompt.split("\n\nAvoid ", 1)[0].lower()
            self.assertEqual(wardrobe.effective_reference_roles, ("identity",))
            self.assertNotIn("sleepwear", sections["user_request"].lower())
            self.assertNotIn("sleepwear", sections["reference_wardrobe"].lower())
            self.assertIn("outfit category=not active", sections["reference_wardrobe"].lower())
            self.assertNotIn("sleepwear", positive_prompt)
            self.assertIn("sleepwear", sections["negative"].lower())
            self.assertIn("Avoid ", final_prompt)

    async def test_nai_natural_exclusion_reaches_negative_user_section(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = _PhotoReliabilityHarness(Path(directory))
            harness.photo_generation_prompt_format = "nai"

            await harness._generate_photo_image(
                workflow_kind="text2img",
                prompt_text="一个女孩，不要水印",
                session_key="default:FriendMessage:10001",
            )

            record = harness.data["recent_photo_generations"][0]
            debug_payload = json.loads(Path(record["prompt_path"]).read_text(encoding="utf-8"))
            user_section = debug_payload["prompt_sections_after"]["user_request"]
            self.assertEqual(user_section["positive"], "一个女孩")
            self.assertEqual(user_section["negative"], "水印")
            self.assertNotIn("不要水印", harness.external_calls[0]["prompt_text"])

    async def test_edit_reference_path_keeps_source_role_even_when_it_matches_library(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference_path = self._write_reference(root)
            harness = _PhotoReliabilityHarness(root)

            async def candidates(**_kwargs):
                return [
                    {
                        "id": "library_sleepwear",
                        "path": str(reference_path),
                        "kind": "library",
                        "reference_roles": ["identity", "outfit"],
                        "outfit_category": "sleepwear",
                        "outfit_lock_default": True,
                    }
                ]

            harness._photo_reference_candidates_async = candidates
            selected = await ProactiveMessageMixin._photo_reference_candidate_for_path_async(
                harness,
                str(reference_path),
                workflow_kind="edit",
            )

            self.assertEqual(selected["id"], "explicit_reference")
            self.assertEqual(selected["kind"], "source")
            self.assertEqual(selected["reference_roles"], ["source"])
            self.assertEqual(selected["outfit_category"], "")
            self.assertFalse(selected["outfit_lock_default"])

    async def test_single_specialized_candidate_can_be_declined_when_explicitly_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference_path = self._write_reference(root)
            harness = _PhotoReliabilityHarness(root)
            harness.enable_photo_reference_image = True
            candidate = {
                "id": "only_sleepwear",
                "path": str(reference_path),
                "kind": "library",
                "note": "睡衣参考，仅用于睡前或卧室",
                "reference_roles": ["identity", "outfit"],
                "outfit_category": "sleepwear",
                "outfit_lock_default": True,
            }

            async def candidates(**_kwargs):
                return [deepcopy(candidate)]

            harness._photo_reference_candidates_async = candidates
            harness._recent_sent_photo_continuity_candidate = lambda *_args, **_kwargs: {}
            harness._task_provider = lambda *_args: ""
            harness._llm_call = AsyncMock(return_value="1")

            selected = await ProactiveMessageMixin._select_photo_reference_candidate_async(
                harness,
                "selfie",
                selection_context="Requested final image: 不要睡衣，普通自拍",
            )

            self.assertEqual(selected, {})
            selection_prompt = harness._llm_call.await_args.args[0]
            self.assertIn("0. 不使用这些候选参考图", selection_prompt)
            self.assertIn("明确否定某类服装", selection_prompt)

    def test_generic_and_source_to_target_outfit_requests_override_old_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference_path = self._write_reference(root)
            harness = _PhotoReliabilityHarness(root)
            daily_reference = {
                "id": "daily_outfit",
                "path": str(reference_path),
                "kind": "daily_outfit",
                "reference_roles": ["identity", "outfit"],
                "outfit_category": "daily_outfit",
                "outfit_lock_default": True,
                "preferred_preset": "日常穿搭",
            }

            cases = (
                ("把校服换成睡衣", "sleepwear", "居家睡衣"),
                ("把睡衣换成校服", "school_uniform", "校服人像"),
                ("换成红色连衣裙自拍", "custom_outfit", "日常穿搭"),
                ("别穿刚才那套，换件清爽衣服", "custom_outfit", "日常穿搭"),
            )
            for request, category, preset in cases:
                with self.subTest(request=request):
                    decision = harness._resolve_photo_wardrobe_decision(
                        workflow_kind="selfie",
                        prompt_text=request,
                        reference=daily_reference,
                        scene_snapshot="今日穿搭：白衬衫和西装外套",
                    )
                    self.assertEqual(decision.mode, "explicit_prompt")
                    self.assertEqual(decision.category, category)
                    self.assertEqual(decision.preset_name, preset)
                    self.assertTrue(decision.remove_daily_outfit_context)
                    self.assertNotIn("outfit", decision.effective_reference_roles)

    def test_structured_scene_budget_and_recent_continuity_keep_core_facts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference_path = self._write_reference(root, "recent.png")
            harness = _PhotoReliabilityHarness(root)
            recent_reference = {
                "id": "recent_sent_photo",
                "path": str(reference_path),
                "kind": "recent_sent_photo",
                "reference_roles": ["identity", "outfit", "scene", "continuity"],
                "outfit_category": "sleepwear",
                "outfit_lock_default": True,
                "preferred_preset": "居家睡衣",
            }
            wardrobe = harness._resolve_photo_wardrobe_decision(
                workflow_kind="selfie",
                prompt_text="给镜头比个心，只换动作",
                reference=recent_reference,
                scene_snapshot="",
            )
            continuity = (
                "Recent-photo continuity: this reference is the last image actually sent in the same conversation. "
                "Unless the current request explicitly changes them, preserve identity, face, hairstyle, exact outfit and accessories, "
                "room or location, lighting, and time of day. Change only the requested action, pose, expression, gaze, camera angle, or framing. "
                "Any explicit new clothing, person, place, time, or scene request still has priority."
            )
            long_scene = (
                "当前日程：" + "很长但仍属于背景的日程描述" * 36
                + "；当前位置：海边；当前场景：外出；天气背景：傍晚晴朗"
            )

            _, sections = harness._build_final_photo_prompt(
                base_prompt="Positive prompt: 给镜头比个心，只换动作. Negative prompt: watermark.",
                workflow_kind="selfie",
                scene_hint=long_scene,
                reference=recent_reference,
                wardrobe=wardrobe,
                preset_section="Scene preset: casual character selfie",
                continuity_section=continuity,
            )

            self.assertIn("当前位置：海边", sections["scene_style_preset"])
            self.assertIn("当前场景：外出", sections["scene_style_preset"])
            self.assertIn("complete outfit", sections["reference_wardrobe"])
            self.assertIn("unless the current request changes them", sections["reference_wardrobe"])
            self.assertIn("Change only the requested action", sections["composition_continuity"])
            self.assertIn("Use the schedule only for missing", sections["reference_wardrobe"])
            self.assertNotIn("schedule context controls only location", sections["reference_wardrobe"])

    async def test_daily_outfit_aliases_are_removed_by_the_full_generation_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference_path = self._write_reference(root)
            reference = {
                "id": "library_sleepwear",
                "path": str(reference_path),
                "source": str(reference_path),
                "kind": "library",
                "note": "奶油色睡衣，卧室、睡前和居家休息时使用",
                "reference_roles": ["identity", "outfit"],
                "outfit_category": "sleepwear",
                "outfit_lock_default": True,
                "scene_categories": ["home", "bedroom"],
                "preferred_preset": "居家睡衣",
            }
            for label in ("今日穿搭", "当天穿搭", "日常穿搭", "today's outfit", "daily outfit"):
                with self.subTest(label=label):
                    harness = _PhotoReliabilityHarness(root, reference)
                    harness._photo_generation_selfie_schedule_scene_hint = (
                        lambda label=label: f"{label}：white shirt and navy blazer；当前位置：卧室"
                    )

                    backend, image_path, note = await harness._generate_photo_image(
                        workflow_kind="selfie",
                        prompt_text="坐在床边拍一张自然自拍",
                        session_key=f"alias:{label}",
                    )

                    self.assertEqual(
                        (backend, image_path, note),
                        ("在线图片 API", str(harness.generated_path), "backend completed"),
                    )
                    record = harness.data["recent_photo_generations"][0]
                    final_prompt = harness.external_calls[0]["prompt_text"]
                    self.assertNotIn("white shirt", final_prompt)
                    self.assertNotIn("navy blazer", final_prompt)
                    self.assertIn("当前位置：卧室", final_prompt)
                    self.assertEqual(
                        harness.external_calls[0]["reference_image_path"],
                        str(reference_path),
                    )
                    self.assertFalse(record["reference_removed"])
                    self.assertIn(
                        "daily_outfit_context_removed",
                        record["removed_conflicts"],
                    )

    def test_prompt_debug_json_is_utf8_hashed_and_retains_only_latest_forty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            harness = _PhotoReliabilityHarness(root)
            wardrobe = PhotoWardrobeDecision(
                mode="reference_outfit",
                source="selected_reference",
                category="sleepwear",
                lock_outfit=True,
                remove_daily_outfit_context=True,
                preset_name="居家睡衣",
            )
            latest_path = ""
            latest_hash = ""
            latest_prompt = ""
            for index in range(45):
                latest_prompt = f"完整中文提示词第{index}条：奶油色睡衣，窗边暖光。"
                latest_path, latest_hash = harness._write_photo_prompt_debug_file(
                    trace_id=f"trace-{index:02d}",
                    session_key="default:FriendMessage:测试用户",
                    workflow_kind="selfie",
                    base_prompt="中文基础提示词",
                    scene_context_before="今日穿搭：白衬衫；当前位置：家里",
                    scene_context_after="当前位置：家里",
                    reference={
                        "id": "睡衣参考图",
                        "kind": "library",
                        "path": "C:/参考图/睡衣.png",
                        "reference_roles": ["identity", "outfit"],
                        "outfit_category": "sleepwear",
                        "outfit_lock_default": True,
                        "preferred_preset": "居家睡衣",
                    },
                    wardrobe=wardrobe,
                    presets=["居家睡衣"],
                    prompt_sections_before={},
                    prompt_sections={"user_request": "自然自拍", "negative": "不要西装"},
                    prompt_sections_after={},
                    final_prompt=latest_prompt,
                    conflicts=[],
                    removed_conflicts=["daily_outfit_context"],
                    residual_conflicts=[],
                    detected_conflict_details=[],
                    removed_conflict_details=[],
                    residual_conflict_details=[],
                    reference_removed=None,
                    sanitizer_version=1,
                )

            debug_files = list((root / "photo_prompt_debug").glob("*.json"))
            self.assertEqual(len(debug_files), 40)
            self.assertTrue(Path(latest_path).is_file())
            raw = Path(latest_path).read_bytes()
            self.assertIn("完整中文提示词".encode("utf-8"), raw)
            payload = json.loads(raw.decode("utf-8"))
            expected_hash = hashlib.sha256(latest_prompt.encode("utf-8")).hexdigest()
            self.assertEqual(latest_hash, expected_hash)
            self.assertEqual(payload["final_prompt_sha256"], expected_hash)
            self.assertEqual(payload["final_prompt_length"], len(latest_prompt))
            self.assertEqual(payload["final_prompt"], latest_prompt)
            self.assertEqual(payload["wardrobe_decision"]["category"], "sleepwear")
            for path in debug_files:
                json.loads(path.read_text(encoding="utf-8"))

    def test_prompt_debug_file_is_not_written_when_trace_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            harness = _PhotoReliabilityHarness(root)
            harness.photo_generation_trace_max_size_kb = 0
            path, prompt_hash = harness._write_photo_prompt_debug_file(
                trace_id="trace-disabled",
                session_key="default:FriendMessage:test-user",
                workflow_kind="selfie",
                base_prompt="base prompt",
                scene_context_before="before",
                scene_context_after="after",
                reference=None,
                wardrobe=PhotoWardrobeDecision(),
                presets=[],
                prompt_sections_before={},
                prompt_sections={},
                prompt_sections_after={},
                final_prompt="private full prompt",
                conflicts=[],
                removed_conflicts=[],
                residual_conflicts=[],
                detected_conflict_details=[],
                removed_conflict_details=[],
                residual_conflict_details=[],
                reference_removed=None,
                sanitizer_version=2,
            )

            self.assertEqual(path, "")
            self.assertEqual(
                prompt_hash,
                hashlib.sha256("private full prompt".encode("utf-8")).hexdigest(),
            )
            self.assertFalse((root / "photo_prompt_debug").exists())

    async def test_structured_result_marks_automatic_reference_used_and_keeps_legacy_tuple(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference_path = self._write_reference(root)
            harness = _PhotoReliabilityHarness(
                root,
                {
                    "id": "automatic-reference",
                    "path": str(reference_path),
                    "kind": "library",
                    "reference_roles": ["identity", "outfit"],
                    "outfit_category": "sleepwear",
                    "outfit_lock_default": True,
                    "preferred_preset": "居家睡衣",
                },
            )
            kwargs = {
                "workflow_kind": "selfie",
                "prompt_text": "在卧室拍一张自然自拍",
                "session_key": "default:FriendMessage:10001",
            }

            result = await harness._generate_photo_image_result(**kwargs)

            self.assertIsInstance(result, PhotoGenerationResult)
            self.assertTrue(result.success)
            self.assertEqual(result.reference_selected_path, str(reference_path))
            self.assertTrue(result.reference_used)
            self.assertEqual(result.reference_id, "automatic-reference")
            self.assertEqual(result.wardrobe_mode, "reference_outfit")
            self.assertEqual(result.wardrobe_category, "sleepwear")
            self.assertEqual(result.preset_names, ("居家睡衣",))
            self.assertEqual(
                result.as_legacy_tuple(),
                ("在线图片 API", str(harness.generated_path), "backend completed"),
            )

            legacy = await harness._generate_photo_image(**kwargs)
            self.assertIsInstance(legacy, tuple)
            self.assertEqual(len(legacy), 3)
            self.assertEqual(legacy, result.as_legacy_tuple())


if __name__ == "__main__":
    unittest.main()
