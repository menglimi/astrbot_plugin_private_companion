from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import asyncio
import importlib
import json
import logging
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import types
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from astrbot_host.integration_registry import UnifiedCompanionExtensionAPI
from astrbot_host.mobile_together import MobileTogetherBridge
from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind, ScopeRef
from companion_v2.sources.models import GovernanceState
from companion_v2.storage.runtime import StorageRuntime
from companion_v2.together.mobile.origin import MOBILE_ROOM_CALL
from companion_v2.together.provider import NativeTogetherProvider


def _load_mobile_gateway_module():
    try:
        from astrbot_host.native_extensions.reality import mobile_gateway
        return mobile_gateway
    except ModuleNotFoundError as exc:
        if exc.name != "astrbot":
            raise
        astrbot_mod = types.ModuleType("astrbot")
        api_mod = types.ModuleType("astrbot.api")
        api_mod.logger = logging.getLogger("astrbot_host.native_extensions.reality.mobile_gateway")
        astrbot_mod.api = api_mod
        with patch.dict(sys.modules, {"astrbot": astrbot_mod, "astrbot.api": api_mod}):
            return importlib.import_module("astrbot_host.native_extensions.reality.mobile_gateway")


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

    async def test_extension_api_forwards_via_mobile_gateway_for_create_invite_and_join(self):
        observed_markers = []
        original_invoke = self.registry.invoke

        async def recording_invoke(host_tool_id, invocation):
            observed_markers.append((invocation.descriptor.tool.action, MOBILE_ROOM_CALL.get()))
            response = await original_invoke(host_tool_id, invocation)
            action = invocation.descriptor.tool.action
            if action == "together.room.invite":
                response.source.payload = json.dumps({"native": {"result": {"invitation": {
                    "invitation_id": "inv-1", "room_id": "room-1",
                    "room_mode": "call", "expires_at": self.room_expiry,
                }}}})
            elif action == "together.room.join":
                response.source.payload = json.dumps({"native": {"result": {"join": {
                    "invitation_id": "inv-1", "room_id": "room-1", "room_mode": "call",
                    "join_url": "http://127.0.0.1:6321/join/member-tok?mode=call",
                    "expires_at": self.room_expiry, "replayed": False,
                }}}})
            return response

        self.registry.invoke = recording_invoke
        api = object.__new__(UnifiedCompanionExtensionAPI)
        api._mobile_together = self.bridge

        await api.companion_v2_mobile_together_create(
            "valid-session-token-123456", "req-create-gw", "call", via_mobile_gateway=True)
        await api.companion_v2_mobile_together_invite(
            "valid-session-token-123456", "req-invite-gw", 300, via_mobile_gateway=True)
        await api.companion_v2_mobile_together_join(
            "valid-session-token-123456", "req-join-gw", "inv-1", via_mobile_gateway=True)
        await api.companion_v2_mobile_together_create(
            "valid-session-token-123456", "req-create-direct", "call")

        self.assertEqual(observed_markers, [
            ("together.room.create", True),
            ("together.room.invite", True),
            ("together.room.join", True),
            ("together.room.create", False),
        ])
        self.assertFalse(MOBILE_ROOM_CALL.get())


class MobileTogetherGatewayEndToEndTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mobile_gateway = _load_mobile_gateway_module()
        temporary = TemporaryDirectory(prefix="v2-mobile-together-e2e-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        runtime = StorageRuntime.open(data_root=self.root / "storage", installation_id="mobile-e2e")
        self.addCleanup(runtime.close)
        self.session_token = "valid-mobile-gateway-token-123456"
        self.context = SimpleNamespace(
            scope=SimpleNamespace(
                installation_id="mobile-e2e",
                bot_id="bot-a",
                account_id="bot-a-account",
                platform="aiocqhttp",
                kind=SimpleNamespace(value="private"),
                native_scope_id="12345",
                session_id="12345",
            ),
            actor=SimpleNamespace(
                subject=SimpleNamespace(subject_id="human-12345"),
                principal=SimpleNamespace(native_account_id="12345"),
            ),
            role_binding=SimpleNamespace(role=SimpleNamespace(subject_id="role-a")),
            policy_revision=1,
            key="context-e2e",
        )
        self.quick_tunnel_calls = 0
        self.observed_gateway_flags = []
        self.observed_contextvar = []

        class LanTogetherPlugin:
            def __init__(plugin_self):
                plugin_self.public_base_url = ""
                plugin_self.server_enabled = True
                plugin_self.room_server = SimpleNamespace(
                    running=True,
                    local_base_url="http://127.0.0.1:6321",
                )
                plugin_self.rooms = {}
                plugin_self.detached_rooms = {}
                plugin_self._tickets = {}

            def work_collaboration_available(plugin_self):
                return True

            def _get_provider_by_id(plugin_self, provider_id):
                if provider_id == "main-chat-model":
                    return SimpleNamespace(text_chat=lambda **kwargs: None)
                return None

            async def _ensure_mobile_room_access(plugin_self, *, via_mobile_gateway=False):
                self.observed_gateway_flags.append(via_mobile_gateway)
                self.observed_contextvar.append(MOBILE_ROOM_CALL.get())
                if via_mobile_gateway:
                    return {
                        "url": plugin_self.room_server.local_base_url,
                        "tunnel_started": False,
                        "tunnel_ready": True,
                        "fixed_public_url": False,
                        "mobile_gateway": True,
                    }
                if plugin_self.public_base_url:
                    return {
                        "url": plugin_self.public_base_url,
                        "tunnel_started": False,
                        "tunnel_ready": True,
                        "fixed_public_url": True,
                    }
                self.quick_tunnel_calls += 1
                raise RuntimeError("cloudflared quick tunnel must not start in LAN/SSH mode")

            def issue_room_ticket(plugin_self, *, mode, user_id, native_v2=None):
                token = f"tok-{len(plugin_self._tickets) + 1}"
                ticket = SimpleNamespace(
                    token=token,
                    mode=mode,
                    user_id=user_id,
                    created_at=100.0,
                    expires_at=datetime.now(timezone.utc).timestamp() + 600,
                    native_v2=native_v2,
                )
                plugin_self._tickets[token] = ticket
                return ticket

            def _ticket_url(plugin_self, ticket):
                base = plugin_self.public_base_url or plugin_self.room_server.local_base_url
                return f"{base}/join/{ticket.token}?mode={ticket.mode}"

        self.together_plugin = LanTogetherPlugin()
        self.provider = NativeTogetherProvider(
            self.together_plugin,
            self.root / "together-data",
            version="7.2.9",
        )
        self.together_plugin._native_provider = self.provider

        self.declarations = []
        for action in ("together.room.create", "together.room.invite", "together.room.join"):
            properties = {"mode": {"type": "string", "enum": ["call", "watch", "work", "novel"]}} if action.endswith("create") else {}
            descriptor = SimpleNamespace(
                tool=SimpleNamespace(
                    action=action,
                    parameters_json=json.dumps({"type": "object", "properties": properties}),
                ),
                binding={"capability": action, "provider": self.provider.manifest()["provider_id"],
                         "version": self.provider.version, "generation": self.provider.generation},
                timeout_seconds=5,
            )
            self.declarations.append(SimpleNamespace(host_tool_id="native:" + action, descriptor=descriptor))

        class Admission:
            def admit(inner, descriptor, context, at):
                return None

        self.application = SimpleNamespace(state="running", external=Admission(), runtime=runtime)

        class ProviderRegistry:
            application = self.application

            def api(inner, extension_id):
                return self.apis.get(extension_id)

            def active_service(inner, extension_id):
                return self.together_plugin if extension_id == "together" else None

            def declarations(inner):
                return tuple(self.declarations)

            def current_declaration(inner, host_tool_id):
                return next((item for item in self.declarations if item.host_tool_id == host_tool_id), None)

            async def invoke(inner, host_tool_id, invocation):
                action = invocation.descriptor.tool.action
                req = {
                    "schema": "companion.native-provider.v1",
                    "invocation_id": invocation.invocation_id,
                    "parent_task_id": invocation.parent_task_id,
                    "tool_call_id": invocation.tool_call_id,
                    "action": action,
                    "origin_key": invocation.context.key,
                    "context": {
                        "actor": {
                            "subject": {"subject_id": invocation.context.actor.subject.subject_id},
                            "principal": {"native_account_id": invocation.context.actor.principal.native_account_id},
                        },
                        "audience": {
                            "scope": {
                                "installation_id": invocation.context.scope.installation_id,
                                "bot_id": invocation.context.scope.bot_id,
                                "account_id": invocation.context.scope.account_id,
                                "platform": invocation.context.scope.platform,
                                "kind": invocation.context.scope.kind.value,
                                "session_id": invocation.context.scope.session_id,
                            },
                        },
                        "role_binding": {"role_id": invocation.context.role_binding.role.subject_id},
                        "policy_revision": invocation.context.policy_revision,
                    },
                    "binding": dict(invocation.descriptor.binding),
                    "arguments": json.loads(invocation.arguments_json),
                    "arguments_hash": invocation.arguments_hash,
                    "deadline_at": invocation.deadline_at.isoformat(),
                }
                if action == "together.room.create":
                    req["host_persona"] = {
                        "persona_id": "role-a",
                        "prompt": "你是陪伴者。",
                        "bot_id": "bot-a",
                        "account_id": "bot-a-account",
                    }
                    req["host_execution"] = {
                        "model_provider_id": "main-chat-model",
                        "tts_provider_id": "",
                        "tts_language": "zh",
                        "tts_timeout_seconds": 30,
                    }
                raw = await self.provider.execute(req, before_dispatch=invocation.before_dispatch)
                source = None
                if raw["state"] == "succeeded":
                    source = SimpleNamespace(
                        state=GovernanceState.ACTIVE,
                        payload=json.dumps({"native": {"result": raw["result"]}}),
                    )
                return SimpleNamespace(
                    state=raw["state"],
                    effect=raw["effect"],
                    operation_id=raw["operation_id"],
                    receipt_ref=raw["receipt_ref"],
                    source=source,
                )

        self.registry = ProviderRegistry()
        self.apis = {
            "reality": SimpleNamespace(
                companion_v2_mobile_session_user=lambda token: {
                    "schema": "companion.mobile-session.v1",
                    "user_id": "12345" if token == self.session_token else "",
                }
            ),
            "together": SimpleNamespace(
                companion_v2_status=lambda: {
                    "schema": "companion.provider-status.v1",
                    "plugin_id": "astrbot_plugin_private_companion.together",
                    "enabled": True,
                    "runtime_ready": True,
                }
            ),
        }
        self.session = SimpleNamespace(profile=SimpleNamespace(target=self.context), application=self.application)
        self.controller = SimpleNamespace(
            context=SimpleNamespace(get_all_stars=lambda: ()),
            session=self.session,
            native_extensions=self.registry,
            _multi_host=False,
            _terminated=False,
            now=lambda: datetime.now(timezone.utc),
        )
        self.bridge = MobileTogetherBridge(self.controller)
        self.host_api = object.__new__(UnifiedCompanionExtensionAPI)
        self.host_api._mobile_together = self.bridge

        gateway_cls = self.mobile_gateway.MobileGatewayMixin
        outer = self

        class FakeMobileGateway(gateway_cls):
            def __init__(gw_self):
                gw_self._mobile_room_start_lock = asyncio.Lock()
                gw_self._payload = {}

            def _mobile_authorize(gw_self, allow_pairing=False):
                return {"user_id": "12345"}

            async def _mobile_request_payload(gw_self):
                return dict(gw_self._payload)

            def _mobile_token_from_request(gw_self):
                return outer.session_token

            def _mobile_proxy_rooms_enabled(gw_self):
                return True

            def _mobile_external_origin(gw_self):
                return "http://127.0.0.1:6322"

            def _mobile_request_host(gw_self):
                return "127.0.0.1:6322"

            def _mobile_room_url_via_gateway(gw_self, room_url):
                return room_url.replace("http://127.0.0.1:6321", "http://127.0.0.1:6322/room-proxy")

            def v2_host_api(gw_self):
                return outer.host_api

            def native_extension(gw_self, name):
                return outer.together_plugin if name == "together" else None

        self.gateway = FakeMobileGateway()

    async def test_mobile_create_room_end_to_end_lan_ssh_tunnel_succeeds_without_cloudflared(self):
        for mode, req_id in (("watch", "lan-watch-1"), ("call", "lan-call-1")):
            with self.subTest(mode=mode):
                self.observed_gateway_flags.clear()
                self.observed_contextvar.clear()
                self.gateway._payload = {"mode": mode, "client": "android_native", "request_id": req_id}

                body, status = await self.gateway.mobile_create_room()

                self.assertEqual(status, 200, body)
                self.assertTrue(body["ok"])
                self.assertEqual(body["data"]["mode"], mode)
                self.assertEqual(body["data"]["request_id"], req_id)
                self.assertIn("/room-proxy/join/tok-", body["data"]["url"])
                self.assertEqual(self.quick_tunnel_calls, 0)
                # First call is gateway pre-prepare, second call is NativeTogetherProvider._create_room.
                self.assertEqual(self.observed_gateway_flags, [True, True])
                self.assertEqual(self.observed_contextvar, [False, True])
                self.assertFalse(MOBILE_ROOM_CALL.get())

        # Sanity check: a non-gateway invocation still expects public access and fails without public_base_url.
        direct = await self.host_api.companion_v2_mobile_together_create(
            self.session_token, "direct-non-gateway-1", "watch", via_mobile_gateway=False)
        self.assertEqual(direct["state"], "failed")
        self.assertEqual(direct["code"], "together_public_access_unavailable")
        self.assertEqual(self.quick_tunnel_calls, 1)

    async def test_mobile_invite_and_join_forward_gateway_flag_and_surface_native_error_codes(self):
        recorded_provider_markers = []
        original_execute = self.provider.execute

        async def spy_execute(req, *, before_dispatch):
            recorded_provider_markers.append((req["action"], MOBILE_ROOM_CALL.get()))
            return await original_execute(req, before_dispatch=before_dispatch)

        with patch.object(self.provider, "execute", side_effect=spy_execute), \
             patch.object(self.mobile_gateway, "logger") as gw_logger:
            self.gateway._payload = {"request_id": "invite-no-room-1", "expires_in_seconds": 300}
            invite_body, invite_status = await self.gateway.mobile_invite_room()

            self.gateway._payload = {
                "request_id": "join-missing-inv-1",
                "invitation_id": "inv-does-not-exist",
                "client": "android_native",
            }
            join_body, join_status = await self.gateway.mobile_join_room()

        self.assertEqual(recorded_provider_markers, [
            ("together.room.invite", True),
            ("together.room.join", True),
        ])
        self.assertEqual(self.quick_tunnel_calls, 0)
        self.assertFalse(MOBILE_ROOM_CALL.get())

        self.assertEqual(invite_status, 409)
        self.assertFalse(invite_body["ok"])
        self.assertEqual(invite_body["code"], "together_no_room_to_invite")
        self.assertEqual(invite_body["data"]["error_code"], "together_no_room_to_invite")
        self.assertEqual(invite_body["data"]["capability"], "together.room.invite")
        self.assertEqual(invite_body["data"]["request_id"], "invite-no-room-1")
        self.assertFalse(invite_body["uncertain"])

        self.assertEqual(join_status, 409)
        self.assertFalse(join_body["ok"])
        self.assertEqual(join_body["code"], "native_invitation_not_found")
        self.assertEqual(join_body["data"]["error_code"], "native_invitation_not_found")
        self.assertEqual(join_body["data"]["capability"], "together.room.join")
        self.assertEqual(join_body["data"]["request_id"], "join-missing-inv-1")
        self.assertFalse(join_body["uncertain"])

        self.assertEqual(gw_logger.warning.call_count, 2)
        warning_repr = repr(gw_logger.warning.call_args_list)
        self.assertIn("together_no_room_to_invite", warning_repr)
        self.assertIn("native_invitation_not_found", warning_repr)
        self.assertNotIn(self.session_token, warning_repr)
        self.assertNotIn(self.session_token, repr(invite_body))
        self.assertNotIn(self.session_token, repr(join_body))

    async def test_mobile_invite_and_join_exceptions_and_unknown_state_log_and_preserve_codes(self):
        cases = (
            ("invite", self.gateway.mobile_invite_room, "companion_v2_mobile_together_invite",
             {"request_id": "inv-err-1"}, "together.room.invite"),
            ("join", self.gateway.mobile_join_room, "companion_v2_mobile_together_join",
             {"request_id": "join-err-1", "invitation_id": "inv-1", "client": "android_native"},
             "together.room.join"),
        )
        for label, handler, api_method, payload, capability in cases:
            with self.subTest(operation=label, kind="exception"):
                self.gateway._payload = dict(payload)
                exc = TaskError("mobile_together_request_conflict", "secret:" + self.session_token)
                with patch.object(self.host_api, api_method, new=AsyncMock(side_effect=exc)), \
                     patch.object(self.mobile_gateway, "logger") as gw_logger:
                    body, status = await handler()
                self.assertEqual(status, 409)
                self.assertEqual(body["code"], "mobile_together_request_conflict")
                self.assertEqual(body["data"]["capability"], capability)
                self.assertFalse(body.get("uncertain", False))
                gw_logger.warning.assert_called_once()
                self.assertNotIn(self.session_token, repr(body))
                self.assertNotIn(self.session_token, repr(gw_logger.mock_calls))

            with self.subTest(operation=label, kind="unknown"):
                self.gateway._payload = dict(payload)
                unknown_result = {
                    "matched": True,
                    "state": "unknown",
                    "effect": "unknown",
                    "operation_id": "together-op-unknown",
                    "receipt_ref": "together.native.operation:together-op-unknown",
                    "code": "execution_interrupted",
                    "request_id": payload["request_id"],
                }
                with patch.object(self.host_api, api_method, new=AsyncMock(return_value=unknown_result)), \
                     patch.object(self.mobile_gateway, "logger") as gw_logger:
                    body, status = await handler()
                self.assertEqual(status, 409)
                self.assertEqual(body["code"], "mobile_together_result_unknown")
                self.assertEqual(body["data"]["error_code"], "execution_interrupted")
                self.assertEqual(body["data"]["capability"], capability)
                self.assertTrue(body["uncertain"])
                gw_logger.warning.assert_called_once()
                self.assertIn("execution_interrupted", repr(gw_logger.warning.call_args))


if __name__ == "__main__":
    unittest.main()
