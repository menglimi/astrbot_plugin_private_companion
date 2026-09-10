# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from astrbot_plugin_private_companion.private_image import PrivateImageMixin


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "pages" / "companion-panel" / "app.js").read_text(encoding="utf-8")
MIRROR_APP_JS = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
SCHEMA = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))


class _ReviewProvider:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls = 0
        self.requests: list[dict[str, object]] = []

    async def text_chat(self, **kwargs):
        self.calls += 1
        self.requests.append(dict(kwargs))
        return SimpleNamespace(completion_text=json.dumps(self.payload, ensure_ascii=False))


class _ReviewEvent:
    unified_msg_origin = "default:GroupMessage:10001"

    @staticmethod
    def get_sender_id():
        return "10001"


class _ReviewHarness(PrivateImageMixin):
    group_nsfw_image_review_mode = "single"
    group_nsfw_image_review_sensitivity = "balanced"
    group_nsfw_image_review_min_confidence = 0.7
    group_nsfw_image_review_timeout_seconds = 8.0
    group_nsfw_image_review_max_dimension = 0
    group_nsfw_image_review_custom_prompt = ""
    plugin_vision_provider_id = "primary"

    def __init__(self, providers: dict[str, _ReviewProvider], temp_dir: str) -> None:
        self.providers = providers
        self.data_dir = temp_dir
        self.task_prompt_extra = ""

    def _apply_task_prompt_override_for_call(
        self,
        task,
        prompt,
        system_prompt=None,
        *,
        flatten_system_prompt=False,
    ):
        if not self.task_prompt_extra:
            return prompt, system_prompt
        self.applied_task = task
        return f"{prompt}\n\n{self.task_prompt_extra}", None

    async def _prepare_private_image_sources_for_model(self, image_sources, *, namespace="vision"):
        return list(image_sources)

    @staticmethod
    def _private_image_model_image_items(_sources):
        return [("image-key", "data:image/png;base64,AA==")]

    def _private_image_visual_provider_candidates(self, _umo=""):
        return [(provider_id, "plugin_vision", "") for provider_id in self.providers]

    def _private_image_provider_by_id(self, provider_id):
        return self.providers.get(provider_id)

    @staticmethod
    def _provider_supports_image(_provider):
        return True

    @staticmethod
    def _private_image_provider_in_failure_cooldown(_provider_id, _provider_source):
        return False

    @staticmethod
    def _can_run_llm_task(_provider_id, *, task):
        return task == "group_nsfw_image_review"

    @staticmethod
    def _extract_json_payload(raw_text):
        return json.loads(raw_text)

    @staticmethod
    def _record_llm_usage(**_kwargs):
        return None

    @staticmethod
    def _clear_private_image_provider_failure(_provider_id, _provider_source):
        return None

    @staticmethod
    def _note_private_image_visual_provider_success(*_args, **_kwargs):
        return None

    @staticmethod
    def _mark_private_image_provider_failure(*_args, **_kwargs):
        return None


class _BlockedDeliveryHarness(PrivateImageMixin):
    enable_group_nsfw_private_fallback = True
    group_nsfw_image_review_failure_action = "block"

    @staticmethod
    def _build_outbound_chain(caption, image_path, **_kwargs):
        return [(caption, image_path)]

    @staticmethod
    def _extract_group_id_from_event(_event):
        return "20001"

    @staticmethod
    async def _review_group_generated_image_for_delivery(_event, _image_path):
        return {"label": "unavailable", "reason": "没有可用视觉模型"}


