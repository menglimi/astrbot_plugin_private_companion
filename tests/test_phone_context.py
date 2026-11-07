from datetime import datetime, timezone
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from astrbot_host.core_reply_context import build_core_reply_prompt
from astrbot_host.core_reply_mobile import phone_scene_snapshot
from companion_v2.conversation.phone_context import (
    PHONE_CONTEXT_INSTRUCTION, SNAPSHOT_VERSION, project_phone_context,
)
from companion_v2.foundation.refs import ScopeKind


AT = datetime(2026, 11, 4, 9, 27, 0, tzinfo=timezone.utc)


def _mobile_snapshot():
    return {
        "available": True,
        "activity": {
            "available": True,
            "category": "shopping",
            "app_label": "美团",
            "display_title": "美团外卖",
            "captured_at": 1790848200,
            "last_report_at": 1790848200,
            "age_seconds": 20,
            "stale": False,
            "privacy_mode": True,
            "music": {"playing": False, "source": ""},
        },
        "device": {"available": True, "app_state": "foreground"},
    }


def _messages():
    return NS(
        role_profile_context=None,
        acquaintance=NS(card_contribution=lambda *_args, **_kwargs: None),
        knowledge=None,
        proactive_inspirations=None,
        relationship_profile_context=None,
        memory_context=None,
        relationship_context=None,
        life_context=None,
        role_growth_context=None,
        role_self_review_context=None,
        open_items_context=None,
        role_state_context=None,
        expression_style_context=None,
        group_context=NS(authorize=lambda *_args: None),
    )


SETTINGS = {
    "guidance": "", "calendar_timezone": "Asia/Shanghai", "calendar_enabled": False,
    "health_enabled": False, "platform_enabled": True, "model_route_enabled": True,
    "configured_modalities_enabled": True,
}


class PhoneProjectionTests(unittest.TestCase):
    def test_projection_keeps_the_foreground_app_and_its_freshness(self):
        result = project_phone_context(_mobile_snapshot(), observed_at=AT)
        self.assertEqual(result["version"], SNAPSHOT_VERSION)
        self.assertEqual(result["privacy"], "private_only")
        self.assertEqual(result["app"], "美团")
        self.assertEqual(result["title"], "美团外卖")
        self.assertEqual(result["category"], "shopping")
        self.assertEqual(result["age_seconds"], 20)
        self.assertFalse(result["stale"])
        self.assertNotIn("music", result)
        self.assertIn("美团", result["summary"])

    def test_projection_drops_redundant_title_and_marks_old_reports(self):
        snapshot = _mobile_snapshot()
        snapshot["activity"].update(display_title="美团", age_seconds=900, stale=False)
        result = project_phone_context(snapshot, observed_at=AT)
        self.assertNotIn("title", result)
        self.assertTrue(result["stale"])
        self.assertIn("未更新", result["summary"])

    def test_projection_uses_observation_time_when_the_gateway_omits_age(self):
        snapshot = _mobile_snapshot()
        snapshot["activity"].pop("age_seconds")
        result = project_phone_context(snapshot, observed_at=AT)
        self.assertGreater(result["age_seconds"], 0)
        self.assertTrue(result["stale"])

    def test_projection_reports_playing_music_and_rejects_unusable_activity(self):
        snapshot = _mobile_snapshot()
        snapshot["activity"]["music"] = {"playing": True, "source": "网易云音乐"}
        self.assertEqual(project_phone_context(snapshot, observed_at=AT)["music"], "网易云音乐")

        self.assertIsNone(project_phone_context(
            {"activity": {"available": False, "reason": "activity_disabled"}}, observed_at=AT))
        self.assertIsNone(project_phone_context(
            {"activity": {"available": True, "app_label": "  "}}, observed_at=AT))
        self.assertIsNone(project_phone_context({}, observed_at=AT))
        with self.assertRaises(ValueError):
            project_phone_context(_mobile_snapshot(), observed_at=AT.replace(tzinfo=None))


class PhonePromptTests(unittest.TestCase):
    def _application(self):
        return NS(messages=_messages(),
                  perception_settings=NS(capture=lambda *_args: {"settings": SETTINGS}))

    def _context(self, kind=ScopeKind.PRIVATE):
        return NS(scope=NS(kind=kind), key="phone-private",
                  actor=NS(), role_binding=NS(role=NS()))

    def test_phone_context_reaches_only_the_private_prompt(self):
        projection = project_phone_context(_mobile_snapshot(), observed_at=AT)
        with patch("astrbot_host.core_reply_context.phone_scene_snapshot",
                   return_value=projection) as phone:
            prompt = build_core_reply_prompt(
                self._application(), self._context(), NS(text="我在美团上看到一家店", event_id="phone"),
                at=AT)
        perception = json.loads(prompt.perception_context)
        self.assertEqual(perception["phone_context"], projection)
        self.assertIn(PHONE_CONTEXT_INSTRUCTION, prompt.perception_instructions)
        self.assertEqual(phone.call_count, 1)

        with patch("astrbot_host.core_reply_context.phone_scene_snapshot",
                   return_value=projection) as phone:
            prompt = build_core_reply_prompt(
                self._application(), self._context(ScopeKind.GROUP),
                NS(text="我在美团上看到一家店", event_id="phone-group"), at=AT)
        self.assertNotIn("phone_context", json.loads(prompt.perception_context))
        self.assertFalse(phone.called)

    def test_empty_phone_source_adds_no_instruction(self):
        with patch("astrbot_host.core_reply_context.phone_scene_snapshot", return_value=None):
            prompt = build_core_reply_prompt(
                self._application(), self._context(), NS(text="在吗", event_id="phone-empty"), at=AT)
        self.assertNotIn("phone_context", json.loads(prompt.perception_context))
        self.assertNotIn(PHONE_CONTEXT_INSTRUCTION, prompt.perception_instructions)


class PhoneHostAdapterTests(unittest.TestCase):
    def test_adapter_requires_private_target_role_and_consent(self):
        service = Mock()
        service.mobile_context.return_value = _mobile_snapshot()
        registry = Mock()
        registry.active_service.return_value = service
        app = NS(runtime=object(), native_tool_port=registry)
        actor = NS(principal=NS(native_account_id="user-1"))
        context = NS(scope=NS(kind=ScopeKind.PRIVATE), actor=actor,
                     role_binding=NS(role=NS()))
        with patch("astrbot_host.core_reply_health._role_enabled", return_value=True), \
             patch("astrbot_host.core_reply_health._consent_active", return_value=True):
            result = phone_scene_snapshot(app, context, AT)
        self.assertEqual(result["app"], "美团")
        service.mobile_context.assert_called_once_with("user-1")

        with patch("astrbot_host.core_reply_health._role_enabled", return_value=True), \
             patch("astrbot_host.core_reply_health._consent_active", return_value=False):
            self.assertIsNone(phone_scene_snapshot(app, context, AT))

        context.scope.kind = ScopeKind.GROUP
        self.assertIsNone(phone_scene_snapshot(app, context, AT))


if __name__ == "__main__":
    unittest.main()
