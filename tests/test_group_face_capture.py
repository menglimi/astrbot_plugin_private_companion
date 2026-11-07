"""A group sticker is an emotional message, not unreadable media.

The capture used to reject every raw group component it did not model, so one
QQ face turned the whole turn into ``host_group_media_unsupported`` and silently
dropped V2's reply path (voice, segmentation, Laya). A face now becomes a bounded
``[表情]`` marker that counts as the member's message.
"""

from types import SimpleNamespace as NS
import unittest

from companion_v2.adapters.astrbot.group import NativeGroupBridge


def mapping_event(*segments, native=()):
    return NS(
        message_obj=NS(
            raw_message={"message": list(segments)},
            message=list(native),
            self_id="10001",
        ),
    )


def native_event(*parts):
    return NS(
        message_obj=NS(raw_message=None, message=list(parts), self_id="10001"),
    )


ROUTE = NS(account_id="10001")


class GroupFaceCaptureTests(unittest.TestCase):
    def test_raw_face_only_message_is_captured_as_an_expression(self):
        text = NativeGroupBridge._text_only_parts(
            mapping_event({"type": "face", "data": {"id": "13"}}), ROUTE)
        self.assertIn("[表情]", text)

    def test_raw_face_alongside_text_keeps_both(self):
        text = NativeGroupBridge._text_only_parts(
            mapping_event({"type": "text", "data": {"text": "哈哈"}},
                          {"type": "face", "data": {"id": "13"}}), ROUTE)
        self.assertIn("哈哈", text)
        self.assertIn("[表情]", text)

    def test_native_face_only_message_is_captured_as_an_expression(self):
        text = NativeGroupBridge._text_only_parts(
            native_event(NS(type="Face", id="13")), ROUTE)
        self.assertIn("[表情]", text)

    def test_native_market_face_is_captured_as_an_expression(self):
        text = NativeGroupBridge._text_only_parts(
            native_event(NS(type="Mface", id="1")), ROUTE)
        self.assertIn("[表情]", text)

    def test_raw_sticker_image_is_captured_as_an_expression(self):
        text = NativeGroupBridge._text_only_parts(
            mapping_event({"type": "image", "data": {"file": "a.gif", "sub_type": 1}},
                          native=(NS(type="Image", file="a.gif", url=""),)), ROUTE)
        self.assertIn("[表情包]", text)

    def test_plain_photo_is_not_reported_as_a_sticker(self):
        text = NativeGroupBridge._text_only_parts(
            mapping_event({"type": "image", "data": {"file": "a.jpg", "sub_type": 0}},
                          native=(NS(type="Image", file="a.jpg", url=""),)), ROUTE)
        self.assertIn("[图片不可用]", text)
        self.assertNotIn("[表情包]", text)


if __name__ == "__main__":
    unittest.main()
