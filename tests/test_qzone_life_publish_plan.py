# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import random
import time
import logging
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

try:
    from astrbot.api import logger as _astrbot_logger  # noqa: F401
except ModuleNotFoundError:
    astrbot_module = types.ModuleType("astrbot")
    astrbot_api_module = types.ModuleType("astrbot.api")
    astrbot_event_module = types.ModuleType("astrbot.api.event")
    astrbot_api_module.logger = logging.getLogger("qzone-life-publish-test")
    astrbot_event_module.AstrMessageEvent = object
    astrbot_module.api = astrbot_api_module
    sys.modules.setdefault("astrbot", astrbot_module)
    sys.modules.setdefault("astrbot.api", astrbot_api_module)
    sys.modules.setdefault("astrbot.api.event", astrbot_event_module)

from astrbot_plugin_private_companion.qzone_integration import QzoneMixin
from astrbot_plugin_private_companion.qzone_media import QzoneIntegrationError
from astrbot_plugin_private_companion.helpers import _day_start_ts, _today_key


class _PlanHarness(QzoneMixin):
    qzone_life_publish_max_daily = 3
    qzone_life_publish_probability = 1.0
    qzone_life_publish_window_mode = "custom"
    qzone_life_publish_windows = "07:00-11:00\n12:00-13:00\n18:00-22:00"
    qzone_life_publish_allow_insomnia_night = False
    qzone_life_publish_intra_day_gap_minutes = 60
    qzone_life_publish_min_interval_hours = 0

    def __init__(self) -> None:
        self.data = {"daily_plan": {"date": _today_key(), "items": []}}

    def _has_active_insomnia_state(self) -> bool:
        return False


class _CommentHarness(QzoneMixin):
    qzone_comment_inbox_recent_posts = 5
    qzone_comment_inbox_interval_minutes = 5
    qzone_comment_inbox_max_replies_per_tick = 1
    enable_qzone_comment_inbox = True

    def __init__(self, post, event_id: str = "100") -> None:
        self.post = post
        self.event_id = event_id
        self.data = {"qzone_integration": {}}
        self.sent = []

    def _qzone_available(self, _event=None) -> bool:
        return True

    async def _qzone_get_cookies(self, _event=None) -> str:
        return "uin=o123; skey=x"

    def _qzone_context_from_cookies(self, _cookies: str) -> dict:
        return {"uin": 123}

    async def _qzone_query_feeds(self, *_args, **_kwargs):
        return [self.post]

    async def _qzone_decide_comment_reply(self, _post, _comment, **_kwargs) -> dict:
        return {"decision": "reply", "reply": "收到啦", "reason": "ok"}

    async def _qzone_reply_to_comment(self, _event, _post, _comment, reply: str) -> str:
        self.sent.append(reply)
        return reply

    def _save_data_sync(self, **_kwargs) -> None:
        pass


class _PublishHarness(QzoneMixin):
    enable_qzone_integration = True

    def __init__(self) -> None:
        self.data = {"qzone_integration": {}}

    def _qzone_available(self, _event=None) -> bool:
        return True

    async def _qzone_publish_post(self, *_args, **_kwargs):
        raise QzoneIntegrationError(
            "投递结果未知",
            "timed out",
            delivery_unknown=True,
        )

    def _qzone_clear_auth_failure(self) -> None:
        pass

    def _qzone_mark_auth_failure(self, *_args, **_kwargs) -> None:
        pass


class _VerifiedPublishHarness(_PublishHarness):
    def __init__(self) -> None:
        super().__init__()
        self.recorded: dict[str, object] = {}

    async def _qzone_publish_post(self, *_args, **_kwargs):
        return SimpleNamespace(
            tid="",
            uin="123",
            text="短动态",
            images=[],
        )

    async def _qzone_verify_published_post(self, *_args, **_kwargs):
        return {
            "verified": True,
            "tid": "verified-feed-id",
            "images": 0,
            "message": "已反查到最近说说",
        }

    async def _qzone_record_published_post(self, text, **kwargs) -> None:
        self.recorded = {"text": text, **kwargs}


