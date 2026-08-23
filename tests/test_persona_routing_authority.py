from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from astrbot_plugin_private_companion.main import PrivateCompanionPlugin
from astrbot_plugin_private_companion.tests.test_multi_persona_isolation import (
    _plugin_harness,
)


class _PersonaManager:
    def __init__(self, *, session_persona: str = "") -> None:
        self.session_persona = session_persona
        self.personas_v3 = [
            {"name": "main", "prompt": "MAIN"},
            {"name": "alt", "prompt": "ALT"},
            {"name": "disabled", "prompt": "DISABLED"},
        ]

    def get_persona_v3_by_id(self, persona_id: str):
        return next(
            (item for item in self.personas_v3 if item["name"] == persona_id),
            None,
        )

    async def resolve_selected_persona(
        self,
        *,
        umo,
        conversation_persona_id,
        platform_name,
        provider_settings,
    ):
        del umo, platform_name
        selected = self.session_persona
        forced = selected
        if not selected:
            selected = conversation_persona_id
            forced = ""
            if selected is None:
                selected = provider_settings.get("default_personality")
        persona = self.get_persona_v3_by_id(selected)
        return selected, persona, forced, False


class _ConversationManager:
    def __init__(self, persona_id) -> None:
        self.persona_id = persona_id

    async def get_curr_conversation_id(self, umo: str) -> str:
        return f"conversation:{umo}"

    async def get_conversation(self, umo: str, conversation_id: str):
        del umo, conversation_id
        return SimpleNamespace(persona_id=self.persona_id)


def _event(umo: str = "QBot123:GroupMessage:opaque") -> SimpleNamespace:
    return SimpleNamespace(
        unified_msg_origin=umo,
        get_platform_name=lambda: "qq_official",
    )


def _routing_harness(root: str, *, conversation_persona="alt", session_persona=""):
    plugin = _plugin_harness(root)
    plugin.context = SimpleNamespace(
        conversation_manager=_ConversationManager(conversation_persona),
        persona_manager=_PersonaManager(session_persona=session_persona),
        get_config=lambda **_kwargs: {
            "provider_settings": {"default_personality": "main"}
        },
    )
    plugin._ensure_persona_profile("main")
    plugin._ensure_persona_profile("alt")
    return plugin


