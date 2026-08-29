# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from astrbot_plugin_private_companion.creative import CreativeMixin


class _SecondAcquireBarrier:
    def __init__(self) -> None:
        self.acquire_count = 0
        self.second_acquire_started = asyncio.Event()
        self.release_second_acquire = asyncio.Event()

    async def __aenter__(self):
        self.acquire_count += 1
        if self.acquire_count == 2:
            self.second_acquire_started.set()
            await self.release_second_acquire.wait()
        return self

    async def __aexit__(self, *_args) -> bool:
        return False


class _CreativeCoverHarness(CreativeMixin):
    def __init__(self, root: str) -> None:
        self.data_dir = root
        self.data = {
            "creative_projects": [
                {
                    "id": "project-1",
                    "title": "玻璃杯里的小雨",
                    "work_type": "短篇小说",
                    "premise": "一个人在雨夜发现杯中的倒影连接着另一座城市。",
                    "tone": "安静、轻悬疑",
                    "source_text": "窗边的雨声",
                    "status": "drafting",
                    "draft_chunks": [
                        {
                            "text": "雨水沿着玻璃缓慢滑下，她在杯底看见一盏不属于这间屋子的灯。",
                            "chars": 35,
                        }
                    ],
                    "characters": [{"name": "岚", "description": "独居的年轻修复师"}],
                }
            ]
        }
        self._data_lock = asyncio.Lock()
        self.enable_creative_cover_generation = True
        self.enable_photo_text_action = True
        self.enable_photo_reference_image = False
        self.photo_persona_reference_image_path = ""
        self.photo_generation_prompt_format = "traditional"
        self.generate_calls: list[dict] = []
        self.save_count = 0
        self.fail_generation = False
        self.reference_path = ""

    def _save_data_sync(self, **_kwargs) -> None:
        self.save_count += 1

    @staticmethod
    def _photo_text_available() -> bool:
        return True

    async def _generate_photo_image(self, **kwargs):
        self.generate_calls.append(dict(kwargs))
        if self.fail_generation:
            return "测试后端", "", "测试生图失败"
        source = Path(self.data_dir) / "generated.png"
        source.write_bytes(b"creative-cover")
        return "测试后端", str(source), "ok"

    async def _photo_persona_reference_image_for_kind_async(self, *_args, **_kwargs) -> str:
        return self.reference_path


