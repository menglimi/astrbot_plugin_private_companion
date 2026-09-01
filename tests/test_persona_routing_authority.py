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
            self.assertEqual(1, items[0]["lifetime_count"])
            self.assertEqual("active", items[0]["status"])
            self.assertEqual("passive_delivery", items[0]["warning_family"])
            self.assertEqual(2, plugin._data_default["persona_routing_warnings"]["schema_version"])

    async def test_healthy_passive_route_resolves_same_window_warning(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _routing_harness(root, conversation_persona="disabled")
            umo = "onebot:FriendMessage:recover"

            token, _ = await plugin._activate_persona_for_event_context(_event(umo))
            plugin._deactivate_persona_for_event(token)
            warning = plugin._data_default["persona_routing_warnings"]["items"][0]
            self.assertEqual("active", warning["status"])

            plugin.context.conversation_manager.persona_id = "alt"
            token, persona_id = await plugin._activate_persona_for_event_context(_event(umo))
            plugin._deactivate_persona_for_event(token)

            self.assertEqual("alt", persona_id)
            self.assertEqual("resolved", warning["status"])
            self.assertGreater(warning["resolved_ts"], 0)

    async def test_passive_recovery_isolated_by_channel_and_window(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _routing_harness(root, conversation_persona="disabled")
            first_umo = "onebot:FriendMessage:first"
            second_umo = "onebot:FriendMessage:second"
            for umo in (first_umo, second_umo):
                token, _ = await plugin._activate_persona_for_event_context(_event(umo))
                plugin._deactivate_persona_for_event(token)

            await plugin._record_persona_routing_warning(
                code="persona.route.proactive_multi_mismatch_blocked",
                channel="proactive",
                disposition="blocked",
                reason_code="target_persona_mismatch",
                window_key=first_umo,
            )
            plugin.context.conversation_manager.persona_id = "alt"
            token, _ = await plugin._activate_persona_for_event_context(_event(first_umo))
            plugin._deactivate_persona_for_event(token)

            items = plugin._data_default["persona_routing_warnings"]["items"]
            passive = {
                item["window_key"]: item["status"]
                for item in items
                if item["channel"] == "passive"
            }
            proactive = next(item for item in items if item["channel"] == "proactive")
            self.assertEqual("resolved", passive[first_umo])
            self.assertEqual("active", passive[second_umo])
            self.assertEqual("active", proactive["status"])

    async def test_resolved_warning_recurrence_starts_new_episode(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _routing_harness(root, conversation_persona="disabled")
            umo = "onebot:FriendMessage:recurrence"

            token, _ = await plugin._activate_persona_for_event_context(_event(umo))
            plugin._deactivate_persona_for_event(token)
            plugin.context.conversation_manager.persona_id = "alt"
            token, _ = await plugin._activate_persona_for_event_context(_event(umo))
            plugin._deactivate_persona_for_event(token)
            plugin.context.conversation_manager.persona_id = "disabled"
            token, _ = await plugin._activate_persona_for_event_context(_event(umo))
            plugin._deactivate_persona_for_event(token)

            items = plugin._data_default["persona_routing_warnings"]["items"]
            self.assertEqual(1, len(items))
            warning = items[0]
            self.assertEqual("active", warning["status"])
            self.assertEqual(0, warning["resolved_ts"])
            self.assertEqual(1, warning["count"])
            self.assertEqual(2, warning["lifetime_count"])

    async def test_recurrence_matches_family_without_requested_persona_id(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _routing_harness(root, conversation_persona="disabled")
            umo = "onebot:FriendMessage:family-recurrence"
            token, _ = await plugin._activate_persona_for_event_context(_event(umo))
            plugin._deactivate_persona_for_event(token)
            warning = plugin._data_default["persona_routing_warnings"]["items"][0]
            self.assertEqual("disabled", warning["requested_persona_id"])

            plugin.context.conversation_manager.persona_id = "alt"
            token, _ = await plugin._activate_persona_for_event_context(_event(umo))
            plugin._deactivate_persona_for_event(token)
            plugin.context.conversation_manager.persona_id = "another-disabled"
            token, _ = await plugin._activate_persona_for_event_context(_event(umo))
            plugin._deactivate_persona_for_event(token)

            items = plugin._data_default["persona_routing_warnings"]["items"]
            self.assertEqual(1, len(items))
            self.assertIs(warning, items[0])
            self.assertEqual("another-disabled", warning["requested_persona_id"])
            self.assertEqual(1, warning["count"])
            self.assertEqual(2, warning["lifetime_count"])

    async def test_healthy_route_resolves_legacy_v1_warning_without_rewriting_history(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _routing_harness(root, conversation_persona="alt")
            umo = "onebot:FriendMessage:legacy-warning"
            legacy = {
                "id": "legacy-id",
                "schema_version": 1,
                "code": "persona.route.passive_primary_fallback",
                "channel": "passive",
                "window_key": umo,
                "requested_persona_id": "retained-persona",
                "last_ts": 1,
                "count": 4,
            }
            plugin._data_default["persona_routing_warnings"] = {
                "schema_version": 1,
                "items": [legacy],
            }

            token, persona_id = await plugin._activate_persona_for_event_context(_event(umo))
            plugin._deactivate_persona_for_event(token)

            self.assertEqual("alt", persona_id)
            self.assertIs(legacy, plugin._data_default["persona_routing_warnings"]["items"][0])
            self.assertEqual("retained-persona", legacy["requested_persona_id"])
            self.assertEqual(4, legacy["count"])
            self.assertEqual("resolved", legacy["status"])
            self.assertEqual(2, legacy["schema_version"])

    async def test_reopening_legacy_warning_retains_original_history_id(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _routing_harness(root, conversation_persona="disabled")
            umo = "onebot:FriendMessage:legacy-reopen"
            legacy = {
                "id": "legacy-history-id",
                "schema_version": 1,
                "code": "persona.route.passive_primary_fallback",
                "channel": "passive",
                "window_key": umo,
                "reason_code": "persona_not_enabled",
                "last_ts": 1,
                "count": 2,
            }
            plugin._data_default["persona_routing_warnings"] = {
                "schema_version": 1,
                "items": [legacy],
            }
            await plugin._record_persona_routing_warning(
                code=legacy["code"],
                channel="passive",
                disposition="fallback",
                reason_code=legacy["reason_code"],
                window_key=umo,
                requested_persona_id="disabled",
                resolved_persona_id="main",
                active_persona_id="main",
            )
            self.assertEqual("legacy-history-id", legacy["id"])
            self.assertEqual(1, legacy["count"])
            self.assertEqual(3, legacy["lifetime_count"])

    async def test_stale_warning_recurrence_starts_fresh_current_episode(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _routing_harness(root, conversation_persona="disabled")
            umo = "onebot:FriendMessage:stale-recurrence"
            await plugin._record_persona_routing_warning(
                code="persona.route.passive_primary_fallback",
                channel="passive",
                disposition="fallback",
                reason_code="persona_not_enabled",
                window_key=umo,
                requested_persona_id="disabled",
                resolved_persona_id="main",
                active_persona_id="main",
            )
            warning = plugin._data_default["persona_routing_warnings"]["items"][0]
            warning["last_ts"] -= 2 * 60 * 60 + 1
            warning["first_ts"] = warning["last_ts"]
            warning["count"] = 7
            warning["lifetime_count"] = 7

            await plugin._record_persona_routing_warning(
                code="persona.route.passive_primary_fallback",
                channel="passive",
                disposition="fallback",
                reason_code="persona_not_enabled",
                window_key=umo,
                requested_persona_id="disabled",
                resolved_persona_id="main",
                active_persona_id="main",
            )

            self.assertEqual("active", warning["status"])
            self.assertEqual(1, warning["count"])
            self.assertEqual(8, warning["lifetime_count"])
            self.assertEqual(warning["last_ts"], warning["first_ts"])

    async def test_malformed_legacy_warning_counters_do_not_break_recording(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _routing_harness(root, conversation_persona="disabled")
            umo = "onebot:FriendMessage:malformed-legacy"
            warning = {
                "id": "legacy-malformed",
                "schema_version": 1,
                "code": "persona.route.passive_primary_fallback",
                "channel": "passive",
                "window_key": umo,
                "reason_code": "persona_not_enabled",
                "last_ts": "not-a-timestamp",
                "count": "not-a-count",
                "lifetime_count": None,
            }
            plugin._data_default["persona_routing_warnings"] = {
                "schema_version": 1,
                "items": [warning],
            }
            plugin._persona_routing_warning_save_marks = {"legacy-malformed": "bad"}
            plugin._persona_routing_warning_log_marks = {"legacy-malformed": "bad"}

            await plugin._record_persona_routing_warning(
                code=warning["code"],
                channel="passive",
                disposition="fallback",
                reason_code=warning["reason_code"],
                window_key=umo,
                requested_persona_id="disabled",
                resolved_persona_id="main",
                active_persona_id="main",
            )

            self.assertEqual(1, warning["count"])
            self.assertEqual(1, warning["lifetime_count"])
            self.assertEqual("active", warning["status"])

    async def test_resolved_warning_state_is_persisted(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _routing_harness(root, conversation_persona="disabled")
            umo = "onebot:FriendMessage:persist"
            token, _ = await plugin._activate_persona_for_event_context(_event(umo))
            plugin._deactivate_persona_for_event(token)
            plugin.context.conversation_manager.persona_id = "alt"
            token, _ = await plugin._activate_persona_for_event_context(_event(umo))
            plugin._deactivate_persona_for_event(token)

            await plugin._flush_scheduled_data_save()

            stored = json.loads(Path(plugin.data_file).read_text(encoding="utf-8"))
            stored_warning = stored["persona_routing_warnings"]["items"][0]
            self.assertEqual("resolved", stored_warning["status"])
            self.assertGreater(stored_warning["resolved_ts"], 0)

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

    async def test_matched_proactive_validation_resolves_same_window_warning(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _routing_harness(root, conversation_persona="alt")
            umo = "onebot:FriendMessage:proactive-recover"

            blocked = await plugin._validate_proactive_persona_delivery(umo, "main")
            self.assertFalse(blocked["ok"])
            warning = plugin._data_default["persona_routing_warnings"]["items"][0]
            self.assertEqual("active", warning["status"])

            plugin.context.conversation_manager.persona_id = "main"
            matched = await plugin._validate_proactive_persona_delivery(umo, "main")

            self.assertTrue(matched["ok"])
            self.assertEqual("matched", matched["action"])
            self.assertEqual("resolved", warning["status"])

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

            plugin.plugin_specific_persona_id = "main"
            await plugin._activate_persona_for_event_context(_event())
            self.assertEqual("resolved", warnings[0]["status"])

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
