"""Phone task records are the durable state; nothing here starts ADB or a model."""

from __future__ import annotations

import base64
import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from astrbot_host.phone_command import receive_phone_command
from companion_v2.device.action_service import (
    PhoneActionService, PhoneSettings, PhoneTaskRecord, load_settings, phone_profile,
)
from companion_v2.device.adb import AdbClient, UiElement
from companion_v2.device.session import PhoneObservation, PhoneSession
from companion_v2.device.skills import PREFIX as SKILL_PREFIX, PhoneSkillMemory, SkillScreen
from companion_v2.adapters.astrbot.model_wire import build_wire
from companion_v2.configuration.registry import FieldRegistry
from companion_v2.execution.models import TaskContext, TaskError
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import AudienceRef
from companion_v2.model_gateway.routing import configuration_fields
from companion_v2.storage.runtime import StorageRuntime
from tests.test_conversation_images import image_bytes
from tests.test_device_adb import FakeAdb
from tests.test_host_application import HostFixture
from tests.test_management_access import NOW, SCOPE
from tests.test_role_tool_loop import answered


class PhoneSettingsTests(unittest.TestCase):
    def test_missing_file_means_disabled(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertFalse(load_settings(root).enabled)

    def test_subject_allowlist_can_restrict_the_channel(self):
        from types import SimpleNamespace

        settings = PhoneSettings(enabled=True, allowed_subjects=("u-1",))
        context = SimpleNamespace(actor=SimpleNamespace(subject=SimpleNamespace(subject_id="u-1")))
        other = SimpleNamespace(actor=SimpleNamespace(subject=SimpleNamespace(subject_id="u-2")))
        self.assertTrue(settings.permits(context))
        self.assertFalse(settings.permits(other))
        self.assertTrue(PhoneSettings(enabled=True).permits(other))

    def test_explicit_enable_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "phone.json"
            path.write_text(json.dumps({"enabled": True, "serial": "A1", "max_steps": 3}),
                            encoding="utf-8")
            settings = load_settings(root)
            self.assertTrue(settings.enabled)
            self.assertEqual(settings.serial, "A1")
            self.assertEqual(settings.max_steps, 3)
            path.write_text(json.dumps({"enabled": True, "evil": 1}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_settings(root)

    def test_invalid_enabled_type_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "phone.json").write_text('{"enabled": 1}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_settings(root)

    def test_done_and_fail_cannot_be_removed_from_the_allowlist(self):
        with self.assertRaises(ValueError):
            PhoneSettings(enabled=True, allowed=("launch",))

    def test_unicode_input_backend_parses_and_is_validated(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "phone.json").write_text(
                json.dumps({"enabled": True, "unicode_input": "adbkeyboard"}), encoding="utf-8")
            self.assertEqual(load_settings(root).unicode_input, "adbkeyboard")
            (Path(root) / "phone.json").write_text(
                json.dumps({"enabled": True, "unicode_input": "ime"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_settings(root)

    def test_allowlist_parses_and_rejects_unknown_fields(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "phone.json").write_text(json.dumps({
                "enabled": True, "allowed": ["launch", "tap_text", "done", "fail"],
                "allowed_subjects": ["u-1"]}), encoding="utf-8")
            settings = load_settings(root)
            self.assertEqual(settings.allowed, ("launch", "tap_text", "done", "fail"))
            self.assertEqual(settings.allowed_subjects, ("u-1",))
            (Path(root) / "phone.json").write_text(
                json.dumps({"enabled": True, "allowed": ["launch"]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_settings(root)


class PhoneRecordTests(unittest.TestCase):
    def record(self) -> PhoneTaskRecord:
        return PhoneTaskRecord("phone-1", "{}", "打开应用签到", "active", 0, 4, (),
                               "2026-11-21T00:00:00+00:00", "2026-11-21T00:00:00+00:00",
                               "2026-11-21T02:00:00+00:00")

    def test_round_trip_and_optional_fields(self):
        parsed = PhoneTaskRecord.parse(self.record().document())
        self.assertEqual(parsed, self.record())
        with_inflight = PhoneTaskRecord.parse(
            replace(self.record(), inflight_at="2026-11-21T00:01:00+00:00").document())
        self.assertEqual(with_inflight.inflight_at, "2026-11-21T00:01:00+00:00")

    def test_unknown_shape_is_refused(self):
        with self.assertRaises(ValueError):
            PhoneTaskRecord.parse({"version": 1})
        with self.assertRaises(ValueError):
            PhoneTaskRecord.parse({**self.record().document(), "extra": True})


class PhoneServiceStateTests(unittest.TestCase):
    def test_records_are_saved_listed_and_cancelled(self):
        root = tempfile.mkdtemp(prefix="phone-action-test-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        data = Path(root) / "data"
        data.mkdir()
        runtime = StorageRuntime.open(data_root=data, installation_id="phone-test")
        try:
            service = PhoneActionService(runtime, settings_reader=lambda _root: PhoneSettings(enabled=True))
            record = PhoneTaskRecord("phone-1", "{}", "签到", "active", 0, 2, (),
                                     "2026-11-21T00:00:00+00:00", "2026-11-21T00:00:00+00:00",
                                     "2026-11-21T02:00:00+00:00")
            service._save(record)
            self.assertEqual(service._read("phone-1"), record)
            self.assertEqual([item.task_id for item in service._records()], ["phone-1"])
            self.assertTrue(service.cancel("phone-1"))
            self.assertEqual(service._read("phone-1").state, "cancelled")
            self.assertFalse(service.cancel("phone-1"))
        finally:
            runtime.close()


class PhoneCheckoutFlowTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.profiles += (phone_profile("primary", max_output_tokens=100),)
        self.providers = tuple(replace(p, capabilities=("text", "image"))
                               for p in self.providers)
        self.registry = FieldRegistry(version="test-phone", fields=configuration_fields(self.profiles))

    async def test_ordinary_task_reports_result_and_carries_ineffective_tap_to_the_next_task(self):
        app = (await self.start()).application
        context = TaskContext(self.important, self.identity.resolve_role(SCOPE, observed_at=NOW).unwrap(),
                              AudienceRef(SCOPE, (self.important.subject,)))
        service = app.phone_actions
        service._settings_reader = Mock()
        service._settings_reader.read.return_value = PhoneSettings(enabled=True, serial="test-device")
        client = Mock()
        service._client_factory = lambda settings: client
        picture = image_bytes("blue", size=(200, 400))
        button = UiElement("下载", "", "com.example.store", "android.widget.Button", True, True,
                           (30, 250, 150, 300), 1)
        screen = PhoneObservation(0, 200, 400, (button,), "same-screen", len(picture), picture)

        async def decide(envelope):
            if len(self.called) == 1:
                return answered('{"kind":"tap","x":90,"y":275}')
            self.assertIn("相同页面上点击 (90, 275) 曾没有产生可观察的变化",
                          envelope.original.instruction)
            if len(self.called) == 2:
                return answered('{"kind":"fail","reason":"下载按钮没有响应。"}')
            return answered('{"kind":"done","reason":"已核对安装状态。"}')

        self.model_behavior = decide
        with patch.object(PhoneSession, "observe", return_value=screen):
            first = service.create(context, "下载淘宝")["task_id"]
            await self.until(lambda: service._read(first).step == 1)
            self.clock.advance(timedelta(seconds=10))
            app.notify()
            await self.until(lambda: len(self.sent) == 1)
            self.assertIn("下载按钮没有响应", self.sent[0].text)
            queue = app.outbound.queue
            deferred = []

            def temporarily_unavailable(request):
                if request.owner == "phone.notice" and not deferred:
                    deferred.append(request.delivery_id)
                    return Outcome.failed("test_notice_queue_unavailable", "temporary queue error")
                return queue(request)

            with patch.object(app.outbound, "queue", side_effect=temporarily_unavailable):
                second = service.create(context, "检查淘宝是否安装")["task_id"]
                await self.until(lambda: len(self.sent) == 2)
            self.assertEqual(len(deferred), 1)
            self.assertIn("手机任务已完成", self.sent[1].text)
            self.assertEqual(service._read(second).state, "done")
            client.tap.assert_called_once_with(90, 275)
            service.advance().unwrap()
            self.assertEqual(self.runtime.read_one(
                "SELECT COUNT(*) FROM background_deliveries WHERE owner='phone.notice'", ())[0], 2)

    async def test_private_screen_command_reads_one_screenshot_and_replies_without_phone_input(self):
        app = (await self.start()).application
        context = TaskContext(self.important, self.identity.resolve_role(SCOPE, observed_at=NOW).unwrap(),
                              AudienceRef(SCOPE, (self.important.subject,)))
        service = app.phone_actions
        service._settings_reader = Mock()
        service._settings_reader.read.return_value = PhoneSettings(enabled=True, serial="SER123")
        adb = FakeAdb()
        screen = image_bytes("blue", size=(200, 400))
        adb.responses[("-s", "SER123", "shell", "wm", "size")] = "Physical size: 200x400\n"
        adb.responses[("-s", "SER123", "exec-out", "screencap", "-p")] = screen
        service._client_factory = lambda settings: AdbClient("adb.exe", serial=settings.serial, runner=adb)

        async def describe(envelope):
            request = envelope.original
            self.assertIn("goal: 手机当前显示什么？", request.instruction)
            self.assertIn("签到", request.instruction)
            self.assertIn("只描述当前画面", request.instruction)
            self.assertNotIn("100px", request.instruction)
            self.assertEqual(base64.b64decode(request.images[0].data_base64), screen)
            self.assertEqual(build_wire(envelope, model_name="test").image_count, 1)
            return answered('{"action":"done","reason":"当前显示签到页，页面写着今日已签到。"}')

        self.model_behavior = describe
        event = SimpleNamespace(unified_msg_origin="qq:FriendMessage:200", text="查看手机屏幕")
        captured = SimpleNamespace(event_id="screen-request", route=SimpleNamespace(scope=SCOPE),
                                    principal=context.actor.principal, recipients=context.audience,
                                    role=context.role_binding.role, text=event.text)
        session = SimpleNamespace(application=app, profile=SimpleNamespace(target=context),
                                  bridge=SimpleNamespace(capture=AsyncMock(return_value=Outcome.success(captured))))
        controller = SimpleNamespace(session=session, claims=lambda _: True, now=self.clock.now)
        with patch("astrbot_host.phone_command.authenticate_notice_route", return_value=object()):
            self.assertTrue((await receive_phone_command(controller, event, target=None)).is_success)
            await self.until(lambda: bool(self.sent))
            self.assertTrue((await receive_phone_command(controller, event, target=None)).is_success)

        self.assertEqual(len(service._records()), 1)
        self.assertEqual(len(self.called), 1)
        self.assertEqual(self.sent[0].context, context)
        self.assertIn("今日已签到", self.sent[0].text)
        self.assertFalse(any("input" in command for command in adb.calls))
        self.assertFalse((Path(self.runtime.data_root) / "phone").exists())

    async def checkout(self, *, label="提交订单", changed=False, supports_images=True, quick_approval=False):
        self.sender = replace(self.sender, supports_images=supports_images)
        host = await self.start()
        app = host.application
        context = TaskContext(self.important, self.identity.resolve_role(SCOPE, observed_at=NOW).unwrap(),
                              AudienceRef(SCOPE, (self.important.subject,)))
        service = app.phone_actions
        settings = PhoneSettings(enabled=True, serial="test-device")
        service._settings_reader = Mock()
        service._settings_reader.read.return_value = settings
        client = Mock()
        service._client_factory = lambda settings: client
        screen = image_bytes("blue", size=(200, 400))
        node = UiElement(label, "", "test.takeout", "android.widget.Button", True, True, (80, 325, 180, 375), 1)
        observation = PhoneObservation(0, 200, 400, (node,), "screen", len(screen), screen)
        captured = []

        async def decide(envelope):
            captured.append(build_wire(envelope, model_name="test"))
            return answered(json.dumps({"action": "confirm_checkout", "merchant": "测试饮品店",
                "item": "红茶拿铁 × 1", "options": "中杯、少冰、三分糖，美团红包抵扣 ¥3.00", "amount": "13.50",
                "address_hint": "测试小区", "button": {"kind": "tap_text", "text": label}}, ensure_ascii=False))

        self.model_behavior = decide
        with patch.object(PhoneSession, "observe", return_value=observation) as observe:
            task_id = service.create(context, "在美团买一杯红茶拿铁，用红包，支付前确认", shopping=True)["task_id"]
            await self.until(lambda: service.status(context, task_id)["state"] != "active")
            result = service.status(context, task_id)
            self.assertEqual(result["state"], "awaiting_confirmation", result)
            self.assertIn("¥13.50", result["notice"])
            self.assertTrue(captured)
            instructions = "\n".join(message["content"] for message in captured[0].messages
                                     if message["role"] == "system")
            self.assertIn("让本单实付最低的可用红包", instructions)
            self.assertIn("购神券包立减", instructions)
            self.assertIn("核对底部「红包优惠」并点「确认」", instructions)
            actions = next(line for line in instructions.splitlines() if line.startswith("本轮允许的动作"))
            self.assertIn("confirm_checkout", actions)
            await self.until(lambda: bool(self.sent) and self.runtime.read_one(
                "SELECT 1 FROM background_deliveries WHERE owner = 'phone.notice' AND state = 'delivered'", ()))
            sent = self.sent[0]
            token = service._read(task_id).checkout["token"]
            self.assertEqual(sent.context, context)
            self.assertIn("红茶拿铁", sent.text)
            self.assertIn("美团红包抵扣 ¥3.00", sent.text)
            self.assertIn("回复「确认支付」", sent.text)
            if supports_images:
                self.assertEqual(len(sent.images), 1)
                self.assertEqual(sent.images[0].source.scope, context.scope)
                self.assertEqual(base64.b64decode(sent.images[0].data_base64), screen)
            else:
                self.assertFalse(getattr(sent, "images", ()))
            client.tap.assert_not_called()
            with self.assertRaises(TaskError):
                service.answer_checkout(context, "0" * 10, approve=True)
            if changed:
                observe.return_value = replace(observation, screenshot=image_bytes("red", size=(200, 400)))
            if quick_approval:
                with self.assertRaises(TaskError):
                    service.answer_checkout(context, None, approve=True, received_at=NOW - timedelta(seconds=1))
                service.answer_checkout(context, None, approve=True, received_at=self.clock.now())
            else:
                service.answer_checkout(context, token, approve=True)
            await self.until(lambda: service.status(context, task_id)["step"] == 2
                             or service.status(context, task_id)["state"] == "failed")
            if changed:
                self.assertEqual(service.status(context, task_id)["code"], "phone_checkout_changed")
                client.tap.assert_not_called()
            else:
                client.tap.assert_called_once_with(130, 350)
            with self.assertRaises(TaskError):
                service.answer_checkout(context, token, approve=True)

    async def test_checkout_notice_and_single_approved_tap(self):
        await self.checkout(label="极速支付", quick_approval=True)

    async def meituan_checkout_with_cutlery(self, *, paid):
        app = (await self.start()).application
        context = TaskContext(self.important, self.identity.resolve_role(SCOPE, observed_at=NOW).unwrap(),
                              AudienceRef(SCOPE, (self.important.subject,)))
        service = app.phone_actions
        service._settings_reader = Mock()
        service._settings_reader.read.return_value = PhoneSettings(enabled=True, serial="test-device")
        client = Mock()
        service._client_factory = lambda settings: client

        def control(label, bounds, enabled=True):
            return UiElement(label, "", "com.sankuai.meituan", "android.widget.TextView", False, enabled, bounds)

        express = control("极速支付", (130, 340, 195, 380))
        no_cutlery = control("无需餐具", (110, 240, 190, 280))
        submit = control("确认并提交订单", (40, 340, 160, 380))
        cutlery = (control("当前地址下单，是否需要餐具？", (10, 190, 155, 220)),
                   control("需要餐具", (10, 240, 90, 280)), no_cutlery)
        checkout = (express,)
        select = (*cutlery, control("保存", submit.bounds, enabled=False))
        selected = (*cutlery, submit)
        after_submit = (control("等待商家接单", (15, 30, 170, 60)),) if paid else selected
        screens = iter((select, selected, after_submit))
        current = checkout

        def tap(x, y):
            nonlocal current
            current = next(screens)

        client.tap.side_effect = tap
        screenshot = image_bytes("blue", size=(200, 400))

        def observe(step):
            return PhoneObservation(step, 200, 400, current, "meituan", len(screenshot), screenshot)

        async def decide(envelope):
            self.assertEqual(current, checkout)
            return answered(json.dumps({
                "action": "confirm_checkout", "merchant": "沪上阿姨", "item": "杨枝甘露 × 1",
                "options": "大杯，零卡糖，1对1急送，红包抵扣5元", "amount": "18.60", "address_hint": "测试小区",
                "button": {"kind": "tap_text", "text": express.label},
            }, ensure_ascii=False))

        self.model_behavior = decide
        with patch.object(PhoneSession, "observe", side_effect=observe):
            task_id = service.create(context, "美团点一杯沪上阿姨杨枝甘露，支付前确认", shopping=True)["task_id"]
            await self.until(lambda: service.status(context, task_id)["state"] == "awaiting_confirmation")
            await self.until(lambda: self.runtime.read_one(
                "SELECT 1 FROM background_deliveries WHERE owner = 'phone.notice' AND state = 'delivered'", ()))
            client.tap.assert_not_called()
            service.answer_checkout(context, None, approve=True, received_at=self.clock.now())
            for step in (2, 3, 4):
                await self.until(lambda: service._read(task_id).step >= step)
                self.clock.advance(timedelta(seconds=10))
                app.notify()
            await self.until(lambda: service._read(task_id).state != "active")
            await self.until(lambda: len(self.sent) == 2)
            self.assertEqual([call.args for call in client.tap.call_args_list],
                             [express.center, no_cutlery.center, submit.center])
            self.assertEqual(len(self.called), 1)
            self.assertIn("回复「确认支付」", self.sent[0].text)
            if paid:
                self.assertEqual(service.status(context, task_id)["code"], "phone_order_paid")
                self.assertIn("等待商家接单", self.sent[1].text)
            else:
                self.assertEqual(service.status(context, task_id)["code"], "phone_checkout_outcome_unknown")
                self.assertIn("当前画面无法确认付款结果", self.sent[1].text)

    async def test_meituan_cutlery_and_submission_use_one_order_confirmation(self):
        await self.meituan_checkout_with_cutlery(paid=True)

    async def test_meituan_does_not_retry_an_unverified_submission(self):
        await self.meituan_checkout_with_cutlery(paid=False)

    async def test_checkout_revision_retires_old_confirmation_and_recovers_missing_text(self):
        app = (await self.start()).application
        context = TaskContext(self.important, self.identity.resolve_role(SCOPE, observed_at=NOW).unwrap(),
                              AudienceRef(SCOPE, (self.important.subject,)))
        service = app.phone_actions
        service._settings_reader = Mock()
        service._settings_reader.read.return_value = PhoneSettings(enabled=True, serial="test-device")
        client = Mock()
        service._client_factory = lambda settings: client
        screen = image_bytes("blue", size=(200, 400))
        pay = UiElement("极速支付", "", "com.sankuai.meituan", "android.widget.Button", False, True,
                        (80, 325, 180, 375))
        delivery = replace(pay, text="专人直送", clickable=True, bounds=(105, 120, 190, 140))
        observation = PhoneObservation(0, 200, 400, (pay, delivery), "checkout", len(screen), screen)
        client.tap_text.return_value = delivery
        decision = {"action": "confirm_checkout", "merchant": "演练商家", "item": "杨枝甘露 × 1",
                    "options": "大杯、零卡糖", "amount": "19", "address_hint": "演练地址",
                    "button": {"kind": "tap_text", "text": "极速支付"}}

        async def decide(envelope):
            prompt = envelope.original.instruction
            if "从手机当前结账页接续" not in prompt:
                return answered(json.dumps(decision, ensure_ascii=False))
            label = "专人直送" if "adb_text_not_found" in prompt else "1对1急送"
            return answered(json.dumps({"action": "tap_text", "text": label}, ensure_ascii=False))

        self.model_behavior = decide
        with patch.object(PhoneSession, "observe", return_value=observation):
            first = service.create(context, "美团杨枝甘露，支付前确认", shopping=True)["task_id"]
            await self.until(lambda: service.status(context, first)["state"] == "awaiting_confirmation")
            old_token = service._read(first).checkout["token"]
            second = service.create(context, "美团杨枝甘露，使用一对一配送", resume_checkout=True)["task_id"]
            self.assertEqual(service.status(context, first)["code"], "phone_checkout_revised")
            with self.assertRaises(TaskError):
                service.answer_checkout(context, old_token, approve=True)
            await self.until(lambda: service._read(second).step >= 1)
            self.assertEqual(service._read(second).history[0]["code"], "adb_text_not_found")
            client.tap_text.assert_not_called()
            self.clock.advance(timedelta(seconds=10))
            app.notify()
            await self.until(lambda: service._read(second).step >= 2)
            client.tap_text.assert_called_once_with("专人直送")
            client.tap.assert_not_called()

    async def test_changed_checkout_cannot_reuse_approval(self):
        await self.checkout(changed=True)

    async def test_text_only_sender_still_receives_checkout_confirmation(self):
        await self.checkout(supports_images=False)

    async def test_learns_checkout_navigation_and_reuses_it_from_the_live_page(self):
        app = (await self.start()).application
        context = TaskContext(self.important, self.identity.resolve_role(SCOPE, observed_at=NOW).unwrap(),
                              AudienceRef(SCOPE, (self.important.subject,)))
        service = app.phone_actions
        service._settings_reader = Mock()
        service._settings_reader.read.return_value = PhoneSettings(enabled=True, serial="test-device")
        client = Mock()
        service._client_factory = lambda settings: client
        screen = image_bytes("blue", size=(200, 400))
        button = UiElement("去结算", "", "com.sankuai.meituan", "android.widget.Button", True, True,
                           (80, 325, 180, 375), 1)
        cart = PhoneObservation(0, 200, 400, (button,), "cart", len(screen), screen)
        checkout = replace(cart, elements=(replace(button, text="立即支付"),), screenshot_sha256="checkout")
        moved = replace(cart, elements=(replace(button, bounds=(40, 250, 140, 300)),))
        goal = "用美团点一杯沪上阿姨杨枝甘露，加0卡糖，支付前确认"
        current = cart

        def go_to_checkout(x, y):
            nonlocal current
            self.assertEqual((x, y), current.elements[0].center)
            current = checkout

        async def decide(envelope):
            if current is checkout:
                return answered(json.dumps({"action": "confirm_checkout",
                    "merchant": "演练商家", "item": "杨枝甘露 × 1", "options": "中杯、0卡糖",
                    "amount": "19", "address_hint": "演练地址",
                    "button": {"kind": "tap_text", "text": "立即支付"}}, ensure_ascii=False))
            self.assertIn("手机技能「美团外卖点单」", envelope.original.instruction)
            x, y = current.elements[0].center
            return answered(json.dumps({"action": "tap", "x": x, "y": y}))

        async def order():
            task_id = service.create(context, goal, shopping=True)["task_id"]
            await self.until(lambda: service._read(task_id).step == 1 or service._read(task_id).state != "active")
            self.assertEqual(service._read(task_id).step, 1, service.status(context, task_id))
            self.clock.advance(timedelta(seconds=10))
            app.notify()
            await self.until(lambda: service._read(task_id).state != "active")
            self.assertEqual(service.status(context, task_id)["state"], "awaiting_confirmation")
            return task_id

        client.tap.side_effect = go_to_checkout
        self.model_behavior = decide
        with patch.object(PhoneSession, "observe", side_effect=lambda step: replace(current, step=step)):
            first = await order()
            self.assertEqual(len(self.called), 2)
            rows = self.runtime.read_all("SELECT value FROM runtime_metadata WHERE key LIKE ?", (SKILL_PREFIX + "%",))
            self.assertEqual(len(rows), 1)
            self.assertEqual(len(json.loads(rows[0]["value"])["taps"]), 1)
            self.assertNotIn("演练地址", rows[0]["value"])

            reopened = self.open_runtime()
            try:
                memory = PhoneSkillMemory(reopened)
                patch_screen = SkillScreen.capture(moved)
                replay = memory.recall(context, goal, True, patch_screen, moved, ())
                self.assertEqual((replay.x, replay.y), (90, 275))
                other = replace(context, actor=self.standard, audience=AudienceRef(SCOPE, (self.standard.subject,)))
                self.assertIsNone(memory.recall(other, goal, True, patch_screen, moved, ()))
            finally:
                reopened.close()

            self.assertTrue(service.cancel(first))
            current = moved
            second = await order()
            self.assertEqual(len(self.called), 3)
            self.assertEqual(service.status(context, second)["reused_steps"], 1)
            self.assertEqual(client.tap.call_count, 2)

            self.assertTrue(service.cancel(second))
            current = replace(cart, elements=(replace(button, text="预览订单"),))
            third = await order()
            self.assertEqual(len(self.called), 5)
            self.assertEqual(service.status(context, third)["reused_steps"], 0)
            self.assertEqual(client.tap.call_count, 3)


if __name__ == "__main__":
    unittest.main()
