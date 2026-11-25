"""Configuration transfers follow the selected Bot, role and explicit window."""

import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from tests.multi_host_support import MultiFixture
from astrbot.api.web import PluginMultiDict
from astrbot_host.config_port_api import ConfigPortWebAPI
from companion_v2.configuration.portability import SCHEMA
from companion_v2.execution.models import TaskError


async def call(handler, *, body=None, query=None):
    async def read():
        return json.dumps(body).encode("utf-8")
    pairs = list(query.items()) if isinstance(query, dict) else list(query or ())
    with patch("astrbot_host.config_port_api.request",
               NS(username="config-admin", query=PluginMultiDict(pairs), body=read)):
        return await handler()


class ConfigPortWebAPITests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await self.start_host()
        self.api = ConfigPortWebAPI(self.controller)

    @staticmethod
    def selection(target="second", *, window_selected=False):
        return {"target_id": target, "role_id": "role-a",
                "bot_id": "bot-b" if target == "second" else "bot-a",
                "window_selected": window_selected}

    async def export(self, target="second", *, window_selected=False):
        selected = self.selection(target, window_selected=window_selected)
        selected["window_selected"] = str(window_selected).lower()
        return await call(self.api.export, query=selected)

    async def import_sections(self, sections, *, window_selected=False):
        return await call(self.api.import_values, body={
            **self.selection(window_selected=window_selected),
            "document": {"schema": SCHEMA, "sections": {
                name: {"settings": settings} for name, settings in sections.items()}}})

    async def test_transfer_isolated_between_accounts_with_the_same_role_id(self):
        before = await self.export("first")
        self.assertEqual(before["status"], "ok", before)
        self.assertTrue(before["data"]["sections"]["group_participation"]["settings"]
                        ["enable_group_repeat_follow"])
        saved = await self.import_sections({"group_participation": {"enable_group_repeat_follow": False}})
        self.assertEqual(saved["status"], "ok", saved)
        self.assertEqual(saved["data"]["applied"], {"group_participation": ["enable_group_repeat_follow"]})
        first, second = await self.export("first"), await self.export("second")
        self.assertTrue(first["data"]["sections"]["group_participation"]["settings"]
                        ["enable_group_repeat_follow"])
        self.assertFalse(second["data"]["sections"]["group_participation"]["settings"]
                         ["enable_group_repeat_follow"])

    async def test_role_only_export_does_not_include_representative_window_values(self):
        exported = await self.export()
        self.assertEqual(exported["status"], "ok", exported)
        document = exported["data"]
        self.assertIn("group_participation", document["sections"])
        self.assertIn("reaction", document["sections"])
        for section in ("recall", "screen", "life"):
            self.assertNotIn(section, document["sections"])
            self.assertEqual(document["unavailable"][section], "configuration_port_window_required")

    async def test_window_import_requires_selection_and_keeps_other_sections(self):
        before = await self.export(window_selected=True)
        self.assertTrue(before["data"]["sections"]["recall"]["settings"]["transcribe_enabled"])
        result = await self.import_sections({"recall": {"transcribe_enabled": False},
                                            "group_participation": {"enable_group_repeat_follow": False}})
        self.assertEqual(result["data"]["failed"], {"recall": "configuration_port_window_required"})
        self.assertIn("group_participation", result["data"]["applied"])
        unchanged = await self.export(window_selected=True)
        self.assertTrue(unchanged["data"]["sections"]["recall"]["settings"]["transcribe_enabled"])
        saved = await self.import_sections({"recall": {"transcribe_enabled": False}}, window_selected=True)
        self.assertEqual(saved["data"]["applied"], {"recall": ["transcribe_enabled"]})
        changed = await self.export(window_selected=True)
        self.assertFalse(changed["data"]["sections"]["recall"]["settings"]["transcribe_enabled"])
        other = await self.export("first", window_selected=True)
        self.assertTrue(other["data"]["sections"]["recall"]["settings"]["transcribe_enabled"])

    async def test_missing_or_changed_selection_is_rejected(self):
        missing = await call(self.api.export)
        self.assertEqual(missing["message"], "configuration_port_selection_required")
        selection = {**self.selection(), "window_selected": "false"}
        for unexpected in (("target_id", "first"), ("extra", "value")):
            response = await call(self.api.export, query=[*selection.items(), unexpected])
            self.assertEqual(response["message"], "configuration_port_selection_required", response)
        for changed in ({"bot_id": "bot-a"}, {"role_id": "removed-role"}):
            selection = {**self.selection(), **changed, "window_selected": "false"}
            response = await call(self.api.export, query=selection)
            self.assertEqual(response["message"], "configuration_target_changed", response)
        response = await call(self.api.import_values, body={
            **self.selection(), "bot_id": "bot-a", "document": {"schema": SCHEMA, "sections": {}}})
        self.assertEqual(response["message"], "configuration_target_changed", response)

    async def test_changed_runtime_role_binding_is_not_replaced_by_another_context(self):
        with patch.object(self.controller.authority, "context", side_effect=TaskError(
                "host_persona_binding_changed", "the selected role changed")):
            response = await self.export()
        self.assertEqual(response["message"], "host_persona_binding_changed", response)

    def test_role_section_with_window_field_is_not_treated_as_role_only(self):
        status = {"scope": "role", "fields": {"value": {"write_layer": "window"}}}
        self.assertTrue(self.api._requires_window(status, {"value": True}))


if __name__ == "__main__":
    unittest.main()
