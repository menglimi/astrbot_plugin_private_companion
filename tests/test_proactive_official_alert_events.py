"""Synthetic official-alert contact lifecycle and attribution."""

from datetime import timedelta
import json
import unittest

from companion_v2.conversation.foreground import BackgroundPromptTrigger
from companion_v2.conversation.reply_management import ReplyManagement
from companion_v2.execution.models import TaskError
from companion_v2.proactive.official_alert_events import SOURCE_OWNER, SOURCE_URL
from companion_v2.proactive.prompts import contact_output
from companion_v2.proactive.weather_alerts import normalize_qweather_alerts
from companion_v2.role_work.state.management import StateManagement
from tests.contact_schedule_support import ContactScheduleFixture
from tests.test_planning_weather import LOCATION_JSON
from tests.test_role_tool_loop import answered


ATTRIBUTIONS = ("https://developer.qweather.com/attribution.html", "预警数据以官方发布为准。")
ALERT_LOCATION_JSON = json.dumps({**json.loads(LOCATION_JSON), "alert_city": "目标城市",
                                  "alert_area": "目标镇街"}, ensure_ascii=False)


class AlertPort:
    def __init__(self, clock, replies):
        self.clock, self.replies, self.calls = clock, list(replies), 0

    async def fetch(self, location):
        self.calls += 1
        rows = self.replies.pop(0)
        return normalize_qweather_alerts({"metadata": {"zeroResult": not rows,
            "attributions": list(ATTRIBUTIONS)}, "alerts": rows}, location, self.clock.now())


