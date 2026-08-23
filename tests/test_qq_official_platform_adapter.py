# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
import json
from types import SimpleNamespace
from typing import Any

from astrbot_plugin_private_companion.event_dispatch import EventDispatchMixin
from astrbot_plugin_private_companion.llm_tool_actions import LlmToolActionsMixin
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi
from astrbot_plugin_private_companion.platform_compat import PlatformCompatibilityMixin
from astrbot_plugin_private_companion.proactive import ProactiveMixin
from astrbot_plugin_private_companion.proactive_engine import ProactiveEngineMixin
from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin
from astrbot_plugin_private_companion.qzone_integration import QzoneMixin


OFFICIAL_OPENID = "test-openid-owner-001"
OFFICIAL_UMO = f"测试官方实例:FriendMessage:{OFFICIAL_OPENID}"


class _FakePlatform:
    def __init__(self, instance_id: str = "测试官方实例", name: str = "qq_official", status: Any = None) -> None:
        self._meta = SimpleNamespace(id=instance_id, name=name, description="QQ 官方机器人")
        self.status = status

    def meta(self) -> Any:
        return self._meta


class _RejectingOfficialPlatform(_FakePlatform):
    def __init__(self) -> None:
        super().__init__()
        self.send_calls = 0

    async def send_by_session(self, session: Any, chain: Any) -> bool:
        self.send_calls += 1
        return False


class _FakePlatformManager:
    def __init__(self, *platforms: _FakePlatform) -> None:
        self._platforms = list(platforms)

    def get_insts(self) -> list[_FakePlatform]:
        return list(self._platforms)


class _PlatformHarness(PlatformCompatibilityMixin):
    def __init__(self, *platforms: _FakePlatform, target_platform: str = "aiocqhttp") -> None:
        self.context = SimpleNamespace(platform_manager=_FakePlatformManager(*platforms))
        self.target_platform = target_platform
        self.data: dict[str, Any] = {"users": {}}


class _CustomNamedOfficialEvent:
    unified_msg_origin = OFFICIAL_UMO

    @staticmethod
    def get_platform_name() -> str:
        return "测试官方实例"


class _BrokenPlatformGetterEvent(_CustomNamedOfficialEvent):
    @staticmethod
    def get_platform_name() -> str:
        raise RuntimeError("adapter getter failed")


class _DefaultUmoHarness(PlatformCompatibilityMixin, ProactiveMixin):
    def __init__(self, *platforms: _FakePlatform) -> None:
        self.context = SimpleNamespace(platform_manager=_FakePlatformManager(*platforms))
        self.target_platform = "aiocqhttp"
        self.data: dict[str, Any] = {"users": {}}
        self.private_user_aliases: dict[str, str] = {}
        self.private_user_delivery_aliases: dict[str, str] = {}

    @staticmethod
    def _normalize_private_identity_id(value: Any, limit: int = 128) -> str:
        text = str(value or "").strip()
        if ":FriendMessage:" in text:
            text = text.rsplit(":FriendMessage:", 1)[-1]
        return text[:limit]

    def _canonical_private_user_id(self, user_id: str) -> str:
        return self.private_user_aliases.get(str(user_id or ""), str(user_id or ""))

    def _note_private_user_umo(self, user_id: str, user: dict[str, Any] | None, umo: str) -> None:
        if isinstance(user, dict):
            user["last_inbound_umo"] = umo
            self._remember_private_delivery_route(user, umo, outcome="observed")
            user["umo"] = umo

    @staticmethod
    def _is_bot_self_user_id(user_id: str) -> bool:
        return False


class _ActionHarness(PlatformCompatibilityMixin, ProactiveEngineMixin):
    def __init__(self) -> None:
        self.context = SimpleNamespace(platform_manager=_FakePlatformManager(_FakePlatform()))
        self.target_platform = "qq_official"

    @staticmethod
    def _friend_sensitive_proactive_action(action: str) -> bool:
        return False

    @staticmethod
    def _photo_text_available(user: dict[str, Any] | None = None) -> bool:
        return True

    @staticmethod
    def _voice_available(user: dict[str, Any] | None = None) -> bool:
        return True


