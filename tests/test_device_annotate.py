"""The annotated screenshot must keep its size and draw the numbers the prompt uses."""

from __future__ import annotations

import base64
from io import BytesIO
import hashlib
import unittest

from PIL import Image

from companion_v2.conversation.media.models import ImageDescriptor
from companion_v2.device.adb import parse_ui_hierarchy
from companion_v2.device.annotate import annotate_screen, image_size
from companion_v2.device.prompt_image import OWNER, PhonePromptImage
from companion_v2.execution.models import TaskError
from companion_v2.foundation.refs import ScopeKind, ScopeRef, SourceRef, VersionRef
from datetime import datetime, timezone

XML = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
  <node text="签到" class="android.widget.Button" package="com.example.app"
        clickable="true" enabled="true" bounds="[50,50][150,150]" />
  <node text="说明" class="android.widget.TextView" package="com.example.app"
        clickable="false" enabled="true" bounds="[40,180][200,220]" />
</hierarchy>"""
SCOPE = ScopeRef("install", "onebot", ScopeKind.PRIVATE, "native", "bot", "account", "conversation", "session")
NOW = datetime(2026, 11, 21, tzinfo=timezone.utc)


def screenshot(width=240, height=480) -> bytes:
    image = Image.new("RGB", (width, height), (20, 20, 20))
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


class AnnotateTests(unittest.TestCase):
    def test_grid_and_clickable_boxes_are_drawn_without_resizing(self):
        source = screenshot()
        annotated = annotate_screen(source, parse_ui_hierarchy(XML), grid=100)
        self.assertEqual(image_size(annotated), (240, 480))
        with Image.open(BytesIO(annotated)) as image:
            # A grid line at x=100 crosses the screen; the original pixel was dark.
            self.assertGreater(image.getpixel((100, 60))[0], 40)
            # The clickable node's left edge at (50, 100) is green.
            self.assertGreater(image.getpixel((50, 100))[1], 120)

    def test_non_png_and_bad_grid_are_refused(self):
        with self.assertRaises(ValueError):
            annotate_screen(b"not a png", ())
        with self.assertRaises(ValueError):
            annotate_screen(screenshot(), (), grid=500)

    def test_annotation_changes_the_bytes(self):
        source = screenshot()
        annotated = annotate_screen(source, parse_ui_hierarchy(XML))
        self.assertNotEqual(hashlib.sha256(source).hexdigest(), hashlib.sha256(annotated).hexdigest())

    def test_unstable_frames_keep_the_grid_but_hide_stale_boxes(self):
        annotated = annotate_screen(screenshot(), parse_ui_hierarchy(XML), boxes=False)
        with Image.open(BytesIO(annotated)) as image:
            self.assertLess(image.getpixel((50, 100))[1], 80)


class PromptImageTests(unittest.TestCase):
    def image(self, owner=OWNER):
        data = screenshot(120, 240)
        source = SourceRef("shot-1", owner, SCOPE, VersionRef("shot-1", 1), NOW)
        descriptor = ImageDescriptor(0, "image/png", 120, 240, len(data),
                                     hashlib.sha256(data).hexdigest())
        return PhonePromptImage(source, source, descriptor, base64.b64encode(data).decode("ascii"))

    def test_a_device_capture_is_material_not_conversation_evidence(self):
        image = self.image()
        self.assertEqual(image.evidence_images, ())
        self.assertEqual(image.source.owner, OWNER)

    def test_a_foreign_source_owner_is_refused(self):
        with self.assertRaises(TaskError):
            self.image(owner="conversation.image")


if __name__ == "__main__":
    unittest.main()
