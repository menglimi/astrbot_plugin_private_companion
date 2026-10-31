"""记忆联动：V2 完成的日记、梦境与主动消息真的到达「我会牢牢记住你」。

V2 的记忆表达让位给牢插件（见 ``tests/test_memory_ownership.py``），但记录的**产生**
仍然在 V2 自己的领域里。这一组测的就是那条单向链路，而且每一条都对应一个真出现过的
故障形态：

1. 写完的日记没进牢插件 → 用户在那边问「你昨天写了什么」，它答不出来。
2. 做完的梦没进牢插件 → 梦境连续性断在 V2 自己的库里。
3. 发出去的主动消息没进牢插件 → 角色否认自己主动说过的话。
4. 牢插件不在 → 日记**照样写成**，而且一次都不去碰它。
5. 牢插件的桥坏了（写库抛异常）→ 日记**照样写成**，也不能因此丢掉这次书写。
6. 同一条记录推两次 → 只给一个 ``memory_id``：牢插件按 ``memory_id`` 更新同一条
   记忆（``core/store.py`` 的 ``_insert_memory_sync`` 是 ``ON CONFLICT(id) DO UPDATE``），
   所以重复推送不能变成两条记忆，两条不同记录也不能互相覆盖。

假桥记录的是**真实参数**（方法名、正文、每个关键字），断言不看布尔标志：只写一个
「推送过」的开关，上面六条里有四条都能通过而实际什么都没写对。
"""

from __future__ import annotations

import json
import sys
import unittest

from companion_v2.adapters.astrbot.memory_owner import MEMORY_OWNER_MODULE_NAMES, read_memory_owner
from companion_v2.adapters.astrbot.memory_push import PUSH_SOURCE_PLUGIN, memory_push_provider
from companion_v2.capabilities.memory_push import (MEMORY_PUSH_ID_PREFIX, PUSH_PERSONA_LIFE,
    PUSH_PROACTIVE_MESSAGE, MemoryPushRecord, push_memory_id)
from companion_v2.capabilities.ownership import (REASON_DECLARATION_ABSENT, REASON_EXTERNAL_ACTIVE,
    REASON_HANDLE_DISABLED, REASON_HANDLE_UNREADABLE, REASON_MODULE_ABSENT, RENDER_EXTERNAL, RENDER_SELF)
from companion_v2.configuration.registry import FieldRegistry
from companion_v2.execution.models import TaskContext, digest
from companion_v2.foundation.refs import AudienceRef, RoleOwnerRef
from companion_v2.model_gateway.routing import configuration_fields
from companion_v2.role_work.journal import JournalContext, JournalKey, JournalKind, RoleJournalService
from companion_v2.role_work.journal.writing import writing_profiles
from tests.proactive_support import ContactFixture
from tests.test_host_application import HostFixture
from tests.test_management_access import ROLE, SCOPE
from tests.test_role_tool_loop import answered


class RecordingBridge:
    """牢插件的桥：只把每一次写入的正文与关键字原样记下来。

    ``fail`` 里的方法会抛异常，用来证明「桥坏了也不影响 V2 的领域动作」。
    """

    def __init__(self, *, active=True, fail=()):
        self._active = active
        self._fail = frozenset(fail)
        self.calls = []

    def bridge_lifecycle_status(self):
        return {"active": self._active}

    async def record_persona_life(self, *, content, **kwargs):
        return await self._record("record_persona_life", content, kwargs)

    async def record_proactive_message(self, *, content, **kwargs):
        return await self._record("record_proactive_message", content, kwargs)

    async def _record(self, method, content, kwargs):
        self.calls.append({"method": method, "content": content, "kwargs": kwargs})
        if method in self._fail:
            raise RuntimeError("memory plugin write failed")
        return "memory-" + str(len(self.calls))

    def only(self, method):
        calls = [call for call in self.calls if call["method"] == method]
        self.assert_one(calls, method)
        return calls[0]

    @staticmethod
    def assert_one(calls, method):
        if len(calls) != 1:
            raise AssertionError("expected exactly one " + method + " call, got " + str(len(calls)))


class FakeModule:
    """牢插件自己公开的声明：只提供两个取句柄函数。"""

    def __init__(self, handle):
        self._handle = handle

    def get_memory_companion_bridge(self):
        return self._handle

    def get_active_bridge(self):
        return self._handle


class FakeStar:
    def __init__(self, module, *, activated=True):
        self.name = "MemoryCompanion"
        self.module = module
        self.module_path = MEMORY_OWNER_MODULE_NAMES[0]
        self.root_dir_name = "astrbot_plugin_remember_you"
        self.activated = activated


