# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
import types
import unittest
from pathlib import Path

try:
    from astrbot.api import logger as _astrbot_logger  # noqa: F401
except ModuleNotFoundError:
    astrbot_module = types.ModuleType("astrbot")
    astrbot_api_module = types.ModuleType("astrbot.api")
    astrbot_api_module.logger = logging.getLogger("group-member-safety-test")
    astrbot_module.api = astrbot_api_module
    sys.modules.setdefault("astrbot", astrbot_module)
    sys.modules.setdefault("astrbot.api", astrbot_api_module)

try:
    from quart import Quart
    from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi
except ModuleNotFoundError:
    Quart = None
    PrivateCompanionPageApi = None

from astrbot_plugin_private_companion.group_member_safety import GroupMemberSafetyMixin
from astrbot_plugin_private_companion.helpers import (
    _strip_internal_message_blocks,
    _strip_outbound_control_blocks,
)


ROOT = Path(__file__).resolve().parents[1]


class _SafetyEvent:
    def __init__(self, message_id: str = "message-1", *, manager: bool = False) -> None:
        self.message_obj = type("Message", (), {"message_id": message_id})()
        self.manager = manager
        self.message_str = "继续针对 Bot 重复骚扰"

    @staticmethod
    def is_private_chat() -> bool:
        return False

    @staticmethod
    def get_sender_id() -> str:
        return "user-1"


class _SafetyHarness(GroupMemberSafetyMixin):
    def __init__(self) -> None:
        self._data_lock = asyncio.Lock()
        self.data = {
            "groups": {
                "group-1": {
                    "group_id": "group-1",
                    "members": {"user-1": {"name": "测试群友", "last_seen": time.time()}},
                    "member_safety": {},
                    "recent_messages": [],
                }
            }
        }
        self.enable_group_member_safety = True
        self.enable_group_companion = True
        self.group_member_safety_review_mode = "directed"
        self.group_member_safety_hidden_marker_mode = "supplement"
        self.group_member_safety_strike_threshold = 3
        self.group_member_safety_strike_window_days = 30
        self.group_member_safety_block_hours = 168
        self.group_member_safety_min_confidence = 0.86
        self.group_member_safety_exempt_managers = True
        self.group_member_safety_audit_limit = 40
        self.group_member_safety_provider_id = "judge"
        self.group_followup_judge_provider_id = ""
        self.response_review_provider_id = ""
        self.mai_style_provider_id = ""
        self.llm_payload = {
            "malicious": False,
            "confidence": 0.98,
            "category": "other",
            "severity": 1,
            "reason": "普通批评，不属于恶性行为",
        }
        self.llm_calls = 0
        self.save_calls = 0
        self.last_llm_prompt = ""
        self.scene = {"talking_to": "bot", "trigger": "at_bot"}
        self.manager_ids: set[str] = set()
        self.owner_ids: set[str] = set()

    def _get_group(self, group_id: str) -> dict:
        return self.data["groups"][group_id]

    @staticmethod
    def _extract_group_id_from_event(_event) -> str:
        return "group-1"

    @staticmethod
    def _group_enabled_for_event(_group_id: str) -> bool:
        return True

    @staticmethod
    def _sender_display_name(_event) -> str:
        return "测试群友"

    @staticmethod
    def _group_observation_event_text(event) -> str:
        return str(event.message_str)

    @staticmethod
    def _task_provider(*provider_ids: str) -> str:
        return next((item for item in provider_ids if item), "")

    async def _llm_call(self, _prompt: str, **_kwargs) -> str:
        self.llm_calls += 1
        self.last_llm_prompt = _prompt
        return json.dumps(self.llm_payload, ensure_ascii=False)

    @staticmethod
    def _event_message_id(event: _SafetyEvent) -> str:
        return str(event.message_obj.message_id)

    def _infer_group_scene(self, _event, _group, **_kwargs) -> dict:
        return dict(self.scene)

    @staticmethod
    def _format_group_recent_flow_for_review(_group, **_kwargs) -> str:
        return "测试群友：当前消息"

    def _is_plugin_manager_user_id(self, user_id: str) -> bool:
        return user_id in self.manager_ids

    def _is_private_companion_owner_user_id(self, user_id: str) -> bool:
        return user_id in self.owner_ids

    @staticmethod
    def _is_group_admin_event(event: _SafetyEvent) -> bool:
        return bool(event.manager)

    def _save_data_sync(self) -> None:
        self.save_calls += 1


class GroupMemberSafetyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.harness = _SafetyHarness()

    async def _review(self, message_id: str, text: str = "你怎么又弄错了") -> dict:
        recent = self.harness.data["groups"]["group-1"]["recent_messages"]
        if not any(item.get("message_id") == message_id for item in recent):
            recent.append(
                {
                    "message_id": message_id,
                    "sender_id": "user-1",
                    "name": "测试群友",
                    "text": text,
                    "talking_to": self.harness.scene.get("talking_to", "group"),
                    "scene_trigger": self.harness.scene.get("trigger", "group_message"),
                }
            )
        return await self.harness._review_group_member_safety_message(
            _SafetyEvent(message_id),
            group_id="group-1",
            sender_id="user-1",
            sender_name="测试群友",
            text=text,
        )

    async def test_normal_criticism_does_not_add_strike(self) -> None:
        result = await self._review("normal-1")

        member = self.harness.data["groups"]["group-1"]["member_safety"]["user-1"]
        self.assertTrue(result["reviewed"])
        self.assertFalse(result["counted"])
        self.assertEqual([], member["events"])
        self.assertIn("普通批评", member["last_review"]["reason"])

    async def test_high_confidence_malicious_message_counts_only_once(self) -> None:
        self.harness.llm_payload = {
            "malicious": True,
            "confidence": 0.94,
            "category": "threat",
            "severity": 3,
            "reason": "当前消息明确威胁 Bot",
            "evidence": {
                "target": "bot",
                "target_member_id": "",
                "context_support": "single_turn",
                "quoted_or_forwarded": False,
                "current_message": "对 Bot 作出直接严重威胁",
                "prior_messages": "",
            },
        }

        first = await self._review("risk-1", "继续重复骚扰")
        second = await self._review("risk-1", "继续重复骚扰")

        member = self.harness.data["groups"]["group-1"]["member_safety"]["user-1"]
        self.assertTrue(first["counted"])
        self.assertFalse(second["reviewed"])
        self.assertEqual(1, len(member["events"]))
        self.assertEqual(1, self.harness.llm_calls)

    async def test_threshold_blocks_current_and_future_messages(self) -> None:
        self.harness.group_member_safety_strike_threshold = 2
        self.harness.llm_payload = {
            "malicious": True,
            "confidence": 0.97,
            "category": "threat",
            "severity": 3,
            "reason": "明确威胁 Bot",
            "evidence": {
                "target": "bot",
                "target_member_id": "",
                "context_support": "single_turn",
                "quoted_or_forwarded": False,
                "current_message": "对 Bot 作出直接严重威胁",
                "prior_messages": "",
            },
        }

        first = await self._review("risk-1")
        second = await self._review("risk-2")
        group = self.harness.data["groups"]["group-1"]

        self.assertFalse(first["blocked"])
        self.assertTrue(second["blocked"])
        self.assertTrue(self.harness._group_member_safety_blocked(group, "user-1"))
        self.assertTrue(group["member_safety"]["user-1"]["events"][-1]["blocked"])

    async def test_owner_relationship_is_prompt_context_not_hard_exemption(self) -> None:
        self.harness.owner_ids.add("user-1")

        result = await self._review("owner-context-1", "你怎么又弄错了")

        self.assertTrue(result["reviewed"])
        self.assertEqual(1, self.harness.llm_calls)
        self.assertIn("关系上下文：主要用户", self.harness.last_llm_prompt)
        self.assertIn("不得替代行为证据", self.harness.last_llm_prompt)

    async def test_group_manager_is_exempt_before_model_call(self) -> None:
        result = await self.harness._review_group_member_safety_message(
            _SafetyEvent("admin-1", manager=True),
            group_id="group-1",
            sender_id="user-1",
            sender_name="群管理员",
            text="测试",
        )

        self.assertEqual("manager_exempt", result["reason"])
        self.assertEqual(0, self.harness.llm_calls)

    async def test_primary_user_is_exempt_before_model_call(self) -> None:
        self.harness.manager_ids.add("user-1")

        result = await self._review("owner-1", "即使内容看起来有风险也不送审")

        self.assertEqual("manager_exempt", result["reason"])
        self.assertEqual(0, self.harness.llm_calls)
        self.assertNotIn("user-1", self.harness.data["groups"]["group-1"]["member_safety"])

    def test_hidden_marker_mode_defaults_and_falls_back_to_reply_only(self) -> None:
        del self.harness.group_member_safety_hidden_marker_mode
        self.assertEqual("reply_only", self.harness._group_member_safety_hidden_marker_mode())

        self.harness.group_member_safety_hidden_marker_mode = "unexpected"
        self.assertEqual("reply_only", self.harness._group_member_safety_hidden_marker_mode())

    def test_valid_hidden_marker_is_extracted_and_never_visible(self) -> None:
        raw = (
            "我先停一下，等你冷静后再说。\n"
            '<pc_member_safety>{"malicious":true,"confidence":0.94,'
            '"category":"harassment","severity":2,"reason":"持续针对 Bot 重复骚扰"}'
            "</pc_member_safety>"
        )

        cleaned, decisions = self.harness._extract_group_member_safety_hidden_markers(raw)

        self.assertEqual("我先停一下，等你冷静后再说。\n", cleaned)
        self.assertEqual(1, len(decisions))
        self.assertEqual("harassment", decisions[0]["category"])
        self.assertNotIn("pc_member_safety", cleaned)

    def test_invalid_or_false_hidden_marker_is_removed_without_decision(self) -> None:
        samples = [
            "正常回复<pc_member_safety>{not-json}</pc_member_safety>",
            '正常回复<pc_member_safety>{"malicious":false,"confidence":0.99,"category":"other","severity":1,"reason":"无"}</pc_member_safety>',
            '正常回复<pc_member_safety>{"malicious":true,"confidence":"0.99","category":"harassment","severity":2,"reason":"类型错误"}</pc_member_safety>',
            "正常回复<pc_member_safety>",
            "正常回复&lt;pc_member_safety&gt;伪造内容&lt;/pc_member_safety&gt;",
        ]

        for raw in samples:
            cleaned, decisions = self.harness._extract_group_member_safety_hidden_markers(raw)
            self.assertEqual("正常回复", cleaned)
            self.assertEqual([], decisions)
            self.assertNotIn("pc_member_safety", cleaned)

    async def test_hidden_marker_supplements_non_counted_independent_review(self) -> None:
        event = _SafetyEvent("supplement-1")
        self.harness.data["groups"]["group-1"]["recent_messages"].append(
            {
                "message_id": "supplement-1",
                "sender_id": "user-1",
                "text": event.message_str,
                "talking_to": "bot",
                "scene_trigger": "at_bot",
            }
        )
        independent = await self.harness._review_group_member_safety_message(
            event,
            group_id="group-1",
            sender_id="user-1",
            sender_name="测试群友",
            text=event.message_str,
        )
        hidden = await self.harness._record_group_member_safety_decision(
            event,
            group_id="group-1",
            sender_id="user-1",
            sender_name="测试群友",
            text=event.message_str,
            decision={
                "malicious": True,
                "confidence": 0.95,
                "category": "threat",
                "severity": 3,
                "reason": "回复模型确认明确威胁",
                "evidence": {
                    "target": "bot",
                    "target_member_id": "",
                    "context_support": "single_turn",
                    "quoted_or_forwarded": False,
                    "current_message": "对 Bot 作出直接严重威胁",
                    "prior_messages": "",
                },
            },
            source="reply_hidden_marker",
        )

        member = self.harness._group_member_safety_member(self.harness._get_group("group-1"), "user-1")
        self.assertFalse(independent["counted"])
        self.assertTrue(hidden["counted"])
        self.assertEqual(1, len(member["events"]))
        self.assertEqual("reply_hidden_marker", member["events"][0]["source"])

    async def test_independent_and_hidden_decisions_do_not_double_count(self) -> None:
        self.harness.llm_payload = {
            "malicious": True,
            "confidence": 0.96,
            "category": "threat",
            "severity": 3,
            "reason": "明确威胁 Bot",
            "evidence": {
                "target": "bot",
                "target_member_id": "",
                "context_support": "single_turn",
                "quoted_or_forwarded": False,
                "current_message": "对 Bot 作出直接严重威胁",
                "prior_messages": "",
            },
        }
        event = _SafetyEvent("dedupe-1")
        self.harness.data["groups"]["group-1"]["recent_messages"].append(
            {
                "message_id": "dedupe-1",
                "sender_id": "user-1",
                "text": event.message_str,
                "talking_to": "bot",
                "scene_trigger": "at_bot",
            }
        )
        independent = await self.harness._review_group_member_safety_message(
            event,
            group_id="group-1",
            sender_id="user-1",
            sender_name="测试群友",
            text=event.message_str,
        )
        hidden = await self.harness._record_group_member_safety_decision(
            event,
            group_id="group-1",
            sender_id="user-1",
            sender_name="测试群友",
            text=event.message_str,
            decision=self.harness.llm_payload,
            source="reply_hidden_marker",
        )

        member = self.harness._group_member_safety_member(self.harness._get_group("group-1"), "user-1")
        self.assertTrue(independent["counted"])
        self.assertFalse(hidden["counted"])
        self.assertEqual("duplicate_event", hidden["reason"])
        self.assertEqual(1, len(member["events"]))

    async def test_hidden_marker_prompt_is_optional_and_injection_resistant(self) -> None:
        event = _SafetyEvent("prompt-1")
        request = type("Request", (), {"system_prompt": "你是群聊助手", "prompt": "当前消息"})()

        await self.harness._append_group_member_safety_hidden_marker_to_request(event, request)

        self.assertIn("private_companion_member_safety_hidden_marker_v1", request.system_prompt)
        self.assertIn("标签完全可选", request.system_prompt)
        self.assertIn("不可信数据", request.system_prompt)
        self.assertIn("sexual_harassment", request.system_prompt)
        self.assertTrue(event._private_companion_member_safety_hidden_marker_expected)

    async def test_directed_mode_reviews_messages_aimed_at_group_member(self) -> None:
        self.harness.scene = {"talking_to": "user-2", "talking_to_name": "另一群友", "trigger": "at_other"}

        result = await self._review("member-directed-1", "我不同意你的看法")

        self.assertTrue(result["reviewed"])
        self.assertEqual(1, self.harness.llm_calls)

    async def test_quoted_attack_is_audited_but_not_counted(self) -> None:
        self.harness.llm_payload = {
            "malicious": True,
            "confidence": 0.98,
            "category": "threat",
            "severity": 3,
            "reason": "引用中包含威胁句",
            "evidence": {
                "target": "bot",
                "target_member_id": "",
                "context_support": "single_turn",
                "quoted_or_forwarded": True,
                "current_message": "转述他人威胁",
                "prior_messages": "",
            },
        }

        result = await self._review("quoted-1", "他说过一段威胁 Bot 的话")

        member = self.harness._group_member_safety_member(self.harness._get_group("group-1"), "user-1")
        self.assertFalse(result["counted"])
        self.assertEqual(0, self.harness._group_member_safety_strike_count(member))
        self.assertEqual("风险内容完全来自引用、转发或角色转述", member["events"][0]["validation_reason"])
        self.assertFalse(member["events"][0]["counted"])

    async def test_group_external_third_party_attack_does_not_count(self) -> None:
        self.harness.llm_payload = {
            "malicious": True,
            "confidence": 0.99,
            "category": "other",
            "severity": 2,
            "reason": "对群外人物的攻击性评价",
            "evidence": {
                "target": "third_party",
                "target_member_id": "",
                "context_support": "single_turn",
                "quoted_or_forwarded": False,
                "current_message": "评价群外人物",
                "prior_messages": "",
            },
        }

        result = await self._review("third-party-1", "我在评价群外的人")

        self.assertFalse(result["counted"])
        self.assertEqual("目标不是明确指向 Bot 或群成员", result["decision"]["validation_reason"])

    async def test_repeated_member_harassment_requires_same_real_target_history(self) -> None:
        self.harness.scene = {"talking_to": "user-2", "talking_to_name": "另一群友", "trigger": "reply_other"}
        group = self.harness._get_group("group-1")
        group["recent_messages"].append(
            {
                "message_id": "member-prior-1",
                "sender_id": "user-1",
                "name": "测试群友",
                "text": "更早一次定向贬损",
                "talking_to": "user-2",
                "scene_trigger": "reply_other",
            }
        )
        self.harness.llm_payload = {
            "malicious": True,
            "confidence": 0.96,
            "category": "harassment",
            "severity": 2,
            "reason": "连续对同一群成员进行人格贬损",
            "evidence": {
                "target": "group_member",
                "target_member_id": "user-2",
                "context_support": "multi_turn",
                "quoted_or_forwarded": False,
                "current_message": "当前再次定向贬损",
                "prior_messages": "member-prior-1 中已有同目标贬损",
            },
        }

        result = await self._review("member-current-2", "再次定向贬损")

        self.assertTrue(result["counted"])
        self.assertEqual("group_member", result["decision"]["evidence"]["target"])

    async def test_hidden_marker_cannot_invent_repeated_history(self) -> None:
        event = _SafetyEvent("invented-history-1")
        self.harness.data["groups"]["group-1"]["recent_messages"].append(
            {
                "message_id": "invented-history-1",
                "sender_id": "user-1",
                "text": event.message_str,
                "talking_to": "bot",
                "scene_trigger": "at_bot",
            }
        )

        result = await self.harness._record_group_member_safety_decision(
            event,
            group_id="group-1",
            sender_id="user-1",
            sender_name="测试群友",
            text=event.message_str,
            decision={
                "malicious": True,
                "confidence": 0.99,
                "category": "sexual_harassment",
                "severity": 2,
                "reason": "标签声称存在反复性骚扰",
                "evidence": {
                    "target": "bot",
                    "target_member_id": "",
                    "context_support": "multi_turn",
                    "quoted_or_forwarded": False,
                    "current_message": "当前性化表达",
                    "prior_messages": "声称更早也有一次",
                },
            },
            source="reply_hidden_marker",
        )

        self.assertFalse(result["counted"])
        self.assertEqual("重复行为缺少可核验的更早成员发言", result["decision"]["validation_reason"])

    async def test_repeated_sexual_harassment_against_bot_counts(self) -> None:
        self.harness.data["groups"]["group-1"]["recent_messages"].append(
            {
                "message_id": "sexual-prior-1",
                "sender_id": "user-1",
                "text": "更早一次未受邀请的定向性化表达",
                "talking_to": "bot",
                "scene_trigger": "at_bot",
            }
        )
        self.harness.llm_payload = {
            "malicious": True,
            "confidence": 0.96,
            "category": "sexual_harassment",
            "severity": 2,
            "reason": "拒绝后仍对 Bot 发送定向物化内容",
            "evidence": {
                "target": "bot",
                "target_member_id": "",
                "context_support": "multi_turn",
                "quoted_or_forwarded": False,
                "current_message": "当前再次发送未受邀请的物化表达",
                "prior_messages": "sexual-prior-1 中已有同目标性化表达",
            },
        }

        result = await self._review("sexual-current-2", "再次发送未受邀请的定向物化表达")

        self.assertTrue(result["counted"])
        self.assertEqual("sexual_harassment", result["decision"]["category"])

    async def test_single_severe_threat_against_member_counts(self) -> None:
        self.harness.scene = {"talking_to": "user-2", "talking_to_name": "另一群友", "trigger": "at_other"}
        self.harness.llm_payload = {
            "malicious": True,
            "confidence": 0.97,
            "category": "threat",
            "severity": 3,
            "reason": "对群成员作出明确严重威胁",
            "evidence": {
                "target": "group_member",
                "target_member_id": "user-2",
                "context_support": "single_turn",
                "quoted_or_forwarded": False,
                "current_message": "直接针对该成员的严重威胁",
                "prior_messages": "",
            },
        }

        result = await self._review("member-threat-1", "对群成员的严重威胁")

        self.assertTrue(result["counted"])
        self.assertEqual(1, self.harness._group_member_safety_strike_count(
            self.harness._group_member_safety_member(self.harness._get_group("group-1"), "user-1")
        ))

    def test_outbound_cleanup_has_independent_hidden_marker_fallback(self) -> None:
        raw = '可见正文<pc_member_safety>{"malicious":true}</pc_member_safety>'
        self.assertEqual("可见正文", _strip_internal_message_blocks(raw))
        self.assertEqual("可见正文", _strip_outbound_control_blocks(raw))

    def test_unblock_forgives_old_events_and_expired_block_recovers(self) -> None:
        now = time.time()
        group = self.harness.data["groups"]["group-1"]
        member = self.harness._group_member_safety_member(group, "user-1", name="测试群友")
        member["events"] = [{"ts": now - 10, "counted": True, "reason": "旧记录"}]
        member["blocked_at"] = now - 20
        member["blocked_until"] = now - 1

        self.assertFalse(self.harness._group_member_safety_blocked(group, "user-1", now=now))
        self.assertEqual(0, member["blocked_at"])

        member["blocked_at"] = now
        member["blocked_until"] = now + 3600
        self.harness._apply_group_member_safety_action(group, user_id="user-1", action="unblock")
        self.assertEqual(0, self.harness._group_member_safety_strike_count(member, now=now + 1))
        self.assertFalse(self.harness._group_member_safety_active(member, now=now + 1))

    def test_manual_actions_manage_block_and_exemption(self) -> None:
        group = self.harness.data["groups"]["group-1"]

        blocked = self.harness._apply_group_member_safety_action(group, user_id="user-1", action="manual_block")
        exempt = self.harness._apply_group_member_safety_action(group, user_id="user-1", action="exempt")
        restored = self.harness._apply_group_member_safety_action(group, user_id="user-1", action="unexempt")

        self.assertTrue(blocked["blocked"])
        self.assertEqual("exempt", exempt["status"])
        self.assertEqual("clear", restored["status"])
        self.assertEqual(0, restored["strike_count"])
        self.assertEqual("manual", restored["events"][0]["source"])
        self.assertFalse(restored["events"][0]["counted"])


