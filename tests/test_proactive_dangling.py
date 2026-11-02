"""A proactive turn must not answer the role's own previous reply."""

import unittest
from types import SimpleNamespace

from companion_v2.proactive.service import ProactiveContacts


def job(last_reply_at="2026-11-01T10:20:05+00:00", last_input_at="2026-11-01T10:19:00+00:00"):
    return SimpleNamespace(
        last_reply={"observed_at": last_reply_at},
        interaction={"last_input_at": last_input_at},
    )


class DanglingProactiveTests(unittest.TestCase):
    def test_marker_after_the_roles_own_reply_is_dangling(self):
        self.assertTrue(ProactiveContacts._dangling_after_own_reply(
            job(), ("哦？", "照这么数下去", "四碳糖、三碳糖……")))

    def test_a_new_user_input_closes_the_previous_reply(self):
        self.assertFalse(ProactiveContacts._dangling_after_own_reply(
            job(last_input_at="2026-11-01T10:21:00+00:00"), ("哦？",)))

    def test_an_independent_opener_is_allowed(self):
        self.assertFalse(ProactiveContacts._dangling_after_own_reply(
            job(), ("刚路过楼下的桂花树", "香得有点过分了")))

    def test_no_previous_reply_is_not_dangling(self):
        self.assertFalse(ProactiveContacts._dangling_after_own_reply(
            SimpleNamespace(last_reply=None, interaction={"last_input_at": "2026-11-01T10:19:00+00:00"}),
            ("哦？",)))


if __name__ == "__main__":
    unittest.main()
