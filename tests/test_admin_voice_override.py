import unittest

from astrbot_host.voice import _provider_with_overrides, admin_voice_override, tts_provider_catalog
from companion_v2.conversation.voice_reply import VoiceReplyPolicy, with_voice_override


CATALOG = {"state": "ready", "providers": [
    {"id": "fixture_tts_s21", "model": "s2.1-pro-free", "voice": "示例音色甲",
     "provider_type": "fishaudio_tts_api"},
    {"id": "fixture_tts_drama3", "model": "drama-3", "voice": "示例音色乙",
     "provider_type": "fishaudio_tts_api"},
]}


class AdminVoiceOverrideTests(unittest.TestCase):
    def test_model_request_selects_matching_loaded_provider_and_forces_one_turn_voice(self):
        policy = VoiceReplyPolicy(provider_id="fallback", language="zh", probability=0.1,
                                  group_probability=0.1)
        result = admin_voice_override(policy, "这次用 drama3 模型语音回复", CATALOG)
        self.assertIsNotNone(result)
        self.assertEqual(result.provider_for_language(), "fixture_tts_drama3")
        self.assertEqual(result.trigger_mode, "always")
        self.assertEqual((result.probability, result.group_probability), (1.0, -1.0))

    def test_short_model_alias_and_language_are_applied(self):
        result = admin_voice_override(
            VoiceReplyPolicy(), "请用日语，2.1free 模型语音回复", CATALOG)
        self.assertIsNotNone(result)
        self.assertEqual(result.provider_for_language(), "fixture_tts_s21")
        self.assertEqual(result.language, "ja")

    def test_unknown_model_hint_keeps_saved_provider_and_sets_turn_override(self):
        result = admin_voice_override(
            VoiceReplyPolicy(provider_id="saved-provider"), "用 drama4 模型语音回复", CATALOG)
        self.assertIsNotNone(result)
        self.assertEqual(result.provider_for_language(), "saved-provider")
        self.assertEqual(getattr(result, "_model_override", ""), "drama4")

    def test_ordinary_discussion_does_not_override_voice(self):
        self.assertIsNone(admin_voice_override(
            VoiceReplyPolicy(), "这个模型的语音效果怎么样？", CATALOG))

    def test_transient_provider_override_precedes_saved_binding(self):
        policy = with_voice_override(VoiceReplyPolicy(provider_id="saved", zh_provider_id="zh-saved"),
                                     provider_id="temporary")
        self.assertEqual(policy.provider_for_language(), "temporary")

    def test_provider_clone_applies_model_and_voice_without_touching_original(self):
        class FishProvider:
            def __init__(self, config, settings):
                self.provider_config = config
                self.provider_settings = settings

        original = FishProvider({"model": "s2.1-pro-free", "fishaudio-tts-character": "示例音色甲",
                                 "api_key": "secret"}, {"timeout": 20})
        cloned = _provider_with_overrides(original, "drama-3", "示例音色乙")
        self.assertEqual(cloned.provider_config["model"], "drama-3")
        self.assertEqual(cloned.provider_config["fishaudio-tts-character"], "示例音色乙")
        self.assertEqual(original.provider_config["model"], "s2.1-pro-free")

    def test_catalog_does_not_expose_secrets(self):
        class Meta:
            id = "fixture_tts_s21"
            model = "s2.1-pro-free"

        class Provider:
            provider_config = {"api_key": "secret", "model": "s2.1-pro-free",
                               "fishaudio-tts-character": "示例音色甲"}

            def meta(self):
                return Meta()

        class Context:
            def get_all_tts_providers(self):
                return [Provider()]

        catalog = tts_provider_catalog(Context())
        self.assertEqual(catalog["state"], "ready")
        self.assertEqual(catalog["providers"][0]["id"], "fixture_tts_s21")
        self.assertEqual(catalog["providers"][0]["voice"], "示例音色甲")
        self.assertNotIn("secret", str(catalog))


if __name__ == "__main__":
    unittest.main()
