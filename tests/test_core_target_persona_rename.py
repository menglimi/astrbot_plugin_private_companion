import unittest
from types import SimpleNamespace

from astrbot_host.multi_controller import AstrBotMultiController


def target(bot_id="bot-a", persona="new-name"):
    return SimpleNamespace(platform_id="aiocqhttp", account_id="bot-a", participant_id="1001",
                           target_id="t1", bot_id=bot_id, persona_id=persona, role_id="role")


def binding(persona="old-name"):
    return SimpleNamespace(platform_id="aiocqhttp", account_id="bot-a", participant_id="1001",
                           persona_id=persona)


def resolve(personas):
    fake = SimpleNamespace(settings=SimpleNamespace(targets=[target()], personas=personas))
    return AstrBotMultiController._core_target(fake, binding())


class CoreTargetPersonaRenameTests(unittest.TestCase):
    def test_single_persona_window_survives_an_upstream_rename(self):
        match = resolve([SimpleNamespace(bot_id="bot-a", persona_id="new-name", role_id="role")])
        self.assertIsNotNone(match)
        self.assertEqual(match.participant_id, "1001")

    def test_exact_name_still_wins_without_fallback(self):
        match = resolve([SimpleNamespace(bot_id="bot-a", persona_id="old-name", role_id="other")])
        self.assertIsNotNone(match)

    def test_ambiguous_multi_persona_window_still_refuses(self):
        match = resolve([SimpleNamespace(bot_id="bot-a", persona_id="new-name", role_id="role"),
                         SimpleNamespace(bot_id="bot-a", persona_id="another", role_id="role2")])
        self.assertIsNone(match)

    def test_persona_of_another_bot_is_not_accepted(self):
        match = resolve([SimpleNamespace(bot_id="bot-b", persona_id="new-name", role_id="role")])
        self.assertIsNone(match)


if __name__ == "__main__":
    unittest.main()
