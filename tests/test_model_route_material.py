from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from astrbot_host.model_route_material import (build_summary, image_edit_reference_limit,
                                               image_route_snapshot, route_snapshot, scope_umo)
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind, ScopeRef, SubjectKind, SubjectRef


class Meta(dict):
    """Provider meta 用属性读取，普通 dict 不够。"""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as error:
            raise AttributeError(name) from error


class Provider:
    def __init__(self, meta):
        self._meta = Meta(meta)

    def meta(self):
        return self._meta


class Host:
    def __init__(self, chat=None, tts=None):
        self._chat, self._tts = chat, tts

    def get_using_provider(self, umo=None):
        return self._chat

    def get_using_tts_provider(self, umo=None):
        return self._tts


def context():
    return SimpleNamespace(actor="actor", observed_at=NOW)


SNAPSHOT = {
    "tasks": [
        {"kind": "conversation.reply", "fields": {"provider": {"value": "p-chat"}}},
        {"kind": "vision.review", "fields": {"provider": {"value": "p-vision"}}},
        {"kind": "image.generate", "fields": {"provider": {"value": "p-image"}}},
    ],
    "providers": [
        {"id": "p-chat", "model": "gemini-3.8-flash"},
        {"id": "p-vision", "model": "deepseek-v4-flash"},
        {"id": "p-image", "model": "gpt-image-2"},
    ],
}

NOW = datetime(2026, 10, 25, 10, 0, tzinfo=timezone.utc)


class ModelRouteMaterialTests(unittest.TestCase):
    def test_the_same_snapshot_as_the_dashboard_card_drives_every_category(self):
        # 扩展页那张卡读的就是这份快照；回复链路直接复用，避免"页面看得到、聊天看不到"。
        host = Host(tts=Provider({"id": "fishaudio", "model": "s2.1-pro-free"}))
        with patch("astrbot_host.model_route_material.route_snapshot", return_value=SNAPSHOT):
            summary = build_summary(None, host, None, context(), NOW)
        self.assertEqual(summary["对话"], {"provider": "p-chat", "model": "gemini-3.8-flash"})
        self.assertEqual(summary["视觉理解"], {"provider": "p-vision", "model": "deepseek-v4-flash"})
        self.assertEqual(summary["生图"], {"provider": "p-image", "model": "gpt-image-2"})
        self.assertEqual(summary["语音合成"], {"provider": "fishaudio", "model": "s2.1-pro-free"})

    def test_without_a_snapshot_it_falls_back_to_host_providers(self):
        host = Host(chat=Provider({"id": "星渊哈基米", "model": "gemini-3.8-flash"}))
        with patch("astrbot_host.model_route_material.route_snapshot", return_value=None):
            summary = build_summary(None, host, None, context(), NOW)
        self.assertEqual(summary["对话"], {"provider": "星渊哈基米", "model": "gemini-3.8-flash"})
        self.assertEqual(summary["生图"]["state"], "unavailable")
        self.assertEqual(summary["视觉理解"]["state"], "unavailable")

    def test_missing_everything_is_still_an_explicit_state(self):
        with patch("astrbot_host.model_route_material.route_snapshot", return_value=None):
            summary = build_summary(None, None, None, context(), NOW)
        for title in ("对话", "语音合成", "视觉理解", "生图"):
            self.assertEqual(summary[title]["state"], "unavailable", title)

    def test_scope_umo_matches_host_convention(self):
        scope = SimpleNamespace(installation_id="inst", kind=SimpleNamespace(value="private"),
                                platform="aiocqhttp", native_scope_id="995051631")
        self.assertEqual(scope_umo(SimpleNamespace(scope=scope)), "aiocqhttp:FriendMessage:995051631")
        scope.kind = SimpleNamespace(value="group")
        scope.native_scope_id = "1097283005"
        self.assertEqual(scope_umo(SimpleNamespace(scope=scope)), "aiocqhttp:GroupMessage:1097283005")
        self.assertEqual(scope_umo(SimpleNamespace()), "")

    def test_route_snapshot_reads_window_configuration_without_management_status(self):
        snapshot = route_snapshot(RouteApplication(), route_context())
        self.assertEqual(snapshot["tasks"][0]["fields"]["provider"]["value"], "chat")
        self.assertEqual(snapshot["providers"][0]["model"], "chat-model")

    def test_visual_route_uses_registered_screen_task_and_native_image_model(self):
        summary = build_summary(RouteApplication(), RouteHost(), None, route_context(), NOW,
                                native_tool_port=NativePort())
        self.assertEqual(summary["视觉理解"]["model"], "chat-model")
        self.assertEqual(summary["生图"]["model"], "gpt-image-1")

    def test_image_route_is_unavailable_without_an_enabled_endpoint(self):
        self.assertEqual(image_route_snapshot(None)["state"], "unavailable")

    def test_local_image_route_does_not_invent_a_model_name(self):
        summary = image_route_snapshot(LocalNativePort())
        self.assertEqual(summary, {"provider": "sd_webui"})

    def test_nai_route_reports_plugin_defaults_and_omits_unavailable_operations(self):
        port = NativePort()
        port.api.settings["operation_routes"] = {"generate": ["nai"], "edit": ["nai"]}
        port.api.nai = {"available": True, "default_model": "nai-diffusion-5-full",
                        "operations": ["generate", "edit"], "api_key": "must-not-escape"}
        summary = image_route_snapshot(port)
        self.assertEqual(summary, {"provider": "NAI 生图插件", "model": "nai-diffusion-5-full"})
        self.assertEqual(image_edit_reference_limit(port), 1)
        port.api.nai["operations"] = ["generate"]
        self.assertIsNone(image_edit_reference_limit(port))
        port.api.nai["available"] = False
        self.assertEqual(image_route_snapshot(port)["state"], "unavailable")
        port.api.nai["available"] = True
        port.api.settings["enabled"] = False
        self.assertEqual(image_route_snapshot(port)["state"], "unavailable")

    def test_edit_reference_limit_uses_the_most_conservative_configured_fallback(self):
        port = NativePort()
        settings = port.image_extension_api().output_configuration()["settings"]
        settings["operation_routes"]["edit"] = ["online:primary", "online:secondary"]
        settings["endpoints"].append({"id": "secondary", "enabled": True,
            "base_url": "https://backup.example", "platform": "openai", "model": "backup",
            "max_reference_images": 3})

        self.assertEqual(image_edit_reference_limit(port), 1)
        self.assertNotIn("max_reference_images", image_route_snapshot(port))

    def test_edit_reference_limit_reads_through_a_composite_tool_port(self):
        port = NativePort()

        self.assertEqual(image_edit_reference_limit(SimpleNamespace(ports=(port,))), 1)

    def test_edit_reference_limit_ignores_unavailable_protocols_and_reads_local_workflow_slots(self):
        online = NativePort()
        settings = online.image_extension_api().output_configuration()["settings"]
        settings["operation_routes"]["edit"] = ["online:primary"]
        settings["endpoints"][0]["platform"] = "minimax"
        self.assertIsNone(image_edit_reference_limit(online))

        local = LocalNativePort()
        settings = local.image_extension_api().output_configuration()["settings"]
        settings["operation_routes"]["edit"] = ["comfyui"]
        settings["comfyui"] = {"base_url": "http://127.0.0.1:8188", "workflows": {
            "edit": {"mapping": {"fields": {
                "identity": {"kind": "image"}, "outfit": {"kind": "image"}}}}}}
        self.assertEqual(image_edit_reference_limit(local), 2)