class CreativeCoverGenerationTests(unittest.IsolatedAsyncioTestCase):
    def test_cover_style_is_matched_from_work_type_and_tone(self) -> None:
        mystery = {"work_type": "短篇小说", "tone": "轻悬疑", "premise": "雨夜谜案"}
        poetry = {"work_type": "诗歌", "tone": "安静", "premise": "写给春天"}
        fantasy = {"work_type": "奇幻小说", "tone": "壮阔", "premise": "漂浮城市"}
        fallback = {"work_type": "作品", "tone": "", "premise": ""}

        self.assertEqual(CreativeMixin._creative_cover_style_instruction(mystery)[0], "悬疑黑色电影")
        self.assertEqual(CreativeMixin._creative_cover_style_instruction(poetry)[0], "诗意水彩")
        self.assertEqual(CreativeMixin._creative_cover_style_instruction(fantasy)[0], "幻想概念插画")
        self.assertEqual(CreativeMixin._creative_cover_style_instruction(fallback)[0], "文学编辑插画")

    async def test_disabled_cover_generation_has_no_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            harness = _CreativeCoverHarness(root)
            harness.enable_creative_cover_generation = False

            self.assertEqual(harness._creative_cover_candidate_id(), "")
            self.assertIsNone(await harness._maybe_generate_creative_cover("project-1"))
            self.assertEqual(harness.generate_calls, [])

    async def test_cover_is_generated_once_and_saved_to_dedicated_directory(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            harness = _CreativeCoverHarness(root)

            self.assertEqual(harness._creative_cover_candidate_id(), "project-1")
            result = await harness._maybe_generate_creative_cover("project-1")

            self.assertIsNotNone(result)
            project = harness.data["creative_projects"][0]
            cover_path = Path(project["cover_path"])
            self.assertTrue(cover_path.is_file())
            self.assertEqual(cover_path.parent.name, "creative_covers")
            self.assertEqual(project["cover_generation_status"], "ready")
            self.assertEqual(project["cover_generation_attempts"], 1)
            self.assertEqual(harness._creative_cover_candidate_id(), "")
            self.assertEqual(len(harness.generate_calls), 1)
            call = harness.generate_calls[0]
            self.assertEqual(call["workflow_kind"], "text2img")
            self.assertFalse(call["allow_daily_outfit_reference"])
            self.assertEqual(call["reference_image_path"], "")
            self.assertEqual(call["prompt_format"], "traditional")
            self.assertIn("readable text", call["prompt_text"])
            self.assertTrue(call["prompt_text"].startswith("Positive prompt:"))
            self.assertIn("Negative prompt:", call["prompt_text"])
            self.assertIn("second person", call["prompt_text"])
            self.assertEqual(project["cover_generation_person_policy"], "symbolic_no_people")
            self.assertEqual(project["cover_generation_style"], "悬疑黑色电影")

            await harness._maybe_generate_creative_cover("project-1")
            self.assertEqual(len(harness.generate_calls), 1)

    async def test_cover_storage_preserves_legal_internal_spaces_in_source_path(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            harness = _CreativeCoverHarness(root)
            source = Path(root) / "generated  cover.png"
            source.write_bytes(b"creative-cover")

            stored = await harness._store_creative_cover_image("project-1", str(source))

            self.assertTrue(Path(stored).is_file())
            self.assertEqual(Path(stored).read_bytes(), b"creative-cover")

    async def test_person_cover_uses_reference_and_forbids_other_characters(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            harness = _CreativeCoverHarness(root)
            reference = Path(root) / "persona.png"
            reference.write_bytes(b"persona-reference")
            harness.reference_path = str(reference)
            harness.enable_photo_reference_image = True
            harness.photo_persona_reference_image_path = str(reference)
            harness.photo_generation_prompt_format = "natural_language"

            project = await harness._maybe_generate_creative_cover("project-1")

            self.assertIsNotNone(project)
            call = harness.generate_calls[0]
            self.assertEqual(call["workflow_kind"], "portrait")
            self.assertEqual(call["reference_image_path"], str(reference))
            self.assertEqual(call["prompt_format"], "natural_language")
            self.assertIn("exactly one person", call["prompt_text"])
            self.assertIn("Do not show any second person", call["prompt_text"])
            self.assertIn("figure inside a mirror/window/portal/screen", call["prompt_text"])
            self.assertNotIn("Characters:", call["prompt_text"])
            self.assertEqual(project["cover_generation_reference_image"], str(reference))
            self.assertEqual(project["cover_generation_person_policy"], "single_reference_character")

    async def test_cover_reference_record_preserves_long_path_and_internal_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            harness = _CreativeCoverHarness(root)
            reference = "C:/reference/" + ("nested  folder/" * 36) + "persona  original.png"
            self.assertGreater(len(reference), 500)
            harness.reference_path = reference
            harness.enable_photo_reference_image = True
            harness.photo_persona_reference_image_path = reference

            project = await harness._maybe_generate_creative_cover("project-1")

            self.assertIsNotNone(project)
            self.assertEqual(project["cover_generation_reference_image"], reference)

    def test_cover_prompt_can_use_traditional_or_natural_language_format(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            harness = _CreativeCoverHarness(root)
            project = harness.data["creative_projects"][0]

            harness.photo_generation_prompt_format = "traditional"
            traditional = harness._creative_cover_prompt(project, person_reference_available=True)
            harness.photo_generation_prompt_format = "natural_language"
            natural = harness._creative_cover_prompt(project, person_reference_available=True)

            self.assertTrue(traditional.startswith("Positive prompt:"))
            self.assertIn("Negative prompt:", traditional)
            self.assertFalse(natural.startswith("Positive prompt:"))
            self.assertIn("Create a polished book cover illustration", natural)

    async def test_cover_keeps_prompt_format_snapshot_while_waiting_to_generate(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            harness = _CreativeCoverHarness(root)
            barrier = _SecondAcquireBarrier()
            harness._data_lock = barrier
            harness.photo_generation_prompt_format = "traditional"
            generation = asyncio.create_task(
                harness._maybe_generate_creative_cover("project-1")
            )

            try:
                await asyncio.wait_for(barrier.second_acquire_started.wait(), timeout=3)
                harness.photo_generation_prompt_format = "natural_language"
                barrier.release_second_acquire.set()
                await asyncio.wait_for(generation, timeout=5)
            finally:
                barrier.release_second_acquire.set()
                if not generation.done():
                    generation.cancel()
                await asyncio.gather(generation, return_exceptions=True)

            call = harness.generate_calls[0]
            self.assertEqual(harness.photo_generation_prompt_format, "natural_language")
            self.assertEqual(call["prompt_format"], "traditional")
            self.assertTrue(call["prompt_text"].startswith("Positive prompt:"))

    async def test_legacy_generated_person_cover_is_upgraded_once_when_reference_exists(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            harness = _CreativeCoverHarness(root)
            reference = Path(root) / "persona.png"
            reference.write_bytes(b"persona-reference")
            legacy_cover = Path(root) / "legacy-cover.png"
            legacy_cover.write_bytes(b"legacy-cover")
            project = harness.data["creative_projects"][0]
            project["cover_path"] = str(legacy_cover)
            project["cover_generated_at"] = 123.0
            project["cover_generation_attempts"] = 1
            harness.reference_path = str(reference)
            harness.enable_photo_reference_image = True
            harness.photo_persona_reference_image_path = str(reference)

            self.assertEqual(harness._creative_cover_candidate_id(), "project-1")
            upgraded = await harness._maybe_generate_creative_cover("project-1")

            self.assertIsNotNone(upgraded)
            self.assertEqual(len(harness.generate_calls), 1)
            self.assertEqual(harness.generate_calls[0]["reference_image_path"], str(reference))
            self.assertEqual(upgraded["cover_generation_person_policy"], "single_reference_character")
            self.assertEqual(harness._creative_cover_candidate_id(), "")

    async def test_failed_cover_generation_enters_retry_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            harness = _CreativeCoverHarness(root)
            harness.fail_generation = True

            project = await harness._maybe_generate_creative_cover("project-1")

            self.assertIsNotNone(project)
            self.assertEqual(project["cover_generation_status"], "failed")
            self.assertEqual(project["cover_generation_attempts"], 1)
            self.assertEqual(project["cover_generation_error"], "测试生图失败")
            self.assertGreater(project["cover_generation_next_retry_at"], 0)
            self.assertEqual(harness._creative_cover_candidate_id(), "")


if __name__ == "__main__":
    unittest.main()
