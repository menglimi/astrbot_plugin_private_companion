# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import time
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock

from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin
from astrbot_plugin_private_companion.photo_wardrobe_decision import (
    analyze_photo_wardrobe,
    resolve_photo_wardrobe_decision,
)
from astrbot_plugin_private_companion.self_timeline import SelfTimelineMixin


class _ContinuityHarness(ProactiveMessageMixin):
    def __init__(self, *, candidates=None, model_reply: str = "") -> None:
        self.enable_photo_reference_image = True
        self.data: dict = {}
        self.photo_prompt_provider_id = ""
        self.fast_response_provider_id = ""
        self.llm_provider_id = ""
        self.mai_style_provider_id = ""
        self._normal_candidates = list(candidates or [])
        self.model_reply = model_reply
        self.selection_prompt = ""

    def _save_data_sync(self) -> None:
        return None

    def _task_provider(self, *_args) -> str:
        return ""

    async def _llm_call(self, prompt: str, **_kwargs) -> str:
        self.selection_prompt = prompt
        return self.model_reply

    async def _photo_reference_candidates_async(self, *, allow_daily_outfit: bool = True):
        return deepcopy(self._normal_candidates)


class _TimelineHarness(SelfTimelineMixin, ProactiveMessageMixin):
    pass


class PhotoGenerationContinuityTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _write_image(directory: str, name: str) -> str:
        path = Path(directory) / name
        path.write_bytes(b"not-a-real-image-but-a-stable-test-file")
        return str(path.resolve())

    @staticmethod
    def _remember(harness: _ContinuityHarness, *, key: str, path: str, prompt: str = "") -> None:
        harness._remember_sent_photo_continuity_reference(
            {
                "ts": time.time(),
                "ok": True,
                "sent": True,
                "continuity_key": key,
                "path": path,
                "kind": "selfie",
                "intent_kind": "selfie",
                "prompt": prompt or "宿舍里穿白色外套拿着晚饭自拍",
                "caption": "刚回宿舍，把饭盒放下呢。",
            }
        )

    async def test_pose_followup_can_select_last_sent_photo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recent_path = self._write_image(directory, "recent.png")
            persona_path = self._write_image(directory, "persona.png")
            harness = _ContinuityHarness(
                candidates=[
                    {
                        "id": "persona_default",
                        "path": persona_path,
                        "source": persona_path,
                        "kind": "persona",
                        "note": "基础人物身份参考",
                    }
                ],
                model_reply="1",
            )
            key = harness._compose_photo_continuity_key(
                "default:GroupMessage:12345",
                "10001",
            )
            self._remember(harness, key=key, path=recent_path)

            selected = await harness._select_photo_reference_image_async(
                "selfie",
                selection_context="user request: 给镜头比个心，只换动作",
                continuity_key=key,
            )

            self.assertEqual(selected, recent_path)
            self.assertIn("recent_sent_photo", harness.selection_prompt)
            self.assertIn("自然续拍", harness.selection_prompt)

    async def test_explicit_new_outfit_can_keep_normal_reference_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recent_path = self._write_image(directory, "recent.png")
            persona_path = self._write_image(directory, "persona.png")
            harness = _ContinuityHarness(
                candidates=[
                    {
                        "id": "persona_default",
                        "path": persona_path,
                        "source": persona_path,
                        "kind": "persona",
                        "note": "基础人物身份参考",
                    }
                ],
                model_reply="2",
            )
            key = harness._compose_photo_continuity_key(
                "default:FriendMessage:10001",
                "10001",
            )
            self._remember(harness, key=key, path=recent_path)

            selected = await harness._select_photo_reference_image_async(
                "selfie",
                selection_context="user request: 换上新的红色礼服，在宴会厅重新拍一张",
                continuity_key=key,
            )

            self.assertEqual(selected, persona_path)

    async def test_model_can_decline_only_recent_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recent_path = self._write_image(directory, "recent.png")
            harness = _ContinuityHarness(model_reply="0")
            key = harness._compose_photo_continuity_key(
                "default:FriendMessage:10001",
                "10001",
            )
            self._remember(harness, key=key, path=recent_path)

            selected = await harness._select_photo_reference_image_async(
                "selfie",
                selection_context="user request: 去海边拍一张完全不同的照片",
                continuity_key=key,
            )

            self.assertEqual(selected, "")

    async def test_invalid_model_reply_never_rule_forces_recent_photo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recent_path = self._write_image(directory, "recent.png")
            persona_path = self._write_image(directory, "persona.png")
            harness = _ContinuityHarness(
                candidates=[
                    {
                        "id": "persona_default",
                        "path": persona_path,
                        "source": persona_path,
                        "kind": "persona",
                        "note": "基础人物身份参考",
                    }
                ],
                model_reply="reuse",
            )
            key = harness._compose_photo_continuity_key(
                "default:FriendMessage:10001",
                "10001",
            )
            self._remember(harness, key=key, path=recent_path)

            selected = await harness._select_photo_reference_image_async(
                "selfie",
                selection_context="user request: 给镜头比个心",
                continuity_key=key,
            )

            self.assertEqual(selected, persona_path)

    def test_continuity_candidate_isolated_by_group_sender_and_file_lifetime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recent_path = self._write_image(directory, "recent.png")
            harness = _ContinuityHarness()
            first_key = harness._compose_photo_continuity_key(
                "default:GroupMessage:12345",
                "10001",
            )
            second_key = harness._compose_photo_continuity_key(
                "default:GroupMessage:12345",
                "10002",
            )
            self._remember(harness, key=first_key, path=recent_path)

            self.assertTrue(harness._recent_sent_photo_continuity_candidate(first_key))
            self.assertFalse(harness._recent_sent_photo_continuity_candidate(second_key))

            store_key = harness._photo_continuity_store_key(first_key)
            harness.data["recent_photo_continuity"][store_key]["sent_at"] = time.time() - 3600
            self.assertFalse(harness._recent_sent_photo_continuity_candidate(first_key))

            harness.data["recent_photo_continuity"][store_key]["sent_at"] = time.time()
            Path(recent_path).unlink()
            self.assertFalse(harness._recent_sent_photo_continuity_candidate(first_key))

    def test_annotation_with_path_does_not_mark_concurrent_same_session_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first_path = self._write_image(directory, "first.png")
            second_path = self._write_image(directory, "second.png")
            harness = _ContinuityHarness()
            key = harness._compose_photo_continuity_key(
                "default:FriendMessage:10001",
                "10001",
            )
            harness.data["recent_photo_generations"] = [
                {
                    "ts": time.time(),
                    "session": "tool_photo_default:FriendMessage:10001",
                    "continuity_key": key,
                    "path": second_path,
                    "kind": "selfie",
                    "ok": True,
                },
                {
                    "ts": time.time() - 1,
                    "session": "tool_photo_default:FriendMessage:10001",
                    "continuity_key": key,
                    "path": first_path,
                    "kind": "selfie",
                    "ok": True,
                },
            ]

            harness._annotate_recent_photo_generation(
                image_path=first_path,
                session_key="tool_photo_default:FriendMessage:10001",
                sent=True,
            )

            self.assertNotIn("sent", harness.data["recent_photo_generations"][0])
            self.assertTrue(harness.data["recent_photo_generations"][1]["sent"])
            candidate = harness._recent_sent_photo_continuity_candidate(key)
            self.assertEqual(candidate.get("path"), first_path)

    def test_recent_continuity_prompt_preserves_unrequested_visual_details(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recent_path = self._write_image(directory, "recent.png")
            harness = _ContinuityHarness()
            key = harness._compose_photo_continuity_key(
                "default:FriendMessage:10001",
                "10001",
            )
            self._remember(harness, key=key, path=recent_path)

            request = "user request: 给镜头比个心"
            reference = harness._recent_sent_photo_continuity_candidate(key)
            wardrobe = resolve_photo_wardrobe_decision(
                workflow_kind="selfie",
                prompt_text=request,
                intent=analyze_photo_wardrobe(request),
                reference=reference,
                scene_context="",
                base_prompt=request,
                available_presets=("居家睡衣", "日常穿搭", "角色自拍"),
            )
            prompt, applied = harness._photo_generation_recent_continuity_constraint(
                "selfie",
                reference_image_path=recent_path,
                continuity_key=key,
                wardrobe=wardrobe,
            )

            self.assertTrue(applied)
            self.assertIn("exact outfit and accessories", prompt)
            self.assertIn("explicit new clothing", prompt)

    async def test_remote_reference_config_setters_are_awaited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stable_path = self._write_image(directory, "cached.png")
            persona_harness = _ContinuityHarness()
            persona_harness.data_dir = directory
            persona_harness.photo_persona_reference_image_path = "https://example.com/persona.png"
            persona_harness._photo_reference_source_to_stable_path = AsyncMock(return_value=stable_path)
            persona_harness._set_photo_reference_config_path = AsyncMock(return_value=True)

            persona_path = await persona_harness._photo_persona_reference_image_path_async()

            library_harness = _ContinuityHarness()
            library_harness.data_dir = directory
            library_harness.photo_persona_reference_image_path = ""
            library_harness.photo_reference_library = ["https://example.com/home.png || 居家服参考"]
            library_harness._photo_reference_source_to_stable_path = AsyncMock(return_value=stable_path)
            library_harness._set_photo_reference_library_config = AsyncMock(return_value=True)
            candidates = await ProactiveMessageMixin._photo_reference_candidates_async(
                library_harness,
                allow_daily_outfit=False,
            )

            self.assertEqual(persona_path, stable_path)
            persona_harness._set_photo_reference_config_path.assert_awaited_once_with(stable_path)
            library_harness._set_photo_reference_library_config.assert_awaited_once()
            self.assertTrue(any(item.get("path") == stable_path for item in candidates))

    def test_self_timeline_filters_photo_records_to_current_conversation(self) -> None:
        harness = _TimelineHarness()
        current_user = {
            "user_id": "10001",
            "umo": "default:GroupMessage:12345",
        }
        current_key = harness._compose_photo_continuity_key(current_user["umo"], current_user["user_id"])
        other_key = harness._compose_photo_continuity_key(current_user["umo"], "10002")
        data = {
            "recent_photo_generations": [
                {
                    "ts": time.time(),
                    "continuity_key": other_key,
                    "kind": "selfie",
                    "ok": True,
                    "sent": True,
                    "prompt": "其他群友的红色礼服自拍",
                },
                {
                    "ts": time.time() - 1,
                    "continuity_key": current_key,
                    "kind": "selfie",
                    "ok": True,
                    "sent": True,
                    "prompt": "当前用户看到的白色外套自拍",
                },
            ]
        }

        entries = harness._self_timeline_from_photo_generation(data, user=current_user)
        details = "\n".join(str(item.get("detail") or "") for item in entries)

        self.assertIn("白色外套自拍", details)
        self.assertNotIn("红色礼服自拍", details)


if __name__ == "__main__":
    unittest.main()
