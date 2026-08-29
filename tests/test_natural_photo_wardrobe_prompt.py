# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from astrbot_plugin_private_companion.command_handlers import CommandHandlersMixin


class _NaturalPhotoPromptHarness(CommandHandlersMixin):
    natural_language_photo_extra_prompt = ""


class _AnimeNaturalPhotoPromptHarness(_NaturalPhotoPromptHarness):
    def _get_photo_style_instruction(self):
        return "二次元", "日系二次元插画风"

    def _photo_style_prompt_en(self, _style_name: str, _style_instruction: str = "") -> str:
        return "2D anime illustration style, clean detailed character art, cel-shaded rendering, soft colors"


class NaturalPhotoWardrobePromptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _NaturalPhotoPromptHarness()

    def _selfie_prompt(self, request: str, *, has_reference: bool = True) -> str:
        return self.harness._build_natural_language_photo_prompt(
            prompt=request,
            kind="selfie",
            has_reference=has_reference,
        )

    def test_base_prompt_preserves_explicit_wardrobe_request_without_stale_outfit_boilerplate(self) -> None:
        requests = (
            "在卧室穿睡衣拍一张自拍",
            "换成魔法少女 COS 服拍照",
            "穿校服在教室门口自拍",
            "换上红色晚礼服去宴会厅拍照",
            "在海边穿泳装自拍",
            "健身结束后穿运动服拍一张",
            "在家换成宽松居家服自拍",
        )

        for request in requests:
            with self.subTest(request=request):
                prompt = self._selfie_prompt(request)
                self.assertIn(f"user request: {request}", prompt)
                self.assertIn("preserve character identity and stable appearance", prompt)
                self.assertNotIn("keep today's outfit and character appearance", prompt)

    def test_english_wardrobe_requests_remain_verbatim_for_structured_resolution(self) -> None:
        requests = (
            "take a bedtime selfie in pajamas",
            "cosplay as a shrine maiden",
            "wear a school uniform for the classroom photo",
            "wear an evening gown at the reception",
            "take a beach selfie in swimwear",
            "wear sportswear after the workout",
            "take a relaxed portrait in homewear",
        )

        for request in requests:
            with self.subTest(request=request):
                prompt = self._selfie_prompt(request)
                self.assertIn(f"user request: {request}", prompt)
                self.assertIn("preserve character identity and stable appearance", prompt)
                self.assertNotIn("keep today's outfit and character appearance", prompt)

    def test_ordinary_selfie_keeps_identity_continuity_without_forcing_an_outfit(self) -> None:
        with_reference = self._selfie_prompt("在窗边比个心拍一张自拍")
        without_reference = self._selfie_prompt("在窗边比个心拍一张自拍", has_reference=False)

        self.assertIn("preserve character identity and stable appearance from the selected reference image", with_reference)
        self.assertIn("preserve character identity and stable appearance from available visual continuity", without_reference)
        self.assertNotIn("today's outfit", with_reference)
        self.assertNotIn("explicit clothing or outfit request", with_reference)

    def test_current_wardrobe_decision_follows_stale_visual_memory(self) -> None:
        prompt = self.harness._build_natural_language_photo_prompt(
            prompt="回卧室换上睡衣拍一张自拍",
            kind="selfie",
            has_reference=True,
            memory_context="今日穿搭：蓝色校服外套；地点：学校",
        )

        self.assertIn("visual continuity reference", prompt)
        self.assertNotIn("keep today's outfit and character appearance", prompt)
        self.assertIn("preserve character identity and stable appearance", prompt)

    def test_structured_contract_is_fixed_and_visual_context_stays_bounded(self) -> None:
        sections = self.harness._build_natural_language_photo_prompt(
            prompt="在卧室穿睡衣和朋友拍一张合影",
            kind="selfie",
            has_reference=True,
            memory_context=(
                "发色：银白；眼睛：绿色；穿搭：奶油色睡衣；"
                "地点：卧室窗边；背景：暖色床头灯；表情：自然微笑；"
            )
            * 8,
            structured=True,
        )

        by_name = {section.name: section for section in sections}
        self.assertEqual(by_name["natural_language_contract"].source, "fixed_prompt")
        self.assertTrue(by_name["natural_language_contract"].protected)
        visual_chars = sum(
            len(section.positive) + len(section.negative)
            for section in sections
            if section.source not in {"user_request", "fixed_prompt"}
        )
        self.assertLessEqual(visual_chars, 500)
        self.assertLessEqual(
            len(by_name["natural_language_contract"].positive),
            1400,
        )
        self.assertLessEqual(
            len(by_name["natural_language_contract"].negative),
            760,
        )

    def test_cos_marker_does_not_match_an_unrelated_english_word(self) -> None:
        prompt = self._selfie_prompt("use cosine-shaped window light for a portrait")

        self.assertIn("preserve character identity and stable appearance", prompt)
        self.assertNotIn("explicit clothing or outfit request", prompt)

    def test_text2img_scene_does_not_invite_an_unrequested_character(self) -> None:
        harness = _AnimeNaturalPhotoPromptHarness()

        prompt = harness._build_natural_language_photo_prompt(
            prompt="傍晚阳台视角的粉紫色晚霞",
            kind="text2img",
            has_reference=False,
        )

        self.assertIn("detailed environment and object art", prompt)
        self.assertNotIn("clean detailed character art", prompt)
        self.assertIn("do not add any unrequested person", prompt)
        self.assertIn("back-view person", prompt)

    def test_text2img_keeps_an_explicit_person_subject(self) -> None:
        harness = _AnimeNaturalPhotoPromptHarness()

        prompt = harness._build_natural_language_photo_prompt(
            prompt="一个女孩站在阳台上背对镜头看晚霞",
            kind="text2img",
            has_reference=False,
        )

        self.assertIn("clean detailed character art", prompt)
        self.assertNotIn("do not add any unrequested person", prompt)
        self.assertNotIn("back-view person", prompt)

    def test_explicit_back_view_selfie_keeps_the_requested_pose(self) -> None:
        prompt = self._selfie_prompt("我站在阳台背对镜头看晚霞")
        positive, negative = prompt.split(". Negative prompt: ", 1)

        self.assertIn("the requested back view is intentional", positive)
        self.assertIn("recognizable hairstyle silhouette", positive)
        self.assertIn("preserve character identity and stable appearance from the selected reference image", positive)
        self.assertNotIn("back view", negative)
        self.assertNotIn("face hidden", negative)

    def test_group_photo_prompt_requires_reference_and_preserves_referenced_people(self) -> None:
        without_reference = self._selfie_prompt("我和朋友拍一张合影", has_reference=False)
        with_reference = self._selfie_prompt("我和朋友拍一张合影", has_reference=True)

        self.assertIn("single character selfie", without_reference)
        self.assertIn("other people", without_reference)
        self.assertIn("multi-person photo based only on the explicitly supplied source reference", with_reference)
        self.assertIn("preserve every referenced person's identity", with_reference)
        self.assertNotIn("single character selfie", with_reference)

    def test_negated_group_photo_phrase_does_not_trigger_group_contract(self) -> None:
        prompt = self._selfie_prompt("不要我和小林一起拍照，只要一张单人自拍", has_reference=True)

        self.assertIn("single character selfie", prompt)
        self.assertNotIn("multi-person photo", prompt)


if __name__ == "__main__":
    unittest.main()
