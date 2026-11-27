"""Regression tests for core auto-consent parity and private device_context projection."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA
from astrbot_host.core_reply_context import build_core_reply_prompt
from astrbot_host.core_reply_health import _consent_active, authorized_mobile_snapshot
from astrbot_host.core_reply_mobile import device_scene_snapshot, location_scene_snapshot
from astrbot_host.core_reply_perception import (
    _PRIVATE_HOISTED_RULES,
    _PRIVATE_STATIC_CONTRACT,
    inject_core_reply_perception_context,
    inject_core_reply_perception_policy,
)
from companion_v2.conversation.device_context import (
    DEVICE_CONTEXT_INSTRUCTION,
    SNAPSHOT_VERSION,
    project_device_context,
)
from companion_v2.conversation.location_context import LOCATION_CONTEXT_INSTRUCTION
from companion_v2.conversation.phone_context import PHONE_CONTEXT_INSTRUCTION
from companion_v2.foundation.refs import ScopeKind, ScopeRef, SubjectKind, SubjectRef
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.storage.runtime import StorageRuntime


AT = datetime(2026, 11, 26, 14, 30, 0, tzinfo=timezone.utc)


def _device_snapshot(
    *,
    battery_percent=18,
    charging=False,
    app_state="foreground",
    online=True,
    age_seconds=45,
    stale=False,
    available=True,
):
    now_ts = AT.timestamp()
    report_ts = now_ts - age_seconds
    return {
        "available": True,
        "device": {
            "available": available,
            "online": online,
            "device_name": "Pixel 9",
            "platform": "android",
            "app_state": app_state,
            "battery_percent": battery_percent,
            "charging": charging,
            "captured_at": report_ts,
            "last_report_at": report_ts,
            "age_seconds": age_seconds,
            "stale": stale,
            "source": "android_device_status",
        },
        "location": {
            "available": True,
            "captured_at": report_ts,
            "received_at": report_ts,
            "place": {"matched": True, "name": "家", "kind": "home", "area_label": "上海市·徐汇区"},
        },
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
    "guidance": "",
    "calendar_timezone": "Asia/Shanghai",
    "calendar_enabled": False,
    "health_enabled": False,
    "platform_enabled": True,
    "model_route_enabled": True,
    "configured_modalities_enabled": True,
}


class CoreAutoConsentParityTests(unittest.TestCase):
    """_consent_active() must align with reality_access.py for CORE tier subjects."""

    def setUp(self):
        tmp = TemporaryDirectory(prefix="companion-v2-core-consent-")
        self.addCleanup(tmp.cleanup)
        self.runtime = StorageRuntime.open(data_root=Path(tmp.name) / "data", installation_id="inst-1")
        self.addCleanup(self.runtime.close)

    def _context(self, *, subject_id="user-1", kind=ScopeKind.PRIVATE):
        scope = ScopeRef("inst-1", "qq", kind, "200", "bot-1", "acct-1", "conv-1", "sess-1")
        subject = SubjectRef("inst-1", SubjectKind.HUMAN, subject_id)
        actor = NS(subject=subject, principal=NS(native_account_id="200"))
        return NS(scope=scope, actor=actor, role_binding=NS(role=SubjectRef("inst-1", SubjectKind.BOT, "role-a")))

    def _set_tier(self, subject_id: str, tier: AuthorizationTier):
        with self.runtime.transaction() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO subject_access_tiers
                   (subject_kind, subject_id, tier, revision, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (SubjectKind.HUMAN.value, subject_id, tier.value, 1, AT.isoformat()),
            )

    def test_core_subject_passes_consent_without_explicit_authorization_row(self):
        self._set_tier("core-admin", AuthorizationTier.CORE)
        ctx = self._context(subject_id="core-admin", kind=ScopeKind.PRIVATE)
        self.assertTrue(_consent_active(self.runtime, ctx))

        # Core subject in group chat MUST still be rejected (mobile telemetry is strictly private-only).
        group_ctx = self._context(subject_id="core-admin", kind=ScopeKind.GROUP)
        self.assertFalse(_consent_active(self.runtime, group_ctx))

    def test_standard_and_important_subjects_still_require_explicit_active_consent(self):
        self._set_tier("std-user", AuthorizationTier.STANDARD)
        self._set_tier("imp-user", AuthorizationTier.IMPORTANT)
        for uid in ("std-user", "imp-user"):
            with self.subTest(uid=uid):
                ctx = self._context(subject_id=uid, kind=ScopeKind.PRIVATE)
                self.assertFalse(_consent_active(self.runtime, ctx))

    def test_authorized_mobile_snapshot_delivers_for_core_without_explicit_row(self):
        self._set_tier("core-admin", AuthorizationTier.CORE)
        ctx = self._context(subject_id="core-admin", kind=ScopeKind.PRIVATE)
        service = Mock()
        service.mobile_context.return_value = _device_snapshot()
        registry = Mock()
        registry.active_service.return_value = service
        app = NS(runtime=self.runtime, native_tool_port=registry)

        with patch("astrbot_host.core_reply_health._role_enabled", return_value=True):
            snap = authorized_mobile_snapshot(app, ctx)
            loc = location_scene_snapshot(app, ctx, AT)
            dev = device_scene_snapshot(app, ctx, AT)

        self.assertIsNotNone(snap)
        self.assertEqual(loc["place_name"], "家")
        self.assertEqual(dev["battery_percent"], 18)


class DeviceContextProjectionTests(unittest.TestCase):
    def test_projects_low_battery_unplugged_device_cleanly(self):
        projected = project_device_context(_device_snapshot(battery_percent=15, charging=False), observed_at=AT)
        self.assertIsNotNone(projected)
        self.assertEqual(projected["version"], SNAPSHOT_VERSION)
        self.assertEqual(projected["privacy"], "private_only")
        self.assertEqual(projected["battery_percent"], 15)
        self.assertFalse(projected["charging"])
        self.assertEqual(projected["app_state"], "foreground")
        self.assertTrue(projected["online"])
        self.assertFalse(projected["stale"])
        self.assertIn("电量 15%（未充电）", projected["summary"])
        self.assertIn("应用在前台", projected["summary"])

    def test_projects_charging_background_and_stale_states(self):
        charging = project_device_context(
            _device_snapshot(battery_percent=82, charging=True, app_state="background", age_seconds=120),
            observed_at=AT,
        )
        self.assertTrue(charging["charging"])
        self.assertEqual(charging["app_state"], "background")
        self.assertIn("充电中", charging["summary"])
        self.assertIn("应用在后台", charging["summary"])

        stale = project_device_context(
            _device_snapshot(battery_percent=50, charging=False, age_seconds=600, online=True),
            observed_at=AT,
        )
        self.assertTrue(stale["stale"])
        self.assertFalse(stale["online"])
        self.assertIn("离线或超过 5 分钟未更新", stale["summary"])

    def test_rejects_expired_missing_or_naive_observed_at(self):
        self.assertIsNone(project_device_context(_device_snapshot(age_seconds=31 * 60), observed_at=AT))
        self.assertIsNone(project_device_context(_device_snapshot(available=False), observed_at=AT))
        self.assertIsNone(project_device_context({}, observed_at=AT))
        with self.assertRaises(ValueError):
            project_device_context(_device_snapshot(), observed_at=AT.replace(tzinfo=None))


class DevicePromptInjectionAndHoistingTests(unittest.IsolatedAsyncioTestCase):
    def _application(self):
        return NS(
            messages=_messages(),
            perception_settings=NS(capture=lambda *_args: {"settings": SETTINGS}),
        )

    def _context(self, kind=ScopeKind.PRIVATE):
        return NS(scope=NS(kind=kind, platform="qq"), key=f"dev-{kind.value}",
                  actor=NS(), role_binding=NS(role=NS()))

    async def test_device_context_injects_private_only_and_preserves_system_prompt_cache(self):
        projection = project_device_context(_device_snapshot(), observed_at=AT)
        app = self._application()
        priv_ctx = self._context(ScopeKind.PRIVATE)
        group_ctx = self._context(ScopeKind.GROUP)

        with patch("astrbot_host.core_reply_context.device_scene_snapshot", return_value=projection) as dev_mock:
            prompt = build_core_reply_prompt(app, priv_ctx, NS(text="手机快没电了", event_id="dev-1"), at=AT)
        self.assertEqual(dev_mock.call_count, 1)
        perception = json.loads(prompt.perception_context)
        self.assertEqual(perception["device_context"], projection)
        self.assertIn(DEVICE_CONTEXT_INSTRUCTION, prompt.perception_instructions)
        self.assertIn(DEVICE_CONTEXT_INSTRUCTION, _PRIVATE_HOISTED_RULES)
        self.assertIn(DEVICE_CONTEXT_INSTRUCTION, _PRIVATE_STATIC_CONTRACT)

        # Prompt injection hoists DEVICE_CONTEXT_INSTRUCTION into the cacheable
        # static system_prompt prefix once and keeps <companion_v2_context> compact.
        original_system = "STABLE_SYSTEM_PREFIX"
        req = NS(system_prompt=original_system, prompt="手机快没电了",
                 extra_user_content_parts=[], contexts=[], model="test-model")
        state = {"prepared": {
            "context": priv_ctx,
            "native_input": NS(text="手机快没电了", group_message_text=None),
            "perception_context": prompt.perception_context,
            "perception_instructions": prompt.perception_instructions,
            "perception_guidance": "",
            "perception_options": None,
        }}
        event = NS(
            get_extra=lambda key, default=None: state if key == CORE_REPLY_CONTEXT_EXTRA else default,
            is_at_or_wake_command=False,
            unified_msg_origin="qq:FriendMessage:200",
        )
        inject_core_reply_perception_policy(event, req)
        hoisted_system = req.system_prompt
        self.assertIn(DEVICE_CONTEXT_INSTRUCTION, hoisted_system)
        await inject_core_reply_perception_context(event, req)
        self.assertEqual(req.system_prompt, hoisted_system)
        self.assertEqual(len(req.extra_user_content_parts), 1)
        temp_text = req.extra_user_content_parts[0].text
        self.assertIn("device_context", temp_text)
        self.assertIn("电量 18%", temp_text)
        self.assertNotIn(DEVICE_CONTEXT_INSTRUCTION, temp_text)

        # Group chat MUST NOT receive device_context.
        with patch("astrbot_host.core_reply_context.device_scene_snapshot", return_value=projection) as dev_mock:
            group_prompt = build_core_reply_prompt(app, group_ctx, NS(text="电量多少", event_id="dev-g"), at=AT)
        self.assertFalse(dev_mock.called)
        self.assertNotIn("device_context", json.loads(group_prompt.perception_context))
        self.assertNotIn(DEVICE_CONTEXT_INSTRUCTION, group_prompt.perception_instructions)

    def test_balanced_instructions_preserve_privacy_and_natural_care(self):
        for instruction in (LOCATION_CONTEXT_INSTRUCTION, PHONE_CONTEXT_INSTRUCTION, DEVICE_CONTEXT_INSTRUCTION):
            self.assertIn("群聊和其他窗口不得使用", instruction)
            self.assertIn("不猜测、不补造", instruction)
            self.assertIn("监控", instruction)


if __name__ == "__main__":
    unittest.main()