class _EventDispatchHarness(PlatformCompatibilityMixin, EventDispatchMixin):
    def __init__(self) -> None:
        self.context = SimpleNamespace(platform_manager=_FakePlatformManager(_FakePlatform()))
        self.target_platform = "aiocqhttp"
        self.platform_action_calls = 0

    async def _call_platform_action(self, event: Any, action: str, **kwargs: Any) -> None:
        self.platform_action_calls += 1


class _RejectingContext:
    def __init__(self) -> None:
        self.platform = _RejectingOfficialPlatform()
        self.platform_manager = _FakePlatformManager(self.platform)
        self.calls = 0

    async def send_message(self, session: Any, result: Any) -> bool:
        self.calls += 1
        return False


class _OfficialSendHarness(PlatformCompatibilityMixin, ProactiveMessageMixin):
    def __init__(self) -> None:
        self.context = _RejectingContext()
        self.target_platform = "aiocqhttp"
        self.enable_precise_platform_send = False
        self.direct_fallback_calls = 0

    @staticmethod
    def _forbidden_recall_hit(text: str) -> str:
        return ""

    @staticmethod
    def _chain_text_for_forbidden_recall(chain: list[Any]) -> str:
        return "测试消息"

    @staticmethod
    async def _trigger_proactive_decorating_hooks(umo: str, chain: list[Any]) -> list[Any]:
        return chain

    @staticmethod
    def _build_result_from_chain(chain: list[Any]) -> list[Any]:
        return chain

    @staticmethod
    def _is_onebot_event_checker_send_rejection(error: Any) -> bool:
        return False

    @staticmethod
    def _describe_send_target(umo: str, session: Any, platform: Any) -> str:
        return umo

    @staticmethod
    def _format_send_exception(error: Any) -> str:
        return str(error or "")

    async def _send_chain_components_via_onebot_direct(
        self,
        umo: str,
        session: Any,
        chain: list[Any],
    ) -> tuple[bool, str]:
        self.direct_fallback_calls += 1
        return False, "should not run"


class _PagePlugin(_PlatformHarness):
    def __init__(self) -> None:
        super().__init__(_FakePlatform())
        self.max_daily_messages = 3
        self.idle_minutes = 30
        self.min_interval_minutes = 10
        self.screen_peek_max_daily = 0
        self.photo_action_max_daily = 0

    @staticmethod
    def _private_user_role(user: dict[str, Any], user_id: str = "") -> str:
        return "owner"

    @staticmethod
    def _private_user_role_label(role: str) -> str:
        return "主要用户"

    @staticmethod
    def _format_timestamp_elapsed(value: Any) -> str:
        return "从未"

    @staticmethod
    def _format_next_proactive(user: dict[str, Any]) -> str:
        return "未计划"


class _OfficialQzoneHarness(PlatformCompatibilityMixin, QzoneMixin, LlmToolActionsMixin):
    def __init__(self, *platforms: _FakePlatform) -> None:
        self.context = SimpleNamespace(platform_manager=_FakePlatformManager(*(platforms or (_FakePlatform(),))))
        self.target_platform = "aiocqhttp"
        self.enabled = True
        self.enable_qzone_integration = True
        self.enable_qzone_comment_inbox = True
        self.enable_qzone_life_publish = True
        self.data = {"users": {OFFICIAL_OPENID: {"umo": OFFICIAL_UMO}}, "qzone_integration": {}}
        self.query_calls = 0

    async def _qzone_query_feeds(self, *args: Any, **kwargs: Any) -> list[Any]:
        self.query_calls += 1
        return []

    @staticmethod
    def _format_timestamp_elapsed(value: Any) -> str:
        return "从未"