class PersonaRoutingAuthorityTests(unittest.IsolatedAsyncioTestCase):
    async def test_primary_uses_single_store_without_profile_file(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            plugin._data_default["users"]["real_data_marker"] = {"value": 1}

            primary = plugin._ensure_persona_profile("main")
            token = plugin._activate_persona_id("main")
            try:
                self.assertIs(plugin.data, plugin._data_default)
                plugin.data["users"]["real_data_marker"] = {"value": 2}
                plugin._save_data_sync(sections={"users"})
            finally:
                plugin._deactivate_persona_for_event(token)
            await plugin._flush_scheduled_data_save()

            self.assertIs(primary, plugin._data_default)
            self.assertFalse(plugin._persona_profile_path("main").exists())
            stored = json.loads(Path(plugin.data_file).read_text(encoding="utf-8"))
            self.assertEqual({"value": 2}, stored["users"]["real_data_marker"])

    async def test_session_rule_wins_over_conversation_persona(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _routing_harness(
                root,
                conversation_persona="main",
                session_persona="alt",
            )
            event = _event()

            token, persona_id = await plugin._activate_persona_for_event_context(event)
            try:
                self.assertEqual("alt", persona_id)
                self.assertEqual("alt", plugin._active_persona_scope())
                self.assertEqual("session_rule", event._private_companion_persona_route_decision["source"])
            finally:
                plugin._deactivate_persona_for_event(token)

    async def test_conversation_and_provider_default_follow_astrbot_precedence(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _routing_harness(root, conversation_persona="alt")
            token, persona_id = await plugin._activate_persona_for_event_context(_event())
            plugin._deactivate_persona_for_event(token)
            self.assertEqual("alt", persona_id)

            plugin.context.conversation_manager.persona_id = None
            token, persona_id = await plugin._activate_persona_for_event_context(
                _event("onebot:FriendMessage:1")
            )
            plugin._deactivate_persona_for_event(token)
            self.assertEqual("main", persona_id)

    async def test_unenabled_astrbot_persona_falls_back_to_primary_and_warns_once(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _routing_harness(root, conversation_persona="disabled")
            event = _event()

            first_token, first = await plugin._activate_persona_for_event_context(event)
            plugin._deactivate_persona_for_event(first_token)
            second_token, second = await plugin._activate_persona_for_event_context(event)
            plugin._deactivate_persona_for_event(second_token)

            self.assertEqual(("main", "main"), (first, second))
            decision = event._private_companion_persona_route_decision
            self.assertTrue(decision["fallback"])
            self.assertEqual("persona_not_enabled", decision["reason_code"])
            items = plugin._data_default["persona_routing_warnings"]["items"]
            self.assertEqual(1, len(items))
            self.assertEqual("persona.route.passive_primary_fallback", items[0]["code"])
            self.assertEqual(1, items[0]["count"])

    async def test_missing_secondary_profile_does_not_get_created_by_passive_event(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _routing_harness(root, conversation_persona="alt")
            plugin._persona_data_profiles.pop("alt", None)
            alt_path = plugin._persona_profile_path("alt")
            alt_path.unlink(missing_ok=True)

            token, persona_id = await plugin._activate_persona_for_event_context(_event())
            plugin._deactivate_persona_for_event(token)

            self.assertEqual("main", persona_id)
            self.assertFalse(alt_path.exists())
            warning = plugin._data_default["persona_routing_warnings"]["items"][0]
            self.assertEqual("persona_config_missing", warning["reason_code"])

    async def test_degraded_secondary_profile_falls_back_to_primary(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _routing_harness(root, conversation_persona="alt")
            plugin._persona_profile_errors = {"alt": "broken json"}

            token, persona_id = await plugin._activate_persona_for_event_context(_event())
            plugin._deactivate_persona_for_event(token)

            self.assertEqual("main", persona_id)
            warning = plugin._data_default["persona_routing_warnings"]["items"][0]
            self.assertEqual("persona_profile_degraded", warning["reason_code"])

    async def test_stale_primary_profile_error_does_not_override_single_store(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _routing_harness(root, conversation_persona="disabled")
            plugin._persona_profile_errors = {"main": "broken json"}

            token, persona_id = await plugin._activate_persona_for_event_context(_event())
            plugin._deactivate_persona_for_event(token)

            self.assertEqual("main", persona_id)
            self.assertFalse(plugin._persona_profile_path("main").exists())
            warning = plugin._data_default["persona_routing_warnings"]["items"][0]
            self.assertEqual("fallback", warning["disposition"])

    async def test_legacy_binding_is_backed_up_and_never_overrides_astrbot(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _routing_harness(root, conversation_persona="alt")
            umo = "onebot:FriendMessage:legacy"
            plugin.config["multi_persona_window_bindings"] = {umo: "main"}
            status = plugin._retire_legacy_persona_routing_sync()

            token, persona_id = await plugin._activate_persona_for_event_context(_event(umo))
            plugin._deactivate_persona_for_event(token)

            self.assertEqual("alt", persona_id)
            self.assertTrue(status["ignored"])
            self.assertTrue(Path(status["backup_path"]).is_file())
            warnings = plugin._data_default["persona_routing_warnings"]["items"]
            self.assertEqual("persona.route.legacy_binding_ignored", warnings[0]["code"])

    async def test_proactive_mismatch_is_allowed_in_single_mode_and_blocked_in_multi_mode(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _routing_harness(root, conversation_persona="alt")
            plugin.enable_multi_persona_mode = False
            allowed = await plugin._validate_proactive_persona_delivery(
                "onebot:FriendMessage:1", "main"
            )
            self.assertTrue(allowed["ok"])
            self.assertEqual("sent_with_warning", allowed["action"])

            plugin.enable_multi_persona_mode = True
            blocked = await plugin._validate_proactive_persona_delivery(
                "onebot:FriendMessage:1", "main"
            )
            self.assertFalse(blocked["ok"])
            self.assertEqual("blocked", blocked["action"])
            codes = {
                item["code"]
                for item in plugin._data_default["persona_routing_warnings"]["items"]
            }
            self.assertIn("persona.route.proactive_single_mismatch_allowed", codes)
            self.assertIn("persona.route.proactive_multi_mismatch_blocked", codes)

    async def test_multi_persona_proactive_without_active_scheduler_scope_is_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _routing_harness(root, conversation_persona="main")

            blocked = await plugin._validate_proactive_persona_delivery(
                "onebot:FriendMessage:1", ""
            )

            self.assertFalse(blocked["ok"])
            self.assertEqual("scheduled_persona_missing", blocked["reason_code"])

    async def test_single_persona_mode_never_activates_secondary_profile(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _routing_harness(root, conversation_persona="alt")
            plugin.enable_multi_persona_mode = False

            token, persona_id = await plugin._activate_persona_for_event_context(_event())

            self.assertIsNone(token)
            self.assertEqual("", persona_id)
            self.assertEqual("", plugin._active_persona_scope())
            self.assertEqual(plugin._data_default, plugin.data)

    async def test_single_mode_without_plugin_persona_records_unspecified_warning(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _routing_harness(root, conversation_persona="alt")
            plugin.enable_multi_persona_mode = False
            plugin.plugin_specific_persona_id = ""

            token, persona_id = await plugin._activate_persona_for_event_context(_event())

            self.assertIsNone(token)
            self.assertEqual("", persona_id)
            warnings = plugin._data_default["persona_routing_warnings"]["items"]
            self.assertEqual(1, len(warnings))
            self.assertEqual("persona.route.plugin_persona_unspecified", warnings[0]["code"])
            self.assertEqual("plugin_persona_unspecified", warnings[0]["reason_code"])
            self.assertEqual("passive", warnings[0]["channel"])

    async def test_single_mode_without_plugin_persona_allows_proactive_delivery(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _routing_harness(root, conversation_persona="alt")
            plugin.enable_multi_persona_mode = False
            plugin.plugin_specific_persona_id = ""

            result = await plugin._validate_proactive_persona_delivery(
                "onebot:FriendMessage:1", ""
            )

            self.assertTrue(result["ok"])
            self.assertEqual("sent_with_warning", result["action"])
            self.assertEqual("plugin_persona_unspecified", result["reason_code"])
            warnings = plugin._data_default["persona_routing_warnings"]["items"]
            self.assertEqual(1, len(warnings))
            self.assertEqual("proactive", warnings[0]["channel"])


if __name__ == "__main__":
    unittest.main()