class FakeRegistryContext:
    def __init__(self, stars=()):
        self.stars = list(stars)

    def get_all_stars(self):
        return list(self.stars)


class RecordingPort:
    """假端口：把生产者真正交出来的那条记录留在手里。"""

    def __init__(self):
        self.records = []

    async def push(self, record):
        self.records.append(record)
        return None


class MemoryPluginDeclaration:
    """装／卸牢插件声明的 mixin：探测与推送必须读同一份 ``sys.modules``。"""

    def setUp(self):
        self._declared = {name: sys.modules.get(name) for name in MEMORY_OWNER_MODULE_NAMES}
        for name in MEMORY_OWNER_MODULE_NAMES:
            sys.modules.pop(name, None)
        super().setUp()
        self.addCleanup(self._restore_declaration)

    def _restore_declaration(self):
        for name, module in self._declared.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def live_plugin(self, *, active=True, fail=()):
        """把一份「装好并且在服务」的声明放进 ``sys.modules``，返回它的桥。"""

        bridge = RecordingBridge(active=active, fail=fail)
        sys.modules[MEMORY_OWNER_MODULE_NAMES[0]] = FakeModule(bridge)
        return bridge


class MemoryPushPortTests(MemoryPluginDeclaration, unittest.IsolatedAsyncioTestCase):
    """端口本身：只有活句柄才写，而且重复推送只给一个身份。"""

    @staticmethod
    def record(**changes):
        fields = {"memory_id": push_memory_id(PUSH_PERSONA_LIFE, "journal", "identity", 1),
                  "content": "Bot 在 2026-09-21 写下了自己的日记（随记）：今天读到第三章。",
                  "kind": PUSH_PERSONA_LIFE, "scope": "unknown",
                  "session_id": "private_companion_v2:journal:diary", "occurred_at": "2026-09-21T12:00:00+00:00",
                  "tags": ("journal", "diary", "persona_life"), "metadata": (("local_date", "2026-09-21"),)}
        fields.update(changes)
        return MemoryPushRecord(**fields)

    async def test_the_same_record_pushed_twice_sends_one_identity_and_one_content(self):
        bridge = self.live_plugin()
        port = memory_push_provider(None)
        record = self.record()

        self.assertIsNotNone(await port.push(record))
        self.assertIsNotNone(await port.push(record))

        self.assertEqual([call["kwargs"]["memory_id"] for call in bridge.calls],
                         [record.memory_id, record.memory_id])
        self.assertEqual([call["content"] for call in bridge.calls], [record.content, record.content])
        self.assertEqual([call["kwargs"]["source_plugin"] for call in bridge.calls],
                         [PUSH_SOURCE_PLUGIN, PUSH_SOURCE_PLUGIN])

    async def test_two_different_records_never_collide(self):
        self.live_plugin()
        port = memory_push_provider(None)
        first, second = self.record(), self.record(
            memory_id=push_memory_id(PUSH_PERSONA_LIFE, "journal", "identity", 2),
            content="Bot 在 2026-09-22 写下了自己的日记（随记）：今天什么都没写。")

        await port.push(first)
        await port.push(second)

        self.assertNotEqual(first.memory_id, second.memory_id)

    async def test_an_absent_declaration_is_never_written(self):
        port = memory_push_provider(None)
        self.assertIsNone(await port.push(self.record()))
        state = read_memory_owner(None)
        self.assertEqual((state.ownership.reason, state.handle), (REASON_MODULE_ABSENT, None))

    async def test_a_handle_that_is_not_serving_is_never_written(self):
        """句柄为空时算「装了但关着」，句柄自报停了算「读不到」——两种都不写。"""

        port = memory_push_provider(None)
        sys.modules[MEMORY_OWNER_MODULE_NAMES[0]] = FakeModule(None)
        self.assertIsNone(await port.push(self.record()))
        self.assertEqual(read_memory_owner(None).ownership.reason, REASON_HANDLE_DISABLED)

        stopped = self.live_plugin(active=False)
        self.assertIsNone(await port.push(self.record()))
        self.assertEqual(read_memory_owner(None).ownership.reason, REASON_HANDLE_UNREADABLE)
        self.assertEqual(stopped.calls, [])

        class Bare:
            """模块在，但没有取句柄函数：老版本或根本是别的插件。"""

        sys.modules[MEMORY_OWNER_MODULE_NAMES[0]] = Bare()
        self.assertIsNone(await port.push(self.record()))
        self.assertEqual(read_memory_owner(None).ownership.reason, REASON_DECLARATION_ABSENT)

    async def test_a_plugin_the_registry_deactivated_is_never_written(self):
        bridge = self.live_plugin()
        port = memory_push_provider(FakeRegistryContext([FakeStar(FakeModule(bridge), activated=False)]))
        self.assertIsNone(await port.push(self.record()))
        self.assertEqual(bridge.calls, [])

    async def test_a_live_handle_still_serves_the_ownership_verdict(self):
        self.live_plugin()
        owner = read_memory_owner(None)
        self.assertEqual((owner.ownership.owner, owner.ownership.reason),
                         (RENDER_EXTERNAL, REASON_EXTERNAL_ACTIVE))
        self.assertIsNotNone(owner.handle)


