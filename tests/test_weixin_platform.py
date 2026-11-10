"""Personal WeChat (`weixin_oc`) uses its own account and subject namespace."""

import unittest
from types import SimpleNamespace as NS

from tests import astrbot_data_root  # noqa: F401 - isolate the SDK before import
from astrbot_host.binding import event_identity
from companion_v2.adapters.astrbot.native import (
    event_account_id, native_user_subject_id, supported_platform_name,
)


def weixin_event(*, message_type="FriendMessage", sender="o9cq8043@im.wechat"):
    meta = NS(name="weixin_oc", id="wx-main")
    message = NS(type=NS(value=message_type), self_id="wxid_bot",
                 sender=NS(user_id=sender), group_id="")
    return NS(platform_meta=meta, message_obj=message,
              unified_msg_origin=f"wx-main:{message_type}:{sender}")


class WeixinPlatformTests(unittest.TestCase):
    def test_weixin_is_in_the_supported_platform_set(self):
        self.assertTrue(supported_platform_name("weixin_oc"))

    def test_instance_id_is_the_stable_account_key(self):
        self.assertEqual(event_account_id(weixin_event()), "wx-main")

    def test_weixin_subjects_use_their_own_namespace(self):
        self.assertEqual(native_user_subject_id("weixin_oc", "o9cq8043@im.wechat"),
                         "weixin-user:o9cq8043@im.wechat")

    def test_binding_identity_keeps_the_openid_as_participant(self):
        self.assertEqual(event_identity(weixin_event()),
                         {"platform_id": "wx-main", "account_id": "wx-main",
                          "participant_id": "o9cq8043@im.wechat"})


if __name__ == "__main__":
    unittest.main()
