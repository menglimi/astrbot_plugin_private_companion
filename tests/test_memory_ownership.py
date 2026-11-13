"""记忆归属：V2 与「我会牢牢记住你」之间谁对外表达。

这一组测的是本次改动的核心约定，而且每一条都对应一个**真出现过的故障形态**：

1. 牢插件在 → V2 不注入记忆，也不给模型 ``memory.*`` 工具（否则模型会把新记忆写进
   两个库、并读到两套互相矛盾的叙事）。
2. 牢插件不在 → V2 的注入与工具**和以前完全一样**，不因为多了这套机制而少给东西。
3. 牢插件在、但句柄读不到（被停用、依赖缺失、接口变了）→ V2 立刻恢复自己的记忆，
   并且**页面说得出来**为什么。这是最重要的一条：两边都不注入而没人说明，是本次
   唯一不可接受的结局。
4. 工具门断言的是**交给模型的那份清单**，不是一个布尔标志。
5. 页面文案按事实说归属：让位时点名牢插件，自己来时不再声称 V2 独占记忆。

用到真库、真任务、真提示词组装（继承 ``HostFixture``），只有「谁在提供记忆」这一个
端口的实现是可替换的——那正是被测的边界。
"""

from __future__ import annotations

import json
import pathlib
import sys
import unittest
from dataclasses import dataclass

from astrbot_host.memory_api import PREFIX as MEMORY_PREFIX
from astrbot_host.page_api import PageWebAPI
from companion_v2.adapters.astrbot.memory_owner import (
    MEMORY_OWNER_MODULE_NAMES,
    MEMORY_OWNER_PLUGIN_ID,
    memory_owner_provider,
    read_memory_ownership,
)
from companion_v2.capabilities.ownership import (
    DEFAULT_MEMORY_OWNER,
    MemoryOwnership,
    REASON_DECLARATION_ABSENT,
    REASON_EXTERNAL_ACTIVE,
    REASON_HANDLE_DISABLED,
    REASON_HANDLE_UNREADABLE,
    REASON_MODULE_ABSENT,
    RENDER_EXTERNAL,
    RENDER_SELF,
    SelfMemoryOwner,
    delegated_memory,
)
from companion_v2.legacy.catalog import (
    CAPABILITY_DISPOSITIONS,
    LEGACY_COMPATIBILITY_CATALOG,
    CatalogAction,
    validate_capability_dispositions,
)
from companion_v2.memory import codec
from companion_v2.memory.context import DELEGATED_STATE
from companion_v2.memory.models import AtomRef, Proposal
from companion_v2.memory.prompts import MEMORY_INSTRUCTION
from tests.test_host_application import HostFixture

PAGE = pathlib.Path(__file__).resolve().parents[1] / "pages" / "陪伴小屋"
DELEGATED_NAME = "我会牢牢记住你"
MEMORY_ACTIONS = frozenset({"memory.query", "memory.sources", "memory.proposal.submit", "memory.operation.get",
                            "memory.forget", "memory.query.status", "memory.experience.save", "memory.legacy.query"})


@dataclass
class FakeOwner:
    """一个可替换的记忆归属端口：只回答判定，别的什么都不做。"""

    value: MemoryOwnership

    def state(self) -> MemoryOwnership:
        return self.value


class BrokenOwner:
    """判定本身坏掉的端口：调用方必须倒向「V2 自己来」，而不是把异常抛给对话。"""

    def state(self) -> MemoryOwnership:
        raise RuntimeError("owner probe exploded")


def external(name=DELEGATED_NAME) -> FakeOwner:
    return FakeOwner(MemoryOwnership(owner=RENDER_EXTERNAL, reason=REASON_EXTERNAL_ACTIVE,
                                     provider=MEMORY_OWNER_PLUGIN_ID, display_name=name))


class FakeStar:
    def __init__(self, module, *, activated=True, name="MemoryCompanion"):
        self.name = name
        self.module = module
        self.module_path = "data.plugins.astrbot_plugin_remember_you.main"
        self.root_dir_name = "astrbot_plugin_remember_you"
        self.activated = activated


class FakeRegistryContext:
    """只实现探测真正用到的那一个只读方法。"""

    def __init__(self, stars=()):
        self.stars = list(stars)

    def get_all_stars(self):
        return list(self.stars)


class FakeModule:
    def __init__(self, handle):
        self._handle = handle

    def get_active_bridge(self):
        return self._handle

    def get_memory_companion_bridge(self):
        return self._handle


