import unittest
from types import SimpleNamespace

from astrbot_host.fish_audio import DRAMA_DIRECTION, INDEXTTS_DIRECTION, provider_direction
from companion_v2.conversation.voice_reply import VoiceReplyPolicy


def provider(provider_type, model, identifier="p1"):
    return SimpleNamespace(id=identifier, provider_config={"type": provider_type, "model": model},
                           get_model=lambda: model)


def context(target):
    return SimpleNamespace(
        get_provider_by_id=lambda identifier: target if identifier == target.id else None,
        get_all_tts_providers=lambda: [target],
        get_using_tts_provider_async=None,
        get_using_tts_provider=None,
    )


class VoiceDirectionModeTests(unittest.IsolatedAsyncioTestCase):
    async def direction(self, policy, target):
        return await provider_direction(context(target), policy, "umo")

    async def test_off_never_adds_directions(self):
        policy = VoiceReplyPolicy(generation_mode="model_tag", provider_id="p1", direction_mode="off")
        self.assertEqual(await self.direction(policy, provider("fishaudio_tts_api", "drama-3")), "")

    async def test_auto_only_speaks_for_engines_that_support_directions(self):
        policy = VoiceReplyPolicy(generation_mode="model_tag", provider_id="p1")
        self.assertEqual(await self.direction(policy, provider("fishaudio_tts_api", "drama-3")), DRAMA_DIRECTION)
        self.assertEqual(await self.direction(policy, provider("fishaudio_tts_api", "s2.1-pro")), "")
        self.assertEqual(await self.direction(policy, provider("indextts2_tts_api", "indextts2")), INDEXTTS_DIRECTION)

    async def test_forced_fishaudio_keeps_the_drama_contract(self):
        policy = VoiceReplyPolicy(generation_mode="model_tag", provider_id="p1", direction_mode="fishaudio")
        self.assertEqual(await self.direction(policy, provider("fishaudio_tts_api", "s2.1-pro")), DRAMA_DIRECTION)
        self.assertEqual(await self.direction(policy, provider("indextts2_tts_api", "indextts2")), "")

    async def test_forced_indextts2_only_targets_index_providers(self):
        policy = VoiceReplyPolicy(generation_mode="model_tag", provider_id="p1", direction_mode="indextts2")
        self.assertEqual(await self.direction(policy, provider("indextts2_tts_api", "indextts2")), INDEXTTS_DIRECTION)
        self.assertEqual(await self.direction(policy, provider("IndexTTS-2", "v2")), INDEXTTS_DIRECTION)
        self.assertEqual(await self.direction(policy, provider("fishaudio_tts_api", "drama-3")), "")

    async def test_direction_mode_requires_a_supported_value(self):
        with self.assertRaises(ValueError):
            VoiceReplyPolicy(direction_mode="network")


if __name__ == "__main__":
    unittest.main()
