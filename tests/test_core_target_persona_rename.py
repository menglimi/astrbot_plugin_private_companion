"""A renamed persona must not deadlock the confirmed core-admin window."""

import unittest
from types import SimpleNamespace

from astrbot_host.multi_controller import AstrBotMultiController


def persona(name, role="role"):
    return SimpleNamespace(bot_id="bot-a", persona_id=name, role_id=role)


def target():
    return SimpleNamespace(platform_id="qq", account_id="100", participant_id="200",
                           bot_id="bot-a", target_id="qq:100:200")


class CoreTargetPersonaTests(unittest.TestCase):
    def controller(self, personas):
        controller = object.__new__(AstrBotMultiController)
        controller.settings = SimpleNamespace(personas=tuple(personas), targets=(target(),))
        return controller

    def binding(self, name):
        return SimpleNamespace(platform_id="qq", account_id="100", participant_id="200",
                               persona_id=name)

    def test_old_persona_name_still_resolves_through_the_roles_unique_mapping(self):
        controller = self.controller([persona("liloth", role="莉莉丝")])
        self.assertIs(controller._core_target(self.binding("莉莉丝")), controller.settings.targets[0])

    def test_single_persona_for_the_bot_resolves_even_without_the_old_name(self):
        controller = self.controller([persona("liloth", role="new-role")])
        self.assertIs(controller._core_target(self.binding("莉莉丝")), controller.settings.targets[0])

    def test_default_placeholder_does_not_make_the_renamed_persona_ambiguous(self):
        controller = self.controller([persona("[%None]", role="default"),
                                      persona("liloth", role="new-role")])
        self.assertIs(controller._core_target(self.binding("莉莉丝")), controller.settings.targets[0])

    def test_multiple_personas_stay_rejected_instead_of_guessed(self):
        controller = self.controller([persona("first", role="one"),
                                      persona("second", role="two")])
        self.assertIsNone(controller._core_target(self.binding("莉莉丝")))

    def test_exact_name_wins_even_when_other_personas_exist(self):
        controller = self.controller([persona("莉莉丝", role="one"),
                                      persona("second", role="two")])
        self.assertIs(controller._core_target(self.binding("莉莉丝")), controller.settings.targets[0])


if __name__ == "__main__":
    unittest.main()
