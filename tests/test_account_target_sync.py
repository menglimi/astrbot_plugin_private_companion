"""Account edits synchronize private targets without widening group access."""

from copy import deepcopy
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from tests import astrbot_data_root  # Establish isolation before the SDK import.
from astrbot_host.account_session_api import AccountSessionWebAPI
from astrbot_host.bootstrap_provider import supported_text_provider, text_provider_predicate
from tests.group_host_support import group_config


class AccountTargetSyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = group_config()
        self.accounts = [
            {"platform_id": "qq-main", "account_id": "100", "bot_id": "bot-a"},
            {"platform_id": "qq-second", "account_id": "101", "bot_id": "bot-b"},
        ]
        self.personas = deepcopy(self.config["multi_host"]["personas"])
        self.discovered = {"accounts": self.accounts, "personas": self.personas, "targets": []}

        async def apply(candidate, *, expected_config=None):
            if expected_config is not None and expected_config != self.config:
                raise AssertionError("stale role save")
            self.config.clear()
            self.config.update(candidate)
            return {"saved": True, "requires_reload": False, "requires_initialize": False}

        self.controller = NS(config=self.config, apply_bindings=AsyncMock(side_effect=apply))
        self.api = AccountSessionWebAPI(self.controller)
        self.api._discovered = AsyncMock(return_value=self.discovered)

    async def save(self, **body):
        request = NS(username="admin", query={},
            body=AsyncMock(return_value=json.dumps(body).encode("utf-8")))
        with patch("astrbot_host.account_session_api.request", request):
            return await self.api.save()

    async def add_role(self, **body):
        request = NS(username="admin", query={},
            body=AsyncMock(return_value=json.dumps(body, ensure_ascii=False).encode("utf-8")))
        with patch("astrbot_host.account_session_api.request", request):
            return await self.api.add_role()

    async def test_inline_role_preserves_other_accounts_and_group_routes(self):
        before = deepcopy(self.config["multi_host"])
        self.discovered["personas"].extend([
            {**self.accounts[0], "persona_id": "另一个人格"},
            {**self.accounts[0], "persona_id": "第三个人格"},
        ])
        result = await self.add_role(**self.accounts[0],
            persona_ids=["另一个人格", "第三个人格"], role_id="新角色")
        self.assertEqual(result["status"], "ok", result)
        after = self.config["multi_host"]
        for key in ("targets", "group_targets"):
            self.assertEqual(after[key], before[key])
        self.assertIn(self.accounts[0], after["accounts"])
        self.assertEqual(after["personas"][:-2], before["personas"])
        self.assertEqual(after["personas"][-2:], [
            {"bot_id": "bot-a", "persona_id": "另一个人格", "role_id": "新角色"},
            {"bot_id": "bot-a", "persona_id": "第三个人格", "role_id": "新角色"},
        ])

    async def test_inline_role_refuses_reassignment_of_existing_persona(self):
        before = deepcopy(self.config)
        existing = self.personas[0]
        account = next(item for item in self.accounts if item["bot_id"] == existing["bot_id"])
        self.discovered["personas"].append({**account, "persona_id": "可用人格"})
        result = await self.add_role(**account,
            persona_ids=["可用人格", existing["persona_id"]], role_id="别的角色")
        self.assertEqual(result["data"]["code"], "account_role_persona_in_use")
        self.assertEqual(self.config, before)
        self.controller.apply_bindings.assert_not_awaited()

    async def test_inline_role_can_register_another_connected_bot(self):
        account = {"platform_id": "qq-third", "account_id": "102", "bot_id": "bot-c"}
        self.discovered["accounts"].append(account)
        self.discovered["personas"].append({**account, "persona_id": "新的人格"})
        old_groups = deepcopy(self.config["multi_host"]["group_targets"])
        result = await self.add_role(**account, persona_ids=["新的人格"], role_id="第三个角色")
        self.assertEqual(result["status"], "ok", result)
        section = self.config["multi_host"]
        self.assertIn(account, section["accounts"])
        self.assertEqual(section["group_targets"], old_groups)
        new_targets = [item for item in section["targets"] if item["bot_id"] == "bot-c"]
        self.assertEqual(len(new_targets), 1)
        self.assertEqual({key: new_targets[0][key] for key in account}, account)
        self.assertEqual(new_targets[0]["participant_id"], "*")
        self.assertTrue(new_targets[0]["target_id"].startswith("private-"))

    async def test_inline_role_requires_one_distinct_persona_from_same_bot(self):
        account = self.accounts[0]
        self.discovered["personas"].append({**account, "persona_id": "可用人格"})
        before = deepcopy(self.config)
        for persona_ids, code in (([], "account_config_invalid"),
                                  (["可用人格", "可用人格"], "account_config_invalid"),
                                  (["可用人格", "其他 Bot 人格"], "host_persona_unavailable")):
            with self.subTest(persona_ids=persona_ids):
                result = await self.add_role(**account, persona_ids=persona_ids, role_id="新角色")
                self.assertEqual(result["data"]["code"], code)
                self.assertEqual(self.config, before)
        self.controller.apply_bindings.assert_not_awaited()

    async def test_new_account_gets_private_template_and_preserves_existing_ids_and_groups(self):
        section = self.config["multi_host"]
        section["targets"] = [row for row in section["targets"] if row["bot_id"] == "bot-a"]
        section["group_targets"] = [row for row in section["group_targets"] if row["bot_id"] == "bot-a"]
        section["personas"] = [row for row in section["personas"] if row["bot_id"] == "bot-a"]
        previous_private = deepcopy(section["targets"])
        previous_groups = deepcopy(section["group_targets"])

        result = await self.save(accounts=self.accounts, personas=self.personas, groups=previous_groups)

        self.assertEqual(result["status"], "ok", result)
        current = self.config["multi_host"]
        self.assertEqual(current["targets"][:1], previous_private)
        template = current["targets"][1]
        self.assertEqual((template["platform_id"], template["account_id"],
                          template["bot_id"], template["participant_id"]),
                         ("qq-second", "101", "bot-b", "*"))
        self.assertEqual(current["group_targets"], previous_groups)
        self.assertTrue(all(row["group_id"] != "*" for row in current["group_targets"]))
        self.assertEqual(result["data"]["configuration"]["sessions"], current["targets"])

    async def test_explicit_all_groups_target_is_saved_and_keeps_its_stable_id(self):
        wildcard = {"platform_id": "qq-main", "account_id": "100", "bot_id": "bot-a", "group_id": "*"}

        result = await self.save(accounts=self.accounts, personas=self.personas, groups=[wildcard])

        self.assertEqual(result["status"], "ok", result)
        saved = self.config["multi_host"]["group_targets"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["group_id"], "*")
        self.assertTrue(saved[0]["target_id"].startswith("group-"))
        target_id = saved[0]["target_id"]

        result = await self.save(accounts=self.accounts, personas=self.personas, groups=[wildcard])
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(self.config["multi_host"]["group_targets"][0]["target_id"], target_id)

    async def test_removed_account_drops_old_private_group_and_persona_references(self):
        previous = deepcopy(self.config["multi_host"])

        result = await self.save(accounts=self.accounts[:1], personas=previous["personas"],
            sessions=previous["targets"], groups=previous["group_targets"])

        self.assertEqual(result["status"], "ok", result)
        current = self.config["multi_host"]
        self.assertEqual(current["accounts"], self.accounts[:1])
        self.assertEqual(current["targets"], previous["targets"][:1])
        self.assertEqual(current["group_targets"], previous["group_targets"][:2])
        self.assertTrue(all(row["bot_id"] == "bot-a" for row in current["personas"]))

    async def test_omitted_groups_preserve_exact_groups_and_explicit_empty_groups_remove_them(self):
        groups = deepcopy(self.config["multi_host"]["group_targets"])

        result = await self.save(accounts=self.accounts, personas=self.personas)

        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(self.config["multi_host"]["group_targets"], groups)
        result = await self.save(accounts=self.accounts, personas=self.personas, groups=[])
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(self.config["multi_host"]["group_targets"], [])

    async def test_existing_wildcard_targets_keep_their_ids_on_role_save(self):
        section = self.config["multi_host"]
        section["targets"][0]["participant_id"] = "*"
        section["group_targets"][0]["group_id"] = "*"
        previous = deepcopy(section)

        result = await self.save(accounts=self.accounts, personas=self.personas)

        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(self.config["multi_host"]["targets"], previous["targets"])
        self.assertEqual(self.config["multi_host"]["group_targets"], previous["group_targets"])

    async def test_new_target_reference_to_unselected_account_is_rejected(self):
        before = deepcopy(self.config)
        groups = [*self.config["multi_host"]["group_targets"],
                  {**self.accounts[1], "group_id": "999"}]

        result = await self.save(accounts=self.accounts[:1], personas=self.personas, groups=groups)

        self.assertEqual(result["status"], "error", result)
        self.assertEqual(result["data"]["code"], "account_config_invalid")
        self.assertEqual(self.config, before)
        self.controller.apply_bindings.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()


class SharedModelProviderBootstrapTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        class FakeProvider:
            def meta(self):
                return NS(id="provider-a", name="Shared model")

        class NativeTypes:
            """Mirror the live NativeAdapterTypes shape: a bound method, not a type.

            ``provider_types`` stays declared as on main, so the fixture keeps both
            the provider classes and the live callable checker in sync.
            """

            provider_types = (FakeProvider,)

            def is_text_provider(self, provider):
                return isinstance(provider, self.provider_types)

        self.provider = FakeProvider()
        self.config = group_config()
        self.config.setdefault("model", {})["provider_id"] = ""
        self.controller = NS(
            config=self.config,
            context=NS(get_all_providers=lambda: [self.provider],
                       get_provider_by_id=lambda provider_id: self.provider if provider_id == "provider-a" else None),
            native_types=NativeTypes(),
            settings_error=None,
            save_plugin_config=self.save_config,
            refresh_settings=lambda: None,
            initialize_profile=AsyncMock(return_value=NS(failure=None)),
            status=lambda: {"state": "uninitialized"},
        )
        self.api = AccountSessionWebAPI(self.controller)
        self.saved = 0

    def save_config(self, candidate):
        self.saved += 1
        self.config.clear()
        self.config.update(candidate)

    async def call(self, **body):
        request = NS(username="admin", query={},
            body=AsyncMock(return_value=json.dumps(body, ensure_ascii=False).encode("utf-8")))
        with patch("astrbot_host.account_session_api.request", request):
            return await self.api.save_model_provider()

    def test_blank_config_uses_astrbot_current_provider_automatically(self):
        view = self.api._model_view()
        self.assertEqual(view["provider_id"], "provider-a")
        self.assertEqual(view["provider_source"], "auto")
        self.assertEqual(view["configured_provider_id"], "")
    async def test_shared_provider_can_be_selected_before_role_runtime_exists(self):
        result = await self.call(provider_id="provider-a")
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(self.config["model"]["provider_id"], "provider-a")
        self.assertTrue(result["data"]["saved"])
        self.assertTrue(result["data"]["prepared"])
        self.assertEqual(result["data"]["model"]["provider_id"], "provider-a")
        self.assertIn({"id": "provider-a", "name": "Shared model"}, result["data"]["model"]["providers"])
        self.controller.initialize_profile.assert_awaited_once()

    async def test_save_shared_provider_works_with_callable_text_predicate(self):
        expected_type = text_provider_predicate(self.controller.native_types)
        self.assertTrue(callable(expected_type))
        self.assertFalse(isinstance(expected_type, type))
        self.assertTrue(supported_text_provider(self.provider, expected_type))

        result = await self.call(provider_id="provider-a")

        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(self.saved, 1)
        self.assertEqual(self.config["model"]["provider_id"], "provider-a")
        self.assertTrue(result["data"]["saved"])

    async def test_save_shared_provider_never_returns_account_config_invalid(self):
        before = deepcopy(self.config)
        for body in ({"provider_id": "missing-provider"}, {"provider_id": 123}):
            result = await self.call(**body)
            self.assertEqual(result["status"], "error", result)
            self.assertNotEqual(result["data"]["code"], "account_config_invalid", result)
            self.assertEqual(self.config, before)

        # The id is listed but AstrBot no longer returns the instance; the
        # validation must report an unavailable provider, not an account error.
        self.controller.context = NS(
            get_all_providers=lambda: [self.provider], get_provider_by_id=lambda _provider_id: None)
        result = await self.call(provider_id="provider-a")
        self.assertEqual(result["status"], "error", result)
        self.assertNotEqual(result["data"]["code"], "account_config_invalid", result)
        self.assertEqual(self.config, before)
        self.controller.initialize_profile.assert_not_awaited()

    async def test_unknown_shared_provider_is_rejected_without_overwriting_config(self):
        before = deepcopy(self.config)
        result = await self.call(provider_id="missing-provider")
        self.assertEqual(result["status"], "error", result)
        self.assertEqual(result["data"]["code"], "host_provider_unavailable")
        self.assertEqual(self.config, before)
        self.controller.initialize_profile.assert_not_awaited()
