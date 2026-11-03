"""Native features prefer the task panel route over their legacy provider ids."""

import unittest
from types import SimpleNamespace

from astrbot_host.native_extensions.game.activity_methods import GameActivityMethods
from astrbot_host.native_extensions.together.model_methods import TogetherModelMethods
from companion_v2.reality.model_profile import reality_text_profile


class Provider:
    def __init__(self, name):
        self.name = name
        self.provider_config = {"modalities": ["text", "image"]}

    async def text_chat(self, **_kwargs):
        return None


class Context:
    def __init__(self, providers):
        self.providers = providers

    def get_provider_by_id(self, provider_id):
        return self.providers.get(provider_id)


class Integration:
    def __init__(self, route):
        self.route = route

    def task_model_route(self, user_id, kind):
        return self.route.get(kind, {})


class TogetherHost(TogetherModelMethods):
    def __init__(self, providers, route):
        self.context = Context(providers)
        self.controller = SimpleNamespace(integration=Integration(route))
        self.chat_provider_id = "legacy"


class GameHost(GameActivityMethods):
    def __init__(self, providers, route):
        self.context = Context(providers)
        self.controller = SimpleNamespace(integration=Integration(route))


class NativeModelRouteTests(unittest.TestCase):
    def test_together_prefers_the_panel_reply_route(self):
        host = TogetherHost({"panel": Provider("panel"), "legacy": Provider("legacy")},
                            {"conversation.reply": {"provider": "panel", "fallback": "legacy"}})
        self.assertEqual(host._task_route_provider("user-1", "conversation.reply").name, "panel")

    def test_game_prefers_the_panel_vision_route(self):
        host = GameHost({"panel": Provider("panel"), "legacy": Provider("legacy")},
                        {"screen.interpretation": {"provider": "panel", "fallback": ""}})
        room = SimpleNamespace(creator_qq="user-1", player_qq="")
        self.assertEqual(host._task_vision_provider(room).name, "panel")

    def test_reality_text_builds_a_background_task_profile(self):
        profile = reality_text_profile("primary")
        self.assertEqual(profile.kind, "reality.text")
        self.assertEqual(profile.default_provider, "primary")


if __name__ == "__main__":
    unittest.main()