class GroupImageReviewConfigTests(unittest.IsolatedAsyncioTestCase):
    def test_unsafe_text_cannot_be_misread_as_safe(self) -> None:
        self.assertEqual(
            "adult_nsfw",
            _ReviewHarness._normalize_group_generated_image_review_label("not safe / unsafe"),
        )

    async def test_low_confidence_result_tries_the_next_visual_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = os.path.join(temp_dir, "generated.png")
            Path(image_path).write_bytes(b"image")
            providers = {
                "primary": _ReviewProvider({"label": "safe", "confidence": 0.4}),
                "fallback": _ReviewProvider({"label": "safe", "confidence": 0.92}),
            }
            result = await _ReviewHarness(providers, temp_dir)._review_group_generated_image_for_delivery(
                _ReviewEvent(), image_path
            )

        self.assertEqual("safe", result["label"])
        self.assertEqual("fallback", result["provider_id"])
        self.assertEqual(1, providers["primary"].calls)
        self.assertEqual(1, providers["fallback"].calls)

    async def test_group_review_task_prompt_override_reaches_visual_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = os.path.join(temp_dir, "generated.png")
            Path(image_path).write_bytes(b"image")
            providers = {"primary": _ReviewProvider({"label": "safe", "confidence": 0.95})}
            harness = _ReviewHarness(providers, temp_dir)
            harness.task_prompt_extra = "审核附加约束：保守判定"

            result = await harness._review_group_generated_image_for_delivery(
                _ReviewEvent(), image_path
            )

        self.assertEqual("safe", result["label"])
        self.assertEqual("group_nsfw_image_review", harness.applied_task)
        self.assertIn(harness.task_prompt_extra, providers["primary"].requests[0]["prompt"])

    async def test_dual_review_uses_two_distinct_models_and_keeps_stricter_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = os.path.join(temp_dir, "generated.png")
            Path(image_path).write_bytes(b"image")
            providers = {
                "primary": _ReviewProvider({"label": "safe", "confidence": 0.95}),
                "fallback": _ReviewProvider({"label": "adult_nsfw", "confidence": 0.88}),
            }
            harness = _ReviewHarness(providers, temp_dir)
            harness.group_nsfw_image_review_mode = "dual"
            result = await harness._review_group_generated_image_for_delivery(_ReviewEvent(), image_path)

        self.assertEqual("adult_nsfw", result["label"])
        self.assertEqual("primary,fallback", result["provider_id"])
        self.assertEqual(2, len(result["reviews"]))

    async def test_dual_review_can_stop_on_a_decisive_unsafe_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = os.path.join(temp_dir, "generated.png")
            Path(image_path).write_bytes(b"image")
            providers = {
                "primary": _ReviewProvider({"label": "disallowed", "confidence": 0.93}),
                "fallback": _ReviewProvider({"label": "safe", "confidence": 0.99}),
            }
            harness = _ReviewHarness(providers, temp_dir)
            harness.group_nsfw_image_review_mode = "dual"
            result = await harness._review_group_generated_image_for_delivery(_ReviewEvent(), image_path)

        self.assertEqual("disallowed", result["label"])
        self.assertEqual(1, providers["primary"].calls)
        self.assertEqual(0, providers["fallback"].calls)

    def test_strict_prompt_and_custom_rules_are_effective(self) -> None:
        harness = _ReviewHarness({}, "")
        harness.group_nsfw_image_review_sensitivity = "strict"
        harness.group_nsfw_image_review_custom_prompt = "明显血腥内容不得发群"

        prompt = harness._group_generated_image_review_prompt()

        self.assertIn("严格标准", prompt)
        self.assertIn("明显血腥内容不得发群", prompt)
        self.assertIn("不能把非法内容判为 safe", prompt)

    async def test_failure_action_can_block_instead_of_sending_privately(self) -> None:
        result = await _BlockedDeliveryHarness()._deliver_generated_image_to_event(
            _ReviewEvent(),
            image_path="generated.png",
            caption="给你看",
        )

        self.assertFalse(result["sent"])
        self.assertEqual("blocked", result["destination"])
        self.assertEqual("unavailable", result["review_label"])

    def test_large_review_image_uses_a_smaller_temporary_copy(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "large.png"
            Image.new("RGB", (1200, 600), "white").save(source)
            harness = _ReviewHarness({}, temp_dir)
            harness.group_nsfw_image_review_max_dimension = 256

            prepared = harness._prepare_group_generated_image_review_sources([str(source)])

            self.assertNotEqual(str(source), prepared[0])
            with Image.open(prepared[0]) as resized:
                self.assertEqual((256, 128), resized.size)
            with Image.open(source) as original:
                self.assertEqual((1200, 600), original.size)

    def test_schema_and_both_panel_bundles_expose_all_review_settings(self) -> None:
        keys = (
            "group_nsfw_image_review_mode",
            "group_nsfw_image_review_sensitivity",
            "group_nsfw_image_review_min_confidence",
            "group_nsfw_image_review_timeout_seconds",
            "group_nsfw_image_review_max_dimension",
            "group_nsfw_image_review_failure_action",
            "group_nsfw_image_review_custom_prompt",
        )
        photo_items = SCHEMA["photo_action_config"]["items"]
        for key in keys:
            self.assertIn(key, photo_items)
            self.assertIn(key, APP_JS)
        self.assertEqual(APP_JS, MIRROR_APP_JS)


if __name__ == "__main__":
    unittest.main()
