from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from astrbot_host.mobile_together import MobileTogetherBridge
from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind, ScopeRef
from companion_v2.sources.models import GovernanceState
from companion_v2.storage.runtime import StorageRuntime


class MobileTogetherBridgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="v2-mobile-together-")
        self.addCleanup(temporary.cleanup)
        runtime = StorageRuntime.open(data_root=Path(temporary.name), installation_id="mobile-test")
        self.addCleanup(runtime.close)
        self.room_expiry = datetime.now(timezone.utc).timestamp() + 3600
        self.allowed = {"together.room.create", "together.room.invite", "together.room.join"}
        self.context = SimpleNamespace(
            scope=SimpleNamespace(kind=SimpleNamespace(value="private"), native_scope_id="12345"),
            actor=SimpleNamespace(principal=SimpleNamespace(native_account_id="12345")),
            role_binding=SimpleNamespace(role=SimpleNamespace(subject_id="role-a")),
            key="context-a",
        )

        class Admission:
            def admit(inner, descriptor, context, at):
                self.admissions.append((descriptor, context))
                if descriptor.tool.action not in self.allowed:
                    raise TaskError("capability_denied", "denied")

        self.application = SimpleNamespace(state="running", external=Admission(), runtime=runtime)
        self.declarations = []
        for action in ("together.room.create", "together.room.invite", "together.room.join"):
            properties = {"mode": {"type": "string", "enum": ["call", "watch"]}} if action.endswith("create") else {}
            descriptor = SimpleNamespace(tool=SimpleNamespace(
                action=action,
                parameters_json=json.dumps({"type": "object", "properties": properties}),
            ), timeout_seconds=5)
            self.declarations.append(SimpleNamespace(host_tool_id="native:" + action, descriptor=descriptor))

        class Registry:
            application = self.application

            def api(inner, extension_id):
                self.api_lookups.append(extension_id)
                return self.apis.get(extension_id)

            def declarations(inner):
                return tuple(self.declarations)

            def current_declaration(inner, host_tool_id):
                return next((item for item in self.declarations if item.host_tool_id == host_tool_id), None)

            async def invoke(inner, host_tool_id, invocation):
                self.invocations.append(invocation)
                if self.before_dispatch is not None:
                    self.before_dispatch()
                invocation.before_dispatch()
                self.dispatched.append(invocation)
                if (getattr(inner, "reject_argument_conflict", False)
                        and len(self.invocations) > 1
                        and self.invocations[0].arguments_hash != invocation.arguments_hash):
                    error = RuntimeError("native_original_invocation_mismatch")
                    error.code = "native_original_invocation_mismatch"
                    raise error
                payload = json.dumps({"native": {"result": {"room": {
                    "url": "https://room.example/join/ticket", "mode": "call",
                    "expires_at": self.room_expiry, "ticket_token": "must-not-leak",
                }}}})
                source = SimpleNamespace(state=GovernanceState.ACTIVE, payload=payload)
                if self.after_dispatch is not None:
                    self.after_dispatch()
                return SimpleNamespace(state="succeeded", effect="committed", operation_id="op-1",
                    receipt_ref="receipt-1", source=source)

        self.invocations = []
        self.dispatched = []
        self.admissions = []
        self.api_lookups = []
        self.before_dispatch = self.after_dispatch = None
        self.registry = Registry()
        self.session = SimpleNamespace(profile=SimpleNamespace(target=self.context), application=self.application)
        self.session_users = {"valid-session-token-123456": "12345", "other-session-token-123456": "99999"}
        self.apis = {
            "reality": SimpleNamespace(
                companion_v2_mobile_session_user=lambda token: {
                    "schema": "companion.mobile-session.v1",
                    "user_id": self.session_users.get(token, ""),
                }),
            "together": SimpleNamespace(companion_v2_status=lambda: {
                "schema": "companion.provider-status.v1",
                "plugin_id": "astrbot_plugin_private_companion.together",
                "enabled": True,
                "runtime_ready": False,
            }),
        }
        def unexpected_star_lookup(plugin_id):
            raise AssertionError("mobile capabilities must use the V2 internal registry")

        host_context = SimpleNamespace(get_registered_star=unexpected_star_lookup,
                                       get_all_stars=lambda: ())
        self.controller = SimpleNamespace(
            context=host_context,
            session=self.session,
            native_extensions=self.registry,
            _multi_host=False,
            _terminated=False,
            now=lambda: datetime.now(timezone.utc),
        )
        self.bridge = MobileTogetherBridge(self.controller)

    def test_status_uses_per_action_v2_authorization_and_room_modes(self):
        self.allowed.remove("together.room.invite")
        result = self.bridge.status("valid-session-token-123456")
        self.assertTrue(result["matched"])
        self.assertTrue(result["ready"])
        self.assertFalse(result["capabilities"]["invite"])
        self.assertEqual(result["modes"], ["call", "watch"])
        self.assertEqual(self.api_lookups, ["reality", "together"])
        self.assertTrue(all(context is self.context for _, context in self.admissions))

    def test_disabled_room_service_is_distinct_from_missing_create_consent(self):
        self.apis['together'].companion_v2_status = lambda: {
            'schema': 'companion.provider-status.v1',
            'plugin_id': 'astrbot_plugin_private_companion.together',
            'enabled': False, 'runtime_ready': False}
        disabled = self.bridge.status('valid-session-token-123456')
        self.assertFalse(disabled['enabled'])
        self.assertFalse(disabled['ready'])
        self.assertEqual(disabled['reason'], 'mobile_together_disabled')
        self.apis['together'].companion_v2_status = lambda: {
            'schema': 'companion.provider-status.v1',
            'plugin_id': 'astrbot_plugin_private_companion.together',
            'enabled': True, 'runtime_ready': True}
        self.allowed.remove('together.room.create')
        denied = self.bridge.status('valid-session-token-123456')
        self.assertTrue(denied['enabled'])
        self.assertTrue(denied['running'])
        self.assertFalse(denied['ready'])
        self.assertEqual(denied['reason'], 'mobile_together_not_authorized')

    def test_failed_room_start_cannot_report_ready_even_with_create_consent(self):
        self.registry.status = lambda name: {'state': 'failed', 'error_code': 'native_service_start_failed'}
        failed = self.bridge.status('valid-session-token-123456')
        self.assertFalse(failed['ready'])
        self.assertEqual(failed['reason'], 'mobile_v2_together_start_failed')

    async def test_mobile_invocation_is_stable_for_retries_and_projects_ticket_secret(self):
        first = await self.bridge.invoke("valid-session-token-123456", request_id="request-1",
                                         operation="create", arguments={"mode": "call"})
        second = await self.bridge.invoke("valid-session-token-123456", request_id="request-1",
                                          operation="create", arguments={"mode": "call"})
        self.assertEqual(self.invocations[0].invocation_id, self.invocations[1].invocation_id)
        self.assertEqual(self.invocations[0].parent_task_id, self.invocations[1].parent_task_id)
        self.assertEqual(self.invocations[0].tool_call_id, self.invocations[1].tool_call_id)
        self.assertIs(self.invocations[0].context, self.context)
        self.assertIs(self.invocations[0].descriptor, self.declarations[0].descriptor)
        self.assertEqual(json.loads(self.invocations[0].arguments_json), {"mode": "call"})
        self.assertEqual(len(self.dispatched), 2)
        projected = self.bridge.mobile_result("create", first)
        self.assertEqual(projected["result"]["room"]["url"], "https://room.example/join/ticket")
        self.assertNotIn("ticket_token", json.dumps(projected))
        self.assertEqual(second["state"], "succeeded")

    async def test_operation_requires_stable_request_id_and_current_consent(self):
        with self.assertRaisesRegex(TaskError, "stable request_id"):
            await self.bridge.invoke(
                "valid-session-token-123456", request_id="invalid request id", operation="create",
                arguments={"mode": "call"})
        self.allowed.remove("together.room.create")
        with self.assertRaisesRegex(TaskError, "not authorized"):
            await self.bridge.invoke("valid-session-token-123456", request_id="request-2", operation="create",
                                     arguments={"mode": "call"})

    async def test_reusing_request_id_with_different_arguments_is_a_known_conflict(self):
        self.registry.reject_argument_conflict = True
        await self.bridge.invoke("valid-session-token-123456", request_id="request-conflict",
                                 operation="create", arguments={"mode": "call"})
        with self.assertRaisesRegex(TaskError, "different room arguments") as raised:
            await self.bridge.invoke("valid-session-token-123456", request_id="request-conflict",
                                     operation="create", arguments={"mode": "watch"})
        self.assertEqual(raised.exception.code, "mobile_together_request_conflict")
        self.assertEqual(len(self.invocations), 2)

    async def test_android_request_without_id_reuses_room_after_repair_and_bridge_restart(self):
        first = await self.bridge.invoke("valid-session-token-123456", request_id=None,
                                         operation="create", arguments={"mode": "call"})
        self.session_users["repaired-session-token-123456"] = "12345"
        bridge = MobileTogetherBridge(self.controller)
        second = await bridge.invoke("repaired-session-token-123456", request_id=None,
                                     operation="create", arguments={"mode": "call"})
        self.assertTrue(first["request_id"].startswith("mobile-room-"))
        self.assertEqual(second["request_id"], first["request_id"])
        self.assertEqual(self.invocations[0].invocation_id, self.invocations[1].invocation_id)

    async def test_android_unknown_result_and_cancellation_retain_original_request(self):
        for mode, error in (("call", TaskError("mobile_together_result_unknown", "unconfirmed")),
                            ("watch", asyncio.CancelledError())):
            with self.subTest(error=type(error).__name__):
                callback = AsyncMock(side_effect=error)
                with patch.object(self.bridge, "_invoke_native", callback):
                    with self.assertRaises(type(error)):
                        await self.bridge.invoke("valid-session-token-123456", request_id=None,
                                                 operation="create", arguments={"mode": mode})
                original = callback.await_args.args[-2]
                retry = await self.bridge.invoke("valid-session-token-123456", request_id=None,
                                                operation="create", arguments={"mode": mode})
                self.assertEqual(retry["request_id"], original)

    async def test_android_request_cannot_move_an_unconfirmed_room_to_another_persona(self):
        callback = AsyncMock(side_effect=TaskError("mobile_together_result_unknown", "unconfirmed"))
        with patch.object(self.bridge, "_invoke_native", callback):
            with self.assertRaises(TaskError):
                await self.bridge.invoke("valid-session-token-123456", request_id=None,
                                         operation="create", arguments={"mode": "call"})
        replacement = deepcopy(self.context)
        replacement.key = "context-other-persona"
        self.session.profile.target = replacement
        with self.assertRaises(TaskError) as raised:
            await self.bridge.invoke("valid-session-token-123456", request_id=None,
                                     operation="create", arguments={"mode": "call"})
        self.assertEqual(raised.exception.code, "mobile_v2_context_changed")
        self.assertEqual(self.dispatched, [])

    async def test_android_request_still_requires_current_consent(self):
        first = await self.bridge.invoke("valid-session-token-123456", request_id=None,
                                         operation="create", arguments={"mode": "call"})
        self.allowed.remove("together.room.create")
        with self.assertRaises(TaskError) as raised:
            await self.bridge.invoke("valid-session-token-123456", request_id=None,
                                     operation="create", arguments={"mode": "call"})
        self.assertEqual(raised.exception.code, "mobile_together_not_authorized")
        self.assertEqual(len(self.dispatched), 1)
        self.assertTrue(first["request_id"])

    async def test_android_confirmed_expiry_allows_next_room_but_unknown_never_expires(self):
        self.room_expiry = datetime.now(timezone.utc).timestamp() - 1
        first = await self.bridge.invoke("valid-session-token-123456", request_id=None,
                                         operation="create", arguments={"mode": "call"})
        second = await self.bridge.invoke("valid-session-token-123456", request_id=None,
                                          operation="create", arguments={"mode": "call"})
        self.assertNotEqual(first["request_id"], second["request_id"])
        callback = AsyncMock(return_value={"matched": True, "state": "unknown", "effect": "unknown"})
        with patch.object(self.bridge, "_invoke_native", callback):
            unknown = await self.bridge.invoke("valid-session-token-123456", request_id=None,
                                               operation="create", arguments={"mode": "call"})
            self.controller.now = lambda: datetime(2099, 1, 1, tzinfo=timezone.utc)
            retry = await self.bridge.invoke("valid-session-token-123456", request_id=None,
                                             operation="create", arguments={"mode": "call"})
        self.assertEqual(retry["request_id"], unknown["request_id"])

    def test_failed_operation_reads_its_recorded_error_code(self):
        store = SimpleNamespace(find=lambda operation_id: {"error": "together_public_access_unavailable"})
        registry = SimpleNamespace(active_service=lambda name: SimpleNamespace(
            _native_provider=SimpleNamespace(store=store)))
        self.assertEqual(MobileTogetherBridge._native_error(registry, "together-op-1"),
                         "together_public_access_unavailable")
        self.assertEqual(MobileTogetherBridge._native_error(registry, ""), "")
        missing = SimpleNamespace(active_service=lambda name: SimpleNamespace(
            _native_provider=SimpleNamespace(store=SimpleNamespace(find=lambda operation_id: None))))
        self.assertEqual(MobileTogetherBridge._native_error(missing, "together-op-2"), "")

    async def test_android_confirmed_failure_can_retry_with_a_new_identity(self):
        callback = AsyncMock(return_value={"matched": True, "state": "failed", "effect": "not_committed"})
        with patch.object(self.bridge, "_invoke_native", callback):
            failed = await self.bridge.invoke("valid-session-token-123456", request_id=None,
                                              operation="create", arguments={"mode": "call"})
        retry = await self.bridge.invoke("valid-session-token-123456", request_id=None,
                                        operation="create", arguments={"mode": "call"})
        self.assertNotEqual(retry["request_id"], failed["request_id"])

    def test_unmatched_user_is_not_retargeted_to_the_active_v2_profile(self):
        result = self.bridge.status("other-session-token-123456")
        self.assertFalse(result["matched"])

    async def test_current_consent_is_rechecked_before_dispatch(self):
        self.before_dispatch = lambda: self.allowed.remove("together.room.create")
        with self.assertRaises(TaskError) as raised:
            await self.bridge.invoke("valid-session-token-123456", request_id="request-revoked",
                                     operation="create", arguments={"mode": "call"})
        self.assertEqual(raised.exception.code, "capability_denied")
        self.assertEqual(self.dispatched, [])

    async def test_active_private_context_is_rechecked_after_native_result(self):
        replacement = deepcopy(self.context)
        replacement.key = "context-other-persona"
        replacement.role_binding.role.subject_id = "role-b"
        self.after_dispatch = lambda: setattr(self.session.profile, "target", replacement)
        with self.assertRaises(TaskError) as raised:
            await self.bridge.invoke("valid-session-token-123456", request_id="request-persona-changed",
                                     operation="create", arguments={"mode": "call"})
        self.assertEqual(raised.exception.code, "mobile_v2_context_changed")
        self.assertEqual(len(self.dispatched), 1)

    async def test_revoked_phone_session_cannot_obtain_completed_room_url(self):
        self.after_dispatch = lambda: self.session_users.pop("valid-session-token-123456")
        with self.assertRaises(TaskError) as raised:
            await self.bridge.invoke("valid-session-token-123456", request_id="request-session-revoked",
                                     operation="create", arguments={"mode": "call"})
        self.assertEqual(raised.exception.code, "mobile_session_invalid")
        self.assertEqual(len(self.dispatched), 1)

    def test_duplicate_multi_targets_are_refused(self):
        self.controller._multi_host = True
        targets = {"account-a": SimpleNamespace(participant_id="12345"),
                   "account-b": SimpleNamespace(participant_id="12345")}
        self.controller.settings = SimpleNamespace(
            accounts=tuple(SimpleNamespace(platform_id="qq", self_id=key) for key in targets),
            private_target=lambda platform, account, participant: targets[account]
                if participant == "12345" else None)
        self.controller.authority = object()
        with self.assertRaisesRegex(TaskError, "more than one"):
            self.bridge._target_context("12345")

    def test_explicit_primary_target_wins_over_wildcard_test_account(self):
        self.controller._multi_host = True
        exact = SimpleNamespace(target_id="exact-primary", participant_id="12345")
        template = SimpleNamespace(target_id="wildcard-template", participant_id="*")
        generated = SimpleNamespace(target_id="private-generated", participant_id="12345")
        self.controller.settings = SimpleNamespace(
            targets=(template, exact),
            accounts=(SimpleNamespace(platform_id="qq", self_id="account-a"),
                      SimpleNamespace(platform_id="test", self_id="test-account")),
            private_target=lambda platform, account, participant: (
                exact if account == "account-a" else generated)
                if participant == "12345" else None)
        scope = ScopeRef("mobile-test", "qq", ScopeKind.PRIVATE, "12345",
                         "bot-a", "account-a", "conversation-a", "12345")
        context = SimpleNamespace(
            scope=scope,
            actor=SimpleNamespace(principal=SimpleNamespace(native_account_id="12345")),
            key="context-exact",
        )
        selected_targets = []

        class Authority:
            def read(inner, key):
                return scope.document()

            def context(inner, target_id, role_id):
                selected_targets.append(target_id)
                return context

            class Personas:
                def resolve(inner, scope, *, observed_at):
                    return Outcome.success(SimpleNamespace(
                        binding=SimpleNamespace(role=SimpleNamespace(subject_id="role-a"))))

            personas = Personas()

        self.controller.authority = Authority()
        resolved = self.bridge._target_context("12345")
        self.assertIsNotNone(resolved)
        self.assertIs(resolved[2], context)
        self.assertEqual(selected_targets, ["exact-primary"])


if __name__ == "__main__":
    unittest.main()
