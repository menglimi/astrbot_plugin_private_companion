"""Image weight is a real threshold for image-only group messages."""

import unittest

from companion_v2.conversation.group_admission import image_wakeup_candidate
from companion_v2.conversation.reply_settings import group_wakeup_settings


def wakeup(**overrides):
    return group_wakeup_settings({"group_wakeup_image_enabled": True,
                                  "group_wakeup_image_weight": 0.2, **overrides})


class ImageWakeupCandidateTests(unittest.TestCase):
    def test_low_weight_image_alone_does_not_wake_the_group(self):
        """The reported 0.2 must not behave like an image-only invitation."""
        self.assertFalse(image_wakeup_candidate(wakeup(), has_image=True, text=""))
        self.assertFalse(image_wakeup_candidate(wakeup(), has_image=True, text="[图片]"))

    def test_midpoint_weight_admits_an_image_only_message(self):
        self.assertTrue(image_wakeup_candidate(wakeup(group_wakeup_image_weight=0.5),
                                               has_image=True, text=""))

    def test_text_decides_candidacy_and_images_stay_context(self):
        self.assertFalse(image_wakeup_candidate(wakeup(group_wakeup_image_weight=1.0),
                                                has_image=True, text="看这个"))

    def test_disabled_or_imageless_messages_never_use_the_image_path(self):
        self.assertFalse(image_wakeup_candidate(wakeup(group_wakeup_image_enabled=False),
                                                has_image=True, text=""))
        self.assertFalse(image_wakeup_candidate(wakeup(group_wakeup_image_weight=1.0),
                                                has_image=False, text=""))


if __name__ == "__main__":
    unittest.main()