class FakeBridge:
    def __init__(self, *, active=True):
        self._active = active

    def bridge_lifecycle_status(self):
        return {"active": self._active}


class FakeSession:
    def __init__(self, application):
        self.application = application


class FakeController:
    def __init__(self, application):
        self.session = FakeSession(application)
        self.last_session = None
        self._terminated = False


def install_memory_module(module) -> str:
    """把合成模块放进 ``sys.modules``：探测要读到的是插件自己公开的声明。"""

    name = MEMORY_OWNER_MODULE_NAMES[0]
    sys.modules[name] = module
    return name


class MemoryOwnershipTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    """端到端：归属判定真的改变了注入与工具目录。"""

    def host_with(self, owner, **changes):
        return self.host(memory_owner=owner, **changes)

    def seed_private_fact(self, app, batch):
        """在事件被受理之后、生成之前写入一条私聊事实。

        写入走的是 V2 自己的记忆域，所以它同时证明「让位只关表达、不关存储」：
        同一个库在两种归属下都在长。
        """

        context = self.context(app, batch)
        row = app.runtime.read_one(
            "SELECT event_json FROM conversation_inputs WHERE batch_id = ? ORDER BY event_key LIMIT 1", (batch,))
        source = codec.source(json.loads(row["event_json"])["source"])
        evidence = app.memory.writer.access.capture(context, source)
        return app.memory.writer.submit(context, Proposal(
            "add", predicate="饮用偏好", object="不喝咖啡", representation="用户明确说自己不喝咖啡。",
            evidence=(evidence,), single_value=True), operation_key="ownership-seed").unwrap()

    def read_private_fact(self, app, batch, receipt):
        return app.memory.writer.read(self.context(app, batch), AtomRef(receipt["atom_id"], 1)).unwrap()

    def envelope(self, app, batch):
        """这一个批次真正交给模型的那次调用（提示词材料与工具清单都在里面）。"""

        row = app.runtime.read_one(
            "SELECT event_json FROM conversation_inputs WHERE batch_id = ? ORDER BY event_key LIMIT 1", (batch,))
        event_id = json.loads(row["event_json"])["event_id"]
        envelope = next((item for item in self.called if item.original.event.event_id == event_id), None)
        self.assertIsNotNone(envelope, "the batch never reached the model")
        return envelope

    async def complete(self, app, batch):
        self.advance(app)
        return await self.done(app, batch)

    # ── 1. 牢插件在：注入与工具都让位 ───────────────────────────────

    async def test_present_memory_plugin_yields_both_injection_and_tools(self):
        host = await self.start(self.host_with(external()))
        app = host.application

        first = host.receive(self.event("seed", text="记住，我不喝咖啡")).unwrap()
        saved = self.seed_private_fact(app, first)
        self.assertEqual((await self.complete(app, first))["state"], "complete")

        second = host.receive(self.event("after", text="我平时喝咖啡吗？")).unwrap()
        self.assertEqual((await self.complete(app, second))["state"], "complete")

        # 事实确实在 V2 库里（让位不关存储），但这一轮的提示词里没有它。
        self.assertEqual(self.read_private_fact(app, second, saved)["object"], "不喝咖啡")
        envelope = self.envelope(app, second)
        self.assertFalse([item for item in envelope.original.context.materials if item.owner == "memory"],
                         "delegation must not inject any memory material")
        self.assertNotIn(MEMORY_INSTRUCTION, envelope.original.instruction)

        # 交给模型的那份工具清单里一个 memory.* 都没有。
        self.assertEqual([tool.action for tool in envelope.tools if tool.action.startswith("memory.")], [])

    # ── 2. 牢插件不在：和以前完全一样 ───────────────────────────────

    async def test_absent_memory_plugin_keeps_injection_and_tools(self):
        host = await self.start(self.host_with(DEFAULT_MEMORY_OWNER))
        app = host.application

        first = host.receive(self.event("seed", text="记住，我不喝咖啡")).unwrap()
        self.seed_private_fact(app, first)
        self.assertEqual((await self.complete(app, first))["state"], "complete")

        second = host.receive(self.event("after", text="我平时喝咖啡吗？")).unwrap()
        self.assertEqual((await self.complete(app, second))["state"], "complete")

        envelope = self.envelope(app, second)
        material = next(item for item in envelope.original.context.materials if item.purpose == "current_memory")
        self.assertEqual(json.loads(material.payload)["object"], "不喝咖啡")
        self.assertIn(MEMORY_INSTRUCTION, envelope.original.instruction)
        self.assertEqual({tool.action for tool in envelope.tools if tool.action.startswith("memory.")}, MEMORY_ACTIONS)

    # ── 3. 牢插件在但句柄坏了：V2 立刻恢复，并且说得出来 ─────────────

    async def test_unreadable_handle_resumes_v2_memory_and_reports_the_fallback(self):
        host = await self.start(self.host_with(FakeOwner(MemoryOwnership(
            owner=RENDER_SELF, reason=REASON_HANDLE_UNREADABLE,
            provider=MEMORY_OWNER_PLUGIN_ID, display_name=DELEGATED_NAME))))
        app = host.application

        first = host.receive(self.event("seed", text="记住，我不喝咖啡")).unwrap()
        self.seed_private_fact(app, first)
        self.assertEqual((await self.complete(app, first))["state"], "complete")

        second = host.receive(self.event("after", text="我平时喝咖啡吗？")).unwrap()
        self.assertEqual((await self.complete(app, second))["state"], "complete")

        envelope = self.envelope(app, second)
        material = next(item for item in envelope.original.context.materials if item.purpose == "current_memory")
        self.assertEqual(json.loads(material.payload)["object"], "不喝咖啡")
        self.assertTrue({tool.action for tool in envelope.tools if tool.action.startswith("memory.")})
        # 页面要说得出「现在是 V2 在提供、为什么」，而不是两边都沉默。
        state = app.memory.context.ownership()
        self.assertEqual((state["owner"], state["reason"]), (RENDER_SELF, REASON_HANDLE_UNREADABLE))
        self.assertEqual(state["display_name"], DELEGATED_NAME)

    async def test_disabled_plugin_handle_never_goes_silent(self):
        """装了牢插件、但它的桥没启用时，V2 必须自己上，并且说得出来。

        这是本次唯一不可接受的故障形态：两边都不注入、也没人说明。这里让探测器
        真的读到一个 ``get_active_bridge() is None`` 的声明，端到端确认 V2 立刻
        恢复自己的注入与工具，归属接口报的是「声明被停用」。
        """

        name = install_memory_module(FakeModule(None))
        self.addCleanup(sys.modules.pop, name, None)
        self.assertFalse(delegated_memory(memory_owner_provider(None)))
        own_host = await self.start(self.host_with(memory_owner_provider(None)))
        own_app = own_host.application
        batch = own_host.receive(self.event("fallback", text="我平时喝咖啡吗？")).unwrap()
        self.advance(own_app)
        await self.done(own_app, batch)
        envelope = self.envelope(own_app, batch)
        self.assertTrue({tool.action for tool in envelope.tools if tool.action.startswith("memory.")})
        self.assertIn(MEMORY_INSTRUCTION, envelope.original.instruction)
        state = own_app.memory.context.ownership()
        self.assertEqual((state["owner"], state["reason"]), (RENDER_SELF, REASON_HANDLE_DISABLED))
        self.assertEqual(state["display_name"], DELEGATED_NAME)
        self.assertEqual(await PageWebAPI(FakeController(own_app)).ownership(),
                         {"status": "ok", "message": "", "data": state})

    async def test_a_broken_probe_never_silences_both_sides(self):
        """判定端口自己抛异常时也只能倒向「V2 自己来」。"""

        self.assertFalse(delegated_memory(BrokenOwner()))
        self.assertFalse(delegated_memory(None))
        self.assertEqual(SelfMemoryOwner().state().owner, RENDER_SELF)

    # ── 4. 工具门断言真实清单（不是标志） ────────────────────────────

    async def test_tool_catalog_omits_memory_only_while_delegated(self):
        delegated_host = await self.start(self.host_with(external()))
        delegated_app = delegated_host.application
        batch = delegated_host.receive(self.event("tools", text="随便聊聊")).unwrap()
        self.advance(delegated_app)
        await self.done(delegated_app, batch)
        delegated_context = self.context(delegated_app, batch)
        self.assertEqual([tool.action for tool in delegated_app.tools.catalog_for(delegated_context)
                          if tool.action.startswith("memory.")], [])

    async def test_tool_catalog_keeps_memory_when_v2_owns_it(self):
        own_host = await self.start(self.host_with(DEFAULT_MEMORY_OWNER,
                                               runtime=self.open_runtime()))
        own_app = own_host.application
        batch = own_host.receive(self.event("tools", text="随便聊聊")).unwrap()
        self.advance(own_app)
        await self.done(own_app, batch)
        own_context = self.context(own_app, batch)
        self.assertEqual({tool.action for tool in own_app.tools.catalog_for(own_context)
                          if tool.action.startswith("memory.")}, MEMORY_ACTIONS)

    async def test_delegated_snapshot_records_no_references(self):
        host = await self.start(self.host_with(external()))
        app = host.application
        batch = host.receive(self.event("snapshot", text="随口的确认")).unwrap()
        self.advance(app)
        await self.done(app, batch)
        row = app.runtime.read_one("SELECT contributions_json FROM conversation_batches WHERE batch_id = ?", (batch,))
        snapshot = json.loads(row["contributions_json"])["memory_context"]
        self.assertEqual(snapshot["state"], DELEGATED_STATE)
        self.assertEqual(snapshot["references"], [])
        self.assertEqual(snapshot["handles"], [])
        self.assertEqual(snapshot["delegated_to"], DELEGATED_NAME)

    # ── 5. 页面文案与归属接口 ────────────────────────────────────────

    async def test_ownership_page_copy_names_the_plugin_and_never_claims_v2_owns_memory(self):
        delegated_host = await self.start(self.host_with(external()))
        delegated_app = delegated_host.application
        document = delegated_app.memory.context.ownership()
        self.assertEqual(document["owner"], RENDER_EXTERNAL)
        self.assertEqual(document["display_name"], DELEGATED_NAME)
        self.assertEqual(document["provider"], MEMORY_OWNER_PLUGIN_ID)
        self.assertEqual(await PageWebAPI(FakeController(delegated_app)).ownership(),
                         {"status": "ok", "message": "", "data": document})

        page = (PAGE / "memory-page.js").read_text(encoding="utf-8")
        # 归属是后端判定的原文：页面必须用它的字段说话，而不是把名字写死。
        self.assertIn("data.ownership", page)
        for field in ("owner", "reason", "display_name"):
            self.assertIn(field, page)
        self.assertIn("长期记录：〈", page)
        self.assertIn("长期记录：本插件事实库", page)
        # 旧的徽标文案读的是「这一页连不连得上」，却写成「记忆读取正常」——
        # 必须收窄到本插件自己的事实库。
        self.assertNotIn("记忆读取正常", page)
        self.assertIn("事实库读取正常", page)

    async def test_ownership_endpoint_reports_v2_when_nothing_else_is_loaded(self):
        own_host = await self.start(self.host_with(DEFAULT_MEMORY_OWNER,
                                               runtime=self.open_runtime()))
        own_app = own_host.application
        state = own_app.memory.context.ownership()
        self.assertEqual(state["owner"], RENDER_SELF)
        self.assertIn(state["reason"], {REASON_MODULE_ABSENT, REASON_DECLARATION_ABSENT})
        self.assertEqual(await PageWebAPI(FakeController(own_app)).ownership(),
                         {"status": "ok", "message": "", "data": state})

    async def test_ownership_route_degrades_explicitly(self):
        route, handler, methods, description = next(
            item for item in PageWebAPI(FakeController(None)).routes() if item[0].endswith("/page/ownership"))
        self.assertEqual(methods, ["GET"])
        self.assertTrue(description)
        self.assertFalse(route.endswith(MEMORY_PREFIX))

        class Terminated(FakeController):
            def __init__(self):
                super().__init__(None)
                self._terminated = True

        class Unloaded(FakeController):
            def __init__(self):
                super().__init__(None)
                self.session = None

        cases = ((Terminated(), "staging_host_terminated"), (Unloaded(), "staging_not_initialized"))
        for controller, code in cases:
            response = await PageWebAPI(controller).ownership()
            self.assertEqual(response["status"], "error")
            self.assertEqual(response["data"]["code"], code)

    # ── 能力级处置与数据级事实必须同时成立 ──────────────────────────

    def test_legacy_catalog_keeps_the_data_fact_and_adds_the_capability_disposition(self):
        entry = LEGACY_COMPATIBILITY_CATALOG.entry("memory.timeline_and_governance")
        self.assertEqual(entry.action, CatalogAction.DATA_CONVERTED)
        self.assertEqual(entry.handler_id, "v2.memory-snapshot-archive")
        self.assertEqual(entry.source_plugin_id, MEMORY_OWNER_PLUGIN_ID)
        self.assertEqual(validate_capability_dispositions(), ())
        disposition, = CAPABILITY_DISPOSITIONS
        self.assertEqual(disposition.capability_id, "memory")
        self.assertEqual(disposition.provider_plugin_id, MEMORY_OWNER_PLUGIN_ID)
        self.assertEqual(disposition.provider_display_name, DELEGATED_NAME)
        self.assertEqual(disposition.disposition, CatalogAction.RETAINED_EXTERNAL)
        # 能力级记录不进迁移发现：那是一条数据在哪的报告，不是能力归属。
        self.assertNotIn(disposition.capability_id,
                         {item.source_id for item in LEGACY_COMPATIBILITY_CATALOG.entries})


