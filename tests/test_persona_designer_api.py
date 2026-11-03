"""Dashboard, provider and failure-isolation contracts, with no live bot traffic."""

import asyncio
import builtins
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

import tests.astrbot_data_root  # isolate SDK import-time files before loading AstrBot
from astrbot.api.web import PluginMultiDict
from astrbot_host.persona_designer_api import PersonaDesignerWebAPI, PREFIX
from astrbot_host.optional_persona_designer import register_optional_designer
from companion_v2.execution.models import TaskError
from tests.test_persona_designer import response, state
from tests.test_persona_workshop_api import PersonaWorkshopFixture


def request(body=None, query=None, username="admin"):
    async def reader():
        return body if isinstance(body, bytes) else json.dumps(body).encode()
    pairs = list(query.items()) if isinstance(query, dict) else list(query or [])
    return NS(username=username, query=PluginMultiDict(pairs), body=reader)


class DesignerAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="companion-design-api-")
        self.addCleanup(self.temp.cleanup)
        self.provider = NS(kind="text", provider_config={"id": "design-model"}, name="设计模型",
                           text_chat=AsyncMock(return_value=NS(completion_text=response())))
        self.personas = [{"name": "conversation-role", "prompt": "另一个角色"},
                         {"name": "configured", "prompt": "保留，保留！  \n\n"}]
        self.manager = NS(personas_v3=self.personas,
                          get_persona_v3_by_id=lambda key: next((p for p in self.personas if p["name"] == key), None))
        self.context = NS(persona_manager=self.manager,
            get_config=lambda **_: {"provider_settings": {"default_personality": "configured"}},
            get_all_providers=lambda: [self.provider, NS(kind="image", provider_config={"id": "image-model"})])
        self.controller = NS(context=self.context, data_root=Path(self.temp.name), runtime=None,
                             native_types=NS(is_text_provider=lambda p: p.kind == "text"), _terminated=False)
        self.api = PersonaDesignerWebAPI(self.controller)
        self.routes = {(path, methods[0]): handler for path, handler, methods, _ in self.api.routes()}

    async def asyncTearDown(self):
        self.api.close()
        await asyncio.sleep(0)

    async def call(self, suffix, *, method="GET", body=None, query=None, username="admin"):
        with patch("astrbot_host.persona_designer_api.request", request(body, query, username)):
            return await self.routes[(PREFIX + suffix, method)]()

    def turn_body(self, **changes):
        return {"request_id": "a" * 32, "action": "draft", "state": state(), **changes}

    async def test_no_companion_setup_needed_and_configured_persona_is_first(self):
        result = (await self.call("/options"))["data"]
        self.assertEqual([model["id"] for model in result["models"]], ["design-model"])
        self.assertEqual(result["personas"][0], {"id": "configured", "name": "configured", "default": True})
        self.assertEqual(result["default_persona_id"], "configured")
        read = await self.call("/persona", query={"id": "configured"})
        self.assertEqual(read["data"]["text"], self.personas[1]["prompt"])
        result = await self.call("/turn", method="POST", body=self.turn_body())
        self.assertEqual(result["status"], "ok")
        call = self.provider.text_chat.call_args.kwargs
        self.assertEqual(json.loads(call["prompt"])["provider_id"], "design-model")
        self.assertEqual(call["contexts"], [])
        self.assertFalse((self.controller.data_root / "persona_designer").exists())

    async def test_bad_request_output_ceiling_retries_once_at_4096(self):
        class BadRequestError(Exception):
            pass

        self.provider.text_chat = AsyncMock(side_effect=[
            BadRequestError("400 invalid max_tokens"),
            NS(completion_text=response()),
        ])
        result = await self.call("/turn", method="POST", body=self.turn_body())
        self.assertEqual(result["status"], "ok")
        limits = [call.kwargs["max_tokens"] for call in self.provider.text_chat.call_args_list]
        self.assertEqual(limits, [8000, 4096])

    async def test_new_agent_runner_default_and_persona_list_failure_are_local(self):
        self.context.get_config = lambda **_: {"agent_runner": {"runner_type": "local",
            "config": {"persona": {"persona_id": "conversation-role"}}}}
        self.assertEqual((await self.call("/options"))["data"]["personas"][0]["id"], "conversation-role")
        with patch("astrbot_host.persona_designer_api.available_personas", AsyncMock(side_effect=OSError("unavailable"))):
            result = (await self.call("/options"))["data"]
        self.assertEqual(result["personas"], [])
        self.assertEqual(len(result["models"]), 1)
        self.assertTrue(result["persona_issue"])

    async def test_generate_save_and_reopen_for_two_personas_never_writes_host_configuration(self):
        config = {"provider_settings": {"default_personality": "configured", "provider_id": "chat-model"}}
        self.context.get_config = lambda **_: config
        self.personas[1]["_begin_dialogs_processed"] = [
            {"role": "user", "content": "旧示例，保留空格  ", "_no_save": True},
            {"role": "assistant", "content": "旧回答\n"}]
        before_personas, before_config = deepcopy(self.personas), deepcopy(config)
        writes = [AsyncMock(side_effect=AssertionError("persona writes are forbidden")) for _ in range(4)]
        for name, write in zip(("create_persona", "update_persona", "delete_persona", "set_persona"), writes):
            setattr(self.manager, name, write)
        for index, record in enumerate(before_personas):
            read = await self.call("/persona", query={"id": record["name"]})
            self.assertEqual(read["status"], "ok", read)
            text = read["data"]["text"]
            self.assertTrue(text.startswith(record["prompt"]))
            if index == 1:
                examples = json.loads(text.rsplit("\n", 1)[-1])
                self.assertEqual(examples, [{"role": "user", "content": "旧示例，保留空格  "},
                                            {"role": "assistant", "content": "旧回答\n"}])
                self.assertNotIn("_no_save", text)
            snapshot = state(mode="optimize", source_persona_id=record["name"], source_text=text)
            self.provider.text_chat.return_value = NS(completion_text=response(draft="生成的新稿" + str(index)))
            result = await self.call("/turn", method="POST", body=self.turn_body(state=snapshot))
            self.assertEqual(result["status"], "ok", result)
            snapshot["draft"] = result["data"]["draft"]
            identifier = str(index + 1) * 32
            saved = await self.call("/drafts", method="POST", body={"id": identifier, "title": record["name"], "state": snapshot})
            self.assertEqual(saved["status"], "ok", saved)
            restored = await self.call("/draft", query={"id": identifier})
            self.assertEqual(restored["data"]["state"], snapshot)
            self.assertEqual(self.personas, before_personas)
            self.assertEqual(config, before_config)
        for write in writes:
            write.assert_not_called()
        self.assertEqual(len((await self.call("/drafts"))["data"]["items"]), 2)

    async def test_legacy_async_persona_reader_preserves_original_and_missing_persona_is_local(self):
        original = {"name": "legacy", "prompt": "旧版原文  \n\t\n"}
        self.context.persona_manager = NS(get_personas=AsyncMock(return_value=[original]),
                                          get_persona=AsyncMock(return_value=original))
        result = await self.call("/persona", query={"id": "legacy"})
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["data"]["text"], "旧版原文  \n\t\n")
        missing = await self.call("/persona", query={"id": "deleted"})
        self.assertEqual(missing["message"], "persona_designer_persona_missing")
        self.assertEqual((await self.call("/options"))["status"], "ok")

    async def test_dashboard_identity_cannot_be_supplied_in_body(self):
        for name in (None, "", " admin", "api_key:test"):
            result = await self.call("/turn", method="POST", body=self.turn_body(), username=name)
            self.assertEqual(result["message"], "dashboard_identity_required")
        body = self.turn_body(owner="admin")
        self.assertEqual((await self.call("/turn", method="POST", body=body))["message"], "persona_designer_invalid")
        self.provider.text_chat.assert_not_awaited()

    async def test_real_sdk_query_mapping_rejects_duplicate_and_unknown_fields(self):
        for query in ([('id', 'configured'), ('id', 'conversation-role')], {"id": "configured", "owner": "other"}):
            self.assertEqual((await self.call("/persona", query=query))["message"], "persona_designer_invalid")
        self.assertEqual((await self.call("/options"))["status"], "ok")

    async def test_existing_management_policy_is_enforced_when_runtime_exists(self):
        self.controller.runtime = NS(installation_id="test-installation")
        self.controller.now = lambda: "test-time"
        with patch("astrbot_host.persona_designer_api.dashboard_operator", side_effect=TaskError("core_access_required", "denied")):
            result = await self.call("/options")
        self.assertEqual(result["message"], "core_access_required")

    async def test_missing_provider_does_not_fall_back_or_change_any_configuration(self):
        result = await self.call("/turn", method="POST", body=self.turn_body(state=state(provider_id="removed")))
        self.assertEqual(result["message"], "persona_designer_model_missing")
        self.provider.text_chat.assert_not_awaited()
        self.assertEqual(self.context.get_config()["provider_settings"]["default_personality"], "configured")
        self.assertEqual(self.personas[0]["prompt"], "另一个角色")

    async def test_provider_and_storage_errors_do_not_leak_details(self):
        self.provider.text_chat.side_effect = RuntimeError("secret-provider-token")
        result = await self.call("/turn", method="POST", body=self.turn_body())
        self.assertEqual(result["message"], "persona_designer_unavailable")
        self.assertNotIn("secret-provider-token", json.dumps(result))
        with patch.object(self.api.store, "save", side_effect=OSError("secret-file-path")):
            result = await self.call("/drafts", method="POST", body={"id": "b" * 32, "title": "一", "state": state()})
        self.assertEqual(result["message"], "persona_designer_unavailable")
        self.assertNotIn("secret-file-path", json.dumps(result))
        self.assertEqual((await self.call("/options"))["status"], "ok")

    async def test_model_protocol_failure_is_retryable_and_saves_are_explicit_owner_scoped(self):
        self.provider.text_chat.return_value = NS(completion_text="plain answer without JSON")
        result = await self.call("/turn", method="POST", body=self.turn_body())
        self.assertEqual(result["message"], "persona_designer_response_invalid")
        self.assertEqual((await self.call("/drafts"))["data"]["items"], [])
        snapshot = state(input="未发送", draft="手工改稿")
        saved = await self.call("/drafts", method="POST", body={"id": "b" * 32, "title": "一", "state": snapshot})
        self.assertEqual(saved["status"], "ok")
        self.assertEqual((await self.call("/draft", query={"id": "b" * 32}))["data"]["state"], snapshot)
        self.assertEqual((await self.call("/drafts", username="other"))["data"]["items"], [])
        self.assertEqual((await self.call("/draft", query={"id": "b" * 32}, username="other"))["message"], "persona_designer_draft_missing")

    async def test_stop_and_timeout_return_promptly_even_if_provider_suppresses_cancel(self):
        release = asyncio.Event()
        async def stubborn(**_):
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return NS(completion_text=response())
        self.provider.text_chat.side_effect = stubborn
        try:
            with patch("astrbot_host.persona_designer_api.MODEL_TIMEOUT", .01):
                result = await asyncio.wait_for(self.call("/turn", method="POST", body=self.turn_body()), .5)
            self.assertEqual(result["message"], "persona_designer_timeout")
            self.assertEqual(len(self.api.jobs), 1, "uncancelled provider work keeps its concurrency slot")
            self.assertEqual((await self.call("/turn", method="POST", body=self.turn_body()))["message"], "persona_designer_busy")
            self.assertEqual((await self.call("/options"))["status"], "ok")
        finally:
            release.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        self.assertFalse(self.api.jobs)

    async def test_cancel_and_close_stop_only_the_designers_own_work(self):
        started = asyncio.Event()
        async def slow(**_):
            started.set()
            await asyncio.Event().wait()
        self.provider.text_chat.side_effect = slow
        foreground = asyncio.create_task(asyncio.Event().wait())
        try:
            for close in (False, True):
                turn = asyncio.create_task(self.call("/turn", method="POST", body=self.turn_body()))
                await started.wait(); started.clear()
                if close:
                    self.api.close()
                else:
                    result = await self.call("/cancel", method="POST", body={"request_id": "a" * 32})
                    self.assertTrue(result["data"]["cancelled"])
                self.assertEqual((await asyncio.wait_for(turn, .5))["message"], "persona_designer_cancelled")
                self.assertFalse(foreground.done())
        finally:
            foreground.cancel()
            await asyncio.gather(foreground, return_exceptions=True)

    async def test_global_and_per_admin_slots_are_bounded_without_locking_other_requests(self):
        release = asyncio.Event()
        async def slow(**_):
            await release.wait()
            return NS(completion_text=response())
        self.provider.text_chat.side_effect = slow
        # The real request proxy is context-local. Direct handler fixtures use
        # the same synthetic identity/body, while owners exercise the job limit.
        with patch("astrbot_host.persona_designer_api.request", request(self.turn_body())):
            first = asyncio.create_task(self.api.turn("first"))
            second = asyncio.create_task(self.api.turn("second"))
            await asyncio.sleep(0)
            try:
                for owner in ("first", "third"):
                    with self.assertRaises(TaskError) as raised:
                        await self.api.turn(owner)
                    self.assertEqual(raised.exception.code, "persona_designer_busy")
                self.assertEqual((await self.api.options("first"))["models"][0]["id"], "design-model")
            finally:
                release.set()
                await asyncio.gather(first, second)