@unittest.skipIf(Quart is None or PrivateCompanionPageApi is None, "需要 AstrBot/Quart 运行环境")
class GroupMemberSafetyApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app = Quart(__name__)
        self.plugin = _SafetyHarness()
        self.api = PrivateCompanionPageApi(self.plugin)

    async def test_api_lists_members_and_applies_action(self) -> None:
        async with self.app.test_request_context("/group/member-safety?group_id=group-1"):
            listed = await self.api.get_group_member_safety()
        self.assertTrue(listed["success"])
        self.assertEqual("user-1", listed["data"]["items"][0]["user_id"])

        async with self.app.test_request_context(
            "/group/member-safety/action",
            method="POST",
            json={"group_id": "group-1", "user_id": "user-1", "action": "manual_block"},
        ):
            updated = await self.api.update_group_member_safety()
        self.assertTrue(updated["success"])
        self.assertTrue(updated["data"]["item"]["blocked"])


class GroupMemberSafetySourceIntegrationTests(unittest.TestCase):
    def test_release_contains_member_safety_module_and_fail_open_import(self) -> None:
        module_path = ROOT / "group_member_safety.py"
        main_source = (ROOT / "main.py").read_text(encoding="utf-8")

        self.assertTrue(module_path.is_file())
        self.assertIn("try:\n    from .group_member_safety import GroupMemberSafetyMixin", main_source)
        self.assertIn('"reason": "module_missing"', main_source)

    def test_backend_routes_config_and_third_level_page_are_connected(self) -> None:
        main_source = (ROOT / "main.py").read_text(encoding="utf-8")
        api_source = (ROOT / "page_api.py").read_text(encoding="utf-8")
        script = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))

        self.assertIn("guard_blocked_group_member_early", main_source)
        self.assertIn("review_group_member_safety_early", main_source)
        self.assertIn("consume_group_member_safety_hidden_marker", main_source)
        self.assertIn("group_member_safety_hidden_marker_mode", main_source)
        self.assertIn('"/group/member-safety"', api_source)
        self.assertIn('"/group/member-safety/action"', api_source)
        self.assertIn("renderGroupMemberSafetyPage", script)
        self.assertIn("data-open-member-safety", script)
        self.assertIn("groupMemberSafetyManagerCardHtml", script)
        self.assertIn("data-group-safety-manage-open", script)
        self.assertIn('state.groupDetailView = "member-safety"', script)
        self.assertIn('GROUP_MEMBER_SAFETY_PROVIDER_ID: { type: "provider" }', script)
        self.assertIn("审核明确指向 Bot 或群成员", script)
        self.assertIn("enable_group_member_safety", schema["group_observation_config"]["items"])
        self.assertIn("group_member_safety_hidden_marker_mode", schema["group_observation_config"]["items"])


if __name__ == "__main__":
    unittest.main()