def route_context():
    scope = ScopeRef("install", "qq", ScopeKind.PRIVATE, "friend",
                     bot_id="bot", account_id="account", conversation_id="conversation",
                     session_id="session")
    role = SubjectRef("install", SubjectKind.BOT, "role")
    return SimpleNamespace(scope=scope,
                           role_binding=SimpleNamespace(role=role), actor=object())


class RouteProvider:
    def __init__(self, identifier, model):
        self.binding = SimpleNamespace(provider=identifier)
        self.model = model
        self.capabilities = ("text", "image")


class RouteProfile:
    def __init__(self, kind, provider="chat"):
        self.kind = kind
        self.group = "model." + kind
        self.default_provider = provider


class RouteRouter:
    def __init__(self):
        self.profiles = {
            "conversation.reply": RouteProfile("conversation.reply"),
            "screen.interpretation": RouteProfile("screen.interpretation"),
        }
        self.providers = {"chat": RouteProvider("chat", "chat-model")}

    def provider_default(self, context, profile, snapshot=None):
        return None

    def apply_provider_default(self, profile, snapshot, provider_id):
        return snapshot


class RouteConfiguration:
    def read(self, actor, *, target, group_id, observed_at):
        return Outcome.success(SimpleNamespace(fields={
            group_id + ".provider": SimpleNamespace(value="chat"),
        }))


class RouteApplication:
    def __init__(self):
        self.router = RouteRouter()
        self.configuration = RouteConfiguration()
        self.now = lambda: NOW


class RouteHost:
    def get_using_provider(self, umo=None):
        return SimpleNamespace(meta=lambda: SimpleNamespace(id="chat", model="chat-model"))

    def get_using_tts_provider(self, umo=None):
        return SimpleNamespace(meta=lambda: SimpleNamespace(id="tts", model="voice-model"))


class ImageAPI:
    def __init__(self):
        self.nai = None
        self.settings = {
            "enabled": True,
            "operation_routes": {"generate": ["online:primary"], "edit": ["online:primary"]},
            "endpoints": [{"id": "primary", "enabled": True, "base_url": "https://image.example",
                           "platform": "openai", "model": "gpt-image-1", "max_reference_images": 1}],
        }

    def output_configuration(self):
        return {"settings": self.settings, "nai": self.nai}


class NativePort:
    def __init__(self):
        self.api = ImageAPI()

    def image_extension_api(self):
        return self.api


class LocalImageAPI:
    def __init__(self):
        self.settings = {
            "enabled": True,
            "operation_routes": {"generate": ["sd_webui"]},
            "sd_webui": {"base_url": "http://127.0.0.1:7860"},
        }

    def output_configuration(self):
        return {"settings": self.settings}


class LocalNativePort:
    def __init__(self):
        self.api = LocalImageAPI()

    def image_extension_api(self):
        return self.api


if __name__ == "__main__":
    unittest.main()
