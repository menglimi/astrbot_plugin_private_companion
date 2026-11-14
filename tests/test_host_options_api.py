"""Dashboard contract for host-wide voice and received-poke defaults."""

import asyncio
from decimal import Decimal
import atexit
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

# AstrBot resolves its data root at import time; host API tests keep that root isolated.
_previous_astrbot_root = os.environ.get("ASTRBOT_ROOT")
_astrbot_test_root = TemporaryDirectory(prefix="companion-v2-host-options-")
os.environ["ASTRBOT_ROOT"] = _astrbot_test_root.name


def _cleanup_astrbot_test_root():
    _astrbot_test_root.cleanup()
    if _previous_astrbot_root is None:
        os.environ.pop("ASTRBOT_ROOT", None)
    else:
        os.environ["ASTRBOT_ROOT"] = _previous_astrbot_root


atexit.register(_cleanup_astrbot_test_root)

from astrbot_host.service_balance import configured_balance_port
from astrbot_host.host_options_api import HostOptionsWebAPI, PREFIX as HOST_OPTIONS_PREFIX
from companion_v2.conversation.forwards.models import ForwardIntakePolicy
from companion_v2.conversation.pokes import PokePolicy


class HostOptionsSchemaTests(unittest.TestCase):
    def test_migrated_host_defaults_have_one_dashboard_entry(self):
        root = Path(__file__).resolve().parents[1]
        schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8"))
        voice = schema["voice_reply"]["items"]
        poke = schema["poke"]["items"]

        self.assertTrue(all(voice[name]["invisible"] for name in (
            "enabled", "probability", "private_probability", "group_probability")))
        self.assertTrue(schema["poke"]["invisible"])
        self.assertEqual(poke["back_times"]["default"], 1)
        self.assertFalse(poke["model_reply_enabled"]["default"])
        self.assertEqual(poke["model_reply_probability"]["default"], 0.3)
        self.assertTrue(schema["multi_host"]["items"]["group_delivery_mode"]["invisible"])
        self.assertTrue(schema["service_balance"]["invisible"])
        self.assertTrue(schema["service_balance"]["items"]["api_key"]["invisible"])
        runtime = schema["runtime"]["items"]
        self.assertTrue(all(runtime[name]["invisible"] for name in (
            "forward_max_forwards", "forward_max_nodes", "forward_max_depth", "forward_parse_nested")))
        self.assertEqual(runtime["forward_max_nodes"]["max"], 300)


class HostOptionsWebAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = {"voice_reply": {"enabled": False}, "poke": {}, "runtime": {}}
        self.saved = []
        self.refreshed = 0
        self.multi_mode = False
        self.policy = ForwardIntakePolicy()

        def save(candidate):
            self.saved.append(candidate)
            self.config.clear()
            self.config.update(candidate)

        def refresh():
            self.refreshed += 1
            runtime = self.config.get("runtime", {})
            self.policy = ForwardIntakePolicy(
                max_forwards=runtime.get("forward_max_forwards", 4),
                max_nodes=runtime.get("forward_max_nodes", 32),
                max_depth=runtime.get("forward_max_depth", 4),
                parse_nested=runtime.get("forward_parse_nested", True),
            )
            self.controller.settings = (NS(common=NS(forward_intake=self.policy)) if self.multi_mode
                                        else NS(forward_intake=self.policy))

        self.controller = NS(
            config=self.config,
            runtime=NS(installation_id="host-options-installation"),
            _control_lock=asyncio.Lock(),
            _terminated=False,
            now=lambda: None,
            save_plugin_config=save,
            refresh_settings=refresh,
            settings_error=None,
            poke_policy=PokePolicy(),
            settings=NS(forward_intake=self.policy),
            session=NS(bridge=NS(forward_intake=self.policy)),
            last_session=None,
        )
        self.api = HostOptionsWebAPI(self.controller)

    async def call(self, name, body=None, *, username="administrator", query=None):
        payload = json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
        request = NS(username=username, query=query or {}, body=AsyncMock(return_value=payload))
        with patch("astrbot_host.host_options_api.request", request), \
             patch("astrbot_host.host_options_api.dashboard_operator", return_value=object()):
            return await getattr(self.api, name)()

    async def snapshot(self):
        response = await self.call("status")
        self.assertEqual(response["status"], "ok", response)
        return response["data"]

    async def test_astrbot_balance_binding_preserves_manual_secrets_and_hides_host_secrets(self):
        from tests.test_service_balance_provider import context
        from tests.test_service_balance_script import SCRIPT
        self.controller.context = context()
        self.config["service_balance"] = {"advanced_base_url":"https://manual.example.test",
            "advanced_api_key":"manual-secret", "advanced_proxy_url":"http://localhost:1081"}
        current = await self.snapshot()
        response = await self.call("balance", {"edit_token":current["edit_tokens"]["balance"],
            "changes":{"advanced_enabled":True, "advanced_from_astrbot":True,
                       "advanced_provider_id":"", "advanced_script":SCRIPT}})
        self.assertEqual(response["status"], "ok", response)
        settings = self.config["service_balance"]
        self.assertEqual(settings["advanced_api_key"], "manual-secret")
        self.assertEqual(settings["advanced_base_url"], "https://manual.example.test")
        for secret in ("host-key-a", "host-key-b", "host-proxy-secret", "manual-secret"):
            self.assertNotIn(secret, json.dumps(response))
            self.assertNotIn(secret, json.dumps(self.saved[-1]) if secret != "manual-secret" else "")
        self.assertEqual(response["data"]["service_balance"]["astrbot_default_provider_id"], "source-a")
        current = await self.snapshot()
        response = await self.call("balance", {"edit_token":current["edit_tokens"]["balance"],
            "changes":{"advanced_enabled":True, "advanced_from_astrbot":False}})
        self.assertEqual(response["status"], "ok", response)
        self.assertEqual(self.config["service_balance"]["advanced_api_key"], "manual-secret")

    async def test_astrbot_draft_query_uses_selected_provider_without_saving(self):
        from tests.test_service_balance_provider import context
        from tests.test_service_balance_script import SCRIPT
        from datetime import datetime, timezone
        from companion_v2.proactive.balance_events import BalanceObservation
        self.controller.context = context()
        seen = []
        async def fetch(port):
            seen.append((port.base_url, port.api_key, port.proxy_url))
            return BalanceObservation(Decimal("12.3"), None, datetime.now(timezone.utc))
        current = await self.snapshot()
        with patch("astrbot_host.service_balance_script.ScriptServiceBalancePort.fetch", fetch):
            response = await self.call("balance_test", {"edit_token":current["edit_tokens"]["balance"],
                "changes":{"advanced_enabled":True, "advanced_from_astrbot":True,
                           "advanced_provider_id":"source-b", "advanced_script":SCRIPT}})
        self.assertEqual(response["status"], "ok", response)
        self.assertEqual(response["data"]["amount"], "12.30")
        self.assertEqual(seen, [("https://b.example.test", "host-key-b", "")])
        self.assertEqual(self.saved, [])
        self.assertNotIn("host-key-b", json.dumps(response))

    async def test_astrbot_binding_rejects_unknown_source_and_mixed_manual_edits(self):
        from tests.test_service_balance_provider import context
        from tests.test_service_balance_script import SCRIPT
        self.controller.context = context()
        for fields in ({"advanced_provider_id":"removed"}, {"advanced_api_key":"manual-edit"},
                       {"advanced_base_url":"https://override.example.test"},
                       {"advanced_proxy_url":"http://localhost:1234"},
                       {"advanced_from_astrbot":"true"}):
            current = await self.snapshot()
            response = await self.call("balance", {"edit_token":current["edit_tokens"]["balance"],
                "changes":{"advanced_enabled":True, "advanced_from_astrbot":True,
                           "advanced_script":SCRIPT, **fields}})
            self.assertNotEqual(response["status"], "ok", response)
        self.assertEqual(self.saved, [])

    async def test_dashboard_identity_is_required_before_writing(self):
        response = await self.call("poke", {"edit_token": "x" * 64, "changes": {"enabled": True}},
                                   username="api_key:fixture")

        self.assertEqual(response["message"], "dashboard_identity_required")
        self.assertEqual(self.saved, [])

    async def test_edit_token_and_field_allowlist_protect_host_settings(self):
        current = await self.snapshot()
        stale = await self.call("voice", {"edit_token": "0" * 64, "changes": {"enabled": True}})
        unknown = await self.call("voice", {"edit_token": current["edit_tokens"]["voice"],
                                             "changes": {"provider_id": "unexpected"}})

        self.assertEqual(stale["message"], "host_options_conflict")
        self.assertEqual(unknown["message"], "host_options_request_invalid")
        self.assertEqual(self.saved, [])

    async def test_voice_defaults_save_to_plugin_config_and_report_restart(self):
        current = await self.snapshot()
        response = await self.call("voice", {
            "edit_token": current["edit_tokens"]["voice"],
            "changes": {"enabled": True, "private_probability": 0.35},
        })

        self.assertEqual(response["status"], "ok", response)
        self.assertEqual(response["data"]["apply_mode"], "restart_host")
        self.assertTrue(self.config["voice_reply"]["enabled"])
        self.assertEqual(self.config["voice_reply"]["private_probability"], 0.35)
        self.assertEqual(len(self.saved), 1)
        self.assertEqual(self.refreshed, 1)

    async def test_received_poke_policy_is_updated_immediately(self):
        current = await self.snapshot()
        response = await self.call("poke", {
            "edit_token": current["edit_tokens"]["poke"],
            "changes": {"enabled": True, "trigger_probability": 0.4, "replies": ["我在"], "back_times": 3,
                        "model_reply_enabled": True, "model_reply_probability": 0.7},
        })

        self.assertEqual(response["status"], "ok", response)
        self.assertEqual(response["data"]["apply_mode"], "immediate")
        self.assertTrue(self.controller.poke_policy.enabled)
        self.assertEqual(self.controller.poke_policy.trigger_probability, 0.4)
        self.assertEqual(self.controller.poke_policy.back_times, 3)
        self.assertTrue(self.controller.poke_policy.model_reply_enabled)
        self.assertEqual(self.controller.poke_policy.model_reply_probability, 0.7)
        self.assertEqual(self.controller.poke_policy.replies, ("我在",))
        self.assertEqual(self.refreshed, 0)

    async def test_balance_source_saves_secret_without_returning_it_and_updates_live_contact(self):
        from astrbot_host.service_balance import configured_balance_port

        from unittest.mock import Mock
        invalidations = Mock()
        self.controller.session = NS(application=NS(contact_schedule=NS(balance_events=NS(port=None)),
            keyword_materials=NS(invalidate=invalidations)))
        current = await self.snapshot()
        response = await self.call("balance", {
            "edit_token": current["edit_tokens"]["balance"],
            "changes": {"url": "https://newapi.example.com", "api_key": "fixture-secret-token"},
        })

        self.assertEqual(response["status"], "ok", response)
        self.assertEqual(response["data"]["apply_mode"], "immediate")
        self.assertTrue(response["data"]["service_balance"]["has_api_key"])
        self.assertNotIn("fixture-secret-token", json.dumps(response, ensure_ascii=False))
        self.assertEqual(self.config["service_balance"]["api_key"], "fixture-secret-token")
        application = self.controller.session.application
        self.assertEqual(application.contact_schedule.balance_events.port.provider, "newapi")
        self.assertIs(application.balance_port, application.contact_schedule.balance_events.port)
        invalidations.assert_called_once_with("service.balance")
        self.assertEqual(configured_balance_port(self.config).url, "https://newapi.example.com/api/user/self")

        current = response["data"]
        preserved = await self.call("balance", {
            "edit_token": current["edit_tokens"]["balance"],
            "changes": {"url": "https://newapi.example.com", "api_key": "",
                        "user_id": "2468", "value_divisor": "250000"},
        })
        self.assertEqual(preserved["status"], "ok", preserved)
        self.assertEqual(self.config["service_balance"]["api_key"], "fixture-secret-token")
        self.assertEqual(self.config["service_balance"]["user_id"], "2468")
        self.assertNotIn("fixture-secret-token", json.dumps(preserved))

        cleared = await self.call("balance", {
            "edit_token": preserved["data"]["edit_tokens"]["balance"],
            "changes": {"clear_configuration": True},
        })
        self.assertEqual(cleared["status"], "ok", cleared)
        self.assertNotIn("service_balance", self.config)
        self.assertIsNone(self.controller.session.application.contact_schedule.balance_events.port)
        self.assertIsNone(application.balance_port)
        self.assertEqual(invalidations.call_count, 3)
        self.assertTrue(all(call.args == ("service.balance",) for call in invalidations.call_args_list))

    async def test_balance_source_rejects_stale_tokens_and_non_https_urls(self):
        current = await self.snapshot()
        stale = await self.call("balance", {"edit_token": "0" * 64,
            "changes": {"url": "https://newapi.example.com", "api_key": "fixture-token"}})
        invalid = await self.call("balance", {"edit_token": current["edit_tokens"]["balance"],
            "changes": {"url": "http://newapi.example.com", "api_key": "fixture-token"}})

        self.assertEqual(stale["message"], "host_options_conflict")
        self.assertEqual(invalid["message"], "host_options_invalid")
        self.assertEqual(self.saved, [])

    def test_balance_status_and_save_share_a_single_host_options_route(self):
        routes = self.api.routes()
        balance_methods = {method for path, _handler, methods, _description in routes
                           if path == HOST_OPTIONS_PREFIX + "/balance" for method in methods}
        test_methods = {method for path, _handler, methods, _description in routes
                        if path == HOST_OPTIONS_PREFIX + "/balance/test" for method in methods}
        self.assertEqual(balance_methods, {"GET", "POST"})
        self.assertEqual(test_methods, {"POST"})

    async def test_balance_test_checks_an_unsaved_source_without_persisting_or_echoing_its_token(self):
        from datetime import datetime, timezone
        from decimal import Decimal

        current = await self.snapshot()
        port = NS(provider="newapi", provider_label="NewAPI", currency_label="额度",
            fetch=AsyncMock(return_value=NS(balance=Decimal("12.5"),
                observed_at=datetime(2026, 10, 5, tzinfo=timezone.utc))))

        def select_port(config, *, context=None):
            return port if config.get("service_balance", {}).get("url") == "https://newapi.example.com" else None

        with patch.dict("os.environ", {}, clear=True), \
             patch("astrbot_host.host_options_api.configured_balance_port", side_effect=select_port):
            response = await self.call("balance_test", {
                "edit_token": current["edit_tokens"]["balance"],
                "changes": {"url": "https://newapi.example.com", "api_key": "fixture-secret-token",
                            "user_id": "1234", "value_divisor": "1000000"},
            })

        self.assertEqual(response["status"], "ok", response)
        self.assertEqual(response["data"]["amount"], "12.50")
        self.assertEqual(response["data"]["currency_label"], "额度")
        self.assertTrue(response["data"]["available"])
        self.assertNotIn("fixture-secret-token", json.dumps(response, ensure_ascii=False))
        self.assertEqual(self.saved, [])
        self.assertNotIn("service_balance", self.config)
        port.fetch.assert_awaited_once_with()

    async def test_advanced_mode_is_exclusive_retains_legacy_and_redacts_credentials(self):
        from tests.test_service_balance_script import SCRIPT
        self.config["service_balance"] = {"url": "https://old.example.test", "api_key": "old-secret",
                                          "user_id": "123", "value_divisor": "500000"}
        current = await self.snapshot()
        enabled = await self.call("balance", {"edit_token": current["edit_tokens"]["balance"], "changes": {
            "advanced_enabled": True, "advanced_base_url": "https://new.example.test",
            "advanced_api_key": "advanced-secret", "advanced_script": SCRIPT,
            "advanced_access_token": "access-secret", "advanced_user_id": "456",
            "advanced_proxy_url": "socks5://name:proxy-secret@localhost:1234",
            "url": "http://ignored.invalid", "api_key": "ignored-secret"}})
        self.assertEqual(enabled["status"], "ok", enabled)
        self.assertEqual(enabled["data"]["service_balance"]["provider"], "script")
        self.assertTrue(enabled["data"]["service_balance"]["has_advanced_api_key"])
        self.assertTrue(enabled["data"]["service_balance"]["has_advanced_proxy_url"])
        for secret in ("old-secret", "advanced-secret", "access-secret", "proxy-secret", "ignored-secret"):
            self.assertNotIn(secret, json.dumps(enabled))
        self.assertEqual(self.config["service_balance"]["url"], "https://old.example.test")
        self.assertEqual(self.config["service_balance"]["api_key"], "old-secret")
        disabled = await self.call("balance", {"edit_token": enabled["data"]["edit_tokens"]["balance"],
            "changes": {"advanced_enabled": False, "advanced_script": "ignored invalid script"}})
        self.assertEqual(disabled["status"], "ok", disabled)
        self.assertEqual(configured_balance_port(self.config).provider, "newapi")
        self.assertEqual(self.config["service_balance"]["advanced_script"], SCRIPT)
        reenabled = await self.call("balance", {"edit_token": disabled["data"]["edit_tokens"]["balance"],
            "changes": {"advanced_enabled": True}})
        self.assertEqual(reenabled["status"], "ok", reenabled)
        self.assertEqual(configured_balance_port(self.config).provider, "script")
        self.assertEqual(self.config["service_balance"]["advanced_api_key"], "advanced-secret")
        self.assertEqual(self.config["service_balance"]["advanced_access_token"], "access-secret")
        self.assertEqual(self.config["service_balance"]["advanced_user_id"], "456")

    async def test_advanced_draft_test_uses_shared_validation_without_saving(self):
        from tests.test_service_balance_script import SCRIPT
        from astrbot_host.service_balance_script import ScriptServiceBalancePort
        from companion_v2.proactive.balance_events import BalanceObservation
        from datetime import datetime, timezone
        current = await self.snapshot()
        observation = BalanceObservation(Decimal("12.3"), Decimal("16.8"), datetime.now(timezone.utc),
                                         unit="CNY", used=Decimal("4.5"), plan_name="Synthetic plan")
        with patch.object(ScriptServiceBalancePort, "fetch", AsyncMock(return_value=observation)):
            tested = await self.call("balance_test", {"edit_token": current["edit_tokens"]["balance"],
                "changes": {"advanced_enabled": True, "advanced_script": SCRIPT,
                            "advanced_base_url": "https://new.example.test", "advanced_api_key": "draft-secret"}})
        self.assertEqual(tested["status"], "ok", tested)
        self.assertEqual(tested["data"]["currency_label"], "CNY")
        self.assertEqual(tested["data"]["amount"], "12.30")
        self.assertEqual(tested["data"]["plan_name"], "Synthetic plan")
        self.assertNotIn("draft-secret", json.dumps(tested))
        self.assertNotIn("service_balance", self.config)
        self.assertEqual(self.saved, [])

    async def test_advanced_long_script_clear_secrets_and_conflict(self):
        from tests.test_service_balance_script import SCRIPT
        current = await self.snapshot()
        saved = await self.call("balance", {"edit_token": current["edit_tokens"]["balance"], "changes": {
            "advanced_enabled": True, "advanced_script": SCRIPT + " " * 9000,
            "advanced_api_key": "advanced-secret", "advanced_proxy_url": "http://localhost:1234"}})
        self.assertEqual(saved["status"], "ok", saved)
        stale = await self.call("balance_test", {"edit_token": current["edit_tokens"]["balance"],
            "changes": {"use_current": True}})
        self.assertEqual(stale["message"], "host_options_conflict")
        cleared = await self.call("balance", {"edit_token": saved["data"]["edit_tokens"]["balance"],
            "changes": {"advanced_enabled": True, "clear_advanced_api_key": True,
                        "clear_advanced_proxy_url": True}})
        self.assertEqual(cleared["status"], "ok", cleared)
        self.assertFalse(cleared["data"]["service_balance"]["has_advanced_api_key"])
        self.assertFalse(cleared["data"]["service_balance"]["has_advanced_proxy_url"])
        self.assertNotEqual(saved["data"]["edit_tokens"]["balance"], cleared["data"]["edit_tokens"]["balance"])

    async def test_advanced_invalid_account_has_no_amount_and_does_not_fall_back(self):
        from tests.test_service_balance_script import SCRIPT
        from astrbot_host.service_balance_script import BalanceQueryError, ScriptServiceBalancePort
        current = await self.snapshot()
        changes = {"advanced_enabled": True, "advanced_script": SCRIPT}
        with patch.object(ScriptServiceBalancePort, "fetch", AsyncMock(side_effect=
                BalanceQueryError("account_invalid", "Synthetic account expired"))):
            tested = await self.call("balance_test", {"edit_token": current["edit_tokens"]["balance"], "changes": changes})
        self.assertEqual(tested["status"], "ok", tested)
        self.assertFalse(tested["data"]["available"])
        self.assertIsNone(tested["data"]["amount"])
        self.assertEqual(tested["data"]["state"], "account_invalid")
        invalid = await self.call("balance", {"edit_token": current["edit_tokens"]["balance"],
            "changes": {"advanced_enabled": True, "advanced_script": ""}})
        self.assertNotEqual(invalid["status"], "ok")
        self.assertNotIn("service_balance", self.config)

    async def test_dashboard_balance_status_prefers_saved_source_and_never_echoes_environment_secrets(self):
        self.config["service_balance"] = {"url": "https://dashboard.example.com", "api_key": "saved-secret",
                                           "user_id": "1234", "value_divisor": "1000000"}
        with patch.dict("os.environ", {
            "COMPANION_V2_BALANCE_PROVIDER": "newapi",
            "COMPANION_V2_BALANCE_URL": "https://environment.example.com",
            "COMPANION_V2_BALANCE_API_KEY": "environment-secret",
        }, clear=True):
            current = await self.snapshot()

        self.assertEqual(current["service_balance"]["source"], "plugin")
        self.assertEqual(current["service_balance"]["url"], "https://dashboard.example.com")
        self.assertEqual(current["service_balance"]["user_id"], "1234")
        self.assertEqual(current["service_balance"]["value_divisor"], "1000000")
        self.assertNotIn("saved-secret", json.dumps(current))
        self.assertNotIn("environment-secret", json.dumps(current))

        self.config.pop("service_balance")
        with patch.dict("os.environ", {
            "COMPANION_V2_BALANCE_PROVIDER": "newapi",
            "COMPANION_V2_BALANCE_URL": "https://environment.example.com",
            "COMPANION_V2_BALANCE_API_KEY": "environment-secret",
        }, clear=True):
            environment = await self.snapshot()
        self.assertEqual(environment["service_balance"]["source"], "environment")
        self.assertEqual(environment["service_balance"]["url"], "")
        self.assertNotIn("environment-secret", json.dumps(environment))

    async def test_query_overrides_and_invalid_values_are_refused(self):
        current = await self.snapshot()
        query = await self.call("voice", {"edit_token": current["edit_tokens"]["voice"],
                                           "changes": {"probability": 0.5}}, query={"probability": "1"})
        invalid = await self.call("poke", {"edit_token": current["edit_tokens"]["poke"],
                                            "changes": {"trigger_probability": 1.5}})
        invalid_back_count = await self.call("poke", {"edit_token": current["edit_tokens"]["poke"],
                                                        "changes": {"back_times": 6}})

        self.assertEqual(query["message"], "host_options_request_invalid")
        self.assertEqual(invalid["message"], "host_options_invalid")
        self.assertEqual(invalid_back_count["message"], "host_options_invalid")
        self.assertEqual(self.saved, [])

    async def test_forward_intake_limits_persist_and_apply_to_the_live_bridge(self):
        self.config["model"] = {"provider_id": "fixture-provider"}
        self.config["multi_host"] = {
            "enabled": True,
            "installation_id": "main",
            "targets": [{"target_id": "friend-1", "platform_id": "aiocqhttp", "account_id": "10001",
                         "bot_id": "fixture-bot", "participant_id": "10002"}],
            "personas": [{"bot_id": "fixture-bot", "persona_id": "default", "role_id": "fixture-role"}],
        }
        self.multi_mode = True
        current = await self.snapshot()
        response = await self.call("forward_intake", {
            "edit_token": current["edit_tokens"]["forward_intake"],
            "changes": {"max_forwards": 2, "max_nodes": 8, "max_depth": 2},
        })

        self.assertEqual(response["status"], "ok", response)
        self.assertEqual(response["data"]["apply_mode"], "immediate_next_input")
        self.assertEqual(self.config["runtime"], {
            "forward_max_forwards": 2, "forward_max_nodes": 8, "forward_max_depth": 2,
        })
        self.assertEqual(response["data"]["forward_intake"], {
            "max_forwards": 2, "max_nodes": 8, "max_depth": 2, "parse_nested": True,
        })
        self.assertEqual(self.config["model"]["provider_id"], "fixture-provider")
        self.assertEqual(self.config["multi_host"]["targets"][0]["target_id"], "friend-1")
        self.assertEqual(self.controller.session.bridge.forward_intake, self.policy)
        self.assertEqual(self.refreshed, 1)

    async def test_nested_forward_switch_persists_and_updates_the_live_bridge(self):
        current = await self.snapshot()
        response = await self.call("forward_intake", {
            "edit_token": current["edit_tokens"]["forward_intake"],
            "changes": {"max_nodes": 300, "parse_nested": False},
        })

        self.assertEqual(response["status"], "ok", response)
        self.assertEqual(self.config["runtime"], {"forward_max_nodes": 300, "forward_parse_nested": False})
        self.assertEqual(response["data"]["forward_intake"]["max_nodes"], 300)
        self.assertFalse(self.controller.session.bridge.forward_intake.parse_nested)
        self.assertEqual(self.controller.session.bridge.forward_intake.max_nodes, 300)

    async def test_forward_intake_limits_apply_to_a_staging_bridge(self):
        self.config["runtime"] = {"foreground_slots": 3}
        current = await self.snapshot()
        response = await self.call("forward_intake", {
            "edit_token": current["edit_tokens"]["forward_intake"],
            "changes": {"max_forwards": 1, "max_nodes": 6, "max_depth": 3},
        })

        self.assertEqual(response["status"], "ok", response)
        self.assertEqual(self.config["runtime"], {
            "foreground_slots": 3, "forward_max_forwards": 1,
            "forward_max_nodes": 6, "forward_max_depth": 3,
        })
        self.assertEqual(self.controller.settings.forward_intake, self.policy)
        self.assertEqual(self.controller.session.bridge.forward_intake, self.policy)

    async def test_forward_intake_rejects_nonintegers_and_structural_overflow(self):
        current = await self.snapshot()
        for changes in ({"max_forwards": 5}, {"max_nodes": 301}, {"max_nodes": True},
                        {"max_depth": 0}, {"parse_nested": 1}):
            response = await self.call("forward_intake", {
                "edit_token": current["edit_tokens"]["forward_intake"], "changes": changes,
            })
            self.assertEqual(response["message"], "host_options_invalid", changes)
        self.assertEqual(self.saved, [])


if __name__ == "__main__":
    unittest.main()