class ExistingWorkshopDataTests(PersonaWorkshopFixture):
    async def test_existing_questionnaire_and_role_profile_survive_designer_save_and_close(self):
        from tests.test_persona_workshop_api import (
            RoleProfileConfigurationOwner, role_profile_fields, role_profile_target_for, support)

        # Create existing data using the original APIs before enabling the designer.
        with patch("astrbot_host.optional_persona_designer.register_optional_designer", return_value=False):
            await self.initialize()
        saved = await self.post({"action": "save", "text": "原问卷旧草稿  \n", "note": "原有版本", "expected_current": ""})
        self.assertEqual(saved["status"], "ok", saved)
        runtime = self.controller.runtime
        owner = RoleProfileConfigurationOwner(runtime)
        fields = role_profile_fields()
        target = role_profile_target_for(self.controller.session.profile.target)
        previous = owner.read(target, fields)
        owner.write(target, fields, changes={"role.profile.name": "原角色档案", "role.profile.personality": "保留原性格"},
                    reset=(), expected_revision=previous.revision, observed_at=support.NOW)
        previous_profile = owner.read(target, fields)
        previous_workshop = await self.state()
        previous_persona = deepcopy(self.native.persona)
        with runtime.read_only_snapshot() as db:
            previous_database = list(db.iterdump())

        api = PersonaDesignerWebAPI(self.controller)
        routes = {(path, methods[0]): handler for path, handler, methods, _ in api.routes()}
        async def call(suffix, method="GET", body=None, query=None):
            with patch("astrbot_host.persona_designer_api.request", request(body, query)):
                return await routes[(PREFIX + suffix, method)]()
        try:
            read = await call("/persona", query={"id": "original"})
            self.assertEqual(read["status"], "ok", read)
            self.assertEqual(read["data"]["text"], previous_workshop["persona"]["text"])
            snapshot = state(mode="optimize", source_persona_id="original", source_text=read["data"]["text"], draft="仅存于新的她")
            saved = await call("/drafts", "POST", {"id": "d" * 32, "title": "独立草稿", "state": snapshot})
            self.assertEqual(saved["status"], "ok", saved)
            self.assertEqual((await call("/draft", query={"id": "d" * 32}))["data"]["state"], snapshot)
        finally:
            api.close()

        # Original readers still see exactly the same data after the feature closes.
        self.assertEqual(await self.state(), previous_workshop)
        self.assertEqual(owner.read(target, fields), previous_profile)
        self.assertEqual(self.native.persona, previous_persona)
        with runtime.read_only_snapshot() as db:
            self.assertEqual(list(db.iterdump()), previous_database)


class OptionalRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.core = ("/core", object(), ["GET"], "Core")
        self.context = NS(registered_web_apis=[self.core])
        self.owner = NS(controller=NS(context=self.context), _registered_routes=[self.core[:2]])

    def test_import_failure_leaves_core_routes_owned_and_registered(self):
        original = builtins.__import__
        def importer(name, *args, **kwargs):
            if name == "persona_designer_api":
                raise ImportError("optional dependency unavailable")
            return original(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=importer):
            self.assertFalse(register_optional_designer(self.owner, lambda *_: None))
        self.assertEqual(self.context.registered_web_apis, [self.core])
        self.assertEqual(self.owner._registered_routes, [self.core[:2]])

    def test_partial_registration_rollback_never_unregisters_core_even_if_close_fails(self):
        class Optional:
            def __init__(self, _): pass
            def routes(self):
                return [("/optional/one", object(), ["GET"], ""), ("/optional/two", object(), ["GET"], "")]
            def close(self): raise RuntimeError("cleanup failure")
        def register(*route):
            self.context.registered_web_apis.append(route)
            if route[0].endswith("two"):
                raise RuntimeError("partial registration failure")
        with patch("astrbot_host.persona_designer_api.PersonaDesignerWebAPI", Optional):
            self.assertFalse(register_optional_designer(self.owner, register))
        self.assertEqual(self.context.registered_web_apis, [self.core])
        self.assertEqual(self.owner._registered_routes, [self.core[:2]])


class HostLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_and_multi_core_routes_survive_optional_init_failure(self):
        from tests import test_astrbot_staging_host as support
        from tests.multi_host_support import multi_config
        from astrbot_host.multi_controller import AstrBotMultiController

        for constructor, config in ((support.AstrBotStagingController, support.host_config()),
                                    (AstrBotMultiController, multi_config())):
            with self.subTest(host=constructor.__name__), TemporaryDirectory(prefix="companion-design-host-") as root:
                native = support.WebContext()
                types = support.NativeAdapterTypes("4.27.2", support.FakeEvent, support.FakeProvider,
                                                  NS, NS, support.FakeReverseApi)
                controller = constructor(native, config, Path(root), native_types=types, now=lambda: support.NOW)
                try:
                    with patch("astrbot_host.persona_designer_api.PersonaDesignerWebAPI", side_effect=RuntimeError("optional init failure")):
                        controller.register_routes()
                    self.assertTrue(native.registered_web_apis)
                    self.assertFalse(any(path.startswith(PREFIX + "/") for path, *_ in native.registered_web_apis))
                    status = next(handler for path, handler, *_ in native.registered_web_apis if path.endswith("/status"))
                    with patch("astrbot_host.web_api.request", request()), patch("astrbot_host.multi_api.request", request()):
                        self.assertEqual((await status())["status"], "ok")
                finally:
                    await controller.terminate()
                self.assertEqual(native.registered_web_apis, [])


if __name__ == "__main__":
    unittest.main()
