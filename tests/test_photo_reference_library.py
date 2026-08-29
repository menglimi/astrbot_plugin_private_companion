# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from astrbot_plugin_private_companion.command_handlers import CommandHandlersMixin
from astrbot_plugin_private_companion.helpers import _today_key
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi
from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin
from astrbot_plugin_private_companion.photo_wardrobe_decision import (
    analyze_photo_wardrobe,
    resolve_photo_wardrobe_decision,
)
from astrbot_plugin_private_companion.scene_context import SceneContextMixin


class _ReferenceLibraryHarness(ProactiveMessageMixin):
    def __init__(self, root: Path) -> None:
        self.data_dir = str(root)
        self.enable_photo_reference_image = True
        self.photo_persona_reference_image_path = ""
        self.photo_reference_library: list[str] = []
        self.data: dict = {}
        self.photo_prompt_provider_id = ""
        self.fast_response_provider_id = ""
        self.llm_provider_id = ""
        self.mai_style_provider_id = ""

    def _task_provider(self, *_args) -> str:
        return ""


class _SceneReferenceLibraryHarness(SceneContextMixin, _ReferenceLibraryHarness):
    pass


class _ModelReferenceLibraryHarness(_ReferenceLibraryHarness):
    def __init__(self, root: Path, response: str) -> None:
        super().__init__(root)
        self.model_response = response
        self.model_calls: list[dict] = []

    async def _llm_call(self, prompt: str, **kwargs) -> str:
        self.model_calls.append({"prompt": prompt, **kwargs})
        return self.model_response


class _CommandReferenceHarness(CommandHandlersMixin):
    def __init__(self, root: Path, sources: list[str]) -> None:
        self.data_dir = str(root)
        self.sources = sources

    async def _photo_reference_sources_from_current_event(self, _event, _user_id: str) -> list[str]:
        return list(self.sources)

    def _photo_reference_sources_from_reply_cache(self, _event) -> list[str]:
        return []

    async def _photo_reference_sources_from_reply_event(self, _event) -> list[str]:
        return []


class PhotoReferenceLibraryTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_library_path_preserves_double_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "persona  original.png"
            reference.write_bytes(b"image")
            harness = _ReferenceLibraryHarness(root)
            harness.photo_reference_library = [
                {"path": str(reference), "note": "基础身份图", "reference_roles": ["identity"]}
            ]

            candidates = await harness._photo_reference_candidates_async()

            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]["path"], str(reference.resolve()))
            self.assertIn("persona  original.png", candidates[0]["path"])

    async def test_same_attachment_from_persisted_and_raw_sources_is_added_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            persisted = root / "persisted.png"
            raw = root / "raw.png"
            persisted.write_bytes(b"same-image-content")
            raw.write_bytes(b"same-image-content")
            harness = _CommandReferenceHarness(root, [str(persisted), str(raw)])

            images, saw_image = await harness._photo_reference_images_from_command_context(
                object(),
                "10001",
                limit=12,
            )

            self.assertTrue(saw_image)
            self.assertEqual(len(images), 1)
            stored = list((root / "photo_reference_images").glob("*"))
            self.assertEqual(len(stored), 1)

    def test_edit_intent_keeps_edit_semantics_but_uses_image_input_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            harness = _ReferenceLibraryHarness(Path(temp_dir))
            harness.comfyui_selfie_workflow_name = "img2img-workflow"
            harness.comfyui_text2img_workflow_name = "txt2img-workflow"

            self.assertEqual(CommandHandlersMixin._photo_generation_workflow_kind("edit"), "edit")
            self.assertEqual(harness._choose_photo_workflow_name("edit"), "img2img-workflow")
            decision = resolve_photo_wardrobe_decision(
                workflow_kind="edit",
                prompt_text="背景换成蓝色",
                reference=None,
                available_presets=harness._photo_generation_scene_presets().keys(),
            )
            self.assertEqual(decision.selected_presets, ())

    def test_page_save_preserves_spaces_and_annotations(self) -> None:
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api.plugin = object()
        api._schema_key_index_cache = None
        value = "C:\\Photo Library\\home look.png || 居家服，在家时使用\nhttps://example.com/formal.webp || 礼服，正式场合"

        normalized = api._normalize_setting_value("photo_reference_library", value)

        self.assertEqual(
            normalized,
            [
                {
                    "path": "C:\\Photo Library\\home look.png",
                    "note": "居家服，在家时使用",
                },
                {
                    "path": "https://example.com/formal.webp",
                    "note": "礼服，正式场合",
                },
            ],
        )
        self.assertIn("photo_reference_library", api._allowed_setting_keys())

    def test_page_metadata_is_kept_structured_for_remote_cache_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
            api.plugin = object()
            api._schema_key_index_cache = None
            normalized = api._normalize_setting_value(
                "photo_reference_library",
                (
                    "https://example.com/sleepwear.png || 睡衣参考 || "
                    '{"outfit_category":"sleepwear","outfit_lock_default":false,'
                    '"scene_categories":["home"],"preferred_preset":"居家睡衣"}'
                ),
            )
            harness = _ReferenceLibraryHarness(Path(temp_dir))
            harness.photo_reference_library = normalized

            entry = harness._photo_reference_library_entries()[0]
            persisted = harness._photo_reference_config_value(entry, str(Path(temp_dir) / "cached.png"))

            self.assertEqual(entry["_config_format"], "dict")
            self.assertIsInstance(persisted, dict)
            self.assertEqual(persisted["outfit_category"], "sleepwear")
            self.assertIs(persisted["outfit_lock_default"], False)
            self.assertEqual(persisted["scene_categories"], ["home"])
            self.assertEqual(persisted["preferred_preset"], "居家睡衣")

    async def test_home_scene_prefers_annotated_home_reference_over_daily_outfit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home.png"
            outdoor = root / "outdoor.png"
            daily = root / "daily.png"
            for path in (home, outdoor, daily):
                path.write_bytes(b"image")
            harness = _ReferenceLibraryHarness(root)
            harness.photo_reference_library = [
                f"{outdoor} || 外出服，通勤、上学、逛街时使用",
                f"{home} || 居家服，在家、卧室、睡前或刚起床时使用",
            ]
            harness.data["daily_outfit_photo"] = {
                "date": _today_key(),
                "path": str(daily),
            }

            selected = await harness._photo_persona_reference_image_for_kind_async(
                "selfie",
                selection_context="在卧室里刚起床，穿着舒服的居家服拍一张自然自拍",
            )

            self.assertEqual(selected, str(home.resolve()))

    async def test_outdoor_scene_can_choose_daily_outfit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home.png"
            daily = root / "daily.png"
            home.write_bytes(b"image")
            daily.write_bytes(b"image")
            harness = _ReferenceLibraryHarness(root)
            harness.photo_reference_library = [
                f"{home} || 居家服，在家、卧室、睡前使用",
            ]
            harness.data["daily_outfit_photo"] = {
                "date": _today_key(),
                "path": str(daily),
            }

            selected = await harness._photo_persona_reference_image_for_kind_async(
                "portrait",
                selection_context="展示今天上学时的外出穿搭，在校门口拍一张全身照",
            )

            self.assertEqual(selected, str(daily.resolve()))

    async def test_model_selection_overrides_rule_fallback_and_records_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home.png"
            persona = root / "persona.png"
            home.write_bytes(b"image")
            persona.write_bytes(b"image")
            harness = _ModelReferenceLibraryHarness(root, "候选1")
            harness.photo_reference_library = [
                f"{home} || 居家服，在家、卧室或睡前使用",
            ]
            harness.photo_persona_reference_image_path = str(persona)

            with patch("astrbot_plugin_private_companion.proactive_message.logger.info") as info:
                selected = await harness._photo_persona_reference_image_for_kind_async(
                    "selfie",
                    selection_context="坐在电脑桌前看向镜头",
                )

            self.assertEqual(selected, str(home.resolve()))
            self.assertEqual(len(harness.model_calls), 1)
            self.assertEqual(harness.model_calls[0]["task"], "photo_reference_selection")
            self.assertIn("优先选择用途更具体且与当前场景兼容", harness.model_calls[0]["prompt"])
            self.assertIn("不要仅凭疲惫、揉眼睛、电脑桌", harness.model_calls[0]["prompt"])
            final_log = next(
                call for call in info.call_args_list
                if call.args and "参考图库已选图" in str(call.args[0])
            )
            self.assertEqual(final_log.args[1], "model")
            self.assertEqual(final_log.args[2], "valid_candidate_number")

    async def test_invalid_model_selection_uses_auditable_rule_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home.png"
            persona = root / "persona.png"
            home.write_bytes(b"image")
            persona.write_bytes(b"image")
            harness = _ModelReferenceLibraryHarness(root, "无法判断")
            harness.photo_reference_library = [
                f"{home} || 居家服，在家、卧室或睡前使用",
            ]
            harness.photo_persona_reference_image_path = str(persona)

            with patch("astrbot_plugin_private_companion.proactive_message.logger.info") as info:
                selected = await harness._photo_persona_reference_image_for_kind_async(
                    "selfie",
                    selection_context="坐在电脑桌前看向镜头",
                )

            self.assertEqual(selected, str(persona.resolve()))
            final_log = next(
                call for call in info.call_args_list
                if call.args and "参考图库已选图" in str(call.args[0])
            )
            self.assertEqual(final_log.args[1], "rule_fallback")
            self.assertEqual(final_log.args[2], "model_invalid_response")

    async def test_home_location_category_reaches_reference_selection_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home.png"
            daily = root / "daily.png"
            home.write_bytes(b"image")
            daily.write_bytes(b"image")
            harness = _SceneReferenceLibraryHarness(root)
            harness.photo_reference_library = [
                f"{home} || 居家服，在家、卧室、睡前使用",
            ]
            harness.data["daily_state"] = {"location": "家里"}
            harness.data["daily_plan"] = {
                "items": [{"time": "15:00", "activity": "顺便看一些账号运营的内容"}],
            }
            harness.data["daily_outfit_photo"] = {
                "date": _today_key(),
                "path": str(daily),
            }

            scene_hint = harness._photo_generation_selfie_schedule_scene_hint()
            selected = await harness._photo_persona_reference_image_for_kind_async(
                "selfie",
                selection_context=scene_hint,
            )

            self.assertIn("当前场景：居家室内", scene_hint)
            self.assertEqual(selected, str(home.resolve()))

    def test_library_parser_preserves_path_and_annotation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            harness = _ReferenceLibraryHarness(Path(temp_dir))
            harness.photo_reference_library = [
                r"C:\Photo Library\home look.png || 居家服，在家时使用",
                "https://example.com/formal.webp ｜｜ 礼服，正式场合使用",
            ]

            entries = harness._photo_reference_library_entries()

            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0]["source"], r"C:\Photo Library\home look.png")
            self.assertEqual(entries[0]["note"], "居家服，在家时使用")
            self.assertEqual(entries[1]["note"], "礼服，正式场合使用")

    def test_sleepwear_prompt_uses_sleepwear_preset_before_generic_skirt_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            harness = _ReferenceLibraryHarness(Path(temp_dir))

            prompt = "white lace nightgown 睡裙，睡前在卧室拍一张自拍"
            decision = resolve_photo_wardrobe_decision(
                workflow_kind="selfie",
                prompt_text=prompt,
                intent=analyze_photo_wardrobe(prompt),
                reference=None,
                available_presets=harness._photo_generation_scene_presets().keys(),
            )

            self.assertEqual(decision.selected_presets, ("居家睡衣",))

    def test_selected_daily_outfit_reference_keeps_daily_continuity_as_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            daily = root / "daily.png"
            daily.write_bytes(b"image")
            harness = _ReferenceLibraryHarness(root)
            harness.data["daily_outfit_photo"] = {
                "date": _today_key(),
                "path": str(daily),
            }
            harness._photo_generation_selfie_schedule_scene_hint = lambda: "当前位置：校门口；今日穿搭：cream sweatshirt"

            reference = {
                "id": "daily_outfit",
                "path": str(daily),
                "kind": "daily_outfit",
                "reference_roles": ["identity", "outfit"],
                "outfit_category": "daily_outfit",
                "outfit_lock_default": True,
                "preferred_preset": "日常穿搭",
            }
            prompt = "Show today's school outfit in a natural portrait."
            decision = resolve_photo_wardrobe_decision(
                workflow_kind="selfie",
                prompt_text=prompt,
                intent=analyze_photo_wardrobe(prompt),
                reference=reference,
                scene_context=harness._photo_generation_selfie_schedule_scene_hint(),
                base_prompt=harness._apply_photo_generation_prompt_format(prompt),
                available_presets=harness._photo_generation_scene_presets().keys(),
            )

            self.assertEqual(decision.mode, "daily_outfit")
            self.assertEqual(decision.category, "daily_outfit")
            self.assertEqual(decision.selected_presets, ("日常穿搭",))
            self.assertIn("outfit", decision.effective_reference_roles)

    def test_explicit_bedroom_scene_overrides_stale_schedule_location(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            harness = _ReferenceLibraryHarness(Path(temp_dir))
            harness._photo_generation_selfie_schedule_scene_hint = (
                lambda: "当前位置：工作地点；当前场景：外出"
            )

            prompt = "宿舍床上自拍，穿宽松睡衣，靠在床头喝牛奶。"
            decision = resolve_photo_wardrobe_decision(
                workflow_kind="selfie",
                prompt_text=prompt,
                intent=analyze_photo_wardrobe(prompt),
                reference=None,
                scene_context=harness._photo_generation_selfie_schedule_scene_hint(),
                base_prompt=harness._apply_photo_generation_prompt_format(prompt),
                available_presets=harness._photo_generation_scene_presets().keys(),
            )

            self.assertNotIn("工作地点", decision.scene_context)
            self.assertNotIn("当前场景：外出", decision.scene_context)
            self.assertIn("ambient_location_context_removed", decision.adjustments)


if __name__ == "__main__":
    unittest.main()
