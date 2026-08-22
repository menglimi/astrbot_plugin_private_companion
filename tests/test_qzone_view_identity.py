# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from typing import Any

from astrbot_plugin_private_companion.llm_tool_actions import LlmToolActionsMixin
from astrbot_plugin_private_companion.qzone_integration import QzoneMixin


BOT_UIN = "10001"
USER_UIN = "88515653"
THIRD_PARTY_UIN = "70007"


class _Event:
    def __init__(self, sender_uin: str = USER_UIN) -> None:
        self.sender_uin = sender_uin
        self.message_str = "帮我看一下空间动态"

    def get_sender_id(self) -> str:
        return self.sender_uin


class _QzoneViewHarness(LlmToolActionsMixin, QzoneMixin):
    def __init__(self, post_uin: str, *, bot_uin: str = BOT_UIN) -> None:
        self.enabled = True
        self.enable_qzone_integration = True
        self.data: dict[str, Any] = {"qzone_integration": {}}
        self.bot_uin = bot_uin
        self.post = SimpleNamespace(
            tid="post-1",
            uin=post_uin,
            name="琳沐",
            text="也是从广东深圳，广东珠海，玩到了西藏山南。",
            rt_con="",
            images=[],
        )
        self.cookie_calls = 0
        self.query_targets: list[str] = []

    @staticmethod
    def _qzone_available(_event=None) -> bool:
        return True

    async def _qzone_get_cookies(self, _event=None) -> str:
        self.cookie_calls += 1
        return f"uin=o{self.bot_uin}; skey=test"

    def _qzone_context_from_cookies(self, _cookie_header: str) -> dict[str, Any]:
        return {"uin": int(self.bot_uin)}

    async def _qzone_query_feeds(self, _event, *, target_id=None, **_kwargs):
        self.query_targets.append(str(target_id or ""))
        return [self.post]


class QzoneViewIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_sample_user_post_is_labeled_as_sourced_observation(self) -> None:
        harness = _QzoneViewHarness(USER_UIN)
        event = _Event()

        result = json.loads(
            await harness._pc_qzone_view_feed_impl(
                event,
                target_scope="current_user",
            )
        )

        self.assertEqual("success", result["status"])
        self.assertEqual("琳沐", result["author"])
        self.assertEqual(USER_UIN, result["uin"])
        self.assertEqual("current_user", result["identity"]["owner_role"])
        self.assertTrue(result["identity"]["owner_matches_target"])
        self.assertTrue(result["identity"]["pronoun_safe"])
        self.assertEqual(
            "external_observation_only",
            result["identity"]["memory_policy"],
        )
        self.assertEqual([USER_UIN], harness.query_targets)
        self.assertFalse(hasattr(event, "_private_companion_skip_long_term_memory"))
        self.assertEqual("current_user", event._private_companion_qzone_view_observations[-1]["owner_role"])

    async def test_bot_scope_uses_login_uin_and_labels_bot_self(self) -> None:
        harness = _QzoneViewHarness(BOT_UIN)
        event = _Event()

        result = json.loads(
            await harness._pc_qzone_view_feed_impl(event, target_scope="bot_self")
        )

        self.assertEqual("success", result["status"])
        self.assertEqual(BOT_UIN, result["identity"]["target_uin"])
        self.assertEqual(BOT_UIN, result["identity"]["owner_uin"])
        self.assertEqual("bot_self", result["identity"]["owner_role"])
        self.assertTrue(result["identity"]["current_persona_verified"])
        self.assertEqual(
            "verified_persona_observation",
            result["identity"]["memory_policy"],
        )
        self.assertEqual([BOT_UIN], harness.query_targets)

    async def test_secondary_persona_treats_bot_uin_as_shared_account(self) -> None:
        harness = _QzoneViewHarness(BOT_UIN)
        harness.enable_multi_persona_mode = True
        harness.plugin_specific_persona_id = "main"
        harness._active_persona_scope = lambda: "alt"

        result = json.loads(
            await harness._pc_qzone_view_feed_impl(
                _Event(),
                target_scope="self",
            )
        )

        self.assertEqual("success", result["status"])
        self.assertEqual("bot_self", result["identity"]["owner_role"])
        self.assertFalse(result["identity"]["current_persona_verified"])
        self.assertFalse(result["identity"]["pronoun_safe"])
        self.assertEqual(
            "shared_account_observation",
            result["identity"]["memory_policy"],
        )
        self.assertIn("不得表述为当前人格", result["identity"]["response_guard"])

    async def test_primary_persona_does_not_claim_unrecorded_shared_account_post(self) -> None:
        harness = _QzoneViewHarness(BOT_UIN)
        harness.enable_multi_persona_mode = True
        harness.plugin_specific_persona_id = "main"
        harness._active_persona_scope = lambda: "main"

        result = json.loads(
            await harness._pc_qzone_view_feed_impl(
                _Event(),
                target_scope="bot_self",
            )
        )

        self.assertEqual("success", result["status"])
        self.assertFalse(result["identity"]["current_persona_verified"])
        self.assertFalse(result["identity"]["pronoun_safe"])
        self.assertEqual(
            "shared_account_observation",
            result["identity"]["memory_policy"],
        )
        self.assertEqual("", result["identity"]["persona_verification_basis"])

    async def test_primary_persona_claims_post_matching_verified_publish_tid(self) -> None:
        harness = _QzoneViewHarness(BOT_UIN)
        harness.enable_multi_persona_mode = True
        harness.plugin_specific_persona_id = "main"
        harness._active_persona_scope = lambda: "main"
        harness.data["qzone_integration"]["recent_life_publish_texts"] = [
            {
                "text": "另一条正文也不影响 tid 核验",
                "tid": "post-1",
                "verified": True,
                "at": 0,
            }
        ]

        result = json.loads(
            await harness._pc_qzone_view_feed_impl(
                _Event(),
                target_scope="bot_self",
            )
        )

        self.assertEqual("success", result["status"])
        self.assertTrue(result["identity"]["current_persona_verified"])
        self.assertTrue(result["identity"]["pronoun_safe"])
        self.assertEqual(
            "verified_persona_observation",
            result["identity"]["memory_policy"],
        )
        self.assertEqual(
            "verified_publish_tid",
            result["identity"]["persona_verification_basis"],
        )

    async def test_persona_can_use_verified_exact_text_when_record_has_no_tid(self) -> None:
        harness = _QzoneViewHarness(BOT_UIN)
        harness.enable_multi_persona_mode = True
        harness.plugin_specific_persona_id = "main"
        harness._active_persona_scope = lambda: "main"
        harness.post.create_time = 1_775_000_000
        harness.data["qzone_integration"]["recent_life_publish_texts"] = [
            {
                "text": "也是从广东深圳，\n广东珠海，玩到了西藏山南。",
                "tid": "",
                "verified": True,
                "at": 1_775_000_120,
            }
        ]

        result = json.loads(
            await harness._pc_qzone_view_feed_impl(
                _Event(),
                target_scope="bot_self",
            )
        )

        self.assertTrue(result["identity"]["current_persona_verified"])
        self.assertEqual(
            "verified_publish_text",
            result["identity"]["persona_verification_basis"],
        )

    async def test_missing_target_returns_clarification_without_fetching_cookie(self) -> None:
        harness = _QzoneViewHarness(USER_UIN)

        result = json.loads(await harness._pc_qzone_view_feed_impl(_Event()))

        self.assertEqual("needs_target", result["status"])
        self.assertFalse(result["success"])
        self.assertFalse(result["target_verified"])
        self.assertFalse(result["should_retry"])
        self.assertEqual(0, harness.cookie_calls)
        self.assertEqual([], harness.query_targets)
        self.assertEqual("not_recorded", result["identity"]["memory_policy"])

    async def test_returned_author_uin_must_match_requested_target(self) -> None:
        harness = _QzoneViewHarness(USER_UIN)
        event = _Event()

        result = json.loads(
            await harness._pc_qzone_view_feed_impl(event, target_scope="bot_self")
        )

        self.assertEqual("target_mismatch", result["status"])
        self.assertEqual("identity_mismatch", result["identity"]["owner_role"])
        self.assertFalse(hasattr(event, "_private_companion_skip_long_term_memory"))
        self.assertEqual([BOT_UIN], harness.query_targets)
        self.assertNotIn("text", result)

    async def test_explicit_legacy_user_id_remains_a_third_party_target(self) -> None:
        harness = _QzoneViewHarness(THIRD_PARTY_UIN)

        result = json.loads(
            await harness._pc_qzone_view_feed_impl(_Event(), user_id=THIRD_PARTY_UIN)
        )

        self.assertEqual("success", result["status"])
        self.assertEqual("explicit_uin", result["identity"]["requested_scope"])
        self.assertEqual("third_party", result["identity"]["owner_role"])
        self.assertEqual([THIRD_PARTY_UIN], harness.query_targets)

    async def test_shared_bot_and_sender_uin_is_not_assigned_a_pronoun(self) -> None:
        harness = _QzoneViewHarness(BOT_UIN)

        result = json.loads(
            await harness._pc_qzone_view_feed_impl(
                _Event(BOT_UIN),
                target_scope="current_user",
            )
        )

        self.assertEqual("identity_ambiguous", result["status"])
        self.assertEqual("shared_identity", result["identity"]["owner_role"])
        self.assertFalse(result["identity"]["pronoun_safe"])

    async def test_selector_cannot_replace_a_confirmed_target_uin(self) -> None:
        harness = _QzoneViewHarness(USER_UIN)

        result = json.loads(
            await harness._pc_qzone_view_feed_impl(
                _Event(),
                target_scope="current_user",
                selector=f"@{THIRD_PARTY_UIN} 最新",
            )
        )

        self.assertEqual("invalid_target", result["status"])
        self.assertFalse(result["success"])
        self.assertFalse(result["target_verified"])
        self.assertFalse(result["should_retry"])
        self.assertEqual([], harness.query_targets)


if __name__ == "__main__":
    unittest.main()