class QqOfficialPlatformAdapterTests(unittest.IsolatedAsyncioTestCase):
    def test_custom_instance_id_is_resolved_from_umo_and_event(self) -> None:
        harness = _PlatformHarness(_FakePlatform())

        self.assertEqual("qq_official", harness._platform_kind_for_umo(OFFICIAL_UMO))
        self.assertEqual("qq_official", harness._platform_kind_for_event(_CustomNamedOfficialEvent()))
        self.assertEqual("qq_official", harness._platform_kind_for_event(_BrokenPlatformGetterEvent()))

    def test_qq_official_capability_profile_is_restricted_but_keeps_media(self) -> None:
        harness = _PlatformHarness(_FakePlatform())
        unsupported = (
            "onebot_actions",
            "poke",
            "input_status",
            "message_recall",
            "reply_quote",
            "segmented_reply",
            "merged_forward",
            "file",
            "qzone",
        )

        for capability in unsupported:
            with self.subTest(capability=capability):
                self.assertFalse(harness._platform_supports(capability, umo=OFFICIAL_UMO))
        self.assertTrue(harness._platform_supports("image", umo=OFFICIAL_UMO))
        self.assertTrue(harness._platform_supports("voice", umo=OFFICIAL_UMO))
        harness.enable_qq_official_segmented_reply = True
        self.assertTrue(harness._platform_supports("segmented_reply", umo=OFFICIAL_UMO))
        self.assertFalse(harness._platform_supports("merged_forward", umo=OFFICIAL_UMO))
        self.assertIn("只有实际工具/发送结果成功后", harness._platform_capability_prompt(_CustomNamedOfficialEvent()))
        self.assertIn("不支持 QQ 空间", harness._platform_capability_prompt(_CustomNamedOfficialEvent()))

    def test_qq_official_segmenting_gate_uses_the_explicit_opt_in(self) -> None:
        harness = _OfficialSendHarness()

        self.assertFalse(harness._segmented_platform_allows(umo=OFFICIAL_UMO))
        harness.enable_qq_official_segmented_reply = True
        self.assertTrue(harness._segmented_platform_allows(umo=OFFICIAL_UMO))

    def test_unique_running_instance_is_used_without_manual_mode_switch(self) -> None:
        harness = _DefaultUmoHarness(_FakePlatform())

        self.assertEqual("测试官方实例", harness._preferred_platform_instance_id())
        self.assertEqual(OFFICIAL_UMO, harness._default_private_umo_for_user_id(OFFICIAL_OPENID))

    def test_stale_default_umo_yields_to_the_running_platform_instance(self) -> None:
        harness = _DefaultUmoHarness(_FakePlatform())
        user = {"user_id": OFFICIAL_OPENID, "umo": f"default:FriendMessage:{OFFICIAL_OPENID}"}
        harness.data["users"][OFFICIAL_OPENID] = user

        self.assertEqual(OFFICIAL_UMO, harness._private_delivery_umo_for_user_id(OFFICIAL_OPENID))

    def test_explicit_chat_binding_outranks_an_older_successful_route(self) -> None:
        fallback_umo = f"备用实例:FriendMessage:{OFFICIAL_OPENID}"
        harness = _DefaultUmoHarness(
            _FakePlatform(),
            _FakePlatform(instance_id="备用实例", name="qq_official"),
        )
        user = {"user_id": OFFICIAL_OPENID}
        harness.data["users"][OFFICIAL_OPENID] = user
        harness._remember_private_delivery_route(user, fallback_umo, outcome="success")

        ok, _ = harness._bind_private_delivery_umo(OFFICIAL_OPENID, user, OFFICIAL_UMO)

        self.assertTrue(ok)
        self.assertEqual(OFFICIAL_UMO, harness._private_delivery_umo_for_user_id(OFFICIAL_OPENID))
        self.assertEqual("bound", harness._private_delivery_route_status(OFFICIAL_OPENID, user)["source"])

    def test_unbind_restores_automatic_route_selection(self) -> None:
        harness = _DefaultUmoHarness(_FakePlatform())
        user = {"user_id": OFFICIAL_OPENID}
        harness.data["users"][OFFICIAL_OPENID] = user
        harness._bind_private_delivery_umo(OFFICIAL_OPENID, user, OFFICIAL_UMO)

        changed, message = harness._unbind_private_delivery_umo(user)

        self.assertTrue(changed)
        self.assertNotIn("bound_delivery_umo", user)
        self.assertIn("自动选择", message)

    def test_delivery_alias_reuses_observed_official_umo_in_mixed_deployment(self) -> None:
        harness = _DefaultUmoHarness(
            _FakePlatform(),
            _FakePlatform(instance_id="默认QQ", name="aiocqhttp"),
        )
        canonical_id = "10001"
        harness.private_user_aliases = {OFFICIAL_OPENID: canonical_id}
        harness.private_user_delivery_aliases = {canonical_id: OFFICIAL_OPENID}
        user = {"user_id": canonical_id, "umo": "默认QQ:FriendMessage:10001"}
        harness.data["users"][canonical_id] = user

        harness._note_private_user_umo(canonical_id, user, OFFICIAL_UMO)

        self.assertEqual(OFFICIAL_UMO, user["umo"])
        self.assertEqual(OFFICIAL_UMO, harness._private_delivery_umo_for_user_id(canonical_id))
        self.assertFalse(harness._ensure_private_user_umo(canonical_id, user))

    def test_delivery_alias_accepts_complete_official_umo(self) -> None:
        harness = _DefaultUmoHarness(
            _FakePlatform(),
            _FakePlatform(instance_id="默认QQ", name="aiocqhttp"),
        )
        canonical_id = "10001"
        harness.private_user_delivery_aliases = {canonical_id: OFFICIAL_UMO}
        user = {"user_id": canonical_id, "umo": "默认QQ:FriendMessage:10001"}
        harness.data["users"][canonical_id] = user

        changed = harness._ensure_private_user_umo(canonical_id, user)

        self.assertTrue(changed)
        self.assertEqual(OFFICIAL_UMO, user["umo"])
        self.assertEqual(OFFICIAL_OPENID, harness._private_delivery_user_id_for(canonical_id))

    def test_failed_route_yields_to_an_observed_route_for_same_official_user(self) -> None:
        harness = _DefaultUmoHarness(
            _FakePlatform(),
            _FakePlatform(instance_id="默认QQ", name="aiocqhttp"),
        )
        wrong_umo = f"默认QQ:FriendMessage:{OFFICIAL_OPENID}"
        user = {"user_id": OFFICIAL_OPENID, "umo": wrong_umo}
        harness.data["users"][OFFICIAL_OPENID] = user
        harness._remember_private_delivery_route(user, wrong_umo, outcome="observed")
        harness._remember_private_delivery_route(user, OFFICIAL_UMO, outcome="observed")

        harness._note_private_delivery_failure(
            OFFICIAL_OPENID,
            user,
            wrong_umo,
            "ServerError: invalid request",
        )

        self.assertEqual(OFFICIAL_UMO, user["umo"])
        self.assertEqual(OFFICIAL_UMO, harness._private_delivery_umo_for_user_id(OFFICIAL_OPENID))

        route_status = harness._private_delivery_route_status(OFFICIAL_OPENID, user)
        self.assertEqual(OFFICIAL_UMO, route_status["umo"])
        self.assertEqual("inbound", route_status["source"])
        self.assertEqual(2, route_status["route_count"])
        self.assertIn("invalid request", route_status["recent_error"])
        self.assertEqual(wrong_umo, route_status["recent_error_umo"])

    def test_route_status_reports_explicit_complete_umo(self) -> None:
        harness = _DefaultUmoHarness(_FakePlatform())
        canonical_id = "10001"
        harness.private_user_delivery_aliases = {canonical_id: OFFICIAL_UMO}
        user = {"user_id": canonical_id, "umo": OFFICIAL_UMO}
        harness.data["users"][canonical_id] = user

        route_status = harness._private_delivery_route_status(canonical_id, user)

        self.assertEqual(OFFICIAL_UMO, route_status["umo"])
        self.assertEqual("explicit", route_status["source"])
        self.assertEqual("管理员指定完整会话", route_status["source_label"])

    def test_explicit_custom_instance_and_stopped_instances_are_resolved_safely(self) -> None:
        second = _FakePlatform(instance_id="备用测试实例")
        explicit = _PlatformHarness(_FakePlatform(), second, target_platform="测试官方实例")
        running_only = _PlatformHarness(
            _FakePlatform(instance_id="停用实例", status=SimpleNamespace(name="STOPPED")),
            _FakePlatform(instance_id="测试官方实例"),
            target_platform="qq_official",
        )

        self.assertEqual("测试官方实例", explicit._preferred_platform_instance_id())
        self.assertEqual("测试官方实例", running_only._preferred_platform_instance_id())

    def test_unavailable_poke_falls_back_to_text_but_photo_and_voice_remain(self) -> None:
        harness = _ActionHarness()
        user = {"umo": OFFICIAL_UMO}

        self.assertEqual("message", harness._fallback_action_for_unavailable("poke", user))
        self.assertEqual("photo_text+voice", harness._fallback_action_for_unavailable("photo_text+voice", user))

    async def test_quote_and_recall_actions_are_not_called_on_qq_official(self) -> None:
        harness = _EventDispatchHarness()
        event = _BrokenPlatformGetterEvent()

        self.assertFalse(await harness._try_delete_message(event, "message-1", reason="测试"))
        self.assertEqual(0, harness.platform_action_calls)
        self.assertIsNone(harness._make_reply_component("message-1", event))

    async def test_failed_official_send_never_enters_onebot_direct_fallback(self) -> None:
        harness = _OfficialSendHarness()

        with self.assertRaisesRegex(RuntimeError, "不使用 OneBot 原生兜底"):
            await harness._send_chain_components(OFFICIAL_UMO, ["测试消息"])
        self.assertEqual(1, harness.context.calls)
        self.assertEqual(0, harness.direct_fallback_calls)

    async def test_qzone_is_not_exposed_or_called_on_qq_official(self) -> None:
        harness = _OfficialQzoneHarness()
        event = _CustomNamedOfficialEvent()

        self.assertFalse(harness._qzone_platform_supported(event))
        self.assertFalse(harness._qzone_available())
        self.assertEqual("", harness._qzone_tool_instruction(event))
        view = json.loads(await harness._pc_qzone_view_feed_impl(event))
        publish = json.loads(await harness._pc_qzone_publish_feed_impl(event, "测试说说"))
        self.assertEqual("unsupported_platform", view["status"])
        self.assertEqual("unsupported_platform", publish["status"])
        self.assertIn("QQ 官方机器人不支持 QQ 空间", view["message"])
        self.assertEqual(0, harness.query_calls)
        await harness._maybe_process_qzone_comment_inbox()
        await harness._maybe_publish_qzone_life_post()
        self.assertEqual(0, harness.query_calls)

        summary = PrivateCompanionPageApi(harness)._qzone_summary(harness.data)
        self.assertFalse(summary["platform_supported"])
        self.assertFalse(summary["available"])
        self.assertFalse(summary["enabled"])

    def test_mixed_deployment_keeps_onebot_qzone_without_exposing_it_to_official_event(self) -> None:
        harness = _OfficialQzoneHarness(
            _FakePlatform(),
            _FakePlatform(instance_id="默认QQ", name="aiocqhttp"),
        )

        self.assertTrue(harness._qzone_available())
        self.assertFalse(harness._qzone_available(_CustomNamedOfficialEvent()))

    def test_qzone_remains_available_for_onebot(self) -> None:
        harness = _PlatformHarness(_FakePlatform(instance_id="默认QQ", name="aiocqhttp"))

        self.assertTrue(harness._platform_kind_available("onebot"))
        self.assertTrue(harness._platform_supports("qzone", umo="默认QQ:FriendMessage:10001"))

    async def test_precise_send_false_is_not_counted_as_success(self) -> None:
        harness = _OfficialSendHarness()
        harness.enable_precise_platform_send = True

        with self.assertRaisesRegex(RuntimeError, "精确平台发送返回 False"):
            await harness._send_chain_components(OFFICIAL_UMO, ["测试消息"])
        self.assertEqual(1, harness.context.platform.send_calls)
        self.assertEqual(1, harness.context.calls)
        self.assertEqual(0, harness.direct_fallback_calls)

    def test_page_summary_treats_openid_as_stable_qq_official_identity(self) -> None:
        page = PrivateCompanionPageApi(_PagePlugin())
        summary = page._user_summary(
            OFFICIAL_OPENID,
            {"umo": OFFICIAL_UMO, "nickname": "默认用户", "enabled": True},
        )

        self.assertEqual("qq_official", summary["platform_kind"])
        self.assertEqual("QQ 官方机器人", summary["platform_label"])
        self.assertTrue(summary["stable_platform_identity"])
        self.assertFalse(summary["is_qq_user"])
        self.assertTrue(summary["display_name"].startswith("QQ 官方 · "))

    def test_official_openid_is_accepted_as_worldbook_identity(self) -> None:
        plugin = _PagePlugin()
        plugin.data["users"][OFFICIAL_OPENID] = {"umo": OFFICIAL_UMO}
        page = PrivateCompanionPageApi(plugin)

        self.assertTrue(page._worldbook_known_opaque_member_id(OFFICIAL_OPENID))
        self.assertTrue(page._worldbook_member_id_valid(OFFICIAL_OPENID, allow_opaque=True))
        self.assertTrue(
            page._worldbook_setup_member_id_valid(
                OFFICIAL_OPENID,
                target_ids=[OFFICIAL_OPENID],
                target_platform="qq_official",
            )
        )
        screenshot_openid = "2BEEB3934D9528AC571554425261FCAB"
        self.assertTrue(
            page._worldbook_setup_member_id_valid(
                screenshot_openid,
                target_ids=[screenshot_openid],
                target_platform="qq_official",
            )
        )
        self.assertFalse(page._worldbook_member_id_valid("abc", allow_opaque=True))

    def test_unknown_opaque_worldbook_identity_stays_rejected(self) -> None:
        page = PrivateCompanionPageApi(_PagePlugin())

        self.assertFalse(page._worldbook_member_id_valid(OFFICIAL_OPENID))
        self.assertFalse(page._worldbook_known_opaque_member_id(OFFICIAL_OPENID))
        self.assertFalse(
            page._worldbook_setup_member_id_valid(
                OFFICIAL_OPENID,
                target_ids=[OFFICIAL_OPENID],
                target_platform="aiocqhttp",
            )
        )
        self.assertFalse(
            page._worldbook_setup_member_id_valid(
                OFFICIAL_OPENID,
                target_ids=["another-user"],
                target_platform="qq_official",
            )
        )

    def test_overview_reports_automatic_qq_official_adaptation(self) -> None:
        harness = _PlatformHarness(_FakePlatform(), target_platform="qq_official")
        harness.data["users"][OFFICIAL_OPENID] = {"umo": OFFICIAL_UMO}

        overview = harness._platform_adaptation_overview()

        self.assertTrue(overview["auto_detect"])
        self.assertFalse(overview["manual_mode_required"])
        self.assertTrue(overview["qq_official_detected"])
        official = next(item for item in overview["profiles"] if item["kind"] == "qq_official")
        self.assertFalse(official["capabilities"]["reply_quote"])
        self.assertTrue(official["capabilities"]["voice"])


if __name__ == "__main__":
    unittest.main()