class MemoryOwnerDetectionTests(unittest.TestCase):
    """探测器只读牢插件自己公开的声明，并且每一步都倒向「V2 自己来」。"""

    def setUp(self):
        self.original = {name: sys.modules.get(name) for name in MEMORY_OWNER_MODULE_NAMES}
        for name in MEMORY_OWNER_MODULE_NAMES:
            sys.modules.pop(name, None)
        self.addCleanup(self.restore)

    def restore(self):
        for name, module in self.original.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_declared_and_serving_handle_owns_memory(self):
        module = FakeModule(FakeBridge())
        install_memory_module(module)
        state = read_memory_ownership(None)
        self.assertEqual(state.owner, RENDER_EXTERNAL)
        self.assertEqual(state.reason, REASON_EXTERNAL_ACTIVE)
        self.assertEqual(state.provider, MEMORY_OWNER_PLUGIN_ID)
        self.assertEqual(state.display_name, DELEGATED_NAME)

    def test_absent_module_leaves_memory_to_v2(self):
        state = read_memory_ownership(FakeRegistryContext())
        self.assertEqual((state.owner, state.reason), (RENDER_SELF, REASON_MODULE_ABSENT))

    def test_disabled_bridge_handle_leaves_memory_to_v2(self):
        # 声明自己在「插件在但桥没启用」时返回 None——这正是配置
        # private_companion_bridge.enabled=false 时的形态。
        install_memory_module(FakeModule(None))
        state = read_memory_ownership(None)
        self.assertEqual((state.owner, state.reason), (RENDER_SELF, REASON_HANDLE_DISABLED))
        self.assertEqual(state.display_name, DELEGATED_NAME)

    def test_accessor_that_raises_reports_an_unreadable_handle(self):
        class Exploding(FakeModule):
            def get_active_bridge(self):
                raise RuntimeError("dependency missing")

            get_memory_companion_bridge = get_active_bridge

        install_memory_module(Exploding(None))
        state = read_memory_ownership(None)
        self.assertEqual((state.owner, state.reason), (RENDER_SELF, REASON_HANDLE_UNREADABLE))

    def test_deactivated_handle_is_not_treated_as_serving(self):
        install_memory_module(FakeModule(FakeBridge(active=False)))
        state = read_memory_ownership(None)
        self.assertEqual((state.owner, state.reason), (RENDER_SELF, REASON_HANDLE_UNREADABLE))

    def test_module_without_the_declaration_is_reported_as_such(self):
        class Bare:
            pass

        install_memory_module(Bare())
        state = read_memory_ownership(None)
        self.assertEqual((state.owner, state.reason), (RENDER_SELF, REASON_DECLARATION_ABSENT))

    def test_registry_deactivation_wins_over_a_leftover_module(self):
        """被停用的插件可能还挂在 ``sys.modules`` 上，注册表说停用就按停用算。"""

        module = FakeModule(FakeBridge())
        install_memory_module(module)
        context = FakeRegistryContext([FakeStar(module, activated=False)])
        state = read_memory_ownership(context)
        self.assertEqual((state.owner, state.reason), (RENDER_SELF, REASON_HANDLE_DISABLED))

    def test_registry_declaration_is_read_from_the_loaded_module(self):
        module = FakeModule(FakeBridge())
        context = FakeRegistryContext([FakeStar(module)])
        state = read_memory_ownership(context)
        self.assertEqual(state.owner, RENDER_EXTERNAL)

    def test_provider_returns_the_same_document_shape_the_page_reads(self):
        install_memory_module(FakeModule(FakeBridge()))
        document = memory_owner_provider(None).state().document()
        self.assertEqual(set(document), {"owner", "reason", "module", "provider", "display_name"})
        self.assertTrue(document["module"].endswith("astrbot_plugin_remember_you.main"))
