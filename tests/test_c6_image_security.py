from __future__ import annotations

import asyncio
import importlib
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def _install_astrbot_stubs() -> None:
    class _Logger:
        def __getattr__(self, _name: str):
            return lambda *args, **kwargs: None

    def ensure_module(name: str) -> types.ModuleType:
        module = sys.modules.get(name)
        if not isinstance(module, types.ModuleType):
            module = types.ModuleType(name)
            sys.modules[name] = module
        # Earlier unittest modules install a minimal ``astrbot.api`` module.
        # Marking it as a package lets this test add the submodules required by
        # the real image/command imports without depending on import order.
        if name in {"astrbot", "astrbot.api", "astrbot.core", "astrbot.core.agent", "astrbot.core.utils"}:
            module.__path__ = getattr(module, "__path__", [])
        return module

    astrbot = ensure_module("astrbot")
    api = ensure_module("astrbot.api")
    if not hasattr(api, "logger"):
        api.logger = _Logger()
    else:
        for method_name in ("debug", "info", "warning", "error", "exception"):
            if not hasattr(api.logger, method_name):
                setattr(api.logger, method_name, lambda *args, **kwargs: None)
    event = ensure_module("astrbot.api.event")
    event.AstrMessageEvent = getattr(event, "AstrMessageEvent", object)
    # page_api.py 顶层 `from astrbot.api.event import MessageChain` 需要此桩。
    event.MessageChain = getattr(event, "MessageChain", type("MessageChain", (), {"__init__": lambda self, *a, **k: None}))
    message_components = ensure_module("astrbot.api.message_components")
    message_components.Image = getattr(message_components, "Image", type("Image", (), {}))
    message_components.Plain = getattr(message_components, "Plain", type("Plain", (), {}))
    provider = ensure_module("astrbot.api.provider")
    provider.ProviderRequest = getattr(provider, "ProviderRequest", type("ProviderRequest", (), {}))

    core = ensure_module("astrbot.core")
    if not hasattr(core, "file_token_service"):
        core.file_token_service = types.SimpleNamespace()
    agent = ensure_module("astrbot.core.agent")
    agent_message = ensure_module("astrbot.core.agent.message")
    agent_message.AssistantMessageSegment = getattr(
        agent_message, "AssistantMessageSegment", type("AssistantMessageSegment", (), {})
    )
    agent_message.UserMessageSegment = getattr(
        agent_message, "UserMessageSegment", type("UserMessageSegment", (), {})
    )
    # private_image.py 顶层还需要 TextPart。
    agent_message.TextPart = getattr(
        agent_message, "TextPart", type("TextPart", (), {"__init__": lambda self, *a, **k: None})
    )
    astr_main_agent = ensure_module("astrbot.core.astr_main_agent")
    astr_main_agent.MainAgentBuildConfig = getattr(
        astr_main_agent, "MainAgentBuildConfig", type("MainAgentBuildConfig", (), {})
    )
    if not hasattr(astr_main_agent, "build_main_agent"):
        astr_main_agent.build_main_agent = lambda *args, **kwargs: None
    utils = ensure_module("astrbot.core.utils")
    astrbot_path = ensure_module("astrbot.core.utils.astrbot_path")
    if not hasattr(astrbot_path, "get_astrbot_data_path"):
        astrbot_path.get_astrbot_data_path = lambda: tempfile.gettempdir()


def _load_modules():
    _install_astrbot_stubs()
    package_name = "c6_image_security_companion"
    package = types.ModuleType(package_name)
    package.__path__ = [str(ROOT)]
    sys.modules[package_name] = package
    helpers = importlib.import_module(f"{package_name}.helpers")
    command_handlers = importlib.import_module(f"{package_name}.command_handlers")
    private_image = importlib.import_module(f"{package_name}.private_image")
    return helpers, command_handlers.CommandHandlersMixin, private_image


helpers, CommandHandlersMixin, private_image = _load_modules()


class _Host(CommandHandlersMixin, private_image.PrivateImageMixin):
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = str(data_dir)