class _Event:
    def __init__(self, sender_id: str) -> None:
        self.sender_id = sender_id

    def get_sender_id(self) -> str:
        return self.sender_id


class QzoneLifePublishPlanTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_image_setting_without_generator_falls_back_to_text(self) -> None:
        class LegacyImageHarness(QzoneMixin):
            enable_qzone_integration = True
            enable_qzone_generated_image_publish = True
            qzone_generated_image_probability = 1.0

            def __init__(self) -> None:
                self.data = {"qzone_integration": {}}

        harness = LegacyImageHarness()
        images = await harness._maybe_generate_qzone_publish_image(
            post_text="今天有点累，但还是想记录一下。",
            reason="life_publish",
            state=harness.data["qzone_integration"],
            force=True,
        )
        self.assertEqual(images, [])
        self.assertEqual(
            harness.data["qzone_integration"].get("last_life_publish_generated_image_status"),
            "skipped:no_generator",
        )

    def test_nested_failure_code_wins_over_empty_normalized_code(self) -> None:
        self.assertEqual(_PlanHarness._qzone_response_code({"code": None, "_raw_code": -3000}), -3000)
        self.assertEqual(_PlanHarness._qzone_response_code({"code": "", "ret": -1}), -1)
        self.assertEqual(_PlanHarness._qzone_response_code({"code": 0, "_raw_code": 0}), 0)

    def test_windows_are_unlimited_and_overlaps_merge(self) -> None:
        windows = _PlanHarness._qzone_parse_windows("07:00-09:00\n08:00-11:00\n12:00-13:00\n18:00-19:00\n20:00-21:00")
        self.assertEqual(windows, [(420, 660), (720, 780), (1080, 1140), (1200, 1260)])

    def test_cross_midnight_window_is_split_without_default_fallback(self) -> None:
        windows = _PlanHarness._qzone_parse_windows("23:00-02:00")
        self.assertEqual(windows, [(0, 120), (1380, 1440)])

    def test_invalid_custom_window_stops_daily_plan(self) -> None:
        harness = _PlanHarness()
        harness.qzone_life_publish_windows = "not-a-window"
        plan = harness._qzone_life_publish_daily_plan({}, now=time.time())
        self.assertEqual(plan["skip_reason"], "invalid_window_config")
        self.assertEqual(harness._qzone_life_publish_effective_windows(), [])

    def test_single_post_can_use_later_window(self) -> None:
        harness = _PlanHarness()
        harness.qzone_life_publish_max_daily = 1
        now = _day_start_ts(time.time()) + 60 * 60
        with (
            patch.object(random, "shuffle", side_effect=lambda values: values.reverse()),
            patch.object(random, "uniform", side_effect=lambda start, end: (start + end) / 2),
        ):
            slots = harness._qzone_life_publish_pick_slots(target_count=1, earliest=0, now=now)
        minute = int((slots[0] - _day_start_ts(now)) / 60)
        self.assertGreaterEqual(minute, 18 * 60)

    def test_config_change_rebuilds_undelivered_plan(self) -> None:
        harness = _PlanHarness()
        state = {}
        with patch("astrbot_plugin_private_companion.qzone_integration.random.random", return_value=0.0):
            first = harness._qzone_life_publish_daily_plan(state, now=time.time())
            harness.qzone_life_publish_windows = "16:00-17:00"
            second = harness._qzone_life_publish_daily_plan(state, now=time.time())
        self.assertIsNot(first, second)
        self.assertNotEqual(first["config_signature"], second["config_signature"])

    def test_schedule_labels_are_backfilled_after_current_agenda_fact_arrives(self) -> None:
        harness = _PlanHarness()
        harness.data = {"daily_plan": {"date": _today_key(), "items": []}}
        day_start = _day_start_ts(time.time())
        now = day_start + 8 * 60 * 60
        with patch("astrbot_plugin_private_companion.qzone_integration.random.random", return_value=0.0):
            plan = harness._qzone_life_publish_daily_plan({}, now=now)
        self.assertTrue(all(not item.get("schedule_label") for item in plan["items"]))
        harness._agenda_disclosure_view = lambda *_args, **_kwargs: SimpleNamespace(
            entries=[
                {
                    "entry_id": "agenda-evening-walk",
                    "title": "晚饭后散步",
                    "start_at": day_start,
                    "end_at": day_start + 24 * 60 * 60,
                    "fact_eligibility": "current_internal",
                    "temporal_phase": "current",
                    "evidence_kind": "committed_schedule",
                }
            ]
        )
        self.assertTrue(harness._qzone_backfill_plan_schedule_labels(plan))
        self.assertTrue(any(item.get("schedule_label") == "晚饭后散步" for item in plan["items"]))

    def test_n_one_always_plans_one_item(self) -> None:
        harness = _PlanHarness()
        harness.qzone_life_publish_max_daily = 1
        local = time.localtime()
        now = time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 8, 0, 0, 0, 0, -1))
        with patch("astrbot_plugin_private_companion.qzone_integration.random.random", return_value=0.0):
            plan = harness._qzone_life_publish_daily_plan({}, now=now)
        self.assertEqual(plan["target_count"], 1)
        self.assertEqual(len(plan["items"]), 1)

    def test_plan_is_reused_for_the_same_day(self) -> None:
        harness = _PlanHarness()
        state = {}
        with patch("astrbot_plugin_private_companion.qzone_integration.random.random", return_value=0.0):
            first = harness._qzone_life_publish_daily_plan(state, now=time.time())
            second = harness._qzone_life_publish_daily_plan(state, now=time.time() + 60)
        self.assertIs(first, second)

    async def test_immediate_comment_reply_marks_stable_key_once(self) -> None:
        comment = SimpleNamespace(comment_id="c1", uin="100", name="user", content="我刚刚评论啦", raw={})
        post = SimpleNamespace(tid="post1", comments=[comment])
        harness = _CommentHarness(post)

        result = await harness._qzone_reply_my_comment(_Event("100"), comment_hint="刚刚评论")

        self.assertEqual(result["status"], "replied")
        self.assertEqual(harness.sent, ["收到啦"])
        state = harness.data["qzone_integration"]
        self.assertIn("c1", state["comment_inbox_replied_ids"])
        self.assertEqual(len(state["comment_inbox_replied_keys"]), 1)

        again = await harness._qzone_reply_my_comment(_Event("100"), comment_hint="刚刚评论")
        self.assertEqual(again["status"], "not_found")

    async def test_concurrent_immediate_comment_replies_send_once(self) -> None:
        comment = SimpleNamespace(comment_id="c1", uin="100", name="user", content="我刚刚评论啦", raw={})
        harness = _CommentHarness(SimpleNamespace(tid="post1", comments=[comment]))
        original = harness._qzone_reply_to_comment

        async def delayed_reply(*args, **kwargs):
            await asyncio.sleep(0.01)
            return await original(*args, **kwargs)

        harness._qzone_reply_to_comment = delayed_reply
        results = await asyncio.gather(
            harness._qzone_reply_my_comment(_Event("100"), comment_hint="刚刚评论"),
            harness._qzone_reply_my_comment(_Event("100"), comment_hint="刚刚评论"),
        )
        self.assertEqual(len(harness.sent), 1)
        self.assertEqual({item["status"] for item in results}, {"replied", "not_found"})

    async def test_immediate_comment_reply_refuses_ambiguous_matches(self) -> None:
        comments = [
            SimpleNamespace(comment_id="c1", uin="100", name="user", content="今天真不错", raw={}),
            SimpleNamespace(comment_id="c2", uin="200", name="other", content="今天真不错", raw={}),
        ]
        harness = _CommentHarness(SimpleNamespace(tid="post1", comments=comments))

        result = await harness._qzone_reply_my_comment(_Event("0"), comment_hint="今天真不错")

        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(harness.sent, [])

    async def test_known_sender_wins_over_foreign_exact_hint(self) -> None:
        comments = [
            SimpleNamespace(comment_id="c1", uin="100", name="user", content="这是我刚刚留的评论", raw={}),
            SimpleNamespace(comment_id="c2", uin="200", name="other", content="精确关键词", raw={}),
        ]
        harness = _CommentHarness(SimpleNamespace(tid="post1", comments=comments))

        result = await harness._qzone_reply_my_comment(_Event("100"), comment_hint="精确关键词")

        self.assertEqual(result["status"], "replied")
        self.assertEqual(result["comment"], "这是我刚刚留的评论")

    async def test_immediate_comment_reply_failure_is_retryable(self) -> None:
        comment = SimpleNamespace(comment_id="c1", uin="100", name="user", content="我刚刚评论啦", raw={})
        harness = _CommentHarness(SimpleNamespace(tid="post1", comments=[comment]))

        async def fail_reply(*_args, **_kwargs):
            raise RuntimeError("评论失败 code=-3000")

        harness._qzone_reply_to_comment = fail_reply
        result = await harness._qzone_reply_my_comment(_Event("100"), comment_hint="刚刚评论")

        self.assertEqual(result["status"], "error")
        state = harness.data["qzone_integration"]
        self.assertIn("c1", state["comment_inbox_retry_ids"])
        self.assertNotIn("c1", state.get("comment_inbox_replied_ids", []))

    async def test_unknown_delivery_result_is_not_retried(self) -> None:
        comment = SimpleNamespace(comment_id="c1", uin="100", name="user", content="我刚刚评论啦", raw={})
        harness = _CommentHarness(SimpleNamespace(tid="post1", comments=[comment]))

        async def fail_reply(*_args, **_kwargs):
            raise TimeoutError("connection timed out")

        harness._qzone_reply_to_comment = fail_reply
        result = await harness._qzone_reply_my_comment(_Event("100"), comment_hint="刚刚评论")

        self.assertEqual(result["status"], "error")
        self.assertFalse(result["retryable"])
        state = harness.data["qzone_integration"]
        self.assertEqual(state["last_comment_inbox_status"], "tool_delivery_unknown")
        self.assertNotIn("c1", state.get("comment_inbox_retry_ids", []))
        self.assertIn("c1", state.get("comment_inbox_delivery_unknown_ids", []))
        again = await harness._qzone_reply_my_comment(_Event("100"), comment_hint="刚刚评论")
        self.assertEqual(again["status"], "not_found")

    async def test_secondary_persona_skips_account_wide_maintenance(self) -> None:
        comment = SimpleNamespace(comment_id="c1", uin="100", name="user", content="评论", raw={})
        harness = _CommentHarness(SimpleNamespace(tid="post1", comments=[comment]))
        harness.enable_multi_persona_mode = True
        harness.plugin_specific_persona_id = "primary"
        harness._active_persona_scope = lambda: "secondary"
        await harness._maybe_process_qzone_comment_inbox()
        self.assertEqual(harness.sent, [])
        self.assertEqual(harness.data["qzone_integration"], {})

    async def test_publish_timeout_is_structured_as_delivery_unknown(self) -> None:
        result = await _PublishHarness()._publish_qzone_text("测试说说")
        self.assertFalse(result["success"])
        self.assertTrue(result["delivery_unknown"])

    async def test_verified_feed_id_is_recorded_when_publish_response_has_no_tid(self) -> None:
        harness = _VerifiedPublishHarness()

        result = await harness._publish_qzone_text("短动态")

        self.assertTrue(result["success"])
        self.assertEqual("verified-feed-id", result["tid"])
        self.assertEqual("verified-feed-id", harness.recorded["tid"])
        self.assertTrue(harness.recorded["verified"])


if __name__ == "__main__":
    unittest.main()
