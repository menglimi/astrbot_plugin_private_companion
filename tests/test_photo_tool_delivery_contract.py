# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace

from astrbot_plugin_private_companion.command_handlers import CommandHandlersMixin
from astrbot_plugin_private_companion.llm_tool_actions import (
    LlmToolActionsMixin,
    PHOTO_TOOL_SILENT_SENTINEL,
)
from astrbot_plugin_private_companion.proactive_message import PhotoGenerationResult
from astrbot_plugin_private_companion.private_image import PrivateImageMixin


class _FakeEvent:
    unified_msg_origin = "default:FriendMessage:10001"

    def __init__(self, reference_path: str = "", message_str: str = "") -> None:
        self.reference_path = reference_path
        self.message_str = message_str
        self.stopped = False

    def get_sender_id(self) -> str:
        return "10001"

    def stop_event(self) -> None:
        self.stopped = True


class _PhotoToolHarness(LlmToolActionsMixin):
    def __init__(self) -> None:
        self.enabled = True
        self.enable_photo_text_action = True
        self.natural_language_photo_generation_mode = "tool_first"
        self.context = SimpleNamespace(
            get_config=lambda: {"provider_settings": {"tool_call_timeout": 120}}
        )
        self.image_path = ""
        self.delivery = {
            "sent": True,
            "destination": "current",
            "message": "图片已发送",
        }
        self.workflow_kind = ""
        self.generation_kwargs: dict = {}
        self.generation_delay = 0.0
        self.memory_calls: list[dict] = []
        self.delivery_kwargs: dict = {}
        self.delivery_calls = 0
        self.private_delivery_kwargs: dict = {}

    def _photo_text_available(self) -> bool:
        return True

    async def _generate_photo_image(self, **kwargs):
        self.generation_kwargs = dict(kwargs)
        self.workflow_kind = str(kwargs.get("workflow_kind") or "")
        if self.generation_delay:
            await asyncio.sleep(self.generation_delay)
        return "test-backend", self.image_path, "ok"

    async def _deliver_generated_image_to_event(self, *_args, **kwargs):
        self.delivery_calls += 1
        self.delivery_kwargs = dict(kwargs)
        return dict(self.delivery)

    @staticmethod
    def _build_outbound_chain(text, image_path, **_kwargs):
        return [("plain", text), ("image", image_path)]

    async def _send_atrelay_chain_to_target(self, _event, **kwargs):
        self.private_delivery_kwargs = dict(kwargs)
        return True, "", "default:FriendMessage:10001"

    async def _memory_companion_record_photo_generation(self, _event, **kwargs):
        self.memory_calls.append(dict(kwargs))


class _StructuredPhotoToolHarness(_PhotoToolHarness):
    def __init__(
        self,
        *,
        reference_used: bool,
        note: str,
        reference_plan: tuple[dict, ...] = (),
        prompt_path: str = "",
        reference_fallback_message: str = "",
        generation_completed: bool = False,
        failure_stage: str = "",
    ) -> None:
        super().__init__()
        self.structured_reference_used = reference_used
        self.structured_note = note
        self.structured_reference_plan = reference_plan
        self.structured_prompt_path = prompt_path
        self.structured_reference_fallback_message = reference_fallback_message
        self.structured_generation_completed = generation_completed
        self.structured_failure_stage = failure_stage

    async def _generate_photo_image_result(self, **kwargs):
        self.workflow_kind = str(kwargs.get("workflow_kind") or "")
        return PhotoGenerationResult(
            backend="test-backend",
            image_path=self.image_path,
            note=self.structured_note,
            reference_selected_path=str(kwargs.get("reference_image_path") or ""),
            reference_used=self.structured_reference_used,
            reference_plan=self.structured_reference_plan,
            prompt_path=self.structured_prompt_path,
            reference_fallback_message=self.structured_reference_fallback_message,
            generation_completed=self.structured_generation_completed,
            failure_stage=self.structured_failure_stage,
        )


class _PhotoIntentHarness(LlmToolActionsMixin, CommandHandlersMixin):
    pass


class _AmbiguousSendEvent:
    unified_msg_origin = "default:GroupMessage:10001"

    def __init__(self) -> None:
        self.send_calls: list[object] = []

    async def send(self, result) -> None:
        self.send_calls.append(result)
        raise TimeoutError("waiting for platform acknowledgement timed out")

    @staticmethod
    def chain_result(chain):
        return {"fallback_chain": list(chain)}


class _DirectPhotoDeliveryHarness(PrivateImageMixin):
    enable_group_nsfw_private_fallback = False

    @staticmethod
    def _mark_private_companion_skip_reaction_expression(_event) -> None:
        return None

    @staticmethod
    def _sanitize_photo_tool_caption(value, *, limit=120) -> str:
        return str(value or "")[:limit]

    @staticmethod
    def _build_outbound_chain(text, image_path, **_kwargs):
        return [(text, image_path)]

    @staticmethod
    def _build_result_from_chain(chain):
        return {"chain": list(chain)}

    @staticmethod
    def _extract_group_id_from_event(_event) -> str:
        return ""


class _PromptAwarePhotoToolHarness(_PhotoToolHarness, CommandHandlersMixin):
    natural_language_photo_extra_prompt = ""

    def __init__(self) -> None:
        super().__init__()
        self._command_photo_quota_left = None

    def _get_photo_style_instruction(self):
        return "二次元", "日系二次元插画风"

    def _photo_style_prompt_en(self, _style_name: str, _style_instruction: str = "") -> str:
        return "2D anime illustration style, clean detailed character art, cel-shaded rendering, soft colors"

    @staticmethod
    async def _photo_reference_source_to_stable_path(source: str, **_kwargs) -> str:
        return source

    @staticmethod
    async def _photo_reference_image_from_command_context(event, _user_id: str):
        path = str(getattr(event, "reference_path", "") or "")
        return path, "随消息发送的图片" if path else "", bool(path)


