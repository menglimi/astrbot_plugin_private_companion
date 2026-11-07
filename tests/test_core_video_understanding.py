"""Native core video route and original-byte dispatch contracts."""

from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from astrbot_host.video.core_understanding import select_native_video_routes, summarize_original_video


class CoreVideoUnderstandingTests(unittest.IsolatedAsyncioTestCase):
    def test_loaded_gemini_is_available_when_not_in_the_image_fallback_chain(self):
        class Gemini:
            def get_model(self):
                return "gemini-video"

        provider = Gemini()
        host = NS(get_provider_by_id=lambda _id: None,
                  get_config=lambda **_kwargs: {"provider_settings": {}, "agent_runner": {}},
                  get_all_providers=lambda: [object(), provider])
        event = NS(unified_msg_origin="qq:private:1", get_extra=lambda _key: None)
        with patch("astrbot_host.video.core_understanding.ProviderGoogleGenAI", Gemini):
            self.assertEqual(select_native_video_routes(NS(router=None), host, None, event, None),
                             ((provider, "gemini-video"),))

    async def test_native_request_sends_the_complete_mp4_and_accepts_string_finish_reason(self):
        original = b"complete-original-mp4"
        result = NS(candidates=[NS(finish_reason="STOP", content=NS(parts=[NS(text="完整视频转述", thought=False)]))])
        generate = AsyncMock(return_value=result)
        provider = NS(client=NS(models=NS(generate_content=generate)))
        with patch("astrbot_host.video.core_understanding._prepare_generation_config",
                   AsyncMock(return_value=NS())), \
             patch("astrbot_host.video.core_understanding.inspect_container"):
            text = await summarize_original_video(original, provider, "gemini-video", {})
        self.assertEqual(text, "完整视频转述")
        kwargs = generate.await_args.kwargs
        self.assertEqual(kwargs["model"], "gemini-video")
        parts = kwargs["contents"][0].parts
        self.assertEqual(parts[1].inline_data.mime_type, "video/mp4")
        self.assertEqual(parts[1].inline_data.data, original)
