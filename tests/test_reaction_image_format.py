# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import os
import tempfile
import unittest
from pathlib import Path

from astrbot.api.event import MessageChain
from astrbot.api.message_components import BaseMessageComponent, Image
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from astrbot_plugin_private_companion.main import PrivateCompanionPlugin
from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin


class _PlatformEvent:
    unified_msg_origin = "default:FriendMessage:10001"

    def __init__(self, platform_name: str) -> None:
        self.platform_name = platform_name

    def get_platform_name(self) -> str:
        return self.platform_name


class ReactionImageFormatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        handle = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        handle.write(b"reaction-image")
        handle.close()
        self.image_path = Path(handle.name)
        self.plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)

    def tearDown(self) -> None:
        self.image_path.unlink(missing_ok=True)

    async def test_onebot_qq_emoji_format_preserves_sub_type(self) -> None:
        self.plugin.reaction_expression_image_format = "qq_emoji"

        component = self.plugin._build_reaction_image_component(
            _PlatformEvent("aiocqhttp"),
            str(self.image_path),
        )

        self.assertIsInstance(component, BaseMessageComponent)
        self.assertNotIsInstance(component, Image)
        segment = component.toDict()
        self.assertEqual("image", segment["type"])
        self.assertEqual(1, segment["data"]["sub_type"])
        self.assertEqual(
            b"reaction-image",
            base64.b64decode(segment["data"]["file"].removeprefix("base64://")),
        )
        self.assertEqual(
            [segment],
            await AiocqhttpMessageEvent._parse_onebot_json(
                MessageChain(chain=[component])
            ),
        )
        self.assertEqual(
            ("image", os.path.normcase(str(self.image_path.resolve()))),
            PrivateCompanionPlugin._reaction_expression_delivery_signature(component),
        )
        self.assertTrue(
            ProactiveMessageMixin._proactive_components_contain_image([component])
        )

    def test_qq_official_falls_back_to_native_image(self) -> None:
        self.plugin.reaction_expression_image_format = "qq_emoji"

        component = self.plugin._build_reaction_image_component(
            _PlatformEvent("qq_official"),
            str(self.image_path),
        )

        self.assertIsInstance(component, Image)
        self.assertTrue(
            getattr(component, "_private_companion_reaction_expression", False)
        )
        self.assertFalse(hasattr(component, "sub_type"))

    def test_default_format_keeps_native_image_on_onebot(self) -> None:
        self.plugin.reaction_expression_image_format = "image"

        component = self.plugin._build_reaction_image_component(
            _PlatformEvent("napcat"),
            str(self.image_path),
        )

        self.assertIsInstance(component, Image)

    def test_unknown_format_softly_falls_back_to_native_image(self) -> None:
        self.plugin.reaction_expression_image_format = "future-format"

        component = self.plugin._build_reaction_image_component(
            _PlatformEvent("onebot"),
            str(self.image_path),
        )

        self.assertIsInstance(component, Image)


if __name__ == "__main__":
    unittest.main()