class JournalMemoryPushTests(MemoryPluginDeclaration, HostFixture, unittest.IsolatedAsyncioTestCase):
    """真日记、真任务、真存储：只有牢插件的声明是合成的。"""

    def setUp(self):
        super().setUp()
        self.profiles += writing_profiles("primary", max_output_tokens=100)
        self.registry = FieldRegistry(version="host.journal.memory.v1", fields=configuration_fields(self.profiles))
        self.owner = RoleOwnerRef(SCOPE.installation_id, SCOPE.bot_id, ROLE.subject_id)

    async def setup_writer(self, *, memory_push=None):
        port = memory_push_provider(None) if memory_push is None else memory_push
        self.host_instance = await self.start(self.host(memory_push=port))
        self.app = self.host_instance.application
        self.writer = self.app.journal_writer
        self.trigger = self.host_instance.inbound.ingest(self.event("journal-trigger"), capability="text").unwrap()
        self.task_context = TaskContext(self.important, self.trigger.runtime_scope.persona,
                                        AudienceRef(SCOPE, (self.important.subject,)))

    def target(self, day="2026-09-21", kind=JournalKind.DIARY):
        return JournalContext(JournalKey(self.owner, kind, day), SCOPE, "Asia/Shanghai")

    def diary(self, body="今天读到第三章。\n\n有个问题还没想通。"):
        async def model(envelope):
            return answered(json.dumps(dict(body=body, form="随记", recall=None, afterglow=""), ensure_ascii=False))
        self.model_behavior = model

    def dream(self, body="", afterglow="只记得有一点轻松，梦已经想不起来。", recall="afterglow"):
        async def model(envelope):
            return answered(json.dumps(dict(body=body, form="梦记", recall=recall, afterglow=afterglow),
                                         ensure_ascii=False))
        self.model_behavior = model

    def derivation(self):
        async def model(envelope):
            return answered(json.dumps({"summary": "读了第三章。", "continuity": "", "dream_fragments": []},
                                       ensure_ascii=False))
        self.model_behavior = model

    async def write(self, *, target=None, request="write"):
        return await self.writer.write(self.task_context, self.trigger, target or self.target(),
            role_instruction="保留的角色人格", request_id=request)

    def stored(self):
        """重新打开这个库读回那条日记：提交是持久的，不是内存里的假象。"""

        return RoleJournalService(self.open_runtime(), now=self.clock.now).get(self.target().key)

    def expected_diary_identity(self, key, revision):
        """独立复述一遍身份构成：只取角色/日期/种类与修订号，不掺时间也不掺随机。"""

        return f"{MEMORY_PUSH_ID_PREFIX}_{PUSH_PERSONA_LIFE}_" + digest(
            [MEMORY_PUSH_ID_PREFIX, PUSH_PERSONA_LIFE, "journal", key, revision])

    # ── 1. 写完的日记到达牢插件 ─────────────────────────────────────

    async def test_a_completed_diary_reaches_the_memory_owner(self):
        bridge = self.live_plugin()
        await self.setup_writer()
        self.diary()

        task = (await self.write()).unwrap().task

        self.assertEqual((task.state, task.effect), ("succeeded", "committed"))
        call = bridge.only("record_persona_life")
        self.assertIn("今天读到第三章。", call["content"])
        self.assertIn("2026-09-21", call["content"])
        self.assertIn("随记", call["content"])
        entry = self.stored()
        self.assertEqual(call["kwargs"]["memory_id"], self.expected_diary_identity(entry.reference.key, entry.reference.revision))
        self.assertEqual(call["kwargs"]["source_plugin"], PUSH_SOURCE_PLUGIN)
        self.assertEqual(call["kwargs"]["scope"], "unknown")
        self.assertEqual(call["kwargs"]["session_id"], "private_companion_v2:journal:diary")
        self.assertEqual(call["kwargs"]["tags"], ["journal", "diary", "persona_life"])
        self.assertEqual(call["kwargs"]["metadata"]["journal_kind"], "diary")
        self.assertEqual(call["kwargs"]["metadata"]["local_date"], "2026-09-21")
        self.assertEqual(call["kwargs"]["metadata"]["journal_revision"], entry.reference.revision)
        self.assertEqual(call["kwargs"]["occurred_at"], entry.updated_at.isoformat())

    # ── 2. 做完的梦到达牢插件，而且与同一天的日记不撞身份 ───────────

    async def test_a_completed_dream_reaches_the_memory_owner_as_its_own_record(self):
        bridge = self.live_plugin()
        await self.setup_writer()
        self.diary()
        diary_task = (await self.write()).unwrap().task
        self.dream(body="我记得一段通向云里的窄楼梯。", recall="fragments", afterglow="醒来还觉得轻松。")
        dream_target = self.target("2026-09-21", JournalKind.DREAM)

        dream_task = (await self.write(target=dream_target, request="dream")).unwrap().task

        self.assertEqual((diary_task.state, dream_task.state), ("succeeded", "succeeded"))
        self.assertEqual([call["method"] for call in bridge.calls],
                         ["record_persona_life", "record_persona_life"])
        dream = bridge.calls[-1]
        self.assertIn("窄楼梯", dream["content"])
        self.assertIn("醒来还觉得轻松。", dream["content"])
        self.assertEqual(dream["kwargs"]["metadata"]["journal_kind"], "dream")
        self.assertEqual(dream["kwargs"]["session_id"], "private_companion_v2:journal:dream")
        self.assertEqual(dream["kwargs"]["tags"], ["journal", "dream", "persona_life"])
        self.assertNotEqual(bridge.calls[0]["kwargs"]["memory_id"], dream["kwargs"]["memory_id"])

    # ── 3. 发出去的主动消息到达牢插件 ───────────────────────────────

    async def test_a_withheld_diary_never_reaches_the_memory_owner(self):
        """派生材料不是「角色又过了一天」：它不该在记忆库里变成第二种说法。"""

        bridge = self.live_plugin()
        await self.setup_writer()
        self.diary()
        task = (await self.write()).unwrap().task
        self.assertEqual(task.state, "succeeded")
        entry = self.stored()
        self.derivation()

        derived = await self.writer.derive(self.task_context, self.trigger, entry.reference,
            role_instruction="保留的角色人格", request_id="derive")

        self.assertEqual(derived.unwrap().task.state, "succeeded")
        self.assertEqual([call["method"] for call in bridge.calls], ["record_persona_life"])

    # ── 4. 牢插件不在：日记照样写成，一次都不去碰它 ─────────────────

    async def test_an_absent_memory_plugin_still_completes_the_diary(self):
        await self.setup_writer()
        self.diary()
        state = read_memory_owner(None)
        self.assertEqual((state.ownership.owner, state.ownership.reason, state.handle),
                         (RENDER_SELF, REASON_MODULE_ABSENT, None))

        task = (await self.write()).unwrap().task

        self.assertEqual((task.state, task.effect), ("succeeded", "committed"))
        self.assertIn("今天读到第三章。", self.stored().draft.body)

    async def test_a_stopped_memory_plugin_is_never_pushed_to(self):
        """声明在、句柄在，但它自报已经停了：一次都不写它。"""

        stopped = self.live_plugin(active=False)
        await self.setup_writer()
        self.diary()

        task = (await self.write()).unwrap().task

        self.assertEqual(task.state, "succeeded")
        self.assertEqual(stopped.calls, [])
        self.assertIn("今天读到第三章。", self.stored().draft.body)

    # ── 5. 桥坏了：日记照样写成 ─────────────────────────────────────

    async def test_a_failing_memory_plugin_write_never_withholds_the_diary(self):
        broken = self.live_plugin(fail=("record_persona_life",))
        await self.setup_writer()
        self.diary()

        task = (await self.write()).unwrap().task

        # 试过了（说明没被静默跳过），但写库失败既没回滚日记也没让任务失败。
        self.assertEqual([call["method"] for call in broken.calls], ["record_persona_life"])
        self.assertEqual((task.state, task.effect), ("succeeded", "committed"))
        self.assertIn("今天读到第三章。", self.stored().draft.body)

    # ── 6. 同一条记录推两次只给一个身份，正文不变 ───────────────────

    async def test_the_same_diary_keeps_one_identity_across_pushes(self):
        bridge = self.live_plugin()
        captured = RecordingPort()
        await self.setup_writer(memory_push=captured)
        self.diary()
        task = (await self.write()).unwrap().task
        self.assertEqual(task.state, "succeeded")
        record, = captured.records
        self.assertIsNotNone(record.context)
        self.assertEqual(record.context.scope, SCOPE)

        port = memory_push_provider(None)
        await port.push(record)
        await port.push(record)

        self.assertEqual([call["kwargs"]["memory_id"] for call in bridge.calls],
                         [record.memory_id, record.memory_id])
        self.assertEqual([call["content"] for call in bridge.calls], [record.content, record.content])
        entry = self.stored()
        self.assertEqual(record.memory_id, self.expected_diary_identity(entry.reference.key, entry.reference.revision))
        self.assertEqual(len({call["kwargs"]["memory_id"] for call in bridge.calls}), 1)