class ImageSecurityTests(unittest.TestCase):
    def test_url_host_classifier_rejects_private_and_accepts_public_resolution(self) -> None:
        private_info = [(2, 1, 6, "", ("127.0.0.1", 0))]
        public_info = [(2, 1, 6, "", ("93.184.216.34", 0))]
        with patch.object(helpers.socket, "getaddrinfo", return_value=private_info):
            self.assertFalse(helpers._url_host_is_public("https://example.test/image.png"))
        with patch.object(helpers.socket, "getaddrinfo", return_value=public_info):
            self.assertTrue(helpers._url_host_is_public("https://example.test/image.png"))
        self.assertFalse(helpers._url_host_is_public("file:///etc/passwd"))

    def test_untrusted_reference_path_and_internal_url_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_dir = root / "plugin-data"
            data_dir.mkdir()
            outside = root / "outside.png"
            outside.write_bytes(b"not a secret image")
            host = _Host(data_dir)

            self.assertEqual("", host._photo_reference_copy_local_file(outside, trusted=False))
            self.assertTrue(host._photo_reference_copy_local_file(outside, trusted=True))
            self.assertEqual(
                "",
                asyncio.run(
                    host._photo_reference_source_to_stable_path(
                        "http://169.254.169.254/latest/meta-data/",
                        trusted=False,
                    )
                ),
            )

    def test_exact_current_event_temp_image_is_copied_without_broad_temp_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_dir = root / "plugin-data"
            data_dir.mkdir()
            event_image = root / "astrbot-temp" / "media_image_current.png"
            event_image.parent.mkdir()
            event_image.write_bytes(b"current-event-image")
            unrelated = root / "astrbot-temp" / "unrelated.png"
            unrelated.write_bytes(b"unrelated-image")
            host = _Host(data_dir)
            event = types.SimpleNamespace()

            async def current_sources(_event, _user_id):
                return [str(event_image)]

            async def reply_sources(_event):
                return []

            host._photo_reference_sources_from_current_event = current_sources
            host._photo_reference_sources_from_reply_cache = lambda _event: []
            host._photo_reference_sources_from_reply_event = reply_sources

            stable = asyncio.run(
                host._photo_reference_event_bound_stable_path(
                    event,
                    "10001",
                    str(event_image),
                    stem="verified_event",
                )
            )
            rejected = asyncio.run(
                host._photo_reference_event_bound_stable_path(
                    event,
                    "10001",
                    str(unrelated),
                    stem="unrelated",
                )
            )

            self.assertTrue(stable)
            self.assertTrue(Path(stable).is_relative_to(data_dir.resolve()))
            self.assertEqual(event_image.read_bytes(), Path(stable).read_bytes())
            self.assertEqual("", rejected)

    def test_private_image_source_guard_does_not_encode_arbitrary_local_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_dir = root / "plugin-data"
            data_dir.mkdir()
            outside = root / "outside.png"
            outside.write_bytes(b"private")
            host = _Host(data_dir)
            with patch.object(private_image.tempfile, "gettempdir", return_value=str(data_dir / "tmp")), patch.object(
                private_image, "get_astrbot_data_path", return_value=str(data_dir / "astrbot")
            ):
                self.assertEqual("", host._private_image_source_to_model_url(str(outside)))

            inside = data_dir / "inside.png"
            inside.write_bytes(b"image")
            self.assertTrue(host._private_image_source_to_model_url(str(inside)).startswith("data:image/png;base64,"))

    def test_public_only_redirect_handler_rejects_private_target(self) -> None:
        handler = private_image._PublicOnlyRedirectHandler()
        with patch.object(private_image, "_url_host_is_public", return_value=False):
            self.assertIsNone(handler.redirect_request(None, None, 302, "", {}, "http://127.0.0.1/secret"))

    def test_stale_prepared_image_cleanup_removes_only_old_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target_dir = Path(temporary)
            old_file = target_dir / "old.png"
            fresh_file = target_dir / "fresh.png"
            old_file.write_bytes(b"old")
            fresh_file.write_bytes(b"fresh")
            old_timestamp = private_image._now_ts() - private_image.PREPARED_IMAGE_MAX_AGE_SECONDS - 5
            os.utime(old_file, (old_timestamp, old_timestamp))
            host = _Host(Path(temporary) / "plugin-data")

            self.assertEqual(1, host._sweep_stale_prepared_image_files(target_dir))
            self.assertFalse(old_file.exists())
            self.assertTrue(fresh_file.exists())

    def test_remote_download_guard_short_circuits_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            host = _Host(Path(temporary))
            with patch.object(private_image.urllib.request, "urlopen") as urlopen:
                result = asyncio.run(
                    host._persist_private_remote_image_source(
                        "http://10.0.0.8/image.png",
                        Path(temporary),
                        "probe",
                        public_hosts_only=True,
                    )
                )
            self.assertEqual("", result)
            urlopen.assert_not_called()

    def test_rejected_remote_source_is_not_passed_through_to_the_model(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            host = _Host(Path(temporary))
            host._event_components = lambda _event: []
            host._raw_private_image_sources = lambda _event: [
                "http://169.254.169.254/latest/meta-data/"
            ]
            with patch.object(private_image, "_url_host_is_public", return_value=False):
                result = asyncio.run(host._persist_private_inbound_images(object(), "owner"))
            self.assertEqual([], result)


if __name__ == "__main__":
    unittest.main()