class _RelationshipRolePhotoToolHarness(_PromptAwarePhotoToolHarness):
    def __init__(
        self,
        *,
        explicit_mention: bool = True,
        role_reference_path: str | None = None,
    ) -> None:
        super().__init__()
        self.enable_photo_reference_image = True
        self.explicit_mention = explicit_mention
        self.role_reference_path = role_reference_path

    def _photo_reference_role_asset_candidates(self, *, request_text: str):
        return [
            {
                "id": "sister-ref",
                "kind": "relation_role",
                "path": self.image_path if self.role_reference_path is None else self.role_reference_path,
                "role_name": "姐姐",
                "role_explicit_mention": self.explicit_mention,
                "group_photo_requested": True,
            }
        ]


class _CommandEntryPhotoHarness(_PromptAwarePhotoToolHarness):
    enable_natural_language_photo_generation = True
    natural_language_photo_generation_mode = "rule_fast"

    def __init__(self) -> None:
        super().__init__()
        del self._command_photo_quota_left
        self.natural_language_photo_generation_mode = "rule_fast"
        self._data_lock = asyncio.Lock()
        self.user = {"user_id": "10001", "enabled": True}
        self.replies: list[str] = []

    @staticmethod
    def _private_event_has_image_safe(event, **_kwargs) -> bool:
        return bool(getattr(event, "reference_path", ""))

    @staticmethod
    def _photo_reference_sources_from_reply_cache(_event) -> list[str]:
        return []

    @staticmethod
    async def _photo_reference_sources_from_reply_event(_event) -> list[str]:
        return []

    @staticmethod
    def _natural_language_photo_intent(_text: str, **_kwargs):
        return {"kind": "selfie", "prompt": "重新拍一张", "needs_prompt": False}

    def _get_user(self, _user_id: str):
        return self.user

    @staticmethod
    def _is_target_private_user(_user_id: str, _user) -> bool:
        return True

    @staticmethod
    def _private_user_role(_user, _user_id: str = "") -> str:
        return "primary"

    @staticmethod
    def _natural_language_photo_quota_left(_user) -> int:
        return 1

    @staticmethod
    def _command_photo_quota_left(_user) -> int:
        return 1

    @staticmethod
    def _photo_generation_workflow_kind(kind: str) -> str:
        return kind

    @staticmethod
    def _compose_photo_continuity_key(_session: str, _user_id: str) -> str:
        return "default:FriendMessage:10001|sender=10001"

    async def _reply(self, _event, text: str, **_kwargs) -> None:
        self.replies.append(text)

    @staticmethod
    async def _natural_language_photo_ack_reply_text(*_args, **_kwargs) -> str:
        return "等我一下。"

    @staticmethod
    async def _natural_language_photo_done_reply_text(*_args, **_kwargs) -> str:
        return "好了，你看。"

    @staticmethod
    def _note_natural_language_photo_generation_attempt(_user, *, image_path: str = "") -> None:
        return None

    @staticmethod
    def _note_command_photo_generation_attempt(_user, *, image_path: str = "") -> None:
        return None

    @staticmethod
    def _save_data_sync(**_kwargs) -> None:
        return None


class PhotoToolDeliveryContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        generated_dir = os.path.join(self.temp_dir.name, "generated_photos")
        os.makedirs(generated_dir, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(suffix=".png", dir=generated_dir, delete=False)
        handle.write(b"\x89PNG\r\n\x1a\n" + b"test-image")
        handle.close()
        self.image_path = handle.name

    def tearDown(self) -> None:
        if os.path.exists(self.image_path):
            os.unlink(self.image_path)
        self.temp_dir.cleanup()

    async def test_production_host_without_image_extension_fails_before_backend_checks(self) -> None:
        harness = _PhotoToolHarness()
        backend_checks: list[str] = []
        harness._image_companion_required = lambda: True
        harness._image_companion_available = lambda: False
        harness._photo_text_available = lambda: backend_checks.append("availability") or True

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="画一颗星星",
                kind="text2img",
            )
        )

        self.assertEqual("unavailable", payload["status"])
        self.assertEqual("image_extension_unavailable", payload["error_code"])
        self.assertFalse(payload["success"])
        self.assertFalse(payload["generated"])
        self.assertFalse(payload["sent"])
        self.assertEqual([], backend_checks)
        self.assertEqual({}, harness.generation_kwargs)
        self.assertEqual(0, harness.delivery_calls)

    async def test_ambiguous_send_error_does_not_retry_same_image(self) -> None:
        harness = _DirectPhotoDeliveryHarness()
        event = _AmbiguousSendEvent()

        delivery = await harness._deliver_generated_image_to_event(
            event,
            image_path=self.image_path,
            caption="给你看",
        )

        self.assertFalse(delivery["sent"])
        self.assertTrue(delivery["uncertain"])
        self.assertEqual("current", delivery["destination"])
        self.assertIn("acknowledgement", delivery["message"])
        self.assertIn("不再重试", delivery["message"])
        self.assertEqual(1, len(event.send_calls))

    async def test_current_media_tool_sends_fresh_image_from_allowed_root(self) -> None:
        harness = _PhotoToolHarness()
        harness.data_dir = self.temp_dir.name
        event = _FakeEvent()

        payload = json.loads(
            await harness._pc_send_current_media_impl(
                event,
                media_path=self.image_path,
                caption="刚生成的这张给你。",
            )
        )

        self.assertEqual(payload["status"], "success")
        self.assertTrue(payload["sent"])
        self.assertEqual(harness.delivery_calls, 1)
        self.assertEqual(harness.delivery_kwargs["image_path"], os.path.realpath(self.image_path))
        self.assertTrue(event._private_companion_photo_tool_sent)
        self.assertIn(PHOTO_TOOL_SILENT_SENTINEL, payload["final_response_instruction"])
        self.assertNotIn(self.image_path, json.dumps(payload, ensure_ascii=False))

    async def test_current_media_tool_sends_to_explicit_requester_private_chat(self) -> None:
        harness = _PhotoToolHarness()
        harness.data_dir = self.temp_dir.name
        event = _FakeEvent(message_str="私聊发我")
        event.unified_msg_origin = "default:GroupMessage:20001"

        payload = json.loads(
            await harness._pc_send_current_media_impl(
                event,
                media_path=self.image_path,
                caption="月亮染红好啦",
                destination="requester_private",
            )
        )

        self.assertEqual("success", payload["status"])
        self.assertTrue(payload["sent"])
        self.assertEqual("requester_private", payload["delivery"])
        self.assertEqual("private", harness.private_delivery_kwargs["message_type"])
        self.assertEqual("10001", harness.private_delivery_kwargs["target_id"])
        self.assertEqual(
            [("plain", "月亮染红好啦"), ("image", os.path.realpath(self.image_path))],
            harness.private_delivery_kwargs["chain"],
        )
        self.assertEqual(0, harness.delivery_calls)

    async def test_current_media_tool_rejects_unrequested_private_delivery(self) -> None:
        harness = _PhotoToolHarness()
        harness.data_dir = self.temp_dir.name

        payload = json.loads(
            await harness._pc_send_current_media_impl(
                _FakeEvent(message_str="发出来看看"),
                media_path=self.image_path,
                destination="requester_private",
            )
        )

        self.assertEqual("destination_not_confirmed", payload["status"])
        self.assertFalse(payload["sent"])
        self.assertEqual({}, harness.private_delivery_kwargs)

    def test_natural_media_followups_are_recognized(self) -> None:
        harness = _PhotoToolHarness()

        self.assertTrue(harness._referenced_media_edit_instruction_matches("帮我把这个月亮改成红的"))
        self.assertTrue(harness._current_media_delivery_instruction_matches("私聊发我"))
        self.assertTrue(harness._current_media_delivery_instruction_matches("把改好的图发出来"))
        self.assertTrue(
            harness._current_media_delivery_instruction_matches(
                "我重启了，你再试试发过来，不要生成新图，把你刚刚生成的没发出来的发给我就可以了"
            )
        )
        self.assertTrue(
            harness._current_media_delivery_instruction_matches(
                "不要重新画，把刚刚没发出来的那张发给我"
            )
        )
        self.assertFalse(harness._current_media_private_delivery_instruction_matches("发出来看看"))

    async def test_current_media_tool_does_not_repeat_confirmed_media(self) -> None:
        harness = _PhotoToolHarness()
        harness.data_dir = self.temp_dir.name
        event = _FakeEvent()
        image_component = type("Image", (), {})()
        event._private_companion_confirmed_send_chains = [[image_component]]

        payload = json.loads(
            await harness._pc_send_current_media_impl(
                event,
                media_path=self.image_path,
            )
        )

        self.assertEqual(payload["status"], "already_sent")
        self.assertTrue(payload["sent"])
        self.assertEqual(harness.delivery_calls, 0)
        self.assertTrue(event._private_companion_photo_tool_sent)

    async def test_current_media_tool_rejects_stale_image(self) -> None:
        harness = _PhotoToolHarness()
        harness.data_dir = self.temp_dir.name
        old = time.time() - 31 * 60
        os.utime(self.image_path, (old, old))

        payload = json.loads(
            await harness._pc_send_current_media_impl(
                _FakeEvent(),
                media_path=self.image_path,
            )
        )

        self.assertEqual(payload["status"], "invalid_media")
        self.assertFalse(payload["sent"])
        self.assertIn("不是本轮近期生成", payload["message"])
        self.assertEqual(harness.delivery_calls, 0)

    async def test_current_media_tool_rejects_path_outside_allowed_roots(self) -> None:
        harness = _PhotoToolHarness()
        with tempfile.TemporaryDirectory() as allowed_root:
            harness.data_dir = allowed_root
            payload = json.loads(
                await harness._pc_send_current_media_impl(
                    _FakeEvent(),
                    media_path=self.image_path,
                )
            )

        self.assertEqual(payload["status"], "invalid_media")
        self.assertFalse(payload["sent"])
        self.assertIn("不在允许", payload["message"])
        self.assertEqual(harness.delivery_calls, 0)

    async def test_current_media_tool_rejects_renamed_non_image(self) -> None:
        fake_path = os.path.join(self.temp_dir.name, "generated_photos", "fake.jpg")
        with open(fake_path, "wb") as handle:
            handle.write(b"not-an-image")
        harness = _PhotoToolHarness()
        harness.data_dir = self.temp_dir.name

        payload = json.loads(
            await harness._pc_send_current_media_impl(
                _FakeEvent(),
                media_path=fake_path,
            )
        )

        self.assertEqual(payload["status"], "invalid_media")
        self.assertFalse(payload["sent"])
        self.assertIn("实际图片格式", payload["message"])
        self.assertEqual(harness.delivery_calls, 0)

    async def test_current_media_tool_normalizes_valid_image_with_wrong_suffix(self) -> None:
        mismatched_path = os.path.join(
            self.temp_dir.name,
            "generated_photos",
            "generated-as-jpg.jpg",
        )
        with open(mismatched_path, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n" + b"generated-image")
        harness = _PhotoToolHarness()
        harness.data_dir = self.temp_dir.name

        payload = json.loads(
            await harness._pc_send_current_media_impl(
                _FakeEvent(),
                media_path=mismatched_path,
            )
        )

        delivered_path = harness.delivery_kwargs["image_path"]
        self.assertEqual("success", payload["status"])
        self.assertTrue(payload["sent"])
        self.assertTrue(delivered_path.endswith(".png"))
        self.assertNotEqual(os.path.realpath(mismatched_path), delivered_path)
        with open(delivered_path, "rb") as handle:
            self.assertTrue(handle.read(8).startswith(b"\x89PNG"))

    async def test_delivery_failure_is_not_top_level_success(self) -> None:
        harness = _PhotoToolHarness()
        harness.image_path = self.image_path
        harness.delivery = {
            "sent": False,
            "destination": "current",
            "message": "图片发送失败：平台拒绝",
        }

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="拍一张日常自拍",
                kind="selfie",
            )
        )

        self.assertEqual(payload["status"], "delivery_failed")
        self.assertFalse(payload["success"])
        self.assertTrue(payload["generated"])
        self.assertFalse(payload["sent"])
        self.assertTrue(payload["must_not_claim_sent"])
        self.assertEqual(payload["failure_stage"], "delivery")

    async def test_materialization_failure_reports_upstream_completion_without_false_send(self) -> None:
        harness = _StructuredPhotoToolHarness(
            reference_used=False,
            note="生成已完成但图片结果取回失败：下载在线图片结果超时",
            generation_completed=True,
            failure_stage="result_materialization",
        )

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="画一张测试图片",
                send=True,
            )
        )

        self.assertEqual(payload["status"], "result_retrieval_failed")
        self.assertFalse(payload["success"])
        self.assertFalse(payload["generated"])
        self.assertTrue(payload["generation_completed"])
        self.assertTrue(payload["upstream_generated"])
        self.assertFalse(payload["sent"])
        self.assertEqual(payload["failure_stage"], "result_materialization")
        self.assertFalse(payload["retryable"])
        self.assertFalse(payload["same_turn_retry_allowed"])
        self.assertIn("不要再次调用 pc_generate_photo", payload["final_response_instruction"])

    async def test_gateway_failure_is_unconfirmed_and_never_auto_retryable(self) -> None:
        harness = _StructuredPhotoToolHarness(
            reference_used=False,
            note=(
                "参考图接口失败：参考图接口端点 https://images.example/v1/images/edits "
                "HTTP 504：上游生图服务临时失败或网关中断。"
            ),
        )

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="把月亮改成红色",
                kind="selfie",
                send=True,
            )
        )

        self.assertEqual("submission_unconfirmed", payload["status"])
        self.assertEqual("upstream_response", payload["failure_stage"])
        self.assertFalse(payload["retryable"])
        self.assertFalse(payload["same_turn_retry_allowed"])
        self.assertTrue(payload["possible_upstream_execution"])
        self.assertIn("不要自动重试", payload["final_response_instruction"])

    async def test_provider_policy_refusal_is_not_exposed_to_the_reply_model(self) -> None:
        provider_error = (
            "The prompt could not be submitted. The prompt contains sensitive words "
            "that violate Google's Generative AI Prohibited Use Policy "
            "(https://policies.google.com/terms/generative-ai/use-policy). "
            "Try rephrasing the prompt."
        )
        harness = _StructuredPhotoToolHarness(
            reference_used=False,
            note=provider_error,
        )

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="拍一张测试图片",
                send=True,
            )
        )
        serialized = json.dumps(payload, ensure_ascii=False)

        self.assertEqual("error", payload["status"])
        self.assertEqual("provider_policy_refusal", payload["error_code"])
        self.assertFalse(payload["generated"])
        self.assertFalse(payload["sent"])
        self.assertTrue(payload["retryable"])
        self.assertIn("没有生成或发送图片", payload["message"])
        self.assertIn("不要复述", payload["final_response_instruction"])
        self.assertNotIn("The prompt", serialized)
        self.assertNotIn("sensitive words", serialized)
        self.assertNotIn("policies.google.com", serialized)

    def test_policy_refusal_detection_requires_both_refusal_and_policy_signals(self) -> None:
        harness = _PhotoToolHarness()

        self.assertTrue(
            harness._photo_generation_policy_refusal(
                "The prompt could not be submitted because it contains sensitive words."
            )
        )
        self.assertFalse(
            harness._photo_generation_policy_refusal(
                "The phrase content policy violation can appear in an error log, but the request succeeded."
            )
        )

    async def test_send_false_reports_generated_but_not_sent(self) -> None:
        harness = _PhotoToolHarness()
        harness.image_path = self.image_path

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="画一张房间照片",
                send=False,
            )
        )

        self.assertEqual(payload["status"], "success")
        self.assertTrue(payload["success"])
        self.assertTrue(payload["generated"])
        self.assertFalse(payload["send_requested"])
        self.assertFalse(payload["sent"])
        self.assertTrue(payload["must_not_claim_sent"])

    async def test_tool_photo_prompt_builder_respects_configured_format(self) -> None:
        cases = (
            ("nai", "1girl, red hair, {holding umbrella}, rain, wet ground, solo", False),
            (
                "traditional",
                "Positive prompt: 1girl, red hair, rain. Negative prompt: text, watermark.",
                False,
            ),
            (
                "natural_language",
                "A red-haired girl holds an umbrella on a rainy street.",
                True,
            ),
        )

        for prompt_format, prompt, expects_sections in cases:
            with self.subTest(prompt_format=prompt_format):
                harness = _PromptAwarePhotoToolHarness()
                harness.image_path = self.image_path
                harness._photo_generation_prompt_format_mode = (
                    lambda mode=prompt_format: mode
                )

                payload = json.loads(
                    await harness._pc_generate_photo_impl(
                        _FakeEvent(),
                        prompt=prompt,
                        kind="selfie",
                        send=False,
                    )
                )

                self.assertEqual(payload["status"], "success")
                self.assertEqual(harness.generation_kwargs["prompt_text"], prompt)
                self.assertEqual(
                    harness.generation_kwargs["prompt_format"],
                    prompt_format,
                )
                self.assertEqual(
                    harness.generation_kwargs["prompt_sections"] is not None,
                    expects_sections,
                )

    async def test_group_photo_without_explicit_reference_is_rejected_before_generation(self) -> None:
        harness = _PhotoToolHarness()
        harness.image_path = self.image_path

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="我和小林在咖啡店拍一张照片",
                kind="selfie",
                send=False,
            )
        )

        self.assertEqual(payload["status"], "need_reference")
        self.assertFalse(payload["generated"])
        self.assertFalse(payload["sent"])
        self.assertTrue(payload["must_not_claim_sent"])
        self.assertEqual(harness.generation_kwargs, {})

    async def test_group_photo_with_named_relationship_role_reference_reaches_generation(self) -> None:
        harness = _RelationshipRolePhotoToolHarness()
        harness.image_path = self.image_path

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="我和姐姐在咖啡店拍一张合影",
                kind="selfie",
                send=False,
            )
        )

        self.assertEqual(payload["status"], "success")
        self.assertEqual(harness.generation_kwargs["reference_image_path"], "")
        self.assertEqual(harness.generation_kwargs["reference_image_paths"], [])

    async def test_group_photo_does_not_use_unmentioned_relationship_role_reference(self) -> None:
        harness = _RelationshipRolePhotoToolHarness(explicit_mention=False)
        harness.image_path = self.image_path

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="我和朋友们在咖啡店拍一张合影",
                kind="selfie",
                send=False,
            )
        )

        self.assertEqual(payload["status"], "need_reference")
        self.assertEqual(harness.generation_kwargs, {})

    async def test_group_photo_named_role_without_managed_asset_is_rejected(self) -> None:
        harness = _RelationshipRolePhotoToolHarness(role_reference_path="")
        harness.image_path = self.image_path

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="我和姐姐在咖啡店拍一张合影",
                kind="selfie",
                send=False,
            )
        )

        self.assertEqual(payload["status"], "need_reference")
        self.assertEqual(harness.generation_kwargs, {})

    async def test_group_photo_named_role_is_rejected_when_reference_feature_is_disabled(self) -> None:
        harness = _RelationshipRolePhotoToolHarness()
        harness.image_path = self.image_path
        harness.enable_photo_reference_image = False

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="我和姐姐在咖啡店拍一张合影",
                kind="selfie",
                send=False,
            )
        )

        self.assertEqual(payload["status"], "need_reference")
        self.assertEqual(harness.generation_kwargs, {})

    async def test_group_photo_current_event_image_takes_priority_over_named_role_asset(self) -> None:
        harness = _RelationshipRolePhotoToolHarness(role_reference_path="C:/managed/sister.png")
        harness.image_path = self.image_path

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(reference_path=self.image_path),
                prompt="我和姐姐在咖啡店拍一张合影",
                kind="selfie",
                send=False,
            )
        )

        self.assertEqual(payload["status"], "success")
        self.assertEqual(
            harness.generation_kwargs["reference_image_path"],
            self.image_path,
        )
        self.assertEqual(
            harness.generation_kwargs["reference_image_paths"],
            [self.image_path],
        )

    async def test_group_photo_with_explicit_reference_uses_multi_person_contract(self) -> None:
        harness = _PromptAwarePhotoToolHarness()
        harness.image_path = self.image_path

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(reference_path=self.image_path),
                prompt="把我和小林的合影变成咖啡店随手拍",
                kind="selfie",
                reference_image_path=self.image_path,
                send=False,
            )
        )

        prompt_sections = harness.generation_kwargs["prompt_sections"]
        positive = "\n".join(section.positive for section in prompt_sections if section.positive)
        negative = "\n".join(section.negative for section in prompt_sections if section.negative)
        self.assertEqual(payload["status"], "success")
        self.assertIn("multi-person photo based only on the explicitly supplied source reference", positive)
        self.assertNotIn("single character selfie", positive)
        self.assertIn("unreferenced extra people", negative)

    async def test_group_photo_parameter_path_cannot_replace_current_event_reference(self) -> None:
        harness = _PromptAwarePhotoToolHarness()
        harness.image_path = self.image_path

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="我和小林拍一张合影",
                kind="selfie",
                reference_image_path=self.image_path,
                send=False,
            )
        )

        self.assertEqual(payload["status"], "need_reference")
        self.assertFalse(payload["generated"])
        self.assertTrue(payload["must_not_claim_sent"])
        self.assertEqual(harness.generation_kwargs, {})

    async def test_quoted_image_is_used_for_plain_selfie_retake(self) -> None:
        harness = _PromptAwarePhotoToolHarness()
        harness.image_path = self.image_path

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(reference_path=self.image_path),
                prompt="重新拍一张",
                kind="selfie",
                send=False,
            )
        )

        self.assertEqual(payload["status"], "success")
        self.assertEqual(
            harness.generation_kwargs["reference_image_path"],
            self.image_path,
        )
        self.assertEqual(
            harness.generation_kwargs["reference_image_paths"],
            [self.image_path],
        )

    async def test_rule_fast_selfie_uses_context_image_as_explicit_reference(self) -> None:
        harness = _CommandEntryPhotoHarness()
        harness.image_path = self.image_path
        event = _FakeEvent(reference_path=self.image_path)

        handled = await harness._maybe_handle_natural_language_photo_request(
            event,
            "10001",
            "重新拍一张",
            directed=True,
        )

        self.assertTrue(handled)
        self.assertTrue(event.stopped)
        self.assertEqual(
            harness.generation_kwargs["reference_image_path"],
            self.image_path,
        )

    async def test_companion_selfie_command_uses_context_image_as_explicit_reference(self) -> None:
        harness = _CommandEntryPhotoHarness()
        harness.image_path = self.image_path
        event = _FakeEvent(reference_path=self.image_path)

        handled = await harness._handle_companion_photo_command(
            event,
            "10001",
            "自拍",
            "重新拍一张",
        )

        self.assertTrue(handled)
        self.assertTrue(event.stopped)
        self.assertEqual(
            harness.generation_kwargs["reference_image_path"],
            self.image_path,
        )

    async def test_companion_selfie_command_without_context_image_keeps_normal_selection(self) -> None:
        harness = _CommandEntryPhotoHarness()
        harness.image_path = self.image_path
        event = _FakeEvent()

        handled = await harness._handle_companion_photo_command(
            event,
            "10001",
            "自拍",
            "重新拍一张",
        )

        self.assertTrue(handled)
        self.assertTrue(event.stopped)
        self.assertEqual(harness.generation_kwargs["reference_image_path"], "")

    async def test_leg_request_is_classified_as_character_selfie(self) -> None:
        harness = _PhotoToolHarness()
        harness.image_path = self.image_path
        event = _FakeEvent()

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                event,
                prompt="给我看看腿",
                caption="拍给你看啦～看看是不是很有食欲呀？以后有机会一定带你去吃嘛。",
            )
        )

        self.assertEqual(harness.workflow_kind, "selfie")
        self.assertEqual(payload["intent_kind"], "selfie")
        self.assertTrue(payload["sent"])
        self.assertIn(PHOTO_TOOL_SILENT_SENTINEL, payload["final_response_instruction"])
        self.assertTrue(event._private_companion_photo_tool_sent)
        self.assertIn("拍给你看啦", event._private_companion_photo_tool_sent_caption)

    async def test_proactive_framework_defers_photo_to_unified_send_chain(self) -> None:
        harness = _PhotoToolHarness()
        harness.image_path = self.image_path
        event = _FakeEvent()
        event.private_companion_proactive_framework = True

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                event,
                prompt="拍一张窗边的日常自拍",
                kind="selfie",
                caption="刚好坐到窗边，就想拍给你看看。",
            )
        )

        self.assertEqual(payload["status"], "success")
        self.assertTrue(payload["success"])
        self.assertTrue(payload["generated"])
        self.assertFalse(payload["sent"])
        self.assertTrue(payload["delivery_deferred"])
        self.assertEqual(harness.delivery_calls, 0)
        self.assertTrue(event._private_companion_photo_tool_deferred)
        self.assertEqual(event._private_companion_photo_tool_deferred_path, self.image_path)
        self.assertEqual(
            event._private_companion_photo_tool_deferred_caption,
            "刚好坐到窗边，就想拍给你看看。",
        )
        self.assertIn(PHOTO_TOOL_SILENT_SENTINEL, payload["final_response_instruction"])
        self.assertNotIn("delivery_error", payload)

    async def test_proactive_framework_removes_status_only_photo_caption(self) -> None:
        harness = _PhotoToolHarness()
        harness.image_path = self.image_path
        event = _FakeEvent()
        event.private_companion_proactive_framework = True

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                event,
                prompt="拍一张窗边的日常自拍",
                kind="selfie",
                caption="图生好了",
            )
        )

        self.assertTrue(payload["delivery_deferred"])
        self.assertEqual(event._private_companion_photo_tool_deferred_caption, "")
        self.assertEqual(harness.delivery_calls, 0)

    async def test_bot_in_scene_text2img_is_promoted_to_reference_capable_selfie(self) -> None:
        harness = _PhotoToolHarness()
        harness.bot_name = "测试角色"
        harness.image_path = self.image_path

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="测试角色站在阳台上背对镜头看晚霞",
                kind="text2img",
                send=False,
            )
        )

        self.assertEqual(harness.workflow_kind, "selfie")
        self.assertEqual(payload["intent_kind"], "selfie")

    async def test_first_person_scene_text2img_is_promoted_to_reference_capable_selfie(self) -> None:
        harness = _PhotoToolHarness()
        harness.image_path = self.image_path

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="我站在阳台上背对镜头看晚霞",
                kind="text2img",
                send=False,
            )
        )

        self.assertEqual(harness.workflow_kind, "selfie")
        self.assertEqual(payload["intent_kind"], "selfie")

    async def test_sunset_scene_text2img_keeps_scene_workflow_without_character_prompt(self) -> None:
        harness = _PromptAwarePhotoToolHarness()
        harness.image_path = self.image_path

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="傍晚阳台视角的绝美晚霞，天空是粉紫色和橙黄色的渐变",
                kind="text2img",
                scene_preset="可拍画面",
                send=False,
            )
        )

        prompt_sections = harness.generation_kwargs["prompt_sections"]
        prompt_text = "\n".join(
            part
            for section in prompt_sections
            for part in (section.positive, section.negative)
            if part
        )
        self.assertEqual(harness.workflow_kind, "text2img")
        self.assertEqual(payload["intent_kind"], "text2img")
        self.assertIn("detailed environment and object art", prompt_text)
        self.assertIn("do not add any unrequested person", prompt_text)
        self.assertNotIn("clean detailed character art", prompt_text)

    async def test_named_bot_back_view_uses_selfie_prompt_without_pose_conflict(self) -> None:
        harness = _PromptAwarePhotoToolHarness()
        harness.bot_name = "测试角色"
        harness.image_path = self.image_path

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="测试角色站在阳台上背对镜头看晚霞",
                kind="text2img",
                send=False,
            )
        )

        prompt_sections = harness.generation_kwargs["prompt_sections"]
        positive = "\n".join(section.positive for section in prompt_sections if section.positive)
        negative = "\n".join(section.negative for section in prompt_sections if section.negative)
        self.assertEqual(harness.workflow_kind, "selfie")
        self.assertEqual(payload["intent_kind"], "selfie")
        self.assertIn("the requested back view is intentional", positive)
        self.assertNotIn("back view", negative)

    async def test_photo_caption_removes_internal_emotion_control_tags(self) -> None:
        harness = _PhotoToolHarness()
        harness.image_path = self.image_path
        event = _FakeEvent()

        await harness._pc_generate_photo_impl(
            event,
            prompt="拍一张居家自拍",
            kind="selfie",
            caption="&&shy&& 今天的穿搭……就是普通的居家服啦。",
        )

        self.assertEqual(harness.delivery_kwargs["caption"], "今天的穿搭……就是普通的居家服啦。")
        self.assertFalse(harness.delivery_kwargs.get("reaction_image", False))
        self.assertEqual(event._private_companion_photo_tool_sent_caption, "今天的穿搭……就是普通的居家服啦。")

    async def test_missing_caption_sends_image_without_receipt(self) -> None:
        harness = _PhotoToolHarness()
        harness.image_path = self.image_path

        await harness._pc_generate_photo_impl(
            _FakeEvent(),
            prompt="草稿纸上的小星星",
            kind="text2img",
        )

        self.assertEqual(harness.delivery_kwargs["caption"], "")

    async def test_generic_caption_is_removed_instead_of_replaced_with_receipt(self) -> None:
        harness = _PhotoToolHarness()
        harness.image_path = self.image_path

        await harness._pc_generate_photo_impl(
            _FakeEvent(),
            prompt="草稿纸上的小星星",
            kind="text2img",
            caption="生成好了。",
        )

        self.assertEqual(harness.delivery_kwargs["caption"], "")

    def test_photo_caption_receipt_detection_keeps_only_substantive_text(self) -> None:
        harness = _PhotoToolHarness()

        for receipt in (
            "图生好了",
            "图片生成好了。",
            "生图成功啦",
            "我拍好了，给你看～",
            "按你的要求画好了，给你看",
            "图片已发送",
            "已经发给你了",
            "给你看看",
        ):
            with self.subTest(receipt=receipt):
                self.assertTrue(harness._photo_caption_is_generic(receipt))

        for natural_text in (
            "窗边的光正好，拍给你看看。",
            "今天换了你上次说好看的那件外套。",
            "这个表情特别像你刚才吐槽我的样子。",
        ):
            with self.subTest(natural_text=natural_text):
                self.assertFalse(harness._photo_caption_is_generic(natural_text))

    async def test_edit_with_reference_keeps_edit_workflow_kind(self) -> None:
        harness = _PhotoToolHarness()
        harness.image_path = self.image_path

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="只把背景换成蓝色，其他不变",
                kind="edit",
                reference_image_path=self.image_path,
                send=False,
            )
        )

        self.assertEqual(harness.workflow_kind, "edit")
        self.assertEqual(payload["kind"], "edit")
        self.assertEqual(payload["intent_kind"], "edit")
        self.assertNotIn("path", payload)
        self.assertNotIn("reference_image_path", payload)
        self.assertNotIn("prompt_path", payload)
        self.assertIsNone(harness.memory_calls[0]["reference_used"])

    async def test_model_temp_reference_recovers_only_through_current_event_verification(self) -> None:
        harness = _PhotoToolHarness()
        harness.image_path = self.image_path
        event_temp_path = os.path.join(self.temp_dir.name, "astrbot-temp", "media_image_current.png")
        stable_path = os.path.join(self.temp_dir.name, "photo_reference_images", "verified.png")
        event = _FakeEvent(reference_path=event_temp_path)
        calls: list[tuple[str, str]] = []

        async def reject_untrusted(source, **_kwargs):
            calls.append(("untrusted", source))
            return ""

        async def verify_event(_event, _user_id, source, **_kwargs):
            calls.append(("event", source))
            return stable_path if source == event.reference_path else ""

        harness._photo_reference_source_to_stable_path = reject_untrusted
        harness._photo_reference_event_bound_stable_path = verify_event

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                event,
                prompt="只把背景换成蓝色",
                kind="edit",
                reference_image_path=event_temp_path,
                send=False,
            )
        )

        self.assertEqual("success", payload["status"])
        self.assertEqual(stable_path, harness.generation_kwargs["reference_image_path"])
        self.assertEqual(
            [("untrusted", event_temp_path), ("event", event_temp_path)],
            calls,
        )

    async def test_unrelated_model_temp_reference_remains_rejected(self) -> None:
        harness = _PhotoToolHarness()
        source = "/AstrBot/data/temp/unrelated.png"

        async def reject_untrusted(_source, **_kwargs):
            return ""

        async def reject_event(_event, _user_id, _source, **_kwargs):
            return ""

        harness._photo_reference_source_to_stable_path = reject_untrusted
        harness._photo_reference_event_bound_stable_path = reject_event

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="修改图片",
                kind="edit",
                reference_image_path=source,
                send=False,
            )
        )

        self.assertEqual("invalid_reference", payload["status"])
        self.assertFalse(payload["generated"])
        self.assertNotIn(source, json.dumps(payload, ensure_ascii=False))

    async def test_explicit_tool_reference_preserves_double_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reference = os.path.join(directory, "persona  original.png")
            with open(reference, "wb") as handle:
                handle.write(b"reference")
            harness = _PhotoToolHarness()
            harness.image_path = self.image_path

            payload = json.loads(
                await harness._pc_generate_photo_impl(
                    _FakeEvent(),
                    prompt="保持人物生成自拍",
                    kind="selfie",
                    reference_image_path=reference,
                    send=False,
                )
            )

        self.assertEqual(harness.generation_kwargs["reference_image_path"], reference)
        self.assertNotIn(reference, json.dumps(payload, ensure_ascii=False))

    async def test_scene_preset_is_forwarded_structurally_without_mutating_prompt(self) -> None:
        harness = _PhotoToolHarness()
        harness.image_path = self.image_path
        source_prompt = (
            "Positive prompt: at a dorm desk after evening skincare, calm Japanese-style portrait. "
            "Negative prompt: text, watermark, cropped head."
        )

        await harness._pc_generate_photo_impl(
            _FakeEvent(),
            prompt=source_prompt,
            kind="selfie",
            scene_preset="居家睡衣",
            send=False,
        )

        self.assertEqual(harness.generation_kwargs["prompt_text"], source_prompt)
        self.assertEqual(harness.generation_kwargs["requested_scene_preset"], "居家睡衣")
        self.assertNotIn("【指定生图场景预设】", harness.generation_kwargs["prompt_text"])

    async def test_structured_reference_usage_is_forwarded_to_memory_without_note_guessing(self) -> None:
        cases = (
            (True, "ok (generate_selfie)"),
            (False, "ok；已使用在线 API #1"),
        )
        for expected, note in cases:
            with self.subTest(expected=expected):
                harness = _StructuredPhotoToolHarness(reference_used=expected, note=note)
                harness.image_path = self.image_path

                payload = json.loads(
                    await harness._pc_generate_photo_impl(
                        _FakeEvent(),
                        prompt="保持人物生成一张自拍",
                        kind="selfie",
                        reference_image_path=self.image_path,
                        send=False,
                    )
                )

                self.assertEqual(payload["used_reference"], expected)
                self.assertEqual(harness.memory_calls[0]["reference_used"], expected)

    async def test_reference_plan_payload_does_not_expose_binding_paths(self) -> None:
        hidden_reference = "C:/private/reference library/persona.png"
        harness = _StructuredPhotoToolHarness(
            reference_used=True,
            note="ok",
            reference_plan=(
                {
                    "reference_id": "persona",
                    "path": hidden_reference,
                    "resolved_path": hidden_reference,
                    "roles": ["identity"],
                    "priority": 100,
                    "preserve": ["face", "hair"],
                    "ignore": ["outfit"],
                    "submitted": True,
                },
            ),
        )
        harness.image_path = self.image_path

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="拍一张日常自拍",
                kind="selfie",
                send=False,
            )
        )

        self.assertEqual(
            payload["reference_plan"],
            [
                {
                    "reference_id": "persona",
                    "roles": ["identity"],
                    "priority": 100,
                    "preserve": ["face", "hair"],
                    "ignore": ["outfit"],
                    "submitted": True,
                }
            ],
        )
        self.assertNotIn(hidden_reference, json.dumps(payload, ensure_ascii=False))

    async def test_model_visible_receipt_redacts_paths_from_all_text_fields(self) -> None:
        generated_path = self.image_path
        windows_path = r"C:\Users\99505\AppData\Local\Temp\prompt trace.json"
        unc_path = r"\\private-server\reference library\persona.png"
        posix_path = "/var/lib/private-companion/prompts/trace.json"
        relative_path = "data/photo_prompt_debug/trace.json"
        public_url = "https://api.example.com/v1/images/output.png"
        harness = _StructuredPhotoToolHarness(
            reference_used=True,
            note=f"后端 {public_url} 记录写入 {windows_path}",
            prompt_path=windows_path,
            reference_fallback_message=f"参考图读取自 {unc_path}，调试记录位于 {relative_path}",
            reference_plan=(
                {
                    "reference_id": f"source:{posix_path}",
                    "path": unc_path,
                    "roles": ["identity"],
                    "submitted": True,
                },
            ),
        )
        harness.image_path = generated_path
        harness.delivery = {
            "sent": False,
            "destination": "current",
            "message": f"图片发送失败，临时文件位于 {generated_path}",
        }

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="拍一张日常自拍",
                kind="selfie",
            )
        )

        serialized = json.dumps(payload, ensure_ascii=False)
        for hidden_path in (generated_path, windows_path, unc_path, posix_path, relative_path):
            self.assertNotIn(hidden_path, serialized)
        self.assertNotIn("path", payload)
        self.assertNotIn("reference_image_path", payload)
        self.assertNotIn("prompt_path", payload)
        self.assertIn("[本地路径已隐藏]", serialized)
        self.assertIn(public_url, serialized)

    async def test_reference_resolution_error_receipt_redacts_local_path(self) -> None:
        hidden_path = r"C:\Users\99505\Pictures\private reference.png"
        harness = _PhotoToolHarness()

        async def fail_to_resolve(_event, _user_id):
            raise RuntimeError(f"cannot open {hidden_path}")

        harness._photo_reference_image_from_command_context = fail_to_resolve
        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="把这张图的背景换成海边",
                kind="edit",
                send=False,
            )
        )

        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertEqual(payload["status"], "error")
        self.assertNotIn(hidden_path, serialized)
        self.assertIn("[本地路径已隐藏]", serialized)

    def test_photo_caption_short_repeat_is_suppressed_but_new_information_is_kept(self) -> None:
        harness = _PhotoToolHarness()
        caption = "拍给你看啦～看看是不是很有食欲呀？以后有机会一定带你去吃嘛。"

        self.assertTrue(
            harness._photo_tool_followup_is_redundant(
                caption,
                "拍给你看啦，看看是不是很有食欲呀？",
            )
        )
        self.assertFalse(
            harness._photo_tool_followup_is_redundant(
                caption,
                "图片发送成功，不过参考图没有成功载入。",
            )
        )

    def test_exact_five_character_photo_caption_repeat_is_suppressed(self) -> None:
        harness = _PhotoToolHarness()

        self.assertTrue(
            harness._photo_tool_followup_is_redundant(
                "今晚的夜色",
                "今晚的夜色。",
            )
        )
        self.assertFalse(
            harness._photo_tool_followup_is_redundant(
                "今晚的夜色",
                "窗台上的绿萝也入镜了。",
            )
        )

    async def test_plugin_returns_structured_timeout_before_framework_timeout(self) -> None:
        harness = _PhotoToolHarness()
        harness.image_path = self.image_path
        harness.generation_delay = 0.2
        harness._photo_tool_call_timeout_seconds = lambda: 0.05

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="拍一张自拍",
                kind="selfie",
            )
        )

        self.assertEqual(payload["status"], "timeout")
        self.assertFalse(payload["success"])
        self.assertFalse(payload["generated"])
        self.assertFalse(payload["sent"])
        self.assertTrue(payload["must_not_claim_sent"])

    def test_instruction_matches_leg_request_and_forbids_false_receipt(self) -> None:
        harness = _PhotoToolHarness()

        self.assertTrue(harness._photo_generation_instruction_matches("宝宝，看看腿"))
        instruction = harness._photo_generation_tool_instruction()
        self.assertIn("只有工具返回 `sent=true`", instruction)
        self.assertIn("工具返回 `sent=false`", instruction)
        self.assertIn("本轮最多调用一次 `pc_generate_photo`", instruction)
        self.assertIn("`failure_stage=result_materialization`", instruction)
        self.assertIn("不要重复提交同一画面", instruction)
        self.assertIn("绝对不能声称", instruction)
        self.assertIn(PHOTO_TOOL_SILENT_SENTINEL, instruction)
        self.assertIn("不要把最终回复留空", instruction)
        self.assertIn("不要再写承接句", instruction)
        self.assertIn("不要写 `&&shy&&`", instruction)
        self.assertIn("不要复述或翻译 Provider 的英文原文", instruction)
        self.assertIn("pc_send_current_media", instruction)
        self.assertIn("另一个生图工具", instruction)
        self.assertIn("画面中不出现角色本人", instruction)
        self.assertIn("背影、侧脸、环境人像", instruction)
        self.assertIn("合影、合照、双人或多人同框", instruction)
        self.assertIn("纯文字关系卡都不算其他人物参考", instruction)
        self.assertIn("结合本轮已有的当前日程、位置和对话判断", instruction)
        self.assertIn("分享之前拍的、相册里的照片", instruction)
        self.assertIn("这不是固定拒绝规则", instruction)
        self.assertIn("明确强调“现在、立刻、现场拍”", instruction)
        truth_rule = harness._media_delivery_truth_instruction()
        self.assertIn("只有本轮消息链实际包含图片", truth_rule)
        self.assertIn("人格和角色扮演不能覆盖", truth_rule)


    def test_rule_fast_intent_treats_leg_request_as_selfie(self) -> None:
        intent = _PhotoIntentHarness()._natural_language_photo_intent(
            "看看腿",
            directed=True,
        )

        self.assertEqual(intent["kind"], "selfie")
        self.assertEqual(intent["prompt"], "看看腿")


if __name__ == "__main__":
    unittest.main()