class ProactiveMemoryPushTests(MemoryPluginDeclaration, ContactFixture, unittest.IsolatedAsyncioTestCase):
    """真机会、真决定、真投递：主动消息说出去之后才写进记忆。"""

    def visible(self):
        return "想起你上次提到的书，读到哪里了？"

    async def enabled_with_memory_plugin(self, bridge_active=True, fail=()):
        bridge = self.live_plugin(active=bridge_active, fail=fail)
        app = await self.enabled(memory_push=memory_push_provider(None))
        return app, bridge

    def contact_id(self, app):
        row = app.runtime.read_one("SELECT contact_id FROM proactive_contacts ORDER BY created_at DESC LIMIT 1", ())
        return row["contact_id"]

    async def test_a_completed_proactive_message_reaches_the_memory_owner(self):
        app, bridge = await self.enabled_with_memory_plugin()

        task = (await self.contact(app)).unwrap().task

        self.assertEqual((task.state, task.effect), ("succeeded", "committed"))
        call = bridge.only("record_proactive_message")
        self.assertIn(self.visible(), call["content"])
        self.assertEqual(call["kwargs"]["memory_id"],
                         push_memory_id(PUSH_PROACTIVE_MESSAGE, "proactive", self.contact_id(app)))
        self.assertEqual(call["kwargs"]["source_plugin"], PUSH_SOURCE_PLUGIN)
        self.assertEqual(call["kwargs"]["scope"], "private")
        self.assertEqual(call["kwargs"]["platform"], SCOPE.platform)
        self.assertEqual(call["kwargs"]["tags"], ["proactive", "proactive_message", "bot_action"])
        self.assertEqual(call["kwargs"]["metadata"]["clean_visible_text"], self.visible())
        self.assertEqual(call["kwargs"]["metadata"]["event_type"], "proactive_message")
        self.assertEqual(call["kwargs"]["metadata"]["contact_id"], self.contact_id(app))

    async def test_completed_contact_retains_context_for_generic_providers(self):
        port = RecordingPort()
        app = await self.enabled(memory_push=port)
        task = (await self.contact(app)).unwrap().task
        self.assertEqual(task.state, "succeeded")
        record, = port.records
        self.assertIsNotNone(record.context)
        self.assertEqual(record.context.scope, SCOPE)
        self.assertEqual(record.context.role_binding.role, ROLE)

    async def test_a_skipped_opportunity_is_never_written_to_memory(self):
        """模型决定这次不发：什么都没发生，记忆里也不能多出一句话。"""

        app, bridge = await self.enabled_with_memory_plugin()

        async def skipped(envelope):
            return answered(json.dumps({"decision": "skip", "text": ""}, ensure_ascii=False))
        self.model_behavior = skipped

        task = (await self.contact(app)).unwrap().task

        self.assertEqual(task.state, "succeeded")
        self.assertEqual(task.effect, "committed")
        self.assertEqual(bridge.calls, [])
        self.assertEqual(self.sent, [])

    async def test_a_failing_memory_plugin_write_never_withholds_the_contact(self):
        app, bridge = await self.enabled_with_memory_plugin(fail=("record_proactive_message",))

        task = (await self.contact(app)).unwrap().task

        self.assertEqual([call["method"] for call in bridge.calls], ["record_proactive_message"])
        self.assertEqual((task.state, task.effect), ("succeeded", "committed"))
        await self.delivery_state(app, task.task_id, "delivered")
        self.assertEqual(self.sent[0].text, self.visible())


if __name__ == "__main__":
    unittest.main()