class OfficialAlertContactTests(ContactScheduleFixture, unittest.IsolatedAsyncioTestCase):
    async def generate(self, envelope):
        if self.model_behavior:
            return await super().generate(envelope)
        if isinstance(envelope.original.event, BackgroundPromptTrigger) and any(
                item.owner == SOURCE_OWNER for item in envelope.original.context.materials):
            self.called.append(envelope)
            return answered(json.dumps({"decision": "send", "text": "请留意预警。",
                "coverage": "covered", "coverage_quote": "全市发布大风预警"}, ensure_ascii=False))
        return await super().generate(envelope)

    async def setup_alerts(self, *, enabled=True, alert_scope=True):
        host, batch = await self.scheduled()
        app = host.application
        context = self.context(app, batch)
        manager = StateManagement(app, context)
        before = manager.status(self.core).unwrap()
        manager.command(self.core, dict(action="configure", operation_key="official-alert-location",
            edit_token=before["edit_token"], changes={"planning_weather_location":
                ALERT_LOCATION_JSON if alert_scope else LOCATION_JSON})).unwrap()
        self.settings(app, {"chat.enabled": False, "official_weather_alert_enabled": enabled}).unwrap()
        return app, context

    def alert(self, identifier="alert-1", *, kind="alert", supersedes=()):
        now = self.clock.now()
        return {"id": identifier, "senderName": "当地气象台",
            "issuedTime": (now - timedelta(minutes=1)).isoformat(),
            "messageType": {"code": kind, "supersedes": list(supersedes)},
            "eventType": {"name": "大风", "code": "1006"}, "severity": "moderate",
            "effectiveTime": (now - timedelta(minutes=1)).isoformat(),
            "expireTime": (now + timedelta(hours=1)).isoformat(),
            "headline": "大风预警", "description": "全市发布大风预警，请注意防范", "instruction": "留意官方更新"}

    async def test_city_only_location_does_not_poll_official_alerts(self):
        app, _ = await self.setup_alerts(alert_scope=False)
        port = AlertPort(self.clock, [[self.alert()]])
        app.contact_schedule.official_alert_events.port = port
        self.advance(app, 60)
        app.contact_schedule.advance()
        self.assertEqual(port.calls, 0)

    def test_send_requires_original_coverage_quote(self):
        alert = {"alert_area": "目标镇街", "alert": {"headline": "预警",
            "description": "目标镇街在预警范围内。", "instruction": ""}}
        def output(**extra):
            return json.dumps({"decision": "send", "text": "请留意。", **extra}, ensure_ascii=False)
        self.assertEqual(contact_output(output(coverage="covered", coverage_quote="目标镇街在预警范围内"),
                                        official_alert=alert), ("请留意。", None))
        for extras in ({}, {"coverage": "covered", "coverage_quote": "其他镇街"},
                       {"coverage": "unclear", "coverage_quote": "目标镇街在预警范围内"}):
            with self.subTest(extras=extras):
                self.assertEqual(contact_output(output(**extras), official_alert=alert), (None, None))
        for description, quote in (("全市发布预警，目标镇街除外。", "全市发布预警"),
                                   ("全市发布预警。目标镇街不在预警范围内。", "全市发布预警"),
                                   ("全市部分镇街发布预警。", "全市部分镇街发布预警"),
                                   ("仅其他镇街发布预警。", "仅其他镇街发布预警")):
            with self.subTest(description=description):
                alert["alert"]["description"] = description
                self.assertEqual(contact_output(output(coverage="covered", coverage_quote=quote),
                                                official_alert=alert), (None, None))
        alert["alert"]["description"] = "目标镇街发布预警。"
        self.assertEqual(contact_output(output(coverage="covered", coverage_quote="目标镇街发布预警"),
                                        official_alert=alert), ("请留意。", None))

    def test_coverage_guard_rejects_exclusions_naming_the_target_first(self):
        """「目标镇街之外」和「除目标镇街外」是同一句排除，必须同样拒绝。"""
        def output(quote):
            return json.dumps({"decision": "send", "text": "请留意。", "coverage": "covered",
                               "coverage_quote": quote}, ensure_ascii=False)
        body = {"alert_area": "目标镇街", "alert": {"headline": "大风预警",
            "description": "", "instruction": ""}}
        for description, quote in (
                ("全市发布大风预警，目标镇街以外地区不受影响。", "目标镇街以外地区不受影响"),
                ("本次预警覆盖目标镇街之外的其他区域。", "目标镇街之外的其他区域"),
                ("目标镇街不属于本次预警范围。", "目标镇街不属于本次预警范围"),
                ("目标镇街不适用于本次预警。", "目标镇街不适用于本次预警"),
                ("目标镇街被排除在本次预警范围外。", "目标镇街被排除在本次预警范围外"),
                ("目标镇街未覆盖到，请注意其他区域。", "目标镇街未覆盖到"),
                ("目标镇街不在本次预警覆盖范围内。", "目标镇街不在本次预警覆盖范围内"),
                ("目标镇街未受影响。", "目标镇街未受影响")):
            with self.subTest(description=description):
                body["alert"]["description"] = description
                self.assertEqual(contact_output(output(quote), official_alert=body), (None, None))
        # 正向说法不能被误伤：只有「排除」才拒绝，覆盖原句照旧放行。
        for description, quote in (("目标镇街发布大风预警，请注意防范。", "目标镇街发布大风预警"),
                                   ("目标镇街处于本次预警范围内。", "目标镇街处于本次预警范围内")):
            with self.subTest(description=description):
                body["alert"]["description"] = description
                self.assertEqual(contact_output(output(quote), official_alert=body), ("请留意。", None))
        # 英文通告同样要认得正反两个方向。
        english = {"alert_area": "Vienna", "alert": {"headline": "Wind warning",
            "description": "", "instruction": ""}}
        for description, quote in (("Vienna is outside the warning area.", "Vienna is outside the warning area"),
                                   ("The warning excludes Vienna and its suburbs.", "The warning excludes Vienna")):
            with self.subTest(description=description):
                english["alert"]["description"] = description
                self.assertEqual(contact_output(output(quote), official_alert=english), (None, None))
        english["alert"]["description"] = "Vienna is covered by the wind warning."
        self.assertEqual(contact_output(output("Vienna is covered by the wind warning"),
                                        official_alert=english), ("请留意。", None))

    async def test_excluded_target_is_skipped_even_when_model_requests_send(self):
        app, _ = await self.setup_alerts()
        row = self.alert()
        row["description"] = "全市发布大风预警。目标镇街不在预警范围内。"
        port = AlertPort(self.clock, [[row]])
        app.contact_schedule.official_alert_events.port = port

        async def mistaken_coverage(envelope):
            return answered(json.dumps({"decision": "send", "text": "请留意预警。",
                "coverage": "covered", "coverage_quote": "全市发布大风预警"}, ensure_ascii=False))

        self.model_behavior = mistaken_coverage
        self.advance(app, 60)
        await self.until(lambda: len(self.contacts(app)) == 1)
        self.assertEqual(json.loads(self.contacts(app)[0]["record_json"])["decision"], "skip")
        self.assertEqual(app.runtime.read_one("SELECT COUNT(*) FROM background_deliveries", ())[0], 0)

    async def test_confirmed_diagnostic_keeps_official_alert_coverage_guard(self):
        app, context = await self.setup_alerts()
        app.contact_schedule.official_alert_events.port = AlertPort(self.clock, [[self.alert()]])
        async def wrong_coverage(_envelope):
            return answered(json.dumps({"decision": "send", "text": "请留意预警。",
                "coverage": "covered", "coverage_quote": "请注意安全"}, ensure_ascii=False))
        self.model_behavior = wrong_coverage
        self.advance(app, 60)

        candidate = await app.contact_schedule.diagnostic_candidate(context, "environment", at=self.clock.now())
        self.assertEqual(candidate["route"]["id"], "official_alert_event")
        result = await app.proactive.contact(context, candidate["opportunity"], candidate["intents"],
            role_instruction=candidate["role_instruction"], selection_seed="confirmed-alert-diagnostic",
            administrator_test=True, diagnostic_source_backed=True,
            diagnostic_origin=candidate["diagnostic_origin"])

        self.assertIsNone(result.failure, result.failure)
        await self.until(lambda: len(self.contacts(app)) == 1)
        saved = json.loads(self.contacts(app)[0]["record_json"])
        self.assertEqual(saved["skip_reason"], "source_coverage")
        self.assertEqual(app.runtime.read_one("SELECT COUNT(*) FROM background_deliveries", ())[0], 0)

    async def test_opt_in_and_attribution_is_program_added(self):
        app, _ = await self.setup_alerts(enabled=False)
        port = AlertPort(self.clock, [[self.alert()]])
        app.contact_schedule.official_alert_events.port = port
        self.advance(app, 60)
        app.contact_schedule.advance()
        self.assertEqual(port.calls, 0)
        self.settings(app, {"official_weather_alert_enabled": True}).unwrap()
        app.contact_schedule.advance()
        await self.automatic_delivery(app)
        self.assertEqual(port.calls, 1)
        self.assertEqual(len(self.contact_calls()), 1)
        record = self.contacts(app)[0]
        self.assertIn("来源：和风天气 " + SOURCE_URL, record["body"])
        self.assertIn("\n".join(ATTRIBUTIONS), record["body"])
        material = next(item for item in self.contact_calls()[0].original.context.materials
                        if item.owner == SOURCE_OWNER)
        self.assertNotIn("_receipt", json.loads(material.payload))
        self.assertEqual(json.loads(material.payload)["alert"]["headline"], "大风预警")

    async def test_update_after_a_skipped_contact_reaches_the_role_again(self):
        """首条被跳过不等于已经联系过：同 chain 的升级版本必须还能生成联系。"""
        app, _ = await self.setup_alerts()
        first = self.alert()
        first["description"] = "全市部分镇街发布大风预警，请注意防范。"
        update = self.alert("alert-2", kind="update", supersedes=("alert-1",))
        update["severity"] = "extreme"
        update["description"] = "目标镇街发布大风预警，请注意防范。"
        quotes = iter(["全市部分镇街发布大风预警", "目标镇街发布大风预警"])

        async def send_with_quote(envelope):
            if isinstance(envelope.original.event, BackgroundPromptTrigger) and any(
                    item.owner == SOURCE_OWNER for item in envelope.original.context.materials):
                return answered(json.dumps({"decision": "send", "text": "请留意预警。",
                    "coverage": "covered", "coverage_quote": next(quotes)}, ensure_ascii=False))
            if isinstance(envelope.original.event, BackgroundPromptTrigger):
                return answered('{"decision":"send","text":"刚想起上次的话题。"}')
            return "记住了。"

        self.model_behavior = send_with_quote
        port = AlertPort(self.clock, [[first], [update]])
        app.contact_schedule.official_alert_events.port = port
        self.advance(app, 60)
        await self.until(lambda: len(self.contacts(app)) == 1)
        skipped = json.loads(self.contacts(app)[0]["record_json"])
        self.assertEqual((skipped["decision"], skipped["skip_reason"]), ("skip", "source_coverage"))
        self.assertEqual(app.runtime.read_one("SELECT COUNT(*) FROM background_deliveries", ())[0], 0)

        self.clock.advance(timedelta(minutes=5))
        app.contact_schedule.advance()
        await self.until(lambda: port.calls == 2 and not app.contact_schedule.active_handles)
        self.assertEqual(len(self.contacts(app)), 2)
        sent = json.loads(self.contacts(app)[1]["record_json"])
        self.assertEqual(sent["decision"], "send")
        marker = sent["automatic_origin"]["official_alert_event"]
        self.assertEqual((marker["alert_id"], marker["chain_id"]), ("alert-2", "alert-1"))

    async def test_update_chain_does_not_repeat_after_restart(self):
        app, _ = await self.setup_alerts()
        port = AlertPort(self.clock, [[self.alert()],
            [self.alert("alert-2", kind="update", supersedes=("alert-1",))]])
        app.contact_schedule.official_alert_events.port = port
        self.advance(app, 60)
        await self.automatic_delivery(app)
        self.clock.advance(timedelta(minutes=5))
        app.contact_schedule.advance()
        await self.until(lambda: port.calls == 2 and not app.contact_schedule.active_handles)
        self.assertEqual(len(self.contacts(app)), 1)
        rows = app.runtime.read_all("SELECT alert_id, chain_id, state FROM proactive_official_alerts ORDER BY alert_id", ())
        self.assertEqual([(row["alert_id"], row["chain_id"], row["state"]) for row in rows],
                         [("alert-1", "alert-1", "retired"), ("alert-2", "alert-1", "current")])
        await app.close()
        restarted = (await self.start(self.host(runtime=self.open_runtime()), 1, 1)).application
        restarted.contact_schedule.official_alert_events.port = port
        restarted.contact_schedule.advance()
        self.assertEqual(len(self.contacts(restarted)), 1)

    async def test_text_presentation_keeps_alert_body_and_program_attribution_literal(self):
        app, context = await self.setup_alerts()
        manager = ReplyManagement(app, context)
        status = manager.status(self.core).unwrap()
        manager.configure(self.core, {"operation_key": "alert-literal-text", "edit_token": status["edit_token"],
            "changes": {"content_cleanup_enabled": True, "content_cleanup_words": '["请留意预警。", "\\n"]',
                        "content_replacement_enabled": True,
                        "content_replacements": '["预警 => 测试", "天气 => 改写"]'}}).unwrap()
        app.contact_schedule.official_alert_events.port = AlertPort(self.clock, [[self.alert()]])
        self.advance(app, 60)
        await self.automatic_delivery(app)
        record = self.contacts(app)[0]
        self.assertTrue(record["body"].startswith("请留意预警。"))
        self.assertIn("来源：和风天气 " + SOURCE_URL, record["body"])
        self.assertIn("\n".join(ATTRIBUTIONS), record["body"])

    async def test_cancel_retires_original_source(self):
        app, context = await self.setup_alerts()
        port = AlertPort(self.clock, [[self.alert()],
            [self.alert("cancel-1", kind="cancel", supersedes=("alert-1",))]])
        app.contact_schedule.official_alert_events.port = port
        self.advance(app, 60)
        await self.automatic_delivery(app)
        source = next(item.source for item in self.contact_calls()[0].original.context.materials
                      if item.owner == SOURCE_OWNER)
        self.clock.advance(timedelta(minutes=5))
        app.contact_schedule.advance()
        await self.until(lambda: port.calls == 2 and not app.contact_schedule.active_handles)
        self.assertFalse(app.proactive.sources.validate_reference_for_audience(
            source, recipient=context.actor.subject, observed_at=self.clock.now()).is_success)
        self.assertEqual(len(self.contacts(app)), 1)

    async def test_marker_must_match_its_current_alert_source(self):
        app, context = await self.setup_alerts()
        port = AlertPort(self.clock, [[self.alert(), self.alert("alert-2")]])
        app.contact_schedule.official_alert_events.port = port
        self.advance(app, 60)
        await self.automatic_delivery(app)
        marker = json.loads(self.contacts(app)[0]["record_json"])["automatic_origin"]["official_alert_event"]
        events = app.contact_schedule.official_alert_events
        events.validate(context, marker, self.clock.now())
        other_id = "alert-2" if marker["alert_id"] == "alert-1" else "alert-1"
        with self.assertRaises(TaskError):
            events.validate(context, {**marker, "alert_id": other_id, "chain_id": other_id}, self.clock.now())
        for key, value in (("alert_id", "another-alert"), ("chain_id", "another-chain"),
                           ("location_hash", "another-location")):
            changed = {**marker, key: value}
            with self.subTest(key=key), self.assertRaises(TaskError):
                events.validate(context, changed, self.clock.now())


if __name__ == "__main__":
    unittest.main()
