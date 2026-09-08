# -*- coding: utf-8 -*-
from __future__ import annotations

import time
import unittest

from astrbot_plugin_private_companion.photo_nai_params import extract_user_photo_nai_params
from test_photo_tool_delivery_contract import _FakeEvent, _PhotoToolHarness


TAGS = "masterpiece, best quality, 1girl, green hair, sitting, bedroom, soft light, detailed eyes"


class PhotoToolNaiParamsTests(unittest.IsolatedAsyncioTestCase):
    def harness(self, scope="private", mode="nai"):
        harness = _PhotoToolHarness()
        user = {
            "user_id": "10001",
            "last_photo_nai_params": TAGS,
            "last_photo_nai_params_at": time.time(),
        }
        harness.data = {"users": {"10001": user}}
        harness._get_user = lambda _user_id: user
        harness._extract_user_photo_nai_params = extract_user_photo_nai_params
        harness._photo_generation_prompt_format_mode = lambda: mode
        harness._photo_generation_scope = lambda *_args, **_kwargs: scope
        harness._extract_group_id_from_event = lambda _event: "20001"
        harness._group_enabled_for_event = lambda _group_id: True
        return harness

    async def test_current_tags_only_merge_into_nai_prompt(self):
        for mode in ("nai", "natural_language", "traditional"):
            with self.subTest(mode=mode):
                harness = self.harness(mode=mode)
                prompt = "Positive prompt: a park. Negative prompt: blurry" if mode == "traditional" else "a quiet park"
                await harness._pc_generate_photo_impl(_FakeEvent(message_str=TAGS), prompt=prompt, send=False)
                expected = TAGS + ", " + prompt if mode == "nai" else prompt
                self.assertEqual(harness.generation_kwargs["prompt_text"], expected)
                self.assertEqual(harness.generation_kwargs["prompt_format"], mode)

    async def test_new_text_does_not_reuse_cached_tags(self):
        harness = self.harness()
        await harness._pc_generate_photo_impl(_FakeEvent(message_str="Draw a coffee cup"), prompt="coffee cup", send=False)
        self.assertEqual(harness.generation_kwargs["prompt_text"], "coffee cup")

    async def test_missing_text_uses_only_recent_private_cache(self):
        for scope in ("private", "group", "proactive"):
            with self.subTest(scope=scope):
                harness = self.harness(scope=scope)
                event = _FakeEvent()
                event.is_private_chat = lambda: scope != "group"
                if scope == "group":
                    event.unified_msg_origin = "default:GroupMessage:20001"
                await harness._pc_generate_photo_impl(event, prompt="park", send=False)
                expected = TAGS + ", park" if scope == "private" else "park"
                self.assertEqual(harness.generation_kwargs["prompt_text"], expected)

    async def test_expired_cache_does_not_reach_generator(self):
        harness = self.harness()
        harness.data["users"]["10001"]["last_photo_nai_params_at"] -= 3 * 3600
        await harness._pc_generate_photo_impl(_FakeEvent(), prompt="park", send=False)
        self.assertEqual(harness.generation_kwargs["prompt_text"], "park")
