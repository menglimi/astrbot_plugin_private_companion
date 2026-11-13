"""Dashboard requests and deterministic native model doubles for private routines."""

import asyncio
import json

from tests.multi_host_support import MultiFixture
from tests.native_adapter_support import completion
from astrbot.api.web import PluginRequest, bind_request_context
from starlette.requests import Request


class MultiLifeFixture(MultiFixture):
    def register(self):
        self.controller.register_routes()
        self.routes = {path: handler for path, handler, _, _ in self.native.registered_web_apis}
        self.paths = {(target["target_id"], role["role_id"]): role["life_api"]
                      for target in self.controller.web_api.target_inventory() for role in target["roles"]}

    async def initialize(self, config=None):
        if config is not None:
            self.controller = self.make_controller(config)
        self.register()
        result = await self.controller.initialize_profile()
        self.assertTrue(result.is_success, result.failure)
        self.native.provider.client.state.behavior = self.model
        return self.controller.session.application

    async def start(self):
        result = await self.controller.start()
        self.assertTrue(result.is_success, result.failure)
        return self.controller.session.application

    async def request(self, target="first", role="role-a", *, data=None, username="admin", query="", gate=None):
        path = self.paths[(target, role)] + ("/command" if data is not None else "")
        body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else b""
        async def receive():
            if gate is not None:
                gate[0].set()
                await gate[1].wait()
            return {"type": "http.request", "body": body, "more_body": False}
        native = Request({"type": "http", "method": "POST" if data is not None else "GET", "path": path,
            "headers": [(b"content-type", b"application/json")], "query_string": query.encode("utf-8")}, receive=receive)
        with bind_request_context(PluginRequest(native, username=username)):
            return await self.routes[path]()

    def ok(self, result):
        self.assertEqual(result["status"], "ok", result)
        return result["data"]

    async def status(self, target="first", role="role-a"):
        return self.ok(await self.request(target, role))

    def context(self, target="first", role="role-a"):
        return self.controller.authority.context(target, role)

    def routine(self, target="first", role="role-a"):
        return self.controller.session.application.life_runtime.for_context(self.context(target, role))

    async def body(self, action, target="first", role="role-a", *, item=None, key=None, **fields):
        status = await self.status(target, role)
        body = dict(action=action, operation_key=key or action, view_token=status["view_token"])
        if item is not None:
            body.update(view_token=item["view_token"], expected_revision=item["revision"])
            if "activity_id" in item:
                body["activity_id"] = item["activity_id"]
            else:
                body.update(kind=item["kind"], local_date=item["local_date"])
        if action == "configure":
            body["edit_token"] = status["edit_token"]
        return body | fields

    async def command(self, action, target="first", role="role-a", **fields):
        return self.ok(await self.request(target, role, data=await self.body(action, target, role, **fields)))

    async def enable(self, target="first", role="role-a", **changes):
        return await self.command("configure", target, role,
            changes={"enabled": True, "diary_enabled": False, "derive_enabled": False, **changes})

    async def book(self, target="first", role="role-a", *, key="book"):
        await self.command("add_text", target, role, key=key, title=target + role, text="这是 " + target + role + " 的原文。")
        return (await self.status(target, role))["activities"][-1]

    async def until(self, predicate):
        async def wait():
            while not predicate():
                await asyncio.sleep(0.01)
        await asyncio.wait_for(wait(), 8)

    async def settled(self):
        app = self.controller.session.application
        await self.until(lambda: not app.life_runtime.active_handles and not app.journal_schedule.active_handles)

    async def model(self, payload, tools):
        persona = ("第二人格" if payload["messages"][0]["content"].startswith("你是第二个人格")
                   else "原人格")
        materials = {}
        for message in payload["messages"]:
            if message["role"] == "user":
                document = json.loads(message["content"])
                materials.update((m["purpose"], m["text"]) for m in document.get("materials", []))
        if "original_reading_span" in materials:
            data = {"reflection": persona + "对原文的阅读感受。"}
        elif "parent_journal" in materials:
            data = {"summary": persona + "的私人记录。", "dream_fragments": [], "continuity": ""}
        elif "material_candidates" in materials:
            data = {"material_ids": [m["id"] for m in json.loads(materials["material_candidates"])[-8:]]}
        elif "writing_date" in materials:
            dream = json.loads(materials["writing_date"])["kind"] == "dream"
            data = {"body": persona + "记下一个念头。", "form": "随记",
                    "recall": "fragments" if dream else None, "afterglow": ""}
        else:
            return completion()
        return completion(json.dumps(data, ensure_ascii=False))
