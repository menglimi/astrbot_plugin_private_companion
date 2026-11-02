"""Installation registries and per-conversation model budgets are independent."""

from dataclasses import replace
import json
from types import SimpleNamespace
import unittest

from companion_v2.conversation.agenda_tools import TOOL_FIELDS
from companion_v2.conversation.tool_loop import LocalRoleTools, specification
from companion_v2.execution.models import TaskError
from companion_v2.model_gateway.models import MAX_TOOL_CATALOG
from tests.test_conversation_messages import MessageFixture
from tests.test_role_tool_loop import ToolLoopFixture


class RegisteredTools:
    def __init__(self, messages, count=64):
        self.messages = messages
        self.catalog = tuple(specification(f"content.fixture.{index}", ()) for index in range(count))
        self.actions = frozenset(tool.action for tool in self.catalog)
        self.providers = {tool.action: SimpleNamespace(descriptor=SimpleNamespace(tool=tool))
                          for tool in self.catalog}
        self.allowed = ()

    def catalog_for(self, context):
        return self.allowed

    def catalog_with_diagnostics(self, context):
        return self.allowed, ()


class ToolCatalogTests(MessageFixture):
    def tool_registry(self):
        external = RegisteredTools(self.messages)
        return LocalRoleTools(self.messages, external=external), external

    def test_registered_but_unavailable_extensions_do_not_block_host_assembly(self):
        registry, external = self.tool_registry()
        self.assertGreater(len(registry.catalog), MAX_TOOL_CATALOG)
        catalog = registry.catalog_for(self.context)
        self.assertEqual({tool.action for tool in catalog}, {*TOOL_FIELDS, "source.read"})
        self.assertTrue(external.actions.isdisjoint(tool.action for tool in catalog))

    def test_context_catalog_preserves_all_authorized_extension_tools(self):
        registry, external = self.tool_registry()
        external.allowed = external.catalog[:7]
        expected = registry.catalog_for(self.context)
        self.assertEqual(expected[-7:], external.allowed)
        self.assertEqual(registry.catalog_for(self.context), expected)
        external.allowed = ()
        self.assertTrue(external.actions.isdisjoint(tool.action for tool in registry.catalog_for(self.context)))

    def test_exact_gateway_budget_is_allowed_and_overflow_is_explicit(self):
        registry, external = self.tool_registry()
        base_count = len(registry.catalog_for(self.context))
        external.allowed = external.catalog[:MAX_TOOL_CATALOG - base_count]
        self.assertEqual(len(registry.catalog_for(self.context)), MAX_TOOL_CATALOG)
        external.allowed = external.catalog[:MAX_TOOL_CATALOG + 1 - base_count]
        with self.assertRaises(TaskError) as failure:
            registry.catalog_for(self.context)
        self.assertEqual(failure.exception.code, "tool_catalog_budget_exceeded")
        self.assertIn(f"{MAX_TOOL_CATALOG + 1} tools", str(failure.exception))
        self.assertEqual(self.runtime.read_all("SELECT * FROM model_calls", ()), ())
        self.assertEqual(self.runtime.read_all("SELECT * FROM conversation_tool_calls", ()), ())
        external.allowed = ()
        self.assertEqual(len(registry.catalog_for(self.context)), base_count)

    def test_reminder_create_declares_its_schedule_shape(self):
        spec = specification("reminder.create", *TOOL_FIELDS["reminder.create"])
        schema = json.loads(spec.parameters_json)
        schedule = schema["properties"]["schedule"]
        self.assertEqual(schedule["type"], "object")
        self.assertEqual(schedule["required"], ["anchor", "timezone_name"])
        self.assertEqual(schedule["properties"]["anchor"]["format"], "date-time")
        self.assertIn({"required": ["schedule", "message"]}, schema["anyOf"])
        self.assertEqual(schema["properties"]["message"]["minLength"], 1)

    def test_agenda_catalog_rechecks_current_feature_authority(self):
        registry, _ = self.tool_registry()
        context = replace(self.context, actor=self.standard,
                          audience=replace(self.context.audience, recipients=(self.standard.subject,)))
        self.assertEqual([tool.action for tool in registry.catalog_for(context)], ["source.read"])
        grant = self.grant(self.standard, context.scope, "memo.list")
        self.assertEqual({tool.action for tool in registry.catalog_for(context)}, {"memo.list", "source.read"})
        self.identity.revoke_grant(grant.grant_id, expected_revision=self.identity.revision,
                                   revoked_at=self.clock.now())
        self.assertEqual([tool.action for tool in registry.catalog_for(context)], ["source.read"])


class ToolCatalogGatewayTests(ToolLoopFixture, unittest.IsolatedAsyncioTestCase):
    async def test_gateway_accepts_complete_catalog_and_rejects_overflow_before_io(self):
        _, run = await self.manual_run()
        tools = tuple(specification(f"test.action.{index}", ()) for index in range(MAX_TOOL_CATALOG))
        session = self.gateway.open_session(run, self.messages.request(run), tools=tools)
        self.assertEqual(session.tools, tools)
        with self.assertRaises(ValueError):
            self.gateway.open_session(run, self.messages.request(run),
                tools=(*tools, specification("test.overflow", ())))
        self.assertEqual(self.runtime.read_all("SELECT * FROM model_calls", ()), ())
